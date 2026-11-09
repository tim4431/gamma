"""Synced vaults and PDFs read where they are (docs/dev/synced_vaults.md
"The routes"): the folders of a workspace the desktop
app's own server keeps as directories on this computer and the pages that
follow a PDF file on it (gamma/folder_links.py), of its own workspaces or of
another Gamma server reached with a token of it, and the PDFs opened from
this computer (gamma/linked_files.py). Only that server answers
(``config.folder_links_enabled``); any other answers 404. A signed-in
account's session only: an integration token is refused as for backups, and
so is the guest. Making, changing, syncing or removing a link of this
server's workspace takes the editor role; listing, any member's; and the
account that made a link sees it, pauses it and removes it whatever its role
there now, none included (``_kept``). A link with
a remote source is the account's own: it alone sees and changes it. The routes
that take a path are the desktop app's alone: they also take the shell's
secret, when it has one (``_from_shell``), for a page of Gamma never names a
path, the shell's native dialog does. A page's own routes take no path and
answer the page."""

import secrets
from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from .. import config, folder_links, linked_files, workspaces
from ..auth import require_personal_user_id, require_ws, ws_role
from ..markdown_import import MAX_MARKDOWN_BYTES
from ..ops import OpError

router = APIRouter(prefix="/api/folder-links", tags=["folder-links"])

SIGN_IN = "Sign in with an account to sync with this computer."
ELSEWHERE = ("Synced vaults are kept by the Gamma desktop app: open this server in it and use a folder's "
             "“Sync with this computer…” menu item or the sync panel's “Add a synced vault…”.")
SHELL_HEADER = "x-gamma-shell"


class LinkCreate(BaseModel):
    kind: Literal["folder", "file"] = "folder"
    folder: str = Field(default="", max_length=64)   # a folder link's folder: a folder block id, or "root"
    page: str = Field(default="", max_length=64)     # a file link's page
    path: str = Field(min_length=1, max_length=400)  # the directory's, or the PDF's, full path on this computer
    notes: bool = True
    pull: bool = True                                # Gamma's changes are written to the directory
    # What a folder link sends back: "off" | "pdfs" | "everything" (a file
    # link always sends its PDF).
    push: str = "off"
    new_folder: bool = False   # made from the directory: a new folder named after it
    adopt: bool = False        # a directory holding a vault marker no link holds keeps it
    # A folder or page on another Gamma server: its address, a token of it
    # (read access is enough for a folder that does not push), that token's
    # id for whoever revokes it later, and the token's workspace when the
    # caller wants it checked.
    remote_url: str = Field(default="", max_length=400)
    token: str = Field(default="", max_length=400)
    token_id: str = Field(default="", max_length=120)
    workspace: str = Field(default="", max_length=64)


class LinkPatch(BaseModel):
    notes: bool | None = None
    paused: bool | None = None
    pull: bool | None = None
    push: str | None = None
    # A new token of the other server for a link to one, kept in place of the
    # link's own once that server takes it (one that may write to push).
    token: str = Field(default="", max_length=400)
    token_id: str = Field(default="", max_length=120)


class OpenFile(BaseModel):
    path: str = Field(min_length=1, max_length=400)   # a PDF's full path on this computer, from the shell's dialog


class Preview(BaseModel):
    path: str = Field(min_length=1, max_length=400)   # the directory, or the PDF, the shell's dialog picked
    folder: str = Field(default="", max_length=64)    # the folder it would be synced with ("" for a new one)
    kind: Literal["folder", "file"] = "folder"


class Relink(BaseModel):
    path: str = Field(min_length=1, max_length=400)   # where the vault's directory, or its file, is now


class AddToLibrary(BaseModel):
    folder: str = Field(default="", max_length=64)    # a folder id to file the page in; "" the library's top
    keep_pushing: bool = False                        # the file goes on sending its new versions: a vault of one file
    workspace: str = Field(default="", max_length=64)  # another workspace of this server to take it to; "" its own


class Carried(BaseModel):
    digest: str = Field(min_length=1, max_length=64)  # GET .../carried's answer, read before the carry's export


class Resolution(BaseModel):
    choice: Literal["keep", "mine", "theirs"]


