"""The routes of an agent on this computer (docs/dev/local_agents.md "The
routes"): turning one on for a work folder (``PATCH /api/folder-links/{id}``
``agent``), its gate checked in order; the links and a page's standing
carrying the folder's agent, the Feedback dialog's ``doc.agent`` and
``GET /api/local-agents``, none of which runs a process; a pause, a
relink and a removal stopping the run or forgetting the agent, an unpause
handing over what waits. The stand-in Codex (tests/fake_agents/) is the
only agent on this computer, and the vault is the sample paper
(test_local_agents_runner's fixtures)."""

import threading
from types import SimpleNamespace

import pytest

from conftest import account_of, fresh_client, login, make_user, token_for
from fake_agents import script_agent
from gamma import workspaces
from gamma.local_agents import catalog, procs, runner, store
from gamma.routers import folder_links as folder_links_api, local_agents as local_agents_api
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


def _links(c) -> dict:
    r = c.get("/api/folder-links")
    assert r.status_code == 200, r.text
    return {link["id"]: link for link in r.json()["links"]}


def _feedback(c, scope) -> dict:
    r = c.get("/api/feedback", params={"scope": scope})
    assert r.status_code == 200, r.text
    return r.json()


# --- turning an agent on ------------------------------------------------------------------

def test_the_patch_gate_in_order(lab, disk, monkeypatch):
    f = _folder(lab, disk, agent="")
    kept = _vault(lab.owner, disk.root / "kept", {"k.md": "k\n"}, push="off")
    monkeypatch.setenv("GAMMA_SHELL_SECRET", "the-secret")

    # the vault's maker first, whatever else would refuse it
    monkeypatch.setenv("GAMMA_LOCAL_AGENTS", "0")
    assert _patch(lab.editor, f.id, 403, agent="codex")["detail"] == folder_links_api.MAKER_ONLY
    assert _patch(lab.editor, f.id, 403, agent="")["detail"] == folder_links_api.MAKER_ONLY
    # then a server that runs agents
    assert _patch(lab.owner, f.id, 404, agent="codex")["detail"] == "Agents run on the desktop app's own server."
    monkeypatch.setenv("GAMMA_LOCAL_AGENTS", "1")
    # then a work folder
    assert _patch(lab.owner, kept["id"], 400, agent="codex")["detail"] == "Only a work folder can have an agent."
    # then, for a name, the shell's word, a known agent and one ready
    assert _patch(lab.owner, f.id, 403, agent="gpt")["detail"] == (
        "Only the desktop app may turn on an agent for this folder.")
    assert _patch(lab.owner, f.id, 400, SHELL, agent="gpt")["detail"] == "Gamma knows no agent named gpt."
    script_agent(monkeypatch, disk.log, fail="not_signed_in")
    assert _patch(lab.owner, f.id, 400, SHELL, agent="codex")["detail"] == catalog.NOT_SIGNED_IN["codex"]
    assert store.agent_of(f.id) is None

    # ready: on, the Sends waiting now handed to it, the maker's alone
    script_agent(monkeypatch, disk.log)
    sent = _send(lab.owner, f)
    other = _send(lab.editor, f)
    on = _patch(lab.owner, f.id, 200, SHELL, agent="codex")
    assert on["queued"] == 1 and on["agent"]["name"] == "codex" and on["agent"]["label"] == "Codex"
    assert (store.agent_of(f.id)["agent"], store.agent_of(f.id)["set_by"]) == ("codex", lab.user)
    assert runner.wait_idle(60)
    assert _request(lab, sent["id"])["state"] == "done" and _request(lab, other["id"])["state"] == "sent"

    # the same again takes nothing more; changing it stops a run and hands over what waits
    assert _patch(lab.owner, f.id, 200, SHELL, agent="codex")["queued"] == 0
    stopped: list = []
    monkeypatch.setattr(runner, "stop_link", stopped.append)
    assert _patch(lab.owner, f.id, 200, SHELL, agent="claude")["agent"]["label"] == "Claude Code"
    assert stopped == [f.id]

    # off: the maker's alone, without the shell's word
    off = _patch(lab.owner, f.id, 200, agent="")
    assert off["agent"] is None and off["queued"] == 0 and store.agent_of(f.id) is None
    assert stopped == [f.id, f.id]
    assert runner.wait_idle(30)


def test_a_pause_stops_the_run_and_an_unpause_hands_over_what_waits(lab, disk, monkeypatch):
    f = _folder(lab, disk)
    stopped, queued = [], []
    monkeypatch.setattr(runner, "stop_link", stopped.append)
    monkeypatch.setattr(runner, "queue_folder", lambda ws, link_id: queued.append((ws, link_id)) or 0)
    _patch(lab.owner, f.id, paused=True)
    assert stopped == [f.id] and not queued
    _patch(lab.owner, f.id, paused=False)
    assert stopped == [f.id] and queued == [(lab.ws, f.id)]

    # a vault whose push comes down (every file of it in the library) is no work folder: its run stops
    shelf = _vault(lab.owner, disk.root / "shelf", {"s.md": "s\n"}, library=True)
    store.set_agent(shelf["id"], "codex", by=lab.user)
    _patch(lab.owner, shelf["id"], push="pdfs")
    assert stopped == [f.id, shelf["id"]]


