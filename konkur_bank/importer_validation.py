"""PsychoPlan: isolated offline STAGING importer (standard library only).

Not a production Django/Postgres deployment. No network or production paths.
All writes target an explicitly specified SQLite staging DB and are atomic.
"""
from __future__ import annotations
import argparse
import csv
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import sqlite3
import stat
import sys
import unicodedata
import zipfile

SUPPORTED_SCHEMA = 'psychoplan-konkur-v1'
SUPPORTED_TYPES = {'OFFICIAL_YEARLY', 'ANALYSIS_FAMILY'}
MAX_ZIP_BYTES = 30 * 1024 * 1024
MAX_UNCOMPRESSED = 36 * 1024 * 1024
MAX_MEMBER = 18 * 1024 * 1024
MAX_MEMBERS = 22
MAX_ROWS = 15000
SQLITE_APP_ID = 0x50504B42  # 'PPKB', reserved for this isolated staging database
OPTIONAL_FILES = {'README_FA.txt'}
ALLOWED = {
 'OFFICIAL_YEARLY': {'questions.csv','collections.csv','collection_items.csv','sources.csv','passages.csv','reading_items.csv'},
 'ANALYSIS_FAMILY': {'families.csv','concepts.csv','authored_questions.csv','explanations.csv','references.csv'},
}

class ValidationError(ValueError):
    pass


def norm(s):
    return unicodedata.normalize('NFKC', str(s or '')).translate(str.maketrans('۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩', '01234567890123456789')).strip()


def text_digest(*parts):
    return hashlib.sha256(json.dumps(parts,ensure_ascii=False,separators=(',',':')).encode()).hexdigest()


def require(condition, message):
    if not condition:
        raise ValidationError(message)


def load_zip(path):
    path = Path(path)
    require(path.is_file() and 0 < path.stat().st_size <= MAX_ZIP_BYTES,'ZIP not found or too large')
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    try:
        with zipfile.ZipFile(path) as z:
            entries=z.infolist()
            require(0<len(entries)<=MAX_MEMBERS, 'Too many ZIP entries')
            names=[]
            total=0
            for item in entries:
                name=item.filename
                p=PurePosixPath(name)
                require(not item.is_dir() and not p.is_absolute() and '..' not in p.parts
                        and not name.startswith('/') and not name.startswith('\\')
                        and '\\' not in name and len(p.parts)==1, 'Unsafe ZIP member '+name)
                mode=item.external_attr>>16
                require(not stat.S_ISLNK(mode),'Symlink ZIP member forbidden')
                require(name in {'manifest.json'} | OPTIONAL_FILES | set().union(*ALLOWED.values()),'Unexpected ZIP member '+name)
                require(item.file_size <= MAX_MEMBER,'Oversized member '+name)
                require(item.compress_size>0 or item.file_size==0,'Invalid ZIP member size '+name)
                require(item.file_size <= max(item.compress_size,1)*250,'Compression ratio too high '+name)
                total += item.file_size
                require(total <= MAX_UNCOMPRESSED,'ZIP content too large')
                names.append(name)
            require(len(names)==len(set(names)),'Duplicate ZIP member names')
            require('manifest.json' in names,'Missing manifest.json')
            manifest=json.loads(z.read('manifest.json').decode('utf-8-sig'))
            require(isinstance(manifest,dict),'Manifest must be an object')
            require(manifest.get('schema')==SUPPORTED_SCHEMA,'Unsupported schema')
            kind=manifest.get('package_type')
            require(kind in SUPPORTED_TYPES,'Unsupported package type')
            require(set(names)-OPTIONAL_FILES==ALLOWED[kind]|{'manifest.json'},'Missing or extra package CSV files')
            require(manifest.get('status')=='DRAFT_ONLY' and manifest.get('may_publish') is False,
                    'Staging accepts explicitly marked draft packages only')
            require(isinstance(manifest.get('expected_question_count'),int),'Invalid expected count')
            require(re.fullmatch(r'[A-Za-z0-9_-]{3,120}',str(manifest.get('package_id',''))), 'Invalid package ID')
            records={}
            for name in sorted(ALLOWED[kind]):
                data=z.read(name).decode('utf-8-sig')
                table=csv.DictReader(io.StringIO(data,newline=''))
                require(table.fieldnames and len(table.fieldnames)==len(set(table.fieldnames)), 'Bad CSV header '+name)
                rows=[]
                for row in table:
                    require(None not in row and all(v is not None for v in row.values()),'Malformed CSV '+name)
                    require(all(len(v)<=50000 for v in row.values()),'Oversize field '+name)
                    rows.append({k:norm(v) for k,v in row.items()})
                    require(len(rows)<=MAX_ROWS,'Too many CSV rows '+name)
                records[name]=rows
            return manifest,records,digest
    except (zipfile.BadZipFile,UnicodeDecodeError,json.JSONDecodeError,csv.Error,KeyError) as e:
        raise ValidationError('Unreadable or corrupt package: '+str(e)) from e


