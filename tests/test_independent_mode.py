"""Pure contract checks: existing callers unchanged, new callers isolated."""
import ast
from pathlib import Path
import types
import unittest

SOURCE=Path(__file__).resolve().parents[1]/'konkur_bank'


def function(name, filename, bindings=None):
    parsed=ast.parse((SOURCE/filename).read_text())
    node=next(node for node in parsed.body if isinstance(node,ast.FunctionDef) and node.name==name)
    snippet=ast.Module(body=[node],type_ignores=[])
    namespace=dict(bindings or {})
    exec(compile(snippet,filename,'exec'),namespace)
    return namespace[name]


class Coll:
    def __init__(self, pk, title, lo='1400',hi='1405'):
        self.pk=pk
        self.metadata={'title':title,'year_start':lo,'year_end':hi}


class Tests(unittest.TestCase):
    def setUp(self):
        self.cards=function('_catalog_cards','v15_student.py')
        self.key=('SCIENCE','MASTER','GROWTH')
        self.groups={self.key:[(Coll('CID-A','رشد مجموعه اول'),20),(Coll('CID-B','رشد مجموعه دوم'),30)]}
        self.years={'CID-A':{1405},'CID-B':{1404,1405}}

    def test_new_subject_catalog_has_two_independent_cards(self):
        cards=self.cards('subjects',self.groups,self.years,True)
        self.assertEqual(len(cards),2)
        self.assertEqual({c['collection_id'] for c in cards},{'CID-A','CID-B'})
        self.assertEqual([c['question_count'] for c in cards],[20,30])
        self.assertEqual([c['title'] for c in cards],['رشد مجموعه اول','رشد مجموعه دوم'])
        self.assertEqual([c['year_start'] for c in cards],[1400,1400])

    def test_old_subject_catalog_stays_merged(self):
        cards=self.cards('subjects',self.groups,self.years,False)
        self.assertEqual(len(cards),1)
        self.assertEqual(cards[0]['question_count'],50)
        self.assertNotIn('collection_id',cards[0])

    def test_new_chapter_catalog_does_not_merge(self):
        key=self.key+('IDENTITY','MAR')
        cards=self.cards('chapters',{key:self.groups[self.key]},self.years,True)
        self.assertEqual(len(cards),2)
        self.assertEqual({row['topic'] for row in cards},{'MAR'})

    def test_missing_legacy_full_exam_remains_eligible_for_legacy_fallback(self):
        source=(SOURCE/'v15_student.py').read_text()
        self.assertIn("if 'collection_id' in selection:",source)
        self.assertNotIn("or mode == 'full_exams'",source)

    def test_client_opt_in_and_fail_closed_for_id(self):
        s=(SOURCE/'student_views.py').read_text()
        self.assertIn("request.query_params.get('collection_mode') == 'independent_v15'",s)
        p=(SOURCE/'student_engine.py').read_text()
        self.assertIn("allowed = allowed | {'collection_id'}",p)
        v=(SOURCE/'v15_student.py').read_text()
        self.assertIn("qs = qs.filter(pk=selection['collection_id'])",v)

    def test_selector_with_collection_id_validates(self):
        validate=function('validate_selection','student_engine.py',
                          {'ALLOWED_MODES':{'chapters','subjects','full_exams','families'}})
        sel={'exam_body':'SCIENCE','degree':'MASTER','subject_code':'GROWTH',
             'year_start':1400,'year_end':1405,'collection_id':'CID-A'}
        self.assertTrue(validate('subjects',sel))
        self.assertTrue(validate('subjects',{k:v for k,v in sel.items() if k!='collection_id'}))
        with self.assertRaises(ValueError):
            validate('subjects',{**sel,'collection_id':53})
        with self.assertRaises(ValueError):
            validate('subjects',{**sel,'extra':'x'})

    def test_unrecognized_explicit_collection_does_not_fall_back(self):
        class Empty:
            def exists(self):return False
        f=function('independent_eligible_questions','v15_student.py',{
            'portal_enabled':lambda:True,
            'MODES':('full_exams','subjects','chapters'),
            '_candidate_collections':lambda mode,sel:Empty(),
        })
        with self.assertRaisesRegex(ValueError,'در دسترس'):
            f('subjects',{'collection_id':'CID-missing'})
        self.assertIsNone(f('subjects',{'subject_code':'GROWTH'}))
        self.assertIsNone(f('full_exams',{'exam_id':'LEGACY-1405'}))

    def test_bad_subject_window_is_omitted(self):
        groups={self.key:[(Coll('CID-X','invalid','1398','1435'),20)]}
        self.assertEqual(self.cards('subjects',groups,{'CID-X':{1405}},True),[])

if __name__=='__main__':unittest.main()
