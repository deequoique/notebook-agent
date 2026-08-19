import json
import logging
from datetime import date
from pathlib import Path

import pytest
from pydantic_ai.exceptions import ModelHTTPError

from app.config import Settings
from app.diagnostics import RequestDiagnostics


def test_diagnostics_emit_only_stable_fields(caplog):
    diagnostics = RequestDiagnostics.start("a" * 32, 7)
    private_message = "private question and secret-looking payload"

    with caplog.at_level(logging.INFO, logger="notebook_agent.runtime"):
        diagnostics.event(
            "embedding_failed",
            error_code="embedding_unavailable",
            exception=RuntimeError(private_message),
        )

    payload = caplog.records[-1].diagnostic_payload
    assert payload["request_id"] == "a" * 32
    assert payload["error_code"] == "embedding_unavailable"
    assert payload["error_class"] == "RuntimeError"
    assert private_message not in json.dumps(payload)


def test_diagnostics_preserve_safe_quota_error_code(caplog):
    diagnostics = RequestDiagnostics.start("a" * 32, 7)

    with caplog.at_level(logging.INFO, logger="notebook_agent.runtime"):
        diagnostics.event("agent_failed", error_code="quota_exceeded")

    assert caplog.records[-1].diagnostic_payload["error_code"] == "quota_exceeded"


def test_diagnostics_allow_phase_and_skipped_tool_without_content(caplog):
    diagnostics = RequestDiagnostics.start("a" * 32, 7)

    with caplog.at_level(logging.INFO, logger="notebook_agent.runtime"):
        diagnostics.event(
            "tool_call",
            tool_name="search_segments",
            tool_outcome="skipped",
            call_index=2,
            agent_phase="retrieval",
        )
        diagnostics.event(
            "agent_failed",
            error_code="limit",
            limit_kind="output_tokens",
            limit_value=2000,
            used_value=2066,
            agent_phase="answer",
        )

    skipped, answer_limit = [record.diagnostic_payload for record in caplog.records[-2:]]
    assert skipped["tool_outcome"] == "skipped"
    assert skipped["agent_phase"] == "retrieval"
    assert answer_limit["agent_phase"] == "answer"
    assert answer_limit["limit_kind"] == "output_tokens"


def test_diagnostics_allowlists_answer_failure_reason_without_private_content(caplog):
    diagnostics = RequestDiagnostics.start("a" * 32, 7)

    with caplog.at_level(logging.INFO, logger="notebook_agent.runtime"):
        diagnostics.event(
            "agent_failed",
            error_code="answer_unavailable",
            agent_phase="answer",
            failure_reason="invalid_citation",
        )
        diagnostics.event(
            "agent_failed",
            error_code="answer_unavailable",
            agent_phase="answer",
            failure_reason="PRIVATE-draft-and-provider-payload",
        )

    allowed, rejected = [record.diagnostic_payload for record in caplog.records[-2:]]
    assert allowed["failure_reason"] == "invalid_citation"
    assert "failure_reason" not in rejected
    assert "PRIVATE-draft-and-provider-payload" not in json.dumps([allowed, rejected])


def test_diagnostics_allow_bounded_todo_and_recovery_error_codes_without_content(caplog):
    diagnostics = RequestDiagnostics.start("a" * 32, 7)
    private_todo = "choose https://private.example/item/42"

    with caplog.at_level(logging.INFO, logger="notebook_agent.runtime"):
        diagnostics.event(
            "tool_call",
            tool_name="todo_write",
            tool_outcome="succeeded",
            call_index=1,
            result_count=1,
            agent_phase="retrieval",
            todo_used=True,
        )
        diagnostics.event(
            "agent_failed",
            error_code="todo_incomplete",
            agent_phase="retrieval",
            error_category="missing_context",
            recovery_outcome="denied",
            recovery_count=2,
        )
        diagnostics.event(
            "agent_failed",
            error_code="item_scope_required",
            agent_phase="retrieval",
            error_category="policy_or_security",
            recovery_outcome="denied",
            recovery_count=2,
        )

    todo, incomplete, scope = [record.diagnostic_payload for record in caplog.records[-3:]]
    assert todo["tool_name"] == "todo_write"
    assert todo["todo_used"] is True
    assert incomplete["error_code"] == "todo_incomplete"
    assert scope["error_code"] == "item_scope_required"
    serialized = json.dumps([todo, incomplete, scope])
    assert private_todo not in serialized
    assert "https://" not in serialized


