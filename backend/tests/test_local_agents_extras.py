"""The rest of the chat's computer side (step 3c; docs/dev/local_agents.md
"The chat's computer side"), against the stand-ins (tests/fake_agents/)
through the real processes, on the sample paper as a work folder of this
computer (test_local_agents_runner's fixtures).

The probe lists the models each agent offers without using one (Codex's
``model/list``, its pages followed and the hidden left out; Claude Code's
``initialize``), in the listing and the strip; a turn asks for a model, an
effort and a speed the agent listed (Codex's thread and turn, Claude Code's
``--model``, ``--effort`` and ``fastMode``), any other refused; a chat turn's reply grows
as its deltas come and the whole message takes their place; the terminal
route gives the shell the conversation's session to resume, to the vault's
maker only, never while a turn works, never a session of another form,
agent or directory; the sources route maps the composer's selections to
their lines; Background tasks lists the account's turns while they are
queued or running, asking or not."""

import json
import os
import subprocess
import sys
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from conftest import account_of, fresh_client, login, make_user, token_for
from fake_agents import ARGV, read_log, requests_in, script_agent, turns_in
from gamma import agents, workspaces
from gamma.db import connect_pages_db
from gamma.local_agents import catalog, chats, claude, codex, events, runner, store
from gamma.local_agents import sessions as sessions_mod
from test_local_agents_chat import PLACES, _chat, _reply, _say, _stop, _turns_in
from test_local_agents_runner import _folder, _until, disk  # noqa: F401: the disk fixture
from test_vault_routes import _page_of

OWNER, EDITOR = "laex_owner", "laex_editor"
NO_SPEED = {"speeds": [], "speed": None}
CODEX_MODELS = [{"id": "gpt-fake", "label": "GPT Fake", "efforts": ["low", "medium", "high"], "effort": "medium",
                 "default": True, "speeds": ["standard", "fast"], "speed": "standard"},  # ultrafast: not offered
                {"id": "gpt-fake-mini", "label": "GPT Fake Mini", "efforts": ["low", "medium"], "effort": "low",
                 "default": False, **NO_SPEED}]
CLAUDE_MODELS = [{"id": "default", "label": "Default (recommended)", "efforts": ["low", "medium", "high"],
                  "effort": None, "default": True, "speeds": ["standard", "fast"], "speed": "standard"},
                 {"id": "sonnet", "label": "Sonnet", "efforts": ["low", "medium", "high", "max"], "effort": None,
                  "default": False, **NO_SPEED},
                 {"id": "haiku", "label": "Haiku", "efforts": [], "effort": None, "default": False, **NO_SPEED}]


@pytest.fixture(scope="module")
def lab(client):
    """A shared workspace of laex_owner's that laex_editor edits."""
    for name in (OWNER, EDITOR):
        make_user(name, f"pw-{name}")
    owner, editor = login(OWNER, f"pw-{OWNER}"), login(EDITOR, f"pw-{EDITOR}")
    ws = workspaces.create("Agents extras", account_of(OWNER), kind="shared")["id"]
    workspaces.set_member(ws, account_of(EDITOR), "editor", by=account_of(OWNER))
    for c in (owner, editor):
        c.headers["X-Gamma-Workspace"] = ws
    return SimpleNamespace(ws=ws, owner=owner, editor=editor, user=account_of(OWNER))


def _terminal(who, page, status=200) -> dict:
    r = who.get("/api/local-agents/chat/terminal", params={"bucket": page})
    assert r.status_code == status, r.text
    return r.json()


def _sources(who, page, selections, status=200) -> dict:
    r = who.post("/api/local-agents/chat/sources", json={"bucket": page, "selections": selections})
    assert r.status_code == status, r.text
    return r.json()


def _rows(who) -> list[dict]:
    return [j for j in who.get("/api/jobs").json()["jobs"] if j["kind"] == "computer-turn"]


# --- the models -----------------------------------------------------------------------------------

def test_the_probe_lists_the_models(lab, disk, monkeypatch):
    codex_answer, claude_answer = catalog.probes(fresh=True)
    assert codex_answer["ready"] and codex_answer["models"] == CODEX_MODELS  # two pages, the hidden one left out
    assert claude_answer["ready"] and claude_answer["models"] == CLAUDE_MODELS
    sent = requests_in(disk.log)
    assert [m["method"] for m in sent if m.get("method") == "model/list"] == ["model/list", "model/list"]
    assert not [m for m in sent if m.get("method") in ("thread/start", "turn/start")]  # no model used
    asked = [m for m in sent if m.get("type") == "control_request"]
    assert [m["request"]["subtype"] for m in asked] == ["initialize"]
    assert not [m for m in sent if m.get("type") == "user"]
    assert not turns_in(disk.log)
    listed = lab.owner.get("/api/local-agents").json()["agents"]
    assert [a["models"] for a in listed] == [CODEX_MODELS, CLAUDE_MODELS]
    # a run's start asks only whether the agent is ready: the models stay those of the last probe
    count = len(requests_in(disk.log))
    assert [catalog.probe(n, fresh=True, models=False)["models"] for n in catalog.AGENTS] == [CODEX_MODELS,
                                                                                               CLAUDE_MODELS]
    assert len(requests_in(disk.log)) == count
    # runs keep the answer fresh, never the list: one listed too long ago is listed again by a read
    catalog._listed["codex"] -= catalog.PROBE_TTL_S
    catalog.probe("codex", fresh=True, models=False)
    catalog.cached("codex")
    _until(lambda: not catalog._refreshing)
    assert len([m for m in requests_in(disk.log) if m.get("method") == "model/list"]) == 4  # two pages again
    # a run's start before any listing leaves none, and the next read lists them
    catalog.forget()
    assert catalog.probe("claude", fresh=True, models=False)["models"] == []
    assert catalog.cached("claude")["models"] == []
    _until(lambda: not catalog._refreshing)
    assert catalog.cached("claude")["models"] == CLAUDE_MODELS
    # a reader that comes back often takes what is kept however old, and starts no probe to refresh it
    catalog._cache["claude"]["at"] -= catalog.PROBE_TTL_S
    assert catalog.known("claude")["models"] == CLAUDE_MODELS and not catalog._refreshing

    # a Codex that cannot list them is still ready, with none
    script_agent(monkeypatch, disk.log, fail="models")
    assert catalog.probe("codex", fresh=True)["models"] == [] and catalog.probe("codex")["ready"]
    # an agent not signed in offers none
    script_agent(monkeypatch, disk.log, fail="not_signed_in")
    assert [a["models"] for a in catalog.probes(fresh=True)] == [[], []]


