"""How this server's outbound connections reach the internet: the network
proxy an admin sets in Settings → Administration → Network proxy
(gamma/proxy_settings.py keeps it; docs/dev/network_proxy.md).

``route(host, scheme)`` decides per destination. Loopback and the proxy
itself always go direct. Mode ``none`` goes direct; ``manual`` takes the
configured proxy unless a "Don't use the proxy for" rule matches the host
(a name or domain, an IP address, a range, ``<local>``, ``*``; a range also
matches a name that resolves into it here); ``system`` follows the
environment's or the OS's proxy as urllib reads them (``getproxies``,
``proxy_bypass``). A decision is kept a minute and the system's proxies
half a minute, so a change applies without a restart, and ``system`` with
no proxy found costs no lookup.

``open_tunnel`` opens a stream to a target through an HTTP proxy (CONNECT)
or a SOCKS5 one. The urllib handlers (``RoutedHTTPHandler``,
``RoutedHTTPSHandler``) put each request on its route: an https target,
and any target through SOCKS5, over a tunnel, TLS still naming the URL's
host; an http target through an HTTP proxy in absolute form
(``GET http://host/path``), as HTTP clients send it, when the proxy is
asked for the URL's own host. A proxy builds the Host it forwards from that
target, so an address pinned in its place (``pin``) goes over a tunnel
too, the Host header the URL's. ``install`` makes them
urllib's global opener, which every bare ``urlopen`` reads when it runs;
gamma/net_guard.py builds the guarded fetches' opener from the same
handlers with its own checks (``direct``, ``pin``). The Zotero stream
(``socket_for``), boto3 (``botocore_proxies``) and the local agents' CLIs
(``agent_env``) take the same route their own way.

A ``ProxyError``'s text is shown to people and logged: it names the proxy
as host:port and never holds its sign-in.
"""

import base64
import dataclasses
import http.client
import ipaddress
import socket
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from functools import lru_cache
from typing import NamedTuple

from .logbuf import log

ROUTE_TTL_S = 60          # how long a route decision is kept
SYSTEM_TTL_S = 30         # how long the system's proxies are kept
ROUTES_MAX = 4096         # decisions kept at most; past it they are all dropped
HEAD_MAX = 64 * 1024      # the longest CONNECT answer read
DEFAULT_PORTS = {"http": 80, "https": 443}
_WEB_SCHEMES = {"ws": "http", "wss": "https"}
SOCKS_REPLIES = {1: "general SOCKS server failure", 2: "connection not allowed by ruleset",
                 3: "network unreachable", 4: "host unreachable", 5: "connection refused", 6: "TTL expired",
                 7: "command not supported", 8: "address type not supported"}
# What a VPN in fake-IP mode answers for every name (Clash, sing-box's
# inet4_range and inet6_range): the address it gives is its own, the name
# being resolved behind the proxy. Real unique-local addresses sit in fd00::/8.
FAKE_IP = (ipaddress.ip_network("198.18.0.0/15"), ipaddress.ip_network("fc00::/18"))


class ProxyError(OSError):
    """The proxy could not carry a connection."""


class ProxyUnreachable(ProxyError):
    """No connection to the proxy itself."""


class ProxyAuthError(ProxyError):
    """The proxy wants a sign-in, or refused the one given."""


class ProxyRefused(ProxyError):
    """The proxy would not connect to the target."""


@dataclass(frozen=True)
class ProxyServer:
    scheme: str      # http | socks5 | socks5h (the two SOCKS ones alike: the proxy resolves names)
    host: str        # a name or an IP address, IPv6 without brackets
    port: int
    username: str = ""
    password: str = field(default="", repr=False)

    @property
    def kind(self) -> str:
        return "http" if self.scheme == "http" else "socks5"

    @property
    def hostport(self) -> str:
        return _hostport(self.host, self.port)

    def url(self, credentials: bool = False) -> str:
        """``scheme://host:port``; with ``credentials``, its sign-in in it,
        percent-encoded (for boto3 and the agents' environment only)."""
        auth = ""
        if credentials and self.username:
            auth = urllib.parse.quote(self.username, safe="") + (
                ":" + urllib.parse.quote(self.password, safe="") if self.password else "") + "@"
        return f"{self.scheme}://{auth}{self.hostport}"

    def basic(self) -> str:
        """The Proxy-Authorization of its sign-in."""
        return "Basic " + base64.b64encode(f"{self.username}:{self.password}".encode()).decode("ascii")


