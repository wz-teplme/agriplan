"""Discussion never writes business facts; explicit proposals save one reviewed C."""
import json
import os
import unittest
from unittest.mock import patch

import httpx
import test_mvp as baseline


CONFIG={'AGRIPLAN_MODE':'llm','AGRIPLAN_MODEL_BASE_URL':'https://model.example/v1',
        'AGRIPLAN_MODEL':'discussion-contract','AGRIPLAN_MODEL_API_KEY':'synthetic-private-test-key'}


class DiscussionTests(unittest.TestCase):
    setUp=baseline.WorkflowTests.setUp
    tearDown=baseline.WorkflowTests.tearDown
    login=baseline.WorkflowTests.login
    start=baseline.WorkflowTests.start
    drain=baseline.WorkflowTests.drain
    detail=baseline.WorkflowTests.detail

    def ready(self):
        rid=self.start()['id']
        self.drain()
        return self.detail(rid)

    def ask(self,run,**values):
        return self.client.post('/api/runs/'+run['id']+'/discuss',json={
            'message':'能否解释本季为什么先核对现场条件？','expected_revision':run['revision'],**values})

    def propose(self,run,**values):
        return self.ask(run,intent='change_request',message='请把摘要改成先核对采购与服务条件',
            proposed_edit={'summary':'先核对本季采购与服务条件，再安排现场服务。','stages':run['draft']['stages']},**values)

    def apply(self,run,proposal):
        return self.client.post('/api/runs/'+run['id']+'/proposals/'+proposal['id']+'/apply',
            json={'expected_revision':run['revision']})

    def model_call(self,run,output,on_call=None,**request):
        seen=[]
        def handler(req):
            payload=json.loads(req.content)
            context=json.loads(payload['messages'][1]['content'])
            seen.append(context)
            if on_call:
                on_call(context)
            return httpx.Response(200,json={'choices':[{'message':{'content':json.dumps(output,ensure_ascii=False)}}]})
        real_client=httpx.Client
        def client(**kwargs):
            return real_client(transport=httpx.MockTransport(handler),**kwargs)
        with patch.dict(os.environ,CONFIG),patch('app.model.httpx.Client',side_effect=client):
            response=self.ask(run,**request)
        return response,seen

    def test_manual_proposal_does_not_edit_until_applied_and_retries_once(self):
        run=self.ready()
        response=self.propose(run)
        self.assertEqual(response.status_code,200,response.text)
        proposal=response.json()['proposal']
        pending=self.detail(run['id'])
        self.assertEqual(pending['draft'],run['draft'])
        self.assertEqual(pending['candidate_version'],1)
        self.assertEqual(proposal['content']['before']['summary'],run['draft']['summary'])
        self.assertIn('未记录',proposal['content']['reason'])
        saved=self.apply(run,proposal)
        self.assertEqual(saved.status_code,200,saved.text)
        self.assertEqual(self.apply(run,proposal).json()['candidate_version'],2)
        changed=self.detail(run['id'])
        self.assertEqual(changed['candidate_version'],2)
        self.assertEqual(changed['draft']['summary'],'先核对本季采购与服务条件，再安排现场服务。')
        self.assertEqual(changed['draft']['calculation'],run['draft']['calculation'])
        self.assertEqual(changed['snapshot'],run['snapshot'])
        self.assertEqual(changed['review']['candidate_version'],2)
        self.assertEqual(changed['proposals'][0]['status'],'applied')

    def test_model_semantic_question_calls_provider_without_writing_candidate(self):
        run=self.ready()
        self.assertEqual(self.ask(run,message='之前的问题：请解释历史沿用的边界').status_code,200)
        output={'intent':'explain','answer':'先核对本季条件，是因为往季计划不能证明本季适用或实际效果。',
            'refs':['facts:R1'],'edit':None,'pending_decisions':[]}
        def can_write(_):
            with self.app.state.store.write() as db:
                db.execute("UPDATE products SET revision=revision WHERE id='fert-1'")
        response,seen=self.model_call(run,output,on_call=can_write)
        self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(len(seen),1)
        self.assertIn('之前的问题：请解释历史沿用的边界',json.dumps(seen[0].get('prior_discussion',[]),ensure_ascii=False))
        self.assertEqual(response.json()['mode'],'semantic_model_explanation')
        self.assertIsNone(response.json()['proposal'])
        self.assertEqual(self.detail(run['id'])['draft'],run['draft'])
        self.assertNotIn('远山',json.dumps(seen,ensure_ascii=False))

    def test_model_edit_is_preview_and_only_explicit_sr_reason_is_saved(self):
        run=self.ready()
        output={'intent':'edit','answer':'建议把核对采购条件列为播前第一步。','refs':['facts:R1'],
            'edit':{'summary':'播前先核对采购条件。','stages':run['draft']['stages']},'pending_decisions':[]}
        response,_=self.model_call(run,output,intent='change_request',reason='客户要求先确认采购可行性')
        self.assertEqual(response.status_code,200,response.text)
        proposal=response.json()['proposal']
        self.assertEqual(proposal['content']['reason'],'客户要求先确认采购可行性')
        self.assertEqual(self.apply(run,proposal).status_code,200)
        current=self.detail(run['id'])
        self.assertEqual(current['versions'][0]['reason'],'客户要求先确认采购可行性')
        self.assertEqual(current['draft']['constraints'],run['draft']['constraints'])

    def test_model_unknown_refs_or_extra_business_fields_or_question_edit_are_rejected(self):
        run=self.ready()
        normal={'intent':'explain','answer':'解释','refs':[],'edit':None,'pending_decisions':[]}
        variants=[normal|{'refs':['other-customer-private']},normal|{'calculation':{'cost_cny':'0'}},
            normal|{'intent':'edit','edit':{'summary':'擅自修改','stages':run['draft']['stages']}}]
        for output in variants:
            with self.subTest(output=output):
                response,_=self.model_call(run,output)
                self.assertEqual(response.status_code,502,response.text)
                self.assertNotIn(CONFIG['AGRIPLAN_MODEL_API_KEY'],response.text)
        self.assertEqual(self.detail(run['id'])['draft'],run['draft'])
        self.assertEqual(self.detail(run['id'])['messages'],[])

    def test_missing_facts_response_does_not_confirm_variety_or_create_edit(self):
        run=self.ready()
        output={'intent':'needs_facts','answer':'换品种需要SR确认本季采购事实，保存本季资料后重新研制。',
            'refs':['facts:R1'],'edit':None,'pending_decisions':['确认客户能采购的品种及来源']}
        response,_=self.model_call(run,output,intent='change_request',message='客户只能买另一种种子，请调整')
        self.assertEqual(response.status_code,200,response.text)
        self.assertIsNone(response.json()['proposal'])
        self.assertEqual(response.json()['category'],'needs_facts')
        self.assertEqual(self.detail(run['id'])['snapshot'],run['snapshot'])

    def test_stale_request_or_draft_change_during_model_call_leaves_no_message(self):
        run=self.ready()
        self.assertEqual(self.ask(run|{'revision':run['revision']+1}).status_code,409)
        def change(_):
            response=self.client.put('/api/runs/'+run['id']+'/draft',json={
                'expected_revision':run['revision'],'summary':'另一窗口保存','stages':run['draft']['stages']})
            self.assertEqual(response.status_code,200,response.text)
        output={'intent':'explain','answer':'旧版解释','refs':[],'edit':None,'pending_decisions':[]}
        response,_=self.model_call(run,output,on_call=change)
        self.assertEqual(response.status_code,409,response.text)
        self.assertEqual(self.detail(run['id'])['messages'],[])

    def test_source_revoked_during_model_response_does_not_commit_stale_answer(self):
        run=self.ready()
        def revoke(_):
            with self.app.state.store.write() as db:
                db.execute("UPDATE knowledge SET status='revoked',revision=revision+1 WHERE id='k1'")
        output={'intent':'explain','answer':'旧资料解释','refs':['k1'],'edit':None,'pending_decisions':[]}
        response,_=self.model_call(run,output,on_call=revoke)
        self.assertEqual(response.status_code,409,response.text)
        self.assertEqual(self.detail(run['id'])['messages'],[])

    def test_apply_rechecks_quality_after_source_revocation(self):
        run=self.ready()
        response=self.propose(run)
        self.assertEqual(response.status_code,200,response.text)
        with self.app.state.store.write() as db:
            db.execute("UPDATE knowledge SET status='revoked',revision=revision+1 WHERE id='k1'")
        self.assertEqual(self.apply(run,response.json()['proposal']).status_code,200)
        current=self.detail(run['id'])
        self.assertEqual(current['status'],'limited')
        self.assertFalse(current['review']['can_confirm'])

    def test_proposal_rejects_other_version_scope_fr_and_formal_plan(self):
        run=self.ready()
        response=self.propose(run)
        self.assertEqual(response.status_code,200,response.text)
        proposal=response.json()['proposal']
        self.login('fr_zhou')
        self.assertEqual(self.propose(run).status_code,403)
        self.assertEqual(self.apply(run,proposal).status_code,403)
        self.assertEqual(self.ask(run).status_code,200)
        self.login('sr_liu')
        self.assertEqual(self.apply(run,proposal).status_code,404)
        self.login('sr_chen')
        confirmed=self.client.post('/api/runs/'+run['id']+'/confirm',json={'expected_revision':run['revision']})
        self.assertEqual(confirmed.status_code,200,confirmed.text)
        formal=self.detail(run['id'])
        self.assertEqual(self.apply(formal,proposal).status_code,409)
        self.assertEqual(self.propose(formal).status_code,409)
        self.assertEqual(formal['formal']['content'],self.detail(run['id'])['formal']['content'])

    def test_old_proposal_cannot_overwrite_manual_edit_and_missing_revision_rejected(self):
        run=self.ready()
        response=self.propose(run)
        self.assertEqual(response.status_code,200,response.text)
        self.client.put('/api/runs/'+run['id']+'/draft',json={'expected_revision':run['revision'],
            'summary':'新版摘要','stages':run['draft']['stages']})
        current=self.detail(run['id'])
        self.assertEqual(self.apply(current,response.json()['proposal']).status_code,409)
        missing=self.client.post('/api/runs/'+run['id']+'/discuss',json={'message':'请调整','intent':'change_request'})
        self.assertEqual(missing.status_code,422)

    def test_demo_natural_request_and_identical_text_create_no_proposal(self):
        run=self.ready()
        response=self.ask(run,intent='change_request',message='请将预算改成一万元')
        self.assertEqual(response.status_code,200,response.text)
        self.assertIsNone(response.json()['proposal'])
        response=self.ask(run,intent='change_request',proposed_edit={
            key:run['draft'][key] for key in ('summary','stages')})
        self.assertEqual(response.status_code,200,response.text)
        self.assertIsNone(response.json()['proposal'])
        self.assertEqual(self.detail(run['id'])['snapshot'],run['snapshot'])
        self.assertEqual(self.detail(run['id'])['candidate_version'],1)

    def test_explicit_reason_is_not_classified_by_its_wording(self):
        run=self.ready()
        response=self.propose(run,reason='未记录的采购条件现已补充确认，仅调整服务表述')
        self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(self.apply(run,response.json()['proposal']).status_code,200)
        event=next(item for item in self.detail(run['id'])['audit'] if item['event']=='discussion_proposal_applied')
        self.assertEqual(event['payload']['reason_source'],'sr_explicit')

    def test_demo_cooperation_total_question_uses_current_multiline_cost(self):
        run=self.ready()
        response=self.ask(run,message='合作合计为什么是19200元？')
        self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(response.json()['category'],'cost')
        self.assertIn('19200.00',response.json()['answer'])
        self.assertEqual(self.detail(run['id'])['candidate_version'],1)
