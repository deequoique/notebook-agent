"""Safe orchestration, assertions, scoring, and sanitized live reports."""

from __future__ import annotations

import asyncio
import json
import os
import re
import secrets
import time
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Iterable

from alembic.config import Config as AlembicConfig
from alembic.script import ScriptDirectory
from pydantic_ai.models.function import FunctionModel
from pydantic_ai.models.test import TestModel
from sqlalchemy import text

from app.agent.provider import build_model
from app.config import Settings
from app.db import get_session_factory
from app.ingest.submission import normalize_item_reference
from app.mcp_grants import McpGrantService
from app.mcp_readiness import assess_mcp_mutation_readiness, probe_mcp_worker
from app.mcp_server import MCP_TOOL_NAMES
from app.models import AppUser

from .fixtures import FixtureState, prepare_fixtures
from .mcp_runtime import LiveMcpSession, ToolTrace
from .schema import Case, Catalog, Turn, capture_path, render_template

ROOT = Path(__file__).resolve().parents[2]
_SAFETY_CRITICAL = frozenset(
    {
        "request_save_confirmation", "save_videos", "confirm_video_save",
        "update_saved_item", "delete_saved_items", "confirm_item_deletion",
        "restore_saved_items", "retry_item_ingestion",
    }
)
_TEARDOWN_TIMEOUT_SECONDS = 10.0


class EvalPreflightError(RuntimeError):
    pass


class EvalTeardownError(RuntimeError):
    """Bounded cleanup failure; never includes provider, token, or DB text."""

    pass


@dataclass(frozen=True)
class EvalConfig:
    enabled: bool
    user_id: int | None
    results_dir: Path
    repeat: int
    threshold: float | None
    ingest_timeout_seconds: float

    @classmethod
    def from_environment(cls) -> "EvalConfig":
        enabled = os.getenv("NATURAL_LANGUAGE_EVAL_ENABLED", "").lower() in {"1", "true", "yes", "on"}
        raw_user = os.getenv("NATURAL_LANGUAGE_EVAL_USER_ID", "").strip()
        raw_threshold = os.getenv("NATURAL_LANGUAGE_EVAL_THRESHOLD", "").strip()
        try:
            user_id = int(raw_user) if raw_user else None
            repeat = int(os.getenv("NATURAL_LANGUAGE_EVAL_REPEAT", "1"))
            threshold = float(raw_threshold) if raw_threshold else None
            timeout = float(os.getenv("NATURAL_LANGUAGE_EVAL_INGEST_TIMEOUT_SECONDS", "900"))
        except ValueError as exc:
            raise EvalPreflightError("invalid natural-language evaluation configuration") from exc
        if user_id is not None and user_id <= 0:
            raise EvalPreflightError("evaluation user id must be positive")
        if repeat < 1 or repeat > 20 or (threshold is not None and not 0 < threshold <= 1) or not 1 <= timeout <= 7200:
            raise EvalPreflightError("evaluation repeat, threshold, or timeout is out of bounds")
        results = Path(os.getenv("NATURAL_LANGUAGE_EVAL_RESULTS_DIR", ".eval-results/natural-language"))
        return cls(enabled, user_id, results, repeat, threshold, timeout)


@dataclass
class TurnResult:
    index: int
    route: str
    status: str | None
    error_code: str | None
    request_id: str | None
    model_attempt: bool
    tools: list[dict[str, Any]]
    citations_count: int
    elapsed_ms: int
    passed: bool
    failures: list[str] = field(default_factory=list)


@dataclass
class AttemptResult:
    attempt: int
    passed: bool
    turns: list[TurnResult]
    failure_stage: str | None = None


@dataclass
class CaseResult:
    case_id: str
    category: str
    status: str
    pass_rate: float
    threshold: float
    attempts: list[AttemptResult]
    reason: str | None = None


