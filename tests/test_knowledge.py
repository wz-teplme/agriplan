import importlib
import importlib.util
import json
import sqlite3
import tempfile
import unittest
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import HTTPException


class DocumentStore:
    def __init__(self, path):
        self.path = path

    @contextmanager
    def read(self):
        db = sqlite3.connect(self.path)
        db.row_factory = sqlite3.Row
        try:
            yield db
        finally:
            db.close()

    @contextmanager
    def write(self):
        with self.read() as db:
            try:
                yield db
                db.commit()
            except BaseException:
                db.rollback()
                raise


class KnowledgeTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(importlib.util.find_spec('app.knowledge'), '资料生命周期模块尚未实现')
        self.k = importlib.import_module('app.knowledge')
        self.tmp = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1])
        self.addCleanup(self.tmp.cleanup)
        self.store = DocumentStore(str(Path(self.tmp.name) / 'knowledge.db'))
        self.sr = {'id': 'u1', 'role': 'SR'}
        self.other = {'id': 'u2', 'role': 'SR'}
        self.fr = {'id': 'u3', 'role': 'FR'}
        with self.store.write() as db:
            db.execute('CREATE TABLE knowledge(id TEXT PRIMARY KEY,title TEXT,body TEXT,crop TEXT,source TEXT,locator TEXT,version TEXT)')
            db.execute('CREATE TABLE assignments(user_id TEXT,customer_id TEXT)')
            db.execute('CREATE TABLE audit(id TEXT PRIMARY KEY,season_id TEXT,run_id TEXT,actor_id TEXT,event TEXT,payload TEXT,created_at TEXT)')
            db.executemany('INSERT INTO assignments VALUES(?,?)', [('u1','c1'),('u2','c2'),('u3','c1')])
            db.executemany('INSERT INTO knowledge VALUES(?,?,?,?,?,?,?)', [('k1','历史预算流程','先核对预算和历史。','通用','演示手册','第一节','demo-v1'), ('old','旧资料','未经审核。','通用','旧来源','第二节','v1')])
            self.k.ensure_schema(db)

    def document(self, actor=None, **changes):
        data = {'title': '排水巡查', 'body': '核实排水口后记录现场。', 'kind': 'procedure', 'purpose': 'plan_research', 'crop': '玉米', 'region': '吉林', 'source': '演示服务规范', 'locator': '第一节', 'demo': True, 'customer_id': None, 'supported_claims': ['核实排水口后记录现场。']}
        return self.k.create_document(self.store, actor or self.sr, data | changes)

    def published(self, **changes):
        doc = self.document(**changes)
        return self.k.publish_document(self.store, self.sr, doc['id'], 1, '审核后发布')

    def assert_http(self, status, function, *args):
        with self.assertRaises(HTTPException) as caught:
            function(*args)
        self.assertEqual(caught.exception.status_code, status)

    def test_legacy_migration_keeps_revocation_and_caller_transaction(self):
        with self.store.write() as db:
            migrated = dict(db.execute('SELECT * FROM knowledge WHERE id="k1"').fetchone())
            self.assertEqual(migrated['status'], 'published')
            self.assertEqual(migrated['kind'], 'procedure')
            self.assertEqual(json.loads(migrated['supported_claims']), ['先核对预算和历史。'])
            self.assertEqual(db.execute('SELECT status FROM knowledge WHERE id="old"').fetchone()[0], 'draft')
            db.execute('UPDATE knowledge SET status="revoked" WHERE id="k1"')
            self.k.ensure_schema(db)
            self.assertEqual(db.execute('SELECT status FROM knowledge WHERE id="k1"').fetchone()[0], 'revoked')
            self.assertTrue(db.in_transaction)

    def test_draft_publication_revoke_revision_and_audit(self):
        doc = self.document()
        with self.store.read() as db:
            self.assertEqual(self.k.search_documents(db, '排水巡查'), [])
        published = self.k.publish_document(self.store, self.sr, doc['id'], 1, '仅供流程参考')
        self.assertEqual(published['revision'], 2)
        with self.store.read() as db:
            self.assertEqual([x['id'] for x in self.k.search_documents(db, '排水巡查')], [doc['id']])
            self.assertEqual(self.k.search_documents(db, '完全无关内容'), [])
        self.assert_http(409, self.k.revoke_document, self.store, self.sr, doc['id'], 1, '撤回')
        revoked = self.k.revoke_document(self.store, self.sr, doc['id'], 2, '来源待复查')
        self.assertEqual(revoked['status'], 'revoked')
        with self.store.read() as db:
            self.assertEqual(self.k.search_documents(db, '排水巡查'), [])
            self.assertEqual(self.k.evidence_status(db, doc['id'], '玉米', '吉林', 'c1')[1], 'EVIDENCE_REVOKED')
            self.assertEqual(db.execute('SELECT COUNT(*) FROM audit').fetchone()[0], 3)

    def test_scope_roles_and_shared_ownership(self):
        private = self.published(customer_id='c1')
        self.assert_http(404, self.k.get_document, self.store, self.other, private['id'])
        self.assert_http(404, self.k.create_document, self.store, self.other, {'customer_id': 'c1'})
        self.assertEqual(self.k.get_document(self.store, self.fr, private['id'])['id'], private['id'])
        draft = self.document()
        self.assert_http(404, self.k.get_document, self.store, self.fr, draft['id'])
        self.assert_http(403, self.k.publish_document, self.store, self.fr, private['id'], 2, '无权')
        self.assert_http(403, self.k.publish_document, self.store, self.other, draft['id'], 1, '无权')
        self.assertNotIn(draft['id'], [d['id'] for d in self.k.list_documents(self.store, self.fr)])
        with self.store.read() as db:
            self.assertEqual(self.k.search_documents(db, '排水巡查', customer_id='c2'), [])
            self.assertEqual(self.k.evidence_status(db, private['id'], '玉米', '吉林', 'c2')[1], 'EVIDENCE_SCOPE')

    def test_evidence_validity_and_applicability(self):
        doc = self.published()
        with self.store.read() as db:
            self.assertIsNone(self.k.evidence_status(db, doc['id'], '玉米', '吉林', 'c1')[1])
            self.assertEqual(self.k.evidence_status(db, doc['id'], '水稻', '吉林', 'c1')[1], 'EVIDENCE_NOT_APPLICABLE')
            self.assertEqual(self.k.search_documents(db, '排水巡查', crop='玉米', region='黑龙江'), [])
            self.assertEqual(self.k.evidence_status(db, 'missing', '玉米', '吉林', 'c1')[1], 'EVIDENCE_NOT_FOUND')
        old = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
        expired = self.document(valid_until=old)
        self.assert_http(422, self.k.publish_document, self.store, self.sr, expired['id'], 1, '过期发布')
        with self.store.write() as db:
            db.execute('UPDATE knowledge SET valid_until=? WHERE id=?', (old, doc['id']))
            self.assertEqual(self.k.evidence_status(db, doc['id'], '玉米', '吉林', 'c1')[1], 'EVIDENCE_EXPIRED')

    def test_version_publication_preserves_old_text_and_supersedes_evidence(self):
        previous = self.published()
        data = {k: previous[k] for k in ('title','body','kind','purpose','crop','region','source','locator','demo','customer_id','supported_claims')}
        revised = self.k.create_document(self.store, self.sr, data | {'body': '新流程正文', 'expected_revision': 2}, previous_id=previous['id'])
        self.assertEqual(revised['document_id'], previous['document_id'])
        self.assertEqual(revised['version_number'], 2)
        self.assertNotEqual(revised['id'], previous['id'])
        self.k.publish_document(self.store, self.sr, revised['id'], 1, '修订发布')
        old = self.k.get_document(self.store, self.sr, previous['id'])
        self.assertEqual(old['body'], '核实排水口后记录现场。')
        self.assertEqual(old['status'], 'superseded')
        with self.store.read() as db:
            self.assertEqual(self.k.evidence_status(db, old['id'], '玉米', '吉林', 'c1')[1], 'EVIDENCE_NOT_PUBLISHED')

    def test_malformed_customer_scope_is_validation_error(self):
        self.assert_http(422, lambda: self.document(customer_id=['c1']))

    def test_revoking_newer_version_cannot_publish_an_older_draft(self):
        first=self.document()
        payload={key:first[key] for key in ('title','body','kind','purpose','crop','region','source','locator','demo','customer_id','supported_claims')}
        second=self.k.create_document(self.store,self.sr,payload|{'expected_revision':1},previous_id=first['id'])
        published=self.k.publish_document(self.store,self.sr,second['id'],1,'新版审核通过')
        self.k.revoke_document(self.store,self.sr,second['id'],published['revision'],'新版来源需复查')
        self.assert_http(409,self.k.publish_document,self.store,self.sr,first['id'],1,'试图回退旧草稿')


if __name__ == '__main__':
    unittest.main()
