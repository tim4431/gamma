"""The routes of an agent on this computer (docs/dev/local_agents.md "The
routes"): turning one on for the computer and what it may run (``PATCH
/api/local-agents/{agent}``), its gate checked in order, and the
directories offered (``PUT /api/local-agents/dirs``); the links and a
page's standing carrying the agent that works in a vault, the Feedback
dialog's ``doc.agent`` and ``GET /api/local-agents``, none of which runs a
process; a pause and a relink stopping the run, an unpause handing over
what waits. The stand-in Codex (tests/fake_agents/) is the only agent on
this computer, and the vault is the sample paper
(test_local_agents_runner's fixtures)."""

import threading
from types import SimpleNamespace

import pytest

from conftest import account_of, fresh_client, login, make_user, token_for
from fake_agents import script_agent
from gamma import folder_links, workspaces
from gamma.local_agents import catalog, procs, runner, store
from gamma.routers import local_agents as local_agents_api
from test_local_agents_runner import (_folder, _request, _send, _turn_started, _withdraw,  # noqa: F401: the disk fixture
                                      disk)
from test_vault_routes import _vault

OWNER, EDITOR, VIEWER = "lagr_owner", "lagr_editor", "lagr_viewer"
SHELL = {"X-Gamma-Shell": "the-secret"}


@pytest.fixture(scope="module")
def lab(client):
    """A shared workspace of lagr_owner's that lagr_editor edits and
    lagr_viewer reads."""
    for name in (OWNER, EDITOR, VIEWER):
        make_user(name, f"pw-{name}")
    owner, editor, viewer = (login(name, f"pw-{name}") for name in (OWNER, EDITOR, VIEWER))
    ws = workspaces.create("Agents routes", account_of(OWNER), kind="shared")["id"]
    workspaces.set_member(ws, account_of(EDITOR), "editor", by=account_of(OWNER))
    workspaces.set_member(ws, account_of(VIEWER), "viewer", by=account_of(OWNER))
    for c in (owner, editor, viewer):
        c.headers["X-Gamma-Workspace"] = ws
    return SimpleNamespace(ws=ws, owner=owner, editor=editor, viewer=viewer, user=account_of(OWNER))


def _no_process(monkeypatch) -> list[str]:
    """Every process an agent's probe or turn would start from now on
    refused, recorded by the name of the thread that asked: a read path
    asks none on its own."""
    asked: list[str] = []

    def refused(*args, **kw):
        asked.append(threading.current_thread().name)
        raise AssertionError("a read path started a process")

    monkeypatch.setattr(procs, "run_quick", refused)
    monkeypatch.setattr(procs, "spawn", refused)
    return asked


def _known_ready():
    """Both agents asked once (the stand-ins, signed in): what the read
    paths show from then on, for ``PROBE_TTL_S``."""
    assert catalog.probe("codex", fresh=True)["ready"]
    assert catalog.probe("claude", fresh=True)["ready"]


def _patch(c, link_id, status=200, headers=None, **body):
    r = c.patch(f"/api/folder-links/{link_id}", json=body, headers=headers or {})
    assert r.status_code == status, r.text
    return r.json()


def _agent(c, name, status=200, headers=None, **body):
    r = c.patch(f"/api/local-agents/{name}", json=body, headers=headers or {})
    assert r.status_code == status, r.text
    return r.json()


def _links(c) -> dict:
    r = c.get("/api/folder-links")
    assert r.status_code == 200, r.text
    return {link["id"]: link for link in r.json()["links"]}


def _feedback(c, scope) -> dict:
    r = c.get("/api/annotations", params={"scope": scope})
    assert r.status_code == 200, r.text
    return r.json()


# --- turning an agent on ------------------------------------------------------------------

