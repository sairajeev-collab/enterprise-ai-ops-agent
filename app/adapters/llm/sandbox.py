"""Deterministic sandbox LLM.

Lets the entire pipeline run with no model server, in CI and offline dev. It is
not a stub that returns a constant: it produces *plausible, structured* output by
reading a ``TASK:`` marker that the graph nodes place at the top of every system
prompt, then applying small keyword heuristics to the user content. Real models
simply treat that marker as an ordinary instruction, so the seam stays honest.

Determinism matters: given the same input, the sandbox returns the same output,
which is what makes end-to-end assertions possible.
"""

from __future__ import annotations

import hashlib
import json
import re

from app.adapters.base import LlmPort

_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
_TASK_RE = re.compile(r"TASK:\s*(\w+)", re.IGNORECASE)

# Keyword -> request_type. First match wins, so order encodes precedence.
_TYPE_KEYWORDS: list[tuple[str, str]] = [
    ("refund", "billing"),
    ("invoice", "billing"),
    ("charge", "billing"),
    ("billing", "billing"),
    ("payment", "billing"),
    ("password", "account"),
    ("login", "account"),
    ("account", "account"),
    ("error", "technical_support"),
    ("bug", "technical_support"),
    ("broken", "technical_support"),
    ("not working", "technical_support"),
    ("crash", "technical_support"),
    ("pricing", "sales"),
    ("quote", "sales"),
    ("demo", "sales"),
    ("purchase", "sales"),
    ("complaint", "complaint"),
    ("terrible", "complaint"),
    ("unacceptable", "complaint"),
]

_URGENT_WORDS = ("urgent", "asap", "immediately", "critical", "outage")
_HIGH_WORDS = ("important", "soon", "priority", "blocked")


class SandboxLlm(LlmPort):
    async def complete(
        self, *, system: str, user: str, temperature: float = 0.0, json_mode: bool = False
    ) -> str:
        task = self._task_of(system)
        if task == "classify":
            return json.dumps(self._classify(user))
        if task == "extract":
            return json.dumps(self._extract(user))
        if task == "draft_reply":
            return self._draft_reply(user)
        if task == "report":
            return self._report(user)
        # Unknown task: echo a short deterministic acknowledgement.
        return "acknowledged"

    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [self._pseudo_vector(text) for text in texts]

    # -- task implementations ------------------------------------------------ #
    @staticmethod
    def _task_of(system: str) -> str:
        match = _TASK_RE.search(system)
        return match.group(1).lower() if match else ""

    @staticmethod
    def _classify(text: str) -> dict[str, object]:
        lowered = text.lower()
        request_type = "other"
        matched = False
        for keyword, rtype in _TYPE_KEYWORDS:
            if keyword in lowered:
                request_type, matched = rtype, True
                break

        if any(word in lowered for word in _URGENT_WORDS):
            priority = "urgent"
        elif any(word in lowered for word in _HIGH_WORDS):
            priority = "high"
        else:
            priority = "medium"

        # Low confidence on unrecognized content routes to human review.
        confidence = 0.92 if matched else 0.35
        return {
            "request_type": request_type,
            "priority": priority,
            "confidence": confidence,
            "reason": (
                f"matched keyword for {request_type}" if matched else "no known keyword matched"
            ),
        }

    @staticmethod
    def _extract(text: str) -> dict[str, object]:
        email_match = _EMAIL_RE.search(text)
        email = email_match.group(0) if email_match else ""

        name = ""
        name_match = re.search(r"(?:from|regards,|thanks,|—)\s+([A-Z][a-z]+ [A-Z][a-z]+)", text)
        if name_match:
            name = name_match.group(1)

        first_line = next((ln.strip() for ln in text.splitlines() if ln.strip()), "")
        return {
            "customer_name": name,
            "customer_email": email,
            "subject": first_line[:120],
            "summary": (text.strip()[:280]),
            "entities": {"has_email": bool(email)},
        }

    @staticmethod
    def _draft_reply(user: str) -> str:
        name_match = re.search(r"customer_name:\s*(.+)", user)
        name = name_match.group(1).strip() if name_match else "there"
        name = name or "there"
        return (
            f"Hi {name},\n\n"
            "Thanks for reaching out. We've logged your request and our team is on it. "
            "You'll receive an update as soon as we have news.\n\n"
            "Best regards,\nCustomer Operations"
        )

    @staticmethod
    def _report(user: str) -> str:
        return (
            "## Operations Summary\n\n"
            "A new request was processed by the automated pipeline. "
            "See the structured fields below for classification, actions taken, "
            "and links to the created ticket.\n\n"
            f"{user.strip()[:400]}"
        )

    @staticmethod
    def _pseudo_vector(text: str, dim: int = 16) -> list[float]:
        # Deterministic, content-derived vector. Not semantically meaningful, but
        # stable and unit-testable; sandbox knowledge retrieval does not rely on it.
        digest = hashlib.sha256(text.encode("utf-8")).digest()
        return [digest[i % len(digest)] / 255.0 for i in range(dim)]
