"""What a file is to a synced vault, by its name: the class registry
(docs/dev/synced_vaults.md "The classes"). A vault's row keeps its class as
``kind``, the stored word, and one object here stands for each class:
``NOTES`` (a Markdown note, its page's blocks both ways), ``PDF`` (a paper
read where it is), ``PICTURE`` (a picture a note links, read where it is,
no page: a kept folder's) and ``FILE`` (any other file of a work folder,
pictures included, read where it is: a page whose document is the file,
docs/dev/work_folders.md "The round in a work folder"). Each says what its
rows are to a round (``compared``, ``in_place``, ``in_library``,
``makes_pages``) and does the round's steps for its files (``read``,
``known``, ``new``, ``change``, ``moved``, ``left``, ``swept``, ``pull``,
``place``): the code of gamma/vault/sync.py's ``Round``, dispatched by
class, a step that is one class's alone staying a ``Round`` method the class
calls (``_write_notes``, ``_copy_pdf``, ``_merge_into``, ``_kept_note``,
``_push_doc``). Pairing a PDF with its notes stays the round's: it is
between two classes.

The class is decided **by name, never by bytes** (``class_of``:
``documents.formats.format_of`` with no head read): ``notes`` for the
``markdown`` format, ``pdf`` for a ``.pdf``, and in a kept folder
``picture`` for an ``image``, none for any other file, which a vault counts
and does not read; in a work folder's round (``work``) every other file is a
``file``. A file's **format** is the document's, read with the file
(``file_format``: the name, else the first bytes; a PDF by its bytes alone
is ``binary``, a PDF being a PDF by its name). ``by_kind`` finds a row's or
a tombstone's class, ``main`` a page's. Only ``documents.formats`` is
imported when this module loads; whatever else a class needs it imports
where it runs, so that gamma/linked_files.py, gamma/folder_links.py and the
engine's own modules, which import this one, form no cycle.
``SETTLE_NOTE_S`` is read at each call (tests patch it).
"""

import os
import time

from ..documents import formats

SETTLE_NOTE_S = 1.0        # a note written more recently than this is still being written


def file_format(leaf: str, head: bytes | None) -> str:
    """The format of a work folder's file named ``leaf`` whose first bytes
    are ``head`` (``formats.format_of``): a PDF by its bytes alone, under
    another name, is ``binary`` (the pdf class is the ``.pdf`` name's)."""
    fmt, _language = formats.format_of(leaf, head)
    return "binary" if fmt == "pdf" else fmt


class Kind:
    """One class of a vault's files; ``name`` is the row's ``kind``.
    ``compared``: its rows meet the scan in ``Round._classify``;
    ``in_place``: its bytes are a ``linked_files`` row named
    ``stored_name(hash, ext)``, read where they are; ``in_library``: a work
    folder's library switch stamps ``library`` on its new pages;
    ``makes_pages``: a new file of it is a page. The methods take the round
    they run in (``rnd``) and are its steps for a file of the class."""

    name = ""
    compared = True
    in_place = False
    in_library = False
    makes_pages = True

    def read(self, rnd, path: str, found):
        """The file read whole once it has settled (``Round._read``)."""
        raise NotImplementedError(f"a {self.name} is never read")

    def known(self, path: str, row: dict):
        """The row as ``disk.pair_moves`` compares it (``Round._known``)."""
        from . import disk

        return disk.Known(path, row["kind"], row["size"], row["hash"], row["file_id"], "", None, row["mtime_ns"])

    def weight(self, found) -> int:
        """The bytes a round's progress counts for the file: those it hashes in place."""
        return 0

    def pending_text(self, read):
        """The journal's ``text`` of a new base: a note's text, a PDF's ``/ID``."""
        return None

    def new(self, rnd, path: str, read, partner=None) -> None:
        """A file that is no page yet made one (``Round._push_new``);
        ``partner`` the note a PDF pairs with."""
        raise NotImplementedError(f"a {self.name} makes no page")

    def change(self, rnd, path: str, read, row: dict) -> None:
        """A file of the rows with new bytes, into its page (``Round._push_file``)."""
        raise NotImplementedError(f"a {self.name} is never compared")

    def moved(self, rnd, new: str, old: str, row: dict) -> None:
        """The class's part of a move on disk (``Round._moved``, ``_unbury``):
        a file read in place is relinked."""

    def left(self, rnd, page_id: str, path: str, row: dict) -> bool:
        """The class's part of ``Round._left``, before the page's files go to
        the trash: whether they go on (False: they stay this round)."""
        return True

    def swept(self, rnd, tomb: dict, at: str) -> None:
        """``Round._sweep``'s spare, before a tombstone's file at ``at`` goes or
        stays."""

    def link_name(self, path: str, row: dict):
        """The upload a note's link to the file reads (``Round._ctx_at``);
        None for none yet."""
        return None

    def pull(self, rnd, info, rows: dict, folder: str) -> None:
        """``Round._pull_page`` for a page whose main class this is."""

    def place(self, rnd, info, rows: dict, folder: str) -> None:
        """``Round._place`` for a page whose main class this is."""


