"""The Inbox (gamma/inbox.py, routers/inbox.py, docs/dev/comments.md "The
inbox"): every producer — a mention in a note, in a comment, a reply, a
resolve, a share, an invitation —; the mention diff (a re-save is silent, a
new name in the same block notifies); no self, no guest, no non-member; a
share-invited account's way in, and no other share's link handed out; a
half-typed name archived, a name moved by a split or a join followed; the
AI's batch and a replayed one; the 25 cap; the filters and the paging; the settle on trash, deletion and lost
access, but not for a workspace that failed; the way in and the title as
they are now; marks; the cleanup on member removal, workspace and account
deletion; the sweep; the nudge to the recipient's sockets only; the hosted
read-only gate; and a users.db error that never fails the comment."""

import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from conftest import account_of, login, make_folder, make_page, make_user, recv, recv_hello, workspace_of

from gamma import comments, hosted, inbox, ops, workspaces
from gamma.db import connect_users_db, page_now
from gamma.seed import insert_account


@pytest.fixture(scope="module")
def own():
    make_user("ib_own", "ibownpw123")
    return login("ib_own", "ibownpw123")


@pytest.fixture(scope="module")
def edi():
    make_user("ib_edi", "ibedipw123")
    return login("ib_edi", "ibedipw123")


@pytest.fixture(scope="module")
def vie():
    make_user("ib_vie", "ibviepw123")
    return login("ib_vie", "ibviepw123")


@pytest.fixture(scope="module")
def out():
    """An account that is no member of the lab: what a share admits."""
    make_user("ib_out", "iboutpw123")
    return login("ib_out", "iboutpw123")


@pytest.fixture(scope="module")
def lab(own, edi, vie):
    """A shared workspace: ib_own owns it, ib_edi edits, ib_vie views."""
    info = workspaces.create("Inbox lab", account_of("ib_own"), kind="shared")
    workspaces.set_member(info["id"], account_of("ib_edi"), "editor", by=account_of("ib_own"))
    workspaces.set_member(info["id"], account_of("ib_vie"), "viewer", by=account_of("ib_own"))
    return info["id"]


def _in(ws):
    return {"X-Gamma-Workspace": ws}


def _page(who, title, ws=None):
    r = who.post("/api/blocks", json={"parent_id": "root", "content": title}, headers=_in(ws) if ws else None)
    assert r.status_code == 200, r.text
    return r.json()["id"]


def _ops(who, page_id, ops_, ws=None, params=None, client_id="t"):
    r = who.post(f"/api/pages/{page_id}/ops", json={"client": client_id, "ops": ops_},
                 headers=_in(ws) if ws else None, params=params)
    assert r.status_code == 200, r.text
    return r.json()


def _note(who, page_id, block_id, text, ws=None, params=None):
    _ops(who, page_id, [{"op": "insert", "id": block_id, "parent": page_id, "content": text}], ws, params)


def _say(who, page_id, block_id, text, ws=None, params=None):
    _ops(who, page_id, [{"op": "set", "id": block_id, "content": text}], ws, params)


def _comment(who, page_id, block_id, body, ws=None, params=None, **more):
    r = who.post(f"/api/pages/{page_id}/comments", json={"block_id": block_id, "body": body, **more},
                 headers=_in(ws) if ws else None, params=params)
    assert r.status_code == 201, r.text
    return r.json()


def _items(who, filter="all", **match):
    """The account's items of ``filter`` matching every field of ``match``."""
    r = who.get("/api/inbox", params={"filter": filter, "limit": 100})
    assert r.status_code == 200, r.text
    return [i for i in r.json()["items"] if all(i.get(k) == v for k, v in match.items())]


def _kinds(who, filter="all", **match):
    return sorted(i["kind"] for i in _items(who, filter, **match))


# --- producers ------------------------------------------------------------------------

