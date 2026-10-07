"""One workspace's backup: the ``gamma-backup-1`` zip, and the snapshots of
it the server keeps.

The zip is what ``GET /api/export`` downloads and ``POST /api/import-data``
restores: consistent copies of ``pages.db`` and ``data.db`` (taken with the
SQLite backup API, so safe while the app serves), every stored file
(``uploads/``) when asked, and a ``manifest.json``. ``write_zip`` writes one,
``restore_zip`` applies one (replace or merge) — the two halves of every
backup path in the app, so a stored snapshot, a downloaded export and a
page export all restore the same way. Every database copy is quick-checked
on the way in and out (``gamma/integrity.py``): a snapshot records the
result in its manifest, a restore refuses a damaged backup before touching
anything. What is each account's own in the workspace (``PRIVATE_TABLES``:
the members' open tabs, recents and reading positions) is no library
content: the zip's pages.db has those tables emptied, and a restore keeps
the live rows.

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

from . import config, integrity, jobs, pdf_index, storage, upload_gc
from .backups import snapshot_db
from .blocks_store import (BLOCK_COLUMNS, FOLDERS, IN_LIBRARY, LABELS, PATH_SEP, STORED_COLUMNS, TRASH, TREES,
                           delete_subtree, existing_in, fetch_subtree, filing, folder_paths, last_child_position,
                           named, new_block_id, touch_page, tree_children, tree_parents, tree_rows, write_lock)
from .db import (PAGES_SCHEMA, account_names, connect_pages_db, connect_users_db, copy_chats, page_now,
                 register_functions, safe_ws_id, ws_dir)
from .logbuf import log
from .normalize import (block_columns, block_fts, folder_blocks, highlight_shape, normalize_data_db,
                        normalize_pages_db, page_changes, page_ops_batch_id, pages_db_chats)
from .seed import create_workspace_files

FORMAT = "gamma-backup-1"
# pages.db tables that hold each account's own state, not the library's
# (db.WORKSPACE_PREFS_SCHEMA): empty in every zip, never replaced by a restore.
PRIVATE_TABLES = ("workspace_prefs",)
SCRUB_CHUNK = 64 << 20             # bytes of zeros per row when a copy's free pages are overwritten
MAX_PER_WORKSPACE = 20             # manual snapshots per workspace
MAX_SCHEDULED_PER_WORKSPACE = 100  # scheduled ones per workspace, every task together
PRE_RESTORE_KEEP = 3               # "pre-restore" snapshots kept per workspace
MIN_FREE_BYTES = 1 << 30           # no new snapshot while the disk has less free than this
DELETED_KEEP_DAYS = 90             # final copies of deleted workspaces (backups/deleted/)
STALE_TEMP_S = 3600                # a snapshot's work file this old was left by a killed process
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
              progress=None, scheduled: bool = False, task_id: str = "", auto: bool = False) -> dict:
    """Write the workspace's backup zip to ``dest``. The databases are
    copied first and the uploads listed only after them, so every file the
    copied pages reference is stored when the list is taken; a file that
    goes in between (an orphan sweep) is recorded in ``missing_uploads``,
    never a failed backup. Each database copy is quick-checked
    (``integrity`` in the manifest, and the workspace's latest check).
    ``by`` is the id of the account taking it; the manifest names people by
    username. ``progress`` (a background job's report, gamma/jobs.py) hears
    the bytes written so far of the estimated total. Returns the manifest."""
    from . import workspaces  # local: workspaces imports seed, which imports db

    progress = progress or jobs.no_progress
    root = ws_dir(ws)
    db_files = [root / n for n in ("pages.db", "data.db") if (root / n).exists()]
    estimate = storage.usage(ws) if uploads else 0
    done, total = 0, sum(_size(f) for f in db_files) + estimate
    progress(done=done, total=total, unit="bytes")
    info = workspaces.get(ws) or {}
    owner = workspaces.personal_owner(ws)
    with connect_users_db() as conn:
        names = account_names(conn, [owner, by])
    checks, stored, missing = {}, [], []
    with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as z:
        for src in db_files:
            snap = Path(str(dest) + "." + src.name)
            try:
                checks[src.name] = snapshot_db(src, snap)
                if src.name == "pages.db" and not checks[src.name].startswith("unreadable"):
                    _strip_private(snap)
                z.write(snap, src.name)
            finally:
                for side in (snap, Path(str(snap) + "-wal"), Path(str(snap) + "-shm")):
                    side.unlink(missing_ok=True)
            done += _size(src)
            progress(done=done, total=total, unit="bytes")
        if uploads:
            listed = sorted(storage.list(ws))
            total = done + sum(size for _, size, _ in listed)
            for name, size, _ in listed:
                try:
                    path = storage.open_path(ws, name)
                    if path is None:
                        raise FileNotFoundError(name)
                    z.write(path, f"uploads/{name}")
                except FileNotFoundError:
                    missing.append(name)  # swept since the listing: no copied page names it
                    continue
                stored.append(name)
                done += size
                progress(done=done, total=total, unit="bytes")
        manifest = {
            "format": FORMAT,
            "workspace": ws,
            "workspace_name": info.get("name", ""),
            "kind": info.get("kind", ""),
            "user": names.get(owner) or next(  # whose it is
                (m["username"] for m in workspaces.members(ws) if m["role"] == "owner"), ""),
            "exported_by": names.get(by, ""),
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


def _strip_private(copy: Path) -> None:
    """Empty the ``PRIVATE_TABLES`` of a pages.db copy before it is zipped
    (a copy without them, of a workspace not upgraded to step 34 yet, keeps
    those rows in users.db), leaving none of their bytes in the file: with
    ``secure_delete`` on, what the delete frees is overwritten, and the
    pages that were free already in the copy (an older value of a pref, the
    file's own free pages) are taken into a table of zeros and freed again.
    A damaged copy that refuses is zipped as it is, logged; a write that
    fails (a full disk) fails the backup, as the copy itself would."""
    try:
        with closing(sqlite3.connect(str(copy))) as conn:
            conn.execute("PRAGMA secure_delete = ON")
            tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
            for table in PRIVATE_TABLES:
                if table in tables:
                    conn.execute(f"DELETE FROM {table}")
            free = conn.execute("PRAGMA freelist_count").fetchone()[0] * conn.execute("PRAGMA page_size").fetchone()[0]
            if free:
                conn.execute("CREATE TABLE gamma_scrub (zeros BLOB)")
                for at in range(0, free, SCRUB_CHUNK):  # SQLite caps one blob at 1 GB
                    conn.execute("INSERT INTO gamma_scrub VALUES (zeroblob(?))", (min(SCRUB_CHUNK, free - at),))
                conn.execute("DROP TABLE gamma_scrub")
            conn.commit()
    except sqlite3.OperationalError:
        raise
    except sqlite3.DatabaseError as e:
        log.warning(f"[backups] {copy.name}: the private tables of a damaged copy could not be emptied: {e}")


def read_manifest(path: Path) -> dict:
    """The manifest of a backup zip, or {} when it has none."""
    try:
        with zipfile.ZipFile(path) as z:
            return json.loads(z.read("manifest.json"))
    except (OSError, KeyError, ValueError, zipfile.BadZipFile):
        return {}


# --- restore -------------------------------------------------------------------------

def restore_zip(ws: str, zpath: Path, mode: str = "replace", *, selected: set[str] | None = None,
                by: str = "", progress=None) -> dict:
    """Apply a backup zip to the workspace.

    Everything is checked before any live data is touched: the zip's shape
    and ``PRAGMA quick_check`` of its databases (a damaged backup is refused
    whole). Then the unpacked copies are brought to the current shapes
    (normalized: a backup can be older than any step — its chats move from
    its data.db into its pages.db — and its notes index is built again).
    ``replace``: what the workspace holds now is kept as an automatic
    ``pre-restore`` snapshot (uploads included; not for a guest's workspace
    — refused when that snapshot cannot be taken), then pages.db is copied
    in within one write transaction and data.db with the sqlite backup API
    (both transactional, safe while the app serves).
    ``merge``: additive — pages and chats the workspace does not have are
    appended, everything it has stays (``_merge``). In both modes the
    backup's uploads the workspace lacks are copied in first (content-hash
    names: identical files never conflict, nothing existing is
    overwritten); after a replace, files only the old pages used are left
    to the orphan cleanup.

    Every page the restore wrote moves forward in its op log (a ``reload``
    above any seq a client has seen, so seqs never go back) and in the
    workspace's change log, which a restore never replaces; pages a replace
    removed turn ``deleted`` there; the open pages are told to reload. ``by``
    (an account id) is the actor in the log and on the snapshot. ``progress``
    (a background job's report, gamma/jobs.py) hears each phase — unpacking,
    checking, saving (the pre-restore snapshot), copying (the files), then
    restoring, from which point on the restore can no longer be stopped.
    Raises BackupError."""
    if mode not in ("replace", "merge"):
        raise BackupError("mode must be 'replace' or 'merge'")
    if selected is not None and mode != "merge":
        raise BackupError("selection is only supported for additive imports")
    progress = progress or jobs.no_progress
    with tempfile.TemporaryDirectory(prefix="gamma-restore-") as td:
        tdir = Path(td)
        upload_names = _unpack(zpath, tdir, progress)
        progress(phase="checking")
        _validate(tdir)
        _normalize_copies(tdir)
        root = ws_dir(ws)
        review = None
        if selected is not None:
            review = _review_import(ws, tdir, upload_names)
            from .import_review import validate_selection
            validate_selection(selected, (p["selection_ids"][0] for p in review))
            chosen = [p for p in review if p["selection_ids"][0] in selected]
            keep_blocks = {bid for p in chosen for bid in p["_blocks"] | p["_tree"]}
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
            with closing(sqlite3.connect(str(tdir / "pages.db"))) as conn, conn:
                register_functions(conn)  # the deletes fire the notes index's triggers
                for name, keep in (("keep_blocks", keep_blocks), ("keep_chats", keep_chats)):
                    conn.execute(f"CREATE TEMP TABLE {name} (id TEXT PRIMARY KEY)")
                    conn.executemany(f"INSERT INTO {name} VALUES (?)", ((i,) for i in keep))
                conn.execute("DELETE FROM unified_blocks WHERE id NOT IN (SELECT id FROM keep_blocks)")
                for table in ("chats", "chat_history"):
                    conn.execute(f"DELETE FROM {table} WHERE bucket NOT IN (SELECT id FROM keep_chats)")
            upload_names = [n for n in upload_names if n in keep_uploads]
        if not (root / "pages.db").exists():
            create_workspace_files(ws)
        if mode == "merge":
            uploads_added = _copy_uploads(ws, tdir, upload_names, progress)
            progress(phase="restoring", stoppable=False)
            result = _merge(ws, tdir, by)
        else:
            progress(phase="saving")
            pre = _keep_current(ws, by, progress)
            uploads_added = _copy_uploads(ws, tdir, upload_names, progress)
            progress(phase="restoring", stoppable=False)
            result = {**_replace(ws, root, tdir, by), "pre_restore": pre["name"] if pre else ""}
    if review is not None:
        result["pages"] = [{k: v for k, v in p.items() if not k.startswith("_")} for p in chosen]
        result["warnings"] = [w for p in chosen for w in p["warnings"]]
    return {"mode": mode, "workspace": ws, **result,
            "uploads_in_backup": len(upload_names), "uploads_added": uploads_added}


def _copy_uploads(ws: str, tdir: Path, upload_names: list[str], progress=jobs.no_progress) -> int:
    """The backup's files the workspace lacks, each stored whole
    (``storage.put_upload``): a restore cut short never leaves a
    truncated file under a content-hash name."""
    added = 0
    for n, base in enumerate(upload_names):
        progress(phase="copying", done=n, total=len(upload_names), unit="files")
        if not storage.exists(ws, base):
            storage.put_upload(ws, base, (tdir / "uploads" / base).read_bytes())
            added += 1
    return added


def _normalize_copies(tdir: Path) -> None:
    """Bring the unpacked databases to the current shapes before anything
    reads them or anything live is touched. pages.db, in this order: a copy
    stamped below the base (``migrations.WS_VERSION_BASE``; 0, a backup
    from before the stamps, can be of any age up to it) goes through the
    normalizers of the steps that walked every workspace: the block
    table's hot fields (``normalize.block_columns``, step 26), the change
    log (``page_changes``, step 27), the chats its data.db held
    (``pages_db_chats``, step 28) and the op log's batch columns
    (``page_ops_batch_id``, step 33). Then every copy runs the workspace
    steps above its stamp (0 counting as the base,
    ``migrations.run_workspace_steps``), and the schema statements (an old
    file lacks the op log), the notes index built from its rows
    (``block_fts``, step 28), the folder and label trees (``folder_blocks``,
    step 29), the content normalizers (whose rewrites the index follows)
    and the highlight shape (``highlight_shape``, step 30) follow; the copy
    is stamped current. data.db: ``normalize_data_db`` drops the tables
    that moved out of it."""
    from . import migrations  # local: only a restore runs the step list

    data_db = tdir / "data.db"
    with closing(sqlite3.connect(str(tdir / "pages.db"))) as conn:
        register_functions(conn)
        stamp = conn.execute("PRAGMA user_version").fetchone()[0]
        if stamp < migrations.WS_VERSION_BASE:
            block_columns(conn)
            page_changes(conn)
            pages_db_chats(conn, data_db)
            page_ops_batch_id(conn)
        steps = migrations.workspace_steps_after(migrations.workspace_version(conn))
        if steps:
            data = sqlite3.connect(str(data_db)) if data_db.exists() else None
            try:
                migrations.run_workspace_steps("", conn, data, steps)
            except migrations.MigrationError as e:
                raise BackupError(f"the backup could not be brought to this version of Gamma: {e}") from e
            finally:
                if data is not None:
                    data.close()
        for stmt in PAGES_SCHEMA:
            conn.execute(stmt)
        block_fts(conn)
        folder_blocks(conn)
        normalize_pages_db(conn)
        highlight_shape(conn)
        upload_gc.restart_clocks(conn)  # its orphan rows' 30 days start now, not when it was taken
        if stamp < migrations.SCHEMA_VERSION:
            conn.execute(f"PRAGMA user_version = {migrations.SCHEMA_VERSION}")
            conn.commit()
    if data_db.exists():
        with closing(sqlite3.connect(str(data_db))) as conn:
            normalize_data_db(conn)


def _keep_current(ws: str, by: str, progress=jobs.no_progress) -> dict | None:
    """Before a replace: the workspace as it is now, as an automatic
    ``pre-restore`` snapshot with its uploads (the newest PRE_RESTORE_KEEP
    stay). None for a guest's workspace, which keeps no snapshots."""
    from . import workspaces

    if workspaces.is_guest_workspace(ws):
        return None
    try:
        kept = create(ws, label="pre-restore", uploads=True, by=by, auto=True, progress=progress)
    except (OSError, sqlite3.Error, BackupError) as e:
        raise BackupError(f"nothing was restored: the workspace's current state could not be "
                          f"saved first ({e})") from e
    for old in [b for b in list_backups(ws) if b["auto"]][PRE_RESTORE_KEEP:]:
        delete(ws, old["name"])
    return kept


def _copy_tables(conn, schema: str, keep: tuple = ()) -> None:
    """Make every table of ``conn``'s main database but those in ``keep``
    hold what the attached ``schema`` holds (the columns both have; a table
    the copy lacks ends empty) — inside the caller's transaction. Ordinary
    tables only: the notes index (an FTS5 table and its shadow tables)
    follows the block rows through its triggers as they are copied."""
    theirs = {r[0] for r in conn.execute(f"SELECT name FROM {schema}.sqlite_master WHERE type = 'table'")}
    for table in [r[1] for r in conn.execute("PRAGMA main.table_list")
                  if r[2] == "table" and not r[1].startswith("sqlite_")]:
        if table in keep:
            continue
        conn.execute(f'DELETE FROM main."{table}"')
        if table not in theirs:
            continue
        have = {r[1] for r in conn.execute(f'PRAGMA {schema}.table_info("{table}")')}
        cols = ", ".join(f'"{r[1]}"' for r in conn.execute(f'PRAGMA main.table_info("{table}")') if r[1] in have)
        conn.execute(f'INSERT INTO main."{table}" ({cols}) SELECT {cols} FROM {schema}."{table}"')


def _replace(ws: str, root: Path, tdir: Path, by: str) -> dict:
    """Swap the (normalized) copies in. pages.db is copied into the live
    file in ONE write transaction, which also reads the live op log and
    writes the new one: each restored page gets a ``reload`` above the
    highest seq either side had, read under the same lock — so no batch
    committed while the restore runs can hold a seq the log hands out again
    (a client never sees a seq go back). The change log is the live one,
    carried on (a copy's cursor in it stays good): every restored page is
    touched live, and every page that is no page of the library now — one
    the restore removed, one in the restored Recently deleted, one the
    backup's log has as deleted — is touched ``deleted`` unless it already
    is. The chats come with the pages (the backup's replace the live ones),
    and the notes index follows the copied rows (its triggers). The
    accounts' own tables (``PRIVATE_TABLES``) stay the live ones too. data.db
    follows through the sqlite backup API."""
    from . import ops

    with connect_pages_db(ws) as live:
        live.execute("ATTACH DATABASE ? AS restored", (str(tdir / "pages.db"),))
        try:
            write_lock(live)  # batches wait here until the restore is in, then land above it
            live_seqs = dict(live.execute("SELECT page_id, MAX(seq) FROM page_ops GROUP BY page_id").fetchall())
            live_pages = {r[0] for r in live.execute("SELECT id FROM unified_blocks WHERE parent_id = 'root'")}
            live_ids = {r[0] for r in live.execute("SELECT id FROM unified_blocks")}
            _copy_tables(live, "restored", keep=("page_changes", *PRIVATE_TABLES))
            pages = [r[0] for r in live.execute("SELECT id FROM unified_blocks WHERE parent_id = 'root'")] + [
                tree for tree in TREES if live.execute("SELECT 1 FROM unified_blocks WHERE id = ?", (tree,)).fetchone()]
            for page in pages:
                ops.log_reload(live, page, by, after=live_seqs.get(page, 0))
            removed = sorted(live_pages - set(pages))
            unpaged = {r[0] for r in live.execute(  # no page of the library now, and not deleted yet
                "SELECT page_id FROM page_changes WHERE kind = 'live' UNION "
                "SELECT id FROM unified_blocks WHERE parent_id = ? UNION "
                "SELECT page_id FROM restored.page_changes WHERE kind = 'deleted' EXCEPT "
                "SELECT page_id FROM page_changes WHERE kind = 'deleted'", (TRASH,))}
            for page in sorted(unpaged - set(pages)):
                touch_page(live, page, by, "deleted")
            restored_ids = {r[0] for r in live.execute("SELECT id FROM unified_blocks")}
            live.commit()
        except BaseException:
            live.rollback()
            raise
        finally:
            live.execute("DETACH DATABASE restored")
    restored = ["pages.db"]
    snap = tdir / "data.db"
    if snap.exists():
        with closing(sqlite3.connect(str(snap))) as src_conn, \
                closing(sqlite3.connect(str(root / "data.db"), timeout=10)) as dst_conn:
            src_conn.backup(dst_conn)
        restored.append("data.db")
    if live_ids - restored_ids:  # the PDF rows of papers that went (data.db may not have come with the backup)
        with connect_pages_db(ws) as conn:
            pdf_index.purge_unused(ws, conn)
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
    ops.notify_commit(ws)


def _review_import(ws, tdir, upload_names):
    """Plan the same additive merge, using only the extracted (normalized)
    snapshot. A chat is a bucket's conversations, the active one and its
    history: with its page when the bucket is one of the backup's pages,
    else an entry of its own (the library's chat, or a folder's). An entry's
    ``folders`` are the paths (lists of names) it is filed under — the
    workspace's page's when it has the page —, its ``_tree`` the backup's
    folders and labels it needs (with the folders above them)."""
    from .sync_tree import upload_refs

    available = set(upload_names) | {name for name, _, _ in storage.list(ws)}
    conversations = "SELECT 'chats', bucket, bucket, messages FROM chats UNION ALL " \
                    "SELECT 'chat_history', id, bucket, messages FROM chat_history"
    with closing(sqlite3.connect(str(ws_dir(ws) / "pages.db"))) as live:
        # the library's blocks: a page only in Recently deleted comes back (``_merge``)
        live_pages = {r[0]: (r[1], json.loads(r[2] or "{}"))
                      for r in live.execute(f"SELECT id, content, properties FROM unified_blocks WHERE {IN_LIBRARY}")}
        live_docs = dict(live.execute(
            "SELECT doc_id, id FROM unified_blocks WHERE doc_id IS NOT NULL AND parent_id = 'root'").fetchall())
        live_chats = {(table, key) for table, key, _, _ in live.execute(conversations)}
        live_paths = folder_paths(live)
    chats = {}  # bucket -> [((table, key), messages)], the backup's conversations
    pages, claimed_chats = [], set()
    with closing(sqlite3.connect(str(tdir / "pages.db"))) as src:
        paths = folder_paths(src)
        parents = tree_parents(src, FOLDERS)

        def above(folder_id):  # the folder and the folders above it
            while folder_id in parents:
                yield folder_id
                folder_id = parents[folder_id]

        for table, key, bucket, messages in src.execute(conversations):
            chats.setdefault(bucket, []).append(((table, key), messages))
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
                                  + [{"content": messages} for c in page_chats for _, messages in chats[c]])
            missing = uploads - available
            selection_id = f"page:{row[0]}"
            warnings = [{"title": row[3], "reason": f"Missing attachment: {name}", "selection_id": selection_id}
                        for name in sorted(missing)]
            shown = live_paths if destination_id else paths
            tree = {f for i in filing(props, FOLDERS) for f in above(i)} | set(filing(props, LABELS))
            pages.append({"id": destination_id or row[0], "title": title,
                          "folders": [shown[i] for i in filing(destination_props, FOLDERS) if i in shown],
                          "selection_ids": [selection_id], "kind": "pdf" if destination_props.get("doc_id") else "page",
                          "action": "skip" if destination_id else "create",
                          "source_paths": ["pages.db", *[f"uploads/{n}" for n in sorted(uploads)]],
                          "warnings": warnings, "missing": bool(missing),
                          "_blocks": ids, "_tree": tree, "_chats": page_chats, "_uploads": uploads,
                          "_references": references})
    for chat_id, held in chats.items():
        if chat_id in claimed_chats:
            continue
        uploads = upload_refs([{"content": messages} for _, messages in held])
        selection_id = f"chat:{chat_id}"
        title = f"Folder chat: {PATH_SEP.join(paths[chat_id])}" if chat_id in paths else "Library chat"
        warnings = [{"title": title, "reason": f"Missing attachment: {name}", "selection_id": selection_id}
                    for name in sorted(uploads - available)]
        known = all(key in live_chats for key, _ in held)
        pages.append({"id": chat_id, "title": title, "folders": [["Chats"]], "kind": "chat",
                      "action": "skip" if known else "create", "selection_ids": [selection_id], "source_paths": ["pages.db"],
                      "warnings": warnings, "missing": bool(uploads - available),
                      "_blocks": set(), "_tree": set(above(chat_id)), "_chats": {chat_id}, "_uploads": uploads})
    return pages


def preview_zip(ws: str, zpath: Path) -> dict:
    from .import_review import archive_entries
    with tempfile.TemporaryDirectory(prefix="gamma-preview-") as td:
        tdir = Path(td)
        uploads = _unpack(zpath, tdir)
        _validate(tdir)
        _normalize_copies(tdir)
        pages = _review_import(ws, tdir, uploads)
    with zipfile.ZipFile(zpath) as zf:
        entries = archive_entries(zf)
    return {"pages": [{k: v for k, v in p.items() if not k.startswith("_")} for p in pages],
            "entries": entries, "warnings": [w for p in pages for w in p["warnings"]], "folder": ""}


def _unpack(zpath: Path, tdir: Path, progress=jobs.no_progress) -> list[str]:
    progress(phase="unpacking")
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
        # Accept flat uploads/<file> entries only — the exporter never
        # writes nested paths or dotfiles (also a zip-slip guard).
        entries = [n for n in sorted(names)
                   if n == f"uploads/{os.path.basename(n)}" and os.path.basename(n)
                   and not os.path.basename(n).startswith(".")]
        for i, n in enumerate(entries):
            progress(phase="unpacking", done=i, total=len(entries), unit="files")
            base = os.path.basename(n)
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
    """A page's rows (``fetch_subtree``'s: the root first, every parent
    before its children) ready to insert as ``STORED_COLUMNS``, each in the
    page of the root. A block whose id the workspace already uses — it lives
    on another page now — gets a new id and its children follow it there,
    so nothing is grafted under a live block; a row whose parent is not part
    of the copy is left out."""
    remap, inside, out = {}, set(), []
    for i, row in enumerate(rows):
        vals = list(row)  # BLOCK_COLUMNS: the stored columns
        vals[1] = remap.get(vals[1], vals[1])
        if i and vals[1] not in inside:
            continue
        if vals[0] in taken:
            fresh = new_block_id()
            while fresh in taken:
                fresh = new_block_id()
            remap[vals[0]] = fresh
            vals[0] = fresh
        vals[7] = out[0][0] if out else vals[0]  # page_id
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
    (``_fresh_ids``). Each added page gets a ``reload`` at the top of its
    op log, which touches it live in the change log, so the change feed, a
    clone and an old tab all see it. Chats merge the same way, in the same
    transaction: every conversation the workspace lacks (a bucket's active
    one, an archived one by its id) is added; ``chats_added`` counts them.
    The backup's folders and labels join the workspace's trees first
    (``_merge_trees``), and the pages it adds are filed under the
    workspace's ids for them (an id that is no folder or label of the
    workspace is dropped), the folder chats moved with their folders.
    Nothing in the backup's data.db is touched."""
    from fractional_indexing import generate_n_keys_between

    from . import ops

    pages_skipped = chats_added = 0
    added, untrashed, grew = [], [], []
    snap = tdir / "pages.db"
    if snap.exists():
        with closing(sqlite3.connect(str(snap))) as src, connect_pages_db(ws) as dst:
            backup_seqs = dict(src.execute("SELECT page_id, MAX(seq) FROM page_ops GROUP BY page_id").fetchall())
            write_lock(dst)
            try:
                taken = {r[0] for r in dst.execute("SELECT id FROM unified_blocks")}
                mapping, grew = _merge_trees(src, dst, taken)
                in_trash = {r[0] for r in dst.execute("SELECT id FROM unified_blocks WHERE parent_id = ?", (TRASH,))}
                live_docs = {r[0] for r in dst.execute(
                    "SELECT doc_id FROM unified_blocks WHERE doc_id IS NOT NULL AND parent_id = 'root'")}
                new_roots = []
                for row in src.execute("SELECT id, doc_id FROM unified_blocks WHERE parent_id = 'root' "
                                       "ORDER BY position ASC").fetchall():
                    doc_id = row[1]
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
                        rows[0][4] = _refiled(dst, rows[0][4], mapping)
                        dst.executemany(f"INSERT INTO unified_blocks ({STORED_COLUMNS}) "
                                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)", rows)
                        ops.log_reload(dst, row[0], by, after=backup_seqs.get(row[0], 0))
                        added.append(row[0])
                for tree in grew:
                    ops.log_reload(dst, tree, by)
                for theirs, mine in mapping.items():  # a folder's chat goes where the folder went
                    if theirs != mine:
                        src.execute("UPDATE OR IGNORE chats SET bucket = ? WHERE bucket = ?", (mine, theirs))
                        src.execute("UPDATE chat_history SET bucket = ? WHERE bucket = ?", (mine, theirs))
                chats_added = copy_chats(src, dst)
                dst.commit()
            except BaseException:
                dst.rollback()
                raise
    if added or grew:
        _announce(ws, added + grew)
    return {"pages_added": len(added), "pages_skipped": pages_skipped, "chats_added": chats_added,
            "from_trash": len(untrashed)}


def _merge_trees(src, dst, taken: set) -> tuple[dict, list[str]]:
    """The backup's folder and label trees into the workspace's, inside the
    merge's transaction: a block of the backup's tree is the workspace's
    block of that id when it has one there, else the one at the same place
    with the same name (a folder under the folder its parent became, a
    label of that name; ``blocks_store.named``), else it is added there,
    last among its siblings, under its own id unless the workspace uses it
    (``taken``, which learns the new ids). Returns ``({backup id: workspace
    id}, the trees that gained blocks)``."""
    from fractional_indexing import generate_key_between

    mapping, grew = {}, []
    now = page_now()
    for tree in TREES:
        mine = {r[0] for r in dst.execute("SELECT id FROM unified_blocks WHERE page_id = ?", (tree,))}
        kids = tree_children(dst, tree)
        created = dict(src.execute("SELECT id, created_at FROM unified_blocks WHERE page_id = ?", (tree,)).fetchall())
        for bid, parent, name, props in tree_rows(src, tree):  # parents first
            if bid in mine:
                mapping[bid] = bid
                continue
            at = mapping.get(parent, tree)
            same = named(kids.get(at, []), name or "")
            if same:
                mapping[bid] = same[0][0]
                continue
            new_id = bid if bid not in taken else new_block_id()
            dst.execute(f"INSERT INTO unified_blocks ({STORED_COLUMNS}) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                        (new_id, at, generate_key_between(last_child_position(dst, at), None), name or "",
                         props or "{}", created.get(bid) or now, now, tree))
            taken.add(new_id)
            kids.setdefault(at, []).append((new_id, name or ""))
            mapping[bid] = new_id
            if tree not in grew:
                grew.append(tree)
    return mapping, grew


