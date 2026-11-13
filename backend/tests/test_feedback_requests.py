"""Send and its requests (gamma/feedback/requests.py, routers/feedback.py,
docs/dev/feedback.md "Requests and their states"): who may send and what,
a retry that writes once, "Nothing to send"; every change of state row by
row for each writer (a feedback token, a write token, the requester's
session, another member, a viewer, a read token, a share visitor); the
cursor that shows a new request and a state change; a thread answered by a
token's reply or another member's, not by the requester's own nor by a
reopen; the room's nudge, the work folder's files marked (the files'
writer stood in for: gamma/feedback/files.py has its own tests) and the
sweep."""

import sys
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

import gamma.feedback
from conftest import (account_of, fresh_client, login, make_folder, make_page, make_user, mark_work_folder, thread_on,
                      token_for, work_props)
from gamma import collab, workspaces
from gamma.auth import DISK_CLIENT
from gamma.blocks_store import create_page
from gamma.db import connect_pages_db, format_stamp
from gamma.feedback import requests
from gamma.feedback.requests import TRANSITIONS

OWNER, EDITOR, VIEWER = "fbq_own", "fbq_edi", "fbq_vie"


def _bearer(token: str):
    c = fresh_client()
    c.headers["Authorization"] = f"Bearer {token}"
    return c


def _ok(r, status=200):
    assert r.status_code == status, r.text
    return r.json()


@pytest.fixture(scope="module")
def lab(anon_module):
    """A shared workspace owned by fbq_own (fbq_edi edits, fbq_vie views)
    with a work folder ``fbq-paper``: ``main.pdf`` and ``fig.pdf``, two
    open threads on the first and one on the second; a library page.
    Tokens of fbq_own's there: ``agent`` (feedback, "Claude Code"),
    ``script`` (write), ``reader`` (read); and a folder share of the work
    folder for a visitor."""
    for name in (OWNER, EDITOR, VIEWER):
        make_user(name, f"pw-{name}")
    owner, editor, viewer = (login(n, f"pw-{n}") for n in (OWNER, EDITOR, VIEWER))
    ws = workspaces.create("Send lab", account_of(OWNER), kind="shared")["id"]
    workspaces.set_member(ws, account_of(EDITOR), "editor", by=account_of(OWNER))
    workspaces.set_member(ws, account_of(VIEWER), "viewer", by=account_of(OWNER))
    for c in (owner, editor, viewer):
        c.headers["X-Gamma-Workspace"] = ws
    W = make_folder(owner, "fbq-paper")
    mark_work_folder(owner, W)
    with connect_pages_db(ws) as conn:
        main, fig = (create_page(conn, name, work_props(W, f"{name}.pdf"), actor=account_of(OWNER),
                                 client=DISK_CLIENT)["id"] for name in ("main", "fig"))
    library = make_page(owner, "a library paper")["id"]
    t = {"one": thread_on(owner, main, main, "one")["id"], "two": thread_on(owner, main, main, "two")["id"],
         "fig": thread_on(owner, fig, fig, "use viridis")["id"]}
    share = _ok(owner.post(f"/api/share/folder/{W}", json={"audience": "anyone", "role": "edit"}))["token"]
    return SimpleNamespace(ws=ws, owner=owner, editor=editor, viewer=viewer, W=W, main=main, fig=fig,
                           library=library, t=t, share=share, anon=anon_module,
                           agent=_bearer(token_for(owner, "feedback", "Claude Code")),
                           script=_bearer(token_for(owner, "write", "Script")),
                           reader=_bearer(token_for(owner, "read", "Reader")))


@pytest.fixture(scope="module")
def anon_module():
    return fresh_client()


def _send(lab, who=None, status=201, **body):
    r = (who or lab.owner).post("/api/feedback/requests", json={"scope": lab.W, **body},
                                params={"share": lab.share} if who is lab.anon else None)
    assert r.status_code == status, r.text
    return r.json().get("request")