def test_the_agent_gate_in_order(lab, disk, monkeypatch):
    f = _folder(lab, disk, agent="")
    monkeypatch.setenv("GAMMA_SHELL_SECRET", "the-secret")

    # a server that runs agents, an agent Gamma knows, a known value of what it may run
    monkeypatch.setenv("GAMMA_LOCAL_AGENTS", "0")
    assert _agent(lab.owner, "codex", 404, enabled=True)["detail"] == "Agents run on the desktop app's own server."
    monkeypatch.setenv("GAMMA_LOCAL_AGENTS", "1")
    assert _agent(lab.owner, "gpt", 404, SHELL, enabled=True)["detail"] == "Gamma knows no agent named gpt."
    assert _agent(lab.owner, "codex", 400, SHELL, runs="always")["detail"] == "What an agent may run is never or ask."
    # then, turning it on, the shell's word and an agent ready
    assert _agent(lab.owner, "codex", 403, enabled=True)["detail"] == (
        "Only the desktop app may turn on an agent on this computer.")
    script_agent(monkeypatch, disk.log, fail="not_signed_in")
    assert _agent(lab.owner, "codex", 400, SHELL, enabled=True)["detail"] == catalog.NOT_SIGNED_IN["codex"]
    assert store.agents() == {}

    # ready: on, its owner the account that turned it on, the Sends waiting now handed to it
    script_agent(monkeypatch, disk.log)
    sent = _send(lab.owner, f)
    other = _send(lab.editor, f)
    on = _agent(lab.owner, "codex", 200, SHELL, enabled=True)
    assert (on["name"], on["enabled"], on["runs"], on["mine"], on["queued"]) == ("codex", True, "never", True, 1)
    assert store.agent_for(lab.user)["agent"] == "codex"
    assert runner.wait_idle(60)
    assert _request(lab, sent["id"])["state"] == "done" and _request(lab, other["id"])["state"] == "sent"
    assert _agent(lab.owner, "codex", 200, SHELL, enabled=True)["queued"] == 0   # on already: nothing more

    # another account's agent: not that one's to take over, turn off without the shell's word, or narrow
    assert _agent(lab.editor, "codex", 409, SHELL, enabled=True)["detail"] == (
        "Codex was turned on by another account on this computer.")
    assert _agent(lab.editor, "codex", 403, enabled=False)["detail"] == (
        "Only the account that turned on Codex, or the desktop app, may turn it off.")
    assert _agent(lab.editor, "codex", 403, runs="never")["detail"].startswith("Only the account that turned on Codex")
    assert lab.editor.get("/api/local-agents").json()["agents"][0]["mine"] is False

    # what it may run: ask takes the shell's word, never its owner alone; neither for an agent that is off
    assert _agent(lab.owner, "codex", 403, runs="ask")["detail"] == (
        "Only the desktop app may let an agent ask to run commands on this computer.")
    assert _agent(lab.owner, "codex", 200, SHELL, runs="ask")["runs"] == "ask"
    never: list = []
    monkeypatch.setattr(runner, "runs_never", never.append)
    assert _agent(lab.owner, "codex", 200, runs="never")["runs"] == "never" and never == ["codex"]
    assert _agent(lab.owner, "claude", 400, SHELL, runs="ask")["detail"] == "Turn Claude Code on first."

    # off: its owner's alone, without the shell's word; its runs stop and the pages hear it
    stopped, told = [], []
    monkeypatch.setattr(runner, "stop_agent", stopped.append)
    monkeypatch.setattr(folder_links, "agents_changed", lambda: told.append(True))
    off = _agent(lab.owner, "codex", 200, enabled=False)
    assert (off["enabled"], off["runs"], off["queued"]) == (False, "never", 0) and store.agent_for(lab.user) is None
    assert stopped == ["codex"] and told == [True]
    assert _agent(lab.owner, "codex", 200, enabled=False) and stopped == ["codex"]   # off already: nothing more
    assert runner.wait_idle(30)


