"""Unit tests for the real adapters.

We drive the real HTTP/SMTP/Qdrant code paths with in-memory transports and
fakes — no network — focusing on the behavior that matters: happy path, the
error-translation contract (transient vs permanent), and idempotency. This keeps
the production adapters honest, not just the sandboxes.
"""

from __future__ import annotations

import httpx
import pytest
from app.adapters.base import (
    EmailMessage,
    KnowledgeDoc,
    NotifyMessage,
    PermanentAdapterError,
    TicketRequest,
    TransientAdapterError,
)
from app.adapters.email.smtp import SmtpEmail
from app.adapters.jira.rest import JiraRestTickets
from app.adapters.knowledge.qdrant_store import QdrantKnowledge
from app.adapters.llm.openai_compatible import OpenAICompatibleLlm
from app.adapters.slack.webhook import SlackWebhookNotifier
from qdrant_client.http.exceptions import UnexpectedResponse

from tests.unit.fakes import StubLlm


def _llm(handler: httpx.MockTransport) -> OpenAICompatibleLlm:
    client = httpx.AsyncClient(transport=handler, base_url="http://llm/v1")
    return OpenAICompatibleLlm(
        base_url="http://llm/v1",
        api_key="k",
        chat_model="m",
        embed_model="e",
        timeout_seconds=5,
        client=client,
    )


# --------------------------------------------------------------------------- #
# OpenAI-compatible LLM
# --------------------------------------------------------------------------- #
async def test_llm_complete_and_embed_happy_path() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/chat/completions"):
            return httpx.Response(200, json={"choices": [{"message": {"content": " hi "}}]})
        return httpx.Response(200, json={"data": [{"index": 0, "embedding": [0.1, 0.2, 0.3]}]})

    llm = _llm(httpx.MockTransport(handler))
    assert await llm.complete(system="s", user="u") == "hi"
    assert await llm.embed(["x"]) == [[0.1, 0.2, 0.3]]
    assert await llm.embed([]) == []


async def test_llm_5xx_is_transient() -> None:
    llm = _llm(httpx.MockTransport(lambda r: httpx.Response(500, text="boom")))
    with pytest.raises(TransientAdapterError):
        await llm.complete(system="s", user="u")


async def test_llm_4xx_is_permanent() -> None:
    llm = _llm(httpx.MockTransport(lambda r: httpx.Response(400, text="bad")))
    with pytest.raises(PermanentAdapterError):
        await llm.complete(system="s", user="u")


async def test_llm_malformed_response_is_permanent() -> None:
    llm = _llm(httpx.MockTransport(lambda r: httpx.Response(200, json={})))
    with pytest.raises(PermanentAdapterError):
        await llm.complete(system="s", user="u")


async def test_llm_timeout_is_transient() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("slow", request=request)

    llm = _llm(httpx.MockTransport(handler))
    with pytest.raises(TransientAdapterError):
        await llm.embed(["x"])


# --------------------------------------------------------------------------- #
# Jira
# --------------------------------------------------------------------------- #
def _jira(handler: httpx.MockTransport) -> JiraRestTickets:
    client = httpx.AsyncClient(transport=handler, base_url="https://jira.example")
    return JiraRestTickets(
        base_url="https://jira.example",
        email="e@x.io",
        api_token="t",
        project_key="OPS",
        client=client,
    )


async def test_jira_creates_new_ticket() -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(f"{request.method} {request.url.path}")
        if request.url.path.endswith("/search"):
            return httpx.Response(200, json={"issues": []})
        return httpx.Response(201, json={"key": "OPS-7"})

    ref = await _jira(httpx.MockTransport(handler)).create_ticket(
        TicketRequest(summary="s", description="d"), idempotency_key="req-1"
    )
    assert ref.key == "OPS-7"
    assert ref.url.endswith("/browse/OPS-7")
    assert any("/issue" in c for c in calls)


async def test_jira_reuses_existing_ticket_for_idempotency() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/search"):
            return httpx.Response(200, json={"issues": [{"key": "OPS-5"}]})
        raise AssertionError("must not create when an idempotent match exists")

    ref = await _jira(httpx.MockTransport(handler)).create_ticket(
        TicketRequest(summary="s", description="d"), idempotency_key="req-1"
    )
    assert ref.key == "OPS-5"


async def test_jira_5xx_is_transient() -> None:
    jira = _jira(httpx.MockTransport(lambda r: httpx.Response(503, text="down")))
    with pytest.raises(TransientAdapterError):
        await jira.create_ticket(TicketRequest(summary="s", description="d"), idempotency_key="k")


# --------------------------------------------------------------------------- #
# Slack
# --------------------------------------------------------------------------- #
async def test_slack_posts_and_dedupes() -> None:
    calls: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        import json

        calls.append(json.loads(request.content))
        return httpx.Response(200, text="ok")

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    notifier = SlackWebhookNotifier(
        webhook_url="https://hooks.slack/x", default_channel="#ops", client=client
    )
    await notifier.notify(NotifyMessage(text="hello"), idempotency_key="n1")
    await notifier.notify(NotifyMessage(text="hello"), idempotency_key="n1")  # deduped
    assert len(calls) == 1
    assert calls[0]["text"] == "hello"