def _refiled(dst, props_raw: str, mapping: dict) -> str:
    """A page root's properties from the backup, filed under the
    workspace's folder and label ids (``mapping``), ids it does not have
    left out."""
    props = json.loads(props_raw or "{}")
    for tree in TREES:
        if tree in props:
            kept = existing_in(dst, tree, [mapping.get(i, i) for i in filing(props, tree)])
            if kept:
                props[tree] = kept
            else:
                props.pop(tree)
    return json.dumps(props)


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
            "damaged": integrity.damaged(m.get("integrity"))}


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


def _is_temp(path: Path) -> bool:
    """A snapshot's work file: ``.<name>.part`` (a zip, a server backup's
    folder) or a database copy beside it (``.<name>.part.pages.db``, its
    ``-wal`` / ``-shm``)."""
    return path.name.startswith(".") and ".part" in path.name


def _sweep_temp(d: Path, now: float) -> int:
    removed = 0
    if not d.is_dir():
        return 0
    for f in d.iterdir():
        try:
            if not _is_temp(f):
                continue
            # a folder is written deep inside (a server backup's uploads): its newest file dates it
            touched = max([f.stat().st_mtime] + ([p.stat().st_mtime for p in f.rglob("*")] if f.is_dir() else []))
            if now - touched < STALE_TEMP_S:
                continue
            if f.is_dir():
                shutil.rmtree(str(f))
            else:
                f.unlink()
            removed += 1
        except OSError:
            continue  # in use after all, or gone meanwhile: the next sweep
    return removed


