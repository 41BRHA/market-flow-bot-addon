"""Market Flow Bot — main loop (volume-flow engine)."""
from __future__ import annotations

import logging
from datetime import datetime, timezone
import time

from . import constituents, webserver, events, watchlist
from .config import Config, Sector
from .history import History
from .barstore import BarStore
from .period import PeriodEngine
from .maxpain import MaxPainStore, MaxPainWorker
from .notify import Notifier
from .providers import make_provider
from .signals import compute, pct_changes
from .state import Cooldown, Store, active_session

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("main")

INTERVAL = "5m"
LOOKBACK = "5d"
RESOLVE_EVERY = 6 * 3600


def _resolve(cfg) -> tuple[list[Sector], list[str]]:
    mapping = constituents.resolve(cfg.sectors, cfg.auto_constituents, cfg.top_n)
    # overlay the runtime watchlist — extra tickers added from the dashboard,
    # merged into existing sectors (or creating a new sector by name)
    for name, extra in watchlist.load().items():
        lst = mapping.setdefault(name, [])
        for s in extra:
            if s and s not in lst:
                lst.append(s)
    sectors = [Sector(name=n, symbols=syms) for n, syms in mapping.items() if syms]
    symbols: list[str] = []
    for sec in sectors:
        for s in sec.symbols:
            if s not in symbols:
                symbols.append(s)
    for s in cfg.benchmark:
        if s not in symbols:
            symbols.append(s)
    return sectors, symbols


