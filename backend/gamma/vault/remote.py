"""What a synced vault's round reads and writes Gamma through, and what both
remotes share (docs/dev/synced_vaults.md "A round"): the shapes a remote
answers (``Page``, ``Info``, ``Layout``), what a plan writes (``Edit``), the
refusals (``Refused``, ``Changed``, ``Gone``, ``Waiting``), the op-log client
a vault's writes carry (``CLIENT``) and the conflict kind of a clash the
file won (``CONFLICT``), ``set_page`` and ``set_doc`` through either
remote's ``apply``, and
``remote_for``: a link's remote, this server's workspace in-process
(gamma/vault/local_remote.py ``LocalRemote``) or a folder of another server
over HTTP (gamma/vault/http_remote.py ``HttpRemote``), with the pages
another vault takes left out (``left_out``).
"""

from dataclasses import dataclass, field

from .. import folder_links
from ..auth import DISK_CLIENT
from ..blocks_store import folder_subtree_ids
from ..db import connect_pages_db
from ..folder_sync import ROOT
from ..work_folders.library import work_folder_of

CLIENT = DISK_CLIENT       # what a vault's writes are logged as: no Inbox item, no hot round of their own
CONFLICT = "file_won"      # the conflict kind of a clash the file won
PLAN_TRIES = 3             # a push planned again this often when Gamma's edits land between its plan and the lock


class Refused(Exception):
    """The account that made the vault may no longer edit its workspace: the
    vault waits, as for its marker or its disk."""


class Changed(Exception):
    """The page moved under a plan: the ops built under the lock are not the
    ones the journal named. Nothing was written."""


class Gone(Exception):
    """The page is not there to write: gone for good, or in Recently deleted."""


class Waiting(Exception):
    """A file not settled yet, or a page that moved while it was read: the
    next round looks again."""


@dataclass(slots=True)
class Page:
    """A page as a round reads it: its change-log ``seq``, read before its
    ``blocks`` (a ``sync_tree`` snapshot, its root's included), and whether it
    is in Recently deleted."""
    id: str
    seq: int
    blocks: dict
    trashed: bool = False


@dataclass(slots=True)
class Info:
    """A page below the vault's folder: its title and seq, the folders its
    top is filed in (in their order), its document (``doc_id``, the hash of
    the document it owns: gamma/documents/model.py ``own_hash``, "" for a
    listed one; ``linked`` when it reads the file where it is; ``format``,
    ``ext`` and ``at`` the document's, "" for none: ``doc_info``), its
    labels' names, its aliases, the page it is a sub-page of
    (``page_parents``; "" for a library page), and a work folder's stamps:
    the work folder it is a file of ("" for none), whether it is in the
    library too, its file's name."""
    id: str
    title: str
    seq: int
    folders: list
    doc_id: str = ""
    linked: bool = False
    labels: list = field(default_factory=list)
    aliases: list = field(default_factory=list)
    parent: str = ""
    work_folder: str = ""
    library: bool = False
    file_name: str = ""
    format: str = ""
    ext: str = ""
    at: str = ""


@dataclass(slots=True)
class Layout:
    """The vault's folder as Gamma has it: ``pages`` (id → ``Info``, in
    library order), ``folders`` (id → ``(parent id, name)``, below the vault's
    folder, which is left out), the change log's ``cursor``, read first, the
    labels tree's seq (``labels``: a label renamed moves it, and no page's),
    ``shared``, ``(page id, title)`` of the pages filed here too that a
    vault beside this one, made before it, keeps (left out of ``pages``),
    and ``work``, the mark the vault's folder carries (``{pull, library}``:
    a work folder's; None for none, gamma/work_folders/marks.py)."""
    pages: dict
    folders: dict
    cursor: int
    labels: int = 0
    shared: list = field(default_factory=list)
    work: dict | None = None


def info_stamps(props: dict) -> dict:
    """``Info``'s fields of a page's work-folder stamps."""
    name = props.get("file_name")
    return {"work_folder": work_folder_of(props), "library": props.get("library") is True,
            "file_name": name if isinstance(name, str) else ""}


def doc_info(view) -> dict:
    """``Info``'s fields of a page's document (its ``page_document`` view;
    None for none)."""
    return {"format": view["format"], "ext": view["ext"], "at": view["at"]} if view else {}


