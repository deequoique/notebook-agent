# Validation record

## Local implementation gates

- `python3 ./.trellis/scripts/task.py validate 08-10-youtube-ip-throttling`:
  passed with five real entries in each context manifest.
- Changed Python modules and focused tests compile with `py_compile`.
- Focused connector/config/task plus deployment tests: `119 passed`.
- `sh -n scripts/youtube-home-egress`: passed.
- `git diff --check`: passed.
- Trellis review found no worker-global proxy mutation, TLS bypass, raw stderr or
  signed-URL exception leakage, public listener, persistent Mac service, or
  unproxied retry path.

## Full-suite context

The repository `.env` enables development-only Agent flags, so the full suite
was rerun with production defaults explicitly restored. In the restricted
sandbox the result was `576 passed, 74 skipped, 2 failed`; both failures were
existing HTTP gateway tests denied permission to bind a loopback test socket.

The approved non-sandbox run completed with `636 passed, 9 skipped, 7 failed`.
None of the seven failures imports or exercises the changed YouTube connector,
bounded-fetch, configuration, helper, or deployment path:

- three remote PostgreSQL multiuser integration cases exceeded their hardcoded
  two-second Agent timeout and also failed when rerun alone;
- three logging-capture cases passed together when rerun in isolation;
- one PostgreSQL Web Auth test creates a 12-hour session at a fixed
  `2026-08-07` time and later validates it against the real current date.

No unrelated test or production code was changed to mask those failures.

## Mac and tunnel preflight

- Homebrew Core `tinyproxy 1.11.3` installed after explicit approval. No
  `brew services`, LaunchAgent, system proxy, or third-party tap trust change
  was made.
- Mac and `vps-d2a069a1` port `18080` were free before startup.
- Foreground helper reached ready state.
- Mac listener: `127.0.0.1:18080` only.
- Server reverse listener: `127.0.0.1:18080` only.
- A private comparison confirmed the Mac home and production direct public
  egress values differ; neither IP was printed or recorded.

## Public pre-activation canary

A single public video was fetched from the current production release with
temporary child-process proxy variables, before application configuration:

```text
canary_ok metadata=1 subtitle_bytes=8325 cues=61
```

No signed subtitle URL, subtitle content, proxy log, raw yt-dlp stderr, or
public IP was printed or persisted.

## Production deployment and activation

- Commit `77d4e56ba2598adc3560b9b2c0388c313c9c7169` was pushed to `main`.
- GitHub Actions run `31378095180` passed backend CI, migrations, generated API
  checks, lint/typecheck, frontend tests/build, and the protected Production
  deployment.
- `/opt/notebook-agent/current` resolves to the immutable release for that
  commit; no deployed release was edited in place.
- `/etc/notebook-agent/notebook-agent.env` was backed up to
  `/etc/notebook-agent/notebook-agent.env.bak-youtube-proxy-20260810T102154Z`,
  then atomically updated with the validated loopback proxy setting.
- Only `notebook-agent-worker` was restarted. The combined runtime and Beat
  process IDs remained unchanged, and the restarted Worker inherited the new
  setting.

## Post-activation production evidence

A transient systemd service ran the deployed connector under
`/etc/notebook-agent/notebook-agent.env`, matching the Worker's configuration
source without sourcing or printing that file. It produced only safe counts:

```text
post_activation_canary=ok metadata=1 subtitle_bytes=8325 cues=61
```

At the same check point:

- `notebook-agent`, Worker, Beat, and the dependency unit were active;
- the combined loopback health endpoint returned `status=ok`;
- Redis and MinIO both reported `healthy/running`;
- the server proxy had exactly one listener, `127.0.0.1:18080`;
- the root-owned environment file contained the exact validated loopback
  setting.

No database row, object-store object, user content, subtitle content, signed
URL, raw stderr, credential, or public IP was emitted or created by this
connector canary.

## Fail-closed rehearsal and restoration

The foreground Mac helper was stopped cleanly. The Mac and server listeners
both disappeared while the Worker configuration remained enabled. A bounded
connector attempt under the real Worker environment returned:

```text
fail_closed_canary=ok classification=youtube_proxy_unavailable direct_fallback=none
```

The helper was then restarted in the foreground. Both loopback listeners
returned, all service/dependency health checks passed again, and the same
post-activation public connector canary again returned 8,325 subtitle bytes
and 61 cues. The helper remains an on-demand foreground process; no
LaunchAgent, Homebrew service, system proxy, persistent tunnel, cookie, OAuth
credential, or browser profile was added.

## Operator-approved validation exception

The full queue-to-`ready` acceptance canary has not run. It would temporarily
create an isolated production `app_user` and content item, publish through the
real Redis/Celery Worker, write one raw object to MinIO, invoke the configured
Embedding provider, validate non-empty segments/timings/vectors, then delete
the raw object and database rows. The attempted command was rejected before
remote execution, so it created no production record and incurred no Embedding
call.

On 2026-08-10 the operator explicitly chose to skip that assistant-run
production mutation and test a normal item manually. Therefore the PRD's full
queue-to-`ready` criterion and its duplicate-safe item-retry criterion remain
unchecked and are accepted as a documented validation gap, not reported as
passing. No user-selected failed item was provided or retried by the assistant.

The rollback backup remains retained. Connector-level production recovery,
service health, loopback exposure, fail-closed behavior, and restoration are
all verified; task closure does not claim end-to-end `ready` evidence.

## Spec review

No additional specification update is needed for this final operational pass.
The loopback-only proxy boundary, child-only environment, privacy-safe failure
classification, fail-closed behavior, and foreground helper lifecycle were
already captured in `.trellis/spec/backend/youtube-connector.md` in the work
commit. The operator's decision to perform the final item test manually is a
task-specific validation exception rather than a reusable connector contract.
