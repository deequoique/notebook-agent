# Notebook Agent 启动与部署手册

本文覆盖当前 P1 与首个 Web MVP 的受支持部署方式：PostgreSQL/pgvector、Redis、MinIO、
Notebook Agent gateway、同源 FastAPI + React 视频资料库，以及 LangBot 4.10.6 的
Telegram/微信 adapter 与薄桥接插件。
真实平台验收步骤另见 Trellis 任务中的 `manual-acceptance.md`。

如果当前目标只是配置 `.env`、MCP 或可选 LangBot bridge，请先阅读
[环境配置指南](../getting-started/configuration.md)。本手册保留拓扑、启动顺序、systemd、
日志、备份、回滚和故障排查等运维流程。

## 1. 部署拓扑

```text
Telegram --------\
                  LangBot core + enabled adapters
WeChat ----------/             |
                                v
                     LangBot plugin runtime
                                |
                    HTTP + HMAC over loopback
                                |
                  Notebook Agent gateway-server
                    |          |              |
                 Agent      query embedding  PostgreSQL
                  model          API          + pgvector
                                                  |
                                    durable ingest dispatch
                                                  |
                       Redis `ingest` queue -> Celery worker
                                                  |
                                 MinIO + embedding API

Bundled Web:
Browser -> HTTPS reverse proxy -> Notebook Agent web-server (127.0.0.1:8000)
                                  |-- FastAPI /api/v1
                                  |-- React production assets
                                  `-- same PostgreSQL/Redis/MinIO services

