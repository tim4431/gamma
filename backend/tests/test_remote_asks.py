"""Asking on this computer (step 5b of docs/research/agent-collab.md): a
chat turn of a page of another server's work folder (test_remote_chat.py's
loop) asks to run a command, and the ask is held here, listed and answered
by the desktop app alone (``GET`` and ``POST /api/local-agents/asks``,
runner.py ``computer_asks``, ``answer_computer``), never written there:
that server's message says ``waiting``.

One process plays both, as test_remote_chat.py does (its ``lab`` and
``nas``), the NAS reached at http://testserver, read here as reached
securely (``remote_servers.plain`` "") but where a test says otherwise. The
shell is a thread of its own (``shell``) holding ``GET
/api/local-agents/asks`` with the shell's word on the owner's session, as
the desktop app's follower does, so this computer hears it throughout.

The policy ``computer`` set with the shell's word for a work folder of
another server alone, over https alone, and ``older`` from a server whose
pages do not say a turn waits; an ask listed here with Gamma's reading of
it and none of it there, answered once or declined by the shell alone (the
chat's routes refusing, here and there), declined by Gamma when nobody
answers, when nobody is there to, over plain http, without a secret, too
long to read, to an address that does not read as one, past the caps;
Stop there declining what waits here; an answer never waiting on that
server; a lost write of ``waiting`` written again, a refused one not; a turn's wait moving its
deadline ten minutes at most; the read held until a change; no grant for a
remote turn; the maker alone; Background tasks there; ``waiting`` false at
every end; the card's file from this computer's rows alone; a vault moved
unchecked losing its policy."""

import io
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from conftest import fresh_client, login
from fake_agents import read_log, requests_in, script_agent
from gamma import config, folder_links, gamma_sync, remote_servers
from gamma.db import connect_pages_db
from gamma.local_agents import chats, home, runner, store
from gamma.routers import folder_links as folder_links_api
from test_remote_chat import (OWNER, PASSWORD, _chat, _reply, _run_there, _say, _sent, _stop, _turn_row,  # noqa: F401
                              lab, nas)  # the fixtures
from test_remote_sends import SECRET, SHELL, _made, _until

CURL = "curl -L https://example.org/refs.bib -o refs.bib"
ASK = {"command": CURL, "reason": "Fetch the bibliography the paper cites.", "host": "example.org"}


@pytest.fixture
def shell(lab, nas, monkeypatch):
    """The desktop app's follower: the owner's session with the shell's
    word, holding ``GET /api/local-agents/asks`` in a loop (``last`` its
    last answer), the NAS read as reached securely."""
    monkeypatch.setattr(remote_servers, "plain", lambda url: "")
    c = login(OWNER, PASSWORD)
    c.headers.update(SHELL)
    state = SimpleNamespace(client=c, stop=threading.Event(), last=None)

    def follow():
        version = -1
        while not state.stop.is_set():
            r = c.get("/api/local-agents/asks", params={"after": version, "wait": 0.5})
            if r.status_code != 200:
                time.sleep(0.1)
                continue
            state.last = r.json()
            version = state.last["version"]

    thread = threading.Thread(target=follow, name="test-shell", daemon=True)
    thread.start()
    _until(lambda: state.last is not None)
    yield state
    state.stop.set()
    thread.join(10)


def _asking(lab, nas, name: str, monkeypatch, *ask, **script) -> SimpleNamespace:
    """The paper as a work folder of the NAS whose agent may ask on this
    computer, the stand-in Codex asking ``ask``; this computer announced."""
    script_agent(monkeypatch, nas.log, ask=list(ask or [ASK]), **script)
    f = _made(lab, nas, name)
    f.turn.once()
    r = lab.owner.patch(f"/api/folder-links/{f.id}", json={"agent_runs": "computer"}, headers=SHELL)
    assert r.status_code == 200, r.text
    assert (r.json()["agent"]["runs"], r.json()["older"]) == ("computer", False)
    return f


def _started(lab, f, text="Fetch the bibliography.") -> str:
    """A message on the NAS page, run here → the turn's id."""
    turn_id = _say(lab.owner, f.page, text)["turn"]["id"]
    f.turn.once()
    return turn_id


