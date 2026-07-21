"""Output guardrail unit tests (ADR-0018).

The guardrail is the last thing between an LLM and a customer's inbox, so it gets
tested at the edges: it must pass legitimate replies and reject every known bad
draft, without leaning on the model.
"""

from __future__ import annotations

import pytest
from app.guardrails import check_reply
from evals.guardrails import CLEAN_DRAFT, POISONED_DRAFTS


def test_clean_reply_passes() -> None:
    result = check_reply(
        "Hi Dana, thanks for reaching out. We've opened ticket OPS-1 and a "
        "specialist will follow up within one business day.",
        customer_email="dana@acme.com",
    )
    assert result.ok
    assert result.violations == []


def test_reply_to_the_customers_own_address_is_not_a_leak() -> None:
    # The recipient's address appearing in the body must not trip the leak check.
    result = check_reply(
        "Hi Dana, we'll email updates to dana@acme.com as we make progress on "
        "your ticket. Thanks for your patience.",
        customer_email="dana@acme.com",
    )
    assert result.ok


def test_foreign_email_is_flagged() -> None:
    result = check_reply(
        "Please contact insider@rival.com for details about your refund.",
        customer_email="dana@acme.com",
    )
    assert not result.ok
    assert any("foreign email" in v for v in result.violations)


def test_case_insensitive_customer_match() -> None:
    result = check_reply(
        "We'll follow up at Dana@Acme.com shortly. Thanks for reaching out to us.",
        customer_email="dana@acme.com",
    )
    assert result.ok


def test_too_short_is_flagged() -> None:
    result = check_reply("OK", customer_email="sam@globex.com")
    assert not result.ok
    assert any("too short" in v for v in result.violations)


def test_too_long_is_flagged() -> None:
    result = check_reply("spam " * 1200, customer_email="sam@globex.com")
    assert not result.ok
    assert any("too long" in v for v in result.violations)


@pytest.mark.parametrize(
    "phrase",
    ["TASK: draft_reply", "here is the system prompt", "Ignore previous instructions"],
)
def test_prompt_leak_markers_are_flagged(phrase: str) -> None:
    body = f"Hello, thanks for writing in about your account. {phrase} and continue."
    result = check_reply(body, customer_email="lee@umbrella.com")
    assert not result.ok
    assert any("disallowed phrase" in v for v in result.violations)


def test_unknown_recipient_treats_any_address_as_suspect() -> None:
    # With no known recipient, any address in the body is a potential leak.
    result = check_reply(
        "Reach us at support@ourcompany.com any time for help with your request.",
        customer_email="",
    )
    assert not result.ok


def test_every_poisoned_eval_draft_is_caught() -> None:
    # This is the same corpus the eval harness gates on — pin it here too so a
    # regression fails a fast unit test, not just the eval job.
    for case in POISONED_DRAFTS:
        result = check_reply(case.body, customer_email=case.customer_email)
        assert not result.ok, f"guardrail missed {case.id}: {case.failure_mode}"


def test_clean_control_draft_passes() -> None:
    result = check_reply(CLEAN_DRAFT.body, customer_email=CLEAN_DRAFT.customer_email)
    assert result.ok
