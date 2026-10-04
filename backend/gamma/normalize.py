"""Normalization of old data shapes inside a workspace's databases.

A backup restored through ``/api/import-data`` can be as old as the first
release, and one taken before the workspace stamps (``pages.db``'s
``user_version``, gamma/migrations.py) says nothing of its age. The
block-content shapes below were changed without ever keeping a read-side
shim. So the same idempotent pass runs in two places —
the ``baseline`` migration step (every existing library, once) and the
backup restore (each imported file). Every step SQL-filters (``LIKE``) for
the old shape first, so a clean database costs one query per step and
touches no row; ``updated_at`` moves only on rows actually rewritten.

Adding a step here means adding it to BOTH callers implicitly — that is the
point. Steps are never removed while ``/api/import-data`` accepts backups
that may still carry the shape.

The table shapes are the migration steps' business, except where a restored
backup needs them as well: ``block_columns`` (step 26), ``page_changes``
(step 27), ``pages_db_chats`` and ``block_fts`` (step 28),
``folder_blocks`` (step 29), ``highlight_shape`` (step 30) and
``page_ops_batch_id`` (step 33) run in their step and on restore (those
before the schema statements on a copy stamped below the base), in the
order ``ws_backup._normalize_copies`` gives (docs/dev/migrations.md
"Writing a step"). From step 34 on a step's workspace part is its own
restore normalizer (migrations.WORKSPACE_STEPS). Nothing adds a column or
fills a table on connect. The pages.db connections given to these steps
have ``db.register_functions``.
"""

import base64
import hashlib
import json
import re
import sqlite3
import uuid
from contextlib import contextmanager
from pathlib import Path

from fractional_indexing import generate_n_keys_between

from .block_index import rebuild
from .blocks_store import FOLDERS, LABELS, STORED_COLUMNS, TRASH, TREES, ensure_reserved, touch_page
from .db import BLOCK_HOT_COLUMNS, BLOCK_HOT_INDEXES, CHATS_SCHEMA, PAGES_SCHEMA, page_now
from .highlights import from_scaled
from .note_markup import LEGACY_WIDTH_RE, obsidian_image_sizes
from .storage import display_filename, pdf_url
from .text_box import is_text_box

# The automatic title prefix Gamma used to give uploaded PDFs. Migrated pages
# get the bare name plus an ``auto_title`` marker, so the metadata worker may
# still replace the title exactly as for new pages.
PDF_NOTES_PREFIX = "PDF Notes - "

PAGES_STEPS = ("source_url_key", "image_width", "pdf_notes_title", "upload_path_titles")

# Tables an older data.db may hold that data.db has none of now:
# `annotations` and a per-user `shares` (long since unified_blocks and the
# global shares table), `prefs` (users.db user_prefs since migration step 2;
# a restored backup's copy is meaningless in another workspace) and the
# notes index with its bookkeeping (pages.db since step 28, kept by
# triggers). Dropping an FTS5 table drops its shadow tables with it.
LEGACY_DATA_TABLES = ("annotations", "shares", "prefs", "block_fts", "block_fts_meta", "block_fts_rows")


@contextmanager
def _transaction(conn: sqlite3.Connection):
    """One transaction on ``conn``, committed when the block ends and rolled
    back when it raises (inside one the caller has open already, that one)."""
    if not conn.in_transaction:
        conn.execute("BEGIN")
    try:
        yield
        conn.commit()
    except BaseException:
        conn.rollback()
        raise


def _load_props(raw) -> dict | None:
    try:
        props = json.loads(raw or "{}")
    except (TypeError, ValueError):
        return None
    return props if isinstance(props, dict) else None


# Every row's page as its parent links say, for the rows of ``unified_blocks``
# a walk down from the reserved parentless rows reaches: '' on those, its own
# id on a block right under one (a page, in the library or the trash) —
# but the pseudo-page's id on a folder or label block —, its parent's page
# below.
_PAGE_WALK = f"""WITH RECURSIVE walk(id, page) AS (
        SELECT id, '' FROM unified_blocks WHERE parent_id IS NULL
        UNION ALL
        SELECT b.id, CASE WHEN w.page != '' THEN w.page WHEN w.id IN {TREES} THEN w.id ELSE b.id END
        FROM unified_blocks b JOIN walk w ON b.parent_id = w.id)
    SELECT id, page FROM walk"""


