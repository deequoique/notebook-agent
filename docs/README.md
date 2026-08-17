# Notebook Agent documentation

This documentation is arranged in the order most people need it: get a local
runtime working first, connect an interface or channel next, then use the
deployment runbooks when operating a service.

```text
docs/
├── getting-started/  Choose a runtime profile and configure a first run
├── interfaces/       MCP and browser/API contracts
├── integrations/     Optional third-party channel bridge
└── deployment/       General deployment and specialised production runbooks
```

## Start here

1. [Getting started](getting-started/README.md) — prerequisites, runtime
   profiles, private configuration, and a successful local start.
2. [Interfaces](interfaces/README.md) — connect an MCP client or understand
   the browser application's authentication and conversation contract.
3. [Integrations](integrations/README.md) — optionally add LangBot, Telegram,
   and WeChat.
4. [Deployment](deployment/README.md) — deploy, operate, back up, upgrade, or
   troubleshoot a service.

## Specialist guides

- [Frontend split deployment](deployment/frontend.md)
- [OVHcloud Caddy production deployment](deployment/production/ovh-caddy.md)
- [Private Telegram-only LangBot operation](deployment/production/langbot-telegram.md)
- [Temporary YouTube home-network egress](deployment/production/youtube-home-egress.md)
- [Real-model natural-language evaluation](../evals/natural_language/README.md)
