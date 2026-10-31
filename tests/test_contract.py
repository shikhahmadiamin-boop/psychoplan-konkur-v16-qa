import copy,sys,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from konkur_bank.v15_contract import validate_workbook
from konkur_bank.importer_validation import ValidationError

BASE_HEADERS=['question_id','exam_id','year','exam_body','degree','original_number','subject_code','stem','option_1','option_2','option_3','option_4','correct_option','key_source','lesson','correct_reason','wrong_1','wrong_2','wrong_3','wrong_4','references']

def question(n='95',original='95'):
    d={'question_id':f'SCI-MA-1405-{n}','exam_id':'SCI-MA-1405','year':'1405','exam_body':'SCI','degree':'MA','original_number':str(original),'subject_code':'GROWTH','stem':'Identity by Marcia', 'option_1':'A','option_2':'B','option_3':'C','option_4':'D','correct_option':'2','key_source':'official','lesson':'explanation','correct_reason':'because B', 'wrong_1':'because not A','wrong_2':'', 'wrong_3':'because not C', 'wrong_4':'because not D','references':'source'}
    return [d.get(k,'') for k in BASE_HEADERS]

def grid(mode='subjects'):
    manifest=[['field','value','guidance'],['schema','psychoplan-konkur-v13',''],['mode',mode,''],['package_id','PKG-TEST-001','']]
    if mode=='full_exams':
        q=question('95','1');return {'manifest':manifest, 'sources':[['exam_id','year','exam_body','degree','expected_total_questions'],['SCI-MA-1405','1405','SCI','MA','1']], 'questions':[BASE_HEADERS,q]}
    if mode=='subjects':
        h=['collection_id']+BASE_HEADERS
        return {'manifest':manifest,'collections':[['collection_id','title','exam_body','degree','subject_code','year_start','year_end'], ['C-01','Growth','SCI','MA','GROWTH','1405','1405']], 'questions':[h,['C-01']+question()]}
    if mode=='chapters':
        h=['collection_id','chapter_code','topic_code','subtopic_code']+BASE_HEADERS
        return {'manifest':manifest,'collections':[['collection_id','title','exam_body','degree','subject_code','chapter_code','topic_code'], ['C-01','Identity','SCI','MA','GROWTH','GROWTH','IDENTITY']], 'questions':[h,['C-01','GROWTH','IDENTITY','MAR']+question()]}
    h=['family_id','question_id','related_official_id','concept_codes','stem','option_1','option_2','option_3','option_4','correct_option','lesson','correct_reason','wrong_1','wrong_2','wrong_3','wrong_4','references']
    row=['F-01','AUTH-01','SCI-MA-1405-095','MAR','Authored Q','A','B','C','D','1','explanation','A','', 'not B','not C','not D','book']
    return {'manifest':manifest,'families':[['family_id','public_topic','related_official_id','concept_codes'],['F-01','identity','SCI-MA-1405-095','MAR']], 'questions':[h,row]}

def validate(rows,mode=None):
    with tempfile.TemporaryDirectory() as t:
        p=Path(t)/'placeholder.xlsx';p.write_bytes(b'test')
        with patch('konkur_bank.v15_contract.parse_workbook',return_value=rows):
            return validate_workbook(p,mode)

class IndependentInputs(unittest.TestCase):
    def test_all_four_routes(self):
        for mode in ('full_exams','subjects','chapters','families'):
            with self.subTest(mode=mode):self.assertEqual(validate(grid(mode),mode)['question_count'],1)
    def test_repeated_same_question_in_two_subject_collections(self):
        g=grid('subjects');g['collections'].append(['C-02','Other','SCI','MA','GROWTH','1405','1405']);g['questions'].append(['C-02']+question())
        self.assertEqual(validate(g,'subjects')['question_count'],2)
    def test_repeated_same_official_id_with_different_scientific_content_across_collections(self):
        g=grid('subjects');g['collections'].append(['C-02','Other','SCI','MA','GROWTH','1405','1405'])
        duplicate=['C-02']+question();duplicate[g['questions'][0].index('stem')]='Editor prepared different text'
        duplicate[g['questions'][0].index('correct_option')]='3'
        duplicate[g['questions'][0].index('wrong_2')]='because not B'
        duplicate[g['questions'][0].index('wrong_3')]=''
        g['questions'].append(duplicate)
        self.assertEqual(validate(g,'subjects')['question_count'],2)
    def test_identical_text_with_two_official_ids_is_accepted(self):
        g=grid('subjects');second=['C-01']+question('96','96');g['questions'].append(second)
        self.assertEqual(validate(g,'subjects')['question_count'],2)
    def test_repeat_same_id_within_same_collection_is_rejected(self):
        g=grid('subjects');g['questions'].append(copy.deepcopy(g['questions'][1]));
        with self.assertRaises(ValidationError):validate(g,'subjects')
    def test_same_official_question_in_two_chapters_accepted(self):
        g=grid('chapters');g['collections'].append(['C-02','Alt','SCI','MA','GROWTH','GROWTH','MAR'])
        g['questions'].append(['C-02','GROWTH','MAR','DIFF']+question())
        self.assertEqual(validate(g,'chapters')['question_count'],2)
    def test_same_authored_question_in_two_families_accepted(self):
        g=grid('families');g['families'].append(['F-02','identity 2','SCI-MA-1405-095','MAR'])
        row=copy.deepcopy(g['questions'][1]);row[0]='F-02';g['questions'].append(row)
        self.assertEqual(validate(g,'families')['question_count'],2)
    def test_full_exam_missing_position_rejected_structurally(self):
        g=grid('full_exams');g['sources'][1][4]='2'
        with self.assertRaises(ValidationError):validate(g,'full_exams')
    def test_invalid_option_rejected_structurally(self):
        g=grid('subjects');g['questions'][1][g['questions'][0].index('correct_option')]='8'
        with self.assertRaises(ValidationError):validate(g,'subjects')
    def test_cross_mode_wrong_file_rejected(self):
        with self.assertRaises(ValidationError):validate(grid('chapters'),'subjects')
    def test_missing_necessary_text_rejected(self):
        g=grid('subjects');g['questions'][1][g['questions'][0].index('stem')]=''
        with self.assertRaises(ValidationError):validate(g,'subjects')

if __name__=='__main__':unittest.main()
