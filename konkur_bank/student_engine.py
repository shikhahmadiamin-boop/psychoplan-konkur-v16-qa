"""Student-taking candidate. Requires 0002 migration and explicit disabled-by-default flags.

No existing weekly exam tables are mutated. A server-only question snapshot freezes
stems/keys/explanations at start, protecting historical reports from later edits.
"""
import hashlib
import json
from datetime import timedelta
from django.conf import settings
from django.db import transaction
from django.utils import timezone
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from .models import BankAttempt, BankQuestion, ReadingItem, QuestionExplanation
from .scoring import QuestionSnapshot, grade, safe_report
from .student_views import student_catalog, resolve_public_family_token
from .bookmark_core import ordered_marked
from .bookmark_fsrs import sync_after_finish, bookmark_summary

MAX_PAPER = 450
ALLOWED_MODES = {'chapters','subjects','full_exams','families'}


def enabled():
    return getattr(settings,'PP_KONKUR_STUDENT_TAKING_ENABLED',False) is True


def validate_selection(mode, selection):
    """Validate untrusted selectors BEFORE entitlement lookup or ORM access.

    Chapter and subject selectors both carry the catalog's 12-year window;
    otherwise the listed count could disagree with what the learner receives.
    """
    if type(selection) is not dict or mode not in ALLOWED_MODES:
        raise ValueError('Invalid mode or selector')
    allowed = ({'exam_body','degree','subject_code','chapter','topic','year_start','year_end'} if mode == 'chapters' else
               {'exam_body','degree','subject_code','year_start','year_end'} if mode == 'subjects' else
               {'exam_id'} if mode == 'full_exams' else {'family_id'})
    has_collection_id = 'collection_id' in selection
    # New selectors carry the immutable collection identity. Legacy selectors
    # without it remain supported for existing student clients/saved attempts.
    if mode in ('subjects','chapters') and has_collection_id:
        allowed = allowed | {'collection_id'}
    if set(selection) != allowed:
        raise ValueError('Unexpected selector fields')
    for k, v in selection.items():
        if k in ('year_start','year_end'):
            if type(v) is not int:
                raise ValueError('Invalid year range')
        elif not isinstance(v, str) or not v.strip() or len(v) > 128:
            raise ValueError('Invalid selector value')
    if mode in ('chapters','subjects'):
        lo,hi=selection['year_start'],selection['year_end']
        if lo < 1390 or hi > 1420 or not (lo <= hi <= lo+11):
            raise ValueError('Invalid year range')
    return True


def eligible_questions(mode, selection):
    validate_selection(mode, selection)
    from .v15_flags import portal_enabled
    if mode in ('subjects', 'chapters') and 'collection_id' in selection and not portal_enabled():
        raise ValueError('مجموعه انتخابی در دسترس نیست.')
    if portal_enabled():
        from .v15_student import independent_eligible_questions
        explicit=independent_eligible_questions(mode, selection, max_paper=MAX_PAPER)
        if explicit is not None:
            return explicit
    qs=BankQuestion.objects.filter(is_published=True,review_status='APPROVED')
    if mode=='families':
        qs=qs.filter(origin='AUTHORED',family_id=resolve_public_family_token(selection['family_id']),
            family__metadata__review_status='APPROVED',
            explanation__payload__review_status='APPROVED').select_related('family','explanation')
    else:
        qs=qs.filter(origin='OFFICIAL',rights_status='VERIFIED',
            exam_source__metadata__exam_body=selection['exam_body'] if mode!='full_exams' else None) if mode!='full_exams' else qs.filter(origin='OFFICIAL',rights_status='VERIFIED',exam_source_id=selection['exam_id'])
        if mode!='full_exams':
            qs=qs.filter(exam_source__metadata__degree=selection['degree'],payload__subject_code=selection['subject_code'])
        qs=qs.filter(classification__status='APPROVED').select_related('exam_source','classification')
        qs=qs.exclude(classification__chapter_code='').exclude(classification__topic_code='')
        if mode=='chapters':
            qs=qs.filter(classification__chapter_code=selection['chapter'],classification__topic_code=selection['topic'])
    result=[]
    for q in qs.iterator(chunk_size=250):
        source=q.exam_source.metadata if q.exam_source_id else {}
        source=source or {}
        if mode!='families' and not all(source.get(k) for k in ('year','exam_body','degree')):
            continue
        if mode in ('subjects','chapters'):
            try: year=int(source.get('year'))
            except (ValueError,TypeError): continue
            if not(selection['year_start']<=year<=selection['year_end']):continue
        payload=q.payload or {}
        # Draft placeholders are never a takeable exam.
        if payload.get('correct_option') not in ('1','2','3','4',1,2,3,4) or not all(payload.get(f) for f in ('stem','option_1','option_2','option_3','option_4')):
            continue
        if mode=='families':
            exp=q.explanation.payload or {}
            if not all(exp.get(k) for k in ('lesson','correct_reason','references')) or any(not exp.get('wrong_'+str(n)) for n in range(1,5) if n!=int(payload['correct_option'])):
                continue
        result.append(q)
        if len(result)>MAX_PAPER:raise ValueError('Exam exceeds safe item limit')
    if mode=='full_exams':
        source = result[0].exam_source.metadata if result else {}
        try: expected=int(source.get('expected_total_questions'))
        except (ValueError,TypeError):expected=0
        positions=[int(x.payload.get('original_number') or 0) for x in result]
        if not expected or len(result)!=expected or set(positions)!=set(range(1,expected+1)):
            raise ValueError('Full paper is incomplete or not published')
    if not result:raise ValueError('No fully approved questions in this set')
    return sorted(result,key=lambda q:(int(q.exam_source.metadata.get('year') or 0) if q.exam_source_id else 0,
                      int(q.payload.get('original_number') or 0) if q.origin=='OFFICIAL' else 0,q.question_id))


