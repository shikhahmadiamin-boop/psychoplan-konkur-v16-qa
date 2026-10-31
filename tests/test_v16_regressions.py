from pathlib import Path
import ast,unittest
ROOT=Path(__file__).resolve().parents[1]
class Regression(unittest.TestCase):
 def test_frontend_exactly_two_files(self):
  names={p.relative_to(ROOT/'frontend').as_posix() for p in (ROOT/'frontend').rglob('*') if p.is_file()}
  self.assertEqual(names,{'app/konkur-years/page.tsx','components/konkur-bank/useKonkurCatalog.ts'})
 def test_matching_opt_in(self):
  hook=(ROOT/'frontend/components/konkur-bank/useKonkurCatalog.ts').read_text()
  front=(ROOT/'frontend/app/konkur-years/page.tsx').read_text()
  v=(ROOT/'konkur_bank/student_views.py').read_text()
  self.assertIn('collection_mode=independent_v15',hook)
  self.assertIn('collection_id:row.collection_id',front)
  self.assertIn("request.query_params.get('collection_mode') == 'independent_v15'",v)
 def test_explicit_collection_identity_no_legacy_mix(self):
  s=(ROOT/'konkur_bank/v15_student.py').read_text()
  self.assertIn("if mode in ('subjects', 'chapters') and 'collection_id' not in selection:",s)
  self.assertIn('if not portal_enabled() or not independent:',s)
  self.assertIn('modes.setdefault(mode,[]).extend(replacement)',s)
  self.assertNotIn('replaced=set(groups[mode])',s)
 def test_flags_fail_closed(self):
  s=(ROOT/'konkur_bank/student_engine.py').read_text()
  self.assertIn("'collection_id' in selection and not portal_enabled()",s)
 def test_all_python_parses(self):
  for f in (ROOT/'konkur_bank').rglob('*.py'):
   with self.subTest(f=f.name):ast.parse(f.read_text())
if __name__=='__main__':unittest.main()
