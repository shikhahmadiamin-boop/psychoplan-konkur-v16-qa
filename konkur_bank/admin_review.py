"""Superuser-only editorial workflow for the existing Konkur Bank models.

No new tables, migration, URLs or frontend builds. Explicit bulk confirmation;
no answer is released until the server-side publication contract passes.
"""
import hashlib
import json
from django import forms
from django.conf import settings
from django.contrib import admin, messages
from django.contrib.admin.utils import quote
from django.core.exceptions import ValidationError, PermissionDenied
from django.db import transaction
from django.http import HttpResponseRedirect
from django.template.response import TemplateResponse
from django.urls import reverse
from django.utils.html import format_html
from . import models as m
from .admin_review_rules import publication_blockers


def _sha(data):
    return hashlib.sha256(json.dumps(data,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()


def _superuser(request):
    u = request.user
    return bool(u.is_authenticated and u.is_active and u.is_staff and u.is_superuser)


# Declaration of the publisher's INTERNAL workflow policy, effective 2026-10-30.
# This is NOT evidence of permission issued by the exam authority and must
# never be written as such to any question's rights_evidence or rights_status.
# Only questions whose origin is explicitly OFFICIAL are covered.
_OWNER_POLICY_RIGHTS_BLOCKERS = (
    'مجوز بازنشر سؤال هنوز تأیید نشده',
    'مستند مجوز بازنشر در پرونده سؤال ثبت نشده',
)


def _owner_declared_official_rights(obj):
    return obj.origin == 'OFFICIAL'


def _is_owner_policy_rights_blocker(message):
    # Fail closed on unknown blockers: remove ONLY the two known legal workflow
    # reminders, not new or differently worded restrictions.
    return any(str(message).strip().startswith(x) for x in _OWNER_POLICY_RIGHTS_BLOCKERS)


class SuperuserOnly:
    def has_module_permission(self, request):
        return False  # Links within the existing 2 main portals, not extra index blocks.
    def has_view_permission(self, request, obj=None):
        return _superuser(request)
    def has_change_permission(self, request, obj=None):
        return _superuser(request)
    def has_add_permission(self, request):
        return False
    def has_delete_permission(self, request, obj=None):
        return False


class BankQuestionForm(forms.ModelForm):
    stem = forms.CharField(label='متن سؤال',widget=forms.Textarea(attrs={'rows':4}))
    option_1 = forms.CharField(label='گزینه ۱',widget=forms.Textarea(attrs={'rows':2}))
    option_2 = forms.CharField(label='گزینه ۲',widget=forms.Textarea(attrs={'rows':2}))
    option_3 = forms.CharField(label='گزینه ۳',widget=forms.Textarea(attrs={'rows':2}))
    option_4 = forms.CharField(label='گزینه ۴',widget=forms.Textarea(attrs={'rows':2}))
    correct_option = forms.ChoiceField(label='کلید پاسخ',choices=[('','هنوز مشخص نیست')]+[(str(i),f'گزینه {i}') for i in range(1,5)],required=False)
    key_source = forms.CharField(label='منبع بررسی کلید پاسخ',required=False,widget=forms.Textarea(attrs={'rows':2}))
    rights_evidence = forms.CharField(label='مستند مجوز بازنشر (برای سؤال سنواتی)',required=False,widget=forms.Textarea(attrs={'rows':2}))
    text_verified = forms.BooleanField(label='متن و چهار گزینه با منبع اصلی مقابله شده‌اند',required=False)
    class Meta:
        model=m.BankQuestion
        fields=('review_status','rights_status')
    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs)
        p=self.instance.payload or {} if self.instance.pk else {}
        for field in ('stem','option_1','option_2','option_3','option_4','correct_option','key_source','rights_evidence','text_verified'):
            self.fields[field].initial=p.get(field,False if field=='text_verified' else '')
        self.fields['review_status'].widget=forms.Select(choices=[('DRAFT','در انتظار بازبینی'),('APPROVED','تأیید علمی'),('RECHECK','نیازمند اصلاح')])
        self.fields['rights_status'].widget=forms.Select(choices=[('UNVERIFIED','تأیید نشده'),('VERIFIED','مجاز با مستند معتبر')])
    def clean(self):
        d=super().clean()
        if self.instance.pk and self.instance.is_published:
            raise ValidationError('برای ویرایش متن یا کلید، ابتدا سؤال را از انتشار خارج کنید.')
        if d.get('review_status')=='APPROVED' and (not d.get('correct_option') or
                any(not d.get(x,'').strip() for x in ('stem','option_1','option_2','option_3','option_4'))):
            raise ValidationError('تأیید علمی بدون متن کامل، چهار گزینه و کلید معتبر مجاز نیست.')
        if self.instance.origin=='OFFICIAL' and d.get('rights_status')=='VERIFIED' and not d.get('rights_evidence','').strip():
            raise ValidationError('ثبت مستند معتبر مجوز بازنشر الزامی است.')
        return d
    def save(self,commit=True):
        obj=super().save(commit=False)
        p=dict(obj.payload or {})
        for f in ('stem','option_1','option_2','option_3','option_4','correct_option','key_source','rights_evidence','text_verified'):
            p[f]=self.cleaned_data[f]
        obj.payload=p
        obj.sha256=_sha(p)
        if commit: obj.save()
        return obj


@admin.register(m.BankQuestion)
class BankQuestionAdmin(SuperuserOnly, admin.ModelAdmin):
    form=BankQuestionForm
    list_display=('question_id','origin','exam_source','review_status','rights_policy_display','is_published','classification_link','readiness')
    list_display_links=('question_id',)
    list_filter=('origin','review_status','rights_status','is_published','exam_source')
    search_fields=('question_id','payload__stem')
    list_select_related=('exam_source',)
    list_per_page=40
    ordering=('-created_at',)
    readonly_fields=('question_id','origin','exam_source','family','is_published','classification_link','readiness_full',
                     'created_at','updated_at')
    fieldsets=(("منبع و وضعیت", {'fields':('question_id','origin','exam_source','family','is_published','review_status','rights_status')}),
              ("ویرایش و بازبینی سؤال", {'fields':('stem','option_1','option_2','option_3','option_4','correct_option',
                                             'key_source','text_verified','rights_evidence')}),
              ("تأیید فصل و مبحث و کنترل انتشار", {'fields':('classification_link','readiness_full')}),
              ("اطلاعات ثبت", {'fields':('created_at','updated_at'),'classes':('collapse',)}))
    actions=['publish_selected','unpublish_selected','approve_scientific_selected']
    def get_queryset(self, request):
        return super().get_queryset(request).select_related('classification','family','explanation','exam_source')
    @admin.display(description='طبقه‌بندی علمی')
    def classification_link(self,obj):
        try:
            c=obj.classification
        except m.QuestionClassification.DoesNotExist:
            return 'ابتدا طبقه‌بندی ایجاد شود؛ از پشتیبانی ورود علمی استفاده کنید.'
        url=reverse('admin:konkur_bank_questionclassification_change',args=[quote(obj.pk)])
        return format_html('<a href="{}">ویرایش فصل و مبحث ({})</a>',url,c.get_status_display())
    @admin.display(description='وضعیت حقوقی / سیاست داخلی')
    def rights_policy_display(self,obj):
        if _owner_declared_official_rights(obj):
            return 'پوشش اعلامی مدیر؛ مستند رسمی ثبت نشده' if obj.rights_status != 'VERIFIED' or not (obj.payload or {}).get('rights_evidence') else 'مستند ثبت‌شده'
        return obj.rights_status

    def _base_blockers(self, obj):
        try:c=obj.classification
        except m.QuestionClassification.DoesNotExist:c=None
        try:e=obj.explanation
        except m.QuestionExplanation.DoesNotExist:e=None
        return publication_blockers(origin=obj.origin,payload=obj.payload,review_status=obj.review_status,
            rights_status=obj.rights_status,source_metadata=(obj.exam_source.metadata or {}) if obj.exam_source_id else {},
            classification={'status':c.status,'chapter_code':c.chapter_code,'topic_code':c.topic_code} if c else {},
            explanation=(e.payload or {}) if e else {},family_metadata=(obj.family.metadata or {}) if obj.family_id else {})

    def _blockers(self, obj):
        blocks=self._base_blockers(obj)
        if not _owner_declared_official_rights(obj):
            return blocks
        # Publisher declaration changes only this *internal publishing workflow*.
        # It does not verify a licence, change DB fields or forge rights evidence.
        return [b for b in blocks if not _is_owner_policy_rights_blocker(b)]
    @admin.display(description='آمادگی انتشار')
    def readiness(self,obj):
        if obj.is_published:return 'منتشرشده'
        blockers=self._blockers(obj)
        if blockers:return f'{len(blockers)} مانع'
        return 'آماده (سیاست اعلامی)' if _owner_declared_official_rights(obj) else 'آماده انتشار'
    @admin.display(description='شرایط انتشار')
    def readiness_full(self,obj):
        if not obj.pk:return 'ابتدا سؤال را ذخیره کنید.'
        blocks=self._blockers(obj)
        if blocks:return ' | '.join(blocks)
        if _owner_declared_official_rights(obj):
            return 'شروط داخلی انتشار انجام شده است. وضعیت مجوز رسمی و مستند آن به‌طور مستقل احراز نشده؛ انتشار مبتنی بر اظهار و مسئولیت ناشر است.'
        return 'همه الزامات تأیید شده‌اند.'
    def save_model(self,request,obj,form,change):
        if not _superuser(request):raise PermissionDenied()
        if not change:raise PermissionDenied()
        old=m.BankQuestion.objects.select_for_update().get(pk=obj.pk)
        if old.is_published:raise PermissionDenied('برای ویرایش ابتدا لغو انتشار کنید.')
        if old.sha256 != obj.sha256:
            m.QuestionRevision.objects.create(question=old,previous_sha=old.sha256,
                previous_payload=old.payload,source_package_id='ADMIN_EDITOR')
        super().save_model(request,obj,form,change)


    @admin.action(description='تأیید علمی گروهی + تطبیق متن با دفترچه (بدون انتشار)')
    def approve_scientific_selected(self, request, queryset):
        """One-click, all-or-nothing scientific review. No custom confirmation page.

        This action is explicitly a human attestation that all selected questions
        were already checked against their source. Never alters publication/rights.
        """
        from django.utils import timezone
        from django.core.exceptions import ValidationError as DjangoValidationError
        from .bulk_scientific_rules import scientific_blockers
        import logging

        back = HttpResponseRedirect(request.path)
        if not _superuser(request):
            raise PermissionDenied('فقط مدیر ارشد می‌تواند داوری گروهی انجام دهد.')
        # Use Django's already filtered queryset; refuse overly broad selections.
        try:
            count = queryset.count()
            if count < 1 or count > 200:
                self.message_user(request, 'از ۱ تا ۲۰۰ سؤال را انتخاب کنید.', level=messages.ERROR)
                return back
            ids = list(queryset.values_list('pk', flat=True))
            if len(set(ids)) != count:
                self.message_user(request, 'شناسه‌های تکراری در انتخاب پیدا شد.', level=messages.ERROR)
                return back
        except Exception:
            logging.getLogger(__name__).exception('bulk science selection failed')
            self.message_user(request, 'انتخاب سؤالات قابل خواندن نیست؛ عملیاتی انجام نشد.', level=messages.ERROR)
            return back

        try:
            with transaction.atomic():
                questions = list(m.BankQuestion.objects.filter(pk__in=ids).order_by('pk').select_for_update())
                if len(questions) != count:
                    raise DjangoValidationError('تعداد سؤالات در زمان تأیید تغییر کرده است.')
                explanations = {o.question_id:o for o in m.QuestionExplanation.objects.filter(question_id__in=ids).order_by('question_id').select_for_update()}
                classes = {o.question_id:o for o in m.QuestionClassification.objects.filter(question_id__in=ids).order_by('question_id').select_for_update()}
                blockers = []
                for q in questions:
                    e = explanations.get(q.pk)
                    c = classes.get(q.pk)
                    problems = scientific_blockers(
                        origin=q.origin, published=q.is_published, status=q.review_status,
                        payload=q.payload, explanation=(e.payload or {}) if e else None,
                        classification={'chapter_code':c.chapter_code,'topic_code':c.topic_code,
                                        'status':c.status} if c else None,
                        expect_key_source=True,
                    )
                    if problems:
                        blockers.append(f'{q.pk}: {"، ".join(problems[:2])}')
                        if len(blockers) >= 5:
                            break
                if blockers:
                    raise DjangoValidationError('؛ '.join(blockers))

                changed = 0
                now = timezone.now()
                for q in questions:
                    dirty = False
                    fields = []
                    if q.review_status != 'APPROVED':
                        q.review_status = 'APPROVED'
                        fields.append('review_status')
                        dirty = True
                    if (q.payload or {}).get('text_verified') is not True:
                        previous = dict(q.payload or {})
                        new = dict(previous)
                        new['text_verified'] = True
                        m.QuestionRevision.objects.create(
                            question=q, previous_sha=q.sha256,
                            previous_payload=previous,
                            source_package_id='ADMIN_BULK_SCIENCE_V7',
                        )
                        q.payload = new
                        q.sha256 = _sha(new)
                        fields.extend(('payload','sha256'))
                        dirty = True
                    if fields:
                        fields.append('updated_at')
                        q.save(update_fields=fields)

                    e = explanations[q.pk]
                    ep = dict(e.payload or {})
                    if ep.get('review_status') != 'APPROVED':
                        m.ExplanationRevision.objects.create(
                            question=q, previous_sha=e.sha256,
                            previous_payload=ep,
                            source_package_id='ADMIN_BULK_SCIENCE_V7',
                        )
                        ep['review_status'] = 'APPROVED'
                        e.payload = ep
                        e.sha256 = _sha(ep)
                        e.save(update_fields=('payload','sha256'))
                        dirty = True

                    c = classes[q.pk]
                    if (c.status != 'APPROVED' or c.reviewed_by_id != request.user.pk
                            or c.reviewed_at is None):
                        c.status = 'APPROVED'
                        c.reviewed_by = request.user
                        c.reviewed_at = now
                        c.save(update_fields=('status','reviewed_by','reviewed_at'))
                        dirty = True
                    if dirty:
                        changed += 1
                    self.log_change(request,q,'تأیید علمی گروهی V7، پاسخنامه، فصل و مبحث و تطبیق متن؛ بدون تغییر حقوق یا انتشار')

            self.message_user(
                request, f'تأیید علمی {count} سؤال انجام شد؛ {changed} رکورد تغییر کرد. '
                         'مجوز بازنشر و وضعیت انتشار بدون تغییر باقی ماند.',
                level=messages.SUCCESS,
            )
        except DjangoValidationError as exc:
            msg = '; '.join(str(x) for x in exc.messages)[:1100]
            self.message_user(request, 'هیچ تغییری ثبت نشد. ' + msg, level=messages.ERROR)
        except Exception:
            logging.getLogger(__name__).exception('bulk science transaction failed')
            self.message_user(
                request, 'خطای فنی در تأیید گروهی؛ تراکنش بازگشت داده شد. '
                         'هیچ موردی نیمه‌کاره تأیید نشد. گزارش فنی را بررسی کنید.',
                level=messages.ERROR,
            )
        return back

    def _bulk(self,request,queryset,action):
        if not _superuser(request) or getattr(settings,'PP_KONKUR_ADMIN_PUBLICATION_ENABLED',False) is not True:
            raise PermissionDenied('عملیات انتشار مدیریت غیرفعال است.')
        ids=list(queryset.values_list('pk',flat=True)[:501])
        if not ids or len(ids)>500:
            self.message_user(request,'۱ تا ۵۰۰ سؤال را انتخاب کنید.',level=messages.ERROR)
            return None
        if request.POST.get('confirm_editorial_action') != action:
            return TemplateResponse(request,'konkur_bank/admin_publish_confirm.html',{
                **self.admin_site.each_context(request),'title':('تأیید انتشار نهایی' if action=='publish' else 'تأیید لغو انتشار'),
                'action_name':'publish_selected' if action=='publish' else 'unpublish_selected',
                'action_code':action,'question_ids':ids,'count':len(ids),'opts':self.model._meta})
        with transaction.atomic():
            # Lock only the base question rows. Nullable LEFT JOIN relations must
            # not participate in SELECT FOR UPDATE on PostgreSQL.
            # _blockers() retrieves related review records separately.
            rows=list(m.BankQuestion.objects.filter(pk__in=ids)
                      .order_by('pk').select_for_update())
            if len(rows)!=len(ids):
                self.message_user(request,'تعداد رکوردها تغییر کرده؛ عملیات لغو شد.',level=messages.ERROR)
                return None
            errors=[]
            if action=='publish':
                for row in rows:
                    if row.is_published:continue
                    for message in self._blockers(row):
                        errors.append((row.pk,message))
                        if len(errors)>=20:break
                    if len(errors)>=20:break
                if errors:
                    self.message_user(request,'انتشار انجام نشد. موانع: '+ '؛ '.join(f'{qid}: {issue}' for qid,issue in errors[:10]),level=messages.ERROR)
                    return None
            m.BankQuestion.objects.filter(pk__in=ids).update(is_published=(action=='publish'))
            # Django admin log stores the acting superuser, object IDs and action.
            for row in rows:
                self.log_change(request,row,'انتشار نهایی توسط مدیر' if action=='publish' else 'لغو انتشار توسط مدیر')
            self.message_user(request,f'{len(rows)} سؤال '+('منتشر شد.' if action=='publish' else 'از انتشار خارج شد.'),level=messages.SUCCESS)
        return HttpResponseRedirect(request.get_full_path().split('?')[0])
    @admin.action(description='بررسی و انتشار نهایی سؤالات انتخاب‌شده')
    def publish_selected(self,request,queryset):
        return self._bulk(request,queryset,'publish')
    @admin.action(description='لغو انتشار سؤالات انتخاب‌شده')
    def unpublish_selected(self,request,queryset):
        return self._bulk(request,queryset,'unpublish')


class ExplanationForm(forms.ModelForm):
    lesson=forms.CharField(label='درسنامه',required=False,widget=forms.Textarea(attrs={'rows':5}))
    correct_reason=forms.CharField(label='چرا پاسخ صحیح درست است؟',required=False,widget=forms.Textarea(attrs={'rows':3}))
    wrong_1=forms.CharField(label='دلیل رد گزینه ۱',required=False,widget=forms.Textarea(attrs={'rows':2}))
    wrong_2=forms.CharField(label='دلیل رد گزینه ۲',required=False,widget=forms.Textarea(attrs={'rows':2}))
    wrong_3=forms.CharField(label='دلیل رد گزینه ۳',required=False,widget=forms.Textarea(attrs={'rows':2}))
    wrong_4=forms.CharField(label='دلیل رد گزینه ۴',required=False,widget=forms.Textarea(attrs={'rows':2}))
    references=forms.CharField(label='منابع علمی معتبر',required=False,widget=forms.Textarea(attrs={'rows':3}))
    review_status=forms.ChoiceField(label='داوری پاسخ تشریحی',choices=[('DRAFT','پیش‌نویس'),('APPROVED','تأیید علمی'),('RECHECK','نیازمند اصلاح')])
    class Meta:
        model=m.QuestionExplanation
        fields=('question',)
    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs)
        p=(self.instance.payload or {}) if self.instance.pk else {}
        for k in ('lesson','correct_reason','wrong_1','wrong_2','wrong_3','wrong_4','references','review_status'):
            self.fields[k].initial=p.get(k,'DRAFT' if k=='review_status' else '')
    def clean(self):
        d=super().clean()
        q=d.get('question') or (self.instance.question if self.instance.pk else None)
        if q and q.is_published:
            raise ValidationError('برای تغییر پاسخنامه ابتدا سؤال را از انتشار خارج کنید.')
        if d.get('review_status')=='APPROVED':
            for field in ('lesson','correct_reason','references'):
                if not d.get(field,'').strip():self.add_error(field,'برای تأیید علمی الزامی است.')
            key=str((q.payload or {}).get('correct_option') or '') if q else ''
            if key not in '1234' or len(key)!=1:
                raise ValidationError('برای تأیید پاسخ تشریحی، کلید معتبر سؤال لازم است.')
            for k in '1234':
                if k!=key and not d.get('wrong_'+k,'').strip():
                    self.add_error('wrong_'+k,'دلیل رد این گزینه الزامی است.')
        return d
    def save(self,commit=True):
        obj=super().save(commit=False)
        p=dict(obj.payload or {})
        for f in ('lesson','correct_reason','wrong_1','wrong_2','wrong_3','wrong_4','references','review_status'):
            p[f]=self.cleaned_data[f]
        obj.payload=p;obj.sha256=_sha(p)
        if commit:obj.save()
        return obj


