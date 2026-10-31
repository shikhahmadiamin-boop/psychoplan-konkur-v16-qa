"""Pure, fail-closed publication checks. No database or Django imports.

Admin actions may call these checks; student API retains its own approved-only filters.
Never treat the uploaded draft's `review_status`/`license_status` as approval.
"""

def _filled(value):
    return isinstance(value, str) and bool(value.strip())


def publication_blockers(*, origin, payload, review_status, rights_status,
                         source_metadata=None, classification=None,
                         explanation=None, family_metadata=None):
    """Return a list of explicit human-readable blockers, not an authorization.

    `classification` is a dict containing chapter_code, topic_code, status.
    `explanation` is a dict representing the *saved* QuestionExplanation payload.
    """
    p = payload if isinstance(payload, dict) else {}
    src = source_metadata if isinstance(source_metadata, dict) else {}
    c = classification if isinstance(classification, dict) else {}
    exp = explanation if isinstance(explanation, dict) else {}
    family = family_metadata if isinstance(family_metadata, dict) else {}
    errors = []
    if origin not in ('OFFICIAL', 'AUTHORED'):
        return ['نوع سؤال نامعتبر است.']
    for field, title in [('stem', 'متن سؤال'), ('option_1','گزینه ۱'),
                         ('option_2','گزینه ۲'), ('option_3','گزینه ۳'), ('option_4','گزینه ۴')]:
        if not _filled(p.get(field)):
            errors.append(f'{title} خالی است.')
    key = str(p.get('correct_option') or '').strip()
    if key not in ('1','2','3','4'):
        errors.append('کلید پاسخ معتبر (۱ تا ۴) ثبت نشده است.')
    if review_status != 'APPROVED':
        errors.append('بازبینی علمی خود سؤال تأیید نشده است.')
    if not _filled(exp.get('lesson')):
        errors.append('درسنامه پاسخ تشریحی خالی است.')
    if not _filled(exp.get('correct_reason')):
        errors.append('دلیل درستی پاسخ خالی است.')
    if key in ('1','2','3','4'):
        for k in '1234':
            if k != key and not _filled(exp.get('wrong_'+k)):
                errors.append(f'دلیل رد گزینه {k} خالی است.')
    if not _filled(exp.get('references')):
        errors.append('منبع علمی پاسخ تشریحی ثبت نشده است.')
    if exp.get('review_status') != 'APPROVED':
        errors.append('پاسخنامه تشریحی تأیید علمی نشده است.')
    if origin == 'OFFICIAL':
        if rights_status != 'VERIFIED':
            errors.append('مجوز بازنشر سؤال هنوز تأیید نشده است.')
        if not _filled(p.get('rights_evidence')):
            errors.append('مستند مجوز بازنشر در پرونده سؤال ثبت نشده است.')
        if not _filled(p.get('key_source')):
            errors.append('منبع معتبر کلید پاسخ ثبت نشده است.')
        if p.get('text_verified') is not True:
            errors.append('تطبیق متن با دفترچه اصلی تأیید نشده است.')
        if not all(_filled(str(src.get(k))) if src.get(k) is not None else False
                   for k in ('year','exam_body','degree')):
            errors.append('اطلاعات سال، مرجع و مقطع دفترچه ناقص است.')
        try:
            year, original = int(src.get('year')), int(p.get('original_number'))
            if not (1390 <= year <= 1420 and original > 0):
                raise ValueError()
        except (TypeError, ValueError, OverflowError):
            errors.append('سال یا شماره اصلی سؤال معتبر نیست.')
        if c.get('status') != 'APPROVED' or not _filled(c.get('chapter_code')) or not _filled(c.get('topic_code')):
            errors.append('فصل و مبحث باید در داوری علمی تأیید شوند.')
    if origin == 'AUTHORED':
        if family.get('review_status') != 'APPROVED':
            errors.append('خانواده مفهومی تأیید نشده است.')
        if not _filled(family.get('public_topic')):
            errors.append('عنوان عمومی خانواده مفهومی خالی است.')
    return errors
