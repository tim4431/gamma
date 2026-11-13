"""A synced vault's rounds (docs/research/vaults.md "A vault is a folder",
"Notes both ways"): a directory on this computer and a folder of Gamma kept
in step, the disk's changes read first (**push**), Gamma's written after
(**pull**), each only where the vault's switch for it is on.

A round reads and writes Gamma through a **remote**, the small interface of
what it needs there: the pages below the folder with their change-log seqs
(``layout``), a page's blocks (``page``), ops applied under the workspace's
write lock with the ops built under it (``apply``), a page made with its
blocks (``create``), the trash, filing, folders, titles, labels by name
(``label_ids``, made where missing), a PDF read where it is or stored, and
the conflicts the file won, recorded in the transaction of the ops. No connection crosses it: gamma/vault/local_remote.py's ``LocalRemote`` writes this
server's workspace in-process as the account that made the link and asks before each
write whether that account may still edit there; for a vault of another
server gamma/vault/http_remote.py's ``HttpRemote`` takes its place, over HTTP with
the link's token (gamma/vault/remote.py ``remote_for``, which also holds
what a round and both remotes share). What a round does with a file is its **class's**
(gamma/vault/kinds.py: ``notes``, ``pdf``, ``picture`` or, in a work folder,
``file``, decided by the name alone), the round's steps dispatched to it
where they differ by class.

**Crash safety** is a journal (gamma/vault/store.py): before any effect a
file's row holds ``pending``, what its base is about to be, under a token;
after the effect it is promoted. A push names its first batch's log row with
the token (``page_ops.batch_id``), and a round that finds the pending promotes
it when that row is there or, where the log no longer says, the page holds
what the plan wrote (a block it made, or each block it wrote as it wrote it),
and drops it otherwise, so that the file is read again; one whose page is in Recently
deleted on another server, which serves no log of it, waits for the push that
brings the page back (``_settled_push``); a new page is there or not, or
there without its blocks (a remote over HTTP makes the page, then its
blocks), and then the note's page all the same, its blocks merged into it
from no base; a file pull wrote holds the pending's hash,
or is still the file the write put there (the file id the pending names, or a
text nearer the pending's than the base's after a safe save: a person saved
over it since, which the scan then reads), or is that file moved (the scan
finds the file id elsewhere, and reads the move), or neither,
and the bytes a write replaced, kept beside the note until the history has
them, go to the history; a file on its way to the vault's trash is where the
pending says or where it was. Neither: the old base stays, and the worst is a
block read twice, never one lost. A pending that cannot be settled waits for
a later round, the vault going on. A move on disk holds its rows' pending
until each page is filed and named by it. The ids of new blocks are minted
from the token, and a plan is written as the journal named it, never built
again under the lock: a page Gamma wrote in between is planned again
outside it.

**Push**, disk to Gamma. The scan's files against the rows: added, changed (a
stat that moved, or the racy rule, then the hash), removed, moved
(``disk.pair_moves``, two names swapped too, a name changed only in
case whatever the text), and a directory renamed is one folder renamed; a
vault that does not push everything makes and removes nothing, its rows
following the files moved while Gamma keeps its places, and says which note
went. A file missing is removed once two scans
``store.MISSING_S`` apart agree and an ``lstat`` just before says so (and
no file of its kind waits to be read: that may be it, moved), a note emptied
is held the same way, and a round whose removals since the last quiet one, the
notes emptied counting, pass ``REMOVE_SHARE`` of ``REMOVE_FLOOR`` files or
more stops before it writes anything (``held``) until asked to go on. A new
note is a page, a new PDF a page reading its file where it is, with the note
of its stem or the note whose front matter names it, and a new note that pairs
so with a paper whose notes went from where they were is those notes moved; a
new file the remote refuses is sent again once it changes, and so are a PDF's
new bytes. A
changed note is merged into its page (gamma/vault/merge.py): the page as
it is with what the file changed since the base, **the file winning** a clash
and Gamma's text kept as a ``file_won`` conflict in the transaction of the ops;
a block Gamma moved to another page takes the file's change of it there, and
the page's labels what the note's ``tags`` changed, Gamma's own staying. A
callout that links the page's PDF and is no highlight Gamma knows is one of
its quote and page alone, which the relocation run then places, dashed. A
note whose base names none of its blocks (``_unknown``: a folder kept on disk
before synced vaults, gamma/vault/upgrade.py) is never merged, which would
add every block again: changed on disk it is kept until a force decides, and
pull writes it anew; a force push pairs its blocks with the page's as pull
would write them, an older client's highlight read as it wrote one (and pull
then writes that note anew). A new note whose ``gamma_id`` (an older client's line)
names a page of the vault is that page's notes, moved or found again, before
any pairing with a paper, and so is one pull finds where the page's new notes
would go, or where the client kept a sub-page's, beside its parent's.
A page in Recently deleted whose file changed comes back, and so does one
whose file is back where it lived or with its file id (``disk.came_back``).
A file the scan leaves out for a fixed rule (a link above it, a case clash)
but is there is never a removal; one the vault's ``.gammaignore`` leaves out
is deselected (below). The base is then the
file, and its seq the page's only when the page says what the file says, its
front matter too, so that pull writes the rest.

**Pull**, Gamma to disk, for each page whose seq passed its base's (and, when
the labels tree changed, each note with tags): its blocks
with a span (``md_lens.project``), each block's text in the file's space
(gamma/vault/links.py; a block whose text is the base's keeps its file text),
the file patched by ``md_lens.shape``. What a file cannot hold as Gamma has it,
a block split or lifted, goes back to Gamma as ops before the file is written,
only where the vault pushes everything and no caret is on those blocks; a
split that cannot go then waits, the file as it was. The
file must still be the base, by its hash: one changed since is pushed first,
once a round, or with push off kept and reported. The write is
``disk.replace_note``, what it replaced kept in the history. A title
changed renames the note (a ``title:`` key is patched instead), a first filing
changed moves it (a row that recorded none, an older folder's, moves only
once Gamma changes it), a PDF going with it only when it is the note's stem
beside it; a folder renamed renames its directory; a page trashed, or one that left
the vault (a PDF read in place stored first), moves its files to the vault's
trash, where each keeps its row until its page is purged and the trash has
held it ``disk.TRASH_DAYS`` (a PDF another page reads in place stored
for it first), and a page back brings them back; but a
note changed on disk that push has not read stays, kept. A PDF pull copied
out follows the page's when Gamma replaces it, unless it changed on disk: in
a vault that does not push PDFs that one is kept and reported until a force
pull. A page that did not change is never written, nor a PDF pull did not put
there; and a page's new file that the disk holds already, with the very bytes
pull would write and no row (a vault made again over the files it kept when it
was removed), there or where the client kept a sub-page's, is taken as that
page's, never written beside. A PDF changed on
disk whose page left the vault stays where it is, no longer synced. A vault
without notes files (an older folder kept with its PDFs alone) writes PDFs
only.

**What a vault tracks** is its ``.gammaignore`` (gamma/work_folders/
ignore_rules.py), else the default (notes, PDFs and pictures): one gone
since a round read it stops the vault, and pull writes nothing it leaves out.
**A work folder** (docs/dev/work_folders.md) is a vault that pushes
everything into a folder that carries the mark (``Layout.work``): its
directory is the source. Every file it tracks is a page: a note, a PDF, or
a **file** of any other format, pictures included (``kinds.FILE``: read in
place, its page's document the file, titled by its leaf, never in the
library by the switch; one past ``config.vault_keep_bytes`` is listed with
no bytes). Its new pages are stamped as its files, a move on
disk files a page first in its new directory's folder and names it, pull
renames or moves only a note that follows Gamma (no document, where the
vault pulls), copies no PDF out and writes no note beside a file, and a
file the selection leaves out, or a file whose page left, stays where it
is: its page to Recently deleted
first, then its row an in-place tombstone (``store.bury_in_place``),
which nothing moves or purges. In a kept folder a file left out is
forgotten, its page staying. A whole round of a work folder holds to its cap
of files and writes its mark again where the folder lost it.

**Force**: ``pull`` makes every file its page, where the file is now (the
rows follow the disk's moves first, Gamma keeping its places), ``push`` every
page's blocks with a span its file; both record what they replaced, written resolved, and a
block a force push deletes is kept in its base's ``gone`` with its properties
(a highlight's quote, page and rectangles). A force push is pushing
everything, refused where that is (``folder_links.forced``).

Errors are a file's (its row's ``problem``, the status's ``kept``), never the
vault's, which stops only for its marker (``disk.Stopped``, a copy of
the vault at its place too; its ``.gammaignore`` gone or unreadable, and a
work folder past its cap, the same way), its disk (``Offline``) and its account
(``Refused``). Nothing here writes vaults.db while it holds a workspace's
write lock.

``sync(link)`` is a whole round; ``sync_page(link, page_id)`` the hot loop's,
for one page's files: no scan, so nothing is removed, and nothing is made or
moved in a directory the disk changed since the last scan.
"""

import fnmatch
import json
import os
import secrets
import stat
import time
from collections import Counter
from dataclasses import dataclass, replace
from pathlib import Path

from .. import config, folder_links, gamma_sync, linked_files, md_lens, storage
from ..blocks_store import FOLDERS, LABELS, PARENT_PAGE, block_kind, new_block_id
from ..db import page_now
from ..documents import formats
from ..documents.model import document, is_listed, page_document, stored_name
from ..folder_links import COUNTS, DIRECTIONS, KEPT_SHOWN
from ..folder_sync import ID_KEY, ROOT
from ..jobs import error_text
from ..logbuf import log
from ..markdown_import import block_class
from ..obsidian_export import anchor_marker, vault_name
from ..ops import CYCLE, MAX_CONTENT, OpError
from ..work_folders import ignore_rules, selection, takeover
from ..work_folders.selection import MAX_FILES
from . import disk, kinds, links, merge, store
from .remote import PLAN_TRIES, Changed, Edit, Gone, Info, Page, Refused, Waiting, remote_for

REMOVE_SHARE = 0.5         # removals since the last quiet round past this share of the files hold a round
REMOVE_FLOOR = 10          # of this many files at least
FORCES = ("pull", "push")
PLACE = "marker_file"      # the state's key of the marker's file id (``Round._same_place``)
SELECTION = "selection"    # the state's key of the hash of the .gammaignore a whole round read ("" for none)
LEFT_OUT = "left out by .gammaignore; its page keeps its other file"
UNKNOWN = ("changed on this computer, and Gamma does not know which of its blocks the note held: a force pull "
           "writes Gamma's page here, a force push makes the page this file")
BOTH_PDF = ("changed on this computer and in Gamma: a force pull writes Gamma's PDF here, a force push makes this "
            "file the page's")
UNREAD = "unread"          # the hash of an older client's note taken as it wrote it while a cloud placeholder held it
LEFT = "changed on this computer, and its page left the vault: it stays where it is, no longer synced"


def _token() -> str:
    return secrets.token_urlsafe(9)


def _why(e: Exception) -> str:
    """A file's failure as its row and the status say it, which the vault's
    viewers read: an OpError's detail, an OSError without the path it names
    (``jobs.error_text``)."""
    return str(getattr(e, "detail", "") or error_text(e)) or type(e).__name__


def _refused(e: BaseException) -> bool:
    """Whether the remote refused a write for what it is (a 4xx: malformed,
    too large for a proxy), so that the same bytes sent again would be
    refused again; not a clash with an edit made meanwhile, a timeout or a
    rate limit, which the next round tries again."""
    return isinstance(e, OpError) and not e.conflict and 400 <= e.status < 500 and e.status not in (408, 409, 429)


# --- a block in Gamma, a block in the file ----------------------------------------------------

def _kind_of(page_id: str):
    return lambda block: block_kind(block["parent"], page_id, block["props"])


def _g(block: dict, page_id: str):
    """A block's text in Gamma as a file holds it: a highlight's note, quote
    and PDF page (``links.highlight``), any other block's content."""
    if block_kind(block["parent"], page_id, block["props"]) == "highlight":
        pos = block["props"].get("pdf_position") or {}
        return links.highlight(block["content"], str(block["props"].get("quote") or ""), pos.get("pageNumber"))
    return block["content"]


def _text(g) -> str:
    return g["note"] if isinstance(g, dict) else g


def _marker(block_id: str) -> str:
    """A new highlight's anchor: its id as an Obsidian anchor, which needs a letter."""
    marker = anchor_marker(block_id)
    return marker if any(c.isalpha() for c in marker) else f"b{marker}"


def _fields(text) -> dict:
    """A note's front matter, ``{}`` for none."""
    doc = md_lens.parse(text) if text is not None else None
    return doc.front.fields if doc and doc.front else {}


def _gamma_id(text) -> str:
    """The page a note's front matter names by ``gamma_id``, as the folders
    kept on disk before synced vaults wrote it ("" for none). It is read where
    a file has it, when its row does not say which page it is, and no pull
    writes it."""
    return str(_fields(text).get(ID_KEY) or "").strip() if text else ""


def _unknown(row: dict | None) -> bool:
    """Whether a note's base names none of its blocks (``nodes`` none): an
    older folder's note taken onto the engine (gamma/vault/upgrade.py), or one
    whose ``gamma_id`` named its page. Pull writes it anew; a change on disk is
    kept, never merged (it would add every block again), until a force
    decides (``UNKNOWN``)."""
    return row is not None and row["kind"] == kinds.NOTES.name and row["nodes"] is None and row["text"] is not None


@dataclass(slots=True)
class Base:
    """A note's base as its row keeps it: the text read (``doc``) and, by each
    block's place in it, ``{id, g, m?, a?}``: its id, its text in Gamma, how
    its links were written, its anchor."""
    doc: md_lens.Doc
    nodes: list

    @property
    def ids(self) -> list:
        return [node["id"] for node in self.nodes]


def _base(row) -> Base | None:
    if not row or row.get("text") is None:
        return None
    doc = md_lens.parse(row["text"])
    nodes = row.get("nodes") or []
    return Base(doc, nodes) if len(nodes) == len(doc.flat) else None


def _gamma_tree(doc: md_lens.Doc, entries: list, ids=None, props=None, skip=()) -> list:
    """``doc``'s tree with each block's text in Gamma (``entries``, by place),
    its id (``ids``, by place; None without) and what a new block of it
    takes (``props``, by place); the blocks of ``skip`` (places of blocks with
    no children) left out."""
    def node_of(node):
        out = {"id": ids[node.index] if ids else None, "content": _text(entries[node.index]["g"]),
               "children": [node_of(child) for child in node.children if child.index not in skip]}
        if node.anchor:
            out["anchor"] = node.anchor
        if props and props.get(node.index):
            out["props"] = props[node.index]
        return out
    return [node_of(node) for node in doc.nodes if node.index not in skip]


def _new_props(g) -> dict:
    """What a block the file brings takes: a highlight read from a callout
    that links the page's PDF has its quote and page, its place on the page
    not known yet (``pageNumber`` alone, as gamma/highlights.py allows), for
    the relocation run to find (``LocalRemote.place_highlights``)."""
    if not isinstance(g, dict):
        return {}
    return {"quote": g["quote"], "pdf_position": {"pageNumber": int(g["page"] or 1)}}


def _named(merged: merge.Merged, token: str, known: set) -> merge.Merged:
    """``merged`` with each id the merge minted (any not in ``known``) named by
    ``token`` and its place in the file: one plan built twice names the same
    blocks, so the journal knows their ids before the lock."""
    names = {block_id: f"{token}{n:x}" for n, block_id in enumerate(merged.ids) if block_id not in known}
    if not names:
        return merged

    def rename(block_id):
        return names.get(block_id, block_id)
    ops = []
    for op in merged.ops:
        op = {**op, "id": rename(op["id"])}
        if "parent" in op:
            op["parent"] = rename(op["parent"])
        ops.append(op)
    target = {rename(b): {**block, "parent": rename(block["parent"])} for b, block in merged.target.items()}
    return merge.Merged(target, ops, [rename(b) for b in merged.ids], merged.conflicts, merged.deleted, merged.level,
                              merged.away)


def _wrote(ops: list) -> dict:
    """What a plan's ``ops`` leave on the blocks they write, by which the
    journal knows the plan on the page when the log cannot say
    (``Round._plan_landed``): ``{id: the text set or inserted, None for one
    deleted}``."""
    out = {}
    for op in ops:
        if op["op"] == "delete":
            out[op["id"]] = None
        elif op["op"] in ("set", "insert") and "content" in op:
            out[op["id"]] = op["content"]
    return out


def _front_props(fields: dict, stem: str) -> tuple[str, dict, list]:
    """What a note's front matter says of its page, ``(title, props, labels)``,
    as the vault import reads it (``markdown_zip_import.MarkdownSource``):
    ``title``, ``tags`` (labels), ``aliases``, and ``doi``, ``authors``,
    ``year`` as the paper's record. ``parent`` makes a new page a sub-page
    once the round's new pages are made (``Round._sub_page``)."""
    def text(key):
        value = fields.get(key)
        return str((value[0] if value else "") if isinstance(value, list) else (value or ""))

    def items(*keys):
        out = []
        for key in keys:
            value = fields.get(key)
            out += value if isinstance(value, list) else ([value] if value else [])
        return [str(v).strip() for v in out if str(v).strip()]
    title = (text("title") if text("title").strip() else stem)[:500] or "Untitled"   # outer spaces: a quoted value's own
    props = {}
    aliases = list(dict.fromkeys(items("aliases", "alias")))
    if aliases:
        props["aliases"] = aliases
    if any(fields.get(k) for k in ("doi", "authors", "year")):
        props["meta"] = {"title": title, "doi": text("doi"), "authors": items("authors"), "year": text("year"),
                         "source": "manual"}
    return title, props, _tags(fields)


def _tagged(text) -> bool:
    """Whether a note's text names tags in its front matter."""
    return bool(text) and text.lstrip("﻿").startswith("---") and bool(_tags(_fields(text)))


def _tags(fields: dict) -> list:
    """A note's tags as label names: ``tags`` and ``tag``, a list or one, the
    ``#`` off."""
    out = []
    for key in ("tags", "tag"):
        value = fields.get(key)
        out += value if isinstance(value, list) else ([value] if value else [])
    return list(dict.fromkeys(t for t in (str(v).lstrip("#").strip() for v in out) if t))


def _labels_with(page: list, base: list, file: list) -> list:
    """The labels a page takes when its note's ``tags`` changed since the
    base: the file's tags in its order, but one Gamma took off since (in the
    base, not on the page), then Gamma's own (on the page, in neither). Names
    compare ignoring case, as a tag is read (``Round._front``)."""
    def folded(names):
        return {name.casefold() for name in names}
    on, was, now = folded(page), folded(base), folded(file)
    return [tag for tag in file if tag.casefold() in on or tag.casefold() not in was] + \
        [name for name in page if name.casefold() not in now | was]


def _blank(text: str | None) -> bool:
    """Whether a note's text holds nothing: blanks, line ends, a byte order
    mark (Notepad's empty "UTF-8 with BOM")."""
    return text is not None and not text.lstrip("﻿").strip()


def _carries(row: dict, pending: dict, data: bytes) -> bool:
    """Whether the note a pull's write (``pending``) went into, saved by a
    person since, still holds that write: its text nearer the pending's than
    the base's (``disk.nearer``)."""
    if row["kind"] != kinds.NOTES.name or not pending.get("text"):
        return False
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return False
    return disk.nearer(text, pending["text"], row.get("text") or "")


def _row_folder(directory: str, folder_id: str) -> str:
    """The first filing a row records for a file in ``directory``: its folder,
    "" for the vault's root, as pull reads a page's (``Round._pull_page``)."""
    return folder_id if directory else ""


def _stem(path: str) -> str:
    return os.path.splitext(path.rsplit("/", 1)[-1])[0]


def _name(title: str, ext: str = "") -> str:
    """A page's or a folder's name in the vault (``vault_name``; ``ext`` for a
    file's, none for a directory's), one the scan lists: a name it leaves out
    (``~$Plan``, ``node_modules``) takes ``_`` before it, or pull's own scan
    would never see the file."""
    name = vault_name(title)
    return name if disk.listed(name + ext, directory=not ext) else f"_{name}"


def _dir(path: str) -> str:
    return path.rpartition("/")[0]


def _join(directory: str, name: str) -> str:
    return f"{directory}/{name}" if directory else name


