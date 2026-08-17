# Agent → Embedding → pgvector 连接设计

## 1. Current gap

当前数据流已经存在，但契约是隐式和可选的：

```text
Channel/CLI
  → ChannelService
  → KnowledgeAgent.search_segments
  → KnowledgeServices(embedder: Any | None)
      ├─ bm25_search(tenant)
      └─ if embedder: embed(query) → vector_search(tenant)
```

这会产生三个问题：缺 key 时静默变成纯词法；CLI 与 gateway 重复装配；没有测试跨过
Agent、embedding client、pgvector 和 citation 四个边界。因此“代码能走向量分支”不等于
“部署中的 Agent 已可靠连接 embedding 数据库”。

真实微信探针还显示，一条普通非命令入站先得到 Agent 的 evidence guard failure，随后又
得到渠道不可用提示。它至少证明 adapter → bridge → gateway → Agent 已经连通，但没有
`search_segments`/embedding 证据；两条回复可能来自重复 delivery、plugin 在成功拿到
`AgentAnswer` 后仍进入异常 fallback，或 LangBot required-plugin guard 误判。现阶段不凭
截图猜根因，实现前必须用 correlation/message ID 和 fake adapter 复现，且不得记录原消息。

## 2. Target data flow

```text
trusted ChannelEnvelope / CLI identity
              |
              v
         AgentRequest
              |
              v
 PydanticAI search_segments(query)
              |
              v
 KnowledgeServices(TenantContext, EmbeddingProvider)
      |                         |
      |                         +--> embed_query(query)
      |                                  |
      |                         validate count/dim/finite
      |                                  |
      +--> bm25_search(tenant)            v
                               vector_search(tenant, vector)
                  \             /
                   bounded de-dup
                         |
                  hydrate Citation
                         |
              evidence post-validation
                         |
                    AgentAnswer
```

Tenant 只在可信应用层注入一次。Embedding provider 只接收 query text；retrieval 只接收
应用生成的 vector 和固定 tenant ID；模型看不到 `user_id`、向量或 SQL。

## 3. Shared embedding contract

引入最小 protocol（最终路径在实现时按现有依赖方向选择，优先共享模块而非 Agent 私有）：

```python
class EmbeddingProvider(Protocol):
    dimensions: int

    def embed(self, texts: list[str]) -> list[list[float]]: ...
```

Zhipu implementation 的边界校验：

1. 空输入不发请求；
2. 每批响应按 index 恢复顺序；
3. 响应数量必须与输入相同；
4. 每个 vector 长度必须等于 configured dimensions；
5. 每个值必须是有限数值；
6. 对外异常只含 provider/status/error class，不复制 text、向量或 key。

Ingestion 与 query retrieval 使用同一个 protocol/implementation。避免复制一个
“query embedder”，否则 ingest/query 模型或维度会漂移。

## 4. Composition and readiness

建立一个共享 composition helper 构造 embedding provider 和 KnowledgeAgent，供 CLI
`ask` 与 `build_channel_service()` 使用。普通知识问题必须拿到 provider；若配置缺失，
`KnowledgeServices.search_segments()` 抛出领域级 `EmbeddingUnavailable`，由 Agent runtime
映射为稳定 `embedding_unavailable` 答案。

gateway 本身不因 provider 暂时缺失而拒绝启动，因为 `/start`、`/whoami`、`/link`、
`/new` 不依赖 embedding。这样渠道身份和运维命令保持可用，但普通问答不会假装使用了
embedding。真实 provider 配置存在但格式非法（例如非正 dimensions）应在 composition
阶段立即失败。

## 5. Retrieval and merge boundary

- `vector_search()` 和 `bm25_search()` 继续在 SQL 层强制 `ContentItem.user_id == tenant`。
- hydration 再次 join `ContentItem` 并检查 tenant，形成 defense in depth。
- 当前任务保留 bounded candidate de-dup，不把不同 score 空间解释为统一概率，也不新增
  RRF/reranker。必要时只使顺序确定且可测试。
- query vector 为空、维度不符或 provider error 时不执行 vector SQL，也不返回 lexical-only
  Agent answer。

## 6. Error matrix

| Failure | Layer | External result | Forbidden behavior |
| --- | --- | --- | --- |
| key missing | composition/service | `embedding_unavailable` | silent lexical-only answer |
| provider timeout/HTTP/invalid JSON | embedding | `embedding_unavailable` | echo query/key/body |
| count/dimension/non-finite mismatch | embedding | `embedding_unavailable` | send malformed vector to pgvector |
| pgvector/database error | retrieval | stable retrieval failure | expose SQL/DSN |
| embedding + database completed, no tenant-owned hits | service | `status=not_found`, `error_code=no_evidence` | call it unavailable/failed |
| draft cites unknown segment | output validator | internal retry + mandatory fresh search | return draft/guard copy |
| citation repair exhausted | Agent runtime | `failed/answer_unavailable` | expose invalid ID or `citation_required` |
| deterministic command | ChannelService | normal command result | call model/embedder |
| Agent 已返回 fail-closed answer | bridge/channel | exactly one rendered reply | append a second availability reply |