def unique_rows(rows, key, label):
    ids=[r.get(key,'') for r in rows]
    require(all(ids) and len(ids)==len(set(ids)),'Duplicate/missing '+label+' identifiers')
    return set(ids)


def validate(path):
    man,data,digest=load_zip(path)
    issues=[];warnings=[]
    kind=man['package_type']
    if kind=='OFFICIAL_YEARLY':
        src=data['sources.csv']; q=data['questions.csv']; c=data['collections.csv']
        ids=unique_rows(q,'question_id','official question')
        source_ids=unique_rows(src,'exam_id','exam source')
        col_ids=unique_rows(c,'collection_id','collection')
        passage_ids=unique_rows(data['passages.csv'],'passage_id','passage')
        require(len(q)==man['expected_question_count'],'Official row count mismatch')
        require(len(set((r.get('exam_id'),r.get('original_number')) for r in q))==len(q), 'Duplicate source position')
        require(all(r.get('exam_id') in source_ids for r in q), 'Unknown exam reference')
        for row in q:
            rid=row['question_id']
            require(re.fullmatch(r'[A-Za-z0-9_-]+',rid) is not None, 'Invalid question ID '+rid)
            require(re.fullmatch(r'[0-9]+',row.get('original_number','')) is not None,'Invalid original number '+rid)
            require(row.get('correct_option','') in ('','1','2','3','4'),'Invalid answer key '+rid)
            require(row.get('license_status') in {'VERIFIED','UNVERIFIED','RESTRICTED'},'Invalid rights status '+rid)
            fields=('stem','option_1','option_2','option_3','option_4','correct_option')
            if not all(row.get(k) for k in fields): warnings.append('Untranscribed/unkeyed draft: '+rid)
        for row in c:
            require(row.get('exam_id') in source_ids,'Collection has unknown exam')
            require(row.get('published','').lower() not in {'true','1','yes'},'Draft collection must not publish')
            if row.get('collection_type') in {'FULL','FULL_EXAM','COMPLETE'}:
                require(row.get('source_completeness')=='FULL','A partial source cannot be called a full exam')
        seen=set()
        for row in data['collection_items.csv']:
            pair=(row.get('collection_id'),row.get('question_id'))
            require(pair not in seen,'Duplicate collection-question assignment')
            seen.add(pair)
            require(pair[0] in col_ids and pair[1] in ids,'Broken collection assignment')
        for row in data['reading_items.csv']:
            require(row.get('passage_id') in passage_ids and row.get('question_id') in ids,
                    'Broken passage-question assignment')
        for row in data['passages.csv']:
            require(row.get('exam_id') in source_ids,'Passage has unknown exam')
        if any(not r.get('body') for r in data['passages.csv']):warnings.append('Empty reading passage body')
    else:
        q=data['authored_questions.csv']
        ids=unique_rows(q,'question_id','authored question')
        families=unique_rows(data['families.csv'],'family_id','family')
        concepts=unique_rows(data['concepts.csv'],'concept_id','concept')
        explanations=unique_rows(data['explanations.csv'],'question_id','explanation')
        require(len(q)==man['expected_question_count'],'Authored row count mismatch')
        require(ids==explanations,'Every authored question must have one explanation row')
        byexp={x['question_id']:x for x in data['explanations.csv']}
        for row in data['concepts.csv']:
            require(row.get('family_id') in families,'Concept references missing family')
        seen_stems=set()
        for row in q:
            rid=row['question_id']
            require(re.fullmatch(r'[A-Za-z0-9_-]+',rid) is not None,'Invalid authored question ID '+rid)
            require(row.get('family_id') in families, 'Missing family '+rid)
            linked=[x for x in row.get('concept_ids','').split('|') if x]
            require(linked and set(linked).issubset(concepts),'Missing/unknown concepts '+rid)
            require(row.get('correct_option') in {'1','2','3','4'},'Invalid answer key '+rid)
            require(all(row.get(k) for k in ('stem','option_1','option_2','option_3','option_4')),
                    'Incomplete authored item '+rid)
            stem=re.sub(r'\s+',' ',row['stem']).casefold()
            require(stem not in seen_stems,'Exact duplicate authored stem in same batch')
            seen_stems.add(stem)
            e=byexp[rid]
            if not (e.get('lesson') and e.get('correct_reason') and e.get('references') and
                    all(e.get('wrong_'+n) for n in '1234' if n!=row['correct_option'])):
                warnings.append('Incomplete scholarly explanation: '+rid)
            if row.get('review_status')!='APPROVED' or e.get('review_status')!='APPROVED':
                warnings.append('Academic review pending: '+rid)
    return {'ok':True, 'package_id':man['package_id'],'kind':kind,'digest':digest,
            'question_count':man['expected_question_count'],'warnings':warnings,
            'safe_to_publish':False,'records':data,'manifest':man}

