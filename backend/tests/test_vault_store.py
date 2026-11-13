"""gamma/vault/store.py: a vault's base in vaults.db, its journal, tombstones,
missing files, dropped blocks and history (docs/dev/synced_vaults.md "What
lands on disk", "History", "Rules that keep files safe")."""

import sqlite3

import pytest

from gamma.vault import store

LINK = "link-1"
NODES = [{"id": "b1", "g": "first"}, {"id": "b2", "g": "second", "m": {"x": "[[X]]"}}]


@pytest.fixture(autouse=True)
def own_vaults_db(data_dir):
    """Every test on a vaults.db of its own (conftest ``data_dir``)."""
    return data_dir / "vaults.db"


def base(path="a.md", **fields):
    store.put(LINK, path, **{"kind": "notes", "page_id": "p1", "hash": "h1", "size": 5, "mtime_ns": 1, "file_id": "9",
                             "read_at": 100.0, "text": "first\n\nsecond\n", "nodes": NODES, **fields})
    return store.get(LINK, path)


def test_a_base_reads_back_as_written(own_vaults_db):
    row = base(gone=[{"id": "x", "hash": "h", "anchor": "", "props": {}, "at": 1.0}], title="A", folder="f1")
    assert row["nodes"] == NODES and row["gone"][0]["id"] == "x" and row["pending"] is None
    assert (row["title"], row["folder"], row["was"], row["missing_since"], row["ours"]) == ("A", "f1", "", 0, 0)
    store.put(LINK, "a.md", size=6, problem="not written to disk: an open code block")
    row = store.get(LINK, "a.md")
    assert row["size"] == 6 and row["hash"] == "h1" and row["problem"].startswith("not written")
    assert list(store.files(LINK)) == ["a.md"] and store.files("another link") == {}
    with pytest.raises(ValueError):
        store.put(LINK, "a.md", nonsense=1)
    with pytest.raises(sqlite3.IntegrityError):
        store.put(LINK, "b.md", size=1)    # a new row needs its kind
    assert own_vaults_db.is_file()


# --- the journal ----------------------------------------------------------------

def test_a_new_files_pending_is_its_row_until_promoted(own_vaults_db):
    """A new page's id is minted once, kept in the journal
    before the page is made."""
    store.put_pending(LINK, "new.md", "notes", {"token": "t1", "hash": "h", "text": "x\n", "nodes": NODES,
                                                "page_id": "fresh"})
    row = store.get(LINK, "new.md")
    assert row["pending"]["page_id"] == "fresh" and row["page_id"] == "" and row["text"] is None
    assert [r["path"] for r in store.pending(LINK)] == ["new.md"]
    assert store.promote(LINK, "new.md", "other token") is None
    row = store.promote(LINK, "new.md", "t1", size=2, mtime_ns=7, read_at=3.0, seq=12)
    assert (row["page_id"], row["hash"], row["text"], row["nodes"], row["seq"], row["pending"]) == (
        "fresh", "h", "x\n", NODES, 12, None)
    assert store.pending(LINK) == []


def test_a_pending_gives_a_row_its_class_now(own_vaults_db):
    """A kept folder's picture row at a path a work folder reads as a file:
    the create's pending makes the row a file's, promoted so."""
    store.put(LINK, "img/pic.png", kind="picture", hash="abc.png", size=3, mtime_ns=1, file_id="9")
    store.put_pending(LINK, "img/pic.png", "file", {"op": "create", "token": "t1", "hash": "h" * 24, "page_id": "p1"})
    assert store.get(LINK, "img/pic.png")["kind"] == "file"
    store.promote(LINK, "img/pic.png", "t1", size=3, mtime_ns=1, file_id="9", read_at=1.0)
    row = store.get(LINK, "img/pic.png")
    assert row["kind"] == "file" and row["page_id"] == "p1" and row["hash"] == "h" * 24 and row["pending"] is None


def test_a_pending_dropped_leaves_the_base_or_no_row(own_vaults_db):
    """A kill between the effect and the base must leave the
    old base, at worst a duplicate, never a delete."""
    store.put_pending(LINK, "never.md", "notes", {"token": "t1", "page_id": "p9"})
    store.drop(LINK, "never.md", "t1")
    assert store.get(LINK, "never.md") is None
    before = base()
    store.put_pending(LINK, "a.md", "notes", {"token": "t2", "hash": "h2", "text": "changed\n", "nodes": []})
    store.drop(LINK, "a.md", "not mine")
    assert store.get(LINK, "a.md")["pending"]["token"] == "t2"
    store.drop(LINK, "a.md", "t2")
    assert store.get(LINK, "a.md") == before
    with pytest.raises(ValueError):
        store.put_pending(LINK, "a.md", "notes", {"hash": "no token"})


