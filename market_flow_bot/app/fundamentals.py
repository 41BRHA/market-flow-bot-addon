"""Persistent, non-blocking company profile and valuation cache."""
from __future__ import annotations

import json
import math
import os
import re
import sqlite3
import threading
import time
import queue


def _number(value, positive=False):
    try:value=float(value)
    except (TypeError,ValueError):return None
    if not math.isfinite(value) or (positive and value<=0):return None
    return value


class FundamentalsService:
    def __init__(self,path=None):
        base='/data' if os.path.isdir('/data') else os.path.dirname(__file__)
        self.db=sqlite3.connect(path or os.path.join(base,'fundamentals.db'),check_same_thread=False)
        self.db.execute('PRAGMA busy_timeout=5000')
        self.db.execute('CREATE TABLE IF NOT EXISTS fundamentals(ticker TEXT PRIMARY KEY,payload TEXT,updated REAL,error TEXT)')
        self.db.commit();self.lock=threading.RLock();self.inflight=set();self.q=queue.Queue()
        threading.Thread(target=self._worker,daemon=True).start()

    def _cached(self,ticker):
        with self.lock:
            row=self.db.execute('SELECT payload,updated,error FROM fundamentals WHERE ticker=?',(ticker,)).fetchone()
        if not row:return None
        payload=json.loads(row[0]) if row[0] else {'ticker':ticker}
        payload['age_seconds']=round(max(0,time.time()-row[1]));payload['error']=row[2]
        return payload

    def get(self,ticker):
        ticker=str(ticker or '').strip().upper().replace('.','-')
        if not re.fullmatch(r'[A-Z][A-Z0-9-]{0,14}',ticker):return {'ticker':ticker,'error':'Unsupported ticker'}
        cached=self._cached(ticker);age=cached.get('age_seconds',10**9) if cached else 10**9
        retry_after=3600 if cached and cached.get('error') else 86400
        with self.lock:
            start=age>retry_after and ticker not in self.inflight
            if start:self.inflight.add(ticker);self.q.put(ticker)
        if cached:
            cached['stale']=age>86400;cached['pending']=start
            return cached
        return {'ticker':ticker,'pending':True}

    def get_many(self,tickers):
        return {ticker:self.get(ticker) for ticker in dict.fromkeys(tickers)}

    def _worker(self):
        while True:
            ticker=self.q.get();self._refresh(ticker);time.sleep(1.0)

    def _refresh(self,ticker):
        try:
            import yfinance as yf
            info=yf.Ticker(ticker).get_info() or {}
            payload={
                'ticker':ticker,'name':str(info.get('longName') or info.get('shortName') or ticker)[:160],
                'quote_type':str(info.get('quoteType') or ''),'currency':str(info.get('currency') or ''),
                'sector':str(info.get('sector') or '')[:100],
                'industry':str(info.get('industry') or '')[:140],
                'market_cap':_number(info.get('marketCap'),True),
                'enterprise_value':_number(info.get('enterpriseValue'),True),
                'trailing_pe':_number(info.get('trailingPE'),True),
                'forward_pe':_number(info.get('forwardPE'),True),
                'price_to_book':_number(info.get('priceToBook'),True),
                'profit_margin':_number(info.get('profitMargins')),
                'revenue_growth':_number(info.get('revenueGrowth')),
                'week_52_low':_number(info.get('fiftyTwoWeekLow'),True),
                'week_52_high':_number(info.get('fiftyTwoWeekHigh'),True),
                'as_of':time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime()),
            }
            with self.lock,self.db:
                self.db.execute('INSERT OR REPLACE INTO fundamentals VALUES(?,?,?,NULL)',(ticker,json.dumps(payload),time.time()))
        except Exception as exc:  # noqa: BLE001
            with self.lock,self.db:
                old=self.db.execute('SELECT payload FROM fundamentals WHERE ticker=?',(ticker,)).fetchone()
                self.db.execute('INSERT OR REPLACE INTO fundamentals VALUES(?,?,?,?)',(ticker,old[0] if old else json.dumps({'ticker':ticker}),time.time(),type(exc).__name__))
        finally:
            with self.lock:self.inflight.discard(ticker)