def preflight(settings: Settings, config: EvalConfig) -> dict[str, bool]:
    if not config.enabled:
        raise EvalPreflightError("set NATURAL_LANGUAGE_EVAL_ENABLED=true to authorize paid persistent evaluation")
    if config.user_id is None:
        raise EvalPreflightError("NATURAL_LANGUAGE_EVAL_USER_ID must identify a dedicated evaluation user")
    try:
        model = build_model(settings)
    except Exception:
        raise EvalPreflightError("configured model could not be initialized") from None
    if isinstance(model, (FunctionModel, TestModel)):
        raise EvalPreflightError("fake or programmed models are forbidden")
    if not settings.agent_api_key:
        raise EvalPreflightError("AGENT_API_KEY is required; evaluator will not use a fake model")
    if not settings.zhipu_api_key:
        raise EvalPreflightError("ZHIPU_API_KEY is required for full retrieval and ingestion")
    if settings.notebook_agent_env == "production":
        raise EvalPreflightError("refusing to run persistent evaluation in production")
    try:
        factory = get_session_factory()
        with factory() as db:
            user = db.get(AppUser, config.user_id)
            if user is None or user.disabled_at is not None:
                raise EvalPreflightError("dedicated evaluation user is missing or disabled")
    except EvalPreflightError:
        raise
    except Exception:
        raise EvalPreflightError("evaluation database is unavailable") from None
    if not _migrations_current(factory):
        raise EvalPreflightError("database migration head is not current")
    try:
        readiness = assess_mcp_mutation_readiness(
            settings, session_factory=factory, worker_probe=probe_mcp_worker
        )
    except Exception:
        raise EvalPreflightError("full-stack readiness assessment failed") from None
    if not readiness.ready:
        raise EvalPreflightError(f"full-stack readiness failed: {','.join(readiness.failure_codes)}")
    return dict(readiness.checks)


