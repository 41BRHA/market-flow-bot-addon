"""Read-only bridge to the separate Smart Money add-on. No price downloads.

The tracker's internal Home-Assistant hostname depends on HOW it was installed:
  * Local add-on          -> local-smart-money-tracker
  * From a GitHub repo    -> <repo-prefix>-smart-money-tracker  (same prefix as
                             THIS add-on, e.g. 77bc3c25-smart-money-tracker)
So a single hardcoded default fails for repo installs. We instead try a list of
candidate hostnames — the configured one first, then one DERIVED from this
add-on's own hostname (which shares the repo prefix), then the common fallbacks —
and remember the first that answers. This makes it work regardless of install.
"""
import json
import os
import re
import urllib.parse
import urllib.request
import threading
import time

_cache = {}
_lock = threading.Lock()
_good = None            # the base_url that last worked (tried first next time)


def _candidates(configured):
    cands = []
    if configured:
        cands.append(configured.rstrip('/'))
    # derive the tracker host from our own hostname (shares the repo prefix)
    own = os.environ.get('HOSTNAME', '') or ''
    for suffix in ('-market-flow-bot', '_market_flow_bot'):
        if own.endswith(suffix):
            prefix = own[:-len(suffix)]
            if prefix:
                cands.append('http://%s-smart-money-tracker:8098' % prefix)
            break
    cands += ['http://local-smart-money-tracker:8098', 'http://smart-money-tracker:8098']
    seen, out = set(), []
    if _good:
        out.append(_good)           # last-known-good first, even when configured
        seen.add(_good)
    for c in cands:
        if c and c not in seen:
            seen.add(c); out.append(c)
    return out


def _try(base, ticker):
    url = base.rstrip('/') + '/api/trades?' + urllib.parse.urlencode(
        {'ticker': ticker, 'limit': 20, 'grouped': '1', 'market_bridge': '1'})
    with urllib.request.urlopen(url, timeout=4) as r:
        raw = r.read(1_000_001)
    if len(raw) > 1_000_000:
        raise ValueError('Response too large')
    result = json.loads(raw)
    if not isinstance(result.get('trades'), list):
        raise ValueError('Invalid tracker response')
    result['available'] = True
    return result


def get_trades(base_url, ticker):
    global _good
    ticker = str(ticker).strip().upper().replace('.', '-')
    if not re.fullmatch(r'[A-Z][A-Z0-9-]{0,14}', ticker):
        return {'available': False, 'error': 'No supported equity ticker', 'trades': []}
    with _lock:
        cached = _cache.get(ticker)
        pending = cached and any(t.get('price_pending') for t in cached[1].get('trades', []))
        if cached and time.monotonic() - cached[0] < (5 if pending else 60):
            return cached[1]
    tried = []
    result = None
    for base in _candidates(base_url):
        try:
            result = _try(base, ticker)
            _good = base                  # remember the winner
            break
        except Exception:                 # noqa: BLE001 - try the next candidate
            tried.append(base.replace('http://', '').split(':')[0])
    if result is None and cached and cached[1].get('available'):
        # A transient add-on/DNS timeout should not replace useful data with an
        # error card. Keep the last success and retry shortly on the next open.
        result = dict(cached[1]);result['stale_bridge'] = True
    elif result is None:
        result = {'available': False, 'trades': [],
                  'error': 'Smart Money Tracker not reachable. Tried: ' + ', '.join(tried)
                           + '. Check the tracker add-on is running, or set smart_money_url to its hostname.'}
    with _lock:
        if len(_cache) > 1000:
            _cache.clear()
        # Stale fallbacks get a short retry window; healthy results keep 60 s.
        stamp=time.monotonic()-55 if result.get('stale_bridge') else time.monotonic()
        _cache[ticker] = (stamp, result)
    return result
