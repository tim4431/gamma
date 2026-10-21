"""Comments (gamma/comments.py, routers/comments.py, docs/dev/comments.md):
threads on a note, a page and a highlight, replies, edits, deletes and
tombstones, resolve and reopen; who may write them (the permission table
row by row); a comment following its block — hidden in Recently deleted,
orphaned by a delete and brought back by an undo, swept seven days on,
moved with its block, purged with its page, through the subtree replace,
the backup merge and the Gamma export; the room's nudge; a client id
repeated. The suite's drift checks run after every test as always: a
comment write touches no page."""

import io
import sqlite3
import tempfile
import zipfile
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from conftest import account_of, drop_user, login, make_page, make_user, recv, recv_hello, workspace_of
from fastapi import HTTPException

from gamma import comments
from gamma.blocks_store import create_page
from gamma.db import connect_pages_db


@pytest.fixture(scope="module")
def own():
    make_user("cm_own", "cmownpw123")
    return login("cm_own", "cmownpw123")


@pytest.fixture(scope="module")
def edi():
    make_user("cm_edi", "cmedipw123")
    return login("cm_edi", "cmedipw123")


@pytest.fixture(scope="module")
def vie():
    make_user("cm_vie", "cmviepw123")
    return login("cm_vie", "cmviepw123")


@pytest.fixture(scope="module")
def out():
    """An account that is no member of the lab: what a share admits."""
    make_user("cm_out", "cmoutpw123")
    return login("cm_out", "cmoutpw123")


@pytest.fixture(scope="module")
def lab(own, edi, vie):
    """A shared workspace: cm_own owns it, cm_edi edits, cm_vie views."""
    from gamma import workspaces
    info = workspaces.create("Comment lab", account_of("cm_own"), kind="shared")
    workspaces.set_member(info["id"], account_of("cm_edi"), "editor", by=account_of("cm_own"))
    workspaces.set_member(info["id"], account_of("cm_vie"), "viewer", by=account_of("cm_own"))
    return info["id"]


def _in(ws):
    return {"X-Gamma-Workspace": ws}


def _block(client, parent_id, content, ws=None):
    r = client.post("/api/blocks", json={"parent_id": parent_id, "content": content}, headers=_in(ws) if ws else None)
    assert r.status_code == 200, r.text
    return r.json()


def _post(who, page_id, block_id, body, *, ws=None, params=None, **more):
    return who.post(f"/api/pages/{page_id}/comments", json={"block_id": block_id, "body": body, **more},
                       headers=_in(ws) if ws else None, params=params)


def _ok(r, status=201):
    assert r.status_code == status, r.text
    return r.json()


def _threads(client, page_id, *, ws=None, params=None, state=None):
    query = {**(params or {}), **({"state": state} if state else {})}
    return _ok(client.get(f"/api/pages/{page_id}/comments", headers=_in(ws) if ws else None, params=query), 200)["threads"]


def _ops(client, page_id, ops, ws=None, client_id="t"):
    r = client.post(f"/api/pages/{page_id}/ops", json={"client": client_id, "ops": ops},
                    headers=_in(ws) if ws else None)
    assert r.status_code == 200, r.text
    return r.json()


def _rows(ws, where="1", args=()):
    with connect_pages_db(ws) as conn:
        return conn.execute(f"SELECT id, thread_id, block_id, page_id, orphaned_at FROM comments WHERE {where}",
                            args).fetchall()


def _share(owner, page_id, **settings):
    r = owner.post(f"/api/share/{page_id}")
    assert r.status_code == 200, r.text
    token = r.json()["token"]
    if settings:
        assert owner.put(f"/api/share-settings/{page_id}", json=settings).status_code == 200
    return token


def _changes(ws):
    with connect_pages_db(ws) as conn:
        return conn.execute("SELECT MAX(seq) FROM page_changes").fetchone()[0]


# --- threads ------------------------------------------------------------------------

