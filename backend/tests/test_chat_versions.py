"""Chats don't overwrite each other: the active conversation's updated_at is
its version, a save names the version its copy is based on, and one made
from an older copy is refused with the stored conversation (409) for the
client to merge (chat/chatSession.js). "New chat" or opening an entry from
an older copy archives the newer stored conversation instead of dropping
it."""

import pytest

from conftest import login, make_user


def _msgs(*texts, who="A"):
    return [{"id": f"{who}{i}", "role": "user" if i % 2 == 0 else "ai", "text": t} for i, t in enumerate(texts)]


@pytest.fixture(scope="module")
def tabs(client):
    """Two tabs of one account (one client does for the server)."""
    make_user("cv_user", "pw")
    return login("cv_user", "pw")


def _sessions(c, bucket):
    return c.get("/api/chat-history", params={"bucket": bucket}).json()["sessions"]


def test_a_save_from_an_older_copy_is_refused_with_the_stored_one(tabs):
    c, key = tabs, "home:cv/save"
    first = c.get(f"/api/chats/{key}").json()
    assert first == {"messages": [], "title": "", "updated_at": ""}
    # Tab A saves after reading "no conversation"; tab B read the same.
    a = _msgs("A: question 1", "A: long answer 1")
    r = c.put(f"/api/chats/{key}", json={"messages": a, "updated_at": ""})
    assert r.status_code == 200 and r.json()["updated_at"]
    version = r.json()["updated_at"]
    assert c.get(f"/api/chats/{key}").json()["updated_at"] == version
    b = _msgs("B: question", "B: answer", who="B")
    r = c.put(f"/api/chats/{key}", json={"messages": b, "updated_at": ""})
    assert r.status_code == 409
    assert r.json()["messages"] == a and r.json()["updated_at"] == version
    assert c.get(f"/api/chats/{key}").json()["messages"] == a  # nothing changed
    # B merges and saves against the version it now knows.
    r = c.put(f"/api/chats/{key}", json={"messages": a + b, "updated_at": version})
    assert r.status_code == 200 and r.json()["updated_at"] > version
    # A's own next checkpoint is based on its last write: refused, not lost.
    r = c.put(f"/api/chats/{key}", json={"messages": a + _msgs("A: q2"), "updated_at": version})
    assert r.status_code == 409 and r.json()["messages"] == a + b
    # A save without a version is unconditional (older clients).
    assert c.put(f"/api/chats/{key}", json={"messages": a + b}).status_code == 200


def test_every_save_moves_the_version_on(tabs):
    c, key = tabs, "home:cv/versions"
    at = ""
    for i in range(5):
        r = c.put(f"/api/chats/{key}", json={"messages": _msgs(f"q{i}"), "updated_at": at})
        assert r.status_code == 200, r.text
        assert r.json()["updated_at"] > at
        at = r.json()["updated_at"]


def test_a_rename_changes_only_the_title(tabs):
    c, key = tabs, "home:cv/rename"
    at = c.put(f"/api/chats/{key}", json={"messages": _msgs("hello"), "updated_at": ""}).json()["updated_at"]
    r = c.put(f"/api/chats/{key}", json={"title": "Named"})
    assert r.status_code == 200 and r.json()["updated_at"] == at
    stored = c.get(f"/api/chats/{key}").json()
    assert stored == {"messages": _msgs("hello"), "title": "Named", "updated_at": at}
    # The next autosave from the same copy still goes through.
    assert c.put(f"/api/chats/{key}", json={"messages": _msgs("hello", "hi"), "updated_at": at}).status_code == 200


def test_new_chat_from_an_older_copy_archives_the_newer_one_too(tabs):
    c, key = tabs, "home:cv/archive"
    stale = _msgs("A: question")
    at = c.put(f"/api/chats/{key}", json={"messages": stale, "updated_at": ""}).json()["updated_at"]
    newer = stale + _msgs("B: follow-up", "B: answer", who="B")
    c.put(f"/api/chats/{key}", json={"messages": newer, "updated_at": at})
    # Tab A, still on its old copy, starts a new chat: both copies are kept —
    # here the stored one holds all of A's, so it alone goes to history.
    r = c.post("/api/chat-history/archive", json={"bucket": key, "messages": stale, "updated_at": at})
    assert r.status_code == 200 and r.json()["id"]
    assert c.get(f"/api/chats/{key}").json()["messages"] == []
    (entry,) = _sessions(c, key)
    assert entry["count"] == len(newer)
    # Two copies that each hold something the other lacks are both kept.
    key = "home:cv/archive-both"
    base = _msgs("shared question")
    at = c.put(f"/api/chats/{key}", json={"messages": base, "updated_at": ""}).json()["updated_at"]
    c.put(f"/api/chats/{key}", json={"messages": base + _msgs("B only", who="B"), "updated_at": at})
    c.post("/api/chat-history/archive", json={"bucket": key, "messages": base + _msgs("A only", who="C"),
                                               "updated_at": at})
    assert sorted(s["count"] for s in _sessions(c, key)) == [2, 2]
    # A client whose copy is current archives just its copy, as before.
    key = "home:cv/archive-current"
    at = c.put(f"/api/chats/{key}", json={"messages": base, "updated_at": ""}).json()["updated_at"]
    c.post("/api/chat-history/archive", json={"bucket": key, "messages": base + _msgs("unsaved tail", who="D"),
                                               "updated_at": at})
    assert [s["count"] for s in _sessions(c, key)] == [2]


def test_opening_an_entry_from_an_older_copy_keeps_the_newer_one(tabs):
    c, key = tabs, "home:cv/open"
    entry = c.post("/api/chat-history/archive", json={"bucket": key, "messages": _msgs("old convo")}).json()["id"]
    at = c.put(f"/api/chats/{key}", json={"messages": _msgs("mine"), "updated_at": ""}).json()["updated_at"]
    c.put(f"/api/chats/{key}", json={"messages": _msgs("mine") + _msgs("theirs", who="T"), "updated_at": at})
    r = c.post(f"/api/chat-history/{entry}/open", json={"bucket": key, "messages": _msgs("mine"), "updated_at": at})
    assert r.status_code == 200
    opened = r.json()
    assert opened["messages"] == _msgs("old convo") and opened["updated_at"]
    assert c.get(f"/api/chats/{key}").json()["updated_at"] == opened["updated_at"]
    assert [s["count"] for s in _sessions(c, key)] == [2]  # the newer stored copy, not the stale one
