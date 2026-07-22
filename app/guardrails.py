"""Output guardrails for customer-facing text.

The single highest-blast-radius output in this system is the reply the ``reply``
node drafts with an LLM and then *emails a real customer*. Everything else lands
in a Jira ticket or a Slack channel a human reads; the email leaves the building.
So before it sends, the draft passes a deterministic gate here (ADR-0018).

These checks are intentionally boring and rule-based. Regex and length, no model
judging a model. A guardrail you can't reason about isn't a guardrail. The gate is
conservative: on any violation the worker holds the email and flags the run for a
human instead of sending. False positives cost a human glance; a false negative
emails a hallucinated refund promise or another customer's address to the wrong
person, so we bias toward holding.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# Loose but serviceable email matcher. We only need to notice that the draft
# *contains an address*, not to validate deliverability.
_EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}")

# Phrases that should never appear in a sent reply. Two failure modes:
#  - the model echoed its own scaffolding ("TASK:", "system prompt"), meaning the
#    prompt leaked into the output;
#  - the model was steered by an injection in the customer message and narrated it
#    ("ignore previous instructions", "as an AI language model").
_LEAK_MARKERS = (
    "task:",
    "system prompt",
    "ignore previous",
    "ignore all previous",
    "as an ai language model",
    "as an ai model",
    "i cannot fulfill",
)

# A drafted reply shorter than this is almost certainly a degenerate generation
# (empty, "OK", a stray token) rather than a real acknowledgement.
_MIN_LENGTH = 20
# ... and one longer than this is a runaway. Real replies here are a few short
# paragraphs; anything past this is a symptom, not a message.
_MAX_LENGTH = 4000


@dataclass(frozen=True)
class GuardrailResult:
    """Outcome of gating one piece of customer-facing text."""

    ok: bool
    violations: list[str] = field(default_factory=list)

    @property
    def reason(self) -> str:
        return "; ".join(self.violations)


def check_reply(body: str, *, customer_email: str) -> GuardrailResult:
    """Gate an LLM-drafted reply before it is emailed to a customer.

    ``customer_email`` is the address we intend to send to; any *other* address
    appearing in the body is treated as a leak (wrong recipient or another
    customer's data). Passing ``""`` means we don't know the recipient, so any
    address in the body is suspect.
    """

    violations: list[str] = []
    text = body.strip()

    if len(text) < _MIN_LENGTH:
        violations.append(f"reply too short ({len(text)} chars)")
    if len(text) > _MAX_LENGTH:
        violations.append(f"reply too long ({len(text)} chars)")

    lowered = text.lower()
    for marker in _LEAK_MARKERS:
        if marker in lowered:
            violations.append(f"contains disallowed phrase: {marker!r}")

    expected = customer_email.strip().lower()
    foreign = sorted({addr for addr in _EMAIL_RE.findall(text) if addr.lower() != expected})
    if foreign:
        # Truncate the reported set so a spammy body can't blow up the log line.
        shown = ", ".join(foreign[:3])
        violations.append(f"leaks foreign email address(es): {shown}")

    return GuardrailResult(ok=not violations, violations=violations)
