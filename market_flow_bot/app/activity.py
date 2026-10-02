"""Unusual stock and options activity scoring.

The stock calculation is deliberately provider-neutral and uses the 5-minute
bars already downloaded by Market Flow.  Volume is compared with previous
sessions at the *same point in the trading day*; comparing a partial day with a
full-day average would create false alerts every morning.  When history permits,
the relative-volume score blends robust 5-, 20- and 40-session medians so one
abnormal day cannot dominate the signal.

Yahoo option chains are sampled only for the strongest stock candidates by a
slow background worker.  They are snapshots, not an OPRA trade feed, so the
result is labelled options confirmation rather than options-flow certainty.
"""
from __future__ import annotations

import json
import logging
import math
import os
import queue
import threading
import time
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import numpy as np

from .signals import _stock_flow

log = logging.getLogger("activity")
ET = ZoneInfo("America/New_York")


def _finite(value, default=0.0):
    try:
        value = float(value)
        return value if math.isfinite(value) else default
    except (TypeError, ValueError):
        return default


def _clamp(value, low=0.0, high=100.0):
    return max(low, min(high, value))


def _horizon_ratio(current: float, values: list[float], sessions: int) -> float | None:
    """Current volume / same-time median for the requested trailing horizon."""
    sample = values[-sessions:]
    if not sample:
        return None
    baseline = float(np.nanmedian(sample))
    return current / baseline if baseline > 0 else None


def _blend_ratios(ratios: list[tuple[float | None, float]]) -> float:
    """Weighted geometric mean, redistributing weight across available history."""
    available = [(ratio, weight) for ratio, weight in ratios if ratio is not None and ratio > 0]
    if not available:
        return 0.0
    total_weight = sum(weight for _, weight in available)
    return math.exp(sum((weight / total_weight) * math.log(ratio)
                        for ratio, weight in available))


def stock_activity(symbol, df, now=None) -> dict | None:
    """Score one stock from intraday bars; return None for insufficient data."""
    if df is None or len(df) < 12 or "volume" not in df or "close" not in df:
        return None
    work = df.copy().sort_index()
    work = work[~work.index.duplicated(keep="last")]
    now = now or datetime.now(timezone.utc)
    try:
        idx = work.index.tz_convert(ET) if work.index.tz is not None else work.index.tz_localize("UTC").tz_convert(ET)
    except Exception:  # noqa: BLE001
        return None
    # Bar timestamps are starts. Never score the currently forming 5m bar.
    complete = np.array([(now-v.astimezone(timezone.utc)).total_seconds() >= 300 for v in idx])
    work = work.iloc[np.flatnonzero(complete)]
    idx = idx[complete]
    if len(work)<6: return None
    minutes = np.array([v.hour*60+v.minute for v in idx])
    dates = np.array([v.date() for v in idx])
    latest_minute=int(minutes[-1])
    if 240<=latest_minute<570: start,end,session=240,570,"premarket"
    elif 570<=latest_minute<960: start,end,session=570,960,"regular"
    elif 960<=latest_minute<1200: start,end,session=960,1200,"postmarket"
    else: return None
    selected=(minutes>=start)&(minutes<end)
    regular=(minutes>=570)&(minutes<960)
    today=dates[-1]
    current_idx=np.flatnonzero(selected&(dates==today))
    if len(current_idx)<3:return None
    current_minute=int(minutes[current_idx[-1]])
    volume=work["volume"].to_numpy(dtype=float)
    close=work["close"].to_numpy(dtype=float)
    expected=np.arange(start,current_minute+1,5)
    if not np.array_equal(minutes[current_idx],expected):return None
    if not np.all(np.isfinite(volume[current_idx])&(volume[current_idx]>=0)):return None
    if not np.all(np.isfinite(close[current_idx])&(close[current_idx]>0)):return None
    current_volume=float(np.sum(volume[current_idx]))
    baselines,burst_baselines=[],[]
    for day in sorted(set(dates[selected&(dates<today)]))[-40:]:
        comparable=np.flatnonzero(selected&(dates==day)&(minutes<=current_minute))
        if not np.array_equal(minutes[comparable],expected):continue
        values=volume[comparable]
        if not np.all(np.isfinite(values)&(values>=0)) or np.sum(values)<=0:continue
        baselines.append(float(np.sum(values)))
        burst_baselines.append(float(np.sum(values[-3:])))
    if not baselines:return None
    recent=float(np.sum(volume[current_idx[-3:]]))
    previous=float(np.sum(volume[current_idx[-6:-3]])) if len(current_idx)>=6 else 0.0
    rvol_5d=_horizon_ratio(current_volume,baselines,5)
    rvol_20d=_horizon_ratio(current_volume,baselines,20) if len(baselines)>=20 else None
    rvol_40d=_horizon_ratio(current_volume,baselines,40) if len(baselines)>=40 else None
    rvol=_blend_ratios([(rvol_5d,.25),(rvol_20d,.50),(rvol_40d,.25)])
    burst=recent/float(np.median(burst_baselines[-20:])) if np.median(burst_baselines[-20:])>0 else 0.0
    acceleration=recent/previous if previous>0 else 0.0

    previous_days = np.flatnonzero(regular & (dates < today))
    if not len(previous_days):
        return None
    previous_day = dates[previous_days[-1]]
    prior_close_idx = np.flatnonzero(regular & (dates == previous_day))[-1]
    last = _finite(close[current_idx[-1]])
    prior_close = _finite(close[prior_close_idx])
    if last <= 0 or prior_close <= 0:
        return None
    change = (last / prior_close - 1.0) * 100.0
    net, gross = _stock_flow(work.iloc[current_idx], None)
    flow = net / gross if gross > 0 else 0.0
    dollar_volume = float(np.nansum(close[current_idx] * volume[current_idx]))

    # Score unusual participation first; direction determines opportunity label.
    unusual = (
        min(30.0, max(0.0, rvol - 1.0) * 30.0) +
        min(25.0, max(0.0, burst - 1.0) * 20.0) +
        min(15.0, max(0.0, acceleration - 1.0) * 10.0) +
        min(15.0, abs(change) * 2.5) +
        min(15.0, abs(flow) * 25.0)
    )
    aligned = 1 if change > 0.15 and flow > 0.03 else -1 if change < -0.15 and flow < -0.03 else 0
    return {
        "session": session, "ticker": symbol, "price": round(last, 4), "change_pct": round(change, 2),
        "rvol": round(rvol, 2),
        "rvol_5d": round(rvol_5d, 2) if rvol_5d is not None else None,
        "rvol_20d": round(rvol_20d, 2) if rvol_20d is not None else None,
        "rvol_40d": round(rvol_40d, 2) if rvol_40d is not None else None,
        "volume_baseline_sessions": len(baselines),
        "burst_ratio": round(burst, 2),
        "acceleration": round(acceleration, 2), "flow": round(float(flow), 3),
        "volume": round(current_volume), "dollar_volume": round(dollar_volume),
        "stock_score": round(_clamp(unusual), 1), "direction": aligned,
        "asof": idx[current_idx[-1]].astimezone(timezone.utc).isoformat(timespec="seconds"),
    }


