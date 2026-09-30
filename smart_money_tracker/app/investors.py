"""SEC 13F collector and quarterly holding comparison.

13F is deliberately kept separate from politician transaction disclosures:
it is a delayed quarterly holdings snapshot, not a trade tape.
"""
from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
import threading
import time
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

log = logging.getLogger("institutional-13f")
SEC_DATA = "https://data.sec.gov/submissions/CIK{cik}.json"
SEC_ARCHIVE = "https://www.sec.gov/Archives/edgar/data/{cik}/{accession}"

# Legal filing entities, not personalities.  Kept intentionally small and curated.
MANAGERS = (
    {"cik": "0001067983", "name": "Berkshire Hathaway", "lead": "Warren Buffett / investment managers"},
    {"cik": "0001336528", "name": "Pershing Square Capital Management", "lead": "Bill Ackman"},
    {"cik": "0001649339", "name": "Scion Asset Management", "lead": "Michael Burry"},
    {"cik": "0001350694", "name": "Bridgewater Associates", "lead": "Investment committee"},
    {"cik": "0001536411", "name": "Duquesne Family Office", "lead": "Stanley Druckenmiller"},
)
MANAGER_BY_CIK = {m["cik"]: m for m in MANAGERS}


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def local(tag):
    return str(tag).rsplit("}", 1)[-1]


def child_text(node, name):
    for item in node.iter():
        if local(item.tag) == name:
            return (item.text or "").strip()
    return ""


def parse_information_table(data, filing_date=""):
    root = ET.fromstring(data)
    rows = []
    # Since the 2023 form revision, value is reported in dollars. Older XML used thousands.
    multiplier = 1000 if filing_date and filing_date < "2023-01-03" else 1
    for item in root.iter():
        if local(item.tag) != "infoTable":
            continue
        value = int(float(child_text(item, "value") or 0)) * multiplier
        shares = float(child_text(item, "sshPrnamt") or 0)
        row = {
            "issuer": child_text(item, "nameOfIssuer"),
            "title_class": child_text(item, "titleOfClass"),
            "cusip": child_text(item, "cusip").upper(),
            "value": value,
            "shares": shares,
            "share_type": child_text(item, "sshPrnamtType"),
            "put_call": child_text(item, "putCall"),
            "discretion": child_text(item, "investmentDiscretion"),
            "voting_sole": float(child_text(item, "Sole") or 0),
            "voting_shared": float(child_text(item, "Shared") or 0),
            "voting_none": float(child_text(item, "None") or 0),
            # The official 13F information table has no dependable ticker field.
            "ticker": "",
        }
        if row["issuer"] and row["cusip"]:
            rows.append(row)
    return rows


def holding_key(row):
    return "|".join((row.get("cusip", ""), row.get("title_class", ""), row.get("put_call", "")))