def test_a_mention_in_a_note_and_the_diff(own, edi, vie, out, lab):
    page = _page(own, "Mention page", lab)
    _note(own, page, "ibMen1", "hello @ib_edi and @ib_out and @ib_own", lab)
    [item] = _items(edi, page_id=page)
    assert (item["kind"], item["actor"], item["block_id"], item["share_token"]) == ("mention", "ib_own", "ibMen1", "")
    assert (item["workspace_id"], item["workspace_name"], item["page_title"]) == (lab, "Inbox lab", "Mention page")
    assert item["snippet"] == "hello @ib_edi and @ib_out and @ib_own" and item["read_at"] is None
    assert _items(own, page_id=page) == [] and _items(out, page_id=page) == []  # no self, no non-member
    # a re-save that keeps the name tells no one again; a new name in the same block does
    _say(own, page, "ibMen1", "hello @ib_edi and @ib_out and @ib_own, see below", lab)
    assert len(_items(edi, page_id=page)) == 1 and _items(vie, page_id=page) == []
    _say(own, page, "ibMen1", "hello @ib_edi and @ib_vie", lab)
    assert len(_items(edi, page_id=page)) == 1 and _kinds(vie, page_id=page) == ["mention"]
    # a reminder, code and an e-mail address mention no one
    _note(own, page, "ibMen2", "`@ib_edi` @2026-10-10 09:00 (remind @ib_vie) mail ib_edi@example.org", lab)
    assert len(_items(edi, page_id=page)) == 1 and len(_items(vie, page_id=page)) == 1
    # the editor mentions the owner, through the block endpoint too
    r = edi.post("/api/blocks", json={"parent_id": page, "content": "ping @ib_own"}, headers=_in(lab))
    assert r.status_code == 200
    assert _items(own, page_id=page, block_id=r.json()["id"])[0]["actor"] == "ib_edi"


def test_an_items_snippet_names_its_links(own, edi, lab):
    """A [[link]] in the comment or note an item quotes is written as the
    page's title or the note's first line, not as its id; one to nothing
    stays as written."""
    target = _page(own, "Appendix tables", lab)
    page = _page(own, "Linking page", lab)
    _note(own, page, "ibLnk1", "the claim", lab)
    _note(own, page, "ibLnk2", "Second note\nwith a second line", lab)
    _comment(own, page, "ibLnk1", f"see [[{target}]] and ![[ibLnk2]] and [[nowhere1]], @ib_edi", lab)
    [item] = _items(edi, kind="comment_mention", block_id="ibLnk1")
    assert item["snippet"] == "see Appendix tables and Second note and [[nowhere1]], @ib_edi"
    _note(own, page, "ibLnk3", f"ping @ib_edi about [[{target}]]", lab)
    [item] = _items(edi, kind="mention", block_id="ibLnk3")
    assert item["snippet"] == "ping @ib_edi about Appendix tables"


def test_a_mention_typed_elsewhere_or_by_no_account_tells_no_one(own, edi, lab):
    page = _page(own, "Quiet mention page", lab)
    by = account_of("ib_own")
    ops.commit_ops(lab, page, [{"op": "insert", "id": "ibSync", "parent": page, "content": "@ib_edi"}],
                   actor=by, client=inbox.SYNC_CLIENT)
    ops.commit_ops(lab, page, [{"op": "insert", "id": "ibMirr", "parent": page, "content": "@ib_edi"}],
                   actor="mirror")
    ops.commit_ops(lab, page, [{"op": "insert", "id": "ibLink", "parent": page, "content": "@ib_edi"}],
                   actor="link:Otter")
    # a block inserted and deleted in one batch mentions no one either
    ops.commit_ops(lab, page, [{"op": "insert", "id": "ibGone", "parent": page, "content": "@ib_edi"},
                               {"op": "delete", "id": "ibGone"}], actor=by)
    assert _items(edi, page_id=page) == []
    from gamma import sync_engine
    assert inbox.SYNC_CLIENT == sync_engine.CLIENT


def test_the_ais_batch_is_its_chat_owners_and_a_replay_tells_no_one_twice(own, edi, lab):
    page = _page(own, "AI and replay page", lab)
    ops.commit_ops(lab, page, [{"op": "insert", "id": "ibAi01", "parent": page, "content": "see @ib_edi"}],
                   actor=account_of("ib_own"), client="ai")
    [item] = _items(edi, block_id="ibAi01")
    assert (item["kind"], item["actor"]) == ("mention", "ib_own")
    # the same batch sent again (its answer was lost) is answered from the log, and tells no one again,
    # even once the first item is read
    batch = {"client": "tabR", "batch": "ib-r-1",
             "ops": [{"op": "insert", "id": "ibRep1", "parent": page, "content": "and @ib_edi"}]}
    assert own.post(f"/api/pages/{page}/ops", json=batch, headers=_in(lab)).status_code == 200
    [first] = _items(edi, block_id="ibRep1")
    assert edi.post("/api/inbox/mark", json={"ids": [first["id"]], "read": True}).status_code == 200
    again = own.post(f"/api/pages/{page}/ops", json=batch, headers=_in(lab))
    assert again.status_code == 200
    assert [i["id"] for i in _items(edi, block_id="ibRep1")] == [first["id"]]


def test_no_guest_hears_of_anything(own, guest):
    from conftest import guest_name
    page = make_page(own, "Guest mention page")
    r = own.post(f"/api/share/{page['id']}")
    assert r.status_code == 200  # anyone with the link, which a guest may open
    _note(own, page["id"], "ibGst1", f"hi @{guest_name()}")
    with connect_users_db() as conn:
        assert not conn.execute("SELECT 1 FROM inbox_items WHERE user_id = ?",
                                (account_of(guest_name()),)).fetchone()
    assert guest.get("/api/inbox").status_code == 403


