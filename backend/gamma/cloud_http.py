"""The HTTP of the cloud backup stores (docs/dev/debugging.md "Cloud
destinations"): Google Drive (gamma/gdrive.py) and OneDrive
(gamma/onedrive.py) are REST APIs reached with plain urllib on the server's
network route (gamma/net_proxy.py), signed in to through OAuth
(gamma/cloud_oauth.py). What they share is here: one transport, the pauses
between tries, how a refusal is read, and the errors every cloud store
raises.

``_request`` is the one transport and the tests' single patch point
(tests/cloud_fake.py routes it to the fake services by address). A redirect
to another host drops the Authorization header, as requests does (a
OneDrive download is sent to a pre-signed address elsewhere). ``retried``
says which answers are worth another try (a 429, a 5xx, a rate-limit
refusal); ``pause`` is the exponential pause before it, or the service's
own ``Retry-After``; ``_sleep`` waits it (instant in the tests).

A call a service refuses or that gets no answer raises ``CloudError``, a
``StoreError`` as the bucket's ``S3Error`` is (``CloudUnreachable`` for no
answer at all); no client, no connection or one the service no longer
honours raises ``CloudConfigError``, a ``StoreConfigError``.
"""

from __future__ import annotations

import json
import random
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from http.client import HTTPException
from typing import Any

from . import net_proxy
from .backup_store import StoreConfigError, StoreError

TIMEOUT_S = 60
ATTEMPTS = 5
INTERACTIVE_ATTEMPTS = 2    # a call the pane waits on (a code asked for, a folder listed)
MAX_PAUSE_S = 64
READ_CHUNK = 1024 * 1024    # what a download reads at a time
RETRIED = (429, 500, 502, 503, 504)
RATE_REASONS = ("userRateLimitExceeded", "rateLimitExceeded", "activityLimitReached")


class CloudError(StoreError):
    """A cloud service refused a call or could not be reached."""


class CloudUnreachable(CloudError):
    """No answer came."""


class CloudConfigError(StoreConfigError):
    """No cloud store that can work: no client, no connection, a connection
    the service no longer honours (revoked, unused for months, made through
    another client)."""


@dataclass
class Response:
    """One HTTP answer: ``headers`` with lower-case names; ``body`` is b""
    when it was streamed into a sink."""
    status: int
    headers: dict[str, str]
    body: bytes

    def json(self) -> Any:
        try:
            return json.loads(self.body or b"{}")
        except ValueError:
            return {}


class _Redirect(urllib.request.HTTPRedirectHandler):
    """Follow a redirect, without the Authorization header when it leaves
    the host."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        new = super().redirect_request(req, fp, code, msg, headers, newurl)
        if new is not None and urllib.parse.urlsplit(newurl).hostname != urllib.parse.urlsplit(req.full_url).hostname:
            new.remove_header("Authorization")
        return new


_opener = net_proxy.build_opener(_Redirect)


def _lower(headers) -> dict[str, str]:
    return {name.lower(): value for name, value in headers.items()} if headers else {}


def unreachable(who: str, e: BaseException) -> CloudUnreachable:
    reason = getattr(e, "reason", None) or str(e) or type(e).__name__
    return CloudUnreachable(f"{who} could not be reached: {reason}")


def _read(resp, n: int = -1) -> bytes:
    try:
        return resp.read(n) if n > 0 else resp.read()
    except (OSError, HTTPException) as e:
        raise unreachable(urllib.parse.urlsplit(resp.geturl() or "").hostname or "The service", e) from None


def _request(method: str, url: str, headers: dict, body: bytes | None = None, timeout: float = TIMEOUT_S,
             sink=None) -> Response:
    """One HTTP call. With ``sink`` a 2xx body is streamed into it and
    ``Response.body`` is b"". CloudUnreachable when no answer came; an
    error writing the sink is the sink's own. The tests replace it."""
    req = urllib.request.Request(url, data=body, method=method, headers=headers)
    try:
        resp = _opener.open(req, timeout=timeout)
    except urllib.error.HTTPError as e:
        with e:
            return Response(e.code, _lower(e.headers), _read(e))
    except (OSError, HTTPException) as e:  # a URLError is an OSError
        raise unreachable(urllib.parse.urlsplit(url).hostname or "The service", e) from None
    with resp:
        answered = _lower(resp.headers)
        if sink is None:
            return Response(resp.status, answered, _read(resp))
        while chunk := _read(resp, READ_CHUNK):
            sink.write(chunk)
        return Response(resp.status, answered, b"")