def _state(lab, who, request_id, state, detail=""):
    return who.post(f"/api/feedback/requests/{request_id}/state", json={"state": state, "detail": detail},
                    params={"share": lab.share} if who is lab.anon else None)


def _request_in(lab, state: str) -> str:
    request_id = _send(lab)["id"]
    if state != "sent":
        _ok(_state(lab, lab.agent, request_id, state, "no build" if state == "failed" else ""))
    return request_id


# ---- sending ----------------------------------------------------------------------------

def test_a_send_and_what_it_holds(lab):
    sent = _send(lab, note="fix these")
    assert {k: sent[k] for k in ("work_folder", "scope", "by", "note", "state", "state_by", "detail")} == {
        "work_folder": {"id": lab.W, "name": "fbq-paper"}, "scope": {"kind": "work_folder", "id": lab.W,
                                                                     "name": "fbq-paper"},
        "by": OWNER, "note": "fix these", "state": "sent", "state_by": "", "detail": ""}
    assert sent["state_at"] == sent["at"] and sent["rev"] >= 1
    assert sorted(t["id"] for t in sent["threads"]) == sorted(lab.t.values())
    assert not any(t["answered"] for t in sent["threads"])
    got = _ok(lab.owner.get(f"/api/feedback/requests/{sent['id']}"))
    assert got["request"] == sent
    assert {e["id"] for e in got["entries"]} == set(lab.t.values()) and "picture_url" in got["entries"][0]
    assert lab.owner.get("/api/feedback/requests/noSuchRequest").status_code == 404
    one = _send(lab, scope=lab.main, threads=[lab.t["two"]])
    assert one["scope"] == {"kind": "file", "id": lab.main, "name": "main.pdf"}
    assert [t["id"] for t in one["threads"]] == [lab.t["two"]]
    # the threads' Sent chip
    sent_chip = {t["id"]: t["sent"] for t in _ok(lab.owner.get(f"/api/pages/{lab.main}/comments"))["threads"]}
    assert sent_chip[lab.t["two"]]["request_id"] == one["id"] and sent_chip[lab.t["two"]]["state"] == "sent"


def test_a_retry_writes_once(lab):
    first = _send(lab, id="fbqRetry1", note="once")
    again = _send(lab, status=200, id="fbqRetry1", note="twice")
    assert again == first
    assert _send(lab, lab.editor, status=409, id="fbqRetry1") is None
    assert lab.owner.post("/api/feedback/requests", json={"scope": lab.W, "id": "not an id"}).status_code == 400


def test_nothing_to_send_and_what_is_refused(lab):
    with connect_pages_db(lab.ws) as conn:
        quiet = create_page(conn, "quiet", work_props(lab.W, "quiet.pdf"), actor=account_of(OWNER),
                            client=DISK_CLIENT)["id"]
    done = thread_on(lab.owner, quiet, quiet, "done")["id"]
    _ok(lab.owner.post(f"/api/comments/{done}/resolve", json={"resolved": True}))
    r = lab.owner.post("/api/feedback/requests", json={"scope": quiet})
    assert r.status_code == 400 and r.json()["detail"] == "Nothing to send: no open comments here."
    for body, status in (({"scope": lab.W, "threads": [done]}, 400), ({"scope": lab.main, "threads": [lab.t["fig"]]}, 400),
                         ({"scope": lab.W, "threads": [f"t{n}" for n in range(501)]}, 400),
                         ({"scope": lab.W, "threads": []}, 400), ({"scope": lab.library}, 400),
                         ({"scope": "noSuchThing"}, 404), ({"scope": lab.W, "note": "x" * 2001}, 413)):
        assert lab.owner.post("/api/feedback/requests", json=body).status_code == status, body


