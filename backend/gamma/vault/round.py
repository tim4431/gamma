"""One round of a synced vault and what its parts share (gamma/vault/sync.py
runs it): ``Round``, the state of one round through its remote (the rows,
the scan, this round's reads, the counts, what it keeps and reports), with
the steps every part takes: a file's problem, a failure that is one file's
alone (``_each``), how far the round is (``_report``), the links' context
of a note (``_ctx_at``), a file of the rows as the scan finds it
(``_stat``) and as its class reads it (``_read``), the folder of a
directory and a page's first filing (``_folder_for``, ``_first``), and a
page in the file's space (``_file_tree``, ``_front``). Beside it: ``Read``,
a file as a round read it; ``_Rows``, the rows by path and by page; a
note's ``Base`` and its blocks' text in Gamma and in the file; the vault's
paths (``_stem``, ``_name``, ``_dir``, ``_join``).

The parts take the round as ``rnd`` and import one way: this module, then
gamma/vault/journal.py, then pages.py, then push.py and pull.py, then
sync.py, which none of them imports. A part calls another's step through
its module (``pages._push_file(rnd, …)``), so a test that replaces one
reaches it.
"""

import os
import secrets
from dataclasses import dataclass
from pathlib import Path

from .. import config, folder_links, md_lens
from ..blocks_store import block_kind
from ..folder_links import COUNTS, DIRECTIONS
from ..folder_sync import ID_KEY, ROOT
from ..jobs import error_text
from ..logbuf import log
from ..obsidian_export import anchor_marker, vault_name
from ..work_folders import ignore_rules
from . import disk, kinds, links, merge, store
from .remote import Info, Page, Refused, Waiting


FORCES = ("pull", "push")
LEFT_OUT = "left out by .gammaignore; its page keeps its other file"


def _token() -> str:
    return secrets.token_urlsafe(9)


def _why(e: Exception) -> str:
    """A file's failure as its row and the status say it, which the vault's
    viewers read: an OpError's detail, an OSError without the path it names
    (``jobs.error_text``)."""
    return str(getattr(e, "detail", "") or error_text(e)) or type(e).__name__


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
    decides (``pages.UNKNOWN``)."""
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


def _front_props(fields: dict, stem: str) -> tuple[str, dict, list]:
    """What a note's front matter says of its page, ``(title, props, labels)``,
    as the vault import reads it (``markdown_zip_import.MarkdownSource``):
    ``title``, ``tags`` (labels), ``aliases``, and ``doi``, ``authors``,
    ``year`` as the paper's record. ``parent`` makes a new page a sub-page
    once the round's new pages are made (``pages._sub_page``)."""
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


def _tags(fields: dict) -> list:
    """A note's tags as label names: ``tags`` and ``tag``, a list or one, the
    ``#`` off."""
    out = []
    for key in ("tags", "tag"):
        value = fields.get(key)
        out += value if isinstance(value, list) else ([value] if value else [])
    return list(dict.fromkeys(t for t in (str(v).lstrip("#").strip() for v in out) if t))


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
    id is ``state["vault"]``) through ``remote``; ``sync.run`` gives its status.
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
        self.parents = {}           # a new note -> the ``parent`` its front matter names (``pages._sub_page``)
        self.work = None            # the mark of the vault's folder, {pull, library}: a work folder's round
        self.rules = None           # what the vault tracks (its .gammaignore, else the default), read in ``sync._open``
        self.relabelled = False     # the labels tree changed since the last round: a note's tags may be stale
        self.layout = None
        self.cursor = None
        self.progress = progress
        self.done = self.total = self.bytes_done = self.bytes_total = 0
        self._ctx = None
        self._sources = None

    def _tracked(self, rel: str) -> bool:
        """Whether the vault tracks ``rel``, a path pull would write."""
        return ignore_rules.tracks(self.rules, rel)

    def _not_tracked(self, rel: str, page_id: str = "") -> None:
        """A path pull would write that the vault's rules leave out: not
        written, said, and tried again next round."""
        self._keep(rel, f"not written: .gammaignore leaves out {rel}; tick its folder whole in Choose files…")
        if page_id:
            self.remote.touched.add(page_id)

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
        in a work folder every file is a page, which a link to it mentions
        (``add_page_file``, a listed file's too), its row naming the upload a
        picture's embed and a note's own PDF read (``Kind.link_name``), and
        one the scan found that has no row yet (new this round, its page made
        after the notes changed) is read in place when a link names it
        (``_picture``), its row the page's once the page is made."""
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
                    continue
                if self.work and row["page_id"]:
                    ctx.add_page_file(rel, row["page_id"])
                if (name := cls.link_name(rel, row)):
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
        """A file's new base in the rows, one read in place, and in a work
        folder any file that is a page, in the links' context too (a note
        read later this round may link it); its page hears that its file
        changed."""
        row = store.get(self.id, path)
        if row and not row["was"]:
            self.rows[path] = row
            cls = kinds.by_kind(row["kind"])
            if self._ctx is not None and self.work and row["page_id"] and cls is not kinds.NOTES:
                self._ctx.add_page_file(path, row["page_id"])
            if self._ctx is not None and cls.in_place and (name := cls.link_name(path, row)):
                self._ctx.add_file(path, name)
        if row and row["page_id"]:
            self.remote.touched.add(row["page_id"])

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

    def _tomb_paths(self) -> set:
        """The paths the tombstones hold, a file in the trash or none: no new
        trash path takes one."""
        return {tomb["path"] for tomb in store.tombstones(self.id)}

    def _first(self, folders: list, inside: dict) -> str:
        """A page's first filing in the vault as its rows record it: the first
        of ``folders`` that is the vault's own folder ("": its top) or one of
        ``inside``, the folders below it; "" for none."""
        own = self.link["folder_id"]
        return next(("" if f == own else f for f in folders if f == own or f in inside), "")

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


def _known_nodes(nodes) -> list:
    """A base's nodes as ``links.LinkContext.add_note`` reads a note:
    its blocks' ids, anchors and texts."""
    return [{"id": n["id"], "content": _text(n["g"]), "children": [], **({"anchor": n["a"]} if n.get("a") else {})}
            for n in nodes or ()]