@dataclass(slots=True)
class Edit:
    """What a plan writes: ``ops`` on the page and the ``conflicts`` the file
    won (``{block_id, mine, theirs, result, base}`` in Gamma's space, written
    ``resolved`` for a force); the ids of the file's blocks it gives
    (``ids``), whether the page then says what the file says (``level``), the
    blocks the file dropped (``deleted``), and the file's changes of blocks
    Gamma moved to other pages (``away``, ``merge.Merged.away``)."""
    ops: list
    conflicts: list = field(default_factory=list)
    resolved: bool = False
    ids: list = field(default_factory=list)
    level: bool = True
    deleted: list = field(default_factory=list)
    away: list = field(default_factory=list)


def set_page(remote, page_id: str, *, title=None, props=None) -> tuple[int, int]:
    """The page's title and root properties (a None value drops the key),
    through a remote's ``apply`` (``LocalRemote``, gamma/vault/http_remote.py
    ``HttpRemote``). → (seq before, seq after)."""

    def build(page, _held):
        root = page.blocks[page_id]
        op = {"op": "set", "id": page_id}
        if title is not None and title != root["content"]:
            op["content"] = title
        patch = {k: v for k, v in (props or {}).items() if root["props"].get(k) != v}
        if patch:
            op["props"] = patch
        return Edit([op] if len(op) > 2 else [])
    before, after, _edit = remote.apply(page_id, build)
    return before, after


def set_doc(remote, page_id: str, doc: dict) -> tuple[int, int]:
    """The page's document set to ``doc`` (a canonical doc,
    gamma/documents/model.py ``document``) and nothing else, through a
    remote's ``apply``: the vault's client, which the work-folder rule lets
    write a document (a file that became or stopped being listed, a rename
    that changed its format or extension). → (seq before, seq after)."""

    def build(page, _held):
        if page.blocks[page_id]["props"].get("doc") == doc:
            return Edit([])
        return Edit([{"op": "set", "id": page_id, "props": {"doc": doc}}])
    before, after, _edit = remote.apply(page_id, build)
    return before, after


def remote_for(link: dict):
    """A link's remote: for a folder of another server ``HttpRemote``
    (gamma/vault/http_remote.py) with the link's token (``folder_links.get_link``
    with ``with_token``); on this server its workspace, as its maker. Left out
    are the pages of the links below it, and of those made before it that
    are not above it (a page filed below two is the first's; the second
    reports it, ``Layout.shared``), that may take a page from it: one that
    pushes, or one that only pulls when this one only pulls too, and then
    only one made before it, so that of two that only pull each writes the
    copies it wrote before (two folders kept on disk before synced vaults,
    one inside the other's folder, kept full copies). One that only pulls
    never takes a page from a vault that pushes: it writes a copy in its own
    directory and nothing into Gamma, so the vault keeps its pages and its
    files. A vault of another server leaves out those of its workspace there
    by the same rule (``left_out``), from that server's folder tree."""
    from .local_remote import LocalRemote  # local: it imports this module

    if link["remote_url"]:
        from .http_remote import HttpRemote  # local: it imports this module

        return HttpRemote(link, folder_links.remote_server(link))
    with connect_pages_db(link["workspace_id"]) as conn:
        leave_out, siblings = left_out(link, lambda folder_id: folder_subtree_ids(conn, folder_id))
    return LocalRemote(link["workspace_id"], link["folder_id"], link["created_by"], leave_out, siblings)


def left_out(link: dict, subtree) -> tuple[list, list]:
    """``remote_for``'s rule: ``(the folders of the vaults whose pages the
    vault link leaves out, those of them beside it)``, of the vaults of its
    workspace on its server; ``subtree(folder id)`` is that folder and every
    folder below it there."""
    leave_out, siblings, earlier = [], [], True
    mine = subtree(link["folder_id"]) if link["folder_id"] != ROOT else None
    for other in folder_links.all_links():       # oldest first
        if other["id"] == link["id"]:
            earlier = False
            continue
        if (other["kind"] != "folder" or other["remote_url"] != link["remote_url"]
                or other["workspace_id"] != link["workspace_id"] or other["folder_id"] == ROOT):
            continue
        if not (other["push"] or not link["push"]):
            continue
        below = mine is None or other["folder_id"] in mine
        above = link["folder_id"] != ROOT and link["folder_id"] in subtree(other["folder_id"])
        if (below and (other["push"] or earlier)) or (earlier and not above):
            leave_out.append(other["folder_id"])
        if not below and earlier and not above:
            siblings.append(other["folder_id"])
    return leave_out, siblings
