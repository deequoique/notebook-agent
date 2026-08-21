"""Private, allow-listed runtime diagnostics and bounded local log sinks."""

from __future__ import annotations

import json
import logging
import os
import re
import sys
import time
import math
from dataclasses import dataclass
from datetime import date
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any


LOGGER = logging.getLogger("notebook_agent.runtime")
_TRACE_RE = re.compile(r"[0-9a-f]{32}\Z")
_REQUEST_RE = re.compile(r"[0-9a-f]{32}\Z")
_STAGES = frozenset({
    "accepted", "route", "duplicate", "gateway_response_ready", "agent_started",
    "model_attempt", "tool_call", "embedding_started", "embedding_completed",
    "embedding_failed", "retrieval_started", "retrieval_completed", "retrieval_failed",
    "citation_validated", "context_compressed", "agent_failed", "action_validated",
    "recovery", "todo_used",
    "completion_event_created", "completion_event_enqueued",
    "completion_event_publish_failed", "completion_event_sweep",
})
_ROUTES = frozenset({"agent", "command", "duplicate", "action"})
_TOOLS = frozenset({
    "search_segments", "get_neighbors", "get_item", "open_at", "todo_write",
    "request_save_confirmation", "save_videos", "confirm_video_save",
    "clarify_save_confirmation", "cancel_video_save", "list_saved_items",
    "get_saved_item", "update_saved_item", "delete_saved_items",
    "confirm_item_deletion", "clarify_item_deletion", "cancel_item_deletion",
    "restore_saved_items", "retry_item_ingestion",
})
_LIMITS = frozenset({"request", "tool_calls", "output_tokens", "unknown"})
_AGENT_PHASES = frozenset({"retrieval", "answer"})
_ERROR_CLASS_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,63}\Z")
_ANSWER_FAILURE_REASONS = frozenset({
    "invalid_structure", "unsafe_text", "missing_citation",
    "invalid_citation", "unknown_citation", "duplicate_citation",
    "too_many_segments", "too_many_items",
    "missing_scope_item", "provider_failure",
})
_RECOVERY_CATEGORIES = frozenset({
    "transient_read", "read_unavailable", "missing_context",
    "policy_or_security", "side_effect_indeterminate", "provider_failure",
    "answer_validation",
})
_RECOVERY_ACTIONS = frozenset({
    "retry_same_read", "use_existing_evidence", "return_partial",
    "ask_clarification", "report_unavailable", "reformulate_search",
    "repair_answer",
})
_RECOVERY_OUTCOMES = frozenset({"granted", "consumed", "denied", "exhausted"})
_ERRORS = frozenset({
    "-", "no_evidence", "embedding_unavailable", "retrieval_unavailable",
    "timeout", "limit", "answer_unavailable", "runtime_error", "not_found",
    "search_required", "empty_answer", "identity_error", "thread_missing",
    "queue_unavailable", "quota_exceeded", "invalid_envelope",
    "item_not_found", "invalid_cursor", "invalid_batch", "invalid_why_saved",
    "invalid_location", "invalid_filter", "management_failed", "object_delete_failed",
    "confirmation_required", "confirmation_missing", "confirmation_expired",
    "item_deleted", "item_expired", "retry_not_allowed", "management_unavailable",
    "management_failed", "items_listed", "item_read", "item_updated", "items_deleted",
    "items_restored", "delete_cancelled", "retry_queued", "purge_failed",
    "purge_in_progress",
    "delete_failed",
    "delete_in_progress",
    "save_confirmation_required", "save_cancelled", "save_partial", "save_accepted",
    "save_failed", "save_unavailable", "invalid_url", "batch_too_large", "empty_batch",
    "channel_unavailable", "challenge_invalid", "challenge_expired",
    "challenge_used", "account_disabled", "web_login_unavailable",
    "ingestion_failed", "transient_fetch_failed", "ingest_too_large", "completion_publish_failed",
    "transient_read", "read_unavailable", "answer_validation", "provider_failure",
    "todo_incomplete", "item_scope_required",
})
_DISPOSITIONS = frozenset({"grounded", "no_evidence", "canonical", "action", "failed"})


def new_trace_id() -> str:
    """Create a random opaque correlation id; it carries no business meaning."""

    from uuid import uuid4
    return uuid4().hex


def is_trace_id(value: object) -> bool:
    return isinstance(value, str) and bool(_TRACE_RE.fullmatch(value))


