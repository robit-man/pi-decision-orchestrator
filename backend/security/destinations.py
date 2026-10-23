import asyncio
import ipaddress
import socket
from urllib.parse import urlsplit, urlunsplit

from backend.errors import DomainError


def parse_destination(url: str, allowed_schemes=("http", "https")):
    try:
        parsed = urlsplit(url)
        port = parsed.port
    except ValueError:
        raise DomainError("invalid_destination", "Use a valid destination URL.") from None
    if (
        parsed.scheme not in allowed_schemes
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.fragment
    ):
        raise DomainError(
            "invalid_destination", "Use an allowed URL scheme without embedded credentials or fragments."
        )
    if len(url) > 2048 or "\\" in url or any(ord(char) < 32 for char in url):
        raise DomainError("invalid_destination", "The destination URL is invalid.")
    return parsed, port or {"http": 80, "https": 443, "mqtt": 1883, "mqtts": 8883}[parsed.scheme]


async def resolve_destination(url: str, config: dict, schemes=("http", "https")) -> tuple[str, str, int]:
    parsed, port = parse_destination(url, schemes)
    allowed = config.get("allowed_hosts", [])
    if parsed.hostname not in allowed:
        raise DomainError(
            "destination_not_allowed", "The destination host must be explicitly allowlisted.", 403
        )
    try:
        records = await asyncio.get_running_loop().getaddrinfo(parsed.hostname, port, type=socket.SOCK_STREAM)
    except OSError:
        raise DomainError("destination_dns", "The destination hostname could not be resolved.", 502) from None
    addresses = list(dict.fromkeys(record[4][0] for record in records))
    if not addresses:
        raise DomainError("destination_dns", "The destination hostname has no usable address.", 502)
    for address in addresses:
        value = ipaddress.ip_address(address)
        if isinstance(value, ipaddress.IPv6Address) and value.ipv4_mapped:
            value = value.ipv4_mapped
        if (
            value.is_loopback
            or value.is_link_local
            or value.is_multicast
            or value.is_unspecified
            or value.is_reserved
        ):
            raise DomainError(
                "ssrf_blocked", "Loopback, link-local, multicast, and reserved destinations are blocked.", 403
            )
        if not value.is_global and not config.get("allow_private", False):
            raise DomainError(
                "private_destination",
                "Approve this private LAN destination in the connection settings first.",
                403,
            )
        cidrs = config.get("allowed_cidrs", [])
        if cidrs and not any(value in ipaddress.ip_network(cidr, strict=False) for cidr in cidrs):
            raise DomainError(
                "destination_network",
                "The resolved address is outside this connection's allowed networks.",
                403,
            )
    selected = next((address for address in addresses if ":" not in address), addresses[0])
    host = f"[{selected}]" if ":" in selected else selected
    pinned = urlunsplit((parsed.scheme, f"{host}:{port}", parsed.path or "/", parsed.query, ""))
    return pinned, parsed.hostname, port
