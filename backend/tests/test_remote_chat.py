"""The chat's computer side on a page of another server's work folder (step
4d of docs/research/agent-collab.md): the computer's side
(gamma/local_agents/home.py ``HttpChatHome``, runner.py ``queue_remote_chat``,
``stop_remote_chat`` and ``_ChatTurn`` through its home, computers/channel.py
``_chats`` and the announce's ``models``) against that server's chat routes
(gamma/routers/local_agents.py for a page whose agent runs on another
computer, gamma/routers/computers.py ``/computers/chats``).

One process plays both, as tests/test_remote_sends.py does: the NAS is a
TestClient of its own reached through ``gamma_sync.default_open``, the
computer this server as the desktop app runs it, with the stand-in Codex
(tests/fake_agents/). This computer's own in-process reads and writes of the
NAS's workspace fail (``far``), its conversation's too, so a turn that read
or wrote the wrong pages.db fails though both share one data directory.

The announce naming each assistant's models; the channel queueing the turns
that server gives and stopping the ones stopped there; a turn queued once,
a stop there taking a queued one out; the writes rising in ``seq``, a lost
one carried by the next, the last tried again, a 409 ending them; the
maker's message on the NAS run here, its reply, steps and changed files
written back there, the source line of its passage computed here; the
conversation read while the computer is away and a message waiting for it;
another account's turn never run; the routes' gates; Stop of a queued and a
running turn; a write retried or late never going backwards; a 409 stopping
the turn; a follow-up resuming the session here, a lost one taking the
earlier messages along; the model and effort checked there and here; a turn
there never asking in the chat (test_remote_asks.py asks on this computer);
recovery failing an open turn there, and the channel's next announce one
whose end, or whose claim, never reached it, never one claimed again here; a Stop there of a turn left open here ending it; a turn
whose agent was turned off while it waited failing there with why; the
watch of a turn there never waiting on a write; Background tasks there.
The local chat is test_local_agents_chat.py's."""

import io
import json
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from conftest import account_of, fresh_client, login, make_page, make_user
from fake_agents import requests_in, script_agent, settle_probes, use_fakes
from gamma import folder_links, gamma_sync, pdf_meta, storage, workspaces
from gamma.computers import channel, registry
from gamma.db import connect_pages_db, connect_users_db, connect_vaults_db
from gamma.feedback import files as feedback_files
from gamma.integrations import create_token
from gamma.local_agents import catalog, chats, codex, events, home, info, procs, runner, store
from gamma.vault import kinds, store as vault_store
from gamma.work_folders import selection
from test_remote_sends import SECRET, _bearer, _made, _mint, _until

OWNER, EDITOR = "rch_owner", "rch_editor"
PASSWORD = "rch-pass-1234"
PLACES = json.loads((Path(__file__).parent / "samples" / "synctex" / "paper" / "places.json").read_text(
    encoding="utf-8"))
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
    """test_remote_sends' ``nas``: this server as the desktop app runs it,
    the NAS a TestClient reached through ``gamma_sync.default_open``
    (``down`` makes it unreachable), polls held no time, no channel thread,
    a remote turn's message written at most every 0.2 s, and this
    computer's in-process reads and writes of the NAS's workspace refused,
    its conversations' included."""
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

    for module in (folder_links, selection, feedback_files, runner, home, info):
        monkeypatch.setattr(module, "connect_pages_db", guarded(module.connect_pages_db))
    for name in ("reply_of", "write_reply", "sources_of"):
        monkeypatch.setattr(chats, name, guarded(getattr(chats, name)))
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
        for table in ("agent_runs", "agent_folders", "agent_threads", "agent_policies", "agent_turns",
                      "agent_conversations"):
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

def _chat(who, page, status=200, **params) -> dict:
    r = who.get("/api/local-agents/chat", params={"page": page, **params})
    assert r.status_code == status, r.text
    return r.json()


