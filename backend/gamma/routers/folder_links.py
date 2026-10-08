"""Folders on disk (docs/dev/folder_sync.md "Links kept by the server"):
the links of the request's workspace — a folder kept as a directory under
the server's folders root (gamma/folder_links.py) — their status, a round
now, and their removal. A signed-in account's session only: an integration
token is refused as for backups, and so is the guest. Making, changing,
syncing or removing a link takes the editor role; listing, any member's."""

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from .. import folder_links
from ..auth import require_personal_user_id, require_ws

router = APIRouter(prefix="/api/folder-links", tags=["folder-links"])


class LinkCreate(BaseModel):
    folder: str = Field(min_length=1, max_length=64)   # a folder block id, or "root"
    path: str = Field(default="", max_length=240)      # below the folders root; the folder's own path when empty
    notes: bool = True


class LinkPatch(BaseModel):
    notes: bool | None = None


def _member(request: Request) -> str:
    require_personal_user_id(request, "Sign in with an account to keep folders on disk.")
    return require_ws(request)


def _writer(request: Request) -> tuple[str, str]:
    user_id = require_personal_user_id(request, "Sign in with an account to keep folders on disk.")
    return require_ws(request, write=True), user_id


def _mine(request: Request, link_id: str, write: bool = True) -> dict:
    ws = _writer(request)[0] if write else _member(request)
    link = folder_links.get_link(link_id)
    if link is None or link["workspace_id"] != ws:
        raise HTTPException(status_code=404, detail="no such link")
    return link


def _info(link: dict) -> dict:
    return {**link, "dest": str(folder_links.dest_of(link))}


@router.get("")
def list_links(request: Request):
    """``{links: [{id, folder_id, path, notes, created_at, cursor, status,
    dest}], root, anywhere}`` — the workspace's links, the folders root they
    are written under, and whether a link may name any directory of the
    machine instead (the desktop app's local server)."""
    ws = _member(request)
    return {"links": [_info(link) for link in folder_links.list_links(ws)], "root": str(folder_links.root_dir()),
            "anywhere": folder_links.anywhere()}


@router.post("", status_code=201)
def create_link(payload: LinkCreate, request: Request):
    """``{folder, path?, notes?}`` → the link; its first round runs in the
    background. 400 for a folder that does not exist, a path another link
    writes, a directory holding another folder's files, or an unwritable
    root."""
    ws, user_id = _writer(request)
    try:
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
    """``{notes?}`` — whether notes files are written (a turned-off link's
    notes files leave the disk at the next round)."""
    link = _mine(request, link_id)
    if payload.notes is not None:
        try:
            link = folder_links.set_notes(link_id, payload.notes) or link
        except folder_links.LinkError as e:
            raise HTTPException(status_code=400, detail=str(e))
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
