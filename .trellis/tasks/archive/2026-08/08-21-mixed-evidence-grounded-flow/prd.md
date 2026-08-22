# 混合证据 Grounded 回答链路改造

## 问题

当一条用户消息包含多个知识子问题时，当前主 Agent 可以在检索成功后调用
`report_no_relevant_evidence`。这个调用是模型控制的、整条消息级别的终态判断，
因此一个没有证据的子问题可能把另一个已有证据的子问题一起变成 `no_evidence`。

## 需求

1. 从主 Agent 的工具面板中移除 `report_no_relevant_evidence`。模型不能通过工具
   直接选择公开的 `no_evidence` 结果。
2. `search_segments` 继续作为主 Agent 的检索工具。主 Agent 可以根据问题自由选择检索
   当前 tenant 的整个知识库，或传入 `item_id` 限定某个视频；模型不能传入或修改
   `tenant_id` / `user_id`。
3. 服务器只把当前 tenant 有权访问的知识库作为检索授权上限。`item_id` 非空时，服务器
   必须验证该条目属于当前 tenant 且处于可检索状态；不增加
   `strict_video_scope` / `tenant_wide_scope` 之类的服务器范围意图状态。
4. 一次或多次干净检索最终没有留下任何候选时，由服务器生成服务器拥有的
   `no_evidence` 响应。只要本轮存在候选，就必须进入 grounded 回答路径，不再由服务器
   提前判断候选是否覆盖全部语义子问题。
5. 非空候选统一进入结构化 Answer Composer；不得让主 Agent 的一段自然文本凭一个
   Citation 直接覆盖整条混合问题。Grounded 回答必须支持：有证据的部分使用本轮
   Citation；另一部分使用显式 `unsupported` section，由服务器渲染固定的证据不足文案。
6. 保留服务器拥有的 Citation/安全校验：引用 ID 必须来自本轮检索实际返回的
   allow-list；继续执行租户边界、条目/片段归属、URL/来源块禁令和来源服务器渲染。
7. 保留瞬时读取失败的独立语义。读取未完成时，不能仅因为候选为空就转换成
   `no_evidence`。
8. 保留现有回答 Agent 的有界重试、持久化、诊断、Action 优先级和非流式公开响应，
   除非回归测试证明必须调整。

## 验收标准

- 干净的空检索返回 `not_found/no_evidence`，且不调用回答 Composer。
- 非空检索不再暴露或调用 `report_no_relevant_evidence`，而是进入 grounded 回答处理。
- 主 Agent 可以在 tenant 全库范围调用 `search_segments(query)`，也可以调用
  `search_segments(query, item_id=...)`；两种调用都由服务器重复验证 tenant 和可检索状态。
- 显式视频 URL 不会额外生成决定检索上限的服务器范围意图状态。
- 混合 q1/q2 请求可以返回带 Citation 的 q1 答案，同时明确说明 q2 缺少足够证据，且
  不丢弃 q1 的 Citation。
- unsupported section 不能携带模型自定义事实或自由解释；服务器必须使用固定文案，
  防止未引用事实混入 grounded 响应。
- 伪造/未知 Citation、危险 URL/来源块、范围越界和数量超限仍由服务器安全校验并
  fail closed。
- 既有 Action、读取失败、重试和单问题 grounded 测试继续通过。
- 增加回归测试，覆盖工具移除、空检索终态、非空候选路由以及混合 q1/q2 回答。
