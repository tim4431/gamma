"""Sends of a work folder a computer keeps with an agent, the server's side
(step 4c of docs/research/agent-collab.md; gamma/routers/computers.py,
gamma/computers/registry.py, gamma/feedback/requests.py): the announce's
vault agent, kept and shown to its maker alone; the channel's Send routes
with a computer token, each answering only for its account's Sends made in
Gamma in a folder it announced with an agent; the claim keyed by the run (a
retry held, another taker refused); progress and answers only from the run
that holds the request; the answers written here, signed as the agent with
the claim's ``seen``, a retry writing nothing twice; a withdraw told once
and never overwritten; a token's Send and a token's change of a held
request refused; the held poll woken by a Send; a mention made here; the
Feedback document's agent; the pictures of a held thread.

One process plays both sides (as tests/test_computers.py): the routes are
driven directly with the computer's token, as the computer's runner would."""

import json
import threading
import time
from types import SimpleNamespace

import pytest

from conftest import account_of, fresh_client, login, make_folder, make_user, mark_work_folder, thread_on, token_for, \
    work_props
from gamma import storage, workspaces
from gamma.auth import DISK_CLIENT
from gamma.blocks_store import create_page
from gamma.computers import registry
from gamma.db import connect_pages_db, connect_users_db
from gamma.documents.model import document

MAKER, EDITOR, STRANGER = "cps_maker", "cps_editor", "cps_stranger"
FRAME = {"width": 600, "height": 800}
CODEX = [{"name": "codex", "label": "Codex", "found": True, "ready": True, "version": "0.167.0", "code": ""}]


def _pdf() -> bytes:
    import pymupdf

    doc = pymupdf.open()
    doc.new_page(width=600, height=800).insert_text((72, 100), "The loss is defined as follows.", fontsize=14)
    return doc.tobytes()


def _bearer(token: str):
    return fresh_client(headers={"Authorization": f"Bearer {token}"})


def _ok(r, status=200):
    assert r.status_code == status, r.text
    return r.json()


@pytest.fixture(scope="module")
def lab():
    """A shared workspace of cps_maker's, cps_editor an editor there: the
    work folder ``cps-paper`` with ``main.pdf`` (a stored one-page PDF),
    another work folder ``cps-other`` with ``other.pdf``, a plain folder."""
    for name in (MAKER, EDITOR, STRANGER):
        make_user(name, f"pw-{name}")
    maker, editor, stranger = (login(n, f"pw-{n}") for n in (MAKER, EDITOR, STRANGER))
    ws = workspaces.create("Computer sends lab", account_of(MAKER), kind="shared")["id"]
    workspaces.set_member(ws, account_of(EDITOR), "editor", by=account_of(MAKER))
    for c in (maker, editor):
        c.headers["X-Gamma-Workspace"] = ws
    W, W2, plain = make_folder(maker, "cps-paper"), make_folder(maker, "cps-other"), make_folder(maker, "cps-plain")
    mark_work_folder(maker, W)
    mark_work_folder(maker, W2)
    pdf, _ = storage.store_pdf(ws, _pdf())
    with connect_pages_db(ws) as conn:
        main = create_page(conn, "main", work_props(W, "main.pdf", doc=document(pdf, name="main.pdf")),
                           actor=account_of(MAKER), client=DISK_CLIENT)["id"]
        other = create_page(conn, "other", work_props(W2, "other.pdf"), actor=account_of(MAKER),
                            client=DISK_CLIENT)["id"]
    return SimpleNamespace(ws=ws, maker=maker, editor=editor, stranger=stranger, W=W, W2=W2, plain=plain, main=main,
                           other=other, passages=0)


@pytest.fixture(autouse=True)
def _forget(lab):
    """No computer, token or open Send of a test outlives it."""
    yield
    registry.reset()
    with connect_users_db() as conn:
        for name in (MAKER, EDITOR, STRANGER):
            conn.execute("DELETE FROM integration_tokens WHERE user_id = ?", (account_of(name),))
            conn.execute("DELETE FROM computers WHERE user_id = ?", (account_of(name),))
    with connect_pages_db(lab.ws) as conn:
        conn.execute("UPDATE feedback_requests SET state = 'failed' WHERE state IN ('sent', 'running')")
        conn.commit()


