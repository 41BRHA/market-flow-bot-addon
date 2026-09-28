"""Free provider using yfinance, with polite batching + backoff.

Data is ~15-min delayed (fine for basket rotation). Includes pre/post-market
bars. To avoid Yahoo's "Too Many Requests" throttle when fetching a few hundred
symbols, requests are split into batches with a short pause between them, and
rate-limit errors trigger an exponential backoff + retry rather than dropping
the batch.
"""
from __future__ import annotations

import logging
import time

import pandas as pd

log = logging.getLogger("provider.yahoo")

BATCH_SIZE = 40          # symbols per Yahoo request
BATCH_PAUSE = 1.5        # seconds between batches (politeness)
MAX_RETRIES = 4          # per batch on rate-limit
BACKOFF_BASE = 5         # seconds, doubles each retry


class YahooProvider:
    name = "yahoo"

    def __init__(self):
        self._tz_set = False

    def _ensure_tz(self, yf):
        if self._tz_set:
            return
        try:
            yf.set_tz_cache_location("/tmp/py-yfinance")
        except Exception:
            pass
        self._tz_set = True

    def _download_batch(self, yf, batch, interval, lookback):
        """Download one batch with backoff on rate-limit. Returns raw yf frame or None."""
        for attempt in range(MAX_RETRIES):
            try:
                data = yf.download(
                    tickers=batch, period=lookback, interval=interval,
                    group_by="ticker", prepost=True, auto_adjust=False,
                    threads=True, progress=False,
                )
                return data
            except Exception as exc:  # noqa: BLE001
                msg = str(exc).lower()
                if "too many requests" in msg or "rate limit" in msg:
                    wait = BACKOFF_BASE * (2 ** attempt)
                    log.warning("rate-limited on batch of %d; backing off %ds (attempt %d/%d)",
                                len(batch), wait, attempt + 1, MAX_RETRIES)
                    time.sleep(wait)
                    continue
                log.warning("batch download error (%s); skipping this batch", exc)
                return None
        log.warning("batch still rate-limited after %d retries; skipping", MAX_RETRIES)
        return None

    def _extract(self, data, batch, out):
        for sym in batch:
            try:
                df = data if len(batch) == 1 else data[sym]
                frame = pd.DataFrame(
                    {"close": df["Close"], "volume": df["Volume"]}
                ).dropna(subset=["close"])
                if not frame.empty:
                    out[sym] = frame
            except Exception:  # noqa: BLE001 - one bad symbol shouldn't kill the batch
                pass

    def get_bars(self, symbols, interval: str = "5m", lookback: str = "5d") -> dict:
        import yfinance as yf
        self._ensure_tz(yf)

        out: dict[str, pd.DataFrame] = {}
        if not symbols:
            return out

        batches = [symbols[i:i + BATCH_SIZE] for i in range(0, len(symbols), BATCH_SIZE)]
        for i, batch in enumerate(batches):
            data = self._download_batch(yf, batch, interval, lookback)
            if data is not None:
                self._extract(data, batch, out)
            if i < len(batches) - 1:
                time.sleep(BATCH_PAUSE)

        got, want = len(out), len(symbols)
        if got < want:
            log.info("fetched %d/%d symbols (%d missing this cycle)", got, want, want - got)
        return out

    def get_range(self, symbols, interval, start_ts, end_ts) -> dict:
        """Fetch bars for an explicit [start_ts, end_ts] epoch window at `interval`.
        Used by the period backend to gap-fill history. Same batching + backoff."""
        import yfinance as yf
        from datetime import datetime, timezone
        self._ensure_tz(yf)
        out: dict[str, pd.DataFrame] = {}
        if not symbols:
            return out
        start = datetime.fromtimestamp(start_ts, tz=timezone.utc)
        end = datetime.fromtimestamp(end_ts, tz=timezone.utc)
        batches = [symbols[i:i + BATCH_SIZE] for i in range(0, len(symbols), BATCH_SIZE)]
        for bi, batch in enumerate(batches):
            for attempt in range(MAX_RETRIES):
                try:
                    data = yf.download(tickers=batch, start=start, end=end, interval=interval,
                                       group_by="ticker", prepost=True, auto_adjust=False,
                                       threads=True, progress=False)
                    self._extract(data, batch, out)
                    break
                except Exception as exc:  # noqa: BLE001
                    if "too many requests" in str(exc).lower() or "rate limit" in str(exc).lower():
                        time.sleep(BACKOFF_BASE * (2 ** attempt))
                        continue
                    log.warning("range fetch error (%s); skipping batch", exc)
                    break
            if bi < len(batches) - 1:
                time.sleep(BATCH_PAUSE)
        return out