def combine_score(row: dict, option: dict | None = None) -> dict:
    """Attach options confirmation and a conservative opportunity label."""
    out = dict(row)
    option = option or {}
    try:
        age=time.time()-datetime.fromisoformat(str(option.get("asof","")).replace("Z","+00:00")).timestamp()
        if not 0<=age<=3600 or _finite(option.get("total_volume"))<=0: option={}
    except (ValueError,TypeError,OverflowError): option={}
    call_put = _finite(option.get("call_put_volume_ratio"))
    option_score = _finite(option.get("score"))
    direction = int(out.get("direction") or 0)
    confirm = 0.0
    if direction > 0 and call_put >= 1.3:
        confirm = min(15.0, option_score * 0.15)
    elif direction < 0 and option and 0 <= call_put <= 0.77:
        confirm = min(15.0, option_score * 0.15)
    stock_score = _finite(out.get("stock_score"))
    combined = _clamp(stock_score + confirm)
    # A strong opposite options skew is a caution, never enough by itself to
    # reverse the stock signal (multi-leg hedges are common).
    if option_score and ((direction > 0 and option and 0 <= call_put < 0.67) or
                         (direction < 0 and call_put > 1.5)):
        combined = _clamp(combined - min(10.0, option_score * 0.10))
    if direction > 0:
        label = "Potential Buy" if combined >= 80 else "Bullish Watch" if combined >= 65 else "Watch"
    elif direction < 0:
        label = "Reduce / Risk" if combined >= 80 else "Bearish Watch" if combined >= 65 else "Watch"
    else:
        label = "Unusual / Mixed" if combined >= 65 else "Watch"
    out.update(score=round(combined, 1), signal=label,
               options=option or None, options_confirmed=bool(confirm))
    return out


def scan(frames: dict, option_snapshots: dict | None = None) -> list[dict]:
    options = option_snapshots or {}
    rows = []
    for symbol, frame in frames.items():
        # Futures and indices do not have comparable equity option/volume rules.
        if "=" in symbol or symbol.startswith("^"):
            continue
        row = stock_activity(symbol, frame)
        if row:
            rows.append(combine_score(row, options.get(symbol)))
    return sorted(rows, key=lambda value: value["score"], reverse=True)


