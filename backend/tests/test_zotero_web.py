"""Zotero's Web API client (gamma/zotero_web.py) against the fake Zotero
(zotero_fake.py): the headers every call carries, the throttle a key's
clients share, retries, the heartbeat, error mapping, the reads a sync
makes (versions since a library version, hard deletes, objects by key,
children), writes and the trash, file downloads and uploads, the libraries
a key reaches, and the real urllib transport over a served fake. The fake's
own Zotero rules (the annotation checks, the versions, the group members)
are pinned here too, since the engine's tests lean on them."""

import copy
import hashlib
import io
import json
import math
import socket
import threading
import time

import pytest
from zotero_fake import CHUNK, FULL_ACCESS, FakeZotero
from zotero_fixtures import CAROL, PICTURE, PICTURE_KEY, pdf_bytes, standard_library

from gamma import zotero_web
from gamma.zotero_web import (KEY_ALPHABET, WriteResult, ZoteroAuthError, ZoteroClient, ZoteroCompressed,
                              ZoteroConflict, ZoteroError, ZoteroNotFound, ZoteroQuota, ZoteroUnreachable, item_uri,
                              my_library_access, object_key, parse_item_uri, random_key)

LIB = "users/123"


class Stopped(BaseException):
    """What a stopped job's heartbeat raises, as ``jobs.Cancelled`` does."""


def _stop():
    raise Stopped()


@pytest.fixture
def fake(monkeypatch):
    return FakeZotero().install(monkeypatch)


@pytest.fixture
def zc(fake):
    return ZoteroClient(fake.api_key)


@pytest.fixture
def made(fake):
    return standard_library(fake)


def _books(fake, n, library=LIB):
    return [fake.add_item(library, {"itemType": "book", "title": f"Book {i}"}) for i in range(n)]


def _paths(fake, method="GET", needle=""):
    return [path for m, path, _ in fake.calls if m == method and needle in path]


def _another_key(fake, key="zkey-other"):
    fake.keys[key] = copy.deepcopy(fake.keys[fake.api_key])
    return key


def _create(zc, data, seed="x", library=LIB):
    """Create one object under a derived key; its key."""
    key = object_key(seed)
    result = zc.write(library, "items", [{"key": key, "version": 0, **data}])
    assert set(result.successful) == {0}, result.failed
    return key


def _failure(zc, data, library=LIB, kind="items"):
    """The one object's refusal: ``(code, message)``."""
    result = zc.write(library, kind, [data])
    assert not result.successful, result
    return result.failed[0]["code"], result.failed[0]["message"]


def _annotation(parent, kind="highlight", **fields):
    data = {"itemType": "annotation", "parentItem": parent, "annotationType": kind, "annotationColor": "#ffd400",
            "annotationPageLabel": "1", "annotationSortIndex": "00000|000010|00100",
            "annotationPosition": json.dumps({"pageIndex": 0, "rects": [[72, 700, 200, 712]]})}
    if kind in ("highlight", "underline"):
        data["annotationText"] = "quoted"
    return {**data, **fields}


# --- keys, URIs and the transport ---------------------------------------------

def test_object_keys_are_deterministic_over_zoteros_alphabet_and_random_ones_are_fresh():
    key = object_key("ws1", "page1", LIB)
    assert key == object_key("ws1", "page1", LIB)
    assert len(key) == 8 and set(key) <= set(KEY_ALPHABET)
    assert key != object_key("ws1", "page2", LIB)
    assert object_key("a", "bc") != object_key("ab", "c")  # the parts stay apart
    fresh = {random_key() for _ in range(50)}
    assert len(fresh) == 50 and all(len(k) == 8 and set(k) <= set(KEY_ALPHABET) for k in fresh)


def test_item_uris_round_trip_and_anything_else_is_not_one():
    uri = item_uri("groups/9", "ABCD2345")
    assert uri == "http://zotero.org/groups/9/items/ABCD2345"
    assert parse_item_uri(uri) == ("groups/9", "ABCD2345")
    for other in ("#item_3", "https://www.nature.com/articles/x", "http://zotero.org/users/1/items/abcd2345",
                  "http://zotero.org/users/1/items/ABCD234", "http://zotero.org/people/1/items/ABCD2345", None, 5):
        assert parse_item_uri(other) is None


def test_every_call_names_the_api_version_the_key_and_gamma(fake, zc):
    info = zc.key_info()
    assert info["userID"] == 123 and info["username"] == "tim"
    assert "key" not in info  # what Zotero echoes back is not passed on
    method, path, headers = fake.calls[-1]
    assert (method, path) == ("GET", "/keys/current")
    assert headers["zotero-api-version"] == "3"
    assert headers["zotero-api-key"] == "zkey-good"
    assert headers["user-agent"].startswith("Gamma/")


def test_an_unknown_key_is_refused_without_repeating_it(fake):
    with pytest.raises(ZoteroAuthError) as refused:
        ZoteroClient("zkey-unknown-1234").key_info()
    assert refused.value.status == 403
    assert "reconnect your account in Settings" in str(refused.value)
    assert "zkey-unknown-1234" not in str(refused.value)


def test_an_error_body_that_quotes_the_key_is_scrubbed(fake, zc, monkeypatch):
    monkeypatch.setattr(zotero_web, "_request",
                        lambda *a, **k: zotero_web.Response(400, {}, b"Bad key zkey-good in request"))
    with pytest.raises(ZoteroError) as refused:
        zc.key_info()
    assert str(refused.value) == "Zotero answered 400: Bad key … in request"


def test_paged_reads_page_after_page_with_the_first_pages_version(fake, zc):
    keys = _books(fake, 250)
    objects, version = zc.paged(f"/{LIB}/items")
    assert [o["key"] for o in objects] == keys[::-1]  # Zotero's default: the most recently changed first
    assert version == fake.version(LIB)
    assert _paths(fake) == [f"/{LIB}/items?start={start}&limit=100" for start in (0, 100, 200)]
    fake.calls.clear()
    assert len(zc.paged(f"/{LIB}/items", {"itemType": "book", "limit": 5})[0]) == 250  # its own limit
    fake.calls.clear()
    assert [o["key"] for o in zc.paged(f"/{LIB}/items", {"sort": "dateAdded", "direction": "asc"})[0]] == keys
    _books(fake, 1)
    fake.calls.clear()
    zc.paged(f"/{LIB}/items", {"itemType": "-attachment"})
    assert len(_paths(fake)) == 3


# --- waits and retries ----------------------------------------------------------

def test_a_429_waits_as_long_as_zotero_asks_then_retries(fake, zc):
    fake.fail_next(429, "/keys/current", {"Retry-After": "7"})
    assert zc.key_info()["userID"] == 123
    assert len(fake.sleeps) == 1 and 6 < fake.sleeps[0] <= 7
    assert len(_paths(fake, needle="/keys/current")) == 2


def test_without_retry_after_waits_double_and_the_fifth_refusal_stands(fake, zc):
    fake.fail_next(503, "/keys/current", times=5)
    with pytest.raises(ZoteroError) as busy:
        zc.key_info()
    assert busy.value.status == 503 and "busy" in str(busy.value)
    assert [round(s) for s in fake.sleeps] == [1, 2, 4, 8]
    assert len(_paths(fake, needle="/keys/current")) == 5


def test_a_long_retry_after_is_capped_and_a_zero_one_retries_at_once(fake, zc):
    fake.fail_next(429, "/keys/current", {"Retry-After": "3600"})
    zc.key_info()
    assert len(fake.sleeps) == 1 and 59 < fake.sleeps[0] <= zotero_web.MAX_WAIT
    fresh = ZoteroClient(_another_key(fake))  # Zotero paused the first key, not this one
    fake.fail_next(429, "/keys/current", {"Retry-After": "0"})
    fresh.key_info()
    assert len(fake.sleeps) == 1