def test_comment_mentions_replies_and_resolves(own, edi, vie, out, lab):
    page = _page(own, "Comment inbox page", lab)
    _note(own, page, "ibCom1", "a note", lab)
    first = _comment(edi, page, "ibCom1", "what about this, @ib_vie?", lab)
    root = first["thread"]["id"]
    [item] = _items(vie, thread_id=root)
    assert (item["kind"], item["actor"], item["comment_id"], item["block_id"]) == (
        "comment_mention", "ib_edi", root, "ibCom1")
    # a reply: the thread's author and the people it mentions hear of it
    reply = _comment(own, page, "ibCom1", "fine by me", lab, thread_id=root)["comment"]["id"]
    assert _kinds(edi, comment_id=reply) == ["reply"] and _kinds(vie, comment_id=reply) == ["reply"]
    assert _items(own, comment_id=reply) == []
    # someone a reply mentions gets that, not a second item as a reply
    again = _comment(own, page, "ibCom1", "@ib_vie please look", lab, thread_id=root)["comment"]["id"]
    assert _kinds(vie, comment_id=again) == ["comment_mention"] and _kinds(edi, comment_id=again) == ["reply"]
    # an edit tells the people it adds only, and never a non-member
    r = edi.patch(f"/api/comments/{root}", json={"body": "what about this, @ib_vie and @ib_own and @ib_out?"},
                  headers=_in(lab))
    assert r.status_code == 200
    assert _kinds(own, comment_id=root) == ["comment_mention"] and len(_items(vie, comment_id=root)) == 1
    assert _items(out, comment_id=root) == []
    # resolved by someone else: its author hears; by its author, or a reopen: no one
    assert own.post(f"/api/comments/{root}/resolve", json={"resolved": True}, headers=_in(lab)).status_code == 200
    [done] = _items(edi, kind="resolved", thread_id=root)
    assert (done["actor"], done["comment_id"], done["snippet"]) == ("ib_own", root, "what about this, @ib_vie and "
                                                                    "@ib_own and @ib_out?")
    assert own.post(f"/api/comments/{root}/resolve", json={"resolved": False}, headers=_in(lab)).status_code == 200
    assert edi.post(f"/api/comments/{root}/resolve", json={"resolved": True}, headers=_in(lab)).status_code == 200
    assert len(_items(edi, kind="resolved")) == 1
    # deleting a comment deletes its items
    assert own.delete(f"/api/comments/{again}", headers=_in(lab)).status_code == 200
    assert _items(vie, comment_id=again) == [] and _items(edi, comment_id=again) == []


def test_a_share_tells_its_new_invitees_with_the_way_in(own, vie, out):
    page = make_page(own, "Shared inbox page")["id"]
    r = own.post(f"/api/share/{page}", json={"audience": "list", "users": [{"name": "ib_out", "role": "view"}]})
    assert r.status_code == 200, r.text
    token = r.json()["token"]
    [item] = _items(out, page_id=page)
    assert (item["kind"], item["actor"], item["share_token"], item["snippet"]) == (
        "shared", "ib_own", token, "Shared inbox page")
    assert item["workspace_id"] == workspace_of("ib_own") and item["page_title"] == "Shared inbox page"
    with connect_users_db() as conn:
        added = conn.execute("SELECT added_at FROM share_users WHERE token = ?", (token,)).fetchone()[0]
    # a save that adds someone tells only them; the one invited before keeps when she was
    r = own.put(f"/api/share-settings/{page}", json={"users": [{"name": "ib_out", "role": "edit"},
                                                               {"name": "ib_vie", "role": "view"}]})
    assert r.status_code == 200
    assert len(_items(out, page_id=page)) == 1 and _kinds(vie, page_id=page) == ["shared"]
    with connect_users_db() as conn:
        assert conn.execute("SELECT added_at FROM share_users WHERE token = ? AND user_id = ?",
                            (token, account_of("ib_out"))).fetchone()[0] == added
    # what happens on the page later reaches the invitee through the share
    _note(own, page, "ibShr1", "over to you @ib_out", params=None)
    [mention] = _items(out, kind="mention", page_id=page)
    assert mention["share_token"] == token
    thread = _comment(out, page, page, "a question", params={"share": token})["thread"]["id"]
    _comment(own, page, page, "an answer", thread_id=thread)
    assert _items(out, kind="reply", thread_id=thread)[0]["share_token"] == token


