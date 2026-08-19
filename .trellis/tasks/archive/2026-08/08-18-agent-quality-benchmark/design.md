# Agent 质量 Benchmark 技术设计

## 1. 设计边界

本任务在现有 `evals/natural_language` 之上增加质量、稳定性和性能观测层，保留现有 catalog 的行为契约。`runner.py` 继续负责真实 MCP 生命周期、临时 grant、fixture 准备、tool trace 和安全断言；新增 scorer 只消费经过规范化的结果，不改变 Agent 的工具选择或权限逻辑。

质量结果分成三条独立链路：

```text
真实模型 turn
  ├─ 行为契约：tool policy / status / state / safety
  ├─ 检索证据：排名结果、引用、时间戳 → gold scorer
  └─ 运行指标：latency / model calls / tool calls / recovery
```

## 2. Gold Evidence 数据契约

推荐新增 `evals/natural_language/gold_evidence.yaml`，而不是把人工判断塞进 `Expectation`。这样可以继续复用现有 22 个 case，并允许一个行为 case 关联多个 query/turn 的证据标签。

每条样本使用版本化结构：

```yaml
- id: retrieval.cross-segment-01
  case_id: retrieval.search
  turn_index: 1
  query: "这个视频里提到的 xxx 在哪里？"
  gold_item_id: baseline
  gold_segment_ids: [baseline.seg.03, baseline.seg.04]
  gold_timestamp_range: [312, 365]
  timestamp_tolerance_sec: 5
  evidence_groups: [[baseline.seg.03], [baseline.seg.04]]
  kind: multi_segment
```

`gold_item_id` 和 `gold_segment_ids` 在源文件中必须是稳定 fixture alias/segment key，而不是某次数据库运行的自增 ID。fixture resolver 在运行时将它们映射为真实 ID，并在内部结果中同时保留稳定 key 和 resolved ID。无证据样本使用 `gold_item_id: null`、空 segment 列表和明确的 `kind: no_evidence`，避免把缺标注和真正的 negative 混为一谈。

标注规则：直接关键词、同义改写、中文问题/英文视频、跨多个片段、需要前后文、知识库不存在各至少覆盖一条；跨片段使用 `evidence_groups` 定义“完整回答需要覆盖的证据组”。时间戳按片段起止秒数比较，允许误差显式记录。

## 3. 结果规范化和评分

现有报告只保存安全的 tool 名称和计数，不能直接支持排名评分。因此在 evaluator 内增加“质量投影”：仅保存检索结果的稳定 item/segment key、resolved ID、rank、start/end 秒和最终引用的相同字段；不保存摘录、问题、回答、tool 参数或原始结果。未知/缺字段结果记为 `unscorable`，不能猜测。

评分规则：

- Recall@k：前 k 个检索结果中至少有一个 gold segment；无证据样本单独统计 false positive。
- MRR：第一个相关 segment 的 `1/rank`，未命中为 0。
- Citation Precision：最终引用中属于 gold item/segment（或明确允许的 evidence group）的比例；无引用按样本类型判定，不把空结果默认为通过。
- Citation Completeness：已覆盖的必需 evidence group / 总 group 数；单证据问题退化为 0/1。
- Timestamp Hit Rate：引用时间戳区间与 gold 范围相交且误差不超过 `timestamp_tolerance_sec` 的比例。

聚合报告必须同时显示 micro（按证据/引用计数）和 macro（按 query 平均），并列出样本缺失、fixture skip、unscorable 的数量。Citation Precision 只评最终回答引用；Recall/MRR 只评检索排名，避免重复计分。

## 4. 行为结果分层

扩展 `CaseResult` 或新增汇总投影，保留原始 pass/fail/skip：

- `policy_pass`：required/allowed/forbidden tools、状态和模型调用约束满足；
- `task_pass`：所有 turn 的用户目标、状态迁移和 gold evidence 满足；
- `citation_validity`：引用 item/segment/timestamp 通过 gold 校验；
- `conversation_state_pass`：跨轮指代、隔离、重启恢复符合 catalog；
- `safety_violation`：越权/危险 tool 一次即失败。

