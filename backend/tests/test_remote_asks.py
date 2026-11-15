"""Asking on this computer (step 5b of docs/research/agent-collab.md): a
chat turn of a page of another server's work folder (test_remote_chat.py's
loop) asks to run a command, and the ask is held here, listed and answered
by the desktop app (``GET`` and ``POST /api/local-agents/asks``, runner.py
``computer_asks``, ``answer_computer``), and, where the link lets that
server answer (step 4.3 of docs/research/agents-as-participants-plan.md),
relayed there too; else never written there: that server's message says
``waiting``.

One process plays both, as test_remote_chat.py does (its ``lab`` and
``nas``), the NAS reached at http://testserver, read here as reached
securely (``remote_servers.plain`` "") but where a test says otherwise. The
shell is a thread of its own (``shell``) holding ``GET
/api/local-agents/asks`` with the shell's word on the owner's session, as
the desktop app's follower does, so this computer hears it throughout.

A turn there may ask while its agent may (``runs`` ``ask``, the
computer's), over https alone and with the shell's secret; an ask listed
here with Gamma's reading of
it and none of it there, answered once or declined by the shell alone (the
chat's routes refusing, here and there), declined by Gamma when nobody
answers, when nobody is there to, over plain http, without a secret, too
long to read, to an address that does not read as one, past the caps;
Stop there declining what waits here; an answer never waiting on that
server; a lost write of ``waiting`` written again, a refused one not; a turn's wait moving its
deadline ten minutes at most; the read held until a change; no grant for a
remote turn; the maker alone; Background tasks there; ``waiting`` false at
every end; the card's file from this computer's rows alone. Relayed: the
ask in that server's message, allowed once there by the turn's person or
the agent's owner (another account refused, Allow in this chat refused),
the answer kept with the run, listed to this computer and taken by the
waiting turn, the bar's card closing; the first answer the one, here or
there; an answer there waiting out a channel down; a Stop from either side
ending the ask, an answer after it refused; the link off relaying nothing,
a co-author's turn then asking nothing; a co-author's ask relayed, once at
most, answered by them or the owner, no grant kept."""

import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from conftest import account_of, fresh_client, login
from fake_agents import read_log, requests_in, script_agent
from gamma import config, folder_links, gamma_sync, remote_servers
from gamma.computers import channel
from gamma.db import connect_pages_db, connect_vaults_db
from gamma.local_agents import chats, home, runner, store
from test_remote_chat import (EDITOR, OWNER, PASSWORD, SECRET, SHELL, _chat, _made, _reply,  # noqa: F401
                              _run_there, _say, _sent, _stop, _turn_row, _until, lab, nas)  # the fixtures

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
    """The paper as a work folder of the NAS, its agent let ask (on this
    computer, for a turn there), the stand-in Codex asking ``ask``; this
    computer announced."""
    script_agent(monkeypatch, nas.log, ask=list(ask or [ASK]), **script)
    f = _made(lab, nas, name)
    f.turn.once()
    r = lab.owner.patch("/api/local-agents/codex", json={"runs": "ask"}, headers=SHELL)
    assert r.status_code == 200 and r.json()["runs"] == "ask", r.text
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

def test_a_turn_there_asks_while_its_agent_may_over_https_with_the_secret(lab, nas, monkeypatch):
    f = _made(lab, nas, "RA policy")
    link = folder_links.get_link(f.id)["remote_url"]
    assert runner.ask_refusal(link, True, "codex") == runner.RUNS_NEVER     # never until it is let ask
    assert runner.ask_refusal("", True, "codex") == runner.RUNS_NEVER       # its channel gone
    r = lab.owner.patch("/api/local-agents/codex", json={"runs": "ask"}, headers=SHELL)
    assert r.status_code == 200 and r.json()["runs"] == "ask", r.text
    assert runner.ask_refusal(link, True, "codex") == runner.NOT_SECURE     # http://testserver: plain http
    monkeypatch.setattr(remote_servers, "plain", lambda url: "")
    assert runner.ask_refusal(link, True, "codex") == ""
    assert runner.ask_refusal(link, True, "claude") == runner.RUNS_NEVER    # another agent's turn
    monkeypatch.setenv("GAMMA_SHELL_SECRET", "")
    assert runner.ask_refusal(link, True, "codex") == runner.NOBODY_HERE    # no window to ask in
    monkeypatch.setenv("GAMMA_SHELL_SECRET", SECRET)
    lab.owner.patch("/api/local-agents/codex", json={"runs": "never"}).raise_for_status()   # its owner's alone
    assert runner.ask_refusal(link, True, "codex") == runner.RUNS_NEVER


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
    # only the shell answers: the NAS's route (the link relays no asks), this server's own chat route and the
    # rule, a session alone
    r = lab.owner.post(f"/api/local-agents/chat/turns/{turn_id}/asks/{card['id']}", json={"decision": "once"})
    assert (r.status_code, r.json()["detail"]) == (409, "Codex no longer waits for that.")
    with pytest.raises(chats.ChatError) as refused:
        runner.answer_chat(f.turn.link_id, turn_id, card["id"], "once")
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


