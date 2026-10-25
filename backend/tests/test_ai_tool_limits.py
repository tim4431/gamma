"""The tool loop's limits (gamma/ai_agent.py AgentLoop): a reply the round
limit, the change limit or the model's window stopped ends on a {"limit"}
line the chat offers Continue on; Unlimited rounds lifts the round and change
limits for a streamed reply; and Stop ends the loop, a read_paper helper's
too, without opening another provider turn."""

import json
import threading
import time

import pytest

from gamma.ai_tools import CHANGE_LIMIT_PREFIX

from ai_fixtures import ALLOW_ALL, FakeResp, ai_provider, org  # noqa: F401  (fixtures)


@pytest.fixture(scope="module", autouse=True)
def _provider(ai_provider):
    """Every chat here needs a provider entry on the module's account."""


@pytest.fixture(autouse=True)
def _offline_model_facts(monkeypatch):
    monkeypatch.setattr("gamma.ai_catalog.context_window", lambda *args: (0, ""))
    monkeypatch.setattr("gamma.ai_catalog.image_input", lambda *args: (None, ""))


def _tool_turn(cid, name, args):
    """A streamed Anthropic turn that calls one tool."""
    return FakeResp([
        {"type": "content_block_start", "content_block": {"type": "tool_use", "id": cid, "name": name}},
        {"type": "content_block_delta", "delta": {"type": "input_json_delta", "partial_json": json.dumps(args)}},
        {"type": "content_block_stop"}])


def _renaming(ids, rounds):
    """A fake _open_ai whose first ``rounds`` turns rename page a, after
    which it answers "Done."; ``.opens`` counts the turns it opened."""
    def fake_open(messages, system, entry, rt, pdf_b64s=None, **kw):
        fake_open.opens += 1
        n = fake_open.opens
        if n <= rounds:
            return _tool_turn(f"t{n}", "rename_page", {"page_id": ids["a"], "title": f"limits {n}"})
        return FakeResp([{"type": "content_block_delta", "delta": {"type": "text_delta", "text": "Done."}}])
    fake_open.opens = 0
    return fake_open


def _chat(c, ids, **body):
    """A folder chat on "readout" that may change anything, its NDJSON lines
    (the JSON body when ``stream`` is False)."""
    r = c.post("/api/ai/chat", json={"prompt": "rename it", "agent_scope": "folder", "folder": ids["readout"],
                                     "stream": True, "permissions": ALLOW_ALL, **body})
    assert r.status_code == 200, r.text
    if body.get("stream") is False:
        return r.json()
    return [json.loads(line) for line in r.text.splitlines() if line.strip()]


def _text(lines):
    return "".join(line.get("delta", "") for line in lines)


def test_the_change_limit_ends_the_reply_on_a_limit_line(org, monkeypatch):
    c, ids = org
    import gamma.routers.ai as ai_mod

    monkeypatch.setattr("gamma.ai_agent.MAX_TOOL_ACTIONS", 2)
    fake = _renaming(ids, 4)
    monkeypatch.setattr(ai_mod, "_open_ai", fake)
    lines = _chat(c, ids)
    actions = [line["action"] for line in lines if "action" in line]
    assert [bool(a.get("error")) for a in actions] == [False, False, True, True]
    assert actions[2]["result"].startswith(CHANGE_LIMIT_PREFIX) and "(2 changes)" in actions[2]["result"]
    # The model answered after the refusals; the reply still says it stopped short.
    assert lines[-1] == {"limit": {"kind": "changes", "rounds": 5, "changes": 2}}
    assert fake.opens == 5 and _text(lines) == "Done."


def test_the_round_limit_ends_the_reply_with_its_line_and_a_limit_line(org, monkeypatch):
    c, ids = org
    import gamma.routers.ai as ai_mod

    fake = _renaming(ids, 4)
    monkeypatch.setattr(ai_mod, "_open_ai", fake)
    lines = _chat(c, ids, tool_rounds=2)
    assert [line["action"]["tool"] for line in lines if "action" in line] == ["rename_page"] * 2
    assert _text(lines).endswith("*(stopped: tool-round limit reached)*")
    assert lines[-1] == {"limit": {"kind": "rounds", "rounds": 2, "changes": 2}}
    assert fake.opens == 2
    # A change refused at the change limit names that limit, not the rounds:
    # more rounds would not have helped.
    monkeypatch.setattr("gamma.ai_agent.MAX_TOOL_ACTIONS", 1)
    monkeypatch.setattr(ai_mod, "_open_ai", _renaming(ids, 4))
    lines = _chat(c, ids, tool_rounds=3)
    assert lines[-1]["limit"]["kind"] == "changes"


