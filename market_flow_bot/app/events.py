"""High-impact US economic events — free ForexFactory weekly XML feed.

Keeps only USD **High-impact** releases (FOMC, CPI, NFP, GDP, PCE, ISM, retail
sales…) — the ones that actually move the tape — and caches them. The free feed
carries schedule + forecast + previous; the printed 'actual' is not in it, so
that field stays blank until a result source is wired (follow-up).

The feed is unofficial and can change without notice, so failures keep the last
good cache and the panel degrades gracefully.
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from xml.etree import ElementTree as ET
from zoneinfo import ZoneInfo

import requests

log = logging.getLogger("events")

FEED_URL = "https://nfs.faireconomy.media/ff_calendar_thisweek.xml"
ET_TZ = ZoneInfo("America/New_York")
_PATH = ("/data/events.json" if os.path.isdir("/data")
         else os.path.join(os.path.dirname(__file__), "events.json"))


def _txt(el, tag: str) -> str:
    c = el.find(tag)
    return (c.text or "").strip() if c is not None and c.text else ""


def _parse_dt(date: str, tm: str):
    """date 'MM-DD-YYYY', time '8:30am' / 'All Day' / 'Tentative'. Returns ET-aware dt or None."""
    try:
        d = datetime.strptime(date.strip(), "%m-%d-%Y")
    except Exception:
        return None
    t = tm.strip().lower()
    if t in ("", "all day", "tentative", "day"):
        return d.replace(hour=0, minute=0, tzinfo=ET_TZ)
    try:
        parsed = datetime.strptime(t.replace(" ", ""), "%I:%M%p")
        return d.replace(hour=parsed.hour, minute=parsed.minute, tzinfo=ET_TZ)
    except Exception:
        return d.replace(hour=0, minute=0, tzinfo=ET_TZ)


def parse_feed(xml_bytes) -> list[dict]:
    root = ET.fromstring(xml_bytes)
    out: list[dict] = []
    for ev in root.findall(".//event"):
        if _txt(ev, "country").upper() != "USD":
            continue
        if _txt(ev, "impact").lower() != "high":
            continue
        dt = _parse_dt(_txt(ev, "date"), _txt(ev, "time"))
        out.append({
            "title": _txt(ev, "title"),
            "impact": "High",
            "datetime_utc": dt.astimezone(timezone.utc).isoformat() if dt else None,
            "forecast": _txt(ev, "forecast"),
            "previous": _txt(ev, "previous"),
            "actual": "",   # not in the free feed
        })
    out.sort(key=lambda e: e["datetime_utc"] or "")
    return out


def refresh():
    try:
        r = requests.get(FEED_URL, timeout=20, headers={"User-Agent": "Mozilla/5.0"})
        r.raise_for_status()
        evs = parse_feed(r.content)
        payload = {"events": evs, "updated": datetime.now(timezone.utc).isoformat(timespec="seconds")}
        with open(_PATH, "w", encoding="utf-8") as f:
            json.dump(payload, f)
        log.info("events refreshed: %d high-impact USD this week", len(evs))
        return payload
    except Exception as exc:  # noqa: BLE001 - keep last good cache
        log.warning("events refresh failed: %s", exc)
        return None


def enrich_actuals() -> bool:
    """Fill 'actual' on cached events from government sources (best-effort)."""
    from . import actuals
    data = load()
    evs = data.get("events") or []
    if not evs:
        return False
    if actuals.enrich(evs):
        data["updated"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        try:
            with open(_PATH, "w", encoding="utf-8") as f:
                json.dump(data, f)
        except Exception as exc:  # noqa: BLE001
            log.warning("events save failed: %s", exc)
        return True
    return False


def load() -> dict:
    try:
        with open(_PATH, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"events": [], "updated": None}
