# 建立带 Gold Evidence 的 Agent 质量 Benchmark

## Goal

将现有自然语言行为契约评测扩展为可验证检索质量、任务成功率、人工回答质量和可靠性/性能基线；第一阶段优先为 15–20 条检索样本加入 gold item/segment/timestamp，并保留真实模型 tool trace 与失败样本。

## Task Map

本任务是统一工作入口。Benchmark 本身仍负责评测数据、runner、报告和人工 review；由评测暴露出的产品缺陷保留为可独立规划、实施和验收的子任务：

- `08-18-scoped-search-returned-item`：修复限定检索只能使用本轮已返回条目的问题；已归档完成。
- `08-18-answer-agent-recovery`：收敛回答 Agent 的引用选择与失败恢复；已归档完成。
- 人工 review 暴露的可信 Section 与 channel-save 后续方案已移交 `08-19-trusted-response-boundary`，不再作为 benchmark 子任务继续开发。

子任务自己的 PRD、设计和验收条件是产品实现的事实源；父任务只负责把它们的结果纳入同一套真实模型 trace 与人工评测，不在父 PRD 中重复实现细节。

## Requirements

- **R1. 保留现有行为契约基线。** 现有 `evals/natural_language/catalog.yaml` 的 22 个 case 继续覆盖 retrieval、save、inventory、context、conversation、safety；不得用新的质量断言替换 required/allowed/forbidden tools、状态、引用存在性、重启和租户安全断言。完整目录运行结果必须区分 `pass`、`fail`、`skip`，并保留每个 turn 的 tool trace、耗时和失败原因。
- **R2. 建立 Gold Evidence 语料。** 为第一阶段至少 15 条、目标 15–20 条固定视频查询建立人工标注文件。每条样本至少包含 `query`、`gold_item_id`、`gold_segment_ids`、`gold_timestamp_range`；还要标明问题类型（关键词、同义改写、跨片段、前后文、跨语言、无证据）和允许的时间戳误差。固定视频必须能由现有 fixture 或稳定平台标识重建，不能依赖某次运行临时生成的数据库 ID。
- **R3. 可解释的检索和引用指标。** 对有证据样本计算 Recall@1、Recall@3、MRR、Citation Precision、Citation Completeness、Timestamp Hit Rate；对无证据样本单独计算 false-positive/错误引用。检索结果排名与最终回答引用要分开评分，不能把“有引用”当作“引用正确”。指标定义、分母、空集处理和样本缺失处理必须写入报告。
- **R4. 任务结果与策略分离。** 报告同时给出 Task Success Rate、Tool Policy Pass Rate、Citation Validity、Conversation State Pass Rate、Safety Violation Rate，并能定位“工具策略通过但任务/证据失败”的 case。多轮任务只有在最终用户目标、状态迁移和 gold evidence 均满足时才算 task pass；安全关键工具越权仍为零容忍。
- **R5. 人工回答质量集。** 先由样本作者只填写预置 20 条问题的 `reference_answer`；`case_id`、`turn_index`、`kind` 和 fixture 元数据由任务预填，segment/timestamp 证据在后续实现阶段补齐。模型输出和引用保存为受控本地评测副本，由人工按统一 0/1 rubric 评分：回答是否解决问题、是否只用知识库证据、视频是否正确、时间戳是否可定位、是否有无依据陈述、语气/引导是否合理。LLM-as-a-judge 可以作为探索性信息，但不能成为发布门槛或简历数字的唯一来源。人工样本填写规范见同目录 `human-eval-data-contract.md` 和 `human_eval_samples.yaml`。
- **R6. 可靠性和性能基线。** 在固定 fixture、专属评测用户和真实模型下记录单轮请求 p50/p95 延迟、平均模型调用次数、平均 tool 调用次数、Agent loop 超限率、入库成功率、重试后最终成功率、进程重启恢复率，以及 notification outbox 投递成功率/重试次数。先提供并发 1、5、10 的小规模基线，不做未经授权的大规模压测。
- **R7. 失败注入覆盖。** 为 worker 中断、Redis 暂不可用、MinIO 获取失败、embedding 超时、notification consumer 重启、MCP session 重启建立可重复且有界的 case；检查任务保留、`failed`/`retryable` 状态、错误码、重试能力和无幽灵数据。失败注入不能污染共享 fixture，必须有清理/恢复步骤。
- **R8. 运行、安全和可复现性。** 真实模型评测继续遵循当前 preflight：非 production、专属用户、当前 migration、完整 MCP profile 和临时 grant。默认不把问题、回答、tool 参数/结果或敏感 ID 写入发布报告；人工评测副本必须显式 opt-in、仅本地保存且不提交仓库。没有实际运行的数据不得写成通过率、准确率、p95 或简历指标。
- **R9. 分阶段交付。** 第一阶段先完成 Gold Evidence schema、15–20 条标注和 Retrieval/Citation scorer；第二阶段接入现有 22-case 的任务/状态汇总；第三阶段加入人工 rubric 和性能/失败注入 harness。每阶段都可独立运行并输出带版本号的报告。

