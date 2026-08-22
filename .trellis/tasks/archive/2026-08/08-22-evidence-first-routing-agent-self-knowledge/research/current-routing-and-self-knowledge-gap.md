# 当前路由与 Agent 自知识缺口

> 范围更新：本任务仅通过 prompt 要求主 Agent 对实质性问题先调用 `search_segments`。
> 用户明确不要求服务器零检索兜底、强制重试或 `search_required` enforcement。
> 下文其他架构建议只保留为历史研究，不进入本任务实现。

## 现场复现

2026-08-22 本地 `dev` 运行时，用户询问“为什么欧洲人不装空调”。页面一次性显示完整
Markdown 回答，没有 Citation 卡片，也没有 section 增量。

对应安全日志显示该请求进入 Agent，但没有 `search_segments` tool call。主 Agent 直接生成自然
文本，服务器在 `search_calls < 1` 分支仅执行文本安全检查，随后接受为：

```text
status=ok
citations=[]
text=<完整 Markdown 常识回答>
```

`KnowledgeAgent.stream()` 只有在 `deps.citations` 非空时才进入 Citation-first section stream，
因此该回答只能走 one-delta compatibility path。已有测试
`test_agent_rejects_model_answer_that_skips_retrieval` 正在失败，它不是无关基线，而是现场问题的
准确回归信号。

## 根因

当前主 Agent prompt 同时允许：

- 问候、感谢、能力询问、澄清和普通交流直接回答；
- 私有知识问题自主选择检索工具。

服务器却没有一个可信的“非知识 disposition”契约。只要模型跳过检索，并且文本没有 URL、
Citation marker 或来源块，`validate_natural_answer()` 就可能接受任意长常识回答。文本安全检查
只能证明“没有伪造来源格式”，不能证明“这个问题无需检索”。

## 产品决策

### 已确定

1. 实质性问题必须先尝试 tenant-bound 检索。
2. 普通常识路径不能在检索前由模型自由选择。
3. `no_evidence` 是服务器状态，不是模型 tool。
4. 有候选时进入 grounded plan；服务器校验 Citation 当前 tenant/run 授权，不做语义事实 verifier。
5. Agent 自身架构与运行状态不能依赖模型训练记忆。

### 已确认：显式常识补充

当检索候选为空或计划结果全部 unsupported 时，采用：

1. 先说明知识库无法确认；
2. 再单独输出明确标注、无 Citation 的模型常识；
3. 协议使用显式 `general_knowledge` state；
4. mixed evidence 仍只使用 grounded + unsupported，不在同一回答混入常识补充。

该状态需要贯穿协议、UI、历史和评测，绝不能混入 grounded section 或来源卡片。

## 自知识建议范围

自知识应拆成两类来源：

### 版本化静态知识

- 产品用途与支持的渠道；
- 主 Agent、retrieval、Composer、Citation、stream、Channel 和 persistence 的职责；
- 常见公开错误码及用户可以采取的安全动作；
- 明确说明不能访问或执行的能力。

### 服务器生成的动态快照

- 当前启用的公开能力；
- 依赖 readiness 的安全枚举，不含主机、端口、凭据和异常详情；
- 当前 conversation 最近一次请求的 route/search/candidate/section/stream/fallback/error 摘要；
- 摘要必须受 tenant + thread 绑定、TTL 和字段 allow-list 保护。

模型只负责把上述结构化事实解释给用户，不能自行声明“Redis 正常”“刚才检索过”或“当前使用
某 provider”。
