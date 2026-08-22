# 技术设计

## 当前问题

当前 prompt 允许“不需要私有知识的普通交流”直接回答，但边界过宽。模型把“为什么欧洲人不装
空调”当作普通常识，未调用 `search_segments`。服务器随后在 `search_calls < 1` 分支接受了通过
文本安全检查的 Markdown，最终只能走 one-delta compatibility。

`validate_natural_answer()` 只能检查 URL、Citation marker 和来源区块，不能证明问题无需检索。

## 最小改动

### 1. 收紧主 Agent 指令

把规则改为：

```text
只有明确的问候、感谢、能力说明和必要澄清可以不调用知识工具。
其他任何实质性问题，即使你知道常识答案，也必须先调用 search_segments 搜索当前视频资料库。
搜索后再根据候选进入 grounded/unsupported；不要在搜索前输出常识答案。
```

不增加新的 intent Agent、预分类模型或工具。

### 2. 不增加服务器兜底

```text
主 Agent 收到实质性问题
└── prompt 要求先调用 search_segments
    └── 服务器继续使用现有 finalization，不新增强制判断
```

服务器不检查“模型是否应该搜索”，不自动发起第二次模型调用，也不因零检索返回
`failed/search_required`。正常路径完全依赖主 Agent 按新指令第一次就调用 `search_segments`。

问候、感谢、能力说明和必要澄清继续按 prompt 允许直接回答；能力说明不得声称动态运行状态或
未验证能力。

### 3. 现有后续流程保持不变

```text
主 Agent 按 prompt 调用 search_segments
├── 候选为空 → 现有 no_evidence
└── 候选非空 → 现有 grounded/unsupported Composer
                   └── Citation-first provider streaming
```

不修改 ResponseEnvelope、AgentAnswer、SSE schema、ConversationTurn 或前端。

### 4. Prompt 内的轻量 guidance

Guidance 只约束主 Agent 的必要澄清：

```text
不要只说“请补充信息”。指出缺少什么，并给一个短例子，
例如“请告诉我是哪个视频，例如粘贴视频链接或说出标题”。
```

不修改 `no_evidence`、`unsupported`、ResponseEnvelope、JSON/SSE 或前端。

## 风险与控制

- **模型仍可能忽略 prompt**：本任务明确接受该风险；通过 prompt regression 和受控模型评测监控，
  不在服务器增加 enforcement。
- **普通问题多一次视频搜索**：这是用户确认的 evidence-first 产品行为，受现有 search budget 限制。
- **简单交流误判**：direct allow-list 保守；若模型误把实质性问题当作简单交流，服务器不做意图兜底，
  通过 prompt contract 和受控模型测试持续监控该风险。
- **能力说明漂移**：本任务只保留现状，不扩大内容；完整 Agent 自知识后续单独设计。
- **澄清过长**：prompt 只要求一个短例子，不扩展成排障手册。

## 回滚

回滚只涉及 prompt；不涉及服务器逻辑、固定文案、数据库、OpenAPI 或前端资产。
