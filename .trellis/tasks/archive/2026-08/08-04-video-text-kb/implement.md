# 执行计划：P0（服务端摄入 + 切分 + 检索，命令行验证）

配套文档：`prd.md`（需求/验收）、`design.md`（技术设计）。本文只排 P0 的执行顺序和验证命令，不重复设计决策。

P0 范围 = design.md「实施顺序」表的步骤 1–6，入口/出口用命令行（design.md 已定）。P1 是紧接其后的自然语言检索 Agent；扩展/Web UI/`api/` 顺延到 P2，均不在本次执行范围内。

## 前置

- [x] 确认本机可跑 Docker（M1，运行环境见 design.md「运行环境」小节）
- [x] 确认 `ZHIPU_API_KEY` 可用（智谱 Embedding-3 调用需要；2026-08-05 真实 YouTube 摄入已完成 1536 维向量写入）
- [x] 装 `deno`（yt-dlp 依赖，缺失时实测 1/6 概率随机失败）

## 步骤 1 — 基础设施

**产出**

- [x] `docker-compose.yml`：`pgvector/pgvector:pg17` / `redis` / `minio`
- [x] `app/models.py`：`content_item` + `segment` 两表的 SQLAlchemy 模型（对齐 design.md 数据模型章节的 DDL，包括枚举类型、`loc_ck` 约束、`UNIQUE(item_id, seq)`）
- [x] `migrations/`：Alembic 初始化 + 首版迁移（含 HNSW / GIN / trigram 三个索引）
- [x] `app/config.py`：读取 `ZHIPU_API_KEY`、智谱 embedding 参数及 DB/Redis/MinIO 连接串（环境变量，不硬编码）

**验证**

```bash
docker compose up -d
alembic upgrade head
docker compose exec postgres psql -U postgres -d kb -c '\d segment'
```

期望：`\d segment` 显示 HNSW（`embedding`）、GIN（`fts`）、GIN trigram（`text`）三个索引存在，`loc_ck` 约束存在。

**回滚点**：迁移失败可 `alembic downgrade base` 后重建，此阶段无生产数据，可安全重跑。

---

## 步骤 2 — YouTube 摄入 + 空成功守卫

**产出**

- [x] `app/connectors/base.py`：`Connector` Protocol（`match` / `fetch_meta` / `fetch_text`），`ItemMeta` / `TextResult` / `NeedsExtension` / `NeedsASR` 数据类型
- [x] `app/connectors/youtube.py`：子进程调用 `yt-dlp`，拉取 `json3` 字幕 + chapters + 元数据
- [x] `app/ingest/validate.py`：`EmptySuccess` 异常 + `guard_transcript`（design.md 已给出实现骨架，直接落地）
- [x] `app/ingest/tasks.py`：`fetch_text_task`（Celery，`autoretry_for=(TransientFetchError,)`，`retry_backoff=8, retry_backoff_max=600`）
- [x] 原始 `json3` 落 MinIO，`content_item.raw_object_key` 记录对象键
- [x] `app/cli.py`：`ingest <url>` 子命令，直接调用 `save_item` 任务链（跳过 HTTP 层，design.md「P0 入口/出口」已定）

**验证**

```bash
python -m app.cli ingest "https://www.youtube.com/watch?v=<真实视频ID>"
docker compose exec postgres psql -U postgres -d kb \
  -c "select id, state, text_source, raw_object_key from content_item order by id desc limit 1;"
```

期望：`state='ready'`（若有字幕）或合理的中间态，`raw_object_key` 非空且能在 MinIO 里找到对应 json3 对象。

已验证（2026-08-05）：`qz9tKlF431k` 选择 `auto_caption/en-orig`，最终为 `item=1 state=ready`；数据库有 291 个分段，原始对象、内容哈希、合法时间戳和 1536 维向量均通过聚合验收。字幕轨与 yt-dlp 运行环境的具体修复记录在子任务 `08-05-youtube-subtitle-track-reliability`。

**回滚点**：单个视频摄入失败不影响表结构，删除对应 `content_item` 行重试即可。

