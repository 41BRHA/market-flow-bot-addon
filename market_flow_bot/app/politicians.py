"""Read-only bridge to the separate Smart Money add-on. No price downloads."""
import json
import re
import urllib.parse
import urllib.request
import threading
import time

_cache={};_lock=threading.Lock()


def get_trades(base_url,ticker):
    ticker=str(ticker).strip().upper().replace('.', '-')
    if not re.fullmatch(r'[A-Z][A-Z0-9-]{0,14}',ticker):
        return {'available':False,'error':'No supported equity ticker','trades':[]}
    if not base_url:
        return {'available':False,'error':'Smart Money URL is not configured','trades':[]}
    key=(base_url,ticker)
    with _lock:
        cached=_cache.get(key)
        if cached and time.monotonic()-cached[0]<60: return cached[1]
    url=base_url.rstrip('/')+'/api/trades?'+urllib.parse.urlencode({'ticker':ticker,'limit':20})
    try:
        with urllib.request.urlopen(url,timeout=4) as r:
            raw=r.read(1_000_001)
        if len(raw)>1_000_000: raise ValueError('Response too large')
        result=json.loads(raw)
        if not isinstance(result.get('trades'),list): raise ValueError('Invalid tracker response')
        result['available']=True
    except Exception:
        result={'available':False,'error':'Smart Money Tracker unavailable. Check that the add-on is running and smart_money_url is correct.','trades':[]}
    with _lock:
        if len(_cache)>1000:_cache.clear()
        _cache[key]=(time.monotonic(),result)
    return result
