"""Ephemeral PostgreSQL CI: no production credentials, no production network access."""
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
assert os.environ.get('CI')=='true', 'CI-only test: refusing to run outside CI'
assert os.environ.get('QA_DB_HOST') in ('127.0.0.1','localhost')
assert os.environ.get('QA_DB_NAME')=='psychoplan_v16_ci'
assert os.environ.get('QA_DB_USER')=='v16_tester'

from django.conf import settings
settings.configure(
    SECRET_KEY='test-only-not-used-in-production',
    ALLOWED_HOSTS=['testserver'],
    ROOT_URLCONF='konkur_bank.urls',
    INSTALLED_APPS=['django.contrib.admin','django.contrib.auth',
      'django.contrib.contenttypes','django.contrib.sessions',
      'django.contrib.messages','rest_framework','konkur_bank'],
    DATABASES={'default':{
        'ENGINE':'django.db.backends.postgresql',
        'NAME':os.environ['QA_DB_NAME'],
        'USER':os.environ['QA_DB_USER'],
        'PASSWORD':os.environ['QA_DB_PASSWORD'],
        'HOST':os.environ['QA_DB_HOST'],'PORT':'5432',
        'CONN_MAX_AGE':0,
    }},
    TEMPLATES=[{'BACKEND':'django.template.backends.django.DjangoTemplates',
      'APP_DIRS':True,'OPTIONS':{'context_processors':[
      'django.template.context_processors.request',
      'django.contrib.auth.context_processors.auth',
      'django.contrib.messages.context_processors.messages']}}],
    MIDDLEWARE=[
      'django.contrib.sessions.middleware.SessionMiddleware',
      'django.middleware.csrf.CsrfViewMiddleware',
      'django.contrib.auth.middleware.AuthenticationMiddleware',
      'django.contrib.messages.middleware.MessageMiddleware'],
    DEFAULT_AUTO_FIELD='django.db.models.BigAutoField',
    USE_TZ=True,
    STATIC_URL='/static/',
)
import django
django.setup()
from django.core.management import call_command
from django.db import connection
from django.contrib.auth import get_user_model
from openpyxl import Workbook
from konkur_bank import models as m, v15_service as svc
from tests.test_contract import grid

assert connection.vendor=='postgresql'
with connection.cursor() as cur:
    cur.execute('SELECT current_database(), current_user, inet_server_addr()')
    database, dbuser, addr=cur.fetchone()
    assert database=='psychoplan_v16_ci' and dbuser=='v16_tester'
print('ISOLATED_POSTGRESQL_17: PASS',flush=True)
# This copy of the schema has no migrations; run_syncdb creates only empty test tables.
call_command('migrate',run_syncdb=True,interactive=False,verbosity=0)
print('FRESH_TEST_SCHEMA: PASS',flush=True)
admin=get_user_model().objects.create_superuser(username='v16_qa',email='qa@example.invalid',password='ephemeral-qa-only')

def workbook(mode, package_id, folder):
    data=grid(mode)
    # Only the package ID changes; all scientific content is test fixture text.
    data['manifest'][3][1]=package_id
    wb=Workbook()
    wb.remove(wb.active)
    for name,rows in data.items():
        ws=wb.create_sheet(name)
        for row in rows:ws.append(row)
    output=folder/(package_id+'.xlsx')
    wb.save(output)
    return output

with tempfile.TemporaryDirectory(prefix='v16-pg-test-') as temporary:
    folder=Path(temporary)
    # Real XLSX -> real parser -> ORM -> transaction on isolated PostgreSQL.
    for mode in ('full_exams','subjects','chapters','families'):
        source=workbook(mode,'QA-'+mode.upper(),folder)
        outcome=svc.publish(source,mode,admin)
        assert outcome['status']=='PUBLISHED', outcome
        again=svc.publish(source,mode,admin)
        assert again['status']=='ALREADY_PUBLISHED',again
        print('POSTGRES_PUBLISH_'+mode.upper()+': PASS',flush=True)
    assert m.ImportBatch.objects.count()==4
    assert m.BankQuestion.objects.count()==4
    ids=[q.payload['source_question_id'] for q in m.BankQuestion.objects.filter(origin='OFFICIAL')]
    assert len(ids)==3 and len(set(ids))==1
    print('CROSS_COLLECTION_DUPLICATE_ALLOWED: PASS',flush=True)
    collections={c.metadata['v15_mode']:c for c in m.BankCollection.objects.all()}
    assert all(collections[x].is_published for x in ('full_exams','subjects','chapters'))
    first=collections['full_exams']
    svc.toggle_publication('full_exams',first.pk,admin,False)
    first.refresh_from_db()
    assert not first.is_published
    assert collections['subjects'].is_published and collections['chapters'].is_published
    print('INDEPENDENT_UNPUBLISH: PASS',flush=True)
    models=(m.BankQuestion,m.ExamSource,m.BankCollection,m.CollectionItem,m.ImportBatch)
    before=[x.objects.count() for x in models]
    broken=workbook('full_exams','QA-ROLLBACK',folder)
    with patch.object(svc,'_save_question',side_effect=RuntimeError('forced QA failure')):
        try:svc.publish(broken,'full_exams',admin)
        except RuntimeError:pass
        else:raise AssertionError('Expected failure not raised')
    assert before==[x.objects.count() for x in models]
    print('POSTGRES_ATOMIC_ROLLBACK: PASS',flush=True)
print('NO_PRODUCTION_DATABASE_ACCESS: YES',flush=True)