def test_a_choice_is_one_the_agent_listed():
    assert catalog.refused_choice("codex", CODEX_MODELS, "", "") == ""
    assert catalog.refused_choice("codex", CODEX_MODELS, "gpt-fake-mini", "medium") == ""
    assert catalog.refused_choice("codex", CODEX_MODELS, "", "high") == ""  # the default model's
    assert catalog.refused_choice("codex", CODEX_MODELS, "gpt-5", "") == (
        "Codex does not offer that model on this computer.")
    assert catalog.refused_choice("codex", CODEX_MODELS, "gpt-fake-mini", "high") == (
        "Codex does not offer that effort for this model.")
    assert catalog.refused_choice("claude", CLAUDE_MODELS, "haiku", "low") == (
        "Claude Code does not offer that effort for this model.")
    assert catalog.refused_choice("claude", [], "", "low") != ""  # no default model known: no effort
    assert catalog.refused_choice("codex", CODEX_MODELS, "", "", "fast") == ""  # the default model's
    assert catalog.refused_choice("codex", CODEX_MODELS, "gpt-fake", "high", "standard") == ""
    for model, speed in (("gpt-fake-mini", "fast"), ("", "ultrafast"), ("", "flex")):
        assert catalog.refused_choice("codex", CODEX_MODELS, model, "", speed) == (
            "Codex does not offer that speed for this model."), (model, speed)
    assert catalog.refused_choice("claude", [], "", "", "fast") != ""
    assert events.model("", "x", [], None, True) is None
    assert events.model(" m ", "", ["low", 3, ""], "", 1) == {"id": "m", "label": "m", "efforts": ["low"],
                                                             "effort": None, "default": False, **NO_SPEED}
    # the speeds Gamma knows, cheapest first; a default speed not listed is none
    assert events.model("m", "", [], None, True, ["fast", "warp", "standard", "flex"], "fast")["speeds"] == [
        "flex", "standard", "fast"]
    assert events.model("m", "", [], None, True, ["standard", "fast"], "flex")["speed"] is None
    # Codex: its fast tier is priority (an older Codex says fast), the config's tier its default speed
    listed = {"model": "m", "serviceTiers": [{"id": "priority", "name": "Fast"}, {"id": "ultrafast"}]}
    assert [(m["speeds"], m["speed"]) for m in codex._models([listed], "priority")] == [
        (["standard", "fast"], "fast")]
    assert codex._models([{**listed, "defaultServiceTier": "default"}], "priority")[0]["speed"] == "standard"
    assert codex._models([{"model": "m", "additionalSpeedTiers": ["fast"]}])[0]["speeds"] == ["standard", "fast"]
    for unknown in ([{"id": "ultrafast"}], [], None):   # no tier Gamma knows: no choice of speed
        assert codex._models([{"model": "m", "serviceTiers": unknown}], "priority")[0]["speeds"] == []
    assert claude.command(["claude"], stream=True, model="sonnet", effort="max", session="5e55-s") == [
        "claude", *claude.FLAGS, "--include-partial-messages", "--model", "sonnet", "--effort", "max",
        "--resume", "5e55-s"]
    assert claude.command(["claude"], model="default", effort="low") == ["claude", *claude.FLAGS, "--effort", "low"]
    # a speed is the fastMode setting, in the one --settings a turn with ask has too
    assert claude.command(["claude"], speed="fast") == ["claude", *claude.FLAGS, "--settings", '{"fastMode":true}']
    assert claude.command(["claude"], ask=True, speed="standard") == [
        "claude", *claude.ASK_FLAGS, "--settings", '{"permissions":{"ask":["Bash"]},"fastMode":false}']
    assert claude.command(["claude"], ask=True) == ["claude", *claude.ASK_FLAGS, "--settings",
                                                    '{"permissions":{"ask":["Bash"]}}']


def test_a_turn_runs_the_model_and_effort_asked_for(lab, disk, monkeypatch):
    f = _folder(lab, disk)
    catalog.probe("codex", fresh=True)
    for body, detail in (({"model": "gpt-5"}, "Codex does not offer that model on this computer."),
                         ({"model": "gpt-fake-mini", "effort": "high"},
                          "Codex does not offer that effort for this model.")):
        assert _say(lab.owner, f.page, status=400, **body)["detail"] == detail
    assert _chat(lab.owner, f.page)["conversation"] is None  # nothing was written

    made = _say(lab.owner, f.page, model="gpt-fake-mini", effort="low")
    queued = made["conversation"]["messages"][1]
    assert (queued["model"], queued["effort"]) == ("gpt-fake-mini", "low")
    assert runner.wait_idle(60)
    reply = _reply(lab, f.page)
    assert (reply["state"], reply["model"], reply["effort"]) == ("done", "gpt-fake-mini", "low")
    sent = requests_in(disk.log)
    started = [m["params"] for m in sent if m.get("method") == "thread/start"][-1]
    turn = [m["params"] for m in sent if m.get("method") == "turn/start"][-1]
    assert started["model"] == "gpt-fake-mini" and turn["effort"] == "low" and "model" not in turn
    assert "serviceTierForTurn" not in turn and "serviceTier" not in started  # no speed: the thread's own
    # none chosen: the agent's default, nothing said
    _say(lab.owner, f.page, "Again.")
    assert runner.wait_idle(60)
    assert (_reply(lab, f.page)["model"], _reply(lab, f.page)["effort"]) == (None, None)
    resumed = [m["params"] for m in requests_in(disk.log) if m.get("method") == "thread/resume"][-1]
    assert "model" not in resumed and "effort" not in [m["params"] for m in requests_in(disk.log)
                                                       if m.get("method") == "turn/start"][-1]


def _turn_started(log) -> dict:
    return [m["params"] for m in requests_in(log) if m.get("method") == "turn/start"][-1]


def test_a_turn_runs_at_the_speed_asked_for(lab, disk, monkeypatch):
    f = _folder(lab, disk)
    catalog.probe("codex", fresh=True)
    assert _say(lab.owner, f.page, model="gpt-fake-mini", speed="fast", status=400)["detail"] == (
        "Codex does not offer that speed for this model.")
    made = _say(lab.owner, f.page, speed="fast")                         # the default model's fast tier
    assert made["conversation"]["messages"][1]["speed"] == "fast"
    assert runner.wait_idle(60)
    assert (_reply(lab, f.page)["state"], _reply(lab, f.page)["speed"]) == ("done", "fast")
    started = [m["params"] for m in requests_in(disk.log) if m.get("method") == "thread/start"][-1]
    # the turn's tier alone, never the thread's: the next turn, or the person's codex resume, runs at its own
    assert _turn_started(disk.log)["serviceTierForTurn"] == "priority" and "serviceTier" not in started
    assert "serviceTier" not in _turn_started(disk.log)
    _say(lab.owner, f.page, "Again, at standard speed.", speed="standard")
    assert runner.wait_idle(60)
    assert _turn_started(disk.log)["serviceTierForTurn"] == "default"
    _say(lab.owner, f.page, "And as Codex is set up.")
    assert runner.wait_idle(60)
    assert _reply(lab, f.page)["speed"] is None and "serviceTierForTurn" not in _turn_started(disk.log)
    # the tier the user's config sets is the default speed of the models that offer it
    script_agent(monkeypatch, disk.log, config={"service_tier": "priority"})
    try:
        assert [m["speed"] for m in catalog.probe("codex", fresh=True)["models"]] == ["fast", None]
    finally:
        script_agent(monkeypatch, disk.log)
        catalog.probe("codex", fresh=True)


