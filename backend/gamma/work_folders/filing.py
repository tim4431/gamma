"""The work-folder op rule (docs/dev/work_folders.md "The op rule"): who may
change a work folder's file and the folder tree inside a work folder. Pure:
it reads a ``marks.Tree`` and a page's properties, and answers with what
is stored or ``Refused``. gamma/ops.py and blocks_store's page writers call
it; ops turns a refusal into a 403 with the ``locked`` conflict.

Three writers. The mirror (op client ``sync``) passes every rule: it
replays another copy's writes. The vault (``disk``) passes the person's
rules but is held to the shape, so a vault bug fails its file's round
rather than storing a bad shape. Anyone else is a person: a tab, a share
editor, the AI, an import, a route.

The shape (S), of what a write leaves on a page root, checked for the keys
the write touches:

- S1. A file of W is filed first in a folder of W (W(folders[0]) == W),
  then only in folders of no work folder, and those only while it is in
  the library.
- S2. Any other page is filed in no folder of a work folder.
- S3. No ``parent_page`` on a file, and none that names a file.
- S4. ``library`` is true or absent, ``file_name`` a string, both only on
  a file; ``work_folder`` names a folder with the mark, or one this copy
  does not have.

A person, on a file P of W: the stamps, the document and ``parent_page``
are the vault's; ``folders[0]`` moves only when P ``follows``
(library.follows: a note where W pulls, which pull then moves on disk),
within W; ``folders[1:]`` are free while P is in the library (library
folders only), and clearing ``library`` drops them; the title is free
while P is in the library (the paper's title; the file keeps its name) or
follows; labels, pins, notes and comments are free. Trash and restore
only when P follows, and no restore of what the vault trashed. A person
files a new page in W only as a note at ``folders[0]`` where W pulls,
which the rule stamps as W's (``creation``). Inside W the folder tree is
the disk's (T): no rename, move, delete of or insert below a folder under
W; W itself may be renamed and moved out of every work folder, never
deleted; the mark is the vault's. Twin of
frontend/src/explorer/workFolders.js; tests/shared/workfiling.json holds
the two to the same answers.
"""

from . import marks
from .library import STAMPS, follows, work_folder_of

WRITERS = ("person", "vault", "mirror")

# The words a person reads, by kind: the routes, the AI tools and the
# client's twin say these.
WORDS = {
    "filed": "A work folder's file is filed by its directory: add it to the library to file it in library "
             "folders as well.",
    "named": "A work folder's file is named by its file: rename it on disk.",
    "trash": "A work folder's file leaves when its file is deleted or untracked: use Choose files….",
    "restore": "It comes back when its file is tracked again.",
    "directories": "A work folder's directories follow the disk.",
    "remove": "Remove the synced vault to delete its folder.",
    "document": "That file's PDF is read where it is: it cannot be changed in Gamma.",
}


class Refused(Exception):
    """A write the rule refuses: ``kind`` (a key of ``WORDS``) and
    ``detail``, the words a person reads."""

    def __init__(self, kind: str):
        self.kind, self.detail = kind, refusal_text(kind)
        super().__init__(self.detail)


def refusal_text(kind: str) -> str:
    return WORDS[kind]


def writer_of(client: str) -> str:
    """The writer an op client is: ``sync`` the mirror, ``disk`` the vault,
    any other a person."""
    return {"sync": "mirror", "disk": "vault"}.get(client or "", "person")


def _folders(value) -> list:
    return [f for f in value if isinstance(f, str)] if isinstance(value, list) else []


def _merged(before: dict, patch: dict) -> dict:
    out = dict(before)
    for key, value in patch.items():
        if value is None:
            out.pop(key, None)
        else:
            out[key] = value
    return out


def _shape(tree: marks.Tree, after: dict, touched: dict, target_is_file: bool) -> None:
    """S1–S4 on the properties ``after`` a write, for what it ``touched``."""
    w = work_folder_of(after)
    stamp = touched.get("work_folder")
    if stamp is not None and (not isinstance(stamp, str) or not stamp or (stamp in tree and not tree.mark(stamp))):
        raise Refused("filed")
    name = touched.get("file_name")
    if name is not None and (not isinstance(name, str) or not w):
        raise Refused("named")
    library = touched.get("library")
    if library is not None and (library is not True or not w):
        raise Refused("filed")
    if touched.get("parent_page") is not None and (w or target_is_file):
        raise Refused("filed")
    if "folders" in touched or (stamp is not None and w):
        folders = _folders(after.get("folders"))
        if w:
            if not folders or tree.work_of(folders[0]) != w or any(tree.work_of(f) for f in folders[1:]):
                raise Refused("filed")
            if len(folders) > 1 and after.get("library") is not True:
                raise Refused("filed")
        elif any(tree.work_of(f) for f in folders):
            raise Refused("directories")


