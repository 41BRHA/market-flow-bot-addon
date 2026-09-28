"""Market Flow Bot — main loop (volume-flow engine)."""
from __future__ import annotations

import logging
from datetime import datetime, timezone
import time

from . import constituents, webserver, events
from .config import Config, Sector
from .history import History
from .barstore import BarStore
from .period import PeriodEngine
from .maxpain import MaxPainStore, MaxPainWorker
from .notify import Notifier
from .providers import make_provider
from .signals import compute
from .state import Cooldown, Store, active_session

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("main")

INTERVAL = "5m"
LOOKBACK = "5d"
RESOLVE_EVERY = 6 * 3600


def _resolve(cfg) -> tuple[list[Sector], list[str]]:
    mapping = constituents.resolve(cfg.sectors, cfg.auto_constituents, cfg.top_n)
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
    webserver.start(cfg.ingress_port, engine=engine)
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
        if time.time() - last_resolve > RESOLVE_EVERY:
            sectors, symbols = _resolve(cfg)
            last_resolve = time.time()
        try:
            now_ts = time.time()
            frames = provider.get_bars(symbols, interval=INTERVAL, lookback=LOOKBACK)
            for _sym, _df in frames.items():
                bars.put_bars("5m", _sym, _df)
            signals, flowboard, directions = compute(
                frames, sectors, cfg.benchmark, cfg.thresholds, cfg.alerts,
                prev_dir, session=session, history=history, first_cycle=first_cycle)
            updated = datetime.now(timezone.utc).isoformat(timespec="seconds")
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
                "leader": flowboard[0].name if flowboard else None,
            })
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
                for sig in signals:
                    cd = summary_cd if sig.key == "flow_summary" else cooldown
                    if cd.ready(sig.key, now_ts):
                        log.info("[%s] %s: %s", session, sig.title, sig.message)
                        notifier.send(f"[{session}] {sig.title}", sig.message)
        except Exception as exc:  # noqa: BLE001
            log.exception("cycle error: %s", exc)
        time.sleep(cfg.poll_interval_seconds)


if __name__ == "__main__":
    run()