class NoteText(BaseModel):
    text: str = Field(max_length=MAX_MARKDOWN_BYTES)  # the whole file, from Gamma's file editor
    hash: str = Field(min_length=1, max_length=64)    # the file's hash when the editor read it (GET .../file)


def _account(request: Request) -> str:
    """The signed-in account, on the one server that keeps links."""
    if not config.folder_links_enabled():
        raise HTTPException(status_code=404, detail=ELSEWHERE)
    return require_personal_user_id(request, SIGN_IN)


def _from_shell(request: Request) -> str:
    """``_account`` for a route that takes a path: when the app started this
    server with a secret (``config.shell_secret``), a request without it is
    a page speaking for itself, 403."""
    user_id = _account(request)
    secret = config.shell_secret()
    if secret and not secrets.compare_digest(request.headers.get(SHELL_HEADER, "").encode(), secret.encode()):
        raise HTTPException(status_code=403, detail="Only the desktop app itself may name a path on this computer.")
    return user_id


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


def _kept(request: Request, link_id: str, write: bool = True) -> dict:
    """``_mine``, or the link to the account that made it whatever its role
    in the workspace now, made a viewer or no member at all: the directory is
    on that account's computer, so it may always see where, pause the vault
    and remove it. What the vault holds of the workspace (its conflicts) is
    ``_mine``'s alone."""
    link = folder_links.get_link(link_id)
    if link is not None and link["created_by"] == _account(request):
        return link
    return _mine(request, link_id, write)


def _made(request: Request, link_id: str) -> dict:
    """``_kept`` for what only the account that made the link may do: change
    what its disk sends or takes (``push``, ``pull``, ``notes``), force a
    round either way or past its guard, relink or remove it. The directory is
    on that account's computer: another editor of the workspace edits the
    pages, never what reaches that disk or leaves it."""
    link = _kept(request, link_id)
    if link["created_by"] != _account(request):
        raise HTTPException(status_code=403, detail="Only the account that made this link may do that: its files are "
                                                    "on that account's computer.")
    return link


def _info(link: dict, can_act: bool = True) -> dict:
    """The link as the API answers it: its direction in words, of a file
    link's state only the page's title (and never a token: a link is read
    here without it). For a member who cannot act on it (``_acts``) no place
    on this computer: no path, and of its status no text a round wrote,
    which may name one."""
    info = {k: v for k, v in link.items() if k != "state"}
    info.update(dest=str(folder_links.dest_of(link)), pull=bool(link["pull"]), push=folder_links.push_word(link["push"]))
    if link["kind"] == "file":
        info["title"] = link["state"].get("title", "")
    if not can_act:
        status = dict(link["status"])
        for key, said in (("last_error", "The last round did not finish."), ("stopped", "The vault stopped."),
                          ("push_off", "Push was turned off when the vault was taken onto synced vaults.")):
            if status.get(key):
                status[key] = said
        info.update(path="", dest="", status=status)
    return info


def _acts(link: dict, user_id: str) -> bool:
    """Whether the account may act on the link, so read where its files lie:
    its maker, or an editor of its workspace when it is this server's."""
    if link["created_by"] == user_id:
        return True
    return not link["remote_url"] and workspaces.at_least(workspaces.role_of(link["workspace_id"], user_id), "editor")


def _refused(e: folder_links.LinkError, status: int = 400) -> HTTPException:
    return HTTPException(status_code=status, detail=str(e))


@router.get("")
def list_links(request: Request):
    """``{links: [{id, kind, workspace_id, folder_id, page_id, path, notes,
    pull (a bool), push ("off" | "pdfs" | "everything"), created_at, cursor,
    status, dest, remote_url, token_id, title?}]}``
    — every link the account may see, whatever workspace the request names:
    those of this server's workspaces it has a role in (``remote_url``
    empty), and its own to another server (``remote_url`` the other server,
    ``workspace_id`` the workspace there). A folder link (``kind``
    ``folder``) has its ``folder_id``, a file link (``file``) its
    ``page_id`` and the page's ``title``; ``dest`` is the directory or the
    file. The desktop app's sync panel lists them all. A folder vault's
    ``status``: ``{running, started_at, last_sync, last_error, failed_at?,
    counts, kept, untracked?, refused?, capped?, held?, stopped?, offline?,
    shared?, upgraded_at?, push_off?, paused_at?, folder_path}``
    (``folder_links._vault_round``), ``shared`` the pages filed in it that a
    vault beside it, made before it, keeps (``[{page, title}]``)."""
    user_id = _account(request)
    return {"links": [_info(link, _acts(link, user_id)) for link in folder_links.visible_links(user_id)]}