# --- relayed to that server (step 4.3 of docs/research/agents-as-participants-plan.md) -----------------

def _relaying(lab, f, on: bool = True) -> None:
    """The link lets the NAS answer what this computer's agents ask (on: the
    shell's word), announced there."""
    r = lab.owner.patch(f"/api/computer-links/{f.turn.link_id}", json={"asks": on}, headers=SHELL)
    assert r.status_code == 200 and r.json()["asks"] is on, r.text
    f.turn.once()


def _there(who, turn_id, ask_id, decision="once", status=200, **body) -> dict:
    """An answer on the NAS's chat route."""
    r = who.post(f"/api/local-agents/chat/turns/{turn_id}/asks/{ask_id}", json={"decision": decision, **body})
    assert r.status_code == status, r.text
    return r.json()


def _relayed(lab, page) -> dict:
    """The ask the NAS's message holds, once the computer wrote it there."""
    return _until(lambda: (_reply(lab, page).get("asks") or [None])[0])


def test_a_turn_there_relays_its_ask_where_the_link_lets_that_server_answer_and_takes_the_answer_given_there(
        lab, nas, shell, monkeypatch):
    f = _asking(lab, nas, "RA relayed", monkeypatch)
    _relaying(lab, f)
    assert next(c for c in lab.owner.get(f"/api/pages/{f.page}/computer").json()["computers"])["asks"] is True
    turn_id = _started(lab, f)
    card = _card(shell)                                                    # this computer's bar shows it still
    ask = _relayed(lab, f.page)
    # the chat's ask, as a turn of the NAS's own writes it: allowed once at most, its folder relative
    assert {k: ask[k] for k in ("id", "kind", "command", "host", "reason", "once")} == {
        "id": card["id"], "kind": "command", "command": CURL, "host": "example.org",
        "reason": "Fetch the bibliography the paper cites.", "once": True}
    reply = _reply(lab, f.page)
    assert (reply["state"], reply["waiting"]) == ("running", False)
    assert str(f.root).replace("\\", "\\\\") not in json.dumps(_chat(lab.owner, f.page))
    row = next(j for j in lab.owner.get("/api/jobs").json()["jobs"] if j["id"] == f"computer-{turn_id}")
    assert row["progress"] == {"phase": "asking"}                          # waiting for you, in the chat
    # there: never in this chat, another account refused, allowed once alone
    assert _there(lab.owner, turn_id, ask["id"], "chat", 400)["detail"] == (
        "A turn on another computer is allowed once at most.")
    assert _there(lab.editor, turn_id, ask["id"], status=404)["detail"] == "No turn has that id."
    assert _there(lab.owner, turn_id, ask["id"], "once") == {"answered": True}
    assert _reply(lab, f.page)["asks"] == []                               # its card closes there at once
    assert _there(lab.owner, turn_id, ask["id"], "deny", 409)["detail"] == "That was answered already."
    assert [a["id"] for a in _bearer_chats(f)["answers"] if a["id"] == turn_id] == [turn_id]
    # this computer takes it at its next turn of the channel: the agent goes on, the bar's card goes
    f.turn.once()
    assert runner.wait_idle(60)
    reply = _reply(lab, f.page)
    assert (reply["state"], reply["text"], reply["asks"], reply["waiting"]) == ("done", "Ran it.", [], False)
    assert [a["result"] for a in _answers(nas.log)] == [{"decision": "accept"}]
    _until(lambda: shell.last["asks"] == [])
    assert _post(shell.client, card["id"], "once", 404)["detail"] == "No question on this computer has that id."
    assert _run_there(lab, turn_id)["answers"] == "{}"


