"""``@codex`` in a comment (gamma/local_agents/mention.py, the comment routes,
the runner's follow-ups; docs/dev/local_agents.md "A mention in a comment"):
the work folder's maker mentions the folder's agent on this computer, and
the comment is a Send of its thread, or of the file's open comments from
the page's own thread, its other words the instruction.

Each gate off leaves the mention plain text (another member, a token, the
agent off, a server that runs no agents, another agent's name, code, a
page outside a work folder, the agent's own replies); an edit sends only a
new mention of the thread's newest comment; a resolved thread, a viewer, a
server that takes no writes and too many comments are refused with the
comment posted; a retried POST sends once. The Inbox tells a member named
codex nothing of the maker's mention, as before elsewhere. A mention made
while a run works on the thread is the next run's (the thread is not
resolved over it), a follow-up resumes the session that answered, and a
resume refused starts afresh. The vault says whose it is, and a switch of
its agent reaches the folder's open pages."""

from types import SimpleNamespace

import pytest

from conftest import account_of, fresh_client, login, make_page, make_user, token_for
from fake_agents import requests_in, script_agent, turns_in
from gamma import collab, folder_links, hosted, mentions, workspaces
from gamma.db import connect_pages_db, connect_users_db
from gamma.local_agents import mention, runner, store
from test_local_agents_runner import _folder, _request, _turn_started, disk  # noqa: F401: the disk fixture

OWNER, EDITOR, NAMED = "lam_owner", "lam_editor", "codex"


@pytest.fixture(scope="module")
def lab(client):
    """A shared workspace of lam_owner's that lam_editor edits; an account
    named codex reads it."""
    for name in (OWNER, EDITOR, NAMED):
        make_user(name, f"pw-{name}")
    owner, editor = login(OWNER, f"pw-{OWNER}"), login(EDITOR, f"pw-{EDITOR}")
    ws = workspaces.create("Mentions lab", account_of(OWNER), kind="shared")["id"]
    workspaces.set_member(ws, account_of(EDITOR), "editor", by=account_of(OWNER))
    workspaces.set_member(ws, account_of(NAMED), "viewer", by=account_of(OWNER))
    for c in (owner, editor):
        c.headers["X-Gamma-Workspace"] = ws
    return SimpleNamespace(ws=ws, owner=owner, editor=editor, user=account_of(OWNER))


def _note(lab, f, text="a note on the paper") -> str:
    r = lab.owner.post("/api/blocks", json={"parent_id": f.page, "content": text})
    assert r.status_code == 200, r.text
    return r.json()["id"]


def _post(who, page, block, body, status=201, **more) -> dict:
    r = who.post(f"/api/pages/{page}/comments", json={"block_id": block, "body": body, **more})
    assert r.status_code == status, r.text
    return r.json()


def _edit(who, comment_id, body) -> dict:
    r = who.patch(f"/api/comments/{comment_id}", json={"body": body})
    assert r.status_code == 200, r.text
    return r.json()


def _thread(lab, f, thread_id) -> dict:
    r = lab.owner.get(f"/api/pages/{f.page}/comments", params={"state": "all"})
    assert r.status_code == 200, r.text
    return next(t for t in r.json()["threads"] if t["id"] == thread_id)


def _requests(lab) -> int:
    with connect_pages_db(lab.ws) as conn:
        return conn.execute("SELECT COUNT(*) FROM feedback_requests").fetchone()[0]


def _resolve(lab, thread_id, resolved=True):
    r = lab.owner.post(f"/api/comments/{thread_id}/resolve", json={"resolved": resolved})
    assert r.status_code == 200, r.text


def _told(lab, kind) -> set:
    """The comments the account named codex was told of, as ``kind``."""
    with connect_users_db() as conn:
        return {r[0] for r in conn.execute("SELECT comment_id FROM inbox_items WHERE user_id = ? AND kind = ? "
                                           "AND workspace_id = ?", (account_of(NAMED), kind, lab.ws))}


