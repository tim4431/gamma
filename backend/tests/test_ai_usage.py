"""Token usage and cost: the wire parser's ("usage", …) event per protocol,
the chat stream's {"usage"} line (one per agent round) with its billing and
cost, the price of a model (exact matches only) and what a call cost, the
per-account ai_usage rows behind GET /api/ai/usage, and the reset."""

import json
from pathlib import Path

import pytest

from ai_fixtures import CONF, FakeResp, ai_provider, org, sse  # noqa: F401  (fixtures)
from gamma.ai_client import add_usage, normalize_usage, sse_events
from gamma.ai_protocols import WIRES

openai_request = WIRES["openai"].request
SUMS = json.loads((Path(__file__).resolve().parents[2] / "tests" / "shared" / "ai_models.json")
                  .read_text(encoding="utf-8"))["sums"]["cases"]


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


COUNTS = ("calls", "input", "output", "cache_read", "cache_write")


def counts(obj):
    """The token counts of a counts object, without its cost and splits."""
    return {k: obj[k] for k in COUNTS}


def _turn(text, usage):
    return FakeResp([
        {"type": "message_start", "message": {"usage": usage}},
        {"type": "content_block_delta", "delta": {"type": "text_delta", "text": text}},
        {"type": "message_delta", "delta": {"stop_reason": "end_turn"}, "usage": {"output_tokens": 4}},
    ])


def test_chat_stream_emits_usage_and_records_it(org, monkeypatch):
    c, ids = org
    import gamma.routers.ai as ai_mod
    from gamma import ai_catalog

    assert c.delete("/api/ai/usage").status_code == 200
    # $3 / $15 per million tokens: 50 in and 4 out cost $0.00021.
    monkeypatch.setattr(ai_catalog, "price", lambda conf, model, provider_id="", wait=0:
                        {"input": 3, "output": 15} if model == "claude-solo" else None)
    monkeypatch.setattr(ai_mod, "_open_ai",
                        lambda *a, **kw: _turn("Hello.", {"input_tokens": 50, "output_tokens": 1}))
    r = c.post("/api/ai/chat", json={"prompt": "hi", "stream": True})
    assert r.status_code == 200, r.text
    lines = [json.loads(l) for l in r.text.splitlines() if l.strip()]
    assert "".join(l.get("delta", "") for l in lines) == "Hello."
    # The usage line is the last one, with how the call was paid for and its cost.
    assert lines[-1] == {"usage": {"input": 50, "output": 4, "cache_read": 0, "cache_write": 0,
                                   "billing": "api", "cost": 0.00021}}

    s = c.get("/api/ai/usage").json()
    today = s["windows"]["today"]
    assert counts(today) == {"calls": 1, "input": 50, "output": 4, "cache_read": 0, "cache_write": 0}
    assert (today["cost"], today["priced_calls"], today["unpriced_calls"]) == (0.00021, 1, 0)
    assert today["billing"]["api"]["cost"] == 0.00021
    assert s["windows"]["all"]["calls"] == 1 and s["kinds"] == {"chat": today}
    assert s["models"][0]["model"] == "claude-solo" and s["models"][0]["input"] == 50
    assert s["models"][0]["billing"] == "api"

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
    assert r.json()["usage"]["billing"] == "api" and r.json()["usage"]["cost"] == 0.000036
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
    # No source prices claude-solo: each api call's cost is unknown, never $0.
    assert [(u["billing"], u["cost"]) for u in usage] == [("api", None), ("api", None)]
    today = c.get("/api/ai/usage").json()["windows"]["today"]
    assert counts(today) == {"calls": 2, "input": 400, "output": 24, "cache_read": 0, "cache_write": 0}
    assert (today["cost"], today["priced_calls"], today["unpriced_calls"]) == (0.0, 0, 2)

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
    # A day is a counts object like every other, split by billing.
    assert daily[-2] == {"date": "2024-02-29", "calls": 2, "input": 50, "output": 4, "cache_read": 6, "cache_write": 2,
                         "cost": 0.0, "priced_calls": 0, "unpriced_calls": 0,
                         "billing": {"": {"calls": 2, "input": 50, "output": 4, "cache_read": 6, "cache_write": 2,
                                          "cost": 0.0}}}
    assert {k: daily[-1][k] for k in COUNTS} == counts(data["windows"]["today"])
    assert daily[1] == {"date": "2023-03-04", "calls": 0, "input": 0, "output": 0, "cache_read": 0, "cache_write": 0,
                        "cost": 0.0, "priced_calls": 0, "unpriced_calls": 0, "billing": {}}
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
        "usage": {"input": 50, "output": 4, "cache_read": 0, "cache_write": 0, "billing": "api", "cost": None}}
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


