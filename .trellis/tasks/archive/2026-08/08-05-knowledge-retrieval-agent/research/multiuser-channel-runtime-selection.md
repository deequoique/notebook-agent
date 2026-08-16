# 多用户与多渠道 Runtime 选型

日期：2026-08-05

## 决策约束

- 五天内交付首个可用结果。
- 多个渠道用户的会话不能互相串线。
- 微信、Slack、Telegram 必须进入同一套渠道管理模型。
- 所有已启用的渠道 adapter 必须同时运行，不能在微信和 Telegram 之间二选一。
- 长期用户记忆暂缓，但最近对话必须持久化并能在 runtime 重启后恢复。
- 复用现有 Python、PostgreSQL/pgvector 和 retrieval 层。
- 若知识按用户私有，tenant 身份必须在 LLM 之外强制执行。

## 先区分两种“多用户”

1. 多个人和同一个 bot 对话，共享同一个知识库：隔离 session 即可。
2. 每个人拥有私有知识：除 session 隔离外，检索还必须由服务端强制绑定 `user_id`。

模型传入的 `user_id`、system prompt 中的身份说明、未做 scope 的 MCP tool 都不是授权边界。私有知识场景必须先把渠道 sender identity 映射成 `AppUser.id`，再通过可信运行时上下文注入 retrieval。

**已确认决策：选择第 2 种。每个用户拥有私有知识库，必须实施服务端强制的 tenant isolation。** 未绑定的渠道身份不能使用默认用户或进入检索链路。

后续需求澄清：正常用户必须能自助创建账户，管理员不能成为注册必经入口。推荐由可信渠道身份完成首账户的原子自助创建；第二渠道通过短期单次绑定凭据关联到已有 `AppUser`。管理员仅保留封禁、纠错和恢复能力。

## 候选结论

| 候选 | 渠道覆盖 | 多用户适配 | 五天结论 |
| --- | --- | --- | --- |
| PydanticAI 单独使用 | 没有渠道 gateway，需要自己接 adapter | 一旦获得可信身份，typed dependency 很适合绑定用户 | 保留为 Agent 核心，不承担渠道层 |
| Hermes Agent | 单一 gateway 覆盖 Telegram、Slack、Weixin/WeCom 等 | per-sender session 较完整，但未确认可信 sender identity 会自动传给外部 MCP 检索服务 | 共享知识库上线快；不宜直接作为私有 tenant 边界 |
| OpenClaw | 多渠道较强，个人微信通过 Tencent iLink plugin | 官方安全文档明确更接近单一可信操作者，而非不互信的多租户安全边界 | 不作为产品 tenant 边界 |
| Agno AgentOS | Slack、Telegram 是一等 interface | `user_id` / `session_id` 模型清楚 | 无原生微信，五天内会引入最大自定义变量 |
| LangBot | Telegram、Slack、企业微信、公众号，个人微信可走 OpenClaw/iLink adapter | pipeline/event 明确暴露 channel、launcher、sender、session、conversation 标识 | 最符合当前 Python 项目和五天渠道目标 |

## 推荐架构

用 **LangBot 负责渠道接入与统一生命周期管理**；应用自己的 identity/conversation repository 负责租户映射和可恢复上下文；保留 **PydanticAI 作为窄 Agent 核心**，除非一个严格限时的 spike 证明 LangBot 内置 Local Agent + 自定义 retrieval tool 已足够。

```text
微信 adapter --\
Telegram adapter ---> LangBot / Channel Supervisor（同时运行）
future Slack -----/             |
                         可信 sender identity
                                  |
                         channel_identity 映射
                 (platform, sender_id) -> AppUser.id
                                  |
                     conversation thread/turn store
                         恢复有界最近上下文
                                  |
                         AgentRequest + history
                       user_id 由 dependency 注入
                                  |
                         只读 retrieval tools
                                  |
                         PostgreSQL / pgvector
              WHERE content_items.user_id = trusted_user_id
```

渠道层可以把用户文本交给模型，但模型不能提供 tenant identity。这样未来替换 LangBot 或 PydanticAI 时，数据隔离规则都无需改变。

## 五天验证策略（已确认渠道范围）

- Day 1：限时验证 LangBot；证明 Telegram 消息可以产生可信 `sender_id` 并抵达现有 Python retrieval 边界，同时确认多 adapter 并发运行和故障隔离方式，随后冻结版本和契约。
- Day 2：完成 channel identity mapping、服务端 `user_id` 绑定和 conversation thread/turn 存储；用两个用户做禁止跨用户检索的负向测试。
- Day 3：把 Telegram 做成标准端到端链路，补齐多轮追问、重启恢复与错误处理。
- Day 4：通过 LangBot OpenClaw adapter 做个人微信私聊扫码登录、接收消息和发送回复 smoke test；与 Telegram 同时运行并交错发消息。
- Day 5：限制工具权限、补日志与故障处理、部署说明、回归测试和缓冲。

这里不会在五天内把三个平台做成同等深度：Telegram 是完整端到端基准渠道；微信个人号只验证私聊收发并进入同一 gateway 契约；Slack 保留在统一 adapter 设计与 gateway 选型范围内，但不做本阶段实机验收。验收期间 Telegram 与微信必须并发在线，单渠道断线不得拖垮另一条链路。

## 风险与停止条件

- 个人微信依赖 Tencent iLink/OpenClaw adapter，首版优先验证私聊；群聊能力和账号策略限制需要单独确认。
- Slack 通常需要 app credentials 和可访问的 HTTPS endpoint；本阶段不做实机验收，不应让其阻塞 Telegram、微信和核心 gateway/agent 契约。
- 如果 Day 1 无法从 LangBot 获得可信 sender identity 并传给 retrieval，立即停止该路线，不允许把 `user_id` 放进模型可见文本来绕过。
- 如果 LangBot 内置 Agent 无法把可信 session identity 绑定到自定义 retrieval tool，则使用 event listener 或薄的内部请求边界调用 PydanticAI。
- 如果 LangBot 不能让 Telegram 与微信 adapter 在同一管理面下并发运行，则允许用受统一 supervisor 管理的多实例部署，但不能退化成手动切换“当前渠道”。
- 如果框架自带 session 无法稳定跨重启恢复，使用应用自己的 PostgreSQL conversation repository，不把恢复能力绑定在框架内存中。

## 官方资料

- LangBot 支持的平台：https://docs.langbot.app/en/usage/platforms/readme
- LangBot pipeline 变量与 external runner：https://docs.langbot.app/en/usage/pipelines/readme
- LangBot pipeline events：https://docs.langbot.app/en/plugin/dev/apis/pipeline-events
- LangBot 个人微信 adapter：https://docs.langbot.app/en/usage/platforms/wechat/weixin
- Hermes messaging gateway：https://hermes-agent.nousresearch.com/docs/user-guide/messaging
- Hermes sessions：https://hermes-agent.nousresearch.com/docs/user-guide/sessions/
- OpenClaw security model：https://docs.openclaw.ai/security
- OpenClaw session scoping：https://docs.openclaw.ai/concepts/session
- Agno interfaces：https://docs.agno.com/use-cases/product-agents/interfaces
