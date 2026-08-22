# 技术设计：跨平台个人知识库

配套文档：`prd.md`（需求与实测约束）。本文只写技术决策，不重复实测数据。

## 设计目标的优先级

1. **摄入层可替换** — 平台策略变更时只改 connector，不动 schema 和检索
2. **原始件永久保留** — 重新切分/重新嵌入不需要重新抓取（平台随时可能再收紧）
3. **失效可观测** — 「静默空成功」和切分降级必须在数据里留痕，不能只靠日志
4. 视频与图文走同一条检索链路，只在渲染跳转时分叉

## 模块结构

```
app/
  connectors/          # 每个平台一个，接口统一
    base.py            # Connector 协议：fetch_meta / fetch_text
    youtube.py         # yt-dlp 子进程
    bilibili.py        # view API（元数据）+ 音频下载
    wechat_mp.py       # HTML 解析
  ingest/
    validate.py        # 空响应/空成功守卫
    chunker.py         # 分级降级切分
    embed.py
    tasks.py           # Celery 任务图
  retrieval/
    search.py          # 元数据过滤 + BM25 + 向量 + RRF
    rerank.py
  agent/
    tools.py           # LangGraph 工具定义
  api/
    items.py           # 摄入 + 扩展回传
    search.py
  models.py            # SQLAlchemy
migrations/            # Alembic
extension/             # WXT (独立构建)
```

`connectors/base.py` 定义唯一契约，新平台只实现两个方法：

```python
class Connector(Protocol):
    platform: Platform
    def match(self, url: str) -> str | None: ...          # 返回 platform_id 或 None
    def fetch_meta(self, platform_id: str) -> ItemMeta: ...
    def fetch_text(self, platform_id: str) -> TextResult | NeedsExtension | NeedsASR: ...
```

`fetch_text` 的返回类型是设计核心：B站字幕返回 `NeedsExtension`，无字幕视频返回 `NeedsASR`，这两种不是异常而是**正常的状态转移**，由任务图分派到不同后续路径。

## 数据模型

统一 `content_item` + `segment`，`kind` 区分视频/图文，定位符分列存储。

```sql
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_trgm;

CREATE TYPE platform      AS ENUM ('youtube','bilibili','wechat_mp');
CREATE TYPE item_kind     AS ENUM ('video','article');
CREATE TYPE text_source   AS ENUM ('official_cc','auto_caption','asr_whisper','article_text','none');
CREATE TYPE ingest_state  AS ENUM ('pending','fetching','needs_extension','needs_asr',
                                   'chunking','embedding','ready','failed','no_text');

CREATE TABLE content_item (
  id             bigserial PRIMARY KEY,
  user_id        bigint      NOT NULL REFERENCES app_user(id),
  platform       platform    NOT NULL,
  platform_id    text        NOT NULL,          -- video_id / bvid / sn
  kind           item_kind   NOT NULL,
  url            text        NOT NULL,
  title          text,
  author         text,
  published_at   timestamptz,
  duration_sec   int,                            -- video only
  char_count     int,                            -- article only
  lang           text,                           -- 'en' / 'zh' -> 驱动切分阈值
  description    text,
  tags           text[],
  chapters       jsonb,                          -- YouTube: [{start,end,title}]
  cover_url      text,
  -- 用户侧
  saved_at       timestamptz NOT NULL DEFAULT now(),
  why_saved      text,
  watch_state    text        DEFAULT 'unwatched',
  watch_pos_sec  int,
  -- 去重与溯源
  content_hash   text,                           -- 正文/转录规范化后 sha256
  raw_object_key text,                           -- MinIO: 原始 json3 / HTML
  text_source    text_source  NOT NULL DEFAULT 'none',
  state          ingest_state NOT NULL DEFAULT 'pending',
  fail_reason    text,
  UNIQUE (user_id, platform, platform_id)
);
CREATE INDEX ON content_item (user_id, saved_at DESC);
CREATE INDEX ON content_item (content_hash) WHERE content_hash IS NOT NULL;
```

`content_hash` 不加唯一约束 —— 实测两个不同 `video_id` 转录逐字节相同，那是**同一内容的两个来源**，都要保留（用户可能收藏了其中一个），只在检索时合并展示。

