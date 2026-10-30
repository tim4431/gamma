"""The Zotero side of a Zotero link's round (docs/dev/zotero_sync.md), over
a ``zotero_web.ZoteroClient`` and knowing nothing of Gamma's blocks.

- What changed in the library since a version: ``list_changes`` (None for
  Zotero's 304), put into the round's queue by ``queue_changes``.
- The queue (pages.db ``zotero_queue``): Zotero keys listed and not yet
  applied, Gamma pages to visit (``page_entry``: ``page:<id>``) and PDF
  transfers (``file_entry``: ``file:<attachment key>``), worked highest
  priority first with a backoff: ``enqueue``, ``take``, ``done``,
  ``drop``, ``backoff``, ``size``, ``due``, ``wake``, ``next_wait``
  (``when`` reads a stored time as an aware UTC datetime); a queued child's
  parent as a round read it (``set_parents``, ``unread_children``,
  ``children_of``); the link's state row (``zotero_state``): ``get_state``,
  ``set_state``; ``clear`` empties both and the object rows.
- What a unit reads: ``fetch_items``, ``fetch_collections`` (whole
  objects, the trash included, 50 keys a read, absent keys apart, which
  ``hard_deleted`` tells apart) and ``fetch_units`` / ``fetch_unit`` (an
  item with its children, the annotations of its PDFs and the pictures of
  its notes: ``descendants``, which reads what ``holds_children``).
- What a unit writes: ``Writes``, which sends creates, updates, retypes,
  trash and restore flags in dependency order, 50 objects a request, each
  with its own version, and answers each object's ``Outcome``;
  ``trash`` and ``restore`` for one-off writes.
- Files: ``download_pdf`` (spooled into the workspace's store, the
  storage limits asked as it arrives), ``upload_pdf``, ``download_image``
  (a note's picture); a file that cannot come now raises
  ``FileUnavailable`` with its reason.
- ``error_kind``: the word a link's status uses for what went wrong.

The queue and state helpers take an open pages.db connection and never
commit: the caller's ``with connect_pages_db(ws)`` block does.
"""

import io
import json
import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import NamedTuple

from fastapi import HTTPException

from . import storage
from .db import format_stamp, parse_stamp
from .logbuf import log
from .zotero_account import AccountError
from .zotero_merge import LINKED, NO_ACCESS, PDF_TYPE, UNAVAILABLE, is_pdf_attachment
from .zotero_web import (KEY_RE, NO_FILE_ACCESS, WRITE_LIMIT, ZoteroAuthError, ZoteroClient, ZoteroCompressed,
                         ZoteroConflict, ZoteroError, ZoteroNotFound, ZoteroQuota, ZoteroUnreachable)

ERROR_KINDS = ("offline", "auth", "not_connected", "library_gone", "busy", "other")

QUEUE_KINDS = ("top", "child", "collection", "page", "file")
PAGE_PREFIX, FILE_PREFIX = "page:", "file:"
PAGE_PRIORITY = 1 << 40    # a Gamma page to visit: before any Zotero object, whose priority is its version
OPENED_PRIORITY = 1 << 41  # a page someone opened, and its PDF: before everything else
CHILD_OFFSET = 1 << 39     # a child listed with its item comes after every item: the item's unit takes it
BACKOFF_S = 30             # the wait after a first failure, doubled after each next one
BACKOFF_MAX_S = 6 * 3600
MAX_ATTEMPTS = 8           # failures after which the engine gives an entry up ("could not apply")
REASON_MAX = 500           # characters of an error kept on a queue entry
STATE_FIELDS = ("library", "version", "local_cursor", "labels", "first", "restored_at", "whole_version")
_IN_CHUNK = 500            # keys per ``IN (…)`` of one query

# What a write is, in the order a flush sends them: a parent before what
# hangs under it, the trash flags last.
STAGES = ("collections", "items", "attachments", "notes", "annotations", "trash")
OBJECT_STAGES = STAGES[:-1]
OK, UNCHANGED, CONFLICT, EXISTS, TRANSIENT, REJECTED = "ok", "unchanged", "conflict", "exists", "transient", "rejected"
LANDED = (OK, UNCHANGED, CONFLICT, EXISTS)  # the object is in Zotero, whatever it holds
_SPLIT = (400, 413, 500)  # a request refused whole for one object in it: sent again in halves to find it

COMPRESSED, QUOTA_HERE, FAILED = "compressed", "quota_here", "failed"
IMAGE_MAX_BYTES = 25_000_000  # the largest note picture taken

Quota = ZoteroQuota        # 413 when an upload is authorized: the library owner's storage is full
Conflict = ZoteroConflict  # 412 when an upload is authorized: the attachment's file is not the one assumed


# --- what changed in Zotero ----------------------------------------------------------

class Changes(NamedTuple):
    """What changed in a library after a version (``list_changes``).
    ``version``: every change at or below it is in this listing (a change
    made while the listing was read is in it too, and is listed again next
    time). ``{key: version}`` of the ``collections``, the top-level items
    (``top``) and the child items (``children``: attachments, notes,
    annotations, a note's pictures) that changed, trashed ones included;
    ``deleted``: the keys Zotero removed for good, by kind
    ("collections", "items", "searches", "tags", "settings")."""
    version: int
    collections: dict[str, int]
    top: dict[str, int]
    children: dict[str, int]
    deleted: dict[str, list[str]]