@admin.register(m.QuestionExplanation)
class QuestionExplanationAdmin(SuperuserOnly,admin.ModelAdmin):
    form=ExplanationForm
    list_display=('question','status','question_origin')
    search_fields=('question__question_id',)
    list_per_page=40
    autocomplete_fields=('question',)
    fields=('question','lesson','correct_reason','wrong_1','wrong_2','wrong_3','wrong_4','references','review_status')
    def has_add_permission(self,request):return _superuser(request)
    def get_readonly_fields(self,request,obj=None):return ('question',) if obj else ()
    def status(self,obj):return (obj.payload or {}).get('review_status','DRAFT')
    status.short_description='وضعیت داوری'
    def question_origin(self,obj):return obj.question.origin
    question_origin.short_description='نوع'
    actions=('approve_selected_explanations',)
    @admin.action(description='تأیید علمی گروهی پاسخنامه‌های انتخاب‌شده (بدون انتشار)')
    def approve_selected_explanations(self, request, queryset):
        import logging
        from django.core.exceptions import ValidationError as DjangoValidationError
        from .bulk_scientific_rules import _filled
        if not _superuser(request):
            raise PermissionDenied('فقط مدیر ارشد مجاز است.')
        ids=list(queryset.values_list('pk',flat=True)[:201])
        if not ids or len(ids)>200 or len(set(ids))!=len(ids):
            self.message_user(request,'بین ۱ تا ۲۰۰ پاسخنامه را انتخاب کنید.',messages.ERROR)
            return HttpResponseRedirect(request.path)
        try:
            with transaction.atomic():
                rows=list(m.QuestionExplanation.objects.filter(pk__in=ids).order_by('pk').select_for_update())
                if len(rows)!=len(ids):
                    raise DjangoValidationError('تعداد پاسخنامه‌ها تغییر کرده است.')
                question_ids=[e.question_id for e in rows]
                questions=m.BankQuestion.objects.filter(pk__in=question_ids).in_bulk()
                problems=[]
                for e in rows:
                    q=questions.get(e.question_id)
                    p=e.payload if isinstance(e.payload,dict) else {}
                    key=str(((q.payload or {}).get('correct_option') if q else '') or '')
                    if not q or q.is_published:
                        problems.append(f'{e.question_id}: سؤال منتشرشده یا ناموجود')
                    elif key not in ('1','2','3','4'):
                        problems.append(f'{e.question_id}: کلید معتبر ندارد')
                    elif p.get('review_status','DRAFT') not in ('DRAFT','APPROVED'):
                        problems.append(f'{e.question_id}: نیازمند اصلاح موردی')
                    elif any(not _filled(p.get(k)) for k in ('lesson','correct_reason','references')):
                        problems.append(f'{e.question_id}: درسنامه، دلیل صحیح یا منابع ناقص')
                    elif any(not _filled(p.get('wrong_'+k)) for k in '1234' if k!=key):
                        problems.append(f'{e.question_id}: دلیل رد گزینه‌ها ناقص')
                if problems:
                    raise DjangoValidationError('؛ '.join(problems[:8]))
                changed=0
                for e in rows:
                    old=dict(e.payload or {})
                    if old.get('review_status')=='APPROVED':
                        continue
                    new=dict(old)
                    new['review_status']='APPROVED'
                    m.ExplanationRevision.objects.create(question_id=e.question_id,previous_sha=e.sha256,
                        previous_payload=old,source_package_id='ADMIN_BULK_EXPLANATION_V9')
                    e.payload=new
                    e.sha256=_sha(new)
                    e.save(update_fields=['payload','sha256'])
                    changed+=1
                    self.log_change(request,e,'تأیید علمی گروهی پاسخنامه؛ بدون انتشار')
            self.message_user(request,f'{len(rows)} پاسخنامه بررسی شد؛ {changed} مورد تأیید جدید ثبت شد. انتشار و مجوزها تغییری نکرد.',messages.SUCCESS)
        except DjangoValidationError as exc:
            self.message_user(request,'هیچ تغییری ثبت نشد: '+'؛ '.join(exc.messages)[:900],messages.ERROR)
        except Exception:
            logging.getLogger(__name__).exception('konkur explanations bulk approval failed')
            self.message_user(request,'خطای فنی؛ تراکنش بازگردانده شد. گزارش امن سرور را بررسی کنید.',messages.ERROR)
        return HttpResponseRedirect(request.path)
    def save_model(self,request,obj,form,change):
        if not _superuser(request):raise PermissionDenied()
        if obj.question.is_published:raise PermissionDenied('ابتدا لغو انتشار کنید.')
        if change:
            old=m.QuestionExplanation.objects.select_for_update().get(pk=obj.pk)
            if old.sha256!=obj.sha256:
                m.ExplanationRevision.objects.create(question=old.question,previous_sha=old.sha256,
                    previous_payload=old.payload,source_package_id='ADMIN_EDITOR')
        super().save_model(request,obj,form,change)


