"""The server's stand-in while this build cannot open the data directory.

``app.create_app`` runs the migration runner first (gamma/migrations.py).
When it refuses — a library a newer Gamma wrote, one older than this build
upgrades, a step that failed — the app it returns is ``standby_app``: the
guidance (``migrations.guidance``) as a 503 JSON at every /api address, and
the app's own shell everywhere else (``spa.serve``), whose first read, the
server's config, gets that 503 and shows the page (frontend
``auth/StandbyPage.jsx``, docs/dev/ui-design.md "Sign-in and status
pages"). Nothing here opens the databases the way the app does: no router,
no background round, never ``db.connect_users_db`` (which applies this
build's schema statements to whatever file it opens). What it reads goes
through ``_reading``, a connection that cannot write.

For a library a newer Gamma wrote (``kind`` ``newer``) the page leads with
how to update Gamma as it was installed (the 503's ``install``,
``version.install_info``) and lets an admin save a copy of the library
meanwhile, so it is never out of reach:

- **Sign-in.** ``/api/session`` and ``/api/login`` keep the app's contract,
  so the desktop, iPad and Android shells sign their seeded account (an
  admin) in as they always do and their users never see a form; a browser
  gets one. An account is known by a sign-in here, kept in memory under a
  cookie of its own, or by the app's own session cookie (read, never
  written). Nothing is written to users.db.
- **Save a copy.** As Settings → Workspaces offers it: a workspace's
  export zip (``ws_backup.zip_files``), or every personal workspace's in
  one zip (``ws_backup.write_bundle``, Export all), made in the background
  (``Export``, in the jobs' shape the page shows) and downloaded once it is
  ready. An up-to-date Gamma restores each from Settings → Workspaces →
  Data. Admins only: the newer files may hold tables of each member's own,
  or rules about who sees what, that this build does not know, and an
  admin holds the files already.

Going back to the newer Gamma's snapshot from before its upgrade stays the
operator's route in the technical details (``manage.py backups
--restore``, server stopped): rewinding a library also restarts its links
(published pages, Zotero, offline copies, folders on disk) from the older
state.

Docs: docs/dev/migrations.md "When the server cannot open the library".
"""

import html
import os
import secrets
import sqlite3
import tempfile
import threading
from contextlib import contextmanager
from pathlib import Path

import bcrypt
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from starlette.concurrency import run_in_threadpool

from . import auth, config, ratelimit, spa, version, ws_backup
from .db import BUSY_TIMEOUT_S, page_now, workspace_ids
from .logbuf import log

COOKIE = "gamma_standby"  # a sign-in here; the app's own ``session`` cookie is left as it is
LABEL = "newer-library"   # the label in a saved copy's manifest
# The app's baseline headers, which its middleware adds and the standby has
# none of, and no caching.
HEADERS = {"Cache-Control": "no-store", **auth.SECURITY_HEADERS}


# --- reading the newer files ----------------------------------------------------------

@contextmanager
def _reading(path: Path):
    """A connection to ``path`` that cannot change it (``query_only``). The
    file was written by a newer Gamma: the queries below name only columns
    every release has had, and a file that answers them otherwise raises
    ``sqlite3.Error``, which every caller takes as "not known"."""
    conn = sqlite3.connect(str(path), timeout=BUSY_TIMEOUT_S)
    try:
        conn.execute("PRAGMA query_only = ON")
        yield conn
    finally:
        conn.close()


def _account(where: str, args: tuple) -> dict | None:
    """``{id, username, is_admin}`` of the users.db row ``where`` names;
    None for none, a guest, or a file that cannot answer."""
    try:
        with _reading(config.USERS_DB) as conn:
            row = conn.execute(f"SELECT id, username, is_guest, is_admin FROM users WHERE {where}", args).fetchone()
    except sqlite3.Error:
        return None
    if not row or row[2]:
        return None
    return {"id": row[0], "username": row[1], "is_admin": bool(row[3])}