# --- the Send ---------------------------------------------------------------------------------

def test_a_mention_in_a_thread_sends_that_thread(lab, disk, monkeypatch):
    f = _folder(lab, disk, comments=("left alone",))
    note = _note(lab, f)
    out = _post(lab.owner, f.page, note, "@codex please fix the `@codex` typo", id="lamThread1")
    assert out["send"] == {"agent": "Codex", "request_id": "lamThread1", "scope": "thread", "threads": 1}
    assert out["thread"]["sent"]["request_id"] == "lamThread1"  # the thread read again: its Sent chip at once
    assert runner.wait_idle(60)

    got = _request(lab, "lamThread1")
    assert (got["state"], got["note"], [t["id"] for t in got["threads"]]) == (
        "done", "please fix the `@codex` typo", ["lamThread1"])
    thread = _thread(lab, f, "lamThread1")
    assert [c.get("agent_kind") for c in thread["comments"]] == [None, "local"] and thread["resolved_at"]
    assert len(_thread(lab, f, f.threads[0])["comments"]) == 1  # the file's other thread is not sent
    row = store.threads_of(f.id, ["lamThread1"])["lamThread1"]
    assert (row["agent"], row["cwd"], row["request_id"], row["comment_id"]) == (
        "codex", f.link["path"], "lamThread1", thread["comments"][1]["id"])
    assert row["session"] == store.run_of(lab.ws, "lamThread1")["session"] and row["seen_at"]


def test_a_mention_in_the_pages_thread_sends_the_files_comments(lab, disk):
    f = _folder(lab, disk)
    out = _post(lab.owner, f.page, f.page, "@codex go through these, please")
    assert out["send"]["scope"] == "file" and out["send"]["threads"] == 3
    assert runner.wait_idle(60)
    got = _request(lab, out["send"]["request_id"])
    assert got["state"] == "done" and got["note"] == "go through these, please" and got["scope"]["kind"] == "file"
    assert all(_thread(lab, f, t)["resolved_at"] for t in (*f.threads, out["thread"]["id"]))


def test_where_a_mention_is_plain_text(lab, disk, monkeypatch):
    f = _folder(lab, disk, comments=())
    note = _note(lab, f)
    library = make_page(lab.owner, "a library page")["id"]
    script = fresh_client()
    script.headers["Authorization"] = f"Bearer {token_for(lab.owner, 'write', 'Script')}"
    script.headers["X-Gamma-Workspace"] = lab.ws
    before = _requests(lab)
    for who, page, block, body in ((lab.editor, f.page, note, "@codex fix it"),       # another member
                                   (script, f.page, note, "@codex fix it"),           # a token
                                   (lab.owner, f.page, note, "@claude fix it"),       # another agent's name
                                   (lab.owner, f.page, note, "see `@codex` here"),    # code
                                   (lab.owner, f.page, note, "@Codex fix it"),        # not its name
                                   (lab.owner, library, library, "@codex fix it")):   # no work folder's file
        assert "send" not in _post(who, page, block, body)
    store.set_agent(f.id, "", by=lab.user)  # the agent off
    assert "send" not in _post(lab.owner, f.page, note, "@codex fix it")
    store.set_agent(f.id, "codex", by=lab.user)
    monkeypatch.setenv("GAMMA_LOCAL_AGENTS", "0")  # a server that runs no agents
    assert "send" not in _post(lab.owner, f.page, note, "@codex fix it")
    assert _requests(lab) == before

    # the rule's gates on their own: an agent's reply, a token's, a share's, a resolve
    event = {"kind": "reply", "actor": lab.user, "page_id": f.page, "block_id": note, "thread_id": "t",
             "comment_id": "c", "body": "@codex again", "agent": "", "agent_id": ""}
    monkeypatch.setenv("GAMMA_LOCAL_AGENTS", "1")
    assert mention.agent_named(lab.ws, event, via="") == "codex"
    assert mention.agent_named(lab.ws, {**event, "agent": "Codex", "agent_id": "local:codex"}, via="") == ""
    assert mention.agent_named(lab.ws, {**event, "agent": "Script", "agent_id": "tok1"}, via="") == ""
    assert mention.agent_named(lab.ws, event, via="shareToken") == ""
    assert mention.agent_named(lab.ws, {**event, "kind": "resolve"}, via="") == ""
    assert mention.agent_named(lab.ws, {**event, "actor": account_of(EDITOR)}, via="") == ""


