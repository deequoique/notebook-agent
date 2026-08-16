# 私有多用户知识库 Agent 与多渠道设计

## 1. 设计目标

在五天内交付一个可验证的私有多用户知识库 Agent：Telegram 完成端到端，微信个人号完成私聊收发 smoke test，Slack 保持架构兼容但不实测。首版持久化有边界的对话上下文并支持重启恢复；跨会话用户画像、偏好等长期记忆、写操作和多 Agent 不进入本阶段。

首要正确性不是“bot 能回复”，而是任意渠道消息都必须先解析出可信外部身份，再映射成内部 `AppUser.id`，之后所有 retrieval tool 都只能读取该用户的数据。

## 2. 已确认决策与本阶段默认值

### 已确认

- 每个用户拥有私有知识库，禁止跨用户检索。
- 微信、Slack、Telegram 使用统一渠道管理边界。
- Telegram 是完整 E2E 基准；微信个人号只做私聊 smoke；Slack 不做五天实机验收。
- 先限时验证 LangBot Local Agent；身份边界不成立时切换到 LangBot + PydanticAI。
- LangBot 失败时只参考 Hermes 的身份/session 实现，不自动替换整个系统。
- 首个可信 Telegram/微信身份自助创建账户，第二渠道通过短期单次绑定 token 关联；不引入邮箱/Web 主账户。
- 已启用的微信、Telegram 等渠道网关必须并发运行；不是在配置中选择一个“当前渠道”。
- 同一可信会话需要持久化最近的多轮上下文，并能在进程重启或渠道重连后恢复。

### 为减少五天变量而采用的默认值

- 对话恢复使用框架无关的 PostgreSQL repository；每次只加载最近已完成 turn，并受可配置的 turn 数与 token budget 双重限制。
- 不同渠道会话默认隔离上下文；绑定到同一 `AppUser` 只代表共享私有知识库，不代表自动合并微信和 Telegram 历史。
- 模型 provider 与渠道 gateway 是两层概念。模型 provider 保持可替换，首版不要求自动 fallback/routing；渠道 gateway 则要求所有已启用 adapter 同时运行。
- 采用渠道身份自助注册；管理员 CLI 仅用于封禁、纠错和恢复。跨渠道使用一次性绑定凭据，不做邮箱/密码、OAuth 或管理后台。
- Agent 只开放本任务定义的只读知识库工具，关闭 terminal、filesystem、web、delegation、memory 和平台管理工具。

## 3. 系统边界

```text
Telegram adapter ----\
WeChat adapter --------> Channel Gateway Supervisor / Dispatcher
future Slack adapter --/        |  (concurrent lifecycle + reply routing)
                                v
                    ChannelEnvelope (trusted fields)
                                |
                    ChannelIdentityResolver
             (channel, account_id, external_user_id)
                                |
                         TenantContext
                    (internal app_user_id)
                                |
               ConversationRepository / ContextBuilder
                   (durable bounded recent turns)
                                |
                  AgentRequest(question + history)
                                |
              Local Agent tool OR PydanticAI deps
                                |
                    read-only domain services
                                |
              PostgreSQL / pgvector scoped by user_id
```

`external_user_id` 和 `app_user_id` 可以存在于可信应用上下文中，但不会进入模型可填写的 tool schema。模型只选择查询文本、结果数量等业务参数。渠道 supervisor 可以由一个支持多 adapter 的进程或多个受统一管理的 adapter 进程构成；验收关注并发在线、统一契约和故障隔离，不强制必须在单进程内运行。

## 4. 身份与数据模型

`app_user` 从占位 FK 实体升级为账户主体，至少保存创建时间与可禁用状态；渠道身份是它的登录方式，而不是用户本身。

新增 `channel_identity`：

| 字段 | 说明 |
| --- | --- |
| `id` | bigint PK |
| `app_user_id` | FK → `app_user.id`，不可空 |
| `channel` | `telegram` / `wechat` / `slack`；使用可扩展文本字段 |
| `account_id` | bot、微信登录账号或 Slack workspace/app 的稳定标识 |
| `external_user_id` | 渠道事件提供的稳定 sender 标识 |
| `created_at` | 绑定时间 |

唯一约束：`(channel, account_id, external_user_id)`。同一外部身份只能映射到一个内部用户；同一内部用户未来可以绑定多个渠道身份。

新增短期 `channel_link_token`（也可在 Gate 1 后选择 Redis 实现，但 contract 不变）：

| 字段 | 说明 |
| --- | --- |
| `token_hash` | 只保存随机 token 的哈希，不保存明文 |
| `app_user_id` | 发起绑定的已认证用户 |
| `expires_at` | 短期过期时间 |
| `consumed_at` | 单次使用标记 |
| `target_channel` | 可选，限制目标渠道 |

新增框架无关的会话持久化模型：