def test_a_folder_share_tells_its_invitees(own, out):
    folder_id = make_folder(own, "Inbox shared folder")
    r = own.post(f"/api/share/folder/{folder_id}", json={"audience": "list", "users": ["ib_out"]})
    assert r.status_code == 200, r.text
    [item] = _items(out, kind="shared", block_id=folder_id)
    assert (item["page_id"], item["share_token"], item["page_title"]) == ("", r.json()["token"],
                                                                         "Inbox shared folder")
    # the share stopped: the item settles
    assert own.delete(f"/api/share-settings/folder/{folder_id}").status_code == 200
    assert _items(out, "all", block_id=folder_id) == []
    assert len(_items(out, "archived", block_id=folder_id)) == 1


def test_an_invitation_to_a_workspace(own, out):
    info = workspaces.create("Inbox invitation lab", account_of("ib_own"), kind="shared")
    assert workspaces.set_member(info["id"], account_of("ib_out"), "viewer", by=account_of("ib_own")) is True
    [item] = _items(out, workspace_id=info["id"])
    assert (item["kind"], item["actor"], item["workspace_name"], item["page_id"]) == (
        "invited", "ib_own", "Inbox invitation lab", "")
    # a new role is no new invitation
    assert workspaces.set_member(info["id"], account_of("ib_out"), "editor", by=account_of("ib_own")) is False
    assert len(_items(out, workspace_id=info["id"])) == 1
    # leaving takes the workspace's items along
    workspaces.remove_member(info["id"], account_of("ib_out"))
    assert _items(out, "all", workspace_id=info["id"]) == [] and _items(out, "archived", workspace_id=info["id"]) == []


def test_one_event_tells_at_most_25(own, lab):
    page = _page(own, "Crowded page", lab)
    with connect_users_db() as conn:
        ids = []
        for i in range(30):
            uid = insert_account(conn, f"ib_crowd{i:02d}", "x")
            conn.execute("INSERT INTO workspace_members (workspace_id, user_id, role, added_by, added_at) "
                         "VALUES (?, ?, 'viewer', '', ?)", (lab, uid, page_now()))
            ids.append(uid)
        # accounts named first who cannot open the page take none of the 25 places
        strangers = [insert_account(conn, f"ib_stranger{i:02d}", "x") for i in range(30)]
        conn.commit()
    told = inbox.emit("mention", strangers + ids, account_of("ib_own"), lab, page_id=page, block_id=page,
                      snippet="all of you")
    assert told == ids[:inbox.MAX_RECIPIENTS]
    with connect_users_db() as conn:
        assert conn.execute("SELECT COUNT(*) FROM inbox_items WHERE page_id = ?", (page,)).fetchone()[0] == 25


# --- the listing --------------------------------------------------------------------------

def test_filters_marks_and_paging(own, vie, lab):
    make_user("ib_pager", "ibpagerpw1")
    pager = login("ib_pager", "ibpagerpw1")
    uid = account_of("ib_pager")
    workspaces.set_member(lab, uid, "viewer", by=account_of("ib_own"))  # one invited item
    page = _page(own, "Paging page", lab)
    for i in range(6):
        _note(own, page, f"ibPage{i}", f"note {i} for @ib_pager", lab)
    body = pager.get("/api/inbox").json()
    assert body["unread"] == 7 and len(body["items"]) == 7 and body["more"] is False
    assert [i["block_id"] for i in body["items"][:6]] == [f"ibPage{i}" for i in range(5, -1, -1)]  # newest first
    assert pager.get("/api/inbox/unread").json() == {"unread": 7}
    # paging
    first = pager.get("/api/inbox", params={"limit": 3}).json()
    assert len(first["items"]) == 3 and first["more"] is True
    second = pager.get("/api/inbox", params={"limit": 3, "before": first["items"][-1]["at"]}).json()
    assert [i["id"] for i in first["items"] + second["items"]] == [i["id"] for i in body["items"][:6]]
    # marks: read, unread again, archived, everything read
    ids = [i["id"] for i in body["items"]]
    assert pager.post("/api/inbox/mark", json={"ids": ids[:2], "read": True}).json() == {"unread": 5}
    assert [i["id"] for i in pager.get("/api/inbox").json()["items"]] == ids[2:]
    assert len(pager.get("/api/inbox", params={"filter": "all"}).json()["items"]) == 7
    assert pager.post("/api/inbox/mark", json={"ids": ids[:1], "read": False}).json() == {"unread": 6}
    assert pager.post("/api/inbox/mark", json={"ids": ids[1:3], "archived": True}).json() == {"unread": 5}
    archived = pager.get("/api/inbox", params={"filter": "archived"}).json()["items"]
    assert sorted(i["id"] for i in archived) == sorted(ids[1:3])
    assert pager.post("/api/inbox/mark", json={"ids": ids[1:2], "archived": False}).json() == {"unread": 5}
    assert pager.post("/api/inbox/mark", json={"all": True, "read": True}).json() == {"unread": 0}
    assert pager.get("/api/inbox").json()["items"] == []
    assert vie.post("/api/inbox/mark", json={"ids": ids, "read": False}).json()["unread"] == \
        vie.get("/api/inbox/unread").json()["unread"]  # someone else's ids mark nothing
    assert pager.get("/api/inbox/unread").json() == {"unread": 0}
    # the bounds
    assert pager.post("/api/inbox/mark", json={"ids": ["x"] * 101, "read": True}).status_code == 422
    assert pager.post("/api/inbox/mark", json={"read": True}).status_code == 400
    assert pager.post("/api/inbox/mark", json={"all": True}).status_code == 400
    assert pager.get("/api/inbox", params={"filter": "later"}).status_code == 400


