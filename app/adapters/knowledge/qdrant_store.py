"""Real knowledge adapter backed by Qdrant.

Embeddings are produced by the injected :class:`LlmPort` so the vector space is
consistent between indexing and querying. Collection creation is idempotent
(``ensure_ready``), which keeps startup and the seed script safe to run repeatedly.
"""

from __future__ import annotations

from qdrant_client import AsyncQdrantClient, models
from qdrant_client.http.exceptions import ResponseHandlingException, UnexpectedResponse

from app.adapters.base import (
    KnowledgeDoc,
    KnowledgeHit,
    KnowledgePort,
    LlmPort,
    PermanentAdapterError,
    TransientAdapterError,
)
from app.logging import get_logger

logger = get_logger(__name__)


class QdrantKnowledge(KnowledgePort):
    def __init__(
        self,
        *,
        llm: LlmPort,
        url: str,
        collection: str,
        vector_size: int,
        client: AsyncQdrantClient | None = None,
    ) -> None:
        self._llm = llm
        self._collection = collection
        self._vector_size = vector_size
        self._client = client or AsyncQdrantClient(url=url)

    async def ensure_ready(self) -> None:
        try:
            exists = await self._client.collection_exists(self._collection)
            if not exists:
                await self._client.create_collection(
                    collection_name=self._collection,
                    vectors_config=models.VectorParams(
                        size=self._vector_size, distance=models.Distance.COSINE
                    ),
                )
        except (UnexpectedResponse, ResponseHandlingException) as exc:
            raise self._translate(exc) from exc

    async def upsert(self, docs: list[KnowledgeDoc]) -> int:
        if not docs:
            return 0
        vectors = await self._llm.embed([doc.text for doc in docs])
        points = [
            models.PointStruct(
                id=self._point_id(doc.id),
                vector=vector,
                payload={"doc_id": doc.id, "text": doc.text, "source": doc.source, **doc.metadata},
            )
            for doc, vector in zip(docs, vectors, strict=True)
        ]
        try:
            await self._client.upsert(collection_name=self._collection, points=points)
        except (UnexpectedResponse, ResponseHandlingException) as exc:
            raise self._translate(exc) from exc
        return len(points)

    async def search(self, query: str, *, top_k: int = 5) -> list[KnowledgeHit]:
        vectors = await self._llm.embed([query])
        try:
            results = await self._client.search(
                collection_name=self._collection,
                query_vector=vectors[0],
                limit=top_k,
                with_payload=True,
            )
        except (UnexpectedResponse, ResponseHandlingException) as exc:
            raise self._translate(exc) from exc

        hits: list[KnowledgeHit] = []
        for point in results:
            payload = point.payload or {}
            hits.append(
                KnowledgeHit(
                    id=str(payload.get("doc_id", point.id)),
                    text=str(payload.get("text", "")),
                    score=float(point.score),
                    source=str(payload.get("source", "")),
                )
            )
        return hits

    @staticmethod
    def _point_id(doc_id: str) -> int:
        # Qdrant point ids must be int or UUID; hash the string id stably so
        # re-upserting the same doc_id overwrites rather than duplicates.
        import hashlib

        return int(hashlib.sha256(doc_id.encode("utf-8")).hexdigest()[:15], 16)

    @staticmethod
    def _translate(exc: Exception) -> Exception:
        status = getattr(exc, "status_code", None)
        if isinstance(exc, ResponseHandlingException):
            return TransientAdapterError("Qdrant unreachable", code="qdrant_transport")
        if isinstance(status, int) and (status >= 500 or status == 429):
            return TransientAdapterError(f"Qdrant upstream {status}", code="qdrant_upstream")
        return PermanentAdapterError(f"Qdrant error: {exc}", code="qdrant_client_error")
