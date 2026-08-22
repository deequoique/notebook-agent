# Citation 先行的 Provider Token Streaming

## 目标

把当前“活动状态流式、完整答案一次性发送”的兼容链路升级为 Citation 先行的
provider-level 文本流。知识回答必须先确定 section 状态和 Citation，再把该 section 的
provider 文本增量发送给浏览器。

Citation 在本任务中的职责仅是：证明来源属于当前 tenant 的本轮检索结果，并提供来源
追溯。服务器不判断 Citation 是否在语义上证明每句话；相关性和总结质量由回答 LLM
负责。

## 需求

1. 非空检索候选进入两阶段回答流程：第一阶段生成结构化 section/Citation 计划；第二阶段
   根据已经锁定的 section 计划流式生成正文。不得在 Citation 校验完成前公开该 section
   的模型文本。
2. `grounded` section 必须携带至少一个来自本轮 Citation allow-list 的 ID；服务器校验
   tenant、本轮归属、正整数、去重、数量上限和 section 生命周期，不做语义事实验证。
3. `unsupported` section 不调用正文模型、不接受模型自由文本，由服务器发送固定的证据
   不足文案。
4. 公开事件协议增加 section 生命周期，至少包含 `section_started`、`text_delta`、
   `section_completed`；技术中断可使用 `section_aborted` 撤掉尚未完成的临时 section。
5. provider 文本增量只能出现在一个已经通过校验、尚未结束的 section 内；事件必须继续
   携带稳定的 request/message 标识、严格递增 sequence 和 section ID。
6. 保留硬安全边界：不得把系统提示词、工具参数、原始 provider 事件、其他 tenant 的
   内容或模型自带的 URL/来源块直接发送到浏览器。该边界不是语义事实验证。
7. 完整答案仍由服务器按 section 顺序组装，使用现有 Citation/来源投影，只持久化一次；
   临时 delta、未完成 section 和 provider 载荷不得进入会话历史或数据库。
8. provider 不支持流式时继续使用当前 one-delta 兼容路径，同一 `message_id` 不得重新
   执行或重复持久化。现有 `AGENT_STREAMING_ENABLED` 仍可关闭公开 SSE。
9. 浏览器按 section 增量渲染。Citation 默认只显示视频标题、链接和时间戳；字幕 excerpt
   继续保留在数据中，但默认折叠，用户点击来源后再展开。
10. 连接断开、取消、超时或 provider 失败必须有确定终态：已公开但未完成的 section 从
    临时 UI 中撤回或标记中断，不持久化半截答案，也不自动重复提交请求。

## 非目标

- 不增加独立事实 verifier，不逐句判断 Citation 是否语义支持回答。
- 不把 Citation 描述为服务器认证的“事实证明”；产品文案使用“来源”。
- 不改变 `search_segments` 的 tenant 授权边界、空检索 `no_evidence` 状态或 Action 优先级。
- 不要求每个网络包只包含一个 token；浏览器可以收到 provider/代理合并后的小文本块。

## 验收标准

- [ ] fake streaming provider 的 grounded section 在第一个 `text_delta` 前必定出现并通过
      `section_started(citation_ids=...)` 校验。
- [ ] 非法、跨 tenant、非本轮或重复 Citation ID 在任何正文公开前失败关闭。
- [ ] 一个 grounded section 能在 `section_completed` 前产生至少两个可见文本增量，最终
      `completed.response` 与服务器组装并持久化的答案一致。
- [ ] unsupported section 只显示服务器固定文案，模型无法注入正文。
- [ ] provider 不支持 streaming 时只执行一次兼容调用并发送 one-delta，不重复提交或
      重复保存同一消息。
- [ ] provider 中断或客户端取消后，没有未完成正文进入历史；前端清除或标记对应临时
      section，并进入明确终态。
- [ ] SSE 客户端继续拒绝乱序、缺口、错误 request/message/section 标识，忽略重复或终态
      后事件。
- [ ] Citation 默认展示标题、链接和时间戳；字幕 excerpt 默认折叠且可按需展开。
- [ ] 日志只记录固定事件类型、计数、耗时和终态，不包含问题、正文 delta、Citation 内容、
      provider payload 或工具参数。
- [ ] 现有 mixed-evidence grounded、非流式 JSON、Action、tenant 隔离、前端、OpenAPI 和
      持久化回归继续通过。
