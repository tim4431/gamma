"""One workspace's backup: the ``gamma-backup-1`` zip, and the snapshots of
it the server keeps.

The zip is what ``GET /api/export`` downloads and ``POST /api/import-data``
restores: consistent copies of ``pages.db`` and ``data.db`` (taken with the
SQLite backup API, so safe while the app serves), every file under
``uploads/`` when asked, and a ``manifest.json``. ``write_zip`` writes one,
``restore_zip`` applies one (replace or merge) — the two halves of every
backup path in the app, so a stored snapshot, a downloaded export and a
page export all restore the same way. Every database copy is quick-checked
on the way in and out (``gamma/integrity.py``): a snapshot records the
result in its manifest, a restore refuses a damaged backup before touching
anything.

Stored snapshots (``backups/workspaces/<ws>/<time>-<label>.zip``) are what
Settings → Backups manages: taken by a workspace owner (or for every
workspace of an account at once), listed, downloaded, restored in place,
deleted. Each is a FULL copy — no incremental chain, so any one of them
restores on its own and deleting one never breaks another; the price is
size, bounded by ``MAX_PER_WORKSPACE`` manual snapshots, the databases-only
choice and ``MIN_FREE_BYTES`` of free disk. Automatic snapshots have
separate retention in ``backup_schedule.py`` (and at most
``MAX_SCHEDULED_PER_WORKSPACE``); a replace restore first keeps what it
replaces as a ``pre-restore`` snapshot (the newest ``PRE_RESTORE_KEEP``
stay). Snapshots are not metered against anyone's quota (they live outside
``uploads/``) and are not part of admin server snapshots
(``gamma/backups.py`` copies the databases and uploads, not ``backups/``);
deleting a workspace deletes its snapshots but first writes one final copy
to ``backups/deleted/`` (``keep_final_copy``).
"""

import json
import os
import re
import secrets
import shutil
import sqlite3
import tempfile
import threading
import time
import zipfile
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

from . import config, integrity, upload_gc
from .backups import snapshot_db
from .blocks_store import (BLOCK_COLUMNS, TRASH, delete_subtree, fetch_subtree, last_child_position, trashed_ids,
                           write_lock)
from .db import PAGES_SCHEMA, connect_data_db, connect_pages_db, page_now, safe_ws_id, ws_dir
from .logbuf import log
from .normalize import normalize_data_db, normalize_pages_db
from .seed import create_workspace_files

FORMAT = "gamma-backup-1"
MAX_PER_WORKSPACE = 20             # manual snapshots per workspace
MAX_SCHEDULED_PER_WORKSPACE = 100  # scheduled ones per workspace, every task together
PRE_RESTORE_KEEP = 3               # "pre-restore" snapshots kept per workspace
MIN_FREE_BYTES = 1 << 30           # no new snapshot while the disk has less free than this
DELETED_KEEP_DAYS = 90             # final copies of deleted workspaces (backups/deleted/)
NAME_RE = re.compile(r"^\d{8}-\d{6}-[A-Za-z0-9_.-]{1,40}$")
LABEL_RE = re.compile(r"^[A-Za-z0-9_.-]{1,40}$")


class BackupError(ValueError):
    """A bad or damaged zip, a bad name, a full store or disk — the API
    maps it to 400."""


# --- the zip ----------------------------------------------------------------------

def _size(path: Path) -> int:
    try:
        return path.stat().st_size
    except OSError:
        return 0


