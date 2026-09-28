"""Cooldown, US session gating, and small persisted state (leaderboard ranks)."""
from __future__ import annotations

import json
import os
from datetime import datetime, time
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")


class Cooldown:
    def __init__(self, minutes: int):
        self.window = minutes * 60
        self._last: dict[str, float] = {}

    def ready(self, key: str, now_ts: float) -> bool:
        last = self._last.get(key)
        if last is None or (now_ts - last) >= self.window:
            self._last[key] = now_ts
            return True
        return False


class Store:
    """Persist small state across restarts (last leaderboard ranks)."""
    def __init__(self):
        base = "/data" if os.path.isdir("/data") else os.path.dirname(__file__)
        self.path = os.path.join(base, "state.json")

    def load(self) -> dict:
        try:
            with open(self.path, "r", encoding="utf-8") as fh:
                return json.load(fh)
        except Exception:
            return {}

    def save(self, data: dict) -> None:
        try:
            with open(self.path, "w", encoding="utf-8") as fh:
                json.dump(data, fh)
        except Exception:
            pass


def active_session(sessions: dict[str, bool], now: datetime | None = None) -> str | None:
    now = (now or datetime.now(ET)).astimezone(ET)
    if now.weekday() >= 5:
        return None
    t = now.time()
    windows = {
        "premarket": (time(4, 0), time(9, 30)),
        "regular": (time(9, 30), time(16, 0)),
        "postmarket": (time(16, 0), time(20, 0)),
    }
    for name, (start, end) in windows.items():
        if sessions.get(name) and start <= t < end:
            return name
    return None