class Rule(NamedTuple):
    """One "Don't use the proxy for" entry (proxy_settings.parse_bypass)."""
    kind: str      # all | local | ip | net | name
    value: object  # the address, the network or the name; None for all and local
    text: str      # as the list shows it, and a route's reason names it


class Route(NamedTuple):
    kind: str                   # direct | http | socks5
    server: ProxyServer | None  # the proxy, unless direct
    reason: str = ""            # why direct: loopback, the proxy itself, no proxy, bypass: <rule>, system bypass


@dataclass(frozen=True)
class Policy:
    """The setting in force (proxy_settings.policy); hashable, so it keys the
    route decisions. ``proxy_dns``: the guarded fetches ask the proxy for
    the name instead of the address it resolves to here (gamma/net_guard.py)."""
    mode: str = "system"  # system | none | manual
    server: ProxyServer | None = None
    rules: tuple = ()
    proxy_dns: bool = False


_routes: dict = {}           # (policy, server, host, port) -> (expires, Route)
_system: tuple = (0.0, {})   # (expires, the system's proxies)
_s3_said: set = set()        # what botocore_proxies logged, each once


def current() -> Policy:
    """The saved setting, read now."""
    from .proxy_settings import policy  # local: proxy_settings imports this module

    return policy()


def _hostport(host: str, port: int | None = None) -> str:
    host = f"[{host}]" if ":" in host else host
    return f"{host}:{port}" if port else host


def _address(host: str):
    """``host`` as an IP address (an IPv4-mapped one as IPv4), None for a name."""
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return None
    return (ip.ipv4_mapped or ip) if ip.version == 6 else ip


def _ascii(host: str) -> str:
    return host if host.isascii() else host.encode("idna").decode("ascii")


def is_fake_ip(ip) -> bool:
    """``ip`` (an ipaddress address) is a fake-IP VPN's answer (FAKE_IP)."""
    return any(ip.version == net.version and ip in net for net in FAKE_IP)


# --- routing -------------------------------------------------------------------------

def route(host: str, scheme: str, policy: Policy | None = None, port: int | None = None) -> Route:
    """How a connection to ``host`` for ``scheme`` (http, https, ws, wss)
    goes under ``policy``, the saved setting by default. ``port``: the one
    the URL names, when it names one, which a system no_proxy entry may
    name too (``host:port``, as urllib read them)."""
    policy = policy or current()
    host = host.lower().strip("[]").rstrip(".")
    scheme = _WEB_SCHEMES.get(scheme.lower(), scheme.lower())
    ip = _address(host)
    if host == "localhost" or host.endswith(".localhost") or (ip is not None and ip.is_loopback):
        return Route("direct", None, "loopback")
    if policy.mode == "none":
        return Route("direct", None, "no proxy")
    server = policy.server if policy.mode == "manual" else _system_server(scheme)
    if server is None:
        return Route("direct", None, "no proxy")
    if host == server.host:
        return Route("direct", None, "the proxy itself")
    key, now = (policy, server, host, port), time.monotonic()
    kept = _routes.get(key)
    if kept and kept[0] > now:
        return kept[1]
    if policy.mode == "manual":
        rule = _bypass_rule(policy.rules, host)
        decided = Route("direct", None, f"bypass: {rule}") if rule else Route(server.kind, server)
    else:
        bypass = _system_bypass(host, port)
        decided = Route("direct", None, "system bypass") if bypass else Route(server.kind, server)
    if len(_routes) >= ROUTES_MAX:
        _routes.clear()
    _routes[key] = (now + ROUTE_TTL_S, decided)
    return decided