def test_an_edit_sends_a_new_mention_of_the_newest_comment(lab, disk):
    f = _folder(lab, disk, comments=())
    root = _post(lab.owner, f.page, _note(lab, f), "the axis is unlabelled")
    assert "send" not in root and runner.wait_idle(10)
    thread_id = root["thread"]["id"]
    edited = _edit(lab.owner, thread_id, "@codex the axis is unlabelled")
    assert edited["send"]["scope"] == "thread" and edited["send"]["request_id"] != thread_id
    assert edited["thread"]["sent"]["request_id"] == edited["send"]["request_id"]
    assert runner.wait_idle(60)
    _resolve(lab, thread_id, False)  # the agent fixed it; reopened for more
    again = _edit(lab.owner, thread_id, "@codex the axis is unlabelled, still")
    assert set(again) == {"comment"}  # not a new mention: no Send, no thread
    reply = _post(lab.owner, f.page, root["thread"]["block_id"], "and the legend", thread_id=thread_id)
    assert "send" not in reply
    older = _edit(lab.owner, thread_id, "@codex @claude the axis is unlabelled")  # still no new codex mention
    assert "send" not in older
    with connect_pages_db(lab.ws) as conn:  # an older comment newly naming the agent
        conn.execute("UPDATE comments SET body = 'the axis' WHERE id = ?", (thread_id,))
        conn.commit()
    refused = _edit(lab.owner, thread_id, "@codex the axis")
    assert refused["send"] == {"agent": "Codex", "refused": "not_newest",
                               "why": "Mention Codex in a new reply to send this thread again."}


def test_what_is_refused_and_the_comment_posted_anyway(lab, disk, monkeypatch):
    f = _folder(lab, disk)
    note = _note(lab, f)
    root = _post(lab.owner, f.page, note, "the caption")
    assert runner.wait_idle(10)
    _resolve(lab, root["thread"]["id"])
    closed = _post(lab.owner, f.page, note, "@codex one more", thread_id=root["thread"]["id"])
    assert closed["send"] == {"agent": "Codex", "refused": "closed",
                              "why": "Reopen this thread to send it to Codex again."}
    assert closed["comment"]["body"] == "@codex one more"

    monkeypatch.setattr(runner, "MAX_THREADS", 2)
    many = _post(lab.owner, f.page, f.page, "@codex all of them")
    assert many["send"] == {"agent": "Codex", "refused": "too_many", "why": (
        "Codex takes at most 2 comments at a time: mention it in the threads you want answered.")}
    monkeypatch.setattr(runner, "MAX_THREADS", 100)

    event = {"kind": "create", "actor": lab.user, "page_id": f.page, "block_id": note, "thread_id": many["thread"]["id"],
             "comment_id": many["comment"]["id"], "body": "@codex x", "agent": "", "agent_id": ""}
    monkeypatch.setattr(hosted, "read_only", lambda: "This server takes no changes now.")
    assert mention.send(lab.ws, event, "codex", by_name=OWNER) == {
        "agent": "Codex", "refused": "writes", "why": "This server takes no changes now."}
    monkeypatch.setattr(workspaces, "role_of", lambda ws, user_id: "viewer")
    assert mention.send(lab.ws, event, "codex", by_name=OWNER)["refused"] == "viewer"
    with connect_pages_db(lab.ws) as conn:
        made = {r[0] for r in conn.execute("SELECT id FROM feedback_requests")}
    assert not made & {closed["comment"]["id"], many["comment"]["id"]}