def classify_usage_limit(exc: BaseException) -> tuple[str, int | None, int | None]:
    """Project known PydanticAI limit text without ever logging that text."""

    message = str(exc)
    patterns = {
        "request": r"^The next request would exceed the request_limit of (\d+)",
        "tool_calls": r"^The next tool call\(s\) would exceed the tool_calls_limit of (\d+) \(tool_calls=(\d+)\)",
        "output_tokens": r"^Exceeded the output_tokens_limit of (\d+) \(output_tokens=(\d+)\)",
    }
    for kind, pattern in patterns.items():
        match = re.match(pattern, message)
        if match:
            limit = int(match.group(1))
            used = int(match.group(2)) if match.lastindex and match.lastindex >= 2 else None
            return kind, limit, used
    return "unknown", None, None


class _SafeJsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = getattr(record, "diagnostic_payload", None)
        if not isinstance(payload, dict):
            payload = {"event": "runtime_diagnostic_unavailable"}
        return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


class DailySizeRotatingFileHandler(RotatingFileHandler):
    """One owner, daily files with bounded in-day size rotations."""

    def __init__(self, directory: Path, *, max_bytes: int, backup_count: int,
                 stdout_handler: logging.Handler | None = None) -> None:
        self.directory = directory
        self._active_day = date.today()
        self._max_files = backup_count
        self._stdout_handler = stdout_handler
        self._reported_failure = False
        directory.mkdir(mode=0o750, parents=True, exist_ok=True)
        if os.name != "nt":
            os.chmod(directory, 0o750)
            if os.stat(directory).st_mode & 0o777 != 0o750:
                raise PermissionError("log directory mode could not be secured")
        super().__init__(
            self._path_for_day(self._active_day), maxBytes=max_bytes,
            backupCount=backup_count, encoding="utf-8", delay=False,
        )

    def _path_for_day(self, day: date) -> str:
        return str(self.directory / f"notebook-agent-{day.isoformat()}.log")

    def emit(self, record: logging.LogRecord) -> None:
        try:
            today = date.today()
            if today != self._active_day:
                if self.stream:
                    self.stream.close()
                    self.stream = None
                self._active_day = today
                self.baseFilename = os.path.abspath(self._path_for_day(today))
            super().emit(record)
            self._trim_days()
        except Exception:
            self.handleError(record)

    def _open(self):
        stream = super()._open()
        try:
            if os.name != "nt":
                os.chmod(self.baseFilename, 0o640)
                if os.stat(self.baseFilename).st_mode & 0o777 != 0o640:
                    raise PermissionError("log file mode could not be secured")
            return stream
        except Exception:
            try:
                stream.close()
            except Exception:
                pass
            raise

    def _trim_days(self) -> None:
        # Keep current file plus at most backup_count previous rotated/day files.
        files = sorted(self.directory.glob("notebook-agent-*.log*"), key=lambda p: p.stat().st_mtime)
        excess = len(files) - (self._max_files + 1)
        for path in files[:max(0, excess)]:
            path.unlink()

    def handleError(self, record: logging.LogRecord) -> None:  # noqa: N802
        # Logging is observability only. In particular, do not let a transient
        # disk failure surface a traceback (which could contain record data) or
        # change the request/response path.
        if self._reported_failure:
            return
        self._reported_failure = True
        LOGGER.removeHandler(self)
        try:
            self.close()
        except Exception:
            pass
        if self._stdout_handler is not None:
            fallback = LOGGER.makeRecord(
                LOGGER.name, logging.INFO, __file__, 0, "diagnostic", (), None,
                extra={"diagnostic_payload": {
                    "event": "file_logging_unavailable", "error_class": "OSError",
                }},
            )
            self._stdout_handler.handle(fallback)


