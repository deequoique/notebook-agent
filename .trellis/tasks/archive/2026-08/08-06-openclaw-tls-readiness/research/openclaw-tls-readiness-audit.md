# Sanitized OpenClaw TLS and readiness audit

Date: 2026-08-06

## Historical failure and current evidence

Historical logs recorded an aiohttp connector certificate exception while connecting to the HTTPS iLink endpoint.
The underlying OpenSSL error was `CERTIFICATE_VERIFY_FAILED` with an unavailable local issuer. This is stale:
on 2026-08-07 the user confirmed that the actual deployed LangBot/OpenClaw can log in and continuously poll
WeChat successfully. No token, QR content, user identity, or message was copied into this artifact.

## Runtime trust evidence

The historical environment inspection found a certifi bundle while Python reported no default CA file and the
process had neither `SSL_CERT_FILE` nor `REQUESTS_CA_BUNDLE`. It must not be used to perturb today's healthy
default trust path. Disabling certificate verification is not an acceptable remedy; any future enterprise CA
override must be explicit, client-local and verified.

## Readiness mismatch

The patched `openclaw_weixin.py` starts `_poll_loop()` in the background and immediately logs that the
adapter is running. The poll loop catches broad exceptions, prints a traceback, applies exponential backoff,
and continues. Consequently:

- required-plugin readiness proves the Notebook Agent bridge initialized, not that WeChat polling works;
- process `/healthz` can remain healthy during permanent adapter TLS failure;
- there is no reliable channel path for an in-WeChat failure message when inbound polling itself is down.

The correct operator signal belongs in startup preflight, adapter health/readiness, and the management UI or
logs. Healthy must require successful authentication/poll activity.
