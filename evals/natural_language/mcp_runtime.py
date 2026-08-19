"""Official MCP stdio lifecycle and privacy-safe diagnostic correlation."""

from __future__ import annotations

import asyncio
import json
import math
import os
import re
import sys
import tempfile
from contextlib import AsyncExitStack
from dataclasses import dataclass, field
from pathlib import Path
from threading import Lock
from typing import Any, TextIO

from mcp.client.session import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

from app.diagnostics import _TOOLS as AGENT_TOOL_NAMES
from app.mcp_server import MCP_TOOL_NAMES

_SAFE_STAGES = frozenset(
    {
        "model_attempt",
        "tool_call",
        "action_validated",
        "agent_failed",
        "citation_validated",
        "gateway_response_ready",
    }
)
_SAFE_OUTCOMES = frozenset({"started", "succeeded", "failed", "skipped"})
_SAFE_ERROR_CODES = frozenset(
    {
        "limit",
        "timeout",
        "item_scope_required",
        "answer_unavailable",
        "runtime_error",
        "not_found",
        "search_required",
        "no_evidence",
    }
)
_SAFE_FAILURE_REASONS = frozenset(
    {
        "invalid_structure", "unsafe_text", "missing_citation", "invalid_citation",
        "unknown_citation", "duplicate_citation",
        "too_many_segments", "too_many_items", "missing_scope_item",
        "no_evidence_unavailable", "provider_failure",
    }
)
_SAFE_DISPOSITIONS = frozenset({"grounded", "no_evidence", "canonical", "action", "failed"})
_REQUEST_ID_RE = re.compile(r"[0-9a-f]{32}\Z")


@dataclass(frozen=True)
class ToolTrace:
    tool_name: str
    call_index: int
    outcome: str
    boundary: str = "agent_model"


@dataclass
class DiagnosticCollector:
    _buffer: str = ""
    _events: list[dict[str, Any]] = field(default_factory=list)
    _retrieval_events: list[dict[str, Any]] = field(default_factory=list)
    malformed_count: int = 0
    _lock: Lock = field(default_factory=Lock)

    def write(self, value: str) -> int:
        with self._lock:
            self._buffer += value
            if len(self._buffer) > 16_384 and "\n" not in self._buffer:
                self._buffer = ""
                self.malformed_count += 1
                return len(value)
            while "\n" in self._buffer:
                line, self._buffer = self._buffer.split("\n", 1)
                self._accept(line)
        return len(value)

    def flush(self) -> None:
        return None

    def _accept(self, line: str) -> None:
        try:
            value = json.loads(line)
        except (json.JSONDecodeError, TypeError):
            self.malformed_count += 1
            return
        if not isinstance(value, dict):
            return
        if value.get("event") == "retrieval_detail":
            self._accept_retrieval(value)
            return
        if value.get("event") != "knowledge_request":
            return
        stage, request_id = value.get("stage"), value.get("request_id")
        if stage not in _SAFE_STAGES or not isinstance(request_id, str) or not _REQUEST_ID_RE.fullmatch(request_id):
            return
        event: dict[str, Any] = {"stage": stage, "request_id": request_id}
        if value.get("tool_name") in AGENT_TOOL_NAMES:
            event["tool_name"] = value["tool_name"]
        if isinstance(value.get("call_index"), int) and 0 <= value["call_index"] <= 100:
            event["call_index"] = value["call_index"]
        if value.get("tool_outcome") in _SAFE_OUTCOMES:
            event["tool_outcome"] = value["tool_outcome"]
        if value.get("agent_phase") in {"retrieval", "answer"}:
            event["agent_phase"] = value["agent_phase"]
        if value.get("error_code") in _SAFE_ERROR_CODES:
            event["error_code"] = value["error_code"]
        if value.get("failure_reason") in _SAFE_FAILURE_REASONS:
            event["failure_reason"] = value["failure_reason"]
        if value.get("disposition") in _SAFE_DISPOSITIONS:
            event["disposition"] = value["disposition"]
        self._events.append(event)

    def _accept_retrieval(self, value: dict[str, Any]) -> None:
        request_id = value.get("request_id")
        tool_name = value.get("tool_name")
        if (
            not isinstance(request_id, str)
            or not _REQUEST_ID_RE.fullmatch(request_id)
            or tool_name not in {"search_segments", "get_neighbors"}
        ):
            return
        event: dict[str, Any] = {"request_id": request_id, "tool_name": tool_name}
        call_index = value.get("call_index")
        if isinstance(call_index, int) and not isinstance(call_index, bool) and 0 <= call_index <= 100:
            event["call_index"] = call_index
        for key in ("item_id", "segment_id"):
            item = value.get(key)
            if isinstance(item, int) and not isinstance(item, bool) and item > 0:
                event[key] = item
        for source, target in (("start", "start_sec"), ("score", "score")):
            item = value.get(source)
            if isinstance(item, (int, float)) and not isinstance(item, bool) and math.isfinite(item):
                event[target] = float(item)
        self._retrieval_events.append(event)

    def events_for(self, request_id: str) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(row) for row in self._events if row["request_id"] == request_id]

    async def wait_for_response_diagnostics(self, request_id: str, *, timeout_seconds: float) -> None:
        deadline = asyncio.get_running_loop().time() + timeout_seconds
        while asyncio.get_running_loop().time() < deadline:
            if any(row["stage"] == "gateway_response_ready" for row in self.events_for(request_id)):
                return
            await asyncio.sleep(0.005)

    def traces_for(self, request_id: str) -> list[ToolTrace]:
        grouped: dict[tuple[int, str], list[str]] = {}
        for row in self.events_for(request_id):
            if row.get("stage") != "tool_call" or not isinstance(row.get("tool_name"), str):
                continue
            key = (int(row.get("call_index", 0)), row["tool_name"])
            grouped.setdefault(key, []).append(str(row.get("tool_outcome", "unknown")))
        priority = {"succeeded": 4, "failed": 3, "skipped": 2, "started": 1, "unknown": 0}
        traces = [
            ToolTrace(name, index, max(outcomes, key=lambda value: priority.get(value, 0)))
            for (index, name), outcomes in grouped.items()
        ]
        return sorted(traces, key=lambda trace: (trace.call_index, trace.tool_name))

    def has_model_attempt(self, request_id: str) -> bool:
        return any(row["stage"] == "model_attempt" for row in self.events_for(request_id))

    def model_attempt_count(self, request_id: str) -> int:
        return sum(
            row["stage"] == "model_attempt" for row in self.events_for(request_id)
        )

    def agent_failure_code(self, request_id: str) -> str | None:
        failures = [
            row.get("error_code")
            for row in self.events_for(request_id)
            if row["stage"] == "agent_failed"
            and isinstance(row.get("error_code"), str)
        ]
        return failures[-1] if failures else None

    def has_retrieval_detail_call(
        self, request_id: str, *, tool_name: str = "search_segments"
    ) -> bool:
        with self._lock:
            return any(
                row["request_id"] == request_id and row["tool_name"] == tool_name
                for row in self._retrieval_events
            )

    def retrieval_hits_for(
        self, request_id: str, *, tool_name: str = "search_segments"
    ) -> list[dict[str, Any]]:
        with self._lock:
            rows = [
                dict(row)
                for row in self._retrieval_events
                if row["request_id"] == request_id
                and row["tool_name"] == tool_name
                and "item_id" in row
                and "segment_id" in row
            ]
        return [
            {
                "item_id": row["item_id"],
                "segment_id": row["segment_id"],
                **({"start_sec": row["start_sec"]} if "start_sec" in row else {}),
            }
            for row in rows
        ]