def configure_runtime_logging(
    *, log_dir: str, max_bytes: int = 10 * 1024 * 1024, backup_count: int = 5,
    console_stream: str = "stdout",
) -> bool:
    """Install idempotent stdout + optional private file handlers.

    Returns whether the file sink is active. A broken file sink never prevents
    stdout/journal diagnostics or request handling.
    """

    if max_bytes <= 0 or backup_count <= 0:
        raise ValueError("logging rotation limits must be positive")
    if console_stream not in {"stdout", "stderr"}:
        raise ValueError("console_stream must be stdout or stderr")
    config = (str(Path(log_dir).resolve()), max_bytes, backup_count, console_stream)
    managed = [
        handler
        for handler in LOGGER.handlers
        if getattr(handler, "_notebook_runtime_config", None) == config
    ]
    if managed:
        return any(
            isinstance(handler, DailySizeRotatingFileHandler)
            for handler in managed
        )
    for handler in list(LOGGER.handlers):
        LOGGER.removeHandler(handler)
        handler.close()
    LOGGER.setLevel(logging.INFO)
    LOGGER.propagate = False
    formatter = _SafeJsonFormatter()
    console = logging.StreamHandler(
        sys.stderr if console_stream == "stderr" else sys.stdout
    )
    console._notebook_runtime_config = config  # type: ignore[attr-defined]
    console.setFormatter(formatter)
    LOGGER.addHandler(console)
    try:
        file_handler = DailySizeRotatingFileHandler(
            Path(log_dir), max_bytes=max_bytes, backup_count=backup_count,
            stdout_handler=console,
        )
        file_handler._notebook_runtime_config = config  # type: ignore[attr-defined]
        file_handler.setFormatter(formatter)
        LOGGER.addHandler(file_handler)
    except OSError as exc:
        LOGGER.info(
            "diagnostic", extra={"diagnostic_payload": {
                "event": "file_logging_unavailable", "error_class": type(exc).__name__,
            }}
        )
        return False
    LOGGER.info("diagnostic", extra={"diagnostic_payload": {"event": "runtime_logging_enabled"}})
    return True


def shutdown_runtime_logging() -> None:
    for handler in list(LOGGER.handlers):
        LOGGER.removeHandler(handler)
        handler.close()


