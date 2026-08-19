# Human review findings: trusted response sections

## Evidence source

- Human-review run: `20260819T072852Z-7e8a6e54`
- Workbook: `.eval-results/natural-language/20260819T072852Z-7e8a6e54/human_review.md`
- Review authority: manual decisions and notes; automated Gold metrics are diagnostics only.

## Confirmed no-evidence failures

### he-011

The question asks whether one fixed video discusses quantum-computer hardware. The answer says it does not, but cites five broad intro/outro segments and renders a complete source block. The reviewer marked fail: the answer should not attach a section/source block.

### he-019

The question asks about the nonexistent “ZQ-947” protocol. Retrieval returned eight unrelated segments from several videos. The answer correctly says nothing was found but cites every unrelated segment and renders their sources. The reviewer marked fail: no source should be attached.

### he-020

The question narrows the nonexistent protocol to the baseline video. The answer cites eight intro/outro segments merely showing what the video is about and claims the protocol was not mentioned. The reviewer marked fail: the response should not carry a source section.

## Current implementation cause

- `AnswerSection.citation_ids` has `min_length=1`.
- `AnswerDraft.selected_segment_ids` and `AnswerDraft.sections` both have `min_length=1`.
- `_draft_failure_reason()` rejects empty selection and requires the union of all section IDs to equal the duplicated top-level selection.
- `_render_sections()` always calls `_append_sources()` after any accepted draft.
- The orchestrator treats `deps.citations` non-empty as evidence available, although those values only prove that a trusted retrieval tool returned candidates.

The model therefore has no valid structural way to say “none of these candidates supports the answer.” It must either select unrelated segments or fail answer validation.

## he-003 is two failures, not one

The run did not place gold segments `1362`, `1403`, `1449`, `1488` in the retrieval top 3. That is a retrieval miss independent of response rendering. The Answer Agent then failed three drafts under the broad `invalid_citation` category. Because failed drafts are intentionally not logged, the exact subreason cannot be reconstructed.

The new plan must preserve both layers:

- evaluator diagnosis of gold absence from candidates;
- production diagnosis of answer structure/selection without retaining model drafts.

## Shared root with channel-save

The shared root applies to the response/confirmation half of channel-save:

- capability answers need server-owned URL examples but currently look like ordinary model prose to the validator;
- a model can say “要我保存吗” without that prose proving a pending action exists;
- ActionOutcome and canonical read text bypass the natural-answer path through separate flat-string branches.

A typed server-owned canonical/action section solves this ownership problem. Bilibili worker absence, short-link resolution and quote extraction remain separate domain gaps; they should consume the shared response contract but are not caused by AnswerDraft itself.

## Decision

Replace the single forced-citation AnswerDraft with a discriminated `grounded | no_relevant_evidence` decision, derive selected Citation IDs from grounded sections only, and normalize every public response through an internal envelope with explicit section provenance. Render sources only for grounded responses.
