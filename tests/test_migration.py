"""Upgrade a real v1-shaped database without rewriting its business history."""
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from app.domain import dumps
from app.storage import SCHEMA, Store


class MigrationTests(unittest.TestCase):
    def test_v5_module_upgrade_keeps_actions_and_business_history_without_inventing_receipts(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1]) as folder:
            path=Path(folder)/'v5.db'
            store=Store(path)
            with store.write() as db:
                db.execute("INSERT INTO runs(id,season_id,actor_id,goal,status,snapshot,state,mode,created_at) VALUES('v5-run','s1','u1','旧研究','completed','{}','{}','demo','2026-01-01')")
                columns={row['name'] for row in db.execute('PRAGMA table_info(actions)')}
                if 'generation' in columns:
                    db.execute('ALTER TABLE actions DROP COLUMN generation')
                db.execute("INSERT INTO actions VALUES('v5-action','v5-run',1,'query_history','旧历史动作','[]','2026-01-01')")
                db.execute('DROP TABLE IF EXISTS research_results')
                db.execute('DROP TABLE IF EXISTS research_refreshes')
                db.execute('PRAGMA user_version=5')
                before={name:[tuple(row) for row in db.execute('SELECT * FROM '+name)]
                        for name in ('runs','seasons','fact_versions','history','products','knowledge')}
            Store(path); reopened=Store(path)
            with reopened.read() as db:
                self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0],6)
                for name,rows in before.items():
                    self.assertEqual([tuple(row) for row in db.execute('SELECT * FROM '+name)],rows)
                action=dict(db.execute("SELECT * FROM actions WHERE id='v5-action'").fetchone())
                self.assertEqual(action['generation'],1)
                self.assertEqual(action['output'],'[]')
                for table in ('research_results','research_refreshes'):
                    self.assertEqual(db.execute('SELECT count(*) FROM '+table).fetchone()[0],0)

    def test_v1_plan_candidate_question_and_record_survive_repeated_upgrade(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1]) as folder:
            path=Path(folder)/'legacy.db'
            db=sqlite3.connect(path)
            # v1 had the same core tables and only seven knowledge columns.
            legacy='\n'.join(line for line in SCHEMA.splitlines()
                             if not any(name in line for name in ('candidate_reviews','reviews_run','answer_contexts')))
            db.executescript(legacy)
            db.execute('PRAGMA user_version=1')
            db.execute("INSERT INTO users VALUES('u1','legacy','旧用户','SR','keep-hash')")
            db.execute("INSERT INTO customers VALUES('c1','旧客户','吉林')")
            db.execute("INSERT INTO assignments VALUES('u1','c1')")
            db.execute("INSERT INTO plots VALUES('p1','c1','旧田')")
            db.execute("INSERT INTO seasons VALUES('s1','p1','c1',2026,'玉米',3,'{}','P-old')")
            db.execute("INSERT INTO runs(id,season_id,actor_id,goal,status,revision,snapshot,state,draft,candidate_version,formal_id,mode,created_at) VALUES('r1','s1','u1','旧研究','completed',9,'{}','{}',?,2,'P-old','demo','2026-01-01')",(dumps({'summary':'原草稿'}),))
            db.execute("INSERT INTO candidate_versions VALUES('r1',2,?,'u1','原理由','2026-01-01')",(dumps({'summary':'原草稿'}),))
            content=dumps({'summary':'已签存的原技术方案','schema_version':1})
            db.execute("INSERT INTO plans VALUES('P-old','s1','r1',1,?,3,'u1','2026-01-01')",(content,))
            db.execute("INSERT INTO questions VALUES('q1','r1',1,'rate','原问题','answered','{}','u1')")
            db.execute("INSERT INTO records VALUES('field1','s1','u1','inspection','2026-01-02','原实际','P-old','2026-01-02')")
            db.execute("INSERT INTO knowledge VALUES('k1','原资料','原正文','通用','原来源','原定位','demo-v1')")
            before={table:db.execute('SELECT * FROM '+table).fetchall() for table in ('plans','candidate_versions','questions','records','runs')}
            db.commit()
            db.close()
            store=Store(path)
            with store.read() as current:
                self.assertEqual(current.execute('PRAGMA user_version').fetchone()[0],6)
                for table,rows in before.items():
                    self.assertEqual([tuple(r) for r in current.execute('SELECT * FROM '+table)],rows)
                self.assertEqual(current.execute('SELECT count(*) FROM candidate_reviews').fetchone()[0],0)
                self.assertEqual(current.execute("SELECT body FROM knowledge WHERE id='k1'").fetchone()[0],'原正文')
            with store.write() as current:
                current.execute("UPDATE knowledge SET status='revoked' WHERE id='k1'")
            Store(path)
            with store.read() as current:
                self.assertEqual(current.execute("SELECT status FROM knowledge WHERE id='k1'").fetchone()[0],'revoked')
                self.assertEqual(json.loads(current.execute('SELECT content FROM plans').fetchone()[0])['schema_version'],1)

    def test_v2_upgrade_adds_question_metadata_without_rewriting_waiting_jobs(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1]) as folder:
            path=Path(folder)/'v2.db'
            store=Store(path)
            with store.write() as db:
                db.execute('DROP TABLE question_contexts')
                db.execute('PRAGMA user_version=2')
                db.execute("INSERT INTO runs(id,season_id,actor_id,goal,status,snapshot,state,mode,created_at) VALUES('waiting','s2','u1','旧等待研究','waiting_input','{}','{}','demo','2026-01-01')")
                db.execute("INSERT INTO jobs VALUES('job','waiting',1,'done',NULL,NULL,1)")
                db.execute("INSERT INTO questions VALUES('q-old','waiting',1,'rate_kg_per_mu','原用量问题','open',NULL,NULL)")
            Store(path)
            Store(path)
            with store.read() as db:
                self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0],6)
                self.assertEqual(db.execute('SELECT count(*) FROM question_contexts').fetchone()[0],0)
                self.assertEqual(db.execute("SELECT status FROM runs WHERE id='waiting'").fetchone()[0],'waiting_input')
                self.assertEqual(db.execute('SELECT count(*) FROM jobs').fetchone()[0],1)

    def test_v3_upgrade_preserves_messages_and_candidate_content(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1]) as folder:
            path=Path(folder)/'v3.db'
            store=Store(path)
            with store.write() as db:
                db.execute("INSERT INTO runs(id,season_id,actor_id,goal,status,snapshot,state,draft,mode,created_at) VALUES('legacy','s1','u1','旧任务','completed','{}','{}',?,'demo','2026-01-01')",(dumps({'summary':'旧草稿'}),))
                db.execute("INSERT INTO candidate_versions VALUES('legacy',1,?,'u1','未记录','2026-01-01')",(dumps({'summary':'旧草稿'}),))
                db.execute("INSERT INTO messages VALUES('old-msg','legacy','u1','旧问题','旧解释','2026-01-01')")
                db.execute("INSERT INTO answer_contexts VALUES('old-msg','cost','[]','{}','rule_question_explanation')")
                db.execute('DROP TABLE discussion_proposals')
                db.execute('PRAGMA user_version=3')
                before={name:[tuple(row) for row in db.execute('SELECT * FROM '+name)]
                        for name in ('runs','candidate_versions','messages','answer_contexts')}
            Store(path)
            reopened=Store(path)
            with reopened.read() as db:
                self.assertEqual(db.execute('SELECT count(*) FROM discussion_proposals').fetchone()[0],0)
                self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0],6)
                for name,rows in before.items():
                    self.assertEqual([tuple(row) for row in db.execute('SELECT * FROM '+name)],rows)

    def test_v4_report_tables_added_without_rewriting_existing_history(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1]) as folder:
            path=Path(folder)/'v4.db'
            store=Store(path)
            with store.write() as db:
                for name in ('fact_report_sources','report_extractions','reports'):
                    db.execute('DROP TABLE '+name)
                db.execute('PRAGMA user_version=4')
                before={name:[tuple(row) for row in db.execute('SELECT * FROM '+name)]
                        for name in ('seasons','fact_versions','history','products','knowledge')}
            Store(path); reopened=Store(path)
            with reopened.read() as db:
                self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0],6)
                for name in ('reports','report_extractions','fact_report_sources'):
                    self.assertEqual(db.execute('SELECT count(*) FROM '+name).fetchone()[0],0)
                for name,rows in before.items():
                    self.assertEqual([tuple(row) for row in db.execute('SELECT * FROM '+name)],rows)
