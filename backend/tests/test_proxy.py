"""The network proxy (gamma/proxy_settings.py, gamma/net_proxy.py): parsing,
routing, the HTTP and SOCKS5 tunnels against stand-in proxies on 127.0.0.1,
the trusted and the guarded fetches through them, the admin API and its
Test, the local agents' environment, boto3's proxies and the log's scrub."""

import base64
import functools
import http.client
import io
import ipaddress
import json
import os
import socket
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

import pytest

from conftest import drop_user, login, make_user
from gamma import db, net_guard, net_proxy, proxy_settings
from gamma.logbuf import scrub
from gamma.net_proxy import Policy, ProxyServer, Route
from gamma.proxy_settings import DEFAULT_BYPASS, parse_bypass, parse_server
from gamma.publisher_sessions import unseal

PUBLIC = "93.184.216.34"


@pytest.fixture(autouse=True)
def fresh_routes(monkeypatch):
    """Each test reads the system's proxies anew and decides every route
    again; neither the environment nor the OS has a proxy of its own."""
    monkeypatch.setattr(net_proxy, "_system", (0.0, {}))
    monkeypatch.setattr(net_proxy, "_routes", {})
    monkeypatch.setattr(urllib.request, "getproxies", lambda: {})
    for name in [name for name in os.environ if name.lower().endswith("_proxy")]:
        monkeypatch.delenv(name)


@pytest.fixture
def dns(monkeypatch):
    """Local DNS as the test sets it, ``{name: address or [addresses]}``; a
    name it lacks does not resolve, an IP address and localhost are
    themselves."""
    answers, real = {}, socket.getaddrinfo

    def resolve(host, port, *args, **kwargs):
        if host in answers:
            found = answers[host] if isinstance(answers[host], list) else [answers[host]]
            return [(socket.AF_INET6 if ":" in ip else socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "",
                     (ip, port or 0)) for ip in found]
        try:
            ipaddress.ip_address(host)
        except ValueError:
            if host != "localhost":
                raise socket.gaierror(socket.EAI_NONAME, "Name or service not known") from None
        return real(host, port, *args, **kwargs)

    monkeypatch.setattr(socket, "getaddrinfo", resolve)
    return answers


def _recv(conn, n):
    data = b""
    while len(data) < n:
        chunk = conn.recv(n - len(data))
        if not chunk:
            raise ConnectionError("closed")
        data += chunk
    return data


def _head(conn):
    data = b""
    while not data.endswith(b"\r\n\r\n"):
        data += _recv(conn, 1)
    return data


OK = b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\nConnection: close\r\n\r\nok"


class FakeProxy:
    """A proxy on 127.0.0.1 in threads: ``serve(proxy, conn)`` speaks for
    each connection and puts what it got in ``seen``. Closed at the end of
    the test (``_close_proxies``)."""

    made: list = []

    def __init__(self, serve):
        self.listener = socket.create_server(("127.0.0.1", 0))
        self.port = self.listener.getsockname()[1]
        self.serve, self.seen = serve, []
        FakeProxy.made.append(self)
        threading.Thread(target=self._accept, daemon=True).start()

    def _accept(self):
        while True:
            try:
                conn, _ = self.listener.accept()
            except OSError:
                return
            threading.Thread(target=self._one, args=(conn,), daemon=True).start()

    def _one(self, conn):
        with conn:
            conn.settimeout(5)
            try:
                self.serve(self, conn)
            except OSError:
                pass

    def server(self, scheme="http", username="", password=""):
        return ProxyServer(scheme, "127.0.0.1", self.port, username, password)


@pytest.fixture(autouse=True)
def _close_proxies():
    yield
    while FakeProxy.made:
        FakeProxy.made.pop().listener.close()


def http_proxy(status=200):
    """An HTTP proxy: a CONNECT answered with ``status`` (then the request
    inside the tunnel, the tests' TLS leaving it plain), any other request
    answered as the target."""
    def serve(proxy, conn):
        head = _head(conn)
        proxy.seen.append(head)
        if head.startswith(b"CONNECT"):
            if status != 200:
                reason = {403: "Forbidden", 407: "Proxy Authentication Required"}[status]
                conn.sendall(f"HTTP/1.1 {status} {reason}\r\nContent-Length: 0\r\n\r\n".encode())
                return
            conn.sendall(b"HTTP/1.1 200 Connection established\r\n\r\n")
            proxy.seen.append(_head(conn))
        conn.sendall(OK)
    return serve


def socks5_proxy(username="", password="", reply=0):
    """A SOCKS5 proxy wanting ``username``/``password`` when given, answering
    a CONNECT with ``reply``; ``seen`` gets (address type, address, port)
    and then the request on the stream."""
    def serve(proxy, conn):
        _, count = _recv(conn, 2)
        methods = _recv(conn, count)
        if username:
            if 2 not in methods:
                conn.sendall(b"\x05\xff")
                return
            conn.sendall(b"\x05\x02")
            _, size = _recv(conn, 2)
            user = _recv(conn, size)
            secret = _recv(conn, _recv(conn, 1)[0])
            good = (user, secret) == (username.encode(), password.encode())
            conn.sendall(b"\x01\x00" if good else b"\x01\x01")
            if not good:
                return
        else:
            conn.sendall(b"\x05\x00")
        _, _, _, kind = _recv(conn, 4)
        if kind == 3:
            address = _recv(conn, _recv(conn, 1)[0]).decode()
        else:
            address = str(ipaddress.ip_address(_recv(conn, 4 if kind == 1 else 16)))
        proxy.seen.append((kind, address, int.from_bytes(_recv(conn, 2), "big")))
        conn.sendall(b"\x05" + bytes([reply]) + b"\x00\x01" + bytes(6))
        if not reply:
            proxy.seen.append(_head(conn))
            conn.sendall(OK)
    return serve


class PlainTLS:
    """A TLS context that leaves the stream plain, keeping the names asked for."""

    def __init__(self):
        self.names = []

    def wrap_socket(self, sock, server_hostname):
        self.names.append(server_hostname)
        return sock


def manual(server, bypass="", proxy_dns=False):
    return Policy("manual", server, parse_bypass(bypass), proxy_dns)


# --- parsing -------------------------------------------------------------------------