def _card(shell, seen=()) -> dict:
    """The next ask the follower lists, one not in ``seen``."""
    return _until(lambda: next((a for a in (shell.last or {}).get("asks") or [] if a["id"] not in seen), None))


def _post(c, ask_id, decision, status=200) -> dict:
    r = c.post(f"/api/local-agents/asks/{ask_id}", json={"decision": decision})
    assert r.status_code == status, r.text
    return r.json()


def _answers(log) -> list[dict]:
    return [entry["answer"] for entry in read_log(log) if "answer" in entry]


def _steers(log) -> list[str]:
    return [m["params"]["input"][0]["text"] for m in requests_in(log) if m.get("method") == "turn/steer"]


# --- the policy --------------------------------------------------------------------------------------

def test_ask_on_this_computer_is_set_with_the_shells_word_for_another_servers_work_folder_alone(lab, nas,
                                                                                               monkeypatch):
    f = _made(lab, nas, "RA policy")
    path = f"/api/folder-links/{f.id}"
    r = lab.owner.patch(path, json={"agent_runs": "computer"}, headers=SHELL)  # http://testserver: plain http
    assert (r.status_code, r.json()["detail"]) == (
        409, "testserver is reached over plain http: Ask on this computer needs https.")
    monkeypatch.setattr(remote_servers, "plain", lambda url: "")
    r = lab.owner.patch(path, json={"agent_runs": "computer"})
    assert (r.status_code, r.json()["detail"]) == (403, "Only the desktop app may let an agent ask on this computer.")
    monkeypatch.setenv("GAMMA_SHELL_SECRET", "")                           # no secret: refused, never unchecked
    assert lab.owner.patch(path, json={"agent_runs": "computer"}, headers=SHELL).status_code == 403
    monkeypatch.setenv("GAMMA_SHELL_SECRET", SECRET)
    r = lab.owner.patch(path, json={"agent_runs": "ask"}, headers=SHELL)
    assert (r.status_code, r.json()["detail"]) == (409, "Asking in the chat is for a work folder of this computer's "
                                                        "server: for one of another server, choose Ask on this "
                                                        "computer.")
    r = lab.owner.patch(path, json={"agent_runs": "always"}, headers=SHELL)
    assert (r.status_code, r.json()["detail"]) == (400, "What an agent may run is never, ask or computer.")
    on = lab.owner.patch(path, json={"agent_runs": "computer"}, headers=SHELL)
    assert on.status_code == 200 and (on.json()["agent"]["runs"], on.json()["older"]) == ("computer", False)
    listed = next(link for link in lab.owner.get("/api/folder-links").json()["links"] if link["id"] == f.id)
    assert (listed["agent"]["runs"], listed["plain"]) == ("computer", "")
    assert store.runs_of(f.id) == "computer"
    # a local work folder: here the agent asks in the chat
    local = {**folder_links.get_link(f.id), "remote_url": ""}
    with pytest.raises(folder_links_api.HTTPException) as refused:
        folder_links_api._runs_allowed(None, local, "computer", "codex")
    assert (refused.value.status_code, refused.value.detail) == (
        409, "Ask on this computer is for a work folder of another server: here the agent asks in the chat.")
    # a NAS whose pages do not say a turn waits: older, the policy set all the same
    opened = gamma_sync.default_open

    def older(method, path_, headers, body=None):
        status, answer_headers, data = opened(method, path_, headers, body)
        if path_ == "/api/sync/whoami":
            who = json.loads(data.read())
            who.pop("computer_asks", None)
            data = io.BytesIO(json.dumps(who).encode())
        return status, answer_headers, data

    monkeypatch.setattr(gamma_sync, "default_open", older)
    again = lab.owner.patch(path, json={"agent_runs": "computer"}, headers=SHELL)
    assert again.status_code == 200 and again.json()["older"] is True
    nas.down.append(True)                                                  # not reached: no older, still set
    assert lab.owner.patch(path, json={"agent_runs": "computer"}, headers=SHELL).json()["older"] is False
    nas.down.clear()
    assert lab.owner.patch(path, json={"agent_runs": "never"}).json()["agent"]["runs"] == "never"   # the maker's alone
    store.set_runs(f.id, "computer", by=lab.user)
    assert lab.owner.patch(path, json={"agent": ""}).status_code == 200    # the agent off forgets it
    assert store.runs_of(f.id) == "never"


