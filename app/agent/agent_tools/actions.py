"""Save, confirmation, and item-management tool registration."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field
from pydantic_ai import Agent, RunContext
from pydantic_ai.exceptions import ModelRetry

from app.agent.actions import ActionInputMismatch
from app.agent.agent_tools.policy import ToolPolicy
from app.agent.runtime_state import AgentDeps


def register_action_tools(
    agent: Agent,
    policy: ToolPolicy,
) -> None:
    @agent.tool(prepare=policy.prepare_bare_url_action)
    def request_save_confirmation(
        ctx: RunContext[AgentDeps], urls: list[str]
    ) -> dict:
        """Persist a bounded bare-URL batch for explicit confirmation."""

        try:
            outcome, call_index = policy.execute_tool(
                ctx.deps,
                "request_save_confirmation",
                lambda: ctx.deps.actions.request_confirmation(urls),
            )
        except ActionInputMismatch:
            raise ModelRetry(
                "Include every URL from the current user message exactly once "
                "per occurrence and in the original order; do not add URLs."
            ) from None
        ctx.deps.tool_event(
            "request_save_confirmation",
            "succeeded",
            call_index,
            len(outcome.results),
        )
        return outcome.tool_payload()

    @agent.tool(prepare=policy.prepare_save)
    def save_videos(
        ctx: RunContext[AgentDeps],
        urls: list[str],
        why_saved: str | None = None,
    ) -> dict:
        """Queue current-message video URLs for the private tenant."""

        try:
            outcome, call_index = policy.execute_tool(
                ctx.deps,
                "save_videos",
                lambda: ctx.deps.actions.save_videos(
                    urls,
                    why_saved=why_saved,
                ),
            )
        except ActionInputMismatch:
            raise ModelRetry(
                "Include every URL from the current user message exactly once "
                "per occurrence and in the original order; do not add URLs."
            ) from None
        ctx.deps.tool_event(
            "save_videos", "succeeded", call_index, len(outcome.results)
        )
        return outcome.tool_payload()

    @agent.tool(prepare=policy.prepare_pending_save)
    def confirm_video_save(ctx: RunContext[AgentDeps]) -> dict:
        """Consume the current conversation's persisted pending batch."""

        outcome, call_index = policy.execute_tool(
            ctx.deps,
            "confirm_video_save",
            ctx.deps.actions.confirm,
        )
        ctx.deps.tool_event(
            "confirm_video_save", "succeeded", call_index, len(outcome.results)
        )
        return outcome.tool_payload()

    @agent.tool(prepare=policy.prepare_pending_save)
    def clarify_save_confirmation(ctx: RunContext[AgentDeps]) -> dict:
        """Ask for a clear decision without consuming the pending batch."""

        outcome, call_index = policy.execute_tool(
            ctx.deps,
            "clarify_save_confirmation",
            ctx.deps.actions.clarify_confirmation,
        )
        ctx.deps.tool_event(
            "clarify_save_confirmation",
            "succeeded",
            call_index,
            len(outcome.results),
        )
        return outcome.tool_payload()

    @agent.tool(prepare=policy.prepare_pending_save)
    def cancel_video_save(ctx: RunContext[AgentDeps]) -> dict:
        """Cancel the current conversation's persisted pending batch."""

        outcome, call_index = policy.execute_tool(
            ctx.deps,
            "cancel_video_save",
            ctx.deps.actions.cancel,
        )
        ctx.deps.tool_event(
            "cancel_video_save", "succeeded", call_index, len(outcome.results)
        )
        return outcome.tool_payload()

    @agent.tool(prepare=policy.prepare_management)
    def list_saved_items(
        ctx: RunContext[AgentDeps],
        kind: Literal["video", "article"] | None = None,
        platform: Literal[
            "youtube", "bilibili", "wechat_mp", "ntu_kaltura"
        ] | None = None,
        state: Literal[
            "pending", "fetching", "needs_extension", "needs_asr", "chunking",
            "embedding", "ready", "failed", "no_text",
        ] | None = None,
        location: Literal["library", "trash"] = "library",
        limit: Annotated[int, Field(ge=1, le=50)] = 20,
        cursor: Annotated[str | None, Field(max_length=512)] = None,
    ) -> dict:
        """List the current tenant's bounded inventory projection."""
        arguments = {
            "kind": kind,
            "platform": platform,
            "state": state,
            "location": location,
            "limit": int(limit),
            "cursor": cursor,
        }
        return policy.run_management_read(
            ctx,
            tool_name="list_saved_items",
            arguments=arguments,
            operation=lambda: ctx.deps.actions.list_saved_items(**arguments),
        )

    @agent.tool(prepare=policy.prepare_management)
    def get_saved_item(
        ctx: RunContext[AgentDeps], item_id: Annotated[int, Field(gt=0)]
    ) -> dict:
        """Read one item previously identified by the inventory list."""
        return policy.run_management_read(
            ctx,
            tool_name="get_saved_item",
            arguments={"item_id": int(item_id)},
            operation=lambda: ctx.deps.actions.get_saved_item(item_id),
        )

    @agent.tool(prepare=policy.prepare_management)
    def update_saved_item(
        ctx: RunContext[AgentDeps],
        item_id: Annotated[int, Field(gt=0)],
        why_saved: Annotated[str | None, Field(max_length=500)],
    ) -> dict:
        """Update or clear only the user's saved reason."""
        outcome, call_index = policy.execute_tool(
            ctx.deps,
            "update_saved_item",
            lambda: ctx.deps.actions.update_saved_item(item_id, why_saved),
        )
        ctx.deps.tool_event(
            "update_saved_item",
            "succeeded" if outcome.status == "ok" else "failed",
            call_index,
            len(outcome.results),
        )
        return outcome.tool_payload()

    @agent.tool(prepare=policy.prepare_management)
    def delete_saved_items(
        ctx: RunContext[AgentDeps],
        item_ids: Annotated[
            list[Annotated[int, Field(gt=0)]],
            Field(min_length=1, max_length=10),
        ],
    ) -> dict:
        """Create a durable deletion confirmation; never deletes immediately."""
        outcome, call_index = policy.execute_tool(
            ctx.deps,
            "delete_saved_items",
            lambda: ctx.deps.actions.request_delete(item_ids),
        )
        ctx.deps.tool_event(
            "delete_saved_items",
            "succeeded" if outcome.status == "ok" else "failed",
            call_index,
            len(outcome.results),
        )
        return outcome.tool_payload()

    @agent.tool(prepare=policy.prepare_pending_delete)
    def confirm_item_deletion(ctx: RunContext[AgentDeps]) -> dict:
        outcome, call_index = policy.execute_tool(
            ctx.deps,
            "confirm_item_deletion",
            ctx.deps.actions.confirm_delete,
        )
        ctx.deps.tool_event(
            "confirm_item_deletion",
            "succeeded" if outcome.status == "ok" else "failed",
            call_index,
            len(outcome.results),
        )
        return outcome.tool_payload()

    @agent.tool(prepare=policy.prepare_pending_delete)
    def clarify_item_deletion(ctx: RunContext[AgentDeps]) -> dict:
        outcome, call_index = policy.execute_tool(
            ctx.deps,
            "clarify_item_deletion",
            ctx.deps.actions.clarify_delete,
        )
        ctx.deps.tool_event(
            "clarify_item_deletion",
            "succeeded" if outcome.status == "ok" else "failed",
            call_index,
            len(outcome.results),
        )
        return outcome.tool_payload()

    @agent.tool(prepare=policy.prepare_pending_delete)
    def cancel_item_deletion(ctx: RunContext[AgentDeps]) -> dict:
        outcome, call_index = policy.execute_tool(
            ctx.deps,
            "cancel_item_deletion",
            ctx.deps.actions.cancel_delete,
        )
        ctx.deps.tool_event(
            "cancel_item_deletion",
            "succeeded" if outcome.status == "ok" else "failed",
            call_index,
            len(outcome.results),
        )
        return outcome.tool_payload()

    @agent.tool(prepare=policy.prepare_management)
    def restore_saved_items(
        ctx: RunContext[AgentDeps],
        item_ids: Annotated[
            list[Annotated[int, Field(gt=0)]],
            Field(min_length=1, max_length=10),
        ],
    ) -> dict:
        outcome, call_index = policy.execute_tool(
            ctx.deps,
            "restore_saved_items",
            lambda: ctx.deps.actions.restore_saved_items(item_ids),
        )
        ctx.deps.tool_event(
            "restore_saved_items",
            "succeeded" if outcome.status == "ok" else "failed",
            call_index,
            len(outcome.results),
        )
        return outcome.tool_payload()

    @agent.tool(prepare=policy.prepare_management)
    def retry_item_ingestion(
        ctx: RunContext[AgentDeps], item_id: Annotated[int, Field(gt=0)]
    ) -> dict:
        outcome, call_index = policy.execute_tool(
            ctx.deps,
            "retry_item_ingestion",
            lambda: ctx.deps.actions.retry_item_ingestion(item_id),
        )
        ctx.deps.tool_event(
            "retry_item_ingestion",
            "succeeded" if outcome.status == "ok" else "failed",
            call_index,
            len(outcome.results),
        )
        return outcome.tool_payload()