def _mint(c, name="Gamma desktop: TIM-PC") -> dict:
    return _ok(c.post("/api/integrations/tokens", json={"name": name, "scope": "computer", "expires_in_days": 365}),
               201)


def _announce(c, *vaults, name="TIM-PC", computer_id="cps-pc") -> str:
    return _ok(c.post("/api/computers/announce", json={"computer": {"id": computer_id, "name": name}, "app": "v9",
                                                       "vaults": list(vaults), "assistants": CODEX}))["version"]


def _kept(lab, agent="codex", **more) -> dict:
    return {"kind": "folder", "folder_id": lab.W, "agent": agent, **more}


@pytest.fixture
def pc(lab):
    """The maker's computer, TIM-PC, keeping ``cps-paper`` with Codex."""
    item = _mint(lab.maker)
    c = _bearer(item["token"])
    version = _announce(c, _kept(lab))
    return SimpleNamespace(c=c, id=item["id"], version=version)


def _passage(lab) -> str:
    """A new passage on main.pdf's page (a block of its own): its id."""
    lab.passages += 1
    block = f"cpsH{lab.passages}"
    pos = {"pageNumber": 1, **FRAME, "boundingRect": {"x1": 72, "y1": 86, "x2": 400, "y2": 104},
           "rects": [{"x1": 72, "y1": 86, "x2": 400, "y2": 104}]}
    _ok(lab.maker.post(f"/api/pages/{lab.main}/ops", json={"client": "t", "ops": [
        {"op": "insert", "id": block, "parent": lab.main, "content": "",
         "props": {"pdf_position": pos, "quote": "The loss is defined as follows"}}]}))
    return block


def _send(lab, *threads, who=None, page=None, status=201) -> dict:
    r = (who or lab.maker).post("/api/feedback/requests",
                                json={"scope": page or lab.main, **({"threads": list(threads)} if threads else {})})
    assert r.status_code == status, r.text
    return r.json().get("request") or r.json()


def _sends(pc) -> dict:
    return _ok(pc.c.get("/api/computers/sends"))


def _claim(pc, request_id, run="run-1", agent="codex", status=200) -> dict:
    r = pc.c.post(f"/api/computers/sends/{request_id}/claim", json={"run": run, "agent": agent})
    assert r.status_code == status, r.text
    return r.json()


def _answers(pc, request_id, run="run-1", replies=(), state="done", status=200, **more) -> dict:
    r = pc.c.post(f"/api/computers/sends/{request_id}/answers",
                  json={"run": run, "replies": list(replies), "state": state, **more})
    assert r.status_code == status, r.text
    return r.json()


def _request(lab, request_id) -> dict:
    return _ok(lab.maker.get(f"/api/feedback/requests/{request_id}"))["request"]


def _comments(lab, thread_id) -> list:
    threads = _ok(lab.maker.get(f"/api/pages/{lab.main}/comments"))["threads"]
    return next(t for t in threads if t["id"] == thread_id)


# --- the binding --------------------------------------------------------------------------------------

