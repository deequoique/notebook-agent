# 主 Agent 视频优先检索

## 目标

修复主 Agent 在实质性问题上跳过 `search_segments`、直接输出无 Citation 模型常识的行为。
除明确的简单交流外，主 Agent 必须先搜索当前 tenant 有权访问的视频资料，再决定如何回答。

## 需求

- 问候、感谢、能力说明和必要澄清可以不检索直接回答。
- 其他实质性问题，即使模型认为自己知道答案，也必须先调用 `search_segments`。
- 搜索范围继续由服务器绑定当前 tenant；工具参数不得出现 tenant/user ID。
- 搜索有候选时继续使用现有 grounded / unsupported Composer 与 Citation-first streaming。
- 搜索候选为空时继续使用现有服务器 `no_evidence` 行为；显式模型常识补充不在本任务实现。
- 服务器不新增零检索兜底、强制重试或 `search_required` 判断；是否先搜索由主 Agent 指令约束。
- 必要澄清不能只说“请补充信息”，必须用一个短例子说明用户需要补充什么。
- guidance 只体现在主 Agent prompt 中，不修改服务器固定文案或公共协议。
- JSON、SSE 和所有 Channel 使用同一规则。

## 验收标准

- [ ] 主 Agent 指令明确要求实质性问题先调用 `search_segments`。
- [ ] 真实/受控模型按指令先搜索，再进入现有 grounded streaming。
- [ ] “你好”“谢谢”“你能做什么”和可信澄清仍可不搜索。
- [ ] 必要澄清指出缺失信息并给一个短例子。
- [ ] 现有 mixed evidence、no-evidence、Action、Citation 和持久化行为无回归。

## 非目标

- 不新增 `general_knowledge`、`answer_mode` 或新的 SSE section 状态。
- 不修改 `no_evidence` / `unsupported` 固定文案，不新增 guidance schema 或前端引导面板。
- 不新增服务器零检索 gate、强制重试或 search-required enforcement。
- Agent 架构知识和用户自助排障作为后续独立需求，不与本次路由 bug 绑定。