def test_who_may_send(lab):
    for who in (lab.viewer, lab.agent, lab.reader, lab.anon):
        assert _send(lab, who, status=403) is None
    assert _send(lab, lab.script)["by"] == OWNER
    assert _send(lab, lab.editor)["by"] == EDITOR


# ---- states -----------------------------------------------------------------------------

WRITERS = ("feedback", "write", "requester", "member", "viewer", "read", "visitor")


@pytest.mark.parametrize("writer", WRITERS)
def test_every_change_of_state_by_each_writer(lab, writer):
    who = {"feedback": lab.agent, "write": lab.script, "requester": lab.owner, "member": lab.editor,
           "viewer": lab.viewer, "read": lab.reader, "visitor": lab.anon}[writer]
    role = {"feedback": "agent", "write": "agent", "requester": "requester"}.get(writer)
    for start in requests.STATES:
        for to in ("running", "done", "failed"):
            request_id = _request_in(lab, start)
            r = _state(lab, who, request_id, to, "row test")
            if role is None:
                assert r.status_code == 403, (writer, start, to, r.text)
                continue
            if role in TRANSITIONS.get((start, to), ()):
                assert r.status_code == 200, (writer, start, to, r.text)
                got = r.json()["request"]
                assert got["state"] == to
                assert (got["detail"], got["state_by"]) == (
                    ("withdrawn", OWNER) if role == "requester" else
                    ("row test", "Claude Code" if writer == "feedback" else "Script"))
            else:
                assert r.status_code == 409, (writer, start, to, r.text)
                assert r.json()["detail"] == f"A {start} request cannot become {to}."


def test_the_same_state_and_detail_change_nothing(lab):
    request_id = _request_in(lab, "running")
    first = _ok(_state(lab, lab.agent, request_id, "running", "compiling"))["request"]
    assert _ok(_state(lab, lab.agent, request_id, "running", "compiling"))["request"]["rev"] == first["rev"]
    withdrawn = _ok(_state(lab, lab.owner, request_id, "failed", "whatever I say"))["request"]
    assert withdrawn["detail"] == "withdrawn" and withdrawn["rev"] > first["rev"]
    assert _ok(_state(lab, lab.owner, request_id, "failed"))["request"]["rev"] == withdrawn["rev"]
    done = _request_in(lab, "done")
    assert _ok(_state(lab, lab.agent, done, "done"))["request"]["state"] == "done"


def test_a_states_refusals(lab):
    request_id = _request_in(lab, "sent")
    assert _state(lab, lab.agent, "noSuchRequest", "running").status_code == 404
    assert _state(lab, lab.agent, request_id, "sent").status_code == 200  # as it is: nothing changes
    assert _state(lab, lab.agent, request_id, "sent", "again").status_code == 409
    assert _state(lab, lab.agent, request_id, "paused").status_code == 400
    assert _state(lab, lab.agent, request_id, "running", "x" * 501).status_code == 413
    other = _send(lab, lab.editor)["id"]
    assert _state(lab, lab.owner, other, "failed").status_code == 403  # the owner did not send it
    assert _ok(_state(lab, lab.editor, other, "failed"))["request"]["detail"] == "withdrawn"