def _bypass_rule(rules: tuple, host: str) -> str:
    """The first rule that sends ``host`` direct, "" when none does. Only
    with a range among the rules is a name resolved here; one that doesn't
    resolve, or resolves only to fake-IP answers, takes the proxy."""
    ip = _address(host)
    for kind, value, text in rules:
        if (kind == "all" or (kind == "local" and ip is None and "." not in host)
                or (kind == "ip" and ip == value)
                or (kind == "net" and ip is not None and ip.version == value.version and ip in value)
                or (kind == "name" and (host == value or host.endswith("." + value)))):
            return text
    nets = [(value, text) for kind, value, text in rules if kind == "net"]
    if ip is None and nets:
        try:
            infos = socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
        except (OSError, UnicodeError):
            return ""
        for answer in filter(None, (_address(info[4][0]) for info in infos)):
            if is_fake_ip(answer):  # the VPN's own address says nothing of where the name is
                continue
            for net, text in nets:
                if answer.version == net.version and answer in net:
                    return text
    return ""


def _system_proxies() -> dict:
    """The environment's or the OS's proxies as urllib reads them
    (``{scheme: url}``, no_proxy left out), kept SYSTEM_TTL_S."""
    global _system
    expires, proxies = _system
    now = time.monotonic()
    if expires <= now:
        proxies = {k: v for k, v in urllib.request.getproxies().items() if k != "no" and v}
        _system = (now + SYSTEM_TTL_S, proxies)
    return proxies


@lru_cache(maxsize=16)
def _system_entry(entry: str) -> tuple:
    """``(ProxyServer, "")`` for a system proxy Gamma can use, else ``(None,
    the entry without its sign-in)``: another scheme (socks4), or no
    address at all. An ``https://`` entry is the plain HTTP proxy urllib
    took it for (CONNECT in the clear, port 443 unless it names one)."""
    from .proxy_settings import parse_server  # local: proxy_settings imports this module

    try:
        parts = urllib.parse.urlsplit(entry)
        usable = (parts._replace(scheme="http", netloc=parts.netloc + ("" if parts.port else ":443")).geturl()
                  if parts.scheme.lower() == "https" else entry)
        return parse_server(usable, require_port=False), ""
    except ValueError:
        pass
    # the host and port alone: whatever comes before the last @ may be a sign-in, a "/" in it or not
    scheme, sep, rest = entry.partition("://")
    if not sep:
        scheme, rest = "http", entry
    try:
        parts = urllib.parse.urlsplit(f"{scheme}://{rest.rpartition('@')[2]}")
        return None, f"{parts.scheme}://{_hostport(parts.hostname or '', parts.port)}"
    except ValueError:
        return None, "an unreadable address"


def _system_server(scheme: str) -> ProxyServer | None:
    proxies = _system_proxies()
    entry = proxies.get(scheme) or proxies.get("all")
    return _system_entry(entry)[0] if entry else None


def _system_bypass(host: str, port: int | None = None) -> bool:
    try:
        return bool(urllib.request.proxy_bypass(_hostport(host, port)))
    except Exception:  # noqa: BLE001 — an OS setting that cannot be read bypasses nothing
        return False


def system_view() -> dict:
    """What ``system`` mode finds, for the pane: ``proxy`` (what an https
    destination uses, without its sign-in), ``http`` (the proxy http
    destinations use when it is another, or https has none; "" else),
    ``from`` (``environment`` when a *_proxy variable is set, else ``os``)
    and ``unsupported`` (a proxy found that Gamma cannot use: it connects
    directly)."""
    proxies = _system_proxies()
    entries = [proxies.get("https") or proxies.get("all"), proxies.get("http") or proxies.get("all")]
    if not any(entries):
        return {"proxy": "", "http": "", "from": "", "unsupported": ""}
    (secure, unsupported), (plain, plain_unsupported) = (_system_entry(e) if e else (None, "") for e in entries)
    proxy, http = (server.url() if server else "" for server in (secure, plain))
    env = any(k != "no" and v for k, v in urllib.request.getproxies_environment().items())
    return {"proxy": proxy, "http": http if http != proxy else "", "from": "environment" if env else "os",
            "unsupported": unsupported or plain_unsupported}