def test_the_announce_keeps_a_work_folders_agent_and_shows_it_to_its_maker_alone(lab):
    item = _mint(lab.maker)
    c = _bearer(item["token"])
    _announce(c, _kept(lab), {"kind": "folder", "folder_id": lab.W2, "agent": "gpt"},
              {"kind": "folder", "folder_id": lab.plain, "agent": "claude"},
              {"kind": "file", "page_id": lab.other, "agent": "codex"})
    with connect_users_db() as conn:
        vaults = json.loads(conn.execute("SELECT vaults FROM computers WHERE token_id = ?", (item["id"],)).fetchone()[0])
    assert vaults == [{"kind": "folder", "folder_id": lab.W, "paused": False, "agent": "codex"},
                      {"kind": "folder", "folder_id": lab.W2, "paused": False},          # an agent unknown here: none
                      {"kind": "folder", "folder_id": lab.plain, "paused": False},       # no work folder: none
                      {"kind": "file", "page_id": lab.other, "paused": False}]           # a file vault has none
    # only a work folder's root here counts
    assert registry.agent_folders(item["id"], lab.ws) == {lab.W: {"agent": "codex", "paused": False}}
    found = registry.agent_of(lab.ws, lab.W, account_of(MAKER))
    assert found == {
        "token_id": item["id"], "user_id": account_of(MAKER), "agent": "codex", "label": "Codex", "computer": "TIM-PC",
        "online": True, "paused": False, "ready": True, "code": ""}
    assert registry.agent_of(lab.ws, lab.W, account_of(EDITOR)) is None        # another account's computer: none
    assert registry.agent_of(lab.ws, lab.W2, account_of(MAKER)) is None
    assert registry.agent_of(lab.ws, lab.plain, account_of(MAKER)) is None
    shown = _ok(lab.maker.get(f"/api/pages/{lab.main}/computer"))["computers"]
    assert shown[0]["vault"] == {"kind": "folder", "name": "cps-paper", "paused": False,
                                 "agent": {"name": "codex", "label": "Codex"}}
    assert _ok(lab.editor.get(f"/api/pages/{lab.main}/computer"))["computers"] == []
    # every channel route takes the computer's token alone, and it opens nothing outside the channel
    for c in (lab.maker, _bearer(token_for(lab.maker, "write"))):
        assert c.get("/api/computers/sends").status_code == 403
        assert c.post("/api/computers/sends/x/claim", json={"run": "r", "agent": "codex"}).status_code == 403
    assert _bearer(item["token"]).get("/api/feedback/requests").status_code == 403


def test_another_members_computer_never_takes_the_makers_binding(lab, pc):
    """An editor's computer announcing the maker's work folder with an agent
    serves that editor's own Sends alone; a viewer's keeps no agent."""
    evil = _mint(lab.editor, "Gamma desktop: EVIL-PC")
    _announce(_bearer(evil["token"]), _kept(lab), name="EVIL-PC", computer_id="cps-evil")   # announced last, online
    assert registry.agent_of(lab.ws, lab.W, account_of(MAKER))["computer"] == "TIM-PC"
    assert registry.agent_of(lab.ws, lab.W, account_of(EDITOR))["computer"] == "EVIL-PC"
    # the maker's mention, dialog and token Send still name TIM-PC
    r = _ok(lab.maker.post(f"/api/pages/{lab.main}/comments",
                           json={"block_id": _passage(lab), "body": "@codex please tighten the intro"}), 201)
    assert [s["id"] for s in _sends(pc)["sends"]] == [r["send"]["request_id"]]
    assert _ok(lab.maker.get("/api/feedback", params={"scope": lab.W}))["agent"]["computer"] == "TIM-PC"
    r = _bearer(token_for(lab.maker, "write")).post("/api/feedback/requests", json={"scope": lab.main})
    assert (r.status_code, r.json()["detail"]) == (403, "Codex on TIM-PC takes Sends made in Gamma, not with a token.")
    # the editor's own @codex is the editor's Send, never listed to the maker's computer
    r = _ok(lab.editor.post(f"/api/pages/{lab.main}/comments", json={"block_id": _passage(lab), "body": "@codex hi"}),
            201)
    assert r["send"]["request_id"] not in [s["id"] for s in _sends(pc)["sends"]]
    assert [s["id"] for s in _ok(_bearer(evil["token"]).get("/api/computers/sends"))["sends"]] == \
        [r["send"]["request_id"]]
    # a viewer's computer keeps no agent
    viewer = "cps_viewer"
    make_user(viewer, f"pw-{viewer}")
    workspaces.set_member(lab.ws, account_of(viewer), "viewer", by=account_of(MAKER))
    v = login(viewer, f"pw-{viewer}")
    v.headers["X-Gamma-Workspace"] = lab.ws
    try:
        seen = _mint(v, "Gamma desktop: VIEW-PC")
        _announce(_bearer(seen["token"]), _kept(lab), name="VIEW-PC", computer_id="cps-view")
        with connect_users_db() as conn:
            kept = json.loads(conn.execute("SELECT vaults FROM computers WHERE token_id = ?",
                                           (seen["id"],)).fetchone()[0])
        assert kept == [{"kind": "folder", "folder_id": lab.W, "paused": False}]
        assert registry.agent_folders(seen["id"], lab.ws) == {}
        assert registry.agent_of(lab.ws, lab.W, account_of(viewer)) is None
        # an editor demoted to viewer: their computer serves nothing from then on
        workspaces.set_member(lab.ws, account_of(EDITOR), "viewer", by=account_of(MAKER))
        assert registry.agent_folders(evil["id"], lab.ws) == {}
        assert registry.agent_of(lab.ws, lab.W, account_of(EDITOR)) is None
    finally:
        workspaces.set_member(lab.ws, account_of(EDITOR), "editor", by=account_of(MAKER))
        with connect_users_db() as conn:
            conn.execute("DELETE FROM integration_tokens WHERE user_id = ?", (account_of(viewer),))
            conn.execute("DELETE FROM computers WHERE user_id = ?", (account_of(viewer),))