def test_parse_server_accepts_and_moves_the_sign_in():
    assert parse_server(" 127.0.0.1:7890 ") == ProxyServer("http", "127.0.0.1", 7890)
    assert parse_server("SOCKS5H://Proxy.Example:1080/") == ProxyServer("socks5h", "proxy.example", 1080)
    assert parse_server("http://[::1]:3128").url() == "http://[::1]:3128"
    server = parse_server("http://al%40ice:p%3Ass@proxy.corp:8080")
    assert (server.username, server.password, server.url()) == ("al@ice", "p:ss", "http://proxy.corp:8080")
    assert server.url(credentials=True) == "http://al%40ice:p%3Ass@proxy.corp:8080"
    assert "p:ss" not in repr(server)
    assert parse_server("socks5://h", require_port=False).port == 1080  # a system entry


@pytest.mark.parametrize("text,words", [
    ("", "Enter the proxy's address"),
    ("https://alice:hunter2@proxy:443", "https:// proxy isn't supported yet"),
    ("socks4://alice:hunter2@proxy:1080", "socks4:// proxy isn't supported"),
    ("http://alice:hunter2@proxy", "Add the proxy's port"),
    ("http://alice:hunter2@proxy:0", "Add the proxy's port"),
    ("http://alice:hunter2@proxy:99999", "isn't a proxy address"),
    ("http://alice:hunter2@proxy:8080/path", "without a path"),
    ("http://alice:hunter2@proxy:8080?x=1", "without a path"),
    ("alice:hunter2@proxy:8080/x://", "isn't a proxy address"),
    ("http://alice:hunter2@pro xy:1", "isn't a proxy address"),
    ("http://alice:hunt%0aer2@proxy:1", "control characters"),
])
def test_parse_server_refuses_without_echoing_a_password(text, words):
    with pytest.raises(ValueError) as refused:
        parse_server(text)
    assert words in str(refused.value) and "hunter2" not in str(refused.value) and "alice" not in str(refused.value)


def test_parse_bypass_normalizes_and_refuses():
    assert ", ".join(rule.text for rule in parse_bypass(DEFAULT_BYPASS)) == DEFAULT_BYPASS
    rules = parse_bypass("Example.COM. , .corp.example\n*.lan [::1] 10.1.2.3/8 * <local> example.com")
    assert [rule.text for rule in rules] == ["example.com", ".corp.example", "*.lan", "::1", "10.0.0.0/8", "*",
                                             "<local>"]
    assert [rule.kind for rule in rules] == ["name", "name", "name", "ip", "net", "all", "local"]
    for entry in ("host:8080", "http://x", "a/b", "ex@mple", "*example.com"):
        with pytest.raises(ValueError, match="isn’t a host, domain, IP address or IP range"):
            parse_bypass(f"ok.example, {entry}")


# --- routing -------------------------------------------------------------------------

def test_route_by_the_manual_rules(dns):
    server = ProxyServer("http", "10.0.0.2", 3128)
    policy = manual(server, "example.com, 192.168.0.0/16, <local>, 203.0.113.7")
    dns.update({"nas.example.org": "192.168.1.10", "api.openai.com": "104.18.1.1"})
    for host in ("localhost", "a.localhost", "127.5.5.5", "::1", "::ffff:127.0.0.1", "[::1]"):
        assert net_proxy.route(host, "https", policy) == Route("direct", None, "loopback"), host
    assert net_proxy.route("10.0.0.2", "https", policy).reason == "the proxy itself"
    expected = {"example.com": "bypass: example.com", "www.example.com": "bypass: example.com",
                "intranet": "bypass: <local>", "203.0.113.7": "bypass: 203.0.113.7",
                "192.168.1.5": "bypass: 192.168.0.0/16", "nas.example.org": "bypass: 192.168.0.0/16"}
    for host, reason in expected.items():
        assert net_proxy.route(host, "https", policy) == Route("direct", None, reason), host
    for host in ("notexample.com", "api.openai.com", "unresolvable.example", "10.9.9.9"):
        assert net_proxy.route(host, "wss", policy) == Route("http", server), host
    assert net_proxy.route("anything.example", "http", manual(server, "*")).reason == "bypass: *"
    assert net_proxy.route("api.openai.com", "https", Policy("none")) == Route("direct", None, "no proxy")


def test_a_name_is_resolved_only_for_a_range_and_the_answer_is_kept(monkeypatch):
    asked = []
    monkeypatch.setattr(socket, "getaddrinfo", lambda host, *a, **kw: asked.append(host) or [
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("104.18.1.1", 0))])
    server = ProxyServer("socks5", "10.0.0.2", 1080)
    net_proxy.route("api.openai.com", "https", manual(server, "example.com, <local>"))
    assert asked == []
    with_range = manual(server, "10.0.0.0/8")
    assert net_proxy.route("api.openai.com", "https", with_range).kind == "socks5"
    assert net_proxy.route("api.openai.com", "http", with_range).kind == "socks5"
    assert asked == ["api.openai.com"]


def test_route_by_the_systems_proxy(monkeypatch):
    bypassed, asked = [], []
    monkeypatch.setattr(urllib.request, "getproxies", lambda: {
        "https": "http://user:pw@10.0.0.9:8080", "http": "socks5://10.0.0.8:1080", "no": "skip.example"})
    monkeypatch.setattr(urllib.request, "proxy_bypass", lambda host: bypassed.append(host) or host == "skip.example")
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **kw: asked.append(a) or [])
    https = net_proxy.route("api.openai.com", "https", Policy())
    assert https.kind == "http" and https.server == ProxyServer("http", "10.0.0.9", 8080, "user", "pw")
    assert net_proxy.route("api.openai.com", "ws", Policy()).server.hostport == "10.0.0.8:1080"
    assert net_proxy.route("skip.example", "https", Policy()) == Route("direct", None, "system bypass")
    assert net_proxy.route("10.0.0.9", "https", Policy()) == Route("direct", None, "the proxy itself")
    assert asked == [] and "api.openai.com" in bypassed
    assert net_proxy.system_view() == {"proxy": "http://10.0.0.9:8080", "http": "socks5://10.0.0.8:1080",
                                       "from": "os", "unsupported": ""}


