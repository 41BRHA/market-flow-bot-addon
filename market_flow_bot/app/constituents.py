"""Resolve each sector's constituents.

Preferred: pull the sector's SPDR Select Sector ETF *daily holdings* from State
Street and take the cap-weighted top-N (the actual biggest N companies in the
sector). Cached to /data and refreshed weekly.

Fallback: real S&P 500 sector membership baked into fallback.py, used if the
SSGA fetch/parse is unavailable. Theme baskets (explicit `symbols`) are used
as-is.

NOTE: the SSGA URL template and workbook layout could not be exercised from the
build sandbox (ssga.com not reachable there). The parser is defensive and falls
back cleanly, but validate the live fetch on first run — see DOCS.md.
"""
from __future__ import annotations

import io
import json
import logging
import os
import time

import requests

from .fallback import FALLBACK

log = logging.getLogger("constituents")

SSGA_URL = ("https://www.ssga.com/us/en/intermediary/etfs/library-content/"
            "products/fund-data/etfs/us/holdings-daily-us-en-{etf}.xlsx")
CACHE_MAX_AGE = 7 * 24 * 3600  # weekly refresh


def _clean(ticker: str) -> str | None:
    t = str(ticker).strip().upper().replace(".", "-")
    if not t or len(t) > 6:
        return None
    # Drop non-equity holdings: index/cash-hedge futures rows carry digits
    # (e.g. IXP26, XAS26, IXD26) and money-market/cash lines.
    if any(ch.isdigit() for ch in t):
        return None
    if not t.replace("-", "").isalpha():
        return None
    if t in {"CASH", "USD", "-", "SSIXX"}:
        return None
    return t


def fetch_ssga_holdings(etf: str, top_n: int) -> list[str]:
    """Download the daily holdings xlsx and return the top-N tickers by weight."""
    import pandas as pd

    url = SSGA_URL.format(etf=etf.lower())
    raw = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=30).content
    # The sheet has a few preamble rows; scan for the header row with Ticker+Weight.
    book = pd.read_excel(io.BytesIO(raw), header=None)
    hdr = None
    for i in range(min(15, len(book))):
        row = [str(x).strip().lower() for x in book.iloc[i].tolist()]
        if "ticker" in row and "weight" in row:
            hdr = i
            break
    if hdr is None:
        raise ValueError(f"{etf}: could not locate Ticker/Weight header")
    df = pd.read_excel(io.BytesIO(raw), header=hdr)
    cols = {c.lower().strip(): c for c in df.columns}
    tcol, wcol = cols.get("ticker"), cols.get("weight")
    df = df[[tcol, wcol]].copy()
    df[wcol] = pd.to_numeric(df[wcol].astype(str).str.replace("%", "").str.replace(",", "."), errors="coerce")
    df = df.dropna(subset=[wcol]).sort_values(wcol, ascending=False)
    out: list[str] = []
    for t in df[tcol]:
        c = _clean(t)
        if c and c not in out:
            out.append(c)
        if len(out) >= top_n:
            break
    if not out:
        raise ValueError(f"{etf}: no tickers parsed")
    return out


def _cache_path() -> str:
    base = "/data" if os.path.isdir("/data") else os.path.dirname(__file__)
    return os.path.join(base, "constituents_cache.json")


def _load_cache() -> dict:
    try:
        with open(_cache_path(), "r", encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return {}


def _save_cache(cache: dict) -> None:
    try:
        with open(_cache_path(), "w", encoding="utf-8") as fh:
            json.dump(cache, fh)
    except Exception:
        pass


def resolve(sectors, auto: bool, top_n: int) -> dict[str, list[str]]:
    """Return {sector_name: [symbols]} for the configured sectors."""
    cache = _load_cache()
    now = time.time()
    resolved: dict[str, list[str]] = {}

    for sec in sectors:
        # Theme basket with explicit symbols -> use as-is.
        if sec.symbols:
            resolved[sec.name] = list(sec.symbols)
            continue

        etf = getattr(sec, "holdings_etf", "") or ""
        if not etf:
            log.warning("%s has neither symbols nor holdings_etf; skipping", sec.name)
            continue

        if not auto:
            resolved[sec.name] = [etf]
            continue

        # fresh cache?
        entry = cache.get(etf)
        if entry and (now - entry.get("ts", 0)) < CACHE_MAX_AGE and entry.get("symbols"):
            cached = [c for c in (_clean(x) for x in entry["symbols"]) if c]
            resolved[sec.name] = cached[:top_n]
            continue

        # live fetch
        try:
            syms = fetch_ssga_holdings(etf, top_n)
            cache[etf] = {"ts": now, "symbols": syms}
            resolved[sec.name] = syms
            log.info("%s: %d holdings from SSGA (%s)", sec.name, len(syms), etf)
        except Exception as exc:  # noqa: BLE001
            fb = FALLBACK.get(sec.name)
            if fb:
                fb = [c for c in (_clean(x) for x in fb) if c]
                resolved[sec.name] = fb[:top_n] if len(fb) > top_n else fb
                log.warning("%s: SSGA fetch failed (%s); using %d fallback names",
                            sec.name, exc, len(resolved[sec.name]))
            else:
                resolved[sec.name] = [etf]
                log.warning("%s: SSGA + fallback unavailable (%s); using ETF only", sec.name, exc)

    _save_cache(cache)
    return resolved
