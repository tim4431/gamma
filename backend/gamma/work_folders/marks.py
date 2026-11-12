"""The folder tree as the work-folder rules read it (docs/dev/work_folders.md
"The mark"). A work folder's root folder W carries the mark,
``properties.vault = {pull, library}``, written by its vault alone: ``pull``
whether a person's changes to its notes reach the disk, ``library``
whether the files it makes pages of are library documents too. W(f), the
work folder a folder is in, is the nearest folder on f's chain, f
included, that carries the mark; none for a library folder and for an id
that names no folder. Twin of frontend/src/explorer/workFolders.js
``treeOf`` / ``workOf`` / ``markOf``."""

import json

from .. import blocks_store  # a module import: blocks_store's page writers import this module


def mark_of(props) -> dict | None:
    """A folder's mark from its properties, ``{pull, library}``; None for a
    folder that carries none."""
    mark = props.get("vault") if isinstance(props, dict) else None
    return {"pull": mark.get("pull") is True, "library": mark.get("library") is True} if isinstance(mark, dict) else None


class Tree:
    """The folder tree from ``tree_rows(conn, FOLDERS)``: ``parent`` (id ->
    parent id, ``FOLDERS`` at the top), ``names`` (id -> name), ``marks``
    (W -> {"pull", "library"}) and ``order`` (ids, parents first)."""

    def __init__(self, parent: dict, names: dict, marks: dict, order: list):
        self.parent, self.names, self.marks, self.order = parent, names, marks, order
        self._work: dict = {}

    @classmethod
    def read(cls, conn) -> "Tree":
        return cls.of(blocks_store.tree_rows(conn, blocks_store.FOLDERS))

    @classmethod
    def of(cls, rows) -> "Tree":
        """From ``(id, parent_id, content, properties)`` rows, properties a
        JSON string or a dict: the pure form."""
        parent, names, marks, order = {}, {}, {}, []
        for block_id, parent_id, content, props in rows:
            if isinstance(props, str):
                try:
                    props = json.loads(props or "{}")
                except ValueError:
                    props = {}
            parent[block_id], names[block_id] = parent_id or blocks_store.FOLDERS, content or ""
            order.append(block_id)
            mark = mark_of(props)
            if mark is not None:
                marks[block_id] = mark
        return cls(parent, names, marks, order)

    def __contains__(self, folder_id) -> bool:
        return folder_id in self.parent

    def work_of(self, folder_id) -> str:
        """W(f): the work folder ``folder_id`` is in, "" for none or an
        unknown id."""
        if folder_id in self._work:
            return self._work[folder_id]
        seen, at, found = set(), folder_id, ""
        while at in self.parent and at not in seen:
            if at in self.marks:
                found = at
                break
            seen.add(at)
            at = self.parent[at]
        self._work[folder_id] = found
        return found

    def mark(self, w) -> dict | None:
        return self.marks.get(w)

    def roots(self) -> list[str]:
        """Every work folder's root, in tree order."""
        return [f for f in self.order if f in self.marks]

    def subtree(self, folder_id) -> set:
        """``folder_id`` and every folder below it (``FOLDERS``: every
        folder); empty for an unknown id."""
        kids: dict = {}
        for f in self.order:
            kids.setdefault(self.parent[f], []).append(f)
        out = {folder_id} if folder_id in self.parent else set()
        todo = list(kids.get(folder_id, []))
        while todo:
            f = todo.pop()
            if f not in out:
                out.add(f)
                todo.extend(kids.get(f, []))
        return out


def filing_targets(tree: Tree) -> list[str]:
    """The folders a person may file a page in: every folder in no work
    folder, in tree order."""
    return [f for f in tree.order if not tree.work_of(f)]
