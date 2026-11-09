"""SQLite connections and atomic business writes. No network calls in transactions."""
import sqlite3
from contextlib import contextmanager
from pathlib import Path


SCHEMA = '''
CREATE TABLE IF NOT EXISTS users(id TEXT PRIMARY KEY, username TEXT UNIQUE NOT NULL, name TEXT NOT NULL, role TEXT NOT NULL, password_hash TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS sessions(token_hash TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id), expires_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS customers(id TEXT PRIMARY KEY, name TEXT NOT NULL, region TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS assignments(user_id TEXT REFERENCES users(id), customer_id TEXT REFERENCES customers(id), PRIMARY KEY(user_id,customer_id));
CREATE TABLE IF NOT EXISTS plots(id TEXT PRIMARY KEY, customer_id TEXT REFERENCES customers(id), name TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS seasons(id TEXT PRIMARY KEY, plot_id TEXT REFERENCES plots(id), customer_id TEXT REFERENCES customers(id), year INTEGER NOT NULL, crop TEXT NOT NULL, revision INTEGER NOT NULL, facts TEXT NOT NULL, current_plan_id TEXT);
CREATE TABLE IF NOT EXISTS fact_versions(season_id TEXT REFERENCES seasons(id), revision INTEGER NOT NULL, facts TEXT NOT NULL, actor_id TEXT REFERENCES users(id), source TEXT NOT NULL, created_at TEXT NOT NULL, PRIMARY KEY(season_id,revision));
CREATE TABLE IF NOT EXISTS history(id TEXT PRIMARY KEY, plot_id TEXT, year INTEGER NOT NULL, crop TEXT NOT NULL, region TEXT NOT NULL, published INTEGER NOT NULL DEFAULT 0, content TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS products(id TEXT PRIMARY KEY, name TEXT NOT NULL, price TEXT NOT NULL, unit TEXT NOT NULL, revision INTEGER NOT NULL, active INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS knowledge(id TEXT PRIMARY KEY, title TEXT NOT NULL, body TEXT NOT NULL, crop TEXT NOT NULL, source TEXT NOT NULL, locator TEXT NOT NULL, version TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS runs(id TEXT PRIMARY KEY, season_id TEXT NOT NULL REFERENCES seasons(id), actor_id TEXT NOT NULL REFERENCES users(id), goal TEXT NOT NULL, status TEXT NOT NULL, revision INTEGER NOT NULL DEFAULT 1, snapshot TEXT NOT NULL, state TEXT NOT NULL, draft TEXT, candidate_version INTEGER NOT NULL DEFAULT 0, formal_id TEXT, mode TEXT NOT NULL, error TEXT, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS jobs(id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES runs(id), generation INTEGER NOT NULL, status TEXT NOT NULL, token TEXT, lease_until REAL, attempts INTEGER NOT NULL DEFAULT 0, UNIQUE(run_id,generation));
CREATE TABLE IF NOT EXISTS questions(id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES runs(id), revision INTEGER NOT NULL, field TEXT NOT NULL, prompt TEXT NOT NULL, status TEXT NOT NULL, answer TEXT, actor_id TEXT, UNIQUE(run_id,field,revision));
CREATE TABLE IF NOT EXISTS question_contexts(question_id TEXT PRIMARY KEY REFERENCES questions(id), answer_type TEXT NOT NULL, purpose TEXT NOT NULL, impact TEXT NOT NULL, options TEXT NOT NULL, input_revision INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS actions(id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES runs(id), sequence INTEGER NOT NULL, type TEXT NOT NULL, purpose TEXT NOT NULL, output TEXT NOT NULL, created_at TEXT NOT NULL, UNIQUE(run_id,sequence));
CREATE TABLE IF NOT EXISTS candidate_versions(run_id TEXT NOT NULL REFERENCES runs(id), version INTEGER NOT NULL, content TEXT NOT NULL, actor_id TEXT REFERENCES users(id), reason TEXT NOT NULL, created_at TEXT NOT NULL, PRIMARY KEY(run_id,version));
CREATE TABLE IF NOT EXISTS plans(id TEXT PRIMARY KEY, season_id TEXT NOT NULL REFERENCES seasons(id), run_id TEXT NOT NULL REFERENCES runs(id), version INTEGER NOT NULL, content TEXT NOT NULL, input_revision INTEGER NOT NULL, actor_id TEXT NOT NULL REFERENCES users(id), created_at TEXT NOT NULL, UNIQUE(season_id,version));
CREATE TABLE IF NOT EXISTS records(id TEXT PRIMARY KEY, season_id TEXT NOT NULL REFERENCES seasons(id), actor_id TEXT NOT NULL REFERENCES users(id), kind TEXT NOT NULL, observed_at TEXT NOT NULL, notes TEXT NOT NULL, plan_id TEXT REFERENCES plans(id), created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS audit(id TEXT PRIMARY KEY, season_id TEXT REFERENCES seasons(id), run_id TEXT REFERENCES runs(id), actor_id TEXT REFERENCES users(id), event TEXT NOT NULL, payload TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS messages(id TEXT PRIMARY KEY, run_id TEXT REFERENCES runs(id), actor_id TEXT REFERENCES users(id), message TEXT NOT NULL, answer TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS answer_contexts(message_id TEXT PRIMARY KEY REFERENCES messages(id), category TEXT NOT NULL, refs TEXT NOT NULL, versions TEXT NOT NULL, mode TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS discussion_proposals(id TEXT PRIMARY KEY REFERENCES messages(id), run_id TEXT NOT NULL REFERENCES runs(id), actor_id TEXT NOT NULL REFERENCES users(id), binding TEXT NOT NULL, content TEXT NOT NULL, status TEXT NOT NULL, applied_version INTEGER, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS reports(id TEXT PRIMARY KEY, season_id TEXT NOT NULL REFERENCES seasons(id), filename TEXT NOT NULL, mime_type TEXT NOT NULL, sha256 TEXT NOT NULL, size_bytes INTEGER NOT NULL, revision INTEGER NOT NULL, status TEXT NOT NULL, current_extraction_id TEXT, confirmed_input_revision INTEGER, confirmation_source TEXT, confirmed_by TEXT REFERENCES users(id), confirmed_at TEXT, uploaded_by TEXT NOT NULL REFERENCES users(id), uploaded_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS report_extractions(id TEXT PRIMARY KEY, report_id TEXT NOT NULL REFERENCES reports(id), sequence INTEGER NOT NULL, method TEXT NOT NULL, pages TEXT NOT NULL, candidate TEXT, issues TEXT NOT NULL, actor_id TEXT NOT NULL REFERENCES users(id), created_at TEXT NOT NULL, UNIQUE(report_id,sequence));
CREATE TABLE IF NOT EXISTS fact_report_sources(season_id TEXT NOT NULL, revision INTEGER NOT NULL, field TEXT NOT NULL, report_id TEXT NOT NULL REFERENCES reports(id), extraction_id TEXT NOT NULL REFERENCES report_extractions(id), PRIMARY KEY(season_id,revision,field), FOREIGN KEY(season_id,revision) REFERENCES fact_versions(season_id,revision));
CREATE TABLE IF NOT EXISTS idempotency(actor_id TEXT REFERENCES users(id), key TEXT NOT NULL, payload_hash TEXT NOT NULL, response TEXT NOT NULL, PRIMARY KEY(actor_id,key));
CREATE TABLE IF NOT EXISTS candidate_reviews(id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES runs(id), candidate_version INTEGER NOT NULL, content_hash TEXT NOT NULL, input_revision INTEGER NOT NULL, evidence_fingerprint TEXT NOT NULL, rule_version TEXT NOT NULL, dimensions TEXT NOT NULL, issues TEXT NOT NULL, can_confirm INTEGER NOT NULL, created_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS reviews_run ON candidate_reviews(run_id,candidate_version);
CREATE INDEX IF NOT EXISTS jobs_ready ON jobs(status,lease_until);
CREATE INDEX IF NOT EXISTS runs_season ON runs(season_id,created_at);
CREATE INDEX IF NOT EXISTS records_season ON records(season_id,observed_at);
CREATE TABLE IF NOT EXISTS research_results(id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES runs(id), module TEXT NOT NULL, input_hash TEXT NOT NULL, input_refs TEXT NOT NULL, result TEXT NOT NULL, generation INTEGER NOT NULL, input_revision INTEGER NOT NULL, created_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS research_results_run ON research_results(run_id,module);
CREATE TABLE IF NOT EXISTS research_refreshes(id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES runs(id), actor_id TEXT NOT NULL REFERENCES users(id), request_hash TEXT NOT NULL, context_hash TEXT NOT NULL, intent TEXT NOT NULL, generation INTEGER NOT NULL, created_at TEXT NOT NULL, UNIQUE(run_id,actor_id,request_hash));
'''