@router.post("", status_code=201)
def create_link(payload: LinkCreate, request: Request):
    """``{folder, path, notes?, pull?, push?}`` → a folder link, ``{path,
    new_folder: true, pull?, push?, adopt?}`` → a vault made from the
    directory into a new folder named after it, ``{kind: "file", page, path}``
    → a file link; its first round runs in the background. ``push`` is a
    word (``folder_links.push_value``). With ``remote_url`` and ``token`` the
    folder or page is one of another Gamma server's, reached there with the
    token (``workspace``, when given, must be the token's; a file link, or a
    folder link that pushes, takes a token that may write). 400 for a folder
    or page that does not exist, a path that is not a full one (of a PDF
    that is there, for a file link) or another link's, a directory holding
    another folder's files or a vault marker not to be adopted, or one of
    ``folder_links.check_vault``'s refusals, or a server that refuses the
    token."""
    user_id = _from_shell(request)
    try:
        if payload.remote_url and (payload.new_folder or payload.adopt):
            raise folder_links.LinkError("A vault of another server is made from its folder.")
        if payload.remote_url and payload.kind == "file":
            link = folder_links.create_remote_file_link(payload.remote_url, payload.token, payload.token_id,
                                                        payload.workspace, payload.page, payload.path, user_id)
        elif payload.remote_url:
            link = folder_links.create_remote_link(payload.remote_url, payload.token, payload.token_id, payload.workspace,
                                                   payload.folder, payload.path, payload.notes, user_id, payload.push,
                                                   payload.pull)
        elif payload.kind == "file":
            link = folder_links.create_file_link(require_ws(request, write=True), payload.page, payload.path, user_id)
        else:
            ws = require_ws(request, write=True)
            link = folder_links.create_link(ws, payload.folder, payload.path, payload.notes, user_id, payload.push,
                                            payload.pull, new_folder=payload.new_folder, adopt=payload.adopt)
    except folder_links.LinkError as e:
        raise _refused(e)
    folder_links.run_in_background(link["id"])
    return _info(link)


@router.post("/preview")
def preview(payload: Preview, request: Request):
    """What making a vault of ``{path, folder?, kind?}`` would find and write,
    before anything is written: ``{path, notes, pdfs, pictures, untracked,
    empty, marker: null | {id, link_id}, refusal, pages, will_write}``
    (``folder_links.preview``). 400 for a path no vault can have, or a
    directory that cannot be read."""
    _from_shell(request)
    ws = require_ws(request, write=True)
    try:
        return folder_links.preview(ws, payload.path, payload.folder, payload.kind)
    except folder_links.LinkError as e:
        raise _refused(e)


@router.post("/hint")
def hint(request: Request):
    """A window of the app got the focus: the links' tick runs now, in the
    background (``folder_links.hint``). → ``{ok}``."""
    _account(request)
    folder_links.hint()
    return {"ok": True}


@router.post("/open")
def open_file(payload: OpenFile, request: Request):
    """A PDF of this computer opened where it is (``linked_files.open_pdf``):
    ``{path}`` → ``{workspace, page, created, title}``, the page's id and
    the workspace it is in, which is the request's unless the file belongs to
    a vault of another. 400 for a path that is not a full one, a file that
    is no PDF, is not there or cannot be read, one inside the data directory,
    or a clone's workspace."""
    user_id = _from_shell(request)
    ws = require_ws(request, write=True)
    try:
        opened = linked_files.open_pdf(ws, payload.path, user_id)
    except folder_links.LinkError as e:
        raise _refused(e)
    return {"workspace": opened["workspace"], "page": opened["page"]["id"], "created": opened["created"],
            "title": opened["page"]["content"]}