# --- tunnels -------------------------------------------------------------------------

def _dial(server: ProxyServer, timeout) -> socket.socket:
    try:
        return socket.create_connection((server.host, server.port), timeout)
    except OSError as e:
        why = e.strerror or str(e) or type(e).__name__
        raise ProxyUnreachable(f"Couldn't reach the proxy at {server.hostport} ({why}). Is it running?") from e


def _recv(sock: socket.socket, n: int, server: ProxyServer) -> bytes:
    data = b""
    while len(data) < n:
        chunk = sock.recv(n - len(data))
        if not chunk:
            raise ProxyError(f"The proxy at {server.hostport} closed the connection")
        data += chunk
    return data


def open_tunnel(route: Route, target: str, port: int, timeout) -> socket.socket:
    """A stream to ``target``:``port`` (a name or an IP address) through
    ``route``'s proxy, keeping ``timeout`` as ``socket.create_connection``
    leaves it. ProxyError when the proxy cannot be reached or says no."""
    server = route.server
    sock = _dial(server, timeout)
    try:
        (_connect_http if route.kind == "http" else _connect_socks5)(sock, server, target, port)
    except BaseException:
        sock.close()
        raise
    return sock


def _sign_in_refused(server: ProxyServer) -> ProxyAuthError:
    """An HTTP proxy's 407, to a CONNECT or to a request sent to it in full."""
    return ProxyAuthError(f"The proxy at {server.hostport} " +
                          ("refused the sign-in" if server.username else "asks for a sign-in (407)"))


def _connect_http(sock: socket.socket, server: ProxyServer, target: str, port: int) -> None:
    where = _hostport(_ascii(target), port)
    head = f"CONNECT {where} HTTP/1.1\r\nHost: {where}\r\n"
    if server.username:
        head += f"Proxy-Authorization: {server.basic()}\r\n"
    sock.sendall((head + "\r\n").encode("ascii"))
    answer = b""
    # a byte at a time: nothing of the tunnel is read here. A bare \n ends
    # a line too, as http.client reads it (\n\r\n and \n\n cover \r\n\r\n)
    while not answer.endswith((b"\n\n", b"\n\r\n")):
        if len(answer) >= HEAD_MAX:
            raise ProxyError(f"The proxy at {server.hostport} sent an answer too long to read")
        answer += _recv(sock, 1, server)
    status = answer.split(b"\n", 1)[0].rstrip(b"\r").decode("latin-1").split(" ", 2)
    if (len(status) < 2 or not status[0].startswith("HTTP/")
            or not (status[1].isascii() and status[1].isdigit())):
        raise ProxyError(f"The proxy at {server.hostport} didn't answer as an HTTP proxy")
    code = int(status[1])
    if 200 <= code < 300:
        return
    if code == 407:
        raise _sign_in_refused(server)
    phrase = "".join(c for c in status[2] if c.isprintable())[:80] if len(status) > 2 else ""
    raise ProxyRefused(f"The proxy at {server.hostport} refused the connection to {where} "
                       f"({' '.join(filter(None, (str(code), phrase)))})")


