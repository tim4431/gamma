"""Outbound-fetch safety: block SSRF to internal/loopback/metadata hosts and
non-HTTP schemes.

Every server-side fetch of a user-supplied URL (the PDF proxy, PDF resolution,
the AI PDF re-download) must go through :func:`guarded_urlopen` so an attacker
can't turn the server into a request forwarder for ``file://`` reads,
``http://127.0.0.1`` / ``169.254.169.254`` probes, or reaching private-network
services the box can see but the internet can't.

The transport resolves and validates all addresses immediately before opening
a socket, then connects to that numeric address without resolving again. The
original hostname remains the HTTP Host and TLS certificate/SNI name.

These fetches take the route every connection of the server takes
(gamma/net_proxy.py: the admin's network proxy and its bypass list, never a
proxy a member chooses), and stay pinned through the proxy: the host is
resolved and checked here, and the proxy is asked for the checked address
(CONNECT or SOCKS5 to it, an http URL's too: a proxy sent the URL in full
would forward the Host it names), the hostname kept for Host and TLS. Only
with the admin's ``proxy_dns`` ("Let the proxy look up names", for networks
where only the proxy resolves names, or a VPN answering fake addresses) is
the proxy asked for the name, once the name passed the checks it allows
(``_proxy_resolvable``): the proxy then decides which addresses such a
fetch reaches.
"""

import ipaddress
import socket
import urllib.parse
from contextlib import contextmanager
from contextvars import ContextVar
from urllib.error import URLError
from urllib.request import BaseHandler, HTTPCookieProcessor, HTTPRedirectHandler

from . import net_proxy
from .publisher_sessions import browser_profile

_ALLOWED_SCHEMES = ("http", "https")
# Names only a local network knows, which a proxy must never be asked for.
_LOCAL_SUFFIXES = (".localhost", ".local", ".internal", ".home.arpa", ".lan")


class BlockedUrlError(URLError):
    """Raised when a URL is refused by the SSRF guard (subclass of URLError so
    existing ``except URLError`` handlers surface it as a clean 400)."""

    def __init__(self, reason: str):
        super().__init__(reason)


def _ip_is_blocked(ip: str) -> bool:
    addr = ipaddress.ip_address(ip)
    return (
        not addr.is_global
        or addr.is_loopback
        or addr.is_link_local      # 169.254/16 — cloud metadata lives here
        or addr.is_multicast
        or addr.is_reserved
        or addr.is_unspecified
        or (addr.version == 6 and addr.ipv4_mapped is not None and _ip_is_blocked(str(addr.ipv4_mapped)))
    )


def validate_public_url(url: str, policy=None) -> str:
    """Return the URL unchanged if it is an http(s) URL whose host may be
    fetched on its route under ``policy`` (the saved one by default): one
    that resolves only to public addresses, or with ``proxy_dns`` through
    the proxy, one ``_proxy_resolvable`` passes; otherwise raise
    BlockedUrlError."""
    try:
        parsed = urllib.parse.urlparse(url)
        host, named = parsed.hostname, parsed.port
        port = named or (443 if parsed.scheme.lower() == "https" else 80)
    except ValueError as exc:
        raise BlockedUrlError("invalid URL host or port") from exc
    if parsed.scheme.lower() not in _ALLOWED_SCHEMES:
        raise BlockedUrlError(f"blocked URL scheme: {parsed.scheme or '(none)'}")
    if not host:
        raise BlockedUrlError("URL has no host")
    policy = policy or net_proxy.current()
    route = net_proxy.route(host, parsed.scheme, policy, named)
    if route.kind == "direct":
        _public_addresses(host, port)
    else:
        _target(policy, route, host, port)
    return url


def _public_addresses(host, port):
    """Validate the complete DNS answer before any address is connected."""
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM, proto=socket.IPPROTO_TCP)
    except socket.gaierror:
        raise BlockedUrlError(f"cannot resolve host: {host}")
    if not infos:
        raise BlockedUrlError(f"cannot resolve host: {host}")
    for info in infos:
        ip = info[4][0]
        try:
            if _ip_is_blocked(ip):
                raise BlockedUrlError(f"blocked internal address for {host}: {ip}")
        except ValueError:
            raise BlockedUrlError(f"unparseable address for {host}: {ip}")
    return infos


def _public_connection(address, timeout=socket._GLOBAL_DEFAULT_TIMEOUT, source_address=None):
    """Like create_connection, without a second DNS lookup after validation."""
    host, port = address
    infos = _public_addresses(host, port)
    last_error = None
    for family, socktype, proto, _, sockaddr in infos:
        sock = None
        try:
            sock = socket.socket(family, socktype, proto)
            if timeout is not socket._GLOBAL_DEFAULT_TIMEOUT:
                sock.settimeout(timeout)
            if source_address:
                sock.bind(source_address)
            sock.connect(sockaddr)
            return sock
        except OSError as exc:
            last_error = exc
            if sock is not None:
                sock.close()
    raise last_error