def _bearer_chats(f) -> dict:
    """What the NAS lists to this computer on its channel."""
    r = fresh_client(headers={"Authorization": f"Bearer {f.token['token']}"}).get("/api/computers/chats")
    assert r.status_code == 200, r.text
    return r.json()


def test_the_first_answer_is_the_one_here_or_there(lab, nas, shell, monkeypatch):
    f = _asking(lab, nas, "RA first", monkeypatch, ASK, ASK, ASK)
    _relaying(lab, f)
    turn_id = _started(lab, f)
    first = _card(shell)
    assert _relayed(lab, f.page)["id"] == first["id"]
    _there(lab.owner, turn_id, first["id"], "deny", note="Use the bibliography in the repository.")
    f.turn.once()                                                          # taken here: the bar's card goes
    second = _card(shell, {first["id"]})
    assert _post(shell.client, first["id"], "once", 409)["detail"] == "Codex no longer waits for that."
    assert _steers(nas.log) == ["The owner declined that: Use the bibliography in the repository."]
    # the second answered on this computer first: there it is no longer waited for
    _until(lambda: _relayed(lab, f.page)["id"] == second["id"])
    _post(shell.client, second["id"], "once")
    third = _card(shell, {first["id"], second["id"]})
    _until(lambda: _relayed(lab, f.page)["id"] == third["id"])
    assert _there(lab.owner, turn_id, second["id"], "deny", 409)["detail"] == "Codex no longer waits for that."
    _there(lab.owner, turn_id, third["id"], "once")
    f.turn.once()
    assert runner.wait_idle(60)
    assert [a["result"] for a in _answers(nas.log)] == [{"decision": "decline"}, {"decision": "accept"},
                                                         {"decision": "accept"}]
    assert _reply(lab, f.page)["text"] == "Ran it."


def test_an_answer_there_waits_for_a_channel_down_and_a_stop_from_either_side_ends_the_ask(lab, nas, monkeypatch):
    monkeypatch.setattr(remote_servers, "plain", lambda url: "")
    f = _asking(lab, nas, "RA down", monkeypatch)
    _relaying(lab, f)
    monkeypatch.setenv("GAMMA_SHELL_SECRET", "")                           # no window here: that server alone answers
    turn_id = _started(lab, f)
    ask = _relayed(lab, f.page)
    nas.down.append(True)                                                  # the channel drops while it waits
    _there(lab.owner, turn_id, ask["id"], "once")
    assert f.turn.once() == channel.RETRY_FIRST_S
    assert list(runner._active[(f.turn.link_id, turn_id)]["chat"].asks) == [ask["id"]]   # it waits with the turn here
    nas.down.clear()
    f.turn.once()                                                          # back: the answer is taken
    assert runner.wait_idle(60) and _reply(lab, f.page)["text"] == "Ran it."

    turn_id = _started(lab, f, "Again.")                                   # Stop there while it waits
    ask = _relayed(lab, f.page)
    assert _stop(lab.owner, turn_id) == {"stopped": True}
    f.turn.once()
    assert runner.wait_idle(30)
    reply = _reply(lab, f.page)
    assert (reply["state"], reply["asks"]) == ("stopped", [])
    assert [a["result"] for a in _answers(nas.log)][-1] == {"decision": "cancel"}
    assert _there(lab.owner, turn_id, ask["id"], status=409)["detail"] == "This turn has ended."

    turn_id = _started(lab, f, "Once more.")                               # stopped here: the agent turned off
    ask = _relayed(lab, f.page)
    store.set_agent("codex", False, by=lab.user)
    runner.stop_agent("codex")
    assert runner.wait_idle(30)
    assert (_reply(lab, f.page)["state"], _reply(lab, f.page)["asks"]) == ("failed", [])
    assert _there(lab.owner, turn_id, ask["id"], status=409)["detail"] == "This turn has ended."