def test_system_mode_without_a_proxy_costs_nothing(monkeypatch):
    monkeypatch.setattr(urllib.request, "proxy_bypass", lambda host: pytest.fail("no bypass to read"))
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **kw: pytest.fail("no lookup"))
    assert net_proxy.route("api.openai.com", "https", Policy()) == Route("direct", None, "no proxy")
    assert net_proxy.system_view() == {"proxy": "", "http": "", "from": "", "unsupported": ""}


def test_an_unusable_system_proxy_connects_directly(monkeypatch):
    monkeypatch.setattr(urllib.request, "getproxies", lambda: {"https": "socks4://bob:pw@10.0.0.1:1080"})
    monkeypatch.setenv("HTTPS_PROXY", "socks4://bob:pw@10.0.0.1:1080")
    assert net_proxy.route("api.openai.com", "https", Policy()) == Route("direct", None, "no proxy")
    assert net_proxy.system_view() == {"proxy": "", "http": "", "from": "environment",
                                       "unsupported": "socks4://10.0.0.1:1080"}


@pytest.mark.parametrize("entry", ["http://alice:1234/abcd@proxy.corp:3128", "http://alice:p@ss/word@proxy.corp:3128",
                                   "http://alice:4321#zz@proxy.corp:3128", "alice:99?x@proxy.corp:3128"])
def test_an_unreadable_system_proxy_is_shown_without_its_sign_in(monkeypatch, entry):
    monkeypatch.setattr(urllib.request, "getproxies", lambda: {"https": entry})
    unsupported = net_proxy.system_view()["unsupported"]
    assert unsupported == "http://proxy.corp:3128"
    assert not any(part in unsupported for part in ("alice", "1234", "abcd", "word", "4321", "99"))


def test_an_https_system_proxy_is_the_plain_proxy_urllib_took_it_for(monkeypatch):
    monkeypatch.setattr(urllib.request, "getproxies", lambda: {"https": "https://proxy:3129",
                                                                "http": "https://u:p@10.0.0.8"})
    assert net_proxy.route("api.openai.com", "https", Policy()) == Route("http", ProxyServer("http", "proxy", 3129))
    assert net_proxy.route("example.org", "http", Policy()).server == ProxyServer("http", "10.0.0.8", 443, "u", "p")
    assert net_proxy.system_view() == {"proxy": "http://proxy:3129", "http": "http://10.0.0.8:443", "from": "os",
                                       "unsupported": ""}
    with pytest.raises(ValueError, match="An https:// proxy isn't supported yet"):  # never under Manual
        parse_server("https://proxy:3129")


def test_a_system_proxy_for_http_alone_is_shown_as_such(monkeypatch):
    monkeypatch.setattr(urllib.request, "getproxies", lambda: {"http": "http://10.0.0.1:3128"})
    assert net_proxy.route("api.openai.com", "https", Policy()) == Route("direct", None, "no proxy")
    assert net_proxy.route("example.org", "http", Policy()).server.hostport == "10.0.0.1:3128"
    assert net_proxy.system_view() == {"proxy": "", "http": "http://10.0.0.1:3128", "from": "os", "unsupported": ""}


def test_a_system_no_proxy_entry_with_a_port(monkeypatch):
    monkeypatch.setattr(urllib.request, "getproxies", lambda: {"http": "http://10.0.0.5:3128",
                                                                "https": "http://10.0.0.5:3128"})
    monkeypatch.setenv("no_proxy", "git.example:8080")  # read by urllib's proxy_bypass, as before
    assert net_proxy.route("git.example", "http", Policy(), 8080) == Route("direct", None, "system bypass")
    assert net_proxy.route("git.example", "http", Policy()).kind == "http"  # the port its scheme gives
    assert net_proxy.route("git.example", "https", Policy(), 9443).kind == "http"


# --- tunnels -------------------------------------------------------------------------

def test_http_connect_tunnel():
    proxy = FakeProxy(http_proxy(200))
    server = proxy.server(username="u", password="p")
    with net_proxy.open_tunnel(Route("http", server), "example.org", 443, 5) as sock:
        sock.sendall(b"GET / HTTP/1.1\r\nHost: example.org\r\n\r\n")
        assert sock.recv(64).startswith(b"HTTP/1.1 200 OK") and sock.gettimeout() == 5
    connect = proxy.seen[0]
    assert connect.startswith(b"CONNECT example.org:443 HTTP/1.1\r\nHost: example.org:443\r\n")
    assert b"Proxy-Authorization: Basic " + base64.b64encode(b"u:p") + b"\r\n" in connect
    with net_proxy.open_tunnel(Route("http", proxy.server()), "2001:db8::1", 443, 5):
        pass
    assert proxy.seen[-1].startswith(b"CONNECT [2001:db8::1]:443 HTTP/1.1\r\n")


@pytest.mark.parametrize("status,username,error,words", [
    (407, "", net_proxy.ProxyAuthError, "asks for a sign-in (407)"),
    (407, "alice", net_proxy.ProxyAuthError, "refused the sign-in"),
    (403, "", net_proxy.ProxyRefused, "refused the connection to example.org:443 (403 Forbidden)"),
])
def test_http_connect_refusals(status, username, error, words):
    proxy = FakeProxy(http_proxy(status))
    with pytest.raises(error) as refused:
        net_proxy.open_tunnel(Route("http", proxy.server(username=username, password="hunter2")), "example.org", 443, 5)
    assert str(refused.value) == f"The proxy at 127.0.0.1:{proxy.port} {words}"


@pytest.mark.parametrize("answer", [b"HTTP/1.0 200 Connection established\n\n",
                                    b"HTTP/1.0 200 Connection established\n\r\n",
                                    b"HTTP/1.1 200 OK\r\nVia: x\n\r\n"])
def test_a_connect_answer_with_bare_line_ends_opens_the_tunnel(answer):
    proxy = FakeProxy(lambda proxy, conn: (_head(conn), conn.sendall(answer + b"tunnel"), conn.recv(1)))
    with net_proxy.open_tunnel(Route("http", proxy.server()), "example.org", 443, 2) as sock:
        assert sock.recv(6) == b"tunnel"  # nothing of the tunnel read with the answer


