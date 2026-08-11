# Frontend package and split deployment

## What `web/` is

`web/` is the Notebook Agent browser application. It is already an independent,
private package with its own dependency lock, tests, lint, typecheck, OpenAPI
snapshot, and Vite build. Its deployable artifact is `web/dist`.

It is not a reusable component library. The package owns application routes,
authentication, server-state queries, CSRF handling, and product pages, and
there is currently no second application that would consume a shared UI API.
Keep it in this repository so changes to `/api/v1` and the generated frontend
contract can be reviewed atomically.

## Supported deployment shapes

### Bundled mode

Bundled mode is backward-compatible and remains the default:

```text
Browser -> https://kb.example.com -> Python web-server
                                   |-- /api/v1/*
                                   `-- web/dist
```

```dotenv
WEB_ORIGIN=https://kb.example.com
WEB_SERVE_STATIC=true
WEB_STATIC_DIR=web/dist
```

Build the frontend before starting `web-server`:

```bash
corepack pnpm --dir web install --frozen-lockfile
corepack pnpm --dir web check:api
corepack pnpm --dir web test
corepack pnpm --dir web typecheck
corepack pnpm --dir web lint
corepack pnpm --dir web build
.venv/bin/python -m app.cli web-server
```

### Split services behind one public origin

The static frontend and Python API may run on different services:

```text
Browser -> https://kb.example.com
           |-- /*         -> static service containing web/dist
           `-- /api/v1/*  -> Python web-server (API-only)
```

Backend configuration:

```dotenv
WEB_ORIGIN=https://kb.example.com
WEB_SERVE_STATIC=false
WEB_STATIC_DIR=web/dist
```

`WEB_STATIC_DIR` stays configured so switching back to bundled mode is
deterministic, but API-only startup does not access the directory.

The public reverse proxy must:

- forward `/api/*`, including methods, query strings, request bodies,
  `Origin`, `Sec-Fetch-Site`, cookies, CSRF headers, and `Set-Cookie` responses;
- serve `index.html` for frontend routes such as `/login`, `/library`, and
  `/videos/<public-id>`;
- return the backend JSON response for unknown `/api/*` paths instead of the
  SPA shell;
- disable CDN caching for `/api/v1/*` and HTML, while allowing immutable caching
  for fingerprinted `/assets/*` files;
- apply the same browser security policy that bundled mode adds in
  `app/api/app.py` to static HTML and assets: HTTPS/HSTS, `nosniff`, a restrictive
  `Referrer-Policy` and `Permissions-Policy`, frame blocking, and a CSP whose
  `connect-src` remains `'self'` and whose image allowlist is limited to the
  YouTube thumbnail hosts used by the application;
- keep the channel gateway private and never expose its loopback port.

The backend profile must also keep the per-item ingestion limits documented in
`docs/getting-started/configuration.md`. They are enforced before raw-object writes
and provider calls; changing the frontend upload form does not raise those
server-side ceilings.

Do not put the frontend at one browser origin and call a second public API
origin directly. The current security model intentionally requires exact
`Origin`, `Sec-Fetch-Site: same-origin`, host-only `__Host-kb_session` and
`__Host-kb_csrf` cookies, and `X-CSRF-Token`. Adding wildcard CORS, domain
cookies, browser storage tokens, or a permissive fallback would weaken that
model and is not supported.

## Recommended Debian/Ubuntu deployment (including domestic servers)

The runtime topology is cloud-vendor-neutral and is the recommended default for
a team-owned Linux host. The commands below are a Debian/Ubuntu reference and
assume systemd plus the packaged Nginx `snippets` / `sites-available` layout.
Rocky, Alma, RHEL, CentOS, 1Panel, or another control panel may use the same
topology, but the operator must map the service user and Nginx files to that
distribution's `nginx` group and `/etc/nginx/conf.d` conventions. Nginx serves
the built SPA, while the Python process exposes only the Web API on loopback.

```text
Browser -> https://kb.example.com
           |-- /*        -> Nginx -> /opt/notebook-agent/current/web/dist
           `-- /api/*    -> Nginx -> 127.0.0.1:8000

127.0.0.1:8765 -> private channel gateway; never expose it through Nginx
```

The committed templates are:

- `deploy/nginx/notebook-agent-web.conf` — TLS site, SPA fallback, API proxy,
  caching, and privacy-safe logging defaults;
- `deploy/nginx/notebook-agent-web-security-headers.conf` — the browser policy
  shared by static and proxied responses;
- `deploy/systemd/notebook-agent-web.service` — the API-only Web process;
- `deploy/systemd/notebook-agent-web-migrate.service` — a oneshot migration
  admission that reads secrets with systemd's `EnvironmentFile` parser rather
  than executing the file as shell code. It creates a release-local
  `.migration-admitted` marker only after `upgrade`, `current`, and `check` all
  succeed; the Web unit refuses every start without that marker.

Replace the example domain and certificate paths locally on the server. Do not
commit the real host inventory, certificate, private key, database URL, or Web
auth secret.

### 1. Prepare the private environment

Keep `/etc/notebook-agent/notebook-agent.env` owned by root and readable by the
service group only (`0640`). It must contain the complete backend profile plus:

```dotenv
WEB_ORIGIN=https://kb.example.com
WEB_AUTH_SECRET=<at-least-32-random-characters>
WEB_COOKIE_SECURE=true
WEB_SERVE_STATIC=false
WEB_HOST=127.0.0.1
WEB_PORT=8000
WEB_FORWARDED_ALLOW_IPS=127.0.0.1
```

`WEB_ORIGIN` must exactly match the final public HTTPS origin. Use the same
file/profile that the gateway needs for Web login approval, but never put
secrets directly in the systemd unit or Nginx configuration.

Provision the service account and release directories once as root. If the Git
repository is not already present, clone the authorized fork into the prepared
`repository` directory as `notebook-agent`:

```bash
sudo install -d -o notebook-agent -g notebook-agent -m 0755 \
  /opt/notebook-agent \
  /opt/notebook-agent/repository \
  /opt/notebook-agent/releases
```

### 2. Build one frontend + API release

Keep the Git checkout at `/opt/notebook-agent/repository`. Each release is an
exact detached worktree, with its own Python environment and frontend build, so
the API and SPA always share one commit SHA. Enter the service account before
building and exit it after the release is complete:

```bash
sudo -iu notebook-agent
repo=/opt/notebook-agent/repository
release_sha="$(git -C "${repo}" rev-parse HEAD)"
release_id="$(git -C "${repo}" rev-parse --short=12 "${release_sha}")"
release_dir="/opt/notebook-agent/releases/${release_id}"

install -d -m 0755 /opt/notebook-agent/releases
git -C "${repo}" worktree add --detach "${release_dir}" "${release_sha}"
cd "${release_dir}"
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[dev]'
corepack pnpm --dir web install --frozen-lockfile
corepack pnpm --dir web check:api
corepack pnpm --dir web test
corepack pnpm --dir web typecheck
corepack pnpm --dir web lint
corepack pnpm --dir web build

printf 'COMMIT_SHA=%s\nSCHEMA_HEAD=%s\nFRONTEND_ARTIFACT=%s\nAPI_ENTRYPOINT=%s\n' \
  "${release_sha}" 'f1a2b3c4d5e6' 'web/dist' 'app.cli web-server' \
  > release-manifest.env
exit
```

Keep `release-manifest.env` with the release. It is non-secret and binds the SPA,
API, and expected schema for audit and rollback. Do not reuse one mutable
virtualenv across releases.

### 3. Provision TLS before enabling the HTTPS site

Provision or install the TLS certificate through the team's existing server
provider **before** copying the HTTPS config into Nginx or running `nginx -t`.
The template deliberately does not guess whether the host uses Certbot
standalone, a cloud load balancer, or a domestic provider's managed
certificate. Replace `kb.example.com` and verify the final local paths first:

```bash
sudo test -r /etc/letsencrypt/live/kb.example.com/fullchain.pem
sudo test -r /etc/letsencrypt/live/kb.example.com/privkey.pem
```

Certificate renewal remains the server operator's responsibility. Its deploy
hook must run `nginx -t` and reload Nginx only after the renewed files validate.

### 4. Install, switch, and validate the services

```bash
set -euo pipefail

sudo install -m 0644 deploy/nginx/notebook-agent-web-security-headers.conf \
  /etc/nginx/snippets/notebook-agent-web-security-headers.conf
sudo install -m 0644 deploy/nginx/notebook-agent-web.conf \
  /etc/nginx/sites-available/notebook-agent-web.conf
sudo ln -sfn /etc/nginx/sites-available/notebook-agent-web.conf \
  /etc/nginx/sites-enabled/notebook-agent-web.conf
sudo install -m 0644 deploy/systemd/notebook-agent-web.service \
  /etc/systemd/system/notebook-agent-web.service
sudo install -m 0644 deploy/systemd/notebook-agent-web-migrate.service \
  /etc/systemd/system/notebook-agent-web-migrate.service

release_id="$(git -C /opt/notebook-agent/repository rev-parse --short=12 HEAD)"
release_dir="/opt/notebook-agent/releases/${release_id}"
sudo rm -f "${release_dir}/.migration-admitted"
sudo ln -sfn "${release_dir}" /opt/notebook-agent/current.next
sudo systemctl daemon-reload
if ! sudo systemctl stop notebook-agent-web; then
  echo 'failed to stop Web service' >&2
  exit 1
fi
if ! active_state="$(
  sudo systemctl show notebook-agent-web --property=ActiveState --value
)"; then
  echo 'failed to query Web service state' >&2
  exit 1
fi
if [ "${active_state}" != 'inactive' ]; then
  echo "refusing to switch an active Web service; state=${active_state}" >&2
  exit 1
fi
sudo mv -Tf /opt/notebook-agent/current.next /opt/notebook-agent/current

sudo nginx -t
sudo systemd-analyze verify /etc/systemd/system/notebook-agent-web.service
sudo systemd-analyze verify /etc/systemd/system/notebook-agent-web-migrate.service
if ! sudo systemctl start notebook-agent-web-migrate.service; then
  echo 'migration admission failed; Web remains stopped' >&2
  exit 1
fi
sudo test -f /opt/notebook-agent/current/.migration-admitted
if ! sudo systemctl enable notebook-agent-web; then
  echo 'failed to enable Web service' >&2
  exit 1
fi
if ! sudo systemctl restart notebook-agent-web; then
  echo 'failed to start admitted Web release' >&2
  exit 1
fi
curl --fail http://127.0.0.1:8000/api/v1/health
sudo systemctl reload nginx
```

The release-local admission marker also protects recovery: a migration failure
survives a reboot because `notebook-agent-web.service` checks the marker before
every start. Do not manually create or copy this marker between releases.

The symlink switch, migration, and process restart are a planned maintenance
window. Remove this host from an upstream load balancer or enable the team's
maintenance response before the switch; otherwise clients may receive a short
503 while the API is stopped. Do not serve the new SPA as a successful release
until the API and dependency checks below pass.

The local `/api/v1/health` check proves only that the Web process is alive; it is
not full dependency readiness. Before enabling save/retry, also confirm the
database is at `f1a2b3c4d5e6`, Redis answers `PONG` with the documented AOF
settings, MinIO is ready, and the expected Celery worker/beat queues are active,
using the commands in [the main deployment runbook](README.md#61-readiness-与-celery-worker).
`After=docker.service` in the unit controls startup order only and is not a
readiness guarantee.

### 5. Smoke the public origin

Use the final domain and verify:

```text
GET  /                      -> SPA index
GET  /login                 -> SPA index
GET  /library               -> SPA index
GET  /videos/<public-id>    -> SPA index
GET  /api/v1/health         -> 200 JSON
GET  /api/v1/capabilities   -> 200 JSON
GET  /api/v1/does-not-exist -> JSON 404, never SPA HTML
```

Then approve one real login challenge and verify the library/detail pages and
one CSRF-protected mutation. Confirm only ports 80/443 are public; Web API port
8000 and channel gateway `127.0.0.1:8765` must stay private.

### 6. Roll back the paired release

Read the previous release's `release-manifest.env`, then point the single
`/opt/notebook-agent/current` link back to that exact release. This restores its
API code, Python environment, and `web/dist` together:

```bash
set -euo pipefail

previous_release=/opt/notebook-agent/releases/SET_PREVIOUS_RELEASE_ID
test -r "${previous_release}/release-manifest.env"
test -f "${previous_release}/.migration-admitted"
cat "${previous_release}/release-manifest.env"
previous_schema="$(sed -n 's/^SCHEMA_HEAD=//p' \
  "${previous_release}/release-manifest.env")"
live_schema="$(
  sudo systemd-run --quiet --wait --collect --pipe \
    --uid=notebook-agent --gid=notebook-agent \
    --working-directory=/opt/notebook-agent/current \
    --property=EnvironmentFile=/etc/notebook-agent/notebook-agent.env \
    /opt/notebook-agent/current/.venv/bin/alembic current \
    | awk 'NR == 1 {print $1}'
)"
if [ -z "${previous_schema}" ] || [ "${live_schema}" != "${previous_schema}" ]; then
  echo 'schema admission failed; obtain a reviewed compatibility proof' >&2
  exit 1
fi
sudo ln -sfn "${previous_release}" /opt/notebook-agent/current.next
if ! sudo systemctl stop notebook-agent-web; then
  echo 'failed to stop Web service during rollback' >&2
  exit 1
fi
if ! active_state="$(
  sudo systemctl show notebook-agent-web --property=ActiveState --value
)"; then
  echo 'failed to query Web service state during rollback' >&2
  exit 1
fi
if [ "${active_state}" != 'inactive' ]; then
  echo "refusing to switch an active Web service during rollback; state=${active_state}" >&2
  exit 1
fi
sudo mv -Tf /opt/notebook-agent/current.next /opt/notebook-agent/current
if ! sudo systemctl restart notebook-agent-web; then
  echo 'failed to restart Web service during rollback' >&2
  exit 1
fi
sudo nginx -t
sudo systemctl reload nginx
```

The coordinated stop/symlink/restart is a brief maintenance window, not a
cross-process transaction. Keep new save/retry admissions disabled until the
API liveness, dependency readiness, public smoke, and one real authenticated
flow succeed. The default gate requires the previous manifest's `SCHEMA_HEAD`
to equal the live database revision. A differing revision requires a separately
reviewed forward-compatibility procedure; do not bypass the command inline.
Keep the database schema forward-compatible: **不要自动执行 Alembic downgrade**
against production data. A database downgrade remains a separate,
backup-gated operation.

Retain at least the current and previous known-good release. Before removing an
older release, confirm it is not the `current` symlink target, verify free-space
pressure, then remove its registered worktree with:

```bash
git -C /opt/notebook-agent/repository worktree remove \
  /opt/notebook-agent/releases/SET_OLD_RELEASE_ID
```

Never delete the active release or bulk-remove the releases directory.

## Verification

Before routing traffic, verify the same public origin:

```text
GET  /                      -> SPA index
GET  /login                 -> SPA index
GET  /library               -> SPA index
GET  /videos/<public-id>    -> SPA index
GET  /api/v1/health         -> 200 JSON
GET  /api/v1/capabilities   -> 200 JSON
GET  /api/v1/does-not-exist -> JSON 404, never SPA HTML
```

Then complete a real login challenge, confirm both `__Host-` cookies are scoped
to the public origin, load the library, and exercise one CSRF-protected mutation.
Browser developer tools must show requests to relative `/api/v1/*` URLs and no
cross-origin preflight.

## Rollback

- Frontend rollback: redeploy the previous known-good `web/dist` artifact.
- API rollback: redeploy the previous backend while keeping the same public
  origin and route split.
- Routing rollback: switch `WEB_SERVE_STATIC=true`, restore the built
  `WEB_STATIC_DIR`, point all public paths back to `web-server`, and re-run the
  verification list above.

Never change cookie or CORS rules as an emergency routing workaround.