def test_the_shell_turns_off_an_agent_whoever_owns_it(lab, disk, monkeypatch):
    """The person at the computer turns off an agent whose owner is signed
    out of the app or gone: with the shell's word, never unchecked."""
    _known_ready()
    stopped: list = []
    monkeypatch.setattr(runner, "stop_agent", stopped.append)
    store.set_agent("codex", True, by=lab.user)
    # a server started with no secret has no shell to say it: the owner alone
    monkeypatch.delenv("GAMMA_SHELL_SECRET", raising=False)
    assert _agent(lab.editor, "codex", 403, SHELL, enabled=False)["detail"] == (
        "Only the account that turned on Codex, or the desktop app, may turn it off.")
    monkeypatch.setenv("GAMMA_SHELL_SECRET", "the-secret")
    assert _agent(lab.editor, "codex", 403, enabled=False)
    off = _agent(lab.editor, "codex", 200, SHELL, enabled=False)
    assert (off["enabled"], off["runs"]) == (False, "never") and stopped == ["codex"]
    assert store.agent_for(lab.user) is None


def test_an_account_deleted_turns_its_agents_off(lab, disk, monkeypatch):
    stopped: list = []
    monkeypatch.setattr(runner, "stop_agent", stopped.append)
    make_user("lagr_leaving", "pw-lagr_leaving")
    leaving = account_of("lagr_leaving")
    store.set_agent("claude", True, by=leaving)
    store.set_agent("codex", True, by=lab.user)
    workspaces.delete_account(leaving, release_now=True)
    assert not store.agents()["claude"]["enabled"] and stopped == ["claude"]
    assert store.agent_for(lab.user)["agent"] == "codex"                   # another account's stays on
    store.set_agent("codex", False, by=lab.user)


def test_the_directories_offered(lab, disk, monkeypatch):
    f = _folder(lab, disk)                                   # the owner's Codex on, a vault of the paper
    monkeypatch.setenv("GAMMA_SHELL_SECRET", "the-secret")
    a, b = disk.tmp / "offered-a", disk.tmp / "offered-b"
    a.mkdir()
    b.mkdir()

    def put(c, entries, status=200, headers=None):
        r = c.put("/api/local-agents/dirs", json=entries, headers=headers or {})
        assert r.status_code == status, r.text
        return r.json()

    # naming a path takes the shell's word; a place no vault may have, or none, is refused
    assert put(lab.owner, [{"path": str(a)}], 403)["detail"] == "Only the desktop app itself may name a path on this computer."
    assert put(lab.owner, [{"path": "relative/dir"}], 400, SHELL)["detail"] == "Give the full path of a directory on this computer."
    assert "is no directory" in put(lab.owner, [{"path": str(disk.tmp / "missing")}], 400, SHELL)["detail"]
    dirs = put(lab.owner, [{"path": str(a)}, {"path": str(b), "name": "Lab notes"}], 200, SHELL)["dirs"]
    assert [(d["path"], d["name"]) for d in dirs] == [(str(a), "offered-a"), (str(b), "Lab notes")]
    assert lab.owner.get("/api/local-agents").json()["dirs"] == dirs

    # an account with no agent on here changes nothing without the word, and stops no turn there
    stopped: list = []
    monkeypatch.setattr(runner, "stop_dirs", stopped.append)
    assert put(lab.editor, [], 403)["detail"] == (
        "Only an account with an agent on this computer, or the desktop app, may change the directories it offers.")
    assert stopped == [] and lab.owner.get("/api/local-agents").json()["dirs"] == dirs
    # leaving one out or renaming one names no path: a page of an account with an agent on may, without the word
    kept = put(lab.owner, [{"id": dirs[1]["id"], "path": str(b), "name": "Notes"}])["dirs"]
    assert [(d["id"], d["name"]) for d in kept] == [(dirs[1]["id"], "Notes")] and stopped == [[str(a)]]
    # an id with another path is a new path
    assert put(lab.owner, [{"id": dirs[1]["id"], "path": str(a)}], 403)
    assert put(lab.owner, [{"path": str(a)}, {"path": str(a)}], 400, SHELL)["detail"] == "A directory is offered once."
    # one place, one queue: no new path inside or around a vault here, or another one offered
    for place in (f.root.parent, f.root, f.root / "sections"):
        assert "is inside or around the synced folder" in put(lab.owner, [{"path": str(place)}], 400, SHELL)["detail"]
    (a / "inner").mkdir()
    assert put(lab.owner, [{"path": str(a)}, {"path": str(a / "inner")}], 400, SHELL)["detail"] == (
        f"{a} is inside or around {a / 'inner'}, also offered.")
    # and no vault inside or around a directory offered
    put(lab.owner, [{"path": str(a)}], 200, SHELL)
    r = lab.owner.post("/api/folder-links", json={"path": str(a / "inner"), "new_folder": True, "push": "everything"},
                       headers=SHELL)
    assert r.status_code == 400 and "is inside or around a directory offered to agents" in r.json()["detail"], r.text
    assert put(lab.owner, [])["dirs"] == []
    # a synced folder detached is no vault here: its directory may be offered
    r = lab.owner.post(f"/api/folders/{f.w}/detach", json={"pages": "keep"})
    assert r.status_code == 200, r.text
    assert [d["path"] for d in put(lab.owner, [{"path": str(f.root)}], 200, SHELL)["dirs"]] == [str(f.root)]
    assert put(lab.owner, [])["dirs"] == []

    monkeypatch.setenv("GAMMA_LOCAL_AGENTS", "0")
    assert put(lab.owner, [], 404)["detail"] == "Agents run on the desktop app's own server."


