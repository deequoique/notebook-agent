"""Persistent tenant-scoped live fixture discovery and provisioning."""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Any

from sqlalchemy import func, select

from app.db import get_session_factory
from app.ingest.submission import normalize_item_reference
from app.models import ContentItem, IngestCompletionEvent, IngestDispatch, Segment

from .mcp_runtime import LiveMcpSession
from .schema import Catalog


@dataclass(frozen=True)
class FixtureState:
    variables: dict[str, Any]
    capabilities: frozenset[str]
    readiness: dict[str, bool]


def _reference_key(url: str) -> tuple[str, str] | None:
    try:
        value = normalize_item_reference(url)
        return value.platform, value.platform_id
    except Exception:
        return None


async def prepare_fixtures(
    runtime: LiveMcpSession, catalog: Catalog, *, user_id: int, timeout_seconds: float
) -> FixtureState:
    variables: dict[str, Any] = {"unknown_topic": "月球背面的紫色咖啡机协议 ZQ-947"}
    for name, fixture in catalog.fixtures.items():
        variables[f"{name}_url"] = fixture.url
        variables[f"{name}_topic"] = fixture.topic

    inventory = await _all_inventory(runtime)
    by_reference = {
        key: row for row in inventory if isinstance(row, dict)
        if isinstance(row.get("url"), str) and (key := _reference_key(row["url"])) is not None
    }
    newly_submitted: set[int] = set()
    for fixture in catalog.fixtures.values():
        row = by_reference.get(_reference_key(fixture.url))
        if row is None or row.get("deleted_at") is not None:
            output = await runtime.call(
                "submit_knowledge_urls",
                {"urls": [fixture.url], "why_saved": "natural-language live eval fixture"},
            )
            for result in output.get("results", []):
                if (
                    isinstance(result, dict)
                    and result.get("status") == "queued"
                    and isinstance(result.get("item_id"), int)
                ):
                    newly_submitted.add(result["item_id"])

    required = {_reference_key(value.url) for value in catalog.fixtures.values()}
    if None in required or len(required) != len(catalog.fixtures):
        raise RuntimeError("catalog fixture references are invalid or duplicated")
    deadline = time.monotonic() + timeout_seconds
    ready: dict[tuple[str, str], dict[str, Any]] = {}
    while time.monotonic() < deadline:
        inventory = await _all_inventory(runtime)
        ready = {
            key: row for row in inventory if isinstance(row, dict)
            if isinstance(row.get("url"), str)
            and (key := _reference_key(row["url"])) in required
            and row.get("ingestion_state") == "ready" and row.get("deleted_at") is None
        }
        if required.issubset(ready):
            break
        await asyncio.sleep(min(5.0, max(0.1, deadline - time.monotonic())))
    if not required.issubset(ready):
        raise RuntimeError(f"fixture ingestion did not reach ready state ({len(required - set(ready))} unavailable)")
    for name, fixture in catalog.fixtures.items():
        item_id = ready[_reference_key(fixture.url)].get("item_id")
        if not isinstance(item_id, int):
            raise RuntimeError("fixture inventory omitted a typed item id")
        variables[f"{name}_item_id"] = item_id

    capabilities = {"ready_item", "full_management"}
    if any(fixture.mutable for fixture in catalog.fixtures.values()):
        capabilities.add("mutable_item")
    fixture_ids = {
        int(variables[f"{name}_item_id"]) for name in catalog.fixtures
    }
    readiness = _verify_persistence(user_id, fixture_ids, newly_submitted)
    failed_id = _find_failed_item(user_id)
    if failed_id is not None:
        capabilities.add("failed_item")
        variables["failed_item_id"] = failed_id
    return FixtureState(variables, frozenset(capabilities), readiness)


async def prepare_existing_fixtures(
    runtime: LiveMcpSession,
    catalog: Catalog,
    *,
    fixture_names: set[str],
) -> FixtureState:
    """Resolve ready read-only fixtures without provisioning or worker checks."""

    if not fixture_names or fixture_names - set(catalog.fixtures):
        raise RuntimeError("human benchmark references unknown fixtures")
    variables: dict[str, Any] = {"unknown_topic": "月球背面的紫色咖啡机协议 ZQ-947"}
    selected = {name: catalog.fixtures[name] for name in sorted(fixture_names)}
    inventory = await _all_inventory(runtime)
    by_reference = {
        key: row
        for row in inventory
        if isinstance(row, dict)
        and isinstance(row.get("url"), str)
        and (key := _reference_key(row["url"])) is not None
    }
    for name, fixture in selected.items():
        variables[f"{name}_url"] = fixture.url
        variables[f"{name}_topic"] = fixture.topic
        row = by_reference.get(_reference_key(fixture.url))
        if (
            row is None
            or row.get("ingestion_state") != "ready"
            or row.get("deleted_at") is not None
            or not isinstance(row.get("item_id"), int)
        ):
            raise RuntimeError("required read-only fixture is unavailable")
        variables[f"{name}_item_id"] = row["item_id"]
    return FixtureState(
        variables,
        frozenset({"ready_item"}),
        {"existing_ready_items": True, "provisioned_this_run": False},
    )


async def _all_inventory(runtime: LiveMcpSession) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    cursor: str | None = None
    for _ in range(20):
        args: dict[str, Any] = {"location": "library", "limit": 50}
        if cursor:
            args["cursor"] = cursor
        output = await runtime.call("list_saved_items", args)
        rows.extend(row for row in output.get("items", []) if isinstance(row, dict))
        cursor = output.get("next_cursor")
        if not isinstance(cursor, str) or not cursor:
            return rows
    raise RuntimeError("fixture inventory exceeded the bounded page limit")


def _verify_persistence(
    user_id: int, fixture_ids: set[int], newly_submitted: set[int]
) -> dict[str, bool]:
    factory = get_session_factory()
    with factory() as db:
        items = list(db.scalars(select(ContentItem).where(
            ContentItem.user_id == user_id,
            ContentItem.id.in_(fixture_ids),
            ContentItem.state == "ready",
            ContentItem.deleted_at.is_(None),
        )))
        ids = [item.id for item in items]
        def count(model) -> int:
            return int(db.scalar(select(func.count()).select_from(model).where(model.item_id.in_(ids))) or 0) if ids else 0
        embedded_segments = int(db.scalar(
            select(func.count()).select_from(Segment).where(
                Segment.item_id.in_(ids), Segment.embedding.is_not(None)
            )
        ) or 0) if ids else 0
        return {
            "postgres_ready_items": set(ids) == fixture_ids,
            "pgvector_segments": embedded_segments > 0,
            "minio_object_present": any(bool(item.raw_object_key) for item in items),
            "ingest_dispatches": count(IngestDispatch) > 0,
            "completion_events": count(IngestCompletionEvent) > 0,
            "provisioned_this_run": bool(newly_submitted),
        }


def _find_failed_item(user_id: int) -> int | None:
    with get_session_factory()() as db:
        candidates = list(db.scalars(select(ContentItem.id).where(
            ContentItem.user_id == user_id, ContentItem.state == "failed", ContentItem.deleted_at.is_(None)
        ).order_by(ContentItem.id.desc()).limit(50)))
        for item_id in candidates:
            latest_state = db.scalar(
                select(IngestDispatch.state)
                .where(IngestDispatch.item_id == item_id)
                .order_by(IngestDispatch.attempt.desc())
                .limit(1)
            )
            if latest_state not in {"pending", "enqueued", "running"}:
                return item_id
    return None