# --- prices and cost ---------------------------------------------------------

def test_price_from_an_openrouter_listing(monkeypatch):
    """OpenRouter's listing prices each model per token (strings): the
    price is that ×1e6, its cache prices and its long-prompt tier with it;
    a model the listing doesn't price is looked up nowhere else that isn't
    named exactly."""
    from gamma import ai_catalog

    monkeypatch.setattr(ai_catalog, "_listings", {})
    monkeypatch.setattr(ai_catalog, "_models_dev", {"index": None, "prices": None, "until": 0.0})
    monkeypatch.setattr(ai_catalog, "fetch_json", lambda req: {"data": [
        {"id": "anthropic/claude-x", "pricing": {"prompt": "0.000003", "completion": "0.000015",
                                                 "input_cache_read": "0.0000003", "input_cache_write": "0.00000375",
                                                 "overrides": [{"min_prompt_tokens": 200001, "prompt": "0.000006",
                                                                "completion": "0.0000225"}]}},
        {"id": "router/auto", "pricing": {"prompt": "-1", "completion": "-1"}}]})
    monkeypatch.setattr(ai_catalog, "urlopen", lambda *a, **kw: pytest.fail("models.dev is not asked"))
    conf = {"protocol": "openai", "api_key": "k", "base_url": "https://openrouter.ai/api/v1", "name": "OpenRouter"}
    assert ai_catalog.price(conf, "anthropic/claude-x", "or", wait=None) == {
        "input": 3.0, "output": 15.0, "cache_read": 0.3, "cache_write": 3.75,
        "tiers": [{"size": 200000, "input": 6.0, "output": 22.5}]}
    assert ai_catalog.price(conf, "router/auto", "or", wait=None) is None  # a price that varies is none


def test_price_from_an_opencode_listing(monkeypatch):
    """OpenCode's listing prices a model per million tokens (``cost.text``):
    its context overrides are tiers and its service-tier multipliers
    speeds; a multiplier or an override on anything else (a time of day, a
    promotion's dates) makes the price unknown, and models.dev is not
    asked for it either."""
    from gamma import ai_catalog

    text = {"input": {"price": 1, "cache": {"read": 0.1, "write": 1.25},
                      "overrides": [{"when": {"context": {"gt": 200000}}, "price": 2, "cache": {"read": 0.2}}]},
            "output": {"price": 5, "overrides": [{"when": {"context": {"gt": 200000}}, "price": 7.5}]}}
    tiered = {"text": text, "multipliers": [{"when": {"service_tier": {"equals": "priority"}}, "factor": 2},
                                            {"when": {"service_tier": {"equals": "flex"}}, "factor": {"text": 0.5}}]}
    at_night = {"text": text, "multipliers": [{"when": {"time": {"between": ["00:00", "08:00"]}}, "factor": 0.5}]}
    promotion = {"text": {**text, "output": {"price": 5, "overrides": [
        {"when": {"context": {"gt": 200000}, "date": {"before": "2026-12-01"}}, "price": 0}]}}}
    monkeypatch.setattr(ai_catalog, "_listings", {})
    monkeypatch.setattr(ai_catalog, "_models_dev", {"index": None, "prices": None, "until": 0.0})
    monkeypatch.setattr(ai_catalog, "fetch_json", lambda req: {"data": [
        {"id": "m-tiered", "cost": tiered}, {"id": "m-night", "cost": at_night}, {"id": "m-promo", "cost": promotion}]})
    monkeypatch.setattr(ai_catalog, "urlopen", lambda *a, **kw: pytest.fail("models.dev is not asked"))
    conf = {"protocol": "openai", "api_key": "k", "base_url": "https://opencode.ai/inference/openai/v1",
            "name": "OpenCode Zen"}
    assert ai_catalog.price(conf, "m-tiered", "oc", wait=None) == {
        "input": 1.0, "output": 5.0, "cache_read": 0.1, "cache_write": 1.25,
        "tiers": [{"size": 200000, "input": 2.0, "output": 7.5, "cache_read": 0.2, "cache_write": 1.25}],
        "speeds": {"fast": 2.0, "flex": 0.5}}
    assert ai_catalog.price(conf, "m-night", "oc", wait=None) is None
    assert ai_catalog.price(conf, "m-promo", "oc", wait=None) is None


