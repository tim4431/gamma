"""A work folder of many files, seeded as its vault would leave the rows
(docs/dev/work_folders.md "The readers, classified"): the perf fixture of
tests/test_work_folder_scale.py and the browser suite's 20,000 files
(frontend/tests/e2e), where a vault's real round would hash every file and
the API would take a request each.

The rows are inserted directly: the directories below W, then the files,
every third a note (``file-NNNNN.md``) and the others listed binaries
(``file-NNNNN.bin``, ``at: "listed"``, no hash: no indexer and no reader of
bytes ever reaches them), each with its row of the change log.

As a script, from backend/ with GAMMA_DATA_DIR set (it imports ``gamma``,
as manage.py does)::

    python tests/seed_work_folder.py <workspace id> <W's name> <files> [--big N] [--dirs N]

makes a top folder of that name, marks it a work folder's root as its vault
does (``DISK_CLIENT``), seeds it and prints ``{"w", "dirs", "files"}`` as
JSON: ``big`` files in the directory ``big``, the rest spread over ``dirs``
directories ``dir 0`` … ``dir N-1``."""

import argparse
import json
import secrets
import sys
from pathlib import Path

if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fractional_indexing import generate_n_keys_between  # noqa: E402

from gamma.blocks_store import BLOCK_COLUMNS, FOLDERS, last_child_position, touch_page  # noqa: E402
from gamma.documents.model import document  # noqa: E402


def add_folders(conn, parent: str, names: list[str], now: str) -> list[str]:
    """Folder rows below ``parent``, inserted directly; their ids."""
    ids = [secrets.token_urlsafe(9) for _ in names]
    keys = generate_n_keys_between(last_child_position(conn, parent), None, n=len(names))
    conn.executemany(f"INSERT INTO unified_blocks ({BLOCK_COLUMNS}) VALUES (?, ?, ?, ?, '{{}}', ?, ?, ?)",
                     [(i, parent, key, name, now, now, FOLDERS) for i, name, key in zip(ids, names, keys)])
    return ids


def add_files(conn, w: str, directory: str, count: int, now: str, start: int) -> None:
    """``count`` files of the work folder ``w`` filed in ``directory``,
    numbered from ``start``, as their vault stamps them (every third a
    note, the others listed binaries of ``1000 + n`` bytes), each with its
    row of the change log."""
    rows, seq = [], conn.execute("SELECT COALESCE(MAX(seq), 0) FROM page_changes").fetchone()[0]
    keys = generate_n_keys_between(last_child_position(conn, "root"), None, n=count) if count else []
    for n, key in zip(range(start, start + count), keys):
        page_id, leaf = secrets.token_urlsafe(9), f"file-{n:05d}"
        props = {"work_folder": w, "folders": [directory]}
        if n % 3:
            name = f"{leaf}.bin"
            props |= {"file_name": name,
                      "doc": document(name=name, at="listed", format="binary", ext=".bin", size=1000 + n)}
        else:
            props["file_name"] = f"{leaf}.md"
        rows.append((page_id, "root", key, leaf, json.dumps(props), now, now, page_id))
    conn.executemany(f"INSERT INTO unified_blocks ({BLOCK_COLUMNS}) VALUES (?, ?, ?, ?, ?, ?, ?, ?)", rows)
    conn.executemany("INSERT INTO page_changes (page_id, seq, kind, at, actor) VALUES (?, ?, 'live', ?, '')",
                     [(r[0], seq + k, now) for k, r in enumerate(rows, 1)])


def seed(conn, w: str, *, files: int, big: int, dirs: int, now: str) -> list[str]:
    """``files`` files below the work folder ``w``: ``big`` of them in a
    directory ``big`` (none without), the rest spread evenly over ``dirs``
    directories ``dir 0`` …, numbered on from the big one's. The
    directories' ids, ``big``'s first. The caller commits and touches the
    folder tree."""
    names = (["big"] if big else []) + [f"dir {n}" for n in range(dirs)]
    rest, start = files - big, big
    if rest < 0 or (rest and dirs < 1):
        raise ValueError("big is at most the files, and the rest need a directory")
    ids = add_folders(conn, w, names, now)
    if big:
        add_files(conn, w, ids[0], big, now, 0)
    spread = ids[1:] if big else ids
    for n, directory in enumerate(spread):
        count = rest // len(spread) + (1 if n < rest % len(spread) else 0)
        add_files(conn, w, directory, count, now, start)
        start += count
    return ids


def main(argv=None) -> None:
    from gamma.auth import DISK_CLIENT
    from gamma.db import connect_pages_db, connect_users_db, page_now
    from gamma.ops import commit_ops

    parser = argparse.ArgumentParser(description="Seed a work folder of many files into a workspace.")
    parser.add_argument("workspace")
    parser.add_argument("name")
    parser.add_argument("files", type=int)
    parser.add_argument("--big", type=int, default=0)
    parser.add_argument("--dirs", type=int, default=10)
    args = parser.parse_args(argv)
    if args.big > args.files or (args.files > args.big and args.dirs < 1):
        parser.error("--big is at most the files, and the rest need --dirs of at least 1")
    with connect_users_db() as conn:
        row = conn.execute("SELECT created_by FROM workspaces WHERE id = ?", (args.workspace,)).fetchone()
    if row is None:
        parser.error(f"no workspace {args.workspace}")
    actor, w = row[0], secrets.token_urlsafe(9)
    commit_ops(args.workspace, FOLDERS, [{"op": "insert", "id": w, "parent": FOLDERS, "content": args.name}],
               actor=actor)
    commit_ops(args.workspace, FOLDERS, [{"op": "set", "id": w, "props": {"vault": {"pull": True, "library": False}}}],
               actor=actor, client=DISK_CLIENT)
    now = page_now()
    with connect_pages_db(args.workspace) as conn:
        dirs = seed(conn, w, files=args.files, big=args.big, dirs=args.dirs if args.files > args.big else 0, now=now)
        touch_page(conn, FOLDERS, actor, now=now)
        conn.commit()
    print(json.dumps({"w": w, "dirs": dirs, "files": args.files}))


if __name__ == "__main__":
    main()
