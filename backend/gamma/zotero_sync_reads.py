"""What the units of a Zotero link's round read, shared by
gamma/zotero_sync_items.py, gamma/zotero_sync_pdfs.py,
gamma/zotero_sync_pages.py, gamma/zotero_sync_gone.py, the first link's
match and the field resolvers: Gamma's side of a page (``Side``, ``page_side``: its root, labels,
folders and stored PDF, whose MD5 is read only when a decision asks for
it), the projections both sides are compared in (``gamma_item`` /
``zotero_item``, a standalone PDF's and a note page's), the row rules every
unit shares (``HELD_ROWS``, ``moving_pdf``, ``entries_done``), and Zotero's
objects read by key ahead of their units: an item with the next due ones
(``read_ahead``), and a fill's item with no row with its children too
while the queue holds them all (``read_unit``, ``_listed_children``),
else child list after child list.

Of the engine's parts it imports only gamma/zotero_sync_blocks.py and
gamma/zotero_sync_rows.py.
"""

import json
from typing import NamedTuple

from . import storage, zotero_local, zotero_merge
from . import zotero_remote as zr
from .bibtex import pinned_key
from .blocks_store import (BLOCK_COLUMNS, FOLDERS, LABELS, TRASH, attachment_file, block_to_dict, existing_in, filing,
                           label_names, page_attachment, write_lock)
from .db import connect_pages_db, page_now
from .zotero_annotations import file_md5
from .zotero_sync_blocks import note_hash, note_page_html
from .zotero_sync_rows import Round
from .zotero_web import ZoteroNotFound

READ_AHEAD = 50            # due items (and attachments of due files) read by key at once, ahead of their units
CHILD_READS = 5000         # queued children a round reads by key for their parents at most (``_parents_known``)...
CHILDREN_PER_ITEM = 30     # ...while there are at most this many per item they may serve (``_worth_reading``)
CHILDREN_AHEAD = 250       # children of the next items read ahead read by key with an item's at most
HELD_ROWS = ("live", "waiting", "pending")  # the states in which an attachment row is its page's PDF


# --- Gamma's side: a page as a unit reads it --------------------------------------------

def collection_map(conn) -> dict[str, str]:
    """``{collection key: folder id}`` of the collections that take part in
    item merges: live rows whose folders exist (a trashed one is out of both
    sides)."""
    folders = {row[0] for row in conn.execute("SELECT id FROM unified_blocks WHERE page_id = ?", (FOLDERS,))}
    return {key: block for key, block in conn.execute(
        "SELECT key, block_id FROM zotero_objects WHERE kind = 'collection' AND state = 'live'") if block in folders}


class _FileMD5:
    """A stored PDF's MD5, read from the file the first time a decision asks
    for it (``Side.md5``), never for a page whose visit decides nothing."""

    def __init__(self, path):
        self.path, self.value = path, None

    def get(self) -> str:
        if self.value is None:
            self.value = file_md5(self.path)
        return self.value


class Side(NamedTuple):
    """What Gamma holds of a page, read for one unit (its root only).
    ``block``: the root (None: gone for good); ``trashed``: in Recently
    deleted; ``stamp``: what the unit's compare-and-set checks; ``labels``
    ``{label id: name}``; ``folders`` its folder ids that exist; ``doc_id``
    and ``md5`` of its stored PDF ("" for none; the MD5 read from the file
    only when asked, ``file_md5``), ``fsig`` the file's ``[size,
    mtime_ns]`` (``[]``); ``has_pdf``: a PDF, stored or shown from an
    address; ``note_hash``: a note page's note as Gamma would send it,
    hashed (read with ``note``)."""
    page_id: str
    block: dict | None
    trashed: bool
    stamp: dict
    labels: dict
    folders: list
    doc_id: str
    md5_of: "str | _FileMD5"
    has_pdf: bool
    fsig: list = []
    note_hash: str = ""

    @property
    def md5(self) -> str:
        return self.md5_of if isinstance(self.md5_of, str) else self.md5_of.get()

    @property
    def live(self) -> bool:
        return self.block is not None and not self.trashed

    @property
    def props(self) -> dict:
        return (self.block or {}).get("properties") or {}

    @property
    def title(self) -> str:
        return (self.block or {}).get("content") or ""


