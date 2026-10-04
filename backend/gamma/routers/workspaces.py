"""Workspaces API (/api/workspaces*): create, inspect, rename, access,
quota, kind, default, delete, members, and invitations by Gamma Cloud
username (pending memberships, claimed on the person's first sign-in).

The model and its rules live in gamma/workspaces.py; this is the HTTP skin.
Anyone creates personal workspaces for themselves; server admins create
shared ones (for any owner) and set access, quota and kind. Owner-only
operations (rename, delete, members) also pass for admins, who need no
membership (a lab workspace whose last owner left can be recovered, and
Settings → Workspaces manages every workspace from one place). A change
that can take access away (access, a role, a member removed, the workspace
deleted) re-checks the workspace's open page sockets (``collab.revalidate``).
People are named by username here, as the dialogs name them; the model
takes account ids.
"""

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from .. import cloud_auth, collab, ratelimit, workspaces
from ..auth import require_user_id
from ..db import account_id, account_name, connect_users_db
from ..server_settings import user_limits, usage_bytes, validate_quota_mb, workspace_bytes, workspace_quota

router = APIRouter(prefix="/api/workspaces", tags=["workspaces"])

LOOKUPS_PER_10_MIN = 30  # cloud username lookups per inviting account


class WorkspaceCreate(BaseModel):
    name: str
    kind: str | None = None          # personal (default) / shared (admins)
    owner: str | None = None         # admins: create it for this account
    access: str | None = None        # admins, shared: private (default) / public
    public_role: str | None = None   # admins, shared: viewer (default) / editor
    quota_mb: int | None = None      # admins, shared: 0 or omitted = unlimited


class WorkspaceUpdate(BaseModel):
    name: str | None = None          # owners
    default: bool | None = None      # the caller: make this personal workspace my default
    kind: str | None = None          # admins: personal / shared
    access: str | None = None        # admins
    public_role: str | None = None   # admins (with access)
    quota_mb: int | None = None      # admins; explicit null = unlimited (model_fields_set tells)


class MemberRole(BaseModel):
    role: str


class CloudInvite(BaseModel):
    username: str                    # a Gamma Cloud username
    role: str = "editor"             # editor / viewer


def _member(request: Request, ws: str, needed: str) -> str:
    """The session account's id, if a member of ``ws`` with at least
    ``needed`` (server admins pass every check). 404 for a workspace that
    does not exist (or that the caller may not know exists)."""
    user_id = require_user_id(request)
    if not workspaces.get(ws):
        raise HTTPException(status_code=404, detail="workspace not found")
    if request.state.is_admin:
        return user_id
    role = workspaces.role_of(ws, user_id)
    if not workspaces.at_least(role, needed):
        if role:
            raise HTTPException(status_code=403, detail=f"only a workspace {needed} can do that")
        raise HTTPException(status_code=404, detail="workspace not found")
    return user_id


def _admin_only(request: Request, what: str) -> None:
    if not request.state.is_admin:
        raise HTTPException(status_code=403, detail=f"only a server admin can {what}")


def _quota(mb) -> int | None:
    """A workspace quota as stored: None for unlimited (0, null, omitted)."""
    if mb is None:
        return None
    return validate_quota_mb(mb) or None


def _account(username: str) -> str:
    """The id of the account named ``username``; 400 when none is."""
    with connect_users_db() as conn:
        user_id = account_id(conn, username)
    if not user_id:
        raise HTTPException(status_code=400, detail=f"unknown user: {username}")
    return user_id


def _valid_owner(username: str) -> str:
    """The id of an existing non-guest account (the directory the pickers
    show)."""
    if not any(a["username"] == username for a in workspaces.accounts()):
        raise HTTPException(status_code=400, detail=f"unknown user: {username}")
    return _account(username)


def _payload(ws: str, user_id: str) -> dict:
    """The workspace as the caller (``user_id``, an id) sees it: its row, the
    caller's role (None for an admin who is no member), whose personal
    workspace it is (``personal_of``, a username; "" when shared), whether
    it is the caller's default, and the explicit members followed by the
    pending invitations (tagged ``pending: true``, with the cloud
    ``subject``)."""
    info = workspaces.get(ws)
    with connect_users_db() as conn:
        personal_of = account_name(conn, workspaces.personal_owner(ws))
    return {**info, "role": workspaces.role_of(ws, user_id),
            "personal_of": personal_of,
            "default": workspaces.default_workspace(user_id) == ws,
            "members": workspaces.members_with_pending(ws)}


@router.post("")
def create_workspace(payload: WorkspaceCreate, request: Request):
    """A new personal workspace of the caller's. Admins may make it
    ``shared`` (with ``access`` / ``public_role`` / ``quota_mb``) and name
    another ``owner`` (by username)."""
    user_id = require_user_id(request)
    if request.state.is_guest:
        raise HTTPException(status_code=403, detail="the guest account cannot create workspaces")
    name = workspaces.clean_name(payload.name)
    if not name:
        raise HTTPException(status_code=400, detail="workspace name required")
    kind = payload.kind or "personal"
    other = bool(payload.owner) and payload.owner != request.state.user
    if kind != "personal" or other or payload.access or payload.public_role or payload.quota_mb:
        _admin_only(request, "create a shared workspace, name another owner, or set access and quota")
    owner = _valid_owner(payload.owner) if other else user_id
    if workspaces.membership_count(owner) >= workspaces.MAX_WORKSPACES_PER_USER:
        raise HTTPException(status_code=400, detail="too many workspaces")
    try:
        info = workspaces.create(
            name, owner, kind=kind, by=user_id, access=payload.access or "private",
            public_role=payload.public_role or "viewer", quota_mb=_quota(payload.quota_mb))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return _payload(info["id"], user_id)