def write_zip(ws: str, dest: Path, *, uploads: bool = True, by: str = "", label: str = "",
              progress: dict | None = None, scheduled: bool = False, task_id: str = "",
              auto: bool = False) -> dict:
    """Write the workspace's backup zip to ``dest``. The databases are
    copied first and the uploads listed only after them, so every file the
    copied pages reference is on disk when the list is taken; a file that
    goes in between (an orphan sweep) is recorded in ``missing_uploads``,
    never a failed backup. Each database copy is quick-checked
    (``integrity`` in the manifest, and the workspace's latest check).
    ``progress`` (a dict the caller shares with a poller) gets ``total`` /
    ``done`` byte counts. Returns the manifest."""
    from . import workspaces  # local: workspaces imports seed, which imports db

    root = ws_dir(ws)
    uploads_dir = root / "uploads"
    db_files = [root / n for n in ("pages.db", "data.db") if (root / n).exists()]
    if progress is not None:
        estimate = sum(_size(f) for f in uploads_dir.iterdir() if f.is_file()) if uploads and uploads_dir.is_dir() else 0
        progress.update(total=sum(_size(f) for f in db_files) + estimate, done=0)
    info = workspaces.get(ws) or {}
    checks, stored, missing = {}, [], []
    with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as z:
        for src in db_files:
            snap = Path(str(dest) + "." + src.name)
            try:
                checks[src.name] = snapshot_db(src, snap)
                z.write(snap, src.name)
            finally:
                for side in (snap, Path(str(snap) + "-wal"), Path(str(snap) + "-shm")):
                    side.unlink(missing_ok=True)
            if progress is not None:
                progress["done"] += _size(src)
        if uploads and uploads_dir.is_dir():
            listed = sorted(f for f in uploads_dir.iterdir() if f.is_file())
            if progress is not None:
                progress["total"] = progress["done"] + sum(_size(f) for f in listed)
            for f in listed:
                try:
                    z.write(f, f"uploads/{f.name}")
                except FileNotFoundError:
                    missing.append(f.name)  # swept since the listing: no copied page names it
                    continue
                stored.append(f.name)
                if progress is not None:
                    progress["done"] += _size(f)
        manifest = {
            "format": FORMAT,
            "workspace": ws,
            "workspace_name": info.get("name", ""),
            "kind": info.get("kind", ""),
            "user": workspaces.personal_owner(ws) or next(  # whose it is
                (m["username"] for m in workspaces.members(ws) if m["role"] == "owner"), ""),
            "exported_by": by,
            "exported_at": page_now(),
            "label": label,
            "scheduled": scheduled,
            "task_id": task_id,
            "auto": auto,
            "uploads": bool(uploads),
            "upload_files": len(stored),
            "missing_uploads": missing,
            "integrity": checks,
        }
        z.writestr("manifest.json", json.dumps(manifest, indent=2))
    try:
        base = root.relative_to(config.DATA_DIR).as_posix()
    except ValueError:
        base = f"workspaces/{safe_ws_id(ws)}"
    integrity.record({f"{base}/{name}": result for name, result in checks.items()}, "workspace backup")
    return manifest


def read_manifest(path: Path) -> dict:
    """The manifest of a backup zip, or {} when it has none."""
    try:
        with zipfile.ZipFile(path) as z:
            return json.loads(z.read("manifest.json"))
    except (OSError, KeyError, ValueError, zipfile.BadZipFile):
        return {}


# --- restore -------------------------------------------------------------------------

