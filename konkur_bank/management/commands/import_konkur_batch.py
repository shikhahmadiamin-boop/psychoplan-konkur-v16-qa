"""Django/PostgreSQL bulk importer: draft-only, transactionally validated.

Protected behind explicit PP_KONKUR_ADMIN_DRAFT_IMPORT_ENABLED=True.
Never deploy before generating/testing migrations against the latest site source.
"""
import hashlib
import json
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from konkur_bank.importer_validation import validate, ValidationError
from konkur_bank import models as m


def canon(row):
    payload=json.dumps(row,ensure_ascii=False,sort_keys=True,separators=(',',':'))
    return hashlib.sha256(payload.encode('utf-8')).hexdigest()


def is_same(existing,expected):
    return existing.sha256==canon(expected)


def sync_immutable(model,pk,data,extra=None):
    existing=model.objects.filter(pk=pk).first()
    if existing is not None:
        if not is_same(existing,data):
            raise CommandError('Conflict in immutable '+model.__name__+' '+str(pk))
        return existing,False
    kwargs={model._meta.pk.name:pk,'metadata':data,'sha256':canon(data)}
    kwargs.update(extra or {})
    return model.objects.create(**kwargs),True


def sync_question(row,origin,package_id,source_id=None,family_id=None,allow_revision=False):
    pk=row['question_id']
    sha=canon(row)
    obj=m.BankQuestion.objects.select_for_update().filter(pk=pk).first()
    if obj is None:
        obj=m.BankQuestion.objects.create(question_id=pk,origin=origin,exam_source_id=source_id,
            family_id=family_id,payload=row,sha256=sha,review_status='DRAFT',
            rights_status=row.get('license_status','UNVERIFIED'),is_published=False)
        return obj,True
    if obj.sha256==sha:return obj,False
    if not allow_revision or obj.is_published:
        raise CommandError('Existing question changed without an explicit draft revision: '+pk)
    if (obj.origin,obj.exam_source_id,obj.family_id)!=(origin,source_id,family_id):
        raise CommandError('Structural origin/source/family conflict '+pk)
    for field in ('question_id','exam_id','original_number','family_id','concept_ids','related_official_ids'):
        if obj.payload.get(field)!=row.get(field):raise CommandError('Structural revision conflict '+field+' '+pk)
    m.QuestionRevision.objects.create(question=obj,previous_sha=obj.sha256,
        previous_payload=obj.payload,source_package_id=package_id)
    obj.payload=row;obj.sha256=sha;obj.save(update_fields=['payload','sha256','updated_at'])
    return obj,False


def sync_explanation(question,row,package_id,allow_revision):
    sha=canon(row)
    obj=m.QuestionExplanation.objects.select_for_update().filter(pk=question).first()
    if obj is None:
        m.QuestionExplanation.objects.create(question=question,payload=row,sha256=sha)
    elif obj.sha256!=sha:
        if question.is_published or not allow_revision:
            raise CommandError('Explanation changed without explicit draft revision: '+question.pk)
        m.ExplanationRevision.objects.create(question=question,previous_sha=obj.sha256,
            previous_payload=obj.payload,source_package_id=package_id)
        obj.payload=row;obj.sha256=sha;obj.save(update_fields=['payload','sha256'])


def relationship(model,lookup,fields=None):
    obj,created=model.objects.get_or_create(defaults=fields or {},**lookup)
    if not created:
        for name,value in (fields or {}).items():
            if getattr(obj,name)!=value:
                raise CommandError('Existing relationship changed: '+model.__name__)


