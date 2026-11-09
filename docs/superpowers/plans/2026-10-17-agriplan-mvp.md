# AgriPlan MVP Implementation Plan

> **For agentic workers:** Use executing-plans to implement this plan task-by-task in the current session.

**Goal:** 交付可启动并可验收的本地 AgriPlan MVP。

**Architecture:** FastAPI 负责身份与对象边界，SQLite 保存业务版本及持久作业，Worker 推进受控研究状态机。原生网页通过同源 API 实现五入口。

**Tech Stack:** Python 3.11+ / FastAPI / Pydantic / SQLite / unittest / HTML/CSS/JavaScript。

**Spec:** `docs/superpowers/specs/2026-10-17-agriplan-mvp-design.md`

## Global Constraints

- 所有演示资料及规则模式明确标注，不宣称实际农艺效果。
- Decimal 核算；客户、地块、季节、研究权限由服务端校验。
- 技术确认、商务批准、执行放行分别表示。
- 本目录不是 Git 仓库；不初始化或提交用户原文档。代码隔离在 agriplan 子目录。

### Task 1: 数据、工具与权限

Files: `app/storage.py`, `app/domain.py`, `app/seed.py`, `tests/test_mvp.py`, `requirements.txt`。

Interfaces: `Store(path).read()/write()`, `calculate(area, rate, price) -> dict`, `create_app(db_path) -> FastAPI`。

- [x] 写 unittest 验证精确金额、非法数值、登录与对象越权，先运行观察失败。
- [x] 建立 SQLite schema、演示数据、Decimal 工具、会话及对象权限。
- [x] 运行 `python -m unittest discover -s tests -v`，确认新增行为通过。

### Task 2: 研究、等待、修订与现场闭环

Files: `app/research.py`, `app/services.py`, `app/main.py`, `app/worker.py`, `app/model.py`, `tests/test_mvp.py`。

Interfaces: `work_once(store) -> bool`, `POST /api/research-runs`, `POST /api/runs/{id}/answers`, `PUT /api/runs/{id}/draft`, `POST /api/runs/{id}/confirm`。

- [x] 补充端到端测试：创建任务→等待→回答→恢复→修改→确认→现场记录，先观察失败。
- [x] 实现输入快照、历史/证据/核算动作、幂等、持久问题与作业、租约 fencing。
- [x] 接入可选 Chat Completions 模型规划器，校验动作白名单，外部错误保留为失败。
- [x] 测试资料变更阻止旧确认、询问不改方案、FR 角色限制、重启恢复及市场报价边界。

### Task 3: 五入口与交付

Files: `web/index.html`, `web/app.js`, `web/style.css`, `run.py`, `start.ps1`, `start.cmd`, `.env.example`, `README.md`。

Interfaces: 同源 JSON API，`python run.py` 默认启动本机 127.0.0.1:8000。

- [x] 实现中文响应式五入口和任务五页签；表单包含标签、等待及失败状态。
- [x] 所有关键提交携带 revision，服务端 409 后提示刷新，不悄悄覆盖。
- [x] 运行 API 验收测试、JavaScript 语法检查及浏览器桌面/手机业务演示。
- [x] 完成启动、模型配置、演示脚本及已知边界文档，记录实际验证结果。


完成验证：14 项 Python 验收、4 项 Node 前端行为、JavaScript 语法、Python 编译与依赖检查通过。桌面与 390px 手机端完成实际操作；详见 ../../验收记录.md。