def list_changes(client: ZoteroClient, library: str, since: int) -> Changes | None:
    """The library's changes after version ``since`` (0: everything), or
    None when Zotero answers that nothing changed (one small call with
    ``If-Modified-Since-Version``). The version is the first answer's, so
    the library moving mid-walk loses nothing. All items are listed before
    the top-level ones: an item made between the two reads is then never
    taken for a child."""
    since = max(0, int(since or 0))
    version = None
    if since:
        version = client.library_version(library, since)
        if version is None:
            return None
    collections, answered = client.versions(library, "collections", since)
    if version is None:
        version = answered
    everything, _ = client.versions(library, "items", since)
    top, _ = client.versions(library, "items/top", since)
    deleted, _ = client.deleted(library, since)
    children = {key: v for key, v in everything.items() if key not in top}
    return Changes(version, collections, top, children, deleted)


def queue_changes(conn, changes: Changes) -> int:
    """Queue what ``changes`` lists, newest first (an entry's priority is
    its version), the collections and top-level items before every child
    (``CHILD_OFFSET``: an item's unit reads its children and takes their
    entries off the queue, so a child listed with its item costs no read
    of its own), leaving out each key whose ``zotero_objects`` row is at
    the listed version already (the link's own write, or a row kept
    terminal at that version) unless the row is to be decided afresh (its
    ``detail.first``: a first link's or a force's pair), and each key
    Zotero removed for good (the engine applies ``changes.deleted`` at
    once). How many it queued."""
    gone = {key for keys in changes.deleted.values() for key in keys}
    listed = [(key, version, kind)
              for kind, found in (("collection", changes.collections), ("top", changes.top),
                                  ("child", changes.children))
              for key, version in found.items() if key not in gone]
    known = _row_versions(conn, [key for key, _, _ in listed])
    return enqueue(conn, [{"id": key, "kind": kind, "version": version,
                           "priority": version - CHILD_OFFSET if kind == "child" else version}
                          for key, version, kind in listed if known.get(key, (None, False)) != (version, False)])


def _row_versions(conn, keys) -> dict[str, tuple[int, bool]]:
    """``{key: (its row's version, whether the row is to be decided afresh)}``."""
    out = {}
    for i in range(0, len(keys), _IN_CHUNK):
        chunk = keys[i:i + _IN_CHUNK]
        marks = ",".join("?" * len(chunk))
        out.update((key, (version, bool(first))) for key, version, first in conn.execute(
            f"SELECT key, version, json_extract(detail, '$.first') FROM zotero_objects WHERE key IN ({marks})",
            chunk))
    return out


# --- the queue -----------------------------------------------------------------------

class Entry(NamedTuple):
    """One row of ``zotero_queue``."""
    id: str
    kind: str
    version: int
    priority: int
    attempts: int
    next_at: str
    reason: str


_ENTRY_COLUMNS = "id, kind, version, priority, attempts, next_at, reason"
# (a newer version is read again for its parent: a child moved to another item is listed anew)
_UPSERT = """INSERT INTO zotero_queue (id, kind, version, priority, attempts, next_at, reason)
    VALUES (:id, :kind, :version, :priority, 0, :next_at, :reason)
    ON CONFLICT(id) DO UPDATE SET
        kind = CASE WHEN excluded.kind != '' THEN excluded.kind ELSE zotero_queue.kind END,
        parent = CASE WHEN excluded.version > zotero_queue.version THEN '' ELSE zotero_queue.parent END,
        attempts = CASE WHEN excluded.version > zotero_queue.version THEN 0 ELSE zotero_queue.attempts END,
        next_at = CASE WHEN :next_given THEN excluded.next_at
                       WHEN excluded.version > zotero_queue.version THEN '' ELSE zotero_queue.next_at END,
        reason = CASE WHEN :reason_given THEN excluded.reason
                      WHEN excluded.version > zotero_queue.version THEN '' ELSE zotero_queue.reason END,
        version = MAX(zotero_queue.version, excluded.version),
        priority = MAX(zotero_queue.priority, excluded.priority)"""


def page_entry(page_id: str, *, opened: bool = False, next_at=None, reason: str | None = None) -> dict:
    """The queue entry of a Gamma page to visit (``page:<id>``), before
    any Zotero object; an ``opened`` page before everything. ``next_at``
    (a time) holds it back until then, as a page whose notes wait for
    quiet."""
    return {"id": PAGE_PREFIX + page_id, "kind": "page", "priority": OPENED_PRIORITY if opened else PAGE_PRIORITY,
            "next_at": next_at, "reason": reason}


def file_entry(key: str, version: int = 0, *, opened: bool = False) -> dict:
    """The queue entry of an attachment's file to transfer
    (``file:<key>``), by its version (the newest first), or first of all
    for a page someone ``opened``."""
    return {"id": FILE_PREFIX + key, "kind": "file", "version": int(version),
            "priority": OPENED_PRIORITY if opened else None}


def _kind_of(entry_id: str) -> str:
    return "page" if entry_id.startswith(PAGE_PREFIX) else "file" if entry_id.startswith(FILE_PREFIX) else ""


def enqueue(conn, entries) -> int:
    """Put ``entries`` in the queue: dicts with ``id`` and, optional,
    ``kind`` (``QUEUE_KINDS``; a ``page:`` or ``file:`` id names its own),
    ``version``, ``priority`` (the version unless said; ``PAGE_PRIORITY``
    for a page), ``next_at`` and ``reason``. One already queued keeps the
    higher version and the higher priority; a newer version starts its
    backoff over, and its ``next_at`` and ``reason`` stay unless given.
    How many entries it took."""
    rows = []
    for entry in entries:
        entry_id = str(entry["id"])
        kind = entry.get("kind") or _kind_of(entry_id)
        if kind not in ("", *QUEUE_KINDS):
            raise ValueError(f"not a kind of queue entry: {kind!r}")
        version = int(entry.get("version") or 0)
        priority = entry.get("priority")
        if priority is None:
            priority = PAGE_PRIORITY if kind == "page" else version
        next_at, reason = entry.get("next_at"), entry.get("reason")
        rows.append({"id": entry_id, "kind": kind, "version": version, "priority": int(priority),
                     "next_at": "" if next_at is None else (_stamp(next_at) if next_at else ""),
                     "reason": _clip(reason), "next_given": next_at is not None, "reason_given": reason is not None})
    if rows:
        conn.executemany(_UPSERT, rows)
    return len(rows)


