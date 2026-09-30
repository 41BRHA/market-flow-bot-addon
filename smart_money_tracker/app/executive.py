"""Best-effort collector for directly downloadable OGE executive disclosures.

The OGE PAS view is the official index.  Only documents available for direct
download are read; the collector never submits Form 201 requests or bypasses
access controls.  Asset names without an explicit ticker remain visible in the
disclosure history but are excluded from ticker-based exposure calculations.
"""
from __future__ import annotations

import hashlib
import html
import io
import logging
import re
import threading
import time
from urllib.parse import urljoin

import pdfplumber
import requests

from .core import normalise,utcnow

log=logging.getLogger('oge-collector')
INDEX='https://extapps2.oge.gov/201/Presiden.nsf/PAS%20Filings%20by%20Date?OpenView'
UA={'User-Agent':'Smart Money Tracker (personal public-disclosure monitor)'}


def _reports():
    r=requests.get(INDEX,timeout=30,headers=UA);r.raise_for_status()
    links=[]
    for m in re.finditer(r'href=["\']([^"\']+\.pdf[^"\']*)["\']',r.text,re.I):
        href=html.unescape(m.group(1));context=re.sub('<[^>]+>',' ',r.text[max(0,m.start()-500):m.end()+250])
        hay=(href+' '+html.unescape(context)).lower()
        if 'trump' in hay and ('278t' in hay or '278-t' in hay or 'transaction' in hay):
            links.append(urljoin(INDEX,href))
    return list(dict.fromkeys(links))


def _ticker(description):
    matches=re.findall(r'[\[(]([A-Z]{1,5}(?:-[A-Z])?)[\])]',description or '')
    return matches[-1] if matches else ''


def _parse(url,content):
    rows=[]
    url_date=re.search(r'(\d{1,2})[.-](\d{1,2})[.-](20\d{2})',url)
    document_date=(f'{url_date.group(1)}/{url_date.group(2)}/{url_date.group(3)}' if url_date else '')
    with pdfplumber.open(io.BytesIO(content)) as pdf:
        tables=[]
        for page in pdf.pages:tables.extend(page.extract_tables() or [])
    for table in tables:
        for cells in table or []:
            cells=[' '.join(str(x or '').split()) for x in cells]
            joined=' | '.join(cells)
            amount=re.search(r'\$\s*[\d,]+\s*(?:-|–|to)\s*\$?\s*[\d,]+|(?:over|more than)\s*\$\s*[\d,]+',joined,re.I)
            dates=re.findall(r'\b\d{1,2}/\d{1,2}/\d{2,4}\b',joined)
            kind=re.search(r'\b(Purchase|Sale|Exchange|P|S|E)\b',joined,re.I)
            if not amount or not dates or not kind:continue
            action={'p':'Purchase','s':'Sale','e':'Exchange'}.get(kind.group(1).lower(),kind.group(1))
            before=joined[:kind.start()]
            description=max((x for x in cells if x and '$' not in x and not re.fullmatch(r'\d{1,2}/\d{1,2}/\d{2,4}',x)),key=len,default=before)
            filed=document_date or (dates[1] if len(dates)>1 else dates[0])
            raw=dict(ticker=_ticker(description),politician='Donald J. Trump',chamber='Executive',owner='Not stated',
                     action=action,transaction_date=dates[0],disclosure_date=filed,amount=amount.group(0),
                     asset=description,asset_type='OGE 278-T',source_url=url,
                     filer_status='President — Executive Branch')
            identity=hashlib.sha256((url+'|'+joined).encode()).hexdigest()
            try:rows.append(normalise(raw,'oge_official',identity))
            except ValueError as exc:log.debug('OGE row skipped: %s',exc)
    return rows


class ExecutiveCollector:
    def __init__(self,store,alerts=None,enabled=True,refresh_hours=6):
        self.store=store;self.alerts=alerts;self.enabled=bool(enabled);self.refresh_hours=max(1,int(refresh_hours));self.wake=threading.Event()

    def start(self):threading.Thread(target=self._loop,daemon=True).start()
    def request(self):self.wake.set()

    def _loop(self):
        while True:
            if self.enabled:self.collect()
            else:self.store.set_meta('executive',{'state':'disabled','checked_at':utcnow()})
            self.wake.wait(self.refresh_hours*3600);self.wake.clear()

    def collect(self):
        stored=[];errors=[]
        try:
            links=_reports()
            for url in links:
                try:
                    r=requests.get(url,timeout=45,headers=UA);r.raise_for_status();stored.extend(_parse(url,r.content))
                except Exception as exc:errors.append(type(exc).__name__)
            if stored:self.store.add_rows(stored)
            if self.alerts and stored:self.alerts.process(stored)
            self.store.set_meta('executive',{'state':'ready','checked_at':utcnow(),'documents':len(links),
                'parsed_trades':len(stored),'errors':errors[:10],
                'note':'Direct-download OGE 278-T reports; assets without explicit tickers remain disclosure-only.'})
        except Exception as exc:
            log.warning('OGE index refresh failed: %s',type(exc).__name__)
            self.store.set_meta('executive',{'state':'error','checked_at':utcnow(),'error':type(exc).__name__})
