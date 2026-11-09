"""Candidate review checks run against an isolated SQLite database."""
import copy
import importlib.util
import json
import sqlite3
import unittest


class CandidateReviewTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(':memory:')
        self.db.row_factory = sqlite3.Row
        self.db.executescript('''
            CREATE TABLE seasons(id TEXT PRIMARY KEY, revision INTEGER);
            CREATE TABLE products(id TEXT PRIMARY KEY, name TEXT, price TEXT, unit TEXT, revision INTEGER, active INTEGER);
            CREATE TABLE knowledge(id TEXT PRIMARY KEY, title TEXT, body TEXT, crop TEXT, source TEXT, locator TEXT, version TEXT, metadata TEXT);
            CREATE TABLE candidate_reviews(id TEXT PRIMARY KEY, run_id TEXT, candidate_version INTEGER,
                content_hash TEXT, input_revision INTEGER, evidence_fingerprint TEXT, rule_version TEXT,
                dimensions TEXT, issues TEXT, can_confirm INTEGER, created_at TEXT);
            INSERT INTO seasons VALUES('s1',1);
            INSERT INTO products VALUES('fert-1','产品','3.20','CNY/kg',1,1);
        ''')
        from app.knowledge import ensure_schema
        ensure_schema(self.db)
        self.run = {'id':'r1', 'season_id':'s1', 'candidate_version':1, 'snapshot':{
            'revision':1, 'crop':'玉米', 'region':'吉林', 'customer_id':'c1',
            'facts':{'area_mu':'300','rate_kg_per_mu':'20','rate_source':'SR 复核参数',
                     'budget_cny':'26000','budget_basis':'company_cooperation_amount',
                     'variety':'客户自有种子','product_id':'fert-1','soil_ph':None},
            'product':{'id':'fert-1','name':'产品','price':'3.20','unit':'CNY/kg','revision':1,'active':1}}}
        self.draft = {'schema_version':2,'summary':'本季流程草稿','input_revision':1,
            'variety':'客户自有种子','product':copy.deepcopy(self.run['snapshot']['product']),
            'calculation':{'id':'calc1','total_kg':'6000','cost_cny':'19200.00',
                'quantity_unit':'kg','money_unit':'CNY','rule_version':'quantity-cost-v1',
                'inputs':{'area_mu':'300','rate_kg_per_mu':'20','price_cny_per_kg':'3.20'},
                'input_refs':{'season_id':'s1','fact_revision':1,'product_id':'fert-1','product_revision':1,'rate_source':'SR 复核参数'}},
            'constraints':{'variety':'客户自有种子','budget_cny':'26000','budget_basis':'company_cooperation_amount'},
            'history_decisions':[], 'pending_decisions':[], 'warnings':[],
            'stages':[{'name':'巡查','timing':'本季','action':'核实现场并记录。'}],
            'evidence_refs':['doc1'], 'measures':[{'id':'m1','kind':'procedure','text':'核实现场并记录。',
                'evidence_refs':['doc1'],'fact_refs':[],'calculation_refs':[],'conditions':[]}]}

    def tearDown(self):
        self.db.close()

    def review(self, draft=None):
        from app.review import inspect_candidate
        return inspect_candidate(self.db, self.run, self.draft if draft is None else draft)

    def codes(self, draft=None):
        return {i['code'] for i in self.review(draft)['issues']}

    def document(self, **changes):
        values = {'id':'doc1','document_id':'doc1','title':'巡查流程','body':'核实现场并记录。','crop':'玉米',
                  'source':'SR 审核服务规范','locator':'第一节','version':'v1','status':'published',
                  'kind':'procedure','purpose':'plan_research','region':'吉林','revision':2,
                  'content_hash':'body-v1','supported_claims':json.dumps(['核实现场并记录。']),
                  'conditions':'[]','demo':0} | changes
        self.db.execute('INSERT OR REPLACE INTO knowledge('+','.join(values)+') VALUES('+','.join('?' for _ in values)+')', tuple(values.values()))

    def test_missing_evidence_blocks_confirmation_and_reports_closure(self):
        self.assertIsNotNone(importlib.util.find_spec('app.review'), 'candidate review module is required')
        from app.review import inspect_candidate
        self.draft['evidence_refs'] = []
        self.draft['measures'][0]['evidence_refs'] = []
        review = inspect_candidate(self.db, self.run, self.draft)
        self.assertFalse(review['can_confirm'])
        issue = next(i for i in review['issues'] if i['code']=='EVIDENCE_REQUIRED')
        self.assertEqual(issue['severity'], 'blocking')
        self.assertTrue(issue['close_condition'])
        self.assertEqual(issue['responsible_role'], 'SR')
        self.assertEqual(issue['references'], [issue['field']])

    def test_numeric_values_units_and_calculation_lineage_are_recomputed(self):
        self.draft['evidence_refs'] = []
        cases = [('total_kg','6001'), ('cost_cny','19200.01'), ('money_unit','USD'),
                 ('quantity_unit','tonne'), ('input_refs',{}), ('inputs',{})]
        for field, value in cases:
            with self.subTest(field=field):
                draft = copy.deepcopy(self.draft)
                draft['calculation'][field] = value
                self.assertIn('CALCULATION_MISMATCH', self.codes(draft))

    def test_rate_source_and_over_budget_cannot_be_removed_from_warnings(self):
        self.draft['evidence_refs'] = []
        self.run['snapshot']['facts']['budget_cny'] = '19000'
        self.draft['over_budget'] = False
        self.assertIn('BUDGET_EXCEEDED', self.codes())
        self.run['snapshot']['facts']['rate_source'] = ''
        self.assertIn('RATE_SOURCE_REQUIRED', self.codes())
        self.run['snapshot']['facts']['rate_kg_per_mu'] = None
        self.assertIn('RATE_CONFIRMATION_REQUIRED', self.codes())

    def test_stale_input_and_product_versions_are_blocking(self):
        self.draft['evidence_refs'] = []
        self.db.execute('UPDATE seasons SET revision=2')
        self.assertIn('INPUT_REVISION_STALE', self.codes())
        self.db.execute('UPDATE products SET revision=2')
        self.assertIn('PRODUCT_VERSION_CHANGED', self.codes())

    def test_legacy_draft_unknown_measure_and_missing_readiness_do_not_pass(self):
        self.draft['evidence_refs'] = []
        draft = copy.deepcopy(self.draft)
        draft.pop('measures')
        self.assertIn('STRUCTURED_DRAFT_REQUIRED', self.codes(draft))
        draft = copy.deepcopy(self.draft)
        draft['measures'][0]['kind'] = 'model_approved'
        self.assertIn('MEASURE_KIND_INVALID', self.codes(draft))
        draft = copy.deepcopy(self.draft)
        draft['stages'] = []
        self.assertIn('EXECUTION_STAGES_REQUIRED', self.codes(draft))

    def test_missing_condition_and_hard_constraints_are_not_self_certified(self):
        self.draft['evidence_refs'] = []
        self.draft['measures'][0]['conditions'] = [{'field':'soil_ph','value':'6.5','verified':True}]
        self.assertIn('CONDITION_REVIEW_REQUIRED', self.codes())
        self.draft['variety'] = '擅自更换品种'
        self.assertIn('CONSTRAINT_VIOLATION', self.codes())
        self.draft['pending_decisions'] = [{'id':'p1','text':'先补齐专业复核','blocking':False,'resolved':True}]
        self.assertIn('PENDING_DECISION', self.codes())

    def test_review_hash_ignores_review_fields_and_persistence_binds_candidate(self):
        from app.review import latest_review, save_review
        self.draft['evidence_refs'] = []
        first = save_review(self.db, self.run, self.draft, 1)
        annotated = self.draft | {'review':{'issues':[]},'can_confirm':True,'content_hash':'invented'}
        self.assertEqual(first['content_hash'], self.review(annotated)['content_hash'])
        changed = self.draft | {'summary':'新的本季表述'}
        second = save_review(self.db, self.run, changed, 2)
        self.assertNotEqual(first['content_hash'], second['content_hash'])
        self.assertEqual(latest_review(self.db, 'r1'), second)
        self.assertEqual(second['candidate_version'], 2)
        self.assertEqual(second['input_revision'], 1)
        self.assertEqual(second['rule_version'], 'candidate-review-v4')
        self.assertEqual(len(second['dimensions']), 5)
        self.assertIsInstance(second['issues'], list)
        self.assertIs(second['can_confirm'], False)

    def test_valid_procedure_is_confirmable_with_optional_ph_only_partial(self):
        self.document()
        review = self.review()
        self.assertTrue(review['can_confirm'], review['issues'])
        self.assertEqual(review['dimensions']['condition_fit'], '无法评价')
        self.assertEqual(review['dimensions']['evidence_sufficiency'], '符合')
        self.assertIn('SOIL_PH_UNKNOWN', self.codes())

    def test_each_unavailable_source_has_a_specific_blocking_issue(self):
        cases = [({},'EVIDENCE_NOT_FOUND'), ({'status':'draft'},'EVIDENCE_NOT_PUBLISHED'),
                 ({'status':'revoked'},'EVIDENCE_REVOKED'), ({'status':'superseded'},'EVIDENCE_NOT_PUBLISHED'),
                 ({'valid_until':'2000-01-01T00:00:00+00:00'},'EVIDENCE_EXPIRED'),
                 ({'customer_id':'other'},'EVIDENCE_SCOPE'), ({'crop':'水稻'},'EVIDENCE_NOT_APPLICABLE'),
                 ({'region':'黑龙江'},'EVIDENCE_NOT_APPLICABLE')]
        for changes, code in cases:
            with self.subTest(code=code, changes=changes):
                self.db.execute('DELETE FROM knowledge')
                if changes:
                    self.document(**changes)
                review = self.review()
                self.assertFalse(review['can_confirm'])
                self.assertIn(code, {i['code'] for i in review['issues']})

    def test_professional_claim_requires_exact_approved_professional_source(self):
        self.document()
        self.draft['measures'][0].update(kind='professional', text='按确认地块执行专业处方。')
        self.assertIn('PROFESSIONAL_SUPPORT_REQUIRED', self.codes())
        self.document(kind='professional', supported_claims=json.dumps(['按确认地块执行专业处方。']))
        self.assertTrue(self.review()['can_confirm'])
        self.document(kind='professional', supported_claims=json.dumps(['类似的其他专业处方。']))
        self.assertIn('PROFESSIONAL_SUPPORT_REQUIRED', self.codes())
        self.document(kind='professional', supported_claims=json.dumps(['按确认地块执行专业处方。']), conditions=json.dumps(['pH 已确认 6.5']))
        self.assertIn('CONDITION_REVIEW_REQUIRED', self.codes())

    def test_wrong_purpose_missing_source_and_procedure_claim_substitution_block(self):
        for changes, code in [({'purpose':'market'},'EVIDENCE_PURPOSE_MISMATCH'),
                              ({'source':''},'EVIDENCE_SOURCE_REQUIRED'),
                              ({'supported_claims':'[]'},'PROCEDURE_SUPPORT_REQUIRED')]:
            with self.subTest(code=code):
                self.document(**changes)
                self.assertIn(code, self.codes())

    def test_confirmed_input_is_limited_to_server_checked_facts_and_calculation(self):
        self.document()
        measure = {'id':'m2','kind':'confirmed_input',
            'text':'SR 本季确认用量 20 kg/亩，来源：SR 复核参数；按确认面积 300 亩核算产品总量 6000 kg、金额 19200.00 CNY。',
            'evidence_refs':[], 'fact_refs':['area_mu','rate_kg_per_mu','rate_source','product_id'],
            'calculation_refs':['calc1'],'conditions':[]}
        self.draft['measures'].append(measure)
        self.assertTrue(self.review()['can_confirm'])
        measure['text'] = 'SR已经确认全部额外的专业措施安全有效。'
        self.assertIn('CONFIRMED_INPUT_MISMATCH', self.codes())
        measure['fact_refs'] = ['made_up_fact']
        self.assertIn('FACT_REFERENCE_INVALID', self.codes())
        measure['calculation_refs'] = ['made_up_calculation']
        self.assertIn('CALCULATION_REFERENCE_INVALID', self.codes())

    def test_evidence_fingerprint_changes_after_revocation_or_product_change(self):
        self.document()
        before = self.review()
        self.db.execute("UPDATE knowledge SET status='revoked',revision=revision+1")
        revoked = self.review()
        self.assertNotEqual(before['evidence_fingerprint'], revoked['evidence_fingerprint'])
        self.assertEqual(before['content_hash'], revoked['content_hash'])
        self.assertFalse(revoked['can_confirm'])
        self.db.execute('UPDATE products SET revision=2')
        self.assertNotEqual(revoked['evidence_fingerprint'], self.review()['evidence_fingerprint'])

    def test_any_measure_cannot_reference_missing_facts_or_calculations(self):
        self.document()
        self.draft['measures'][0]['fact_refs'] = ['nonexistent_fact']
        self.assertIn('FACT_REFERENCE_INVALID', self.codes())
        self.draft['measures'][0]['calculation_refs'] = ['nonexistent_calculation']
        self.assertIn('CALCULATION_REFERENCE_INVALID', self.codes())

    def test_schema_missing_or_duplicate_measure_ids_are_blocking(self):
        self.document()
        self.draft['measures'][0].pop('id')
        self.assertIn('MEASURE_ID_INVALID', self.codes())
        self.draft['measures'][0]['id'] = 'm1'
        self.draft['measures'].append(copy.deepcopy(self.draft['measures'][0]))
        self.assertIn('MEASURE_ID_INVALID', self.codes())

    def test_inspection_is_read_only_and_missing_evidence_is_unevaluable(self):
        from app.review import latest_review
        self.assertIsNone(latest_review(self.db, 'r1'))
        original = copy.deepcopy(self.draft)
        count = self.db.total_changes
        self.review()
        self.assertEqual(self.db.total_changes, count)
        self.assertEqual(self.draft, original)
        self.draft['evidence_refs'] = []
        self.draft['measures'][0]['evidence_refs'] = []
        self.assertEqual(self.review()['dimensions']['evidence_sufficiency'], '无法评价')

    def test_constraints_must_match_snapshot_including_unknown_variety(self):
        self.document()
        for field, value in [('budget_cny','30000'),('budget_basis','net_profit'),('variety','')]:
            with self.subTest(field=field):
                draft = copy.deepcopy(self.draft)
                draft['constraints'][field] = value
                self.assertIn('CONSTRAINT_VIOLATION', self.codes(draft))
        self.run['snapshot']['facts']['variety'] = None
        self.draft['constraints']['variety'] = None
        self.draft['variety'] = '自动推荐的品种'
        self.assertIn('CONSTRAINT_VIOLATION', self.codes())
        self.draft['variety'] = None
        self.assertTrue(self.review()['can_confirm'])

    def test_free_text_is_always_explicitly_left_for_sr_review(self):
        self.document()
        review = self.review()
        warning = next(i for i in review['issues'] if i['code']=='SR_FREE_TEXT_REVIEW')
        self.assertEqual(warning['severity'], 'warning')
        self.assertIn('summary', warning['field'])
        self.assertIn('stages', warning['field'])
        self.assertTrue(review['can_confirm'])
        self.assertEqual(review['dimensions']['risk_preparedness'], '部分符合')

    def test_unknown_budget_cannot_be_described_as_fully_evaluated(self):
        self.document()
        self.run['snapshot']['facts']['budget_cny'] = None
        self.draft['constraints']['budget_cny'] = None
        review = self.review()
        self.assertTrue(review['can_confirm'])
        self.assertEqual(review['dimensions']['risk_preparedness'], '无法评价')
        self.assertIn('BUDGET_UNKNOWN', {i['code'] for i in review['issues']})


if __name__ == '__main__':
    unittest.main()