class _Notes(Kind):
    """A Markdown note: its page's blocks, both ways."""

    name = "notes"
    in_library = True

    def read(self, rnd, path, found):
        from . import disk, sync
        from .remote import Waiting

        if 0 <= time.time() - found.mtime_ns / 1e9 < SETTLE_NOTE_S:
            raise Waiting("still being written")
        target = disk.path(rnd.root, path)
        read_at = time.time()
        data = target.read_bytes()
        st = os.stat(target)
        if (st.st_size, st.st_mtime_ns) != (found.size, found.mtime_ns) or len(data) != st.st_size:
            raise Waiting("changed while it was read")
        try:
            text, problem = data.decode("utf-8"), ""
        except UnicodeDecodeError:
            text, problem = None, "not UTF-8, so it cannot be edited here"
        return sync.Read(path, self.name, st.st_size, st.st_mtime_ns, str(st.st_ino), read_at, disk.sha(data), text,
                         problem=problem)

    def known(self, path, row):
        from . import disk

        return disk.Known(path, row["kind"], row["size"], row["hash"], row["file_id"], "", row["text"], row["mtime_ns"])

    def pending_text(self, read):
        return read.text

    def new(self, rnd, path, read, partner=None):
        rnd._new_note(path, read)

    def change(self, rnd, path, read, row):
        if rnd.push_all:
            rnd._push_note(path, read, row)
        else:
            rnd._kept_note(path, row, read=read)

    def left(self, rnd, page_id, path, row):
        """A note changed on disk since its base is the disk's word, which
        push has not read yet: the page's files stay and the note is kept
        (False); a force pull keeps its bytes in the history first."""
        from . import disk, store

        current = rnd._current(path)
        if current is None or current == row["hash"]:
            return True
        if rnd.force != "pull":
            if path not in rnd.kept_paths:      # reported once
                rnd._hold(path)
                rnd._keep(path, "changed on this computer since Gamma last read it, and its page left the vault", True)
            return False
        store.keep(rnd.id, path, disk.read(rnd.root, path), always=True)
        return True

    def pull(self, rnd, info, rows, folder):
        rnd._write_notes(info, rows.get(NOTES.name), rows.get(PDF.name), folder)

    def place(self, rnd, info, rows, folder):
        rnd._place_notes(info, rows, folder)


class _Pdf(Kind):
    """A paper, read where it is: its page's document at ``path``."""

    name = "pdf"
    in_place = True
    in_library = True

    def read(self, rnd, path, found):
        from .. import gamma_sync, linked_files
        from . import disk, sync
        from .remote import Waiting

        target = disk.path(rnd.root, path)
        why = gamma_sync.settled(target)
        if why:
            raise Waiting(why)
        read_at = time.time()
        try:
            seen = linked_files.hash_file(target)
        except ValueError as e:
            raise Waiting(str(e)) from e
        return sync.Read(path, self.name, seen[1], seen[2], seen[3], read_at, seen[0], seen=seen,
                         pdf_id=disk.pdf_id(target), format="pdf", ext=".pdf")

    def known(self, path, row):
        from . import disk

        return disk.Known(path, row["kind"], row["size"], row["hash"], row["file_id"], row["text"] or "", None,
                          row["mtime_ns"])

    def weight(self, found):
        return found.size

    def pending_text(self, read):
        return read.pdf_id

    def new(self, rnd, path, read, partner=None):
        rnd._new_pdf(path, read, partner)

    def change(self, rnd, path, read, row):
        if rnd.push_pdfs:
            rnd._push_doc(path, read, row)

    def moved(self, rnd, new, old, row):
        rnd.remote.relink(str(rnd.root / old), str(rnd.root / new))

    def left(self, rnd, page_id, path, row):
        """A PDF changed on disk since its base (a person's annotated copy in a
        vault that does not push it) is the person's: it stays where it is,
        no longer synced, and is reported (``sync.LEFT``); one read in place
        by a page that lives on is stored first."""
        from . import store, sync

        if rnd._pdf_changed(path, row):
            store.forget(rnd.id, path)
            rnd.rows.pop(path, None)
            rnd._keep(path, sync.LEFT)
        elif (rnd.remote.state(page_id) == "live" and os.path.isfile(rnd.root / path)
              and rnd.remote.doc_of(page_id) == row["hash"]):
            rnd.remote.store_doc(rnd.root / path, page_id)
        return True

    def swept(self, rnd, tomb, at):
        if tomb["hash"]:
            rnd.remote.spare(str(rnd.root / at), self.link_name(at, tomb), tomb["page_id"])

    def link_name(self, path, row):
        from ..documents.model import stored_name

        return stored_name(row["hash"], ".pdf") if row["hash"] else None

    def pull(self, rnd, info, rows, folder):
        rnd._write_notes(info, rows.get(NOTES.name), rows.get(PDF.name), folder)

    def place(self, rnd, info, rows, folder):
        rnd._place_notes(info, rows, folder)