class OptionsActivityWorker:
    """Paced option-chain sampler with a small persistent snapshot cache."""
    def __init__(self, refresh_seconds=1800, pacing_seconds=2.0):
        base = "/data" if os.path.isdir("/data") else os.path.dirname(__file__)
        self.path = os.path.join(base, "options_activity.json")
        self.refresh_seconds = max(900, int(refresh_seconds))
        self.pacing_seconds = max(1.0, float(pacing_seconds))
        self.q: "queue.Queue[str]" = queue.Queue()
        self._queued, self._lock = set(), threading.RLock()
        self.data = self._load()
        threading.Thread(target=self._run, daemon=True).start()

    def _load(self):
        try:
            with open(self.path, "r", encoding="utf-8") as fh:
                return json.load(fh)
        except Exception:
            return {}

    def _save(self):
        try:
            tmp = self.path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(self.data, fh)
            os.replace(tmp, self.path)
        except Exception as exc:  # noqa: BLE001
            log.warning("could not save options activity: %s", exc)

    def snapshots(self):
        with self._lock:
            return {key: dict(value) for key, value in self.data.items()}

    def request(self, symbols, limit=8):
        now = time.time()
        for symbol in list(symbols)[:max(0, int(limit))]:
            with self._lock:
                last = _finite((self.data.get(symbol) or {}).get("asof_ts"))
                if now - last < self.refresh_seconds or symbol in self._queued:
                    continue
                self._queued.add(symbol)
            self.q.put(symbol)

    def _run(self):
        while True:
            symbol = self.q.get()
            try:
                result = self._fetch(symbol)
                if result:
                    with self._lock:
                        previous = self.data.get(symbol) or {}
                        prior_total = _finite(previous.get("total_volume"))
                        same_day = (str(previous.get("asof") or "")[:10] == str(result.get("asof") or "")[:10]
                                    and previous.get("expiries") == result.get("expiries"))
                        change = max(0.0, result["total_volume"] - prior_total) if prior_total and same_day else None
                        result["volume_change"] = change
                        result["rising_volume_pct"] = (change / prior_total) if change is not None and prior_total else None
                        if result["rising_volume_pct"] is not None:
                            result["score"] = round(_clamp(result["score"] + min(15.0, result["rising_volume_pct"] * 30.0)), 1)
                        self.data[symbol] = result
                        self._save()
            except Exception as exc:  # noqa: BLE001
                log.info("options activity %s unavailable: %s", symbol, exc)
            finally:
                with self._lock:
                    self._queued.discard(symbol)
            time.sleep(self.pacing_seconds)

    @staticmethod
    def _fetch(symbol):
        import yfinance as yf
        ticker = yf.Ticker(symbol)
        expiries = list(ticker.options or [])[:3]
        if not expiries:
            return None
        calls_volume = puts_volume = calls_oi = puts_oi = call_premium = put_premium = 0.0
        used = []
        for expiry in expiries:
            chain = ticker.option_chain(expiry)
            used.append(expiry)
            for frame, is_call in ((chain.calls, True), (chain.puts, False)):
                volume = float(np.nansum(frame.get("volume", 0)))
                oi = float(np.nansum(frame.get("openInterest", 0)))
                last = np.asarray(frame.get("lastPrice", 0), dtype=float)
                vols = np.asarray(frame.get("volume", 0), dtype=float)
                premium = float(np.nansum(np.nan_to_num(last) * np.nan_to_num(vols) * 100.0))
                if is_call:
                    calls_volume += volume; calls_oi += oi; call_premium += premium
                else:
                    puts_volume += volume; puts_oi += oi; put_premium += premium
        total = calls_volume + puts_volume
        if total <= 0 or calls_oi+puts_oi <= 0:
            return None
        ratio = calls_volume / max(1.0, puts_volume)
        volume_oi = total / max(1.0, calls_oi + puts_oi)
        imbalance = max(ratio, 1.0 / max(0.01, ratio))
        score = _clamp(min(55.0, max(0.0, imbalance - 1.0) * 22.0) + min(45.0, volume_oi * 90.0))
        return {"asof_ts": time.time(), "asof": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "expiries": used, "call_volume": round(calls_volume), "put_volume": round(puts_volume),
                "total_volume": round(total), "call_put_volume_ratio": round(ratio, 2),
                "volume_open_interest_ratio": round(volume_oi, 3),
                "call_premium": round(call_premium), "put_premium": round(put_premium),
                "score": round(score, 1), "source": "Yahoo delayed snapshot"}