@pytest.mark.parametrize("status", [400, 422, 429, 500, 503])
def test_diagnostics_projects_only_valid_http_status_without_error_body(caplog, status):
    body_sentinel = f"PRIVATE-provider-body-{status}"
    diagnostics = RequestDiagnostics.start("a" * 32, 7)

    with caplog.at_level(logging.INFO, logger="notebook_agent.runtime"):
        diagnostics.event(
            "agent_failed",
            error_code="answer_unavailable",
            exception=ModelHTTPError(status, "test-model", body=body_sentinel),
            http_status=status,
            agent_phase="answer",
        )

    payload = caplog.records[-1].diagnostic_payload
    assert payload["http_status"] == status
    assert payload["error_class"] == "ModelHTTPError"
    assert payload["agent_phase"] == "answer"
    assert "exception_message" not in payload
    assert "provider_model" not in payload
    assert "provider_response_body" not in payload
    assert body_sentinel not in json.dumps(payload)


def test_development_diagnostics_include_complete_provider_error(caplog):
    body_sentinel = "provider rejected output schema"
    body = {
        "error": {
            "message": body_sentinel,
            "type": "invalid_request_error",
            "param": "tools[0].function.parameters",
        },
        "raw_bytes": b"debug-body",
    }
    error = ModelHTTPError(400, "development-model", body=body)
    diagnostics = RequestDiagnostics.start(
        "a" * 32,
        7,
        environment="development",
    )

    with caplog.at_level(logging.INFO, logger="notebook_agent.runtime"):
        diagnostics.event(
            "agent_failed",
            error_code="answer_unavailable",
            exception=error,
            http_status=error.status_code,
            agent_phase="answer",
        )

    payload = caplog.records[-1].diagnostic_payload
    assert payload["http_status"] == 400
    assert payload["exception_message"] == str(error)
    assert payload["provider_model"] == "development-model"
    assert payload["provider_response_body"]["error"]["message"] == body_sentinel
    assert payload["provider_response_body"]["raw_bytes"] == "b'debug-body'"
    json.dumps(payload)


@pytest.mark.parametrize("status", [True, False, 99, 600, -1, 429.0, "429", None])
def test_diagnostics_rejects_invalid_http_status(status, caplog):
    diagnostics = RequestDiagnostics.start("a" * 32, 7)

    with caplog.at_level(logging.INFO, logger="notebook_agent.runtime"):
        diagnostics.event("agent_failed", http_status=status)

    assert "http_status" not in caplog.records[-1].diagnostic_payload


def test_invalid_request_id_is_replaced_before_logging():
    private = "PRIVATE-request-body"
    payloads = []
    diagnostics = RequestDiagnostics.start(private, 7)
    assert diagnostics.request_id != private
    diagnostics.event("accepted")


def test_retrieval_content_is_explicitly_local_only(tmp_path: Path, capsys):
    from app.diagnostics import configure_runtime_logging, shutdown_runtime_logging

    configure_runtime_logging(log_dir=str(tmp_path))
    private = "history-prompt-model-output-secret"
    RequestDiagnostics.start("a" * 32, 1).retrieval_detail(
        tool_name="search_segments", call_index=1, query="allowed query", url="https://allowed", excerpt="allowed excerpt"
    )
    RequestDiagnostics.start("b" * 32, 1, allow_retrieval_content=True, environment="development").retrieval_detail(
        tool_name="search_segments", call_index=1, query="allowed query", url="https://allowed", excerpt="allowed excerpt"
    )
    output = capsys.readouterr().out
    disk = "\n".join(path.read_text() for path in tmp_path.glob("*.log*"))
    assert "allowed query" in output and "allowed query" in disk
    assert private not in output and private not in disk
    shutdown_runtime_logging()