@pytest.mark.parametrize("answer", [b"HTTP/1.1 \xb2 weird\r\n\r\n", b"SSH-2.0-OpenSSH\r\n\r\n"])
def test_a_connect_answer_that_isnt_http(answer):
    proxy = FakeProxy(lambda proxy, conn: (_head(conn), conn.sendall(answer)))
    with pytest.raises(net_proxy.ProxyError, match="didn't answer as an HTTP proxy"):
        net_proxy.open_tunnel(Route("http", proxy.server()), "example.org", 443, 2)


def test_an_unreachable_proxy():
    with pytest.raises(net_proxy.ProxyUnreachable, match=r"Couldn't reach the proxy at 127\.0\.0\.1:1 \(.+\)\. Is it"):
        net_proxy.open_tunnel(Route("http", ProxyServer("http", "127.0.0.1", 1, "u", "hunter2")), "x.org", 443, 5)


def test_socks5_tunnel():
    proxy = FakeProxy(socks5_proxy())
    for target, port in (("example.org", 443), ("93.184.216.34", 80), ("2001:db8::1", 443)):
        with net_proxy.open_tunnel(Route("socks5", proxy.server("socks5h")), target, port, 5) as sock:
            sock.sendall(b"GET / HTTP/1.1\r\n\r\n")
            assert sock.recv(64).startswith(b"HTTP/1.1 200 OK")
    assert [seen for seen in proxy.seen if isinstance(seen, tuple)] == [
        (3, "example.org", 443), (1, "93.184.216.34", 80), (4, "2001:db8::1", 443)]


def test_socks5_sign_in_and_refusals():
    signed = FakeProxy(socks5_proxy("alice", "s3cret"))
    with net_proxy.open_tunnel(Route("socks5", signed.server("socks5", "alice", "s3cret")), "example.org", 443, 5):
        pass
    assert signed.seen[0] == (3, "example.org", 443)
    for server, words in ((signed.server("socks5", "alice", "hunter2"), "refused the sign-in"),
                          (signed.server("socks5"), "asks for a sign-in")):
        with pytest.raises(net_proxy.ProxyAuthError) as refused:
            net_proxy.open_tunnel(Route("socks5", server), "example.org", 443, 5)
        assert str(refused.value) == f"The proxy at 127.0.0.1:{signed.port} {words}"
    refusing = FakeProxy(socks5_proxy(reply=5))
    with pytest.raises(net_proxy.ProxyRefused, match=r"the connection to example\.org:443 \(connection refused\)"):
        net_proxy.open_tunnel(Route("socks5", refusing.server("socks5")), "example.org", 443, 5)


# --- the trusted opener --------------------------------------------------------------

def test_https_through_an_http_proxy_tunnels_and_keeps_the_tls_name():
    proxy, tls = FakeProxy(http_proxy(200)), PlainTLS()
    policy = Policy("manual", proxy.server(username="u", password="p"))
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}),
                                         net_proxy.RoutedHTTPSHandler(policy=policy, context=tls))
    with opener.open("https://api.example.org/v1/models?x=1", timeout=5) as response:
        assert response.read() == b"ok"
    connect, inner = proxy.seen
    assert connect.startswith(b"CONNECT api.example.org:443 HTTP/1.1\r\n") and b"Proxy-Authorization: Basic" in connect
    assert inner.startswith(b"GET /v1/models?x=1 HTTP/1.1\r\n") and b"Host: api.example.org\r\n" in inner
    assert b"Proxy-Authorization" not in inner and tls.names == ["api.example.org"]


def test_the_tls_context_is_made_at_the_first_https_request(monkeypatch):
    """So an opener built at import (zotero_web's, sync_engine's) loads the
    CAs that app.py's certifi fallback, run after the imports, names."""
    made = []
    monkeypatch.setattr(http.client, "_create_https_context", lambda version: made.append(version) or PlainTLS())
    proxy = FakeProxy(http_proxy(200))
    opener = net_proxy.build_opener(policy=Policy("manual", proxy.server()))
    assert made == []
    for _ in range(2):
        with opener.open("https://api.example.org/", timeout=5) as response:
            assert response.read() == b"ok"
    assert len(made) == 1


def test_http_through_an_http_proxy_is_sent_in_absolute_form(monkeypatch):
    proxy = FakeProxy(http_proxy())
    monkeypatch.setattr(net_proxy, "current", lambda: Policy("manual", proxy.server(username="u", password="p")))
    net_proxy.install()  # what create_app does: every bare urlopen routed
    with urllib.request.urlopen("http://example.org:8080/a?b=1", timeout=5) as response:
        assert response.read() == b"ok"
    head = proxy.seen[0]
    assert head.startswith(b"GET http://example.org:8080/a?b=1 HTTP/1.1\r\n")
    assert b"Host: example.org:8080\r\n" in head
    assert b"Proxy-Authorization: Basic " + base64.b64encode(b"u:p") + b"\r\n" in head


def test_http_through_socks5():
    proxy = FakeProxy(socks5_proxy())
    with net_proxy.build_opener(policy=Policy("manual", proxy.server("socks5"))).open("http://example.org/a",
                                                                                      timeout=5) as response:
        assert response.read() == b"ok"
    assert proxy.seen[0] == (3, "example.org", 80)
    assert proxy.seen[1].startswith(b"GET /a HTTP/1.1\r\n") and b"Host: example.org\r\n" in proxy.seen[1]


def test_a_failing_proxy_reaches_urlopens_caller_as_its_reason():
    opener = net_proxy.build_opener(policy=Policy("manual", ProxyServer("http", "127.0.0.1", 1, "u", "hunter2")))
    with pytest.raises(urllib.error.URLError) as failed:
        opener.open("https://api.example.org/", timeout=5)
    assert isinstance(failed.value.reason, net_proxy.ProxyUnreachable) and "hunter2" not in str(failed.value)


# --- guarded fetches -----------------------------------------------------------------

@pytest.fixture
def guarded_tls(monkeypatch):
    tls = PlainTLS()
    monkeypatch.setattr(net_proxy, "RoutedHTTPSHandler", functools.partial(net_proxy.RoutedHTTPSHandler, context=tls))
    return tls