```sql
CREATE TABLE segment (
  id            bigserial PRIMARY KEY,
  item_id       bigint NOT NULL REFERENCES content_item(id) ON DELETE CASCADE,
  seq           int    NOT NULL,
  -- 定位符：视频用秒，图文用字符偏移；互斥
  start_sec     numeric(9,2),
  end_sec       numeric(9,2),
  char_start    int,
  char_end      int,
  anchor        text,                    -- 图文：段落序号或标题锚点
  text          text   NOT NULL,
  embedding     vector(1536),
  fts           tsvector,
  boundary_kind text   NOT NULL,         -- chapter|gap|punct|semantic|hard_cut
  CONSTRAINT loc_ck CHECK (
    (start_sec IS NOT NULL AND char_start IS NULL) OR
    (char_start IS NOT NULL AND start_sec IS NULL)
  ),
  UNIQUE (item_id, seq)
);
```

`boundary_kind` 是**可观测性字段**，不是元数据。切分质量直接查得出来：

```sql
SELECT lang, boundary_kind, count(*)
FROM segment s JOIN content_item i ON i.id = s.item_id
GROUP BY 1,2;
```

中文 `hard_cut` 占比就是 PRD 里那个 19/20 基线的持续监控指标。

### 索引

```sql
CREATE INDEX ON segment USING hnsw (embedding vector_cosine_ops)
  WITH (m = 16, ef_construction = 64);
CREATE INDEX ON segment USING gin (fts);
CREATE INDEX ON segment USING gin (text gin_trgm_ops);   -- 中文回退
```

**HNSW 而非 IVFFlat**：IVFFlat 需要先有数据才能训练聚类，个人库是增量写入，冷启动时召回会很差；HNSW 无训练步骤，插入即可用。代价是索引体积大约 2 倍，但 92MB 量级无所谓。

**中文全文检索**：`to_tsvector('english', '大语言模型')` 会把整句当一个 token，完全不可用。方案是双轨：
- 英文段落写 `fts = to_tsvector('english', text)`
- 中文段落 `fts` 留空，检索走 `text ILIKE` + `gin_trgm_ops`

不引入 `zhparser`（需要编译 PG 扩展，Docker 镜像得自己构建，对个人项目不值得）。trigram 对中文短查询召回够用，且 `pgvector` 本身承担了主要语义检索职责。

## 摄入任务图

```
save_item(url, why_saved?)
  └─ resolve_connector → fetch_meta          [state: fetching]
       └─ fetch_text
            ├─ TextResult      → validate → chunk → embed   [ready]
            ├─ NeedsExtension  → 挂起                        [needs_extension]
            ├─ NeedsASR        → 挂起（等用户显式触发）      [needs_asr]
            └─ 无文本可得                                    [no_text]
```

`needs_extension` / `needs_asr` 是**终态之一**，不是失败。UI 上分别显示「打开B站页面以补全字幕」和「转录此视频」按钮。这是混合架构的核心：服务端诚实地承认自己拿不到，而不是重试到死。

Celery 配置要点：

```python
@app.task(bind=True, max_retries=5,
          autoretry_for=(TransientFetchError,),
          retry_backoff=8, retry_backoff_max=600, retry_jitter=True)
def fetch_text_task(self, item_id: int): ...
```

- **429 走 `TransientFetchError`**，8s 起指数退避到 10 分钟。实测 429 是单视频级，所以只重试该任务，**不降速整个队列**
- 摄入队列 `concurrency=2`，ASR 队列 `concurrency=1`（CPU 密集，实测 1.8x 实时会吃满核）
- 两个队列必须分开，否则一个 30 分钟视频的 ASR 会堵住所有元数据摄入

### 空成功守卫

这是全项目最容易出错的地方，单独成模块并强制在写入前调用：

```python
# ingest/validate.py
class EmptySuccess(TransientFetchError): ...

def guard_transcript(body: bytes, parsed_cues: list) -> None:
    """HTTP 200 + 0 字节是 YouTube 的静默拒绝，不是空字幕。"""
    if not body:
        raise EmptySuccess("empty body on HTTP 200")
    if len(parsed_cues) == 0:
        raise EmptySuccess("parsed 0 cues from non-empty body")

def guard_article(text: str) -> None:
    if len(text.strip()) < 50:            # 公众号错误页也返回 200
        raise EmptySuccess(f"article text too short: {len(text)}")
```