Split services, same public origin:
Browser -> HTTPS reverse proxy -> /*        -> React static service
                               `-> /api/v1  -> Notebook Agent web-server
                                               (WEB_SERVE_STATIC=false)

                 terminal dispatch -> durable PostgreSQL outbox row
                                                  |
                 delivery ledger poller on `maintenance`
                              (default every 10 seconds)
                                                  |
                       LangBot source bot + conversation
```

安全边界有一个重要限制：`gateway-server` 只允许绑定 `127.0.0.1`、`::1` 或
`localhost`，插件也只允许调用 loopback URL。因此，**Notebook Agent gateway 与
LangBot plugin runtime 必须共享网络命名空间**。

受支持的放置方式：

- 本地验收：两者都作为宿主机进程运行。
- Linux 单机：两者都作为同一宿主机上的 systemd 服务运行。
- Kubernetes：两者作为同一个 Pod 内的两个 container，Pod 内共享 loopback。
- Docker：把两者放在同一 container，或让 plugin runtime 使用
  `network_mode: service:<agent-service>` 共享 Agent container 的网络命名空间。

不支持把 plugin runtime 放在普通独立 container、再把 gateway 暴露到公网或局域网。
如果未来必须跨主机部署，应另行设计 mTLS/private network 边界，不要放宽当前
loopback 检查。

## 2. 运行要求

- Python 3.11。
- Node.js >=22.22.2 与 pnpm，用于生成 OpenAPI 类型、测试和构建 React production assets。
- Docker + Docker Compose，用于项目自带的 PostgreSQL 17/pgvector、Redis、MinIO。
- 一个监听 `ingest` queue 的兼容 Celery worker；保存功能不得由 gateway 同步抓取。
- LangBot 4.10.6 与 `langbot-plugin` 0.4.13。
- 一个 embedding provider；当前 segment 维度固定为 1536。
- 一个 PydanticAI 支持的模型 provider，或 OpenAI-compatible gateway。
- Telegram bot token；微信个人号验收使用 LangBot OpenClaw/iLink 扫码。

凭据只能进入未提交的 `.env`、systemd `EnvironmentFile`、容器 secret 或平台 secret
存储。不要把 Telegram token、微信二维码、模型 key、绑定码写入仓库或日志。

### macOS TLS CA

当前部署已经可以成功登录并持续轮询 WeChat；这是本 patch 必须保留的默认基线。没有显式 CA
覆盖时，OpenClaw 继续使用 LangBot/aiohttp/Python 原有的已验证 TLS 行为：不强制选择 certifi、
不改写 `SSL_CERT_FILE` / `REQUESTS_CA_BUNDLE`，也不额外发送 TLS preflight GET。

通常不必手工配置 macOS。仅当部署需要企业/私有 CA，或 adapter readiness 显示
`certificate_verification_failed` 时，才在对应 OpenClaw bot 的 adapter config 设置
`tls_ca_bundle`，或在启动 LangBot 的环境设置 `TLS_CA_BUNDLE`。其值必须是可读的 PEM bundle；
patch 会创建只供该 OpenClaw client 使用的 `CERT_REQUIRED` / hostname-checked TLS context。
可用下列命令定位候选 CA 文件：

```bash
.venv/bin/python -c 'import certifi; print(certifi.where())'
```

将输出路径作为 `tls_ca_bundle` 或 `TLS_CA_BUNDLE` 的值后再启动 LangBot：

```dotenv
TLS_CA_BUNDLE=/absolute/path/to/certifi/cacert.pem
```

该变量不是 secret；不要把机器上的绝对路径当作可移植默认值提交到仓库。无效的显式覆盖会以
`certificate_verification_failed` fail closed；移除或修正该覆盖后再启动。Linux 镜像若已经有
正确 CA bundle，不需要添加任何覆盖。不要使用 `ssl=False`、unverified SSL context、HTTP
endpoint 或忽略证书异常作为临时修复。

Notebook Agent gateway 与独立 Celery worker 都会在各自进程中解析可信 CA：优先使用可读的
`TLS_CA_BUNDLE`，其次是已有的 `SSL_CERT_FILE` / `REQUESTS_CA_BUNDLE`，最后是该
环境的 certifi bundle。gateway 将它用于 query embedding，worker 将它用于 ingestion
embedding；模型 provider 通过标准环境变量取得同一 bundle。两个进程必须显式获得一致配置，
不能因为 gateway 正常就假设 worker 自动继承其进程环境。
显式配置了不存在或不可读的 `TLS_CA_BUNDLE` 时，修正配置后再启动，绝不要用关闭 TLS
校验作为替代方案。

## 3. 首次安装

在项目根目录执行：

```bash
python3.11 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -e '.[dev]'
```

通用单机部署优先使用统一入口，而不是复制完整配置并分别打开多个终端：

```bash
./scripts/notebook-agent init --profile read     # 或 full / langbot
./scripts/notebook-agent start
./scripts/notebook-agent status
```

启动器生成最小 `.env.runtime`、按 profile 启动必要的本地 Compose service、执行迁移，并在
同一 supervisor 下管理 MCP/Gateway、一个双队列 Celery worker 和唯一的 Celery Beat。
`read` 只包含 MCP；`langbot` 包含 worker、Beat 与 gateway；`full` 是两者的并集，包含
worker、Beat、MCP 与 gateway。gateway 指 Notebook Agent 自带进程，不会代替外部的
LangBot core、plugin runtime 或微信 adapter。
这些组件仍为独立进程。`stop` 只停止启动器拥有且身份匹配的应用进程；持久卷和外部数据库、
Redis、MinIO 不会被停止或删除。日志位于 `.runtime/deployment/logs/`，通过
`./scripts/notebook-agent logs <component>` 查看。
迁移完成后，`full` 会直接拉起 worker、Beat、MCP 和 LangBot gateway，不会让串行的深度
依赖探测阻塞后续组件。MCP 与 gateway 两个端口都开始监听后 `start` 才成功返回；数据库、
Redis、MinIO 和 worker 的深度探测只在执行 `status` 时按需运行，不占用 supervisor，也不会
关闭仍在运行的进程。只有托管
子进程真实退出或收到明确的 `stop`/终止信号才关闭整组 runtime。
`full` 的 `MCP_PORT` 与 `CHANNEL_GATEWAY_PORT` 必须不同，启动器会在任何副作用前拒绝端口冲突。
启动器会保存不含凭据的依赖目标指纹；如果 `status` 的临时环境覆盖指向另一套数据库、
Redis、MinIO 或监听地址，它会显示 `configuration.runtime: unavailable` 并拒绝误探测。
因此使用一次性环境变量启动时，后续 `status` 也应提供相同的目标配置。

容器或 systemd 可使用 `start --foreground`，由外层 service manager 管理 supervisor。
生产 secret manager 注入值优先于 `.env` 和生成文件。若 `DATABASE_URL` 是 Neon pooled
runtime URL，还必须向启动命令提供 direct `MIGRATION_DATABASE_URL`；启动器拒绝使用 pooled
URL 执行 Alembic。主机安装、TLS proxy、systemd hardening 和 firewall 仍由平台部署流程负责。
该 lifecycle launcher 面向 Linux/macOS；Windows 环境继续使用下面的直接 Python/Celery
命令。组件日志按 `NOTEBOOK_AGENT_LOG_MAX_BYTES` 与
`NOTEBOOK_AGENT_LOG_BACKUP_COUNT` 轮转。

需要完全手工管理进程时，可以继续复制完整参考：

```bash
cp .env.example .env
cd web
corepack pnpm install --frozen-lockfile
corepack pnpm build
cd ..
```

不要从这份长手册逐项猜测 `.env`。先打开
[环境配置指南](../getting-started/configuration.md)，选择一个运行场景并复制对应的最小配置：

- 本地只读/stdio MCP：PostgreSQL、embedding、Agent provider；不需要 Redis、MinIO 或 worker。
- 完整 MCP：再加入 Redis、MinIO、Celery worker/beat；保存与管理能力没有环境开关。
- Streamable HTTP / MiXer：配置 MCP listener、TLS proxy 和每用户 grant。
- LangBot：额外配置 gateway，以及安装插件目录中的独立私有 `.env`。
- Web MVP：在完整服务基础上配置精确的 `WEB_ORIGIN`、安全 cookie、loopback `web-server`
  和同源 TLS reverse proxy；前端 production build 由 `WEB_STATIC_DIR` 提供。

前端部署优先核对以下 Web 和共享依赖变量；完整环境 profile 与变量字典仍以
[环境配置指南](../getting-started/configuration.md)为准：

| 配置 | 必需 | 说明 |
| --- | --- | --- |
| `POSTGRES_PASSWORD` | 是 | PostgreSQL 密码，不能保留示例值 |
| `MINIO_ROOT_USER` / `MINIO_ROOT_PASSWORD` | 是 | 原始文本对象存储凭据 |
| `ZHIPU_API_KEY` | 生产检索需要 | query 与 segment embedding |
| `TLS_CA_BUNDLE` | 可选 | gateway 与 Celery worker embedding 共用的可读 PEM bundle；保持证书/hostname 校验 |
| `AGENT_MODEL` | 是 | PydanticAI 模型名 |
| `AGENT_API_KEY` | 视 provider | 模型凭据 |
| `AGENT_BASE_URL` | OpenAI-compatible 时 | 以 `/v1` 结尾的兼容接口根地址 |
| `AGENT_TOOL_TIMEOUT_SECONDS` | 是 | 单次 Agent tool 上限；必须小于外层 `AGENT_TIMEOUT_SECONDS` |
| `AGENT_OUTPUT_TOKEN_LIMIT` | 是 | primary Turn Agent 与同证据 Composer repair 各自的模型输出安全上限；不要通过简单提高该值修复检索收敛 |
| `AGENT_COMPOSER_MAX_TOKENS` | 是 | Composer repair 请求真正发给 provider 的生成上限，默认 `1000`；每轮最多执行一次、没有内部 output retry，失败直接返回可信证据 fallback。DeepSeek Composer 同时关闭 thinking |
| `BROKER_PUBLISH_TIMEOUT_SECONDS` | 是 | channel 保存消息发布总预算；运行时会压到 Agent tool 上限以内 |
| `BROKER_PUBLISH_MAX_RETRIES` | 是 | broker 发布的有限 retry 次数；不影响 worker ingestion retry |
| `INGEST_MAX_ACTIVE_PER_USER` / `INGEST_MAX_ACTIVE_GLOBAL` | 是 | 同时执行的 per-user 与全局入库上限；默认 10 / 100 |
| `INGEST_DAILY_NEW_ITEM_LIMIT` / `INGEST_DAILY_NEW_ITEM_LIMIT_GLOBAL` | 是 | 每个 UTC 日新增内容的 per-user 与全局成本保险丝；默认 50 / 300 |
| `INGEST_DAILY_DISPATCH_LIMIT_PER_USER` / `INGEST_DAILY_DISPATCH_LIMIT_GLOBAL` | 是 | 每个 UTC 日创建 dispatch 的 per-user 与全局上限，失败重试也计数；默认 100 / 1000 |
| `INGEST_MAX_ITEMS_PER_USER` | 是 | 包含 archived 内容的每用户存储硬上限；默认 1000 |
| `TRASH_RETENTION_DAYS` | 是 | 回收站保留天数，默认 30；必须为正数 |
| `TRASH_PURGE_INTERVAL_SECONDS` | 是 | purge sweep 周期，默认 3600 秒 |
| `TRASH_PURGE_BATCH_SIZE` | 是 | 每轮最多 claim 100 个条目，默认 20 |
| `TRASH_PURGE_CLAIM_TIMEOUT_SECONDS` | 是 | stale purge claim 可重试的超时，默认 1800 秒 |
| `TRASH_PURGE_MAX_DURATION_SECONDS` | 是 | 单轮 purge wall-clock 上限，默认 30 秒；超出批次会释放 claim 并延后 |
| `TRASH_PURGE_OBJECT_TIMEOUT_SECONDS` | 是 | 单个 MinIO delete 的 connect/read timeout，默认 10 秒 |
| `CHANNEL_GATEWAY_SECRET` | 是 | 至少 32 字符的随机共享密钥 |
| `WEB_AUTH_SECRET` | Web 必需 | 独立的至少 32 字符随机密钥；不得复用 gateway secret |
| `WEB_ORIGIN` | Web 必需 | 浏览器看到的精确 HTTPS origin，不含末尾 `/` 或路径 |
| `WEB_COOKIE_SECURE` | Web 必需 | 保持 `true`；`__Host-` session/CSRF cookie 依赖它 |
| `WEB_LOGIN_CHANNELS` / `WEB_AUTH_*_TTL_SECONDS` / `WEB_AUTH_ATTEMPT_LIMIT` | Web 必需 | 可用登录渠道、challenge/session 有效期和登录码最大尝试次数；默认渠道为 Telegram + 微信 |
| `WEB_AUTH_RATE_*` / `WEB_AUTH_*_RETENTION_SECONDS` | Web 必需 | 登录 challenge 的请求者/全局/活动数限流和有界过期清理；保留期不得短于限流窗口 |
| `WEB_HOST` / `WEB_PORT` | Web 必需 | 默认仅绑定 `127.0.0.1:8000`，由本机 TLS proxy 转发 |
| `WEB_STATIC_DIR` | Web 必需 | React production build；默认 `web/dist` |
| `WEB_PUBLISH_BUDGET_SECONDS` | Web 必需 | 单次 Web batch/retry 的 broker 发布总预算，必须为正数 |
| `WEB_FORWARDED_ALLOW_IPS` | Web 必需 | 仅列出可信反向代理地址；禁止空值和 `*`，默认仅 `127.0.0.1` |

指南后半部分是完整变量字典，标明消费者、默认值、适用场景、secret 属性和重启范围。
根 `.env`、stdio 的进程级 `MCP_TOKEN`、LangBot plugin 私有 `.env` 是三个不同边界，
不得互相复制整份内容。

## 4. 数据服务与 migration

启动项目自带基础服务：

```bash
docker compose up -d
docker compose ps
```

等待 postgres、redis、minio 都显示 healthy，然后执行：

```bash
.venv/bin/alembic upgrade head
.venv/bin/alembic current
.venv/bin/alembic check
```

当前 head 应为 `f1a2b3c4d5e6`，`alembic check` 应显示没有新的 upgrade operation。

本地 Compose Redis 使用持久卷、AOF 和 `appendfsync=always`，使 Celery 接收到的
persistent 完成消息在确认 publish 前落盘。确认配置没有被覆盖：

```bash
docker compose exec -T redis redis-cli CONFIG GET appendonly
docker compose exec -T redis redis-cli CONFIG GET appendfsync
```

期望分别返回 `yes` 和 `always`。远程 `REDIS_URL` 必须指向提供等价“写入确认前持久化”
保证的托管实例，避免已经确认的 `ingest` broker task 丢失。completion notification 的
at-least-once source of truth 是 PostgreSQL event + delivery ledger，不依赖 Redis snapshot。

如果 Agent 自身也容器化，数据库主机应使用 Compose service 名 `postgres`；如果
Agent 运行在宿主机，使用当前示例中的 `localhost:5432`。

## 5. 模型与 embedding 配置

直接 provider 使用 PydanticAI 的模型名和该 provider 所需凭据。OpenAI-compatible
gateway 示例：

```dotenv
AGENT_MODEL=openai:your-model-name
AGENT_API_KEY=replace-me
AGENT_BASE_URL=https://gateway.example/v1
```

首版不做模型 provider 自动 fallback。更换 provider 只修改配置，不修改 Agent
prompt、tool schema、tenant dependency 或 `AgentAnswer`。

embedding 的模型和维度必须与已写入数据库的 `segment.embedding vector(1536)` 保持一致：

```dotenv
EMBEDDING_MODEL=embedding-3
EMBEDDING_DIMENSIONS=1536
EMBEDDING_BATCH_SIZE=64
```

普通知识问题在进入 BM25/pgvector 前会先生成并校验 query embedding；缺少 key、provider
故障、响应维度不符或含非有限数值时，请求会以“查询能力暂时不可用”结束，**不会**降级成
看似成功的纯词法回答。`/start`、`/whoami`、`/link`、`/new` 是确定性身份/会话命令，
不调用模型或 embedding，因此仍可用。成功但没有 tenant-owned 证据时，回复会说明知识库
没有足够证据；这与“查询能力暂时不可用”是两种不同状态，排障时不要混淆。

## 6. 启动 ingestion worker 与 Notebook Agent gateway

### Web API 与同进程 MCP

Web 登录与 Streamable HTTP MCP 共用 `mcp-server --transport streamable-http`
进程。启用前设置 `WEB_AUTH_ENABLED=true`、HTTPS `WEB_PUBLIC_ORIGIN`、至少 32 字符的
`WEB_AUTH_SECRET`、`RESEND_API_KEY` 与已验证的 `RESEND_FROM_EMAIL`；生产 Redis 不可用时
登录会 fail closed，既有 Telegram、WeChat 和 MCP grant 不受影响。

反向代理必须终止 HTTPS 并将 Cookie 原样转发，禁止为 `/api/v1` 配置 CORS。对所有 Web
状态变更请求保留原始 `Origin` header；只对列入 `WEB_TRUSTED_PROXY_HOSTS` 的代理转发
`X-Forwarded-For`。Web 对话的应用预算为 45 秒，proxy upstream read timeout 必须至少为
60 秒。访问日志不得记录 Cookie、Authorization、验证码、link token、邮件地址、消息正文
或完整 MCP capability URL。

上线时由指定操作者使用 direct Neon URL 执行 `alembic upgrade head`，再检查
`alembic current`；不要通过 pooled runtime URL、应用 build 或请求处理执行 migration。

### 6.1 readiness 与 Celery worker

保存与条目管理能力没有环境开关。在应用接收用户流量前，先确认三个依赖均 ready：

```bash
docker compose ps
docker compose exec -T redis redis-cli ping
docker compose exec -T redis redis-cli CONFIG GET appendonly
docker compose exec -T redis redis-cli CONFIG GET appendfsync
curl --fail http://127.0.0.1:9000/minio/health/ready
.venv/bin/alembic current
```

通过条件分别为 postgres/redis/minio healthy、Redis 返回 `PONG` 且本地实例报告
`appendonly=yes`、`appendfsync=always`、MinIO ready endpoint
返回成功、schema 为 `f1a2b3c4d5e6 (head)`。若 worker 不与 Redis 位于同一主机，必须在
worker 的私有环境中显式设置完整 `REDIS_URL`；不要依赖示例的 localhost 默认值。

在独立终端或受管理服务中启动消费 `ingest` 与 `maintenance` queue 的 worker：

```bash
.venv/bin/celery -A app.ingest.tasks.celery_app worker \
  --loglevel=INFO --queues=ingest,maintenance

# 只启动一个 beat 实例；它投递 source-channel notification poller 和 purge task。
.venv/bin/celery -A app.ingest.tasks.celery_app beat --loglevel=INFO
```

从同一环境检查 worker：

```bash
.venv/bin/celery -A app.ingest.tasks.celery_app inspect ping
.venv/bin/celery -A app.ingest.tasks.celery_app inspect active_queues

# Legacy Redis transport: inspect the retired completion backlog without consuming it.
docker compose exec -T redis redis-cli LLEN ingest-completion
```

至少一个目标 worker 必须返回 `pong`，且 active queue 包含 `ingest` 与 `maintenance`。
`ingest-completion` 已由旧 producer 声明为 durable queue，但当前 poller 从 PostgreSQL
直接读取 completion event；不要把该 queue 加入现有 worker，也不要恢复旧 producer。
发布 poller 前先停旧版本 producer、确认数据库事件覆盖，再按运维授权清理 backlog。
worker 必须拥有
PostgreSQL、Redis、MinIO、`ZHIPU_API_KEY` 和可信 CA 配置；不得在 gateway 请求进程内同步
执行 metadata、字幕、MinIO、chunk 或 embedding。worker 的任务参数只应是内部 dispatch ID。

### 6.1.1 source-channel notification heartbeat and recovery

每次 maintenance poller tick 完成后，worker 会写一条固定的
`notification_poller_heartbeat` diagnostic。它只包含 `heartbeat`、
`claimed/succeeded/skipped/failed/deferred`、`duration_ms`、
`oldest_eligible_backlog_age_seconds` 和必要时的数值
`observability_failed=1`；不包含 bot、conversation、用户、标题、URL、消息或异常文本。
`oldest_eligible_backlog_age_seconds=0` 表示当前没有可领取事件。该 heartbeat 不是 MCP
readiness 依赖；它用于确认 Beat + maintenance worker 仍在运行。

在配置的 runtime log 或 worker stdout/journal 中查看最近 heartbeat（路径取决于部署的
`NOTEBOOK_AGENT_LOG_DIR`）：

```bash
grep '"event":"notification_poller_heartbeat"' \
  .runtime/logs/notebook-agent-$(date +%F).log | tail -n 5
```

数据库 backlog/failed-ledger 检查必须使用受保护的 operator 连接。下面的查询只返回计数和
时间年龄，不读取目标地址或通知正文；`300` 应替换成实际的
`INGEST_NOTIFICATION_CLAIM_TIMEOUT_SECONDS`：

```bash
docker compose exec -T postgres psql \
  -U "${POSTGRES_USER:-postgres}" -d "${POSTGRES_DB:-kb}" \
  -v claim_timeout_seconds="${INGEST_NOTIFICATION_CLAIM_TIMEOUT_SECONDS:-300}" \
  -c "
SELECT count(*) AS eligible_count,
       COALESCE(EXTRACT(EPOCH FROM (clock_timestamp() - min(e.created_at)))::bigint, 0)
         AS oldest_eligible_backlog_age_seconds
FROM ingest_completion_event AS e
LEFT JOIN ingest_completion_delivery AS d
  ON d.event_id = e.id
 AND d.handler_key = 'source-channel.notification.v1'
WHERE d.id IS NULL
   OR (d.status = 'failed' AND d.next_attempt_at IS NOT NULL
       AND d.next_attempt_at <= now())
   OR (d.status = 'claimed'
       AND (d.claimed_at IS NULL OR d.claimed_at <= now()
            - (:'claim_timeout_seconds' || ' seconds')::interval));

SELECT count(*) FILTER (WHERE status = 'failed') AS failed_count,
       count(*) FILTER (WHERE status = 'failed' AND disposition = 'retry_exhausted')
         AS retry_exhausted_count
FROM ingest_completion_delivery
WHERE handler_key = 'source-channel.notification.v1';
"
```

After fixing the LangBot API key/network or target configuration, a failed row can be manually
re-driven through the implemented Python hook. There is no separate public CLI or HTTP endpoint:

```bash
EVENT_ID=123 .venv/bin/python - <<'PY'
import os

from app.ingest.notifications import redrive_failed_ingest_notification

event_id = int(os.environ["EVENT_ID"])
if not redrive_failed_ingest_notification(event_id):
    raise SystemExit("no failed source-channel delivery for event")
print("notification_redrive_queued")
PY
```

The hook resets the selected failed delivery, clears any terminal disposition, and makes it eligible
for the next Beat tick; it does not re-run ingestion and does not send an HTTP request inline. Confirm the next
heartbeat and failed-ledger count before considering the incident resolved. If the poller is paused,
stop only its Beat entry and preserve PostgreSQL event/ledger rows; do not drain or replay the retired
`ingest-completion` queue as a notification workaround.

### 6.2 gateway

前台启动，适合首次排错：

```bash
.venv/bin/python -m app.cli gateway-server
```

### 6.3 同源 Web 视频资料库

`web/` 是一个可独立构建和部署的私有前端应用包，不是 npm 组件库。详细的 bundled、
split-service 和回滚契约见[前端独立部署说明](frontend.md)。

Bundled 模式下，Web 前端必须先完成 production build，并与 FastAPI 从同一个 browser origin 提供：

```bash
cd web
corepack pnpm install --frozen-lockfile
corepack pnpm check:api
corepack pnpm test
corepack pnpm typecheck
corepack pnpm lint
corepack pnpm build
cd ..
.venv/bin/python -m app.cli web-server
```

前后端由不同服务运行时，后端设置 `WEB_SERVE_STATIC=false`，静态服务提供 `web/dist`，
公网 proxy 仍按同一域名把 `/api/v1/*` 转发到后端。此模式不要求后端镜像包含
`web/dist`。不要配置跨 origin API、wildcard CORS、domain cookie 或浏览器 token fallback。

`web-server` 默认只监听 `127.0.0.1:8000`。生产只把公网 `443` 反向代理到该端口；
`8765` channel gateway 继续保持 loopback-only，绝不能一起暴露。TLS proxy 必须保留原始
`Host`/`Origin`，并只从 `WEB_FORWARDED_ALLOW_IPS` 中列出的本机代理接受 forwarded headers。

`WEB_ORIGIN` 必须与浏览器地址完全一致，例如 `https://kb.example.com`。不要配置 wildcard
CORS，也不要把 React assets 放到另一个 origin。Session cookie 是 `Secure`、`HttpOnly`、
`SameSite=Strict` 的 `__Host-kb_session`；CSRF cookie 是同样 Secure/SameSite 的
`__Host-kb_csrf`，前端只把它复制到 `X-CSRF-Token`，两者都不进入 Web Storage。
登录 challenge 只保存受信任客户端地址的 HMAC，不保存原始地址；创建接口同时执行
per-requester、全局和活动 challenge 上限，命中时返回统一 `rate_limited`，不会暴露是哪一条
容量规则。`WEB_FORWARDED_ALLOW_IPS` 必须逐项列出本机受信任代理，不得使用 `*`。
入库的 per-user 与全局配额在 PostgreSQL 中先取得全局 admission lock、再锁 tenant 行，
commit 后才调用 broker。重复请求和已有内容不消耗新增配额；`:retry` 也受 active 配额约束。
每日 dispatch 上限同时约束新内容和失败重试，防止仅靠更换 idempotency key 重复消耗 worker。
当前渠道身份仍可自动注册新 tenant，因此这些全局上限是必要的成本保险丝，不应把系统描述成
invite-only。
应用关闭了 Uvicorn 默认 access log，避免资料库搜索词随 query string 写入日志。TLS
反向代理也必须使用不含 query string 的访问日志格式，并且不得记录 Cookie、Authorization、
CSRF header、请求体、URL 列表或 `why_saved`。

启动后检查：

```bash
curl -fsS https://kb.example.com/api/v1/health
curl -fsS https://kb.example.com/api/v1/capabilities
```

`/login`、`/library` 和 `/videos/<public-id>` 刷新必须返回 React shell；任意不存在的
`/api/...` 必须仍返回 JSON 404，不能被 SPA fallback 吞掉。

另一个终端检查：

```bash
curl --fail http://127.0.0.1:8765/health
```

预期返回 `{"status": "ok"}`。该 endpoint 是进程存活检查，不会主动调用模型或
数据库；数据库检查使用 `alembic current`，真实模型/检索检查使用后面的 CLI smoke。

保存与条目管理工具始终组成 gateway。只有 migration、worker、Redis、MinIO 和 gateway
均 ready 后，才允许上游向 gateway 导入用户流量；不要重置 LangBot channel identity、
微信登录或已有 content。

生产环境不要把 8765 端口映射到公网。保持：

```dotenv
CHANNEL_GATEWAY_HOST=127.0.0.1
CHANNEL_GATEWAY_PORT=8765
```

## 6.5 MCP 核心入口（无需 LangBot）

MCP 是 Notebook Agent 的核心评测入口，LangBot 只负责可选的个人微信/Telegram
渠道适配。安装 `.[dev]` 会固定官方 Python SDK `mcp==2.0.0`；`app/` 不导入
LangBot SDK，删除 `integrations/` 不影响 MCP 或 CLI。

### 6.5.1 stdio 本地验收

stdio 的 stdout 只能包含 MCP 协议字节，应用诊断会写入 stderr 和受限的私有日志。
先为目标用户签发 grant，再把 raw token 只传给该 stdio 进程：

```bash
.venv/bin/python -m app.cli mcp-grant issue --user-id 12 --scope read --label local-stdio
MCP_TOKEN='<raw-token>' \
  .venv/bin/python -m app.cli mcp-server --transport stdio
```

用官方 MCP client/Inspector 初始化后执行 `tools/list`，再调用
`ask_notebook_agent` 的自然语言问题。仅调用 `tools/list` 不能证明 Notebook
Agent 的模型执行。

### 6.5.2 Streamable HTTP 与授权

默认配置是 `MCP_HOST=127.0.0.1`、`MCP_PORT=8000`、`MCP_PATH=/mcp`。公开部署必须
使用 HTTPS 和反向代理，优先传 `Authorization: Bearer <token>`：

```bash
.venv/bin/python -m app.cli mcp-grant issue --user-id 12 --scope full --label mixer
.venv/bin/python -m app.cli mcp-server --transport streamable-http
```

grant 的原始 token 只在 issue/rotate 时显示；数据库只存哈希，`list`/`show` 不会
显示 bearer。每个 grant 映射一个稳定 MCP principal、一个 `AppUser` 和 `read` 或
`full` scope；服务端每次请求都检查禁用、撤销和可选过期。浏览器/demo 使用
`read` grant，且应为每个浏览器会话生成新的高熵 `conversation_id`。

MiXer 等 URL-only 客户端只有在显式设置 `MCP_URL_TOKEN_MODE=true` 后才能使用
`/mcp/c/<opaque-token>`。仅接受 HTTPS 动态路径，随后内部改写为 `/mcp`；不接受
`?token=`，应用和代理访问日志必须省略或 redaction 完整 request URI。竞赛结束或
疑似泄露后立即 rotate/revoke。

MCP 进程可用性不等于数据库、模型、embedding、Redis、MinIO、Celery 或 maintenance
readiness。read-only 问答可以不启动 Redis/MinIO/worker；启用 full 的保存、重试和
回收站操作前，必须检查相应依赖和 migration `f1a2b3c4d5e6 (head)`。

## 7. 安装 LangBot 桥接（可选）

### 7.1 应用启动就绪与隐私补丁

在固定版本 LangBot 4.10.6 源码根目录执行：

```bash
patch --dry-run -p1 < /absolute/path/to/notebook-agent/integrations/langbot-4.10.6-redact-monitoring.patch
patch -p1 < /absolute/path/to/notebook-agent/integrations/langbot-4.10.6-redact-monitoring.patch
```

文件名因兼容性保留 `redact-monitoring`，但补丁同时实现三件事：

1. monitoring、adapter 日志、MessageProcessor 与 plugin diagnostic 不复制私聊正文、
   昵称、外部 sender ID 或 message preview；
2. 当 `plugin.required_plugins` 非空时，LangBot 只会在这些插件的 runtime 状态全部为
   `initialized` 后启动任何 enabled adapter；
3. 对显式绑定 required plugin 的 pipeline，每条消息都必须确认该插件处理了早期 event
   并调用 `prevent_default()`。runtime 断线、插件缺席或未阻止默认处理时，LangBot
   只返回固定“渠道暂时不可用”提示，绝不回退到 Local Agent。
4. OpenClaw/iLink 的普通登录和长轮询保持 upstream 已验证 TLS 行为；只有显式设置
   `tls_ca_bundle` 或 `TLS_CA_BUNDLE` 时才为该 client 创建可追踪的 verified CA context。
   无论使用哪条路径，都只有成功完成 `getUpdates` poll 才将微信 adapter 标为 `healthy`。

把下列配置写入 LangBot 的 `data/config.yaml`。本项目必须明确配置 bridge plugin；留空
会保留上游兼容行为，但不会得到本部署的启动保护。

```yaml
plugin:
  required_plugins:
    - notebook-agent/notebook-knowledge-agent
  required_plugins_ready_timeout_seconds: 30
```

deadline 是最长等待时间，不是启动延迟：状态首次达到 `initialized` 会立即开放全部
adapter；30 秒仍未达到时 LangBot 退出且 adapter 不会启动。由 systemd/Docker 重启或
人工排障，**不要添加固定 `sleep`，也不要配置 LangBot 内置模型作为 fallback**。

环境变量部署可使用等价的 `PLUGIN__REQUIRED_PLUGINS`（逗号分隔）和
`PLUGIN__REQUIRED_PLUGINS_READY_TIMEOUT_SECONDS`。变更后必须重启 LangBot；不要编辑
被忽略的 `.runtime/langbot/patched_site` 作为长期配置来源。

补丁只支持 wheel SHA-256 为
`ee950fd6a687cb8c7cfe646d2b9a92cfbf09b3ddfbaf8f43ea0613905d3ffbff` 的
LangBot 4.10.6。升级版本时重新取得上游 source、先做 `--dry-run`，再重新审查全部 hunk。
未应用补丁时不得通过隐私或渠道可用性验收。

启动时必须使用应用了该补丁的同一份 Python package。仅仅在另一个源码目录执行
`patch`，不会改变已安装的 `langbot` 命令；这种情况下旧版的 readiness gate 可能仍是
空实现，而 `/healthz` 仍会返回 200。源码树或本机生成的 patched package 需要显式放到
运行时搜索路径，例如：

```bash
PYTHONPATH=/absolute/path/to/patched_site \
  /absolute/path/to/langbot-venv/bin/langbot
```

启动前可用下面的只读检查确认解释器加载了目标文件（不要打印环境变量或 secret）：

```bash
PYTHONPATH=/absolute/path/to/patched_site \
  /absolute/path/to/langbot-venv/bin/python -c \
  'import inspect; from langbot.pkg.plugin.connector import PluginRuntimeConnector; \
print(inspect.getsourcefile(PluginRuntimeConnector))'
```

输出必须指向已应用补丁的 `langbot/pkg/plugin/connector.py`；之后仍要以
`Required plugins initialized; message adapters may start.` 作为 readiness 判据。

### 7.2 安装 plugin

把整个目录复制或挂载到 LangBot plugin workspace：

```text
integrations/langbot_kb_plugin/
```

将 `.env.example` 复制到**LangBot 已安装插件目录**的 `.env`，而不是项目根目录的
`.env`。LangBot plugin worker 在非 Windows 环境从该安装目录加载其私有 `.env`：

```bash
cp /absolute/path/to/notebook-agent/integrations/langbot_kb_plugin/.env.example \
  /absolute/path/to/langbot/data/plugins/notebook-agent__notebook-knowledge-agent/.env
chmod 600 /absolute/path/to/langbot/data/plugins/notebook-agent__notebook-knowledge-agent/.env
```

plugin runtime 私有配置：

```dotenv
CHANNEL_GATEWAY_SECRET=与Notebook-Agent完全相同的值
CHANNEL_GATEWAY_URL=http://127.0.0.1:8765/v1/messages
KB_BOT_CHANNELS={"telegram-bot-uuid":"telegram","wechat-bot-uuid":"wechat"}
```

`KB_BOT_CHANNELS` 必须列出每个启用 bot 的 UUID；没有默认值，未映射的 bot 会
fail closed。UUID 来自 LangBot bot 配置，不是 Telegram 用户 ID 或微信昵称。
同一个私有 `.env` 可以同时映射 Telegram、微信和后续其他 adapter 的多个 bot UUID。
不要在 LangBot core 的日志、systemd unit、截图或工单中粘贴该文件内容。

### 7.3 配置并同时启用渠道

在 LangBot 中：

1. 新建并启用 Telegram bot adapter。
2. 新建并启用 OpenClaw/iLink 微信 adapter，完成个人号扫码。
3. 两个 bot 都绑定安装了 bridge plugin 的 pipeline。
4. 两个 adapter 保持 enabled；不要配置“当前渠道”开关。

bridge pipeline 必须关闭“启用全部插件”，并显式只绑定
`notebook-agent/notebook-knowledge-agent`。这正是运行时 fail-closed gate 的适用范围；
不要把 required bridge 仅靠全局自动发现绑定。

### 7.4 OpenClaw TLS 与 adapter readiness

`/healthz` 保持进程级兼容语义：它只说明 LangBot HTTP process 可响应，不能证明微信
上游仍可 poll。应用补丁后，登录管理面可通过受认证接口查看 adapter 级状态：

```bash
curl --fail -H 'Authorization: Bearer <LangBot-admin-token>' \
  http://127.0.0.1:<LangBot API port>/api/v1/platform/adapters/readiness
```

返回项只包含 adapter 名称、`state`、稳定 `error_code`、`exception_class`、最近一次成功 poll
的年龄、retry 次数/下次 retry，以及 CA bundle 路径；不会包含微信 token、二维码、昵称、外部
用户 ID、消息正文、cookie 或 provider payload。不同版本的 LangBot 管理认证 header 可能不同，
请使用该部署既有的管理员认证方式，且不要把 token 贴入终端历史、日志或工单。

状态解释：

| state | 含义与操作 |
| --- | --- |
| `starting` / `authenticating` | adapter 已开始但尚无成功 poll；它不是 healthy。等待登录或检查启动日志。 |
| `healthy` | 至少一次 `getUpdates` 成功；随后每次成功 poll 刷新 success age。验收需要连续三次成功 poll 或持续两分钟 healthy。 |
| `degraded` | DNS、timeout、reset 或临时 upstream 故障；adapter 以 1–10 秒的有界指数退避重试，状态包含安全的错误类别与 retry metadata。 |
| `failed` + `certificate_verification_failed` | 显式 CA 覆盖不可读/无效，或真实证书链/hostname 无法验证；不会无限 retry。修正或移除显式覆盖后安全重启 LangBot。 |
| `failed` + `authentication_failed` | 维持既有登录失败语义；检查受保护的登录管理流程，不要在健康接口或日志中复制二维码/token。 |
| `stopped` | 已按正常停止流程终止。 |

正常路径没有 CA preflight；真实 poll 的非证书网络失败会显示 `degraded/upstream_unavailable`，
再以 verified TLS 重试。若状态是 `certificate_verification_failed`，重新扫码不会修复根证书
问题。确认或移除显式 CA bundle 后只重启 LangBot core/adapter，不要重置 Telegram token、
微信登录、bot UUID、用户绑定、conversation history 或内容数据。

平台配置参考：

- Telegram：https://docs.langbot.app/en/usage/platforms/telegram
- 微信个人号：https://docs.langbot.app/en/usage/platforms/wechat/weixin

## 8. 完整启动与停止顺序

首次开启自然语言保存或升级：

1. PostgreSQL、Redis、MinIO。
2. 保持 gateway、web-server 与上游流量入口停止，备份后执行 `alembic upgrade head` 并确认 current/check。
3. 部署并启动兼容 Celery worker，确认它监听 `ingest,maintenance`，并启动单一 beat 实例；CA/Redis/MinIO 均 ready。
4. 部署并启动 Notebook Agent `gateway-server`，检查 loopback health 与只读检索 smoke。
5. 构建并启动 `web-server`，从精确 HTTPS origin 检查登录、资料库和详情页。
6. 确认已应用 TLS/readiness patch；保留已证实的默认登录/轮询路径。仅有企业 CA 需求或明确
   诊断时，在 LangBot 进程中设置 `tls_ca_bundle` 或 `TLS_CA_BUNDLE`。不要禁用 TLS verification。
7. Docker/WebSocket 模式先启动 plugin runtime；stdio 模式由 LangBot core 启动它。
8. 启动 LangBot core。patched core 会先连接 runtime、检查 bridge plugin 为
   `initialized`，**之后**才启动每个 enabled adapter；不要手工改变这个顺序。
9. 检查 readiness；自动化验证通过后，Telegram 完整 E2E 与微信保存 smoke 仍由人工执行。

readiness 检查不使用“等待 N 秒”。在 LangBot 进程日志中确认
`Required plugins initialized; message adapters may start.`，再确认：

```bash
curl --fail http://127.0.0.1:<LangBot API port>/healthz
```

HTTP `healthz` 只有在 application 已通过启动 gate 并创建 HTTP task 后才会返回成功，不能
替代微信 poll 检查。接着查询 `/api/v1/platform/adapters/readiness`：OpenClaw 必须是
`healthy`，并满足连续三次成功 poll 或保持两分钟 healthy；`starting`、`degraded` 或 `failed`
都不能作为微信可用证据。管理面板的 plugin 详情还必须显示 bridge status 为 `initialized`；
若 deadline 超时，先检查 plugin package、其私有 `.env` 权限、gateway health 和 CA，再重启
LangBot。不要先启动 adapter 试图“等它自己恢复”。

日常停止采用相反顺序：

1. 停 LangBot adapters/core，停止接收新消息。
2. 停 plugin runtime。
3. 停止或在反向代理隔离 Notebook Agent gateway 与 `web-server`，阻止渠道保存、Agent
   管理 tools 以及所有 Web 写入。保留 maintenance worker/beat 以继续安全清理，或按需暂停 beat。
4. 让 active ingestion 完成；需要立即止损时再停止 Celery worker。
5. 确认没有 ingestion 工作后，再按需停止 Redis、MinIO 与 PostgreSQL。

不要在消息处理中直接执行 migration downgrade。生产 downgrade 前必须完成 PostgreSQL/MinIO
备份，停止 gateway、web-server 与 Celery beat，确认所有回收站条目已经逐项恢复或
完成对象删除 + 数据库 purge，并执行普通库存与 semantic/BM25 检索 smoke，确认没有软删除内容
复活。migration 会在发现任何 `deleted_at IS NOT NULL` 行时主动拒绝 downgrade，不会通过删列使
回收站内容重新可见。

## 9. Linux systemd 示例

下面的内联示例管理 Notebook Agent gateway；LangBot 按其部署方式单独管理。把路径和用户
改成实际值，并把 secret 文件权限设为 `0600`。独立静态前端 + API-only Web 的可安装
Nginx 与 systemd 模板位于 `deploy/nginx/` 和 `deploy/systemd/notebook-agent-web*.service`，
完整构建、原子切换、TLS、smoke 与回滚步骤见 [Frontend deployment](frontend.md)。

```ini
[Unit]
Description=Notebook private knowledge Agent gateway
After=network-online.target docker.service
Wants=network-online.target

[Service]
Type=simple
User=notebook-agent
Group=notebook-agent
WorkingDirectory=/opt/notebook-agent
EnvironmentFile=/etc/notebook-agent/notebook-agent.env
Environment=PYTHONDONTWRITEBYTECODE=1
Environment=NOTEBOOK_AGENT_LOG_DIR=/var/log/notebook-agent
Environment=NOTEBOOK_AGENT_ENV=production
Environment=NOTEBOOK_AGENT_LOG_RETRIEVAL_CONTENT=false
ExecStart=/opt/notebook-agent/.venv/bin/python -m app.cli gateway-server
Restart=on-failure
RestartSec=5
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
LogsDirectory=notebook-agent
LogsDirectoryMode=0750
UMask=0027

[Install]
WantedBy=multi-user.target
```

安装后：

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now notebook-agent-gateway
sudo systemctl status notebook-agent-gateway
```

systemd 日志中不应出现问题正文、证据全文、平台 token 或外部身份映射。

## 9.1 安全诊断日志

### 路径与所有权

三个进程各自管理日志，不共同写一个文件：

| 组件 | 本地路径或入口 | Linux 服务器路径或入口 | 说明 |
| --- | --- | --- | --- |
| LangBot core | `<LangBot data>/logs/langbot-YYYY-MM-DD.log` 与启动终端 | `data/logs/langbot-YYYY-MM-DD.log` 与 LangBot 自身 stdout | 由 LangBot 自己写入和轮转；Notebook Agent 不写这个文件。当前仓库的本地运行目录通常是 `.runtime/langbot/data/logs/`。 |
| Notebook Agent gateway / CLI | `.runtime/logs/notebook-agent-YYYY-MM-DD.log` 与启动终端 | `/var/log/notebook-agent/notebook-agent-YYYY-MM-DD.log` 与 systemd journal | 同一条结构化 INFO 事件同时写 stdout 和每日文件。`NOTEBOOK_AGENT_LOG_DIR` 只控制 Notebook Agent 文件目录。 |
| LangBot bridge plugin | LangBot plugin 详情页中的有界 stderr 日志 | 同左 | bridge 是独立子进程，**没有 bridge 日志文件**，也不把事件复制进 LangBot core 每日文件。 |

在 POSIX 主机上，Notebook Agent 文件目录权限为 `0750`，文件权限为 `0640`；Windows
使用部署账户继承的 NTFS ACL，不把 POSIX mode 当作有效权限证明。Windows 上线前必须用
`icacls <NOTEBOOK_AGENT_LOG_DIR>` 确认只有部署账户、`SYSTEM` 和本机管理员可读取；未完成这项检查时，
保持 `NOTEBOOK_AGENT_LOG_RETRIEVAL_CONTENT=false`，并把该目录视为可能泄露检索内容的风险项。
文件按日期和大小轮转，默认单文件上限
10 MiB、保留 5 个备份，可通过 `NOTEBOOK_AGENT_LOG_MAX_BYTES` 与
`NOTEBOOK_AGENT_LOG_BACKUP_COUNT` 调整。文件初始化或后续写入失败时，gateway 继续运行，并在
stdout/journal 输出一次 `file_logging_unavailable`；成功启用时可看到 `runtime_logging_enabled`。

### 启动模式

生产和普通本地运行使用安全默认值：

```dotenv
NOTEBOOK_AGENT_ENV=production
NOTEBOOK_AGENT_LOG_RETRIEVAL_CONTENT=false
```

只有本地排查检索链路时，才同时显式设置下面两个值并重启 gateway：

```bash
NOTEBOOK_AGENT_ENV=development \
NOTEBOOK_AGENT_LOG_RETRIEVAL_CONTENT=true \
.venv/bin/python -m app.cli gateway-server
```

不能根据日志目录、TTY、hostname 或是否在服务器上自动推断开发环境。`production + true` 或未知的
`NOTEBOOK_AGENT_ENV` 会在启动配置校验时失败，而不是静默开启或降级。排查完成后恢复
`production + false` 并重启；systemd 示例必须始终保持生产配置。

开发开关只豁免 Notebook Agent 的检索详情，包括 query、limit/radius、item/segment ID、标题、
作者/描述、URL、score、excerpt 与 start/anchor。历史、完整 prompt、模型输出、action/save payload、
embedding 向量、外部身份、provider payload、token/DSN/secret 和异常消息在开发模式中仍然禁止。
开发检索详情只进入 Notebook Agent 的 stdout 与 `.runtime/logs/`，不会发送给 bridge、LangBot core、
模型 provider、`AgentAnswer` 或 conversation store。

### 查看日志

本地跟踪 Notebook Agent 当日日志：

```bash
tail -f ".runtime/logs/notebook-agent-$(date +%F).log"
```

服务器同时查看 journal 与文件：

```bash
journalctl -u notebook-agent-gateway -f
sudo tail -f "/var/log/notebook-agent/notebook-agent-$(date +%F).log"
```

LangBot core 使用自己的日志文件；bridge 事件在管理界面的 plugin 详情/日志页查看：

```bash
tail -f "<LangBot data>/logs/langbot-$(date +%F).log"
```

不要为 bridge 创建 `bridge-*.log`，也不要让 Notebook Agent 写入 LangBot 的 `data/logs/`。

### 按请求联查

bridge 首次转发会生成随机 32 位 `trace_id`，gateway 再生成独立的 `request_id`。先在 bridge plugin
stderr 找到 `trace_id`，再用它查询 Notebook Agent；进入 gateway 后也可用 `request_id` 聚合该请求的
model、tool、embedding、retrieval、validation 与最终响应阶段：

```bash
rg '"trace_id":"<32位 trace ID>"' .runtime/logs/notebook-agent-*.log*
rg '"request_id":"<32位 request ID>"' .runtime/logs/notebook-agent-*.log*

journalctl -u notebook-agent-gateway | rg '"trace_id":"<32位 trace ID>"'
```

`trace_id` 只用于日志关联，不参与身份、tenant、授权、幂等或消息去重。日志中的普通安全事件仅包含
阶段、内部 request/tenant ID、trace ID、固定 route/tool/limit/error 枚举、计数、异常类和耗时；
生产日志不包含问题正文、检索词、证据、内部内容 ID 或 URL。

## 10. 首次启动 smoke

先验证本地用户与真实模型，不经过消息平台：

```bash
.venv/bin/python -m app.cli users create
.venv/bin/python -m app.cli ingest --user-id <返回的编号> 'https://youtu.be/...'
.venv/bin/python -m app.cli ask --user-id <编号> --thread deploy-smoke '询问视频中的独有概念'
```

答案必须含真实标题、证据片段和时间戳。然后在 Telegram 发送 `/start` 或
`/whoami`，再按人工验收清单测试两用户隔离、上下文重启恢复和微信 smoke。

## 11. 健康检查

| 检查 | 命令或入口 | 通过条件 |
| --- | --- | --- |
| 基础服务 | `docker compose ps` | postgres/redis/minio healthy |
| schema | `.venv/bin/alembic current` | 当前 revision 为 head |
| migration drift | `.venv/bin/alembic check` | 无新 upgrade operation |
| Redis | `redis-cli ping` | `PONG` |
| MinIO | `GET /minio/health/ready` | HTTP 200 |
| ingestion worker | Celery `inspect ping` + `active_queues` | worker pong 且监听 `ingest`、`maintenance` |
| Agent 进程 | `GET http://127.0.0.1:8765/health` | HTTP 200、status ok |
| LangBot process health | `GET /healthz` | required bridge 为 `initialized` 后 API 才可用；不代表微信 poll healthy |
| OpenClaw adapter readiness | `GET /api/v1/platform/adapters/readiness`（管理员认证） | `state=healthy`，连续三次成功 poll 或持续两分钟；无 `certificate_verification_failed` |
| bridge runtime | LangBot plugin detail | `notebook-agent/notebook-knowledge-agent` 为 `initialized` |
| 模型与检索 | CLI `ask` smoke | 有工具证据和时间戳 |
| Telegram | `/whoami` | 返回稳定内部用户编号 |
| 微信 | 私聊 `/whoami` | 返回绑定后的同一编号 |
| 多渠道 | 两端交错发送 | 回复来源正确、历史不串线 |

跨渠道身份绑定只支持 Telegram 与微信。来源端发送 `/link <目标渠道>`，再在目标端发送返回的 `/link <绑定码>`；目标端即使已经自动注册并拥有内容，也会完整归并到来源端 `/whoami` 对应的 tenant。绑定码默认 10 分钟过期、限定目标渠道且只能成功消费一次。若返回“目标账户仍有内容正在处理”，等待 ingestion 完成后使用同一绑定码重试；该失败不会消费绑定码。

上线前运行 `bash scripts/smoke_identity_link.sh`，按固定检查点完成人工 Telegram -> 微信和微信 -> Telegram smoke。脚本只比较 `/whoami` 编号并记录固定 pass/fail，不接收、回显或保存绑定码、平台 sender identity 与消息正文。绑定完成后还要从两端分别验证同一条知识可检索，并确认两端对话历史不互相带入。

## 12. 备份与恢复

必须同时保护：

- PostgreSQL：用户、渠道身份、知识条目、segment/embedding、conversation history。
- MinIO：原始字幕/文本对象。
- LangBot 自身数据库与非仓库内平台配置。
- secret 管理系统中的凭据；不要把凭据打进普通备份压缩包。

PostgreSQL 逻辑备份示例：

```bash
docker compose exec -T postgres pg_dump -U postgres -Fc kb > kb-YYYYMMDD.dump
```

备份文件含私聊历史与私有知识内容，必须加密、限制访问并设置保留期。恢复演练应在
隔离环境进行；恢复前停止渠道入口，避免旧备份与新消息同时写入。MinIO volume
需要使用基础设施快照或 MinIO/S3 兼容备份工具另行备份，只有 PostgreSQL dump
不能恢复原始对象。

身份归并提交后不可通过用户命令拆分。回滚应用版本会保留已经归并的知识，但不会恢复原来的两个 tenant；如需拆分只能从正常 PostgreSQL 备份执行管理员恢复流程。

回收站容量巡检（在备份与 purge smoke 后执行）：

```sql
SELECT pg_size_pretty(pg_database_size(current_database())) AS database_size;
SELECT relname, pg_size_pretty(pg_total_relation_size(relid)) AS total_size,
       n_live_tup, n_dead_tup, last_autovacuum
FROM pg_stat_user_tables
WHERE relname IN ('content_item', 'segment', 'ingest_dispatch');
SELECT indexrelname, pg_size_pretty(pg_relation_size(indexrelid)) AS index_size
FROM pg_stat_user_indexes
WHERE relname IN ('content_item', 'segment');
SELECT count(*) AS trash_count,
       min(deleted_at) AS oldest_trash,
       count(*) FILTER (WHERE deleted_at <= now() - interval '30 days') AS expired
FROM content_item
WHERE deleted_at IS NOT NULL;
```

按 `purge_sweep` 的 claimed/completed/failed/deferred counters 与 oldest trash 观察 backlog；
不得在请求路径执行 `VACUUM FULL` 或 `REINDEX`，只在实测 dead tuples/index bloat 后安排维护。

## 13. 升级与回滚

升级顺序：

1. 停止 LangBot adapters/plugin，阻止新请求。
2. 备份 PostgreSQL、MinIO 与 LangBot 配置。
3. 停止 gateway 与 `web-server`，并在反向代理隔离其写入口，从而冻结 Agent 与 Web 写入，再安装新的 Python 依赖。
4. 执行 `alembic upgrade head`，确认 revision `f1a2b3c4d5e6`。
5. 在 `web/` 执行 `corepack pnpm install --frozen-lockfile`、`check:api`、`test`、
   `typecheck`、`lint` 与 `build`，禁止继续提供旧的 `web/dist`。
6. 启动 compatible worker（`ingest,maintenance`）与单一 beat，确认 ingest queue、completion outbox publisher、CA、Redis AOF 与 MinIO readiness。
7. 启动 gateway 并检查 health、schema 与 CLI ask。
8. 启动 `web-server`，从精确 HTTPS origin 检查 `/api/v1/health`、首页、详情页刷新，
    并确认未知 `/api/*` 返回 JSON 404 而不是 SPA HTML。
9. 最后启动 plugin/LangBot，并按第 8 节验证 required-plugin 与 adapter readiness。

保存或 Agent 条目管理路径异常时，停止或在反向代理隔离 gateway 与 `web-server`，阻止新的
pending action、ContentItem、dispatch、Agent management calls 以及 Web 写入。不要删除或重绑用户数据。已在运行的 worker
任务可以安全完成；若出现 tenant mismatch 或无界重复 enqueue，再停止 worker intake，并保留
`ingest_dispatch` / `pending_channel_action` rows 供审计。

代码回滚时优先保持数据库向前兼容：回滚 gateway/worker binary，但保留新增列与表。只有在
隔离恢复演练中确认安全且已有备份时才执行指定 revision downgrade；生产回滚不得用 destructive
downgrade 删除 action/dispatch audit。回滚与重启都不得重置 Telegram/微信身份、微信扫码登录、
conversation history、已有 content 或 MinIO 对象。

Completion notification poller 出现异常时，只停止 beat 的 notification entry，不停止
ingestion 真相写入或其他 `maintenance` 工作；保留 `ingest_completion_event` 与
`ingest_completion_delivery` rows。代码回滚也保留这些表，修复后由下一 tick 继续；不得通过
重跑 ingestion、恢复旧 publisher/consumer、清空 Redis queue 或删除 event/ledger 来“修复”通知。

LangBot 版本升级必须重新验证：sender ID、bot UUID、conversation ID、平台 message
ID、plugin event 顺序、monitoring 隐私补丁和两个 adapter 并发。不能假设 4.10.6
补丁可直接应用到后续版本。

## 14. 常见故障

### gateway 无法启动

- `CHANNEL_GATEWAY_SECRET is required`：应用 `.env` 未设置共享密钥。
- `must be at least 32 characters`：共享密钥过短。
- `must bind to loopback`：不要绑定 `0.0.0.0`；重新规划同网络命名空间部署。
- 数据库连接失败：检查 `POSTGRES_HOST`、端口、密码和 container health。
- 模型构造失败：检查 `AGENT_MODEL`、API key、base URL 与 provider 名称。

### plugin 回复“渠道暂时不可用”

- gateway 未启动或 8765 不在 plugin runtime 的 loopback。
- 两边 `CHANNEL_GATEWAY_SECRET` 不一致。
- 系统时钟偏差超过 60 秒，HMAC 请求会被拒绝。
- bot UUID 未加入 `KB_BOT_CHANNELS`，或 channel 拼写不是 `telegram/wechat/slack`。

### LangBot 在 adapter 出现前退出或 readiness deadline 超时

- 检查 `plugin.required_plugins` 是否为精确的
  `notebook-agent/notebook-knowledge-agent`，bridge pipeline 是否显式绑定它。
- 在管理面板检查插件状态；`installed`、`starting` 或不存在都不是 `initialized`。
- 检查安装插件目录的私有 `.env` 是否存在且为 `0600`，但不要打印文件内容。
- 检查 gateway 的 loopback health、HMAC secret 是否两端一致，以及 macOS CA 设置。
- stdio plugin runtime 断线后，LangBot 4.10.6 不能安全自动重连；停止 LangBot core 后
  再按第 8 节顺序启动。Docker/WebSocket 重连期间消息会 fail closed，不会落入 Local Agent。

### 微信二维码过期或重新登录失败

1. 停止 LangBot core/adapters，保留 gateway、数据服务、bridge package 和私有 `.env`。
2. 在 LangBot 管理面板为 OpenClaw/iLink 微信 adapter 发起新的登录会话；只在本机受信任
   屏幕展示二维码，二维码不要截进仓库、日志或工单。
3. 使用微信个人号完成扫码，等待管理面板显示成功并持久化登录状态。
4. 重新启动 LangBot；先通过 required-plugin readiness，再做一次微信 `/whoami` smoke。
5. 若仍出现 `SSLCertVerificationError`，回到本章的 macOS TLS CA 配置，不要靠反复扫码
   或关闭 TLS 校验解决。

### HTTP 401

检查共享密钥、时钟、请求 nonce，以及请求体是否在签名后被代理修改。不要通过关闭
HMAC 或暴露未认证 endpoint 绕过。

### 能回复但没有知识结果

- 确认内容属于 `/whoami` 返回的同一 `AppUser.id`。
- 确认 `content_item.state` 已到 `ready`。
- 确认 embedding key、model 和 1536 维度一致。
- 不要把另一个用户的内部编号传给 ingest。

### 普通知识问题回复“查询能力暂时不可用”

- 检查 `ZHIPU_API_KEY` 是否只在应用的私有 `.env` / secret store 中配置，且 gateway 重启后已加载。
- 确认 `EMBEDDING_MODEL`、endpoint 和 `EMBEDDING_DIMENSIONS=1536` 与导入内容时使用的配置一致。
- provider 的超时、响应数量/维度错误或非有限数值会被安全拒绝；不要在日志中添加问题正文、
  vector、请求 payload 或 API key 来排查。
- gateway 的 `notebook_agent.runtime` 日志只记录内部 request ID、tenant ID、阶段、稳定
  错误码、异常类和耗时。用 request ID 区分 `embedding_failed` 与 `retrieval_failed`；不要
  为了排障记录问题正文、外部身份、证据内容、DSN、SQL、向量或 provider payload。
- 如果确定性命令也不可用，则先按“plugin 回复渠道暂时不可用”排查 gateway/bridge，而不是
  把问题归因于 embedding。

### 保存已入队但内容没有 ready

- 先确认 worker `inspect ping/active_queues`、Redis、MinIO 和 schema head；
  不要让用户反复发送同一 URL 作为重试机制。
- 用内部 request、tenant、item、dispatch ID 关联 gateway 与 worker，只记录 stage、duration 和
  `queue_unavailable`、`transient_fetch_failed`、`ingestion_failed` 等稳定错误码。
- 不得记录或返回消息正文、URL、`why_saved`、外部身份、DSN、secret、字幕、vector、Celery
  backend payload、provider payload、exception message 或 traceback。
- `pending/enqueued/running/completed/failed` 是 dispatch 状态；`ContentItem.ready` 才表示可检索。
  “数据库没有搜索结果”与 ingestion/queue/provider 失败必须保持不同用户提示。
- worker TLS 失败时修复 CA 并按新 request 的有界 retry 流程恢复；不得关闭证书或 hostname 校验。

### 重启后追问丢失

- 确认 PostgreSQL 中 conversation tables 存在且 migration 在 head。
- 确认 bot UUID、external user、conversation ID 没有变化。
- 确认没有发送 `/new`，并检查 `CONTEXT_MAX_TURNS` 与 `CONTEXT_TOKEN_BUDGET`。
- Telegram 与微信默认不共享历史，即使两者已绑定同一个 AppUser。

### 微信断线

微信 adapter 应独立恢复。不要停止 Telegram、gateway 或 Agent；先确认 Telegram
仍能问答，再单独处理 OpenClaw/iLink 登录。若 sender identity 发生变化，不要手工
回退到默认用户，应重新执行可信绑定或管理员纠错。

## 15. 部署完成定义

部署完成不等于产品验收完成。部署阶段通过条件是：服务健康、migration 在 head、
CLI 真实模型 smoke 成功、bridge plugin 为 `initialized` 后两个 LangBot adapter 同时
enabled、启动/隐私补丁生效。之后仍需
按 `manual-acceptance.md` 由人工核对 Telegram 完整 E2E、微信个人号 smoke、时间戳
准确性、两用户数据隔离和断线故障隔离。