def take(conn, *, now, limit: int | None = None, kinds=None, exclude=()) -> list[Entry]:
    """Up to ``limit`` entries (every one: None) due at ``now`` (no
    ``next_at``, or one not after it), the highest priority first (then the
    newest version: the queue's order index walks it so), of ``kinds`` when
    given, none whose id is in ``exclude`` (what a round already passed
    over). They stay queued until ``done`` or ``drop``."""
    sql = f"SELECT {_ENTRY_COLUMNS} FROM zotero_queue WHERE (next_at = '' OR next_at <= ?)"
    args: list = [_stamp(now)]
    if kinds:
        kinds = (kinds,) if isinstance(kinds, str) else tuple(kinds)
        sql += f" AND kind IN ({','.join('?' * len(kinds))})"
        args += kinds
    if exclude:
        sql += " AND id NOT IN (SELECT value FROM json_each(?))"
        args.append(json.dumps(sorted(exclude)))
    sql += " ORDER BY priority DESC, version DESC, id LIMIT ?"
    return [Entry(*row) for row in conn.execute(sql, [*args, -1 if limit is None else int(limit)]).fetchall()]


def done(conn, ids) -> int:
    """Remove the entries a round applied: ``ids``, or ``{id: version}`` to
    remove each only while it is queued at or below that version (one
    listed again at a newer version stays). How many went."""
    if isinstance(ids, dict):
        rows = [(str(i), int(v or 0)) for i, v in ids.items()]
        return conn.executemany("DELETE FROM zotero_queue WHERE id = ? AND version <= ?", rows).rowcount if rows else 0
    rows = [(str(i),) for i in ids]
    return conn.executemany("DELETE FROM zotero_queue WHERE id = ?", rows).rowcount if rows else 0


def drop(conn, ids) -> int:
    """Remove entries a round will not apply on their own (children a unit
    read with their item, entries given up): as ``done``."""
    return done(conn, ids)


def backoff(conn, entry_id: str, error, *, now, kind: str = "") -> int:
    """Count a failure of ``entry_id`` (queued, or queued now as ``kind``):
    ``attempts`` + 1, kept back until ``now`` + 30 s · 2^(attempts - 1),
    at most 6 h, ``error`` kept as its reason. Its attempts: at
    ``MAX_ATTEMPTS`` the engine gives it up."""
    entry_id = str(entry_id)
    row = conn.execute("SELECT attempts FROM zotero_queue WHERE id = ?", (entry_id,)).fetchone()
    attempts = (row[0] if row else 0) + 1
    delay = min(BACKOFF_S * 2 ** min(attempts - 1, 20), BACKOFF_MAX_S)
    next_at = format_stamp(when(now) + timedelta(seconds=delay))
    if row:
        conn.execute("UPDATE zotero_queue SET attempts = ?, next_at = ?, reason = ? WHERE id = ?",
                     (attempts, next_at, _clip(error), entry_id))
    else:
        kind = kind or _kind_of(entry_id)
        conn.execute("INSERT INTO zotero_queue (id, kind, version, priority, attempts, next_at, reason) "
                     "VALUES (?, ?, 0, ?, ?, ?, ?)",
                     (entry_id, kind, PAGE_PRIORITY if kind == "page" else 0, attempts, next_at, _clip(error)))
    return attempts


def size(conn, *, kinds=None, now=None) -> int:
    """How many entries are queued (of ``kinds``; due at ``now`` when
    given): the status's ``backlog``."""
    sql, args = "SELECT COUNT(*) FROM zotero_queue WHERE 1", []
    if kinds:
        kinds = (kinds,) if isinstance(kinds, str) else tuple(kinds)
        sql += f" AND kind IN ({','.join('?' * len(kinds))})"
        args += kinds
    if now is not None:
        sql += " AND (next_at = '' OR next_at <= ?)"
        args.append(_stamp(now))
    return conn.execute(sql, args).fetchone()[0]


def due(conn, ids, *, now) -> list[str]:
    """Which of the entries ``ids`` are queued and due at ``now``, sorted."""
    ids = sorted(set(ids))
    if not ids:
        return []
    return [row[0] for row in conn.execute(
        "SELECT id FROM zotero_queue WHERE id IN (SELECT value FROM json_each(?)) "
        "AND (next_at = '' OR next_at <= ?) ORDER BY id", (json.dumps(ids), _stamp(now)))]


def wake(conn, ids, *, reason: str) -> int:
    """Make the entries of ``ids`` held back for ``reason`` due now (a page
    changed again whose notes waited to be still). How many."""
    ids = sorted(set(ids))
    if not ids:
        return 0
    return conn.execute("UPDATE zotero_queue SET next_at = '' WHERE reason = ? AND id IN "
                        "(SELECT value FROM json_each(?))", (_clip(reason), json.dumps(ids))).rowcount


def set_parents(conn, read: dict) -> int:
    """Keep the parent of each queued child ``read`` names (``{key: (its
    parentItem, the version read)}``) unless it is queued at a newer
    version since (read again then). How many it kept."""
    rows = [(str(parent), str(key), int(version or 0)) for key, (parent, version) in read.items() if parent]
    if not rows:
        return 0
    return conn.executemany("UPDATE zotero_queue SET parent = ? WHERE id = ? AND kind = 'child' AND version <= ?",
                            rows).rowcount