def _connect_socks5(sock: socket.socket, server: ProxyServer, target: str, port: int) -> None:
    """RFC 1928, with RFC 1929's username and password when the proxy has a
    sign-in: an IP address is asked for as one, a name for the proxy to
    resolve."""
    not_socks = f"The proxy at {server.hostport} didn't answer as a SOCKS5 proxy"
    sock.sendall(b"\x05\x02\x00\x02" if server.username else b"\x05\x01\x00")
    version, method = _recv(sock, 2, server)
    if version != 5:
        raise ProxyError(not_socks)
    if method == 2 and server.username:
        user, password = server.username.encode(), server.password.encode()
        sock.sendall(b"\x01" + bytes([len(user)]) + user + bytes([len(password)]) + password)
        if _recv(sock, 2, server)[1] != 0:
            raise ProxyAuthError(f"The proxy at {server.hostport} refused the sign-in")
    elif method != 0:
        raise ProxyAuthError(f"The proxy at {server.hostport} " +
                             ("refused the sign-in" if server.username else "asks for a sign-in"))
    try:
        ip = ipaddress.ip_address(target)
        address = (b"\x01" if ip.version == 4 else b"\x04") + ip.packed
    except ValueError:
        name = _ascii(target).encode("ascii")
        if len(name) > 255:
            raise ProxyError(f"The name {target[:80]} is too long for a SOCKS5 proxy") from None
        address = b"\x03" + bytes([len(name)]) + name
    sock.sendall(b"\x05\x01\x00" + address + port.to_bytes(2, "big"))
    version, reply, _, kind = _recv(sock, 4, server)
    if version != 5:
        raise ProxyError(not_socks)
    if reply:
        raise ProxyRefused(f"The proxy at {server.hostport} refused the connection to {_hostport(target, port)} "
                           f"({SOCKS_REPLIES.get(reply, f'SOCKS5 reply {reply}')})")
    if kind not in (1, 3, 4):
        raise ProxyError(not_socks)
    size = 4 if kind == 1 else 16 if kind == 4 else _recv(sock, 1, server)[0]
    _recv(sock, size + 2, server)  # the address and port the proxy bound, unused


def connect(host: str, port: int, timeout, *, scheme: str, policy: Policy | None = None) -> socket.socket:
    """A stream to ``host``:``port`` on its route (a trusted destination:
    a proxy is asked for the name)."""
    way = route(host, scheme, policy, port)
    if way.kind == "direct":
        return socket.create_connection((host, port), timeout)
    return open_tunnel(way, host, port, timeout)


def socket_for(url: str, timeout, policy: Policy | None = None) -> socket.socket:
    """The stream a WebSocket of ``url`` (ws/wss) opens on (gamma/zotero_stream.py)."""
    parts = urllib.parse.urlsplit(url)
    scheme = _WEB_SCHEMES.get(parts.scheme.lower(), parts.scheme.lower())
    return connect(parts.hostname or "", parts.port or DEFAULT_PORTS[scheme], timeout, scheme=scheme, policy=policy)


# --- urllib --------------------------------------------------------------------------

class _Connection:
    """The routed handlers' connections: the socket comes from the route's
    ``dial``; an http request sent to an HTTP proxy itself names its target
    in full (``absolute``, ``http://host[:port]``, the URL's own host, which
    the proxy builds the forwarded Host from) and carries the proxy's
    sign-in. The Host header is the URL's, as urllib sets it."""

    def __init__(self, *args, dial, absolute: str = "", proxy_auth: str = "", **kwargs):
        super().__init__(*args, **kwargs)
        self._create_connection = dial
        self._absolute, self._proxy_auth = absolute, proxy_auth

    def putrequest(self, method, url, *args, **kwargs):
        super().putrequest(method, self._absolute + (url or "/") if self._absolute else url, *args, **kwargs)
        if self._proxy_auth:
            self.putheader("Proxy-Authorization", self._proxy_auth)


class _HTTPConnection(_Connection, http.client.HTTPConnection):
    pass


class _HTTPSConnection(_Connection, http.client.HTTPSConnection):
    pass


