# 阶段2D：报告原件、提取候选与SR确认

用户已确认六阶段路线，并在2C验收后要求继续。本阶段新增报告输入子流程，复用客户/地块/本季、事实R及权限。采用本机文件存储+SQLite元数据，不把原件塞进研究状态，不新增Agent。

## 范围与选择

现有事实只有soil_ph检测字段，首版只将土壤pH候选映射到事实；其他检测值保留原文，不自动推导用量。支持UTF-8 txt/md、文本层PDF、PNG/JPEG原件，单件最多8MiB。PDF使用pypdf；无需增加多文件表单依赖，上传使用带文件名查询参数的有界原始请求体。

自动OCR引擎当前不存在。图片/扫描PDF保留原件并明确显示人工转录，不将其宣称为自动OCR；已有PDF文本层同样仅是候选。云端提取和真实报告效果评测留到具备接口/样本时。本地提取失败不会删除原件。

## 存储和原件边界

v5增量新增reports、report_extractions、fact_report_sources三表。原件按随机report ID存放于数据库同级source-files，不使用用户文件名构造路径；保存SHA-256、大小、规范MIME、原文件名、上传者、当前提取ID和修订。下载/提取/确认均核对原件哈希并检查当前职责。只有职责内SR可上传/提取/确认，FR可读。原件不可覆盖，无删除入口。

POST /seasons/{id}/reports?filename=...，Content-Type与扩展名/签名一致（PDF/PNG/JPEG/text）。上传限额同时检查Content-Length及实际流长度，JSON入口仍64KiB。原件先持久保存，写库前重查权限；失败清理本次新文件，不触及已有数据。响应201报告元数据，尚未提取/确认。

## 候选与确认

POST /reports/{id}/extract body expected_revision，显式触发有界本地提取。PDF在独立进程解析，最多30页、总文本100000字、超时15秒，失败或扫描件转manual_required；正文按页保存。txt/md以一页保留行定位。自动模式仅匹配明确标记的地块、采样日期和单个pH，多个值/冲突不猜测。

每次提取保存不可改写的report_extractions：id/report_id/sequence/method/pages/candidate/issues/actor/time。candidate包含field=soil_ph、raw_value、value、original_unit、normalized_unit=1、plot_name、sampled_on、page、locator、excerpt、identity和复核信号。候选不是事实，也不是专业适用性证明。

POST /reports/{id}/candidates 手工转录，body expected_revision、raw_value、original_unit、plot_name、sampled_on（字符串可空）、page、locator、excerpt。保留原自动结果及新手工身份；修改错误候选不覆盖原件。pH只接受有限0..14数值和pH/1/无量纲单位，未知/不符保留阻塞项；地块与当前本季精确一致、日期有效且不在未来、来源定位完整才可确认。采样年份与本季不同提示SR核实适用性，不自行制定农艺有效期。

POST /reports/{id}/confirm body expected_revision、expected_input_revision、extraction_id、source。只接受当前候选，SR明确核对后写soil_ph和新R，同事务写fact_report_sources及审计/报告确认状态。重复相同确认不再新增R；冲突、过期、跨对象、缺原件、单位/地块/日期错误拒绝。确认后不再改该报告；修订须新上传。迟到报告只更新当前R，使旧研究过期，不倒写旧C/P。

所有一般事实更新及补问在soil_ph数值未变时继承原报告来源；值变化则清除其关联。研究快照保存本R的report_refs（原件ID/哈希、提取ID、测量值、日期、单位和定位）。质量评价重查引用及原件完整性；报告本身不自动成为支持农艺处方的审核资料。模型讨论可读取本研究已确认报告来源，不读取未确认候选作为事实。

## 页面与验收

本季页报告区显示原件/哈希、状态、提取历史、按页文本、候选和阻塞项；SR可上传、提取、手工修正、明确确认，FR只有受控原件和候选读取。表单绑定报告/本季/候选修订，迟到响应不污染其他对象。研究资料页显示固定报告来源及原件入口。

验收覆盖原始字节及哈希不变、候选不写R、确认仅改pH与来源/一次R、单位/地块/日期/多值阻塞、PDF有文本/扫描件/超限回退、人工身份、越权/篡改/过期拒绝、来源继承清除、迟到报告不改P、v4重复迁移、完整既有回归和桌面/手机实际流程。2E局部依赖图、2F专业评测和阶段3—6仍待实现。
