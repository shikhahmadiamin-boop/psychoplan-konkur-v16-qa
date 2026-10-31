"""V15 staging: publish fully prepared workbooks without any server-side scientific inference.

Every question appearance has a separate *internal* ID scoped to its collection.
The official ID stays intact in payload.source_question_id. No cross-collection
comparison, deduplication or classification is performed. No schema migration.
"""
import hashlib
import json
from django.db import transaction
from django.utils import timezone
from . import models as m
from .v15_contract import validate_workbook,HEADERS,_sheet
from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory
import zipfile
from .xlsx_bridge import parse_workbook
from .importer_validation import ValidationError

MODES=('full_exams','subjects','chapters','families')
LABELS={'full_exams':'کنکور کامل','subjects':'جامع درس‌به‌درس',
        'chapters':'طبقه‌بندی فصل و مبحث','families':'تحلیل هوشمند'}



@contextmanager
def uploaded_xlsx(uploaded):
    """Bounded single-workbook ZIP/XLSX upload. Nothing persists on server."""
    if Path(uploaded.name).suffix.lower() not in ('.xlsx','.zip'):
        raise ValidationError('Only XLSX or single-XLSX ZIP files accepted')
    if uploaded.size is None or uploaded.size<=0 or uploaded.size>21*1024*1024:
        raise ValidationError('Upload size exceeds safe limit')
    with TemporaryDirectory(prefix='pp_konkur_v15_') as dirname:
        output=Path(dirname)/'payload.xlsx'
        incoming=Path(dirname)/'upload.zip' if uploaded.name.lower().endswith('.zip') else output
        size=0
        with incoming.open('xb') as f:
            for part in uploaded.chunks(chunk_size=262144):
                size+=len(part)
                if size>21*1024*1024:raise ValidationError('Upload exceeds limit')
                f.write(part)
        if incoming != output:
            try:
                with zipfile.ZipFile(incoming) as z:
                    entries=z.infolist()
                    if len(entries)!=1 or entries[0].is_dir() or Path(entries[0].filename).name!=entries[0].filename or not entries[0].filename.lower().endswith('.xlsx'):
                        raise ValidationError('ZIP must contain a single root-level XLSX')
                    if entries[0].file_size>20*1024*1024:
                        raise ValidationError('Expanded workbook exceeds 20 MB')
                    with z.open(entries[0]) as read, output.open('xb') as write:
                        total=0
                        while True:
                            part=read.read(262144)
                            if not part:break
                            total+=len(part)
                            if total>20*1024*1024:raise ValidationError('Expansion limit exceeded')
                            write.write(part)
            except (zipfile.BadZipFile,RuntimeError,EOFError) as exc:
                raise ValidationError('Invalid ZIP archive') from exc
        yield output


def stable_id(prefix,*parts):
    """Short deterministic technical PK; never infer scientific sameness from text."""
    material=json.dumps(parts,ensure_ascii=False,separators=(',',':')).encode('utf-8')
    return prefix+hashlib.sha256(material).hexdigest()[:48]


def digest(data):
    return hashlib.sha256(json.dumps(data,sort_keys=True,ensure_ascii=False,separators=(',',':')).encode()).hexdigest()


def require_manager(actor):
    if not (actor.is_authenticated and actor.is_active and actor.is_staff and actor.is_superuser):
        raise ValidationError('Admin permission required')


def _source_id(package_id,scope,exam_id):
    return stable_id('V15S-',package_id,scope,exam_id)


def _save_source(package_id,scope,row,expected=None):
    original=row['exam_id']
    payload={'exam_id':original,'year':row['year'],'exam_body':row['exam_body'],
             'degree':row['degree'],'v15_package_id':package_id,'v15_scope':scope}
    if expected is not None:payload['expected_total_questions']=expected
    return m.ExamSource.objects.create(exam_id=_source_id(package_id,scope,original),
                                       metadata=payload,sha256=digest(payload))


def _save_question(package_id,scope,row,origin,source=None,family=None):
    pk=stable_id('V15Q-',package_id,scope,
                 (source.metadata or {}).get('exam_id','') if origin=='OFFICIAL' else '',
                 row['question_id'])
    payload=dict(row)
    payload['source_question_id']=row['question_id']
    payload['v15_package_id']=package_id
    payload['v15_scope']=scope
    # Pre-approved authored content is copied verbatim, never scientifically judged here.
    q=m.BankQuestion.objects.create(question_id=pk,origin=origin,exam_source=source,
        family=family,payload=payload,sha256=digest(payload),is_published=True,
        review_status='APPROVED',rights_status='UNVERIFIED')
    explanation={k:row.get(k,'') for k in
       ('lesson','correct_reason','wrong_1','wrong_2','wrong_3','wrong_4','references')}
    explanation['review_status']='APPROVED'
    m.QuestionExplanation.objects.create(question=q,payload=explanation,sha256=digest(explanation))
    return q


