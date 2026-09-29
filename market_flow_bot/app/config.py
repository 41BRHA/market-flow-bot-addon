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
    alert_on_summary: bool = True    # periodic "into X / out of Y" digest
    alert_on_flip: bool = True       # money reverses direction with conviction
    alert_on_strong: bool = True     # strong one-directional flow on heavy volume
    min_flip_ratio: float = 0.40     # |flow_ratio| needed to call a flip
    strong_ratio: float = 0.65       # |flow_ratio| needed to call it "strong"
    min_rvol: float = 1.0            # volume must be this x its baseline to alert
    min_density: float = 0.5         # fraction of the window that must have traded (kills thin pre-market)
    suppress_startup: bool = True    # no alerts on the first cycle after a restart
    # price-move alerts (separate from the flow signals above)
    alert_on_sector_move: bool = True   # notify when a sector's average % move crosses the threshold
    sector_move_pct: float = 2.0        # |sector avg %| to alert
    alert_on_stock_move: bool = True    # notify when a single stock's % move crosses the threshold
    stock_move_pct: float = 7.0         # |stock %| to alert
    move_reference: str = "prev_close"  # "prev_close" (incl. overnight gap) or "session_open"
    move_cooldown_minutes: int = 240    # per-symbol quiet period so a big move isn't re-alerted every cycle


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
class Config:
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

    @classmethod
    def load(cls) -> "Config":
        raw = _load_raw()
        th = raw.get("thresholds", {})
        nt = raw.get("notify", {})
        wb = raw.get("webull", {})
        al = raw.get("alerts", {})
        sectors = [Sector(name=s["name"], symbols=list(s.get("symbols", [])), holdings_etf=s.get("holdings_etf", "")) for s in raw.get("sectors", [])]
        return cls(
            data_source=raw.get("data_source", "yahoo"),
            poll_interval_seconds=int(raw.get("poll_interval_seconds", 900)),
            sessions=raw.get("sessions", {"premarket": True, "regular": True, "postmarket": False}),
            benchmark=list(raw.get("benchmark", ["SPY"])),
            sectors=sectors,
            thresholds=Thresholds(**{k: th[k] for k in th if k in Thresholds.__annotations__}),
            alerts=AlertCfg(**{k: al[k] for k in al if k in AlertCfg.__annotations__}),
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
        )

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
