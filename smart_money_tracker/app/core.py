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


def normalise(row, source, source_id):
    sym = ticker(row.get('ticker') or row.get('symbol'))
    if not sym:
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
    if chamber not in ('House', 'Senate'):
        raise ValueError('Chamber must be House or Senate')
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

    def disclosures(self, ticker_value='', person='', action='', since='', limit=25, offset=0):
        """One result row per politician and disclosure date, with its trades."""
        where,args=self._where(ticker_value,person,action,since)
        clause=' AND '.join(where);limit=max(1,min(50,int(limit)));offset=max(0,min(100000,int(offset)))
        with self.lock:
            total=self.db.execute('SELECT count(*) FROM (SELECT 1 FROM trades WHERE '+clause+' GROUP BY politician,disclosure_date)',args).fetchone()[0]
            keys=self.db.execute('SELECT politician,disclosure_date FROM trades WHERE '+clause+' GROUP BY politician,disclosure_date ORDER BY disclosure_date DESC,politician LIMIT ? OFFSET ?',args+[limit,offset]).fetchall()
            groups=[]
            for politician,disclosure_date in keys:
                group_where=where+['politician=?','disclosure_date=?']
                group_args=args+[politician,disclosure_date]
                rows=self.db.execute('SELECT payload FROM trades WHERE '+' AND '.join(group_where)+' ORDER BY transaction_date DESC,ticker,id',group_args).fetchall()
                trades=[json.loads(r[0]) for r in rows]
                groups.append(dict(politician=politician,disclosure_date=disclosure_date,
                                   chamber=trades[0].get('chamber') if trades else '',trades=trades,
                                   trade_count=len(trades)))
        return dict(disclosures=groups,disclosures_total=total,limit=limit,offset=offset)

    def people(self):
        with self.lock:
            rows=self.db.execute('SELECT politician,count(*) AS n,max(disclosure_date) AS latest FROM trades GROUP BY politician ORDER BY politician COLLATE NOCASE').fetchall()
        return [dict(name=r[0],trade_count=r[1],latest_disclosure=r[2]) for r in rows]

    def trades_for_people(self, names, limit=2000):
        names=list(dict.fromkeys(str(n) for n in names if n))[:50]
        if not names:return []
        marks=','.join('?' for _ in names)
        with self.lock:
            rows=self.db.execute(f'SELECT payload FROM trades WHERE politician IN ({marks}) ORDER BY disclosure_date DESC,transaction_date DESC LIMIT ?',names+[max(1,min(5000,int(limit)))]).fetchall()
        return [json.loads(r[0]) for r in rows]

    def status(self):
        with self.lock:
            counts={r[0]:r[1] for r in self.db.execute('SELECT state,count(*) FROM reports GROUP BY state')}
            total=self.db.execute('SELECT count(*) FROM trades').fetchone()[0]
            problems=[dict(r) for r in self.db.execute("SELECT id,state,error,skipped FROM reports WHERE state IN ('partial','error','unparsed') LIMIT 20")]
        return dict(house_reports=counts,trade_count=total,problems=problems,
                    house=self.get_meta('house',{}),senate=self.get_meta('senate',{'state':'not_configured'}),
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
