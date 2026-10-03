"""Token usage: the wire parser's ("usage", …) event per protocol, the
chat stream's {"usage"} line (one per agent round), the per-account
ai_usage rows behind GET /api/ai/usage, and the reset."""

import json

import pytest

from ai_fixtures import CONF, FakeResp, ai_provider, org, sse  # noqa: F401  (fixtures)
from gamma.ai_client import add_usage, normalize_usage, sse_events
from gamma.ai_protocols import WIRES

openai_request = WIRES["openai"].request


@pytest.fixture(scope="module", autouse=True)
def _provider(ai_provider):
    """Every chat here needs a provider entry on the module's account."""


def test_normalize_usage_per_protocol():
    # Anthropic counts the cached parts beside input_tokens; "input" is the whole prompt.
    assert normalize_usage({"input_tokens": 10, "output_tokens": 4, "cache_read_input_tokens": 90,
                            "cache_creation_input_tokens": 5}, "anthropic") == \
        {"input": 105, "output": 4, "cache_read": 90, "cache_write": 5}
    assert normalize_usage({"prompt_tokens": 20, "completion_tokens": 3,
                            "prompt_tokens_details": {"cached_tokens": 8}}, "openai") == \
        {"input": 20, "output": 3, "cache_read": 8, "cache_write": 0}
    assert normalize_usage({"input_tokens": 7, "output_tokens": 2,
                            "input_tokens_details": {"cached_tokens": 1}}, "chatgpt") == \
        {"input": 7, "output": 2, "cache_read": 1, "cache_write": 0}
    assert normalize_usage({"input_tokens": 0, "output_tokens": 0}, "openai-responses") is None
    assert normalize_usage(None, "openai") is None
    assert add_usage({"input": 1, "output": 2, "cache_read": 0, "cache_write": 0},
                     {"input": 3, "output": 4, "cache_read": 1, "cache_write": 0}) == \
        {"input": 4, "output": 6, "cache_read": 1, "cache_write": 0}


def test_openai_stream_asks_for_usage_chunk():
    body = json.loads(openai_request(CONF, [{"role": "user", "content": "hi"}], "", "m", stream=True).data)
    assert body["stream_options"] == {"include_usage": True}
    body = json.loads(openai_request(CONF, [{"role": "user", "content": "hi"}], "", "m").data)
    assert "stream_options" not in body


def test_sse_events_report_usage_last():
    anthropic = sse(
        {"type": "message_start", "message": {"usage": {"input_tokens": 12, "output_tokens": 1,
                                                        "cache_read_input_tokens": 100}}},
        {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "hi"}},
        {"type": "message_delta", "delta": {"stop_reason": "end_turn"}, "usage": {"output_tokens": 9}},
    )
    assert list(sse_events(anthropic, "anthropic")) == [
        ("text", "hi"), ("usage", {"input": 112, "output": 9, "cache_read": 100, "cache_write": 0}),
        ("stop", "end_turn")]  # the provider's stop reason closes every stream

    openai = sse(
        {"choices": [{"delta": {"content": "hi"}}]},
        {"choices": [], "usage": {"prompt_tokens": 30, "completion_tokens": 5}},
    )
    assert list(sse_events(openai, "openai")) == [
        ("text", "hi"), ("usage", {"input": 30, "output": 5, "cache_read": 0, "cache_write": 0})]

    responses = sse(
        {"type": "response.output_text.delta", "delta": "hi"},
        {"type": "response.completed", "response": {"status": "completed",
                                                    "usage": {"input_tokens": 3, "output_tokens": 2}}},
    )
    assert list(sse_events(responses, "chatgpt")) == [
        ("text", "hi"), ("usage", {"input": 3, "output": 2, "cache_read": 0, "cache_write": 0}),
        ("stop", "completed")]

    # No report from the provider: no event, and nothing else changes.
    assert list(sse_events(sse({"choices": [{"delta": {"content": "x"}}]}), "openai")) == [("text", "x")]


def _turn(text, usage):
    return FakeResp([
        {"type": "message_start", "message": {"usage": usage}},
        {"type": "content_block_delta", "delta": {"type": "text_delta", "text": text}},
        {"type": "message_delta", "delta": {"stop_reason": "end_turn"}, "usage": {"output_tokens": 4}},
    ])


