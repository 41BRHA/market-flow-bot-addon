"""Max-pain per stock — computed from Yahoo option chains, with history.

Max pain = the expiry price at which the total value of in-the-money options
(weighted by open interest) is smallest — i.e. where the most option premium
expires worthless. Prices often gravitate toward it near expiry.

Design (per the plan + research):
  * On-demand only: computing a sector's ~30 chains takes ~30-60s at a polite
    pace, so it runs in a **background worker**, never in the HTTP request.
  * Cache-first: the last calculation is persisted with a 'calculated_at' stamp
    and held until a new one replaces it — nothing silently expires.
  * Every calculation is stored, which gives the history for free:
    vs last calc / vs ~1 day / vs ~1 week, plus drift toward the pin.

Data: yfinance option_chain() (strike, openInterest) — no key. openInterest is a
snapshot, not live order flow — good for "where's the pin", not "who just bought".
"""
from __future__ import annotations

import logging
import math
import os
import queue
import sqlite3
import threading
import time

log = logging.getLogger("maxpain")


def _oi(v) -> float:
    """Open interest as a finite float; NaN/blank/None -> 0. A NaN slipping into
    the sum poisons the min-search and returns the wrong strike."""
    try:
        x = float(v)
        return x if math.isfinite(x) else 0.0
    except (TypeError, ValueError):
        return 0.0

REFRESH_MIN_AGE = 3600      # don't recompute a ticker more often than hourly
PACING = 1.5               # seconds between tickers (polite to Yahoo)
BACKOFF = 8                # seconds on a rate-limit


# ---- the maths (pure, fully testable) --------------------------------------
def max_pain_from_chain(call_oi: list[tuple[float, float]],
                        put_oi: list[tuple[float, float]]) -> float | None:
    """call_oi/put_oi: lists of (strike, open_interest). Returns the max-pain
    strike (the strike minimising total ITM option value at expiry)."""
    strikes = sorted({k for k, _ in call_oi} | {k for k, _ in put_oi})
    if not strikes:
        return None
    # coerce any NaN/inf open interest to 0 up front — a single NaN in the sum
    # makes every `total < best_val` comparison False and freezes the search.
    call_oi = [(k, oi if math.isfinite(oi) else 0.0) for k, oi in call_oi]
    put_oi = [(k, oi if math.isfinite(oi) else 0.0) for k, oi in put_oi]
    best_strike, best_val = None, None
    for s in strikes:
        total = 0.0
        for k, oi in call_oi:          # calls ITM when expiry price s > strike k
            if s > k:
                total += oi * (s - k)
        for k, oi in put_oi:           # puts ITM when expiry price s < strike k
            if s < k:
                total += oi * (k - s)
        if best_val is None or total < best_val:
            best_val, best_strike = total, s
    return best_strike


