"""The Zotero side of a link's round (gamma/zotero_remote.py) against the
fake Zotero (zotero_fake.py): the delta listing (a 304, a library moving
mid-walk, hard deletes) and how it is queued, the queue's order and
backoff, the state row, reads by key and whole units, the write queue
(dependency order, 50 a request, every outcome class), PDF downloads into
a workspace (every reason one cannot come), uploads and note pictures, and
the error words a link's status uses. Workspaces are named ``zr…``."""

import hashlib
import json
import secrets
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException
from zotero_fake import FakeZotero
from zotero_fixtures import PICTURE, PICTURE_KEY, pdf_bytes, standard_library

from gamma import seed, storage, zotero_remote as zr, zotero_web
from gamma.db import connect_pages_db, page_now
from gamma.zotero_account import AccountError
from gamma.zotero_web import (WriteResult, ZoteroAuthError, ZoteroClient, ZoteroCompressed, ZoteroConflict,
                              ZoteroError, ZoteroNotFound, ZoteroQuota, ZoteroUnreachable, object_key)

LIB = "users/123"
NOW = "2026-10-28T12:00:00.000000Z"


class Stopped(BaseException):
    """What a stopped job's tick raises, as ``jobs.Cancelled`` does."""


@pytest.fixture
def fake(monkeypatch):
    return FakeZotero().install(monkeypatch)


@pytest.fixture
def zc(fake):
    return ZoteroClient(fake.api_key)


@pytest.fixture
def made(fake):
    return standard_library(fake)


@pytest.fixture
def ws():
    name = "zr" + secrets.token_hex(5)
    seed.create_workspace_files(name)
    return name


@pytest.fixture
def conn(ws):
    with connect_pages_db(ws) as c:
        yield c


def _calls(fake, method="GET", needle=""):
    return [path for m, path, _ in fake.calls if m == method and needle in path]


def _row(conn, key, version, kind="item", state="live"):
    conn.execute("INSERT INTO zotero_objects (key, kind, version, state) VALUES (?, ?, ?, ?)",
                 (key, kind, version, state))


def _partial_files(ws):
    folder = storage.partial_dir(ws)
    return [p.name for p in folder.iterdir()] if folder.is_dir() else []


def _after(stamp, seconds):
    base = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    return (base + timedelta(seconds=seconds)).strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z"


# --- the listing -------------------------------------------------------------------------

def test_a_first_listing_names_every_collection_top_item_and_child_trash_included(fake, zc, made):
    keys = made["keys"]
    changes = zr.list_changes(zc, LIB, 0)
    assert changes.version == fake.version(LIB)
    assert set(changes.collections) == {keys[n] for n in ("ml", "transformers", "efficient", "reading")}
    top = {keys[n] for n in ("attention", "ppo", "deep_learning", "thesis", "conference", "standalone_pdf",
                             "standalone_note", "trashed")}
    assert set(changes.top) == top  # the trashed article too
    children = {d["key"] for d in fake.items(LIB)} - top
    assert set(changes.children) == children and keys["image"] in children and keys["highlight"] in children
    assert changes.top[keys["attention"]] == fake.data(LIB, keys["attention"])["version"]
    assert not any(changes.deleted.values())
    assert not _calls(fake, needle="limit=1&format=versions")  # nothing to compare a first listing with


def test_nothing_changed_is_one_small_call_answered_304(fake, zc, made):
    fake.calls.clear()
    assert zr.list_changes(zc, LIB, fake.version(LIB)) is None
    assert len(fake.calls) == 1
    method, path, headers = fake.calls[0]
    assert "/items?" in path and "limit=1" in path and "format=versions" in path
    assert headers["if-modified-since-version"] == str(fake.version(LIB))


def test_a_later_listing_names_only_what_moved_trash_and_hard_deletes(fake, zc, made):
    keys = made["keys"]
    since = fake.version(LIB)
    fake.edit(LIB, keys["ppo"], title="PPO, revised")
    fake.trash(LIB, keys["note"])
    fake.trash(LIB, keys["reading"])
    fake.delete(LIB, keys["underline"])
    changes = zr.list_changes(zc, LIB, since)
    assert changes.version == fake.version(LIB)
    assert changes.top == {keys["ppo"]: fake.data(LIB, keys["ppo"])["version"]}
    assert changes.children == {keys["note"]: fake.data(LIB, keys["note"])["version"]}
    assert set(changes.collections) == {keys["reading"]}
    assert changes.deleted["items"] == [keys["underline"]]
    listings = [path for path in _calls(fake, needle="since=") if "/items" in path]
    assert len(listings) == 2 and all("includeTrashed=1" in path for path in listings)


def test_a_change_landing_mid_walk_is_listed_now_and_again_next_time(fake, zc, made):
    keys = made["keys"]
    since = fake.version(LIB)
    fake.edit(LIB, keys["thesis"], title="Bosonic codes, again")
    before_walk = fake.version(LIB)
    fake.on_request("/items/top", lambda: fake.edit(LIB, keys["ppo"], title="Moved meanwhile"))
    changes = zr.list_changes(zc, LIB, since)
    assert changes.version == before_walk  # the first answer's: the edit mid-walk is above it
    assert keys["ppo"] in changes.top and keys["thesis"] in changes.top
    again = zr.list_changes(zc, LIB, changes.version)
    assert again is not None and set(again.top) == {keys["ppo"]}


def test_an_item_made_between_the_two_item_listings_is_never_taken_for_a_child(fake, zc, made):
    since = fake.version(LIB)
    fake.edit(LIB, made["keys"]["thesis"], title="Something moved")
    added = []
    fake.on_request("/items/top", lambda: added.append(fake.add_item(LIB, {"itemType": "book", "title": "New"})))
    changes = zr.list_changes(zc, LIB, since)
    assert added and added[0] in changes.top and added[0] not in changes.children


def test_listed_keys_are_queued_newest_first_without_our_own_writes(fake, zc, made, conn):
    keys = made["keys"]
    changes = zr.list_changes(zc, LIB, 0)
    _row(conn, keys["ppo"], changes.top[keys["ppo"]])                  # our own write, as listed: skipped
    _row(conn, keys["thesis"], changes.top[keys["thesis"]] - 1)        # older: queued
    _row(conn, keys["highlight"], -1, "annotation", "pending")        # a create to confirm: queued
    deleted = changes._replace(deleted={"items": [keys["deep_learning"]]})
    queued = zr.queue_changes(conn, deleted)
    rows = {e.id: e for e in zr.take(conn, now=NOW, limit=1000)}
    assert queued == len(rows)
    assert keys["ppo"] not in rows and keys["deep_learning"] not in rows
    assert rows[keys["thesis"]].kind == "top" and rows[keys["highlight"]].kind == "child"
    assert rows[keys["ml"]].kind == "collection"
    order = [e.id for e in zr.take(conn, now=NOW, limit=1000)]
    priorities = [rows[i].priority for i in order]
    assert priorities == sorted(priorities, reverse=True)
    assert rows[keys["thesis"]].priority == changes.top[keys["thesis"]]


