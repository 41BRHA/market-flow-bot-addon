"""Normalisation and persistent disclosure storage. No market-price requests."""
import csv
import hashlib
import io
import json
import re
import sqlite3
import threading
from datetime import date, datetime, timezone
from urllib.parse import urlparse


def utcnow():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def ticker(value):
    value = str(value or '').strip().upper().replace('.', '-')
    return value if re.fullmatch(r'[A-Z][A-Z0-9-]{0,14}', value) else ''


def iso_date(value):
    value = str(value or '').strip()
    for fmt in ('%Y-%m-%d', '%m/%d/%Y', '%m/%d/%y'):
        try:
            return datetime.strptime(value[:10], fmt).date().isoformat()
        except ValueError:
            pass
    raise ValueError('Missing or invalid date: ' + value[:30])


def source_link(value):
    value = str(value or '').strip()
    p = urlparse(value)
    return value if p.scheme == 'https' and p.netloc and not p.username else ''


def amount_range(value):
    text = ' '.join(str(value or '').split())
    nums = [int(x.replace(',', '')) for x in re.findall(r'\$\s*([\d,]+)', text)]
    if len(nums) == 2 and nums[0] <= nums[1]:
        return text, nums[0], nums[1]
    if len(nums) == 1 and re.search('over|more than|>', text, re.I):
        return text, nums[0], None
    return text, None, None


def estimated_amount(trade):
    """Best single-value estimate for a disclosed range.

    Bounded ranges use their midpoint.  Open-ended ranges use the disclosed
    lower bound, so callers can still sort/filter them without pretending an
    unknown upper bound is known.
    """
    low, high = trade.get('amount_min'), trade.get('amount_max')
    if not isinstance(low, (int, float)):
        return None
    if isinstance(high, (int, float)):
        return (float(low) + float(high)) / 2
    return float(low)


def amount_summary(trades):
    estimates = [estimated_amount(t) for t in trades]
    known = [v for v in estimates if v is not None]
    lows = [t.get('amount_min') for t in trades]
    highs = [t.get('amount_max') for t in trades]
    return {
        'estimated_value': round(sum(known), 2) if known else None,
        'reported_min': sum(v for v in lows if isinstance(v, (int, float))) if any(isinstance(v, (int, float)) for v in lows) else None,
        'reported_max': sum(highs) if highs and all(isinstance(v, (int, float)) for v in highs) else None,
        'value_incomplete': len(known) != len(trades),
        'value_open_ended': any(isinstance(lo, (int, float)) and not isinstance(hi, (int, float)) for lo, hi in zip(lows, highs)),
    }


def alert_identity(politician, disclosure_date, trades):
    ids='|'.join(sorted(str(t.get('id') or t.get('source_id') or '') for t in trades))
    return hashlib.sha256(f'{politician}|{disclosure_date}|{ids}'.encode()).hexdigest()