# --- hearing the Sends ----------------------------------------------------------------------------------

def test_the_makers_sends_made_in_gamma_wait_for_the_computer_and_no_one_elses(lab, pc):
    t = thread_on(lab.maker, lab.main, lab.main, "use viridis")["id"]
    sent = _send(lab, t)
    listed = _sends(pc)
    assert listed == {"sends": [{"id": sent["id"], "folder_id": lab.W, "page_id": lab.main, "at": sent["at"]}],
                      "withdrawn": []}
    _send(lab, t, who=lab.editor)                                       # another account's Send: never this computer's
    o = thread_on(lab.maker, lab.other, lab.other, "elsewhere")["id"]
    _send(lab, o, page=lab.other)                                       # a folder kept without an agent
    whole = _send(lab, page=lab.W)                                      # a Send of the whole folder: no page
    assert [s.get("page_id") for s in _sends(pc)["sends"]] == [lab.main, None] and _sends(pc)["sends"][1]["id"] == whole["id"]
    # a token's Send there is refused: the computer takes the Sends made in Gamma
    r = _bearer(token_for(lab.maker, "write")).post("/api/feedback/requests", json={"scope": lab.main})
    assert (r.status_code, r.json()["detail"]) == (403, "Codex on TIM-PC takes Sends made in Gamma, not with a token.")
    # paused there: nothing waits for it
    _announce(pc.c, _kept(lab, paused=True))
    assert _sends(pc)["sends"] == []
    assert _claim(pc, sent["id"], status=409)["detail"] == "The work folder is paused: Codex starts once it syncs again."


def test_a_send_wakes_the_computers_held_poll_or_its_next(lab, pc):
    t = thread_on(lab.maker, lab.main, lab.main, "wake up")["id"]
    got = []
    poller = threading.Thread(target=lambda: got.append(
        pc.c.get("/api/computers/poll", params={"v": pc.version, "wait": 10}).json()))
    started = time.monotonic()
    poller.start()
    end = time.monotonic() + 5
    while registry._live[pc.id].holding != 1 and time.monotonic() < end:
        time.sleep(0.02)
    _send(lab, t)
    poller.join(5)
    assert got == [{"wake": ["sends"]}] and time.monotonic() - started < 5
    # no poll held: the next one is answered at once
    _send(lab, thread_on(lab.maker, lab.main, lab.main, "again")["id"])
    started = time.monotonic()
    assert _ok(pc.c.get("/api/computers/poll", params={"v": pc.version, "wait": 10})) == {"wake": ["sends"]}
    assert time.monotonic() - started < 5
    # a Send of another account wakes nothing
    _send(lab, t, who=lab.editor)
    assert _ok(pc.c.get("/api/computers/poll", params={"v": pc.version, "wait": 0})) == {}


# --- the claim -------------------------------------------------------------------------------------------