def test_retrieval_detail_projects_score_without_tool_payload_leak(tmp_path: Path, capsys):
    from app.diagnostics import configure_runtime_logging, shutdown_runtime_logging
    from app.agent.types import Citation
    configure_runtime_logging(log_dir=str(tmp_path))
    citation = Citation(item_id=4, segment_id=8, title="title", excerpt="excerpt", url="https://url", start_sec=3)
    citation._retrieval_score = 0.875
    RequestDiagnostics.start("c" * 32, 2, allow_retrieval_content=True, environment="development").retrieval_detail(
        tool_name="search_segments", call_index=2, segment_id=citation.segment_id, score=citation._retrieval_score, title=citation.title, url=citation.url, excerpt=citation.excerpt
    )
    text = capsys.readouterr().out
    assert "0.875" in text
    assert "_retrieval_score" not in citation.model_dump()
    shutdown_runtime_logging()


def test_settings_reject_production_or_unknown_retrieval_content(monkeypatch):
    from app.config import Settings
    monkeypatch.setenv("NOTEBOOK_AGENT_ENV", "production")
    monkeypatch.setenv("NOTEBOOK_AGENT_LOG_RETRIEVAL_CONTENT", "true")
    with __import__("pytest").raises(ValueError): Settings()
    monkeypatch.setenv("NOTEBOOK_AGENT_ENV", "unknown")
    monkeypatch.setenv("NOTEBOOK_AGENT_LOG_RETRIEVAL_CONTENT", "false")
    with __import__("pytest").raises(ValueError): Settings()


def test_settings_validate_composer_provider_budget(monkeypatch):
    monkeypatch.setenv("AGENT_OUTPUT_TOKEN_LIMIT", "2000")
    monkeypatch.setenv("AGENT_COMPOSER_MAX_TOKENS", "0")
    with pytest.raises(ValueError, match="AGENT_COMPOSER_MAX_TOKENS must be positive"):
        Settings()

    monkeypatch.setenv("AGENT_COMPOSER_MAX_TOKENS", "2001")
    with pytest.raises(ValueError, match="must not exceed AGENT_OUTPUT_TOKEN_LIMIT"):
        Settings()

    monkeypatch.setenv("AGENT_COMPOSER_MAX_TOKENS", "2000")
    assert Settings().agent_composer_max_tokens == 2000


def test_context_compressed_diagnostic_contains_only_safe_counts(caplog):
    diagnostics = RequestDiagnostics.start("a" * 32, 7)

    with caplog.at_level(logging.INFO, logger="notebook_agent.runtime"):
        diagnostics.event(
            "context_compressed",
            retry_count=1,
            result_count=8,
            projected_value=20,
            limit_kind="output_tokens",
            agent_phase="answer",
        )

    payload = caplog.records[-1].diagnostic_payload
    assert payload["stage"] == "context_compressed"
    assert payload["retry_count"] == 1
    assert payload["result_count"] == 8
    assert payload["projected_value"] == 20
    assert payload["limit_kind"] == "output_tokens"
    assert set(payload) == {
        "event", "stage", "request_id", "trace_id", "tenant_id",
        "duration_ms", "error_code", "error_class", "agent_phase",
        "retry_count", "result_count", "projected_value", "limit_kind",
    }


def test_runtime_logging_dual_writes_and_keeps_private_sentinels_out(
    tmp_path: Path, capsys
):
    from app.diagnostics import configure_runtime_logging, shutdown_runtime_logging

    configure_runtime_logging(log_dir=str(tmp_path), max_bytes=100, backup_count=2)
    sentinel = "PRIVATE-question-prompt-output-url-token"
    RequestDiagnostics.start("r", 1, "a" * 32).event(
        "embedding_failed", error_code="embedding_unavailable", exception=RuntimeError(sentinel)
    )
    captured = capsys.readouterr().out
    contents = "\n".join(path.read_text() for path in tmp_path.glob("*.log*"))
    assert "embedding_unavailable" in captured and "embedding_unavailable" in contents
    assert sentinel not in captured and sentinel not in contents
    shutdown_runtime_logging()