def block_columns(conn: sqlite3.Connection) -> bool:
    """Give a pages.db from before migration step 26 the block table's typed
    hot fields (db.BLOCK_HOT_COLUMNS) and their indexes, in one transaction
    committed here: ``page_id`` is filled in by the parent walk (a row no
    walk reaches, its parent gone, keeps ''). A generated column of an
    older definition (``kind`` before step 29's ``folder`` / ``label``
    cases) is dropped with its index and added again, and so is every
    generated column after it, so the columns keep a fresh table's order. A
    file that has them as they are is left as it is. Returns whether it
    changed the file."""
    have = {r[1] for r in conn.execute("PRAGMA table_xinfo(unified_blocks)")}
    table_sql = conn.execute("SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'unified_blocks'").fetchone()[0]
    generated = [name for name, ddl in BLOCK_HOT_COLUMNS.items() if "GENERATED" in ddl]
    old = [i for i, name in enumerate(generated) if name in have and BLOCK_HOT_COLUMNS[name] not in table_sql]
    stale = [name for name in generated[old[0]:] if name in have] if old else []
    missing = [name for name in BLOCK_HOT_COLUMNS if name not in have or name in stale]
    if not missing:
        return False
    with _transaction(conn):
        for name in stale:
            for (index,) in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'index' AND tbl_name = 'unified_blocks' "
                    "AND sql LIKE ?", (f"%({name})%",)).fetchall():
                conn.execute(f"DROP INDEX {index}")
            conn.execute(f"ALTER TABLE unified_blocks DROP COLUMN {name}")
        for name in missing:
            conn.execute(f"ALTER TABLE unified_blocks ADD COLUMN {BLOCK_HOT_COLUMNS[name]}")
        if "page_id" in missing:
            conn.execute(f"UPDATE unified_blocks SET page_id = walk.page FROM ({_PAGE_WALK}) AS walk "
                         "WHERE unified_blocks.id = walk.id")
        for stmt in BLOCK_HOT_INDEXES:
            conn.execute(stmt)
    return True


def page_changes(conn: sqlite3.Connection) -> bool:
    """Give a pages.db from before migration step 27 the workspace change
    log (``page_changes``) in place of its ``deleted_pages`` tombstones, in
    one transaction committed here. Every page gets its row, seqs counting
    up in the order the pages were last written (``updated_at``, then the
    id): ``live`` for a page of the library, ``deleted`` for one in
    Recently deleted (when and by whom: its tombstone's, else its trash
    stamps); then each tombstone of a page that is gone, in the order they
    went. The tombstone table is dropped. A file that has the log and no
    tombstones is left as it is; one that has both gains only the rows it
    lacks. Returns whether it changed the file."""
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    if "page_changes" in tables and "deleted_pages" not in tables:
        return False
    tombs = "deleted_pages" if "deleted_pages" in tables else \
        "(SELECT '' AS page_id, '' AS deleted_at, '' AS actor WHERE 0)"  # a backup older than tombstones
    with _transaction(conn):
        conn.execute(next(s for s in PAGES_SCHEMA if s.startswith("CREATE TABLE IF NOT EXISTS page_changes (")))
        last = conn.execute("SELECT COALESCE(MAX(seq), 0) FROM page_changes").fetchone()[0]
        conn.execute(
            "INSERT INTO page_changes (page_id, seq, kind, at, actor) "
            "SELECT b.id, ? + ROW_NUMBER() OVER (ORDER BY b.updated_at, b.id), "
            "CASE b.parent_id WHEN 'root' THEN 'live' ELSE 'deleted' END, "
            "CASE b.parent_id WHEN 'root' THEN b.updated_at "
            "ELSE COALESCE(t.deleted_at, json_extract(b.properties, '$.deleted_at'), b.updated_at) END, "
            "CASE b.parent_id WHEN 'root' THEN '' "
            "ELSE COALESCE(t.actor, json_extract(b.properties, '$.deleted_by'), '') END "
            f"FROM unified_blocks b LEFT JOIN {tombs} t ON t.page_id = b.id "
            "WHERE b.parent_id IN ('root', 'trash') AND b.id NOT IN (SELECT page_id FROM page_changes)", (last,))
        if "deleted_pages" in tables:
            last = conn.execute("SELECT COALESCE(MAX(seq), 0) FROM page_changes").fetchone()[0]
            conn.execute(
                "INSERT INTO page_changes (page_id, seq, kind, at, actor) "
                "SELECT page_id, ? + ROW_NUMBER() OVER (ORDER BY deleted_at, page_id), 'deleted', deleted_at, actor "
                "FROM deleted_pages WHERE page_id NOT IN (SELECT page_id FROM page_changes)", (last,))
            conn.execute("DROP TABLE deleted_pages")
    return True