def test_a_guarded_fetch_through_the_proxy_names_the_validated_address(dns, guarded_tls):
    proxy = FakeProxy(http_proxy(200))
    policy = manual(proxy.server())
    dns["publisher.example"] = PUBLIC
    with net_guard.guarded_urlopen("https://publisher.example/paper.pdf", timeout=5, policy=policy) as response:
        assert response.read() == b"ok"
    assert proxy.seen[0].startswith(f"CONNECT {PUBLIC}:443 HTTP/1.1\r\n".encode())
    assert b"Host: publisher.example\r\n" in proxy.seen[1] and guarded_tls.names == ["publisher.example"]
    # an http URL too: sent in full naming the address, the proxy would forward that as the Host
    with net_guard.guarded_urlopen("http://publisher.example/paper.pdf", timeout=5, policy=policy) as response:
        assert response.read() == b"ok"
    assert proxy.seen[2].startswith(f"CONNECT {PUBLIC}:80 HTTP/1.1\r\n".encode())
    assert proxy.seen[3].startswith(b"GET /paper.pdf HTTP/1.1\r\n") and b"Host: publisher.example\r\n" in proxy.seen[3]

    dns["internal.example"] = "10.0.0.5"
    for url in ("https://internal.example/", "http://10.0.0.5/", "http://unresolvable.example/"):
        with pytest.raises(net_guard.BlockedUrlError):
            net_guard.guarded_urlopen(url, timeout=5, policy=policy)
    assert len(proxy.seen) == 4  # nothing asked of the proxy


def forwarding_proxy():
    """An HTTP proxy that forwards as RFC 9112 has it: a request sent to it
    in full goes on with the Host its target names, whatever Host it
    carried; a tunnel's as it is. ``seen`` gets the Host the origin gets."""
    def serve(proxy, conn):
        head = _head(conn)
        if head.startswith(b"CONNECT"):
            conn.sendall(b"HTTP/1.1 200 Connection established\r\n\r\n")
            head = _head(conn)
        target = head.split(b" ", 2)[1].decode()
        headers = dict(line.split(": ", 1) for line in head.decode().split("\r\n")[1:] if ": " in line)
        proxy.seen.append(urllib.parse.urlsplit(target).netloc if target.startswith("http://") else headers["Host"])
        conn.sendall(OK)
    return serve


def test_the_origin_gets_the_urls_host_through_a_proxy_that_rebuilds_it(dns):
    proxy = FakeProxy(forwarding_proxy())
    dns["publisher.example"] = PUBLIC
    for policy in (manual(proxy.server()), manual(proxy.server(), proxy_dns=True)):  # pinned, then by name
        with net_guard.guarded_urlopen("http://publisher.example/paper.pdf", timeout=5, policy=policy) as response:
            assert response.read() == b"ok"
    with net_proxy.build_opener(policy=manual(proxy.server())).open("http://example.org:8080/", timeout=5) as response:
        assert response.read() == b"ok"
    assert proxy.seen == ["publisher.example", "publisher.example", "example.org:8080"]


def test_a_407_to_a_request_sent_in_full_is_the_proxys_refusal(admin, dns, monkeypatch):
    def serve(proxy, conn):
        proxy.seen.append(_head(conn))
        conn.sendall(b"HTTP/1.1 407 Proxy Authentication Required\r\nProxy-Authenticate: Basic realm=\"p\"\r\n"
                     b"Content-Length: 0\r\n\r\n")
    proxy = FakeProxy(serve)
    for username, words in (("", "asks for a sign-in (407)"), ("alice", "refused the sign-in")):
        opener = net_proxy.build_opener(policy=manual(proxy.server(username=username, password="hunter2")))
        with pytest.raises(urllib.error.URLError) as refused:
            opener.open("http://example.org/", timeout=5)
        assert isinstance(refused.value.reason, net_proxy.ProxyAuthError)
        assert str(refused.value.reason) == f"The proxy at 127.0.0.1:{proxy.port} {words}"
    monkeypatch.setattr(proxy_settings, "TEST_TARGETS", (("Example", "http://api.example.org/", False),))
    r = admin.post("/api/admin/proxy/test", json={"mode": "manual", "server": f"http://127.0.0.1:{proxy.port}"})
    (line,) = [line for line in r.json()["results"] if line["name"] == "Example"]
    assert line["ok"] is False and line["error"] == f"The proxy at 127.0.0.1:{proxy.port} asks for a sign-in (407)"


def test_a_redirect_through_the_proxy_is_checked_again(dns):
    def serve(proxy, conn):
        head = _head(conn)
        if head.startswith(b"CONNECT"):
            conn.sendall(b"HTTP/1.1 200 Connection established\r\n\r\n")
            head = _head(conn)
        proxy.seen.append(head)
        conn.sendall(b"HTTP/1.1 302 Found\r\nLocation: http://internal.example/secret\r\nContent-Length: 0\r\n\r\n")
    proxy = FakeProxy(serve)
    dns.update({"publisher.example": PUBLIC, "internal.example": "192.168.0.10"})
    with pytest.raises(net_guard.BlockedUrlError, match="blocked internal address"):
        net_guard.guarded_urlopen("http://publisher.example/paper.pdf", timeout=5, policy=manual(proxy.server()))
    assert len(proxy.seen) == 1


def test_with_proxy_dns_the_proxy_resolves_what_passes_the_name_checks(dns, guarded_tls):
    proxy = FakeProxy(http_proxy(200))
    policy = manual(proxy.server(), proxy_dns=True)
    dns.update({"fake-ip.example": "198.18.0.5", "rebind.example": "10.0.0.7"})
    for host in ("only-the-proxy-knows.example", "fake-ip.example"):
        assert net_guard.validate_public_url(f"https://{host}/", policy)
        with net_guard.guarded_urlopen(f"https://{host}/paper.pdf", timeout=5, policy=policy) as response:
            assert response.read() == b"ok"
        assert proxy.seen[-2].startswith(f"CONNECT {host}:443 HTTP/1.1\r\n".encode())
    seen = len(proxy.seen)
    for url in ("http://localhost/", "http://intranet/", "http://printer.local/", "http://metadata.google.internal/",
                "http://nas.home.arpa/", "http://router.lan/", "http://10.0.0.1/", "http://169.254.169.254/",
                "http://198.18.0.1/", "http://rebind.example/"):
        with pytest.raises(net_guard.BlockedUrlError):
            net_guard.guarded_urlopen(url, timeout=5, policy=policy)
    assert len(proxy.seen) == seen


