"""US economic events — free ForexFactory weekly XML feeds.

Keeps USD releases at every impact tier (Low / Medium / High), tagged with a
numeric `level` (1/2/3) so the dashboard can filter by importance (1-dot shows
all, 2-dot shows medium+high, 3-dot shows high only). Both the current and the
next week are fetched so the panel can scroll forward day by day. The free feed
carries schedule + forecast + previous; the printed 'actual' is not in it, so
that field is filled from government sources (see actuals.py) once released.

The feed is unofficial and can change without notice, so failures keep the last
good cache and the panel degrades gracefully.
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from xml.etree import ElementTree as ET
import requests

log = logging.getLogger("events")

FEED_URLS = [
    "https://nfs.faireconomy.media/ff_calendar_thisweek.xml",
    "https://nfs.faireconomy.media/ff_calendar_nextweek.xml",
]
# impact string (lower-case) -> importance level (dots). Anything else is dropped.
IMPACT_LEVEL = {"high": 3, "medium": 2, "low": 1}
# The Fair Economy XML feed publishes its clock values in GMT/UTC.  Treating
# them as New York local time shifts US releases several hours late when the
# dashboard converts them to the configured display timezone.
FEED_TZ = timezone.utc
_PATH = ("/data/events.json" if os.path.isdir("/data")
         else os.path.join(os.path.dirname(__file__), "events.json"))


def _txt(el, tag: str) -> str:
    c = el.find(tag)
    return (c.text or "").strip() if c is not None and c.text else ""


def _parse_dt(date: str, tm: str):
    """Parse the feed's GMT/UTC date and clock into an aware datetime."""
    try:
        d = datetime.strptime(date.strip(), "%m-%d-%Y")
    except Exception:
        return None
    t = tm.strip().lower()
    if t in ("", "all day", "tentative", "day"):
        return d.replace(hour=0, minute=0, tzinfo=FEED_TZ)
    try:
        parsed = datetime.strptime(t.replace(" ", ""), "%I:%M%p")
        return d.replace(hour=parsed.hour, minute=parsed.minute, tzinfo=FEED_TZ)
    except Exception:
        return d.replace(hour=0, minute=0, tzinfo=FEED_TZ)


def parse_feed(xml_bytes) -> list[dict]:
    root = ET.fromstring(xml_bytes)
    out: list[dict] = []
    for ev in root.findall(".//event"):
        if _txt(ev, "country").upper() != "USD":
            continue
        level = IMPACT_LEVEL.get(_txt(ev, "impact").lower())
        if level is None:      # drop holidays / non-economic / blank impact
            continue
        dt = _parse_dt(_txt(ev, "date"), _txt(ev, "time"))
        out.append({
            "title": _txt(ev, "title"),
            "impact": _txt(ev, "impact").title(),   # High / Medium / Low
            "level": level,                          # 3 / 2 / 1  (dots)
            "datetime_utc": dt.astimezone(timezone.utc).isoformat() if dt else None,
            "forecast": _txt(ev, "forecast"),
            "previous": _txt(ev, "previous"),
            "actual": "",   # not in the free feed; filled by actuals.py after release
        })
    return out


def refresh():
    """Fetch this week + next week, merge, de-dupe, and cache."""
    previous = load()
    previous_actuals = {(e.get("title"), e.get("datetime_utc")): e
                        for e in previous.get("events", []) if e.get("actual")}
    collected: list[dict] = []
    ok = False
    for url in FEED_URLS:
        try:
            r = requests.get(url, timeout=20, headers={"User-Agent": "Mozilla/5.0"})
            r.raise_for_status()
            collected += parse_feed(r.content)
            ok = True
        except Exception as exc:  # noqa: BLE001 - a missing next-week feed is fine
            log.warning("events feed %s failed: %s", url.rsplit("/", 1)[-1], exc)
    if not ok:
        log.warning("events refresh failed: no feed reachable; keeping last cache")
        return None
    # de-dupe on (title, datetime) and sort chronologically
    seen, evs = set(), []
    for e in collected:
        k = (e["title"], e["datetime_utc"])
        if k in seen:
            continue
        old = previous_actuals.get(k)
        if old and "adp" not in str(e.get("title", "")).lower():
            e["actual"] = old.get("actual", "")
            if old.get("actual_at"):
                e["actual_at"] = old["actual_at"]
            if old.get("actual_source"):
                e["actual_source"] = old["actual_source"]
        seen.add(k)
        evs.append(e)
    evs.sort(key=lambda e: e["datetime_utc"] or "")
    payload = {"events": evs, "updated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
               "actual_checked": previous.get("actual_checked"),
               "actuals_updated": previous.get("actuals_updated")}
    try:
        with open(_PATH, "w", encoding="utf-8") as f:
            json.dump(payload, f)
    except Exception as exc:  # noqa: BLE001
        log.warning("events save failed: %s", exc)
    by_lvl = {3: 0, 2: 0, 1: 0}
    for e in evs:
        by_lvl[e["level"]] = by_lvl.get(e["level"], 0) + 1
    log.info("events refreshed: %d USD (high %d / med %d / low %d)",
             len(evs), by_lvl[3], by_lvl[2], by_lvl[1])
    return payload


def enrich_actuals() -> bool:
    """Fill 'actual' on cached events from government sources (best-effort)."""
    from . import actuals
    data = load()
    evs = data.get("events") or []
    if not evs:
        return False
    checked = datetime.now(timezone.utc).isoformat(timespec="seconds")
    changed = actuals.enrich(evs)
    data["actual_checked"] = checked
    if changed:
        data["actuals_updated"] = checked
    try:
        with open(_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f)
    except Exception as exc:  # noqa: BLE001
        log.warning("events save failed: %s", exc)
    return changed


def load() -> dict:
    try:
        with open(_PATH, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"events": [], "updated": None}