def test_a_pause_stops_the_run_and_an_unpause_hands_over_what_waits(lab, disk, monkeypatch):
    f = _folder(lab, disk)
    stopped, queued = [], []
    monkeypatch.setattr(runner, "stop_link", lambda link_id, why="": stopped.append(link_id))
    monkeypatch.setattr(runner, "queue_folder", lambda ws, link_id: queued.append((ws, link_id)) or 0)
    _patch(lab.owner, f.id, paused=True)
    assert stopped == [f.id] and not queued
    _patch(lab.owner, f.id, paused=False)
    assert stopped == [f.id] and queued == [(lab.ws, f.id)]

    # a vault whose push comes down (every file of it in the library) is no work folder: its run stops
    shelf = _vault(lab.owner, disk.root / "shelf", {"s.md": "s\n"}, library=True)
    _patch(lab.owner, shelf["id"], push="pdfs")
    assert stopped == [f.id, shelf["id"]]

    # with no agent on, an unpause hands nothing over
    store.set_agent("codex", False, by=lab.user)
    _patch(lab.owner, f.id, paused=True)
    _patch(lab.owner, f.id, paused=False)
    assert queued == [(lab.ws, f.id)]


def test_a_relink_and_a_removal_stop_the_run_and_keep_the_agent(lab, disk, monkeypatch):
    f = _folder(lab, disk)
    stopped: list = []
    monkeypatch.setattr(runner, "stop_link", lambda link_id, why="": stopped.append((link_id, why)))
    r = lab.owner.post(f"/api/folder-links/{f.id}/relink", json={"path": str(f.root)})
    assert r.status_code == 200, r.text
    assert r.json()["agent"]["name"] == "codex" and stopped == [(f.id, "")]

    r = lab.owner.delete(f"/api/folder-links/{f.id}")
    assert r.status_code == 200, r.text
    assert stopped == [(f.id, ""), (f.id, "removed")] and store.agent_for(lab.user)["agent"] == "codex"

    # a Detach of a synced folder removes its vault so too
    second = _vault(lab.owner, disk.root / "second", {"s.md": "s\n"})
    r = lab.owner.post(f"/api/folders/{second['folder_id']}/detach", json={"pages": "keep"})
    assert r.status_code == 200, r.text
    assert stopped[2:] == [(second["id"], "removed")] and store.agent_for(lab.user)["agent"] == "codex"


# --- the read paths: nothing runs ------------------------------------------------------------

