"""A work folder's file by its path, both ways (docs/dev/work_folders.md
"Paths"): ``page_path`` is a page's path in its work folder, ``page_at_path``
the pages at a path, ``page_at_href`` the pages a note's relative link
names, ``work_of_path`` the work folder a path's first segment names. A
path is the names of the directories below W and the
file's ``file_name``, joined with ``/``, as the folder tree and the vault's
stamps give them; nothing here reads the disk (feedback's ``file_path``
prefers the vault's rows on the computer that holds it).

One rule for a name typed by a person or a model, a work folder's or a
directory's or a file's: exactly, else ignoring case by
``blocks_store.named`` (``lower()``, as the web app and the extension
compare paths), so one typed path names one place to all of them; both
sides NFC, as the vault's link reader compares them, since a name stands
as the disk gave it (NFD from a Mac) and a typed one is NFC.
"""

import json
import posixpath
import re
import unicodedata
from urllib.parse import unquote

from .. import blocks_store  # a module import, as marks: blocks_store's page writers import this package
from ..db import SQL_FILE_NAME
from ..documents.formats import extension_of
from ..markdown_zip_links import _SCHEME_RE
from .library import work_folder_of
from .marks import Tree

# The extensions LaTeX adds to a name ``\input``, ``\include`` and
# ``\bibliography`` give without one, tried in this order.
IMPLIED_EXTENSIONS = (".tex", ".bib")

_SEPARATORS = re.compile(r"[\\/]")


def _nfc(text: str) -> str:
    return unicodedata.normalize("NFC", text or "")


def _parts(path: str) -> list[str]:
    """A path's segments: split on ``/`` and ``\\``, empty ones and ``.``
    dropped."""
    return [p for p in _SEPARATORS.split(str(path or "")) if p and p.strip() not in ("", ".")]


def _dir_names(tree: Tree, folder: str, w: str) -> list[str] | None:
    """The names of the directories from below ``w`` down to ``folder``;
    None when ``folder`` is not in ``w``."""
    names, at, seen = [], folder, set()
    while at != w:
        if at not in tree.parent or at in seen:
            return None
        seen.add(at)
        names.append(tree.names.get(at, ""))
        at = tree.parent[at]
    return list(reversed(names))


def dir_path(tree: Tree, folder: str, w: str) -> str:
    """The path of the directory ``folder`` in the work folder ``w``: the
    names below W down to it, joined with ``/`` ("" for W itself, and for
    a folder not in W)."""
    return "/".join(_dir_names(tree, folder, w) or [])


def path_top(tree: Tree, w: str, shared: str = "") -> str:
    """The folder a path in the work folder ``w`` is measured from: W, or
    for a folder share of a directory below W (``shared``) that
    directory's parent, so a path the share answers names no folder above
    the shared one, which its view never shows."""
    if shared and shared != w and tree.work_of(shared) == w:
        return tree.parent.get(shared, w)
    return w


def page_path(tree: Tree, props: dict, *, title: str = "", shared: str = "") -> str:
    """The page's path in its work folder: the names of its directories
    (from its ``folders[0]`` up to W, W left out; for a folder share,
    ``shared``, up to ``path_top``) and its ``file_name``, joined with
    ``/``; a page without ``file_name`` (a note not yet named by its vault)
    its ``title``; "" for a page in no work folder, and for one whose
    ``folders[0]`` is not in its W (no path names it).
    tests/shared/workpaths.json holds the rule."""
    w = work_folder_of(props)
    if not w:
        return ""
    folders = props.get("folders") if isinstance(props.get("folders"), list) else []
    top = path_top(tree, w, shared)
    names = _dir_names(tree, folders[0], top) if folders and isinstance(folders[0], str) else None
    if names is None:
        return ""
    name = props.get("file_name") if isinstance(props.get("file_name"), str) else ""
    return "/".join([*names, name]) if name else title


def work_folders_named(tree: Tree, name_or_id: str) -> list[str]:
    """The work folders' roots ``name_or_id`` names: itself when it is a
    root's id; else the roots whose name is it exactly, else ignoring case
    (``blocks_store.named``). Empty for none or a blank name; several when
    roots share the name, for the caller to refuse."""
    roots = tree.roots()
    if name_or_id in roots:
        return [name_or_id]
    if not isinstance(name_or_id, str) or not name_or_id.strip():
        return []
    return [w for w, _ in blocks_store.named([(w, _nfc(tree.names.get(w, ""))) for w in roots], _nfc(name_or_id))]


def _children(tree: Tree, folders: list[str]) -> list[tuple[str, str]]:
    wanted = set(folders)
    return [(f, _nfc(tree.names.get(f, ""))) for f in tree.order if tree.parent.get(f) in wanted]


