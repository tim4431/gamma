"""What every part of a Zotero link's engine shares (gamma/zotero_sync.py,
the public face, and the gamma/zotero_sync_*.py parts its rounds run): the
op-log client and actor of its writes and the words the parts answer the
round with; the link's stored row (users.db ``zotero_links``:
``get_link``, ``patch_status``, through gamma/sync_clock.py's
``LinkTable``, as the mirror's); the round's state (``Round``); the rows of
pages.db ``zotero_objects``; the kinds of conflict rows a link records
(``CONFLICT_KINDS``), those rows and log lines in gamma/sync_engine.py's
tables. It imports neither gamma/zotero_sync.py nor any of its parts. See
docs/dev/zotero_sync.md#data.

Every object the link knows has a row with two bases (gamma/zotero_merge.py;
docs/dev/zotero_sync.md#merging): Zotero changed a field when its value
left ``zbase``, Gamma when its value left ``gbase``. A field Gamma pushes keeps its old ``gbase``
until the write lands; one both sides changed whose Gamma value won but has
not gone yet has no ``gbase`` at all, so whatever Gamma holds of it then
goes, even a change taken back. An item in both trashes keeps Zotero's
changes pending (its ``zbase`` stays) for whichever side brings it back.
"""

import hashlib
import json
from dataclasses import dataclass, field

from . import sync_clock, sync_engine
from . import zotero_remote as zr
from .auth import ZOTERO_ACTOR, ZOTERO_CLIENT
from .db import connect_pages_db, connect_users_db

ENGINE = "zotero"       # the link's engine as gamma/sync_clock.py names it
CLIENT = ZOTERO_CLIENT  # the op-log client of every Gamma write a link makes ("zotero")
ACTOR = ZOTERO_ACTOR    # ...and its actor, shown as "Zotero"

LIVE_POLL_S = 5                     # the cadence "Live": Zotero's stream, and a safety round...
LIVE_SAFETY_S = 30                  # ...this often
DEBOUNCE_S = 3.0                    # a local write → a round once things have been this quiet
WORKERS = 4                         # links whose rounds run at once (the clock's pool)

COUNTERS = ("pages_pulled", "pages_pushed", "pages_deleted", "files_pulled", "files_pushed", "blocks_added",
            "blocks_removed", "blocks_changed", "annotations_pulled", "annotations_pushed", "notes_pulled",
            "notes_pushed")
# What stops a whole round, as the status words it (zotero_remote.error_kind):
# no unit is to blame, so none is backed off for it.
STOPS_ROUND = ("offline", "auth", "not_connected", "library_gone", "busy")

HELD = "held"  # a unit's answer: nothing done now, the entry stays queued and is passed over this round
SETTLED = "settled"  # ...nothing to do (a page the feed lists that is as last synced): done, at no cost of the budget


json_dict = sync_clock.json_dict  # a JSON column as a dict ({} for anything else)


class Detached(Exception):
    """The link was detached or removed while a round ran: the round stops
    where it is (its client's heartbeat raises it during a wait or a
    transfer) and ends as a detached round does."""


def signature(*parts) -> str:
    """A cheap signature of what Gamma held when a base was taken."""
    return hashlib.sha1(json.dumps(parts, ensure_ascii=False).encode("utf-8")).hexdigest()[:16]


def without(detail: dict, *names) -> dict:
    """``detail`` without the keys ``names``."""
    return {k: v for k, v in (detail or {}).items() if k not in names}


# --- the link's row ---------------------------------------------------------------------

_COLS = ("workspace_id, owner, library, library_name, mode, download, upload, auto_tags, send_unrecorded, "
         "poll_s, on_change, status, created_at")


def _row_info(row) -> dict:
    return {"workspace_id": row[0], "owner": row[1], "library": row[2], "library_name": row[3], "mode": row[4],
            "download": row[5], "upload": bool(row[6]), "auto_tags": bool(row[7]), "send_unrecorded": bool(row[8]),
            "poll_s": int(row[9] if row[9] is not None else LIVE_POLL_S), "on_change": bool(row[10]),
            "status": json_dict(row[11]), "created_at": row[12]}


def get_link(ws: str) -> dict | None:
    """The workspace's link, or None."""
    with connect_users_db() as conn:
        row = conn.execute(f"SELECT {_COLS} FROM zotero_links WHERE workspace_id = ?", (ws,)).fetchone()
    return _row_info(row) if row else None


