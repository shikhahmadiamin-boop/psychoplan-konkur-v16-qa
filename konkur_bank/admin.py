"""Two explicit Django admin menu entries. Preview works without DB writes.
Import to DB is STAGING-only and requires two independent feature flags.
"""
from io import StringIO
import logging
import zipfile
from django import forms
from django.conf import settings
from django.contrib import admin, messages
from django.core.management import call_command
from django.http import Http404, HttpResponseRedirect
from django.template.response import TemplateResponse
from django.urls import path, reverse
from .models import OfficialImportPortal, AnalysisImportPortal, QuestionClassification
from .upload_service import prepare_package, preview_package
from .importer_validation import ValidationError

class PackageForm(forms.Form):
    package = forms.FileField(label='فایل اکسل یا ZIP')
    action = forms.ChoiceField(label='عملیات', choices=[('preview','فقط بررسی و پیش‌نمایش'),
                  ('save_draft','ثبت فقط پیش‌نویس (بدون انتشار)')], initial='preview')


class BasePortal(admin.ModelAdmin):
    change_list_template = 'admin/change_list.html'
    list_per_page = 30
    kind = None
    heading = None
    def has_module_permission(self, request):
        return bool(request.user.is_active and request.user.is_staff and request.user.is_superuser)
    def has_view_permission(self, request, obj=None):
        return self.has_module_permission(request)
    def has_add_permission(self, request): return False
    def has_change_permission(self, request, obj=None): return False
    def has_delete_permission(self, request, obj=None): return False
    def get_urls(self):
        opts=self.model._meta
        my=[path('upload/',self.admin_site.admin_view(self.upload_view),
            name=f'{opts.app_label}_{opts.model_name}_upload'),
            path('template/',self.admin_site.admin_view(self.template_view),
            name=f'{opts.app_label}_{opts.model_name}_template')]
        return my+super().get_urls()
    def template_view(self,request):
        from django.http import FileResponse
        from pathlib import Path
        from django.core.exceptions import PermissionDenied
        if not self.has_module_permission(request):
            raise PermissionDenied
        filename=('PsychoPlan_OFFICIAL_Bulk_Excel_Template.xlsx' if self.kind=='OFFICIAL_YEARLY'
                  else 'PsychoPlan_ANALYSIS_Bulk_Excel_Template.xlsx')
        path=Path(__file__).resolve().parent/'resources'/filename
        return FileResponse(path.open('rb'),as_attachment=True,filename=filename,
                            content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    def changelist_view(self, request, extra_context=None):
        opts=self.model._meta
        return HttpResponseRedirect(reverse(f'admin:{opts.app_label}_{opts.model_name}_upload'))
    def upload_view(self,request):
        if not self.has_module_permission(request) or not getattr(settings,'PP_KONKUR_ADMIN_PREVIEW_ENABLED',False):
            raise Http404()
        can_draft=bool(getattr(settings,'PP_KONKUR_ADMIN_DRAFT_IMPORT_ENABLED',False) is True)
        report=None
        form=PackageForm(request.POST or None,request.FILES or None)
        if request.method=='POST' and form.is_valid():
            try:
                with prepare_package(form.cleaned_data['package']) as path:
                    report=preview_package(path,self.kind,window=12)
                    if form.cleaned_data['action']=='save_draft':
                        if not can_draft:
                            raise ValidationError('Draft import disabled by server settings')
                        output=StringIO()
                        call_command('import_konkur_batch',str(path),commit_draft=True,stdout=output)
                        messages.success(request,'بسته فقط به‌صورت پیش‌نویس ثبت شد؛ هیچ انتشاری انجام نشد.')
                    else:
                        messages.success(request,'اعتبارسنجی انجام شد. فقط پیش‌نمایش؛ هیچ داده‌ای ثبت نشده است.')
            except (ValidationError,ValueError,OSError,zipfile.BadZipFile) as exc:
                form.add_error('package',str(exc))
            except Exception as exc:
                # Fail closed; don't leak paths, credentials or DB details in the web response.
                form.add_error(None,'ورود متوقف شد. گزارش خطا را فقط در لاگ امن مدیر بررسی کنید.')
                logging.getLogger(__name__).exception('Konkur admin import failed')
        from .models import BankQuestion as _PublishableQuestion
        _origin = 'OFFICIAL' if self.kind=='OFFICIAL_YEARLY' else 'AUTHORED'
        _published_qs = _PublishableQuestion.objects.filter(origin=_origin)
        _total_question_count = _published_qs.count()
        _published_question_count = _published_qs.filter(is_published=True).count()
        opts=self.model._meta
        context={**self.admin_site.each_context(request),
                 'publication_total':_total_question_count,
                 'publication_published':_published_question_count,
                 'publication_draft':_total_question_count-_published_question_count,
                 'title':self.heading, 'opts':opts,'form':form,'report':report,
                 'can_draft':can_draft,'kind':self.kind,
                 'template_link':reverse(f'admin:{opts.app_label}_{opts.model_name}_template'),
                 'year_span':'بازه ۱۲ ساله قابل تنظیم پس از تکمیل داده‌ها',
                 'is_popup':False}
        return TemplateResponse(request,'konkur_bank/admin_portal.html',context)

@admin.register(OfficialImportPortal)
class OfficialPortalAdmin(BasePortal):
    kind='OFFICIAL_YEARLY'
    heading='مدیریت کنکورهای سنوات گذشته'

@admin.register(AnalysisImportPortal)
class AnalysisPortalAdmin(BasePortal):
    kind='ANALYSIS_FAMILY'
    heading='مدیریت تحلیل هوشمند و سوالات تألیفی'


# Classification editor is a subpage reachable from the official portal only.
# Hidden from the admin index to keep precisely two main entries.
from django.utils import timezone
from django.core.exceptions import ValidationError as DjangoValidationError
from django import forms as djforms

class ClassificationForm(djforms.ModelForm):
    class Meta:
        model = QuestionClassification
        fields = ('chapter_code','topic_code','subtopic_code','secondary_tags','status','notes')
    def clean(self):
        data=super().clean()
        if data.get('status')=='APPROVED' and not (data.get('chapter_code') and data.get('topic_code')):
            raise DjangoValidationError('برای تأیید علمی، فصل و مبحث الزامی است.')
        return data

@admin.register(QuestionClassification)
class ClassificationAdmin(admin.ModelAdmin):
    form=ClassificationForm
    list_display=('question_id_col','chapter_code','topic_code','subtopic_code','status','reviewed_at')
    list_filter=('status',)
    search_fields=('question__question_id','chapter_code','topic_code','subtopic_code')
    readonly_fields=('question','reviewed_by','reviewed_at')
    list_per_page=40
    actions=('approve_selected_classifications',)
    @admin.action(description='تأیید علمی گروهی فصل و مبحث انتخاب‌شده (بدون انتشار)')
    def approve_selected_classifications(self, request, queryset):
        from django.db import transaction
        from .models import BankQuestion
        from django.core.exceptions import ValidationError, PermissionDenied
        if not self.has_change_permission(request):
            raise PermissionDenied('فقط مدیر ارشد مجاز است.')
        ids=list(queryset.values_list('pk',flat=True)[:201])
        if not ids or len(ids)>200 or len(set(ids))!=len(ids):
            self.message_user(request,'بین ۱ تا ۲۰۰ طبقه‌بندی معتبر را انتخاب کنید.',messages.ERROR)
            return HttpResponseRedirect(request.path)
        try:
            with transaction.atomic():
                rows=list(QuestionClassification.objects.filter(pk__in=ids).order_by('pk').select_for_update())
                if len(rows)!=len(ids):
                    raise ValidationError('فهرست انتخاب‌شده تغییر کرده است.')
                qs=BankQuestion.objects.filter(pk__in=[c.question_id for c in rows]).in_bulk()
                issues=[]
                for c in rows:
                    q=qs.get(c.question_id)
                    if not q or q.is_published:
                        issues.append(f'{c.question_id}: منتشرشده یا ناموجود')
                    elif c.status not in ('PENDING','APPROVED'):
                        issues.append(f'{c.question_id}: نیازمند اصلاح موردی')
                    elif not str(c.chapter_code or '').strip() or not str(c.topic_code or '').strip():
                        issues.append(f'{c.question_id}: فصل یا مبحث خالی')
                if issues:
                    raise ValidationError('؛ '.join(issues[:8]))
                changed=0
                for c in rows:
                    if c.status!='APPROVED' or c.reviewed_by_id!=request.user.pk or not c.reviewed_at:
                        c.status='APPROVED'
                        c.reviewed_by=request.user
                        c.reviewed_at=timezone.now()
                        c.save(update_fields=['status','reviewed_by','reviewed_at'])
                        changed+=1
                        self.log_change(request,c,'تأیید علمی گروهی طبقه‌بندی؛ بدون انتشار')
            self.message_user(request,f'طبقه‌بندی {len(rows)} مورد تأیید شد؛ {changed} مورد تغییر کرد. انتشار و مجوزها تغییری نکرد.',messages.SUCCESS)
        except ValidationError as exc:
            self.message_user(request,'هیچ تغییری ثبت نشد: '+'؛ '.join(exc.messages)[:900],messages.ERROR)
        except Exception:
            logging.getLogger(__name__).exception('konkur classification bulk approval failed')
            self.message_user(request,'خطای فنی؛ تراکنش بازگردانده شد. گزارش امن سرور را بررسی کنید.',messages.ERROR)
        return HttpResponseRedirect(request.path)
    def question_id_col(self,obj):return obj.question_id
    question_id_col.short_description='شناسه سؤال'
    def has_module_permission(self,request):return False
    def has_view_permission(self,request,obj=None):
        return request.user.is_active and request.user.is_staff and request.user.is_superuser
    def has_change_permission(self,request,obj=None):return self.has_view_permission(request,obj)
    def has_add_permission(self,request):return False
    def has_delete_permission(self,request,obj=None):return False
    def save_model(self,request,obj,form,change):
        # An accepted classification is attributable to the responsible admin.
        if obj.status=='APPROVED':
            obj.reviewed_by=request.user
            obj.reviewed_at=timezone.now()
        else:
            obj.reviewed_by=None
            obj.reviewed_at=None
        super().save_model(request,obj,form,change)

# PP_KONKUR_ADMIN_PUBLISH_V1
from . import admin_review

# PP Konkur V15: four independent pre-prepared upload pages (feature-flagged).
from .v15_admin import add_to_existing_portals
add_to_existing_portals(OfficialPortalAdmin, AnalysisPortalAdmin)
