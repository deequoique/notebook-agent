# 实施计划

## 子 Agent 执行顺序

1. 更新主 Agent 静态和动态 instructions，明确视频内容问答必须先调用 `search_segments`。
2. 在 prompt 中排除库存、保存、删除、恢复和确认等专用管理工具路径。
3. 收紧澄清 prompt：指出缺失信息并给一个短示例，不输出空泛“请补充信息”。
4. 运行现有 Agent、mixed evidence 和 Citation-first streaming 回归测试。

## 必须覆盖的测试

- 主 Agent prompt 对视频内容问答要求调用 `search_segments`。
- instruction 文本明确包含“即使知道常识答案也先搜索视频资料库”。
- prompt 明确排除库存和 mutation/pending 管理操作。
- 必要澄清指出缺失信息并给一个短例子。
- 现有搜索、no-evidence、grounded/unsupported 和 provider streaming 测试保持通过。

## 验收命令

```bash
.venv/bin/pytest -q \
  tests/test_agent_runtime.py \
  tests/test_bounded_autonomy_runtime.py \
  tests/test_mixed_evidence_grounded_flow.py \
  tests/test_citation_first_provider_streaming.py \
  tests/test_conversation_streaming.py

.venv/bin/python .trellis/scripts/task.py validate \
  08-22-evidence-first-routing-agent-self-knowledge
git diff --check
```

最后运行完整 Python 套件，并单独记录既有环境失败。

## Review Gate

- 不新增模型调用、工具、状态、数据库列、API 或前端协议。
- 不修改零检索 finalization，不新增服务器 gate、强制重试或 search-required enforcement。
- 只修改 `app/agent/agent_builder.py` 的 prompt，不修改其他业务代码或测试契约。
- 不改变现有 no-evidence 和 mixed-evidence 语义。
- 不把自知识需求偷偷带回本任务。