def list_links(owner: str) -> list[dict]:
    """The account's links, oldest first."""
    with connect_users_db() as conn:
        rows = conn.execute(f"SELECT {_COLS} FROM zotero_links WHERE owner = ? ORDER BY created_at",
                            (owner,)).fetchall()
    return [_row_info(r) for r in rows]


# The links' table as the clock reads it: a link whose workspace is no longer a personal one its owner
# owns is not scheduled (the key is the owner's; ``zotero_sync._access`` refuses it too).
LINKS = sync_clock.LinkTable(
    "zotero_links", "zotero",
    where="w.kind = 'personal' AND EXISTS (SELECT 1 FROM workspace_members m WHERE m.workspace_id = "
          "t.workspace_id AND m.user_id = t.owner AND m.role = 'owner')")
save_link = LINKS.save              # write fields of the link's row
patch_status = LINKS.patch_status   # change some keys of its status, under the table's lock
_status_guard = LINKS.guard


def without_quota_wait(status: dict) -> dict:
    """The status with no wait on Zotero's full storage
    (``waiting.quota_until``): Sync now, a settings change, the wait over."""
    waiting = status.get("waiting") or {}
    return {**status, "waiting": without(waiting, "quota_until")} if waiting.get("quota_until") else status


# --- the round's state ------------------------------------------------------------------

@dataclass
class Round:
    ws: str
    link: dict
    started: float                      # monotonic, before anything was read: what settles the dirty mark
    library: str = ""
    client: object = None
    access: dict = field(default_factory=dict)
    username: str = ""
    mode: str = "two-way"               # the round's own: pull when the link or the key only receives
    state: dict = field(default_factory=dict)
    adopt: str = ""                     # a first link's or a force's side (theirs | mine)
    report: dict = field(default_factory=lambda: dict.fromkeys(COUNTERS, 0))
    errors: list = field(default_factory=list)   # (sentence, error kind), the first first
    excluded: set = field(default_factory=set)   # queue entries passed over this round...
    again: set = field(default_factory=set)      # ...those of them decided again soon (a page or Zotero
    #                                              changed under a decision): a round comes for them
    units: int = 0
    detached: bool = False
    quota_until: str = ""               # Zotero's storage is full: no PDF goes before then
    no_downloads: bool = False          # this server's storage is full: no download for the rest of the round
    bytes_done: int = 0                 # bytes of PDFs moved this round, either way...
    bytes_total: int = 0                # ...and those plus the ones still queued, as last counted
    fill_units: int = 0                 # units and bytes the rounds before this one did of the same fill (the
    fill_bytes: int = 0                 # status's ``fill``: rounds that each asked for the next at once)
    decided: dict = field(default_factory=dict)  # differences a first link or a force decided, by what
    read_ahead: dict = field(default_factory=dict)  # Zotero objects read ahead of their units, by key
    children_read: set = field(default_factory=set)  # queued children read for their parents this round...
    children_worth: bool | None = None  # ...and whether reading them pays (zotero_sync_reads._worth_reading)

    @property
    def pushes(self) -> bool:
        return self.mode == "two-way"

    def error(self, e, kind: str = "") -> None:
        self.errors.append((str(e) or type(e).__name__, kind or zr.error_kind(e)))

    def decide_again(self, *ids) -> None:
        """Queue entries passed over for the rest of the round because what
        their unit decided on changed meanwhile: the next round, soon, takes
        them again."""
        self.excluded.update(ids)
        self.again.update(ids)


# --- the rows ---------------------------------------------------------------------------

_ROW_FIELDS = ("key", "kind", "page_id", "block_id", "slot", "parent", "zotero_type", "version", "state", "zbase",
               "gbase", "gsig", "detail", "synced_at")
_JSON_FIELDS = ("zbase", "gbase", "detail")


def read_rows(conn, where: str = "1", args=()) -> dict[str, dict]:
    """``zotero_objects`` rows ``WHERE where`` by key, the JSON columns read."""
    out = {}
    for values in conn.execute(f"SELECT {', '.join(_ROW_FIELDS)} FROM zotero_objects WHERE {where}", args):
        row = dict(zip(_ROW_FIELDS, values))
        for name in _JSON_FIELDS:
            row[name] = json_dict(row[name])
        out[row["key"]] = row
    return out


