"""The chat's computer side on another server's bucket (step 4d of
docs/research/agent-collab.md, step 4.2 of
docs/research/agents-as-participants-plan.md): the computer's side
(gamma/local_agents/home.py ``HttpChatHome`` and ``far_place``, runner.py
``queue_remote_chat``, ``stop_remote_chat`` and ``_ChatTurn`` through its
home, computers/channel.py ``_chats`` and the announce's ``models`` and
``dirs``) against that server's chat routes (gamma/routers/local_agents.py
for a conversation whose agent runs on another computer,
gamma/routers/computers.py ``/computers/chats``).

One process plays both: the NAS is a TestClient of its own reached through
``gamma_sync.default_open``, the computer this server as the desktop app
runs it, with the stand-in Codex (tests/fake_agents/). This computer's own
in-process reads and writes of the NAS's workspace fail (``far``), its
conversation's too, so a turn that read or wrote the wrong pages.db fails
though both share one data directory.

A source line found through the vault's rows; the id of a turn's reply in
its thread the same on every try; the announce naming the agents on for the
channel's account, a work folder's vault its agent for an older server,
each assistant's models, the directories offered by id and name and never a
path; a turn pushing nothing while its vault is paused or gone; Stop
showing leaving the computer's agents be; the channel queueing the turns
that server gives where they work and stopping the ones stopped there; a
turn queued once, one that names no directory here in a queue of its own, a
stop there taking a queued one out; the directory a turn names checked here
(a vault of this channel alone, a directory offered, one of its own, never
a path); the writes rising in ``seq``, a lost one carried by the next, the
last tried again, a 409 ending them; the maker's message on the NAS run
here, its reply, steps and changed files written back there, the source
line of its passage computed here; a page in no synced folder run here in
a directory of its own or one offered; a far turn's token of that server,
one per turn, taken only at that server's own address and gone at the
turn's end, with which the agent comments as itself; the conversation read
while the computer is away and a message for it refused;
a comment sent there to the computer's agent run here as a turn of its
thread, the thread, its source line and its region's pictures (fetched
through the channel while its run holds the turn, and only then) in the
prompt, the file it edited pushed there, and its end written in the thread
there under the agent's name, an offline computer refused; an ``@codex``
made there run here so too;
another account's turn never run; the routes' gates; Stop of a queued and a
running turn; a write retried or late never going backwards; a 409 stopping
the turn; a follow-up resuming the session here, a lost one taking the
earlier messages along; the model and effort checked there and here; a turn
there asking nothing in the chat while its link relays no asks
(test_remote_asks.py: asking on this computer, and relayed there);
recovery failing an open turn there, and the channel's next announce one
whose end, or whose claim, never reached it, never one claimed again here; a Stop there of a turn left open here ending it; a turn
whose agent was turned off while it waited failing there with why; the
watch of a turn there never waiting on a write; Background tasks there.
The local chat is test_local_agents_chat.py's."""

import io
import json
import os
import re
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest

from conftest import account_of, fresh_client, login, make_folder, make_page, make_user, thread_on
from fake_agents import read_log, requests_in, script_agent, serve_mcp, settle_probes, use_fakes
from gamma import agents, config, folder_links, gamma_sync, integrations, pdf_meta, storage, workspaces
from gamma.blocks_store import new_block_id
from gamma.computers import channel, registry, sessions as computer_sessions
from gamma.db import connect_pages_db, connect_users_db, connect_vaults_db
from gamma.annotations import files as annotation_files, sources
from gamma.integrations import create_token
from gamma.local_agents import catalog, chats, codex, events, home, procs, runner, sessions, store
from gamma.ops import commit_ops
from gamma.routers import computers as computer_routes
from gamma.vault import kinds, store as vault_store
from gamma.work_folders import selection
from test_folder_links import _sync
from test_highlight_relocate import H, W, _position
from test_local_agents_extras import _codex_store
from test_vault_sync import insert

OWNER, EDITOR = "rch_owner", "rch_editor"
PASSWORD = "rch-pass-1234"
SECRET = "rch-shell-secret"
SHELL = {"X-Gamma-Shell": SECRET}
PAPER = Path(__file__).parent / "samples" / "synctex" / "paper"
PLACES = json.loads((PAPER / "places.json").read_text(encoding="utf-8"))
SOURCES = ("main.pdf", "main.synctex.gz", "main.tex", "sections/method.tex", "figures/fig1.pdf")
THREAD = "019a0000-fake-thread"   # the stand-in Codex's thread


@pytest.fixture(scope="module")
def lab(client):
    """A shared workspace of rch_owner's on the NAS that rch_editor edits."""
    for name in (OWNER, EDITOR):
        make_user(name, PASSWORD)
    owner, editor = login(OWNER, PASSWORD), login(EDITOR, PASSWORD)
    ws = workspaces.create("RC lab", account_of(OWNER), kind="shared")["id"]
    workspaces.set_member(ws, account_of(EDITOR), "editor", by=account_of(OWNER))
    for c in (owner, editor):
        c.headers["X-Gamma-Workspace"] = ws
    return SimpleNamespace(ws=ws, owner=owner, editor=editor, user=account_of(OWNER))


@pytest.fixture
def nas(lab, tmp_path, monkeypatch):
    """This server as the desktop app runs it (agents on, the shell's secret
    set, the stand-in Codex), the NAS a TestClient reached through
    ``gamma_sync.default_open`` (``down`` makes it unreachable), polls held
    no time, no channel thread unless a test runs a turn, a remote turn's
    message written at most every 0.2 s, and this computer's in-process
    reads and writes of the NAS's workspace refused, its conversations'
    included (``far``)."""
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
    monkeypatch.setattr(channel, "POLL_S", 0)
    monkeypatch.setattr(channel, "start", lambda link_id: None)
    monkeypatch.setattr(home, "RETRY_S", 0)
    monkeypatch.setattr(home, "WRITE_EVERY_S", 0.2)
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

    for module in (folder_links, selection, annotation_files, home):
        monkeypatch.setattr(module, "connect_pages_db", guarded(module.connect_pages_db))
    for name in ("reply_of", "write_reply", "sources_of"):
        monkeypatch.setattr(chats, name, guarded(getattr(chats, name)))
    monkeypatch.setattr(folder_links, "commit_ops", guarded(folder_links.commit_ops))
    runner.register()
    use_fakes(monkeypatch, tmp_path / "agent.log")
    catalog.forget()
    runner.reset()
    registry.reset()
    computer_sessions.reset()
    yield SimpleNamespace(url="http://testserver", root=tmp_path / "disk", log=tmp_path / "agent.log", down=down,
                          pushed=pushed, http=http)
    runner.reset()
    settle_probes()
    catalog.forget()
    left = list(procs._live)
    procs.stop_all(timeout=1)
    channel.stop_all()
    registry.reset()
    computer_sessions.reset()
    for link in folder_links.all_links():
        if folder_links.inside(link["path"], str(tmp_path)):
            folder_links._forget(link)
    with connect_vaults_db() as conn:
        for table in ("computer_agents", "agent_dir_grants", "agent_turns", "agent_conversations", "agent_people",
                      "agent_dirs"):
            conn.execute(f"DELETE FROM {table}")
    with connect_users_db() as conn:
        for user in (account_of(OWNER), account_of(EDITOR)):
            conn.execute("DELETE FROM integration_tokens WHERE user_id = ?", (user,))
            conn.execute("DELETE FROM computers WHERE user_id = ?", (user,))
            conn.execute("DELETE FROM computer_links WHERE created_by = ?", (user,))
    with annotation_files._guard:
        annotation_files._pages.clear()
        annotation_files._scopes.clear()
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
    ``turn`` a turn of it) and ``agent`` turned on for the computer with the
    shell's word; a passage and the figure commented on there."""
    w = make_folder(lab.owner, name)
    root = nas.root / name
    for rel in SOURCES:
        _put(root / rel, (PAPER / "v1" / rel).read_bytes())
    token = create_token(lab.user, lab.ws, "rch-write", 1, scope="write")
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
        r = lab.owner.patch(f"/api/local-agents/{agent}", json={"enabled": True}, headers=SHELL)
        assert r.status_code == 200 and r.json()["enabled"], r.text
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
        row = conn.execute("SELECT vaults, assistants FROM computers WHERE token_id = ?", (token_id,)).fetchone()
    return {"vaults": json.loads(row[0]), "assistants": json.loads(row[1])} if row else {}


def _threads(lab, f) -> dict:
    r = lab.owner.get(f"/api/pages/{f.page}/comments", params={"state": "all"})
    assert r.status_code == 200, r.text
    return {t["id"]: t for t in r.json()["threads"]}


def _block_of(lab, f, thread_id) -> str:
    return _threads(lab, f)[thread_id]["block_id"]


def _chat(who, page, status=200, **params) -> dict:
    r = who.get("/api/local-agents/chat", params={"bucket": page, **params})
    assert r.status_code == status, r.text
    return r.json()


def _say(who, page, text="Shorten the title.", status=202, **body) -> dict:
    r = who.post("/api/local-agents/chat/turns", json={"bucket": page, "text": text, **body})
    assert r.status_code == status, r.text
    return r.json()


def _stop(who, turn_id, status=200) -> dict:
    r = who.post(f"/api/local-agents/chat/turns/{turn_id}/stop")
    assert r.status_code == status, r.text
    return r.json()


def _reply(lab, page) -> dict:
    """The agent's last message of the page's active conversation there."""
    return [m for m in _chat(lab.owner, page)["conversation"]["messages"] if m["role"] == "agent"][-1]


def _turn_row(turn_id: str) -> dict | None:
    """This computer's record of a turn there (``store.get_turn`` answers a
    local one's only)."""
    return next((t for t in _rows() if t["id"] == turn_id), None)


def _rows() -> list[dict]:
    with connect_vaults_db() as conn:
        rows = conn.execute("SELECT id, ws, link_id, conversation_id, page_id, agent, state, ended_at, error, pid "
                            "FROM agent_turns").fetchall()
    return [dict(zip(("id", "ws", "link_id", "conversation_id", "page_id", "agent", "state", "ended_at", "error",
                      "pid"), row)) for row in rows]


def _run_there(lab, turn_id: str) -> dict | None:
    """The NAS's ``computer_chat_runs`` row of the turn."""
    with connect_pages_db(lab.ws) as conn:
        return chats.run_of(conn, turn_id)


def _sent(log, method: str) -> list[dict]:
    """The params of each ``method`` the stand-in Codex read, in order."""
    return [m["params"] for m in requests_in(log) if m.get("method") == method]


def _told(log) -> tuple[str, dict]:
    """The last turn's prompt, and the message's JSON block in it."""
    text = _sent(log, "turn/start")[-1]["input"][0]["text"]
    return text, json.loads(text.split("```json\n", 1)[1].split("\n```", 1)[0])


def _box(place: dict) -> list:
    rects = place["rects"]
    return [rects[1][0], rects[0][1], rects[1][2], rects[2][3]]