| 实体 | 说明 |
| --- | --- |
| `conversation_thread` | 归属 `app_user_id` 与 `channel_identity_id`，保存渠道 `conversation_id`、当前状态和更新时间 |
| `conversation_turn` | 保存 thread 内已完成的用户消息、Agent 回答、结构化来源、状态和时间；失败/中断 turn 不进入恢复窗口 |

`conversation_thread` 对可信渠道会话键建立唯一约束。消息正文只保存在应用会话存储中，不写入普通运行日志；上下文构建器按最近 turn 数和模型 token budget 截断。首版支持重置/开启新会话，不从其他渠道自动拼接历史。

### 首个渠道自助注册

1. 用户向 Telegram/微信 bot 发送 `/start` 或第一条消息。
2. Gateway 提供可信 `(channel, account_id, external_user_id)`。
3. 注册服务在一个事务中查找或创建 `AppUser + ChannelIdentity`；唯一约束保证并发幂等。
4. 新账户得到空的私有知识库和 onboarding；已有账户直接进入自己的 tenant context。

不能用聊天消息里声明的 `user_id` 创建账户，也不能由客户端指定内部主键。

### 第二渠道自助绑定

1. 用户在已经登录的渠道请求“绑定新渠道”，服务端生成高熵、短期、单次 token。
2. 用户在第二渠道把 token 作为首条绑定命令提交。
3. 服务端验证 token、目标渠道和未使用状态后，将第二个 `ChannelIdentity` 关联到原 `AppUser`，并原子消费 token。
4. 过期、重放、已绑定或冲突身份全部拒绝；系统不按昵称自动合并账户。

如果用户未使用绑定 token，直接从新渠道注册，则形成一个新的私有账户。首版不自动合并两个已有账户，避免错误合并导致数据泄漏。

不允许 ingestion 自动创建未知 `AppUser`，也不允许未绑定身份回退到 `user_id=1`。

## 5. 可信契约

框架无关领域类型：

- `ChannelEnvelope`：`channel`、`account_id`、`external_user_id`、`conversation_id`、`message_id`、`text`。
- `TenantContext`：`app_user_id`、`channel_identity_id`、账户状态和审计关联值。
- `AgentRequest`：当前问题、不可变 `TenantContext`、`thread_id` 和由服务端恢复的有界历史；客户端和模型都不能提交任意历史覆盖服务端记录。
- `AgentAnswer`：答案正文、结构化来源、失败类型和运行元数据。

身份解析/注册发生在 Agent 运行之前。非注册消息解析失败返回 `UnboundIdentity`；不能让 Agent 自己决定如何处理，更不能调用 retrieval。注册和跨渠道绑定由确定性的应用服务处理，不交给 LLM。

如果采用 HTTP 形式连接 LangBot 与 PydanticAI，只允许 loopback/private network，并使用共享密钥签名或等价的机器认证；外部请求不得直接提交可被信任的 `app_user_id`。

## 6. Retrieval 强制隔离

现有代码的以下默认值必须删除：

- `create_item(..., user_id=1)`
- `ingest_url(..., user_id=1)`
- `vector_search(..., user_id=1)`
- `bm25_search(..., user_id=1)`
- CLI 隐式使用默认用户

所有入口必须显式拿到已存在的 `AppUser.id`。`get_item`、`get_neighbors`、`open_at` 不能只凭对象 ID 查询，必须 join/验证所属 `ContentItem.user_id == TenantContext.app_user_id`。查不到或属于其他用户时统一返回 not found，避免泄漏对象是否存在。

Agent tool schema 只包含：查询文本、限定条数、item/segment 引用等业务参数。`user_id` 由 closure、dependency 或可信 session context 注入。

## 7. LangBot 两小时 Spike

### 成功条件

- Telegram adapter 事件中能取得平台提供的稳定 sender ID、bot/account ID、conversation ID 和 message ID。
- Local Agent 自定义 tool handler 能从事件/session 的可信上下文取得 sender identity，而不是从 prompt 或 LLM tool arguments 获取。
- 两个 Telegram 用户连续发消息时得到不同且稳定的 identity/session。
- 新身份可以在 Agent 运行前完成确定性自助注册；绑定/注册失败时不会调用 LLM 或 retrieval。
- 能只启用知识库 retrieval tool，关闭通用高权限工具。

### 停止条件

任一关键身份字段只能从消息文本、display name、模型参数或不可验证的客户端字段获得；或者 Local Agent tool 无法访问当前 event/session identity。两小时到点仍未证明也视为失败，不继续打补丁绕过。

### 失败后的路径

LangBot 继续只做渠道 adapter。Event Listener 从可信事件构造 `ChannelEnvelope`，解析为 `TenantContext` 后调用 PydanticAI Agent 核心。PydanticAI 通过 typed dependencies 把 `app_user_id` 注入只读工具。

## 8. Hermes 参考回退

只有 LangBot spike 失败时才进入，研究范围限定为：

- gateway adapter 如何从平台事件提取 sender/source identity；
- session key 如何组合 platform、account、chat、user；
- tool execution context 如何携带当前 session/source；
- group session 是否按用户拆分；
- 通用工具如何按平台禁用。

