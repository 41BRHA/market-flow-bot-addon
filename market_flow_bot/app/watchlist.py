"""Runtime watchlist — extra tickers per sector, added at runtime (from the
dashboard) without editing the add-on config.

Persisted to /data/watchlist.json so it survives restarts and add-on updates.
The main loop merges these into each sector every cycle, so a newly added
ticker starts being fetched from Yahoo on the next poll automatically — and
flows through everything (map, drill-down, period views, move alerts).

Shape: {"Technology": ["PLTR", "SNOW"], "My Watchlist": ["ASML"], ...}
Adding a ticker under a NEW sector name creates that sector on the fly.
"""
from __future__ import annotations

import json
import os
import threading

_LOCK = threading.Lock()
_PATH = ("/data/watchlist.json" if os.path.isdir("/data")
         else os.path.join(os.path.dirname(__file__), "watchlist.json"))


def _clean_ticker(t: str) -> str | None:
    t = str(t).strip().upper().replace(".", "-")
    if not t or len(t) > 6:
        return None
    # allow equities (AAPL, BRK-B) and futures (GC=F)
    if not t.replace("-", "").replace("=", "").isalnum():
        return None
    return t


def load() -> dict[str, list[str]]:
    try:
        with open(_PATH, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return {str(k): [str(x) for x in v] for k, v in data.items() if isinstance(v, list)}
    except Exception:  # noqa: BLE001 - missing/corrupt file -> empty
        return {}


def _save(data: dict) -> None:
    try:
        with open(_PATH, "w", encoding="utf-8") as fh:
            json.dump(data, fh)
    except Exception:  # noqa: BLE001
        pass


def add(sector: str, ticker: str) -> dict[str, list[str]]:
    sector = (sector or "").strip()
    t = _clean_ticker(ticker)
    if not sector or not t:
        return load()
    with _LOCK:
        data = load()
        lst = data.setdefault(sector, [])
        if t not in lst:
            lst.append(t)
        _save(data)
        return data


def remove(sector: str, ticker: str) -> dict[str, list[str]]:
    t = _clean_ticker(ticker)
    with _LOCK:
        data = load()
        if sector in data and t in data[sector]:
            data[sector].remove(t)
            if not data[sector]:
                del data[sector]
        _save(data)
        return data
