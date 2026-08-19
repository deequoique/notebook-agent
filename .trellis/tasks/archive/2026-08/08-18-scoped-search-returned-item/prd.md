# 修复检索结果无法继续限定 item scope

## Goal

复现并修复 Agent 在本轮 `search_segments` 已返回某 item 的可信证据后，
仍拒绝使用该 `item_id` 继续限定检索并返回 `item_scope_required` 的问题；
同时保持租户隔离、伪造 ID fail-closed 和 exact URL scope 安全边界。

## Requirements

- 建立确定性复现：同一 Agent turn 先执行不带 `item_id` 的全局
  `search_segments`，获得 item A 的 Citation，随后使用 A 的 `item_id`
  进行更精确的 `search_segments`。修复前必须稳定复现
  `item_scope_required` 且第二次后端查询未执行。
- 本轮成功检索并进入可信 Citation cache 的 item 必须可以作为后续
  `search_segments(item_id=...)` 的限定范围；不要求模型额外调用
  `list_saved_items` 或 `get_saved_item` 才能获得同一 item 的授权。
- 保留现有两种可信来源：本轮 inventory/detail read observation，以及
  经过 `ContextBuilder` 验证的 prior inventory context。
- 任意模型猜测、未在上述可信集合中出现、非正整数或跨租户的 item ID
  必须继续 fail closed，不能触发 embedding、SQL 或对象读取。
- exact current-message URL scope 仍是更严格的上界。只有通过该 scope
  过滤并进入当前 run Citation cache 的 item 才能授权后续限定检索；
  历史 source、模型历史文本和原始 tool payload 不得扩大 scope。
- `KnowledgeServices` 必须继续重复 tenant、active/deleted、ready-state、
  item 和 reference predicates；runtime trust 只允许尝试读取，不替代
  数据层授权。
- 修复不得通过增加 request/tool/timeout budget、放宽所有 item ID、解析
  模型文本中的 ID，或吞掉 `item_scope_required` 来实现。
- 自动诊断需能区分合法 follow-up scoped search 与真正的 forged scope；
  不记录问题、tool 参数、正文、URL 或 provider 原始错误。

## Acceptance Criteria

- [ ] 新增测试在修复前稳定复现“全局 search 返回 item 12，随后 scoped
      search item 12 被拒绝”的路径。
- [ ] 修复后同一路径执行第二次后端 search，参数 `item_id == 12`，最终
      不返回 `item_scope_required`。
- [ ] 本轮 neighbor/其他成功 retrieval 写入 Citation cache 后，其 item
      信任语义与 search Citation 一致。
- [ ] 现有 unobserved item 999 回归继续返回 `failed/item_scope_required`，
      且 backend search 调用数为 0。
- [ ] prior inventory context 仍可授权；prior source/history 本身不得新增
      item 授权。
- [ ] exact URL scope 下，out-of-scope Citation/item 不能进入授权集合，
      scoped miss 不得回退到租户内其他 item。
- [ ] bounded autonomy、exact video routing、multi-user tenant isolation、
      diagnostics privacy 和自然语言 evaluator 相关回归全部通过。
- [ ] 使用至少一个此前出现该错误的 human case 做真实模型定向复测；
      只记录新回答和 error code，由人工判断答案，不用自动 Gold 判定修复。

## Notes

- 本任务修复 read-scope authorization，不改变人工 benchmark 的 verdict
  规则。自动指标和 `item_scope_required` 仅作为复现/诊断证据。
- 非目标：扩大历史上下文、允许模型直接指定任意数据库 ID、调整检索
  排名算法、提高 Agent 硬限制或修改 mutation authorization。
