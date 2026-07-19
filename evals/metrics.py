"""Metric computation for the evaluation harness.

Pure functions over ``(expected, predicted)`` pairs, so they are trivially
unit-testable and framework-free. Classification uses per-class
precision/recall/F1 plus macro-F1 (averaged over the union of true and predicted
labels, matching scikit-learn's default), which is the honest way to score a
multi-class problem with imbalance.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ClassMetric:
    precision: float
    recall: float
    f1: float
    support: int


@dataclass(frozen=True)
class ClassificationReport:
    accuracy: float
    macro_f1: float
    per_class: dict[str, ClassMetric]
    n: int


@dataclass(frozen=True)
class ExtractionReport:
    email_accuracy: float
    email_support: int


def _f1(precision: float, recall: float) -> float:
    denom = precision + recall
    return (2 * precision * recall / denom) if denom else 0.0


def classification_report(pairs: list[tuple[str, str]]) -> ClassificationReport:
    if not pairs:
        raise ValueError("cannot score an empty result set")

    n = len(pairs)
    correct = sum(1 for expected, predicted in pairs if expected == predicted)
    labels = sorted({expected for expected, _ in pairs} | {predicted for _, predicted in pairs})

    per_class: dict[str, ClassMetric] = {}
    f1_scores: list[float] = []
    for label in labels:
        tp = sum(1 for e, p in pairs if e == label and p == label)
        fp = sum(1 for e, p in pairs if e != label and p == label)
        fn = sum(1 for e, p in pairs if e == label and p != label)
        support = sum(1 for e, _ in pairs if e == label)
        precision = tp / (tp + fp) if (tp + fp) else 0.0
        recall = tp / (tp + fn) if (tp + fn) else 0.0
        f1 = _f1(precision, recall)
        per_class[label] = ClassMetric(precision, recall, f1, support)
        f1_scores.append(f1)

    macro_f1 = sum(f1_scores) / len(f1_scores) if f1_scores else 0.0
    return ClassificationReport(accuracy=correct / n, macro_f1=macro_f1, per_class=per_class, n=n)


def accuracy(pairs: list[tuple[str, str]]) -> float:
    if not pairs:
        return 0.0
    return sum(1 for expected, predicted in pairs if expected == predicted) / len(pairs)


def extraction_report(pairs: list[tuple[str, str]]) -> ExtractionReport:
    """Email-extraction accuracy over cases that actually contain an email."""

    relevant = [(expected, predicted) for expected, predicted in pairs if expected]
    support = len(relevant)
    correct = sum(1 for expected, predicted in relevant if expected.lower() == predicted.lower())
    email_accuracy = correct / support if support else 1.0
    return ExtractionReport(email_accuracy=email_accuracy, email_support=support)