def restore_zip(ws: str, zpath: Path, mode: str = "replace", *, selected: set[str] | None = None,
                by: str = "") -> dict:
    """Apply a backup zip to the workspace.

    Everything is checked before any live data is touched: the zip's shape
    and ``PRAGMA quick_check`` of its databases (a damaged backup is refused
    whole). ``replace``: the unpacked copies are brought to the current
    shapes first (normalized: a backup can be older than any step), what
    the workspace holds now is kept as an automatic ``pre-restore`` snapshot
    (uploads included; not for a guest's workspace — refused when that
    snapshot cannot be taken), then pages.db and data.db are swapped in with
    the sqlite backup API (transactional, safe while the app serves).
    ``merge``: additive — pages (and chats) the workspace does not have are
    appended, everything it has stays (``_merge``). In both modes the
    backup's uploads the workspace lacks are copied in first (content-hash
    names: identical files never conflict, nothing existing is
    overwritten); after a replace, files only the old pages used are left
    to the orphan cleanup.

    Every page the restore wrote moves forward in its op log (a ``reload``
    above any seq a client has seen, so seqs never go back) and is stamped
    now for the change feed; pages a replace removed get tombstones; the
    open pages are told to reload. ``by`` is the actor in the log and on
    the snapshot. Raises BackupError."""
    if mode not in ("replace", "merge"):
        raise BackupError("mode must be 'replace' or 'merge'")
    if selected is not None and mode != "merge":
        raise BackupError("selection is only supported for additive imports")
    with tempfile.TemporaryDirectory(prefix="gamma-restore-") as td:
        tdir = Path(td)
        upload_names = _unpack(zpath, tdir)
        _validate(tdir)
        root = ws_dir(ws)
        review = None
        if selected is not None:
            review = _review_import(root, tdir, upload_names)
            from .import_review import validate_selection
            validate_selection(selected, (p["selection_ids"][0] for p in review))
            chosen = [p for p in review if p["selection_ids"][0] in selected]
            keep_blocks = {bid for p in chosen for bid in p["_blocks"]}
            keep_chats = {bid for p in chosen for bid in p["_chats"]}
            keep_uploads = {name for p in chosen for name in p["_uploads"]}
            omitted = {bid for p in review for bid in p["_blocks"]} - keep_blocks
            with closing(sqlite3.connect(str(root / "pages.db"))) as live:
                omitted -= {r[0] for r in live.execute("SELECT id FROM unified_blocks")}
            for page in chosen:
                unresolved = page.get("_references", set()) & omitted
                if unresolved:
                    page["warnings"].append({"title": page["title"], "selection_id": page["selection_ids"][0],
                                             "reason": f"Links to {len(unresolved)} unselected pages or notes are kept, but their targets are not imported."})
            for dbname, table, column, keep in (("pages.db", "unified_blocks", "id", keep_blocks),
                                               ("data.db", "chats", "block_id", keep_chats)):
                snap = tdir / dbname
                if not snap.exists():
                    continue
                with closing(sqlite3.connect(str(snap))) as conn, conn:
                    if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone():
                        continue
                    conn.execute("CREATE TEMP TABLE import_keep (id TEXT PRIMARY KEY)")
                    conn.executemany("INSERT INTO import_keep VALUES (?)", ((i,) for i in keep))
                    conn.execute(f"DELETE FROM {table} WHERE {column} NOT IN (SELECT id FROM import_keep)")
            upload_names = [n for n in upload_names if n in keep_uploads]
        if not (root / "pages.db").exists():
            create_workspace_files(ws)
        if mode == "merge":
            uploads_added = _copy_uploads(root, tdir, upload_names)
            result = _merge(ws, tdir, by)
        else:
            _normalize_copies(tdir)
            pre = _keep_current(ws, by)
            uploads_added = _copy_uploads(root, tdir, upload_names)
            result = {**_replace(ws, root, tdir, by), "pre_restore": pre["name"] if pre else ""}
    if review is not None:
        result["pages"] = [{k: v for k, v in p.items() if not k.startswith("_")} for p in chosen]
        result["warnings"] = [w for p in chosen for w in p["warnings"]]
    return {"mode": mode, "workspace": ws, **result,
            "uploads_in_backup": len(upload_names), "uploads_added": uploads_added}


def _copy_uploads(root: Path, tdir: Path, upload_names: list[str]) -> int:
    """The backup's files the workspace lacks, each written whole
    (``storage.write_atomic``): a restore cut short never leaves a
    truncated file under a content-hash name."""
    from .storage import write_atomic

    dest_uploads = root / "uploads"
    dest_uploads.mkdir(parents=True, exist_ok=True)
    added = 0
    for base in upload_names:
        target_file = dest_uploads / base
        if not target_file.exists():
            write_atomic(target_file, (tdir / "uploads" / base).read_bytes())
            added += 1
    return added


def _normalize_copies(tdir: Path) -> None:
    """Bring the unpacked databases to the current shapes before anything
    live is touched (the schema statements too: an old backup lacks the op
    log and the tombstones)."""
    with closing(sqlite3.connect(str(tdir / "pages.db"))) as conn:
        for stmt in PAGES_SCHEMA:
            conn.execute(stmt)
        normalize_pages_db(conn)
        upload_gc.restart_clocks(conn)  # its orphan rows' 30 days start now, not when it was taken
    if (tdir / "data.db").exists():
        with closing(sqlite3.connect(str(tdir / "data.db"))) as conn:
            normalize_data_db(conn)


def _keep_current(ws: str, by: str) -> dict | None:
    """Before a replace: the workspace as it is now, as an automatic
    ``pre-restore`` snapshot with its uploads (the newest PRE_RESTORE_KEEP
    stay). None for a guest's workspace, which keeps no snapshots."""
    from . import workspaces

    if workspaces.is_guest_workspace(ws):
        return None
    try:
        kept = create(ws, label="pre-restore", uploads=True, by=by, auto=True)
    except (OSError, sqlite3.Error, BackupError) as e:
        raise BackupError(f"nothing was restored: the workspace's current state could not be "
                          f"saved first ({e})") from e
    for old in [b for b in list_backups(ws) if b["auto"]][PRE_RESTORE_KEEP:]:
        delete(ws, old["name"])
    return kept


