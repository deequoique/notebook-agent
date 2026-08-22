# 实施计划

## 子 Agent 执行顺序

1. **API 能力验证与基线测试**
   - 在不访问网络的 fake model 上确认当前 PydanticAI/provider 能提供真实文本 delta、完成
     结果、取消和 usage 信息。
   - 写一个最小 spike 测试，证明首个 provider delta 早于最终结果；若不成立，保留当前
     one-delta fallback，不伪造 token streaming。
   - 记录最终使用的 API 和 provider capability 判断，不把原始 provider event 暴露为
     公共协议。

2. **拆分 Answer Plan 与 Section Stream**
   - 新增 `AnswerStreamPlan` / `PlannedSection`，复用当前 Citation allow-list、数量和 tenant
     边界。
   - 把现有完整 `AnswerDraft` 路径保留为非流式/不支持 provider streaming 的兼容路径。
   - 实现一次计划调用和按 grounded section 顺序执行的无工具文本 stream；unsupported
     section 直接使用服务器固定文案。
   - 首 delta 前允许有界重试；首 delta 后失败则 abort，不静默重跑。

3. **建立单次执行的 Channel 流接口**
   - 从 Agent 到 Channel 增加受控 async iterator/callback，传递 activity、section 生命周期
     和安全文本 delta。
   - 保持 tenant 解析、message 幂等锁、Action 优先级和持久化只由现有 ChannelService
     所有；不得在 SSE route 创建第二条业务执行路径。
   - 客户端断开时关闭 generator/provider stream，并确保未完成 section 不持久化。

4. **扩展 SSE 与前端状态机**
   - 更新 Pydantic/OpenAPI/TypeScript 事件 schema，增加 section ID、status、Citation
     metadata、`section_started/completed/aborted`。
   - SSE adapter 只投影受控内部事件，维持 request/message/sequence 校验和终态屏障。
   - 前端按 section 追加 delta；重复事件幂等、乱序/缺口失败关闭、aborted 清除临时正文，
     completed response 最终校正 UI。
   - Citation 默认显示标题、链接和时间戳，excerpt 折叠并支持键盘展开。

5. **安全、兼容与可观测性**
   - 增加跨 chunk URL/Citation marker/来源块滚动缓冲测试；禁止 reasoning、tool payload、
     prompt 和非当前 section evidence 出现在公开事件。
   - provider 不支持 streaming、SSE disabled、JSON 客户端、Action/no-evidence/canonical 回答
     保持兼容，同一 message 只执行和保存一次。
   - 日志只增加固定阶段、计数、耗时、capability/fallback 枚举和终态。

## 必须覆盖的测试

1. 两个以上真实 fake-provider delta，且 Citation `section_started` 先于第一个 delta。
2. 非本轮/跨 tenant/重复/超限 Citation 在正文公开前失败。
3. grounded + unsupported 混合回答；unsupported 无模型正文。
4. provider 不支持 stream 的 one-delta 单次 fallback。
5. 首 delta 前失败可重试；首 delta 后失败 abort 且不持久化。
6. 客户端取消、网络断开、超时、乱序、重复、sequence 缺口和终态尾随事件。
7. 最终 `completed.response`、历史和数据库 Citation/正文完全一致，只保存一次。
8. Citation excerpt 默认折叠、展开可访问、移动端不撑坏布局。
9. 生产日志敏感哨兵扫描：正文、问题、Citation 内容、URL、prompt/provider/tool payload
   均不可出现。

## 验收命令

```bash
# 后端聚焦
pytest -q tests/test_conversation_streaming.py \
  tests/test_mixed_evidence_grounded_flow.py \
  tests/test_trusted_response_boundary.py \
  tests/test_agent_runtime.py

# 前端（使用项目要求的 Node >= 22.22.2）
pnpm --dir web test
pnpm --dir web run typecheck
pnpm --dir web run build
pnpm --dir web run check:api

# 契约和任务
PYTHONPATH=. python scripts/export_web_openapi.py --check
python3 ./.trellis/scripts/task.py validate 08-22-citation-first-provider-streaming
git diff --check
```

最后运行完整 Python 测试并单独记录既有环境/配置失败，不把无关工作区改动纳入本任务。

## Review Gate

- Citation 校验只代表 tenant/本轮来源授权，不得新增语义 verifier 或“事实已认证”文案。
- 任何公开文本 delta 都必须属于已验证且 open 的 section。
- provider stream、业务执行和持久化各只有一条路径；fallback 不造成第二次模型执行或保存。
- 未完成 section 不进入历史；最终 `completed.response` 是唯一权威结果。
- 不依赖 JSON 字段顺序、正则猜测半截结构化输出或模型自觉遵守 tenant 边界。

## 回滚点

- 通过 `AGENT_STREAMING_ENABLED=false` 回退到非流式 JSON。
- provider capability 不满足时保留已验证的 one-delta SSE compatibility path。
- 新 section 事件前端出现兼容问题时，服务端可只发送旧 activity/text_delta/completed，
  但不得删除最终 Citation 和持久化校验。