def user_can_access(request,mode,selection):
    if request.user.is_staff and request.user.is_active:return True
    from subscriptions.permissions import HasActiveSubscription
    if HasActiveSubscription().has_permission(request,None):return True
    if mode not in ('chapters','subjects'):return False
    from marketplace.models import Product
    from marketplace.services import has_product_access
    return has_product_access(request.user,Product.Kind.SUBJECT,selection['subject_code'])


def frozen_snapshot(questions):
    qids=[x.question_id for x in questions]
    readings={}
    for ri in ReadingItem.objects.filter(question_id__in=qids).select_related('passage'):
        readings[ri.question_id]=(ri.passage.metadata or {}).get('body','')
    explanations={row['question_id']:row['payload'] for row in QuestionExplanation.objects.filter(question_id__in=qids).values('question_id','payload')}
    snap=[]
    for q in questions:
        p=q.payload or {}
        exp=explanations.get(q.question_id) or {}
        c=getattr(q,'classification',None) if q.origin=='OFFICIAL' else None
        snap.append({'id':q.question_id,'sha':q.sha256,'stem':p['stem'],
            'options':[p['option_1'],p['option_2'],p['option_3'],p['option_4']],
            'correct':int(p['correct_option']),'subject':p.get('subject_code',''),
            'chapter':c.chapter_code if c else '',
            'passage':readings.get(q.question_id,''),
            'explanation':exp})
    return snap


def paper_response(a):
    remaining=max(0,int((a.deadline_at-timezone.now()).total_seconds()))
    return {'attempt_id':a.pk,'mode':a.mode,'status':a.status,
            'remaining_seconds':remaining,'deadline':a.deadline_at,
            'answers':a.answers,
            'marked_question_ids':ordered_marked(a.snapshot,a.marked_question_ids),
            # Deliberately NO key / detailed subtopic / explanation in prefinish API.
            'questions':[{'id':q['id'],'stem':q['stem'],'options':q['options'],
                          'subject':q['subject'],'chapter':q['chapter'],'passage':q['passage']}
                          for q in a.snapshot]}


def error(message, status=400):return Response({'detail':message},status=status)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def catalog(request):
    return student_catalog(request)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def start(request):
    if not enabled():return error('آزمون هنوز فعال نیست.',404)
    mode=request.data.get('mode')
    selector=request.data.get('selector')
    try:
        validate_selection(mode,selector)
        if not user_can_access(request,mode,selector):return error('اشتراک معتبر برای این بخش لازم است.',403)
        with transaction.atomic():
            # Serialize simultaneous start requests for the same user.
            from django.contrib.auth import get_user_model
            get_user_model().objects.select_for_update().get(pk=request.user.pk)
            key=hashlib.sha256(json.dumps([mode,selector],ensure_ascii=False,sort_keys=True).encode()).hexdigest()
            a=BankAttempt.objects.filter(user=request.user,collection_key=key,status='IN_PROGRESS',deadline_at__gt=timezone.now()).order_by('-id').first()
            if a:return Response(paper_response(a))
            qs=eligible_questions(mode,selector)
            duration = 180 if mode in ('subjects','full_exams') else 90
            a=BankAttempt.objects.create(user=request.user,mode=mode,selector=selector,
                        collection_key=key,snapshot=frozen_snapshot(qs),
                        deadline_at=timezone.now()+timedelta(minutes=duration))
            return Response(paper_response(a),status=201)
    except (ValueError,TypeError,KeyError) as exc:
        return error(str(exc))


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def paper(request,pk):
    if not enabled():return error('غیرفعال',404)
    a=BankAttempt.objects.filter(pk=pk,user=request.user).first()
    if not a:return error('یافت نشد.',404)
    if a.status!='IN_PROGRESS':return error('این آزمون پایان یافته است.',409)
    if timezone.now()>=a.deadline_at:return error('زمان آزمون تمام شده؛ آزمون را پایان دهید.',409)
    return Response(paper_response(a))


