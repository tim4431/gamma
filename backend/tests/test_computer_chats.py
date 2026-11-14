"""The chat's computer side on a page of a work folder a computer keeps with
an agent, the server's side (step 4d of docs/research/agent-collab.md;
gamma/routers/local_agents.py, gamma/local_agents/chats.py,
gamma/routers/computers.py, gamma/computers/registry.py): the announce's
models kept; the chat routes answering the maker of the folder's binding
alone (another member, a token, the guest, another page: refused), the
strip from the binding and presence, the asks refused, the sources null,
no terminal; a turn written here and its computer woken, waiting while it
is away; the channel's chat routes with a computer token, answering only
for its account's turns in a folder it announced with an agent; the claim
keyed by the run (a retry held, another taker refused), with the message,
the earlier ones and the page's path; the writes taken only from the run
that holds the turn running and only when newer (``seq``), the text made
inert, the paths relative; Stop of a queued and of a running turn, the
computer told, one away ended at once; a token taken back failing what its
runs hold; New chat, History and delete; Background tasks rows.

One process plays both sides (as tests/test_computer_sends.py): the
routes are driven directly with the computer's token, as the computer's
runner would."""

import threading
import time
from types import SimpleNamespace

import pytest

from conftest import account_of, fresh_client, login, make_folder, make_page, make_user, mark_work_folder, \
    token_for, work_props
from gamma import workspaces
from gamma.auth import DISK_CLIENT
from gamma.blocks_store import create_page
from gamma.computers import registry
from gamma.db import connect_pages_db, connect_users_db
from gamma.local_agents import chats

MAKER, EDITOR, STRANGER = "ccx_maker", "ccx_editor", "ccx_stranger"
MODELS = [{"id": "gpt-5", "label": "GPT-5", "efforts": ["low", "medium", "high"], "effort": "medium", "default": True},
          {"id": "gpt-5-mini", "label": "GPT-5 mini", "efforts": [], "effort": None, "default": False}]
CODEX = [{"name": "codex", "label": "Codex", "found": True, "ready": True, "version": "0.167.0", "code": "",
          "models": MODELS}]


def _bearer(token: str):
    return fresh_client(headers={"Authorization": f"Bearer {token}"})


def _ok(r, status=200):
    assert r.status_code == status, r.text
    return r.json()


@pytest.fixture(scope="module")
def lab():
    """A shared workspace of ccx_maker's, ccx_editor an editor there: the
    work folder ``ccx-paper`` with ``main.pdf``, another work folder
    ``ccx-other`` with ``other.pdf``, a library page."""
    for name in (MAKER, EDITOR, STRANGER):
        make_user(name, f"pw-{name}")
    maker, editor, stranger = (login(n, f"pw-{n}") for n in (MAKER, EDITOR, STRANGER))
    ws = workspaces.create("Computer chats lab", account_of(MAKER), kind="shared")["id"]
    workspaces.set_member(ws, account_of(EDITOR), "editor", by=account_of(MAKER))
    for c in (maker, editor, stranger):
        c.headers["X-Gamma-Workspace"] = ws
    W, W2 = make_folder(maker, "ccx-paper"), make_folder(maker, "ccx-other")
    mark_work_folder(maker, W)
    mark_work_folder(maker, W2)
    with connect_pages_db(ws) as conn:
        main = create_page(conn, "main", work_props(W, "main.pdf"), actor=account_of(MAKER), client=DISK_CLIENT)["id"]
        other = create_page(conn, "other", work_props(W2, "other.pdf"), actor=account_of(MAKER),
                            client=DISK_CLIENT)["id"]
    plain = make_page(maker, "A library page")["id"]
    return SimpleNamespace(ws=ws, maker=maker, editor=editor, stranger=stranger, W=W, W2=W2, main=main, other=other,
                           plain=plain)


@pytest.fixture(autouse=True)
def _forget(lab, monkeypatch):
    """This server runs no agents of its own (a NAS); no computer, token or
    conversation of a test outlives it."""
    monkeypatch.setenv("GAMMA_LOCAL_AGENTS", "0")
    yield
    registry.reset()
    with connect_users_db() as conn:
        for name in (MAKER, EDITOR, STRANGER):
            conn.execute("DELETE FROM integration_tokens WHERE user_id = ?", (account_of(name),))
            conn.execute("DELETE FROM computers WHERE user_id = ?", (account_of(name),))
    with connect_pages_db(lab.ws) as conn:
        conn.execute("DELETE FROM computer_chats")
        conn.execute("DELETE FROM computer_chat_runs")
        conn.commit()