---

## 步骤 3 — 切分器（前 3 级）

**产出**

- [x] `app/ingest/chunker.py`：`chunk()` 实现 chapter / gap≥2.0s / 句末标点 三级降级（design.md 切分算法章节的阈值表：中文按字符数、英文按词数）
- [x] 单测：`tests/test_chunker.py`
  - [x] chapters 存在且 ≤180s → `boundary_kind='chapter'`
  - [x] 无 chapters，有 gap≥2s → `boundary_kind='gap'`
  - [x] 无 gap，有句末标点 → `boundary_kind='punct'`
  - [x] **无标点样本**（PRD 实测：某英文视频句号逗号全为 0）→ 验证不会误判为 `punct`，正确落到下一级判据

**验证**

```bash
pytest tests/test_chunker.py -v
python -m app.cli ingest "<英文视频url>"
docker compose exec postgres psql -U postgres -d kb \
  -c "select boundary_kind, count(*) from segment group by 1;"
```

期望：英文视频 `boundary_kind` 分布里 `chapter`/`gap`/`punct` 占绝大多数，不应该此阶段就出现大量 `hard_cut`（英文有足够信号）。

**回滚点**：`chunker.py` 是纯函数，改动只影响新摄入内容；已有 `segment` 行不会被现有代码路径回溯修改。

---

## 步骤 4 — 嵌入 + 检索 CLI（向量 + BM25 双路对比）

用户决策：P0 阶段就把 BM25 接上做对比，不等到 P3。原因是中文检索走 `trigram` 而非 `to_tsvector('english')`（design.md 已定），这条路径和英文 `fts` 路径是否都可用，越早验证越好——如果 P0 就发现中文 trigram 召回明显弱于向量，后面的中文切分降级链（P2）要连带考虑检索侵蚀，不是纯切分问题。

范围边界：本步骤要的是「向量结果 vs BM25 结果」两路都跑起来、CLI 里能对比着看，**不要求** RRF 融合和 rerank——融合策略（RRF 的 k 常数）本身还是 design.md 标注的未验证假设之一，P0 阶段先看两路原始排序是否都合理，比强行融合更诚实。

**产出**

- [x] `app/ingest/embed.py`：调用智谱 Embedding-3 API，按最多 64 条顺序拆批并写入 `segment.embedding`
- [x] `app/retrieval/search.py`：
  - [x] `vector_search(query, k)` — `embedding <=> query_vec`
  - [x] `bm25_search(query, k)` — 英文走 `fts @@ websearch_to_tsquery('english', ...)`；中文走 `text ILIKE` + `gin_trgm_ops`（design.md 双轨方案，按 `content_item.lang` 分派）
  - [ ] RRF 融合（`score = sum(1/(60+rank_i))`）留到 P4，本步骤**不实现**，避免把「两路各自是否可用」和「融合是否合理」两个问题混在一起
- [x] `app/cli.py`：`search <query>` 子命令，**并排打印两路结果**（各自 top-k + 各自的 `item + segment + 跳转链接`），不做自动合并展示

**验证（对应 PRD P0 验收标准，扩展为双路对比）**

```bash
# 先摄入 20 个真实视频，含 1 个无标点英文、1 个中文
for url in $(cat sample_urls.txt); do python -m app.cli ingest "$url"; done
python -m app.cli search "<只在某英文视频中段出现的概念>"
python -m app.cli search "<只在某中文视频中段出现的概念>"
```

期望：
- 两路结果里至少一路的 Top 结果命中目标 segment，跳转链接落在那句话所在时间点附近（人工核对，PRD 明确要求的端到端验收，不是单测能替代的）
- 记录中文查询下 BM25/trigram 路的召回质量——如果明显弱于向量路，是预期内的（trigram 定位是"回退"，design.md 已说明），但要写进结果观察里，供 P3 中文切分降级链参考

**回滚点**：`embed.py` 失败不影响已切分的 `segment.text`，重跑该函数即可补齐 `embedding` 列，无需重新摄入。

---