def test_a_listing_never_splits_one_moment(own):
    make_user("ib_moment", "ibmomentpw1")
    who = login("ib_moment", "ibmomentpw1")
    uid, ws = account_of("ib_moment"), workspace_of("ib_moment")
    at = page_now()
    with connect_users_db() as conn:
        conn.executemany("INSERT INTO inbox_items (id, user_id, kind, actor, workspace_id, at) "
                         "VALUES (?, ?, 'invited', '', ?, ?)",
                         [(f"ibmoment{i}", uid, ws, at) for i in range(3)] +
                         [("ibmomentold", uid, ws, "2020-01-01T00:00:00.000000Z")])
        conn.commit()
    first = who.get("/api/inbox", params={"limit": 2}).json()
    assert len(first["items"]) == 3 and first["more"] is True
    rest = who.get("/api/inbox", params={"limit": 2, "before": at}).json()
    assert [i["id"] for i in rest["items"]] == ["ibmomentold"] and rest["more"] is False


def test_the_listing_settles_what_leads_nowhere(own, edi, out, lab, monkeypatch):
    page = _page(own, "Settling page", lab)
    _note(own, page, "ibSet1", "@ib_edi one", lab)
    _note(own, page, "ibSet2", "@ib_edi two", lab)
    other = _page(own, "Settling page two", lab)
    _note(own, other, "ibSet3", "@ib_edi three", lab)
    assert len(_items(edi, "unread", workspace_id=lab, kind="mention")) >= 3
    # a block deleted: its item is archived
    _ops(own, page, [{"op": "delete", "id": "ibSet2"}], lab)
    assert [i["block_id"] for i in _items(edi, "unread", page_id=page)] == ["ibSet1"]
    assert [i["block_id"] for i in _items(edi, "archived", page_id=page)] == ["ibSet2"]
    # a workspace that cannot be read keeps its items, whatever happened there
    assert own.delete(f"/api/blocks/{page}", headers=_in(lab)).status_code == 200
    real = inbox.connect_pages_db

    def broken(ws):
        if ws == lab:
            raise sqlite3.OperationalError("database disk image is malformed")
        return real(ws)

    monkeypatch.setattr(inbox, "connect_pages_db", broken)
    assert [i["block_id"] for i in _items(edi, "unread", page_id=page)] == ["ibSet1"]
    monkeypatch.setattr(inbox, "connect_pages_db", real)
    # read whole: a page in Recently deleted settles, and its restore leaves it archived
    unread = edi.get("/api/inbox/unread").json()["unread"]
    body = edi.get("/api/inbox").json()
    assert body["unread"] == unread - 1 == len(body["items"])
    assert sorted(i["block_id"] for i in _items(edi, "archived", page_id=page)) == ["ibSet1", "ibSet2"]
    # a member finds the page in Recently deleted: its title stays
    assert {i["page_title"] for i in _items(edi, "archived", page_id=page)} == {"Settling page"}
    assert own.post(f"/api/trash/{page}/restore", headers=_in(lab)).status_code == 200
    assert _items(edi, "unread", page_id=page) == []
    # access lost: an item through a share settles once the share stops
    mine = make_page(own, "Settling shared page")["id"]
    assert own.post(f"/api/share/{mine}", json={"audience": "list", "users": ["ib_out"]}).status_code == 200
    _note(own, mine, "ibSet4", "@ib_out look")
    assert _kinds(out, "unread", page_id=mine) == ["mention", "shared"]
    assert own.delete(f"/api/share-settings/{mine}").status_code == 200
    assert _items(out, "unread", page_id=mine) == []
    assert _kinds(out, "archived", page_id=mine) == ["mention", "shared"]