def _models_dev(monkeypatch, catalog):
    """models.dev answering ``catalog``, the listings answering nothing."""
    from gamma import ai_catalog

    class Body:
        def read(self):
            return json.dumps(catalog).encode()

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(ai_catalog, "_listings", {})
    monkeypatch.setattr(ai_catalog, "_models_dev", {"index": None, "prices": None, "until": 0.0})
    monkeypatch.setattr(ai_catalog, "fetch_json", lambda req: {"data": []})
    monkeypatch.setattr(ai_catalog, "urlopen", lambda req, timeout=0: Body())


def test_price_from_models_dev_under_the_connection_s_own_key(monkeypatch):
    """models.dev prices a model under the provider key the connection
    names (its host, or its service's catalog) — the exact model id, never
    another provider's price nor a loose alias; a custom gateway that names
    no key has none."""
    from gamma import ai_catalog

    gemini_pro = {"input": 1.25, "output": 10, "cache_read": 0.125,
                  "tiers": [{"input": 2.5, "output": 15, "cache_read": 0.25,
                             "tier": {"type": "context", "size": 200000}}]}
    _models_dev(monkeypatch, {
        "google": {"models": {"gemini-pro": {"limit": {"context": 1_000_000}, "cost": gemini_pro}}},
        "anthropic": {"models": {"claude-y": {"limit": {"context": 200_000},
                                              "cost": {"input": 3, "output": 15, "cache_read": 0.3,
                                                       "cache_write": 3.75}}}},
        "gateway": {"models": {"vendor/claude-y": {"limit": {"context": 200_000},
                                                   "cost": {"input": 1, "output": 1}}}},
    })
    gemini = {"protocol": "openai", "api_key": "k", "name": "Gemini",
              "base_url": "https://generativelanguage.googleapis.com/v1beta/openai"}
    anthropic = {"protocol": "anthropic", "api_key": "k", "name": "A", "base_url": "https://api.anthropic.com"}
    custom = {"protocol": "openai", "api_key": "k", "name": "Mine", "base_url": "https://llm.example.org/v1"}
    assert ai_catalog.price(gemini, "gemini-pro", "g", wait=None) == {
        "input": 1.25, "output": 10.0, "cache_read": 0.125,
        "tiers": [{"size": 200000, "input": 2.5, "output": 15.0, "cache_read": 0.25}]}
    assert ai_catalog.price(anthropic, "claude-y", "a", wait=None)["cache_write"] == 3.75
    # The gateway's alias of claude-y is not the connection's model.
    assert ai_catalog.price(anthropic, "vendor/claude-y", "a", wait=None) is None
    # A custom gateway names no provider: unknown, though the model is known.
    assert ai_catalog.price(custom, "claude-y", "c", wait=None) is None
    assert ai_catalog.context_window("c", custom, "claude-y")[0] == 200_000  # facts may agree, prices never


def test_cost_counts_cache_buckets_a_context_tier_and_speed():
    """What one call cost: the uncached input at the input price, the cache
    reads and writes at theirs (the input price where none is named), the
    output at its own — at the tariff of the tier the whole prompt reaches;
    a served speed with no tariff is unknown."""
    from gamma.ai_usage import cost_of

    price = {"input": 2, "output": 10, "cache_read": 0.2,
             "tiers": [{"size": 200_000, "input": 4, "output": 15, "cache_read": 0.4}]}
    small = {"input": 10_000, "output": 1_000, "cache_read": 8_000, "cache_write": 1_000}
    # 1,000 uncached × 2 + 8,000 × 0.2 + 1,000 written at the input price × 2 + 1,000 out × 10, per million
    assert cost_of(small, price) == pytest.approx((1_000 * 2 + 8_000 * 0.2 + 1_000 * 2 + 1_000 * 10) / 1e6)
    big = {"input": 250_000, "output": 1_000, "cache_read": 200_000, "cache_write": 0}
    assert cost_of(big, price) == pytest.approx((50_000 * 4 + 200_000 * 0.4 + 1_000 * 15) / 1e6)
    assert cost_of(small, price, "fast") is None
    assert cost_of(small, {**price, "speeds": {"fast": 2.0}}, "fast") == pytest.approx(2 * cost_of(small, price))
    assert cost_of(small, None) is None


