"""What this computer knows of its synced vaults, in vaults.db
(``db.connect_vaults_db``; docs/research/vaults.md "Where everything lives"):
each vault's files as its last round left them (``vault_files``), its
directories (``vault_dirs``) and the bytes its writes replaced
(``vault_history``), only where links are kept
(``config.folder_links_enabled``). The rounds (gamma/vault/sync.py) write
them; besides, gamma/folder_links.py reads a page's rows for its chip and its
file editor, keeps the history of a write from that editor, makes the row of
a file vault a new vault takes in, and drops a vault's rows when it goes;
gamma/vault/upgrade.py writes the rows a folder kept on disk before synced
vaults starts from (``take``), gamma/vault/http_remote.py keeps the text a clash on
another server took from Gamma, and gamma/linked_files.py reads whether a PDF
is a vault's.

A file's row is its **base**: what the file was when the round last took it,
its hash and stat and when they were read (``read_at``, for
``disk.racy``), a note's text and its blocks (``nodes``, ``[{id, g,
m?}]`` in document order) and ``gone``, the blocks the file dropped, each kept
``GONE_DAYS`` with its properties so that rectangles come back with the text.
Before a round does anything to a file it writes what it is about to make the
base into ``pending``, ``{token, hash, text, nodes, path?, page_id?}`` (the
journal: ``put_pending``), and after, it ``promote``s it, or ``drop``s it when
the effect did not happen; a round that died leaves the pending for the next to
decide, never a base that says what is not so.

A file whose page went to Recently deleted keeps its row, a **tombstone**
(``bury``): ``path`` is where the trash holds the file and ``was`` where it
lived, so that the same file come back finds its page
(``disk.came_back``) and a restored page finds its file. A work
folder's file its ``.gammaignore`` leaves out stays where it is, and so does
a PDF of a work folder whose page went: its tombstone is **in place**
(``bury_in_place``), ``path`` ``KEPT`` and ``was`` (no file of the trash's,
none to purge), and ``was`` is where the file is (``on_disk``). ``missing_since`` holds a file the scan did not
find, which counts as removed only once ``MISSING_S`` later scans agree
(``missing_long``): a safe save deletes and renames.

History is one entry a path every ``HISTORY_EVERY_S`` at most (``keep``), the
first bytes replaced in that time, compressed, ``KEEP_HISTORY_DAYS`` and
``HISTORY_BYTES`` a vault at most; bytes a write replaced that were not the
base it read (a save in the moment between its check and its rename) are
always kept.

Every function opens its own short connection and commits before it returns.
None may run while the caller holds a workspace's pages.db write lock: a round
reads and writes here between its page transactions, never inside one, so that
a slow disk under vaults.db never holds every writer of a workspace.
"""

import json
import time
import zlib
from datetime import datetime, timedelta, timezone

from ..db import connect_vaults_db, format_stamp

GONE_DAYS = 30                  # how long a block the file dropped can come back with its id
MISSING_S = 10                  # a file missing from scans this far apart is removed
HISTORY_EVERY_S = 300           # one entry a path in this long, at most
HISTORY_BYTES = 32 << 20        # compressed history a vault keeps, at most
KEEP_HISTORY_DAYS = 7
KEPT = "\x00kept/"              # an in-place tombstone's path: this, then where its file still is (``bury_in_place``)

_FILE_COLUMNS = ("link_id", "path", "page_id", "kind", "hash", "size", "mtime_ns", "file_id", "read_at", "text",
                 "nodes", "gone", "title", "folder", "seq", "ours", "was", "missing_since", "problem", "pending")
_JSON = ("nodes", "gone", "pending")
_SELECT = f"SELECT {', '.join(_FILE_COLUMNS)} FROM vault_files"
_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


def _row(values) -> dict:
    row = dict(zip(_FILE_COLUMNS, values))
    for key in _JSON:
        row[key] = json.loads(row[key]) if row[key] is not None else None
    return row


def _stored(key: str, value):
    return json.dumps(value) if key in _JSON and value is not None else value


def files(link_id: str) -> dict:
    """``{path: row}`` of the vault's files where they are: every row but the
    tombstones."""
    with connect_vaults_db() as conn:
        rows = conn.execute(f"{_SELECT} WHERE link_id = ? AND was = ''", (link_id,)).fetchall()
    return {row["path"]: row for row in map(_row, rows)}


