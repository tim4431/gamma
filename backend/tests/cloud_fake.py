"""What the fake cloud services share (tests/gdrive_fake.py FakeGoogle,
tests/onedrive_fake.py FakeMicrosoft): their answers (``Reply``), a request
left unanswered (``Dropped``), and the two ways gamma reaches them.

``install(monkeypatch, fake)`` points gamma at a fake in-process:
``cloud_http._request``, the clouds' one transport, routed by address to
whichever installed fake's ``base`` (or one of its ``bases``) the URL
starts with (two may be installed at once), redirects followed as urllib follows them (the
Authorization header dropped on another host), a 2xx body streamed into a
sink; ``cloud_http._sleep`` keeps the pauses asked in each fake's
``sleeps`` instead of waiting. It also empties each cloud account's state
held in memory (a code waiting, an access token), which the module keeps
across tests. ``serve(fake, port)`` answers over HTTP on 127.0.0.1 for the
browser e2e. A fake has ``base``, ``sleeps`` and ``handle(method, url,
headers, body) → Reply``.
"""

import urllib.parse
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread


@dataclass
class Reply:
    status: int
    headers: dict = field(default_factory=dict)
    body: bytes = b""


class Dropped(Exception):
    """A request the fake answers with nothing (``fail_next(..., 0)``)."""


def _route(fakes, url):
    for fake in fakes:
        if any(url.startswith(base) for base in getattr(fake, "bases", [fake.base])):
            return fake
    raise AssertionError(f"no fake service answers {url}")


def install(monkeypatch, fake):
    from gamma import cloud_account, cloud_http

    fakes = getattr(cloud_http._request, "fakes", None)
    if fakes is None:
        fakes = []

        def request(method, url, headers, body=None, timeout=None, sink=None):
            for _hop in range(5):
                try:
                    reply = _route(fakes, url).handle(method, url, headers, body)
                except Dropped:
                    raise cloud_http.CloudUnreachable(f"{urllib.parse.urlsplit(url).hostname} could not be reached: "
                                                      "fake") from None
                if reply.status in (301, 302, 303, 307) and reply.headers.get("location"):
                    new = urllib.parse.urljoin(url, reply.headers["location"])
                    if urllib.parse.urlsplit(new).hostname != urllib.parse.urlsplit(url).hostname:
                        headers = {k: v for k, v in headers.items() if k.lower() != "authorization"}
                    method, body, url = ("GET", None, new) if reply.status == 303 or method == "GET" else (method, body, new)
                    continue
                if sink is not None and 200 <= reply.status < 300:
                    sink.write(reply.body)
                    return cloud_http.Response(reply.status, reply.headers, b"")
                return cloud_http.Response(reply.status, reply.headers, reply.body)
            raise AssertionError(f"too many redirects from {url}")

        request.fakes = fakes
        monkeypatch.setattr(cloud_http, "_request", request)
        monkeypatch.setattr(cloud_http, "_sleep", lambda s: [f.sleeps.append(s) for f in fakes])
    fakes.append(fake)
    for cloud in cloud_account.ACCOUNTS.values():
        for name, empty in (("_pending", None), ("_last_error", ""), ("_browser", {}), ("_held", None)):
            monkeypatch.setattr(cloud, name, empty)
    return fake


def serve(fake, port: int = 0):
    """Answer over HTTP on 127.0.0.1, in a daemon thread: ``(httpd, base)``,
    ``fake.base`` now the server's own. Stop it with ``httpd.shutdown()``."""

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def _any(self):
            length = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(length) if length else b""
            try:
                reply = fake.handle(self.command, fake.base + self.path, dict(self.headers.items()), body)
            except Dropped:
                self.close_connection = True
                return
            self.send_response(reply.status)
            for name, value in reply.headers.items():
                self.send_header(name, value)
            self.send_header("Content-Length", str(len(reply.body)))
            self.end_headers()
            self.wfile.write(reply.body)

        do_GET = do_POST = do_PUT = do_DELETE = do_PATCH = _any

        def log_message(self, *args):
            pass

    httpd = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    fake.base = f"http://127.0.0.1:{httpd.server_port}"
    Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, fake.base
