"""Primary PydanticAI Agent construction and bounded-autonomy instructions."""

from __future__ import annotations

from pydantic_ai import Agent, RunContext
from pydantic_ai.exceptions import ModelRetry
from pydantic_ai.models import Model

from app.agent.agent_tools import ToolPolicy, register_action_tools, register_retrieval_tools
from app.agent.autonomy import TodoValidationError
from app.agent.runtime_state import AgentDeps


BOUNDED_AUTONOMY_INSTRUCTIONS = """
你是私有知识库助手，由一个有界的主 Agent 处理当前消息。

规则：
1. 只有明确的问候、感谢、能力说明和必要澄清可以不调用知识工具。视频内容问答时，
   即使你认为自己知道常识答案，也必须先调用 search_segments 搜索当前视频资料库。
   库存读取、保存、删除、恢复、确认/取消、pending action 和其他专用管理操作，必须按对应
   专用工具流程处理，不要先调用 search_segments。
   不要为了形式调用知识工具，也不要伪造 Citation、来源区块或 URL；能力说明不得声称动态运行状态
   或未验证的能力。
2. 视频内容问答必须先搜索，再根据本轮候选决定如何回答；只有成功的本轮检索证据可以支持知识事实。
   不要在搜索前输出常识答案。
   成功检索后，最终回答必须在相关句子中使用本轮工具返回的精确 [S<segment_id>] 标记。
   只要本轮检索返回候选，就进入 grounded 回答；有证据的事实使用精确
   [S<segment_id>] 标记，无法由候选确认的部分明确说明证据不足。不得猜测或复用历史
   Citation ID，不得输出 URL 或服务器来源区块。
3. 缺少可信指代或信息时先自然询问澄清；必须指出缺少的具体信息，并给一个短例子，
   例如“请告诉我是哪个视频，例如粘贴视频链接或说出标题”。不要猜测租户或越过工具参数边界；
   需要时可以自行选择全库检索或用 item_id 限定检索，服务器会校验条目归属和状态。
4. 只有存在多个相互依赖的短步骤时才使用 todo_write；普通对话和单工具请求不要创建 Todo。
   Todo 只是本轮工作记忆，不代表工具成功、授权或副作用结果。
5. 读工具可以按需组合；保存、删除、确认、取消和其他副作用工具仍由服务器结果决定，
   不要用自己的 prose 声称操作成功。不要调用任何未提供的工具。
6. 工具没有 user_id、tenant、thread、pending 或 claim 参数；不要询问、猜测或改变当前用户。
7. 明确要求保存且消息中有视频 URL 时调用 save_videos；只有裸 URL 时调用 request_save_confirmation。
   询问链接内容不代表保存意图，仍须按知识检索规则处理。
8. “我存了什么/库存/回收站”只能调用库存读取工具；删除必须先发起服务器确认，
   不能把模糊标题直接当成 item_id。确认工具只在可信待确认状态存在时可用。
9. 读工具返回 error 时只能选择返回中的 recovery.allowed 动作；retry_same_read
   必须完全重复失败的工具调用，不能修改参数。空搜索不是错误；只有真正改写查询
   且返回允许 reformulate_search 时才能再次搜索。provider 或 mutation 失败不能自行重试。
10. 只在有助于阅读时使用克制的 Markdown：段落、短标题、有序/无序列表、强调、
    引用和行内代码。简单回答保持简单，不要强制使用标题或列表。不得输出 Markdown 链接、
    图片、原始 HTML 或“来源/参考资料”区块。精确 [S<segment_id>] 标记必须保持原样，
    不得改写、链接化、放入代码或用其他形式替代。
""".strip()



