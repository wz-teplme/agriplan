import json
import unittest
from datetime import datetime, timedelta, timezone

import test_mvp as baseline


class ResearchV2Tests(unittest.TestCase):
    setUp=baseline.WorkflowTests.setUp
    tearDown=baseline.WorkflowTests.tearDown
    login=baseline.WorkflowTests.login
    start=baseline.WorkflowTests.start
    drain=baseline.WorkflowTests.drain
    detail=baseline.WorkflowTests.detail
    def document(self, **changes):
        body={'title':'北区排水巡查流程','body':'北区排水巡查时先核实排水口与现场情况，再记录实际；不自动决定施药。',
              'kind':'procedure','purpose':'plan_research','crop':'玉米','region':'吉林',
              'source':'SR 人工审核的演示服务规范','locator':'第一节','demo':True,
              'customer_id':None,'supported_claims':['核实排水口与现场情况后保存巡查实际。']}
        response=self.client.post('/api/documents',json=body|changes)
        self.assertEqual(response.status_code,201,response.text)
        return response.json()

    def test_document_publication_revocation_and_scope(self):
        doc=self.document()
        before=self.client.post('/api/knowledge',json={'message':'北区排水巡查'}).json()['evidence']
        self.assertNotIn(doc['id'],[d['id'] for d in before])
        published=self.client.post(f"/api/documents/{doc['id']}/publish",json={'expected_revision':doc['revision'],'note':'SR 复核：仅供演示流程参考'})
        self.assertEqual(published.status_code,200,published.text)
        evidence=self.client.post('/api/knowledge',json={'message':'北区排水巡查'}).json()['evidence']
        self.assertIn(doc['id'],[d['id'] for d in evidence])
        revoked=self.client.post(f"/api/documents/{doc['id']}/revoke",json={'expected_revision':published.json()['revision'],'note':'适用条件需修订，暂停当前使用'})
        self.assertEqual(revoked.status_code,200,revoked.text)
        after=self.client.post('/api/knowledge',json={'message':'北区排水巡查'}).json()['evidence']
        self.assertNotIn(doc['id'],[d['id'] for d in after])
        self.assertEqual(self.client.get(f"/api/documents/{doc['id']}").json()['status'],'revoked')
        self.login('sr_liu')
        private=self.document(customer_id='c3')
        self.client.post(f"/api/documents/{private['id']}/publish",json={'expected_revision':1,'note':'确认客户范围'})
        self.login('sr_chen')
        self.assertEqual(self.client.get(f"/api/documents/{private['id']}").status_code,404)

    def test_empty_evidence_yields_limited_draft_and_cannot_confirm(self):
        with self.app.state.store.write() as db:
            db.execute('DELETE FROM knowledge')
        run_id=self.start()['id']
        self.drain()
        run=self.detail(run_id)
        self.assertEqual(run['status'],'limited')
        self.assertIsNotNone(run['draft'])
        self.assertFalse(run['review']['can_confirm'])
        self.assertIn('EVIDENCE_REQUIRED',[i['code'] for i in run['review']['issues']])
        response=self.client.post(f'/api/runs/{run_id}/confirm',json={'expected_revision':run['revision']})
        self.assertEqual(response.status_code,409)

    def test_published_evidence_revocation_blocks_existing_draft(self):
        run_id=self.start()['id']
        self.drain()
        run=self.detail(run_id)
        evidence_id=run['draft']['evidence_refs'][0]
        doc=self.client.get('/api/documents/'+evidence_id).json()
        response=self.client.post(f'/api/documents/{evidence_id}/revoke',json={'expected_revision':doc['revision'],'note':'来源已撤回'})
        self.assertEqual(response.status_code,200,response.text)
        current=self.detail(run_id)
        result=self.client.post(f'/api/runs/{run_id}/confirm',json={'expected_revision':current['revision']})
        self.assertEqual(result.status_code,409,result.text)
        updated=self.detail(run_id)
        self.assertFalse(updated['review']['can_confirm'])
        self.assertIn('EVIDENCE_REVOKED',[i['code'] for i in updated['review']['issues']])

    def test_review_binds_candidate_and_professional_claim_needs_support(self):
        from app.review import inspect_candidate
        run_id=self.start()['id']
        self.drain()
        first=self.detail(run_id)
        self.assertTrue(first['draft']['measures'])
        self.assertTrue(first['draft']['history_decisions'])
        self.assertEqual(first['review']['candidate_version'],1)
        edited=self.client.put(f'/api/runs/{run_id}/draft',json={'expected_revision':first['revision'],'summary':'SR 明确确认的表述调整','stages':first['draft']['stages'],'reason':'采购条件'})
        self.assertEqual(edited.status_code,200,edited.text)
        second=self.detail(run_id)
        self.assertEqual(second['review']['candidate_version'],2)
        self.assertNotEqual(first['review']['content_hash'],second['review']['content_hash'])
        unsupported=second['draft']|{'measures':[{'id':'bad','kind':'professional','text':'没有审核依据的专业施药处方','evidence_refs':['k1'],'fact_refs':[],'calculation_refs':[],'conditions':[]}]}
        with self.app.state.store.read() as db:
            review=inspect_candidate(db,second,unsupported)
        self.assertFalse(review['can_confirm'])
        self.assertIn('PROFESSIONAL_SUPPORT_REQUIRED',[i['code'] for i in review['issues']])

    def test_question_specific_answers_preserve_candidate(self):
        run_id=self.start()['id']
        self.drain()
        before=self.detail(run_id)
        answers=[]
        for message in ['产品预算怎么算？','历史方案有哪些变化？','合同是否已锁价？']:
            response=self.client.post(f'/api/runs/{run_id}/discuss',json={'message':message})
            self.assertEqual(response.status_code,200,response.text)
            answers.append(response.json())
        self.assertEqual(len({a['category'] for a in answers}),3)
        self.assertEqual(len({a['answer'] for a in answers}),3)
        self.assertIn('19200.00',answers[0]['answer'])
        self.assertIn('未接入',answers[2]['answer'])
        self.assertEqual(self.detail(run_id)['candidate_version'],before['candidate_version'])

    def test_old_database_reopen_and_document_version_preserve_history(self):
        from app.storage import Store
        run_id=self.start()['id']
        self.drain()
        first=self.detail(run_id)
        plan=self.client.post(f'/api/runs/{run_id}/confirm',json={'expected_revision':first['revision']}).json()
        doc=self.document()
        pub=self.client.post(f"/api/documents/{doc['id']}/publish",json={'expected_revision':1,'note':'发布初版'}).json()
        revised=self.client.post(f"/api/documents/{doc['id']}/versions",json={'expected_revision':pub['revision'],'title':'北区排水巡查修订','body':'修订后的北区排水巡查流程，先核实现场并保存记录。','kind':'procedure','purpose':'plan_research','crop':'玉米','region':'吉林','source':'SR 审核演示规范','locator':'第一节修订','demo':True,'customer_id':None,'supported_claims':['先核实现场并保存记录。']})
        self.assertEqual(revised.status_code,201,revised.text)
        self.assertNotEqual(revised.json()['id'],doc['id'])
        Store(self.app.state.store.path)
        reopened=self.detail(run_id)
        self.assertEqual(reopened['formal']['content'],plan['content'])
        self.assertEqual(self.client.get(f"/api/documents/{doc['id']}").json()['body'],doc['body'])

    def test_expired_document_cannot_publish_as_current_evidence(self):
        old=(datetime.now(timezone.utc)-timedelta(days=1)).isoformat()
        doc=self.document(valid_until=old)
        result=self.client.post(f"/api/documents/{doc['id']}/publish",json={'expected_revision':1,'note':'过期测试'})
        self.assertEqual(result.status_code,422)

    def test_read_review_has_no_mutation_and_write_review_checks_role_and_revision(self):
        run_id=self.start()['id']
        self.drain()
        before=self.detail(run_id)
        with self.app.state.store.read() as db:
            count=db.execute('SELECT count(*) FROM candidate_reviews').fetchone()[0]
        self.assertEqual(self.client.get(f'/api/runs/{run_id}/review').status_code,200)
        self.assertEqual(self.detail(run_id)['revision'],before['revision'])
        with self.app.state.store.read() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM candidate_reviews').fetchone()[0],count)
        self.assertEqual(self.client.post(f'/api/runs/{run_id}/review',json={'expected_revision':before['revision']-1}).status_code,409)
        self.login('fr_zhou')
        self.assertEqual(self.client.post(f'/api/runs/{run_id}/review',json={'expected_revision':before['revision']}).status_code,403)

    def test_fr_sees_current_revoked_source_identity_without_rewriting_confirmed_plan(self):
        run_id=self.start()['id']
        self.drain()
        before=self.detail(run_id)
        plan=self.client.post(f'/api/runs/{run_id}/confirm',json={'expected_revision':before['revision']}).json()
        eid=before['draft']['evidence_refs'][0]
        doc=self.client.get('/api/documents/'+eid).json()
        self.client.post('/api/documents/'+eid+'/revoke',json={'expected_revision':doc['revision'],'note':'原资料撤回，需查新依据'})
        self.login('fr_zhou')
        after=self.detail(run_id)
        status=next(item for item in after['evidence_statuses'] if item['id']==eid)
        self.assertEqual(status['error'],'EVIDENCE_REVOKED')
        self.assertEqual(status['document']['publication_note'],'原资料撤回，需查新依据')
        self.assertEqual(after['formal']['content'],plan['content'])
        self.assertEqual(self.client.get('/api/documents/'+eid).status_code,404)
        self.assertEqual(self.client.get('/api/documents').json(),[d for d in self.client.get('/api/documents').json() if d['status']=='published'])
