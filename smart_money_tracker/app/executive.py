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
API='https://extapps2.oge.gov/201/Presiden.nsf/API.xsp/v2/rest'
UA={'User-Agent':'Smart Money Tracker (personal public-disclosure monitor)'}

# Exact issuer-name aliases only. OGE forms commonly omit exchange tickers; we
# intentionally do not fuzzy-guess names. Unknown bonds/private assets remain
# visible in Disclosures but are excluded from ticker exposure calculations.
KNOWN_TICKERS={
    'AUTOMATIC DATA PROCESSING INC':'ADP','INTUITIVE SURGICAL INC':'ISRG',
    'COMCAST CORP CL A':'CMCSA','PALANTIR TECHNOLOGIES INC CL A':'PLTR',
    'INSMED INC PAR $.01':'INSM','ALNYLAM PHARMACEUTICALS INC':'ALNY',
    'ENTEGRIS INC':'ENTG','NVIDIA CORP':'NVDA','VISA INC':'V',
    'PFIZER INC':'PFE','TEXAS INSTRS INC':'TXN','THE COCA-COLA CO':'KO',
    'NETFLIX INC':'NFLX','DELL TECHNOLOGIES INC':'DELL',
    'CADENCE DESIGN SYS INC':'CDNS','UNITEDHEALTH GROUP INC':'UNH',
    'CROCS INC':'CROX','STRYKER CORP':'SYK','ABBVIE INC':'ABBV',
    'ABBOTT LABS CO':'ABT','PEPSICO INC':'PEP','PAYCHEX INC':'PAYX',
}


def _reports():
    links=[]
    # The former PAS view now redirects to OGE's searchable collection. Its
    # public JSON endpoint is what the official table itself uses. Reading a
    # generous recent window catches the president's monthly 278-T reports.
    try:
        r=requests.get(API,params={'draw':1,'start':0,'length':2500},timeout=45,headers=UA);r.raise_for_status()
        for row in r.json().get('data',[]):
            if 'trump' not in str(row.get('name','')).lower():continue
            typ=html.unescape(str(row.get('type','')))
            if 'transaction' not in typ.lower():continue
            links.extend(re.findall(r"href=['\"]([^'\"]+\.pdf[^'\"]*)",typ,re.I))
    except Exception as exc:log.warning('OGE API discovery failed: %s',type(exc).__name__)
    if not links:
        r=requests.get(INDEX,timeout=30,headers=UA);r.raise_for_status()
        for m in re.finditer(r'href=["\']([^"\']+\.pdf[^"\']*)["\']',r.text,re.I):
            href=html.unescape(m.group(1));context=re.sub('<[^>]+>',' ',r.text[max(0,m.start()-500):m.end()+250])
            hay=(href+' '+html.unescape(context)).lower()
            if 'trump' in hay and ('278t' in hay or '278-t' in hay or 'transaction' in hay):links.append(urljoin(INDEX,href))
    return list(dict.fromkeys(urljoin(API,x) for x in links))


def _ticker(description):
    cleaned=' '.join(str(description or '').upper().split())
    if cleaned in KNOWN_TICKERS:return KNOWN_TICKERS[cleaned]
    tagged=re.search(r'\((?:NYSE|NASDAQ|AMEX)\s*:\s*([A-Z]{1,5}(?:-[A-Z])?)\)',cleaned)
    return tagged.group(1) if tagged else ''


def _clean_amount(value):
    value=str(value or '').replace('S','$')
    value=re.sub(r'(?<=\d)[ ,.](?=\d{3}\b)',',',value)
    value=re.sub(r'\$\s*','$',value)
    return value


def _kind_match(text):
    """Tolerate the recurring OGE scan OCR errors without matching bond 'B/E'."""
    return re.search(r'\b(?:purchase|purchaso|ourchase|ourchaso|lourchase|lourchaso|curchase|curchaso|sale|exchange)\b',text,re.I)


def _make_row(url,joined,description,kind,dates,amount,document_date):
    low=kind.lower();action='Purchase' if 'urchas' in low else 'Sale' if low=='sale' else 'Exchange'
    filed=document_date or (dates[1] if len(dates)>1 else dates[0])
    raw=dict(ticker=_ticker(description),politician='Donald J. Trump',chamber='Executive',owner='Not stated',
             action=action,transaction_date=dates[0],disclosure_date=filed,amount=_clean_amount(amount),
             asset=description,asset_type='OGE 278-T',source_url=url,
             filer_status='President — Executive Branch')
    identity=hashlib.sha256((url+'|'+joined).encode()).hexdigest()
    return normalise(raw,'oge_official',identity)


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
            amount=re.search(r'[\$S]\s*[\d ,.]+\s*(?:-|–|to)\s*[\$S]?\s*[\d ,.]+|(?:over|more than)\s*[\$S]\s*[\d ,.]+',joined,re.I)
            dates=re.findall(r'\b\d{1,2}/\d{1,2}/\d{2,4}\b',joined)
            kind=_kind_match(joined)
            if not amount or not dates or not kind:continue
            before=joined[:kind.start()]
            description=max((x for x in cells if x and '$' not in x and not re.fullmatch(r'\d{1,2}/\d{1,2}/\d{2,4}',x)),key=len,default=before)
            try:rows.append(_make_row(url,joined,description,kind.group(0),dates,amount.group(0),document_date))
            except ValueError as exc:log.debug('OGE row skipped: %s',exc)
    # Table extraction varies between digitally generated and scanned forms.
    # Fall back to line-oriented OCR text, de-duplicating against table rows.
    with pdfplumber.open(io.BytesIO(content)) as pdf:text='\n'.join(p.extract_text(x_tolerance=2,y_tolerance=3) or '' for p in pdf.pages)
    for line in text.splitlines():
        line=' '.join(line.split())
        amount=re.search(r'[\$S]\s*[\d ,.]+\s*(?:-|–|to|•)\s*[\$S]?\s*[\d ,.]+',line,re.I)
        dates=re.findall(r'\b\d{1,2}/\d{1,2}/\d{2,4}\b',line)
        kind=_kind_match(line)
        if not (amount and dates and kind):continue
        description=re.sub(r'^\s*\d+\s+','',line[:kind.start()]).strip(' |')
        try:rows.append(_make_row(url,line,description,kind.group(0),dates,amount.group(0),document_date))
        except ValueError as exc:log.debug('OGE OCR row skipped: %s',exc)
    return list({r['id']:r for r in rows}.values())


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
            state='ready' if stored else 'no_records'
            self.store.set_meta('executive',{'state':state,'checked_at':utcnow(),'documents':len(links),
                'parsed_trades':len(stored),'errors':errors[:10],
                'note':'Direct-download OGE 278-T reports; assets without explicit tickers remain disclosure-only.'})
        except Exception as exc:
            log.warning('OGE index refresh failed: %s',type(exc).__name__)
            self.store.set_meta('executive',{'state':'error','checked_at':utcnow(),'error':type(exc).__name__})
