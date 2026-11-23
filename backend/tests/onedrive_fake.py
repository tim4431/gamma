"""An in-memory Microsoft for the tests and the browser e2e: the identity
platform's device flow, sign-in page and tokens, and Microsoft Graph's
OneDrive as gamma/onedrive.py uses it, with Microsoft's refusals.

``FakeMicrosoft.install(monkeypatch)`` points gamma at it in-process
(tests/cloud_fake.py; the test gives gamma the app it knows,
``CLIENT_ID``). A test drives the device flow with ``approve``, ``deny`` and
``expire``; ``revoke_all`` makes every refresh token invalid (the person
removed the app); ``expire_access`` makes the next call's access token
answer 401; ``fail_next(match, status, times, retry_after)`` answers the
next requests whose ``METHOD path`` contains ``match`` with ``status`` (0:
no answer at all); ``quota`` caps the bytes the account may hold, the
recycle bin's included; ``permanent`` off answers a permanent delete
``notSupported``. ``calls`` lists every request as ``"METHOD path"``.
``files()`` is ``{path: bytes}`` of what the OneDrive holds outside the
recycle bin, ``recycled`` what went there. Served (``cloud_fake.serve``)
it answers the same for the browser e2e, with ``POST /_fake/approve``
(``deny``, ``expire``) and ``GET /_fake/state`` (``{files: {path: size}}``):
``python -I backend/tests/onedrive_fake.py --port 0`` prints ``MICROSOFT
<base>`` and serves until killed.

What it checks as Microsoft does: the client is public (a secret sent is
refused, AADSTS700025), a Bearer token on every Graph call but none on an
upload session's chunks or a download's pre-signed address (another host
in-process); the sign-in page's redirect URI is ``http://localhost`` with
the registered path, any port; the code is traded once with its PKCE
verifier; every renewal answers a new refresh token (the old one still
works); a folder is made only in a folder that is there, and so is a file;
an upload session's chunks come in order, each but the last a multiple of
320 KiB, a range it holds already is a 416. Not checked against Microsoft
itself.
"""

import argparse
import base64
import hashlib
import itertools
import json
import os
import re
import sys
import time
import urllib.parse
from threading import Lock

if __name__ == "__main__":  # run on its own (the browser e2e, ``python -I``): its neighbours are not on the path
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cloud_fake  # noqa: E402
from cloud_fake import Dropped, Reply  # noqa: E402

CLIENT_ID = "11111111-2222-4333-8444-555555555555"
CALLBACK_PATH = "/api/admin/clouds/onedrive/callback"   # the redirect URI registered, as gamma sends it
DEVICE_GRANT = "urn:ietf:params:oauth:grant-type:device_code"
DRIVE_ID = "d0c0ffee"
PIECE = 320 * 1024


