# Agent 框架候选清单

调研日期：2026-08-05

## 选型硬条件

- Python 3.11 项目可直接使用。
- 模型供应商可替换，能接多个 provider、router 或 OpenAI-compatible gateway。
- 自定义只读工具能够复用现有 SQLAlchemy + pgvector 检索层。
- Agent 核心与 CLI、Web、浏览器扩展、微信等 channel adapter 解耦。
- 能限制模型调用轮数、处理工具错误，并支持不调用真实模型的单元测试或可替换 model client。

## 候选总览

| 框架 | 最适合的方向 | 多 provider / gateway | 多渠道与未来扩展 | 主要代价 | 本项目定位 |
|---|---|---|---|---|---|
| **PydanticAI** | 强类型、单 Agent、Python 产品后端 | 原生多 provider、OpenAI-compatible endpoint、fallback model | 有 AG-UI；推荐仍使用自己的 channel contract | 生态小于 LangChain；深度观测默认偏 Logfire | **优先调研** |
| **LangChain `create_agent` + LangGraph** | 大生态、复杂状态、长期工作流 | 多 provider package；OpenRouter/LiteLLM；自定义 base URL | LangGraph persistence/streaming/HITL，适合未来复杂编排 | 依赖和抽象多；P1 可能过重 | **优先调研** |
| **Agno** | 快速搭完整 Agent 平台、知识库与多 Agent 产品 | 官方列出 40+ model providers，含 LiteLLM/OpenRouter/Portkey 等 | AgentOS、teams、workflows、storage、interfaces 较完整 | 与现有 pgvector/ingest/retrieval 能力重叠较多 | **优先调研** |
| **Microsoft Agent Framework** | 企业级、多 Agent、MCP/A2A、显式工作流 | OpenAI、Azure、Anthropic、Ollama、Foundry 等，支持自定义 provider | sessions、middleware、telemetry、checkpoint、AG-UI/A2A | 框架较新且偏微软/Foundry生态；P1 较重 | 第二梯队 |
| **OpenAI Agents SDK** | 简洁工具循环、OpenAI tracing、OpenAI Responses | 支持 custom `ModelProvider`、OpenAI-compatible endpoint 和第三方 adapter | channel 层需自建；sessions/tracing 强 | OpenAI-first；非 OpenAI 后端存在功能差异 | 对照候选 |

## 1. PydanticAI

### 值得调研的原因

- 模型、provider、工具依赖和最终输出都有明确类型。
- 原生支持 OpenAI、Anthropic、Gemini、Bedrock、OpenRouter 等，也支持任意 OpenAI-compatible `base_url`。
- `FallbackModel` 可以跨模型/provider 顺序回退。
- `TestModel` / `FunctionModel` 能在 pytest 中确定性测试工具循环，并可全局禁止真实模型请求。
- 有 AG-UI adapter，但不会强迫项目采用特定前端。

### 重点验证

- 同一个 Agent 在 OpenAI、Anthropic、OpenRouter 三类 provider 之间切换时，工具 schema 是否完全不改。
- structured output、streaming 和 fallback 在目标模型上的行为是否一致。
- 不使用 Logfire 时，本地 OpenTelemetry/结构化日志是否足够。

官方入口：

