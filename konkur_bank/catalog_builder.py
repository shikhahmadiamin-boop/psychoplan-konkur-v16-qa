"""Preview the three official modes from one original-question table. Pure Python.
No question copy, no publication, no database writes. Year window configurable.
"""
from collections import defaultdict


def build_official_preview(data, window=12):
    if not isinstance(window, int) or not (1 <= window <= 30):
        raise ValueError('Invalid year window')
    sources = {r['exam_id']: r for r in data['sources.csv']}
    questions = data['questions.csv']
    groups = defaultdict(list)
    chapters = defaultdict(list)
    individual = defaultdict(list)
    per_exam = defaultdict(list)
    needs_topic = []
    topic_not_confirmed = []
    for q in questions:
        source = sources[q['exam_id']]
        key=(source['exam_body'], source['degree'], q['subject_code'])
        yr=int(source['year'])
        groups[key].append((yr,q['question_id']))
        individual[(q['exam_id'],q['subject_code'])].append(q['question_id'])
        per_exam[q['exam_id']].append(q)
        if q.get('topic_id'):
            chapters[key+(q['topic_id'],)].append(q['question_id'])
            # A label from any uploaded file is a proposed label, never a human approval.
            topic_not_confirmed.append(q['question_id'])
        else:
            needs_topic.append(q['question_id'])
    subject_windows=[]
    for (body,degree,subject),items in sorted(groups.items()):
        max_year=max(y for y,_ in items)
        window_items=[qid for y,qid in items if max_year-window+1 <= y <= max_year]
        subject_windows.append({'exam_body':body,'degree':degree,'subject_code':subject,
                                'year_from':max_year-window+1,'year_to':max_year,
                                'question_count':len(window_items),'years_present':len({y for y,_ in items if max_year-window+1 <= y <= max_year}),
                                'window_complete':len({y for y,_ in items if max_year-window+1 <= y <= max_year})==window})
    full=[]
    for exam_id, qs in sorted(per_exam.items()):
        source=sources[exam_id]
        # No guessing from partial subjects. Expected total must be independently verified.
        raw=source.get('expected_total_questions','').strip()
        expected=int(raw) if raw.isdecimal() and int(raw)>0 else None
        numbers=[int(q['original_number']) for q in qs]
        complete=bool(expected is not None and len(qs)==expected and set(numbers)==set(range(1,expected+1)))
        full.append({'exam_id':exam_id,'question_count':len(qs),'expected_total':expected,
                     'full_exam_eligible':complete, 'label':'کنکور کامل' if complete else 'گزیده دروس / ناقص'})
    return {'number_of_questions':len(questions),'chapter_groups':len(chapters),
            'questions_without_topic':len(needs_topic), 'questions_topic_not_confirmed':len(topic_not_confirmed), 'subject_year_groups':len(individual),
            'subject_windows':subject_windows, 'full_exams':full,
            'rules':{'chapter':'requires verified topic_id','subject_window':'rolling range, incomplete years explicitly flagged',
                     'full_exam':'requires verified expected_total_questions and all original positions'}}
