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

from evals.dataset import CASES, EvalCase
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


def summarize(results: list[CaseResult]) -> EvalSummary:
    type_pairs = [(r.case.expected_type, r.predicted_type) for r in results]
    priority_pairs = [(r.case.expected_priority, r.predicted_priority) for r in results]
    email_pairs = [(r.case.expected_email, r.predicted_email) for r in results]

    correct_conf = [r.confidence for r in results if r.case.expected_type == r.predicted_type]
    wrong_conf = [r.confidence for r in results if r.case.expected_type != r.predicted_type]

    return EvalSummary(
        classification=classification_report(type_pairs),
        priority_accuracy=accuracy(priority_pairs),
        extraction=extraction_report(email_pairs),
        mean_confidence_correct=_mean(correct_conf),
        mean_confidence_incorrect=_mean(wrong_conf),
    )


def to_dict(summary: EvalSummary) -> dict[str, Any]:
    report = summary.classification
    return {
        "n": report.n,
        "classification_accuracy": round(report.accuracy, 4),
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
        f"cases:                     {report.n}",
        f"classification accuracy:   {report.accuracy:.1%}",
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
