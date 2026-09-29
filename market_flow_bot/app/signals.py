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

# Futures carry a contract multiplier, so price x contracts is NOT the dollar
# turnover — a gold contract is 100 oz, silver 5,000 oz, etc. Equities are 1x.
# (Only affects the $ magnitude shown for these sectors; the flow RATIO is
# size-neutral and unchanged either way.)
CONTRACT_MULT = {
    "GC=F": 100, "MGC=F": 10, "SI=F": 5000, "SIL=F": 1000, "HG=F": 25000,
    "PL=F": 50, "PA=F": 100, "CL=F": 1000, "NG=F": 10000,
    "ES=F": 50, "NQ=F": 20, "YM=F": 5, "RTY=F": 50,
}


def contract_mult(sym: str) -> float:
    return float(CONTRACT_MULT.get(sym, 1.0))


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


def _stock_flow(df: pd.DataFrame, recent_bars: int | None, mult: float = 1.0) -> tuple[float, float]:
    """(net_money_flow_dollar, gross_dollar_volume) over the last recent_bars bars
    (or the whole df when recent_bars is None). `mult` is the futures contract
    multiplier (1.0 for equities).

    Money flow uses the **Chaikin money-flow multiplier**: for each bar,
        MFM = ((Close - Low) - (High - Close)) / (High - Low)   in [-1, +1]
    i.e. +1 if the bar closed on its high (buyers dominated), -1 on its low
    (sellers), graded in between — a far better estimate of buy/sell pressure
    than a binary up/down sign. Each bar's dollar-volume is weighted by MFM.

        net   = sum( MFM * close * volume * mult )   (signed money-flow $)
        gross = sum(       close * volume * mult )   (total turnover, ball size)
        ratio = net / gross                          (in [-1, +1])

    Fallback: if a bar has no usable high/low (old rows, or High==Low), it
    reverts to the up/down-vs-previous-close sign so nothing breaks."""
    if df.shape[0] < 1:
        return 0.0, 0.0
    tail = df if recent_bars is None else df.tail(recent_bars)
    closes = tail["close"].to_numpy(dtype=float)
    vols = tail["volume"].to_numpy(dtype=float)
    has_hl = ("high" in tail.columns and "low" in tail.columns)
    highs = tail["high"].to_numpy(dtype=float) if has_hl else None
    lows = tail["low"].to_numpy(dtype=float) if has_hl else None
    net = gross = 0.0
    prev = None
    for i in range(len(closes)):
        c, v = closes[i], vols[i]
        dv = c * v * mult
        if not np.isfinite(dv) or v < 0:      # bad tick / negative volume -> skip
            prev = c if np.isfinite(c) else prev
            continue
        if highs is not None and np.isfinite(highs[i]) and np.isfinite(lows[i]) and highs[i] > lows[i]:
            mfm = ((c - lows[i]) - (highs[i] - c)) / (highs[i] - lows[i])   # Chaikin, [-1,1]
            # a bad tick (close printed outside the bar's high/low) can push this
            # past ±1; clamp so the ratio invariant [-1,1] always holds.
            if mfm > 1.0: mfm = 1.0
            elif mfm < -1.0: mfm = -1.0
        else:
            if prev is None:
                prev = c
                continue        # legacy fallback: first bar can't be signed -> skip entirely
            mfm = 0.0 if c == prev else (1.0 if c > prev else -1.0)
        net += mfm * dv
        gross += dv
        prev = c
    return net, gross


def pct_changes(frames, mode: str = "prev_close") -> dict[str, float]:
    """Per-symbol price % change for the move alerts.

    mode='prev_close'  -> vs the last close BEFORE today (US/Eastern), so it
                          includes the overnight gap (the usual "up 7% today").
    mode='session_open'-> vs the first bar of today's session (intraday move only).
    """
    from zoneinfo import ZoneInfo
    ET = ZoneInfo("America/New_York")
    out: dict[str, float] = {}
    for sym, df in frames.items():
        if df is None or df.shape[0] < 2:
            continue
        closes = df["close"].to_numpy(dtype=float)
        idx = df.index
        try:
            et = idx.tz_convert(ET) if getattr(idx, "tz", None) is not None else idx.tz_localize("UTC").tz_convert(ET)
        except Exception:  # noqa: BLE001
            continue
        dates = np.array([t.date() for t in et])
        mins = np.array([t.hour * 60 + t.minute for t in et])
        regular = (mins >= 570) & (mins <= 960)          # 09:30–16:00 ET regular session
        today = dates[-1]
        last = closes[-1]
        if not np.isfinite(last):
            continue
        if mode == "session_open":
            reg_today = np.where((dates == today) & regular)[0]
            any_today = np.where(dates == today)[0]
            idxs = reg_today if reg_today.size else any_today   # true open, else first (pre-market) bar
            if idxs.size == 0:
                continue
            ref = closes[int(idxs[0])]
        else:  # prev_close -> the last REGULAR-session close before today (not after-hours)
            reg_prev = np.where((dates < today) & regular)[0]
            any_prev = np.where(dates < today)[0]
            idxs = reg_prev if reg_prev.size else any_prev
            if idxs.size == 0:
                continue
            ref = closes[int(idxs[-1])]
        if np.isfinite(ref) and ref != 0:
            out[sym] = (last / ref - 1.0) * 100.0
    return out


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
        return 0.0        # no baseline yet -> NOT volume-confirmed (fails the alert gate)
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
            n, g = _stock_flow(frames[s], th.recent_bars, contract_mult(s))
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
            # only say "out of X" when X is actually outflowing; don't claim an
            # outflow when the whole board is positive (or an inflow when it's all red)
            if bot.flow_ratio < 0 and top.flow_ratio > 0:
                title = f"Flow: into {top.name}, out of {bot.name}"
                msg = f"Inflows: {lead}. Outflows: {lag}."
            elif top.flow_ratio > 0:
                title = f"Flow: strongest into {top.name}"
                msg = f"All inflowing — leaders {lead}; weakest {lag}."
            elif bot.flow_ratio < 0:
                title = f"Flow: broad outflow, least from {top.name}"
                msg = f"All outflowing — heaviest {lag}; least {lead}."
            else:
                title = "Flow: balanced across sectors"
                msg = f"Top {lead}. Bottom {lag}."
            signals.append(Signal(
                "flow_summary", "info", title, msg,
                float(top.flow_ratio - bot.flow_ratio)))

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
            n, g = _stock_flow(frames[s], recent_bars, contract_mult(s))
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
