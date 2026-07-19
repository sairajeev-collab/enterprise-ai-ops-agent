"""Unit tests for boundary validation on domain and API models."""

from __future__ import annotations

import pytest
from app.api.schemas import CreateRequest
from app.config import DEFAULT_JWT_SECRET, Settings
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


_STRONG_SECRET = "a" * 48


def test_production_rejects_default_jwt_secret() -> None:
    with pytest.raises(ValidationError, match="JWT_SECRET"):
        Settings(app_env="production", jwt_secret=DEFAULT_JWT_SECRET)


def test_production_rejects_real_integration_without_credentials() -> None:
    with pytest.raises(ValidationError, match="SLACK_MODE=real"):
        Settings(
            app_env="production",
            jwt_secret=_STRONG_SECRET,
            slack_mode="real",
            slack_webhook_url="",
        )


def test_production_accepts_strong_secret_and_sandbox_modes() -> None:
    settings = Settings(app_env="production", jwt_secret=_STRONG_SECRET)
    assert settings.is_production


def test_non_production_tolerates_default_secret() -> None:
    # Local/CI must remain frictionless with the placeholder secret.
    settings = Settings(app_env="local", jwt_secret=DEFAULT_JWT_SECRET)
    assert not settings.is_production