def _replace(ws: str, root: Path, tdir: Path, by: str) -> dict:
    """Swap the (normalized) copies in. The op log, the tombstones and the
    page stamps are written into the copy first, so they arrive with it in
    one step: each restored page gets a ``reload`` above the highest seq
    either side had (a client never sees a seq go back), its root is
    stamped now, and pages the restore removes get a tombstone."""
    from . import block_index, ops

    with connect_pages_db(ws) as live:
        live_seqs = dict(live.execute("SELECT page_id, MAX(seq) FROM page_ops GROUP BY page_id").fetchall())
        live_pages = {r[0] for r in live.execute("SELECT id FROM unified_blocks WHERE parent_id = 'root'")}
        live_ids = {r[0] for r in live.execute("SELECT id FROM unified_blocks")}
    with closing(sqlite3.connect(str(tdir / "pages.db"))) as conn:
        pages = [r[0] for r in conn.execute("SELECT id FROM unified_blocks WHERE parent_id = 'root'")]
        for page in pages:
            ops.log_reload(conn, page, by, after=live_seqs.get(page, 0))
        now = page_now()
        removed = sorted(live_pages - set(pages))
        conn.executemany("INSERT OR REPLACE INTO deleted_pages (page_id, deleted_at, actor) VALUES (?, ?, ?)",
                         [(p, now, by) for p in removed])
        conn.executemany("DELETE FROM deleted_pages WHERE page_id = ?", [(p,) for p in pages])
        restored_ids = {r[0] for r in conn.execute("SELECT id FROM unified_blocks")}
        conn.commit()
    restored = []
    for dbname in ("pages.db", "data.db"):
        snap = tdir / dbname
        if not snap.exists():
            continue
        with closing(sqlite3.connect(str(snap))) as src_conn, \
                closing(sqlite3.connect(str(root / dbname), timeout=10)) as dst_conn:
            src_conn.backup(dst_conn)
        restored.append(dbname)
    gone = sorted(live_ids - restored_ids)
    if gone:  # chats and index rows of what went (data.db may not have come with the backup)
        with connect_pages_db(ws) as conn:
            block_index.purge_page_data(ws, conn, gone)
    _announce(ws)
    return {"restored": restored, "pages_removed": len(removed)}


def _announce(ws: str, pages: list[str] | None = None) -> None:
    """After a restore wrote pages behind the op log: the open pages refetch
    their tree (every room of the workspace, or the rooms of ``pages``), and
    the commit listeners hear of it (a clone's sync on change)."""
    from . import collab, ops

    if pages is None:
        collab.publish_all(ws, {"t": "reload", "seq": None})
    else:
        for page in pages:
            collab.publish_reload(ws, page)
    ops._notify(ws)