def test_threads_replies_edits_and_resolve(own):
    ws = workspace_of("cm_own")
    page = make_page(own, "Threads page")
    note = _block(own, page["id"], "a note")
    before = _changes(ws)
    first = _ok(_post(own, page["id"], note["id"], "Is this right? @cm_edi"))
    thread = first["thread"]
    assert first["comment"]["author"] == "cm_own" and first["comment"]["deleted"] is False
    assert thread["id"] == first["comment"]["id"] == first["comment"]["thread_id"]
    assert (thread["block_id"], thread["page_id"], thread["resolved_at"]) == (note["id"], page["id"], None)
    on_page = _ok(_post(own, page["id"], page["id"], "About the whole page"))
    reply = _ok(_post(own, page["id"], note["id"], "Yes", thread_id=thread["id"]))
    assert reply["thread"]["id"] == thread["id"] and [c["body"] for c in reply["thread"]["comments"]] == [
        "Is this right? @cm_edi", "Yes"]

    got = _threads(own, page["id"])
    assert [(t["id"], t["block_id"]) for t in got] == [(thread["id"], note["id"]), (on_page["thread"]["id"], page["id"])]
    assert got[0]["last_at"] == reply["comment"]["created_at"]

    edited = _ok(own.patch(f"/api/comments/{reply['comment']['id']}", json={"body": "Yes, see p. 3"}), 200)["comment"]
    assert edited["body"] == "Yes, see p. 3" and edited["updated_at"] > edited["created_at"]
    same = _ok(own.patch(f"/api/comments/{reply['comment']['id']}", json={"body": "Yes, see p. 3"}), 200)["comment"]
    assert same["updated_at"] == edited["updated_at"]  # no change, no new stamp

    resolved = _ok(own.post(f"/api/comments/{thread['id']}/resolve", json={"resolved": True}), 200)["thread"]
    assert resolved["resolved_at"] and resolved["resolved_by"] == "cm_own"
    assert [t["id"] for t in _threads(own, page["id"], state="open")] == [on_page["thread"]["id"]]
    assert [t["id"] for t in _threads(own, page["id"], state="resolved")] == [thread["id"]]
    reopened = _ok(own.post(f"/api/comments/{thread['id']}/resolve", json={"resolved": False}), 200)["thread"]
    assert (reopened["resolved_at"], reopened["resolved_by"]) == (None, None)
    assert own.post(f"/api/comments/{reply['comment']['id']}/resolve", json={"resolved": True}).status_code == 400
    assert _changes(ws) == before  # no comment write touches a page (the change feed, the home order)


def test_delete_leaves_a_tombstone_while_replies_remain(own):
    page = make_page(own, "Tombstone page")
    note = _block(own, page["id"], "note")
    root = _ok(_post(own, page["id"], note["id"], "root"))["thread"]["id"]
    reply = _ok(_post(own, page["id"], note["id"], "reply", thread_id=root))["comment"]["id"]
    assert _ok(own.delete(f"/api/comments/{root}"), 200) == {"deleted": True}
    [thread] = _threads(own, page["id"])
    assert [(c["id"], c["body"], c["deleted"]) for c in thread["comments"]] == [(root, "", True), (reply, "reply", False)]
    assert own.patch(f"/api/comments/{root}", json={"body": "back"}).status_code == 404
    assert own.delete(f"/api/comments/{root}").status_code == 404
    # the last reply takes the tombstone with it
    _ok(own.delete(f"/api/comments/{reply}"), 200)
    assert _threads(own, page["id"]) == [] and _rows(workspace_of("cm_own"), "block_id = ?", (note["id"],)) == []
    # a root alone goes at once
    alone = _ok(_post(own, page["id"], note["id"], "alone"))["thread"]["id"]
    _ok(own.delete(f"/api/comments/{alone}"), 200)
    assert _threads(own, page["id"]) == []


def test_validation(own):
    page = make_page(own, "Validation page")
    other = make_page(own, "Another page")
    note = _block(own, page["id"], "note")
    elsewhere = _block(own, other["id"], "elsewhere")
    root = _ok(_post(own, page["id"], note["id"], "root"))["thread"]["id"]
    reply = _ok(_post(own, page["id"], note["id"], "reply", thread_id=root))["comment"]["id"]
    assert _post(own, page["id"], note["id"], "   ").status_code == 400
    assert _post(own, page["id"], note["id"], "x" * comments.MAX_BODY).status_code == 201
    assert _post(own, page["id"], note["id"], "x" * (comments.MAX_BODY + 1)).status_code == 413
    assert _post(own, page["id"], elsewhere["id"], "not here").status_code == 400
    assert _post(own, page["id"], "nosuchblock", "gone").status_code == 400
    assert _post(own, page["id"], note["id"], "into a reply", thread_id=reply).status_code == 400
    assert _post(own, page["id"], page["id"], "wrong block", thread_id=root).status_code == 400
    assert _post(own, page["id"], note["id"], "bad id", id="no/slashes").status_code == 400
    assert _post(own, "nosuchpage", note["id"], "no page").status_code == 404
    assert own.get(f"/api/pages/{page['id']}/comments", params={"state": "later"}).status_code == 400
    assert own.patch("/api/comments/nosuchcomment", json={"body": "x"}).status_code == 404
    # half an emoji is stored as U+FFFD
    r = own.post(f"/api/pages/{page['id']}/comments", content=b'{"block_id": "%s", "body": "hi \\ud83d"}' % note["id"].encode(),
                 headers={"Content-Type": "application/json"})
    assert _ok(r)["comment"]["body"] == "hi �"


