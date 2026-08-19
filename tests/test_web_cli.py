from types import SimpleNamespace

import uvicorn

from app import cli
from app.api import runtime


def test_web_server_disables_query_bearing_uvicorn_access_logs(monkeypatch):
    settings = SimpleNamespace(
        notebook_agent_log_dir=".runtime/logs",
        notebook_agent_log_max_bytes=1024,
        notebook_agent_log_backup_count=2,
        web_host="127.0.0.1",
        web_port=8000,
        web_forwarded_allow_ips="127.0.0.1",
    )
    application = object()
    captured = {}
    calls = []
    monkeypatch.setattr("sys.argv", ["kb", "web-server"])
    monkeypatch.setattr(cli, "get_settings", lambda: settings)
    monkeypatch.setattr(runtime, "build_web_app", lambda value: application)
    monkeypatch.setattr(
        cli,
        "configure_runtime_logging",
        lambda **kwargs: calls.append(("logging", kwargs)),
    )
    monkeypatch.setattr(
        uvicorn,
        "run",
        lambda app, **kwargs: (
            calls.append(("uvicorn", None)),
            captured.update(app=app, **kwargs),
        ),
    )

    cli.main()

    assert captured["app"] is application
    assert captured["access_log"] is False
    assert calls == [
        (
            "logging",
            {
                "log_dir": ".runtime/logs",
                "max_bytes": 1024,
                "backup_count": 2,
            },
        ),
        ("uvicorn", None),
    ]


def test_stdio_mcp_configures_logging_on_stderr_before_server_start(monkeypatch):
    from app import mcp_server

    settings = SimpleNamespace(
        notebook_agent_log_dir=".runtime/logs",
        notebook_agent_log_max_bytes=1024,
        notebook_agent_log_backup_count=2,
    )
    calls = []
    monkeypatch.setattr("sys.argv", ["kb", "mcp-server", "--transport", "stdio"])
    monkeypatch.setattr(cli, "get_settings", lambda: settings)
    monkeypatch.setattr(
        cli,
        "configure_runtime_logging",
        lambda **kwargs: calls.append(("logging", kwargs)),
    )
    monkeypatch.setattr(
        mcp_server,
        "run_stdio",
        lambda *, settings: calls.append(("stdio", settings)),
    )

    cli.main()

    assert calls == [
        (
            "logging",
            {
                "log_dir": ".runtime/logs",
                "max_bytes": 1024,
                "backup_count": 2,
                "console_stream": "stderr",
            },
        ),
        ("stdio", settings),
    ]