def page_at_path(conn, tree: Tree, work: str, path: str) -> list[str]:
    """The live root pages at ``path`` in the work folder ``work``: its
    directories descended by name, then the leaf a file's ``file_name``; a
    note page by its title (a ``.md`` dropped) when no file carries the
    name; a leaf without an extension that matched nothing tried with
    IMPLIED_EXTENSIONS (LaTeX names its files without one, and the outline
    prints them as written). Each name exactly, else ignoring case. Several
    pages (twin directories, twin titles) are all answered, for the caller
    to refuse naming them; none is ``[]``. A file named exactly as typed is
    one seek of ``idx_ub_file_name``; the rest reads the directory's rows (a
    link that names nothing in a directory of 10,000 files reads them all)."""
    parts = _parts(path)
    if not parts or work not in tree:
        return []
    level = [work]
    for name in parts[:-1]:
        level = [f for f, _ in blocks_store.named(_children(tree, level), _nfc(name))]
        if not level:
            return []
    exact = [r[0] for r in conn.execute(
        f"SELECT id FROM unified_blocks INDEXED BY idx_ub_file_name WHERE parent_id = 'root' "
        f"AND work_folder IS NOT NULL AND {SQL_FILE_NAME} = ? AND json_extract(properties, '$.file_name') = ? "
        "AND work_folder = ? AND first_folder IN (SELECT value FROM json_each(?))",
        (parts[-1].strip(), parts[-1].strip(), work, json.dumps(level)))]
    if exact:
        return exact
    leaf = _nfc(parts[-1])
    rows = conn.execute(
        "SELECT id, json_extract(properties, '$.file_name'), content, json_type(properties, '$.doc') "
        "FROM unified_blocks INDEXED BY idx_ub_dir WHERE parent_id = 'root' AND work_folder IS NOT NULL "
        "AND work_folder = ? AND first_folder IN (SELECT value FROM json_each(?))", (work, json.dumps(level))).fetchall()
    files = [(r[0], _nfc(r[1])) for r in rows if isinstance(r[1], str) and r[1]]
    found = blocks_store.named(files, leaf)
    if not found:
        title = leaf[:-3] if leaf.lower().endswith(".md") else leaf
        found = blocks_store.named([(r[0], _nfc(r[2])) for r in rows if r[3] is None], title)
    if not found and not extension_of(leaf):
        for ext in IMPLIED_EXTENSIONS:
            found = blocks_store.named(files, leaf + ext)
            if found:
                break
    return [page_id for page_id, _ in found]


def work_of_path(conn, tree: Tree, path: str) -> tuple[str, str] | None:
    """``(W, the rest of the path)`` when ``path``'s first segment names a
    work folder (``work_folders_named``); None when it names none.
    ValueError naming the roots when several carry the name."""
    parts = _parts(path)
    if not parts:
        return None
    found = work_folders_named(tree, parts[0])
    if len(found) > 1:
        raise ValueError(f"{len(found)} work folders are named {parts[0]!r} ({', '.join(found)}): name one by its id")
    return (found[0], "/".join(parts[1:])) if found else None


def is_relative_href(href: str) -> bool:
    """Whether a link's target is a path relative to its note: not blank,
    no scheme (the vault's link reader's ``_SCHEME_RE``: a one-letter one
    too, so a Windows path "C:/paper/main.tex" is none), not starting with
    ``/``, ``#`` or ``?``, read with the spaces around it dropped. The
    client's ``isRelativeHref`` (explorer/workPaths.js) is its twin."""
    href = str(href or "").strip()
    return bool(href) and href[0] not in "/#?" and not _SCHEME_RE.match(href)


def _climbs(path: str) -> bool:
    return path == ".." or path.startswith("../")


def page_at_href(conn, tree: Tree, work: str, base_dir: str, href: str, *, allows=None) -> list[str]:
    """The live root pages a link of a note names in the work folder
    ``work``: ``href`` as the note wrote it, relative to the note's
    directory ``base_dir`` (``dir_path``'s spelling, "" for W; a directory,
    never the note's path: a note made in Gamma has no ``file_name`` until
    its vault names it). A ``#…`` or ``?…`` suffix is dropped and ``%20``
    read; a link ``is_relative_href`` refuses names nothing (``[]``; the
    predicate and its twin are pinned by tests/shared/workpaths.json). Tried
    through ``page_at_path`` in the vault's order (markdown_zip_links
    ``resolve_path``): relative to the note's directory, then from W's top;
    a candidate that climbs above W is skipped. ``allows(page_id)`` (a
    share's reach) keeps a candidate's pages to those it allows, one left
    with none tried as if it named nothing, so what is out of reach changes
    no answer. No search by the bare name: a typed path names a place, and
    the vault's own rewrite took that case when it pulled the note."""
    if not is_relative_href(href):
        return []
    target = unquote(re.split(r"[#?]", href.strip(), maxsplit=1)[0]).replace("\\", "/")
    if not target.strip() or target.startswith("/"):
        return []
    base = "/".join(_parts(base_dir))
    tried = []
    for candidate in (posixpath.normpath(posixpath.join(base, target)), posixpath.normpath(target)):
        if candidate in tried or _climbs(candidate):
            continue
        tried.append(candidate)
        found = [i for i in page_at_path(conn, tree, work, candidate) if allows is None or allows(i)]
        if found:
            return found
    return []