def test_an_item_hands_out_only_a_share_that_names_the_account_or_the_actors_own(own, out):
    make_user("ib_pup", "ibpuppw123")
    pup = login("ib_pup", "ibpuppw123")
    # the folder is shared view-only with ib_out by name; the page also has an
    # anyone-with-the-link edit share that ib_out was never given
    folder = make_folder(own, "Way-in folder")
    page = make_page(own, "Way-in page", {"folders": [folder]})["id"]
    r = own.post(f"/api/share/folder/{folder}", json={"audience": "list", "users": [{"name": "ib_out", "role": "view"}]})
    assert r.status_code == 200, r.text
    folder_token = r.json()["token"]
    edit_token = own.post(f"/api/share/{page}", json={"audience": "anyone", "role": "edit"}).json()["token"]
    # the view-only visitor mentions an account of its own: no item, so no edit link
    _comment(out, page, page, "hey @ib_pup", params={"share": folder_token})
    _note(own, page, "ibWay1", "and @ib_pup from the owner")  # a member's write hands out no link either
    assert _items(pup, page_id=page) == [] and _items(pup, "archived", page_id=page) == []
    # through the anyone link itself, its token is the way in: the actor's own
    _comment(out, page, page, "@ib_pup this one", params={"share": edit_token})
    [item] = _items(pup, page_id=page)
    assert (item["kind"], item["share_token"]) == ("comment_mention", edit_token)
    # the invitee of the folder share hears of the owner's mention through it
    _note(own, page, "ibWay2", "@ib_out the folder way")
    assert _items(out, kind="mention", block_id="ibWay2")[0]["share_token"] == folder_token


def test_a_note_that_names_someone_no_more_settles_and_a_moved_name_follows(own, edi, lab):
    for name in ("ib_ben", "ib_benjamin"):
        make_user(name, f"{name}pw123")
        workspaces.set_member(lab, account_of(name), "viewer", by=account_of("ib_own"))
    ben = login("ib_ben", "ib_benpw123")
    page = _page(own, "Half-typed page", lab)
    # the editor saves after a pause in typing: "@ib_ben" on its way to "@ib_benjamin"
    invited = ben.get("/api/inbox/unread").json()["unread"]  # the invitation to the lab
    _note(own, page, "ibHalf1", "ask @ib_ben", lab)
    assert ben.get("/api/inbox/unread").json()["unread"] == invited + 1
    _say(own, page, "ibHalf1", "ask @ib_benjamin", lab)
    assert ben.get("/api/inbox/unread").json()["unread"] == invited  # archived at once, before any listing
    assert _items(ben, page_id=page) == [] and len(_items(ben, "archived", page_id=page)) == 1
    assert len(_items(login("ib_benjamin", "ib_benjaminpw123"), page_id=page)) == 1
    # a name gone from its note by any other way settles at the listing
    _note(own, page, "ibHalf2", "and @ib_ben", lab)
    with inbox.connect_pages_db(lab) as conn:
        conn.execute("UPDATE unified_blocks SET content = 'and nobody' WHERE id = 'ibHalf2'")
        conn.commit()
    assert _items(ben, page_id=page) == []
    # Enter before the name (one batch: the head set, the tail inserted), then a
    # join: one item for a name typed once, which follows it, still unread
    _note(own, page, "ibSplit1", "Hi @ib_edi see this", lab)
    _ops(own, page, [{"op": "set", "id": "ibSplit1", "content": "Hi "},
                     {"op": "insert", "id": "ibSplit2", "parent": page, "content": "@ib_edi see this"}], lab)
    [item] = _items(edi, "unread", page_id=page)
    assert item["block_id"] == "ibSplit2"
    _ops(own, page, [{"op": "set", "id": "ibSplit1", "content": "Hi @ib_edi see this"},
                     {"op": "delete", "id": "ibSplit2"}], lab)
    assert [i["block_id"] for i in _items(edi, "unread", page_id=page)] == ["ibSplit1"]
    assert _items(edi, "archived", page_id=page) == []