### Result taxonomy and copy contract

状态由实际完成边界决定，不能根据“是否有答案文本”猜测：

| Internal outcome | `AgentAnswer.status` | Stable code | User-facing intent |
| --- | --- | --- | --- |
| embedding 与 retrieval 成功，候选为空 | `not_found` | `no_evidence` | 当前私有知识库没有找到足够证据；可换关键词或先导入内容 |
| provider/config/vector validation 失败 | `failed` | `embedding_unavailable` | 查询能力暂时不可用，请稍后重试 |
| database/pgvector/tool execution 失败 | `failed` | `retrieval_unavailable` | 查询能力暂时不可用，请稍后重试 |
| Agent 未执行 required search | `failed` | `search_required` | 本次未完成必要检索，不能返回无来源答案 |
| 草稿 citation 不属于真实工具结果 | no external answer | internal `citation_mismatch` | 丢弃草稿并强制重新检索/生成 |
| citation 有界修复耗尽 | `failed` | `answer_unavailable` | 暂时无法生成可靠答案，请换个问法或稍后重试 |

渠道插件只渲染 `AgentAnswer.text`，不得把所有非 `ok` 状态统一改写成“渠道不可用”。尤其是
`not_found` 是一次成功请求的业务结果，不应触发 transport/runtime fallback；`failed` 也只
回复一次由 Agent/gateway 生成的稳定文案。

### Citation repair loop

Citation allow-list 校验从 run 后的终止检查前移到 PydanticAI output validator。当前安装版本
支持 validator 抛出 `ModelRetry`，将安全的修复指令送回同一次 Agent run。流程为：

```text
model draft
  → parse [S…] markers
  → markers ⊆ tool-returned Citation IDs ?
      yes → accept and append sources
      no  → discard draft (never persist/reply)
            → record current search-call generation
            → ModelRetry: rewrite query and call search_segments again
            → validate next draft and require a newer search call
            → accept valid draft or stop at bounded retry/usage limit
```

validator 的 retry prompt 只包含允许的内部 citation ID、错误类型和“重新检索”的指令，不
包含用户原文、完整证据、外部身份或 provider 信息。`AgentDeps` 记录第一次 mismatch 时的
`search_calls`；后续草稿只有在 `search_calls` 增加后才可通过，避免模型只把伪造编号机械替换
而没有执行用户要求的重新查找。

修复耗尽时 runtime 捕获 output retry exhaustion，构造一个没有 citations/new messages 的
`failed/answer_unavailable`。无效 draft 不写入 `ConversationTurn`，也不向 bridge 返回。

## 7. Test strategy

### Unit

- embedding response validation: order, count, dimension, finite values, empty input;
- missing/failing provider error mapping without content leakage;
- zero-hit、embedding failure 与 database failure 的 status/code/copy 分离；
- fake model 先输出未知 citation，随后必须再次调用 search 并用真实 ID 修复；
- 连续输出未知 citation 时只产生一个通用 `answer_unavailable`，无效 draft/ID 不出现在回复或持久化中；
- CLI/gateway composition invokes one shared builder;
- deterministic commands never touch embedder.
- fake bridge event 覆盖 `search_required`、`embedding_unavailable` 与成功答案，断言每个
  correlation/message ID 恰好一次 reply，且 LangBot default/fallback 不追加回复。

### PostgreSQL integration

Create two users with mutually exclusive ready items and 1536-dimensional vectors. A fake provider
returns a deterministic query vector. Run through `KnowledgeAgent` with a PydanticAI test model and
assert:

- provider called with rewritten/search query;
- pgvector returns only current tenant's segment;
- citation/title/timestamp come from stored rows;
- the other tenant cannot be reached through search, neighbor, item or open_at.

### Manual smoke

Use an existing internal user with ready embedded content:

```bash
.venv/bin/python -m app.cli ask --user-id <internal-id> '<question>'
```

Record only pass/fail, source title, timestamp validity and latency range. Do not record the question,
private excerpt, API key or external channel identity.

微信复测还需使用一个脱敏知识问题证明真实 embedding/pgvector 路径，并确认平台只收到一条
最终回复。普通问候触发 evidence guard 可以作为 fail-closed 测试，但不能作为 embedding
连接成功的证据。

## 8. Rollout and rollback

Rollout requires no schema migration and no re-embedding. Deploy code, verify provider configuration,
run automatic tests, then restart gateway so both CLI and channels use the shared composition.

Rollback restores the previous composition/service implementation; database embeddings and user data
remain untouched. If provider is unavailable during rollout, keep deterministic commands available and
fail ordinary questions closed rather than enabling lexical-only answers.