def _session_account(token: str) -> dict | None:
    """The account of the app's own session cookie, expired as ``auth``
    expires it, without deleting anything."""
    try:
        with _reading(config.USERS_DB) as conn:
            row = conn.execute("SELECT user_id, created_at FROM sessions WHERE token = ?", (token,)).fetchone()
    except sqlite3.Error:
        return None
    if not row or auth._session_expired(row[1]):
        return None
    return _account("id = ?", (row[0],))


def _password_account(username: str, password: str) -> dict | None:
    """The account a password signs in, checked as ``routers/auth.login``
    checks it: never a guest, nor an account without a password (Gamma
    Cloud's)."""
    try:
        with _reading(config.USERS_DB) as conn:
            row = conn.execute("SELECT id, password_hash, is_guest FROM users WHERE username = ?",
                               (username,)).fetchone()
    except sqlite3.Error:
        return None
    if not row or row[2] or not row[1] or not bcrypt.checkpw(password.encode(), row[1].encode()):
        return None
    return _account("id = ?", (row[0],))


def workspaces_of(account: dict) -> list[dict]:
    """``[{id, name, personal, owner, size}]``: the account's workspaces
    that have their files, as Settings → Workspaces lists them, personal
    ones (``workspaces.personal_workspaces``'s rule: a personal workspace it
    is a member of) first, oldest first, then the shared by name.
    ``owner`` is whose it is, as ``ws_backup.write_zip`` names them: the
    first member of a personal workspace, else its first owner."""
    try:
        with _reading(config.USERS_DB) as conn:
            rows = conn.execute(
                "SELECT w.id, w.name, w.kind FROM workspaces w JOIN workspace_members m ON m.workspace_id = w.id "
                "WHERE m.user_id = ? ORDER BY w.created_at", (account["id"],)).fetchall()
            owners = {}
            for ws, username in conn.execute(
                    "SELECT m.workspace_id, u.username FROM workspace_members m JOIN workspaces w "
                    "ON w.id = m.workspace_id JOIN users u ON u.id = m.user_id "
                    "WHERE w.kind = 'personal' OR m.role = 'owner' ORDER BY m.added_at"):
                owners.setdefault(ws, username)
    except sqlite3.Error:
        return []
    on_disk = set(workspace_ids())
    found = []
    for ws, name, kind in rows:
        root = config.WORKSPACES_DIR / ws
        if ws in on_disk and (root / "pages.db").is_file():
            found.append({"id": ws, "name": name or "Workspace", "personal": kind == "personal",
                          "owner": owners.get(ws, ""),
                          "size": _size(root)})
    shared = sorted((w for w in found if not w["personal"]), key=lambda w: w["name"].lower())
    return [w for w in found if w["personal"]] + shared


def _size(root: Path) -> int:
    """The bytes under a workspace's directory. A copy being made opens and
    closes its pages.db meanwhile, and SQLite deletes pages.db-wal on the
    close, which may be after the listing named it: a file gone by its
    turn counts as nothing."""
    total = 0
    for f in root.rglob("*"):
        try:
            if f.is_file():
                total += f.stat().st_size
        except FileNotFoundError:
            continue
    return total


# --- an admin's copy -----------------------------------------------------------------------

