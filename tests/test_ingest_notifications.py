import json
import logging
from pathlib import Path
from datetime import UTC, datetime
from types import SimpleNamespace
from urllib.error import HTTPError

import pytest
from sqlalchemy.dialects import postgresql

from app.config import Settings
from app.ingest.notifications import (
    DeliveryClaim,
    HANDLER_KEY,
    IngestNotificationPoller,
    LangBotOutboundClient,
    NotificationTransportError,
    _notification_diagnostic,
    render_completion_notification,
)
from app.ingest.tasks import celery_app, _notification_interval_from_env


@pytest.mark.parametrize(
    ("outcome", "item_state", "needle"),
    [
        ("completed", "ready", "加入知识库，可以开始提问"),
        ("completed", "needs_extension", "浏览器扩展补充文本"),
        ("completed", "needs_asr", "语音识别"),
        ("failed", "failed", "解析失败"),
    ],
)
def test_notification_renderer_has_four_fixed_snapshots(
    outcome, item_state, needle
):
    message = render_completion_notification(
        outcome,
        item_state,
        "  私密\n标题\x00 " + ("x" * 200),
    )
    assert needle in message
    assert "\x00" not in message
    assert len(message) <= 320


def test_notification_schedule_is_maintenance_and_has_one_owner():
    schedule = celery_app.conf.beat_schedule
    assert schedule["deliver-pending-ingest-notifications"]["task"] == (
        "app.ingest.tasks.deliver_pending_ingest_notifications_task"
    )
    assert schedule["deliver-pending-ingest-notifications"]["options"] == {
        "queue": "maintenance"
    }
    assert "publish-pending-ingest-completion-events" not in schedule
    assert celery_app.conf.task_routes[
        "app.ingest.tasks.deliver_pending_ingest_notifications_task"
    ]["queue"] == "maintenance"


@pytest.mark.parametrize("value", ["0", "-1", "not-an-int"])
def test_notification_interval_fails_closed(monkeypatch, value):
    monkeypatch.setenv("INGEST_NOTIFICATION_INTERVAL_SECONDS", value)
    with pytest.raises(ValueError, match="positive integer"):
        _notification_interval_from_env()


class _Response:
    status = 202

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def getcode(self):
        return self.status


class _Opener:
    def __init__(self, response=None):
        self.response = response or _Response()
        self.request = None
        self.timeout = None

    def open(self, request, timeout):
        self.request = request
        self.timeout = timeout
        if isinstance(self.response, BaseException):
            raise self.response
        return self.response


def test_langbot_client_uses_exact_private_target_and_plain_chain():
    opener = _Opener()
    client = LangBotOutboundClient(
        "http://127.0.0.1:5300",
        "dedicated-key",
        timeout_seconds=3,
        opener=opener,
    )
    client.send_message(
        bot_uuid="bot/uuid",
        conversation_id="-100/topic-7",
        text="已完成",
        timeout_seconds=0.25,
    )
    assert opener.request.full_url.endswith(
        "/api/v1/platform/bots/bot%2Fuuid/send_message"
    )
    assert opener.request.get_header("X-api-key") == "dedicated-key"
    assert opener.timeout == 0.25
    assert json.loads(opener.request.data) == {
        "target_type": "person",
        "target_id": "-100/topic-7",
        "message_chain": {"root": [{"type": "Plain", "text": "已完成"}]},
    }


@pytest.mark.parametrize(
    ("status", "error_code", "retryable"),
    [
        (400, "outbound_contract_invalid", False),
        (401, "outbound_auth_rejected", False),
        (404, "outbound_target_not_found", False),
        (429, "outbound_rate_limited", True),
        (503, "outbound_server_error", True),
    ],
)
def test_langbot_client_classifies_status_without_response_body(
    status, error_code, retryable
):
    opener = _Opener(HTTPError("http://127.0.0.1", status, "private body", {}, None))
    client = LangBotOutboundClient(
        "http://127.0.0.1:5300", "key", opener=opener
    )
    with pytest.raises(NotificationTransportError) as caught:
        client.send_message(bot_uuid="bot", conversation_id="chat", text="x")
    assert caught.value.error_code == error_code
    assert caught.value.retryable is retryable
    assert "private body" not in repr(caught.value)


