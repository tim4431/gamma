"""In-memory server log behind Settings → Diagnostics → "Server log".

Everything the backend logs through the shared `log` logger (plus uvicorn's
error/traceback logger) is scrubbed for secret-shaped substrings and kept in a
bounded ring buffer; admins read it via GET /api/admin/logs. Console output is
unchanged — the buffer is a debugging window, not an audit trail, and holds
nothing across restarts.

Scrubbing happens at insert time so a secret never sits in the buffer or
crosses the wire. The one deliberate exception lives in seed.py: the one-time
seeded admin password is a raw print() and must never be routed through here.
"""

import logging
import re
import sys
import threading
import time
from collections import deque

log = logging.getLogger("gamma")

_MAX_ENTRIES = 2000
_buf = deque(maxlen=_MAX_ENTRIES)
_lock = threading.Lock()
_seq = 0
_counts = {"info": 0, "warning": 0, "error": 0}  # since startup, beyond what the ring still holds
_last_seq = {"info": 0, "warning": 0, "error": 0}  # seq of the newest line of each level

_SCRUB_RULES = (
    # Bearer/sk- first: the key=value rule below would otherwise consume the
    # word "Bearer" in "Authorization: Bearer <token>" and leave the token.
    (re.compile(r"(?i)\bbearer\s+[a-z0-9._~+/-]{8,}={0,2}"), "Bearer ***"),
    # OpenAI/Anthropic-style keys (sk-, sk-proj-, sk-ant-…)
    (re.compile(r"\bsk-[A-Za-z0-9_-]{8,}"), "sk-***"),
    # key=value / key: value shapes (query strings, exception messages that
    # echo request bodies or headers)
    (re.compile(r"(?i)\b(password|passwd|secret|token|api[_-]?key|authorization)(\s*[=:]\s*)[^\s&'\"]+"),
     r"\1\2***"),
    # Long urlsafe-base64 runs: session/share tokens are token_urlsafe(32) →
    # 43 chars. The 40+ threshold spares block ids and sha256[:24] upload
    # names, which are worth keeping readable in a debugging log.
    (re.compile(r"\b[A-Za-z0-9_-]{40,}\b"), "***"),
)


def scrub(text: str) -> str:
    for pattern, repl in _SCRUB_RULES:
        text = pattern.sub(repl, text)
    return text


class _BufferHandler(logging.Handler):
    def emit(self, record):
        try:
            msg = scrub(self.format(record))
        except Exception:
            return
        global _seq
        with _lock:
            _seq += 1
            _buf.append({"seq": _seq, "t": time.time(), "level": record.levelname, "msg": msg})
            key = "error" if record.levelno >= logging.ERROR else "warning" if record.levelno >= logging.WARNING else "info"
            _counts[key] += 1
            _last_seq[key] = _seq


def counts() -> dict:
    """Lines logged since startup by level: ``{info, warning, error}`` — the
    dashboard's tiles, unaffected by the ring buffer's cap."""
    with _lock:
        return dict(_counts)


def last_seq(level: str) -> int:
    """The seq of the newest line at ``level`` (``warning`` / ``error``),
    0 when none was logged since startup. A notice's fingerprint
    (gamma/notices.py): unchanged until another such line arrives."""
    with _lock:
        return _last_seq.get(level, 0)


def tail(after: int = 0) -> list:
    """Entries with seq > after, oldest first (the poll cursor for the UI)."""
    with _lock:
        return [e for e in _buf if e["seq"] > after]


def setup_logging():
    """Wire the gamma logger to the console (same look as the old print()s)
    and the ring buffer, and capture uvicorn's error/traceback lines too.
    Idempotent: uvicorn --reload re-imports the app module.

    uvicorn.access is deliberately NOT captured — its lines include full query
    strings (?share=… tokens); the middleware's [http] line covers requests
    with path-only URLs.
    """
    if any(isinstance(h, _BufferHandler) for h in log.handlers):
        return
    buffer_handler = _BufferHandler()
    buffer_handler.setFormatter(logging.Formatter("%(message)s"))  # + traceback when exc_info is set
    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(logging.Formatter("%(message)s"))
    log.setLevel(logging.INFO)
    log.propagate = False
    log.addHandler(console)
    log.addHandler(buffer_handler)
    uvicorn_error = logging.getLogger("uvicorn.error")  # keeps its own console handlers
    if not any(isinstance(h, _BufferHandler) for h in uvicorn_error.handlers):
        uvicorn_error.addHandler(buffer_handler)
    if not any(isinstance(f, _InvalidRequestDetail) for f in uvicorn_error.filters):
        uvicorn_error.addFilter(_InvalidRequestDetail())
    access = logging.getLogger("uvicorn.access")
    if not any(isinstance(f, _QuietPolls) for f in access.filters):
        access.addFilter(_QuietPolls())


class _QuietPolls(logging.Filter):
    """Drops the Background tasks tray's answered polls from uvicorn's access
    log: ``GET /api/jobs`` comes every 1.5 s while a job runs. A failed or
    slow poll still has its ``[http]`` line (auth.py)."""

    def filter(self, record):
        args = record.args
        if isinstance(args, tuple) and len(args) == 5:  # client, method, path, http version, status
            _, method, path, _, status = args
            if method == "GET" and path == "/api/jobs" and isinstance(status, int) and status < 400:
                return False
        return True


_INVALID_REQUEST = "Invalid HTTP request received."


class _InvalidRequestDetail(logging.Filter):
    """Says who sent uvicorn's bare "Invalid HTTP request received." and what
    it looked like. uvicorn logs it while handling the parser's error, inside
    the protocol method that holds the connection and the bytes it read, so
    they are read off the stack. A TLS handshake is the usual one: something
    opened https:// on this plain-HTTP port, mostly scanners on an exposed
    port."""

    def filter(self, record):
        if record.msg == _INVALID_REQUEST and not record.args:
            try:
                record.msg = _INVALID_REQUEST[:-1] + _invalid_request_detail()
            except Exception:
                pass
        return True


def _invalid_request_detail() -> str:
    peer = data = None
    frame = sys._getframe()
    while frame is not None and data is None:
        conn = frame.f_locals.get("self")
        if type(conn).__module__.startswith("uvicorn.protocols.http"):
            peer = peer or getattr(conn, "client", None)
            if isinstance(frame.f_locals.get("data"), bytes):
                data = frame.f_locals["data"]
        frame = frame.f_back
    detail = f" from {peer[0]}:{peer[1]}" if peer else ""
    if not data:
        return detail + "."
    if data[:2] == b"\x16\x03":
        return detail + ": a TLS handshake (https:// sent to this plain-HTTP port)"
    if data.startswith(b"PRI * HTTP/2.0"):
        return detail + ": HTTP/2 without TLS, which this server does not speak"
    if data.startswith(b"PROXY "):
        return detail + ": a PROXY protocol header, which this server does not read"
    # The first line, its query left out (it can carry ?share= tokens), as a
    # bytes repr without the b so control bytes show escaped.
    line, query, _ = data.split(b"\n", 1)[0].rstrip(b"\r")[:80].partition(b"?")
    detail += ": starts " + repr(line + b"?..." if query else line)[1:]
    exc = sys.exc_info()[1]
    return detail + (f" ({exc})" if exc and str(exc) else "")
