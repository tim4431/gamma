"""Synced vaults and files read where they are (docs/dev/synced_vaults.md
"The routes"): the folders of a workspace the desktop
app's own server keeps as directories on this computer and the pages that
follow a PDF file on it (gamma/folder_links.py), of its own workspaces or of
another Gamma server reached with a token of it, the files opened from
this computer (gamma/linked_files.py), and what a path of it is to Gamma
(``POST /where``, for the desktop app's *Open with Gamma*). Only that server answers
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
answer the page. A work folder's tracking dialog names the vault by its id
and its files by their paths in it, the vault maker's alone (``_chooser``:
the listing names files Gamma never reads)."""

import secrets
from typing import Literal

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from .. import config, folder_links, linked_files, remote_servers, workspaces
from ..auth import require_personal_user_id, require_ws, ws_role
from ..documents.model import page_document
from ..logbuf import log
from ..markdown_import import MAX_MARKDOWN_BYTES
from ..ops import OpError
from ..vault import disk as vault_disk
from ..work_folders import selection

router = APIRouter(prefix="/api/folder-links", tags=["folder-links"])

SIGN_IN = "Sign in with an account to sync with this computer."
MAKER_ONLY = "Only the account that made this link may do that: its files are on that account's computer."
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
    choose: bool = False       # a work folder whose files its tracking dialog chooses first: no round before
    library: bool = False      # a work folder's switch: the pages of its files are library documents too
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
    library: bool | None = None
    # A work folder's agent on this computer: "codex" or "claude" on, "" off
    # (docs/dev/local_agents.md).
    agent: str | None = Field(default=None, max_length=32)
    # What that agent may run: "never", "ask" (its chat's turns may ask in the chat: a work folder of this
    # server) or "computer" (a work folder of another server: they ask in the desktop app's window).
    agent_runs: str | None = Field(default=None, max_length=16)
    # A new token of the other server for a link to one, kept in place of the
    # link's own once that server takes it (one that may write to push).
    token: str = Field(default="", max_length=400)
    token_id: str = Field(default="", max_length=120)


class OpenFile(BaseModel):
    path: str = Field(min_length=1, max_length=400)   # a file's full path on this computer, from the shell


class WherePath(BaseModel):
    path: str = Field(min_length=1, max_length=400)   # a file's or a directory's full path, from the file manager


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


class SelectionChange(BaseModel):
    # One tick or untick of the tracking dialog: a file, or a directory ("build/", every file below it), by its
    # path in the vault; or every file of a format ("latex": the LaTeX and BibTeX sources).
    path: str | None = Field(default=None, min_length=1, max_length=1000)
    format: Literal["pdf", "markdown", "latex", "image"] | None = None
    on: bool


class Selection(BaseModel):
    base: Literal["file", "preset"] = "file"   # what the vault's .gammaignore tracks now, or a new work folder's preset
    changes: list[SelectionChange] = Field(default_factory=list, max_length=50_000)


class SelectionPreview(BaseModel):
    selection: Selection


class SelectionSave(BaseModel):
    selection: Selection
    base_hash: str = Field(default="", max_length=64)   # the hash of the .gammaignore the dialog read ("" for none)
    library: bool | None = None                         # the work folder's library switch, set with the save


def _account(request: Request) -> str:
    """The signed-in account, on the one server that keeps links."""
    if not config.folder_links_enabled():
        raise HTTPException(status_code=404, detail=ELSEWHERE)
    return require_personal_user_id(request, SIGN_IN)


def _from_shell(request: Request, refused: str = "Only the desktop app itself may name a path on this computer.") -> str:
    """``_account`` for a route that takes a path: when the app started this
    server with a secret (``config.shell_secret``), a request without it is
    a page speaking for itself, 403 with ``refused``."""
    user_id = _account(request)
    secret = config.shell_secret()
    if secret and not secrets.compare_digest(request.headers.get(SHELL_HEADER, "").encode(), secret.encode()):
        raise HTTPException(status_code=403, detail=refused)
    return user_id


def _shell_strict(request: Request, refused: str) -> str:
    """``_from_shell`` for a route that sends this computer's tokens on or
    decides what it trusts (routers/remote_servers.py): a server started
    with no secret refuses it too (403), so it never passes unchecked."""
    user_id = _account(request)
    secret = config.shell_secret()
    if not secret or not secrets.compare_digest(request.headers.get(SHELL_HEADER, "").encode(), secret.encode()):
        raise HTTPException(status_code=403, detail=refused)
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
        raise HTTPException(status_code=403, detail=MAKER_ONLY)
    return link