# --- the queue ---------------------------------------------------------------------------

def test_enqueue_keeps_the_newest_version_and_the_higher_priority(conn):
    zr.enqueue(conn, [{"id": "AAAAAAAA", "kind": "top", "version": 7}])
    zr.enqueue(conn, [{"id": "AAAAAAAA", "kind": "top", "version": 5, "priority": 100}])
    (entry,) = zr.take(conn, now=NOW, limit=5)
    assert (entry.version, entry.priority, entry.kind) == (7, 100, "top")
    zr.enqueue(conn, [{"id": "AAAAAAAA", "version": 9}])  # no kind: the one it had stays
    (entry,) = zr.take(conn, now=NOW, limit=5)
    assert (entry.version, entry.priority, entry.kind) == (9, 100, "top")
    with pytest.raises(ValueError):
        zr.enqueue(conn, [{"id": "BBBBBBBB", "kind": "nonsense"}])


def test_a_queued_childs_parent_is_kept_as_read_until_it_is_listed_again(conn):
    zr.enqueue(conn, [{"id": "ITEM2222", "kind": "top", "version": 3},
                      {"id": "PDF22222", "kind": "child", "version": 4},
                      {"id": "ANNO2222", "kind": "child", "version": 6},
                      {"id": "NOTE2222", "kind": "child", "version": 5}])
    assert zr.unread_children(conn, limit=10) == ["ANNO2222", "NOTE2222", "PDF22222"]
    assert zr.unread_children(conn, limit=10, exclude={"NOTE2222"}) == ["ANNO2222", "PDF22222"]
    # a parent read before a newer version was listed is not kept; a child read with none is not either
    assert zr.set_parents(conn, {"PDF22222": ("ITEM2222", 4), "ANNO2222": ("PDF22222", 5),
                                 "NOTE2222": ("", 5)}) == 1
    assert zr.set_parents(conn, {"ANNO2222": ("PDF22222", 6), "ITEM2222": ("X2222222", 3)}) == 1  # (no child)
    assert zr.children_of(conn, ["ITEM2222"]) == {"PDF22222": ("ITEM2222", 4)}
    assert zr.children_of(conn, ["PDF22222", "NOTE2222"]) == {"ANNO2222": ("PDF22222", 6)}
    assert zr.unread_children(conn, limit=10) == ["NOTE2222"]
    zr.enqueue(conn, [{"id": "ANNO2222", "kind": "child", "version": 6}])  # listed again as it was: kept
    assert zr.children_of(conn, ["PDF22222"]) == {"ANNO2222": ("PDF22222", 6)}
    zr.enqueue(conn, [{"id": "ANNO2222", "kind": "child", "version": 9}])  # changed (moved?): read again
    assert zr.children_of(conn, ["PDF22222"]) == {} and zr.unread_children(conn, limit=1) == ["ANNO2222"]


def test_pages_come_before_items_and_an_opened_page_before_everything(conn):
    zr.enqueue(conn, [{"id": "OLD22222", "kind": "top", "version": 3},
                      {"id": "NEW22222", "kind": "top", "version": 900},
                      zr.page_entry("p1"),
                      zr.file_entry("FILE2222", 40),
                      zr.file_entry("OPEN2222", 2, opened=True)])
    assert [e.id for e in zr.take(conn, now=NOW, limit=10)] == [
        "file:OPEN2222", "page:p1", "NEW22222", "file:FILE2222", "OLD22222"]
    assert [e.id for e in zr.take(conn, now=NOW, limit=10, kinds="file")] == ["file:OPEN2222", "file:FILE2222"]
    assert [e.id for e in zr.take(conn, now=NOW, limit=2, kinds=("top", "page"))] == ["page:p1", "NEW22222"]
    assert zr.size(conn) == 5 and zr.size(conn, kinds=("top",)) == 2
    # what a round passed over this time stays queued but is not taken again
    assert [e.id for e in zr.take(conn, now=NOW, limit=2, exclude={"file:OPEN2222", "page:p1"})] == [
        "NEW22222", "file:FILE2222"]


def test_a_failure_backs_off_from_30_s_doubling_to_6_h_and_counts_attempts(conn):
    zr.enqueue(conn, [{"id": "FAIL2222", "kind": "top", "version": 4}])
    waits = []
    for n in range(1, 12):
        assert zr.backoff(conn, "FAIL2222", f"failure {n}", now=NOW) == n
        (row,) = conn.execute("SELECT next_at, reason FROM zotero_queue WHERE id = 'FAIL2222'").fetchall()
        waits.append(row[0])
        assert row[1] == f"failure {n}"
    assert waits[0] == _after(NOW, 30) and waits[1] == _after(NOW, 60) and waits[7] == _after(NOW, 30 * 128)
    assert waits[-1] == _after(NOW, 6 * 3600)
    assert zr.take(conn, now=NOW, limit=5) == []  # kept back
    assert [e.id for e in zr.take(conn, now=_after(NOW, 6 * 3600), limit=5)] == ["FAIL2222"]
    assert zr.size(conn, now=NOW) == 0 and zr.size(conn) == 1
    assert zr.next_wait(conn, now=NOW) == waits[-1]


def test_a_newer_version_starts_the_backoff_over_and_a_page_keeps_its_wait(conn):
    zr.enqueue(conn, [{"id": "BACK2222", "kind": "child", "version": 4},
                      zr.page_entry("quiet", next_at=_after(NOW, 60))])
    zr.backoff(conn, "BACK2222", "Zotero is busy", now=NOW)
    zr.enqueue(conn, [{"id": "BACK2222", "kind": "child", "version": 4}])  # listed again, same version
    assert zr.take(conn, now=NOW, limit=5) == []
    zr.enqueue(conn, [{"id": "BACK2222", "kind": "child", "version": 5}, zr.page_entry("quiet")])
    (entry,) = zr.take(conn, now=NOW, limit=5)
    assert (entry.id, entry.attempts, entry.next_at, entry.reason) == ("BACK2222", 0, "", "")
    assert zr.next_wait(conn, now=NOW) == _after(NOW, 60)  # the page's wait: BACK2222 is due now
    zr.enqueue(conn, [zr.page_entry("quiet", next_at="")])  # an explicit "now"
    assert {e.id for e in zr.take(conn, now=NOW, limit=5)} == {"BACK2222", "page:quiet"}