def test_a_vault_moved_unchecked_no_longer_asks_on_this_computer(lab, nas):
    f = _made(lab, nas, "RA moved", shown=False, agent="")
    store.set_runs(f.id, "computer", by=lab.user)
    remote_servers._unasked([f.id])
    assert store.runs_of(f.id) == "never"
    store.set_runs(f.id, "ask", by=lab.user)                               # another policy: not this move's
    remote_servers._unasked([f.id])
    assert store.runs_of(f.id) == "ask"


# --- the loop ------------------------------------------------------------------------------------------

def test_a_turn_there_asks_and_only_this_computer_sees_and_answers_it(lab, nas, shell, monkeypatch):
    f = _asking(lab, nas, "RA loop", monkeypatch)
    turn_id = _started(lab, f)
    card = _card(shell)
    # what this computer lists: Gamma's reading of the ask, nothing of the NAS's but the account
    assert {k: card[k] for k in ("turn_id", "link_id", "agent", "label", "kind", "command", "host", "reason")} == {
        "turn_id": turn_id, "link_id": f.id, "agent": "codex", "label": "Codex", "kind": "command",
        "command": CURL, "host": "example.org", "reason": "Fetch the bibliography the paper cites."}
    assert (card["dir"], card["folder"], card["file"]) == (str(f.root), str(f.root), "main.pdf")
    assert card["server"] == {"origin": "http://testserver", "account": OWNER}
    assert card["at"] < card["expires_at"] and "marked" not in card and "lines" not in card
    # what the NAS learns: that it waits, and the step; never the ask
    reply = _until(lambda: (r := _reply(lab, f.page)).get("waiting") and r)
    assert (reply["state"], reply["asks"]) == ("running", [])
    [step] = [s for s in reply["steps"] if s["status"] == "asked"]
    assert step["program"] == "curl" and card["id"] not in step["id"]
    there = json.dumps(_chat(lab.owner, f.page))
    assert "example.org" not in there and "refs.bib" not in there and str(f.root).replace("\\", "\\\\") not in there
    # only the shell answers: the NAS's route, this server's own chat route and the rule, a session alone
    r = lab.owner.post(f"/api/local-agents/chat/turns/{turn_id}/asks/{card['id']}", json={"decision": "once"})
    assert (r.status_code, r.json()["detail"]) == (409, "This is answered on the computer that runs the agent.")
    with pytest.raises(chats.ChatError) as refused:
        runner.answer_chat(f.id, turn_id, card["id"], "once")
    assert (refused.value.status, refused.value.detail) == (409, runner.FAR_ASKS)
    session = login(OWNER, PASSWORD)
    assert session.get("/api/local-agents/asks").status_code == 403
    assert _post(session, card["id"], "once", 403)["detail"] == "Only the desktop app answers on this computer."
    assert _post(shell.client, card["id"], "chat", 400)["detail"] == (
        "On this computer an ask is allowed once or declined.")
    assert _post(shell.client, "noSuchAsk", "once", 404)["detail"] == "No question on this computer has that id."
    assert [a["id"] for a in shell.client.get("/api/local-agents/asks").json()["asks"]] == [card["id"]]
    # allowed once: the agent has it, the command runs, the NAS hears it
    answered = _post(shell.client, card["id"], "once")
    assert answered["answered"] is True and answered["version"] > 0
    assert runner.wait_idle(60)
    reply = _reply(lab, f.page)
    assert (reply["state"], reply["text"], reply["waiting"]) == ("done", "Ran it.", False)
    assert [s["status"] for s in reply["steps"] if s["id"] == step["id"]] == ["completed"]
    assert [a["result"] for a in _answers(nas.log)] == [{"decision": "accept"}]
    assert _sent(nas.log, "thread/start")[-1]["approvalPolicy"] == "on-request"
    assert _post(shell.client, card["id"], "once", 404)["detail"] == "No question on this computer has that id."
    assert _turn_row(turn_id)["state"] == "done"