def root_patch(tree: marks.Tree, before: dict, patch: dict | None, content: str | None, writer: str,
               *, target_is_file: bool = False) -> dict | None:
    """The props patch of a page root ``set`` as stored, or ``Refused``:
    ``before`` the page's properties, ``patch`` the op's (a null removes a
    key; None for none), ``content`` its new title (None for none),
    ``target_is_file`` whether the ``parent_page`` it sets names a file.
    Clearing ``library`` on a file drops its ``folders[1:]`` in the same
    patch."""
    if writer == "mirror":
        return patch
    before = before if isinstance(before, dict) else {}
    out = dict(patch) if patch else {}
    if "library" in out and out["library"] is None and work_folder_of(_merged(before, out)):
        have = _folders(out["folders"] if "folders" in out else before.get("folders"))
        if len(have) > 1:
            out["folders"] = have[:1]
    if writer == "person":
        for key, kind in (("work_folder", "filed"), ("file_name", "named")):
            if key in out and out[key] != before.get(key):
                raise Refused(kind)
        w = work_folder_of(before)
        if w:
            follows_ = follows(before, tree.mark(w))
            if "doc" in out and out["doc"] != before.get("doc"):
                raise Refused("document")
            library_after = out["library"] is True if "library" in out else before.get("library") is True
            if "folders" in out:
                old, new = _folders(before.get("folders")), _folders(out["folders"])
                if new[:1] != old[:1] and not (follows_ and new and tree.work_of(new[0]) == w):
                    raise Refused("filed")
                if new[1:] and (not library_after or any(tree.work_of(f) for f in new[1:])):
                    raise Refused("filed")
            if content is not None and not (before.get("library") is True or library_after or follows_):
                raise Refused("named")
    _shape(tree, _merged(before, out), out, target_is_file)
    return out if patch is not None else None


def creation(tree: marks.Tree, props: dict, writer: str, *, target_is_file: bool = False) -> dict:
    """A new page's properties as stored, or ``Refused``. A person's stamps
    are dropped (importers carry old ones); a person's page filed in a work
    folder is a note at ``folders[0]`` of a W that pulls, stamped W's, in
    the library when W's switch says so, then filed in the filing targets
    among its other folders while it is."""
    if writer == "mirror":
        return props
    out = dict(props or {})
    if writer == "person":
        for key in STAMPS:
            out.pop(key, None)
        folders = _folders(out.get("folders"))
        if any(tree.work_of(f) for f in folders):
            w = tree.work_of(folders[0])
            mark = tree.mark(w) if w else None
            if not mark or any(tree.work_of(f) for f in folders[1:]) or out.get("doc") is not None \
                    or not mark["pull"]:
                raise Refused("directories")
            if out.get("parent_page") is not None:
                raise Refused("filed")
            out["work_folder"] = w
            if mark["library"]:
                targets = set(marks.filing_targets(tree))
                out["library"] = True
                out["folders"] = folders[:1] + [f for f in folders[1:] if f in targets]
            else:
                out["folders"] = folders[:1]
    _shape(tree, out, out, target_is_file)
    return out


def may_trash(tree: marks.Tree, props: dict, writer: str) -> None:
    """``Refused`` when ``writer`` may not send the page to Recently
    deleted: a person, a work folder's file that does not follow."""
    w = work_folder_of(props)
    if writer == "person" and w and not follows(props, tree.mark(w)):
        raise Refused("trash")


def may_restore(tree: marks.Tree, props: dict, writer: str) -> dict:
    """The page's properties as a restore leaves them, or ``Refused``. A
    file whose work folder carries no mark here (its vault removed while
    it was in Recently deleted) comes back as a library page, its stamps
    dropped; a person restores a file only when it follows and the vault
    did not trash it. A library page comes back filed in no folder of a
    work folder (S2: one it was filed in became one, or a directory of
    one, meanwhile), so in its library folders or at the top. The
    mirror's restore keeps what it carries."""
    if writer == "mirror":
        return props
    w = work_folder_of(props)
    mark = tree.mark(w) if w else None
    if w and mark is not None:
        if writer == "person" and (props.get("deleted_by_vault") or not follows(props, mark)):
            raise Refused("restore")
        return props
    out = {key: value for key, value in props.items() if key not in STAMPS} if w else dict(props)
    folders = _folders(out.get("folders"))
    if any(tree.work_of(f) for f in folders):
        out["folders"] = [f for f in folders if not tree.work_of(f)]
    return out


def tree_op(tree: marks.Tree, op: dict, writer: str) -> None:
    """``Refused`` when a person's op on the folders pseudo-page (``{op,
    id, parent?, content?, props?}``) changes the folder tree inside a work
    folder, or a mark; a reorder under the same parent and props other than
    the mark pass."""
    if writer != "person":
        return
    kind, folder = op.get("op"), op.get("id")
    props = op.get("props")
    if isinstance(props, dict) and "vault" in props:
        raise Refused("directories")
    w = tree.work_of(folder)
    if kind == "set":
        if op.get("content") is not None and w and w != folder:
            raise Refused("directories")
    elif kind == "insert":
        if tree.work_of(op.get("parent")):
            raise Refused("directories")
    elif kind == "move":
        parent = op.get("parent")
        if folder in tree and tree.parent[folder] == parent:
            return
        if (w and w != folder) or tree.work_of(parent):
            raise Refused("directories")
    elif kind == "delete":
        if w:
            raise Refused("remove" if w == folder else "directories")
        below = tree.subtree(folder)
        if any(root in below for root in tree.roots()):
            raise Refused("remove")
