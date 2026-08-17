from pathlib import Path
import re

from fastapi.testclient import TestClient

from app.api.app import create_app


ROOT = Path(__file__).resolve().parents[1]
NGINX_SITE = ROOT / "deploy" / "nginx" / "notebook-agent-web.conf"
NGINX_HEADERS = (
    ROOT / "deploy" / "nginx" / "notebook-agent-web-security-headers.conf"
)
SYSTEMD_UNIT = ROOT / "deploy" / "systemd" / "notebook-agent-web.service"
MIGRATION_UNIT = (
    ROOT / "deploy" / "systemd" / "notebook-agent-web-migrate.service"
)
FRONTEND_GUIDE = ROOT / "docs" / "deployment" / "frontend.md"


def _required_text(path: Path) -> str:
    assert path.is_file(), f"missing deployment artifact: {path.relative_to(ROOT)}"
    return path.read_text(encoding="utf-8")


def test_nginx_serves_the_spa_and_keeps_all_api_paths_out_of_the_fallback():
    site = _required_text(NGINX_SITE)
    headers = _required_text(NGINX_HEADERS)

    assert "server_name kb.example.com;" in site
    assert "default_server" in site
    assert "listen 443 ssl default_server;" in site
    assert "return 301 https://kb.example.com$request_uri;" in site
    assert "root /opt/notebook-agent/current/web/dist;" in site
    assert "location ^~ /api/" in site
    assert "proxy_pass http://127.0.0.1:8000;" in site
    assert "proxy_set_header Host $server_name;" in site
    assert "proxy_set_header X-Forwarded-Proto $scheme;" in site
    assert "proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;" in site
    assert "proxy_cache_bypass 1;" in site
    assert "try_files $uri $uri/ /index.html;" in site
    assert "public, max-age=31536000, immutable" in site
    assert "no-store" in site
    assert "access_log off;" in site
    assert "proxy_pass http://127.0.0.1:8765" not in site

    assert "Content-Security-Policy" in headers
    assert "connect-src 'self'" in headers
    assert "frame-ancestors 'none'" in headers
    assert "Strict-Transport-Security" in headers
    assert "X-Content-Type-Options" in headers
    assert "Referrer-Policy" in headers
    assert "Permissions-Policy" in headers


def test_nginx_browser_policy_matches_the_python_web_policy_exactly():
    headers = _required_text(NGINX_HEADERS)
    configured = dict(
        re.findall(r'^add_header ([^ ]+) "([^"]+)" always;$', headers, re.MULTILINE)
    )
    with TestClient(create_app(expected_origin="https://kb.example.com")) as client:
        response = client.get("/api/v1/health")

    expected_names = {
        "X-Content-Type-Options",
        "X-Frame-Options",
        "Referrer-Policy",
        "Permissions-Policy",
        "Content-Security-Policy",
        "Strict-Transport-Security",
    }
    assert configured.keys() == expected_names
    assert configured == {name: response.headers[name] for name in expected_names}


def test_systemd_runs_only_the_loopback_api_with_private_runtime_settings():
    unit = _required_text(SYSTEMD_UNIT)

    assert "WorkingDirectory=/opt/notebook-agent/current" in unit
    assert "EnvironmentFile=/etc/notebook-agent/notebook-agent.env" in unit
    assert "Environment=WEB_SERVE_STATIC=false" in unit
    assert "Environment=WEB_HOST=127.0.0.1" in unit
    assert "Environment=WEB_PORT=8000" in unit
    assert "Environment=WEB_COOKIE_SECURE=true" in unit
    assert "Environment=WEB_FORWARDED_ALLOW_IPS=127.0.0.1" in unit
    assert (
        "ExecStart=/opt/notebook-agent/current/.venv/bin/python -m app.cli web-server"
        in unit
    )
    assert (
        "ExecStartPre=/usr/bin/test -f "
        "/opt/notebook-agent/current/.migration-admitted" in unit
    )
    assert "NoNewPrivileges=true" in unit
    assert "ProtectSystem=strict" in unit
    assert "ProtectHome=true" in unit
    assert "PrivateTmp=true" in unit
    assert "UMask=0027" in unit
    assert "WEB_AUTH_SECRET=" not in unit
    assert "DATABASE_URL=" not in unit


