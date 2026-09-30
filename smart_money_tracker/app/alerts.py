"""Opt-in Home Assistant notifications for newly stored politician filings."""
from __future__ import annotations

import logging
import os
import json
import urllib.request
from collections import defaultdict

from .core import alert_identity,estimated_amount

log=logging.getLogger('filing-alerts')
CORE_API='http://supervisor/core/api'
SUPERVISOR_TOKEN=os.environ.get('SUPERVISOR_TOKEN','')


def _money(value):
    if value is None:return 'value not stated'
    if value>=1_000_000:return f'about ${value/1_000_000:.2f}m'
    if value>=1_000:return f'about ${value/1_000:.0f}k'
    return f'about ${value:,.0f}'


class FilingAlerts:
    def __init__(self,store,service='notify.notify'):
        self.store=store
        self.service=str(service or 'notify.notify').strip()

    def status(self):
        return {'configured':bool(SUPERVISOR_TOKEN and self.service.startswith('notify.')),
                'service':self.service,'selected':sum(1 for x in self.store.alert_subscriptions() if x['enabled'])}

    def _send(self,title,message):
        if not (SUPERVISOR_TOKEN and self.service.startswith('notify.')):
            log.warning('filing alert not sent: Home Assistant notify service is not configured')
            return False
        domain,_,service=self.service.partition('.')
        try:
            body=json.dumps({'title':title,'message':message}).encode()
            request=urllib.request.Request(f'{CORE_API}/services/{domain}/{service}',data=body,method='POST',
                headers={'Authorization':f'Bearer {SUPERVISOR_TOKEN}','Content-Type':'application/json'})
            with urllib.request.urlopen(request,timeout=10) as response:
                return 200<=response.status<300
        except Exception as exc:
            log.warning('filing alert delivery failed: %s',type(exc).__name__);return False

    def send(self,title,message):
        """Shared, validated Home Assistant delivery for other opt-in filing types."""
        return self._send(title,message)

    def process(self,rows):
        """Group one successful storage batch and notify selected people once."""
        groups=defaultdict(list)
        for trade in rows or []:
            groups[(trade.get('politician',''),trade.get('disclosure_date',''))].append(trade)
        sent=0
        for (person,day),trades in groups.items():
            rule=self.store.matching_alert(person,day)
            if not rule:continue
            eligible=[t for t in trades if t.get('action') in rule['actions']]
            if not eligible:continue
            known=[estimated_amount(t) for t in eligible if estimated_amount(t) is not None]
            total=sum(known) if known else None
            if rule['min_value'] and (total is None or total<rule['min_value']):continue
            identity=alert_identity(person,day,eligible)
            if self.store.alert_delivered(identity):continue
            counts=defaultdict(int)
            for t in eligible:counts[t.get('action','Other')]+=1
            summary=' · '.join(f'{n} {action.lower()}{"s" if n!=1 else ""}' for action,n in counts.items())
            lines=[]
            for trade in eligible[:4]:
                company=str(trade.get('asset') or '').strip()
                label=f"{trade.get('ticker','')} — {company}" if company else trade.get('ticker','')
                lines.append(f"{label}: {trade.get('action','Other')} ({trade.get('amount','value not stated')})")
            if len(eligible)>4:lines.append(f'+ {len(eligible)-4} more transaction(s)')
            message=f'Disclosed {day} · {summary} · {_money(total)}\n'+'\n'.join(lines)
            if self._send(f'New politician filing — {person}',message):
                self.store.mark_alert_delivered(identity);sent+=1
        return sent

    def send_test(self,person='Selected politician'):
        return self._send(f'Test politician alert — {person}',
                          'Notifications are connected. New matching filings will appear here once collected.')