class _Server:
    """A stand-in for that server's client: each request answered from
    ``answers`` (``(status, body)``; a None status cannot reach it), each
    one sent kept in ``sent``."""

    last_headers = {}   # what the channel reads a refusal's headers from (gamma_sync.Server.last_headers)

    def __init__(self, answers=None):
        self.answers, self.sent = list(answers or []), []

    def ask(self, method, path, obj=None):
        self.sent.append((method, path, obj))
        status, body = self.answers.pop(0)
        if status is None:
            raise gamma_sync.Unreachable("cannot reach it")
        return status, json.dumps(body).encode()


# --- the computer's own parts --------------------------------------------------------------------

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


def test_a_far_threads_pictures_are_written_as_the_annotation_files_are(tmp_path):
    """The pictures a thread's turn fetches go below ``.gamma/annotations/img``
    through ``vault_disk.write_own``: never through a link or a junction on
    the way, never bytes that are no JPEG, and one that cannot be written is
    left out of the prompt, the turn going on."""
    from gamma.vault import disk as vault_disk

    jpeg = annotation_files.JPEG + b"picture"
    asked = []

    def far(answers):
        made = home.HttpChatHome.__new__(home.HttpChatHome)
        made.link_id = "lnk"
        made._ask = lambda method, path, body=None, *, retry=True: (asked.append(path), answers.pop(0))[1]
        return made

    thread = {"id": "t1", "picture": f"{annotation_files.IMG}/k1.jpg", "before": f"{annotation_files.IMG}/k2.jpg"}
    root = tmp_path / "paper"
    vault_disk.make_marker(root)
    far([(200, jpeg), (200, b"<html>no picture</html>")])._pictures(thread, root)
    assert (root / annotation_files.IMG / "k1.jpg").read_bytes() == jpeg
    assert not (root / annotation_files.IMG / "k2.jpg").exists()               # no JPEG: not written
    assert (root / annotation_files.DIR / ".gitignore").read_bytes() == annotation_files.GITIGNORE
    asked.clear()
    far([(200, jpeg)])._pictures({**thread, "picture": None}, root)             # k1 kept: k2 asked alone
    assert len(asked) == 1 and "at=before" in asked[0]

    elsewhere, linked = tmp_path / "elsewhere", tmp_path / "linked"
    elsewhere.mkdir()
    vault_disk.make_marker(linked)
    (linked / annotation_files.DIR).mkdir(parents=True)
    if os.name == "nt":
        import _winapi
        _winapi.CreateJunction(str(elsewhere), str(linked / annotation_files.IMG))
    else:
        os.symlink(elsewhere, linked / annotation_files.IMG)
    asked.clear()
    far([(200, jpeg), (200, jpeg)])._pictures(thread, linked)
    assert not asked and not list(elsewhere.iterdir())                         # nothing asked, nothing written
    blocked = tmp_path / "blocked"
    vault_disk.make_marker(blocked)
    (blocked / annotation_files.DIR).mkdir(parents=True)
    (blocked / annotation_files.IMG).write_bytes(b"a file where the directory goes")
    far([(200, jpeg), (200, jpeg)])._pictures(thread, blocked)                 # an OSError: left out, no raise
    assert (blocked / annotation_files.IMG).is_file()


def test_a_turns_reply_in_its_thread_has_one_id_however_often_its_end_is_written():
    assert home.reply_id("turn-1", "t1") == home.reply_id("turn-1", "t1") != home.reply_id("turn-1", "t2")
    assert len(home.reply_id("turn-1", "t1")) == 12 and re.match(r"^[A-Za-z0-9_-]+$", home.reply_id("turn-1", "t1"))


def test_the_announce_names_the_agents_on_for_the_channels_account_and_never_a_path(lab, nas):
    f = _made(lab, nas, "RC announce")
    link = channel.get(f.turn.link_id)
    # the agents on for the channel's account, and a work folder's vault its agent for an older server alone
    state = channel.state_of(link)
    assert [v for v in state["vaults"] if v.get("folder_id") == f.w] == [
        {"kind": "folder", "folder_id": f.w, "paused": False, "agent": "codex"}]
    assert [(a["name"], a["enabled"]) for a in state["assistants"]] == [("codex", True), ("claude", False)]
    assert channel.agent_folders(link) == {f.w: (folder_links.get_link(f.id), "codex")}
    # the directories offered its agents, by id and name
    drafts = nas.root / "RC drafts"
    drafts.mkdir(parents=True)
    [offered] = store.set_dirs([{"path": str(drafts), "name": "Drafts"}])
    state = channel.state_of(link)
    assert state["dirs"] == [{"id": offered["id"], "name": "Drafts"}]
    assert str(nas.root) not in json.dumps(state) and str(nas.root).replace("\\", "/") not in json.dumps(state)
    # with no agent on, the channel asks that server for nothing and offers no directory
    store.set_agent("codex", False, by=lab.user)
    assert channel.agent_folders(link) == {} and channel.state_of(link)["dirs"] == []
    f.turn._chats(None, link)                                            # no request made: None would fail


def test_a_turn_pushes_nothing_while_its_vault_is_paused_or_gone(lab, nas):
    f = _made(lab, nas, "RC paused push", agent="")
    nas.pushed.clear()
    turning = SimpleNamespace(link_id=f.id, name="t1")
    folder_links.set_paused(f.id, True)                                  # its maker keeps the edits off the NAS
    runner._Turning._push(turning)
    runner._Turning._push(SimpleNamespace(link_id="no-such-link", name="t1"))
    assert nas.pushed == []
    folder_links.set_paused(f.id, False)
    runner._Turning._push(turning)
    assert nas.pushed == [(f.id, {"direction": "push"})]


def test_stop_showing_leaves_the_computers_agents_be(lab, nas):
    f = _made(lab, nas, "RC unshown")
    r = lab.owner.delete(f"/api/computer-links/{f.turn.link_id}", headers=SHELL)
    assert r.status_code == 200, r.text
    assert store.agent_for(lab.user)["agent"] == "codex"
    # shown there again: the announce says the computer's agents as they are
    item = _mint(lab.owner)
    r = lab.owner.post("/api/computer-links", json={"remote_url": nas.url, "workspace": lab.ws,
                                                    "token": item["token"], "token_id": item["id"]}, headers=SHELL)
    assert r.status_code == 201, r.text
    channel._Channel(r.json()["link"]["id"]).once()
    assert [(a["name"], a["enabled"]) for a in _computer_row(item["id"])["assistants"]] == [("codex", True),
                                                                                           ("claude", False)]


def test_the_announce_names_the_models_each_assistant_offers(lab, nas):
    f = _made(lab, nas, "RC models")
    found = catalog.probe("codex")
    link = channel.get(f.turn.link_id)
    codex_said = next(a for a in channel.state_of(link)["assistants"] if a["name"] == "codex")
    assert codex_said["models"] == found["models"] and found["models"]
    assert "gpt-fake-hidden" not in [m["id"] for m in codex_said["models"]]
    many = {**found, "models": [{**found["models"][0], "id": f"m{n}"} for n in range(40)]}
    catalog._cache["codex"] = many
    assert len(next(a for a in channel.state_of(link)["assistants"] if a["name"] == "codex")["models"]) == \
        channel.MAX_MODELS
    catalog._cache["codex"] = found
    f.turn.once()                                                          # announced with them: the strip there
    computer = _chat(lab.owner, f.page)["computer"]
    assert [m["id"] for m in computer["models"]] == [m["id"] for m in found["models"]]
    assert (computer["agent"], computer["remote"], computer["online"], computer["session"]) == (
        "codex", True, True, False)


def test_the_channel_queues_the_turns_that_server_gives_and_stops_the_ones_stopped_there(lab, nas, monkeypatch):
    f = _made(lab, nas, "RC heard")
    queued, stopped, ended = [], [], []
    monkeypatch.setattr(runner, "queue_remote_chat", lambda link_id, turn_id, **kw: queued.append((link_id, turn_id,
                                                                                                    kw)))
    monkeypatch.setattr(runner, "stop_remote_chat",
                        lambda link_id, turn_id, run: stopped.append((link_id, turn_id, run)) or turn_id == "t9")
    monkeypatch.setattr(runner, "end_stopped_remote", lambda link_id, turn_id, run: ended.append((link_id, turn_id,
                                                                                                   run)))
    kept = {"kind": "vault", "id": f.w}
    answer = {"turns": [{"id": "t1", "conversation_id": "c1", "bucket": f.page, "agent": "codex", "dir": kept,
                         "at": "x"},
                        {"id": "t2", "conversation_id": "c2", "bucket": f.page, "agent": "gpt", "dir": kept},
                        {"id": "../t3", "agent": "codex"}, {"id": 5, "agent": "codex"}, "junk",
                        # a server from before the agents' buckets: the work folder a vault here keeps, its agent
                        {"id": "t4", "conversation_id": "c4", "page_id": f.page, "folder_id": f.w, "at": "x"},
                        {"id": "t5", "conversation_id": "c5", "page_id": f.page, "folder_id": "not-kept-here"}],
              "stops": [{"id": "t0", "run": "run-0"}, {"id": "t9", "run": "run-9"}, {"id": ""}, {"id": "../t8"}]}
    ch = f.turn.link_id
    link = channel.get(ch)
    server = _Server([(200, answer)])
    f.turn._chats(server, link)
    assert server.sent == [("GET", "/api/computers/chats", None)]
    assert queued == [(ch, "t1", {"agent": "codex", "conversation_id": "c1", "bucket": f.page, "dir": kept}),
                      (ch, "t4", {"agent": "codex", "conversation_id": "c4", "bucket": f.page, "dir": kept})]
    assert stopped == [(ch, "t0", "run-0"), (ch, "t9", "run-9")] and f.turn.chats_due is False
    assert ended == [(ch, "t0", "run-0")]                                # this channel neither queues nor runs it: ended
    # with no agent on, nothing is asked at all
    store.set_agent("codex", False, by=lab.user)
    f.turn._chats(_Server(), link)
    store.set_agent("codex", True, by=lab.user)
    # a server from before the chat ran there (404, or 403: its channel opened no chat route): asked again later
    for status in (404, 403):
        f.turn.chats_due = True
        f.turn._chats(_Server([(status, {"detail": "no"})]), link)
        assert f.turn.chats_due is False
    # that server not reached: asked at the next turn
    f.turn._chats(_Server([(None, None)]), link)
    assert f.turn.chats_due is True


