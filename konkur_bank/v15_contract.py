"""PsychoPlan Konkur V15: file-only structural checks for four independent sections.

Pure validation: no Django dependency, ORM access, file modification or publication.
The runtime importer must confirm DB conflicts and publication constraints separately.
"""
from __future__ import annotations
from collections import defaultdict
from hashlib import sha256
import json
from pathlib import Path
import re
from .xlsx_bridge import parse_workbook
import zipfile
import xml.etree.ElementTree as ET
from .importer_validation import norm, ValidationError

MODES = {
    'full_exams': ('manifest', 'sources', 'questions'),
    'subjects': ('manifest', 'collections', 'questions'),
    'chapters': ('manifest', 'collections', 'questions'),
    'families': ('manifest', 'families', 'questions'),
}

OFFICIAL = ('question_id','exam_id','year','exam_body','degree','original_number','subject_code',
           'stem','option_1','option_2','option_3','option_4','correct_option','key_source',
           'lesson','correct_reason','wrong_1','wrong_2','wrong_3','wrong_4',
           'references')
AUTHORED = ('family_id','question_id','related_official_id','concept_codes','stem',
            'option_1','option_2','option_3','option_4','correct_option','lesson','correct_reason',
            'wrong_1','wrong_2','wrong_3','wrong_4','references')
HEADERS = {
    'full_exams': {'sources':('exam_id','year','exam_body','degree','expected_total_questions'),
                   'questions':OFFICIAL},
    'subjects': {'collections':('collection_id','title','exam_body','degree','subject_code','year_start','year_end'),
                 'questions':('collection_id',)+OFFICIAL},
    'chapters': {'collections':('collection_id','title','exam_body','degree','subject_code','chapter_code','topic_code'),
                 'questions':('collection_id','chapter_code','topic_code','subtopic_code')+OFFICIAL},
    'families': {'families':('family_id','public_topic','related_official_id','concept_codes'),
                 'questions':AUTHORED},
}
IDENT = re.compile(r'[A-Za-z0-9_-]{3,128}\Z')
MUST = {'stem','option_1','option_2','option_3','option_4','correct_option','lesson','correct_reason','references'}


def require(condition, message):
    if not condition:
        raise ValidationError(message)


def _sheet(grid, headers, sheetname):
    require(bool(grid),f'{sheetname}: missing header')
    h=[norm(i) for i in grid[0]]
    require(h==list(headers),f'{sheetname}: unexpected columns or order')
    result=[]
    for index,row in enumerate(grid[1:],start=2):
        require(len(row)<=len(h) or not any(norm(x) for x in row[len(h):]),f'{sheetname} row {index}: extra cells')
        cells=[norm(row[i]) if i<len(row) else '' for i in range(len(h))]
        if not any(cells): continue
        result.append(dict(zip(h,cells)))
        require(len(result)<=15000,f'{sheetname}: row limit exceeded')
    return result


def _positive_int(value,name,minimum=1,maximum=20000):
    require(str(value).isdecimal(), f'{name}: must be positive integer')
    n=int(value)
    require(minimum<=n<=maximum, f'{name}: out of range')
    return n


def _ids(rows,key,label):
    ids=[x.get(key,'') for x in rows]
    require(ids and all(IDENT.fullmatch(i) for i in ids),f'{label}: invalid or missing ID')
    require(len(ids)==len(set(ids)),f'{label}: duplicate ID')
    return set(ids)


def _reviewed(value):
    return str(value).strip().upper() in ('YES','TRUE','1')


