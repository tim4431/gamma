"""The server's stand-in while this build cannot open the data directory.

``app.create_app`` runs the migration runner first (gamma/migrations.py).
When it refuses — a library a newer Gamma wrote, one older than this build
upgrades, a step that failed — the app it returns is ``standby_app``: one
page at every address saying what to do (``migrations.guidance``), the same
as a 503 JSON under /api, and nothing that opens the databases the way the
app does: no router, no background round, never ``db.connect_users_db``
(which applies this build's schema statements to whatever file it opens).
What it reads, it reads through ``_reading``, a connection that cannot
write.

For a library a newer Gamma wrote (``kind`` ``newer``) the page leads with
how to update Gamma where it runs, and lets an admin save a copy of every
workspace meanwhile, so the library is never out of reach:

- **Sign-in.** ``/api/session`` and ``/api/login`` keep the app's contract,
  so the desktop, iPad and Android shells sign their seeded account (an
  admin) in as they always do and their users never see a form; a browser
  gets one. An account is known by a sign-in here, kept in memory under a
  cookie of its own, or by the app's own session cookie (read, never
  written). Nothing is written to users.db.
- **Save a copy.** Each workspace downloads as the zip ``GET /api/export``
  sends (``ws_backup.zip_files``), which an up-to-date Gamma restores from
  Settings → Workspaces → Data. Admins only: the newer files may hold
  tables of each member's own, or rules about who sees what, that this
  build does not know, and an admin holds the files already.

Going back to the newer Gamma's snapshot from before its upgrade stays the
operator's route in Technical details (``manage.py backups --restore``,
server stopped): rewinding a library also restarts its links (published
pages, Zotero, offline copies, folders on disk) from the older state.

Docs: docs/dev/migrations.md "When the server cannot open the library".
"""

import html
import re
import secrets
import sqlite3
from contextlib import contextmanager
from pathlib import Path

import bcrypt
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from starlette.concurrency import run_in_threadpool

from . import auth, config, migrations, ratelimit, version, ws_backup
from .db import BUSY_TIMEOUT_S, page_now, workspace_ids
from .downloads import TempFileResponse, temp_file

COOKIE = "gamma_standby"  # a sign-in here; the app's own ``session`` cookie is left as it is
LABEL = "newer-library"   # the label in a saved copy's manifest
# The app's baseline headers (auth._apply_security_headers), which its
# middleware adds and the standby has none of.
HEADERS = {"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff",
           "X-Frame-Options": "SAMEORIGIN", "Referrer-Policy": "no-referrer",
           "Content-Security-Policy": "frame-ancestors 'self'"}


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


def workspaces() -> list[dict]:
    """``[{id, name, kind, owner, size}]``: every workspace users.db names
    that has its files, personal ones first, then by name. ``owner`` is
    whose it is as ``ws_backup.write_zip`` names them: the first member of
    a personal workspace, else its first owner."""
    try:
        with _reading(config.USERS_DB) as conn:
            named = {ws: (name, kind) for ws, name, kind in conn.execute("SELECT id, name, kind FROM workspaces")}
            owners = {}
            for ws, username in conn.execute(
                    "SELECT m.workspace_id, u.username FROM workspace_members m JOIN workspaces w "
                    "ON w.id = m.workspace_id JOIN users u ON u.id = m.user_id "
                    "WHERE w.kind = 'personal' OR m.role = 'owner' ORDER BY m.added_at"):
                owners.setdefault(ws, username)
    except sqlite3.Error:
        return []
    found = []
    for ws in workspace_ids():
        root = config.WORKSPACES_DIR / ws
        if ws in named and (root / "pages.db").is_file():
            name, kind = named[ws]
            found.append({"id": ws, "name": name or "Workspace", "kind": kind, "owner": owners.get(ws, ""),
                          "size": sum(f.stat().st_size for f in root.rglob("*") if f.is_file())})
    return sorted(found, key=lambda w: (w["kind"] != "personal", w["name"].lower()))


def _filename(ws: dict) -> str:
    """What ``GET /api/export`` names a workspace's zip."""
    slug = "".join(c if c.isalnum() else "-" for c in ws["name"]).strip("-")[:40] or ws["id"]
    return f"gamma-export-{slug}-{page_now()[:10]}.zip"


def _size(n: float) -> str:
    for unit in ("bytes", "KB", "MB"):
        if n < 1024:
            return f"{n:.0f} {unit}" if unit == "bytes" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} GB"