def get(link_id: str, path: str) -> dict | None:
    with connect_vaults_db() as conn:
        found = conn.execute(f"{_SELECT} WHERE link_id = ? AND path = ?", (link_id, path)).fetchone()
    return _row(found) if found else None


def put(link_id: str, path: str, **fields) -> None:
    """The row of ``path`` with ``fields`` set, made when there is none (a new
    one needs ``kind``)."""
    keys = list(fields)
    if not keys or not set(keys) <= set(_FILE_COLUMNS[2:]):
        raise ValueError(f"no such fields: {sorted(set(keys) - set(_FILE_COLUMNS[2:]))}")
    values = [_stored(k, fields[k]) for k in keys]
    with connect_vaults_db() as conn:
        if not conn.execute(f"UPDATE vault_files SET {', '.join(f'{k} = ?' for k in keys)} WHERE link_id = ? AND path = ?",
                            (*values, link_id, path)).rowcount:
            conn.execute(f"INSERT INTO vault_files (link_id, path, {', '.join(keys)}) VALUES (?, ?{', ?' * len(keys)})",
                         (link_id, path, *values))


def forget(link_id: str, path: str) -> None:
    """A row no longer kept: its file and its page are both gone for good."""
    with connect_vaults_db() as conn:
        conn.execute("DELETE FROM vault_files WHERE link_id = ? AND path = ?", (link_id, path))


def forget_kind(link_id: str, kind: str) -> None:
    """Every row of the vault's files of class ``kind``, tombstones included:
    a work folder let go forgets its ``file`` rows, whose pages keep their
    documents (gamma/work_folders/takeover.py ``release``)."""
    with connect_vaults_db() as conn:
        conn.execute("DELETE FROM vault_files WHERE link_id = ? AND kind = ?", (link_id, kind))


def forget_link(link_id: str) -> None:
    """Everything kept of a vault that was removed, but for a chat turn
    still open: its end may never be written (Gamma closing right after),
    and the next start fails it then (runner.recover), its conversation's
    message too, without the vault."""
    with connect_vaults_db() as conn:
        for table in ("vault_files", "vault_dirs", "vault_history", "agent_folders", "agent_threads",
                      "agent_conversations", "agent_policies", "agent_grants"):
            conn.execute(f"DELETE FROM {table} WHERE link_id = ?", (link_id,))
        conn.execute("DELETE FROM agent_turns WHERE link_id = ? AND ended_at IS NOT NULL", (link_id,))


def unlevel(link_id: str) -> None:
    """Every file of the vault level with no seq, so that the next pull looks
    at each page again: its notes files turned on."""
    with connect_vaults_db() as conn:
        conn.execute("UPDATE vault_files SET seq = -1 WHERE link_id = ? AND was = ''", (link_id,))


def take(link_id: str, files: dict, dirs: dict) -> None:
    """A vault's rows written whole, in one transaction, in place of any it
    had: ``files`` ``{path: fields}`` (each with its ``kind``), ``dirs``
    ``{path: (folder id, file id)}``. What a folder kept on disk before
    synced vaults starts from (gamma/vault/upgrade.py)."""
    with connect_vaults_db() as conn:
        conn.execute("DELETE FROM vault_files WHERE link_id = ?", (link_id,))
        conn.execute("DELETE FROM vault_dirs WHERE link_id = ?", (link_id,))
        for path, fields in files.items():
            keys = list(fields)
            if not set(keys) <= set(_FILE_COLUMNS[2:]) or "kind" not in fields:
                raise ValueError(f"not a file's row: {sorted(keys)}")
            conn.execute(f"INSERT INTO vault_files (link_id, path, {', '.join(keys)}) VALUES (?, ?{', ?' * len(keys)})",
                         (link_id, path, *(_stored(k, fields[k]) for k in keys)))
        conn.executemany("INSERT INTO vault_dirs (link_id, path, folder_id, file_id) VALUES (?, ?, ?, ?)",
                         [(link_id, path, folder_id, file_id) for path, (folder_id, file_id) in dirs.items()])


# --- the journal --------------------------------------------------------------------