def test_a_turn_there_is_queued_once_and_a_stop_there_takes_it_out_or_stops_it(lab, nas):
    f = _made(lab, nas, "RC queue")
    ch, kept = f.turn.link_id, {"kind": "vault", "id": f.w}
    slots = runner._slots
    for _ in range(runner.MAX_RUNS):                                       # the slots held: the turns wait
        slots.acquire()
    try:
        assert runner.queue_remote_chat(ch, "rcTurnA", conversation_id="c", bucket=f.page, agent="codex", dir=kept)
        _until(lambda: (ch, "rcTurnA") in runner._active)
        assert runner.queue_remote_chat(ch, "rcTurnA", conversation_id="c", bucket=f.page, agent="codex", dir=kept)
        assert runner.queue_remote_chat(ch, "rcTurnB", conversation_id="c", bucket=f.page, agent="codex", dir=kept)
        place = runner._place(f.link["path"])                              # its vault's directory's queue
        assert list(runner._queues[place]) == [(ch, "rcTurnB")] and runner._queued[(ch, "rcTurnB")] == f.id
        # one that names no directory here waits in a queue of its own, to fail with why once taken
        assert runner.queue_remote_chat(ch, "rcTurnP", conversation_id="c", bucket=f.page, agent="codex",
                                        dir={"kind": "offered", "id": "C:\\Users"})
        assert list(runner._queues[f"{ch}:rcTurnP"]) == [(ch, "rcTurnP")]
        assert runner.stop_remote_chat(ch, "rcTurnP")
        assert runner.chat_turns(lab.user) == []                           # no account here is a turn there's
        assert runner.at_work([f.id]) == {}                                # not claimed yet
        assert runner.stop_remote_chat(ch, "rcTurnB")                       # queued: out of the queue
        assert (ch, "rcTurnB") not in runner._queued and not runner._queues[place]
        assert not runner.stop_remote_chat(ch, "rcTurnA", "another-run")    # another run's: not this one
        assert runner.stop_remote_chat(ch, "rcTurnA", home.chat_run_id("rcTurnA"))
        assert not runner.stop_remote_chat(ch, "rcTurnA")                   # stopped already
        assert not runner.end_stopped_remote(ch, "rcTurnA")                 # it runs here: its own end writes it
        assert not runner.end_stopped_remote(ch, "rcTurnZ", "another-run")  # another run's: not this one's
    finally:
        for _ in range(runner.MAX_RUNS):
            slots.release()
    assert runner.wait_idle(30)
    assert _turn_row("rcTurnA") is None                                    # stopped before its claim: never taken
    assert home.chat_run_id("rcTurnA") == home.chat_run_id("rcTurnA") != home.chat_run_id("rcTurnB")
    assert len(home.chat_run_id("rcTurnA")) == 22


def test_the_directory_a_turn_there_names_is_one_this_computer_offers_that_server(lab, nas):
    """``home.far_place``: the vault of that server's work folder this
    channel's account keeps (never one of another channel), a directory
    offered while the account has an agent on, one of its own; anything
    else refused with why, a path above all."""
    f = _made(lab, nas, "RC places")
    link = channel.get(f.turn.link_id)
    vault = home.far_place(link, {"kind": "vault", "id": f.w}, "conv1")
    assert (vault["kind"], vault["dir"], Path(vault["path"]), vault["link"]["id"]) == ("vault", f.w, f.root, f.id)
    own = home.far_place(link, {"kind": "own", "id": ""}, "conv1")
    assert (own["kind"], Path(own["path"])) == ("own", config.AGENTS_DIR / link["id"] / "conv1")
    drafts = nas.root / "RC drafts"
    drafts.mkdir(parents=True)
    [offered] = store.set_dirs([{"path": str(drafts), "name": "Drafts"}])
    found = home.far_place(link, {"kind": "offered", "id": offered["id"]}, "conv1")
    assert (found["kind"], found["dir"], found["name"], Path(found["path"])) == ("offered", offered["id"], "Drafts",
                                                                              drafts)
    refused = [({"kind": "vault", "id": "another-folder"}, "conv1", home.NOT_KEPT),
               ({"kind": "offered", "id": "not-offered"}, "conv1", home.NOT_OFFERED),
               ({"kind": "offered", "id": str(drafts)}, "conv1", home.NO_SUCH_DIR),     # a path, never
               ({"kind": "vault", "id": "../" + f.w}, "conv1", home.NO_SUCH_DIR),
               ({"kind": "own", "id": "x"}, "conv1", home.NO_SUCH_DIR),
               ({"kind": "elsewhere", "id": ""}, "conv1", home.NO_SUCH_DIR),
               ("own", "conv1", home.NO_SUCH_DIR), ({"kind": "own", "id": ""}, "../conv", home.NO_SUCH_DIR)]
    for dir, conversation, why in refused:
        with pytest.raises(ValueError, match=re.escape(why)):
            home.far_place(link, dir, conversation)
    # a vault of another channel's server never answers for this one
    with pytest.raises(ValueError, match=re.escape(home.NOT_KEPT)):
        home.far_place({**link, "remote_url": "https://elsewhere.example"}, {"kind": "vault", "id": f.w}, "conv1")
    store.set_agent("codex", False, by=lab.user)                             # no agent on: nothing offered there
    with pytest.raises(ValueError, match=re.escape(home.NOT_OFFERED)):
        home.far_place(link, {"kind": "offered", "id": offered["id"]}, "conv1")


def test_the_writes_rise_in_seq_a_lost_one_is_carried_by_the_next_and_the_last_is_tried_again(lab, nas,
                                                                                               monkeypatch):
    f = _made(lab, nas, "RC writes", shown=False, agent="")
    turning = SimpleNamespace(turn_id="rcTurnW")
    there = home.HttpChatHome(f.id, "rcTurnW")
    there.server = _Server([(200, {"ok": True}), (None, None), (200, {"ok": True, "stale": True})])
    before = time.time_ns() // 1000
    assert there.write(turning, {"text": "a", "asks": [], "state": "running", "elsewhere": 1})
    assert there.write(turning, {"text": "ab"})                           # lost on the way: the next carries it all
    assert there.write(turning, {"text": "abc"})                          # stale there: nothing to do
    seqs = [body["seq"] for _, _, body in there.server.sent]
    assert seqs == sorted(set(seqs)) and seqs[0] >= before
    assert there.server.sent[0] == ("POST", "/api/computers/chats/rcTurnW/write", {
        "run": home.chat_run_id("rcTurnW"), "seq": seqs[0], "fields": {"text": "a", "asks": [], "state": "running"}})
    # the last: tried again while that server cannot be reached, the same write each time
    monkeypatch.setattr(home, "RETRY_S", 5)
    monkeypatch.setattr(home, "RETRY_FIRST_S", 0.05)
    there.server.answers += [(None, None), (503, {}), (200, {"ok": True})]
    assert there.write(turning, {"state": "done"}, last=True)
    assert len({body["seq"] for _, _, body in there.server.sent[-3:]}) == 1 and seqs[-1] < there.seq
    # a 409 (stopped there, or taken): nothing more is sent
    there.server.answers += [(409, {"detail": "The turn is stopped now."})]
    assert there.write(turning, {"text": "x"}) is False and there.lost
    sent = len(there.server.sent)
    assert there.write(turning, {"text": "y"}, last=True) is False and len(there.server.sent) == sent
    # past RETRY_S the last raises (the turn stays open) and is not tried again
    monkeypatch.setattr(home, "RETRY_S", 0)
    other = home.HttpChatHome(f.id, "rcTurnX")
    other.server = _Server([(None, None)])
    with pytest.raises(gamma_sync.Unreachable):
        other.write(turning, {"state": "done"}, last=True)
    with pytest.raises(gamma_sync.Unreachable):
        other.write(turning, {"state": "done"}, last=True)
    assert len(other.server.sent) == 1


def test_the_watch_of_a_turn_there_never_waits_on_a_write_there(lab, nas):
    run = {"link_id": "rcVaultT", "dir": "rcVaultT", "stop": threading.Event(), "reason": "", "claimed": False,
           "agent": "", "slot": True, "detail": ""}
    turning = runner._ChatTurn("rcLinkT", "rcTurnT", "rcVaultT", run, {
        "channel": "rcLinkT", "conversation_id": "c", "bucket": "p", "dir": None, "agent": "codex", "model": "",
        "effort": ""})
    assert turning.home.remote
    held, calls = threading.Event(), []

    def write(_turning, fields, *, last=False):              # that server stalls
        calls.append(fields)
        held.wait(10)
        return True

    turning.home = SimpleNamespace(remote=True, write=write, missed=False)
    turning._dirty = True
    began = time.monotonic()
    turning._tick()                                                        # the watcher: handed to a writer
    assert time.monotonic() - began < 0.5
    _until(lambda: calls)
    time.sleep(0.3)
    turning._dirty = True
    began = time.monotonic()
    turning._tick()                                                        # one write there at a time
    assert time.monotonic() - began < 0.5 and len(calls) == 1
    held.set()
    _until(lambda: not turning._flushing.locked())
    turning._ended, turning._dirty = True, True                            # past its end: no progress more
    time.sleep(0.3)
    turning._tick()
    _until(lambda: not turning._flushing.locked())
    assert len(calls) == 1


# --- the loop, against that server's routes -------------------------------------------------------

def test_the_makers_message_there_runs_here_and_its_reply_is_written_there(lab, nas, monkeypatch):
    script_agent(monkeypatch, nas.log, edits=[{"path": "main.tex", "old": "Weight Decay", "new": "Weight decay"}],
                 commands=["latexmk -pdf main.tex"], chat="Lowered the title's case in main.tex.")
    f = _made(lab, nas, "RC loop")
    f.turn.once()
    passage = PLACES["v1"]["passage"]
    made = _say(lab.owner, f.page, "Lower the title's case.", id="rcMsgOne",
                selections=[{"text": passage["quote"], "page": 1, "box": _box(passage)}])
    turn_id, conversation_id = made["turn"]["id"], made["conversation"]["id"]
    assert made["turn"]["state"] == "queued"
    assert _turn_row(turn_id) is None                                     # this computer has not heard it yet
    f.turn.once()                                                          # woken for it: heard, queued, run
    assert runner.wait_idle(60)

    reply = _reply(lab, f.page)
    assert (reply["state"], reply["error"], reply["fresh"]) == ("done", "", False), reply
    assert reply["text"] == "Lowered the title's case in main.tex."
    assert reply["steps"] == [{"id": "fc0:0", "kind": "edit", "status": "completed", "path": "main.tex"},
                              {"id": "ce0", "kind": "run", "status": "completed", "program": "latexmk"}]
    assert (reply["changes"], reply["changed"], reply["built"], reply["ended_at"] is not None) == (
        ["main.tex"], 1, False, True)
    assert "Weight decay" in (f.root / "main.tex").read_text(encoding="utf-8")
    raw = json.dumps(_chat(lab.owner, f.page))
    assert json.dumps(str(nas.root))[1:-1] not in raw and str(nas.root).replace("\\", "/") not in raw
    assert nas.pushed and nas.pushed[-1] == (f.id, {"direction": "push"})   # what it changed goes there now

    # what the agent was told: the message, the file here, the passage's source line computed on this disk
    text, message = _told(nas.log)
    assert message["message"] == "Lower the title's case." and message["about"]["path"] == "main.pdf"
    [chosen] = message["selections"]
    assert (chosen["quote"], chosen["page"], chosen["source"]["file"]) == (passage["quote"], 1, "sections/method.tex")
    assert OWNER in text and "Earlier in this conversation" not in text
    assert _sent(nas.log, "thread/start")[-1]["approvalPolicy"] == "never"   # a turn there never asks

    # what this computer keeps, and what that server does
    kept = store.conversation_of(conversation_id)
    assert (kept["agent"], kept["session"], kept["link_id"], Path(kept["cwd"])) == ("codex", THREAD, f.id, f.root)
    row = _turn_row(turn_id)
    assert (row["state"], row["ws"], row["link_id"], row["conversation_id"], row["page_id"]) == (
        "done", f.turn.link_id, f.turn.link_id, conversation_id, f.page) and row["ended_at"] and row["pid"]
    assert store.get_turn(turn_id) is None                                 # never a turn of this server's
    there = _run_there(lab, turn_id)
    assert (there["state"], there["run"], there["holder"]) == ("done", home.chat_run_id(turn_id), f.token["id"])


