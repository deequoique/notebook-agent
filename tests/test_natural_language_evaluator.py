from __future__ import annotations

from asyncio import CancelledError
from dataclasses import replace
import tempfile
from types import SimpleNamespace

import pytest

from app.config import Settings
from evals.natural_language.mcp_runtime import DiagnosticCollector
from evals.natural_language.runner import (
    AttemptResult,
    CaseResult,
    EvalConfig,
    EvalPreflightError,
    EvalTeardownError,
    LiveEvaluator,
    _required_threshold,
    _summary,
    assert_expectation,
    preflight,
    redact_report,
    write_preflight_failure,
)
from evals.natural_language.schema import (
    Catalog,
    CatalogError,
    Turn,
    capture_path,
    load_catalog,
    render_template,
)


def test_live_catalog_is_valid_and_has_real_model_smoke_coverage():
    catalog = load_catalog()
    assert len(catalog.cases) >= 15
    assert len({case.id for case in catalog.cases}) == len(catalog.cases)
    assert {case.category for case in catalog.cases} >= {
        "retrieval", "save", "inventory", "context", "conversation", "safety"
    }
    smoke = [case for case in catalog.cases if case.smoke]
    assert smoke
    assert all(any(turn.route == "model" for turn in case.turns) for case in smoke)
    assert all(
        turn.expect.statuses
        for case in catalog.cases
        for turn in case.turns
        if turn.route == "model"
    )
    required_tools = {
        tool for case in catalog.cases for turn in case.turns
        for tool in turn.expect.required_tools
    }
    assert required_tools >= {
        "search_segments", "get_neighbors", "save_videos",
        "confirm_video_save", "cancel_video_save", "clarify_save_confirmation",
        "list_saved_items", "get_saved_item", "update_saved_item",
        "delete_saved_items", "confirm_item_deletion", "cancel_item_deletion",
        "restore_saved_items", "retry_item_ingestion",
    }


def test_live_configuration_is_explicit_and_bounded(monkeypatch, tmp_path):
    monkeypatch.delenv("NATURAL_LANGUAGE_EVAL_ENABLED", raising=False)
    monkeypatch.delenv("NATURAL_LANGUAGE_EVAL_USER_ID", raising=False)
    config = EvalConfig.from_environment()
    assert not config.enabled and config.user_id is None
    monkeypatch.setenv("NATURAL_LANGUAGE_EVAL_REPEAT", "0")
    with pytest.raises(EvalPreflightError):
        EvalConfig.from_environment()

    safe_config = EvalConfig(False, None, tmp_path, 1, 1.0, 30)
    target = write_preflight_failure(
        safe_config, load_catalog(), Settings(), error_code="preflight_unavailable"
    )
    report = (target / "report.json").read_text(encoding="utf-8")
    assert '"failure_stage": "preflight"' in report
    assert "api_key" not in report and "tenant" not in report


def test_preflight_bounds_database_exception_text(monkeypatch, tmp_path):
    class BrokenFactory:
        def __call__(self):
            raise RuntimeError("password=must-not-escape")

    settings = replace(
        Settings(),
        notebook_agent_env="development",
        agent_api_key="configured",
        zhipu_api_key="configured",
    )
    monkeypatch.setattr(
        "evals.natural_language.runner.get_session_factory", lambda: BrokenFactory()
    )
    with pytest.raises(EvalPreflightError) as captured:
        preflight(settings, EvalConfig(True, 1, tmp_path, 1, None, 30))
    assert str(captured.value) == "evaluation database is unavailable"


def test_preflight_refuses_production_before_opening_database(monkeypatch, tmp_path):
    settings = replace(
        Settings(),
        notebook_agent_env="production",
        notebook_agent_log_retrieval_content=False,
        agent_api_key="configured",
        zhipu_api_key="configured",
    )
    monkeypatch.setattr(
        "evals.natural_language.runner.get_session_factory",
        lambda: pytest.fail("production preflight must not open the database"),
    )
    with pytest.raises(EvalPreflightError, match="refusing.*production"):
        preflight(settings, EvalConfig(True, 1, tmp_path, 1, None, 30))