class LiveMcpSession:
    """MCP process whose stderr is captured by an actual tempfile file descriptor."""

    def __init__(self, token: str, *, cwd: Path) -> None:
        self._token = token
        self._cwd = cwd
        self._stack: AsyncExitStack | None = None
        self.client: ClientSession | None = None
        self.diagnostics = DiagnosticCollector()
        self._errlog: TextIO | None = None
        self._err_offset = 0

    async def start(self) -> None:
        if self._stack is not None:
            return
        stack = AsyncExitStack()
        # O_APPEND prevents the parent's seek/read drain from moving the
        # subprocess's shared stderr file offset backwards while it writes.
        errlog = tempfile.TemporaryFile(mode="a+", encoding="utf-8")
        env = dict(os.environ)
        env["MCP_TOKEN"] = self._token
        parameters = StdioServerParameters(
            command=sys.executable,
            args=["-m", "app.cli", "mcp-server", "--transport", "stdio"],
            env=env,
            cwd=self._cwd,
        )
        try:
            streams = await stack.enter_async_context(stdio_client(parameters, errlog=errlog))
            client = await stack.enter_async_context(ClientSession(*streams))
            await client.initialize()
            self._stack, self.client, self._errlog = stack, client, errlog
        except BaseException:
            try:
                await stack.aclose()
            finally:
                if not errlog.closed:
                    errlog.close()
            raise

    def _drain_stderr(self) -> None:
        if self._errlog is None or self._errlog.closed:
            return
        self._errlog.flush()
        self._errlog.seek(self._err_offset)
        chunk = self._errlog.read()
        self._err_offset = self._errlog.tell()
        if chunk:
            self.diagnostics.write(chunk)

    async def stop(self) -> None:
        stack, self._stack, self.client = self._stack, None, None
        errlog = self._errlog
        close_error: BaseException | None = None
        try:
            if stack is not None:
                self._drain_stderr()
                try:
                    await stack.aclose()
                except BaseException as exc:
                    close_error = exc
        finally:
            # The tempfile deliberately outlives the transport stack so final
            # server diagnostics emitted during shutdown can still be read.
            try:
                self._drain_stderr()
            finally:
                self._errlog = None
                if errlog is not None and not errlog.closed:
                    errlog.close()
            self._err_offset = 0
        if close_error is not None:
            raise close_error

    async def restart(self) -> None:
        await self.stop()
        await self.start()

    async def list_tools(self) -> tuple[str, ...]:
        if self.client is None:
            raise RuntimeError("MCP session is not started")
        result = await self.client.list_tools()
        return tuple(tool.name for tool in result.tools)

    async def call(self, tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
        if self.client is None:
            raise RuntimeError("MCP session is not started")
        if tool not in MCP_TOOL_NAMES:
            raise ValueError("unknown MCP tool")
        result = await self.client.call_tool(tool, arguments)
        payload = result.structured_content
        if result.is_error or not isinstance(payload, dict):
            raise RuntimeError("MCP tool returned a protocol error")
        request_id = payload.get("request_id")
        if isinstance(request_id, str):
            for _ in range(20):
                self._drain_stderr()
                if any(row["stage"] == "gateway_response_ready" for row in self.diagnostics.events_for(request_id)):
                    break
                await asyncio.sleep(0.005)
        else:
            self._drain_stderr()
        return payload
