# 实施计划：私有多用户知识库 Agent 与渠道验证

## Gate 0 — 规划评审

- [x] 用户确认 `prd.md`、`design.md` 和本实施计划。
- [x] 确认渠道身份自助注册和一次性 token 跨渠道绑定；本阶段不引入邮箱/Web 主账户。
- [x] 确认首版需要可持久化、可重启恢复的有界多轮上下文，但不做跨会话长期记忆。
- [x] 确认“多网关”指多个输入输出渠道同时运行；模型 provider fallback/routing 与渠道并发分开处理。
- [x] Trellis 任务已进入 `in_progress`；本轮先完成规划同步，之后再恢复框架 spike 或业务实现。

## Gate 1 — LangBot 身份 Spike（最多两小时）

- [x] 固定被测 LangBot 版本并记录运行环境；凭据只放本地配置/环境变量。
- [ ] 启动最小 Telegram adapter，捕获一条真实入站事件。
- [x] 通过固定版本源码记录事件可提供的 `account_id`、稳定 `external_user_id`、`conversation_id` 与 `message_id` 缺口，不把 token 或消息正文写入研究记录。
- [x] 验证 Local Agent plugin tool 可以从 query context 读取 sender/bot identity，且 `user_id` 不需要出现在模型 tool arguments 中。
- [ ] 用两个 Telegram 账号验证身份和 session 分离。
- [ ] 验证新身份能在 Agent 前进入确定性注册流程，失败/禁用身份会在调用模型和 retrieval 之前被拒绝。
- [x] 验证只启用 retrieval tool、禁用通用高权限工具的配置方式。
- [x] 通过固定版本源码验证 LangBot 可为多个 enabled bot 启动独立 channel adapter task；实际断线隔离留给 fake adapter 自动测试和人工平台验收。
- [x] 将结果、版本、证据和通过/失败结论写入 `research/langbot-identity-spike.md`。

### Gate 1 决策

- [ ] **通过**：使用 LangBot Local Agent + 自定义 retrieval tool，继续 Gate 2。
- [x] **失败/超时**：LangBot channel adapter 通过，但内置 session/message-id 边界不满足要求；已按限定范围参考 Hermes，冻结为 LangBot Event Listener + PydanticAI，不改用 Hermes runtime。

## Gate 2 — 私有用户与渠道身份基础

- [x] 补充 `AppUser` 生命周期字段；新增 `ChannelIdentity` ORM 模型和 Alembic migration：FK、唯一约束、必要索引、downgrade。
- [x] 新增一次性 `ChannelLinkToken` 持久化或等价 Redis 实现：哈希存储、TTL、原子消费、目标渠道限制。
- [x] 新增框架无关 `ConversationThread` / `ConversationTurn` 持久化：tenant/channel/thread 归属、唯一约束、状态、来源和时间字段。
- [x] 新增框架无关 `ChannelEnvelope`、`TenantContext`、identity resolver、幂等 `resolve_or_register` 和明确的身份错误。
- [x] 实现确定性的 `/start` 自助注册和 `/link` 跨渠道绑定流程；这些流程不调用 LLM。
- [x] 管理员 CLI 仅提供禁用、纠错和恢复能力，不作为正常注册/绑定入口。
- [x] 删除 ingestion/search 中所有 `user_id=1` 默认值。
- [x] ingestion 不再自动创建未知 `AppUser`；CLI 的 ingest/search/ask 显式要求内部用户。
- [x] 添加 migration、identity resolver、自助注册、绑定 token 和显式用户参数测试。

### Gate 2 验证

- [x] 两个新外部身份可自助注册为两个内部用户；同一身份重复/并发注册保持幂等。
- [x] 有效 token 可把第二渠道绑定到已有用户；过期、重放、冲突和错误目标渠道全部 fail closed。
- [x] 被禁用身份、伪造身份和注册失败全部 fail closed。
- [x] 现有 ingestion/retrieval 测试更新后仍通过。
- [x] conversation 数据与知识库内容分离，migration downgrade 不触碰 content/segment/embedding。

## Gate 3 — Agent 领域契约与隔离工具

- [x] 建立 `AgentRequest`、`AgentAnswer`、source citation、失败类型等框架无关类型；请求包含服务端解析的 thread 和有界历史。
- [x] 实现 conversation repository/context builder：按最近 turn 数和 token budget 恢复上下文，持久化完成的 turn，忽略失败或半完成 turn。
- [x] 使用平台 `message_id` 做幂等去重，并对同一 thread 顺序处理；不同 thread 可以并发。
- [x] 提供开启新会话/重置当前上下文的确定性入口，不让 LLM 决定 thread 归属。
- [x] 封装 `search_segments`、`get_neighbors`、`get_item`、`open_at` 只读 service。
- [x] 所有 service 从不可变 `TenantContext` 取得 `app_user_id`；模型 tool schema 不包含 `user_id`。
- [x] 对 item/segment ID 查询统一 join/验证 owner；跨用户对象返回 not found。
- [x] 设置结果条数、工具轮数、模型请求数、超时和 evidence-required 后置校验。
- [x] 单元测试使用确定性模型/tool-loop 替身，不调用真实模型。

### Gate 3 安全测试

