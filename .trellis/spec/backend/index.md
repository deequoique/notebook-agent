# Backend Development Guidelines

> Best practices for backend development in this project.

---

## Overview

This directory contains guidelines for backend development. Fill in each file with your project's specific conventions.

---

## Guidelines Index

| Guide | Description | Status |
|-------|-------------|--------|
| [Directory Structure](./directory-structure.md) | Module organization and file layout | To fill |
| [Database Guidelines](./database-guidelines.md) | Neon runtime URLs, migration-head synchronization, and deployment safety | Active |
| [Error Handling](./error-handling.md) | Error types, handling strategies | To fill |
| [Quality Guidelines](./quality-guidelines.md) | Code standards, forbidden patterns | To fill |
| [Logging Guidelines](./logging-guidelines.md) | Structured logging, log levels | To fill |
| [YouTube Connector](./youtube-connector.md) | Subtitle-track selection and yt-dlp runtime contract | Active |
| [Bilibili Connector](./bilibili-connector.md) | Strict video URL admission, public yt-dlp metadata/SRT, and browser/ASR fallback | Active |
| [LangBot Channel Runtime](./langbot-channel-runtime.md) | Required bridge readiness, fail-closed routing, and channel privacy | Active |
| [Provider TLS and Request Diagnostics](./provider-tls-diagnostics.md) | Verified outbound CA composition and redacted Agent/retrieval stage diagnostics | Active |
| [Agent Retrieval Convergence](./agent-retrieval-convergence.md) | Server-enforced retrieval convergence, tool-free answer composition, evidence fallback, and Top-5 video-level sources | Active |
| [Channel Identity Linking](./channel-identity-linking.md) | Deterministic `/link` validation, single-use tokens, tenant merge and privacy boundaries | Active |
| [Knowledge Item Management](./knowledge-item-management.md) | Tenant-scoped inventory tools, durable destructive confirmation, recycle-bin lifecycle, retry, and bounded purge | Active |
| [Ingestion Completion Queue](./ingest-completion-queue.md) | Transactional completion outbox, durable broker boundary, at-least-once delivery, and bounded repair sweeps | Active |
| [MCP Channel Runtime](./mcp-channel-runtime.md) | Official MCP v2 transports, tenant-bound grants, scope-gated tools, URL-token safety, and fail-closed readiness | Active |
| [Web Browser Runtime](./web-browser-runtime.md) | Canonical browser app ownership, email session/CSRF contract, tenant affinity, OpenAPI composition, and MCP isolation | Active |
| [Browser Companion Capture](./browser-companion-capture.md) | MV3 pairing grants, capture.v1, canonical transcripts, extension CORS, and YouTube/NTU Kaltura ingestion | Active |
| [Deployment Lifecycle](./deployment-lifecycle.md) | Profile-aware one-command startup, minimal configuration, process ownership, and exactly-one-Beat safety | Active |

---

## How to Fill These Guidelines

For each guideline file:

1. Document your project's **actual conventions** (not ideals)
2. Include **code examples** from your codebase
3. List **forbidden patterns** and why
4. Add **common mistakes** your team has made

The goal is to help AI assistants and new team members understand how YOUR project works.

---

**Language**: All documentation should be written in **English**.