def _mint(c, name="Gamma desktop: TIM-PC") -> dict:
    return _ok(c.post("/api/integrations/tokens", json={"name": name, "scope": "computer", "expires_in_days": 365}),
               201)


def _announce(c, *vaults, name="TIM-PC", computer_id="ccx-pc", assistants=CODEX) -> str:
    return _ok(c.post("/api/computers/announce", json={"computer": {"id": computer_id, "name": name}, "app": "v9",
                                                       "vaults": list(vaults), "assistants": assistants}))["version"]


def _kept(lab, agent="codex", **more) -> dict:
    return {"kind": "folder", "folder_id": lab.W, "agent": agent, **more}


@pytest.fixture
def pc(lab):
    """The maker's computer, TIM-PC, keeping ``ccx-paper`` with Codex."""
    item = _mint(lab.maker)
    c = _bearer(item["token"])
    version = _announce(c, _kept(lab))
    return SimpleNamespace(c=c, id=item["id"], version=version)


def _chat(lab, who=None, page=None, status=200) -> dict:
    r = (who or lab.maker).get("/api/local-agents/chat", params={"page": page or lab.main})
    assert r.status_code == status, r.text
    return r.json()


def _say(lab, text="Shorten the title.", who=None, page=None, status=202, **body) -> dict:
    r = (who or lab.maker).post("/api/local-agents/chat/turns", json={"page": page or lab.main, "text": text, **body})
    assert r.status_code == status, r.text
    return r.json()


def _reply(lab) -> dict:
    return [m for m in _chat(lab)["conversation"]["messages"] if m["role"] == "agent"][-1]


def _chats(pc) -> dict:
    return _ok(pc.c.get("/api/computers/chats"))


def _claim(pc, turn_id, run="run-1", agent="codex", status=200) -> dict:
    r = pc.c.post(f"/api/computers/chats/{turn_id}/claim", json={"run": run, "agent": agent})
    assert r.status_code == status, r.text
    return r.json()


def _write(pc, turn_id, seq, run="run-1", status=200, **fields) -> dict:
    r = pc.c.post(f"/api/computers/chats/{turn_id}/write", json={"run": run, "seq": seq, "fields": fields})
    assert r.status_code == status, r.text
    return r.json()


def _row(lab, turn_id) -> dict:
    with connect_pages_db(lab.ws) as conn:
        row = conn.execute("SELECT holder, run, seq, stop, state FROM computer_chat_runs WHERE turn_id = ?",
                           (turn_id,)).fetchone()
    return dict(zip(("holder", "run", "seq", "stop", "state"), row)) if row else None


# --- the announce's models --------------------------------------------------------------------------

def test_the_announce_keeps_each_assistants_models(lab):
    item = _mint(lab.maker)
    c = _bearer(item["token"])
    many = [{"id": f"m{i}", "label": "M", "efforts": [f"e{j}" for j in range(12)], "effort": "e0"} for i in range(3)]
    _announce(c, _kept(lab), assistants=[{**CODEX[0], "models": [*MODELS, {"label": "no id"}, MODELS[0], *many]},
                                         {"name": "claude", "label": "Claude Code", "found": False, "ready": False,
                                          "code": "missing"}])
    assistants, _seen = registry.said_of(item["id"])
    assert assistants[0]["models"][:2] == MODELS                      # one without an id, and a repeat, left out
    assert [m["id"] for m in assistants[0]["models"]] == ["gpt-5", "gpt-5-mini", "m0", "m1", "m2"]
    assert assistants[0]["models"][2]["efforts"] == [f"e{j}" for j in range(registry.MAX_EFFORTS)]
    assert assistants[1]["models"] == []                              # none announced: none
    for bad in ({**CODEX[0], "models": "all"}, {**CODEX[0], "models": ["gpt-5"]},
                {**CODEX[0], "models": [{"id": f"m{i}"} for i in range(registry.MAX_MODELS + 1)]}):
        r = c.post("/api/computers/announce", json={"computer": {"id": "ccx-pc", "name": "TIM-PC"}, "vaults": [],
                                                    "assistants": [bad]})
        assert r.status_code == 400, bad


# --- who may --------------------------------------------------------------------------------------------

