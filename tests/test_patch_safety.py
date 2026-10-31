import ast,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
MOD=ROOT/'konkur_bank'

def src(name):return (MOD/name).read_text(encoding='utf-8')

class Safety(unittest.TestCase):
    def test_compiles_all(self):
        for f in MOD.glob('*.py'):
            with self.subTest(f=f.name):ast.parse(f.read_text(encoding='utf8'))
    def test_new_flags_fail_closed(self):
        s=src('v15_flags.py')
        self.assertIn("PP_KONKUR_V15_ENABLED', False",s)
        self.assertIn("PP_KONKUR_V15_PUBLISH_ENABLED', False",s)
        self.assertIn(".pp_v15_portal_enabled",s)
    def test_student_flags_gate_both_paths(self):
        self.assertIn('if portal_enabled():',src('student_engine.py'))
        self.assertIn('if portal_enabled():',src('student_views.py'))
        self.assertIn("if payload.get('v15_package_id'):",src('student_views.py'))
    def test_full_exam_unique_selector(self):
        s=src('v15_student.py')
        self.assertIn("return (str(collection_id),)",s)
        self.assertIn("qs.filter(pk=selection['exam_id'])",s)
        self.assertIn('exam_id=internal_id',s)
        # No fallback to old official exam ID for V15 groups.
        self.assertNotIn("filter(metadata__exam_id=selection['exam_id'])",s)
    def test_catalog_doesnt_load_all_memberships(self):
        s=src('v15_student.py')
        self.assertIn('Count(',s)
        self.assertIn('.distinct()',s)
        self.assertNotIn('all_ids=',s)
        self.assertNotIn('bycollection=',s)
        self.assertNotIn('select_related(\'question\',\'question__exam_source\')',s)
    def test_edit_is_scoped_and_stale_safe(self):
        s=src('v15_service.py')
        self.assertIn('with transaction.atomic():',s)
        self.assertIn('if q.sha256!=previous_sha:',s)
        self.assertIn('collectionitem__collection_id=collection_id',s)
        self.assertIn('QuestionRevision.objects.create',s)
        self.assertIn('ExplanationRevision.objects.create',s)
    def test_publish_atomic_and_no_inference(self):
        s=src('v15_service.py')
        self.assertIn('m.ImportBatch.objects.create',s)
        self.assertIn('with transaction.atomic():',s)
        for unwanted in ('difflib','fuzzy','sentence_transformer','sklearn','embeddings'):
            self.assertNotIn(unwanted,s)
    def test_admin_permission_csrf_and_four_routes(self):
        s=src('v15_admin.py');html=(MOD/'templates/konkur_bank/v15_portal.html').read_text()
        self.assertIn('admin_site.admin_view',s)
        self.assertIn('def superuser(',s)
        self.assertIn('if not publish_enabled()',s)
        self.assertIn('csrf_token',html)
        for name in ('full_exams','subjects','chapters','families'):
            self.assertIn(name,src('v15_contract.py'))
    def test_no_migration_next_or_massive_build(self):
        self.assertFalse(list(MOD.rglob('migrations')))
        self.assertFalse(list(ROOT.rglob('node_modules')))
        self.assertFalse((MOD/'models.py').exists())
    def test_rename_protected(self):
        self.assertIn('update_fields',src('v15_service.py'))

if __name__=='__main__':unittest.main()