def test_a_claim_is_one_write_keyed_by_its_run(lab, pc):
    block = _passage(lab)
    t = thread_on(lab.maker, lab.main, block, "lambda is never introduced")["id"]
    sent = _send(lab, t)
    claimed = _claim(pc, sent["id"])
    assert set(claimed) == {"request", "entries", "seen", "threads"}
    record = claimed["request"]
    assert (record["state"], record["state_by"], record["detail"]) == ("running", "Codex", "Starting Codex")
    [entry] = claimed["entries"]
    assert entry["id"] == t and entry["kind"] == "passage" and entry["picture"].startswith("img/")
    assert entry["place"]["page"] == 1 and entry["place"]["doc_id"] and len(entry["place"]["rects"]) == 1
    assert "picture_url" not in entry and claimed["seen"] >= sent["at"]
    [said] = claimed["threads"]
    assert (said["id"], said["by_agent"], said["comment_id"], [p["id"] for p in said["people"]]) == (t, False, t, [t])
    # the same run again: it holds it (a lost answer retried)
    again = _claim(pc, sent["id"])
    assert again["request"]["state"] == "running" and again["request"]["rev"] == record["rev"]
    # another run, another computer of the same account: refused
    assert _claim(pc, sent["id"], run="run-2", status=409)["detail"] == "Another run holds this request."
    laptop = _bearer(_mint(lab.maker, "Gamma desktop: LAPTOP")["token"])
    _announce(laptop, _kept(lab), name="LAPTOP", computer_id="cps-laptop")
    r = laptop.post(f"/api/computers/sends/{sent['id']}/claim", json={"run": "run-1", "agent": "codex"})
    assert (r.status_code, r.json()["detail"]) == (409, "Another run holds this request.")
    # a token's change of the request the computer holds is refused; the requester may still withdraw
    r = _bearer(token_for(lab.maker, "feedback", "Claude Code")).post(
        f"/api/feedback/requests/{sent['id']}/state", json={"state": "done"})
    assert (r.status_code, r.json()["detail"]) == (409, "Codex on TIM-PC is answering this request.")
    # not this computer's: another account's Send, another account's computer, another agent, a bad run id
    theirs = _send(lab, t, who=lab.editor)
    assert _claim(pc, theirs["id"], status=404)["detail"] == "no such request"
    stranger = _bearer(_mint(lab.stranger)["token"])
    assert stranger.post(f"/api/computers/sends/{sent['id']}/claim", json={"run": "r", "agent": "codex"}).status_code == 404
    other = _send(lab, thread_on(lab.maker, lab.main, lab.main, "another")["id"])
    assert _claim(pc, other["id"], agent="claude", status=409)["detail"] == "This work folder's agent is Codex."
    assert _claim(pc, other["id"], run="bad id!", status=400)
    # a Send withdrawn before it is claimed is no longer sent
    _ok(lab.maker.post(f"/api/feedback/requests/{other['id']}/state", json={"state": "failed"}))
    assert _claim(pc, other["id"], status=409)["detail"] == "The request is failed now."


# --- the answers -----------------------------------------------------------------------------------------

