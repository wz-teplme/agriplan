"""Versioned source documents and scoped lexical evidence retrieval."""
import hashlib
import json
import re
from datetime import datetime, timezone

from fastapi import HTTPException

from .domain import dumps, timestamp, uid


_COLUMNS = {
    'document_id': 'TEXT', 'revision': 'INTEGER NOT NULL DEFAULT 1',
    'version_number': 'INTEGER NOT NULL DEFAULT 1', 'status': "TEXT NOT NULL DEFAULT 'draft'",
    'kind': "TEXT NOT NULL DEFAULT 'procedure'", 'purpose': "TEXT NOT NULL DEFAULT 'plan_research'",
    'region': "TEXT NOT NULL DEFAULT '通用'", 'customer_id': 'TEXT',
    'valid_from': 'TEXT', 'valid_until': 'TEXT', 'demo': 'INTEGER NOT NULL DEFAULT 1',
    'created_by': 'TEXT', 'published_by': 'TEXT', 'revoked_by': 'TEXT',
    'created_at': 'TEXT', 'published_at': 'TEXT', 'revoked_at': 'TEXT',
    'content_hash': 'TEXT', 'supported_claims': "TEXT NOT NULL DEFAULT '[]'",
    'conditions': "TEXT NOT NULL DEFAULT '[]'", 'publication_note': 'TEXT',
}


def ensure_schema(db):
    """Extend legacy rows inside the caller's transaction; never republish migrated rows."""
    columns = {r['name'] for r in db.execute('PRAGMA table_info(knowledge)')}
    for name, definition in _COLUMNS.items():
        if name not in columns:
            db.execute(f'ALTER TABLE knowledge ADD COLUMN {name} {definition}')
    for row in db.execute('SELECT * FROM knowledge WHERE document_id IS NULL').fetchall():
        seeded = row['id'] in {'k1', 'k2', 'k3', 'k4'}
        now = timestamp()
        db.execute('''UPDATE knowledge SET document_id=id,content_hash=?,created_at=COALESCE(created_at,?),
                      created_by=COALESCE(created_by,?),status=?,kind='procedure',demo=1,
                      published_by=?,published_at=?,supported_claims=? WHERE id=?''',
                   (_hash(row['body']), now, 'u1' if seeded else None,
                    'published' if seeded and row['status'] == 'draft' else row['status'],
                    'u1' if seeded else None, now if seeded else None,
                    dumps([row['body']]) if seeded else '[]', row['id']))
    db.execute('CREATE INDEX IF NOT EXISTS knowledge_scope ON knowledge(status,customer_id,crop,region)')
    db.execute('CREATE UNIQUE INDEX IF NOT EXISTS knowledge_versions ON knowledge(document_id,version_number)')


def _hash(body):
    return hashlib.sha256(body.encode('utf-8')).hexdigest()


def _view(row):
    result = dict(row)
    for field in ('supported_claims', 'conditions'):
        result[field] = json.loads(result.get(field) or '[]')
    result['demo'] = bool(result['demo'])
    return result


def _date(value):
    if value is None:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
        return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed
    except (ValueError, TypeError, AttributeError):
        raise HTTPException(422, '有效时间格式错误，请使用 ISO 日期或时间') from None


def _applicable(document, crop, region):
    return all(wanted == '通用' or document[field] in ('通用', wanted)
               for field, wanted in (('crop', crop), ('region', region)))


def evidence_status(db, evidence_id, crop, region, customer_id):
    row = db.execute('SELECT * FROM knowledge WHERE id=?', (evidence_id,)).fetchone()
    if row is None:
        return None, 'EVIDENCE_NOT_FOUND'
    if row['customer_id'] and row['customer_id'] != customer_id:
        return None, 'EVIDENCE_SCOPE'
    doc = _view(row)
    if doc['status'] == 'revoked':
        return doc, 'EVIDENCE_REVOKED'
    if doc['status'] != 'published':
        return doc, 'EVIDENCE_NOT_PUBLISHED'
    now = datetime.now(timezone.utc)
    try:
        start, end = _date(doc['valid_from']), _date(doc['valid_until'])
    except HTTPException:
        return doc, 'EVIDENCE_EXPIRED'
    if (start and start > now) or (end and end <= now):
        return doc, 'EVIDENCE_EXPIRED'
    if not _applicable(doc, crop, region):
        return doc, 'EVIDENCE_NOT_APPLICABLE'
    return doc, None


