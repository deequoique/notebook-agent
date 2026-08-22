# 技术设计

## 当前链路

当前浏览器 SSE 路由执行一次 `ChannelService.handle()`，等待完整 `AgentAnswer`，然后发送
一条包含完整答案的 `text_delta` 和一条 `completed`。活动状态是流式的，但正文不是
provider-level streaming。

现有 Answer Composer 返回完整 `AnswerDraft`：每个 section 同时包含 `status`、`text` 和
`citation_ids`。如果直接流式解析这个 JSON，Citation 字段与 text 字段的到达顺序依赖
模型和 provider，服务器无法保证 Citation 一定先于正文。因此本设计不依赖 JSON 字段
顺序或不完整 JSON 的启发式解析。

## 核心决策：两阶段 Composer

### 阶段 A：Section/Citation 计划

使用一次短的结构化调用生成 `AnswerStreamPlan`：

```python
class PlannedSection(BaseModel):
    section_id: str
    task: str
    status: Literal["grounded", "unsupported"]
    citation_ids: list[int]

class AnswerStreamPlan(BaseModel):
    kind: Literal["grounded"]
    sections: list[PlannedSection]
```

服务器只做结构和授权校验：

- `grounded` 至少一个 Citation ID；`unsupported` 不得携带 Citation；
- ID 必须来自当前 run 的 `ComposerDeps.citations`；
- ID 为正整数、不得重复，并继续遵守最多 8 个 segment、5 个 item、8 个 section；
- Citation 候选已经由 tenant-bound retrieval 产生，任何模型参数都不能提供 tenant/user ID；
- section ID 由服务器归一化或重新分配，不能成为授权凭证。

task 是最多 240 字符的主题/回答任务，不是答案正文；服务器拒绝其中的 URL、Citation
标记和来源区块，并将它连同锁定的 Citation 传给该 section 的正文 provider。这样一个
同时包含 q1/q2 的请求可以让每个正文调用看到不同的任务边界。这里不判断 Citation 是否
语义支持 section。模型负责相关性，服务器负责来源权限与追溯。

### 阶段 B：按 section 流式生成正文

服务器按计划顺序处理 section：

```text
grounded:
  emit section_started(section_id, citation_ids, public citations)
  run provider stream with only this section's locked evidence
  emit text_delta(section_id, delta)*
  emit section_completed(section_id)

unsupported:
  emit section_started(section_id, status=unsupported)
  emit text_delta(section_id, server fixed text)
  emit section_completed(section_id)
```

每个 grounded section 使用独立、无工具的 provider stream。它只接收用户问题、该 section
的任务提示和已经锁定的 Citation evidence；不能改变 Citation 列表。这样无需从普通文本
流中猜测 Citation，provider 的正文 token 可以在调用开始后直接转发。

代价是 provider 调用数从一次完整 Composer 变为“一次计划 + N 个 grounded section”。
第一版维持最多 8 个 section，但提示词要求合并相关内容，正常目标为 1～3 个 section。
实施时记录调用数和首 token 延迟，若成本不可接受，再评估 provider 原生的结构化事件流，
不能通过放松 Citation 先行约束来优化。

## 内部事件与公开 SSE

Agent/Channel 层增加受控的异步事件接口，概念上为：

```python
async def handle_stream(envelope) -> AsyncIterator[AgentPublicEvent]: ...
```

公开事件扩展为：

```text
started
activity(retrieving)
activity(planning_answer)
section_started(section_id, status, citation_ids, citations)
text_delta(section_id, text)
section_completed(section_id)
completed(response)
```

异常可产生：

```text
section_aborted(section_id, reason=provider_failure|timeout|cancelled)
error | cancelled
```

`section_aborted` 是技术撤回，不是事实纠错。前端删除该 section 的临时正文或显示固定的
“生成中断”状态；服务器不持久化它。

事件层保持以下状态机约束：

```text
section_started -> text_delta* -> section_completed
                -> text_delta* -> section_aborted -> terminal
```

同一时间最多一个 open section。`text_delta` 必须引用当前 open section。终态之后的事件
全部忽略；sequence 缺口或非法状态转换失败关闭。

## 硬安全与语义边界

发送前必须阻止：

- 原始 provider event、reasoning、system prompt 和工具参数；
- 非当前 section 的 evidence 或其他 tenant 内容；
- 模型自带的 Citation marker、来源块和 URL；真实来源由服务器事件/最终投影提供；
- 超过文本、事件、section、token、时间和队列上限的数据。

这些检查是协议、授权和信息泄漏防护，不是语义 verifier。服务器不判断回答结论是否正确，
也不因语义相关性撤回已经发送的正文。

跨 chunk 的 URL/marker 检查需要保留一个短滚动尾缓冲；只有无法与下一 chunk 拼成禁用
模式的前缀才可释放。最终 section 结束时刷新剩余安全文本并运行现有自然文本结构校验。

## 最终化与持久化

服务端同时累积每个已完成 section 的正文。全部 section 完成后：

1. 构造现有 `GroundedResponseSection` / `UnsupportedResponseSection`；
2. 复用 `ResponseEnvelope.grounded()` 校验 section Citation union 和公开 Citation；
3. 生成权威 `completed.response`；
4. 只调用一次现有 turn 持久化路径。

任何 section 未完成都不能生成成功的 `completed.response`。已经发送到浏览器的文本只是
临时显示，数据库和历史只保存最终 response。

## 兼容、重试与回滚

- 保留现有 `ChannelService.handle()` 和 JSON endpoint。
- `handle_stream()` 不可用或 provider 不支持 stream 时，在同一次请求内调用一次现有
  whole-answer 路径并发送 one-delta；不能请求第二个 HTTP endpoint 或更换 message ID。
- section 在首个公开 delta 之前失败，可以按现有有界策略重试；一旦公开过 delta，不静默
  重跑该 section，改发 `section_aborted` 和终态，避免重复/冲突正文。
- `AGENT_STREAMING_ENABLED=false` 继续关闭 SSE。实施阶段评估是否需要独立的
  `AGENT_PROVIDER_STREAMING_ENABLED` 灰度开关；若增加，默认关闭并在 fake/provider 回归
  完成后再开启。

## 前端与 Citation 展示

前端 pending state 从单个字符串改为按 `section_id` 保存：

```ts
type PendingSection = {
  status: "grounded" | "unsupported";
  text: string;
  citations: ConversationCitation[];
  phase: "streaming" | "completed" | "aborted";
};
```

Citation 数据继续保留 excerpt，但默认卡片只显示视频标题和可点击时间戳。点击卡片展开
excerpt，再次点击折叠；键盘和读屏可访问。最终 `completed.response` 替换临时 section，
用于纠正代理合包、重复事件或显示格式差异。

## 可观测性

只记录固定字段：request ID、阶段、section 数、delta 数、调用数、首 delta 延迟、总耗时、
fallback 原因枚举和终态。禁止记录正文、问题、Citation title/excerpt/URL、provider event、
prompt、工具参数和异常消息。