def test_the_answers_are_written_here_as_the_agents_once(lab, pc):
    fix = thread_on(lab.maker, lab.main, _passage(lab), "define lambda")["id"]
    keep = thread_on(lab.maker, lab.main, lab.main, "use viridis")["id"]
    sent = _send(lab, fix, keep)
    _claim(pc, sent["id"])
    _ok(pc.c.post(f"/api/computers/sends/{sent['id']}/progress", json={"run": "run-1", "detail": "Editing\n main.tex"}))
    assert _request(lab, sent["id"])["detail"] == "Editing main.tex"
    r = pc.c.post(f"/api/computers/sends/{sent['id']}/progress", json={"run": "run-2", "detail": "mine"})
    assert (r.status_code, r.json()["detail"]) == (409, "Another run holds this request.")
    replies = [{"id": "cpsReplyFix1", "thread_id": fix, "text": "Defined it in 2.1. ![x](http://evil.example/a.png)",
                "resolve": True},
               {"id": "cpsReplyKeep1", "thread_id": keep, "text": "Kept the palette: it is the journal's.",
                "resolve": False}]
    # a reply to a thread the request does not name, a path outside the folder: refused, nothing written
    stray = {"id": "cpsStray1", "thread_id": thread_on(lab.maker, lab.main, lab.main, "not sent")["id"], "text": "x"}
    assert _answers(pc, sent["id"], replies=[stray], status=400)["detail"] == "A reply answers a thread of the request."
    for path in ("../secret.txt", "C:/Users/tim/main.tex", "/etc/passwd", "a\\b.tex"):
        _answers(pc, sent["id"], replies=replies, changed=[path], status=400)
    assert _request(lab, sent["id"])["state"] == "running"
    out = _answers(pc, sent["id"], replies=replies, summary="Fixed the notation", changed=["main.tex"])
    assert out == {"ok": True, "written": ["cpsReplyFix1", "cpsReplyKeep1"]}
    record = _request(lab, sent["id"])
    assert (record["state"], record["state_by"], record["detail"]) == ("done", "Codex",
                                                                      "Fixed the notation. Changed main.tex.")
    assert all(t["answered"] for t in record["threads"])
    fixed, kept = _comments(lab, fix), _comments(lab, keep)
    reply = fixed["comments"][-1]
    assert (reply["id"], reply["agent"], reply.get("agent_kind")) == ("cpsReplyFix1", "Codex", "local")
    assert reply["body"] == "Defined it in 2.1. [x](http://evil.example/a.png)"     # inert: no picture loads
    assert fixed["resolved_at"] and not kept["resolved_at"]
    # a retry writes nothing twice and answers the same
    assert _answers(pc, sent["id"], replies=replies, summary="Fixed the notation") == out
    assert len(_comments(lab, fix)["comments"]) == 2 and len(_comments(lab, keep)["comments"]) == 2
    # the run's end is written: no more progress, nothing from another run
    r = pc.c.post(f"/api/computers/sends/{sent['id']}/progress", json={"run": "run-1", "detail": "late"})
    assert (r.status_code, r.json()["detail"]) == (409, "The request is done now.")
    assert _answers(pc, sent["id"], run="run-2", replies=replies, status=409)["detail"] == "Another run holds this request."
    assert _sends(pc) == {"sends": [], "withdrawn": []}


def test_a_thread_a_person_spoke_in_during_the_run_stays_open_and_a_failure_is_told(lab, pc):
    spoken = thread_on(lab.maker, lab.main, lab.main, "shorten the abstract")["id"]
    sent = _send(lab, spoken)
    _claim(pc, sent["id"])
    _ok(lab.maker.post(f"/api/pages/{lab.main}/comments", json={"block_id": lab.main, "thread_id": spoken,
                                                                "body": "and keep the second sentence"}), 201)
    _answers(pc, sent["id"], replies=[{"id": "cpsReplySpoken1", "thread_id": spoken, "text": "Shortened.",
                                       "resolve": True}], summary="")
    assert not _comments(lab, spoken)["resolved_at"]                      # a word the run never read: still open
    assert _request(lab, sent["id"])["detail"] == "Answered 1 of 1 comment."
    # a run that failed says why, in the words it gave
    again = _send(lab, spoken)
    _claim(pc, again["id"], run="run-f")
    _answers(pc, again["id"], run="run-f", state="failed", error="Codex is not signed in on this computer")
    record = _request(lab, again["id"])
    assert (record["state"], record["detail"]) == ("failed", "Codex is not signed in on this computer.")


