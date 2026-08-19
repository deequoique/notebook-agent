# 可信回答 Section 与渠道输出边界技术设计

## 1. 问题边界

人工评测已经证明，当前问题不是“有没有 Citation”，而是系统没有显式表达“谁有权声明一个响应 section”。现有 `AgentAnswer` 只有扁平的 `text`、`citations` 和 `action_results`，但内部混合了四类来源：

1. 主 Turn Agent 的自然 prose；
2. Answer Agent 基于候选片段生成的 sections；
3. 服务器生成的 no-evidence、能力说明和管理读取文案；
4. 保存、删除和确认等 ActionOutcome。

这些输出没有共同的 provenance/disposition 合同。检索候选一旦非空就容易被误当成答案证据；服务器可信文本只能通过特殊字符串分支绕开 validator；channel-save 若继续增加 canonical help 和保存提议，会复制相同旁路。

## 2. 候选与证据分层

```text
retrieval candidates
        │
        ├─ 召回判断：相关/gold 片段是否进入候选？
        │
        └─ 回答判断：候选是否足够支持当前问题？
                 ├─ grounded
                 └─ no_relevant_evidence
```

生产内部区分：

- `CandidateEvidence`：当前轮 tenant-scoped 工具返回，允许 Answer Agent 查看；
- `SelectedCitation`：Answer Agent 选择且服务器校验后，允许进入最终回答；
- Gold evidence：只属于 evaluator，用于诊断 retrieval/selection，不进入生产授权。

`deps.citations` 非空只说明有合法候选，不再等价于“存在可引用的相关证据”。

## 3. Answer Agent 判别联合

私有输出合同改为判别联合：

```python
class GroundedSection(BaseModel):
    text: str
    citation_ids: list[int] = Field(min_length=1, max_length=8)

class GroundedDraft(BaseModel):
    kind: Literal["grounded"]
    sections: list[GroundedSection] = Field(min_length=1, max_length=8)

class NoRelevantEvidenceDraft(BaseModel):
    kind: Literal["no_relevant_evidence"]

AnswerDecision = Annotated[
    GroundedDraft | NoRelevantEvidenceDraft,
    Field(discriminator="kind"),
]
```

删除重复的顶层 `selected_segment_ids`。服务器按 section 首次出现顺序派生唯一 Citation ID 集，再校验：

- 每个 ID 属于本轮 candidate allow-list；
- 同一 ID 不跨 section 重复；
- 合计不超过 8 个 segment、5 个 item；
- 所有选中 Citation 满足当前 explicit reference scope；
- 多对象覆盖要求来自任务语义，不因某 item 恰好返回无关候选就强迫引用。

`no_relevant_evidence` 不包含模型正文、自由 reason 或 Citation。服务器根据请求范围生成固定 no-evidence 文案。模型可以安全少答；false negative 交给 benchmark 观测，不能通过附加无关 Citation 来“证明没有”。

## 4. 保留 bounded single Turn Agent

现有 backend spec 明确不采用“Turn Agent 只检索、Composer 每次都回答”的固定双阶段运行时。新设计保留这个决策，避免给每个正常答案增加一次模型调用：

```text
Turn Agent
  ├─ terminal Action -> server Action section
  ├─ server read/canonical -> server canonical section
  ├─ search/read failure -> typed retryable failure
  ├─ zero candidates -> server no-evidence section
  ├─ explicit report_no_relevant_evidence -> server no-evidence section
  ├─ valid cited prose -> grounded envelope directly
  └─ invalid/missing-citation prose or primary failure with candidates
          -> Answer Agent, at most 3 attempts
              ├─ grounded
              ├─ no_relevant_evidence
              └─ answer_unavailable
```

新增无参数、无副作用的 `report_no_relevant_evidence` response tool/disposition。它只在本轮至少完成一次成功 search、没有 pending read failure 且没有 terminal Action 时可生效；服务器丢弃模型自由 prose并生成 canonical no-evidence section。无候选时服务器可直接到达同一 disposition。

Answer Agent 仍是恢复边界，不成为每次正常检索的强制第二阶段。它采用第 3 节的判别联合，因此主 Agent 忘记 Citation、输出结构无效或 primary failure with candidates 时，既能选择可靠 grounded section，也能明确拒绝无关候选。

普通 valid cited prose 继续按 current-run allow-list 验证并直接归一化为 grounded envelope。语义相关性仍需要模型判断；如果模型选择了合法但无关的 Citation，Gold/human benchmark 会把它识别为 selection failure，不能靠服务器 ID allow-list 假装语义已验证。

## 5. 内部 ResponseEnvelope

先新增内部合同，保持外部 API 兼容：

```python
class GroundedResponseSection:
    kind: Literal["grounded"]
    text: str
    citation_ids: tuple[int, ...]

class CanonicalResponseSection:
    kind: Literal["canonical"]
    template_key: RegisteredTemplateKey
    params: Mapping[str, TrustedScalar]

class ActionResponseSection:
    kind: Literal["action"]
    action_code: RegisteredActionCode
    results: tuple[PublicActionResult, ...]

class ResponseEnvelope:
    status: Literal["ok", "not_found", "failed"]
    disposition: Literal["grounded", "no_evidence", "canonical", "action", "failed"]
    sections: tuple[ResponseSection, ...]
    citations: tuple[Citation, ...]
    error_code: ErrorCode | None
```

信任规则由类型决定：

- grounded text 来自模型，但 URL/source-marker 已拒绝，Citation 来自 allow-list；
- canonical section 只能由 application code + 注册模板创建；
- action section 只能由实际服务器 Action 创建，prose 不能模拟副作用；
- “来源”不是模型 section，而是 adapter 仅对 grounded envelope 派生的展示块。

第一阶段由 adapter 渲染回现有 `AgentAnswer.text/citations/action_results`。conversation sources、ChannelService、MCP 和 CLI 都消费同一个 envelope Citation 集。

## 6. Channel-save 接入

- “支持哪些链接”使用 `CanonicalResponseSection(template_key="supported_video_links")`，服务器模板可以安全包含 URL 示例，普通模型 section 的 URL 禁令不变。
- `offer_video_save` 先提交 pending，再产生 `ActionResponseSection(action_code="save_offer_created")`；没有持久化成功就不能显示“要我保存吗”。
- “保存但没有可信目标”使用 `CanonicalResponseSection(template_key="save_target_missing")`，不从模型历史恢复 URL。
- Bilibili worker/短链/quote 是独立领域实现，但必须将结果投影到共享 envelope，不能新增另一套字符串旁路。

## 7. HE-003 双层诊断

Evaluator 将生产 trace 与 gold 对照：

```text
gold 不在 candidates       -> retrieval_miss
gold 在 candidates 但未选  -> evidence_selection_miss
选择合法但合同失败         -> answer_contract_failure
provider/timeout            -> provider_failure
```

生产的 `invalid_citation` 拆为 `unknown_citation`、`duplicate_citation`、`too_many_segments` 和 `too_many_items`。显式范围外或伪造 ID 不暴露范围信息，统一按 `unknown_citation` fail-closed；不记录失败草稿也能定位结构原因。

## 8. 兼容与回滚

- 内部 envelope 先落地，第一阶段不修改 MCP/OpenAPI public schema。
- `_append_sources()` 只接受已验证 grounded envelope；其他 disposition 调用必须失败测试。
- retrieval answer、canonical read、terminal Action 逐个迁移，禁止新旧路径同时追加来源。
- 历史 conversation turn 不反向重写；回滚只恢复 adapter/orchestrator wiring，无数据库迁移。