def test_migration_unit_reuses_systemd_environment_without_sourcing_secrets():
    unit = _required_text(MIGRATION_UNIT)

    assert "Type=oneshot" in unit
    assert "User=notebook-agent" in unit
    assert "WorkingDirectory=/opt/notebook-agent/current" in unit
    assert "EnvironmentFile=/etc/notebook-agent/notebook-agent.env" in unit
    assert "ExecStart=/opt/notebook-agent/current/.venv/bin/alembic upgrade head" in unit
    assert "ExecStart=/opt/notebook-agent/current/.venv/bin/alembic current" in unit
    assert "ExecStart=/opt/notebook-agent/current/.venv/bin/alembic check" in unit
    assert "ExecStart=/usr/bin/touch /opt/notebook-agent/current/.migration-admitted" in unit
    assert "ReadWritePaths=/opt/notebook-agent/releases" in unit
    assert "ConditionPathExists" not in unit
    assert "/bin/sh" not in unit
    assert "/bin/bash" not in unit


def test_linux_guide_documents_atomic_delivery_smoke_and_rollback_boundaries():
    guide = _required_text(FRONTEND_GUIDE)

    assert "deploy/nginx/notebook-agent-web.conf" in guide
    assert "deploy/systemd/notebook-agent-web.service" in guide
    assert "deploy/systemd/notebook-agent-web-migrate.service" in guide
    assert "/opt/notebook-agent/releases/" in guide
    assert "release-manifest.env" in guide
    assert "ln -sfn" in guide
    assert "nginx -t" in guide
    assert "systemd-analyze verify" in guide
    assert "systemctl restart notebook-agent-web" in guide
    assert "GET  /api/v1/does-not-exist" in guide
    assert "127.0.0.1:8765" in guide
    assert "不要" in guide and "自动" in guide and "downgrade" in guide
    assert "source /etc/notebook-agent/notebook-agent.env" not in guide
    assert "|| true" not in guide

    provision_step = guide.index(
        "sudo install -d -o notebook-agent -g notebook-agent"
    )
    build_step = guide.index("git -C \"${repo}\" worktree add")
    assert provision_step < build_step

    reload_units_step = guide.index("sudo systemctl daemon-reload")
    stop_service_step = guide.index("if ! sudo systemctl stop notebook-agent-web")
    query_state_step = guide.index(
        "sudo systemctl show notebook-agent-web --property=ActiveState --value"
    )
    assert_inactive_step = guide.index("refusing to switch an active Web service")
    switch_step = guide.index(
        "sudo mv -Tf /opt/notebook-agent/current.next /opt/notebook-agent/current"
    )
    assert (
        reload_units_step
        < stop_service_step
        < query_state_step
        < assert_inactive_step
        < switch_step
    )
    assert "failed to stop Web service" in guide
    assert "failed to query Web service state" in guide

    migration_gate = "if ! sudo systemctl start notebook-agent-web-migrate.service"
    assert migration_gate in guide
    migration_gate_step = guide.index(migration_gate)
    migration_failure_step = guide.index("migration admission failed")
    enable_step = guide.index("sudo systemctl enable notebook-agent-web")
    restart_step = guide.index("sudo systemctl restart notebook-agent-web")
    assert migration_gate_step < migration_failure_step < enable_step < restart_step
    assert "/opt/notebook-agent/current/.migration-admitted" in guide
    assert "survives a reboot" in guide

    rollback = guide[guide.index("### 6. Roll back the paired release") :]
    for required in (
        'test -f "${previous_release}/.migration-admitted"',
        "live_schema=",
        "schema admission failed",
        "refusing to switch an active Web service during rollback",
    ):
        assert required in rollback
    rollback_schema_step = rollback.index("live_schema=")
    rollback_schema_failure_step = rollback.index("schema admission failed")
    rollback_link_step = rollback.index(
        "sudo ln -sfn \"${previous_release}\" /opt/notebook-agent/current.next"
    )
    rollback_stop_step = rollback.index("if ! sudo systemctl stop notebook-agent-web")
    rollback_query_step = rollback.index(
        "sudo systemctl show notebook-agent-web --property=ActiveState --value"
    )
    rollback_inactive_step = rollback.index(
        "refusing to switch an active Web service during rollback"
    )
    rollback_switch_step = rollback.index(
        "sudo mv -Tf /opt/notebook-agent/current.next /opt/notebook-agent/current"
    )
    assert (
        rollback_schema_step
        < rollback_schema_failure_step
        < rollback_link_step
        < rollback_stop_step
        < rollback_query_step
        < rollback_inactive_step
        < rollback_switch_step
    )
    assert "failed to stop Web service during rollback" in rollback
    assert "failed to query Web service state during rollback" in rollback
    assert "failed to restart Web service during rollback" in rollback

    certificate_step = guide.index("Provision or install the TLS certificate")
    install_site_step = guide.index(
        "sudo install -m 0644 deploy/nginx/notebook-agent-web.conf"
    )
    validate_step = guide.index("sudo nginx -t")
    assert certificate_step < install_site_step < validate_step
