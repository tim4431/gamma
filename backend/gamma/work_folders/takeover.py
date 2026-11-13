"""A synced vault from before work folders taken over as one, and a work
folder let go (docs/dev/work_folders.md "The takeover"). Run by the round's
caller under the link's lock (gamma/folder_links.py ``_vault_round``), on
this server's workspace (``vault.local_remote.LocalRemote``): only a vault of this
computer's server is a work folder.

``take_over`` writes the mark on the vault's folder, then stamps each page
the vault keeps (its layout, which leaves the pages of the vaults inside or
before it out, as a round does: a page shared with a vault beside it stays
that vault's) as a file of the work folder, in the library, named by its
file: filed first in the folder of its file's directory (the top's for one
at the top, made where missing), then in the folders it had outside every
work folder; a sub-page flattened; its PDF's size on its document. The
stamps are quiet (no ``updated_at``: nobody's recents reorder), one
transaction a chunk of pages, each page skipped once it is stamped so, so a
takeover killed anywhere resumes at the next round. The rows' seq follows
where it was level with the page, so pull writes no note again, and each
row records its directory's folder. The link's state with ``TAKEN_OVER`` is
the commit point, written by the caller.

``release`` unstamps every live file of the work folder, the files not in
the library sent to Recently deleted instead when the vault is removed,
forgets the rows of its files of other formats (gamma/vault/kinds.py
``FILE``, a work folder's class alone) and then takes the mark off: they
are library pages, filed where they were.
Files in Recently deleted keep their stamps, which a restore drops once no
mark names their work folder (``filing.may_restore``)."""

import os
from pathlib import Path

from ..vault import disk as vault_disk, kinds, store as vault_store
from ..db import page_now
from . import selection
from .library import STAMPS, work_folder_of
from .selection import MAX_FILES

TAKEN_OVER = "work_folder_at"     # the link state's key: when the vault became a work folder


def mark_of(link: dict) -> dict:
    """The mark a work folder's link asks of its folder: its pull and its
    library switch."""
    return {"pull": bool(link.get("pull")), "library": bool(link.get("library"))}


def write_mark(link: dict, remote) -> dict:
    """The link's mark on its folder, written when the folder carries
    another or none (a restore, a mirror's merge). → the mark."""
    want = mark_of(link)
    if remote.mark() != want:
        remote.folder_op({"op": "set", "id": link["folder_id"], "props": {"vault": want}})
    return want


def _dir(path: str) -> str:
    return path.rpartition("/")[0]


def _folders(remote, link: dict, directories: set, inside: dict) -> dict:
    """``{directory: folder id}`` of the vault's ``directories`` ("" the
    vault's own folder), each the folder its row records while that folder is
    below the vault's (``inside``), else made where missing (``remote.folders``,
    which reuses a folder of the name) and recorded."""
    recorded, out = vault_store.dirs(link["id"]), {"": link["folder_id"]}
    missing = set()
    for directory in directories - {""}:
        folder_id = (recorded.get(directory) or {}).get("folder_id")
        if folder_id in inside:
            out[directory] = folder_id
        else:
            missing.add(directory)
    if missing:
        prefixes = {tuple(d.split("/")[:k]) for d in missing for k in range(1, d.count("/") + 2)}
        made = remote.folders(sorted(prefixes, key=len))
        for names in sorted(prefixes, key=len):
            path = "/".join(names)
            if path in missing or not (recorded.get(path) or {}).get("folder_id") in inside:
                try:
                    file_id = str(os.stat(Path(link["path"]) / path).st_ino)
                except OSError:
                    file_id = ""
                vault_store.put_dir(link["id"], path, made[names], file_id)
            if path in missing:
                out[path] = made[names]
    return out


def _library_pages(pages: dict, main: dict, pull: bool) -> set:
    """The pages of the vault's layout (``{id: Info}``) with no file on its
    disk that would not follow (a PDF, or any page where the vault does not
    pull) and the sub-pages below them that have none either: no file of W,
    which no round would ever trash and a person could not, but library
    pages."""
    out: dict = {}

    def stays(page_id: str, seen: frozenset) -> bool:
        if page_id not in out:
            info = pages.get(page_id)
            if page_id in main or info is None or page_id in seen:
                out[page_id] = False
            else:
                out[page_id] = not pull or bool(info.doc_id) or (
                    bool(info.parent) and stays(info.parent, seen | {page_id}))
        return out[page_id]
    return {page_id for page_id in pages if stays(page_id, frozenset())}