最终摘要提供五个独立分母，不能以 policy pass 代替 task pass。跳过 fixture 或无 gold 的旧 case 继续显示为 skip/未评分，不伪造 0 分。

## 5. 人工评测数据契约和副本

样本作者先填写任务目录中的 `human_eval_samples.yaml` 的 `reference_answer`，详细字段说明见 `human-eval-data-contract.md`。问题、case、turn、fixture alias 和类型已经预填；segment/timestamp/evidence groups 延后到实现阶段根据 transcript 补齐。该文件是人工 gold，不是模型运行结果，必须满足以下边界：

- 每条样本有稳定、脱离运行顺序的 `sample_id`，并关联现有 `case_id`/`turn_index`；这些元数据由模板预填，同一问题的改写使用不同 ID。
- `fixture_ref` 使用 fixture alias 或公开平台标识，`gold_item_id`/`gold_segment_ids` 使用稳定 key；禁止填写某次数据库运行的自增 ID 作为唯一依据。
- 第一轮 `reference_answer` 写 1–3 句人工参考答案或 2–5 个事实要点；后续再从中整理 `reference_points` 和 `answer_boundary`，不要求模型逐字复述参考答案。
- 多证据问题必须声明 `evidence_groups` 和完整性要求；无证据问题必须声明 `no_evidence: true`、允许的拒答语义和禁止的常识补充。
- `gold_timestamp_range` 是定位范围而不是回答字数提示；必须写 `timestamp_tolerance_sec`，未知时间不能用 `[0, 0]` 伪造。

新增显式 opt-in 的本地导出（例如 `--export-human-set`），从这些固定 query 生成脱离运行顺序的样本包：样本 ID、query、模型回答、引用视频/时间戳和 rubric 空表。该包位于 `.eval-results/`，默认 `.gitignore`，不会进入 sanitized `report.json`/`report.md`。人工评分模板为同目录 `human_eval_review_template.yaml`，评分记录不回写 gold 样本。

人工评分采用固定 0/1 字段：回答解决问题、只使用知识库证据、视频正确、时间戳可定位、无明显无依据补充、语气/引导合理。`null` 表示未评分，`disputed` 表示需要复核，不能静默当作 0。导入评分后只发布聚合计数和样本数；如未来需要多人评审，再另行增加一致性指标，不把未校准的 LLM judge 当作发布门槛。

## 6. 可靠性、性能和失败注入

在现有 `elapsed_ms`、诊断 trace 和 fixture 生命周期上增加 run-level collector：统计模型调用数、tool 调用数、loop 超限、ingestion/retry/outbox 状态和重启恢复。小规模性能命令固定使用并发 1、5、10，输出 p50/p95、成功率和错误分类；不改变普通 `--all` 的串行、可审计语义。

失败注入使用可注入的 bounded adapter/fixture 开关，不在生产代码里随机 sleep 或吞错。每个注入场景都记录预期终态（保留任务、`failed`/`retryable`、错误码、可重试、无幽灵数据），并在 teardown 中恢复共享 fixture。优先覆盖 worker stop、Redis/MinIO/embedding 暂时失败、notification consumer 重启和 MCP session 重启。

## 7. 隐私、兼容和发布

- 默认报告仍禁止问题、回答、tool 参数/结果、URL、原始 ID 和 provider 错误正文；质量投影只保留必要的稳定键和数值。
- Gold 数据只包含固定公开 fixture 的人工标签，不携带真实用户内容或秘密。
- 现有 `--validate-catalog`、`--preflight`、`--prepare-fixtures`、`--case`、`--category`、`--smoke`、`--all` 语义保持兼容；新增选项应独立且有界。
- 报告 schema 和 gold schema 均带版本号；旧报告仍可阅读。没有实际运行结果时，文档只描述指标和命令，不填写百分比。