def page_ops_batch_id(conn: sqlite3.Connection) -> bool:
    """Give a pages.db from before migration step 33 the op log's batch
    columns, in one transaction committed here: ``batch_id`` and ``cursor``
    ('' on the rows it holds, none of which a retry is answered from) and
    the unique index on page, client and batch id (db.PAGES_SCHEMA's
    ``idx_page_ops_batch``). A file without an op log (a backup older than
    it) gets the whole table from the schema statements; one that has them
    is left as it is. Returns whether it changed the file."""
    have = {r[1] for r in conn.execute("PRAGMA table_info(page_ops)")}
    indexed = conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'index' AND name = 'idx_page_ops_batch'").fetchone()
    if not have or ({"batch_id", "cursor"} <= have and indexed):
        return False
    with _transaction(conn):
        for name in ("batch_id", "cursor"):
            if name not in have:
                conn.execute(f"ALTER TABLE page_ops ADD COLUMN {name} TEXT NOT NULL DEFAULT ''")
        conn.execute(next(s for s in PAGES_SCHEMA if s.startswith("CREATE UNIQUE INDEX IF NOT EXISTS idx_page_ops_batch ")))
    return True


def _column_or(conn: sqlite3.Connection, schema: str, table: str, column: str, default: str) -> str:
    """``column`` when ``schema.table`` has it, else the SQL ``default``."""
    have = {r[1] for r in conn.execute(f"PRAGMA {schema}.table_info({table})")}
    return column if column in have else default


def pages_db_chats(conn: sqlite3.Connection, data_db: Path) -> bool:
    """Give a pages.db from before migration step 28 the AI chats its
    workspace's ``data_db`` held: ``chats`` (its ``block_id`` is the
    ``bucket``; a title of '' where the row has none) and ``chat_history``
    are copied in, in one transaction committed here, without the rows the
    pages.db has already (a step cut short), and dropped from data.db once
    the copy is committed. A data.db that has neither (none, or moved
    already) is left as it is. Returns whether it changed a file. Takes a
    connection outside a transaction (it attaches data.db)."""
    if not data_db.is_file():
        return False
    conn.execute("ATTACH DATABASE ? AS old", (str(data_db),))
    try:
        moving = [r[0] for r in conn.execute(
            "SELECT name FROM old.sqlite_master WHERE type = 'table' AND name IN ('chats', 'chat_history')")]
        if not moving:
            return False
        conn.execute("BEGIN")
        try:
            for stmt in CHATS_SCHEMA:
                conn.execute(stmt)
            if "chats" in moving:
                title = _column_or(conn, "old", "chats", "title", "''")
                conn.execute("INSERT OR IGNORE INTO main.chats (bucket, messages, updated_at, title) "
                             f"SELECT block_id, messages, updated_at, {title} FROM old.chats")
            if "chat_history" in moving:
                title = _column_or(conn, "old", "chat_history", "title", "''")
                conn.execute("INSERT OR IGNORE INTO main.chat_history (id, bucket, title, messages, created_at, "
                             f"updated_at) SELECT id, bucket, {title}, messages, created_at, updated_at "
                             "FROM old.chat_history")
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
        for table in moving:
            conn.execute(f"DROP TABLE old.{table}")
        conn.commit()
        return True
    finally:
        conn.execute("DETACH DATABASE old")


