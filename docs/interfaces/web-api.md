# Web API：邮箱登录、对话与跨渠道绑定

> Web API 的路径前缀固定为 `/api/v1`；`WEB_API_PREFIX` 不支持改成其他值。

```bash
export BASE='https://app.example.com'
export ORIGIN='https://app.example.com' # 必须与 .env 的 WEB_PUBLIC_ORIGIN 完全相同
export COOKIE_JAR='./web-cookies.txt'
```

除 `GET /api/v1/auth/session` 外，每个状态变更请求都必须带精确匹配的
`Origin: $ORIGIN`。每个 `POST` 还必须带 `Content-Type: application/json`；
`DELETE /api/v1/auth/session` 不需要 JSON body。所有公开错误均为固定的
`{"code":"...","message":"..."}`，不会返回 provider、邮箱存在性、验证码、token
或内部 ID。API 不提供 CORS。

成功登录后，服务端设置 `__Host-kb_session` 和 `__Host-kb_csrf` Cookie：`Secure`、
`HttpOnly`、`SameSite=Lax`、`Path=/`，没有 `Domain` 属性，`Max-Age` 为
`WEB_SESSION_TTL_SECONDS`（默认 30 天）。只有 session Cookie 为 `HttpOnly`；CSRF
Cookie 由浏览器读取并通过 `X-CSRF-Token` double-submit。下面用 curl 的 cookie jar
保存和发送它。

## 接口总览

| 方法与路径 | Cookie | 成功响应 |
| --- | --- | --- |
| `POST /api/v1/auth/challenges` | 否 | `200 {"status":"accepted"}` |
| `POST /api/v1/auth/verify` | 否 | `200`、设置 session Cookie、返回 session |
| `GET /api/v1/auth/session` | 是 | `200`、返回 session |
| `DELETE /api/v1/auth/session` | 是 + CSRF | `204`、清除当前 Cookie |
| `POST /api/v1/conversations/{conversation_id}/messages` | 是 + CSRF | `200`、返回 `AgentAnswer` |
| `POST /api/v1/conversations/{conversation_id}/messages/stream` | 是 + CSRF | `text/event-stream`、返回公开生命周期事件 |
| `POST /api/v1/conversations/{conversation_id}/reset` | 是 + CSRF | `200`、返回 `AgentAnswer` |
| `POST /api/v1/link-tokens` | 是 + CSRF | `200 {"token":"..."}` |
| `POST /api/v1/link-tokens/consume` | 是 + CSRF | `200 {"linked":true}`、清除 Cookie |

客户端不能提交 tenant、channel、account 或 external user id；它们只从 session 中解析。

## 1. 邮箱登录

### 申请验证码

```bash
curl -i -X POST "$BASE/api/v1/auth/challenges" \
  -H "Origin: $ORIGIN" \
  -H 'Content-Type: application/json' \
  --data '{"email":"person@example.com"}'
```

成功是 `200 {"status":"accepted"}`。邮箱会被 trim 并按大小写无关方式处理；
不符合邮箱格式时返回 `422 {"code":"invalid_email","message":"..."}`。邮件投递
不可用时返回 `503 {"code":"email_delivery_unavailable","message":"..."}`。

同邮箱重新申请会使此前尚未消费的验证码失效。发送限流时仍返回同一个
`{"status":"accepted"}`，不会透露邮箱或限流状态。验证码默认有效 10 分钟，单个
challenge 最多验证 5 次；实际限制由 `WEB_AUTH_*` 配置决定。

### 验证并建立 session

把邮件中的六码数字替换为实际验证码：

```bash
curl -i -c "$COOKIE_JAR" -X POST "$BASE/api/v1/auth/verify" \
  -H "Origin: $ORIGIN" \
  -H 'Content-Type: application/json' \
  --data '{"email":"person@example.com","code":"123456"}'
```

成功响应示例：

```json
{
  "authenticated": true,
  "login_channel": "email",
  "expires_at": "2026-09-08T12:00:00+00:00"
}
```