def test_a_repeated_client_id_is_answered_as_stored(own, edi, lab):
    page = _block(own, "root", "Retry page", ws=lab)
    first = _ok(_post(own, page["id"], page["id"], "once", id="cmRetry1", ws=lab))
    again = _ok(_post(own, page["id"], page["id"], "once, retried", id="cmRetry1", ws=lab))
    assert again["comment"] == first["comment"] and again["thread"]["comments"] == first["thread"]["comments"]
    assert len(_rows(lab, "id = 'cmRetry1'")) == 1
    assert _post(edi, page["id"], page["id"], "someone else's id", id="cmRetry1", ws=lab).status_code == 409
    # the same author's id on another page is taken there: a retry answers no thread off the page asked for
    other = _block(own, "root", "Another retry page", ws=lab)
    assert _post(own, other["id"], other["id"], "elsewhere", id="cmRetry1", ws=lab).status_code == 409
    # hidden since (its page in Recently deleted): 404, and answered again once it is back
    assert own.delete(f"/api/blocks/{page['id']}", headers=_in(lab)).status_code == 200
    assert _post(own, page["id"], page["id"], "once", id="cmRetry1", ws=lab).status_code == 404
    assert own.post(f"/api/trash/{page['id']}/restore", headers=_in(lab)).status_code == 200
    assert _ok(_post(own, page["id"], page["id"], "once", id="cmRetry1", ws=lab))["comment"] == first["comment"]


def test_a_retry_through_a_share_reads_no_other_page(own, out):
    """A share visitor's own comment on page B, retried on page A that
    another share admits: 409, not B's thread (with the owner's reply)."""
    a = make_page(own, "Retry share A")
    b = make_page(own, "Retry share B")
    share_a = {"share": _share(own, a["id"], audience="anyone", role="view")}
    share_b = {"share": _share(own, b["id"], audience="anyone", role="view")}
    mine = _ok(_post(out, b["id"], b["id"], "on B", id="cmShRetry1", params=share_b))["thread"]["id"]
    _ok(_post(own, b["id"], b["id"], "SECRET", thread_id=mine))
    r = _post(out, a["id"], a["id"], "on B", id="cmShRetry1", params=share_a)
    assert r.status_code == 409 and "SECRET" not in r.text
    # B's share stopped: B is out of reach, and A still answers 409
    assert own.delete(f"/api/share-settings/{b['id']}").status_code == 200
    assert out.get(f"/api/pages/{b['id']}/comments", params=share_b).status_code in (401, 403, 404)
    r = _post(out, a["id"], a["id"], "on B", id="cmShRetry1", params=share_a)
    assert r.status_code == 409 and "SECRET" not in r.text


def test_a_write_checks_the_authorized_page_under_the_lock(own):
    """The router reads the comment's page before the lock; the writers
    refuse once the comment is on another page by the time they hold it."""
    page = make_page(own, "Authorized page")
    elsewhere = make_page(own, "Not authorized")
    root = _ok(_post(own, page["id"], page["id"], "here"))["thread"]["id"]
    ws = workspace_of("cm_own")
    me = account_of("cm_own")
    with connect_pages_db(ws) as conn:
        for write in (lambda: comments.edit(conn, root, page_id=elsewhere["id"], body="x", author=me),
                      lambda: comments.resolve(conn, root, page_id=elsewhere["id"], resolved=True, by=me),
                      lambda: comments.delete(conn, root, page_id=elsewhere["id"], user=me)):
            with pytest.raises(HTTPException) as e:
                write()
            assert e.value.status_code == 404
    [thread] = _threads(own, page["id"])
    assert thread["resolved_at"] is None and thread["comments"][0]["body"] == "here"


def test_after_write_hears_every_kind(own, monkeypatch):
    ws = workspace_of("cm_own")
    heard = []
    monkeypatch.setattr(comments, "after_write", lambda w, event: heard.append((w, event)))
    page = make_page(own, "Events page")
    note = _block(own, page["id"], "note")
    root = _ok(_post(own, page["id"], note["id"], "first"))["thread"]["id"]
    reply = _ok(_post(own, page["id"], note["id"], "second", thread_id=root))["comment"]["id"]
    _ok(_post(own, page["id"], note["id"], "first", id=root))  # a retry: no event
    _ok(own.patch(f"/api/comments/{reply}", json={"body": "second, edited"}), 200)
    _ok(own.patch(f"/api/comments/{reply}", json={"body": "second, edited"}), 200)  # no change: no event
    _ok(own.post(f"/api/comments/{root}/resolve", json={"resolved": True}), 200)
    _ok(own.post(f"/api/comments/{root}/resolve", json={"resolved": True}), 200)  # already: no event
    _ok(own.post(f"/api/comments/{root}/resolve", json={"resolved": False}), 200)
    _ok(own.delete(f"/api/comments/{reply}"), 200)
    me = account_of("cm_own")
    # ``via``: the share the writer came through, "" for a member
    base = {"actor": me, "page_id": page["id"], "block_id": note["id"], "thread_id": root, "via": ""}
    assert heard == [
        (ws, {"kind": "create", **base, "comment_id": root, "body": "first"}),
        (ws, {"kind": "reply", **base, "comment_id": reply, "body": "second"}),
        (ws, {"kind": "edit", **base, "comment_id": reply, "body": "second, edited", "before": "second"}),
        (ws, {"kind": "resolve", **base, "comment_id": root, "thread_author": me}),
        (ws, {"kind": "reopen", **base, "comment_id": root, "thread_author": me}),
        (ws, {"kind": "delete", **base, "comment_id": reply, "comment_ids": [reply]}),
    ]
    heard.clear()
    _ops(own, page["id"], [{"op": "delete", "id": note["id"]}])
    comments.sweep(now=datetime.now(timezone.utc) + timedelta(days=8))
    [(_ws, event)] = [h for h in heard if h[0] == ws]
    assert root in event.pop("comment_ids") and event == {"kind": "sweep", "actor": "", "page_id": "", "block_id": "",
                                                          "thread_id": "", "comment_id": ""}


