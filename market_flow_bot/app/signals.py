"""Volume-flow rotation engine.

For each sector, money flow is computed from its constituent stocks: each bar's
dollar volume (close x volume) is signed by whether the bar closed up or down,
summed across the recent window and across the sector's stocks. That net is
divided by the sector's gross dollar volume to give a **flow ratio in [-1, 1]** —
size-neutral, so a mega-cap sector doesn't dominate purely by being large:

    +1.0  = essentially all money went into up-bars (strong inflow)
     0.0  = balanced
    -1.0  = strong outflow

This ratio is the arrow value on the flow map: sign = in/out, magnitude =
conviction. Alongside it, per sector:
  * breadth      — % of the sector's stocks that are net-inflowing (is it broad?)
  * rel_strength — sector return minus SPY return (is it beating the market?)
  * rvol         — recent vs earlier volume (is the flow on heavy or light volume?)

Alerts are deliberately rare: a **flip** (money reverses direction with conviction)
or a **strong** one-directional flow. Everything is tunable, and the first cycle
after startup is silenced so a restart doesn't blast notifications.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd


@dataclass
class Signal:
    key: str
    severity: str      # info | warning | alert
    title: str
    message: str
    value: float


@dataclass
class FlowStat:
    name: str
    flow_ratio: float          # [-1, 1] signed conviction — the arrow value
    net_dollar: float          # raw signed dollar-volume (secondary)
    gross: float               # total gross dollar-volume (ball size)
    breadth: float | None      # % of stocks inflowing
    rel_strength: float        # sector return - SPY return (%)
    rvol: float                # recent/earlier volume ratio
    density: float = 1.0       # fraction of the window that actually traded (confidence)
    rank: int = 0


def _weighted_index(frames, symbols) -> pd.Series | None:
    cols = {s: frames[s]["close"] for s in symbols if s in frames and not frames[s].empty}
    if not cols:
        return None
    closes = pd.DataFrame(cols).sort_index().ffill().dropna()
    if closes.shape[0] < 2:
        return None
    return (closes / closes.iloc[0]).mean(axis=1)


def _window_return(idx: pd.Series | None) -> float:
    if idx is None or idx.shape[0] < 2:
        return 0.0
    return 100.0 * (idx.iloc[-1] / idx.iloc[0] - 1.0)


def _stock_flow(df: pd.DataFrame, recent_bars: int | None) -> tuple[float, float]:
    """(net_signed_dollar_volume, gross_dollar_volume). Over the last recent_bars
    bars, or over the whole df when recent_bars is None (period windows)."""
    if df.shape[0] < 2:
        return 0.0, 0.0
    tail = df if recent_bars is None else df.tail(recent_bars + 1)
    closes = tail["close"].to_numpy(dtype=float)
    vols = tail["volume"].to_numpy(dtype=float)
    net = gross = 0.0
    for i in range(1, len(closes)):
        dv = closes[i] * vols[i]
        if not np.isfinite(dv):
            continue
        net += dv if closes[i] >= closes[i - 1] else -dv
        gross += dv
    return net, gross


def _sector_rvol(frames, symbols, recent_bars: int) -> float:
    """Recent-window volume vs the earlier window — flow conviction proxy."""
    recent = earlier = 0.0
    for s in symbols:
        df = frames.get(s)
        if df is None or df.shape[0] < recent_bars * 2:
            continue
        v = df["volume"].to_numpy(dtype=float)
        recent += float(np.nansum(v[-recent_bars:]))
        earlier += float(np.nansum(v[-2 * recent_bars:-recent_bars]))
    if earlier <= 0:
        return 1.0
    return recent / earlier


def compute(frames, sectors, benchmark_syms, th, ac, prev_dir, session="regular",
            history=None, first_cycle=False):
    """Return (signals, flowboard[FlowStat], directions{name: sign})."""
    bench_idx = _weighted_index(frames, benchmark_syms) if benchmark_syms else None
    spy_ret = _window_return(bench_idx)

    stats: list[FlowStat] = []
    for sec in sectors:
        present = [s for s in sec.symbols if s in frames and not frames[s].empty]
        if not present:
            continue
        net = gross = 0.0
        inflowing = counted = samples = 0
        for s in present:
            n, g = _stock_flow(frames[s], th.recent_bars)
            net += n
            gross += g
            if g > 0:
                counted += 1
                inflowing += int(n > 0)
                samples += min(frames[s].shape[0] - 1, th.recent_bars)   # bars that traded
        flow_ratio = (net / gross) if gross > 0 else 0.0
        # density = fraction of the possible window bars that actually traded.
        # Thin pre-market -> low density; regular hours / 24h futures -> ~1.0.
        density = samples / (counted * th.recent_bars) if counted else 0.0
        idx = _weighted_index(frames, present)
        stats.append(FlowStat(
            name=sec.name,
            flow_ratio=flow_ratio,
            net_dollar=net,
            gross=gross,
            breadth=(100.0 * inflowing / counted) if (counted and len(present) > 1) else None,
            rel_strength=_window_return(idx) - spy_ret,
            rvol=_sector_rvol(frames, present, th.recent_bars),
            density=round(density, 2),
        ))

    flowboard = sorted(stats, key=lambda s: s.flow_ratio, reverse=True)
    directions = {s.name: (1 if s.flow_ratio > 0 else -1 if s.flow_ratio < 0 else 0) for s in flowboard}
    for i, s in enumerate(flowboard):
        s.rank = i + 1

    signals: list[Signal] = []
    if first_cycle or not flowboard:
        return signals, flowboard, directions   # silence the startup cycle

    # periodic digest (own cooldown in main)
    if ac.alert_on_summary:
        # Only rank sectors with enough traded data — keeps thin pre-market
        # readings (flow pinned to ±1.00 on a couple of bars) out of the digest.
        conf = [s for s in flowboard if s.density >= ac.min_density]
        if len(conf) >= 2:
            top, bot = conf[0], conf[-1]
            lead = ", ".join(f"{s.name} {s.flow_ratio:+.2f}" for s in conf[:3])
            lag = ", ".join(f"{s.name} {s.flow_ratio:+.2f}" for s in conf[-3:])
            signals.append(Signal(
                "flow_summary", "info",
                f"Flow: into {top.name}, out of {bot.name}",
                f"Inflows: {lead}. Outflows: {lag}.", float(top.flow_ratio - bot.flow_ratio)))

    for s in flowboard:
        prev = prev_dir.get(s.name, 0)
        cur = directions[s.name]

        # flip: money reversed direction with conviction and on real volume
        if (ac.alert_on_flip and prev != 0 and cur != 0 and cur != prev
                and abs(s.flow_ratio) >= ac.min_flip_ratio and s.rvol >= ac.min_rvol
                and s.density >= ac.min_density):
            io = "into" if cur > 0 else "out of"
            signals.append(Signal(
                f"flip_{s.name}", "alert", f"Money rotating {io} {s.name}",
                f"{s.name} flow flipped to {s.flow_ratio:+.2f} "
                f"(breadth {s.breadth:.0f}%, {s.rvol:.1f}x volume)."
                if s.breadth is not None else
                f"{s.name} flow flipped to {s.flow_ratio:+.2f} ({s.rvol:.1f}x volume).",
                float(s.flow_ratio)))

        # strong one-directional flow on heavy volume
        elif (ac.alert_on_strong and abs(s.flow_ratio) >= ac.strong_ratio and s.rvol >= ac.min_rvol
                and s.density >= ac.min_density):
            io = "inflow" if s.flow_ratio > 0 else "outflow"
            signals.append(Signal(
                f"strong_{s.name}", "warning", f"Strong {io}: {s.name}",
                f"{s.name} flow {s.flow_ratio:+.2f} on {s.rvol:.1f}x volume"
                + (f", breadth {s.breadth:.0f}%." if s.breadth is not None else "."),
                float(s.flow_ratio)))

    return signals, flowboard, directions


def flowboard_over(frames, sectors, benchmark_syms, recent_bars=None):
    """Per-sector flow over the whole provided window (period views) or the last
    `recent_bars` bars. Returns a sorted list[FlowStat] — no alerts/history."""
    bench_idx = _weighted_index(frames, benchmark_syms) if benchmark_syms else None
    spy_ret = _window_return(bench_idx)
    stats: list[FlowStat] = []
    for sec in sectors:
        present = [s for s in sec.symbols if s in frames and not frames[s].empty]
        if not present:
            continue
        net = gross = 0.0
        inflowing = counted = 0
        for s in present:
            n, g = _stock_flow(frames[s], recent_bars)
            net += n
            gross += g
            if g > 0:
                counted += 1
                inflowing += int(n > 0)
        flow_ratio = (net / gross) if gross > 0 else 0.0
        idx = _weighted_index(frames, present)
        stats.append(FlowStat(
            name=sec.name, flow_ratio=flow_ratio, net_dollar=net, gross=gross,
            breadth=(100.0 * inflowing / counted) if (counted and len(present) > 1) else None,
            rel_strength=_window_return(idx) - spy_ret, rvol=1.0))
    fb = sorted(stats, key=lambda s: s.flow_ratio, reverse=True)
    for i, s in enumerate(fb):
        s.rank = i + 1
    return fb