def new_row(key: str, kind: str, **fields) -> dict:
    row = {"key": key, "kind": kind, "page_id": "", "block_id": "", "slot": "", "parent": "", "zotero_type": "",
           "version": 0, "state": "live", "zbase": {}, "gbase": {}, "gsig": "", "detail": {}, "synced_at": ""}
    row.update(fields)
    return row


def write_rows(conn, rows) -> None:
    for row in rows:
        values = [json.dumps(row[name], ensure_ascii=False, sort_keys=True) if name in _JSON_FIELDS else row[name]
                  for name in _ROW_FIELDS]
        conn.execute(f"INSERT OR REPLACE INTO zotero_objects ({', '.join(_ROW_FIELDS)}) "
                     f"VALUES ({', '.join('?' * len(_ROW_FIELDS))})", values)


def drop_rows(conn, keys) -> None:
    conn.executemany("DELETE FROM zotero_objects WHERE key = ?", [(k,) for k in keys])


# --- conflicts and log lines ------------------------------------------------------------

# The conflict rows a link records (sync_conflicts.kind; see
# docs/dev/zotero_sync.md#conflicts-and-the-log); the frontend's conflict
# cards read this literal tuple.
CONFLICT_KINDS = ("zotero_field", "zotero_note", "zotero_annotation", "zotero_file", "zotero_deleted_edited",
                  "zotero_locked", "zotero_lossy", "zotero_rejected")
ONE_OPEN = ("zotero_lossy", "zotero_locked", "zotero_rejected")  # kinds Gamma never sends: one open row per block
# a resolver's refusal of "theirs": Zotero changed the value again since the conflict was written
MOVED_ON = "Zotero changed this again since: its newer value is here now"
# what a receive-only round did with a difference both sides made whose Gamma side won: kept here, not sent (the
# end of its row's ``result``, which the frontend's conflict card reads: conflictKinds.js ``keptHere``)
KEPT_HERE = "Gamma's is kept here; it goes to Zotero once the link sends changes again"


def field_base(theirs: dict, synced: dict) -> dict:
    """A ``zotero_field`` row's ``base``: Zotero's values (``theirs``) and,
    per field, Zotero's side as the link's row held it once the round was
    done (``synced``, a flat projection), so "theirs" can tell when Zotero
    moved on since."""
    return {"theirs": theirs, "zbase": {name: synced.get(name) for name in theirs}}


def record_conflict(ws: str, page_id: str, block_id: str, kind: str, mine="", theirs="", result="", base="", *,
                    resolved: bool = False) -> int:
    """One conflict row, after the commits it is about (never inside a
    unit's transaction), of one of ``CONFLICT_KINDS``. A kind Gamma never
    sends (``ONE_OPEN``) keeps one open row per block, refreshed in place,
    so each burst of edits adds none. ``resolved``: a difference an
    explicit adopt or a force decided, kept for the record. The row's id."""
    if kind not in CONFLICT_KINDS:
        raise ValueError(f"not a Zotero conflict: {kind!r}")
    with connect_pages_db(ws) as conn:
        if kind in ONE_OPEN and not resolved:
            found = conn.execute("SELECT id FROM sync_conflicts WHERE resolved = 0 AND block_id = ? AND kind = ?",
                                 (block_id, kind)).fetchone()
            if found:
                conn.execute("UPDATE sync_conflicts SET page_id = ?, mine = ?, theirs = ?, result = ?, base = ? "
                             "WHERE id = ?", (page_id, mine or "", theirs or "", result or "", base or "", found[0]))
                conn.commit()
                return found[0]
        return sync_engine._conflict(conn, page_id, block_id, kind, mine, theirs, result, base, resolved=resolved)


# A page's log line in the mirror's words, with what it counts as: a page made or brought back adds one,
# a page deleted removes one, anything else changes one (sync_engine._note's stats).
_ACTION_STATS = {"created here": (1, 0, 0), "created there": (1, 0, 0), "restored here": (1, 0, 0),
                 "restored there": (1, 0, 0), "deleted here": (0, 1, 0), "deleted there": (0, 1, 0)}


def page_note(r: Round, page_id: str, action: str) -> None:
    """A page's line in the link's log (``_ACTION_STATS``), counted in the round's report."""
    add, removed, changed = _ACTION_STATS.get(action, (0, 0, 1))
    sync_engine._note(r.ws, page_id, action, stats={"add": add, "del": removed, "mod": changed}, report=r.report)
