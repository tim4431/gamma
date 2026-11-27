"""An assistant over MCP asking the person about a change in two places at
once (gamma/mcp_server.py ``_ask_both``, gamma/agent_asks.py): its client's
question (elicitation) and Gamma, where the change is drawn on its page and
answered as a chat card is. The first answer decides; answered in Gamma,
the client's question is withdrawn, each ask its own when one session asks
two at once; either way Gamma draws it no more, nor once the call is
cancelled. The page's read lists the questions drawn on it, for its
account's signed-in sessions alone: the connection's own token neither
reads nor answers them. A real client's round trip is the browser suite's
MCP group."""

import threading
import time
from types import SimpleNamespace

import anyio

from conftest import account_of, fresh_client, login, make_page, make_user, token_for, workspace_of
from gamma import agent_asks, ai_permissions, mcp_server

PREVIEW = {"page_id": "p1", "title": "Paper", "block_id": "b1", "mode": "append", "text": "Draft\nMore."}
QUESTION = "Codex wants to add to a note."


class Session:
    """The client's side of an elicitation, as the SDK's session shows it to
    the server: ``answer`` is what it answers (None: it waits until
    cancelled; an exception: it cannot show the question). ``asked`` holds
    the id each question went out under, ``withdrawn`` the id each call's
    cancel named, by the call."""

    def __init__(self, answer=None):
        self._request_id = 7
        self.answer = answer
        self.sent = []
        self.asked = {}
        self.withdrawn = {}

    async def elicit_form(self, question, schema, related_request_id=None):
        # the id is taken as the request goes out, as the SDK's send_request takes it
        self.asked[question] = self._request_id
        self._request_id += 1
        if isinstance(self.answer, Exception):
            raise self.answer
        if self.answer is None:
            await anyio.sleep_forever()
        return self.answer

    async def send_notification(self, notification, related_request_id=None):
        self.sent.append(notification)
        self.withdrawn[related_request_id] = notification.root.params.requestId


def _conn(monkeypatch):
    monkeypatch.setattr(mcp_server, "of_connection", lambda conn: SimpleNamespace(label="Codex"))
    return SimpleNamespace(user_id="u-asks", token_id="t1")


def _ask(session, monkeypatch, shown=("ws1", PREVIEW)):
    context = SimpleNamespace(session=session, request_id=3)
    return anyio.run(mcp_server._ask_both, context, _conn(monkeypatch), "edit_block", QUESTION, shown)


def _answer_in_gamma(*answers):
    """The person's answers on the pages, ``(page_id, decision, note)`` each
    in turn, once its question is drawn there. Returns the function that
    waits for them and fails the test with what failed in the answering
    thread."""
    failed = []

    def answer():
        try:
            for page, decision, note in answers:
                for _ in range(100):
                    waiting = agent_asks.on_page("u-asks", "ws1", page)
                    if waiting:
                        assert waiting[0]["agent"] == "Codex" and waiting[0]["preview"] == {**PREVIEW, "page_id": page}
                        assert waiting[0]["tool"] == "edit_block" and waiting[0]["perm"] == "block_edit"
                        assert ai_permissions.answer(waiting[0]["id"], "u-asks", decision, note)
                        break
                    time.sleep(0.02)
                else:
                    raise AssertionError(f"no question was drawn on {page}")
        except BaseException as e:  # the test's failure, not the thread's
            failed.append(e)

    thread = threading.Thread(target=answer, daemon=True)
    thread.start()

    def answered():
        thread.join(10)
        if failed:
            raise failed[0]
        assert not thread.is_alive(), "the answers were never given"
    return answered


def test_answered_in_gamma_first_the_clients_question_is_withdrawn(monkeypatch):
    session = Session()
    answered = _answer_in_gamma(("p1", "deny", "use the other note"))
    assert _ask(session, monkeypatch) == ("deny", "use the other note")
    answered()
    [cancelled] = session.sent
    assert cancelled.root.method == "notifications/cancelled"
    assert cancelled.root.params.requestId == session.asked[QUESTION] == 7
    assert agent_asks.on_page("u-asks", "ws1", "p1") == []


def test_two_asks_on_one_session_each_withdraw_their_own_question(monkeypatch):
    """The asks of one session run at once and share its count of requests:
    each withdraws the id its own question went out under."""
    session, conn, answers = Session(), _conn(monkeypatch), {}

    async def ask(call, page):
        context = SimpleNamespace(session=session, request_id=call)
        answers[call] = await mcp_server._ask_both(context, conn, "edit_block", f"ask {call}",
                                                 ("ws1", {**PREVIEW, "page_id": page}))

    async def both():
        async with anyio.create_task_group() as group:
            group.start_soon(ask, "a", "p1")
            group.start_soon(ask, "b", "p2")

    answered = _answer_in_gamma(("p2", "deny", "not this one"), ("p1", "once", ""))
    anyio.run(both)
    answered()
    assert answers == {"a": ("once", ""), "b": ("deny", "not this one")}
    assert session.asked["ask a"] != session.asked["ask b"]
    assert session.withdrawn == {"a": session.asked["ask a"], "b": session.asked["ask b"]}