def test_the_conversation_reads_while_the_computer_is_away_and_a_message_for_it_is_refused(lab, nas):
    f = _made(lab, nas, "RC away")
    f.turn.once()
    _say(lab.owner, f.page, "Fix the title.")
    f.turn.once()
    assert runner.wait_idle(60)
    nas.down.append(True)                                                  # away: the NAS cannot be reached
    assert f.turn.once() == channel.RETRY_FIRST_S
    registry.reset()                                                       # and that server sees it offline
    read = _chat(lab.owner, f.page)
    assert read["computer"]["online"] is False and read["computer"]["since"]
    assert [m["state"] for m in read["conversation"]["messages"] if m["role"] == "agent"] == ["done"]
    computer = registry.computer_of(f.token["id"])[1]
    assert _say(lab.owner, f.page, "Fix the abstract.", status=409)["detail"] == chats.OFFLINE.format(
        computer=computer)                                                 # never queued for a computer away
    nas.down.clear()
    f.turn.once()                                                          # back: announced again, online
    _say(lab.owner, f.page, "Fix the abstract.")
    f.turn.once()
    assert runner.wait_idle(60)
    assert _reply(lab, f.page)["state"] == "done"


def test_a_comment_sent_there_runs_here_and_is_answered_in_its_thread_there(lab, nas, monkeypatch):
    script_agent(monkeypatch, nas.log, edits=[{"path": "main.tex", "old": "Weight Decay", "new": "Weight decay"}])
    f = _made(lab, nas, "RC thread")
    f.turn.once()
    computer_id, computer = registry.computer_of(f.token["id"])
    to = agents.computer_agent(computer_id, "codex").id
    passage = f.threads[0]
    block = next(t for t in lab.owner.get(f"/api/pages/{f.page}/comments").json()["threads"]
                 if t["id"] == passage)["block_id"]
    r = lab.owner.post(f"/api/pages/{f.page}/comments", json={"body": "Define lambda here.", "thread_id": passage,
                                                              "block_id": block, "to": to})
    assert r.status_code == 201, r.text
    turn = r.json()["turn"]
    assert (turn["agent"], turn["state"]) == ("Codex", "queued")
    assert r.json()["thread"]["agent_chat"]["turn"] == {"turn_id": turn["turn_id"], "state": "queued",
                                                        "asking": False, "waiting": False, "computer": computer}
    claimed = _bearer(f.token["token"]).get("/api/computers/chats").json()["turns"]
    assert [t["id"] for t in claimed] == [turn["turn_id"]]
    f.turn.once()
    assert runner.wait_idle(60)

    # the computer has no token there: the turn's end is written in the thread there, signed as its agent
    thread = next(t for t in lab.owner.get(f"/api/pages/{f.page}/comments").json()["threads"] if t["id"] == passage)
    reply = thread["comments"][-1]
    assert (reply["agent"], reply["agent_kind"], reply["agent_id"], reply["agent_on"], reply["body"]) == (
        "Codex", "computer", to, computer, "Done: Define lambda here.")
    assert thread["agent_chat"]["turn"] is None
    # told here: the thread with its source line computed on this disk, the comment as the message
    text, message = _told(nas.log)
    assert message["message"] == "Define lambda here." and message["thread"]["thread_id"] == passage
    assert message["thread"]["file"] == "main.pdf" and message["thread"]["source"]["file"] == "sections/method.tex"
    assert [c["text"] for c in message["thread"]["comments"]] == ["lambda is never introduced", "Define lambda here."]
    assert "Your final message is your reply" in text
    # its region's pictures fetched into the folder and handed to the agent, never in git; what it changed pushed
    named = message["thread"]["pictures"]
    assert named and all(rel.startswith(f"{annotation_files.IMG}/") and (f.root / rel).is_file() for rel in named)
    turn_start = _sent(nas.log, "turn/start")[-1]
    assert [Path(p["path"]).resolve() for p in turn_start["input"] if p.get("type") == "localImage"] == [
        (f.root / rel).resolve() for rel in named]
    assert (f.root / annotation_files.DIR / ".gitignore").read_bytes() == annotation_files.GITIGNORE
    assert "Weight decay" in (f.root / "main.tex").read_text(encoding="utf-8")
    assert nas.pushed and nas.pushed[-1] == (f.id, {"direction": "push"})
    # offline: refused, the comment stored
    registry.reset()
    away = lab.owner.post(f"/api/pages/{f.page}/comments", json={"body": "Again.", "thread_id": passage,
                                                                 "block_id": block, "to": to}).json()
    assert away["turn"]["refused"] == "offline" and away["comment"]["body"] == "Again."


def test_an_at_name_made_there_runs_here_as_a_turn_of_its_thread(lab, nas):
    f = _made(lab, nas, "RC mention")
    f.turn.once()
    r = lab.owner.post(f"/api/pages/{f.page}/comments", json={"block_id": f.page, "body": "@codex tighten the title"})
    assert r.status_code == 201, r.text
    assert r.json()["turn"]["agent"] == "Codex"
    thread_id = r.json()["thread"]["id"]
    f.turn.once()
    assert runner.wait_idle(60)
    reply = _threads(lab, f)[thread_id]["comments"][-1]
    assert (reply["agent"], reply.get("agent_kind"), reply["body"]) == ("Codex", "computer", "Done: tighten the title")


def test_a_threads_pictures_are_the_computers_while_its_run_holds_the_turn(lab, nas):
    f = _made(lab, nas, "RC pictures")
    f.turn.once()
    computer_id, _computer = registry.computer_of(f.token["id"])
    passage, elsewhere = f.threads
    r = lab.owner.post(f"/api/pages/{f.page}/comments", json={
        "body": "This figure.", "thread_id": passage, "block_id": _block_of(lab, f, passage),
        "to": agents.computer_agent(computer_id, "codex").id})
    turn_id = r.json()["turn"]["turn_id"]
    computer, run = _bearer(f.token["token"]), home.chat_run_id(turn_id)
    picture = f"/api/computers/threads/{passage}/picture"
    assert computer.get(picture).status_code == 404                      # not held yet
    assert computer.post(f"/api/computers/chats/{turn_id}/claim", json={"run": run, "agent": "codex"}).status_code == 200
    got = computer.get(picture)
    assert got.status_code == 200 and got.headers["content-type"] == "image/jpeg" and got.content[:2] == b"\xff\xd8"
    assert computer.get(picture, params={"at": "before"}).status_code == 404
    assert computer.get(picture, params={"at": "later"}).status_code == 400
    assert computer.get(f"/api/computers/threads/{elsewhere}/picture").status_code == 404   # another thread
    assert lab.owner.get(picture).status_code == 403                     # a session: not the channel
    laptop = _bearer(_mint(lab.owner)["token"])
    assert laptop.get(picture).status_code == 404                        # another computer holds nothing
    write = f"/api/computers/chats/{turn_id}/write"
    assert computer.post(write, json={"run": run, "seq": 1, "fields": {"state": "done"}}).status_code == 200
    assert computer.get(picture).status_code == 404                      # the turn ended


def test_the_routes_answer_the_maker_alone_and_another_accounts_turn_never_runs_here(lab, nas, anon, guest):
    f = _made(lab, nas, "RC gates")
    f.turn.once()
    assert _chat(lab.owner, f.page)["conversation"] is None
    for status, who in ((404, lab.editor), (401, anon)):
        assert who.get("/api/local-agents/chat", params={"bucket": f.page}).status_code == status
        assert who.post("/api/local-agents/chat/turns", json={"bucket": f.page, "text": "hi"}).status_code == status
        assert who.post("/api/local-agents/chat/new", json={"bucket": f.page}).status_code == status
    # History is the account's own whatever its seat: nothing of the maker's to another
    assert lab.editor.get("/api/local-agents/chat/history", params={"bucket": f.page}).json() == {
        "conversations": [], "active": None, "lent": []}
    assert anon.get("/api/local-agents/chat/history", params={"bucket": f.page}).status_code == 401
    token = create_token(lab.user, lab.ws, "rch-script", 1, scope="write")
    script = _bearer(token["token"])
    script.headers["X-Gamma-Workspace"] = lab.ws
    assert script.get("/api/local-agents/chat", params={"bucket": f.page}).status_code == 403
    assert guest.get("/api/local-agents/chat", params={"bucket": f.page}).status_code == 403
    plain = make_page(lab.owner, "An RC library page")["id"]   # a page of no work folder: a directory of its own here
    assert lab.owner.get("/api/local-agents/chat", params={"bucket": plain}).json()["computer"]["dir"] is None
    # what is the computer's own there: no terminal, no source lines
    assert lab.owner.get("/api/local-agents/chat/terminal", params={"bucket": f.page}).status_code == 404
    r = lab.owner.post("/api/local-agents/chat/sources", json={"bucket": f.page, "selections": [{"text": "x"}]})
    assert r.status_code == 200 and r.json()["sources"] == [None]

    # the channel's chat routes: a computer's token alone, and only its own account's turns
    made = _say(lab.owner, f.page)
    turn_id = made["turn"]["id"]
    assert lab.owner.get("/api/computers/chats").status_code == 403
    assert script.get("/api/computers/chats").status_code == 403
    theirs = _bearer(_mint(lab.editor)["token"])
    assert theirs.get("/api/computers/chats").json()["turns"] == []
    assert theirs.post(f"/api/computers/chats/{turn_id}/claim",
                       json={"run": "rch-run-x", "agent": "codex"}).status_code == 404
    assert lab.editor.post(f"/api/local-agents/chat/turns/{turn_id}/stop").status_code == 404
    mine = _bearer(f.token["token"]).get("/api/computers/chats").json()["turns"]
    assert [t["id"] for t in mine] == [turn_id] and mine[0]["dir"] == {"kind": "vault", "id": f.w}


def test_stop_ends_a_queued_turn_there_and_a_running_one_here(lab, nas, monkeypatch):
    f = _made(lab, nas, "RC stop")
    f.turn.once()
    queued = _say(lab.owner, f.page, "Not yet.")["turn"]["id"]
    assert _stop(lab.owner, queued) == {"stopped": True}                  # queued there: ended at once
    assert _reply(lab, f.page)["state"] == "stopped"
    f.turn.once()
    assert runner.wait_idle(30) and _turn_row(queued) is None              # never run here

    script_agent(monkeypatch, nas.log, delay=30)
    running = _say(lab.owner, f.page, "Take your time.")["turn"]["id"]
    f.turn.once()
    _until(lambda: (row := _turn_row(running)) and row["pid"])
    assert _reply(lab, f.page)["state"] == "running"
    assert _stop(lab.owner, running) == {"stopped": True}                 # its stop set there, this computer woken
    assert _run_there(lab, running)["stop"] and _reply(lab, f.page)["state"] == "running"
    began = time.monotonic()
    f.turn.once()                                                          # the stop heard: the turn stops
    assert runner.wait_idle(30) and time.monotonic() - began < 20         # long before its agent would end
    assert (_reply(lab, f.page)["state"], _turn_row(running)["state"]) == ("stopped", "stopped")


