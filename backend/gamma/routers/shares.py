"""Share links — one per (workspace, page) or per (workspace, folder),
Notion-style people + general access.

A share names a page's root block — papers (the PDF, highlights and notes)
and plain note pages alike — or a folder block: the pages filed in that
folder or below it, read live, so pages filed later join, pages moved out
leave and a folder moved in brings its pages (gamma/auth.py ShareScope). Any editor or owner of the workspace
manages it. Settings:

- ``users``: the people invited — ``[{"name", "role"}]`` by username, each
  with their own ``view``/``edit`` (stored as ``share_users`` rows by
  account id); they get in whatever the general access says.
- ``audience`` (general access): ``anyone`` (the link alone, no login),
  ``users`` (any signed-in non-guest account on this server), ``list`` (only
  the invited people).
- ``role``: what general access grants — ``view`` or ``edit``. Editing is
  confined to the shared pages' block trees (gamma/auth.py require_ws_writer
  + the blocks router's scope checks); a folder edit share covers every page
  in the folder, now and later, never the pages' own settings. ``edit`` with
  ``anyone`` makes the link itself the key: whoever opens it may edit,
  attributed as ``link:<name>`` (gamma/auth.py actor_of) — the sharer's
  call, warned about in the dialog.

Workspace members keep their workspace role on top (gamma/auth.py
share_access). The token confines reads (and edit writes) to the shared
pages' subtrees and assets (share_grant / share_scope).

The token lives until "Stop sharing" (DELETE; sharing again mints a new
one). A folder share names its folder by id, so a rename or a move changes
nothing about it; it dies with the folder (``delete_folder_shares``, run by
``ops.after_commit`` for every path that deletes one). Unknown tokens are counted
per IP (gamma/auth.py note_share_miss). A change that can take access away
re-checks the open page sockets of the workspace (``collab.revalidate``),
so a stopped share stops the live updates too.
"""

import json
import secrets
import sqlite3

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from .. import collab
from ..auth import SHARE_AUDIENCES, SHARE_ROLES, ShareScope, note_share_miss, require_ws, share_access, share_lookup
from ..blocks_store import FOLDERS, TRASH, page_attachment
from ..db import account_name, account_names, connect_pages_db, connect_users_db, delete_shares, page_now

router = APIRouter(prefix="/api", tags=["shares"])


class ShareSettings(BaseModel):
    audience: str | None = None
    role: str | None = None
    users: list | None = None  # ["carol"] or [{"name": "carol", "role": "edit"}] (bare names = view)


def _settings(share: dict) -> dict:
    """A share as its dialog shows it: the people (the invited and who
    created it) by username."""
    with connect_users_db() as conn:
        names = account_names(conn, [share["created_by"], *(u["user_id"] for u in share["users"])])
    invited = [{"name": names[u["user_id"]], "role": u["role"]} for u in share["users"] if u["user_id"] in names]
    return {"token": share["token"], "page_id": share["page_id"], "folder": share["folder"],
            "audience": share["audience"], "role": share["role"], "users": invited,
            "created_by": names.get(share["created_by"], "")}


# A share's target is the ShareScope it grants: the page or the folder, the
# other ''. Everything below takes one and never asks which kind it is.

def _unshared(target: ShareScope) -> dict:
    return {"token": None, "page_id": target.page, "folder": target.folder}


def _find(ws: str, target: ShareScope) -> dict | None:
    with connect_users_db() as conn:
        row = conn.execute(
            "SELECT token FROM shares WHERE workspace_id = ? AND page_id = ? AND folder = ?",
            (ws, target.page, target.folder)).fetchone()
    return share_lookup(row[0]) if row else None


def _require_page(ws: str, page_id: str) -> ShareScope:
    """The page target; 404/400 unless page_id is one of the workspace's root
    pages (a page in Recently deleted is not found)."""
    with connect_pages_db(ws) as conn:
        row = conn.execute(
            "SELECT parent_id FROM unified_blocks WHERE id = ?", (page_id,)).fetchone()
    if not row or row[0] == TRASH:
        raise HTTPException(status_code=404, detail="page not found")
    if row[0] != "root":
        raise HTTPException(status_code=400, detail="only pages can be shared")
    return ShareScope(page=page_id)


def _require_folder(ws: str, folder_id: str) -> ShareScope:
    """The folder target; 404 unless ``folder_id`` is one of the
    workspace's folders (an empty one too)."""
    with connect_pages_db(ws) as conn:
        if not conn.execute("SELECT 1 FROM unified_blocks WHERE id = ? AND page_id = ?",
                            (folder_id, FOLDERS)).fetchone():
            raise HTTPException(status_code=404, detail="folder not found")
    return ShareScope(folder=folder_id)


def _page_doc_id(ws: str, page_id: str) -> str:
    """The id of the shared page's PDF attachment ("" without one)."""
    try:
        with connect_pages_db(ws) as conn:
            row = conn.execute(
                "SELECT properties FROM unified_blocks WHERE id = ?", (page_id,)).fetchone()
    except (sqlite3.Error, ValueError):
        return ""
    if not row:
        return ""
    try:
        attachment = page_attachment(json.loads(row[0] or "{}"))
    except ValueError:
        return ""
    return attachment["id"] if attachment else ""