验证码、邮箱或 challenge 无效、过期、已消费、已失效或尝试次数耗尽时，均返回
`401 {"code":"verification_failed","message":"..."}`。字段缺失、长度不符合请求模型等
请求格式错误返回固定的 `422 {"code":"validation_error","message":"..."}`。

## 2. 查询和退出当前 session

```bash
curl -i -b "$COOKIE_JAR" "$BASE/api/v1/auth/session"
```

响应与登录成功时相同。没有、过期、撤销、已禁用或不匹配的 session 返回
`401 {"code":"session_invalid","message":"..."}`。

```bash
curl -i -b "$COOKIE_JAR" -c "$COOKIE_JAR" \
  -X DELETE "$BASE/api/v1/auth/session" \
  -H "Origin: $ORIGIN" \
  -H "X-CSRF-Token: <value from __Host-kb_csrf>"
```

成功返回 `204`（无响应 body），撤销当前 session 并返回清除该 Cookie 的
`Set-Cookie`。它不会退出其他设备；没有有效 session 时返回 `401`。

## 3. 对话与系统命令

`conversation_id` 由客户端选择，不能为空且长度最多 128；`message_id` 不能为空且
长度最多 128；`text` 不能为空且长度最多 16,000。请为每次新的用户提交生成新的
`message_id`；用相同 id 重试同一消息时，会返回已完成的结果。

```bash
curl -sS -b "$COOKIE_JAR" -X POST \
  "$BASE/api/v1/conversations/browser-chat-001/messages" \
  -H "Origin: $ORIGIN" \
  -H "X-CSRF-Token: <value from __Host-kb_csrf>" \
  -H 'Content-Type: application/json' \
  --data '{"message_id":"msg-001","text":"请总结我保存的视频。"}'
```

响应始终是 `AgentAnswer` 投影：

```json
{
  "status": "ok",
  "text": "...",
  "citations": [],
  "action_results": [],
  "thread_id": "server-generated-id-or-null",
  "error_code": null
}
```

`status` 为 `ok`、`not_found` 或 `failed`。`conversation_id` 无效时为
`422 {"code":"validation_error","message":"..."}`；Agent 超过
`AGENT_TIMEOUT_SECONDS` 时为 `504 {"code":"request_failed","message":"..."}`。未登录时为 401。

### 流式生命周期（SSE）

浏览器在 `AGENT_STREAMING_ENABLED=true`（默认）时使用带相同 session/CSRF
边界的 `/messages/stream`。每条 `data` 是一个公开的 JSON 事件，包含稳定的
`request_id`、`message_id` 和从 1 开始递增的 `sequence`：

```text
started → activity(retrieving|composing) → text_delta* → completed
```

异常终态为 `error` 或 `cancelled`，并带固定的安全错误摘要。`activity` 只来自受控
阶段标签；provider 内容、工具参数、原始日志和隐藏推理不会进入事件。客户端应按
`request_id` 校验并忽略重复/过期 sequence，遇到缺口或连接中断显示失败状态，不要
静默重发同一消息。将 `AGENT_STREAMING_ENABLED` 设为 `false`、`0`、`no` 或 `off`
时该路径返回 `406 streaming_disabled`，浏览器只做一次原 JSON endpoint 降级；原
`/messages` 路径始终保留给不支持 SSE 的客户端。

### 系统命令不是 HTTP endpoint

`/start`、`/whoami`、`/new` 和 `/link` 是消息 `text` 的确定性命令，不是
`/api/v1/...` 路径。Web 用户与普通问题一样，通过 messages endpoint 发送：

```bash
curl -sS -b "$COOKIE_JAR" -X POST \
  "$BASE/api/v1/conversations/browser-chat-001/messages" \
  -H "Origin: $ORIGIN" \
  -H "X-CSRF-Token: <value from __Host-kb_csrf>" \
  -H 'Content-Type: application/json' \
  --data '{"message_id":"msg-002","text":"/whoami"}'
```

| 文本 | 行为 |
| --- | --- |
| `/start` | 自动注册/确认当前身份，并返回内部用户编号。 |
| `/whoami` | 返回当前 channel identity 关联的内部用户编号。 |
| `/new` | 为当前 `conversation_id` 开启新内部 thread，不再带入旧上下文。若删除操作正在执行，响应 `AgentAnswer.status="failed"`、`error_code="delete_in_progress"`。 |
| `/link <参数>` | 创建或消费跨渠道绑定码；见“跨渠道绑定”。 |

