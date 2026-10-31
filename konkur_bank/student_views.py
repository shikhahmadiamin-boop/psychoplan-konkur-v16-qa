"""Approved-only student catalog for the already installed `konkur_bank` schema.

No answer keys, internal subtopics, explanation data, or draft contents are ever
included in this response. This is separate from the Django Admin catalog.
"""
from collections import defaultdict
import hashlib
import hmac
from django.conf import settings
from .models import QuestionFamily
from rest_framework.response import Response
from .models import BankQuestion


def public_family_token(family_id):
    """Do not expose internal family IDs, which may encode answer-specific details."""
    key=settings.SECRET_KEY.encode('utf-8')
    return hmac.new(key,('pp-kb-family-v1:'+str(family_id)).encode('utf-8'),hashlib.sha256).hexdigest()[:40]


def resolve_public_family_token(token):
    if not isinstance(token,str) or len(token)!=40:
        raise ValueError('Unknown family')
    for f in QuestionFamily.objects.filter(metadata__review_status='APPROVED').only('family_id'):
        if hmac.compare_digest(public_family_token(f.family_id),token):
            return f.family_id
    raise ValueError('Unknown family')


def _public_rows():
    rows = (BankQuestion.objects.filter(is_published=True, review_status='APPROVED')
            .select_related('exam_source','family','classification','explanation')
            .order_by('question_id'))
    for q in rows.iterator(chunk_size=250):
        payload = q.payload or {}
        if payload.get('v15_package_id'):
            continue  # New collections must never enter legacy group inference.
        if (payload.get('correct_option') not in ('1','2','3','4',1,2,3,4)
            or not all(str(payload.get(k) or '').strip()
                       for k in ('stem','option_1','option_2','option_3','option_4'))):
            continue
        if q.origin == 'OFFICIAL':
            if q.rights_status != 'VERIFIED' or not q.exam_source_id:
                continue
            c = getattr(q,'classification',None)
            if not c or c.status!='APPROVED' or not c.chapter_code or not c.topic_code:
                continue
            source=q.exam_source.metadata or {}
            if not all(source.get(k) for k in ('year','exam_body','degree')):
                continue
            try:
                year=int(source['year']);pos=int(payload.get('original_number') or 0)
            except (TypeError,ValueError):continue
            if not (1390<=year<=1420) or pos<1:continue
            yield ('OFFICIAL',q,source,c,year,pos)
        elif q.origin == 'AUTHORED':
            f=getattr(q,'family',None)
            e=getattr(q,'explanation',None)
            if not f or (f.metadata or {}).get('review_status')!='APPROVED':continue
            exp=(e.payload or {}) if e else {}
            if exp.get('review_status')!='APPROVED':continue
            if not all(str(exp.get(k) or '').strip() for k in ('lesson','correct_reason','references')):continue
            try:correct=int(payload['correct_option'])
            except (ValueError,TypeError):continue
            if any(not str(exp.get('wrong_'+str(i)) or '').strip() for i in (1,2,3,4) if i!=correct):continue
            if not str((f.metadata or {}).get('public_topic') or '').strip():continue
            yield ('AUTHORED',q,f.metadata,None,0,0)


def build_catalog(rows):
    """Pure aggregation so it can be tested without Django and without DB writes."""
    chapters=defaultdict(list);subjects=defaultdict(list)
    full=defaultdict(list);families=defaultdict(list)
    source_meta={}
    for kind,q,meta,c,year,pos in rows:
        if kind=='AUTHORED':
            families[(public_family_token(q.family_id),str(meta.get('public_topic')))].append(q.question_id)
            continue
        body=str(meta['exam_body']);degree=str(meta['degree']);subject=str(q.payload.get('subject_code') or '')
        if not subject:continue
        chapters[(body,degree,subject,c.chapter_code,c.topic_code)].append((year,q.question_id))
        subjects[(body,degree,subject)].append((year,q.question_id))
        full[q.exam_source_id].append((pos,q.question_id))
        source_meta[q.exam_source_id]=meta
    chapters_out=[];subjects_out=[];full_out=[];family_out=[]
    for (body,degree,subject,chapter,topic),items in sorted(chapters.items()):
        top=max(y for y,_ in items);lo=max(1390,top-11)
        inside=[(y,q) for y,q in items if y>=lo]
        chapters_out.append(dict(exam_body=body,degree=degree,subject_code=subject,
              chapter=chapter,topic=topic,year_start=lo,year_end=top,
              years_present=len({y for y,_ in inside}),question_count=len(inside)))
    for (body,degree,subject),items in sorted(subjects.items()):
        top=max(y for y,_ in items);lo=max(1390,top-11)
        inside=[(y,q) for y,q in items if y>=lo]
        subjects_out.append(dict(exam_body=body,degree=degree,subject_code=subject,
              year_start=lo,year_end=top,years_present=len({y for y,_ in inside}),
              question_count=len(inside)))
    for exam_id,items in sorted(full.items()):
        meta=source_meta[exam_id]
        try: expected=int(meta['expected_total_questions'])
        except (KeyError,ValueError,TypeError):continue
        positions=[p for p,_ in items]
        # A selected-subject-only paper is never presented as the complete paper.
        if expected<=0 or len(items)!=expected or set(positions)!=set(range(1,expected+1)):
            continue
        full_out.append(dict(exam_id=exam_id,year=int(meta['year']),
                   exam_body=meta['exam_body'],degree=meta['degree'],question_count=len(items)))
    for (family_id,label),ids in sorted(families.items()):
        family_out.append(dict(family_id=family_id,label=label,question_count=len(ids)))
    return dict(chapters=chapters_out,subjects=subjects_out,
                full_exams=full_out,families=family_out)


def student_catalog(request):
    if getattr(settings,'PP_KONKUR_STUDENT_CATALOG_ENABLED',False) is not True:
        return Response({'enabled':False,'modes':build_catalog(())})
    # No secret fields ever leave this endpoint; auth is enforced by the caller.
    from .v15_flags import portal_enabled
    catalog = build_catalog(_public_rows())
    if portal_enabled():
        from .v15_student import merge_v15_catalog
        # Explicit opt-in: existing public frontend response is unchanged until
        # a separately verified frontend update sends this query value.
        independent = request.query_params.get('collection_mode') == 'independent_v15'
        catalog = merge_v15_catalog(catalog, public_family_token, independent=independent)
    return Response({'enabled':True,'modes':catalog})