def _review_import(root, tdir, upload_names):
    """Plan the same additive merge, using only the extracted snapshot."""
    from .foldertags import parse_tags
    from .sync_tree import upload_refs

    available = set(upload_names) | {p.name for p in (root / "uploads").glob("*")}
    with closing(sqlite3.connect(str(root / "pages.db"))) as live:
        trashed = trashed_ids(live)  # a page only in Recently deleted comes back (``_merge``)
        live_pages = {r[0]: (r[1], json.loads(r[2] or "{}"))
                      for r in live.execute("SELECT id, content, properties FROM unified_blocks") if r[0] not in trashed}
        live_docs = {json.loads(r[1] or "{}").get("doc_id"): r[0]
                     for r in live.execute("SELECT id, properties FROM unified_blocks WHERE parent_id='root'")}
    chats = {}
    live_chats = set()
    if (root / "data.db").exists():
        with closing(sqlite3.connect(str(root / "data.db"))) as conn:
            if conn.execute("SELECT 1 FROM sqlite_master WHERE name='chats'").fetchone():
                live_chats = {r[0] for r in conn.execute("SELECT block_id FROM chats")}
    if (tdir / "data.db").exists():
        with closing(sqlite3.connect(str(tdir / "data.db"))) as conn:
            if conn.execute("SELECT 1 FROM sqlite_master WHERE name='chats'").fetchone():
                chats = {row[0]: row[1] for row in conn.execute("SELECT block_id, messages FROM chats")}
    pages, claimed_chats = [], set()
    with closing(sqlite3.connect(str(tdir / "pages.db"))) as src, src:
        normalize_pages_db(src)
        for row in src.execute(f"SELECT {BLOCK_COLUMNS} FROM unified_blocks WHERE parent_id='root' ORDER BY position").fetchall():
            props = json.loads(row[4] or "{}")
            destination_id = row[0] if row[0] in live_pages else live_docs.get(props.get("doc_id")) if props.get("doc_id") else None
            title, destination_props = live_pages[destination_id] if destination_id else (row[3], props)
            blocks = fetch_subtree(src, row[0])
            ids = {b[0] for b in blocks}
            references = {ref for b in blocks for ref in re.findall(r"\[\[([A-Za-z0-9_-]+)\]\]", b[3] or "")}
            page_chats = ids & chats.keys()
            claimed_chats.update(page_chats)
            uploads = upload_refs([{"content": b[3], "props": json.loads(b[4] or "{}")} for b in blocks]
                                  + [{"content": chats[c]} for c in page_chats])
            missing = uploads - available
            selection_id = f"page:{row[0]}"
            warnings = [{"title": row[3], "reason": f"Missing attachment: {name}", "selection_id": selection_id}
                        for name in sorted(missing)]
            pages.append({"id": destination_id or row[0], "title": title, "folders": parse_tags(destination_props.get("folder")),
                          "selection_ids": [selection_id], "kind": "pdf" if destination_props.get("doc_id") else "page",
                          "action": "skip" if destination_id else "create",
                          "source_paths": ["pages.db", *[f"uploads/{n}" for n in sorted(uploads)]],
                          "warnings": warnings, "missing": bool(missing),
                          "_blocks": ids, "_chats": page_chats, "_uploads": uploads, "_references": references})
    for chat_id, messages in chats.items():
        if chat_id in claimed_chats:
            continue
        uploads = upload_refs([{"content": messages}])
        selection_id = f"chat:{chat_id}"
        warnings = [{"title": "Library chat", "reason": f"Missing attachment: {name}", "selection_id": selection_id}
                    for name in sorted(uploads - available)]
        pages.append({"id": chat_id, "title": "Library chat", "folders": ["Chats"], "kind": "chat",
                      "action": "skip" if chat_id in live_chats else "create", "selection_ids": [selection_id], "source_paths": ["data.db"],
                      "warnings": warnings, "missing": bool(uploads - available),
                      "_blocks": set(), "_chats": {chat_id}, "_uploads": uploads})
    return pages


def preview_zip(ws: str, zpath: Path) -> dict:
    from .import_review import archive_entries
    with tempfile.TemporaryDirectory(prefix="gamma-preview-") as td:
        tdir = Path(td)
        uploads = _unpack(zpath, tdir)
        _validate(tdir)
        pages = _review_import(ws_dir(ws), tdir, uploads)
    with zipfile.ZipFile(zpath) as zf:
        entries = archive_entries(zf)
    return {"pages": [{k: v for k, v in p.items() if not k.startswith("_")} for p in pages],
            "entries": entries, "warnings": [w for p in pages for w in p["warnings"]], "folder": ""}


def _unpack(zpath: Path, tdir: Path) -> list[str]:
    try:
        zf = zipfile.ZipFile(zpath)
    except zipfile.BadZipFile:
        raise BackupError("not a zip file")
    with zf:
        names = set(zf.namelist())
        if "manifest.json" in names:
            try:
                fmt = json.loads(zf.read("manifest.json")).get("format")
            except Exception:
                fmt = None
            if fmt != FORMAT:
                raise BackupError("unsupported backup format")
        if "pages.db" not in names:
            raise BackupError("not a Gamma backup (no pages.db in the zip)")
        for dbname in ("pages.db", "data.db"):
            if dbname in names:
                with zf.open(dbname) as src, open(tdir / dbname, "wb") as out:
                    shutil.copyfileobj(src, out)
        (tdir / "uploads").mkdir()
        upload_names = []
        for n in sorted(names):
            base = os.path.basename(n)
            # Accept flat uploads/<file> entries only — the exporter never
            # writes nested paths or dotfiles (also a zip-slip guard).
            if n != f"uploads/{base}" or not base or base.startswith("."):
                continue
            with zf.open(n) as src, open(tdir / "uploads" / base, "wb") as out:
                shutil.copyfileobj(src, out)
            upload_names.append(base)
    return upload_names


