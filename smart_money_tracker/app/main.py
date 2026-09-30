import json
import logging
import math
import os
import threading
import time
from zoneinfo import ZoneInfo,ZoneInfoNotFoundError
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs,urlparse
from .core import Store,estimated_amount,import_csv,ticker
from .prices import PriceBridge,politician_score
from .profiles import ProfileService
from .sync import Collector
from .alerts import FilingAlerts
from .investors import InvestorStore,InvestorCollector


def start_stock_refresh(store,prices,interval=8*3600):
    """Warm politician-only market/option caches three times per day."""
    def loop():
        while True:
            tickers=store.tickers()
            try:
                if tickers:prices.stock_snapshots(tickers)
            except Exception:logging.exception('politician stock refresh failed')
            time.sleep(interval if tickers else 600)
    threading.Thread(target=loop,daemon=True).start()


def make_handler(store,collector,prices=None,profiles=None,alerts=None,investors=None,investor_collector=None,display_timezone='Europe/London'):
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
                if p.path=='/api/settings': return self.send(200,{'display_timezone':display_timezone})
                if p.path=='/api/people': return self.send(200,{'people':store.people()})
                if p.path=='/api/investors':
                    return self.send(200,{'managers':investors.managers(),'status':investors.status(investor_collector.configured)})
                if p.path=='/api/investor-holdings':
                    result=investors.comparison(q.get('cik',''),q.get('change',''),q.get('query',''),q.get('min_value',0),q.get('sort','value_desc'))
                    result['status']=investors.status(investor_collector.configured);return self.send(200,result)
                if p.path=='/api/alerts':
                    subscriptions={x['politician'].lower():x for x in store.alert_subscriptions()}
                    people=[]
                    for person in store.people():
                        rule=subscriptions.get(person['name'].lower(),{})
                        people.append({**person,'enabled':bool(rule.get('enabled',False)),
                            'actions':rule.get('actions',['Buy','Sell']),'min_value':rule.get('min_value',0),
                            'alert_start_date':rule.get('start_date')})
                    return self.send(200,{'people':people,'status':alerts.status() if alerts else {'configured':False,'selected':0}})
                if p.path=='/api/stocks':
                    rows=store.stock_exposure(q.get('person',''),q.get('chamber',''),q.get('action',''),
                        q.get('since',''),q.get('until',''),q.get('date_basis','disclosure'))
                    market=prices.stock_snapshots([r['ticker'] for r in rows]) if prices is not None else {}
                    people=list(dict.fromkeys(name for row in rows for name in row['politician_names']))
                    history=store.trades_for_people(people)
                    enriched=prices.enrich(history,cache_ttl=8*3600) if prices is not None else history
                    by_person={}
                    for trade in enriched:by_person.setdefault(trade.get('politician',''),[]).append(trade)
                    scores={name:politician_score(by_person.get(name,[])) for name in people}
                    for row in rows:
                        row['market']=market.get(row['ticker'],{'ticker':row['ticker'],'pending':True})
                        rated=[scores[n]['score'] for n in row['politician_names'] if scores.get(n,{}).get('rated_trades',0)>0]
                        row['average_politician_score']=round(sum(rated)/len(rated),1) if rated else None
                        row['rated_politicians']=len(rated)
                        for person_row in row['top_politicians']:person_row['score']=scores.get(person_row['name'],{}).get('score')
                    available_sectors=sorted({r['market'].get('sector') or 'Unclassified' for r in rows})
                    available_industries=sorted({r['market'].get('industry') for r in rows if r['market'].get('industry')})
                    sector=q.get('sector','');industry=q.get('industry','');minimum=float(q['min_value']) if q.get('min_value','').strip() else None
                    maximum=float(q['max_value']) if q.get('max_value','').strip() else None
                    min_people=max(0,int(q.get('min_politicians',0) or 0));min_score=float(q['min_score']) if q.get('min_score','').strip() else None
                    for value in (minimum,maximum,min_score):
                        if value is not None and not math.isfinite(value):raise ValueError('Stock filters must be finite numbers')
                    if (minimum is not None and minimum<0) or (maximum is not None and maximum<0):raise ValueError('Value filters cannot be negative')
                    if minimum is not None and maximum is not None and minimum>maximum:raise ValueError('Minimum value exceeds maximum')
                    if min_score is not None and not 0<=min_score<=100:raise ValueError('Minimum score must be between 0 and 100')
                    rows=[r for r in rows if (not sector or r['market'].get('sector')==sector)
                          and (not industry or r['market'].get('industry')==industry)
                          and (minimum is None or r['gross_estimated']>=minimum)
                          and (maximum is None or r['gross_estimated']<=maximum)
                          and r['politician_count']>=min_people
                          and (min_score is None or (r['average_politician_score'] is not None and r['average_politician_score']>=min_score))]
                    sort=q.get('sort','net_desc')
                    if sort=='net_desc':rows.sort(key=lambda r:(r['net_estimated'],r['gross_estimated']),reverse=True)
                    elif sort=='net_asc':rows.sort(key=lambda r:(r['net_estimated'],r['gross_estimated']))
                    elif sort=='gross_desc':rows.sort(key=lambda r:r['gross_estimated'],reverse=True)
                    elif sort=='politicians_desc':rows.sort(key=lambda r:(r['politician_count'],r['gross_estimated']),reverse=True)
                    elif sort=='score_desc':rows.sort(key=lambda r:(r['average_politician_score'] is not None,r['average_politician_score'] or 0),reverse=True)
                    elif sort=='recent':rows.sort(key=lambda r:(r['last_disclosure'],r['last_transaction']),reverse=True)
                    elif sort=='max_pain':rows.sort(key=lambda r:(r['market'].get('max_pain_distance_pct') is not None,abs(r['market'].get('max_pain_distance_pct') or 0)),reverse=True)
                    elif sort=='ticker':rows.sort(key=lambda r:r['ticker'])
                    else:raise ValueError('Invalid stock sort order')
                    total=len(rows);limit=max(1,min(500,int(q.get('limit',500))));offset=max(0,min(100000,int(q.get('offset',0))))
                    return self.send(200,{'stocks':rows[offset:offset+limit],'total':total,'limit':limit,'offset':offset,
                        'sectors':available_sectors,
                        'industries':available_industries,
                        'price_link':prices.status() if prices is not None else {'state':'unavailable'},
                        'refresh_hours':8,'status':store.status()})
                if p.path=='/api/stock-detail':
                    symbol=ticker(q.get('ticker',''))
                    if not symbol:raise ValueError('Invalid ticker')
                    result=store.search(symbol,limit=100,offset=0);result['trades']=prices.enrich(result['trades']) if prices is not None else result['trades']
                    for trade in result['trades']:
                        trade['estimated_value']=estimated_amount(trade)
                        if profiles is not None:trade['profile']=profiles.get(trade.get('politician',''),trade.get('chamber',''),trade.get('district',''))
                    result['market']=(prices.stock_snapshots([symbol],details=True).get(symbol,{}) if prices is not None else {})
                    return self.send(200,result)
                if p.path=='/api/trades':
                    result=store.search(q.get('ticker',''),q.get('person',''),q.get('action',''),q.get('since',''),q.get('limit',50),q.get('offset',0))
                    bridge=q.get('market_bridge') in ('1','true','yes')
                    if prices is not None:
                        result['trades']=prices.enrich_cached(result['trades']) if bridge else prices.enrich(result['trades'])
                    if q.get('grouped') in ('1','true','yes'):
                        groups=store.disclosures(q.get('ticker',''),q.get('person',''),q.get('action',''),q.get('since',''),
                                                q.get('min_value',''),q.get('max_value',''),q.get('value_scope','filing'))
                        people=list(dict.fromkeys(g['politician'] for g in groups))
                        history=store.trades_for_people(people)
                        enriched=(prices.enrich_cached(history) if bridge else prices.enrich(history)) if prices is not None else history
                        by_id={t.get('id'):t for t in enriched}
                        by_person={}
                        for trade in enriched:by_person.setdefault(trade.get('politician',''),[]).append(trade)
                        for group in groups:
                            group['trades']=[by_id.get(t.get('id'),t) for t in group['trades']]
                            for trade in group['trades']:
                                trade['estimated_value']=estimated_amount(trade)
                            group['score']=politician_score(by_person.get(group['politician'],[]))
                            if profiles is not None:
                                district=group['trades'][0].get('district','') if group['trades'] else ''
                                group['profile']=profiles.get(group['politician'],group.get('chamber',''),district)
                        sort=q.get('sort','recent')
                        if sort=='score_desc':
                            groups.sort(key=lambda g:(g['score'].get('rated_trades',0)>0,g['score'].get('score',50),g['disclosure_date']),reverse=True)
                        elif sort=='score_asc':
                            groups.sort(key=lambda g:(g['score'].get('rated_trades',0)==0,g['score'].get('score',50),g['disclosure_date']))
                        elif sort=='value_desc':
                            groups.sort(key=lambda g:(g.get('estimated_value') is not None,g.get('estimated_value') or 0,g['disclosure_date']),reverse=True)
                        elif sort=='value_asc':
                            groups.sort(key=lambda g:(g.get('estimated_value') is None,g.get('estimated_value') or 0,g['disclosure_date']))
                        elif sort=='recent':
                            groups.sort(key=lambda g:(g['disclosure_date'],g['politician'].lower()),reverse=True)
                        else:
                            raise ValueError('Invalid sort order')
                        limit=max(1,min(50,int(q.get('limit',25))));offset=max(0,min(100000,int(q.get('offset',0))))
                        result.update(disclosures=groups[offset:offset+limit],disclosures_total=len(groups),limit=limit,offset=offset)
                    if prices is not None:result['price_link']=prices.status()
                    if profiles is not None:result['profile_status']=profiles.status()
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
                if path=='/api/investors/refresh': investor_collector.request();return self.send(202,{'queued':True})
                if path=='/api/investor-alerts':
                    payload=json.loads(content.decode('utf-8') or '{}')
                    if not isinstance(payload.get('enabled'),bool):raise ValueError('Enabled must be true or false')
                    saved=investors.set_subscription(str(payload.get('cik') or ''),payload['enabled'],payload.get('changes') or [],payload.get('min_value',0))
                    return self.send(200,{'saved':saved,'status':investors.status(investor_collector.configured)})
                if path=='/api/alerts':
                    payload=json.loads(content.decode('utf-8') or '{}')
                    if not isinstance(payload.get('enabled'),bool):raise ValueError('Enabled must be true or false')
                    rule=store.set_alert_subscription(payload.get('politician'),payload['enabled'],
                                                       payload.get('actions') or [],payload.get('min_value',0))
                    return self.send(200,{'saved':rule,'status':alerts.status() if alerts else {'configured':False}})
                if path=='/api/alerts/test':
                    payload=json.loads(content.decode('utf-8') or '{}')
                    if not alerts or not alerts.send_test(str(payload.get('politician') or 'Selected politician')[:160]):
                        return self.send(503,{'error':'Notification delivery failed. Check the configured Home Assistant notify service.'})
                    return self.send(200,{'sent':True})
                if path=='/api/import':
                    rows=import_csv(content.decode('utf-8-sig'))
                    store.add_rows(rows);return self.send(200,{'accepted':len(rows),'note':'Exact duplicate CSV records are replaced, not added twice.'})
                return self.send(404,{'error':'Not found'})
            except (ValueError,UnicodeError,json.JSONDecodeError) as e: return self.send(400,{'error':str(e)})
            except Exception:
                logging.exception('import failed');return self.send(500,{'error':'Import failed'})

        def log_message(self,*args): pass
    return Handler


