# 阶段2C：绑定版本的语义讨论与明确修改

用户已确认六阶段路线，并在2B验收后要求继续2C。本阶段是现有讨论/草稿编辑流程的扩展，增加持久修改预览与模型解释协议；沿用本机Python、SQLite、原生页面，不增加依赖。

## 决策与范围

采用“询问”和“提出修改”两个明确入口。仅靠模型判断是否授权修改容易把疑问当确认；直接聊天覆盖草稿无法审阅差异。本版通过显式请求意图+服务器字段白名单+SR二次保存完成修改。

询问在demo模式沿用分类解释；llm模式使用已有JSON请求边界做语义解释。模型只能引用服务器提供的本对象参考ID，不能运行工具或写事实。讨论提供当前草稿、固定事实、核算、历史、实际引用的可访问资料及当前审查，并携带同研究最近六轮讨论及原R/C身份用于追问，不携带其他客户上下文。旧讨论不成为确认事实。自由文字仍须人工复核，协议验收不代表真实语义质量已验证。

## 请求与协议

Discuss增加intent(question/change_request，默认question)、expected_revision（可选，兼容旧查询客户端）、reason（SR明确理由，可空）、proposed_edit（可选，summary/stages完整文字）。修改必须提交expected_revision；问题页面也绑定该版本，过期返回409。未形成草稿仍可按旧规则询问。

默认demo只接受手工proposed_edit预览；纯自然语言修改返回明确的能力限制和入口提示，不猜测修改。llm配置下无手工edit时由模型输出严格DiscussionResult：intent(explain/edit/needs_facts)、answer、refs、edit(summary/stages或null)、pending_decisions。询问不能携带edit；修改依赖事实或资料不足则needs_facts/no edit，不能把谈话自动写成本季确认事实。模型额外字段、未知引用、无效输出返回502，保留原草稿，错误信息不暴露密钥。

## 修改预览与保存

discussion_proposals一张增量表保存message ID、run ID、actor、绑定{run_revision,input_revision,candidate_version,formal_id,content_hash}、前后文字、服务器计算的影响和预览评价、SR明确理由、pending/applied状态及applied_version。数据库升级v4，旧消息和C/P内容不改写。

只有职责内SR、当前未过期completed/limited草稿可以提出和应用修改；FR仅解释。候选字段仅summary/stages，保留原核算、物资、结构化措施、约束、引用与待决项。没有实际文字变化不创建proposal。理由只使用独立SR输入，留空记“未记录”，模型不得猜测。

生成解释/建议时先读取授权上下文、关闭连接，在事务外调用模型，提交前重新检查职责、R/C/run revision/内容hash及当前引用状态。模型期间对象变化或引用失效返回409，无消息/建议部分落库。

POST /runs/{run}/proposals/{message_id}/apply，body expected_revision。同一事务核对对象、SR、版本、内容和当前事实；复用草稿保存评价逻辑生成C+1，再标applied及关联audit。相同建议重复保存只返回同一次结果，不新增C；若已进入其他版本则拒绝。资料或目录变化由保存时重新评价反映为limited，不能绕过技术确认门槛。正式P仍要求重新研制/业务变更，不允许建议应用。

## 页面与验收

任务讨论表单绑定run与revision，询问按钮显示规则/模型身份。SR“提出修改”弹窗提供请求、可空理由；demo填写摘要/阶段预览，llm可自然语言起草，也可手工明确修改。每条建议展示前后文字、影响、绑定C/R、预览评价及“确认保存为新草稿”。失效建议不能套用当前其他版本/客户；异步返回不会跳转到另一个对象。

验收覆盖询问不改R/C、模型自然语义响应及引用白名单、事务外网络、版本/职责/引用竞态、demo手工预览、理由不猜测、应用恰好一次、保存重新评价、FR/跨客户拒绝、正式P不变、v3重复迁移，及真实桌面/手机流程。2D报告、2E局部依赖图、2F真实模型质量及阶段3—6不在此次完成范围。