@dataclass(frozen=True)
class RequestDiagnostics:
    """Only serializes the explicit event contract; all content is rejected."""

    request_id: str
    tenant_id: int
    trace_id: str
    started_at: float
    allow_retrieval_content: bool = False
    environment: str = "production"

    @classmethod
    def start(
        cls, request_id: str, tenant_id: int, trace_id: str | None = None,
        *, allow_retrieval_content: bool = False, environment: str = "production"
    ) -> "RequestDiagnostics":
        safe_request_id = request_id if isinstance(request_id, str) and _REQUEST_RE.fullmatch(request_id) else new_trace_id()
        enabled = environment == "development" and allow_retrieval_content is True
        return cls(safe_request_id, _safe_int(tenant_id), trace_id if is_trace_id(trace_id) else new_trace_id(), time.monotonic(), enabled, environment if environment in {"development", "production"} else "production")

    def retrieval_detail(self, *, tool_name: str, call_index: int, query: str | None = None,
                         limit: int | None = None, radius: int | None = None,
                         item_id: int | None = None, segment_id: int | None = None,
                         title: str | None = None, author: str | None = None,
                         description: str | None = None, url: str | None = None,
                         score: float | None = None, excerpt: str | None = None,
                         start: float | None = None, anchor: str | None = None) -> None:
        """Local-only typed retrieval facts; no generic payload escape hatch."""
        if not (self.environment == "development" and self.allow_retrieval_content) or tool_name not in {"search_segments", "get_neighbors", "get_item", "open_at"}:
            return
        try:
            payload: dict[str, Any] = {"event": "retrieval_detail", "request_id": self.request_id, "trace_id": self.trace_id, "tenant_id": self.tenant_id, "duration_ms": max(0, int((time.monotonic() - self.started_at) * 1000)), "tool_name": tool_name, "call_index": _safe_int(call_index)}
            strings = {"query": query, "title": title, "author": author, "description": description, "url": url, "excerpt": excerpt, "anchor": anchor}
            for key, value in strings.items():
                projected = _safe_text(value)
                if projected is not None: payload[key] = projected
            for key, value in {"limit": limit, "radius": radius, "item_id": item_id, "segment_id": segment_id}.items():
                projected = _safe_int(value, none=True)
                if projected is not None: payload[key] = projected
            for key, value in {"score": score, "start": start}.items():
                projected = _safe_float(value)
                if projected is not None: payload[key] = projected
            LOGGER.info("diagnostic", extra={"diagnostic_payload": payload})
        except Exception:
            return

    def event(self, stage: str, *, route: str | None = None,
              tool_name: str | None = None, tool_outcome: str | None = None,
              call_index: int | None = None, result_count: int | None = None,
              retry_count: int | None = None, limit_kind: str | None = None,
              limit_value: int | None = None, used_value: int | None = None,
              projected_value: int | None = None, error_code: str | None = None,
              exception: BaseException | None = None,
              duration_ms: int | None = None,
              agent_phase: str | None = None,
              http_status: int | None = None,
              error_category: str | None = None,
              recovery_action: str | None = None,
              recovery_outcome: str | None = None,
              recovery_count: int | None = None,
              error_class: str | None = None,
              failure_reason: str | None = None,
              disposition: str | None = None,
              todo_used: bool | None = None) -> None:
        try:
            safe_error_class = _safe_error_class(error_class)
            payload: dict[str, Any] = {
                "event": "knowledge_request",
                "stage": stage if stage in _STAGES else "agent_failed",
                "request_id": self.request_id, "trace_id": self.trace_id,
                "tenant_id": self.tenant_id,
                "duration_ms": _safe_int(duration_ms) if duration_ms is not None else max(0, int((time.monotonic() - self.started_at) * 1000)),
                "error_code": error_code if error_code in _ERRORS else "-",
                "error_class": safe_error_class or (
                    type(exception).__name__ if exception is not None else "-"
                ),
            }
            if route in _ROUTES: payload["route"] = route
            if tool_name in _TOOLS: payload["tool_name"] = tool_name
            if tool_outcome in {"started", "succeeded", "failed", "skipped"}: payload["tool_outcome"] = tool_outcome
            if agent_phase in _AGENT_PHASES: payload["agent_phase"] = agent_phase
            if error_category in _RECOVERY_CATEGORIES:
                payload["error_category"] = error_category
            if failure_reason in _ANSWER_FAILURE_REASONS:
                payload["failure_reason"] = failure_reason
            if disposition in _DISPOSITIONS:
                payload["disposition"] = disposition
            if recovery_action in _RECOVERY_ACTIONS:
                payload["recovery_action"] = recovery_action
            if recovery_outcome in _RECOVERY_OUTCOMES:
                payload["recovery_outcome"] = recovery_outcome
            if isinstance(recovery_count, int) and not isinstance(recovery_count, bool):
                payload["recovery_count"] = max(0, recovery_count)
            if isinstance(todo_used, bool):
                payload["todo_used"] = todo_used
            safe_http_status = _safe_http_status(http_status)
            if safe_http_status is not None: payload["http_status"] = safe_http_status
            if self.environment == "development" and exception is not None:
                payload["exception_message"] = _debug_exception_text(exception)
                if hasattr(exception, "model_name"):
                    payload["provider_model"] = _debug_json_value(
                        getattr(exception, "model_name")
                    )
                if hasattr(exception, "body"):
                    payload["provider_response_body"] = _debug_json_value(
                        getattr(exception, "body")
                    )
            for key, value in {"call_index": call_index, "result_count": result_count, "retry_count": retry_count, "limit_value": limit_value, "used_value": used_value, "projected_value": projected_value}.items():
                projected = _safe_int(value, none=True)
                if projected is not None: payload[key] = projected
            if limit_kind in _LIMITS: payload["limit_kind"] = limit_kind
            LOGGER.info("diagnostic", extra={"diagnostic_payload": payload})
        except Exception:
            return


def _safe_text(value: object, limit: int = 4096) -> str | None:
    return value[:limit] if isinstance(value, str) else None


def _safe_error_class(value: object) -> str | None:
    """Accept only a bounded class label supplied by an adapter.

    Answer recovery deliberately supplies this label without the exception
    object, so development diagnostics retain a useful failure category while
    never serializing provider messages or response bodies.
    """

    if isinstance(value, str) and _ERROR_CLASS_RE.fullmatch(value):
        return value
    return None


def _safe_int(value: object, *, none: bool = False) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int): return None if none else 0
    return max(0, value)


def _safe_http_status(value: object) -> int | None:
    """Project only an actual, valid HTTP response status."""

    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value if 100 <= value <= 599 else None


def _debug_exception_text(exception: BaseException) -> str:
    """Preserve the complete local-development exception text if possible."""

    try:
        return str(exception)
    except Exception:
        return repr(exception)


def _debug_json_value(value: object) -> object:
    """Normalize arbitrary provider details without breaking JSON logging."""

    try:
        return json.loads(json.dumps(value, ensure_ascii=False, default=repr))
    except Exception:
        return repr(value)


def _safe_float(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)): return None
    result = float(value)
    return result if math.isfinite(result) else None