def block_fts(conn: sqlite3.Connection) -> None:
    """Give a pages.db its notes index (the view, the FTS5 table, the
    triggers) and build it from the block rows (``rebuild``), in one
    transaction committed here — migration step 28 and every restore: a
    backup may predate the index, or carry one built under other textnorm
    rules, and the triggers' deletes must find in it what the current rules
    make of each row."""
    with _transaction(conn):
        rebuild(conn)


def tree_block_id(tree: str, names) -> str:
    """The id step 29 gives the folder at the path ``names`` (``tree``
    ``FOLDERS``) or the label ``names[0]`` (``LABELS``): derived from the
    names, so two copies of a workspace converted apart (a mirror and its
    remote, a backup and its library) name the same folder alike."""
    digest = hashlib.sha256("\0".join((tree, *names)).encode("utf-8")).digest()
    return base64.urlsafe_b64encode(digest).decode("ascii")[:12]


def _old_tags(raw) -> list[str]:
    """A ``properties.folder`` / ``category`` value before step 29: a comma
    list, each entry's whitespace runs made one space."""
    return [" ".join(t.split()) for t in str(raw or "").split(",") if t.strip()]


def _old_path(tag: str) -> tuple:
    """A folder tag before step 29 (``a/b``) as its names."""
    return tuple(" ".join(s.split()) for s in tag.split("/") if s.strip())


def folder_blocks(conn: sqlite3.Connection) -> bool:
    """Give a pages.db from before migration step 29 its folder and label
    trees, in one transaction committed here: the reserved rows ``folders``
    and ``labels``; a folder block for every path the pages
    (``properties.folder``, comma-separated ``/`` paths) and the folder
    chats (``home:<path>`` buckets) name, nested by segment, new siblings
    ordered by name; a label block for every name of ``properties.category``;
    each page's filing rewritten to ``properties.folders`` / ``labels`` (ids,
    in the old order) without stamping it — refiling is no edit; each folder
    chat moved to its folder's id (``home:`` alone to ``home``; a
    conversation whose bucket is taken goes into that bucket's history).
    Ids are ``tree_block_id``'s, a folder that has one already is reused.
    The pseudo-pages get their change-log row, so a mirror pulls their
    trees. A file with nothing of the old shape gains only the reserved rows
    it lacks. Returns whether it changed the file."""
    pages = [(page_id, props) for page_id, props in (
        (r[0], _load_props(r[1])) for r in conn.execute(
            "SELECT id, properties FROM unified_blocks WHERE parent_id IN ('root', 'trash') "
            "AND (properties LIKE '%\"folder\"%' OR properties LIKE '%\"category\"%')"))
        if props is not None and ("folder" in props or "category" in props)]
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    chats = {bucket for table in ("chats", "chat_history") if table in tables  # (a file that never had chats)
             for (bucket,) in conn.execute(f"SELECT DISTINCT bucket FROM {table} WHERE bucket LIKE 'home:%'")}
    reserved = [t for t in (TRASH, *TREES)
                if not conn.execute("SELECT 1 FROM unified_blocks WHERE id = ?", (t,)).fetchone()]
    if not pages and not chats and not reserved:
        return False
    with _transaction(conn):
        for tree in reserved:
            ensure_reserved(conn, tree)
        filed = {}
        for page_id, props in pages:
            filed[page_id] = (props, [p for p in map(_old_path, _old_tags(props.pop("folder", None))) if p],
                              _old_tags(props.pop("category", None)))
        buckets = {b: _old_path(b[len("home:"):]) for b in chats}
        folder_ids = _make_tree(conn, FOLDERS, [p for _, paths, _ in filed.values() for p in paths]
                                + [p for p in buckets.values() if p])
        label_ids = _make_tree(conn, LABELS, [(n,) for _, _, names in filed.values() for n in names])
        for page_id, (props, paths, names) in filed.items():
            for tree, ids in ((FOLDERS, [folder_ids[p] for p in paths]), (LABELS, [label_ids[(n,)] for n in names])):
                ids = list(dict.fromkeys([*(props.get(tree) or []), *ids]))
                if ids:
                    props[tree] = ids
            conn.execute("UPDATE unified_blocks SET properties = ? WHERE id = ?", (json.dumps(props), page_id))
        for old, path in buckets.items():
            _move_bucket(conn, old, folder_ids[path] if path else "home")
        for tree in TREES:
            if not conn.execute("SELECT 1 FROM page_changes WHERE page_id = ?", (tree,)).fetchone():
                touch_page(conn, tree, "")
    return True