def test_a_pending_that_names_a_path_moves_the_row_and_its_history(own_vaults_db):
    base("Old title.md")
    store.keep(LINK, "Old title.md", b"what the file was", now=1000.0)
    store.put_pending(LINK, "Old title.md", "notes", {"token": "t", "path": "New title.md", "hash": "h3"})
    row = store.promote(LINK, "Old title.md", "t", read_at=5.0)
    assert row["path"] == "New title.md" and row["hash"] == "h3" and store.get(LINK, "Old title.md") is None
    assert [h["data"] for h in store.history(LINK, "New title.md")] == [b"what the file was"]


# --- tombstones and missing files ---------------------------------------------------------

def test_a_tombstone_keeps_its_page_while_a_new_file_takes_the_path(own_vaults_db):
    """A branch switched away and back finds its pages;
    the row is at the trash path, so a new file at the old one has a row of
    its own."""
    base("notes/a.md", hash="h1", file_id="41")
    store.bury(LINK, "notes/a.md", ".gamma/trash/20260101-000000/notes/a.md")
    assert store.files(LINK) == {}
    (stone,) = store.tombstones(LINK)
    assert (stone["path"], stone["was"], stone["page_id"]) == (".gamma/trash/20260101-000000/notes/a.md", "notes/a.md", "p1")
    base("notes/a.md", page_id="p2", hash="other", file_id="42")
    assert [t["page_id"] for t in store.tombstones(LINK)] == ["p1"]       # a live row is no tombstone
    store.forget(LINK, "notes/a.md")
    store.unbury(LINK, stone["path"], "notes/a.md")
    assert store.get(LINK, "notes/a.md")["page_id"] == "p1" and store.tombstones(LINK) == []


def test_a_file_is_removed_only_when_two_scans_far_apart_miss_it(own_vaults_db):
    """A safe save deletes and renames, and a 2 s poll can fall
    in between."""
    base("a.md")
    store.mark_missing(LINK, ["a.md"], now=1000.0)
    store.mark_missing(LINK, ["a.md"], now=1004.0)     # the first miss stands
    row = store.get(LINK, "a.md")
    assert row["missing_since"] == 1000.0
    assert not store.missing_long(row, now=1009.9) and store.missing_long(row, now=1010.0)
    store.found_again(LINK, ["a.md"])
    assert not store.missing_long(store.get(LINK, "a.md"), now=5000.0)


def test_a_dropped_block_stays_thirty_days():
    """A callout that no longer decodes, a note truncated,
    must not take a highlight's rectangles for good."""
    gone = store.with_gone([], [{"id": "h1", "hash": "x", "anchor": "abc", "props": {"pdf_position": {}}}], now=0.0)
    assert gone == [{"id": "h1", "hash": "x", "anchor": "abc", "props": {"pdf_position": {}}, "at": 0.0}]
    gone = store.with_gone(gone, [{"id": "h2", "hash": "y", "anchor": "", "props": {}}], now=86400.0)
    assert [e["id"] for e in store.kept_gone(gone, now=30 * 86400.0 - 1)] == ["h1", "h2"]
    assert [e["id"] for e in store.kept_gone(gone, now=30 * 86400.0)] == ["h2"]
    again = store.with_gone(gone, [{"id": "h1", "hash": "z", "anchor": "", "props": {}}], now=2 * 86400.0)
    assert [(e["id"], e["at"]) for e in again] == [("h2", 86400.0), ("h1", 2 * 86400.0)]


# --- directories -------------------------------------------------------------------------

def test_a_directory_moved_is_one_rewrite_of_everything_below_it(own_vaults_db):
    """``docs/`` renamed ``notes/`` is no 500 refilings."""
    for path in ("docs/a.md", "docs/sub/b.md", "docs2/c.md", "top.md"):
        base(path)
    store.put_dir(LINK, "docs", "f1", "100")
    store.put_dir(LINK, "docs/sub", "f2", "101")
    store.put_dir(LINK, "docs2", "f3", "102")
    store.keep(LINK, "docs/sub/b.md", b"old b", now=10.0)
    base("docs/gone.md")
    store.bury(LINK, "docs/gone.md", ".gamma/trash/x/docs/gone.md")
    assert store.repath(LINK, "docs", "notes") == 2
    assert sorted(store.files(LINK)) == ["docs2/c.md", "notes/a.md", "notes/sub/b.md", "top.md"]
    assert store.dirs(LINK) == {"notes": {"folder_id": "f1", "file_id": "100"},
                                "notes/sub": {"folder_id": "f2", "file_id": "101"},
                                "docs2": {"folder_id": "f3", "file_id": "102"}}
    assert store.history(LINK, "notes/sub/b.md")[0]["data"] == b"old b"
    assert store.tombstones(LINK)[0]["was"] == "notes/gone.md"
    assert store.repath(LINK, "top.md", "Top.md") == 1 and "Top.md" in store.files(LINK)
    store.forget_dir(LINK, "docs2")
    assert "docs2" not in store.dirs(LINK)