# --- the page ----------------------------------------------------------------------------

def _text(text: str) -> str:
    """Guidance text as HTML: escaped, `code` spans marked up, bare https://
    addresses made links."""
    def plain(part: str) -> str:
        return re.sub(r"https://[^\s<>\"']*[^\s<>\"'.,;:)]",
                      lambda m: f'<a href="{m[0]}" target="_blank" rel="noopener">{m[0]}</a>', part)
    parts = html.escape(text).split("`")
    return "".join(f"<code>{p}</code>" if i % 2 else plain(p) for i, p in enumerate(parts))


# The page is served before the frontend build and its theme tokens are in
# play, so its light and dark palettes are spelled out here, in the default
# themes' colours (frontend/src/shared/styles/tokens.css).
STYLE = """
:root { color-scheme: light dark; --ground: #f5f6f8; --card: #ffffff; --ink: #1a1a1a; --muted: #5d636b;
  --line: #e2e5e9; --accent: #3a7bd5; --accent-fill: #3a7bd5; --accent-soft: #eaf1fb; --on-accent: #ffffff;
  --ok: #2f8f57; --ok-soft: #e5f3ea; --danger: #c23b3b; --code: #eff1f4; }
@media (prefers-color-scheme: dark) {
  :root { --ground: #141414; --card: #1e1e1e; --ink: #eeeeee; --muted: #a2a8af; --line: #313131;
    --accent: #5b9bd5; --accent-soft: rgba(58, 123, 213, .16); --ok: #4caf7d; --ok-soft: rgba(76, 175, 125, .14);
    --danger: #e05c5c; --code: #2a2a2a; }
}
* { box-sizing: border-box; }
body { margin: 0; min-height: 100vh; background: var(--ground); color: var(--ink);
  font: 15px/1.55 "Inter Variable", Inter, -apple-system, BlinkMacSystemFont, "Segoe UI", system-ui, sans-serif;
  -webkit-text-size-adjust: 100%; }
main { width: 100%; max-width: 580px; margin: 0 auto; padding: 56px 16px 40px; }
.brand { margin: 0 4px 14px; color: var(--muted); font-weight: 600; font-size: .9rem; }
.card { background: var(--card); border: 1px solid var(--line); border-radius: 14px; padding: 24px; margin: 0 0 14px; }
.status { display: inline-flex; align-items: center; gap: 8px; margin: 0 0 14px; padding: 4px 12px 4px 10px;
  border-radius: 999px; background: var(--ok-soft); color: var(--ok); font-size: .82rem; font-weight: 600; }
.status::before { content: ""; flex: none; align-self: flex-start; width: 7px; height: 7px; margin-top: .5em;
  border-radius: 50%; background: currentColor; }
h1 { font-size: 1.4rem; line-height: 1.3; margin: 0 0 8px; letter-spacing: -.01em; }
h2 { font-size: 1rem; margin: 0 0 4px; }
p { margin: 0; color: var(--muted); }
.lead { font-size: 1rem; }
.with-icon { display: flex; gap: 14px; }
.way { margin-top: 20px; padding: 16px; border-radius: 12px; background: var(--accent-soft); }
.way p { color: var(--ink); }
.icon { flex: none; display: grid; place-items: center; width: 34px; height: 34px; border-radius: 50%;
  background: var(--accent-fill); color: var(--on-accent); }
.icon svg { width: 18px; height: 18px; }
.card.with-icon .icon { background: var(--accent-soft); color: var(--accent); }
.body { flex: 1; min-width: 0; }
.rows { list-style: none; margin: 14px 0 12px; padding: 0; border-top: 1px solid var(--line); }
.rows li { display: flex; align-items: center; gap: 12px; padding: 10px 0; border-bottom: 1px solid var(--line); }
.rows .name { flex: 1; min-width: 0; overflow-wrap: anywhere; font-weight: 500; }
.rows .size { color: var(--muted); font-size: .85rem; font-variant-numeric: tabular-nums; white-space: nowrap; }
.hint { font-size: .85rem; }
.button { display: inline-flex; align-items: center; justify-content: center; min-height: 36px; padding: 0 14px;
  border-radius: 8px; border: 1px solid var(--line); background: var(--card); color: var(--ink); font: inherit;
  font-size: .9rem; font-weight: 600; text-decoration: none; cursor: pointer; white-space: nowrap; }
.button:hover { border-color: var(--accent); color: var(--accent); }
.button.primary { background: var(--accent-fill); border-color: var(--accent-fill); color: var(--on-accent); }
.button.primary:hover { filter: brightness(1.08); color: var(--on-accent); }
.button:disabled { opacity: .55; cursor: default; }
.button:focus-visible, input:focus-visible, summary:focus-visible, a:focus-visible {
  outline: 2px solid var(--accent); outline-offset: 2px; }
.actions { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 14px; }
form.signin { display: grid; gap: 8px; margin-top: 14px; }
input { width: 100%; min-height: 38px; padding: 0 12px; border-radius: 8px; border: 1px solid var(--line);
  background: var(--ground); color: var(--ink); font: inherit; }
.error { color: var(--danger); font-size: .88rem; }
ol.steps { margin: 18px 0 0; padding: 0; list-style: none; counter-reset: step; }
ol.steps li { position: relative; padding-left: 34px; margin: 0 0 12px; counter-increment: step; overflow-wrap: anywhere; }
ol.steps li::before { content: counter(step); position: absolute; left: 0; top: 1px; width: 22px; height: 22px;
  border-radius: 50%; background: var(--accent-fill); color: var(--on-accent); font-size: .75rem; font-weight: 700;
  display: grid; place-items: center; }
details { margin: 4px 4px 0; color: var(--muted); font-size: .85rem; }
details.fold { margin: 16px 0 0; padding-top: 12px; border-top: 1px solid var(--line); font-size: .9rem; }
summary { cursor: pointer; font-weight: 600; width: fit-content; }
details ul { margin: 8px 0 0; padding-left: 1.2em; } details li { margin: 6px 0; overflow-wrap: anywhere; }
code { font: .86em/1.4 ui-monospace, "SF Mono", Menlo, Consolas, monospace; padding: 1px 5px; border-radius: 5px;
  background: var(--code); overflow-wrap: anywhere; }
a { color: var(--accent); }
[hidden] { display: none !important; }
@media (max-width: 480px) { main { padding-top: 28px; } .card { padding: 18px; } .with-icon { gap: 12px; } }
"""

