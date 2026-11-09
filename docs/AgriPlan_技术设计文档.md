# AgriPlan 农业技术服务交付平台 — 技术设计文档

| 项 | 内容 |
| --- | --- |
| 文档版本 | v1.0 |
| 更新日期 | 2026-11-09 |
| 作者 | 王喆 |
| 实现状态 | 已实现：FastAPI + SQLite + 原生 JS + 显式状态机（MVP） |
| 目标蓝图 | LangGraph + PostgreSQL + pgvector + 双 Agent（规划，未实现） |
| 关联文档 | 《AgriPlan 产品需求文档（PRD）》《AgriPlan README》 |

> 全文用「**现状（已实现）**」和「**目标（规划）**」两栏区分，避免把规划中的技术栈（LangGraph/pgvector/PostgreSQL）误述为已实现。

---

## 1. 概述

AgriPlan 是面向农业技术服务公司的交付平台。本设计文档说明其总体架构、技术选型、数据模型、接口与关键机制，并如实区分「已实现的 MVP」与「目标蓝图」。

### 1.1 现状（已实现）

一个本地可运行的 MVP：FastAPI 后端 + SQLite 数据库 + 原生 JavaScript 前端，覆盖「客户/地块/种植季 → 研究研制 → 草稿确认 → 物资核算 → 现场记录 → 跨季留存」完整业务链。采用**显式受控状态机**实现「Agent 式」研究流程，未接入真实 LLM、RAG 或 LangGraph。

### 1.2 目标（规划）

在现有业务闭环之上，逐步接入真实模型（结构化研制）、语义 RAG（pgvector）、LangGraph 执行图、独立市场 Agent 及外部业务系统，并完成生产化。

## 2. 总体架构

### 2.1 现状架构（已实现）

```text
           响应式网页（内部工作台 / 五入口 / 五页签）
                              │ 同源 JSON API
              FastAPI（身份、权限、请求、结果协议）
                              │
        业务服务：事实、版本、草稿确认、核算、现场记录
                              │
           SQLite（增量迁移）+ 持久作业 → 独立 Worker
                              │
     研究流程 = 显式状态机（受控动作选择 + 模板生成 + 关键词检索）
```

### 2.2 目标架构（规划）

```text
  内部 AR/SR/FR 工作台                客户行情入口
          └──────────────┬──────────────┘
                  FastAPI（身份、权限、专用结果）
                         │
        业务服务：事实、版本、审批、任务、确认与交付条件
                         │
        PostgreSQL 持久任务 / 恢复作业 → Worker
                         │
              普通程序按任务类型分流
            ┌────────────┼────────────┐
        SR 方案研制   客户个性化研究   一般行情问答
        主方案 Agent   市场 Agent   检索 + 模型解释
            │ 委派市场 Agent
       动作校验 → 受控工具 → 结构化反馈 → 重规划/停止
                         │
   事实查询、农艺 RAG、专业计算、行情资料与报价
                         │
  PostgreSQL / pgvector / 持久 Checkpoint / 文件存储
                         │
  授权外部集成：CRM / 产品与 ERP / 审批与财务 / 获准行情来源
```

## 3. 技术选型

### 3.1 现状（已实现）

| 技术 | 用途 | 选择理由 |
| --- | --- | --- |
| Python 3.12 | 后端、业务、计算、Worker | 统一语言，便于联调 |
| FastAPI | API、鉴权、参数与结果协议 | 类型化接口，支持异步上传流 |
| SQLite | 数据存储 | 本机单 Worker 演示足够，事务 + 增量迁移 |
| 原生 JavaScript | 前端（app.js / style.css） | 无构建依赖，可离线本机运行 |
| Pydantic | 请求/响应/事实的结构校验 | 减少协议歧义 |
| pytest/unittest + Node 行为检查 | 测试 | 124 项 Python + 39 项前端场景 |

### 3.2 目标（规划，未实现）

| 技术 | 用途 | 引入原因 | 工程成本 |
| --- | --- | --- | --- |
| LangGraph | Agent 状态机、节点、条件路由、中断、Checkpoint | 有状态反馈循环 | 需自实现业务规则、幂等、失效治理 |
| PostgreSQL | 事实、版本、事务、任务、约束、审计 | 关系与可靠写入统一 | 迁移、索引、锁、备份 |
| pgvector | 获准资料的语义检索 | 复用数据库底座 | Embedding、过滤、召回、版本维护 |
| 外部模型 API / Embedding | 提取、规划、语义判断、解释 | 无需自管推理集群 | 限流、成本、数据发送、供应商管理 |

> 现状未使用 LangGraph / pgvector / PostgreSQL，采用 SQLite + 显式状态机作为替代实现。**缺少框架名不等于功能缺陷，但不能宣称已实现对应技术方案**。

## 4. 工程结构

### 4.1 现状目录（已实现）