SCHEMA=r'''
PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS batches (package_id TEXT PRIMARY KEY, sha256 TEXT NOT NULL UNIQUE, kind TEXT NOT NULL,
 imported_utc TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, publish_status TEXT NOT NULL DEFAULT 'DRAFT_ONLY');
CREATE TABLE IF NOT EXISTS source_exams (exam_id TEXT PRIMARY KEY, metadata_json TEXT NOT NULL, content_sha TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS families (family_id TEXT PRIMARY KEY, metadata_json TEXT NOT NULL, content_sha TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS concepts (concept_id TEXT PRIMARY KEY, family_id TEXT NOT NULL REFERENCES families(family_id),
 metadata_json TEXT NOT NULL, content_sha TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS passages (passage_id TEXT PRIMARY KEY, exam_id TEXT NOT NULL REFERENCES source_exams(exam_id),
 metadata_json TEXT NOT NULL, content_sha TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS questions (question_id TEXT PRIMARY KEY, origin TEXT NOT NULL CHECK(origin IN ('OFFICIAL','AUTHORED')),
 source_exam_id TEXT REFERENCES source_exams(exam_id), family_id TEXT REFERENCES families(family_id),
 metadata_json TEXT NOT NULL, content_sha TEXT NOT NULL, publish_status TEXT NOT NULL DEFAULT 'DRAFT_ONLY');
CREATE TABLE IF NOT EXISTS explanations (question_id TEXT PRIMARY KEY REFERENCES questions(question_id),
 metadata_json TEXT NOT NULL, content_sha TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS collections (collection_id TEXT PRIMARY KEY, exam_id TEXT REFERENCES source_exams(exam_id),
 metadata_json TEXT NOT NULL, content_sha TEXT NOT NULL, publish_status TEXT NOT NULL DEFAULT 'DRAFT_ONLY');
CREATE TABLE IF NOT EXISTS collection_items (collection_id TEXT NOT NULL REFERENCES collections(collection_id),
 question_id TEXT NOT NULL REFERENCES questions(question_id), position INTEGER NOT NULL,
 PRIMARY KEY (collection_id,question_id), UNIQUE(collection_id,position));
CREATE TABLE IF NOT EXISTS reading_items (passage_id TEXT NOT NULL REFERENCES passages(passage_id),
 question_id TEXT NOT NULL REFERENCES questions(question_id), position INTEGER NOT NULL,
 PRIMARY KEY (passage_id,question_id), UNIQUE(passage_id,position));
CREATE TABLE IF NOT EXISTS question_concepts (question_id TEXT NOT NULL REFERENCES questions(question_id),
 concept_id TEXT NOT NULL REFERENCES concepts(concept_id), PRIMARY KEY(question_id,concept_id));
CREATE TABLE IF NOT EXISTS revision_history (id INTEGER PRIMARY KEY AUTOINCREMENT, table_name TEXT NOT NULL, entity_id TEXT NOT NULL,
 previous_sha TEXT NOT NULL, previous_payload TEXT NOT NULL, revision_package_id TEXT NOT NULL, edited_utc TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS question_source_links (question_id TEXT NOT NULL REFERENCES questions(question_id),
 related_official_id TEXT NOT NULL, PRIMARY KEY(question_id,related_official_id));
CREATE INDEX IF NOT EXISTS idx_q_origin_status ON questions(origin,publish_status);
CREATE INDEX IF NOT EXISTS idx_ci_question ON collection_items(question_id);
'''