class _Picture(Kind):
    """A picture a note links, read where it is under its upload name: a row
    of no page, never compared with the scan (a kept folder's; in a work
    folder a picture is a ``file``)."""

    name = "picture"
    compared = False
    makes_pages = False

    def link_name(self, path, row):
        return row["hash"] or None


class _File(Kind):
    """Any other file of a work folder, pictures included, read where it is:
    a page whose document is the file (its format read with it, its
    extension ``formats.vault_extension``), titled by its leaf, never in the
    library by the switch; one past ``config.vault_keep_bytes`` is *listed*,
    its page a document with no bytes (docs/dev/documents.md "The listed
    state"). Pull writes nothing for it, and nothing ever moves it."""

    name = "file"
    in_place = True

    def read(self, rnd, path, found):
        from .. import config, gamma_sync, linked_files
        from . import disk, sync
        from .remote import Waiting

        target = disk.path(rnd.root, path)
        why = gamma_sync.settled(target, tail=None)
        if why:
            raise Waiting(why)
        read_at, leaf = time.time(), path.rsplit("/", 1)[-1]
        ext = formats.vault_extension(leaf)
        before = os.stat(target)       # the size that decides, the stat the read records: one file's
        if before.st_size > config.vault_keep_bytes():
            with open(target, "rb") as f:
                head = f.read(formats.SNIFF_BYTES)
            st = os.stat(target)
            if (before.st_size, before.st_mtime_ns) != (st.st_size, st.st_mtime_ns):
                raise Waiting("changed while it was read")
            return sync.Read(path, self.name, st.st_size, st.st_mtime_ns, str(st.st_ino), read_at, "",
                             format=file_format(leaf, head), ext=ext, listed=True)
        try:
            *seen, head = linked_files.hash_any(target)
        except ValueError as e:
            raise Waiting(str(e)) from e
        return sync.Read(path, self.name, seen[1], seen[2], seen[3], read_at, seen[0], seen=tuple(seen),
                         format=file_format(leaf, head), ext=ext)

    def weight(self, found):
        from .. import config

        return found.size if found.size <= config.vault_keep_bytes() else 0

    def new(self, rnd, path, read, partner=None):
        rnd._new_file(path, read)

    def change(self, rnd, path, read, row):
        rnd._push_doc(path, read, row)

    def moved(self, rnd, new, old, row):
        rnd.remote.relink(str(rnd.root / old), str(rnd.root / new))

    def swept(self, rnd, tomb, at):
        if tomb["hash"]:
            rnd.remote.spare(str(rnd.root / at), self.link_name(at, tomb), tomb["page_id"])

    def link_name(self, path, row):
        from ..documents.model import stored_name

        return stored_name(row["hash"], formats.vault_extension(path.rsplit("/", 1)[-1])) if row["hash"] else None

    def pull(self, rnd, info, rows, folder):
        """Nothing is written: the file is the disk's, its notes Gamma's own.
        The row takes the page's seq, title and filing, level with it."""
        path, row = rows[self.name]
        rnd._put(path, row, seq=info.seq, title=info.title, folder=folder)

    def place(self, rnd, info, rows, folder):
        """The title and the filing recorded; the file is never moved."""
        for at, at_row in rows.values():
            if at_row["title"] != info.title or at_row["folder"] != folder:
                rnd._put(at, at_row, title=info.title, folder=folder)


NOTES, PDF, PICTURE, FILE = _Notes(), _Pdf(), _Picture(), _File()
CLASSES = {k.name: k for k in (NOTES, PDF, PICTURE, FILE)}
_BY_FORMAT = {"markdown": NOTES, "pdf": PDF, "image": PICTURE}


def class_of(name: str, *, work: bool = False) -> Kind | None:
    """The class of a file named ``name`` (a vault path or a name), by its
    leaf alone and no bytes read: ``NOTES`` for the ``markdown`` format,
    ``PDF`` for a ``.pdf``; in a kept folder ``PICTURE`` for an ``image`` and
    None for any other file (a leaf that is all extension, ``.md``, has
    none); in a work folder's round (``work``) ``FILE`` for every other
    file, pictures included."""
    cls = _BY_FORMAT.get(formats.format_of(name.replace("\\", "/").rsplit("/", 1)[-1])[0])
    if work and cls not in (NOTES, PDF):
        return FILE
    return cls


def by_kind(kind: str) -> Kind | None:
    """The class a row or a tombstone keeps as its ``kind``."""
    return CLASSES.get(kind)


def main(rows: dict) -> Kind | None:
    """A page's class, from its rows by kind (``Round._Rows.of``): ``PDF``
    when it has a PDF row, else ``NOTES``, else ``FILE``, else None."""
    return next((cls for cls in (PDF, NOTES, FILE) if cls.name in rows), None)
