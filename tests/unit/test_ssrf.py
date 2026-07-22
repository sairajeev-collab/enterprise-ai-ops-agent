"""SSRF egress-guard unit tests (ADR-0021).

Uses IP literals so getaddrinfo resolves to itself and the suite needs no network.
The one thing that must never regress: a caller-supplied callback can't be pointed
at the private network or the cloud metadata endpoint.
"""

from __future__ import annotations

import pytest
from app.security.ssrf import EgressBlocked, validate_egress_url


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1/hook",  # loopback
        "http://10.0.0.5/hook",  # private
        "http://192.168.1.10/hook",  # private
        "http://169.254.169.254/latest/meta-data/",  # cloud metadata (link-local)
        "http://[::1]/hook",  # ipv6 loopback
    ],
)
def test_private_and_metadata_targets_are_blocked(url: str) -> None:
    with pytest.raises(EgressBlocked):
        validate_egress_url(url)


def test_public_ip_is_allowed() -> None:
    # 8.8.8.8 is a public address; a literal resolves to itself (no network).
    validate_egress_url("https://8.8.8.8/callback")


@pytest.mark.parametrize("url", ["ftp://example.com/x", "file:///etc/passwd", "gopher://x"])
def test_non_http_schemes_are_blocked(url: str) -> None:
    with pytest.raises(EgressBlocked):
        validate_egress_url(url)


def test_credentials_in_url_are_blocked() -> None:
    with pytest.raises(EgressBlocked):
        validate_egress_url("https://user:pass@8.8.8.8/callback")


def test_missing_host_is_blocked() -> None:
    with pytest.raises(EgressBlocked):
        validate_egress_url("http:///nohost")


def test_allowlist_permits_listed_host_and_skips_ip_check() -> None:
    # An allowlisted internal host is reachable on purpose. The operator opted in.
    validate_egress_url("http://internal-hook.svc/notify", allowed_hosts=["internal-hook.svc"])


def test_allowlist_rejects_unlisted_host() -> None:
    with pytest.raises(EgressBlocked):
        validate_egress_url("https://8.8.8.8/callback", allowed_hosts=["only-this.example.com"])


def test_block_private_false_is_syntactic_only() -> None:
    # Intake mode: cheap checks pass a private IP through; the fire-time check with
    # block_private=True is the authoritative gate.
    validate_egress_url("http://10.0.0.5/hook", block_private=False)
    # ...but syntactic failures still raise even in this mode.
    with pytest.raises(EgressBlocked):
        validate_egress_url("ftp://10.0.0.5/hook", block_private=False)