def test_backoff_pauses_the_clients_later_calls(fake, zc, monkeypatch):
    def with_backoff(*args, **kwargs):
        resp = fake.request(*args, **kwargs)
        resp.headers["backoff"] = "5"
        return resp

    monkeypatch.setattr(zotero_web, "_request", with_backoff)
    zc.key_info()
    assert fake.sleeps == []
    zc.key_info()
    assert len(fake.sleeps) == 1 and 4 < fake.sleeps[0] <= 5


def test_statuses_map_to_their_errors(fake, zc, tmp_path):
    with pytest.raises(ZoteroNotFound) as missing:
        zc.request("GET", f"/{LIB}/items/ABCD2345")
    assert str(missing.value) == "Zotero has no such library, item or file"  # no API path in a sentence
    key = fake.add_item(LIB, {"itemType": "book", "title": "Old"})
    with pytest.raises(ZoteroConflict):
        zc.put_item(LIB, key, fake.version(LIB) - 1, {"itemType": "book", "title": "New"})
    fake.fail_next(500, "/items")
    with pytest.raises(ZoteroError) as failed:
        zc.write(LIB, "items", [{"itemType": "book", "title": "Refused"}])
    assert failed.value.status == 500 and str(failed.value) == "Zotero answered 500: Injected failure"
    assert fake.sleeps == []  # a write is never sent twice
    group = fake.add_group(10, "Readers", write=False)
    with pytest.raises(ZoteroAuthError):
        zc.write(group, "items", [{"itemType": "book", "title": "Not allowed"}])
    fake.fail_next(413, "/file")
    path = tmp_path / "big.pdf"
    path.write_bytes(b"%PDF-1.4 big")
    attachment = _create(zc, {"itemType": "attachment", "linkMode": "imported_file", "title": "Big",
                              "contentType": "application/pdf", "filename": "big.pdf"})
    with pytest.raises(ZoteroQuota) as full:
        zc.upload_file(LIB, attachment, path, filename="big.pdf", content_type="application/pdf",
                       md5=hashlib.md5(b"%PDF-1.4 big").hexdigest(), mtime_ms=1, size=12)
    assert full.value.status == 413 and isinstance(full.value, ZoteroError)


def test_a_get_zotero_fails_for_a_moment_is_made_again_a_few_times(fake, zc):
    keys = _books(fake, 3)
    for status in (502, 500, 504):
        fake.fail_next(status, "/items")
    found, _ = zc.versions(LIB, "items", 0)
    assert set(found) == set(keys)
    assert fake.sleeps == [1, 2, 4]
    fake.sleeps.clear()
    fake.fail_next(502, "/items", times=zotero_web.MAX_RETRIES + 1)
    with pytest.raises(ZoteroError) as failed:
        zc.versions(LIB, "items", 0)
    assert failed.value.status == 502 and fake.sleeps == [1, 2, 4, 8]


def test_a_get_that_got_no_answer_is_made_again_and_a_write_is_not(fake, zc, monkeypatch):
    lost = {"n": 1}

    def flaky(method, url, *args, **kwargs):
        if lost["n"]:
            lost["n"] -= 1
            raise ZoteroUnreachable("Zotero could not be reached: connection reset")
        return fake.request(method, url, *args, **kwargs)

    monkeypatch.setattr(zotero_web, "_request", flaky)
    assert zc.key_info()["userID"] == 123
    assert fake.sleeps == [1]
    lost["n"] = 1
    with pytest.raises(ZoteroError, match="could not be reached"):
        zc.write(LIB, "items", [{"itemType": "book", "title": "Sent once"}])
    assert fake.sleeps == [1] and _paths(fake, "POST") == []


def test_a_heartbeat_beats_through_every_wait_and_a_stop_ends_it(fake, monkeypatch):
    beats = []
    zc = ZoteroClient(fake.api_key, heartbeat=lambda: beats.append(1))
    fake.fail_next(429, "/keys/current", {"Retry-After": "3"})
    assert zc.key_info()["userID"] == 123
    assert len(fake.sleeps) == len(beats) == 3  # the wait, a second at a time
    assert max(fake.sleeps) <= zotero_web.HEARTBEAT and 2.9 < sum(fake.sleeps) <= 3
    monkeypatch.setattr(zotero_web, "_throttles", {})
    fake.sleeps.clear()

    def stopping():
        beats.append(1)
        if len(beats) > 5:
            raise Stopped()

    fake.fail_next(429, "/keys/current", {"Retry-After": "30"})
    calls = len(_paths(fake, needle="/keys/current"))
    with pytest.raises(Stopped):
        ZoteroClient(fake.api_key, heartbeat=stopping).key_info()
    assert fake.sleeps == [1, 1]  # stopped two seconds into a thirty seconds' wait
    assert len(_paths(fake, needle="/keys/current")) == calls + 1  # and never asked again


def test_a_stop_lands_while_other_clients_hold_the_keys_slots_or_reads_are_in_flight(fake, monkeypatch):
    monkeypatch.setattr(zotero_web, "HEARTBEAT", 0.01)
    beats = []

    def stopping():
        beats.append(1)
        if len(beats) >= 3:
            raise Stopped()

    slots = ZoteroClient(fake.api_key)._throttle.slots
    for _ in range(zotero_web.KEY_CONCURRENCY):
        slots.acquire()  # every call the key may have in flight is someone else's
    try:
        with pytest.raises(Stopped):
            ZoteroClient(fake.api_key, heartbeat=stopping).key_info()
    finally:
        for _ in range(zotero_web.KEY_CONCURRENCY):
            slots.release()
    assert _paths(fake, needle="/keys/current") == []
    keys = _books(fake, 450)
    release = threading.Event()

    def held(method, url, *args, **kwargs):
        release.wait(10)
        return fake.request(method, url, *args, **kwargs)

    monkeypatch.setattr(zotero_web, "_request", held)
    beats.clear()
    began = time.monotonic()
    try:
        with pytest.raises(Stopped):
            ZoteroClient(fake.api_key, heartbeat=stopping).objects(LIB, "items", keys)
        assert time.monotonic() - began < 5  # not held until the reads in flight end
    finally:
        release.set()


def test_clients_of_one_key_share_its_backoff_and_its_calls_in_flight(fake, monkeypatch):
    def with_backoff(*args, **kwargs):
        resp = fake.request(*args, **kwargs)
        resp.headers["backoff"] = "5"
        return resp

    monkeypatch.setattr(zotero_web, "_request", with_backoff)
    ZoteroClient(fake.api_key).key_info()
    monkeypatch.setattr(zotero_web, "_request", fake.request)
    ZoteroClient(fake.api_key).key_info()  # another client of the key: another link's round
    assert len(fake.sleeps) == 1 and 4 < fake.sleeps[0] <= 5
    ZoteroClient(_another_key(fake)).key_info()  # another key is not held up
    assert len(fake.sleeps) == 1
    monkeypatch.setattr(zotero_web, "_throttles", {})
    keys = _books(fake, 1000)
    lock, state = threading.Lock(), {"now": 0, "most": 0}

    def slow(*args, **kwargs):
        with lock:
            state["now"] += 1
            state["most"] = max(state["most"], state["now"])
        time.sleep(0.02)
        try:
            return fake.request(*args, **kwargs)
        finally:
            with lock:
                state["now"] -= 1

    monkeypatch.setattr(zotero_web, "_request", slow)
    found = []
    readers = [threading.Thread(target=lambda: found.append(len(ZoteroClient(fake.api_key).objects(
        LIB, "items", keys)))) for _ in range(3)]
    for reader in readers:
        reader.start()
    for reader in readers:
        reader.join()
    assert found == [1000] * 3
    assert 2 <= state["most"] <= zotero_web.KEY_CONCURRENCY


def test_bad_libraries_kinds_and_keys_are_refused_before_any_call(fake, zc):
    for library in ("users/abc", "groups/", "users/1/../2", ""):
        with pytest.raises(ValueError):
            zc.versions(library, "items", 0)
    for call in (lambda: zc.versions(LIB, "searches", 0), lambda: zc.objects(LIB, "tags", ["ABCD2345"]),
                 lambda: zc.objects(LIB, "items", ["../keys"]), lambda: zc.children(LIB, "../keys"),
                 lambda: zc.write(LIB, "items", [{}] * 51), lambda: zc.write(LIB, "searches", [{}]),
                 lambda: zc.trash(LIB, "items", [("nope", 1)]), lambda: zc.deleted("users/me", 0)):
        with pytest.raises(ValueError):
            call()
    assert zc.write(LIB, "items", []) == WriteResult()  # nothing to send: nothing sent
    assert zc.objects(LIB, "items", []) == []
    assert fake.calls == []


