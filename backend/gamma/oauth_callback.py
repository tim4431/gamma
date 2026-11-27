"""The loopback listener a subscription sign-in catches its OAuth redirect
on when the browser shares the server's machine: gamma/chatgpt_oauth.py on
Codex CLI's registered localhost:1455, gamma/gemini_oauth.py on a free port
(Google lets a desktop client come back to any loopback port).

Each flow has one ``Listener``, open while one of its sign-ins waits for
its redirect and closed once none does. The flow's ``capture(query)`` takes
the redirect's query and says what the browser tab shows; the listener
owns the port, the thread and the page.
"""

import http.server
import os
import threading
import urllib.parse

from . import html_page
from .logbuf import log


class _Server(http.server.HTTPServer):
    # Windows' SO_REUSEADDR would let this bind a port another program
    # (Codex CLI's own login) is listening on; elsewhere it only skips
    # TIME_WAIT, which a second sign-in soon after the first needs.
    allow_reuse_address = os.name != "nt"
    timeout = 1


class Listener:
    """``name`` names the thread and the log lines; ``path`` is the
    redirect's path; ``lock`` is the flow's, under which ``waiting()`` says
    whether a sign-in still waits for its redirect; ``capture(query)``
    returns ``(ok, message)``; ``titles`` are the tab's (signed in, failed)
    titles and ``done`` its message when ``capture`` gives none."""

    def __init__(self, *, name: str, path: str, lock, waiting, capture, titles: tuple, done: str):
        self.name, self.path, self.lock = name, path, lock
        self.waiting, self.capture, self.titles, self.done = waiting, capture, titles, done
        self.server = None  # the running server, under ``lock``

    def open(self, port: int = 0) -> int:
        """Listen on ``port`` (0: a free one) unless already listening: the
        port listened on, 0 when it could not be opened (taken by another
        program, another Gamma server)."""
        with self.lock:
            if self.server is None:
                try:
                    self.server = _Server(("127.0.0.1", port), self._handler())
                except OSError as e:
                    where = f"localhost:{port} is taken" if port else "no loopback port is free"
                    log.info(f"[{self.name}] {where} ({e}); the sign-in ends with a paste")
                    return 0
                threading.Thread(target=self._serve, args=(self.server,), name=f"{self.name}-callback",
                                 daemon=True).start()
            return self.server.server_address[1]

    def _serve(self, server) -> None:
        while True:
            server.handle_request()
            with self.lock:
                if not self.waiting():
                    self.server = None
                    server.server_close()
                    return

    def _handler(self):
        listener = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                parts = urllib.parse.urlsplit(self.path)
                if parts.path != listener.path:
                    self.send_error(404)
                    return
                ok, message = listener.capture({k: v[0] for k, v in urllib.parse.parse_qs(parts.query).items()})
                body = html_page.page(listener.titles[0] if ok else listener.titles[1],
                                      message or listener.done).encode()
                self.send_response(200 if ok else 400)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                for name, value in html_page.headers().items():
                    self.send_header(name, value)
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):  # the server log is logbuf's
                pass
        return Handler
