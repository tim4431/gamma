"""Share links — one per (workspace, page) or per (workspace, folder),
Notion-style people + general access.

A share names a page's root block — papers (the PDF, highlights and notes)
and plain note pages alike — or a folder block: the pages filed in that
folder or below it, read live, so pages filed later join, pages moved out
leave and a folder moved in brings its pages (gamma/auth.py ShareScope). Any editor or owner of the workspace
manages it. Settings:

- ``users``: the people invited — ``[{"name", "role"}]`` by username, each
  with their own ``view``/``edit`` (stored as ``share_users`` rows by
  account id); they get in whatever the general access says. On a server
  with Gamma Cloud sign-in a person can also be invited through Gamma
  Cloud (``/invites``): by their Gamma Cloud username before they have an
  account here (gamma/shares.py), or by e-mail address, whoever it belongs
  to (gamma/email_invites.py: the account server mails the link,
  ``/email-invites/{id}`` withdraws or resends it). The settings list
  those as ``pending`` until the person's first sign-in, or their sign-in
  through the mailed link (those by e-mail only for the managers signed in
  with a session), and show the share's managers the requests to join
  that came through its link (``requests``, gamma/join_requests.py), which
  they approve (an invitation by subject) or decline.
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

A token is ``<workspace id>.<secret>`` (``db.share_token_workspace`` reads
it), so share traffic can be placed by its prefix without a lookup; one of
another shape, or whose prefix is not its share's workspace, is refused like
an unknown token.

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

from .. import cloud_auth, collab, email_invites, join_requests, ratelimit, shares
from ..auth import (SHARE_AUDIENCES, SHARE_ROLES, ShareScope, can_write, is_token, note_share_miss, require_user_id,
                    require_ws, share_access, share_lookup)
from ..blocks_store import FOLDERS, TRASH, page_attachment
from ..db import account_name, account_names, connect_pages_db, connect_users_db, delete_shares, page_now

router = APIRouter(prefix="/api", tags=["shares"])


class ShareSettings(BaseModel):
    audience: str | None = None
    role: str | None = None
    users: list | None = None  # ["carol"] or [{"name": "carol", "role": "edit"}] (bare names = view)


class CloudInvite(BaseModel):
    # exactly one of the four
    username: str = ""    # a Gamma Cloud username, looked up at the account server
    subject: str = ""     # or a cloud account the share knows: its waiting invitation, its request to join
    email: str = ""       # or an e-mail address, which the account server mails an invitation to
    invitation: str = ""  # or an invitation by e-mail still waiting: its role changes
    role: str = "view"


def _settings(share: dict, request: Request) -> dict:
    """A share as its dialog shows it to the caller: the people (the invited
    and who created it) by username, an invited account's Gamma Cloud
    username beside it (``cloud``) where the two differ; then the
    invitations still waiting (``pending``), by Gamma Cloud username (with
    their ``subject``) followed, for the people who manage them
    (``_sees_email_invites``), by those by e-mail (with their ``id`` and
    ``email``); and for a manager (``_manages``: who may change the share)
    the requests to join that came through it (``requests``)."""
    mailed = email_invites.listing("share", token=share["token"]) if _sees_email_invites(request) else []
    with connect_users_db() as conn:
        names = account_names(conn, [share["created_by"], *(u["user_id"] for u in share["users"])])
        clouds = cloud_auth.cloud_names(conn, [u["user_id"] for u in share["users"]])
    # a Gamma Cloud username beside a name here that differs from it (tim-2)
    invited = [{"name": names[u["user_id"]], "role": u["role"],
                **({"cloud": clouds[u["user_id"]]} if u["user_id"] in clouds else {})}
               for u in share["users"] if u["user_id"] in names]
    out = {"token": share["token"], "page_id": share["page_id"], "folder": share["folder"],
           "audience": share["audience"], "role": share["role"], "users": invited,
           "pending": shares.pending(share["token"]) + mailed, "created_by": names.get(share["created_by"], "")}
    if _manages(request):
        out["requests"] = join_requests.for_share(share["token"])
    return out


def _manages(request: Request) -> bool:
    """Whether the caller may change the workspace's shares and decide the
    requests to join that come through them: a workspace editor or owner
    with an account that is no guest's."""
    return can_write(request) and not request.state.is_guest


def _sees_email_invites(request: Request) -> bool:
    """Whether the caller sees a share's invitations by e-mail: a manager
    signed in with a session, who can withdraw and resend them. An
    integration token can do neither, and their addresses are personal
    data."""
    return _manages(request) and not is_token(request)