## 步骤 5 — 空成功 / 429 回归测试（必须在步骤 6 之前）

design.md 已明确：**守卫比切分质量优先**，守卫缺失静默丢数据，切分差只是效果差。

**产出**

- [x] `tests/test_validate.py`：mock 一个返回 HTTP 200 + `content-length: 0` 的 timedtext 响应，断言：
  - [x] `guard_transcript` 抛出 `EmptySuccess`
  - [x] 该任务重试耗尽后落 `state='failed'`（**不是** `'no_text'`——两者语义不同，见 design.md 空成功守卫章节）
- [x] `tests/test_tasks.py`：mock 连续 15 次摄入调用，其中 1 次返回 429，断言：
  - [x] 该次任务走指数退避重试
  - [x] 其余 14 次任务不受影响、正常完成（验证 429 是单任务级重试，不暂停整个队列）
- [x] `empty_success_total{platform}` counter 埋点（design.md 已定的可观测性指标）

**验证**

```bash
pytest tests/test_validate.py tests/test_tasks.py -v
```

期望：两条 PRD 回归验收全部通过，且断言的是状态机结果（`state` 字段），不是仅仅"没有抛未捕获异常"。

**回滚点**：纯测试新增，无生产逻辑改动；若断言失败，说明步骤 2 的 `validate.py`/`tasks.py` 实现有缺口，回步骤 2 修复而不是在这里绕过。

---

## 步骤 6 — 语义切分（第 4 级）+ 中文样本

design.md 标注这是**P0 需要验证的假设**（不是已确定可行的设计），执行前明确这一步的产出是「验证结论」而不仅是「代码」。

**产出**

- [x] `app/ingest/chunker.py`：第 4 级语义切分——对 cue 逐条嵌入，相邻余弦相似度局部极小点切开；触发条件写成显式判据（`边界数 < duration/180`），不用 try/except
- [x] 第 5 级硬切 + 15% 重叠（仅 hard_cut 加重叠，前 4 级不加，design.md 已定原因）
- [x] 中文样本单测：验证局部极小点检测逻辑本身（不依赖真实 API 调用的确定性测试）

**验证**

```bash
python -m app.cli ingest "<中文视频url>"
docker compose exec postgres psql -U postgres -d kb \
  -c "select lang, boundary_kind, count(*) from segment s join content_item i on i.id=s.item_id group by 1,2;"
```

期望：中文 `hard_cut` 占比相对 19/20 基线（PRD 实测数据）显著下降。

**若语义切分对中文无效**（局部极小点不可识别，`hard_cut` 占比未显著下降）：这是设计里标注的未验证假设落空，不要在这一步硬撑或调参数掩盖，回到 design.md 补一条决策记录，评估是否引入标点恢复模型（如 `funasr` 的 punc 模型），再决定要不要继续往下走。

**回滚点**：若语义切分效果不理想，可先禁用第 4 级触发条件（回退到全量 hard_cut+重叠），不影响前 3 级已验证的路径。

---

## 完成后的收尾

- [ ] 跑一遍完整 CLI 闭环：`ingest` 20 个视频 → `search` 验证中段概念命中 → 人工核对跳转
- [ ] 记录 `empty_success_total` 和 `hard_cut` 占比的当前基线值（作为 P3 中文降级链的对比基准，design.md 已引用 19/20 这个数字）
- [ ] 更新 `prd.md` 的 P0 / P0 回归三条验收标准为已勾选状态
- [ ] 提请用户 review，确认 P0 是否验证了核心价值假设，然后进入 P1 自然语言检索 Agent

## 不在本次范围（明确排除，避免执行中蔓延）

- 自然语言检索 Agent（LangGraph） —— 紧接本任务的 P1，不并入 P0 收尾
- `api/items.py`、B站字幕回传协议、浏览器扩展 —— P2
- B站 / 微信公众号 connector —— P3
- RRF 融合 + rerank —— P4（P0 步骤4已接入向量+BM25双路对比，但不做融合/重排，见步骤4范围边界）
- ASR —— P5