def test_backoff_of_an_entry_not_queued_queues_it(conn):
    assert zr.backoff(conn, "page:gone", "boom", now=NOW) == 1
    (entry,) = zr.take(conn, now=_after(NOW, 31), limit=5)
    assert (entry.kind, entry.priority, entry.attempts) == ("page", zr.PAGE_PRIORITY, 1)


def test_done_and_drop_remove_entries_and_a_version_guard_keeps_a_newer_listing(conn):
    zr.enqueue(conn, [{"id": k, "kind": "child", "version": v} for k, v in
                      (("CHLD2222", 3), ("CHLD3333", 8), ("CHLD4444", 2), ("CHLD5555", 1))])
    assert zr.done(conn, {"CHLD2222": 5, "CHLD3333": 5}) == 1  # CHLD3333 was listed newer than what was read
    assert zr.drop(conn, ["CHLD4444"]) == 1
    assert zr.done(conn, []) == 0 and zr.done(conn, {}) == 0
    assert {e.id for e in zr.take(conn, now=NOW, limit=9)} == {"CHLD3333", "CHLD5555"}
    assert zr.next_wait(conn, now=NOW) is None  # only entries due now: none held back
    zr.drop(conn, ["CHLD3333", "CHLD5555"])
    assert zr.next_wait(conn, now=NOW) is None and zr.size(conn) == 0


def test_the_queue_answers_which_entries_are_due_wakes_a_reason_and_names_the_next_wait(conn):
    now = datetime(2026, 10, 28, 12, tzinfo=timezone.utc)
    zr.enqueue(conn, [zr.page_entry("a"), zr.page_entry("b", next_at=now + timedelta(seconds=60), reason="w"),
                      zr.page_entry("c", next_at=now + timedelta(seconds=30), reason="other")])
    assert zr.due(conn, ["page:a", "page:b", "page:c", "page:zz"], now=now) == ["page:a"]
    assert zr.due(conn, [], now=now) == []
    assert zr.next_wait(conn, now=now) == zr._stamp(now + timedelta(seconds=30))
    assert zr.wake(conn, ["page:b", "page:c"], reason="w") == 1
    assert zr.due(conn, ["page:a", "page:b", "page:c"], now=now) == ["page:a", "page:b"]
    assert len(zr.take(conn, now=now + timedelta(hours=1))) == 3  # no limit: every due entry
    assert zr.when("2026-10-28T12:00:00.000000Z") == now


def test_times_may_be_datetimes_and_bad_ones_are_refused(conn):
    zr.enqueue(conn, [{"id": "TIME2222", "kind": "top", "version": 1}])
    zr.backoff(conn, "TIME2222", "x", now=datetime(2020, 1, 1, 12, tzinfo=timezone.utc))
    assert zr.take(conn, now=page_now(), limit=1)  # long due by now
    zr.backoff(conn, "TIME2222", "x", now=datetime(2020, 1, 1, 12))  # a naive one is UTC
    (row,) = conn.execute("SELECT next_at FROM zotero_queue").fetchall()
    assert row[0] == "2020-01-01T12:01:00.000000Z"
    with pytest.raises(ValueError):
        zr.take(conn, now="not a time", limit=1)


def test_the_state_row_has_defaults_and_keeps_what_is_set(conn):
    assert zr.get_state(conn) == {"library": "", "version": 0, "local_cursor": "", "labels": {}, "first": "",
                                  "restored_at": "", "whole_version": 0}
    state = zr.set_state(conn, library=LIB, version=41, labels={"l1": "Reading"}, first="new", whole_version="41")
    assert state == {"library": LIB, "version": 41, "local_cursor": "", "labels": {"l1": "Reading"},
                     "first": "new", "restored_at": "", "whole_version": 41}
    assert zr.set_state(conn, local_cursor="17", first="")["version"] == 41
    assert zr.set_state(conn)["local_cursor"] == "17"
    assert conn.execute("SELECT COUNT(*) FROM zotero_state").fetchone()[0] == 1
    with pytest.raises(ValueError):
        zr.set_state(conn, cursor="x")
    with pytest.raises(ValueError):
        zr.set_state(conn, labels="Reading")


def test_clear_empties_the_links_three_tables(conn):
    _row(conn, "ROWS2222", 3)
    zr.enqueue(conn, [zr.page_entry("p")])
    zr.set_state(conn, library=LIB)
    zr.clear(conn)
    assert [conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
            for t in ("zotero_objects", "zotero_queue", "zotero_state")] == [0, 0, 0]


def test_a_fresh_pages_db_has_the_tables_and_their_indexes(conn):
    names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master")}
    assert {"zotero_objects", "zotero_queue", "zotero_state", "idx_zotero_objects_block",
            "idx_zotero_objects_page", "idx_zotero_objects_parent_version", "idx_zotero_queue_parent"} <= names
    assert "idx_zotero_objects_parent" not in names  # (the parent-version index's first column serves its lookups)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO zotero_state (id) VALUES (2)")


# --- reads -------------------------------------------------------------------------------

def test_items_are_read_fifty_keys_a_call_trash_included_and_absent_keys_apart(fake, zc, made):
    keys = [fake.add_item(LIB, {"itemType": "book", "title": f"Book {i}"}) for i in range(110)]
    fake.trash(LIB, keys[3])
    fake.calls.clear()
    fetched = zr.fetch_items(zc, LIB, [*keys, "ABSENT22", made["keys"]["trashed"], keys[0]])
    assert list(fetched.objects) == [*keys, made["keys"]["trashed"]]
    assert fetched.objects[keys[3]]["data"]["deleted"] == 1 and fetched.absent == ["ABSENT22"]
    reads = _calls(fake, needle="itemKey=")
    assert len(reads) == 3 and all("includeTrashed=1" in p for p in reads)