def test_a_write_retried_or_late_never_goes_backwards_and_another_run_is_refused(lab, nas):
    f = _made(lab, nas, "RC seq")
    f.turn.once()
    turn_id = _say(lab.owner, f.page, "Tighten the abstract.")["turn"]["id"]
    computer = _bearer(f.token["token"])
    run = home.chat_run_id(turn_id)
    claim, write = f"/api/computers/chats/{turn_id}/claim", f"/api/computers/chats/{turn_id}/write"
    got = computer.post(claim, json={"run": run, "agent": "codex"})
    assert got.status_code == 200, got.text
    assert got.json()["turn"]["text"] == "Tighten the abstract." and got.json()["about"]["path"]
    assert got.json()["dir"] == {"kind": "vault", "id": f.w} and got.json()["tools"] is None   # no address it reaches
    assert computer.post(claim, json={"run": run, "agent": "codex"}).status_code == 200      # held: told so
    assert computer.post(claim, json={"run": "rch-other", "agent": "codex"}).status_code == 409
    assert computer.post(write, json={"run": run, "seq": 5, "fields": {"text": "newer"}}).json() == {"ok": True}
    assert computer.post(write, json={"run": run, "seq": 3, "fields": {"text": "older"}}).json()["stale"] is True
    assert computer.post(write, json={"run": run, "seq": 5, "fields": {"text": "again"}}).json()["stale"] is True
    assert _reply(lab, f.page)["text"] == "newer"
    assert computer.post(write, json={"run": "rch-other", "seq": 9, "fields": {"text": "theirs"}}).status_code == 409
    changes = ["/etc/passwd", "C:/Users/x.tex", "../up.tex", "notes/ok.tex"]   # never a path beyond the folder
    assert computer.post(write, json={"run": run, "seq": 6, "fields": {"changes": changes}}).status_code == 200
    assert _reply(lab, f.page)["changes"] == ["notes/ok.tex"]
    assert computer.post(write, json={"run": run, "seq": 7, "fields": {"state": "done"}}).status_code == 200
    late = computer.post(write, json={"run": run, "seq": 8, "fields": {"state": "failed", "error": "late"}})
    assert late.status_code == 409 and _reply(lab, f.page)["state"] == "done"   # an end is never undone


def test_a_409_there_stops_the_turn_here(lab, nas, monkeypatch):
    script_agent(monkeypatch, nas.log, chat=" ".join(f"word{n}" for n in range(40)), typing=0.25)
    f = _made(lab, nas, "RC refused")
    f.turn.once()
    turn_id = _say(lab.owner, f.page)["turn"]["id"]
    f.turn.once()
    _until(lambda: _reply(lab, f.page)["text"])                          # its reply is being typed there
    assert chats.stop_run(lab.ws, turn_id, online=lambda holder: False)    # that server ended it (away, it thinks)
    assert runner.wait_idle(30)                                            # the next write refused: the turn stops
    reply = _reply(lab, f.page)
    assert reply["state"] == "stopped" and "word39" not in reply["text"]   # nothing written after the end there
    assert _turn_row(turn_id)["state"] == "stopped"


def test_a_follow_up_resumes_the_session_here_and_a_lost_one_takes_the_earlier_messages(lab, nas):
    f = _made(lab, nas, "RC follow-up")
    f.turn.once()
    conversation_id = _say(lab.owner, f.page, "Shorten the title.")["conversation"]["id"]
    f.turn.once()
    assert runner.wait_idle(60)
    _say(lab.owner, f.page, "And the abstract.")
    f.turn.once()
    assert runner.wait_idle(60)
    assert [p["threadId"] for p in _sent(nas.log, "thread/resume")] == [THREAD]
    text, _ = _told(nas.log)
    assert "Earlier in this conversation" not in text and _reply(lab, f.page)["fresh"] is False

    with connect_vaults_db() as conn:                                     # this computer lost the session
        conn.execute("DELETE FROM agent_conversations WHERE conversation_id = ?", (conversation_id,))
    _say(lab.owner, f.page, "And the conclusion.")
    f.turn.once()
    assert runner.wait_idle(60)
    text, _ = _told(nas.log)
    assert "Earlier in this conversation" in text and "Shorten the title." in text and "And the abstract." in text
    reply = _reply(lab, f.page)
    assert (reply["state"], reply["fresh"]) == ("done", True)


def test_the_model_and_effort_are_checked_there_and_again_here(lab, nas):
    f = _made(lab, nas, "RC model")
    catalog.probe("codex")
    f.turn.once()                                                          # announced with its models
    assert "on this computer" not in _say(lab.owner, f.page, model="no-such-model", status=400)["detail"]
    _say(lab.owner, f.page, model="gpt-fake-mini", effort="high", status=400)   # not that model's effort
    _say(lab.owner, f.page, "Use the small one.", model="gpt-fake-mini", effort="medium")
    f.turn.once()
    assert runner.wait_idle(60)
    reply = _reply(lab, f.page)
    assert (reply["state"], reply["model"], reply["effort"]) == ("done", "gpt-fake-mini", "medium"), reply
    assert _sent(nas.log, "thread/start")[-1]["model"] == "gpt-fake-mini"
    assert _sent(nas.log, "turn/start")[-1]["effort"] == "medium"

    # taken there, but no longer offered here when it runs: it fails here with why
    _say(lab.owner, f.page, "Again.", model="gpt-fake-mini")
    catalog._cache["codex"]["models"] = [m for m in catalog._cache["codex"]["models"] if m["id"] != "gpt-fake-mini"]
    f.turn.once()
    assert runner.wait_idle(60)
    reply = _reply(lab, f.page)
    assert (reply["state"], reply["error"]) == ("failed", "Codex does not offer that model on this computer.")


def test_a_turn_there_asks_nothing_in_the_chat_while_its_link_relays_no_asks(lab, nas):
    f = _made(lab, nas, "RC asks")
    store.set_runs("codex", "ask", by=lab.user)                            # it may ask: on this computer alone, never there
    f.turn.once()
    turn_id = _say(lab.owner, f.page)["turn"]["id"]
    r = lab.owner.post(f"/api/local-agents/chat/turns/{turn_id}/asks/a1", json={"decision": "once"})
    assert (r.status_code, r.json()["detail"]) == (409, "No question of this turn waits.")
    f.turn.once()
    assert runner.wait_idle(60)
    settle_probes()
    reply = _reply(lab, f.page)
    assert reply["state"] == "done" and not reply.get("asks")
    assert _sent(nas.log, "thread/start")[-1]["approvalPolicy"] == "never"


def test_recovery_fails_an_open_turn_there(lab, nas):
    f = _made(lab, nas, "RC recover")
    f.turn.once()
    made = _say(lab.owner, f.page)
    turn_id = made["turn"]["id"]
    computer = _bearer(f.token["token"])
    assert computer.post(f"/api/computers/chats/{turn_id}/claim",
                         json={"run": home.chat_run_id(turn_id), "agent": "codex"}).status_code == 200
    store.claimed_turn(turn_id, link_id=f.turn.link_id, conversation_id=made["conversation"]["id"], page_id=f.page,
                       agent="codex")
    runner.recover()
    _until(lambda: _turn_row(turn_id)["ended_at"])
    reply = _reply(lab, f.page)
    assert (reply["state"], reply["error"]) == ("failed", "Gamma stopped before Codex finished.")
    assert _turn_row(turn_id)["state"] == "failed"


def test_a_turn_whose_end_never_reached_that_server_fails_there_once_it_is_back(lab, nas, monkeypatch):
    f = _made(lab, nas, "RC end lost")
    f.turn.once()
    opened = gamma_sync.default_open

    def losing(method, path, headers, body=None):
        content = body if body is None or isinstance(body, bytes) else b"".join(body)
        if path.endswith("/write") and b'"done"' in (content or b""):
            raise gamma_sync.Unreachable("cannot reach http://testserver: refused")
        return opened(method, path, headers, content)

    monkeypatch.setattr(gamma_sync, "default_open", losing)
    turn_id = _say(lab.owner, f.page)["turn"]["id"]
    f.turn.once()
    assert runner.wait_idle(60)
    assert _reply(lab, f.page)["state"] == "running" and _turn_row(turn_id)["ended_at"] is None   # left open
    monkeypatch.setattr(gamma_sync, "default_open", opened)
    nas.down.append(True)
    assert f.turn.once() == channel.RETRY_FIRST_S
    nas.down.clear()
    f.turn.once()                                                          # back: announced again, the turn failed
    _until(lambda: _turn_row(turn_id)["ended_at"])
    reply = _reply(lab, f.page)
    assert (reply["state"], reply["error"]) == ("failed", "Gamma stopped before Codex finished.")


def test_a_claim_whose_answer_never_came_is_failed_there_once_it_is_reached(lab, nas, monkeypatch):
    f = _made(lab, nas, "RC claim lost")
    f.turn.once()
    opened = gamma_sync.default_open

    def losing(method, path, headers, body=None):
        answer = opened(method, path, headers, body)
        if method == "POST" and path.endswith("/claim"):
            raise gamma_sync.Unreachable("the answer broke off")             # that server took it; no answer came
        return answer

    monkeypatch.setattr(gamma_sync, "default_open", losing)
    turn_id = _say(lab.owner, f.page)["turn"]["id"]
    f.turn.once()
    assert runner.wait_idle(60)
    assert _reply(lab, f.page)["state"] == "running"
    assert _turn_row(turn_id)["ended_at"] is None                         # recorded open, for it to be failed there
    monkeypatch.setattr(gamma_sync, "default_open", opened)
    nas.down.append(True)
    f.turn.once()
    nas.down.clear()
    f.turn.once()
    _until(lambda: _turn_row(turn_id)["ended_at"])
    assert (_reply(lab, f.page)["state"], _reply(lab, f.page)["error"]) == (
        "failed", "Gamma stopped before Codex finished.")


def test_a_stop_there_of_a_turn_left_open_here_ends_it_there_and_here(lab, nas, monkeypatch):
    f = _made(lab, nas, "RC stop open")
    catalog.probes()                                                       # announced as found: nothing to announce again
    f.turn.once()
    opened = gamma_sync.default_open

    def losing(method, path, headers, body=None):
        answer = opened(method, path, headers, body)
        if method == "POST" and path.endswith("/claim"):
            raise gamma_sync.Unreachable("the answer broke off")             # that server took it; no answer came
        return answer

    monkeypatch.setattr(gamma_sync, "default_open", losing)
    turn_id = _say(lab.owner, f.page)["turn"]["id"]
    f.turn.once()
    assert runner.wait_idle(60)
    assert _reply(lab, f.page)["state"] == "running" and _turn_row(turn_id)["ended_at"] is None   # left open
    monkeypatch.setattr(gamma_sync, "default_open", opened)
    assert _stop(lab.owner, turn_id) == {"stopped": True}                 # online there: its stop set
    assert _run_there(lab, turn_id)["stop"]
    f.turn.once()                                                          # the stop heard, no announce: ended
    _until(lambda: _turn_row(turn_id)["ended_at"])
    assert (_reply(lab, f.page)["state"], _turn_row(turn_id)["state"], _run_there(lab, turn_id)["state"]) == (
        "stopped", "stopped", "stopped")
    assert _say(lab.owner, f.page, "Next.")["turn"]["state"] == "queued"   # the page takes a message again


