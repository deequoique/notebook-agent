# Web 邮箱与 Telegram Bot 账户绑定

## Goal

让已登录 Web 的邮箱用户能够通过项目配置的 Telegram Bot，将本人控制的 Telegram 账户
安全绑定到同一个 `AppUser`，从而在 Web 与 Telegram 共用同一份私人资料库，同时继续保留
两个渠道各自的聊天历史。

## User Value

- 用户只需在 Web 和 Telegram Bot 之间传递一次性绑定码，不需要理解 tenant、identity、
  bot UUID 或内部账户 ID。
- 用户以后通过邮箱或 Telegram Bot 进入产品时，访问的是同一个私人资料库。
- 微信扫码与个人 OpenClaw 插件不会阻塞 Telegram-first MVP 的交付。

## Confirmed Facts

- 父任务 `08-09-web-email-auth-linking` 已实现邮箱 OTP、Web session、link-token 领域服务和
  双向 HTTP 契约，但明确排除前端代码。
- 后端已有 `POST /api/v1/link-tokens`：已登录 Web 用户可生成目标为 `telegram` 的一次性
  绑定码；Telegram 用户在 Bot 中发送 `/link <code>` 消费。
- 后端已有 `POST /api/v1/link-tokens/consume`：Telegram 用户可在 Bot 中发送 `/link web`
  生成绑定码，再由已登录 Web 用户粘贴消费。成功后会清除当前 Web session，用户需要重新
  通过邮箱登录进入合并后的账户。
- Telegram 已有项目认可的渠道路径：Telegram adapter -> LangBot 4.10.6 -> required bridge
  plugin -> loopback HMAC gateway -> `ChannelService`。不需要新增一套直接 webhook/bot runtime。
- bridge 通过私有配置中的 LangBot bot UUID 将事件映射到 `telegram`，并使用平台可信
  sender identity 区分不同 Telegram 用户；Bot token 不属于 Web 或 Notebook Agent 根配置。
- 用户将创建 Telegram Bot 并安全提供其 token；真实 token 不写入 Git、任务文档、日志、
  截图或测试。
- 用户确认两个绑定方向都需要支持。
- 微信目前依赖个人 OpenClaw/iLink 插件扫码登录，接入成本和稳定性不适合作为本期阻塞项；
  微信绑定延后处理。

## Requirements

### Telegram Bot Prerequisite

- 用户在 Telegram 官方 Bot 管理流程中创建专用 Bot，并只把 token 配置到 LangBot 的私有
  Telegram adapter secret 中。
- LangBot 中该 Bot 必须绑定显式启用 Notebook Agent bridge plugin 的 pipeline；不得配置
  Local Agent fallback。
- bridge plugin 私有 `.env` 必须用该 LangBot bot UUID 配置
  `KB_BOT_CHANNELS={"<bot-uuid>":"telegram"}`，并继续使用 loopback gateway 与共享 HMAC secret。
- Bot token、gateway secret 和外部 Telegram sender ID 不得进入前端、公开 API、项目根
  `.env`、日志或版本控制。

### Entry and Guidance

- 已认证用户必须能从现有账户菜单进入“绑定 Telegram”界面；未认证用户不能访问。
- 页面必须明确展示项目 Telegram Bot `@notebook_agent_bot`，并提供指向
  `https://t.me/notebook_agent_bot` 的可点击外部链接，让用户能直接打开正确的私聊入口。
- Bot 链接不得携带一次性绑定码或其他敏感参数；两个绑定方向都必须在操作说明附近提供该入口。
- 页面必须说明绑定后 Web 与 Telegram 共用私人资料库，但聊天历史仍按渠道独立。
- 页面必须说明绑定码短期有效、只能使用一次且不能转发给他人。

### Web-Initiated Binding

- Web 必须调用现有 link-token API，以 `target_channel="telegram"` 生成一次性绑定码。
- UI 必须展示可复制的完整 Telegram Bot 指令 `/link <code>`，并提示用户发送给项目配置的 Bot。
- 重新生成必须由用户主动触发；新码不会让 UI 声称旧码已被撤销，旧码状态仍以后端为准。
- 绑定码不得进入日志、分析事件、URL、持久化浏览器存储或错误报告。