def test_collections_are_read_by_key_a_trashed_one_too(fake, zc, made):
    keys = made["keys"]
    fake.trash(LIB, keys["reading"])
    fetched = zr.fetch_collections(zc, LIB, [keys["ml"], keys["reading"], "MISS2222"])
    assert set(fetched.objects) == {keys["ml"], keys["reading"]} and fetched.absent == ["MISS2222"]
    assert fetched.objects[keys["reading"]]["data"]["deleted"] is True


def test_absent_keys_are_told_apart_from_hard_deletes(fake, zc, made):
    keys = made["keys"]
    since = fake.version(LIB)
    fake.delete(LIB, keys["link"])
    assert zr.hard_deleted(zc, LIB, [keys["link"], "MISSED22"], since=since) == {keys["link"]}


def test_a_unit_is_its_item_children_their_annotations_and_note_pictures(fake, zc, made):
    keys = made["keys"]
    fake.trash(LIB, keys["note"])
    fake.calls.clear()
    unit = zr.fetch_unit(zc, LIB, keys["attention"])
    assert unit.item["key"] == keys["attention"]
    got = {c["key"] for c in unit.children}
    annotations = {keys[n] for n in ("highlight", "underline", "note_annotation", "image_annotation",
                                     "ink_annotation", "text_annotation", "spanning")}
    direct = {keys[n] for n in ("pdf", "supplement", "note", "image_note", "image", "snapshot", "link")}
    assert got == direct | annotations
    assert next(c for c in unit.children if c["key"] == keys["note"])["data"]["deleted"] == 1
    read = {p.split("/items/")[1].split("/")[0] for p in _calls(fake, needle="/children")}
    # the PDFs' annotations and the picture note's picture; not the plain note, the snapshot or the link
    assert read == {keys["attention"], keys["pdf"], keys["supplement"], keys["image_note"]}
    assert all("includeTrashed=1" in p for p in _calls(fake, needle="/children"))


def test_a_standalone_pdf_unit_holds_its_annotations_and_a_long_list_is_paged(fake, zc, made):
    keys = made["keys"]
    rect = '{"pageIndex":0,"rects":[[72,700,200,712]]}'
    made_keys = [fake.add_item(LIB, {"itemType": "annotation", "parentItem": keys["standalone_pdf"],
                                     "annotationType": "image", "annotationColor": "#ffd400",
                                     "annotationSortIndex": f"00000|{i:06d}|00080", "annotationPosition": rect})
                 for i in range(130)]
    unit = zr.fetch_unit(zc, LIB, keys["standalone_pdf"])
    assert {c["key"] for c in unit.children} == set(made_keys)
    assert len(_calls(fake, needle=f"/items/{keys['standalone_pdf']}/children")) == 2


def test_units_are_read_together_and_one_zotero_no_longer_has_is_absent(fake, zc, made):
    keys = made["keys"]
    fake.delete(LIB, keys["thesis"])
    fake.calls.clear()
    units, absent = zr.fetch_units(zc, LIB, [keys["ppo"], keys["thesis"], keys["standalone_note"]])
    assert set(units) == {keys["ppo"], keys["standalone_note"]} and absent == [keys["thesis"]]
    assert units[keys["ppo"]].children == [] and units[keys["standalone_note"]].children == []
    assert len(_calls(fake, needle="itemKey=")) == 1
    assert len(_calls(fake, needle="/children")) == 1  # the note shows no picture: only the preprint's list
    assert zr.fetch_unit(zc, LIB, keys["thesis"]) == zr.Unit(None, [])


def test_an_item_removed_while_its_unit_is_read_is_absent_and_a_child_removed_is_left_out(fake, zc, made):
    keys = made["keys"]
    fake.on_request(f"/items/{keys['ppo']}/children", lambda: fake.delete(LIB, keys["ppo"]))
    units, absent = zr.fetch_units(zc, LIB, [keys["ppo"]])
    assert units == {} and absent == [keys["ppo"]]
    fake.on_request(f"/items/{keys['supplement']}/children", lambda: fake.delete(LIB, keys["supplement"]))
    unit = zr.fetch_unit(zc, LIB, keys["attention"])
    assert keys["supplement"] in {c["key"] for c in unit.children}  # listed before it went
    assert keys["highlight"] in {c["key"] for c in unit.children}


# --- writes ------------------------------------------------------------------------------

def _annotation(parent, i, **extra):
    return {"itemType": "annotation", "parentItem": parent, "annotationType": "image", "annotationColor": "#ffd400",
            "annotationSortIndex": f"00000|{i:06d}|00080",
            "annotationPosition": '{"pageIndex":0,"rects":[[72,700,200,712]]}', **extra}


@pytest.fixture
def posts(fake, monkeypatch):
    """The multi-object writes Zotero is sent, as ``(endpoint, objects)``."""
    seen = []
    handle = fake.handle

    def keeping(method, url, headers, body):
        if method == "POST" and isinstance(body, bytes) and body.startswith(b"["):
            seen.append((url.split("?")[0].rsplit("/", 1)[-1], json.loads(body)))
        return handle(method, url, headers, body)

    monkeypatch.setattr(fake, "handle", keeping)
    return seen


def _sizes(posts):
    return [(endpoint, len(objects)) for endpoint, objects in posts]


def test_writes_go_in_dependency_order_fifty_a_request(fake, zc, posts):
    writes = zr.Writes(zc, LIB)
    item, attachment, note = object_key("w", "item"), object_key("w", "att"), object_key("w", "note")
    marks = [object_key("w", "ann", str(i)) for i in range(60)]
    for i, key in enumerate(marks):  # added first, sent last
        writes.create("annotations", key, _annotation(attachment, i))
    writes.create("notes", note, {"itemType": "note", "parentItem": item, "note": "<p>Notes from Gamma</p>"})
    writes.create("attachments", attachment, {"itemType": "attachment", "parentItem": item,
                                              "linkMode": "imported_file", "contentType": "application/pdf",
                                              "filename": "paper.pdf"})
    writes.create("items", item, {"itemType": "journalArticle", "title": "A paper"})
    top = object_key("w", "c0")
    chain = [top] + [object_key("w", f"c{i}") for i in range(1, 4)]
    for parent, child in reversed(list(zip(chain, chain[1:]))):  # the deepest first
        writes.create("collections", child, {"name": child, "parentCollection": parent})
    writes.create("collections", top, {"name": "Top"})
    assert len(writes) == 67
    out = writes.flush()
    assert {o.status for o in out.values()} == {zr.OK} and len(out) == 67 and len(writes) == 0
    assert _sizes(posts) == [("collections", 1), ("collections", 1), ("collections", 1), ("collections", 1),
                             ("items", 1), ("items", 1), ("items", 1), ("items", 50), ("items", 10)]
    assert out[item].data["data"]["title"] == "A paper" and out[item].version == fake.data(LIB, item)["version"]
    assert fake.data(LIB, chain[-1])["parentCollection"] == chain[-2]
    assert writes.library_version == fake.version(LIB)
    assert all(obj["version"] == 0 for _, objects in posts for obj in objects)


