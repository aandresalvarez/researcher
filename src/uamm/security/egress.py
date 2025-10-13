from dataclasses import dataclass
from ipaddress import ip_address, IPv4Address, IPv6Address
from urllib.parse import urlparse
import socket


@dataclass
class EgressPolicy:
    block_private_ip: bool = True
    allow_redirects: int = 3
    max_payload_bytes: int = 5 * 1024 * 1024
    enforce_tls: bool = True
    denylist_hosts: tuple[str, ...] = ()
    allowlist_hosts: tuple[
        str, ...
    ] = ()  # empty means allow all (subject to other checks)


_PRIVATE_CIDRS = (
    ("127.0.0.0", 8),  # loopback
    ("10.0.0.0", 8),  # RFC1918
    ("172.16.0.0", 12),  # RFC1918
    ("192.168.0.0", 16),  # RFC1918
)


def _is_private_ip(addr: str) -> bool:
    try:
        ip = ip_address(addr)
    except ValueError:
        return False
    if isinstance(ip, (IPv4Address, IPv6Address)) and (
        ip.is_private or ip.is_loopback or ip.is_link_local
    ):
        return True
    # additional IPv4 CIDR checks
    if isinstance(ip, IPv4Address):
        a = int(ip)
        for base, mask in _PRIVATE_CIDRS:
            b = int(ip_address(base))
            m = (0xFFFFFFFF << (32 - mask)) & 0xFFFFFFFF
            if (a & m) == (b & m):
                return True
    return False


def check_url_allowed(url: str, policy: EgressPolicy) -> None:
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise ValueError("disallowed scheme")
    if policy.enforce_tls and parsed.scheme != "https":
        raise ValueError("TLS required")
    host = parsed.hostname or ""
    if not host:
        raise ValueError("missing host")
    if policy.denylist_hosts and host in policy.denylist_hosts:
        raise ValueError("host denied")
    if policy.allowlist_hosts and host not in policy.allowlist_hosts:
        raise ValueError("host not in allowlist")
    if policy.block_private_ip:
        try:
            infos = socket.getaddrinfo(host, None)
            addrs = {info[4][0] for info in infos}
            for addr in addrs:
                if _is_private_ip(addr):
                    raise ValueError("private IP blocked")
        except socket.gaierror as e:
            raise ValueError(f"DNS resolution failed: {e}")
