"""Tiered local bar-store — cache-first history for any period.

Every bar the bot fetches is written here, so a request for a window is served
from disk when we already hold it and only the *missing* sub-ranges are fetched
from the source. Three resolutions cover the whole period range:

    5m  — intraday (1h–3d).  Yahoo only serves ~60 days of 5-min, so this store
          is built forward from when the bot starts; rolled off after ~60 days.
    1h  — weeks–months (1w–3m). Yahoo serves ~730 days; kept ~2 years.
    1d  — months–years (6m+). Yahoo serves full history; backfilled once, kept
          for good (tiny).

Coverage is tracked as the wall-clock *ranges we fetched* (not per-bar), so
market-closed gaps like weekends are never re-fetched. `missing_ranges` returns
exactly the sub-windows still needed; the caller fetches only those, calls
`put_bars`, and computes from `get_bars`.

/data-backed SQLite, survives restarts, included in HA backups.
"""
from __future__ import annotations

import os
import sqlite3
import threading

import pandas as pd

RES_SECONDS = {"5m": 300, "1h": 3600, "1d": 86400}
KEEP_DAYS = {"5m": 60, "1h": 730, "1d": 4000}   # roll-off per resolution


def resolution_for(window_seconds: float) -> str:
    """Pick the bar resolution appropriate for a requested window length."""
    if window_seconds <= 3 * 86400:
        return "5m"
    if window_seconds <= 90 * 86400:
        return "1h"
    return "1d"


class BarStore:
    def __init__(self, path: str | None = None):
        base = "/data" if os.path.isdir("/data") else os.path.dirname(__file__)
        self.path = path or os.path.join(base, "bars.db")
        self.conn = sqlite3.connect(self.path, check_same_thread=False)
        self.conn.execute("PRAGMA busy_timeout=5000")
        self.conn.execute("PRAGMA journal_mode=WAL")
        # ONE connection shared across the main loop, the period-engine warm
        # threads, the max-pain worker and each web request. A single reentrant
        # lock serialises every DB call so concurrent access can't raise
        # sqlite3 "bad parameter or other API misuse" (SQLITE_MISUSE). Reentrant
        # so put_bars can call record_coverage while already holding it.
        self._lock = threading.RLock()
        self._init()

    def _init(self) -> None:
        c = self.conn
        c.execute("""CREATE TABLE IF NOT EXISTS bars(
                       res TEXT, symbol TEXT, ts REAL, close REAL, volume REAL,
                       PRIMARY KEY(res, symbol, ts))""")
        c.execute("""CREATE TABLE IF NOT EXISTS coverage(
                       res TEXT, symbol TEXT, start_ts REAL, end_ts REAL)""")
        c.execute("CREATE INDEX IF NOT EXISTS idx_bars ON bars(res, symbol, ts)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_cov ON coverage(res, symbol)")
        c.commit()

    # ---- bars ----
    def put_bars(self, res: str, symbol: str, df: pd.DataFrame) -> int:
        """Store a symbol's bars. df indexed by tz-aware datetime (or epoch),
        with columns close, volume. Records coverage over the df's span."""
        if df is None or df.empty:
            return 0
        idx = df.index
        if isinstance(idx, pd.DatetimeIndex):
            dt = idx.tz_convert("UTC").tz_localize(None) if idx.tz is not None else idx
            epochs = (dt.astype("datetime64[ns]").astype("int64") // 1_000_000_000).tolist()
        else:
            epochs = [float(x) for x in idx]              # already epoch seconds
        rows = [(res, symbol, float(t), float(c), float(v))
                for t, c, v in zip(epochs, df["close"].to_numpy(), df["volume"].to_numpy())
                if pd.notna(c)]
        with self._lock:
            self.conn.executemany("INSERT OR REPLACE INTO bars VALUES(?,?,?,?,?)", rows)
            self.conn.commit()
            if rows:
                self.record_coverage(res, symbol, min(r[2] for r in rows), max(r[2] for r in rows))
        return len(rows)

    def get_bars(self, res: str, symbol: str, start_ts: float, end_ts: float) -> pd.DataFrame:
        with self._lock:
            cur = self.conn.execute(
                "SELECT ts, close, volume FROM bars WHERE res=? AND symbol=? AND ts>=? AND ts<=? ORDER BY ts",
                (res, symbol, start_ts, end_ts))
            data = cur.fetchall()
        if not data:
            return pd.DataFrame(columns=["close", "volume"])
        ts = pd.to_datetime([d[0] for d in data], unit="s", utc=True)
        return pd.DataFrame({"close": [d[1] for d in data], "volume": [d[2] for d in data]}, index=ts)

    # ---- coverage / gap finding ----
    def coverage_of(self, res: str, symbol: str) -> list[tuple[float, float]]:
        with self._lock:
            cur = self.conn.execute(
                "SELECT start_ts, end_ts FROM coverage WHERE res=? AND symbol=? ORDER BY start_ts",
                (res, symbol))
            return [(r[0], r[1]) for r in cur.fetchall()]

    def record_coverage(self, res: str, symbol: str, start_ts: float, end_ts: float) -> None:
        """Add a fetched range and merge overlapping/adjacent intervals."""
        tol = RES_SECONDS.get(res, 300) * 1.5
        with self._lock:
            intervals = self.coverage_of(res, symbol) + [(start_ts, end_ts)]
            intervals.sort()
            merged: list[list[float]] = []
            for s, e in intervals:
                if merged and s <= merged[-1][1] + tol:
                    merged[-1][1] = max(merged[-1][1], e)
                else:
                    merged.append([s, e])
            self.conn.execute("DELETE FROM coverage WHERE res=? AND symbol=?", (res, symbol))
            self.conn.executemany("INSERT INTO coverage VALUES(?,?,?,?)",
                                  [(res, symbol, s, e) for s, e in merged])
            self.conn.commit()

    def missing_ranges(self, res: str, symbol: str, start_ts: float, end_ts: float) -> list[tuple[float, float]]:
        """Sub-ranges of [start,end] not yet fetched — exactly what to pull."""
        gaps: list[tuple[float, float]] = []
        cur = start_ts
        for cs, ce in self.coverage_of(res, symbol):
            if ce < start_ts or cs > end_ts:
                continue
            if cs > cur:
                gaps.append((cur, min(cs, end_ts)))
            cur = max(cur, ce)
            if cur >= end_ts:
                break
        if cur < end_ts:
            gaps.append((cur, end_ts))
        return [(s, e) for s, e in gaps if e > s]

    def prune(self, res: str | None = None) -> None:
        import time
        now = time.time()
        with self._lock:
            for r in ([res] if res else list(KEEP_DAYS)):
                cutoff = now - KEEP_DAYS[r] * 86400
                self.conn.execute("DELETE FROM bars WHERE res=? AND ts < ?", (r, cutoff))
                self.conn.execute("DELETE FROM coverage WHERE res=? AND end_ts < ?", (r, cutoff))
            self.conn.commit()