def test_claude_code_runs_the_model_and_effort_asked_for(lab, disk):
    g = _folder(lab, disk, agent="claude")
    catalog.probe("claude", fresh=True)
    assert _say(lab.owner, g.page, model="haiku", effort="low", status=400)["detail"] == (
        "Claude Code does not offer that effort for this model.")
    _say(lab.owner, g.page, model="sonnet", effort="max")
    assert runner.wait_idle(60)
    argv = turns_in(disk.log)[-1]["argv"]
    assert argv[argv.index("--model") + 1] == "sonnet" and argv[argv.index("--effort") + 1] == "max"
    assert claude.STREAM_FLAG in argv and "--settings" not in argv
    assert _say(lab.owner, g.page, model="sonnet", speed="fast", status=400)["detail"] == (
        "Claude Code does not offer that speed for this model.")
    _say(lab.owner, g.page, "Quickly.", speed="fast")                    # the default model has fast mode
    assert runner.wait_idle(60)
    argv = turns_in(disk.log)[-1]["argv"]
    assert json.loads(argv[argv.index("--settings") + 1]) == {"fastMode": True}


# --- the reply as it is typed ----------------------------------------------------------------------

def test_the_drivers_stream_a_chat_turns_text(disk, monkeypatch, tmp_path):
    work = tmp_path / "work"
    work.mkdir()
    chat = 'Talk:\n```json\n{"message": "hi"}\n```\n'
    for name in ("codex", "claude"):
        script_agent(monkeypatch, disk.log, deltas=["Dra", "ft ", "words"], chat="Final words.")
        got = list(events.start(name, argv=ARGV[name], cwd=str(work), prompt=chat, stream=True).events())
        said = [e for e in got if e["type"] in ("delta", "text")]
        assert said == [{"type": "delta", "text": "Dra"}, {"type": "delta", "text": "ft "},
                        {"type": "delta", "text": "words"}, {"type": "text", "text": "Final words."}], name
        assert got[-1]["ok"]
        # without stream: the whole message alone
        got = list(events.start(name, argv=ARGV[name], cwd=str(work), prompt=chat).events())
        assert [e for e in got if e["type"] in ("delta", "text")] == [{"type": "text", "text": "Final words."}], name
    inits = [m["params"] for m in requests_in(disk.log) if m.get("method") == "initialize"]
    assert codex.DELTA not in inits[0]["capabilities"]["optOutNotificationMethods"]
    assert inits[1]["capabilities"]["optOutNotificationMethods"] == codex.QUIET


def test_the_reply_grows_then_the_whole_message_takes_its_place(lab, disk, monkeypatch):
    monkeypatch.setattr(runner, "CHAT_EVERY_S", 0.05)
    f = _folder(lab, disk)
    script_agent(monkeypatch, disk.log, deltas=["Drafting ", "the ", "answer"], typing=0.5, chat="The answer.")
    _say(lab.owner, f.page)
    seen = set()

    def growing():
        reply = _reply(lab, f.page)
        seen.add(reply["text"])
        return "Drafting the" in reply["text"] and reply["state"] == "running"
    _until(growing)
    assert runner.wait_idle(60)
    reply = _reply(lab, f.page)
    assert (reply["state"], reply["text"]) == ("done", "The answer.")  # once, the deltas gone
    assert "Drafting" in seen and not [t for t in seen if "The answer." in t and "Drafting" in t]


# --- continue in a terminal --------------------------------------------------------------------------

def test_the_terminal_resumes_the_conversations_session(lab, disk, monkeypatch, anon):
    f = _folder(lab, disk)
    strip = lambda: lab.owner.get("/api/local-agents/chat", params={"bucket": f.page}).json()["computer"]  # noqa: E731
    assert _terminal(lab.owner, f.page, 404)["detail"] == "This conversation has no session to continue."
    assert strip()["session"] is False, "the strip says so too: the menu offers no terminal"
    made = _say(lab.owner, f.page)
    assert runner.wait_idle(60)
    found = _terminal(lab.owner, f.page)
    assert found == {"cwd": found["cwd"], "argv": ARGV["codex"] + ["resume", "019a0000-fake-thread"],
                     "agent": "codex", "label": "Codex"}
    assert Path(found["cwd"]) == f.root
    assert strip()["session"] is True

    # the maker alone, by a session
    assert lab.editor.get("/api/local-agents/chat/terminal", params={"bucket": f.page}).status_code == 404
    assert anon.get("/api/local-agents/chat/terminal", params={"bucket": f.page}).status_code == 401
    script = fresh_client()
    script.headers.update({"Authorization": f"Bearer {token_for(lab.owner, 'write', 'Script')}",
                           "X-Gamma-Workspace": lab.ws})
    assert script.get("/api/local-agents/chat/terminal", params={"bucket": f.page}).status_code == 403

    # never a session of another form, agent or directory
    conversation = made["conversation"]["id"]
    for session, agent, cwd in (("x; rm -rf ~", "codex", str(f.root)), ("019a-other", "claude", str(f.root)),
                                ("019a-other", "codex", str(f.root.parent))):
        store.ran_conversation(conversation, link_id=f.id, agent=agent, session=session, cwd=cwd)
        assert _terminal(lab.owner, f.page, 404)["detail"] == "This conversation has no session to continue."
    store.ran_conversation(conversation, link_id=f.id, agent="codex", session="019a0000-fake-thread",
                           cwd=str(f.root))

    # never while a turn works
    script_agent(monkeypatch, disk.log, delay=30)
    turn = _say(lab.owner, f.page, "Take your time.")["turn"]["id"]
    _until(lambda: (runner.chat_state(lab.ws, turn) or {}).get("state") == "running")
    assert _terminal(lab.owner, f.page, 409)["detail"] == "Codex is still working: stop it first."
    _stop(lab.owner, turn)
    assert runner.wait_idle(30)
    assert _terminal(lab.owner, f.page)["argv"][-2:] == ["resume", "019a0000-fake-thread"]

    # the agent turned off: no terminal
    store.set_agent("codex", False, by=lab.user)
    assert _terminal(lab.owner, f.page, 409)["detail"] == "Turn on Codex in Settings → AI → Agents first."