class Export:
    """A copy being made for an account, in the jobs' shape (gamma/jobs.py:
    ``kind``, ``state``, ``progress``, ``params``, ``artifact``), so the page
    follows it as Settings follows a workspace-export job (frontend
    tasks/TasksTray.jsx ``TaskBlock``), then downloads it. In this
    process's memory only: the standby writes nothing to the databases."""

    def __init__(self, account: dict, spaces: list[dict], uploads: bool, every: bool):
        self.account, self.spaces, self.uploads, self.every = account, spaces, uploads, every
        self.state, self.error, self.progress = "running", "", {}
        self.started_at, self.finished_at = page_now(), ""
        handle, path = tempfile.mkstemp(prefix="gamma-standby-", suffix=".zip")
        os.close(handle)
        self.path = Path(path)
        self.name = (ws_backup.export_all_name(account["username"], uploads) if every
                     else ws_backup.export_name(spaces[0]["name"], spaces[0]["id"], uploads))

    def report(self, **progress) -> None:
        if progress:
            self.progress = progress

    def _write(self, space: dict, path: Path, progress) -> None:
        ws_backup.zip_files(space["id"], path, {"workspace_name": space["name"],
                                                "kind": "personal" if space["personal"] else "shared",
                                                "user": space["owner"], "exported_by": self.account["username"]},
                            uploads=self.uploads, label=LABEL, progress=progress)

    def run(self) -> None:
        try:
            if self.every:
                ws_backup.write_bundle(self.path, [(w["id"], w["name"], lambda path, beat, w=w: self._write(w, path, beat))
                                                   for w in self.spaces], self.report)
            else:
                self._write(self.spaces[0], self.path, self.report)
            self.state = "done"
        except Exception as e:  # noqa: BLE001 — told to the page; the library is untouched either way
            log.exception("[standby] a copy of the library failed")
            self.state, self.error = "failed", str(e)
        self.finished_at = page_now()

    def view(self) -> dict:
        first = self.spaces[0]
        return {"kind": "workspace-export", "state": self.state, "error": self.error, "progress": self.progress,
                "started_at": self.started_at, "finished_at": self.finished_at,
                "params": {"all": self.every, "uploads": self.uploads, "count": len(self.spaces),
                           "ws": first["id"], "name": first["name"]},
                "artifact": {"name": self.name, "size": self.path.stat().st_size} if self.state == "done" else None}

    def drop(self) -> None:
        self.path.unlink(missing_ok=True)


# --- the page without a frontend build -------------------------------------------------

