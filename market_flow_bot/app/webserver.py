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
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

log = logging.getLogger("web")

_HTML_PATH = os.path.join(os.path.dirname(__file__), "flow_map.html")
_LATEST_PATH = ("/data/latest.json" if os.path.isdir("/data")
                else os.path.join(os.path.dirname(__file__), "latest.json"))

_ENGINE = None   # PeriodEngine, set by start()


def _parse_range(frm: str, to: str):
    """(start_ts, end_ts) from ISO date/datetime strings, with the end made
    INCLUSIVE: a bare 'to' date covers that whole day, so from==to returns the
    full day instead of a zero-length window."""
    from datetime import datetime, timedelta, timezone
    start = datetime.fromisoformat(frm).replace(tzinfo=timezone.utc)
    end = datetime.fromisoformat(to).replace(tzinfo=timezone.utc)
    if len(to) <= 10:          # bare date (YYYY-MM-DD) -> include the end day
        end = end + timedelta(days=1)
    return start.timestamp(), end.timestamp()


def write_latest(payload: dict) -> None:
    """Called by the main loop each cycle to publish the current snapshot."""
    try:
        with open(_LATEST_PATH, "w", encoding="utf-8") as fh:
            json.dump(payload, fh)
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


def start(port: int = 8099, engine=None) -> None:
    global _ENGINE
    _ENGINE = engine

    def _run():
        try:
            srv = ThreadingHTTPServer(("0.0.0.0", port), _Handler)
            log.info("dashboard web server on :%d", port)
            srv.serve_forever()
        except Exception as exc:  # noqa: BLE001
            log.warning("web server stopped: %s", exc)
    threading.Thread(target=_run, daemon=True).start()