@router.post("/opened/{page_id}/add")
def add_opened(page_id: str, payload: AddToLibrary, request: Request):
    """An opened PDF goes into the library of its workspace, or with
    ``workspace`` of another of this server the account may edit, there with
    its notes, highlights and chats, the opened page gone
    (``linked_files.add_to_library``): ``{folder?, keep_pushing?,
    workspace?}`` → the page, ``folder`` one of the workspace it goes to. 400
    for a page that reads no file here, a folder that does not exist, a file
    that is gone or changed, a workspace that takes no such page, or
    ``keep_pushing`` or ``workspace`` asked by any account but the one that
    opened the file; 507 past the quota."""
    user_id = _account(request)
    ws = require_ws(request, write=True)
    try:
        return linked_files.add_to_library(ws, page_id, payload.folder, payload.keep_pushing, user_id,
                                           workspace=payload.workspace)
    except folder_links.LinkError as e:
        raise _refused(e)


@router.get("/opened/{page_id}/carried")
def carried(page_id: str, request: Request):
    """What carrying an opened PDF to another server takes of it, the page
    and the pages inside it (``linked_files.carry_digest``): ``{digest,
    pages}``, their digest and their ids, the page first. The desktop app
    reads it before its export, looks for each page there first, and hands
    the digest back to ``POST`` once the page is there. 400 for a page that
    reads no file here, or any account but the one that opened the file."""
    user_id = _account(request)
    ws = require_ws(request, write=True)
    try:
        digest, pages = linked_files.carry_digest(ws, page_id, user_id)
    except folder_links.LinkError as e:
        raise _refused(e)
    return {"digest": digest, "pages": pages}


@router.post("/opened/{page_id}/carried")
def let_go(page_id: str, payload: Carried, request: Request):
    """An opened PDF the desktop app carried to another server let go here
    (``linked_files.let_go``): ``{digest}``, the ``GET``'s answer from
    before the export → ``{ok}``, the page and the pages inside it in
    Recently deleted, and the file no page's here. 409
    (``linked_files.CHANGED_SINCE``), and nothing touched, when they changed
    since that digest; 400 as for the ``GET``."""
    user_id = _account(request)
    ws = require_ws(request, write=True)
    try:
        if not linked_files.let_go(ws, page_id, payload.digest, user_id):
            raise HTTPException(status_code=409, detail=linked_files.CHANGED_SINCE)
    except folder_links.LinkError as e:
        raise _refused(e)
    return {"ok": True}


@router.post("/opened/{page_id}/locate")
def locate_opened(page_id: str, payload: OpenFile, request: Request):
    """The file of an opened PDF found again (``linked_files.locate``):
    ``{path}`` → the page. 400 as for ``/open``, or for a page that reads
    no file here."""
    user_id = _from_shell(request)
    ws = require_ws(request, write=True)
    try:
        return linked_files.locate(ws, page_id, payload.path, user_id)
    except folder_links.LinkError as e:
        raise _refused(e)


@router.get("/page/{page_id}")
def page_state(page_id: str, request: Request):
    """How a page stands with this computer: ``{vault, reader}``. ``vault``
    is ``{id, name, scope, path, pull, push, problem, conflicts, paused}``
    for a page a synced vault keeps (``folder_links.page_vault``), else
    null; ``reader`` is ``{path, missing, mine, relocation_error?}`` for a
    PDF read where it is, else null, ``mine`` whether the asking account
    opened the file (what keeps it pushing or takes it elsewhere is that
    account's alone). A ``path`` is empty for a member who cannot act on it,
    but the vault's maker: where the file lies is the reading of whoever made
    the vault or opened the file."""
    user_id = _account(request)
    ws = require_ws(request)
    can_act = workspaces.at_least(ws_role(request), "editor")
    reader = linked_files.reader(ws, page_id, user_id)
    if reader and not can_act:
        reader["path"] = ""
    return {"vault": folder_links.page_vault(ws, page_id, can_act=can_act, user_id=user_id), "reader": reader}


