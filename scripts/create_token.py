"""Mint a JWT for local development or operator access.

Usage:
    python -m scripts.create_token --subject ops-service \
        --scopes requests:write,reports:read

The signing secret and issuer come from the environment (same as the running
API), so tokens minted here are accepted by the service.
"""

from __future__ import annotations

import argparse

from app.config import get_settings
from app.security.jwt import create_access_token


def main() -> None:
    settings = get_settings()
    parser = argparse.ArgumentParser(description="Mint a service JWT.")
    parser.add_argument("--subject", default=settings.service_account_id)
    parser.add_argument(
        "--scopes",
        default="requests:write,reports:read",
        help="Comma-separated scopes.",
    )
    parser.add_argument("--ttl", type=int, default=settings.jwt_ttl_seconds)
    args = parser.parse_args()

    token = create_access_token(
        subject=args.subject,
        scopes=[s for s in args.scopes.split(",") if s],
        secret=settings.jwt_secret,
        issuer=settings.jwt_issuer,
        ttl_seconds=args.ttl,
    )
    print(token)


if __name__ == "__main__":
    main()
