# 连接 Agent 层与现有 Embedding 检索层

## Goal

把已经存在的 PydanticAI Agent、Embedding provider 和 PostgreSQL/pgvector 检索代码
连接成一条明确且可验证的生产链路：自然语言问题必须由应用生成 query embedding，
在当前 `AppUser` 的私有知识库内执行向量检索，并把真实片段作为 Agent 工具证据返回。

本任务不是重新实现 Agent 或 embedding，而是完成现有初步接线、消除静默降级和重复
装配，并补足跨层集成测试与部署诊断。

## Background

- `app/agent/runtime.py` 已注册 `search_segments`、`get_neighbors`、`get_item`、`open_at`
  四个只读工具，并强制答案至少引用一次真实检索证据。
- `app/agent/services.py` 已能接收可选 embedder，并把 query vector 交给
  `app/retrieval/search.py::vector_search()`；向量查询已按 `ContentItem.user_id` 过滤。
- `app/bootstrap.py` 在存在 `ZHIPU_API_KEY` 时为 channel gateway 注入 embedder，但缺少
  key 时会静默退化为纯词法检索，无法证明 Agent 与 embedding 层已连接。
- CLI `ask` 与 channel gateway 分别构造 embedder/Agent/service，装配规则可能漂移。
- 当前测试分别覆盖 Agent tool loop、embedding HTTP client、tenant isolation 和向量 SQL，
  但没有一条测试证明 `Agent → query embedding → pgvector → Citation → AgentAnswer` 全链路。
- `ZhipuEmbedder` 会校验响应条数，但尚未验证每个向量的维度和数值有效性；错误最终只
  映射为笼统 runtime failure，不利于安全排障。
- 最新微信实测显示：一条普通非命令消息已经穿过 adapter、bridge 和 gateway 到达 Agent；
  Agent 因没有发生必要检索而返回 evidence guard failure，随后客户端又收到一条渠道不可用
  提示。该探针证明渠道→Agent 已通，但不能证明 query embedding/pgvector 已执行；同时暴露
  “一次入站疑似得到两次回复”的未定位问题。任务证据只记录此状态，不保存截图、原消息、
  外部身份或具体会话时间。

## Requirements

### R1 — Query embedding is a required knowledge-search dependency

- 所有普通知识问答必须先由受信任应用代码为检索词生成 query embedding，再调用
  pgvector；模型不得直接提供向量、SQL 或 `user_id`。
- 缺少 embedding 配置、provider 请求失败、响应条数/维度错误时必须 fail closed，返回
  稳定且不包含问题正文的 `embedding_unavailable` 类错误；不得静默降级为“看起来已经
  接通”的纯词法 Agent。
- `/start`、`/whoami`、`/link`、`/new` 等确定性渠道命令不调用模型或 embedding，provider
  暂时不可用时仍可完成身份与会话管理。

### R2 — One shared embedding boundary

- 定义最小 typed embedding protocol，供 ingestion 和 query retrieval 共同使用；
  `KnowledgeServices` 不再依赖 `Any`。
- Zhipu/OpenAI-compatible 实现必须校验每批响应顺序、数量、配置维度、有限数值和空输入。
- CLI `ask` 与 channel gateway 必须复用同一个 composition helper；embedding provider
  的模型、endpoint、dimensions 和 batch size 只能有一套装配规则。
- 不把 embedding API key、请求正文或向量写入日志、测试输出、Trellis 文档或异常文本。

### R3 — Tenant-scoped hybrid retrieval

- query embedding 和词法检索可以共同提供候选，但所有 `vector_search`、`bm25_search`、
  segment/item hydration 均必须使用服务端注入的不可变 `TenantContext.app_user_id`。
- 模型工具 schema 继续不包含 `user_id`；跨用户 segment/item 必须统一表现为 not found。
- 本任务保持现有有界去重/合并策略，不引入 RRF、cross-encoder rerank 或新的 ranking
  产品变量。

### R4 — Evidence-preserving Agent integration

- `search_segments` 返回的候选必须 hydrate 为真实 `Citation`，含内部 item/segment ID、
  标题、证据片段和可点击时间戳/锚点。
- Agent 可基于首次结果改写 query 或继续调用 neighbors/item/open_at；最终答案仍由现有
  evidence-required 后置校验约束，不能引用未被工具返回的片段。
- embedding/数据库异常、空结果和 citation 不一致必须在现有工具轮数与 timeout 内停止。
- “检索成功但零命中”与“检索未成功完成”必须是两个稳定状态和两套提示：
  - `not_found`：query embedding 和数据库查询均成功完成，但当前用户知识库没有足够证据；
    提示用户换关键词或先导入内容，不暗示系统故障。
  - `failed`：embedding provider、pgvector/数据库、timeout、配置或工具执行异常；提示知识库
    服务暂时不可用并建议稍后重试，不声称知识库中不存在相关内容。