`EmptySuccess` 继承 `TransientFetchError` 所以会自动重试，重试耗尽后落 `state='failed'` 而**不是** `'no_text'`。这两个状态必须区分：`no_text` 是「这个视频真的没字幕」，`failed` 是「我们被拒绝了」。混淆的后果是永久丢数据且没人发现。

指标：`empty_success_total{platform}` counter。这个数字从 0 变正就是平台策略变更的最早信号，比异常率敏感得多。

## 切分算法

```python
def chunk(cues: list[Cue], *, lang: str, chapters: list | None) -> list[Segment]:
    """分级降级。返回的每个 Segment 带 boundary_kind 以便事后审计。"""
```

阈值按语言分设（实测中文 279 汉字/分钟 vs 英文 168 词/分钟）：

| lang | target | max | 计量单位 |
|---|---|---|---|
| en | 60s / ~170 词 | 120s | 词数 |
| zh | 60s / ~280 字 | 120s | 字符数 |

降级顺序（PRD 已定，此处补实现要点）：

1. **chapters** — 若 `chapters` 非空且某章 ≤ 180s，直接用章节边界，`boundary_kind='chapter'`。超长章节再进下一级细分
2. **gap ≥ 2.0s** — `boundary_kind='gap'`
3. **句末标点** — `[.?!。？！]`，`boundary_kind='punct'`
4. **语义切分** — 仅当前 3 级在整条轨上产出边界数 `< duration/180`（即平均片段 > 3 分钟）时触发。对 cue 逐条嵌入，相邻余弦相似度的**局部极小点**切开。`boundary_kind='semantic'`
5. **硬切 + 重叠** — `boundary_kind='hard_cut'`，附加 15% 重叠

第 4 级的触发条件要写成显式判据而不是 try/except，否则英文视频也会白跑一轮嵌入。触发时额外成本约为主嵌入的 1.5 倍（cue 比 segment 多），整库仍在 $0.2 量级。

**重叠只在 `hard_cut` 加**。前 4 级的边界是有语义依据的，加重叠反而会让同一句话在两个 segment 里重复命中，污染检索结果。

### 图文切分

公众号 HTML 结构清晰，不需要降级链：

```python
def chunk_article(html: str) -> list[Segment]:
    # 1. 提取 #js_content
    # 2. 按 <p> / <h1-h6> / <section> 拆段，保留段落序号作 anchor
    # 3. 合并短段到 ~400 字（中文）/ ~250 词（英文）
    # 4. 图片位置记入 anchor，正文里留 [图N] 占位
```

图片不做 OCR（MVP 范围外），但**位置要留痕**，否则「那张红色架构图在哪」这类查询将来没法接。

## 扩展 ↔ 服务端协议

MV3 约束下的两条硬规则：**不能远程加载代码**（选择器/正则必须随包发布，或从服务端拉**数据**而非代码），**service worker 随时被回收**（状态只能放 `chrome.storage`）。

### 认证

扩展安装时向服务端换取一枚长期 `device_token`（opaque，绑定 user_id），存 `chrome.storage.local`，请求走 `Authorization: Bearer`。**不用 OAuth**：个人自用工具，跳转授权流反而增加摩擦。服务端对 device_token 做速率限制即可。

### 一键保存

```
POST /api/items
{
  "url": "https://www.bilibili.com/video/BV1xtGV61Et4",
  "watch_pos_sec": 122.4,          // 用户当前播放位置，可选
  "why_saved": "讲了 checkpointer 的坑"   // 可选
}
→ 202 { "item_id": 41, "state": "fetching" }
```

服务端自己去抓元数据，扩展**不传**标题/作者 —— 扩展抓的是渲染后的 DOM，不如服务端 API 干净，且 DOM 结构会变。扩展只负责提供 URL 和用户上下文。

### B站字幕回传

这是扩展唯一不可替代的职责。`content_scripts` 用 `world: "MAIN"` 注入以 hook 页面自身的 `fetch`/`XHR`，**观测已签名的请求**——不重放、不构造签名：

