"""Actual released values for high-impact events — straight from the source.

No calendar vendor has the 'actual' free; the government publishes it. Adapters:
  * BLS Public API v1 (no key, JSON)  -> CPI, Core CPI, NFP
  * Federal Reserve monetary RSS       -> FOMC rate decision
  * FRED graph CSV (no key)            -> GDP, PCE, Core PCE

Consensus/forecast stays from the ForexFactory feed (events.py); we only fill the
`actual` here. Fetches happen only for events whose scheduled time has passed and
whose actual is still blank, so traffic is near-zero (well under BLS's 25/day).

Parsing gov output is inherently brittle and can't be tested without the live
endpoints, so every adapter fails soft (returns None) and is easy to tweak once
we see a real release.
"""
from __future__ import annotations

import csv
import html
import io
import logging
import re
from datetime import date, datetime, timezone

import requests

log = logging.getLogger("actuals")

UA = {"User-Agent": "Mozilla/5.0"}
BLS_V1 = "https://api.bls.gov/publicAPI/v1/timeseries/data/{sid}"
FRED_CSV = "https://fred.stlouisfed.org/graph/fredgraph.csv?id={sid}"
FED_RSS = "https://www.federalreserve.gov/feeds/press_monetary.xml"
BEA_GDP = "https://www.bea.gov/data/gdp/gross-domestic-product"
BEA_PCE = "https://www.bea.gov/data/income-saving/personal-income"


# ---- source fetchers -------------------------------------------------------
def _bls_series(series_id: str):
    """Return recent [(year, month_int, value_float)] newest-first, or []."""
    try:
        r = requests.get(BLS_V1.format(sid=series_id), timeout=20, headers=UA)
        r.raise_for_status()
        data = r.json()["Results"]["series"][0]["data"]
        out = []
        for d in data:
            per = d.get("period", "")
            if not per.startswith("M"):
                continue
            out.append((int(d["year"]), int(per[1:]), float(d["value"])))
        return out  # BLS returns newest-first already
    except Exception as exc:  # noqa: BLE001
        log.warning("BLS %s failed: %s", series_id, exc)
        return []


def _fred_series(series_id: str):
    """Return [(date, value_float)] oldest-first from FRED graph CSV, or []."""
    try:
        r = requests.get(FRED_CSV.format(sid=series_id), timeout=20, headers=UA)
        r.raise_for_status()
        rows = list(csv.reader(io.StringIO(r.text)))
        out = []
        for row in rows[1:]:
            if len(row) < 2 or row[1] in (".", ""):
                continue
            try:
                out.append((row[0], float(row[1])))
            except ValueError:
                continue
        return out
    except Exception as exc:  # noqa: BLE001
        log.warning("FRED %s failed: %s", series_id, exc)
        return []


def _bea_text(url: str):
    """Read the current BEA release summary without requiring an API key."""
    try:
        r=requests.get(url,timeout=20,headers=UA);r.raise_for_status()
        text=re.sub(r"<script[\s\S]*?</script>|<style[\s\S]*?</style>"," ",r.text,flags=re.I)
        return ' '.join(html.unescape(re.sub(r"<[^>]+>"," ",text)).split())
    except Exception as exc:  # noqa: BLE001
        log.warning("BEA release page failed: %s",exc);return ''


def _signed_percent(text, pattern):
    m=re.search(pattern,text,re.I)
    if not m:return None
    value=float(m.group(2));return -value if m.group(1).lower()=='decreased' else value


# ---- freshness gate --------------------------------------------------------
# The government API can lag the printed release: filling an event's "actual"
# from a series that hasn't updated yet would label an OLD month/quarter as the
# new number (and it's then retained). So only fill when the newest data point
# is recent enough to plausibly BE the release we're waiting on.
MONTHLY_MAX_DAYS = 70
QUARTERLY_MAX_DAYS = 150

def _fresh_bls(series, max_days=MONTHLY_MAX_DAYS) -> bool:
    if not series:
        return False
    y, m, _ = series[0]                            # newest-first
    try:
        return (datetime.now(timezone.utc).date() - date(y, m, 1)).days <= max_days
    except Exception:  # noqa: BLE001
        return False

def _fresh_fred(series, max_days=MONTHLY_MAX_DAYS) -> bool:
    if not series:
        return False
    try:
        return (datetime.now(timezone.utc).date() - date.fromisoformat(series[-1][0])).days <= max_days
    except Exception:  # noqa: BLE001
        return False


# ---- per-event actuals -----------------------------------------------------
def _mom(series):
    return None if len(series) < 2 else (series[0][2] / series[1][2] - 1) * 100

def _yoy(series):
    return None if len(series) < 13 else (series[0][2] / series[12][2] - 1) * 100

def cpi_mom():
    s = _bls_series("CUSR0000SA0")                 # CPI-U, seasonally adjusted
    if not _fresh_bls(s):
        return None
    v = _mom(s)
    return None if v is None else f"{v:+.1f}% MoM"