class LiveEvaluator:
    def __init__(self, catalog: Catalog, settings: Settings, config: EvalConfig) -> None:
        self.catalog, self.settings, self.config = catalog, settings, config
        self.run_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + "-" + secrets.token_hex(4)
        self.grants = McpGrantService(get_session_factory())
        self.grant_id: str | None = None
        self.runtime: LiveMcpSession | None = None
        self.fixture_state: FixtureState | None = None
        self.readiness: dict[str, bool] = {}

    async def __aenter__(self) -> "LiveEvaluator":
        try:
            self.readiness = preflight(self.settings, self.config)
            assert self.config.user_id is not None
            issued = self.grants.issue(
                self.config.user_id, scope="full",
                expires_at=datetime.now(UTC) + timedelta(hours=2),
                label=f"natural-language-eval:{self.run_id}",
                created_by="natural-language-evaluator",
            )
            self.grant_id = issued.grant_id
            self.runtime = LiveMcpSession(issued.raw_token, cwd=ROOT)
            await self.runtime.start()
            tools = await self.runtime.list_tools()
            if set(tools) != set(MCP_TOOL_NAMES) or len(tools) != len(MCP_TOOL_NAMES):
                raise EvalPreflightError("full MCP discovery did not expose exactly ten public tools")
            return self
        except BaseException:
            await self.__aexit__(None, None, None)
            raise

    async def __aexit__(self, *_exc: object) -> None:
        cleanup_failed = False
        runtime, self.runtime = self.runtime, None
        try:
            if runtime is not None:
                # MCP's stdio client owns an AnyIO cancel scope that must be
                # exited by the same asyncio Task that entered it. wait_for()
                # creates a child Task; timeout() preserves task affinity while
                # retaining the same bounded cleanup contract.
                async with asyncio.timeout(_TEARDOWN_TIMEOUT_SECONDS):
                    await runtime.stop()
        except BaseException:
            cleanup_failed = True
        if self.grant_id is not None:
            grant_id, self.grant_id = self.grant_id, None
            try:
                self.grants.revoke(grant_id)
            except BaseException:
                cleanup_failed = True
        if cleanup_failed:
            raise EvalTeardownError("evaluation teardown failed") from None

    async def prepare(self) -> FixtureState:
        assert self.runtime is not None and self.config.user_id is not None
        self.fixture_state = await prepare_fixtures(
            self.runtime, self.catalog, user_id=self.config.user_id,
            timeout_seconds=self.config.ingest_timeout_seconds,
        )
        return self.fixture_state

    async def run(self, cases: Iterable[Case], *, repeat: int, threshold: float | None) -> list[CaseResult]:
        if self.fixture_state is None:
            await self.prepare()
        results: list[CaseResult] = []
        for case in cases:
            missing = set(case.requires) - set(self.fixture_state.capabilities)
            required_rate = _required_threshold(case.threshold, threshold)
            if missing:
                results.append(CaseResult(case.id, case.category, "skip", 0, required_rate, [], f"fixture_unavailable:{','.join(sorted(missing))}"))
                continue
            attempts = [await self._run_attempt(case, index + 1) for index in range(repeat)]
            pass_rate = sum(value.passed for value in attempts) / len(attempts)
            safety_failure = any(
                any("safety-critical tool" in failure for turn in attempt.turns for failure in turn.failures)
                for attempt in attempts
            )
            results.append(CaseResult(case.id, case.category, "pass" if pass_rate >= required_rate and not safety_failure else "fail", pass_rate, required_rate, attempts))
        return results

    async def _run_attempt(self, case: Case, attempt_number: int) -> AttemptResult:
        assert self.runtime is not None and self.fixture_state is not None
        variables = dict(self.fixture_state.variables)
        variables["run_id"] = self.run_id
        conversations: dict[str, str] = {}
        results: list[TurnResult] = []
        for index, turn in enumerate(case.turns, 1):
            try:
                if turn.restart_before:
                    await self.runtime.restart()
                conversation_id = conversations.setdefault(
                    turn.conversation, _conversation_id(self.run_id, case.id, attempt_number, turn.conversation)
                )
                variables["conversation_id"] = conversation_id
                rendered = render_template(turn.input, variables)
                arguments = _render_arguments(turn.arguments, variables)
                tool = turn.tool or "ask_notebook_agent"
                if tool == "ask_notebook_agent":
                    arguments = {"question": rendered, "conversation_id": conversation_id, **arguments}
                started = time.monotonic()
                payload = await self.runtime.call(tool, arguments)
                elapsed = max(0, int((time.monotonic() - started) * 1000))
                for capture in turn.capture:
                    variables[capture.name] = capture_path(payload, capture.path)
                results.append(self._assert_turn(index, turn, payload, elapsed, rendered))
            except BaseException as exc:
                if isinstance(exc, (asyncio.CancelledError, KeyboardInterrupt, SystemExit)):
                    raise
                results.append(TurnResult(index, turn.route, None, None, None, False, [], 0, 0, False, [f"{type(exc).__name__}: bounded execution failure"]))
                return AttemptResult(attempt_number, False, results, "infrastructure_or_fixture")
        return AttemptResult(attempt_number, all(value.passed for value in results), results)

    def _assert_turn(self, index: int, turn: Turn, payload: dict[str, Any], elapsed: int, rendered: str) -> TurnResult:
        assert self.runtime is not None
        request_id = payload.get("request_id") if isinstance(payload.get("request_id"), str) else None
        if turn.route == "model" and request_id:
            traces = self.runtime.diagnostics.traces_for(request_id)
            model_attempt = self.runtime.diagnostics.has_model_attempt(request_id)
        else:
            traces = [ToolTrace(turn.tool or "ask_notebook_agent", 0, "succeeded", "mcp_direct")]
            model_attempt = False
        terminal = [trace.tool_name for trace in traces if trace.boundary == "agent_model" and trace.outcome in {"succeeded", "failed"}]
        observed = [trace.tool_name for trace in traces if trace.boundary == "agent_model"]
        failures = assert_expectation(turn, payload, terminal, observed_tools=observed, model_attempt=model_attempt, rendered_input=rendered)
        return TurnResult(
            index, turn.route,
            payload.get("status") if isinstance(payload.get("status"), str) else None,
            payload.get("error_code") if isinstance(payload.get("error_code"), str) else None,
            request_id, model_attempt, [asdict(trace) for trace in traces],
            len(payload.get("citations", [])) if isinstance(payload.get("citations"), list) else 0,
            elapsed, not failures, failures,
        )