```js
// world: MAIN, run_at: document_start
const of = window.fetch;
window.fetch = async (...a) => {
  const res = await of(...a);
  const u = (typeof a[0] === 'string' ? a[0] : a[0]?.url) || '';
  if (u.includes('aisubtitle.hdslb.com') || u.includes('/bfs/subtitle/')) {
    res.clone().json().then(j => window.postMessage(
      { __kb: 'subtitle', bvid: getBvid(), payload: j }, '*'));
  }
  return res;
};
```

拿到的是 B站字幕原生格式（`{body:[{from,to,content}]}`），回传：

```
POST /api/items/{item_id}/text
{
  "source": "official_cc",
  "format": "bilibili_json",
  "lang": "zh-CN",
  "payload": { "body": [ {"from": 0.5, "to": 3.2, "content": "..."} ] }
}
→ 200 { "state": "chunking" }
```

服务端在 `connectors/bilibili.py` 里把 `bilibili_json` 归一到内部 `Cue` 结构，与 yt-dlp 的 `json3` 走同一条切分链路。**归一化必须在服务端**，这样扩展不需要知道切分逻辑，将来加平台也不用改扩展。

若 `item_id` 尚不存在（用户先在B站页面点保存），扩展先调 `POST /api/items` 拿到 id 再回传字幕。两步而非一步，是为了让「保存」立即返回、字幕异步补齐。

### 边界情况

- **服务端已有字幕**：`POST /text` 返回 `409`，扩展静默丢弃，不覆盖 `official_cc`
- **同一视频多语言字幕**：`lang` 不同则都存，检索时按用户偏好语言优先
- **service worker 被回收**：字幕 payload 先写 `chrome.storage.local` 队列，`onStartup` 时重放未确认项

## 混合检索

```python
def search(q: str, *, user_id: int, filters: Filters, k: int = 20) -> list[Hit]:
```

四段式，前三段并发：

1. **元数据过滤** — 平台、时间范围、`kind`、`watch_state`、时长。生成 SQL `WHERE` 前置，缩小后两路的候选集
2. **BM25** — `fts @@ websearch_to_tsquery` (英文) / `text ILIKE` + trigram (中文)
3. **向量** — `embedding <=> query_vec`，`LIMIT 50`
4. **融合 + 重排**

**融合用 RRF（Reciprocal Rank Fusion）而非加权分数相加**：BM25 分数和余弦距离量纲不可比，归一化后的权重需要按数据集调参，而 RRF 只用排名、无参数、对个人库这种规模足够稳：

```python
score = sum(1.0 / (60 + rank_i) for rank_i in ranks)
```

**重排用 cross-encoder 本地模型（`bge-reranker-v2-m3`）而非 LLM**：
- 多语言（中英混合库必须）
- ~50ms / 20 候选，LLM 重排要 1–3s
- 无 API 成本，检索是高频操作

LLM 只在 Agent 层做「这些结果里哪些偏工程实践」这类**需要理解用户偏好**的判断，不做基础相关性排序。

### 同视频多片段合并

同一 `item_id` 命中多个 segment 时不要平铺——用户要的是「这个视频里相关的几段」：

```python
# 按 item 聚合，item 分数取 max（不是 sum，避免长视频因片段多而虚高）
# item 内保留 top-3 片段，相邻片段（gap < 30s）合并为一段展示
```

## Agent 工具层（P1，紧接 P0）

P0 一旦完成 embedding 数据库的真实数据验证，下一阶段不先做 Web UI/扩展，而是先打通自然语言检索闭环：

```
用户自然语言问题
  → LangGraph Agent 判断检索意图并生成查询
  → search_segments（P1 默认走 pgvector；BM25 可作为独立补充调用）
  → 按需要调用 get_neighbors / get_item 扩展上下文
  → open_at 生成来源定位
  → 基于检索证据回答
```

P1 入口为 `python -m app.cli ask "<自然语言问题>"`。CLI 只是最薄的交互壳；Agent 本体放在 `app/agent/`，不能把工具选择和回答逻辑写进 CLI。

P1 的最低目录形态：

```
app/agent/
  tools.py       # 数据库检索工具；参数/返回值使用结构化类型
  graph.py       # LangGraph 工具调用循环、系统约束、终止条件
  prompts.py     # 证据约束与回答格式
```

