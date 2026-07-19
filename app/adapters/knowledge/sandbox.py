"""In-memory sandbox knowledge base.

Deterministic keyword-overlap retrieval over an in-process document list. Ships
with a small, real seed corpus so the ``retrieve`` node returns useful grounding
even before anyone runs the seed script. No network, no embeddings — ideal for
CI and offline development.
"""

from __future__ import annotations

import re

from app.adapters.base import KnowledgeDoc, KnowledgeHit, KnowledgePort

_TOKEN_RE = re.compile(r"[a-z0-9]+")

# A minimal but genuine internal knowledge base. Content mirrors what a support
# ops team would actually document.
SEED_DOCS: list[KnowledgeDoc] = [
    KnowledgeDoc(
        id="kb-refund-policy",
        text=(
            "Refund policy: customers may request a full refund within 30 days of purchase. "
            "Refunds are processed to the original payment method within 5-7 business days. "
            "For billing disputes, create a ticket tagged 'billing' and notify the finance channel."
        ),
        source="handbook/billing.md",
    ),
    KnowledgeDoc(
        id="kb-password-reset",
        text=(
            "Account access: if a customer cannot log in, direct them to the self-serve password "
            "reset link. If SSO is enabled for their organization, password reset must be done by "
            "their identity provider. Never reset passwords manually."
        ),
        source="handbook/accounts.md",
    ),
    KnowledgeDoc(
        id="kb-outage-runbook",
        text=(
            "Technical outages: for reports of the product not working or errors, check the status "
            "page first. If an incident is open, reply with the incident link and set priority high. "
            "Escalate urgent outages to on-call engineering immediately."
        ),
        source="runbooks/incidents.md",
    ),
    KnowledgeDoc(
        id="kb-sales-handoff",
        text=(
            "Sales inquiries: pricing, quote, and demo requests should be routed to the sales team. "
            "Capture company size and use case, then create a ticket in the SALES project."
        ),
        source="handbook/sales.md",
    ),
]


class SandboxKnowledge(KnowledgePort):
    def __init__(self, *, seed: bool = True) -> None:
        self._docs: dict[str, KnowledgeDoc] = {}
        if seed:
            for doc in SEED_DOCS:
                self._docs[doc.id] = doc

    async def ensure_ready(self) -> None:  # nothing to provision in-memory
        return None

    async def upsert(self, docs: list[KnowledgeDoc]) -> int:
        for doc in docs:
            self._docs[doc.id] = doc
        return len(docs)

    async def search(self, query: str, *, top_k: int = 5) -> list[KnowledgeHit]:
        query_tokens = self._tokenize(query)
        if not query_tokens:
            return []

        scored: list[KnowledgeHit] = []
        for doc in self._docs.values():
            doc_tokens = self._tokenize(doc.text)
            overlap = query_tokens & doc_tokens
            if not overlap:
                continue
            # Jaccard similarity keeps scores in [0, 1], matching the real adapter.
            score = len(overlap) / len(query_tokens | doc_tokens)
            scored.append(
                KnowledgeHit(id=doc.id, text=doc.text, score=round(score, 4), source=doc.source)
            )

        scored.sort(key=lambda hit: (hit.score, hit.id), reverse=True)
        return scored[:top_k]

    @staticmethod
    def _tokenize(text: str) -> set[str]:
        return set(_TOKEN_RE.findall(text.lower()))