def test_a_withdraw_stops_the_run_once_and_is_never_overwritten(lab, pc):
    t = thread_on(lab.maker, lab.main, lab.main, "rename the variable")["id"]
    sent = _send(lab, t)
    _claim(pc, sent["id"], run="run-w")
    _ok(lab.maker.post(f"/api/feedback/requests/{sent['id']}/state", json={"state": "failed"}))
    assert _sends(pc)["withdrawn"] == [{"id": sent["id"], "run": "run-w"}]
    assert _sends(pc)["withdrawn"] == []                                   # told once
    r = pc.c.post(f"/api/computers/sends/{sent['id']}/progress", json={"run": "run-w", "detail": "still here"})
    assert (r.status_code, r.json()["detail"]) == (409, "The request is failed now.")
    r = _answers(pc, sent["id"], run="run-w", replies=[{"id": "cpsReplyLate1", "thread_id": t, "text": "Renamed.",
                                                        "resolve": True}], status=409)
    assert r["detail"] == "The request is failed now."
    record = _request(lab, sent["id"])
    assert (record["state"], record["detail"]) == ("failed", "withdrawn")
    assert [c["id"] for c in _comments(lab, t)["comments"]] == [t]        # nothing written


def test_an_agent_taking_a_withdrawn_request_again_ends_the_computers_run(lab, pc):
    t = thread_on(lab.maker, lab.main, lab.main, "retake me")["id"]
    sent = _send(lab, t)
    _claim(pc, sent["id"], run="run-a")
    _ok(lab.maker.post(f"/api/feedback/requests/{sent['id']}/state", json={"state": "failed"}))  # the computer sleeps
    agent = _bearer(token_for(lab.maker, "feedback", "Claude Code"))
    _ok(agent.post(f"/api/feedback/requests/{sent['id']}/state", json={"state": "running", "detail": "On it"}))
    with connect_pages_db(lab.ws) as conn:
        assert conn.execute("SELECT ended FROM feedback_request_runs WHERE request_id = ?",
                            (sent["id"],)).fetchone()[0] == "stopped"
    _ok(agent.post(f"/api/feedback/requests/{sent['id']}/state", json={"state": "done", "detail": "Did it"}))
    # the old run wakes: refused, nothing written over the agent's work
    r = _answers(pc, sent["id"], run="run-a", replies=[{"id": "cpsReplyOld1", "thread_id": t, "text": "Late.",
                                                         "resolve": True}], state="failed", status=409)
    assert r["detail"] == "The request is done now."
    record = _request(lab, sent["id"])
    assert (record["state"], record["detail"]) == ("done", "Did it")
    assert [c["id"] for c in _comments(lab, t)["comments"]] == [t]
    assert _sends(pc)["withdrawn"] == []


def test_a_computers_token_gone_fails_the_sends_its_runs_hold(lab, pc):
    t = thread_on(lab.maker, lab.main, lab.main, "hold this")["id"]
    held, waiting = _send(lab, t), _send(lab, thread_on(lab.maker, lab.main, lab.main, "wait")["id"])
    _claim(pc, held["id"], run="run-g")
    r = lab.maker.delete(f"/api/integrations/tokens/{pc.id}")             # Stop showing, or a new token replaced it
    assert _ok(r)["revoked"] is True
    record = _request(lab, held["id"])
    assert (record["state"], record["state_by"], record["detail"]) == (
        "failed", "Codex", "The computer's channel was taken back before Codex finished.")
    assert _request(lab, waiting["id"])["state"] == "sent"                  # a Send not taken waits for another
    with connect_pages_db(lab.ws) as conn:
        assert conn.execute("SELECT ended FROM feedback_request_runs WHERE request_id = ?",
                            (held["id"],)).fetchone()[0] == "stopped"
    assert pc.c.get("/api/computers/sends").status_code == 401


# --- the pictures ----------------------------------------------------------------------------------------

def test_the_pictures_of_a_thread_the_computer_holds(lab, pc):
    t = thread_on(lab.maker, lab.main, _passage(lab), "this figure")["id"]
    elsewhere = thread_on(lab.maker, lab.main, _passage(lab), "not sent")["id"]
    sent = _send(lab, t)
    picture = f"/api/computers/threads/{t}/picture"
    assert pc.c.get(picture).status_code == 404                           # not held yet
    _claim(pc, sent["id"])
    r = pc.c.get(picture)
    assert r.status_code == 200 and r.headers["content-type"] == "image/jpeg" and r.content[:2] == b"\xff\xd8"
    assert pc.c.get(picture, params={"at": "before"}).status_code == 404
    assert pc.c.get(picture, params={"at": "later"}).status_code == 400
    assert pc.c.get(f"/api/computers/threads/{elsewhere}/picture").status_code == 404
    laptop = _bearer(_mint(lab.maker, "Gamma desktop: LAPTOP")["token"])
    assert laptop.get(picture).status_code == 404                         # another computer holds nothing
    _answers(pc, sent["id"], replies=[{"id": "cpsReplyPic1", "thread_id": t, "text": "Done.", "resolve": True}])
    assert pc.c.get(picture).status_code == 404                           # the run ended