def _validate(tdir: Path) -> None:
    # data.db needs no table check: every access path applies DATA_SCHEMA
    # (IF NOT EXISTS) on connect.
    for dbname, required_table in (("pages.db", "unified_blocks"), ("data.db", None)):
        snap = tdir / dbname
        if not snap.exists():
            continue
        try:
            with closing(sqlite3.connect(str(snap))) as conn:
                tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        except sqlite3.DatabaseError:
            raise BackupError(f"{dbname} in the zip is not a valid SQLite database")
        if required_table and required_table not in tables:
            raise BackupError(f"{dbname} in the zip has no {required_table} table")
        check = integrity.quick_check(snap)
        if check != "ok":
            raise BackupError(f"{dbname} in the zip is damaged ({check[:200]}); nothing was restored")


def _fresh_ids(rows, taken: set) -> list[list]:
    """A page's rows ready to insert (the root first, every parent before
    its children, as ``fetch_subtree`` returns them). A block whose id the
    workspace already uses — it lives on another page now — gets a new id
    and its children follow it there, so nothing is grafted under a live
    block; a row whose parent is not part of the copy is left out."""
    remap, inside, out = {}, set(), []
    for i, row in enumerate(rows):
        vals = list(row)
        vals[1] = remap.get(vals[1], vals[1])
        if i and vals[1] not in inside:
            continue
        if vals[0] in taken:
            fresh = secrets.token_urlsafe(9)
            while fresh in taken:
                fresh = secrets.token_urlsafe(9)
            remap[vals[0]] = fresh
            vals[0] = fresh
        taken.add(vals[0])
        inside.add(vals[0])
        out.append(vals)
    return out