class FakeMicrosoft:
    def __init__(self, email="someone@outlook.com", base="https://microsoft.fake",
                 files_base="https://files.microsoft.fake", interval=5):
        self.email = email
        self.base = base
        self.files_base = files_base   # the pre-signed addresses: another host
        self._lock = Lock()
        self.items = {"root": {"id": "root", "name": "root", "parent": None, "folder": True, "data": b"",
                               "modified": 0, "recycled": False}}
        self.devices = {}        # device_code → {user_code, state}
        self.refresh = {}        # refresh token → valid
        self.codes = {}          # authorization code → {redirect_uri, challenge, used}
        self.access = set()
        self.sessions = {}       # upload id → {parent, name, data}
        self.calls, self.sleeps, self.failures = [], [], []
        self.quota = 0           # 0: unlimited (Graph then answers a large total)
        self.permanent = True
        self.granted = "Files.ReadWrite User.Read profile openid email"
        self.interval = interval
        self.rotations = 0
        self._ids = itertools.count(1)
        self._clock = time.mktime((2026, 11, 22, 10, 0, 0, 0, 0, 0))

    @property
    def bases(self):
        return [self.base, self.files_base]

    # --- the test's levers -------------------------------------------------

    def install(self, monkeypatch):
        monkeypatch.setenv("GAMMA_MICROSOFT_LOGIN_BASE", self.base)
        monkeypatch.setenv("GAMMA_MICROSOFT_GRAPH_BASE", self.base)
        return cloud_fake.install(monkeypatch, self)

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
        reply = self.handle("GET", url, {})
        assert reply.status == 302, reply
        return reply.headers["location"]

    def revoke_all(self):
        self.refresh = {t: False for t in self.refresh}
        self.access.clear()

    def expire_access(self):
        self.access.clear()

    def fail_next(self, match, status, times=1, retry_after=None):
        self.failures.append([match, status, times, retry_after])

    def path(self, item_id):
        parts, at = [], self.items[item_id]
        while at["parent"] is not None:
            parts.append(at["name"])
            at = self.items[at["parent"]]
        return "/".join(reversed(parts))

    def files(self):
        return {self.path(i["id"]): i["data"] for i in self.items.values()
                if not i["folder"] and not self._recycled(i)}

    @property
    def recycled(self):
        return sorted(self.path(i["id"]) for i in self.items.values() if not i["folder"] and i["recycled"])

    def add(self, path, data=b"", folder=False):
        """A file or folder the person put there."""
        parent = "root"
        names = path.split("/")
        for name in names[:-1]:
            child = self._child(parent, name)
            parent = child["id"] if child else self._new(parent, name, folder=True)["id"]
        return self._new(parent, names[-1], folder=folder, data=data)

    # --- the transport ------------------------------------------------------

    def handle(self, method, url, headers, body=None):
        with self._lock:
            return self._handle(method, url, {k.lower(): v for k, v in headers.items()}, body or b"")

    def _handle(self, method, url, headers, body):
        parts = urllib.parse.urlsplit(url)
        query = dict(urllib.parse.parse_qsl(parts.query, keep_blank_values=True))
        path = parts.path
        if path.startswith("/_fake/"):
            return self._control(method, path)
        line = f"{method} {path}"
        self.calls.append(line)
        for failure in self.failures:
            if failure[0] in line and failure[2] > 0:
                failure[2] -= 1
                if failure[1] == 0:
                    raise Dropped()
                extra = {"retry-after": str(failure[3])} if failure[3] is not None else {}
                return self._error(failure[1], "serviceNotAvailable" if failure[1] >= 500 else "activityLimitReached",
                                   "fake", extra)
        if path.startswith("/common/oauth2/v2.0/"):
            if path.endswith("/authorize"):
                return self._page(query)
            return self._oauth(path.rsplit("/", 1)[1], dict(urllib.parse.parse_qsl(body.decode())))
        if path.startswith(("/upload/", "/download/")):  # a pre-signed address
            if "authorization" in headers and self.files_base != self.base:
                return self._error(401, "unauthenticated", "a pre-signed address takes no token")
            if path.startswith("/upload/"):
                return self._chunk(method, path.rsplit("/", 1)[1], headers, body)
            item = self.items.get(path.rsplit("/", 1)[1])
            return Reply(200, {"content-type": "application/octet-stream"}, item["data"]) if item else self._error(
                404, "itemNotFound", "gone")
        auth = headers.get("authorization", "")
        if not auth.startswith("Bearer ") or auth[7:] not in self.access:
            return self._error(401, "InvalidAuthenticationToken", "Access token has expired or is not yet valid.")
        return self._graph(method, path, query, headers, body)

    def _control(self, method, path):
        if method == "POST" and path in ("/_fake/approve", "/_fake/deny", "/_fake/expire"):
            self._device({"approve": "approved", "deny": "denied", "expire": "expired"}[path[7:]])
            return self._json(200, {})
        if method == "GET" and path == "/_fake/state":
            return self._json(200, {"files": {p: len(d) for p, d in self.files().items()}})
        return self._json(404, {})

    @staticmethod
    def _json(status, data, headers=None):
        return Reply(status, {"content-type": "application/json", **(headers or {})}, json.dumps(data).encode())

    def _error(self, status, code, message, headers=None):
        return self._json(status, {"error": {"code": code, "message": message}}, headers)

    def _oauth_error(self, status, error, codes=()):
        return self._json(status, {"error": error, "error_description": f"AADSTS: {error}", "error_codes": list(codes)})

    # --- the identity platform -----------------------------------------------------

    def _oauth(self, endpoint, form):
        if form.get("client_id") != CLIENT_ID:
            return self._oauth_error(400, "unauthorized_client", [700016])
        if "client_secret" in form:  # a public client presents none
            return self._oauth_error(401, "invalid_client", [700025])
        if endpoint == "devicecode":
            code = f"dev-{next(self._ids)}"
            self.devices[code] = {"user_code": f"MS{len(self.devices) + 1:06d}", "state": "pending"}
            return self._json(200, {"device_code": code, "user_code": self.devices[code]["user_code"],
                                    "verification_uri": "https://microsoft.com/devicelogin", "expires_in": 900,
                                    "interval": self.interval, "message": "To sign in, use a web browser…"})
        assert endpoint == "token", endpoint
        grant = form.get("grant_type")
        if grant == DEVICE_GRANT:
            device = self.devices.get(form.get("device_code", ""))
            if device is None:
                return self._oauth_error(400, "bad_verification_code", [70000])
            if device["state"] in ("expired", "used"):
                return self._oauth_error(400, "expired_token", [70019])
            if device["state"] == "pending":
                return self._oauth_error(400, "authorization_pending", [70016])
            if device["state"] == "denied":
                return self._oauth_error(400, "authorization_declined", [70002])
            device["state"] = "used"
            return self._json(200, self._mint())
        if grant == "authorization_code":
            held = self.codes.get(form.get("code", ""))
            verifier = form.get("code_verifier", "").encode("ascii")
            challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier).digest()).rstrip(b"=").decode("ascii")
            if held is None or held["used"] or held["redirect_uri"] != form.get("redirect_uri") \
                    or held["challenge"] != challenge:
                return self._oauth_error(400, "invalid_grant", [70000])
            held["used"] = True
            return self._json(200, self._mint())
        if grant == "refresh_token":
            if not self.refresh.get(form.get("refresh_token", "")):
                return self._oauth_error(400, "invalid_grant", [70008])
            self.rotations += 1
            return self._json(200, self._mint())  # a new refresh token every time; the old one stays good
        return self._oauth_error(400, "unsupported_grant_type")

    def _mint(self):
        refresh, access = f"mrt-{next(self._ids)}", f"mat-{next(self._ids)}"
        self.refresh[refresh] = True
        self.access.add(access)
        return {"token_type": "Bearer", "scope": self.granted, "expires_in": 3599, "access_token": access,
                "refresh_token": refresh}

    def _page(self, query):
        back = query.get("redirect_uri", "")
        where = urllib.parse.urlsplit(back)
        if (query.get("client_id") != CLIENT_ID or query.get("response_type") != "code"
                or query.get("code_challenge_method") != "S256" or where.scheme != "http"
                or where.hostname != "localhost" or where.path != CALLBACK_PATH):
            return Reply(400, {"content-type": "text/plain"}, b"AADSTS50011: the redirect URI does not match")
        code = f"mac-{next(self._ids)}"
        self.codes[code] = {"redirect_uri": back, "challenge": query.get("code_challenge", ""), "used": False}
        return Reply(302, {"location": f"{back}?{urllib.parse.urlencode({'code': code, 'state': query.get('state', '')})}"})

    # --- Graph -------------------------------------------------------------------

    def _recycled(self, item):
        while item:
            if item["recycled"]:
                return True
            item = self.items.get(item["parent"]) if item["parent"] else None
        return False

    def _child(self, parent, name):
        for item in self.items.values():
            if item["parent"] == parent and item["name"].lower() == name.lower() and not item["recycled"]:
                return item
        return None

    def _new(self, parent, name, folder=False, data=b""):
        self._clock += 1
        item = {"id": f"i{next(self._ids)}", "name": name, "parent": parent, "folder": folder, "data": data,
                "modified": self._clock, "recycled": False}
        self.items[item["id"]] = item
        return item

    def _resolve(self, path):
        at = self.items["root"]
        for name in filter(None, path.split("/")):
            at = self._child(at["id"], name) if at["folder"] else None
            if at is None:
                return None
        return at

    def _view(self, item):
        out = {"id": item["id"], "name": item["name"], "parentReference": {"driveId": DRIVE_ID},
               "lastModifiedDateTime": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(item["modified"]))}
        if item["folder"]:
            out["folder"] = {"childCount": sum(1 for i in self.items.values() if i["parent"] == item["id"])}
        else:
            out.update(size=len(item["data"]), file={"mimeType": "application/octet-stream"})
        return out

    def _used(self):
        return sum(len(i["data"]) for i in self.items.values())

    def _full(self, more):
        return self.quota and self._used() + more > self.quota

    def _graph(self, method, path, query, headers, body):
        if path == "/v1.0/me":
            return self._json(200, {"displayName": "Some One", "mail": None, "userPrincipalName": self.email})
        if path == "/v1.0/me/drive":
            return self._json(200, {"owner": {"user": {"displayName": "Some One"}},
                                    "quota": {"total": self.quota or 5 * 10 ** 12, "used": self._used()}})
        if m := re.fullmatch(r"/v1\.0/drives/([^/]+)/items/([^/]+)/permanentDelete", path):
            if not self.permanent:
                return self._error(400, "notSupported", "Operation not supported")
            item = self.items.get(m.group(2))
            if item is None:
                return self._error(404, "itemNotFound", "gone")
            del self.items[item["id"]]
            return Reply(204)
        if m := re.fullmatch(r"/v1\.0/me/drive/items/([^/]+)(/children)?", path):
            item = self.items.get(m.group(1))
            if item is None:
                return self._error(404, "itemNotFound", "gone")
            if m.group(2):
                return self._children(item, query)
            assert method == "DELETE", method
            item["recycled"] = True
            return Reply(204)
        rel, action = self._address(method, path)
        item = self._resolve(rel)
        if action == "children" and method == "POST":
            if item is None or not item["folder"]:
                return self._error(404, "itemNotFound", "The parent is not there")
            meta = json.loads(body)
            assert "folder" in meta and meta.get("@microsoft.graph.conflictBehavior") == "fail", meta
            if self._child(item["id"], meta["name"]):
                return self._error(409, "nameAlreadyExists", "An item with the same name already exists")
            return self._json(201, self._view(self._new(item["id"], meta["name"], folder=True)))
        if action == "children":
            return self._error(404, "itemNotFound", "gone") if item is None or not item["folder"] else \
                self._children(item, query)
        if action in ("content", "createUploadSession") and method in ("PUT", "POST"):
            parent_path, _, name = rel.rpartition("/")
            parent = self._resolve(parent_path)
            if parent is None or not parent["folder"]:
                return self._error(404, "itemNotFound", "The parent is not there")
            if action == "createUploadSession":
                meta = json.loads(body or b"{}").get("item") or {}
                assert meta.get("@microsoft.graph.conflictBehavior") == "replace", meta
                upload = f"s{next(self._ids)}"
                self.sessions[upload] = {"parent": parent["id"], "name": name, "data": b""}
                return self._json(200, {"uploadUrl": f"{self.files_base}/upload/{upload}",
                                        "expirationDateTime": "2026-11-29T10:00:00Z"})
            assert query.get("@microsoft.graph.conflictBehavior") == "replace", query
            assert len(body) <= 4 * 1024 * 1024, "a large file goes in an upload session"
            return self._store(parent["id"], name, body)
        if action == "content":
            if item is None or item["folder"]:
                return self._error(404, "itemNotFound", "gone")
            return Reply(302, {"location": f"{self.files_base}/download/{item['id']}"})
        assert action is None and method == "GET", f"{method} {path}"
        return self._error(404, "itemNotFound", "gone") if item is None else self._json(200, self._view(item))

    @staticmethod
    def _address(method, path):
        """``(path from the top, action)`` of a Graph address by path:
        ``/me/drive/root``, ``…/root/children``, ``…/root:/a/b`` and
        ``…/root:/a/b:/<action>`` (a name's ``:`` comes encoded)."""
        rest = path[len("/v1.0/me/drive/root"):] if path.startswith("/v1.0/me/drive/root") else None
        if rest in ("", "/children"):
            return "", rest[1:] or None
        assert rest is not None and rest.startswith(":/"), f"a call the fake does not know: {method} {path}"
        inner, action = rest[2:], None
        if m := re.fullmatch(r"(.*?):/(children|content|createUploadSession)", inner):
            inner, action = m.group(1), m.group(2)
        return urllib.parse.unquote(inner.rstrip(":")), action

    def _children(self, item, query):
        found = sorted((i for i in self.items.values() if i["parent"] == item["id"] and not i["recycled"]),
                       key=lambda i: i["modified"])
        top, skip = int(query.get("$top", 200)), int(query.get("$skiptoken", 0))
        page = {"value": [self._view(i) for i in found[skip:skip + top]]}
        if skip + top < len(found):
            page["@odata.nextLink"] = (f"{self.base}/v1.0/me/drive/items/{item['id']}/children?"
                                       f"$top={top}&$skiptoken={skip + top}")
        return self._json(200, page)

    def _store(self, parent, name, data):
        existing = self._child(parent, name)
        if self._full(len(data) - (len(existing["data"]) if existing else 0)):
            return self._error(507, "quotaLimitReached", "Insufficient Storage")
        self._clock += 1
        if existing:
            existing["data"], existing["modified"] = data, self._clock
            return self._json(200, self._view(existing))
        return self._json(201, self._view(self._new(parent, name, data=data)))

    def _chunk(self, method, upload, headers, body):
        session = self.sessions.get(upload)
        if session is None:
            return self._error(404, "itemNotFound", "The upload session is gone")
        held = len(session["data"])
        if method == "GET":
            return self._json(200, {"nextExpectedRanges": [f"{held}-"], "expirationDateTime": "2026-11-29T10:00:00Z"})
        assert method == "PUT", method
        m = re.fullmatch(r"bytes (\d+)-(\d+)/(\d+)", headers["content-range"])
        start, end, size = int(m.group(1)), int(m.group(2)), int(m.group(3))
        assert end - start + 1 == len(body) == int(headers["content-length"])
        if start < held:
            return self._error(416, "invalidRange", "The range is there already")
        assert start == held, "a chunk must start where the session's next expected range does"
        if end + 1 < size:
            assert len(body) % PIECE == 0, "each chunk but the last is a multiple of 320 KiB"
        session["data"] += body
        if len(session["data"]) < size:
            return self._json(202, {"nextExpectedRanges": [f"{len(session['data'])}-"],
                                    "expirationDateTime": "2026-11-29T10:00:00Z"})
        del self.sessions[upload]
        return self._store(session["parent"], session["name"], session["data"])


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description="Serve a fake Microsoft: the device flow and OneDrive's Graph API.")
    parser.add_argument("--port", type=int, default=0, help="port on 127.0.0.1 (0 = any free one)")
    parser.add_argument("--interval", type=int, default=2, help="the polling interval its device codes ask for")
    args = parser.parse_args(argv)
    fake = FakeMicrosoft(interval=args.interval)
    httpd, base = cloud_fake.serve(fake, args.port)
    fake.files_base = base
    print(f"MICROSOFT {base}", flush=True)
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        httpd.shutdown()


if __name__ == "__main__":
    main()
