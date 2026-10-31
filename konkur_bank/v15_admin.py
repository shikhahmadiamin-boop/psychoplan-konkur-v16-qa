"""Minimal additive Django Admin UI: 4 tabs, each a one-shot publish flow."""
import logging
import zipfile
import xml.etree.ElementTree as ET
from django import forms
from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.http import Http404, FileResponse, HttpResponseRedirect
from django.template.response import TemplateResponse
from django.urls import path, reverse
from .v15_service import (MODES, LABELS, uploaded_xlsx, publish, toggle_publication,
    get_editable_question, edit_question, edit_collection_title,
    EDITABLE_QUESTION_FIELDS, EDITABLE_EXPLANATION_FIELDS)
from .v15_flags import portal_enabled, publish_enabled
from .v15_contract import validate_workbook
from .importer_validation import ValidationError

LOGGER=logging.getLogger(__name__)


class V15Form(forms.Form):
    package=forms.FileField(label='فایل اکسل یا ZIP (حداکثر ۲۰ مگابایت)')
    action=forms.ChoiceField(label='عملیات',choices=[('preview','فقط بررسی'),
        ('publish','تأیید نهایی و انتشار یک‌کلیکی')])


class EditQuestionForm(forms.Form):
    """Edits one occurrence in one prepared package, never another package."""
    original_sha=forms.CharField(widget=forms.HiddenInput())
    stem=forms.CharField(max_length=10000,widget=forms.Textarea(attrs={'rows':3}),label='متن سؤال')
    option_1=forms.CharField(max_length=3000,label='گزینه ۱')
    option_2=forms.CharField(max_length=3000,label='گزینه ۲')
    option_3=forms.CharField(max_length=3000,label='گزینه ۳')
    option_4=forms.CharField(max_length=3000,label='گزینه ۴')
    correct_option=forms.ChoiceField(choices=[(str(x),str(x)) for x in range(1,5)],label='کلید')
    lesson=forms.CharField(max_length=10000,widget=forms.Textarea(attrs={'rows':2}),label='آموزش')
    correct_reason=forms.CharField(max_length=10000,widget=forms.Textarea(attrs={'rows':2}),label='دلیل درستی')
    wrong_1=forms.CharField(max_length=10000,required=False,label='رد گزینه ۱')
    wrong_2=forms.CharField(max_length=10000,required=False,label='رد گزینه ۲')
    wrong_3=forms.CharField(max_length=10000,required=False,label='رد گزینه ۳')
    wrong_4=forms.CharField(max_length=10000,required=False,label='رد گزینه ۴')
    references=forms.CharField(max_length=10000,label='منابع')

    def clean(self):
        data=super().clean()
        correct=data.get('correct_option')
        if correct:
            for option in '1234':
                if option!=correct and not data.get('wrong_'+option):
                    self.add_error('wrong_'+option,'برای گزینه نادرست دلیل لازم است.')
        return data


def superuser(request):
    user=request.user
    return bool(user.is_authenticated and user.is_active and user.is_staff and user.is_superuser)


def add_to_existing_portals(official_admin,analysis_admin):
    """No proxy models, migrations, custom URLconfs or new Admin app registrations."""
    original=official_admin.get_urls

    def urls(self):
        local=[]
        for mode in MODES:
            local.append(path('bulk-v15/'+mode+'/',self.admin_site.admin_view(
                lambda request, selected=mode: v15_view(self,request,selected)),
                name='konkur_bank_v15_'+mode))
            local.append(path('bulk-v15/'+mode+'/template/',self.admin_site.admin_view(
                lambda request, selected=mode: template_view(request,selected)),
                name='konkur_bank_v15_template_'+mode))
        return local+original(self)

    official_admin.get_urls=urls

    def redirect_official(self,request,extra_context=None):
        if not superuser(request):raise PermissionDenied
        if not portal_enabled():
            return self.upload_view(request)
        return HttpResponseRedirect(reverse('admin:konkur_bank_v15_full_exams'))

    def redirect_analysis(self,request,extra_context=None):
        if not superuser(request):raise PermissionDenied
        if not portal_enabled():
            return self.upload_view(request)
        return HttpResponseRedirect(reverse('admin:konkur_bank_v15_families'))

    official_admin.changelist_view=redirect_official
    analysis_admin.changelist_view=redirect_analysis