def test_chat_stream_emits_usage_and_records_it(org, monkeypatch):
    c, ids = org
    import gamma.routers.ai as ai_mod

    assert c.delete("/api/ai/usage").status_code == 200
    monkeypatch.setattr(ai_mod, "_open_ai",
                        lambda *a, **kw: _turn("Hello.", {"input_tokens": 50, "output_tokens": 1}))
    r = c.post("/api/ai/chat", json={"prompt": "hi", "stream": True})
    assert r.status_code == 200, r.text
    lines = [json.loads(l) for l in r.text.splitlines() if l.strip()]
    assert "".join(l.get("delta", "") for l in lines) == "Hello."
    # The usage line is the last one.
    assert lines[-1] == {"usage": {"input": 50, "output": 4, "cache_read": 0, "cache_write": 0}}

    s = c.get("/api/ai/usage").json()
    assert s["windows"]["today"] == {"calls": 1, "input": 50, "output": 4, "cache_read": 0, "cache_write": 0}
    assert s["windows"]["all"]["calls"] == 1 and s["kinds"] == {"chat": s["windows"]["today"]}
    assert s["models"][0]["model"] == "claude-solo" and s["models"][0]["input"] == 50

    # Non-stream callers get the counts in the body.
    class _Ctx(FakeResp):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def fake_read(resp, proto, on_usage=None):
        on_usage({"input": 7, "output": 1, "cache_read": 0, "cache_write": 0})
        return "ok"

    monkeypatch.setattr(ai_mod, "_open_ai", lambda *a, **kw: _Ctx([]))
    monkeypatch.setattr(ai_mod, "_read_reply", fake_read)
    r = c.post("/api/ai/chat", json={"prompt": "hi", "stream": False})
    assert r.status_code == 200 and r.json()["usage"]["input"] == 7
    assert c.get("/api/ai/usage").json()["windows"]["today"]["calls"] == 2


def test_agent_rounds_each_report_usage(org, monkeypatch):
    c, ids = org
    import gamma.routers.ai as ai_mod

    assert c.delete("/api/ai/usage").status_code == 200
    calls = []

    def fake_open(messages, system, entry, rt, pdf_b64s=None, **kw):
        calls.append(1)
        if len(calls) == 1:
            return FakeResp([
                {"type": "message_start", "message": {"usage": {"input_tokens": 100, "output_tokens": 1}}},
                {"type": "content_block_start", "content_block":
                    {"type": "tool_use", "id": "t1", "name": "list_pages"}},
                {"type": "content_block_delta", "delta": {"type": "input_json_delta", "partial_json": "{}"}},
                {"type": "content_block_stop"},
                {"type": "message_delta", "delta": {"stop_reason": "tool_use"}, "usage": {"output_tokens": 20}},
            ])
        return _turn("Done.", {"input_tokens": 300, "output_tokens": 1})

    monkeypatch.setattr(ai_mod, "_open_ai", fake_open)
    r = c.post("/api/ai/chat", json={"prompt": "list", "agent_scope": "folder", "folder": "",
                                     "stream": True})
    assert r.status_code == 200, r.text
    usage = [l["usage"] for l in (json.loads(l) for l in r.text.splitlines() if l.strip()) if "usage" in l]
    assert [u["input"] for u in usage] == [100, 300] and [u["output"] for u in usage] == [20, 4]
    today = c.get("/api/ai/usage").json()["windows"]["today"]
    assert today == {"calls": 2, "input": 400, "output": 24, "cache_read": 0, "cache_write": 0}

    # Reset forgets everything.
    assert c.delete("/api/ai/usage").json()["deleted"] == 2
    assert c.get("/api/ai/usage").json()["windows"]["all"]["calls"] == 0