def run():
    logging.basicConfig(level=logging.INFO)
    base=Path(os.environ.get('TRACKER_DATA','/data'));base.mkdir(parents=True,exist_ok=True)
    options_path=base/'options.json'
    options=json.loads(options_path.read_text()) if options_path.exists() else {}
    display_timezone=str(options.get('display_timezone') or 'Europe/London').strip()
    try:ZoneInfo(display_timezone)
    except (ZoneInfoNotFoundError,ValueError):
        logging.warning('invalid display_timezone %r; using Europe/London',display_timezone);display_timezone='Europe/London'
    store=Store(str(base/'disclosures.db'))
    alerts=FilingAlerts(store,options.get('notify_service','notify.notify'))
    collector=Collector(store,options,alerts);collector.start()
    investors=InvestorStore(str(base/'institutional.db'))
    investor_collector=InvestorCollector(investors,options.get('sec_user_agent',''),alerts);investor_collector.start()
    prices=PriceBridge(options.get('market_flow_url','http://local-market-flow-bot:8099'))
    start_stock_refresh(store,prices)
    profiles=ProfileService(str(base/'politician_profiles.json'))
    ThreadingHTTPServer(('0.0.0.0',8098),make_handler(store,collector,prices,profiles,alerts,investors,investor_collector,display_timezone)).serve_forever()

if __name__=='__main__': run()