def test_a_decline_and_an_unanswered_ask_never_allow(lab, nas, shell, monkeypatch):
    f = _asking(lab, nas, "RA decline", monkeypatch)
    _started(lab, f)
    _post(shell.client, _card(shell)["id"], "deny")
    assert runner.wait_idle(60)
    assert (_reply(lab, f.page)["text"], [a["result"] for a in _answers(nas.log)]) == (
        "Did not run it.", [{"decision": "decline"}])
    assert _steers(nas.log) == []                                          # the person's decline carries no note here

    monkeypatch.setattr(runner, "ASK_TIMEOUT_S", 0.5)                      # nobody answers: declined, never allowed
    _started(lab, f, "Again.")
    assert runner.wait_idle(60)
    reply = _reply(lab, f.page)
    assert (reply["state"], reply["text"], reply["waiting"]) == ("done", "Did not run it.", False)
    assert _steers(nas.log) == ["Gamma declined that: No answer within 10 minutes."]


def test_stop_there_declines_what_waits_here(lab, nas, shell, monkeypatch):
    f = _asking(lab, nas, "RA stop", monkeypatch)
    turn_id = _started(lab, f)
    card = _card(shell)
    assert _stop(lab.owner, turn_id) == {"stopped": True}                  # its stop set there
    f.turn.once()                                                          # heard here: the turn stops
    assert runner.wait_idle(30)
    _until(lambda: shell.last["asks"] == [])
    reply = _reply(lab, f.page)
    assert (reply["state"], reply["waiting"]) == ("stopped", False)
    assert [a["result"] for a in _answers(nas.log)] == [{"decision": "cancel"}]
    _post(shell.client, card["id"], "once", 404)                           # too late: nothing allowed


def test_a_turn_claimed_before_the_app_reads_still_asks(lab, nas, monkeypatch):
    assert runner._shell_seen == float("-inf")                             # never read yet: not "just after a boot"
    assert not runner.shell_listening()
    monkeypatch.setattr(remote_servers, "plain", lambda url: "")
    monkeypatch.setattr(runner, "SHELL_GRACE_S", 5)
    f = _asking(lab, nas, "RA grace", monkeypatch)
    app = login(OWNER, PASSWORD)
    app.headers.update(SHELL)
    _started(lab, f)                                                       # claimed: it starts able to ask
    _until(lambda: _sent(nas.log, "thread/start") and _reply(lab, f.page)["state"] == "running")
    assert _sent(nas.log, "thread/start")[-1]["approvalPolicy"] == "on-request"
    time.sleep(0.5)                                                        # the ask waits for the app's first read
    card = _until(lambda: app.get("/api/local-agents/asks").json()["asks"])[0]   # it comes within the grace: held
    _post(app, card["id"], "once")
    assert runner.wait_idle(60) and _reply(lab, f.page)["text"] == "Ran it."

    runner.reset()                                                         # nobody reads: declined unseen
    monkeypatch.setattr(runner, "SHELL_GRACE_S", 0.3)
    _started(lab, f, "Again.")
    assert runner.wait_idle(60)
    assert (_reply(lab, f.page)["text"], _steers(nas.log)) == (
        "Did not run it.", ["Gamma declined that: Nobody can answer on this computer now."])


def test_an_ask_over_plain_http_or_without_a_secret_is_declined_unseen(lab, nas, shell, monkeypatch):
    f = _asking(lab, nas, "RA plain", monkeypatch, ASK, ASK)
    secure = {"now": True}
    monkeypatch.setattr(remote_servers, "plain", lambda url: "" if secure["now"] else "lan")
    turn_id = _started(lab, f)
    card = _card(shell)
    secure["now"] = False                                                  # its address no longer https: the next ask
    _post(shell.client, card["id"], "once")
    assert runner.wait_idle(60)
    assert _steers(nas.log) == [f"Gamma declined that: {runner.NOT_SECURE}"]
    assert [a["result"] for a in _answers(nas.log)] == [{"decision": "accept"}, {"decision": "decline"}]
    assert _turn_row(turn_id)["state"] == "done"

    secure["now"] = True
    monkeypatch.setenv("GAMMA_SHELL_SECRET", "")                           # no secret: no window to ask in
    _started(lab, f, "Again.")
    assert runner.wait_idle(60)
    assert _sent(nas.log, "thread/resume")[-1]["approvalPolicy"] == "never"   # it starts without asking at all
    assert _reply(lab, f.page)["state"] == "done"