def test_template_resolution_and_typed_capture_fail_closed():
    assert render_template("hello {topic}", {"topic": "world"}) == "hello world"
    assert capture_path({"results": [{"item_id": 7}]}, "results.0.item_id") == 7
    with pytest.raises(CatalogError):
        render_template("{item.id}", {"item": object()})
    with pytest.raises(CatalogError):
        render_template("{missing}", {})
    with pytest.raises(CatalogError):
        capture_path({"results": []}, "results.0.item_id")


def test_catalog_rejects_capture_overwriting_a_trusted_fixture():
    raw = load_catalog().model_dump()
    raw["cases"][0]["turns"][0]["capture"] = [
        {"name": "baseline_url", "path": "request_id"}
    ]
    with pytest.raises(ValueError, match="reserved captures"):
        Catalog.model_validate(raw)


def test_catalog_rejects_duplicate_capture_names_in_one_turn():
    raw = load_catalog().model_dump()
    raw["cases"][0]["turns"][0]["capture"] = [
        {"name": "captured_id", "path": "request_id"},
        {"name": "captured_id", "path": "thread_id"},
    ]
    with pytest.raises(ValueError, match="capture names may not be reused"):
        Catalog.model_validate(raw)


def test_diagnostic_collector_correlates_only_safe_fields():
    collector = DiagnosticCollector()
    request_id = "a" * 32
    collector.write(f'{{"event":"knowledge_request","stage":"model_attempt","request_id":"{request_id}","tenant_id":99}}\n')
    collector.write(f'{{"event":"knowledge_request","stage":"tool_call","request_id":"{request_id}","tool_name":"search_segments","call_index":1,"tool_outcome":"started","question":"private"}}\n')
    collector.write(f'{{"event":"knowledge_request","stage":"tool_call","request_id":"{request_id}","tool_name":"search_segments","call_index":1,"tool_outcome":"succeeded","tool_result":"private"}}\n')
    collector.write('not json\n')
    assert collector.has_model_attempt(request_id)
    assert [trace.tool_name for trace in collector.traces_for(request_id)] == ["search_segments"]
    assert collector.traces_for(request_id)[0].outcome == "succeeded"
    assert collector.malformed_count == 1
    assert all("tenant_id" not in event and "question" not in event for event in collector.events_for(request_id))


@pytest.mark.asyncio
async def test_diagnostic_collector_waits_for_terminal_stderr_event():
    collector = DiagnosticCollector()
    request_id = "b" * 32

    async def deliver():
        import asyncio
        await asyncio.sleep(0.01)
        collector.write(
            f'{{"event":"knowledge_request","stage":"gateway_response_ready",'
            f'"request_id":"{request_id}"}}\n'
        )

    import asyncio
    task = asyncio.create_task(deliver())
    await collector.wait_for_response_diagnostics(request_id, timeout_seconds=0.2)
    await task
    assert any(
        event["stage"] == "gateway_response_ready"
        for event in collector.events_for(request_id)
    )


def test_scoring_requires_model_attempt_and_enforces_tool_sets():
    turn = Turn.model_validate({
        "input": "查一下",
        "expect": {
            "required_tools": ["search_segments"],
            "allowed_tools": ["search_segments", "get_neighbors"],
            "forbidden_tools": ["save_videos"],
            "statuses": ["ok"],
            "citations": "required",
        },
    })
    payload = {"status": "ok", "citations": [{"segment_id": 1}]}
    assert not assert_expectation(turn, payload, ["search_segments"], model_attempt=True)
    failures = assert_expectation(turn, payload, ["save_videos"], model_attempt=False)
    assert any("missing required" in failure for failure in failures)
    assert any("forbidden tool" in failure for failure in failures)
    assert any("model_attempt" in failure for failure in failures)

    skipped_forbidden = assert_expectation(
        turn,
        payload,
        ["search_segments"],
        observed_tools=["search_segments", "save_videos"],
        model_attempt=True,
    )
    assert any("forbidden tool" in failure for failure in skipped_forbidden)
    assert any("safety-critical tool" in failure for failure in skipped_forbidden)

    unexpected_critical = assert_expectation(
        turn,
        payload,
        ["search_segments", "delete_saved_items"],
        observed_tools=["search_segments", "delete_saved_items"],
        model_attempt=True,
    )
    assert any("safety-critical tool" in failure for failure in unexpected_critical)

    incomplete = assert_expectation(
        turn,
        payload,
        [],
        observed_tools=["search_segments"],
        model_attempt=True,
    )
    assert any("missing required" in failure for failure in incomplete)


