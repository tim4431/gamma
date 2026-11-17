"""Steer: a message the user hands to their chat reply while it runs
(gamma/ai_steer.py) cuts the provider turn short at its next event, as
Codex's instant interrupt does — what the model wrote so far is its turn,
the round's calls not started are skipped, those running finish — and
becomes the next turn's; the stream says where. A reply that ended takes
none."""

import json

import pytest

from ai_fixtures import ALLOW_ALL, FakeResp, ai_provider, org, text_stream  # noqa: F401  (fixtures)
from gamma.ai_protocols.base import attach_index


@pytest.fixture(scope="module", autouse=True)
def _provider(ai_provider):
    """Every chat here needs a provider entry on the module's account."""


@pytest.fixture(autouse=True)
def _offline_model_facts(monkeypatch):
    monkeypatch.setattr("gamma.ai_catalog.context_window", lambda *args: (0, ""))
    monkeypatch.setattr("gamma.ai_catalog.image_input", lambda *args: (None, ""))


def _steer(client, item_id, text):
    """The user steers ``text`` into their running reply, through the
    endpoint, under the id the stream gave (the only reply running)."""
    from gamma import ai_steer
    [steering] = list(ai_steer._open.values())
    return client.post(f"/api/ai/chat/steer/{steering.id}", json={"id": item_id, "text": text})


class SteeredAfter(FakeResp):
    """A provider turn the user steers ``text`` into once its first
    ``after`` events have streamed."""

    def __init__(self, events, client, item_id, text, after):
        super().__init__(events)
        self.steer = lambda: _steer(client, item_id, text)
        self.after = after

    def __iter__(self):
        for i, line in enumerate(self._lines):
            if i == self.after:
                r = self.steer()
                assert r.status_code == 200, r.text
            yield line


def _lines(r):
    assert r.status_code == 200, r.text
    return [json.loads(line) for line in r.text.splitlines() if line.strip()]


def _said(lines):
    """The reply's text, with ``|`` where a steered message came in."""
    return "".join(line.get("delta") or ("|" if "steered" in line else "") for line in lines)


def _text(t):
    return {"type": "content_block_delta", "delta": {"type": "text_delta", "text": t}}


def _call(call_id, name, args):
    return [{"type": "content_block_start", "content_block": {"type": "tool_use", "id": call_id, "name": name}},
            {"type": "content_block_delta", "delta": {"type": "input_json_delta", "partial_json": json.dumps(args)}},
            {"type": "content_block_stop"}]


def _steer_during_calls(monkeypatch, client, item_id, text):
    """The user steers in while the reply's first tool call runs; returns
    the names of the calls that ran."""
    import gamma.ai_agent as agent
    ran, real = [], agent.run_agent_tool

    def run(ws, scope, name, args, **kw):
        if not ran:
            assert _steer(client, item_id, text).status_code == 200
        ran.append(name)
        return real(ws, scope, name, args, **kw)

    monkeypatch.setattr(agent, "run_agent_tool", run)
    return ran


def test_a_message_steered_in_while_the_model_writes_cuts_its_turn_short(org, monkeypatch):
    c, ids = org
    import gamma.routers.ai as ai_mod

    opened = []

    def fake_open(messages, system, entry, rt, pdf_b64s=None, **kw):
        opened.append([dict(m) for m in messages])
        if len(opened) == 1:
            return SteeredAfter([_text("Two papers"), _text(" and a long list of them follows")],
                                c, "q1", "just the count, please", after=1)
        return text_stream(["Two."])

    monkeypatch.setattr(ai_mod, "_open_ai", fake_open)
    lines = _lines(c.post("/api/ai/chat", json={"prompt": "how many papers?", "agent_scope": "folder",
                                                "folder": ids["readout"], "stream": True,
                                                "permissions": ALLOW_ALL}))
    assert any("steer" in line for line in lines[:4])
    # The rest of the first turn never streams: the steered message comes in where it stopped.
    assert _said(lines) == "Two papers|Two."
    assert next(line["steered"] for line in lines if "steered" in line) == {"ids": ["q1"]}
    second = opened[1]
    assert second[-2:] == [{"role": "assistant", "content": "Two papers"},
                           {"role": "user", "content": "just the count, please", "steered": True}]
    # The question's own attachments stay on the question, not the steered turn.
    assert attach_index(second) == len(second) - 3