def unread_children(conn, *, limit: int, exclude=()) -> list[str]:
    """Up to ``limit`` queued children whose parent no round has read yet
    (``set_parents``), none of ``exclude``."""
    return [row[0] for row in conn.execute(
        "SELECT id FROM zotero_queue WHERE kind = 'child' AND parent = '' AND id NOT IN "
        "(SELECT value FROM json_each(?)) ORDER BY id LIMIT ?", (json.dumps(sorted(exclude)), int(limit)))]


def children_of(conn, keys) -> dict[str, tuple[str, int]]:
    """``{key: (parent, version)}`` of the queued children whose parent, as
    read, is one of ``keys``."""
    keys = sorted(set(keys))
    if not keys:
        return {}
    return {key: (parent, version) for key, parent, version in conn.execute(
        "SELECT id, parent, version FROM zotero_queue WHERE kind = 'child' AND parent IN "
        "(SELECT value FROM json_each(?))", (json.dumps(keys),))}


def next_wait(conn, *, now) -> str | None:
    """The soonest ``next_at`` after ``now``: when an entry held back (a
    backoff, a page's notes waiting to be still) is next due; None when
    none is held back."""
    return conn.execute("SELECT MIN(next_at) FROM zotero_queue WHERE next_at > ?", (_stamp(now),)).fetchone()[0]


def when(now) -> datetime:
    """``now`` (a datetime, or a stored time as ``db.page_now`` makes) as
    an aware UTC datetime."""
    if isinstance(now, datetime):
        return now.astimezone(timezone.utc) if now.tzinfo else now.replace(tzinfo=timezone.utc)
    found = parse_stamp(now)
    if found is None:
        raise ValueError(f"not a time: {now!r}")
    return found if found.tzinfo else found.replace(tzinfo=timezone.utc)


def _stamp(now) -> str:
    return format_stamp(when(now).replace(tzinfo=None))


def _clip(text) -> str:
    return "" if text is None else str(text)[:REASON_MAX]


# --- the link's state ------------------------------------------------------------------

def get_state(conn) -> dict:
    """The ``zotero_state`` row as a dict of ``STATE_FIELDS`` (``labels``
    decoded); the defaults when there is none. ``whole_version``: the
    version of the last listing from the start (a first link's, a force's,
    one notes access started over), 0 for none: a top-level item queued at
    a version above 0 and at most this one, with no row, has every child
    queued too, since a unit only takes a child's entry off the queue
    with its item's, and an item a unit leaves with no row (one in
    Zotero's trash) is queued again only at a newer version."""
    row = conn.execute(f"SELECT {', '.join(STATE_FIELDS)} FROM zotero_state WHERE id = 1").fetchone()
    if not row:
        return {"library": "", "version": 0, "local_cursor": "", "labels": {}, "first": "", "restored_at": "",
                "whole_version": 0}
    state = dict(zip(STATE_FIELDS, row))
    try:
        labels = json.loads(state["labels"] or "{}")
    except ValueError:
        labels = {}
    state["labels"] = labels if isinstance(labels, dict) else {}
    return state


def set_state(conn, **fields) -> dict:
    """Write ``fields`` (of ``STATE_FIELDS``; ``labels`` a dict) into the
    state row, making it when there is none; the state after."""
    unknown = set(fields) - set(STATE_FIELDS)
    if unknown:
        raise ValueError(f"not a field of the Zotero link's state: {sorted(unknown)}")
    values = dict(fields)
    if "labels" in values:
        if not isinstance(values["labels"], dict):
            raise ValueError("labels must be a dict")
        values["labels"] = json.dumps(values["labels"], ensure_ascii=False, sort_keys=True)
    for name in ("version", "whole_version"):
        if name in values:
            values[name] = int(values[name])
    names = list(values)
    if names:
        conn.execute(f"INSERT INTO zotero_state (id, {', '.join(names)}) VALUES (1, {', '.join('?' * len(names))}) "
                     f"ON CONFLICT(id) DO UPDATE SET {', '.join(f'{n} = excluded.{n}' for n in names)}",
                     [values[n] for n in names])
    else:
        conn.execute("INSERT INTO zotero_state (id) VALUES (1) ON CONFLICT(id) DO NOTHING")
    return get_state(conn)


def clear(conn) -> None:
    """Empty the link's tables (``zotero_objects``, ``zotero_queue``,
    ``zotero_state``): a link removed, or relinked to another library."""
    for table in ("zotero_objects", "zotero_queue", "zotero_state"):
        conn.execute(f"DELETE FROM {table}")


# --- reading objects -----------------------------------------------------------------

class Fetched(NamedTuple):
    """Objects read by key: ``objects`` ``{key: whole object}`` (``key``,
    ``version``, ``data``, ``meta``) in the order asked, ``absent`` the keys
    Zotero did not answer (removed for good: ``hard_deleted`` tells; or a
    note a key without notes access cannot see)."""
    objects: dict[str, dict]
    absent: list[str]


class Unit(NamedTuple):
    """A top-level item as a round reconciles it: ``item`` the whole
    object (None when Zotero no longer has it), ``children`` every
    descendant that can travel, whole and the trash included: its
    attachments and notes, the annotations of each PDF attachment, the
    pictures of each note that shows one. Each names its parent in
    ``data.parentItem``."""
    item: dict | None
    children: list[dict]


def fetch_items(client: ZoteroClient, library: str, keys) -> Fetched:
    """The items of ``keys``, whole, the trashed ones included, 50 keys a
    read (several in flight)."""
    return _fetch(client, library, "items", keys)


