# Trusted response boundary final-review findings — 2026-08-19

## Validation baseline

- Trusted-response/runtime group: `125 passed`.
- Evaluation/action/MCP/Web group under an explicit safe environment: `92 passed`.
- `git diff --check` and Trellis context validation passed.
- The default local environment still contaminates unrelated tests with an invalid proxy port and a development wildcard extension Origin; those failures disappear under explicit safe environment values.

## Confirmed contract checks

- `ResponseEnvelope.project()` no longer accepts a caller-supplied text override.
- Canonical template keys and Action codes reject unregistered values.
- No-evidence uses one fixed server text and rejects custom text and Citation payloads.
- `AnswerDraft` forbids the legacy top-level `selected_segment_ids` field.
- The no-evidence tool is hidden when a read failure or terminal Action exists, and a later successful search clears an earlier no-evidence vote.
- Evaluator diagnostics preserve the HE-003 pair `retrieval_miss` plus `answer_contract_failure`, while a gold candidate omitted from the final answer remains `evidence_selection_miss`.

## Blocking findings

### 1. Canonical and Action body ownership is still nominal

`CanonicalResponseSection` and `ActionResponseSection` validate a registered label but still accept arbitrary free-form `text`. `ResponseEnvelope.canonical()` and `.action()` expose that text parameter publicly, and `project()` renders it verbatim. A caller can therefore attach model-authored text to a server-owned key/code and receive trusted projection.

The approved design requires registered server renderers or typed server payloads to own canonical/action prose. The fix should remove raw free-form text from public section factories, render registered templates from typed parameters, and adapt existing management/action outcomes without allowing model output to cross that constructor boundary.

### 2. Zero-search model responses bypass the envelope

The zero-search conversational branch in `orchestrator.py` still returns `AgentAnswer(...)` directly after natural-answer validation. This violates PRD R9's requirement that the external `AgentAnswer` shape be projected from the unified internal envelope.

The implementation needs an explicit model-authored, no-Citation conversational section/disposition with the existing URL/source-marker safety validation, or a documented narrowing of R9 approved by the user. It must not be mislabeled as server canonical text.

## Gate decision

Do not start `08-18-channel-save-link-routing` until both ownership gaps are resolved and the focused review is repeated. The existing automated tests prove current behavior but do not close these provenance gaps.
