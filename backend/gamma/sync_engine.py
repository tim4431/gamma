"""Mirrors: a local workspace kept in step with a workspace on another Gamma
server (docs/dev/mirror.md).

The remote is the authority. One round (``sync_workspace``) asks both
change feeds (``/api/sync/changes``, local and remote) which pages moved,
then reconciles each page three ways from the tree it held at the last
round (``sync_pages.base``):

- remote-only change: the remote's diff is applied locally;
- local-only change: the local diff is pushed, with ``base`` texts so the
  remote merges against anything that landed there meanwhile;
- both: the remote diff is applied locally first (the local server's own
  three-way text merge keeps the local keystrokes), then this copy's own
  edits are pushed, the remote tree is fetched back and becomes the new base.

Only edits made here are ever pushed (``_split``): what differs here only
through the engine's own writes (a key re-keyed on arrival, a block moved
over from another page) is put back as the remote has it (``_strays``), so
a copy that changed nothing writes nothing to its origin.

The base is saved as each of those steps lands (the bare page once it
exists on both sides, the remote's tree once its changes are applied here,
a push's batches under their ids before it goes and what was pushed once
it lands), so a round cut short anywhere goes on from there and never
applies a change twice: a push whose answer was lost is confirmed under
its ids first thing the next round (``_confirm_push``).

An edit beats a delete, in both directions: a subtree the remote deleted
stays when it was edited here (and is re-inserted there by the push), and
a subtree deleted here comes back when the remote edited inside it. Every
such decision and every text merge that changed a block is a row of
``sync_conflicts`` for the person to look at; sync never blocks on one.
Pages deleted on one side and untouched on the other are deleted on the
other; deleted-and-edited pages come back.

Files travel by content hash: uploads a page references are fetched when
missing here and uploaded when missing there, so a re-run never duplicates.

A mirror may carry a page filter (``page_filter``, a list of page ids; NULL
= every page): a round then looks only at those pages, in both feeds, moves
only their files, and a page outside it never travels either way. A listed
page with no saved base is "new" whatever the feeds say, which is how a page
added to the filter goes over at the next round. Publishing a page to the
share host is such a filtered mirror (gamma/publish.py).

Writes on the remote carry the mirror's write-scope integration token
(``Authorization: Bearer``), so they land under the account that made the
mirror. Local writes go through ``commit_ops`` with client ``"sync"``, which
is how the local change feed's re-listing of them costs nothing (they diff
to no-op) and how the page's live viewers see them arrive.
"""

import http.client
import io
import json
import mimetypes
import re
import secrets
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

from . import config, ops, pdf_meta, textmerge, workspaces
from fractional_indexing import FIError, generate_key_between, validate_order_key

from .blocks_store import create_page, fetch_subtree, last_child_position, page_root_id
from .db import connect_pages_db, connect_users_db, page_now, ws_uploads_dir
from .logbuf import log
from .ops import MAX_OPS, OpError, commit_ops, latest_seq, props_patch, trash_page
from .publisher_sessions import cipher
from .routers.sync import changes as local_changes
from .storage import matches_name, write_atomic
from .sync_tree import (ancestors, apply, children_of, diff, moved, snapshot_from_rows, snapshot_from_tree,
                        subtree_ids, tree_order, upload_refs)

SYNC_LOG_KEEP = 500        # rows of sync_log kept per mirror
CLIENT = "sync"            # the op-log client of every local write the engine makes
ACTOR = "mirror"           # ...and its actor (the remote's per-op authors are not carried over)
MODES = ("two-way", "pull", "off")   # off = detached: the link (token, cursors, bases) is kept, no round runs
ADOPT = ("theirs", "mine")           # whose version a never-reconciled page takes (a linked workspace, a force)
DEBOUNCE_S = 1.0                     # a local edit → a round once things have been quiet this long (the loop wakes for it)
TICK_S = 1                           # the loop's clock
WHOAMI_TTL_S = 900                   # how long a round trusts the remote's last whoami (a failed round asks again)
UNKNOWN_SEQ = -1                     # a base saved before the remote's answer was read: the next round fetches the page
STREAM_CHUNK = 256 * 1024
default_fetch = None       # the tests point this at an in-process TestClient; None = urllib
UPLOAD_NAME_RE = re.compile(r"^[0-9a-f]{8,64}\.[a-z0-9]{1,8}$")
_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()


class RemoteError(Exception):
    def __init__(self, status: int, detail: str = ""):
        super().__init__(f"{status}: {detail}" if detail else str(status))
        self.status, self.detail = status, detail


class PageDeferred(Exception):
    """A page the round leaves for the next one without calling it an
    error: its push was refused because a block it holds lives in another
    page on the remote (a cross-page move there, which the other page's
    round of this same pass settles), or the page it was about to create
    there appeared meanwhile (the next round finds it on both sides)."""


class Remote:
    """A thin HTTP client for one remote workspace. ``fetch(method, path,
    body, headers) -> (status, bytes)`` is the transport — urllib in
    production, a TestClient wrapper in the tests."""

    def __init__(self, url: str, ws: str, token: str, fetch=None):
        self.url = url.rstrip("/")
        self.ws = ws
        self.token = token
        self.fetch = fetch or default_fetch or self._urllib_fetch

    def _urllib_fetch(self, method, path, body, headers):
        req = urllib.request.Request(self.url + path, data=body, method=method, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                return resp.status, resp.read()
        except urllib.error.HTTPError as e:
            return e.code, e.read()
        except (urllib.error.URLError, OSError, ValueError, http.client.HTTPException) as e:
            # (IncompleteRead, the answer cut short, is an HTTPException)
            raise RemoteError(0, f"cannot reach {self.url}: {e}") from e

    def request(self, method, path, *, body=None, content_type=None, ok=(200,)):
        status, data = self.fetch(method, path, body, self._headers(content_type))
        if status not in ok:
            try:
                detail = json.loads(data.decode("utf-8")).get("detail", "")
            except Exception:
                detail = (data or b"")[:200].decode("utf-8", "replace")
            raise RemoteError(status, str(detail))
        return status, data

    def get(self, path):
        _, data = self.request("GET", path)
        return json.loads(data) if data else None

    def post(self, path, payload, ok=(200, 201)):
        _, data = self.request("POST", path, body=json.dumps(payload).encode("utf-8"),
                               content_type="application/json", ok=ok)
        return json.loads(data) if data else None

    def delete(self, path, ok=(200, 404)):
        self.request("DELETE", path, ok=ok)

    def head_ok(self, path) -> bool:
        status, _ = self.request("HEAD", path, ok=(200, 404))
        return status == 200

    def _headers(self, content_type=None):
        from . import cloud_auth  # local: cloud_auth imports this module

        # the remote may sit behind Cloudflare, which blocks a bare Python-urllib signature
        headers = {"Authorization": f"Bearer {self.token}", "Accept": "application/json",
                   "User-Agent": cloud_auth.user_agent()}
        if self.ws:
            headers["X-Gamma-Workspace"] = self.ws
        if content_type:
            headers["Content-Type"] = content_type
        return headers

    def _streaming(self) -> bool:
        return self.fetch == self._urllib_fetch

    def _open(self, req, timeout: int):
        """``urlopen`` for the streaming paths, every failure a RemoteError
        (``_urllib_fetch`` keeps an HTTP error's status and body instead, for
        ``request`` to read the detail from)."""
        try:
            return urllib.request.urlopen(req, timeout=timeout)
        except urllib.error.HTTPError as e:
            raise RemoteError(e.code, (e.read() or b"")[:200].decode("utf-8", "replace")) from e
        except (urllib.error.URLError, OSError, ValueError, http.client.HTTPException) as e:
            raise RemoteError(0, f"cannot reach {self.url}: {e}") from e

    def get_bytes(self, path, progress=None) -> bytes:
        """A file's bytes; ``progress(done, total)`` as they arrive (total 0
        when the remote sends no length). Only the real transport streams —
        the tests' in-process one reports once, at the end. A body that ends
        before the length the remote announced (the link dropped mid-file:
        ``read`` just stops) is a RemoteError, never bytes to store."""
        if not self._streaming():
            _, data = self.request("GET", path)
            if progress:
                progress(len(data), len(data))
            return data
        req = urllib.request.Request(self.url + path, headers=self._headers())
        with self._open(req, 60) as resp:
            total = int(resp.headers.get("Content-Length") or 0)
            chunks, done = [], 0
            while True:
                try:
                    chunk = resp.read(STREAM_CHUNK)
                except (OSError, ValueError, http.client.HTTPException) as e:
                    raise RemoteError(0, f"{path}: the transfer stopped at {done} bytes: {e}") from e
                if not chunk:
                    break
                chunks.append(chunk)
                done += len(chunk)
                if progress:
                    progress(done, total)
        if total and done != total:
            raise RemoteError(0, f"{path}: the transfer stopped at {done} of {total} bytes")
        return b"".join(chunks)

    def post_file(self, path, name: str, data: bytes, progress=None):
        boundary = "gammaMirror" + str(int(time.time() * 1000))
        ctype = mimetypes.guess_type(name)[0] or "application/octet-stream"
        body = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"{name}\"\r\n"
                f"Content-Type: {ctype}\r\n\r\n").encode() + data + f"\r\n--{boundary}--\r\n".encode()
        content_type = f"multipart/form-data; boundary={boundary}"
        if not self._streaming():
            _, out = self.request("POST", path, body=body, content_type=content_type)
            if progress:
                progress(len(data), len(data))
            return json.loads(out) if out else None
        total = len(body)

        class Reader(io.BytesIO):
            def read(self, n=-1):
                chunk = super().read(n)
                if progress:
                    progress(min(self.tell(), len(data)), len(data))
                return chunk

        headers = self._headers(content_type)
        headers["Content-Length"] = str(total)
        req = urllib.request.Request(self.url + path, data=Reader(body), method="POST", headers=headers)
        with self._open(req, 600) as resp:
            out = resp.read()
        return json.loads(out) if out else None


# --- the registry -----------------------------------------------------------------

_COLS = ("workspace_id, remote_url, remote_ws, remote_name, token, owner, mode, remote_cursor, local_cursor, "
         "status, created_at, poll_s, on_change, page_filter")


def _parse_filter(raw) -> list[str] | None:
    """A stored ``page_filter``: None (every page travels) or the page ids."""
    if raw is None:
        return None
    try:
        ids = json.loads(raw)
    except ValueError:
        return None
    return [i for i in ids if isinstance(i, str)] if isinstance(ids, list) else None


def _row_info(row, *, with_token=False) -> dict:
    info = {"workspace_id": row[0], "remote_url": row[1], "remote_ws": row[2], "remote_name": row[3],
            "owner": row[5], "mode": row[6], "remote_cursor": row[7], "local_cursor": row[8],
            "status": json.loads(row[9] or "{}"), "created_at": row[10],
            "poll_s": int(row[11] if row[11] is not None else 30), "on_change": bool(row[12] if row[12] is not None else 1),
            "page_filter": _parse_filter(row[13])}
    if with_token:
        info["token"] = cipher().decrypt(row[4].encode("ascii")).decode("utf-8")
    return info


def get_mirror(ws: str, *, with_token: bool = False) -> dict | None:
    with connect_users_db() as conn:
        row = conn.execute(f"SELECT {_COLS} FROM mirrors WHERE workspace_id = ?", (ws,)).fetchone()
    return _row_info(row, with_token=with_token) if row else None


def list_mirrors(owner: str) -> list[dict]:
    with connect_users_db() as conn:
        rows = conn.execute(f"SELECT {_COLS} FROM mirrors WHERE owner = ? ORDER BY created_at", (owner,)).fetchall()
    return [_row_info(r) for r in rows]


def _seal(token: str) -> str:
    """The token as stored: Fernet-encrypted with the data directory's key."""
    return cipher().encrypt(token.encode("utf-8")).decode("ascii")


def _clear_bases(ws: str) -> None:
    """Forget every page's base tree: the next round has nothing to merge
    from and adopts one side's version (a link, a re-link elsewhere, a force)."""
    with connect_pages_db(ws) as conn:
        conn.execute("DELETE FROM sync_pages")
        conn.commit()


def _save(ws: str, **fields) -> None:
    if "status" in fields and not isinstance(fields["status"], str):
        fields["status"] = json.dumps(fields["status"])
    if "page_filter" in fields:
        _filters.pop(ws, None)
    sets = ", ".join(f"{k} = ?" for k in fields)
    with connect_users_db() as conn:
        conn.execute(f"UPDATE mirrors SET {sets} WHERE workspace_id = ?", (*fields.values(), ws))
        conn.commit()