def test_with_proxy_dns_an_address_written_as_a_name_is_still_an_address(dns, monkeypatch):
    """A trailing dot, or a short or hex IPv4 form, is the address to a
    proxy (and to Squid's URL parser): refused when it is blocked, the
    system's proxy taking every such host here."""
    monkeypatch.setattr(urllib.request, "getproxies", lambda: {"http": "http://10.9.9.9:3128",
                                                                "https": "http://10.9.9.9:3128"})
    policy = Policy("system", proxy_dns=True)
    for url in ("http://169.254.169.254./latest/meta-data/", "http://10.0.0.5./admin", "http://127.1./",
                "http://0x7f.1/", "http://10.1/", "https://[::ffff:10.0.0.5]/"):
        with pytest.raises(net_guard.BlockedUrlError, match="blocked internal address"):
            net_guard.validate_public_url(url, policy)
    way = net_proxy.route("example.org", "http", policy)
    assert net_guard._target(policy, way, "example.org.", 80) == "example.org"  # the proxy never sees the dot
    assert net_guard._target(policy, way, f"{PUBLIC}.", 80) == PUBLIC


def test_a_fake_ip_vpns_ipv6_answer_is_fake_too(dns):
    """sing-box answers a name with 198.18/15 and fc00::/18 at once: with
    proxy_dns the name goes to the proxy, and the default bypass list's
    fc00::/7 does not send it direct. A real unique-local answer (fd00::/8)
    still refuses it."""
    server = ProxyServer("http", "10.0.0.2", 3128)
    dns.update({"fake.example": ["198.18.0.7", "fc00::7"], "ula.example": ["198.18.0.8", "fd00::8"]})
    for bypass in (DEFAULT_BYPASS, ""):
        policy = manual(server, bypass, proxy_dns=True)
        assert net_proxy.route("fake.example", "https", policy) == Route("http", server), bypass
        assert net_guard.validate_public_url("https://fake.example/", policy)
        with pytest.raises(net_guard.BlockedUrlError, match="blocked internal address"):
            net_guard.validate_public_url("https://ula.example/", policy)
    assert net_proxy.route("fake.example", "https", manual(server, "198.18.0.0/15")).kind == "http"


# --- the agents' environment and boto3 -----------------------------------------------

def test_agent_env_per_mode(monkeypatch):
    env = {"PATH": "/bin", "https_proxy": "http://old:1", "No_Proxy": "x", "HOME": "/home/a"}
    server = ProxyServer("http", "127.0.0.1", 7890, "al@ice", "p:ss")
    got = net_proxy.agent_env(dict(env), manual(server, "*.corp.example, 10.0.0.0/8, <local>, intranet"))
    url = "http://al%40ice:p%3Ass@127.0.0.1:7890"
    assert got["HTTP_PROXY"] == got["HTTPS_PROXY"] == url and "ALL_PROXY" not in got and "No_Proxy" not in got
    assert got["NO_PROXY"] == "localhost,127.0.0.1,::1,corp.example,.corp.example,10.0.0.0/8,intranet,.intranet"
    assert got["PATH"] == "/bin" and got["HOME"] == "/home/a"
    if sys.platform != "win32":
        assert got["https_proxy"] == url and got["no_proxy"] == got["NO_PROXY"]
    # SOCKS5 in ALL_PROXY alone: an HTTP client reading HTTPS_PROXY speaks only to an HTTP proxy
    socks = net_proxy.agent_env(dict(env), manual(ProxyServer("socks5", "127.0.0.1", 1080)))
    assert socks["ALL_PROXY"] == "socks5h://127.0.0.1:1080" and socks["NO_PROXY"] == "localhost,127.0.0.1,::1"
    assert not {"HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"} & set(socks)
    if sys.platform != "win32":
        assert socks["all_proxy"] == socks["ALL_PROXY"] and socks["no_proxy"] == socks["NO_PROXY"]
    assert net_proxy.agent_env(dict(env), Policy("none")) == {"PATH": "/bin", "HOME": "/home/a"}
    assert net_proxy.agent_env(dict(env), Policy()) == env  # the environment's own proxy stays
    monkeypatch.setattr(urllib.request, "getproxies", lambda: {"https": "http://10.0.0.9:8080"})  # the OS's alone
    alone = net_proxy.agent_env({"PATH": "/bin"}, Policy())
    assert alone["HTTPS_PROXY"] == "http://10.0.0.9:8080"
    assert {"10.0.0.0/8", "192.168.0.0/16", "fc00::/7", "local", ".local"} <= set(alone["NO_PROXY"].split(","))


def test_botocore_proxies(monkeypatch):
    said = []
    monkeypatch.setattr(net_proxy, "log", type("Log", (), {"info": staticmethod(said.append)}))
    monkeypatch.setattr(net_proxy, "_s3_said", set())
    server = ProxyServer("http", "10.0.0.2", 3128, "u", "p")
    assert net_proxy.botocore_proxies("https://minio.example:9000", manual(server)) == {
        "http": "http://u:p@10.0.0.2:3128", "https": "http://u:p@10.0.0.2:3128"}
    assert net_proxy.botocore_proxies(None, manual(server, "*.amazonaws.com")) == {}
    assert net_proxy.botocore_proxies("http://127.0.0.1:9000", manual(server)) == {}
    assert net_proxy.botocore_proxies(None, Policy("none")) == {}
    assert said == []
    socks = manual(ProxyServer("socks5", "10.0.0.2", 1080))
    assert net_proxy.botocore_proxies("https://minio.example:9000", socks) == {}
    assert net_proxy.botocore_proxies("https://minio.example:9000", socks) == {}
    assert said == ["[proxy] S3 cannot go through the SOCKS5 proxy at 10.0.0.2:1080: the off-site copies connect "
                    "to minio.example directly"]
    # a sign-in boto3 cannot send (it encodes it as ASCII, and only with a password): the proxy without it
    for user, password in (("alice", "pässwort"), ("alice", "")):
        unsent = manual(ProxyServer("http", "10.0.0.3", 3128, user, password))
        for _ in range(2):
            assert net_proxy.botocore_proxies("https://minio.example:9000", unsent) == {
                "http": "http://10.0.0.3:3128", "https": "http://10.0.0.3:3128"}
    assert said[1:] == ["[proxy] S3 cannot sign in to the proxy at 10.0.0.3:3128 (boto3 sends only an ASCII user "
                        "name with a password): the off-site copies go through it without a sign-in"]