def _chooser(request: Request, link_id: str) -> dict:
    """The link, for the tracking dialog's routes: a vault of a folder of this
    server, or a work folder of another server (``folder_links.work_vault``),
    paused or choosing too, answered to the account that made it alone, as
    ``_made`` (its listing names files Gamma never reads, on that account's
    computer): another editor of its workspace 403, anyone else 404, and
    404 for a file's vault and a kept folder of another server."""
    user_id = _account(request)
    link = folder_links.get_link(link_id)
    if (link is None or link["kind"] != "folder" or (link["remote_url"] and not folder_links.work_vault(link))
            or not _acts(link, user_id)):
        raise HTTPException(status_code=404, detail="no such link")
    if link["created_by"] != user_id:
        raise HTTPException(status_code=403, detail=MAKER_ONLY)
    return link


def _not_chosen(e: selection.SelectionError):
    """A dialog route's refusal, with the hash of the ``.gammaignore`` there
    now for one changed since the dialog read it."""
    if e.hash is not None:
        return JSONResponse({"detail": e.detail, "hash": e.hash}, status_code=e.status)
    raise HTTPException(status_code=e.status, detail=e.detail)


def _info(link: dict, can_act: bool = True, agents: dict | None = None) -> dict:
    """The link as the API answers it: its direction in words, of a file
    link's state only the page's title, whether it keeps a work folder, of
    this server or of another (``work``: ``folder_links.work_vault``), and
    whether a work folder waits for its files to be chosen (``choosing``;
    and never a token: a link is read here without it), and a work folder's
    ``agent`` on this computer
    (``local_agents.info``, null for none; ``agents`` a listing's, asked
    once for every link). For a member who cannot act on it (``_acts``) no
    place on this computer: no path, of its status no text a round wrote,
    which may name one, and no detail of its agent's run."""
    from ..local_agents import info as agent_info  # local: the runner it asks imports the feedback modules

    info = {k: v for k, v in link.items() if k not in ("state", "token")}
    info.update(dest=str(folder_links.dest_of(link)), pull=bool(link["pull"]), push=folder_links.push_word(link["push"]),
                choosing=folder_links.choosing(link), work=folder_links.work_vault(link),
                plain=remote_servers.plain(link["remote_url"]))
    info["agent"] = agents[link["id"]] if agents is not None else agent_info.agent_info(link, can_act=can_act)
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
    pull (a bool), push ("off" | "pdfs" | "everything"), library, choosing,
    work, created_at, cursor, status, dest, remote_url, plain, token_id, title?}]}``
    — every link the account may see, whatever workspace the request names:
    those of this server's workspaces it has a role in (``remote_url``
    empty), and its own to another server (``remote_url`` the other server,
    ``workspace_id`` the workspace there; ``plain`` whether that address is
    plain http, ``remote_servers.plain``, "" | "lan" | "name", no DNS
    asked: "" reached securely, which Ask on this computer needs). A folder
    link (``kind`` ``folder``) has its ``folder_id``, a file link (``file``) its
    ``page_id`` and the page's ``title``; ``dest`` is the directory or the
    file. ``work`` says the link keeps a work folder, of this server or of
    another (``folder_links.work_vault``), ``library`` is a work folder's
    switch, ``choosing`` whether its
    files wait for its tracking dialog, ``mine`` whether the account made
    the link (the tracking dialog's routes are its maker's alone), ``agent``
    a work folder's agent on this computer, ``{name, label, ready, problem,
    running: {kind: "send" | "chat", request_id (null for a chat turn),
    detail} | null}`` or null (``local_agents.info``:
    nothing is run to know it). The desktop app's sync panel lists
    them all. A folder vault's ``status``: ``{running, started_at,
    last_sync, last_error, failed_at?, counts, kept, untracked?, refused?,
    capped?, held?, stopped?, offline?, shared?, upgraded_at?,
    work_folder_at?, push_off?, paused_at?, error_kind?, move_to?, upgrade?,
    cert?, blocked?, folder_path}`` (``folder_links._vault_round``),
    ``shared`` the pages filed in it that a vault beside it, made before
    it, keeps (``[{page, title}]``); ``error_kind`` and the rest what the
    last failure of a vault of another server was
    (``gamma_sync.status_fields``)."""
    from ..local_agents import info as agent_info  # local: the runner it asks imports the feedback modules

    user_id = _account(request)
    links = folder_links.visible_links(user_id)
    acts = {link["id"]: _acts(link, user_id) for link in links}
    agents = agent_info.listing(links, can_act=lambda link: acts[link["id"]])
    return {"links": [{**_info(link, acts[link["id"]], agents), "mine": link["created_by"] == user_id}
                      for link in links]}


@router.post("", status_code=201)
def create_link(payload: LinkCreate, request: Request):
    """``{folder, path, notes?, pull?, push?}`` → a folder link, ``{path,
    new_folder: true, pull?, push?, adopt?}`` → a vault made from the
    directory into a new folder named after it, ``{kind: "file", page, path}``
    → a file link; its first round runs in the background. ``push`` is a
    word (``folder_links.push_value``). A folder of this server that pushes
    everything is a work folder (``library``: its switch); with ``choose``
    it is made paused, choosing, and runs no round until its tracking
    dialog saves (``PUT /{id}/selection``). With ``remote_url`` and ``token`` the
    folder or page is one of another Gamma server's, reached there with the
    token (``workspace``, when given, must be the token's; a file link, or a
    folder link that pushes, takes a token that may write); with ``choose``
    too it is a work folder there (``folder_links.create_remote_link``: a
    folder that is not the library's top, pushing everything and pulling,
    on a server whose whoami says ``work_folders``, into a folder that is
    empty once marked), made paused and choosing as one of this server.
    400 for a folder or page that does not exist, a path that is not a full
    one (of a PDF that is there, for a file link) or another link's, a
    directory holding another folder's files or a vault marker not to be
    adopted, or one of ``folder_links.check_vault``'s refusals, or a server
    that refuses the token, or plain http to a host on the internet; for a
    work folder of another server, 409 for its folder there as it stands (a
    work folder already, inside or around one, not empty, a mark that server
    refuses: a clone, a Zotero link) and 502 for a server that does not
    answer. For another server, 409 too for one that redirects, and 502 for
    a certificate not trusted here, no HTTPS, or a sign-in or proxy in the
    way, the answer carrying beside its ``detail`` what the status would
    (``error_kind``, ``move_to``, ``upgrade``, ``cert``, ``blocked``)."""
    user_id = _from_shell(request)
    try:
        if payload.remote_url and (payload.new_folder or payload.adopt):
            raise folder_links.LinkError("A vault of another server is made from its folder.")
        if payload.choose and payload.kind == "file":
            raise folder_links.LinkError(folder_links.CHOOSES_ONLY)
        if payload.remote_url and payload.kind == "file":
            link = folder_links.create_remote_file_link(payload.remote_url, payload.token, payload.token_id,
                                                        payload.workspace, payload.page, payload.path, user_id)
        elif payload.remote_url:
            link = folder_links.create_remote_link(payload.remote_url, payload.token, payload.token_id, payload.workspace,
                                                   payload.folder, payload.path, payload.notes, user_id, payload.push,
                                                   payload.pull, choose=payload.choose, library=payload.library)
        elif payload.kind == "file":
            link = folder_links.create_file_link(require_ws(request, write=True), payload.page, payload.path, user_id)
        else:
            ws = require_ws(request, write=True)
            link = folder_links.create_link(ws, payload.folder, payload.path, payload.notes, user_id, payload.push,
                                            payload.pull, new_folder=payload.new_folder, adopt=payload.adopt,
                                            choose=payload.choose, library=payload.library)
    except folder_links.LinkError as e:
        if e.fields:   # what the other server's failure was: the certificate it served, where it redirected
            return JSONResponse({"detail": str(e), **e.fields}, status_code=e.status)
        raise _refused(e, e.status)
    if not folder_links.choosing(link):
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


@router.post("/where")
def where(payload: WherePath, request: Request):
    """What a file or a directory of this computer is to Gamma, for the
    asking account (``folder_links.where_is``): ``{path}`` → ``{is: "vault"
    | "hidden" | "around" | "file_vault" | "none", …}``, read only (no
    round, no page, nothing written). The desktop app's *Open with Gamma*
    asks each local server, then goes by the ids it answers. No workspace:
    the answer names the workspace a vault is in. 400 for a path that is
    not a full one or names nothing; a place no vault may have is no 400
    but ``none``'s ``refusal``."""
    user_id = _from_shell(request)
    try:
        return folder_links.where_is(payload.path, user_id)
    except folder_links.LinkError as e:
        raise _refused(e)