def assert_expectation(
    turn: Turn, payload: dict[str, Any], actual_tools: list[str], *,
    observed_tools: list[str] | None = None, model_attempt: bool, rendered_input: str = "",
) -> list[str]:
    expected, actual = turn.expect, set(actual_tools)
    observed = set(observed_tools if observed_tools is not None else actual_tools)
    failures: list[str] = []
    missing = set(expected.required_tools) - actual
    forbidden = set(expected.forbidden_tools) & observed
    permitted = set(expected.required_tools) | set(expected.allowed_tools)
    unexpected = observed - permitted if permitted or turn.route == "model" else set()
    if missing:
        failures.append(f"missing required tools: {sorted(missing)}")
    if forbidden:
        failures.append(f"forbidden tool observed: {sorted(forbidden)}")
    critical = (forbidden | unexpected) & _SAFETY_CRITICAL
    if critical:
        failures.append(f"safety-critical tool observed: {sorted(critical)}")
    if unexpected:
        failures.append(f"unexpected tools: {sorted(unexpected)}")
    if turn.route == "model" and not model_attempt:
        failures.append("real model_attempt diagnostic missing")
    if turn.route != "model" and model_attempt:
        failures.append("deterministic/setup turn unexpectedly invoked model")
    if expected.statuses and payload.get("status") not in expected.statuses:
        failures.append(f"unexpected status: {payload.get('status')!r}")
    if expected.error_codes and payload.get("error_code") not in expected.error_codes:
        failures.append(f"unexpected error_code: {payload.get('error_code')!r}")
    citations = payload.get("citations", [])
    if expected.citations == "required" and not citations:
        failures.append("citations required")
    if expected.citations == "forbidden" and citations:
        failures.append("citations forbidden")
    if expected.exact_url_scope:
        input_urls = set(re.findall(r"https?://[^\s，。！？]+", rendered_input))
        citation_urls = {row.get("url") for row in citations if isinstance(row, dict) and isinstance(row.get("url"), str)}
        try:
            refs = lambda urls: {(normalize_item_reference(url).platform, normalize_item_reference(url).platform_id) for url in urls}
            if not input_urls or not refs(citation_urls).issubset(refs(input_urls)):
                failures.append("citation escaped exact URL scope")
        except Exception:
            failures.append("citation escaped exact URL scope")
    answer = payload.get("answer", "") if isinstance(payload.get("answer", ""), str) else ""
    if any(value not in answer for value in expected.contains):
        failures.append("required response marker missing")
    if any(value in answer for value in expected.excludes):
        failures.append("forbidden response marker observed")
    return failures


def _required_threshold(catalog_threshold: float, override: float | None) -> float:
    return override if override is not None else catalog_threshold


def write_report(evaluator: LiveEvaluator, results: list[CaseResult], *, output_root: Path | None = None) -> Path:
    target = (output_root or evaluator.config.results_dir) / evaluator.run_id
    payload = {
        "schema_version": "1.0.0", "run_id": evaluator.run_id,
        "catalog_version": evaluator.catalog.version, "created_at": datetime.now(UTC).isoformat(),
        "model": evaluator.settings.agent_model, "provider": _provider_label(evaluator.settings),
        "real_model_required": True, "full_mcp_tools": list(MCP_TOOL_NAMES),
        "readiness": evaluator.readiness,
        "fixture_proof": evaluator.fixture_state.readiness if evaluator.fixture_state else {},
        "summary": _summary(results), "results": [asdict(value) for value in results],
    }
    return _write_report_dir(target, payload)


def write_preflight_failure(
    config: EvalConfig, catalog: Catalog, settings: Settings, *, error_code: str,
    failure_stage: str = "preflight",
) -> Path:
    run_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + "-preflight-" + secrets.token_hex(3)
    target = config.results_dir / run_id
    payload = {
        "schema_version": "1.0.0", "run_id": run_id, "catalog_version": catalog.version,
        "created_at": datetime.now(UTC).isoformat(), "model": settings.agent_model,
        "provider": _provider_label(settings), "real_model_required": True,
        "failure_stage": failure_stage if failure_stage in {"preflight", "fixture", "infrastructure"} else "infrastructure",
        "error_code": error_code,
        "summary": {"counts": {"pass": 0, "fail": 0, "skip": 0}, "categories": {}, "observed_tools": {}, "direct_mcp_tools": {}},
        "results": [],
    }
    return _write_report_dir(target, payload)


