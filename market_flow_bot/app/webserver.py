"""Tiny web server for the flow-map dashboard (served via HA ingress).

Runs in a daemon thread alongside the main loop. Serves:
  GET /            -> the flow-map HTML page
  GET /api/flow    -> the latest computed snapshot (written by the main loop to
                      /data/latest.json each cycle)

HA ingress proxies these under an authenticated URL and strips its own prefix,
so the page uses relative paths (./api/flow) and works unchanged.
"""
from __future__ import annotations

import json
import logging
import os
import threading
import re
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

log = logging.getLogger("web")

_HTML_PATH = os.path.join(os.path.dirname(__file__), "flow_map.html")
_LATEST_PATH = ("/data/latest.json" if os.path.isdir("/data")
                else os.path.join(os.path.dirname(__file__), "latest.json"))

_SMART_URL = ""
_ENGINE = None   # PeriodEngine, set by start()
_FUNDAMENTALS = None
_DISPLAY_TIMEZONE = "Europe/London"


def company_name(ticker: str, wait_seconds: float = 6.0) -> str:
    """Return a cached company name, briefly allowing a queued lookup to finish."""
    service = _FUNDAMENTALS
    if service is None:
        return ""
    deadline = time.monotonic() + max(0.0, wait_seconds)
    while True:
        profile = service.get(ticker)
        name = str(profile.get("name") or "").strip()
        if name and name.upper() != ticker.upper():
            return name
        if time.monotonic() >= deadline or profile.get("error"):
            return ""
        time.sleep(0.2)


def _parse_range(frm: str, to: str):
    """(start_ts, end_ts) from ISO date/datetime strings, with the end made
    INCLUSIVE: a bare 'to' date covers that whole day, so from==to returns the
    full day instead of a zero-length window."""
    from datetime import datetime, timedelta, timezone
    start = datetime.fromisoformat(frm)
    end = datetime.fromisoformat(to)
    start = start.replace(tzinfo=timezone.utc) if start.tzinfo is None else start.astimezone(timezone.utc)
    end = end.replace(tzinfo=timezone.utc) if end.tzinfo is None else end.astimezone(timezone.utc)
    if len(to) <= 10:          # bare date (YYYY-MM-DD) -> include the end day
        end = end + timedelta(days=1)
    if end <= start:
        raise ValueError("end must be after start")
    return start.timestamp(), end.timestamp()


def write_latest(payload: dict) -> None:
    """Called by the main loop each cycle to publish the current snapshot."""
    try:
        tmp = _LATEST_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(payload, fh)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, _LATEST_PATH)
    except Exception as exc:  # noqa: BLE001
        log.warning("could not write latest.json: %s", exc)