def _stored_md5(conn, page_id: str, doc_id: str, path) -> tuple["str | _FileMD5", list]:
    """The MD5 and ``[size, mtime_ns]`` of a page's stored PDF: the one its
    attachment row recorded while the file is the same (its size and time
    unchanged), so no round reads a whole library's PDFs again; else read
    when a decision asks for it (``_FileMD5``: ``zotero_annotations.file_md5``;
    a strip rewrites a file in place under its name, which this sees as a
    change)."""
    st = path.stat()
    fsig = [st.st_size, st.st_mtime_ns]
    for gmd5, known, doc in conn.execute(
            "SELECT json_extract(detail, '$.gmd5'), json_extract(detail, '$.fsig'), json_extract(detail, '$.doc_id') "
            "FROM zotero_objects WHERE kind = 'attachment' AND page_id = ?", (page_id,)).fetchall():
        if gmd5 and doc == doc_id and known and json.loads(known) == fsig:
            return gmd5, fsig
    return _FileMD5(path), fsig


def file_sig(ws: str, doc_id: str) -> list:
    """A stored PDF's ``[size, mtime_ns]`` (``[]`` for none), as an
    attachment row keeps it beside the MD5 it read (``_stored_md5``)."""
    path = storage.open_path(ws, f"{doc_id}.pdf") if doc_id else None
    if not path:
        return []
    st = path.stat()
    return [st.st_size, st.st_mtime_ns]


def page_side(conn, ws: str, page_id: str, *, note: bool = False) -> Side:
    row = conn.execute(f"SELECT {BLOCK_COLUMNS} FROM unified_blocks WHERE id = ?", (page_id,)).fetchone() \
        if page_id else None
    if not row or row[1] not in ("root", TRASH):
        return Side(page_id, None, False, {page_id: None}, {}, [], "", "", False)
    block = block_to_dict(row)
    props = block["properties"] or {}
    known = label_names(conn)
    attachment = page_attachment(props)
    doc_id = attachment_file(attachment)
    path = storage.open_path(ws, f"{doc_id}.pdf") if doc_id else None
    md5, fsig = _stored_md5(conn, page_id, doc_id, path) if path else ("", [])
    return Side(page_id, block, block["parent_id"] == TRASH, zotero_local.stamps(conn, [page_id]),
                 {lid: known[lid] for lid in filing(props, LABELS) if lid in known},
                 existing_in(conn, FOLDERS, filing(props, FOLDERS)), doc_id if path else "", md5,
                 attachment is not None, fsig, note_hash(note_page_html(conn, page_id)) if note else "")


# --- the projections both sides are compared in (docs/dev/zotero_sync.md#merging) -------

def gamma_item(side: Side, inverse: dict, item_type: str) -> dict:
    """Gamma's page as its item would be sent (``zotero_merge.page_projection``)."""
    props = side.props
    return zotero_merge.page_projection(props.get("meta"), pinned_key(props), side.title, item_type,
                                        side.labels.values(), [inverse[f] for f in side.folders if f in inverse])


def zotero_item(r: Round, data: dict, live) -> dict:
    return zotero_merge.item_projection(data, live_collections=live, auto_tags=r.link["auto_tags"])


def gamma_standalone(side: Side, inverse: dict, *, title: bool = True) -> dict:
    """A standalone PDF's page as its attachment would hold it
    (``zotero_merge.page_standalone_projection``): its title, tags and
    collections (the record, Gamma's alone until it gets a parent item,
    never travels)."""
    return zotero_merge.page_standalone_projection(side.title, side.labels.values(),
                                                   [inverse[f] for f in side.folders if f in inverse],
                                                   with_title=title)


def zotero_standalone(r: Round, data: dict, live, *, title: bool = True) -> dict:
    return zotero_merge.standalone_projection(data, live_collections=live, auto_tags=r.link["auto_tags"],
                                              title=title)


def gamma_note(side: Side, inverse: dict) -> dict:
    """A note page as its standalone note would be sent: its tags, its
    collections and its note (``note_hash``: the text travels whole, a
    block-level diff on the way in)."""
    return {**gamma_standalone(side, inverse, title=False), "note": side.note_hash}


def zotero_note(r: Round, data: dict, live) -> dict:
    return {**zotero_standalone(r, data, live, title=False), "note": note_hash(str(data.get("note") or ""))}


def flat_fields(projection) -> dict:
    """A projection's fields as one dict (a record's ``fields`` members
    beside its other keys), as a conflict names them."""
    projection = projection if isinstance(projection, dict) else {}
    members = projection.get(zotero_merge.FIELDS)
    return {**{k: v for k, v in projection.items() if k != zotero_merge.FIELDS},
            **(members if isinstance(members, dict) else {})}


# --- rows and queue entries every unit shares -------------------------------------------

def moving_pdf(rows: dict) -> dict | None:
    """A page's standalone PDF whose move under the page's new item is
    still to be sent (``detail.parent_to``), None for none."""
    return next((row for row in rows.values() if row["kind"] == "attachment" and not row["parent"]
                 and row["detail"].get("parent_to") and row["state"] in HELD_ROWS), None)