def fetch_collections(client: ZoteroClient, library: str, keys) -> Fetched:
    """The collections of ``keys``, whole, the trashed ones included."""
    return _fetch(client, library, "collections", keys)


def _fetch(client, library, kind, keys) -> Fetched:
    keys = list(dict.fromkeys(keys))
    found = {obj.get("key"): obj for obj in client.objects(library, kind, keys) if isinstance(obj, dict)}
    return Fetched({key: found[key] for key in keys if key in found}, [key for key in keys if key not in found])


def hard_deleted(client: ZoteroClient, library: str, keys, *, since: int = 0, kind: str = "items") -> set[str]:
    """Which of ``keys`` (of ``kind``) Zotero removed for good after
    library version ``since``: an absent key that is not here is one a
    read missed, to try again."""
    deleted, _ = client.deleted(library, since)
    return set(keys) & set(deleted.get(kind) or ())


def holds_children(obj: dict) -> bool:
    """Whether a child read of ``obj`` can find something that travels: a
    regular item's attachments and notes, a PDF attachment's annotations,
    the pictures of a note that shows one."""
    data = obj.get("data") or {}
    item_type = data.get("itemType")
    if item_type == "attachment":
        return is_pdf_attachment(data)
    if item_type == "note":
        return "data-attachment-key" in str(data.get("note") or "")
    return item_type != "annotation"


def descendants(client: ZoteroClient, library: str, obj: dict) -> list[dict]:
    """Every descendant of ``obj`` (a whole object) that can travel, as
    ``Unit.children`` holds them, read child list after child list. One
    that goes while it is read is left out, as a read a moment later
    would; ZoteroNotFound when ``obj`` itself went."""
    out, todo = [], [obj]
    while todo:
        parent = todo.pop(0)
        if not holds_children(parent):
            continue
        try:
            found = client.children(library, parent["key"])
        except ZoteroNotFound:
            if parent is obj:
                raise
            continue
        out.extend(found)
        todo.extend(found)
    return out


def fetch_units(client: ZoteroClient, library: str, keys) -> tuple[dict[str, Unit], list[str]]:
    """The units of the top-level items ``keys``: ``({key: Unit}, absent
    keys)``, the items read 50 a call, then each one's descendants. An item
    removed while it was read is absent."""
    fetched = fetch_items(client, library, keys)
    units, absent = {}, list(fetched.absent)
    for key, item in fetched.objects.items():
        try:
            units[key] = Unit(item, descendants(client, library, item))
        except ZoteroNotFound:
            absent.append(key)
    return units, absent


def fetch_unit(client: ZoteroClient, library: str, key: str) -> Unit:
    """One item's unit; ``Unit(None, [])`` when Zotero no longer has it."""
    units, _ = fetch_units(client, library, [key])
    return units.get(key) or Unit(None, [])


# --- writing objects -----------------------------------------------------------------

class Outcome(NamedTuple):
    """What became of one object a ``Writes`` sent. ``status``:
    ``ok`` (saved; ``data`` Zotero's saved object, whole, ``version`` its
    new version), ``unchanged`` (Zotero held that already), ``conflict``
    (an update Zotero refused because the object changed or went
    meanwhile: decide again), ``exists`` (a create under a key Zotero has:
    ``data`` and ``version`` that object's, when it could be read),
    ``transient`` (not saved this time: Zotero busy or unreachable, a
    parent not saved; send again later), ``rejected`` (Zotero refuses it:
    ``message`` its sentence; send again only once it changed)."""
    key: str
    status: str
    data: dict | None
    version: int
    message: str


@dataclass
class _Write:
    stage: str      # when it is sent (STAGES)
    endpoint: str   # "items" | "collections"
    key: str
    version: int    # what it is sent with: 0 creates it
    body: dict
    put: bool = False


def _needs(body: dict) -> list[str]:
    """The keys an object's body names that must exist in Zotero first."""
    out = [body.get(name) for name in ("parentItem", "parentCollection")]
    out += list(body.get("collections") or ())
    return [key for key in out if isinstance(key, str) and key]


def _status(code, version_sent: int) -> str:
    """The outcome of a refusal with HTTP ``code`` of an object sent at
    ``version_sent``."""
    if code == 412:
        return EXISTS if version_sent == 0 else CONFLICT
    if code == 404 and version_sent > 0:
        return CONFLICT  # it went meanwhile: what Zotero holds is to be read again
    if not code or code in (408, 428, 429) or code >= 500:
        return TRANSIENT
    return REJECTED


def _stops(error: ZoteroError) -> bool:
    """Whether a request's failure stops the rest of a flush: no answer,
    a refused key, a library that is gone, a busy Zotero."""
    return (error.status is None or isinstance(error, (ZoteroAuthError, ZoteroNotFound))
            or error.status in (429, 502, 503, 504))


