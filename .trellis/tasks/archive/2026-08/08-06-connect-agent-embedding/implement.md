# 执行计划

## 1. 固化当前失败边界

- [ ] 增加跨层失败测试，证明当前缺 key 时 gateway 普通问答会静默 lexical-only，且 CLI 与
  gateway 存在重复 embedder 装配。
- [ ] 增加 embedding 响应维度和非有限数值测试；测试数据不得包含真实问题或 key。
- [ ] 增加 PostgreSQL fixture：两个 tenant、互斥 ready content/segment 和确定性 1536 维向量。
- [ ] 用 fake LangBot/plugin event 重现“Agent fail-closed answer 后又出现渠道不可用回复”；
  通过内部 correlation/message ID 定位重复 delivery、plugin fallback 或 required-plugin
  guard 边界，不复制微信消息正文和外部身份。

## 2. 提取共享 embedding contract

- [ ] 定义 `EmbeddingProvider` protocol，移除 `KnowledgeServices` 的 `Any`。
- [ ] 让 ingestion 与 query retrieval 复用同一个 Zhipu/OpenAI-compatible 实现和配置。
- [ ] 校验响应顺序、数量、dimension 与 finite values；异常不包含 input/vector/key。
- [ ] 避免保留第二套 query-only client 或维度常量。

## 3. 收紧 Agent → vector retrieval

- [ ] 普通知识搜索强制生成 query embedding；缺失/失败时抛出领域级 embedding unavailable。
- [ ] 将领域错误映射为稳定 `AgentAnswer.error_code`，不静默 lexical-only。
- [ ] 把成功零命中固定映射为 `status=not_found/no_evidence`；把 embedding、数据库和工具异常
  固定映射为 `status=failed` 的不同 error code，渠道不得统一改写。
- [ ] 将 citation allow-list 检查移入 PydanticAI output validator；未知 citation 触发内部
  `ModelRetry`，并要求下一次候选答案前 `search_segments` 调用次数必须增加。
- [ ] 设置独立且有界的 output repair budget；耗尽后映射为通用
  `failed/answer_unavailable`，不返回 `citation_required`、无效 marker 或内部拒绝文案。
- [ ] 确认无效 draft 和其 model messages 不会进入 `AgentAnswer`、`ConversationTurn`、渠道回复
  或日志。
- [ ] 保留 tenant-scoped vector/BM25 SQL、hydration 二次 owner 校验和有界去重。
- [ ] 确认模型 tool schema 仍不包含 `user_id`、vector 或 SQL。

## 4. 统一 composition root

- [ ] 提取 CLI `ask` 与 channel gateway 共用的 provider/Agent/service builder。
- [ ] 保持 `/start`、`/whoami`、`/link`、`/new` 不调用 embedding；普通问题才检查 provider。
- [ ] 非法静态配置启动即失败；暂时 provider 故障在请求级 fail closed。
- [ ] 保证一个入站 correlation/message ID 只渲染一个 `AgentAnswer`；Agent 已返回失败答案也
  视为 bridge 成功响应，不再追加渠道不可用提示。
- [ ] 更新 `.env.example`/部署文档，说明 query 与 ingest 必须使用同模型和 1536 维配置，
  示例不含真实凭据。

## 5. 自动验证

- [ ] 单元测试覆盖 embedding validation、缺配置、provider failure、确定性命令绕过。
- [ ] 使用同一脱敏 query 分别模拟 zero-hit、embedding failure、database failure，断言
  status、error code 和用户提示意图不同，且失败路径不声称“知识库没有”。
- [ ] fake model 第一次引用未知 ID、第二次重新搜索后引用真实 ID，断言用户只看到修复后的
  答案；连续无效输出时只看到一次 `answer_unavailable`，且持久化中没有无效草稿。
- [ ] pgvector 集成测试覆盖完整 Agent→embedding→vector→Citation 链和两个 tenant 隔离。
- [ ] bridge/channel 回归覆盖 success、not found、search required 和 embedding unavailable，
  每种情况均只调用一次 reply。
- [ ] 回归 Agent tool loop、multiuser、gateway、ingestion 和 embedding client。

建议命令：

```bash
.venv/bin/pytest -q tests/test_embed.py tests/test_agent_runtime.py
.venv/bin/pytest -q tests/test_multiuser_integration.py tests/test_http_gateway.py
.venv/bin/pytest -q
git diff --check
python3 ./.trellis/scripts/task.py validate 08-06-connect-agent-embedding
```

## 6. 人工验收与收尾

- [ ] 使用真实 provider、真实 pgvector 和已有私有内容运行一次 CLI `ask`，确认标题、证据和
  时间戳正确；只记录非敏感结果。
- [ ] 重启 gateway 后通过一个已启用渠道运行同类问题，确认走同一 Agent/service composition。
- [ ] 微信复测确认知识问题只有一条最终回复；普通非知识输入即使 fail closed 也不再出现
  第二条渠道不可用提示。
- [ ] 更新父任务对应证据，不提前勾选 Telegram 完整 E2E 或未执行的多渠道验收。
- [ ] 完成 Trellis check、必要 spec 更新、提交并归档子任务。

## Rollback points

- shared builder 回归：恢复旧 composition，但不得把 lexical-only 作为“embedding 已连接”。
- provider validation 与现有响应不兼容：保持普通问答 fail closed，记录响应 metadata 类型，
  不打印 payload，再修正兼容层。
- 集成测试发现 tenant 泄漏：停止真实渠道问答，保留身份命令和数据，不做 schema downgrade。