_status_guard = threading.Lock()  # every read-modify-write of a mirror's status JSON (and its cursors)


def _patch_status(ws: str, patch, *, drop=()) -> dict:
    """Change some keys of the mirror's stored status and leave the rest as
    they are now — one read-modify-write under a lock, so a round and the
    actions taken while it runs (detach, a force, the filter, a direction
    change) never overwrite each other's keys. ``patch`` is a dict laid over
    the status, or a function of the current status returning the new one;
    ``drop`` names keys to remove first. Returns the status as saved."""
    with _status_guard:
        mirror = get_mirror(ws)
        status = {k: v for k, v in (mirror["status"] if mirror else {}).items() if k not in drop}
        status = patch(status) if callable(patch) else {**status, **patch}
        _save(ws, status=status)
    return status


def _stored_mode(ws: str) -> str:
    with connect_users_db() as conn:
        row = conn.execute("SELECT mode FROM mirrors WHERE workspace_id = ?", (ws,)).fetchone()
    return row[0] if row else "off"


def whoami(remote: Remote) -> dict:
    """The remote's view of the token: ``{user, workspace: {id, name}, role,
    scope}`` (``GET /api/sync/whoami``)."""
    return remote.get("/api/sync/whoami")


_whoami_seen: dict[str, tuple[tuple, float, dict]] = {}  # ws -> ((url, remote ws, token), when, answer)


def _round_whoami(ws: str, mirror: dict, remote: Remote) -> dict:
    """``whoami`` for a round: the answer an earlier round got for the same
    link while it is younger than ``WHOAMI_TTL_S``, else a fresh one. A
    round that ends with any error forgets it (``_round``), so a revoked
    token or a lowered role is seen by the next round."""
    key = (mirror["remote_url"], mirror["remote_ws"], mirror["token"])
    seen = _whoami_seen.get(ws)
    if seen and seen[0] == key and time.monotonic() - seen[1] < WHOAMI_TTL_S:
        return seen[2]
    me = whoami(remote)
    _whoami_seen[ws] = (key, time.monotonic(), me)
    return me


def _check_remote(remote_url: str, token: str, mode: str, fetch) -> tuple[str, dict, str]:
    """Validate the address and the token against the remote's ``whoami``:
    ``(remote_url, me, mode)`` — the mode dropped to ``pull`` when the token
    or the role may not write."""
    remote_url = (remote_url or "").strip().rstrip("/")
    if not re.match(r"^https?://[^/\s]+(/[^\s]*)?$", remote_url):
        raise ValueError("the server address must be an http(s) URL")
    if mode not in ("two-way", "pull"):
        raise ValueError("mode must be two-way or pull")
    token = (token or "").strip()
    if not token.startswith("gamma_"):
        raise ValueError("that is not a Gamma integration token")
    try:
        me = whoami(Remote(remote_url, "", token, fetch))
    except RemoteError as e:
        if e.status in (401, 403):
            raise ValueError("the remote did not accept the token") from e
        raise
    if not me or not me.get("workspace"):
        raise ValueError("the remote did not recognise the token")
    if mode == "two-way" and (me.get("scope") != "write" or me.get("role") == "viewer"):
        mode = "pull"
    return remote_url, me, mode


def create_mirror(owner: str, remote_url: str, token: str, *, name: str = "", mode: str = "two-way",
                  fetch=None, workspace_id: str = "", adopt: str = "theirs",
                  page_filter: list[str] | None = None) -> dict:
    """Make a local workspace of ``owner``'s that mirrors the remote
    workspace the token belongs to — or link ``workspace_id``, an existing
    personal workspace of the owner's (an imported backup, a copy detached
    and forgotten): its pages that exist on both sides have no common base,
    so the first round adopts ``adopt``'s version of each and records what
    differed as ``diverged`` conflicts. Talks to the remote first
    (``whoami``), so a bad URL or token fails before anything is created.
    ``page_filter`` limits the mirror to those pages (several filtered
    mirrors of one remote workspace may coexist: each moves only its own).
    Returns the mirror's info (run ``sync_workspace`` for the first fill)."""
    if adopt not in ADOPT:
        raise ValueError("adopt must be theirs or mine")
    if page_filter is not None and (not isinstance(page_filter, list)
                                    or not all(isinstance(p, str) and p for p in page_filter)):
        raise ValueError("page_filter must be a list of page ids")
    remote_url, me, mode = _check_remote(remote_url, token, mode, fetch)
    token = token.strip()
    remote_ws, remote_name = me["workspace"]["id"], me["workspace"].get("name") or "Workspace"
    with connect_users_db() as conn:
        if page_filter is None and conn.execute(
                "SELECT 1 FROM mirrors WHERE owner = ? AND remote_url = ? AND remote_ws = ? AND mode != 'off' "
                "AND page_filter IS NULL", (owner, remote_url, remote_ws)).fetchone():
            raise ValueError("you already mirror that workspace")
    status = {"remote_user": me.get("user"), "remote_role": me.get("role")}
    if workspace_id:
        info = workspaces.get(workspace_id)
        if not info or info["kind"] != "personal" or workspaces.role_of(workspace_id, owner) != "owner":
            raise ValueError("that is not a workspace of yours")
        if get_mirror(workspace_id):
            raise ValueError("that workspace already mirrors something — detach or forget it first")
        status["adopt"] = adopt
        _clear_bases(workspace_id)
    else:
        info = workspaces.create(name or f"{remote_name} (offline copy)", owner)
    with connect_users_db() as conn:
        conn.execute(
            "INSERT INTO mirrors (workspace_id, remote_url, remote_ws, remote_name, token, owner, mode, "
            "remote_cursor, local_cursor, status, created_at, page_filter) VALUES (?, ?, ?, ?, ?, ?, ?, '', '', ?, ?, ?)",
            (info["id"], remote_url, remote_ws, remote_name, _seal(token), owner, mode, json.dumps(status), page_now(),
             None if page_filter is None else json.dumps(list(dict.fromkeys(page_filter)))))
        conn.commit()
    _filters.pop(info["id"], None)
    return get_mirror(info["id"])


def replace_token(ws: str, token: str) -> None:
    """Store a new token for the same remote workspace (the old one expired
    or was replaced there); the bases and cursors stay."""
    _save(ws, token=_seal(token.strip()))


def round_lock(ws: str) -> threading.Lock:
    """The lock a round of ``ws`` holds. Held around a change that must not
    interleave with a round (the page filter and what goes with it, such as
    unpublishing a page); not re-entrant: never call ``sync_workspace``
    while holding it."""
    return _lock(ws)


def filter_add(ws: str, page_id: str, *, adopt: str = "") -> dict:
    """Add a page to a filtered mirror's ``page_filter`` (a mirror without a
    filter already moves every page: unchanged). The next round finds it
    "new" and sends it over; ``adopt`` sets whose version a page both sides
    hold without a base takes. Hold ``round_lock``."""
    mirror = get_mirror(ws)
    if not mirror:
        raise ValueError("not a mirror")
    if adopt and adopt not in ADOPT:
        raise ValueError("adopt must be theirs or mine")
    if mirror["page_filter"] is not None and page_id not in mirror["page_filter"]:
        _save(ws, page_filter=json.dumps(mirror["page_filter"] + [page_id]))
    if adopt:
        _patch_status(ws, {"adopt": adopt})
    return get_mirror(ws)


def filter_remove(ws: str, page_ids) -> dict | None:
    """Drop pages from a filtered mirror's ``page_filter`` together with
    their saved bases and retry entries: they stop travelling, the pages
    stay where they are on both sides. An empty filter leaves the row (a
    round of it asks nothing of the remote). Hold ``round_lock``."""
    mirror = get_mirror(ws)
    if not mirror or mirror["page_filter"] is None:
        return mirror
    drop = set(page_ids)
    _save(ws, page_filter=json.dumps([p for p in mirror["page_filter"] if p not in drop]))
    _patch_status(ws, lambda s: {**s, "retry": {k: v for k, v in (s.get("retry") or {}).items() if k not in drop}})
    with connect_pages_db(ws) as conn:
        for page_id in drop:
            _drop_state(conn, page_id)
    return get_mirror(ws)


def set_cadence(ws: str, *, poll_s: int | None = None, on_change: bool | None = None,
                mode: str | None = None) -> dict:
    """How a copy keeps in step: ``poll_s`` (a round every so many seconds,
    0 = only Sync now), ``on_change`` (a round ``DEBOUNCE_S`` after a local
    edit), ``mode`` (two-way / pull)."""
    fields = {}
    if poll_s is not None:
        fields["poll_s"] = max(0, min(int(poll_s), 86400))
    if on_change is not None:
        fields["on_change"] = 1 if on_change else 0
    with _status_guard:  # the cursor reset must not race a round's cursor save
        if mode is not None:
            if mode not in ("two-way", "pull"):
                raise ValueError("mode must be two-way or pull")
            current = get_mirror(ws)
            if current and current["mode"] == "off":
                raise ValueError("the copy is detached — reattach it first")
            fields["mode"] = mode
            # a receive-only round leaves the local cursor where it is, so the
            # first two-way round pushes what it kept; copies from before that
            # rule moved it, so back in two-way the cursor starts over anyway
            if current and current["mode"] == "pull" and mode == "two-way":
                fields["local_cursor"] = ""
        if fields:
            _save(ws, **fields)
    return get_mirror(ws)


def detach_mirror(ws: str) -> dict:
    """Detach: no round runs, the workspace is an ordinary one again, but
    the link — token, cursors, every page's base — is kept, so a later
    ``relink_mirror`` continues with a proper three-way merge of what both
    sides did meanwhile."""
    mirror = get_mirror(ws)
    if not mirror:
        raise ValueError("not a mirror")
    if mirror["mode"] == "off":
        return mirror
    # a round in flight stops at its next page (``_round`` checks the stored mode) and
    # brings ``running`` down itself; a force asked for but not yet run is forgotten
    _save(ws, mode="off")
    _patch_status(ws, {"detached_at": page_now(), "detached_mode": mirror["mode"]}, drop=("force",))
    return get_mirror(ws)


def relink_mirror(ws: str, *, token: str = "", remote_url: str = "", adopt: str = "theirs", fetch=None) -> dict:
    """Link a detached copy again. Without a token the stored one is
    checked against the remote; a new token (or address) that points at the
    same remote workspace keeps the saved bases — anything else starts over
    with the ``adopt`` policy. Returns the mirror; the caller runs a round."""
    mirror = get_mirror(ws, with_token=True)
    if not mirror:
        raise ValueError("not a mirror")
    if adopt not in ADOPT:
        raise ValueError("adopt must be theirs or mine")
    wanted = mirror["status"].get("detached_mode") or "two-way"
    if wanted not in ("two-way", "pull"):
        wanted = "two-way"
    remote_url, me, mode = _check_remote(remote_url or mirror["remote_url"], token or mirror["token"], wanted, fetch)
    token = (token or mirror["token"]).strip()
    remote_ws, remote_name = me["workspace"]["id"], me["workspace"].get("name") or "Workspace"
    patch = {"remote_user": me.get("user"), "remote_role": me.get("role")}
    fields = {"mode": mode, "remote_url": remote_url, "remote_ws": remote_ws, "remote_name": remote_name,
              "token": _seal(token)}
    if remote_url != mirror["remote_url"] or remote_ws != mirror["remote_ws"]:
        # a different original: the saved bases mean nothing, its pages are adopted
        _clear_bases(ws)
        fields.update(remote_cursor="", local_cursor="")
        patch["adopt"] = adopt
    with _status_guard:
        _save(ws, **fields)
    _patch_status(ws, patch, drop=("detached_at", "detached_mode", "last_error"))
    return get_mirror(ws)