def entries_done(r: Round, ids) -> None:
    """Queue entries done, in a transaction of their own."""
    with connect_pages_db(r.ws) as conn:
        write_lock(conn)
        zr.done(conn, list(ids))
        conn.commit()


# --- Zotero's side: objects read by key, ahead of their units ---------------------------

def keep_ahead(r: Round, *objs) -> dict:
    """The round's objects read ahead of their units (``read_ahead``), with
    ``objs`` (just read: an attachment whose file is queued, children read
    for their parents) put in."""
    r.read_ahead.update({obj["key"]: obj for obj in objs if isinstance(obj, dict) and obj.get("key")})
    return r.read_ahead


def take_ahead(r: Round, key: str, version: int = 0) -> dict | None:
    """The object ``key`` as the round read it ahead (taken once), unless
    it was read before ``version`` was listed."""
    obj = r.read_ahead.pop(key, None)
    return obj if obj is not None and int(obj.get("version") or 0) >= int(version or 0) else None


def read_ahead(r: Round, key: str, kind: str, version: int = 0) -> dict | None:
    """The object ``key`` whole, as Zotero holds it now (None: it has none):
    read by key together with the next due entries of ``kind`` ("top", or
    "file" for their attachments), ``READ_AHEAD`` a read, and kept for their
    units this round (each taken once; one read before a newer version was
    listed is read again)."""
    ahead = keep_ahead(r)
    obj = take_ahead(r, key, version)
    if obj is not None:
        return obj
    with connect_pages_db(r.ws) as conn:
        due = zr.take(conn, now=page_now(), limit=READ_AHEAD, kinds=kind, exclude=r.excluded)
    prefix = zr.FILE_PREFIX if kind == "file" else ""
    keys = [key] + [k for k in (e.id[len(prefix):] for e in due) if k != key and k not in ahead][:READ_AHEAD - 1]
    found = zr.fetch_items(r.client, r.library, keys).objects
    ahead.update({k: v for k, v in found.items() if k != key})
    return found.get(key)


def read_unit(r: Round, key: str, version: int = 0, listed: zr.Entry | None = None) -> zr.Unit:
    """``zotero_remote.fetch_unit`` with the item read ahead (``read_ahead``)
    and, for an item its own queue entry (``listed``) brings, its children
    as the queue holds them when it holds them all (``_listed_children``):
    else read child list after child list."""
    item = read_ahead(r, key, "top", version)
    if item is None:
        return zr.Unit(None, [])
    children = _listed_children(r, item, listed) if listed is not None else None
    if children is not None:
        return zr.Unit(item, children)
    try:
        return zr.Unit(item, zr.descendants(r.client, r.library, item))
    except ZoteroNotFound:
        return zr.Unit(None, [])


def _listed_children(r: Round, item: dict, entry: zr.Entry) -> list[dict] | None:
    """The children of a top-level item (as ``descendants`` reads them) when
    the queue holds every one of them: the item queued by a listing from
    the start (its entry at a version above 0 and at most
    ``whole_version``, ``zotero_remote.get_state``) with no row, none of its
    queued children with one and no row under any of them, every queued
    child's parent read (``_parents_known``). They are read by key, with
    those of the items read ahead (``_read_listed``). None when the queue
    cannot know them: their child lists are read then."""
    key = item["key"]
    whole = int(r.state.get("whole_version") or 0)
    if (item.get("data") or {}).get("parentItem") or not 0 < int(entry.version or 0) <= whole:
        return None
    if not zr.holds_children(item):
        return []  # (as ``descendants``: nothing of it to read)
    # the link keeps rows of it (or under it): a child a unit took off the queue may hang under it
    rows_of = "SELECT 1 FROM zotero_objects WHERE key IN (SELECT value FROM json_each(?)) OR parent IN " \
              "(SELECT value FROM json_each(?)) LIMIT 1"
    with connect_pages_db(r.ws) as conn:
        if conn.execute(rows_of, (json.dumps([key]),) * 2).fetchone():
            return None
    if not _parents_known(r):
        return None
    with connect_pages_db(r.ws) as conn:
        family = zr.children_of(conn, [key])
        family.update(zr.children_of(conn, family))  # (an attachment's annotations, a note's pictures)
        if family and conn.execute(rows_of, (json.dumps(sorted(family)),) * 2).fetchone():
            return None
    return _family(item, _read_listed(r, key, family))