- [PydanticAI overview](https://pydantic.dev/docs/ai/overview/)
- [Models and providers](https://pydantic.dev/docs/ai/models/overview/)
- [Testing](https://pydantic.dev/docs/ai/guides/testing/)
- [AG-UI adapter](https://pydantic.dev/docs/ai/api/ui/ag_ui/)

## 2. LangChain `create_agent` + LangGraph

### 值得调研的原因

- LangChain v1 的 `create_agent` 是较高层 Agent API，运行时建立在 LangGraph 上；不必一开始手写 StateGraph。
- provider integration 数量最多，模型、embedding、vector store、toolkit 生态完整。
- 支持 provider:model、独立 provider package、OpenRouter、LiteLLM 和 OpenAI-compatible endpoint。
- 当以后需要持久对话、checkpoint、人工审批或复杂分支时，可以逐步下沉到 LangGraph。

### 重点验证

- 最小依赖集需要多少包，升级时 provider packages 是否容易发生版本联动。
- 是否能完全复用现有 retrieval 层，而不是被 LangChain retriever/document 抽象反向改造。
- LangSmith 是否只是可选观测，不使用它时 trace/eval 怎么落地。

官方入口：

- [LangChain agents](https://docs.langchain.com/oss/python/langchain/agents)
- [Providers and models](https://docs.langchain.com/oss/python/concepts/providers-and-models)
- [LangGraph overview](https://docs.langchain.com/oss/python/langgraph/overview)

## 3. Agno

### 值得调研的原因

- 同时覆盖 Agent、Team、Workflow、memory、knowledge、storage 和 AgentOS，较接近完整产品平台。
- 官方示例覆盖 40+ 模型 provider、18 个 vector database 和大量 tools。
- 模型层包含 OpenAI、Anthropic、Google、Ollama、LiteLLM、OpenRouter、Portkey、vLLM 等。
- 如果未来想快速提供多 Agent、后台运行、统一服务接口和运营能力，Agno 的现成面较多。

### 重点验证

- 能否把 Agno 只当 Agent runtime，而不引入它自己的 knowledge/vector store 层。
- AgentOS 是否会与现有 FastAPI/Celery/Postgres 运行架构重叠。
- 单元测试是否能像 PydanticAI 一样方便地替换确定性模型。

官方入口：

- [Agno overview](https://docs.agno.com/)
- [Agents](https://docs.agno.com/agents/overview)
- [Models](https://docs.agno.com/examples/models/overview)
- [Compatibility](https://docs.agno.com/models/compatibility)

## 4. Microsoft Agent Framework

### 值得调研的原因

- 把原 AutoGen 的 Agent 抽象与 Semantic Kernel 的企业能力统一到一个框架。
- 提供 sessions、middleware、telemetry、MCP、A2A、多 Agent orchestration 和 checkpoint workflow。
- 支持 OpenAI、Azure OpenAI、Anthropic、Ollama、Foundry Local 等 provider，并允许自定义 provider。

### 重点验证

- Python API 的成熟度和稳定性；部分 workflow API 仍可能处于 experimental 状态。
- 非 Azure/Foundry 部署是否足够轻量。
- 对当前单 Agent 检索任务是否引入了不必要的企业平台复杂度。

官方入口：

- [Microsoft Agent Framework overview](https://learn.microsoft.com/en-us/agent-framework/overview/)
- [Providers](https://learn.microsoft.com/en-us/agent-framework/agents/providers/)
- [Workflows](https://learn.microsoft.com/en-us/agent-framework/workflows/)

## 5. OpenAI Agents SDK

### 值得作为对照的原因

- 单 Agent 工具调用循环、session、guardrail 和 tracing 比较直接。
- 不只支持 OpenAI：可以在全局、每次 run 或每个 Agent 注入自定义 model/provider，也可接 OpenAI-compatible endpoint 和第三方 adapter。

### 为什么不是首选

- 整体仍是 OpenAI-first；Responses-only 工具在非 OpenAI provider 上不可用。
- 混合 provider 时必须逐项核对 structured output、tool calling、tracing 等差异。
- 你的硬条件是供应商可替换，因此它更适合当实现复杂度和 tracing 体验的对照组。

官方入口：

- [OpenAI Agents SDK](https://developers.openai.com/api/docs/guides/agents)
- [Non-OpenAI models](https://openai.github.io/openai-agents-python/models/litellm/)

## 建议的调研顺序

1. **PydanticAI**：先看它能否以最少代码满足当前 P1。
2. **LangChain/LangGraph**：判断未来持久工作流的价值是否值得当前复杂度。
3. **Agno**：判断你是否想要的不只是框架，而是完整 Agent 平台。
4. Microsoft Agent Framework：如果未来明显偏企业/MCP/A2A，再深入。
5. OpenAI Agents SDK：作为 OpenAI-first 的体验与复杂度基线。

## 建议统一做的最小 PoC

不要分别照着各框架的天气示例判断。给每个候选完全相同的实验：

1. 注册 `search_segments` 和 `get_neighbors` 两个只读工具。
2. 输入同一个自然语言问题，要求必须检索后回答并给出 segment 引用。
3. 把模型从 OpenAI 切到另一个 provider/gateway，不改工具和输出类型。
4. mock 第一次检索为空，让 Agent 改写查询后再检索一次。
5. mock 429/timeout，验证停止或 fallback 行为。
6. 在 pytest 中禁止真实模型请求，检查工具调用轨迹和最终结构化答案。

比较指标：代码行数、依赖数量、provider 切换改动、测试难度、trace 可读性、首次响应延迟、完整运行耗时。

