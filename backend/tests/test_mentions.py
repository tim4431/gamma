"""The @ menu's server side (gamma/mentions.py, routers/mentions.py): the
people of a workspace, and an account's reminders — found in every
workspace it can open, written in note text as
``@YYYY-MM-DD HH:MM (remind @username)`` — with the ones it dismissed."""

import pytest

from conftest import account_of, fresh_client, login, make_page, make_user, workspace_of

DAY = "2026-10-10"


@pytest.fixture(scope="module")
def ann():
    make_user("mt_ann", "annpw12345")
    return login("mt_ann", "annpw12345")


@pytest.fixture(scope="module")
def ben():
    make_user("mt_ben", "benpw12345")
    return login("mt_ben", "benpw12345")


@pytest.fixture(scope="module")
def lab(ann, ben):
    """A shared workspace: ann owns it, ben edits."""
    from gamma import workspaces
    info = workspaces.create("Mention lab", account_of("mt_ann"), kind="shared")
    workspaces.set_member(info["id"], account_of("mt_ben"), "editor", by=account_of("mt_ann"))
    return info["id"]


def _in(ws):
    return {"X-Gamma-Workspace": ws}


def _note(client, parent_id, content, ws=None):
    r = client.post("/api/blocks", json={"parent_id": parent_id, "content": content},
                    headers=_in(ws) if ws else None)
    assert r.status_code == 200, r.text
    return r.json()


def _reminders(client):
    r = client.get("/api/reminders")
    assert r.status_code == 200, r.text
    return r.json()


def test_people_are_the_workspace_members(ann, ben, lab):
    r = ben.get("/api/people", headers=_in(lab))
    assert r.status_code == 200, r.text
    assert r.json()["people"] == [{"username": "mt_ann", "role": "owner"}, {"username": "mt_ben", "role": "editor"}]
    assert ann.get("/api/people").json()["people"] == [{"username": "mt_ann", "role": "owner"}]
    # ben is no member of ann's personal workspace
    assert ben.get("/api/people", headers=_in(workspace_of("mt_ann"))).status_code == 403
    assert fresh_client().get("/api/people").status_code == 401


def test_reminders_follow_their_person_across_workspaces(ann, ben, lab):
    page = make_page(ann, "Thesis plan")
    mine = _note(ann, page["id"], f"Draft due @{DAY} 09:00 (remind @mt_ann) — ask @mt_ben")
    _note(ann, page["id"], f"Not mine @{DAY} 10:00 (remind @mt_ben)")
    _note(ann, page["id"], f"An example: `@{DAY} 11:00 (remind @mt_ann)` in code")
    shared_page = _note(ben, "root", "Lab meeting", ws=lab)
    for_ann = _note(ben, shared_page["id"], "Bring the plots @2026-10-09 (remind @mt_ann)", ws=lab)
    for_ben = _note(ann, shared_page["id"], f"Book the room @{DAY} 08:30 (remind @mt_ben)", ws=lab)

    got = _reminders(ann)["reminders"]
    assert [(r["block_id"], r["date"], r["time"]) for r in got] == [
        (for_ann["id"], "2026-10-09", ""), (mine["id"], DAY, "09:00")]
    first = got[0]
    assert first["workspace_id"] == lab and first["page_id"] == shared_page["id"]
    assert first["page_title"] == "Lab meeting" and first["key"] == f"{for_ann['id']}/2026-10-09/"
    assert got[1]["text"].startswith("Draft due")
    # The one for ben in ann's personal workspace reaches no one: ben cannot open it.
    assert [r["block_id"] for r in _reminders(ben)["reminders"]] == [for_ben["id"]]


def test_a_deleted_page_reminds_no_one(ann):
    page = make_page(ann, "Gone soon")
    note = _note(ann, page["id"], f"@{DAY} 15:00 (remind @mt_ann)")
    assert note["id"] in {r["block_id"] for r in _reminders(ann)["reminders"]}
    assert ann.delete(f"/api/blocks/{page['id']}").status_code == 200
    assert note["id"] not in {r["block_id"] for r in _reminders(ann)["reminders"]}


def test_done_reminders_are_remembered_per_account(ann, ben):
    key = _reminders(ann)["reminders"][0]["key"]
    r = ann.post("/api/reminders/done", json={"keys": [key]})
    assert r.status_code == 200 and r.json()["done"] == [key]
    assert _reminders(ann)["done"] == [key]
    assert key not in _reminders(ben)["done"]
    assert ann.post("/api/reminders/done", json={"keys": [key], "done": False}).json()["done"] == []


def _fill(client, page_id, contents):
    """``page_id``'s children replaced by a block per text, in order."""
    r = client.put(f"/api/blocks/{page_id}/children", json={"blocks": [{"content": c} for c in contents]})
    assert r.status_code == 200, r.text


def _dismiss(client, keys, done=True):
    for i in range(0, len(keys), 100):  # what one request takes
        r = client.post("/api/reminders/done", json={"keys": keys[i:i + 100], "done": done})
        assert r.status_code == 200, r.text


def _marks(username):
    """The account's done marks as stored: ``{key: workspace}``."""
    from gamma.db import connect_users_db
    with connect_users_db() as conn:
        return dict(conn.execute("SELECT key, workspace_id FROM reminders_done WHERE user_id = ?",
                                 (account_of(username),)).fetchall())