def test_langbot_client_rejects_redirect_without_following_it():
    opener = _Opener(_Response())
    opener.response.status = 302
    client = LangBotOutboundClient(
        "http://127.0.0.1:5300", "key", opener=opener
    )

    with pytest.raises(NotificationTransportError) as caught:
        client.send_message(bot_uuid="bot", conversation_id="chat", text="x")

    assert caught.value.error_code == "redirect_rejected"
    assert caught.value.retryable is False


class _ClaimSession:
    bind = None

    def __init__(self):
        self.statement = None

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def scalar(self, _statement):
        return datetime(2026, 8, 9, tzinfo=UTC)

    def scalars(self, statement):
        self.statement = statement
        return ()

    def commit(self):
        return None


class _FailureSession(_ClaimSession):
    def __init__(self, row):
        super().__init__()
        self.row = row
        self.scalar_calls = 0

    def scalar(self, _statement):
        self.scalar_calls += 1
        if self.scalar_calls == 1:
            return datetime(2026, 8, 9, tzinfo=UTC)
        return self.row


def test_claim_query_locks_only_event_side_of_outer_join():
    session = _ClaimSession()
    settings = SimpleNamespace(
        ingest_notification_claim_timeout_seconds=300,
        ingest_notification_batch_size=20,
    )
    poller = IngestNotificationPoller(lambda: session)

    assert poller._claim_batch(
        now=datetime(2026, 8, 9, tzinfo=UTC),
        settings=settings,
    ) == []

    sql = str(
        session.statement.compile(
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    )
    assert "LEFT OUTER JOIN ingest_completion_delivery" in sql
    assert "FOR UPDATE OF ingest_completion_event SKIP LOCKED" in sql


def test_ack_failure_syncs_retry_exhausted_error_code():
    row = SimpleNamespace(
        status="claimed",
        disposition=None,
        claim_token="token",
        claimed_at=datetime(2026, 8, 9, tzinfo=UTC),
        attempts=2,
        next_attempt_at=None,
        last_error_code=None,
        completed_at=None,
        updated_at=None,
    )
    session = _FailureSession(row)
    settings = SimpleNamespace(
        ingest_notification_max_attempts=2,
        ingest_notification_retry_base_seconds=5,
        ingest_notification_retry_max_seconds=30,
    )
    poller = IngestNotificationPoller(lambda: session)

    acknowledged, exhausted = poller._ack_failure(
        DeliveryClaim(11, 22, "token", 2),
        error_code="outbound_server_error",
        settings=settings,
        now=datetime(2026, 8, 9, tzinfo=UTC),
    )

    assert (acknowledged, exhausted) == (True, True)
    assert row.status == "failed"
    assert row.disposition == "retry_exhausted"
    assert row.last_error_code == "retry_exhausted"
    assert row.next_attempt_at is None


class _ReleaseSession(_ClaimSession):
    def __init__(self, row):
        super().__init__()
        self.row = row

    def scalars(self, statement):
        self.statement = statement
        return (self.row,)


def test_deferred_claim_is_token_fenced_and_immediately_eligible():
    row = SimpleNamespace(
        status="claimed",
        disposition=None,
        claim_token="token",
        claimed_at=datetime(2026, 8, 9, tzinfo=UTC),
        next_attempt_at=None,
        last_error_code=None,
        completed_at=None,
        updated_at=None,
    )
    session = _ReleaseSession(row)
    poller = IngestNotificationPoller(lambda: session)

    released = poller._release_deferred_claims(
        (DeliveryClaim(11, 22, "token", 1),)
    )

    assert released == 1
    assert row.status == "failed"
    assert row.claim_token is None
    assert row.claimed_at is None
    assert row.last_error_code == "notification_deferred"
    assert row.next_attempt_at == datetime(2026, 8, 9, tzinfo=UTC)


@pytest.mark.parametrize("disposition", ["terminal_failure", "retry_exhausted"])
def test_terminal_disposition_cannot_be_acknowledged_as_succeeded(disposition):
    poller = IngestNotificationPoller(
        lambda: (_ for _ in ()).throw(AssertionError("must not open session"))
    )

    with pytest.raises(ValueError, match="invalid_notification_success_disposition"):
        poller._ack_succeeded(
            DeliveryClaim(11, 22, "token", 1),
            disposition=disposition,
            now=datetime(2026, 8, 9, tzinfo=UTC),
        )


class _DeferredReclaimSession(_ClaimSession):
    def __init__(self, event, row):
        super().__init__()
        self.event = event
        self.row = row
        self.scalar_calls = 0

    def scalar(self, _statement):
        self.statement = _statement
        self.scalar_calls += 1
        if self.scalar_calls == 1:
            return datetime(2026, 8, 9, tzinfo=UTC)
        return self.row

    def scalars(self, statement):
        self.statement = statement
        return (self.event,)


def test_deadline_deferred_reclaim_does_not_consume_retry_attempt():
    event = SimpleNamespace(id=11)
    row = SimpleNamespace(
        id=22,
        status="failed",
        disposition=None,
        claim_token=None,
        claimed_at=None,
        attempts=3,
        next_attempt_at=datetime(2026, 8, 9, tzinfo=UTC),
        last_error_code="notification_deferred",
        completed_at=None,
        updated_at=None,
    )
    session = _DeferredReclaimSession(event, row)
    settings = SimpleNamespace(
        ingest_notification_claim_timeout_seconds=300,
        ingest_notification_batch_size=20,
        ingest_notification_max_attempts=5,
    )
    poller = IngestNotificationPoller(
        lambda: session,
        token_factory=lambda: "next-token",
    )

    claims = poller._claim_batch(
        now=datetime(2026, 8, 9, tzinfo=UTC),
        settings=settings,
    )

    assert claims == [DeliveryClaim(11, 22, "next-token", 3)]
    assert row.status == "claimed"
    assert row.attempts == 3


class _BacklogSession(_ClaimSession):
    def __init__(self, oldest):
        super().__init__()
        self.oldest = oldest
        self.scalar_calls = 0

    def scalar(self, _statement):
        self.statement = _statement
        self.scalar_calls += 1
        if self.scalar_calls == 1:
            return datetime(2026, 8, 9, 0, 0, tzinfo=UTC)
        return self.oldest


def test_oldest_eligible_backlog_age_uses_bounded_read_only_query():
    session = _BacklogSession(datetime(2026, 8, 8, 23, 59, 18, tzinfo=UTC))
    settings = SimpleNamespace(ingest_notification_claim_timeout_seconds=300)
    poller = IngestNotificationPoller(lambda: session)

    age = poller._oldest_eligible_backlog_age_seconds(
        settings=settings,
        budget_seconds=0.137,
    )

    assert age == 42
    sql = str(
        session.statement.compile(
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    )
    assert "LEFT OUTER JOIN ingest_completion_delivery" in sql
    assert "FOR UPDATE" not in sql


def test_oldest_eligible_backlog_age_reserves_zero_for_empty_queue():
    settings = SimpleNamespace(ingest_notification_claim_timeout_seconds=300)
    empty = IngestNotificationPoller(lambda: _BacklogSession(None))
    just_created = IngestNotificationPoller(
        lambda: _BacklogSession(datetime(2026, 8, 9, 0, 0, tzinfo=UTC))
    )

    assert empty._oldest_eligible_backlog_age_seconds(
        settings=settings,
        budget_seconds=0.1,
    ) == 0
    assert just_created._oldest_eligible_backlog_age_seconds(
        settings=settings,
        budget_seconds=0.1,
    ) == 1


def test_successful_tick_heartbeat_is_numeric_and_observation_failure_is_isolated(
    monkeypatch,
):
    diagnostics = []
    observation_budgets = []
    monkeypatch.setattr(
        "app.ingest.notifications._notification_diagnostic",
        lambda event, **values: diagnostics.append((event, values)),
    )

    class _HeartbeatPoller(IngestNotificationPoller):
        def _claim_batch(self, **_kwargs):
            return []

        def _oldest_eligible_backlog_age_seconds(self, **_kwargs):
            observation_budgets.append(_kwargs["budget_seconds"])
            return 42

    settings = SimpleNamespace(ingest_notification_max_duration_seconds=8.0)
    poller = _HeartbeatPoller(
        lambda: None,
        _NeverSendClient(),
        settings=settings,
        clock=lambda: 0.0,
    )
    result = poller.sweep_once()

    assert result.claimed == 0
    heartbeat = [
        entry
        for entry in diagnostics
        if entry[0] == "notification_poller_heartbeat"
    ]
    assert heartbeat == [
        (
            "notification_poller_heartbeat",
            {
                "heartbeat": 1,
                "claimed": 0,
                "succeeded": 0,
                "skipped": 0,
                "failed": 0,
                "deferred": 0,
                "duration_ms": 0,
                "observability_failed": 0,
                "oldest_eligible_backlog_age_seconds": 42,
            },
        )
    ]
    assert observation_budgets == [0.25]

    diagnostics.clear()

    class _BrokenObservationPoller(_HeartbeatPoller):
        def _oldest_eligible_backlog_age_seconds(self, **_kwargs):
            raise RuntimeError("private observation failure")

    broken = _BrokenObservationPoller(
        lambda: None,
        _NeverSendClient(),
        settings=settings,
        clock=lambda: 0.0,
    )
    assert broken.sweep_once().claimed == 0
    broken_heartbeat = [
        entry
        for entry in diagnostics
        if entry[0] == "notification_poller_heartbeat"
    ]
    assert broken_heartbeat[0][1]["observability_failed"] == 1
    assert "private observation failure" not in repr(diagnostics)


def test_notification_diagnostic_enforces_numeric_privacy_allowlist(caplog):
    sentinel = "private-title-and-target"
    with caplog.at_level(logging.INFO, logger="notebook_agent.runtime"):
        _notification_diagnostic(
            "untrusted-event-name",
            heartbeat=1,
            claimed=4,
            observability_failed=True,
            oldest_eligible_backlog_age_seconds=2**40,
            title=sentinel,
            bot_uuid=sentinel,
        )

    payload = caplog.records[-1].diagnostic_payload
    assert payload == {
        "event": "notification_sweep",
        "heartbeat": 1,
        "claimed": 4,
        "oldest_eligible_backlog_age_seconds": 2_147_483_647,
    }
    assert sentinel not in repr(payload)


def test_operator_docs_do_not_restore_retired_completion_consumer():
    root = Path(__file__).parents[1]
    readme_zh = (root / "README.zh-CN.md").read_text(encoding="utf-8")
    deployment = (root / "docs/deployment/README.md").read_text(encoding="utf-8")
    environment = (root / "docs/getting-started/configuration.md").read_text(
        encoding="utf-8"
    )

    combined = "\n".join((readme_zh, deployment, environment))
    assert "future idempotent consumer only" not in combined
    assert "真实 consumer 部署前" not in combined
    assert "Completion publisher/consumer" not in combined
    assert "notification_poller_heartbeat" in combined
    assert "delivery ledger" in combined
    assert "`ingest-completion` queue 已退役" in combined


class _NeverSendClient:
    def send_message(self, **_kwargs):
        raise AssertionError("deadline-expired claim must not be sent")


def test_sweep_releases_all_unsent_claims_before_stale_timeout():
    clock_value = [0.0]
    claims = [
        DeliveryClaim(11, 21, "one", 1),
        DeliveryClaim(12, 22, "two", 1),
    ]

    class _DeadlinePoller(IngestNotificationPoller):
        def _claim_batch(self, **_kwargs):
            clock_value[0] = 7.5
            return claims

        def _release_deferred_claims(self, pending, **_kwargs):
            self.released = list(pending)
            return len(self.released)

    settings = SimpleNamespace(ingest_notification_max_duration_seconds=8.0)
    poller = _DeadlinePoller(
        lambda: None,
        _NeverSendClient(),
        settings=settings,
        clock=lambda: clock_value[0],
    )

    result = poller.sweep_once()

    assert result.claimed == 2
    assert result.deferred == 2
    assert poller.released == claims


def test_langbot_non_loopback_http_is_rejected():
    with pytest.raises(ValueError, match="HTTPS"):
        LangBotOutboundClient("http://langbot.internal:5300", "key")