def test_the_chat_answers_the_maker_of_the_folders_binding_alone(lab, pc, anon, guest):
    assert _chat(lab) == {"computer": {
        "agent": "codex", "label": "Codex", "ready": True, "problem": "", "models": MODELS, "folder": "ccx-paper",
        "paused": False, "turn": None, "session": False, "computer": "TIM-PC", "online": True, "since": None,
        "remote": True}, "conversation": None}
    for status, who in ((404, lab.editor), (403, lab.stranger), (401, anon)):   # no member: the workspace's 403
        assert who.get("/api/local-agents/chat", params={"page": lab.main}).status_code == status
        assert who.post("/api/local-agents/chat/turns", json={"page": lab.main, "text": "hi"}).status_code == status
        assert who.post("/api/local-agents/chat/new", json={"page": lab.main}).status_code == status
        assert who.get("/api/local-agents/chat/history", params={"page": lab.main}).status_code == status
    script = fresh_client()
    script.headers.update({"Authorization": f"Bearer {token_for(lab.maker, 'write', 'Script')}",
                           "X-Gamma-Workspace": lab.ws})
    assert script.get("/api/local-agents/chat", params={"page": lab.main}).status_code == 403
    assert guest.get("/api/local-agents/chat", params={"page": lab.main}).status_code == 403
    assert pc.c.get("/api/local-agents/chat", params={"page": lab.main}).status_code == 403   # the channel's token
    # another work folder (not kept with an agent), a library page, no page
    for page in (lab.other, lab.plain, ""):
        assert lab.maker.get("/api/local-agents/chat", params={"page": page}).status_code == 404
    # a co-author reaches none of the maker's turns or conversations
    made = _say(lab)
    assert lab.editor.post(f"/api/local-agents/chat/turns/{made['turn']['id']}/stop").status_code == 404
    assert lab.editor.post(f"/api/local-agents/chat/history/{made['conversation']['id']}/open").status_code == 404
    # an editor's own computer keeping the folder serves that editor's chat, never the maker's turn
    evil = _mint(lab.editor, "Gamma desktop: EVIL-PC")
    evil_c = _bearer(evil["token"])
    _announce(evil_c, _kept(lab), name="EVIL-PC", computer_id="ccx-evil")
    assert _ok(evil_c.get("/api/computers/chats")) == {"turns": [], "stops": []}
    assert evil_c.post(f"/api/computers/chats/{made['turn']['id']}/claim",
                       json={"run": "r", "agent": "codex"}).status_code == 404
    assert _chat(lab, who=lab.editor)["computer"]["computer"] == "EVIL-PC"
    # the computer gone: no seat, though the conversation stays
    registry.forget(pc.id)
    assert lab.maker.get("/api/local-agents/chat", params={"page": lab.main}).status_code == 404
    with connect_pages_db(lab.ws) as conn:
        assert conn.execute("SELECT COUNT(*) FROM computer_chats").fetchone()[0] == 1


def test_the_channels_chat_routes_take_the_computers_token_alone(lab, pc):
    for c in (lab.maker, _bearer(token_for(lab.maker, "write"))):
        assert c.get("/api/computers/chats").status_code == 403
        assert c.post("/api/computers/chats/x/claim", json={"run": "r", "agent": "codex"}).status_code == 403
        assert c.post("/api/computers/chats/x/write", json={"run": "r", "seq": 1, "fields": {}}).status_code == 403


# --- a turn ------------------------------------------------------------------------------------------------