def _say(who, page, text="Shorten the title.", status=202, **body) -> dict:
    r = who.post("/api/local-agents/chat/turns", json={"page": page, "text": text, **body})
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
                        lambda ids, turn_id, run: stopped.append((ids, turn_id, run)) or turn_id == "t9")
    monkeypatch.setattr(runner, "end_stopped_remote", lambda ids, turn_id, run: ended.append((ids, turn_id, run)))
    answer = {"turns": [{"id": "t1", "conversation_id": "c1", "page_id": f.page, "folder_id": f.w, "at": "x"},
                        {"id": "t2", "conversation_id": "c2", "page_id": f.page, "folder_id": "elsewhere"},
                        {"id": "../t3", "folder_id": f.w}, {"id": 5, "folder_id": f.w}, "junk"],
              "stops": [{"id": "t0", "run": "run-0"}, {"id": "t9", "run": "run-9"}, {"id": ""}, {"id": "../t8"}]}
    link = channel.get(f.turn.link_id)
    server = _Server([(200, answer)])
    f.turn._chats(server, link)
    assert server.sent == [("GET", "/api/computers/chats", None)]
    assert queued == [(f.id, "t1", {"agent": "codex", "conversation_id": "c1", "page_id": f.page})]
    assert stopped == [([f.id], "t0", "run-0"), ([f.id], "t9", "run-9")] and f.turn.chats_due is False
    assert ended == [([f.id], "t0", "run-0")]                            # no vault here queues or runs it: ended
    # a vault with no agent asks nothing at all
    store.set_agent(f.id, "", by=lab.user)
    f.turn._chats(_Server(), link)
    store.set_agent(f.id, "codex", by=lab.user)
    # a server from before the chat ran there (404, or 403: its channel opens the Sends alone): asked again later
    for status in (404, 403):
        f.turn.chats_due = True
        f.turn._chats(_Server([(status, {"detail": "no"})]), link)
        assert f.turn.chats_due is False
    # that server not reached: asked at the next turn
    f.turn._chats(_Server([(None, None)]), link)
    assert f.turn.chats_due is True


def test_a_turn_there_is_queued_once_and_a_stop_there_takes_it_out_or_stops_it(lab, nas):
    f = _made(lab, nas, "RC queue")
    slots = runner._slots
    for _ in range(runner.MAX_RUNS):                                       # the slots held: the turns wait
        slots.acquire()
    try:
        assert runner.queue_remote_chat(f.id, "rcTurnA", conversation_id="c", page_id=f.page, agent="codex")
        _until(lambda: (f.id, runner.CHAT, "rcTurnA") in runner._active)
        assert runner.queue_remote_chat(f.id, "rcTurnA", conversation_id="c", page_id=f.page, agent="codex")
        assert runner.queue_remote_chat(f.id, "rcTurnB", conversation_id="c", page_id=f.page, agent="codex")
        assert list(runner._queues[f.id]) == [(f.id, runner.CHAT, "rcTurnB")]
        assert runner.chat_turns(lab.user) == []                           # no account here is a turn there's
        assert runner.at_work([f.id]) == {}                                # not claimed yet
        assert runner.stop_remote_chat([f.id], "rcTurnB")                   # queued: out of the queue
        assert (f.id, runner.CHAT, "rcTurnB") not in runner._queued and not runner._queues[f.id]
        assert not runner.stop_remote_chat([f.id], "rcTurnA", "another-run")   # another run's: not this one
        assert runner.stop_remote_chat([f.id], "rcTurnA", home.chat_run_id("rcTurnA"))
        assert not runner.stop_remote_chat([f.id], "rcTurnA")                  # stopped already
        assert not runner.end_stopped_remote([f.id], "rcTurnA")                # it runs here: its own end writes it
        assert not runner.end_stopped_remote([f.id], "rcTurnZ", "another-run")  # another run's: not this one's
    finally:
        for _ in range(runner.MAX_RUNS):
            slots.release()
    assert runner.wait_idle(30)
    assert _turn_row("rcTurnA") is None                                    # stopped before its claim: never taken
    assert home.chat_run_id("rcTurnA") == home.chat_run_id("rcTurnA") != home.chat_run_id("rcTurnB")
    assert len(home.chat_run_id("rcTurnA")) == 22


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
        "run": home.chat_run_id("rcTurnW"), "seq": seqs[0], "fields": {"text": "a", "state": "running"}})
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
    run = {"link_id": "rcVaultT", "stop": threading.Event(), "reason": "", "claimed": False,
           "agent": "", "slot": True, "detail": ""}
    turning = runner._ChatTurn("rcVaultT", "rcTurnT", "rcVaultT", run, {
        "conversation_id": "c", "page_id": "p", "agent": "codex", "model": "", "effort": ""})
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
    assert message["message"] == "Lower the title's case." and message["file"] == "main.pdf"
    [chosen] = message["selections"]
    assert (chosen["quote"], chosen["page"], chosen["source"]["file"]) == (passage["quote"], 1, "sections/method.tex")
    assert OWNER in text and "Earlier in this conversation" not in text
    assert _sent(nas.log, "thread/start")[-1]["approvalPolicy"] == "never"   # a turn there never asks

    # what this computer keeps, and what that server does
    kept = store.conversation_of(conversation_id)
    assert (kept["agent"], kept["session"], kept["link_id"], Path(kept["cwd"])) == ("codex", THREAD, f.id, f.root)
    row = _turn_row(turn_id)
    assert (row["state"], row["ws"], row["link_id"], row["conversation_id"], row["page_id"]) == (
        "done", f.id, f.id, conversation_id, f.page) and row["ended_at"] and row["pid"]
    assert store.get_turn(turn_id) is None                                 # never a turn of this server's
    there = _run_there(lab, turn_id)
    assert (there["state"], there["run"], there["holder"]) == ("done", home.chat_run_id(turn_id), f.token["id"])


