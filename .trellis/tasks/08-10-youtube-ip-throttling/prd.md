# Route YouTube acquisition through the user's Mac

## Goal

Restore automatic public YouTube subtitle ingestion for short-term personal use
by routing only the connector's YouTube requests through the user's already
working home-computer egress.

The selected MVP is intentionally on-demand: ingestion is available while the
Mac proxy and reverse SSH tunnel are running. When they are absent, the worker
must fail safely and must not fall back to the rate-limited production-server
egress.

This planning task does not authorize code changes, package installation,
tunnel startup, or production configuration changes until the user approves
the final planning summary.

## Confirmed facts and decisions

- The correct production target is OVHcloud `ubuntu@51.79.159.110`, hostname
  `vps-d2a069a1`. A read-only check on 2026-08-10 confirmed the combined
  Notebook Agent service, worker, and Beat are active.
- A bounded public-video probe from that production host reproduced HTTP 429.
  The worker's same-egress retries do not change the failing path; detailed
  evidence is in `research/current-production-evidence.md`.
- The user confirmed that public subtitle acquisition succeeds on their own
  Mac and selected that computer as the temporary acquisition egress.
- At planning time the Mac ran macOS 15.7.7 on arm64, had Homebrew and OpenSSH
  9.9, and did not yet have `tinyproxy` installed. It was installed later only
  after the separately approved operator step recorded in `validation.md`.
- The production server runs OpenSSH 9.6. Its effective configuration reports
  `AllowTcpForwarding yes`, `GatewayPorts no`, and `PermitOpen any`. A remote
  forward can therefore listen on server loopback without opening a public
  port.
- Tailscale is not part of the chosen solution: the Mac installation is not
  usable in the current CLI context, and the correct production server has no
  configured Tailscale path.
- `YouTubeConnector` makes two separate outbound operations: `_run()` invokes
  yt-dlp to resolve metadata and a signed subtitle URL, then
  `_download_subtitle()` invokes `app.connectors.bounded_fetch` to download the
  subtitle body. Both must use the same home egress.
- The worker initializes trusted CA variables before connector construction
  (`app/ingest/tasks.py:182-192`). Any child-specific proxy environment must
  preserve `SSL_CERT_FILE` and `REQUESTS_CA_BUNDLE` as required by
  `.trellis/spec/backend/youtube-connector.md:44-51`.
- The production worker receives root-owned configuration from
  `/etc/notebook-agent/notebook-agent.env`; its systemd sandbox permits normal
  loopback TCP connections and no service-file change is required.
- The user selected short-term personal use rather than an always-on,
  multi-user or commercially operated acquisition service. Manual foreground
  startup and shutdown are acceptable for this MVP.

Research comparing home acquisition, dedicated datacenter proxies, and
residential pools is retained in
`research/local-acquisition-and-datacenter-proxy.md`.

## Requirements

- The Mac must run an HTTP CONNECT proxy bound only to `127.0.0.1`; it must not
  listen on a LAN, Tailnet, or public interface.
- The Mac must create an outbound SSH session to
  `ubuntu@51.79.159.110` that reverse-forwards a server-loopback port to the
  Mac-loopback proxy. No home-router port forwarding is allowed.
- The server-side reverse-forward listener must bind exactly to
  `127.0.0.1`; it must never use `0.0.0.0`, `GatewayPorts`, or a public Caddy
  route.
- Use one explicit application setting, `YOUTUBE_PROXY_URL`, for the connector
  boundary. For this MVP it must accept only a credential-free loopback HTTP
  proxy URL with an explicit port.
- When `YOUTUBE_PROXY_URL` is set, both the yt-dlp metadata child and bounded
  subtitle child must use the same proxy. Proxy configuration must be supplied
  only to these child processes and must not mutate the worker's process-wide
  environment.
- The child environment must preserve the resolved CA bundle variables and TLS
  certificate/hostname verification. No TLS interception or verification
  bypass is permitted.