首版 Agent 只开放只读工具，工具签名保持窄且可组合：

```python
search_items(query, platform?, date_range?, kind?, tags?) -> list[ItemHit]
search_segments(query, filters?) -> list[SegmentHit]     # 时间戳/段落级
get_item(item_id) -> ItemDetail
get_neighbors(segment_id, window=2) -> list[Segment]     # 上下文扩展
open_at(item_id, pos) -> str                             # 生成跳转 URL
```

`transcribe` 和 `mark_watched` 不进入 P1 工具集。前者随 P5 ASR 加入且必须经过用户确认，后者等 P2 有用户状态交互后再加入。

Agent 的回答约束：

- 对知识库内容作答前必须至少成功调用一次检索工具，不能靠模型记忆代答
- 每个关键结论附带 `item_id`、标题、证据片段和时间戳/段落链接
- 检索无结果或证据不足时明确返回「知识库中未找到」，不得补写看似合理的内容
- P1 允许 Agent 改写查询、追加检索和读取相邻片段，但基础相关性仍由 pgvector/BM25 决定，LLM 不直接给数据库候选打相关性分
- 工具调用设置最大轮数和超时，防止无结果时循环搜索

`open_at` 按 `kind` 分叉，这是唯一需要区分视频/图文的地方：

```python
youtube  → https://youtu.be/{id}?t={int(sec)}
bilibili → https://www.bilibili.com/video/{bvid}?t={int(sec)}
wechat   → {url}#anchor-{anchor}      # 段落锚点
```

后续加入 `transcribe` 时**必须经用户确认**才执行（PRD 决策 3）。Agent 可以建议但不能自动触发——实测 1.8x 实时，一次误触发能占满 CPU 半小时。

## 依赖与运行环境

```
yt-dlp            # 唯一单点依赖，pin 版本 + 每周 CI 回归
deno              # yt-dlp 已警告缺 JS runtime；不装会随机失败（实测 1/6）
faster-whisper    # small 模型，int8
ffmpeg            # 音频转码；yt-dlp 已警告缺失
bge-reranker-v2-m3
pgvector/pgvector:pg17
```

`yt-dlp` **不要 pin 死**：它靠频繁更新对抗平台变更，锁版本等于主动失效。策略是 pin 到次版本 + 每周 CI 跑真实摄入，CI 红了再升。这是本项目唯一接受"依赖会主动漂移"的地方。

### 运行环境：单机 M1，暂不需要 GPU

整套栈除 ASR 外都不吃 GPU：FastAPI/Celery/Redis/Postgres+pgvector/MinIO 是常规 IO/DB 负载；嵌入走智谱 Embedding-3 API（1536 维、每批最多 64 条）；重排是小型 cross-encoder，CPU 上个人库规模（20 候选/次、低频查询）足够。

唯一可能用到 GPU 的是 ASR。实测基线是 M1 CPU int8，`small` 模型 1.8x 实时（445s 音频耗 242s）。这个数字已经够用，原因是 ASR 在架构里是**低频、异步、用户显式触发**的兜底路径（PRD 决策 3：仅无字幕且用户要求时才跑），不在任何实时链路上——一个 20 分钟无字幕视频后台转录约 11 分钟，不阻塞摄入队列（`ingest/tasks.py` 里 ASR 队列已单独 `concurrency=1`，就是按 CPU 密集任务设计的）。

2060S（CUDA，`faster-whisper`/`ctranslate2` 支持）大概率能把这个数字压到 5–10x 实时，但这是**延迟优化，不是能力缺口**。租云 GPU 更不划算：单用户偶发 ASR 的工作量，实例冷启动 + 音频上传的开销经常比在 M1 上多等几分钟还大，还多了一层「音频离开本机」的隐私考量。

结论：MVP 阶段全部服务（含 ASR）跑在 M1 单机 docker-compose 里，不引入 GPU 依赖。只有当 ASR 实际用量成为可观测瓶颈（比如长视频排队转录明显拖慢体验）时，才值得让 2060S 所在机器跑一个指向同一 Redis/Postgres 的 Celery worker；云 GPU 租用只在那之后仍不够时才考虑，而不是现在预置。

