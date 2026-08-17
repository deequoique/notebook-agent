# Journal - lane (Part 1)

> AI development session journal
> Started: 2026-08-04

---



## Session 1: 继续实现 video-text-kb P0

**Date**: 2026-08-04
**Task**: 继续实现 video-text-kb P0

### Summary

完成 P0 步骤1-6本地代码：YouTube android_vr 摄入、空成功守卫、五级切分、嵌入、双路检索与CLI；19项测试、编译、依赖、Docker迁移和索引验证通过。任务保持 in_progress，等待 OPENAI_API_KEY 与20视频真实人工验收；当前目录无Git元数据，未提交。

### Main Changes

- Detailed change bullets were not supplied; see the summary above.

### Git Commits

(No commits - planning session)

### Testing

- Validation was not recorded for this session.

### Status

[OK] **Completed**

### Next Steps

- None - task complete


## Session 2: 日志初步搭建

**Date**: 2026-08-07
**Task**: 日志初步搭建
**Branch**: `main`

### Summary

完成 LangBot bridge 与 Notebook Agent 的结构化诊断日志：Notebook Agent 双写 stdout/每日文件，bridge 仅写 plugin stderr；使用 trace/request ID 联查，生产默认脱敏，本地 development+显式开关可记录受限检索详情。修复日志初始化幂等、权限失败关闭流和保留清理 fallback，Terra 定向与全量回归通过，并补充部署与排障文档。

### Git Commits

| Hash | Message |
|------|---------|
| `c55688c` | (see git log) |
| `128f15f` | (see git log) |

### Status

[OK] **Completed**


## Session 3: Agent retrieval convergence and provider-compatible answers

**Date**: 2026-08-08
**Task**: Agent retrieval convergence and provider-compatible answers
**Branch**: `main`

### Summary

Implemented provider-independent retrieval convergence, diversified evidence selection, independent answer composition, development HTTP diagnostics, and PromptedOutput compatibility for DeepSeek Thinking mode.

### Main Changes

- Enforced one backend retrieval per model step with bounded 5/2/3 budgets and typed skipped results.
- Added Top-5 source diversity, independent answer composition, citation validation, canonical history, and deterministic evidence fallback.
- Captured full provider HTTP errors in development, diagnosed Thinking mode rejecting tool_choice=required, and switched composer to PromptedOutput.

### Git Commits

| Hash | Message |
|------|---------|
| `27140c8` | (see git log) |
| `3c35631` | (see git log) |
| `ced5cac` | (see git log) |

### Testing

- [OK] Earlier directed diagnostics/runtime validation passed 46 tests; PromptedOutput switch was not run by Codex at user request, and the user confirmed the task complete.

### Status

[OK] **Completed**


## Session 4: Composer 预算与自动上下文压缩

**Date**: 2026-08-08
**Task**: Composer 预算与自动上下文压缩
**Branch**: `main`

### Summary

为回答 Composer 设置 1000-token provider 上限并关闭 DeepSeek thinking；在输出超限或长度截断时按可信证据自动压缩重试，补齐安全诊断、规范与回归测试。

### Git Commits

| Hash | Message |
|------|---------|
| `fa1dff9` | (see git log) |
| `6e51bb2` | (see git log) |

### Status

[OK] **Completed**


## Session 5: Deploy competition environment on Vercel and Neon

**Date**: 2026-08-08
**Task**: Deploy competition environment on Vercel and Neon
**Branch**: `codex/vercel-neon-main-sync`

### Summary

Connected Vercel Production to GitHub main, upgraded Neon to d4e5f6a7b8c9, documented organization-scoped team access and local DATABASE_URL setup, added migration-head drift checks, fixed static source disclosure, and verified public health and 404 routes.

### Git Commits

| Hash | Message |
|------|---------|
| `3dbc919` | (see git log) |
| `0e13425` | (see git log) |
| `72181b3` | (see git log) |
| `3de5d7a` | (see git log) |
| `c1599b4` | (see git log) |

### Status

[OK] **Completed**


## Session 6: Tenant-scoped MCP server

**Date**: 2026-08-08
**Task**: Tenant-scoped MCP server
**Branch**: `codex/mcp-server-optional-langbot`

### Summary

Added the official MCP v2 stdio and Streamable HTTP adapter, tenant-bound hash-only grants, scope-gated tools, fail-closed mutation readiness, MiXer path-token compatibility, CLI lifecycle operations, durable delete safety, documentation, code-specs, and protocol-level tests.

