"""An in-memory Google for the tests and the browser e2e: the OAuth device
flow, tokens and revocation, and the Drive API v3 as gamma/gdrive.py uses
it (about, files listed by query, folders made, resumable uploads and
updates, downloads, deletes), with Google's refusals.

``FakeGoogle.install(monkeypatch)`` points gamma at it in-process (the
test gives gamma the app it knows, ``CLIENT_ID`` and ``CLIENT_SECRET``):
``gdrive._request`` to ``request``, the pauses to ``sleeps`` (instant; the pauses asked are kept). A test drives
the device flow with ``approve``, ``deny`` and ``expire``; ``revoke_all``
makes every refresh token invalid (the person removed the app);
``expire_access`` makes the next call's access token answer 401.
``fail_next(match, status, times)`` answers the next requests whose
``METHOD path`` contains ``match`` with ``status`` (0: no answer at all);
``quota`` caps the bytes the account may hold. ``calls`` lists every
request as ``"METHOD path"``. ``serve()`` answers the same over HTTP for
the browser e2e, with ``POST /_fake/approve`` (``deny``, ``expire``) and
``GET /_fake/state`` (``{files: {path: size}, revoked}``) for the
scenario's side: ``python -I backend/tests/gdrive_fake.py --port 0``
prints ``GOOGLE <base>`` and serves until killed. Every endpoint, OAuth's
and Drive's, is on the one origin, told apart by path.

Google's authorization page is ``authorize(url)`` in-process and ``GET
/o/oauth2/v2/auth`` over HTTP: the person allows at once (``browser_deny``
to refuse), and the browser is sent back to the redirect URI with a code
bound to the PKCE challenge; the code is traded once, with its verifier
and redirect URI, by the "Desktop app" client (``DESKTOP_ID``), which
cannot ask for a device code, as the "TVs" one (``CLIENT_ID``) cannot
open the page. A refresh token is renewed only by the client that made it.

What it checks as Google does: the client ID and secret, a Bearer token on
every API call (none on a session's chunks), the device code's states,
``drive.file``'s reach (only the files this client made), queries of the
shapes gamma sends (``name = '…'``, ``'…' in parents``, ``trashed =
false``, ``mimeType = / != '…'``, joined by ``and``; ``orderBy
createdTime``), chunks that start where the last ended, ``Content-Range:
bytes */<size>`` answered with a 308 and the ``Range`` held, the last
chunk completing the file. Not checked against Google itself.
"""

import argparse
import base64
import hashlib
import itertools
import json
import re
import time
import urllib.parse
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Lock, Thread

CLIENT_ID, CLIENT_SECRET = "fake-client.apps.googleusercontent.com", "GOCSPX-fake"
DESKTOP_ID, DESKTOP_SECRET = "fake-desktop.apps.googleusercontent.com", "GOCSPX-desktop"
SECRETS = {CLIENT_ID: CLIENT_SECRET, DESKTOP_ID: DESKTOP_SECRET}
SCOPE = "https://www.googleapis.com/auth/drive.file"   # gdrive.SCOPE
DEVICE_GRANT = "urn:ietf:params:oauth:grant-type:device_code"
FOLDER = "application/vnd.google-apps.folder"


@dataclass
class Reply:
    status: int
    headers: dict = field(default_factory=dict)
    body: bytes = b""


class Dropped(Exception):
    """A request the fake answers with nothing (``fail_next(..., 0)``)."""