class Writes:
    """The writes one unit makes to one library, gathered and then sent by
    ``flush`` in ``STAGES`` order: collections (a parent before its
    subcollections), items, attachments, notes, annotations, trash flags;
    50 objects a request, each carrying its own version (0 creates it
    under the key it names), never the library's. One key written twice
    before a flush is one write: the later fields go into the first one,
    which keeps its version and the earlier stage. An object whose parent
    (``parentItem``, ``parentCollection``, a collection it is filed in)
    this flush did not save is not sent. A failure that stops the flush
    (``error``: no answer, a refused key, a gone library, a busy Zotero)
    leaves the rest unsent, each ``transient``, and so does every later
    flush of the same ``Writes``: a round makes a new one."""

    def __init__(self, client: ZoteroClient, library: str):
        self.client, self.library = client, library
        self.outcomes: dict[str, Outcome] = {}  # every flush's, by key
        self.error: ZoteroError | None = None
        self.library_version = 0  # the library's, after the last write it saw
        self._pending: dict[str, _Write] = {}
        self._endpoints: dict[str, str] = {}

    def __len__(self) -> int:
        return len(self._pending)

    def __contains__(self, key) -> bool:
        return key in self._pending

    def create(self, stage: str, key: str, data: dict) -> None:
        """Create ``key`` (its ``data``: an item's ``itemType`` and fields,
        a collection's ``name``)."""
        self._add(stage, key, 0, data)

    def update(self, stage: str, key: str, version: int, data: dict) -> None:
        """Change the fields ``data`` holds of ``key``, which Zotero holds at
        ``version``; a field left out stays as Zotero has it."""
        if int(version) <= 0:
            raise ValueError("an update names the version Zotero holds")
        self._add(stage, key, int(version), data)

    def replace(self, key: str, version: int, data: dict) -> None:
        """Replace the item ``key``'s editable fields with ``data`` (a
        retype, ``zotero_fields.patch_for``'s replace), in the items stage,
        while Zotero still holds it at ``version``."""
        self._add("items", key, int(version), data, put=True)

    def trash(self, stage: str, key: str, version: int) -> None:
        """Move ``key`` (of ``stage``'s kind) to Zotero's trash, in the trash
        stage; never a DELETE."""
        self._add(stage, key, int(version), {"deleted": True}, at="trash")

    def restore(self, stage: str, key: str, version: int) -> None:
        """Bring ``key`` back from Zotero's trash, in its own stage."""
        self._add(stage, key, int(version), {"deleted": False})

    def _add(self, stage, key, version, body, *, put=False, at=None) -> None:
        if stage not in OBJECT_STAGES:
            raise ValueError(f"not a kind of Zotero write: {stage!r}")
        if not isinstance(key, str) or not KEY_RE.match(key):
            raise ValueError(f"not a Zotero object key: {key!r}")
        endpoint = "collections" if stage == "collections" else "items"
        at = at or stage
        found = self._pending.get(key)
        if found is None:
            self._pending[key] = _Write(at, endpoint, key, version, dict(body), put)
            return
        if found.endpoint != endpoint:
            raise ValueError(f"{key} is written as both an item and a collection")
        found.body.update(body)
        found.put = found.put or put
        if STAGES.index(at) < STAGES.index(found.stage):
            found.stage = at

    def flush(self, *, until: str | None = None) -> dict[str, Outcome]:
        """Send what is gathered, the stages up to ``until`` (all when None;
        the later ones stay for the next flush): ``{key: Outcome}`` of what
        this flush sent or held back."""
        last = STAGES.index(until) if until else len(STAGES) - 1
        out: dict[str, Outcome] = {}
        for stage in STAGES[:last + 1]:
            writes = [w for w in self._pending.values() if w.stage == stage]
            for w in writes:
                del self._pending[w.key]
                self._endpoints[w.key] = w.endpoint
            for level in _levels(writes):
                for endpoint in ("collections", "items"):
                    batch = [w for w in level if w.endpoint == endpoint and not w.put]
                    for i in range(0, len(batch), WRITE_LIMIT):
                        self._post(endpoint, batch[i:i + WRITE_LIMIT], out)
                for w in level:
                    if w.put:
                        self._put(w, out)
        self._read_existing(out)
        self.outcomes.update(out)
        return out

    def _held(self, w: _Write, out: dict) -> Outcome | None:
        """The outcome of a write that is not to be sent now, or None."""
        if self.error is not None:
            return Outcome(w.key, TRANSIENT, None, w.version, f"not sent: {self.error}")
        for need in _needs(w.body):
            found = out.get(need) or self.outcomes.get(need)
            if found is not None and found.status not in LANDED:
                return Outcome(w.key, TRANSIENT, None, w.version, f"waits for {need}, which Zotero did not save")
        return None

    def _post(self, endpoint: str, batch: list[_Write], out: dict) -> None:
        ready = []
        for w in batch:
            held = self._held(w, out)
            if held:
                out[w.key] = held
            else:
                ready.append(w)
        if ready:
            self._send(endpoint, ready, out)

    def _send(self, endpoint: str, ready: list[_Write], out: dict) -> None:
        if self.error is not None:
            for w in ready:
                out[w.key] = Outcome(w.key, TRANSIENT, None, w.version, f"not sent: {self.error}")
            return
        bodies = [{**w.body, "key": w.key, "version": w.version} for w in ready]
        try:
            result = self.client.write(self.library, endpoint, bodies)
        except ZoteroError as e:
            if _stops(e):
                self.error = e
                log.info(f"[zotero] writes to {self.library} stopped: {e}")
                for w in ready:
                    out[w.key] = Outcome(w.key, TRANSIENT, None, w.version, str(e))
            elif len(ready) > 1 and e.status in _SPLIT:
                half = len(ready) // 2
                self._send(endpoint, ready[:half], out)
                self._send(endpoint, ready[half:], out)
            else:
                for w in ready:
                    out[w.key] = Outcome(w.key, _status(e.status, w.version), None, w.version, str(e))
            return
        self.library_version = max(self.library_version, result.version)
        for i, w in enumerate(ready):
            if i in result.successful:
                saved = result.successful[i]
                out[w.key] = Outcome(w.key, OK, saved, int(saved.get("version") or result.version), "")
            elif i in result.unchanged:
                out[w.key] = Outcome(w.key, UNCHANGED, None, w.version, "")
            elif i in result.failed:
                failed = result.failed[i]
                out[w.key] = Outcome(w.key, _status(failed["code"], w.version), None, w.version,
                                     failed["message"] or f"Zotero answered {failed['code']}")
            else:
                out[w.key] = Outcome(w.key, TRANSIENT, None, w.version, "Zotero did not say what became of it")

    def _put(self, w: _Write, out: dict) -> None:
        held = self._held(w, out)
        if held:
            out[w.key] = held
            return
        try:
            version = self.client.put_item(self.library, w.key, w.version, w.body)
        except ZoteroNotFound as e:  # the item went, not the library
            out[w.key] = Outcome(w.key, CONFLICT, None, w.version, str(e))
            return
        except ZoteroError as e:
            if _stops(e):
                self.error = e
                out[w.key] = Outcome(w.key, TRANSIENT, None, w.version, str(e))
            else:
                out[w.key] = Outcome(w.key, _status(e.status, w.version), None, w.version, str(e))
            return
        saved = self._read("items", [w.key]).get(w.key)  # a PUT answers no object
        self.library_version = max(self.library_version, version)
        out[w.key] = Outcome(w.key, OK, saved, int((saved or {}).get("version") or version), "")

    def _read(self, endpoint: str, keys) -> dict[str, dict]:
        """``keys`` read whole, {} for what cannot be read now."""
        if self.error is not None or not keys:
            return {}
        try:
            return _fetch(self.client, self.library, endpoint, keys).objects
        except ZoteroError as e:
            log.info(f"[zotero] could not read back {len(keys)} objects of {self.library}: {e}")
            return {}

    def _read_existing(self, out: dict) -> None:
        """Give each ``exists`` outcome the object Zotero holds under its key."""
        for endpoint in ("collections", "items"):
            keys = [key for key, o in out.items() if o.status == EXISTS and self._endpoints.get(key) == endpoint]
            found = self._read(endpoint, keys)
            for key in keys:
                if key in found:
                    out[key] = out[key]._replace(data=found[key], version=int(found[key].get("version") or 0))