def test_a_remote_ask_too_long_or_with_an_odd_host_is_declined_unseen(lab, nas, shell, monkeypatch):
    long_one, lines = "echo " + "x" * 9000, "\n".join(f"echo {n}" for n in range(201))
    asks = [{"command": long_one}, {"command": lines}, {"command": "curl evil", "host": "evil example"},
            {"path": "../outside.tex", "diff": "+x\n", "repeat": 12_000}, {"path": "../out0.tex", "diff": "+x\n"},
            {"command": "make", "reason": "‮gnp.exe"}]
    f = _asking(lab, nas, "RA long", monkeypatch, *asks)
    _started(lab, f)
    outside = _card(shell)                                                 # a small file change: shown
    assert outside["kind"] == "files" and outside["paths"] == [str((f.root.parent / "out0.tex").resolve())]
    _post(shell.client, outside["id"], "deny")
    marked = _card(shell, {outside["id"]})                                # a mark is drawn, not refused
    assert (marked["reason"], marked["marked"]) == ("⟨U+202E⟩gnp.exe", 1)
    _post(shell.client, marked["id"], "deny")
    assert runner.wait_idle(60)
    assert _steers(nas.log) == [f"Gamma declined that: {words}" for words in (
        runner.TOO_LONG, runner.TOO_LONG, runner.HOST_UNREAD, runner.TOO_LONG)]


def test_a_turn_holds_at_most_twenty_asks_and_three_declines_in_a_row(lab, nas, shell, monkeypatch):
    monkeypatch.setattr(runner, "MAX_ASKS", 3)
    f = _asking(lab, nas, "RA caps", monkeypatch, *[{"command": f"make t{n}"} for n in range(5)])
    _started(lab, f)
    seen = set()
    for decision in ("deny", "once", "deny"):                              # an allow sets the count back
        card = _card(shell, seen)
        seen.add(card["id"])
        _post(shell.client, card["id"], decision)
    assert runner.wait_idle(60)
    assert _steers(nas.log) == [f"Gamma declined that: {runner.ASKED_TOO_OFTEN}"] * 2
    monkeypatch.setattr(runner, "MAX_ASKS", 20)
    _started(lab, f, "Again.")
    seen = set()
    for _ in range(3):
        card = _card(shell, seen)
        seen.add(card["id"])
        _post(shell.client, card["id"], "deny")
    assert runner.wait_idle(60)
    assert _steers(nas.log)[-2:] == [f"Gamma declined that: {runner.DECLINED_OFTEN}"] * 2


def test_answering_never_waits_on_the_nas(lab, nas, shell, monkeypatch):
    f = _asking(lab, nas, "RA hang", monkeypatch)
    _started(lab, f)
    card = _card(shell)
    held, opened = threading.Event(), gamma_sync.default_open

    def hanging(method, path, headers, body=None):
        if path.endswith("/write"):
            held.wait(20)                                                  # that server stalls
        return opened(method, path, headers, body)

    monkeypatch.setattr(gamma_sync, "default_open", hanging)
    time.sleep(0.5)                                                        # a flush stalls there first
    began = time.monotonic()
    assert _post(shell.client, card["id"], "once")["answered"] is True
    assert time.monotonic() - began < 2
    _until(lambda: [a["result"] for a in _answers(nas.log)] == [{"decision": "accept"}])   # the agent has it
    held.set()
    assert runner.wait_idle(60) and _reply(lab, f.page)["text"] == "Ran it."


def test_a_lost_write_of_waiting_is_written_again(lab, nas, shell, monkeypatch):
    f = _asking(lab, nas, "RA lost", monkeypatch)
    opened, lost = gamma_sync.default_open, []

    def losing(method, path, headers, body=None):
        content = body if body is None or isinstance(body, bytes) else b"".join(body)
        if path.endswith("/write") and b'"waiting": true' in (content or b"") and not lost:
            lost.append(path)
            raise gamma_sync.Unreachable("cannot reach http://testserver: refused")
        return opened(method, path, headers, content)

    monkeypatch.setattr(gamma_sync, "default_open", losing)
    _started(lab, f)
    card = _card(shell)
    _until(lambda: lost)
    _until(lambda: _reply(lab, f.page).get("waiting"), timeout=home.WRITE_EVERY_S + 2)
    _post(shell.client, card["id"], "deny")
    assert runner.wait_idle(60)