def test_slack_requires_webhook_url() -> None:
    with pytest.raises(PermanentAdapterError):
        SlackWebhookNotifier(webhook_url="", default_channel="#ops")


async def test_slack_4xx_is_permanent() -> None:
    client = httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(403)))
    notifier = SlackWebhookNotifier(webhook_url="https://h/x", default_channel="#c", client=client)
    with pytest.raises(PermanentAdapterError):
        await notifier.notify(NotifyMessage(text="hi"), idempotency_key="n2")


# --------------------------------------------------------------------------- #
# SMTP email
# --------------------------------------------------------------------------- #
class _FakeSMTP:
    instances: list[_FakeSMTP] = []

    def __init__(self, host: str, port: int, timeout: int) -> None:
        self.sent: list[object] = []
        _FakeSMTP.instances.append(self)

    def __enter__(self) -> _FakeSMTP:
        return self

    def __exit__(self, *exc: object) -> bool:
        return False

    def starttls(self) -> None:
        return None

    def login(self, username: str, password: str) -> None:
        return None

    def send_message(self, message: object) -> None:
        self.sent.append(message)


async def test_smtp_sends_and_returns_receipt(monkeypatch: pytest.MonkeyPatch) -> None:
    _FakeSMTP.instances.clear()
    monkeypatch.setattr("app.adapters.email.smtp.smtplib.SMTP", _FakeSMTP)
    adapter = SmtpEmail(host="smtp", port=587, username="u", password="p", sender="ops@x.io")

    receipt = await adapter.send(
        EmailMessage(to="a@b.com", subject="Hi", body="body"), idempotency_key="e1"
    )
    assert receipt.message_id.endswith("@ops-agent>")
    assert _FakeSMTP.instances[0].sent, "message should have been handed to SMTP"


def test_smtp_requires_host() -> None:
    with pytest.raises(PermanentAdapterError):
        SmtpEmail(host="", port=587, username="", password="", sender="ops@x.io")


async def test_smtp_connect_error_is_transient(monkeypatch: pytest.MonkeyPatch) -> None:
    import smtplib

    def boom(*args: object, **kwargs: object) -> None:
        raise smtplib.SMTPConnectError(421, "unavailable")

    monkeypatch.setattr("app.adapters.email.smtp.smtplib.SMTP", boom)
    adapter = SmtpEmail(host="smtp", port=587, username="", password="", sender="ops@x.io")
    with pytest.raises(TransientAdapterError):
        await adapter.send(EmailMessage(to="a@b.com", subject="s", body="b"), idempotency_key="e")


# --------------------------------------------------------------------------- #
# Qdrant knowledge
# --------------------------------------------------------------------------- #
class _FakePoint:
    def __init__(self, doc_id: str, text: str, score: float) -> None:
        self.id = 1
        self.score = score
        self.payload = {"doc_id": doc_id, "text": text, "source": "kb"}


class _FakeQdrant:
    def __init__(self, *, exists: bool = False) -> None:
        self._exists = exists
        self.created = False
        self.upserted: list[object] = []

    async def collection_exists(self, name: str) -> bool:
        return self._exists

    async def create_collection(self, collection_name: str, vectors_config: object) -> None:
        self.created = True

    async def upsert(self, collection_name: str, points: list) -> None:
        self.upserted = points

    async def search(self, **kwargs: object) -> list[_FakePoint]:
        return [_FakePoint("kb-1", "refund policy", 0.87)]


def _qdrant(client: object) -> QdrantKnowledge:
    return QdrantKnowledge(
        llm=StubLlm(reply="", embed_dim=4),
        url="http://qdrant",
        collection="c",
        vector_size=4,
        client=client,  # type: ignore[arg-type]
    )


async def test_qdrant_ensure_ready_creates_missing_collection() -> None:
    fake = _FakeQdrant(exists=False)
    await _qdrant(fake).ensure_ready()
    assert fake.created is True


async def test_qdrant_upsert_and_search() -> None:
    fake = _FakeQdrant(exists=True)
    kb = _qdrant(fake)
    count = await kb.upsert([KnowledgeDoc(id="kb-1", text="refund policy")])
    assert count == 1 and fake.upserted

    hits = await kb.search("refund")
    assert hits[0].id == "kb-1"
    assert 0.0 <= hits[0].score <= 1.0


async def test_qdrant_5xx_is_transient() -> None:
    class Failing(_FakeQdrant):
        async def search(self, **kwargs: object) -> list[_FakePoint]:
            raise UnexpectedResponse(
                status_code=500, reason_phrase="err", content=b"", headers=None
            )

    with pytest.raises(TransientAdapterError):
        await _qdrant(Failing(exists=True)).search("q")