def test_usage_limit_classifier_never_returns_original_text():
    from app.diagnostics import classify_usage_limit
    from pydantic_ai.exceptions import UsageLimitExceeded

    assert classify_usage_limit(
        UsageLimitExceeded("The next request would exceed the request_limit of 3")
    ) == ("request", 3, None)
    assert classify_usage_limit(
        UsageLimitExceeded("The next tool call(s) would exceed the tool_calls_limit of 7 (tool_calls=6)")
    ) == ("tool_calls", 7, 6)
    assert classify_usage_limit(
        UsageLimitExceeded("Exceeded the output_tokens_limit of 99 (output_tokens=101)")
    ) == ("output_tokens", 99, 101)
    assert classify_usage_limit(RuntimeError("PRIVATE sentinel")) == ("unknown", None, None)


def test_size_rotation_is_bounded_and_file_failure_keeps_stdout(
    tmp_path: Path, capsys
):
    from app.diagnostics import configure_runtime_logging, shutdown_runtime_logging

    configure_runtime_logging(log_dir=str(tmp_path), max_bytes=80, backup_count=2)
    diagnostics = RequestDiagnostics.start("request", 1, "b" * 32)
    for _ in range(8):
        diagnostics.event("accepted")
    assert len(list(tmp_path.glob("notebook-agent-*.log*"))) <= 3
    shutdown_runtime_logging()

    for invalid in (0, -1):
        try:
            configure_runtime_logging(log_dir=str(tmp_path), backup_count=invalid)
        except ValueError:
            pass
        else:
            raise AssertionError("zero/negative backup count must be rejected")
    blocked = tmp_path / "not-a-directory"
    blocked.write_text("block")
    assert not configure_runtime_logging(log_dir=str(blocked))
    assert "file_logging_unavailable" in capsys.readouterr().out
    shutdown_runtime_logging()


def test_daily_handler_switches_to_the_current_date(tmp_path: Path):
    from app.diagnostics import DailySizeRotatingFileHandler

    handler = DailySizeRotatingFileHandler(tmp_path, max_bytes=1024, backup_count=1)
    handler.setFormatter(logging.Formatter("%(message)s"))
    handler._active_day = date(2000, 1, 1)
    handler.emit(logging.makeLogRecord({"msg": "safe", "levelno": logging.INFO, "levelname": "INFO"}))
    assert (tmp_path / f"notebook-agent-{date.today().isoformat()}.log").exists()
    handler.close()


def test_later_file_sink_failure_reports_once_to_stdout(tmp_path: Path, capsys):
    from app.diagnostics import DailySizeRotatingFileHandler, LOGGER, configure_runtime_logging, shutdown_runtime_logging

    assert configure_runtime_logging(log_dir=str(tmp_path))
    file_handler = next(handler for handler in LOGGER.handlers if isinstance(handler, DailySizeRotatingFileHandler))
    record = logging.makeLogRecord({"msg": "safe", "levelno": logging.INFO, "levelname": "INFO"})
    file_handler.handleError(record)
    file_handler.handleError(record)
    output = capsys.readouterr().out
    assert output.count("file_logging_unavailable") == 1
    assert file_handler not in LOGGER.handlers
    shutdown_runtime_logging()


def test_emit_retention_failure_falls_back_without_raising(tmp_path: Path, capsys, monkeypatch):
    from app.diagnostics import DailySizeRotatingFileHandler, LOGGER, configure_runtime_logging, shutdown_runtime_logging

    configure_runtime_logging(log_dir=str(tmp_path))
    handler = next(value for value in LOGGER.handlers if isinstance(value, DailySizeRotatingFileHandler))
    monkeypatch.setattr(handler, "_trim_days", lambda: (_ for _ in ()).throw(OSError("PRIVATE")))
    LOGGER.info("diagnostic", extra={"diagnostic_payload": {"event": "safe"}})
    assert "file_logging_unavailable" in capsys.readouterr().out
    assert handler not in LOGGER.handlers
    shutdown_runtime_logging()