# --- the reads a sync makes ---------------------------------------------------------

def test_library_version_is_none_while_nothing_changed(fake, zc):
    _books(fake, 2)
    now = fake.version(LIB)
    assert zc.library_version(LIB) == now
    assert "if-modified-since-version" not in fake.calls[-1][2]
    assert zc.library_version(LIB, now) is None  # a 304
    assert fake.calls[-1][1] == f"/{LIB}/items?limit=1&format=versions"
    assert fake.calls[-1][2]["if-modified-since-version"] == str(now)
    fake.add_collection(LIB, "A change anywhere")
    assert zc.library_version(LIB, now) == now + 1


def test_versions_list_what_changed_since_trashed_included_in_one_call(fake, zc, made):
    keys, start = made["keys"], fake.version(LIB)
    top, version = zc.versions(LIB, "items/top", 0)
    assert version == start and keys["trashed"] in top and keys["pdf"] not in top
    assert top[keys["attention"]] == fake.data(LIB, keys["attention"])["version"]
    notes, _ = zc.versions(LIB, "items", 0, {"itemType": "note"})
    assert set(notes) == {keys["note"], keys["image_note"], keys["standalone_note"]}
    annotations, _ = zc.versions(LIB, "items", 0, {"itemType": "annotation"})
    assert len(annotations) == 7
    attachments, _ = zc.versions(LIB, "items", 0, {"itemType": "attachment"})
    assert keys["standalone_pdf"] in attachments and keys["link"] in attachments
    collections, _ = zc.versions(LIB, "collections", 0)
    assert set(collections) == {keys[k] for k in ("ml", "transformers", "efficient", "reading")}
    assert _paths(fake, needle="/collections")[-1] == f"/{LIB}/collections?since=0&format=versions"
    assert "includeTrashed=1" in _paths(fake, needle="/items/top")[-1]

    fake.edit(LIB, keys["thesis"], title="Retitled")
    fake.trash(LIB, keys["ppo"])
    fake.trash(LIB, keys["ml"])
    changed, version = zc.versions(LIB, "items/top", start)
    assert set(changed) == {keys["thesis"], keys["ppo"]} and version == fake.version(LIB)
    trashed = {keys["ml"], keys["transformers"], keys["efficient"]}  # a desktop trashes the subcollections too
    assert set(zc.versions(LIB, "collections", start)[0]) == trashed  # a trashed collection is listed
    assert zc.versions(LIB, "items", version)[0] == {}
    many = _books(fake, 250)
    fake.calls.clear()
    assert set(zc.versions(LIB, "items", version)[0]) == set(many) and len(fake.calls) == 1  # not paged
    with pytest.raises(ValueError):
        zc.versions(LIB, "items/top/x", 0)


def test_deleted_names_only_what_left_for_good(fake, zc, made):
    keys, start = made["keys"], fake.version(LIB)
    fake.trash(LIB, keys["thesis"])
    gone, version = zc.deleted(LIB, start)
    assert gone["items"] == [] and gone["collections"] == [] and version == fake.version(LIB)
    fake.delete(LIB, keys["attention"])  # its attachments, notes and annotations go with it
    fake.delete(LIB, keys["transformers"])
    gone, _ = zc.deleted(LIB, start)
    assert {keys["attention"], keys["pdf"], keys["highlight"], keys["note"]} <= set(gone["items"])
    assert set(gone["collections"]) == {keys["transformers"], keys["efficient"]}
    assert keys["efficient"] not in fake.data(LIB, keys["conference"])["collections"]
    assert zc.deleted(LIB, fake.version(LIB))[0]["items"] == []


def test_objects_are_read_whole_fifty_keys_a_call_the_trash_included(fake, zc, made):
    keys = made["keys"]
    books = _books(fake, 60)
    fake.trash(LIB, books[0])
    found = zc.objects(LIB, "items", books + ["ABCD2345", books[1]])
    assert sorted(o["key"] for o in found) == sorted(books)  # the missing key absent, a repeat read once
    assert len(_paths(fake, needle="itemKey=")) == 2
    trashed = next(o for o in found if o["key"] == books[0])
    assert trashed["data"]["deleted"] == 1 and {"key", "version", "data", "meta", "library"} <= set(trashed)
    collections = zc.objects(LIB, "collections", [keys["ml"], keys["efficient"]])
    assert {c["key"]: c["data"]["parentCollection"] for c in collections} == {
        keys["ml"]: False, keys["efficient"]: keys["transformers"]}
    assert _paths(fake, needle="collectionKey=")[-1].startswith(f"/{LIB}/collections?collectionKey=")
    fake.trash(LIB, keys["ml"])
    assert zc.objects(LIB, "collections", [keys["ml"]])[0]["data"]["deleted"] is True


def test_children_are_every_child_with_the_trashed_ones_in_the_order_added(fake, zc, made):
    keys = made["keys"]
    children = zc.children(LIB, keys["attention"])
    assert [c["key"] for c in children] == [keys[k] for k in ("pdf", "supplement", "note", "image_note",
                                                              "snapshot", "link")]
    fake.trash(LIB, keys["ink_annotation"])
    fake.edit(LIB, keys["highlight"], annotationComment="edited: still where it was added")
    annotations = zc.children(LIB, keys["pdf"])
    assert len(annotations) == 7 and all(isinstance(a["data"]["annotationPosition"], str) for a in annotations)
    assert [a["key"] for a in annotations][:2] == [keys["highlight"], keys["underline"]]
    assert next(a for a in annotations if a["key"] == keys["ink_annotation"])["data"]["deleted"] == 1
    assert all({"key", "version", "data", "meta"} <= set(a) for a in annotations)
    listed = f"/{LIB}/items/{keys['pdf']}/children?includeTrashed=1&sort=dateAdded&direction=asc&start=0&limit=100"
    assert _paths(fake, needle="/children")[-1] == listed  # one page holds them: one call
    assert [c["key"] for c in zc.children(LIB, keys["image_note"])] == [PICTURE_KEY]  # a note's picture
    assert zc.children(LIB, keys["thesis"]) == []
    many = [fake.add_item(LIB, {"itemType": "note", "parentItem": keys["thesis"], "note": f"<p>{n}</p>"})
            for n in range(130)]
    assert [c["key"] for c in zc.children(LIB, keys["thesis"])] == many  # page after page


def test_the_standard_library_holds_one_of_each_thing_a_link_meets(fake, zc, made):
    keys = made["keys"]
    kinds = {a["data"]["annotationType"] for a in zc.children(LIB, keys["pdf"])}
    assert kinds == {"highlight", "underline", "note", "image", "ink", "text"}
    for annotation in zc.children(LIB, keys["pdf"]):
        data = annotation["data"]
        assert ("annotationText" in data) == (data["annotationType"] in ("highlight", "underline"))
        assert json.loads(data["annotationPosition"])["pageIndex"] in (0, 1)
    pdfs = [c for c in zc.children(LIB, keys["attention"]) if c["data"].get("contentType") == "application/pdf"]
    assert min(pdfs, key=lambda c: c["data"]["dateAdded"])["key"] == keys["pdf"]  # the primary is the oldest
    assert fake.data(LIB, keys["note"])["note"].startswith('<div data-schema-version="')
    assert fake.data(LIB, keys["attention"])["tags"] == [{"tag": "transformers"}, {"tag": "attention", "type": 1}]
    assert fake.data(LIB, keys["snapshot"])["linkMode"] == "imported_url"
    assert fake.data(LIB, keys["trashed"])["deleted"] == 1
    note = fake.data(LIB, keys["image_note"])["note"]
    assert f'data-attachment-key="{PICTURE_KEY}"' in note and "data:" not in note
    picture = fake.data(LIB, keys["image"])
    assert (picture["parentItem"], picture["linkMode"], picture["contentType"]) == (
        keys["image_note"], "embedded_image", "image/png")
    sink = io.BytesIO()
    assert zc.download(LIB, keys["image"], sink, md5=picture["md5"]) == len(PICTURE) and sink.getvalue() == PICTURE
    linked = fake.data(LIB, keys["linked_pdf"])
    assert (linked["linkMode"], linked["contentType"], linked["path"]) == (
        "linked_file", "application/pdf", "/Users/tim/Papers/lewis2020.pdf")
    assert not {"filename", "md5", "mtime"} & set(linked)  # a linked file's lies on a desktop
    with pytest.raises(ZoteroNotFound):
        zc.download(LIB, keys["linked_pdf"], io.BytesIO())
    assert fake.data(LIB, keys["standalone_note"])["collections"] == [keys["reading"]]
    group_items = zc.objects(made["group"], "items", [keys["mine"], keys["carols"]])
    assert {o["key"]: o["meta"]["createdByUser"]["id"] for o in group_items} == {keys["mine"]: 123,
                                                                                  keys["carols"]: CAROL}