# --- the mention and the Feedback dialog ------------------------------------------------------------------

def test_a_mention_made_here_is_a_send_the_computer_takes(lab, pc):
    r = _ok(lab.maker.post(f"/api/pages/{lab.main}/comments",
                           json={"block_id": _passage(lab), "body": "@codex please tighten the intro"}), 201)
    send = r["send"]
    assert (send["agent"], send["scope"], send["threads"]) == ("Codex", "thread", 1)
    assert [s["id"] for s in _sends(pc)["sends"]] == [send["request_id"]]
    assert _request(lab, send["request_id"])["note"] == "please tighten the intro"
    # another member's @codex is no Send; nor the maker's once the agent is off there
    r = _ok(lab.editor.post(f"/api/pages/{lab.main}/comments", json={"block_id": lab.main, "body": "@codex hi"}), 201)
    assert "send" not in r
    _announce(pc.c, {"kind": "folder", "folder_id": lab.W})
    r = _ok(lab.maker.post(f"/api/pages/{lab.main}/comments", json={"block_id": lab.main, "body": "@codex hi"}), 201)
    assert "send" not in r


def test_the_feedback_document_names_the_computers_agent_to_its_maker_alone(lab, pc):
    agent = _ok(lab.maker.get("/api/feedback", params={"scope": lab.W}))["agent"]
    assert agent == {"name": "codex", "label": "Codex", "computer": "TIM-PC", "online": True, "ready": True,
                     "problem": "", "takes": True, "why": ""}
    assert not _ok(lab.editor.get("/api/feedback", params={"scope": lab.W})).get("agent")
    assert not _ok(lab.maker.get("/api/feedback", params={"scope": lab.W2})).get("agent")
    _announce(pc.c, _kept(lab, paused=True))
    agent = _ok(lab.maker.get("/api/feedback", params={"scope": lab.W}))["agent"]
    assert (agent["takes"], agent["why"]) == (False, "The work folder is paused on TIM-PC: Codex starts once it syncs "
                                                     "again.")
    # away: a Send waits for it, and the dialog says so
    _announce(pc.c, _kept(lab))
    registry.reset()
    agent = _ok(lab.maker.get("/api/feedback", params={"scope": lab.W}))["agent"]
    assert (agent["online"], agent["problem"], agent["takes"]) == (False, "TIM-PC is offline: this runs when it "
                                                                         "connects.", True)
    t = thread_on(lab.maker, lab.main, lab.main, "while away")["id"]
    sent = _send(lab, t)
    _announce(pc.c, _kept(lab))                                           # back: it asks for what waits
    assert [s["id"] for s in _sends(pc)["sends"]] == [sent["id"]]


@pytest.mark.parametrize("code, found, problem", [
    ("signed_out", True, "Codex is not signed in on TIM-PC."),
    ("missing", False, "Codex is not on TIM-PC."),
    ("not_ready", True, "Codex is not ready on TIM-PC."),
])
def test_the_feedback_document_says_why_an_online_computers_agent_is_not_ready(lab, pc, code, found, problem):
    _ok(pc.c.post("/api/computers/announce", json={
        "computer": {"id": "cps-pc", "name": "TIM-PC"}, "app": "v9", "vaults": [_kept(lab)],
        "assistants": [{**CODEX[0], "found": found, "ready": False, "code": code}]}))
    agent = _ok(lab.maker.get("/api/feedback", params={"scope": lab.W}))["agent"]
    assert (agent["online"], agent["ready"], agent["problem"], agent["takes"]) == (True, False, problem, True)
