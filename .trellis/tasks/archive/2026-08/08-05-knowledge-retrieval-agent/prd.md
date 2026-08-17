# 自然语言知识库检索 Agent

## Goal

基于 P0 已建立的 pgvector embedding 数据库，让 Agent 通过自然语言自主调用只读检索工具，返回带证据片段和时间戳链接的答案。

## Background

- P0 已有 `segment.embedding`、HNSW 索引、向量检索和 BM25/trigram 检索代码。
- 当前交互入口是固定流程的 `ingest` / `search` CLI；`app/agent/` 仍为空目录，没有 Agent 运行时。
- 项目是 Python 3.11、SQLAlchemy、PostgreSQL/pgvector，embedding 已使用 OpenAI-compatible API。
- 本任务是父任务 `08-04-video-text-kb` 的 P1 子任务，优先于 Web UI、浏览器扩展和新增平台 Connector。
- 产品方向已明确要求多用户，并需要统一考虑微信、Slack、Telegram 三个渠道；长期用户记忆暂缓，但必须持久化并恢复有边界的对话上下文。
- 首版交付窗口只有五天，需要先建立正确的用户/数据隔离边界，再用一条主渠道做完整验收、其余渠道做浅层验证，减少并行变量。

## Requirements

- 提供 `python -m app.cli ask "<自然语言问题>"` 入口。
- Agent 可以根据问题自主改写检索词、重复检索，并按需读取相邻片段或内容条目详情。
- P1 只开放只读工具：`search_segments`、`get_neighbors`、`get_item`、`open_at`；不得转录、写库或修改观看状态。
- 知识库内容回答必须来自实际工具结果，关键结论必须附标题、证据片段和时间戳链接。
- 无结果或证据不足时明确回答知识库中未找到，不得依靠模型记忆补写。
- 工具参数与返回值使用结构化类型，设置最大调用轮数、超时和异常边界。
- Agent 运行时不能侵入现有 retrieval 层；`app/agent/tools.py` 只封装并复用已有检索能力。
- 框架、模型接口和可观测方案必须基于官方资料完成技术选型，并记录取舍。
- Agent 模型层必须保持供应商可替换，支持多个直接 provider 或 OpenAI-compatible gateway；业务工具、prompt 和最终答案契约不能依赖某个模型厂商的专有类型。
- 多平台输入/输出必须通过 channel adapter 与 Agent 核心隔离。各平台负责把自身事件归一成统一请求，并把统一答案渲染成平台消息；Agent 运行时不得直接依赖具体平台 SDK。
- 所有已配置且启用的渠道网关必须能够同时在线运行；启用微信不能要求停用 Telegram，未来启用 Slack 也不能修改或替换 Agent 核心。
- 渠道管理层必须支持独立启动、健康检查、故障隔离和按来源路由回复；单个渠道断线不得阻塞其他渠道处理消息。
- 渠道身份必须先映射为内部 `AppUser`，再允许执行检索；`user_id` 只能由可信应用代码或 Agent dependency 注入，不能由模型选择或覆盖。
- 每个渠道用户必须拥有独立的对话/session 标识，避免聊天上下文串线。
- 对话上下文必须持久化到框架无关的存储中；同一可信会话在服务重启、渠道短暂断线或 Agent runtime 重启后，能够恢复最近的多轮对话并继续处理追问。
- 上下文恢复只加载受条数和 token budget 限制的最近已完成 turn；不得把它扩展成跨会话用户画像、偏好推断或语义长期记忆。
- 同一 `AppUser` 可以同时通过多个已绑定渠道使用同一私有知识库；不同渠道会话默认各自维护上下文，不自动合并或串联历史。
- 渠道运行时必须用一套管理模型覆盖微信、Slack、Telegram，首版不得分别从零实现三套 bot adapter。
- 每个用户拥有私有知识库；所有检索入口必须强制按当前内部 `AppUser.id` 过滤，禁止跨用户读取标题、片段、相邻片段、内容详情和时间戳链接。
- 未完成渠道身份解析的消息不得进入检索链路；新用户必须通过可信渠道身份完成自助注册，不允许自动落到共享或默认用户。
- 现有 ingestion、search 和新 `ask` CLI 必须显式指定已存在的内部用户；删除 `user_id=1` 默认值和 ingestion 自动创建用户行为。
- 首次收到可信 Telegram/微信身份时，注册服务应原子地创建 `AppUser` 与首个 `ChannelIdentity`；重复或并发消息必须幂等，不能生成多个用户。
- 同一用户绑定第二个渠道时必须使用短期、单次、不可猜测的绑定凭据，将新 `ChannelIdentity` 关联到已有 `AppUser`；不得根据昵称、手机号猜测或自动合并。
- 管理员只负责封禁、纠错和恢复等运维操作，不作为正常用户创建或渠道绑定的必经入口。

## Acceptance Criteria

