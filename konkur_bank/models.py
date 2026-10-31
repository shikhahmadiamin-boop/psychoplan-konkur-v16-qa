"""ADDITIVE-only candidate schema: never touches weekly_exams/practice/duel_arena/konkur_sim.
Migration must be generated and tested against an actual, current staging source.
"""
from django.db import models
from django.conf import settings


class ExamSource(models.Model):
    exam_id = models.CharField(max_length=128, primary_key=True)
    metadata = models.JSONField(default=dict)
    sha256 = models.CharField(max_length=64)


class QuestionFamily(models.Model):
    family_id = models.CharField(max_length=128, primary_key=True)
    metadata = models.JSONField(default=dict)
    sha256 = models.CharField(max_length=64)


class Concept(models.Model):
    concept_id = models.CharField(max_length=128, primary_key=True)
    family = models.ForeignKey(QuestionFamily, on_delete=models.PROTECT, related_name='concepts')
    metadata = models.JSONField(default=dict)
    sha256 = models.CharField(max_length=64)


class BankQuestion(models.Model):
    question_id = models.CharField(max_length=128, primary_key=True)
    origin = models.CharField(max_length=12, choices=[('OFFICIAL','سنواتی'),('AUTHORED','تألیفی')], db_index=True)
    exam_source = models.ForeignKey(ExamSource, on_delete=models.PROTECT, null=True, blank=True)
    family = models.ForeignKey(QuestionFamily, on_delete=models.PROTECT, null=True, blank=True)
    payload = models.JSONField(default=dict)
    sha256 = models.CharField(max_length=64)
    is_published = models.BooleanField(default=False, db_index=True)
    review_status = models.CharField(max_length=24, default='DRAFT')
    rights_status = models.CharField(max_length=24, default='UNVERIFIED')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [models.Index(fields=['origin','is_published'],name='konkur_origin_pub_idx')]


class QuestionRevision(models.Model):
    question = models.ForeignKey(BankQuestion, on_delete=models.PROTECT, related_name='revisions')
    previous_sha = models.CharField(max_length=64)
    previous_payload = models.JSONField(default=dict)
    source_package_id = models.CharField(max_length=128)
    recorded_at = models.DateTimeField(auto_now_add=True)


class QuestionExplanation(models.Model):
    question = models.OneToOneField(BankQuestion,on_delete=models.PROTECT,primary_key=True,related_name='explanation')
    payload = models.JSONField(default=dict)
    sha256 = models.CharField(max_length=64)


class BankCollection(models.Model):
    collection_id = models.CharField(max_length=128, primary_key=True)
    exam_source = models.ForeignKey(ExamSource, on_delete=models.PROTECT, null=True, blank=True)
    metadata = models.JSONField(default=dict)
    sha256 = models.CharField(max_length=64)
    is_published = models.BooleanField(default=False)


class CollectionItem(models.Model):
    collection = models.ForeignKey(BankCollection,on_delete=models.PROTECT,related_name='items')
    question = models.ForeignKey(BankQuestion,on_delete=models.PROTECT)
    position = models.PositiveIntegerField()

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['collection','question'],name='konkur_coll_q_uniq'),
            models.UniqueConstraint(fields=['collection','position'],name='konkur_coll_pos_uniq'),
        ]


class ReadingPassage(models.Model):
    passage_id = models.CharField(max_length=128,primary_key=True)
    exam_source = models.ForeignKey(ExamSource,on_delete=models.PROTECT)
    metadata = models.JSONField(default=dict)
    sha256 = models.CharField(max_length=64)


class ReadingItem(models.Model):
    passage = models.ForeignKey(ReadingPassage,on_delete=models.PROTECT,related_name='items')
    question = models.ForeignKey(BankQuestion,on_delete=models.PROTECT)
    position = models.PositiveIntegerField()

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['passage','question'],name='konkur_read_q_uniq'),
            models.UniqueConstraint(fields=['passage','position'],name='konkur_read_pos_uniq'),
        ]


