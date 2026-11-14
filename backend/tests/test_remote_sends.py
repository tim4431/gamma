"""Sends and mentions of another server's work folder run on this computer
(step 4c of docs/research/agent-collab.md): the computer's side
(gamma/local_agents/home.py ``HttpHome``, runner.py ``queue_remote`` and
``withdraw_remote``, computers/channel.py ``_sends`` and the announce's
``agent``, feedback/sources.py ``source_in_vault``, the panel's gate in
routers/folder_links.py) against that server's channel routes
(gamma/routers/computers.py).

One process plays both: the NAS is a TestClient of its own reached through
``gamma_sync.default_open``, the computer this server as the desktop app runs
it, with the stand-in Codex (tests/fake_agents/). This computer's own
in-process reads and writes of the NAS's workspace fail (``far``), so a run
that read the wrong pages.db fails though both share one data directory, and
the local runner (which hears every request written in this process) never
takes the NAS's Send itself.

The binding announced and kept; the maker's Send run here and answered there
(replies signed Codex as an agent on a computer, the fixed threads resolved,
the request done with its summary and the files changed, the source lines
computed here from the PDF and the sources on this disk, the region pictures
fetched); a Send made while the computer is away taken when it comes back;
a token's Send refused there and another account's never run; a withdraw
stopping the run, never overwritten; a claim retried (the same run told it
holds it, another refused) and answers retried writing nothing twice; an
``@codex`` mention made there run here; recovery failing an open run there,
and the channel's next announce one left open while that server was away (a
claim never answered too); a paused vault pushing nothing; Stop showing
turning the agents there off; the routes' refusals; the panel's gate (shown
there first, asking refused).
The local path is the local agents' tests' (test_local_agents_*.py)."""

import io
import json
import os
import re
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from conftest import account_of, fresh_client, login, make_folder, make_user, thread_on
from fake_agents import requests_in, script_agent, settle_probes, use_fakes
from gamma import folder_links, gamma_sync, pdf_meta, storage, workspaces
from gamma.blocks_store import new_block_id
from gamma.computers import channel, registry
from gamma.db import connect_pages_db, connect_users_db, connect_vaults_db
from gamma.feedback import files as feedback_files, requests, sources
from gamma.integrations import create_token
from gamma.local_agents import catalog, codex, events, home, info, procs, runner, store
from gamma.ops import commit_ops
from gamma.vault import kinds, store as vault_store
from gamma.work_folders import selection
from test_folder_links import _sync
from test_highlight_relocate import H, W, _position
from test_vault_sync import insert

OWNER, EDITOR = "rsn_owner", "rsn_editor"
PASSWORD = "rsn-pass-1234"
SECRET = "rsn-shell-secret"
SHELL = {"X-Gamma-Shell": SECRET}
PAPER = Path(__file__).parent / "samples" / "synctex" / "paper"
PLACES = json.loads((PAPER / "places.json").read_text(encoding="utf-8"))
SOURCES = ("main.pdf", "main.synctex.gz", "main.tex", "sections/method.tex", "figures/fig1.pdf")


@pytest.fixture(scope="module")
def lab(client):
    """A shared workspace of rsn_owner's on the NAS that rsn_editor edits."""
    for name in (OWNER, EDITOR):
        make_user(name, PASSWORD)
    owner, editor = login(OWNER, PASSWORD), login(EDITOR, PASSWORD)
    ws = workspaces.create("RS lab", account_of(OWNER), kind="shared")["id"]
    workspaces.set_member(ws, account_of(EDITOR), "editor", by=account_of(OWNER))
    for c in (owner, editor):
        c.headers["X-Gamma-Workspace"] = ws
    return SimpleNamespace(ws=ws, owner=owner, editor=editor, user=account_of(OWNER))