def _manager(request: Request, what: str) -> str:
    """The caller's account id for an action on the people invited through
    Gamma Cloud, by username or by e-mail (``what``): a session, never a
    guest's (403) or an integration token's (401)."""
    user_id = require_user_id(request)
    if request.state.is_guest:
        raise HTTPException(status_code=403, detail=f"a guest cannot {what}")
    return user_id


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


def _shared(ws: str, target: ShareScope, what: str) -> dict:
    """The target's share, or 404 ("page is not shared")."""
    share = _find(ws, target)
    if not share:
        raise HTTPException(status_code=404, detail=f"{what} is not shared")
    return share


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


def _invite(conn, token: str, users: list[dict]) -> list[str]:
    """The share's invited people become ``users`` ([{user_id, role}]).
    Someone invited before keeps when they were (``added_at``); returns
    the ids of the people invited now, whom ``shares.tell`` lets know."""
    had = dict(conn.execute("SELECT user_id, added_at FROM share_users WHERE token = ?", (token,)).fetchall())
    now = page_now()
    conn.execute("DELETE FROM share_users WHERE token = ?", (token,))
    conn.executemany("INSERT INTO share_users (token, user_id, role, added_at) VALUES (?, ?, ?, ?)",
                     [(token, u["user_id"], u["role"], had[u["user_id"]] if u["user_id"] in had else now)
                      for u in users])
    return [u["user_id"] for u in users if u["user_id"] not in had]


# ---- the four operations, the same for both targets -------------------------

def _create(ws: str, request: Request, target: ShareScope, payload: ShareSettings | None) -> dict:
    """Create the target's share link (defaults: anyone, view) — or, when one
    exists, return it unchanged so re-sharing never invalidates a link already
    sent around. An optional body applies settings to a NEW link only."""
    existing = _find(ws, target)
    if existing:
        return _settings(existing, request)
    fields = _validated(request.state.user_id, {"audience": "anyone", "role": "view", "users": []},
                        payload or ShareSettings())
    token = f"{ws}.{secrets.token_urlsafe(12)}"  # <workspace id>.<secret>: db.share_token_workspace
    with connect_users_db() as conn:
        # One share per target (the unique indexes): when another request
        # created it since the lookup above, that link stands and is answered.
        cur = conn.execute(
            "INSERT OR IGNORE INTO shares (token, workspace_id, page_id, folder, created_by, audience, role, "
            "created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (token, ws, target.page, target.folder, request.state.user_id, fields["audience"], fields["role"],
             page_now()),
        )
        invited = _invite(conn, token, fields["users"]) if cur.rowcount else []
    shares.tell(ws, target, token, request.state.user_id, invited)
    return _settings(_find(ws, target), request)


def _get(ws: str, request: Request, target: ShareScope) -> dict:
    share = _find(ws, target)
    return _settings(share, request) if share else _unshared(target)


def _update(ws: str, request: Request, target: ShareScope, payload: ShareSettings, what: str) -> dict:
    """Change who may open the link and what they may do. The token stays."""
    share = _shared(ws, target, what)
    fields = _validated(request.state.user_id, share, payload)
    with connect_users_db() as conn:
        cur = conn.execute("UPDATE shares SET audience = ?, role = ? WHERE token = ?",
                           (fields["audience"], fields["role"], share["token"]))
        invited = _invite(conn, share["token"], fields["users"]) if cur.rowcount else []
    if not cur.rowcount:  # stopped by another request meanwhile
        raise HTTPException(status_code=404, detail=f"{what} is not shared")
    collab.revalidate(ws)
    shares.tell(ws, target, share["token"], request.state.user_id, invited)
    return _settings(share_lookup(share["token"]), request)