### Git Commits

| Hash | Message |
|------|---------|
| `c11c9d1` | (see git log) |

### Status

[OK] **Completed**


## Session 7: Deployment environment configuration guide

**Date**: 2026-08-08
**Task**: Deployment environment configuration guide
**Branch**: `main`

### Summary

Reorganized the root dotenv template and added scenario-first environment profiles for local read-only MCP, full mutation MCP, Streamable HTTP/MiXer, and optional LangBot, with bilingual quick-start and deployment guidance.

### Main Changes

- Added a canonical environment configuration guide and complete variable matrix.
- Updated dotenv grouping, stdio grant startup, worker queues, migration head, and bilingual entry points.

### Git Commits

| Hash | Message |
|------|---------|
| `18707d3` | (see git log) |

### Testing

- [OK] 60 focused MCP, diagnostics, provider, and deployment tests passed.
- [OK] Validated 52 dotenv keys, 54 config consumers, local Markdown links, and git diff hygiene.

### Status

[OK] **Completed**


## Session 8: Fix exact video reference and session routing

**Date**: 2026-08-09
**Task**: Fix exact video reference and session routing
**Branch**: `main`

### Summary

Enforced deterministic bare-URL save confirmation and exact platform/video retrieval scopes so stale session history cannot route or cite the wrong saved video.

### Main Changes

- Added server-owned routing invariants for bare supported URL batches and explicit URL content questions.
- Applied fail-closed exact video scope across lexical/vector retrieval, hydration, neighbors, details, timestamps, and citations.
- Restricted management and save tools using only the current user message while preserving legitimate management follow-ups.

### Git Commits

| Hash | Message |
|------|---------|
| `105d0db` | (see git log) |

### Testing

- [OK] 26 passed: ingest submission and agent actions.
- [OK] 45 passed: knowledge services, agent runtime, and exact video routing.
- [OK] 35 passed, 16 skipped: multiuser integration and item management; compileall and Trellis validation passed.
- [OK] Broader suite reached 244 passed, 34 skipped; remaining failures were environment-bound PostgreSQL/socket/composition checks.

### Status

[OK] **Completed**


## Session 9: Add durable ingestion completion queue

**Date**: 2026-08-09
**Task**: Add durable ingestion completion queue
**Branch**: `main`

### Summary

Added transactional completion outbox, durable completion queue, bounded repair sweep, Redis persistence contract, migration, tests, docs, and backend spec.

### Git Commits

| Hash | Message |
|------|---------|
| `998baea` | (see git log) |

### Status

[OK] **Completed**


## Session 10: Ingest completion source-channel notifications

**Date**: 2026-08-09
**Task**: Ingest completion source-channel notifications
**Branch**: `dev`

### Summary

Added a 10-second Celery Beat poller over durable PostgreSQL completion events, idempotent delivery/retry ledger, trusted source-thread propagation, LangBot outbound notifications, migrations, observability, deployment docs, and unit/PostgreSQL coverage. Archived the completed Trellis task; known bounded-autonomy regressions remain outside this task.

### Git Commits

| Hash | Message |
|------|---------|
| `42873cc` | (see git log) |

### Status

[OK] **Completed**


## Session 11: Real-model natural language evaluation

**Date**: 2026-08-09
**Task**: Real-model natural language evaluation
**Branch**: `dev`

### Summary

Added a 22-case real-model MCP/context evaluator, verified the complete stack with a 6/6 smoke run, hardened teardown/report privacy, and archived the Trellis task.

### Main Changes

- Added opt-in real-model natural-language catalog, runner, fixtures, trace correlation, scoring, and sanitized reports.
- Verified PostgreSQL/pgvector, Redis/Celery, MinIO, ingestion, embedding, MCP and context persistence through a dedicated retained-data evaluation user.
- Hardened stdio diagnostic capture, cancellation, teardown deadlines, grant revocation, production refusal, and safety-critical scoring.

### Git Commits

| Hash | Message |
|------|---------|
| `877459b` | (see git log) |
| `8e526b7` | (see git log) |

### Testing

- [OK] Catalog 1.0.0: 22 cases valid.
- [OK] 49 evaluator/diagnostics tests and 4 readiness tests passed.
- [OK] Paid full-stack smoke: 6 pass / 0 fail / 0 skip.

