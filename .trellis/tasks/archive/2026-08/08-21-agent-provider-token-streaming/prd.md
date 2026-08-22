# Agent Provider Token-Level Streaming

## Goal

将 Agent provider/runtime 从完整答案调用升级为可安全消费的 token-level 异步流，并接入现有 SSE 事件与前端增量渲染。

## Requirements

- 为 Agent 执行链提供受控的异步增量接口，优先使用当前 PydanticAI 版本支持的
  `run_stream` / `StreamedRunResult.stream_text(delta=True)` 或等价 API；工具调用、
  检索收敛、预算限制和现有错误语义保持不变。
- 仅在 provider 已产生可安全公开的最终回答文本时向 SSE 转发 `text_delta`；不得
  将隐藏推理、系统提示词、工具参数、原始 provider 事件或未经安全投影的 URL/来源
  发给浏览器。
- 增量文本必须经过与非流式路径一致的答案校验、证据/引用边界和最终投影；流中的
  临时文本不能改变最终持久化的 `ConversationTurn`，最终答案仍只提交一次。
- provider 不支持流式、流式中途失败、客户端断开或取消时，必须有确定的回退/终止
  行为：不重复执行同一消息，不留下永久 pending 状态，不把不完整文本当作完成答案。
- 每个公开文本增量仍沿用现有 request/message 标识和递增 sequence；前端能够逐个
  追加并在完成事件时以最终安全响应校正内容。
- 为真实流式、非流式回退、取消/断线、错误、敏感内容过滤和最终答案一致性补充
  自动化测试，并记录不含正文的 provider 流生命周期指标。

## Acceptance Criteria

- [ ] 使用可控的 fake streaming provider 时，一次请求在 `completed` 之前收到至少两
  个有时间间隔的 `text_delta` 事件，前端逐段显示而不是一次性出现。
- [ ] 流式路径最终呈现的完整答案、引用、状态和持久化记录与同一请求的非流式路径一致。
- [ ] Agent 需要工具调用时，工具/检索阶段仍按现有预算和顺序执行；只有安全的最终
  回答增量进入公开 SSE，不出现工具参数、原始事件或隐藏推理。
- [ ] provider 不支持 streaming 时自动采用现有兼容行为；同一 `message_id` 不会因
  fallback 被静默提交两次。
- [ ] provider 错误、超时、客户端断开和取消都会终止/回收流，返回明确终态，且不
  持久化半截答案或让 UI 永久显示进行中。
- [ ] 重复/乱序增量仍按现有客户端协议被去重或拒绝；最终完成事件包含完整安全答案。
- [ ] 后端、前端和 provider fake 测试覆盖上述路径；现有非流式测试和 OpenAPI 契约保持通过。

## Notes

- Keep `prd.md` focused on requirements, constraints, and acceptance criteria.
- Lightweight tasks can remain PRD-only.
- For complex tasks, add `design.md` for technical design and `implement.md` for execution planning before `task.py start`.
