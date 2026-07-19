"""Assemble and run the LangGraph pipeline.

``build_graph`` wires the pure node functions into an explicit ``StateGraph``,
binding each to the injected :class:`NodeContext`. The structure here is the
source of truth for the Mermaid diagram in the README. Two entry points are
exposed: ``run_pipeline`` (whole-graph invoke, used by tests and the inline API
path) and ``stream_pipeline`` (per-node updates, used by the worker to checkpoint).
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any

from langgraph.graph import END, START, StateGraph

from app.domain.state import AgentState
from app.graph import nodes
from app.graph.context import NodeContext

# Ordered node names, also used by the worker and tests.
# Node names must not collide with AgentState field names (LangGraph reserves
# state keys as channels), so the reply/report nodes are named distinctly from
# the `reply`/`report` fields they populate.
NODE_CLASSIFY = "classify"
NODE_EXTRACT = "extract"
NODE_RETRIEVE = "retrieve"
NODE_CREATE_TICKET = "create_ticket"
NODE_REPLY = "draft_reply"
NODE_NOTIFY = "notify"
NODE_PERSIST = "persist"
NODE_REPORT = "generate_report"
NODE_NEEDS_REVIEW = "needs_review"


NodeFn = Callable[[AgentState, NodeContext], Awaitable[dict[str, Any]]]


def _bind(fn: NodeFn, ctx: NodeContext) -> Callable[[AgentState], Awaitable[dict[str, Any]]]:
    async def run(state: AgentState) -> dict[str, Any]:
        return await fn(state, ctx)

    return run


def build_graph(ctx: NodeContext) -> Any:
    """Construct and compile the pipeline graph for the given context."""

    builder: StateGraph = StateGraph(AgentState)

    builder.add_node(NODE_CLASSIFY, _bind(nodes.classify_node, ctx))
    builder.add_node(NODE_EXTRACT, _bind(nodes.extract_node, ctx))
    builder.add_node(NODE_RETRIEVE, _bind(nodes.retrieve_node, ctx))
    builder.add_node(NODE_CREATE_TICKET, _bind(nodes.create_ticket_node, ctx))
    builder.add_node(NODE_REPLY, _bind(nodes.reply_node, ctx))
    builder.add_node(NODE_NOTIFY, _bind(nodes.notify_node, ctx))
    builder.add_node(NODE_PERSIST, _bind(nodes.persist_node, ctx))
    builder.add_node(NODE_REPORT, _bind(nodes.report_node, ctx))
    builder.add_node(NODE_NEEDS_REVIEW, _bind(nodes.needs_review_node, ctx))

    builder.add_edge(START, NODE_CLASSIFY)
    builder.add_conditional_edges(
        NODE_CLASSIFY,
        nodes.make_route_after_classify(ctx.config.confidence_threshold),
        {NODE_EXTRACT: NODE_EXTRACT, NODE_NEEDS_REVIEW: NODE_NEEDS_REVIEW},
    )
    builder.add_edge(NODE_EXTRACT, NODE_RETRIEVE)
    builder.add_edge(NODE_RETRIEVE, NODE_CREATE_TICKET)
    builder.add_edge(NODE_CREATE_TICKET, NODE_REPLY)
    builder.add_edge(NODE_REPLY, NODE_NOTIFY)
    builder.add_edge(NODE_NOTIFY, NODE_PERSIST)
    builder.add_edge(NODE_PERSIST, NODE_REPORT)
    builder.add_edge(NODE_REPORT, END)
    builder.add_edge(NODE_NEEDS_REVIEW, END)

    return builder.compile()


async def run_pipeline(ctx: NodeContext, state: AgentState) -> AgentState:
    """Run the whole pipeline and return the final, validated state."""

    graph = build_graph(ctx)
    result = await graph.ainvoke(state)
    # LangGraph returns the merged state (dict or model depending on version);
    # normalize back into our typed model either way.
    return AgentState.model_validate(result)


async def stream_pipeline(
    ctx: NodeContext, state: AgentState
) -> AsyncIterator[tuple[str, dict[str, Any]]]:
    """Yield ``(node_name, delta)`` after each node executes.

    The worker consumes this to checkpoint progress into ``run_step`` as the
    pipeline advances.
    """

    graph = build_graph(ctx)
    async for update in graph.astream(state, stream_mode="updates"):
        # astream(updates) yields {node_name: delta_dict} per completed node.
        for node_name, delta in update.items():
            yield node_name, delta
