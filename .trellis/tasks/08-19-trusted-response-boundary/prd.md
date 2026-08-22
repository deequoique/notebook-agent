# 统一 Agent 可信响应与渠道动作边界

## Goal

以已完成的 20 条人工评测为事实输入，统一 Agent 最终响应的信任边界：区分检索候选与真正支持答案的证据，区分模型撰写 section 与服务器生成 section，并让 grounded answer、no-evidence、能力说明和保存 Action 通过同一套可验证合同组装。

当前交付是整理并审核实现方案。方案通过 review gate 后才进入业务代码开发。

## Confirmed Evidence

- `he-011`、`he-019`、`he-020` 属于 no-evidence，但当前 `AnswerDraft` 强制至少选择一个 segment、每个 section 强制至少一个 Citation，最终又无条件生成来源区块。
- `he-003` 的 gold 片段没有进入检索 top 3，随后 Answer Agent 三次以笼统的 `invalid_citation` 失败；retrieval miss 与 answer-contract failure 必须分开。
- channel-save 中链接能力说明、pending save 确认和普通模型回答走不同的扁平文本路径，validator 无法根据 provenance 判断 URL、来源或 Action 是否由服务器拥有。

事实来源为已归档 benchmark 的 `validation.md`、人工 review 汇总和 trusted-section findings。

## Requirements

- **R1. 候选不等于证据。** 检索返回的 segment 只是 candidate；只有 Answer Agent 选择并通过 allow-list/scope 校验的 segment 才能进入最终 Citation、持久化 sources 和可见来源。
- **R2. 显式 disposition。** 检索回答必须区分 `grounded` 与 `no_relevant_evidence`。grounded 至少有一个带 Citation 的模型 section；no-evidence 不允许模型正文、segment ID、Citation 或来源，由服务器生成固定文案。
- **R3. Section 所有权可判定。** 内部使用 discriminated type 区分模型 grounded section、服务器 canonical notice 和服务器 Action outcome。模型不能构造或伪装 server-owned section。
- **R4. 单一 Citation 事实源。** 删除 `selected_segment_ids` 与 section union 的重复真值。服务器从 grounded section Citation union 派生最终 ID 集，再校验合计最多 8 个 segment、5 个视频、当前轮 allow-list、重复和 scope。
- **R5. 来源按 disposition 渲染。** 只有 grounded 响应可以根据最终选中 Citation 派生来源。no-evidence、canonical、Action 和 failed 不得因为本轮曾返回候选而附加来源。
- **R6. 保留单主 Agent 运行时。** 主 Turn Agent 的合法 cited answer 继续直接交付，不把每次检索都强制改成第二次 Composer 调用。成功检索后必须有显式 server-owned `no_relevant_evidence` disposition/tool；主回答缺 Citation 或校验失败时，现有 tool-free Answer Agent 使用 grounded/no-evidence 判别联合进行最多三次恢复。
- **R7. 可解释失败。** 诊断区分 retrieval miss、selection miss、invalid structure、unknown/duplicate Citation、segment/item 超限、provider failure 和 answer unavailable；显式范围外或伪造 ID 在不暴露范围信息的前提下统一 fail-closed 为 `unknown_citation`；不得记录问题、草稿、URL 或 excerpt。
- **R8. Channel-save 复用合同。** 链接能力说明使用服务器 canonical section；自然保存提议必须先持久化 pending action，再返回服务器 Action/canonical section；普通模型 URL 禁令不变。
- **R9. 外部兼容。** 第一阶段对外仍可投影为现有 `AgentAnswer.text/citations/action_results`，但必须来自统一内部 envelope，不能继续在 orchestrator 分支手工拼字符串。
- **R10. 人工评测权威。** 修复后只运行一次聚焦 human review，保存 Agent 回答和 tool trace，由人工决定 pass/fail；自动 Gold 只做诊断。

## Task Map

- 父任务直接负责 ResponseEnvelope、AnswerDecision、section provenance、Citation 选择/渲染以及聚焦复验。
- 子任务 `08-18-channel-save-link-routing` 在共享合同上实现链接能力说明、结构化引用框 SaveTargetSet、真实 `offer_video_save`、Bilibili worker 和短链。
- 已归档 benchmark、scoped-search 和 answer-agent-recovery 是证据与实现基线，不在本任务中改写历史。

## Acceptance Criteria

- [ ] no-evidence schema 无法携带 sections、selected IDs 或 Citation；grounded schema 无法省略带 Citation 的 section。
- [ ] `he-011`、`he-019`、`he-020` 回归返回 no-evidence，正文无 `[S…]`，`citations=[]`，不渲染“来源”。
- [ ] grounded 最终 Citation 只从 section union 派生；跨 section 合计最多 8 个 segment、5 个视频，unknown、duplicate、超限分别得到准确诊断。
- [ ] `he-003` 能分开显示 gold 未进入候选与回答合同失败；候选不相关时可以安全 no-evidence，不强迫选择无关 segment。
- [ ] 检索成功但候选均不相关时，不把 candidate 非空直接解释成存在可引用证据。
- [ ] 正常合法 cited answer 不额外调用 Answer Agent；只有显式 no-evidence、主回答校验失败或 primary failure with candidates 才进入相应可信终态/恢复路径。
- [ ] “支持哪些 Bilibili 链接”通过服务器 canonical section 返回合法示例，不触发 `model_url`；普通模型自行输出 URL 仍失败关闭。
- [ ] Bot 显示“要我保存吗”前 active pending 已提交；下一条确认只入队一次，不依赖模型历史 URL。
- [ ] terminal Action、canonical、grounded、no-evidence 和 failed 通过同一 envelope 投影；来源只在 grounded 分支生成。
- [ ] 聚焦自动化通过后执行一次真实模型 human-review 子集；最终判断由人工填写。

## Out of Scope

- 不继续扩 benchmark 并发 5/10、failure injection 或新指标；benchmark 已独立归档。
- 不在本任务中修改 embedding、索引或大规模 reranker；`he-003` retrieval miss 只要求正确分类并保留给后续检索优化。
- Bilibili connector、`b23.tv` 和 quote 的领域实现归 channel-save 子任务；父任务只定义共享可信响应合同。

## Notes

- 本任务保持 `planning`，等待用户确认方案后再 `task.py start`。