@router.get("/mine")
def my_workspaces(request: Request):
    """Settings → Workspaces: every workspace I can open, each with its
    upload size, plus my account's storage (limits + the usage of all my
    personal workspaces together)."""
    user_id = require_user_id(request)
    mine = [{**w, "used_bytes": workspace_bytes(w["id"])} for w in workspaces.list_for_user(user_id)]
    return {"workspaces": mine, "account": {**user_limits(user_id), "used_bytes": usage_bytes(user_id)}}


@router.get("/find-page/{page_id}")
def find_page(page_id: str, request: Request):
    """Which of my workspaces holds this page — for a deep link that names
    no workspace. 404 when none does."""
    user_id = require_user_id(request)
    ws = workspaces.find_page(user_id, page_id)
    if not ws:
        raise HTTPException(status_code=404, detail="page not found in your workspaces")
    return {"workspace_id": ws}


@router.get("/{ws}")
def get_workspace(ws: str, request: Request):
    """The workspace with its members and the storage that applies to it
    (any member; admins)."""
    user_id = _member(request, ws, "viewer")
    return {**_payload(ws, user_id), "quota": workspace_quota(ws)}


@router.put("/{ws}")
def update_workspace(ws: str, payload: WorkspaceUpdate, request: Request):
    """Rename (owner); make it my default (the owner of a personal
    workspace); set kind, access + public role, or the workspace's own
    quota (admin). Fields left out stay as they are."""
    user_id = _member(request, ws, "owner")
    changes = payload.model_dump(exclude_none=True, exclude={"default", "quota_mb"})
    if "quota_mb" in payload.model_fields_set:
        changes["quota_mb"] = _quota(payload.quota_mb)
    if {"kind", "access", "public_role", "quota_mb"} & changes.keys():
        _admin_only(request, "change a workspace's kind, access or quota")
    try:
        workspaces.update(ws, changes, default_for=user_id if payload.default else "")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if {"kind", "access", "public_role"} & changes.keys():
        collab.revalidate(ws)
    return {**_payload(ws, user_id), "quota": workspace_quota(ws)}


# Sync def: the final copy zips the whole workspace first.
@router.delete("/{ws}")
def delete_workspace(ws: str, request: Request):
    """Delete a workspace and everything in it (owner): ``{ok, warning,
    final_copy}``. A final copy is kept on the server first (backups/deleted/,
    ``final_copy`` its file name); when it cannot be written nothing is
    deleted (507). An account's last personal workspace cannot be deleted;
    deleting the default moves the default to another personal one."""
    user_id = _member(request, ws, "owner")
    try:
        result = workspaces.delete(ws, by=user_id)
    except workspaces.FinalCopyError as e:
        raise HTTPException(status_code=507, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    collab.revalidate(ws)
    return {"ok": True, **result}


@router.put("/{ws}/members/{username}")
def set_member(ws: str, username: str, payload: MemberRole, request: Request):
    """Invite an account, or change a member's role (owner of a shared
    workspace). Naming someone owner is how ownership is handed on."""
    user_id = _member(request, ws, "owner")
    try:
        workspaces.set_member(ws, _account(username), payload.role, by=user_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    collab.revalidate(ws)
    return _payload(ws, user_id)


@router.delete("/{ws}/members/{username}")
def remove_member(ws: str, username: str, request: Request):
    """Remove a member (owner), or leave yourself (any explicit member)."""
    user_id = require_user_id(request)
    leaving = username == request.state.user
    if not leaving:
        _member(request, ws, "owner")
    elif not workspaces.role_of(ws, user_id) and not request.state.is_admin:
        raise HTTPException(status_code=404, detail="workspace not found")
    try:
        workspaces.remove_member(ws, user_id if leaving else _account(username))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    collab.revalidate(ws)
    return {"ok": True, "left": leaving}


@router.get("/{ws}/invites")
def list_invites(ws: str, request: Request):
    """The invitations by Gamma Cloud username still waiting for the
    person's first sign-in (any member; admins)."""
    _member(request, ws, "viewer")
    return {"invites": workspaces.pending_invites(ws)}


@router.post("/{ws}/invites")
def invite_by_cloud_username(ws: str, payload: CloudInvite, request: Request):
    """Invite a Gamma Cloud account by its username (owner of a shared
    workspace; admins) — sync: the lookup goes to the account server. A
    person whose cloud identity is already linked to an account here joins
    at once; anyone else gets a pending membership their first cloud sign-in
    turns into a real one. Refused on personal workspaces and when cloud
    sign-in is off on this server."""
    user_id = _member(request, ws, "owner")
    if workspaces.get(ws)["kind"] != "shared":
        raise HTTPException(status_code=400, detail="a personal workspace has no other members")
    if not cloud_auth.settings()["enabled"]:
        raise HTTPException(status_code=400, detail="Gamma Cloud sign-in is not set up on this server")
    ratelimit.check(f"cloud-lookup:{user_id}", LOOKUPS_PER_10_MIN, 600)
    try:
        invited = workspaces.invite_cloud(ws, payload.username, payload.role, by=user_id)
    except workspaces.CloudLookupError as e:
        raise HTTPException(status_code=503, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {**_payload(ws, user_id), "invited": invited}


@router.delete("/{ws}/invites/{subject}")
def cancel_invite(ws: str, subject: str, request: Request):
    """Withdraw a pending invitation (owner; admins)."""
    _member(request, ws, "owner")
    try:
        workspaces.cancel_invite(ws, subject)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return {"ok": True}