def put_pending(link_id: str, path: str, kind: str, pending: dict) -> None:
    """What a round is about to make the base of ``path``, before it touches
    anything: ``pending`` holds its ``token`` (the op batch's id) and the base
    to be. A new file's row is made with it, and no base; a row that is there
    takes ``kind``, its class now (a kept folder's picture row at a path a
    work folder reads as a file)."""
    if not pending.get("token"):
        raise ValueError("a pending base needs its token")
    with connect_vaults_db() as conn:
        conn.execute("INSERT INTO vault_files (link_id, path, kind, pending) VALUES (?, ?, ?, ?) "
                     "ON CONFLICT (link_id, path) DO UPDATE SET pending = excluded.pending, kind = excluded.kind",
                     (link_id, path, kind, json.dumps(pending)))


def pending(link_id: str) -> list:
    """The rows a round left a pending in: for the next one to promote or
    drop, by what the page and the file say."""
    with connect_vaults_db() as conn:
        rows = conn.execute(f"{_SELECT} WHERE link_id = ? AND pending IS NOT NULL", (link_id,)).fetchall()
    return [_row(r) for r in rows]


def promote(link_id: str, path: str, token: str, **fields) -> dict | None:
    """The pending of ``path`` made its base, when it is still ``token``'s:
    its hash, text, nodes and page, with ``fields`` (the stat, ``read_at``,
    ``seq``...), at its ``path`` when it names another (a file a round renamed,
    its history going along). The row as it is now; None when no pending of
    that token is there."""
    with connect_vaults_db() as conn:
        found = conn.execute(f"{_SELECT} WHERE link_id = ? AND path = ?", (link_id, path)).fetchone()
        row = _row(found) if found else None
        if row is None or not row["pending"] or row["pending"].get("token") != token:
            return None
        base = {k: row["pending"][k] for k in ("hash", "text", "nodes", "page_id") if k in row["pending"]}
        base.update(fields, pending=None)
        at = row["pending"].get("path") or path
        if at != path:
            _repath(conn, link_id, path, at, tree=False)
        conn.execute(f"UPDATE vault_files SET {', '.join(f'{k} = ?' for k in base)} WHERE link_id = ? AND path = ?",
                     (*(_stored(k, v) for k, v in base.items()), link_id, at))
        return _row(conn.execute(f"{_SELECT} WHERE link_id = ? AND path = ?", (link_id, at)).fetchone())


def drop(link_id: str, path: str, token: str) -> None:
    """The pending of ``path`` let go, when it is still ``token``'s: the base
    stays as it was, and a row that had none (a new file never made) goes."""
    with connect_vaults_db() as conn:
        found = conn.execute(f"{_SELECT} WHERE link_id = ? AND path = ?", (link_id, path)).fetchone()
        row = _row(found) if found else None
        if row is None or not row["pending"] or row["pending"].get("token") != token:
            return
        if not row["hash"] and row["text"] is None and not row["page_id"]:
            conn.execute("DELETE FROM vault_files WHERE link_id = ? AND path = ?", (link_id, path))
        else:
            conn.execute("UPDATE vault_files SET pending = NULL WHERE link_id = ? AND path = ?", (link_id, path))


# --- tombstones, missing files, dropped blocks ------------------------------------------------

def tombstones(link_id: str) -> list:
    """The rows of files whose pages are in Recently deleted (``bury``,
    ``bury_in_place``)."""
    with connect_vaults_db() as conn:
        rows = conn.execute(f"{_SELECT} WHERE link_id = ? AND was != ''", (link_id,)).fetchall()
    return [_row(r) for r in rows]


def bury(link_id: str, path: str, trash: str) -> None:
    """The row of ``path`` kept as a tombstone: at ``trash``, where the vault's
    trash holds the file (or would: one removed on disk is not there), ``was``
    where it lived. Written before the file moves."""
    with connect_vaults_db() as conn:
        conn.execute("UPDATE vault_files SET path = ?, was = ?, missing_since = 0 WHERE link_id = ? AND path = ?",
                     (trash, path, link_id, path))


def bury_in_place(link_id: str, path: str) -> None:
    """The row of ``path`` kept as a tombstone whose file stays where it is:
    at ``KEPT`` + ``path``, ``was`` ``path``. No pending goes with it: nothing
    moves, so a round that dies leaves nothing to finish. An older one of
    that path, a page made of the file since, goes."""
    with connect_vaults_db() as conn:
        conn.execute("DELETE FROM vault_files WHERE link_id = ? AND path = ?", (link_id, KEPT + path))
        conn.execute("UPDATE vault_files SET path = ?, was = ?, missing_since = 0 WHERE link_id = ? AND path = ?",
                     (KEPT + path, path, link_id, path))


