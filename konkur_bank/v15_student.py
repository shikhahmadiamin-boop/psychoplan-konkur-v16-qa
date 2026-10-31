"""Explicit membership for V15 prepared packages. No inference or scientific audit.

The legacy catalog is never modified. Full-exam selectors use an internal
collection ID, so identical official exam IDs in separate uploaded packages
cannot combine. Existing subject/chapter selectors are group-wide by contract:
legacy selectors continue to form a common pool for old clients; new clients
explicitly opt into per-collection selectors to prevent accidental merging.
"""
from collections import defaultdict
import hmac
from django.db.models import Count, Q
from .models import BankCollection, CollectionItem, BankQuestion, QuestionFamily
from .v15_flags import portal_enabled

MODES = ('full_exams', 'subjects', 'chapters')


def selector_for(mode, meta, collection_id=None):
    if mode == 'full_exams':
        return (str(collection_id),)
    key = (str(meta.get('exam_body') or ''), str(meta.get('degree') or ''),
           str(meta.get('subject_code') or ''))
    if mode == 'chapters':
        key += (str(meta.get('chapter_code') or ''), str(meta.get('topic_code') or ''))
    return key


def _candidate_collections(mode, selection):
    qs = BankCollection.objects.filter(metadata__v15_mode=mode)
    if mode == 'full_exams':
        return qs.filter(pk=selection['exam_id'])
    # The optional collection ID is the only identity used by the new client.
    # The other fields are still checked to reject spoofed/mismatched selectors.
    if 'collection_id' in selection:
        qs = qs.filter(pk=selection['collection_id'])
    qs = qs.filter(metadata__exam_body=selection['exam_body'],
                   metadata__degree=selection['degree'],
                   metadata__subject_code=selection['subject_code'])
    if mode == 'chapters':
        qs = qs.filter(metadata__chapter_code=selection['chapter'],
                       metadata__topic_code=selection['topic'])
    return qs


def _v15_family_by_token(token):
    from .student_views import public_family_token
    if not isinstance(token, str) or len(token) != 40:
        return None
    # Bounded by the number of uploaded families, not the question-bank size.
    for f in QuestionFamily.objects.filter(metadata__v15_mode='families').only('family_id'):
        if hmac.compare_digest(public_family_token(f.family_id), token):
            return f
    return None


def independent_eligible_questions(mode, selection, max_paper=450):
    """None means not a V15 selection; [] is never returned for a disabled V15 set."""
    if not portal_enabled():
        return None
    if mode == 'families':
        f = _v15_family_by_token(selection['family_id'])
        if f is None:
            return None
        qs = BankQuestion.objects.filter(family=f, origin='AUTHORED',
                 is_published=True, review_status='APPROVED').select_related('family','explanation')
        result = list(qs.order_by('question_id')[:max_paper+1])
        if not result: raise ValueError('این خانواده در حال حاضر منتشر نشده است.')
        if len(result)>max_paper: raise ValueError('مجموعه از حد مجاز سؤال بیشتر است.')
        return result
    if mode not in MODES:
        return None
    if mode in ('subjects', 'chapters') and 'collection_id' not in selection:
        # Keep existing test sessions and legacy student clients on their own pool.
        # New prepared collections only work with their explicit ID.
        return None
    rows = _candidate_collections(mode, selection)
    if not rows.exists():
        # Do not silently fall back to legacy catalog if a caller explicitly
        # selected a V15 collection; that would leak or combine other pools.
        if 'collection_id' in selection:
            raise ValueError('مجموعه انتخابی در دسترس نیست.')
        return None
    active = rows.filter(is_published=True)
    ids = CollectionItem.objects.filter(collection__in=active).values('question_id')
    qs = (BankQuestion.objects.filter(pk__in=ids, origin='OFFICIAL',
               is_published=True, review_status='APPROVED')
          .select_related('exam_source', 'classification'))
    found=[]
    for q in qs.iterator(chunk_size=200):
        source = (q.exam_source.metadata or {}) if q.exam_source_id else {}
        try: year = int(source['year'])
        except (TypeError,KeyError,ValueError): continue
        if mode in ('subjects','chapters') and not (selection['year_start']<=year<=selection['year_end']):
            continue
        payload = q.payload or {}
        if str(payload.get('correct_option')) not in ('1','2','3','4'):
            continue
        if not all(str(payload.get(k) or '').strip() for k in ('stem','option_1','option_2','option_3','option_4')):
            continue
        found.append(q)
        if len(found)>max_paper: raise ValueError('مجموعه از حد مجاز سؤال بیشتر است.')
    if not found: raise ValueError('این مجموعه سؤال منتشرشده ندارد.')
    if mode=='full_exams':
        # Full exams select one and only one collection, never all papers with the same official ID.
        c=active.first()
        if c is None: raise ValueError('کنکور غیرفعال است.')
        try: expected=int((c.metadata or {})['expected_total_questions'])
        except (TypeError,ValueError,KeyError): raise ValueError('اطلاعات دفترچه کامل نیست.')
        positions=[int((q.payload or {}).get('original_number') or 0) for q in found]
        if len(found)!=expected or set(positions)!=set(range(1, expected+1)):
            raise ValueError('این دفترچه کامل نیست.')
        return sorted(found, key=lambda q: int((q.payload or {}).get('original_number') or 0))
    return sorted(found, key=lambda q: (int((q.exam_source.metadata or {}).get('year') or 0),
                       int((q.payload or {}).get('original_number') or 0), q.pk))