@router.post("/open")
def open_file(payload: OpenFile, request: Request):
    """A file of this computer opened where it is
    (``linked_files.open_file``), of any format Gamma can show: ``{path}``
    → ``{workspace, page, created, title, format}``, the page's id, the
    workspace it is in, which is the request's unless the file belongs to a
    vault of another, and the format of the page's document (``pdf``,
    ``text`` (a Markdown file outside every vault among them), ``image``,
    ``audio``, ``video``; "" for a page with none, a work folder's note).
    400 for a path that is not a full one, a file that is not there or
    cannot be read, a program, a binary, a file of another format than PDF
    past the keep limit, a ``.pdf`` that is no PDF, one inside the data
    directory, or a clone's workspace; and inside a vault for a work folder
    whose files are not chosen yet, a file its scan never tracks, a work
    folder's file it does not track, a file with no page yet of a paused
    vault, a file whose page is in Recently deleted, and a file a vault of
    another server that the account made reads (``kept on <host>: open it
    there``)."""
    user_id = _from_shell(request)
    ws = require_ws(request, write=True)
    try:
        opened = linked_files.open_file(ws, payload.path, user_id)
    except folder_links.LinkError as e:
        raise _refused(e)
    view = page_document(opened["page"].get("properties"))
    return {"workspace": opened["workspace"], "page": opened["page"]["id"], "created": opened["created"],
            "title": opened["page"]["content"], "format": view["format"] if view else ""}


