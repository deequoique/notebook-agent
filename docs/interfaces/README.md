# Interfaces

Notebook Agent exposes two independently authenticated interface families.

## MCP

The server supports MCP 2.0 over stdio and Streamable HTTP. Every client uses
an operator-issued, hash-stored grant bound to one tenant and scope. Prefer an
`Authorization: Bearer` header; the optional URL-token compatibility path is
explicitly gated and never accepts query-string tokens.

Start with [Getting started](../getting-started/README.md), then use the
deployment guide's [MCP section](../deployment/README.md#65-mcp-核心入口无需-langbot)
for transport setup, readiness, and TLS/proxy constraints.

## Browser application and Web API

The same-origin browser application uses email OTP and server-side session
cookies at `/api/v1/*`; those cookies do not authenticate MCP requests.

Read the [Web API contract](web-api.md) for login, sessions, conversations,
and cross-channel linking. For static delivery and split frontend/API releases,
use [Frontend deployment](../deployment/frontend.md).
