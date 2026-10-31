"""Pure bookmark contracts for PsychoPlan (no ORM, no answer disclosure)."""
import hashlib


def canonical_key(question_id):
    if not isinstance(question_id, str) or not question_id or len(question_id) > 128:
        raise ValueError('Invalid canonical question ID')
    return 'konkur:' + hashlib.sha256(question_id.encode('utf8')).hexdigest()


def ordered_marked(snapshot, marked, question_id=None):
    """Server-side membership validation; client cannot inject foreign questions."""
    allowed = [row['id'] for row in snapshot]
    if not all(isinstance(qid, str) and qid for qid in allowed) or len(set(allowed)) != len(allowed):
        raise ValueError('Invalid question snapshot')
    if not isinstance(marked, list) or any(not isinstance(qid, str) for qid in marked):
        raise ValueError('Invalid saved markers')
    if any(qid not in allowed for qid in marked):
        raise ValueError('Unknown saved question')
    if question_id is not None and question_id not in allowed:
        raise ValueError('Question is not part of your attempt')
    selected = set(marked)
    if question_id is not None:
        selected.add(question_id)
    return [qid for qid in allowed if qid in selected]


def explanation_to_text(payload):
    if isinstance(payload, str):
        return payload.strip()
    if not isinstance(payload, dict):
        return ''
    names = [('lesson','درسنامه'),('correct_reason','دلیل پاسخ صحیح'),
             ('wrong_1','بررسی گزینه ۱'),('wrong_2','بررسی گزینه ۲'),
             ('wrong_3','بررسی گزینه ۳'),('wrong_4','بررسی گزینه ۴'),
             ('references','منابع')]
    return '\n\n'.join(f'{title}: {str(payload[k]).strip()}'
                      for k,title in names if payload.get(k))


def fsrs_item(snapshot_row, answers, *, subject_label, exam_title):
    """Only call AFTER finish. No client-provided key/answer is trusted."""
    qid = snapshot_row['id']
    correct = snapshot_row['correct']
    options = snapshot_row['options']
    if type(correct) is not int or correct not in (1,2,3,4):
        raise ValueError('Unreviewed question has no valid answer key')
    if not isinstance(options,list) or len(options)!=4 or any(not str(x).strip() for x in options):
        raise ValueError('Incomplete options')
    selected = answers.get(qid, 0)
    if type(selected) is not int or selected not in (0,1,2,3,4):
        raise ValueError('Invalid saved answer')
    return {'key':canonical_key(qid),'question_id':qid,
      'text':str(snapshot_row['stem']), 'options':options,
      'reading_passage_text':snapshot_row.get('passage') or '',
      'correct_option':correct,'explanation':explanation_to_text(snapshot_row.get('explanation')),
      'selected':selected,'state':'blank' if selected==0 else 'correct' if selected==correct else 'wrong',
      'subject_code':str(snapshot_row.get('subject') or '')[:40],
      'subject_label':str(subject_label)[:120],
      'topic':str(snapshot_row.get('chapter') or 'سؤال کنکور')[:200],
      'exam_title':str(exam_title)[:200]}