def test_a_claim_takes_only_a_sent_request(lab):
    """``claim`` sets a sent request running for an agent on this computer
    and takes the next rev; any other state is left as it is, None."""
    request_id = _send(lab)["id"]
    with connect_pages_db(lab.ws) as conn:
        before = requests.get(conn, request_id)
        got = requests.claim(lab.ws, conn, request_id, by_name="Codex", detail="Starting Codex")
        assert (got["state"], got["state_by"], got["detail"]) == ("running", "Codex", "Starting Codex")
        assert got["rev"] > before["rev"] and got["state_at"] >= before["state_at"]
        assert requests.claim(lab.ws, conn, request_id, by_name="Claude Code", detail="again") is None
        assert requests.get(conn, request_id) == got
        assert requests.claim(lab.ws, conn, "noSuchRequest", by_name="Codex", detail="") is None
    chips = {t["id"]: t["sent"] for t in _ok(lab.owner.get(f"/api/pages/{lab.main}/comments"))["threads"]}
    assert chips[lab.t["one"]]["request_id"] == request_id and chips[lab.t["one"]]["state_by"] == "Codex"
    for state in ("done", "failed"):
        other = _request_in(lab, state)
        with connect_pages_db(lab.ws) as conn:
            rev = requests.get(conn, other)["rev"]
            assert requests.claim(lab.ws, conn, other, by_name="Codex", detail="") is None
            assert requests.get(conn, other)["rev"] == rev
    withdrawn = _send(lab)["id"]
    _ok(_state(lab, lab.owner, withdrawn, "failed"))
    with connect_pages_db(lab.ws) as conn:
        assert requests.claim(lab.ws, conn, withdrawn, by_name="Codex", detail="") is None


def test_only_from_refuses_a_state_the_writer_did_not_expect(lab):
    request_id = _send(lab)["id"]
    with connect_pages_db(lab.ws) as conn:
        with pytest.raises(requests.FeedbackError) as e:
            requests.set_state(lab.ws, conn, request_id, "done", by_name="Codex", writer="agent", only_from=("running",))
        assert e.value.status == 409 and requests.get(conn, request_id)["state"] == "sent"
        requests.claim(lab.ws, conn, request_id, by_name="Codex", detail="Starting Codex")
        got = requests.set_state(lab.ws, conn, request_id, "running", detail="Reading main.pdf", by_name="Codex",
                                 writer="agent", only_from=("running",))
        assert got["detail"] == "Reading main.pdf"
    _ok(_state(lab, lab.owner, request_id, "failed"))  # withdrawn meanwhile
    with connect_pages_db(lab.ws) as conn:
        with pytest.raises(requests.FeedbackError) as e:
            requests.set_state(lab.ws, conn, request_id, "done", detail="all fixed", by_name="Codex", writer="agent",
                               only_from=("running",))
        assert e.value.status == 409
        assert (requests.get(conn, request_id)["state"], requests.get(conn, request_id)["detail"]) == (
            "failed", "withdrawn")
        assert requests.set_state(lab.ws, conn, request_id, "done", by_name="Codex", writer="agent")["state"] == "done"


def test_the_change_listeners_hear_every_write(lab, monkeypatch):
    """Each listener hears a Send, a claim and a change of state, after its
    commit; one that raises stops neither the write nor the next listener.
    ``_changed``'s early return (the request unreadable after its write)
    still tells them."""
    heard = []

    def broken(ws, request_id):
        raise RuntimeError("a broken listener")

    def listener(ws, request_id):
        with connect_pages_db(ws) as conn:
            heard.append((ws, request_id, requests.get(conn, request_id)["state"]))

    monkeypatch.setattr(requests, "change_listeners", [broken, listener])
    request_id = _send(lab)["id"]
    with connect_pages_db(lab.ws) as conn:
        requests.claim(lab.ws, conn, request_id, by_name="Codex", detail="Starting Codex")
        assert requests.claim(lab.ws, conn, request_id, by_name="Codex", detail="") is None  # nothing written
    _ok(_state(lab, lab.agent, request_id, "done"))
    assert heard == [(lab.ws, request_id, "sent"), (lab.ws, request_id, "running"), (lab.ws, request_id, "done")]
    heard.clear()
    _ok(_state(lab, lab.agent, request_id, "done"))  # the same again changes nothing
    assert heard == []
    told = []
    monkeypatch.setattr(requests, "change_listeners", [lambda ws, rid: told.append(rid)])
    with connect_pages_db(lab.ws) as conn:
        requests._changed(lab.ws, conn, "noSuchRequest")
    assert told == ["noSuchRequest"]