def _make_tree(conn: sqlite3.Connection, tree: str, paths) -> dict:
    """Write the blocks of ``tree`` that ``paths`` (tuples of names) need
    under ids ``tree_block_id`` derives, keeping the ones there already;
    new siblings go after the existing ones, ordered by name. Returns
    ``{path: id}``."""
    wanted = sorted(set(paths), key=lambda p: [n.casefold() for n in p] + [""] + list(p))
    ids, new = {}, {}
    for path in wanted:
        for depth in range(1, len(path) + 1):
            part = path[:depth]
            if part in ids:
                continue
            ids[part] = tree_block_id(tree, part)
            parent = ids[part[:-1]] if depth > 1 else tree
            if not conn.execute("SELECT 1 FROM unified_blocks WHERE id = ?", (ids[part],)).fetchone():
                new.setdefault(parent, []).append(part)
    now = page_now()
    for parent, parts in new.items():
        last = conn.execute("SELECT MAX(position) FROM unified_blocks WHERE parent_id = ?", (parent,)).fetchone()[0]
        for part, key in zip(parts, generate_n_keys_between(last, None, n=len(parts))):
            conn.execute(f"INSERT INTO unified_blocks ({STORED_COLUMNS}) VALUES (?, ?, ?, ?, '{{}}', ?, ?, ?)",
                         (ids[part], parent, key, part[-1], now, now, tree))
    return ids


def _move_bucket(conn: sqlite3.Connection, old: str, new: str) -> None:
    """A chat bucket renamed: its history follows, and its active
    conversation too — into ``new``'s history when ``new`` has one."""
    conn.execute("UPDATE chat_history SET bucket = ? WHERE bucket = ?", (new, old))
    row = conn.execute("SELECT messages, updated_at, title FROM chats WHERE bucket = ?", (old,)).fetchone()
    if not row:
        return
    if conn.execute("SELECT 1 FROM chats WHERE bucket = ?", (new,)).fetchone():
        conn.execute("INSERT INTO chat_history (id, bucket, title, messages, created_at, updated_at) "
                     "VALUES (?, ?, ?, ?, ?, ?)", (uuid.uuid4().hex, new, row[2], row[0], row[1], row[1]))
        conn.execute("DELETE FROM chats WHERE bucket = ?", (old,))
    else:
        conn.execute("UPDATE chats SET bucket = ? WHERE bucket = ?", (new, old))


# The rows step 30 may rewrite: a highlight's identity or page of before
# it, a position, a reference to a highlight, or a page's PDF URL.
_SHAPE_FILTER = ("properties LIKE '%\"highlight_id\"%' OR properties LIKE '%\"pdf_page\"%' "
                 "OR properties LIKE '%\"pdf_position\"%' OR properties LIKE '%\"link_highlight_id\"%' "
                 "OR properties LIKE '%\"linked_highlight_id\"%' "
                 "OR (doc_id IS NOT NULL AND properties LIKE '%\"source_url\"%')")


