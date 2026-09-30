"""Current congressional roles from official House and Senate XML sources."""
import json
import os
import re
import threading
import time
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

HOUSE_URL='https://clerk.house.gov/xml/lists/memberdata.xml'
SENATE_URL='https://www.senate.gov/legislative/LIS_MEMBER/cvc_member_data.xml'

AREA_RULES=[
 ('armed services','defence policy and military oversight'),('appropriations','federal spending and departmental budgets'),
 ('financial services','banking, capital markets, insurance and housing'),('banking, housing','banking, financial markets and housing'),
 ('energy and commerce','energy, telecommunications, healthcare and consumer protection'),('energy and natural resources','energy, mining and public lands'),
 ('agriculture','agriculture, food policy and commodity markets'),('intelligence','intelligence agencies and national security'),
 ('homeland security','homeland security, border policy and cybersecurity'),('science, space','science, aerospace and technology policy'),
 ('commerce, science','commerce, transport, communications and technology'),('transportation and infrastructure','transport and infrastructure'),
 ('ways and means','taxation, trade, Social Security and Medicare'),('finance','taxation, trade and federal health programmes'),
 ('judiciary','courts, antitrust, immigration and intellectual property'),('foreign','foreign policy, diplomacy and international security'),
 ('environment','environmental policy and public works'),('health, education','health, education, labour and pensions'),
 ('budget','federal budget policy'),('veterans','veterans policy and services'),('small business','small-business policy'),
]

def _norm(value):return re.sub(r'[^a-z0-9]','',str(value or '').lower())
def _text(node,path):
    found=node.find(path)
    return (found.text or '').strip() if found is not None else ''
def _download(url):
    req=urllib.request.Request(url,headers={'User-Agent':'SmartMoneyTracker/0.2 (+Home Assistant)','Accept':'application/xml,text/xml'})
    with urllib.request.urlopen(req,timeout=30) as response:data=response.read(6_000_001)
    if len(data)>6_000_000:raise ValueError('Profile source too large')
    return data

def _areas(committees):
    out=[]
    for committee in committees:
        lower=committee['name'].lower()
        for needle,area in AREA_RULES:
            if needle in lower and area not in out:out.append(area)
    return out[:5]

def _house(data):
    root=ET.fromstring(data);names={}
    for committee in root.findall('./committees/committee'):
        code=committee.attrib.get('comcode','');name=_text(committee,'committee-fullname')
        if code and name:names[code]=name
    profiles=[]
    for member in root.findall('./members/member'):
        info=member.find('member-info');assign=member.find('committee-assignments')
        if info is None:continue
        committees=[]
        for item in (assign.findall('committee') if assign is not None else []):
            name=names.get(item.attrib.get('comcode',''),item.attrib.get('comcode',''))
            if name:committees.append({'name':name,'leadership':item.attrib.get('leadership','')})
        state=info.find('state');state_name=_text(info,'state/state-fullname');district=_text(info,'district')
        party={'R':'Republican','D':'Democrat','I':'Independent'}.get(_text(info,'party'),_text(info,'party'))
        official=_text(info,'official-name');bioguide=_text(info,'bioguideID');seat=_text(member,'statedistrict')
        if not official:continue  # vacant seat placeholders have no member identity
        profile={'name':official,'chamber':'House','party':party,'state':state_name,'state_code':state.attrib.get('postal-code','') if state is not None else '',
                 'district':district,'seat':seat,'bioguide_id':bioguide,'committees':committees,'responsibilities':_areas(committees),
                 'leadership':[c['leadership']+' — '+c['name'] for c in committees if c['leadership']],
                 'source_url':f'https://clerk.house.gov/members/{bioguide}' if bioguide else HOUSE_URL,'current':True}
        profile['title']='U.S. Representative · '+party+' · '+state_name+(' · '+district+' district' if district and district!='At Large' else ' · At Large')
        profiles.append(profile)
    return profiles,root.attrib.get('publish-date','')

