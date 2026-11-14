"""Exercise real urllib cookie/redirect handling with a fake HTTPS transport."""

import functools
import io
import socket
from email.message import Message
from urllib.request import HTTPSHandler, ProxyHandler, build_opener
from urllib.response import addinfourl

import pytest

from gamma import net_guard, net_proxy


@pytest.fixture(autouse=True)
def fresh_routes(monkeypatch):
    """Each test reads the environment's proxies anew."""
    monkeypatch.setattr(net_proxy, "_system", (0.0, {}))
    monkeypatch.setattr(net_proxy, "_routes", {})


@pytest.fixture
def transport(monkeypatch):
    routes = {}
    seen = []

    class FakeHTTPS(HTTPSHandler):
        def https_open(self, req):
            seen.append((req.full_url, req.get_header("Cookie")))
            status, headers, body = routes[req.full_url]
            message = Message()
            for key, value in headers.items():
                message[key] = value
            response = addinfourl(io.BytesIO(body), message, req.full_url, status)
            response.msg = "Found" if status == 302 else "OK"
            return response

    monkeypatch.setattr(net_guard.net_proxy, "build_opener", lambda *handlers, **routing: build_opener(
        ProxyHandler({}), *handlers, FakeHTTPS()))
    # Fake public DNS only; private redirect targets must still be refused.
    monkeypatch.setattr(net_guard.socket, "getaddrinfo", lambda host, port, **kw: [
        (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "",
         ("127.0.0.1" if host == "127.0.0.1" else "8.8.8.8", port))])
    return routes, seen


def test_publisher_cookie_survives_redirect_but_not_next_fetch(transport):
    routes, seen = transport
    start = "https://publisher.example/paper.pdf"
    target = "https://publisher.example/authenticated.pdf"
    routes[start] = (302, {
        "Location": target, "Set-Cookie": "access=institution; Path=/; Secure",
    }, b"")
    routes[target] = (200, {"Content-Type": "application/pdf"}, b"%PDF-1.4")

    with net_guard.guarded_urlopen(start) as response:
        assert response.read() == b"%PDF-1.4"
    assert seen == [(start, None), (target, "access=institution")]

    # A separate request (possibly another user) must start without cookies.
    with net_guard.guarded_urlopen(target) as response:
        response.read()
    assert seen[-1] == (target, None)


def test_publisher_cookie_is_not_forwarded_to_unrelated_host(transport):
    routes, seen = transport
    start = "https://publisher.example/paper.pdf"
    target = "https://other.example/paper.pdf"
    routes[start] = (302, {
        "Location": target, "Set-Cookie": "access=institution; Path=/; Secure",
    }, b"")
    routes[target] = (200, {}, b"%PDF-1.4")
    with net_guard.guarded_urlopen(start) as response:
        response.read()
    assert seen == [(start, None), (target, None)]


@pytest.mark.parametrize("target", ["https://127.0.0.1/secret", "file:///secret"])
def test_cookie_redirect_still_validates_destination(transport, target):
    routes, seen = transport
    start = "https://publisher.example/paper.pdf"
    routes[start] = (302, {
        "Location": target, "Set-Cookie": "access=institution; Path=/; Secure",
    }, b"")
    # urllib rejects non-HTTP redirects before our handler; the guard rejects
    # private HTTP(S) targets. Both must fail before another transport call.
    from urllib.error import URLError
    with pytest.raises(URLError):
        net_guard.guarded_urlopen(start)
    assert seen == [(start, None)]


@pytest.mark.parametrize("url", [
    "http://100.64.0.1/metadata", "https://[::ffff:100.64.0.1]/",
    "https://example.org:invalid/", "https://example.org:65536/", "https://[broken/",
])
def test_nonpublic_or_malformed_url_is_refused(url):
    with pytest.raises(net_guard.BlockedUrlError):
        net_guard.validate_public_url(url)