```text
agriplan/
  app/
    main.py           API 入口（30+ 端点、中间件、同源/大小限制）
    domain.py         事实/计算/状态规则、Decimal 核算
    services.py       业务用例与事务、版本/确认/记录
    research.py       研究流程、受控动作、历史参考
    storage.py        数据库访问（read/write 事务上下文）
    knowledge.py      资料版本、关键词检索
    reports.py        原件上传/提取/候选确认
    worker.py         独立作业进程
    seed.py           演示种子数据
  data/               agriplan.db + 原件 source-files/
  web/                index.html / app.js / style.css
  tests/              test_*.py + test_ui.cjs
  docs/               分阶段实施方案、验收记录、覆盖清单
  run.py / start.ps1 / start.cmd / requirements.lock.txt / .env.example
```

### 4.2 目标目录（规划）

在原结构上新增 `agents/`（图、状态、规划、市场研究）、`tools/`（查询、检索、计算、契约）、`retrieval/`（入库、切分、索引、证据包）、`integrations/`（CRM、ERP、模型、行情适配）、`migrations/`、`evaluations/`、`deploy/`。

## 5. 数据模型

### 5.1 现状（已实现）

核心对象：**客户（customers）→ 地块（plots）→ 种植季（plot_seasons）**；研究为**研究作业（research_runs）→ 动作（actions）→ 草稿（drafts）→ 正式方案（plans）**；辅以事实、补问、现场记录、资料版本、报告原件、会话等表。关键设计：

- **版本化**：事实 R、候选 C、正式技术方案 P 分离，`revision` 乐观并发 + 审计。
- **引用指纹**：研究输入快照、计算来源、依据引用分别记录。
- **金额计算**：`Decimal` 逐产品计算，保存目录快照、公式与取整规则。

### 5.2 目标（规划，摘自目标数据手册）

| 表 | 核心字段 | 用途 |
| --- | --- | --- |
| customers / customer_links | customer_id, crm_customer_id, sync_status | 本地客户与外部对象关联 |
| plots / plot_seasons | location, area_reference / season, crop | 地块与当季事实 |
| facts | field, value, unit, basis, source_id, status, version | 候选/确认/估计身份 |
| research_runs | run_type, thread_id, scope, status, revision, workflow_version | 顶层研究生命周期 |
| actions | type, purpose, input_refs, status | 模型提议与执行记录 |
| evidence | source_version, locator, claim, applicability | 来源与主张支持 |
| calculations | input_refs, rule_version, result, unit, rounding | 数值血缘 |
| candidates / review_issues | content, evidence_refs / category, severity, close_condition | 候选与有限修复 |
| questions / answers | owner_id, answer_policy / value | 人工待确认项 |
| jobs | kind, generation, token, lease_until, attempts | 持久执行与恢复 |

## 6. API 设计（现状，已实现）

同源 JSON API，写操作需登录会话，按角色/对象/revision 校验。约 35 个端点，分组如下：

| 分组 | 端点（节选） | 说明 |
| --- | --- | --- |
| 健康/会话 | `GET /api/health`、`POST /api/login`、`POST /api/logout` | 登录返回 HttpOnly 会话 Cookie |
| 客户/种植季 | `POST /api/customers`、`GET/PUT /api/seasons/{id}`、`POST /api/seasons/{id}/next` | 建档、编辑、新建下一季 |
| 研究作业 | `POST /api/research-runs`（202 + 幂等键）、`GET /api/runs/{id}`、`POST .../answers`、`.../refresh`、`.../cancel` | 持久作业 + 补问 + 恢复 |
| 草稿/确认 | `PUT .../draft`、`POST .../confirm`、`.../discuss`、`.../proposals/{id}/apply` | 编辑、技术确认、对话 |
| 报告 | `POST /api/seasons/{id}/reports`、`GET .../original`、`.../extract`、`.../candidates`、`.../confirm` | 原件上传与提取确认 |
| 现场/知识/资料 | `POST/GET /api/field-records`、`POST /api/knowledge`、`GET/POST /api/documents`、`.../publish`、`.../revoke` | 记录、检索、资料版本 |
| 评价/市场 | `GET/POST /api/runs/{id}/review`、`GET /api/seasons/{id}/market` | 候选评价、市场演示 |

**关键机制**：`POST /api/research-runs` 返回 `202`，携带 `Idempotency-Key` 头防止重试重复建单；`jobs` 由独立 Worker 领取并续租，租约过期可被重领。

## 7. 关键流程（现状，已实现）

### 7.1 研究作业生命周期

```text
SR 发起研制 → 创建 research_runs（202，幂等键）
  → 持久 job → Worker 领取（租约令牌）
  → 受控状态机：查历史 → 查资料 → [缺用量 → 补问 waiting_input]
  → 核算（面积×用量×单价） → 生成草稿 → 五维评价
  → SR 编辑/对话 → 技术确认 → 正式方案 P（不可原地覆盖）
  → FR 只读 + 现场记录 → 下一季转历史参考
```