def test_the_conversation_reads_while_the_computer_is_away_and_a_message_waits_for_it(lab, nas):
    f = _made(lab, nas, "RC away")
    f.turn.once()
    nas.down.append(True)                                                  # away: the NAS cannot be reached
    assert f.turn.once() == channel.RETRY_FIRST_S
    registry.reset()                                                       # and that server sees it offline
    made = _say(lab.owner, f.page, "Fix the abstract.")
    time.sleep(0.3)
    read = _chat(lab.owner, f.page)
    assert read["computer"]["online"] is False and read["computer"]["since"]
    assert [m["state"] for m in read["conversation"]["messages"] if m["role"] == "agent"] == ["queued"]
    assert _turn_row(made["turn"]["id"]) is None                          # waits for it
    nas.down.clear()
    f.turn.once()                                                          # back: announced again, the turns asked
    assert runner.wait_idle(60)
    assert _reply(lab, f.page)["state"] == "done"


def test_the_routes_answer_the_maker_alone_and_another_accounts_turn_never_runs_here(lab, nas, anon, guest):
    f = _made(lab, nas, "RC gates")
    f.turn.once()
    assert _chat(lab.owner, f.page)["conversation"] is None
    for status, who in ((404, lab.editor), (401, anon)):
        assert who.get("/api/local-agents/chat", params={"page": f.page}).status_code == status
        assert who.post("/api/local-agents/chat/turns", json={"page": f.page, "text": "hi"}).status_code == status
        assert who.post("/api/local-agents/chat/new", json={"page": f.page}).status_code == status
        assert who.get("/api/local-agents/chat/history", params={"page": f.page}).status_code == status
    token = create_token(lab.user, lab.ws, "rch-script", 1, scope="write")
    script = _bearer(token["token"])
    script.headers["X-Gamma-Workspace"] = lab.ws
    assert script.get("/api/local-agents/chat", params={"page": f.page}).status_code == 403
    assert guest.get("/api/local-agents/chat", params={"page": f.page}).status_code == 403
    plain = make_page(lab.owner, "An RC library page")["id"]
    assert lab.owner.get("/api/local-agents/chat", params={"page": plain}).status_code == 404
    # what is the computer's own there: no terminal, no source lines
    assert lab.owner.get("/api/local-agents/chat/terminal", params={"page": f.page}).status_code == 404
    r = lab.owner.post("/api/local-agents/chat/sources", json={"page": f.page, "selections": [{"text": "x"}]})
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
    assert [t["id"] for t in mine] == [turn_id] and mine[0]["folder_id"] == f.w


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
    assert got.json()["turn"]["text"] == "Tighten the abstract." and got.json()["page"]["path"]
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