class _Routed:
    """What the two handlers share. ``policy``: the setting to route by,
    the saved one (read per request) by default. ``direct(address, timeout,
    source_address)`` connects a direct route (``socket.create_connection``
    by default); ``pin(policy, route, host, port)`` is the host a proxy is
    asked for (the URL's own by default). net_guard passes its own two."""

    def __init__(self, *args, policy: Policy | None = None, direct=None, pin=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.policy, self.direct, self.pin = policy, direct or socket.create_connection, pin

    def _routed_open(self, connection, req, **kwargs):
        url = urllib.parse.urlsplit(req.full_url)
        scheme, host = url.scheme.lower(), url.hostname
        if not host:
            raise urllib.error.URLError("no host given")
        port = url.port or DEFAULT_PORTS[scheme]
        policy = self.policy or current()
        way = route(host, scheme, policy, url.port)
        if way.kind == "direct":
            return self.do_open(connection, req, dial=self.direct, **kwargs)
        target = self.pin(policy, way, host, port) if self.pin else host
        # Sent to the proxy itself, naming the target in full, only when the
        # proxy is asked for the URL's own host: it forwards the Host the
        # target names, so a pinned address goes over a tunnel (CONNECT
        # address:80) with the URL's Host instead
        absolute = way.kind == "http" and scheme == "http" and target == host
        if absolute:
            kwargs.update(absolute="http://" + _hostport(_ascii(target), port if port != 80 else None),
                          proxy_auth=way.server.basic() if way.server.username else "")

        def dial(address, timeout, source_address=None):
            return _dial(way.server, timeout) if absolute else open_tunnel(way, target, port, timeout)

        response = self.do_open(connection, req, dial=dial, **kwargs)
        if absolute and response.status == 407:  # only a proxy sends it: the proxy refused, as to a CONNECT
            response.close()
            raise urllib.error.URLError(_sign_in_refused(way.server))
        return response


class RoutedHTTPHandler(_Routed, urllib.request.HTTPHandler):
    def http_open(self, req):
        return self._routed_open(_HTTPConnection, req)


_NO_CONTEXT = object()  # RoutedHTTPSHandler's context until its first https request


class RoutedHTTPSHandler(_Routed, urllib.request.HTTPSHandler):
    """Its TLS context, unless one is given, is made at its first https
    request rather than as it is built: an opener built at import
    (zotero_web's, sync_engine's) then loads the CAs app.py's certifi
    fallback names."""

    def __init__(self, *args, context=None, **kwargs):
        super().__init__(*args, context=_NO_CONTEXT if context is None else context, **kwargs)

    def https_open(self, req):
        if self._context is _NO_CONTEXT:
            self._context = http.client._create_https_context(http.client.HTTPSConnection._http_vsn)
        return self._routed_open(_HTTPSConnection, req, context=self._context)


def build_opener(*handlers, policy: Policy | None = None, direct=None, pin=None):
    """urllib's ``build_opener(*handlers)`` with the routed handlers in
    place of its own and of its environment proxies."""
    return urllib.request.build_opener(
        urllib.request.ProxyHandler({}), RoutedHTTPHandler(policy=policy, direct=direct, pin=pin),
        RoutedHTTPSHandler(policy=policy, direct=direct, pin=pin), *handlers)


def install() -> None:
    """Route every bare ``urlopen`` of this process: they read urllib's
    global opener when they run, ``from urllib.request import urlopen``
    ones too."""
    urllib.request.install_opener(build_opener())


# --- other clients -------------------------------------------------------------------

def agent_env(env: dict, policy: Policy | None = None) -> dict:
    """``env`` (a local agent CLI's, gamma/local_agents/procs.py) with the
    proxy variables of the setting: ``manual`` sets HTTP(S)_PROXY with the
    sign-in, or for SOCKS5 ALL_PROXY alone (as socks5h: an HTTP client
    reading HTTP(S)_PROXY speaks to an HTTP proxy only), and NO_PROXY from
    the bypass list; ``none`` removes every *_proxy variable; ``system``
    leaves them, except that a proxy only the OS knows (its settings, no
    variable) is passed on like a manual one, NO_PROXY from DEFAULT_BYPASS
    (the OS's own exceptions are not read)."""
    policy = policy or current()
    if policy.mode == "system" and not any(v and k.lower().endswith("_proxy") for k, v in env.items()):
        from .proxy_settings import DEFAULT_BYPASS, parse_bypass  # local: proxy_settings imports this module

        server, rules = _system_server("https"), parse_bypass(DEFAULT_BYPASS)
    elif policy.mode == "manual":
        server, rules = policy.server, policy.rules
    elif policy.mode == "none":
        return {k: v for k, v in env.items() if not k.lower().endswith("_proxy")}
    else:
        return env
    if server is None:
        return env
    env = {k: v for k, v in env.items() if not k.lower().endswith("_proxy")}
    url = (server if server.kind == "http" else dataclasses.replace(server, scheme="socks5h")).url(credentials=True)
    no_proxy = ["localhost", "127.0.0.1", "::1"]
    for kind, value, text in rules:
        # a name matches itself and its subdomains, which tools spell two ways
        no_proxy += [value, "." + value] if kind == "name" else [] if kind == "local" else [text]
    values = {"HTTP_PROXY": url, "HTTPS_PROXY": url} if server.kind == "http" else {"ALL_PROXY": url}
    values["NO_PROXY"] = ",".join(dict.fromkeys(no_proxy))
    for name, value in values.items():
        env[name] = value
        if sys.platform != "win32":  # Windows has one variable whatever its case
            env[name.lower()] = value
    return env


def botocore_proxies(endpoint_url: str | None, policy: Policy | None = None) -> dict:
    """boto3's ``Config(proxies=...)`` for a bucket at ``endpoint_url``
    (AWS when None): the HTTP proxy of its route, else {} (direct: an empty
    dict also keeps botocore from reading the environment). boto3 cannot
    use a SOCKS5 proxy: that route connects directly. Nor can it send a
    sign-in that isn't ASCII (it fails every request) or a user name
    without a password: the proxy is then given without one. Each said
    once in the log."""
    parts = urllib.parse.urlsplit(endpoint_url or "https://s3.amazonaws.com")
    way = route(parts.hostname or "", parts.scheme or "https", policy, parts.port)
    said = ""
    if way.kind == "http":
        server = way.server
        if server.username and not (server.password and (server.username + server.password).isascii()):
            said = (f"[proxy] S3 cannot sign in to the proxy at {server.hostport} (boto3 sends only an ASCII user "
                    f"name with a password): the off-site copies go through it without a sign-in")
    elif way.kind == "socks5":
        said = (f"[proxy] S3 cannot go through the SOCKS5 proxy at {way.server.hostport}: the off-site copies "
                f"connect to {parts.hostname} directly")
    if said and said not in _s3_said:
        _s3_said.add(said)
        log.info(said)
    if way.kind == "http":
        url = way.server.url(credentials=not said)
        return {"http": url, "https": url}
    return {}


# --- answers that refuse this network ----------------------------------------------
# Some 403s are about the network a request came from, not the request or
# its credential: OpenAI refuses a country or region it doesn't serve, and
# Cloudflare in front of a service challenges or blocks an address or a
# client it distrusts. A browser behind a proxy or VPN then works while
# this server, connecting another way, is refused, and such an answer must
# not read as a rejected key or an expired sign-in: the remedy is the proxy.

ERROR_BODY_MAX = 8192
REFUSED_HINT = ("If your browser reaches it through a proxy or VPN, give this server the same one "
                "in Settings → Network proxy.")


def error_body(error: urllib.error.HTTPError) -> bytes:
    """The first ERROR_BODY_MAX bytes of an HTTP error answer's body, read
    once and kept on the error, so each reader (its message, its OAuth
    code, refused_network) sees them."""
    body = getattr(error, "_gamma_body", None)
    if body is None:
        try:
            body = error.read(ERROR_BODY_MAX) or b""
        except Exception:
            body = b""
        error._gamma_body = body
    return body


def refused_network(error) -> str:
    """Why an answer refuses this server's network rather than the request,
    "" when it doesn't: a 403 naming OpenAI's region code
    (``unsupported_country_region_territory``), or a 403 of Cloudflare's
    own (``cf-mitigated``, or a page where the service behind it answers
    JSON). Anything but an HTTPError is "": an unreachable service is not
    refused."""
    if not isinstance(error, urllib.error.HTTPError) or error.code != 403:
        return ""
    headers = {k.lower(): v for k, v in (error.headers or {}).items()}
    body = error_body(error).decode("utf-8", "replace").lstrip()
    if "unsupported_country" in body:
        return "it doesn't serve the country or region this server connects from"
    cloudflare = "cf-ray" in headers or "cloudflare" in headers.get("server", "").lower()
    if headers.get("cf-mitigated") or (cloudflare and not body.startswith(("{", "["))):
        return "Cloudflare in front of it refused this server's connection"
    return ""