def test_more_than_500_done_reminders_stay_done():
    make_user("mt_cid", "cidpw12345")
    cid = login("mt_cid", "cidpw12345")
    page = make_page(cid, "Every day")
    _fill(cid, page["id"], [f"Step {i} @2025-01-01 09:00 (remind @mt_cid)" for i in range(620)])
    keys = [r["key"] for r in _reminders(cid)["reminders"]]
    assert len(keys) == 620
    _dismiss(cid, keys)
    got = _reminders(cid)
    assert len(got["reminders"]) == 620 and sorted(got["done"]) == sorted(keys)
    assert set(_marks("mt_cid").values()) == {workspace_of("mt_cid")}  # each placed by the listing
    _dismiss(cid, keys[:1], done=False)
    assert sorted(_reminders(cid)["done"]) == sorted(keys[1:])


def test_a_reminder_among_more_than_500_matching_blocks_is_found():
    # Every one of these blocks has the words the notes-index query asks
    # for; the reminder comes last.
    make_user("mt_dan", "danpw12345")
    dan = login("mt_dan", "danpw12345")
    page = make_page(dan, "Chatter")
    _fill(dan, page["id"], [f"remind mt_dan of item {i}" for i in range(600)]
          + [f"The real one @{DAY} 10:00 (remind @mt_dan)"])
    got = _reminders(dan)["reminders"]
    assert [(r["text"], r["time"]) for r in got] == [(f"The real one @{DAY} 10:00 (remind @mt_dan)", "10:00")]


def test_a_listing_drops_only_the_marks_of_reminders_that_are_gone(ann, monkeypatch):
    from gamma import mentions, workspaces
    make_user("mt_eve", "evepw12345")
    eve = login("mt_eve", "evepw12345")
    mine = workspace_of("mt_eve")
    lab = workspaces.create("Eve lab", account_of("mt_ann"), kind="shared")["id"]
    workspaces.set_member(lab, account_of("mt_eve"), "editor", by=account_of("mt_ann"))
    page = make_page(eve, "Plans")
    deleted = _note(eve, page["id"], f"Deleted @{DAY} 09:00 (remind @mt_eve)")
    edited = _note(eve, page["id"], f"Edited @{DAY} 09:00 (remind @mt_eve)")
    kept = _note(eve, page["id"], f"Kept @{DAY} 09:00 (remind @mt_eve)")
    trashed = make_page(eve, "Trashed soon")
    in_trash = _note(eve, trashed["id"], f"In the trash @{DAY} 09:00 (remind @mt_eve)")
    shared_page = _note(eve, "root", "Lab notes", ws=lab)
    in_lab = _note(eve, shared_page["id"], f"In the lab @{DAY} 09:00 (remind @mt_eve)", ws=lab)
    key = {b["id"]: f"{b['id']}/{DAY}/09:00" for b in (deleted, edited, kept, in_trash, in_lab)}
    _dismiss(eve, list(key.values()))
    assert sorted(_reminders(eve)["done"]) == sorted(key.values())
    assert _marks("mt_eve") == {**{key[b["id"]]: mine for b in (deleted, edited, kept, in_trash)},
                                key[in_lab["id"]]: lab}

    assert eve.delete(f"/api/blocks/{deleted['id']}").status_code == 200
    assert eve.put(f"/api/blocks/{edited['id']}", json={"content": "Edited: no reminder now"}).status_code == 200
    assert eve.delete(f"/api/blocks/{trashed['id']}").status_code == 200  # to Recently deleted
    workspaces.remove_member(lab, account_of("mt_eve"))

    # A workspace that cannot be read keeps its marks.
    real = mentions.connect_pages_db

    def failing(ws):
        if ws == mine:
            raise OSError("disk unplugged")
        return real(ws)
    monkeypatch.setattr(mentions, "connect_pages_db", failing)
    assert _reminders(eve) == {"reminders": [], "done": []}
    assert len(_marks("mt_eve")) == 5
    monkeypatch.setattr(mentions, "connect_pages_db", real)

    # Read whole, it drops the marks of the reminders deleted and edited
    # away; the one in Recently deleted is still there, and the lab, which
    # eve can no longer open, keeps its own.
    assert _reminders(eve)["done"] == [key[kept["id"]]]
    assert set(_marks("mt_eve")) == {key[b["id"]] for b in (kept, in_trash, in_lab)}
    assert eve.post(f"/api/trash/{trashed['id']}/restore").status_code == 200
    workspaces.set_member(lab, account_of("mt_eve"), "editor", by=account_of("mt_ann"))
    assert sorted(_reminders(eve)["done"]) == sorted(key[b["id"]] for b in (kept, in_trash, in_lab))

    # A mark not yet placed goes only once every workspace was read, and
    # never one made after the listing began.
    mentions.set_done(account_of("mt_eve"), ["nowhere/2026-01-01/"], True)
    mentions._settle(account_of("mt_eve"), {}, set(), True, "2000-01-01T00:00:00.000Z")
    assert "nowhere/2026-01-01/" in _marks("mt_eve")
    _reminders(eve)
    assert "nowhere/2026-01-01/" not in _marks("mt_eve")

    # The marks go with their workspace.
    workspaces.delete(lab, by=account_of("mt_ann"))
    assert key[in_lab["id"]] not in _marks("mt_eve")