@pytest.fixture
def nas(lab, tmp_path, monkeypatch):
    """This server as the desktop app runs it (agents on, the shell's secret
    set, the stand-in Codex), the NAS a TestClient reached through
    ``gamma_sync.default_open`` (``down`` makes it unreachable), polls held
    no time, no channel thread unless a test runs a turn, and this
    computer's in-process reads of the NAS's workspace refused (``far``)."""
    monkeypatch.setenv("GAMMA_FOLDER_LINKS", "1")
    monkeypatch.setenv("GAMMA_LOCAL_AGENTS", "1")
    monkeypatch.setenv("GAMMA_SHELL_SECRET", SECRET)
    pushed: list = []
    monkeypatch.setattr(folder_links, "run_in_background", lambda link_id, **kw: pushed.append((link_id, kw)))
    monkeypatch.setattr(folder_links, "due", lambda link: "")
    monkeypatch.setattr(folder_links, "HOT_S", 3600)
    monkeypatch.setattr(kinds, "SETTLE_NOTE_S", 0)
    monkeypatch.setattr(gamma_sync, "SETTLE_S", 0)
    monkeypatch.setattr(vault_store, "MISSING_S", 0)
    monkeypatch.setattr(pdf_meta, "schedule", lambda ws, doc_id: None)
    monkeypatch.setattr(storage, "linked_moved", [])
    monkeypatch.setattr(storage, "LINKED_STAT_S", 0)
    monkeypatch.setattr(storage, "_stats", {})
    monkeypatch.setattr(storage, "_offline", {})
    monkeypatch.setattr(codex, "CHECK_SANDBOX", False)
    monkeypatch.setattr(events, "STOP_GRACE_S", 1.0)
    monkeypatch.setattr(runner, "DETAIL_EVERY_S", 0)
    monkeypatch.setattr(channel, "POLL_S", 0)
    monkeypatch.setattr(channel, "start", lambda link_id: None)
    monkeypatch.setattr(home, "RETRY_S", 0)
    http, down = fresh_client(), []

    def open_(method, path, headers, body=None):
        if down:
            raise gamma_sync.Unreachable("cannot reach http://testserver: refused")
        content = body if body is None or isinstance(body, bytes) else b"".join(body)
        r = http.request(method, path, headers=headers, content=content)
        return r.status_code, r.headers, io.BytesIO(r.content)

    monkeypatch.setattr(gamma_sync, "default_open", open_)

    def guarded(fn):
        def call(at, *args, **kwargs):
            assert at != lab.ws, "this computer read or wrote the other server's workspace in-process"
            return fn(at, *args, **kwargs)
        return call

    for module in (folder_links, selection, feedback_files, runner, home, info):
        monkeypatch.setattr(module, "connect_pages_db", guarded(module.connect_pages_db))
    monkeypatch.setattr(folder_links, "commit_ops", guarded(folder_links.commit_ops))
    runner.register()
    use_fakes(monkeypatch, tmp_path / "agent.log")
    catalog.forget()
    runner.reset()
    registry.reset()
    yield SimpleNamespace(url="http://testserver", root=tmp_path / "disk", log=tmp_path / "agent.log", down=down,
                          pushed=pushed, http=http)
    runner.reset()
    settle_probes()
    catalog.forget()
    left = list(procs._live)
    procs.stop_all(timeout=1)
    channel.stop_all()
    registry.reset()
    for link in folder_links.all_links():
        if folder_links.inside(link["path"], str(tmp_path)):
            folder_links._forget(link)
    with connect_vaults_db() as conn:
        for table in ("agent_runs", "agent_folders", "agent_threads", "agent_policies"):
            conn.execute(f"DELETE FROM {table}")
    with connect_users_db() as conn:
        for user in (account_of(OWNER), account_of(EDITOR)):
            conn.execute("DELETE FROM integration_tokens WHERE user_id = ?", (user,))
            conn.execute("DELETE FROM computers WHERE user_id = ?", (user,))
            conn.execute("DELETE FROM computer_links WHERE created_by = ?", (user,))
    with feedback_files._guard:
        feedback_files._pages.clear()
        feedback_files._folders.clear()
    assert not left, f"children left running: {[c.proc.pid for c in left]}"


# --- the helpers ---------------------------------------------------------------------------------

def _put(path: Path, data: bytes, age: float = 10) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    then = time.time() - age
    os.utime(path, (then, then))


def _rects(fractions) -> list:
    return [{"x1": x0 * W, "y1": y0 * H, "x2": x1 * W, "y2": y1 * H} for x0, y0, x1, y1 in fractions]


