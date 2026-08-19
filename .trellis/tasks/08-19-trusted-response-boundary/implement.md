# 可信响应边界执行计划

## Phase 0 — 方案冻结（当前阶段）

- [x] 读取 benchmark 的 20 条人工 review 与 tool trace。
- [x] 将 `he-011`、`he-019`、`he-020` 的无关来源映射到强制 Citation schema 和无条件 source renderer。
- [x] 将 `he-003` 分为 retrieval miss 与 answer-contract failure 两层。
- [x] 识别 channel-save 的能力回答、自然确认 prose 和 ActionOutcome 与同一 provenance 缺口的关系。
- [ ] 用户 review 本 PRD/design/implement；通过前保持任务 `planning`，不修改业务代码。

## Phase 1 — 合同与纯函数测试

- [ ] 新增 `GroundedDraft | NoRelevantEvidenceDraft` 判别联合。
- [ ] 删除顶层 `selected_segment_ids`，从 grounded sections 派生唯一 Citation 集。
- [ ] 将 unknown、duplicate、segment/item 超限和 unsafe text 分开诊断；显式范围外/伪造 ID 统一按 unknown fail-closed。
- [ ] 新增内部 ResponseEnvelope、grounded/canonical/action section 和 legacy AgentAnswer adapter。
- [ ] 证明 no-evidence 无法携带 Citation/source，canonical/action 无法由模型输出伪造，grounded 跨 section 总量保持 8/5 上限。

## Phase 2 — 主 Agent disposition 与恢复

- [ ] 保留 normal valid cited answer 直出，不给每个成功检索固定增加 Answer Agent 调用。
- [ ] 新增无参数、无副作用的 `report_no_relevant_evidence` response tool/disposition，仅在成功 search 且无 read failure/terminal Action 时接受。
- [ ] zero candidates 与显式 no-relevant-evidence 都生成相同 server canonical envelope，丢弃模型 prose 和所有 candidates。
- [ ] 主回答缺 Citation/校验失败或 primary failure with candidates 时，Answer Agent 使用 grounded/no-evidence 判别联合，保持三次总尝试上限。
- [ ] explicit scope 约束选中 Citation 必须在 scope 内，但不因无关候选存在而强迫引用。
- [ ] search/read unavailable 继续是 retryable failure，不得伪装成 no-evidence。

## Phase 3 — 渲染、持久化和投影

- [ ] 来源区块只对 grounded envelope 生成，且只使用最终 selected citations。
- [ ] conversation sources、ChannelService、MCP 与 CLI 从同一 envelope 投影。
- [ ] 迁移 canonical reads 和 terminal ActionOutcome，移除 orchestrator 中并行的手工拼接。
- [ ] 保持 public response shape、历史可见性和 pending/save 不进入模型历史的现有安全边界。

## Phase 4 — Channel-save 子任务接入

- [ ] `supported_video_links` 与 `save_target_missing` 使用注册 canonical section。
- [ ] `offer_video_save` 只在 pending 提交成功后生成成功 Action section。
- [ ] quote/SaveTargetSet/Bilibili connector/短链统一消费 envelope 与 error catalog，不新增字符串旁路。

## Phase 5 — 聚焦验证

- [ ] `he-011`、`he-019`、`he-020`：无 Citation、无来源、status/disposition 正确。
- [ ] `he-003`：evaluator 能区分 retrieval miss、selection miss 和 answer-contract failure。
- [ ] 回归 grounded、exact URL、多视频、8 segment、5 item 和三次失败行为。
- [ ] 回归普通模型 URL 拒绝、服务器能力 URL 允许、Action 优先级、pending 幂等和租户隔离。
- [ ] 只运行一次聚焦真实模型 human-review 子集；系统记录回答/trace，人工填写 pass/fail。

## Focused validation commands

```bash
.venv/bin/pytest -q tests/test_agent_runtime.py tests/test_exact_video_reference_routing.py
.venv/bin/pytest -q tests/test_natural_language_quality.py tests/test_natural_language_evaluator.py
.venv/bin/pytest -q tests/test_agent_actions.py tests/test_multiuser_integration.py tests/test_mcp_server.py
python3 ./.trellis/scripts/task.py validate 08-19-trusted-response-boundary
python3 ./.trellis/scripts/task.py validate 08-18-channel-save-link-routing
```

## Review gates

- Phase 0 未经用户确认不得开始实现。
- no-evidence 若仍能携带 Citation 或来源，停止迁移。
- grounded Citation 若不是 section union 的唯一事实源，停止 adapter 切换。
- canonical/action section 若能由模型 text 或 tool argument 伪造，失败关闭。
- pending 未证明先于确认问句提交，不能发布自然保存提议。
- 聚焦 human review 未完成前，不宣称问题修复。