## Acceptance Criteria

- [ ] `gold_evidence.yaml`（或等价版本化数据文件）通过严格 schema 校验，至少包含 15 条固定查询，覆盖直接关键词、同义改写、中文问英文视频、跨片段、前后文和知识库不存在六类；每条有效样本具备 item、segment、timestamp 标注和误差规则。
- [ ] Gold item/segment 能从稳定 fixture 标识解析到本次运行的真实 ID；解析失败的样本明确 `skip` 并写明原因，不静默计入分母。
- [ ] 对合成的已知排名/引用输入，Recall@1/3、MRR、Citation Precision/Completeness、Timestamp Hit Rate 的单元测试得到预期结果；真实运行报告展示每个 case 的分子、分母和聚合值。
- [ ] 现有 `python -m evals.natural_language --all --repeat 1` 仍能运行，报告同时展示行为契约结果和质量指标；至少能区分 policy pass、task pass、citation invalid 和 safety violation。
- [ ] 至少一次真实模型完整目录运行产出 pass/fail/skip、分类汇总、tool trace、耗时和失败原因；任务未实际运行前，文档和简历不声称 22/22、准确率、引用率或 p95。
- [ ] `human_eval_samples.yaml` 已预置至少 20 条固定问题，样本作者只需补齐每条 `reference_answer`；预置元数据包含唯一 ID、query/case/turn、fixture 和样本类型。
- [ ] 人工评测导出包含上述固定样本、脱离运行顺序的样本 ID、回答/引用快照和六项 0/1 rubric；发布报告不泄露这些正文，且至少能计算回答正确率、引用正确率、时间戳命中率和无依据陈述率。
- [ ] 并发 1/5/10 基线报告包含 p50/p95、模型/tool 调用次数、loop 超限率和重试后结果；至少一个失败注入 case 验证重试/恢复且无幽灵数据。
- [ ] 失败注入、重启、fixture 清理和结果写入均有自动化或可重复命令；所有新测试通过，现有安全边界、租户隔离和 citation allow-list 测试不回归。

## Out of Scope

- 父任务的直接实现不重写 Agent 的检索、引用、渠道保存或安全策略；这类产品修复只在对应子任务中实施，benchmark 负责测量和复验。
- 不做大规模容量压测、长期 soak、跨 provider 排名结论或把 LLM-as-a-judge 作为权威分数。
- 不把人工评测正文、真实用户数据、provider 原始响应或未验证的性能数字提交到仓库。
- 本任务按用户确认以 Gold + 20 条人工 review 阶段收口；并发 5/10、failure injection 和完整 mutation catalog rerun 延后，详见 `validation.md`，归档不代表这些延后项已完成。

## Notes

- Keep `prd.md` focused on requirements, constraints, and acceptance criteria.
- Lightweight tasks can remain PRD-only.
- For complex tasks, add `design.md` for technical design and `implement.md` for execution planning before `task.py start`.