def test_an_item_takes_the_way_in_there_is_now_and_no_title_once_out(own):
    make_user("ib_far", "ibfarpw123")
    far = login("ib_far", "ibfarpw123")
    # a share stopped and made again (a new token): the mention takes the new
    # one, the old share's item settles, the new share has its own
    page = make_page(own, "Reshared page")["id"]
    old = own.post(f"/api/share/{page}", json={"audience": "list", "users": ["ib_far"]}).json()["token"]
    _note(own, page, "ibResh1", "look @ib_far")
    assert own.delete(f"/api/share-settings/{page}").status_code == 200
    new = own.post(f"/api/share/{page}", json={"audience": "list", "users": ["ib_far"]}).json()["token"]
    assert new != old
    assert sorted((i["kind"], i["share_token"]) for i in _items(far, page_id=page)) == [
        ("mention", new), ("shared", new)]
    [gone] = _items(far, "archived", page_id=page)
    assert gone["kind"] == "shared" and gone["page_title"] == "Reshared page"  # the new share lets it in
    # stopped for good and renamed after: an archived item never shows the title now
    assert own.delete(f"/api/share-settings/{page}").status_code == 200
    assert own.put(f"/api/blocks/{page}", json={"content": "Secret new title"}).status_code == 200
    assert {i["page_title"] for i in _items(far, "archived", page_id=page)} == {""}
    # an item archived by hand that still leads somewhere keeps its title
    other = make_page(own, "Still shared page")["id"]
    own.post(f"/api/share/{other}", json={"audience": "list", "users": ["ib_far"]})
    [item] = _items(far, page_id=other)
    far.post("/api/inbox/mark", json={"ids": [item["id"]], "archived": True})
    assert _items(far, "archived", page_id=other)[0]["page_title"] == "Still shared page"
    # an invitee who becomes a member opens the page as one
    info = workspaces.create("Way-in lab", account_of("ib_own"), kind="shared")
    by = account_of("ib_own")
    lab_page = own.post("/api/blocks", json={"parent_id": "root", "content": "Lab page"},
                        headers=_in(info["id"])).json()["id"]
    token = own.post(f"/api/share/{lab_page}", json={"audience": "list", "users": ["ib_far"]},
                     headers=_in(info["id"])).json()["token"]
    _note(own, lab_page, "ibResh2", "@ib_far in the lab", info["id"])
    assert _items(far, kind="mention", page_id=lab_page)[0]["share_token"] == token
    workspaces.set_member(info["id"], account_of("ib_far"), "viewer", by=by)
    assert {i["share_token"] for i in _items(far, page_id=lab_page)} == {""}


def test_a_shared_workspace_made_for_another_owner_tells_them(own, out):
    info = workspaces.create("Made for you", account_of("ib_out"), kind="shared", by=account_of("ib_own"))
    [item] = _items(out, workspace_id=info["id"])
    assert (item["kind"], item["actor"], item["workspace_name"]) == ("invited", "ib_own", "Made for you")
    mine = workspaces.create("Made by me", account_of("ib_out"), kind="shared", by=account_of("ib_out"))
    assert _items(out, workspace_id=mine["id"]) == []


# --- upkeep -------------------------------------------------------------------------------

def test_cleanup_with_a_workspace_and_an_account(own):
    from gamma.workspaces import delete, delete_account
    make_user("ib_gone", "ibgonepw123")
    gone = account_of("ib_gone")
    info = workspaces.create("Inbox doomed lab", account_of("ib_own"), kind="shared")
    workspaces.set_member(info["id"], gone, "viewer", by=account_of("ib_own"))
    workspaces.set_member(info["id"], account_of("ib_out"), "viewer", by=account_of("ib_own"))
    with connect_users_db() as conn:
        assert conn.execute("SELECT COUNT(*) FROM inbox_items WHERE workspace_id = ?", (info["id"],)).fetchone()[0] == 2
    delete(info["id"], by=account_of("ib_own"))
    with connect_users_db() as conn:
        assert conn.execute("SELECT COUNT(*) FROM inbox_items WHERE workspace_id = ?", (info["id"],)).fetchone()[0] == 0
    lab2 = workspaces.create("Inbox second lab", account_of("ib_own"), kind="shared")
    workspaces.set_member(lab2["id"], gone, "viewer", by=account_of("ib_own"))
    delete_account(gone)
    with connect_users_db() as conn:
        assert conn.execute("SELECT COUNT(*) FROM inbox_items WHERE user_id = ?", (gone,)).fetchone()[0] == 0