def validate_workbook(path,expected_mode=None):
    """Fail closed. Returns counts and digest but never signals production publish permission."""
    raw=Path(path)
    require(raw.is_file() and 0<raw.stat().st_size<=20*1024*1024,'Invalid upload size')
    try:
        sheets=parse_workbook(raw)  # secured stdlib OOXML reader
    except (zipfile.BadZipFile, ET.ParseError, KeyError, IndexError, TypeError, UnicodeError, OverflowError, ValueError) as exc:
        raise ValidationError('Malformed or unsafe XLSX file') from exc
    require('manifest' in sheets,'Missing manifest')
    mf=_sheet(sheets['manifest'],('field','value','guidance'),'manifest')
    manifest={r['field']:r['value'] for r in mf}
    require(len(mf)==len(manifest),'Manifest duplicate key')
    require(manifest.get('schema')=='psychoplan-konkur-v13','Wrong schema version')
    mode=manifest.get('mode','')
    require(mode in MODES,'Unknown mode')
    require(expected_mode is None or mode==expected_mode,'File belongs to another section')
    require(set(sheets)==set(MODES[mode]),'Wrong worksheets for selected section')
    package_id=manifest.get('package_id','')
    require(bool(IDENT.fullmatch(package_id)),'Missing/invalid package ID')
    # Final approval is a single authenticated management action.
    # No repeated per-row paper checkboxes are required.
    data={name:_sheet(sheets[name],columns,name) for name,columns in HEADERS[mode].items()}
    q=data['questions']
    # Each row belongs to its OWN target collection. A question can repeat in other
    # collections/packages with different prepared chapter tags or explanations.
    # No cross-collection identity/scientific checks are performed by Django.
    multi=mode in ('subjects','chapters')
    # For a full paper, the source number and position are the local identity.
    # For authored questions, (family_id, question_id) is the local identity.
    require(bool(q),'questions: empty sheet')
    seen_memberships=set()
    for row in q:
        ident=row['question_id']
        key=row['correct_option']
        require(key in ('1','2','3','4'),f'{ident}: invalid answer key')
        require(all(row.get(col) for col in MUST),f'{ident}: incomplete question/explanation')
        require(all(row.get('wrong_'+n) for n in '1234' if n!=key),f'{ident}: missing distractor explanation')
        require(bool(IDENT.fullmatch(ident)),f'{ident}: invalid question ID')
        scope=((row['collection_id'],row['exam_id'],ident) if multi else
               (row['exam_id'],ident) if mode=='full_exams' else
               (row['family_id'],ident))
        require(scope not in seen_memberships, f'{ident}: duplicate position within the SAME collection')
        seen_memberships.add(scope)
        if mode!='families':
            require(bool(row.get('key_source')),f'{ident}: official key source missing')
            year=_positive_int(row['year'],f'{ident} year',1390,1420)
            _positive_int(row['original_number'],f'{ident} original_number',1,1000)
            require(all(row.get(k) for k in ('exam_id','exam_body','degree','subject_code')),
                    f'{ident}: source metadata missing')
            # Original source number is metadata, NOT a globally unique database ID.
    warnings=[]
    if mode=='full_exams':
        sources=data['sources'];sids=_ids(sources,'exam_id','sources')
        for source in sources:
            sid=source['exam_id']
            expected=_positive_int(source['expected_total_questions'],f'{sid} expected_total',1,1000)
            n=[r for r in q if r['exam_id']==sid]
            require(len(n)==expected,f'{sid}: incomplete paper; {len(n)}/{expected}')
            require(len(set(int(r['original_number']) for r in n))==expected and
                    set(int(r['original_number']) for r in n)==set(range(1,expected+1)),
                    f'{sid}: missing original question positions')
            require(all(r['year']==source['year'] and r['exam_body']==source['exam_body'] and
                        r['degree']==source['degree'] for r in n),f'{sid}: inconsistent source metadata')
        require({r['exam_id'] for r in q}==sids,'Questions/sources mismatch')
    elif mode in ('subjects','chapters'):
        coll=data['collections']; ids=_ids(coll,'collection_id','collections')
        counts=defaultdict(int)
        for row in q:
            cid=row['collection_id']
            require(cid in ids,f'{row["question_id"]}: unknown collection')
            counts[cid]+=1
        require(set(counts)==ids,'Empty collection')
        byid={x['collection_id']:x for x in coll}
        for row in q:
            target=byid[row['collection_id']]
            ident=row['question_id']
            require(all(row[k]==target[k] for k in ('exam_body','degree','subject_code')),
                    f'{ident}: collection mismatch')
            if mode=='subjects':
                year=_positive_int(row['year'],f'{ident} year',1390,1420)
                lo=_positive_int(target['year_start'],f'{ident} start',1390,1420)
                hi=_positive_int(target['year_end'],f'{ident} end',1390,1420)
                require(lo<=year<=hi and hi-lo<=11,f'{ident}: invalid subject year range')
            else:
                require(row['chapter_code'] and row['topic_code'], f'{ident}: chapter/topic required')
                require(all(row[k]==target[k] for k in ('chapter_code','topic_code')),
                        f'{ident}: mismatched chapter/topic')
    else:
        fam=data['families']; fids=_ids(fam,'family_id','families')
        for f in fam:
            require(all(f.get(x) for x in ('public_topic','related_official_id','concept_codes')),
                    f'{f["family_id"]}: missing public topic / official link / concepts')
            require(any(x.strip() for x in f.get('concept_codes','').split('|')),f'{f["family_id"]}: empty concept codes')
        fbyid={f['family_id']:f for f in fam}
        for row in q:
            f=fbyid.get(row['family_id'])
            require(bool(f),f'{row["question_id"]}: unknown family')
            # Links/topics are taken verbatim from the prepared workbook; never
            # look up an official question or invent concept classifications.
    require(len(q)<=15000, 'Too many rows')
    digest=sha256(raw.read_bytes()).hexdigest()
    return {'schema':'psychoplan-konkur-v13','mode':mode,'package_id':package_id,'sha256':digest,
            'question_count':len(q),'membership_count':len(q),
            'collection_count':len(data.get('collections',[])),
            'family_count':len(data.get('families',[])),
            'warnings':warnings,'preview_only':True,'db_checked':False,'publishable':False}