def test_the_terminal_of_claude_code(lab, disk):
    f = _folder(lab, disk, agent="claude")
    _say(lab.owner, f.page)
    assert runner.wait_idle(60)
    assert _terminal(lab.owner, f.page)["argv"] == ARGV["claude"] + ["--resume", "5e55-fake-session"]
    lab.owner.post("/api/local-agents/chat/new", json={"bucket": f.page}).raise_for_status()
    assert _terminal(lab.owner, f.page, 404)["detail"] == "This conversation has no session to continue."


# --- a selection's source line -------------------------------------------------------------------------

def test_the_sources_of_the_selections(lab, disk):
    f = _folder(lab, disk)
    passage = PLACES["v1"]["passage"]
    box = [passage["rects"][1][0], passage["rects"][0][1], passage["rects"][1][2], passage["rects"][2][3]]
    chosen = {"text": passage["quote"], "page": 1, "box": box}
    found = _sources(lab.owner, f.page, [chosen, {"text": "no box", "page": 1}, "junk", {"text": " "}])["sources"]
    assert found[0]["file"] == "sections/method.tex" and isinstance(found[0]["line"], int)
    assert found[1:] == [None, None, None]
    assert _sources(lab.owner, f.page, [])["sources"] == []
    assert _sources(lab.owner, f.page, [chosen] * (chats.MAX_SELECTIONS + 1), 400)["detail"] == (
        f"At most {chats.MAX_SELECTIONS} selections at a time.")
    r = lab.editor.post("/api/local-agents/chat/sources", json={"bucket": f.page, "selections": [chosen]})
    assert r.status_code == 404
    assert not turns_in(disk.log)  # no agent ran


# --- Background tasks -------------------------------------------------------------------------------------

def test_background_tasks_list_the_turns_that_work(lab, disk, monkeypatch):
    f = _folder(lab, disk)
    store.set_runs("codex", "ask", by=lab.user)
    script_agent(monkeypatch, disk.log, ask=[{"command": "curl https://example.com", "host": "example.com"}])
    assert _rows(lab.owner) == []
    said = _say(lab.owner, f.page)
    turn = said["turn"]["id"]
    _until(lambda: _reply(lab, f.page).get("asks"))
    [row] = _rows(lab.owner)
    with connect_pages_db(lab.ws) as conn:
        title = conn.execute("SELECT content FROM unified_blocks WHERE id = ?", (f.page,)).fetchone()[0]
    assert title and row == {
        "id": f"computer-{turn}", "kind": "computer-turn", "owner": lab.user, "workspace": lab.ws,
        "title": f"Codex · {title}", "params": {"bucket": f.page, "turn": turn, "agent": "codex",
                                                    "agent_id": agents.here("codex").id, "label": "Codex",
                                                    "title": title, "conversation": said["conversation"]["id"]},
        "state": "running", "progress": {"phase": "asking"}, "error": "", "created_at": row["created_at"],
        "started_at": row["started_at"], "finished_at": "", "artifact": None, "downloaded": False,
        "stoppable": True, "stopping": False, "readonly": True}
    assert row["created_at"] and row["started_at"] >= row["created_at"]
    assert _rows(lab.editor) == []  # the owner's alone
    _stop(lab.owner, turn)
    assert runner.wait_idle(30)
    assert _rows(lab.owner) == []

    # one that waits for its slot reads queued, and asks nothing
    store.set_runs("codex", "never", by=lab.user)
    script_agent(monkeypatch, disk.log, delay=30)
    held = threading.BoundedSemaphore(1)
    held.acquire()
    monkeypatch.setattr(runner, "_slots", held)
    turn = _say(lab.owner, f.page, "Wait for it.")["turn"]["id"]
    _until(lambda: _rows(lab.owner))
    [row] = _rows(lab.owner)
    assert (row["state"], row["progress"], row["started_at"]) == ("queued", {}, "")
    _stop(lab.owner, turn)
    _until(lambda: not _rows(lab.owner) or _rows(lab.owner)[0]["stopping"])
    held.release()
    assert runner.wait_idle(30)
    assert _rows(lab.owner) == []


def test_a_co_authors_turns_and_chats_reach_the_owner_who_reads_them(lab, disk, monkeypatch):
    """A co-author's turn with the owner's agent is in the owner's Background
    tasks too, naming the person and the conversation the row opens; the
    co-author's conversations are in the owner's History under their name,
    read there in place of the owner's own (``?conversation=``), stopped and
    exported, never opened as the owner's or deleted."""
    f = _folder(lab, disk)
    editor = account_of(EDITOR)
    store.name_person("codex", "", lab.ws, editor, EDITOR)
    try:
        script_agent(monkeypatch, disk.log, delay=30)
        made = _say(lab.editor, f.page, "Fix the caption.")
        turn, conversation = made["turn"]["id"], made["conversation"]["id"]
        _until(lambda: _rows(lab.owner))
        [row] = _rows(lab.owner)
        assert (row["params"]["conversation"], row["params"]["person"], "thread" in row["params"]) == (
            conversation, EDITOR, False)
        [theirs] = _rows(lab.editor)
        assert theirs["params"]["conversation"] == conversation and "person" not in theirs["params"]
        assert _stop(lab.owner, turn) == {"stopped": True}                     # the owner may stop it
        assert runner.wait_idle(30)
        listed = lab.owner.get("/api/local-agents/chat/history", params={"bucket": f.page}).json()
        assert [(c["id"], c["person"], c["agent"]) for c in listed["lent"]] == [(conversation, EDITOR, "codex")]
        assert conversation not in [c["id"] for c in listed["conversations"]]
        assert lab.editor.get("/api/local-agents/chat/history", params={"bucket": f.page}).json()["lent"] == []
        seen = _chat(lab.owner, f.page, conversation=conversation)
        assert (seen["conversation"]["id"], seen["conversation"]["person"], seen["computer"]["session"]) == (
            conversation, EDITOR, False)
        assert (_chat(lab.owner, f.page)["conversation"] or {}).get("id") != conversation   # never the owner's own
        assert _chat(lab.editor, f.page, conversation=conversation)["conversation"] is None
        assert lab.owner.get(f"/api/local-agents/chat/history/{conversation}/export").status_code == 200
        assert lab.owner.post(f"/api/local-agents/chat/history/{conversation}/open").status_code == 404
        assert lab.owner.delete(f"/api/local-agents/chat/history/{conversation}").status_code == 404
    finally:
        store.unname_person("codex", "", lab.ws, editor)


