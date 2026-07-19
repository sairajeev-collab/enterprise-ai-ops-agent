"""Unit tests for boundary validation on domain and API models."""

from __future__ import annotations

import pytest
from app.api.schemas import CreateRequest
from app.domain.enums import Priority, RequestType
from app.domain.state import Classification
from pydantic import ValidationError


def test_classification_confidence_bounds() -> None:
    with pytest.raises(ValidationError):
        Classification(request_type=RequestType.OTHER, priority=Priority.LOW, confidence=1.5)
    with pytest.raises(ValidationError):
        Classification(request_type=RequestType.OTHER, priority=Priority.LOW, confidence=-0.1)


def test_create_request_strips_control_characters() -> None:
    payload = CreateRequest(channel="email", subject="hi\x00there", body="line1\x07line2")
    assert "\x00" not in payload.subject
    assert "\x07" not in payload.body
    # Newlines/tabs are preserved as legitimate content.
    keep = CreateRequest(channel="email", subject="s", body="a\nb\tc")
    assert keep.body == "a\nb\tc"


def test_create_request_rejects_empty_body_after_sanitize() -> None:
    with pytest.raises(ValidationError):
        CreateRequest(channel="email", subject="s", body="\x00\x01\x02")


def test_create_request_rejects_unknown_channel() -> None:
    with pytest.raises(ValidationError):
        CreateRequest(channel="carrier_pigeon", body="hello")  # type: ignore[arg-type]


def test_create_request_enforces_body_length() -> None:
    with pytest.raises(ValidationError):
        CreateRequest(channel="email", body="x" * 50_001)
