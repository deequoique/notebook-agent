# OpenClaw TLS 与 adapter readiness 设计

## 1. Boundary

版本化改动只落在 LangBot 4.10.6 patch、Notebook Agent launcher/config、测试和部署文档。
`.runtime/langbot/patched_site` 是生成产物，可以用于 smoke，但不是修复的 source of truth。

## 2. CA resolution

当前部署已成功登录并持续 poll，因此没有显式覆盖时不解析或替换 CA：保留 upstream
`aiohttp.ClientSession()` 与 Python/OpenSSL 的原有 verified TLS 行为。不得写入
`SSL_CERT_FILE` / `REQUESTS_CA_BUNDLE`，也不得添加正常路径的 preflight GET。

仅在 adapter `tls_ca_bundle` 或 `TLS_CA_BUNDLE` 明确设置时，验证其为可读、可加载的 PEM，并将
由 `ssl.create_default_context(cafile=...)` 创建的 context 注入**该 OpenClaw client**。该 context
继续执行证书链与 hostname 校验；无效显式覆盖是 `certificate_verification_failed`，而非回退或
关闭校验。

## 3. Adapter state machine

```text
starting
  → authenticating
  → polling
  → healthy
       ↘ transient failure → degraded/retrying → polling
  → certificate/config failure → failed
  → stopped
```

`run_async()` 启动 background task 只表示 started，不能设置 healthy。第一次成功 login/poll 后才
healthy；每次 poll 成功更新 last-success。异常按类别映射稳定错误码，证书错误直接标记 failed
或让 preflight 阻止 adapter 启动。暂时错误允许有界指数退避，状态保持 degraded。

## 4. Health contract

通过现有可扩展的 adapter health/management status 暴露：adapter、state、stable error code、
last-success age、retry count/next retry 和 exception class。若现有 `/healthz` 只能表达进程级状态，
不得破坏其兼容格式；新增 detail/readiness 或管理状态，并在部署文档解释差异。

required plugin readiness 与 adapter readiness 是两个独立条件：前者阻止 bridge 未就绪时启动
channels，后者证明特定 channel 能与上游平台交换数据。

## 5. Error and privacy contract

| Error | State | Code | Retry |
| --- | --- | --- | --- |
| explicit CA missing/unreadable | failed | `certificate_verification_failed` | configuration change required |
| certificate chain/hostname failure | failed | `certificate_verification_failed` | no blind retry loop |
| timeout/reset/DNS | degraded | `upstream_unavailable` | bounded backoff |
| auth/login expiry | degraded or failed per existing protocol | existing safe auth code | preserve existing relogin behavior |

日志不包含 token、QR payload、cookie、message、nickname 或 external ID。完整 traceback 可以保留在
受控 debug 级别，但常规错误必须有单行 stable diagnostic。

## 6. Compatibility and rollback

不改变 OpenClaw API request/response、login 或 message handling。Telegram 与 required plugin 流程
应保持不变。若 health reporting 有兼容风险，可回滚展示层，但必须保留 verified CA 和“不在 TLS
永久失败时报告 healthy”的安全性质。