def test_daily_calendar_utc_boundaries_totals_and_reset(org, monkeypatch):
    from datetime import datetime, timezone
    from gamma import ai_usage
    from gamma.db import connect_users_db

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2024, 3, 1, 12, tzinfo=timezone.utc)

    monkeypatch.setattr(ai_usage, "datetime", Clock)
    c, ids = org
    c.delete("/api/ai/usage")
    with connect_users_db() as conn:
        for user, at, tokens in [
            (ids["user_id"], "2023-03-02T23:59:59", 999),  # outside the calendar
            (ids["user_id"], "2023-03-03T00:00:00", 10),
            (ids["user_id"], "2024-02-29T00:00:00", 20),
            (ids["user_id"], "2024-02-29T23:59:59", 30),
            (ids["user_id"], "2024-03-01T00:00:00", 40),
            ("another-account", "2024-03-01T00:00:00", 9999),
        ]:
            conn.execute(
                "INSERT INTO ai_usage (user_id, at, kind, provider_id, provider_name, model, "
                "input, output, cache_read, cache_write) VALUES (?, ?, 'chat', 'own', 'Own', 'model', ?, 2, 3, 1)",
                (user, at, tokens))
    data = c.get("/api/ai/usage").json()
    daily = data["daily"]
    assert len(daily) == 365
    assert daily[0]["date"] == "2023-03-03" and daily[0]["input"] == 10
    assert daily[-2] == {"date": "2024-02-29", "calls": 2, "input": 50, "output": 4, "cache_read": 6, "cache_write": 2}
    assert daily[-1] == {"date": "2024-03-01", **data["windows"]["today"]}
    assert daily[1] == {"date": "2023-03-04", "calls": 0, "input": 0, "output": 0, "cache_read": 0, "cache_write": 0}
    assert sum(day["calls"] for day in daily) == 4
    c.delete("/api/ai/usage")
    assert all(day["calls"] == 0 for day in c.get("/api/ai/usage").json()["daily"])


def _model_lines(text):
    return [l["model"] for l in (json.loads(l) for l in text.splitlines() if l.strip()) if "model" in l]


def test_reply_speed_is_what_the_provider_served(org, monkeypatch):
    """The reply's ``speed`` is the tier that served it, not the one asked
    for: the stream's head names the tier the wire asked for, and when the
    provider's report says it ran at another, the ``model`` line comes
    again, corrected, before the usage line (which stays counts only). A
    provider that says nothing leaves the asked-for tier standing."""
    c, ids = org
    import gamma.routers.ai as ai_mod

    def turn(usage):
        return FakeResp([
            {"type": "message_start", "message": {"usage": usage}},
            {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "Hello."}},
            {"type": "message_delta", "delta": {"stop_reason": "end_turn"}, "usage": {"output_tokens": 4}},
        ])

    # Asked for fast on Anthropic itself, served standard.
    monkeypatch.setattr(ai_mod, "_open_ai",
                        lambda *a, **kw: turn({"input_tokens": 50, "output_tokens": 1, "speed": "standard"}))
    r = c.post("/api/ai/chat", json={"prompt": "hi", "stream": True, "speed": "fast"})
    assert r.status_code == 200, r.text
    lines = [json.loads(l) for l in r.text.splitlines() if l.strip()]
    assert [m["speed"] for m in _model_lines(r.text)] == ["fast", ""]
    assert lines[-2]["model"]["speed"] == "" and lines[-1] == {
        "usage": {"input": 50, "output": 4, "cache_read": 0, "cache_write": 0}}
    # Served as asked: one model line, fast.
    monkeypatch.setattr(ai_mod, "_open_ai",
                        lambda *a, **kw: turn({"input_tokens": 50, "output_tokens": 1, "speed": "fast"}))
    r = c.post("/api/ai/chat", json={"prompt": "hi", "stream": True, "speed": "fast"})
    assert [m["speed"] for m in _model_lines(r.text)] == ["fast"]
    # The provider says nothing: what was asked for stands.
    monkeypatch.setattr(ai_mod, "_open_ai", lambda *a, **kw: turn({"input_tokens": 50, "output_tokens": 1}))
    r = c.post("/api/ai/chat", json={"prompt": "hi", "stream": True, "speed": "fast"})
    assert [m["speed"] for m in _model_lines(r.text)] == ["fast"]
    # Nothing asked for: "" throughout, and a "standard" report changes nothing.
    monkeypatch.setattr(ai_mod, "_open_ai",
                        lambda *a, **kw: turn({"input_tokens": 50, "output_tokens": 1, "speed": "standard"}))
    r = c.post("/api/ai/chat", json={"prompt": "hi", "stream": True})
    assert [m["speed"] for m in _model_lines(r.text)] == [""]

    # A non-streamed reply carries the served speed in its model field.
    class _Ctx(FakeResp):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def fake_read(resp, proto, on_usage=None):
        on_usage({"input": 7, "output": 1, "cache_read": 0, "cache_write": 0, "speed": ""})
        return "ok"

    monkeypatch.setattr(ai_mod, "_open_ai", lambda *a, **kw: _Ctx([]))
    monkeypatch.setattr(ai_mod, "_read_reply", fake_read)
    r = c.post("/api/ai/chat", json={"prompt": "hi", "stream": False, "speed": "fast"})
    assert r.status_code == 200, r.text
    assert r.json()["model"]["speed"] == "" and "speed" not in r.json()["usage"]


