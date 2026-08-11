# Implementation plan

## 1. Application proxy boundary

- [x] Add optional, strictly validated `YOUTUBE_PROXY_URL` configuration for a
      credential-free loopback HTTP proxy; reject credentials, paths, queries,
      fragments, non-loopback hosts, missing/invalid ports, and non-HTTP
      schemes.
- [x] Compose a child-only environment for YouTube operations without mutating
      `os.environ`; preserve verified CA variables and prevent ambient
      `NO_PROXY` from bypassing the configured YouTube route.
- [x] Pass the same child environment to yt-dlp metadata resolution and the
      bounded subtitle subprocess.
- [x] Normalize proxy connection/timeout failures without including proxy URLs,
      signed subtitle URLs, raw stderr, or response bodies.
- [x] Preserve direct behavior when the setting is absent and fail closed with
      no direct fallback when it is present.

## 2. Tests and connector contract

- [x] Extend settings tests for the valid loopback URL and every rejected
      shape.
- [x] Extend YouTube connector tests to assert both child runners receive the
      same proxy and both verified CA variables while the parent environment
      remains unchanged.
- [x] Add tests for missing tunnel/proxy failure, 429 classification, timeout,
      bounded subtitle failure, and absence of unproxied fallback.
- [x] Retain existing language-selection, empty-body, size-limit, TLS, and real
      bounded-child regression tests.
- [x] Update `.trellis/spec/backend/youtube-connector.md` only after the code and
      checks establish the final connector contract.

## 3. Foreground Mac helper

- [x] Add a shell helper that requires explicit SSH target/port inputs, checks
      `ssh` and `tinyproxy`, creates private temporary state, renders a
      loopback-only CONNECT-443 tinyproxy configuration, and starts the reverse
      SSH forward with fail-fast and keepalive options.
- [x] Make signal/child-exit cleanup deterministic and leave no LaunchAgent,
      system proxy, permanent config, PID, or tunnel process.
- [x] Add shell/static tests for loopback bindings, forbidden public bindings,
      missing dependency behavior, SSH options, cleanup traps, and secret-free
      output.
- [x] Document the explicit Homebrew installation command without executing it
      automatically.

## 4. Local and remote preflight

- [x] Confirm Mac and server `127.0.0.1:18080` are free immediately before use;
      choose another explicit port if either is occupied.
- [x] Install tinyproxy on the Mac only after separate operator approval.
- [x] Start the foreground helper and verify the server listener is loopback
      only.
- [x] Compare home-proxied versus server-direct public egress privately without
      writing either IP to application logs or task artifacts.
- [x] Run one public metadata-and-subtitle canary through the server loopback
      proxy before touching application configuration.

## 5. Verification before production activation

- [x] Run focused tests:

  ```bash
  python -m pytest -q tests/test_youtube.py tests/test_ingest_submission.py tests/test_tasks.py
  ```

- [x] Run deployment/static checks affected by the helper and environment
      contract:

  ```bash
  sh -n scripts/youtube-home-egress
  python -m pytest -q tests/test_linux_frontend_deployment.py tests/test_production_caddy_deployment.py
  git diff --check
  ```

- [x] Run the full test suite if focused checks pass. See `validation.md` for
      unrelated environment/time-sensitive failures.
- [x] Review the diff for global proxy variables, TLS bypass, raw stderr/URL
      logging, public listeners, unrelated deployment changes, and persistent
      Mac configuration.

## 6. Production rollout

- [x] Deploy the reviewed release through the existing production release
      process; do not edit an immutable release in place.
- [x] Back up and update only the root-owned production environment entry for
      `YOUTUBE_PROXY_URL=http://127.0.0.1:18080` after the helper is healthy.
- [x] Restart only `notebook-agent-worker` and verify the combined service,
      worker, Beat, Redis, MinIO, Web, and MCP remain healthy.
- [x] Run a public connector canary from the deployed release under the same
      systemd environment file as the Worker and verify safe metadata plus a
      non-empty bounded subtitle body.
- [ ] Run one temporary full-ingestion canary through the real queue, Worker,
      MinIO, and Embedding path; validate `ready`, raw-object/content-hash,
      segment timings, and embeddings, then remove its isolated test data.
      The operator explicitly skipped this production-data mutation on
      2026-08-10 and will test a normal item manually. No user-selected failed
      item was supplied for an assistant-run retry.
- [x] Stop on proxy error, 429, bot challenge, 403, unexpected egress, or any
      credential/content leakage; do not fan out across more jobs.

## 7. Rollback and handoff

- [ ] Remove/disable `YOUTUBE_PROXY_URL`, restart only the worker, and confirm
      unrelated services/data remain unchanged.
- [x] During a bounded fail-closed rehearsal, stop the foreground Mac helper
      and confirm both loopback listeners are gone; restore the helper and
      repeat the successful public connector canary immediately afterward.
- [x] Record the exact start/stop/retry procedure, the on-demand availability
      limitation, and the fact that direct YouTube recovery is not guaranteed
      after rollback.
- [ ] Run Trellis check/update-spec/commit/finish steps after recording the
      operator-approved full-ingestion test exception.