def _library_page(tree, own: list, props: dict, stays: set) -> dict | None:
    """A library page of the vault's layout (``_library_pages``): filed in
    its folders outside every work folder (none: the library's top), and out
    of a page that becomes a file."""
    patch = {}
    filed = [f for f in own if not tree.work_of(f)]
    if filed != own:
        patch["folders"] = filed or None
    parent = props.get("parent_page")
    if parent and parent not in stays:
        patch["parent_page"] = None
    return patch or None


def take_over(link: dict, remote) -> dict:
    """The vault ``link`` taken over as a work folder through ``remote``
    (the module doc). → the state to keep: the link's, with ``TAKEN_OVER``
    and the cap of files a round reads (``cap``: ``MAX_FILES``, or the
    files it tracks now when they are more, counted as a work folder's
    round counts them: every file the rules take, pictures included).
    ``Offline`` for a directory that does not answer, before anything is
    written."""
    w = link["folder_id"]
    root = Path(link["path"])
    found = vault_disk.walk(root, selection.rules_of(root), work=True)
    seen = len(found.files) + found.pictures + found.untracked
    write_mark(link, remote)
    layout = remote.layout()
    rows = vault_store.files(link["id"])
    files: dict = {}
    for path, row in rows.items():
        cls = kinds.by_kind(row["kind"])
        if row["page_id"] and cls is not None and cls.makes_pages:
            files.setdefault(row["page_id"], {})[row["kind"]] = (path, row)
    main = {page_id: next(by_kind[k] for k in (kinds.PDF.name, kinds.NOTES.name, kinds.FILE.name) if k in by_kind)
            for page_id, by_kind in files.items() if page_id in layout.pages}
    dirs = _folders(remote, link, {_dir(path) for path, _row in main.values()}, layout.folders)
    tree = remote.work_tree()
    pull = mark_of(link)["pull"]
    stays = _library_pages(layout.pages, main, pull)

    def plan(page_id: str, props: dict) -> dict | None:
        own = [f for f in props.get("folders") or () if isinstance(f, str)]
        if page_id in stays:
            return _library_page(tree, own, props, stays)
        if page_id in main:
            path, _row = main[page_id]
            first = dirs[_dir(path)]
        else:
            first = next((f for f in own if tree.work_of(f) == w), w)
        filed = [first] + [f for f in own if f != first and not tree.work_of(f)]
        patch = {}
        if props.get("work_folder") != w:
            patch["work_folder"] = w
        if props.get("library") is not True:
            patch["library"] = True
        if page_id in main and props.get("file_name") != main[page_id][0].rsplit("/", 1)[-1]:
            patch["file_name"] = main[page_id][0].rsplit("/", 1)[-1]
        if filed != own:
            patch["folders"] = filed
        if props.get("parent_page"):
            patch["parent_page"] = None
        doc, pdf = props.get("doc"), files.get(page_id, {}).get(kinds.PDF.name)
        if pdf and isinstance(doc, dict) and doc.get("size") is None and pdf[1]["size"]:
            patch["doc"] = {**doc, "size": pdf[1]["size"]}
        return patch or None

    seqs = remote.stamp(list(layout.pages), plan)
    for page_id, (path, _row) in main.items():
        folder = "" if dirs[_dir(path)] == w else dirs[_dir(path)]
        before, after = seqs.get(page_id, (None, None))
        for at, row in files[page_id].values():
            fields = {}
            if row["folder"] != folder:
                fields["folder"] = folder
            if before is not None and row["seq"] == before:
                fields["seq"] = after
            if fields:
                vault_store.put(link["id"], at, **fields)
    return {**(link.get("state") or {}), TAKEN_OVER: page_now(), "cap": max(MAX_FILES, len(rows), seen)}


def release(link: dict, remote, *, trash_files: bool) -> None:
    """The work folder of ``link`` let go (the module doc): its live files
    unstamped, quietly, or, with ``trash_files``, those not in the library
    sent to Recently deleted; the rows of its files of other formats
    forgotten (a kept folder's round compares no such class, and would move
    a file a page in Recently deleted left behind: their pages keep their
    documents, stored where they were kept); then its mark taken off."""
    w = link["folder_id"]
    pages = remote.work_pages()
    if trash_files:
        for page_id, props in pages.items():
            if props.get("library") is not True:
                remote.trash(page_id)
        pages = {page_id: props for page_id, props in pages.items() if props.get("library") is True}

    def plan(_page_id: str, props: dict) -> dict | None:
        if work_folder_of(props) != w:
            return None
        return {key: None for key in STAMPS if key in props} or None

    remote.stamp(list(pages), plan)
    vault_store.forget_kind(link["id"], kinds.FILE.name)
    if remote.mark() is not None:
        remote.folder_op({"op": "set", "id": w, "props": {"vault": None}})