def _rects_dist(a, b):
    """The reader's ``rectsDist`` (selection.js): how far rect ``b`` lies
    from rect ``a``, 0 when they touch."""
    ax1, ay1, ax2, ay2 = a
    bx1, by1, bx2, by2 = b
    left, right, bottom, top = bx2 < ax1, ax2 < bx1, by2 < ay1, ay2 < by1
    if top and left:
        return math.hypot(ax1 - bx2, ay2 - by1)
    if left and bottom:
        return math.hypot(ax1 - bx2, ay1 - by2)
    if bottom and right:
        return math.hypot(ax2 - bx1, ay1 - by2)
    if right and top:
        return math.hypot(ax2 - bx1, ay2 - by1)
    return (ax1 - bx2) if left else (bx1 - ax2) if right else (ay1 - by2) if bottom else (by1 - ay2) if top else 0


def _reader_sort_index(glyphs, view_height, position):
    """The reader's ``getSortIndex``: the rect with the greatest x2 (an ink's
    paths' bounding box), the index of the nearest character among those that
    are not whitespace, the page height above the rect."""
    if position.get("paths"):
        xs = [v for path in position["paths"] for v in path[0::2]]
        ys = [v for path in position["paths"] for v in path[1::2]]
        rect = [min(xs), min(ys), max(xs), max(ys)]
    else:
        rect = sorted(position["rects"], key=lambda r: -r[2])[0]
    chars = [box for char, box in glyphs if char.strip() and (box[2] > box[0] or box[3] > box[1])]
    nearest = min(range(len(chars)), key=lambda i: (_rects_dist(chars[i], rect), i))
    top = math.floor(max(0, view_height - rect[3]))
    return f"{position['pageIndex']:05d}|{nearest:06d}|{top:05d}"


def test_the_fixture_annotations_carry_the_readers_sort_index(fake, made):
    from gamma.pdf_text import GlyphPages

    annotations = [d for library in (made["library"], made["group"]) for d in fake.items(library)
                   if d["itemType"] == "annotation"]
    assert len(annotations) == 9
    with GlyphPages(pdf_bytes()) as glyphs:
        for data in annotations:
            position = json.loads(data["annotationPosition"])
            (box, _rotation) = glyphs.frame(position["pageIndex"] + 1)
            assert data["annotationSortIndex"] == _reader_sort_index(
                glyphs.page(position["pageIndex"] + 1), box[3] - box[1], position), data["annotationType"]


def test_the_fixture_pdfs_text_lies_under_its_annotation_rects():
    """What zotero_fixtures promises: the quote Gamma reads under each rect
    is the text the annotation says it marks."""
    from zotero_fixtures import (HIGHLIGHT_RECT, HIGHLIGHT_TEXT, SPANNING_RECTS, SPANNING_TEXT, TITLE_RECT,
                                 TITLE_TEXT, UNDERLINE_RECT, UNDERLINE_TEXT)

    from gamma.pdf_text import GlyphPages, quote_under

    assert pdf_bytes() == pdf_bytes() and pdf_bytes("x") != pdf_bytes()
    with GlyphPages(pdf_bytes()) as glyphs:
        first = glyphs.page(1)
        assert quote_under(first, [HIGHLIGHT_RECT]) == HIGHLIGHT_TEXT
        assert quote_under(first, [UNDERLINE_RECT]) == UNDERLINE_TEXT
        assert quote_under(first, [TITLE_RECT]) == TITLE_TEXT
        assert quote_under(glyphs.page(2), SPANNING_RECTS) == SPANNING_TEXT


# --- writes and the trash -------------------------------------------------------------

def test_writes_create_once_update_by_version_and_put_replaces(fake, zc):
    key = object_key("ws", "page", LIB)
    new = {"key": key, "version": 0, "itemType": "book", "title": "Deep Learning"}
    first = zc.write(LIB, "items", [new])
    assert set(first.successful) == {0} and first.successful[0]["data"]["title"] == "Deep Learning"
    version = first.successful[0]["version"]
    assert first.version == version == fake.version(LIB)
    assert "if-unmodified-since-version" not in fake.calls[-1][2]  # each object carries its own
    again = zc.write(LIB, "items", [new])  # a create retried: it landed already
    assert again.failed == {0: {"key": key, "code": 412, "message": "Item has been modified since specified "
                                                                    f"version (expected 0, found {version})"}}
    patched = zc.write(LIB, "items", [{"key": key, "version": version, "publisher": "MIT Press"},
                                      {"key": key, "version": version - 1, "publisher": "Stale"}])
    assert set(patched.successful) == {0} and patched.failed[1]["code"] == 412
    data = fake.data(LIB, key)
    assert data["title"] == "Deep Learning" and data["publisher"] == "MIT Press"
    assert zc.write(LIB, "items", [{"key": key, "version": data["version"], "publisher": "MIT Press"}]).unchanged == {
        0: key}
    with pytest.raises(ZoteroConflict):
        zc.put_item(LIB, key, version, {"itemType": "book", "title": "Stale"})
    newer = zc.put_item(LIB, key, data["version"], {"itemType": "book", "title": "Deep Learning, 2nd ed."})
    assert newer == fake.version(LIB) > data["version"]
    assert fake.data(LIB, key)["title"] == "Deep Learning, 2nd ed." and fake.data(LIB, key)["publisher"] == ""
    assert zc.write(LIB, "items", [{"key": key, "itemType": "book"}]).failed[0] == {
        "key": key, "code": 428, "message": "Either If-Unmodified-Since-Version or object version property must "
                                            "be provided for key-based writes"}


def test_trash_and_restore_are_writes_never_deletes(fake, zc, made):
    keys = made["keys"]
    attention = fake.data(LIB, keys["attention"])
    pdf_version = fake.data(LIB, keys["pdf"])["version"]
    result = zc.trash(LIB, "items", [(keys["attention"], attention["version"])])
    assert set(result.successful) == {0} and result.successful[0]["data"]["deleted"] == 1
    assert fake.data(LIB, keys["pdf"])["version"] == pdf_version  # only the parent is flagged
    assert "deleted" not in fake.data(LIB, keys["pdf"])
    restored = zc.trash(LIB, "items", [(keys["attention"], result.version)], restore=True)
    assert set(restored.successful) == {0} and "deleted" not in fake.data(LIB, keys["attention"])
    ml = fake.data(LIB, keys["ml"])
    trashed = zc.trash(LIB, "collections", [(keys["ml"], ml["version"])])
    assert fake.data(LIB, keys["ml"])["deleted"] is True
    zc.trash(LIB, "collections", [(keys["ml"], trashed.version)], restore=True)
    assert "deleted" not in fake.data(LIB, keys["ml"])
    assert not [m for m, _, _ in fake.calls if m == "DELETE"]