def test_each_object_carries_its_own_version_and_never_the_librarys(fake, zc, made):
    keys = made["keys"]
    fake.calls.clear()
    writes = zr.Writes(zc, LIB)
    writes.update("items", keys["ppo"], fake.data(LIB, keys["ppo"])["version"], {"title": "PPO 2"})
    out = writes.flush()
    assert out[keys["ppo"]].status == zr.OK and fake.data(LIB, keys["ppo"])["title"] == "PPO 2"
    assert not any("if-unmodified-since-version" in h for _, _, h in fake.calls)


def test_every_outcome_class_from_zoteros_answers(fake, zc, made):
    keys = made["keys"]
    ppo, thesis = fake.data(LIB, keys["ppo"]), fake.data(LIB, keys["thesis"])
    gone = keys["deep_learning"]
    gone_version = fake.data(LIB, gone)["version"]
    fake.delete(LIB, gone)
    writes = zr.Writes(zc, LIB)
    new = object_key("outcomes", "new")
    writes.create("items", new, {"itemType": "book", "title": "New"})                         # ok
    writes.update("items", keys["ppo"], ppo["version"], {"title": ppo["title"]})              # unchanged
    writes.update("items", keys["thesis"], thesis["version"] - 1, {"title": "Stale"})        # conflict (412)
    writes.create("items", keys["conference"], {"itemType": "book", "title": "Mine"})        # exists (412 on 0)
    writes.update("items", gone, gone_version, {"title": "Gone"})                             # conflict (404)
    writes.create("annotations", object_key("outcomes", "bad"),
                  _annotation(keys["pdf"], 1, annotationColor="yellow"))                      # rejected (400)
    out = writes.flush()
    assert out[new].status == zr.OK and out[new].data["key"] == new
    assert out[keys["ppo"]] == zr.Outcome(keys["ppo"], zr.UNCHANGED, None, ppo["version"], "")
    assert out[keys["thesis"]].status == zr.CONFLICT and "modified since" in out[keys["thesis"]].message
    exists = out[keys["conference"]]
    assert exists.status == zr.EXISTS and exists.data["data"]["itemType"] == "conferencePaper"
    assert exists.version == fake.data(LIB, keys["conference"])["version"]
    assert out[gone].status == zr.CONFLICT
    bad = out[object_key("outcomes", "bad")]
    assert bad.status == zr.REJECTED and bad.message == "annotationColor must be a hex color (e.g., '#FF0000')"
    assert writes.outcomes == out


def test_transient_classes_and_codes_zotero_may_answer_per_object():
    assert [zr._status(c, 3) for c in (412, 404, 428, 429, 500, 503, 0, 400, 403, 409, 413)] == [
        zr.CONFLICT, zr.CONFLICT, zr.TRANSIENT, zr.TRANSIENT, zr.TRANSIENT, zr.TRANSIENT, zr.TRANSIENT,
        zr.REJECTED, zr.REJECTED, zr.REJECTED, zr.REJECTED]
    assert zr._status(412, 0) == zr.EXISTS and zr._status(404, 0) == zr.REJECTED


class _Answers:
    """A client whose writes answer what a test says, by index."""

    def __init__(self, failed=None, fail_with=None):
        self.failed, self.fail_with, self.sent = failed or {}, fail_with, []

    def write(self, library, kind, objects):
        self.sent.append([o["key"] for o in objects])
        if self.fail_with:
            raise self.fail_with
        return WriteResult(failed={i: {"key": o["key"], "code": self.failed[o["key"]], "message": "said"}
                                   for i, o in enumerate(objects) if o["key"] in self.failed},
                           successful={i: {"key": o["key"], "version": 9, "data": o}
                                       for i, o in enumerate(objects) if o["key"] not in self.failed},
                           version=9)


def test_per_object_428_429_and_5xx_are_transient():
    a, b, c, d = (object_key("t", n) for n in "abcd")
    client = _Answers(failed={a: 428, b: 429, c: 503})
    writes = zr.Writes(client, LIB)
    for key in (a, b, c, d):
        writes.update("items", key, 4, {"title": "x"})
    out = writes.flush()
    assert [out[k].status for k in (a, b, c, d)] == [zr.TRANSIENT] * 3 + [zr.OK]
    assert writes.error is None


def test_a_child_waits_when_zotero_did_not_save_its_parent(fake, zc, posts):
    item, attachment, other = object_key("p", "item"), object_key("p", "att"), object_key("p", "other")
    writes = zr.Writes(zc, LIB)
    writes.create("items", item, {"itemType": "nonsense", "title": "Refused"})
    writes.create("attachments", attachment, {"itemType": "attachment", "parentItem": item,
                                              "linkMode": "imported_file", "contentType": "application/pdf"})
    writes.create("annotations", object_key("p", "ann"), _annotation(attachment, 1))
    writes.create("items", other, {"itemType": "book", "title": "Fine"})
    out = writes.flush()
    assert out[item].status == zr.REJECTED and out[other].status == zr.OK
    assert out[attachment].status == zr.TRANSIENT and item in out[attachment].message
    assert out[object_key("p", "ann")].status == zr.TRANSIENT
    assert _sizes(posts) == [("items", 2)]  # neither child was sent


def test_a_collection_filed_in_a_collection_zotero_did_not_save_waits(fake, zc):
    writes = zr.Writes(zc, LIB)
    folder, page = object_key("f", "folder"), object_key("f", "page")
    writes.create("collections", folder, {"name": ""})  # refused: a collection needs a name
    writes.create("items", page, {"itemType": "book", "title": "Filed", "collections": [folder]})
    out = writes.flush()
    assert out[folder].status == zr.REJECTED and out[page].status == zr.TRANSIENT


