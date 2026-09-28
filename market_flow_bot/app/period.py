"""Period engine — net money flow over any requested window.

Cache-first: for a window it asks the bar-store what's missing, fetches only
those gaps from the provider, stores them, then computes flow over the whole
window (option A — the net across the period, per sector). Resolution is picked
automatically (5-min for short, hourly for weeks, daily for months+).
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timezone

from . import barstore
from .signals import flowboard_over

log = logging.getLogger("period")

PERIOD_SECONDS = {
    "1h": 3600, "3h": 3 * 3600, "6h": 6 * 3600,
    "1d": 86400, "3d": 3 * 86400,
    "1w": 7 * 86400, "2w": 14 * 86400,
    "1m": 30 * 86400, "3m": 90 * 86400, "6m": 182 * 86400,
    "1y": 365 * 86400, "2y": 730 * 86400, "3y": 1095 * 86400, "5y": 1825 * 86400,
}


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat(timespec="seconds")


def _merge(intervals):
    if not intervals:
        return []
    intervals = sorted(intervals)
    out = [list(intervals[0])]
    for s, e in intervals[1:]:
        if s <= out[-1][1]:
            out[-1][1] = max(out[-1][1], e)
        else:
            out.append([s, e])
    return [(s, e) for s, e in out]


class PeriodEngine:
    def __init__(self, get_sectors, benchmark, provider, store: barstore.BarStore,
                 mp_store=None, mp_worker=None):
        self.get_sectors = get_sectors      # callable -> current resolved sectors
        self.benchmark = benchmark
        self.provider = provider
        self.store = store
        self.mp_store = mp_store            # MaxPainStore (optional)
        self.mp_worker = mp_worker          # MaxPainWorker (optional)

    def window_for(self, period=None, start=None, end=None):
        now = time.time()
        if start is not None and end is not None:
            return float(start), float(end)
        secs = PERIOD_SECONDS.get(period, 86400)
        return now - secs, now

    def _symbols(self, sectors):
        syms = []
        for sec in sectors:
            for s in sec.symbols:
                if s not in syms:
                    syms.append(s)
        for s in self.benchmark:
            if s not in syms:
                syms.append(s)
        return syms

    def _fill_gaps(self, syms, res, interval, s, e):
        missing = []
        for sym in syms:
            missing += self.store.missing_ranges(res, sym, s, e)
        for gs, ge in _merge(missing):
            got = self.provider.get_range(syms, interval, gs, ge)
            for sym, df in got.items():
                self.store.put_bars(res, sym, df)
            if got:  # fetch worked -> the whole requested range is now covered for all
                for sym in syms:
                    self.store.record_coverage(res, sym, gs, ge)

    def compute(self, period=None, start=None, end=None) -> dict:
        s, e = self.window_for(period, start, end)
        res = barstore.resolution_for(e - s)
        sectors = self.get_sectors()
        syms = self._symbols(sectors)
        try:
            self._fill_gaps(syms, res, res, s, e)
        except Exception as exc:  # noqa: BLE001 - serve whatever is cached
            log.warning("gap fill failed (%s); serving cached", exc)
        frames = {}
        for sym in syms:
            df = self.store.get_bars(res, sym, s, e)
            if not df.empty:
                frames[sym] = df
        fb = flowboard_over(frames, sectors, self.benchmark, recent_bars=None)
        label = period or f"{_iso(s)[:16]} to {_iso(e)[:16]}"
        return {
            "sectors": [
                {"rank": st.rank, "sector": st.name, "flow": round(st.flow_ratio, 3),
                 "direction": "in" if st.flow_ratio > 0 else "out" if st.flow_ratio < 0 else "flat",
                 "net_dollar": round(st.net_dollar), "gross": round(st.gross),
                 "breadth": (round(st.breadth) if st.breadth is not None else None),
                 "rel_strength": round(st.rel_strength, 2), "rvol": round(st.rvol, 2)}
                for st in fb],
            "fetched": len(frames), "total_symbols": len(syms),
            "updated": _iso(e), "period": label, "resolution": res,
            "leader": fb[0].name if fb else None,
        }

    def sector_detail(self, name, lookback_seconds=7200) -> dict:
        """Per-stock last value + recent flow for one sector's constituents.
        Reads the last ~2h of 5-min bars from the store (accumulated live)."""
        from .signals import _stock_flow
        sectors = self.get_sectors()
        sec = next((s for s in sectors if s.name == name), None)
        if sec is None:
            return {"sector": name, "stocks": [], "error": "unknown sector"}
        now = time.time()
        s = now - lookback_seconds
        stocks = []
        for sym in sec.symbols:
            df = self.store.get_bars("5m", sym, s, now)
            if df.empty:
                stocks.append({"ticker": sym, "last": None, "flow": 0.0,
                               "net_dollar": 0, "gross": 0, "ts": None})
                continue
            net, gross = _stock_flow(df, None)
            stocks.append({
                "ticker": sym,
                "last": round(float(df["close"].iloc[-1]), 2),
                "flow": round((net / gross) if gross > 0 else 0.0, 3),
                "net_dollar": round(net), "gross": round(gross),
                "ts": df.index[-1].isoformat(),
            })
        # attach cached max-pain + history for each stock
        if self.mp_store is not None:
            for st in stocks:
                h = self.mp_store.history(st["ticker"])
                if h:
                    st["maxpain"] = round(h["max_pain"], 2)
                    st["maxpain_spot"] = round(h["spot"], 2) if h["spot"] else None
                    st["maxpain_expiry"] = h["expiry"]
                    st["maxpain_at"] = _iso(h["calculated_at"])
                    st["maxpain_vs_last"] = h["vs_last"]
                    st["maxpain_vs_1d"] = h["vs_1d"]
                    st["maxpain_vs_1w"] = h["vs_1w"]
        # kick off a gentle background refresh for this sector's names
        if self.mp_worker is not None:
            self.mp_worker.request([st["ticker"] for st in stocks])
        stocks.sort(key=lambda x: x["flow"], reverse=True)
        for i, st in enumerate(stocks):
            st["rank"] = i + 1
        return {"sector": name, "stocks": stocks, "updated": _iso(now)}
