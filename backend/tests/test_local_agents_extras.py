"""The rest of the chat's computer side (step 3c; docs/dev/local_agents.md
"The chat's computer side"), against the stand-ins (tests/fake_agents/)
through the real processes, on the sample paper as a work folder of this
computer (test_local_agents_runner's fixtures).

The probe lists the models each agent offers without using one (Codex's
``model/list``, its pages followed and the hidden left out; Claude Code's
``initialize``), in the listing and the strip; a turn asks for a model and
an effort the agent listed (Codex's thread and turn, Claude Code's
``--model`` and ``--effort``), any other refused; a chat turn's reply grows
as its deltas come and the whole message takes their place; the terminal
route gives the shell the conversation's session to resume, to the vault's
maker only, never while a turn works, never a session of another form,
agent or directory; the sources route maps the composer's selections to
their lines; Background tasks lists the account's turns while they are
queued or running, asking or not."""

import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from conftest import account_of, fresh_client, login, make_user, token_for
from fake_agents import ARGV, requests_in, script_agent, turns_in
from gamma import workspaces
from gamma.db import connect_pages_db
from gamma.local_agents import catalog, chats, claude, codex, events, runner, store
from test_local_agents_chat import PLACES, _chat, _reply, _say, _stop
from test_local_agents_runner import _folder, _until, disk  # noqa: F401: the disk fixture

OWNER, EDITOR = "laex_owner", "laex_editor"
CODEX_MODELS = [{"id": "gpt-fake", "label": "GPT Fake", "efforts": ["low", "medium", "high"], "effort": "medium",
                 "default": True},
                {"id": "gpt-fake-mini", "label": "GPT Fake Mini", "efforts": ["low", "medium"], "effort": "low",
                 "default": False}]
CLAUDE_MODELS = [{"id": "default", "label": "Default (recommended)", "efforts": ["low", "medium", "high"],
                  "effort": None, "default": True},
                 {"id": "sonnet", "label": "Sonnet", "efforts": ["low", "medium", "high", "max"], "effort": None,
                  "default": False},
                 {"id": "haiku", "label": "Haiku", "efforts": [], "effort": None, "default": False}]


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
    r = who.get("/api/local-agents/chat/terminal", params={"page": page})
    assert r.status_code == status, r.text
    return r.json()


def _sources(who, page, selections, status=200) -> dict:
    r = who.post("/api/local-agents/chat/sources", json={"page": page, "selections": selections})
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
    assert events.model("", "x", [], None, True) is None
    assert events.model(" m ", "", ["low", 3, ""], "", 1) == {"id": "m", "label": "m", "efforts": ["low"],
                                                             "effort": None, "default": False}
    assert claude.command(["claude"], stream=True, model="sonnet", effort="max", session="5e55-s") == [
        "claude", *claude.FLAGS, "--include-partial-messages", "--model", "sonnet", "--effort", "max",
        "--resume", "5e55-s"]
    assert claude.command(["claude"], model="default", effort="low") == ["claude", *claude.FLAGS, "--effort", "low"]


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
    # none chosen: the agent's default, nothing said
    _say(lab.owner, f.page, "Again.")
    assert runner.wait_idle(60)
    assert (_reply(lab, f.page)["model"], _reply(lab, f.page)["effort"]) == (None, None)
    resumed = [m["params"] for m in requests_in(disk.log) if m.get("method") == "thread/resume"][-1]
    assert "model" not in resumed and "effort" not in [m["params"] for m in requests_in(disk.log)
                                                       if m.get("method") == "turn/start"][-1]


def test_claude_code_runs_the_model_and_effort_asked_for(lab, disk):
    g = _folder(lab, disk, agent="claude")
    catalog.probe("claude", fresh=True)
    assert _say(lab.owner, g.page, model="haiku", effort="low", status=400)["detail"] == (
        "Claude Code does not offer that effort for this model.")
    _say(lab.owner, g.page, model="sonnet", effort="max")
    assert runner.wait_idle(60)
    argv = turns_in(disk.log)[-1]["argv"]
    assert argv[argv.index("--model") + 1] == "sonnet" and argv[argv.index("--effort") + 1] == "max"
    assert claude.STREAM_FLAG in argv


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
        # without stream: the whole message alone (a Send's)
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
    strip = lambda: lab.owner.get("/api/local-agents/chat", params={"page": f.page}).json()["computer"]  # noqa: E731
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
    assert lab.editor.get("/api/local-agents/chat/terminal", params={"page": f.page}).status_code == 404
    assert anon.get("/api/local-agents/chat/terminal", params={"page": f.page}).status_code == 401
    script = fresh_client()
    script.headers.update({"Authorization": f"Bearer {token_for(lab.owner, 'write', 'Script')}",
                           "X-Gamma-Workspace": lab.ws})
    assert script.get("/api/local-agents/chat/terminal", params={"page": f.page}).status_code == 403

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
    store.set_agent(f.id, "", by=lab.user)
    assert _terminal(lab.owner, f.page, 409)["detail"] == "No agent is on for this work folder on this computer."


def test_the_terminal_of_claude_code(lab, disk):
    f = _folder(lab, disk, agent="claude")
    _say(lab.owner, f.page)
    assert runner.wait_idle(60)
    assert _terminal(lab.owner, f.page)["argv"] == ARGV["claude"] + ["--resume", "5e55-fake-session"]
    lab.owner.post("/api/local-agents/chat/new", json={"page": f.page}).raise_for_status()
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
    r = lab.editor.post("/api/local-agents/chat/sources", json={"page": f.page, "selections": [chosen]})
    assert r.status_code == 404
    assert not turns_in(disk.log)  # no agent ran


# --- Background tasks -------------------------------------------------------------------------------------

def test_background_tasks_list_the_turns_that_work(lab, disk, monkeypatch):
    f = _folder(lab, disk)
    store.set_runs(f.id, "ask", by=lab.user)
    script_agent(monkeypatch, disk.log, ask=[{"command": "curl https://example.com", "host": "example.com"}])
    assert _rows(lab.owner) == []
    turn = _say(lab.owner, f.page)["turn"]["id"]
    _until(lambda: _reply(lab, f.page).get("asks"))
    [row] = _rows(lab.owner)
    with connect_pages_db(lab.ws) as conn:
        title = conn.execute("SELECT content FROM unified_blocks WHERE id = ?", (f.page,)).fetchone()[0]
    assert title and row == {
        "id": f"computer-{turn}", "kind": "computer-turn", "owner": lab.user, "workspace": lab.ws,
        "title": f"Codex · {title}", "params": {"page": f.page, "turn": turn, "agent": "codex", "label": "Codex",
                                                    "title": title},
        "state": "running", "progress": {"phase": "asking"}, "error": "", "created_at": row["created_at"],
        "started_at": row["started_at"], "finished_at": "", "artifact": None, "downloaded": False,
        "stoppable": True, "stopping": False, "readonly": True}
    assert row["created_at"] and row["started_at"] >= row["created_at"]
    assert _rows(lab.editor) == []  # the maker's alone
    _stop(lab.owner, turn)
    assert runner.wait_idle(30)
    assert _rows(lab.owner) == []

    # one that waits for its slot reads queued, and asks nothing
    store.set_runs(f.id, "never", by=lab.user)
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