- When the tunnel is absent, the configured proxy path must fail closed with a
  stable privacy-safe classification. It must never retry the same job without
  the proxy or silently use the production host's direct egress.
- Provider 429, proxy unavailable, proxy timeout/authentication, subtitle
  fetch failure, content too large, and unusable content must remain
  distinguishable inside the worker while the external failure surface stays
  privacy-safe.
- Do not attach YouTube account cookies, OAuth tokens, PO tokens, or the user's
  browser profile. Public-caption acquisition must remain anonymous.
- The Mac helper must run in the foreground, create only temporary private
  configuration/state, use bounded SSH keepalives, stop both child processes
  on Ctrl-C, and install no LaunchAgent or persistent daemon.
- The helper must not install Homebrew packages automatically. Missing
  `tinyproxy` must produce an actionable preflight error; installation is a
  separate explicit operator step.
- The proxy must be destination-restricted to the YouTube/Googlevideo HTTPS
  hosts required by the connector where tinyproxy's filtering supports that
  safely. It must permit CONNECT only to port 443 and perform no TLS
  interception.
- Production validation must use a public canary video first, then at most one
  user-selected item. It must not print URLs with signed query parameters,
  subtitle bodies, proxy settings, raw yt-dlp stderr, or user content.
- Rollback must require only stopping the Mac helper, removing or disabling
  `YOUTUBE_PROXY_URL`, and restarting the owned worker. No database, object
  store, Redis, Caddy, Web/MCP, or migration change is required.

## Acceptance criteria

- [x] A preflight confirms Mac loopback proxy availability, SSH key access to
      `ubuntu@51.79.159.110`, and that the selected remote loopback port is free
      before starting either long-running process.
- [x] While the helper is running, the proxy-observed public egress differs
      from the production server's direct egress without exposing either value
      in application logs.
- [x] Unit tests prove yt-dlp and the bounded subtitle child receive the same
      configured proxy, retain both verified CA variables, and leave the parent
      process environment unchanged.
- [x] Unit/integration tests prove missing tunnel, malformed/non-loopback proxy
      configuration, timeout, and provider 429 do not trigger an unproxied
      request and produce stable privacy-safe outcomes.
- [ ] A public production canary obtains both metadata and a non-empty bounded
      subtitle body through the home egress, then reaches `ready` with a raw
      object key, content hash, segments, valid timings, and valid embeddings.
- [x] The existing production-IP canary can remain 429-limited without
      preventing the home-egress canary from succeeding.
- [x] Database, Redis, MinIO, embedding, email, Web, MCP, and Caddy traffic do
      not receive the YouTube proxy setting and continue using their existing
      routes.
- [ ] Stopping the SSH tunnel causes a bounded proxy-unavailable failure rather
      than direct fallback; restarting the helper and explicitly retrying the
      item can recover it without creating a duplicate item.
- [x] The Mac and server expose no new public listener, and proxy/tunnel
      processes terminate on operator stop without persistent startup entries.
- [ ] Rollback restores the previous worker configuration and leaves all
      durable application data unchanged.

## Out of scope

- Tailscale setup, router port forwarding, a public forward proxy, or a
  permanent VPN/exit-node configuration.
- LaunchAgent, autossh, systemd tunnel units, an always-on home agent, device
  fleet management, or unattended availability while the Mac is offline.
- Paid datacenter/residential proxies, proxy rotation, multi-provider failover,
  or proxy-IP reputation management.
- YouTube OAuth, account cookies, browser-session export, browser extensions,
  PO tokens, CAPTCHA/fingerprint evasion, or authenticated/private content.
- Official YouTube metadata migration, transcript upload/paste, media upload,
  or ASR. These remain documented future alternatives, not this recovery MVP.
- Worker-global `HTTP_PROXY`, `HTTPS_PROXY`, or `ALL_PROXY` configuration.
- Changing Caddy, public firewall rules, database schema, object-store layout,
  Redis topology, embedding composition, or unrelated production services.