def test_agent_reply_speed_follows_the_rounds(org, monkeypatch):
    """Over an agent reply's rounds the last report wins: a first round
    served fast and a second served standard leave the reply at standard,
    the model line re-sent once, where it changed."""
    c, ids = org
    import gamma.routers.ai as ai_mod
    calls = []

    def fake_open(messages, system, entry, rt, pdf_b64s=None, **kw):
        calls.append(1)
        if len(calls) == 1:
            return FakeResp([
                {"type": "message_start", "message": {"usage": {"input_tokens": 100, "output_tokens": 1,
                                                                "speed": "fast"}}},
                {"type": "content_block_start", "content_block":
                    {"type": "tool_use", "id": "t1", "name": "list_pages"}},
                {"type": "content_block_delta", "delta": {"type": "input_json_delta", "partial_json": "{}"}},
                {"type": "content_block_stop"},
                {"type": "message_delta", "delta": {"stop_reason": "tool_use"}, "usage": {"output_tokens": 20}},
            ])
        return _turn("Done.", {"input_tokens": 300, "output_tokens": 1, "speed": "standard"})

    monkeypatch.setattr(ai_mod, "_open_ai", fake_open)
    r = c.post("/api/ai/chat", json={"prompt": "list", "agent_scope": "folder", "folder": "",
                                     "stream": True, "speed": "fast"})
    assert r.status_code == 200, r.text
    lines = [json.loads(l) for l in r.text.splitlines() if l.strip()]
    assert [m["speed"] for m in _model_lines(r.text)] == ["fast", ""]
    usage = [l["usage"] for l in lines if "usage" in l]
    assert len(usage) == 2 and not any("speed" in u for u in usage)
    # The corrected model line comes right before the round's usage line.
    second = [i for i, l in enumerate(lines) if "model" in l][1]
    assert "usage" in lines[second + 1]


def test_a_speed_the_wire_never_asks_for_is_not_claimed(org, monkeypatch):
    """Fast mode asked of a service that speaks Anthropic's API behind
    another host is never sent (the wire has no tier there), so the reply
    reports the usual routing rather than a tier nobody was asked for."""
    c, ids = org
    import gamma.routers.ai as ai_mod

    r = c.post("/api/ai/providers", json={"protocol": "anthropic", "api_key": "sk-other-key",
                                          "base_url": "https://api.moonshot.ai/anthropic",
                                          "models": "kimi-served"})
    assert r.status_code == 200, r.text
    other = next(p for p in r.json()["providers"] if p["base_url"] == "https://api.moonshot.ai/anthropic")
    try:
        monkeypatch.setattr(ai_mod, "_open_ai",
                            lambda *a, **kw: _turn("Hello.", {"input_tokens": 5, "output_tokens": 1}))
        r = c.post("/api/ai/chat", json={"prompt": "hi", "stream": True, "speed": "fast", "model": "kimi-served"})
        assert r.status_code == 200, r.text
        assert [(m["name"], m["speed"]) for m in _model_lines(r.text)] == [("kimi-served", "")]
    finally:
        assert c.delete(f"/api/ai/providers/{other['id']}").status_code == 200