def sweep_stale_temp(now: float | None = None) -> int:
    """Remove what snapshots a killed process never finished left behind —
    the work files (``_is_temp``) older than STALE_TEMP_S in every
    workspace's snapshot folder, ``backups/deleted/`` and ``backups/`` (the
    server backups' work folders); a younger one may be a snapshot running
    now. Returns how many went. The app runs it at startup and every
    STALE_TEMP_S; ``create`` sweeps its own folder first."""
    now = datetime.now(timezone.utc).timestamp() if now is None else now
    dirs = [config.BACKUPS_DIR, deleted_dir()]
    root = config.BACKUPS_DIR / "workspaces"
    if root.is_dir():
        dirs += [d for d in root.iterdir() if d.is_dir()]
    removed = sum(_sweep_temp(d, now) for d in dirs)
    if removed:
        log.info(f"[backups] removed {removed} temp file(s) of snapshots that never finished")
    return removed


def create(ws: str, *, label: str = "manual", uploads: bool = True, by: str = "", scheduled: bool = False,
           task_id: str = "", auto: bool = False, progress=None) -> dict:
    """Take a snapshot: ``scheduled`` for a backup task's run, ``auto`` for
    the one a replace restore keeps, else a manual one. One at a time per
    workspace; written under a unique temporary name and renamed when
    complete. ``progress``: see ``write_zip``. Raises BackupError on a bad
    label, a full store (MAX_PER_WORKSPACE manual,
    MAX_SCHEDULED_PER_WORKSPACE scheduled snapshots) or a disk with less
    than MIN_FREE_BYTES free."""
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
        _sweep_temp(d, datetime.now(timezone.utc).timestamp())  # what a snapshot killed half way left
        check_free_space(d)
        name = _free_name(d, label)
        dest = d / f"{name}.zip"
        tmp = d / f".{name}.{secrets.token_hex(4)}.part"
        try:
            write_zip(ws, tmp, uploads=uploads, by=by, label=label, scheduled=scheduled, task_id=task_id,
                      auto=auto, progress=progress)
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