# ---- history store ---------------------------------------------------------
class MaxPainStore:
    def __init__(self, path: str | None = None):
        base = "/data" if os.path.isdir("/data") else os.path.dirname(__file__)
        self.path = path or os.path.join(base, "maxpain.db")
        self.conn = sqlite3.connect(self.path, check_same_thread=False)
        self.conn.execute("PRAGMA busy_timeout=5000")
        self.conn.execute("PRAGMA journal_mode=WAL")
        self._lock = threading.RLock()   # the worker writes while requests read — serialise
        self.conn.execute("""CREATE TABLE IF NOT EXISTS maxpain(
            ticker TEXT, expiry TEXT, ts REAL, max_pain REAL, spot REAL)""")
        self.conn.execute("CREATE INDEX IF NOT EXISTS idx_mp ON maxpain(ticker, ts)")
        self.conn.commit()

    def record(self, ticker, expiry, ts, max_pain, spot):
        with self._lock:
            self.conn.execute("INSERT INTO maxpain VALUES(?,?,?,?,?)",
                              (ticker, expiry, ts, max_pain, spot))
            self.conn.commit()

    def _rows(self, ticker, limit=400):
        with self._lock:
            cur = self.conn.execute(
                "SELECT ts, expiry, max_pain, spot FROM maxpain WHERE ticker=? ORDER BY ts DESC LIMIT ?",
                (ticker, limit))
            return cur.fetchall()

    def _closest_before(self, rows, target_ts):
        # rows are newest-first; find the newest row at or before target_ts
        for ts, _e, mp, _s in rows:
            if ts <= target_ts:
                return mp
        return None

    def history(self, ticker) -> dict | None:
        rows = self._rows(ticker)
        if not rows:
            return None
        ts, expiry, mp, spot = rows[0]
        now = time.time()
        # Only compare like-for-like: the max-pain of the SAME expiry. As the
        # nearest expiry rolls, last week's row is a different contract, so a raw
        # cross-expiry "vs 1 week" would be apples-to-oranges (returns None when
        # there's no same-expiry point that far back, which is the honest answer).
        same = [r for r in rows if r[1] == expiry]
        prev = same[1][2] if len(same) > 1 else None
        d1 = self._closest_before(same, now - 86400)
        w1 = self._closest_before(same, now - 7 * 86400)
        def delta(a, b):
            return None if (a is None or b is None) else round(a - b, 2)
        return {
            "max_pain": mp, "spot": spot, "expiry": expiry,
            "calculated_at": ts,
            "vs_last": delta(mp, prev),
            "vs_1d": delta(mp, d1),
            "vs_1w": delta(mp, w1),
        }

    def last_ts(self, ticker) -> float:
        rows = self._rows(ticker, limit=1)
        return rows[0][0] if rows else 0.0


# ---- background worker -----------------------------------------------------
class MaxPainWorker:
    """Computes max pain for requested tickers on a background thread, gently."""
    def __init__(self, store: MaxPainStore, spot_fn=None):
        self.store = store
        self.spot_fn = spot_fn            # optional: ticker -> spot from bar-store
        self.q: "queue.Queue[str]" = queue.Queue()
        self._queued: set[str] = set()
        self._lock = threading.Lock()
        threading.Thread(target=self._run, daemon=True).start()

    def request(self, tickers, force=False):
        now = time.time()
        for t in tickers:
            if not force and now - self.store.last_ts(t) < REFRESH_MIN_AGE:
                continue
            with self._lock:
                if t in self._queued:
                    continue
                self._queued.add(t)
            self.q.put(t)

    def _run(self):
        while True:
            t = self.q.get()
            try:
                self.compute_ticker(t)
            except Exception as exc:  # noqa: BLE001
                log.warning("max pain %s failed: %s", t, exc)
            finally:
                with self._lock:
                    self._queued.discard(t)
            time.sleep(PACING)

    def compute_ticker(self, ticker: str, retries: int = 3):
        import yfinance as yf
        tk = yf.Ticker(ticker)
        for attempt in range(retries):
            try:
                exps = tk.options
                if not exps:
                    return None
                expiry = exps[0]                       # nearest expiry
                chain = tk.option_chain(expiry)
                call_oi = [(float(r.strike), _oi(r.openInterest))
                           for r in chain.calls.itertuples()]
                put_oi = [(float(r.strike), _oi(r.openInterest))
                          for r in chain.puts.itertuples()]
                mp = max_pain_from_chain(call_oi, put_oi)
                if mp is None:
                    return None
                spot = None
                try:
                    spot = float(chain.underlying.get("regularMarketPrice"))
                except Exception:
                    pass
                if spot is None and self.spot_fn:
                    spot = self.spot_fn(ticker)
                self.store.record(ticker, expiry, time.time(), mp, spot or 0.0)
                log.info("max pain %s (%s): %.2f (spot %.2f)", ticker, expiry, mp, spot or 0.0)
                return mp
            except Exception as exc:  # noqa: BLE001
                if "too many requests" in str(exc).lower() or "rate limit" in str(exc).lower():
                    time.sleep(BACKOFF * (attempt + 1))
                    continue
                raise
        return None