def test_a_remote_turn_waits_ten_minutes_at_most_past_its_deadline(lab, nas, shell, monkeypatch):
    monkeypatch.setenv("GAMMA_AGENT_RUN_TIMEOUT", "1")
    monkeypatch.setattr(runner, "FAR_WAIT_S", 1)
    f = _asking(lab, nas, "RA deadline", monkeypatch)
    _started(lab, f)
    _card(shell)
    assert runner.wait_idle(30)
    reply = _reply(lab, f.page)
    assert (reply["state"], reply["error"], reply["waiting"]) == (
        "failed", "Codex took longer than 1 second and was stopped.", False)
    _until(lambda: shell.last["asks"] == [])


# --- the rest -----------------------------------------------------------------------------------------

def test_the_asks_read_waits_for_a_change(lab, nas, shell):
    version = runner.asks_version()
    began = time.monotonic()
    assert runner.wait_asks(version, 0.3) == version and time.monotonic() - began >= 0.25
    got = []
    reader = threading.Thread(target=lambda: got.append(runner.wait_asks(version, 10)))
    reader.start()
    time.sleep(0.2)
    runner._asks_changed()
    reader.join(5)
    assert got == [version + 1]
    assert runner.wait_asks(version, 10) == version + 1                    # a change since: at once
    r = shell.client.get("/api/local-agents/asks", params={"after": version + 1, "wait": 0.3})
    assert r.json() == {"version": version + 1, "asks": []}


def test_a_remote_turn_never_answers_from_a_grant(lab, nas, shell, monkeypatch):
    f = _asking(lab, nas, "RA grant", monkeypatch)
    turn_id = _started(lab, f)
    card = _card(shell)
    conversation = _run_there(lab, turn_id)["conversation_id"]
    _post(shell.client, card["id"], "deny")
    assert runner.wait_idle(60)
    store.grant(conversation, CURL, link_id=f.id, host="example.org")      # a grant this computer would never keep
    _started(lab, f, "Again.")
    _post(shell.client, _card(shell, {card["id"]})["id"], "deny")          # asked all the same
    assert runner.wait_idle(60)


def test_only_the_vaults_maker_reads_and_answers(lab, nas, shell, monkeypatch, anon):
    f = _asking(lab, nas, "RA maker", monkeypatch)
    _started(lab, f)
    card = _card(shell)
    editor = fresh_client(headers=SHELL)
    editor.cookies = lab.editor.cookies
    assert editor.get("/api/local-agents/asks").json()["asks"] == []
    assert _post(editor, card["id"], "once", 404)["detail"] == "No question on this computer has that id."
    anon.headers.update(SHELL)
    assert anon.get("/api/local-agents/asks").status_code == 401
    monkeypatch.setenv("GAMMA_LOCAL_AGENTS", "0")                          # a server that runs no agents
    assert shell.client.get("/api/local-agents/asks").status_code == 404
    monkeypatch.setenv("GAMMA_LOCAL_AGENTS", "1")
    _post(shell.client, card["id"], "deny")
    assert runner.wait_idle(60)


def test_background_tasks_there_say_the_turn_waits_on_that_computer(lab, nas, shell, monkeypatch):
    f = _asking(lab, nas, "RA tasks", monkeypatch)
    turn_id = _started(lab, f)
    card = _card(shell)
    _until(lambda: _reply(lab, f.page).get("waiting"))
    row = next(j for j in lab.owner.get("/api/jobs").json()["jobs"] if j["id"] == f"computer-{turn_id}")
    assert (row["state"], row["progress"]) == ("running", {"phase": "asking"}) and row["params"]["computer"]
    _post(shell.client, card["id"], "deny")
    assert runner.wait_idle(60)