研究结果写入当前任务 `research/`。如果 Hermes 自身可以安全完成私有 tenant 绑定而 LangBot 不行，需要先修订本设计并重新评审，不能在实施中静默换框架。

## 9. 会话与记忆

首版使用可信 `(channel, account_id, external_user_id, conversation_id)` 解析内部 `conversation_thread`。同一 thread 的已完成 turn 持久化到 PostgreSQL，Agent/channel runtime 重启或渠道短暂重连后，`ContextBuilder` 自动加载最近上下文，因此“这个呢？”一类依赖前文的追问可以继续。

恢复窗口同时受最大 turn 数和 token budget 限制，具体默认值在实现时固化到配置并测试；不得无限增长。写入采用 message id 幂等和 thread 内顺序控制：同一会话按序处理，不同用户/会话可并发。半完成、失败或被重放的消息不得污染恢复上下文。

用户开启新会话后创建新的 logical thread，旧 thread 不再自动进入上下文。绑定到同一 `AppUser` 的微信和 Telegram 身份共享知识库权限，但各自 conversation 默认维护独立 thread；本阶段不自动跨渠道恢复同一聊天历史。

这属于“会话连续性”，不是长期记忆：不抽取用户画像，不生成跨会话摘要，不把历史 embedding 后做语义召回，也不将偏好注入所有未来对话。

## 10. 渠道验收

### Telegram E2E

- 两个真实或测试账号分别自助注册为两个 `AppUser`。
- 两边知识内容不同。
- 每个账号只能命中自己的内容，答案带证据和时间戳。
- 新账号可自助注册；被禁用、注册失败或伪造的账号被拒绝且不会触发 retrieval。
- 完成一轮依赖前文的追问，并在 runtime 重启后恢复同一会话上下文。

### 微信个人号 smoke

- 通过 LangBot 的 OpenClaw/iLink adapter 扫码登录。
- 私聊入站事件能取得稳定 sender identity。
- 同一 `ChannelEnvelope → identity resolver → Agent` 契约完成一次问答回复。
- 微信 adapter 与 Telegram adapter 同时启用；两边交错发消息仍各自正确回复，任一 adapter 断线不终止另一边。
- 不把群聊、长期在线稳定性和账号策略风险算作本阶段通过条件。

### Slack 兼容

保留 `channel="slack"`、`account_id` 和 `external_user_id` 的统一模型，并确保 Agent 核心不导入 Telegram/微信 SDK；不申请凭据、不部署 callback、不做实机验收。

## 11. 安全与可观测

- 未绑定身份只允许进入确定性的注册/绑定流程；其他请求默认拒绝。
- 注册、登录和跨渠道绑定都是 Agent 之前的确定性流程；LLM 不参与账户决策。
- 注册采用唯一约束和事务保证幂等；绑定 token 高熵、短期、单次使用，仅存哈希。
- 不记录问题全文、证据全文、token、二维码或平台 secret；测试日志可记录内部用户 ID、channel、哈希化外部 ID、message correlation ID、工具名和耗时。
- 为恢复上下文而保存的消息正文只进入按 tenant/thread 隔离的应用数据表，并受明确的上下文窗口、保留/删除策略和访问控制约束；不得复制到可观测日志。
- 只开放只读 retrieval tools，限制调用轮数、每次结果数和超时。
- 跨用户查询返回统一 not found，不区分“不存在”和“属于别人”。
- 真实平台凭据只走环境变量或 LangBot 本地 secret/config，不提交仓库。

## 12. Rollout / Rollback

- 数据库迁移补充 `app_user` 生命周期、`channel_identity`、一次性绑定 token 以及 conversation thread/turn，并收紧应用层显式用户参数；原 `content_item.user_id` 结构保持不变。
- LangBot adapter 与 Agent 核心通过领域契约隔离，失败时可关闭 gateway 而不影响现有 ingest/search。
- 渠道 adapter 独立启停和健康检查；单个 adapter 回滚或断线不要求停止其他已启用渠道。
- 每个阶段独立提交；若微信 smoke 不通过，保留 Telegram E2E，并把微信标记为受阻，不放宽身份规则。
- 回滚迁移前先停止 gateway；删除新增身份、绑定和会话结构不触碰用户内容、segment 或 embedding 数据；会话记录与知识库内容分开处理。

## 13. 官方依据

- LangBot 平台：https://docs.langbot.app/en/usage/platforms/readme
- LangBot pipeline 变量：https://docs.langbot.app/en/usage/pipelines/readme
- LangBot pipeline events：https://docs.langbot.app/en/plugin/dev/apis/pipeline-events
- LangBot 微信 adapter：https://docs.langbot.app/en/usage/platforms/wechat/weixin
- Hermes sessions：https://hermes-agent.nousresearch.com/docs/user-guide/sessions/
- Hermes messaging gateway：https://hermes-agent.nousresearch.com/docs/user-guide/messaging
- OpenClaw security：https://docs.openclaw.ai/security
