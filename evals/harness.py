"""Evaluation runner.

Drives the production ``classify`` and ``extract`` nodes over the dataset and
aggregates the results. The node context uses the configured LLM (sandbox or
real) plus sandbox stubs for the ports the two nodes don't touch, so evaluation
never opens a ticket or sends mail.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.adapters.base import LlmPort
from app.adapters.email.sandbox import SandboxEmail
from app.adapters.jira.sandbox import SandboxTickets
from app.adapters.knowledge.sandbox import SandboxKnowledge
from app.adapters.llm.openai_compatible import OpenAICompatibleLlm
from app.adapters.llm.sandbox import SandboxLlm
from app.adapters.slack.sandbox import SandboxNotifier
from app.config import IntegrationMode, Settings
from app.domain.enums import Channel
from app.domain.state import AgentState
from app.graph.context import NodeConfig, NodeContext
from app.graph.nodes import classify_node, extract_node
from app.guardrails import check_reply

from evals.dataset import CASES, EvalCase
from evals.guardrails import CLEAN_DRAFT, POISONED_DRAFTS
from evals.metrics import (
    ClassificationReport,
    ExtractionReport,
    accuracy,
    classification_report,
    extraction_report,
)


@dataclass(frozen=True)
class CaseResult:
    case: EvalCase
    predicted_type: str
    predicted_priority: str
    confidence: float
    predicted_email: str


@dataclass(frozen=True)
class EvalSummary:
    classification: ClassificationReport
    priority_accuracy: float
    extraction: ExtractionReport
    mean_confidence_correct: float
    mean_confidence_incorrect: float
    # Which model produced these numbers. Without this a report is unattributable —
    # "88% accuracy" is meaningless if you can't say 88% *of what model*.
    model: str
    guardrail_catch_rate: float
    guardrail_support: int


def model_label(settings: Settings) -> str:
    """A stable, human-readable name for the model under test, so every report is
    attributable to what actually produced it."""

    if settings.llm_mode is IntegrationMode.REAL:
        return settings.llm_chat_model
    return "sandbox (deterministic stub)"


def build_llm(settings: Settings) -> LlmPort:
    if settings.llm_mode is IntegrationMode.REAL:
        return OpenAICompatibleLlm(
            base_url=settings.llm_base_url,
            api_key=settings.llm_api_key,
            chat_model=settings.llm_chat_model,
            embed_model=settings.llm_embed_model,
            timeout_seconds=settings.llm_timeout_seconds,
        )
    return SandboxLlm()


def build_eval_context(llm: LlmPort) -> NodeContext:
    return NodeContext(
        llm=llm,
        knowledge=SandboxKnowledge(),
        tickets=SandboxTickets(),
        email=SandboxEmail(),
        notifier=SandboxNotifier(),
        config=NodeConfig(),
    )


async def run_cases(ctx: NodeContext, cases: list[EvalCase]) -> list[CaseResult]:
    results: list[CaseResult] = []
    for case in cases:
        state = AgentState(
            request_id=case.id,
            channel=Channel(case.channel),
            raw_subject=case.subject,
            raw_body=case.body,
        )
        classification = (await classify_node(state, ctx))["classification"]
        state.classification = classification
        extracted = (await extract_node(state, ctx))["extracted"]
        results.append(
            CaseResult(
                case=case,
                predicted_type=classification.request_type.value,
                predicted_priority=classification.priority.value,
                confidence=classification.confidence,
                predicted_email=extracted.customer_email,
            )
        )
    return results


def guardrail_catch_rate() -> tuple[float, int]:
    """Fraction of known-bad drafts the guardrail rejects (and it must pass the
    clean control). Deterministic, no model involved, so it's cheap to run every
    time and gates against a weakened guardrail (ADR-0018)."""

    caught = sum(
        1
        for case in POISONED_DRAFTS
        if not check_reply(case.body, customer_email=case.customer_email).ok
    )
    clean_ok = check_reply(CLEAN_DRAFT.body, customer_email=CLEAN_DRAFT.customer_email).ok
    # The clean control failing is a hard miss: fold it in as a "not caught".
    rate = caught / len(POISONED_DRAFTS) if POISONED_DRAFTS else 1.0
    return (rate if clean_ok else 0.0, len(POISONED_DRAFTS))


def summarize(results: list[CaseResult], *, model: str) -> EvalSummary:
    type_pairs = [(r.case.expected_type, r.predicted_type) for r in results]
    priority_pairs = [(r.case.expected_priority, r.predicted_priority) for r in results]
    email_pairs = [(r.case.expected_email, r.predicted_email) for r in results]

    correct_conf = [r.confidence for r in results if r.case.expected_type == r.predicted_type]
    wrong_conf = [r.confidence for r in results if r.case.expected_type != r.predicted_type]

    catch_rate, catch_support = guardrail_catch_rate()

    return EvalSummary(
        classification=classification_report(type_pairs),
        priority_accuracy=accuracy(priority_pairs),
        extraction=extraction_report(email_pairs),
        mean_confidence_correct=_mean(correct_conf),
        mean_confidence_incorrect=_mean(wrong_conf),
        model=model,
        guardrail_catch_rate=catch_rate,
        guardrail_support=catch_support,
    )


def to_dict(summary: EvalSummary) -> dict[str, Any]:
    report = summary.classification
    return {
        "model": summary.model,
        "n": report.n,
        "classification_accuracy": round(report.accuracy, 4),
        "guardrail_catch_rate": round(summary.guardrail_catch_rate, 4),
        "guardrail_support": summary.guardrail_support,
        "classification_macro_f1": round(report.macro_f1, 4),
        "priority_accuracy": round(summary.priority_accuracy, 4),
        "email_extraction_accuracy": round(summary.extraction.email_accuracy, 4),
        "email_support": summary.extraction.email_support,
        "mean_confidence_correct": round(summary.mean_confidence_correct, 4),
        "mean_confidence_incorrect": round(summary.mean_confidence_incorrect, 4),
        "per_class": {
            label: {
                "precision": round(m.precision, 4),
                "recall": round(m.recall, 4),
                "f1": round(m.f1, 4),
                "support": m.support,
            }
            for label, m in report.per_class.items()
        },
    }


def format_report(summary: EvalSummary) -> str:
    report = summary.classification
    lines = [
        "Evaluation summary",
        "==================",
        f"model:                     {summary.model}",
        f"cases:                     {report.n}",
        f"classification accuracy:   {report.accuracy:.1%}",
        f"guardrail catch rate:      {summary.guardrail_catch_rate:.1%} "
        f"(n={summary.guardrail_support})",
        f"classification macro-F1:   {report.macro_f1:.3f}",
        f"priority accuracy:         {summary.priority_accuracy:.1%}",
        f"email extraction accuracy: {summary.extraction.email_accuracy:.1%} "
        f"(n={summary.extraction.email_support})",
        f"mean confidence (correct): {summary.mean_confidence_correct:.2f}",
        f"mean confidence (wrong):   {summary.mean_confidence_incorrect:.2f}",
        "",
        f"{'class':<20}{'precision':>10}{'recall':>10}{'f1':>10}{'support':>9}",
    ]
    for label, m in sorted(report.per_class.items()):
        lines.append(f"{label:<20}{m.precision:>10.2f}{m.recall:>10.2f}{m.f1:>10.2f}{m.support:>9}")
    return "\n".join(lines)


def _mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def default_cases() -> list[EvalCase]:
    return CASES