# --- who may comment ------------------------------------------------------------------

def test_members_by_role(own, edi, vie, lab):
    page = _block(own, "root", "Roles page", ws=lab)
    note = _block(edi, page["id"], "note", ws=lab)
    by_owner = _ok(_post(own, page["id"], note["id"], "owner's", ws=lab))["comment"]["id"]
    by_editor = _ok(_post(edi, page["id"], note["id"], "editor's", ws=lab))["thread"]["id"]
    by_viewer = _ok(_post(vie, page["id"], note["id"], "viewer's", ws=lab))["thread"]["id"]
    assert len(_threads(vie, page["id"], ws=lab)) == 3
    # edit your own only
    assert edi.patch(f"/api/comments/{by_owner}", json={"body": "x"}, headers=_in(lab)).status_code == 403
    assert _ok(vie.patch(f"/api/comments/{by_viewer}", json={"body": "viewer's, edited"}, headers=_in(lab)), 200)
    # delete your own; the owner deletes any
    assert edi.delete(f"/api/comments/{by_viewer}", headers=_in(lab)).status_code == 403
    assert vie.delete(f"/api/comments/{by_editor}", headers=_in(lab)).status_code == 403
    assert _ok(own.delete(f"/api/comments/{by_editor}", headers=_in(lab)), 200)
    assert _ok(vie.delete(f"/api/comments/{by_viewer}", headers=_in(lab)), 200)
    # anyone who may comment resolves, viewers included
    resolved = _ok(vie.post(f"/api/comments/{by_owner}/resolve", json={"resolved": True}, headers=_in(lab)), 200)
    assert resolved["thread"]["resolved_by"] == "cm_vie"
    # no member, no comment
    make_user("cm_stranger", "cmstrangerpw1")
    stranger = login("cm_stranger", "cmstrangerpw1")
    assert _post(stranger, page["id"], note["id"], "hi", ws=lab).status_code == 403
    assert stranger.get(f"/api/pages/{page['id']}/comments", headers=_in(lab)).status_code == 403


def test_share_visitors(own, out, anon):
    page = make_page(own, "Shared comments page")
    note = _block(own, page["id"], "shared note")
    hidden = make_page(own, "Not shared")
    token = _share(own, page["id"], audience="anyone", role="view")
    share = {"share": token}
    root = _ok(_post(own, page["id"], note["id"], "from the owner"))["thread"]["id"]
    # a link visitor reads, and may not write
    assert [t["id"] for t in _threads(anon, page["id"], params=share)] == [root]
    assert _post(anon, page["id"], note["id"], "hello", params=share).status_code == 403
    assert anon.post(f"/api/comments/{root}/resolve", json={"resolved": True}, params=share).status_code == 403
    # a signed-in account the share admits (view) comments, edits and deletes its own, resolves
    mine = _ok(_post(out, page["id"], note["id"], "from outside", params=share))["comment"]
    assert mine["author"] == "cm_out"
    assert _ok(out.patch(f"/api/comments/{mine['id']}", json={"body": "edited"}, params=share), 200)
    assert out.delete(f"/api/comments/{root}", params=share).status_code == 403
    assert _ok(out.post(f"/api/comments/{root}/resolve", json={"resolved": True}, params=share), 200)
    assert _ok(out.delete(f"/api/comments/{mine['id']}", params=share), 200)
    # only within the share's reach
    assert _post(out, hidden["id"], hidden["id"], "out of reach", params=share).status_code == 403
    assert out.get(f"/api/pages/{hidden['id']}/comments", params=share).status_code == 403
    # an invited account on a list share, which admits no one else
    _share(own, page["id"], audience="list", role="view", users=[{"name": "cm_out", "role": "view"}])
    assert _post(out, page["id"], page["id"], "invited", params=share).status_code == 201
    assert anon.get(f"/api/pages/{page['id']}/comments", params=share).status_code == 401


