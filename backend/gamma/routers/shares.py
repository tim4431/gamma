"""Share links — one per (workspace, page) or per (workspace, folder),
Notion-style people + general access.

A share names a page's root block — papers (the PDF, highlights and notes)
and plain note pages alike — or a folder-label path: the pages filed in that
folder or below it, read live, so pages filed later join and pages moved out
leave (gamma/auth.py ShareScope). Any editor or owner of the workspace
manages it. Settings:

- ``users``: the people invited — ``[{"name", "role"}]``, each with their
  own ``view``/``edit``; they get in whatever the general access says.
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
one). A folder share follows the folder's renames (``move_folder_shares``,
POST /folders/rename) and dies with the folder. Unknown tokens are counted
per IP (gamma/auth.py note_share_miss).
"""

import json
import secrets
import sqlite3

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from ..auth import (SHARE_AUDIENCES, SHARE_ROLES, ShareScope, note_share_miss, require_ws, serialize_share_users,
                    share_access, share_lookup)
from ..blocks_store import page_attachment, root_pages
from ..db import connect_pages_db, connect_users_db, page_now
from ..foldertags import clean_path, path_within

router = APIRouter(prefix="/api", tags=["shares"])


class ShareSettings(BaseModel):
    audience: str | None = None
    role: str | None = None
    users: list | None = None  # ["carol"] or [{"name": "carol", "role": "edit"}] (bare names = view)


def _settings(share: dict) -> dict:
    return {"token": share["token"], "page_id": share["page_id"], "folder": share["folder"],
            "audience": share["audience"], "role": share["role"], "users": share["users"],
            "created_by": share["created_by"]}


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
    """The page target; 404/400 unless page_id is one of the workspace's root pages."""
    with connect_pages_db(ws) as conn:
        row = conn.execute(
            "SELECT parent_id FROM unified_blocks WHERE id = ?", (page_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="page not found")
    if row[0] != "root":
        raise HTTPException(status_code=400, detail="only pages can be shared")
    return ShareScope(page=page_id)


def _require_folder(ws: str, name: str) -> ShareScope:
    """The folder target; 400 for an empty path, 404 unless some page is
    filed in the folder (folders exist only through their pages)."""
    folder = clean_path(name or "")
    if not folder:
        raise HTTPException(status_code=400, detail="folder name required")
    with connect_pages_db(ws) as conn:
        if not root_pages(conn, folder):
            raise HTTPException(status_code=404, detail="folder not found")
    return ShareScope(folder=folder)


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
    audience = payload.audience if payload.audience is not None else current["audience"]
    role = payload.role if payload.role is not None else current["role"]
    users = payload.users if payload.users is not None else current["users"]
    if audience not in SHARE_AUDIENCES:
        raise HTTPException(status_code=400, detail="audience must be anyone, users or list")
    if role not in SHARE_ROLES:
        raise HTTPException(status_code=400, detail="role must be view or edit")
    cleaned: list[dict] = []
    for entry in users:
        if isinstance(entry, dict):
            name, person_role = str(entry.get("name") or "").strip(), entry.get("role") or "view"
        else:
            name, person_role = str(entry or "").strip(), "view"
        if person_role not in SHARE_ROLES:
            raise HTTPException(status_code=400, detail="a person's role must be view or edit")
        if name and name != editor and all(u["name"] != name for u in cleaned):
            cleaned.append({"name": name, "role": person_role})
    if cleaned:
        names = [u["name"] for u in cleaned]
        with connect_users_db() as conn:
            placeholders = ",".join("?" * len(names))
            known = {r[0] for r in conn.execute(
                f"SELECT username FROM users WHERE is_guest = 0 AND username IN ({placeholders})", names)}
        unknown = [n for n in names if n not in known]
        if unknown:
            raise HTTPException(status_code=400, detail=f"unknown user(s): {', '.join(unknown)}")
    return {"audience": audience, "role": role, "users": cleaned}


# ---- the four operations, the same for both targets -------------------------

def _create(ws: str, request: Request, target: ShareScope, payload: ShareSettings | None) -> dict:
    """Create the target's share link (defaults: anyone, view) — or, when one
    exists, return it unchanged so re-sharing never invalidates a link already
    sent around. An optional body applies settings to a NEW link only."""
    existing = _find(ws, target)
    if existing:
        return _settings(existing)
    fields = _validated(request.state.user, {"audience": "anyone", "role": "view", "users": []},
                        payload or ShareSettings())
    token = secrets.token_urlsafe(12)
    with connect_users_db() as conn:
        conn.execute(
            "INSERT INTO shares (token, workspace_id, page_id, folder, created_by, audience, role, allowed_users, "
            "created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (token, ws, target.page, target.folder, request.state.user, fields["audience"],
             fields["role"], serialize_share_users(fields["users"]), page_now()),
        )
        conn.commit()
    return _settings(share_lookup(token))


def _get(ws: str, target: ShareScope) -> dict:
    share = _find(ws, target)
    return _settings(share) if share else _unshared(target)


def _update(ws: str, request: Request, target: ShareScope, payload: ShareSettings, what: str) -> dict:
    """Change who may open the link and what they may do. The token stays."""
    share = _find(ws, target)
    if not share:
        raise HTTPException(status_code=404, detail=f"{what} is not shared")
    fields = _validated(request.state.user, share, payload)
    with connect_users_db() as conn:
        conn.execute(
            "UPDATE shares SET audience = ?, role = ?, allowed_users = ? WHERE token = ?",
            (fields["audience"], fields["role"], serialize_share_users(fields["users"]), share["token"]),
        )
        conn.commit()
    return _settings(share_lookup(share["token"]))