def test_the_links_and_a_page_carry_the_agent_without_running_anything(lab, disk, monkeypatch):
    f = _folder(lab, disk)
    store.set_agent("claude", True, by=lab.user)
    second = SimpleNamespace(id=_vault(lab.owner, disk.root / "second", {"s.md": "s\n"})["id"])
    kept =_vault(lab.owner, disk.root / "kept", {"k.md": "k\n"}, push="off")
    _known_ready()
    started = _no_process(monkeypatch)
    asked = []
    agents = store.agents
    monkeypatch.setattr(store, "agents", lambda: asked.append(True) or agents())

    links = _links(lab.owner)
    assert len(asked) == 1  # one query for the whole listing
    # the agent of the vault's maker that works there: its first on, Codex before Claude Code
    assert links[f.id]["agent"] == {"name": "codex", "label": "Codex", "ready": True, "problem": "", "running": None}
    assert links[second.id]["agent"]["label"] == "Codex"
    assert links[kept["id"]]["agent"] is None
    assert _links(lab.viewer)[f.id]["agent"]["name"] == "codex"

    vault = lab.owner.get(f"/api/folder-links/page/{f.page}").json()["vault"]
    assert vault["agent"] == links[f.id]["agent"]
    assert lab.owner.get(f"/api/folder-links/{f.id}").json()["agent"] == links[f.id]["agent"]

    # an agent not asked yet reads "checking", asked in the background alone
    catalog.forget()
    assert _links(lab.owner)[f.id]["agent"]["ready"] is None
    assert all(name.startswith("agent-probe-") for name in started), started

    # a server that runs no agents says none
    monkeypatch.setenv("GAMMA_LOCAL_AGENTS", "0")
    assert _links(lab.owner)[f.id]["agent"] is None
    assert "agent" not in lab.owner.get(f"/api/folder-links/page/{f.page}").json()["vault"]


def test_a_run_shows_on_the_links_its_detail_to_who_may_act(lab, disk, monkeypatch):
    script_agent(monkeypatch, disk.log, delay=30)
    f = _folder(lab, disk)
    sent = _send(lab.owner, f)
    _turn_started(lab, sent["id"])
    running = _links(lab.owner)[f.id]["agent"]["running"]
    assert running["request_id"] == sent["id"] and running["detail"].startswith("Reading 2 comments")
    assert _links(lab.editor)[f.id]["agent"]["running"]["detail"] == running["detail"]
    assert _links(lab.viewer)[f.id]["agent"]["running"] == {"kind": "send", "request_id": sent["id"], "detail": ""}
    page = lab.viewer.get(f"/api/folder-links/page/{f.page}").json()["vault"]
    assert page["agent"]["running"] == {"kind": "send", "request_id": sent["id"], "detail": ""}
    _withdraw(lab, sent["id"])
    assert runner.wait_idle(30)
    assert _links(lab.owner)[f.id]["agent"]["running"] is None


def test_the_feedback_dialog_reads_the_persons_agent(lab, disk, monkeypatch):
    f = _folder(lab, disk, agent="")
    _known_ready()
    started = _no_process(monkeypatch)

    doc = _feedback(lab.owner, f.w)
    assert doc["agent"] is None and doc["agents_found"] is True
    store.set_agent("codex", True, by=lab.user)
    assert _feedback(lab.owner, f.w)["agent"] == {"name": "codex", "label": "Codex", "ready": True, "problem": "",
                                                  "takes": True, "why": ""}
    other = _feedback(lab.editor, f.w)["agent"]
    assert (other["takes"], other["why"]) == (
        False, "Only the person who turned on Codex on this computer can send to it.")
    _patch(lab.owner, f.id, paused=True)
    paused = _feedback(lab.owner, f.w)["agent"]
    assert (paused["takes"], paused["why"]) == (
        False, "The synced folder is paused on this computer: Codex starts once it syncs again.")
    _patch(lab.owner, f.id, paused=False)

    # never in the Markdown, nor on a server that runs no agents
    r = lab.owner.get("/api/annotations", params={"scope": f.w, "format": "md"})
    assert r.status_code == 200 and "Codex" not in r.text
    monkeypatch.setenv("GAMMA_LOCAL_AGENTS", "0")
    assert "agent" not in _feedback(lab.owner, f.w) and "agents_found" not in _feedback(lab.owner, f.w)
    assert not started
    monkeypatch.setenv("GAMMA_LOCAL_AGENTS", "1")
    assert runner.wait_idle(30)