### Telegram-Initiated Binding

- 页面必须指导用户先在 Telegram Bot 中发送 `/link web`，再将 Bot 返回的绑定码粘贴到 Web。
- 输入只做非空、长度等基本校验；格式、目标、过期、重放和 tenant merge 以后端为准。
- `link_merge_busy` 必须允许用户保留并使用同一个码重试。
- 消费成功后必须立即清理旧 tenant 的前端缓存，按照后端会话撤销语义返回登录页并展示一次性
  成功提示；不得继续展示旧账户数据。

### Errors, Security, and Accessibility

- 所有 mutation 必须复用 `requestJson()`，携带 same-origin credentials 与 CSRF header。
- `session_invalid` 必须进入现有全局 session 失效流程；已使用、过期、渠道错误、无效、账户
  不可用、归并冲突和归并繁忙必须映射为安全且可操作的中文提示。
- 生成、复制、提交、成功和失败状态必须支持键盘操作与屏幕阅读器；提交期间阻止重复请求。
- 页面必须 mobile-first，并在现有桌面布局下正常使用。

## Out of Scope

- 微信/OpenClaw/iLink 扫码登录、微信绑定 UI 和微信端到端验收。
- 新建独立 Telegram webhook server、绕过 LangBot bridge，或让 LangBot Local Agent 管理租户。
- 账户拆分、解绑、手工选择保留账户、迁移渠道聊天历史或恢复已消费绑定码。
- 持久化“已绑定渠道”列表、携带绑定码的 Telegram 深链、二维码、OAuth、管理员代绑和跨用户搜索。
- 在浏览器暴露 Bot token、MCP grant、tenant ID、channel identity ID 或其他内部权限凭证。

## Acceptance Criteria

- [ ] 用户创建的 Telegram Bot 能通过 LangBot required bridge 到达 `ChannelService`，并通过真实
  私聊 `/start` 或 `/whoami` smoke 证明 sender identity 与 bot UUID 映射可用。
- [ ] 已登录邮箱用户可从账户菜单进入 Telegram 绑定界面；未登录访问被认证守卫拦截。
- [ ] 两个绑定方向都能看见并打开 `@notebook_agent_bot`；链接目标固定为
  `https://t.me/notebook_agent_bot`，且 URL 中不包含绑定码。
- [ ] Web 可生成目标为 Telegram 的一次性绑定码，并展示正确、可复制的 `/link <code>` 指令、
  短期有效说明和安全提醒。
- [ ] 用户在 Telegram Bot 中发送 Web 生成的 `/link <code>` 后，Telegram identity 与邮箱
  identity 归属于同一个 `AppUser`，两个入口能访问同一私人资料库。
- [ ] 用户可在 Telegram Bot 中发送 `/link web`，再在 Web 粘贴返回码；成功后旧 Web 缓存被
  清空，用户重新邮箱登录后进入合并后的账户。
- [ ] 过期、已使用、目标渠道错误、无效、账户不可用、归并冲突和归并繁忙都有稳定 UI 反馈；
  归并繁忙不会消费码或被误报为成功。
- [ ] session 失效复用现有 401 处理，重复点击不会发起并行 mutation。
- [ ] 绑定码和 Bot token 不会进入 URL、Web Storage、日志、遥测或真实值测试快照。
- [ ] 页面在移动端和桌面端可用，并覆盖键盘、可见焦点、表单标签和 live-region。
- [ ] OpenAPI 生成类型仍是 API 类型来源；现有登录、资料库、视频详情和退出登录回归继续通过。

## Key Decisions

- MVP 收缩为邮箱与 Telegram Bot 双向绑定；微信整体延后。
- Telegram 使用现有 LangBot adapter 与 Notebook Agent bridge，不新增直接 Bot runtime。
- 用户负责创建 Bot 和保管 token；本任务负责配置边界、Web 交互和真实 Telegram smoke 清单。
- 本任务复用现有 link-token、tenant merge 和 session 撤销语义。
- Web 成功消费绑定码后立即清理并轮换私有 QueryClient，再以不含绑定码的临时路由状态回到
  登录页显示成功提示。