def test_a_turn_waits_here_runs_there_and_its_message_comes_back(lab, pc):
    got = []
    poller = threading.Thread(target=lambda: got.append(
        pc.c.get("/api/computers/poll", params={"v": pc.version, "wait": 10}).json()))
    poller.start()
    end = time.monotonic() + 5
    while registry._live[pc.id].holding != 1 and time.monotonic() < end:
        time.sleep(0.02)
    selection = {"text": "The loss is defined as follows", "page": 1, "box": [0.1, 0.1, 0.6, 0.15]}
    made = _say(lab, "Tighten this paragraph.", selections=[selection], model="gpt-5", effort="high", id="ccxMsg1")
    poller.join(5)
    assert got == [{"wake": ["chat"]}]                                  # the computer is woken for it
    turn = made["turn"]["id"]
    assert made["turn"]["state"] == "queued"
    assert _say(lab, "Tighten this paragraph.", id="ccxMsg1")["turn"]["id"] == turn   # a retried POST
    assert _chat(lab)["computer"]["turn"] == {"turn_id": turn, "state": "queued", "ahead": False}
    with connect_pages_db(lab.ws) as conn:
        at = conn.execute("SELECT queued_at FROM computer_chat_runs WHERE turn_id = ?", (turn,)).fetchone()[0]
    assert _chats(pc) == {"turns": [{"id": turn, "conversation_id": made["conversation"]["id"], "page_id": lab.main,
                                     "folder_id": lab.W, "at": at}], "stops": []}
    assert _say(lab, "And another.", status=409)["detail"].startswith("Codex is still working")

    claimed = _claim(pc, turn)
    assert claimed["turn"] == {"id": turn, "conversation_id": made["conversation"]["id"], "page_id": lab.main,
                               "text": "Tighten this paragraph.", "selections": [selection], "model": "gpt-5",
                               "effort": "high"}
    assert claimed["history"] == [] and claimed["page"] == {"path": "main.pdf", "title": "main"} and claimed["seen"]
    assert _reply(lab)["state"] == "running" and _chats(pc)["turns"] == []
    assert _chat(lab)["computer"]["turn"]["state"] == "running"

    assert _write(pc, turn, 1, state="running", text="Working ![x](http://evil.example/a.png)",
                  steps=[{"id": "s1", "kind": "read", "status": "done", "path": "main.tex"},
                         {"id": "s2", "kind": "edit", "status": "done", "path": "C:/Users/tim/secret.txt"},
                         {"id": "s3", "kind": "run", "status": "started", "program": "latexmk"}],
                  asks=[{"id": "a1"}]) == {"ok": True}
    reply = _reply(lab)
    assert reply["text"] == "Working [x](http://evil.example/a.png)"             # made inert again
    assert reply["steps"] == [{"id": "s1", "kind": "read", "status": "done", "path": "main.tex"},
                              {"id": "s2", "kind": "edit", "status": "done", "path": "outside"},
                              {"id": "s3", "kind": "run", "status": "started", "program": "latexmk"}]
    assert reply["asks"] == []                                                    # it never asks
    # a retry, or a late write, never goes back
    assert _write(pc, turn, 1, text="Older") == {"ok": True, "stale": True}
    assert _write(pc, turn, 0, text="Oldest") == {"ok": True, "stale": True}
    assert _reply(lab)["text"] == "Working [x](http://evil.example/a.png)"
    # a state it may not write, a field of another shape
    for bad in ({"state": "queued"}, {"text": 3}, {"steps": "all"}, {"changed": -1}, {"built": "yes"},
                {"changes": "main.tex"}, {"changes": [3]}):
        _write(pc, turn, 5, status=400, **bad)
    assert _row(lab, turn)["seq"] == 1

    # a changed file named by more than its path (a name the computer's disk allows) is left out, not refused
    assert _write(pc, turn, 2, state="done", text="Done: shorter now.", changed=1, built=True, fresh=False,
                  changes=["main.tex", "notes/fig:1.png", "a\\b.tex", "../outside.tex", "C:/x.tex", "/etc/x",
                           "x" * 1025], ended_at="1999-01-01") == {"ok": True}
    reply = _reply(lab)
    assert (reply["state"], reply["text"], reply["changes"], reply["changed"], reply["built"]) == (
        "done", "Done: shorter now.", ["main.tex"], 1, True)
    assert reply["ended_at"] and reply["ended_at"] != "1999-01-01"               # this server's clock
    assert _row(lab, turn)["state"] == "done"
    assert _write(pc, turn, 2, state="done") == {"ok": True, "stale": True}      # the end retried
    assert _write(pc, turn, 3, text="After the end", status=409)["detail"] == "The turn is done now."
    assert _chat(lab)["computer"]["turn"] is None

    # a follow-up: the earlier messages as the turn's prompt takes them
    second = _say(lab, "Now the abstract.")["turn"]["id"]
    assert _claim(pc, second, run="run-2")["history"] == [
        {"role": "owner", "text": "Tighten this paragraph."}, {"role": "agent", "text": "Done: shorter now."}]