def test_the_log_scrubs_a_proxy_sign_in():
    for line in ("proxy http://alice:hunter2@proxy.corp:3128 failed", "socks5h://alice:hunter2%40x@10.0.0.2:1080",
                 "https_proxy=http://alice:hunter2@h:1", "http://alice:hunt@er2@h:1/x",
                 "Proxy-Authorization: Basic YWxpY2U6aHVudGVyMg==",
                 "{'Proxy-Authorization': 'Basic YWxpY2U6aHVudGVyMg=='}"):
        cleaned = scrub(line)
        assert "hunter2" not in cleaned and "er2" not in cleaned and "YWxpY2U6aHVudGVyMg" not in cleaned, cleaned
    assert scrub("proxy http://alice:hunter2@proxy.corp:3128 failed") == "proxy http://***@proxy.corp:3128 failed"
    for kept in ("https://example.org/a@b", "mailto:bob@example.org", "Basic settings saved"):
        assert scrub(kept) == kept


def test_the_scrub_of_a_long_request_path_is_quick():
    """A request path anyone can send is logged on a 404: its scrub runs on
    the event loop, so it must stay linear in the line's length."""
    started = time.monotonic()
    scrub("[http] GET /api/" + "a." * 50_000 + " 404")
    assert time.monotonic() - started < 0.5
    assert scrub("GET http://u:p@h:1 failed") == "GET http://***@h:1 failed"


# --- the admin API -------------------------------------------------------------------

@pytest.fixture(scope="module")
def admins(client):
    make_user("px_admin", "px-admin-pw", is_admin=1)
    make_user("px_user", "px-user-pw")
    yield login("px_admin", "px-admin-pw"), login("px_user", "px-user-pw")
    drop_user("px_admin")
    drop_user("px_user")


@pytest.fixture
def admin(admins):
    """The admin's client; the saved proxy setting removed after the test,
    so no later test routes through it."""
    yield admins[0]
    with db.connect_users_db() as conn:
        conn.execute("DELETE FROM settings WHERE key = 'proxy'")
        conn.commit()


def _stored_raw() -> str:
    with db.connect_users_db() as conn:
        row = conn.execute("SELECT value FROM settings WHERE key = 'proxy'").fetchone()
    return row[0] if row else ""


def test_the_endpoints_are_admin_only(admins, anon):
    _, user = admins
    for method, path in (("GET", "/api/admin/proxy"), ("PUT", "/api/admin/proxy"),
                         ("POST", "/api/admin/proxy/test")):
        assert user.request(method, path, json={}).status_code == 403
        assert anon.request(method, path, json={}).status_code == 401


def test_the_setting_through_the_api(admin):
    r = admin.get("/api/admin/proxy")
    assert r.status_code == 200 and r.json() == {
        "mode": "system", "server": "", "username": "", "password_set": False, "bypass": DEFAULT_BYPASS,
        "default_bypass": DEFAULT_BYPASS, "proxy_dns": False, "managed": None,
        "system": {"proxy": "", "http": "", "from": "", "unsupported": ""}}

    r = admin.put("/api/admin/proxy", json={"mode": "manual", "server": "http://alice:s3cret-pw@127.0.0.1:7890",
                                            "bypass": "*.corp.example\n10.0.0.0/8", "proxy_dns": True})
    assert r.status_code == 200, r.text
    assert "s3cret-pw" not in r.text
    assert {k: r.json()[k] for k in ("mode", "server", "username", "password_set", "bypass", "proxy_dns")} == {
        "mode": "manual", "server": "http://127.0.0.1:7890", "username": "alice", "password_set": True,
        "bypass": "*.corp.example, 10.0.0.0/8", "proxy_dns": True}
    saved = json.loads(_stored_raw())
    assert "s3cret-pw" not in json.dumps(saved) and saved["server"] == "http://127.0.0.1:7890"
    assert unseal(saved["password"]) == "s3cret-pw"  # sealed with the data directory's key
    policy = proxy_settings.policy()
    assert policy.server == ProxyServer("http", "127.0.0.1", 7890, "alice", "s3cret-pw") and policy.proxy_dns

    # an empty password keeps the saved one; an emptied username drops it
    r = admin.put("/api/admin/proxy", json={"username": "alice", "password": ""})
    assert r.json()["password_set"] and proxy_settings.policy().server.password == "s3cret-pw"
    assert "s3cret-pw" not in admin.get("/api/admin/proxy").text
    r = admin.put("/api/admin/proxy", json={"username": ""})
    assert r.json()["password_set"] is False and proxy_settings.policy().server.password == ""

    for body in ({"server": ""}, {"server": "https://p:443"}, {"bypass": "host:8080"}, {"mode": "bogus"},
                 {"server": "http://alice:hunter2@p"}):
        r = admin.put("/api/admin/proxy", json=body)
        assert r.status_code == 400 and r.json()["detail"] and "hunter2" not in r.text, body
    assert admin.get("/api/admin/proxy").json()["server"] == "http://127.0.0.1:7890"  # nothing saved

    r = admin.put("/api/admin/proxy", json={"mode": "system"})
    assert r.json()["mode"] == "system" and proxy_settings.policy() == Policy("system", proxy_dns=True)


def test_a_cross_origin_change_is_refused(admin):
    for headers in ({"origin": "https://evil.example"}, {"sec-fetch-site": "cross-site"}):
        r = admin.put("/api/admin/proxy", json={"mode": "none"}, headers=headers)
        assert r.status_code == 403
        assert admin.post("/api/admin/proxy/test", json={}, headers=headers).status_code == 403
    assert admin.get("/api/admin/proxy").json()["mode"] == "system"


def test_a_hosted_server_keeps_the_operators_network(admin, monkeypatch):
    admin.put("/api/admin/proxy", json={"mode": "manual", "server": "127.0.0.1:7890"})
    monkeypatch.setenv("GAMMA_HOSTED", "1")
    r = admin.put("/api/admin/proxy", json={"mode": "none"})
    assert r.status_code == 409 and r.json()["detail"] == "Gamma Cloud manages this server's network."
    view = admin.get("/api/admin/proxy").json()
    assert view["managed"] == "hosted" and view["mode"] == "system"
    assert proxy_settings.policy() == Policy()