def insert_no_overwrite(con,table,idfield,item,other=None,allow_revision=False,revision_package_id=None):
    rid=item[idfield]
    payload=json.dumps(item,sort_keys=True,ensure_ascii=False,separators=(',',':'))
    digest=hashlib.sha256(payload.encode('utf8')).hexdigest()
    old=con.execute('SELECT content_sha FROM '+table+' WHERE '+idfield+'=?',(rid,)).fetchone()
    if old:
        if old[0] == digest:
            return False
        require(allow_revision and table in {'questions','explanations'},
                'CONFLICT: previously imported '+table+' ID changed: '+rid)
        if table == 'questions':
            saved=con.execute('SELECT metadata_json,publish_status,origin,source_exam_id,family_id FROM questions WHERE question_id=?',(rid,)).fetchone()
            require(saved[1]=='DRAFT_ONLY','Published question cannot be revised')
            require(saved[2]==(other or {}).get('origin'),'Question origin cannot change')
            require(saved[3]==(other or {}).get('source_exam_id'),'Source exam cannot change')
            require(saved[4]==(other or {}).get('family_id'),'Family cannot change')
            older=json.loads(saved[0])
            for field in ('question_id','exam_id','original_number','family_id','concept_ids','related_official_ids'):
                require(older.get(field)==item.get(field), 'Structural field changed: '+rid+' '+field)
        else:
            require(con.execute('SELECT publish_status FROM questions WHERE question_id=?',(rid,)).fetchone()[0]=='DRAFT_ONLY',
                    'Published question explanation cannot be revised')
        previous=con.execute('SELECT metadata_json,content_sha FROM '+table+' WHERE '+idfield+'=?',(rid,)).fetchone()
        con.execute('INSERT INTO revision_history(table_name,entity_id,previous_sha,previous_payload,revision_package_id) VALUES (?,?,?,?,?)',
                    (table,rid,previous[1],previous[0],revision_package_id))
        con.execute('UPDATE '+table+' SET metadata_json=?, content_sha=? WHERE '+idfield+'=?',(payload,digest,rid))
        return False
    cols=[idfield,'metadata_json','content_sha']+list((other or {}).keys())
    vals=[rid,payload,digest]+list((other or {}).values())
    con.execute('INSERT INTO '+table+' ('+','.join(cols)+') VALUES ('+','.join('?'*len(cols))+')',vals)
    return True


def import_staging(path,db_path,commit=False,allow_draft_revision=False):
    v=validate(path)
    out={k:v[k] for k in ('ok','package_id','kind','digest','question_count','warnings','safe_to_publish')}
    db=Path(db_path).expanduser().resolve()
    if not commit:
        out['mode']='DRY_RUN'; out['db_touched']=False;return out
    require(db.name.endswith('.ppstage.sqlite3'),'Use a dedicated *.ppstage.sqlite3 file (never an application DB)')
    require(not db.is_symlink(),'Staging DB path may not be a symlink')
    require('/var/www/' not in str(db) and '/etc/' not in str(db), 'Refusing production/system destination')
    require(db.parent.is_dir(),'Staging DB directory must exist')
    # Refuse to open an existing non-PsychoPlan staging DB.
    if db.exists():
        require(db.is_file(),'Staging DB target is not a regular file')
        probe=sqlite3.connect('file:'+db.as_posix()+'?mode=ro',uri=True)
        try:
            app_id=probe.execute('PRAGMA application_id').fetchone()[0]
            require(app_id==SQLITE_APP_ID,'Refusing to modify a foreign or unmarked database')
        finally:probe.close()
    # SQLite atomic commit, no modifications to existing Django/Postgres tables.
    con=sqlite3.connect(str(db),timeout=15)
    try:
        if not con.execute('PRAGMA application_id').fetchone()[0]:
            con.execute('PRAGMA application_id='+str(SQLITE_APP_ID))
        con.executescript(SCHEMA)
        con.execute('PRAGMA foreign_keys=ON')
        man=v['manifest']; data=v['records'];kind=v['kind']; pid=v['package_id']
        con.execute('BEGIN IMMEDIATE')
        old=con.execute('SELECT sha256 FROM batches WHERE package_id=?',(pid,)).fetchone()
        if old:
            require(old[0]==v['digest'],'CONFLICT: same package ID, different content')
            con.rollback();out['mode']='ALREADY_IMPORTED';out['db_touched']=False;return out
        if con.execute('SELECT package_id FROM batches WHERE sha256=?',(v['digest'],)).fetchone():
            con.rollback();out['mode']='ALREADY_IMPORTED';out['db_touched']=False;return out
        added={'questions':0,'sources':0,'collections':0,'families':0,'concepts':0,'passages':0}
        if kind=='OFFICIAL_YEARLY':
            for row in data['sources.csv']:
                added['sources']+=insert_no_overwrite(con,'source_exams','exam_id',row)
            for row in data['questions.csv']:
                added['questions']+=insert_no_overwrite(con,'questions','question_id',row,
                    {'origin':'OFFICIAL','source_exam_id':row['exam_id']},allow_draft_revision,pid)
            for row in data['passages.csv']:
                added['passages']+=insert_no_overwrite(con,'passages','passage_id',row,{'exam_id':row['exam_id']})
            for row in data['collections.csv']:
                added['collections']+=insert_no_overwrite(con,'collections','collection_id',row,{'exam_id':row['exam_id']})
            for row in data['collection_items.csv']:
                cid,qid=row['collection_id'],row['question_id'];pos=int(row['order'])
                con.execute('INSERT OR IGNORE INTO collection_items VALUES (?,?,?)',(cid,qid,pos))
                prev=con.execute('SELECT position FROM collection_items WHERE collection_id=? AND question_id=?',(cid,qid)).fetchone()
                require(prev and prev[0]==pos,'CONFLICT: question order changed')
            for row in data['reading_items.csv']:
                cid,qid=row['passage_id'],row['question_id'];pos=int(row['order'])
                con.execute('INSERT OR IGNORE INTO reading_items VALUES (?,?,?)',(cid,qid,pos))
                prev=con.execute('SELECT position FROM reading_items WHERE passage_id=? AND question_id=?',(cid,qid)).fetchone()
                require(prev and prev[0]==pos,'CONFLICT: reading order changed')
        else:
            for row in data['families.csv']:
                added['families']+=insert_no_overwrite(con,'families','family_id',row)
            for row in data['concepts.csv']:
                added['concepts']+=insert_no_overwrite(con,'concepts','concept_id',row,{'family_id':row['family_id']})
            explanations={e['question_id']:e for e in data['explanations.csv']}
            for row in data['authored_questions.csv']:
                rid=row['question_id']
                added['questions']+=insert_no_overwrite(con,'questions','question_id',row,
                    {'origin':'AUTHORED','family_id':row['family_id']},allow_draft_revision,pid)
                insert_no_overwrite(con,'explanations','question_id',explanations[rid],allow_revision=allow_draft_revision,revision_package_id=pid)
                for cid in row.get('concept_ids','').split('|'):
                    if cid:con.execute('INSERT OR IGNORE INTO question_concepts VALUES (?,?)',(rid,cid))
                for target in row.get('related_official_ids','').split('|'):
                    if target:con.execute('INSERT OR IGNORE INTO question_source_links VALUES (?,?)',(rid,target))
        con.execute('INSERT INTO batches(package_id,sha256,kind) VALUES (?,?,?)',(pid,v['digest'],kind))
        con.commit()
        out.update({'mode':'IMPORTED_STAGING_ONLY','db_touched':True,'inserted':added})
        return out
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()