@router.get("/page/{page_id}/file")
def page_file(page_id: str, request: Request):
    """The page's notes file in its vault, as it is on disk: ``{text, hash,
    path}``, for Gamma's file editor; an editor's, and of a vault that does
    not push everything its maker's alone: what that vault keeps off Gamma
    reaches no other account. 404 for a page with no notes file, 400 for one
    that is not UTF-8 text."""
    user_id = _account(request)
    ws = require_ws(request, write=True)
    link = folder_links.vault_of_page(ws, page_id)
    if link and link["push"] != folder_links.PUSH_ALL and link["created_by"] != user_id:
        raise HTTPException(status_code=403, detail="This vault does not push its notes: its files are read on the "
                                                    "computer of the account that made it.")
    try:
        found = folder_links.read_note(ws, page_id)
    except folder_links.LinkError as e:
        raise _refused(e)
    if found is None:
        raise HTTPException(status_code=404, detail="This page has no notes file on this computer.")
    return found


@router.put("/page/{page_id}/file")
def write_page_file(page_id: str, payload: NoteText, request: Request):
    """``{text, hash}``: the page's notes file written whole, as a person's
    editor saves it (``folder_links.write_note``), and the page's round runs
    soon after → ``{hash, path}``. 409 with ``hash``, what the file holds
    now, when it changed since ``hash``; 400 for a text that is no note
    Gamma holds, or a vault that does not push notes; 404 for a page with no
    notes file."""
    _account(request)
    ws = require_ws(request, write=True)
    try:
        written = folder_links.write_note(ws, page_id, payload.text, payload.hash)
    except folder_links.NoteMoved as e:
        return JSONResponse({"detail": str(e), "hash": e.hash}, status_code=409)
    except folder_links.LinkError as e:
        raise _refused(e)
    if written is None:
        raise HTTPException(status_code=404, detail="This page has no notes file on this computer.")
    return written


@router.get("/{link_id}")
def get_link(link_id: str, request: Request):
    link = _kept(request, link_id, write=False)
    return _info(link, _acts(link, _account(request)))


@router.patch("/{link_id}")
def update_link(link_id: str, payload: LinkPatch, request: Request):
    """``{notes?, pull?, push?, token?, token_id?, paused?}`` — a folder
    link's whether notes files are written (only a folder kept before with
    its PDFs alone has them off, and turns them on; ``folder_links.set_notes``),
    whether Gamma's changes are written (``pull``) and what goes back, as a
    word (``folder_links.push_value``; on another server, with a token of it
    that may write, ``token`` or the link's own); ``token`` alone, a new token
    of the other server in place of the link's (``folder_links.set_token``:
    one revoked there is replaced so); and whether the link is paused (only a
    sync asked for runs it; resuming runs a round in the background). 400 for
    notes, pull or push on a file link, notes turned off, a change
    ``folder_links.check_vault`` refuses, a token for a link of this server,
    one that other server refuses or that opens another workspace, or one
    that may not write for a link that pushes; 403 for any of them but by the
    link's maker (``_made``), who may pause it whatever its role now
    (``_kept``)."""
    changes = payload.notes is not None or payload.push is not None or payload.pull is not None or bool(payload.token)
    link = _made(request, link_id) if changes else _kept(request, link_id)
    try:
        if payload.notes is not None:
            link = folder_links.set_notes(link_id, payload.notes) or link
        if payload.push is not None:
            link = folder_links.set_push(link_id, payload.push, payload.token, payload.token_id) or link
        elif payload.token:
            link = folder_links.set_token(link_id, payload.token, payload.token_id) or link
        if payload.pull is not None:
            link = folder_links.set_pull(link_id, payload.pull) or link
    except folder_links.LinkError as e:
        raise _refused(e)
    if payload.paused is not None:
        link = folder_links.set_paused(link_id, payload.paused) or link
        if not payload.paused:
            folder_links.run_in_background(link_id)
    return _info(link)