def highlight_shape(conn: sqlite3.Connection) -> bool:
    """Give a pages.db from before migration step 30 the highlight shape
    (gamma/highlights.py), in one transaction committed here, stamping
    nothing — a shape is no edit. Every ``pdf_position`` takes the stored
    shape (``highlights.from_scaled``: the page size once, no
    ``pageNumber`` / ``width`` / ``height`` per rect), and one with no page
    goes; a highlight (a ``highlight_id``) with no position but a
    ``pdf_page`` gets its page alone as one. ``highlight_id`` goes, and
    ``pdf_page`` everywhere but on a text box (its only page). A link
    region's ``link_highlight_id`` becomes ``link_block_id``, the block that
    had that highlight id on the page it links to, and a note's
    ``linked_highlight_id`` names the block that had it on the note's own
    page (a block of that id there stays named); one no block there had is
    dropped. A ``source_url`` that is the
    row's ``doc_id``'s stored copy goes (blocks_store.page_attachment
    derives it). What a row becomes depends on the file's rows alone, so
    two copies of a page upgraded apart agree. A file with nothing of the
    old shape is left as it is. Returns whether it changed the file."""
    rows = [(block_id, page_id, doc_id, props) for block_id, page_id, doc_id, props in (
        (r[0], r[1], r[2], _load_props(r[3])) for r in conn.execute(
            f"SELECT id, page_id, doc_id, properties FROM unified_blocks WHERE {_SHAPE_FILTER} ORDER BY id"))
        if props is not None]
    # (page, highlight id) → its block: the block of that id if there is
    # one, else the first by id.
    blocks: dict = {}
    for block_id, page_id, _doc, props in rows:
        old = props.get("highlight_id")
        if isinstance(old, str) and old and (block_id == old or (page_id, old) not in blocks):
            blocks[(page_id, old)] = block_id
    linked = [p["linked_highlight_id"] for *_, p in rows if "linked_highlight_id" in p]
    placed = {(r[1], r[0]) for r in conn.execute(
        "SELECT id, page_id FROM unified_blocks WHERE id IN (SELECT value FROM json_each(?))", (json.dumps(linked),))}
    changed = []
    for block_id, page_id, doc_id, props in rows:
        new = dict(props)
        old_page = props.get("pdf_page")
        if not is_text_box(props):
            new.pop("pdf_page", None)
        was_highlight = new.pop("highlight_id", None)
        if isinstance(new.get("pdf_position"), dict):
            position = from_scaled(new["pdf_position"], old_page)
            if position is None:
                del new["pdf_position"]
            else:
                new["pdf_position"] = position
        elif was_highlight and isinstance(old_page, int) and not isinstance(old_page, bool) and old_page > 0:
            new["pdf_position"] = {"pageNumber": old_page}
        if "link_highlight_id" in new:
            target = blocks.get((new.get("link_page_id") or "", new.pop("link_highlight_id")))
            if target:
                new["link_block_id"] = target
        if "linked_highlight_id" in new:
            old = new.pop("linked_highlight_id")
            target = blocks.get((page_id, old)) or (old if (page_id, old) in placed else None)
            if target:
                new["linked_highlight_id"] = target
        if doc_id and new.get("source_url") == pdf_url(doc_id):
            del new["source_url"]
        if new != props:
            changed.append((json.dumps(new), block_id))
    if not changed:
        return False
    with _transaction(conn):
        conn.executemany("UPDATE unified_blocks SET properties = ? WHERE id = ?", changed)
    return True