def _parents_known(r: Round) -> bool:
    """Whether every queued child has its parent read (``zotero_remote.
    set_parents``): those not read yet are read now, by key
    (``CHILD_READS`` a round at most, while it pays: ``_worth_reading``),
    and kept for their items' units this round. One Zotero no longer
    answers for or that is top-level now is no item's child for the rest of
    the round."""
    room = CHILD_READS - len(r.children_read)
    with connect_pages_db(r.ws) as conn:
        unread = zr.unread_children(conn, limit=max(0, room) + 1, exclude=r.children_read)
        if unread and r.children_worth is None:
            r.children_worth = _worth_reading(conn, r)
    if not unread:
        return True
    if room <= 0 or not r.children_worth:
        return False
    found = zr.fetch_items(r.client, r.library, unread[:room]).objects
    r.children_read.update(unread[:room])
    with connect_pages_db(r.ws) as conn:
        write_lock(conn)
        zr.set_parents(conn, {k: (str((obj.get("data") or {}).get("parentItem") or ""),
                                  int(obj.get("version") or 0)) for k, obj in found.items()})
        conn.commit()
    keep_ahead(r, *found.values())
    return len(unread) <= room


def _worth_reading(conn, r: Round) -> bool:
    """Whether reading the queued children by key for their parents pays
    this round: at most ``CHILDREN_PER_ITEM`` of them not read yet per item
    they may serve (one a listing from the start queued, with no row). Past
    that (items holding hundreds of annotations each) their child lists, a
    hundred a page, cost fewer reads than reading them by key, fifty a
    read, for their parents and again for their items."""
    unread = conn.execute("SELECT COUNT(*) FROM zotero_queue WHERE kind = 'child' AND parent = ''").fetchone()[0]
    items = conn.execute(
        "SELECT COUNT(*) FROM zotero_queue q WHERE q.kind = 'top' AND q.version > 0 AND q.version <= ? "
        "AND NOT EXISTS (SELECT 1 FROM zotero_objects o WHERE o.key = q.id)",
        (int(r.state.get("whole_version") or 0),)).fetchone()[0]
    return unread <= CHILDREN_PER_ITEM * items


def _read_listed(r: Round, key: str, family: dict) -> dict[str, dict]:
    """The queued children ``family`` (``zotero_remote.children_of``'s) of
    the item ``key``, whole: as read ahead, else read by key together with
    those of the next items read ahead that a listing from the start
    queued, whole families up to ``CHILDREN_AHEAD`` children, which are
    kept for their units."""
    out, missing = {}, []
    for k, (_, version) in family.items():
        obj = take_ahead(r, k, version)
        if obj is not None:
            out[k] = obj
        else:
            missing.append(k)
    if not missing:
        return out
    tops = [k for k, obj in r.read_ahead.items() if k != key and not (obj.get("data") or {}).get("parentItem")]
    ahead: list[str] = []
    if tops:
        with connect_pages_db(r.ws) as conn:
            listed = {row[0] for row in conn.execute(
                "SELECT q.id FROM zotero_queue q WHERE q.id IN (SELECT value FROM json_each(?)) AND q.kind = 'top' "
                "AND q.version > 0 AND q.version <= ? AND NOT EXISTS (SELECT 1 FROM zotero_objects o "
                "WHERE o.key = q.id)", (json.dumps(tops), int(r.state.get("whole_version") or 0)))}
            level1 = zr.children_of(conn, listed)
            level2 = zr.children_of(conn, level1)
        families: dict[str, list] = {}
        for k, (parent, _) in [*level1.items(), *level2.items()]:
            families.setdefault(level1[parent][0] if parent in level1 else parent, []).append(k)
        for top in (k for k in tops if k in listed):  # (in the order their units come)
            more = [k for k in families.get(top, ()) if k not in family and k not in r.read_ahead]
            if len(ahead) + len(more) > CHILDREN_AHEAD:
                break
            ahead += more
    found = zr.fetch_items(r.client, r.library, missing + ahead).objects
    out.update({k: found[k] for k in missing if k in found})
    keep_ahead(r, *(found[k] for k in ahead if k in found))
    return out


def _family(item: dict, found: dict) -> list[dict]:
    """``found`` (an item's children read by key) in the order
    ``descendants`` gives them: the item's own as Zotero lists them (by
    ``dateAdded``), then those of each that holds children; one that names
    another parent now (moved since it was listed) left out, as a child
    list read now would."""
    def under(parent: str) -> list[dict]:
        return sorted((obj for obj in found.values() if (obj.get("data") or {}).get("parentItem") == parent),
                      key=lambda obj: (str((obj.get("data") or {}).get("dateAdded") or ""),
                                       int(obj.get("version") or 0), obj["key"]))

    out = under(item["key"])
    for child in list(out):
        if zr.holds_children(child):
            out.extend(under(child["key"]))
    return out