ICON_UPDATE = ('<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 19V5M5 12l7-7 7 7" fill="none" '
               'stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>')
ICON_SAVE = ('<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 4v11m-5-5 5 5 5-5M5 20h14" fill="none" '
             'stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>')

# The sign-in form posts the app's /api/login body and reloads on success.
SCRIPT = """
const form = document.querySelector("form.signin");
if (form) form.addEventListener("submit", async (e) => {
  e.preventDefault();
  const error = form.querySelector(".error"), button = form.querySelector("button");
  button.disabled = true; error.hidden = true;
  const r = await fetch("/api/login", { method: "POST", credentials: "same-origin",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ username: form.username.value, password: form.password.value }) }).catch(() => null);
  if (r && r.ok) return location.reload();
  error.textContent = r && r.status === 429 ? "Too many tries. Wait a few minutes and try again."
    : r && r.status === 401 ? "That username and password don't match." : "Couldn't sign in. Try again.";
  error.hidden = false; button.disabled = false;
});
"""

PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title} · Gamma</title><style>{style}</style></head>
<body><main>
<div class="brand">Gamma</div>
{sections}
<details><summary>Technical details</summary><ul>{details}</ul></details>
</main><script>{script}</script></body></html>
"""


def _way(guide: dict) -> str:
    """The way forward: for a newer library its one step in a callout (with
    a button to the releases page on the desktop and Android), else the
    operator's numbered steps and the other way folded."""
    if guide["kind"] != "newer":
        fallback = guide["fallback"]
        fold = (f'<details class="fold"><summary>{html.escape(fallback["lead"])}</summary><ul>'
                + "".join(f"<li>{_text(s)}</li>" for s in fallback["steps"]) + "</ul></details>"
                if fallback["steps"] else "")
        return '<ol class="steps">' + "".join(f"<li>{_text(s)}</li>" for s in guide["steps"]) + f"</ol>{fold}"
    link = (f'<div class="actions"><a class="button primary" href="{version.RELEASES_PAGE}" target="_blank" '
            f'rel="noopener">Get the latest version</a></div>' if migrations.surface() in ("desktop", "android")
            else "")
    return (f'<div class="way with-icon"><div class="icon">{ICON_UPDATE}</div><div class="body">'
            f'<h2>Update Gamma</h2><p>{_text(guide["steps"][0])}</p>{link}</div></div>')