def test_collections_are_written_by_key_and_moved_and_renamed(fake, zc):
    top, child = object_key("ws", "", "ML"), object_key("ws", "", "ML", "RL")
    result = zc.write(LIB, "collections", [{"key": top, "version": 0, "name": "ML", "parentCollection": False},
                                           {"key": child, "version": 0, "name": "RL", "parentCollection": top}])
    assert {i: o["key"] for i, o in result.successful.items()} == {0: top, 1: child}
    moved = zc.write(LIB, "collections", [{"key": child, "version": result.version, "name": "Reinforcement",
                                           "parentCollection": False}])
    assert set(moved.successful) == {0}
    assert fake.data(LIB, child) == {"key": child, "version": moved.version, "name": "Reinforcement",
                                     "parentCollection": False, "relations": {}}
    assert _failure(zc, {"key": object_key("c"), "version": 0, "name": " "}, kind="collections") == (
        400, "Collection name cannot be empty")


# --- files ---------------------------------------------------------------------------

def test_download_streams_the_file_checks_its_md5_and_leaves_the_key_behind(fake, zc, made):
    keys = made["keys"]
    pdf = fake.data(LIB, keys["pdf"])
    sink = io.BytesIO()
    assert zc.download(LIB, keys["pdf"], sink, md5=pdf["md5"]) == len(pdf_bytes())
    assert sink.getvalue() == pdf_bytes()
    storage = [headers for _, path, headers in fake.calls if path.startswith("/__storage/")]
    assert len(storage) == 1 and not any(name.startswith("zotero-") for name in storage[0])
    with pytest.raises(ZoteroError, match="checksum"):
        zc.download(LIB, keys["pdf"], io.BytesIO(), md5="0" * 32)
    with pytest.raises(ZoteroNotFound):
        zc.download(LIB, keys["link"], io.BytesIO())


def test_a_download_is_fetched_again_until_a_byte_of_it_arrived(fake, zc, made, monkeypatch):
    keys = made["keys"]
    md5 = fake.data(LIB, keys["pdf"])["md5"]
    fake.fail_next(502, "/__storage/")
    fake.fail_next(500, "/file")  # Zotero's own answer, before the storage
    sink = io.BytesIO()
    assert zc.download(LIB, keys["pdf"], sink, md5=md5) == len(pdf_bytes())
    assert sink.getvalue() == pdf_bytes() and fake.sleeps == [1, 1]
    storage = []

    def broken(method, url, headers, body, timeout, sink=None):
        if "/__storage/" in url:
            storage.append(url)
            sink.write(b"%PDF-1.4 and no more")
            raise ZoteroUnreachable("Zotero could not be reached: connection reset")
        return fake.request(method, url, headers, body, timeout, sink)

    monkeypatch.setattr(zotero_web, "_request", broken)
    with pytest.raises(ZoteroError, match="could not be reached"):
        zc.download(LIB, keys["pdf"], io.BytesIO(), md5=md5)
    assert len(storage) == 1  # what reached the sink is not added to


def test_a_file_zotero_keeps_zipped_is_not_taken_for_the_attachment(fake, zc, made, monkeypatch):
    key = made["keys"]["snapshot"]
    md5 = fake.data(LIB, key)["md5"]
    with pytest.raises(ZoteroCompressed) as zipped:
        zc.download(LIB, key, io.BytesIO(), md5=md5)
    assert isinstance(zipped.value, ZoteroError) and str(zipped.value) == zotero_web.COMPRESSED
    assert _paths(fake, needle="/__storage/") == []  # Zotero said so: the storage was never asked

    def unmarked(*args, **kwargs):
        resp = fake.request(*args, **kwargs)
        resp.headers.pop("zotero-file-compressed", None)
        return resp

    monkeypatch.setattr(zotero_web, "_request", unmarked)
    with pytest.raises(ZoteroCompressed):  # a ZIP that is not the file Zotero's checksum names gives it away
        zc.download(LIB, key, io.BytesIO(), md5=md5)
    archive = fake.add_item(LIB, {"itemType": "attachment", "parentItem": made["keys"]["attention"],
                                  "linkMode": "imported_file", "title": "Data", "contentType": "application/zip",
                                  "filename": "data.zip"})
    packed = fake.libraries[LIB].zipped[key]
    fake.add_file(LIB, archive, packed)
    assert zc.download(LIB, archive, io.BytesIO(), md5=fake.data(LIB, archive)["md5"]) == len(packed)


def test_a_file_zotero_cannot_hand_out_is_told_apart(fake, zc, made, monkeypatch):
    keys = made["keys"]
    webdav = fake.add_item(LIB, {"itemType": "attachment", "parentItem": keys["thesis"], "linkMode": "imported_file",
                                 "title": "PDF", "contentType": "application/pdf", "filename": "thesis.pdf"})
    fake.add_file(LIB, webdav, pdf_bytes("Kept on a WebDAV server."), stored=False)
    data = fake.data(LIB, webdav)
    assert data["md5"] == hashlib.md5(pdf_bytes("Kept on a WebDAV server.")).hexdigest() and data["mtime"]
    for missing in (webdav, keys["linked_pdf"], keys["link"]):  # WebDAV's, a desktop's, a web page's
        with pytest.raises(ZoteroNotFound):
            zc.download(LIB, missing, io.BytesIO(), md5=data["md5"])
    fake.fail_next(404, "/__storage/")
    with pytest.raises(ZoteroNotFound):  # Zotero named a file its storage does not hold
        zc.download(LIB, keys["pdf"], io.BytesIO())
    no_files = FakeZotero(access={"user": {"library": True, "notes": True, "write": True}}).install(monkeypatch)
    standard = standard_library(no_files)["keys"]
    with pytest.raises(ZoteroAuthError) as refused:
        ZoteroClient(no_files.api_key).download(LIB, standard["pdf"], io.BytesIO())
    assert refused.value.status == 403 and str(refused.value) == zotero_web.NO_FILE_ACCESS


def test_storage_elsewhere_gets_no_key_and_must_be_a_public_address(fake, zc, made):
    key = made["keys"]["standalone_pdf"]
    fake.storage_base = "https://93.184.216.34"  # a public address: resolves without DNS
    sink = io.BytesIO()
    zc.download(LIB, key, sink)
    assert sink.getvalue() == pdf_bytes("A standalone handout without metadata.")
    fake.storage_base = "http://127.0.0.1:9"
    before = len(fake.calls)
    with pytest.raises(ZoteroError, match="storage address was refused"):
        zc.download(LIB, key, io.BytesIO())
    assert [path for _, path, _ in fake.calls[before:]] == [f"/{LIB}/items/{key}/file"]


def _attachment(zc, seed):
    return _create(zc, {"itemType": "attachment", "linkMode": "imported_file", "title": "Paper",
                        "contentType": "application/pdf", "filename": "paper.pdf"}, seed)


def _upload(zc, key, path, data, **kwargs):
    return zc.upload_file(LIB, key, path, filename="paper.pdf", content_type="application/pdf",
                          md5=hashlib.md5(data).hexdigest(), mtime_ms=1700000000000, size=len(data), **kwargs)


def test_upload_file_authorizes_uploads_and_registers(fake, zc, tmp_path):
    data = pdf_bytes("An uploaded paper.")
    path = tmp_path / "paper.pdf"
    path.write_bytes(data)
    key = _attachment(zc, "upload-1")
    assert fake.data(LIB, key)["md5"] is None and fake.data(LIB, key)["mtime"] is None
    version = _upload(zc, key, path, data)
    stored = fake.data(LIB, key)
    assert version == stored["version"] == fake.version(LIB)  # what the register moved it to
    assert stored["md5"] == hashlib.md5(data).hexdigest() and stored["mtime"] == 1700000000000
    assert fake.libraries[LIB].files[key] == data
    steps = [(m, p, h) for m, p, h in fake.calls if "/file" in p or "/__upload/" in p]
    assert [m for m, _, _ in steps] == ["POST"] * 3 and steps[1][1].startswith("/__upload/")
    assert steps[0][2]["if-none-match"] == "*" and steps[2][2]["if-none-match"] == "*"
    assert not any(name.startswith("zotero-") for name in steps[1][2])
    with pytest.raises(ZoteroConflict):  # it has its file now: a second upload leaves it
        _upload(zc, key, path, data)
    assert fake.data(LIB, key) == stored
    other = _attachment(zc, "upload-2")
    uploads = len(_paths(fake, "POST", "/__upload/"))
    assert _upload(zc, other, path, data) is None  # bytes Zotero holds are not sent again
    assert len(_paths(fake, "POST", "/__upload/")) == uploads
    assert fake.data(LIB, other)["md5"] == stored["md5"]
    # JSON never sets a file's checksum: only an upload does.
    zc.write(LIB, "items", [{"key": key, "version": fake.data(LIB, key)["version"], "md5": "0" * 32}])
    assert fake.data(LIB, key)["md5"] == stored["md5"]