def in_place(row: dict) -> bool:
    """Whether a tombstone is in place (``bury_in_place``)."""
    return row["path"].startswith(KEPT)


def on_disk(row: dict) -> str:
    """Where a row's file is on the disk: an in-place tombstone's at ``was``."""
    return row["was"] if in_place(row) else row["path"]


def unbury(link_id: str, trash: str, path: str) -> None:
    """A tombstone back as the file at ``path``: its page was restored, or the
    same file came back."""
    with connect_vaults_db() as conn:
        conn.execute("UPDATE vault_files SET path = ?, was = '', missing_since = 0 WHERE link_id = ? AND path = ?",
                     (path, link_id, trash))


def mark_missing(link_id: str, paths, now: float | None = None) -> None:
    """``paths`` not found by a scan: missing since ``now`` unless they were
    already. A note emptied (nothing but blanks, line ends or a byte order
    mark) whose base had text is held the same way."""
    now = time.time() if now is None else now
    with connect_vaults_db() as conn:
        conn.executemany("UPDATE vault_files SET missing_since = ? WHERE link_id = ? AND path = ? AND missing_since = 0",
                         [(now, link_id, path) for path in paths])


def found_again(link_id: str, paths) -> None:
    """``paths`` there again: no longer missing."""
    with connect_vaults_db() as conn:
        conn.executemany("UPDATE vault_files SET missing_since = 0 WHERE link_id = ? AND path = ? AND missing_since != 0",
                         [(link_id, path) for path in paths])


def missing_long(row: dict, now: float | None = None) -> bool:
    """Whether the file of ``row`` is missing from two scans ``MISSING_S``
    apart at least: then, ``lstat``ed once more, it is removed."""
    since = row["missing_since"]
    return bool(since) and (time.time() if now is None else now) - since >= MISSING_S


def kept_gone(gone, now: float | None = None) -> list:
    """The entries of a base's ``gone`` younger than ``GONE_DAYS``."""
    now = time.time() if now is None else now
    return [entry for entry in gone or () if now - float(entry.get("at") or 0) < GONE_DAYS * 86400]


def with_gone(gone, dropped, now: float | None = None) -> list:
    """A base's ``gone`` with the blocks ``dropped`` now, ``{id, hash, anchor,
    props}`` each: stamped ``at``, in place of an older entry of the same id,
    the entries past ``GONE_DAYS`` left out."""
    now = time.time() if now is None else now
    ids = {entry["id"] for entry in dropped}
    return [*(e for e in kept_gone(gone, now) if e["id"] not in ids), *({**e, "at": now} for e in dropped)]


# --- directories ------------------------------------------------------------------------

def dirs(link_id: str) -> dict:
    """``{path: {folder_id, file_id}}`` of the vault's directories."""
    with connect_vaults_db() as conn:
        rows = conn.execute("SELECT path, folder_id, file_id FROM vault_dirs WHERE link_id = ?", (link_id,)).fetchall()
    return {path: {"folder_id": folder_id, "file_id": file_id} for path, folder_id, file_id in rows}


def put_dir(link_id: str, path: str, folder_id: str, file_id: str = "") -> None:
    with connect_vaults_db() as conn:
        conn.execute("INSERT INTO vault_dirs (link_id, path, folder_id, file_id) VALUES (?, ?, ?, ?) ON CONFLICT "
                     "(link_id, path) DO UPDATE SET folder_id = excluded.folder_id, file_id = excluded.file_id",
                     (link_id, path, folder_id, file_id))


def forget_dir(link_id: str, path: str) -> None:
    with connect_vaults_db() as conn:
        conn.execute("DELETE FROM vault_dirs WHERE link_id = ? AND path = ?", (link_id, path))


def repath(link_id: str, old: str, new: str) -> int:
    """A file or a directory moved from ``old`` to ``new`` in one write: every
    row at or below it, files, directories, history and the place a tombstone
    came from, so that a directory renamed is one rewrite and not one per
    file. How many file rows moved."""
    with connect_vaults_db() as conn:
        return _repath(conn, link_id, old, new, tree=True)


