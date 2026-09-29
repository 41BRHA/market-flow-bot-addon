"""Per-ticker fundamentals — company name + valuation (P/E, market cap, EPS,
dividend yield, 52-week range, beta).

Best-effort from yfinance, fetched lazily in a background worker when you open a
sector (like max-pain), cached to /data and refreshed slowly (fundamentals move
slowly). yfinance's `.info` is flaky, so a symbol may show "—" until it warms up.
Written atomically (temp + rename) so a crash can't corrupt the cache file.
"""
from __future__ import annotations

import json
import logging
import os
import queue
import threading
import time

log = logging.getLogger("fundamentals")

REFRESH_MIN_AGE = 7 * 24 * 3600     # re-fetch a ticker at most weekly
PACING = 1.0                        # seconds between tickers (polite to Yahoo)

# yfinance .info keys we keep (name + valuation)
FIELDS = ("longName", "shortName", "marketCap", "trailingPE", "forwardPE",
          "trailingEps", "dividendYield", "fiftyTwoWeekHigh", "fiftyTwoWeekLow", "beta")


class FundamentalsStore:
    def __init__(self, path: str | None = None):
        base = "/data" if os.path.isdir("/data") else os.path.dirname(__file__)
        self.path = path or os.path.join(base, "fundamentals.json")
        self._lock = threading.Lock()
        self._data = self._load()

    def _load(self) -> dict:
        try:
            with open(self.path, "r", encoding="utf-8") as fh:
                return json.load(fh)
        except Exception:  # noqa: BLE001
            return {}

    def _save(self) -> None:
        try:
            tmp = self.path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(self._data, fh)
            os.replace(tmp, self.path)      # atomic
        except Exception as exc:  # noqa: BLE001
            log.warning("fundamentals save failed: %s", exc)

    def get(self, sym: str) -> dict | None:
        with self._lock:
            e = self._data.get(sym)
            return dict(e) if e else None

    def last_ts(self, sym: str) -> float:
        with self._lock:
            e = self._data.get(sym)
            return e.get("_ts", 0.0) if e else 0.0

    def record(self, sym: str, info: dict) -> None:
        with self._lock:
            self._data[sym] = {**info, "_ts": time.time()}
            self._save()


class FundamentalsWorker:
    """Fetches fundamentals for requested tickers on a background thread."""
    def __init__(self, store: FundamentalsStore):
        self.store = store
        self.q: "queue.Queue[str]" = queue.Queue()
        self._queued: set[str] = set()
        self._lock = threading.Lock()
        threading.Thread(target=self._run, daemon=True).start()

    def request(self, tickers, force: bool = False) -> None:
        now = time.time()
        for t in tickers:
            if not t:
                continue
            if not force and now - self.store.last_ts(t) < REFRESH_MIN_AGE:
                continue
            with self._lock:
                if t in self._queued:
                    continue
                self._queued.add(t)
            self.q.put(t)

    def _run(self) -> None:
        while True:
            t = self.q.get()
            try:
                self._fetch(t)
            except Exception as exc:  # noqa: BLE001
                log.warning("fundamentals %s failed: %s", t, exc)
            finally:
                with self._lock:
                    self._queued.discard(t)
            time.sleep(PACING)

    def _fetch(self, sym: str) -> None:
        import yfinance as yf
        raw = {}
        try:
            raw = yf.Ticker(sym).info or {}
        except Exception:  # noqa: BLE001 - .info is flaky; record what we can
            raw = {}
        out = {}
        for k in FIELDS:
            v = raw.get(k)
            if v is not None and v != "":
                out[k] = v
        # store even if partial (a name alone is still useful); empty -> mark tried
        self.store.record(sym, out)
