"""Period engine — net money flow over any requested window.

Cache-first AND request-fast. Two layers:

  1. Bar cache (barstore): for a window we ask the store what's missing, fetch
     only those gaps from the provider, store them, then compute flow over the
     whole window. Resolution auto-picked (5-min short, hourly weeks, daily
     months+).

  2. Snapshot cache (this module): the *computed* per-period result is cached in
     memory with a short TTL. The web layer calls `get()`, which NEVER blocks on
     a Yahoo fetch — it returns the cached snapshot immediately (refreshing it in
     a background thread if stale), or a `{"pending": True}` marker the first time
     a period is ever asked for. Concurrent requests for the same window are
     de-duplicated, so repeated tab clicks or the live poll can't stack heavy
     backfills and trip Yahoo's rate limiter.

The main loop warms the common windows in the background each cycle, so the
default view (1d) is essentially always ready.
"""
from __future__ import annotations

import logging
import threading
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

# How long a computed snapshot stays "fresh" before a background refresh, by
# bar resolution. Short windows move fast; long windows barely change intraday.
SNAPSHOT_TTL = {"5m": 180, "1h": 1800, "1d": 6 * 3600}

# The windows the main loop keeps warm each cycle (all 5-minute resolution, so
# they compute straight from bars the live loop already stored — no Yahoo hit).
WARM_PERIODS = ["1d", "3d", "6h", "3h", "1h"]


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
                 mp_store=None, mp_worker=None, fund_store=None, fund_worker=None):
        self.get_sectors = get_sectors      # callable -> current resolved sectors
        self.benchmark = benchmark
        self.provider = provider
        self.store = store
        self.mp_store = mp_store            # MaxPainStore (optional)
        self.mp_worker = mp_worker          # MaxPainWorker (optional)
        self.fund_store = fund_store        # FundamentalsStore (optional)
        self.fund_worker = fund_worker      # FundamentalsWorker (optional)
        # snapshot cache
        self._cache: dict[str, dict] = {}   # key -> {"snap": dict, "ts": float}
        self._inflight: set[str] = set()    # keys currently being computed
        self._lock = threading.Lock()

    # ---- windows / keys -------------------------------------------------
    def window_for(self, period=None, start=None, end=None):
        now = time.time()
        if start is not None and end is not None:
            return float(start), float(end)
        secs = PERIOD_SECONDS.get(period, 86400)
        return now - secs, now

    def _key(self, period=None, start=None, end=None) -> str:
        if start is not None and end is not None:
            return f"{int(start)}_{int(end)}"
        return period or "1d"

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
            # Mark the FULL requested window covered ONLY for symbols we actually
            # received — this still absorbs intra-window weekend/holiday gaps (a
            # symbol that traded gets its whole window marked), but a ticker whose
            # download FAILED stays 'missing' and is retried, instead of being
            # silently treated as permanently empty.
            for sym in got:
                self.store.record_coverage(res, sym, gs, ge)

    # ---- the actual compute (may hit Yahoo; runs off the request thread) -
    def _compute(self, period=None, start=None, end=None) -> dict:
        s, e = self.window_for(period, start, end)
        res = barstore.resolution_for(e - s)
        sectors = self.get_sectors()
        syms = self._symbols(sectors)
        try:
            self._fill_gaps(syms, res, res, s, e)
        except Exception as exc:  # noqa: BLE001 - serve whatever is cached
            log.warning("gap fill failed (%s); serving cached bars", exc)
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

    def _refresh(self, key, period, start, end):
        """Compute in this thread and store the snapshot; clear the in-flight flag."""
        try:
            snap = self._compute(period=period, start=start, end=end)
            with self._lock:
                self._cache[key] = {"snap": snap, "ts": time.time()}
        except Exception as exc:  # noqa: BLE001
            log.warning("period compute failed for %s: %s", key, exc)
            with self._lock:
                # only record an error snapshot if we have no good data to fall back on
                if key not in self._cache:
                    self._cache[key] = {"snap": {"error": str(exc), "sectors": []},
                                        "ts": time.time()}
        finally:
            with self._lock:
                self._inflight.discard(key)

    def _request(self, key, period, start, end, blocking=False):
        """Kick off a compute for `key` unless one is already running (dedup)."""
        with self._lock:
            if key in self._inflight:
                return
            self._inflight.add(key)
        if blocking:
            self._refresh(key, period, start, end)
        else:
            threading.Thread(target=self._refresh, args=(key, period, start, end),
                             daemon=True).start()

    # ---- the web-facing, NON-BLOCKING entry point -----------------------
    def get(self, period=None, start=None, end=None) -> dict:
        """Return a snapshot immediately. Never blocks on Yahoo.

        - cached & fresh   -> that snapshot
        - cached & stale   -> that snapshot (flagged stale) + background refresh
        - never computed   -> {"pending": True} + background compute kicked off
        """
        key = self._key(period, start, end)
        s, e = self.window_for(period, start, end)
        res = barstore.resolution_for(e - s)
        # custom absolute ranges don't move -> keep them fresh for a good while
        ttl = SNAPSHOT_TTL.get(res, 300) if (start is None and end is None) else 3600

        with self._lock:
            entry = self._cache.get(key)
            computing = key in self._inflight

        if entry:
            age = time.time() - entry["ts"]
            snap = dict(entry["snap"])
            if age > ttl and not computing:
                self._request(key, period, start, end)   # refresh in the background
            snap["age"] = round(age)
            if age > ttl:
                snap["stale"] = True
            return snap

        # nothing yet -> start computing and tell the client to poll
        if not computing:
            self._request(key, period, start, end)
        return {"pending": True, "period": period or key, "sectors": []}

    def warm(self, periods=None):
        """Pre-compute common windows in the background (called from the loop)."""
        for p in (periods or WARM_PERIODS):
            self._request(self._key(p), p, None, None)

    # keep the old name as a blocking alias (used by nothing on the hot path now)
    def compute(self, period=None, start=None, end=None) -> dict:
        return self._compute(period=period, start=start, end=end)

    # ---- per-stock drill-down (period-aware) ----------------------------
    def sector_detail(self, name, period=None, start=None, end=None,
                      lookback_seconds=7200) -> dict:
        """Per-stock last value + flow for one sector's constituents.

        `period`/`start`/`end` pick the window, matching whatever tab is on the
        main map (1d, 3h, a custom range …). 'Live' (or nothing) keeps the old
        behaviour: the last ~2h of 5-min bars. Reads are cache-only (the main
        period view already fetched the bars), so this never blocks on Yahoo —
        which also means the drill-down stays meaningful after the US close,
        instead of collapsing every stock to $0 in an empty live window."""
        from .signals import _stock_flow, contract_mult
        sectors = self.get_sectors()
        sec = next((s for s in sectors if s.name == name), None)
        if sec is None:
            return {"sector": name, "stocks": [], "error": "unknown sector"}
        now = time.time()
        if period in (None, "", "Live") and start is None and end is None:
            s, e, res = now - lookback_seconds, now, "5m"
            win_label = "live"
        else:
            s, e = self.window_for(period, start, end)
            res = barstore.resolution_for(e - s)
            win_label = period or f"{_iso(s)[:16]} to {_iso(e)[:16]}"
        stocks = []
        for sym in sec.symbols:
            df = self.store.get_bars(res, sym, s, e)
            if df.empty:
                stocks.append({"ticker": sym, "last": None, "flow": 0.0,
                               "net_dollar": 0, "gross": 0, "ts": None})
                continue
            net, gross = _stock_flow(df, None, contract_mult(sym))
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
        # attach cached fundamentals (company name + valuation) per stock
        if self.fund_store is not None:
            for st in stocks:
                f = self.fund_store.get(st["ticker"])
                if f:
                    st["company"] = f.get("longName") or f.get("shortName")
                    st["market_cap"] = f.get("marketCap")
                    st["pe"] = f.get("trailingPE")
                    st["forward_pe"] = f.get("forwardPE")
                    st["eps"] = f.get("trailingEps")
                    st["div_yield"] = f.get("dividendYield")
                    st["w52_high"] = f.get("fiftyTwoWeekHigh")
                    st["w52_low"] = f.get("fiftyTwoWeekLow")
                    st["beta"] = f.get("beta")
        # kick off gentle background refreshes for this sector's names
        if self.mp_worker is not None:
            self.mp_worker.request([st["ticker"] for st in stocks])
        if self.fund_worker is not None:
            self.fund_worker.request([st["ticker"] for st in stocks])
        stocks.sort(key=lambda x: x["flow"], reverse=True)
        for i, st in enumerate(stocks):
            st["rank"] = i + 1
        return {"sector": name, "stocks": stocks, "updated": _iso(e),
                "period": win_label, "resolution": res}