def test_a_replaced_file_goes_up_only_over_the_one_it_replaces(fake, zc, tmp_path):
    first, second, third = (pdf_bytes(f"Version {n}.") for n in (1, 2, 3))
    key = _attachment(zc, "replace")
    paths = []
    for n, data in enumerate((first, second, third)):
        paths.append(tmp_path / f"v{n}.pdf")
        paths[-1].write_bytes(data)
    assert _upload(zc, key, paths[0], first) == fake.data(LIB, key)["version"]
    assert _upload(zc, key, paths[1], second, replace_md5=hashlib.md5(first).hexdigest()) == fake.version(LIB)
    assert fake.libraries[LIB].files[key] == second
    steps = [h for m, p, h in fake.calls if p.endswith("/file")]
    assert steps[-1]["if-match"] == steps[-2]["if-match"] == hashlib.md5(first).hexdigest()
    # Zotero's file changed meanwhile: not the one Gamma replaces, so it stays.
    with pytest.raises(ZoteroConflict) as moved:
        _upload(zc, key, paths[2], third, replace_md5=hashlib.md5(first).hexdigest())
    assert moved.value.status == 412 and fake.libraries[LIB].files[key] == second


def test_a_full_storage_refuses_the_upload(fake, zc, tmp_path):
    fake.quota = 3000
    data = pdf_bytes("Too much.") + b"x" * 3000
    path = tmp_path / "big.pdf"
    path.write_bytes(data)
    key = _attachment(zc, "full")
    before = fake.data(LIB, key)
    with pytest.raises(ZoteroQuota) as full:
        _upload(zc, key, path, data)
    assert full.value.status == 413 and "File would exceed quota" in str(full.value)
    assert fake.data(LIB, key) == before and key not in fake.libraries[LIB].files
    assert _paths(fake, "POST", "/__upload/") == []  # refused at the authorization: no bytes sent
    fake.quota = None  # freed
    assert _upload(zc, key, path, data) == fake.version(LIB)


def test_transfers_tick_every_few_megabytes_and_beat_while_bytes_flow(fake, monkeypatch, tmp_path):
    monkeypatch.setattr(zotero_web, "TICK_BYTES", 100_000)
    data = b"%PDF-1.4\n" + bytes(range(256)) * 1200
    parent = fake.add_item(LIB, {"itemType": "book", "title": "A long scan"})
    key = fake.add_item(LIB, {"itemType": "attachment", "parentItem": parent, "linkMode": "imported_file",
                              "title": "PDF", "contentType": "application/pdf", "filename": "scan.pdf"})
    fake.add_file(LIB, key, data)
    ticks, beats = [], []
    zc = ZoteroClient(fake.api_key, heartbeat=lambda: beats.append(1))
    sink = io.BytesIO()
    assert zc.download(LIB, key, sink, md5=hashlib.md5(data).hexdigest(), tick=ticks.append) == len(data)
    assert ticks == [2 * CHUNK, 4 * CHUNK, len(data)] and beats
    upload = data + b"another one"
    path = tmp_path / "scan.pdf"
    path.write_bytes(upload)
    ticks.clear()
    target = _attachment(zc, "ticking")
    assert _upload(zc, target, path, upload, tick=ticks.append) == fake.version(LIB)
    assert len(ticks) == 3 and ticks == sorted(ticks) and ticks[-1] == len(upload)
    assert fake.libraries[LIB].files[target] == upload
    with pytest.raises(Stopped):  # a stop lands mid-transfer
        ZoteroClient(fake.api_key, heartbeat=_stop).download(LIB, key, io.BytesIO())


# --- libraries -------------------------------------------------------------------------

def _library(id_, name, kind, read, write, files, notes):
    return {"id": id_, "name": name, "kind": kind, "user_id": 123, "read": read, "write": write, "files": files,
            "notes": notes}


def test_libraries_list_my_library_then_groups_with_what_the_key_may_do(fake, zc):
    fake.add_group(9, "Lab")
    fake.add_group(10, "Readers", write=False)  # only its admins edit it and its files
    assert zc.libraries() == [
        _library(LIB, "My Library", "user", True, True, True, True),
        _library("groups/9", "Lab", "group", True, True, True, True),
        _library("groups/10", "Readers", "group", True, False, False, True),
    ]


def test_libraries_follow_a_keys_per_group_access(monkeypatch):
    fake = FakeZotero(access={"user": {"library": True, "files": True},
                              "groups": {"9": {"library": True, "write": False}}}).install(monkeypatch)
    fake.add_group(9, "Lab")
    fake.add_group(10, "Elsewhere")
    assert ZoteroClient(fake.api_key).libraries() == [
        _library(LIB, "My Library", "user", True, False, True, False),  # files to read: stored only with write
        _library("groups/9", "Lab", "group", True, False, True, True),  # a group's notes come with reading it
        _library("groups/10", "Elsewhere", "group", False, False, False, False),
    ]


def test_a_key_without_group_access_lists_only_my_library(monkeypatch):
    fake = FakeZotero(access={"user": {"library": True, "write": True}}).install(monkeypatch)
    fake.add_group(9, "Lab")
    assert ZoteroClient(fake.api_key).libraries() == [_library(LIB, "My Library", "user", True, True, False, False)]
    assert _paths(fake, needle="/groups") == []


def test_my_librarys_access_needs_reading_it_first():
    assert my_library_access(FULL_ACCESS) == {"read": True, "write": True, "files": True, "notes": True}
    assert my_library_access({"user": {"write": True, "files": True, "notes": True}}) == {
        "read": False, "write": False, "files": False, "notes": False}
    assert my_library_access(None) == my_library_access({"user": "all"}) == my_library_access({}) == {
        "read": False, "write": False, "files": False, "notes": False}


def test_revoke_deletes_the_key_and_a_failure_is_only_logged(fake, zc):
    fake.fail_next(429, "/keys/current", {"Retry-After": "30"}, times=2)
    zc.revoke()  # Zotero is busy: asked once more, then given up, not waited on for minutes
    assert len(_paths(fake, "DELETE")) == 2 and len(fake.sleeps) == 1 and "zkey-good" in fake.keys
    zc.revoke()
    assert "zkey-good" not in fake.keys
    zc.revoke()  # Zotero no longer knows the key: nothing raised
    assert _paths(fake, "DELETE") == ["/keys/current"] * 4


# --- the fake's Zotero rules -----------------------------------------------------------------

