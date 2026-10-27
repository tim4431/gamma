"""Shared, JSON-safe import review and selection helpers.

Selection IDs name source records, never destination database IDs. Omitting
selection preserves the full-import API; an explicit empty list imports nothing.
"""
import json

from fastapi import HTTPException

from .blocks_store import folder_path
from .import_zip import entry_name


def parse_selection(raw: str | None) -> set[str] | None:
    if raw is None:
        return None
    try:
        values = json.loads(raw)
    except (TypeError, ValueError):
        values = None
    if not isinstance(values, list) or any(not isinstance(v, str) for v in values):
        raise HTTPException(status_code=400, detail="selection must be a JSON list of import item IDs")
    return set(values)


def validate_selection(selected, available):
    if selected is not None and selected - set(available):
        raise HTTPException(status_code=400, detail="selection contains unknown import items; review the file again")


def destination(conn, folder: str) -> tuple[str, list[str]]:
    """Where an import files its pages: ``folder`` (the form field) names
    the destination folder by id, "" the library's top. Returns ``(the
    folder's id, "" for the top; its path, the names from the top)``, what
    the import files below (``ops.ensure_filing`` ``under=``) and what its
    review shows the paths under. 400 for an id that is no folder."""
    if not folder:
        return "", []
    path = folder_path(conn, folder)
    if not path:
        raise HTTPException(status_code=400, detail="the destination folder does not exist")
    return folder, path


def archive_entries(zf):
    """The review's source column: every member, named as the zip walk
    names it (``import_zip.entry_name``), which the pages' ``source_paths``
    use too. A directory is one by that name, a backslash-separated one
    included (``is_dir`` reads the raw name)."""
    out = []
    for zi in zf.infolist():
        name = entry_name(zi)
        out.append({"path": name, "size": zi.file_size, "directory": zi.is_dir() or name.endswith("/"),
                    "status": "source"})
    return out


def selected_warnings(warnings, selected):
    return [w for w in warnings if selected is None or not w.get("selection_id") or w["selection_id"] in selected]
