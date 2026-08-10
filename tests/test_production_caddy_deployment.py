from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _text(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_caddy_site_adds_only_the_notebook_hostname_and_loopback_upstream():
    site = _text("deploy/caddy/notebook-agent.caddy")

    assert "notebookai.deequoique.tech" in site
    assert "reverse_proxy 127.0.0.1:8800" in site
    assert "output discard" in site
    assert "localhost:3030" not in site
    assert "www.deequoique.tech" not in site


def test_combined_runtime_and_background_units_are_isolated():
    runtime = _text("deploy/systemd/notebook-agent.service")
    worker = _text("deploy/systemd/notebook-agent-worker.service")
    beat = _text("deploy/systemd/notebook-agent-beat.service")

    assert "mcp-server --transport streamable-http" in runtime
    assert "Environment=MCP_HOST=127.0.0.1" in runtime
    assert "Environment=MCP_PORT=8800" in runtime
    assert "Environment=MCP_URL_TOKEN_MODE=true" in runtime
    assert "Environment=WEB_AUTH_ENABLED=true" in runtime
    assert "web-server" not in runtime
    assert "--queues=ingest,maintenance" in worker
    assert " worker " in worker
    assert " beat " in beat
    assert "--schedule=/var/lib/notebook-agent/celerybeat-schedule" in beat
    assert "CHANNEL_GATEWAY_SECRET=" not in runtime + worker + beat
    for unit in (runtime, worker, beat):
        assert "Environment=NOTEBOOK_AGENT_LOG_DIR=/var/log/notebook-agent" in unit
        assert "ReadWritePaths=" in unit


def test_private_gateway_and_langbot_units_are_isolated_and_telegram_only():
    gateway = _text("deploy/systemd/notebook-agent-gateway.service")
    langbot = _text("deploy/systemd/notebook-agent-langbot.service")
    bootstrap = _text("deploy/scripts/bootstrap-production-langbot")
    wait = _text("deploy/scripts/notebook-agent-wait-gateway")
    patch = _text("integrations/langbot-4.10.6-redact-monitoring.patch")
    caddy = _text("deploy/caddy/notebook-agent.caddy")

    assert "Environment=CHANNEL_GATEWAY_HOST=127.0.0.1" in gateway
    assert "Environment=CHANNEL_GATEWAY_PORT=8765" in gateway
    assert "python -m app.cli gateway-server" in gateway
    assert "EnvironmentFile=/etc/notebook-agent/notebook-agent.env" in gateway
    assert "MIGRATION_DATABASE_URL" not in gateway

    assert "User=notebook-langbot" in langbot
    assert "Group=notebook-langbot" in langbot
    assert "EnvironmentFile=" not in langbot
    assert "LANGBOT_DATA_ROOT=/var/lib/notebook-agent/langbot/data" in langbot
    assert "PYTHONPATH=/opt/notebook-agent/shared/langbot/patched_site" in langbot
    assert "notebook-agent-wait-gateway" in langbot
    assert "ReadWritePaths=/var/lib/notebook-agent/langbot" in langbot
    assert "MIGRATION_DATABASE_URL" not in langbot

    assert "http://127.0.0.1:8765/health" in wait
    assert "sleep 1" in wait
    assert "sleep 5" not in wait
    assert "+                    host='127.0.0.1'," in patch
    assert "host='0.0.0.0'" in patch

    assert "required_plugins_ready_timeout_seconds" in bootstrap
    assert 'KB_BOT_CHANNELS={}\\n' in bootstrap
    assert '"telegram"' not in bootstrap
    assert '"wechat"' not in bootstrap
    assert "enable_marketplace\"] = False" in bootstrap
    assert "disable_telemetry\"] = True" in bootstrap
    assert "box\"][\"enabled\"] = False" in bootstrap
    assert "langbot-4.10.6-redact-monitoring.patch" in bootstrap
    assert "ee950fd6a687cb8c7cfe646d2b9a92cfbf09b3ddfbaf8f43ea0613905d3ffbff" in bootstrap
    assert "'mcp>=1.25,<2'" in bootstrap

    assert "5300" not in caddy
    assert "8765" not in caddy


def test_dependency_admission_requires_the_owned_minio_bucket():
    dependencies = _text("deploy/systemd/notebook-agent-dependencies.service")

    assert "up -d --wait redis minio" in dependencies
    assert "run --rm minio-init" in dependencies
    assert dependencies.index("up -d --wait redis minio") < dependencies.index(
        "run --rm minio-init"
    )


def test_direct_migration_credentials_are_not_loaded_by_long_lived_units():
    migrate = _text("deploy/systemd/notebook-agent-migrate.service")
    runtime = _text("deploy/systemd/notebook-agent.service")
    worker = _text("deploy/systemd/notebook-agent-worker.service")
    beat = _text("deploy/systemd/notebook-agent-beat.service")

    assert "EnvironmentFile=/etc/notebook-agent/migrations.env" in migrate
    assert "EnvironmentFile=/etc/notebook-agent/notebook-agent.env" not in migrate
    for unit in (runtime, worker, beat):
        assert "EnvironmentFile=/etc/notebook-agent/notebook-agent.env" in unit
        assert "migrations.env" not in unit
        assert "MIGRATION_DATABASE_URL" not in unit


def test_dependency_compose_has_no_postgres_and_publishes_only_loopback():
    compose = _text("deploy/compose/production-dependencies.yml")

    assert "  postgres:" not in compose
    assert '"127.0.0.1:${NOTEBOOK_REDIS_PORT:-16379}:6379"' in compose
    assert '"127.0.0.1:${NOTEBOOK_MINIO_API_PORT:-19000}:9000"' in compose
    assert "--appendfsync always" in compose
    assert "--requirepass" in compose
    assert "  minio-init:" in compose
    assert "mc mb --ignore-existing" in compose
    assert "MINIO_BUCKET" in compose


def test_production_workflow_requires_ci_environment_and_serialization():
    workflow = _text(".github/workflows/web-auth-contract.yml")

    gates = workflow[: workflow.index("  deploy-production:")]
    assert "python -m venv .venv" in gates
    assert ".venv/bin/python -m pip install -e '.[dev]'" in gates
    assert ".venv/bin/python -m pytest -q" in gates
    assert ".venv/bin/alembic heads" in gates

    deploy = workflow[workflow.index("  deploy-production:") :]
    assert "needs: deterministic-web-gates" in deploy
    assert "name: Production" in deploy
    assert "cancel-in-progress: false" in deploy
    assert '"deploy $GITHUB_SHA"' in deploy
    assert "StrictHostKeyChecking=yes" in deploy
    for secret in (
        "PRODUCTION_SSH_PRIVATE_KEY",
        "PRODUCTION_SSH_KNOWN_HOSTS",
        "PRODUCTION_SSH_HOST",
        "PRODUCTION_SSH_USER",
    ):
        assert secret in deploy


def test_deploy_dispatcher_accepts_only_an_exact_sha_and_keeps_data():
    script = _text("deploy/scripts/notebook-agent-deploy")

    assert "SSH_ORIGINAL_COMMAND" in script
    assert "^deploy\\ ([0-9a-f]{40})$" in script
    assert "another production deployment is active" in script
    assert "origin/main" in script
    assert "git reset" not in script
    assert "rm -" not in script
    assert "docker compose down" not in script
    assert ".release-built" in script
    assert '/usr/bin/env --chdir="$release_dir/web"' in script
    assert "dependency admission failed; previous release restored" in script
    assert "application startup failed; previous release restored" in script
    assert "http://127.0.0.1:8765/health" in script
    assert "http://127.0.0.1:5300/healthz" in script
    assert "Required plugins initialized; message adapters may start." in script

    stop_langbot = script.index(
        "systemctl stop notebook-agent-langbot.service notebook-agent-gateway.service"
    )
    switch_release = script.index('mv -Tf /opt/notebook-agent/current.next "$current"')
    start_core = script.index("systemctl restart notebook-agent-worker.service")
    start_gateway = script.index("systemctl restart notebook-agent-gateway.service")
    start_langbot = script.index("systemctl restart notebook-agent-langbot.service")
    assert stop_langbot < switch_release < start_core < start_gateway < start_langbot


def test_ssh_dispatcher_validates_before_crossing_the_sudo_boundary():
    script = _text("deploy/scripts/notebook-agent-ssh-dispatch")

    assert "SSH_ORIGINAL_COMMAND" in script
    assert "^deploy\\ ([0-9a-f]{40})$" in script
    assert "unset SSH_ORIGINAL_COMMAND" in script
    assert (
        'exec sudo -n /usr/local/sbin/notebook-agent-deploy deploy "$release_sha"'
        in script
    )

    sudoers = _text("deploy/sudoers/notebook-agent-deploy")
    assert sudoers.strip() == (
        "notebook-deploy ALL=(root) NOPASSWD: "
        "/usr/local/sbin/notebook-agent-deploy"
    )
