# 阶段2F：隔离评测工具、调用证据与人工复核

用户已确认先完成本地评测工具，真实模型稍后配置。本机无.env及模型地址/名称/密钥。本轮不进行外部模型调用，不填造农艺评分，也不把2F或第二阶段整体标为已验收。

## 选择与边界

采用命令行隔离评测与静态HTML/JSON报告：复用真实Store、业务用例和Worker，固定合成案例，最少改动现有工作台。直接用生产库评测会混入真实对象；仅输出unittest通过也无法供人员查看案例、输入、引用和质量。因此每例单独临时库，不接受生产库路径，不读取当前客户数据；不自动确认P。

固定8例：完整单产品、专业用量缺失、零依据、预算超限、两产品、历史差异、面积局部恢复、提示注入备注。缺口预期为waiting/limited属于案例检查通过，不能直接把completed比例当质量分。两产品独立期望25350元；面积301亩期望19264元；专业用量缺失须有open问题且不生成C。注入内容只能作为事实备注，不改变核算/权限。

本地规则运行标demo/rule_generated。未来live必须显式--allow-network、--max-calls和--max-model-seconds，配置完整才允许，当前不执行。live是实际HTTP调用身份，不代表专业有效或生产适用；仍仅发送固定合成数据。调用预算在请求边界核对；剩余时间用作HTTP超时，不能宣称进程级强制停止。供应商缺少token用量时保留null，不填零；费用未配置单价时null，不编造金额。

## 调用记录

model_metrics.py用ContextVar包住现有请求，不增加依赖或业务库表；普通业务无记录器时保持原行为。只记录operation(planner/candidate/discussion)、耗时、HTTP状态、成功/失败/预算阻止类别及供应商报告的input/output tokens。不保存密钥、地址查询参数、请求正文、供应商错误正文。上下文退出必须恢复，不能跨请求/评测串记录。模型协议后续校验和业务评价由案例结果另记，不能把HTTP成功等同专业通过。

## 评测与报告契约

evaluation.py执行案例，返回核心报告：schema_version=1、suite_id、suite_hash、evaluation_id、created_at、mode、provenance、cases、telemetry、external_validation='not_performed'、professional_validation='not_performed'。case包含id/title、run_id、status、snapshot、draft、review、actions（仅type/generation/purpose）、questions（field/status）、checks[{name,passed,expected,actual}]、elapsed_ms、error_category。失败不吞掉或当通过；案例互相隔离。

eval_report.py的finalize_report(core)给每例case_hash、整份report_hash及汇总；save_report(report, output_dir)新建唯一目录并写report.json/report.html/review-template.json，不覆盖旧报告。只输出允许字段并在落盘前移除当前配置密钥的精确值；HTML所有模型文本转义，无外部资源/脚本执行。报告含数据身份、自动检查、研究版本/核算/引用、动作与限制。

人工模板绑定evaluation_id/report_hash及每例case_hash。五维condition_fit/evidence_support/numeric_consistency/execution_readiness/risk_preparedness，四档符合/部分符合/不符合/不可评价。初始reviewer、reviewed_at、各评分为空。validate_reviews(report, payload)拒绝未知/重复案例、错误哈希、不完整填写、未来/无时区时间、无说明的判断；允许未填项保持pending，不自动推测。填写记录标manual_recorded_unverified_identity，仅表示人工填报，不能证明身份或专业资质，也不自动改变专业验证状态。

CLI --review REPORT_JSON --review-file FILE复核绑定并输出新的reviewed报告，不覆盖原件；--mode demo默认直接运行。--preflight只检查配置，显示字段是否齐备，不输出密钥；live参数不足只返回安全错误，不发请求。报告、案例、人工复核和真实专业样本各有身份，真实模型/真实专业样本仍需后续明确验收。

## 验收

先写记录器预算/用量缺失/失败脱敏测试、案例结果与隔离测试、HTML转义和评审绑定测试，再实现。运行8例规则评测并打开HTML检查，故意修改人工模板哈希和评分验证拒绝。完整Python/既有Node回归、独立审查、原库/P/原件哈希核对。README与进度只将2F本地工具标交付，真实调用和专业验收保持依赖外部资料。