class QuestionConcept(models.Model):
    question = models.ForeignKey(BankQuestion,on_delete=models.PROTECT,related_name='concept_links')
    concept = models.ForeignKey(Concept,on_delete=models.PROTECT)

    class Meta:
        constraints=[models.UniqueConstraint(fields=['question','concept'],name='konkur_q_concept_uniq')]


class QuestionSourceLink(models.Model):
    question = models.ForeignKey(BankQuestion,on_delete=models.PROTECT,related_name='source_links')
    related_official_id = models.CharField(max_length=128)

    class Meta:
        constraints=[models.UniqueConstraint(fields=['question','related_official_id'],name='konkur_q_source_uniq')]


class ImportBatch(models.Model):
    package_id = models.CharField(max_length=128,primary_key=True)
    sha256 = models.CharField(max_length=64, unique=True)
    kind = models.CharField(max_length=32)
    imported_at = models.DateTimeField(auto_now_add=True)
    approved_for_publish = models.BooleanField(default=False)


class ExplanationRevision(models.Model):
    question = models.ForeignKey(BankQuestion,on_delete=models.PROTECT,related_name='explanation_history')
    previous_sha = models.CharField(max_length=64)
    previous_payload = models.JSONField(default=dict)
    source_package_id = models.CharField(max_length=128)
    recorded_at = models.DateTimeField(auto_now_add=True)


# Admin-only proxy entries: no extra DB tables, two distinct menu links.
class OfficialImportPortal(ImportBatch):
    class Meta:
        proxy = True
        verbose_name = 'مدیریت کنکورهای سنوات گذشته'
        verbose_name_plural = '۱. کنکورهای سنوات گذشته'


class AnalysisImportPortal(ImportBatch):
    class Meta:
        proxy = True
        verbose_name = 'مدیریت تحلیل هوشمند'
        verbose_name_plural = '۲. تحلیل هوشمند و سؤالات تألیفی'


class QuestionClassification(models.Model):
    """Human-reviewed scientific classification. Independent of the uploaded source row."""
    STATUS_CHOICES = [('PENDING','نیازمند بازبینی'),('APPROVED','تأیید علمی'),('RECHECK','نیازمند اصلاح')]
    question = models.OneToOneField(BankQuestion, primary_key=True, related_name='classification', on_delete=models.PROTECT)
    chapter_code = models.CharField(max_length=128, blank=True, default='')
    topic_code = models.CharField(max_length=128, blank=True, default='')
    subtopic_code = models.CharField(max_length=128, blank=True, default='')
    secondary_tags = models.TextField(blank=True, default='')
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default='PENDING', db_index=True)
    reviewed_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True)
    reviewed_at = models.DateTimeField(null=True, blank=True)
    notes = models.TextField(blank=True, default='')

    class Meta:
        verbose_name = 'بررسی طبقه‌بندی علمی'
        verbose_name_plural = 'بررسی طبقه‌بندی علمی'

# PP KONKUR 2F NEW ATTEMPT MODEL
# Append to konkur_bank/models.py in a guarded install AFTER v3 0001 exists.
class BankAttempt(models.Model):
    MODE_CHOICES = [('chapters','فصل‌به‌فصل'),('subjects','جامع درس'),('full_exams','کنکور کامل'),('families','تحلیل هوشمند')]
    user = models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=models.PROTECT,related_name='konkur_bank_attempts')
    mode = models.CharField(max_length=18,choices=MODE_CHOICES)
    selector = models.JSONField(default=dict)
    collection_key = models.CharField(max_length=255,db_index=True)
    snapshot = models.JSONField(default=list)   # server-only, includes frozen correct keys
    answers = models.JSONField(default=dict)    # canonical question IDs -> 0..4
    marked_question_ids = models.JSONField(default=list)  # mark intent; FSRS generated only after finish
    status = models.CharField(max_length=16,default='IN_PROGRESS',db_index=True)
    started_at = models.DateTimeField(auto_now_add=True)
    deadline_at = models.DateTimeField()
    finished_at = models.DateTimeField(null=True,blank=True)
    duration_seconds = models.PositiveIntegerField(default=0)
    report = models.JSONField(default=dict)     # server-only, keys revealed post-finish
    class Meta:
        indexes=[models.Index(fields=['user','status'],name='ppkb_attempt_user_st_idx')]