def test_a_turn_there_never_asks_in_the_chat(lab, nas):
    f = _made(lab, nas, "RC asks")
    store.set_runs(f.id, "ask", by=lab.user)                               # the chat's policy: not one of a turn there
    f.turn.once()
    turn_id = _say(lab.owner, f.page)["turn"]["id"]
    r = lab.owner.post(f"/api/local-agents/chat/turns/{turn_id}/asks/a1", json={"decision": "once"})
    assert (r.status_code, r.json()["detail"]) == (409, "This is answered on the computer that runs the agent.")
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
    store.claimed_turn(turn_id, link_id=f.id, conversation_id=made["conversation"]["id"], page_id=f.page,
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
    turn = {"id": "rcTurnS", "link_id": "rcVaultS", "ws": "rcVaultS", "agent": "codex"}
    key, ended, refused = ("rcVaultS", runner.CHAT, "rcTurnS"), [], []

    def run_failed(self, run):                                             # meanwhile, that server lists the turn
        refused.append(runner.queue_remote_chat("rcVaultS", "rcTurnS", conversation_id="c", page_id="p",
                                                agent="codex"))
        with runner._lock:
            runner._queued.add(key)                                        # and it was queued here before
        return True

    monkeypatch.setattr(home.HttpHome, "fail_left_open", run_failed)
    monkeypatch.setattr(home.HttpChatHome, "fail_left_open", lambda self, t, state="failed": ended.append(t) or True)
    try:
        runner._fail_remote([{"id": "rcRunS", "link_id": "rcVaultS", "ws": "rcVaultS"}], [turn])
    finally:
        with runner._lock:
            runner._queued.discard(key)
    assert refused == [False] and ended == []                              # not queued while settled; never ended
    assert "rcTurnS" not in runner._settling
    runner._fail_remote([], [turn])                                        # neither queued nor running: ended
    assert ended == [turn]


def test_a_turn_whose_agent_was_turned_off_while_it_waited_fails_there_with_why(lab, nas):
    f = _made(lab, nas, "RC off")
    f.turn.once()
    made = _say(lab.owner, f.page)
    turn_id = made["turn"]["id"]
    store.set_agent(f.id, "", by=lab.user)                                 # turned off here, that server not told yet
    assert runner.queue_remote_chat(f.id, turn_id, conversation_id=made["conversation"]["id"], page_id=f.page,
                                    agent="codex")
    assert runner.wait_idle(30)
    reply = _reply(lab, f.page)
    assert (reply["state"], reply["error"]) == ("failed", "Codex was turned off for this folder.")
    assert (_turn_row(turn_id)["state"], _run_there(lab, turn_id)["state"]) == ("failed", "failed")


def test_background_tasks_there_list_the_turn_and_its_stop(lab, nas):
    f = _made(lab, nas, "RC tasks")
    f.turn.once()
    turn_id = _say(lab.owner, f.page)["turn"]["id"]
    jobs = lab.owner.get("/api/jobs").json()["jobs"]
    row = next(j for j in jobs if j["id"] == f"computer-{turn_id}")
    assert (row["kind"], row["state"], row["params"]["turn"], row["params"]["page"], row["readonly"],
            row["stoppable"]) == ("computer-turn", "queued", turn_id, f.page, True, True)
    assert _stop(lab.owner, turn_id) == {"stopped": True}
    assert all(j["id"] != f"computer-{turn_id}" for j in lab.owner.get("/api/jobs").json()["jobs"])