def test_annotations_are_refused_as_zotero_refuses_them(fake, zc, made):
    keys = made["keys"]
    pdf, number = keys["pdf"], 123
    refusals = [
        (_annotation(pdf, "image", annotationText=""),
         "'annotationText' can only be set for highlight and underline annotations"),
        (_annotation(pdf, annotationColor="#FFD400"), "annotationColor must be a hex color (e.g., '#FF0000')"),
        (_annotation(pdf, annotationSortIndex="0|1|2"), "Invalid sortIndex '0|1|2'"),
        (_annotation(pdf, annotationPageLabel="x" * 51), f"Annotation page label is too long for attachment {pdf}"),
        (_annotation(pdf, "ink", annotationPosition=json.dumps({"pageIndex": 0, "width": 2,
                                                                "paths": [[100.123] * 8000]})),
         f"Annotation position is too long for attachment {pdf}"),
        (_annotation(keys["attention"]),
         f"Parent item {number}/{keys['attention']} of annotation must be a file attachment"),
        (_annotation(keys["link"]), f"Parent item {number}/{keys['link']} of annotation must be a file attachment"),
    ]
    for data, message in refusals:
        assert _failure(zc, data) == (400, message)
    assert _failure(zc, _annotation(keys["snapshot"], "image"))[0] == 400  # a picture needs a PDF
    _create(zc, _annotation(keys["snapshot"], "highlight"), "on a snapshot")  # a highlight may sit there
    key = _create(zc, _annotation(pdf, annotationText="x" * 8000), "long")
    assert len(fake.data(LIB, key)["annotationText"]) == 7500  # cut, as Zotero cuts it
    changed = zc.write(LIB, "items", [{"key": key, "version": fake.data(LIB, key)["version"],
                                       "annotationType": "underline"}])
    assert changed.failed[0] == {"key": key, "code": 400,
                                 "message": f"Cannot change existing annotationType for item {number}/{key}"}
    untyped = _annotation(pdf)
    del untyped["annotationType"]
    with pytest.raises(ZoteroError) as crashed:  # the whole request fails
        zc.write(LIB, "items", [_annotation(pdf), untyped])
    assert crashed.value.status == 500 and "An error occurred" in str(crashed.value)


def test_an_annotation_or_an_attachment_may_move_to_another_parent(fake, zc, made):
    keys = made["keys"]
    highlight = fake.data(LIB, keys["highlight"])
    moved = zc.write(LIB, "items", [{"key": keys["highlight"], "version": highlight["version"],
                                     "parentItem": keys["supplement"]}])
    assert set(moved.successful) == {0} and fake.data(LIB, keys["highlight"])["parentItem"] == keys["supplement"]
    assert zc.write(LIB, "items", [{"key": keys["highlight"], "version": moved.version,
                                    "parentItem": keys["link"]}]).failed[0]["code"] == 400
    standalone = fake.data(LIB, keys["standalone_pdf"])
    assert standalone["collections"] == [keys["ml"]]
    filed = zc.write(LIB, "items", [{"key": keys["standalone_pdf"], "version": standalone["version"],
                                     "parentItem": keys["thesis"]}])
    data = fake.data(LIB, keys["standalone_pdf"])
    assert set(filed.successful) == {0} and data["parentItem"] == keys["thesis"] and "collections" not in data


def test_a_key_without_notes_access_sees_no_note_and_writes_none(monkeypatch):
    fake = FakeZotero(access={"user": {"library": True, "files": True, "write": True}}).install(monkeypatch)
    keys = standard_library(fake)["keys"]
    zc = ZoteroClient(fake.api_key)
    notes = {keys["note"], keys["image_note"], keys["standalone_note"]}
    for path in ("items", "items/top"):
        assert not notes & set(zc.versions(LIB, path, 0)[0])
    assert zc.versions(LIB, "items", 0, {"itemType": "note"})[0] == {}
    assert not notes & {c["key"] for c in zc.children(LIB, keys["attention"])}
    assert zc.objects(LIB, "items", sorted(notes)) == []
    with pytest.raises(ZoteroNotFound):
        zc.request("GET", f"/{LIB}/items/{keys['note']}")
    assert PICTURE_KEY in zc.versions(LIB, "items", 0, {"itemType": "attachment"})[0]  # a picture is no note
    refused = zc.write(LIB, "items", [{"itemType": "note", "parentItem": keys["thesis"], "note": "<p>new</p>"},
                                      {"key": keys["note"], "version": 1, "note": "<p>edited</p>"},
                                      {"itemType": "book", "title": "Still fine"}])
    assert [refused.failed[i]["code"] for i in (0, 1)] == [403, 403] and set(refused.successful) == {2}
    assert zc.libraries()[0]["notes"] is False


def test_tags_keep_their_type_and_a_manual_ones_goes_unsaid(fake, zc):
    sent = [{"tag": "mine", "type": 0}, {"tag": "auto", "type": 1}, {"tag": "plain"}]
    key = _create(zc, {"itemType": "book", "title": "Tagged", "tags": sent})
    assert fake.data(LIB, key)["tags"] == [{"tag": "mine"}, {"tag": "auto", "type": 1}, {"tag": "plain"}]
    data = fake.data(LIB, key)
    same = zc.write(LIB, "items", [{"key": key, "version": data["version"], "tags": sent}])
    assert same.unchanged == {0: key}  # what it holds, said Gamma's way
    zc.write(LIB, "items", [{"key": key, "version": data["version"], "tags": [{"tag": "auto", "type": 1}]}])
    assert fake.data(LIB, key)["tags"] == [{"tag": "auto", "type": 1}]  # a PATCH of tags replaces them all


def test_a_desktop_trashes_a_collection_with_its_subcollections_and_their_items_keep_them(fake, zc, made):
    keys = made["keys"]
    family = [keys["ml"], keys["transformers"], keys["efficient"]]
    items = {k: fake.data(LIB, k) for k in (keys["attention"], keys["conference"], keys["ppo"])}
    before = fake.version(LIB)
    fake.trash(LIB, keys["ml"])
    assert fake.version(LIB) == before + 1  # one change
    assert [fake.data(LIB, k).get("deleted") for k in family] == [True, True, True]
    assert "deleted" not in fake.data(LIB, keys["reading"])
    assert {k: fake.data(LIB, k) for k in items} == items  # members and versions untouched
    assert set(zc.versions(LIB, "collections", before)[0]) == set(family)
    assert zc.versions(LIB, "items", before)[0] == {}
    fake.restore(LIB, keys["ml"])
    assert all("deleted" not in fake.data(LIB, k) for k in family)
    ml = fake.data(LIB, keys["ml"])
    zc.write(LIB, "collections", [{"key": keys["ml"], "version": ml["version"], "deleted": True}])
    assert "deleted" not in fake.data(LIB, keys["transformers"])  # a write flags only what it names
    with pytest.raises(ZoteroError) as unversioned:
        zc.request("PATCH", f"/{LIB}/collections/{keys['reading']}", json_body={"name": "x"}, expect=(204,))
    assert unversioned.value.status == 428


def test_an_embedded_picture_belongs_under_a_note(fake, zc, made):
    keys = made["keys"]
    picture = {"itemType": "attachment", "linkMode": "embedded_image", "contentType": "image/png"}
    assert _failure(zc, {**picture, "parentItem": keys["attention"]}) == (
        400, "An embedded image must be the child of a note")
    assert _failure(zc, picture)[0] == 400
    _create(zc, {**picture, "parentItem": keys["note"]}, "a second picture")


def test_a_write_sent_with_the_library_version_is_refused_whole_when_the_library_moved(fake, zc, made):
    keys = made["keys"]
    now = fake.version(LIB)
    headers = {"If-Unmodified-Since-Version": str(now)}
    body = [{"key": keys["thesis"], "title": "Without its own version"}]
    resp = zc.request("POST", f"/{LIB}/items", json_body=body, headers=headers)
    assert set(json.loads(resp.body)["successful"]) == {"0"}  # the library's version stands for the object's
    with pytest.raises(ZoteroConflict) as moved:
        zc.request("POST", f"/{LIB}/items", json_body=body, headers=headers)
    assert f"Library has been modified since specified version (expected {now}, found {now + 1})" in str(moved.value)