def _save_collection(package_id,mode,original_id,details):
    cid=stable_id('V15C-',package_id,mode,original_id)
    metadata=dict(details)
    metadata.update(v15_mode=mode,v15_package_id=package_id,
                    source_collection_id=original_id)
    return m.BankCollection.objects.create(collection_id=cid,metadata=metadata,
                                            sha256=digest(metadata),is_published=False)


def publish(path,mode,actor):
    """One admin click: atomic save and publish of exactly the supplied rows."""
    require_manager(actor)
    preview=validate_workbook(path,expected_mode=mode)
    if preview['question_count']>2000 or preview['membership_count']>4000:
        raise ValidationError('Maximum 2000 question rows / 4000 memberships per package')
    grids=parse_workbook(path)
    data={name:_sheet(grids[name],headers,name) for name,headers in HEADERS[mode].items()}
    package_id=preview['package_id']
    # Backend is a recording engine, never a science/category/reuse engine.
    with transaction.atomic():
        batch=m.ImportBatch.objects.select_for_update().filter(pk=package_id).first()
        if batch:
            if batch.sha256==preview['sha256'] and batch.kind=='V15_'+mode.upper():
                return {'status':'ALREADY_PUBLISHED','package_id':package_id}
            raise ValidationError('Package identifier already used by another upload')
        if m.ImportBatch.objects.filter(sha256=preview['sha256']).exists():
            raise ValidationError('This identical uploaded file was already processed')
        if mode=='families':
            family_rows={row['family_id']:row for row in data['families']}
            families={}
            for raw_id,row in family_rows.items():
                payload={'v15_mode':mode,'v15_package_id':package_id,
                         'source_family_id':raw_id,'public_topic':row['public_topic'],
                         'related_official_id':row['related_official_id'],
                         'concept_codes':row['concept_codes'],'review_status':'APPROVED'}
                families[raw_id]=m.QuestionFamily.objects.create(
                    family_id=stable_id('V15F-',package_id,raw_id),
                    metadata=payload,sha256=digest(payload))
            for row in data['questions']:
                fid=row['family_id'];q=_save_question(package_id,fid,row,'AUTHORED',family=families[fid])
                # A copied literal relationship; it does not cross-check the official bank.
                m.QuestionSourceLink.objects.create(question=q,related_official_id=row['related_official_id'])
        else:
            raw_sources={row['exam_id']:row for row in data.get('sources',[])}
            collections={}
            if mode=='full_exams':
                for raw_id,row in raw_sources.items():
                    collections[raw_id]=_save_collection(package_id,mode,raw_id,
                        {'exam_id':raw_id,'exam_body':row['exam_body'],
                         'degree':row['degree'],'year':row['year'],
                         'expected_total_questions':int(row['expected_total_questions'])})
            else:
                for row in data['collections']:
                    cid=row['collection_id']
                    collections[cid]=_save_collection(package_id,mode,cid,row)
            sources={}
            positions={cid:0 for cid in collections}
            for row in data['questions']:
                cid=row['exam_id'] if mode=='full_exams' else row['collection_id']
                source_key=(cid,row['exam_id'])
                if source_key not in sources:
                    source_payload=dict(row)
                    expected=raw_sources[row['exam_id']]['expected_total_questions'] if mode=='full_exams' else None
                    sources[source_key]=_save_source(package_id,cid,source_payload,expected)
                q=_save_question(package_id,cid,row,'OFFICIAL',source=sources[source_key])
                if mode=='chapters':
                    # Copy codes from XLSX verbatim; no inference or review on server.
                    m.QuestionClassification.objects.create(
                        question=q,chapter_code=row['chapter_code'],topic_code=row['topic_code'],
                        subtopic_code=row.get('subtopic_code',''),status='APPROVED',
                        reviewed_by=actor,reviewed_at=timezone.now())
                positions[cid]+=1
                position=int(row['original_number']) if mode=='full_exams' else positions[cid]
                m.CollectionItem.objects.create(collection=collections[cid],question=q,position=position)
            for coll in collections.values():
                coll.is_published=True
                coll.save(update_fields=['is_published'])
        m.ImportBatch.objects.create(package_id=package_id,sha256=preview['sha256'],
                                     kind='V15_'+mode.upper(),approved_for_publish=True)
    return {'status':'PUBLISHED','package_id':package_id,'questions':preview['question_count'],
            'collections':preview['collection_count']}


def toggle_publication(mode,record_id,actor,enabled):
    require_manager(actor)
    if mode not in MODES or not record_id or len(record_id)>128:raise ValidationError('Invalid record')
    with transaction.atomic():
        if mode=='families':
            f=m.QuestionFamily.objects.select_for_update().filter(pk=record_id,metadata__v15_mode=mode).first()
            if f is None:raise ValidationError('Unknown family')
            # Family question records are isolated to this family, never shared with others.
            m.BankQuestion.objects.filter(family=f).update(is_published=bool(enabled))
        else:
            c=m.BankCollection.objects.select_for_update().filter(pk=record_id,metadata__v15_mode=mode).first()
            if c is None:raise ValidationError('Unknown collection')
            c.is_published=bool(enabled)
            c.save(update_fields=['is_published'])
    return {'record_id':record_id,'enabled':bool(enabled)}