def test_no_answer_stops_the_flush_and_every_object_is_transient(fake, zc, monkeypatch):
    real = zotero_web._request

    def unreachable(method, url, headers, body, timeout, sink=None):
        if method == "POST":
            raise ZoteroUnreachable("Zotero could not be reached: timed out")
        return real(method, url, headers, body, timeout, sink)

    monkeypatch.setattr(zotero_web, "_request", unreachable)
    writes = zr.Writes(zc, LIB)
    item = object_key("o", "item")
    writes.create("items", item, {"itemType": "book", "title": "Offline"})
    writes.create("notes", object_key("o", "note"), {"itemType": "note", "parentItem": item, "note": "x"})
    writes.create("collections", object_key("o", "c"), {"name": "C"})
    out = writes.flush()
    assert {o.status for o in out.values()} == {zr.TRANSIENT} and len(out) == 3
    assert zr.error_kind(writes.error) == "offline"
    assert len([1 for m, _, _ in fake.calls if m == "POST"]) == 0  # the transport refused the first one


def test_a_busy_zotero_stops_the_flush_after_its_retries(fake, zc):
    fake.fail_next(503, "/items", times=zotero_web.MAX_RETRIES + 1)
    writes = zr.Writes(zc, LIB)
    a, b = object_key("b", "a"), object_key("b", "b")
    writes.create("items", a, {"itemType": "book", "title": "A"})
    writes.create("notes", b, {"itemType": "note", "note": "B"})
    out = writes.flush()
    assert out[a].status == out[b].status == zr.TRANSIENT and zr.error_kind(writes.error) == "busy"
    assert "not sent" in out[b].message


def test_a_request_refused_whole_is_split_to_find_the_object_it_refuses(fake, zc):
    pdf = standard_library(fake)["keys"]["pdf"]
    writes = zr.Writes(zc, LIB)
    good = [object_key("s", str(i)) for i in range(9)]
    poison = object_key("s", "poison")
    for i, key in enumerate(good[:4]):
        writes.create("annotations", key, _annotation(pdf, i))
    data = _annotation(pdf, 99)
    del data["annotationType"]  # Zotero fails the whole request on this one (a 500)
    writes.create("annotations", poison, data)
    for i, key in enumerate(good[4:]):
        writes.create("annotations", key, _annotation(pdf, i + 4))
    out = writes.flush()
    assert all(out[k].status == zr.OK for k in good) and out[poison].status == zr.TRANSIENT
    assert writes.error is None


def test_a_retype_is_a_put_with_the_rows_version_and_reads_the_item_back(fake, zc, made):
    keys = made["keys"]
    version = fake.data(LIB, keys["thesis"])["version"]
    body = {"itemType": "report", "title": "Quantum error correction with bosonic codes", "institution": "Yale"}

    def replace():  # one retype alone
        writes = zr.Writes(zc, LIB)
        writes.replace(keys["thesis"], version, body)
        return writes.flush()[keys["thesis"]]

    outcome = replace()
    assert outcome.status == zr.OK and outcome.data["data"]["itemType"] == "report"
    assert outcome.version == fake.data(LIB, keys["thesis"])["version"] > version
    stale = replace()
    assert stale.status == zr.CONFLICT and stale.version == version
    fake.delete(LIB, keys["ppo"])
    writes = zr.Writes(zc, LIB)
    writes.replace(keys["ppo"], 3, {"itemType": "report", "title": "Gone"})
    writes.create("notes", object_key("put", "note"), {"itemType": "note", "note": "<p>after</p>"})
    out = writes.flush()
    assert out[keys["ppo"]].status == zr.CONFLICT and writes.error is None  # the item went, not the library
    assert out[object_key("put", "note")].status == zr.OK


def test_two_writes_of_one_key_are_one_object_sent_once(fake, zc, made, posts):
    keys = made["keys"]
    version = fake.data(LIB, keys["ppo"])["version"]
    writes = zr.Writes(zc, LIB)
    writes.update("items", keys["ppo"], version, {"title": "Merged"})
    writes.trash("items", keys["ppo"], version)
    assert len(writes) == 1 and keys["ppo"] in writes
    with pytest.raises(ValueError):  # one key is one kind of object
        writes.update("collections", keys["ppo"], version, {"name": "x"})
    out = writes.flush()
    assert out[keys["ppo"]].status == zr.OK
    assert fake.data(LIB, keys["ppo"])["title"] == "Merged" and fake.data(LIB, keys["ppo"])["deleted"] == 1
    assert posts == [("items", [{"title": "Merged", "deleted": True, "key": keys["ppo"], "version": version}])]


def test_trash_flags_go_last_and_restore_brings_back(fake, zc, made, posts):
    keys = made["keys"]
    v = {k: fake.data(LIB, keys[k])["version"] for k in ("reading", "thesis", "ppo")}
    writes = zr.Writes(zc, LIB)
    writes.trash("collections", keys["reading"], v["reading"])
    writes.update("items", keys["thesis"], v["thesis"], {"collections": []})
    out = writes.flush()
    assert {o.status for o in out.values()} == {zr.OK}
    assert _sizes(posts) == [("items", 1), ("collections", 1)]
    assert fake.data(LIB, keys["reading"])["deleted"] is True
    done = zr.trash(zc, LIB, "items", [(keys["ppo"], v["ppo"])])
    assert done[keys["ppo"]].status == zr.OK and fake.data(LIB, keys["ppo"])["deleted"] == 1
    back = zr.restore(zc, LIB, "items", [(keys["ppo"], done[keys["ppo"]].version)])
    assert back[keys["ppo"]].status == zr.OK and "deleted" not in fake.data(LIB, keys["ppo"])


def test_a_flush_until_a_stage_keeps_the_later_ones_for_the_next(fake, zc, made):
    keys = made["keys"]
    writes = zr.Writes(zc, LIB)
    attachment = object_key("u", "att")
    writes.create("attachments", attachment, {"itemType": "attachment", "parentItem": keys["ppo"],
                                              "linkMode": "imported_file", "contentType": "application/pdf"})
    note = object_key("u", "note")
    writes.create("notes", note, {"itemType": "note", "parentItem": keys["ppo"], "note": "<p>x</p>"})
    first = writes.flush(until="attachments")
    assert set(first) == {attachment} and note in writes
    second = writes.flush()
    assert set(second) == {note} and set(writes.outcomes) == {attachment, note}


def test_bad_writes_are_refused_before_any_call(fake, zc):
    writes = zr.Writes(zc, LIB)
    with pytest.raises(ValueError):
        writes.create("trash", object_key("x"), {})
    with pytest.raises(ValueError):
        writes.create("items", "not-a-key", {})
    with pytest.raises(ValueError):
        writes.update("items", object_key("x"), 0, {})
    assert writes.flush() == {} and not fake.calls