def normalise(row, source, source_id):
    sym = ticker(row.get('ticker') or row.get('symbol'))
    if not sym and source!='oge_official':
        raise ValueError('No explicit valid ticker; company names are not guessed')
    person = str(row.get('politician') or ' '.join(filter(None, [row.get('firstName'), row.get('lastName')])) or row.get('office') or '').strip()
    if not person:
        raise ValueError('Politician name required')
    raw_type = str(row.get('action') or row.get('type') or '').strip()
    lower = raw_type.lower()
    if lower in ('p', 'buy', 'purchase') or lower.startswith('purchase'):
        action = 'Buy'
    elif lower.startswith(('s ', 's(', 'sale', 'sell')) or lower == 's':
        action = 'Sell'
    elif lower in ('e', 'exchange'):
        action = 'Exchange'
    else:
        action = 'Other'
    transaction = iso_date(row.get('transaction_date') or row.get('transactionDate'))
    disclosure = iso_date(row.get('disclosure_date') or row.get('disclosureDate'))
    if transaction > disclosure:
        raise ValueError('Transaction date is after disclosure date')
    amount, low, high = amount_range(row.get('amount'))
    owner = str(row.get('owner') or '').strip()
    owner = {'SP': 'Spouse', 'DC': 'Dependent child', 'JT': 'Joint'}.get(owner, owner or 'Not stated')
    chamber = str(row.get('chamber') or 'House')
    if chamber not in ('House', 'Senate', 'Executive'):
        raise ValueError('Branch must be House, Senate or Executive')
    record = dict(ticker=sym, politician=person, chamber=chamber, owner=owner,
        filer_status=str(row.get('filer_status') or 'Not verified'),
        action=action, transaction_type=raw_type, transaction_date=transaction,
        disclosure_date=disclosure, disclosure_delay_days=(date.fromisoformat(disclosure)-date.fromisoformat(transaction)).days,
        amount=amount or 'Not stated', amount_min=low, amount_max=high,
        asset=str(row.get('asset') or row.get('assetDescription') or ''),
        asset_type=str(row.get('asset_type') or row.get('assetType') or ''),
        source_url=source_link(row.get('source_url') or row.get('link')),
        source=source, source_id=source_id, district=str(row.get('district') or ''),
        notes=str(row.get('notes') or row.get('comment') or ''),
        amendment=bool(row.get('amendment')), seen_at=utcnow())
    record['id'] = hashlib.sha256((source+'|'+source_id).encode()).hexdigest()
    return record