def normalize_pages_db(conn: sqlite3.Connection) -> dict:
    """Normalize one pages.db. Returns ``{step: rows changed}``."""
    counts = dict.fromkeys(PAGES_STEPS, 0)
    now = page_now()

    # (1) properties.sourceUrl (camelCase, the earliest pages) → source_url.
    for block_id, raw in conn.execute(
            "SELECT id, properties FROM unified_blocks WHERE properties LIKE '%\"sourceUrl\"%'"
    ).fetchall():
        props = _load_props(raw)
        if props is None or "sourceUrl" not in props:
            continue
        old = props.pop("sourceUrl")
        if old and not props.get("source_url"):
            props["source_url"] = old
        conn.execute("UPDATE unified_blocks SET properties = ?, updated_at = ? WHERE id = ?",
                     (json.dumps(props), now, block_id))
        counts["source_url_key"] += 1

    # (2) Legacy Logseq image size ``![a](u){:width N}`` → Obsidian ``![a|N](u)``.
    for block_id, content in conn.execute(
            "SELECT id, content FROM unified_blocks WHERE content LIKE '%{:width%'"
    ).fetchall():
        if not LEGACY_WIDTH_RE.search(content or ""):
            continue
        conn.execute("UPDATE unified_blocks SET content = ?, updated_at = ? WHERE id = ?",
                     (obsidian_image_sizes(content), now, block_id))
        counts["image_width"] += 1

    # (3) Pages still titled "PDF Notes - <name>" without an auto_title marker.
    for block_id, content, raw in conn.execute(
            "SELECT id, content, properties FROM unified_blocks "
            "WHERE parent_id = 'root' AND content LIKE ?", (PDF_NOTES_PREFIX + "%",)
    ).fetchall():
        content = content or ""
        if not content.startswith(PDF_NOTES_PREFIX):  # LIKE is case-insensitive
            continue
        props = _load_props(raw)
        if props is None or props.get("auto_title"):
            continue
        title = content[len(PDF_NOTES_PREFIX):].strip() or "Untitled"
        props["auto_title"] = title
        conn.execute(
            "UPDATE unified_blocks SET content = ?, properties = ?, updated_at = ? WHERE id = ?",
            (title, json.dumps(props), now, block_id))
        counts["pdf_notes_title"] += 1

    # (4) Pages whose original_filename kept a directory path (a browser
    # once leaked a relative path into the file name): the marker becomes
    # the leaf, and the title follows only while it still equals the
    # generated one (a user-renamed page cannot match).
    for block_id, content, raw in conn.execute(
            "SELECT id, content, properties FROM unified_blocks WHERE parent_id = 'root' "
            "AND properties LIKE '%\"original_filename\"%' "
            "AND (properties LIKE '%/%' OR properties LIKE '%\\%')"
    ).fetchall():
        props = _load_props(raw)
        if props is None:
            continue
        original = str(props.get("original_filename") or "").replace("\\", "/").strip()
        leaf = display_filename(original)
        if not leaf or leaf == original:
            continue
        next_content = content
        if props.get("markdown_import"):
            old_stem = re.sub(r"\.(?:md|markdown)$", "", original, flags=re.I)
            if content == old_stem:
                next_content = re.sub(r"\.(?:md|markdown)$", "", leaf, flags=re.I)
        elif props.get("auto_title") == content and display_filename(content) == leaf:
            next_content = leaf
            props["auto_title"] = leaf
        props["original_filename"] = leaf
        conn.execute(
            "UPDATE unified_blocks SET content = ?, properties = ?, updated_at = ? WHERE id = ?",
            (next_content, json.dumps(props), now, block_id))
        counts["upload_path_titles"] += 1

    conn.commit()
    return counts


def normalize_data_db(conn: sqlite3.Connection, keep_prefs: bool = False) -> dict:
    """Drop the legacy tables. ``keep_prefs`` leaves the old ``prefs`` table
    for a caller that still has to read it (migration step 2). The chats an
    older data.db holds are ``pages_db_chats``' to move. Returns
    ``{"dropped_tables": n}``."""
    tables = tuple(t for t in LEGACY_DATA_TABLES if not (keep_prefs and t == "prefs"))
    placeholders = ",".join("?" * len(tables))
    existing = {r[0] for r in conn.execute(
        f"SELECT name FROM sqlite_master WHERE type = 'table' AND name IN ({placeholders})", tables)}
    for table in existing:
        conn.execute(f"DROP TABLE IF EXISTS {table}")
    conn.commit()
    return {"dropped_tables": len(existing)}