def test_a_retried_post_sends_once(lab, disk, monkeypatch):
    script_agent(monkeypatch, disk.log, delay=2)
    f = _folder(lab, disk, comments=())
    note = _note(lab, f)
    first = _post(lab.owner, f.page, note, "@codex once", id="lamRetry1")
    again = _post(lab.owner, f.page, note, "@codex once", id="lamRetry1")
    assert first["send"]["request_id"] == "lamRetry1" and "send" not in again
    assert runner.wait_idle(60)
    assert len(turns_in(disk.log)) == 1


def test_the_instruction_without_the_mention():
    names = {"codex"}
    assert mentions.without("@codex fix this", names) == "fix this"
    assert mentions.without("Please @codex fix this", names) == "Please fix this"
    assert mentions.without("fix it @codex", names) == "fix it"
    assert mentions.without("thanks @alice and @codex", names) == "thanks @alice and"
    assert mentions.without("@codex\nfix\n@codex", names) == "\nfix\n"
    assert mentions.without("the `@codex` example, @codex", names) == "the `@codex` example,"
    assert mentions.without("```\n@codex\n```\n@codex fix", names) == "```\n@codex\n```\nfix"
    for kept in ("x@codex fix", "@codexx fix", "@claude fix", "@2026-11-13 fix"):
        assert mentions.without(kept, names) == kept


# --- the Inbox ------------------------------------------------------------------------------------

def test_a_member_named_codex_hears_nothing_of_the_makers_mention(lab, disk, monkeypatch):
    script_agent(monkeypatch, disk.log, each="question")
    f = _folder(lab, disk, comments=())
    note = _note(lab, f)
    mine = _post(lab.owner, f.page, note, "@codex fix the caption")
    theirs = _post(lab.editor, f.page, note, "@codex is that you?")
    assert runner.wait_idle(60)
    assert mine["comment"]["id"] not in _told(lab, "comment_mention")
    assert theirs["comment"]["id"] in _told(lab, "comment_mention")  # another member's: as before
    # a reply in the maker's thread tells no one named codex; in the other member's, as before
    in_mine = _post(lab.editor, f.page, note, "agreed", thread_id=mine["thread"]["id"])
    in_theirs = _post(lab.owner, f.page, note, "no, it is not", thread_id=theirs["thread"]["id"])
    told = _told(lab, "reply")
    assert in_mine["comment"]["id"] not in told and in_theirs["comment"]["id"] in told
    agent_reply = _thread(lab, f, mine["thread"]["id"])["comments"][1]
    assert agent_reply["agent_kind"] == "local" and agent_reply["id"] not in told
    # the maker's comment through a token names the member, and so do the thread's later replies
    script = fresh_client()
    script.headers["Authorization"] = f"Bearer {token_for(lab.owner, 'write', 'Script')}"
    script.headers["X-Gamma-Workspace"] = lab.ws
    scripted = _post(script, f.page, note, "@codex can you check the units?")
    assert "send" not in scripted and scripted["comment"]["id"] in _told(lab, "comment_mention")
    later = _post(lab.editor, f.page, note, "the units are SI", thread_id=scripted["thread"]["id"])
    assert later["comment"]["id"] in _told(lab, "reply")
    # the agent off: the maker's mention is the person's again
    store.set_agent(f.id, "", by=lab.user)
    plain = _post(lab.owner, f.page, note, "@codex can you look?")
    assert plain["comment"]["id"] in _told(lab, "comment_mention")


# --- the runs ---------------------------------------------------------------------------------------