class Store:
    def __init__(self, path):
        self.path = str(Path(path).resolve())
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        with self.read() as db:
            db.execute('PRAGMA journal_mode=WAL')
            version = db.execute('PRAGMA user_version').fetchone()[0]
            if version not in (0, 1, 2, 3, 4, 5, 6):
                raise RuntimeError('不兼容的数据库版本，拒绝覆盖')
            db.executescript(SCHEMA)
        from .seed import seed
        seed(self)
        from .knowledge import ensure_schema
        with self.write() as db:
            ensure_schema(db)
            columns = {row['name'] for row in db.execute('PRAGMA table_info(actions)')}
            if 'generation' not in columns:
                db.execute('ALTER TABLE actions ADD COLUMN generation INTEGER NOT NULL DEFAULT 1')
            db.execute('PRAGMA user_version=6')

    @contextmanager
    def read(self):
        db = sqlite3.connect(self.path, timeout=15, isolation_level=None)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA foreign_keys=ON')
        try:
            yield db
        finally:
            db.close()

    @contextmanager
    def write(self):
        # ponytail: SQLite serializes writes; migrate to PostgreSQL when multiple workers need throughput.
        with self.read() as db:
            db.execute('BEGIN IMMEDIATE')
            try:
                yield db
                db.commit()
            except BaseException:
                db.rollback()
                raise