def _merge(ws: str, tdir: Path, by: str) -> dict:
    """Additive import: pages from the backup that don't exist locally (by
    block id, or by doc_id for PDF pages) are appended to the library; pages
    that do exist are left untouched (live data always wins). A page that is
    only in Recently deleted counts as absent: its trashed copy's rows go and
    the backup's version comes back under the same ids (its chats, op log and
    the page's reading state, all keyed by the page id, stay). A new page
    comes in whole: a block of it whose id is taken gets a fresh one
    (``_fresh_ids``). Each added page loses any tombstone, is stamped now
    and gets a ``reload`` at the top of its op log, so the change feed, a
    clone and an old tab all see it. Chats merge the same way; nothing else
    in the backup's data.db is touched."""
    from fractional_indexing import generate_n_keys_between

    from . import ops

    pages_skipped = chats_added = 0
    added, untrashed = [], []
    snap = tdir / "pages.db"
    if snap.exists():
        with closing(sqlite3.connect(str(snap))) as src, connect_pages_db(ws) as dst:
            normalize_pages_db(src)
            backup_seqs = {}
            if src.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'page_ops'").fetchone():
                backup_seqs = dict(src.execute("SELECT page_id, MAX(seq) FROM page_ops GROUP BY page_id").fetchall())
            write_lock(dst)
            try:
                taken = {r[0] for r in dst.execute("SELECT id FROM unified_blocks")}
                in_trash = {r[0] for r in dst.execute("SELECT id FROM unified_blocks WHERE parent_id = ?", (TRASH,))}
                live_docs = {r[0] for r in dst.execute(
                    "SELECT json_extract(properties, '$.doc_id') FROM unified_blocks "
                    "WHERE parent_id = 'root' AND json_extract(properties, '$.doc_id') IS NOT NULL")}
                new_roots = []
                for row in src.execute(
                        f"SELECT {BLOCK_COLUMNS} FROM unified_blocks WHERE parent_id = 'root' "
                        "ORDER BY position ASC").fetchall():
                    doc_id = json.loads(row[4] or "{}").get("doc_id")
                    if (row[0] in taken and row[0] not in in_trash) or (doc_id and doc_id in live_docs):
                        pages_skipped += 1
                        continue
                    new_roots.append(row)
                if new_roots:
                    keys = generate_n_keys_between(last_child_position(dst, "root"), None, n=len(new_roots))
                    for row, key in zip(new_roots, keys):
                        if row[0] in in_trash:  # only in Recently deleted: the backup's version replaces it
                            taken.difference_update(r[0] for r in fetch_subtree(dst, row[0]))
                            delete_subtree(dst, row[0])
                            untrashed.append(row[0])
                        rows = _fresh_ids(fetch_subtree(src, row[0]), taken)
                        rows[0][2] = key  # append after the existing root pages
                        dst.executemany(f"INSERT INTO unified_blocks ({BLOCK_COLUMNS}) VALUES (?, ?, ?, ?, ?, ?, ?)",
                                        rows)
                        dst.execute("DELETE FROM deleted_pages WHERE page_id = ?", (row[0],))
                        ops.log_reload(dst, row[0], by, after=backup_seqs.get(row[0], 0))
                        added.append(row[0])
                dst.commit()
            except BaseException:
                dst.rollback()
                raise

    snap = tdir / "data.db"
    if snap.exists():
        src = sqlite3.connect(str(snap))
        dst = connect_data_db(ws)  # the live file: WAL and the busy timeout
        try:
            src_tables = {r[0] for r in src.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
            if "chats" in src_tables:
                dst.execute("CREATE TABLE IF NOT EXISTS chats "
                            "(block_id TEXT PRIMARY KEY, messages TEXT NOT NULL, updated_at TEXT NOT NULL, "
                            "title TEXT NOT NULL DEFAULT '')")
                for row in src.execute("SELECT block_id, messages, updated_at FROM chats"):
                    cur = dst.execute(
                        "INSERT OR IGNORE INTO chats (block_id, messages, updated_at) VALUES (?, ?, ?)", row)
                    chats_added += cur.rowcount
                dst.commit()
        finally:
            src.close()
            dst.close()
    if added:
        _announce(ws, added)
    return {"pages_added": len(added), "pages_skipped": pages_skipped, "chats_added": chats_added,
            "from_trash": len(untrashed)}


# --- stored snapshots -------------------------------------------------------------------

def store_dir(ws: str) -> Path:
    return config.BACKUPS_DIR / "workspaces" / safe_ws_id(ws)


def backup_path(ws: str, name: str) -> Path | None:
    """The snapshot file for a validated name (None for a bad name — names
    come from the client)."""
    if not NAME_RE.match(name or ""):
        return None
    return store_dir(ws) / f"{name}.zip"


def info(ws: str, name: str) -> dict | None:
    path = backup_path(ws, name)
    if not path or not path.is_file():
        return None
    m = read_manifest(path)
    return {"name": name, "size_bytes": path.stat().st_size, "created_at": m.get("exported_at", ""),
            "label": m.get("label", ""), "uploads": bool(m.get("uploads")),
            "upload_files": m.get("upload_files", 0), "by": m.get("exported_by", ""),
            "scheduled": bool(m.get("scheduled")), "task_id": m.get("task_id", ""),
            "auto": bool(m.get("auto")), "missing_uploads": len(m.get("missing_uploads") or []),
            "damaged": sorted(n for n, r in (m.get("integrity") or {}).items() if r != "ok")}


def list_backups(ws: str) -> list[dict]:
    """The workspace's snapshots, newest first (by the time in the manifest,
    then the name: ``<time>-<label>.2`` follows ``<time>-<label>``)."""
    d = store_dir(ws)
    if not d.is_dir():
        return []
    out = [b for b in (info(ws, f.stem) for f in d.glob("*.zip")) if b]
    return sorted(out, key=lambda b: (b["created_at"], b["name"]), reverse=True)


_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()


def _lock_for(ws: str) -> threading.Lock:
    with _locks_guard:
        return _locks.setdefault(safe_ws_id(ws), threading.Lock())


def _free_name(d: Path, label: str) -> str:
    """``<time>-<label>``, or ``<time>-<label>.<n>`` when that is taken (a
    second snapshot in the same second)."""
    stamp = time.strftime("%Y%m%d-%H%M%S")
    name, n = f"{stamp}-{label}", 2
    while (d / f"{name}.zip").exists():
        suffix = f".{n}"
        name = f"{stamp}-{label[:40 - len(suffix)]}{suffix}"
        n += 1
    return name


def check_free_space(path: Path) -> None:
    """Refuse a new snapshot while the disk holding ``path`` has less than
    MIN_FREE_BYTES free: a backup that fills the disk breaks the library it
    was meant to protect."""
    free = shutil.disk_usage(path).free
    if free < MIN_FREE_BYTES:
        raise BackupError(f"only {free // (1 << 20)} MB is free on the server's disk; backups resume "
                          f"once {MIN_FREE_BYTES // (1 << 20)} MB is free")


def create(ws: str, *, label: str = "manual", uploads: bool = True, by: str = "", scheduled: bool = False,
           task_id: str = "", auto: bool = False) -> dict:
    """Take a snapshot: ``scheduled`` for a backup task's run, ``auto`` for
    the one a replace restore keeps, else a manual one. One at a time per
    workspace; written under a unique temporary name and renamed when
    complete. Raises BackupError on a bad label, a full store
    (MAX_PER_WORKSPACE manual, MAX_SCHEDULED_PER_WORKSPACE scheduled
    snapshots) or a disk with less than MIN_FREE_BYTES free."""
    if not LABEL_RE.match(label or ""):
        raise BackupError("label must be 1-40 chars of letters, digits, _ . -")
    with _lock_for(ws):
        have = list_backups(ws)
        if scheduled and sum(b["scheduled"] for b in have) >= MAX_SCHEDULED_PER_WORKSPACE:
            raise BackupError(f"this workspace already keeps {MAX_SCHEDULED_PER_WORKSPACE} scheduled backups — "
                              "keep fewer in its tasks' retention")
        if not scheduled and not auto and \
                sum(not b["scheduled"] and not b["auto"] for b in have) >= MAX_PER_WORKSPACE:
            raise BackupError(f"this workspace already has {MAX_PER_WORKSPACE} backups — delete one first")
        d = store_dir(ws)
        d.mkdir(parents=True, exist_ok=True)
        check_free_space(d)
        name = _free_name(d, label)
        dest = d / f"{name}.zip"
        tmp = d / f".{name}.{secrets.token_hex(4)}.part"
        try:
            write_zip(ws, tmp, uploads=uploads, by=by, label=label, scheduled=scheduled, task_id=task_id,
                      auto=auto)
            tmp.replace(dest)  # never a half-written snapshot in the listing
        except BaseException:
            tmp.unlink(missing_ok=True)
            raise
    return info(ws, name)


def delete(ws: str, name: str) -> bool:
    path = backup_path(ws, name)
    if not path or not path.is_file():
        return False
    path.unlink()
    return True


def remove_all(ws: str) -> None:
    """Drop the workspace's snapshot directory (with the workspace)."""
    try:
        d = store_dir(ws)
    except ValueError:
        return
    if d.is_dir():
        shutil.rmtree(str(d), ignore_errors=True)


# --- the final copy of a deleted workspace ----------------------------------------------

def deleted_dir() -> Path:
    return config.BACKUPS_DIR / "deleted"


def keep_final_copy(ws: str, *, by: str = "") -> str:
    """Before a workspace is deleted: one full backup zip of it (uploads
    included) in ``backups/deleted/<id>-<name>-<time>.zip`` — outside the
    workspace's folder and its snapshot folder, which go with it — that
    ``/api/import-data`` restores into any workspace. Kept
    DELETED_KEEP_DAYS days. Returns the file's name; "" for a guest's
    workspace (guests keep nothing) or one without files. Raises
    BackupError when it cannot be written: the caller must then leave the
    workspace alone."""
    from . import workspaces

    if workspaces.is_guest_workspace(ws) or not (ws_dir(ws) / "pages.db").exists():
        return ""
    d = deleted_dir()
    name = re.sub(r"[^A-Za-z0-9_.-]+", "-", (workspaces.get(ws) or {}).get("name", "")).strip("-.")[:40]
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    dest = d / f"{safe_ws_id(ws)}-{name or 'workspace'}-{stamp}.zip"
    tmp = d / f".{dest.name}.{secrets.token_hex(4)}.part"
    try:
        d.mkdir(parents=True, exist_ok=True)
        write_zip(ws, tmp, uploads=True, by=by, label="deleted")
        tmp.replace(dest)
    except Exception as e:
        tmp.unlink(missing_ok=True)
        raise BackupError(f"its final copy could not be written ({e})") from e
    log.info(f"[backups] kept a final copy of workspace {ws} before deleting it: {dest}")
    _prune_deleted()
    return dest.name


def _prune_deleted() -> None:
    cutoff = datetime.now(timezone.utc).timestamp() - DELETED_KEEP_DAYS * 86400
    for f in deleted_dir().glob("*.zip"):
        try:
            if f.stat().st_mtime < cutoff:
                f.unlink()
                log.info(f"[backups] removed {f.name}, a deleted workspace's copy older than {DELETED_KEEP_DAYS} days")
        except OSError:
            continue