def _levels(writes: list[_Write]) -> list[list[_Write]]:
    """``writes`` (one stage's) in rounds: each after the writes of the
    same stage it needs (a subcollection after its parent), in the order
    they came."""
    by_key = {w.key: w for w in writes}
    depth: dict[str, int] = {}

    def level(w: _Write, path: frozenset) -> int:
        if w.key not in depth:
            depth[w.key] = max([level(by_key[need], path | {w.key}) + 1 for need in _needs(w.body)
                                if need in by_key and need not in path and need != w.key] or [0])
        return depth[w.key]

    for w in writes:
        level(w, frozenset())
    return [[w for w in writes if depth[w.key] == n] for n in sorted(set(depth.values()))]


def trash(client: ZoteroClient, library: str, stage: str, entries) -> dict[str, Outcome]:
    """Move ``entries`` (``(key, version)`` pairs of ``stage``'s kind) to
    Zotero's trash."""
    writes = Writes(client, library)
    for key, version in entries:
        writes.trash(stage, key, version)
    return writes.flush()


def restore(client: ZoteroClient, library: str, stage: str, entries) -> dict[str, Outcome]:
    """Bring ``entries`` (``(key, version)`` pairs) back from Zotero's trash."""
    writes = Writes(client, library)
    for key, version in entries:
        writes.restore(stage, key, version)
    return writes.flush()


# --- files ---------------------------------------------------------------------------

class FileUnavailable(Exception):
    """An attachment's file that cannot come now, and why: ``reason`` one
    of ``LINKED``, ``UNAVAILABLE``, ``NO_ACCESS``, ``COMPRESSED``,
    ``QUOTA_HERE``, ``FAILED``: ``linked`` (a file on the computer that linked
    it), ``unavailable`` (Zotero Storage holds none: WebDAV, file syncing
    off, not uploaded yet; or what it holds is no PDF, no picture),
    ``no_access`` (the key may not read files), ``compressed`` (a zipped
    snapshot), ``quota_here`` (this server's storage is full), ``failed``
    (the transfer went wrong: a checksum, a storage refusal). What the link
    shows as waiting, never as an error."""

    def __init__(self, reason: str, message: str = ""):
        super().__init__(message or reason)
        self.reason = reason


def _quota_here(e: HTTPException) -> FileUnavailable:
    return FileUnavailable(QUOTA_HERE, str(e.detail) if e.detail else "this server's storage is full")


def _unavailable(e: BaseException) -> FileUnavailable | None:
    """The ``FileUnavailable`` a transfer's failure ``e`` means; None when it
    is to be raised as it is (the round's trouble: offline, busy, a
    refused key; a stop)."""
    if isinstance(e, HTTPException):
        return _quota_here(e) if e.status_code == 507 else None
    if isinstance(e, ZoteroCompressed):
        return FileUnavailable(COMPRESSED, str(e))
    if isinstance(e, ZoteroNotFound):
        return FileUnavailable(UNAVAILABLE, "Zotero Storage holds no copy of this file (WebDAV, file syncing off, "
                                            "or not uploaded yet)")
    if isinstance(e, ZoteroAuthError):
        return FileUnavailable(NO_ACCESS, str(e)) if str(e) == NO_FILE_ACCESS else None
    if isinstance(e, ZoteroError) and error_kind(e) == "other":
        return FileUnavailable(FAILED, str(e))
    if isinstance(e, _TooBig):
        return FileUnavailable(UNAVAILABLE, str(e))
    return None


def _check_room(ws: str, nbytes: int) -> None:
    try:
        storage.check_upload_allowed(ws, nbytes)
    except HTTPException as e:
        if e.status_code != 507:
            raise
        raise _quota_here(e) from e