def cpi_yoy():
    s = _bls_series("CUUR0000SA0")                 # CPI-U, NSA (YoY basis)
    if not _fresh_bls(s):
        return None
    v = _yoy(s)
    return None if v is None else f"{v:.1f}% YoY"

def core_cpi_mom():
    s = _bls_series("CUSR0000SA0L1E")               # Core CPI SA
    if not _fresh_bls(s):
        return None
    v = _mom(s)
    return None if v is None else f"{v:+.1f}% MoM"

def nfp():
    s = _bls_series("CES0000000001")               # Total nonfarm payrolls (level, thousands)
    if len(s) < 2 or not _fresh_bls(s):
        return None
    chg = (s[0][2] - s[1][2]) * 1000
    return f"{chg:+,.0f}"

def fomc_rate():
    try:
        rss = requests.get(FED_RSS, timeout=20, headers=UA).text
        m = re.search(r"https?://[^\s<]+(?:monetary|pressreleases)[^\s<]+\.htm", rss)
        if not m:
            return None
        html = requests.get(m.group(0), timeout=20, headers=UA).text
        rng = re.search(r"target range for the federal funds rate (?:to|at)\s*([\d.]+)\s*(?:to|–|-)\s*([\d.]+)\s*percent", html, re.I)
        if rng:
            return f"{float(rng.group(1)):.2f}–{float(rng.group(2)):.2f}%"
        return None
    except Exception as exc:  # noqa: BLE001
        log.warning("FOMC fetch failed: %s", exc)
        return None

def _fred_mom(sid):
    s = _fred_series(sid)
    if len(s) < 2 or not _fresh_fred(s):
        return None
    return (s[-1][1] / s[-2][1] - 1) * 100

def pce_mom():
    text=_bea_text(BEA_PCE)
    v=_signed_percent(text,r"From the preceding month, the PCE price index[^.]*?\b(increased|decreased)\s+([\d.]+)\s+percent")
    if v is None:v = _fred_mom("PCEPI")
    return None if v is None else f"{v:+.1f}% MoM"

def core_pce_mom():
    text=_bea_text(BEA_PCE)
    v=_signed_percent(text,r"Excluding food and energy, the PCE price index[^.]*?\b(increased|decreased)\s+([\d.]+)\s+percent")
    if v is None:v = _fred_mom("PCEPILFE")
    return None if v is None else f"{v:+.1f}% MoM"

def gdp():
    text=_bea_text(BEA_GDP)
    v=_signed_percent(text,r"real gross domestic product[^.]*?\b(increased|decreased)\s+at an annual rate of\s+([\d.]+)\s+percent")
    if v is not None:return f"{v:+.1f}% (annualised)"
    s = _fred_series("A191RL1Q225SBEA")             # real GDP, annualised QoQ %
    if not s or not _fresh_fred(s, QUARTERLY_MAX_DAYS):
        return None
    return f"{s[-1][1]:+.1f}% (annualised)"


# title predicate -> fetcher (first match wins)
MATCHERS = [
    (lambda t: "core cpi" in t, core_cpi_mom),
    (lambda t: "cpi" in t and ("y/y" in t or "yoy" in t), cpi_yoy),
    (lambda t: "cpi" in t, cpi_mom),
    (lambda t: "adp" not in t and any(k in t for k in ("non-farm", "nonfarm", "non farm", "payroll")), nfp),
    (lambda t: any(k in t for k in ("fomc", "federal funds", "rate decision", "interest rate")), fomc_rate),
    (lambda t: "core pce" in t, core_pce_mom),
    (lambda t: "pce" in t, pce_mom),
    (lambda t: "gdp" in t and "price" not in t, gdp),
]


def actual_for(title: str):
    t = title.lower()
    for pred, fn in MATCHERS:
        if pred(t):
            try:
                return fn()
            except Exception as exc:  # noqa: BLE001
                log.warning("actual fetch for %r failed: %s", title, exc)
                return None
    return None


def enrich(events: list[dict], within_hours: float = 24 * 8) -> bool:
    """Fill `actual` for events whose scheduled time passed within the last
    `within_hours` and is still blank.  The default deliberately revisits the
    previous week: official series and free feeds can lag a release, and an
    add-on restart must not permanently strand yesterday's value as blank.
    Returns True if anything changed."""
    now = datetime.now(timezone.utc)
    changed = False
    for ev in events:
        if ev.get("actual") or not ev.get("datetime_utc"):
            continue
        try:
            when = datetime.fromisoformat(ev["datetime_utc"])
        except Exception:
            continue
        age_h = (now - when).total_seconds() / 3600.0
        if 0 <= age_h <= within_hours:
            val = actual_for(ev.get("title", ""))
            if val:
                ev["actual"] = val
                ev["actual_at"] = now.isoformat(timespec="seconds")
                ev["actual_source"] = "official government release"
                changed = True
                log.info("actual filled: %s = %s", ev["title"], val)
    return changed