def test_the_test_tries_a_draft_and_saves_nothing(admin, dns):
    dns.update({"api.openai.com": PUBLIC, "api.anthropic.com": PUBLIC, "api.zotero.org": PUBLIC,
                "arxiv.org": PUBLIC, "api.deepseek.com": PUBLIC})
    r = admin.post("/api/admin/proxy/test", json={"mode": "manual", "server": "http://u:hunter2@127.0.0.1:1",
                                                  "url": "https://api.deepseek.com"})
    assert r.status_code == 200, r.text
    results = {line["name"] or line["url"]: line for line in r.json()["results"]}
    assert {"OpenAI", "Anthropic", "Zotero", "arXiv", "https://api.deepseek.com"} <= set(results)
    for line in r.json()["results"]:
        if line["route"] == "proxy":  # all but a Gamma Cloud on this machine
            assert line["via"] == "http://127.0.0.1:1" and line["ok"] is False
            assert line["error"].startswith("Couldn't reach the proxy at 127.0.0.1:1 ("), line
    assert results["arXiv"]["route"] == "proxy" and "hunter2" not in r.text
    assert admin.get("/api/admin/proxy").json()["mode"] == "system"  # nothing saved


def test_an_answer_refusing_this_network_fails_its_line(admin, dns, monkeypatch):
    """OpenAI's region block (or a Cloudflare challenge) is an answer, but
    not a way that works: the line fails and says why."""
    dns.update({"api.openai.com": PUBLIC})
    monkeypatch.setattr(proxy_settings, "TEST_TARGETS", (("OpenAI", "https://api.openai.com/v1/models", False),))
    region = b'{"error": {"code": "unsupported_country_region_territory", "message": "Country, region, or territory"}}'

    class Refusing:
        def open(self, url, timeout=None):
            raise urllib.error.HTTPError(url, 403, "Forbidden", {}, io.BytesIO(region))

    monkeypatch.setattr(net_proxy, "build_opener", lambda **kw: Refusing())
    r = admin.post("/api/admin/proxy/test", json={"mode": "none"})
    (line,) = [line for line in r.json()["results"] if line["name"] == "OpenAI"]
    assert (line["ok"], line["status"], line["route"]) == (False, 403, "direct")
    assert line["error"] == "403: it doesn't serve the country or region this server connects from"
    for body in ({"url": "ftp://example.org/"}, {"mode": "manual"}, {"url": "https://u:p@example.org/"}):
        assert admin.post("/api/admin/proxy/test", json=body).status_code == 400, body


def test_a_draft_without_a_password_uses_the_saved_one_on_the_same_proxy(admin, dns, monkeypatch):
    proxy = FakeProxy(http_proxy(403))
    admin.put("/api/admin/proxy", json={"mode": "manual", "server": "http://alice:s3cret@127.0.0.1:9"})
    monkeypatch.setattr(proxy_settings, "TEST_TARGETS", (("Example", "https://api.example.org/", False),))
    # another port of the same host: the same proxy
    r = admin.post("/api/admin/proxy/test", json={"server": f"http://127.0.0.1:{proxy.port}", "username": "alice"})
    (line,) = [line for line in r.json()["results"] if line["name"] == "Example"]
    assert line["error"] == (f"The proxy at 127.0.0.1:{proxy.port} refused the connection to api.example.org:443 "
                             "(403 Forbidden)")
    assert b"Proxy-Authorization: Basic " + base64.b64encode(b"alice:s3cret") in proxy.seen[0]
    assert proxy_settings.policy().server.port == 9  # the draft was not saved


def test_the_saved_password_goes_only_to_its_proxy_and_user_name(admin):
    admin.put("/api/admin/proxy", json={"mode": "manual", "server": "http://alice:s3cret@corp-proxy:8080"})
    again = "Type the proxy's password again: the proxy or its user name changed."
    for draft in ({"server": "http://b-laptop:9999", "username": "alice"}, {"server": "http://bob@other:3128"},
                  {"username": "bob"}, {"server": "socks5://evil:1080"}):
        for method, path in (("PUT", "/api/admin/proxy"), ("POST", "/api/admin/proxy/test")):
            r = admin.request(method, path, json=draft)
            assert r.status_code == 400 and r.json()["detail"] == again, (draft, method)
    assert proxy_settings.policy().server == ProxyServer("http", "corp-proxy", 8080, "alice", "s3cret")
    r = admin.put("/api/admin/proxy", json={"server": "socks5://CORP-PROXY:1080", "username": "alice"})
    assert r.status_code == 200 and proxy_settings.policy().server.password == "s3cret"  # the same host
    r = admin.put("/api/admin/proxy", json={"server": "http://other:3128", "username": "bob", "password": "b0b"})
    assert r.status_code == 200 and proxy_settings.policy().server == ProxyServer("http", "other", 3128, "bob", "b0b")


def test_a_password_needs_its_user_name(admin):
    for draft in ({"mode": "manual", "server": "http://127.0.0.1:2", "username": "", "password": "typed"},
                  {"mode": "manual", "server": "http://:typed@127.0.0.1:2"}):
        r = admin.put("/api/admin/proxy", json=draft)
        assert r.status_code == 400 and r.json()["detail"] == "Add the user name this password goes with.", draft
        assert "typed" not in r.text
    assert admin.get("/api/admin/proxy").json()["mode"] == "system"  # nothing saved


def test_a_password_that_no_longer_unseals_reads_as_none(admin, monkeypatch):
    admin.put("/api/admin/proxy", json={"mode": "manual", "server": "http://alice:s3cret@127.0.0.1:7890"})
    with db.connect_users_db() as conn:
        conn.execute("UPDATE settings SET value = ? WHERE key = 'proxy'",
                     (json.dumps({**json.loads(_stored_raw()), "password": "not-a-token"}),))
        conn.commit()
    assert admin.get("/api/admin/proxy").json()["password_set"] is False
    assert proxy_settings.policy().server.password == ""


def test_a_corrupt_saved_value_reads_as_the_default(admin):
    for raw in ("not json", "[1]", json.dumps({"mode": "manual", "server": "nonsense:"})):
        proxy_settings._set_raw(proxy_settings.SETTINGS_KEY, raw)
        assert proxy_settings.policy() == Policy(), raw
