# 人工评测对照样本填写规范

这份文件定义你需要填写的人工 gold。目标是让另一个人只看样本和模型输出，就能判断“答对了什么、引用哪里、哪些补充不允许”。它不是要求写一篇标准答案，也不是让模型逐字匹配。

## 最低数量和覆盖

先填写 20–30 条，最低不得少于 20 条。建议覆盖：

- 直接关键词检索；
- 同义改写或口语问法；
- 中文问题查询英文视频；
- 需要两个或更多片段才能完整回答；
- 需要前后文才能判断的片段；
- 知识库中不存在、必须明确拒答的问题。

同一视频可以有多条问题，但不要只换一个标点；每种类型至少有 2 条。建议至少 4 条 multi-segment、3 条 no-evidence，其余分散在直接、改写、跨语言和前后文。

## 现在你只需要填写什么

我已经在 `human_eval_samples.yaml` 里预填了 20 条问题、视频 alias、case 和类型。第一轮你只需要填写每条的 `reference_answer`；如果某条问题本身不适合评测，可以在 `annotation_note` 里写明原因。`sample_id`、`case_id`、`turn_index`、`kind` 和 `fixture_alias` 都不要改。

第一轮不要求你填写 segment ID 和时间戳。等答案确定后，再由 benchmark 实现阶段根据答案和 transcript 补齐证据定位。完整字段说明如下：

| 字段 | 要求 |
| --- | --- |
| `sample_id` | 已预填的唯一 ID，不要修改。 |
| `case_id` / `turn_index` | 已预填的内部映射。`case_id` 是它对应的现有评测场景，`turn_index` 是该场景中的第几轮；你不用填写。 |
| `kind` | 已预填的问题类型；你不用填写。`direct_keyword`=直接问，`paraphrase`=换种说法，`cross_language`=跨语言，`multi_segment`=需要多处证据，`context`=需要前后文，`no_evidence`=知识库没有证据。 |
| `query` | 实际发送给 Agent 的完整问题，不要只写主题词。可引用 catalog fixture 占位符（例如 `{baseline_topic}`），运行时会替换为固定视频主题。 |
| `fixture_alias` | 已预填的固定视频别名；你不用填写。 |
| `gold_item_id` / `gold_segment_ids` | 第二轮再补的稳定证据 key；第一轮可以不写。 |
| `gold_timestamp_range` | 第二轮再补的相关时间范围；第一轮可以不写。 |
| `reference_answer` | 你现在要填写的核心内容：1–3 句人工参考答案，或 2–5 个事实要点。不要求逐字匹配。 |
| `annotation_note` | 可选。写答案边界、争议点或“这条不适合评测”的原因。 |

## 怎么写证据

`gold_segment_ids` 是“允许引用的相关片段集合”，不等于模型必须引用全部片段。是否必须覆盖全部由 `evidence_groups` 决定：

- 单事实：`[[seg-a]]`；引用 `seg-a` 即完整。
- 两个独立事实：`[[seg-a], [seg-b]]`；两个 group 都要被引用或回答明确覆盖。
- 一个事实需要相邻上下文：`[[seg-a, seg-b]]`；两段视为同一组，缺一段可能导致时间戳或语义不完整。

如果多个片段都同样正确，把它们放在同一个 group 中，而不是强迫模型命中某个唯一 ID。若只能确认视频正确但无法确认片段或时间，不要猜 segment；先把样本标为待补全。

## 无证据样本

`kind: no_evidence` 时：

- `gold_item_id: null`、`gold_segment_ids: []`、`gold_timestamp_range: null`、`evidence_groups: []`；
- `reference_answer` 写“应明确说明知识库没有相关证据”之类的方向；
- `answer_boundary.must_not_claim` 写禁止的常识补充或虚构视频来源；
- 不要因为模型回答得像真的就新增 gold 证据。

## 第一轮交付前自检

- 保留至少 20 条问题，`sample_id` 不要改名；
- 每条有一段 `reference_answer`；无证据问题写“应明确说明知识库没有相关证据”；
- 答案写事实，不写固定措辞要求；
- 不填真实用户信息、密钥或 provider 原始响应。

第二轮才检查 segment、timestamp、evidence groups 和答案边界，不需要你现在处理。

## 评分文件和样本文件分离

样本作者只编辑 `human_eval_samples.yaml`。模型回答、引用快照和人工分数由后续评测导出到 `.eval-results/`，使用 `human_eval_review_template.yaml` 的字段。不要把评分写回 gold 文件，否则会混淆“标准证据”和“某次模型表现”。