# --- sessions started elsewhere ------------------------------------------------------------------------

def _sessions(who, agent, dir, status=200) -> dict:
    r = who.get("/api/local-agents/sessions", params={"agent": agent, "dir": dir})
    assert r.status_code == status, r.text
    return r.json()


def _open(who, bucket, agent, dir, session, status=200) -> dict:
    r = who.post("/api/local-agents/sessions/open",
                 json={"bucket": bucket, "agent": agent, "dir": dir, "session": session})
    assert r.status_code == status, r.text
    return r.json()


def _shape(messages) -> list:
    return [(m["role"], m["text"], m.get("state"), [s["kind"] for s in m.get("steps") or []], m.get("from_session"))
            for m in messages]


def _codex_store(root: Path, other: Path) -> dict:
    """The threads a person's Codex keeps: one of a terminal in the folder
    (a command, an edit, its thinking, a second turn stopped), one of a
    script there, an old one of Gamma's there, one elsewhere and a
    subagent's."""
    def thread(id, updated, **more):
        return {"id": id, "name": None, "preview": "", "cwd": str(root), "source": "cli", "originator": "codex-tui",
                "createdAt": updated - 60, "updatedAt": updated, "turns": [], **more}
    terminal = thread("019b-term-fix", 1794000300, name="Fix the abstract", preview="fix the abstract's typo", turns=[
        {"id": "t1", "status": "completed", "startedAt": 1794000000, "completedAt": 1794000060, "items": [
            {"type": "userMessage", "id": "u1", "content": [{"type": "text", "text": "fix the abstract's typo"}]},
            {"type": "reasoning", "id": "r1", "summary": ["a summary"], "content": ["secret thoughts"]},
            {"type": "agentMessage", "id": "a1", "text": "Looking at main.tex.", "phase": "commentary"},
            {"type": "commandExecution", "id": "c1", "command": "rg abstract main.tex", "cwd": str(root),
             "status": "completed", "aggregatedOutput": "9: the abstract", "exitCode": 0},
            {"type": "fileChange", "id": "f1", "status": "completed",
             "changes": [{"path": str(root / "main.tex"), "kind": {"type": "update"}, "diff": ""}]},
            {"type": "agentMessage", "id": "a2", "text": "Fixed it in main.tex:9.", "phase": "final_answer"}]},
        {"id": "t2", "status": "interrupted", "startedAt": 1794000200, "completedAt": 1794000300, "items": [
            {"type": "userMessage", "id": "u2", "content": [{"type": "text", "text": "and the title"}]}]}])
    return {"threads": [
        terminal,
        thread("019b-term-script", 1794000100, preview="summarize the method\nin two lines", source="exec",
               originator="codex_exec"),
        thread("019b-gamma-old", 1794000050, name="Shorten the title", source="vscode", originator="gamma"),
        thread("019b-elsewhere", 1794000400, cwd=str(other)),
        thread("019b-subagent", 1794000500, source="subAgent")]}


