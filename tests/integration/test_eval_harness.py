"""The evaluation harness run against the deterministic sandbox model.

This doubles as a regression gate: if a change degrades the sandbox's
classification below the floor, CI fails.
"""

from __future__ import annotations

import json

import pytest
from app.adapters.llm.sandbox import SandboxLlm
from evals.harness import build_eval_context, default_cases, run_cases, summarize, to_dict

pytestmark = pytest.mark.integration


async def test_sandbox_eval_meets_quality_floor() -> None:
    ctx = build_eval_context(SandboxLlm())
    summary = summarize(await run_cases(ctx, default_cases()), model="sandbox")

    assert summary.classification.n == 24
    assert summary.classification.accuracy >= 0.90
    assert summary.classification.macro_f1 >= 0.85
    assert summary.extraction.email_accuracy == pytest.approx(1.0)
    # A useful confidence signal separates right from wrong answers.
    assert summary.mean_confidence_correct > summary.mean_confidence_incorrect
    # The output guardrail catches every known-bad draft (ADR-0018).
    assert summary.guardrail_catch_rate == pytest.approx(1.0)


async def test_eval_summary_is_json_serializable() -> None:
    ctx = build_eval_context(SandboxLlm())
    summary = summarize(await run_cases(ctx, default_cases()), model="sandbox")
    payload = to_dict(summary)
    json.dumps(payload)  # must not raise
    assert payload["n"] == 24
    assert set(payload["per_class"]) == {
        "account",
        "billing",
        "complaint",
        "other",
        "sales",
        "technical_support",
    }