def test_the_cursor_shows_new_requests_and_changes(lab):
    start = _ok(lab.reader.get("/api/feedback/requests", params={"after": 0, "limit": 100}))
    while start["requests"]:
        start = _ok(lab.reader.get("/api/feedback/requests", params={"after": start["cursor"], "limit": 100}))
    cursor = start["cursor"]
    one, two = _send(lab), _send(lab, scope=lab.fig)
    page = _ok(lab.reader.get("/api/feedback/requests", params={"after": cursor}))
    assert [r["id"] for r in page["requests"]] == [one["id"], two["id"]] and page["cursor"] == two["rev"]
    _ok(_state(lab, lab.agent, one["id"], "running"))
    moved = _ok(lab.reader.get("/api/feedback/requests", params={"after": page["cursor"]}))
    assert [r["id"] for r in moved["requests"]] == [one["id"]] and moved["requests"][0]["rev"] > two["rev"]
    assert _ok(lab.reader.get("/api/feedback/requests", params={"after": moved["cursor"]})) == {
        "requests": [], "cursor": moved["cursor"]}
    by_file = _ok(lab.reader.get("/api/feedback/requests", params={"after": cursor, "scope": lab.fig}))
    assert [r["id"] for r in by_file["requests"]] == [two["id"]]
    running = _ok(lab.reader.get("/api/feedback/requests", params={"after": cursor, "state": "running"}))
    assert [r["id"] for r in running["requests"]] == [one["id"]]
    first = _ok(lab.reader.get("/api/feedback/requests", params={"after": cursor, "limit": 1}))
    assert [r["id"] for r in first["requests"]] == [two["id"]] and first["cursor"] == two["rev"]
    assert lab.reader.get("/api/feedback/requests", params={"state": "paused"}).status_code == 400
    assert lab.anon.get("/api/feedback/requests", params={"share": lab.share}).status_code == 403


# ---- answered ---------------------------------------------------------------------------

def test_a_thread_is_answered_by_an_agent_or_another_member(lab):
    with connect_pages_db(lab.ws) as conn:
        page = create_page(conn, "answers", work_props(lab.W, "answers.pdf"), actor=account_of(OWNER),
                           client=DISK_CLIENT)["id"]
    t = {k: thread_on(lab.owner, page, page, k)["id"] for k in ("mine", "theirs", "resolved")}
    sent = _send(lab, scope=page)

    def answered():
        got = _ok(lab.owner.get(f"/api/feedback/requests/{sent['id']}"))["request"]["threads"]
        return {next(k for k, v in t.items() if v == row["id"]) for row in got if row["answered"]}

    def reply(who, thread):
        _ok(who.post(f"/api/pages/{page}/comments", json={"block_id": page, "body": "re", "thread_id": thread}), 201)

    reply(lab.owner, t["mine"])
    assert answered() == set()
    reply(lab.agent, t["mine"])
    reply(lab.editor, t["theirs"])
    assert answered() == {"mine", "theirs"}
    _ok(lab.owner.post(f"/api/comments/{t['resolved']}/resolve", json={"resolved": True}))
    _ok(lab.owner.post(f"/api/comments/{t['resolved']}/resolve", json={"resolved": False}))
    assert answered() == {"mine", "theirs"}
    _ok(lab.agent.post(f"/api/comments/{t['resolved']}/resolve", json={"resolved": True}))
    assert answered() == {"mine", "theirs", "resolved"}
    chips = {th["id"]: th["sent"] for th in _ok(lab.owner.get(f"/api/pages/{page}/comments"))["threads"]}
    assert chips == {t["mine"]: None, t["theirs"]: None, t["resolved"]: None}
    # a later Send waits on them again
    again = _send(lab, scope=page, threads=[t["mine"]])
    assert _ok(lab.owner.get(f"/api/pages/{page}/comments"))["threads"][0]["sent"]["request_id"] == again["id"]


# ---- what follows a write ---------------------------------------------------------------