def request(method: str, url: str, headers: dict, body: bytes | None = None, *, sink=None) -> Response:
    """``_request``, looked up at each call so a test's patch takes."""
    return _request(method, url, headers, body, sink=sink)


def _sleep(seconds: float) -> None:
    time.sleep(seconds)


def sleep(seconds: float) -> None:
    _sleep(seconds)


def pause(attempt: int, resp: Response | None = None) -> float:
    """The pause before try ``attempt + 1``: the service's ``Retry-After``
    (seconds) when it gave one, else exponential with up to a second of
    jitter; at most MAX_PAUSE_S."""
    after = (resp.headers.get("retry-after", "") if resp is not None else "").strip()
    if after.isdigit():
        return min(float(after), MAX_PAUSE_S)
    return min(2 ** attempt + random.random(), MAX_PAUSE_S)


def error(resp: Response) -> tuple[str, str]:
    """A service's ``(reason, message)`` of an error answer: Google's
    ``{error: {errors: [{reason}], message}}``, Microsoft Graph's
    ``{error: {code, message}}`` or OAuth's ``{error, error_description}``."""
    data = resp.json()
    err = data.get("error") if isinstance(data, dict) else None
    if isinstance(err, dict):
        reasons = [e.get("reason", "") for e in err.get("errors") or () if isinstance(e, dict)]
        return (reasons[0] if reasons else str(err.get("code") or err.get("status") or "")), str(err.get("message") or "")
    if isinstance(err, str):
        return err, str(data.get("error_description") or "")
    return "", resp.body[:200].decode("utf-8", "replace").strip()


def retried(resp: Response) -> bool:
    """Whether an answer is worth another try: a 429, a 5xx, or a refusal
    for going too fast."""
    return resp.status in RETRIED or (resp.status == 403 and error(resp)[0] in RATE_REASONS)


def call(method: str, url: str, auth, *, attempts: int = ATTEMPTS, params: dict | None = None,
         body: bytes | None = None, headers: dict | None = None, sink=None) -> Response:
    """One API call with the access token ``auth(renew)`` gives: renewed
    once on a 401, tried again on a 429, a 5xx, a rate-limit refusal or no
    answer, at most ``attempts`` times. The last answer, whatever its
    status; CloudUnreachable once no try got one. A ``sink`` (a file) is
    emptied before each try, so a retried download starts over."""
    if params:
        url += ("&" if "?" in url else "?") + urllib.parse.urlencode(params)
    renewed, attempt = False, 0
    while True:
        sent = {"Authorization": f"Bearer {auth(False)}", **(headers or {})}
        if sink is not None:
            sink.seek(0)
            sink.truncate()
        resp = None
        try:
            resp = request(method, url, sent, body, sink=sink)
        except CloudUnreachable:
            attempt += 1
            if attempt >= attempts:
                raise
        else:
            if resp.status == 401 and not renewed:
                renewed = True
                auth(True)
                continue
            attempt += 1
            if not retried(resp) or attempt >= attempts:
                return resp
        sleep(pause(attempt - 1, resp))


def label(folder: str) -> str:
    """A folder's path as a message shows it: ``Backups › Gamma``."""
    return " › ".join(filter(None, folder.split("/")))


def where(store: str, folder: str) -> str:
    """A store's folder as the log and the messages name it: ``Google
    Drive › Backups › Gamma``."""
    return " › ".join(filter(None, [store, label(folder)]))


def form(url: str, fields: dict, attempts: int = ATTEMPTS) -> Response:
    """A POST of form fields to an OAuth endpoint, tried again as every
    call is; the last answer, whatever its status. CloudUnreachable once no
    try got an answer."""
    body = urllib.parse.urlencode(fields).encode("ascii")
    headers = {"Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"}
    attempt, resp = 0, None
    while True:
        try:
            resp = request("POST", url, headers, body)
        except CloudUnreachable:
            attempt += 1
            if attempt >= attempts:
                raise
            resp = None
        else:
            attempt += 1
            if not retried(resp) or attempt >= attempts:
                return resp
        sleep(pause(attempt - 1, resp))