def force_sync(ws: str, direction: str) -> None:
    """Make one side identical to the other, whatever happened: ``pull``
    replaces this copy with the original (its own pages and edits go, the
    texts they had are kept in ``diverged`` conflicts), ``push`` replaces the
    original with this copy. Every page is reconciled from scratch under the
    adopt policy and pages the losing side alone has are deleted there. The
    round runs in the background: the force is noted on the status
    (``force``) and the next round, under the round lock, starts from it —
    clearing the bases and cursors while a round is in flight would leave
    that round's bookkeeping and the force's fighting over them."""
    if direction not in ("pull", "push"):
        raise ValueError("direction must be pull or push")
    mirror = get_mirror(ws)
    if not mirror:
        raise ValueError("not a mirror")
    if mirror["mode"] == "off":
        raise ValueError("the copy is detached — link it again first")
    if direction == "push" and mirror["mode"] != "two-way":
        raise ValueError("a read-only copy cannot replace the original")
    _patch_status(ws, {"force": direction})
    sync_in_background(ws)


def _start_force(ws: str, mirror: dict) -> dict:
    """The first thing a round does under its lock: a force asked for
    (``status.force``) becomes the round's policy — every base and both
    cursors cleared, ``adopt`` and ``prune`` set. Returns the mirror to run."""
    direction = mirror["status"].get("force")
    if direction not in ("pull", "push"):
        return mirror
    _clear_bases(ws)
    with _status_guard:
        _save(ws, remote_cursor="", local_cursor="")
    status = _patch_status(ws, {"adopt": "theirs" if direction == "pull" else "mine", "prune": True}, drop=("force",))
    return {**mirror, "remote_cursor": "", "local_cursor": "", "status": status}


def remove_mirror(ws: str) -> None:
    """Forget the link: the workspace stays as an ordinary local one; its
    sync state is dropped (a detached copy keeps it — see ``detach_mirror``)."""
    with connect_users_db() as conn:
        conn.execute("DELETE FROM mirrors WHERE workspace_id = ?", (ws,))
        conn.commit()
    _filters.pop(ws, None)
    with connect_pages_db(ws) as conn:
        conn.execute("DELETE FROM sync_pages")
        conn.execute("DELETE FROM sync_conflicts")
        conn.execute("DELETE FROM sync_log")
        conn.commit()


# --- conflicts -----------------------------------------------------------------------