def test_the_threads_pages_hear_a_send_and_a_change(lab, monkeypatch):
    heard = []
    monkeypatch.setattr(collab, "publish", lambda ws, page, msg, exclude="": heard.append((ws, page, msg)))
    sent = _send(lab, threads=[lab.t["one"], lab.t["two"], lab.t["fig"]])
    nudge = {"t": "comments", "block_id": "", "thread_id": ""}
    assert sorted(heard) == sorted([(lab.ws, lab.main, nudge), (lab.ws, lab.fig, nudge)])
    heard.clear()
    _ok(_state(lab, lab.agent, sent["id"], "running"))
    assert {page for _, page, _ in heard} == {lab.main, lab.fig}
    heard.clear()
    _ok(_state(lab, lab.agent, sent["id"], "running"))
    assert heard == []  # nothing changed


@pytest.fixture
def files(monkeypatch):
    """The feedback files' writer stood in for, on a server that keeps
    folders on disk: what it was told, in order."""
    monkeypatch.setenv("GAMMA_FOLDER_LINKS", "1")
    told = []
    fake = SimpleNamespace(mark=lambda ws, page_id: told.append(("page", ws, page_id)),
                           mark_folder=lambda ws, w: told.append(("folder", ws, w)),
                           write_request=lambda link, request_id: told.append(("request", link["id"], request_id)))
    monkeypatch.setitem(sys.modules, "gamma.feedback.files", fake)
    monkeypatch.setattr(gamma.feedback, "files", fake, raising=False)
    return told


def test_the_listeners_mark_the_work_folders_files(lab, files, monkeypatch):
    thread = thread_on(lab.owner, lab.main, lab.main, "marks the files")["id"]
    assert ("page", lab.ws, lab.main) in files
    files.clear()
    _ok(lab.owner.post(f"/api/pages/{lab.fig}/ops", json={"client": "t", "ops": [
        {"op": "insert", "id": "fbqMarked1", "parent": lab.fig, "content": "a note"}]}))
    assert files == [("page", lab.ws, lab.fig)]
    files.clear()
    monkeypatch.setattr(requests, "_local_link", lambda ws, w: {"id": "link1"} if (ws, w) == (lab.ws, lab.W) else None)
    sent = _send(lab, scope=lab.main, threads=[thread])
    assert files == [("folder", lab.ws, lab.W), ("request", "link1", sent["id"])]
    files.clear()
    _ok(lab.agent.post(f"/api/pages/{lab.main}/comments", json={"block_id": lab.main, "body": "done",
                                                                "thread_id": thread}), 201)
    assert ("page", lab.ws, lab.main) in files and ("folder", lab.ws, lab.W) in files  # answered: the request changed
    files.clear()
    monkeypatch.delenv("GAMMA_FOLDER_LINKS")
    thread_on(lab.owner, lab.main, lab.main, "no folders on disk here")
    assert files == []


def test_the_sweep(lab):
    old, recent = _request_in(lab, "done"), _request_in(lab, "failed")
    gone_at = format_stamp(datetime.now(timezone.utc) - timedelta(days=requests.KEEP_DAYS + 1))
    with connect_pages_db(lab.ws) as conn:
        conn.execute("UPDATE feedback_requests SET state_at = ? WHERE id = ?", (gone_at, old))
        conn.execute("INSERT INTO feedback_request_threads (request_id, thread_id) VALUES (?, 'fbqGoneThread')",
                     (recent,))
        conn.commit()
        rows = conn.execute("SELECT COUNT(*) FROM feedback_request_threads WHERE request_id = ?", (old,)).fetchone()[0]
    requests._due.clear()
    assert requests.sweep()[lab.ws] == {"requests": 1, "threads": rows + 1}
    with connect_pages_db(lab.ws) as conn:
        assert requests.get(conn, old) is None
        assert "fbqGoneThread" not in {t["id"] for t in requests.get(conn, recent)["threads"]}
    assert lab.ws not in requests.sweep()
