from ipaddress import ip_address, ip_network
from socket import error, inet_aton


ALLOWED_ALL_IPS = "*"


def is_valid_ipv4_address_or_range(ip: str):
    try:
        inet_aton(ip)
        return True
    except error:
        pass

    try:
        ip_network(ip, strict=False)
        return True
    except Exception:
        return False


def is_ipv4_in_range(ip: str, ip_range: str) -> bool:
    try:
        return ip_address(ip) in ip_network(ip_range, strict=False)
    except Exception:
        return False


def make_valid_ipv4_range(ip: str) -> str:
    octets = ip.split(".")
    if octets[-1] != "0/24":
        octets[-1] = "0/24"
    return ".".join(octets)


def has_allowed_ips(whitelist: list[str] | str | None, ips: list[str] | str | None) -> bool:
    if not whitelist or not ips:
        return False

    if isinstance(whitelist, str):
        whitelist = whitelist.split(",")

    if ALLOWED_ALL_IPS in whitelist:
        return True

    if isinstance(ips, str):
        ips = ips.split(",")

    has_valid_ips = False
    for ip in ips:
        if not is_valid_ipv4_address_or_range(ip):
            continue
        has_valid_ips = True
        break

    if not has_valid_ips:
        return False

    for ip_range in whitelist:
        for ip in ips:
            if ip_range.endswith(".0/24") and is_ipv4_in_range(ip, ip_range):
                return True
            elif ip == ip_range:
                return True

    return False


def filter_valid_ip_whitelist(ip_whitelist: list[str]) -> list[str]:
    valid_ip_whitelist = []
    if ALLOWED_ALL_IPS in ip_whitelist:
        valid_ip_whitelist.append(ALLOWED_ALL_IPS)
    else:
        for ip in ip_whitelist:
            if not is_valid_ipv4_address_or_range(ip):
                continue
            if ip.endswith("/24"):
                ip = make_valid_ipv4_range(ip)
            valid_ip_whitelist.append(ip)
    return valid_ip_whitelist


def is_ip_in_trusted_proxies(ip: str, trusted_proxies: list[str]) -> bool:
    """Check an exact IP against a list of IPs or CIDR ranges."""
    if not ip:
        return False
    for proxy in trusted_proxies:
        if not proxy:
            continue
        if proxy == ip:
            return True
        if "/" in proxy and is_ipv4_in_range(ip, proxy):
            return True
    return False


def resolve_client_ip(
    peer_ip: str | None,
    x_forwarded_for: str | None,
    trusted_proxies: list[str],
) -> str | None:
    """Resolve the client IP, trusting forwarded headers only from trusted proxies.

    The peer address is the only source the client cannot forge. When the peer is
    a trusted proxy, X-Forwarded-For is walked right-to-left while each hop is a
    trusted proxy; the first non-trusted hop (or the leftmost entry) is the
    client. When the peer is NOT a trusted proxy, all forwarded headers are
    attacker-controlled and ignored — the peer itself is the client. Returns
    None when no address can be established (callers must fail closed).
    """
    peer = peer_ip.strip() if isinstance(peer_ip, str) else ""
    try:
        ip_address(peer)
    except ValueError:
        return None
    if not is_ip_in_trusted_proxies(peer, trusted_proxies):
        return peer

    hops = [hop.strip() for hop in (x_forwarded_for or "").split(",")]
    try:
        for hop in hops:
            ip_address(hop)
    except ValueError:
        return None
    # Walk right-to-left while hops are trusted proxies; the first non-trusted
    # hop is the client. If every hop is trusted, the leftmost entry is the
    # originating client.
    for hop in reversed(hops):
        if not is_ip_in_trusted_proxies(hop, trusted_proxies):
            return hop
    return hops[0]
