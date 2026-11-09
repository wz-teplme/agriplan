"""Synthetic evaluation exercises the real services and durable worker, offline."""
import contextlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]


class EvaluationTests(unittest.TestCase):
    def test_eight_cases_keep_expected_gaps_and_exact_server_calculations(self):
        from app.evaluation import run_suite
        core = run_suite()
        cases = {case['id']: case for case in core['cases']}
        self.assertEqual(len(cases), 8)
        self.assertEqual(core['provenance'], 'rule_generated')
        self.assertEqual(core['external_validation'], 'not_performed')
        self.assertEqual(core['professional_validation'], 'not_performed')
        self.assertEqual(core['telemetry']['attempted_calls'], 0)
        for case in cases.values():
            self.assertIsNone(case['error_category'], case)
            self.assertTrue(case['checks'])
            self.assertTrue(all(check['passed'] for check in case['checks']), case)
            self.assertTrue(case['run_id'])
            self.assertTrue(all(set(action) == {'type','generation','purpose'} for action in case['actions']))
        self.assertEqual(cases['complete']['draft']['calculation']['cost_cny'], '19200.00')
        self.assertEqual(cases['two_products']['draft']['calculation']['cooperation_total_cny'], '25350.00')
        self.assertEqual(cases['area_refresh']['draft']['calculation']['cost_cny'], '19264.00')
        self.assertEqual(cases['zero_evidence']['status'], 'limited')
        self.assertFalse(cases['zero_evidence']['review']['can_confirm'])
        self.assertEqual(cases['over_budget']['status'], 'limited')
        missing = cases['missing_rate']
        self.assertEqual(missing['status'], 'waiting_input')
        self.assertIsNone(missing['draft'])
        self.assertIn({'field':'rate_kg_per_mu','status':'open'}, missing['questions'])
        self.assertFalse(any(a['type'] in ('calculate','calculate_quantity') for a in missing['actions']))
        proof = cases['area_refresh']['context']['refresh']
        for module in ('history','evidence','market'):
            self.assertEqual(proof['before'][module], proof['after'][module])
        for module in ('quantity','budget','candidate'):
            self.assertNotEqual(proof['before'][module], proof['after'][module])
        self.assertEqual(cases['injection_note']['draft']['calculation']['cost_cny'], '19200.00')

    def test_demo_ignores_business_database_and_restores_existing_llm_mode(self):
        from app.evaluation import run_suite
        with tempfile.TemporaryDirectory(dir=ROOT) as folder:
            sentinel = Path(folder)/'production.db'
            sentinel.write_bytes(b'never open this production database')
            before = sentinel.read_bytes()
            config = {'AGRIPLAN_MODE':'llm','AGRIPLAN_DB':str(sentinel),
                      'AGRIPLAN_MODEL_BASE_URL':'https://provider.invalid/v1',
                      'AGRIPLAN_MODEL':'example','AGRIPLAN_MODEL_API_KEY':'synthetic-secret'}
            with patch.dict(os.environ, config), patch('httpx.Client', side_effect=AssertionError('network forbidden')):
                core = run_suite(case_ids=['complete','zero_evidence'])
                self.assertEqual(os.environ['AGRIPLAN_MODE'], 'llm')
            self.assertEqual(sentinel.read_bytes(), before)
            self.assertEqual([c['status'] for c in core['cases']], ['completed','limited'])
            self.assertEqual(len({c['run_id'] for c in core['cases']}), 2)

    def test_live_requires_explicit_finite_budgets_permission_and_valid_configuration(self):
        from app.evaluation import run_suite
        config = {'AGRIPLAN_MODEL_BASE_URL':'https://provider.invalid/v1',
                  'AGRIPLAN_MODEL':'example','AGRIPLAN_MODEL_API_KEY':'synthetic-secret'}
        with patch.dict(os.environ, config), patch('httpx.Client', side_effect=AssertionError('network forbidden')):
            for kwargs in ({}, {'allow_network':True}, {'allow_network':True,'max_calls':1},
                           {'allow_network':True,'max_calls':0,'max_seconds':1},
                           {'allow_network':True,'max_calls':1,'max_seconds':float('inf')},
                           {'allow_network':True,'max_calls':True,'max_seconds':1}):
                with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                    run_suite(mode='live', **kwargs)
            for address in ('http://provider.invalid','https://name:password@provider.invalid','https://', 'https://provider.invalid:bad'):
                with patch.dict(os.environ, {'AGRIPLAN_MODEL_BASE_URL':address}), self.assertRaises(ValueError):
                    run_suite(mode='live', allow_network=True, max_calls=1, max_seconds=1)
            with patch.dict(os.environ, {'AGRIPLAN_MODEL_API_KEY':''}), self.assertRaises(ValueError):
                run_suite(mode='live', allow_network=True, max_calls=1, max_seconds=1)

    def test_unknown_or_duplicate_case_selection_is_rejected(self):
        from app.evaluation import run_suite
        for selection in (['unknown'], ['complete','complete'], []):
            with self.subTest(selection=selection), self.assertRaises(ValueError):
                run_suite(case_ids=selection)

    def test_preflight_contains_only_presence_booleans(self):
        from app.evaluation import preflight
        with patch.dict(os.environ, {'AGRIPLAN_MODEL_API_KEY':'secret-value'}):
            status = preflight()
        self.assertTrue(status['AGRIPLAN_MODEL_API_KEY'])
        self.assertTrue(all(type(value) is bool for value in status.values()))
        self.assertNotIn('secret-value', json.dumps(status))

    def test_cli_preflight_and_invalid_live_are_safe_and_demo_writes_report(self):
        import evaluate
        with contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(evaluate.main(['--preflight']), 0)
        self.assertTrue(all(type(value) is bool for value in json.loads(output.getvalue()).values()))
        with contextlib.redirect_stderr(io.StringIO()), patch('httpx.Client', side_effect=AssertionError('network forbidden')):
            self.assertEqual(evaluate.main(['--mode','live']), 2)
        with tempfile.TemporaryDirectory(dir=ROOT) as folder, contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(evaluate.main(['--case','complete','--output',folder]), 0)
            paths = json.loads(output.getvalue())
            self.assertTrue(Path(paths['html']).is_file())
            original = Path(paths['json']).read_bytes()
            with contextlib.redirect_stdout(io.StringIO()) as reviewed:
                self.assertEqual(evaluate.main(['--review', paths['json'], '--review-file',paths['review'],'--output',folder]), 0)
            self.assertNotEqual(json.loads(reviewed.getvalue())['json'], paths['json'])
            self.assertEqual(Path(paths['json']).read_bytes(), original)
            template = json.loads(Path(paths['review']).read_text(encoding='utf-8'))
            template['report_hash'] = '0'*64
            bad = Path(folder)/'invalid-review.json'
            bad.write_text(json.dumps(template), encoding='utf-8')
            with contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(evaluate.main(['--review',paths['json'],'--review-file',str(bad),'--output',folder]), 2)
