"""A work folder's file by its path, both ways (docs/dev/work_folders.md
"Paths"): ``page_path`` is a page's path in its work folder, ``page_at_path``
the pages at a path, ``work_of_path`` the work folder a path's first
segment names. A path is the names of the directories below W and the
file's ``file_name``, joined with ``/``, as the folder tree and the vault's
stamps give them; nothing here reads the disk (feedback's ``file_path``
prefers the vault's rows on the computer that holds it).

One rule for a name typed by a person or a model, a work folder's or a
directory's or a file's: exactly, else ignoring case by
``blocks_store.named`` (``lower()``, as the web app and the extension
compare paths), so one typed path names one place to all of them.
"""

import json
import re

from .. import blocks_store  # a module import, as marks: blocks_store's page writers import this package
from ..documents.formats import extension_of
from .library import work_folder_of
from .marks import Tree

# The extensions LaTeX adds to a name ``\input``, ``\include`` and
# ``\bibliography`` give without one, tried in this order.
IMPLIED_EXTENSIONS = (".tex", ".bib")

_SEPARATORS = re.compile(r"[\\/]")


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


def page_path(tree: Tree, props: dict, *, title: str = "") -> str:
    """The page's path in its work folder: the names of its directories
    (from its ``folders[0]`` up to W, W left out) and its ``file_name``,
    joined with ``/``; a page without ``file_name`` (a note not yet named
    by its vault) its ``title``; "" for a page in no work folder."""
    w = work_folder_of(props)
    if not w:
        return ""
    name = props.get("file_name") if isinstance(props.get("file_name"), str) else ""
    if not name:
        return title
    folders = props.get("folders") if isinstance(props.get("folders"), list) else []
    names = _dir_names(tree, folders[0], w) if folders and isinstance(folders[0], str) else None
    return "/".join([*(names or []), name])


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
    return [w for w, _ in blocks_store.named([(w, tree.names.get(w, "")) for w in roots], name_or_id)]


def _children(tree: Tree, folders: list[str]) -> list[tuple[str, str]]:
    wanted = set(folders)
    return [(f, tree.names.get(f, "")) for f in tree.order if tree.parent.get(f) in wanted]


def page_at_path(conn, tree: Tree, work: str, path: str) -> list[str]:
    """The live root pages at ``path`` in the work folder ``work``: its
    directories descended by name, then the leaf a file's ``file_name``; a
    note page by its title (a ``.md`` dropped) when no file carries the
    name; a leaf without an extension that matched nothing tried with
    IMPLIED_EXTENSIONS (LaTeX names its files without one, and the outline
    prints them as written). Each name exactly, else ignoring case. Several
    pages (twin directories, twin titles) are all answered, for the caller
    to refuse naming them; none is ``[]``."""
    parts = _parts(path)
    if not parts or work not in tree:
        return []
    level = [work]
    for name in parts[:-1]:
        level = [f for f, _ in blocks_store.named(_children(tree, level), name)]
        if not level:
            return []
    rows = conn.execute(
        "SELECT id, json_extract(properties, '$.file_name'), content, json_type(properties, '$.doc') "
        "FROM unified_blocks WHERE parent_id = 'root' AND work_folder = ? "
        "AND first_folder IN (SELECT value FROM json_each(?))", (work, json.dumps(level))).fetchall()
    files = [(r[0], r[1]) for r in rows if isinstance(r[1], str) and r[1]]
    leaf = parts[-1]
    found = blocks_store.named(files, leaf)
    if not found:
        title = leaf[:-3] if leaf.lower().endswith(".md") else leaf
        found = blocks_store.named([(r[0], r[2] or "") for r in rows if r[3] is None], title)
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