def status(db_path):
    db=Path(db_path)
    require(db.is_file(),'Staging DB is not present')
    # use read-only sqlite URI; never create DB for status
    con=sqlite3.connect('file:'+db.resolve().as_posix()+'?mode=ro',uri=True)
    try:
        result={'db':str(db),'tables':{}}
        for t in ('batches','source_exams','families','concepts','passages','questions','collections','collection_items','question_concepts','revision_history'):
            result['tables'][t]=con.execute('SELECT COUNT(*) FROM '+t).fetchone()[0]
        result['published_questions']=con.execute("SELECT COUNT(*) FROM questions WHERE publish_status!='DRAFT_ONLY'").fetchone()[0]
        return result
    finally: con.close()


def main(argv=None):
    ap=argparse.ArgumentParser(description='PsychoPlan isolated bank importer; never touches live site')
    sub=ap.add_subparsers(dest='cmd',required=True)
    check=sub.add_parser('check');check.add_argument('package')
    imp=sub.add_parser('import'); imp.add_argument('package');imp.add_argument('--db',required=True)
    imp.add_argument('--commit-staging',action='store_true',help='Write only to local, isolated staging SQLite DB')
    imp.add_argument('--allow-draft-revision',action='store_true',help='Explicit opt-in: audit and revise unpublished draft content')
    st=sub.add_parser('status');st.add_argument('--db',required=True)
    args=ap.parse_args(argv)
    try:
        if args.cmd=='check':
            v=validate(args.package)
            result={k:v[k] for k in ('ok','package_id','kind','digest','question_count','warnings','safe_to_publish')}
        elif args.cmd=='import':result=import_staging(args.package,args.db,args.commit_staging)
        else:result=status(args.db)
        print(json.dumps(result,ensure_ascii=False,indent=2));return 0
    except (ValueError,sqlite3.Error,OSError) as e:
        print(json.dumps({'ok':False,'error':str(e)},ensure_ascii=False),file=sys.stderr);return 2

if __name__=='__main__':sys.exit(main())