def search_documents(db, query, crop='通用', region='通用', customer_id=None):
    # ponytail: lexical matching suits this small corpus; add semantic retrieval when recall warrants it.
    tokens = set()
    for word in re.findall(r'[a-z0-9]+|[\u4e00-\u9fff]+', query.casefold()):
        if len(word) > 1 and '\u4e00' <= word[0] <= '\u9fff':
            tokens.update(word[i:i+2] for i in range(len(word)-1))
        else:
            tokens.add(word)
    if not tokens:
        return []
    rows = db.execute('''SELECT * FROM knowledge WHERE status='published'
                         AND (customer_id IS NULL OR customer_id=?)
                         AND (?='通用' OR crop IN (?, '通用'))
                         AND (?='通用' OR region IN (?, '通用'))
                         AND (valid_from IS NULL OR julianday(valid_from)<=julianday('now'))
                         AND (valid_until IS NULL OR julianday(valid_until)>julianday('now'))''',
                      (customer_id, crop, crop, region, region))
    ranked = []
    for row in rows:
        doc = _view(row)
        text = (doc['title'] + ' ' + doc['body']).casefold()
        score = sum(token in text for token in tokens)
        if score:
            ranked.append((score, doc))
    return [doc for _, doc in sorted(ranked, key=lambda item: -item[0])]


def _require_sr(actor):
    if actor['role'] != 'SR':
        raise HTTPException(403, '需要 SR 技术经理权限')


def _scope(db, actor, customer_id):
    if customer_id is not None and (not isinstance(customer_id, str) or not customer_id.strip()):
        raise HTTPException(422, '客户范围必须是有效客户标识或共享范围')
    if customer_id is not None and not db.execute(
            'SELECT 1 FROM assignments WHERE user_id=? AND customer_id=?',
            (actor['id'], customer_id)).fetchone():
        raise HTTPException(404, '资料不存在或不在您的责任范围')


def _document_for(db, actor, document_id, manage=False):
    row = db.execute('SELECT * FROM knowledge WHERE id=?', (document_id,)).fetchone()
    if row is None:
        raise HTTPException(404, '资料不存在或无访问权限')
    _scope(db, actor, row['customer_id'])
    if actor['role'] != 'SR' and row['status'] != 'published':
        raise HTTPException(404, '资料不存在或无访问权限')
    if manage and row['customer_id'] is None and row['created_by'] != actor['id']:
        raise HTTPException(403, '只有创建资料的 SR 可以管理共享资料')
    return _view(row)


def list_documents(store, actor):
    with store.read() as db:
        rows = db.execute('''SELECT * FROM knowledge WHERE
                           (customer_id IS NULL OR customer_id IN
                             (SELECT customer_id FROM assignments WHERE user_id=?))
                           AND (?='SR' OR status='published') ORDER BY rowid DESC''',
                          (actor['id'], actor['role']))
        return [_view(row) for row in rows]


def get_document(store, actor, document_id):
    with store.read() as db:
        return _document_for(db, actor, document_id)


def _expect(document, expected_revision):
    if document['revision'] != expected_revision:
        raise HTTPException(409, '资料版本已变化，请刷新后再提交')


def _audit(db, actor, event, document_id, **details):
    db.execute('INSERT INTO audit VALUES(?,?,?,?,?,?,?)',
               (uid(), None, None, actor['id'], event,
                dumps({'document_id': document_id} | details), timestamp()))


def _validated(request):
    result = {}
    for field in ('title', 'body', 'source', 'locator', 'crop', 'region', 'kind', 'purpose'):
        value = request.get(field)
        if not isinstance(value, str) or not value.strip():
            raise HTTPException(422, '资料标题、正文、来源、定位、作物、地区、类型和用途不能为空')
        result[field] = value.strip()
    if result['kind'] not in {'procedure', 'professional'}:
        raise HTTPException(422, '资料类型必须是流程或专业资料')
    if result['purpose'] not in {'plan_research', 'knowledge', 'market'}:
        raise HTTPException(422, '资料用途无效')
    for field in ('supported_claims', 'conditions'):
        value = request.get(field, [])
        if not isinstance(value, list) or any(not isinstance(item, str) or not item.strip() for item in value):
            raise HTTPException(422, '支持主张和适用条件必须是非空文字列表')
        result[field] = dumps([item.strip() for item in value])
    for field in ('valid_from', 'valid_until'):
        value = request.get(field) or None
        parsed = _date(value)
        result[field] = parsed.isoformat() if parsed else None
    if result['valid_from'] and result['valid_until'] and _date(result['valid_from']) >= _date(result['valid_until']):
        raise HTTPException(422, '资料有效期结束时间必须晚于开始时间')
    customer_id = request.get('customer_id')
    if customer_id is not None and (not isinstance(customer_id, str) or not customer_id.strip()):
        raise HTTPException(422, '客户范围必须是有效客户标识或共享范围')
    if not isinstance(request.get('demo', True), bool):
        raise HTTPException(422, '演示标识必须是布尔值')
    return result | {'customer_id': customer_id, 'demo': int(request.get('demo', True))}


