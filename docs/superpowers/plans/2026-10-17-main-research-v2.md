# Main Research V2 Implementation Plan — Stage 1

> **For agentic workers:** Use executing-plans to implement this plan task-by-task in the current session; request an independent review before completion.

**Goal:** 建立可发布/撤回的资料、结构化草稿、版本化评价、确认门槛和问题相关解释。

**Architecture:** 复用 SQLite、FastAPI、持久 Worker 和现有版本。新增 knowledge/review 两个业务模块；资料可用性由服务器计算，评价在生成/编辑/确认边界运行。旧内容保留，数据库通过 v1→v2 增量升级。

**Tech Stack:** Python / Pydantic / SQLite / unittest / 原生网页；本阶段不增加外部依赖。

**Spec:** `docs/superpowers/specs/2026-10-17-main-research-v2-design.md`，用户已确认六阶段路线及本阶段设计。

## Global Constraints

- 专业与流程资料、演示与真实依据分开，演示资料不自动升级成真实农艺依据。
- 无依据、越权/撤回/失效依据、未解决专业判断不能通过确认。
- 五维结果为符合/部分符合/不符合/无法评价，评价固定候选内容、事实与资料版本。
- 询问不改方案，金额只读确定性计算，正式 P 不原地修改。
- 无 Git 仓库，代码在现有 agriplan 子目录中原地增量更新；迁移保留既有数据。

### Task 1: 资料及数据库迁移

Files: `app/storage.py`, `app/knowledge.py`, `app/domain.py`, `app/main.py`, `app/seed.py`, `tests/test_research_v2.py`。

Interfaces:
- `search_documents(db, query, crop, region, customer_id) -> list[dict]`：仅返回当前可用、范围匹配的资料版本及定位。
- `evidence_status(db, evidence_id, crop, region, customer_id) -> (dict|None, str|None)`：检查状态、范围、时点。
- `create_document(store, actor, request)`, `publish_document(...)`, `revoke_document(...)`：带 revision、对象权限及审计。

- [x] 写发布前不可检索、发布后可检索、撤回及过期/客户范围过滤测试；运行观察失败。
- [x] 在 v2 增加资料版本元数据、评价表；迁移历史 knowledge 内容并保留 ID/来源，不重置业务表。
- [x] 实现资料文本/Markdown API、发布/撤回、列表和版本；发布不能继承其他客户范围。
- [x] 运行新增测试，验证旧库重复打开仍保留 R/C/P、现场记录和迁移结果。

验收例：
```python
document = create_document(store, sr, payload)
assert search_documents(db, '历史参考', '玉米', '吉林', 'c1') == []
publish_document(store, sr, document['id'], expected_revision=1)
assert search_documents(db, '历史参考', '玉米', '吉林', 'c1')[0]['id'] == document['id']
```

### Task 2: 结构化结果与五维评价

Files: `app/review.py`, `app/research.py`, `app/services.py`, `tests/test_research_v2.py`。

Interfaces:
- `inspect_candidate(db, run, draft) -> dict`：纯评价，返回 dimensions/issues/can_confirm 与 content_hash、input_revision、evidence fingerprint。
- `save_review(db, run, draft, candidate_version) -> dict`：保存固定版本评价。
- `build_draft(snapshot, state)`：在旧字段外增加 measures、history_decisions、constraints、pending_decisions。

- [x] 先写零依据不能确认、流程资料不能证明专业措施、资料撤回阻塞、C2不得复用C1评价测试。
- [x] 结构化生成本季目标/确认输入/历史差异及采用条件，不按历史自动设定专业参数。
- [x] 核验引用存在及适用性、关键参数来源、计算与预算、待决专业问题；五维与问题关闭条件可读。
- [x] 生成/编辑时保存评价；存在阻塞项保存受限草稿，确认时重新核验。
- [x] 核对旧 P 保持不变，旧 C 必须通过现行评价；成功流程继续有 R/C/P。

验收例：
```python
review = inspect_candidate(db, run, draft_without_evidence)
assert review['can_confirm'] is False
assert any(issue['code'] == 'EVIDENCE_REQUIRED' for issue in review['issues'])
```

### Task 3: 问题相关解释与管理页面

Files: `app/services.py`, `app/main.py`, `web/app.js`, `web/style.css`, `tests/test_research_v2.py`。

- [x] 写金额/历史/合同问题返回不同类别与相关引用、不改 C/P 的失败测试。
- [x] 实现明确规则类别的快照解释；未覆盖问题限制回答，不能编造合同和专业结论。
- [x] 任务详情展示评价、结构化措施、历史关系及阻塞项；受限草稿也能编辑和重评。
- [x] 知识入口管理区支持创建资料、发布、撤回、范围、演示身份；所有正文转义。
- [x] 草稿编辑校验与页面使用同一对象/revision，继续执行前端并发回归。

验收例：
```python
money = discuss(store, run_id, actor, Discuss(message='预算怎么算？'))
history = discuss(store, run_id, actor, Discuss(message='历史方案有哪些变化？'))
assert money['category'] != history['category']
assert money['answer'] != history['answer']
```

### Task 4: 回归、独立审查和交付记录

Files: `tests/test_mvp.py`, `tests/test_ui.cjs`, `README.md`, `docs/实施进度.md`, `docs/阶段1验收记录.md`。

- [x] 运行 `python -X utf8 -m unittest discover -s tests -v`，检查旧用例与新增约束。
- [x] 运行 `node tests/test_ui.cjs`、`node --check web/app.js` 与 `python -m compileall -q app run.py`。
- [x] 独立检查资料越权、撤回后的确认、旧评价和数据库迁移；修复重要问题并留下回归。
- [x] 重启本机 API/Worker，实际验证桌面/手机资料发布→研究→评价→编辑→确认及撤回拦截。
- [x] 更新实际完成和未验证边界，不把本阶段标为完整专业 Agent 已完成。

## 下一阶段衔接

阶段 2 使用本阶段的资料/候选/评价契约接入真实模型生成、语义问答、有限子任务、通用问题与局部重算。不得绕过现有质量门槛。完成本阶段后继续准备阶段 2 的具体实现任务；外部专业材料和真实模型调用状态独立记录。

