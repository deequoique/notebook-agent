# Agent 技术选型调研（第一轮）

调研日期：2026-08-05

## 项目约束

- Python 3.11 + SQLAlchemy + PostgreSQL/pgvector。
- 现有 retrieval 层已经提供向量与词法检索；Agent 不应生成 SQL，只能调用封装后的只读工具。
- P1 只有一个 Agent、4 个左右的只读工具、一次运行通常在秒级完成。
- P1 不需要多 Agent、写操作审批、长期用户记忆或复杂分支图，但需要把有界对话历史持久化并在 runtime 重启后恢复。
- 必须能在 pytest 中用确定性模型替身验证工具循环，不能让单元测试调用真实模型。

## 候选比较

| 候选 | 与本项目匹配的能力 | 主要代价/错配 | 初步结论 |
|---|---|---|---|
| **PydanticAI** | Python 原生；工具、依赖和最终输出强类型；内建 Agent 工具循环；支持多个模型供应商和 OpenAI-compatible 端点；提供 `TestModel` / `FunctionModel` 和禁止真实模型请求的测试开关 | 新增一套框架依赖；可观测性深度更偏向 Logfire 集成 | **当前首选**，尤其适合保持模型可替换和确定性测试 |
| **OpenAI Agents SDK** | SDK 管理 Agent loop；函数工具；会话、guardrail、最大运行边界；内置 tracing 可记录模型/工具/guardrail/handoff | 最顺滑路径以 OpenAI 平台为中心；当前 P1 不需要 handoff、approval 等较多能力，且不能接受 OpenAI-only 绑定 | 若产品明确接受 OpenAI-only，才作为替代 |
| **直接使用 Responses API** | 依赖最少；完全控制工具 schema、调用循环、停止条件和响应状态 | 需要自行实现多轮 tool-call loop、错误映射、测试替身、运行记录与后续会话策略 | P1 规模虽小，但维护成本没有带来足够产品价值，不推荐 |
| **LangGraph** | durable execution、checkpoint、streaming、human-in-the-loop、持久状态和复杂图编排 | P1 只需普通对话 turn 持久化，不需要恢复执行到一半的图节点；引入图编排仍然过重 | **暂不采用**；未来出现可恢复长任务或复杂审批图时再评估 |

## 已确认的产品决策

- Agent 模型层必须保持供应商可替换。
- 架构需要支持多个 provider / gateway，并为后续多平台输入输出保留扩展边界。

因此 OpenAI-only 方案不适合作为默认路线。**PydanticAI 是当前领先候选，但框架尚未定案**；完整 shortlist 及用户调研维度见 `agent-framework-shortlist.md`。是否额外部署 LiteLLM 等独立 gateway，仍需根据 routing/fallback 需求单独判断。

## 当前落地决策

多渠道和私有多用户约束加入后，Agent loop 不能再脱离 gateway 选择。当前采用带停止条件的两阶段路线：

1. 先限时验证 **LangBot Local Agent + 自定义 retrieval tool** 是否可以从可信 platform event/session context 取得 sender identity，并在模型参数之外绑定内部 `AppUser.id`。
2. 若通过，五天首版只运行 LangBot，减少 runtime 数量；若不通过，立即改为 **LangBot 渠道层 + PydanticAI Agent 核心**。
3. 失败时可以研究 Hermes 的 session key、gateway adapter 和 tool context 源码作为实现参考，但不能用 prompt identity 或模型可写的 `user_id` 绕过边界。

无论走哪条路径，都把领域工具保持为普通 Python service：

```text
app/retrieval/*                 现有数据库检索能力
        ↑
app/agent/services.py           框架无关的只读领域函数
        ↑
app/agent/tools.py              PydanticAI 工具适配与结构化 schema
        ↑
app/agent/runtime.py            Agent、模型、轮数/usage 限制
        ↑
app/cli.py ask                  薄入口
```

这条边界保证将来即使从 PydanticAI 切换到 OpenAI Agents SDK，也只需替换 `tools.py` / `runtime.py`，数据库查询和返回契约不变。

## 模型初始候选

OpenAI 官方当前将 GPT-5.6 家族分为 `sol`（旗舰能力）、`terra`（质量/成本平衡）和 `luna`（高吞吐/高效率）。本项目工具少、主要难点是查询改写与证据归纳，建议：

- 默认基线：`gpt-5.6-terra` + `low` reasoning。
- 低成本/低延迟对照：`gpt-5.6-luna`。
- 仅当真实知识库评测显示复杂偏好查询明显受益时，再对照 `gpt-5.6-sol`。

最终模型不能只凭家族定位决定；至少使用 10–20 条真实问题比较工具选择正确率、证据完整率、延迟和 token 使用。

## 不随框架变化的设计约束

1. `search_segments` 在应用代码中完成 query embedding 和 pgvector 查询；模型不能接触 SQL。
2. 工具输出为有上限的结构化数据，包含 `item_id`、`segment_id`、标题、正文、定位信息和 score。
3. 知识库问答必须至少出现一次成功检索调用。该规则由运行后校验执行，不能只写在 prompt 里。
4. 设置最大模型请求数、工具超时和结果条数；空结果允许 Agent 改写后再查，但重试次数有上限。
5. 单元测试使用确定性模型替身；真实模型只用于显式的端到端验收。
6. P1 保存受 turn 数和 token budget 限制的会话历史，并支持 runtime 重启恢复；不抽取用户画像或做跨会话语义长期记忆。
7. 微信、Telegram 等 channel adapter 必须能够同时运行；渠道生命周期、消息路由和故障隔离不能依赖模型 provider。

## 官方资料

- [OpenAI Agents SDK 指南](https://developers.openai.com/api/docs/guides/agents)
- [OpenAI Function Calling 指南](https://developers.openai.com/api/docs/guides/function-calling)
- [OpenAI Agents SDK observability](https://developers.openai.com/api/docs/guides/agents/integrations-observability)
- [OpenAI 当前模型选择指南](https://developers.openai.com/api/docs/guides/latest-model.md)
- [LangGraph overview](https://docs.langchain.com/oss/python/langgraph/overview)
- [LangChain agents](https://docs.langchain.com/oss/python/langchain/agents)
- [PydanticAI Agents](https://pydantic.dev/docs/ai/core-concepts/agent/)
- [PydanticAI Function Tools](https://pydantic.dev/docs/ai/tools-toolsets/tools/)
- [PydanticAI Testing](https://pydantic.dev/docs/ai/guides/testing/)
- [PydanticAI Models and Providers](https://pydantic.dev/docs/ai/models/overview/)

## 后续产品澄清

- 首版必须把短期对话历史持久化，并能在 Agent/channel runtime 重启后恢复；这不等同于长期用户记忆。
- 用户所说的“多网关”指微信、Telegram、Slack 等 channel gateway 同时运行，不是模型 provider 的自动 fallback/routing。
- 模型 provider 保持可替换；自动 fallback/routing 不作为渠道并发验收的前置条件。