def test_the_claim_is_keyed_by_the_run(lab, pc):
    turn = _say(lab)["turn"]["id"]
    assert _claim(pc, turn, run="bad id!", status=400)
    assert _claim(pc, turn, agent="claude", status=409)["detail"] == "This work folder's agent is Codex."
    assert _claim(pc, "no-such-turn", status=404)
    first = _claim(pc, turn)
    again = _claim(pc, turn)                                                     # a retry: held
    assert again["turn"] == first["turn"] and _row(lab, turn)["run"] == "run-1"
    assert _claim(pc, turn, run="run-2", status=409)["detail"] == "Another run holds this turn."
    assert _write(pc, turn, 1, run="run-2", text="Not mine", status=409)["detail"] == "Another run holds this turn."
    # another computer of the same account: not this one's turn to write
    laptop = _mint(lab.maker, "Gamma desktop: LAPTOP")
    lc = _bearer(laptop["token"])
    _announce(lc, _kept(lab), name="LAPTOP", computer_id="ccx-laptop")
    r = lc.post(f"/api/computers/chats/{turn}/write", json={"run": "run-1", "seq": 9, "fields": {"text": "x"}})
    assert r.status_code == 404


def test_a_turn_whose_agent_changed_fails_at_the_announce(lab, pc):
    turn = _say(lab)["turn"]["id"]
    _announce(pc.c, _kept(lab, agent="claude"),
              assistants=[*CODEX, {"name": "claude", "label": "Claude Code", "found": True, "ready": True}])
    reply = _reply(lab)
    assert (reply["state"], reply["error"]) == ("failed", "Codex is no longer this folder's agent.")
    assert _chats(pc)["turns"] == [] and _row(lab, turn)["state"] == "failed"
    assert _claim(pc, turn, agent="claude", status=409)["detail"] == "The turn is failed now."


CLAUDE = [{"name": "claude", "label": "Claude Code", "found": True, "ready": True, "version": "2", "code": ""}]


def _laptop(lab, agent="codex") -> SimpleNamespace:
    """The maker's other computer, LAPTOP, keeping ``ccx-paper`` with ``agent``."""
    item = _mint(lab.maker, "Gamma desktop: LAPTOP")
    c = _bearer(item["token"])
    _announce(c, _kept(lab, agent=agent), name="LAPTOP", computer_id="ccx-laptop",
              assistants=CLAUDE if agent == "claude" else CODEX)
    return SimpleNamespace(c=c, id=item["id"])


def test_a_turn_is_listed_to_the_computer_with_its_agent_alone(lab, pc):
    """TIM-PC keeps the folder with Codex, LAPTOP (seen last) with Claude
    Code: the turn is Claude Code's, so TIM-PC is never given it, and its
    claim leaves it queued for LAPTOP."""
    lc = _laptop(lab, "claude")
    with connect_users_db() as conn:
        conn.execute("UPDATE computers SET seen_at = '2000-01-01T00:00:00Z' WHERE token_id = ?", (pc.id,))
    assert _chat(lab)["computer"]["computer"] == "LAPTOP"
    turn = _say(lab)["turn"]["id"]
    assert _reply(lab)["agent"] == "claude"
    assert _chats(pc)["turns"] == []
    assert _claim(pc, turn, status=409)["detail"] == "This turn is for Claude Code."
    assert _row(lab, turn)["state"] == "queued" and _reply(lab)["state"] == "queued"
    assert [t["id"] for t in _chats(lc)["turns"]] == [turn]
    _claim(lc, turn, agent="claude")
    assert _row(lab, turn)["state"] == "running"


def test_a_paused_folder_takes_no_turn(lab, pc):
    turn = _say(lab)["turn"]["id"]
    _announce(pc.c, _kept(lab, paused=True))
    assert _chats(pc)["turns"] == []
    assert _claim(pc, turn, status=409)["detail"] == "The work folder is paused: Codex starts once it syncs again."
    assert _chat(lab)["computer"]["paused"] is True
    r = lab.maker.post("/api/local-agents/chat/turns", json={"page": lab.main, "text": "More"})
    assert r.status_code == 409 and r.json()["detail"] == \
        "The work folder is paused on TIM-PC: Codex starts once it syncs again."


def test_the_model_and_the_effort_are_among_the_announced(lab, pc):
    assert _say(lab, model="gpt-6", status=400)["detail"] == "Codex does not offer that model on TIM-PC."
    assert _say(lab, model="gpt-5-mini", effort="high", status=400)["detail"] == \
        "Codex does not offer that effort for this model."
    assert _say(lab, effort="low")["turn"]["state"] == "queued"                  # the default model's effort


