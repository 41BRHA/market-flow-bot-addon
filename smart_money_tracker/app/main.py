import json
import logging
import os
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs,urlparse
from .core import Store,import_csv
from .prices import PriceBridge,politician_score
from .sync import Collector


def make_handler(store,collector,prices=None):
    class Handler(BaseHTTPRequestHandler):
        def send(self,code,obj,ctype='application/json'):
            body=obj if isinstance(obj,bytes) else json.dumps(obj,allow_nan=False).encode()
            self.send_response(code);self.send_header('Content-Type',ctype)
            self.send_header('Content-Length',str(len(body)))
            self.send_header('Cache-Control','no-store')
            self.send_header('X-Content-Type-Options','nosniff')
            self.end_headers();self.wfile.write(body)

        def do_GET(self):
            p=urlparse(self.path);q={k:v[0] for k,v in parse_qs(p.query).items()}
            try:
                if p.path=='/api/status': return self.send(200,store.status())
                if p.path=='/api/people': return self.send(200,{'people':store.people()})
                if p.path=='/api/trades':
                    result=store.search(q.get('ticker',''),q.get('person',''),q.get('action',''),q.get('since',''),q.get('limit',50),q.get('offset',0))
                    if prices is not None:
                        result['trades']=prices.enrich(result['trades'])
                    if q.get('grouped') in ('1','true','yes'):
                        grouped=store.disclosures(q.get('ticker',''),q.get('person',''),q.get('action',''),q.get('since',''),q.get('limit',25),q.get('offset',0))
                        people=[g['politician'] for g in grouped['disclosures']]
                        history=store.trades_for_people(people)
                        enriched=prices.enrich(history) if prices is not None else history
                        by_id={t.get('id'):t for t in enriched}
                        by_person={}
                        for trade in enriched:by_person.setdefault(trade.get('politician',''),[]).append(trade)
                        for group in grouped['disclosures']:
                            group['trades']=[by_id.get(t.get('id'),t) for t in group['trades']]
                            group['score']=politician_score(by_person.get(group['politician'],[]))
                        result.update(grouped)
                    result['status']=store.status();return self.send(200,result)
                if p.path=='/api/template.csv':
                    return self.send(200,b'ticker,politician,chamber,action,transaction_date,disclosure_date,amount,source_url,owner,asset,asset_type,notes\n','text/csv')
                if p.path in ('/','/index.html'):
                    return self.send(200,Path(__file__).with_name('index.html').read_bytes(),'text/html; charset=utf-8')
                return self.send(404,{'error':'Not found'})
            except ValueError as e: return self.send(400,{'error':str(e)})
            except Exception:
                logging.exception('request failed');return self.send(500,{'error':'Internal error'})

        def do_POST(self):
            # Mutations use POST; no CORS permission. Require non-simple request
            # header, preventing cross-origin forms from triggering imports/sync.
            if self.headers.get('X-Tracker-Request')!='1': return self.send(403,{'error':'Missing request header'})
            try:
                length=int(self.headers.get('Content-Length','0'))
                if length<0 or length>2_000_000: return self.send(413,{'error':'Maximum body is 2 MB'})
                content=self.rfile.read(length)
                path=urlparse(self.path).path
                if path=='/api/refresh': collector.request();return self.send(202,{'queued':True})
                if path=='/api/import':
                    rows=import_csv(content.decode('utf-8-sig'))
                    store.add_rows(rows);return self.send(200,{'accepted':len(rows),'note':'Exact duplicate CSV records are replaced, not added twice.'})
                return self.send(404,{'error':'Not found'})
            except (ValueError,UnicodeError) as e: return self.send(400,{'error':str(e)})
            except Exception:
                logging.exception('import failed');return self.send(500,{'error':'Import failed'})

        def log_message(self,*args): pass
    return Handler


def run():
    logging.basicConfig(level=logging.INFO)
    base=Path(os.environ.get('TRACKER_DATA','/data'));base.mkdir(parents=True,exist_ok=True)
    options_path=base/'options.json'
    options=json.loads(options_path.read_text()) if options_path.exists() else {}
    store=Store(str(base/'disclosures.db'));collector=Collector(store,options);collector.start()
    prices=PriceBridge(options.get('market_flow_url','http://local-market-flow-bot:8099'))
    ThreadingHTTPServer(('0.0.0.0',8098),make_handler(store,collector,prices)).serve_forever()

if __name__=='__main__': run()