class FakeGoogle:
    def __init__(self, email="someone@gmail.com", base="https://google.fake", interval=5):
        self.email = email
        self.base = base
        self._lock = Lock()
        self.files = {}          # id → {id, name, mimeType, parents, data, created, modified, trashed, mine}
        self.devices = {}        # device_code → {user_code, state, scope}
        self.refresh = {}        # refresh token → valid
        self.made_by = {}        # refresh token → the client that made it
        self.codes = {}          # authorization code → {client_id, redirect_uri, challenge, used}
        self.browser_deny = False
        self.access = {}         # access token → refresh token it came from
        self.sessions = {}       # upload id → {file_id | None, meta, size, data}
        self.calls, self.sleeps, self.failures = [], [], []
        self.quota = 0           # 0: unlimited
        self.granted = SCOPE
        self.interval = interval
        self._ids = itertools.count(1)
        self._clock = time.mktime((2026, 11, 18, 10, 0, 0, 0, 0, 0))

    # --- the test's levers -------------------------------------------------

    def install(self, monkeypatch):
        from gamma import gdrive

        monkeypatch.setenv("GAMMA_GOOGLE_OAUTH_BASE", self.base)
        monkeypatch.setenv("GAMMA_GOOGLE_API_BASE", self.base)
        monkeypatch.setenv("GAMMA_GOOGLE_ACCOUNTS_BASE", self.base)
        monkeypatch.setattr(gdrive, "_request", self.request)
        monkeypatch.setattr(gdrive, "_sleep", self.sleeps.append)
        monkeypatch.setattr(gdrive, "_tokens", {})
        return self

    def _device(self, state):
        for device in self.devices.values():
            if device["state"] == "pending":
                device["state"] = state

    def approve(self):
        self._device("approved")

    def deny(self):
        self._device("denied")

    def expire(self):
        self._device("expired")

    def authorize(self, url):
        """The person on Google's authorization page at ``url``: the address
        the browser is sent back to, with a code and the state (or
        ``error=access_denied`` when ``browser_deny``)."""
        reply = self.handle("GET", url, {})
        assert reply.status == 302, reply
        return reply.headers["location"]

    def revoke_all(self):
        self.refresh = {t: False for t in self.refresh}
        self.access.clear()

    def expire_access(self):
        self.access.clear()

    def fail_next(self, match, status, times=1):
        self.failures.append([match, status, times])

    def mine(self):
        """``{path: bytes}`` of every file (not folder) this client made and
        not trashed, the path from My Drive's root."""
        out = {}
        for f in self.files.values():
            if f["mimeType"] != FOLDER and f["mine"] and not self._trashed(f):
                out[self.path(f["id"])] = f["data"]
        return out

    def path(self, file_id):
        parts, at = [], self.files.get(file_id)
        while at:
            parts.append(at["name"])
            at = self.files.get(at["parents"][0]) if at["parents"] else None
        return "/".join(reversed(parts))

    def add(self, name, parent="root", folder=False, data=b"", mine=True):
        """A file or folder put there directly (by the test, or by the person
        when ``mine`` is false: this client cannot see it)."""
        fid = f"f{next(self._ids)}"
        self._clock += 1
        stamp = self._clock
        self.files[fid] = {"id": fid, "name": name, "mimeType": FOLDER if folder else "application/zip",
                           "parents": [parent], "data": data, "created": stamp, "modified": stamp,
                           "trashed": False, "mine": mine}
        return fid

    def trash(self, file_id):
        self.files[file_id]["trashed"] = True

    # --- the transport ------------------------------------------------------

    def request(self, method, url, headers, body=None, timeout=None, sink=None):
        """``gdrive._request`` in-process: the answer as gdrive's Response, a
        2xx body streamed into ``sink``."""
        from gamma import gdrive

        try:
            reply = self.handle(method, url, headers, body)
        except Dropped:
            raise gdrive.DriveUnreachable("Google could not be reached: fake") from None
        if sink is not None and 200 <= reply.status < 300:
            sink.write(reply.body)
            return gdrive.Response(reply.status, reply.headers, b"")
        return gdrive.Response(reply.status, reply.headers, reply.body)

    def handle(self, method, url, headers, body=None):
        """One request, routed by its path; Dropped for one to leave
        unanswered."""
        with self._lock:
            return self._handle(method, url, headers, body)

    def _handle(self, method, url, headers, body):
        parts = urllib.parse.urlsplit(url)
        query = dict(urllib.parse.parse_qsl(parts.query, keep_blank_values=True))
        path = parts.path
        line = f"{method} {path}"
        if path.startswith("/_fake/"):
            return self._control(method, path)
        self.calls.append(line)
        for failure in self.failures:
            if failure[0] in line and failure[2] > 0:
                failure[2] -= 1
                if failure[1] == 0:
                    raise Dropped()
                return self._error(failure[1], "backendError" if failure[1] >= 500 else "rateLimitExceeded", "fake")
        headers = {k.lower(): v for k, v in headers.items()}
        if path in ("/device/code", "/token", "/revoke"):
            return self._oauth(path, dict(urllib.parse.parse_qsl((body or b"").decode())))
        if path == "/o/oauth2/v2/auth":
            return self._page(query)
        if path == "/upload/session":
            return self._chunk(query["upload_id"], headers, body or b"")
        auth = headers.get("authorization", "")
        if not auth.startswith("Bearer ") or auth[7:] not in self.access:
            return self._error(401, "authError", "Invalid Credentials")
        return self._api(method, path, query, headers, body)

    def _control(self, method, path):
        """The e2e scenario's side of the fake."""
        if method == "POST" and path in ("/_fake/approve", "/_fake/deny", "/_fake/expire"):
            self._device({"approve": "approved", "deny": "denied", "expire": "expired"}[path[7:]])
            return self._json(200, {})
        if method == "GET" and path == "/_fake/state":
            files = {self.path(f["id"]): len(f["data"]) for f in self.files.values()
                     if f["mimeType"] != FOLDER and f["mine"] and not self._trashed(f)}
            return self._json(200, {"files": files, "revoked": sum(1 for ok in self.refresh.values() if not ok)})
        return self._json(404, {})

    @staticmethod
    def _json(status, data, headers=None):
        return Reply(status, {"content-type": "application/json", **(headers or {})}, json.dumps(data).encode())

    def _error(self, status, reason, message):
        return self._json(status, {"error": {"code": status, "message": message, "errors": [{"reason": reason}]}})

    def _oauth_error(self, status, error):
        return self._json(status, {"error": error, "error_description": error})

    # --- OAuth -----------------------------------------------------------------

    def _oauth(self, path, form):
        if path == "/revoke":
            token = form.get("token", "")
            if not self.refresh.get(token):
                return self._oauth_error(400, "invalid_token")
            self.refresh[token] = False
            self.access = {a: r for a, r in self.access.items() if r != token}
            return self._json(200, {})
        client = form.get("client_id")
        if client not in SECRETS:
            return self._oauth_error(401, "invalid_client")
        if path == "/device/code":
            if client != CLIENT_ID:  # Google: "Invalid client type" for any other kind of client
                return self._oauth_error(401, "invalid_client")
            code = f"dev-{next(self._ids)}"
            self.devices[code] = {"user_code": f"ABCD-{len(self.devices) + 1:04d}", "state": "pending",
                                  "scope": form.get("scope", "")}
            return self._json(200, {"device_code": code, "user_code": self.devices[code]["user_code"],
                                    "verification_url": "https://www.google.com/device", "expires_in": 1800,
                                    "interval": self.interval})
        assert path == "/token", path
        if form.get("client_secret") != SECRETS[client]:
            return self._oauth_error(401, "invalid_client")
        grant = form.get("grant_type")
        if grant == "authorization_code":
            held = self.codes.get(form.get("code", ""))
            verifier = form.get("code_verifier", "").encode("ascii")
            challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier).digest()).rstrip(b"=").decode("ascii")
            if (held is None or held["used"] or held["client_id"] != client
                    or held["redirect_uri"] != form.get("redirect_uri") or held["challenge"] != challenge):
                return self._oauth_error(400, "invalid_grant")
            held["used"] = True
            refresh = f"rt-{next(self._ids)}"
            self.refresh[refresh], self.made_by[refresh] = True, client
            return self._json(200, {**self._mint(refresh), "refresh_token": refresh, "scope": self.granted})
        if grant == DEVICE_GRANT:
            device = self.devices.get(form.get("device_code", ""))
            if device is None or device["state"] in ("expired", "used"):
                return self._oauth_error(400, "expired_token" if device else "invalid_grant")
            if device["state"] == "pending":
                return self._oauth_error(428, "authorization_pending")
            if device["state"] == "denied":
                return self._oauth_error(403, "access_denied")
            device["state"] = "used"
            refresh = f"rt-{next(self._ids)}"
            self.refresh[refresh], self.made_by[refresh] = True, client
            return self._json(200, {**self._mint(refresh), "refresh_token": refresh, "scope": self.granted})
        if grant == "refresh_token":
            refresh = form.get("refresh_token", "")
            if not self.refresh.get(refresh) or self.made_by.get(refresh, client) != client:
                return self._oauth_error(400, "invalid_grant")
            return self._json(200, {**self._mint(refresh), "scope": self.granted})
        return self._oauth_error(400, "unsupported_grant_type")

    def _page(self, query):
        """Google's authorization page, the person allowing at once: the
        browser sent back with a code (or the refusal)."""
        back = query.get("redirect_uri", "")
        if (query.get("client_id") != DESKTOP_ID or query.get("response_type") != "code"
                or query.get("code_challenge_method") != "S256" or not back.startswith("http://")):
            return Reply(400, {"content-type": "text/plain"}, b"Error 400: invalid_request")
        if self.browser_deny:
            answer = {"error": "access_denied", "state": query.get("state", "")}
        else:
            code = f"ac-{next(self._ids)}"
            self.codes[code] = {"client_id": DESKTOP_ID, "redirect_uri": back,
                                "challenge": query.get("code_challenge", ""), "used": False}
            answer = {"code": code, "state": query.get("state", ""), "scope": self.granted}
        return Reply(302, {"location": f"{back}?{urllib.parse.urlencode(answer)}"})

    def _mint(self, refresh):
        access = f"at-{next(self._ids)}"
        self.access[access] = refresh
        return {"access_token": access, "expires_in": 3599, "token_type": "Bearer"}

    # --- Drive -----------------------------------------------------------------

    def _trashed(self, f):
        while f:
            if f["trashed"]:
                return True
            f = self.files.get(f["parents"][0]) if f["parents"] else None
        return False

    def _view(self, f):
        out = {"id": f["id"], "name": f["name"], "mimeType": f["mimeType"],
               "modifiedTime": time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime(f["modified"]))}
        if f["mimeType"] != FOLDER:
            out["size"] = str(len(f["data"]))
        return out

    def _matches(self, f, q):
        for term in re.split(r" and (?=(?:[^']*'[^']*')*[^']*$)", q):
            term = term.strip()
            if m := re.fullmatch(r"name = '((?:[^'\\]|\\.)*)'", term):
                if f["name"] != re.sub(r"\\(.)", r"\1", m.group(1)):
                    return False
            elif m := re.fullmatch(r"'([^']*)' in parents", term):
                if m.group(1) not in f["parents"]:
                    return False
            elif term == "trashed = false":
                if f["trashed"]:
                    return False
            elif m := re.fullmatch(r"mimeType (=|!=) '([^']*)'", term):
                if (f["mimeType"] == m.group(2)) != (m.group(1) == "="):
                    return False
            else:
                raise AssertionError(f"a query term the fake does not know: {term!r}")
        return True

    def _api(self, method, path, query, headers, body):
        if path == "/drive/v3/about":
            used = sum(len(f["data"]) for f in self.files.values())
            return self._json(200, {"user": {"emailAddress": self.email, "displayName": "Some One"},
                                    "storageQuota": {"limit": str(self.quota), "usage": str(used)}})
        if path == "/drive/v3/files" and method == "GET":
            found = [f for f in self.files.values() if f["mine"] and self._matches(f, query["q"])]
            if query.get("orderBy") == "createdTime":
                found.sort(key=lambda f: f["created"])
            size = int(query.get("pageSize", 100))
            start = int(query.get("pageToken", 0))
            page = {"files": [self._view(f) for f in found[start:start + size]]}
            if start + size < len(found):
                page["nextPageToken"] = str(start + size)
            return self._json(200, page)
        if path == "/drive/v3/files" and method == "POST":
            meta = json.loads(body)
            assert meta["mimeType"] == FOLDER
            return self._json(200, {"id": self.add(meta["name"], meta["parents"][0], folder=True)})
        if m := re.fullmatch(r"/drive/v3/files/([^/]+)", path):
            f = self.files.get(m.group(1))
            if f is None or not f["mine"]:
                return self._error(404, "notFound", "File not found")
            if method == "DELETE":
                del self.files[f["id"]]
                return Reply(204)
            assert method == "GET" and query.get("alt") == "media"
            return Reply(200, {"content-type": "application/octet-stream"}, f["data"])
        if m := re.fullmatch(r"/upload/drive/v3/files(?:/([^/]+))?", path):
            assert query.get("uploadType") == "resumable"
            if m.group(1) and (m.group(1) not in self.files or not self.files[m.group(1)]["mine"]):
                return self._error(404, "notFound", "File not found")
            upload = f"u{next(self._ids)}"
            self.sessions[upload] = {"file_id": m.group(1), "meta": json.loads(body),
                                     "size": int(headers["x-upload-content-length"]), "data": b""}
            return Reply(200, {"location": f"{self.base}/upload/session?upload_id={upload}"})
        raise AssertionError(f"a call the fake does not know: {method} {path}")

    def _chunk(self, upload, headers, body):
        session = self.sessions.get(upload)
        if session is None:
            return self._error(404, "notFound", "No such upload")
        span = headers["content-range"]
        if m := re.fullmatch(r"bytes \*/(\d+)", span):
            assert int(m.group(1)) == session["size"]
            if session["size"] == len(session["data"]):
                return self._complete(upload)
            return self._held(session)
        m = re.fullmatch(r"bytes (\d+)-(\d+)/(\d+)", span)
        start, end = int(m.group(1)), int(m.group(2))
        assert int(m.group(3)) == session["size"] and end - start + 1 == len(body)
        assert start == len(session["data"]), "a chunk must start where Drive's Range ended"
        if self.quota and sum(len(f["data"]) for f in self.files.values()) + end + 1 > self.quota:
            return self._error(403, "storageQuotaExceeded", "The user's Drive storage quota has been exceeded.")
        session["data"] += body
        if len(session["data"]) == session["size"]:
            return self._complete(upload)
        return self._held(session)

    @staticmethod
    def _held(session):
        held = len(session["data"])
        return Reply(308, {"range": f"bytes=0-{held - 1}"} if held else {})

    def _complete(self, upload):
        session = self.sessions.pop(upload)
        self._clock += 1
        if session["file_id"]:
            f = self.files[session["file_id"]]
            f["data"], f["modified"] = session["data"], self._clock
        else:
            meta = session["meta"]
            fid = self.add(meta["name"], meta["parents"][0], data=session["data"])
            f = self.files[fid]
        return self._json(200, {"id": f["id"], "size": str(len(f["data"]))})

    # --- over HTTP (the browser e2e) ----------------------------------------------

    def serve(self, port: int = 0):
        """Answer over HTTP on 127.0.0.1, in a daemon thread: ``(httpd,
        base)``, ``base`` now the fake's own. Stop it with
        ``httpd.shutdown()``."""
        fake = self

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
        self.base = f"http://127.0.0.1:{httpd.server_port}"
        Thread(target=httpd.serve_forever, daemon=True).start()
        return httpd, self.base


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description="Serve a fake Google: OAuth's device flow and the Drive API.")
    parser.add_argument("--port", type=int, default=0, help="port on 127.0.0.1 (0 = any free one)")
    parser.add_argument("--interval", type=int, default=2, help="the polling interval its device codes ask for")
    args = parser.parse_args(argv)
    httpd, base = FakeGoogle(interval=args.interval).serve(args.port)
    print(f"GOOGLE {base}", flush=True)
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        httpd.shutdown()


if __name__ == "__main__":
    main()