def _catalog_cards(mode, groups, years, independent):
    """Pure list constructor; one card per collection ONLY for opted-in clients.

    `groups` maps existing public filter keys to (collection, count) lists;
    `years` maps collection IDs to year sets. No scientific inference.
    """
    result=[]
    for key, values in groups.items():
        entries = [[one] for one in values] if independent else [values]
        for subset in entries:
            unique_years=set().union(*(years[c.pk] for c,_ in subset))
            if not unique_years:
                continue
            top=max(unique_years)
            lo=max(1390,top-11)
            count=sum(n for _,n in subset)
            item=dict(exam_body=key[0],degree=key[1],subject_code=key[2],
                      year_start=lo,year_end=top,
                      years_present=len({y for y in unique_years if lo<=y<=top}),
                      question_count=count)
            if mode=='chapters':
                item.update(chapter=key[3],topic=key[4])
            if independent:
                coll=subset[0][0]
                item['collection_id']=str(coll.pk)
                item['title']=str((coll.metadata or {}).get('title') or
                                  (coll.metadata or {}).get('source_collection_id') or coll.pk)
                if mode=='subjects':
                    # Prepared XLSX year window is authoritative for the card.
                    try:
                        start=int(coll.metadata['year_start'])
                        end=int(coll.metadata['year_end'])
                    except (TypeError,ValueError,KeyError):
                        continue
                    if not (1390<=start<=end<=1420 and end-start<=11):
                        continue
                    item['year_start']=start
                    item['year_end']=end
                    item['years_present']=len({y for y in unique_years if start<=y<=end})
                    item['question_count']=count
            result.append(item)
    return result


def merge_v15_catalog(original, public_family_token, independent=False):
    """Small aggregated queries; never fetch all question memberships into Python."""
    if not portal_enabled() or not independent:
        return original
    modes={key:list(vals) for key, vals in original.items()}
    qs=(BankCollection.objects.filter(metadata__v15_mode__in=MODES, is_published=True)
        .annotate(question_count=Count('items',filter=Q(items__question__is_published=True,
                         items__question__review_status='APPROVED')))
        .only('collection_id','metadata','is_published'))
    groups={mode:defaultdict(list) for mode in MODES}
    for coll in qs.iterator(chunk_size=200):
        if not coll.question_count: continue
        meta=coll.metadata or {}; mode=meta.get('v15_mode')
        if mode not in groups:continue
        groups[mode][selector_for(mode,meta,coll.pk)].append((coll,coll.question_count))
    for (internal_id,), values in groups['full_exams'].items():
        coll,count=values[0]; meta=coll.metadata
        try: expected=int(meta['expected_total_questions']); year=int(meta['year'])
        except (ValueError,TypeError,KeyError):continue
        if expected != count:continue
        modes.setdefault('full_exams',[]).append(dict(exam_id=internal_id,year=year,
                 exam_body=meta.get('exam_body',''),degree=meta.get('degree',''),question_count=count))
    # Old frontend keeps its grouped selectors. The new frontend must explicitly
    # opt in, display each row and send the row's collection_id unchanged.
    for mode in ('subjects','chapters'):
        # Read only distinct (collection, year) tuples, never all question rows.
        valid_ids=[c.pk for vals in groups[mode].values() for c,_ in vals]
        years=defaultdict(set)
        if valid_ids:
            year_rows=(CollectionItem.objects.filter(collection_id__in=valid_ids,
                       question__is_published=True,question__review_status='APPROVED')
                       .values_list('collection_id','question__exam_source__metadata__year').distinct())
            for cid, rawyear in year_rows.iterator(chunk_size=200):
                try: years[cid].add(int(rawyear))
                except (TypeError,ValueError):continue
        replacement=_catalog_cards(mode, groups[mode], years, independent)
        # Keep legacy collections intact even if lesson/chapter keys coincide.
        # Only add new, explicitly identified sets to the opted-in response.
        modes.setdefault(mode,[]).extend(replacement)
    families=(QuestionFamily.objects.filter(metadata__v15_mode='families')
              .annotate(question_count=Count('bankquestion', filter=Q(bankquestion__is_published=True))))
    # Reverse relation for BankQuestion.family has no related_name, defaults to bankquestion_set / query name bankquestion.
    for family in families.iterator(chunk_size=200):
        if not family.question_count:continue
        meta=family.metadata or {}
        modes.setdefault('families',[]).append(dict(family_id=public_family_token(family.pk),
             label=meta.get('public_topic',''),question_count=family.question_count))
    return modes
