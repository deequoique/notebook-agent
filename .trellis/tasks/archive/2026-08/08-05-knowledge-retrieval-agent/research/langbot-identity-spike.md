# LangBot 身份、会话与多渠道 Spike

日期：2026-08-05

## 固定版本

- `langbot==4.10.6`
  - wheel SHA-256: `ee950fd6a687cb8c7cfe646d2b9a92cfbf09b3ddfbaf8f43ea0613905d3ffbff`
- `langbot-plugin==0.4.13`
  - wheel SHA-256: `9d45ebc7a7ee0413d6db9baa009fcbf0ad07e2e1753a6f0a27f37b8b665cd1ee`
- 参考 Hermes 源码 commit：`1be70d63548845eb8918c08ed698cda0674cf9a7`

本次只下载并解包固定 wheel 做静态源码验证，没有安装到项目环境、没有写入平台凭据，也没有连接真实 Telegram 或微信账号。

## 结论

**LangBot 渠道层通过，LangBot Local Agent 作为本项目会话/租户核心不通过。采用回退 Path B：LangBot 负责 Telegram/微信 adapter 与回复，项目应用使用 PydanticAI + PostgreSQL 管理可信租户、可恢复会话和检索工具。**

真实 Telegram 入站和微信扫码保留到人工验收；在此之前用框架无关 envelope 和 fake adapters 完成自动测试。

## 已验证能力

### 私聊 sender identity

- Telegram adapter 把 `Update.effective_chat.id` 放入 `Friend.id`；私聊中这是稳定 chat/user 标识。
- OpenClaw 微信 adapter 把 `WeixinMessage.from_user_id` 放入 `Friend.id`。
- LangBot `Query` 同时携带 `bot_uuid`、`sender_id`、`launcher_id` 和当前 adapter；插件 `EventContext` 可以读取 sender，并通过 `get_bot_uuid()` 取得 bot 实例标识。
- 因此项目可用 `(adapter/channel, bot_uuid/account, sender_id)` 作为可信外部身份键，内部 `AppUser.id` 不需要进入 prompt 或模型 tool 参数。

### 在调用模型前拦截

- 官方 pipeline event API 允许 `PersonMessageReceived` / `PersonNormalMessageReceived` handler 调用 `prevent_default()` 并直接回复。
- 这允许一个薄 LangBot plugin 在 Local Agent 运行前把可信事件交给项目 Agent service；注册、绑定、禁用和 tenant 解析失败时可以确定性拒绝，不调用 LLM/retrieval。

### 多 adapter 并发

- `PlatformManager` 会加载数据库中的多个 enabled bot。
- 每个 `RuntimeBot.run()` 都通过 LangBot task manager 启动独立 adapter task；Telegram、OpenClaw 微信等 adapter 可以同时运行。
- adapter 独立 `kill()`，可以单独停用。项目仍需补充自己的 health 状态和 fake-adapter 故障隔离测试。

### 工具权限

- Local Agent 支持 `enable-all-tools=false` 与显式 tool 名单，可以关闭通用高权限工具。
- plugin tool 调用能拿到 `query_id` 并读取 query variables；从技术上可以把已解析 tenant 放在模型不可填写的运行时变量中。

## 未通过项与停止原因

### 1. 内置 session key 不满足跨渠道隔离

`SessionManager.get_session()` 只比较 `launcher_type + launcher_id`，没有纳入 `bot_uuid`、adapter/channel 或 account id。同一数值 ID 在不同 bot/channel 上存在碰撞可能。

同时 `session_list` 只在内存中；进程重启后不能满足本项目的上下文恢复要求。可以在插件中覆盖 prompt history，但这等于绕开 LangBot session，因此不再把 Local Agent 当作会话核心。

### 2. plugin/webhook 事件没有稳定暴露平台 message ID

- Telegram 和 OpenClaw 微信的原始平台对象确实含 `message_id`。
- 但 `FriendMessage.model_dump()` 不序列化 `source_platform_object`，当前 adapter 也没有把 message ID 写入 `MessageChain.Source`。
- plugin subprocess 与内置 webhook payload 因而只能看到 sender、文本和 timestamp；内置 webhook 的 UUID是每次推送临时生成，不能替代平台幂等键。

首版桥接层将优先接受平台 message ID；当前 LangBot 缺失时使用受限的兼容幂等键，并在人工验收记录该限制。项目自己的 `ChannelEnvelope` 仍把 `message_id` 定义为必需字段，避免缺口扩散到领域层。

### 3. 默认日志与 monitoring 会记录消息正文

`MessageProcessor` 的默认 info 日志会输出 `event.message_chain`，且 pipeline monitoring 会在 plugin event 之前保存消息正文与外部身份。桥接实现因此改为在更早的 `PersonMessageReceived` 阶段 `prevent_default()`，避开后续 processor 日志；同时提供固定版本源码补丁 `integrations/langbot-4.10.6-redact-monitoring.patch`，把 monitoring 内容和外部身份替换为固定脱敏值。人工验收前必须应用并检查；未应用则隐私项不通过。

## Hermes 参考结果

Hermes 没有被选为本任务 runtime，只参考其边界设计：

- `SessionSource` 明确包含 `platform`、`chat_id`、`chat_type`、`user_id`、`thread_id`、`scope_id/profile` 和 `message_id`。
- `build_session_key()` 把 profile、platform、chat type、chat/thread 和必要的 participant scope 组合成确定性 key。
- `SessionStore` 把路由元数据与消息 transcript 持久化，而不是只保存在进程内存。

本项目采用相同原则，但使用现有 PostgreSQL 和更窄的数据模型，不引入 Hermes 的通用 Agent、shell、cron 或多 Agent 能力。

## 冻结后的实现边界

```text
LangBot Telegram / OpenClaw-Weixin adapters（同时运行）
                       |
      thin plugin: PersonMessageReceived (pre-processor)
      - trusted sender + bot UUID
      - deterministic commands (/start, /link, /new)
      - prevent_default + reply
                       |
             ChannelEnvelope adapter
                       |
       identity + conversation repositories
                       |
          PydanticAI Agent + typed deps
                       |
        tenant-scoped retrieval services
```

LangBot plugin 只做事件翻译和回复，不保存 tenant 权限，不依赖 LangBot 内存 session，不让 Local Agent 执行知识库工具。

## 官方依据

- LangBot pipeline events: https://docs.langbot.app/en/plugin/dev/apis/pipeline-events
- LangBot platform list: https://docs.langbot.app/en/usage/platforms/readme
- LangBot Telegram: https://docs.langbot.app/en/usage/platforms/telegram
- LangBot personal WeChat/OpenClaw: https://docs.langbot.app/en/usage/platforms/wechat/weixin
- PydanticAI message history: https://pydantic.dev/docs/ai/core-concepts/message-history/
- PydanticAI dependencies: https://pydantic.dev/docs/ai/core-concepts/dependencies/
- Hermes session implementation: `gateway/session.py` at commit `1be70d63548845eb8918c08ed698cda0674cf9a7`