class Store:
    def __init__(self, path):
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.lock = threading.RLock()
        self.db.executescript('''PRAGMA journal_mode=WAL; PRAGMA busy_timeout=5000;
        CREATE TABLE IF NOT EXISTS trades(id TEXT PRIMARY KEY, source TEXT, report TEXT,
          ticker TEXT, politician TEXT, action TEXT, transaction_date TEXT, disclosure_date TEXT, payload TEXT);
        CREATE INDEX IF NOT EXISTS trades_ticker_date ON trades(ticker,disclosure_date);
        CREATE TABLE IF NOT EXISTS reports(id TEXT PRIMARY KEY, payload TEXT, state TEXT DEFAULT 'pending',
          checked TEXT, error TEXT, digest TEXT, parsed INTEGER DEFAULT 0, skipped INTEGER DEFAULT 0);
        CREATE TABLE IF NOT EXISTS alert_subscriptions(politician TEXT PRIMARY KEY COLLATE NOCASE,
          enabled INTEGER NOT NULL DEFAULT 0, actions TEXT NOT NULL DEFAULT '["Buy","Sell"]',
          min_value REAL NOT NULL DEFAULT 0, start_date TEXT NOT NULL, updated TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS alert_deliveries(id TEXT PRIMARY KEY, delivered TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY,value TEXT);''')
        self.db.commit()
        with self.db:
            self.db.execute('INSERT OR IGNORE INTO meta VALUES(?,?)',('score_started_at',json.dumps(utcnow())))

    def set_meta(self, key, value):
        with self.lock, self.db:
            self.db.execute('INSERT OR REPLACE INTO meta VALUES(?,?)', (key,json.dumps(value)))

    def get_meta(self, key, default=None):
        with self.lock:
            r=self.db.execute('SELECT value FROM meta WHERE key=?',(key,)).fetchone()
        return json.loads(r[0]) if r else default

    def register(self, reports):
        with self.lock, self.db:
            for r in reports:
                self.db.execute('INSERT OR IGNORE INTO reports(id,payload) VALUES(?,?)',(r['id'],json.dumps(r)))

    def due(self, limit):
        with self.lock:
            rows=self.db.execute("SELECT * FROM reports WHERE state='pending' OR (state IN ('error','partial','unparsed') AND datetime(checked) < datetime('now','-1 day')) OR (state='parsed' AND datetime(checked) < datetime('now','-7 days')) ORDER BY state != 'pending', json_extract(payload,'$.disclosure_date') DESC LIMIT ?",(limit,)).fetchall()
        return [dict(r) for r in rows]

    def _upsert(self, r, report=''):
        self.db.execute('INSERT OR REPLACE INTO trades VALUES(?,?,?,?,?,?,?,?,?)',
          (r['id'],r['source'],report,r['ticker'],r['politician'],r['action'],r['transaction_date'],r['disclosure_date'],json.dumps(r)))

    def save_report(self, report, rows, skipped, digest):
        state='partial' if rows and skipped else 'parsed' if rows else 'unparsed'
        with self.lock, self.db:
            self.db.execute('DELETE FROM trades WHERE report=? AND source=?',(report,'house_official'))
            for r in rows: self._upsert(r,report)
            self.db.execute('UPDATE reports SET state=?,checked=?,error=NULL,digest=?,parsed=?,skipped=? WHERE id=?',
              (state,utcnow(),digest,len(rows),skipped,report))

    def report_error(self, report, message):
        with self.lock, self.db:
            self.db.execute("UPDATE reports SET state='error',checked=?,error=? WHERE id=?",(utcnow(),message[:300],report))

    def add_rows(self, rows):
        with self.lock, self.db:
            for r in rows: self._upsert(r)

    def search(self, ticker_value='', person='', action='', since='', limit=50, offset=0):
        where,args=self._where(ticker_value,person,action,since)
        clause=' AND '.join(where); limit=max(1,min(100,int(limit)));offset=max(0,min(100000,int(offset)))
        with self.lock:
            total=self.db.execute('SELECT count(*) FROM trades WHERE '+clause,args).fetchone()[0]
            rows=self.db.execute('SELECT payload FROM trades WHERE '+clause+' ORDER BY disclosure_date DESC,transaction_date DESC,id LIMIT ? OFFSET ?',args+[limit,offset]).fetchall()
        return dict(trades=[json.loads(r[0]) for r in rows],total=total,limit=limit,offset=offset)

    def _where(self, ticker_value='', person='', action='', since=''):
        where=['1=1']; args=[]
        if ticker_value:
            sym=ticker(ticker_value)
            if not sym: raise ValueError('Invalid ticker')
            where.append('ticker=?');args.append(sym)
        if person:
            where.append('instr(lower(politician),lower(?))>0');args.append(person[:100])
        if action:
            if action not in ('Buy','Sell','Exchange','Other'): raise ValueError('Invalid action')
            where.append('action=?');args.append(action)
        if since: where.append('disclosure_date>=?');args.append(iso_date(since))
        return where,args

    def disclosures(self, ticker_value='', person='', action='', since='', min_value='', max_value='', value_scope='filing'):
        """All matching disclosure groups before scoring/sorting/pagination.

        Value filters use the midpoint of each reported range.  For an
        open-ended range the disclosed lower bound is used and clearly marked
        in the returned summary.
        """
        where,args=self._where(ticker_value,person,action,since)
        clause=' AND '.join(where)
        if value_scope not in ('filing','transaction'):
            raise ValueError('Invalid value filter basis')
        try:
            minimum = float(min_value) if str(min_value).strip() else None
            maximum = float(max_value) if str(max_value).strip() else None
        except (TypeError, ValueError) as exc:
            raise ValueError('Estimated value filters must be numbers') from exc
        if (minimum is not None and minimum < 0) or (maximum is not None and maximum < 0):
            raise ValueError('Estimated value filters cannot be negative')
        if minimum is not None and maximum is not None and minimum > maximum:
            raise ValueError('Minimum estimated value exceeds maximum')
        with self.lock:
            rows=self.db.execute('SELECT payload FROM trades WHERE '+clause+' ORDER BY disclosure_date DESC,politician COLLATE NOCASE,transaction_date DESC,ticker,id',args).fetchall()
        grouped={}
        for row in rows:
            trade=json.loads(row[0]); value=estimated_amount(trade)
            if value_scope == 'transaction' and ((minimum is not None and (value is None or value < minimum)) or
                                                  (maximum is not None and (value is None or value > maximum))):
                continue
            key=(trade.get('politician',''),trade.get('disclosure_date',''))
            grouped.setdefault(key,[]).append(trade)
        groups=[]
        for (politician,disclosure_date),trades in grouped.items():
            values=amount_summary(trades); value=values['estimated_value']
            if value_scope == 'filing' and ((minimum is not None and (value is None or value < minimum)) or
                                           (maximum is not None and (value is None or value > maximum))):
                continue
            for trade in trades:
                trade['estimated_value']=estimated_amount(trade)
            groups.append(dict(politician=politician,disclosure_date=disclosure_date,
                               chamber=trades[0].get('chamber') if trades else '',trades=trades,
                               trade_count=len(trades),**values))
        return groups

    def people(self):
        with self.lock:
            rows=self.db.execute('SELECT politician,count(*) AS n,max(disclosure_date) AS latest FROM trades GROUP BY politician ORDER BY politician COLLATE NOCASE').fetchall()
        return [dict(name=r[0],trade_count=r[1],latest_disclosure=r[2]) for r in rows]

    def alert_subscriptions(self):
        with self.lock:
            rows=self.db.execute('SELECT politician,enabled,actions,min_value,start_date,updated FROM alert_subscriptions ORDER BY politician COLLATE NOCASE').fetchall()
        result=[]
        for r in rows:
            try: actions=json.loads(r['actions'])
            except Exception: actions=['Buy','Sell']
            result.append(dict(politician=r['politician'],enabled=bool(r['enabled']),actions=actions,
                               min_value=float(r['min_value']),start_date=r['start_date'],updated=r['updated']))
        return result

    def global_alert(self):
        rule=self.get_meta('global_filing_alert',{}) or {}
        return dict(enabled=bool(rule.get('enabled',False)),
                    actions=rule.get('actions',['Buy','Sell']),
                    min_value=float(rule.get('min_value',1_000_000) or 0),
                    start_date=rule.get('start_date'),updated=rule.get('updated'))

    def set_global_alert(self,enabled,actions,min_value=1_000_000):
        allowed={'Buy','Sell','Exchange','Other'}
        actions=list(dict.fromkeys(str(a) for a in actions if str(a) in allowed))
        if not actions:raise ValueError('Select at least one transaction type')
        try:min_value=float(min_value or 0)
        except (TypeError,ValueError) as exc:raise ValueError('Minimum value must be a number') from exc
        if min_value<0 or min_value>10_000_000_000:raise ValueError('Minimum value is outside the supported range')
        old=self.global_alert();now=utcnow()
        start=now[:10] if enabled and not old['enabled'] else old.get('start_date') or now[:10]
        rule=dict(enabled=bool(enabled),actions=actions,min_value=min_value,start_date=start,updated=now)
        self.set_meta('global_filing_alert',rule)
        return rule

    def set_alert_subscription(self, politician, enabled, actions, min_value=0):
        politician=' '.join(str(politician or '').split())[:160]
        if not politician:raise ValueError('Politician is required')
        allowed={'Buy','Sell','Exchange','Other'}
        actions=list(dict.fromkeys(str(a) for a in actions if str(a) in allowed))
        if not actions:raise ValueError('Select at least one transaction type')
        try:min_value=float(min_value or 0)
        except (TypeError,ValueError) as exc:raise ValueError('Minimum value must be a number') from exc
        if min_value<0 or min_value>10_000_000_000:raise ValueError('Minimum value is outside the supported range')
        names={p['name'].lower():p['name'] for p in self.people()}
        canonical=names.get(politician.lower())
        if not canonical:raise ValueError('Politician is not in the collected records')
        now=utcnow();today=now[:10]
        with self.lock,self.db:
            previous=self.db.execute('SELECT start_date,enabled FROM alert_subscriptions WHERE politician=?',(canonical,)).fetchone()
            newly_enabled=bool(enabled) and (not previous or not previous['enabled'])
            start=today if newly_enabled else previous['start_date'] if previous else today
            self.db.execute('INSERT OR REPLACE INTO alert_subscriptions VALUES(?,?,?,?,?,?)',
                            (canonical,1 if enabled else 0,json.dumps(actions),min_value,start,now))
            if enabled:
                rows=self.db.execute('SELECT payload FROM trades WHERE politician=? COLLATE NOCASE',(canonical,)).fetchall()
                groups={}
                for row in rows:
                    trade=json.loads(row[0])
                    if trade.get('action') in actions:groups.setdefault(trade.get('disclosure_date',''),[]).append(trade)
                for day,trades in groups.items():
                    self.db.execute('INSERT OR IGNORE INTO alert_deliveries VALUES(?,?)',
                                    (alert_identity(canonical,day,trades),now))
        return next(x for x in self.alert_subscriptions() if x['politician'].lower()==canonical.lower())

    def matching_alert(self, politician, disclosure_date):
        with self.lock:
            row=self.db.execute('SELECT * FROM alert_subscriptions WHERE politician=? COLLATE NOCASE AND enabled=1',(politician,)).fetchone()
        if not row or str(disclosure_date or '')<row['start_date']:return None
        try:actions=json.loads(row['actions'])
        except Exception:actions=['Buy','Sell']
        return dict(politician=row['politician'],actions=actions,min_value=float(row['min_value']),start_date=row['start_date'])

    def matching_alerts(self,politician,disclosure_date):
        rules=[]
        personal=self.matching_alert(politician,disclosure_date)
        if personal:rules.append(personal)
        global_rule=self.global_alert()
        if global_rule['enabled'] and str(disclosure_date or '')>=str(global_rule.get('start_date') or ''):
            rules.append({**global_rule,'politician':'*'})
        return rules

    def alert_delivered(self, identity):
        with self.lock:return self.db.execute('SELECT 1 FROM alert_deliveries WHERE id=?',(identity,)).fetchone() is not None

    def mark_alert_delivered(self, identity):
        with self.lock,self.db:self.db.execute('INSERT OR IGNORE INTO alert_deliveries VALUES(?,?)',(identity,utcnow()))

    def tickers(self):
        with self.lock:rows=self.db.execute('SELECT DISTINCT ticker FROM trades WHERE ticker!="" ORDER BY ticker').fetchall()
        return [r[0] for r in rows]

    def stock_exposure(self,person='',chamber='',action='',since='',until='',date_basis='disclosure'):
        """Aggregate disclosed transaction ranges by ticker.

        This is transaction-flow inference, not a verified holdings ledger:
        complete opening positions and full/partial-sale status are unavailable.
        """
        if date_basis not in ('disclosure','transaction'):raise ValueError('Invalid date basis')
        if chamber and chamber not in ('House','Senate','Executive'):raise ValueError('Invalid chamber')
        if action and action not in ('Buy','Sell','Exchange','Other'):raise ValueError('Invalid action')
        start=iso_date(since) if since else ''
        end=iso_date(until) if until else ''
        if start and end and start>end:raise ValueError('Start date is after end date')
        with self.lock:rows=self.db.execute('SELECT payload FROM trades ORDER BY disclosure_date DESC,transaction_date DESC').fetchall()
        grouped={}
        for row in rows:
            trade=json.loads(row[0]);day=trade.get(date_basis+'_date','')
            if person and person.lower() not in trade.get('politician','').lower():continue
            if chamber and trade.get('chamber')!=chamber:continue
            if action and trade.get('action')!=action:continue
            if (start and day<start) or (end and day>end):continue
            ticker_value=trade.get('ticker','');value=estimated_amount(trade)
            if not ticker_value:continue
            item=grouped.setdefault(ticker_value,dict(ticker=ticker_value,company='',trade_count=0,buy_count=0,sell_count=0,
                other_count=0,buy_estimated=0.0,sell_estimated=0.0,gross_estimated=0.0,net_estimated=0.0,
                estimated_count=0,unknown_value_count=0,politicians={},last_disclosure='',last_transaction=''))
            item['trade_count']+=1;act=trade.get('action')
            if act=='Buy':item['buy_count']+=1
            elif act=='Sell':item['sell_count']+=1
            else:item['other_count']+=1
            if value is None:item['unknown_value_count']+=1
            elif act in ('Buy','Sell'):
                item['estimated_count']+=1;item['gross_estimated']+=value
                signed=value if act=='Buy' else -value;item['net_estimated']+=signed
                item['buy_estimated' if act=='Buy' else 'sell_estimated']+=value
            name=str(trade.get('asset') or '').strip()
            if name and (not item['company'] or trade.get('disclosure_date','')>=item['last_disclosure']):item['company']=name
            item['last_disclosure']=max(item['last_disclosure'],trade.get('disclosure_date',''))
            item['last_transaction']=max(item['last_transaction'],trade.get('transaction_date',''))
            pol=item['politicians'].setdefault(trade.get('politician','Unknown'),dict(name=trade.get('politician','Unknown'),
                chamber=trade.get('chamber',''),trade_count=0,buy_count=0,sell_count=0,net_estimated=0.0,gross_estimated=0.0))
            pol['trade_count']+=1;pol['buy_count']+=act=='Buy';pol['sell_count']+=act=='Sell'
            if value is not None and act in ('Buy','Sell'):
                pol['gross_estimated']+=value;pol['net_estimated']+=value if act=='Buy' else -value
        result=[]
        for item in grouped.values():
            people=list(item.pop('politicians').values())
            for p in people:
                p['net_estimated']=round(p['net_estimated'],2);p['gross_estimated']=round(p['gross_estimated'],2)
            people.sort(key=lambda p:(abs(p['net_estimated']),p['trade_count']),reverse=True)
            item['politician_count']=len(people);item['politician_names']=[p['name'] for p in people];item['top_politicians']=people[:8]
            for key in ('buy_estimated','sell_estimated','gross_estimated','net_estimated'):item[key]=round(item[key],2)
            result.append(item)
        return result

    def trades_for_people(self, names, limit=50000):
        names=list(dict.fromkeys(str(n) for n in names if n))[:900]
        if not names:return []
        marks=','.join('?' for _ in names)
        with self.lock:
            rows=self.db.execute(f'SELECT payload FROM trades WHERE politician IN ({marks}) ORDER BY disclosure_date DESC,transaction_date DESC LIMIT ?',names+[max(1,min(50000,int(limit)))]).fetchall()
        return [json.loads(r[0]) for r in rows]

    def status(self):
        with self.lock:
            counts={r[0]:r[1] for r in self.db.execute('SELECT state,count(*) FROM reports GROUP BY state')}
            total=self.db.execute('SELECT count(*) FROM trades').fetchone()[0]
            problems=[dict(r) for r in self.db.execute("SELECT id,state,error,skipped FROM reports WHERE state IN ('partial','error','unparsed') LIMIT 20")]
        return dict(house_reports=counts,trade_count=total,problems=problems,
                    house=self.get_meta('house',{}),senate=self.get_meta('senate',{'state':'not_configured'}),
                    executive=self.get_meta('executive',{'state':'starting'}),
                    running=self.get_meta('running',False),coverage='partial',score_started_at=self.get_meta('score_started_at'),
                    note='House PTRs only within the configured disclosure window; unmatched, scanned and amended filings may be incomplete. Senate coverage is separate. Disclosed ranges are not exact trade values.')


def import_csv(text):
    reader=csv.DictReader(io.StringIO(text.lstrip('\ufeff')))
    required={'ticker','politician','chamber','action','transaction_date','disclosure_date','amount','source_url'}
    if not required.issubset(set(reader.fieldnames or [])):
        raise ValueError('CSV requires: '+', '.join(sorted(required)))
    rows=[]
    for i,row in enumerate(reader,2):
        if i>5001: raise ValueError('Maximum 5,000 rows per import')
        identity=json.dumps(row,sort_keys=True)
        try: rows.append(normalise(row,'csv_import',hashlib.sha256(identity.encode()).hexdigest()))
        except ValueError as e: raise ValueError(f'CSV row {i}: {e}') from e
    return rows