def _validated(editor: str, current: dict, payload: ShareSettings) -> dict:
    """The settings a write stores: ``payload`` over ``current`` (a share's
    or the defaults), the invited as ``users: [{user_id, role}]`` — names
    resolved to non-guest accounts (400 for one that is none), the editor
    (``editor``, an account id) and repeats left out."""
    audience = payload.audience if payload.audience is not None else current["audience"]
    role = payload.role if payload.role is not None else current["role"]
    if audience not in SHARE_AUDIENCES:
        raise HTTPException(status_code=400, detail="audience must be anyone, users or list")
    if role not in SHARE_ROLES:
        raise HTTPException(status_code=400, detail="role must be view or edit")
    if payload.users is None:
        return {"audience": audience, "role": role, "users": current["users"]}
    named: dict[str, str] = {}
    for entry in payload.users:
        if isinstance(entry, dict):
            name, person_role = str(entry.get("name") or "").strip(), entry.get("role") or "view"
        else:
            name, person_role = str(entry or "").strip(), "view"
        if person_role not in SHARE_ROLES:
            raise HTTPException(status_code=400, detail="a person's role must be view or edit")
        if name:
            named.setdefault(name, person_role)
    ids = {}
    if named:
        with connect_users_db() as conn:
            placeholders = ",".join("?" * len(named))
            ids = dict(conn.execute(
                f"SELECT username, id FROM users WHERE is_guest = 0 AND username IN ({placeholders})", list(named)))
    unknown = [n for n in named if n not in ids]
    if unknown:
        raise HTTPException(status_code=400, detail=f"unknown user(s): {', '.join(unknown)}")
    users = [{"user_id": ids[n], "role": r} for n, r in named.items() if ids[n] != editor]
    return {"audience": audience, "role": role, "users": users}


def _invite(conn, token: str, users: list[dict]) -> None:
    """The share's invited people become ``users`` ([{user_id, role}])."""
    conn.execute("DELETE FROM share_users WHERE token = ?", (token,))
    conn.executemany("INSERT INTO share_users (token, user_id, role) VALUES (?, ?, ?)",
                     [(token, u["user_id"], u["role"]) for u in users])


# ---- the four operations, the same for both targets -------------------------

def _create(ws: str, request: Request, target: ShareScope, payload: ShareSettings | None) -> dict:
    """Create the target's share link (defaults: anyone, view) — or, when one
    exists, return it unchanged so re-sharing never invalidates a link already
    sent around. An optional body applies settings to a NEW link only."""
    existing = _find(ws, target)
    if existing:
        return _settings(existing)
    fields = _validated(request.state.user_id, {"audience": "anyone", "role": "view", "users": []},
                        payload or ShareSettings())
    token = secrets.token_urlsafe(12)
    with connect_users_db() as conn:
        # One share per target (the unique indexes): when another request
        # created it since the lookup above, that link stands and is answered.
        cur = conn.execute(
            "INSERT OR IGNORE INTO shares (token, workspace_id, page_id, folder, created_by, audience, role, "
            "created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (token, ws, target.page, target.folder, request.state.user_id, fields["audience"], fields["role"],
             page_now()),
        )
        if cur.rowcount:
            _invite(conn, token, fields["users"])
    return _settings(_find(ws, target))


def _get(ws: str, target: ShareScope) -> dict:
    share = _find(ws, target)
    return _settings(share) if share else _unshared(target)


def _update(ws: str, request: Request, target: ShareScope, payload: ShareSettings, what: str) -> dict:
    """Change who may open the link and what they may do. The token stays."""
    share = _find(ws, target)
    if not share:
        raise HTTPException(status_code=404, detail=f"{what} is not shared")
    fields = _validated(request.state.user_id, share, payload)
    with connect_users_db() as conn:
        cur = conn.execute("UPDATE shares SET audience = ?, role = ? WHERE token = ?",
                           (fields["audience"], fields["role"], share["token"]))
        if cur.rowcount:
            _invite(conn, share["token"], fields["users"])
    if not cur.rowcount:  # stopped by another request meanwhile
        raise HTTPException(status_code=404, detail=f"{what} is not shared")
    collab.revalidate(ws)
    return _settings(share_lookup(share["token"]))


def _delete(ws: str, target: ShareScope) -> dict:
    """Stop sharing: the token dies; sharing again mints a new one."""
    with connect_users_db() as conn:
        removed = delete_shares(conn, "workspace_id = ? AND page_id = ? AND folder = ?",
                                (ws, target.page, target.folder))
        conn.commit()
    collab.revalidate(ws)
    return {"ok": True, "removed": removed}