def test_unlimited_rounds_lift_both_limits_for_a_streamed_reply(org, monkeypatch):
    c, ids = org
    import gamma.routers.ai as ai_mod

    monkeypatch.setattr("gamma.ai_agent.MAX_TOOL_ACTIONS", 2)
    fake = _renaming(ids, 6)
    monkeypatch.setattr(ai_mod, "_open_ai", fake)
    lines = _chat(c, ids, tool_rounds=1, tool_rounds_unlimited=True)
    actions = [line["action"] for line in lines if "action" in line]
    assert [a["tool"] for a in actions] == ["rename_page"] * 6 and not any(a.get("error") for a in actions)
    assert fake.opens == 7 and not [line for line in lines if "limit" in line]
    assert _text(lines) == "Done."
    # Nothing could stop a reply that is not streamed: it keeps its limits.
    fake = _renaming(ids, 6)
    monkeypatch.setattr(ai_mod, "_open_ai", fake)
    body = _chat(c, ids, tool_rounds=1, tool_rounds_unlimited=True, stream=False)
    assert body["limit"] == {"kind": "rounds", "rounds": 1, "changes": 1}
    assert fake.opens == 1 and "tool-round limit" in body["response"]


def test_the_window_ends_the_reply_on_a_limit_line_not_an_error(org, monkeypatch):
    """A round the provider still calls too long once every earlier result
    is a stub ends the reply as a reply, which the next message replays."""
    c, ids = org
    import gamma.routers.ai as ai_mod

    opens = []

    def fake_open(messages, system, entry, rt, pdf_b64s=None, **kw):
        opens.append(1)
        if len(opens) == 1:
            return _tool_turn("t1", "list_pages", {})
        raise ai_mod.UpstreamError(400, "upstream 400: prompt is too long: 250000 tokens > 200000 maximum")

    monkeypatch.setattr(ai_mod, "_open_ai", fake_open)
    lines = _chat(c, ids)
    assert len(opens) == 3  # the turn, the next one, and its retry with the results elided
    assert not [line for line in lines if "error" in line]
    assert _text(lines).endswith("*(stopped: the conversation grew past the model's window)*")
    assert lines[-1] == {"limit": {"kind": "window", "rounds": 1, "changes": 0}}


# --- Stop --------------------------------------------------------------------------

class _Turn:
    """A provider turn as ``read_events=iter`` reads it: (kind, data) events,
    counting how many were read, and whether it was closed. ``then`` runs
    once the last event was read, before the reader sees the turn end."""

    def __init__(self, events, then=None):
        self.events, self.read, self.closed, self.then = events, 0, False, then

    def __iter__(self):
        for event in self.events:
            self.read += 1
            yield event
        if self.then:
            self.then()

    def close(self):
        self.closed = True


def _looping(stopped, turns, stop_at=0, stop_after=0):
    """An unlimited loop whose every turn calls a tool no chat has, so it
    never answers ("unknown tool" needs no database); its ``open_round``
    (which sets ``stopped`` as it opens turn ``stop_at``, or once turn
    ``stop_after`` streamed its last event) records its turns."""
    from gamma.ai_agent import AgentLoop, Conversation

    def open_round(talk=None):
        n = len(turns) + 1
        turn = _Turn([("text", "."), ("tool", {"id": f"c{n}", "name": "nope", "arguments": {}})],
                     then=stopped.set if n == stop_after else None)
        turns.append(turn)
        if n == stop_at:
            stopped.set()
        return turn
    loop = AgentLoop(ws="x", scope={"type": "folder"}, tools=[],
                     conversation=Conversation([{"role": "user", "content": "go"}]),
                     open_round=open_round, read_events=iter, unbounded=True, stopped=stopped)
    return loop, open_round


