"""How gamma_sync's client fails (gamma/gamma_sync.py, docs/dev/https.md):
it follows no redirect (``Redirected`` for one that keeps the path,
``Blocked`` "signin" for any other), tells a proxy's refusal from Gamma's
own (``blocked``, ``is_gamma_refusal``), names a TLS failure, and leaves
a status the fields of its failure (``status_fields``). Against real
servers on 127.0.0.1."""

import ast
import io
import json
import ssl
import sys
import threading
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from gamma import gamma_sync, net_proxy


class _Handler(BaseHTTPRequestHandler):
    def _answer(self):
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length else b""
        self.server.seen.append((self.command, self.path, dict(self.headers), body))
        status, headers, data = self.server.answer(self)
        self.send_response(status)
        for key, value in headers.items():
            self.send_header(key, value)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    do_GET = do_POST = do_PUT = do_DELETE = _answer

    def log_message(self, *args):
        pass


@pytest.fixture
def serve():
    """``serve(answer)``: a server on 127.0.0.1 answering each request with
    ``answer(handler) -> (status, headers, body)``; its ``seen`` lists
    (method, path, headers, body), ``url`` is its address."""
    servers = []

    def start(answer):
        server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        server.daemon_threads = True
        server.seen, server.answer = [], answer
        server.url = f"http://127.0.0.1:{server.server_address[1]}"
        threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()
        servers.append(server)
        return server

    yield start
    for server in servers:
        server.shutdown()
        server.server_close()


def _json(status, obj, headers=None):
    return lambda h: (status, {"Content-Type": "application/json", **(headers or {})}, json.dumps(obj).encode())


def _to(status, location):
    return lambda h: (status, {"Location": location(h) if callable(location) else location}, b"")


@pytest.fixture(params=["urllib", "routed"])
def opened(request, monkeypatch):
    """Each case through urllib's own opener (the CLI's) and the app's
    routed one (net_proxy.build_opener, as net_proxy.install sets it)."""
    made = urllib.request.build_opener if request.param == "urllib" else net_proxy.build_opener
    monkeypatch.setattr(gamma_sync, "make_opener", made)
    monkeypatch.setattr(gamma_sync, "_opener", (None, None))
    return request.param


@pytest.mark.parametrize("code", [301, 302, 303, 307, 308])
@pytest.mark.parametrize("method", ["GET", "POST", "PUT", "DELETE"])
def test_a_redirect_is_never_followed(serve, opened, code, method):
    there = serve(_json(200, {"ok": True}))
    here = serve(_to(code, lambda h: there.url + h.path))
    server = gamma_sync.Server(here.url, "secret-token", timeout=5)
    with pytest.raises(gamma_sync.Redirected) as e:
        if method == "GET":
            server.get_json("/api/sync/whoami")
        else:
            server.send_json(method, "/api/sync/whoami", {"a": 1})
    assert e.value.to == there.url and e.value.location == there.url + "/api/sync/whoami"
    assert e.value.upgrade is False and str(e.value) == f"{here.url} now redirects to {there.url}"
    assert there.seen == []   # no body, no bearer went there
    assert len(here.seen) == 1


def test_a_same_host_redirect_to_https_is_an_upgrade(serve, opened):
    here = serve(_to(301, "https://127.0.0.1:5001/api/sync/whoami"))
    with pytest.raises(gamma_sync.Redirected) as e:
        gamma_sync.Server(here.url, "t", timeout=5).get_json("/api/sync/whoami")
    assert e.value.upgrade is True and e.value.to == "https://127.0.0.1:5001"
    assert gamma_sync.status_fields(e.value) == {"error_kind": "redirected", "move_to": "https://127.0.0.1:5001",
                                                 "upgrade": True}


def test_another_name_is_no_upgrade(serve, opened):
    here = serve(_to(301, "https://localhost:5001/api/sync/whoami"))
    with pytest.raises(gamma_sync.Redirected) as e:
        gamma_sync.Server(here.url, "t", timeout=5).get_json("/api/sync/whoami")
    assert e.value.upgrade is False and e.value.to == "https://localhost:5001"


def test_the_address_keeps_scheme_host_and_port_only(serve, opened):
    here = serve(_to(308, "https://user:pw@NAS.Example.com:5001/api/sync/whoami?x=1#f"))
    with pytest.raises(gamma_sync.Redirected) as e:
        gamma_sync.Server(here.url, "t", timeout=5).get_json("/api/sync/whoami")
    assert e.value.to == "https://nas.example.com:5001"
    long = "https://" + "a" * 2000 + ".example/api/sync/whoami"
    here.answer = _to(301, long)
    with pytest.raises(gamma_sync.Redirected) as e:
        gamma_sync.Server(here.url, "t", timeout=5).get_json("/api/sync/whoami")
    assert len(e.value.to) == len(e.value.location) == gamma_sync.ADDRESS_MAX