def test_codex_sessions_of_the_folder_listed_opened_and_resumed(lab, disk, monkeypatch, anon, tmp_path):
    """History's On this computer: the threads Codex keeps of the folder's
    directory, started in a terminal, an editor or a script, by name or
    first message, the most recently used first; one opened is a
    conversation of the chat holding its turns (no thinking, its steps as
    the runner words them) whose next turn resumes it, Gamma's own marked
    as such; one held by another Codex refused with why, at its opening and
    at a turn, never forked."""
    from fake_agents.hold_lock import hold

    f = _folder(lab, disk)
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex"))
    kept = _codex_store(f.root, tmp_path / "elsewhere")
    script_agent(monkeypatch, disk.log, sessions=kept)
    listed = _sessions(lab.owner, "codex", f.w)["sessions"]
    assert [(s["id"], s["title"], s["source"], s["ours"], s["busy"]) for s in listed] == [
        ("019b-term-fix", "Fix the abstract", "cli", False, False),
        ("019b-term-script", "summarize the method", "exec", False, False),
        ("019b-gamma-old", "Shorten the title", "gamma", False, False)]
    assert listed[0]["at"].startswith("2026-11-") and listed[0]["created"] < listed[0]["at"]
    assert [m["params"]["sourceKinds"] for m in requests_in(disk.log) if m.get("method") == "thread/list"] == [
        ["cli", "vscode", "exec"]]

    # the agent's owner alone, for a directory its agent works in
    assert lab.editor.get("/api/local-agents/sessions", params={"agent": "codex", "dir": f.w}).status_code == 404
    assert anon.get("/api/local-agents/sessions", params={"agent": "codex", "dir": f.w}).status_code == 401
    script = fresh_client()
    script.headers.update({"Authorization": f"Bearer {token_for(lab.owner, 'write', 'Script')}",
                           "X-Gamma-Workspace": lab.ws})
    assert script.get("/api/local-agents/sessions", params={"agent": "codex", "dir": f.w}).status_code == 403
    for agent, dir in (("claude", f.w), ("codex", "no-such-dir"), ("codex", ""), ("gemini", f.w)):
        _sessions(lab.owner, agent, dir, 404)

    # held by another Codex: busy, and refused with why
    lock = tmp_path / "codex" / "thread-writer-locks" / "019b-term-script.lock"
    with hold(str(lock)):
        assert [s["busy"] for s in _sessions(lab.owner, "codex", f.w)["sessions"]] == [False, True, False]
        assert _open(lab.owner, f.page, "codex", f.w, "019b-term-script", 409)["detail"] == (
            "Codex has this session open in another window: close it there first.")
    lock.write_bytes(b"")   # left by a Codex that was killed: nobody's
    assert [s["busy"] for s in _sessions(lab.owner, "codex", f.w)["sessions"]] == [False, False, False]

    # opened: the chat's conversation now, its turns read, bound to the thread
    _open(lab.owner, "home", "codex", f.w, "019b-term-fix", 400)        # the library's chat works elsewhere
    _open(lab.owner, f.page, "codex", f.w, "019b-elsewhere", 404)       # another directory's
    _open(lab.owner, f.page, "codex", f.w, "x; rm -rf ~", 404)
    opened = _open(lab.owner, f.page, "codex", f.w, "019b-term-fix")["conversation"]
    assert opened["title"] == "Fix the abstract" and opened["agent"] == "codex"
    assert _shape(opened["messages"]) == [
        ("user", "fix the abstract's typo", None, [], True),
        ("agent", "Looking at main.tex.\n\nFixed it in main.tex:9.", "done", ["run", "edit"], True),
        ("user", "and the title", None, [], True),
        ("agent", "", "stopped", [], True)]
    assert opened["messages"][1]["steps"] == [{"id": "c1", "kind": "run", "status": "completed", "program": "rg"},
                                              {"id": "f1:0", "kind": "edit", "status": "completed", "path": "main.tex"}]
    assert "secret" not in str(opened["messages"])
    assert _chat(lab.owner, f.page)["conversation"]["id"] == opened["id"]
    ran = store.conversation_of(opened["id"])
    assert (ran["session"], ran["agent"], ran["link_id"], Path(ran["cwd"])) == ("019b-term-fix", "codex", f.id, f.root)
    assert not turns_in(disk.log), "reading a session runs no turn"
    listed = _sessions(lab.owner, "codex", f.w)["sessions"]
    assert [(s["id"], s["ours"]) for s in listed][:1] == [("019b-term-fix", True)]

    # opened again, or after New chat: the same conversation; from another page of the folder, refused
    assert _open(lab.owner, f.page, "codex", f.w, "019b-term-fix")["conversation"]["id"] == opened["id"]
    lab.owner.post("/api/local-agents/chat/new", json={"bucket": f.page}).raise_for_status()
    assert _open(lab.owner, f.page, "codex", f.w, "019b-term-fix")["conversation"]["id"] == opened["id"]
    assert _chat(lab.owner, f.page)["conversation"]["id"] == opened["id"]
    figure = _page_of(f.link, "figures/fig1.pdf")
    assert _open(lab.owner, figure, "codex", f.w, "019b-term-fix", 409)["detail"] == (
        "The chat of main continues this session: open it there.")
    assert lab.editor.post("/api/local-agents/sessions/open", json={
        "bucket": f.page, "agent": "codex", "dir": f.w, "session": "019b-term-fix"}).status_code == 404

    # bound to a conversation the workspace no longer has (its page deleted for good): listed and opened as any
    # other, the new conversation alone resuming it
    gone = _open(lab.owner, f.page, "codex", f.w, "019b-term-script")["conversation"]
    with connect_pages_db(lab.ws) as conn:
        conn.execute("DELETE FROM computer_chats WHERE id = ?", (gone["id"],))
    assert [(s["id"], s["ours"]) for s in _sessions(lab.owner, "codex", f.w)["sessions"]][1] == (
        "019b-term-script", False)
    anew = _open(lab.owner, f.page, "codex", f.w, "019b-term-script")["conversation"]
    assert anew["id"] != gone["id"] and anew["title"] == "summarize the method"
    assert store.bound("codex", ["019b-term-script"]) == {"019b-term-script": anew["id"]}
    assert store.conversation_of(gone["id"])["session"] == ""
    assert _open(lab.owner, f.page, "codex", f.w, "019b-term-fix")["conversation"]["id"] == opened["id"]

    # the next turn resumes the thread, telling nothing of what the session holds
    _say(lab.owner, f.page, "Now the conclusion.")
    assert runner.wait_idle(60)
    sent = requests_in(disk.log)
    assert [m["params"]["threadId"] for m in sent if m.get("method") == "thread/resume"] == ["019b-term-fix"]
    assert not [m for m in sent if m.get("method") == "thread/start"]
    assert "fix the abstract" not in str(_turns_in(disk.log)[-1])
    assert _reply(lab, f.page)["state"] == "done" and _reply(lab, f.page)["fresh"] is False

    # held when a turn takes it up: the turn fails with why, and no thread is started in its place
    kept["busy"] = ["019b-term-fix"]
    script_agent(monkeypatch, disk.log, sessions=kept)
    _say(lab.owner, f.page, "And the references.")
    assert runner.wait_idle(60)
    reply = _reply(lab, f.page)
    assert (reply["state"], reply["error"]) == ("failed", "Codex has this session open in another window: "
                                                          "close it there first.")
    assert not [m for m in requests_in(disk.log) if m.get("method") == "thread/start"]
    assert store.conversation_of(opened["id"])["session"] == "019b-term-fix"


def test_sessions_of_an_offered_directory_listed_and_opened(lab, disk, monkeypatch, tmp_path):
    """An offered directory picked in the library's chat: its sessions
    listed by the directory's id, one opened there bound to it in that
    directory; a page of a synced folder, whose agent works in the folder,
    refused."""
    f = _folder(lab, disk)
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    [offered] = store.set_dirs([{"path": str(scratch), "name": "Scratch"}])
    try:
        monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex"))
        script_agent(monkeypatch, disk.log, sessions=_codex_store(scratch, tmp_path / "elsewhere"))
        assert [s["id"] for s in _sessions(lab.owner, "codex", offered["id"])["sessions"]] == [
            "019b-term-fix", "019b-term-script", "019b-gamma-old"]
        _open(lab.owner, f.page, "codex", offered["id"], "019b-term-fix", 400)
        opened = _open(lab.owner, "home", "codex", offered["id"], "019b-term-fix")["conversation"]
        assert opened["title"] == "Fix the abstract"
        assert _chat(lab.owner, "home")["computer"]["dir"] == {"kind": "offered", "id": offered["id"],
                                                               "name": "Scratch"}
        ran = store.conversation_of(opened["id"])
        assert (ran["session"], ran["link_id"], Path(ran["cwd"])) == ("019b-term-fix", "", scratch)
        assert [(s["id"], s["ours"]) for s in _sessions(lab.owner, "codex", offered["id"])["sessions"]][0] == (
            "019b-term-fix", True)
    finally:
        store.set_dirs([])


def _claude_session(home: Path, cwd: Path, session: str, records: list[dict], at: float) -> None:
    folder = sessions_mod._long(home / "projects" / sessions_mod.slug(str(cwd)))   # past 260 characters here
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, f"{session}.jsonl")
    with open(path, "w", encoding="utf-8") as file:
        file.write("".join(json.dumps(r) + "\n" for r in records))
    os.utime(path, (at, at))


def _said(uuid, parent, role, content, at, **more) -> dict:
    return {"type": role, "uuid": uuid, "parentUuid": parent, "isSidechain": False, "timestamp": at,
            "message": {"role": role, "content": content}, **more}