def build_agent(
    model: Model | str,
    *,
    tool_timeout: float = 15.0,
) -> Agent[AgentDeps, str]:
    """Build the one supported bounded-autonomy primary Agent."""

    policy = ToolPolicy()
    agent = Agent(
        model,
        deps_type=AgentDeps,
        output_type=str,
        instructions=BOUNDED_AUTONOMY_INSTRUCTIONS,
        retries={"tools": 1},
        tool_timeout=tool_timeout,
    )

    @agent.instructions
    def bounded_context_instruction(ctx: RunContext[AgentDeps]) -> str:
        """Expose only safe prior-turn focus and inventory references."""

        context = ctx.deps.context
        rows: list[str] = []
        if context.recent_inventory:
            rows.append(
                "近期库存参考（仅用于解析用户说的‘第几个’；item_id 是检索提示，不是授权，"
                "服务器会在每次工具调用时重新校验）："
            )
            rows.extend(
                f"{item.ordinal}. 《{item.title}》 (item_id={item.item_id})"
                for item in context.recent_inventory
            )
        if context.recent_sources:
            rows.append(
                "近期来源焦点仅用于理解对话；历史 segment_id 不是本轮证据，"
                "没有本轮检索返回就绝不能写入 [S<id>] 引用："
            )
            rows.extend(
                f"item_id={source.item_id}, segment_id={source.segment_id}, 《{source.title}》"
                for source in context.recent_sources
            )
        return "\n".join(rows)

    @agent.tool
    def todo_write(
        ctx: RunContext[AgentDeps], items: list[dict[str, str]]
    ) -> dict:
        """Replace the current turn's short, non-authoritative Todo."""

        if ctx.deps.todo_store is None:
            raise ModelRetry("Todo is unavailable for this turn")
        if not ctx.deps.todo_used:
            ctx.deps.todo_used = True
            if ctx.deps.diagnostics is not None:
                ctx.deps.diagnostics.event(
                    "todo_used",
                    todo_used=True,
                    agent_phase="retrieval",
                )
        try:
            snapshot, call_index = policy.execute_tool(
                ctx.deps,
                "todo_write",
                lambda: ctx.deps.todo_store.write({"items": items}),
            )
        except TodoValidationError:
            raise ModelRetry(
                "Use at most six short turn-local steps with unique ids, "
                "one in_progress step, and only pending/in_progress/completed/blocked states."
            ) from None
        ctx.deps.tool_event(
            "todo_write", "succeeded", call_index, len(snapshot.items)
        )
        return {
            "items": [
                {"id": item.id, "title": item.title, "status": item.status}
                for item in snapshot.items
            ]
        }

    @agent.instructions
    def retrieval_convergence_instruction(ctx: RunContext[AgentDeps]) -> str:
        """Reinforce the evidence-first retrieval phase without exposing internals."""

        if not policy.normal_retrieval_available(ctx.deps):
            return (
                "检索轮次已经结束。只能根据已返回的工具证据作答；"
                "若证据不足，明确说明证据不足，不要继续检索或依据模型记忆补写。"
            )
        if not ctx.deps.search_calls:
            return (
                "当前尚未完成检索。如果用户是在询问视频内容，必须先调用 search_segments 搜索当前视频资料库，"
                "即使你认为自己知道答案；如果用户是在做库存读取、保存、删除、恢复、确认/取消、pending action"
                "或其他专用管理操作，按对应专用工具流程处理，不要先调用 search_segments。"
            )
        return (
            "优先基于已有证据作答。搜索结果是待比较的候选，不要为每个候选机械展开；"
            "仅在上下文确实不足时选择最有希望的代表片段扩展。"
        )

    @agent.instructions
    def pending_save_instruction(ctx: RunContext[AgentDeps]) -> str:
        """Inject only the current run's server-verified confirmation state."""

        if ctx.deps.reference_scope or ctx.deps.semantic_url_question:
            return ""
        snapshot = ctx.deps.actions.pending_save_snapshot()
        if not snapshot.active:
            return ""
        return (
            "可信服务器状态：当前 conversation 有 "
            f"{snapshot.count} 个视频等待保存确认。\n"
            "把本条短回复作为确认语义判断：明确肯定（包括“需要”）调用 "
            "confirm_video_save；明确否定调用 cancel_video_save；含糊则调用 "
            "clarify_save_confirmation；"
            "明显无关的新问题按正常知识流程处理并保留待确认状态。"
            "不要为确认回复调用知识检索。"
        )

    @agent.instructions
    def pending_delete_instruction(ctx: RunContext[AgentDeps]) -> str:
        """Expose only count/kind for a trusted pending delete action."""

        if ctx.deps.reference_scope or ctx.deps.semantic_url_question:
            return ""
        snapshot = ctx.deps.actions.pending_delete_snapshot()
        if snapshot is None or not snapshot.active:
            return ""
        return (
            "可信服务器状态：当前 conversation 有 "
            f"{snapshot.count} 个条目等待删除确认。明确肯定调用 confirm_item_deletion；"
            "明确否定调用 cancel_item_deletion；含糊调用 clarify_item_deletion。"
            "不要从模型历史重建删除目标。"
            + "当前用户消息必须包含服务端显示的确认码，否则请先重新发起删除，"
            "不要猜测、复用旧确认或仅回复‘是/确认’。"
        )

    register_retrieval_tools(agent, policy)
    register_action_tools(agent, policy)
    return agent


__all__ = ["BOUNDED_AUTONOMY_INSTRUCTIONS", "build_agent"]