def _made(lab, nas, name: str, *, agent: str = "codex", shown: bool = True) -> SimpleNamespace:
    """The paper as a work folder of the NAS made from a directory here, its
    files chosen and pushed there; this computer shown there (its channel,
    ``turn`` a turn of it) and ``agent`` turned on with the shell's word; a
    passage and the figure commented on there."""
    w = make_folder(lab.owner, name)
    root = nas.root / name
    for rel in SOURCES:
        _put(root / rel, (PAPER / "v1" / rel).read_bytes())
    token = create_token(lab.user, lab.ws, "rsn-write", 1, scope="write")
    r = lab.owner.post("/api/folder-links", json={
        "folder": w, "path": str(root), "remote_url": nas.url, "token": token["token"], "token_id": token["id"],
        "push": "everything", "pull": True, "choose": True}, headers=SHELL)
    assert r.status_code == 201, r.text
    link = r.json()
    r = lab.owner.put(f"/api/folder-links/{link['id']}/selection",
                      json={"selection": {"base": "preset", "changes": [{"path": "main.pdf", "on": True}]},
                            "base_hash": ""})
    assert r.status_code == 200, r.text
    assert _sync(lab.owner, link["id"])["last_error"] == ""
    page = vault_store.files(link["id"])["main.pdf"]["page_id"]
    turn = item = None
    if shown:
        item = _mint(lab.owner)
        r = lab.owner.post("/api/computer-links", json={"remote_url": nas.url, "workspace": lab.ws,
                                                        "token": item["token"], "token_id": item["id"]}, headers=SHELL)
        assert r.status_code == 201, r.text
        turn = channel._Channel(r.json()["link"]["id"])
    if agent:
        r = lab.owner.patch(f"/api/folder-links/{link['id']}", json={"agent": agent}, headers=SHELL)
        assert r.status_code == 200, r.text
        assert r.json()["queued"] == 0 and r.json()["agent"]["name"] == agent
    v1 = PLACES["v1"]
    passage, area = new_block_id(), new_block_id()
    commit_ops(lab.ws, page, [
        insert(page, "", {"pdf_position": _position(1, _rects(v1["passage"]["rects"])),
                          "quote": v1["passage"]["quote"]}, passage),
        insert(page, "", {"pdf_position": _position(1, _rects([v1["figure"]["rect"]]), area=True),
                          "color": "#ffe28fa6"}, area)], actor=lab.user)
    threads = [thread_on(lab.owner, page, passage, "lambda is never introduced")["id"],
               thread_on(lab.owner, page, area, "move the legend inside the plot")["id"]]
    return SimpleNamespace(link=link, id=link["id"], w=w, root=root, page=page, turn=turn, token=item,
                           threads=threads)


def _mint(c, **headers) -> dict:
    r = c.post("/api/integrations/tokens", json={"name": "Gamma desktop: TIM-PC", "scope": "computer",
                                                 "expires_in_days": 365}, headers=headers)
    assert r.status_code == 201, r.text
    return r.json()


def _bearer(token: str):
    return fresh_client(headers={"Authorization": f"Bearer {token}"})


def _send(c, f, status=201, **body) -> dict:
    r = c.post("/api/feedback/requests", json={"scope": f.w, **body})
    assert r.status_code == status, r.text
    return r.json().get("request") or r.json()


def _request(lab, request_id) -> dict:
    with connect_pages_db(lab.ws) as conn:
        return requests.get(conn, request_id)


def _threads(lab, f) -> dict:
    r = lab.owner.get(f"/api/pages/{f.page}/comments", params={"state": "all"})
    assert r.status_code == 200, r.text
    return {t["id"]: t for t in r.json()["threads"]}