def test_claude_code_sessions_of_the_folder_listed_opened_and_resumed(lab, disk, monkeypatch, tmp_path):
    """Claude Code's sessions are the files of its project's directory whose
    directory is the folder's: titled by their ai-title, read along the
    branch the last prompt left, a process of its that lives naming one
    busy; one opened is resumed with --resume, refused while a terminal has
    it open, and a resume that fails starts afresh without the session's
    own text."""
    f = _folder(lab, disk, agent="claude")
    home = tmp_path / "claude"
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(home))
    cwd = {"cwd": str(f.root), "entrypoint": "cli", "sessionId": "c1"}
    _claude_session(home, f.root, "c1-method", [
        {"type": "queue-operation", "operation": "enqueue", "sessionId": "c1"},
        _said("u1", None, "user", "Explain the method section", "2026-11-10T10:00:00.000Z", **cwd),
        {"type": "attachment", "uuid": "at1", "parentUuid": "u1", "attachment": {}},
        _said("a1", "at1", "assistant", [
            {"type": "thinking", "thinking": "secret thoughts"}, {"type": "text", "text": "Reading it."},
            {"type": "tool_use", "id": "tu1", "name": "Read",
             "input": {"file_path": str(f.root / "sections" / "method.tex")}}], "2026-11-10T10:00:05.000Z"),
        _said("r1", "a1", "user", [{"type": "tool_result", "tool_use_id": "tu1", "content": "1\tWe fit"}],
              "2026-11-10T10:00:06.000Z"),
        _said("a2", "r1", "assistant", [{"type": "text", "text": "It fits weight decay."}], "2026-11-10T10:00:09.000Z"),
        _said("x1", "a2", "user", "a forked question", "2026-11-10T10:01:00.000Z"),
        _said("x2", "x1", "assistant", [{"type": "text", "text": "a forked answer"}], "2026-11-10T10:01:05.000Z"),
        _said("m1", "a2", "user", "<command-name>/model</command-name>", "2026-11-10T10:02:00.000Z"),
        _said("u2", "m1", "user", [{"type": "text", "text": "Shorter, please"}], "2026-11-10T10:03:00.000Z"),
        _said("a3", "u2", "assistant", [{"type": "text", "text": "Weight decay, fitted."}], "2026-11-10T10:03:04.000Z"),
        {"type": "ai-title", "aiTitle": "Explaining the method", "sessionId": "c1"},
        {"type": "last-prompt", "lastPrompt": "Shorter, please", "leafUuid": "a3", "sessionId": "c1"}], 1794000300)
    _claude_session(home, f.root, "c2-editor", [
        _said("u1", None, "user", "Check the figures\nplease", "2026-11-11T10:00:00.000Z",
              **{**cwd, "entrypoint": "claude-vscode"})], 1794000200)
    other = f.root.parent.parent / "disk-paper"   # another directory of the same project name
    _claude_session(home, other, "c3-other", [
        _said("u1", None, "user", "Elsewhere", "2026-11-12T10:00:00.000Z", cwd=str(other))], 1794000400)
    (home / "sessions").mkdir()
    ended = subprocess.Popen([sys.executable, "-c", "pass"])
    ended.wait()   # a process that ended: its entry names no live one
    (home / "sessions" / "1.json").write_text(json.dumps({"pid": os.getpid(), "sessionId": "c2-editor"}))
    (home / "sessions" / "2.json").write_text(json.dumps({"pid": ended.pid, "sessionId": "c1-method"}))

    listed = _sessions(lab.owner, "claude", f.w)["sessions"]
    assert [(s["id"], s["title"], s["source"], s["ours"], s["busy"]) for s in listed] == [
        ("c1-method", "Explaining the method", "cli", False, False),
        ("c2-editor", "Check the figures", "vscode", False, True)]
    assert (listed[0]["created"], listed[0]["at"]) == ("2026-11-10T10:00:00.000000Z", "2026-11-10T10:03:04.000000Z")
    assert _open(lab.owner, f.page, "claude", f.w, "c2-editor", 409)["detail"] == (
        "Claude Code has this session open in another window: close it there first.")
    _open(lab.owner, f.page, "claude", f.w, "c3-other", 404)

    opened = _open(lab.owner, f.page, "claude", f.w, "c1-method")["conversation"]
    assert opened["title"] == "Explaining the method"
    assert _shape(opened["messages"]) == [
        ("user", "Explain the method section", None, [], True),
        ("agent", "Reading it.\n\nIt fits weight decay.", "done", ["read"], True),
        ("user", "Shorter, please", None, [], True),
        ("agent", "Weight decay, fitted.", "done", [], True)]
    assert opened["messages"][1]["steps"][0] == {"id": "tu1", "kind": "read", "status": "completed",
                                                 "path": "sections/method.tex"}
    assert "secret" not in str(opened["messages"]) and "forked" not in str(opened["messages"])

    # the next turn resumes it
    _say(lab.owner, f.page, "Now the conclusion.")
    assert runner.wait_idle(60)
    assert turns_in(disk.log)[-1]["argv"][-2:] == ["--resume", "c1-method"]

    # open in a terminal when a turn takes it up: refused with why, no process started
    (home / "sessions" / "3.json").write_text(json.dumps({"pid": os.getpid(), "sessionId": "c1-method"}))
    started = len(turns_in(disk.log))
    _say(lab.owner, f.page, "And the references.")
    assert runner.wait_idle(60)
    reply = _reply(lab, f.page)
    assert (reply["state"], reply["error"]) == ("failed", "Claude Code has this session open in another window: "
                                                          "close it there first.")
    assert len(turns_in(disk.log)) == started
    (home / "sessions" / "3.json").unlink()

    # a resume that fails starts afresh, told the conversation's own messages, never the session's
    script_agent(monkeypatch, disk.log, fail="resume")
    _say(lab.owner, f.page, "Once more.")
    assert runner.wait_idle(60)
    told = [e["stdin"] for e in read_log(disk.log) if "stdin" in e][-1]
    assert "Now the conclusion." in told and "Explain the method section" not in told
    assert _reply(lab, f.page)["fresh"] is True


def test_claude_codes_compaction_summary_is_no_prompt(monkeypatch, tmp_path):
    """A session compacted: the summary Claude Code wrote in the person's
    place past the boundary is never read as what they said; the prompts on
    both sides of it are."""
    home, root = tmp_path / "claude", tmp_path / "paper"
    root.mkdir()
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(home))
    cwd = {"cwd": str(root), "entrypoint": "cli", "sessionId": "c1"}
    summary = "This session is being continued from a previous conversation that ran out of context."
    _claude_session(home, root, "c1-compact", [
        _said("u1", None, "user", "Explain the method", "2026-11-10T10:00:00.000Z", **cwd),
        _said("a1", "u1", "assistant", [{"type": "text", "text": "It fits."}], "2026-11-10T10:00:05.000Z"),
        {"type": "system", "subtype": "compact_boundary", "uuid": "b1", "parentUuid": None,
         "logicalParentUuid": "a1", "timestamp": "2026-11-10T11:00:00.000Z"},
        _said("s1", "b1", "user", summary, "2026-11-10T11:00:00.000Z", isCompactSummary=True,
              isVisibleInTranscriptOnly=True, **cwd),
        _said("u2", "s1", "user", "Now the results", "2026-11-10T11:01:00.000Z", **cwd),
        _said("a2", "u2", "assistant", [{"type": "text", "text": "They hold."}], "2026-11-10T11:01:05.000Z"),
        {"type": "last-prompt", "lastPrompt": "Now the results", "leafUuid": "a2", "sessionId": "c1"}], 1794000300)
    read = sessions_mod.read("claude", str(root), "c1-compact")
    assert [turn["prompt"] for turn in read["turns"]] == ["Explain the method", "Now the results"]
    said = [m["text"] for m in sessions_mod.messages("claude", read["turns"], str(root)) if m["role"] == "user"]
    assert said == ["Explain the method", "Now the results"]
    assert summary not in str(read)


