"""Real LLM adapter for any OpenAI-compatible endpoint.

Targets the OpenAI Chat Completions and Embeddings API shapes, which are
implemented by OpenAI itself, Ollama (``/v1``), vLLM, Together, and others. The
default configuration points at a local Ollama server so the system runs with no
paid account (see ADR-0005).

We own a single shared :class:`httpx.AsyncClient` for connection pooling and
translate transport/HTTP failures into the adapter exception contract so the
retry layer can act on them.
"""

from __future__ import annotations

from typing import Any

import httpx

from app.adapters.base import (
    LlmPort,
    PermanentAdapterError,
    TransientAdapterError,
)
from app.logging import get_logger

logger = get_logger(__name__)


class OpenAICompatibleLlm(LlmPort):
    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        chat_model: str,
        embed_model: str,
        timeout_seconds: float,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._chat_model = chat_model
        self._embed_model = embed_model
        # An injected client is used by tests (httpx MockTransport); production
        # builds its own pooled client.
        self._client = client or httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=timeout_seconds,
        )

    async def complete(
        self, *, system: str, user: str, temperature: float = 0.0, json_mode: bool = False
    ) -> str:
        payload: dict[str, Any] = {
            "model": self._chat_model,
            "temperature": temperature,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }
        if json_mode:
            # Supported by OpenAI, Ollama (>=0.5), vLLM, and most compatible servers.
            payload["response_format"] = {"type": "json_object"}
        data = await self._post("/chat/completions", payload)
        try:
            return str(data["choices"][0]["message"]["content"]).strip()
        except (KeyError, IndexError, TypeError) as exc:
            raise PermanentAdapterError(
                "Malformed chat completion response", code="llm_bad_response"
            ) from exc

    async def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        payload: dict[str, Any] = {"model": self._embed_model, "input": texts}
        data = await self._post("/embeddings", payload)
        try:
            items = sorted(data["data"], key=lambda row: row["index"])
            return [list(map(float, row["embedding"])) for row in items]
        except (KeyError, TypeError, ValueError) as exc:
            raise PermanentAdapterError(
                "Malformed embeddings response", code="llm_bad_response"
            ) from exc

    async def _post(self, path: str, payload: dict[str, Any]) -> Any:
        try:
            response = await self._client.post(path, json=payload)
        except httpx.TimeoutException as exc:
            raise TransientAdapterError("LLM request timed out", code="llm_timeout") from exc
        except httpx.TransportError as exc:
            raise TransientAdapterError("LLM transport error", code="llm_transport") from exc

        if response.status_code >= 500 or response.status_code == 429:
            raise TransientAdapterError(
                f"LLM upstream error: {response.status_code}", code="llm_upstream"
            )
        if response.status_code >= 400:
            raise PermanentAdapterError(
                f"LLM rejected request: {response.status_code} {response.text[:200]}",
                code="llm_client_error",
            )

        return response.json()

    async def aclose(self) -> None:
        await self._client.aclose()