### Status

[OK] **Completed**

### Next Steps

- Run the full 22-case paid evaluation only with explicit operator authorization.


## Session 12: Simplify deployment lifecycle

**Date**: 2026-08-09
**Task**: Simplify deployment lifecycle
**Branch**: `dev`

### Summary

Added a profile-aware one-command launcher with minimal runtime configuration, supervised worker and Beat lifecycle, readiness checks, secure shutdown, tests, and deployment documentation.

### Main Changes

- Added read/full/langbot initialization and lifecycle commands
- Documented minimal environment configuration and deployment safety

### Git Commits

| Hash | Message |
|------|---------|
| `872c7b7` | (see git log) |
| `c0dd2d2` | (see git log) |

### Testing

- [OK] 35 deployment CLI tests and 102 related regression tests passed

### Status

[OK] **Completed**


## Session 13: Include LangBot gateway in full profile

**Date**: 2026-08-10
**Task**: Include LangBot gateway in full profile
**Branch**: `codex/full-includes-langbot`

### Summary

Redefined full as worker, Beat, MCP, and Notebook Agent gateway with dual-listener lifecycle checks and aligned operator docs.

### Main Changes

- Added gateway startup, secret handling, dual-listener readiness/status/fingerprint coverage, and fail-fast port collision validation to full.
- Updated English/Chinese deployment documentation and the stable lifecycle specification.

### Git Commits

| Hash | Message |
|------|---------|
| `4ccfae8c57c741ba76ffdfe124e1a02c62ed38db` | (see git log) |
| `677f6cf` | (see git log) |

### Testing

- [OK] Affected regression suite: 105 passed.
- [OK] Independent broader regression: 111 passed, 1 known MCP stdio cold-start test deselected; the timeout reproduces on unmodified dev.
- [OK] Python compile, shell syntax, diff check, and Trellis validation passed.

### Status

[OK] **Completed**

### Next Steps

- Push the reviewed commits to origin/dev while leaving main untouched.


## Session 14: Web email auth contract and full-browser readiness

**Date**: 2026-08-10
**Task**: Web email auth contract and full-browser readiness
**Branch**: `codex/web-email-auth-contract-fix`

### Summary

Unified browser email authentication, canonical FastAPI composition, generated API contracts, frontend login/session behavior, and archived the Trellis task after full automated validation.

### Main Changes

- Unified production browser API composition and email auth/session/logout contracts.
- Aligned React Query session rotation, routes, generated OpenAPI types, CI, and regression coverage.
- Recorded browser runtime conventions in Trellis specs and archived the completed task.

### Git Commits

| Hash | Message |
|------|---------|
| `b883b27` | (see git log) |
| `1ae3d9f` | (see git log) |
| `424e7f5` | (see git log) |

### Testing

- [OK] Python full suite: 542 passed, 74 skipped.
- [OK] Frontend Vitest 79/79, API contract check, lint, typecheck, and production build passed.
- [OK] Mobile browser login/logout smoke passed without console errors or horizontal overflow.

### Status

[OK] **Completed**

### Next Steps

- Merge codex/web-email-auth-contract-fix into main and run real dev database ingestion end to end.


## Session 15: Fix YouTube worker trusted CA initialization

**Date**: 2026-08-10
**Task**: Fix YouTube worker trusted CA initialization
**Branch**: `codex/youtube-subtitle-ca-fix`

### Summary

Initialized and exported the verified CA bundle before worker-owned YouTube metadata and subtitle subprocesses, added ordering/fail-closed/inheritance tests, updated backend contracts, and verified a real dev ingest reached ready with valid transcript segments and embeddings.

### Git Commits

| Hash | Message |
|------|---------|
| `544b809` | (see git log) |
| `2be7c71` | (see git log) |

### Status

[OK] **Completed**


## Session 16: Modularize bounded Agent runtime

**Date**: 2026-08-17
**Task**: Modularize bounded Agent runtime
**Branch**: `dev`

### Summary

Split runtime.py into state, builder, tool policy/registrations, answer pipeline, and orchestration modules; removed legacy/default runtime and three feature flags; made save and item management always available; updated specs, docs, evaluator, and tests.

### Git Commits

| Hash | Message |
|------|---------|
| `38e0804` | (see git log) |

### Status

[OK] **Completed**