def test_two_names_swapped_are_one_transaction(own_vaults_db, monkeypatch):
    """A kill between the two passes of a swap left a
    row at ``\\x00moving/0`` that failed every round for good."""
    base("a.md", hash="ha")
    base("b.md", page_id="p2", hash="hb")
    calls = []
    real = store._repath

    def dying(conn, *args, **kw):
        calls.append(args)
        if len(calls) == 3:
            raise KeyboardInterrupt("killed")
        return real(conn, *args, **kw)
    monkeypatch.setattr(store, "_repath", dying)
    with pytest.raises(KeyboardInterrupt):
        store.repath_files(LINK, [("b.md", "a.md"), ("a.md", "b.md")], "t1")
    assert {p: r["hash"] for p, r in store.files(LINK).items()} == {"a.md": "ha", "b.md": "hb"}
    assert not store.pending(LINK)
    monkeypatch.setattr(store, "_repath", real)
    store.repath_files(LINK, [("b.md", "a.md"), ("a.md", "b.md")], "t2")
    assert {p: r["hash"] for p, r in store.files(LINK).items()} == {"a.md": "hb", "b.md": "ha"}
    assert {r["path"]: r["pending"] for r in store.pending(LINK)} == {"a.md": {"op": "moved", "token": "t2", "from": "b.md"},
                                                                      "b.md": {"op": "moved", "token": "t2", "from": "a.md"}}


def test_bytes_a_write_replaced_that_were_no_base_are_kept_within_the_five_minutes(own_vaults_db):
    """A person's save between pull's check and its
    rename was kept nowhere when Gamma had written that path minutes before."""
    assert store.keep(LINK, "a.md", b"version 1", now=1000.0)
    assert store.keep(LINK, "a.md", b"a person's save", now=1010.0, always=True)
    assert [h["data"] for h in store.history(LINK, "a.md")] == [b"a person's save", b"version 1"]


def test_a_move_onto_a_path_a_row_holds_changes_nothing(own_vaults_db):
    base("a.md")
    base("b.md", page_id="p2")
    with pytest.raises(sqlite3.IntegrityError):
        store.repath(LINK, "a.md", "b.md")
    assert store.get(LINK, "a.md")["page_id"] == "p1" and store.get(LINK, "b.md")["page_id"] == "p2"


# --- history ---------------------------------------------------------------------------

def test_history_is_one_entry_a_path_every_five_minutes(own_vaults_db):
    """A write every 2 s while someone types must not keep a
    whole file every 2 s for a week."""
    assert store.keep(LINK, "a.md", b"version 1", now=1000.0)
    assert not store.keep(LINK, "a.md", b"version 2", now=1000.0 + 299)
    assert store.keep(LINK, "b.md", b"another path", now=1001.0)
    assert store.keep(LINK, "a.md", b"version 3", now=1000.0 + 301)
    assert [h["data"] for h in store.history(LINK, "a.md")] == [b"version 3", b"version 1"]
    assert store.history(LINK, "a.md")[0]["at"].endswith("Z")


def test_history_goes_after_its_days_and_past_its_bytes(own_vaults_db, monkeypatch):
    store.keep(LINK, "old.md", b"a week ago", now=0.0)
    store.keep(LINK, "new.md", b"now", now=store.KEEP_HISTORY_DAYS * 86400.0 + 1)
    assert store.history(LINK, "old.md") == [] and store.history(LINK, "new.md")[0]["data"] == b"now"
    monkeypatch.setattr(store, "HISTORY_BYTES", 300)
    for k in range(6):
        store.keep(LINK, f"f{k}.md", bytes(range(256))[k:] * 1, now=10**6 + k)
    kept = [k for k in range(6) if store.history(LINK, f"f{k}.md")]
    assert kept and kept == list(range(6 - len(kept), 6))      # the oldest went first
    assert not store.keep(LINK, "huge.md", bytes(range(256)) * 20, now=10**7)


def test_forgetting_a_vault_leaves_nothing_of_it(own_vaults_db):
    base("a.md")
    store.put_dir(LINK, "d", "f")
    store.keep(LINK, "a.md", b"x", now=1.0)
    store.put("other", "a.md", kind="notes")
    store.forget_link(LINK)
    assert store.files(LINK) == {} and store.dirs(LINK) == {} and store.history(LINK, "a.md") == []
    assert list(store.files("other")) == ["a.md"]