def test_the_sweep(own):
    make_user("ib_sweep", "ibsweeppw1")
    uid, ws = account_of("ib_sweep"), workspace_of("ib_sweep")
    now = datetime.now(timezone.utc)

    def ago(days):
        return (now - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z"

    rows = [("ibswA", ago(100), ago(99), None),   # read, past 90 days: goes
            ("ibswB", ago(80), ago(79), None),    # read, recent: stays
            ("ibswC", ago(40), None, ago(31)),    # archived a month ago: goes
            ("ibswD", ago(40), None, ago(10)),    # archived lately: stays
            ("ibswE", ago(200), None, None),      # unread, past 180 days: archived
            ("ibswF", ago(10), None, None)]       # unread: stays
    with connect_users_db() as conn:
        conn.executemany("INSERT INTO inbox_items (id, user_id, kind, actor, workspace_id, at, read_at, archived_at) "
                         "VALUES (?, ?, 'invited', '', ?, ?, ?, ?)", [(i, uid, ws, at, r, a) for i, at, r, a in rows])
        conn.commit()
    out = inbox.sweep(now=now)
    assert out["deleted"] >= 2 and out["archived"] >= 1
    with connect_users_db() as conn:
        left = dict(conn.execute("SELECT id, archived_at IS NOT NULL FROM inbox_items WHERE user_id = ?", (uid,)))
    assert left == {"ibswB": 0, "ibswD": 1, "ibswE": 1, "ibswF": 0}


def test_the_comment_sweep_takes_its_items_along(own, edi, lab):
    page = _page(own, "Swept comment page", lab)
    _note(own, page, "ibSwp1", "a note", lab)
    root = _comment(own, page, "ibSwp1", "@ib_edi soon gone", lab)["thread"]["id"]
    assert _items(edi, comment_id=root)
    _ops(own, page, [{"op": "delete", "id": "ibSwp1"}], lab)
    comments.sweep(now=datetime.now(timezone.utc) + timedelta(days=comments.ORPHAN_KEEP_DAYS + 1))
    with connect_users_db() as conn:
        assert not conn.execute("SELECT 1 FROM inbox_items WHERE comment_id = ?", (root,)).fetchone()


# --- delivery and the edges --------------------------------------------------------------

def test_the_nudge_reaches_the_recipients_sockets_only(own, edi, vie, lab):
    page = _page(own, "Nudge page", lab)
    q = f"?ws={lab}"
    with vie.websocket_connect(f"/api/ws/page/{page}{q}&client=ibV") as v:
        recv_hello(v)
        with edi.websocket_connect(f"/api/ws/page/{page}{q}&client=ibE") as e:
            recv_hello(e)
            _comment(own, page, page, "@ib_vie have a look", lab)
            assert recv(v, "inbox", ("join", "leave", "cursor", "comments")) == {"t": "inbox"}
            assert recv(e, "comments", ("join", "leave", "cursor"))
            _comment(own, page, page, "and nothing for anyone", lab)
            assert recv(e, "comments", ("join", "leave", "cursor"))  # no inbox nudge came in between


def test_a_users_db_error_never_fails_the_write(own, edi, lab, monkeypatch):
    page = _page(own, "Busy users.db page", lab)

    def busy():
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(inbox, "connect_users_db", busy)
    created = _comment(own, page, page, "@ib_edi this still lands", lab)
    _note(own, page, "ibBusy1", "@ib_edi and so does this", lab)
    monkeypatch.undo()
    assert _items(edi, page_id=page) == []
    assert [t["id"] for t in own.get(f"/api/pages/{page}/comments", headers=_in(lab)).json()["threads"]] == [
        created["thread"]["id"]]


def test_a_share_name_that_cannot_be_read_still_tells_the_invitee(own, out, monkeypatch):
    """A shared item's snippet is a nicety: a pages.db read that fails is
    logged, and the invitee still hears of the share, without the name."""
    from types import SimpleNamespace

    from gamma import logbuf
    from gamma.auth import ShareScope, share_lookup
    from gamma.routers import shares

    page = make_page(own, "Unreadable share name")["id"]
    r = own.post(f"/api/share/{page}", json={"audience": "list", "users": [{"name": "ib_out", "role": "view"}]})
    assert r.status_code == 200, r.text
    token = r.json()["token"]
    start = logbuf.last_seq("warning")

    def busy(ws):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(shares, "connect_pages_db", busy)
    shares._tell(workspace_of("ib_own"), SimpleNamespace(state=SimpleNamespace(user_id=account_of("ib_own"))),
                 ShareScope.of(share_lookup(token)), token, [account_of("ib_out")])
    monkeypatch.undo()
    assert sorted(i["snippet"] for i in _items(out, kind="shared", page_id=page)) == ["", "Unreadable share name"]
    assert any("[shares] the name of shared" in e["msg"] for e in logbuf.tail(start))


def test_who_has_an_inbox(own, guest, anon):
    from fastapi.testclient import TestClient

    from gamma.app import app
    from gamma.integrations import create_token

    assert own.get("/api/inbox").status_code == 200
    assert anon.get("/api/inbox").status_code == 401
    assert guest.get("/api/inbox/unread").status_code == 403
    bearer = TestClient(app)
    token = create_token(account_of("ib_own"), workspace_of("ib_own"), "ib", 1, scope="write")["token"]
    bearer.headers["Authorization"] = f"Bearer {token}"
    assert bearer.get("/api/inbox").status_code == 403
    assert bearer.post("/api/inbox/mark", json={"all": True, "read": True}).status_code == 403
    # marking is account bookkeeping: a read-only hosted server takes it
    assert hosted.allowed_when_read_only("/api/inbox/mark")
