"""Live smoke test against a real OpenAI-compatible LLM (e.g. Ollama).

Marked ``smoke`` and self-skipping: if no endpoint is reachable at
``LLM_BASE_URL`` it skips rather than fails, so the default suite (and CI) stay
hermetic. To run it for real:

    ollama serve && ollama pull llama3.1 && ollama pull nomic-embed-text
    pytest -m smoke

It exercises the actual HTTP adapter, JSON-mode structured output, embeddings,
and the real classify node end-to-end.
"""

from __future__ import annotations

import os

import httpx
import pytest
from app.adapters.email.sandbox import SandboxEmail
from app.adapters.jira.sandbox import SandboxTickets
from app.adapters.knowledge.sandbox import SandboxKnowledge
from app.adapters.llm.openai_compatible import OpenAICompatibleLlm
from app.adapters.slack.sandbox import SandboxNotifier
from app.domain.enums import RequestType
from app.domain.state import AgentState
from app.graph.context import NodeConfig, NodeContext
from app.graph.nodes import classify_node

pytestmark = pytest.mark.smoke

_BASE_URL = os.getenv("LLM_BASE_URL", "http://localhost:11434/v1")


async def _reachable() -> bool:
    root = _BASE_URL.rsplit("/v1", 1)[0]
    try:
        async with httpx.AsyncClient(timeout=2.0) as client:
            response = await client.get(root)
            return response.status_code < 500
    except httpx.HTTPError:
        return False


def _real_llm() -> OpenAICompatibleLlm:
    return OpenAICompatibleLlm(
        base_url=_BASE_URL,
        api_key=os.getenv("LLM_API_KEY", "ollama"),
        chat_model=os.getenv("LLM_CHAT_MODEL", "llama3.1"),
        embed_model=os.getenv("LLM_EMBED_MODEL", "nomic-embed-text"),
        timeout_seconds=60.0,
    )


async def test_real_llm_classifies_a_clear_request() -> None:
    if not await _reachable():
        pytest.skip(f"no OpenAI-compatible LLM reachable at {_BASE_URL}")

    ctx = NodeContext(
        llm=_real_llm(),
        knowledge=SandboxKnowledge(),
        tickets=SandboxTickets(),
        email=SandboxEmail(),
        notifier=SandboxNotifier(),
        config=NodeConfig(),
    )
    state = AgentState(
        request_id="smoke-1",
        channel="email",
        raw_subject="Refund",
        raw_body="I need a refund for my invoice, I was double charged this month.",
    )

    classification = (await classify_node(state, ctx))["classification"]

    # JSON mode parsed successfully (the fallback path yields confidence 0.0).
    assert classification.confidence > 0.0
    assert classification.request_type is RequestType.BILLING


async def test_real_llm_embeddings_have_dimension() -> None:
    if not await _reachable():
        pytest.skip(f"no OpenAI-compatible LLM reachable at {_BASE_URL}")

    vectors = await _real_llm().embed(["hello world"])
    assert len(vectors) == 1
    assert len(vectors[0]) > 0