def test_recovery_never_ends_a_turn_claimed_again_here(lab, nas, monkeypatch):
    turn = {"id": "rcTurnS", "link_id": "rcLinkS", "ws": "rcLinkS", "agent": "codex"}
    key, ended, refused = ("rcLinkS", "rcTurnS"), [], []

    def turn_failed(self, t, state="failed"):                             # meanwhile, that server lists the turn
        refused.append(runner.queue_remote_chat("rcLinkS", "rcTurnS", conversation_id="c", bucket="p",
                                                agent="codex", dir={"kind": "own", "id": ""}))
        ended.append(t)
        return True

    monkeypatch.setattr(home.HttpChatHome, "fail_left_open", turn_failed)
    with runner._lock:
        runner._queued[key] = ""                                           # queued here before its end goes
    try:
        runner._fail_remote([turn])
    finally:
        with runner._lock:
            runner._queued.pop(key, None)
    assert ended == []                                                     # queued here: never ended
    runner._fail_remote([turn])                                            # neither queued nor running: ended
    assert refused == [False] and ended == [turn]                          # not queued while it is settled
    assert "rcTurnS" not in runner._settling


def test_a_turn_whose_agent_was_turned_off_while_it_waited_fails_there_with_why(lab, nas):
    f = _made(lab, nas, "RC off")
    f.turn.once()
    made = _say(lab.owner, f.page)
    turn_id = made["turn"]["id"]
    store.set_agent("codex", False, by=lab.user)                           # turned off here, that server not told yet
    assert runner.queue_remote_chat(f.turn.link_id, turn_id, conversation_id=made["conversation"]["id"],
                                    bucket=f.page, agent="codex", dir={"kind": "vault", "id": f.w})
    assert runner.wait_idle(30)
    reply = _reply(lab, f.page)
    assert (reply["state"], reply["error"]) == ("failed", "Codex was turned off on this computer.")
    assert (_turn_row(turn_id)["state"], _run_there(lab, turn_id)["state"]) == ("failed", "failed")


def test_background_tasks_there_list_the_turn_and_its_stop(lab, nas):
    f = _made(lab, nas, "RC tasks")
    f.turn.once()
    turn_id = _say(lab.owner, f.page)["turn"]["id"]
    jobs = lab.owner.get("/api/jobs").json()["jobs"]
    row = next(j for j in jobs if j["id"] == f"computer-{turn_id}")
    assert (row["kind"], row["state"], row["params"]["turn"], row["params"]["bucket"], row["readonly"],
            row["stoppable"]) == ("computer-turn", "queued", turn_id, f.page, True, True)
    assert row["params"]["conversation"] == _chat(lab.owner, f.page)["conversation"]["id"]
    assert "thread" not in row["params"] and "person" not in row["params"]
    assert _stop(lab.owner, turn_id) == {"stopped": True}
    assert all(j["id"] != f"computer-{turn_id}" for j in lab.owner.get("/api/jobs").json()["jobs"])


# --- a co-author's turn (step 3.6 of docs/research/agents-as-participants-plan.md) ------------------

def test_a_co_authors_turn_there_runs_here_for_them_asks_nothing_and_its_owner_reads_it(lab, nas, monkeypatch):
    from gamma import remote_servers

    f = _made(lab, nas, "RC co-author")
    editor = account_of(EDITOR)
    monkeypatch.setattr(remote_servers, "plain", lambda url: "")                # its owner's turns there may ask
    assert lab.owner.patch("/api/local-agents/codex", json={"runs": "ask"}, headers=SHELL).status_code == 200
    f.turn.once()
    assert _chat(lab.editor, f.page, 404)                                       # not named yet: no seat
    named = {"server": nas.url, "workspace_id": lab.ws, "account": editor}
    r = lab.owner.post("/api/local-agents/codex/people", json=named, headers=SHELL)
    assert r.status_code == 200, r.text
    f.turn.once()                                                              # announced with the person

    computer_id, computer = registry.computer_of(f.token["id"])
    strip = _chat(lab.editor, f.page)["computer"]
    assert (strip["agent"], strip["remote"], strip["computer"], strip["lent"], strip["grants"]) == (
        "codex", True, computer, {"by": OWNER}, [])
    made = _say(lab.editor, f.page, "Lower the title's case.")
    conversation_id = made["conversation"]["id"]
    rows = {j["id"]: j for j in lab.owner.get("/api/jobs").json()["jobs"]}     # its owner's Background tasks list it
    row = rows[f"computer-{made['turn']['id']}"]
    assert (row["params"]["conversation"], row["params"]["person"], "thread" in row["params"]) == (
        conversation_id, EDITOR, False)
    f.turn.once()                                                              # heard, claimed for the editor, run
    assert runner.wait_idle(60)
    messages = _chat(lab.editor, f.page)["conversation"]["messages"]
    assert [m["state"] for m in messages if m["role"] == "agent"] == ["done"]
    text, message = _told(nas.log)
    assert message["message"] == "Lower the title's case." and f"message {EDITOR} wrote" in text
    assert _sent(nas.log, "thread/start")[-1]["approvalPolicy"] == "never"       # a co-author's turn there asks nothing
    # the editor's conversation: read by the owner too, never the owner's own
    assert _chat(lab.owner, f.page)["conversation"] is None
    r = lab.owner.get(f"/api/local-agents/chat/history/{conversation_id}/export")
    assert r.status_code == 200 and "Lower the title" in r.text
    assert lab.owner.post("/api/local-agents/chat/history/" + conversation_id + "/open").status_code == 404
    assert lab.owner.delete(f"/api/local-agents/chat/history/{conversation_id}").status_code == 404
    # listed in the owner's History under the editor's name, and read there in place of the owner's own
    listed = lab.owner.get("/api/local-agents/chat/history", params={"bucket": f.page}).json()
    assert [(c["id"], c["person"]) for c in listed["lent"]] == [(conversation_id, EDITOR)]
    assert lab.editor.get("/api/local-agents/chat/history", params={"bucket": f.page}).json()["lent"] == []
    seen = _chat(lab.owner, f.page, conversation=conversation_id)
    assert (seen["conversation"]["id"], seen["conversation"]["person"], seen["computer"]["session"]) == (
        conversation_id, EDITOR, False)
    assert _chat(lab.editor, f.page, conversation=conversation_id)["conversation"] is None  # their own: the chat's

    # a comment of the editor's sent to the computer's agent: answered in the thread as theirs, its owner named
    to = agents.computer_agent(computer_id, "codex").id
    passage = f.threads[0]
    block = next(t for t in lab.owner.get(f"/api/pages/{f.page}/comments").json()["threads"]
                 if t["id"] == passage)["block_id"]
    r = lab.editor.post(f"/api/pages/{f.page}/comments", json={"body": "Define lambda here.", "thread_id": passage,
                                                               "block_id": block, "to": to})
    assert r.status_code == 201 and r.json()["turn"]["state"] == "queued", r.text
    row = {j["id"]: j for j in lab.owner.get("/api/jobs").json()["jobs"]}[f"computer-{r.json()['turn']['turn_id']}"]
    assert (row["params"]["thread"], row["params"]["person"]) == (passage, EDITOR)  # it opens the thread's conversation
    f.turn.once()
    assert runner.wait_idle(60)
    threads = {t["id"]: t for t in lab.owner.get(f"/api/pages/{f.page}/comments").json()["threads"]}
    reply = threads[passage]["comments"][-1]
    assert (reply["agent"], reply["author"], reply["agent_on"], reply["agent_owner"], reply["body"]) == (
        "Codex", EDITOR, computer, OWNER, "Done: Define lambda here.")
    # the thread's chip is the owner's to read too, and its conversation, whose it is
    chat = threads[passage]["agent_chat"]
    assert (chat["for"], chat["turn"]) == (EDITOR, None)
    seen = _chat(lab.owner, f.page, thread=passage)["conversation"]
    assert seen["thread_id"] == passage and seen["id"] != conversation_id

    # taken off on the computer: the next announce fails the turn that waits for it there
    r = lab.owner.delete("/api/local-agents/codex/people", params={k: v for k, v in named.items()})
    assert r.status_code == 200 and r.json() == {"people": []}
    waiting = _say(lab.editor, f.page, "And the abstract.")["turn"]["id"]
    f.turn.once()
    assert _run_there(lab, waiting)["state"] == "failed"
    assert _chat(lab.editor, f.page, 404)


# --- any bucket there (step 4.2 of docs/research/agents-as-participants-plan.md) -----------------

def _turn_tokens(user: str) -> list[tuple]:
    """``(id, name, workspace, expires_at)`` of the account's turn tokens."""
    with connect_users_db() as conn:
        return conn.execute("SELECT id, name, workspace_id, expires_at FROM integration_tokens WHERE user_id = ? "
                            "AND scope = ?", (user, integrations.TURN)).fetchall()


def test_a_page_in_no_synced_folder_runs_here_in_a_directory_of_its_own_or_one_offered(lab, nas):
    f = _made(lab, nas, "RC any page")
    f.turn.once()
    computer_id, computer = registry.computer_of(f.token["id"])
    far = agents.computer_agent(computer_id, "codex").id
    plain = make_page(lab.owner, "RC loose notes")["id"]
    assert [(c["name"], c["vault"]) for c in lab.owner.get(f"/api/pages/{plain}/computer").json()["computers"]] == [
        (computer, None)]
    strip = _chat(lab.owner, plain, agent=far)["computer"]
    assert (strip["remote"], strip["computer"], strip["dir"], strip["folder_id"], strip["dirs"]) == (
        True, computer, None, None, [])
    made = _say(lab.owner, plain, "Summarize the notes.", agent=far)
    f.turn.once()
    assert runner.wait_idle(60)
    reply = _reply(lab, plain)
    assert (reply["state"], reply["text"]) == ("done", "Done: Summarize the notes."), reply
    own = config.AGENTS_DIR / f.turn.link_id / made["conversation"]["id"]
    assert Path(store.conversation_of(made["conversation"]["id"])["cwd"]) == own and own.is_dir()
    text, message = _told(nas.log)
    assert "an empty directory of your own" in text and (message["about"]["kind"], message["about"]["id"]) == (
        "page", plain)

    # a directory this computer offers: announced by id and name, picked for a new conversation, worked in here
    drafts = nas.root / "RC offered drafts"
    drafts.mkdir(parents=True)
    [offered] = store.set_dirs([{"path": str(drafts), "name": "Drafts"}])
    f.turn.once()
    assert _chat(lab.owner, plain, agent=far)["computer"]["dirs"] == [{"id": offered["id"], "name": "Drafts"}]
    assert lab.owner.post("/api/local-agents/chat/new", json={"bucket": plain}).status_code == 200
    assert _say(lab.owner, plain, agent=far, dir="not-offered", status=400)["detail"] == (
        f"No directory offered on {computer} has that id.")
    made = _say(lab.owner, plain, "List the drafts.", agent=far, dir=offered["id"])
    f.turn.once()
    assert runner.wait_idle(60)
    assert _reply(lab, plain)["state"] == "done"
    assert Path(store.conversation_of(made["conversation"]["id"])["cwd"]) == drafts
    assert _chat(lab.owner, plain)["computer"]["dir"] == {"kind": "offered", "id": offered["id"], "name": "Drafts"}
    # this computer's own agent is another agent: a conversation keeps the computer it began with
    assert _chat(lab.owner, plain, agent="codex")["conversation"] is None
    # the directory no longer offered: the next message, naming none, archives that conversation and begins one
    # in a directory of its own; one that names it is refused
    store.set_dirs([])
    f.turn.once()
    assert _say(lab.owner, plain, agent=far, dir=offered["id"], status=400)["detail"] == (
        f"No directory offered on {computer} has that id.")
    again = _say(lab.owner, plain, "And the rest.", agent=far)
    f.turn.once()
    assert runner.wait_idle(60)
    assert _reply(lab, plain)["state"] == "done"
    with connect_pages_db(lab.ws) as conn:
        rows = dict(conn.execute("SELECT id, dir FROM computer_chats WHERE id IN (?, ?) AND archived_at IS NULL",
                                 (made["conversation"]["id"], again["conversation"]["id"])).fetchall())
    assert rows == {again["conversation"]["id"]: ""}