def _save(account: dict | None, spaces: list[dict]) -> str:
    """The card that saves a copy: the sign-in, a word to a member, or the
    admin's download per workspace."""
    if not account:
        who = "an admin of this server" if migrations.surface() == "server" else "your account"
        body = (f'<p>Sign in with {who} to download your workspaces as backup files.</p>'
                '<form class="signin"><input name="username" autocomplete="username" placeholder="Username" '
                'aria-label="Username" required><input name="password" type="password" '
                'autocomplete="current-password" placeholder="Password" aria-label="Password" required>'
                '<p class="error" role="alert" hidden></p>'
                '<div><button class="button primary">Sign in</button></div></form>')
    elif not account["is_admin"]:
        body = (f"<p>You're signed in as {html.escape(account['username'])}. Saving a copy is for this server's "
                f"admins: ask yours to update Gamma, or to save a copy of your workspace for you.</p>")
    elif not spaces:
        body = "<p>There is no workspace to save yet.</p>"
    else:
        rows = "".join(
            f'<li><span class="name">{html.escape(w["name"])}</span><span class="size">{_size(w["size"])}</span>'
            f'<a class="button" href="/api/standby/backup/{html.escape(w["id"])}" download>Download</a></li>'
            for w in spaces)
        body = ('<p>Download each workspace as a backup file, to keep safe or to open in an up-to-date Gamma on '
                f'another device.</p><ul class="rows">{rows}</ul><p class="hint">To open one, use Settings → '
                'Workspaces → Data → Restore from a backup in that Gamma.</p>')
    return (f'<section class="card with-icon"><div class="icon">{ICON_SAVE}</div><div class="body">'
            f'<h2>Save a copy of your library</h2>{body}</div></section>')


def render(guide: dict, account: dict | None = None, spaces: list[dict] | None = None) -> str:
    """The page: the calm line, the title and the way forward; for a newer
    library the card that saves a copy; the technical lines folded."""
    sections = (f'<section class="card"><p class="status">{html.escape(guide["status"])}</p>'
                f'<h1>{html.escape(guide["title"])}</h1><p class="lead">{_text(guide["summary"])}</p>'
                f'{_way(guide)}</section>')
    if guide["kind"] == "newer":
        sections += _save(account, spaces or [])
    details = [*guide["details"], f"Gamma {version.label()}", f"Data folder: `{guide['data_dir']}`",
               f"Snapshots: `{guide['backups_dir']}`",
               "How upgrades work: https://github.com/tim4431/gamma/blob/main/docs/dev/migrations.md"]
    return PAGE.format(title=html.escape(guide["title"]), style=STYLE, sections=sections,
                       details="".join(f"<li>{_text(d)}</li>" for d in details), script=SCRIPT)


# --- the app ---------------------------------------------------------------------------------

def standby_app(guide: dict) -> FastAPI:
    """The app while the data directory cannot be opened by this build: the
    page at every address and the guidance as a 503 JSON under /api (the
    shells' health check reads its ``error``); for a newer library also the
    sign-in and an admin's copies."""
    app = FastAPI(title="Gamma PDF Annotator", docs_url=None, redoc_url=None, openapi_url=None)
    body = {"error": "data_directory_not_upgradable", "build": version.label(), **guide}
    sessions: dict[str, str] = {}  # COOKIE token → account id, for this process's life

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

        @app.get("/api/standby/backup/{ws}")
        def backup(ws: str, request: Request):  # a def: the copy is made in the threadpool, as /api/export's
            who = account(request)
            space = next((w for w in workspaces() if w["id"] == ws), None) if who and who["is_admin"] else None
            if not space:
                raise HTTPException(status_code=404, detail="no such workspace")
            with temp_file(".zip") as tmp:
                ws_backup.zip_files(ws, tmp, {"workspace_name": space["name"], "kind": space["kind"],
                                              "user": space["owner"], "exported_by": who["username"]},
                                    label=LABEL)
            return TempFileResponse(tmp, media_type="application/zip", filename=_filename(space))

    @app.api_route("/{path:path}", methods=["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE"], include_in_schema=False)
    def page(path: str, request: Request):  # a def, off the event loop: it reads files
        if path == "api" or path.startswith("api/"):
            return JSONResponse(body, status_code=503, headers={"Retry-After": "60"})
        who = account(request)
        spaces = workspaces() if who and who["is_admin"] else []
        return HTMLResponse(render(guide, who, spaces), status_code=503, headers={"Retry-After": "60"})

    return app