def test_a_call_cancelled_while_it_asks_ends_gammas_question_at_once(monkeypatch):
    """The client's cancel (or the session's end) stops Gamma's wait, whose
    thread cannot be abandoned, within a poll, not when its time is up."""
    session, conn = Session(), _conn(monkeypatch)
    context = SimpleNamespace(session=session, request_id=3)

    async def cancelled():
        with anyio.move_on_after(0.2):
            await mcp_server._ask_both(context, conn, "edit_block", QUESTION, ("ws1", PREVIEW))

    start = time.monotonic()
    anyio.run(cancelled)
    assert time.monotonic() - start < ai_permissions.APPROVAL_TIMEOUT / 2
    assert agent_asks.on_page("u-asks", "ws1", "p1") == [] and session.sent == []
    assert not [a for a in ai_permissions._pending.values() if a["user"] == "u-asks"]


def test_answered_in_the_client_first_gamma_draws_it_no_more(monkeypatch):
    session = Session(SimpleNamespace(action="accept", content={"remember": True}))
    assert _ask(session, monkeypatch) == ("always", "")
    assert session.sent == [] and agent_asks.on_page("u-asks", "ws1", "p1") == []
    assert not [a for a in ai_permissions._pending.values() if a["user"] == "u-asks"]
    declined = Session(SimpleNamespace(action="decline", content=None))
    assert _ask(declined, monkeypatch) == ("deny", "")


def test_a_client_that_cannot_show_it_and_a_question_with_nothing_to_draw(monkeypatch):
    assert _ask(Session(RuntimeError("no form")), monkeypatch)[0] == "cannot"
    assert agent_asks.on_page("u-asks", "ws1", "p1") == []
    # an undo's question, and a page made, have nothing to draw: the client alone asks
    assert _ask(Session(SimpleNamespace(action="accept", content={})), monkeypatch, shown=None) == ("once", "")
    made = ("ws1", {"title": "New notes", "diff": [["ins", "New notes"]]})
    assert _ask(Session(SimpleNamespace(action="accept", content={})), monkeypatch, shown=made) == ("once", "")


def test_a_move_to_another_page_is_asked_where_the_note_is_now():
    approval = agent_asks.ask("u-asks", "ws1", {**PREVIEW, "page_id": "p2", "src_page_id": "p1"},
                              tool="move_block", agent="Codex")
    try:
        assert [a["id"] for a in agent_asks.on_page("u-asks", "ws1", "p1")] == [approval]
        assert agent_asks.on_page("u-asks", "ws1", "p2") == []
    finally:
        agent_asks.settle(approval)


def test_the_pages_read_lists_the_questions_waiting_on_it_for_its_account(client):
    make_user("asks-reader", "pw")
    c = login("asks-reader", "pw")
    me, ws = account_of("asks-reader"), workspace_of("asks-reader")
    page = make_page(c, "Asked page")["id"]
    preview = {**PREVIEW, "page_id": page}
    approval = agent_asks.ask(me, ws, preview, tool="edit_block", agent="Codex")
    other = agent_asks.ask("someone-else", ws, preview, tool="edit_block", agent="Codex")
    try:
        asks = c.get("/api/ai/changes", params={"page_id": page}).json()["asks"]
        assert asks == [{"id": approval, "tool": "edit_block", "perm": "block_edit", "preview": preview, "agent": "Codex"}]
        # answered as a chat card is
        assert c.post(f"/api/ai/approvals/{approval}", json={"decision": "once", "note": ""}).status_code == 200
        assert ai_permissions.wait_for(approval, timeout=1) == ("once", "")
    finally:
        agent_asks.settle(approval)
        agent_asks.settle(other)
    assert c.get("/api/ai/changes", params={"page_id": page}).json()["asks"] == []


def test_the_connections_own_token_neither_reads_nor_answers_its_question(client):
    """Only the person answers in Gamma: the token an assistant works
    through, which reaches the HTTP API too, finds no question to answer,
    "don't ask again" least of all, while the cards of a chat it streams
    itself are still its to answer."""
    make_user("asks-token", "pw")
    c = login("asks-token", "pw")
    me, ws = account_of("asks-token"), workspace_of("asks-token")
    page = make_page(c, "Asked page")["id"]
    agent = fresh_client()
    agent.headers["Authorization"] = f"Bearer {token_for(c, 'read', 'Codex')}"
    approval = agent_asks.ask(me, ws, {**PREVIEW, "page_id": page}, tool="edit_block", agent="Codex")
    card = ai_permissions.open_approval(me)  # a card of the token's own chat
    try:
        r = agent.get("/api/ai/changes", params={"page_id": page})
        assert r.status_code == 200 and r.json()["asks"] == [], r.text
        assert [a["id"] for a in c.get("/api/ai/changes", params={"page_id": page}).json()["asks"]] == [approval]
        r = agent.post(f"/api/ai/approvals/{approval}", json={"decision": "always"})
        assert r.status_code == 404, r.text
        assert agent.post(f"/api/ai/approvals/{card}", json={"decision": "once"}).status_code == 200
        # it still waits, for the person
        assert c.post(f"/api/ai/approvals/{approval}", json={"decision": "deny"}).status_code == 200
        assert ai_permissions.wait_for(approval, timeout=1) == ("deny", "")
        assert ai_permissions.wait_for(card, timeout=1) == ("once", "")
    finally:
        agent_asks.settle(approval)
        ai_permissions.wait_for(card, timeout=0)
        agent.close()