@router.post("/opened/{page_id}/add")
def add_opened(page_id: str, payload: AddToLibrary, request: Request):
    """An opened file goes into the library of its workspace, or with
    ``workspace`` of another of this server the account may edit, there with
    its notes, highlights and chats, the opened page gone
    (``linked_files.add_to_library``): ``{folder?, keep_pushing?,
    workspace?}`` → the page, ``folder`` one of the workspace it goes to. 400
    for a page that reads no file here, a Markdown file read as text (it
    joins the library as a note, dropped there), ``keep_pushing`` for a file
    that is no PDF, a folder that does not exist, a file that is gone or
    changed, a workspace that takes no such page, or ``keep_pushing`` or
    ``workspace`` asked by any account but the one that opened the file;
    507 past the quota."""
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
    reads no file here, one whose document is no PDF (``Only a PDF is
    carried to another server.``), or any account but the one that opened
    the file."""
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
    """The file of an opened page found again (``linked_files.locate``):
    ``{path}`` → the page. 400 as for ``/open``, for a file of another
    format than the page's document, or for a page that reads no file
    here."""
    user_id = _from_shell(request)
    ws = require_ws(request, write=True)
    try:
        return linked_files.locate(ws, page_id, payload.path, user_id)
    except folder_links.LinkError as e:
        raise _refused(e)


@router.get("/page/{page_id}")
def page_state(page_id: str, request: Request):
    """How a page stands with this computer: ``{vault, reader}``. ``vault``
    is ``{id, name, scope, path, pull, push, problem, conflicts, paused,
    work_folder, choosing, library, mine, page: {library, file_name}}`` for
    a page a synced vault keeps (``folder_links.page_vault``: ``mine``
    whether the asking account made the vault, ``page`` the page's own
    stamps as a work folder's file), else null; ``reader`` is ``{path,
    missing, mine, format, ext, relocation_error?}`` for a file read where
    it is, else null, ``mine`` whether the asking account opened the file
    (what keeps it pushing or takes it elsewhere is that account's alone),
    ``format`` and ``ext`` its document's. A ``path`` is empty for a member who cannot act on it,
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
    """``{notes?, pull?, push?, library?, token?, token_id?, paused?}`` — a
    folder link's whether notes files are written (only a folder kept before
    with its PDFs alone has them off, and turns them on;
    ``folder_links.set_notes``), whether Gamma's changes are written
    (``pull``) and what goes back, as a word (``folder_links.push_value``; on
    another server, with a token of it that may write, ``token`` or the
    link's own), and a work folder's library switch (``library``:
    ``folder_links.set_library``); ``token`` alone, a new token of the other
    server in place of the link's (``folder_links.set_token``: one revoked
    there is replaced so); and whether the link is paused (only a sync asked
    for runs it; resuming runs a round in the background). 400 for notes,
    pull, push or library on a file link, notes turned off, a change
    ``folder_links.check_vault`` refuses, a work folder's push lowered while
    some of its files are not in the library, a work folder resumed before
    its files are chosen, a token for a link of this server, one that other
    server refuses or that opens another workspace, or one that may not
    write for a link that pushes; 403 for any of them but by the link's
    maker (``_made``), who may pause it whatever its role now (``_kept``).
    ``agent`` is a work folder's agent on this computer
    (``folder_links.set_agent``; docs/dev/local_agents.md): ``codex`` or
    ``claude`` turns it on, which the desktop app asks with its shell's
    word (``_from_shell``: an agent that edits a directory is the
    computer's decision), and the answer's ``queued`` says how many waiting
    Sends it took; ``""`` turns it off, the maker's alone. A work folder of
    another server takes one too (step 4c: its Sends made there run here),
    once this computer is shown there (400 "Show this computer on NAS
    first."); its channel asks that server for the Sends that wait
    (``queued`` 0). 404 on a server that runs no agents, 400 for a vault
    that is no work folder, an agent Gamma does not know or one not ready
    (its problem). ``agent_runs`` is what that agent may run
    (``folder_links.set_agent_runs``): ``ask`` lets its chat's turns ask
    the person, which the desktop app asks with its shell's word (403
    without), for a work folder of this server only (409); ``computer``
    lets the turns of a work folder of another server ask the person in the
    desktop app's window (step 5b), for such a folder only (409), reached
    securely (409 for plain http), with the shell's word, never unchecked
    (403, a server without a secret too), the answer's ``older`` true when
    that server's pages do not say a turn waits here (its whoami without
    ``computer_asks``; asked once, 15 s at most, false when it does not
    answer); ``never`` the maker's alone; the gate of ``agent``, then 400
    for another value or a folder with no agent on (after the PATCH's own
    ``agent``). A resume hands the agent the Sends made since it was turned
    on that wait."""
    changes = (payload.notes is not None or payload.push is not None or payload.pull is not None
               or payload.library is not None or bool(payload.token) or payload.agent is not None
               or payload.agent_runs is not None)
    link = _made(request, link_id) if changes else _kept(request, link_id)
    if payload.agent is not None:
        _agent_allowed(request, link, payload.agent)
    if payload.agent_runs is not None:
        _runs_allowed(request, link, payload.agent_runs, payload.agent)
    try:
        if payload.notes is not None:
            link = folder_links.set_notes(link_id, payload.notes) or link
        if payload.push is not None:
            link = folder_links.set_push(link_id, payload.push, payload.token, payload.token_id) or link
        elif payload.token:
            link = folder_links.set_token(link_id, payload.token, payload.token_id) or link
        if payload.pull is not None:
            link = folder_links.set_pull(link_id, payload.pull) or link
        if payload.library is not None:
            link = folder_links.set_library(link_id, payload.library) or link
        if payload.paused is not None:
            link = folder_links.set_paused(link_id, payload.paused) or link
        if payload.agent is not None:
            queued = folder_links.set_agent(link_id, payload.agent, by=_account(request))
            link = folder_links.get_link(link_id) or link
        if payload.agent_runs is not None:
            folder_links.set_agent_runs(link_id, payload.agent_runs, by=_account(request))
    except folder_links.LinkError as e:
        raise _refused(e)
    if payload.paused is False:
        folder_links.run_in_background(link_id)
        _hand_waiting(link)
    out = _info(link)
    if payload.agent is not None:
        out["queued"] = queued
    if payload.agent_runs == "computer":
        out["older"] = _older_there(link_id)
    return out


def _agent_allowed(request: Request, link: dict, agent: str) -> None:
    """The PATCH's gate for ``agent``, in order, past ``_made``: a server
    that runs agents (404), a work folder, of this server or of another
    (``folder_links.agent_folder``; 400); for an agent turned on or
    changed, the shell's word (403), an agent Gamma knows (400) and one
    ready now (400 with its problem: this asks it afresh, a process or two).
    Turning it off needs nothing more."""
    from .. import local_agents
    from ..local_agents import catalog

    if not local_agents.enabled():
        raise HTTPException(status_code=404, detail="Agents run on the desktop app's own server.")
    if not folder_links.agent_folder(link):
        raise HTTPException(status_code=400, detail="Only a work folder can have an agent.")
    if not agent:
        return
    _from_shell(request, "Only the desktop app may turn on an agent for this folder.")
    if agent not in catalog.AGENTS:
        raise HTTPException(status_code=400, detail=f"Gamma knows no agent named {agent}.")
    found = catalog.probe(agent, fresh=True)
    if not found["ready"]:
        raise HTTPException(status_code=400,
                            detail=found["problem"] or f"{found['label']} is not ready on this computer.")


def _runs_allowed(request: Request, link: dict, runs: str, agent: str | None) -> None:
    """The PATCH's gate for ``agent_runs``, in order, past ``_made``: a
    server that runs agents (404), a work folder (400), ``never``, ``ask``
    or ``computer`` (400), an agent on once the PATCH's ``agent`` is set
    (400); for ``ask``, a work folder of this server (409: asking is in the
    chat, which a work folder of another server has not here) and the
    shell's word (403: letting an agent ask to run commands is the
    computer's decision); for ``computer``, a work folder of another
    server (409), reached securely (409 for plain http,
    ``remote_servers.plain``: what the person reads on the card must come
    from that server) and the shell's word, never unchecked (403, a server
    without a secret too: without the desktop app there is no window to
    ask in). ``never`` needs nothing more."""
    from .. import local_agents
    from ..local_agents import store

    if not local_agents.enabled():
        raise HTTPException(status_code=404, detail="Agents run on the desktop app's own server.")
    if not folder_links.agent_folder(link):
        raise HTTPException(status_code=400, detail="Only a work folder can have an agent.")
    if runs not in store.RUNS:
        raise HTTPException(status_code=400, detail="What an agent may run is never, ask or computer.")
    if not (agent if agent is not None else (store.agent_of(link["id"]) or {}).get("agent")):
        raise HTTPException(status_code=400, detail="Turn an agent on first.")
    if runs == "ask":
        if not folder_links.work_folder_link(link):
            raise HTTPException(status_code=409, detail="Asking in the chat is for a work folder of this computer's "
                                                        "server: for one of another server, choose Ask on this "
                                                        "computer.")
        _from_shell(request, "Only the desktop app may let an agent ask to run commands in this folder.")
    if runs == "computer":
        if not link["remote_url"] or not folder_links.work_vault(link):
            raise HTTPException(status_code=409, detail="Ask on this computer is for a work folder of another "
                                                        "server: here the agent asks in the chat.")
        if remote_servers.plain(link["remote_url"]) != "":
            raise HTTPException(status_code=409, detail=f"{remote_servers._host(link['remote_url'])} is reached "
                                                        "over plain http: Ask on this computer needs https.")
        _shell_strict(request, "Only the desktop app may let an agent ask on this computer.")


def _older_there(link_id: str) -> bool:
    """Whether the other server of a work folder that may now ask on this
    computer runs a Gamma whose pages do not say a turn waits here (its
    whoami without ``computer_asks``); False for one not reached: the
    policy is set either way."""
    link = folder_links.get_link(link_id, with_token=True)
    if not link or not link["remote_url"]:
        return False
    try:
        who = folder_links.remote_server(link, timeout=folder_links.ASK_TIMEOUT_S).get_json("/api/sync/whoami")
    except Exception as e:  # noqa: BLE001 — a server not reached, a sealed token that no longer opens
        log.info(f"[folders] asking {folder_links.server_name(link)} whether its pages say an agent waits: {e}")
        return False
    return isinstance(who, dict) and not who.get("computer_asks")


def _hand_waiting(link: dict) -> None:
    """A work folder that writes its directory again (resumed, its files
    chosen): its agent takes the Sends that wait for it (``runner.waiting``;
    one of another server, those that server hands it, which its channel
    asks for: ``runner.queue_folder``). An error is logged; the change it
    follows stands."""
    from .. import local_agents
    from ..local_agents import runner, store

    if not local_agents.enabled() or not folder_links.agent_folder(link):
        return
    try:
        if store.agent_of(link["id"]) is not None:
            runner.queue_folder(link["workspace_id"], link["id"])
    except Exception as e:  # noqa: BLE001 — the vault's change is made; the Sends wait for the next chance
        log.warning(f"[agents] the waiting Sends of vault {link['id']}: {e}")


@router.post("/{link_id}/sync")
def sync_link(link_id: str, request: Request, wait: int = 0, force: Literal["pull", "push"] | None = None,
              direction: Literal["both", "pull", "push"] = "both", confirm: int = 0):
    """A round now, in a thread of its own: the answer comes at once, its
    status reading ``running`` (the round it started, or one already
    running, which answers it and after which this one runs; with a page's
    round or another write holding the vault, the round kept for after it)
    until the round ends, which the client follows with ``GET /{link_id}``. Inline with
    ``wait=1``, for scripts (the answer then carries the round's status).
    ``direction`` narrows it to ``pull`` or ``push``; ``force=pull`` makes
    the files Gamma's, and for a vault ``force=push`` makes the pages the
    files (both counted and confirmed by the app first), refused where
    pushing everything is (``folder_links.check_vault``) and for a followed
    file, which always pushes; ``confirm=1`` takes a vault past its guard
    (``status.held``). A force or a confirmation is the link's maker's
    (``_made``), 403 for anyone else. 409 for a work folder whose files are
    not chosen yet: its tracking dialog's save runs its first round."""
    link = _made(request, link_id) if force or confirm else _kept(request, link_id)
    if folder_links.choosing(link):
        raise HTTPException(status_code=409, detail="Choose its files first.")
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
    else:
        folder_links.run_in_background(link_id, **kw)
    return _info(folder_links.get_link(link_id) or link)


@router.post("/{link_id}/relink")
def relink(link_id: str, payload: Relink, request: Request):
    """``{path}``: the vault found at another place (its directory moved, or
    its disk mounted elsewhere), whose marker must be this vault's; a
    followed file's PDF that moved. → the link; a round follows. 400 for a
    place that is not the link's, or one ``folder_links.check_vault``
    refuses; 403 but for the link's maker. Its agent on this computer is
    turned off (``_forget_agent``)."""
    _from_shell(request)
    _made(request, link_id)
    try:
        link = folder_links.relink(link_id, payload.path)
    except folder_links.LinkError as e:
        raise _refused(e)
    if link is None:
        raise HTTPException(status_code=404, detail="no such link")
    _forget_agent(link_id)
    return _info(link)


def _forget_agent(link_id: str) -> None:
    """A vault moved or removed forgets its agent on this computer, whose
    run ``folder_links`` stopped: it was turned on for one directory."""
    from ..local_agents import store

    store.set_agent(link_id, "", by="")


@router.get("/{link_id}/removal")
def removal(link_id: str, request: Request):
    """What removing the link would do, to say before it is asked: ``{pages,
    in_place, bytes, files, in_library, unreachable?}`` — its pages, of
    which a work folder's files not in the library (``files``) go to
    Recently deleted and the others (``in_library``) stay, and the PDFs read
    in place that are stored first, with their size; ``unreachable`` for a
    work folder of another server that cannot be asked now, whose removal
    is refused until it can (``DELETE``'s ``force``)."""
    return folder_links.removal(_kept(request, link_id))


@router.get("/{link_id}/page")
def vault_file_page(link_id: str, request: Request, path: str = Query(min_length=1, max_length=1000)):
    """The page of the work folder's file ``path`` (its path in the vault,
    ``/`` between names), from the vault's row of it
    (``linked_files.vault_page``) → ``{page}``: its id, "" while no round has
    read the file yet and for a page in Recently deleted (a work folder of
    another server's id there, unchecked). What the tracking dialog lands
    on after a Save that ticked a file opened from this computer. 400 for a
    ``path`` outside the vault or below a link; the rest as ``GET
    /{id}/tree`` (``_chooser``)."""
    link = _chooser(request, link_id)
    rel = path.strip().strip("/")
    try:
        vault_disk.path(folder_links.dest_of(link), rel)
    except vault_disk.Outside as e:
        raise HTTPException(status_code=400, detail=str(e))
    page, _trashed = linked_files.vault_page(link, rel)
    return {"page": page}


@router.get("/{link_id}/tree")
def selection_tree(link_id: str, request: Request, dir: str = Query("", max_length=1000),
                   base: Literal["file", "preset"] = "file"):
    """The tracking dialog's tree (gamma/work_folders/selection.py
    ``listing``): the vault's directory ``dir`` ("" its top) listed whole
    → ``{dir, base, rules: {exists, ours, hash}, capped, entries: [{path,
    directory, format, language, size, mtime (ms), git_ignored, selected,
    kind, page, pair, never, placeholder, walked?, locked?}]}``, ``selected``
    under ``base``: what the vault's ``.gammaignore`` tracks (the default
    without one), or a new work folder's preset, its PDFs and notes but those
    inside a directory git ignores. A directory git ignores, or one the
    listing's budget did not reach (``capped``), comes back ``walked: false``,
    listed by itself with ``?dir=``. Nothing is written. 400 for a ``dir``
    outside the vault, below a link or below what the scan never tracks;
    409 for a directory that is not there or is another vault's, or a
    ``.gammaignore`` that cannot be read; 403 but for the vault's maker, 404
    for anyone who cannot act on it, a file's vault and a kept folder of
    another server (``_chooser``)."""
    link = _chooser(request, link_id)
    try:
        return selection.listing(link, dir, base)
    except selection.SelectionError as e:
        return _not_chosen(e)


@router.post("/{link_id}/selection/preview")
def preview_selection(link_id: str, payload: SelectionPreview, request: Request):
    """``{selection: {base, changes}}``: what saving it would do
    (``selection.preview``) → ``{files, bytes, by_format: {pdf, markdown,
    image, other}, unread, over_cap, cap, leaving: [{path, page, title,
    notes, open_comments}], joined: [{path, with}], text, rules_ours}``,
    ``text`` the ``.gammaignore`` it writes; for a work folder of another
    server ``uploads: {files, bytes}`` too, what goes to that server and is
    stored there, and ``secrets``, the ticked files whose names say they
    hold secrets (``notes`` and ``open_comments`` null from a server older
    than this app). Nothing is written. The refusals of ``GET /{id}/tree``."""
    link = _chooser(request, link_id)
    try:
        return selection.preview(link, payload.selection.model_dump())
    except selection.SelectionError as e:
        return _not_chosen(e)


@router.put("/{link_id}/selection")
def save_selection(link_id: str, payload: SelectionSave, request: Request):
    """``{selection, base_hash, library?}``: what the vault tracks, saved
    (``folder_links.save_selection``): the vaults of one file inside it taken
    in, its ``.gammaignore`` written (a hand-written one kept in
    ``.gamma/trash``), a vault that was choosing no longer and none paused,
    its library switch set when given; then a round, confirmed past its
    guard → the link. 409 ``{detail, hash}`` when the ``.gammaignore`` on
    disk is not the one the dialog read (``base_hash``, "" for none); 400
    for more files than the vault reads, or a library switch refused; the
    refusals of ``GET /{id}/tree``. A work folder's agent then takes the
    Sends made since it was turned on that wait."""
    _chooser(request, link_id)
    try:
        link = folder_links.save_selection(link_id, payload.selection.model_dump(), payload.base_hash, payload.library)
    except selection.SelectionError as e:
        return _not_chosen(e)
    except folder_links.LinkError as e:
        raise _refused(e)
    if link is None:
        raise HTTPException(status_code=404, detail="no such link")
    _hand_waiting(link)
    return _info(link)


@router.delete("/{link_id}")
def delete_link(link_id: str, request: Request, files: Literal["keep", "trash"] = "keep", force: int = 0):
    """Remove the link; its pages stay, the PDFs a vault read in place
    stored first, but a work folder's files not in the library, which go to
    Recently deleted. A folder's directory stays as it is with
    ``files=keep`` (but for a vault's marker); with ``files=trash`` the
    files its rounds made move to its trash (``.gamma/trash``: files changed
    on disk and files the rounds never wrote stay). A followed file is never
    touched. A work folder whose files were never chosen is cancelled: its
    marker, its ``.gamma/`` and its folder go when they were made for it.
    → ``{ok, mark_stays?}``. 409 when a PDF read in place could not be
    stored, another program holds the state file a folder kept before left,
    or the other server of a work folder there, removed or cancelled,
    cannot let go of it now (``force=1``: removed all the same,
    ``mark_stays`` true, for a workspace owner's Let go there, as when that
    server stopped answering midway): nothing was removed; 403 but for the
    link's maker. Its agent on this computer is turned off."""
    _made(request, link_id)
    try:
        out = folder_links.delete_link(link_id, files=files, force=bool(force))
    except folder_links.LinkError as e:
        raise _refused(e, 409)
    _forget_agent(link_id)
    return out or {"ok": True}


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