def test_calls_running_when_the_user_steers_finish_and_those_not_started_are_skipped(org, monkeypatch):
    c, ids = org
    import gamma.routers.ai as ai_mod

    opened = []

    def fake_open(messages, system, entry, rt, pdf_b64s=None, **kw):
        opened.append([dict(m) for m in messages])
        if len(opened) == 1:
            # Two changes run one after another; the user steers in during the first.
            return FakeResp(_call("t1", "rename_page", {"page_id": ids["a"], "title": "Cavity A"})
                            + _call("t2", "rename_page", {"page_id": ids["b"], "title": "QEC B"}))
        return text_stream(["Renamed the first only."])

    monkeypatch.setattr(ai_mod, "_open_ai", fake_open)
    ran = _steer_during_calls(monkeypatch, c, "q2", "leave the qec paper alone")
    lines = _lines(c.post("/api/ai/chat", json={"prompt": "rename both papers", "agent_scope": "folder",
                                                "folder": ids["readout"], "stream": True,
                                                "permissions": ALLOW_ALL}))
    assert ran == ["rename_page"]
    kinds = [next(iter(line)) for line in lines if next(iter(line)) in ("action", "steered", "delta")]
    assert kinds == ["action", "steered", "delta"]
    second = opened[1]
    assert [m["role"] for m in second[-4:]] == ["assistant", "tool", "tool", "user"]
    assert second[-3]["content"].startswith("ok")
    assert second[-2]["content"].startswith("not run: the user sent a new message")
    assert second[-1]["content"] == "leave the qec paper alone"


def test_the_last_round_a_limit_allows_is_not_cut(org, monkeypatch):
    c, ids = org
    import gamma.routers.ai as ai_mod

    def fake_open(messages, system, entry, rt, pdf_b64s=None, **kw):
        return SteeredAfter([_text("Listing"), _text(" all of them.")], c, "q3", "stop", after=1)

    monkeypatch.setattr(ai_mod, "_open_ai", fake_open)
    lines = _lines(c.post("/api/ai/chat", json={"prompt": "list", "agent_scope": "folder",
                                                "folder": ids["readout"], "stream": True,
                                                "permissions": ALLOW_ALL, "tool_rounds": 1}))
    # No round is left to answer the message in: the turn ends whole, and
    # the client sends the message as the next one.
    assert _said(lines) == "Listing all of them."


def test_a_plain_chat_is_cut_short_and_goes_on_with_the_message(org, monkeypatch):
    c, _ = org
    import gamma.routers.ai as ai_mod

    opened = []

    def fake_open(messages, system, entry, rt, pdf_b64s=None, tools=None, **kw):
        assert not tools
        opened.append([dict(m) for m in messages])
        if len(opened) == 1:
            return SteeredAfter([_text("Hello"), _text(", and welcome to")], c, "q4", "in French", after=1)
        return text_stream(["Bonjour."])

    monkeypatch.setattr(ai_mod, "_open_ai", fake_open)
    lines = _lines(c.post("/api/ai/chat", json={"prompt": "say hello", "stream": True}))
    assert opened[1][-2:] == [{"role": "assistant", "content": "Hello"},
                              {"role": "user", "content": "in French", "steered": True}]
    assert _said(lines) == "Hello|Bonjour."


def test_a_reply_that_ended_or_is_another_accounts_takes_nothing(org, monkeypatch):
    c, _ = org
    import gamma.routers.ai as ai_mod
    from conftest import login, make_user

    monkeypatch.setattr(ai_mod, "_open_ai", lambda *a, **kw: text_stream(["ok"]))
    lines = _lines(c.post("/api/ai/chat", json={"prompt": "hi", "stream": True}))
    reply = next(line["steer"] for line in lines if "steer" in line)
    # Over: the client sends the message as the next one instead.
    assert c.post(f"/api/ai/chat/steer/{reply}", json={"id": "late", "text": "too late"}).status_code == 404
    assert c.post("/api/ai/chat/steer/no-such-reply", json={"id": "x", "text": "hi"}).status_code == 404

    make_user("steer-stranger", "pw")
    other = login("steer-stranger", "pw")
    tried = []

    class TriedByAnother(FakeResp):
        def __iter__(self):
            tried.append(_steer(other, "x", "hijack"))
            return super().__iter__()

    monkeypatch.setattr(ai_mod, "_open_ai", lambda *a, **kw: TriedByAnother([_text("mine")]))
    lines = _lines(c.post("/api/ai/chat", json={"prompt": "hi", "stream": True}))
    assert tried[0].status_code == 404
    assert _said(lines) == "mine"