- [ ] 输入自然语言问题后，Agent 至少调用一次检索工具，并能定位只在某视频中段出现的概念。
- [ ] Agent 可以完成 `search_segments → get_neighbors/get_item → open_at` 的多步只读工具调用。
- [ ] 最终答案包含来源标题、原文证据和可点击时间戳；人工点击后内容与结论一致。
- [ ] mock 无结果时，Agent 明确返回未找到且不会生成无来源答案。
- [ ] mock 工具异常或持续空结果时，在最大轮数内停止并给出可理解的失败说明。
- [ ] 单元测试不依赖真实模型 API；真实数据库 + 真实模型另设一条可手动运行的端到端验收。
- [ ] 更换模型 provider 不需要修改 Agent prompt、工具实现和答案 schema；provider 专有代码只存在于模型配置/适配层。
- [ ] CLI 通过统一 `AgentRequest` / `AgentAnswer` 契约调用 Agent，Agent 核心不导入 CLI 或任何具体平台 SDK，为后续 Web、扩展和消息平台复用同一核心。
- [ ] 使用两个测试用户验证 session 不串线，并验证用户 A 无法通过任何只读 Agent 工具检索用户 B 的数据。
- [ ] 同一会话完成至少一次依赖前文的追问；重启 Agent/channel runtime 后，系统能从持久化记录恢复最近上下文并正确回答。
- [ ] 上下文窗口达到配置上限或用户开启新会话后，不再把窗口外历史发送给模型；不同用户、不同渠道和不同会话的历史不会串线。
- [ ] 未绑定或伪造渠道身份时请求被拒绝；模型即使生成其他 `user_id` 也不能改变服务端绑定的用户范围。
- [ ] 新渠道用户可以自助创建私有账户；同一可信身份重复/并发注册只产生一个 `AppUser` 和一个 `ChannelIdentity`。
- [ ] 已登录用户可以生成短期单次绑定凭据，在另一渠道绑定到同一 `AppUser`；过期、重放和已使用凭据均被拒绝。
- [ ] Telegram 完成消息进入、身份解析、Agent 检索、带来源答案返回的完整端到端链路。
- [ ] 微信个人号完成私聊扫码登录、接收消息和发送回复的 smoke test，并复用与 Telegram 相同的身份映射和 Agent 请求契约。
- [ ] Telegram 与微信 adapter 能同时保持启用并交错处理消息；微信断线或失败时 Telegram 链路仍可继续工作。
- [ ] Slack 保留在统一渠道 adapter 设计和候选 gateway 能力范围内，但不作为五天首版的实机验收项。

## Out of Scope

- Web UI 和浏览器扩展。
- RRF、cross-encoder rerank 等排序增强。
- ASR、自动标签、写操作工具，以及跨会话用户画像、偏好建模、语义记忆召回等长期记忆能力。
- 多 Agent 协作、handoff 和复杂任务规划。
- 分别从零开发微信、Slack、Telegram 协议适配器。
- 邮箱/密码、OAuth、完整 Web 注册中心和账户合并后台。

## Confirmed Decisions

- 模型供应商必须可替换；不采用 OpenAI-only 的 Agent 架构。
- Agent 框架需要能对接多个模型 provider / gateway，并为未来多平台输入输出保留稳定边界。
- 多用户是硬需求，长期记忆策略本阶段暂缓。
- 每个用户拥有私有知识库；首版必须提供服务端强制的 tenant isolation，而不只是对话 session 隔离。
- 微信、Slack、Telegram 必须进入统一渠道管理方案，但五天内不要求三条渠道都达到相同的端到端测试深度。
- 五天首版的渠道验收深度确定为：Telegram 完整端到端、微信个人号私聊 smoke test；Slack 本阶段只保留架构兼容，不做实机验收。
- 渠道 runtime 采用带停止条件的验证：先限时验证 LangBot Local Agent + 自定义 retrieval tool 能否取得可信 sender identity；成功则首版只用 LangBot，失败则改为 LangBot 渠道层 + PydanticAI Agent 核心。
- LangBot 身份验证失败时，研究 Hermes 的 gateway adapter、session key 和 tool context 实现作为参考；不得用 prompt 或模型参数绕过身份边界，也不得未经重新评审直接替换整个 runtime。
- 正常用户必须可以通过可信渠道身份自助注册；管理员不是创建用户或绑定渠道的必经角色。
- 五天首版采用“渠道即首个登录方式”：Telegram/微信首次 `/start` 原子创建 `AppUser + ChannelIdentity`，第二渠道通过短期单次绑定凭据关联；本阶段不引入邮箱/Web 注册中心。
- 首版需要可持久化、可在进程重启后恢复的有界多轮上下文，但不做用户画像或语义长期记忆。
- “多网关”在本任务中指微信、Telegram、Slack 等输入输出渠道网关：所有已启用渠道必须能并发运行；模型 provider 的自动 fallback/routing 是另一层能力，不是本要求。
- 同一用户绑定多个渠道后共享私有知识库，但各渠道会话上下文默认隔离，不自动跨渠道合并。

## Notes

- 本轮已补齐上下文恢复和多渠道并发要求；在规划文档同步及校验完成前暂停框架 spike 和业务实现。