def _delete(ws: str, target: ShareScope) -> dict:
    """Stop sharing: the token dies; sharing again mints a new one."""
    with connect_users_db() as conn:
        cur = conn.execute("DELETE FROM shares WHERE workspace_id = ? AND page_id = ? AND folder = ?",
                           (ws, target.page, target.folder))
        conn.commit()
    return {"ok": True, "removed": cur.rowcount}


def move_folder_shares(ws: str, src: str, dst: str) -> int:
    """Follow a folder rename / move / delete (POST /folders/rename): the
    shares of ``src`` and its subfolders move under ``dst`` (a share already
    at the destination wins and the moved one is dropped), or die when
    ``dst`` is "" (the folder is gone). Returns how many rows changed."""
    changed = 0
    with connect_users_db() as conn:
        rows = conn.execute("SELECT token, folder FROM shares WHERE workspace_id = ? AND folder != ''",
                            (ws,)).fetchall()
        for token, folder in rows:
            if not path_within(folder, src):
                continue
            target = (dst + folder[len(src):]).strip("/") if dst else ""
            if target and not conn.execute(
                    "SELECT 1 FROM shares WHERE workspace_id = ? AND folder = ?", (ws, target)).fetchone():
                conn.execute("UPDATE shares SET folder = ? WHERE token = ?", (target, token))
            else:
                conn.execute("DELETE FROM shares WHERE token = ?", (token,))
            changed += 1
        conn.commit()
    return changed


# ---- folder shares (before the page routes: "folder" is a static segment) ---

@router.post("/share/folder")
async def create_folder_share(request: Request, name: str, payload: ShareSettings | None = None):
    """Create the folder's share link (``?name=<path>``; defaults anyone,
    view) or return the existing one unchanged; workspace editors and owners."""
    ws = require_ws(request, write=True)
    return _create(ws, request, _require_folder(ws, name), payload)


@router.get("/share-settings/folder")
async def get_folder_share_settings(request: Request, name: str):
    """A member's view of a folder's share: its settings, or ``{"token": null}``."""
    ws = require_ws(request)
    return _get(ws, _require_folder(ws, name))


@router.put("/share-settings/folder")
async def update_folder_share_settings(request: Request, name: str, payload: ShareSettings):
    ws = require_ws(request, write=True)
    return _update(ws, request, ShareScope(folder=clean_path(name or "")), payload, "folder")


@router.delete("/share-settings/folder")
async def delete_folder_share(request: Request, name: str):
    ws = require_ws(request, write=True)
    return _delete(ws, ShareScope(folder=clean_path(name or "")))


# ---- page shares ------------------------------------------------------------

@router.post("/share/{page_id}")
async def create_share(page_id: str, request: Request, payload: ShareSettings | None = None):
    """Create the page's share link (defaults: anyone, view) or return the
    existing one unchanged — root blocks only; workspace editors and owners."""
    ws = require_ws(request, write=True)
    return _create(ws, request, _require_page(ws, page_id), payload)


@router.get("/share-settings/{page_id}")
async def get_share_settings(page_id: str, request: Request):
    """A member's view of a page's share: its settings, or ``{"token": null}``
    when the page isn't shared."""
    ws = require_ws(request)
    return _get(ws, _require_page(ws, page_id))


@router.put("/share-settings/{page_id}")
async def update_share_settings(page_id: str, payload: ShareSettings, request: Request):
    ws = require_ws(request, write=True)
    return _update(ws, request, ShareScope(page=page_id), payload, "page")


@router.delete("/share-settings/{page_id}")
async def delete_share(page_id: str, request: Request):
    ws = require_ws(request, write=True)
    return _delete(ws, ShareScope(page=page_id))


# ---- resolving a link -------------------------------------------------------

@router.get("/share/{token}")
async def get_share(token: str, request: Request):
    """Resolve a link for the viewer: 404 unknown, 401 when signing in could
    grant access, 403 when this signed-in account isn't allowed. Otherwise
    what the link shares plus what this viewer may do (``can_edit``): a page
    share carries ``page_id`` and ``doc_id`` (the page's PDF attachment id,
    "" without one); a folder share carries ``folder`` — the share view then
    lists it through ``GET /blocks/root/children`` like the home library.
    ``username`` is who shared it; ``workspace_id`` the workspace.
    ``viewer`` / ``viewer_is_guest``
    tell the share view whether to offer "Open in my library" (a member) or
    "Add to my library" (an account that can import)."""
    share = share_lookup(token)
    if not share:
        note_share_miss(request)
        raise HTTPException(status_code=404, detail="share not found")
    level, reason = share_access(share, request)
    if not level:
        if reason == "login":
            raise HTTPException(status_code=401, detail="sign in to open this shared page")
        raise HTTPException(status_code=403, detail="this page is shared with specific people only")
    out = {"page_id": share["page_id"], "folder": share["folder"],
           "username": share["created_by"], "workspace_id": share["workspace_id"],
           "audience": share["audience"], "role": share["role"], "can_edit": level == "edit",
           "viewer": request.state.user or "", "viewer_is_guest": bool(request.state.is_guest)}
    if share["page_id"]:
        out["doc_id"] = _page_doc_id(share["workspace_id"], share["page_id"])
    return out
