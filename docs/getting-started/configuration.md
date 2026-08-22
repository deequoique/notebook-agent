# Environment configuration / 环境配置

这份文档是 Notebook Agent 环境变量的开发者入口。先选择运行场景，复制最小配置；需要调优或排障时，再查后面的[变量参考](#变量参考)。

> 安全规则：真实凭据只能进入未提交的 `.env`、进程环境或 secret manager。不要把 token、API key、DSN、HMAC secret、MiXer capability URL 粘贴到仓库、日志、截图或工单。

## 先选运行场景

| 你要做什么 | 需要的进程 | 从这里开始 |
| --- | --- | --- |
| 本地问答、库存查看、MCP Inspector/源码评测 | PostgreSQL、Notebook Agent stdio MCP | [A. 本地只读 MCP](#a-本地只读-mcp) |
| 保存 URL、重试 ingestion、更新/删除/恢复知识条目 | PostgreSQL、Redis、MinIO、Celery worker、Celery beat、Notebook Agent | [B. 完整本地 MCP](#b-完整本地-mcp) |
| 给 MiXer 或其他远程 MCP client 提供 HTTPS endpoint | 场景 A 或 B 的依赖、Streamable HTTP、TLS reverse proxy | [C. Streamable HTTP / MiXer](#c-streamable-http--mixer) |
| 接入 Telegram/微信 | gateway-server、已安装的 LangBot bridge plugin、LangBot core/adapters | [D. 可选 LangBot 渠道](#d-可选-langbot-渠道) |
| 使用私有浏览器视频资料库 | PostgreSQL、静态前端服务或 `web/dist`、web-server、TLS reverse proxy、至少一个登录渠道；保存功能另需场景 B 的依赖 | [E. Same-origin Web library](#e-same-origin-web-library) |

四个容易混淆的配置位置：

| 位置 | 放什么 | 不放什么 |
| --- | --- | --- |
| 项目根目录 `.env` | Notebook Agent、CLI、worker 使用的数据库/provider/功能配置 | 不放 LangBot bot UUID 映射；不提交 |
| stdio MCP 进程环境 `MCP_TOKEN` | 当前 stdio 进程使用的一次 bearer | 不写入 `.env.example`，不提交 |
| 已安装 LangBot plugin 目录下的私有 `.env` | bridge 的 `CHANNEL_GATEWAY_SECRET`、URL、bot UUID 映射 | 不放 Agent provider key 或数据库 DSN |
| reverse proxy / secret manager | 公网 TLS、访问日志策略、生产 secret | 不把 URL capability 记录到普通 access log |

## 一键配置与启动（推荐）

安装 Python 依赖后，部署者不需要复制完整 `.env.example`。选择一个运行模式即可：

```bash
./scripts/notebook-agent init --profile read     # 只读 HTTP MCP
./scripts/notebook-agent init --profile full     # MCP + LangBot gateway + worker + Beat
./scripts/notebook-agent init --profile langbot  # 后台任务 + LangBot gateway
./scripts/notebook-agent start
```

交互式 `init` 只询问 embedding 和 Agent provider key；本地 PostgreSQL、MinIO 和
`full` / `langbot` 所需的 LangBot gateway secret 使用安全随机值。结果写入 gitignored 的
`.env.runtime`，权限为 `0600`，且只包含 secret 和 profile 选择，不复制后面的默认值目录。
改变 profile 时使用 `init --force --profile ...`；已有生成的数据库 secret 会保留，避免使
现有 volume 失配。

非交互模式在调用 `init` 前至少设置 `ZHIPU_API_KEY`；模型凭据可以使用
`AGENT_API_KEY`，也可以继续使用 PydanticAI provider 原生环境变量。启动器按以下优先级
解析配置：

```text
调用进程环境 > 用户维护的根 .env > 生成的 .env.runtime > app.config 默认值
```

因此生产环境可以一直由 secret manager 注入变量；启动器不会把外部环境反写到文件。
`DATABASE_URL`、远程 `REDIS_URL` 或远程 `MINIO_ENDPOINT_URL` 会使对应服务被视为外部
依赖，不会由生命周期命令启动或停止。Neon runtime URL 若使用 pooler，必须另行提供
operator-only 的 `MIGRATION_DATABASE_URL`（direct host）；该值只在迁移子进程中临时覆盖
`DATABASE_URL`，不会输出到状态或日志。

```bash
./scripts/notebook-agent status
./scripts/notebook-agent logs [supervisor|mcp|worker|beat|gateway]
./scripts/notebook-agent stop
./scripts/notebook-agent restart
```

`start` 默认后台运行；`start --foreground` 适合容器或外部 service manager。`stop` 只向
状态文件中记录且命令身份匹配的 supervisor 发信号，不按进程名或端口批量终止。worker 与
Beat 对部署者是一个生命周期，但仍为独立 OS 进程，并且一个 supervisor 只创建一个 Beat。
启动器默认拒绝非 loopback 的 `MCP_HOST`；只有已经配置 TLS reverse proxy 并明确接受绑定
边界时，才同时设置 `NOTEBOOK_AGENT_ALLOW_NON_LOOPBACK=true`。`full` 和 `langbot` 的
gateway 始终只允许绑定 loopback；`full` 的 `MCP_PORT` 与
`CHANNEL_GATEWAY_PORT` 必须不同，避免一个进程的 socket 误充另一个 listener。

`.env.example` 仍是完整高级变量参考，下面的手动 profile 片段也继续受支持。

## 共同准备

```bash
python3.11 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -e '.[dev]'
cp .env.example .env
```

`.env.example` 是完整本地 superset。下面的片段用于说明最小必需值；可以删除 `.env` 中当前场景不需要的可选配置，但不要改动代码默认值来代替显式生产配置。

生成随机 secret：

```bash
openssl rand -hex 32
```

命令输出只写入本地 secret 存储或私有环境文件。

## A. 本地只读 MCP

适合自然语言问答、`list_saved_items`、`get_saved_item`、MCP Inspector 和源码评测。它不需要 Redis、MinIO、Celery worker 或 beat。

### 根 `.env` 最小配置

```dotenv
# PostgreSQL；也可以只设置一个 DATABASE_URL。
POSTGRES_USER=postgres
POSTGRES_PASSWORD=replace-with-a-local-password
POSTGRES_DB=kb
POSTGRES_HOST=localhost
POSTGRES_PORT=5432

# Query embedding。
ZHIPU_API_KEY=replace-with-zhipu-key
EMBEDDING_MODEL=embedding-3
EMBEDDING_ENDPOINT=https://open.bigmodel.cn/api/paas/v4/embeddings
EMBEDDING_DIMENSIONS=1536

# PydanticAI model provider。
AGENT_MODEL=openai:gpt-5-mini
AGENT_API_KEY=replace-with-model-key
# AGENT_BASE_URL=https://openai-compatible.example/v1

NOTEBOOK_AGENT_ENV=development
NOTEBOOK_AGENT_LOG_RETRIEVAL_CONTENT=false
```

启动数据库并升级 schema：

```bash
docker compose up -d postgres
.venv/bin/alembic upgrade head
.venv/bin/alembic current
```

创建用户和只读 grant；原始 token 只显示一次：

```bash
.venv/bin/python -m app.cli users create
.venv/bin/python -m app.cli mcp-grant issue --user-id <user-id> --scope read --label local-stdio
```

启动 stdio MCP。`MCP_TOKEN` 是进程级 bearer，不要把它加入 `.env.example`：

```bash
MCP_TOKEN='<raw-token>' \
  .venv/bin/python -m app.cli mcp-server --transport stdio
```

通过条件：官方 MCP client 完成 `initialize`，`tools/list` 只显示 `ask_notebook_agent`、`list_saved_items`、`get_saved_item`，自然语言 `tools/call` 能进入真实 Agent。

## B. 完整本地 MCP

完整 profile 在场景 A 上增加保存、更新、删除/恢复、ingestion retry。服务端只有在 PostgreSQL、Redis、MinIO、maintenance 配置和 Celery worker 均 ready 时才公布 mutation tools。

### 根 `.env` 增量

保留场景 A 的 provider 与数据库配置，再加入：

```dotenv
REDIS_HOST=localhost
REDIS_PORT=6379
REDIS_DB=0

MINIO_ROOT_USER=minioadmin
MINIO_ROOT_PASSWORD=replace-with-a-local-minio-password
MINIO_ENDPOINT_URL=http://localhost:9000
MINIO_BUCKET=kb-raw

TRASH_RETENTION_DAYS=30
TRASH_PURGE_INTERVAL_SECONDS=3600
```

保存与条目管理能力没有环境开关。首次 rollout 必须先完成依赖、migration 和
worker 检查，再启动会接收用户流量的 Notebook Agent 进程。

启动依赖和 worker：

```bash
docker compose up -d
.venv/bin/alembic upgrade head

.venv/bin/celery -A app.ingest.tasks.celery_app worker \
  --loglevel=INFO --queues=ingest,maintenance
```

另一个终端只启动一个 beat：

```bash
.venv/bin/celery -A app.ingest.tasks.celery_app beat --loglevel=INFO
```

检查 worker：

```bash
.venv/bin/celery -A app.ingest.tasks.celery_app inspect ping
.venv/bin/celery -A app.ingest.tasks.celery_app inspect active_queues
```

至少一个 worker 必须返回 `pong`，并同时监听 `ingest`、`maintenance`。来源通知由默认每
10 秒运行的 PostgreSQL delivery-ledger poller 在 `maintenance` 中处理；旧
`ingest-completion` queue 已退役，不要让 worker 监听、消费或重放它。随后签发 full grant：

```bash
.venv/bin/python -m app.cli mcp-grant issue --user-id <user-id> --scope full --label local-full
```

所有依赖通过 readiness 后，再启动 full stdio MCP：

```bash
MCP_TOKEN='<raw-token>' \
  .venv/bin/python -m app.cli mcp-server --transport stdio
```

通过条件：`tools/list` 显示 10 个 MCP tools；如果仍只有 3 个，先查 readiness，不要绕过检查或把失败 probe 当作 healthy。

## C. Streamable HTTP / MiXer

HTTP 服务使用与场景 A/B 相同的根 `.env`。grant scope 决定 read/full；full 仍受 mutation readiness 限制。

### 根 `.env` 增量

```dotenv
MCP_HOST=127.0.0.1
MCP_PORT=8000
MCP_PATH=/mcp
MCP_URL_TOKEN_MODE=false
```

启动：

```bash
.venv/bin/python -m app.cli mcp-server --transport streamable-http
```

正常客户端使用：

```text
POST https://agent.example/mcp
Authorization: Bearer <raw-token>
```

生产 endpoint 必须放在 TLS reverse proxy 后；应用仍建议绑定 loopback。不要在服务器环境中设置 `MCP_TOKEN`：HTTP token 来自每个请求，而不是一个固定全局用户。

### MiXer 只有 URL 输入框时

仅当客户端无法发送 Authorization header 时设置：

```dotenv
MCP_URL_TOKEN_MODE=true
```

MiXer URL：

```text
https://agent.example/mcp/c/<raw-token>
```

要求：

- 只接受 HTTPS；`http://` path token 会被拒绝。
- 永远不接受 `?token=<raw-token>`。
- reverse proxy、应用错误日志和 analytics 必须省略或脱敏原始 request URI。
- MiXer 和基础设施仍可能保存完整 URL；疑似暴露后立即 rotate/revoke。
- 验收必须走 `initialize -> tools/list -> tools/call`，不能只看 endpoint 可连接。

常用运维命令：

```bash
.venv/bin/python -m app.cli mcp-grant list --limit 100 --offset 0
.venv/bin/python -m app.cli mcp-grant show <grant-id>
.venv/bin/python -m app.cli mcp-grant rotate <grant-id>
.venv/bin/python -m app.cli mcp-grant revoke <grant-id>
.venv/bin/python -m app.cli mcp-grant disable <grant-id>
```

`list`、`show`、`revoke`、`disable` 不显示 raw token。rotate 会显示一个新 token，并立即使旧 token 失效。

## D. 可选 LangBot 渠道

LangBot 不是 MCP 的依赖。统一启动器的 `full` 为了提供完整应用运行时会启动 Notebook
Agent gateway；只有真正接入 Telegram/微信时，才还需要外部 LangBot core、bridge plugin
与 adapter 配置。

### Notebook Agent 根 `.env`

```dotenv
CHANNEL_GATEWAY_SECRET=replace-with-at-least-32-random-characters
CHANNEL_GATEWAY_HOST=127.0.0.1
CHANNEL_GATEWAY_PORT=8765
```

启动：

```bash
.venv/bin/python -m app.cli gateway-server
curl --fail http://127.0.0.1:8765/health
```

### 已安装 plugin 的私有 `.env`

不要写到项目根 `.env`。复制到 LangBot 实际安装目录并限制权限：

```bash
cp integrations/langbot_kb_plugin/.env.example \
  /path/to/langbot/data/plugins/notebook-agent__notebook-knowledge-agent/.env
chmod 600 \
  /path/to/langbot/data/plugins/notebook-agent__notebook-knowledge-agent/.env
```

内容：

```dotenv
CHANNEL_GATEWAY_SECRET=与-Notebook-Agent-根-env-完全相同
CHANNEL_GATEWAY_URL=http://127.0.0.1:8765/v1/messages
KB_BOT_CHANNELS={"telegram-bot-uuid":"telegram","wechat-bot-uuid":"wechat"}
```

`KB_BOT_CHANNELS` 的 key 是 LangBot bot UUID，不是 Telegram 用户 ID、微信昵称或 `AppUser.id`。bridge pipeline 还必须显式绑定 required plugin；完整步骤见[部署手册的 LangBot 章节](../deployment/README.md#7-安装-langbot-桥接可选)。

## E. Same-origin Web library

The OVHcloud/Caddy production shape, combined Web/MCP unit,
restricted GitHub approval flow, and rollback boundaries are documented in
[`ovh-caddy.md`](../deployment/production/ovh-caddy.md).

这是浏览器端视频资料库的运行入口。`web/` 已经是一个独立的私有 React 应用包，
不是需要发布到 npm 的组件库。它可以由 Python `web-server` 直接提供，也可以由独立
静态服务提供；两种模式都必须让浏览器只看到一个 public origin，并把 `/api/v1/*`
路由到 Python API。完整边界与代理示例见[前端独立部署说明](../deployment/frontend.md)。

Web 登录码必须由场景 D 中启用的 Telegram 或微信渠道批准；仅构建前端页面不能替代真实登录渠道。新增视频、失败重试和后台 ingestion 还需要场景 B 的 Redis、MinIO、worker 与 beat。

### 根 `.env` 增量

```dotenv
# 独立随机 secret；不得复用 CHANNEL_GATEWAY_SECRET。
WEB_AUTH_SECRET=replace-with-at-least-32-random-characters
# 浏览器实际看到的精确 origin；production/competition 必须使用 HTTPS，
# 且不能包含路径、query、userinfo 或末尾斜杠。
WEB_ORIGIN=https://kb.example.com
WEB_LOGIN_CHANNELS=telegram,wechat
WEB_COOKIE_SECURE=true

# 应用绑定 loopback，由同机 TLS reverse proxy 提供公网访问。
WEB_HOST=127.0.0.1
WEB_PORT=8000
WEB_SERVE_STATIC=true
WEB_STATIC_DIR=web/dist
WEB_FORWARDED_ALLOW_IPS=127.0.0.1
WEB_PUBLISH_BUDGET_SECONDS=5

```

`WEB_COOKIE_SECURE` 在本地和生产都保持 `true`，因为 session/CSRF 使用 `__Host-` cookie 契约。`WEB_FORWARDED_ALLOW_IPS` 只能列出明确受信任的反向代理地址，禁止 `*`。MCP HTTP 与 Web 是两个独立 server profile；两者同机运行时必须配置不同端口，例如保留 MCP `8000`、把 Web 改为 `8001`。

安装和构建：

```bash
corepack pnpm --dir web install --frozen-lockfile
corepack pnpm --dir web check:api
corepack pnpm --dir web test
corepack pnpm --dir web typecheck
corepack pnpm --dir web lint
corepack pnpm --dir web build
```

启动 gateway、至少一个登录渠道和 Web server。默认 bundled 模式保持
`WEB_SERVE_STATIC=true`，由该进程同时提供 API 与 `web/dist`：

```bash
.venv/bin/python -m app.cli gateway-server
.venv/bin/python -m app.cli web-server
```

TLS reverse proxy 必须把同一个 public origin 的 `/api/v1/*` 转发到 `web-server`，其余路径提供 SPA；不要把 loopback channel gateway 暴露给浏览器。最小验收：

如果前端静态服务与 Python 后端分开部署，把后端设置为
`WEB_SERVE_STATIC=false`；这时后端不读取或要求 `WEB_STATIC_DIR` 中存在构建产物，
但 proxy 仍必须从同一个 public origin 将 `/api/v1/*` 转发到它。不要改成跨 origin
API URL，也不要加入 wildcard CORS。

```text
GET /api/v1/health
GET /
GET /login
GET /library
GET /videos/<public-id>  # 直接刷新仍返回 SPA
GET /api/v1/does-not-exist  # 返回 JSON 404，而不是 SPA HTML
```

登录后先验证只读资料库；确认 Redis、MinIO、worker/beat 与 ingestion queues ready，
再开放 gateway 与 web-server 的用户流量。保存与条目管理能力始终组成运行时；要冻结
全部 Web 写入，必须停止 web-server/gateway 或在反向代理处隔离写请求。

## 变量参考

“重启”表示修改后哪些进程必须重新读取环境。数据库/对象存储自身凭据变化还可能需要单独的数据服务操作，不等于只重启应用即可完成轮换。

### PostgreSQL

| 变量 | 消费者 | 默认值/示例 | 何时需要 | Secret | 重启 |
| --- | --- | --- | --- | --- | --- |
| `DATABASE_URL` | app、CLI、worker、Alembic | 未设置；优先于 `POSTGRES_*` | 托管/外部 PostgreSQL | 是，通常含密码 | app、worker、CLI 新进程 |
| `MIGRATION_DATABASE_URL` | 一键启动器的 Alembic 子进程 | 未设置；Neon pooled runtime 时必须为 direct URL | 仅一键迁移 | 是，通常含密码 | 下次启动/迁移 |
| `POSTGRES_USER` | Compose、URL fallback | `postgres` | 本地 Compose | 否 | PostgreSQL 与所有 DB client |
| `POSTGRES_PASSWORD` | Compose、URL fallback | `changeme` 仅占位 | 本地 Compose 必填 | 是 | PostgreSQL 与所有 DB client |
| `POSTGRES_DB` | Compose、URL fallback | `kb` | 本地 Compose | 否 | PostgreSQL 与所有 DB client |
| `POSTGRES_HOST` | URL fallback | `localhost` | 未设置 `DATABASE_URL` | 否 | app、worker、CLI 新进程 |
| `POSTGRES_PORT` | Compose host port、URL fallback | `5432` | 本地/自定义端口 | 否 | PostgreSQL 与所有 DB client |

### Redis 与 broker

| 变量 | 消费者 | 默认值/示例 | 何时需要 | Secret | 重启 |
| --- | --- | --- | --- | --- | --- |
| `REDIS_URL` | app、Celery worker/beat | 未设置；优先于分项值 | full profile；远程 Redis | 可能含凭据 | app、worker、beat |
| `REDIS_HOST` | URL fallback | `localhost` | full profile | 否 | app、worker、beat |
| `REDIS_PORT` | Compose host port、URL fallback | `6379` | full profile | 否 | Redis 与 client |
| `REDIS_DB` | URL fallback | `0` | full profile | 否 | app、worker、beat |
| `BROKER_PUBLISH_TIMEOUT_SECONDS` | app submission | `5` | full profile tuning | 否 | app |
| `BROKER_PUBLISH_MAX_RETRIES` | app submission | `1` | full profile tuning | 否 | app |

本地 Compose Redis 固定使用持久卷、AOF 和 `appendfsync=always`。配置远程
`REDIS_URL` 时，托管服务必须提供等价的“broker 返回写入成功前已经持久化”保证，避免
已确认的 `ingest` task 丢失。completion notification 的 durable source 是 PostgreSQL
event + delivery ledger；旧 `ingest-completion` queue 不再生产，也不依赖 Redis snapshot。

### MinIO / S3-compatible storage

| 变量 | 消费者 | 默认值/示例 | 何时需要 | Secret | 重启 |
| --- | --- | --- | --- | --- | --- |
| `MINIO_ROOT_USER` | Compose、app、worker | `minioadmin` 仅本地示例 | full profile | 是 | MinIO、app、worker |
| `MINIO_ROOT_PASSWORD` | Compose、app、worker | `changeme12345` 仅占位 | full profile | 是 | MinIO、app、worker |
| `MINIO_ENDPOINT_URL` | app、worker | `http://localhost:9000` | full profile | 否 | app、worker |
| `MINIO_BUCKET` | app、worker | `kb-raw` | full profile | 否 | app、worker |
| `MINIO_API_PORT` | Compose only | `9000` | 本地 Compose port mapping | 否 | MinIO |
| `MINIO_CONSOLE_PORT` | Compose only | `9001` | 本地 Console port mapping | 否 | MinIO |

### Embedding、模型与 TLS

| 变量 | 消费者 | 默认值/示例 | 何时需要 | Secret | 重启 |
| --- | --- | --- | --- | --- | --- |
| `ZHIPU_API_KEY` | app query embedding、worker ingestion | 空 | 问答与 ingestion | 是 | app、worker |
| `EMBEDDING_MODEL` | app、worker | `embedding-3` | 问答与 ingestion | 否 | app、worker |
| `EMBEDDING_ENDPOINT` | app、worker | Zhipu embeddings URL | 自定义 endpoint | 否 | app、worker |
| `EMBEDDING_DIMENSIONS` | app、worker、DB contract | `1536` | 始终保持与向量列一致 | 否 | app、worker；数据迁移另议 |
| `EMBEDDING_BATCH_SIZE` | app、worker | `64` | provider tuning | 否 | app、worker |
| `AGENT_MODEL` | app/CLI | `openai:gpt-5-mini` | 自然语言问答 | 否 | app/CLI 新进程 |
| `AGENT_API_KEY` | app/CLI | 空 | provider 要求时 | 是 | app/CLI 新进程 |
| `AGENT_BASE_URL` | app/CLI | 未设置 | OpenAI-compatible endpoint | 否；URL 若含凭据则按 secret | app/CLI 新进程 |
| `TLS_CA_BUNDLE` | app、worker、LangBot patch（显式时） | 未设置 | 企业/私有 CA 或明确证书故障 | 否，但路径属环境信息 | 对应进程 |

`SSL_CERT_FILE` 和 `REQUESTS_CA_BUNDLE` 是标准库兼容 fallback，不需要复制到普通本地 `.env`。不要用 `ssl=False` 或 HTTP endpoint 绕过证书验证。

### Agent 安全预算与上下文

| 变量 | 消费者 | 默认值 | 何时需要 | Secret | 重启 |
| --- | --- | --- | --- | --- | --- |
| `AGENT_TIMEOUT_SECONDS` | Agent workflow | `45` | 可选 tuning | 否 | app |
| `AGENT_TOOL_TIMEOUT_SECONDS` | 单次 tool | `15` | 可选 tuning；小于外层 timeout | 否 | app |
| `AGENT_REQUEST_LIMIT` | primary Turn Agent | `8` | 安全上限 | 否 | app |
| `AGENT_TOOL_CALLS_LIMIT` | primary Turn Agent tools | `10` | 安全上限 | 否 | app |
| `AGENT_OUTPUT_TOKEN_LIMIT` | Turn Agent / Composer repair | `2000` | 每阶段安全上限 | 否 | app |
| `AGENT_COMPOSER_MAX_TOKENS` | Composer repair request | `1000` | 单次同证据修复 provider cap | 否 | app |
| `CONTEXT_MAX_TURNS` | conversation history | `8` | 上下文 tuning | 否 | app |
| `CONTEXT_TOKEN_BUDGET` | conversation history | `6000` | 上下文 tuning | 否 | app |
| `CHANNEL_LINK_TTL_SECONDS` | cross-channel linking | `600` | 使用绑定码时 | 否 | app |

### 回收站与维护

| 变量 | 消费者 | 默认值 | 何时需要 | Secret | 重启 |
| --- | --- | --- | --- | --- | --- |
| `TRASH_RETENTION_DAYS` | management/maintenance | `30` | item management | 否 | app、worker、beat |
| `TRASH_PURGE_INTERVAL_SECONDS` | Celery beat | `3600` | item management | 否 | beat |
| `TRASH_PURGE_BATCH_SIZE` | purge worker | `20` | item management | 否 | worker |
| `TRASH_PURGE_CLAIM_TIMEOUT_SECONDS` | purge worker | `1800` | item management | 否 | worker |
| `TRASH_PURGE_MAX_DURATION_SECONDS` | purge worker | `30` | item management | 否 | worker |
| `TRASH_PURGE_OBJECT_TIMEOUT_SECONDS` | purge object delete | `10` | item management | 否 | worker |
| `INGEST_COMPLETION_INTERVAL_SECONDS` | Celery beat | `60` | durable completion outbox repair | 否 | beat |
| `INGEST_COMPLETION_BATCH_SIZE` | maintenance worker | `20` | bounded completion repair | 否 | worker |
| `INGEST_COMPLETION_CLAIM_TIMEOUT_SECONDS` | maintenance worker | `300` | stale claim recovery | 否 | worker |
| `INGEST_COMPLETION_MAX_DURATION_SECONDS` | maintenance worker | `30` | bounded sweep wall-clock budget | 否 | worker |
| `INGEST_MAX_RAW_TRANSCRIPT_BYTES` | connector、worker | `5000000` | 单条原始字幕大小上限；对象存储前检查 | 否 | worker、同步 ingest CLI |
| `INGEST_MAX_CUES_PER_ITEM` | worker | `50000` | 单条字幕 cue 数上限；provider 调用前检查 | 否 | worker、同步 ingest CLI |
| `INGEST_MAX_TEXT_CHARS_PER_ITEM` | worker | `1000000` | 单条字幕正文字符上限 | 否 | worker、同步 ingest CLI |
| `INGEST_MAX_SEGMENTS_PER_ITEM` | worker | `5000` | 单条最终检索片段数上限 | 否 | worker、同步 ingest CLI |
| `INGEST_MAX_EMBEDDING_CHARS_PER_ITEM` | worker | `2000000` | 单条所有 embedding 输入字符的累计上限 | 否 | worker、同步 ingest CLI |
| `YOUTUBE_FETCH_TIMEOUT_SECONDS` | YouTube connector | `30` | metadata 与字幕获取的单调用总时限 | 否 | worker、同步 ingest CLI |
| `BILIBILI_FETCH_TIMEOUT_SECONDS` | Bilibili connector | `30` | 元数据与字幕解析的单调用总时限 | 否 | worker、同步 ingest CLI |
| `INGEST_NOTIFICATION_INTERVAL_SECONDS` | Celery beat | `10` | source-channel poll interval; positive | 否 | beat |
| `INGEST_NOTIFICATION_BATCH_SIZE` | maintenance worker | `20` | bounded delivery claims | 否 | worker |
| `INGEST_NOTIFICATION_CLAIM_TIMEOUT_SECONDS` | maintenance worker | `300` | stale claim recovery | 否 | worker |
| `INGEST_NOTIFICATION_MAX_DURATION_SECONDS` | maintenance worker | `8` | must be below interval | 否 | worker |
| `INGEST_NOTIFICATION_MAX_ATTEMPTS` | maintenance worker | `5` | retry ceiling before manual re-drive | 否 | worker |
| `INGEST_NOTIFICATION_RETRY_BASE_SECONDS` | maintenance worker | `5` | exponential backoff base | 否 | worker |
| `INGEST_NOTIFICATION_RETRY_MAX_SECONDS` | maintenance worker | `300` | exponential backoff cap | 否 | worker |
| `LANGBOT_OUTBOUND_BASE_URL` | maintenance worker | `http://127.0.0.1:5300` | loopback HTTP or non-loopback HTTPS | 否 | worker |
| `LANGBOT_OUTBOUND_API_KEY` | maintenance worker | 空 | dedicated LangBot API key | 是 | worker |
| `LANGBOT_OUTBOUND_TIMEOUT_SECONDS` | maintenance worker | `10` | bounded HTTP timeout | 否 | worker |

`INGEST_COMPLETION_*` variables are legacy Redis publisher compatibility
settings only; the notification poller does not read or schedule that queue.

The notification poller has no separate health endpoint or public CLI. A completed Beat tick is
observed through the privacy-safe `notification_poller_heartbeat` line in the maintenance worker's
runtime log/stdout. It reports only numeric counters, duration, and the oldest eligible delivery age;
`observability_failed=1` means that the optional backlog read was unavailable and does not mean a
delivery was changed or dropped. For failed delivery recovery, use the documented
`redrive_failed_ingest_notification(event_id)` Python hook after correcting LangBot configuration;
the next Beat tick performs the actual send. Do not re-run ingestion or consume the retired
`ingest-completion` queue.

关闭 management flag 不会关闭 deleted-content retrieval filters。

五个 `INGEST_MAX_*` 内容上限必须为正数。超过上限的条目会以安全错误码
`ingest_too_large` 终止，不会把原始字幕、provider 异常或内部路径返回给浏览器；调大前应同时评估 worker 内存、MinIO 容量和 embedding 成本。

### 日志与运行环境

| 变量 | 消费者 | 默认值 | 何时需要 | Secret | 重启 |
| --- | --- | --- | --- | --- | --- |
| `NOTEBOOK_AGENT_LOG_DIR` | app/CLI | `.runtime/logs` | 文件日志位置 | 否 | app/CLI 新进程 |
| `NOTEBOOK_AGENT_LOG_MAX_BYTES` | app/CLI | `10485760` | 日志轮转 | 否 | app/CLI 新进程 |
| `NOTEBOOK_AGENT_LOG_BACKUP_COUNT` | app/CLI | `5` | 日志轮转 | 否 | app/CLI 新进程 |
| `NOTEBOOK_AGENT_ENV` | app | `production` | `development` 或 `production` | 否 | app |
| `NOTEBOOK_AGENT_LOG_RETRIEVAL_CONTENT` | app | `false` | 仅本地且 env=development | 否；内容仍敏感 | app |

生产必须保持 `NOTEBOOK_AGENT_ENV=production` 与 `NOTEBOOK_AGENT_LOG_RETRIEVAL_CONTENT=false`。

### Same-origin Web library

| 变量 | 消费者 | 默认值/示例 | 何时需要 | Secret | 重启 |
| --- | --- | --- | --- | --- | --- |
| `WEB_AUTH_SECRET` | web-server、gateway | 无；至少 32 随机字符 | 所有 Web profile | **是** | web-server、gateway |
| `WEB_ORIGIN` | web-server | 无；`https://kb.example.com` | 所有 Web profile | 否 | web-server、reverse proxy 若 origin 改变 |
| `WEB_LOGIN_CHANNELS` | web-server、gateway | `telegram,wechat` | 限定可批准登录码的渠道 | 否 | web-server、gateway |
| `WEB_AUTH_CHALLENGE_TTL_SECONDS` / `WEB_AUTH_SESSION_TTL_SECONDS` / `WEB_AUTH_ATTEMPT_LIMIT` | Web auth | `600` / `2592000` / `5` | 登录期限与尝试上限 | 否 | web-server、gateway |
| `WEB_AUTH_RATE_WINDOW_SECONDS` / `WEB_AUTH_RATE_LIMIT_PER_REQUESTER` / `WEB_AUTH_GLOBAL_RATE_LIMIT` / `WEB_AUTH_ACTIVE_CHALLENGE_LIMIT` | Web auth | `60` / `5` / `100` / `3` | challenge 成本保险丝 | 否 | web-server |
| `WEB_AUTH_CHALLENGE_RETENTION_SECONDS` / `WEB_AUTH_SESSION_RETENTION_SECONDS` | Web auth cleanup | `86400` / `604800` | 有界清理 | 否 | web-server、gateway |
| `WEB_COOKIE_SECURE` | web-server | `true` | 所有 Web profile；不得关闭 | 否 | web-server |
| `WEB_HOST` / `WEB_PORT` | web-server | `127.0.0.1` / `8000` | 应用监听；与 MCP 同机时端口必须不同 | 否 | web-server、reverse proxy |
| `WEB_SERVE_STATIC` | web-server | `true` | `true` 由 Python 提供 `web/dist`；`false` 为同源代理后的 API-only 进程 | 否 | web-server |
| `WEB_STATIC_DIR` | web-server | `web/dist` | React production build | 否 | web-server |
| `WEB_PUBLISH_BUDGET_SECONDS` | Web batch/retry | `5` | broker 总等待预算 | 否 | web-server |
| `WEB_FORWARDED_ALLOW_IPS` | web-server | `127.0.0.1` | 可信 reverse proxy allowlist；禁止 wildcard | 否 | web-server |

`WEB_AUTH_SECRET` 与 `CHANNEL_GATEWAY_SECRET` 必须独立。应用只存储受信任 client address 的 HMAC，不把原始地址、登录码、session token 或 CSRF token 写入诊断日志。

### MCP transport

| 变量 | 消费者 | 默认值 | 何时需要 | Secret | 重启 |
| --- | --- | --- | --- | --- | --- |
| `MCP_HOST` | HTTP server | `127.0.0.1` | Streamable HTTP | 否 | MCP server |
| `NOTEBOOK_AGENT_ALLOW_NON_LOOPBACK` | 一键启动器 | `false` | 已有 TLS proxy 且明确允许 MCP 非 loopback 绑定 | 否 | 下次一键启动 |
| `MCP_PORT` | HTTP server | `8000` | Streamable HTTP | 否 | MCP server |
| `MCP_PATH` | HTTP server/proxy | `/mcp` | Streamable HTTP | 否 | MCP server、proxy |
| `MCP_URL_TOKEN_MODE` | HTTP auth middleware | `false` | URL-only client 才开启 | 否 | MCP server |
| `MCP_TOKEN` | stdio process only | 无 | 每个 stdio process | **是** | 重启该 stdio process |

HTTP 模式不设置固定 `MCP_TOKEN`；每个请求携带自己的 bearer grant。

### 可选 LangBot bridge

| 变量 | 消费者 | 默认值/示例 | 何时需要 | Secret | 重启 |
| --- | --- | --- | --- | --- | --- |
| `CHANNEL_GATEWAY_SECRET` | gateway + installed plugin | 空；至少 32 随机字符 | `full` / `langbot` profile | 是 | gateway、plugin runtime |
| `CHANNEL_GATEWAY_HOST` | gateway | `127.0.0.1` | `full` / `langbot` profile | 否 | gateway |
| `CHANNEL_GATEWAY_PORT` | gateway | `8765` | `full` / `langbot` profile | 否 | gateway、plugin URL 若改变 |
| `CHANNEL_GATEWAY_URL` | installed plugin only | `http://127.0.0.1:8765/v1/messages` | LangBot only | 否 | plugin runtime |
| `KB_BOT_CHANNELS` | installed plugin only | 无 | LangBot only | 含内部 bot UUID，按私有配置处理 | plugin runtime |

## 修改配置后的检查顺序

1. 确认改的是正确文件：根 `.env`、stdio process env、plugin private `.env` 或 proxy secret。
2. 重启表格中列出的消费者；环境变量不会自动热更新。
3. 运行 `.venv/bin/alembic current`，当前 head 应为 `f1a2b3c4d5e6`。
4. full profile 检查 Redis、MinIO、Celery `ping` 和 `active_queues`。
5. MCP 运行 `initialize -> tools/list -> tools/call`；只读应为 3 tools，ready full 应为 10 tools。
6. Web 运行 OpenAPI check、frontend tests/build，检查 `/api/v1/health`、`/login`、`/library` 和详情页直接刷新；未知 `/api/*` 必须返回 JSON 404。
7. LangBot 先检查 gateway health，再确认 required plugin 为 `initialized`，最后做真实渠道 smoke。

更完整的启动顺序、systemd、日志、备份、回滚和故障处理见[部署手册](../deployment/README.md)。
