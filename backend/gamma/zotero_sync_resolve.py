"""The resolvers of a Zotero link's field and file conflicts (see
docs/dev/zotero_sync.md#conflicts-and-the-log): what "theirs" writes for a
``zotero_field`` row (Zotero's values into a paper's record, a standalone
PDF's title or a folder) and for a ``zotero_file`` row (Zotero's file,
kept here when Gamma's went over it, as the page's PDF), each as a
person's edit would be, so the next round sends it back. The resolvers of
the note, lossy-note and annotation conflicts are
gamma/zotero_sync_blocks.py's.

Of the engine's parts it imports only gamma/zotero_sync_reads.py and
gamma/zotero_sync_rows.py.
"""

from . import storage, zotero_fields, zotero_local
from .blocks_store import BLOCK_COLUMNS, FOLDERS, block_to_dict
from .db import connect_pages_db
from .zotero_sync_reads import flat_fields
from .zotero_sync_rows import ACTOR, MOVED_ON, get_link, json_dict, read_rows


def _field_values(row: dict) -> tuple[dict, dict | None]:
    """A ``zotero_field`` row's ``(Zotero's values, Zotero's side then)``;
    an older row's ``base`` holds the values alone (None: nothing to tell)."""
    base = json_dict(row["base"])
    if isinstance(base.get("theirs"), dict):
        was = base.get("zbase")
        return base["theirs"], was if isinstance(was, dict) else None
    return base, None


def _moved_on(synced: dict | None, values: dict, was: dict | None) -> bool:
    return was is not None and synced is not None and any(
        name in was and flat_fields(synced.get("zbase")).get(name) != was[name] for name in values)


def resolve_field(ws: str, row: dict, choice: str) -> None:
    """``zotero_field`` "theirs": Zotero's values (kept in ``base``) into the
    page, or into the folder (``FOLDERS``: its name, its place), written as
    a person's edit would be (client ""), so the next round sends them back
    and both sides hold Zotero's. A paper's go into its record, a standalone
    PDF's title into the page's title (it has no record). Refused while
    Zotero changed one of them again since the conflict (the newer value
    came here then; the row stays open, to keep)."""
    if choice != "theirs":
        raise ValueError("this conflict offers keep or theirs")
    values, was = _field_values(row)
    if row["page_id"] == FOLDERS:
        return _resolve_folder(ws, row["block_id"], values, was)
    with connect_pages_db(ws) as conn:
        found = conn.execute(f"SELECT {BLOCK_COLUMNS} FROM unified_blocks WHERE id = ? AND parent_id = 'root'",
                             (row["page_id"],)).fetchone()
        rows = read_rows(conn, "page_id = ? AND kind IN ('item', 'attachment')", (row["page_id"],))
    if not found:
        raise ValueError("the page is gone")
    item = next((x for x in rows.values() if x["kind"] == "item"), None)
    standalone = None if item else next((x for x in rows.values() if not x["parent"]), None)
    if _moved_on(item or standalone, values, was):
        raise ValueError(MOVED_ON)
    if standalone is not None:
        title = " ".join(str(values.get("title") or "").split())
        if not title:
            raise ValueError("nothing of Zotero's is left to take")
        zotero_local.apply(ws, row["page_id"], [{"op": "set", "id": row["page_id"], "content": title}], actor=ACTOR,
                           client="")
        return None
    fields = {f: v for f, v in values.items() if f in zotero_fields.COMPARE_FIELDS}
    if not fields:
        raise ValueError("nothing of Zotero's is left to take")
    op = zotero_local.record_op(block_to_dict(found), fields)
    if op:
        zotero_local.apply(ws, row["page_id"], [op], actor=ACTOR, client="")
    return None


def _resolve_folder(ws: str, fid: str, values: dict, was: dict | None) -> None:
    """A folder's ``zotero_field`` "theirs": its collection's name, and its
    place under the folder of the parent collection Zotero names (the top
    when that collection has no live folder here)."""
    with connect_pages_db(ws) as conn:
        if not conn.execute("SELECT 1 FROM unified_blocks WHERE id = ? AND page_id = ?", (fid, FOLDERS)).fetchone():
            raise ValueError("the folder is gone")
        synced = next(iter(read_rows(conn, "kind = 'collection' AND block_id = ?", (fid,)).values()), None)
        if _moved_on(synced, values, was):
            raise ValueError(MOVED_ON)
        ops = []
        if "name" in values:
            ops.append(zotero_local.folder_rename(fid, " ".join(str(values["name"] or "").split()) or "Untitled"))
        if "parent" in values:
            parent = None
            found = conn.execute("SELECT block_id FROM zotero_objects WHERE kind = 'collection' AND key = ? "
                                 "AND state != 'trashed'", (str(values["parent"] or ""),)).fetchone()
            if found and found[0] and conn.execute("SELECT 1 FROM unified_blocks WHERE id = ? AND page_id = ?",
                                                   (found[0], FOLDERS)).fetchone():
                parent = found[0]
            ops.append(zotero_local.folder_move(fid, parent))
    if not ops:
        raise ValueError("nothing of Zotero's is left to take")
    zotero_local.apply(ws, FOLDERS, ops, actor=ACTOR, client="")


def resolve_file(ws: str, row: dict, choice: str) -> None:
    """``zotero_file`` "theirs": Zotero's file, kept when Gamma's went over it,
    becomes the page's PDF (its highlights relocate onto it), as a person's
    replacement would (client ""), so the next round sends it back."""
    if choice != "theirs":
        raise ValueError("this conflict offers keep or theirs")
    base = json_dict(row["base"])
    doc_id = base.get("doc_id")
    if not isinstance(doc_id, str) or not doc_id or not storage.open_path(ws, f"{doc_id}.pdf"):
        raise ValueError("Zotero's copy of the file is no longer kept here")
    if base.get("key") and base.get("synced"):  # Zotero's file as synced once Gamma's went: moved on since?
        with connect_pages_db(ws) as conn:
            att = read_rows(conn, "key = ?", (base["key"],)).get(base["key"])
        if att is not None and (att["detail"].get("md5") or "") != base["synced"]:
            raise ValueError("the PDF changed again since: this conflict can only be kept")
    link = get_link(ws)
    from .routers.pages import replace_attachment  # local: keep gamma.* free of the routers package
    replace_attachment(ws, row["page_id"], doc_id, "", str(base.get("filename") or ""), actor=ACTOR, client="",
                       owner=link["owner"] if link else "", check_held=False, check_zotero=False)
