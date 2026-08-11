# Getting started

Use this path to run Notebook Agent locally before configuring a public
endpoint, browser application, or chat channel.

## 1. Install the project

Requirements: Python 3.11+, Docker and Docker Compose, an Agent-model API
credential, and a Zhipu Embedding API credential. The managed launcher works
on Linux and macOS.

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev]'
```

## 2. Choose a runtime profile

| Profile | Starts | Best for |
| --- | --- | --- |
| `read` | PostgreSQL and the Streamable HTTP MCP server | Read-only MCP questions and inventory. |
| `full` | MCP, private gateway, PostgreSQL, Redis, MinIO, one worker, and one Beat | Ingestion, item management, and optional channels. |
| `langbot` | The `full` background/gateway stack without MCP | A channel-only deployment. |

```bash
./scripts/notebook-agent init --profile read
./scripts/notebook-agent start
./scripts/notebook-agent status
```

`init` writes generated local infrastructure credentials to ignored,
mode-0600 `.env.runtime`. Existing environment variables and a user-maintained
`.env` take precedence. `stop` only manages processes started by this launcher;
it preserves Compose volumes and external services.

## 3. Configure the selected path

Read [Configuration](configuration.md) to choose the smallest copyable
environment profile: read-only MCP, full MCP, URL-only HTTP compatibility,
LangBot, or the browser application.

## 4. Connect and verify

- [MCP and Web interfaces](../interfaces/README.md) explain grants, transport,
  browser authentication, and API semantics.
- [Deployment](../deployment/README.md) covers direct commands, production
  ordering, TLS, backups, upgrades, and troubleshooting.

For a local CLI-only smoke, create a user and ask a question:

```bash
.venv/bin/python -m app.cli users create
.venv/bin/python -m app.cli ask --user-id <user-id> --thread demo 'What is in my library?'
```