def download_pdf(ws: str, client: ZoteroClient, library: str, key: str, *, md5: str, tick=None,
                 link_mode: str = "", size: int = 0) -> str:
    """Download the attachment ``key``'s PDF into the workspace's store; its
    doc id (the stored file is ``storage.pdf_url(doc_id)``, referenced by
    nothing yet). The file is spooled to disk and hashed as it arrives,
    this server's storage limits asked every few MB (and first, for
    ``size`` bytes when the caller knows them), checked against ``md5``
    and for a PDF header, then stored by a rename; nothing is left behind
    on any failure. ``tick(bytes so far)`` follows the transfer and may
    raise to stop it. ``link_mode`` "linked_file" or no ``md5`` is
    FileUnavailable at once. FileUnavailable for what keeps this file
    from coming; any other error (Zotero unreachable or busy, the key
    refused, a stop) as it is."""
    if link_mode == "linked_file":
        raise FileUnavailable(LINKED, "a linked file: it lies on the computer that linked it, not in Zotero Storage")
    if not md5:
        raise FileUnavailable(UNAVAILABLE, "Zotero Storage holds no copy of this file")
    if size:
        _check_room(ws, int(size))
    spool = storage.Spool(ws)

    def arrived(done: int) -> None:
        if spool.due_check():
            storage.check_upload_allowed(ws, spool.size)
        if tick:
            tick(done)

    try:
        client.download(library, key, spool, md5=md5, tick=arrived)
        spool.close()
        if not storage.is_pdf(spool.head):
            raise FileUnavailable(UNAVAILABLE, "Zotero's file for this attachment is not a PDF")
        doc_id, _ = storage.store_pdf_path(ws, spool.path, spool.size, spool.doc_id())
        return doc_id
    except BaseException as e:
        spool.discard()
        unavailable = _unavailable(e)
        if unavailable is None:
            raise
        raise unavailable from e


def upload_pdf(client: ZoteroClient, library: str, key: str, path, *, filename: str, md5: str,
               replace_md5: str = "", tick=None) -> int | None:
    """Store the PDF at ``path`` (whose MD5 is ``md5``) as the imported
    attachment ``key``'s file under ``filename``: into an attachment with
    no file yet, or, with ``replace_md5``, over the file Zotero holds while
    it is still that one. The attachment's version after the register,
    which the caller records so its own upload does not read as Zotero's
    change; None when Zotero had the bytes already. ``Quota`` (413) when
    the library owner's storage is full, ``Conflict`` (412) when the
    attachment's file is not the one assumed. ``tick(bytes sent)``
    follows the transfer."""
    st = os.stat(path)
    return client.upload_file(library, key, path, filename=filename, content_type=PDF_TYPE, md5=md5,
                              mtime_ms=int(st.st_mtime * 1000), size=st.st_size, replace_md5=replace_md5,
                              tick=tick)


class _TooBig(Exception):
    """A picture past ``IMAGE_MAX_BYTES``."""


class _Capped:
    """A download's sink in memory, refusing more than ``limit`` bytes."""

    def __init__(self, limit: int):
        self.limit, self.size, self._buffer = limit, 0, io.BytesIO()

    def write(self, data) -> int:
        self.size += len(data)
        if self.size > self.limit:
            raise _TooBig(f"the picture is larger than {self.limit // 1_000_000} MB")
        return self._buffer.write(data)

    def getvalue(self) -> bytes:
        return self._buffer.getvalue()


def download_image(ws: str, client: ZoteroClient, library: str, key: str, *, md5: str = "") -> str:
    """Download the ``embedded_image`` attachment ``key`` (a picture a note
    shows) into the workspace's store, as it is; its upload name
    (``<sha24>.png`` …, referenced by nothing yet). A PNG, JPEG, GIF or
    WebP picture of at most ``IMAGE_MAX_BYTES``, else FileUnavailable
    (``unavailable``), as for a file Zotero cannot hand out;
    ``quota_here`` when this server's storage is full."""
    from .ai_pictures import sniff  # local: it brings Pillow and the PDF renderer

    sink = _Capped(IMAGE_MAX_BYTES)
    try:
        client.download(library, key, sink, md5=md5)
    except BaseException as e:
        unavailable = _unavailable(e)
        if unavailable is None:
            raise
        raise unavailable from e
    data = sink.getvalue()
    ext = storage.IMAGE_EXTENSIONS.get(sniff(data))
    if not ext:
        raise FileUnavailable(UNAVAILABLE, "Zotero's file for this picture is no picture Gamma shows")
    try:
        name, _ = storage.store_file(ws, data, ext)
    except HTTPException as e:
        if e.status_code != 507:
            raise
        raise _quota_here(e) from e
    return name


# --- errors --------------------------------------------------------------------------

def error_kind(exc: BaseException) -> str:
    """The word a link's status uses for what ``exc`` says went wrong
    (``ERROR_KINDS``): ``offline`` (no answer, a gateway), ``auth`` (the
    key refused), ``not_connected`` (no Zotero account), ``library_gone``
    (Zotero has no such library), ``busy`` (429, 500, 503), ``other`` (the
    rest, a ``FileUnavailable`` among them: that waits, it is no error)."""
    if isinstance(exc, AccountError):
        return exc.kind if exc.kind in ERROR_KINDS else "other"
    if isinstance(exc, ZoteroAuthError):
        return "auth"
    if isinstance(exc, ZoteroNotFound):
        return "library_gone"
    if isinstance(exc, ZoteroUnreachable):
        return "offline"
    if isinstance(exc, ZoteroError):
        status = exc.status
        if status is None:  # a checksum mismatch, an unreadable answer: no answer is to blame
            return "other"
        if status in (502, 504):
            return "offline"
        if status == 429 or status >= 500:
            return "busy"
    return "other"
