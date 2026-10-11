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