def test_a_conversations_directory_no_longer_kept_or_offered_gives_one_of_its_own():
    far = {"folder_id": "", "lent_by": "", "computer_id": "c1", "computer": "TIM-PC", "agent": "codex",
           "dirs": [{"id": "d1", "name": "Drafts"}]}
    kept_there = {"computer": "c1", "agent": "codex", "dir": "W-no-longer-kept"}
    assert chats.far_dir(far, None, kept_there) == ""                   # begin archives it: a new one starts
    assert chats.far_dir(far, None, {**kept_there, "dir": "d1"}) == "d1"   # still offered: kept
    assert chats.far_dir(far, None, {**kept_there, "computer": "c2", "dir": "d1"}) == ""
    assert chats.far_dir(far, None, None) == ""
    with pytest.raises(chats.ChatError) as refused:
        chats.far_dir(far, "W-no-longer-kept", kept_there)
    assert refused.value.status == 400
    assert chats.far_dir({**far, "folder_id": "W"}, None, kept_there) == "W"


def test_a_claim_hands_the_turn_a_token_of_its_own_kept_by_its_writes_and_gone_at_its_end(lab, nas, monkeypatch):
    f = _made(lab, nas, "RC tokens")
    f.turn.once()
    monkeypatch.setattr(computer_routes, "_mcp_url", lambda request: f"{nas.url}/mcp")
    minted, real = [], integrations.mint_turn
    monkeypatch.setattr(integrations, "mint_turn", lambda *args: minted.append(args) or real(*args))
    turn_id = _say(lab.owner, f.page, "Tighten the title.")["turn"]["id"]
    computer, run = _bearer(f.token["token"]), home.chat_run_id(turn_id)
    claim, write = f"/api/computers/chats/{turn_id}/claim", f"/api/computers/chats/{turn_id}/write"
    first = computer.post(claim, json={"run": run, "agent": "codex"}).json()["tools"]
    assert first["url"] == f"{nas.url}/mcp" and first["token"].startswith("gamma_")
    agent_id = agents.computer_agent(registry.computer_of(f.token["id"])[0], "codex").id
    assert minted == [(lab.user, lab.ws, agent_id, chats.TOKEN_S)]       # the account's, its workspace's, the agent's
    again = computer.post(claim, json={"run": run, "agent": "codex"}).json()["tools"]
    [(held, name, ws, _expires)] = _turn_tokens(lab.user)                 # a retry: a new one in place of the first
    assert again["token"] != first["token"] and (name, ws) == (agent_id, lab.ws)
    with connect_pages_db(lab.ws) as conn:
        assert chats.run_of(conn, turn_id)["token"] == held
    # the run's writes keep it alive, its end deletes it
    with connect_users_db() as conn:
        conn.execute("UPDATE integration_tokens SET expires_at = ? WHERE id = ?", (int(time.time()) + 5, held))
    assert computer.post(write, json={"run": run, "seq": 1, "fields": {"text": "working"}}).status_code == 200
    assert _turn_tokens(lab.user)[0][3] >= time.time() + chats.TOKEN_S - 5
    assert computer.post(write, json={"run": run, "seq": 2, "fields": {"state": "done"}}).status_code == 200
    assert _turn_tokens(lab.user) == []
    with connect_pages_db(lab.ws) as conn:
        assert chats.run_of(conn, turn_id)["token"] == ""
    # a Stop that ends it here (its computer away) deletes it too, and so does the computer's channel taken back
    for end in ("stop", "revoke"):
        turn_id = _say(lab.owner, f.page, f"Again, {end}.")["turn"]["id"]
        assert computer.post(f"/api/computers/chats/{turn_id}/claim",
                             json={"run": home.chat_run_id(turn_id), "agent": "codex"}).status_code == 200
        assert len(_turn_tokens(lab.user)) == 1
        if end == "stop":
            registry.reset()
            assert _stop(lab.owner, turn_id) == {"stopped": True}
            f.turn.once()                                                  # asked to announce again
            f.turn.once()
        else:
            assert lab.owner.delete(f"/api/integrations/tokens/{f.token['id']}").status_code == 200
        assert _turn_tokens(lab.user) == [] and _run_there(lab, turn_id)["state"] in ("stopped", "failed")


def test_a_far_agent_takes_that_servers_tools_at_its_own_address_alone_and_comments_as_itself(lab, nas, monkeypatch):
    """With the channel at the MCP server's own origin (a loopback socket of
    this test's data), the agent here reads and comments with the turn's
    token as Codex on this computer; an address at another origin is never
    given the token, and the turn says why."""
    monkeypatch.setattr(annotation_files, "connect_pages_db", connect_pages_db)   # that server's own writes
    script_agent(monkeypatch, nas.log, gamma=[{"tool": "comment", "arguments": {"page_id": "$page",
                                                                                "body": "Which title do you want?"}}])
    with serve_mcp() as url:
        origin = url.rsplit("/mcp", 1)[0]
        assert lab.owner.patch("/api/local-agents/codex", json={"enabled": True}, headers=SHELL).status_code == 200
        item = _mint(lab.owner)
        r = lab.owner.post("/api/computer-links", json={"remote_url": origin, "workspace": lab.ws,
                                                        "token": item["token"], "token_id": item["id"]}, headers=SHELL)
        assert r.status_code == 201, r.text
        turn = channel._Channel(r.json()["link"]["id"])
        turn.once()
        computer_id, computer = registry.computer_of(item["id"])
        far = agents.computer_agent(computer_id, "codex").id
        plain = make_page(lab.owner, "RC tool notes")["id"]

        monkeypatch.setattr(computer_routes, "_mcp_url", lambda request: "https://elsewhere.example/mcp")
        _say(lab.owner, plain, "Ask me about the title.", agent=far)
        turn.once()
        assert runner.wait_idle(60)
        reply = _reply(lab, plain)
        assert reply["state"] == "done" and reply["text"].startswith(
            home.FAR_TOOLS.format(origin="https://elsewhere.example", server=origin))
        assert not [e for e in read_log(nas.log) if "gamma" in e] and _turn_tokens(lab.user) == []

        monkeypatch.setattr(computer_routes, "_mcp_url", lambda request: url)
        _say(lab.owner, plain, "Ask me again.", agent=far)
        turn.once()
        assert runner.wait_idle(60)
        called = [e for e in read_log(nas.log) if "gamma" in e][-1]
        assert (called["gamma"], called["http"]) == ("comment", 200), called
        [asked] = lab.owner.get(f"/api/pages/{plain}/comments").json()["threads"]
        said = asked["comments"][0]
        assert (said["body"], said["agent_kind"], said["agent_id"], said["agent_on"], said["author"]) == (
            "Which title do you want?", "computer", far, computer, OWNER)
        assert _reply(lab, plain)["state"] == "done" and _turn_tokens(lab.user) == []


# --- sessions started here, asked there over the channel -----------------------------------------

@contextmanager
def _answering(f):
    """The computer's channel taking turn after turn in the background, as
    its thread does, while a request there waits for what it answers."""
    stop = threading.Event()

    def run():
        while not stop.is_set():
            f.turn.once()
            time.sleep(0.02)

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    try:
        yield
    finally:
        stop.set()
        thread.join(30)


def _sessions(who, agent, dir, status=200) -> dict:
    r = who.get("/api/local-agents/sessions", params={"agent": agent, "dir": dir})
    assert r.status_code == status, r.text
    return r.json()


def _open(who, bucket, agent, dir, session, status=200) -> dict:
    r = who.post("/api/local-agents/sessions/open", json={"bucket": bucket, "agent": agent, "dir": dir,
                                                          "session": session})
    assert r.status_code == status, r.text
    return r.json()


def _with_sessions(lab, nas, monkeypatch, tmp_path, name: str):
    """A work folder kept here, shown there, whose Codex keeps a person's
    threads of its directory (test_local_agents_extras.py ``_codex_store``)
    → ``(f, the agent's id there)``."""
    f = _made(lab, nas, name)
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex"))
    script_agent(monkeypatch, nas.log, sessions=_codex_store(f.root, tmp_path / "elsewhere"))
    f.turn.once()
    computer_id, _computer = registry.computer_of(f.token["id"])
    return f, agents.computer_agent(computer_id, "codex").id


def _listings(log) -> int:
    return len([m for m in requests_in(log) if m.get("method") == "thread/list"])


def test_history_there_lists_the_computers_sessions_for_its_owner_alone(lab, nas, monkeypatch, tmp_path, anon):
    """History's On TIM-PC on that server: the threads Codex keeps here of
    the work folder's directory, asked over the channel for the computer's
    own account alone (a co-author it lends Codex to asks none), by the
    directory's id, never a path; the answer kept a while, so History
    opened again asks this computer nothing."""
    f, far = _with_sessions(lab, nas, monkeypatch, tmp_path, "RC sessions")
    with _answering(f):
        listed = _sessions(lab.owner, far, f.w)
        assert "away" not in listed
        assert [(s["id"], s["title"], s["source"], s["ours"], s["busy"]) for s in listed["sessions"]] == [
            ("019b-term-fix", "Fix the abstract", "cli", False, False),
            ("019b-term-script", "summarize the method", "exec", False, False),
            ("019b-gamma-old", "Shorten the title", "gamma", False, False)]
        asked = _listings(nas.log)
        assert _sessions(lab.owner, far, f.w) == listed                         # kept: this computer not asked
        assert _listings(nas.log) == asked

        # the computer's own account alone, for a directory it keeps or offers there
        store.name_person("codex", nas.url, lab.ws, account_of(EDITOR), EDITOR)
        f.turn.once()
        assert lab.editor.get("/api/local-agents/sessions", params={"agent": far, "dir": f.w}).status_code == 404
        assert anon.get("/api/local-agents/sessions", params={"agent": far, "dir": f.w}).status_code == 401
        for agent, dir in ((far, "not-offered"), (far, str(f.root)), (far, ""),
                           (far.replace(":codex", ":claude"), f.w), (far.replace(":codex", ":gemini"), f.w)):
            _sessions(lab.owner, agent, dir, 404)
        assert _listings(nas.log) == asked                                       # none of them asked here
    store.unname_person("codex", nas.url, lab.ws, account_of(EDITOR))

    # this computer answers that channel's account alone, in a directory named by an id it offers
    link = channel.get(f.turn.link_id)
    refused = [({"kind": "offered", "id": "nope"}, "codex", home.NOT_OFFERED),
               ({"kind": "vault", "id": str(f.root)}, "codex", home.NO_SUCH_DIR),   # a path, never
               ({"kind": "own", "id": ""}, "codex", home.NO_SUCH_DIR),
               ({"kind": "vault", "id": f.w}, "claude", sessions.FAR_OFF)]
    for dir, agent, why in refused:
        assert sessions.answer_far(link, {"kind": "list", "agent": agent, "dir": dir}) == {"error": why,
                                                                                          "status": 409}


