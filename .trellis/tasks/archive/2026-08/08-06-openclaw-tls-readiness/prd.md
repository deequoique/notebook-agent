# 修复 OpenClaw TLS 与渠道就绪状态

## Goal

保留当前已验证的 LangBot/OpenClaw WeChat TLS 行为，并让启动、health/readiness 和管理诊断
准确反映 adapter 是否真正能持续 poll。为确有企业 CA 需求的部署提供可选、局部且 fail-closed 的
覆盖，不改变当前正常渠道的默认信任路径。

## Confirmed Facts

- 历史日志曾显示 `aiohttp.client_exceptions.ClientConnectorCertificateError`，底层错误为
  `CERTIFICATE_VERIFY_FAILED: unable to get local issuer certificate`；它不是当前故障依据。
- 用户在 2026-08-07 确认实际部署可成功登录 WeChat 并持续 poll；这是必须保留的基线。
- `openclaw_weixin.py::run_async()` 创建 `_poll_loop()` 后立即记录 adapter running；poll loop
  对异常只打印 traceback 并退避重试，因此 required-plugin readiness 和 `/healthz` 都无法表示
  微信 adapter 健康。
- poll 失败时没有微信入站消息可供回复，所以用户可见提示必须出现在启动 preflight、adapter
  health 或管理面板，而不是伪造渠道内回复。

## Requirements

### R1 — Verified TLS trust

- 未配置覆盖时保持 LangBot/aiohttp/Python 原有的 verified TLS 默认行为，不强制选择 certifi、
  不写入进程全局 CA 环境变量，也不增加额外 preflight GET。
- 可选 `tls_ca_bundle` 或 `TLS_CA_BUNDLE` 必须是可读、可加载的 PEM；仅为该 OpenClaw client
  注入显式 verified SSL context。
- 保持证书链和 hostname verification 开启。禁止 `ssl=False`、unverified context、忽略
  certificate exception 或降级 HTTP。
- CA 路径缺失、不可读或证书验证失败时必须产生稳定、脱敏的
  `certificate_verification_failed` 状态。

### R2 — Adapter readiness reflects polling

- required-plugin initialized 只表示 bridge 可用，不能单独把 WeChat adapter 标记为 healthy。
- adapter 必须区分至少 `starting`、`healthy/connected`、`degraded/retrying` 和 `failed`；只有
  登录完成且 poll 成功后才能 healthy。
- 连续证书验证失败属于明确的永久配置故障：启动 preflight 应尽早失败，或 adapter health
  必须 failed；不得无限重试同时报告 running/healthy。
- 暂时网络错误可以有界退避重试，但 health 必须同时反映 degraded 和最后安全错误码。

### R3 — Safe operator visibility

- 启动日志和可用的 health/管理状态必须显示 adapter 名称、状态、stable error code、异常类和
  重试信息，不能只输出无人消费的 traceback。
- 不得记录或返回 access token、二维码内容、微信昵称、外部用户 ID、消息正文、cookie 或完整
  provider payload。
- 显式 CA 覆盖无效时要给管理员明确修复方向；不能声称用户应重新扫码来解决 CA 问题。

### R4 — Compatibility and deployment

- 修复必须落在版本化 LangBot patch/launcher/deployment source 和自动测试中，不能只手改
  `.runtime/langbot/patched_site`。
- Telegram、required bridge plugin、现有微信登录协议、轮询请求格式和多渠道并行不得改变。
- 更新启动/部署文档，说明 CA resolution、readiness 语义、故障诊断和安全重启步骤。

## Out of Scope

- OpenClaw 登录协议、二维码重试策略、微信身份绑定或消息处理语义。
- Agent、embedding、PostgreSQL 检索链；由 `08-06-agent-retrieval-live-path` 负责。
- 关闭 TLS 验证、安装系统根证书或修改用户全局 Python 配置。
- 微信渠道内自动故障通知；poll 失败时没有可靠入站/出站通道。

## Acceptance Criteria

- [ ] 默认部署继续保持已证实的成功登录和持续 poll，不因本任务引入 certifi 选择、全局 CA 写入或
  额外 TLS 请求；可选 CA 路径可追踪但不会泄露 credential。
- [ ] 登录后连续 3 次 poll 成功，或 adapter 保持 healthy/connected 至少 2 分钟；状态证据不以
  required-plugin marker 代替 poll 成功。
- [ ] 故意配置不存在/不可读 CA 时，preflight 或 adapter health 明确返回
  `certificate_verification_failed`，进程不得继续报告 WeChat healthy。
- [ ] 暂时网络错误进入 degraded/retrying，并在恢复 poll 后自动回到 healthy；日志仅含脱敏
  diagnostics。
- [ ] fixed LangBot patch 测试、Python compile、Telegram/required-plugin 回归和全量相关测试通过。
- [ ] 部署文档包含 CA、health、重启和排障步骤；最终微信私聊 smoke 由用户人工完成。

## Notes

- Parent task: `08-06-connect-agent-embedding`.
- 本任务必须在 `08-06-agent-retrieval-live-path` 完成并审查后再启动，减少 launcher/部署文档冲突。
