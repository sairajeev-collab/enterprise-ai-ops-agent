"""Authentication endpoint: exchange service credentials for a JWT.

Credentials are verified against the ``service_account`` table (bcrypt). The
endpoint is rate-limited to blunt brute-force attempts, and returns a generic
401 that does not reveal whether the account exists.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from app.api.schemas import TokenRequest, TokenResponse
from app.config import Settings, get_settings
from app.db.repository import Repository
from app.deps import enforce_rate_limit, get_repository
from app.errors import AuthenticationError
from app.security.auth import verify_password
from app.security.jwt import create_access_token

router = APIRouter(prefix="/v1/auth", tags=["auth"])


@router.post("/token", response_model=TokenResponse)
async def issue_token(
    payload: TokenRequest,
    repo: Repository = Depends(get_repository),
    settings: Settings = Depends(get_settings),
    _: None = Depends(enforce_rate_limit),
) -> TokenResponse:
    account = await repo.get_service_account(payload.account_id)
    if account is None or not verify_password(payload.password, account.password_hash):
        # Uniform error regardless of which check failed (no user enumeration).
        raise AuthenticationError("Invalid credentials")

    scopes = [scope for scope in account.scopes.split(",") if scope]
    token = create_access_token(
        subject=account.id,
        scopes=scopes,
        secret=settings.jwt_secret,
        issuer=settings.jwt_issuer,
        ttl_seconds=settings.jwt_ttl_seconds,
    )
    return TokenResponse(access_token=token, expires_in=settings.jwt_ttl_seconds)