def test_a_message_while_the_computer_is_away_waits_for_it(lab, pc):
    registry.reset()                                                             # offline: no poll, no grace
    with connect_users_db() as conn:
        seen_at = conn.execute("SELECT seen_at FROM computers WHERE token_id = ?", (pc.id,)).fetchone()[0]
    strip = _chat(lab)["computer"]
    assert (strip["online"], strip["since"], strip["remote"]) == (False, seen_at, True)
    turn = _say(lab)["turn"]["id"]
    assert _chat(lab)["computer"]["turn"] == {"turn_id": turn, "state": "queued", "ahead": False}
    version = _announce(pc.c, _kept(lab))                                         # back
    assert _ok(pc.c.get("/api/computers/poll", params={"v": version, "wait": 0})) == {}
    assert [t["id"] for t in _chats(pc)["turns"]] == [turn]
    # back but its agent is not ready: refused at once, in the @ menu's words
    _announce(pc.c, _kept(lab), assistants=[{**CODEX[0], "ready": False, "code": "signed_out"}])
    assert _chat(lab)["computer"]["problem"] == "Codex is not signed in on TIM-PC."


# --- Stop -------------------------------------------------------------------------------------------------

def test_stop_of_a_queued_and_of_a_running_turn(lab, pc):
    queued = _say(lab)["turn"]["id"]
    assert _ok(lab.maker.post(f"/api/local-agents/chat/turns/{queued}/stop")) == {"stopped": True}
    assert _reply(lab)["state"] == "stopped" and _row(lab, queued)["state"] == "stopped"
    assert _chats(pc)["turns"] == []
    assert _claim(pc, queued, status=409)["detail"] == "The turn is stopped now."
    assert _ok(lab.maker.post(f"/api/local-agents/chat/turns/{queued}/stop")) == {"stopped": False}

    # running, the computer online: its stop is set and the computer is told
    running = _say(lab, "Again.")["turn"]["id"]
    _claim(pc, running)
    _ok(pc.c.get("/api/computers/poll", params={"v": pc.version, "wait": 0}))   # the begin's wake answered
    assert _ok(lab.maker.post(f"/api/local-agents/chat/turns/{running}/stop")) == {"stopped": True}
    assert _ok(pc.c.get("/api/computers/poll", params={"v": pc.version, "wait": 0})) == {"wake": ["chat"]}
    assert _reply(lab)["state"] == "running" and _row(lab, running)["stop"] == 1
    assert _chats(pc)["stops"] == [{"id": running, "run": "run-1"}]
    assert _chats(pc)["stops"] == [{"id": running, "run": "run-1"}]               # until the run writes its end
    assert _write(pc, running, 1, text="Half done.") == {"ok": True}
    assert _write(pc, running, 2, state="stopped") == {"ok": True}
    assert _reply(lab)["state"] == "stopped" and _reply(lab)["text"] == "Half done."
    assert _chats(pc)["stops"] == []

    # running, the computer away: ended at once, the run refused when it comes back
    away = _say(lab, "Once more.")["turn"]["id"]
    _claim(pc, away)
    registry.reset()
    assert _ok(lab.maker.post(f"/api/local-agents/chat/turns/{away}/stop")) == {"stopped": True}
    assert _reply(lab)["state"] == "stopped"
    assert _write(pc, away, 1, text="Late", status=409)["detail"] == "The turn is stopped now."
    assert _reply(lab)["text"] == ""


def test_a_second_stop_ends_a_running_turn_at_once(lab, pc):
    """A turn the computer no longer runs (its claim's answer lost, its
    end refused) while it stays online: the first Stop sets the stop, a
    second ends the turn here, and the page's chat is free again."""
    turn = _say(lab)["turn"]["id"]
    _claim(pc, turn)
    assert _ok(lab.maker.post(f"/api/local-agents/chat/turns/{turn}/stop")) == {"stopped": True}
    assert _reply(lab)["state"] == "running" and _row(lab, turn)["stop"] == 1
    assert _ok(lab.maker.post(f"/api/local-agents/chat/turns/{turn}/stop")) == {"stopped": True}
    assert _reply(lab)["state"] == "stopped" and _row(lab, turn)["state"] == "stopped"
    assert _chats(pc)["stops"] == []
    assert _write(pc, turn, 1, text="Late", status=409)["detail"] == "The turn is stopped now."
    assert _say(lab, "Next.")["turn"]["state"] == "queued"


