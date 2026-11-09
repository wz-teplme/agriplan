import copy
import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch


class EvaluationReportTests(unittest.TestCase):
    def core(self):
        return {'schema_version':1,'suite_id':'synthetic-v1','suite_hash':'a'*64,
                'evaluation_id':'test-evaluation','created_at':datetime.now(timezone.utc).isoformat(),
                'mode':'demo','provenance':'rule_generated','cases':[
                    {'id':'complete','title':'合成完整输入','status':'completed','snapshot':{'revision':1},
                     'draft':{'summary':'<script>alert(1)</script>','calculation':{'cost_cny':'19200.00'}},
                     'review':{'can_confirm':True},'actions':[],'questions':[],
                     'checks':[{'name':'exact_cost','passed':True,'expected':'19200.00','actual':'19200.00'}],
                     'elapsed_ms':1,'error_category':None},
                    {'id':'waiting','title':'等待确认','status':'waiting_input','snapshot':{},'draft':None,
                     'review':None,'actions':[],'questions':[{'field':'rate_kg_per_mu','status':'open'}],
                     'checks':[{'name':'waits_without_guessing','passed':True,'expected':'waiting_input','actual':'waiting_input'}],
                     'elapsed_ms':1,'error_category':None}],
                'telemetry':{'attempted_calls':0,'input_tokens':None,'output_tokens':None,'estimated_cost':None},
                'external_validation':'not_performed','professional_validation':'not_performed'}

    def report(self):
        from app.eval_report import finalize_report
        return finalize_report(self.core())

    def test_report_counts_expected_waiting_as_pass_and_keeps_professional_unverified(self):
        report=self.report()
        self.assertEqual(report['summary']['passed_cases'],2)
        self.assertEqual(report['summary']['failed_cases'],0)
        self.assertEqual(report['summary']['automatic_pass_rate'],1)
        self.assertEqual(report['professional_validation'],'not_performed')
        self.assertEqual(len(report['report_hash']),64)
        self.assertNotEqual(report['cases'][0]['case_hash'],report['cases'][1]['case_hash'])

    def test_missing_checks_false_checks_or_error_cannot_count_as_pass(self):
        from app.eval_report import finalize_report
        core=self.core()
        for mutation in ('missing','false','error'):
            with self.subTest(mutation=mutation):
                changed=copy.deepcopy(core)
                if mutation=='missing': changed['cases'][0]['checks']=[]
                if mutation=='false': changed['cases'][0]['checks'][0]['passed']=False
                if mutation=='error': changed['cases'][0]['error_category']='ValueError'
                report=finalize_report(changed)
                self.assertEqual(report['summary']['passed_cases'],1)
                self.assertEqual(report['summary']['failed_cases'],1)

    def test_outputs_escape_model_text_redact_key_and_do_not_overwrite_reports(self):
        from app.eval_report import finalize_report,save_report
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1]) as folder:
            with patch.dict(os.environ,{'AGRIPLAN_MODEL_API_KEY':'synthetic-secret-should-never-leak'}):
                core=self.core();core['cases'][0]['draft']['summary']+=" synthetic-secret-should-never-leak"
                report=finalize_report(core)
                first=save_report(report,folder);second=save_report(report,folder)
            self.assertNotEqual(first['json'],second['json'])
            html=Path(first['html']).read_text(encoding='utf-8')
            self.assertNotIn('<script>alert(1)</script>',html)
            self.assertIn('&lt;script&gt;',html)
            self.assertIn('未进行专业验证',html)
            for path in first.values():
                text=Path(path).read_text(encoding='utf-8')
                self.assertNotIn('synthetic-secret-should-never-leak',text)
            self.assertEqual(json.loads(Path(first['json']).read_text(encoding='utf-8'))['report_hash'],report['report_hash'])

    def template(self, report):
        from app.eval_report import review_template
        return review_template(report)

    def fill(self, template):
        result=copy.deepcopy(template)
        review=result['reviews'][0]
        review.update(reviewer='合成复核人员（非资质认证）',reviewed_at=datetime.now(timezone.utc).isoformat(),
                      notes='仅核对本地合成输入、引用及金额；不表示生产农艺效果验证。')
        review['dimensions']={key:'符合' for key in review['dimensions']}
        return result

    def test_blank_template_remains_pending_and_filled_record_does_not_certify_expertise(self):
        from app.eval_report import validate_reviews
        report=self.report();template=self.template(report)
        pending=validate_reviews(report,template)
        self.assertEqual(pending['pending'],2)
        self.assertEqual(pending['recorded'],0)
        recorded=validate_reviews(report,self.fill(template))
        self.assertEqual(recorded['pending'],1)
        self.assertEqual(recorded['recorded'],1)
        self.assertEqual(recorded['records'][0]['status'],'manual_recorded_unverified_identity')
        self.assertEqual(report['professional_validation'],'not_performed')

    def test_wrong_report_case_hash_duplicate_or_unknown_case_is_rejected(self):
        from app.eval_report import validate_reviews
        report=self.report();template=self.fill(self.template(report))
        for change in ('report','case','duplicate','unknown'):
            with self.subTest(change=change):
                invalid=copy.deepcopy(template)
                if change=='report': invalid['report_hash']='b'*64
                if change=='case': invalid['reviews'][0]['case_hash']='b'*64
                if change=='duplicate': invalid['reviews'].append(copy.deepcopy(invalid['reviews'][0]))
                if change=='unknown': invalid['reviews'][0]['case_id']='other-case'
                with self.assertRaises(ValueError):validate_reviews(report,invalid)

    def test_incomplete_review_future_time_and_unknown_grade_are_rejected(self):
        from app.eval_report import validate_reviews
        report=self.report();template=self.fill(self.template(report))
        for change in ('reviewer','dimension','notes','future','naive','grade'):
            with self.subTest(change=change):
                invalid=copy.deepcopy(template);review=invalid['reviews'][0]
                if change=='reviewer':review['reviewer']=''
                if change=='dimension':review['dimensions'].pop(next(iter(review['dimensions'])))
                if change=='notes':review['notes']=''
                if change=='future':review['reviewed_at']=(datetime.now(timezone.utc)+timedelta(days=1)).isoformat()
                if change=='naive':review['reviewed_at']='2026-01-01T12:00:00'
                if change=='grade':review['dimensions'][next(iter(review['dimensions']))]='肯定有效'
                with self.assertRaises(ValueError):validate_reviews(report,invalid)

    def test_modified_report_and_forged_automatic_summary_cannot_validate_review(self):
        from app.eval_report import validate_reviews,save_report
        report=self.report();template=self.template(report)
        report['cases'][0]['draft']['summary']='被改写'
        with self.assertRaises(ValueError):validate_reviews(report,template)
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1]) as folder:
            with self.assertRaises(ValueError):save_report(report,folder)

    def test_empty_duplicate_cases_and_nonboolean_check_results_are_rejected(self):
        from app.eval_report import finalize_report
        for change in ('empty','duplicate','truthy'):
            with self.subTest(change=change):
                core=self.core()
                if change=='empty':core['cases']=[]
                if change=='duplicate':core['cases'].append(copy.deepcopy(core['cases'][0]))
                if change=='truthy':core['cases'][0]['checks'][0]['passed']='false'
                with self.assertRaises(ValueError):finalize_report(core)


if __name__=='__main__':unittest.main()
