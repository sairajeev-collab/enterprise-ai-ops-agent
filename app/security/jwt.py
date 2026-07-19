"""JWT minting and verification (HS256).

Centralized so token semantics live in exactly one place. Scopes are stored in
the standard ``scope`` claim as a space-separated string (OAuth2 convention).
Verification raises :class:`AuthenticationError` for anything untrusted so the
API layer maps it to a clean 401.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

import jwt

from app.errors import AuthenticationError


@dataclass(frozen=True)
class Principal:
    """The authenticated caller derived from a valid token."""

    subject: str
    scopes: frozenset[str]


def create_access_token(
    *,
    subject: str,
    scopes: list[str],
    secret: str,
    issuer: str,
    ttl_seconds: int,
    now: dt.datetime | None = None,
) -> str:
    issued = now or dt.datetime.now(dt.UTC)
    payload = {
        "sub": subject,
        "scope": " ".join(scopes),
        "iss": issuer,
        "iat": int(issued.timestamp()),
        "exp": int((issued + dt.timedelta(seconds=ttl_seconds)).timestamp()),
    }
    return jwt.encode(payload, secret, algorithm="HS256")


def decode_token(token: str, *, secret: str, issuer: str) -> Principal:
    try:
        claims = jwt.decode(
            token,
            secret,
            algorithms=["HS256"],
            issuer=issuer,
            options={"require": ["exp", "iat", "sub", "iss"]},
        )
    except jwt.ExpiredSignatureError as exc:
        raise AuthenticationError("Token has expired") from exc
    except jwt.InvalidTokenError as exc:
        raise AuthenticationError("Invalid authentication token") from exc

    subject = str(claims.get("sub", ""))
    if not subject:
        raise AuthenticationError("Token missing subject")
    scope_str = str(claims.get("scope", ""))
    scopes = frozenset(s for s in scope_str.split(" ") if s)
    return Principal(subject=subject, scopes=scopes)