- `search_required` 属于 Agent 合约失败，不能归类为 `not_found`。模型草稿引用工具未返回的
  segment ID 时，`citation_mismatch` 只作为内部 validation signal：草稿不得返回或持久化，
  Agent 必须在有界预算内改写检索并再次调用 `search_segments`，然后重新生成只引用真实
  Citation 的答案。
- citation 修复预算耗尽时才返回通用 `failed/answer_unavailable` 提示，引导用户换个问法或
  稍后重试；不得向用户展示 `citation_required`、伪造编号、“答案被拒绝”等内部 guard 文案，
  也不得暴露无效草稿、SQL、provider 或 secret。

### R5 — Cross-layer verification and operability

- 自动测试使用 fake embedding provider 和 PydanticAI 测试模型，不调用真实外部 API。
- 至少一条真实 PostgreSQL/pgvector 集成测试覆盖完整 query embedding 路径，并使用两个
  用户证明同一 query vector 不能读取另一用户数据。
- 测试必须证明 CLI/channel 共用装配、普通问题缺少 embedding 时 fail closed、确定性命令
  仍可用，以及 provider 错误不会泄露问题正文或密钥。
- 提供一条显式人工 smoke：使用真实 provider 和已有已 embedding 内容执行 `ask`，确认
  向量检索证据、标题和时间戳正确；人工 smoke 不写入问题正文或凭据。
- 同一个 channel message/correlation 只能产生一条最终平台回复。Agent 已返回
  `search_required`、`embedding_unavailable`、`not_found` 或成功答案后，bridge/LangBot
  不得再追加第二条“渠道不可用”回复；当前微信双回复现象必须先用脱敏 fake event 重现，
  再按实际根因修复。

## Acceptance Criteria

- [ ] AC1：集成测试证明普通 `AgentRequest` 调用 `search_segments` 后，fake provider 收到
  检索词、返回的 query vector 被用于真实 pgvector 查询，最终 `AgentAnswer` 引用实际
  tenant-owned segment。
- [ ] AC2：同一个 query vector 面对用户 A/B 的互斥数据，只能返回当前
  `TenantContext.app_user_id` 所属片段；模型参数中不存在可覆盖 tenant 的字段。
- [ ] AC3：缺少 API key、provider timeout/error、响应数量错误、维度错误或非有限数值时，
  普通知识问答均 fail closed 为稳定 embedding error；不会执行无向量的 Agent 回答。
- [ ] AC4：embedding 不可用时 `/start`、`/whoami`、`/link`、`/new` 仍不调用 embedding；
  被禁用/未可信身份仍在 provider 调用前拒绝。
- [ ] AC5：CLI `ask` 与 channel gateway 复用相同的 embedding/Agent composition helper；
  配置字段和维度校验没有第二套实现。
- [ ] AC6：完整测试不调用真实 embedding/模型 API，不输出问题正文、向量、外部身份或
  secret；现有 51 项回归和新增测试全部通过。
- [ ] AC7：人工 smoke 使用真实 provider 和已有私有知识库返回至少一个真实标题、证据
  片段与可点击时间戳，并由人工确认定位正确。
- [ ] AC8：自动测试和微信 smoke 均证明一条入站消息只产生一条最终回复；Agent 的
  fail-closed 答案不会触发 bridge/LangBot 再发送渠道不可用提示，且诊断不记录消息正文或
  外部身份。
- [ ] AC9：相同问题分别模拟“检索成功零命中”和“embedding/数据库失败”；前者返回
  `status=not_found` 与无结果提示，后者返回 `status=failed` 与稍后重试提示，两者的
  `error_code`、文案和渠道渲染均不混淆。
- [ ] AC10：模型第一次输出不存在的 citation ID 时，该草稿不进入 `AgentAnswer`、conversation
  store 或渠道回复；Agent 至少重新调用一次 `search_segments` 并生成有效引用。若有界修复
  仍失败，用户只收到一次通用 `answer_unavailable` 提示，不看到内部 guard 或无效引用。

## Out of Scope

- 重做 PydanticAI tool loop、ChannelEnvelope、身份注册、跨渠道绑定或 LangBot adapter。
- 更换 embedding 模型、重新生成现有数据库全部向量或修改固定 1536 维 schema。
- RRF、cross-encoder rerank、语义缓存、长期记忆、推荐和写操作工具。
- 把 embedding provider 暴露成模型可调用工具，或允许模型控制 tenant/SQL。

## Notes

- 父任务：`08-05-knowledge-retrieval-agent`。
- 当前代码已有初步接线；本任务以“收紧并证明真实连接”为目标，不重复创建平行实现。