### 7.2 版本与并发

- 每个对象带 `revision`，写操作提交 `expected_revision`，不匹配则 409。
- 正式版确认后不可原地覆盖，只能新建研究产生新版本。
- 审计记录每次关键变更的发起人、时间、理由。

## 8. 安全设计（现状，已实现）

| 措施 | 实现 |
| --- | --- |
| 身份认证 | scrypt 密码哈希；会话 token 以 SHA-256 摘要存储，8 小时过期 |
| 会话安全 | HttpOnly + SameSite=strict Cookie |
| 授权 | SR/FR 角色 + 客户责任范围校验；FR 不能技术确认 |
| 跨站防护 | 写请求同源校验；TrustedHost 白名单；CSP；`X-Content-Type-Options: nosniff` |
| 输出安全 | HTML 转义、JSON 序列化 |
| 资源限制 | 请求体 64KB、报告原件 8MiB 上限 |

## 9. 测试策略（现状，已实现）

- **单元/集成**：124 项 Python 测试（`unittest`），覆盖 Decimal 核算、权限隔离、幂等、恢复、取消、过期租约、迁移等。
- **前端行为**：39 个前端场景（Node `test_ui.cjs`），覆盖迟到响应、幂等重试、旧弹窗误写、导航关闭弹窗等。
- **迁移**：v1–v6 增量迁移，破坏性迁移前备份，失败事务回滚。
- **验收**：每阶段独立验收记录，区分「已实现/部分实现/演示替代/未实现/未验证」。

## 10. 部署与运行（现状，已实现）

```powershell
start.ps1          # 自动创建 .venv 并安装锁定依赖，启动 API + Worker
# 或手动：
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.lock.txt
.\.venv\Scripts\python.exe -X utf8 run.py
```

- 监听本机 `127.0.0.1:8000`；API 文档在 `/docs`（需先登录）。
- 数据存 `data/agriplan.db`，重启不重置；原件存 `data/source-files/`。
- 真实模型通过 `.env` 配置（`AGRIPLAN_MODE=llm` + OpenAI 兼容 Base URL + key），未配置时使用确定性规则模式。

## 11. 已实现 vs 规划（差距总表）

| 能力 | 现状（MVP） | 目标（蓝图） |
| --- | --- | --- |
| 研究引擎 | 显式状态机 + 模板生成 | LangGraph 图 + 真实模型规划 |
| 知识检索 | 4 篇资料关键词检索 | pgvector 语义 RAG + 证据检查 |
| 市场能力 | 同步演示函数（合成报价） | 独立市场 Agent + 真实行情 |
| 数据存储 | SQLite | PostgreSQL |
| 模型接入 | 协议已设计 + Mock 验证 | 真实 key + 质量评测 |
| 外部系统 | 未接入 | CRM/ERP/OA/财务授权集成 |
| 生产化 | 本机运行 | TLS/限流/监控/备份/负载测试 |

## 12. 技术取舍与权衡

| 决策 | 选择 | 理由 | 代价 |
| --- | --- | --- | --- |
| 存储 | 先 SQLite | 本机单 Worker 够用、迁移简单、演示可复现 | 并发/生产能力有限 |
| 研究引擎 | 先显式状态机 | 先把业务规则跑正确、可测 | 不具备真实 LLM 规划能力 |
| 检索 | 先关键词检索 | 无 Embedding 成本、结果可解释 | 无语义召回 |
| 模型 | 接口后置 + Mock | 无 key 也能验证协议与约束 | 语义质量未验证 |

> 核心原则：**先证明业务正确与工程可靠，再引入框架与模型**；不因「用了某个库」而宣称能力达成。

## 13. 后续规划（按六阶段）

1. 补齐主研制价值：真实模型结构化研制 + 真实专业资料 + 主张支持性/硬约束/候选评价门槛。
2. 补对话、补问、局部重研：按问题解释、按字段影响重算、恢复重核权限。
3. 市场能力独立成链：真实行情、售粮策略、客户登录/资格、共享周报。
4. 正式服务与季末闭环：AR 交接、报价/合同、交付任务、异常责任、季末归档。
5. 生产底座：数据库/文件迁移、监控费用、限流、故障/负载/升级测试、备份回退。

## 14. 关键代码位置（现状）

| 文件 | 职责 |
| --- | --- |
| `app/main.py` | API 路由、中间件、同源/大小限制 |
| `app/services.py` | 业务用例、版本、确认、记录 |
| `app/research.py` | 研究流程、受控动作、历史参考 |
| `app/domain.py` | 事实、计算、状态规则、Decimal 核算 |
| `app/storage.py` | 数据库事务访问 |
| `app/reports.py` | 原件上传、提取、候选确认 |
| `tests/` | Python 与前端行为测试 |
