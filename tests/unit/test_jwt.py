"""Unit tests for JWT minting and verification."""

from __future__ import annotations

import datetime as dt

import pytest
from app.errors import AuthenticationError
from app.security.jwt import create_access_token, decode_token

SECRET = "unit-test-secret"
ISSUER = "test-issuer"


def _token(**overrides: object) -> str:
    params: dict[str, object] = {
        "subject": "svc",
        "scopes": ["requests:write", "reports:read"],
        "secret": SECRET,
        "issuer": ISSUER,
        "ttl_seconds": 3600,
    }
    params.update(overrides)
    return create_access_token(**params)  # type: ignore[arg-type]


def test_roundtrip_preserves_subject_and_scopes() -> None:
    principal = decode_token(_token(), secret=SECRET, issuer=ISSUER)
    assert principal.subject == "svc"
    assert principal.scopes == frozenset({"requests:write", "reports:read"})


def test_expired_token_rejected() -> None:
    past = dt.datetime.now(dt.UTC) - dt.timedelta(hours=2)
    token = _token(ttl_seconds=1, now=past)
    with pytest.raises(AuthenticationError):
        decode_token(token, secret=SECRET, issuer=ISSUER)


def test_wrong_secret_rejected() -> None:
    with pytest.raises(AuthenticationError):
        decode_token(_token(), secret="other-secret", issuer=ISSUER)


def test_wrong_issuer_rejected() -> None:
    with pytest.raises(AuthenticationError):
        decode_token(_token(), secret=SECRET, issuer="someone-else")


def test_tampered_token_rejected() -> None:
    token = _token()
    tampered = token[:-3] + ("aaa" if not token.endswith("aaa") else "bbb")
    with pytest.raises(AuthenticationError):
        decode_token(tampered, secret=SECRET, issuer=ISSUER)
