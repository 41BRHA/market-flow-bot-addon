"""Load add-on configuration (multi-sector).

HA writes options to /data/options.json. Sectors are a list of {name, symbols}
objects (the add-on options schema doesn't support dynamic dict keys), which also
lets a "sector" be a single ETF (XLK) or a multi-name theme basket (AI-Infra).
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

DEFAULT_OPTIONS_PATH = os.environ.get("OPTIONS_FILE", "/data/options.json")


def _load_raw() -> dict[str, Any]:
    for path in (DEFAULT_OPTIONS_PATH, os.path.join(os.path.dirname(__file__), "options.json")):
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as fh:
                return json.load(fh)
    return {}


@dataclass
class Thresholds:
    rel_ma_window: int = 20        # bars for the sector-vs-benchmark MA
    recent_bars: int = 6           # window for "fresh" short-term momentum
    flow_return_pct: float = 1.0   # |short-term return| to call a flow event
    volume_z: float = 2.5          # volume z-score to confirm a flow event
    rank_jump: int = 3             # leaderboard places moved to flag rotation
    breadth_ma_window: int = 10
    min_history: int = 30          # samples before history-based baselines kick in
    percentile_alert: float = 98.0 # volume percentile vs session history to flag


@dataclass
class AlertCfg:
    # quiet by default — only genuine, volume-backed shifts get through
    quiet_mode: bool = True          # conservative floors also apply to retained older options
    alert_on_summary: bool = False   # optional digest; quiet mode suppresses it
    alert_on_flip: bool = True       # money reverses direction with conviction
    alert_on_strong: bool = True     # strong one-directional flow on heavy volume
    min_flip_ratio: float = 0.55     # |flow_ratio| needed to call a flip
    strong_ratio: float = 0.75       # |flow_ratio| needed to call it "strong"
    min_rvol: float = 1.5            # volume must be this x its baseline to alert
    min_density: float = 0.75        # fraction of the window that must have traded (kills thin pre-market)
    min_breadth_pct: float = 70.0    # multi-stock sectors need this % aligned with the signal
    max_notifications_per_hour: int = 4
    suppress_startup: bool = True    # no alerts on the first cycle after a restart
    # price-move alerts (separate from the flow signals above)
    alert_on_sector_move: bool = True   # notify when a sector's average % move crosses the threshold
    sector_move_pct: float = 3.0        # |sector avg %| to alert
    alert_on_stock_move: bool = True    # notify when a single stock's % move crosses the threshold
    stock_move_pct: float = 10.0        # |stock %| to alert
    move_reference: str = "prev_close"  # "prev_close" (incl. overnight gap) or "session_open"
    move_cooldown_minutes: int = 480    # per-symbol quiet period so a big move isn't re-alerted every cycle

    def apply_quiet_floors(self) -> None:
        """Keep upgrades quiet when Home Assistant retains older options."""
        if not self.quiet_mode:
            return
        self.alert_on_summary = False
        self.min_flip_ratio = max(self.min_flip_ratio, 0.55)
        self.strong_ratio = max(self.strong_ratio, 0.75)
        self.min_rvol = max(self.min_rvol, 1.5)
        self.min_density = max(self.min_density, 0.75)
        self.min_breadth_pct = max(self.min_breadth_pct, 70.0)
        self.sector_move_pct = max(self.sector_move_pct, 3.0)
        self.stock_move_pct = max(self.stock_move_pct, 10.0)
        self.move_cooldown_minutes = max(self.move_cooldown_minutes, 480)
        self.max_notifications_per_hour = min(max(self.max_notifications_per_hour, 1), 4)


@dataclass
class Sector:
    name: str
    symbols: list[str] = field(default_factory=list)
    holdings_etf: str = ""


@dataclass
class NotifyCfg:
    ha_service: str = "notify.notify"
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""


@dataclass
class WebullCfg:
    app_key: str = ""
    app_secret: str = ""
    region: str = "us"


@dataclass
class ActivityCfg:
    enabled: bool = True
    options_enabled: bool = True
    options_candidates: int = 8
    options_refresh_minutes: int = 30
    app_highlight_score: float = 65.0
    notify_score: float = 85.0
    min_relative_volume: float = 1.8
    min_dollar_volume: float = 5_000_000.0
    notify_cooldown_minutes: int = 240
    max_rows: int = 700
    core_symbols_per_sector: int = 35
    broad_refresh_cycles: int = 4
    broad_symbols_per_sector: int = 60


@dataclass
class Config:
    display_timezone: str = "Europe/London"
    data_source: str = "yahoo"
    poll_interval_seconds: int = 900
    sessions: dict[str, bool] = field(default_factory=lambda: {"premarket": True, "regular": True, "postmarket": False})
    benchmark: list[str] = field(default_factory=lambda: ["SPY"])
    sectors: list[Sector] = field(default_factory=list)
    thresholds: Thresholds = field(default_factory=Thresholds)
    alerts: AlertCfg = field(default_factory=AlertCfg)
    cooldown_minutes: int = 30
    summary_cooldown_minutes: int = 60
    publish_sensor: bool = True
    auto_constituents: bool = True
    top_n: int = 30
    history_days: int = 180
    smart_money_url: str = "http://local-smart-money-tracker:8098"
    ingress_port: int = 8099
    notify: NotifyCfg = field(default_factory=NotifyCfg)
    webull: WebullCfg = field(default_factory=WebullCfg)
    activity: ActivityCfg = field(default_factory=ActivityCfg)

    @classmethod
    def load(cls) -> "Config":
        raw = _load_raw()
        th = raw.get("thresholds", {})
        nt = raw.get("notify", {})
        wb = raw.get("webull", {})
        activity = raw.get("activity", {})
        al = raw.get("alerts", {})
        sectors = [Sector(name=s["name"], symbols=list(s.get("symbols", [])), holdings_etf=s.get("holdings_etf", "")) for s in raw.get("sectors", [])]
        alerts = AlertCfg(**{k: al[k] for k in al if k in AlertCfg.__annotations__})
        alerts.apply_quiet_floors()
        return cls(
            display_timezone=cls.valid_timezone(raw.get("display_timezone", "Europe/London")),
            data_source=raw.get("data_source", "yahoo"),
            poll_interval_seconds=int(raw.get("poll_interval_seconds", 900)),
            sessions=raw.get("sessions", {"premarket": True, "regular": True, "postmarket": False}),
            benchmark=list(raw.get("benchmark", ["SPY"])),
            sectors=sectors,
            thresholds=Thresholds(**{k: th[k] for k in th if k in Thresholds.__annotations__}),
            alerts=alerts,
            cooldown_minutes=int(raw.get("cooldown_minutes", 30)),
            summary_cooldown_minutes=int(raw.get("summary_cooldown_minutes", 60)),
            publish_sensor=bool(raw.get("publish_sensor", True)),
            auto_constituents=bool(raw.get("auto_constituents", True)),
            top_n=int(raw.get("top_n", 30)),
            history_days=int(raw.get("history_days", 180)),
            ingress_port=int(raw.get("ingress_port", 8099)),
            smart_money_url=str(raw.get("smart_money_url", "http://local-smart-money-tracker:8098")),
            notify=NotifyCfg(**{k: nt[k] for k in nt if k in NotifyCfg.__annotations__}),
            webull=WebullCfg(**{k: wb[k] for k in wb if k in WebullCfg.__annotations__}),
            activity=ActivityCfg(**{k: activity[k] for k in activity if k in ActivityCfg.__annotations__}),
        )

    @staticmethod
    def valid_timezone(value: Any) -> str:
        value = str(value or "Europe/London").strip()
        try:
            ZoneInfo(value)
            return value
        except (ZoneInfoNotFoundError, ValueError):
            return "Europe/London"

    @property
    def all_symbols(self) -> list[str]:
        seen: list[str] = []
        for s in self.sectors:
            for sym in s.symbols:
                if sym not in seen:
                    seen.append(sym)
        for sym in self.benchmark:
            if sym not in seen:
                seen.append(sym)
        return seen