def fits(texts) -> None:
    """``Unwritable`` for a note with a block past what the op path takes
    (``ops.MAX_CONTENT``), its file's problem: no page holds what no edit
    could change. Gamma's file editor asks it too."""
    longest = max(map(len, texts), default=0)
    if longest > MAX_CONTENT:
        raise md_lens.Unwritable(f"a block of {longest:,} characters is longer than Gamma takes ({MAX_CONTENT:,})")


def _insertable(node: dict) -> dict:
    """A lens tree node as ``insert_page_tree`` takes it."""
    return {"id": node["id"], "content": node["content"], "properties": node.get("props") or {},
            "children": [_insertable(child) for child in node["children"]]}


def _retexted(tree: list, texts: list) -> list:
    """``tree`` with each node's text the next of ``texts`` (document order)."""
    at = iter(texts)

    def node_of(node):
        out = {"id": node["id"], "content": next(at), "children": []}
        out["children"] = [node_of(child) for child in node["children"]]
        return out
    return [node_of(node) for node in tree]


# --- a round ------------------------------------------------------------------------------

class _Rows(dict):
    """The rows of the vault's files where they are, ``{path: row}``, with
    their index by page (``of``), made again after any change."""

    def __init__(self, rows):
        super().__init__(rows)
        self._pages = None

    def __setitem__(self, path, row):
        super().__setitem__(path, row)
        self._pages = None

    def pop(self, *args):
        self._pages = None
        return super().pop(*args)

    def of(self, page_id: str) -> dict:
        """``{kind: (path, row)}`` of a page's files."""
        if self._pages is None:
            self._pages = {}
            for path, row in self.items():
                self._pages.setdefault(row["page_id"], {})[row["kind"]] = (path, row)
        return dict(self._pages.get(page_id, ()))


@dataclass(slots=True)
class Read:
    """A file as a round read it: its stat after the read, its hash, for a
    note its text (None with a ``problem``: not UTF-8), for a document read
    in place ``linked_files.hash_file``'s ``seen`` (a PDF's trailer's ``/ID``
    too) and its document's ``format`` and ``ext``; a work folder's file past
    the keep limit is ``listed``, no hash read (gamma/vault/kinds.py)."""
    path: str
    kind: str
    size: int
    mtime_ns: int
    file_id: str
    read_at: float
    hash: str
    text: str | None = None
    seen: tuple | None = None
    pdf_id: str = ""
    problem: str = ""
    format: str = ""
    ext: str = ""
    listed: bool = False

    def stat(self) -> dict:
        return {"size": self.size, "mtime_ns": self.mtime_ns, "file_id": self.file_id, "read_at": self.read_at}