def delete_folder_shares(ws: str, folder_ids) -> int:
    """The shares of folders that are gone (the folder and the folders below
    it; ``ops.after_commit`` runs this for the tree batch that deleted them,
    DELETE /folders/{id}'s or a mirror's) die with them. Returns how many
    went."""
    with connect_users_db() as conn:
        removed = delete_shares(conn, "workspace_id = ? AND folder IN (SELECT value FROM json_each(?))",
                                (ws, json.dumps(sorted(folder_ids))))
        conn.commit()
    if removed:
        collab.revalidate(ws)
    return removed


# ---- folder shares (before the page routes: "folder" is a static segment) ---

@router.post("/share/folder/{folder_id}")
def create_folder_share(folder_id: str, request: Request, payload: ShareSettings | None = None):
    """Create the folder's share link (defaults anyone, view) or return the
    existing one unchanged; workspace editors and owners."""
    ws = require_ws(request, write=True)
    return _create(ws, request, _require_folder(ws, folder_id), payload)


@router.get("/share-settings/folder/{folder_id}")
def get_folder_share_settings(folder_id: str, request: Request):
    """A member's view of a folder's share: its settings, or ``{"token": null}``."""
    ws = require_ws(request)
    return _get(ws, _require_folder(ws, folder_id))


@router.put("/share-settings/folder/{folder_id}")
def update_folder_share_settings(folder_id: str, request: Request, payload: ShareSettings):
    ws = require_ws(request, write=True)
    return _update(ws, request, ShareScope(folder=folder_id), payload, "folder")


@router.delete("/share-settings/folder/{folder_id}")
def delete_folder_share(folder_id: str, request: Request):
    ws = require_ws(request, write=True)
    return _delete(ws, ShareScope(folder=folder_id))


# ---- page shares ------------------------------------------------------------

@router.post("/share/{page_id}")
def create_share(page_id: str, request: Request, payload: ShareSettings | None = None):
    """Create the page's share link (defaults: anyone, view) or return the
    existing one unchanged — root blocks only; workspace editors and owners."""
    ws = require_ws(request, write=True)
    return _create(ws, request, _require_page(ws, page_id), payload)


@router.get("/share-settings/{page_id}")
def get_share_settings(page_id: str, request: Request):
    """A member's view of a page's share: its settings, or ``{"token": null}``
    when the page isn't shared."""
    ws = require_ws(request)
    return _get(ws, _require_page(ws, page_id))


@router.put("/share-settings/{page_id}")
def update_share_settings(page_id: str, payload: ShareSettings, request: Request):
    ws = require_ws(request, write=True)
    return _update(ws, request, ShareScope(page=page_id), payload, "page")


@router.delete("/share-settings/{page_id}")
def delete_share(page_id: str, request: Request):
    ws = require_ws(request, write=True)
    return _delete(ws, ShareScope(page=page_id))


# ---- resolving a link -------------------------------------------------------

@router.get("/share/{token}")
def get_share(token: str, request: Request):
    """Resolve a link for the viewer: 404 unknown (or its page deleted or in
    Recently deleted, or its folder gone), 401 when signing in could grant
    access, 403 when this signed-in account isn't allowed. Otherwise what
    the link shares plus what this viewer may do (``can_edit``): a page
    share carries ``page_id`` and ``doc_id`` (the page's PDF attachment id,
    "" without one); a folder share carries ``folder`` (its id) and
    ``folder_name`` — the share view then lists it through
    ``GET /blocks/root/children`` like the home library.
    ``username`` is who shared it; ``workspace_id`` the workspace.
    ``viewer`` / ``viewer_is_guest``
    tell the share view whether to offer "Open in my library" (a member) or
    "Add to my library" (an account that can import)."""
    share = share_lookup(token)
    if not share:
        note_share_miss(request)
        raise HTTPException(status_code=404, detail="share not found")
    level, reason = share_access(share, request.state.user_id, request.state.is_guest)
    if not level:
        if reason == "login":
            raise HTTPException(status_code=401, detail="sign in to open this shared page")
        raise HTTPException(status_code=403, detail="this page is shared with specific people only")
    folder_name = ""
    with connect_pages_db(share["workspace_id"]) as conn:
        if share["page_id"] and not ShareScope.of(share).allows_page(conn, share["page_id"]):
            # deleted, or in Recently deleted: the link opens nothing until a restore
            raise HTTPException(status_code=404, detail="share not found")
        if share["folder"]:
            row = conn.execute("SELECT content FROM unified_blocks WHERE id = ? AND page_id = ?",
                               (share["folder"], FOLDERS)).fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="share not found")
            folder_name = row[0] or ""
    with connect_users_db() as conn:
        sharer = account_name(conn, share["created_by"])
    out = {"page_id": share["page_id"], "folder": share["folder"],
           "username": sharer, "workspace_id": share["workspace_id"],
           "audience": share["audience"], "role": share["role"], "can_edit": level == "edit",
           "viewer": request.state.user or "", "viewer_is_guest": bool(request.state.is_guest)}
    if share["page_id"]:
        out["doc_id"] = _page_doc_id(share["workspace_id"], share["page_id"])
    else:
        out["folder_name"] = folder_name
    return out