# --- files -------------------------------------------------------------------------------

def _attachment(fake, content, *, parent=None, stored=True, **data):
    key = fake.add_item(LIB, {"itemType": "attachment", "linkMode": "imported_file",
                              "contentType": "application/pdf", "filename": "f.pdf",
                              **({"parentItem": parent} if parent else {}), **data})
    if content is not None:
        fake.add_file(LIB, key, content, stored=stored)
    return key, fake.data(LIB, key).get("md5")


def test_a_pdf_comes_down_into_the_workspace_through_the_spool(fake, zc, ws):
    key, md5 = _attachment(fake, pdf_bytes())
    ticks = []
    doc_id = zr.download_pdf(ws, zc, LIB, key, md5=md5, tick=ticks.append)
    assert storage.open_path(ws, f"{doc_id}.pdf").read_bytes() == pdf_bytes()
    assert doc_id == storage.content_digest(pdf_bytes()) and _partial_files(ws) == []
    assert zr.download_pdf(ws, zc, LIB, key, md5=md5) == doc_id  # stored once


def test_a_file_zotero_storage_does_not_hold_is_unavailable(fake, zc, ws):
    key, md5 = _attachment(fake, pdf_bytes("WebDAV"), stored=False)  # a WebDAV user's: md5 and no file
    assert md5
    with pytest.raises(zr.FileUnavailable) as caught:
        zr.download_pdf(ws, zc, LIB, key, md5=md5)
    assert caught.value.reason == zr.UNAVAILABLE and isinstance(caught.value.__cause__, ZoteroNotFound)
    assert _partial_files(ws) == []
    with pytest.raises(zr.FileUnavailable) as caught:  # no md5: no copy, nothing asked
        fake.calls.clear()
        zr.download_pdf(ws, zc, LIB, key, md5="")
    assert caught.value.reason == zr.UNAVAILABLE and not fake.calls


def test_a_linked_file_is_never_asked_for_and_without_its_mode_it_is_unavailable(fake, zc, made, ws):
    linked = made["keys"]["linked_pdf"]
    fake.calls.clear()
    with pytest.raises(zr.FileUnavailable) as caught:
        zr.download_pdf(ws, zc, LIB, linked, md5="0" * 32, link_mode="linked_file")
    assert caught.value.reason == zr.LINKED and not fake.calls
    with pytest.raises(zr.FileUnavailable) as caught:
        zr.download_pdf(ws, zc, LIB, linked, md5="0" * 32)
    assert caught.value.reason == zr.UNAVAILABLE


def test_a_key_without_file_access_gets_no_access(monkeypatch, ws):
    fake = FakeZotero(access={"user": {"library": True, "files": False, "notes": True, "write": True},
                              "groups": {}}).install(monkeypatch)
    key, md5 = _attachment(fake, pdf_bytes())
    with pytest.raises(zr.FileUnavailable) as caught:
        zr.download_pdf(ws, ZoteroClient(fake.api_key), LIB, key, md5=md5)
    assert caught.value.reason == zr.NO_ACCESS and _partial_files(ws) == []


def test_a_zipped_snapshot_is_compressed(fake, zc, made, ws):
    keys = made["keys"]
    md5 = fake.data(LIB, keys["snapshot"])["md5"]
    with pytest.raises(zr.FileUnavailable) as caught:
        zr.download_pdf(ws, zc, LIB, keys["snapshot"], md5=md5)
    assert caught.value.reason == zr.COMPRESSED and isinstance(caught.value.__cause__, ZoteroCompressed)
    assert _partial_files(ws) == []


def test_a_checksum_mismatch_failed_and_nothing_is_kept(fake, zc, ws):
    key, _ = _attachment(fake, pdf_bytes())
    with pytest.raises(zr.FileUnavailable) as caught:
        zr.download_pdf(ws, zc, LIB, key, md5="0" * 32)
    assert caught.value.reason == zr.FAILED and "checksum" in str(caught.value)
    assert _partial_files(ws) == [] and storage.list(ws) == []


def test_a_file_that_is_no_pdf_is_unavailable(fake, zc, ws):
    key, md5 = _attachment(fake, b"<html>not a pdf</html>")
    with pytest.raises(zr.FileUnavailable) as caught:
        zr.download_pdf(ws, zc, LIB, key, md5=md5)
    assert caught.value.reason == zr.UNAVAILABLE and _partial_files(ws) == [] and storage.list(ws) == []


def _full(after_bytes=0):
    """A ``check_upload_allowed`` that refuses once ``after_bytes`` came."""
    def check(ws, nbytes):
        if nbytes > after_bytes:
            raise HTTPException(status_code=507, detail="storage quota exceeded (1 of 1 MB used)")
    return check


def test_this_servers_full_storage_stops_a_download_partway_and_keeps_nothing(fake, zc, ws, monkeypatch):
    big = pdf_bytes() + b"%" * 300_000
    key, md5 = _attachment(fake, big)
    monkeypatch.setattr(zotero_web, "TICK_BYTES", 64 * 1024)
    monkeypatch.setattr(storage, "SPOOL_CHECK_BYTES", 64 * 1024)
    asked = []

    def check(w, nbytes):
        asked.append(nbytes)
        _full(100_000)(w, nbytes)

    monkeypatch.setattr(storage, "check_upload_allowed", check)
    with pytest.raises(zr.FileUnavailable) as caught:
        zr.download_pdf(ws, zc, LIB, key, md5=md5)
    assert caught.value.reason == zr.QUOTA_HERE and "quota" in str(caught.value)
    assert asked and max(asked) < len(big)  # stopped before the whole file came
    assert _partial_files(ws) == [] and storage.list(ws) == []


def test_full_storage_is_seen_at_the_store_and_before_a_known_size_is_asked_for(fake, zc, ws, monkeypatch):
    key, md5 = _attachment(fake, pdf_bytes())
    monkeypatch.setattr(storage, "check_upload_allowed", _full())
    with pytest.raises(zr.FileUnavailable) as caught:
        zr.download_pdf(ws, zc, LIB, key, md5=md5)
    assert caught.value.reason == zr.QUOTA_HERE and _partial_files(ws) == []
    fake.calls.clear()
    with pytest.raises(zr.FileUnavailable) as caught:
        zr.download_pdf(ws, zc, LIB, key, md5=md5, size=len(pdf_bytes()))
    assert caught.value.reason == zr.QUOTA_HERE and not fake.calls


