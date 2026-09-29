"""Official House index + conservative geometry-based PTR PDF extraction."""
import csv
import hashlib
import io
import re
import zipfile
from datetime import date, timedelta
from .core import iso_date, normalise

BASE='https://disclosures-clerk.house.gov'


def parse_index(content, year, since):
    with zipfile.ZipFile(io.BytesIO(content)) as z:
        name=next(n for n in z.namelist() if n.lower().endswith('.txt'))
        if z.getinfo(name).file_size > 20_000_000: raise ValueError('Index too large')
        text=z.read(name).decode('utf-8-sig')
    out=[]
    for row in csv.DictReader(io.StringIO(text),delimiter='\t'):
        if row.get('FilingType')!='P': continue
        doc=row.get('DocID','')
        if not doc.isdigit(): continue
        filed=iso_date(row['FilingDate'])
        if filed < since: continue
        out.append(dict(id=f'{year}:{doc}',politician=' '.join(filter(None,[row.get('First'),row.get('Last'),row.get('Suffix')])),
            disclosure_date=filed,district=row.get('StateDst',''),
            source_url=f'{BASE}/public_disc/ptr-pdfs/{year}/{doc}.pdf'))
    return out


def clean_cell(text):
    lines=[]
    for line in (text or '').splitlines():
        # Metadata below the transaction can span every column in a merged row.
        # Stop at its label; never let it leak into action/ticker/amount fields.
        if '\x00' in line or re.match(r'(Filing Status|Subholding Of|Description|Location)\s*:',line.strip(),re.I):
            break
        lines.append(line)
    return ' '.join(' '.join(lines).split())


def parse_pdf(content, report):
    import pdfplumber
    rows=[]; skipped=0; ordinal=0
    with pdfplumber.open(io.BytesIO(content)) as pdf:
        if len(pdf.pages)>200: raise ValueError('Report exceeds 200 pages')
        amendment=False
        status_match=re.search(r"Status:\s*([^\n]+)",pdf.pages[0].extract_text() or "") if pdf.pages else None
        filer_status=status_match.group(1).strip() if status_match else "Not verified"
        for page in pdf.pages:
            text=(page.extract_text() or '').replace('\x00','')
            amendment=amendment or bool(re.search(r'\bamend(?:ed|ment)\b',text,re.I))
            for table in page.find_tables():
                extracted=table.extract()
                if not extracted: continue
                head=[' '.join((c or '').split()).lower() for c in extracted[0]]
                if 'asset' not in head or 'amount' not in head: continue
                header=table.rows[0].cells
                if len(header)<7 or any(c is None for c in header[:7]): continue
                for geometry in table.rows[1:]:
                    cells=[c for c in geometry.cells if c is not None]
                    if not cells: continue
                    top=min(c[1] for c in cells);bottom=max(c[3] for c in cells)
                    asset_area=page.crop((header[2][0],top,header[2][2],bottom))
                    note_tops=[c['top'] for c in asset_area.chars if '\x00' in c.get('text','')]
                    note_tops += [w['top'] for w in asset_area.extract_words() if w['text'].lower() in ('filing','subholding','description:')]
                    if note_tops and min(note_tops)>top:
                        bottom=min(bottom,min(note_tops)-0.2)
                    vals=[clean_cell(page.crop((h[0],top,h[2],bottom)).extract_text()) for h in header[:7]]
                    # Notes rows have no transaction dates. Count plausible unparsed rows.
                    if not re.search(r'\d{1,2}/\d{1,2}/\d{4}', vals[4]):
                        if vals[3].strip() in ('P','S','E'): skipped+=1
                        continue
                    ordinal+=1
                    asset=vals[2]
                    symbols=re.findall(r'\(([A-Z][A-Z0-9.\-]{0,14})\)',asset)
                    if len(set(symbols))!=1:
                        skipped+=1;continue
                    types=re.findall(r'\[([A-Z]{2})\]',asset)
                    row={**report,'ticker':symbols[0],'owner':vals[1],'asset':asset,
                         'asset_type':types[0] if types else 'Not stated','action':vals[3],
                         'filer_status':filer_status,
                         'transaction_date':vals[4],'amount':vals[6],'amendment':amendment,
                         'notes':'Automatic PDF extraction; inspect original filing. Amendments are not reconciled.'}
                    try: rows.append(normalise(row,'house_official',report['id']+':'+str(ordinal)))
                    except ValueError: skipped+=1
        # Carry document-level amendment marker to rows from earlier pages, too.
        for row in rows: row['amendment']=amendment
    return rows,skipped