class Round:
    """One round of the vault ``link`` (a ``folder_links`` row; its marker's
    id is ``state["vault"]``) through ``remote``; ``run`` gives its status.
    ``direction`` narrows it to ``push`` or ``pull``, ``force`` is ``pull``
    or ``push``, ``confirm`` goes on past the guard, and ``page_id`` makes it
    the hot loop's round of one page's files. ``progress``, when given,
    hears how far a whole round is as it goes (``_report``)."""

    def __init__(self, link: dict, remote, *, direction: str = "both", force: str = "", confirm: bool = False,
                 page_id: str = "", progress=None):
        if force and force not in FORCES:
            raise ValueError(f"force is pull or push, not {force!r}")
        if direction not in DIRECTIONS:
            raise ValueError(f"direction is both, pull or push, not {direction!r}")
        self.link, self.remote, self.force, self.confirm, self.only = link, remote, force, confirm, page_id
        self.id, self.root = link["id"], Path(link["path"])
        self.state = dict(link.get("state") or {})
        push = link.get("push") or folder_links.PUSH_OFF
        self.push_all = push == folder_links.PUSH_ALL or force == "push"
        self.push_pdfs = push != folder_links.PUSH_OFF or force == "push"
        self.notes = bool(link.get("notes", True)) or self.push_all   # off: a folder kept before with its PDFs alone
        # The disk's changes are read first, a force going one way: the side it replaces is not read. What
        # of them reaches Gamma is the switch's: with push off, only where the files went and what is kept.
        self.pushing = direction in ("both", "push") and force != "pull"
        self.pulling = direction in ("both", "pull") and (bool(link.get("pull")) or force == "pull") and force != "push"
        self.places = self.push_all and force != "pull"     # a move on disk refiles and renames its page in Gamma
        self.counts = dict.fromkeys(COUNTS, 0)
        self.kept = []              # (path, why, whether a force pull would replace it)
        self.status = {}
        self.marker = {}
        self.scan = None
        self.rows = _Rows({})
        self.reads = {}             # path -> Read, this round's
        self.origin = {}            # a path as a directory renamed on disk carries it -> its row's path now
        self.kept_paths = set()     # notes changed on disk that pull leaves as they are
        self.pushed_again = set()
        self.anchors = {}           # block id -> the marker a note's link asked of its file
        self.again = set()          # pages to write once more for an anchor
        self.unsettled = []         # (path, pending) of pull's writes the journal waits on the scan for
        self.refiles = []           # (path, where it was, token) of moves the journal says a page is not filed by yet
        self.unplaced = []          # the same, of moves whose page is in Recently deleted this round
        self.buried = set()         # pages whose file a tombstone keeps this round: a scan reads it back, or it is left
        self.parents = {}           # a new note -> the ``parent`` its front matter names (``_sub_page``)
        self.work = None            # the mark of the vault's folder, {pull, library}: a work folder's round
        self.rules = None           # what the vault tracks (its .gammaignore, else the default), read in ``_open``
        self.relabelled = False     # the labels tree changed since the last round: a note's tags may be stale
        self.layout = None
        self.cursor = None
        self.progress = progress
        self.done = self.total = self.bytes_done = self.bytes_total = 0
        self._ctx = None
        self._sources = None

    # --- the whole round -------------------------------------------------------------------

    def run(self) -> dict:
        started = time.time()
        try:
            self._open()
            if self.pushing and not self._push():
                return self._done(started)
            if self.force == "pull" and self.scan is not None:
                self._follow_moves()
            elif not self.pushing and self.scan is not None:
                self._report_kept()
            if self.pulling:
                self._pull()
            if self.scan is not None and self.state.get("placing"):
                self._find_highlights_again()
        except disk.Stopped as e:
            self.status.update(stopped=str(e), last_error=str(e))
        except disk.Offline as e:
            self.status.update(offline=True, last_error=str(e))
        except Refused as e:
            self.status["last_error"] = str(e)
        finally:
            self.remote.tell()
        return self._done(started)

    def _done(self, started: float) -> dict:
        status = {"running": False, "started_at": started, "last_sync": page_now(), "last_error": "",
                  "counts": self.counts, "kept": [{"path": p, "why": w, "force": f} for p, w, f in self.kept[:KEPT_SHOWN]],
                  **self.status}
        if self.scan is not None:
            status["untracked"] = self.scan.untracked
            if self.scan.refused:
                status["refused"] = [{"path": p, "why": w} for p, w in self.scan.refused[:KEPT_SHOWN]]
        return status

    def _open(self) -> None:
        """The marker, the mark of the vault's folder (a work folder's whole
        round writes it again where it is not the link's, after a restore or
        a mirror's merge; a vault that does not push everything, of another
        server's work folder, keeps a kept folder's round), what the vault
        tracks (``_read_rules``), the journal settled (it may move files),
        then the scan, which a work folder's round holds to its cap
        (``_capped``); one page's round reads no scan."""
        vault_id = self.state.get("vault") or ""
        self.marker = disk.check_marker(self.root, vault_id)
        self._same_place()
        self.work = self.remote.mark() if self.link.get("push") == folder_links.PUSH_ALL else None
        if not self.only and self.state.get(takeover.TAKEN_OVER) and folder_links.work_folder_link(self.link):
            self.work = takeover.write_mark(self.link, self.remote)
        self.rules = self._read_rules()
        self._recover()
        if not self.only:
            self.scan = disk.scan(self.root, vault_id, self.rules, work=bool(self.work))
            self._capped()
            disk.clean_temps(self.root, self.scan.temps)
            self._recover_moved()
        self.rows = _Rows(store.files(self.id))
        for path, old, token in self.refiles:
            if path in self.rows:
                self._each(path, self._refile, path, old, token)

    def _read_rules(self):
        """What the vault tracks: its ``.gammaignore`` (gamma/work_folders/
        ignore_rules.py), else the default. One gone since a whole round read
        it (the state's ``SELECTION``, its hash) stops the vault rather than
        reading every note and PDF anew, and so does one that cannot be read;
        one edited by hand is read as it is, its removals going through the
        guard. A whole round keeps the hash it read. The first whole round of
        a vault whose marker names an older app's ignore globs writes them
        once as its ``.gammaignore`` (``_globs_written``)."""
        try:
            rules = ignore_rules.read(self.root)
        except ignore_rules.Unreadable as e:
            raise disk.Stopped(f"its .gammaignore cannot be read: {e.why}") from e
        known = self.state.get(SELECTION)
        if not rules.exists and known:
            raise disk.Stopped("its .gammaignore is gone: put it back, or choose its files again")
        if self.only:
            return rules
        if not rules.exists and known is None:
            rules = self._globs_written() or rules
        self.state[SELECTION] = rules.hash
        return rules

    def _globs_written(self):
        """The marker's ``ignore`` globs, which an older app read, written as
        the vault's ``.gammaignore``: the default, then each glob left out (one
        with a ``/`` anchored at the root, as it matched the path from there).
        Where git's reading of a glob, always case-insensitive, takes other
        entries of the directory than the old ``fnmatch`` did (case-sensitive
        on macOS and Linux), lines that name those entries follow it, so the
        file leaves out what the old scan left out, no more and no less.
        → the rules read back; None when the marker names none."""
        try:
            raw = json.loads((self.root / disk.MARKER).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        globs = raw.get("ignore") if isinstance(raw, dict) else None
        globs = [g for g in (str(g).strip().strip("/") for g in globs or ()) if g] if isinstance(globs, list) else []
        if not globs:
            return None
        lines = [ignore_rules.HEADER, *(line for line in ignore_rules.DEFAULT_TEXT.splitlines()
                                        if line != ignore_rules.HEADER)]
        found = selection.walk(self.root)
        entries = [(d, True) for d in found.dirs] + [(f, False) for f in found.files]
        for glob in globs:
            line = f"\\{glob}" if glob[0] in "!#" else glob
            line = f"/{line}" if "/" in glob else line
            one = ignore_rules.Rules.parse(line)
            old = {(p, d) for p, d in entries if fnmatch.fnmatch(p if "/" in glob else p.rpartition("/")[2], glob)}
            new = {(p, d) for p, d in entries if one.decide(p, d)}
            lines.append(line)
            lines += [f"!{ignore_rules.anchored(p, d)}" for p, d in sorted(new - old)
                      if d or ignore_rules.DEFAULT.decide(p, False) is not True]
            lines += [ignore_rules.anchored(p, d) for p, d in sorted(old - new)]
        try:
            disk.write_new(self.root, ignore_rules.NAME, ("\n".join(lines) + "\n").encode("utf-8"))
        except FileExistsError:
            pass            # one written meanwhile is read as it is
        return ignore_rules.read(self.root)

    def _capped(self) -> None:
        """``Stopped`` before a work folder's round writes anything when its
        scan selects more files than its cap (the state's ``cap``, else
        ``MAX_FILES``), the status saying how many (``too_many``)."""
        if not self.work:
            return
        files = len(self.scan.files) + self.scan.pictures + self.scan.untracked
        cap = int(self.state.get("cap") or MAX_FILES)
        if files > cap:
            self.status["too_many"] = {"files": files, "cap": cap}
            what = "its .gammaignore tracks" if self.rules.exists else "it tracks"
            raise disk.Stopped(f"{what} {files:,} files, more than {cap:,}: choose fewer")

    def _tracked(self, rel: str) -> bool:
        """Whether the vault tracks ``rel``, a path pull would write."""
        return ignore_rules.tracks(self.rules, rel)

    def _not_tracked(self, rel: str, page_id: str = "") -> None:
        """A path pull would write that the vault's rules leave out: not
        written, said, and tried again next round."""
        self._keep(rel, f"not written: .gammaignore leaves out {rel}; tick its folder whole in Choose files…")
        if page_id:
            self.remote.touched.add(page_id)

    def _same_place(self) -> None:
        """``Stopped`` for a copy of the vault at its place (a backup disk given
        its drive letter, a directory restored over it): the marker is the
        vault's, its file another than the one the vault's first round found,
        which the state keeps (``PLACE``). A relink there takes the copy."""
        here, known = disk.marker_file(self.root), self.state.get(PLACE)
        if known and here and here != known:
            raise disk.Stopped("the directory holds a copy of the vault, not the vault itself: locate the vault, "
                                     "or relink to this copy to sync it")
        if here and not known:
            self.state[PLACE] = here

    def _keep(self, path: str, why: str, force: bool = False) -> None:
        self.kept.append((path, why, force))
        self.counts["kept"] += 1

    def _put(self, path: str, row: dict, **fields) -> None:
        """``fields`` of a file's row stored, and in ``row``, the round's copy
        of it, which the rest of the round reads."""
        store.put(self.id, path, **fields)
        row.update(fields)

    def _problem(self, path: str, why: str) -> None:
        """A file's problem, on its row when it has one and in the status; its
        page hears so (``LocalRemote.tell``)."""
        self._keep(path, why)
        if path in self.rows:
            self._put(path, self.rows[path], problem=why)
            if self.rows[path]["page_id"]:
                self.remote.touched.add(self.rows[path]["page_id"])

    def _each(self, path: str, fn, *args) -> None:
        """``fn`` for one file, whose failure is that file's alone: only its
        marker, its disk or its account stops the vault."""
        try:
            fn(*args)
        except (disk.Stopped, disk.Offline, Refused):
            raise
        except Waiting as e:
            self.counts["waiting"] += 1
            log.info(f"[vault] {path}: {e}")
        except md_lens.Unwritable as e:
            self._problem(path, str(e))
        except Exception as e:  # noqa: BLE001: one file never stops the vault
            log.warning(f"[vault] {self.root / path}: {e!r}")
            self._problem(path, _why(e))

    def _report(self, path: str = "") -> None:
        """How far the round is, to ``progress`` (folder_links writes it in
        the status): ``{done, total, path, bytes: {done, total}}``, the files
        push takes into Gamma and then the pages pull looks at, each side's
        joining ``total`` once that side knows them, the bytes of the PDFs push
        takes by the scan's sizes, and ``path`` the file at hand."""
        if self.progress is not None:
            self.progress({"done": self.done, "total": self.total, "path": path,
                           "bytes": {"done": self.bytes_done, "total": self.bytes_total}})

    def _counted(self, path: str, fn, *args, n: int = 1) -> None:
        """``_each`` of a step push counts: ``path`` reported as the file at
        hand, then ``n`` files done (a PDF with the note it pairs with is
        two), its bytes with them when it is hashed in place."""
        self._report(path)
        self._each(path, fn, *args)
        self.done += n
        self.bytes_done += self._read_bytes([path])

    def _read_bytes(self, paths) -> int:
        """The bytes of the files among ``paths`` that are hashed in place (a
        PDF's: ``Kind.weight``), as the scan found them."""
        return sum(kinds.by_kind(found.kind).weight(found) for found in map(self.scan.files.get, paths) if found)

    def _there(self, path: str) -> bool:
        """Whether the vault's file ``path`` is there, by an ``lstat``: one a
        link stands on the way to is, the scan's to report."""
        try:
            return disk.lstat(self.root, path) is not None
        except disk.Outside:
            return True

    def _gone(self, path: str) -> bool:
        """Whether the vault's file ``path`` is gone: not there while the
        marker still is. A disk unplugged during the round is ``Offline``,
        never its files gone."""
        if self._there(path):
            return False
        disk.check_marker(self.root, self.state.get("vault") or "")
        return True

    def _free(self, rel: str, own: str = "") -> str:
        """``disk.free_name`` among the disk's names and the rows' paths,
        for a file pull makes or moves there: in a page's round, which has no
        scan, only into a directory still as recorded (``_steady``)."""
        if self.scan is None:
            self._steady(_dir(rel))
        return disk.free_name(self.root, rel, own=own, taken=self.rows)

    def _steady(self, directory: str) -> None:
        """``Waiting`` when the nearest recorded directory at or above
        ``directory`` is not on the disk as recorded, by its file id: renamed
        or made again since the last scan, which the next whole round reads,
        so that a page's round never brings back a directory the person
        renamed."""
        recorded = store.dirs(self.id)
        while directory and directory not in recorded:
            directory = _dir(directory)
        if not directory:
            return
        try:
            st = disk.lstat(self.root, directory)
        except disk.Outside:
            st = None
        if st is None or recorded[directory]["file_id"] and str(st.st_ino) != recorded[directory]["file_id"]:
            raise Waiting(f"{directory} changed on disk since the last scan")

    def _ctx_at(self, path: str, page_id: str, pdf: str | None = None) -> links.LinkContext:
        """The links' context for the note ``path`` of the page ``page_id``,
        whose PDF is the vault path ``pdf`` (its row's by default). The pages
        whose notes this round cannot write (a problem, a cloud placeholder,
        no pull) take no new anchor: a link to one of their blocks is a link
        to the page. In a kept folder the files a link may name are the
        scan's pictures and those read in place before (``remote.pictures``);
        in a work folder every file is a page, its row naming the upload a
        link reads (``Kind.link_name``), and one the scan found that has no
        row yet (new this round, its page made after the notes changed) is
        read in place when a link names it (``_picture``), its row the
        page's once the page is made."""
        if self._ctx is None:
            ctx = links.LinkContext(links.habit_at(self.root), address=self.remote.address(),
                                          page_of=self.remote.page_of, title_of=self.remote.title_of,
                                          store=self._picture, more=None if self.scan else self._pictures)
            if self.work:
                for rel, found in (self.scan.files.items() if self.scan else ()):
                    if found.kind == kinds.FILE.name and rel not in self.rows:
                        ctx.add_file(rel)
            else:
                for rel in self.scan.images if self.scan else ():
                    ctx.add_file(rel)
                for rel, name in self.remote.pictures(self.root).items():
                    ctx.add_upload(rel, name)
            for rel, row in self.rows.items():
                cls = kinds.by_kind(row["kind"])
                if cls is kinds.NOTES:
                    if row["page_id"]:
                        ctx.add_note(rel, row["page_id"], _known_nodes(row.get("nodes")))
                        found = self.scan.files.get(rel) if self.scan else None
                        if not self.pulling or row["problem"] or (found and found.placeholder):
                            ctx.read_only.add(row["page_id"])
                elif (name := cls.link_name(rel, row)):
                    if cls.in_place:
                        ctx.add_file(rel, name)
                    else:
                        ctx.add_upload(rel, name)
            self._ctx = ctx
        if pdf is None:
            pdf = (self.rows.of(page_id).get(kinds.PDF.name) or ("",))[0]
        return self._ctx.at(path, page_id, pdf)

    def _picture(self, rel: str) -> str | None:
        """A file of the vault a note's link names, read where it is from now
        on (``LocalRemote.link_file``, an upload there over HTTP): its upload
        name, which in a kept folder its row keeps (kind ``picture``, the
        name as its hash) for the links of the rounds after. None for one a
        cloud placeholder holds, never read, and in a work folder for one past
        the keep limit, which its page lists without its bytes."""
        try:
            st = disk.lstat(self.root, rel)
        except disk.Outside:
            return None
        if st is None or disk._placeholder(st):
            return None
        if self.work:
            return None if st.st_size > config.vault_keep_bytes() else self.remote.link_file(disk.path(self.root, rel))
        name = self.remote.link_file(disk.path(self.root, rel))
        store.put(self.id, rel, kind=kinds.PICTURE.name, hash=name, size=st.st_size, mtime_ns=st.st_mtime_ns,
                  file_id=str(st.st_ino))
        return name

    def _pictures(self) -> list:
        """The vault's pictures, for a page's round, which has no scan: the
        directory walked once, when its note links one the round does not
        know (one put there since the last scan)."""
        return disk.walk(self.root, self.rules, work=bool(self.work)).images

    def _hold(self, path: str) -> None:
        """A note pull leaves as it is this round (``kept_paths``): no new
        anchor goes into it."""
        self.kept_paths.add(path)
        if self._ctx is not None and path in self.rows:
            self._ctx.read_only.add(self.rows[path]["page_id"])

    def _known_note(self, path: str, page_id: str, nodes: list) -> None:
        """A note the round just read or wrote, for the next one's links."""
        if self._ctx is not None:
            self._ctx.add_note(path, page_id, _known_nodes(nodes))

    def _find_highlights(self, page_id: str) -> None:
        """The relocation run asked for a page whose note brought highlights
        by their quote alone (``LocalRemote.place_highlights``); one that could
        not start now waits in the state (``placing``) for the next whole
        round. A page's round keeps no state: there it waits for a later run
        (*Find highlights in this PDF*)."""
        if not self.remote.place_highlights(page_id):
            self.state["placing"] = sorted({*(self.state.get("placing") or ()), page_id})

    def _find_highlights_again(self) -> None:
        waiting = [p for p in self.state.get("placing") or () if not self.remote.place_highlights(p)]
        if waiting:
            self.state["placing"] = waiting
        else:
            self.state.pop("placing", None)

    def _promoted(self, path: str) -> None:
        """A file's new base in the rows, one read in place in the links'
        context too (a note read later this round may link it); its page
        hears that its file changed."""
        row = store.get(self.id, path)
        if row and not row["was"]:
            self.rows[path] = row
            cls = kinds.by_kind(row["kind"])
            if self._ctx is not None and cls.in_place and (name := cls.link_name(path, row)):
                self._ctx.add_file(path, name)
        if row and row["page_id"]:
            self.remote.touched.add(row["page_id"])

    # --- the journal ---------------------------------------------------------------------------

    def _recover(self) -> None:
        """What a round that died left in the journal, settled by what the page
        and the file say now (the module doc)."""
        for row in store.pending(self.id):
            path, pending = row["path"], row["pending"]
            op, token = pending.get("op"), pending["token"]
            fields = pending.get("fields") or {}
            try:
                if op == "push":
                    page_id = pending.get("page_id")
                    done = page_id and (self._doc_landed(page_id, pending["hash"], fields.get("size"))
                                        if kinds.by_kind(row["kind"]).in_place else self._plan_landed(page_id, pending))
                    if done:
                        store.promote(self.id, path, token, **fields)
                    elif done is not None or not page_id:     # None: settled when the push brings the page back
                        store.drop(self.id, path, token)
                elif op == "create":
                    self._settle_create(path, pending)
                elif op in ("pull", "move"):
                    self._recover_write(row, path, pending, fields)
                elif op == "moved":
                    self.refiles.append((path, pending["from"], token))      # once the rows are read (``_open``)
                elif op == "trash":
                    self._recover_trash(row, path, pending)
                elif op == "restore":
                    self._recover_restore(path, pending)
                else:
                    store.drop(self.id, path, token)
            except (disk.Stopped, disk.Offline, Refused):
                raise
            except Exception as e:  # noqa: BLE001: a pending that cannot be settled yet waits; the vault goes on
                log.warning(f"[vault] {path}: the journal could not be settled yet: {e!r}")

    def _doc_landed(self, page_id: str, hash: str, size) -> bool:
        """Whether a push of a document read in place landed: the page's
        document is the bytes ``hash``; for a listed file (no hash: a work
        folder's file past the keep limit, pushed as a document of ``size``
        with no bytes) the page's document is listed at that size."""
        if self.remote.doc_of(page_id) != hash:
            return False
        if hash:
            return True
        page = self.remote.page(page_id)
        view = page_document(page.blocks[page_id]["props"]) if page and page_id in page.blocks else None
        return is_listed(view) and view["size"] == size

    def _plan_landed(self, page_id: str, pending: dict) -> bool | None:
        """Whether the push the journal names by ``pending["token"]`` is on the
        page: its batch in the page's log, read from the seq the plan was made
        at (``pending["at"]``) or, in this server's log, among rows as old as
        the note's read; where the log cannot say (the batch pruned, too much
        after it to read over HTTP), the page itself: a block the plan made
        there (``_named``: its id is the token's), or each block it wrote as it
        wrote it (``pending["wrote"]``, ``_wrote``: the text it set, gone for
        one it deleted), whatever Gamma wrote on other blocks since. One whose
        blocks were written on since reads as not landed, and the merge from
        the old base finds the change in them (``merge.file_wins`` makes
        a change once that the text holds). None for a page in Recently
        deleted on a server whose log of it is not served: dropped, a plan that
        only set text would be merged into once more from the old base, its
        words twice, so the push that brings the page back settles it
        (``_push_note``)."""
        token, read_at = pending["token"], (pending.get("fields") or {}).get("read_at", 0.0)
        landed = self.remote.landed(page_id, token, pending.get("at"), read_at)
        if landed is not None:
            return landed
        page = self.remote.page(page_id)
        if page is None:
            return False
        if page.trashed and not page.blocks:      # over HTTP a page in Recently deleted shows none
            return None
        made = next((n["id"] for n in pending.get("nodes") or () if str(n["id"]).startswith(token)), None)
        wrote = pending.get("wrote") or {}
        return made is not None and made in page.blocks or bool(wrote) and all(
            block_id not in page.blocks if text is None else page.blocks.get(block_id, {}).get("content") == text
            for block_id, text in wrote.items())

    def _settle_create(self, path: str, pending: dict) -> None:
        """A create's pending settled by what the remote holds (``_made``):
        promoted, dropped, or, for a page made without its blocks, a row of
        that page with no base, so that the scan reads the note as changed and
        merges its blocks into it, and pull writes nothing for it meanwhile."""
        made, token, fields = self._made(pending), pending["token"], pending.get("fields") or {}
        if made == "made":
            store.promote(self.id, path, token, **fields)
        elif made == "empty":
            store.put(self.id, path, page_id=pending["page_id"], hash="", text=None, nodes=None, size=0,
                            mtime_ns=0, file_id="", read_at=0.0, title=fields.get("title"), folder=fields.get("folder"),
                            seq=-1, ours=0, pending=None)
        else:
            store.drop(self.id, path, token)
            self.rows.pop(path, None)
            return
        self._promoted(path)

    def _made(self, pending: dict) -> str:
        """Whether the page a create named was made: ``made`` with its
        blocks (live with a note's blocks, it holds the first of them),
        ``empty`` without (a remote over HTTP makes the page, then its
        blocks: the page stays the note's, and the scan reads the note as
        changed from no base and merges every block into it, never into
        another page made each round), "" for none."""
        page_id, nodes = pending.get("page_id"), pending.get("nodes") or []
        state = self.remote.state(page_id) if page_id else None
        if not state:
            return ""
        if state != "live" or not nodes or self.remote.page_of(nodes[0]["id"]) == page_id:
            return "made"
        return "empty"

    def _recover_write(self, row: dict, path: str, pending: dict, fields: dict) -> None:
        """A file pull wrote or renamed: its bytes say which base stands. Other
        bytes in the file the write put there, by the file id the journal
        named (``disk.replace_note``'s ``ready``), or a text nearer the
        pending's than the base's (a safe save gave the file another id), are
        a person's save since: the pending is the base all the same, at the
        stat the write left, so that the scan reads the save and pushes it as
        a change, never Gamma's lines a second time. A file gone from where it
        was written waits for the scan (``_recover_moved``). What the write
        replaced, kept beside the note, goes to the history first."""
        self._kept_history(path, pending.get("kept") or "", row["hash"])
        at = pending.get("path") or path
        try:
            data = disk.read(self.root, at)
        except FileNotFoundError:
            if pending["op"] == "pull" and pending.get("file_id"):
                self.unsettled.append((path, pending))
            else:
                store.drop(self.id, path, pending["token"])
            return
        if pending["op"] == "move":       # the file is at its new name, or the rename never happened
            if disk.lstat(self.root, path) is not None:
                store.drop(self.id, path, pending["token"])
            else:
                store.promote(self.id, path, pending["token"])
            return
        st = os.stat(disk.path(self.root, at))
        if disk.sha(data) == pending["hash"]:
            store.promote(self.id, path, pending["token"], **fields, size=st.st_size, mtime_ns=st.st_mtime_ns,
                                file_id=str(st.st_ino), read_at=time.time())
        elif pending.get("file_id") and str(st.st_ino) == pending["file_id"] or _carries(row, pending, data):
            self._landed(path, pending)
        else:
            store.drop(self.id, path, pending["token"])

    def _landed(self, path: str, pending: dict) -> None:
        """A pull's write known to have landed though the file holds other
        bytes now: the pending is the base, at the stat the write left."""
        store.promote(self.id, path, pending["token"], **(pending.get("fields") or {}), size=pending["size"],
                            mtime_ns=pending["mtime_ns"], file_id=pending["file_id"], read_at=pending["mtime_ns"] / 1e9)

    def _recover_moved(self) -> None:
        """Pull's writes whose file was not where they put it when the journal
        was settled: a person moved it before this round. The file the scan
        finds with the file id the write put there means the write landed, so
        its pending is the base where it was written and the scan reads the
        move; with none, it is dropped."""
        ids = {found.file_id for found in self.scan.files.values() if found.file_id}
        for path, pending in self.unsettled:
            if pending["file_id"] in ids:
                self._landed(path, pending)
            else:
                store.drop(self.id, path, pending["token"])
        self.unsettled.clear()

    def _kept_history(self, path: str, kept: str, base: str) -> None:
        """The bytes a write that died had replaced and kept beside the note
        (``disk.replace_note``'s ``hold``), into the history (always
        when they were not the base: a person's save), and the copy gone."""
        if not kept:
            return
        try:
            data = disk.read(self.root, kept)
        except FileNotFoundError:
            return
        store.keep(self.id, path, data, always=disk.sha(data) != base)
        disk.drop_temp(self.root, kept)

    def _recover_trash(self, row: dict, path: str, pending: dict) -> None:
        """Files on their way to the trash finish the way, and a page whose
        files all went from the disk goes to Recently deleted."""
        if not row["was"]:
            store.drop(self.id, path, pending["token"])     # never buried: decided again
            return
        if os.path.lexists(self.root / row["was"]) and not os.path.lexists(self.root / path):
            disk.move(self.root, row["was"], path)
            self.remote.relink(str(self.root / row["was"]), str(self.root / path))
        if pending.get("disk") and row["page_id"]:
            self.remote.trash(row["page_id"])
        store.put(self.id, path, pending=None)

    def _recover_restore(self, path: str, pending: dict) -> None:
        target = pending["path"]
        if os.path.lexists(self.root / path) and not os.path.lexists(self.root / target):
            disk.move(self.root, path, target)
        if os.path.lexists(self.root / target):
            self.remote.relink(str(self.root / path), str(self.root / target))
            store.unbury(self.id, path, target)
            store.put(self.id, target, pending=None)
        else:
            store.put(self.id, path, pending=None)

    # --- push: the disk's changes --------------------------------------------------------------

    def _push(self) -> bool:
        """The disk's changes into Gamma; False when the guard held the round."""
        if self.only:
            for path, row in list(self.rows.items()):
                if row["page_id"] == self.only:
                    self._each(path, self._push_one, path)
            return True
        plan = self._classify()
        if any(plan[k] for k in ("renames", "moves", "changed", "new", "removed", "deselected")):
            self.remote.layout()      # ``Stopped`` when the vault's folder went: nothing is filed into it
        if self._held(plan):
            return False
        self.total += len(plan["changed"]) + len(plan["new"])
        self.bytes_total += self._read_bytes(plan["changed"] + plan["new"])
        self._report()
        self._apply_dirs(plan)
        self._apply_moves(plan)
        for path in plan["changed"]:
            self._counted(path, self._push_file, path, self.scan.files[path])
        unplaced, self.unplaced = self.unplaced, []
        for new, old, token in unplaced:      # a change pushed just now may have brought their page back
            self._each(new, self._refile, new, old, token)
        self._push_new(plan["new"])
        self._apply_removals(plan)
        self._apply_deselections(plan)
        if self.work:
            self.remote.drop_pageless(self.root)
        self._report()
        return True

    def _follow_moves(self) -> None:
        """A force pull's reading of the disk: where its files went, and no
        more. The rows follow each directory renamed and each file moved,
        Gamma keeping its places (``places``), so that Gamma's text is
        written where the note is now, never again where it was beside the
        moved one, which the next round would read as a new page."""
        plan = self._classify()
        self._apply_dirs(plan)
        self._apply_moves(plan)

    def _push_one(self, path: str) -> None:
        """A file of the page's round, pushed when its stat moved."""
        row, found = self.rows[path], self._stat(path)
        if found is None or found.placeholder or not (disk.moved(row, found) or self.force == "push"):
            return
        cls = kinds.by_kind(row["kind"])
        if cls is kinds.NOTES and not self.push_all:
            self._kept_note(path, row, found)
        elif cls.compared:
            self._push_file(path, found)

    def _stat(self, path: str):
        """A file of the rows as the scan would find it; None when it is gone
        or below a link, which the scan does not follow."""
        try:
            st = disk.lstat(self.root, path)
        except disk.Outside:
            return None
        if st is None:
            return None
        return disk.Found(self.rows[path]["kind"], st.st_size, st.st_mtime_ns, str(st.st_ino),
                                disk._placeholder(st))

    def _classify(self) -> dict:
        """The scan against the rows, read and nothing written: directories
        renamed, files moved (one renamed only in case whatever its text,
        ``_recased``), changed, new, missing, removed, and the notes emptied,
        which go as a removal goes (the module doc). The rows the scan meets
        (``known``) are those of a class that compares, of the class the path
        has now: a kept folder's picture row at a path a work folder reads as
        a file is not known, so the path is new, and its page's row takes the
        old one's place (``store.put_pending``). A removal waits while a
        file of its kind waits to be read: that file may be it, moved. A vault
        that does not push everything makes and removes nothing: its rows
        follow the files moved, a new file read only to find one, and a note
        gone is said, Gamma's changes to it waiting for a force pull. A file
        there that the vault's rules leave out is ``deselected``; one the scan
        leaves out for another reason (a link above it, a case clash, a name
        Windows refuses) is no removal at all."""
        found = self.scan.files
        renames = self._dir_renames()

        def carried(path):
            for old, new in renames:
                if path.startswith(f"{old}/"):
                    return new + path[len(old):]
            return path
        work = bool(self.work)
        known = {carried(p): row for p, row in self.rows.items()
                 if kinds.by_kind(row["kind"]).compared and kinds.class_of(p, work=work) is kinds.by_kind(row["kind"])}
        self.origin = {carried(p): p for p in self.rows}
        changed = [p for p in found if p in known and not found[p].placeholder
                   and (self.force == "push" or disk.moved(known[p], found[p]))
                   and (self.push_pdfs or known[p]["kind"] == kinds.NOTES.name)]   # push off: a PDF changed is not read
        gone, left_out, deselected = [], [], []     # a file the scan leaves out that is there is never a removal
        listed = {p.casefold() for p in found}      # Note.md found as note.md is renamed, not left out
        for p in known:
            if p not in found:
                if not self._tracked(p) and self._there(p):
                    deselected.append(p)
                elif p.casefold() not in listed and self._there(p):
                    left_out.append(p)
                else:
                    gone.append(p)
        new = [p for p in found if p not in known and not found[p].placeholder]
        if not self.push_all:
            new = [p for p in new if found[p].kind in {known[g]["kind"] for g in gone}]
        unread = set()
        for p in new + changed:
            try:
                self.reads[p] = self._read(p, found[p])
            except Waiting:
                self.counts["waiting"] += 1
                unread.add(found[p].kind)
            except OSError as e:
                self._keep(p, f"cannot be read: {_why(e)}")
                unread.add(found[p].kind)
        cased = self._recased(gone, new, known)
        olds = [self._known(p, known[p]) for p in gone + changed if p not in cased.values()]
        news = [self._known_read(self.reads[p]) for p in new + changed if p in self.reads and p not in cased]
        pairs, capped = disk.pair_moves(olds, news)
        pairs.update(cased)
        if capped:
            self.status["capped"] = True
            log.info(f"[vault] {self.root}: the search for moved notes stopped at {disk.MOVE_COMPARES} comparisons")
        sources = set(pairs.values())
        pairs = {f: g for f, g in pairs.items() if f not in known or f in sources}   # never onto a file that stays
        pairs.update(self._papers_notes(pairs, known, gone, new))
        pairs.update(self._named_notes(pairs, known, gone, new))
        sources = set(pairs.values())
        displaced = {p for p in changed if p in sources and p not in pairs and p in self.reads}
        moving = sorted(pairs)
        new = sorted({p for p in new if p in self.reads and p not in pairs} | displaced)
        changed = sorted({p for p in changed if p in self.reads and p not in sources} | set(moving))
        missing, removed, empty, now = [], [], set(), time.time()
        for p in gone:
            if p in sources:
                continue
            if not self.push_all:
                if known[p]["kind"] == kinds.NOTES.name and self.force != "pull":       # a force pull writes it
                    self._keep(p, "not on this computer: a force pull writes Gamma's page here again", True)
            elif not known[p]["missing_since"]:
                missing.append(p)
            elif store.missing_long(known[p], now) and known[p]["kind"] not in unread:
                removed.append(p)
        for p in list(changed):     # a note emptied whose base had text waits as a missing one does
            read, row = self.reads[p], known.get(p)
            if (self.push_all and read.kind == kinds.NOTES.name and _blank(read.text) and p not in pairs and row
                    and not _blank(row["text"] or "")):
                empty.add(p)
                if not row["missing_since"]:
                    missing.append(p)
                    changed.remove(p)
                elif not store.missing_long(row, now):
                    changed.remove(p)
        back = [p for p in [*found, *left_out, *deselected] if p in known and known[p]["missing_since"] and p not in empty]
        return {"renames": renames, "moves": [(f, pairs[f]) for f in moving], "changed": changed,
                "new": new if self.push_all else [], "removed": removed, "missing": missing, "back": back,
                "emptied": sorted(empty & set(changed)), "deselected": sorted(deselected)}

    def _recased(self, gone: list, new: list, known: dict) -> dict:
        """``{new path: gone path}`` of files renamed only in case: one file,
        whatever its text, never a second row on it. The old spelling names
        the new file still (a disk that ignores case), or its file id does."""
        spelled, out = {p.casefold(): p for p in new if p in self.reads}, {}
        for p in gone:
            q = spelled.get(p.casefold())
            same = q and self.scan.files[q].file_id not in ("", "0") and self.scan.files[q].file_id == known[p]["file_id"]
            if q and (same or self._there(p)):
                out[q] = p
        return out

    def _papers_notes(self, pairs: dict, known: dict, gone: list, new: list) -> dict:
        """``{new note: notes row}`` beyond ``pairs``: a note new on the disk
        that pairs with a paper (``_pdf_of``) whose page's notes are gone from
        where they were is those notes moved, however much its text changed
        (a paper and its notes renamed together, the notes edited)."""
        pdfs = {f: known[g] for f, g in pairs.items() if known[g]["kind"] == kinds.PDF.name}
        pdfs.update({p: row for p, row in known.items() if row["kind"] == kinds.PDF.name and p in self.scan.files})
        notes_of = {row["page_id"]: p for p, row in known.items() if row["kind"] == kinds.NOTES.name and row["page_id"]}
        out, taken = {}, set(pairs.values())
        for p in new:
            if p in pairs or p not in self.reads or self.reads[p].kind != kinds.NOTES.name:
                continue
            paper = pdfs.get(self._pdf_of(p, list(pdfs)))
            notes = notes_of.get(paper["page_id"]) if paper else None
            if notes in gone and notes not in taken:
                out[p] = notes
                taken.add(notes)
        return out

    def _named_notes(self, pairs: dict, known: dict, gone: list, new: list) -> dict:
        """``{new note: notes row}`` beyond ``pairs``: a note new on the disk
        whose front matter's ``gamma_id`` (an older client wrote it, and no
        pull writes it) names the page of notes gone from where they were is
        those notes moved."""
        notes_of = {row["page_id"]: p for p, row in known.items()
                    if row["kind"] == kinds.NOTES.name and row["page_id"] and p in gone}
        out, taken = {}, set(pairs.values())
        for p in new:
            if p in pairs or p not in self.reads or self.reads[p].kind != kinds.NOTES.name:
                continue
            notes = notes_of.get(_gamma_id(self.reads[p].text))
            if notes and notes not in taken:
                out[p] = notes
                taken.add(notes)
        return out

    def _kept_note(self, path: str, row: dict, found=None, read: "Read | None" = None) -> None:
        """A note whose stat moved in a vault that does not push notes: kept
        and reported, unless its bytes are the base's after all, whose stat
        and time of reading it then takes (``_push_file``'s rule), so that the
        next quiet round costs a stat. ``read``: the note as this round read
        it already; else it is read now, as the scan ``found`` it."""
        try:
            read = read or self.reads.get(path) or self._read(path, found)
        except Waiting:
            self.counts["waiting"] += 1
            return
        except OSError:
            return
        if read.hash == row["hash"]:
            self._put(path, row, **read.stat())
            return
        self._hold(path)
        self._keep(path, "changed on this computer and not pushed; a force pull replaces it", True)

    def _report_kept(self) -> None:
        """In a round of pull alone, the notes changed on disk, reported."""
        for path, row in self.rows.items():
            found = self.scan.files.get(path)
            if row["kind"] == kinds.NOTES.name and found and not found.placeholder and disk.moved(row, found):
                self._kept_note(path, row, found)

    @staticmethod
    def _known(path: str, row: dict) -> disk.Known:
        return kinds.by_kind(row["kind"]).known(path, row)

    @staticmethod
    def _known_read(read: Read) -> disk.Known:
        return disk.Known(read.path, read.kind, read.size, read.hash, read.file_id, read.pdf_id, read.text,
                                read.mtime_ns)

    def _read(self, path: str, found) -> Read:
        """A file read whole once it has settled, as its class reads it
        (``Kind.read``): a note statted again after its read (one that moved
        meanwhile waits), a PDF whole (``gamma_sync.settled``) and hashed in
        place. A time ahead of this clock (a share's whose clock runs ahead,
        a copy) is settled: the stat taken again says whether it is still
        being written."""
        self.counts["read"] += 1
        return kinds.by_kind(found.kind).read(self, path, found)

    # --- the guard -----------------------------------------------------------------------------

    def _held(self, plan: dict) -> bool:
        """Whether the round stops before it writes anything: its removals, a
        note emptied counting as one, and in a work folder each page whose
        files its ``.gammaignore`` leaves out, and those since the last quiet
        round pass ``REMOVE_SHARE`` of the files the vault had then,
        ``REMOVE_FLOOR`` or more. A file missing for the first time is noted
        all the same."""
        origin = self.origin
        if plan["missing"]:
            store.mark_missing(self.id, [origin.get(p, p) for p in plan["missing"]])
        if plan["back"]:
            store.found_again(self.id, [origin.get(p, p) for p in plan["back"]])
        back = {origin.get(p, p) for p in plan["back"]}
        waiting = plan["missing"] or [p for p, r in self.rows.items() if r["missing_since"] and p not in back]
        compared = sum(1 for r in self.rows.values() if kinds.by_kind(r["kind"]).compared)   # a picture's row is no file's
        total = self.state.get("total") or compared
        leaving = {self.rows[origin.get(p, p)]["page_id"] or p for p in plan["deselected"]} if self.work else set()
        going = len(plan["removed"]) + len(plan["emptied"]) + len(leaving)
        removals = self.state.get("removed", 0) + going
        if going and not self.confirm and total >= REMOVE_FLOOR and removals > REMOVE_SHARE * total:
            self.status["held"] = {"removals": removals, "total": total}
            return True
        if not going and not waiting:
            self.state.update(removed=0, total=compared + len(plan["new"]))
        else:
            self.state.update(removed=0 if self.confirm else removals, total=total)
        return False

    # --- push: directories ---------------------------------------------------------------------

    def _dir_renames(self) -> list:
        """``[(old, new)]``: directories gone from where they were and found
        elsewhere, by their file id, else holding most of their files under
        the same names and sizes. Shallowest first; what is below one goes
        with it."""
        recorded, found = store.dirs(self.id), self.scan.dirs
        olds = sorted((p for p in recorded if p not in found), key=lambda p: p.count("/"))
        news = sorted((p for p in found if p not in recorded), key=lambda p: p.count("/"))
        out = []
        for old in olds:
            if any(old.startswith(f"{a}/") for a, _ in out):
                continue
            file_id = recorded[old]["file_id"]
            hit = next((n for n in news if file_id and found[n] == file_id), None)
            if hit is None:
                held = {p[len(old) + 1:]: row for p, row in self.rows.items() if p.startswith(f"{old}/")}
                for n in news:
                    same = sum(1 for rel, row in held.items()
                               if (f := self.scan.files.get(f"{n}/{rel}")) is not None and f.size == row["size"])
                    if held and same * 2 >= len(held):
                        hit = n
                        break
            if hit is not None:
                out.append((old, hit))
                news.remove(hit)
        return out

    def _apply_dirs(self, plan: dict) -> None:
        """A directory renamed or moved on disk: its folder renamed or moved
        where the vault pushes everything (a failure is that directory's),
        and one rewrite of the rows below it. Where it does not, Gamma's
        folders stay, and pull names the directory by its folder again."""
        recorded = store.dirs(self.id)
        for old, new in plan["renames"]:
            folder_id = recorded[old]["folder_id"]
            if folder_id and self.places:
                self._each(new, self._refile_dir, folder_id, old, new)
            store.repath(self.id, old, new)
            store.put_dir(self.id, new, folder_id, self.scan.dirs.get(new, ""))
            self.remote.relink(str(self.root / old), str(self.root / new))
            self.counts["renamed"] += 1
        if plan["renames"]:
            self.rows = _Rows(store.files(self.id))

    def _refile_dir(self, folder_id: str, old: str, new: str) -> None:
        """The folder of a directory that went from ``old`` to ``new`` on disk,
        moved and named as the disk has it."""
        if _dir(old) != _dir(new):
            self._move_folder(folder_id, _dir(new))
        if new.rsplit("/", 1)[-1] != old.rsplit("/", 1)[-1]:
            self.remote.folder_op({"op": "set", "id": folder_id, "content": new.rsplit("/", 1)[-1]})

    def _move_folder(self, folder_id: str, directory: str) -> None:
        """The folder moved into the one of ``directory``. Where Gamma moved
        that one below it meanwhile (``B`` under ``A`` while the disk moved
        ``A`` into ``B``), the move would be a loop: the disk's places win, as
        the file's do for blocks, so the folders of ``directory`` and the
        directories above it first go where the disk has them, from the top."""
        move = {"op": "move", "id": folder_id, "parent": self._folder_for(directory)}
        try:
            self.remote.folder_op(move)
        except OpError as e:
            if e.conflict != CYCLE or not directory:
                raise
            names = directory.split("/")
            for k in range(1, len(names) + 1):
                above = "/".join(names[:k])
                self.remote.folder_op({"op": "move", "id": self._folder_for(above), "parent": self._folder_for(_dir(above))})
            self.remote.folder_op(move)

    def _folder_for(self, directory: str) -> str:
        """The folder a directory of the vault is, made with the folders above
        it where missing; "" for the vault's root (the vault's own folder, or
        none for a vault of the whole library)."""
        if not directory:
            return "" if self.link["folder_id"] == ROOT else self.link["folder_id"]
        recorded = store.dirs(self.id)
        if recorded.get(directory, {}).get("folder_id"):
            return recorded[directory]["folder_id"]
        names = directory.split("/")
        made = self.remote.folders([names[:k] for k in range(1, len(names) + 1)])
        dirs = self.scan.dirs if self.scan else {}
        for k in range(1, len(names) + 1):
            path = "/".join(names[:k])
            if not recorded.get(path, {}).get("folder_id"):
                store.put_dir(self.id, path, made[tuple(names[:k])], dirs.get(path, ""))
        return made[tuple(names)]

    # --- push: moves --------------------------------------------------------------------------

    def _apply_moves(self, plan: dict) -> None:
        """Files moved on disk: their rows follow (names swapped go through
        names no file has), each in the journal until its page is filed and
        named by its file (``_refile``), which a round that dies leaves to
        the next."""
        moves = plan["moves"]
        if not moves:
            return
        token = _token()
        store.repath_files(self.id, moves, token)
        self.rows = _Rows(store.files(self.id))
        for new, old in moves:
            self._each(new, self._refile, new, old, token)
            self.counts["renamed"] += 1

    def _refile(self, new: str, old: str, token: str) -> None:
        """A move's page filed and named by it (``_moved``), its journal let
        go then; one whose page waits in Recently deleted keeps it, tried
        again once this round's changes are pushed (a sub-page's file renamed
        while the page it is under was trashed, a change of that page's file
        bringing both back) and by the rounds after (``_recover``)."""
        if self._moved(new, old):
            store.drop(self.id, new, token)
        elif (new, old, token) not in self.unplaced:
            self.unplaced.append((new, old, token))

    def _moved(self, new: str, old: str) -> bool:
        """A file moved on disk: its page filed in the new directory's folder
        in place of the old one, the same index of its list (a sub-page's
        file moved makes it a library page), and named by the new stem. The
        rows record the first filing pull then reads (``_first``), so pull
        never moves the file again: at the top of a vault of the whole library,
        which is no folder, that is the page's next one. A page in Recently
        deleted whose file changed comes back first (``_push_note``'s rule),
        so it is named and filed by it too; one whose file did not waits
        there: False. Where the vault does not push everything, and in a
        force pull, only the rows follow: Gamma keeps its places. A work
        folder's file is filed first in its new directory's folder, whatever
        moved, then in its folders outside every work folder, and takes the
        file's name; its title follows the stem only while it is not in the
        library, whose paper's title is free. A file of another format is
        titled by its leaf, and one whose new name says another format or
        extension (``notes.txt`` to ``notes.log``, ``a.txt`` to ``a.png``)
        has its document settled again with the same hash, in the same
        ``set``, its linked row under the new name first and the old one gone
        after (``_file_doc``); a rename that changes the class (``a.txt`` to
        ``a.md``) never pairs, and is a removal and a new page."""
        row = self.rows[new]
        cls = kinds.by_kind(row["kind"])
        cls.moved(self, new, old, row)
        page_id = row["page_id"]
        if not page_id or not self.places:
            return True
        state = self.remote.state(page_id)
        if state == "trashed" and new in self.reads and self.reads[new].hash != row["hash"]:
            self.remote.restore(page_id)
            state = "live"
        if state != "live":
            return state is None
        props, folder, free = {}, row["folder"] or "", False
        if self.work:
            folders, parent = self.remote.filing(page_id)
            layout, tree = self.remote.layout(page_id), self.remote.work_tree()
            props[FOLDERS] = [self._folder_for(_dir(new))] + [f for f in folders if not tree.work_of(f)]
            if parent:
                props[PARENT_PAGE] = None
            if cls is kinds.PDF or kinds.PDF.name not in self.rows.of(page_id):
                props["file_name"] = new.rsplit("/", 1)[-1]
            folder = self._first(props[FOLDERS], layout.folders)
            free = page_id in layout.pages and layout.pages[page_id].library
        elif _dir(new) != _dir(old):
            folders, parent = self.remote.filing(page_id)
            was, now = self._folder_for(_dir(old)), self._folder_for(_dir(new))
            filed = list(folders)
            if was and was in filed:
                filed[filed.index(was)] = now
            elif now:
                filed.insert(0, now)
            props[FOLDERS] = [f for f in dict.fromkeys(filed) if f] or None
            if parent:
                props[PARENT_PAGE] = None
            folder = self._first(props[FOLDERS] or [], self.remote.layout(page_id).folders)
        title = row["title"]
        notes = self.rows.of(page_id).get(kinds.NOTES.name)
        unlink = ""
        if cls is kinds.FILE:
            leaf, was = new.rsplit("/", 1)[-1], old.rsplit("/", 1)[-1]
            if leaf != was and not free:
                title = leaf
            doc = self._file_doc(new, old, row)
            if doc is not None:
                props["doc"] = doc
                if row["hash"]:
                    seen = (row["hash"], row["size"], row["mtime_ns"], row["file_id"])
                    self.remote.link_doc(str(self.root / new), seen, page_id, doc["ext"])
                    if doc["ext"] != formats.vault_extension(was):
                        unlink = stored_name(row["hash"], formats.vault_extension(was))
        elif _stem(new) != _stem(old) and (cls is kinds.NOTES or not notes) and not free:
            if not (cls is kinds.NOTES and _fields(row["text"]).get("title")):
                title = _stem(new)
        before, after = self.remote.set_page(page_id, title=title if title != row["title"] else None, props=props or None)
        if unlink:
            self.remote.unlink_doc(str(self.root / new), unlink)
        level = row["seq"] == before
        for path, at_row in self.rows.of(page_id).values():
            self._put(path, at_row, title=title, folder=folder,
                      seq=after if level and at_row["seq"] == before else at_row["seq"])
        return True

    def _file_doc(self, new: str, old: str, row: dict) -> dict | None:
        """The document of a work folder's file moved from ``old`` to ``new``
        when the new leaf says another format or extension than the old one
        did (``kinds.file_format`` with the file's first bytes, which the
        rename kept; ``formats.vault_extension``), the hash kept: a listed
        file's with no bytes. None when they say the same."""
        leaf, was = new.rsplit("/", 1)[-1], old.rsplit("/", 1)[-1]
        try:
            with open(disk.path(self.root, new), "rb") as f:
                head = f.read(formats.SNIFF_BYTES)
        except OSError:
            head = None
        shape = (kinds.file_format(leaf, head), formats.vault_extension(leaf))
        if shape == (kinds.file_format(was, head), formats.vault_extension(was)):
            return None
        if row["hash"]:
            return document(row["hash"], name=leaf, at="path", format=shape[0], ext=shape[1], size=row["size"])
        return document("", name=leaf, at="listed", format=shape[0], ext=shape[1], size=row["size"])

    # --- push: a changed file ---------------------------------------------------------------------

    def _push_file(self, path: str, found) -> None:
        """A file of the rows whose stat moved: the same bytes refresh its row
        and, for a document read in place, the stat of its linked row too
        (``remote.restat_doc``: a file touched goes on being served; a listed
        file's size is its bytes), new ones go to its page as its class sends
        them (``Kind.change``), a note's only where the vault pushes
        everything (else it is kept)."""
        row = self.rows.get(path)
        if row is None or found is None or found.placeholder:
            return
        read = self.reads.get(path) or self._read(path, found)
        same = read.hash == row["hash"] and (not read.listed or read.size == row["size"])
        if same and self.force != "push":
            self._put(path, row, **read.stat(), missing_since=0)
            cls = kinds.by_kind(read.kind)
            if cls.in_place and read.hash and row["page_id"]:
                self.remote.restat_doc(str(self.root / path), read.seen, cls.link_name(path, row))
            return
        kinds.by_kind(read.kind).change(self, path, read, row)

    def _push_doc(self, path: str, read: Read, row: dict) -> None:
        """A document read in place with new bytes (a PDF, a work folder's
        file) is its page's: the disk wins (a followed file's rule,
        ``gamma_sync.Push``; a file replaced in Gamma stands until the file
        changes). A work folder's file that is listed, or was (past the keep
        limit either way), takes one set of its document instead
        (``_set_file_doc``). Bytes the remote refuses are sent again once they
        change, never each round."""
        cls, page_id, leaf = kinds.by_kind(read.kind), row["page_id"], path.rsplit("/", 1)[-1]
        state = self.remote.state(page_id) if page_id else None
        if state is None:
            store.forget(self.id, path)
            self.rows.pop(path, None)
            self._making(read, cls.new, self, path, read)
            return
        doc = self.remote.doc_of(page_id)
        if state == "trashed":
            self.remote.restore(page_id)
        token = _token()
        fields = {**read.stat(), "missing_since": 0, "problem": "", "ours": 0}     # bytes of this computer's now
        store.put_pending(self.id, path, read.kind, {"op": "push", "token": token, "hash": read.hash,
                                                         "text": cls.pending_text(read), "nodes": None,
                                                         "page_id": page_id, "fields": fields})
        try:
            if cls is kinds.FILE and (read.listed or not row["hash"]):
                self._set_file_doc(page_id, path, read, row)
            elif doc != read.hash:
                self.remote.replace_doc(page_id, str(self.root / path), read.seen, leaf, format=read.format, ext=read.ext)
            else:
                self.remote.link_doc(str(self.root / path), read.seen, page_id, read.ext)
        except BaseException as e:
            if read.listed or self.remote.doc_of(page_id) != read.hash:     # a listed read's hash says nothing
                store.drop(self.id, path, token)
                if isinstance(e, Exception) and not isinstance(e, (disk.Stopped, disk.Offline, Refused,
                                                                   Waiting)):
                    self._put(path, row, **read.stat(), problem=_why(e))     # refused: not again until it changes
            raise
        store.promote(self.id, path, token, **fields)
        self._promoted(path)
        self.counts["sent"] += 1

    def _set_file_doc(self, page_id: str, path: str, read: Read, row: dict) -> None:
        """A work folder's file across the keep limit, either way, or listed
        with a new size: its page's document set to the file's shape now
        (``remote.set_doc``: listed with no bytes, or read in place), the
        linked row made first for one read in place, the row of the bytes
        before gone after."""
        at, leaf = str(self.root / path), path.rsplit("/", 1)[-1]
        if read.listed:
            doc = document("", name=leaf, at="listed", format=read.format, ext=read.ext, size=read.size)
        else:
            doc = document(read.hash, name=leaf, at="path", format=read.format, ext=read.ext, size=read.size)
            self.remote.link_doc(at, read.seen, page_id, read.ext)
        self.remote.set_doc(page_id, doc)
        if row["hash"] and row["hash"] != read.hash:
            self.remote.unlink_doc(at, stored_name(row["hash"], read.ext))

    def _push_note(self, path: str, read: Read, row: dict) -> None:
        """A note with new bytes into its page (gamma/vault/merge.py); one whose
        page went for good is a new page, one in Recently deleted comes back.
        One that cannot be read as text keeps the base it had, so that pull
        finds it changed under it and never writes over it."""
        if read.problem:
            self._put(path, row, **read.stat(), problem=read.problem)
            self._keep(path, read.problem)
            return
        page_id = row["page_id"]
        state = self.remote.state(page_id) if page_id else None
        if state is None:
            store.forget(self.id, path)
            self.rows.pop(path, None)
            self._making(read, kinds.NOTES.new, self, path, read)
            return
        if _unknown(row) and self.force != "push":
            self._hold(path)
            self._keep(path, UNKNOWN, True)
            return
        if state == "trashed":
            self.remote.restore(page_id)     # the file is the authority
            row = self._settled_push(path, row)
        self._merge_into(page_id, path, read, row)

    def _settled_push(self, path: str, row: dict) -> dict:
        """The row of a note whose push the journal could not settle while
        its page was in Recently deleted (``_plan_landed`` None), settled now
        that the page is back and its log reads again, so that the note is
        merged from the base that plan left."""
        pending = row.get("pending") or {}
        if pending.get("op") != "push":
            return row
        if self._plan_landed(row["page_id"], pending):
            store.promote(self.id, path, pending["token"], **(pending.get("fields") or {}))
        else:
            store.drop(self.id, path, pending["token"])
        self.rows[path] = row = store.get(self.id, path)
        return row

    def _merge_into(self, page_id: str, path: str, read: Read, row: dict | None, extra=None) -> None:
        """The note's text merged into its page: planned before the lock, for
        the journal, and written as planned, or planned again when Gamma wrote
        the page in between (``PLAN_TRIES``, then the next round); the file
        wins a clash (gamma/vault/merge.py), and a block Gamma moved to another page
        takes the file's change of it there (``_follow``). Then the labels and
        the seq (``_settled``). ``row`` None: a note new to a page that has a
        file already (a paper's), ``extra`` the fields its row starts with."""
        doc = md_lens.parse(read.text)
        if doc.problem:
            raise md_lens.Unwritable(doc.problem)
        base = _base(row)
        ctx = self._ctx_at(path, page_id)
        gone = store.kept_gone((row or {}).get("gone"))
        old, new = md_lens.flatten(md_lens.tree_of(base.doc, base.ids)) if base else [], md_lens.flatten(md_lens.tree_of(doc))
        pairs = _with_moves(old, new, md_lens.match(old, new, gone))
        entries, props, placing = [], {}, False
        for n, node in enumerate(doc.flat):
            i = pairs.get(n)
            was = base.nodes[i] if base and i is not None and i < len(base.nodes) else None
            if was is not None and base.doc.flat[i].content == node.content:
                g, memo = was["g"], was.get("m") or {}      # identity first
            else:
                g, memo = links.to_gamma(node.content, (was or {}).get("m") or {}, ctx,
                                               base=was["g"] if was else None)
            entries.append({"g": g, **({"m": memo} if memo else {}), **({"a": node.anchor} if node.anchor else {})})
            if isinstance(g, dict):          # what it takes if it is new, or back after Gamma deleted it
                props[n] = _new_props(g)
                placing = placing or not isinstance((was or {}).get("g"), dict)
        fits(_text(e["g"]) for e in entries)
        base_tree = _gamma_tree(base.doc, base.nodes, base.ids) if base else []
        file_tree = _gamma_tree(doc, entries, props=props)
        claimed = {(old[i]["id"] if i < len(old) else gone[i - len(old)]["id"]) for i in pairs.values()}
        known = set(base.ids if base else ()) | {e["id"] for e in gone}
        kind_of, force = _kind_of(page_id), self.force == "push"
        older = force and _unknown(row)      # an older folder's note forced: read against the page as pull writes it
        fm_ops, labels = self._front_ops, self._file_labels(page_id, doc, base)
        for _ in range(PLAN_TRIES):
            page = self.remote.page(page_id)
            if page is None or page.trashed:
                raise Waiting("the page went while its note was read")
            token = _token()
            premerged = merge.premerge(page.blocks, base_tree, file_tree, pairs)     # the slow part, before the lock
            folded = set()

            def plan(now, held):
                if force:
                    projected = md_lens.project(now.blocks, kind_of)
                    if older:
                        tree, by_page, folds = self._older_tree(now, projected, doc, entries, props, ctx)
                        folded.update(folds)
                    else:
                        tree, by_page = file_tree, _by_page(projected, base, pairs)
                    edit = _forced(now, projected, tree, by_page, token, known, kind_of)
                    return replace(edit, ops=edit.ops + _label_ops(now, labels))
                # the base's blocks the lens left unpaired too: one moved away may be the file's, its class changed
                holders = held((claimed | set(base.ids if base else ())) - set(now.blocks))
                merged = merge.merge_page(now.blocks, page_id, base_tree, file_tree, pairs, kind_of=kind_of,
                                                merge=premerged, gone=gone, held={b for b, live in holders.items() if not live},
                                                away={b for b, live in holders.items() if live})
                merged = _named(merged, token, known)
                return Edit(merged.ops + fm_ops(now, doc, base, labels), merged.conflicts, False, merged.ids, merged.level,
                            merged.deleted, merged.away)
            expected = plan(page, lambda ids: self.remote.held(page_id, ids))
            try:
                for moved in expected.away:
                    self._follow(moved)
            except Gone:
                continue          # that page went to Recently deleted meanwhile: planned again
            nodes = None if folded else [{"id": block_id, **entry} for block_id, entry in zip(expected.ids, entries)]
            fields = {**read.stat(), "missing_since": 0, "problem": "", "ours": 0, **(extra or {}),    # the person's bytes now
                      "gone": store.with_gone((row or {}).get("gone"), self._dropped(base, page, expected.deleted))}
            pending = {"op": "push", "token": token, "hash": read.hash, "text": read.text, "nodes": nodes,
                       "page_id": page_id, "at": page.seq, "fields": fields, "wrote": _wrote(expected.ops)}
            store.put_pending(self.id, path, read.kind, pending)
            try:
                _before, seq, edit = self.remote.apply(page_id, token=token, built=(page.seq, expected))
                break
            except (Changed, Gone) as e:
                store.drop(self.id, path, token)
                if isinstance(e, Gone):
                    raise Waiting("the page went while its note was read") from None
            except BaseException as e:
                if self._plan_landed(page_id, pending) is False:
                    store.drop(self.id, path, token)
                    if row is not None and _refused(e):      # not again until the file changes
                        self._put(path, row, **read.stat(), problem=_why(e))
                raise
        else:
            raise Waiting("the page kept changing while its note was read; it is read again")
        # An older client's highlights forced in, two blocks of the file for one of the page: the note is as the
        # client wrote it now, and pull writes it once more by the lens, its bytes to the history.
        unknown = {"text": "", "nodes": None, "seq": -1} if folded else {}
        store.promote(self.id, path, token, **{**fields, "seq": row["seq"] if row else 0, **unknown})
        self._promoted(path)
        self._known_note(path, page_id, nodes)
        self._settled(page_id, path, read.text, doc, seq, edit.level and not folded)
        if placing:
            self._find_highlights(page_id)
        self.counts["sent"] += 1
        if not edit.resolved:
            self.counts["conflicts"] += len(edit.conflicts)

    def _older_tree(self, page: Page, projected: list, doc: md_lens.Doc, entries: list, props: dict, ctx):
        """A force push's file tree, and its blocks' places in the page's
        projection, for a note whose base names none of its blocks (an older
        folder's): the file's blocks paired with the page's as pull would
        write them (``md_lens.match``), so that a block the note kept keeps its
        id and its properties, a highlight its quote, rectangles and colour;
        and the older client's highlight read as it wrote one, the callout
        then its note as the paragraph after, where the page's highlight it
        pairs with has a note. → (tree, {file index: projected index}, the
        places of the paragraphs read so)."""
        pairs = md_lens.match(md_lens.flatten(self._file_tree(page, projected, None, ctx, {})),
                              md_lens.flatten(md_lens.tree_of(doc)))
        flat, entries, folded = md_lens.flatten(projected), list(entries), set()
        for n, node in enumerate(doc.flat):
            g, k = entries[n]["g"], pairs.get(n)
            siblings = node.parent.children if node.parent else doc.nodes
            at = next(i for i, s in enumerate(siblings) if s is node) + 1
            note = siblings[at] if at < len(siblings) else None
            if (isinstance(g, dict) and not g["note"] and k is not None and page.blocks[flat[k]["id"]]["content"]
                    and note is not None and not node.children and not note.children and note.index not in pairs
                    and block_class(note.content) == "plain"):
                entries[n] = {**entries[n], "g": {**g, "note": _text(entries[note.index]["g"])}}
                folded.add(note.index)
        place = {i: k for k, i in enumerate(i for i in range(len(doc.flat)) if i not in folded)}
        tree = _gamma_tree(doc, entries, props=props, skip=folded)
        return tree, {place[n]: k for n, k in pairs.items() if n in place}, folded

    def _file_labels(self, page_id: str, doc: md_lens.Doc, base: Base | None) -> list | None:
        """The ids of the labels the page takes with the push of its note when
        the note's ``tags`` changed since the base (``_labels_with``: Gamma's
        own stay), made first where missing (a push that then does not land
        leaves an unused label); None when they did not change. They go in the
        plan with the blocks, so that no round that dies in between leaves a
        base that names a tag the page does not have, which the next pull
        would take out of the file."""
        tags = _tags(doc.front.fields if doc.front else {})
        was = _tags(base.doc.front.fields if base and base.doc.front else {})
        if tags == was:
            return None
        info = self.remote.layout(page_id).pages.get(page_id)
        names = _labels_with(info.labels if info else was, was, tags)
        return self.remote.label_ids(names) if names else []

    def _settled(self, page_id: str, path: str, text: str, doc: md_lens.Doc, seq: int, level: bool) -> None:
        """After a push of the note at ``path``: the base takes the page's seq
        only when the page then says what the file says, its blocks
        (``level``) and its front matter, its labels among it (they went with
        the blocks, ``_file_labels``), so that pull writes a title, a label or
        an alias Gamma gave it."""
        if level and doc.front is not None:      # a note with none never takes Gamma's title, labels or aliases
            info = self.remote.layout(page_id).pages.get(page_id)
            level = info is not None and info.seq == seq and self._front(text, info, new=False) == text
        if not level:
            return
        store.put(self.id, path, seq=seq)
        if path in self.rows:
            self.rows[path]["seq"] = seq

    def _follow(self, moved: dict) -> None:
        """A block Gamma moved to another page whose text the file changed
        (``Merged.away``): the change goes to that page, merged into what it
        holds there (``merge.file_wins``; a clash the file wins, Gamma's
        text a conflict of that page's), before this note's page is written,
        so that a round that dies in between, or a plan made again, follows it
        again to the same text: ``file_wins`` makes a change once that the
        block holds already. The line then leaves this note's file, its block
        living there."""
        page_id = self.remote.page_of(moved["block_id"])
        if page_id is None:
            return

        def plan(page, _held):
            block = page.blocks.get(moved["block_id"])
            if block is None:
                return Edit([])
            text, clean = merge.file_wins(moved["base"], moved["file"], block["content"])
            if text == block["content"]:
                return Edit([])
            conflicts = [] if clean else [{"block_id": moved["block_id"], "mine": block["content"], "theirs": text,
                                           "result": text, "base": moved["base"]}]
            return Edit([{"op": "set", "id": moved["block_id"], "content": text}], conflicts)
        self.remote.apply(page_id, plan)

    @staticmethod
    def _front_ops(page: Page, doc: md_lens.Doc, base: Base | None, labels=None) -> list:
        """The page's title and aliases, when the note's front matter changed
        them since the base, and its labels, ``labels`` their ids
        (``_file_labels``; None: as they are)."""
        now = doc.front.fields if doc.front else {}
        was = base.doc.front.fields if base and base.doc.front else {}
        root = page.blocks[page.id]
        op = {"op": "set", "id": page.id}
        if now != was:
            title, props, _labels = _front_props(now, root["content"])
            if now.get("title") and now.get("title") != was.get("title") and title != root["content"]:
                op["content"] = title
            if now.get("aliases") != was.get("aliases") and props.get("aliases") != root["props"].get("aliases"):
                op["props"] = {"aliases": props.get("aliases")}
        for labelled in _label_ops(page, labels):
            op["props"] = {**op.get("props", {}), **labelled["props"]}
        return [op] if len(op) > 2 else []

    @staticmethod
    def _dropped(base: Base | None, page: Page, deleted: list) -> list:
        """The blocks the file dropped as ``gone`` keeps them: the hash of
        their file text, their anchor, their properties (a highlight's quote,
        page and rectangles). One the base never held (a force push takes
        Gamma's blocks too) has its text in Gamma and, for a highlight, the
        anchor pull would have given it."""
        at = {block_id: i for i, block_id in enumerate(base.ids)} if base else {}
        out = []
        for block_id in deleted:
            block = page.blocks.get(block_id)
            if block is None:
                continue
            if block_id in at:
                node = base.doc.flat[at[block_id]]
                text, anchor = node.content, node.anchor
            else:
                g = _g(block, page.id)
                text, anchor = _text(g), _marker(block_id) if isinstance(g, dict) else ""
            out.append({"id": block_id, "hash": md_lens.content_hash(text), "anchor": anchor, "props": block["props"]})
        return out

    # --- push: new files ----------------------------------------------------------------------

    def _push_new(self, paths: list) -> None:
        """New files: one a page in Recently deleted had brings it back
        (``disk.came_back``'s evidence: where it lived, or by its file
        id), and so does one of a page of the vault whose file went to the
        trash, but never into a page that left the vault; the rest are pages,
        a PDF and the note that pairs with it as one, then a work folder's
        files of other formats (before the notes, so that a note made in the
        same round links a picture's row), and a note of a paper that has a
        page already its notes. A note whose ``gamma_id`` names a page is
        that page's before any pairing: one an older client wrote beside its
        paper (its notes turned off since) holds the page's blocks already,
        and merged from no base it would add each again. A file the remote
        refuses waits until it changes (``_making``). A file at the place of
        an in-place tombstone (a file the selection left, ticked again) is
        that page back first, whatever its bytes."""
        tombs = [t for t in store.tombstones(self.id) if t["page_id"] and not t["pending"]]
        kept = {t["was"].casefold(): t for t in tombs if store.in_place(t)}
        restored = set()
        for path in paths:
            tomb = kept.get(path.casefold())
            if tomb is not None and self.remote.state(tomb["page_id"]) is not None:
                self._counted(path, self._unbury, path, tomb)
                restored.add(path)
        tombs = [t for t in tombs if not store.in_place(t)]
        left = [p for p in paths if p not in restored]
        back = disk.came_back([self._known(t["was"], t) for t in tombs],
                                    [self._known_read(self.reads[p]) for p in left]) if tombs else {}
        for path, k in back.items():
            page_id = tombs[k]["page_id"]
            state = self.remote.state(page_id)
            if state == "trashed" or state == "live" and page_id in self.remote.layout(page_id).pages:
                self._counted(path, self._unbury, path, tombs[k])
                restored.add(path)
        left = [p for p in paths if p not in restored]
        pdfs = [p for p in left if self.reads[p].kind == kinds.PDF.name]
        notes = [p for p in left if self.reads[p].kind == kinds.NOTES.name]
        partner, attach = {}, {}
        for note in notes:
            pdf = self._pdf_of(note, pdfs)
            if pdf in pdfs and pdf not in partner:
                partner[pdf] = note
            elif (pdf in self.rows and self.rows[pdf]["page_id"]
                  and kinds.NOTES.name not in self.rows.of(self.rows[pdf]["page_id"])):
                attach[note] = pdf
        for pdf in pdfs:
            self._counted(pdf, self._making, self.reads[pdf], kinds.PDF.new, self, pdf, self.reads[pdf], partner.get(pdf),
                          n=1 + (pdf in partner))
        for path in (p for p in left if self.reads[p].kind == kinds.FILE.name):
            self._counted(path, self._making, self.reads[path], kinds.FILE.new, self, path, self.reads[path])
        for note in (p for p in notes if p not in partner.values()):
            named = self._named_page(note)      # first: an older client's note beside its paper is no new one
            if named:
                self._counted(note, self._adopt, note, named)
            elif note in attach:
                self._counted(note, self._attach_note, note, attach[note])
            else:
                self._counted(note, self._making, self.reads[note], kinds.NOTES.new, self, note, self.reads[note])
        for note, value in sorted(self.parents.items()):        # once every new note is a page: a parent may be one
            self._each(note, self._sub_page, note, value)

    def _making(self, read: Read, make, *args) -> None:
        """``make(*args)``, a page made of the new file ``read``: one the
        remote refused keeps a row of no page with the file's stat and why,
        so that it is read and sent again once it changes, never each round
        (a 300 MB PDF behind a proxy's 1 MB limit). A page made without its
        blocks keeps its journal instead (``_recover``)."""
        try:
            make(*args)
        except (disk.Stopped, disk.Offline, Refused, Waiting, md_lens.Unwritable):
            raise
        except Exception as e:
            if store.get(self.id, read.path) is None:
                store.put(self.id, read.path, kind=read.kind, hash="", problem=_why(e), **read.stat())
            raise

    def _sub_page(self, path: str, value) -> None:
        """A new note whose front matter names its ``parent`` (``[[Plan]]``, a
        path), a note of the vault: its page that note's page's sub-page, as
        the vault import makes it, filed where its top is in Gamma while its
        file stays where the person keeps it (the rows record that filing, so
        pull moves nothing). A loop of parents leaves it a library page."""
        row = self.rows.get(path)
        notes = [p for p, r in self.rows.items() if r["kind"] == kinds.NOTES.name and r["page_id"] and p != path]
        hit = links.named(value, path, notes) if row and isinstance(value, str) else ""
        parent = self.rows[hit]["page_id"] if hit else ""
        if not parent or parent == row["page_id"] or parent not in self.remote.layout(parent).pages:
            return
        try:
            before, after = self.remote.set_page(row["page_id"], props={PARENT_PAGE: parent})
        except OpError as e:
            if e.conflict != CYCLE:
                raise
            return
        layout = self.remote.layout(row["page_id"])
        info = layout.pages.get(row["page_id"])
        folder = self._first(info.folders, layout.folders) if info else row["folder"]
        for at, at_row in self.rows.of(row["page_id"]).values():
            self._put(at, at_row, folder=folder, seq=after if at_row["seq"] == before else at_row["seq"])

    def _unbury(self, path: str, tomb: dict) -> None:
        """A file whose page is in Recently deleted came back: the page too.
        Its row is this file's now. The copy the vault's trash kept of it goes
        when it holds the same bytes; one that does not (a note come back
        edited) keeps a row of no page, for the sweep to take once the trash
        has held it ``disk.TRASH_DAYS`` (``_sweep``)."""
        self.remote.restore(tomb["page_id"])
        store.unbury(self.id, tomb["path"], path)
        kinds.by_kind(tomb["kind"]).moved(self, path, store.on_disk(tomb), tomb)
        if tomb["path"].startswith(f"{disk.TRASH}/") and self._there(tomb["path"]):
            if tomb["hash"] == self.reads[path].hash:
                disk.purge(self.root, tomb["path"])
            else:
                store.put(self.id, tomb["path"], kind=tomb["kind"], hash=tomb["hash"], was=tomb["was"])
        self.rows = _Rows(store.files(self.id))
        if self.work:
            self._filed_back(tomb["page_id"], path)
        self._push_file(path, self.scan.files[path])
        self.counts["sent"] += 1

    def _filed_back(self, page_id: str, path: str) -> None:
        """A work folder's page come back with its file ``path``: filed first in
        that file's directory's folder, made where missing (a directory gone
        with its pages, back with them), then in its folders outside every
        work folder that are still there, as ``_moved`` files it."""
        folders, _parent = self.remote.filing(page_id)
        tree = self.remote.work_tree()
        want = [self._folder_for(_dir(path))] + [f for f in folders if f in tree and not tree.work_of(f)]
        if want == list(folders):
            return
        before, after = self.remote.set_page(page_id, props={FOLDERS: want})
        folder = self._first(want, self.remote.layout(page_id).folders)
        for at, at_row in self.rows.of(page_id).values():
            self._put(at, at_row, folder=folder, seq=after if at_row["seq"] == before else at_row["seq"])

    def _pdf_of(self, note: str, pdfs: list) -> str:
        """The PDF a new note is the notes of: the one its front matter's
        ``source`` names, else the one of its stem beside it, else, for a note
        in the vault's notes folder, the one of its stem anywhere ("" for
        none). New PDFs and those the rows hold count."""
        source = str(_fields(self.reads[note].text).get("source") or "").strip()
        candidates = [*pdfs, *(p for p, r in self.rows.items() if r["kind"] == kinds.PDF.name)]
        if source:
            hit = links.named(source, note, candidates)
            if hit:
                return hit
        beside = _join(_dir(note), f"{_stem(note)}.pdf").lower()
        hit = next((pdf for pdf in candidates if pdf.lower() == beside), "")
        folder = (self.marker.get("notes_folder") or "").strip("/")
        if not hit and folder and _dir(note) == folder:
            same = [pdf for pdf in candidates if _stem(pdf).lower() == _stem(note).lower()]
            hit = same[0] if len(same) == 1 else ""
        return hit

    def _paired_page(self, pdf: str) -> str:
        """The page a note is already, without a PDF, that a new PDF is the
        file of: by the note's ``source`` or its stem beside ("" for none)."""
        if self._sources is None:
            self._sources = {}
            for path, row in self.rows.items():
                if row["kind"] == kinds.NOTES.name and row["page_id"]:
                    source = str(_fields(row["text"]).get("source") or "").strip()
                    if source:
                        self._sources[path] = source
        note = next((path for path, source in self._sources.items() if links.named(source, path, [pdf])), None)
        if note is None:
            note = next((p for p in self.rows if p.lower() == _join(_dir(pdf), f"{_stem(pdf)}.md").lower()), None)
        row = self.rows.get(note) if note else None
        if row and row["kind"] == kinds.NOTES.name and row["page_id"] and kinds.PDF.name not in self.rows.of(row["page_id"]):
            return row["page_id"]
        return ""

    def _new_note(self, path: str, read: Read) -> None:
        """A note that is no page yet: a new page, titled by its stem or its
        front matter's ``title`` (no automatic title: a lookup never renames
        it), filed in its directory's folder, with its blocks; a note that
        cannot be read as text is a page with none and its reason."""
        doc = md_lens.parse(read.text) if read.text is not None else None
        title, props, labels = _front_props(doc.front.fields if doc and doc.front else {}, _stem(path))
        folder = self._folder_for(_dir(path))
        if folder:
            props[FOLDERS] = [folder]
        problem = read.problem or (doc.problem if doc else "")
        self._create(path, read, new_block_id(), title, props, labels, _row_folder(_dir(path), folder),
                     note=None if problem else (path, read, doc), problem=problem)

    def _named_page(self, note: str) -> str:
        """The page of the vault a new note's ``gamma_id`` names, when that
        page has no notes file here ("" for none)."""
        page_id = _gamma_id(self.reads[note].text)
        if not page_id or kinds.NOTES.name in self.rows.of(page_id):
            return ""
        return page_id if page_id in self.remote.layout(page_id).pages else ""

    def _adopt(self, path: str, page_id: str, read: Read | None = None) -> None:
        """A new note whose ``gamma_id`` names a page of the vault: that page's
        notes, its base naming none of its blocks (``_unknown``), kept until a
        force decides. ``read``: its stat, where this round did not read it
        (pull found it where the page's new notes would go)."""
        read = read or self.reads[path]
        store.put(self.id, path, kind=kinds.NOTES.name, page_id=page_id, hash="", text="", nodes=None, title=None,
                        folder=None, seq=-1, ours=0, **read.stat())
        self.rows = _Rows(store.files(self.id))
        self._hold(path)
        self._keep(path, UNKNOWN, True)

    def _attach_note(self, path: str, pdf: str) -> None:
        """A new note of a paper whose page has its PDF already: its blocks
        merged into that page from no base."""
        pdf_row = self.rows[pdf]
        self._merge_into(pdf_row["page_id"], path, self.reads[path], None,
                         {"title": pdf_row["title"], "folder": pdf_row["folder"], "ours": 0})

    def _new_pdf(self, path: str, read: Read, note: str | None = None) -> None:
        """A PDF that is no page yet: a page reading it where it is, with the
        note it pairs with when there is one; or the file of a page a note is
        already."""
        paired = self._paired_page(path)
        if paired:
            notes = self.rows.of(paired)[kinds.NOTES.name][1]
            token = _token()
            fields = {**read.stat(), "seq": notes["seq"], "title": notes["title"], "folder": notes["folder"], "problem": ""}
            store.put_pending(self.id, path, read.kind, {"op": "push", "token": token, "hash": read.hash,
                                                             "text": kinds.PDF.pending_text(read), "nodes": None,
                                                             "page_id": paired, "fields": fields})
            self.remote.replace_doc(paired, str(self.root / path), read.seen, path.rsplit("/", 1)[-1])
            store.promote(self.id, path, token, **fields)
            self._promoted(path)
            self.counts["sent"] += 1
            return
        page_id = new_block_id()
        note_read = self.reads.get(note) if note else None
        doc = md_lens.parse(note_read.text) if note_read and note_read.text is not None else None
        title, props, labels = _front_props(doc.front.fields if doc and doc.front else {}, _stem(note or path))
        props["doc"] = document(read.hash, name=path.rsplit("/", 1)[-1], at="path", size=read.size if self.work else None)
        folder = self._folder_for(_dir(path))
        if folder:
            props[FOLDERS] = [folder]
        self.remote.link_doc(str(self.root / path), read.seen, page_id, ".pdf")
        try:
            self._create(path, read, page_id, title, props, labels, _row_folder(_dir(path), folder),
                         note=(note, note_read, doc) if note and doc is not None and not doc.problem else None)
        except BaseException:
            if self.remote.state(page_id) is None:
                self.remote.unlink_doc(str(self.root / path), stored_name(read.hash, ".pdf"))
            raise

    def _new_file(self, path: str, read: Read) -> None:
        """A work folder's file of another format that is no page yet: a page
        reading it where it is (its document at ``path``: the format read
        with it, its extension, its size), or listing it without its bytes
        (at ``listed``) past the keep limit; titled by its leaf, filed in its
        directory's folder, never in the library by the switch
        (``kinds.FILE``)."""
        page_id, leaf = new_block_id(), path.rsplit("/", 1)[-1]
        if read.listed:
            doc = document("", name=leaf, at="listed", format=read.format, ext=read.ext, size=read.size)
        else:
            doc = document(read.hash, name=leaf, at="path", format=read.format, ext=read.ext, size=read.size)
        props = {"doc": doc}
        folder = self._folder_for(_dir(path))
        if folder:
            props[FOLDERS] = [folder]
        if not read.listed:
            self.remote.link_doc(str(self.root / path), read.seen, page_id, read.ext)
        try:
            self._create(path, read, page_id, leaf, props, [], _row_folder(_dir(path), folder))
        except BaseException:
            if not read.listed and self.remote.state(page_id) is None:
                self.remote.unlink_doc(str(self.root / path), stored_name(read.hash, read.ext))
            raise

    def _create(self, path: str, read: Read, page_id: str, title: str, props: dict, labels: list, folder: str, *,
                note=None, problem: str = "") -> None:
        """A new page, in the journal first: the file ``path`` its note, its
        PDF or, in a work folder, its file of another format, ``note`` (path,
        read, doc) the note that pairs with a PDF. A work folder's is its
        file: stamped with the work folder and the file's name, in the library
        while the vault's switch says so and its class joins it
        (``Kind.in_library``: notes and PDFs), and never a sub-page."""
        token = _token()
        tree, nodes = [], []
        cls = kinds.by_kind(read.kind)
        pdf = path if cls is kinds.PDF else ""
        if self.work:
            props = {**props, "work_folder": self.link["folder_id"], "file_name": path.rsplit("/", 1)[-1]}
            if self.work["library"] and cls.in_library:
                props["library"] = True
        if note is not None:
            notes_path, notes_read, notes_doc = note
            ctx = self._ctx_at(notes_path, page_id, pdf)
            if pdf:
                ctx.add_file(pdf, stored_name(read.hash, ".pdf"))     # its callouts link the paper this page is made of
            entries, by_place = [], {}
            for n, node in enumerate(notes_doc.flat):
                g, memo = links.to_gamma(node.content, {}, ctx)
                entries.append({"g": g, **({"m": memo} if memo else {}), **({"a": node.anchor} if node.anchor else {})})
                if isinstance(g, dict):
                    by_place[n] = _new_props(g)
            fits(_text(e["g"]) for e in entries)
            ids = [f"{token}{n:x}" for n in range(len(notes_doc.flat))]
            tree = [_insertable(node) for node in _gamma_tree(notes_doc, entries, ids, by_place)]
            nodes = [{"id": block_id, **entry} for block_id, entry in zip(ids, entries)]
            if notes_doc.front and notes_doc.front.fields.get("parent") and not self.work:
                self.parents[notes_path] = notes_doc.front.fields["parent"]     # once every new page is made
        files = {path: (read, cls.pending_text(read))}
        if note is not None and note[0] != path:
            files[note[0]] = (note[1], note[1].text)
        stamp = {"seq": 0, "title": title, "folder": folder, "ours": 0}
        pendings = {}
        for at, (r, text) in files.items():
            notes = kinds.by_kind(r.kind) is kinds.NOTES
            pendings[at] = {
                "op": "create", "token": token, "hash": r.hash, "text": None if notes and problem else text,
                "nodes": (None if problem else nodes) if notes else None, "page_id": page_id,
                "fields": {**r.stat(), **stamp, "problem": problem if notes else ""}}
            store.put_pending(self.id, at, r.kind, pendings[at])
        try:
            seq = self.remote.create(page_id, title, props, tree, labels)
        except BaseException as e:
            made = self.remote.state(page_id) is not None
            for at in files:
                if not made:
                    store.drop(self.id, at, token)
                elif isinstance(e, Exception):      # refused after the page was made: it stays the note's
                    self._settle_create(at, pendings[at])
            raise
        for at, (r, _text_) in files.items():
            store.promote(self.id, at, token, **{**r.stat(), **stamp, "seq": seq,
                                                      "problem": problem if kinds.by_kind(r.kind) is kinds.NOTES else ""})
            self._promoted(at)
        if note is not None:
            self._known_note(note[0], page_id, nodes)
            if any(isinstance(e["g"], dict) for e in nodes):
                self._find_highlights(page_id)
        if problem:
            self._keep(path, problem)
        self.counts["sent"] += 1

    # --- push: removed files -------------------------------------------------------------------

    def _apply_removals(self, plan: dict) -> None:
        """Files gone from two scans and gone still (``lstat``): a page whose
        every file went goes to Recently deleted, its rows kept as tombstones;
        a PDF gone leaves its page reading a missing file; a paper's notes gone
        take their plain blocks, its highlights staying. A directory gone goes
        as a folder only when no page is filed in it; a work folder's
        directory its ``.gammaignore`` leaves out is not gone."""
        by_page = {}
        for path in plan["removed"]:
            if not self._gone(path):
                store.found_again(self.id, [path])
                continue
            by_page.setdefault(self.rows[path]["page_id"], []).append(path)
        for page_id, paths in by_page.items():
            self._each(paths[0], self._removed, page_id, paths)
        for path, row in list(self.rows.items()):      # a kept folder's picture gone: its row, no page's, goes with it
            if not kinds.by_kind(row["kind"]).compared and path not in self.scan.images and not self._there(path):
                store.forget(self.id, path)
                self.rows.pop(path, None)
        if not self.push_all:
            return
        recorded = store.dirs(self.id)
        live = list(store.files(self.id))
        for directory in sorted((d for d in recorded if d not in self.scan.dirs), key=len, reverse=True):
            if any(p.startswith(f"{directory}/") for p in live) or self.work and self._there(directory):
                continue
            folder_id = recorded[directory]["folder_id"]
            if not folder_id or self.remote.drop_folder(folder_id):
                store.forget_dir(self.id, directory)

    def _removed(self, page_id: str, paths: list) -> None:
        stays = [p for _kind, (p, _row) in self.rows.of(page_id).items() if p not in paths]
        if not page_id or self.remote.state(page_id) is None:
            for path in paths:
                store.forget(self.id, path)
                self.rows.pop(path, None)
            return
        if not stays:
            when, token, buried, taken = disk.stamp(), _token(), [], self._tomb_paths()
            for path in paths:
                store.put_pending(self.id, path, self.rows[path]["kind"], {"op": "trash", "token": token, "disk": True})
                trash = disk.trash_path(self.root, path, when, taken)
                store.bury(self.id, path, trash)
                buried.append(trash)
                taken.add(trash)
            self.remote.trash(page_id)
            for trash in buried:
                store.put(self.id, trash, pending=None)
            for path in paths:
                self.rows.pop(path, None)
            self.counts["removed"] += len(paths)
            return
        for path in paths:
            row = self.rows.pop(path)
            if kinds.by_kind(row["kind"]) is kinds.NOTES:
                self._notes_gone(page_id, row)
            store.forget(self.id, path)
            self.counts["removed"] += 1

    def _apply_deselections(self, plan: dict) -> None:
        """Files there that the vault's ``.gammaignore`` leaves out now: in a
        work folder each page whose files all left goes to Recently deleted,
        its files where they are (``_deselected``); in a kept folder, whose
        source Gamma is, the row is forgotten and the page stays."""
        by_page = {}
        for path in plan["deselected"]:
            row = self.rows.get(path)
            if row is None:
                continue
            if not self.work:
                store.forget(self.id, path)
                self.rows.pop(path, None)
                continue
            by_page.setdefault(row["page_id"], []).append(path)
        for page_id, paths in by_page.items():
            self._each(paths[0], self._deselected, page_id, paths)

    def _deselected(self, page_id: str, paths: list) -> None:
        """A work folder's page whose files ``paths`` the selection left: with
        every file of the page gone, the page to Recently deleted first, then
        each row an in-place tombstone (``store.bury_in_place``), the
        file never moved, so that a round that dies in between leaves a live
        row the next deselects again, or a tombstone of a live page, which
        ``_restore_file`` sends back. One of two files left out (a hand edit:
        the dialog keeps a paper's two together) is forgotten, its page
        keeping the other."""
        stays = [p for _kind, (p, _row) in self.rows.of(page_id).items() if p not in paths]
        if page_id and stays:
            for path in paths:
                store.forget(self.id, path)
                self.rows.pop(path, None)
                self._keep(path, LEFT_OUT)
            return
        if page_id and self.remote.state(page_id) is not None:
            self.remote.trash(page_id)
            for path in paths:
                store.bury_in_place(self.id, path)
                self.rows.pop(path, None)
        else:
            for path in paths:
                store.forget(self.id, path)
                self.rows.pop(path, None)
        self.counts["removed"] += len(paths)

    def _notes_gone(self, page_id: str, row: dict) -> None:
        """A paper's notes file gone, its PDF left: the plain blocks the file
        held, unchanged since, go (kept in the PDF's row's ``gone``), the
        highlights stay, and no notes file is made until the page changes."""
        base = _base(row)
        if not base:
            return
        texts = dict(zip(base.ids, (_text(n["g"]) for n in base.nodes)))
        kind_of = _kind_of(page_id)

        def plan(page, _held):
            doomed = {b for b, text in texts.items() if b in page.blocks and kind_of(page.blocks[b]) == "note"
                      and page.blocks[b]["content"] == text}
            ops = []
            for b, block in page.blocks.items():
                if b in doomed or b == page_id or block["parent"] not in doomed:
                    continue
                up = block["parent"]
                while up in doomed:
                    up = page.blocks[up]["parent"]
                ops.append({"op": "move", "id": b, "parent": up})
            ops += [{"op": "delete", "id": b} for b in sorted(doomed) if page.blocks[b]["parent"] not in doomed]
            return Edit(ops, deleted=sorted(doomed))
        page = self.remote.page(page_id)
        before, seq, edit = self.remote.apply(page_id, plan)
        pdf = self.rows.of(page_id).get(kinds.PDF.name)
        if pdf and page:
            self._put(*pdf, gone=store.with_gone(pdf[1].get("gone"), self._dropped(base, page, edit.deleted)),
                      seq=seq if row["seq"] == before else pdf[1]["seq"])

    # --- pull: Gamma's changes ---------------------------------------------------------------------

    def _pull(self) -> None:
        """Gamma's changes onto the disk: folders renamed (never in a work
        folder, whose directories follow the disk), pages that left or went
        to Recently deleted or came back, each page that changed (and, once
        the labels tree changed, each note with tags: a label renamed moves no
        page's seq), and the pages a link asked an anchor of, once more."""
        self.layout = self.remote.layout(self.only)
        if self.only:
            if self.only in self.layout.pages:
                self._tombstones({self.only})
                self._each(self._name_of(self.only), self._pull_page, self.layout.pages[self.only])
            elif self.rows.of(self.only):
                self._each(self._name_of(self.only), self._left, self.only)
            return
        self.cursor = self.layout.cursor
        if self.layout.shared:       # filed below an earlier vault beside this one too: that vault's
            self.status["shared"] = [{"page": page_id, "title": title}
                                     for page_id, title in self.layout.shared[:KEPT_SHOWN]]
        self.relabelled = self.state.get("labels") != self.layout.labels
        self.state["labels"] = self.layout.labels
        if not self.work:
            self._pull_dirs()
        for page_id in sorted({row["page_id"] for row in self.rows.values() if row["page_id"]} - set(self.layout.pages)):
            self._each(self._name_of(page_id), self._left, page_id)
        self._tombstones(set(self.layout.pages))
        self.total += len(self.layout.pages)
        for info in self.layout.pages.values():
            name = self._name_of(info.id)
            self._report(info.title if name == info.id else name)     # a page with no file yet: its title
            self._each(name, self._pull_page, info)
            self.done += 1
        for _ in range(2):
            again, self.again = self.again, set()
            for page_id in sorted(again & set(self.layout.pages)):
                self._each(self._name_of(page_id), self._pull_page, self.layout.pages[page_id], True)
        self._drop_dirs()
        self._report()

    def _drop_dirs(self) -> None:
        """The directories of folders gone from the vault in Gamma (deleted, or
        moved out of it): forgotten, and removed once their pages left them
        empty. A directory still holding a file stays, a person's, and so does
        a link or a junction put in its place (``disk.path``)."""
        for path, entry in sorted(store.dirs(self.id).items(), key=lambda d: -d[0].count("/")):
            if entry["folder_id"] in self.layout.folders:
                continue
            try:
                os.rmdir(disk.path(self.root, path))
            except OSError:
                pass      # gone already, holding a file, or a link (Outside)
            store.forget_dir(self.id, path)

    def _name_of(self, page_id: str) -> str:
        """The file a page's failure is reported under: its notes, which a
        person edits and pull writes, where it has some, whatever order the
        rows come in (a note renamed only in case sorts after its PDF)."""
        rows = self.rows.of(page_id)
        return (rows.get(kinds.NOTES.name) or rows.get(kinds.PDF.name) or next(iter(rows.values())))[0] if rows else page_id

    def _dir_of(self, folder_id: str) -> str:
        """The directory of a folder of the vault: the one recorded, else its
        names below the vault's folder, each one every disk takes."""
        if not folder_id or folder_id not in self.layout.folders:
            return ""
        recorded = {d["folder_id"]: path for path, d in store.dirs(self.id).items() if d["folder_id"]}
        if folder_id in recorded:
            return recorded[folder_id]
        parent, name = self.layout.folders[folder_id]
        return _join(self._dir_of(parent), _name(name))

    def _pull_dirs(self) -> None:
        """A folder renamed or moved in Gamma renames or moves its directory.
        A directory is its folder's name while the two give one vault name
        (``_name``): ``[Archive]`` and ``My  Notes`` keep theirs, and a folder
        moved takes its directory's name along."""
        for path in sorted(store.dirs(self.id), key=lambda p: p.count("/")):
            current = store.dirs(self.id)
            folder_id = (current.get(path) or {}).get("folder_id")
            if not folder_id or folder_id not in self.layout.folders or not os.path.isdir(self.root / path):
                continue
            parent, name = self.layout.folders[folder_id]
            own = path.rsplit("/", 1)[-1]
            want = _join(self._dir_of(parent), own if _name(own) == _name(name) else _name(name))
            if want != path:
                self._each(path, self._move_dir, path, disk.free_name(self.root, want, own=path))

    def _move_dir(self, at: str, want: str) -> None:
        disk.move(self.root, at, want)
        store.repath(self.id, at, want)
        self.remote.relink(str(self.root / at), str(self.root / want))
        self.rows = _Rows(store.files(self.id))
        self.counts["renamed"] += 1

    def _left(self, page_id: str) -> None:
        """A page the vault has files of that is no page of it now (in Recently
        deleted, filed elsewhere, gone for good): its files go to the vault's
        trash (``_to_trash``), a PDF read in place stored first when the page
        lives on, the page keeping everything. A note changed on disk since
        its base is the disk's word, which push has not read yet (a round of
        pull alone, a note still being written): the page's files stay and
        the note is kept, so that push brings the page back with it, or makes
        it a page again; a force pull keeps its bytes in the history first.
        A PDF changed on disk since its base (a person's annotated copy in a
        vault that does not push it) is the person's: it stays where it is,
        no longer synced, and is reported (``LEFT``), as the older client
        left one; never moved or deleted. In a work folder only the tracked
        note of a page that follows goes to the trash (a note a person
        trashed where the vault pulls); any other file, a paper's notes among
        them, stays where it is, its row an in-place tombstone: Gamma never
        moves a file the disk made. Each class goes first (``Kind.left``: the
        note kept, the PDF stored or left), the notes before the PDF."""
        rows = self.rows.of(page_id)
        if not rows:
            return
        for cls in kinds.CLASSES.values():
            at = rows.get(cls.name)
            if at and not cls.left(self, page_id, *at):
                return
        paths = [p for p, _row in self.rows.of(page_id).values()]
        if self.work:
            follows = self.work["pull"] and kinds.PDF.name not in self.rows.of(page_id)
            for path in [p for p in paths
                         if self.rows[p]["kind"] != kinds.NOTES.name or not follows or not self._tracked(p)]:
                store.bury_in_place(self.id, path)
                self.rows.pop(path, None)
                paths.remove(path)
                self.counts["removed"] += 1
        self._to_trash(paths)

    def _pdf_changed(self, path: str, row: dict) -> bool:
        """Whether the vault's PDF at ``path`` holds other bytes than its base
        says: its stat moved and its hash is another (one not there, or not
        to be read, is not)."""
        found = self._stat(path)
        if found is None or found.placeholder or not disk.moved(row, found):
            return False
        try:
            return linked_files.hash_file(disk.path(self.root, path))[0] != row["hash"]
        except (OSError, ValueError):
            return False

    def _to_trash(self, paths: list) -> None:
        """Files moved to ``.gamma/trash/<stamp>/``, each row there first and
        kept there, a tombstone: the file comes back when its page comes back
        into the vault and is swept when the page is purged, thirty days on at
        the earliest (``_tombstones``, ``_sweep``), so the trash holds no file
        that no row holds."""
        when, taken = disk.stamp(), self._tomb_paths()
        for path in paths:
            row, there = self.rows[path], not self._gone(path)
            token = _token()
            trash = disk.trash_path(self.root, path, when, taken)
            taken.add(trash)
            store.put_pending(self.id, path, row["kind"], {"op": "trash", "token": token})
            store.bury(self.id, path, trash)
            if there:
                disk.move(self.root, path, trash)
                self.remote.relink(str(self.root / path), str(self.root / trash))
            store.put(self.id, trash, pending=None)
            self.rows.pop(path, None)
            self.counts["removed"] += 1

    def _tomb_paths(self) -> set:
        """The paths the tombstones hold, a file in the trash or none: no new
        trash path takes one."""
        return {tomb["path"] for tomb in store.tombstones(self.id)}

    def _tombstones(self, pages: set) -> None:
        """Tombstones whose page came back (its files come back from the trash
        under a free name) or went for good (the file with it, once the trash
        has held it long enough and another page that reads a PDF of it in
        place has it stored: ``_sweep``, ``spare``)."""
        for tomb in store.tombstones(self.id):
            if tomb["pending"] or (self.only and tomb["page_id"] != self.only):
                continue
            if tomb["page_id"] in pages:
                self._each(tomb["was"], self._restore_file, tomb)
            elif self.remote.state(tomb["page_id"]) is None:
                self._each(tomb["was"], self._sweep, tomb)

    def _sweep(self, tomb: dict) -> None:
        """A tombstone whose page went for good (or that has none: a copy a
        file come back left, ``_unbury``), its file in the trash with it once
        the trash has held it ``disk.TRASH_DAYS`` (a stamp that says no
        time never: the file stays). An in-place tombstone's file stays where
        it is; a PDF its row read for another page too is stored for that one
        first, as one the trash takes is (``Kind.swept``)."""
        cls = kinds.by_kind(tomb["kind"])
        if store.in_place(tomb):
            if self._there(tomb["was"]):
                cls.swept(self, tomb, tomb["was"])
        elif tomb["path"].startswith(f"{disk.TRASH}/") and self._there(tomb["path"]):
            at = disk.trashed_at(tomb["path"])
            if at is None or time.time() - at < disk.TRASH_DAYS * 86400:
                return
            cls.swept(self, tomb, tomb["path"])
            disk.purge(self.root, tomb["path"])
        store.forget(self.id, tomb["path"])

    def _restore_file(self, tomb: dict) -> None:
        """A file of a page brought back, from the trash where it went; one
        the trash does not hold (it went from the disk) is written anew, but
        where a file is back at the place it lived and this round cannot read
        it (a page's round, or one of pull alone, in a vault that pushes
        everything): that may be the page's own, so the page waits for a
        round that reads the disk (``buried``), never writing another file
        beside it. An in-place tombstone's file is where it was: tracked, its
        row is that file's again, nothing moved. Untracked, in a work folder
        whose page has no other file tracked and there, the page goes back to
        Recently deleted, where a work folder's page whose file the vault
        does not track is; one with its other file is kept by that one, and in
        a kept folder (a work folder's push lowered since) the page stays
        Gamma's: either way the tombstone is forgotten. A place the rules
        leave out is not written to."""
        if store.in_place(tomb):
            page_id, was = tomb["page_id"], tomb["was"]
            if self._tracked(was) and self._there(was):
                store.unbury(self.id, tomb["path"], was)
                self.rows = _Rows(store.files(self.id))
            elif self.work and not self._kept_by(page_id, tomb["path"]):
                self.remote.trash(page_id)
                self.buried.add(page_id)
            else:
                store.forget(self.id, tomb["path"])
                if self.work:
                    self._keep(was, LEFT_OUT)
            return
        if self._gone(tomb["path"]):
            if self.push_all and (self.scan is None or not self.pushing) and self._there(tomb["was"]):
                self.buried.add(tomb["page_id"])
                raise Waiting("a file is back where its page's lived; the next round reads it")
            store.forget(self.id, tomb["path"])
            return
        if not self._tracked(tomb["was"]):
            self._not_tracked(tomb["was"], tomb["page_id"])
            return
        target = self._free(tomb["was"])
        store.put(self.id, tomb["path"], pending={"op": "restore", "token": _token(), "path": target})
        disk.move(self.root, tomb["path"], target)
        self.remote.relink(str(self.root / tomb["path"]), str(self.root / target))
        store.unbury(self.id, tomb["path"], target)
        store.put(self.id, target, pending=None)
        self.rows = _Rows(store.files(self.id))
        self.counts["added"] += 1

    def _kept_by(self, page_id: str, tomb_path: str) -> bool:
        """Whether a page keeps a file other than the in-place tombstone at
        ``tomb_path``: a row of it, or another in-place tombstone of it whose
        file is tracked and there, which this round reads back."""
        if self.rows.of(page_id):
            return True
        return any(t["page_id"] == page_id and t["path"] != tomb_path and store.in_place(t)
                   and self._tracked(t["was"]) and self._there(t["was"])
                   for t in store.tombstones(self.id))

    # --- pull: one page ---------------------------------------------------------------------------

    def _pull_page(self, info: Info, again: bool = False) -> None:
        """One page of the vault onto the disk, when it changed since the base:
        where its files are first, a stored PDF it has and the vault has not
        copied out (a page made in Gamma, a PDF attached there to a page of
        notes) or has replaced since, then its notes. A PDF of the disk's that
        Gamma replaced is that file's problem: pull never writes it. A page
        whose file a tombstone waits to read back (``buried``) waits too. A
        work folder's PDFs are the disk's: pull copies none out, and says so
        of a stored PDF filed there; a document of another format is never
        copied out (a work folder's file is the disk's, a kept folder writes
        notes and PDFs alone). Then the page's main class pulls it
        (``Kind.pull``: a note's and a paper's write its notes, a file's
        nothing)."""
        if info.id in self.buried:
            return
        folder = self._first(info.folders, self.layout.folders)
        rows = self.rows.of(info.id)
        if rows:
            self._place(info, rows, folder)
            rows = self.rows.of(info.id)
        notes, pdf = rows.get(kinds.NOTES.name), rows.get(kinds.PDF.name)
        row = (notes or pdf or next(iter(rows.values()), (None, None)))[1]
        if (row is not None and not again and self.force != "pull" and info.seq == row["seq"]
                and not (self.relabelled and notes and _tagged(notes[1]["text"]))):
            self.counts["unchanged"] += 1
            return
        if info.doc_id and info.format == "pdf" and (not pdf or pdf[1]["hash"] != info.doc_id):
            if self.work and not pdf:
                self._keep(self._name_of(info.id), "a PDF filed here in Gamma is not written into the directory")
            elif not self.work and (not pdf and not info.linked or pdf and pdf[1]["ours"]):
                self._copy_pdf(info, folder, notes, pdf)
            elif pdf:
                self._problem(pdf[0], "its page's PDF was replaced in Gamma, and this file is the one before")
        rows = self.rows.of(info.id)
        (kinds.main(rows) or kinds.NOTES).pull(self, info, rows, folder)

    def _first(self, folders: list, inside: dict) -> str:
        """A page's first filing in the vault as its rows record it: the first
        of ``folders`` that is the vault's own folder ("": its top) or one of
        ``inside``, the folders below it; "" for none."""
        own = self.link["folder_id"]
        return next(("" if f == own else f for f in folders if f == own or f in inside), "")

    def _place(self, info: Info, rows: dict, folder: str) -> None:
        """The page's files where its title and first filing put them, as its
        main class places them (``Kind.place``; a file's are only recorded)."""
        cls = kinds.main(rows)
        if cls is not None:
            cls.place(self, info, rows, folder)

    def _place_notes(self, info: Info, rows: dict, folder: str) -> None:
        """A note's or a paper's files placed: a new title renames the note (a
        ``title:`` key is patched by the write instead), a new first filing
        moves it, and a PDF goes with the note only when it is the note's
        stem beside it: one alone is the disk's. In a work folder only a note
        that follows Gamma (no document, where the vault pulls) is renamed or
        moved, alone, and its file's name written; anything else's title and
        filing are only recorded (a library file's title is free, its file
        keeping its name). A place the rules leave out is not moved to."""
        notes, pdf = rows.get(kinds.NOTES.name), rows.get(kinds.PDF.name)
        path, row = notes or pdf
        moved = row["folder"] is not None and folder != row["folder"]      # none: an older folder's, recorded at its write
        renamed = row["title"] is not None and info.title != row["title"]
        if not moved and not renamed:
            return
        follows = not info.doc_id and bool(self.work and self.work["pull"])
        if notes and (follows or not self.work):
            if notes[0] in self.kept_paths:
                return               # changed on disk and not pushed: placed once it is the base again
            directory = self._dir_of(folder) if moved else _dir(path)
            titled = bool(_fields(notes[1]["text"]).get("title"))
            stem = _name(info.title, ".md") if renamed and not titled else _stem(notes[0])
            for kind, (at, at_row) in rows.items():
                if kind == kinds.PDF.name and (self.work or not (_stem(at) == _stem(notes[0]) and _dir(at) == _dir(notes[0]))):
                    continue
                wanted = _join(directory, stem + os.path.splitext(at)[1])
                if wanted != at and not self._tracked(wanted):
                    self._not_tracked(wanted, info.id)
                    return
                want = self._free(wanted, own=at)
                if want != at:
                    self._rename(at, want, at_row)
                    self._dirs_made(_dir(want), folder)
                    if self.work and info.work_folder:
                        self._file_named(info.id, want)
        for _kind, (at, at_row) in self.rows.of(info.id).items():
            self._put(at, at_row, title=info.title, folder=folder)

    def _file_named(self, page_id: str, path: str) -> None:
        """A work folder's file's name written (``file_name``) after pull
        wrote or renamed its note, its rows' seq following where level."""
        before, after = self.remote.set_page(page_id, props={"file_name": path.rsplit("/", 1)[-1]})
        for at, at_row in self.rows.of(page_id).values():
            if at_row["seq"] == before:
                self._put(at, at_row, seq=after)

    def _rename(self, at: str, want: str, row: dict) -> None:
        """A file renamed or moved by pull, in the journal first."""
        token = _token()
        store.put_pending(self.id, at, row["kind"], {"op": "move", "token": token, "path": want, "hash": row["hash"],
                                                         "text": row["text"], "nodes": row["nodes"], "page_id": row["page_id"]})
        try:
            disk.move(self.root, at, want)
        except BaseException:
            store.drop(self.id, at, token)
            raise
        self.remote.relink(str(self.root / at), str(self.root / want))
        store.promote(self.id, at, token)
        self.rows = _Rows(store.files(self.id))
        self.counts["renamed"] += 1

    def _copy_pdf(self, info: Info, folder: str, notes=None, pdf=None):
        """A page's stored PDF copied out, followed from then on: the one PDF
        pull writes, beside the page's notes under their stem (a pair the scan
        reads back as one page), else in its folder's directory under its
        title. ``pdf``: the copy an earlier pull made of a PDF Gamma has
        replaced since, written over while it is still what pull wrote; one
        changed on disk is the next push's, or in a vault that does not push
        PDFs kept and reported until a force pull writes Gamma's over it, and
        so is one push will not read again until it changes (a folder kept
        before whose client would have kept it: gamma/vault/upgrade.py). A
        new copy the disk holds already where it would go, or where the
        gamma-sync client kept a sub-page's (``_beside_parent``), with those
        bytes and no row, is taken (``_take``); bytes that cannot be the
        stored file wait (``_fetched``). → its ``(path, row)``."""
        if pdf:
            try:
                ours = linked_files.hash_file(disk.path(self.root, pdf[0]))[0] == pdf[1]["hash"]
            except (OSError, ValueError):
                ours = False
            if not ours and self.force != "pull":
                found = self._stat(pdf[0])
                if not self.push_pdfs:
                    self._keep(pdf[0], "changed on this computer and not pushed; a force pull replaces it", True)
                elif found is not None and not disk.moved(pdf[1], found):
                    self._keep(pdf[0], BOTH_PDF, True)
                return pdf
        directory = _dir(notes[0]) if notes else self._dir_of(folder)
        wanted = _join(directory, f"{_stem(notes[0]) if notes else _name(info.title, '.pdf')}.pdf")
        beside = "" if notes or pdf else self._beside_parent(info, ".pdf")
        held = next((at for at in (wanted, beside) if at and self._tracked(at) and self._holds_doc(at, info.doc_id)),
                    "") if not pdf else ""
        if held:
            self._take(held, kinds.PDF.name, info.id, {"hash": info.doc_id, "text": "", "title": info.title,
                                                       "folder": folder, "seq": 0, "problem": ""})
            if held == wanted:
                self._dirs_made(directory, folder)
            return held, self.rows[held]
        if not pdf and not self._tracked(wanted):
            self._not_tracked(wanted, info.id)
            return pdf
        data = _fetched(self.remote, stored_name(info.doc_id, ".pdf"))
        if data is None:
            return pdf
        path = pdf[0] if pdf else self._free(wanted)
        token = _token()
        fields = {"hash": info.doc_id, "ours": 1, "title": info.title, "folder": folder, "seq": pdf[1]["seq"] if pdf else 0,
                  "problem": ""}
        journal = self._journal(path, kinds.PDF.name, token, data, info.id, fields, "")
        if pdf:
            written = disk.replace_note(self.root, path, data, journal)
        else:
            written = disk.write_new(self.root, path, data, ready=journal)
            self._dirs_made(directory, folder)
        store.promote(self.id, path, token, **fields, size=written.size, mtime_ns=written.mtime_ns,
                            file_id=written.file_id, read_at=time.time())
        self._promoted(path)
        self.counts["updated" if pdf else "added"] += 1
        return path, self.rows[path]

    def _holds(self, rel: str, data: bytes) -> bool:
        """Whether ``rel``, a file no row holds, is there with the very bytes
        ``data`` that pull would write as a page's new notes."""
        if rel in self.rows:
            return False
        try:
            st = disk.lstat(self.root, rel)
            if st is None or not stat.S_ISREG(st.st_mode) or disk._placeholder(st) or st.st_size != len(data):
                return False
            return disk.read(self.root, rel) == data
        except OSError:
            return False

    def _holds_doc(self, rel: str, doc_id: str) -> bool:
        """Whether ``rel``, a file no row holds, is there with the bytes of
        the stored PDF ``doc_id`` that pull would copy out."""
        if rel in self.rows:
            return False
        try:
            st = disk.lstat(self.root, rel)
            if st is None or not stat.S_ISREG(st.st_mode) or disk._placeholder(st):
                return False
            return linked_files.hash_file(disk.path(self.root, rel))[0] == doc_id
        except (OSError, ValueError):
            return False

    def _take(self, rel: str, kind: str, page_id: str, fields: dict) -> None:
        """A file the disk holds already with the very bytes pull would write
        for a page that has none here (``_holds``, ``_holds_doc``: a vault made
        again over the files it kept when it was removed, a copy of the vault
        put back): taken as that page's, as pull's own, never written a second
        time beside it."""
        st = os.stat(disk.path(self.root, rel))
        store.put(self.id, rel, kind=kind, page_id=page_id, size=st.st_size, mtime_ns=st.st_mtime_ns,
                        file_id=str(st.st_ino), read_at=time.time(), ours=1, **fields)
        self._promoted(rel)
        self.counts["unchanged"] += 1

    def _dirs_made(self, directory: str, folder: str) -> None:
        """A directory a write made, recorded as its folder's."""
        if directory and directory not in store.dirs(self.id):
            try:
                file_id = str(os.stat(self.root / directory).st_ino)
            except OSError:
                file_id = ""
            store.put_dir(self.id, directory, folder, file_id)

    def _notes_path(self, info: Info, pdf, folder: str) -> str:
        """Where a page's new notes go: in the vault's notes folder or beside
        its PDF under the PDF's stem; else ``<dir>/<title>.md``. Not made
        free yet: a note there may be the page's already (``_older_note``)."""
        if pdf:
            directory = (self.marker.get("notes_folder") or "").strip("/") or _dir(pdf[0])
            return _join(directory, f"{_stem(pdf[0])}.md")
        return _join(self._dir_of(folder), f"{_name(info.title, '.md')}.md")

    def _beside_parent(self, info: Info, ext: str) -> str:
        """Where the gamma-sync client kept a sub-page's file of ``ext``: in a
        directory named for its parent's files, beside them
        (gamma/folder_sync.py); "" for a library page, or one whose parent has
        no file here. A file there may be the page's already, which pull then
        takes (``_older_note``, ``_holds``) and never writes a second time
        where it puts a new one."""
        files = self.rows.of(info.parent) if info.parent else {}
        at = (files.get(kinds.NOTES.name) or files.get(kinds.PDF.name) or ("",))[0]
        return _join(_join(_dir(at), _stem(at)), f"{_name(info.title, ext)}{ext}") if at else ""

    def _older_note(self, path: str, page_id: str) -> bool:
        """Whether the note at ``path``, which no row holds, names the page by
        its ``gamma_id``: an older client's note, kept beside its paper while
        the vault's notes were off. It is that page's notes, never one written
        beside it (``_adopt``); one the vault's rules leave out is none."""
        if path in self.rows or not self._tracked(path):
            return False
        try:
            st = disk.lstat(self.root, path)
            if st is None or disk._placeholder(st) or not os.path.isfile(disk.path(self.root, path)):
                return False
            text = disk.read(self.root, path).decode("utf-8", "replace")
        except OSError:
            return False
        if _gamma_id(text) != page_id:
            return False
        self._adopt(path, page_id, Read(path, kinds.NOTES.name, st.st_size, st.st_mtime_ns, str(st.st_ino), time.time(), ""))
        return True

    def _write_notes(self, info: Info, notes, pdf, folder: str) -> None:
        """The page's blocks with a span written over its notes file as a patch
        of the base (``md_lens.shape``); a base that names none of its blocks
        (``_unknown``) is written as a new file is, the anchors that note held
        kept. A new note the disk holds already, byte for byte, where pull
        puts it or where the gamma-sync client kept a sub-page's
        (``_beside_parent``), is taken (``_take``). A vault without notes (a
        folder kept before with its PDFs alone) writes none, its PDF level
        with the page. Pull writes no note where the vault's rules leave it
        out; a work folder's note made in Gamma takes its file's name once
        written."""
        if not self.notes:
            if pdf:
                self._put(*pdf, seq=info.seq, title=info.title, folder=folder)
            return
        forced = self.force == "pull"
        if notes and ((notes[0] in self.kept_paths and not forced) or notes[1]["text"] is None
                      or not self._tracked(notes[0])):
            return
        found = (self.scan.files.get(notes[0]) if self.scan else self._stat(notes[0])) if notes else None
        if found and found.placeholder:
            return                   # its bytes are in the cloud: never read, never written over, in a page's round too
        page = self.remote.page(info.id)
        if page is None or page.trashed:
            return
        projected = md_lens.project(page.blocks, _kind_of(info.id))
        if notes:
            path, row = notes
            base = _base(row)
            if base is None:
                raise md_lens.Unwritable("the base of this note cannot be read")
        elif pdf and not projected:
            self._put(*pdf, seq=page.seq, title=info.title, folder=folder)
            return
        else:
            wanted, row, base = self._notes_path(info, pdf, folder), None, None
            beside = "" if pdf else self._beside_parent(info, ".md")
            if any(self._older_note(at, info.id) for at in (wanted, beside) if at):
                return
            if not self._tracked(wanted):
                self._not_tracked(wanted, info.id)
                return
            path = self._free(wanted)
        current = self._current(path) if row is not None else None
        made = row is None or current is None
        if row is not None and current is None and not forced:
            return                   # gone: the scan's to tell; a force pull writes it again
        if row is not None and row["hash"] == UNREAD:
            self._first_read(path, row, current)
        if row is not None and current not in (None, row["hash"]) and not forced:
            self._changed_under(path, info)
            return
        if row is not None and current is not None and _unknown(row):
            self._older_anchors(path, page)
        ctx = self._ctx_at(path, info.id, pdf[0] if pdf else "")
        entries = {}
        tree = self._file_tree(page, projected, base, ctx, entries)
        try:
            shaped, text = md_lens.shape(base.doc if base else md_lens.parse(""), base.ids if base else [], tree)
        except md_lens.Unwritable as e:
            if row is not None or not pdf:
                raise
            self._keep(path, str(e))            # the paper's new note, by its own name; its PDF's row says why
            self._put(*pdf, problem=str(e))
            self.remote.touched.add(info.id)
            return
        text = self._front(text, info, new=row is None or _unknown(row))
        nodes, level, seq = self._shaped(page, projected, tree, shaped, entries, ctx)
        self._asked(ctx, info.id)
        self._new_files(ctx)
        data = text.encode("utf-8", "surrogatepass")
        stamp = {"seq": seq if level else (row["seq"] if row else 0), "title": info.title, "folder": folder, "problem": ""}
        held = next((at for at in (wanted, beside) if at and at != path and self._tracked(at) and self._holds(at, data)),
                    "") if row is None else ""
        if held:
            path = held
            self._take(path, kinds.NOTES.name, info.id, {"hash": disk.sha(data), "text": text, "nodes": nodes, **stamp})
            self._known_note(path, info.id, nodes)
            if path == wanted:           # beside its parent's: a sub-page's directory is no folder
                self._dirs_made(_dir(path), folder)
        elif row is not None and disk.sha(data) == current:
            self._put(path, row, hash=current, text=text, nodes=nodes, **stamp)
            self.counts["unchanged"] += 1
        else:
            if forced and not made and current != row["hash"]:
                self._record_force(info.id, path, row, base, ctx)
            self._write(path, data, text, nodes, info.id, new=made, ours=row["ours"] if row else 1, **stamp)
            self._known_note(path, info.id, nodes)
            if made:
                self._dirs_made(_dir(path), folder)
        if pdf and path in self.rows:        # a paper's two files are level with its page together
            self._put(*pdf, seq=self.rows[path]["seq"])
        elif row is None and info.work_folder and self.work and path in self.rows:
            self._file_named(info.id, path)

    def _first_read(self, path: str, row: dict, current: str) -> None:
        """An older client's note taken while a cloud placeholder held it
        (``UNREAD``), its bytes here now: its hash is the one they have while
        its stat is still the one the upgrade took, else none, a change on
        disk kept as one (gamma/vault/upgrade.py)."""
        st = disk.lstat(self.root, path)
        if st is not None and (st.st_size, st.st_mtime_ns) == (row["size"], row["mtime_ns"]):
            fields = {"hash": current, "file_id": str(st.st_ino), "read_at": time.time()}
        else:
            fields = {"hash": ""}
        self._put(path, row, **fields)

    def _older_anchors(self, path: str, page: Page) -> None:
        """The anchors a note whose base names none of its blocks (an older
        client's) holds of its page's blocks, ``^`` and the block's id
        (``obsidian_export.anchor_marker``): written again when pull writes
        the note anew, so that a link to one of those blocks, from a note
        that pull leaves or from one of the person's, still finds it. An
        anchor is never removed because a link went."""
        try:
            doc = md_lens.parse(disk.read(self.root, path).decode("utf-8", "replace"))
        except OSError:
            return
        markers = {anchor_marker(block_id).lower(): block_id for block_id in page.blocks if block_id != page.id}
        for node in doc.flat:
            block_id = markers.get((node.anchor or "").lower())
            if block_id:
                self.anchors.setdefault(block_id, node.anchor)

    def _file_tree(self, page: Page, projected: list, base: Base | None, ctx, entries: dict) -> list:
        """The projected page in the file's space: a block whose text in Gamma
        is the base's keeps its file text (identity first), another is written
        by ``links``; a highlight keeps or takes an anchor, and so does a
        block another note's link asked one of. ``entries`` gets each block's
        ``{g, m, content}``."""
        at = {block_id: i for i, block_id in enumerate(base.ids)} if base else {}

        def node_of(p):
            g = _g(page.blocks[p["id"]], page.id)
            i = at.get(p["id"])
            was = base.nodes[i] if i is not None else None
            if was is not None and was["g"] == g:
                content, memo = base.doc.flat[i].content, was.get("m") or {}
            else:
                content, memo = links.written(g, (was or {}).get("m") or {}, ctx, base=was["g"] if was else None)
            out = {"id": p["id"], "content": content, "children": [node_of(child) for child in p["children"]]}
            anchor = (base.doc.flat[i].anchor or was.get("a", "") if i is not None else "") or self.anchors.get(p["id"], "")
            anchor = anchor or (_marker(p["id"]) if isinstance(g, dict) else "")
            if anchor:
                out["anchor"] = anchor
            entries[p["id"]] = {"g": g, "m": memo, "content": content}
            return out
        tree = [node_of(p) for p in projected]
        for node in md_lens.flatten(tree):       # anchors this note's own links asked of it
            marker = ctx.new_anchors.pop(node["id"], None)
            if marker and not node.get("anchor"):
                node["anchor"] = marker
        return tree

    def _asked(self, ctx, page_id: str) -> None:
        """Anchors a note's links asked of other notes: theirs to write, as a
        change Gamma made. The ask is kept on the other note's row (its node's
        ``a``, its seq none, so the next pull of it writes it) before this
        note is written, and that note is written this round when it can be.
        A note whose base names none of its blocks (``_unknown``) keeps that
        base, never one of no blocks, which the next push would read as every
        block new: it takes the ask only when this round writes it anew."""
        for block_id, marker in list(ctx.new_anchors.items()):
            owner = self.remote.page_of(block_id)
            notes = self.rows.of(owner).get(kinds.NOTES.name) if owner and owner != page_id else None
            if notes and (owner in self.layout.pages or self.only):     # a page's round reads no other page
                if not _unknown(notes[1]):
                    nodes = [{**n, "a": n.get("a") or marker} if n["id"] == block_id else n
                             for n in notes[1]["nodes"] or ()]
                    self._put(*notes, nodes=nodes, seq=-1)
                self.anchors[block_id] = marker
                self.again.add(owner)
        ctx.new_anchors.clear()

    def _front(self, text: str, info: Info, *, new: bool) -> str:
        """The front matter the page's title, labels and aliases ask for: a key
        the note holds is patched when Gamma's value is not what push reads
        from it (``_front_props``; a tag in another case is the label of that
        name, ``tags: Physics`` holding the label ``physics``, and ``aliases:
        Fox`` is ``[Fox]``), so a key Gamma did not change keeps its bytes; a
        note pull made gets its tags and aliases; a person's note never gets
        front matter it did not have."""
        doc = md_lens.parse(text)
        fields = doc.front.fields if doc.front else {}
        title, props, tags = _front_props(fields, info.title)
        want = {}
        if fields.get("title") and title != info.title:
            want["title"] = info.title
        if ("tags" in fields or new) and [t.casefold() for t in tags] != [n.casefold() for n in info.labels]:
            want["tags"] = info.labels or None
        if ("aliases" in fields or new) and props.get("aliases", []) != info.aliases:
            want["aliases"] = info.aliases or None
        if not want or (doc.front is None and not new):
            return text
        return md_lens.patch_front(doc, want)

    def _shaped(self, page: Page, projected: list, tree: list, shaped: list, entries: dict, ctx):
        """The base's nodes for the file as shaped, and whether pull is done
        with the page: what a file cannot hold as Gamma has it goes to Gamma as
        ops, where the vault pushes everything and no caret is on those
        blocks; else the page waits for the next pull. A split that cannot go
        now (a caret on the block, the page written meanwhile) is ``Waiting``,
        the file as it was: written, it would hold a block no block of Gamma
        is, which an edit on disk would bring in beside the one Gamma keeps.
        → (nodes, level, seq)."""
        flat = md_lens.flatten(shaped)
        if md_lens.tree_equal(shaped, tree) or not self.push_all:
            return self._nodes(flat, entries, ctx, [n["id"] or new_block_id() for n in flat]), True, page.seq
        split = any(n["id"] is None for n in flat)
        texts = [self._nodes([n], entries, ctx, [n["id"]])[0]["g"] for n in flat]
        at = {n["id"]: k for k, n in enumerate(md_lens.flatten(projected))}
        pairs = {k: at[n["id"]] for k, n in enumerate(flat) if n["id"] in at}
        file_tree = _retexted(shaped, [_text(g) for g in texts])
        kind_of, token = _kind_of(page.id), _token()

        def plan(now, _held):
            merged = merge.merge_page(now.blocks, page.id, projected, file_tree, pairs, kind_of=kind_of)
            merged = _named(merged, token, set(at))
            return Edit(merged.ops, ids=merged.ids, level=merged.level)
        expected = plan(page, None)
        busy = self.remote.carets(page.id)
        try:
            if any(op["id"] in busy for op in expected.ops):
                raise Changed(page.id)
            _before, seq, edit = self.remote.apply(page.id, plan, token=token, built=(page.seq, expected))
        except (Changed, Gone):
            if split:
                raise Waiting("a block of two paragraphs in Gamma is being edited or changed; it is written once "
                              "Gamma takes it as two") from None
            return self._nodes(flat, entries, ctx, [n["id"] for n in flat]), False, page.seq
        return self._nodes(flat, entries, ctx, edit.ids), edit.level, seq

    @staticmethod
    def _nodes(flat: list, entries: dict, ctx, ids: list) -> list:
        """The base's ``{id, g, m?, a?}`` of a shaped file's blocks: a block's
        text as written kept with its Gamma text, one a split made read back."""
        out = []
        for n, block_id in zip(flat, ids):
            e = entries.get(n["id"])
            if e is not None and e["content"] == n["content"]:
                g, memo = e["g"], e["m"]
            else:
                g, memo = links.to_gamma(n["content"], (e or {}).get("m") or {}, ctx, base=(e or {}).get("g"))
            out.append({"id": block_id, "g": g, **({"m": memo} if memo else {}),
                        **({"a": n["anchor"]} if n.get("anchor") else {})})
        return out

    def _new_files(self, ctx) -> None:
        """Pictures a note's links put in the vault: copied there and read in
        place from then on, where the vault's rules track them. In a work
        folder the picture is written and nothing else: no row (the next
        whole round reads it as a new file, a page), its bytes served from
        the store meanwhile, which Gamma's link names."""
        for rel, name in list(ctx.new_files.items()):
            if not self._there(rel) and not self._tracked(rel):
                self._not_tracked(rel)
                continue
            data = None if self._there(rel) else _fetched(self.remote, name)
            if data is None:
                continue
            if self.scan is None:
                self._steady(_dir(rel))
            if self.work:
                disk.write_new(self.root, rel, data)
                self.counts["added"] += 1
                continue
            token = _token()
            fields = {"hash": name, "ours": 1}
            written = disk.write_new(self.root, rel, data,
                                           ready=self._journal(rel, kinds.PICTURE.name, token, data, "", fields))
            self.remote.link_file(self.root / rel, name)
            store.promote(self.id, rel, token, **fields, size=written.size, mtime_ns=written.mtime_ns,
                                file_id=written.file_id, read_at=time.time())
            self._promoted(rel)
            self.counts["added"] += 1
        ctx.new_files.clear()

    def _current(self, path: str) -> str | None:
        """The hash of the note as it is now: a note is small, so pull always
        hashes (None: it is gone, the scan's to tell)."""
        try:
            return disk.sha(disk.read(self.root, path))
        except FileNotFoundError:
            return None

    def _changed_under(self, path: str, info: Info) -> None:
        """The note changed since the base: it is pushed first and the page
        written again, once a round; with push off it is kept until a force
        pull."""
        if not self.pushing or not self.push_all or path in self.pushed_again:
            self._hold(path)
            self._keep(path, "changed on this computer since Gamma last read it", not self.push_all)
            return
        self.pushed_again.add(path)
        self.reads.pop(path, None)
        self._push_file(path, self._stat(path))
        self.layout = self.remote.layout(self.only)
        if info.id in self.layout.pages:
            self._pull_page(self.layout.pages[info.id], True)

    def _record_force(self, page_id: str, path: str, row: dict, base: Base, ctx) -> None:
        """What a force pull replaced in a note changed on disk, recorded
        resolved: each block of the file as it is that the base did not hold,
        against the page's text of the block it matched."""
        doc = md_lens.parse(disk.read(self.root, path).decode("utf-8", "replace"))
        pairs = md_lens.match(md_lens.flatten(md_lens.tree_of(base.doc, base.ids)), md_lens.flatten(md_lens.tree_of(doc)))
        rows = []
        page = self.remote.page(page_id)
        for n, node in enumerate(doc.flat):
            i = pairs.get(n)
            if i is not None and i < len(base.ids) and base.doc.flat[i].content == node.content:
                continue
            block_id = base.ids[i] if i is not None and i < len(base.ids) else page_id
            mine = page.blocks[block_id]["content"] if page and block_id in page.blocks and block_id != page_id else ""
            theirs = _text(links.to_gamma(node.content, {}, ctx)[0])
            rows.append({"block_id": block_id, "mine": mine, "theirs": theirs, "result": mine,
                         "base": _text(base.nodes[i]["g"]) if i is not None and i < len(base.ids) else ""})
        if rows:
            self.remote.apply(page_id, lambda _page, _held: Edit([], rows, True))

    def _write(self, path: str, data: bytes, text: str, nodes: list, page_id: str, *, new: bool, ours: int,
               **stamp) -> None:
        """The note written (``replace_note``; ``write_new`` for a new one, which
        never replaces a file that appeared meanwhile), in the journal
        (``_journal``); what it replaced kept in the history, always when that
        was not the base the round checked: a person's save in the moment
        between the check and the rename. The copy of those bytes beside the
        note goes only once the history has them."""
        token, base = _token(), (self.rows.get(path) or {}).get("hash")
        journal = self._journal(path, kinds.NOTES.name, token, data, page_id, {**stamp, "ours": ours}, text, nodes)
        try:
            if new:
                written = disk.write_new(self.root, path, data, ready=journal)
            else:
                written = disk.replace_note(self.root, path, data, journal, hold=True)
        except PermissionError as e:
            store.drop(self.id, path, token)
            raise OSError(f"another program holds the file: {error_text(e)}") from e
        except FileNotFoundError:
            store.drop(self.id, path, token)
            return
        except BaseException:
            try:
                landed = disk.sha(disk.read(self.root, path)) == disk.sha(data)
            except OSError:
                landed = False
            if not landed:
                store.drop(self.id, path, token)
            raise
        if written.replaced is not None:
            saved = disk.sha(written.replaced) != base
            if saved:
                log.warning(f"[vault] {self.root / path}: saved while Gamma wrote it; that save is in the vault's history")
            store.keep(self.id, path, written.replaced, always=saved)
        store.promote(self.id, path, token, size=written.size, mtime_ns=written.mtime_ns, file_id=written.file_id,
                            read_at=time.time(), ours=ours, **stamp)
        disk.drop_temp(self.root, written.kept)
        self._promoted(path)
        self.counts["added" if new else "updated"] += 1

    def _journal(self, path: str, kind: str, token: str, data: bytes, page_id: str, fields: dict, text=None,
                 nodes=None):
        """The ``ready`` of a file pull writes: its pending, once the bytes are
        on the disk under a temporary name and before the rename, naming the
        stat the rename puts there and where what it replaces is kept, so that
        a round that died finds the write and those bytes
        (``_recover_write``)."""
        def ready(written):
            store.put_pending(self.id, path, kind, {
                "op": "pull", "token": token, "hash": disk.sha(data), "text": text, "nodes": nodes,
                "page_id": page_id, "fields": fields, "size": written.size, "mtime_ns": written.mtime_ns,
                "file_id": written.file_id, "kept": written.kept})
        return ready


def _with_moves(old: list, new: list, pairs: dict) -> dict:
    """``md_lens.match``'s pairs of a file against its base, and each block
    the file only moved: one left unpaired whose text is that of one base
    block left unpaired, and of no other block left on either side. The lens
    pairs identical text in order, so a line moved past others reads as one
    deleted and one new, and a block Gamma had taken to another page or
    deleted would come back with it, which only a change of its text may
    bring (gamma/vault/merge.py)."""
    taken = set(pairs.values())
    olds = Counter(node["content"] for i, node in enumerate(old) if i not in taken)
    news = Counter(node["content"] for n, node in enumerate(new) if n not in pairs)
    at = {node["content"]: i for i, node in enumerate(old) if i not in taken and olds[node["content"]] == 1}
    out = dict(pairs)
    for n, node in enumerate(new):
        if n not in pairs and node["content"].strip() and news[node["content"]] == 1 and node["content"] in at:
            out[n] = at[node["content"]]
    return out


def _label_ops(page: Page, labels) -> list:
    """``labels``, the ids a push gives the page (``Round._file_labels``;
    None: as they are), as a ``set`` of its root; none when the page has
    them."""
    root = page.blocks[page.id]
    if labels is None or (root["props"].get(LABELS) or None) == (labels or None):
        return []
    return [{"op": "set", "id": page.id, "props": {LABELS: labels or None}}]


def _fetched(remote, name: str) -> bytes | None:
    """The stored file ``name`` as pull writes it into the vault (None: the
    remote has none), once its bytes can be that file: a picture's hash its
    name, a PDF's its doc id, or else a whole PDF (a stored PDF may keep a
    name its bytes do not hash to: ``storage.matches_name``). Anything else
    (a sign-in portal's page answered for it, a body cut short) is never
    written nor taken for the file: ``Waiting``, and the next round fetches
    it again."""
    data = remote.read_upload(name)
    if data is None:
        return None
    stem = name.rpartition(".")[0]
    whole = storage.content_digest(data) == stem or (
        storage.matches_name(name, data) and (not name.endswith(".pdf") or b"%%EOF" in data[-gamma_sync.EOF_TAIL:]))
    if not whole:
        raise Waiting(f"{name}: what came back is not the stored file (a sign-in page, or an answer cut short)")
    return data


def _known_nodes(nodes) -> list:
    """A base's nodes as ``links.LinkContext.add_note`` reads a note:
    its blocks' ids, anchors and texts."""
    return [{"id": n["id"], "content": _text(n["g"]), "children": [], **({"anchor": n["a"]} if n.get("a") else {})}
            for n in nodes or ()]


def _by_page(projected: list, base: Base | None, pairs: dict) -> dict:
    """``match``'s pairs of a file against its base as places in the page's
    projection: ``{file index: projected index}`` of each block the base
    names that the page holds with a span."""
    at = {node["id"]: k for k, node in enumerate(md_lens.flatten(projected))}
    return {n: at[base.ids[i]] for n, i in pairs.items() if base and i < len(base.ids) and base.ids[i] in at}


def _forced(page: Page, projected: list, file_tree: list, by_page: dict, token: str, known: set, kind_of) -> Edit:
    """``force=push``'s plan: the page's blocks with a span (``projected``)
    become ``file_tree``'s (its blocks' places among them ``by_page``), a new
    block taking what the file says of it (a callout its quote and page),
    every text the page had there that the file replaced recorded,
    resolved: a highlight it deletes by its quote and page as well as its
    note (``_said``), which may be none."""
    at = {node["id"]: k for k, node in enumerate(md_lens.flatten(projected))}
    merged = merge.merge_page(page.blocks, page.id, projected, file_tree, by_page, kind_of=kind_of,
                                    merge=lambda _base, theirs, _stored: (theirs, True))
    merged = _named(merged, token, known | set(at))
    replaced = []
    for block_id in at:
        after, block = merged.target.get(block_id), page.blocks[block_id]
        if after is None or after["content"] != block["content"]:
            text = after["content"] if after else ""
            mine = block["content"] if after else _said(block, page.id)
            replaced.append({"block_id": block_id, "mine": mine, "theirs": text, "result": text, "base": block["content"]})
    return Edit(merged.ops, replaced, True, merged.ids, merged.level, merged.deleted)


def _said(block: dict, page_id: str) -> str:
    """A block a force deletes as its record keeps it, for the person to read
    what went: its text, and a highlight's quote with its PDF page above its
    note."""
    g = _g(block, page_id)
    if not isinstance(g, dict):
        return g
    head = f"p. {g['page']}\n" if g.get("page") else ""
    quote = "\n".join(f"> {line}" for line in (g["quote"] or "").split("\n"))
    return head + quote + (f"\n\n{g['note']}" if g["note"] else "")


# --- entries -------------------------------------------------------------------------------------

@dataclass(slots=True)
class Result:
    """A round's outcome for its link's row: the status, the ``state`` to keep
    (the marker's id, the guard's count), the cursor pull read (None: no
    pull ran)."""
    status: dict
    state: dict
    cursor: str | None


def sync(link: dict, *, direction: str = "both", force: str = "", confirm: bool = False, remote=None,
         progress=None) -> Result:
    """A whole round of the vault ``link``, telling ``progress`` how far it is
    (``Round._report``)."""
    rnd = Round(link, remote or remote_for(link), direction=direction, force=force, confirm=confirm,
                progress=progress)
    status = rnd.run()
    return Result(status, rnd.state, None if rnd.cursor is None else str(rnd.cursor))


def sync_page(link: dict, page_id: str, *, remote=None) -> Result:
    """The hot loop's round: only the files of ``page_id``, pushed when their
    stat moved, then the page pulled when it moved. No scan, so no file of the
    disk reads as new or removed; pull may write the page's own files (its
    notes, a stored PDF it has, the pictures its links name), in directories
    still as the last scan recorded them (``Round._steady``)."""
    rnd = Round(link, remote or remote_for(link), page_id=page_id)
    status = rnd.run()
    return Result(status, rnd.state, None)