def test_with_the_link_off_a_turn_there_relays_nothing_and_a_co_authors_asks_nothing(lab, nas, monkeypatch):
    monkeypatch.setattr(remote_servers, "plain", lambda url: "")
    f = _asking(lab, nas, "RA off", monkeypatch)
    named = {"server": nas.url, "workspace_id": lab.ws, "account": account_of(EDITOR)}
    assert lab.owner.post("/api/local-agents/codex/people", json=named, headers=SHELL).status_code == 200
    f.turn.once()
    _say(lab.editor, f.page, "Fetch it for me.")
    f.turn.once()
    assert runner.wait_idle(60)
    assert _sent(nas.log, "thread/start")[-1]["approvalPolicy"] == "never"   # a co-author's turn asks nothing
    assert next(c for c in lab.owner.get(f"/api/pages/{f.page}/computer").json()["computers"])["asks"] is False
    _relaying(lab, f, False)
    monkeypatch.setattr(runner, "SHELL_GRACE_S", 0.3)
    _started(lab, f)                                                        # the owner's: on this computer alone
    assert runner.wait_idle(60)
    reply = _reply(lab, f.page)
    assert (reply["text"], reply["asks"]) == ("Did not run it.", [])
    assert _steers(nas.log) == [f"Gamma declined that: {runner.NOBODY_HERE}"]


def test_a_co_authors_ask_there_is_relayed_allowed_once_and_answered_by_them_or_the_owner(lab, nas, shell, monkeypatch):
    f = _asking(lab, nas, "RA lent", monkeypatch, ASK, ASK)
    named = {"server": nas.url, "workspace_id": lab.ws, "account": account_of(EDITOR)}
    assert lab.owner.post("/api/local-agents/codex/people", json=named, headers=SHELL).status_code == 200
    _relaying(lab, f)
    turn_id = _say(lab.editor, f.page, "Fetch it for me.")["turn"]["id"]
    f.turn.once()
    card = _card(shell)                                                    # the owner's bar, for the editor
    assert card["for"] == EDITOR
    def asked(seen=()):   # the ask the editor's conversation there holds, one not in ``seen``
        return _until(lambda: next((a for a in [m for m in _chat(lab.editor, f.page)["conversation"]["messages"]
                                                if m["role"] == "agent"][-1].get("asks") or [] if a["id"] not in seen),
                                   None))

    first = asked()
    assert (first["id"], first["once"]) == (card["id"], True)
    assert _there(lab.editor, turn_id, first["id"], "chat", 400)["detail"] == runner.ONCE_ONLY
    _there(lab.editor, turn_id, first["id"], "once")                       # the person's own answer
    f.turn.once()
    second = asked({first["id"]})
    assert _card(shell, {card["id"]})["id"] == second["id"]
    _there(lab.owner, turn_id, second["id"], "deny")                       # and the agent's owner's
    f.turn.once()
    assert runner.wait_idle(60)
    assert [a["result"] for a in _answers(nas.log)] == [{"decision": "accept"}, {"decision": "decline"}]
    with connect_vaults_db() as conn:
        assert conn.execute("SELECT COUNT(*) FROM agent_dir_grants").fetchone() == (0,)   # no grant kept


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
    store.grant(conversation, CURL, agent="codex", dir=str(f.root), host="example.org")   # one it would never keep
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
    assert (row["state"], row["progress"]) == ("running", {"phase": "asking", "there": True})
    assert row["params"]["computer"]
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
    assert home.HttpChatHome(f.turn.link_id, turn_id).fail_left_open({"id": turn_id, "agent": "codex"})
    assert (_reply(lab, f.page)["state"], _reply(lab, f.page)["waiting"]) == ("failed", False)


def test_the_card_names_a_file_only_from_the_vaults_rows(lab, nas):
    f = _made(lab, nas, "RA file", shown=False, agent="")
    link = {"id": f.id}
    assert home._vault_file(SimpleNamespace(bucket=f.page), link) == "main.pdf"
    assert home._vault_file(SimpleNamespace(bucket="aPageThere"), link) == ""   # never the NAS's path or title


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
