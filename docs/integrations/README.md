# Integrations

## LangBot: Telegram and WeChat

LangBot is optional. The bridge converts trusted platform events into the
Notebook Agent channel envelope while Notebook Agent retains ownership of
identities, conversations, retrieval, and permissions.

1. Read the [configuration profile](../getting-started/configuration.md#d-可选-langbot-渠道).
2. Follow the [LangBot deployment steps](../deployment/README.md#7-安装-langbot-桥接可选).
3. Read the plugin's [installation and safety notes](../../integrations/langbot_kb_plugin/README.md).

Telegram and WeChat can be linked to the same library with short-lived,
single-use codes. Channel conversation histories remain separate by default.

For a Telegram-only production operating model, see
[Private Telegram-only LangBot operation](../deployment/production/langbot-telegram.md).