def test_an_edit_share_and_a_guest_through_a_share(own, out, guest):
    page = make_page(own, "Edit-shared comments page")
    note = _block(own, page["id"], "note")
    share = {"share": _share(own, page["id"], audience="anyone", role="edit")}
    root = _ok(_post(own, page["id"], note["id"], "from the owner"))["thread"]["id"]
    # a signed-in account through an edit share: as through a view share
    mine = _ok(_post(out, page["id"], note["id"], "through an edit share", params=share))["comment"]["id"]
    assert out.patch(f"/api/comments/{root}", json={"body": "x"}, params=share).status_code == 403
    assert out.delete(f"/api/comments/{root}", params=share).status_code == 403
    assert _ok(out.post(f"/api/comments/{root}/resolve", json={"resolved": True}, params=share), 200)
    assert _ok(out.patch(f"/api/comments/{mine}", json={"body": "edited"}, params=share), 200)
    assert _ok(out.delete(f"/api/comments/{mine}", params=share), 200)
    # a guest account through a share link is a link visitor: it reads, and may not write
    assert [t["id"] for t in _threads(guest, page["id"], params=share)] == [root]
    r = _post(guest, page["id"], note["id"], "from a guest", params=share)
    assert r.status_code == 403 and "sign in" in r.text
    assert guest.post(f"/api/comments/{root}/resolve", json={"resolved": False}, params=share).status_code == 403


def test_guests_and_tokens(own, guest):
    from fastapi.testclient import TestClient

    from gamma.app import app
    from gamma.integrations import create_token

    gpage = make_page(guest, "Guest's own page")
    assert _post(guest, gpage["id"], gpage["id"], "a guest comments at home").status_code == 201

    page = make_page(own, "Token page")
    root = _ok(_post(own, page["id"], page["id"], "for the tokens"))["thread"]["id"]
    ws = workspace_of("cm_own")
    for scope, status in (("read", 403), ("write", 201)):
        bearer = TestClient(app)
        bearer.headers["Authorization"] = f"Bearer {create_token(account_of('cm_own'), ws, scope, 1, scope=scope)['token']}"
        assert [t["id"] for t in _threads(bearer, page["id"])] == [root]
        assert _post(bearer, page["id"], page["id"], f"by a {scope} token").status_code == status
        assert bearer.post(f"/api/comments/{root}/resolve", json={"resolved": True}).status_code == (
            403 if scope == "read" else 200)
        assert bearer.patch(f"/api/comments/{root}", json={"body": "x"}).status_code == (
            403 if scope == "read" else 200)


def test_a_write_token_edits_and_deletes_its_own_only(own, edi, lab):
    """The owner's write token: its own comments, never another's, though
    the owner signed in deletes any."""
    from fastapi.testclient import TestClient

    from gamma.app import app
    from gamma.integrations import create_token

    page = _block(own, "root", "Token rights page", ws=lab)
    theirs = _ok(_post(edi, page["id"], page["id"], "the editor's", ws=lab))["thread"]["id"]
    bearer = TestClient(app)
    bearer.headers["Authorization"] = f"Bearer {create_token(account_of('cm_own'), lab, 'w', 1, scope='write')['token']}"
    mine = _ok(_post(bearer, page["id"], page["id"], "the token's", ws=lab))["comment"]
    assert mine["author"] == "cm_own"
    assert bearer.patch(f"/api/comments/{theirs}", json={"body": "x"}, headers=_in(lab)).status_code == 403
    assert bearer.delete(f"/api/comments/{theirs}", headers=_in(lab)).status_code == 403
    assert _ok(bearer.patch(f"/api/comments/{mine['id']}", json={"body": "edited"}, headers=_in(lab)), 200)
    assert _ok(bearer.delete(f"/api/comments/{mine['id']}", headers=_in(lab)), 200)
    assert _ok(own.delete(f"/api/comments/{theirs}", headers=_in(lab)), 200)


# --- following the block --------------------------------------------------------------

def test_trash_hides_and_restore_shows(own):
    page = make_page(own, "Trashed comments page")
    root = _ok(_post(own, page["id"], page["id"], "on the page"))["thread"]["id"]
    assert own.delete(f"/api/blocks/{page['id']}").status_code == 200
    assert own.get(f"/api/pages/{page['id']}/comments").status_code == 404
    assert own.patch(f"/api/comments/{root}", json={"body": "x"}).status_code == 404
    assert _post(own, page["id"], page["id"], "no").status_code == 404
    assert own.post(f"/api/trash/{page['id']}/restore").status_code == 200
    assert [t["id"] for t in _threads(own, page["id"])] == [root]


