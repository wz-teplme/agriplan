import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

os.environ['AGRIPLAN_MODE'] = 'demo'


class CalculationTests(unittest.TestCase):
    def test_money_is_exact_and_invalid_values_are_rejected(self):
        from app.domain import calculate
        self.assertEqual(calculate('2', '3', '1.25')['cost_cny'], '7.50')
        self.assertEqual(calculate('0.1', '0.2', '0.3')['cost_cny'], '0.01')
        for values in [('NaN', '3', '1'), ('2', '-1', '1'), ('0', '1', '1'), ('2', '1', 'Infinity')]:
            with self.subTest(values=values), self.assertRaises(ValueError):
                calculate(*values)

    def test_market_excludes_stale_or_incomplete_quotes(self):
        from app.domain import compare_quotes
        now = datetime.now(timezone.utc)
        quotes = [
            dict(id='a', buyer='A', price='2.50', freight='0.10', deduction='0.05', crop='玉米', region='吉林', unit='CNY/kg', moisture='14', as_of=now.isoformat()),
            dict(id='b', buyer='B', price='2.60', freight=None, deduction='0', crop='玉米', region='吉林', unit='CNY/kg', moisture='14', as_of=now.isoformat()),
            dict(id='c', buyer='C', price='3.00', freight='0', deduction='0', crop='玉米', region='吉林', unit='CNY/kg', moisture='14', as_of=(now-timedelta(days=8)).isoformat()),
        ]
        result = compare_quotes(quotes, crop='玉米', region='吉林', storage='no', now=now)
        self.assertEqual(result['best_quote_id'], 'a')
        self.assertEqual(result['eligible'][0]['net_cny_per_kg'], '2.3500')
        self.assertEqual(len(result['excluded']), 2)
        self.assertNotIn('store', result['strategies'])


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        from fastapi.testclient import TestClient
        from app.main import create_app
        self.tmp = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1])
        self.app = create_app(Path(self.tmp.name) / 'test.db')
        self.client = TestClient(self.app)
        self.client.__enter__()
        self.login('sr_chen')

    def tearDown(self):
        self.client.__exit__(None, None, None)
        self.tmp.cleanup()

    def login(self, username):
        response = self.client.post('/api/login', json={'username': username, 'password': 'agriplan-demo'})
        self.assertEqual(response.status_code, 200, response.text)

    def start(self, season='s1', key='start-1'):
        response = self.client.post('/api/research-runs', headers={'Idempotency-Key': key}, json={
            'season_id': season, 'goal': '参考多年方案，控制合作预算', 'expected_revision': 1,
            'include_market': False,
        })
        self.assertEqual(response.status_code, 202, response.text)
        return response.json()

    def drain(self):
        from app.research import work_once
        for _ in range(20):
            if not work_once(self.app.state.store):
                break

    def detail(self, run_id):
        response = self.client.get('/api/runs/' + run_id)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def test_history_draft_edit_confirm_and_field_record(self):
        created = self.start()
        duplicate = self.start()
        self.assertEqual(created['id'], duplicate['id'])
        self.drain()
        run = self.detail(created['id'])
        self.assertEqual(run['status'], 'completed')
        self.assertEqual(run['draft']['calculation']['cost_cny'], '19200.00')
        self.assertEqual(run['state']['history'][0]['plot_id'], 'p1')
        self.assertTrue(run['state']['history'][0]['limitations'])
        version = run['candidate_version']
        chat = self.client.post(f"/api/runs/{run['id']}/discuss", json={'message': '为什么这样安排？'})
        self.assertEqual(chat.status_code, 200)
        self.assertEqual(self.detail(run['id'])['candidate_version'], version)
        draft = run['draft'] | {'summary': 'SR 确认采购条件后调整表述'}
        edited = self.client.put(f"/api/runs/{run['id']}/draft", json={'expected_revision': run['revision'], 'summary': draft['summary'], 'stages': draft['stages'], 'reason': '客户采购条件'})
        self.assertEqual(edited.status_code, 200, edited.text)
        changed = self.detail(run['id'])
        self.assertEqual(changed['candidate_version'], version + 1)
        confirmed = self.client.post(f"/api/runs/{run['id']}/confirm", json={'expected_revision': changed['revision']})
        self.assertEqual(confirmed.status_code, 200, confirmed.text)
        self.assertEqual(confirmed.json()['business_status'], 'not_connected')
        self.assertEqual(confirmed.json()['execution_status'], 'blocked')
        record = self.client.post('/api/field-records', json={'season_id': 's1', 'kind': 'inspection', 'observed_at': datetime.now(timezone.utc).isoformat(), 'notes': '苗情正常，记录仅表示巡田实际。'})
        self.assertEqual(record.status_code, 201, record.text)
        self.assertEqual(len(self.client.get('/api/field-records').json()), 1)

    def test_missing_rate_waits_and_duplicate_answer_is_idempotent(self):
        run_id = self.start('s2')['id']
        self.drain()
        waiting = self.detail(run_id)
        self.assertEqual(waiting['status'], 'waiting_input')
        question = waiting['questions'][0]
        answer = {'question_id': question['id'], 'question_revision': question['revision'], 'value': '15', 'source': 'SR 明确确认的演示用量'}
        first = self.client.post(f'/api/runs/{run_id}/answers', json=answer)
        second = self.client.post(f'/api/runs/{run_id}/answers', json=answer)
        self.assertEqual(first.status_code, 200, first.text)
        self.assertEqual(second.status_code, 200, second.text)
        conflict = self.client.post(f'/api/runs/{run_id}/answers', json=answer | {'value': '16'})
        self.assertEqual(conflict.status_code, 409)
        from app.storage import Store
        from app.research import work_once
        reopened=Store(self.app.state.store.path)
        while work_once(reopened):
            pass
        result = self.detail(run_id)
        self.assertEqual(result['status'], 'completed')
        self.assertEqual(result['snapshot']['facts']['rate_kg_per_mu'], '15')
        self.assertEqual(result['draft']['calculation']['cost_cny'], '5760.00')

    def test_other_customer_scope_and_fr_cannot_confirm(self):
        run_id = self.start()['id']
        self.drain()
        self.assertEqual(self.client.get('/api/seasons/s3').status_code, 404)
        self.login('sr_liu')
        self.assertEqual(self.client.get('/api/runs/' + run_id).status_code, 404)
        self.assertEqual(self.client.post('/api/field-records', json={'season_id':'s1', 'kind':'inspection', 'observed_at':datetime.now(timezone.utc).isoformat(), 'notes':'越权'}).status_code, 404)
        self.login('fr_zhou')
        self.assertEqual(self.client.post(f'/api/runs/{run_id}/confirm', json={'expected_revision': 1}).status_code, 403)

    def test_changed_facts_reject_stale_confirmation_and_old_editor(self):
        run_id = self.start()['id']
        self.drain()
        run = self.detail(run_id)
        season = self.client.get('/api/seasons/s1').json()
        body = {'expected_revision': season['revision'], 'facts': season['facts'] | {'area_mu': '301'}, 'source': 'SR 复测面积'}
        self.assertEqual(self.client.put('/api/seasons/s1', json=body).status_code, 200)
        self.assertEqual(self.client.put('/api/seasons/s1', json=body).status_code, 409)
        confirmation = self.client.post(f'/api/runs/{run_id}/confirm', json={'expected_revision': run['revision']})
        self.assertEqual(confirmation.status_code, 409)

    def test_worker_can_recover_expired_lease_and_old_token_cannot_commit(self):
        from app.research import claim_job, commit_step
        run_id = self.start()['id']
        first = claim_job(self.app.state.store, now=100)
        second = claim_job(self.app.state.store, now=1000)
        self.assertNotEqual(first['token'], second['token'])
        self.assertFalse(commit_step(self.app.state.store, first, {'status': 'failed'}, now=1001))
        self.assertEqual(self.detail(run_id)['status'], 'running')

    def test_idempotency_key_cannot_reuse_different_payload(self):
        self.start()
        changed = self.client.post('/api/research-runs', headers={'Idempotency-Key':'start-1'}, json={'season_id':'s2', 'goal':'另一个对象', 'expected_revision':1, 'include_market':False})
        self.assertEqual(changed.status_code, 409)

    def test_new_customer_and_nonoptional_validation(self):
        created = self.client.post('/api/customers', json={'name':'新客户', 'plot_name':'新地块', 'region':'吉林', 'year':2026, 'crop':'玉米', 'area_mu':'20'})
        self.assertEqual(created.status_code, 201, created.text)
        season_id = created.json()['season_id']
        self.assertEqual(self.client.get('/api/seasons/' + season_id).status_code, 200)
        invalid = self.client.post('/api/customers', json={'name':'新客户', 'plot_name':'新地块', 'region':'吉林', 'year':2026, 'crop':'玉米', 'area_mu':'NaN'})
        self.assertEqual(invalid.status_code, 422)

    def test_confirmed_plan_is_immutable_and_becomes_next_season_reference(self):
        run_id=self.start()['id']
        self.drain()
        run=self.detail(run_id)
        plan=self.client.post(f'/api/runs/{run_id}/confirm',json={'expected_revision':run['revision']}).json()
        confirmed=self.detail(run_id)
        edited=self.client.put(f'/api/runs/{run_id}/draft',json={'expected_revision':confirmed['revision'],'summary':'不可覆盖','stages':run['draft']['stages'],'reason':'测试'})
        self.assertEqual(edited.status_code,409)
        next_season=self.client.post('/api/seasons/s1/next',json={'year':2027,'crop':'玉米','area_mu':'300'})
        self.assertEqual(next_season.status_code,201,next_season.text)
        following=self.client.get('/api/seasons/'+next_season.json()['season_id']).json()
        self.assertEqual(following['history'][0]['reference_id'],plan['id'])
        self.assertIsNone(following['facts']['rate_kg_per_mu'])
        self.assertIsNone(following['facts']['budget_cny'])
        self.assertEqual(self.detail(run_id)['formal']['content'],plan['content'])

    def test_unknown_knowledge_does_not_invent_a_match_and_cross_origin_write_denied(self):
        result=self.client.post('/api/knowledge',json={'message':'XYZ123火星天气'}).json()
        self.assertEqual(result['evidence'],[])
        denied=self.client.post('/api/login',headers={'Origin':'https://other.example'},json={'username':'sr_chen','password':'agriplan-demo'})
        self.assertEqual(denied.status_code,403)

    def test_cancelled_worker_cannot_publish_a_draft(self):
        from app.research import claim_job,commit_step
        run_id=self.start()['id']
        job=claim_job(self.app.state.store)
        run=self.detail(run_id)
        cancelled=self.client.post(f'/api/runs/{run_id}/cancel',json={'expected_revision':run['revision']})
        self.assertEqual(cancelled.status_code,200)
        self.assertFalse(commit_step(self.app.state.store,job,{'status':'completed'}))
        self.assertIsNone(self.detail(run_id)['draft'])

    def test_late_record_does_not_assume_current_plan_was_used(self):
        run_id=self.start()['id']
        self.drain()
        run=self.detail(run_id)
        plan=self.client.post(f'/api/runs/{run_id}/confirm',json={'expected_revision':run['revision']}).json()
        early={'season_id':'s1','kind':'operation','observed_at':'2026-01-01T10:00:00+08:00','notes':'补录历史实际，不假定使用后来的方案'}
        self.assertEqual(self.client.post('/api/field-records',json=early).status_code,201)
        self.assertIsNone(self.client.get('/api/field-records').json()[0]['plan_id'])
        self.assertEqual(self.client.post('/api/field-records',json=early|{'plan_id':plan['id']}).status_code,422)


class ModelBoundaryTests(unittest.TestCase):
    def test_json_action_validation_rejects_disallowed_tool(self):
        import httpx
        from unittest.mock import patch
        from app.model import choose_action
        def handler(request):
            payload=__import__('json').loads(request.content)
            self.assertEqual(payload['response_format'],{'type':'json_object'})
            return httpx.Response(200,json={'choices':[{'message':{'content':'{"type":"submit_result","purpose":"尝试提前提交"}'}}]})
        client=httpx.Client(transport=httpx.MockTransport(handler))
        with patch.dict(os.environ,{'AGRIPLAN_MODEL_BASE_URL':'https://model.example/v1','AGRIPLAN_MODEL':'test','AGRIPLAN_MODEL_API_KEY':'fake-test-key'}), patch('app.model.httpx.Client',return_value=client):
            with self.assertRaises(ValueError):
                choose_action({'goal':'演示'},['query_history'])


if __name__ == '__main__':
    unittest.main()