def test_an_end_or_a_failure_left_open_leaves_waiting_false(lab, nas, monkeypatch):
    f = _made(lab, nas, "RA ends")
    f.turn.once()
    turn_id = _say(lab.owner, f.page)["turn"]["id"]
    computer = fresh_client(headers={"Authorization": f"Bearer {f.token['token']}"})
    run = home.chat_run_id(turn_id)
    claim = computer.post(f"/api/computers/chats/{turn_id}/claim", json={"run": run, "agent": "codex"})
    assert claim.status_code == 200
    write = f"/api/computers/chats/{turn_id}/write"
    assert computer.post(write, json={"run": run, "seq": 1, "fields": {"waiting": True}}).status_code == 200
    assert _reply(lab, f.page)["waiting"] is True
    assert computer.post(write, json={"run": run, "seq": 2, "fields": {"waiting": "yes"}}).status_code == 400
    with connect_pages_db(lab.ws) as conn:                                 # a failure that ends it there
        chats._end_run(conn, chats.run_of(conn, turn_id), "failed", {"error": "x"})
    assert _reply(lab, f.page)["waiting"] is False
    turn_id = _say(lab.owner, f.page, "Again.")["turn"]["id"]
    run = home.chat_run_id(turn_id)
    computer.post(f"/api/computers/chats/{turn_id}/claim", json={"run": run, "agent": "codex"})
    computer.post(f"/api/computers/chats/{turn_id}/write", json={"run": run, "seq": 1, "fields": {"waiting": True}})
    assert home.HttpChatHome(f.id, turn_id).fail_left_open({"id": turn_id, "agent": "codex"})
    assert (_reply(lab, f.page)["state"], _reply(lab, f.page)["waiting"]) == ("failed", False)


def test_the_card_names_a_file_only_from_the_vaults_rows(lab, nas):
    f = _made(lab, nas, "RA file", shown=False, agent="")
    link = {"id": f.id}
    assert home._vault_file(SimpleNamespace(page_id=f.page), link) == "main.pdf"
    assert home._vault_file(SimpleNamespace(page_id="aPageThere"), link) == ""   # never the NAS's path or title


def test_the_shells_secret_is_off_the_environment_once_read(monkeypatch):
    monkeypatch.setenv("GAMMA_SHELL_SECRET", "rsa-read-once")
    assert config.shell_secret() == "rsa-read-once"
    assert "GAMMA_SHELL_SECRET" not in os.environ and config.shell_secret() == "rsa-read-once"


# The child reads what another process of the user reads of it: /proc on
# Linux, ``ps eww`` on macOS. The value is never in its argv, which ps shows.
_READ_OWN_ENVIRONMENT = """
import os, subprocess, sys
from gamma import config
value = config._take_env("GAMMA_SHELL_SECRET")
assert value.startswith("rsa-"), value
if sys.platform == "darwin":
    seen = subprocess.run(["ps", "eww", "-p", str(os.getpid())], capture_output=True, check=True).stdout
else:
    seen = open("/proc/self/environ", "rb").read()
    assert b"GAMMA_SHELL_SECRET=" in seen, seen                # the block that held it: its value zeroed
assert value.encode() not in seen, seen
"""


@pytest.mark.skipif(not (sys.platform.startswith("linux") or sys.platform == "darwin"),
                    reason="Windows keeps no copy of the environment a process began with")
def test_the_shells_secret_is_zeroed_in_the_environment_the_server_began_with(tmp_path):
    env = {**os.environ, "GAMMA_SHELL_SECRET": "rsa-read-once", "GAMMA_DATA_DIR": str(tmp_path)}
    subprocess.run([sys.executable, "-c", _READ_OWN_ENVIRONMENT], env=env, check=True,
                   cwd=Path(__file__).resolve().parent.parent)


def test_a_write_refused_there_is_not_tried_again_one_lost_is(lab, nas, monkeypatch):
    f = _made(lab, nas, "RA refused", shown=False, agent="")
    turn = home.HttpChatHome(f.id, "aTurnThere")
    failing = [gamma_sync.Unreachable("cannot reach http://testserver: refused"), home.Refused(401, "x")]

    def answering(*_, **__):
        raise failing.pop(0)

    monkeypatch.setattr(turn, "_json", answering)
    assert turn.write(None, {"text": "a"}) is True and turn.missed is True       # lost: the flush writes it again
    assert turn.write(None, {"text": "a"}) is True and turn.missed is False      # refused: not once a second
    assert not turn.lost