# --- the agents on this computer ----------------------------------------------------------

def test_the_agents_on_this_computer(lab, disk, monkeypatch):
    monkeypatch.setattr(local_agents_api, "_fresh_at", 0.0)
    script = fresh_client()
    script.headers["Authorization"] = f"Bearer {token_for(lab.owner, 'write', 'Script')}"
    assert script.get("/api/local-agents").status_code == 403
    assert fresh_client().get("/api/local-agents").status_code == 401

    _known_ready()
    calls = []
    run_quick = procs.run_quick
    monkeypatch.setattr(procs, "run_quick", lambda *a, **kw: calls.append(a) or run_quick(*a, **kw))
    store.set_agent("claude", True, by=lab.user)
    store.set_runs("claude", "ask", by=lab.user)
    r = lab.owner.get("/api/local-agents")
    assert r.status_code == 200, r.text
    agents = r.json()["agents"]
    assert [(a["name"], a["label"], a["found"], a["ready"], a["problem"], a["code"], a["enabled"], a["runs"],
             a["mine"]) for a in agents] == [("codex", "Codex", True, True, "", "", False, "never", False),
                                             ("claude", "Claude Code", True, True, "", "", True, "ask", True)]
    assert r.json()["here"] is True and r.json()["computer"] and r.json()["dirs"] == [] and not calls

    # fresh: asked anew, at most once every ten seconds on this server
    script_agent(monkeypatch, disk.log, fail="not_signed_in")
    fresh = lab.owner.get("/api/local-agents", params={"fresh": 1}).json()["agents"]
    assert [a["problem"] for a in fresh] == [catalog.NOT_SIGNED_IN["codex"], catalog.NOT_SIGNED_IN["claude"]]
    assert [a["code"] for a in fresh] == ["signed_out", "signed_out"]
    assert [a["enabled"] for a in fresh] == [False, True]
    asked = len(calls)
    assert asked >= 4
    again = lab.owner.get("/api/local-agents", params={"fresh": 1}).json()["agents"]
    assert again == fresh and len(calls) == asked

    # a server that runs no agents says so, and lists none; changing one is 404 there
    monkeypatch.setenv("GAMMA_LOCAL_AGENTS", "0")
    r = lab.owner.get("/api/local-agents")
    assert r.status_code == 200 and r.json() == {"here": False, "computer": "", "agents": [], "dirs": []}
    r = lab.owner.patch("/api/local-agents/codex", json={"enabled": False})
    assert r.status_code == 404 and r.json()["detail"] == "Agents run on the desktop app's own server."


def test_a_request_says_when_an_agent_here_holds_it(lab, disk, monkeypatch):
    script_agent(monkeypatch, disk.log, delay=30)
    f = _folder(lab, disk)
    sent = _send(lab.owner, f)
    _turn_started(lab, sent["id"])
    listed = {r["id"]: r for r in lab.owner.get("/api/feedback/requests", params={"scope": f.w}).json()["requests"]}
    assert listed[sent["id"]]["local"] is True
    assert lab.viewer.get(f"/api/feedback/requests/{sent['id']}").json()["request"]["local"] is True
    _withdraw(lab, sent["id"])
    assert runner.wait_idle(30)
    listed = {r["id"]: r for r in lab.owner.get("/api/feedback/requests", params={"scope": f.w}).json()["requests"]}
    assert listed[sent["id"]]["local"] is False