def repath_files(link_id: str, moves, token: str) -> None:
    """Files moved, ``[(new, old)]``, in one transaction: names swapped go
    through names no file has, which nothing outside the transaction ever
    sees, so a round that dies leaves every row where it was or where it
    goes. Each moved row holds ``{op: moved, token, from: old}`` until its
    page is filed and named by it (``drop``): a round that dies before that
    leaves the next one the move to finish."""
    with connect_vaults_db() as conn:
        parked = {old: f"\x00moving/{n}" for n, (_new, old) in enumerate(moves)}
        for _new, old in moves:
            _repath(conn, link_id, old, parked[old], tree=True)
        for new, old in moves:
            _repath(conn, link_id, parked[old], new, tree=True)
        conn.executemany("UPDATE vault_files SET pending = ? WHERE link_id = ? AND path = ? AND pending IS NULL",
                         [(json.dumps({"op": "moved", "token": token, "from": old}), link_id, new) for new, old in moves])


def _repath(conn, link_id: str, old: str, new: str, *, tree: bool) -> int:
    """``repath`` in ``conn``'s transaction; ``tree`` False moves ``old``
    alone."""
    below = f"{old}/"
    where = "link_id = ? AND (path = ? OR substr(path, 1, ?) = ?)" if tree else "link_id = ? AND path = ?"
    args = (link_id, old, len(below), below) if tree else (link_id, old)
    moved = conn.execute(f"UPDATE vault_files SET path = ? || substr(path, ?) WHERE {where}",
                         (new, len(old) + 1, *args)).rowcount
    for table in ("vault_dirs", "vault_history") if tree else ("vault_history",):
        conn.execute(f"UPDATE {table} SET path = ? || substr(path, ?) WHERE {where}", (new, len(old) + 1, *args))
    if tree:
        conn.execute("UPDATE vault_files SET was = ? || substr(was, ?) WHERE link_id = ? AND was != '' AND "
                     "(was = ? OR substr(was, 1, ?) = ?)", (new, len(old) + 1, link_id, old, len(below), below))
    return moved


# --- history ----------------------------------------------------------------------------

def _stamp(now: float) -> str:
    """``now`` in the ``page_now`` shape (``datetime.fromtimestamp`` refuses a
    time before 1970 on Windows, which a cutoff can be)."""
    return format_stamp(_EPOCH + timedelta(seconds=now))


def keep(link_id: str, path: str, data: bytes, now: float | None = None, *, always: bool = False) -> bool:
    """The bytes a write replaced at ``path``, kept: unless an entry of that
    path is younger than ``HISTORY_EVERY_S``, which holds what the file was
    before Gamma's writes of those minutes. ``always``: these bytes are not
    one of those writes but a person's save, which nothing else holds. Older
    entries go after ``KEEP_HISTORY_DAYS``, and the oldest while the vault's
    history passes ``HISTORY_BYTES``. Whether these bytes were kept."""
    now = time.time() if now is None else now
    packed = zlib.compress(data, 6)
    if len(packed) > HISTORY_BYTES:
        return False
    with connect_vaults_db() as conn:
        newest = conn.execute("SELECT MAX(at) FROM vault_history WHERE link_id = ? AND path = ?",
                              (link_id, path)).fetchone()[0]
        if newest and newest > _stamp(now - HISTORY_EVERY_S) and not always:
            return False
        conn.execute("INSERT INTO vault_history (link_id, path, at, data) VALUES (?, ?, ?, ?)",
                     (link_id, path, _stamp(now), packed))
        conn.execute("DELETE FROM vault_history WHERE link_id = ? AND at < ?",
                     (link_id, _stamp(now - KEEP_HISTORY_DAYS * 86400)))
        total = conn.execute("SELECT COALESCE(SUM(LENGTH(data)), 0) FROM vault_history WHERE link_id = ?",
                             (link_id,)).fetchone()[0]
        for entry_id, size in conn.execute("SELECT id, LENGTH(data) FROM vault_history WHERE link_id = ? ORDER BY at, id",
                                           (link_id,)).fetchall():
            if total <= HISTORY_BYTES:
                break
            conn.execute("DELETE FROM vault_history WHERE id = ?", (entry_id,))
            total -= size
    return True


def history(link_id: str, path: str) -> list:
    """``[{id, at, data}]`` kept of ``path``, newest first."""
    with connect_vaults_db() as conn:
        rows = conn.execute("SELECT id, at, data FROM vault_history WHERE link_id = ? AND path = ? ORDER BY at DESC, id DESC",
                            (link_id, path)).fetchall()
    return [{"id": entry_id, "at": at, "data": zlib.decompress(data)} for entry_id, at, data in rows]
