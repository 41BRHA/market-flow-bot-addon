"""Persistent, broker-free paper portfolios driven by Market Flow scores."""
from __future__ import annotations

import json
import math
import os
import re
from zoneinfo import ZoneInfo
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


def _stamp(value):
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return dt.astimezone(timezone.utc) if dt.tzinfo else None
    except (TypeError, ValueError):
        return None


def fresh_quote(row, now=None):
    now = now or datetime.now(timezone.utc)
    stamp = _stamp(row.get("asof"))
    try:
        price = float(row.get("price"))
        return (stamp is not None and math.isfinite(price) and price > 0
                and 0 <= (now-stamp).total_seconds() <= 1800)
    except (TypeError, ValueError):
        return False


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

            # Additive migrations: balances, trades and positions are never reset.
            columns = {r[1] for r in self.db.execute("PRAGMA table_info(paper_positions)")}
            if "original_shares" not in columns:
                self.db.execute("ALTER TABLE paper_positions ADD COLUMN original_shares REAL")
            if "exit_plan" not in columns:
                self.db.execute("ALTER TABLE paper_positions ADD COLUMN exit_plan TEXT")
            self.db.execute("CREATE TABLE IF NOT EXISTS paper_observations(ticker TEXT PRIMARY KEY, asof TEXT NOT NULL)")
            if not self.db.execute("SELECT 1 FROM paper_meta WHERE key='math_v2'").fetchone():
                # Old confirmations did not require distinct market timestamps.
                self.db.execute("DELETE FROM paper_pending")
                for pos in self.db.execute("SELECT * FROM paper_positions").fetchall():
                    events = self.db.execute("SELECT side,shares FROM paper_events WHERE account=? AND ticker=? ORDER BY id",
                                             (pos["account"],pos["ticker"])).fetchall()
                    balance = original = 0.0
                    for event in events:
                        if event["side"] == "buy":
                            if balance < 1e-8: original = 0.0
                            balance += event["shares"]; original += event["shares"]
                        else:
                            balance = max(0.0, balance-event["shares"])
                    original = max(float(pos["shares"]), original)
                    rr = self.rules(pos["account"])
                    plan = {"targets":rr["targets"],"fractions":rr["target_fractions"]}
                    self.db.execute("UPDATE paper_positions SET original_shares=?,exit_plan=? WHERE account=? AND ticker=?",
                                    (original,json.dumps(plan),pos["account"],pos["ticker"]))
                self.db.execute("INSERT INTO paper_meta VALUES('math_v2','true')")

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
        for key in ("buy_score", "score_exit", "pending_cancel_score"):
            rules[key] = _number(rules[key], key, 0, 100)
        for key in ("stop_loss_pct", "trailing_stop_pct"):
            rules[key] = _number(rules[key], key, 0, 100)
        if rules["starting_cash"] != old["starting_cash"]:
            raise ValueError("Starting cash cannot be changed on an existing account")
        rules["enabled"] = bool(rules["enabled"])
        rules["allow_overnight"] = bool(rules["allow_overnight"])
        targets = list(rules.get("targets") or [])
        fractions = list(rules.get("target_fractions") or [])
        if not targets or len(targets) != len(fractions) or len(targets) > 10:
            raise ValueError("Profit targets and fractions must have the same length")
        rules["targets"] = [_number(x, "profit target", 0.01, 10000) for x in targets]
        rules["target_fractions"] = [_number(x, "target fraction", 0.001, 1) for x in fractions]
        total = sum(rules["target_fractions"])
        if any(b <= a for a,b in zip(rules["targets"],rules["targets"][1:])):
            raise ValueError("Profit targets must be strictly increasing")
        if abs(total-1) <= 0.001:
            rules["target_fractions"] = [f/total for f in rules["target_fractions"]]
        if total > 1.001:
            raise ValueError("Target fractions cannot exceed 100%")
        with self.lock, self.db:
            self.db.execute("INSERT OR REPLACE INTO paper_rules VALUES(?,?)", (account, json.dumps(rules)))
            if account == "auto":
                self.db.execute("DELETE FROM paper_pending")
        return rules

    @staticmethod
    def _account(account):
        if account not in ("auto", "manual"):
            raise ValueError("Invalid paper account")

    def _trade(self, account, ticker, side, shares, price, score, reason, target_mask=None):
        self._account(account); ticker = str(ticker or "").strip().upper()
        if not re.fullmatch(r"[A-Z][A-Z0-9.-]{0,14}", ticker):
            raise ValueError("Invalid ticker")
        shares = _number(shares, "shares", 1e-10); price = _number(price, "price", 0.000001)
        with self.lock, self.db:
            cash = float(self.db.execute("SELECT cash FROM paper_cash WHERE account=?", (account,)).fetchone()[0])
            pos = self.db.execute("SELECT * FROM paper_positions WHERE account=? AND ticker=?", (account, ticker)).fetchone()
            realised = 0.0
            if side == "buy":
                cost = shares * price
                if cost > cash + 1e-8: raise ValueError("Not enough paper cash")
                old_shares = float(pos["shares"]) if pos else 0.0
                old_cost = old_shares * float(pos["avg_price"]) if pos else 0.0
                new_shares = old_shares + shares
                avg = (old_cost + cost) / new_shares
                opened = pos["opened"] if pos else _now()
                high = max(price, float(pos["high_price"])) if pos else price
                mask = int(pos["target_mask"]) if pos else 0
                rules = self.rules(account)
                plan = pos["exit_plan"] if pos else json.dumps({"targets":rules["targets"],"fractions":rules["target_fractions"]})
                original = float(pos["original_shares"] or old_shares)+shares if pos else shares
                self.db.execute("INSERT OR REPLACE INTO paper_positions VALUES(?,?,?,?,?,?,?,?,?,?)",
                    (account,ticker,new_shares,avg,opened,high,score,mask,original,plan))
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
            if target_mask is not None:
                self.db.execute("UPDATE paper_positions SET target_mask=? WHERE account=? AND ticker=?",
                                (target_mask,account,ticker))
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

    def process(self, rows, session=None, now=None):
        """Only distinct fresh, completed observations can trigger simulated fills."""
        with self.lock:
            return self._process(rows, session, now or datetime.now(timezone.utc))

    def _process(self, rows, session, now):
        rules = self.rules("auto")
        if not rules["enabled"] or session not in ("premarket","regular","postmarket"):
            return
        by_ticker = {}
        for row in rows:
            if not fresh_quote(row, now): continue
            stamp = _stamp(row["asof"])
            local = stamp.astimezone(ZoneInfo("America/New_York"))
            current = now.astimezone(ZoneInfo("America/New_York"))
            minute = local.hour*60+local.minute
            source_session = ("premarket" if 240 <= minute < 570 else
                              "regular" if 570 <= minute < 960 else
                              "postmarket" if 960 <= minute < 1200 else None)
            if local.date() != current.date() or source_session != session or local.weekday() >= 5:
                continue
            try:
                if not math.isfinite(float(row["price"])):continue
                if row.get("score") is not None and not math.isfinite(float(row["score"])):continue
                if not math.isfinite(float(row.get("rvol") or 0)):continue
            except (KeyError,TypeError,ValueError): continue
            by_ticker[str(row["ticker"]).upper()] = row

        spy = by_ticker.get("SPY")
        if spy:
            with self.db:
                previous = self.db.execute("SELECT payload FROM paper_meta WHERE key='spy_benchmark'").fetchone()
                data = json.loads(previous[0]) if previous else {"start":spy["price"],"started":_now()}
                data.update(current=spy["price"],updated=spy["asof"])
                self.db.execute("INSERT OR REPLACE INTO paper_meta VALUES('spy_benchmark',?)",(json.dumps(data),))
        # Missing/stale signals break confirmation; never let yesterday's queue buy.
        with self.db:
            for pending in self.db.execute("SELECT * FROM paper_pending").fetchall():
                stamp = _stamp(pending["last_seen"])
                if (pending["ticker"] not in by_ticker or pending["session"] != session
                        or not stamp or (now-stamp).total_seconds() > 1800):
                    self.db.execute("DELETE FROM paper_pending WHERE ticker=?",(pending["ticker"],))

        for ticker,row in by_ticker.items():
            stamp = _stamp(row["asof"])
            seen = self.db.execute("SELECT asof FROM paper_observations WHERE ticker=?",(ticker,)).fetchone()
            if seen and _stamp(seen[0]) and stamp <= _stamp(seen[0]): continue
            # Record consumption before acting: replay cannot duplicate a fill.
            with self.db:
                self.db.execute("INSERT OR REPLACE INTO paper_observations VALUES(?,?)",(ticker,row["asof"]))
            price=float(row["price"]);score=float(row["score"]) if row.get("score") is not None else None
            rvol=float(row.get("rvol") or 0)
            pos = self.db.execute("SELECT * FROM paper_positions WHERE account='auto' AND ticker=?",(ticker,)).fetchone()
            if pos:
                with self.db:
                    self.db.execute("DELETE FROM paper_pending WHERE ticker=?",(ticker,))
                gain = (price/pos["avg_price"]-1)*100
                high = max(float(pos["high_price"]),price)
                with self.db:
                    self.db.execute("UPDATE paper_positions SET high_price=?,score=? WHERE account='auto' AND ticker=?",(high,score,ticker))
                age = (now-_stamp(pos["opened"])).total_seconds()/86400
                reason = None
                if rules["max_holding_days"] and age >= rules["max_holding_days"]: reason="Maximum holding period"
                elif not rules["allow_overnight"] and session=="postmarket": reason="Overnight positions disabled"
                elif gain>0 and score is not None and (score<=rules["score_exit"] or row.get("direction") != 1): reason="Bullish signal weakened while profitable"
                elif rules["stop_loss_pct"] and gain<=-rules["stop_loss_pct"]: reason="Stop loss"
                elif rules["trailing_stop_pct"] and price<=high*(1-rules["trailing_stop_pct"]/100): reason="Trailing stop"
                if reason:
                    self._trade("auto",ticker,"sell",pos["shares"],price,score,reason)
                    continue
                plan=json.loads(pos["exit_plan"]) if pos["exit_plan"] else {"targets":rules["targets"],"fractions":rules["target_fractions"]}
                fractions=plan["fractions"]; total=sum(fractions)
                if abs(total-1)<=.001: fractions=[f/total for f in fractions]
                remaining=float(pos["shares"]); mask=int(pos["target_mask"])
                original=float(pos["original_shares"] or remaining)
                for i,(target,fraction) in enumerate(zip(plan["targets"],fractions)):
                    if mask&(1<<i) or gain+1e-9<target: continue
                    qty=min(remaining,original*fraction)
                    if i==len(fractions)-1 and abs(sum(fractions)-1)<1e-8: qty=remaining
                    if qty>1e-10:
                        self._trade("auto",ticker,"sell",qty,price,score,f"Profit target +{target:g}%",target_mask=mask|(1<<i))
                    remaining-=qty; mask|=1<<i
                    with self.db:
                        self.db.execute("UPDATE paper_positions SET target_mask=? WHERE account='auto' AND ticker=?",(mask,ticker))
                    if remaining<1e-8: break
                continue

            if session!="regular" and not rules["allow_overnight"]: continue
            qualifies=(score is not None and row.get("direction")==1 and score>=rules["buy_score"]
                       and rvol>=rules["confirmation_min_rvol"])
            earnings=row.get("earnings_days")
            if rules["avoid_earnings_days"] and (not isinstance(earnings,(int,float)) or not math.isfinite(earnings)
                                                or 0<=earnings<=rules["avoid_earnings_days"]):
                qualifies=False
            if not qualifies:
                with self.db:self.db.execute("DELETE FROM paper_pending WHERE ticker=?",(ticker,))
                continue
            last=self.db.execute("SELECT ts FROM paper_events WHERE account='auto' AND ticker=? AND side='sell' ORDER BY id DESC LIMIT 1",(ticker,)).fetchone()
            if last and (now-_stamp(last[0])).total_seconds()<rules["reentry_cooldown_hours"]*3600: continue
            allocation=rules["position_size"] if session=="regular" else rules["extended_hours_position_size"]
            required=rules["confirm_regular_scans"] if session=="regular" else rules["confirm_extended_scans"]
            if allocation<=0: continue
            pending=self.db.execute("SELECT * FROM paper_pending WHERE ticker=?",(ticker,)).fetchone()
            first_price=float(pending["first_price"]) if pending else price
            if (price/first_price-1)*100>rules["max_pending_price_rise_pct"]:
                with self.db:self.db.execute("DELETE FROM paper_pending WHERE ticker=?",(ticker,))
                continue
            confirmations=min(required,int(pending["confirmations"])+1 if pending else 1)
            with self.db:
                self.db.execute("INSERT OR REPLACE INTO paper_pending VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                    (ticker,pending["first_seen"] if pending else row["asof"],row["asof"],first_price,price,score,rvol,confirmations,required,session,allocation))
            if confirmations<required: continue
            count=self.db.execute("SELECT count(*) FROM paper_positions WHERE account='auto'").fetchone()[0]
            cash=float(self.db.execute("SELECT cash FROM paper_cash WHERE account='auto'").fetchone()[0])
            if count>=rules["max_positions"] or cash<allocation: continue
            self._trade("auto",ticker,"buy",allocation/price,price,score,
                        f"Confirmed {confirmations} distinct observations ({session}); simulated close fill")
            with self.db:self.db.execute("DELETE FROM paper_pending WHERE ticker=?",(ticker,))

    def snapshot(self, account, prices=None, now=None):
        self._account(account); prices=prices or {}
        with self.lock:
            positions=[dict(x) for x in self.db.execute("SELECT * FROM paper_positions WHERE account=? ORDER BY ticker",(account,))]
            cash=float(self.db.execute("SELECT cash FROM paper_cash WHERE account=?",(account,)).fetchone()[0])
            events=[dict(x) for x in self.db.execute("SELECT * FROM paper_events WHERE account=? ORDER BY id DESC",(account,))]
            benchmark_row=self.db.execute("SELECT payload FROM paper_meta WHERE key='spy_benchmark'").fetchone()
            pending=([dict(x) for x in self.db.execute("SELECT * FROM paper_pending ORDER BY score DESC")]
                     if account == "auto" else [])
        unrealised=0.0; market_value=0.0; missing=0
        for pos in positions:
            quote=prices.get(pos["ticker"],{})
            if not fresh_quote(quote,now):
                pos.update(current_price=None,market_value=None,unrealised=None,return_pct=None,price_status="Missing or stale quote")
                missing+=1
                continue
            current=float(quote["price"])
            pos["price_status"]="Fresh delayed quote"
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
                "events":events[:300],"market_value":None if missing else round(market_value,2),"equity":None if missing else round(cash+market_value,2),
                "unpriced_positions":missing,"known_market_value":round(market_value,2),
                "unrealised":None if missing else round(unrealised,2),"realised":round(realised,2),
                "win_rate":round(100*sum(float(e["realised"] or 0)>0 for e in exits)/len(exits),1) if exits else None,
                "max_realised_drawdown":round(max_drawdown,2),
                "spy_return_pct":round(benchmark_return,2) if benchmark_return is not None else None,
                "spy_started":benchmark.get("started")}