def test_global_threshold_overrides_catalog_threshold():
    assert _required_threshold(0.8, None) == 0.8
    assert _required_threshold(1.0, 0.5) == 0.5


@pytest.mark.asyncio
async def test_evaluator_revokes_its_grant_when_startup_is_cancelled(monkeypatch, tmp_path):
    revoked: list[str] = []

    class Grants:
        def issue(self, *_args, **_kwargs):
            return type("Issued", (), {"grant_id": "grant-1", "raw_token": "raw"})()

        def revoke(self, grant_id):
            revoked.append(grant_id)

    class Runtime:
        async def start(self):
            raise CancelledError()

        async def stop(self):
            return None

    evaluator = LiveEvaluator(
        load_catalog(), Settings(), EvalConfig(True, 1, tmp_path, 1, None, 30)
    )
    evaluator.grants = Grants()
    monkeypatch.setattr(
        "evals.natural_language.runner.preflight", lambda *_args: {"database": True}
    )
    monkeypatch.setattr(
        "evals.natural_language.runner.LiveMcpSession",
        lambda *_args, **_kwargs: Runtime(),
    )

    with pytest.raises(CancelledError):
        await evaluator.__aenter__()
    assert revoked == ["grant-1"]


@pytest.mark.asyncio
async def test_mcp_stop_drains_shutdown_stderr_before_closing_tempfile(tmp_path):
    from evals.natural_language.mcp_runtime import LiveMcpSession

    request_id = "c" * 32
    errlog = tempfile.TemporaryFile(mode="w+", encoding="utf-8")

    class Stack:
        async def aclose(self):
            errlog.write(
                f'{{"event":"knowledge_request","stage":"gateway_response_ready",'
                f'"request_id":"{request_id}"}}\n'
            )
            errlog.flush()

    runtime = LiveMcpSession("raw", cwd=tmp_path)
    runtime._stack = Stack()
    runtime._errlog = errlog
    runtime.client = object()
    await runtime.stop()

    assert errlog.closed
    assert runtime._errlog is None and runtime._stack is None
    assert any(
        event["stage"] == "gateway_response_ready"
        for event in runtime.diagnostics.events_for(request_id)
    )


@pytest.mark.asyncio
async def test_evaluator_teardown_attempts_revoke_and_bounds_both_failures(tmp_path):
    revoked: list[str] = []

    class Runtime:
        async def stop(self):
            raise RuntimeError("stderr tempfile private path")

    class Grants:
        def revoke(self, grant_id):
            revoked.append(grant_id)
            raise RuntimeError("private database detail")

    evaluator = LiveEvaluator(
        load_catalog(), Settings(), EvalConfig(True, 1, tmp_path, 1, None, 30)
    )
    evaluator.runtime = Runtime()
    evaluator.grants = Grants()
    evaluator.grant_id = "grant-1"

    with pytest.raises(EvalTeardownError) as captured:
        await evaluator.__aexit__(None, None, None)
    assert str(captured.value) == "evaluation teardown failed"
    assert revoked == ["grant-1"]
    assert evaluator.runtime is None and evaluator.grant_id is None


@pytest.mark.asyncio
async def test_evaluator_teardown_timeout_still_attempts_grant_revoke(
    monkeypatch, tmp_path
):
    import asyncio

    revoked: list[str] = []

    class Runtime:
        async def stop(self):
            await asyncio.Event().wait()

    class Grants:
        def revoke(self, grant_id):
            revoked.append(grant_id)

    evaluator = LiveEvaluator(
        load_catalog(), Settings(), EvalConfig(True, 1, tmp_path, 1, None, 30)
    )
    evaluator.runtime = Runtime()
    evaluator.grants = Grants()
    evaluator.grant_id = "grant-1"
    monkeypatch.setattr(
        "evals.natural_language.runner._TEARDOWN_TIMEOUT_SECONDS", 0.01
    )

    with pytest.raises(EvalTeardownError):
        await evaluator.__aexit__(None, None, None)
    assert revoked == ["grant-1"]


