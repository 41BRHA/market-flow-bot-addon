"""Read-only price bridge to Market Flow plus transparent politician scoring.

Market Flow's internal Home-Assistant hostname depends on how it was installed
(Local vs a GitHub repo), so a single hardcoded default fails. We try a list of
candidate hostnames — the configured one first, then one DERIVED from this
add-on's own hostname (which shares the repo prefix, e.g. this tracker at
77bc3c25-smart-money-tracker implies Market Flow at 77bc3c25-market-flow-bot),
then the common fallbacks — and remember the first that answers.
"""
import json
import math
import os
import threading
import time
import urllib.parse
import urllib.request


class PriceBridge:
    def __init__(self, base_url):
        self.configured = str(base_url or '').rstrip('/')
        self.base_url = self.configured        # kept for external references
        self._good = None                       # last base_url that worked
        self.cache = {}
        self.stock_cache = {}
        self.lock = threading.Lock()
        self.last_success = None
        self.last_error = None

    @staticmethod
    def _key(trade):
        return f"{trade.get('ticker','')}|{trade.get('transaction_date','')}"

    def _candidates(self):
        cands = []
        if self.configured:
            cands.append(self.configured)
        own = os.environ.get('HOSTNAME', '') or ''
        for suffix in ('-smart-money-tracker', '_smart_money_tracker'):
            if own.endswith(suffix):
                prefix = own[:-len(suffix)]
                if prefix:
                    cands.append('http://%s-market-flow-bot:8099' % prefix)
                break
        cands += ['http://local-market-flow-bot:8099', 'http://market-flow-bot:8099']
        seen, out = set(), []
        if self._good:
            out.append(self._good); seen.add(self._good)
        for c in cands:
            if c and c not in seen:
                seen.add(c); out.append(c)
        return out

    def _fetch(self, pairs):
        query = urllib.parse.urlencode([('pair', p) for p in pairs])
        last = None
        for base in self._candidates():
            try:
                req = urllib.request.Request(
                    base + '/api/trade-prices?' + query,
                    headers={'User-Agent': 'SmartMoneyTracker/0.2', 'Accept': 'application/json'})
                with urllib.request.urlopen(req, timeout=5) as response:
                    raw = response.read(1_000_001)
                if len(raw) > 1_000_000:
                    raise ValueError('Price response too large')
                data = json.loads(raw)
                if not isinstance(data, dict) or data.get('error'):
                    raise RuntimeError('Market Flow price endpoint unavailable')
                self._good = base           # remember the winner
                return data.get('prices', {})
            except Exception as exc:        # noqa: BLE001 - try the next candidate
                last = exc
                continue
        raise last or RuntimeError('No Market Flow host reachable')

    def enrich(self, trades, cache_ttl=60):
        out = [dict(t) for t in trades]
        keys = list(dict.fromkeys(self._key(t) for t in out if t.get('action') in ('Buy', 'Sell')))
        if not keys:
            return out
        now = time.monotonic(); needed = []; found = {}
        with self.lock:
            for key in keys:
                cached = self.cache.get(key)
                ttl = 5 if cached and cached[1].get('pending') else max(5,float(cache_ttl))
                if cached and now - cached[0] < ttl:
                    found[key] = cached[1]
                else:
                    needed.append(key)
        failed = False
        for i in range(0, len(needed), 75):
            batch = needed[i:i + 75]
            try:
                received = self._fetch(batch); self.last_success = time.time(); self.last_error = None
            except Exception as exc:  # noqa: BLE001
                received = {}; self.last_error = type(exc).__name__; failed = True
            with self.lock:
                for key in batch:
                    value = received.get(key, {'pending': True})
                    self.cache[key] = (now, value); found[key] = value
            if failed:
                with self.lock:
                    for key in needed[i + 75:]:
                        self.cache[key] = (now, {'pending': True}); found[key] = {'pending': True}
                break
        for trade in out:
            price = found.get(self._key(trade), {})
            trade['estimated_price'] = price.get('estimated_price')
            trade['estimated_price_date'] = price.get('price_date')
            trade['current_price'] = price.get('current_price')
            trade['current_price_asof'] = price.get('current_asof')
            trade['price_pending'] = bool(price.get('pending', False))
            try:
                estimate = float(trade['estimated_price']); current = float(trade['current_price'])
                market_return = (current / estimate - 1) * 100
                trade['directional_return_pct'] = round(market_return if trade.get('action') == 'Buy' else -market_return, 2)
            except (TypeError, ValueError, ZeroDivisionError):
                trade['directional_return_pct'] = None
        return out

    def enrich_cached(self,trades):
        """Enrich without HTTP, used when the caller is Market Flow itself.

        This breaks the Market Flow -> Smart Money -> Market Flow circular wait.
        The normal background warmer and dashboard requests still populate the
        same cache, so available prices and scores are retained.
        """
        out=[dict(t) for t in trades]
        with self.lock:
            found={key:value[1] for key,value in self.cache.items()}
        for trade in out:
            price=found.get(self._key(trade),{})
            trade['estimated_price']=price.get('estimated_price')
            trade['estimated_price_date']=price.get('price_date')
            trade['current_price']=price.get('current_price')
            trade['current_price_asof']=price.get('current_asof')
            trade['price_pending']=not bool(price) or bool(price.get('pending',False))
            try:
                estimate=float(trade['estimated_price']);current=float(trade['current_price'])
                market_return=(current/estimate-1)*100
                trade['directional_return_pct']=round(market_return if trade.get('action')=='Buy' else -market_return,2)
            except (TypeError,ValueError,ZeroDivisionError):trade['directional_return_pct']=None
        return out

    def _fetch_stocks(self,tickers,details=False):
        query=urllib.parse.urlencode([('ticker',ticker) for ticker in tickers]+([('details','1')] if details else []))
        last=None
        for base in self._candidates():
            try:
                req=urllib.request.Request(base+'/api/stock-snapshots?'+query,
                    headers={'User-Agent':'SmartMoneyTracker/0.3','Accept':'application/json'})
                with urllib.request.urlopen(req,timeout=5) as response:raw=response.read(2_000_001)
                if len(raw)>2_000_000:raise ValueError('Stock response too large')
                data=json.loads(raw)
                if not isinstance(data,dict) or data.get('error'):raise RuntimeError('Market Flow stock endpoint unavailable')
                self._good=base;self.last_success=time.time();self.last_error=None
                return data
            except Exception as exc:  # noqa: BLE001
                last=exc
        self.last_error=type(last).__name__ if last else 'Unavailable'
        raise last or RuntimeError('No Market Flow host reachable')

    def stock_snapshots(self,tickers,details=False):
        tickers=list(dict.fromkeys(str(t or '').strip().upper().replace('.','-') for t in tickers if t))[:1000]
        now=time.monotonic();found={};needed=[]
        with self.lock:
            for ticker in tickers:
                cached=self.stock_cache.get((ticker,details))
                ttl=60 if cached and (cached[1].get('pending') or cached[1].get('fundamentals_pending') or cached[1].get('max_pain') is None) else 8*3600
                if cached and now-cached[0]<ttl:found[ticker]=cached[1]
                else:needed.append(ticker)
        failed=False
        for i in range(0,len(needed),75):
            batch=needed[i:i+75]
            try:data=self._fetch_stocks(batch,details);received=data.get('stocks',{})
            except Exception:received={};failed=True
            with self.lock:
                for ticker in batch:
                    value=received.get(ticker,{'ticker':ticker,'pending':True})
                    self.stock_cache[(ticker,details)]=(now,value);found[ticker]=value
            if failed:
                with self.lock:
                    for ticker in needed[i+75:]:
                        value={'ticker':ticker,'pending':True};self.stock_cache[(ticker,details)]=(now,value);found[ticker]=value
                break
        return found

    def status(self):
        if self.last_success:
            return {'state': 'connected', 'via': self._good,
                    'last_success': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(self.last_success)),
                    'last_error': self.last_error}
        return {'state': 'unavailable' if self.last_error else 'waiting', 'last_error': self.last_error}


def politician_score(trades):
    """0-100 score: return and win rate, shrunk toward 50 for small samples."""
    returns = []
    for trade in trades:
        value = trade.get('directional_return_pct')
        if trade.get('action') in ('Buy', 'Sell') and isinstance(value, (int, float)) and math.isfinite(value):
            returns.append(float(value))
    eligible = sum(1 for t in trades if t.get('action') in ('Buy', 'Sell'))
    if not returns:
        return {'score': 50.0, 'rated_trades': 0, 'eligible_trades': eligible, 'win_rate': None,
                'average_directional_return_pct': None,
                'status': 'waiting_for_prices' if eligible else 'no_buy_sell_trades'}
    count = len(returns); avg = sum(returns) / count; win_rate = 100 * sum(r > 0 for r in returns) / count
    raw = 50 + 0.5 * max(-50, min(50, avg)) + 0.5 * (win_rate - 50)
    reliability = count / (count + 5)
    score = max(0, min(100, 50 + (raw - 50) * reliability))
    return {'score': round(score, 1), 'rated_trades': count, 'eligible_trades': eligible,
            'win_rate': round(win_rate, 1), 'average_directional_return_pct': round(avg, 2),
            'status': 'ready' if count == eligible else 'partial'}
