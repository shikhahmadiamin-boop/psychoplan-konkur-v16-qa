from django.urls import path
from .views import staff_catalog
urlpatterns = [path('staff/catalog/',staff_catalog,name='konkur-staff-catalog')]