@pytest.mark.asyncio
async def test_evaluator_teardown_stops_runtime_in_the_entering_task(tmp_path):
    import asyncio

    caller = asyncio.current_task()
    revoked: list[str] = []

    class Runtime:
        async def stop(self):
            assert asyncio.current_task() is caller

    class Grants:
        def revoke(self, grant_id):
            revoked.append(grant_id)

    evaluator = LiveEvaluator(
        load_catalog(), Settings(), EvalConfig(True, 1, tmp_path, 1, None, 30)
    )
    evaluator.runtime = Runtime()
    evaluator.grants = Grants()
    evaluator.grant_id = "grant-1"

    await evaluator.__aexit__(None, None, None)

    assert revoked == ["grant-1"]
    assert evaluator.runtime is None and evaluator.grant_id is None


@pytest.mark.asyncio
async def test_attempt_does_not_swallow_async_cancellation(tmp_path):
    from evals.natural_language.fixtures import FixtureState

    class Runtime:
        async def call(self, *_args, **_kwargs):
            raise CancelledError()

    evaluator = LiveEvaluator(
        load_catalog(), Settings(), EvalConfig(True, 1, tmp_path, 1, None, 30)
    )
    evaluator.runtime = Runtime()
    evaluator.fixture_state = FixtureState(
        {"baseline_topic": "topic"}, frozenset({"ready_item"}), {}
    )

    with pytest.raises(CancelledError):
        await evaluator._run_attempt(load_catalog().cases[0], 1)


@pytest.mark.asyncio
async def test_cli_writes_success_report_only_after_teardown(monkeypatch, tmp_path):
    import evals.natural_language.__main__ as cli

    state = {"closed": False, "reported": False}
    config = EvalConfig(True, 1, tmp_path, 1, None, 30)

    class Evaluator:
        def __init__(self, catalog, settings, received_config):
            assert received_config == config
            self.settings = settings

        async def __aenter__(self):
            return self

        async def prepare(self):
            return object()

        async def run(self, cases, *, repeat, threshold):
            assert cases and repeat == 1 and threshold is None
            return []

        async def __aexit__(self, *_args):
            state["closed"] = True

    def report(evaluator, results):
        assert state["closed"] and results == []
        state["reported"] = True
        return tmp_path / "report"

    monkeypatch.setattr(cli.EvalConfig, "from_environment", lambda: config)
    monkeypatch.setattr(cli, "LiveEvaluator", Evaluator)
    monkeypatch.setattr(cli, "write_report", report)
    args = SimpleNamespace(
        repeat=None, threshold=None, results_dir=None, preflight=False,
        prepare_fixtures=False, all=False, smoke=True, case_ids=None,
        categories=None,
    )

    assert await cli._live(args, load_catalog()) == 0
    assert state == {"closed": True, "reported": True}


def test_summary_separates_direct_mcp_from_model_tools():
    turn = {
        "index": 1, "route": "setup", "status": "ok", "error_code": None,
        "request_id": None, "model_attempt": False, "citations_count": 0,
        "elapsed_ms": 1, "passed": True, "failures": [],
        "tools": [{
            "tool_name": "restore_saved_items", "call_index": 0,
            "outcome": "succeeded", "boundary": "mcp_direct",
        }],
    }
    from evals.natural_language.runner import TurnResult
    result = CaseResult(
        "inventory.restore", "inventory", "pass", 1.0, 1.0,
        [AttemptResult(1, True, [TurnResult(**turn)])],
    )
    summary = _summary([result])
    assert summary["observed_tools"] == {}
    assert summary["direct_mcp_tools"] == {"restore_saved_items": 1}


def test_report_redaction_removes_sensitive_fields_and_values():
    redacted = redact_report(
        {
            "api_key": "key",
            "tenant_id": 7,
            "answer": "private answer",
            "safe": "prefix raw-secret suffix",
            "nested": {"storage_key": "bucket/private", "status": "ok"},
        },
        ["raw-secret"],
    )
    assert redacted == {"safe": "prefix [REDACTED] suffix", "nested": {"status": "ok"}}


def test_write_report_creates_run_directory_once(tmp_path):
    from evals.natural_language.runner import write_report

    evaluator = LiveEvaluator(
        load_catalog(), Settings(), EvalConfig(True, 1, tmp_path, 1, None, 30)
    )
    evaluator.readiness = {"database": True}

    target = write_report(evaluator, [])

    assert target == tmp_path / evaluator.run_id
    assert (target / "report.json").is_file()
    assert (target / "report.md").is_file()