def test_a_row_with_a_path_keeps_it(serve, opened):
    here = serve(_to(301, lambda h: "https://127.0.0.1:5001" + h.path))
    with pytest.raises(gamma_sync.Redirected) as e:
        gamma_sync.Server(here.url + "/gamma/", "t", timeout=5).get_json("/api/x")
    assert e.value.to == "https://127.0.0.1:5001/gamma"


def test_a_redirect_to_another_path_is_a_sign_in_in_the_way(serve, opened):
    here = serve(_to(302, "https://auth.example/login?rd=https%3A%2F%2Fnas.example.com%2Fapi%2Fsync%2Fwhoami"))
    with pytest.raises(gamma_sync.Blocked) as e:
        gamma_sync.Server(here.url, "t", timeout=5).get_json("/api/sync/whoami")
    assert not isinstance(e.value, gamma_sync.Redirected)
    assert (e.value.kind, e.value.status, e.value.where) == ("signin", 302, "auth.example")
    assert str(e.value) == "a sign-in or proxy in front of 127.0.0.1 redirected this computer to auth.example"
    assert gamma_sync.status_fields(e.value) == {"error_kind": "blocked", "blocked": "signin"}


@pytest.mark.parametrize("status, headers, body, kind", [
    (403, {"Content-Type": "text/html"}, b"<html>Forbidden</html>", "proxy"),
    (401, {"Content-Type": "text/html"}, b"<html>Sign in</html>", "proxy"),
    (407, {}, b"", "proxy"),
    (403, {"Content-Type": "application/json"}, b'{"error": "forbidden"}', "proxy"),
    (403, {"cf-mitigated": "challenge"}, b"<!DOCTYPE html><title>Just a moment...</title>", "cloudflare"),
    (403, {"Server": "cloudflare", "CF-RAY": "1"}, b"<html>Access denied</html>", "cloudflare"),
    (502, {"Content-Type": "text/html"}, b"<html>Bad Gateway</html>", "gateway"),
    (504, {}, b"", "gateway"),
], ids=["403 page", "401 page", "407", "403 other JSON", "challenge", "cloudflare page", "502 page", "504 empty"])
def test_a_refusal_by_something_in_front_is_blocked(serve, status, headers, body, kind):
    here = serve(lambda h: (status, headers, body))
    server = gamma_sync.Server(here.url, "t", timeout=5)
    with pytest.raises(gamma_sync.Blocked) as e:
        server.get_json("/api/sync/whoami")
    assert e.value.kind == kind and e.value.status == status
    assert server.last_headers.get("Content-Length") == str(len(body))


@pytest.mark.parametrize("status, error", [(403, gamma_sync.Denied), (401, gamma_sync.Denied),
                                           (404, gamma_sync.NotFound), (503, gamma_sync.SyncError)])
def test_gammas_own_refusal_is_its_own(serve, status, error):
    here = serve(_json(status, {"detail": "no"}, {"Server": "cloudflare"}))
    with pytest.raises(error) as e:
        gamma_sync.Server(here.url, "t", timeout=5).get_json("/api/sync/whoami")
    assert not isinstance(e.value, gamma_sync.Blocked)


def test_blocked_and_its_own_rule():
    assert gamma_sync.is_gamma_refusal(b'{"detail": "x"}') and not gamma_sync.is_gamma_refusal(b'[{"detail": 1}]')
    assert not gamma_sync.is_gamma_refusal(b"<html>") and not gamma_sync.is_gamma_refusal(b"")
    assert gamma_sync.blocked(200, {}, b"") == "" and gamma_sync.blocked(404, {}, b"<html>") == ""
    cloudflare = {"Server": "cloudflare"}
    # the service another rule calls its own: any JSON (net_proxy.refused_network's)
    any_json = lambda b: b.lstrip().startswith((b"{", b"["))   # noqa: E731
    assert gamma_sync.blocked(403, cloudflare, b'{"error": 1}', own=any_json) == ""
    assert gamma_sync.blocked(403, cloudflare, b"<html>", own=any_json) == "cloudflare"
    assert gamma_sync.blocked(403, {"cf-mitigated": "challenge"}, b'{"detail": 1}') == "cloudflare"


def test_an_https_address_without_tls_is_not_https(serve, opened):
    here = serve(_json(200, {}))
    with pytest.raises(gamma_sync.NotHttps) as e:
        gamma_sync.Server(here.url.replace("http:", "https:"), "t", timeout=5).get_json("/api/sync/whoami")
    assert str(e.value) == "127.0.0.1 does not speak HTTPS at this address"
    assert gamma_sync.status_fields(e.value) == {"error_kind": "not_https"}