# Direct edits affect only the selected physical occurrence, never related packages.
EDITABLE_QUESTION_FIELDS = ('stem','option_1','option_2','option_3','option_4','correct_option')
EDITABLE_EXPLANATION_FIELDS = ('lesson','correct_reason','wrong_1','wrong_2','wrong_3','wrong_4','references')


def _owned_question(mode, collection_id, question_id, lock=False):
    if mode == 'families':
        family = m.QuestionFamily.objects.filter(
            pk=collection_id, metadata__v15_mode='families').first()
        if not family:
            raise ValidationError('Family not found')
        qs = m.BankQuestion.objects.filter(pk=question_id, family=family, origin='AUTHORED')
    else:
        owned = m.BankCollection.objects.filter(
            pk=collection_id, metadata__v15_mode=mode).exists()
        if not owned:
            raise ValidationError('Collection not found')
        qs = m.BankQuestion.objects.filter(pk=question_id,
                        collectionitem__collection_id=collection_id, origin='OFFICIAL')
    if lock: qs=qs.select_for_update()
    q=qs.select_related('explanation').first()
    if q is None:
        raise ValidationError('Question does not belong to this collection')
    return q


def get_editable_question(mode, collection_id, question_id):
    q=_owned_question(mode,collection_id,question_id)
    return q


def edit_question(mode, collection_id, question_id, fields, previous_sha, actor):
    """Optimistic concurrency, atomic edits, revision history, frozen old attempts."""
    require_manager(actor)
    if mode not in MODES: raise ValidationError('Invalid mode')
    if any(len(str(v))>10000 for v in fields.values()):
        raise ValidationError('Text field too large')
    with transaction.atomic():
        q=_owned_question(mode,collection_id,question_id,lock=True)
        if q.sha256!=previous_sha:
            raise ValidationError('Question changed since you opened it; refresh before editing')
        data=dict(q.payload or {})
        exp_obj=m.QuestionExplanation.objects.select_for_update().filter(question=q).first()
        if exp_obj is None:raise ValidationError('Missing explanation record')
        explanation=dict(exp_obj.payload or {})
        for field in EDITABLE_QUESTION_FIELDS:
            if field in fields:data[field]=str(fields[field]).strip()
        for field in EDITABLE_EXPLANATION_FIELDS:
            if field in fields:explanation[field]=str(fields[field]).strip()
        key=data['correct_option']
        if key not in ('1','2','3','4') or not all(data.get(k) for k in EDITABLE_QUESTION_FIELDS):
            raise ValidationError('Incomplete question or invalid correct option')
        if not all(explanation.get(k) for k in ('lesson','correct_reason','references')):
            raise ValidationError('Incomplete explanation')
        if any(not explanation.get('wrong_'+str(n)) for n in range(1,5) if str(n)!=key):
            raise ValidationError('Explanations for incorrect options are required')
        new_sha=digest(data);ex_sha=digest(explanation)
        if new_sha!=q.sha256:
            m.QuestionRevision.objects.create(question=q,previous_sha=q.sha256,
                previous_payload=q.payload,source_package_id='V15-DIRECT-EDIT')
            q.payload=data;q.sha256=new_sha;q.save(update_fields=['payload','sha256','updated_at'])
        if ex_sha!=exp_obj.sha256:
            m.ExplanationRevision.objects.create(question=q,previous_sha=exp_obj.sha256,
                previous_payload=exp_obj.payload,source_package_id='V15-DIRECT-EDIT')
            exp_obj.payload=explanation;exp_obj.sha256=ex_sha
            exp_obj.save(update_fields=['payload','sha256'])
    return {'question_id':question_id,'status':'UPDATED'}


def edit_collection_title(mode,record_id,title,actor):
    require_manager(actor)
    title=str(title).strip()
    if not 1<=len(title)<=200:raise ValidationError('Title must be 1-200 characters')
    with transaction.atomic():
        if mode=='families':
            f=m.QuestionFamily.objects.select_for_update().filter(
                 pk=record_id,metadata__v15_mode='families').first()
            if f is None:raise ValidationError('Unknown family')
            metadata=dict(f.metadata or {});metadata['public_topic']=title
            f.metadata=metadata;f.sha256=digest(metadata);f.save(update_fields=['metadata','sha256'])
        elif mode in ('full_exams','subjects','chapters'):
            c=m.BankCollection.objects.select_for_update().filter(
                 pk=record_id,metadata__v15_mode=mode).first()
            if c is None:raise ValidationError('Unknown collection')
            metadata=dict(c.metadata or {});metadata['title']=title
            c.metadata=metadata;c.sha256=digest(metadata);c.save(update_fields=['metadata','sha256'])
        else:raise ValidationError('Invalid mode')
    return {'record_id':record_id,'status':'UPDATED'}
