"""PsychoPlan Konkur Bank - pure, exact, side-effect-free grading core.

No HTTP, Django, database writes or student-facing key serialization here.
Caller must snapshot immutable questions and check session ownership/timer before grading.
"""
from __future__ import annotations
from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP
from collections import defaultdict
from typing import Mapping, Iterable


@dataclass(frozen=True)
class QuestionSnapshot:
    question_id: str
    correct_option: int
    subject: str
    chapter: str = ''
    score_per_correct: int = 3
    wrong_penalty: int = 1
    # Version is used to freeze question/key on attempt start.
    revision_sha: str = ''


@dataclass(frozen=True)
class QuestionOutcome:
    question_id: str
    selected: int
    correct_option: int
    status: str
    points: int
    subject: str
    chapter: str


def _percentage(raw: int, maximum: int) -> str:
    # Keep signed raw score; display floor at 0 for a student percent.
    value = Decimal(0) if not maximum else Decimal(max(raw, 0))*100/Decimal(maximum)
    return str(min(Decimal(100), value).quantize(Decimal('.01'), rounding=ROUND_HALF_UP))


def grade(questions: Iterable[QuestionSnapshot], answers: Mapping[str, int]):
    qs = list(questions)
    if not qs:
        raise ValueError('Cannot grade an empty exam')
    qids = [q.question_id for q in qs]
    if not all(qids) or len(set(qids)) != len(qids):
        raise ValueError('Duplicate or empty canonical question ID')
    if set(answers) - set(qids):
        raise ValueError('Answer references a question outside this exam')
    if any(type(selected) is not int or selected not in range(5) for selected in answers.values()):
        raise ValueError('Answer must be integer 0,1,2,3,4')
    if any(q.correct_option not in (1, 2, 3, 4) or q.score_per_correct <= 0 or
           q.wrong_penalty < 0 or q.wrong_penalty >= q.score_per_correct for q in qs):
        raise ValueError('Invalid key or grading settings')

    outcomes = []
    by_subject = defaultdict(list)
    by_chapter = defaultdict(list)
    for q in qs:
        selected = answers.get(q.question_id, 0)
        if selected == 0:
            status, points = 'BLANK', 0
        elif selected == q.correct_option:
            status, points = 'CORRECT', q.score_per_correct
        else:
            status, points = 'WRONG', -q.wrong_penalty
        out = QuestionOutcome(q.question_id, selected, q.correct_option,
                              status, points, q.subject, q.chapter)
        outcomes.append(out)
        by_subject[q.subject].append(out)
        by_chapter[(q.subject, q.chapter)].append(out)

    def summarize(items):
        items = list(items)
        raw = sum(x.points for x in items)
        question_map = {q.question_id: q for q in qs}
        max_score = sum(question_map[x.question_id].score_per_correct for x in items)
        return {
            'total': len(items),
            'correct': sum(x.status == 'CORRECT' for x in items),
            'wrong': sum(x.status == 'WRONG' for x in items),
            'blank': sum(x.status == 'BLANK' for x in items),
            'raw_score': raw,
            'max_score': max_score,
            'percent_display': _percentage(raw, max_score),
        }

    return {
        'overall': summarize(outcomes),
        'subjects': {key: summarize(items) for key, items in sorted(by_subject.items())},
        'chapters': [{'subject': key[0], 'chapter': key[1], **summarize(items)}
                     for key,items in sorted(by_chapter.items())],
        'outcomes': outcomes,
    }


def safe_report(result, *, completed: bool):
    """The learner must not receive key or result before a server-side finish."""
    if not completed:
        raise PermissionError('Finish exam before accessing grades, keys or explanations')
    # Explanation should be fetched and attached by the authenticated post-finish view.
    return {**{key: result[key] for key in ('overall', 'subjects', 'chapters')},
            'questions': [{'question_id': x.question_id, 'selected': x.selected,
                           'correct_option': x.correct_option, 'status': x.status,
                           'points': x.points} for x in result['outcomes']]}