def test_a_handshake_cut_short_is_no_answer_not_no_https(opened):
    # a server restarting behind a proxy that accepts the connection: it reads the hello and hangs up
    import socket

    listener = socket.create_server(("127.0.0.1", 0))

    def cut():
        for _ in range(2):   # the routed opener may try twice
            try:
                conn, _addr = listener.accept()
            except OSError:
                return
            conn.recv(4096)
            conn.shutdown(socket.SHUT_RDWR)
            conn.close()

    threading.Thread(target=cut, daemon=True).start()
    url = f"https://127.0.0.1:{listener.getsockname()[1]}"
    try:
        with pytest.raises(gamma_sync.Unreachable) as e:
            gamma_sync.Server(url, "t", timeout=5).get_json("/api/sync/whoami")
    finally:
        listener.close()
    assert not isinstance(e.value, gamma_sync.NotHttps) and gamma_sync.status_fields(e.value) == {}
    for error in (ssl.SSLEOFError(8, "EOF occurred in violation of protocol"), ssl.SSLZeroReturnError(6, "closed"),
                  ssl.SSLSyscallError(5, "syscall")):
        assert gamma_sync.failure_of(url, url, urllib.error.URLError(error)) is None
    assert isinstance(gamma_sync.failure_of(url, url, urllib.error.URLError(ssl.SSLError(1, "wrong version"))),
                      gamma_sync.NotHttps)


def _verify_error(code, message, **attrs):
    error = ssl.SSLCertVerificationError(1, message)
    error.verify_code, error.verify_message = code, message
    for key, value in attrs.items():
        setattr(error, key, value)
    return error


def test_a_certificate_without_its_intermediate_is_worded_as_the_servers():
    alone = gamma_sync.CertificateRefused("nas.example.com", _verify_error(
        20, "unable to get local issuer certificate", chain_length=1))
    assert alone.missing_intermediate and "send the full chain (fullchain.pem)" in str(alone)
    assert str(alone).startswith("nas.example.com sends its certificate without the intermediate certificate")
    unknown = gamma_sync.CertificateRefused("nas.example.com", _verify_error(20, "unable to get local issuer certificate"))
    assert not unknown.missing_intermediate
    assert str(unknown).startswith("nas.example.com's certificate does not lead to an authority this computer trusts")
    other = gamma_sync.CertificateRefused("nas", _verify_error(18, "self-signed certificate", seen={"sha256": "ab"}))
    assert str(other) == "nas's certificate is not trusted on this computer (self-signed certificate)"
    assert gamma_sync.status_fields(other) == {"error_kind": "certificate", "cert": {"sha256": "ab"}}


def test_status_fields_walk_the_chain():
    from gamma.vault.http_remote import Unreachable

    try:
        try:
            raise gamma_sync.Redirected("http://nas:5000", "https://nas:5001/api/sync/whoami")
        except gamma_sync.SyncError as e:
            raise Unreachable(str(e)) from e
    except Unreachable as e:
        assert gamma_sync.status_fields(e) == {"error_kind": "redirected", "move_to": "https://nas:5001",
                                               "upgrade": True}
    assert gamma_sync.status_fields(ValueError("x")) == {}
    assert set(gamma_sync.STATUS_KEYS) == {"error_kind", "move_to", "upgrade", "cert", "blocked"}


def test_the_url_aware_seam_sees_the_servers_address(monkeypatch):
    asked = []

    def open_url(url, method, path, headers, body):
        asked.append((url, method, path))
        return 403, {"Content-Type": "text/html"}, io.BytesIO(b"<html>")

    monkeypatch.setattr(gamma_sync, "default_open_url", open_url)
    monkeypatch.setattr(gamma_sync, "default_open", lambda *a: pytest.fail("default_open asked"))
    server = gamma_sync.Server("https://nas.example.com/", "t")
    assert server.ask("GET", "/api/health") == (403, b"<html>")
    assert asked == [("https://nas.example.com", "GET", "/api/health")]
    assert gamma_sync.blocked(403, server.last_headers, b"<html>") == "proxy"


def test_the_cli_names_the_edit_for_a_moved_server(serve, tmp_path, capsys, opened):
    here = serve(_to(301, lambda h: "https://127.0.0.1:5001" + h.path))
    assert gamma_sync.main(["folders", "--server", here.url, "--token", "t"]) == 1
    assert capsys.readouterr().err.strip() == (
        f'gamma-sync: {here.url} now redirects to https://127.0.0.1:5001: if the server moved, set "server" '
        f'to "https://127.0.0.1:5001" in .gamma-sync.json')


def test_gamma_sync_imports_the_standard_library_only():
    """It is copied anywhere as one file: nothing but the standard library,
    and truststore only where it may be missing (inside try/except
    ImportError)."""
    path = Path(gamma_sync.__file__)
    tree = ast.parse(path.read_text(encoding="utf-8"))
    guarded = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Try) and any(isinstance(h.type, ast.Name) and h.type.id == "ImportError"
                                             for h in node.handlers):
            guarded.update(id(n) for stmt in node.body for n in ast.walk(stmt))
    outside = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            names = [a.name for a in node.names] if isinstance(node, ast.Import) else [node.module or ""]
            for name in names:
                top = name.split(".")[0]
                if node.__class__ is ast.ImportFrom and node.level:
                    outside.append(f"relative import {name}")
                elif top not in sys.stdlib_module_names and not (top == "truststore" and id(node) in guarded):
                    outside.append(name)
    assert outside == []