class _Handler(BaseHTTPRequestHandler):
    def _send(self, code, body: bytes, ctype: str):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):  # noqa: N802
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"
        if path == "/api/politician-trades":
            from .politicians import get_trades
            ticker = (parse_qs(parsed.query).get("ticker") or [""])[0]
            return self._send(200, json.dumps(get_trades(_SMART_URL, ticker)).encode(), "application/json")
        if path == "/api/settings":
            return self._send(200, json.dumps({"display_timezone": _DISPLAY_TIMEZONE}).encode(), "application/json")
        if path == "/api/trade-prices":
            if _ENGINE is None:
                return self._send(200, b'{"error":"engine not ready","prices":{}}', "application/json")
            raw_pairs = parse_qs(parsed.query).get("pair") or []
            pairs = []
            for raw in raw_pairs[:100]:
                try:
                    symbol, day = raw.split("|", 1)
                except ValueError:
                    continue
                symbol = symbol.strip().upper().replace(".", "-")
                if re.fullmatch(r"[A-Z][A-Z0-9-]{0,14}", symbol) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", day):
                    pairs.append((symbol, day))
            return self._send(200, json.dumps(_ENGINE.trade_prices(pairs)).encode(), "application/json")
        if path in ("/api/stock-snapshots", "/api/stock-snapshot"):
            if _ENGINE is None:
                return self._send(200,b'{"error":"engine not ready","stocks":{}}',"application/json")
            q=parse_qs(parsed.query);raw=q.get("ticker") or []
            tickers=[]
            for value in raw[:100]:
                symbol=value.strip().upper().replace(".","-")
                if re.fullmatch(r"[A-Z][A-Z0-9-]{0,14}",symbol) and symbol not in tickers:tickers.append(symbol)
            result=_ENGINE.stock_snapshots(tickers)
            if _FUNDAMENTALS is not None:
                details=(q.get("details") or [""])[0] in ("1","true","yes")
                fundamentals=_FUNDAMENTALS.get_many(tickers)
                for symbol,fund in fundamentals.items():
                    stock=result["stocks"].setdefault(symbol,{})
                    if fund.get("sector") and stock.get("sector")=="Unclassified":stock["sector"]=fund["sector"]
                    stock.update(company_name=fund.get("name"),industry=fund.get("industry"),
                                 market_cap=fund.get("market_cap"),trailing_pe=fund.get("trailing_pe"),
                                 forward_pe=fund.get("forward_pe"),fundamentals_pending=fund.get("pending",False))
                    if details:stock["fundamentals"]=fund
            return self._send(200,json.dumps(result).encode(),"application/json")
        if path == "/api/fundamentals":
            ticker = (parse_qs(parsed.query).get("ticker") or [""])[0]
            result = _FUNDAMENTALS.get(ticker) if _FUNDAMENTALS is not None else {"ticker": ticker, "pending": True}
            return self._send(200, json.dumps(result).encode(), "application/json")
        if path == "/api/events":
            from . import events as _ev
            if (parse_qs(parsed.query).get("refresh") or [None])[0]:
                try:                       # manual refresh button -> re-pull now
                    _ev.refresh()
                    _ev.enrich_actuals()
                except Exception as exc:   # noqa: BLE001 - serve last cache on failure
                    log.warning("manual events refresh failed: %s", exc)
            return self._send(200, json.dumps(_ev.load()).encode(), "application/json")
        if path == "/api/watch":
            from . import watchlist as _wl
            q = parse_qs(parsed.query)
            sector = (q.get("sector") or [""])[0]
            add = (q.get("add") or [None])[0]
            rem = (q.get("remove") or [None])[0]
            if add:
                data = _wl.add(sector, add)
            elif rem:
                data = _wl.remove(sector, rem)
            else:
                data = _wl.load()
            return self._send(200, json.dumps({"watchlist": data}).encode(), "application/json")
        if path == "/api/sector":
            if _ENGINE is None:
                return self._send(200, b'{"error":"engine not ready"}', "application/json")
            q = parse_qs(parsed.query)
            name = (q.get("name") or [""])[0]
            period = (q.get("period") or [None])[0]
            frm = (q.get("from") or [None])[0]
            to = (q.get("to") or [None])[0]
            try:
                start = end = None
                if frm and to:
                    start, end = _parse_range(frm, to)
                snap = _ENGINE.sector_detail(name, period=period, start=start, end=end)
                return self._send(200, json.dumps(snap).encode(), "application/json")
            except Exception as exc:  # noqa: BLE001
                return self._send(200, json.dumps({"error": str(exc)}).encode(), "application/json")
        if path == "/api/flow":
            q = parse_qs(parsed.query)
            period = (q.get("period") or [None])[0]
            frm = (q.get("from") or [None])[0]
            to = (q.get("to") or [None])[0]
            # Live (no params) -> the latest snapshot the main loop wrote.
            if not period and not (frm and to):
                try:
                    with open(_LATEST_PATH, "rb") as fh:
                        return self._send(200, fh.read(), "application/json")
                except Exception:
                    return self._send(200, b"{}", "application/json")
            # A period or explicit date range -> served from the engine's snapshot
            # cache. This NEVER blocks on a Yahoo fetch: it returns cached data
            # instantly, or {"pending": true} while a background compute runs.
            if _ENGINE is None:
                return self._send(200, b'{"error":"engine not ready"}', "application/json")
            try:
                start = end = None
                if frm and to:
                    start, end = _parse_range(frm, to)
                snap = _ENGINE.get(period=period, start=start, end=end)
                return self._send(200, json.dumps(snap).encode(), "application/json")
            except Exception as exc:  # noqa: BLE001
                log.warning("period get failed: %s", exc)
                return self._send(200, json.dumps({"error": str(exc)}).encode(), "application/json")
        if path in ("/", "/index.html"):
            try:
                with open(_HTML_PATH, "rb") as fh:
                    return self._send(200, fh.read(), "text/html; charset=utf-8")
            except Exception as exc:  # noqa: BLE001
                return self._send(500, f"page missing: {exc}".encode(), "text/plain")
        self._send(404, b"not found", "text/plain")

    def log_message(self, *_args):  # silence per-request logging
        pass


def start(port: int = 8099, engine=None, smart_money_url="", display_timezone="Europe/London") -> None:
    global _ENGINE, _SMART_URL, _FUNDAMENTALS, _DISPLAY_TIMEZONE
    _ENGINE = engine
    _SMART_URL = smart_money_url
    _DISPLAY_TIMEZONE = display_timezone
    from .fundamentals import FundamentalsService
    _FUNDAMENTALS = FundamentalsService()

    def _run():
        try:
            srv = ThreadingHTTPServer(("0.0.0.0", port), _Handler)
            log.info("dashboard web server on :%d", port)
            srv.serve_forever()
        except Exception as exc:  # noqa: BLE001
            log.warning("web server stopped: %s", exc)
    threading.Thread(target=_run, daemon=True).start()
