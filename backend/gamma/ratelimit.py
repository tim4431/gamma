"""A tiny in-process rate limiter for abuse-prone unauthenticated endpoints
(login, guest login). Fixed-window counters in a dict — no external store,
resets on restart. Bounded: past MAX_KEYS counters the expired windows are
swept, and if that is not enough the oldest go.

Not a substitute for an edge/WAF rate limit on a large public deployment, but it
stops trivial online password guessing and guest-session floods from a single
host.
"""

import threading
import time

from fastapi import HTTPException, Request

# key -> [window_start_monotonic, count, window_seconds]
_buckets: dict[str, list] = {}
_lock = threading.Lock()
MAX_KEYS = 10_000


def client_ip(request: Request) -> str:
    """The client's address: the socket peer. Behind a reverse proxy that is
    the proxy, unless uvicorn rewrites it from X-Forwarded-For, which it
    does only for a peer listed in FORWARDED_ALLOW_IPS (default 127.0.0.1,
    the Docker image's too — a deployment behind a proxy names it). The
    header itself is never read here: anyone can send it."""
    return request.client.host if request.client else "?"


def _sweep(now: float) -> None:
    """Drop expired windows; if the table is still full (a flood of fresh
    keys), drop the oldest windows down to half. Called with the lock held."""
    for key in [k for k, b in _buckets.items() if now - b[0] >= b[2]]:
        del _buckets[key]
    if len(_buckets) >= MAX_KEYS:
        for key in sorted(_buckets, key=lambda k: _buckets[k][0])[:len(_buckets) - MAX_KEYS // 2]:
            del _buckets[key]


def check(key: str, max_hits: int, window_seconds: int, on_first_exceed=None) -> None:
    """Count one hit for `key`; raise 429 once it exceeds `max_hits` within the
    current window. Windows are fixed and start on the first hit.
    `on_first_exceed(count)` runs on the one hit that crosses the limit — the
    place to log a warning once per window rather than once per request."""
    now = time.monotonic()
    with _lock:
        bucket = _buckets.get(key)
        if bucket is None or now - bucket[0] >= window_seconds:
            if bucket is None and len(_buckets) >= MAX_KEYS:
                _sweep(now)
            bucket = _buckets[key] = [now, 0, window_seconds]
        bucket[1] += 1
        count, started = bucket[1], bucket[0]
    if count > max_hits:
        if count == max_hits + 1 and on_first_exceed is not None:
            on_first_exceed(count)
        retry = max(1, int(window_seconds - (now - started)))
        raise HTTPException(
            status_code=429,
            detail="Too many attempts. Wait a bit and try again.",
            headers={"Retry-After": str(retry)},
        )


def reset(key: str) -> None:
    """Clear a key's counter — call on a successful login so a legitimate user
    who mistyped a few times isn't locked out by their own success."""
    with _lock:
        _buckets.pop(key, None)