def test_a_mention_while_a_run_works_on_the_thread_is_the_next_runs(lab, disk, monkeypatch):
    script_agent(monkeypatch, disk.log, delay=2, distinct=True)
    f = _folder(lab, disk, comments=())
    note = _note(lab, f)
    first = _post(lab.owner, f.page, note, "@codex shorten the caption")
    thread_id = first["thread"]["id"]
    _turn_started(lab, first["send"]["request_id"])
    second = _post(lab.owner, f.page, note, "@codex and the title too", thread_id=thread_id)
    assert second["send"]["request_id"] == second["comment"]["id"]
    assert runner.wait_idle(60)

    got = _request(lab, second["send"]["request_id"])
    assert got["state"] == "done" and got["detail"] != "Nothing new since the last answers."
    thread = _thread(lab, f, thread_id)
    assert [c.get("agent_kind") for c in thread["comments"]] == [None, None, "local", "local"]
    assert thread["resolved_at"]  # by the second run: the first left it open for the word it never saw
    turns = [m["params"] for m in requests_in(disk.log) if m.get("method") == "turn/start"]
    assert "and the title too" not in turns[0]["input"][0]["text"] and "and the title too" in turns[1]["input"][0]["text"]
    # the second resumed the first's session: the thread's last answer was in it
    resumed = [m["params"]["threadId"] for m in requests_in(disk.log) if m.get("method") == "thread/resume"]
    assert resumed == [store.run_of(lab.ws, first["send"]["request_id"])["session"]]


def test_another_members_reply_does_not_cancel_a_waiting_mention(lab, disk, monkeypatch):
    script_agent(monkeypatch, disk.log, delay=2, each="question")
    f = _folder(lab, disk, comments=())
    first = _post(lab.owner, f.page, _note(lab, f), "@codex shorten the caption")
    _turn_started(lab, first["send"]["request_id"])
    waiting = _post(lab.owner, f.page, _note(lab, f, "the legend"), "@codex move the legend")
    _post(lab.editor, f.page, waiting["thread"]["block_id"], "agreed", thread_id=waiting["thread"]["id"])
    assert runner.wait_idle(60)
    got = _request(lab, waiting["send"]["request_id"])
    assert got["state"] == "done" and got["detail"] != "Nothing new since the last answers."
    assert [c.get("agent_kind") for c in _thread(lab, f, waiting["thread"]["id"])["comments"]] == [None, None, "local"]


def test_a_word_the_run_did_not_read_is_unread_whatever_its_time(lab, disk):
    f = _folder(lab, disk, comments=())
    root = _post(lab.owner, f.page, _note(lab, f), "the caption")
    assert runner.wait_idle(10)
    thread_id, comment_id = root["thread"]["id"], root["comment"]["id"]
    later = "9999-12-31T00:00:00.000000Z"  # read "after" the comment was stamped
    assert runner._spoken_after(lab.ws, {thread_id: (later, set())}) == {thread_id}  # stamped before, never read
    assert runner._spoken_after(lab.ws, {thread_id: (later, {comment_id})}) == set()
    assert runner._spoken_after(lab.ws, {thread_id: ("2000-01-01T00:00:00.000000Z", {comment_id})}) == {thread_id}  # edited after
    assert runner._spoken_after(lab.ws, {thread_id: ("", set())}) == set()  # no run read it