class FamilyForm(forms.ModelForm):
    review_status=forms.ChoiceField(label='وضعیت داوری',choices=[('DRAFT','پیش‌نویس'),('APPROVED','تأیید علمی'),('RECHECK','نیازمند اصلاح')])
    public_topic=forms.CharField(label='مبحث عمومی و بی‌طرف (بدون لو دادن جواب)',max_length=200,required=False)
    class Meta:
        model=m.QuestionFamily
        fields=()
    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs)
        p=(self.instance.metadata or {}) if self.instance.pk else {}
        for f in ('review_status','public_topic'):
            self.fields[f].initial=p.get(f,'DRAFT' if f=='review_status' else '')
    def clean(self):
        d=super().clean()
        if d.get('review_status')=='APPROVED' and not d.get('public_topic','').strip():
            self.add_error('public_topic','عنوان عمومیِ بدون افشای پاسخ الزامی است.')
        return d
    def save(self,commit=True):
        obj=super().save(commit=False)
        p=dict(obj.metadata or {})
        for f in ('review_status','public_topic'):p[f]=self.cleaned_data[f]
        obj.metadata=p;obj.sha256=_sha(p)
        if commit:obj.save()
        return obj


@admin.register(m.QuestionFamily)
class FamilyAdmin(SuperuserOnly,admin.ModelAdmin):
    form=FamilyForm
    list_display=('family_id','family_review','questions')
    search_fields=('family_id',)
    fields=('family_id','review_status','public_topic')
    readonly_fields=('family_id',)
    def family_review(self,obj):return (obj.metadata or {}).get('review_status','DRAFT')
    family_review.short_description='تأیید علمی'
    def questions(self,obj):return obj.bankquestion_set.count()
    questions.short_description='تعداد سؤال'
    actions=('approve_selected_families',)
    @admin.action(description='تأیید علمی گروهی خانواده‌های مفهومی انتخاب‌شده (بدون انتشار)')
    def approve_selected_families(self,request,queryset):
        import logging
        from django.core.exceptions import ValidationError as DjangoValidationError
        if not _superuser(request):
            raise PermissionDenied('فقط مدیر ارشد مجاز است.')
        ids=list(queryset.values_list('pk',flat=True)[:201])
        if not ids or len(ids)>200 or len(set(ids))!=len(ids):
            self.message_user(request,'بین ۱ تا ۲۰۰ خانواده مفهومی را انتخاب کنید.',messages.ERROR)
            return HttpResponseRedirect(request.path)
        try:
            with transaction.atomic():
                rows=list(m.QuestionFamily.objects.filter(pk__in=ids).order_by('pk').select_for_update())
                if len(rows)!=len(ids):
                    raise DjangoValidationError('فهرست خانواده‌ها تغییر کرده است.')
                published=set(m.BankQuestion.objects.filter(family_id__in=ids,is_published=True).values_list('family_id',flat=True))
                issues=[]
                for row in rows:
                    p=row.metadata if isinstance(row.metadata,dict) else {}
                    if row.pk in published:
                        issues.append(f'{row.pk}: خانواده دارای سؤال منتشرشده')
                    elif p.get('review_status','DRAFT') not in ('DRAFT','APPROVED'):
                        issues.append(f'{row.pk}: نیازمند اصلاح موردی')
                    elif not str(p.get('public_topic') or '').strip():
                        issues.append(f'{row.pk}: عنوان عمومی بدون افشای پاسخ خالی')
                if issues:
                    raise DjangoValidationError('؛ '.join(issues[:8]))
                changed=0
                for row in rows:
                    p=dict(row.metadata or {})
                    if p.get('review_status')=='APPROVED':continue
                    p['review_status']='APPROVED'
                    row.metadata=p
                    row.sha256=_sha(p)
                    row.save(update_fields=['metadata','sha256'])
                    changed+=1
                    self.log_change(request,row,'تأیید علمی گروهی خانواده؛ بدون انتشار')
            self.message_user(request,f'{len(rows)} خانواده بررسی شد؛ {changed} مورد تأیید جدید ثبت شد. انتشار و مجوزها تغییری نکرد.',messages.SUCCESS)
        except DjangoValidationError as exc:
            self.message_user(request,'هیچ تغییری ثبت نشد: '+'؛ '.join(exc.messages)[:900],messages.ERROR)
        except Exception:
            logging.getLogger(__name__).exception('konkur families bulk approval failed')
            self.message_user(request,'خطای فنی؛ تراکنش بازگردانده شد. گزارش امن سرور را بررسی کنید.',messages.ERROR)
        return HttpResponseRedirect(request.path)
    def save_model(self,request,obj,form,change):
        if m.BankQuestion.objects.filter(family=obj,is_published=True).exists():
            raise PermissionDenied('برای ویرایش خانواده، ابتدا سؤالاتش را از انتشار خارج کنید.')
        super().save_model(request,obj,form,change)