def _target(policy, route, host, port) -> str:
    """What the proxy of ``route`` is asked for (net_proxy's ``pin``): an
    address ``host`` resolves to here, the whole answer checked like a
    direct fetch's (an IPv4 one when there is one: a proxy may have no IPv6
    route); with ``proxy_dns``, the host itself, without a trailing dot,
    once ``_proxy_resolvable`` passed it."""
    if policy.proxy_dns:
        return _proxy_resolvable(host, port)
    addresses = [info[4][0] for info in _public_addresses(host, port)]
    return next((ip for ip in addresses if ":" not in ip), addresses[0])


def _proxy_resolvable(host, port) -> str:
    """``host`` without a trailing dot, the proxy being asked for it by
    name; BlockedUrlError for what it may not be asked for: a blocked
    address (written any way a proxy reads as one: ``10.0.0.5.``,
    ``127.1``, ``0x7f.1``), a name only a local network knows (localhost, a
    name without a dot, _LOCAL_SUFFIXES), or a name that resolves here to a
    blocked address outside net_proxy.FAKE_IP. A name that does not
    resolve here is the proxy's to resolve."""
    bare = host.rstrip(".")
    try:
        ip = str(ipaddress.ip_address(bare))
    except ValueError:
        try:
            ip = socket.inet_ntoa(socket.inet_aton(bare))  # 127.1, 0x7f.1, 10.1: what a proxy reads as IPv4
        except (OSError, UnicodeError, ValueError):
            ip = None
    if ip is not None:
        if _ip_is_blocked(ip):
            raise BlockedUrlError(f"blocked internal address: {host}")
        return bare
    name = bare.lower()
    if name == "localhost" or "." not in name or name.endswith(_LOCAL_SUFFIXES):
        raise BlockedUrlError(f"blocked internal name: {host}")
    try:
        infos = socket.getaddrinfo(bare, port, type=socket.SOCK_STREAM, proto=socket.IPPROTO_TCP)
    except (OSError, UnicodeError):
        return bare
    for info in infos:
        ip = info[4][0]
        try:
            if _ip_is_blocked(ip) and not net_proxy.is_fake_ip(ipaddress.ip_address(ip)):
                raise BlockedUrlError(f"blocked internal address for {host}: {ip}")
        except ValueError:
            raise BlockedUrlError(f"unparseable address for {host}: {ip}")
    return bare


class _GuardedRedirectHandler(HTTPRedirectHandler):
    """Re-validate every redirect target so a public URL can't 302 to an
    internal one."""

    def __init__(self, policy=None):
        self.policy = policy

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        validate_public_url(newurl, self.policy)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class _BrowserAgent(BaseHandler):
    """Present a connected host's own browser User-Agent (the one that
    imported its cookies) on every request to that host, redirects included."""

    def __init__(self, agents: dict):
        self.agents = agents

    def https_request(self, req):
        agent = self.agents.get(urllib.parse.urlsplit(req.full_url).hostname or "")
        if agent:
            req.add_header("User-Agent", agent)
        return req


# The (jar, agents) of the browsing session this thread is in, if any.
_session = ContextVar("net_guard_session", default=None)


@contextmanager
def browsing_session():
    """Share one cookie jar between the guarded fetches inside the block,
    the way a browser tab does: what a landing page sets (a session id, an
    institutional-access handshake) reaches the PDF request it advertises.
    Seeded like any fetch's jar; it lives in this thread's context for the
    block only, so nothing carries across operations or users."""
    token = _session.set(browser_profile())
    try:
        yield
    finally:
        _session.reset(token)


def guarded_urlopen(req, timeout=30, policy=None):
    """Drop-in for urllib.request.urlopen that validates the URL (and every
    redirect) against the SSRF guard first. ``req`` may be a str or a Request;
    ``policy`` the proxy setting to route by (net_proxy), the saved one by
    default (the Test of an unsaved one passes it).

    Keep publisher cookies through this fetch's redirects (e.g. Nature's
    institutional-access handshake). Each call gets a fresh, in-memory jar
    unless it runs inside ``browsing_session``: cookies are never shared
    between users. Authenticated PDF operations can seed the jar from that
    user's explicitly connected publisher sessions.
    """
    url = req.full_url if hasattr(req, "full_url") else req
    validate_public_url(url, policy)
    jar, agents = _session.get() or browser_profile()
    opener = net_proxy.build_opener(
        _GuardedRedirectHandler(policy), HTTPCookieProcessor(jar), _BrowserAgent(agents),
        policy=policy, direct=_public_connection, pin=_target,
    )
    try:
        return opener.open(req, timeout=timeout)
    except URLError as exc:
        # AbstractHTTPHandler wraps socket exceptions, including our refusal.
        if isinstance(exc.reason, BlockedUrlError):
            raise exc.reason from exc
        raise