def test_a_queued_turn_is_stoppable_once_its_seat_is_gone(lab, pc):
    """The computer's token expired: the page's chat no longer answers,
    but its queued turn still stops, and its Background tasks row still
    names the computer."""
    turn = _say(lab)["turn"]["id"]
    with connect_users_db() as conn:
        conn.execute("UPDATE integration_tokens SET expires_at = 1 WHERE id = ?", (pc.id,))
    assert lab.maker.get("/api/local-agents/chat", params={"page": lab.main}).status_code == 404
    row = next(j for j in _ok(lab.maker.get("/api/jobs"))["jobs"] if j["kind"] == "computer-turn")
    assert (row["state"], row["params"]["computer"]) == ("queued", "TIM-PC")
    assert lab.editor.post(f"/api/local-agents/chat/turns/{turn}/stop").status_code == 404
    assert _ok(lab.maker.post(f"/api/local-agents/chat/turns/{turn}/stop")) == {"stopped": True}
    assert _reply_of(lab, turn)["state"] == "stopped"
    assert [j for j in _ok(lab.maker.get("/api/jobs"))["jobs"] if j["kind"] == "computer-turn"] == []


def test_a_queued_turn_of_a_page_deleted_still_stops(lab, pc):
    turn = _say(lab)["turn"]["id"]
    with connect_pages_db(lab.ws) as conn:
        parent = conn.execute("SELECT parent_id FROM unified_blocks WHERE id = ?", (lab.main,)).fetchone()[0]
        conn.execute("UPDATE unified_blocks SET parent_id = 'trash' WHERE id = ?", (lab.main,))
        conn.commit()
    try:
        assert _chats(pc)["turns"] == []
        assert _ok(lab.maker.post(f"/api/local-agents/chat/turns/{turn}/stop")) == {"stopped": True}
        assert _reply_of(lab, turn)["state"] == "stopped"
    finally:
        with connect_pages_db(lab.ws) as conn:
            conn.execute("UPDATE unified_blocks SET parent_id = ? WHERE id = ?", (parent, lab.main))
            conn.commit()
    assert _chats(pc)["turns"] == []                                             # restored: it never runs


def test_a_queued_turn_fails_when_its_agent_is_turned_off(lab, pc):
    """Codex turned off for the folder (the announce names the folder
    without it), or Stop showing (the folder not named): the turns queued
    there fail, so turning Codex on again later runs none of them."""
    turn = _say(lab)["turn"]["id"]
    _announce(pc.c, {"kind": "folder", "folder_id": lab.W})
    reply = _reply_of(lab, turn)
    assert (reply["state"], reply["error"]) == ("failed", "Codex was turned off for this folder.")
    _announce(pc.c, _kept(lab))
    assert _chats(pc)["turns"] == []
    second = _say(lab, "Again.")["turn"]["id"]
    _announce(pc.c)                                                               # Stop showing: no vault at all
    assert _reply_of(lab, second)["error"] == "Codex was turned off for this folder."
    # paused there: it waits
    _announce(pc.c, _kept(lab))
    third = _say(lab, "Once more.")["turn"]["id"]
    _announce(pc.c, _kept(lab, paused=True))
    assert _row(lab, third)["state"] == "queued"
    # another computer of the account still keeps it with Codex: it waits for that one
    lc = _laptop(lab)
    _announce(pc.c)
    assert _row(lab, third)["state"] == "queued"
    assert [t["id"] for t in _chats(lc)["turns"]] == [third]


def test_a_token_taken_back_fails_the_turns_queued_for_it(lab, pc):
    turn = _say(lab)["turn"]["id"]
    assert _ok(lab.maker.delete(f"/api/integrations/tokens/{pc.id}"))["revoked"] is True
    reply = _reply_of(lab, turn)
    assert (reply["state"], reply["error"]) == (
        "failed", "The computer's channel was taken back before Codex finished.")
    assert _row(lab, turn)["state"] == "failed"


def test_a_token_taken_back_fails_what_its_runs_hold(lab, pc):
    turn = _say(lab)["turn"]["id"]
    _claim(pc, turn)
    assert _ok(lab.maker.delete(f"/api/integrations/tokens/{pc.id}"))["revoked"] is True
    reply = _reply_of(lab, turn)
    assert (reply["state"], reply["error"]) == (
        "failed", "The computer's channel was taken back before Codex finished.")


def _reply_of(lab, turn_id) -> dict:
    """The turn's agent message, though the page's chat no longer answers."""
    with connect_pages_db(lab.ws) as conn:
        row = chats.run_of(conn, turn_id)
        conversation = chats.get(conn, row["conversation_id"])
    return next(m for m in conversation["messages"] if m.get("turn_id") == turn_id)


# --- what another computer's turn has not ----------------------------------------------------------------

