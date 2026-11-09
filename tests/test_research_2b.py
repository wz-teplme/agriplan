import json
import unittest

import test_mvp as baseline


class Research2BTests(unittest.TestCase):
    setUp=baseline.WorkflowTests.setUp
    tearDown=baseline.WorkflowTests.tearDown
    login=baseline.WorkflowTests.login
    drain=baseline.WorkflowTests.drain
    detail=baseline.WorkflowTests.detail

    def prepare(self,rate='5',**changes):
        season=self.client.get('/api/seasons/s1').json()
        extra={'id':'extra-b','product_id':'fert-2','rate_kg_per_mu':rate,'rate_source':'SR确认追加产品演示用量' if rate is not None else ''}
        facts=season['facts']|{'additional_materials':[extra]}|changes
        response=self.client.put('/api/seasons/s1',json={'expected_revision':season['revision'],'facts':facts,'source':'SR多产品调查确认'})
        self.assertEqual(response.status_code,200,response.text)
        return response.json()['revision']

    def start(self,required_fields=None):
        season=self.client.get('/api/seasons/s1').json()
        response=self.client.post('/api/research-runs',headers={'Idempotency-Key':'two-b'},json={
            'season_id':'s1','goal':'参考历史方案核对用量和合作预算','expected_revision':season['revision'],
            'required_fields':required_fields or []})
        self.assertEqual(response.status_code,202,response.text)
        self.drain()
        return self.detail(response.json()['id'])

    def answer(self,run,question,value):
        return self.client.post('/api/runs/'+run['id']+'/answers',json={
            'question_id':question['id'],'question_revision':question['revision'],'value':value,
            'source':'SR明确确认本季事实','expected_input_revision':run['snapshot']['revision']})

    def test_two_products_have_exact_cooperation_total_and_confirmed_lines(self):
        self.prepare()
        run=self.start()
        self.assertEqual(run['status'],'completed',run.get('review') or run.get('error'))
        calc=run['draft']['calculation']
        self.assertEqual(calc['cost_cny'],'19200.00')
        self.assertEqual(calc['additional_lines'][0]['cost_cny'],'6150.00')
        self.assertEqual(calc['cooperation_total_cny'],'25350.00')
        self.assertEqual(len(run['draft']['materials']),2)
        self.assertIn('confirmed_material',[m['kind'] for m in run['draft']['measures']])
        self.assertTrue(run['review']['can_confirm'])

    def test_budget_gate_uses_cooperation_total_not_only_primary_line(self):
        self.prepare(rate='10')
        run=self.start()
        self.assertEqual(run['status'],'limited')
        self.assertEqual(run['draft']['calculation']['cooperation_total_cny'],'31500.00')
        self.assertIn('BUDGET_EXCEEDED',[issue['code'] for issue in run['review']['issues']])
        self.assertEqual(self.client.post('/api/runs/'+run['id']+'/confirm',json={'expected_revision':run['revision']}).status_code,409)

    def test_partial_answers_wait_and_final_answer_registers_one_resume(self):
        self.prepare(rate=None,budget_cny=None,soil_ph=None,storage='unknown')
        run=self.start(['budget_cny','soil_ph','storage'])
        self.assertEqual(run['status'],'waiting_input')
        questions={q['field']:q for q in run['questions']}
        self.assertEqual(set(questions),{'additional_materials.extra-b.rate_kg_per_mu','budget_cny','soil_ph','storage'})
        self.assertEqual(questions['storage']['answer_type'],'select')
        values={'additional_materials.extra-b.rate_kg_per_mu':'5','budget_cny':'27000','soil_ph':'6.5','storage':'no'}
        for index,(field,value) in enumerate(values.items()):
            response=self.answer(run,questions[field],value)
            self.assertEqual(response.status_code,200,response.text)
            self.assertEqual(self.answer(run,questions[field],value).status_code,200)
            run=self.detail(run['id'])
            self.assertEqual(run['status'],'queued' if index==3 else 'waiting_input')
            with self.app.state.store.read() as db:
                self.assertEqual(db.execute('SELECT count(*) FROM jobs WHERE run_id=?',(run['id'],)).fetchone()[0],2 if index==3 else 1)
        self.drain()
        run=self.detail(run['id'])
        self.assertEqual(run['status'],'completed',run.get('review') or run.get('error'))
        self.assertEqual(run['draft']['calculation']['cooperation_total_cny'],'25350.00')

    def test_typed_answers_stale_revision_and_scope_fail_closed(self):
        self.prepare(storage='unknown')
        run=self.start(['storage','soil_ph'])
        qs={q['field']:q for q in run['questions']}
        self.assertEqual(self.answer(run,qs['storage'],'maybe').status_code,422)
        self.assertEqual(self.answer(run,qs['soil_ph'],'15').status_code,422)
        self.assertEqual(self.answer(run,qs['storage'],'yes').status_code,200)
        self.assertEqual(self.answer(run,qs['soil_ph'],'6').status_code,409)
        self.login('fr_zhou')
        self.assertEqual(self.answer(self.detail(run['id']),qs['soil_ph'],'6').status_code,403)
        self.login('sr_liu')
        self.assertEqual(self.client.get('/api/runs/'+run['id']).status_code,404)

    def test_additional_product_version_change_blocks_confirmation(self):
        self.prepare()
        run=self.start()
        with self.app.state.store.write() as db:
            db.execute("UPDATE products SET price='4.20',revision=revision+1 WHERE id='fert-2'")
        response=self.client.post('/api/runs/'+run['id']+'/confirm',json={'expected_revision':run['revision']})
        self.assertEqual(response.status_code,409,response.text)
        self.assertIn('PRODUCT_VERSION_CHANGED',[i['code'] for i in self.detail(run['id'])['review']['issues']])

    def test_missing_or_changed_additional_line_cannot_pass_numeric_review(self):
        from app.review import inspect_candidate
        self.prepare()
        run=self.start()
        self.assertIn('additional_lines',run['draft']['calculation'])
        draft=json.loads(json.dumps(run['draft']))
        draft['calculation']['additional_lines'][0]['cost_cny']='0.00'
        draft['calculation']['cooperation_total_cny']='19200.00'
        with self.app.state.store.read() as db:
            review=inspect_candidate(db,run,draft)
        self.assertFalse(review['can_confirm'])
        self.assertIn('CALCULATION_MISMATCH',[i['code'] for i in review['issues']])

    def test_duplicate_products_and_unknown_required_fields_are_rejected(self):
        season=self.client.get('/api/seasons/s1').json()
        facts=season['facts']|{'additional_materials':[{'id':'dup','product_id':'fert-1','rate_kg_per_mu':'2','rate_source':'SR'}]}
        self.assertEqual(self.client.put('/api/seasons/s1',json={'expected_revision':1,'facts':facts,'source':'SR'}).status_code,422)
        response=self.client.post('/api/research-runs',headers={'Idempotency-Key':'bad'},json={'season_id':'s1','expected_revision':1,'goal':'测试','required_fields':['execution_approved']})
        self.assertEqual(response.status_code,422)

    def test_model_selection_cannot_remove_additional_confirmed_input(self):
        from unittest.mock import patch
        from app.model import CandidateProposal
        from app.research import model_draft
        self.prepare()
        run=self.start()
        proposal=CandidateProposal(summary='待SR复核的模型表述',stages=run['draft']['stages'],selected_measure_ids=[],
            history_decisions=[{'reference_id':h['reference_id'],'decision':'not_adopt','reasons':['保留本季确认输入，不沿用往季参数。']} for h in run['state']['history']],pending_decisions=[])
        with patch('app.research.generate_candidate',return_value=proposal):
            result=model_draft(run['snapshot'],run['state'])
        self.assertEqual([m['kind'] for m in result['measures']],['confirmed_input','confirmed_material'])
        self.assertEqual(result['calculation']['cooperation_total_cny'],'25350.00')

    def test_legacy_waiting_question_without_metadata_resumes_after_v2_upgrade(self):
        from app.storage import Store
        response=self.client.post('/api/research-runs',headers={'Idempotency-Key':'legacy'},json={
            'season_id':'s2','goal':'参考历史和预算','expected_revision':1})
        rid=response.json()['id']
        self.drain()
        run=self.detail(rid)
        question=run['questions'][0]
        with self.app.state.store.write() as db:
            db.execute('DELETE FROM question_contexts WHERE question_id=?',(question['id'],))
            db.execute('PRAGMA user_version=2')
        Store(self.app.state.store.path)
        legacy=self.detail(rid)['questions'][0]
        self.assertEqual(legacy['answer_type'],'number')
        self.assertTrue(legacy['purpose'])
        self.assertIsNone(legacy['input_revision'])
        answer=self.client.post('/api/runs/'+rid+'/answers',json={'question_id':question['id'],
            'question_revision':1,'value':'15','source':'旧客户端SR确认的本季用量'})
        self.assertEqual(answer.status_code,200,answer.text)
        self.drain()
        self.assertEqual(self.detail(rid)['status'],'completed')

    def test_material_manifest_and_primary_explanation_cannot_be_removed(self):
        from app.review import inspect_candidate
        self.prepare()
        run=self.start()
        for mutation in ('manifest','amount','primary'):
            with self.subTest(mutation=mutation):
                draft=json.loads(json.dumps(run['draft']))
                if mutation=='manifest':
                    draft['materials']=[]
                elif mutation=='amount':
                    draft['materials'][1]['cost_cny']='0.00'
                else:
                    draft['measures']=[m for m in draft['measures'] if m['kind']!='confirmed_input']
                with self.app.state.store.read() as db:
                    result=inspect_candidate(db,run,draft)
                self.assertFalse(result['can_confirm'],mutation)

    def test_single_product_v3_cannot_forge_additional_calculation(self):
        from app.review import inspect_candidate
        run=self.start()
        draft=json.loads(json.dumps(run['draft']))
        draft['calculation']['cooperation_total_cny']='0.00'
        draft['calculation']['additional_lines']=[{'cost_cny':'9999.00','product_id':'fake'}]
        with self.app.state.store.read() as db:
            result=inspect_candidate(db,run,draft)
        self.assertFalse(result['can_confirm'])
        self.assertIn('CALCULATION_MISMATCH',[i['code'] for i in result['issues']])

    def test_answer_replay_after_cancellation_is_rejected(self):
        self.prepare(rate=None,budget_cny=None)
        run=self.start(['budget_cny'])
        question=run['questions'][0]
        self.assertEqual(self.answer(run,question,'5').status_code,200)
        with self.app.state.store.write() as db:
            db.execute("UPDATE runs SET status='cancelled' WHERE id=?",(run['id'],))
        self.assertEqual(self.answer(run,question,'5').status_code,409)

    def test_answer_replay_after_external_fact_change_is_rejected(self):
        self.prepare(rate=None,budget_cny=None)
        run=self.start(['budget_cny'])
        question=run['questions'][0]
        self.assertEqual(self.answer(run,question,'5').status_code,200)
        season=self.client.get('/api/seasons/s1').json()
        response=self.client.put('/api/seasons/s1',json={'expected_revision':season['revision'],
            'facts':season['facts']|{'area_mu':'301'},'source':'SR更新面积'})
        self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(self.answer(run,question,'5').status_code,409)
