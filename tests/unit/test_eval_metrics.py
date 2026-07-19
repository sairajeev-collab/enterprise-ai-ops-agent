"""Unit tests for the evaluation metric functions."""

from __future__ import annotations

import pytest
from evals.metrics import accuracy, classification_report, extraction_report


def test_classification_report_known_values() -> None:
    # a: tp=1 fn=1 -> P=1.00 R=0.50 F1=0.667 ; b: tp=2 fp=1 -> P=0.667 R=1.0 F1=0.8
    pairs = [("a", "a"), ("a", "b"), ("b", "b"), ("b", "b")]
    report = classification_report(pairs)

    assert report.n == 4
    assert report.accuracy == pytest.approx(0.75)
    assert report.per_class["a"].precision == pytest.approx(1.0)
    assert report.per_class["a"].recall == pytest.approx(0.5)
    assert report.per_class["a"].f1 == pytest.approx(2 / 3, abs=1e-3)
    assert report.per_class["b"].f1 == pytest.approx(0.8)
    assert report.macro_f1 == pytest.approx((2 / 3 + 0.8) / 2, abs=1e-3)


def test_accuracy_matches_report() -> None:
    pairs = [("a", "a"), ("a", "b"), ("b", "b")]
    assert accuracy(pairs) == pytest.approx(classification_report(pairs).accuracy)


def test_classification_report_rejects_empty() -> None:
    with pytest.raises(ValueError):
        classification_report([])


def test_extraction_report_ignores_cases_without_expected_email() -> None:
    pairs = [("a@b.com", "a@b.com"), ("", "noise@x.com"), ("c@d.com", "")]
    report = extraction_report(pairs)
    assert report.email_support == 2  # only the two with an expected email count
    assert report.email_accuracy == pytest.approx(0.5)


def test_extraction_report_is_case_insensitive() -> None:
    report = extraction_report([("User@Example.com", "user@example.com")])
    assert report.email_accuracy == pytest.approx(1.0)