def _senate(data):
    root=ET.fromstring(data);profiles=[]
    for member in root.findall('./senator'):
        first=_text(member,'name/first');last=_text(member,'name/last');suffix=_text(member,'name/suffix')
        official=' '.join(x for x in (first,last,suffix) if x).strip();party={'R':'Republican','D':'Democrat','I':'Independent'}.get(_text(member,'party'),_text(member,'party'))
        committees=[]
        for item in member.findall('./committees/committee'):
            name=(item.text or '').strip()
            if name:committees.append({'name':name,'leadership':item.attrib.get('position','')})
        leadership=_text(member,'leadership_position')
        profile={'name':official,'chamber':'Senate','party':party,'state':_text(member,'state'),'state_code':_text(member,'state'),
                 'district':'','seat':'','bioguide_id':_text(member,'bioguideId'),'committees':committees,'responsibilities':_areas(committees),
                 'leadership':([leadership] if leadership else [])+[c['leadership']+' — '+c['name'] for c in committees if c['leadership']],
                 'source_url':SENATE_URL,'current':True}
        profile['title']='U.S. Senator · '+party+' · '+profile['state_code']
        profiles.append(profile)
    return profiles,_text(root,'lastUpdate/date')

class ProfileService:
    def __init__(self,path=None):
        base=Path('/data') if os.path.isdir('/data') else Path(__file__).parent
        self.path=Path(path) if path else base/'politician_profiles.json';self.lock=threading.RLock();self.profiles=[];self.meta={};self.inflight=False
        try:
            saved=json.loads(self.path.read_text());self.profiles=saved.get('profiles',[]);self.meta=saved.get('meta',{})
        except Exception:pass
        self.refresh_if_needed()

    def refresh_if_needed(self):
        with self.lock:
            if self.inflight or time.time()-self.meta.get('updated_epoch',0)<86400:return
            self.inflight=True
        threading.Thread(target=self._refresh,daemon=True).start()

    def _refresh(self):
        errors=[];profiles=[];source_dates={}
        for chamber,url,parser in [('House',HOUSE_URL,_house),('Senate',SENATE_URL,_senate)]:
            try:
                got,published=parser(_download(url));profiles.extend(got);source_dates[chamber]=published
            except Exception as exc:errors.append(chamber+': '+type(exc).__name__)
        with self.lock:
            if profiles:self.profiles=profiles
            self.meta={'updated_epoch':time.time(),'updated_at':time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime()),'source_dates':source_dates,'errors':errors}
            tmp=self.path.with_suffix('.tmp')
            try:tmp.write_text(json.dumps({'profiles':self.profiles,'meta':self.meta}));os.replace(tmp,self.path)
            except Exception:pass
            self.inflight=False

    def get(self,person,chamber,district=''):
        self.refresh_if_needed()
        if chamber=='Executive' and _norm(person) in ('donaldjtrump','donaldtrump'):
            return {'name':person,'chamber':'Executive','current':True,
                    'title':'President of the United States · Executive Branch',
                    'committees':[],'responsibilities':['executive policy and federal administration'],
                    'leadership':['President of the United States'],
                    'source_url':'https://www.oge.gov/web/oge.nsf/Officials%20Individual%20Disclosures%20Search%20Collection?OpenForm='}
        with self.lock:profiles=list(self.profiles);meta=dict(self.meta);pending=self.inflight
        candidates=[p for p in profiles if p.get('chamber')==chamber]
        exact=next((p for p in candidates if _norm(p.get('name'))==_norm(person)),None)
        if not exact:
            words=re.findall(r'[A-Za-z]+',person or '')
            if words:
                surname=_norm(words[-1]);initial=_norm(words[0])[:1]
                matches=[p for p in candidates if p.get('name') and _norm(p.get('name','').split()[-1])==surname and _norm(p.get('name'))[:1]==initial]
                if chamber=='House' and district:
                    same_seat=[p for p in matches if _norm(p.get('seat'))==_norm(district)]
                    if same_seat:matches=same_seat
                if len(matches)==1:exact=matches[0]
        if exact:return {**exact,'profile_as_of':meta.get('updated_at'),'profile_pending':pending}
        return {'name':person,'chamber':chamber,'current':False,'profile_pending':pending,'profile_as_of':meta.get('updated_at'),
                'title':'Current official profile not matched','committees':[],'responsibilities':[],'leadership':[]}

    def status(self):
        with self.lock:return {**self.meta,'pending':self.inflight,'profile_count':len(self.profiles)}
