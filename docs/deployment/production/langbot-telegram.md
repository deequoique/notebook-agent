# Production LangBot: private Telegram-only operation

This runbook applies to the OVH production deployment for
`notebookai.deequoique.tech`. LangBot is an internal management surface, not a
second public application. Caddy must continue to proxy only the combined
Notebook Agent service on `127.0.0.1:8800`.

## Runtime boundaries

- Channel Gateway listens only on `127.0.0.1:8765`.
- Patched LangBot 4.10.6 listens only on `127.0.0.1:5300`.
- LangBot runs as `notebook-langbot` and does not load Notebook Agent's
  database, model, email, Redis, MinIO, Web Auth, or MCP environment file.
- The required bridge plugin stores only its gateway secret, loopback Gateway
  URL, and trusted bot UUID mapping in a mode-`0600` private `.env`.
- Only a Telegram adapter is created. Do not install, create, scan, or enable a
  WeChat/OpenClaw adapter.

## One-time bootstrap

Use the official `langbot-4.10.6-py3-none-any.whl` only after independently
verifying its expected SHA-256. Keep the wheel outside the Git repository.
The bootstrap script repeats that verification, applies the versioned patch,
compiles every changed Python file, installs the required bridge, generates
independent private keys, disables Box/marketplace/telemetry, and leaves the
bot mapping empty:

```bash
sudo /usr/local/sbin/bootstrap-production-langbot \
  /root/private-artifacts/langbot-4.10.6-py3-none-any.whl
```

Do not pass a Telegram token to this script or place one in an environment
file, command line, GitHub secret, terminal transcript, or screenshot.

Before enabling the units, validate their resolved configuration and confirm
that no public listener was introduced:

```bash
sudo systemd-analyze verify \
  /etc/systemd/system/notebook-agent-gateway.service \
  /etc/systemd/system/notebook-agent-langbot.service
sudo systemctl daemon-reload
sudo systemctl enable --now notebook-agent-gateway.service
sudo systemctl enable --now notebook-agent-langbot.service
sudo ss -ltnp
```

The listener inventory must show `8765` and `5300` on `127.0.0.1`, never
`0.0.0.0`, `::`, or the server's public address. Also verify from a remote
machine that the public server address cannot connect to either port.

## Private management access

Open the tunnel from the operator's workstation:

```bash
ssh -N -L 5300:127.0.0.1:5300 ubuntu@51.79.159.110
```

While that SSH session remains open, visit
`http://127.0.0.1:5300` locally. There is intentionally no LangBot hostname,
Caddy route, or public firewall opening.

In the private UI:

1. Create the administrator/login state if this is the first launch.
2. Install no additional plugin; the Notebook Knowledge Agent bridge is
   already installed and must report `initialized`.
3. Create exactly one Telegram bot adapter and enter the Telegram Bot Token in
   the private UI. Never paste it into chat or a shell command.
4. Create a pipeline with **Enable all plugins disabled** and explicitly bind
   only `notebook-agent/notebook-knowledge-agent`.
5. Bind the Telegram adapter to that bridge-only pipeline. Do not configure a
   Local Agent fallback.
6. Record the adapter's LangBot bot UUID. This is not the Telegram token, chat
   ID, user ID, or bot username.

The bridge initially rejects every bot because `KB_BOT_CHANNELS={}`. After the
Telegram adapter exists, map only its UUID and restart LangBot:

```bash
sudo /usr/local/sbin/configure-production-telegram \
  00000000-0000-0000-0000-000000000000
```

Replace the example UUID with the actual LangBot bot UUID. The helper updates
only the bridge mapping, preserves every secret without displaying it, and
sets the sole allowed channel to `telegram`.

## Acceptance checks

Run only redacted health/readiness checks:

```bash
curl --fail http://127.0.0.1:8765/health
curl --fail http://127.0.0.1:5300/healthz
sudo journalctl -u notebook-agent-langbot.service --since today --no-pager \
  | grep -F 'Required plugins initialized; message adapters may start.'
```

Then send one ordinary human message to the Telegram bot and confirm exactly
one final reply comes from Notebook Agent. Confirm that no WeChat adapter is
present, and inspect logs only for internal state/error classes—not token,
message text, usernames, external sender IDs, or message previews.

## Release and rollback behavior

Production release shutdown order is LangBot, Gateway, combined application,
worker, then Beat. Startup is dependencies, migration, worker/Beat/application,
Gateway, then LangBot. A release is accepted only after the application,
Gateway, LangBot process health, and required-bridge marker all pass.

Rollback switches only the immutable Notebook Agent release and restarts its
owned units. It preserves LangBot's SQLite/configuration, Telegram adapter,
bridge `.env`, Redis/MinIO volumes, and remote Neon data.
