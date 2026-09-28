"""Send alerts + publish the leaderboard as an HA sensor.

Alerts go through an HA notify.* service via the Supervisor proxy, so HA fans
them out to your existing notifiers. The leaderboard is also written to
sensor.market_flow_leaderboard so you can put it on a dashboard.
"""
from __future__ import annotations

import logging
import os

import requests

log = logging.getLogger("notify")

SUPERVISOR_TOKEN = os.environ.get("SUPERVISOR_TOKEN", "")
CORE_API = "http://supervisor/core/api"


class Notifier:
    def __init__(self, cfg, publish_sensor: bool = True):
        self.ha_service = cfg.ha_service or "notify.notify"
        self.tg_token = cfg.telegram_bot_token
        self.tg_chat = cfg.telegram_chat_id
        self.publish_sensor = publish_sensor

    def send(self, title: str, message: str) -> None:
        sent = False
        if SUPERVISOR_TOKEN and self.ha_service.startswith("notify."):
            sent = self._send_ha(title, message) or sent
        if self.tg_token and self.tg_chat:
            sent = self._send_telegram(title, message) or sent
        if not sent:
            log.info("ALERT (no channel): %s — %s", title, message)

    def publish_leaderboard(self, leaderboard, fetched=None, total_symbols=None, updated=None) -> None:
        if not (self.publish_sensor and SUPERVISOR_TOKEN and leaderboard):
            return
        rows = [
            {"rank": i + 1, "sector": s.name,
             "flow": round(s.flow_ratio, 3),                 # [-1,1] arrow value
             "direction": "in" if s.flow_ratio > 0 else "out" if s.flow_ratio < 0 else "flat",
             "net_dollar": round(s.net_dollar),               # signed $ on the arrow
             "gross": round(s.gross),                         # total $ — ball size/value
             "breadth": (round(s.breadth) if s.breadth is not None else None),
             "rel_strength": round(s.rel_strength, 2),        # vs SPY (%)
             "rvol": round(s.rvol, 2)}
            for i, s in enumerate(leaderboard)
        ]
        attrs = {"friendly_name": "Market Flow leader",
                 "icon": "mdi:rotate-3d-variant",
                 "unit_of_measurement": None,
                 "sectors": rows}
        if fetched is not None:
            attrs["fetched"] = fetched
        if total_symbols is not None:
            attrs["total_symbols"] = total_symbols
        if updated is not None:
            attrs["updated"] = updated            # ISO-8601 UTC of this cycle
        try:
            requests.post(
                f"{CORE_API}/states/sensor.market_flow_leaderboard",
                headers={"Authorization": f"Bearer {SUPERVISOR_TOKEN}"},
                json={"state": leaderboard[0].name, "attributes": attrs},
                timeout=10,
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("sensor publish failed: %s", exc)

    def _send_ha(self, title: str, message: str) -> bool:
        domain, _, service = self.ha_service.partition(".")
        try:
            r = requests.post(f"{CORE_API}/services/{domain}/{service}",
                              headers={"Authorization": f"Bearer {SUPERVISOR_TOKEN}"},
                              json={"title": title, "message": message}, timeout=10)
            r.raise_for_status()
            return True
        except Exception as exc:  # noqa: BLE001
            log.warning("HA notify failed: %s", exc)
            return False

    def _send_telegram(self, title: str, message: str) -> bool:
        try:
            r = requests.post(f"https://api.telegram.org/bot{self.tg_token}/sendMessage",
                              json={"chat_id": self.tg_chat, "text": f"*{title}*\n{message}",
                                    "parse_mode": "Markdown"}, timeout=10)
            r.raise_for_status()
            return True
        except Exception as exc:  # noqa: BLE001
            log.warning("Telegram send failed: %s", exc)
            return False