def test_a_follow_up_resumes_the_session_that_answered(lab, disk, monkeypatch):
    script_agent(monkeypatch, disk.log, distinct=True)
    f = _folder(lab, disk, comments=())
    note = _note(lab, f)
    first = _post(lab.owner, f.page, note, "@codex use American spelling")
    assert runner.wait_idle(60)
    thread_id = first["thread"]["id"]
    assert _thread(lab, f, thread_id)["resolved_at"]
    session = store.run_of(lab.ws, first["send"]["request_id"])["session"]
    assert session.startswith("019a0000-fake-thread-")

    _resolve(lab, thread_id, False)
    again = _post(lab.owner, f.page, note, "@codex no, keep the British spelling", thread_id=thread_id)
    assert runner.wait_idle(60)
    assert _request(lab, again["send"]["request_id"])["note"] == "no, keep the British spelling"
    assert [m["params"]["threadId"] for m in requests_in(disk.log) if m.get("method") == "thread/resume"] == [session]
    assert store.run_of(lab.ws, again["send"]["request_id"])["session"] == session

    # two threads answered last in two sessions: a Send of both resumes neither
    other_note = _note(lab, f, "another note")
    other = _post(lab.owner, f.page, other_note, "@codex and this")
    assert runner.wait_idle(60)
    other_session = store.threads_of(f.id, [other["thread"]["id"]])[other["thread"]["id"]]["session"]
    assert other_session not in (session, "")
    for thread, block in ((thread_id, note), (other["thread"]["id"], other_note)):
        _resolve(lab, thread, False)
        assert "send" not in _post(lab.owner, f.page, block, "one more thing", thread_id=thread)
    r = lab.owner.post("/api/feedback/requests", json={"scope": f.page, "threads": [thread_id, other["thread"]["id"]]})
    assert r.status_code == 201, r.text
    assert runner.wait_idle(60)
    assert len([m for m in requests_in(disk.log) if m.get("method") == "thread/resume"]) == 1
    assert store.run_of(lab.ws, r.json()["request"]["id"])["session"] not in (session, other_session, "")


def test_a_refused_resume_starts_afresh(lab, disk, monkeypatch):
    script_agent(monkeypatch, disk.log, fail="resume", distinct=True)
    f = _folder(lab, disk, agent="claude", comments=())
    note = _note(lab, f)
    first = _post(lab.owner, f.page, note, "@claude tighten the abstract")
    assert first["send"]["agent"] == "Claude Code"
    assert runner.wait_idle(60)
    thread_id = first["thread"]["id"]
    session = store.run_of(lab.ws, first["send"]["request_id"])["session"]
    _resolve(lab, thread_id, False)
    again = _post(lab.owner, f.page, note, "@claude shorter still", thread_id=thread_id)
    assert runner.wait_idle(60)
    assert _request(lab, again["send"]["request_id"])["state"] == "done"
    turns = [e["argv"] for e in turns_in(disk.log)]
    assert ["--resume" in argv for argv in turns] == [False, True, False]
    assert turns[1][turns[1].index("--resume") + 1] == session
    fresh = store.run_of(lab.ws, again["send"]["request_id"])["session"]
    assert fresh and fresh != session
    assert store.threads_of(f.id, [thread_id])[thread_id]["session"] == fresh


# --- the vault ----------------------------------------------------------------------------------

def test_the_vault_says_whose_it_is_and_its_pages_hear_the_switch(lab, disk, monkeypatch):
    f = _folder(lab, disk, comments=())
    assert lab.owner.get(f"/api/folder-links/page/{f.page}").json()["vault"]["mine"] is True
    assert lab.editor.get(f"/api/folder-links/page/{f.page}").json()["vault"]["mine"] is False

    library = make_page(lab.owner, "open elsewhere")["id"]
    monkeypatch.setattr(collab, "open_pages", lambda: [(lab.ws, f.page), (lab.ws, library), ("otherWs", f.page)])
    heard: list = []
    monkeypatch.setattr(collab, "publish", lambda ws, page_id, message, exclude="": heard.append((ws, page_id, message)))
    folder_links.set_agent(f.id, "", by=lab.user)
    assert heard == [(lab.ws, f.page, {"t": "vault"})]
    folder_links.set_agent(f.id, "", by=lab.user)  # no change: nothing to hear
    assert len(heard) == 1
    folder_links.set_agent(f.id, "codex", by=lab.user)
    assert heard[-1] == (lab.ws, f.page, {"t": "vault"}) and len(heard) == 2
    assert runner.wait_idle(30)