def test_delete_for_good_purges_but_spares_a_moved_block(own):
    ws = workspace_of("cm_own")
    page = make_page(own, "Purged page")
    stays = make_page(own, "Keeps a moved note")
    note = _block(own, page["id"], "goes")
    moved = _block(own, page["id"], "moves away")
    _ok(_post(own, page["id"], note["id"], "goes with the page"))
    kept = _ok(_post(own, page["id"], moved["id"], "goes with the note"))["thread"]["id"]
    gone_note = _block(own, page["id"], "deleted first")
    _ok(_post(own, page["id"], gone_note["id"], "orphaned first"))
    _ops(own, page["id"], [{"op": "delete", "id": gone_note["id"]}])
    r = own.post(f"/api/blocks/{moved['id']}/reorder", json={"parent_id": stays["id"]})
    assert r.status_code == 200, r.text
    assert [t["id"] for t in _threads(own, stays["id"])] == [kept]
    assert _rows(ws, "id = ?", (kept,))[0][3] == stays["id"]  # its page_id followed it
    assert own.delete(f"/api/blocks/{page['id']}").status_code == 200
    assert own.delete(f"/api/trash/{page['id']}").status_code == 200
    assert _rows(ws, "page_id = ?", (page["id"],)) == []
    assert [t["id"] for t in _threads(own, stays["id"])] == [kept]


def test_create_page_over_a_trashed_copy_purges_its_comments(own):
    ws = workspace_of("cm_own")
    page = make_page(own, "Recreated page")
    note = _block(own, page["id"], "note")
    _ok(_post(own, page["id"], page["id"], "on the page"))
    _ok(_post(own, page["id"], note["id"], "on the note"))
    assert own.delete(f"/api/blocks/{page['id']}").status_code == 200
    assert len(_rows(ws, "page_id = ?", (page["id"],))) == 2
    with connect_pages_db(ws) as conn:
        create_page(conn, "Recreated page", actor=account_of("cm_own"), block_id=page["id"])
    assert _rows(ws, "page_id = ?", (page["id"],)) == [] and _threads(own, page["id"]) == []


def test_a_deleted_block_orphans_an_undo_brings_back_and_the_sweep_clears(own):
    ws = workspace_of("cm_own")
    page = make_page(own, "Orphan page")
    note = _block(own, page["id"], "parent note")
    child = _block(own, note["id"], "child note")
    root = _ok(_post(own, page["id"], child["id"], "on the child"))["thread"]["id"]
    _ops(own, page["id"], [{"op": "delete", "id": note["id"]}])
    assert _threads(own, page["id"]) == []
    [(_id, _thread, _block_id, _page, orphaned)] = _rows(ws, "id = ?", (root,))
    assert orphaned
    assert own.patch(f"/api/comments/{root}", json={"body": "x"}).status_code == 404
    # an undo inserts the blocks again under their ids: the thread is back
    _ops(own, page["id"], [{"op": "insert", "id": note["id"], "parent": page["id"], "content": "parent note"},
                           {"op": "insert", "id": child["id"], "parent": note["id"], "content": "child note"}])
    assert [t["id"] for t in _threads(own, page["id"])] == [root]
    assert _rows(ws, "id = ?", (root,))[0][4] is None
    # deleted again and left: the sweep takes it seven days on, not before
    _ops(own, page["id"], [{"op": "delete", "id": note["id"]}])
    now = datetime.now(timezone.utc)
    assert ws not in comments.sweep(now=now + timedelta(days=6)) and _rows(ws, "id = ?", (root,))
    assert comments.sweep(now=now + timedelta(days=8))[ws] >= 1
    assert _rows(ws, "id = ?", (root,)) == []


def test_an_insert_that_pulls_a_block_out_of_a_trashed_page(own):
    ws = workspace_of("cm_own")
    page = make_page(own, "Trashed holder")
    there = make_page(own, "New home")
    note = _block(own, page["id"], "pulled out")
    child = _block(own, note["id"], "left behind")
    pulled = _ok(_post(own, page["id"], note["id"], "follows the note"))["thread"]["id"]
    behind = _ok(_post(own, page["id"], child["id"], "stays behind"))["thread"]["id"]
    assert own.delete(f"/api/blocks/{page['id']}").status_code == 200
    _ops(own, there["id"], [{"op": "insert", "id": note["id"], "parent": there["id"], "content": "pulled out"}])
    assert [t["id"] for t in _threads(own, there["id"])] == [pulled]
    assert _rows(ws, "id = ?", (pulled,))[0][3:] == (there["id"], None)
    assert _rows(ws, "id = ?", (behind,))[0][4]  # its block went: orphaned