def template_view(request,mode):
    if not superuser(request) or not portal_enabled():raise Http404
    from pathlib import Path
    directory=Path(__file__).resolve().parent/'resources'/'v15'
    names={
        'full_exams':'PsychoPlan_FULL_EXAM_V15.xlsx',
        'subjects':'PsychoPlan_SUBJECT_V15.xlsx',
        'chapters':'PsychoPlan_CHAPTER_V15.xlsx',
        'families':'PsychoPlan_SMART_ANALYSIS_V15.xlsx',
    }
    if mode not in names:raise Http404
    name=names[mode]
    try:return FileResponse((directory/name).open('rb'),as_attachment=True,filename=name)
    except FileNotFoundError:
        LOGGER.error('V15 template is missing: %s',mode)
        raise Http404


def v15_view(admin_instance,request,mode):
    if not superuser(request) or not portal_enabled():raise Http404
    if mode not in MODES:raise Http404
    form=V15Form(request.POST or None,request.FILES or None)
    from . import models as m
    selected_id=(request.GET.get('set') or request.POST.get('set') or '').strip()[:128]
    edit_q=(request.GET.get('edit_q') or request.POST.get('edit_q') or '').strip()[:128]
    edit_form=None
    if request.method=='POST' and request.POST.get('save_title'):
        if not publish_enabled():raise PermissionDenied
        try:
            edit_collection_title(mode,request.POST.get('save_title'),request.POST.get('new_title'),request.user)
            messages.success(request,'عنوان مجموعه به‌روز شد.')
        except ValidationError as exc:messages.error(request,str(exc)[:200])
        except Exception:
            LOGGER.exception('Konkur V15 title edit failure')
            messages.error(request,'ویرایش عنوان متوقف شد؛ گزارش امن سرور را بررسی کنید.')
        return HttpResponseRedirect(reverse('admin:konkur_bank_v15_'+mode))
    if request.method=='POST' and request.POST.get('save_question'):
        if not publish_enabled():raise PermissionDenied
        edit_form=EditQuestionForm(request.POST)
        if edit_form.is_valid():
            try:
                formdata=dict(edit_form.cleaned_data)
                prev=formdata.pop('original_sha')
                edit_question(mode,selected_id,edit_q,formdata,prev,request.user)
                messages.success(request,'این سؤال به‌روز شد؛ کارنامه‌های قبلی تغییر نکردند.')
                return HttpResponseRedirect(reverse('admin:konkur_bank_v15_'+mode)+'?set='+selected_id)
            except ValidationError as exc:edit_form.add_error(None,str(exc)[:250])
            except Exception:
                LOGGER.exception('Konkur V15 question edit failure')
                edit_form.add_error(None,'ویرایش متوقف شد؛ گزارش امن سرور را بررسی کنید.')
    if request.method=='POST' and request.POST.get('toggle_record'):
        if not publish_enabled():
            raise PermissionDenied('V15 publication is disabled')
        try:
            rid=request.POST['toggle_record']
            is_enable=request.POST.get('toggle_action')=='enable'
            if request.POST.get('toggle_action') not in ('enable','disable'):
                raise ValidationError('Invalid action')
            toggle_publication(mode,rid,request.user,is_enable)
            messages.success(request,'وضعیت انتشار این مجموعه تغییر کرد؛ کارنامه‌های قبلی حفظ شدند.')
            return HttpResponseRedirect(reverse('admin:konkur_bank_v15_'+mode))
        except ValidationError as exc:
            messages.error(request,str(exc)[:250])
            return HttpResponseRedirect(reverse('admin:konkur_bank_v15_'+mode))
        except Exception:
            LOGGER.exception('Konkur V15 toggle publication failure')
            messages.error(request,'تغییر انتشار انجام نشد؛ جزئیات در لاگ امن سرور است.')
            return HttpResponseRedirect(reverse('admin:konkur_bank_v15_'+mode))
    report=None
    published=None
    if request.method=='POST' and form.is_valid():
        try:
            action=form.cleaned_data['action']
            with uploaded_xlsx(form.cleaned_data['package']) as path:
                if action=='preview':
                    report=validate_workbook(path,mode)
                    messages.success(request,'فایل معتبر است. اطلاعاتی در دیتابیس ثبت یا منتشر نشد.')
                elif action=='publish':
                    if not publish_enabled():
                        raise ValidationError('انتشار V15 هنوز در تنظیمات سرور فعال نشده است.')
                    published=publish(path,mode,request.user)
                    messages.success(request,'بسته در یک تراکنش ثبت و منتشر شد.')
        except (ValidationError,zipfile.BadZipFile,ET.ParseError,ValueError,KeyError,IndexError,UnicodeError,OverflowError) as exc:
            # Never expose code paths or database internals; validation feedback is user-controlled.
            form.add_error('package',str(exc)[:350])
        except Exception:
            LOGGER.exception('Konkur V15 admin import/publish rejected unexpectedly')
            form.add_error(None,'ورود متوقف شد؛ هیچ انتشار نیمه‌کاره‌ای ثبت نشد. گزارش امن سرور را بررسی کنید.')
    search=(request.GET.get('q') or '').strip()[:80]
    if mode=='families':
        existing=m.QuestionFamily.objects.filter(metadata__v15_mode='families')
        if search:existing=existing.filter(family_id__icontains=search)
        recent=[{'id':f.pk,'title':(f.metadata or {}).get('public_topic',''),
                 'active':m.BankQuestion.objects.filter(family=f,is_published=True).exists()}
                for f in existing.order_by('family_id')[:50]]
    else:
        existing=m.BankCollection.objects.filter(metadata__v15_mode=mode)
        if search:existing=existing.filter(collection_id__icontains=search)
        recent=[{'id':c.pk,'title':(c.metadata or {}).get('title','') or
                 (c.metadata or {}).get('exam_id',''),'active':c.is_published}
                for c in existing.order_by('collection_id')[:50]]
    selected=None
    displayed_questions=[]
    if selected_id:
        if mode=='families':
            selected=m.QuestionFamily.objects.filter(pk=selected_id,metadata__v15_mode='families').first()
            if selected is not None:
                qs=m.BankQuestion.objects.filter(family_id=selected_id,origin='AUTHORED')
        else:
            selected=m.BankCollection.objects.filter(pk=selected_id,metadata__v15_mode=mode).first()
            if selected is not None:
                qs=m.BankQuestion.objects.filter(collectionitem__collection_id=selected_id,origin='OFFICIAL')
        if selected is not None:
            displayed_questions=list(qs.only('question_id','payload','sha256').order_by('question_id')[:100])
    if selected is not None and edit_q:
        try:
            q=get_editable_question(mode,selected_id,edit_q)
            if edit_form is None:
                exp=q.explanation.payload or {}
                edit_form=EditQuestionForm(initial={**(q.payload or {}),**exp,'original_sha':q.sha256})
        except ValidationError as exc:messages.error(request,str(exc)[:200])
    nav=[{'name':LABELS[name], 'url':reverse('admin:konkur_bank_v15_'+name),
          'selected':name==mode} for name in MODES]
    context={**admin_instance.admin_site.each_context(request),
        'title':LABELS[mode],'form':form,'report':report,'published':published,'recent':recent,'search':search,
        'nav':nav,'can_publish':publish_enabled(),
        'selected':selected,'selected_id':selected_id,'displayed_questions':displayed_questions,
        'edit_question_id':edit_q,'edit_form':edit_form,
        'template_link':reverse('admin:konkur_bank_v15_template_'+mode),
        'opts':admin_instance.model._meta,'is_popup':False}
    return TemplateResponse(request,'konkur_bank/v15_portal.html',context)