def _until(test, timeout=30.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        found = test()
        if found:
            return found
        time.sleep(0.05)
    raise AssertionError("timed out")


def _computer_row(token_id: str) -> dict:
    with connect_users_db() as conn:
        row = conn.execute("SELECT vaults FROM computers WHERE token_id = ?", (token_id,)).fetchone()
    return {"vaults": json.loads(row[0])} if row else {}


def _prompt_items(log: Path) -> tuple[list[dict], dict]:
    """The comments the last turn put to the agent, as the prompt's JSON
    blocks give them, and that turn's ``turn/start``."""
    turn = [m for m in requests_in(log) if m.get("method") == "turn/start"][-1]["params"]
    text = turn["input"][0]["text"]
    return [json.loads(block) for block in re.findall(r"```json\n(.*?)\n```", text, re.S)
            if '"thread_id"' in block], turn


# --- the computer's own parts, without that server ---------------------------------------------------

def test_a_source_line_is_found_through_the_vaults_rows(tmp_path):
    root = tmp_path / "paper"
    for rel in SOURCES:
        _put(root / rel, (PAPER / "v1" / rel).read_bytes())
    st = (root / "main.pdf").stat()
    rows = {"main.pdf": {"page_id": "pg1", "kind": kinds.PDF.name, "hash": "doc-v1", "size": st.st_size,
                         "mtime_ns": st.st_mtime_ns, "was": ""},
            "notes.md": {"page_id": "pg1", "kind": kinds.NOTES.name, "hash": "", "size": 1, "mtime_ns": 1, "was": ""}}
    v1 = PLACES["v1"]
    place = {"page_id": "pg1", "kind": "passage", "page": 1, "rects": v1["passage"]["rects"], "doc_id": "doc-v1"}
    assert sources.source_in_vault(rows, root, place) == {"file": "sections/method.tex", "line": 3, "end": 5}
    figure = {**place, "kind": "area", "rects": [v1["figure"]["rect"]]}
    assert sources.source_in_vault(rows, root, figure) == {"file": "main.tex", "line": 22}
    # measured on another version, another page's, a PDF rebuilt since the vault read it: no answer
    assert sources.source_in_vault(rows, root, {**place, "doc_id": "doc-v2"}) is None
    assert sources.source_in_vault(rows, root, {**place, "page_id": "pg2"}) is None
    _put(root / "main.pdf", (PAPER / "v1" / "main.pdf").read_bytes(), age=5)
    assert sources.source_in_vault(rows, root, place) is None


def test_a_replys_id_is_the_same_on_every_try_and_the_resume_rule_reads_the_claim():
    assert home.reply_id("run-1", "t1") == home.reply_id("run-1", "t1") != home.reply_id("run-1", "t2")
    assert len(home.reply_id("run-1", "t1")) == 12 and re.match(r"^[A-Za-z0-9_-]+$", home.reply_id("run-1", "t1"))
    there = home.HttpHome("no-such-link")
    at = "2026-01-01T{}:00:00".format
    there.speakers = home._speakers([
        {"id": "t1", "by_agent": True, "at": at(10), "people": [{"id": "c1", "at": at("09")}]},
        {"id": "t2", "by_agent": False, "at": at(11),
         "people": [{"id": "c2", "at": at("09")}, {"id": "c3", "at": at(11)}]},
        {"id": "t3", "by_agent": False, "at": at(12), "people": [{"id": "c4", "at": at(12)}]}])
    seen = at(10)
    # t1: the person's word read; t2: a word the run did not read; t3: one it read, edited since
    assert there.spoken_after({"t1": (seen, {"c1"}), "t2": (seen, {"c2"}), "t3": (seen, {"c4"}),
                               "t4": ("", set())}) == {"t2", "t3"}


def test_the_channel_queues_the_sends_that_server_gives_and_stops_the_withdrawn(lab, nas, monkeypatch):
    f = _made(lab, nas, "RS heard", shown=True)
    queued, withdrawn = [], []
    monkeypatch.setattr(runner, "queue_remote", lambda link_id, request_id: queued.append((link_id, request_id)))
    monkeypatch.setattr(runner, "withdraw_remote",
                        lambda ids, request_id, run: withdrawn.append((ids, request_id, run)))
    answer = {"sends": [{"id": "r1", "folder_id": f.w, "at": "x"}, {"id": "r2", "folder_id": "elsewhere", "at": "x"},
                        {"id": 5}], "withdrawn": [{"id": "r0", "run": "run-0"}]}

    class Server:
        last_headers = {}   # what the channel reads a refusal's headers from (gamma_sync.Server.last_headers)

        def ask(self, method, path, obj=None):
            assert (method, path) == ("GET", "/api/computers/sends")
            return 200, json.dumps(answer).encode()

    link = channel.get(f.turn.link_id)
    f.turn._sends(Server(), link)
    assert queued == [(f.id, "r1")] and withdrawn == [([f.id], "r0", "run-0")]
    assert f.turn.sends_due is False
    # paused there: nothing taken; a vault with no agent asks nothing at all
    folder_links.set_paused(f.id, True)
    queued.clear()
    f.turn._sends(Server(), link)
    assert queued == []
    store.set_agent(f.id, "", by=lab.user)
    assert channel.agent_folders(link) == {}
    f.turn._sends(None, link)                                            # no request made: None would fail

    # the announce names the folder's agent, never a path
    folder_links.set_paused(f.id, False)
    store.set_agent(f.id, "codex", by=lab.user)
    state = channel.state_of(link)
    assert [v for v in state["vaults"] if v.get("folder_id") == f.w] == [
        {"kind": "folder", "folder_id": f.w, "paused": False, "agent": "codex"}]
    assert str(nas.root) not in json.dumps(state)

    # a server from before the Sends: its channel opens the announce and the poll alone (403), asked again later
    class Older:
        last_headers = {}

        def ask(self, method, path, obj=None):
            return 403, b'{"detail": "A computer token opens only the channel."}'

    f.turn.sends_due = True
    f.turn._sends(Older(), link)
    assert f.turn.sends_due is False


def test_a_run_pushes_nothing_while_its_vault_is_paused_choosing_or_gone(lab, nas):
    f = _made(lab, nas, "RS paused push", agent="")
    nas.pushed.clear()
    ran = SimpleNamespace(link_id=f.id, request_id="r1")
    folder_links.set_paused(f.id, True)                                  # its maker keeps the edits off the NAS
    runner._Run._push(ran)
    runner._Run._push(SimpleNamespace(link_id="no-such-link", request_id="r1"))
    assert nas.pushed == []
    folder_links.set_paused(f.id, False)
    runner._Run._push(ran)
    assert nas.pushed == [(f.id, {"direction": "push"})]


def test_stop_showing_turns_the_agents_there_off(lab, nas):
    f = _made(lab, nas, "RS unshown")
    r = lab.owner.delete(f"/api/computer-links/{f.turn.link_id}", headers=SHELL)
    assert r.status_code == 200, r.text
    assert store.agent_of(f.id) is None
    # shown there again (maybe as another account): no agent comes back without its question
    item = _mint(lab.owner)
    r = lab.owner.post("/api/computer-links", json={"remote_url": nas.url, "workspace": lab.ws,
                                                    "token": item["token"], "token_id": item["id"]}, headers=SHELL)
    assert r.status_code == 201, r.text
    channel._Channel(r.json()["link"]["id"]).once()
    assert [v.get("agent") for v in _computer_row(item["id"])["vaults"] if v.get("folder_id") == f.w] == [None]


def test_the_panel_turns_an_agent_on_for_a_work_folder_of_another_server_shown_there(lab, nas):
    f = _made(lab, nas, "RS gate", agent="", shown=False)
    r = lab.owner.patch(f"/api/folder-links/{f.id}", json={"agent": "codex"}, headers=SHELL)
    assert (r.status_code, r.json()["detail"]) == (400, "Show this computer on testserver first.")
    assert lab.owner.patch(f"/api/folder-links/{f.id}", json={"agent": "codex"}).status_code == 403   # the shell's word
    item = _mint(lab.owner)
    r = lab.owner.post("/api/computer-links", json={"remote_url": nas.url, "workspace": lab.ws, "token": item["token"],
                                                    "token_id": item["id"]}, headers=SHELL)
    assert r.status_code == 201, r.text
    on = lab.owner.patch(f"/api/folder-links/{f.id}", json={"agent": "codex"}, headers=SHELL)
    assert on.status_code == 200 and on.json()["agent"]["name"] == "codex" and on.json()["queued"] == 0
    # asking in the chat stays a work folder of this server's
    r = lab.owner.patch(f"/api/folder-links/{f.id}", json={"agent_runs": "ask"}, headers=SHELL)
    assert (r.status_code, r.json()["detail"]) == (409, "Asking in the chat is for a work folder of this computer's "
                                                        "server.")
    assert lab.owner.patch(f"/api/folder-links/{f.id}", json={"agent_runs": "never"}).status_code == 200
    off = lab.owner.patch(f"/api/folder-links/{f.id}", json={"agent": ""})
    assert off.status_code == 200 and off.json()["agent"] is None


# --- the loop, against that server's routes -------------------------------------------------------

def test_the_makers_send_runs_here_and_is_answered_there(lab, nas, monkeypatch):
    script_agent(monkeypatch, nas.log, edits=[{"path": "main.tex", "old": "Weight Decay", "new": "Weight decay"}],
                 commands=["latexmk -pdf main.tex"], summary="Fixed both comments.")
    f = _made(lab, nas, "RS loop")
    f.turn.once()                                                          # announced with its agent
    assert [v.get("agent") for v in _computer_row(f.token["id"])["vaults"] if v.get("folder_id") == f.w] == [
        "codex"]
    sent = _send(lab.owner, f, note="fix these")
    assert _request(lab, sent["id"])["state"] == "sent"                    # the local runner never took it
    f.turn.once()                                                          # woken, or asked after its announce
    assert runner.wait_idle(60)

    got = _request(lab, sent["id"])
    assert (got["state"], got["state_by"]) == ("done", "Codex"), got
    assert got["detail"].startswith("Fixed both comments.") and all(t["answered"] for t in got["threads"])
    assert "Weight decay" in (f.root / "main.tex").read_text(encoding="utf-8")

    # this computer's record of the run, keyed by the id the claim named there
    run = store.run_of(lab.ws, sent["id"])
    assert (run["state"], run["agent"], run["link_id"], run["changes"]) == ("done", "codex", f.id, ["main.tex"])
    assert nas.pushed and nas.pushed[-1] == (f.id, {"direction": "push"})  # what it changed goes there now

    # the replies, signed Codex as an agent on a computer, written there under the ids minted here; resolved
    threads = _threads(lab, f)
    for thread_id in f.threads:
        reply = threads[thread_id]["comments"][-1]
        assert (reply["agent"], reply.get("agent_kind"), reply["author"]) == ("Codex", "local", OWNER)
        assert (reply["body"], reply["id"]) == (f"Done for {thread_id}.", home.reply_id(run["id"], thread_id))
        assert threads[thread_id]["resolved_at"] and threads[thread_id]["resolved_agent"] == "Codex"
    assert set(store.threads_of(f.id, f.threads)) == set(f.threads)     # their sessions, for a follow-up

    # the source lines computed here, the regions' pictures fetched into the folder and handed to the agent
    items, turn = _prompt_items(nas.log)
    by = {item["thread_id"]: item for item in items}
    assert by[f.threads[0]]["source"] == {"file": "sections/method.tex", "line": 3, "end": 5, "as": "current"}
    assert by[f.threads[1]]["source"] == {"file": "main.tex", "line": 22, "as": "current"}
    assert by[f.threads[0]]["file"] == "main.pdf"
    named = [rel for item in items for rel in item["pictures"]]                  # in the prompt's order
    assert named and all(rel.startswith(f"{feedback_files.IMG}/") and (f.root / rel).is_file() for rel in named)
    assert [Path(p["path"]).resolve() for p in turn["input"] if p.get("type") == "localImage"] == [
        (f.root / rel).resolve() for rel in named]
    assert (f.root / feedback_files.DIR / ".gitignore").read_bytes() == feedback_files.GITIGNORE   # never in git
    assert str(f.root) not in json.dumps(_request(lab, sent["id"]))       # never a path of this computer there


def test_a_send_made_while_the_computer_is_away_runs_once_it_is_back(lab, nas, monkeypatch):
    f = _made(lab, nas, "RS away")
    f.turn.once()
    nas.down.append(True)                                                  # away: the NAS cannot be reached
    assert f.turn.once() == channel.RETRY_FIRST_S
    sent = _send(lab.owner, f)
    time.sleep(0.3)
    assert _request(lab, sent["id"])["state"] == "sent"                    # waits for it
    nas.down.clear()
    f.turn.once()                                                          # back: announced again, the Sends asked
    assert runner.wait_idle(60)
    assert _request(lab, sent["id"])["state"] == "done"


def test_a_tokens_send_is_refused_and_another_accounts_never_runs(lab, nas):
    f = _made(lab, nas, "RS others")
    f.turn.once()
    token = create_token(lab.user, lab.ws, "rsn-script", 1, scope="write")
    r = _bearer(token["token"]).post("/api/feedback/requests", json={"scope": f.w},
                                     headers={"X-Gamma-Workspace": lab.ws})
    assert r.status_code == 403 and "takes Sends made in Gamma" in r.json()["detail"], r.text
    theirs = _send(lab.editor, f)
    f.turn.once()
    assert runner.wait_idle(30)
    assert _request(lab, theirs["id"])["state"] == "sent" and store.run_of(lab.ws, theirs["id"]) is None


def test_a_withdraw_stops_the_run_and_is_never_overwritten(lab, nas, monkeypatch):
    script_agent(monkeypatch, nas.log, delay=30)
    f = _made(lab, nas, "RS withdraw")
    f.turn.once()
    sent = _send(lab.owner, f)
    f.turn.once()
    _until(lambda: (run := store.run_of(lab.ws, sent["id"])) and run["pid"])
    r = lab.owner.post(f"/api/feedback/requests/{sent['id']}/state", json={"state": "failed"})
    assert r.status_code == 200, r.text
    f.turn.once()                                                          # the withdraw heard: the run stops
    assert runner.wait_idle(30)
    got = _request(lab, sent["id"])
    assert (got["state"], got["detail"]) == ("failed", requests.WITHDRAWN)
    assert store.run_of(lab.ws, sent["id"])["state"] == "stopped"
    assert all(len(t["comments"]) == 1 for t in _threads(lab, f).values())   # nothing written


def test_a_claim_and_the_answers_retried_write_nothing_twice(lab, nas):
    f = _made(lab, nas, "RS retried")
    f.turn.once()
    sent = _send(lab.owner, f)
    computer = _bearer(f.token["token"])
    claim = f"/api/computers/sends/{sent['id']}/claim"
    first = computer.post(claim, json={"run": "rsn-run-1", "agent": "codex"})
    assert first.status_code == 200, first.text
    body = first.json()
    assert body["request"]["state"] == "running" and body["seen"]
    assert {e["id"] for e in body["entries"]} == set(f.threads)
    assert all(e["place"]["rects"] and e["place"]["doc_id"] for e in body["entries"] if e["kind"] == "passage")
    assert computer.post(claim, json={"run": "rsn-run-1", "agent": "codex"}).status_code == 200   # held: told so
    assert computer.post(claim, json={"run": "rsn-run-2", "agent": "codex"}).status_code == 409   # another: refused
    progress = f"/api/computers/sends/{sent['id']}/progress"
    assert computer.post(progress, json={"run": "rsn-run-2", "detail": "Reading"}).status_code == 409
    assert computer.post(progress, json={"run": "rsn-run-1", "detail": "Reading 2 comments"}).status_code == 200
    assert _request(lab, sent["id"])["detail"] == "Reading 2 comments"

    replies = [{"id": home.reply_id("rsn-run-1", t), "thread_id": t, "text": "Done.", "resolve": True}
               for t in f.threads]
    answers = {"run": "rsn-run-1", "replies": replies, "state": "done", "summary": "Fixed.", "changed": ["main.tex"]}
    r = computer.post(f"/api/computers/sends/{sent['id']}/answers", json=answers)
    assert r.status_code == 200 and sorted(r.json()["written"]) == sorted(x["id"] for x in replies), r.text
    again = computer.post(f"/api/computers/sends/{sent['id']}/answers", json=answers)
    assert again.status_code == 200
    threads = _threads(lab, f)
    assert all([c["body"] for c in threads[t]["comments"]].count("Done.") == 1 for t in f.threads)
    got = _request(lab, sent["id"])
    assert got["state"] == "done" and got["detail"].startswith("Fixed.") and "main.tex" in got["detail"]


def test_a_run_whose_answers_were_lost_tries_again_and_writes_nothing_twice(lab, nas, monkeypatch):
    f = _made(lab, nas, "RS lost")
    f.turn.once()
    monkeypatch.setattr(home, "RETRY_S", 5)
    monkeypatch.setattr(home, "RETRY_FIRST_S", 0.05)
    opened, lost = gamma_sync.default_open, []

    def losing(method, path, headers, body=None):
        answer = opened(method, path, headers, body)
        if method == "POST" and path.endswith(("/claim", "/answers")) and path not in lost:
            lost.append(path)                    # that server wrote it; its answer never came back
            raise gamma_sync.Unreachable("the answer broke off")
        return answer

    monkeypatch.setattr(gamma_sync, "default_open", losing)
    sent = _send(lab.owner, f)
    f.turn.once()
    assert runner.wait_idle(60)
    assert len(lost) == 2 and _request(lab, sent["id"])["state"] == "done"
    threads = _threads(lab, f)
    assert all([c.get("agent") for c in threads[t]["comments"]].count("Codex") == 1 for t in f.threads)
    assert store.run_of(lab.ws, sent["id"])["state"] == "done"


def test_a_mention_made_there_runs_here(lab, nas):
    f = _made(lab, nas, "RS mention")
    f.turn.once()
    r = lab.owner.post(f"/api/pages/{f.page}/comments", json={"block_id": f.page, "body": "@codex tighten the title"})
    assert r.status_code == 201, r.text
    thread_id = r.json()["thread"]["id"]
    f.turn.once()
    assert runner.wait_idle(60)
    reply = _threads(lab, f)[thread_id]["comments"][-1]
    assert (reply["agent"], reply.get("agent_kind")) == ("Codex", "local")


def test_recovery_fails_an_open_run_there(lab, nas):
    f = _made(lab, nas, "RS recover")
    f.turn.once()
    sent = _send(lab.owner, f)
    computer = _bearer(f.token["token"])
    run_id = "rsn-left-open"
    assert computer.post(f"/api/computers/sends/{sent['id']}/claim",
                         json={"run": run_id, "agent": "codex"}).status_code == 200
    store.start_run(ws=lab.ws, link_id=f.id, request_id=sent["id"], agent="codex", run_id=run_id)
    runner.recover()
    _until(lambda: store.get_run(run_id)["ended_at"])
    got = _request(lab, sent["id"])
    assert (got["state"], got["detail"]) == ("failed", "Gamma stopped before Codex finished.")
    assert store.get_run(run_id)["state"] == "failed"


def test_a_run_left_open_while_that_server_was_away_fails_there_once_it_is_back(lab, nas):
    f = _made(lab, nas, "RS settle")
    f.turn.once()
    sent = _send(lab.owner, f)
    computer = _bearer(f.token["token"])
    run_id = "rsn-left-away"
    assert computer.post(f"/api/computers/sends/{sent['id']}/claim",
                         json={"run": run_id, "agent": "codex"}).status_code == 200
    store.start_run(ws=lab.ws, link_id=f.id, request_id=sent["id"], agent="codex", run_id=run_id)
    # a run that holds it now is never failed
    key = (f.id, runner.SEND, sent["id"])
    with runner._lock:
        runner._active[key] = {"link_id": f.id, "stop": threading.Event(), "reason": "",
                               "claimed": True, "run_id": run_id}
    try:
        runner.fail_remote_of([f.id])
        time.sleep(0.3)
        assert store.get_run(run_id)["ended_at"] is None and _request(lab, sent["id"])["state"] == "running"
    finally:
        with runner._lock:
            runner._active.pop(key, None)
    nas.down.append(True)                                                  # its end lost: that server away
    assert f.turn.once() == channel.RETRY_FIRST_S
    nas.down.clear()
    f.turn.once()                                                          # back: announced again, the run failed
    _until(lambda: store.get_run(run_id)["ended_at"])
    got = _request(lab, sent["id"])
    assert (got["state"], got["detail"]) == ("failed", "Gamma stopped before Codex finished.")


def test_a_claim_whose_answer_never_came_is_failed_there_once_it_is_reached(lab, nas, monkeypatch):
    f = _made(lab, nas, "RS claim lost")
    f.turn.once()
    opened = gamma_sync.default_open

    def losing(method, path, headers, body=None):
        answer = opened(method, path, headers, body)
        if method == "POST" and path.endswith("/claim"):
            raise gamma_sync.Unreachable("the answer broke off")             # that server took it; no answer came
        return answer

    monkeypatch.setattr(gamma_sync, "default_open", losing)
    sent = _send(lab.owner, f)
    f.turn.once()
    assert runner.wait_idle(60)
    assert _request(lab, sent["id"])["state"] == "running"
    run = store.run_of(lab.ws, sent["id"])
    assert run is not None and run["ended_at"] is None                    # recorded open, for it to be failed there
    monkeypatch.setattr(gamma_sync, "default_open", opened)
    nas.down.append(True)
    f.turn.once()
    nas.down.clear()
    f.turn.once()
    _until(lambda: store.get_run(run["id"])["ended_at"])
    got = _request(lab, sent["id"])
    assert (got["state"], got["detail"]) == ("failed", "Gamma stopped before Codex finished.")


def test_the_channels_routes_answer_only_the_computers_own(lab, nas):
    f = _made(lab, nas, "RS refusals")
    f.turn.once()
    theirs = _send(lab.editor, f)
    computer = _bearer(f.token["token"])
    # a session, a write token: not the channel
    assert lab.owner.get("/api/computers/sends").status_code == 403
    write = create_token(lab.user, lab.ws, "rsn-other", 1, scope="write")
    assert _bearer(write["token"]).get("/api/computers/sends", headers={"X-Gamma-Workspace": lab.ws}).status_code == 403
    # another account's request: none of this computer's
    assert computer.post(f"/api/computers/sends/{theirs['id']}/claim",
                         json={"run": "rsn-run-x", "agent": "codex"}).status_code == 404
    assert theirs["id"] not in {s["id"] for s in computer.get("/api/computers/sends").json()["sends"]}
    # a folder no longer announced with an agent
    mine = _send(lab.owner, f)
    lab.owner.patch(f"/api/folder-links/{f.id}", json={"agent": ""}).raise_for_status()
    f.turn.once()
    assert computer.get("/api/computers/sends").json()["sends"] == []
    assert computer.post(f"/api/computers/sends/{mine['id']}/claim",
                         json={"run": "rsn-run-y", "agent": "codex"}).status_code == 404
    assert computer.get(f"/api/computers/threads/{f.threads[0]}/picture?at=now").status_code == 404