def test_put_children_orphans_what_does_not_come_back(own):
    ws = workspace_of("cm_own")
    page = make_page(own, "Replaced page")
    assert own.put(f"/api/blocks/{page['id']}/children", json={"blocks": [
        {"id": "cmPutA", "content": "a"}, {"id": "cmPutB", "content": "b"}]}).status_code == 200
    a = _ok(_post(own, page["id"], "cmPutA", "on a"))["thread"]["id"]
    b = _ok(_post(own, page["id"], "cmPutB", "on b"))["thread"]["id"]
    assert own.put(f"/api/blocks/{page['id']}/children", json={"blocks": [
        {"id": "cmPutA", "content": "a again"}, {"id": "cmPutC", "content": "c"}]}).status_code == 200
    assert [t["id"] for t in _threads(own, page["id"])] == [a]
    assert _rows(ws, "id = ?", (b,))[0][4]
    # brought back by the next replace
    assert own.put(f"/api/blocks/{page['id']}/children", json={"blocks": [
        {"id": "cmPutA", "content": "a"}, {"id": "cmPutB", "content": "b"}]}).status_code == 200
    assert [t["id"] for t in _threads(own, page["id"])] == [a, b]


def test_a_share_editor_never_brings_back_another_pages_hidden_threads(own, out):
    """An id taken from a block deleted on a private page, inserted by an
    edit share's visitor on the shared page (by an op or a subtree replace),
    leaves that page's threads hidden; the visitor's own undo on the shared
    page brings its threads back, and a member's insert still adopts."""
    ws = workspace_of("cm_own")
    private = make_page(own, "Private comments page")["id"]
    _ops(own, private, [{"op": "insert", "id": "cmPriv1", "parent": private, "content": "private"},
                        {"op": "insert", "id": "cmPriv2", "parent": private, "content": "private too"}])
    secret = _ok(_post(own, private, "cmPriv1", "SECRET discussion"))["thread"]["id"]
    _ok(_post(own, private, "cmPriv2", "SECRET too"))
    _ops(own, private, [{"op": "delete", "id": "cmPriv1"}, {"op": "delete", "id": "cmPriv2"}])
    shared = make_page(own, "Edit-shared adoption page")["id"]
    token = _share(own, shared, audience="anyone", role="edit")
    share = {"share": token}
    r = out.post(f"/api/pages/{shared}/ops", params=share, json={"client": "t", "ops": [
        {"op": "insert", "id": "cmPriv1", "parent": shared, "content": "mine now"}]})
    assert r.status_code == 200, r.text
    r = out.put(f"/api/blocks/{shared}/children", params=share, json={"blocks": [
        {"id": "cmPriv1", "content": "mine now"}, {"id": "cmPriv2", "content": "mine too"}]})
    assert r.status_code == 200, r.text
    assert _threads(out, shared, params=share) == []
    assert all(row[4] for row in _rows(ws, "block_id IN ('cmPriv1', 'cmPriv2')"))
    # the visitor's own thread, deleted with its block and inserted again on this page
    mine = _ok(_post(out, shared, "cmPriv1", "on my note", params=share))["thread"]["id"]
    for op in ({"op": "delete", "id": "cmPriv1"},
               {"op": "insert", "id": "cmPriv1", "parent": shared, "content": "back"}):
        assert out.post(f"/api/pages/{shared}/ops", params=share,
                        json={"client": "t", "ops": [op]}).status_code == 200
    assert [t["id"] for t in _threads(out, shared, params=share)] == [mine]
    # a member may read every page: a block moved by cut and paste keeps its threads
    elsewhere = make_page(own, "Member's paste page")["id"]
    _ops(own, shared, [{"op": "delete", "id": "cmPriv1"}])
    _ops(own, elsewhere, [{"op": "insert", "id": "cmPriv1", "parent": elsewhere, "content": "pasted"}])
    assert {t["id"] for t in _threads(own, elsewhere)} == {secret, mine}


# --- copies -------------------------------------------------------------------------------

def _zip_pages_db(data: bytes, sql: str):
    with tempfile.TemporaryDirectory() as td:
        with open(f"{td}/pages.db", "wb") as f:
            f.write(zipfile.ZipFile(io.BytesIO(data)).read("pages.db"))
        with closing(sqlite3.connect(f"{td}/pages.db")) as conn:
            return conn.execute(sql).fetchall()


