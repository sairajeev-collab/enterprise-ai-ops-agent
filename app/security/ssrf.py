"""SSRF egress guard for caller-supplied URLs.

The only place this system makes an HTTP call to an address a *caller* chose is the
completion callback: a request can carry a ``callback_url`` the worker POSTs to when
the job finishes. Left unguarded that's a textbook SSRF. A caller points it at
``http://169.254.169.254/`` (cloud metadata) or an internal service and uses our
worker as a proxy into the private network.

This module is the guard. It rejects non-http(s) URLs, URLs with embedded
credentials, hosts outside an optional allowlist, and. The important part. Any
host that resolves to a private, loopback, link-local, or otherwise non-public
address. It's called at intake (cheap, fail-fast) and again at fire time, because
the DNS answer can change between the two.
"""

from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlparse


class EgressBlocked(ValueError):
    """Raised when a URL is not a permitted egress target."""


def _resolved_ips(host: str) -> list[ipaddress.IPv4Address | ipaddress.IPv6Address]:
    """All IPs a hostname resolves to. A literal IP resolves to itself."""

    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror as exc:
        raise EgressBlocked(f"callback host does not resolve: {host}") from exc
    ips: list[ipaddress.IPv4Address | ipaddress.IPv6Address] = []
    for info in infos:
        sockaddr = info[4]
        ips.append(ipaddress.ip_address(sockaddr[0]))
    return ips


def _is_public(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    # Reject everything that isn't a normal routable public address: this covers
    # loopback (127/8, ::1), private (10/8, 172.16/12, 192.168/16, fc00::/7),
    # link-local (169.254/16. The cloud metadata range, and fe80::/10),
    # multicast, and reserved/unspecified.
    return not (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    )


def validate_egress_url(
    url: str,
    *,
    allowed_hosts: list[str] | None = None,
    block_private: bool = True,
) -> None:
    """Raise :class:`EgressBlocked` if ``url`` is not a safe egress target.

    ``allowed_hosts`` (if non-empty) is a strict allowlist of hostnames; anything
    else is rejected regardless of the IP checks. ``block_private`` gates the
    resolve-and-check-IP step. Kept configurable so a genuinely internal
    deployment can opt out deliberately, rather than the guard silently doing
    nothing.
    """

    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise EgressBlocked("callback_url must be an http(s) URL")
    if parsed.username or parsed.password:
        # Credentials in the URL are a smell and an exfiltration vector.
        raise EgressBlocked("callback_url must not contain credentials")
    host = parsed.hostname
    if not host:
        raise EgressBlocked("callback_url has no host")

    if allowed_hosts:
        if host not in allowed_hosts:
            raise EgressBlocked(f"callback host not in allowlist: {host}")
        # An explicit allowlist is the operator's decision; trust it and skip the
        # IP check so an internal allowlisted host is reachable on purpose.
        return

    if block_private:
        for ip in _resolved_ips(host):
            if not _is_public(ip):
                raise EgressBlocked(f"callback host resolves to a non-public address: {ip}")
