from __future__ import annotations

import pytest

from app.agent.autonomy import (
    ErrorEnvelope,
    RecoveryLedger,
    RecoveryPolicy,
    TodoValidationError,
    TurnTodoItem,
    TurnTodoSnapshot,
    TurnTodoStore,
    normalize_todo_snapshot,
)
def _item(step_id: str, title: str, status: str = "pending") -> TurnTodoItem:
    return TurnTodoItem(id=step_id, title=title, status=status)  # type: ignore[arg-type]


def test_todo_snapshot_validates_invariants_and_rejects_sensitive_text():
    snapshot = normalize_todo_snapshot(
        {
            "items": [
                {"id": "list", "title": "List saved items", "status": "completed"},
                {"id": "summarize", "title": "Summarize the selected item", "status": "in_progress"},
            ]
        }
    )
    assert isinstance(snapshot, TurnTodoSnapshot)
    assert snapshot.in_progress is not None
    assert tuple(item.id for item in snapshot.items) == ("list", "summarize")

    with pytest.raises(TodoValidationError):
        TurnTodoSnapshot(tuple(_item(str(index), "step") for index in range(7)))
    with pytest.raises(TodoValidationError):
        TurnTodoSnapshot((_item("one", "first"), _item("one", "duplicate")))
    with pytest.raises(TodoValidationError):
        TurnTodoSnapshot((_item("one", "first", "in_progress"), _item("two", "second", "in_progress")))
    with pytest.raises(TodoValidationError):
        _item("one", "https://private.example/record")
    with pytest.raises(TodoValidationError):
        _item("tenant_id", "resolve the item")


def test_todo_store_replacement_is_atomic_and_turn_scoped():
    store = TurnTodoStore()
    completed = store.write(
        [{"id": "first", "title": "Read the list", "status": "completed"}]
    )
    assert completed.items[0].status == "completed"

    with pytest.raises(TodoValidationError):
        # A failed replacement must not partially update the in-memory store.
        store.write(
            [
                {"id": "first", "title": "Read the list", "status": "pending"},
                {"id": "bad", "title": "https://example.invalid", "status": "pending"},
            ]
        )
    assert store.snapshot == completed

    assert store.finalize() == completed
    assert store.mark_blocked().items[0].status == "completed"
    assert TurnTodoStore().snapshot == TurnTodoSnapshot.empty()


def test_todo_finalize_allows_only_explicit_partial_or_terminal_outcomes():
    store = TurnTodoStore(
        [
            {"id": "first", "title": "Read the list", "status": "in_progress"},
        ]
    )
    with pytest.raises(TodoValidationError):
        store.finalize()
    blocked = store.mark_blocked()
    assert blocked.items[0].status == "blocked"
    assert store.finalize(normal_completion=False, allow_blocked=True) == blocked

    terminal = TurnTodoStore(
        [{"id": "action", "title": "Submit the change", "status": "in_progress"}]
    )
    assert terminal.finalize(terminal_action=True).unfinished


def test_recovery_policy_grants_one_exact_read_retry_and_two_total_actions():
    ledger = RecoveryLedger()
    policy = RecoveryPolicy(ledger)
    error = ErrorEnvelope.from_category(
        "transient_read", operation="read", partial_evidence=True
    )

    first = policy.grant(
        error,
        read_fingerprint="read-a",
        has_evidence=True,
        retrieval_budget_remaining=1,
    )
    assert first.remaining_actions == 2
    assert first.allowed[:2] == ("retry_same_read", "use_existing_evidence")
    assert ledger.record_same_read_retry("read-a")

    second = policy.grant(
        error,
        read_fingerprint="read-a",
        has_evidence=True,
        retrieval_budget_remaining=1,
    )
    assert "retry_same_read" not in second.allowed
    assert ledger.record_recovery("return_partial", category="transient_read")
    assert ledger.remaining_actions == 0
    assert policy.grant(error, read_fingerprint="read-b").allowed == ()
    assert ledger.same_read_retry_count("read-a") == 1


def test_answer_repair_has_one_ceiling_and_shares_turn_budget():
    ledger = RecoveryLedger()
    policy = RecoveryPolicy(ledger)
    error = ErrorEnvelope.from_category(
        "answer_validation", operation="answer", partial_evidence=True
    )
    grant = policy.grant(error, has_evidence=True)
    assert grant.allowed == ("repair_answer",)
    assert ledger.record_answer_repair()
    assert not ledger.record_answer_repair()
    assert ledger.remaining_actions == 1

    # The second whole-turn action consumes the remaining budget; no third
    # answer repair or read recovery can be granted.
    assert ledger.record_recovery("report_unavailable", category="answer_validation")
    assert ledger.remaining_actions == 0
    assert policy.grant(error, has_evidence=True).allowed == ()


def test_policy_denies_mutation_provider_and_security_recovery():
    policy = RecoveryPolicy()
    mutation_error = ErrorEnvelope.from_category(
        "transient_read", operation="mutation"
    )
    provider_error = ErrorEnvelope.from_category(
        "provider_failure", operation="provider"
    )
    security_error = ErrorEnvelope.from_category(
        "policy_or_security", operation="read"
    )
    assert policy.grant(mutation_error, read_fingerprint="mutation").allowed == ()
    assert policy.grant(provider_error, has_evidence=True).allowed == ()
    assert policy.grant(security_error, has_evidence=True).allowed == ()
    assert not policy.ledger.record_recovery("retry_mutation", operation="mutation")
    assert policy.ledger.total_actions == 0


def test_empty_search_reformulation_requires_remaining_search_budget():
    policy = RecoveryPolicy()
    assert policy.grant_for_empty_search(search_budget_remaining=0).allowed == ()
    grant = policy.grant_for_empty_search(search_budget_remaining=1)
    assert grant.allowed == ("reformulate_search", "report_unavailable")


def test_error_envelope_is_allow_listed_and_redacts_private_values():
    envelope = ErrorEnvelope.from_category("missing_context", operation="read")
    assert envelope.code == "missing_context"
    with pytest.raises(ValueError):
        ErrorEnvelope(
            category="read_unavailable",
            code="read_unavailable",
            operation="read",
            safe_message="provider body: https://private.example/secret",
        )
    with pytest.raises(ValueError):
        ErrorEnvelope(
            category="read_unavailable",
            code="raw_exception_text",
            operation="read",
            safe_message="The read is unavailable.",
        )