- [x] 用户 A 的向量、词法、neighbor、item、open_at 全部无法读到用户 B。
- [x] 伪造问题文本和模型 tool arguments 不能切换 tenant。
- [x] 注册/绑定失败或被禁用的身份不会调用 embedding、LLM 或数据库 retrieval。
- [x] 空结果、工具异常和轮数耗尽均 fail closed 并返回可理解消息。
- [x] 两轮追问、runtime 重启恢复、上下文窗口截断、新会话重置和消息重放均有测试。
- [x] 同一用户不同渠道默认不共享会话历史；不同用户、渠道和 thread 的 context 全部不串线。

## Gate 4 — Agent Runtime 决策路径

### Path A：LangBot Local Agent Spike 通过

- [ ] 把领域 service 封装成 LangBot 自定义只读 tool。
- [ ] 从当前可信 event/session 构造 `TenantContext`，不读取 prompt identity。
- [ ] 配置可替换的模型 provider；首版不要求自动 fallback/routing。
- [ ] 关闭除知识库检索外的全部工具。

### Path B：Spike 失败

- [x] 增加 PydanticAI 依赖并固定版本。
- [x] 使用 typed dependencies 注入 `TenantContext`，注册相同的领域 tools。
- [x] LangBot Event Listener 负责构造 `ChannelEnvelope`、解析身份和调用内部 Agent 边界。
- [x] 如果使用 HTTP，限制到 loopback/private network，并增加机器认证和 replay/correlation 防护；测试拒绝未认证请求。
- [x] 使用 PydanticAI TestModel/FunctionModel 或等价替身验证 tool loop。

### Gate 4 验证

- [x] 运行时更换模型 provider 不修改工具、prompt 或答案 contract。
- [x] 模型看不到可更改 tenant 的 tool 参数。
- [x] Agent 至少一次检索后才能生成知识库事实答案。
- [x] Channel Gateway Supervisor 启动所有 enabled adapters，并按 envelope 来源把答案路由回正确渠道；单渠道故障被隔离。

## Gate 5 — Telegram 完整端到端

- [ ] 配置 Telegram bot 与 LangBot，保存安全的本地部署说明。
- [ ] 两个 Telegram 用户分别完成自助注册；再用一次性 token 验证一个第二渠道身份可以关联既有用户。
- [ ] 为两个用户准备互斥的知识库测试内容。
- [ ] 验证入站消息 → 身份解析 → Agent 多步检索 → 带来源/时间戳答案 → Telegram 回复。
- [ ] 验证依赖前文的追问，并在 Agent/channel runtime 重启后恢复同一 Telegram 会话。
- [ ] 验证新账号注册、禁用账号、绑定 token 重放、无结果、模型失败、retrieval 失败和重试上限。
- [ ] 记录人工 E2E 验收步骤和结果，不提交凭据或私聊内容。

## Gate 6 — 微信个人号私聊 Smoke Test

- [ ] 配置 LangBot OpenClaw/iLink 微信 adapter，扫码凭据仅留本地。
- [ ] 验证私聊入站事件包含稳定 sender identity，并能映射已有 `AppUser`。
- [ ] 复用 Telegram 的 `ChannelEnvelope`、identity resolver 和 Agent 入口完成一次问答回复。
- [ ] 保持 Telegram 与微信 adapter 同时运行，交错发送消息并验证回复路由、上下文和 tenant 均不串线。
- [ ] 人为断开微信 adapter 后验证 Telegram 仍可继续完成问答，再恢复微信 adapter。
- [ ] 记录账号、群聊、长期在线和平台策略限制；这些不作为五天通过条件。
- [ ] 若 sender identity 不稳定或不可验证，停止并报告受阻，不回退默认用户。

## Gate 7 — 回归、文档和收尾

- [x] 运行完整 pytest；真实平台 E2E 保持为显式手动测试。
- [x] 核对 migration upgrade/downgrade，不触碰 content/segment/embedding 数据。
- [x] 检查日志和配置，确保无 token、二维码、私聊正文、证据全文和用户映射泄漏；会话正文只存在于受控 conversation store。
- [x] 更新 README/运行手册：创建用户、绑定渠道、ingest、ask、同时启动全部 enabled gateways、上下文恢复/重置、Telegram E2E、微信 smoke。
- [x] 增加独立启动与部署手册：首次安装、启动/停止顺序、同网络命名空间约束、systemd、健康检查、备份、升级回滚和排障。
- [x] 记录 Slack 后续接入只需新增 adapter 配置，不改变 Agent/retrieval contract。
- [ ] 按 Trellis finish-work 流程做最终 review、spec 学习记录、提交和归档。

## 建议验证命令

实际命令在依赖和 adapter 版本由 Gate 1 固定后补充；最低要求：

```bash
pytest
python -m app.cli --help
python -m app.cli users --help
python -m app.cli ask --help
alembic upgrade head
alembic downgrade -1
alembic upgrade head
```

## 回滚点

- Gate 1：不修改业务代码；删除本地 LangBot spike 配置即可。
- Gate 2：回滚新增身份与会话 migration；用户知识库内容数据不变，会话记录按独立数据类处理。
- Gate 3/4：Agent 模块与既有 retrieval/ingestion 隔离，可关闭新入口。
- Gate 5/6：仅停用故障 channel adapter；其他渠道继续运行。Telegram 或微信失败不放宽 tenant isolation。