def test_cost_only_for_calls_paid_per_token(monkeypatch):
    """A subscription, a shared entry or a local server spends no money per
    token: their calls carry a billing and no cost, even for a priced model."""
    from gamma import ai_catalog, ai_usage

    monkeypatch.setattr(ai_catalog, "price", lambda *a, **kw: {"input": 1, "output": 1})
    recorded = []
    monkeypatch.setattr(ai_usage, "record", lambda *a, **kw: recorded.append((a[6], a[7])))
    for billing, cost in (("api", 0.000002), ("subscription", None), ("shared", None), ("local", None)):
        rt = {"user": "u", "providers": {"p": {"name": "P", "billing": billing, "protocol": "openai"}}}
        usage = {"input": 1, "output": 1}
        ai_usage.recorder("chat", {"provider": "p", "model": "m"}, rt)(usage)
        assert (usage["billing"], usage["cost"]) == (billing, cost)
        assert recorded[-1] == (billing, cost)
    # Asked fast, the report naming the tier that served it: that tier's tariff (none).
    rt = {"user": "u", "providers": {"p": {"name": "P", "billing": "api", "protocol": "openai"}}}
    usage = {"input": 1, "output": 1, "speed": "fast"}
    ai_usage.recorder("chat", {"provider": "p", "model": "m"}, rt, "")(usage)
    assert usage["cost"] is None
    usage = {"input": 1, "output": 1, "speed": ""}  # asked fast, served at the usual tier
    ai_usage.recorder("chat", {"provider": "p", "model": "m"}, rt, "fast")(usage)
    assert usage["cost"] == 0.000002


@pytest.mark.parametrize("case", SUMS, ids=[c["note"] for c in SUMS])
def test_usage_sums_as_the_shared_cases_say(case):
    """tests/shared/ai_models.json ``sums``: a reply's rounds, a helper's or
    a web search's meter (ai_client.meter_usage), summed as the client
    sums its usage lines."""
    total = None
    for call in case["calls"]:
        total = add_usage(total, dict(call))
    assert total == case["sum"]


