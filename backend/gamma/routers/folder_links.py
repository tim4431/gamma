"""Folders on disk (docs/dev/folder_sync.md "Folders kept by the desktop
app"): the folders of a workspace the desktop app's own server keeps as
directories on this computer (gamma/folder_links.py), of its own
workspaces or of another Gamma server read with a token of it. Only that
server answers (``config.folder_links_enabled``); any other answers 404. The
desktop app is the only caller. A signed-in account's session only: an
integration token is refused as for backups, and so is the guest. Making,
changing, syncing or removing a link of this server's workspace takes the
editor role; listing, any member's. A link with a remote source is the
account's own: it alone sees and changes it."""

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from .. import config, folder_links
from ..auth import require_personal_user_id, require_ws

router = APIRouter(prefix="/api/folder-links", tags=["folder-links"])

SIGN_IN = "Sign in with an account to keep folders on disk."
ELSEWHERE = ("Folders on disk are kept by the Gamma desktop app: open this server in it and use a folder's "
             "\u201cKeep on this computer\u2026\u201d menu item or the sync button's \u201cKeep a folder\u2026\u201d.")


class LinkCreate(BaseModel):
    folder: str = Field(min_length=1, max_length=64)   # a folder block id, or "root"
    path: str = Field(min_length=1, max_length=400)    # the directory's full path on this computer
    notes: bool = True
    # A source on another Gamma server: its address, a token of it (read
    # access is enough), that token's id for whoever revokes it later, and
    # the token's workspace when the caller wants it checked.
    remote_url: str = Field(default="", max_length=400)
    token: str = Field(default="", max_length=400)
    token_id: str = Field(default="", max_length=120)
    workspace: str = Field(default="", max_length=64)


class LinkPatch(BaseModel):
    notes: bool | None = None
    paused: bool | None = None


def _account(request: Request) -> str:
    """The signed-in account, on the one server that keeps folders on disk."""
    if not config.folder_links_enabled():
        raise HTTPException(status_code=404, detail=ELSEWHERE)
    return require_personal_user_id(request, SIGN_IN)


def _mine(request: Request, link_id: str, write: bool = True) -> dict:
    """The link, when the request may see it: a remote source's by the
    account that made it, a local one by a member (editor, to change it)
    of its workspace, which must be the request's."""
    user_id = _account(request)
    link = folder_links.get_link(link_id)
    if link is None:
        raise HTTPException(status_code=404, detail="no such link")
    if link["remote_url"]:
        if link["created_by"] != user_id:
            raise HTTPException(status_code=404, detail="no such link")
    elif link["workspace_id"] != require_ws(request, write=write):
        raise HTTPException(status_code=404, detail="no such link")
    return link


def _info(link: dict) -> dict:
    return {**link, "dest": str(folder_links.dest_of(link))}


@router.get("")
def list_links(request: Request):
    """``{links: [{id, workspace_id, folder_id, path, notes, created_at,
    cursor, status, dest, remote_url, token_id}]}`` — every link the
    account may see, whatever workspace the request names: those of this
    server's workspaces it has a role in (``remote_url`` empty), and its own
    with a remote source (``remote_url`` the other server, ``workspace_id``
    the workspace there). The desktop app's sync panel lists them all."""
    user_id = _account(request)
    return {"links": [_info(link) for link in folder_links.visible_links(user_id)]}


@router.post("", status_code=201)
def create_link(payload: LinkCreate, request: Request):
    """``{folder, path, notes?}`` → the link; its first round runs in the
    background. With ``remote_url`` and ``token`` the folder is one of
    another Gamma server's, read there with the token (``workspace``, when
    given, must be the token's). 400 for a folder that does not exist, a
    path that is not a full one or another link's, a directory holding
    another folder's files, or a server that refuses the token."""
    user_id = _account(request)
    try:
        if payload.remote_url:
            link = folder_links.create_remote_link(payload.remote_url, payload.token, payload.token_id, payload.workspace,
                                                   payload.folder, payload.path, payload.notes, user_id)
        else:
            ws = require_ws(request, write=True)
            link = folder_links.create_link(ws, payload.folder, payload.path, payload.notes, user_id)
    except folder_links.LinkError as e:
        raise HTTPException(status_code=400, detail=str(e))
    folder_links.run_in_background(link["id"])
    return _info(link)


@router.get("/{link_id}")
def get_link(link_id: str, request: Request):
    return _info(_mine(request, link_id, write=False))


@router.patch("/{link_id}")
def update_link(link_id: str, payload: LinkPatch, request: Request):
    """``{notes?, paused?}`` — whether notes files are written (a turned-off
    link's notes files leave the disk at the next round), and whether the
    link is paused (only a sync asked for runs it; resuming runs a round
    in the background)."""
    link = _mine(request, link_id)
    if payload.notes is not None:
        try:
            link = folder_links.set_notes(link_id, payload.notes) or link
        except folder_links.LinkError as e:
            raise HTTPException(status_code=400, detail=str(e))
    if payload.paused is not None:
        link = folder_links.set_paused(link_id, payload.paused) or link
        if not payload.paused:
            folder_links.run_in_background(link_id)
    return _info(link)


@router.post("/{link_id}/sync")
def sync_link(link_id: str, request: Request, wait: int = 0, full: int = 0, force: int = 0):
    """A round now: in the background, or inline with ``wait=1`` (the
    answer then carries the round's status). ``full=1`` writes every file
    again, ``force=1`` replaces files changed on disk."""
    link = _mine(request, link_id)
    if wait:
        folder_links.run_link(link_id, full=bool(full), force=bool(force))
        return _info(folder_links.get_link(link_id) or link)
    folder_links.run_in_background(link_id, full=bool(full), force=bool(force))
    return _info(link)


@router.delete("/{link_id}")
def delete_link(link_id: str, request: Request, remove_files: int = 0):
    """Forget the link. The directory stays as it is, or with
    ``remove_files=1`` what the rounds wrote is taken back (files changed
    on disk and files the rounds never wrote stay)."""
    _mine(request, link_id)
    folder_links.delete_link(link_id, remove_files=bool(remove_files))
    return {"ok": True}