def test_backup_merge_on_the_untrash_path(own):
    """A page in Recently deleted taken back from a backup: the trashed
    copy's comments go, the backup's come with its blocks — on the fresh id
    of a block that lives on another page now, under fresh comment ids
    where the workspace has the backup's."""
    ws = workspace_of("cm_own")
    page = make_page(own, "Merged back page")
    other = make_page(own, "Holds a moved note")
    note = _block(own, page["id"], "moves later")
    on_page = _ok(_post(own, page["id"], page["id"], "on the page"))["thread"]["id"]
    on_note = _ok(_post(own, page["id"], note["id"], "on the note"))["thread"]["id"]
    _ok(_post(own, page["id"], note["id"], "a reply", thread_id=on_note))
    backup = own.get("/api/export", params={"uploads": 0})
    assert backup.status_code == 200
    # after the backup: the note moves to another page (its id, and its comments' ids, stay taken there),
    # and the page gets a comment the backup never had, then goes to Recently deleted
    assert own.post(f"/api/blocks/{note['id']}/reorder", json={"parent_id": other["id"]}).status_code == 200
    _ok(_post(own, page["id"], page["id"], "after the backup"))
    assert own.delete(f"/api/blocks/{page['id']}").status_code == 200

    from gamma import ws_backup
    with tempfile.TemporaryDirectory() as td:
        (Path(td) / "b.zip").write_bytes(backup.content)
        review = ws_backup.preview_zip(ws, Path(td) / "b.zip")
    assert next(p for p in review["pages"] if p["id"] == page["id"])["comments"] == 3  # the review counts them
    r = own.post("/api/import-data?mode=merge", files={"file": ("b.zip", backup.content, "application/zip")})
    assert r.status_code == 200, r.text
    assert r.json()["from_trash"] == 1 and r.json()["comments_added"] == 3

    threads = _threads(own, page["id"])
    assert [t["id"] for t in threads][0] == on_page  # free in the workspace again: its own id
    assert [c["body"] for c in threads[0]["comments"]] == ["on the page"]  # the trashed copy's own went
    moved = threads[1]
    assert moved["block_id"] != note["id"] and moved["id"] != on_note  # the block's fresh id, a fresh comment id
    assert [c["body"] for c in moved["comments"]] == ["on the note", "a reply"]
    assert all(c["thread_id"] == moved["id"] for c in moved["comments"])
    assert [t["id"] for t in _threads(own, other["id"])] == [on_note]  # the live ones are untouched
    assert {r[3] for r in _rows(ws, "thread_id = ?", (moved["id"],))} == {page["id"]}


def test_gamma_export_carries_the_threads_with_their_authors_names(own):
    make_user("cm_gone", "cmgonepw123")
    gone = login("cm_gone", "cmgonepw123")
    make_user("cm_takes", "cmtakespw12")
    takes = login("cm_takes", "cmtakespw12")
    page = make_page(gone, "Exported with comments")
    note = _block(gone, page["id"], "noted")
    root = _ok(_post(gone, page["id"], note["id"], "a thought"))["thread"]["id"]
    _ok(gone.post(f"/api/comments/{root}/resolve", json={"resolved": True}), 200)
    dropped = _block(gone, page["id"], "dropped")
    _ok(_post(gone, page["id"], dropped["id"], "orphaned, not exported"))
    _ops(gone, page["id"], [{"op": "delete", "id": dropped["id"]}])
    r = gone.get(f"/api/pages/{page['id']}/export", params={"mode": "gamma"})
    assert r.status_code == 200, r.text
    assert _zip_pages_db(r.content, "SELECT id, block_id, author_name, body FROM comments") == [
        (root, note["id"], "cm_gone", "a thought")]
    imp = takes.post("/api/import-data", params={"mode": "merge"},
                     files={"file": ("g.zip", r.content, "application/zip")})
    assert imp.status_code == 200, imp.text
    drop_user("cm_gone")
    [thread] = _threads(takes, page["id"])
    assert thread["comments"][0]["author"] == "cm_gone"  # the name stored with it, the account being gone
    assert thread["resolved_at"] and thread["resolved_by"] == ""
    # the same export again adds nothing
    again = takes.post("/api/import-data", params={"mode": "merge"},
                       files={"file": ("g.zip", r.content, "application/zip")})
    assert again.json()["comments_added"] == 0 and len(_threads(takes, page["id"])) == 1


# --- live -------------------------------------------------------------------------------------

def test_the_room_hears_every_write_but_the_writers_tab(guest):
    page = make_page(guest, "Comment socket page")
    with guest.websocket_connect(f"/api/ws/page/{page['id']}?client=cmA") as a:
        recv_hello(a)
        with guest.websocket_connect(f"/api/ws/page/{page['id']}?client=cmB") as b:
            recv_hello(b)
            recv(a, "join")
            first = _ok(_post(guest, page["id"], page["id"], "from tab A", client="cmA"))["thread"]["id"]
            assert recv(b, "comments") == {"t": "comments", "block_id": page["id"], "thread_id": first}
            second = _ok(_post(guest, page["id"], page["id"], "from tab B", client="cmB"))["thread"]["id"]
            # A's first nudge is B's write: its own was never sent to it
            assert recv(a, "comments")["thread_id"] == second
            guest.delete(f"/api/comments/{second}", params={"client": "cmA"})
            assert recv(b, "comments")["thread_id"] == second
            guest.post(f"/api/comments/{first}/resolve", json={"resolved": True, "client": "cmB"})
            assert recv(a, "comments")["thread_id"] == first
