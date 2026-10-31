"""Adapter to CURRENT practice.MistakeCard -> courses.FlashCard -> FSRS.

Do not create parallel flashcard/SRS tables. Imports happen only after finish.
"""
from django.db import transaction, IntegrityError
from practice.models import MistakeCard
from practice.mistakes import create_mistake
from weekly_exams.models import Subject
from .bookmark_core import canonical_key, fsrs_item, ordered_marked


def bookmark_summary(attempt):
    marked = ordered_marked(attempt.snapshot, attempt.marked_question_ids)
    if attempt.status != 'COMPLETED':
        return {'marked_question_ids':marked, 'saved_question_ids':[],
                'pending_question_ids':marked}
    keys={canonical_key(qid):qid for qid in marked}
    saved_keys=set(MistakeCard.objects.filter(
        owner=attempt.user, question_key__in=keys,
        card__owner=attempt.user, card__is_active=True,
        card__personal_deck__is_active=True,
    ).values_list('question_key',flat=True))
    saved=[qid for key,qid in keys.items() if key in saved_keys]
    return {'marked_question_ids':marked,'saved_question_ids':saved,
            'pending_question_ids':[qid for qid in marked if qid not in saved]}


def sync_after_finish(attempt):
    """Call only when attempt COMPLETED. Nested savepoints avoid half-created cards.
    Quota/validation failure leaves mark pending and RETRYABLE, without blocking grading.
    """
    if attempt.status != 'COMPLETED':
        raise ValueError('Complete attempt before adding answers to FSRS')
    marked = ordered_marked(attempt.snapshot, attempt.marked_question_ids)
    rows={r['id']:r for r in attempt.snapshot}
    errors=[]
    for qid in marked:
        row=rows[qid]
        subject=row.get('subject') or ''
        try:
            subject_label=Subject.label(subject) or subject
        except (KeyError, ValueError):
            subject_label=subject
        try:
            item=fsrs_item(row,attempt.answers or {},subject_label=subject_label,
                           exam_title='کنکور سنواتی یا تحلیل هوشمند')
            # Each card transaction is independent; existing practice helper locks the student.
            with transaction.atomic():
                create_mistake(attempt.user,item,'konkur',attempt.pk,
                               allow_blank=True,allow_correct=True)
        except (ValueError,IntegrityError) as exc:
            errors.append({'question_id':qid,'reason':str(exc)[:160]})
    summary=bookmark_summary(attempt)
    if errors:summary['errors']=errors
    return summary