class InvestorStore:
    def __init__(self, path):
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.lock = threading.RLock()
        self.db.executescript("""PRAGMA journal_mode=WAL; PRAGMA busy_timeout=5000;
        CREATE TABLE IF NOT EXISTS filings(accession TEXT PRIMARY KEY,cik TEXT,manager TEXT,
          filed_date TEXT,report_period TEXT,form TEXT,source_url TEXT,collected TEXT);
        CREATE TABLE IF NOT EXISTS holdings(accession TEXT,holding_key TEXT,payload TEXT,
          PRIMARY KEY(accession,holding_key));
        CREATE INDEX IF NOT EXISTS filing_manager_period ON filings(cik,report_period DESC,filed_date DESC);
        CREATE TABLE IF NOT EXISTS subscriptions(cik TEXT PRIMARY KEY,enabled INTEGER NOT NULL DEFAULT 0,
          changes TEXT NOT NULL,min_value REAL NOT NULL DEFAULT 0,start_date TEXT NOT NULL,updated TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS deliveries(id TEXT PRIMARY KEY,delivered TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY,value TEXT);""")
        self.db.commit()

    def set_meta(self, key, value):
        with self.lock, self.db:
            self.db.execute("INSERT OR REPLACE INTO meta VALUES(?,?)", (key, json.dumps(value)))

    def get_meta(self, key, default=None):
        with self.lock:
            row = self.db.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else default

    def has_filing(self, accession):
        with self.lock:
            return self.db.execute("SELECT 1 FROM filings WHERE accession=?", (accession,)).fetchone() is not None

    def save_filing(self, filing, rows):
        with self.lock, self.db:
            self.db.execute("INSERT OR REPLACE INTO filings VALUES(?,?,?,?,?,?,?,?)", (
                filing["accession"], filing["cik"], filing["manager"], filing["filed_date"],
                filing["report_period"], filing["form"], filing["source_url"], now()))
            self.db.execute("DELETE FROM holdings WHERE accession=?", (filing["accession"],))
            for row in rows:
                self.db.execute("INSERT OR REPLACE INTO holdings VALUES(?,?,?)",
                                (filing["accession"], holding_key(row), json.dumps(row)))

    def _filings(self, cik):
        with self.lock:
            return self.db.execute("SELECT * FROM filings WHERE cik=? ORDER BY report_period DESC,filed_date DESC", (cik,)).fetchall()

    def _holdings(self, accession):
        with self.lock:
            rows = self.db.execute("SELECT holding_key,payload FROM holdings WHERE accession=?", (accession,)).fetchall()
        return {r["holding_key"]: json.loads(r["payload"]) for r in rows}

    def managers(self):
        subs = {x["cik"]: x for x in self.subscriptions()}
        result = []
        for manager in MANAGERS:
            filings = self._filings(manager["cik"])
            latest = filings[0] if filings else None
            holdings = self._holdings(latest["accession"]) if latest else {}
            rule = subs.get(manager["cik"], {})
            result.append({**manager, "filing_count": len(filings),
                "latest_filed": latest["filed_date"] if latest else None,
                "report_period": latest["report_period"] if latest else None,
                "portfolio_value": sum(x["value"] for x in holdings.values()),
                "holding_count": len(holdings), "enabled": bool(rule.get("enabled")),
                "changes": rule.get("changes", ["New", "Increased", "Reduced", "Exited"]),
                "min_value": rule.get("min_value", 0), "alert_start_date": rule.get("start_date")})
        return result

    def comparison(self, cik, change="", query="", minimum=0, sort="value_desc"):
        if cik not in MANAGER_BY_CIK:
            raise ValueError("Unknown manager")
        if change and change not in ("New", "Increased", "Reduced", "Exited", "Unchanged"):
            raise ValueError("Invalid change filter")
        try: minimum = float(minimum or 0)
        except (TypeError, ValueError) as exc: raise ValueError("Minimum value must be a number") from exc
        if minimum < 0: raise ValueError("Minimum value cannot be negative")
        filings = self._filings(cik)
        if not filings:
            return {"manager": MANAGER_BY_CIK[cik], "filing": None, "previous": None, "holdings": []}
        current = filings[0]
        # Amendments for the same quarter replace that snapshot; compare with
        # the previous distinct report period rather than the original filing.
        previous = next((f for f in filings[1:] if f["report_period"] != current["report_period"]), None)
        cur = self._holdings(current["accession"]); prev = self._holdings(previous["accession"]) if previous else {}
        total = sum(x["value"] for x in cur.values())
        output = []
        for key in sorted(set(cur) | set(prev)):
            a, b = cur.get(key), prev.get(key)
            if a and not b: kind = "New"
            elif b and not a: kind = "Exited"
            elif a["shares"] > b["shares"]: kind = "Increased"
            elif a["shares"] < b["shares"]: kind = "Reduced"
            else: kind = "Unchanged"
            base = a or b
            row = {**base, "change": kind, "current_value": a["value"] if a else 0,
                   "previous_value": b["value"] if b else 0,
                   "value_change": (a["value"] if a else 0) - (b["value"] if b else 0),
                   "current_shares": a["shares"] if a else 0,
                   "previous_shares": b["shares"] if b else 0,
                   "share_change": (a["shares"] if a else 0) - (b["shares"] if b else 0),
                   "portfolio_pct": (a["value"] / total * 100) if a and total else 0}
            if change and kind != change: continue
            if max(row["current_value"], row["previous_value"]) < minimum: continue
            hay = (row["issuer"] + " " + row["cusip"] + " " + row.get("ticker", "")).lower()
            if query and query.lower() not in hay: continue
            output.append(row)
        if sort == "value_desc": output.sort(key=lambda x: x["current_value"], reverse=True)
        elif sort == "change_desc": output.sort(key=lambda x: abs(x["value_change"]), reverse=True)
        elif sort == "weight_desc": output.sort(key=lambda x: x["portfolio_pct"], reverse=True)
        elif sort == "issuer": output.sort(key=lambda x: x["issuer"].lower())
        else: raise ValueError("Invalid institutional sort")
        return {"manager": MANAGER_BY_CIK[cik], "filing": dict(current),
                "previous": dict(previous) if previous else None, "portfolio_value": total,
                "holdings": output, "total": len(output)}

    def subscriptions(self):
        with self.lock:
            rows = self.db.execute("SELECT * FROM subscriptions ORDER BY cik").fetchall()
        result=[]
        for r in rows:
            try: changes=json.loads(r["changes"])
            except Exception: changes=["New","Increased","Reduced","Exited"]
            result.append({"cik":r["cik"],"enabled":bool(r["enabled"]),"changes":changes,
                           "min_value":float(r["min_value"]),"start_date":r["start_date"]})
        return result

    def set_subscription(self, cik, enabled, changes, min_value=0):
        if cik not in MANAGER_BY_CIK: raise ValueError("Unknown manager")
        allowed={"New","Increased","Reduced","Exited"};changes=list(dict.fromkeys(x for x in changes if x in allowed))
        if not changes: raise ValueError("Select at least one change type")
        try:min_value=float(min_value or 0)
        except (TypeError,ValueError) as exc:raise ValueError("Minimum value must be a number") from exc
        if min_value<0 or min_value>1e13:raise ValueError("Minimum value is outside supported range")
        stamp=now();today=stamp[:10]
        with self.lock,self.db:
            old=self.db.execute("SELECT * FROM subscriptions WHERE cik=?",(cik,)).fetchone()
            start=today if enabled and (not old or not old["enabled"]) else old["start_date"] if old else today
            self.db.execute("INSERT OR REPLACE INTO subscriptions VALUES(?,?,?,?,?,?)",
                            (cik,1 if enabled else 0,json.dumps(changes),min_value,start,stamp))
            if enabled:
                for f in self._filings(cik): self.db.execute("INSERT OR IGNORE INTO deliveries VALUES(?,?)",(f["accession"],stamp))
        return next(x for x in self.managers() if x["cik"]==cik)

    def alert_rule(self,cik,filed_date):
        with self.lock:r=self.db.execute("SELECT * FROM subscriptions WHERE cik=? AND enabled=1",(cik,)).fetchone()
        if not r or filed_date<r["start_date"]:return None
        return {"changes":json.loads(r["changes"]),"min_value":float(r["min_value"])}

    def delivered(self,accession):
        with self.lock:return self.db.execute("SELECT 1 FROM deliveries WHERE id=?",(accession,)).fetchone() is not None

    def mark_delivered(self,accession):
        with self.lock,self.db:self.db.execute("INSERT OR IGNORE INTO deliveries VALUES(?,?)",(accession,now()))

    def status(self, configured):
        with self.lock:
            count=self.db.execute("SELECT count(*) FROM filings").fetchone()[0]
        return {"configured":configured,"state":self.get_meta("state","not_configured" if not configured else "starting"),
                "checked_at":self.get_meta("checked_at"),"error":self.get_meta("error"),"filing_count":count,
                "selected":sum(1 for x in self.subscriptions() if x["enabled"]),"refresh_hours":8}