class Command(BaseCommand):
    help='Preview bulk imports; transactional draft-only imports require an explicit admin feature flag'
    def add_arguments(self,parser):
        parser.add_argument('package')
        parser.add_argument('--commit-draft',action='store_true')
        parser.add_argument('--allow-draft-revision',action='store_true')

    def handle(self,*args,**kwargs):
        try:v=validate(kwargs['package'])
        except (ValidationError,OSError) as e:raise CommandError(str(e))
        summary={k:v[k] for k in ('package_id','kind','digest','question_count','safe_to_publish')}
        if not kwargs['commit_draft']:
            self.stdout.write(json.dumps({'mode':'DRY_RUN','result':summary},ensure_ascii=False))
            return
        if getattr(settings,'PP_KONKUR_ADMIN_DRAFT_IMPORT_ENABLED',False) is not True:
            raise CommandError('STOP: admin draft imports are disabled')
        data=v['records'];pid=v['package_id'];kind=v['kind']
        with transaction.atomic():
            prior=m.ImportBatch.objects.select_for_update().filter(pk=pid).first()
            if prior:
                if prior.sha256!=v['digest']:raise CommandError('Existing package ID changed')
                self.stdout.write('ALREADY_IMPORTED; no changes');return
            if m.ImportBatch.objects.filter(sha256=v['digest']).exists():
                self.stdout.write('ALREADY_IMPORTED_BY_HASH; no changes');return
            if kind=='OFFICIAL_YEARLY':
                for row in data['sources.csv']:
                    sync_immutable(m.ExamSource,row['exam_id'],row)
                for row in data['questions.csv']:
                    sync_question(row,'OFFICIAL',pid,source_id=row['exam_id'],
                                  allow_revision=kwargs['allow_draft_revision'])
                # Human review is always separate from extracted/imported metadata.
                # topic_id is a suggestion until an authorized reviewer approves it.
                for row in data['questions.csv']:
                    obj = m.BankQuestion.objects.get(pk=row['question_id'])
                    classified, _ = m.QuestionClassification.objects.get_or_create(
                        question=obj,
                        defaults={'topic_code':row.get('topic_id',''),'status':'PENDING'})
                    if classified.topic_code != row.get('topic_id',''):
                        if classified.status == 'APPROVED' or not kwargs['allow_draft_revision']:
                            raise CommandError('Classification conflict; no silent overwrite: '+row['question_id'])
                        classified.topic_code=row.get('topic_id','')
                        classified.status='PENDING'
                        classified.reviewed_by=None
                        classified.reviewed_at=None
                        classified.save(update_fields=['topic_code','status','reviewed_by','reviewed_at'])
                for row in data['passages.csv']:
                    sync_immutable(m.ReadingPassage,row['passage_id'],row,{'exam_source_id':row['exam_id']})
                for row in data['collections.csv']:
                    sync_immutable(m.BankCollection,row['collection_id'],row,{'exam_source_id':row['exam_id']})
                for row in data['collection_items.csv']:
                    relationship(m.CollectionItem,{'collection_id':row['collection_id'],
                        'question_id':row['question_id']},{'position':int(row['order'])})
                for row in data['reading_items.csv']:
                    relationship(m.ReadingItem,{'passage_id':row['passage_id'],
                        'question_id':row['question_id']},{'position':int(row['order'])})
            else:
                for row in data['families.csv']:
                    sync_immutable(m.QuestionFamily,row['family_id'],row)
                for row in data['concepts.csv']:
                    sync_immutable(m.Concept,row['concept_id'],row,{'family_id':row['family_id']})
                explanations={x['question_id']:x for x in data['explanations.csv']}
                for row in data['authored_questions.csv']:
                    question,_=sync_question(row,'AUTHORED',pid,family_id=row['family_id'],
                        allow_revision=kwargs['allow_draft_revision'])
                    sync_explanation(question,explanations[row['question_id']],pid,kwargs['allow_draft_revision'])
                    for concept in filter(None,row.get('concept_ids','').split('|')):
                        relationship(m.QuestionConcept,{'question_id':question.pk,'concept_id':concept})
                    for ref in filter(None,row.get('related_official_ids','').split('|')):
                        relationship(m.QuestionSourceLink,{'question_id':question.pk,'related_official_id':ref})
            m.ImportBatch.objects.create(package_id=pid,sha256=v['digest'],kind=kind,
                approved_for_publish=False)
        self.stdout.write(json.dumps({'mode':'IMPORTED_DRAFT_ONLY', 'result':summary},ensure_ascii=False))