def test_a_stop_or_a_lost_connection_mid_download_is_raised_as_it_is(fake, zc, ws, monkeypatch):
    big = pdf_bytes() + b"%" * 200_000
    key, md5 = _attachment(fake, big)
    monkeypatch.setattr(zotero_web, "TICK_BYTES", 64 * 1024)

    def stop(done):
        raise Stopped()

    with pytest.raises(Stopped):
        zr.download_pdf(ws, zc, LIB, key, md5=md5, tick=stop)
    assert _partial_files(ws) == []
    fake.fail_next(502, "/__storage/", times=zotero_web.MAX_RETRIES + 1)
    with pytest.raises(ZoteroError) as caught:
        zr.download_pdf(ws, zc, LIB, key, md5=md5)
    assert not isinstance(caught.value, zr.FileUnavailable) and zr.error_kind(caught.value) == "offline"
    assert _partial_files(ws) == [] and storage.list(ws) == []


def _new_attachment(zc, seed, parent):
    key = object_key("upload", seed)
    out = zr.Writes(zc, LIB)
    out.create("attachments", key, {"itemType": "attachment", "parentItem": parent, "linkMode": "imported_file",
                                    "contentType": "application/pdf", "filename": "x.pdf"})
    assert out.flush()[key].status == zr.OK
    return key


def _md5(data):
    return hashlib.md5(data).hexdigest()


def test_an_upload_registers_and_answers_the_attachments_version(fake, zc, made, tmp_path):
    key = _new_attachment(zc, "a", made["keys"]["ppo"])
    data = pdf_bytes("Gamma's own copy")
    path = tmp_path / "up.pdf"
    path.write_bytes(data)
    version = zr.upload_pdf(zc, LIB, key, path, filename="Schulman - PPO.pdf", md5=_md5(data))
    assert version == fake.data(LIB, key)["version"]
    assert fake.data(LIB, key)["md5"] == _md5(data) and fake.data(LIB, key)["filename"] == "Schulman - PPO.pdf"
    again = _new_attachment(zc, "b", made["keys"]["thesis"])  # the same bytes elsewhere: Zotero has them
    assert zr.upload_pdf(zc, LIB, again, path, filename="x.pdf", md5=_md5(data)) is None


def test_a_full_zotero_storage_is_quota_and_a_wrong_file_is_conflict(fake, zc, made, tmp_path):
    keys = made["keys"]
    data = pdf_bytes("Too much")
    path = tmp_path / "big.pdf"
    path.write_bytes(data)
    fake.quota = 10
    key = _new_attachment(zc, "q", keys["ppo"])
    with pytest.raises(zr.Quota):
        zr.upload_pdf(zc, LIB, key, path, filename="big.pdf", md5=_md5(data))
    assert zr.Quota is ZoteroQuota and zr.Conflict is ZoteroConflict
    fake.quota = None
    stored_md5 = fake.data(LIB, keys["pdf"])["md5"]
    with pytest.raises(zr.Conflict):
        zr.upload_pdf(zc, LIB, keys["pdf"], path, filename="x.pdf", md5=_md5(data), replace_md5="0" * 32)
    version = zr.upload_pdf(zc, LIB, keys["pdf"], path, filename="x.pdf", md5=_md5(data), replace_md5=stored_md5)
    assert version == fake.data(LIB, keys["pdf"])["version"] and fake.data(LIB, keys["pdf"])["md5"] == _md5(data)


def test_a_note_picture_is_stored_as_an_upload(fake, zc, made, ws):
    name = zr.download_image(ws, zc, LIB, PICTURE_KEY, md5=_md5(PICTURE))
    assert name == f"{storage.content_digest(PICTURE)}.png"
    assert storage.open_path(ws, name).read_bytes() == PICTURE


def test_a_picture_that_is_none_or_too_big_or_unreachable_is_unavailable(fake, zc, made, ws, monkeypatch):
    note = made["keys"]["image_note"]
    odd = fake.add_item(LIB, {"itemType": "attachment", "parentItem": note, "linkMode": "embedded_image",
                              "contentType": "image/png"})
    fake.add_file(LIB, odd, b"not really a picture")
    with pytest.raises(zr.FileUnavailable) as caught:
        zr.download_image(ws, zc, LIB, odd)
    assert caught.value.reason == zr.UNAVAILABLE and storage.list(ws) == []
    monkeypatch.setattr(zr, "IMAGE_MAX_BYTES", 10)
    with pytest.raises(zr.FileUnavailable) as caught:
        zr.download_image(ws, zc, LIB, PICTURE_KEY)
    assert caught.value.reason == zr.UNAVAILABLE
    monkeypatch.setattr(zr, "IMAGE_MAX_BYTES", 25_000_000)
    with pytest.raises(zr.FileUnavailable) as caught:
        zr.download_image(ws, zc, LIB, "MISSPIC2")
    assert caught.value.reason == zr.UNAVAILABLE
    monkeypatch.setattr(storage, "check_upload_allowed", _full())
    with pytest.raises(zr.FileUnavailable) as caught:
        zr.download_image(ws, zc, LIB, PICTURE_KEY)
    assert caught.value.reason == zr.QUOTA_HERE


# --- errors ------------------------------------------------------------------------------

def test_errors_have_the_words_a_links_status_uses():
    cases = [
        (AccountError("no", 409, "not_connected"), "not_connected"),
        (AccountError("no", 409, "auth"), "auth"),
        (AccountError("no", 502, "offline"), "offline"),
        (ZoteroAuthError("refused", 403), "auth"),
        (ZoteroNotFound("no such library", 404), "library_gone"),
        (ZoteroUnreachable("Zotero could not be reached: timed out"), "offline"),
        (ZoteroError("Zotero could not be reached: timed out"), "other"),  # the type tells, never the words
        (ZoteroError("Zotero answered 502", 502), "offline"),
        (ZoteroError("Zotero answered 504", 504), "offline"),
        (ZoteroError("Zotero is busy", 429), "busy"),
        (ZoteroError("Zotero is busy", 503), "busy"),
        (ZoteroError("Zotero answered 500", 500), "busy"),
        (ZoteroError("The file Zotero sent does not match its checksum: try again"), "other"),
        (ZoteroConflict("changed", 412), "other"),
        (zr.FileUnavailable(zr.LINKED), "other"),
        (ValueError("x"), "other"),
    ]
    assert [zr.error_kind(e) for e, _ in cases] == [kind for _, kind in cases]
    assert set(kind for _, kind in cases) == set(zr.ERROR_KINDS)
