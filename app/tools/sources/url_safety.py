from __future__ import annotations

import ipaddress
import socket
from collections.abc import Callable
from urllib.parse import urlparse


Resolver = Callable[..., list[tuple]]


def validate_public_https_url(
    url: str,
    *,
    allowed_hosts: set[str] | None = None,
    resolver: Resolver = socket.getaddrinfo,
) -> str:
    """Reject non-public destinations before every HTTP or browser request."""
    parsed = urlparse(url)
    hostname = (parsed.hostname or "").casefold().rstrip(".")
    if parsed.scheme != "https" or not hostname or parsed.username or parsed.password:
        raise ValueError("Only credential-free absolute HTTPS public URLs are allowed.")
    if hostname == "localhost" or hostname.endswith(".localhost"):
        raise ValueError("Localhost is not an allowed public destination.")
    if allowed_hosts and not any(
        hostname == allowed or hostname.endswith(f".{allowed}")
        for allowed in {item.casefold().rstrip(".") for item in allowed_hosts}
    ):
        raise ValueError("The destination hostname is not allowlisted.")
    try:
        addresses = {item[4][0] for item in resolver(hostname, parsed.port or 443, type=socket.SOCK_STREAM)}
    except OSError as error:
        raise ValueError("The public destination hostname could not be resolved.") from error
    if not addresses:
        raise ValueError("The public destination hostname did not resolve.")
    for value in addresses:
        address = ipaddress.ip_address(value.split("%", 1)[0])
        if not address.is_global:
            raise ValueError("Private, local, reserved, and link-local destinations are blocked.")
    return url