def create_document(store, actor, request_dict, previous_id=None):
    _require_sr(actor)
    with store.write() as db:
        _scope(db, actor, request_dict.get('customer_id'))
        previous = None
        if previous_id is not None:
            previous = _document_for(db, actor, previous_id, manage=True)
            _expect(previous, request_dict.get('expected_revision'))
        data = _validated(request_dict)
        if previous and data['customer_id'] != previous['customer_id']:
            raise HTTPException(422, '资料新版本不能改变客户范围，请另建资料')
        document_id = uid()
        number = 1
        if previous:
            number = db.execute('SELECT MAX(version_number)+1 FROM knowledge WHERE document_id=?',
                                (previous['document_id'],)).fetchone()[0]
        data |= {'id': document_id, 'document_id': previous['document_id'] if previous else document_id,
                 'revision': 1, 'version_number': number, 'version': f'v{number}', 'status': 'draft',
                 'created_by': actor['id'], 'created_at': timestamp(), 'content_hash': _hash(data['body'])}
        fields = ','.join(data)
        placeholders = ','.join('?' for _ in data)
        db.execute(f'INSERT INTO knowledge({fields}) VALUES({placeholders})', tuple(data.values()))
        _audit(db, actor, 'document_version_created' if previous else 'document_created', document_id,
               previous_id=previous_id, version_number=number)
        return _document_for(db, actor, document_id)


def publish_document(store, actor, document_id, expected_revision, note):
    _require_sr(actor)
    with store.write() as db:
        doc = _document_for(db, actor, document_id, manage=True)
        _expect(doc, expected_revision)
        if doc['status'] != 'draft':
            raise HTTPException(409, '只有草稿资料可以发布；已发布或撤回资料请建立新版本')
        if not isinstance(note, str) or not note.strip():
            raise HTTPException(422, '发布资料需要填写审核确认说明')
        _validated(doc)
        now = datetime.now(timezone.utc)
        start, end = _date(doc['valid_from']), _date(doc['valid_until'])
        if (end and end <= now) or (start and start > now):
            raise HTTPException(422, '资料当前不在有效期内，不能发布为当前依据')
        latest = db.execute('SELECT MAX(version_number) FROM knowledge WHERE document_id=? AND published_at IS NOT NULL',
                            (doc['document_id'],)).fetchone()[0]
        if latest is not None and latest >= doc['version_number']:
            raise HTTPException(409, '已有更新的已发布版本，请建立新版本')
        for old in db.execute('SELECT id FROM knowledge WHERE document_id=? AND status=\'published\'',
                              (doc['document_id'],)).fetchall():
            db.execute('UPDATE knowledge SET status=\'superseded\',revision=revision+1 WHERE id=?', (old['id'],))
            _audit(db, actor, 'document_superseded', old['id'], replacement_id=document_id)
        db.execute('''UPDATE knowledge SET status='published',revision=revision+1,published_by=?,
                      published_at=?,publication_note=? WHERE id=?''',
                   (actor['id'], timestamp(), note.strip(), document_id))
        _audit(db, actor, 'document_published', document_id, note=note.strip())
        return _document_for(db, actor, document_id)


def revoke_document(store, actor, document_id, expected_revision, note):
    _require_sr(actor)
    with store.write() as db:
        doc = _document_for(db, actor, document_id, manage=True)
        _expect(doc, expected_revision)
        if doc['status'] not in {'published', 'superseded'}:
            raise HTTPException(409, '只有已发布资料可以撤回')
        if not isinstance(note, str) or not note.strip():
            raise HTTPException(422, '撤回资料需要填写原因')
        db.execute('''UPDATE knowledge SET status='revoked',revision=revision+1,revoked_by=?,
                      revoked_at=?,publication_note=? WHERE id=?''',
                   (actor['id'], timestamp(), note.strip(), document_id))
        _audit(db, actor, 'document_revoked', document_id, note=note.strip())
        return _document_for(db, actor, document_id)