def test_a_relink_and_a_removal_forget_the_agent(lab, disk, monkeypatch):
    f = _folder(lab, disk)
    stopped: list = []
    monkeypatch.setattr(runner, "stop_link", stopped.append)
    r = lab.owner.post(f"/api/folder-links/{f.id}/relink", json={"path": str(f.root)})
    assert r.status_code == 200, r.text
    assert r.json()["agent"] is None and store.agent_of(f.id) is None and stopped == [f.id]

    store.set_agent(f.id, "codex", by=lab.user)
    r = lab.owner.delete(f"/api/folder-links/{f.id}")
    assert r.status_code == 200, r.text
    assert store.agent_of(f.id) is None and stopped == [f.id, f.id]


# --- the read paths: nothing runs ------------------------------------------------------------

def test_the_links_and_a_page_carry_the_agent_without_running_anything(lab, disk, monkeypatch):
    f = _folder(lab, disk)
    second = SimpleNamespace(id=_vault(lab.owner, disk.root / "second", {"s.md": "s\n"})["id"])
    store.set_agent(second.id, "claude", by=lab.user)
    kept =_vault(lab.owner, disk.root / "kept", {"k.md": "k\n"}, push="off")
    _known_ready()
    started = _no_process(monkeypatch)
    asked = []
    agents_of = store.agents_of
    monkeypatch.setattr(store, "agents_of", lambda ids: asked.append(sorted(ids)) or agents_of(ids))

    links = _links(lab.owner)
    assert len(asked) == 1  # one query for the whole listing
    assert links[f.id]["agent"] == {"name": "codex", "label": "Codex", "ready": True, "problem": "", "running": None,
                                    "runs": "never"}
    assert links[second.id]["agent"]["label"] == "Claude Code"
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


def test_the_feedback_dialog_reads_the_folders_agent(lab, disk, monkeypatch):
    f = _folder(lab, disk, agent="")
    _known_ready()
    started = _no_process(monkeypatch)

    doc = _feedback(lab.owner, f.w)
    assert doc["agent"] is None and doc["agents_found"] is True
    store.set_agent(f.id, "codex", by=lab.user)
    assert _feedback(lab.owner, f.w)["agent"] == {"name": "codex", "label": "Codex", "ready": True, "problem": "",
                                                  "takes": True, "why": ""}
    other = _feedback(lab.editor, f.w)["agent"]
    assert (other["takes"], other["why"]) == (
        False, "Only the person who keeps this folder on this computer can send to Codex.")
    _patch(lab.owner, f.id, paused=True)
    paused = _feedback(lab.owner, f.w)["agent"]
    assert (paused["takes"], paused["why"]) == (
        False, "The work folder is paused on this computer: Codex starts once it syncs again.")
    _patch(lab.owner, f.id, paused=False)

    # never in the Markdown, nor on a server that runs no agents
    r = lab.owner.get("/api/feedback", params={"scope": f.w, "format": "md"})
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
    r = lab.owner.get("/api/local-agents")
    assert r.status_code == 200, r.text
    agents = r.json()["agents"]
    assert [(a["name"], a["label"], a["found"], a["ready"], a["problem"]) for a in agents] == [
        ("codex", "Codex", True, True, ""), ("claude", "Claude Code", True, True, "")]
    assert not calls

    # fresh: asked anew, at most once every ten seconds on this server
    script_agent(monkeypatch, disk.log, fail="not_signed_in")
    fresh = lab.owner.get("/api/local-agents", params={"fresh": 1}).json()["agents"]
    assert [a["problem"] for a in fresh] == [catalog.NOT_SIGNED_IN["codex"], catalog.NOT_SIGNED_IN["claude"]]
    asked = len(calls)
    assert asked >= 4
    again = lab.owner.get("/api/local-agents", params={"fresh": 1}).json()["agents"]
    assert again == fresh and len(calls) == asked

    monkeypatch.setenv("GAMMA_LOCAL_AGENTS", "0")
    r = lab.owner.get("/api/local-agents")
    assert r.status_code == 404 and r.json()["detail"] == "Agents run on the desktop app's own server."


def test_the_waiting_sends_the_panel_counts(lab, disk):
    f = _folder(lab, disk, agent="")
    mine = _send(lab.owner, f)
    _send(lab.editor, f)  # another member's: never the agent's
    r = lab.owner.get("/api/local-agents/waiting", params={"link": f.id})
    assert r.status_code == 200 and r.json() == {"waiting": 1}
    assert lab.editor.get("/api/local-agents/waiting", params={"link": f.id}).status_code == 404
    assert lab.owner.get("/api/local-agents/waiting", params={"link": "noSuchLink"}).status_code == 404
    assert runner.waiting(lab.ws, f.link) == [mine["id"]]


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