def _conflict(conn, page_id: str, block_id: str, kind: str, mine="", theirs="", result="", base="", *,
              once: bool = False) -> None:
    """One row to look at. ``base`` is the text before either side edited it
    (a ``merged`` block), so the resolver can show what each side did; for a
    ``dropped`` one it names the block it was under. ``once``: not again
    while the same row is open (a replace cut short and run again)."""
    if once and conn.execute(
            "SELECT 1 FROM sync_conflicts WHERE resolved = 0 AND page_id = ? AND block_id = ? AND kind = ? "
            "AND mine = ? AND theirs = ?", (page_id, block_id, kind, mine or "", theirs or "")).fetchone():
        return
    conn.execute(
        "INSERT INTO sync_conflicts (page_id, block_id, kind, mine, theirs, result, base, at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (page_id, block_id, kind, mine or "", theirs or "", result or "", base or "", page_now()))
    conn.commit()


def _outline(tree: dict, top: str, only: set | None = None) -> str:
    """A block and what is under it as one text — its content, then its
    descendants as an indented markdown list — how a removed subtree is kept
    in its conflict row. ``only``: the ids that went (a child that lives on
    elsewhere is left out)."""
    kids = children_of(tree)
    lines = [tree[top]["content"]]
    stack = [(c, 0) for c in reversed(kids.get(top, []))]
    while stack:
        bid, depth = stack.pop()
        if only is not None and bid not in only:
            continue
        pad = "  " * depth
        lines.append(f"{pad}- " + tree[bid]["content"].replace("\n", f"\n{pad}  "))
        stack.extend((c, depth + 1) for c in reversed(kids.get(bid, [])))
    return "\n".join(lines)


def _dropped(conn, page_id: str, loser: dict, winner: dict, side: str) -> None:
    """``dropped`` rows for what only the losing side of a link or a force
    had (``side``: ``mine`` when this copy loses it, ``theirs`` when the
    remote does): one per top-most block that goes — the page itself when
    ``winner`` lacks it — with its subtree's text and, in ``base``, the
    block it was under. Written before anything is removed and only once,
    so a replace cut short and run again is on record exactly once."""
    gone = {bid for bid in loser if bid not in winner}
    why = ("only this copy had it; the other side's version was taken" if side == "mine"
           else "only the other side had it; this copy's version was taken")
    for bid in [page_id] + tree_order(loser, page_id):
        if bid in gone and loser[bid]["parent"] not in gone:
            _conflict(conn, page_id, bid, "dropped", result=why, base=loser[bid]["parent"], once=True,
                      **{side: _outline(loser, bid, gone)})


def _stats(ops: list[dict], before: dict | None = None) -> dict:
    """The git-style counts of a batch of ops on one page: ``add`` blocks
    inserted, ``del`` blocks removed (a delete takes its subtree — counted
    from ``before``, the tree the ops applied to), ``mod`` blocks whose text,
    props or place changed (a set or move on a block the batch did not
    insert)."""
    add = {op["id"] for op in ops if op["op"] == "insert"}
    removed = set()
    for op in ops:
        if op["op"] == "delete":
            if before is not None and op["id"] not in before:
                continue  # already gone here (deleted on both sides): nothing removed
            removed |= subtree_ids(before, op["id"]) if before else {op["id"]}
    mod = {op["id"] for op in ops if op["op"] in ("set", "move")} - add - removed
    return {"add": len(add), "del": len(removed), "mod": len(mod)}


def _whole(tree: dict, key: str) -> dict:
    """The counts of a page that came or went whole: its blocks under the
    root, all ``add`` or all ``del``."""
    return {"add": 0, "del": 0, "mod": 0, key: max(0, len(tree) - 1)}


CHANGES_CAP = 40   # block changes kept per log row
TEXT_CAP = 240     # characters of a block's text kept per change


def _clip(text) -> str:
    text = text or ""
    return text if len(text) <= TEXT_CAP else text[:TEXT_CAP] + "…"


def _changes(ops: list[dict], before: dict | None = None) -> list[dict]:
    """What a batch of ops did, block by block, for the log's diff view:
    ``{k: add | del | mod | props | move, id, text, old?}`` — the new text
    (``old`` the text before, for ``mod``), the removed text for ``del``
    (one entry per block of a deleted subtree), the block's text for a
    property or place change. Capped at ``CHANGES_CAP`` entries."""
    before = before or {}
    inserted = {op["id"] for op in ops if op["op"] == "insert"}
    out: list[dict] = []
    for op in ops:
        if len(out) >= CHANGES_CAP:
            break
        bid = op["id"]
        if op["op"] == "insert":
            out.append({"k": "add", "id": bid, "text": _clip(op.get("content", ""))})
        elif op["op"] == "delete":
            if bid not in before:
                continue  # already gone here: nothing to show
            ids = subtree_ids(before, bid)
            for d in sorted(ids, key=lambda i: (i != bid, i)):
                if len(out) >= CHANGES_CAP:
                    break
                out.append({"k": "del", "id": d, "text": _clip((before.get(d) or {}).get("content", ""))})
        elif op["op"] == "set" and bid not in inserted:
            old = (before.get(bid) or {}).get("content", "")
            if "content" in op:
                if op["content"] != old:
                    out.append({"k": "mod", "id": bid, "old": _clip(old), "text": _clip(op["content"])})
            else:
                out.append({"k": "props", "id": bid, "text": _clip(old)})
        elif op["op"] == "move" and bid not in inserted:
            out.append({"k": "move", "id": bid, "text": _clip((before.get(bid) or {}).get("content", ""))})
    return out


def _whole_changes(tree: dict, key: str, root: str) -> list[dict]:
    """The changes of a page that came or went whole: every block under
    the root as one ``add`` / ``del``."""
    return [{"k": key, "id": bid, "text": _clip(b.get("content", ""))}
            for bid, b in tree.items() if bid != root][:CHANGES_CAP]


def _note(ws: str, page_id: str, action: str, title: str = "", *, stats: dict | None = None,
          changes: list[dict] | None = None, report: dict | None = None) -> None:
    """One sync_log row: what a round did to a page (``pulled``, ``pushed``,
    ``created here``, ``created there``, ``deleted here``, ``deleted
    there``, ``restored here``, ``restored there``, ``replaced here`` /
    ``there``) with its block counts, which also add up on the round's
    ``report`` (``blocks_added`` / ``blocks_removed`` / ``blocks_changed``),
    and with ``changes``, what each edit did (``_changes``), stored in the
    same JSON."""
    if report is not None and stats:
        report["blocks_added"] = report.get("blocks_added", 0) + stats["add"]
        report["blocks_removed"] = report.get("blocks_removed", 0) + stats["del"]
        report["blocks_changed"] = report.get("blocks_changed", 0) + stats["mod"]
    title = title or _title_of(ws, page_id)
    with connect_pages_db(ws) as conn:
        blob = {**stats, "changes": changes} if stats and changes else stats
        conn.execute("INSERT INTO sync_log (at, page_id, title, action, stats) VALUES (?, ?, ?, ?, ?)",
                     (page_now(), page_id, title[:200], action, json.dumps(blob) if blob else ""))
        conn.execute("DELETE FROM sync_log WHERE id <= (SELECT MAX(id) FROM sync_log) - ?", (SYNC_LOG_KEEP,))
        conn.commit()


def list_log(ws: str, limit: int = 50) -> list[dict]:
    """The newest sync_log rows: ``[{id, at, page_id, title, action, stats,
    changes, exists}]`` (``stats``: ``{add, del, mod}`` block counts, ``{}``
    for a row from before they were kept; ``changes``: what each edit did,
    block by block (``_changes``), ``[]`` when none were kept; ``exists``:
    the page is still here, so it can be opened)."""
    with connect_pages_db(ws) as conn:
        rows = conn.execute(
            "SELECT l.id, l.at, l.page_id, l.title, l.action, l.stats, "
            "EXISTS (SELECT 1 FROM unified_blocks b WHERE b.id = l.page_id AND b.parent_id = 'root') "
            "FROM sync_log l "
            "ORDER BY l.id DESC LIMIT ?", (max(1, min(int(limit or 50), 500)),)).fetchall()
    out = []
    for r in rows:
        try:
            stats = json.loads(r[5]) if r[5] else {}
        except ValueError:
            stats = {}
        changes = stats.pop("changes", []) if isinstance(stats, dict) else []
        out.append({**dict(zip(("id", "at", "page_id", "title", "action"), r[:5])), "stats": stats,
                    "changes": changes, "exists": bool(r[6])})
    return out


def open_conflicts(ws: str) -> int:
    with connect_pages_db(ws) as conn:
        return conn.execute("SELECT COUNT(*) FROM sync_conflicts WHERE resolved = 0").fetchone()[0]


def open_conflict_mark(ws: str) -> tuple[int, int]:
    """``(count, newest id)`` of the open conflicts — the notice's
    fingerprint (gamma/notices.py): a new conflict changes it, resolving
    some of the old ones does not bring the notice back."""
    with connect_pages_db(ws) as conn:
        count, newest = conn.execute(
            "SELECT COUNT(*), COALESCE(MAX(id), 0) FROM sync_conflicts WHERE resolved = 0").fetchone()
    return int(count), int(newest)


def list_conflicts(ws: str, *, resolved: bool = False, page_id: str = "") -> list[dict]:
    """The decisions to look at (or the looked-at ones), newest first, one
    page's only when ``page_id`` is given."""
    with connect_pages_db(ws) as conn:
        rows = conn.execute(
            "SELECT c.id, c.page_id, c.block_id, c.kind, c.mine, c.theirs, c.result, c.at, c.resolved, "
            "(SELECT content FROM unified_blocks WHERE id = c.page_id), c.base FROM sync_conflicts c "
            "WHERE c.resolved = ? AND (? = '' OR c.page_id = ?) ORDER BY c.id DESC LIMIT 500",
            (1 if resolved else 0, page_id, page_id)).fetchall()
    return [dict(zip(("id", "page_id", "block_id", "kind", "mine", "theirs", "result", "at", "resolved",
                      "page_title", "base"), r)) for r in rows]


def resolve_conflict(ws: str, conflict_id: int, choice: str) -> dict | None:
    """``keep`` marks it seen; ``mine`` / ``theirs`` write that text into the
    block (a normal local edit, pushed by the next round)."""
    if choice not in ("keep", "mine", "theirs"):
        raise ValueError("choice must be keep, mine or theirs")
    with connect_pages_db(ws) as conn:
        row = conn.execute("SELECT page_id, block_id, kind, mine, theirs, result FROM sync_conflicts WHERE id = ?",
                           (conflict_id,)).fetchone()
        if not row:
            return None
        page_id, block_id, kind, mine, theirs, result = row
        # the block's page now: it may have moved to another page since the conflict was recorded
        home = page_root_id(conn, block_id) if choice != "keep" and kind in ("merged", "diverged") else None
    if home:
        # written as an edit from the text the conflict recorded: whatever was
        # typed into the block since is merged over the chosen version, not lost
        op = {"op": "set", "id": block_id, "content": mine if choice == "mine" else theirs}
        if result:
            op["base"] = result
        commit_ops(ws, home, [op], actor=ACTOR)  # an OpError leaves the conflict open
    with connect_pages_db(ws) as conn:
        conn.execute("UPDATE sync_conflicts SET resolved = 1 WHERE id = ?", (conflict_id,))
        conn.commit()
    return {"id": conflict_id, "resolved": True}


# --- uploads ----------------------------------------------------------------------------

def _pull_files(ws: str, remote: Remote, names: set[str], report: dict) -> None:
    uploads = ws_uploads_dir(ws)
    uploads.mkdir(parents=True, exist_ok=True)
    for name in sorted(names):
        if not UPLOAD_NAME_RE.match(name) or (uploads / name).exists():
            continue
        prog = report.get("progress")
        try:
            data = remote.get_bytes(f"/api/uploads/{name}",
                                    progress=(lambda d, t: prog(name, d, t, "down")) if prog else None)
        except RemoteError as e:
            if e.status == 404:
                continue  # the remote lost it too; the reference stays dangling on both
            raise
        if not matches_name(name, data):
            # not the file (a captive portal's page, an error body): keep none
            # rather than one that would be served as this name forever
            log.warning(f"[mirror] {ws}: {name} from {remote.url} is not what its name says "
                        f"({len(data)} bytes) — not stored")
            continue
        write_atomic(uploads / name, data)
        report["files_pulled"] += 1
        if name.endswith(".pdf"):
            pdf_meta.schedule(ws, name[:-4])


def missing_uploads(ws: str, pages=None) -> set[str]:
    """The upload names the workspace's blocks reference (content, props, a
    page's ``doc_id``) that are not in its uploads folder; only those of
    ``pages`` (page ids) when given."""
    with connect_pages_db(ws) as conn:
        if pages is None:
            rows = conn.execute("SELECT content, properties FROM unified_blocks").fetchall()
        else:
            rows = [(r[3], r[4]) for page_id in pages for r in fetch_subtree(conn, page_id)]
    blocks = []
    for content, props in rows:
        try:
            blocks.append({"content": content or "", "props": json.loads(props or "{}")})
        except ValueError:
            blocks.append({"content": content or "", "props": {}})
    uploads = ws_uploads_dir(ws)
    return {n for n in upload_refs(blocks) if UPLOAD_NAME_RE.match(n) and not (uploads / n).exists()}


def _push_files(ws: str, remote: Remote, names: set[str], report: dict) -> None:
    uploads = ws_uploads_dir(ws)
    for name in sorted(names):
        path = uploads / name
        if not UPLOAD_NAME_RE.match(name) or not path.is_file() or remote.head_ok(f"/api/uploads/{name}"):
            continue
        data = path.read_bytes()
        prog = report.get("progress")
        out = remote.post_file("/api/uploads" if name.endswith(".pdf") else "/api/upload-file", name, data,
                               progress=(lambda d, t: prog(name, d, t, "up")) if prog else None)
        got = (out or {}).get("source_url") or (out or {}).get("url") or ""
        if not got.endswith("/" + name):
            log.warning(f"[mirror] {ws}: uploaded {name} but the remote stored it as {got!r}")
        report["files_pushed"] += 1


# --- one page ------------------------------------------------------------------------------

def _local_snapshot(conn, page_id: str) -> dict | None:
    rows = fetch_subtree(conn, page_id)
    if not rows or rows[0][1] != "root":
        return None
    return snapshot_from_rows(rows)


# A push in flight rides in the stored base under this key (a block id is
# [A-Za-z0-9_-] only, so no block ever has it): ``{batches: [{id, ops}], at}``
# written before the first batch goes, or ``{create: true}`` before the page
# is created there. Whatever saves the page's next base clears it.
PENDING = "~pending"


def _state(conn, page_id: str) -> dict | None:
    row = conn.execute("SELECT remote_seq, base FROM sync_pages WHERE page_id = ?", (page_id,)).fetchone()
    if not row:
        return None
    base = json.loads(row[1] or "{}")
    pending = base.pop(PENDING, None)
    return {"remote_seq": row[0], "base": base, "pending": pending}


def _save_state(conn, page_id: str, remote_seq: int, base: dict) -> None:
    _store_state(conn, page_id, remote_seq, base, None)


def _store_state(conn, page_id: str, remote_seq: int, base: dict, pending: dict | None) -> None:
    stored = {**base, PENDING: pending} if pending else base
    conn.execute("INSERT OR REPLACE INTO sync_pages (page_id, remote_seq, base, synced_at) VALUES (?, ?, ?, ?)",
                 (page_id, remote_seq, json.dumps(stored), page_now()))
    conn.commit()


def _drop_state(conn, page_id: str) -> None:
    conn.execute("DELETE FROM sync_pages WHERE page_id = ?", (page_id,))
    conn.commit()


def _remote_tree(remote: Remote, page_id: str) -> tuple[dict | None, int]:
    """``(snapshot, seq)`` of the remote page, ``(None, 0)`` when it is gone."""
    try:
        out = remote.get(f"/api/blocks/{page_id}/subtree")
    except RemoteError as e:
        if e.status == 404:
            return None, 0
        raise
    return snapshot_from_tree(out["block"]), int(out.get("seq") or 0)


def _relocated(ws: str, page_id: str, ops: list[dict], remote_tree: dict | None = None,
               edits: dict | None = None, *, carry: bool = True) -> list[dict]:
    """Inserts of blocks that live in another page here: the remote moved
    them to this page. Each leaves its page here first (``_move_over``) and
    arrives where the remote put it, with the remote's text — plus, with
    ``carry``, what this copy changed in it since that page's last round:
    its text merged into the remote's, the property keys changed here laid
    over the remote's, counted in ``edits`` for the push. A copy that
    changed nothing in it has nothing to send."""
    out, carried, extra = [], {}, []
    for op in ops:
        if op["op"] == "insert" and op["id"] not in carried:
            with connect_pages_db(ws) as conn:
                home = page_root_id(conn, op["id"])
            if home and home != page_id:
                _move_over(ws, page_id, home, op["id"], remote_tree or {}, carried, extra, edits, carry)
        if op["op"] == "insert" and op["id"] in carried:
            op = {**op, **carried[op["id"]]}
        out.append(op)
    return out + extra


def _move_over(ws: str, page_id: str, home: str, top: str, remote_tree: dict, carried: dict, extra: list,
               edits: dict | None, carry: bool) -> None:
    """``top`` leaves ``home``, the page it lives in here, for ``page_id``,
    where the remote holds it now. What the remote moved along (its subtree
    there) goes too, and so do the blocks this copy made under it (not in
    ``home``'s base: new blocks, appended to ``extra``). The rest of what is
    under it here — moved elsewhere or deleted on the remote — stays in
    ``home``, parked at its top level, for that page's own round to place or
    delete as the remote did. ``home``'s base follows all of it, so that
    round takes none of it for an edit made here (a parked block never
    reads as moved by this copy, the blocks that left never as deleted)."""
    with connect_pages_db(ws) as conn:
        here = snapshot_from_rows(fetch_subtree(conn, top))
        state = _state(conn, home)
        last = last_child_position(conn, home)
        was = state["base"] if state else None
        # (plus what this round kept there against the remote's deletion: a move to this page, it turns out)
        known = {**_left.get(ws, {}), **(was or {})}
        along = subtree_ids(remote_tree, top) if top in remote_tree else {top}
        stay, come, park = set(), set(), []
        for bid in tree_order(here, top):  # parents first
            parent = here[bid]["parent"]
            if parent in stay:
                stay.add(bid)  # goes where its parent goes
            elif bid in along:
                continue
            elif parent in come or (was is not None and bid not in known and bid not in remote_tree):
                come.add(bid)
            else:
                stay.add(bid)
                last = generate_key_between(last, None)
                park.append({"op": "move", "id": bid, "parent": home, "position": last})
        for bid in [top] + tree_order(here, top):
            if bid in stay:
                continue
            if bid in come:
                extra.append({"op": "insert", "id": bid, "parent": here[bid]["parent"],
                              "position": here[bid]["position"], "content": here[bid]["content"],
                              "props": dict(here[bid]["props"])})
                if edits is not None:
                    edits[bid] = {"new"}
            else:
                carried[bid] = _carry(conn, page_id, bid, here[bid], known.get(bid), remote_tree.get(bid), edits, carry)
    commit_ops(ws, home, park + [{"op": "delete", "id": top}], actor=ACTOR, client=CLIENT)
    if state:
        left = set(here) - stay
        base = {bid: b for bid, b in was.items() if bid not in left}
        for op in park:
            if op["id"] in base:
                base[op["id"]] = {**base[op["id"]], "parent": home, "position": op["position"]}
        with connect_pages_db(ws) as conn:
            _store_state(conn, home, state["remote_seq"], base, state["pending"])


def _carry(conn, page_id: str, bid: str, mine: dict, was: dict | None, theirs: dict | None, edits: dict | None,
           carry: bool) -> dict:
    """The text and properties a block moved over from another page arrives
    with, over the remote's (``theirs``): what this copy changed since that
    page's base (``was``) — merged, a ``merged`` conflict when both sides
    changed the text. Without a base the values here stay, as for any block
    both sides hold that no base knows (``_known``). Without ``carry`` (a
    page taking the remote's version whole) the remote's stay and a text
    that differed is kept in a ``diverged`` conflict."""
    if theirs is None:
        return {}
    if not carry:
        if mine["content"] != theirs["content"]:
            _conflict(conn, page_id, bid, "diverged", mine=mine["content"], theirs=theirs["content"],
                      result=theirs["content"], once=True)
        return {}
    if was is None:
        text, props = mine["content"], {**theirs["props"], **mine["props"]}
        if theirs["content"] not in mine["content"]:
            _conflict(conn, page_id, bid, "diverged", mine=mine["content"], theirs=theirs["content"],
                      result=mine["content"], once=True)
    else:
        text = theirs["content"]
        if mine["content"] != was["content"]:
            text = textmerge.merge(was["content"], mine["content"], theirs["content"])[0]
            if theirs["content"] != was["content"]:
                _conflict(conn, page_id, bid, "merged", mine=mine["content"], theirs=theirs["content"],
                          result=text, base=was["content"])
        props = dict(theirs["props"])
        for k, v in props_patch(was["props"], mine["props"]).items():
            if v is None:
                props.pop(k, None)
            else:
                props[k] = v
    out = {}
    if text != theirs["content"]:
        out["content"] = text
    if props != theirs["props"]:
        out["props"] = props
    if out and edits is not None:
        edits.setdefault(bid, set()).update(out)
    return out


def _parked(ws: str, page_id: str, ops: list[dict]) -> list[dict]:
    """Moves whose target key a sibling holds that the same batch moves
    away: the server re-keys a colliding block on arrival, which would leave
    this side's keys drifting from the remote's every round. The holder parks
    on a fresh key at the end first, so every move lands where it says."""
    moves = [op for op in ops if op["op"] == "move"]
    if not moves:
        return ops
    with connect_pages_db(ws) as conn:
        snap = _local_snapshot(conn, page_id) or {}
    at = {(b["parent"], b["position"]): bid for bid, b in snap.items() if bid != page_id}
    moving = {op["id"] for op in moves}
    taken = {b["position"] for bid, b in snap.items() if bid != page_id} | {op["position"] for op in moves}
    last = max(taken) if taken else None
    parked = []
    for op in moves:
        holder = at.get((op["parent"], op["position"]))
        if holder and holder != op["id"] and holder in moving:
            last = generate_key_between(last, None)
            parked.append({"op": "move", "id": holder, "parent": snap[holder]["parent"], "position": last})
    return parked + ops


def _apply_local(ws: str, page_id: str, ops: list[dict], remote_tree: dict | None = None,
                 edits: dict | None = None, *, carry: bool = True) -> list[dict]:
    """Apply ops to the local page in MAX_OPS chunks; the applied (echoed)
    ops back. Blocks arriving from another page here move over
    (``_relocated``, against ``remote_tree``, the remote's tree of this
    page), colliding moves park first (``_parked``)."""
    ops = _parked(ws, page_id, _relocated(ws, page_id, ops, remote_tree, edits, carry=carry))
    applied = []
    for i in range(0, len(ops), MAX_OPS):
        applied.extend(commit_ops(ws, page_id, ops[i:i + MAX_OPS], actor=ACTOR, client=CLIENT)["ops"])
    return applied


def _push(remote: Remote, page_id: str, batches: list[dict]) -> None:
    for batch in batches:
        remote.post(f"/api/pages/{page_id}/ops", {"client": CLIENT, "batch": batch["id"], "ops": batch["ops"]})


def _send(ws: str, remote: Remote, page_id: str, seq: int, base: dict, ops: list[dict]) -> dict:
    """Push ``ops`` (from ``base``, the remote's tree as this copy knows it)
    in batches of MAX_OPS, each under an id of its own, all of them written
    into the page's state (``pending``) before the first goes. A push whose
    answer never comes back (the link dropped on the way back, a proxy's
    504, the app quit) is then confirmed or sent again under the same ids
    by the next round (``_confirm_push``) — never taken for the remote's own
    change, nor sent as a new one. Returns what the remote holds once they
    landed: ``base`` with the ops applied."""
    if ops:
        batches = [{"id": secrets.token_urlsafe(12), "ops": ops[i:i + MAX_OPS]} for i in range(0, len(ops), MAX_OPS)]
        with connect_pages_db(ws) as conn:
            _store_state(conn, page_id, seq, base, {"batches": batches, "at": page_now()})
        _push(remote, page_id, batches)
    return apply(base, ops)


def _unlanded(op: dict, base: dict, remote: dict) -> tuple[dict | None, dict | None]:
    """One op of a push whose answer was lost, against the remote's tree
    now: ``(the part the remote shows, the part to send again)``. A change
    the remote shows counts as landed, and so does one it overrode since (it
    moved the block elsewhere, set that property). Neither part (the op
    waits for the merge that follows, where an edit beats a delete): a
    delete of a subtree the remote changed since, a change to a block the
    remote no longer holds."""
    bid, kind = op["id"], op["op"]
    if kind == "insert":
        return (op, None) if bid in remote else (None, op)
    if kind == "delete":
        if bid not in remote:
            return op, None
        there = subtree_ids(remote, bid)
        untouched = there == subtree_ids(base, bid) and all(remote[d] == base[d] for d in there)
        return (None, op) if untouched else (None, None)
    if bid not in remote:
        return None, None
    now, was = remote[bid], base.get(bid) or {}
    if kind == "move":
        # where the base had it: not landed (a landed move sits where it went, or on a key the remote re-keyed it to)
        at = (now["parent"], now["position"])
        return (None, op) if at != (op["parent"], op.get("position")) and at == (was.get("parent"), was.get("position")) \
            else (op, None)
    landed, rest = {"op": "set", "id": bid}, {"op": "set", "id": bid}
    if "content" in op:
        before = op.get("base", was.get("content", ""))
        shows = now["content"] == op["content"] or textmerge.contains(before, op["content"], now["content"])
        part = {"content": op["content"], **({"base": op["base"]} if "base" in op else {})}
        (landed if shows else rest).update(part)
    for k, v in (op.get("props") or {}).items():
        unchanged = now["props"].get(k) == (was.get("props") or {}).get(k) and now["props"].get(k) != v
        (rest if unchanged else landed).setdefault("props", {})[k] = v
    return (landed if len(landed) > 2 else None), (rest if len(rest) > 2 else None)


def _confirm_push(ws: str, remote: Remote, page_id: str, state: dict, *, resend: bool, report: dict) -> dict:
    """A push this copy never read the answer to: its batches (``pending``)
    are sent again under their ids, minus what the remote already shows
    (``_unlanded``). The remote answers a batch it applied (and still
    remembers, ops.REPLAY_TTL) without applying it again, and applies one it
    never got — or forgot, a restart — of which only what it does not show
    is sent, so nothing lands twice. The remote then holds what was pushed,
    which becomes the base: the merge that follows never takes this copy's
    edits for the remote's, nor sends them again. A resend the remote
    refuses (the page changed under it) or no resend (``resend`` false: the
    page is gone here, or the copy only receives) leaves the base with what
    landed; this copy's edits beyond it stay edits. Returns the state."""
    base = state["base"]
    remote_tree, _ = _remote_tree(remote, page_id)
    landed = []
    for batch in state["pending"]["batches"] if remote_tree is not None else ():
        parts = [_unlanded(op, base, remote_tree) for op in batch["ops"]]
        rest = [r for _, r in parts if r]
        sent = False
        if rest and resend:
            try:
                remote.post(f"/api/pages/{page_id}/ops", {"client": CLIENT, "batch": batch["id"], "ops": rest})
                sent = True
            except RemoteError as e:
                if e.status == 0 or e.status in (408, 429) or 500 <= e.status < 600 and e.status != 507:
                    raise  # the link again: the push stays pending for the next round
                log.info(f"[mirror] {ws}: {page_id}: the push sent again was refused ({e}); what landed stays")
        landed += [p for pair in parts for p in (pair[0], pair[1] if sent else None) if p]
    after = apply(base, landed)
    with connect_pages_db(ws) as conn:
        _save_state(conn, page_id, UNKNOWN_SEQ, after)
    if landed:
        _note(ws, page_id, "pushed", stats=_stats(landed, base), changes=_changes(landed, base), report=report)
        report["pages_pushed"] += 1
    return {"remote_seq": UNKNOWN_SEQ, "base": after, "pending": None}


def _known(conn, page_id: str, base: dict, local: dict, remote: dict) -> dict:
    """``base`` plus the blocks both sides hold that it lacks — a round cut
    short after one side got them from the other, a page both sides had
    before any base — each at the remote's version with only the property
    keys this copy has too. So the remote adds only what it alone has (blocks,
    property keys), nothing is inserted twice or overwritten, and this
    copy's text, place and property values stay for the push to send. A
    remote text the text here does not hold (typed on from it) is kept in a
    ``diverged`` conflict."""
    extra = [bid for bid in local if bid in remote and bid not in base]
    if not extra:
        return base
    out = dict(base)
    for bid in extra:
        mine, theirs = local[bid], remote[bid]
        out[bid] = {**theirs, "props": {k: v for k, v in theirs["props"].items() if k in mine["props"]}}
        if theirs["content"] not in mine["content"]:
            _conflict(conn, page_id, bid, "diverged", mine=mine["content"], theirs=theirs["content"],
                      result=mine["content"], once=True)
    return out


def _reconcile_remote_ops(conn, page_id: str, base: dict, local: dict, remote: dict) -> list[dict]:
    """The remote's diff from base, adjusted so an edit beats a delete:
    remote deletes of subtrees edited here are dropped, and subtrees
    deleted here that the remote edited inside come back whole. A block the
    remote moved *out* of a subtree deleted here is not part of that
    deletion any more: it comes back whole where the remote put it (the
    subtree it left stays deleted unless something still inside it was
    touched there). A remote text change this copy's text already holds (it
    was pushed from here, or applied by a round cut short before its
    bookkeeping) is not applied again."""
    remote_ops = diff(base, remote, page_id)
    local_ops = diff(base, local, page_id)
    # (a key the server re-keyed here is no move: ``moved``)
    local_edited = {op["id"] for op in local_ops if op["op"] in ("set", "insert")} | moved(base, local)
    local_edited |= {op["parent"] for op in local_ops if op["op"] == "insert"}
    touched_here = set()
    for bid in local_edited:
        touched_here.add(bid)
        touched_here.update(ancestors(local, bid))
    local_deleted = [op["id"] for op in local_ops if op["op"] == "delete"]
    remote_touched = {op["id"] for op in remote_ops if op["op"] in ("set", "move", "insert")}
    remote_touched |= {op["parent"] for op in remote_ops if op["op"] in ("insert", "move")}

    out, restored, restored_tops, escaped = [], set(), [], set()
    for top in local_deleted:
        if top not in remote:
            continue  # the remote let it go too
        gone = subtree_ids(base, top)
        still = subtree_ids(remote, top)  # what the remote keeps inside it now
        left = {bid for bid in gone - still if bid in remote}
        # the top-most blocks that left the subtree there: each comes back with its remote subtree
        escaped |= {bid for bid in left if not any(a in left for a in ancestors(remote, bid))}
        if any(bid in still for bid in remote_touched):
            restored |= still
            restored_tops.append(top)
    inserted = set()

    def insert_remote(bid):
        r = remote[bid]
        known = r["parent"] in local or r["parent"] in restored or r["parent"] in inserted
        inserted.add(bid)
        out.append({"op": "insert", "id": bid, "parent": r["parent"] if known else page_id,
                    "position": r["position"], "content": r["content"], "props": dict(r["props"])})

    if restored:
        # re-insert the remote's version of each restored subtree, in tree order
        # (a block that exists here — moved in there — is moved by its own op below)
        for bid in tree_order(remote, page_id):
            if bid in restored and bid not in local:
                insert_remote(bid)
        for top in restored_tops:
            _conflict(conn, page_id, top, "restored_remote_edit", theirs=remote[top]["content"],
                      result="kept the other side's version of a subtree deleted here")
    for op in remote_ops:
        bid = op["id"]
        if bid in inserted:
            continue  # already re-inserted whole
        if op["op"] == "move" and bid in escaped:
            # it left a subtree deleted here: back whole, where the remote moved it, with its own
            # subtree (its later set/move ops are covered by the insert; blocks that exist here
            # and were moved under it there keep their own move ops)
            for eid in [bid] + [d for d in tree_order(remote, page_id) if d != bid and bid in ancestors(remote, d)]:
                if eid not in local:
                    insert_remote(eid)
            _conflict(conn, page_id, bid, "restored_remote_edit", theirs=remote[bid]["content"],
                      result="kept a block the other side moved out of a subtree deleted here")
            continue
        if op["op"] == "delete" and (bid in touched_here or any(x in touched_here for x in subtree_ids(local, bid))):
            _conflict(conn, page_id, bid, "kept_local_edit", mine=local.get(bid, {}).get("content", ""),
                      result="kept a subtree edited here that the other side deleted", once=True)
            continue
        if op["op"] in ("set", "move", "delete") and bid not in local:
            continue  # gone here, not restored: the other side's change to it is dropped (a delete of
            # a block already gone — deleted on both sides, or moved to another page here — is done)
        if op["op"] == "set" and "base" in op and local[bid]["content"] not in (op["base"], op["content"]) \
                and textmerge.contains(op["base"], op["content"], local[bid]["content"]):
            # the text here holds the change already (typed on since): merging it in again would double it
            op = {k: v for k, v in op.items() if k not in ("content", "base")}
            if "props" not in op:
                continue
        if op["op"] in ("insert", "move") and op["parent"] not in local and op["parent"] not in restored \
                and op["parent"] not in inserted:
            op = {**op, "parent": page_id}  # its parent is gone here: land at the page's top level
        if op["op"] == "insert":
            inserted.add(bid)
        out.append(op)
    return out


def _own_edits(base: dict, local: dict) -> dict:
    """What this copy changed since ``base``, block by block: ``{id:
    {"new"}}`` for a block made here, ``{"deleted"}`` for one deleted here,
    else the changed ``place`` / ``content`` / ``props``. A block the server
    only re-keyed was not moved (``moved``)."""
    out = {}
    for bid, b in local.items():
        was = base.get(bid)
        if was is None:
            out[bid] = {"new"}
            continue
        kinds = {k for k in ("content", "props") if b[k] != was[k]}
        if kinds:
            out[bid] = kinds
    for bid in moved(base, local):
        out.setdefault(bid, set()).add("place")
    out.update({bid: {"deleted"} for bid in base if bid not in local})
    return out


def _touched(conn, page_id: str, since: int) -> set | None:
    """The blocks of the page a writer other than the engine touched after
    op-log seq ``since``: typing while the round ran. None when that cannot
    be told (the log no longer reaches back to it, or a writer replaced the
    tree): then every difference counts as this copy's edit."""
    rows = conn.execute("SELECT seq, client, ops FROM page_ops WHERE page_id = ? AND seq > ? ORDER BY seq",
                        (page_id, since)).fetchall()
    if rows and rows[0][0] != since + 1:
        return None
    out = set()
    for _, client, raw in rows:
        if client == CLIENT:
            continue
        for op in json.loads(raw or "[]"):
            if op.get("op") == "reload":
                return None
            out.add(op.get("id"))
    return out


def _mine(bid: str, edits: dict, touched: set) -> set:
    return {"content", "props", "place", "deleted"} if bid in touched else edits.get(bid, set())


def _split(ops: list[dict], edits: dict, touched: set | None) -> list[dict]:
    """The part of ``ops`` (remote tree → this copy) that is this copy's to
    push: what it changed since the base (``edits``), what was typed while
    the round ran (``touched``; None: everything) and every insert (a block
    only this copy holds). The rest differs only through the engine's own
    writes — a key the server re-keyed on arrival, a block brought over from
    another page — and is never pushed: a copy that changed nothing sends
    nothing (``_strays`` puts it back here instead)."""
    if touched is None:
        return ops
    out = []
    for op in ops:
        mine = _mine(op["id"], edits, touched)
        if op["op"] == "insert" or "new" in mine:
            out.append(op)
        elif op["op"] == "delete":
            if "deleted" in mine:
                out.append(op)
        elif op["op"] == "move":
            if "place" in mine:
                out.append(op)
        else:
            part = {k: v for k, v in op.items() if k in ("op", "id") or (k in ("content", "base") and "content" in mine)
                    or (k == "props" and "props" in mine)}
            if len(part) > 2:
                out.append(part)
    return out


def _strays(back: list[dict], local: dict, edits: dict, touched: set, elsewhere: set) -> list[dict]:
    """The other half of ``_split``, from the ops the other way (this copy →
    remote tree): what differs here only through the engine's own writes,
    put back as the remote has it — where it lands as it says: a move under
    a parent that is here, onto a key no block that stays holds; an insert
    of a block that is nowhere here (``elsewhere``: ids another page here
    holds), under a parent that is here; a set of a block that is here. A
    block only this copy holds is never removed."""
    out, here = [], set(local)
    for op in back:
        bid, kind = op["id"], op["op"]
        mine = _mine(bid, edits, touched)
        if "new" in mine:
            continue
        if kind == "move":
            if "place" not in mine and bid in local and op["parent"] in here:
                out.append(op)
        elif kind == "insert":
            if "deleted" not in mine and bid not in elsewhere and op["parent"] in here:
                out.append(op)
                here.add(bid)
        elif kind == "set" and bid in local:
            part = {k: v for k, v in op.items() if k in ("op", "id") or (k in ("content", "base") and "content" not in mine)
                    or (k == "props" and "props" not in mine)}
            if len(part) > 2:
                out.append(part)
    moving = {op["id"] for op in out if op["op"] == "move"}
    held = {(b["parent"], b["position"]): h for h, b in local.items()}
    return [op for op in out if op["op"] != "move"
            or held.get((op["parent"], op["position"]), op["id"]) in moving]


def _pull_whole(ws: str, remote: Remote, page_id: str, remote_tree: dict, seq: int, report: dict, action: str) -> None:
    """A page that comes here whole (new here, or restored): its files
    fetched first (the page never names a file this copy lacks), created
    under its id with the bare page as its base at once — a fill cut short
    goes on as a three-way merge, never as a page with no base — then the
    remote tree laid in and the state saved. The page takes the remote's
    place in the library when no page here holds that key."""
    _pull_files(ws, remote, upload_refs(remote_tree.values()), report)
    with connect_pages_db(ws) as conn:
        create_page(conn, remote_tree[page_id]["content"], remote_tree[page_id]["props"], block_id=page_id,
                    position=_free_page_key(conn, remote_tree[page_id]["position"]))
        _save_state(conn, page_id, UNKNOWN_SEQ, {page_id: remote_tree[page_id]})
    _apply_local(ws, page_id, diff({page_id: remote_tree[page_id]}, remote_tree, page_id, with_base=False),
                 remote_tree)
    with connect_pages_db(ws) as conn:
        _save_state(conn, page_id, seq, remote_tree)
    _note(ws, page_id, action, remote_tree[page_id]["content"], stats=_whole(remote_tree, "add"),
          changes=_whole_changes(remote_tree, "add", page_id), report=report)
    report["pages_pulled"] += 1


def _free_page_key(conn, key: str) -> str:
    """``key`` for a page made here when it is a valid key no page holds,
    else "" (last in the library)."""
    try:
        validate_order_key(key or "")
    except FIError:
        return ""
    taken = conn.execute("SELECT 1 FROM unified_blocks WHERE parent_id = 'root' AND position = ?", (key,)).fetchone()
    return "" if taken else key


def _push_whole(ws: str, remote: Remote, page_id: str, local: dict, report: dict, action: str) -> None:
    """A page that goes there whole (new there, or restored): created under
    its id with the bare page as its base at once — a fill cut short (a
    refused file, a dropped link, the app quit) goes on as a three-way
    merge, never as a page with no base — its files uploaded, the local
    tree pushed (``_send``) and, once it landed, taken as the base until the
    remote's answer is read and saved. The bare page is the base from before
    the create goes, marked ``pending`` create: a create whose answer is
    lost is not taken for a page the remote had (a link's adopt policy
    would replace this copy's page with it), and one that never landed is
    sent again."""
    with connect_pages_db(ws) as conn:
        _store_state(conn, page_id, UNKNOWN_SEQ, {page_id: local[page_id]}, {"create": True})
    root = _create_remote_page(remote, page_id, local)
    with connect_pages_db(ws) as conn:
        _save_state(conn, page_id, UNKNOWN_SEQ, {page_id: root})
    _push_files(ws, remote, upload_refs(local.values()), report)
    after = _send(ws, remote, page_id, UNKNOWN_SEQ, {page_id: root},
                  diff({page_id: local[page_id]}, local, page_id, with_base=False))
    with connect_pages_db(ws) as conn:
        _save_state(conn, page_id, UNKNOWN_SEQ, after)
    remote_after, seq = _remote_tree(remote, page_id)
    with connect_pages_db(ws) as conn:
        _save_state(conn, page_id, seq, remote_after or local)
    _note(ws, page_id, action, local[page_id]["content"], stats=_whole(local, "add"),
          changes=_whole_changes(local, "add", page_id), report=report)
    report["pages_pushed"] += 1


def _delete_here(ws: str, page_id: str, local: dict, report: dict) -> None:
    """The page goes to Recently deleted here, like a delete made here
    (gamma/trash.py): the other side's deletion never costs this copy its
    notes for good. Coming back from either side later is a page created
    anew (``create_page`` replaces the trashed copy)."""
    with connect_pages_db(ws) as conn:
        trash_page(ws, conn, page_id, actor=ACTOR, client=CLIENT)
        _drop_state(conn, page_id)
    _note(ws, page_id, "deleted here", local[page_id]["content"], stats=_whole(local, "del"),
          changes=_whole_changes(local, "del", page_id), report=report)
    report["pages_deleted"] += 1


def _delete_there(ws: str, remote: Remote, page_id: str, remote_tree: dict, report: dict) -> None:
    remote.delete(f"/api/blocks/{page_id}")
    with connect_pages_db(ws) as conn:
        _drop_state(conn, page_id)
    _note(ws, page_id, "deleted there", remote_tree[page_id]["content"], stats=_whole(remote_tree, "del"),
          changes=_whole_changes(remote_tree, "del", page_id), report=report)
    report["pages_pushed"] += 1


def _sync_page(ws: str, remote: Remote, page_id: str, *, remote_seq_hint: int | None, remote_gone: bool,
               local_gone: bool, mode: str, report: dict) -> None:
    with connect_pages_db(ws) as conn:
        state = _state(conn, page_id)
        # the page's log head before its snapshot: what others write after it
        # while the round runs is typing here (``_touched``)
        head = latest_seq(conn, page_id)
        local = _local_snapshot(conn, page_id)
    push_allowed = mode == "two-way"
    if state and (state["pending"] or {}).get("batches"):
        # a push whose answer was never read goes first, so that the merge
        # below starts from what the remote holds
        state = _confirm_push(ws, remote, page_id, state, resend=push_allowed and local is not None, report=report)
    base = state["base"] if state else {}
    # no base, or one saved before the remote's answer was read (UNKNOWN_SEQ)
    unsettled = state is None or state["remote_seq"] < 0
    # the page's creation there went out and its answer was never read: it may not have landed
    creating = bool(state and (state["pending"] or {}).get("create"))
    # a link's or a force's policy: whose version a page both sides hold without
    # a base takes (none: both are kept, below)
    adopt, prune = report.get("adopt"), bool(report.get("prune"))
    if prune:
        # a force: tombstones say nothing on either side (the per-page state
        # was cleared, so "deleted here, never synced" would skip a page the
        # copy removed) — what the winner has comes over whole, and a page
        # only the loser has is deleted there (the two branches below)
        local_gone = remote_gone = False

    # --- page-level: one side deleted it
    if remote_gone and not local_gone:
        if local is None:
            with connect_pages_db(ws) as conn:
                _drop_state(conn, page_id)
            return
        if state is None or creating or diff(base, local, page_id):
            # edited here since the last round, or never reconciled here (a
            # linked workspace, a restored backup): the tombstone says nothing
            # about this copy's page, which goes back there (a receive-only
            # copy keeps it here)
            if push_allowed:
                with connect_pages_db(ws) as conn:  # (once: a push cut short and run again is one row)
                    _conflict(conn, page_id, page_id, "page_restored", mine=local[page_id]["content"],
                              result="the other side deleted this page; it was edited here, so it came back there"
                              if state else "the other side deleted this page; this copy had it with no sync "
                              "record (a link, a restored backup), so it went back there", once=True)
                _push_whole(ws, remote, page_id, local, report, "restored there")
            elif state is None:
                log.info(f"[mirror] {ws}: {page_id} was deleted on the remote and has no sync record here; kept")
            return
        _delete_here(ws, page_id, local, report)
        return
    if local_gone and local is None and (state or remote_seq_hint is None):
        if not state:
            return  # never synced: nothing to undo on the other side
        remote_tree, seq = _remote_tree(remote, page_id)
        if remote_tree is None:
            with connect_pages_db(ws) as conn:
                _drop_state(conn, page_id)
            return
        # untouched there since the last round (a base whose seq is not known: the tree it holds)
        untouched = not diff(base, remote_tree, page_id) if unsettled else seq == state["remote_seq"]
        if untouched and push_allowed:
            _delete_there(ws, remote, page_id, remote_tree, report)
            return
        # the remote edited it since (or we may not delete there): it comes back here
        _pull_whole(ws, remote, page_id, remote_tree, seq, report, "restored here")
        with connect_pages_db(ws) as conn:
            _conflict(conn, page_id, page_id, "page_restored_from_remote", theirs=remote_tree[page_id]["content"],
                      result="this page was deleted here but edited on the other side, so it came back")
        return
    # (gone here with no sync record while the remote lists it: the remote has
    # it back — restored from its Recently deleted, re-added by a merge —
    # after this copy's copy went, the deletion carried either way. This
    # copy's own tombstone says nothing about that: it comes here whole, as
    # a new page, below.)

    # --- both exist (or the remote one is new here / the local one is new there)
    remote_changed = unsettled or (remote_seq_hint is not None and remote_seq_hint != state["remote_seq"])
    if remote_changed:
        remote_tree, seq = _remote_tree(remote, page_id)
    else:
        remote_tree, seq = base, state["remote_seq"]
    if remote_tree is None:
        if state is None and local is not None and prune and adopt == "theirs":
            # a force pull: a page the original does not have goes (its text waits in a conflict)
            with connect_pages_db(ws) as conn:
                _dropped(conn, page_id, local, {}, "mine")
            _delete_here(ws, page_id, local, report)
            return
        if (state is None or creating) and local is not None and push_allowed:
            # new here, unknown there (or its creation there never landed): it goes over whole
            _push_whole(ws, remote, page_id, local, report, "created there")
        # else: the feed said it changed, but it is gone now (deleted after the feed): next round's tombstone
        return
    if local is None and prune and adopt == "mine" and push_allowed:
        # a force push: a page this copy does not have goes from the original (its text waits in a conflict)
        with connect_pages_db(ws) as conn:
            _dropped(conn, page_id, remote_tree, {}, "theirs")
        _delete_there(ws, remote, page_id, remote_tree, report)
        return
    if local is None:
        # new here: create it and lay the remote tree in
        _pull_whole(ws, remote, page_id, remote_tree, seq, report, "created here")
        return

    if state is None and adopt:
        # both exist and were never reconciled, under a link's or a force's
        # policy: one side's version is taken whole, what the other had is
        # kept in conflicts
        _adopt_page(ws, remote, page_id, local, remote_tree, seq, adopt if push_allowed else "theirs", report)
        return
    # (both exist without a base and no policy asks for one side — a round cut
    # short between a page's creation and its first base, say: nothing is
    # dropped, the blocks both hold count as known at the remote's version)

    # 1. what the remote changed (from base), applied here with the local merge rules
    remote_ops = []
    if remote_changed:
        with connect_pages_db(ws) as conn:
            base = _known(conn, page_id, base, local, remote_tree)
            remote_ops = _reconcile_remote_ops(conn, page_id, base, local, remote_tree)
    edits = _own_edits(base, local)  # what this copy changed since the last round
    if remote_changed:
        # the whole tree's files, not only the changed blocks': a round cut short
        # after the page landed but before its PDF did is repaired here
        _pull_files(ws, remote, upload_refs(remote_tree.values()), report)
        sent = {op["id"]: op for op in remote_ops if op["op"] == "set" and "content" in op}
        # (blocks moved here from another page bring what this copy changed in them into ``edits``)
        applied = _apply_local(ws, page_id, remote_ops, remote_tree, edits)
        with connect_pages_db(ws) as conn:
            for op in applied:
                if op["op"] == "set" and "content" in op and op["id"] in sent \
                        and op["content"] != sent[op["id"]]["content"]:
                    _conflict(conn, page_id, op["id"], "merged", mine=local[op["id"]]["content"],
                              theirs=sent[op["id"]]["content"], result=op["content"],
                              base=(base.get(op["id"]) or {}).get("content", ""))
            # the remote's tree is the base from here on, so a round cut short
            # after this (a refused push, a dropped link, the app quit) never
            # applies these changes again; what is not there is a local edit
            # (pull mode never pushes it, but a later merge keeps it)
            _save_state(conn, page_id, seq, remote_tree)
        if remote_ops:
            _note(ws, page_id, "pulled", remote_tree[page_id]["content"], stats=_stats(remote_ops, local),
                  changes=_changes(remote_ops, local), report=report)
            report["pages_pulled"] += 1
    # 2. what still differs here: this copy's own edits go there (``_split``);
    # what differs only through the engine's own writes is put back as the
    # remote has it (``_strays``), never pushed
    with connect_pages_db(ws) as conn:
        local_now = _local_snapshot(conn, page_id) or local
        touched = _touched(conn, page_id, head)
        back = diff(local_now, remote_tree, page_id)
        elsewhere = {op["id"] for op in back if op["op"] == "insert" and page_root_id(conn, op["id"])}
    # blocks the remote no longer holds that stay here (an edit here beat
    # their deletion there, or they moved to another page there): what they
    # were at the base, for this round's move over of them (``_move_over``)
    _left.setdefault(ws, {}).update({bid: b for bid, b in base.items()
                                     if bid not in remote_tree and bid in local_now and bid != page_id})
    strays = _strays(back, local_now, edits, touched, elsewhere) if touched is not None else []
    if strays:
        try:
            _apply_local(ws, page_id, strays)
        except OpError as e:
            log.info(f"[mirror] {ws}: {page_id}: left as it is here ({e.detail})")
        with connect_pages_db(ws) as conn:
            local_now = _local_snapshot(conn, page_id) or local
            touched = _touched(conn, page_id, head)
    push_ops = _split(diff(remote_tree, local_now, page_id), edits, touched) if push_allowed else []
    if not push_ops:
        return
    files = upload_refs(push_ops)
    if unsettled:
        # after a round cut short the remote may lack a file of a block that
        # is not pushed again — the page's own PDF above all
        files |= upload_refs(local_now.values())
    _push_files(ws, remote, files, report)
    try:
        after = _send(ws, remote, page_id, seq, remote_tree, push_ops)
    except RemoteError as e:
        if e.status == 403 and "outside this page" in (e.detail or ""):
            # a block of ours now lives in another page there (moved
            # there meanwhile): that page's round moves it here too, and
            # this page is looked at again next round
            raise PageDeferred(f"{page_id}: a block moved to another page on the remote; retried next round")
        raise
    with connect_pages_db(ws) as conn:
        # the push landed: the remote holds what was pushed until its answer is read
        _save_state(conn, page_id, UNKNOWN_SEQ, after)
    remote_after, seq = _remote_tree(remote, page_id)
    if remote_after is None:
        return
    # the remote's answer (re-keyed positions, its merges) lands here, merged over any typing since
    settle = diff(local_now, remote_after, page_id)
    if settle:
        _apply_local(ws, page_id, settle, remote_after)
    with connect_pages_db(ws) as conn:
        _save_state(conn, page_id, seq, remote_after)
    _note(ws, page_id, "pushed", local_now[page_id]["content"], stats=_stats(push_ops, remote_tree),
          changes=_changes(push_ops, remote_tree), report=report)
    report["pages_pushed"] += 1


def _adopt_page(ws: str, remote: Remote, page_id: str, local: dict, remote_tree: dict, seq: int,
                policy: str, report: dict) -> None:
    """A page both sides have but that was never reconciled, under a link's
    or a force's policy: ``policy``'s version (``theirs`` = the original's,
    ``mine`` = this copy's) becomes the page on both sides. Nothing the
    other side had is lost: every block whose text differed is a
    ``diverged`` conflict carrying both texts (Use mine / Use theirs still
    work afterwards) and what only the other side had is a ``dropped`` one,
    both written before anything is replaced."""
    if local == remote_tree:
        with connect_pages_db(ws) as conn:
            _save_state(conn, page_id, seq, remote_tree)
        return
    differed = sorted(bid for bid in set(local) & set(remote_tree) if local[bid]["content"] != remote_tree[bid]["content"])
    winner, loser, side = (local, remote_tree, "theirs") if policy == "mine" else (remote_tree, local, "mine")
    with connect_pages_db(ws) as conn:
        for bid in differed:
            _conflict(conn, page_id, bid, "diverged", mine=local[bid]["content"],
                      theirs=remote_tree[bid]["content"], result=winner[bid]["content"], once=True)
        _dropped(conn, page_id, loser, winner, side)
    if policy == "mine":
        push_ops = diff(remote_tree, local, page_id, with_base=False)
        _push_files(ws, remote, upload_refs(push_ops), report)
        after = _send(ws, remote, page_id, seq, remote_tree, push_ops)
        with connect_pages_db(ws) as conn:
            _save_state(conn, page_id, UNKNOWN_SEQ, after)  # landed: the base until the remote's answer is read
        remote_after, seq = _remote_tree(remote, page_id)
        with connect_pages_db(ws) as conn:
            _save_state(conn, page_id, seq, remote_after or local)
        _note(ws, page_id, "replaced there", local[page_id]["content"], stats=_stats(push_ops, remote_tree),
              changes=_changes(push_ops, remote_tree), report=report)
        report["pages_pushed"] += 1
        return
    ops_ = diff(local, remote_tree, page_id, with_base=False)
    _pull_files(ws, remote, upload_refs(remote_tree.values()), report)
    if ops_:
        _apply_local(ws, page_id, ops_, remote_tree, carry=False)
    with connect_pages_db(ws) as conn:
        _save_state(conn, page_id, seq, remote_tree)
    _note(ws, page_id, "replaced here", remote_tree[page_id]["content"], stats=_stats(ops_, local),
          changes=_changes(ops_, local), report=report)
    report["pages_pulled"] += 1


def _create_remote_page(remote: Remote, page_id: str, local: dict) -> dict:
    """Create the page there under its id; its root as stored there (a
    snapshot entry). An id taken there already (the page appeared there
    meanwhile) leaves the page for the next round, which finds it on both
    sides and keeps both."""
    root = local[page_id]
    try:
        out = remote.post("/api/pages", {"id": page_id, "title": root["content"], "properties": root["props"]}) or {}
    except RemoteError as e:
        if e.status == 409:
            raise PageDeferred(f"{page_id}: the page appeared on the remote meanwhile; reconciled next round") from e
        raise
    return {"parent": "root", "position": out.get("position") or "", "content": out.get("content", root["content"]),
            "props": dict(out.get("properties", root["props"]) or {})}


# --- one round -----------------------------------------------------------------------------

def _lock(ws: str) -> threading.Lock:
    with _locks_guard:
        return _locks.setdefault(ws, threading.Lock())


def _feed_all(remote: Remote, cursor: str) -> tuple[dict, dict, str]:
    """Walk the remote feed to the end: ``({page_id: seq}, {page_id:
    deleted_at}, cursor)``."""
    pages, deleted = {}, {}
    for _ in range(200):
        out = remote.get(f"/api/sync/changes?since={urllib.parse.quote(cursor)}&limit=1000")
        for p in out["pages"]:
            pages[p["id"]] = p["seq"]
        for d in out["deleted"]:
            deleted[d["id"]] = d["deleted_at"]
        cursor = out["cursor"]
        if not out["more"]:
            break
    return pages, deleted, cursor


def _local_feed_all(ws: str, cursor: str) -> tuple[set, set, str]:
    pages, deleted = set(), set()
    with connect_pages_db(ws) as conn:
        for _ in range(200):
            out = local_changes(conn, cursor, 1000)
            pages.update(p["id"] for p in out["pages"])
            deleted.update(d["id"] for d in out["deleted"])
            cursor = out["cursor"]
            if not out["more"]:
                break
    return pages, deleted, cursor


def sync_workspace(ws: str, *, fetch=None) -> dict:
    """One round for the mirror ``ws``. Returns the status saved on the
    mirror (``{last_sync, last_error, pages_pulled, pages_pushed, ...}``).
    Rounds for one workspace never overlap; a second caller waits — and
    reads the mirror only once it holds the lock, so what changed while it
    waited (a page unpublished, a detach, a force) is what it runs with."""
    lock = _lock(ws)
    with lock:
        mirror = get_mirror(ws, with_token=True)
        if not mirror:
            raise ValueError("not a mirror")
        _left[ws] = {}
        try:
            return _round(ws, mirror, fetch)
        finally:
            _left.pop(ws, None)


def _round(ws: str, mirror: dict, fetch) -> dict:
    if mirror["mode"] == "off":
        return mirror["status"]  # detached: nothing runs until it is linked again
    mirror = _start_force(ws, mirror)
    if mirror["page_filter"] is not None:
        mirror = {**mirror, "page_filter": _prune_filter(ws, mirror["page_filter"])}
        if not mirror["page_filter"]:
            return mirror["status"]  # a filter naming no page: nothing travels, the remote is not asked
    remote = Remote(mirror["remote_url"], mirror["remote_ws"], mirror["token"], fetch)
    first = not mirror["status"].get("last_sync")  # the first fill (or one that never completed)
    started = time.monotonic()  # local writes up to here are this round's to push
    # the status is only ever patched: what a detach, a force or the filter write
    # to it while the round runs stays (``_patch_status``)
    _patch_status(ws, {"running": True, "started_at": page_now(), "progress": None}, drop=("interrupted",))
    report = {"pages_pulled": 0, "pages_pushed": 0, "pages_deleted": 0, "files_pulled": 0,
              "files_pushed": 0, "blocks_added": 0, "blocks_removed": 0, "blocks_changed": 0,
              "errors": [], "adopt": mirror["status"].get("adopt"),
              "prune": bool(mirror["status"].get("prune"))}
    progress: dict = {}
    last_file_save = [0.0]

    def file_progress(name, done, total, direction):
        # the popover's file line ("↓ paper.pdf 3.2 / 14 MB"): saved at most a few times a second
        now = time.monotonic()
        if done < total and now - last_file_save[0] < 0.3:
            return
        last_file_save[0] = now
        progress["file"] = {"name": name, "done": done, "total": total, "dir": direction}
        _patch_status(ws, {"progress": dict(progress)})

    report["progress"] = file_progress
    mode = mirror["mode"]
    try:
        me = _round_whoami(ws, mirror, remote)
        role = me.get("role") if me else None
        if mode == "two-way" and (me.get("scope") != "write" or role == "viewer"):
            report["errors"].append("the token or your role on the remote is read-only: pulling only")
            mode = "pull"
        remote_pages, remote_deleted, remote_cursor = _feed_all(remote, mirror["remote_cursor"])
        if mode == "two-way" or (report["prune"] and (report["adopt"] or "theirs") == "theirs"):
            # (a force pull reads the local feed whatever the mode: pages only this copy has go)
            local_pages, local_deleted, local_cursor = _local_feed_all(ws, mirror["local_cursor"])
        else:
            # receive only, or read-only on the remote for now: the local feed is not walked and
            # its cursor stays put, so the first round that may push finds every edit made here
            local_pages, local_deleted, local_cursor = set(), set(), mirror["local_cursor"]
        todo = {}
        for page_id in set(remote_pages) | set(remote_deleted) | local_pages | local_deleted:
            todo[page_id] = {"seq": remote_pages.get(page_id),
                             "remote_gone": page_id in remote_deleted and page_id not in remote_pages,
                             "local_gone": page_id in local_deleted and page_id not in local_pages}
        # pages a previous round could not finish come back with the flags they had then
        # (the cursors have moved past them, so the feeds alone would not list them again)
        for page_id, flags in (mirror["status"].get("retry") or {}).items():
            todo.setdefault(page_id, flags)
        if mirror["page_filter"] is not None:
            todo = _filtered(ws, mirror["page_filter"], todo, force=report["prune"])
        failed = {}
        order = sorted(todo)
        for n, page_id in enumerate(order):
            flags = todo[page_id]
            if _stored_mode(ws) == "off":
                # detached while running: the rest waits for a reattach (the cursors move past it)
                failed.update({p: todo[p] for p in order[n:]})
                break
            # the pill and the popover read this while the round runs: "21 of 79 pages"
            progress.clear()
            progress.update(done=n, total=len(todo), page=_title_of(ws, page_id), first=first, at=page_now())
            _patch_status(ws, {"progress": dict(progress)})
            try:
                _sync_page(ws, remote, page_id, remote_seq_hint=flags["seq"], remote_gone=flags["remote_gone"],
                           local_gone=flags["local_gone"], mode=mode, report=report)
            except PageDeferred as e:
                failed[page_id] = flags  # next round, quietly
                log.info(f"[mirror] {ws}: {e}")
            except Exception as e:  # noqa: BLE001 — one page must not sink the round; it is retried next time
                failed[page_id] = flags
                report["errors"].append(f"{page_id}: {e}")
                log.warning(f"[mirror] {ws}: page {page_id}: {e}")
        # files the copy's pages reference but its uploads folder lacks (an
        # interrupted round, a file lost on disk): fetched again every round
        _pull_files(ws, remote, missing_uploads(ws, mirror["page_filter"]), report)
        # cursors move only when the round could talk to the remote at all, and only
        # when nothing reset them meanwhile (a direction change; a force waits its turn)
        with _status_guard:
            now = get_mirror(ws)
            if now and now["remote_cursor"] == mirror["remote_cursor"] and now["local_cursor"] == mirror["local_cursor"]:
                _save(ws, remote_cursor=remote_cursor, local_cursor=local_cursor)
        report = {k: v for k, v in report.items() if k not in ("adopt", "prune", "progress")}
        errors = report.pop("errors")
        patch = {**report, "running": False, "last_sync": page_now(), "mode": mode,
                 "remote_role": role, "remote_user": me.get("user") if me else None,
                 "last_error": errors[0] if errors else "", "retry": failed}

        def finish(current):
            current = {**current, **patch}
            if not failed:
                # a link's or a force's policy is spent once every page went through — unless
                # a newer one was asked for while the round ran
                for key in ("adopt", "prune"):
                    if current.get(key) == mirror["status"].get(key):
                        current.pop(key, None)
            return current

        status = _patch_status(ws, finish, drop=("progress",))
        if not errors and _dirty.get(ws, float("inf")) <= started:
            _dirty.pop(ws, None)  # everything written before the round started went out with it
    except Exception as e:  # noqa: BLE001 — whatever happens, the running flag comes down
        status = _patch_status(ws, {"running": False, "last_error": str(e), "last_attempt": page_now()},
                               drop=("progress",))
        log.warning(f"[mirror] {ws}: {e}")
    if status.get("last_error"):
        _whoami_seen.pop(ws, None)
    return status


def _prune_filter(ws: str, page_filter: list[str]) -> list[str]:
    """The filter without pages that are gone here and have no base (deleted
    here and the deletion carried there, or never here): nothing of theirs
    is left to move. Runs under the round lock."""
    with connect_pages_db(ws) as conn:
        synced = {r[0] for r in conn.execute("SELECT page_id FROM sync_pages")}
        gone = [p for p in page_filter if p not in synced and not conn.execute(
            "SELECT 1 FROM unified_blocks WHERE id = ? AND parent_id = 'root'", (p,)).fetchone()]
    if gone:
        filter_remove(ws, gone)
    return [p for p in page_filter if p not in gone]


def _filtered(ws: str, page_filter: list[str], todo: dict, *, force: bool) -> dict:
    """A filtered mirror's work list: the feeds' entries for its pages only;
    every listed page without a base as "new" (a tombstone there from an
    earlier publication says nothing about this one); and a page with a base
    that was deleted there and not here leaves the filter instead of being
    deleted here: the copy there was removed (unpublished), the page here
    stays. A force leaves the tombstones to its own rules."""
    keep = set(page_filter)
    with connect_pages_db(ws) as conn:
        synced = {r[0] for r in conn.execute("SELECT page_id FROM sync_pages")}
    out = {p: f for p, f in todo.items() if p in keep}
    for page_id in keep - synced:
        out[page_id] = {"seq": None, "remote_gone": False, "local_gone": False}
    if not force:
        dropped = [p for p, f in out.items() if f["remote_gone"] and not f["local_gone"]]
        if dropped:
            filter_remove(ws, dropped)
            for page_id in dropped:
                out.pop(page_id)
                log.info(f"[mirror] {ws}: {page_id} was removed on the remote; it no longer travels, kept here")
    return out


def _title_of(ws: str, page_id: str) -> str:
    with connect_pages_db(ws) as conn:
        row = conn.execute("SELECT content FROM unified_blocks WHERE id = ?", (page_id,)).fetchone()
    return ((row[0] if row else "") or "")[:120]


def sync_in_background(ws: str) -> None:
    def run():
        try:
            sync_workspace(ws)
        except Exception as e:  # noqa: BLE001 — a background round reports, never dies silently
            log.warning(f"[mirror] {ws}: {e}")

    threading.Thread(target=run, name=f"mirror-{ws}", daemon=True).start()


def reset_interrupted() -> None:
    """At startup: a mirror the previous process left ``running`` (the server
    was stopped in the middle of a round) is not running any more; the
    round's progress is dropped and the next round picks up where it was —
    nothing is lost, a round is idempotent."""
    with connect_users_db() as conn:
        for ws, raw in conn.execute("SELECT workspace_id, status FROM mirrors").fetchall():
            status = json.loads(raw or "{}")
            if not status.get("running"):
                continue
            status.update(running=False, interrupted=True)
            status.pop("progress", None)
            conn.execute("UPDATE mirrors SET status = ? WHERE workspace_id = ?", (json.dumps(status), ws))
            log.warning(f"[mirror] {ws}: the last round was interrupted; it continues at the next one")
        conn.commit()


FIRST_PASS_S = 5  # the loop's first round after startup (a copy interrupted mid-fill continues at once)
_pending: dict[str, float] = {}   # ws -> earliest monotonic time a requested round may run
_last_run: dict[str, float] = {}  # ws -> monotonic time of the last round the loop started
_wants_change: dict[str, bool] = {}  # ws -> a round after a local edit (mode on, on_change set)
_dirty: dict[str, float] = {}     # ws -> monotonic time of the last local write no round has pushed yet
_filters: dict[str, frozenset | None] = {}  # ws -> its mirror's page filter (None: every page), read on first use
# ws -> {block id: its base entry} while a round runs: blocks the remote no
# longer holds in their page that stayed here (an edit here beat their
# deletion there — or they moved to another page there, which a later page
# of the round finds out and moves them over from what they were)
_left: dict[str, dict] = {}
_wake = threading.Event()         # set by request_sync so the loop looks again at once


def request_sync(ws: str, delay: float = DEBOUNCE_S) -> None:
    """A round for ``ws`` once things have been quiet for ``delay`` seconds
    (the loop wakes for it; every further request within the delay pushes it
    back — a typing burst is one round)."""
    _pending[ws] = time.monotonic() + delay
    _wake.set()


def has_local_changes(ws: str) -> bool:
    """Whether a local write happened since the last round that could push
    it (the pill's "edits waiting" state). In memory: a restart runs a round
    anyway."""
    return ws in _dirty


def pending_local(mirror: dict) -> bool:
    """The API's ``pending_local``: a local write no round has pushed yet,
    on a two-way copy (a receive-only one never pushes)."""
    return mirror["mode"] == "two-way" and has_local_changes(mirror["workspace_id"])


def _filter_of(ws: str) -> frozenset | None:
    """The page filter of ``ws``'s mirror (None: no mirror, or one that
    moves every page), cached until the filter changes."""
    if ws not in _filters:
        with connect_users_db() as conn:
            row = conn.execute("SELECT page_filter FROM mirrors WHERE workspace_id = ?", (ws,)).fetchone()
        ids = _parse_filter(row[0]) if row else None
        _filters[ws] = None if ids is None else frozenset(ids)
    return _filters[ws]


def _on_commit(ws: str, client: str, page_id: str = "") -> None:
    """``ops.commit_listeners``: a local write marks the copy dirty and, in
    a copy set to sync on change, asks for a round; the engine's own writes
    (client ``sync``) do neither, nor does a write to a page outside the
    mirror's page filter."""
    if client == CLIENT:
        return
    page_filter = _filter_of(ws)
    if page_filter is not None and page_id and page_id not in page_filter:
        return
    _dirty[ws] = time.monotonic()
    if _wants_change.get(ws):
        request_sync(ws)


ops.commit_listeners.append(_on_commit)


def _refresh_wants() -> list[tuple]:
    with connect_users_db() as conn:
        rows = conn.execute("SELECT workspace_id, mode, poll_s, on_change FROM mirrors").fetchall()
    _wants_change.clear()
    for ws, mode, _, on_change in rows:
        _wants_change[ws] = mode != "off" and bool(on_change)
    return rows


def due_now(ws: str, mode: str, poll_s: int, now: float) -> bool:
    """Whether the loop runs ``ws`` this tick: a requested round whose
    quiet time is over, or the mirror's own cadence come round (``poll_s``
    0 = never by itself)."""
    if mode == "off":
        return False
    if ws in _pending and _pending[ws] <= now:
        return True
    return poll_s > 0 and now - _last_run.get(ws, float("-inf")) >= poll_s


def start_loop() -> None:
    """The engine's clock: every ``TICK_S`` seconds, each mirror that is due
    — its own ``poll_s`` come round, or a local edit ``DEBOUNCE_S`` ago
    (``request_sync``) — gets a round; the first pass ``FIRST_PASS_S`` after
    startup. Started once at app startup; ``GAMMA_SYNC_INTERVAL=0`` turns the
    loop off (the tests; the interrupted flags are still reset)."""
    reset_interrupted()
    if config.sync_interval_s() <= 0:
        return

    def run():
        time.sleep(FIRST_PASS_S)
        while True:
            try:
                rows = _refresh_wants()
            except Exception as e:  # noqa: BLE001 — the loop must survive anything
                log.warning(f"[mirror] loop: {e}")
                time.sleep(TICK_S)
                continue
            now = time.monotonic()
            for ws, mode, poll_s, _ in rows:
                if not due_now(ws, mode, int(poll_s or 0), now):
                    continue
                _pending.pop(ws, None)
                _last_run[ws] = now
                try:
                    sync_workspace(ws)
                except Exception as e:  # noqa: BLE001
                    log.warning(f"[mirror] {ws}: {e}")
            # sleep until the next tick, or until the earliest requested round is due, or a request comes in
            now = time.monotonic()
            wait = TICK_S
            for due in _pending.values():
                wait = min(wait, max(0.05, due - now))
            _wake.wait(wait)
            _wake.clear()

    threading.Thread(target=run, name="mirror-loop", daemon=True).start()