def run() -> None:
    cfg = Config.load()
    if len(cfg.sectors) < 2:
        log.error("Need at least 2 sectors configured. Exiting.")
        return

    provider = make_provider(cfg)
    notifier = Notifier(cfg.notify, publish_sensor=cfg.publish_sensor)
    cooldown = Cooldown(cfg.cooldown_minutes)
    summary_cd = Cooldown(cfg.summary_cooldown_minutes)
    move_cd = Cooldown(cfg.alerts.move_cooldown_minutes)
    store = Store()
    saved = store.load()
    prev_dir = saved.get("dir", {})
    history = History()
    bars = BarStore()
    mp_store = MaxPainStore()
    def _spot(sym):
        import time as _t
        df = bars.get_bars("5m", sym, _t.time() - 7200, _t.time())
        return float(df["close"].iloc[-1]) if not df.empty else None
    mp_worker = MaxPainWorker(mp_store, spot_fn=_spot)
    last_prune = 0.0

    sectors, symbols = _resolve(cfg)
    last_resolve = time.time()
    # Period backend: serves any window on demand, cache-first via the bar-store.
    engine = PeriodEngine(lambda: sectors, cfg.benchmark, provider, bars,
                          mp_store=mp_store, mp_worker=mp_worker)
    webserver.start(cfg.ingress_port, engine=engine, smart_money_url=cfg.smart_money_url)
    engine.warm()   # pre-compute common windows (1d/3d/6h/3h/1h) in the background
    total = sum(len(s.symbols) for s in sectors)
    log.info("Up. source=%s sectors=%d constituents=%d symbols=%d poll=%ss benchmark=%s",
             provider.name, len(sectors), total, len(symbols), cfg.poll_interval_seconds, cfg.benchmark)
    # Quiet start: log only, no push (avoids the notification blast on restart).
    log.info("Market Flow Bot started — watching %d sectors (%d names).", len(sectors), total)

    first_cycle = cfg.alerts.suppress_startup
    events.refresh()
    last_events = time.time()
    last_enrich = time.time()

    while True:
        if time.time() - last_events > 3 * 3600:
            events.refresh()
            last_events = time.time()
        if time.time() - last_enrich > 600:
            events.enrich_actuals()
            last_enrich = time.time()
        session = active_session(cfg.sessions)
        if session is None:
            time.sleep(min(cfg.poll_interval_seconds, 300))
            continue
        # re-resolve every cycle: cheap (SSGA holdings are cached weekly), and it
        # picks up any watchlist tickers added from the dashboard since last time.
        # Reassigning `sectors` also updates what the period engine serves (it
        # holds a `lambda: sectors` over this binding).
        sectors, symbols = _resolve(cfg)
        try:
            now_ts = time.time()
            frames = provider.get_bars(symbols, interval=INTERVAL, lookback=LOOKBACK)
            for _sym, _df in frames.items():
                bars.put_bars("5m", _sym, _df)
            # keep the common period snapshots warm off the freshly-stored 5m bars
            # (background, de-duplicated; reads cache, so no extra Yahoo load)
            engine.warm()
            signals, flowboard, directions = compute(
                frames, sectors, cfg.benchmark, cfg.thresholds, cfg.alerts,
                prev_dir, session=session, history=history, first_cycle=first_cycle)
            # Stamp the snapshot with the NEWEST BAR's time, not the wall clock, so
            # "updated N min ago" reflects the real data age. Yahoo is ~15 min
            # delayed, so this is honestly ~15-20 min behind during a live session;
            # if a fetch fails or we're outside a session it grows and we flag it.
            newest = None
            for _df in frames.values():
                if _df is not None and not _df.empty:
                    t = _df.index[-1]
                    if newest is None or t > newest:
                        newest = t
            if newest is not None:
                try:
                    ts = newest.tz_convert("UTC") if newest.tzinfo else newest.tz_localize("UTC")
                    updated = ts.isoformat(timespec="seconds")
                    data_age_min = round(max(0.0, (now_ts - ts.timestamp())) / 60)
                except Exception:  # noqa: BLE001
                    updated = datetime.now(timezone.utc).isoformat(timespec="seconds"); data_age_min = None
            else:
                updated = datetime.now(timezone.utc).isoformat(timespec="seconds"); data_age_min = None
            stale = (data_age_min is not None and data_age_min > 45)
            notifier.publish_leaderboard(
                flowboard, fetched=len(frames), total_symbols=len(symbols), updated=updated)
            webserver.write_latest({
                "sectors": [
                    {"rank": st.rank, "sector": st.name, "flow": round(st.flow_ratio, 3),
                     "direction": "in" if st.flow_ratio > 0 else "out" if st.flow_ratio < 0 else "flat",
                     "net_dollar": round(st.net_dollar), "gross": round(st.gross),
                     "breadth": (round(st.breadth) if st.breadth is not None else None),
                     "rel_strength": round(st.rel_strength, 2), "rvol": round(st.rvol, 2)}
                    for st in flowboard],
                "fetched": len(frames), "total_symbols": len(symbols), "updated": updated,
                "data_age_min": data_age_min, "stale": stale,
                "leader": flowboard[0].name if flowboard else None,
            })
            if not stale:
                prev_dir = directions
                store.save({"dir": directions})
                history.insert(now_ts, session, [
                    {"sector": st.name, "ret": st.flow_ratio, "ret_recent": st.rvol,
                     "volume": st.net_dollar, "breadth": st.breadth, "rank": st.rank}
                    for st in flowboard])
            if now_ts - last_prune > 86400:
                history.prune(cfg.history_days)
                bars.prune()
                last_prune = now_ts

            if first_cycle:
                log.info("first cycle — baselines set, alerts start next cycle")
                first_cycle = False
            else:
                if not stale:
                    for sig in signals:
                        cd = summary_cd if sig.key == "flow_summary" else cooldown
                        if cd.ready(sig.key, now_ts):
                            log.info("[%s] %s: %s", session, sig.title, sig.message)
                            notifier.send(f"[{session}] {sig.title}", sig.message)

                # ---- price-move alerts (independent of the flow signals) ----
                # Skipped when the data is stale so a delayed/failed fetch can't
                # fire a phantom move. Responsiveness is bounded by the poll
                # interval + Yahoo's ~15-min delay, so "immediate" ≈ next cycle.
                ac = cfg.alerts
                if not stale and (ac.alert_on_stock_move or ac.alert_on_sector_move):
                    moves = pct_changes(frames, ac.move_reference)
                    ref_lbl = "on the session" if ac.move_reference == "session_open" else "vs prior close"
                    if ac.alert_on_stock_move:
                        movers = sorted((kv for kv in moves.items() if abs(kv[1]) >= ac.stock_move_pct),
                                        key=lambda kv: abs(kv[1]), reverse=True)[:12]  # cap the flood on a crash day
                        for sym, p in movers:
                            if move_cd.ready(f"move_{sym}", now_ts):
                                last = float(frames[sym]["close"].iloc[-1])
                                notifier.send(f"{sym} {p:+.1f}%", f"{sym} {p:+.1f}% {ref_lbl} — now ${last:.2f}")
                                log.info("[%s] move alert: %s %+.1f%%", session, sym, p)
                    if ac.alert_on_sector_move:
                        for sec in sectors:
                            vals = [moves[s] for s in sec.symbols if s in moves]
                            need = 1 if len(sec.symbols) == 1 else 3   # don't judge a 30-name sector off one stock
                            if len(vals) < need:
                                continue
                            sp = sum(vals) / len(vals)
                            if abs(sp) >= ac.sector_move_pct and move_cd.ready(f"smove_{sec.name}", now_ts):
                                notifier.send(f"{sec.name} sector {sp:+.1f}%",
                                              f"{sec.name} averaging {sp:+.1f}% {ref_lbl} across {len(vals)} stocks.")
                                log.info("[%s] sector move alert: %s %+.1f%%", session, sec.name, sp)
        except Exception as exc:  # noqa: BLE001
            log.exception("cycle error: %s", exc)
        time.sleep(cfg.poll_interval_seconds)


if __name__ == "__main__":
    run()