def test_a_session_opened_there_is_a_conversation_whose_next_far_turn_resumes_it_here(lab, nas, monkeypatch,
                                                                                      tmp_path):
    """Opened there: a conversation of that server with this computer's
    agent, holding the session's turns as this computer read them (paths
    relative, no thinking), bound here to the session, so the next far turn
    resumes the thread and tells the agent nothing of what the session
    holds; opened again, that conversation; a session this computer names
    as a conversation that server does not have opened afresh; one another
    Codex holds refused with why."""
    from fake_agents.hold_lock import hold

    f, far = _with_sessions(lab, nas, monkeypatch, tmp_path, "RC open")
    with _answering(f):
        _open(lab.owner, "home", far, f.w, "019b-term-fix", 400)                  # the library's chat works elsewhere
        _open(lab.owner, f.page, far, f.w, "019b-elsewhere", 404)                 # another directory's
        _open(lab.owner, f.page, far, f.w, "x; rm -rf ~", 404)
        opened = _open(lab.owner, f.page, far, f.w, "019b-term-fix")["conversation"]
        assert (opened["title"], opened["agent"], opened["agent_id"]) == ("Fix the abstract", "codex", far)
        assert [(m["role"], m["text"], m.get("state"), [s["kind"] for s in m.get("steps") or []], m["from_session"])
                for m in opened["messages"]] == [
            ("user", "fix the abstract's typo", None, [], True),
            ("agent", "Looking at main.tex.\n\nFixed it in main.tex:9.", "done", ["run", "edit"], True),
            ("user", "and the title", None, [], True),
            ("agent", "", "stopped", [], True)]
        assert opened["messages"][1]["steps"][1] == {"id": "f1:0", "kind": "edit", "status": "completed",
                                                     "path": "main.tex"}
        raw = json.dumps(opened)
        assert "secret" not in raw and json.dumps(str(f.root))[1:-1] not in raw
        assert _chat(lab.owner, f.page)["conversation"]["id"] == opened["id"]
        ran = store.conversation_of(opened["id"])
        assert (ran["session"], ran["agent"], ran["link_id"], Path(ran["cwd"])) == ("019b-term-fix", "codex", f.id,
                                                                                    f.root)
        listed = _sessions(lab.owner, far, f.w)["sessions"]
        assert [(s["id"], s["ours"], s["title"]) for s in listed][:1] == [("019b-term-fix", True, "Fix the abstract")]
        assert _open(lab.owner, f.page, far, f.w, "019b-term-fix")["conversation"]["id"] == opened["id"]

        # bound here to a conversation that server does not have (gone there): opened as a new one, which
        # alone resumes it from then on
        store.ran_conversation("rcGoneConv1", link_id=f.id, agent="codex", session="019b-term-script", cwd=str(f.root))
        again = _open(lab.owner, f.page, far, f.w, "019b-term-script")["conversation"]
        assert again["id"] not in (opened["id"], "rcGoneConv1")
        assert store.bound("codex", ["019b-term-script"]) == {"019b-term-script": again["id"]}
        assert store.conversation_of("rcGoneConv1")["session"] == ""

        # held by another Codex here: refused with why
        with hold(str(tmp_path / "codex" / "thread-writer-locks" / "019b-gamma-old.lock")):
            assert [s["busy"] for s in _sessions(lab.owner, far, f.w)["sessions"] if s["id"] == "019b-gamma-old"] == [
                True]
            assert _open(lab.owner, f.page, far, f.w, "019b-gamma-old", 409)["detail"] == (
                "Codex has this session open in another window: close it there first.")
        assert _open(lab.owner, f.page, far, f.w, "019b-term-fix")["conversation"]["id"] == opened["id"]

        # bound to a co-author's conversation that server has: refused, never taken from it
        computer_id = registry.computer_of(f.token["id"])[0]
        theirs = chats.adopt(lab.ws, bucket=f.page, dir=f.w, user_id=account_of(EDITOR), agent="codex",
                             label="Codex", title="bob's", messages=[], computer=computer_id)
        store.ran_conversation(theirs["id"], link_id=f.id, agent="codex", session="019b-gamma-old", cwd=str(f.root))
        assert _open(lab.owner, f.page, far, f.w, "019b-gamma-old", 409)["detail"] == (
            "Another chat of Gamma's continues this session.")
        assert store.bound("codex", ["019b-gamma-old"]) == {"019b-gamma-old": theirs["id"]}
        assert store.conversation_of(theirs["id"])["session"] == "019b-gamma-old"

        # the bucket's active conversation at work: refused before this computer is asked to bind anything
        with connect_pages_db(lab.ws) as conn:
            active = chats.get(conn, opened["id"])
            conn.execute("UPDATE computer_chats SET messages = ? WHERE id = ?", (json.dumps(
                [*active["messages"], {"role": "agent", "state": "running", "text": ""}]), opened["id"]))
        assert _open(lab.owner, f.page, far, f.w, "019b-term-script", 409)["detail"] == (
            "Codex is still working: stop it first.")
        assert store.bound("codex", ["019b-term-script"]) == {"019b-term-script": again["id"]}
        with connect_pages_db(lab.ws) as conn:
            conn.execute("UPDATE computer_chats SET messages = ? WHERE id = ?",
                         (json.dumps(active["messages"]), opened["id"]))

    # the next far turn resumes the thread here, telling nothing of what the session holds
    _say(lab.owner, f.page, "Now the conclusion.")
    f.turn.once()
    assert runner.wait_idle(60)
    assert [p["threadId"] for p in _sent(nas.log, "thread/resume")] == ["019b-term-fix"]
    text, message = _told(nas.log)
    assert message["message"] == "Now the conclusion." and "fix the abstract" not in text
    assert _reply(lab, f.page)["state"] == "done"
    # the session lost here: a turn afresh is told none of the session's messages either
    with connect_vaults_db() as conn:
        conn.execute("DELETE FROM agent_conversations WHERE conversation_id = ?", (opened["id"],))
    _say(lab.owner, f.page, "And the references.")
    f.turn.once()
    assert runner.wait_idle(60)
    text, _ = _told(nas.log)
    assert "fix the abstract" not in text and "Now the conclusion." in text


def test_history_there_says_why_this_computer_answers_nothing(lab, nas, monkeypatch, tmp_path):
    """No answer within the wait says so, and the answer that comes later is
    what History opened again reads; a release before sessions were asked
    over the channel (its announce without ``sessions``) needs an update;
    offline, the section offers nothing and says so."""
    f, far = _with_sessions(lab, nas, monkeypatch, tmp_path, "RC away sessions")
    monkeypatch.setattr(computer_sessions, "LIST_WAIT_S", 0.3)
    assert _sessions(lab.owner, far, f.w) == {"sessions": [], "away": "waiting"}   # the computer took no turn
    [waiting] = computer_sessions._asks.values()
    assert waiting.body == {"kind": "list", "agent": "codex", "dir": {"kind": "vault", "id": f.w}}
    f.turn.once()                                                                # woken: it answers now
    _until(lambda: not computer_sessions._asks)
    asked = _listings(nas.log)
    assert [s["id"] for s in _sessions(lab.owner, far, f.w)["sessions"]] == [
        "019b-term-fix", "019b-term-script", "019b-gamma-old"]
    assert _listings(nas.log) == asked

    state = {k: v for k, v in channel.state_of(channel.get(f.turn.link_id)).items() if k != "sessions"}
    assert _bearer(f.token["token"]).post("/api/computers/announce", json=state).status_code == 200
    computer = registry.computer_of(f.token["id"])[1]
    computer_sessions.reset()
    assert _sessions(lab.owner, far, f.w) == {"sessions": [], "away": "older"}
    assert _open(lab.owner, f.page, far, f.w, "019b-term-fix", 409)["detail"] == (
        f"{computer} needs a newer Gamma to open its sessions here.")

    registry.reset()                                                              # that server sees it offline
    assert _sessions(lab.owner, far, f.w) == {"sessions": [], "away": "offline"}
    assert _open(lab.owner, f.page, far, f.w, "019b-term-fix", 409)["detail"] == chats.OFFLINE.format(
        computer=computer)
    assert not computer_sessions._asks


def test_a_computers_answer_of_sessions_is_its_own_asks_and_kept_in_shape(lab, nas, monkeypatch, tmp_path):
    """The channel's routes: an ask taken once by the token it was asked
    of, answered by it alone, every answer checked and capped as a turn's
    writes are (no path, nothing loading where it is drawn)."""
    f, _far = _with_sessions(lab, nas, monkeypatch, tmp_path, "RC answers")
    computer = _bearer(f.token["token"])
    other = _bearer(_mint(lab.owner)["token"])
    assert lab.owner.get("/api/computers/sessions").status_code == 403         # a session: never the channel's
    with computer_sessions._lock:
        ask = computer_sessions._new(f.token["id"], {"kind": "open", "agent": "codex",
                                                     "dir": {"kind": "vault", "id": f.w}, "session": "019b-term-fix",
                                                     "conversation_id": "rcConv1", "rebind": ""})
    assert other.get("/api/computers/sessions").json() == {"asks": []}
    [taken] = computer.get("/api/computers/sessions").json()["asks"]
    assert taken["id"] == ask.id and computer.get("/api/computers/sessions").json() == {"asks": []}   # once
    answer = {"title": "x" * 300, "messages": [
        {"role": "user", "text": "  <img src=x onerror=alert(1)>  ", "at": "2026-11-14T10:00:00.000Z"},
        {"role": "agent", "text": "Done.", "state": "running", "at": 5,
         "steps": [{"id": "e1", "kind": "edit", "status": "completed", "path": "C:\\Users\\tim\\main.tex"}]}]}
    assert other.post(f"/api/computers/sessions/{ask.id}", json=answer).status_code == 404
    assert computer.post(f"/api/computers/sessions/{ask.id}", json={"messages": "no"}).status_code == 400
    assert computer.post(f"/api/computers/sessions/{ask.id}", json=answer).json() == {"ok": True}
    assert computer.post(f"/api/computers/sessions/{ask.id}", json=answer).status_code == 404   # answered
    kept = ask.answer
    assert len(kept["title"]) == chats.TITLE_CHARS
    user, agent = kept["messages"]
    assert (user["role"], user["from_session"], user["text"].startswith("<img")) == ("user", True, False)
    assert (agent["state"], agent["label"], agent["steps"][0]["path"], agent["at"] != 5) == (
        "done", "Codex", "outside", True)