def _write_report_dir(target: Path, payload: dict[str, Any]) -> Path:
    target.mkdir(parents=True, exist_ok=False)
    safe = redact_report(payload)
    (target / "report.json").write_text(json.dumps(safe, ensure_ascii=False, indent=2), encoding="utf-8")
    (target / "report.md").write_text(_markdown(safe), encoding="utf-8")
    return target


def redact_report(value: Any, secrets_to_remove: Iterable[str] = ()) -> Any:
    blocked = re.compile(r"(token|api.?key|secret|tenant|storage.?key|raw_object|answer|question)", re.I)
    secrets_list = [item for item in secrets_to_remove if item]
    if isinstance(value, dict):
        return {key: redact_report(item, secrets_list) for key, item in value.items() if not blocked.search(str(key))}
    if isinstance(value, list):
        return [redact_report(item, secrets_list) for item in value]
    if isinstance(value, str):
        for secret in secrets_list:
            value = value.replace(secret, "[REDACTED]")
        return value[:1000]
    return value


def _summary(results: list[CaseResult]) -> dict[str, Any]:
    counts = {status: sum(value.status == status for value in results) for status in ("pass", "fail", "skip")}
    categories: dict[str, dict[str, int]] = {}
    model_tools: dict[str, int] = {}
    direct_tools: dict[str, int] = {}
    for result in results:
        categories.setdefault(result.category, {"pass": 0, "fail": 0, "skip": 0})[result.status] += 1
        for attempt in result.attempts:
            for turn in attempt.turns:
                for trace in turn.tools:
                    if trace.get("outcome") == "skipped":
                        continue
                    target = direct_tools if trace.get("boundary") == "mcp_direct" else model_tools
                    name = trace.get("tool_name")
                    if isinstance(name, str):
                        target[name] = target.get(name, 0) + 1
    return {"counts": counts, "categories": categories, "observed_tools": model_tools, "direct_mcp_tools": direct_tools}


def _markdown(payload: dict[str, Any]) -> str:
    summary = payload["summary"]["counts"]
    lines = [
        "# Natural-language live evaluation", "", f"- Run: `{payload['run_id']}`",
        f"- Catalog: `{payload['catalog_version']}`", f"- Model: `{payload['model']}` ({payload['provider']})",
        f"- Results: {summary['pass']} pass / {summary['fail']} fail / {summary['skip']} skip", "",
        "| Case | Category | Result | Pass rate |", "| --- | --- | --- | --- |",
    ]
    for result in payload["results"]:
        lines.append(f"| `{result['case_id']}` | {result['category']} | {result['status']} | {result['pass_rate']:.0%} |")
    lines.extend(["", "Reports omit prompts, answers, identities, tokens, tool arguments, and tool results.", ""])
    return "\n".join(lines)


def _provider_label(settings: Settings) -> str:
    if settings.agent_base_url:
        return "openai-compatible"
    return settings.agent_model.split(":", 1)[0] if ":" in settings.agent_model else "configured"


def _conversation_id(run_id: str, case_id: str, attempt: int, label: str) -> str:
    return re.sub(r"[^A-Za-z0-9._:-]", "-", f"nleval:{run_id}:{case_id}:{attempt}:{label}")[:128]


def _render_arguments(arguments: dict[str, Any], variables: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in arguments.items():
        if isinstance(value, str):
            rendered = render_template(value, variables)
            result[key] = int(rendered) if rendered.isdigit() and key.endswith("_id") else rendered
        elif isinstance(value, list):
            items = [render_template(item, variables) if isinstance(item, str) else item for item in value]
            result[key] = [int(item) if isinstance(item, str) and item.isdigit() and key.endswith("_ids") else item for item in items]
        else:
            result[key] = value
    return result


def _migrations_current(factory) -> bool:
    try:
        config = AlembicConfig(str(ROOT / "alembic.ini"))
        config.set_main_option("script_location", str(ROOT / "migrations"))
        expected = set(ScriptDirectory.from_config(config).get_heads())
        with factory() as db:
            current = {str(row[0]) for row in db.execute(text("SELECT version_num FROM alembic_version"))}
        return bool(expected) and current == expected
    except Exception:
        return False
