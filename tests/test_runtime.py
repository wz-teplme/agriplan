"""Exercise dependency reuse through real API transactions and persisted workers."""
import json
import os
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import httpx
import test_mvp as baseline
from test_model_candidate import CONFIG


class RuntimeTests(unittest.TestCase):
    setUp = baseline.WorkflowTests.setUp
    tearDown = baseline.WorkflowTests.tearDown
    login = baseline.WorkflowTests.login
    drain = baseline.WorkflowTests.drain
    detail = baseline.WorkflowTests.detail

    def start(self, market=True, required=None, drain=True):
        season = self.client.get('/api/seasons/s1').json()
        response = self.client.post('/api/research-runs', headers={'Idempotency-Key': 'runtime-start'}, json={
            'season_id': 's1', 'goal': '核对历史方案、专业资料和本季公司合作预算',
            'expected_revision': season['revision'], 'include_market': market,
            'required_fields': required or [],
        })
        self.assertEqual(response.status_code, 202, response.text)
        if drain:
            self.drain()
        return self.detail(response.json()['id'])

    def edit(self, **changes):
        season = self.client.get('/api/seasons/s1').json()
        response = self.client.put('/api/seasons/s1', json={
            'expected_revision': season['revision'], 'facts': season['facts'] | changes,
            'source': 'SR 明确确认的局部重研验收输入',
        })
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()['revision']

    def refs(self, run):
        return {module['name']: module['result_ref'] for module in run['runtime']['modules']}

    def body(self, run, intent='refresh'):
        return {'expected_revision': run['revision'],
                'expected_input_revision': run['runtime']['current_input_revision'],
                'expected_context_hash': run['runtime']['context_hash'], 'intent': intent}

    def refresh(self, run, intent='refresh'):
        response = self.client.post('/api/runs/' + run['id'] + '/refresh', json=self.body(run, intent))
        self.assertEqual(response.status_code, 202, response.text)
        return response

    def assert_reused(self, before, after, reused, changed):
        old, new = self.refs(before), self.refs(after)
        for name in reused:
            self.assertIsNotNone(old[name], name)
            self.assertEqual(old[name], new[name], name)
        for name in changed:
            self.assertIsNotNone(new[name], name)
            self.assertNotEqual(old[name], new[name], name)

    def test_area_refresh_reuses_sources_and_market_preserving_old_candidate(self):
        original = self.start()
        self.assertEqual(original['status'], 'completed', original.get('error'))
        self.assertEqual(set(self.refs(original)), {'history', 'evidence', 'quantity', 'budget', 'market', 'candidate'})
        old_content = original['versions'][0]['content']
        self.edit(area_mu='301')
        preview = self.detail(original['id'])
        self.assertEqual(set(preview['runtime']['affected_modules']), {'quantity', 'budget', 'candidate'})
        self.assertTrue(preview['runtime']['can_refresh'])
        response = self.refresh(preview)
        duplicate = self.client.post('/api/runs/' + original['id'] + '/refresh', json=self.body(preview))
        self.assertEqual(duplicate.status_code, 202, duplicate.text)
        self.assertTrue(duplicate.json()['duplicate'])
        self.assertEqual(response.json()['generation'], 2)
        self.assertIsNone(self.detail(original['id'])['draft'])
        self.drain()
        result = self.detail(original['id'])
        self.assertEqual(result['status'], 'completed', result.get('error'))
        self.assertEqual(result['draft']['calculation']['cost_cny'], '19264.00')
        self.assertEqual(result['candidate_version'], 2)
        self.assertEqual(result['versions'][-1]['content'], old_content)
        self.assert_reused(original, result, ['history', 'evidence', 'market'], ['quantity', 'budget', 'candidate'])
        differences = result['state']['history'][0]['differences']
        self.assertEqual(next(d['current'] for d in differences if d['field'] == 'area_mu'), '301')
        with self.app.state.store.read() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM jobs WHERE run_id=?', (result['id'],)).fetchone()[0], 2)

    def test_price_changes_only_budget_and_candidate_and_refreshes_product_snapshot(self):
        original = self.start()
        with self.app.state.store.write() as db:
            db.execute("UPDATE products SET price='3.30',revision=revision+1 WHERE id='fert-1'")
        preview = self.detail(original['id'])
        self.assertEqual(set(preview['runtime']['affected_modules']), {'budget', 'candidate'})
        self.refresh(preview)
        self.drain()
        result = self.detail(original['id'])
        self.assertEqual(result['draft']['calculation']['cost_cny'], '19800.00')
        self.assertEqual(result['snapshot']['product']['price'], '3.30')
        self.assertTrue(result['review']['can_confirm'])
        self.assert_reused(original, result, ['history', 'evidence', 'quantity', 'market'], ['budget', 'candidate'])

    def test_storage_reuses_money_but_rebinds_calculation_to_current_r(self):
        original = self.start()
        revision = self.edit(storage='yes')
        preview = self.detail(original['id'])
        self.assertEqual(set(preview['runtime']['affected_modules']), {'market', 'candidate'})
        self.refresh(preview)
        self.drain()
        result = self.detail(original['id'])
        self.assertEqual(result['draft']['input_revision'], revision)
        self.assertEqual(result['draft']['calculation']['cost_cny'], '19200.00')
        self.assertNotEqual(result['draft']['calculation']['id'], original['draft']['calculation']['id'])
        self.assertIn('store', result['state']['market']['strategies'])
        self.assertTrue(result['review']['can_confirm'], result['review'])
        self.assert_reused(original, result, ['history', 'evidence', 'quantity', 'budget'], ['market', 'candidate'])

    def test_expired_market_blocks_confirmation_and_refresh_recomputes_only_market_and_candidate(self):
        original=self.start()
        with patch('app.dependencies.datetime',wraps=datetime) as clock:
            clock.now.return_value=datetime.now(timezone.utc)+timedelta(days=8)
            preview=self.detail(original['id'])
            self.assertEqual(set(preview['runtime']['affected_modules']),{'market','candidate'})
            response=self.client.post('/api/runs/'+original['id']+'/confirm',json={'expected_revision':original['revision']})
            self.assertEqual(response.status_code,409,response.text)
            self.refresh(self.detail(original['id']))
        self.drain()
        refreshed=self.detail(original['id'])
        self.assertEqual(refreshed['status'],'completed',refreshed.get('error'))
        self.assert_reused(original,refreshed,['history','evidence','quantity','budget'],['market','candidate'])

    def test_source_revocation_invalidates_evidence_and_never_reuses_revoked_claim(self):
        original = self.start()
        with self.app.state.store.write() as db:
            db.execute("UPDATE knowledge SET status='revoked',revision=revision+1 WHERE id='k1'")
        preview = self.detail(original['id'])
        self.assertEqual(set(preview['runtime']['affected_modules']), {'evidence', 'candidate'})
        self.refresh(preview)
        self.drain()
        result = self.detail(original['id'])
        self.assertNotIn('k1', result['draft']['evidence_refs'])
        self.assert_reused(original, result, ['history', 'quantity', 'budget', 'market'], ['evidence', 'candidate'])

    def publish_additional_document(self):
        with self.app.state.store.write() as db:
            document = dict(db.execute("SELECT * FROM knowledge WHERE id='k1'").fetchone())
            document.update(id='new-applicable', document_id='new-applicable', title='新增适用流程资料')
            names=list(document)
            db.execute('INSERT INTO knowledge('+','.join(names)+') VALUES('+','.join('?' for _ in names)+')',
                       [document[name] for name in names])

    def test_new_source_published_during_drafting_cannot_commit_an_old_catalog_candidate(self):
        from app.research import build_draft
        def publish_during_draft(snapshot, state):
            candidate=build_draft(snapshot,state)
            self.publish_additional_document()
            return candidate
        with patch('app.research.build_draft',side_effect=publish_during_draft):
            run=self.start()
        self.assertEqual(run['status'],'failed',run.get('error'))
        self.assertIsNone(run['draft'])
        self.assertEqual(run['candidate_version'],0)

    def test_new_source_after_completion_blocks_confirmation_until_explicit_refresh(self):
        run=self.start()
        self.publish_additional_document()
        response=self.client.post('/api/runs/'+run['id']+'/confirm',json={'expected_revision':run['revision']})
        self.assertEqual(response.status_code,409,response.text)
        self.assertIsNone(self.detail(run['id'])['formal_id'])
        self.refresh(self.detail(run['id']))
        self.drain()
        current=self.detail(run['id'])
        self.assertEqual(current['status'],'completed',current.get('error'))
        confirmed=self.client.post('/api/runs/'+run['id']+'/confirm',json={'expected_revision':current['revision']})
        self.assertEqual(confirmed.status_code,200,confirmed.text)

    def test_stale_preview_fr_other_scope_and_formal_plan_cannot_refresh(self):
        original = self.start()
        self.edit(area_mu='301')
        preview = self.detail(original['id'])
        self.edit(area_mu='302')
        self.assertEqual(self.client.post('/api/runs/' + original['id'] + '/refresh', json=self.body(preview)).status_code, 409)
        current = self.detail(original['id'])
        self.login('fr_zhou')
        self.assertFalse(self.detail(original['id'])['runtime']['can_refresh'])
        self.assertEqual(self.client.post('/api/runs/' + original['id'] + '/refresh', json=self.body(current)).status_code, 403)
        self.login('sr_liu')
        self.assertEqual(self.client.post('/api/runs/' + original['id'] + '/refresh', json=self.body(current)).status_code, 404)
        self.login('sr_chen')
        self.refresh(current)
        self.drain()
        current = self.detail(original['id'])
        confirmed = self.client.post('/api/runs/' + original['id'] + '/confirm', json={'expected_revision': current['revision']})
        self.assertEqual(confirmed.status_code, 200, confirmed.text)
        self.edit(area_mu='303')
        formal = self.detail(original['id'])
        self.assertFalse(formal['runtime']['can_refresh'])
        self.assertEqual(self.client.post('/api/runs/' + original['id'] + '/refresh', json=self.body(formal)).status_code, 409)
        self.assertEqual(self.detail(original['id'])['formal']['content'], confirmed.json()['content'])

    def test_refresh_limit_cannot_be_reset_by_changed_inputs_or_repair(self):
        run = self.start()
        for area in ['301', '302', '303']:
            self.edit(area_mu=area)
            self.refresh(self.detail(run['id']))
            self.drain()
        self.edit(area_mu='304')
        run = self.detail(run['id'])
        self.assertEqual(run['runtime']['generation'], 4)
        self.assertFalse(run['runtime']['can_refresh'])
        for intent in ['refresh', 'repair']:
            self.assertEqual(self.client.post('/api/runs/' + run['id'] + '/refresh', json=self.body(run, intent)).status_code, 409)
        with self.app.state.store.read() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM jobs WHERE run_id=?', (run['id'],)).fetchone()[0], 4)
        self.assertEqual(run['candidate_version'], 4)

    def test_old_worker_token_is_fenced_by_refresh_and_removed_assignment_blocks_execution(self):
        from app.research import claim_job, commit_step
        run = self.start(drain=False)
        job = claim_job(self.app.state.store)
        self.edit(area_mu='301')
        self.refresh(self.detail(run['id']))
        self.assertFalse(commit_step(self.app.state.store, job, {'status': 'completed'}))
        with self.app.state.store.write() as db:
            db.execute("DELETE FROM assignments WHERE user_id='u1' AND customer_id='c1'")
        self.drain()
        with self.app.state.store.read() as db:
            row = db.execute('SELECT status,draft FROM runs WHERE id=?', (run['id'],)).fetchone()
            self.assertEqual(row['status'], 'failed')
            self.assertIsNone(row['draft'])
            self.assertEqual(db.execute('SELECT count(*) FROM actions WHERE run_id=?', (run['id'],)).fetchone()[0], 0)

    def test_current_sr_can_take_over_recovery_after_original_sr_assignment_is_removed(self):
        original = self.start()
        self.edit(area_mu='301')
        with self.app.state.store.write() as db:
            db.execute("DELETE FROM assignments WHERE user_id='u1' AND customer_id='c1'")
            db.execute("INSERT INTO assignments VALUES('u2','c1')")
        self.login('sr_liu')
        self.refresh(self.detail(original['id']))
        self.drain()
        recovered = self.detail(original['id'])
        self.assertEqual(recovered['status'], 'completed', recovered.get('error'))
        self.assertEqual(recovered['actor_id'], 'u1')
        self.assertEqual(recovered['draft']['calculation']['cost_cny'], '19264.00')
        self.assert_reused(original, recovered, ['history', 'evidence', 'market'], ['quantity', 'budget', 'candidate'])

    def test_waiting_refresh_supersedes_old_question_and_final_answer_resumes_once(self):
        self.edit(rate_kg_per_mu=None, rate_source='')
        original = self.start()
        self.assertEqual(original['status'], 'waiting_input')
        old_question = next(q for q in original['questions'] if q['status'] == 'open')
        self.edit(area_mu='301')
        self.refresh(self.detail(original['id']))
        self.drain()
        waiting = self.detail(original['id'])
        self.assertEqual(waiting['status'], 'waiting_input')
        self.assertEqual(next(q['status'] for q in waiting['questions'] if q['id'] == old_question['id']), 'superseded')
        current = next(q for q in waiting['questions'] if q['status'] == 'open')
        stale_answer = {'question_id': old_question['id'], 'question_revision': old_question['revision'],
                        'value': '99', 'source': '过期问题不能套用',
                        'expected_input_revision': waiting['snapshot']['revision']}
        self.assertEqual(self.client.post('/api/runs/' + waiting['id'] + '/answers', json=stale_answer).status_code, 409)
        answer = {'question_id': current['id'], 'question_revision': current['revision'], 'value': '20',
                  'source': 'SR 确认的合成用量', 'expected_input_revision': waiting['snapshot']['revision']}
        for _ in range(2):
            response = self.client.post('/api/runs/' + waiting['id'] + '/answers', json=answer)
            self.assertEqual(response.status_code, 200, response.text)
        self.drain()
        result = self.detail(waiting['id'])
        self.assertEqual(result['status'], 'completed', result.get('error'))
        self.assertEqual(result['runtime']['generation'], 3)
        self.assertEqual(result['draft']['calculation']['cost_cny'], '19264.00')
        self.assert_reused(waiting, result, ['history', 'evidence', 'market'], ['quantity', 'budget', 'candidate'])

    def test_old_run_without_receipts_conservatively_recomputes_all_modules(self):
        original = self.start()
        with self.app.state.store.write() as db:
            state = original['state'].copy()
            state.pop('modules', None)
            db.execute('UPDATE runs SET state=? WHERE id=?', (json.dumps(state), original['id']))
        self.edit(storage='yes')
        preview = self.detail(original['id'])
        self.assertEqual(set(preview['runtime']['affected_modules']), {'history', 'evidence', 'quantity', 'budget', 'market', 'candidate'})
        self.refresh(preview)
        self.drain()
        result = self.detail(original['id'])
        self.assertEqual(result['status'], 'completed')
        self.assert_reused(original, result, [], ['history', 'evidence', 'quantity', 'budget', 'market', 'candidate'])

    def test_receipt_from_another_research_cannot_be_reused_even_for_same_inputs(self):
        original = self.start()
        response = self.client.post('/api/research-runs', headers={'Idempotency-Key': 'different-research'}, json={
            'season_id': 's1', 'goal': original['goal'], 'expected_revision': 1, 'include_market': True,
        })
        self.assertEqual(response.status_code, 202, response.text)
        self.drain()
        other = self.detail(response.json()['id'])
        with self.app.state.store.write() as db:
            state = original['state'] | {'modules': other['state']['modules']}
            db.execute('UPDATE runs SET state=? WHERE id=?', (json.dumps(state), original['id']))
        self.edit(storage='yes')
        preview = self.detail(original['id'])
        self.assertEqual(set(preview['runtime']['affected_modules']), {'history', 'evidence', 'quantity', 'budget', 'market', 'candidate'})
        self.refresh(preview)
        self.drain()
        result = self.detail(original['id'])
        self.assertEqual(result['status'], 'completed')
        self.assert_reused(other, result, [], ['history', 'evidence', 'quantity', 'budget', 'market', 'candidate'])

    def test_receipt_payload_restores_money_instead_of_mutable_checkpoint(self):
        original = self.start()
        with self.app.state.store.write() as db:
            state = json.loads(json.dumps(original['state']))
            state['calculation']['cost_cny'] = '0.01'
            db.execute('UPDATE runs SET state=? WHERE id=?', (json.dumps(state), original['id']))
        self.edit(storage='yes')
        self.refresh(self.detail(original['id']))
        self.drain()
        result = self.detail(original['id'])
        self.assertEqual(result['status'], 'completed', result.get('error'))
        self.assertEqual(result['draft']['calculation']['cost_cny'], '19200.00')
        self.assertEqual(self.refs(original)['budget'], self.refs(result)['budget'])

    def test_completed_research_cannot_force_repair_and_old_retry_cannot_claim_new_inputs(self):
        original = self.start()
        self.assertFalse(original['runtime']['can_repair'])
        self.assertEqual(self.client.post('/api/runs/' + original['id'] + '/refresh', json=self.body(original, 'repair')).status_code, 409)
        self.edit(area_mu='301')
        preview = self.detail(original['id'])
        self.refresh(preview)
        self.drain()
        self.edit(area_mu='302')
        self.assertEqual(self.client.post('/api/runs/' + original['id'] + '/refresh', json=self.body(preview)).status_code, 409)

    def test_worker_stops_at_persisted_action_budget_for_current_generation(self):
        original = self.start()
        self.edit(area_mu='301')
        self.refresh(self.detail(original['id']))
        with self.app.state.store.write() as db:
            sequence = db.execute('SELECT max(sequence) FROM actions WHERE run_id=?', (original['id'],)).fetchone()[0]
            for index in range(12):
                db.execute('INSERT INTO actions(id,run_id,sequence,type,purpose,output,created_at,generation) VALUES(?,?,?,?,?,?,?,?)',
                           ('budget-' + str(index), original['id'], sequence + index + 1,
                            'query_history', '模拟已消耗动作', '[]', '2026-01-01', 2))
        self.drain()
        result = self.detail(original['id'])
        self.assertEqual(result['status'], 'limited')
        self.assertIsNone(result['draft'])
        self.assertEqual(result['runtime']['actions_used'], 12)
        self.assertEqual(result['candidate_version'], 1)

    def test_manual_candidate_edit_updates_its_receipt_without_invalidating_dependencies(self):
        original = self.start()
        response = self.client.put('/api/runs/' + original['id'] + '/draft', json={
            'expected_revision': original['revision'], 'summary': 'SR 调整服务安排文字，先核对采购条件。',
            'stages': original['draft']['stages'], 'reason': 'SR 已核对的服务安排',
        })
        self.assertEqual(response.status_code, 200, response.text)
        changed = self.detail(original['id'])
        self.assertEqual(changed['candidate_version'], 2)
        self.assertEqual(changed['runtime']['affected_modules'], [])
        self.assertNotEqual(self.refs(original)['candidate'], self.refs(changed)['candidate'])
        with self.app.state.store.read() as db:
            saved = db.execute('SELECT result FROM research_results WHERE id=?', (self.refs(changed)['candidate'],)).fetchone()
            self.assertEqual(json.loads(saved['result'])['summary'], 'SR 调整服务安排文字，先核对采购条件。')

    def test_empty_evidence_is_repaired_once_then_kept_limited(self):
        with self.app.state.store.write() as db:
            db.execute('DELETE FROM knowledge')
        run = self.start()
        self.assertEqual(run['status'], 'limited')
        self.assertIsNotNone(run['draft'])
        self.assertFalse(run['review']['can_confirm'])
        actions = [a['type'] for a in run['actions']]
        self.assertEqual(actions.count('query_evidence'), 2)
        self.assertEqual(actions.count('repair_draft'), 1)
        self.assertLessEqual(len(actions), 12)

    def test_quality_feedback_rebuilds_candidate_once_and_stop_keeps_partial_result(self):
        real_client = httpx.Client
        for stop in (False, True):
            with self.subTest(stop=stop):
                # Independent research objects keep the automatic repair allowance separate.
                seen = []
                def handler(request):
                    data = json.loads(json.loads(request.content)['messages'][1]['content'])
                    if 'allowed' in data:
                        action = 'stop' if stop and 'repair_draft' in data['allowed'] else data['allowed'][0]
                        output = {'type': action, 'purpose': '根据当前质量反馈推进或停止'}
                    else:
                        seen.append(data)
                        output = {'summary': '待 SR 复核的服务安排，专业参数仅解释确认值。',
                                  'stages': [{'name': '核对', 'timing': '播前', 'action': '核对当前依据与本季资料。'}],
                                  'selected_measure_ids': [] if len(seen) == 1 else [m['id'] for m in data['available_measures']],
                                  'history_decisions': [{'reference_id': h['reference_id'], 'decision': 'reference_only',
                                                         'reasons': ['需核对本季差异。']} for h in data['history']],
                                  'pending_decisions': []}
                    return httpx.Response(200, json={'choices': [{'message': {'content': json.dumps(output, ensure_ascii=False)}}]})
                transport = httpx.MockTransport(handler)
                with patch.dict(os.environ, CONFIG), patch('app.model.httpx.Client', side_effect=lambda **kw: real_client(transport=transport, **kw)):
                    response = self.client.post('/api/research-runs', headers={'Idempotency-Key': 'quality-' + str(stop)}, json={
                        'season_id': 's1', 'goal': '核对本季方案流程与资料依据', 'expected_revision': 1,
                    })
                    self.assertEqual(response.status_code, 202, response.text)
                    self.drain()
                run = self.detail(response.json()['id'])
                self.assertLessEqual(len(run['actions']), 12)
                if stop:
                    self.assertEqual(run['status'], 'limited')
                    self.assertIsNone(run['draft'])
                    self.assertEqual(run['candidate_version'], 0)
                    self.assertTrue(run['state']['partial_candidate']['summary'])
                else:
                    self.assertEqual(run['status'], 'completed', run.get('error'))
                    self.assertEqual(len(seen), 2)
                    self.assertIn('EVIDENCE_REQUIRED', json.dumps(seen[1]['review_feedback']))
                    self.assertTrue(seen[1]['previous_candidate']['summary'])
                    self.assertEqual(run['candidate_version'], 1)
                    self.assertEqual([a['type'] for a in run['actions']].count('query_evidence'), 2)
                    self.assertEqual([a['type'] for a in run['actions']].count('repair_draft'), 1)

    def test_invalid_model_action_can_repair_once_then_execute_valid_protocol(self):
        real_client = httpx.Client
        calls = []
        def handler(request):
            data = json.loads(json.loads(request.content)['messages'][1]['content'])
            calls.append(data)
            if len(calls) == 1:
                output = {'type': 'delete_database', 'purpose': 'synthetic-invalid-tool'}
            elif 'allowed' in data:
                output = {'type': data['allowed'][0], 'purpose': '协议恢复后执行白名单动作'}
            else:
                output = {'summary': '依据本季资料核对服务流程，专业用量由 SR 明确确认。',
                          'stages': [{'name': '核对', 'timing': '播前', 'action': '核对资料和输入。'}],
                          'selected_measure_ids': [m['id'] for m in data['available_measures']],
                          'history_decisions': [{'reference_id': h['reference_id'], 'decision': 'reference_only',
                                                 'reasons': ['效果未验证，需核对本季差异。']} for h in data['history']],
                          'pending_decisions': []}
            return httpx.Response(200, json={'choices': [{'message': {'content': json.dumps(output, ensure_ascii=False)}}]})
        transport = httpx.MockTransport(handler)
        with patch.dict(os.environ, CONFIG), patch('app.model.httpx.Client', side_effect=lambda **kw: real_client(transport=transport, **kw)):
            run = self.start()
        self.assertEqual(run['status'], 'completed', run.get('error'))
        self.assertEqual([a['type'] for a in run['actions']].count('repair_protocol'), 1)
        self.assertNotIn(CONFIG['AGRIPLAN_MODEL_API_KEY'], json.dumps(run))

    def test_repeated_invalid_model_action_stops_after_one_repair(self):
        real_client = httpx.Client
        calls = []
        def handler(request):
            calls.append(request)
            return httpx.Response(200, json={'choices': [{'message': {'content': '{"type":"delete_database","purpose":"synthetic-invalid"}'}}]})
        transport = httpx.MockTransport(handler)
        with patch.dict(os.environ, CONFIG), patch('app.model.httpx.Client', side_effect=lambda **kw: real_client(transport=transport, **kw)):
            run = self.start()
        self.assertEqual(run['status'], 'failed')
        self.assertEqual(len(calls), 2)
        self.assertIsNone(run['draft'])
        self.assertEqual([a['type'] for a in run['actions']].count('repair_protocol'), 1)

    def test_scope_revoked_during_model_call_blocks_next_tool_and_provider_call(self):
        real_client = httpx.Client
        calls = []
        def handler(request):
            data = json.loads(json.loads(request.content)['messages'][1]['content'])
            calls.append(data)
            with self.app.state.store.write() as db:
                db.execute("DELETE FROM assignments WHERE user_id='u1' AND customer_id='c1'")
            output = {'type': data['allowed'][0], 'purpose': '撤权期间返回的旧动作'}
            return httpx.Response(200, json={'choices': [{'message': {'content': json.dumps(output)}}]})
        transport = httpx.MockTransport(handler)
        with patch.dict(os.environ, CONFIG), patch('app.model.httpx.Client', side_effect=lambda **kw: real_client(transport=transport, **kw)):
            run = self.start(drain=False)
            self.drain()
        self.assertEqual(len(calls), 1)
        with self.app.state.store.read() as db:
            current = db.execute('SELECT status,state,draft FROM runs WHERE id=?', (run['id'],)).fetchone()
            self.assertEqual(current['status'], 'failed')
            self.assertIsNone(current['draft'])
            self.assertNotIn('history', json.loads(current['state']))
            self.assertEqual(db.execute('SELECT count(*) FROM actions WHERE run_id=?', (run['id'],)).fetchone()[0], 0)

    def test_http_provider_failure_does_not_trigger_protocol_retry(self):
        real_client = httpx.Client
        calls = []
        def handler(request):
            calls.append(request)
            return httpx.Response(503, json={'error': 'synthetic unavailable ' + CONFIG['AGRIPLAN_MODEL_API_KEY']})
        transport = httpx.MockTransport(handler)
        with patch.dict(os.environ, CONFIG), patch('app.model.httpx.Client', side_effect=lambda **kw: real_client(transport=transport, **kw)):
            run = self.start()
        self.assertEqual(run['status'], 'failed')
        self.assertEqual(len(calls), 1)
        self.assertNotIn(CONFIG['AGRIPLAN_MODEL_API_KEY'], json.dumps(run))
        self.assertNotIn('repair_protocol', [a['type'] for a in run['actions']])

    def test_last_question_cannot_bypass_generation_limit_or_write_an_unresumable_fact(self):
        self.edit(rate_kg_per_mu=None, rate_source='')
        run = self.start()
        for area in ['301', '302', '303']:
            self.edit(area_mu=area)
            self.refresh(self.detail(run['id']))
            self.drain()
        waiting = self.detail(run['id'])
        question = next(q for q in waiting['questions'] if q['status'] == 'open')
        before = self.client.get('/api/seasons/s1').json()
        response = self.client.post('/api/runs/' + run['id'] + '/answers', json={
            'question_id': question['id'], 'question_revision': question['revision'], 'value': '20',
            'source': 'SR 已确认，但预算不足不能保存一半', 'expected_input_revision': before['revision'],
        })
        self.assertEqual(response.status_code, 409, response.text)
        after = self.client.get('/api/seasons/s1').json()
        self.assertEqual(before['revision'], after['revision'])
        self.assertIsNone(after['facts']['rate_kg_per_mu'])
        self.assertEqual(next(q['status'] for q in self.detail(run['id'])['questions'] if q['id'] == question['id']), 'open')

    def test_stop_action_preserves_limited_state_and_cannot_confirm(self):
        real_client = httpx.Client
        def handler(request):
            data = json.loads(json.loads(request.content)['messages'][1]['content'])
            self.assertIn('stop', data['allowed'])
            return httpx.Response(200, json={'choices': [{'message': {'content': '{"type":"stop","purpose":"当前输入不足，交 SR 复核"}'}}]})
        transport = httpx.MockTransport(handler)
        with patch.dict(os.environ, CONFIG), patch('app.model.httpx.Client', side_effect=lambda **kw: real_client(transport=transport, **kw)):
            run = self.start()
        self.assertEqual(run['status'], 'limited')
        self.assertIsNone(run['draft'])
        self.assertEqual(self.client.post('/api/runs/' + run['id'] + '/confirm', json={'expected_revision': run['revision']}).status_code, 409)


if __name__ == '__main__':
    unittest.main()