`/start` 与 `/whoami` 不创建普通对话 thread。Telegram/WeChat 也用同样的命令文本；
命令名可带 bot 后缀（例如 `/whoami@bot`），并按大小写无关处理。

### 重置对话的专用 API

若前端无需显示 `/new` 的命令文本，可调用：

```bash
curl -sS -b "$COOKIE_JAR" -X POST \
  "$BASE/api/v1/conversations/browser-chat-001/reset" \
  -H "Origin: $ORIGIN" \
  -H "X-CSRF-Token: <value from __Host-kb_csrf>" \
  -H 'Content-Type: application/json' \
  --data '{}'
```

此 endpoint 服务端生成 message id，并以同一 `conversation_id` 发送内部 `/new`；
返回上述 `AgentAnswer`。它与消息 endpoint 一样要求 session 和 Origin，但不会调用
Agent；新 thread 的 `thread_id` 位于响应中。要建立独立的浏览器对话，请改用新的
`conversation_id`。

## 4. 跨渠道绑定

绑定码只能使用一次，默认有效 10 分钟（`CHANNEL_LINK_TTL_SECONDS` 可配置），并且限定
目标渠道。合并后渠道共享私有知识库，但各自对话历史保持独立。

### Web 发起：创建 Telegram 或 WeChat 绑定码

Web 只可创建目标为 `telegram` 或 `wechat` 的码：

```bash
curl -sS -b "$COOKIE_JAR" -X POST "$BASE/api/v1/link-tokens" \
  -H "Origin: $ORIGIN" \
  -H "X-CSRF-Token: <value from __Host-kb_csrf>" \
  -H 'Content-Type: application/json' \
  --data '{"target_channel":"telegram"}'
```

成功为 `{"token":"<raw-token>"}`。将该码发送到目标 Telegram/WeChat 对话：
`/link <raw-token>`。`target_channel` 不是 `telegram` 或 `wechat` 时返回
`422 {"code":"validation_error","message":"..."}`。

### Web 作为目标：消费为 Web 生成的码

先在 Telegram 或 WeChat 中发送 `/link web` 生成绑定码，再在已登录的 Web session 中调用：

```bash
curl -i -b "$COOKIE_JAR" -c "$COOKIE_JAR" \
  -X POST "$BASE/api/v1/link-tokens/consume" \
  -H "Origin: $ORIGIN" \
  -H "X-CSRF-Token: <value from __Host-kb_csrf>" \
  -H 'Content-Type: application/json' \
  --data '{"token":"<raw-token>"}'
```

成功为 `200 {"linked":true}`。请求所在的 Web tenant 被归并到创建该码的来源 tenant，
响应会清除浏览器 Cookie；重新通过邮箱验证后再调用受保护接口。

这两个 HTTP endpoint 在绑定失败时均返回 `409`，`code` 为以下稳定错误码之一：
`link_token_used`、`link_token_expired`、`link_channel_mismatch`、
`link_merge_busy`、`link_account_disabled`、`link_source_unbound`、
`link_merge_conflict` 或 `link_token_invalid`。`link_merge_busy` 表示目标内容仍在处理；
可在处理结束后用同一未消费的码重试。请求字段本身不符合模型时仍是 FastAPI 的 `422`
验证错误。

### Telegram / WeChat 命令用法

在 Telegram 或 WeChat 中，`/link telegram`、`/link wechat` 或 `/link web` 的目标必须与
当前渠道不同；它会生成限定目标的码。目标渠道发送 `/link <raw-token>` 消费。对于
Web 目标，消费也可通过上面的专用 HTTP endpoint 完成；Web 发起绑定则应使用
`POST /link-tokens`。命令解析失败、归并失败等会作为 messages endpoint 的
`AgentAnswer`（HTTP 仍为 200）返回；除上述归并错误外，命令还可能返回
`link_usage`、`link_channel_unsupported`、`link_channel_current` 或
`link_token_invalid`。