@api_view(['PUT'])
@permission_classes([IsAuthenticated])
def answer(request,pk):
    if not enabled():return error('غیرفعال',404)
    qid=request.data.get('question_id');option=request.data.get('selected_option')
    if not isinstance(qid,str) or type(option) is not int or option not in (0,1,2,3,4):return error('پاسخ نامعتبر')
    with transaction.atomic():
        a=BankAttempt.objects.select_for_update().filter(pk=pk,user=request.user).first()
        if not a:return error('یافت نشد.',404)
        if a.status!='IN_PROGRESS' or timezone.now()>=a.deadline_at:return error('جلسه بسته شده است.',409)
        if qid not in {q['id'] for q in a.snapshot}:return error('سؤال نامعتبر',404)
        answers=dict(a.answers or {});answers[qid]=option
        a.answers=answers;a.save(update_fields=['answers'])
    return Response({'saved':True,'question_id':qid,'selected_option':option})


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def mark_for_leitner(request,pk):
    """Single action: show bookmark immediately; produce existing FSRS card ONLY postfinish."""
    if not enabled():return error('غیرفعال',404)
    qid=request.data.get('question_id')
    if not isinstance(qid,str) or not qid or len(qid)>128:return error('شناسه سؤال نامعتبر است.')
    with transaction.atomic():
        a=BankAttempt.objects.select_for_update().filter(pk=pk,user=request.user).first()
        if not a:return error('آزمون یافت نشد.',404)
        if a.status not in ('IN_PROGRESS','COMPLETED'):return error('وضعیت آزمون نامعتبر است.',409)
        if a.status=='IN_PROGRESS' and timezone.now()>=a.deadline_at:
            return error('زمان پایان یافته؛ ابتدا آزمون را پایان دهید.',409)
        try:marked=ordered_marked(a.snapshot,a.marked_question_ids,qid)
        except ValueError:return error('سؤال متعلق به این آزمون نیست.',404)
        if marked!=a.marked_question_ids:
            a.marked_question_ids=marked
            a.save(update_fields=['marked_question_ids'])
        # No FSRS answer/key is created or returned while taking the exam.
        if a.status=='IN_PROGRESS':
            return Response({'marked':True,'queued_until_finish':True,
                             'marked_question_ids':marked})
        summary=sync_after_finish(a)
        return Response({'marked':True,'queued_until_finish':False,'marks':summary})


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def finish(request,pk):
    if not enabled():return error('غیرفعال',404)
    with transaction.atomic():
        a=BankAttempt.objects.select_for_update().filter(pk=pk,user=request.user).first()
        if not a:return error('یافت نشد.',404)
        if a.status=='COMPLETED':return Response({'attempt_id':a.pk,'completed':True,'already_finished':True})
        if a.status!='IN_PROGRESS':return error('جلسه نامعتبر',409)
        questions=[QuestionSnapshot(question_id=x['id'],correct_option=x['correct'],
                    subject=x['subject'] or 'GENERAL',chapter=x['chapter'],revision_sha=x['sha']) for x in a.snapshot]
        try:r=grade(questions,a.answers or {})
        except ValueError:return error('ساختار آزمون نامعتبر است.',409)
        report=safe_report(r,completed=True)
        a.status='COMPLETED';a.finished_at=timezone.now()
        a.duration_seconds=max(0,int((a.finished_at-a.started_at).total_seconds()))
        a.report=report
        a.save(update_fields=['status','finished_at','duration_seconds','report'])
    try:
        marks=sync_after_finish(a)
    except Exception:
        import logging
        logging.getLogger(__name__).exception('Deferred FSRS bookmark sync failed')
        marks={'pending_question_ids':list(a.marked_question_ids or []),'saved_question_ids':[]}
    return Response({'attempt_id':a.pk,'completed':True,'marks':marks,
                     'report_url':f'/api/konkur-bank/student/attempt/{a.pk}/report/'})


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def report(request,pk):
    if not enabled():return error('غیرفعال',404)
    a=BankAttempt.objects.filter(pk=pk,user=request.user,status='COMPLETED').first()
    if not a:return error('کارنامه یافت نشد یا آزمون تمام نشده است.',404)
    exp={q['id']:{'stem':q['stem'],'options':q['options'],
                  'explanation':q['explanation']} for q in a.snapshot}
    return Response({'id':a.pk,'finished_at':a.finished_at,'mode':a.mode,
                     'duration_seconds':a.duration_seconds,
                     'report':a.report,'detail':exp,'marks':bookmark_summary(a)})


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def history(request):
    if not enabled():return error('غیرفعال',404)
    rows=BankAttempt.objects.filter(user=request.user,status='COMPLETED').order_by('-finished_at')[:30]
    return Response([{'id':a.pk,'mode':a.mode,'finished_at':a.finished_at,
                     'percent':(a.report or {}).get('overall',{}).get('percent_display','0'),
                     'correct':(a.report or {}).get('overall',{}).get('correct',0)} for a in rows])
