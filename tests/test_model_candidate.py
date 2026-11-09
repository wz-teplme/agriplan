import json
import os
import unittest
from unittest.mock import patch

import httpx
import test_mvp as baseline


CONFIG={'AGRIPLAN_MODE':'llm','AGRIPLAN_MODEL_BASE_URL':'https://model.example/v1',
        'AGRIPLAN_MODEL':'contract-test','AGRIPLAN_MODEL_API_KEY':'synthetic-test-key'}


class ModelCandidateTests(unittest.TestCase):
    setUp=baseline.WorkflowTests.setUp
    tearDown=baseline.WorkflowTests.tearDown
    login=baseline.WorkflowTests.login
    start=baseline.WorkflowTests.start
    drain=baseline.WorkflowTests.drain
    detail=baseline.WorkflowTests.detail

    def provider(self,modify=None):
        seen=[]
        def handler(request):
            payload=json.loads(request.content)
            data=json.loads(payload['messages'][1]['content'])
            self.assertEqual(payload['response_format'],{'type':'json_object'})
            self.assertNotIn(CONFIG['AGRIPLAN_MODEL_API_KEY'],payload['messages'][1]['content'])
            if 'allowed' in data:
                output={'type':data['allowed'][0],'purpose':'受控动作协议测试'}
            else:
                seen.append(data)
                output={'summary':'模型依据本季预算与历史差异提出先核对再服务的建议，具体专业安排由技术经理复核。',
                        'stages':[{'name':'条件复核','timing':'播前','action':'核对已确认条件与引用资料，记录未决事项。'}],
                        'selected_measure_ids':[data['available_measures'][0]['id']],
                        'history_decisions':[{'reference_id':h['reference_id'],'decision':'review_and_adjust',
                                              'reasons':['本季条件不同，不能照搬往季参数。']} for h in data['history']],
                        'pending_decisions':[]}
                if modify:
                    output=modify(output)
            return httpx.Response(200,json={'choices':[{'message':{'content':json.dumps(output,ensure_ascii=False)}}]})
        return handler,seen

    def execute(self,modify=None):
        handler,seen=self.provider(modify)
        transport=httpx.MockTransport(handler)
        real_client=httpx.Client
        def client(**kwargs):
            return real_client(transport=transport,**kwargs)
        with patch.dict(os.environ,CONFIG),patch('app.model.httpx.Client',side_effect=client):
            rid=self.start()['id']
            self.drain()
        return self.detail(rid),seen

    def test_model_generates_candidate_but_server_owns_calculation_and_review(self):
        run,seen=self.execute()
        self.assertEqual(len(seen),1)
        self.assertEqual(run['status'],'completed',run.get('error'))
        self.assertTrue(run['draft']['summary'].startswith('模型依据'))
        self.assertEqual(run['draft']['calculation']['cost_cny'],'19200.00')
        self.assertEqual(run['draft']['generation']['mode'],'model_generated')
        self.assertEqual(run['draft']['generation']['model'],'contract-test')
        self.assertEqual(run['draft']['measures'][0]['kind'],'confirmed_input')
        self.assertTrue(run['review']['can_confirm'])

    def test_unknown_measure_cannot_become_a_supported_claim(self):
        run,_=self.execute(lambda output:output|{'selected_measure_ids':['invented-measure']})
        self.assertEqual(run['status'],'failed')
        self.assertIsNone(run['draft'])
        self.assertNotIn(CONFIG['AGRIPLAN_MODEL_API_KEY'],run['error'])

    def test_model_cannot_override_money_or_input_revision(self):
        run,_=self.execute(lambda output:output|{'calculation':{'cost_cny':'0.01'},'input_revision':99})
        self.assertEqual(run['status'],'failed')
        self.assertIsNone(run['draft'])

    def test_unknown_history_reference_is_rejected(self):
        run,_=self.execute(lambda output:output|{'history_decisions':[{'reference_id':'other-customer','decision':'reference_only','reasons':['随意引用']} ]})
        self.assertEqual(run['status'],'failed')
        self.assertIsNone(run['draft'])

    def test_pending_decision_is_preserved_and_blocks_confirmation(self):
        run,_=self.execute(lambda output:output|{'pending_decisions':['现场条件尚待核实']})
        self.assertEqual(run['status'],'limited')
        self.assertIn('PENDING_DECISION',[issue['code'] for issue in run['review']['issues']])

    def test_source_revoked_during_provider_call_cannot_pass_commit_review(self):
        def revoke(output):
            with self.app.state.store.write() as db:
                db.execute("UPDATE knowledge SET status='revoked',revision=revision+1 WHERE id='k1'")
            return output
        run,_=self.execute(revoke)
        self.assertEqual(run['status'],'limited')
        self.assertIn('EVIDENCE_REVOKED',[issue['code'] for issue in run['review']['issues']])