## 设计上的已知取舍

1. **不做 OCR** — 关键帧文字识别在 MVP 外，但图片位置留痕（`[图N]` 占位 + anchor），将来可增量补
2. **不引入 zhparser** — 需自建 PG 镜像，trigram + 向量对个人库够用
3. **不做 LlamaIndex** — connector / 切分 / 检索 / 工具层全是自定义，框架无剩余价值
4. **单用户优先** — schema 有 `user_id` 但不做多租户隔离、配额、权限。个人自用，加了是负担
5. **不实现 B站 SESSDATA 刷新** — 服务端刷新会踢掉用户浏览器登录态（互斥），这正是选扩展方案的原因之一

## 分阶段验证的假设

设计里有两处没有实测数据支撑，按其实际实施阶段验证：

1. **P0：语义切分（第 4 级）对中文是否真的有效** — 相邻 cue 嵌入的余弦相似度在中文口语转录上是否存在可识别的局部极小点，尚未验证。若无效，中文只能退回硬切+重叠，`hard_cut` 比例降不下来，需要重新评估是否引入标点恢复模型（如 `funasr` 的 punc 模型）
2. **P4：RRF 的 k=60 常数** — 这是论文默认值，在 20 候选的小规模下可能需要调小，不阻塞 P1 Agent 先使用向量检索工具形成闭环

## 实施顺序

对应 PRD 的验收标准，P0 内部再拆：

| 步骤 | 产出 | 验证 |
|---|---|---|
| 1 | docker-compose（PG+pgvector / Redis / MinIO）+ Alembic 首版迁移 | `\d segment` 索引齐全 |
| 2 | `connectors/youtube.py` + `validate.py` | 摄入 1 个视频，原始 json3 落 MinIO |
| 3 | `chunker.py` 前 3 级 + 单测（含无标点样本） | 英文视频 `boundary_kind` 分布合理 |
| 4 | `embed.py` + HNSW 检索 CLI | 检索中段概念，跳转正确 |
| 5 | 空成功 / 429 回归测试 | PRD 两条回归验收 |
| 6 | 语义切分（第 4 级）+ 中文样本 | `hard_cut` 比例 vs 19/20 基线 |

步骤 5 必须在步骤 6 之前 —— 守卫比切分质量优先，因为守卫缺失会静默丢数据，切分差只是效果差。

### P1：自然语言检索 Agent（P0 后的下一个需求）

| 步骤 | 产出 | 验证 |
|---|---|---|
| 1 | `app/agent/tools.py`：封装 `search_segments` / `get_neighbors` / `get_item` / `open_at` | 每个工具可独立测试，返回结构化来源信息 |
| 2 | `app/agent/graph.py`：LangGraph 工具调用循环与只读白名单 | mock 模型验证多步调用、最大轮数和无结果终止 |
| 3 | `app.cli ask` 自然语言入口 | 用自然语言定位只在视频中段出现的概念 |
| 4 | 证据约束与真实库验收 | 答案包含标题、原文片段、时间戳链接；点击后确为对应内容 |

P1 不要求 RRF/rerank，也不要求 UI。它验证的是 Agent 是否能把用户的自然语言意图可靠地转成数据库工具调用；检索排序增强和产品交互分别留到 P4、P2。

### P0 入口/出口：命令行，不搭 API/UI/扩展

P0 的目的是验证「摄入 → 切分 → 检索」这条核心链路本身是否可行，不是验证产品形态。所以入口和出口都用命令行，不提前建 FastAPI 路由、Web UI 或浏览器扩展：

- **入口**：`python -m app.cli ingest <url>` 直接调用 `save_item` 任务链（`ingest/tasks.py`），跳过 HTTP 层
- **出口**：`python -m app.cli search <query>` 直接跑 `retrieval/search.py`，命令行打印 `item + segment + 跳转链接`

`api/items.py`（POST /api/items、B站字幕回传）和浏览器扩展顺延到 P2——P0 之后先用 P1 Agent 验证「自然语言能否可靠调用知识库」；该闭环成立后，再为「怎么把 URL 送进来」搭外围。

module 结构里的 `api/` 目录本身不受影响（仍是最终形态的一部分），只是实现顺序上排在 P2，不在 P0 范围。