def test_claude_codes_project_names():
    """Claude Code's name of a project's directory (check C4): every
    character outside A-Za-z0-9 a dash, case kept, and past 200 characters
    cut with the base-36 of the path's Java hash after; the long sample is
    the one the check saw on disk."""
    assert sessions_mod.slug("C:\\Users\\Tim\\work\\cc term_v1.2") == "C--Users-Tim-work-cc-term-v1-2"
    assert sessions_mod.slug("/home/tim/中文 é") == "-home-tim-----"
    assert sessions_mod.slug("/a/\U0001F600") == "-a---"   # an astral character is two UTF-16 units
    sample = ("C:\\Users\\Tim\\AppData\\Local\\Temp\\claude\\d--Codes-Github-gamma\\c4f3c9ab-5108-43ba-8891-7eb475c93860"
              "\\scratchpad\\stage0\\c3c4\\work\\long_" + "abcdefghij" * 10)
    found = sessions_mod.slug(sample)
    assert len(found) == 205 and found.endswith("-gygl")


def test_codex_threads_read_page_by_page_and_whole(monkeypatch, tmp_path):
    """codex.threads reads thread/list page by page (its cursor) up to the
    limit, a person's sources alone; codex.thread reads a thread's turns
    with every item, oldest first."""
    log, root = tmp_path / "codex.log", tmp_path / "paper"
    kept = _codex_store(root, tmp_path / "elsewhere")
    kept["threads"] += [{**kept["threads"][1], "id": f"019b-more-{i}", "updatedAt": 1794000000 - i}
                        for i in range(3)]
    script_agent(monkeypatch, log, sessions={**kept, "page": 2})
    found = codex.threads(ARGV["codex"], str(root), 5)
    assert [t["id"] for t in found] == ["019b-term-fix", "019b-term-script", "019b-gamma-old", "019b-more-0",
                                        "019b-more-1"]
    assert [m["params"].get("cursor") for m in requests_in(log) if m.get("method") == "thread/list"] == [
        None, "2", "4"]
    thread, turns = codex.thread(ARGV["codex"], "019b-term-fix", 10)
    assert thread["name"] == "Fix the abstract" and [t["id"] for t in turns] == ["t1", "t2"]
    [asked] = [m["params"] for m in requests_in(log) if m.get("method") == "thread/turns/list"]
    assert asked["itemsView"] == "full"
    with pytest.raises(codex.RequestError):
        codex.thread(ARGV["codex"], "019b-no-such", 10)


def test_sessions_are_the_agents_owners_in_a_place_of_theirs(lab, disk, monkeypatch, tmp_path):
    """A directory named by a path is no place; another account with an
    agent of its own on this computer lists nothing of a vault it did not
    make, nor opens one of its sessions."""
    f = _folder(lab, disk)
    home = tmp_path / "claude"
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(home))
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex"))
    script_agent(monkeypatch, disk.log, sessions=_codex_store(f.root, tmp_path / "elsewhere"))
    _claude_session(home, f.root, "c1-method", [
        _said("u1", None, "user", "Explain the method", "2026-11-10T10:00:00.000Z", cwd=str(f.root))], 1794000300)
    for dir in (str(f.root), f.root.as_uri(), "../paper", "paper"):
        _sessions(lab.owner, "codex", dir, 404)
    _open(lab.owner, f.page, "codex", "../paper", "019b-term-fix", 404)
    _open(lab.owner, f.page, "codex", str(f.root), "019b-term-fix", 422)   # longer than any id
    assert store.bound("codex", ["019b-term-fix"]) == {}
    store.set_agent("claude", False, by="", force=True)
    store.set_agent("claude", True, by=account_of(EDITOR))
    try:
        _sessions(lab.editor, "claude", f.w, 404)
        _open(lab.editor, f.page, "claude", f.w, "c1-method", 404)
        assert [s["id"] for s in _sessions(lab.owner, "codex", f.w)["sessions"]][:1] == ["019b-term-fix"]
    finally:
        store.set_agent("claude", False, by="", force=True)


def test_claude_codes_sidechains_and_other_records_are_no_turns(monkeypatch, tmp_path):
    """A sidechain's records (a subagent's) and the records that are no
    message (attachments, queue operations, snapshots) are neither the
    session's directory, its title nor its turns, even when a sidechain's
    is the file's last record and no last-prompt names the leaf."""
    home, root, other = tmp_path / "claude", tmp_path / "paper", tmp_path / "elsewhere"
    root.mkdir()
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(home))
    cwd = {"cwd": str(root), "entrypoint": "cli"}
    _claude_session(home, root, "c1-side", [
        {"type": "queue-operation", "operation": "enqueue"},
        _said("s0", None, "user", "a subagent's task", "2026-11-10T09:59:00.000Z", isSidechain=True, cwd=str(other)),
        {"type": "file-history-snapshot", "messageId": "m", "snapshot": {}},
        _said("u1", None, "user", "Explain the method", "2026-11-10T10:00:00.000Z", **cwd),
        {"type": "attachment", "uuid": "at1", "parentUuid": "u1", "attachment": {}},
        _said("a1", "at1", "assistant", [{"type": "text", "text": "It fits."}], "2026-11-10T10:00:05.000Z"),
        _said("u2", "a1", "user", "And the results?", "2026-11-10T10:01:00.000Z", **cwd),
        _said("a2", "u2", "assistant", [{"type": "text", "text": "They hold."}], "2026-11-10T10:01:05.000Z"),
        _said("s1", "s0", "assistant", [{"type": "text", "text": "a subagent's answer"}], "2026-11-10T10:01:09.000Z",
              isSidechain=True)], 1794000300)
    [row] = sessions_mod._claude_listed(str(root))
    assert (row["id"], row["title"], row["source"]) == ("c1-side", "Explain the method", "cli")
    assert sessions_mod._claude_listed(str(other)) == []
    read = sessions_mod.read("claude", str(root), "c1-side")
    assert [(t["prompt"], t["paragraphs"]) for t in read["turns"]] == [
        ("Explain the method", ["It fits."]), ("And the results?", ["They hold."])]
    assert "subagent" not in str(read)