def test_one_object_is_patched_by_its_version(fake, zc, made):
    keys = made["keys"]
    thesis = fake.data(LIB, keys["thesis"])
    path = f"/{LIB}/items/{keys['thesis']}"
    with pytest.raises(ZoteroError) as unversioned:
        zc.request("PATCH", path, json_body={"title": "No version"}, expect=(204,))
    assert unversioned.value.status == 428
    with pytest.raises(ZoteroConflict) as stale:
        zc.request("PATCH", path, json_body={"title": "Stale"}, expect=(204,),
                   headers={"If-Unmodified-Since-Version": str(thesis["version"] - 1)})
    assert (f"Item has been modified since specified version (expected {thesis['version'] - 1}, "
            f"found {thesis['version']})") in str(stale.value)
    resp = zc.request("PATCH", path, json_body={"title": "Patched", "deleted": True}, expect=(204,),
                      headers={"If-Unmodified-Since-Version": str(thesis["version"])})
    assert resp.headers["last-modified-version"] == str(fake.version(LIB))
    assert fake.data(LIB, keys["thesis"])["title"] == "Patched" and fake.data(LIB, keys["thesis"])["deleted"] == 1
    zc.request("PATCH", path, json_body={"version": fake.version(LIB), "deleted": 0}, expect=(204,))
    assert "deleted" not in fake.data(LIB, keys["thesis"])
    ml = fake.data(LIB, keys["ml"])
    zc.request("PATCH", f"/{LIB}/collections/{keys['ml']}", json_body={"name": "Machine learning"}, expect=(204,),
               headers={"If-Unmodified-Since-Version": str(ml["version"])})
    assert fake.data(LIB, keys["ml"])["name"] == "Machine learning"


def test_reads_answer_304_and_versions_and_negations_leave_out_annotations(fake, zc, made):
    keys = made["keys"]
    now = fake.version(LIB)
    since = {"If-Modified-Since-Version": str(now)}
    for path in (f"/{LIB}/items", f"/{LIB}/items/top", f"/{LIB}/collections", f"/{LIB}/items/{keys['pdf']}"):
        resp = zc.request("GET", path, headers=since, expect=(200, 304))
        assert resp.status == 304 and resp.body == b""
    assert zc.get_json(f"/{LIB}/collections", headers=since, expect=(200, 304))[0] is None
    for path in ("items", "items/top", "collections", f"items/{keys['pdf']}/children", "collections/top"):
        found, _ = zc.get_json(f"/{LIB}/{path}", {"format": "versions"})
        assert isinstance(found, dict) and all(isinstance(v, int) for v in found.values()), path
    keys_only = zc.request("GET", f"/{LIB}/collections/top", params={"format": "keys"}).body.decode().split()
    assert set(keys_only) == {keys["ml"], keys["reading"]}
    every, _ = zc.get_json(f"/{LIB}/items", {"format": "versions"})
    no_attachments, _ = zc.get_json(f"/{LIB}/items", {"format": "versions", "itemType": "-attachment"})
    annotations = {k for k, _ in zc.versions(LIB, "items", 0, {"itemType": "annotation"})[0].items()}
    attachments = set(zc.versions(LIB, "items", 0, {"itemType": "attachment"})[0])
    assert set(no_attachments) == set(every) - attachments - annotations
    both, _ = zc.get_json(f"/{LIB}/items", {"format": "versions", "itemType": "book || thesis"})
    assert set(both) == {keys["deep_learning"], keys["thesis"]}


def test_what_a_create_sends_is_kept_and_every_field_of_the_type_is_there(fake, zc):
    key = _create(zc, {"itemType": "journalArticle", "title": "Dated", "dateAdded": "2020-01-02T03:04:05Z",
                       "dateModified": "2021-01-02T03:04:05Z",
                       "tags": [{"tag": "manual"}, {"tag": "automatic", "type": 1}]})
    data = fake.data(LIB, key)
    assert (data["dateAdded"], data["dateModified"]) == ("2020-01-02T03:04:05Z", "2021-01-02T03:04:05Z")
    assert data["tags"] == [{"tag": "manual"}, {"tag": "automatic", "type": 1}]
    assert data["publicationTitle"] == "" and data["DOI"] == "" and data["creators"] == []
    assert data["collections"] == [] and "parentItem" not in data
    assert _failure(zc, {"itemType": "book", "publicationTitle": "No such field"}) == (
        400, "'publicationTitle' is not a valid field for type 'book'")
    assert _failure(zc, {"itemType": "book", "collections": ["ABCD2345"]}) == (409, "Collection ABCD2345 doesn't exist")


def test_group_objects_name_their_members_and_an_annotation_is_its_creators(fake, zc, made):
    keys, group = made["keys"], made["group"]
    carol = fake.add_user(CAROL, "carol")
    as_carol = ZoteroClient(carol)
    mine = zc.objects(group, "items", [keys["mine"]])[0]
    assert mine["meta"]["createdByUser"]["username"] == "tim" and "lastModifiedByUser" not in mine["meta"]
    item = fake.data(group, keys["group_item"])
    as_carol.write(group, "items", [{"key": keys["group_item"], "version": item["version"], "title": "Carol's"}])
    edited = zc.objects(group, "items", [keys["group_item"]])[0]["meta"]
    assert edited["createdByUser"]["id"] == 123 and edited["lastModifiedByUser"]["id"] == CAROL
    carols = fake.data(group, keys["carols"])
    refused = zc.write(group, "items", [{"key": keys["carols"], "version": carols["version"],
                                         "annotationComment": "not mine to change"}])
    assert refused.failed[0]["code"] == 403
    assert set(zc.trash(group, "items", [(keys["carols"], carols["version"])]).successful) == {0}  # the owner may
    with fake.acting_as(CAROL):
        fake.restore(group, keys["carols"])
    assert set(as_carol.write(group, "items", [{"key": keys["carols"], "version": fake.version(group),
                                                "annotationComment": "my own"}]).successful) == {0}
    assert zc.objects(LIB, "items", [keys["highlight"]])[0]["meta"] == {}  # My Library names no one


def test_a_change_can_land_while_a_sync_walks_the_library(fake, zc, made):
    keys = made["keys"]
    start = fake.version(LIB)
    fake.on_request("itemType=attachment", lambda: fake.edit(LIB, keys["thesis"], title="Meanwhile"))
    top, seen = zc.versions(LIB, "items/top", 0)
    attachments, later = zc.versions(LIB, "items", 0, {"itemType": "attachment"})
    assert seen == start and later == start + 1  # the walk sees the library move
    assert top[keys["thesis"]] < fake.data(LIB, keys["thesis"])["version"]
    zc.versions(LIB, "items", 0, {"itemType": "attachment"})
    assert fake.version(LIB) == start + 1  # once


# --- the real transport ---------------------------------------------------------------------

@pytest.fixture
def served():
    fake = FakeZotero()
    httpd, base = fake.serve()
    yield fake
    httpd.shutdown()
    httpd.server_close()


def test_the_real_transport_against_a_served_fake(served, tmp_path):
    keys = standard_library(served)["keys"]
    zc = ZoteroClient(served.api_key, base=served.base)
    assert zc.key_info()["username"] == "tim"
    sink = io.BytesIO()  # the 302 comes back unfollowed and the storage is fetched without the key
    zc.download(LIB, keys["pdf"], sink, md5=served.data(LIB, keys["pdf"])["md5"])
    assert sink.getvalue() == pdf_bytes()
    data = pdf_bytes("Streamed through urllib.")  # a streamed upload body (prefix + file + suffix) arrives whole
    path = tmp_path / "streamed.pdf"
    path.write_bytes(data)
    key = _attachment(zc, "served")
    assert _upload(zc, key, path, data) == served.version(LIB)
    assert served.libraries[LIB].files[key] == data
    assert zc.library_version(LIB, served.version(LIB)) is None  # a 304 over the wire
    with pytest.raises(ZoteroNotFound):
        zc.request("GET", f"/{LIB}/items/ABCD2345")


def test_an_unreachable_zotero_is_a_sentence_without_a_status(monkeypatch):
    slept = []
    monkeypatch.setattr(zotero_web, "_sleep", slept.append)
    monkeypatch.setattr(zotero_web, "MAX_RETRIES", 1)
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    with pytest.raises(ZoteroError) as unreachable:
        ZoteroClient("zkey-good", base=f"http://127.0.0.1:{port}").key_info()
    assert unreachable.value.status is None
    assert isinstance(unreachable.value, ZoteroUnreachable)
    assert str(unreachable.value).startswith("Zotero could not be reached: ")
    assert "zkey-good" not in str(unreachable.value)
    assert slept == [1]  # a GET is asked again before it fails
