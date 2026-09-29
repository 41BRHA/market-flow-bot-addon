import hashlib
import json
import logging
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date,timedelta
from .core import normalise, utcnow
from .house import BASE,parse_index,parse_pdf

log=logging.getLogger('disclosures')


def download(url, max_bytes=20_000_000):
    req=urllib.request.Request(url,headers={'User-Agent':'PersonalDisclosureTracker/0.1','Accept':'*/*'})
    try:
        with urllib.request.urlopen(req,timeout=30) as r:
            data=r.read(max_bytes+1)
    except urllib.error.HTTPError as e:
        # Never echo a credential-bearing URL into API output or logs.
        raise RuntimeError(f'Source returned HTTP {e.code}') from None
    except Exception:
        raise RuntimeError('Source unavailable or connection timed out') from None
    if len(data)>max_bytes: raise ValueError('Source response exceeds size limit')
    return data


class Collector:
    def __init__(self,store,options):
        self.store=store;self.options=options
        self.lock=threading.Lock();self.wake=threading.Event();self.stop=threading.Event()
        self.store.set_meta('running',False)
        self.thread=None
        self.force=False
        self.last_manual=0.0
        self.manual_lock=threading.Lock()

    def start(self):
        self.thread=threading.Thread(target=self.loop,daemon=True);self.thread.start()

    def request(self):
        with self.manual_lock:
            if time.monotonic()-self.last_manual>=60:
                self.force=True
                self.last_manual=time.monotonic()
        self.wake.set()

    def loop(self):
        while not self.stop.is_set():
            self.wake.clear()
            try: self.cycle()
            except Exception as e: log.error('collector cycle: %s',type(e).__name__)
            self.wake.wait(60)

    def cycle(self):
        if not self.lock.acquire(False): return
        self.store.set_meta('running',True)
        try:
            with self.manual_lock:
                force=self.force
                self.force=False
            self.house(force)
            self.senate(force)
        finally:
            self.store.set_meta('running',False);self.lock.release()

    def house(self,force=False):
        if not self.options.get('house_enabled',True):
            self.store.set_meta('house',{'state':'disabled'});return
        status=self.store.get_meta('house',{})
        interval=max(3600,int(self.options.get('refresh_hours',6))*3600)
        if force or time.time()-status.get('index_attempt_epoch',0)>=interval:
            since=(date.today()-timedelta(days=int(self.options.get('lookback_days',365)))).isoformat()
            errors=[];count=0
            for year in range(int(since[:4]),date.today().year+1):
                try:
                    reports=parse_index(download(f'{BASE}/public_disc/financial-pdfs/{year}FD.zip'),year,since)
                    self.store.register(reports);count+=len(reports)
                except Exception as e: errors.append(f'{year}: {str(e)}')
            status.update(state='error' if errors else 'ready',index_attempt_epoch=time.time(),
                          checked_at=utcnow(),since=since,index_reports=count,errors=errors)
            if not errors: status['last_success']=utcnow()
            self.store.set_meta('house',status)
        for record in self.store.due(int(self.options.get('reports_per_cycle',30))):
            report=json.loads(record['payload'])
            try:
                content=download(report['source_url'])
                if not content.startswith(b'%PDF'): raise ValueError('Expected PDF response')
                rows,skipped=parse_pdf(content,report)
                self.store.save_report(report['id'],rows,skipped,hashlib.sha256(content).hexdigest())
            except Exception as e:
                self.store.report_error(report['id'],str(e))
            if self.stop.wait(1): break

    def senate(self,force=False):
        key=str(self.options.get('fmp_api_key') or '').strip()
        if not key:
            self.store.set_meta('senate',{'state':'not_configured','note':'Optional FMP key with Senate endpoint access, or CSV import.'});return
        status=self.store.get_meta('senate',{})
        if not force and time.time()-status.get('attempt_epoch',0)<max(3600,int(self.options.get('refresh_hours',6))*3600): return
        rows=[];skipped=0;truncated=True
        try:
            # Latest pages only; explicit bounded coverage, never claim full history.
            for page in range(int(self.options.get('senate_pages',5))):
                query=urllib.parse.urlencode({'page':page,'limit':100,'apikey':key})
                data=json.loads(download('https://financialmodelingprep.com/stable/senate-latest?'+query))
                if not isinstance(data,list): raise ValueError('Provider rejected request or returned an unexpected format')
                for raw in data:
                    raw={**raw,'chamber':'Senate'}
                    identity=json.dumps(raw,sort_keys=True)
                    try: rows.append(normalise(raw,'fmp_senate',hashlib.sha256(identity.encode()).hexdigest()))
                    except (ValueError,TypeError): skipped+=1
                if len(data)<100: truncated=False;break
                if self.stop.wait(1): break
            self.store.add_rows(rows)
            status.update(state='ready',checked_at=utcnow(),last_success=utcnow(),loaded=len(rows),skipped=skipped,
                          limited_to_latest_pages=truncated,error=None)
        except Exception as e:
            status.update(state='error',error=str(e),checked_at=utcnow())
        status['attempt_epoch']=time.time();self.store.set_meta('senate',status)
