# 回答 Agent 三次恢复并移除机械保底

## Goal

当主 Agent 已取得可信 Citation、但因调用上限、超时、回答校验或运行错误
未能生成可交付回答时，改由无工具回答 Agent 基于同一批证据压缩并回答；
回答阶段最多尝试三次，仍失败则返回明确错误，不再机械拼接证据作为保底回答。

## Requirements

- 主 Agent 失败且本轮没有可信 Citation 时，不启动回答 Agent，保留原始失败语义。
- 主 Agent 失败但已有可信 Citation 时，启动独立、无工具的回答 Agent；它只能看到
  当前问题和经过服务器过滤的本轮 Citation，不能检索、读取库存、执行 mutation 或扩大 scope。
- 回答 Agent 最多执行三次**总尝试**。每次输出未通过结构、引用或 scope 校验，
  或发生模型超时、usage limit、provider/runtime failure，均消耗一次尝试。
- 回答 Agent 决定相关视频、引用分配和回答文本：每个入选视频至少一条 Citation，
  更重要的视频可分配更多 Citation；最多五个视频、八个不同 segment。
- 回答 Agent 必须先在顶层 `selected_segment_ids` 中集中选择最多八个 segment；
  各 section 的 `citation_ids` 只能引用该集合，且所有顶层选择都必须实际用于回答。
  八条上限必须进入结构化输出 schema，不能只依赖跨 section 的 Prompt 计数。
- 当前消息显式 URL scope 是服务器强制边界。scope 内且已有可信证据的视频不得被回答
  Agent 丢弃；scope 外 Citation 不得进入候选、回答或最终引用。
- 每次回答输出都由服务器校验：只能引用候选中的 segment、不得编造 URL/来源、
  所有入选视频必须被引用、总数与视频数必须满足上限。
- 主 Agent 自然回答即使其他校验通过，只要引用超过八个不同 segment，也必须转入
  回答 Agent 压缩；任何知识成功路径都不得把超过 MCP 上限的 Citation 交给投影层。
- 任意一次回答尝试成功后返回 `status=ok`，可见来源、`AgentAnswer.citations`、
  持久化 sources 和 MCP projection 必须来自同一份已验证选择。
- 三次均失败时返回 `status=failed`、`error_code=answer_unavailable`、空 Citations；
  不返回最后一份未验证草稿，也不返回“自动总结未完成”的机械证据列表。
- 移除所有主流程中的 deterministic evidence fallback。不得通过提高 request、tool、
  timeout 或 output budget 实现恢复。
- 重试诊断只记录允许字段、attempt index、失败类别和最终错误码，不记录问题、正文、
  Citation 内容、tool 参数、URL 或 provider 原始 payload。

## Acceptance Criteria

- [ ] 主 Agent 在有 Citation 后触发 tool-call limit，回答 Agent 第一次成功，最终 MCP
      返回 `ok`、不超过八条 Citation，且不出现 `runtime_error`。
- [ ] 主 Agent 直接生成包含超过八个有效 segment 标记的回答时，不能绕过压缩；完整
      MCP 链路仍返回至多八条 Citation，而不是 `runtime_error`。
- [ ] 回答 Agent 能在多个相关视频间至少各选一条，并把剩余名额分配给更重要视频；
      最终选择不超过五个视频和八个 segment。
- [ ] `AnswerDraft` schema 的顶层 `selected_segment_ids` 直接声明 `maxItems: 8`；
      section 引用集合与顶层选择一致，未知 ID 和未使用的顶层 ID 均被拒绝。
- [ ] 显式单 URL 和多 URL scope 下，所有已有证据的 scope 视频均被保留，scope 外
      Citation 无法进入选择。
- [ ] 前两次回答输出无效、第三次有效时恰好调用三次并成功；第三次仍无效时返回
      `failed/answer_unavailable`、空 Citations。
- [ ] 无 Citation 的 primary failure 不调用回答 Agent，并保留原错误码。
- [ ] timeout、usage limit、provider/runtime failure 的回答尝试都受同一个三次上限约束。
- [ ] 旧 `evidence_fallback` 文案不再从任何 primary/answer failure 路径返回。
- [ ] 现有 forged item、exact URL routing、tenant isolation、action terminal outcome、
      conversation persistence、MCP schema 和诊断隐私回归通过。
- [ ] 真实模型重跑 `human.he-001`：只记录回答供人工判定；公开响应不得因 Citation
      数量投影失败成 `runtime_error`。

## Notes

- “每个视频至少一条”指回答 Agent 明确选择为相关的视频，不要求保留初次宽检索中的
  所有干扰候选；当前消息显式 URL 对应且已有证据的视频例外，必须入选。
- 本任务不改变人工 benchmark verdict 规则，也不自动判断 `he-001` 内容是否通过。
- 本轮结构调整只运行聚焦的结构、恢复与 MCP 投影测试；调整后只执行一次
  `human.he-001` 真实评测导出，不重复付费回放。
