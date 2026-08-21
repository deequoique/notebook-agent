# 实施计划

## 目标

把知识回答链路改成：

```text
search_segments
├── 本轮最终没有候选
│   └── 服务器生成 no_evidence
└── 本轮存在候选
    └── 进入结构化 Answer Composer
        ├── 有证据部分附本轮 Citation
        └── 无法确认部分明确说明证据不足
```

主 Agent 的检索授权上限是当前 tenant 的整个可检索知识库。URL 只作为模型上下文
提示，不自动成为硬 reference scope；主 Agent 可选择全库搜索或传入经过服务器验证的
`item_id`。

## 子 Agent 执行步骤

1. 先检查工作区现有改动，保留与本任务无关的 Web/API/前端改动；本任务早期曾有
   未完成的 agent 文件改动，先将它们整理到一个可运行基线，再继续实现。
2. 从主 Agent 工具面板移除 `report_no_relevant_evidence`、对应 policy、状态字段和
   诊断 allow-list；服务器只从干净空检索状态推导 `no_evidence`。
3. 修改 AnswerDraft/Composer，使非空候选统一进入结构化回答；使用显式 grounded/
   unsupported section，unsupported 的模型文本由服务器丢弃并替换为固定证据不足文案。
   禁止主 Agent 成功检索后的自然文本直出绕过 section-level 校验。保留未知 ID、范围、
   租户、URL、来源块和数量限制。
4. 修改 URL 语义问题路由：保留裸 URL 的确定性保存路由；URL+自然语言问题不再把
   `parsed.references` 注入为全局硬 retrieval scope。`search_segments(query)` 允许 tenant
   全库，`search_segments(query, item_id=...)` 由服务重复验证 item 所属 tenant、active 和
   ready 状态；模型不得提交 tenant/user 参数。
5. 更新主 Agent/Composer 提示词和诊断投影，确保日志不新增用户正文、工具载荷或敏感
   evidence。
6. 增加/调整回归测试：工具不存在、空检索终态、非空候选路由、混合 q1/q2、tenant
   隔离、可选 item 搜索、URL 不创建硬 scope；同步修正旧的 no-evidence tool/schema 测试。
7. 运行 focused agent/response/exact-reference/multiuser 测试，再运行完整 Python 测试、
   编译检查和必要的前端/API 回归，记录失败与修复。

## 主 Agent 验收门

- `build_agent(...)._function_toolset.tools` 不包含 `report_no_relevant_evidence`。
- clean empty search 返回 `not_found/no_evidence`，不调用 Composer。
- non-empty search 统一调用结构化 Composer；unsupported 子段不能注入模型自由事实。
- explicit URL + semantic question 不会自动给服务设置 exact reference filter。
- tenant A 的全库/item 检索不能返回 tenant B 的片段。

## 主会话验收门

- 检查子 Agent diff 只触及任务范围，保留现有用户改动。
- 运行 `task.py validate`，更新 spec 中仍描述旧全局 no-evidence tool 或强制 URL scope
  的段落。
- 必要时由独立检查子 Agent复核安全边界、Citation allow-list、重试和持久化。
