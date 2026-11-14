"""Where a host is, for the rules that depend on it (docs/dev/https.md):
HSTS only on a public DNS name (gamma/auth.py), and plain http:// refused
for a new link to a host on the internet (gamma/remote_servers.py).

``literal`` decides from the name or address alone, with no DNS, so a list
or a response header never waits on a resolver. ``resolved`` asks DNS too,
for the few places that decide whether a token may go somewhere: what a
name resolves to here outweighs its suffix (a search domain or an ISP's
rewrite can make ``nas.lan`` a public address).

The table is the desktop shell's too (desktop/lib/hostScope.js ``literal``):
both read the cases in backend/tests/data/host_scope_cases.json, so the
two cannot drift. Every entry is also in proxy_settings.DEFAULT_BYPASS, the
proxy's own "local" list, ``*.localhost`` being the one addition.
"""

import ipaddress
import socket
import threading
import time

from .net_proxy import is_fake_ip

LOOPBACK = "loopback"   # this computer
LAN = "lan"             # a private address or a name only a local network knows
NAME = "name"           # a dotted name, decided by DNS where it matters
FAR = "far"             # a public address
LAN_NETS = tuple(ipaddress.ip_network(net) for net in (
    "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "100.64.0.0/10",
    "169.254.0.0/16", "fc00::/7", "fe80::/10"))
_GLOBAL_V6 = ipaddress.ip_network("2000::/3")   # IPv6 global unicast: a dual-stack LAN host's other address
LAN_SUFFIXES =(".local", ".lan", ".home.arpa", ".internal")
RESOLVED_TTL_S = 60     # how long an answer of ``resolved`` is kept
RESOLVED_MAX = 1024     # answers kept at most; past it they are all dropped

_resolved: dict = {}    # host -> (expires, (scope, unsure))


def _bare(host: str) -> str:
    return (host or "").strip().lower().strip("[]").rstrip(".")


def _address(host: str):
    """``host`` as an IP address (an IPv4-mapped one as IPv4, an IPv6 zone
    dropped), None for a name."""
    try:
        ip = ipaddress.ip_address(host.split("%", 1)[0])
    except ValueError:
        return None
    return (ip.ipv4_mapped or ip) if ip.version == 6 else ip


def _in_lan(ip) -> bool:
    return any(ip.version == net.version and ip in net for net in LAN_NETS)


def literal(host: str) -> str:
    """No DNS. loopback: 127.0.0.0/8, ::1, localhost. lan: an IP in
    LAN_NETS, a single-label name, a LAN_SUFFIXES name, *.localhost. far:
    any other IP literal. name: any other dotted name (decided by DNS where
    it matters)."""
    host = _bare(host)
    ip = _address(host)
    if ip is not None:
        return LOOPBACK if ip.is_loopback else LAN if _in_lan(ip) else FAR
    if host == "localhost":
        return LOOPBACK
    if "." not in host or host.endswith(LAN_SUFFIXES) or host.endswith(".localhost"):
        return LAN
    return NAME


def _scope_of(host: str, infos) -> str:
    """What ``host``'s DNS answer ``infos`` makes it: loopback when every
    address is, lan when every one is loopback or in LAN_NETS, far for any
    other. Fake-IP answers (net_proxy.is_fake_ip) say nothing of where the
    name is: with none left, the name alone decides, a dotted one being far.
    A dual-stack host of the local network answers a private IPv4 address
    and its global IPv6 one (a home router's DNS, mDNS): beside a LAN_NETS
    IPv4 address, global IPv6 (2000::/3) addresses are not counted."""
    ips = [ip for ip in (_address(str(info[4][0])) for info in infos) if ip is not None and not is_fake_ip(ip)]
    if not ips:
        scope = literal(host)
        return FAR if scope == NAME else scope
    if any(ip.version == 4 and _in_lan(ip) for ip in ips):
        ips = [ip for ip in ips if not (ip.version == 6 and ip in _GLOBAL_V6)]
    if all(ip.is_loopback for ip in ips):
        return LOOPBACK
    return LAN if all(ip.is_loopback or _in_lan(ip) for ip in ips) else FAR


def resolved(host: str, timeout: float = 2.0) -> tuple[str, bool]:
    """(scope, unsure). An IP literal or loopback name: literal's answer.
    Otherwise socket.getaddrinfo on a worker thread, at most ``timeout``;
    any Exception (the test suite's AssertionError included) counts as "does
    not resolve". Fake-IP answers (net_proxy.is_fake_ip) are dropped.
    - answers left: all loopback → loopback; all in LAN_NETS or loopback →
      lan, global IPv6 ones beside a LAN_NETS IPv4 one not counted (a
      dual-stack LAN host); any other → far (whatever the suffix: a search
      domain or an ISP's NXDOMAIN rewrite can make nas.lan public);
    - does not resolve: literal(host), with "name" → far;
    - timed out: lan with unsure=True (a warning, never a refusal).
    Kept 60 s, keyed by host (a time-out is not kept: the lookup, still
    running, keeps its answer when it comes)."""
    host = _bare(host)
    if _address(host) is not None or host == "localhost":
        return literal(host), False
    kept = _resolved.get(host)
    if kept and kept[0] > time.monotonic():
        return kept[1]
    done, answer = threading.Event(), []

    def look():
        try:
            infos = socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
        except Exception:  # noqa: BLE001 — a name that cannot be looked up does not resolve
            infos = []
        answer.append((_scope_of(host, infos), False))
        if len(_resolved) >= RESOLVED_MAX:
            _resolved.clear()
        _resolved[host] = (time.monotonic() + RESOLVED_TTL_S, answer[0])
        done.set()

    threading.Thread(target=look, name="host-scope", daemon=True).start()
    if not done.wait(timeout):
        return LAN, True
    return answer[0]
