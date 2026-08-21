# 技术设计

## 边界变化

`no_evidence` 从“模型可以调用的工具”改为“服务器根据检索状态推导出的终态响应”。
检索仍然只是一个观察步骤，负责填充本轮 Citation allow-list。主 Agent 可以在当前
tenant 有权访问的整个知识库内自由调用 `search_segments`，也可以选择用 `item_id`
限定某个视频。只要候选非空，就把候选交给 LLM，让 LLM 生成 grounded 回答；LLM
可以对没有被候选支持的子问题明确说无法确认。

## 目标流程

```text
主 Agent -> search_segments(query, item_id=None)
          -> 服务器固定 tenant 授权边界，并校验可选 item_id
          -> deps.citations
             -> 本轮最终为空 + 无读取故障：服务器生成 no_evidence
             -> 本轮存在候选：结构化 Answer Composer
```

服务器继续负责 Citation marker、tenant、条目/片段归属和来源安全边界；候选是否支持
q1 或 q2、是否需要限定某个视频检索，这些语义判断交给主 Agent。

## 授权范围与检索范围

- 硬授权范围始终是当前 tenant 有权访问且处于可检索状态的知识库内容。
- `search_segments(query)` 在 tenant 全库检索。
- `search_segments(query, item_id=...)` 限定条目检索；服务器必须重新验证该 item 属于
  当前 tenant 且 active / ready。
- 模型看不到也不能提交 `tenant_id` / `user_id`。
- 不引入 `strict_video_scope` / `tenant_wide_scope` 状态，也不因为消息包含视频 URL 就由
  服务器把整个检索过程硬性缩到该视频。视频引用只作为主 Agent 可使用的可信上下文或
  工具参数来源。

## 模型契约

- 删除 `report_no_relevant_evidence` 的注册和 prepare policy。
- 保留 `search_segments` 作为主 Agent 工具，由主 Agent 决定 query 和可选 `item_id`；
  服务器拥有 tenant 授权与条目归属校验。
- 修改主 Agent 和 Composer 指令：非空候选总是进入结构化 Answer Composer；不能由某个
  未支持子问题终止整轮回答。
- AnswerDraft 使用显式 section 状态：`grounded` section 携带本轮 Citation，
  `unsupported` section 不携带模型自由文本，由服务器渲染固定的“当前证据不足以确认”
  文案。整份草稿至少需要一个 grounded section 才能投影为 grounded 响应。
- 主 Agent 在成功知识检索后的自然文本不再直接公开；它只作为内部运行结果，最终回答
  统一由 Composer 的结构化草稿生成。

## 服务器状态与最终化

第一版只需要使用现有的 `successful_searches` 和本轮 Citation 缓存：

- `successful_searches > 0`、Citation 为空、且没有 pending read failure：服务器生成
  `no_evidence`。
- Citation 非空：跳过全局 no-evidence 判断，直接进入结构化 Answer Composer。
- 读取失败：继续走 `read_unavailable` / 已有证据恢复路径。

旧的 `no_relevant_evidence_requested` 状态应删除或变成不可达状态，不能再参与最终化。

## 兼容性与风险

- 空检索和读取失败必须继续区分。
- 去掉现有“显式 URL 自动成为所有检索的硬 reference scope”的行为时，必须保留 tenant、
  active/ready、item/segment 归属和本轮 Citation allow-list 校验。
- 非空但语义上不相关的候选可能让 LLM 输出 unsupported section；服务器必须丢弃该
  section 的模型文本并渲染固定文案，不能把未引用事实混入 grounded 响应。
- Action 结果继续在知识回答合成之前胜出。
- 对话持久化仍然只保存最终可见回答，不保存中间草稿或工具载荷。

## 验证方式

单元测试覆盖工具注册、最终化分支、Composer schema 和响应渲染；集成测试覆盖：

1. 干净空检索；
2. 非空候选的正常 grounded 回答；
3. 同一条消息中一个子问题有证据、另一个子问题证据不足的混合回答；
4. tenant 全库搜索和可选 item 搜索都无法读取其他 tenant 的内容；
5. 显式视频 URL 不会创建额外的服务器检索范围意图状态。