def test_stop_ends_an_unlimited_loop_at_its_next_line():
    from gamma.routers.ai import keepalive_lines

    # Through the relay, as the chat streams it: Stop sets the event.
    stopped, turns, closed = threading.Event(), [], threading.Event()
    loop, open_round = _looping(stopped, turns)

    def lines():
        try:
            for kind, data in loop.run(open_round()):
                yield json.dumps({kind: data}) + "\n"
        finally:
            closed.set()

    relay = keepalive_lines(lines(), "test", abandoned=stopped)
    for _ in range(20):
        next(relay)
    stopped.set()
    assert closed.wait(5)
    opened = len(turns)
    time.sleep(0.3)
    assert len(turns) == opened
    relay.close()
    # Without the relay: the turn streaming when Stop lands ends at its next
    # event, and no turn opens after it.
    stopped, turns = threading.Event(), []
    loop, open_round = _looping(stopped, turns, stop_at=3)
    events = list(loop.run(open_round()))
    assert len(turns) == 3 and turns[2].read == 1 and turns[2].closed
    assert events[-1][0] == "action"  # the second round's call; nothing of the third turn
    # Between a round's last line and the next turn: the round's call runs,
    # and no turn opens after it.
    stopped, turns = threading.Event(), []
    loop, open_round = _looping(stopped, turns, stop_after=2)
    events = list(loop.run(open_round()))
    assert len(turns) == 2 and turns[1].read == 2 and events[-1][0] == "action"
    # While the provider weighs a turn it then refuses as too long: its
    # retry with the results elided does not open.
    from gamma.ai_client import UpstreamError

    stopped, turns = threading.Event(), []
    loop, open_round = _looping(stopped, turns)
    first = open_round()

    def refused(talk):
        turns.append(None)
        stopped.set()
        raise UpstreamError(400, "upstream 400: prompt is too long: 250000 tokens > 200000 maximum")

    loop.open_round = refused
    events = list(loop.run(first))
    assert len(turns) == 2 and events[-1][0] == "action"  # no retry, and no window line


def test_a_helper_opens_no_turn_once_the_chat_stopped():
    from gamma.ai_agent import Helper

    stopped, opens = threading.Event(), []

    def open_call(talk, tools):
        opens.append(1)
        stopped.set()  # Stop lands while the helper's first turn streams
        return _Turn([("tool", {"id": "c1", "name": "nope", "arguments": {}})])

    helper = Helper(ws="x", open_call=open_call, read_events=iter, stopped=stopped)
    job = {"scope": {"type": "folder"}, "question": "q", "system": "", "tools": [], "label": "x"}
    assert helper.run(**job)["text"] == "" and len(opens) == 1
    # Stopped before the job starts: it opens nothing.
    opens.clear()
    assert helper.run(**job)["text"] == "" and not opens
    # Stopped once its first turn streamed its last event: the turn's call
    # runs, no further turn opens, and what it wrote is no answer.
    late, late_opens = threading.Event(), []

    def open_late(talk, tools):
        late_opens.append(1)
        return _Turn([("text", "Half an answer."), ("tool", {"id": "c1", "name": "nope", "arguments": {}})],
                     then=late.set)

    done = Helper(ws="x", open_call=open_late, read_events=iter, stopped=late).run(**job)
    assert done["text"] == "" and len(done["actions"]) == 1 and len(late_opens) == 1


def test_the_chat_hands_its_stop_to_the_loop_and_its_helper(org, monkeypatch):
    """The request's one ``stopped`` event (WatchedStream sets it) reaches
    the loop, its approval gate and the read_paper helper."""
    c, ids = org
    import gamma.routers.ai as ai_mod

    seen = {}

    class Loop(ai_mod.AgentLoop):
        def __init__(self, **kw):
            super().__init__(**kw)
            seen["loop"] = self

    class Helper(ai_mod.Helper):
        def __init__(self, **kw):
            super().__init__(**kw)
            seen["helper"] = self

    monkeypatch.setattr(ai_mod, "AgentLoop", Loop)
    monkeypatch.setattr(ai_mod, "Helper", Helper)
    monkeypatch.setattr(ai_mod, "_open_ai", _renaming(ids, 0))
    assert _text(_chat(c, ids)) == "Done."
    stopped = seen["loop"].stopped
    assert isinstance(stopped, threading.Event)
    assert seen["loop"].gate.stopped is stopped and seen["helper"].stopped is stopped
