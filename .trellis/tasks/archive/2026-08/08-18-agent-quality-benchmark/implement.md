# Implementation Plan

## Closure decision — 2026-08-19

The user accepted the Gold schema/scorer, readable human-review export and one
completed 20-sample manual review as this task's closure boundary. Remaining
unchecked work below is retained as historical deferred scope, not claimed as
completed. Trusted-section and channel-save remediation moved to
`08-19-trusted-response-boundary`; detailed results are in `validation.md`.

## Completed implementation slice

- [x] Added `evals/natural_language/quality.py` with strict human Gold schema,
      completeness gate, deterministic Recall/MRR/citation/timestamp scorer,
      and separate no-evidence false-positive metrics.
- [x] Added `human_review.py` with validated rubric records and aggregation
      that excludes pending/disputed reviews from rates.
- [x] Added `--validate-human-samples --human-samples PATH` and documented the
      authoring/validation flow; draft samples may contain only a reference
      answer and top-level fixture alias, while the explicit strict gate checks
      complete Gold evidence. Existing catalog and live evaluator behavior
      remains compatible.
- [x] Recorded the fixed video's public chapter anchors in
      `research/qz9tKlF431k-public-chapters.md`; these remain candidate ranges
      until local transcript segments can resolve them.

## 1. 盘点和标注协议

- [ ] 运行 catalog 校验，确认现有 case 数量、分类、smoke 集和缺失 fixture；先以独立评测环境执行 `python -m evals.natural_language --all --repeat 1`，保存 pass/fail/skip、延迟和 tool trace。
- [x] 选定 20 条 retrieval 样本及固定视频，建立人工标注表；每条补齐 query、`gold_item_id`、`gold_segment_ids`、时间范围、误差、问题类型和证据组。
- [ ] 写出 segment key/fixture alias 到本次真实 item/segment ID 的解析规则，并记录标注依据；无法稳定重建的样本先标记为待办，不进入质量分母。

## 2. Gold schema 和 resolver

- [x] 在 `evals/natural_language` 增加严格 Pydantic/YAML schema、版本号、重复 ID/空范围/非法时间/未知 case 校验。
- [ ] 将稳定 fixture alias 解析为运行时 ID，区分“未标注”“fixture 不可用”“解析失败”和“无证据”；为每种情况提供可读但不泄露正文的错误原因。
- [x] 增加标注规则和样例文档，确保新增 query 不需要修改 runner 的安全契约。

## 3. 检索、引用和任务评分

- [x] 在 evaluator 中采集安全的检索排名和最终引用投影：resolved item/segment ID、call order 和 start；不保存摘录、问题、回答或 tool 原始载荷。
- [x] 实现 Recall@1/3、MRR、Citation Precision、Citation Completeness、Timestamp Hit Rate 和 no-evidence false-positive scorer；补充 macro 聚合、空检索/skip/unscorable 分母处理和合成输入单元测试。
- [x] 扩展报告摘要，独立输出 policy pass、task success、citation validity、conversation state pass、safety violation，并保留每个 case 的失败原因和 trace。
- [ ] 确认多轮 case 只有最终目标和证据都满足时才算 task pass；保留安全关键 tool 零容忍和 exact URL scope 断言。

## 4. 人工评测

- [x] 由样本作者在预置的 20 条固定问题上补齐 `reference_answer`，保留 sample/case/turn/kind/fixture 元数据。
- [x] 根据远端只读 transcript 整理 gold segment、时间范围、事实点、答案边界和 evidence groups，并校验 no-evidence 规则。
- [x] 实现显式 opt-in 的样本导出与评分导入；本地包可查看回答/引用，但 sanitized 发布报告不包含正文。
- [x] 固化 `human_eval_review_template.yaml` 的六项 0/1 rubric、`null`/`disputed` 缺失评分处理和 accepted-only 聚合公式。
- [x] 增加人工评测操作说明和 ignored `.eval-results` 保留边界；不得把评分正文或未审核数字提交仓库。

## 5. 可靠性、性能和失败注入

- [ ] 增加固定 fixture 下的并发 1/5/10 基线命令，计算 p50/p95、平均模型/tool 调用、loop 超限率、入库成功率、重试后成功率、重启恢复率和 outbox 投递/重试。
- [ ] 建立 bounded failure-injection harness，覆盖 worker 中断、Redis、MinIO、embedding、notification consumer、MCP session；为每项验证保留任务、状态/错误码、重试和无幽灵数据。
- [ ] 确保注入场景隔离专属评测用户，teardown 可恢复 fixture，失败本身不会污染后续 case。

## 6. 验证和发布门槛

- [x] 运行 Gold schema/scorer、现有 evaluator 和真实 stdio protocol-clean 回归测试。
- [ ] 在独立环境实际运行完整目录和高风险 case 的 repeat 3；报告真实 pass/fail/skip 和失败样本，不在代码或简历中预填结果。
- [x] 审核 sanitized 报告隐私：无问题、回答、tool 参数/结果、URL、秘密或 provider 错误正文泄露；`.eval-results` 不纳入版本控制。
- [x] 更新 `evals/natural_language/README.md`，记录验证、完整 Gold benchmark 和 bounded `--human-case` 命令。
- [ ] 发布前检查报告 schema 版本、gold 数据版本、fixture 版本和运行模型/provider，保证结果可复现和可比较。

## Review Gates

- 在第一阶段 gold schema、样本覆盖和解析规则通过人工复核前，不接入发布门槛。
- 在真实模型完整目录成功产出报告前，不写任何准确率、引用率、时间戳命中率或 p95 数字。
- 若质量投影破坏现有报告隐私、tool trace 配对、租户隔离或安全断言，立即回退到仅行为契约的现有 evaluator。

## Latest verified live baseline

- Run: `20260818T122606Z-b5bc3410`, 20 complete Gold samples, repeat 1,
  concurrency 1, `openai:deepseek-v4-flash` through the configured
  OpenAI-compatible provider.
- Result: 20 `pending_review`, 0 automatic pass/fail/skip. Every answer and
  citation set was recorded in ignored local YAML and readable Markdown.
  Only an accepted human `verdict: pass|fail` contributes to task success.
- Non-verdict Gold diagnostics: Recall@1 41.2%, Recall@3 41.2%, MRR 42.5%,
  citation precision 8.5%, citation completeness 12.7%, timestamp hit 23.5%.
- Negative-set diagnostics: retrieval false positive 100%, citation false
  positive 33.3% (n=3). These do not assign a verdict.
- Serial performance: p50 35,341 ms, p95 44,415 ms, 4.65 model calls and
  3.00 completed Agent tool calls per attempt. Safe diagnostics recorded 7/20
  loop-limit failures (35%); these remain observations for the reviewer.
- The earlier recovered run `20260818T105649Z-054b17b7` has explicit user
  verdicts `he-004`, `he-007`, and `he-008` = pass; its other recovered
  records remain pending. Verdicts are not copied to a new stochastic run.
- Still open: full 22-case mutation-capable catalog run, repeat-3 high-risk
  cases, concurrency 5/10, and bounded failure injection. These numbers are a
  phase-one retrieval/answer baseline, not completion of all four benchmark
  classes.
