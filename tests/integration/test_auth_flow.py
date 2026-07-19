"""Integration tests for the token issuance and scope enforcement flow."""

from __future__ import annotations

import httpx
import pytest

pytestmark = pytest.mark.integration

_CREDS = {"account_id": "ops-service", "password": "test-password"}


async def test_token_issue_and_use(client: httpx.AsyncClient) -> None:
    resp = await client.post("/v1/auth/token", json=_CREDS)
    assert resp.status_code == 200
    token = resp.json()["access_token"]
    assert resp.json()["token_type"] == "bearer"

    # The minted token works on a protected route.
    headers = {"Authorization": f"Bearer {token}"}
    submit = await client.post(
        "/v1/requests?inline=true",
        json={"channel": "email", "body": "I need a refund. from Bo Li bo@x.io"},
        headers=headers,
    )
    assert submit.status_code == 202


async def test_wrong_password_rejected(client: httpx.AsyncClient) -> None:
    resp = await client.post(
        "/v1/auth/token", json={"account_id": "ops-service", "password": "wrong"}
    )
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "authentication_error"


async def test_unknown_account_rejected(client: httpx.AsyncClient) -> None:
    resp = await client.post("/v1/auth/token", json={"account_id": "ghost", "password": "whatever"})
    assert resp.status_code == 401


async def test_missing_scope_forbidden(client: httpx.AsyncClient) -> None:
    # A token minted with an unrelated scope must be rejected on write routes.
    from app.config import get_settings
    from app.security.jwt import create_access_token

    settings = get_settings()
    token = create_access_token(
        subject="limited",
        scopes=["something:else"],
        secret=settings.jwt_secret,
        issuer=settings.jwt_issuer,
        ttl_seconds=3600,
    )
    resp = await client.post(
        "/v1/requests?inline=true",
        json={"channel": "email", "body": "hello there"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "authorization_error"