def test_no_asks_no_source_lines_no_terminal(lab, pc):
    turn = _say(lab)["turn"]["id"]
    _claim(pc, turn)
    r = lab.maker.post(f"/api/local-agents/chat/turns/{turn}/asks/a1", json={"decision": "once"})
    assert r.status_code == 409 and r.json()["detail"] == "Asking is for a work folder of this computer's server."
    assert lab.editor.post(f"/api/local-agents/chat/turns/{turn}/asks/a1", json={"decision": "once"}).status_code == 404
    selection = {"text": "x", "page": 1, "box": [0.1, 0.1, 0.2, 0.2]}
    assert _ok(lab.maker.post("/api/local-agents/chat/sources", json={"page": lab.main,
                                                                        "selections": [selection, {}]})) == {
        "sources": [None, None]}
    r = lab.maker.get("/api/local-agents/chat/terminal", params={"page": lab.main})
    assert r.status_code == 404


# --- New chat, History ----------------------------------------------------------------------------------

def test_new_chat_history_and_delete(lab, pc):
    made = _say(lab)
    turn, first = made["turn"]["id"], made["conversation"]["id"]
    r = lab.maker.post("/api/local-agents/chat/new", json={"page": lab.main})
    assert r.status_code == 409 and r.json()["detail"] == "Codex is still working: stop it first."
    _claim(pc, turn)
    _write(pc, turn, 1, state="done", text="Done.")
    assert _ok(lab.maker.post("/api/local-agents/chat/new", json={"page": lab.main})) == {"conversation": None}
    assert _chat(lab)["conversation"] is None
    listed = _ok(lab.maker.get("/api/local-agents/chat/history", params={"page": lab.main}))["conversations"]
    assert [(c["id"], c["turns"]) for c in listed] == [(first, 1)]
    _say(lab, "A new one.")
    r = lab.maker.post(f"/api/local-agents/chat/history/{first}/open")
    assert r.status_code == 409                                               # the new one's turn waits
    assert _ok(lab.maker.delete(f"/api/local-agents/chat/history/{first}")) == {"deleted": True}
    with connect_pages_db(lab.ws) as conn:
        assert conn.execute("SELECT 1 FROM computer_chat_runs WHERE conversation_id = ?", (first,)).fetchone() is None


# --- Background tasks ---------------------------------------------------------------------------------

def test_background_tasks_list_the_turns_waiting_or_running_there(lab, pc):
    turn = _say(lab)["turn"]["id"]
    rows = [j for j in _ok(lab.maker.get("/api/jobs"))["jobs"] if j["kind"] == "computer-turn"]
    assert len(rows) == 1
    row = rows[0]
    assert (row["id"], row["workspace"], row["title"], row["state"], row["stoppable"], row["stopping"]) == (
        f"computer-{turn}", lab.ws, "Codex · main", "queued", True, False)
    assert row["params"] == {"page": lab.main, "turn": turn, "agent": "codex", "label": "Codex", "title": "main",
                             "computer": "TIM-PC"}  # the computer that keeps it, named
    assert [j for j in _ok(lab.editor.get("/api/jobs"))["jobs"] if j["kind"] == "computer-turn"] == []
    _claim(pc, turn)
    lab.maker.post(f"/api/local-agents/chat/turns/{turn}/stop").raise_for_status()
    row = next(j for j in _ok(lab.maker.get("/api/jobs"))["jobs"] if j["kind"] == "computer-turn")
    assert (row["state"], row["stoppable"], row["stopping"]) == ("running", False, True)
    _write(pc, turn, 1, state="stopped")
    assert [j for j in _ok(lab.maker.get("/api/jobs"))["jobs"] if j["kind"] == "computer-turn"] == []


def test_a_background_tasks_row_names_the_turns_own_agent(lab, pc):
    """The conversation began with Claude Code; the folder's agent is
    Codex now: the follow-up's row is Codex's."""
    _announce(pc.c, _kept(lab, agent="claude"), assistants=CLAUDE)
    first = _say(lab)
    _claim(pc, first["turn"]["id"], agent="claude")
    _write(pc, first["turn"]["id"], 1, state="done", text="Done.")
    _announce(pc.c, _kept(lab))
    turn = _say(lab, "Now the abstract.")["turn"]["id"]
    assert _chat(lab)["conversation"]["agent"] == "claude"
    row = next(j for j in _ok(lab.maker.get("/api/jobs"))["jobs"] if j["kind"] == "computer-turn")
    assert (row["params"]["turn"], row["title"], row["params"]["agent"], row["params"]["label"]) == (
        turn, "Codex · main", "codex", "Codex")