class InvestorCollector:
    def __init__(self, store, user_agent, notifier=None, interval=8*3600):
        self.store=store;self.user_agent=str(user_agent or "").strip();self.notifier=notifier;self.interval=interval
        self.event=threading.Event();self.configured=bool(self.user_agent and "@" in self.user_agent and "." in self.user_agent)

    def request(self): self.event.set()

    def start(self):
        threading.Thread(target=self._loop,daemon=True,name="sec-13f").start()

    def _get(self,url):
        req=urllib.request.Request(url,headers={"User-Agent":self.user_agent,"Accept-Encoding":"identity"})
        with urllib.request.urlopen(req,timeout=30) as response:return response.read()

    def _json(self,url):return json.loads(self._get(url))

    def _filings(self,cik):
        doc=self._json(SEC_DATA.format(cik=cik));recent=doc.get("filings",{}).get("recent",{})
        fields=("accessionNumber","filingDate","reportDate","form","primaryDocument")
        rows=[]
        for values in zip(*(recent.get(x,[]) for x in fields)):
            row=dict(zip(fields,values))
            if row["form"] in ("13F-HR","13F-HR/A"):
                accession=row["accessionNumber"].replace("-","")
                rows.append({"accession":row["accessionNumber"],"filed_date":row["filingDate"],
                    "report_period":row["reportDate"],"form":row["form"],"primary":row["primaryDocument"],
                    "base":SEC_ARCHIVE.format(cik=int(cik),accession=accession)})
        return rows[:4]

    def _table(self,filing):
        index=self._json(filing["base"]+"/index.json")
        names=[x.get("name","") for x in index.get("directory",{}).get("item",[])]
        candidates=[x for x in names if x.lower().endswith(".xml") and x!=filing["primary"]]
        for name in candidates[:12]:
            data=self._get(filing["base"]+"/"+name)
            try: rows=parse_information_table(data,filing["filed_date"])
            except (ET.ParseError,ValueError):continue
            if rows:return rows
        raise ValueError("13F information table XML was not found")

    def cycle(self):
        if not self.configured:
            self.store.set_meta("state","not_configured");return
        self.store.set_meta("state","collecting");errors=[]
        for manager in MANAGERS:
            try:
                for filing in reversed(self._filings(manager["cik"])):
                    if self.store.has_filing(filing["accession"]):continue
                    rows=self._table(filing);record={**filing,"cik":manager["cik"],"manager":manager["name"],
                        "source_url":filing["base"]+"/"+filing["primary"]}
                    self.store.save_filing(record,rows)
                    self._notify(record)
                    time.sleep(.12)
            except Exception as exc:
                errors.append(manager["name"]+": "+type(exc).__name__);log.warning("13F collection failed for %s: %s",manager["name"],exc)
        self.store.set_meta("checked_at",now());self.store.set_meta("error","; ".join(errors) if errors else None)
        self.store.set_meta("state","partial" if errors else "ready")

    def _notify(self,filing):
        rule=self.store.alert_rule(filing["cik"],filing["filed_date"])
        if not rule or self.store.delivered(filing["accession"]):return
        data=self.store.comparison(filing["cik"]);eligible=[x for x in data["holdings"] if x["change"] in rule["changes"] and max(x["current_value"],x["previous_value"])>=rule["min_value"]]
        if not eligible:return
        leaders=sorted(eligible,key=lambda x:abs(x["value_change"]),reverse=True)[:4]
        lines=[f'{x["change"]}: {x["issuer"]} ({x["cusip"]}) ${abs(x["value_change"]):,.0f}' for x in leaders]
        if len(eligible)>4:lines.append(f'+ {len(eligible)-4} more changes')
        ok=self.notifier and self.notifier.send(f'New 13F — {filing["manager"]}',f'Reported {filing["report_period"]} · filed {filing["filed_date"]}\n'+"\n".join(lines))
        if ok:self.store.mark_delivered(filing["accession"])

    def _loop(self):
        while True:
            try:self.cycle()
            except Exception:log.exception("13F cycle failed")
            self.event.wait(self.interval);self.event.clear()