@pytest.mark.parametrize("scheme", ["http", "https"])
def test_dns_rebinding_is_refused_at_connect(monkeypatch, scheme):
    answers = iter(["8.8.8.8", "127.0.0.1"])
    monkeypatch.setattr(net_guard.socket, "getaddrinfo", lambda host, port, **kw: [
        (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", (next(answers), port))])
    def forbidden_socket(*args, **kwargs):
        pytest.fail("rebinding must be refused before creating a socket")
    monkeypatch.setattr(net_guard.socket, "socket", forbidden_socket)
    with pytest.raises(net_guard.BlockedUrlError, match="blocked internal address"):
        net_guard.guarded_urlopen(f"{scheme}://rebind.example/secret")


@pytest.fixture
def wire(monkeypatch):
    """A fake network under the transport: publisher.example resolves to
    8.8.8.8 (at most twice: the URL check and the transport, no third
    lookup), 127.0.0.1 is the environment's proxy, which answers a CONNECT,
    and every socket records where it connected and what it sent."""
    seen = {"resolved": [], "connected": [], "sent": [], "tls": []}

    def resolve(host, port, *args, **kw):
        if host != "127.0.0.1":
            seen["resolved"].append(host)
            assert host == "publisher.example"
            assert len(seen["resolved"]) <= 2
        return [(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "",
                 (host if host == "127.0.0.1" else "8.8.8.8", port))]

    class FakeSocket:
        def __init__(self, *args): self.tunnel = io.BytesIO(b"HTTP/1.1 200 Connection established\r\n\r\n")
        def settimeout(self, timeout): pass
        def setsockopt(self, *args): pass
        def connect(self, address): seen["connected"].append(address)
        def sendall(self, data): seen["sent"].append(data)
        def recv(self, n): return self.tunnel.read(n)
        def makefile(self, *args):
            return io.BytesIO(b"HTTP/1.1 200 OK\r\nContent-Length: 3\r\n\r\npdf")
        def close(self): pass

    class FakeTLS:
        def wrap_socket(self, sock, server_hostname):
            seen["tls"].append(server_hostname)
            return sock

    monkeypatch.setattr(net_proxy, "RoutedHTTPSHandler",
                        functools.partial(net_proxy.RoutedHTTPSHandler, context=FakeTLS()))
    monkeypatch.setattr(net_guard.socket, "getaddrinfo", resolve)
    monkeypatch.setattr(net_guard.socket, "socket", FakeSocket)
    for name in ("no_proxy", "NO_PROXY", "all_proxy", "ALL_PROXY"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("http_proxy", "http://127.0.0.1:9999")
    monkeypatch.setenv("https_proxy", "http://127.0.0.1:9999")
    return seen


@pytest.mark.parametrize("scheme,port", [("http", 80), ("https", 443)])
def test_the_environments_proxy_is_asked_for_the_validated_address(monkeypatch, wire, scheme, port):
    """A tunnel to the address, http too: a proxy sent the URL in full
    would forward the Host it names. Host and TLS name the host."""
    monkeypatch.setattr(net_proxy, "current", lambda: net_proxy.Policy("system"))
    with net_guard.guarded_urlopen(f"{scheme}://publisher.example/paper.pdf") as response:
        assert response.read() == b"pdf"
    sent = b"".join(wire["sent"])
    assert wire["connected"] == [("127.0.0.1", 9999)] and wire["resolved"] == ["publisher.example"] * 2
    assert wire["sent"][0] == f"CONNECT 8.8.8.8:{port} HTTP/1.1\r\nHost: 8.8.8.8:{port}\r\n\r\n".encode()
    assert b"GET /paper.pdf HTTP/1.1\r\n" in sent and b"Host: publisher.example\r\n" in sent
    assert wire["tls"] == (["publisher.example"] if scheme == "https" else [])


@pytest.mark.parametrize("scheme,port", [("http", 80), ("https", 443)])
def test_no_proxy_mode_pins_the_address_and_ignores_the_environment(monkeypatch, wire, scheme, port):
    monkeypatch.setattr(net_proxy, "current", lambda: net_proxy.Policy("none"))
    with net_guard.guarded_urlopen(f"{scheme}://publisher.example/paper.pdf") as response:
        assert response.read() == b"pdf"
    assert wire["connected"] == [("8.8.8.8", port)]
    assert b"Host: publisher.example\r\n" in b"".join(wire["sent"])
    assert wire["tls"] == (["publisher.example"] if scheme == "https" else [])


def test_transport_refuses_mixed_dns_answers_before_any_connect(monkeypatch):
    monkeypatch.setattr(net_guard.socket, "getaddrinfo", lambda host, port, **kw: [
        (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", (ip, port))
        for ip in ["8.8.8.8", "10.0.0.1"]])
    def forbidden_socket(*args, **kwargs):
        pytest.fail("validate every DNS answer before connecting to any of them")
    monkeypatch.setattr(net_guard.socket, "socket", forbidden_socket)
    with pytest.raises(net_guard.BlockedUrlError):
        net_guard._public_connection(("mixed.example", 443))