# A server with no frontend build (GAMMA_STATIC_DIR unset: a checkout run by
# hand, whose page is Vite's) answers its pages with the guidance as plain
# text in a page; everywhere else the app's own page shows it
# (frontend/src/auth/StandbyPage.jsx).
FALLBACK = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title} · Gamma</title><style>body {{ max-width: 40em; margin: 4em auto; padding: 0 16px;
font: 15px/1.55 system-ui, sans-serif; color-scheme: light dark; }}</style></head>
<body><h1>{title}</h1><p>{status}</p><p>{summary}</p><ol>{steps}</ol><ul>{details}</ul></body></html>
"""


def _items(lines: list[str]) -> str:
    return "".join(f"<li>{html.escape(line)}</li>" for line in lines)


def _fallback(guide: dict) -> str:
    return FALLBACK.format(title=html.escape(guide["title"]), status=html.escape(guide["status"]),
                           summary=html.escape(guide["summary"]), steps=_items(guide["steps"]),
                           details=_items([*guide["details"], f"Data folder: {guide['data_dir']}"]))


# --- the app ---------------------------------------------------------------------------------

def standby_app(guide: dict) -> FastAPI:
    """The app while the data directory cannot be opened by this build: the
    guidance as a 503 JSON under /api (the shells' health check reads its
    ``error``, the app's page its words), for a newer library also the
    sign-in, the page's read (``/api/standby``) and an admin's copies, and
    the app's shell at every other address (``spa.serve``), or without a
    build the guidance in a plain page."""
    app = FastAPI(title="Gamma PDF Annotator", docs_url=None, redoc_url=None, openapi_url=None)
    body = {"error": "data_directory_not_upgradable", "build": version.label(), "install": version.install_info(),
            **guide}
    sessions: dict[str, str] = {}  # COOKIE token → account id, for this process's life
    exports: dict[str, Export] = {}  # account id → its latest copy
    exports_lock = threading.Lock()

    def account(request: Request) -> dict | None:
        """The signed-in account: a sign-in here, else the app's own session."""
        if guide["kind"] != "newer":
            return None
        if request.cookies.get(COOKIE) in sessions:
            return _account("id = ?", (sessions[request.cookies[COOKIE]],))
        token = request.cookies.get(auth.SESSION_COOKIE)
        return _session_account(token) if token else None

    @app.middleware("http")
    async def headers(request: Request, call_next):
        response = await call_next(request)
        for name, value in HEADERS.items():
            response.headers.setdefault(name, value)
        return response

    if guide["kind"] == "newer":
        @app.get("/api/session")
        def session(request: Request):
            who = account(request)
            return {"user": who["username"] if who else None, "default_workspace": "", "workspaces": [],
                    "standby": True}

        @app.post("/api/login")
        async def login(request: Request):
            try:
                given = await request.json()
            except ValueError:
                given = None
            if not isinstance(given, dict) or not isinstance(given.get("username"), str) \
                    or not isinstance(given.get("password"), str):
                raise HTTPException(status_code=422, detail="username and password are required")
            ip, username = ratelimit.client_ip(request), given["username"]
            ratelimit.check(f"login:ip:{ip}", max_hits=10, window_seconds=300)
            ratelimit.check(f"login:user:{username}", max_hits=10, window_seconds=300)
            who = await run_in_threadpool(_password_account, username, given["password"])
            if not who:
                raise HTTPException(status_code=401, detail="invalid credentials")
            ratelimit.reset(f"login:ip:{ip}")
            ratelimit.reset(f"login:user:{username}")
            token = secrets.token_urlsafe(32)
            sessions[token] = who["id"]
            response = JSONResponse({"ok": True, "username": who["username"]})
            response.set_cookie(COOKIE, token, httponly=True, samesite="strict", secure=auth._is_https(request))
            return response

        @app.get("/api/standby")
        def library(request: Request):
            """What the page shows besides the guidance: who is signed in,
            for an admin its workspaces, and the copy it is making or made."""
            who = account(request)
            admin = bool(who and who["is_admin"])
            made = exports.get(who["id"]) if admin else None
            return {"user": who["username"] if who else None, "admin": admin,
                    "workspaces": [{k: w[k] for k in ("id", "name", "personal", "size")} for w in workspaces_of(who)]
                    if admin else [],
                    "export": made.view() if made else None}

        @app.post("/api/standby/export")
        async def export(request: Request):
            """Make a copy (``{ws, uploads}``, or ``{all: true, uploads}``
            for every personal workspace, as Settings → Workspaces → Export
            all): an admin's, one at a time; the page follows it at
            ``GET /api/standby`` and downloads it when it is done."""
            who = await run_in_threadpool(account, request)
            if not who or not who["is_admin"]:
                raise HTTPException(status_code=403, detail="Saving a copy is for this server's admins.")
            try:
                given = await request.json()
            except ValueError:
                given = None
            if not isinstance(given, dict):
                raise HTTPException(status_code=422, detail="ws or all is required")
            spaces = await run_in_threadpool(workspaces_of, who)
            every = given.get("all") is True
            chosen = [w for w in spaces if w["personal"]] if every else [w for w in spaces if w["id"] == given.get("ws")]
            if not chosen:
                raise HTTPException(status_code=404, detail="no such workspace")
            with exports_lock:
                made = exports.get(who["id"])
                if made and made.state == "running":
                    raise HTTPException(status_code=409, detail="A copy is being made already.")
                if made:
                    made.drop()
                exports[who["id"]] = made = Export(who, chosen, given.get("uploads") is not False, every)
            threading.Thread(target=made.run, name="standby-export", daemon=True).start()
            return made.view()

        @app.get("/api/standby/export/file")
        def exported(request: Request):
            who = account(request)
            made = exports.get(who["id"]) if who and who["is_admin"] else None
            if not made or made.state != "done":
                raise HTTPException(status_code=404, detail="no copy is ready")
            return FileResponse(made.path, media_type="application/zip", filename=made.name)

    @app.api_route("/api/{path:path}", methods=["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE"],
                   include_in_schema=False)
    def refused(path: str):
        return JSONResponse(body, status_code=503, headers={"Retry-After": "60"})

    if not spa.serve(app):
        @app.api_route("/{path:path}", methods=["GET", "HEAD"], include_in_schema=False)
        def page(path: str):
            return HTMLResponse(_fallback(guide), status_code=503, headers={"Retry-After": "60"})

    return app
