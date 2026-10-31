"""Staff-only, read-only preview. NO learner-facing question/key endpoints until auth integration."""
from django.conf import settings
from django.http import JsonResponse
from django.views.decorators.http import require_GET
from .models import BankQuestion, BankCollection, QuestionFamily, ImportBatch

@require_GET
def staff_catalog(request):
    if not getattr(settings,'PP_KONKUR_FEATURE_ENABLED',False):
        return JsonResponse({'detail':'feature disabled'},status=404)
    if not request.user.is_authenticated or not (request.user.is_staff and request.user.is_superuser and request.user.is_active):
        return JsonResponse({'detail':'staff only'},status=403)
    return JsonResponse({
        'official_drafts':BankQuestion.objects.filter(origin='OFFICIAL',is_published=False).count(),
        'authored_drafts':BankQuestion.objects.filter(origin='AUTHORED',is_published=False).count(),
        'published':BankQuestion.objects.filter(is_published=True).count(),
        'collections':BankCollection.objects.count(),
        'families':QuestionFamily.objects.count(),
        'import_batches':ImportBatch.objects.count(),
    })