def test_summary_by_period_and_kind_split_by_billing(org, monkeypatch):
    """GET /api/ai/usage filters by period on the server: the totals and
    kinds every kind, the models only of ``kind``, whose total is its row
    of kinds; the windows and the calendar never. Each counts object splits
    by billing, the known cost summed and the api calls without a price
    counted apart."""
    from datetime import datetime, timedelta, timezone
    from gamma.db import connect_users_db

    c, ids = org
    c.delete("/api/ai/usage")
    now = datetime.now(timezone.utc)

    def ago(days):
        return (now - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%S")
    with connect_users_db() as conn:
        conn.executemany(
            "INSERT INTO ai_usage (user_id, at, kind, provider_id, provider_name, model, input, output, "
            "billing, cost) VALUES (?, ?, ?, 'p', 'P', ?, ?, 10, ?, ?)", [
                (ids["user_id"], ago(1), "chat", "m-api", 100, "api", 0.25),
                (ids["user_id"], ago(2), "translate", "m-api", 200, "api", None),
                (ids["user_id"], ago(3), "chat", "m-sub", 300, "subscription", None),
                (ids["user_id"], ago(20), "chat", "m-api", 400, "api", 1.0),
                (ids["user_id"], ago(100), "metadata", "m-old", 500, "", None)])
    week = c.get("/api/ai/usage", params={"period": "week"}).json()
    assert week["period"] == "week" and counts(week["totals"])["calls"] == 3
    assert (week["totals"]["cost"], week["totals"]["priced_calls"], week["totals"]["unpriced_calls"]) == (0.25, 1, 1)
    assert week["totals"]["billing"]["subscription"]["input"] == 300
    assert week["totals"]["billing"]["api"] == {"calls": 2, "input": 300, "output": 20, "cache_read": 0,
                                                "cache_write": 0, "cost": 0.25}
    assert set(week["kinds"]) == {"chat", "translate"}
    chat = c.get("/api/ai/usage", params={"period": "month", "kind": "chat"}).json()
    month = c.get("/api/ai/usage", params={"period": "month"}).json()
    assert chat["totals"] == month["totals"] and counts(chat["totals"])["calls"] == 4  # every kind
    assert set(chat["kinds"]) == {"chat", "translate"}  # the period's breakdown, whatever the kind
    assert counts(chat["kinds"]["chat"])["calls"] == 3 and chat["kinds"]["chat"]["cost"] == 1.25
    assert {(m["model"], m["billing"]) for m in chat["models"]} == {("m-api", "api"), ("m-sub", "subscription")}
    everything = c.get("/api/ai/usage", params={"period": "all"}).json()
    assert counts(everything["totals"])["calls"] == 5 and everything["totals"]["billing"][""]["calls"] == 1
    assert everything["windows"] == chat["windows"] and everything["windows"]["all"]["calls"] == 5
    day = next(d for d in everything["daily"] if d["date"] == ago(1)[:10])
    assert day["billing"]["api"]["cost"] == pytest.approx(0.25) and day["priced_calls"] == 1
    assert c.get("/api/ai/usage", params={"period": "decade"}).status_code == 422
    assert c.get("/api/ai/usage", params={"kind": "nope"}).status_code == 400
    c.delete("/api/ai/usage")


# --- quota: OpenCode Go ------------------------------------------------------

# GET https://opencode.ai/inference/go/v1/usage, as OpenCode's docs record it.
GO_USAGE = {"usage": {"rolling": {"status": "ok", "percent": 30, "resetsAt": "2026-11-21T15:00:00.000Z"},
                      "weekly": {"status": "rate-limited", "percent": 100, "resetsAt": "2026-11-23T00:00:00.000Z"},
                      "monthly": {"status": "ok", "percent": 50, "resetsAt": "2026-12-01T00:00:00.000Z"}}}


def test_opencode_go_quota_reads_as_windows(org, monkeypatch):
    """An OpenCode Go key's limits come back as the ChatGPT reader's
    windows; a key Go doesn't cover (403) says so; a pay-per-token key has
    no quota to read."""
    import io
    import urllib.error
    from gamma import ai_catalog

    c, ids = org
    r = c.post("/api/ai/providers", json={"protocol": "openai", "api_key": "sk-go-test-1234",
                                          "base_url": "https://opencode.ai/inference/go/openai/v1",
                                          "models": "kimi-k3"})
    go = next(p for p in r.json()["providers"] if p["base_url"].endswith("/go/openai/v1"))
    assert go["billing"] == "subscription" and go["label"] == "OpenCode Go" and go["quota"] is True
    asked = []
    monkeypatch.setattr(ai_catalog, "fetch_json", lambda req: asked.append(req) or GO_USAGE)
    body = c.post(f"/api/ai/providers/{go['id']}/usage").json()
    assert asked[0].full_url == "https://opencode.ai/inference/go/v1/usage"
    assert asked[0].get_header("Authorization") == "Bearer sk-go-test-1234"
    assert body["available"] is True
    assert [(w["name"], w["used_percent"], w["remaining_percent"]) for w in body["windows"]] == [
        ("5-hour", 30.0, 70.0), ("Weekly", 100.0, 0.0), ("Monthly", 50.0, 50.0)]
    assert body["windows"][0]["reset_at"] == 1795273200  # 2026-11-21T15:00:00Z

    def refused(req):
        raise urllib.error.HTTPError(req.full_url, 403, "Forbidden", {}, io.BytesIO(
            b'{"type":"error","error":{"type":"EntitlementError","message":"OpenCode Go subscription required."}}'))
    monkeypatch.setattr(ai_catalog, "fetch_json", refused)
    body = c.post(f"/api/ai/providers/{go['id']}/usage").json()
    assert body["available"] is False and body["auth"] is False and "doesn't cover OpenCode Go" in body["reason"]
    # The login check asks the same endpoint, so a key Go doesn't cover shows at login.
    health = c.post("/api/ai/health", json={"provider_id": go["id"]}).json()
    assert health["ok"] is False and "doesn't cover OpenCode Go" in health["error"]
    c.delete(f"/api/ai/providers/{go['id']}")