def _cloud_invite(ws: str, request: Request, target: ShareScope, payload: CloudInvite, what: str) -> dict:
    """Invite someone who may have no account here yet, named by exactly one
    of: ``username``, their Gamma Cloud username, or ``subject``, a cloud
    account the share knows — the role of an invitation still waiting
    changes, a request to join that came through the share is approved
    (gamma/shares.py ``invite_cloud``); ``email``, an address the account
    server mails an invitation to (gamma/email_invites.py), whoever it
    belongs to; ``invitation``, an invitation by e-mail still waiting,
    whose role changes. A lookup counts against the caller's lookups, a
    workspace's invitations' too; a mail against the caller's invitations
    by e-mail."""
    user_id = _manager(request, "invite people")
    if sum(bool(name) for name in (payload.username, payload.subject, payload.email, payload.invitation)) != 1:
        raise HTTPException(status_code=400,
                            detail="name one person: a username, a subject, an e-mail address or an invitation")
    share = _shared(ws, target, what)
    if payload.invitation:
        try:
            email_invites.set_role(payload.invitation, payload.role, kind="share", token=share["token"])
        except email_invites.NoSuchInvitation as e:
            raise HTTPException(status_code=404, detail=str(e))
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        return _settings(share_lookup(share["token"]), request)
    if payload.email:
        ratelimit.check(f"email-invite:{user_id}", email_invites.SENDS_PER_HOUR, 3600)
        try:
            invited = email_invites.invite("share", payload.email, user_id,
                                           server_url=cloud_auth.callback_base(request), share=share,
                                           role=payload.role)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        return {**_settings(share_lookup(share["token"]), request), "invited": invited}
    if not cloud_auth.settings()["enabled"]:
        raise HTTPException(status_code=400, detail="Gamma Cloud sign-in is not set up on this server")
    if not payload.subject:
        ratelimit.check(f"cloud-lookup:{user_id}", cloud_auth.LOOKUPS_PER_10_MIN, 600)
    try:
        invited = shares.invite_cloud(share, user_id, role=payload.role, name=payload.username,
                                      subject=payload.subject)
    except cloud_auth.CloudLookupError as e:
        raise HTTPException(status_code=503, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if "user" in invited:
        collab.revalidate(ws)  # an account's role may have dropped to view
    return {**_settings(share_lookup(share["token"]), request), "invited": invited}


def _cancel_invite(ws: str, request: Request, target: ShareScope, subject: str, what: str) -> dict:
    """Withdraw an invitation by Gamma Cloud username still waiting."""
    share = _shared(ws, target, what)
    try:
        shares.cancel(share["token"], subject)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return _settings(share, request)


def _withdraw_email(ws: str, request: Request, target: ShareScope, invite_id: str, what: str) -> dict:
    """Withdraw an invitation by e-mail still waiting: its mailed link stops
    working."""
    user_id = _manager(request, "invite people")
    share = _shared(ws, target, what)
    try:
        email_invites.withdraw(invite_id, user_id, server_url=cloud_auth.callback_base(request), kind="share",
                               token=share["token"])
    except email_invites.NoSuchInvitation as e:
        raise HTTPException(status_code=404, detail=str(e))
    return _settings(share, request)


def _resend_email(ws: str, request: Request, target: ShareScope, invite_id: str, what: str) -> dict:
    """Mail an invitation by e-mail again: a new link, its 14 days from now."""
    user_id = _manager(request, "invite people")
    share = _shared(ws, target, what)
    ratelimit.check(f"email-invite:{user_id}", email_invites.SENDS_PER_HOUR, 3600)
    try:
        email_invites.resend(invite_id, user_id, server_url=cloud_auth.callback_base(request), kind="share",
                             share=share)
    except email_invites.NoSuchInvitation as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return _settings(share, request)


def _decline(ws: str, request: Request, target: ShareScope, subject: str, what: str) -> dict:
    """Decline a request to join that came through the share: the person's
    sign-ins ask nothing more until an admin removes it."""
    user_id = _manager(request, "decide requests to join")
    share = _shared(ws, target, what)
    try:
        join_requests.decline(share["token"], subject, user_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return _settings(share, request)


def _delete(ws: str, target: ShareScope) -> dict:
    """Stop sharing: the token dies, and the mailed links of its invitations
    by e-mail; sharing again mints a new one."""
    with connect_users_db() as conn:
        removed, links = delete_shares(conn, "workspace_id = ? AND page_id = ? AND folder = ?",
                                       (ws, target.page, target.folder))
        conn.commit()
    email_invites.cancel_links(links)
    collab.revalidate(ws)
    return {"ok": True, "removed": removed}


def delete_folder_shares(ws: str, folder_ids) -> int:
    """The shares of folders that are gone (the folder and the folders below
    it; ``ops.after_commit`` runs this for the tree batch that deleted them,
    DELETE /folders/{id}'s or a mirror's) die with them, and the mailed
    links of their invitations by e-mail. Returns how many went."""
    with connect_users_db() as conn:
        removed, links = delete_shares(conn, "workspace_id = ? AND folder IN (SELECT value FROM json_each(?))",
                                       (ws, json.dumps(sorted(folder_ids))))
        conn.commit()
    email_invites.cancel_links(links)
    if removed:
        collab.revalidate(ws)
    return removed


def shared_folders(ws: str, folder_ids) -> set[str]:
    """The folders among ``folder_ids`` that have a share link: the agent
    does not delete those (deleting a folder stops its share for good, and
    sharing again mints another link)."""
    with connect_users_db() as conn:
        return {r[0] for r in conn.execute(
            "SELECT folder FROM shares WHERE workspace_id = ? AND folder IN (SELECT value FROM json_each(?))",
            (ws, json.dumps(sorted(folder_ids))))}


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
    return _get(ws, request, _require_folder(ws, folder_id))


@router.put("/share-settings/folder/{folder_id}")
def update_folder_share_settings(folder_id: str, request: Request, payload: ShareSettings):
    ws = require_ws(request, write=True)
    return _update(ws, request, ShareScope(folder=folder_id), payload, "folder")


@router.delete("/share-settings/folder/{folder_id}")
def delete_folder_share(folder_id: str, request: Request):
    ws = require_ws(request, write=True)
    return _delete(ws, ShareScope(folder=folder_id))


@router.post("/share-settings/folder/{folder_id}/invites")
def invite_to_folder_share(folder_id: str, payload: CloudInvite, request: Request):
    """Invite by Gamma Cloud username or by e-mail, change a waiting
    invitation's role, or approve a request to join (``subject``) — sync:
    the account server is asked."""
    ws = require_ws(request, write=True)
    return _cloud_invite(ws, request, ShareScope(folder=folder_id), payload, "folder")


@router.delete("/share-settings/folder/{folder_id}/invites/{subject}")
def cancel_folder_share_invite(folder_id: str, subject: str, request: Request):
    ws = require_ws(request, write=True)
    return _cancel_invite(ws, request, ShareScope(folder=folder_id), subject, "folder")


@router.delete("/share-settings/folder/{folder_id}/email-invites/{invite_id}")
def withdraw_folder_share_email_invite(folder_id: str, invite_id: str, request: Request):
    ws = require_ws(request, write=True)
    return _withdraw_email(ws, request, ShareScope(folder=folder_id), invite_id, "folder")


@router.post("/share-settings/folder/{folder_id}/email-invites/{invite_id}/resend")
def resend_folder_share_email_invite(folder_id: str, invite_id: str, request: Request):
    ws = require_ws(request, write=True)
    return _resend_email(ws, request, ShareScope(folder=folder_id), invite_id, "folder")


@router.delete("/share-settings/folder/{folder_id}/requests/{subject}")
def decline_folder_share_request(folder_id: str, subject: str, request: Request):
    ws = require_ws(request, write=True)
    return _decline(ws, request, ShareScope(folder=folder_id), subject, "folder")


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
    return _get(ws, request, _require_page(ws, page_id))


@router.put("/share-settings/{page_id}")
def update_share_settings(page_id: str, payload: ShareSettings, request: Request):
    ws = require_ws(request, write=True)
    return _update(ws, request, ShareScope(page=page_id), payload, "page")


@router.delete("/share-settings/{page_id}")
def delete_share(page_id: str, request: Request):
    ws = require_ws(request, write=True)
    return _delete(ws, ShareScope(page=page_id))


@router.post("/share-settings/{page_id}/invites")
def invite_to_share(page_id: str, payload: CloudInvite, request: Request):
    """Invite by Gamma Cloud username or by e-mail, change a waiting
    invitation's role, or approve a request to join (``subject``) — sync:
    the account server is asked."""
    ws = require_ws(request, write=True)
    return _cloud_invite(ws, request, ShareScope(page=page_id), payload, "page")


@router.delete("/share-settings/{page_id}/invites/{subject}")
def cancel_share_invite(page_id: str, subject: str, request: Request):
    ws = require_ws(request, write=True)
    return _cancel_invite(ws, request, ShareScope(page=page_id), subject, "page")


@router.delete("/share-settings/{page_id}/email-invites/{invite_id}")
def withdraw_share_email_invite(page_id: str, invite_id: str, request: Request):
    ws = require_ws(request, write=True)
    return _withdraw_email(ws, request, ShareScope(page=page_id), invite_id, "page")


@router.post("/share-settings/{page_id}/email-invites/{invite_id}/resend")
def resend_share_email_invite(page_id: str, invite_id: str, request: Request):
    ws = require_ws(request, write=True)
    return _resend_email(ws, request, ShareScope(page=page_id), invite_id, "page")


@router.delete("/share-settings/{page_id}/requests/{subject}")
def decline_share_request(page_id: str, subject: str, request: Request):
    ws = require_ws(request, write=True)
    return _decline(ws, request, ShareScope(page=page_id), subject, "page")


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
