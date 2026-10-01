"""Persistent, broker-free paper portfolios driven by Market Flow scores."""
from __future__ import annotations

import json
import math
import os
import sqlite3
import threading
from datetime import datetime, timezone


DEFAULT_RULES = {
    "enabled": True,
    "starting_cash": 100000.0,
    "buy_score": 80.0,
    "score_exit": 75.0,
    "position_size": 3000.0,
    "extended_hours_position_size": 500.0,
    "confirm_regular_scans": 2,
    "confirm_extended_scans": 3,
    "pending_cancel_score": 75.0,
    "confirmation_min_rvol": 1.5,
    "max_pending_price_rise_pct": 3.0,
    "max_positions": 20,
    "targets": [5.0, 12.5, 20.0],
    "target_fractions": [0.333333, 0.333333, 0.333334],
    "stop_loss_pct": 10.0,
    "trailing_stop_pct": 0.0,
    "max_holding_days": 0,
    "reentry_cooldown_hours": 24,
    "allow_overnight": True,
    "avoid_earnings_days": 0,
}


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _number(value, name, low=0.0, high=1e12):
    try:
        value = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be a number") from exc
    if not math.isfinite(value) or not low <= value <= high:
        raise ValueError(f"{name} is outside the supported range")
    return value


class PaperLedger:
    """Two paper accounts: rule-driven ``auto`` and user-operated ``manual``."""

    def __init__(self, path=None):
        base = "/data" if os.path.isdir("/data") else os.path.dirname(__file__)
        self.db = sqlite3.connect(path or os.path.join(base, "paper.db"), check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.lock = threading.RLock()
        with self.db:
            self.db.executescript("""
              PRAGMA journal_mode=WAL; PRAGMA busy_timeout=5000;
              CREATE TABLE IF NOT EXISTS paper_rules(account TEXT PRIMARY KEY, payload TEXT NOT NULL);
              CREATE TABLE IF NOT EXISTS paper_cash(account TEXT PRIMARY KEY, cash REAL NOT NULL);
              CREATE TABLE IF NOT EXISTS paper_positions(
                account TEXT, ticker TEXT, shares REAL NOT NULL, avg_price REAL NOT NULL,
                opened TEXT NOT NULL, high_price REAL NOT NULL, score REAL, target_mask INTEGER DEFAULT 0,
                PRIMARY KEY(account,ticker));
              CREATE TABLE IF NOT EXISTS paper_events(
                id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL, account TEXT NOT NULL,
                ticker TEXT, side TEXT NOT NULL, shares REAL, price REAL, score REAL, reason TEXT NOT NULL,
                realised REAL DEFAULT 0);
              CREATE TABLE IF NOT EXISTS paper_pending(
                ticker TEXT PRIMARY KEY, first_seen TEXT NOT NULL, last_seen TEXT NOT NULL,
                first_price REAL NOT NULL, last_price REAL NOT NULL, score REAL NOT NULL,
                rvol REAL NOT NULL, confirmations INTEGER NOT NULL, required INTEGER NOT NULL,
                session TEXT NOT NULL, allocation REAL NOT NULL);
              CREATE TABLE IF NOT EXISTS paper_meta(key TEXT PRIMARY KEY, payload TEXT NOT NULL);
            """)
            for account in ("auto", "manual"):
                self.db.execute("INSERT OR IGNORE INTO paper_rules VALUES(?,?)",
                                (account, json.dumps(DEFAULT_RULES)))
                self.db.execute("INSERT OR IGNORE INTO paper_cash VALUES(?,?)",
                                (account, DEFAULT_RULES["starting_cash"]))

    def rules(self, account="auto"):
        self._account(account)
        with self.lock:
            row = self.db.execute("SELECT payload FROM paper_rules WHERE account=?", (account,)).fetchone()
        return {**DEFAULT_RULES, **json.loads(row[0])}

    def save_rules(self, payload, account="auto"):
        self._account(account)
        old = self.rules(account)
        allowed = set(DEFAULT_RULES)
        rules = {**old, **{k: payload[k] for k in payload if k in allowed}}
        for key in ("starting_cash", "buy_score", "score_exit", "position_size",
                    "extended_hours_position_size", "stop_loss_pct",
                    "pending_cancel_score", "confirmation_min_rvol", "max_pending_price_rise_pct",
                    "trailing_stop_pct", "max_holding_days", "reentry_cooldown_hours", "avoid_earnings_days"):
            rules[key] = _number(rules[key], key)
        rules["max_positions"] = int(_number(rules["max_positions"], "max_positions", 1, 1000))
        rules["confirm_regular_scans"] = int(_number(rules["confirm_regular_scans"], "confirm_regular_scans", 1, 100))
        rules["confirm_extended_scans"] = int(_number(rules["confirm_extended_scans"], "confirm_extended_scans", 1, 100))
        rules["enabled"] = bool(rules["enabled"])
        rules["allow_overnight"] = bool(rules["allow_overnight"])
        targets = list(rules.get("targets") or [])
        fractions = list(rules.get("target_fractions") or [])
        if not targets or len(targets) != len(fractions) or len(targets) > 10:
            raise ValueError("Profit targets and fractions must have the same length")
        rules["targets"] = [_number(x, "profit target", 0.01, 10000) for x in targets]
        rules["target_fractions"] = [_number(x, "target fraction", 0.001, 1) for x in fractions]
        total = sum(rules["target_fractions"])
        if total > 1.001:
            raise ValueError("Target fractions cannot exceed 100%")
        with self.lock, self.db:
            self.db.execute("INSERT OR REPLACE INTO paper_rules VALUES(?,?)", (account, json.dumps(rules)))
        return rules

    @staticmethod
    def _account(account):
        if account not in ("auto", "manual"):
            raise ValueError("Invalid paper account")

    def _trade(self, account, ticker, side, shares, price, score, reason):
        self._account(account); ticker = str(ticker or "").strip().upper()
        shares = _number(shares, "shares", 0.000001); price = _number(price, "price", 0.000001)
        with self.lock, self.db:
            cash = float(self.db.execute("SELECT cash FROM paper_cash WHERE account=?", (account,)).fetchone()[0])
            pos = self.db.execute("SELECT * FROM paper_positions WHERE account=? AND ticker=?", (account, ticker)).fetchone()
            realised = 0.0
            if side == "buy":
                cost = shares * price
                if cost > cash + 0.01: raise ValueError("Not enough paper cash")
                old_shares = float(pos["shares"]) if pos else 0.0
                old_cost = old_shares * float(pos["avg_price"]) if pos else 0.0
                new_shares = old_shares + shares
                avg = (old_cost + cost) / new_shares
                opened = pos["opened"] if pos else _now()
                high = max(price, float(pos["high_price"])) if pos else price
                mask = int(pos["target_mask"]) if pos else 0
                self.db.execute("INSERT OR REPLACE INTO paper_positions VALUES(?,?,?,?,?,?,?,?)",
                    (account,ticker,new_shares,avg,opened,high,score,mask))
                cash -= cost
            elif side == "sell":
                if not pos or shares > float(pos["shares"]) + 1e-8: raise ValueError("Not enough paper shares")
                shares = min(shares, float(pos["shares"])); realised = (price-float(pos["avg_price"]))*shares
                remain = float(pos["shares"])-shares; cash += shares*price
                if remain < 1e-8: self.db.execute("DELETE FROM paper_positions WHERE account=? AND ticker=?",(account,ticker))
                else: self.db.execute("UPDATE paper_positions SET shares=?,high_price=?,score=? WHERE account=? AND ticker=?",
                                      (remain,max(price,float(pos["high_price"])),score,account,ticker))
            else: raise ValueError("Side must be buy or sell")
            self.db.execute("UPDATE paper_cash SET cash=? WHERE account=?",(cash,account))
            self.db.execute("INSERT INTO paper_events(ts,account,ticker,side,shares,price,score,reason,realised) VALUES(?,?,?,?,?,?,?,?,?)",
                            (_now(),account,ticker,side,shares,price,score,reason[:300],realised))
        return {"ok": True, "realised": round(realised, 2)}

    def manual_trade(self, payload):
        side = str(payload.get("side") or "").strip().lower()
        price = _number(payload.get("price"), "price", 0.000001)
        shares = payload.get("shares")
        amount = payload.get("amount")
        # Buying by cash value is friendlier than making the user calculate
        # fractional shares. Explicit shares remain supported for both sides.
        if side == "buy" and amount not in (None, ""):
            amount = _number(amount, "spend", 0.01)
            shares = amount / price
        return self._trade("manual", payload.get("ticker"), side, shares,
                           price, payload.get("score"), "Manual paper order")

    def process(self, rows, session=None):
        """Apply automatic rules to the latest complete score snapshot."""
        rules = self.rules("auto")
        if not rules["enabled"]: return
        by_ticker = {r.get("ticker"): r for r in rows if r.get("ticker") and float(r.get("price") or 0)>0}
        spy = by_ticker.get("SPY")
        if spy:
            with self.lock, self.db:
                previous = self.db.execute("SELECT payload FROM paper_meta WHERE key='spy_benchmark'").fetchone()
                data = json.loads(previous[0]) if previous else {"start": float(spy["price"]), "started": _now()}
                data.update(current=float(spy["price"]), updated=_now())
                self.db.execute("INSERT OR REPLACE INTO paper_meta VALUES('spy_benchmark',?)", (json.dumps(data),))
        with self.lock:
            positions = [dict(x) for x in self.db.execute("SELECT * FROM paper_positions WHERE account='auto'")]
            cash = float(self.db.execute("SELECT cash FROM paper_cash WHERE account='auto'").fetchone()[0])
        for pos in positions:
            row = by_ticker.get(pos["ticker"])
            if not row: continue
            price, score = float(row["price"]), float(row.get("score") or 0)
            gain = (price/pos["avg_price"]-1)*100
            high = max(float(pos["high_price"]), price)
            with self.lock, self.db:
                self.db.execute("UPDATE paper_positions SET high_price=?,score=? WHERE account='auto' AND ticker=?",(high,score,pos["ticker"]))
            age_days = (datetime.now(timezone.utc)-datetime.fromisoformat(pos["opened"])).total_seconds()/86400
            if rules["max_holding_days"] and age_days >= rules["max_holding_days"]:
                self._trade("auto",pos["ticker"],"sell",pos["shares"],price,score,"Maximum holding period"); continue
            if not rules["allow_overnight"] and session == "postmarket":
                self._trade("auto",pos["ticker"],"sell",pos["shares"],price,score,"Overnight positions disabled"); continue
            if gain > 0 and score <= rules["score_exit"]:
                self._trade("auto",pos["ticker"],"sell",pos["shares"],price,score,"Score fell to exit threshold while profitable"); continue
            if rules["stop_loss_pct"] and gain <= -rules["stop_loss_pct"]:
                self._trade("auto",pos["ticker"],"sell",pos["shares"],price,score,"Stop loss"); continue
            if rules["trailing_stop_pct"] and price <= high*(1-rules["trailing_stop_pct"]/100) and price>pos["avg_price"]:
                self._trade("auto",pos["ticker"],"sell",pos["shares"],price,score,"Trailing stop"); continue
            mask = int(pos["target_mask"])
            original = pos["shares"] / max(0.000001, 1-sum(f for i,f in enumerate(rules["target_fractions"]) if mask&(1<<i)))
            for i,(target,fraction) in enumerate(zip(rules["targets"],rules["target_fractions"])):
                if not mask&(1<<i) and gain >= target:
                    qty=min(pos["shares"],original*fraction)
                    if qty>1e-8:self._trade("auto",pos["ticker"],"sell",qty,price,score,f"Profit target +{target:g}%")
                    mask|=1<<i
                    with self.lock,self.db:self.db.execute("UPDATE paper_positions SET target_mask=? WHERE account='auto' AND ticker=?",(mask,pos["ticker"]))
                    break
        with self.lock:
            count=self.db.execute("SELECT count(*) FROM paper_positions WHERE account='auto'").fetchone()[0]
            cash=float(self.db.execute("SELECT cash FROM paper_cash WHERE account='auto'").fetchone()[0])
        # When overnight positions are allowed, a qualifying pre/post-market
        # signal should be paper-bought at the same cached price that produced
        # the alert. Previously every non-regular-session candidate was silently
        # skipped, even though the UI rule explicitly allowed overnight holding.
        if session not in (None, "regular") and not rules["allow_overnight"]:
            return
        current_session = session or "regular"
        allocation = (rules["position_size"] if current_session == "regular"
                      else rules["extended_hours_position_size"])
        required = (rules["confirm_regular_scans"] if current_session == "regular"
                    else rules["confirm_extended_scans"])
        for row in rows:
            earnings_days = row.get("earnings_days")
            if (rules["avoid_earnings_days"] and isinstance(earnings_days,(int,float))
                    and 0 <= earnings_days <= rules["avoid_earnings_days"]):
                continue
            ticker = str(row.get("ticker") or "").upper()
            score = float(row.get("score") or 0)
            price = float(row.get("price") or 0)
            rvol = float(row.get("rvol") or 0)
            if not ticker or price <= 0:
                continue
            with self.lock:
                exists=self.db.execute("SELECT 1 FROM paper_positions WHERE account='auto' AND ticker=?",(ticker,)).fetchone()
                last=self.db.execute("SELECT ts FROM paper_events WHERE account='auto' AND ticker=? AND side='sell' ORDER BY id DESC LIMIT 1",(ticker,)).fetchone()
                pending=self.db.execute("SELECT * FROM paper_pending WHERE ticker=?",(ticker,)).fetchone()
            if exists:
                with self.lock, self.db:
                    self.db.execute("DELETE FROM paper_pending WHERE ticker=?", (ticker,))
                continue
            if last:
                age=(datetime.now(timezone.utc)-datetime.fromisoformat(last[0])).total_seconds()/3600
                if age<rules["reentry_cooldown_hours"]:
                    continue
            if pending:
                rise = (price / float(pending["first_price"]) - 1) * 100
                if score < rules["pending_cancel_score"] or rise > rules["max_pending_price_rise_pct"]:
                    with self.lock, self.db:
                        self.db.execute("DELETE FROM paper_pending WHERE ticker=?", (ticker,))
                    continue
                confirmations = int(pending["confirmations"])
                if score >= rules["buy_score"] and rvol >= rules["confirmation_min_rvol"]:
                    confirmations += 1
                else:
                    confirmations = 0
                with self.lock, self.db:
                    self.db.execute("UPDATE paper_pending SET last_seen=?,last_price=?,score=?,rvol=?,confirmations=? WHERE ticker=?",
                                    (_now(),price,score,rvol,confirmations,ticker))
                pending_required = int(pending["required"])
                pending_allocation = float(pending["allocation"])
                if confirmations < pending_required:
                    continue
                if count >= rules["max_positions"] or cash < pending_allocation:
                    continue
                amount = min(cash, pending_allocation)
                hours = "regular hours" if pending["session"] == "regular" else "extended hours"
                self._trade("auto",ticker,"buy",amount/price,price,score,
                            f"Confirmed for {confirmations} scans ({hours})")
                with self.lock, self.db:
                    self.db.execute("DELETE FROM paper_pending WHERE ticker=?", (ticker,))
                cash-=amount;count+=1
                continue
            if score < rules["buy_score"] or rvol < rules["confirmation_min_rvol"]:
                continue
            with self.lock, self.db:
                self.db.execute("INSERT OR REPLACE INTO paper_pending VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                                (ticker,_now(),_now(),price,price,score,rvol,1,required,current_session,allocation))

    def snapshot(self, account, prices=None):
        self._account(account); prices=prices or {}
        with self.lock:
            positions=[dict(x) for x in self.db.execute("SELECT * FROM paper_positions WHERE account=? ORDER BY ticker",(account,))]
            cash=float(self.db.execute("SELECT cash FROM paper_cash WHERE account=?",(account,)).fetchone()[0])
            events=[dict(x) for x in self.db.execute("SELECT * FROM paper_events WHERE account=? ORDER BY id DESC LIMIT 300",(account,))]
            benchmark_row=self.db.execute("SELECT payload FROM paper_meta WHERE key='spy_benchmark'").fetchone()
            pending=([dict(x) for x in self.db.execute("SELECT * FROM paper_pending ORDER BY score DESC")]
                     if account == "auto" else [])
        unrealised=0.0; market_value=0.0
        for pos in positions:
            quote=prices.get(pos["ticker"],{}); current=float(quote.get("price") or pos["avg_price"])
            value=current*pos["shares"]; pnl=(current-pos["avg_price"])*pos["shares"]
            pos.update(current_price=current,market_value=round(value,2),unrealised=round(pnl,2),
                       return_pct=round((current/pos["avg_price"]-1)*100,2))
            market_value+=value;unrealised+=pnl
        realised=sum(float(e["realised"] or 0) for e in events)
        exits=[e for e in events if e["side"]=="sell"]
        curve=0.0;peak=0.0;max_drawdown=0.0
        for event in reversed(events):
            curve+=float(event["realised"] or 0);peak=max(peak,curve);max_drawdown=min(max_drawdown,curve-peak)
        benchmark=json.loads(benchmark_row[0]) if benchmark_row else {}
        benchmark_return=((float(benchmark.get("current"))/float(benchmark.get("start"))-1)*100
                          if benchmark.get("start") and benchmark.get("current") else None)
        return {"account":account,"rules":self.rules(account),"cash":round(cash,2),"positions":positions,"pending":pending,
                "events":events,"market_value":round(market_value,2),"equity":round(cash+market_value,2),
                "unrealised":round(unrealised,2),"realised":round(realised,2),
                "win_rate":round(100*sum(float(e["realised"] or 0)>0 for e in exits)/len(exits),1) if exits else None,
                "max_realised_drawdown":round(max_drawdown,2),
                "spy_return_pct":round(benchmark_return,2) if benchmark_return is not None else None,
                "spy_started":benchmark.get("started")}