@router.post("/{link_id}/sync")
def sync_link(link_id: str, request: Request, wait: int = 0, force: Literal["pull", "push"] | None = None,
              direction: Literal["both", "pull", "push"] = "both", confirm: int = 0):
    """A round now: in the background, or inline with ``wait=1`` (the
    answer then carries the round's status). ``direction`` narrows it to
    ``pull`` or ``push``; ``force=pull`` makes the files Gamma's, and for a
    vault ``force=push`` makes the pages the files (both counted and
    confirmed by the app first), refused where pushing everything is
    (``folder_links.check_vault``) and for a followed file, which always
    pushes; ``confirm=1`` takes a vault past its guard (``status.held``). A
    force or a confirmation is the link's maker's (``_made``), 403 for anyone
    else."""
    link = _made(request, link_id) if force or confirm else _kept(request, link_id)
    if force == "push" and not folder_links.engine(link):
        raise HTTPException(status_code=400, detail="A vault of one file always pushes: there is nothing to force.")
    if force == "push":
        try:
            folder_links.check_vault(folder_links.forced(link, force), earlier=True)
        except folder_links.LinkError as e:
            raise _refused(e)
    kw = {"force": force or "", "direction": direction, "confirm": bool(confirm)}
    if wait:
        folder_links.run_link(link_id, wait=True, **kw)
        return _info(folder_links.get_link(link_id) or link)
    folder_links.run_in_background(link_id, **kw)
    return _info(link)


@router.post("/{link_id}/relink")
def relink(link_id: str, payload: Relink, request: Request):
    """``{path}``: the vault found at another place (its directory moved, or
    its disk mounted elsewhere), whose marker must be this vault's; a
    followed file's PDF that moved. → the link; a round follows. 400 for a
    place that is not the link's, or one ``folder_links.check_vault``
    refuses; 403 but for the link's maker."""
    _from_shell(request)
    _made(request, link_id)
    try:
        link = folder_links.relink(link_id, payload.path)
    except folder_links.LinkError as e:
        raise _refused(e)
    if link is None:
        raise HTTPException(status_code=404, detail="no such link")
    return _info(link)


@router.get("/{link_id}/removal")
def removal(link_id: str, request: Request):
    """What removing the link would do, to say before it is asked: ``{pages,
    in_place, bytes}`` — its pages, which stay, and the PDFs read in place
    that are stored first, with their size."""
    return folder_links.removal(_kept(request, link_id))


@router.delete("/{link_id}")
def delete_link(link_id: str, request: Request, files: Literal["keep", "trash"] = "keep"):
    """Remove the link; its pages stay, the PDFs a vault read in place
    stored first. A folder's directory stays as it is with ``files=keep``
    (but for a vault's marker); with ``files=trash`` the files its rounds
    made move to its trash (``.gamma/trash``: files changed on disk and
    files the rounds never wrote stay). A followed file is never touched.
    409 when a PDF read in place could not be stored, or another program
    holds the state file a folder kept before left: nothing was removed;
    403 but for the link's maker."""
    _made(request, link_id)
    try:
        folder_links.delete_link(link_id, files=files)
    except folder_links.LinkError as e:
        raise _refused(e, 409)
    return {"ok": True}


@router.get("/{link_id}/conflicts")
def list_conflicts(link_id: str, request: Request, page: str = ""):
    """``{conflicts: [{id, page_id, page_title, block_id, kind, mine, theirs,
    result, at, resolved, base}]}`` — the open clashes the vault's files won
    (kind ``file_won``: ``theirs`` the file's text, which the block took,
    ``mine`` Gamma's), one page's with ``?page=``."""
    return {"conflicts": folder_links.conflicts(_mine(request, link_id, write=False), page)}


@router.post("/{link_id}/conflicts/{conflict_id}")
def resolve_conflict(link_id: str, conflict_id: int, payload: Resolution, request: Request):
    """``{choice: keep | mine | theirs}``: ``mine`` writes Gamma's text back
    into the block, which the next pull writes to the file, ``theirs`` the
    file's, then the conflict is resolved (``folder_links.resolve``); a write
    the block refuses (409) leaves it open. 404 for a conflict that is not
    the vault's."""
    link = _mine(request, link_id)
    try:
        out = folder_links.resolve(link, conflict_id, payload.choice)
    except OpError as e:
        raise HTTPException(status_code=409, detail=f"the text could not be written: {e}")
    if not out:
        raise HTTPException(status_code=404, detail="no such conflict")
    return out
