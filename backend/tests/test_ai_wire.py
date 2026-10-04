"""Pure protocol code: tool definitions, calls and results on each provider
wire (Anthropic Messages, OpenAI Chat Completions, OpenAI Responses, the
ChatGPT backend), the SSE parsers' tool-call events, and how a saved chat's
tool history is replayed into the next request."""

import json

from gamma.ai_client import sse_events, wire_protocol
from gamma.ai_protocols import WIRES
from gamma.ai_context import TOOL_REPLAY_BUDGET, build_messages

from ai_fixtures import ALL_TOOLS, CONF, TURNS, payload, sse

anthropic_request = WIRES["anthropic"].request
openai_request = WIRES["openai"].request
openai_responses_request = WIRES["openai-responses"].request
chatgpt_request = WIRES["chatgpt"].request


def test_anthropic_wire_tools_and_results():
    req = anthropic_request(CONF, [dict(m) for m in TURNS], "sys", "m", tools=ALL_TOOLS)
    body = json.loads(req.data)
    assert body["tools"][0]["name"] == "list_pages" and "input_schema" in body["tools"][0]
    assert body["messages"][1]["content"] == [
        {"type": "text", "text": "listing"},
        {"type": "tool_use", "id": "c1", "name": "list_pages", "input": {}}]
    assert body["messages"][2] == {"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": "c1", "content": "Pages…"}]}


def test_openai_wire_tools_and_results():
    req = openai_request(CONF, [dict(m) for m in TURNS], "sys", "m", tools=ALL_TOOLS)
    body = json.loads(req.data)
    assert body["tools"][0] == {"type": "function", "function": {
        "name": "list_pages", "description": ALL_TOOLS[0]["description"],
        "parameters": ALL_TOOLS[0]["parameters"]}}
    call = body["messages"][2]["tool_calls"][0]
    assert call["function"]["name"] == "list_pages" and call["id"] == "c1"
    assert body["messages"][3] == {"role": "tool", "tool_call_id": "c1", "content": "Pages…"}


def test_anthropic_wire_maps_minimal_effort_to_low():
    msgs = [{"role": "user", "content": "hi"}]
    body = json.loads(anthropic_request(CONF, msgs, "", "m", effort="minimal").data)
    assert body["output_config"] == {"effort": "low"}
    body = json.loads(anthropic_request(CONF, msgs, "", "m", effort="high").data)
    assert body["output_config"] == {"effort": "high"}


def test_fast_mode_goes_out_as_each_wire_names_it():
    msgs = [{"role": "user", "content": "hi"}]
    # Anthropic: a top-level speed plus the beta flag the preview needs.
    req = anthropic_request({**CONF, "base_url": "https://api.anthropic.com"}, msgs, "", "m", speed="fast")
    assert json.loads(req.data)["speed"] == "fast"
    assert req.headers["Anthropic-beta"] == "fast-mode-2026-02-01"
    # OpenAI and the Codex backend: the service tier, "priority" for fast.
    for request in (openai_request, openai_responses_request, chatgpt_request):
        conf = {**CONF, "base_url": "https://api.openai.com"}
        assert json.loads(request(conf, msgs, "", "m", speed="fast").data)["service_tier"] == "priority"
        assert json.loads(request(conf, msgs, "", "m", speed="flex").data)["service_tier"] == "flex"
        assert "service_tier" not in json.loads(request(conf, msgs, "", "m").data)


def test_a_wire_without_a_tier_leaves_the_field_out():
    # Only the providers' own endpoints route by tier; a service merely
    # speaking their API gets the plain request (and Anthropic no flag).
    msgs = [{"role": "user", "content": "hi"}]
    req = anthropic_request({**CONF, "base_url": "https://api.moonshot.ai/anthropic"}, msgs, "", "m", speed="fast")
    assert "speed" not in json.loads(req.data) and "Anthropic-beta" not in req.headers
    body = json.loads(openai_request({**CONF, "base_url": "https://api.deepseek.com"},
                                     msgs, "", "m", speed="fast").data)
    assert "service_tier" not in body


def test_openai_output_cap_field_follows_the_endpoint():
    # OpenAI itself wants max_completion_tokens; compatible servers such as
    # DeepSeek only read max_tokens.
    msgs = [{"role": "user", "content": "hi"}]
    official = json.loads(openai_request(
        {**CONF, "base_url": "https://api.openai.com"}, msgs, "", "m", max_tokens=99).data)
    assert official["max_completion_tokens"] == 99 and "max_tokens" not in official
    deepseek = json.loads(openai_request(
        {**CONF, "base_url": "https://api.deepseek.com"}, msgs, "", "m", max_tokens=99).data)
    assert deepseek["max_tokens"] == 99 and "max_completion_tokens" not in deepseek


def test_chatgpt_wire_tools_and_results():
    req = chatgpt_request(CONF, [dict(m) for m in TURNS], "sys", "m", tools=ALL_TOOLS)
    body = json.loads(req.data)
    assert body["tools"][0]["type"] == "function" and body["tools"][0]["name"] == "list_pages"
    kinds = [i["type"] for i in body["input"]]
    assert kinds == ["message", "message", "function_call", "function_call_output"]
    assert body["input"][2]["call_id"] == "c1"
    assert body["input"][3] == {"type": "function_call_output", "call_id": "c1", "output": "Pages…"}


def test_openai_responses_wire_shape():
    req = openai_responses_request(CONF, [dict(m) for m in TURNS], "sys", "gpt-5.6-sol",
                                   tools=ALL_TOOLS)
    assert req.full_url == "https://example.test/v1/responses"
    assert req.headers["Authorization"] == "Bearer k"
    body = json.loads(req.data)
    assert body["instructions"] == "sys" and body["stream"] is True
    assert body["tools"][0]["type"] == "function" and body["tools"][0]["name"] == "list_pages"
    kinds = [i["type"] for i in body["input"]]
    assert kinds == ["message", "message", "function_call", "function_call_output"]


def test_anthropic_cache_breakpoints_on_anthropic_itself_only():
    """Anthropic's prompt cache is opt-in: the last tool spec, the system
    prompt and the last two user turns get breakpoints — on api.anthropic.com
    only; another host speaking the API may not take the field."""
    conf = {**CONF, "base_url": "https://api.anthropic.com"}
    turns = [{"role": "user", "content": "context + q1"}, {"role": "assistant", "content": "a1"},
             {"role": "user", "content": "q2"}, {"role": "assistant", "content": "a2"},
             {"role": "user", "content": "q3"}]
    body = json.loads(anthropic_request(conf, turns, "sys", "m", tools=ALL_TOOLS, cache_key="k").data)
    mark = {"type": "ephemeral"}
    assert body["system"] == [{"type": "text", "text": "sys", "cache_control": mark}]
    assert body["tools"][-1]["cache_control"] == mark and "cache_control" not in body["tools"][0]
    marked = [i for i, m in enumerate(body["messages"])
              if isinstance(m["content"], list) and m["content"][-1].get("cache_control")]
    assert marked == [2, 4]  # the previous user turn and the current one
    assert body["messages"][0]["content"] == "context + q1" and body["messages"][1]["content"] == "a1"
    # A tool-result turn is a user turn too: its last result block carries the mark.
    body = json.loads(anthropic_request(conf, [dict(m) for m in TURNS], "sys", "m", tools=ALL_TOOLS).data)
    assert body["messages"][2]["content"][-1]["type"] == "tool_result"
    assert body["messages"][2]["content"][-1]["cache_control"] == mark
    assert body["messages"][0]["content"][-1] == {"type": "text", "text": "tidy up", "cache_control": mark}
    # Another host: the plain shapes, no markers anywhere.
    body = json.loads(anthropic_request(CONF, turns, "sys", "m", tools=ALL_TOOLS, cache_key="k").data)
    assert body["system"] == "sys" and "cache_control" not in json.dumps(body)


def test_openai_wires_send_the_conversation_cache_key():
    """One opaque id per conversation routes every turn to the same prompt
    cache: prompt_cache_key on OpenAI's own endpoints (never on a compatible
    server, which may reject the field), and the Codex backend's session id
    — stable per conversation, a fresh uuid only without one."""
    msgs = [{"role": "user", "content": "hi"}]
    official = json.loads(openai_request({**CONF, "base_url": "https://api.openai.com"},
                                         msgs, "", "m", cache_key="k1").data)
    assert official["prompt_cache_key"] == "k1"
    assert "prompt_cache_key" not in json.loads(openai_request(CONF, msgs, "", "m", cache_key="k1").data)
    body = json.loads(openai_responses_request(CONF, msgs, "", "m", cache_key="k1").data)
    assert body["prompt_cache_key"] == "k1" and body["store"] is False
    req = chatgpt_request(CONF, msgs, "", "m", cache_key="k1")
    assert req.get_header("Session_id") == "k1" and json.loads(req.data)["prompt_cache_key"] == "k1"
    req = chatgpt_request(CONF, msgs, "", "m")
    assert len(req.get_header("Session_id")) == 36 and "prompt_cache_key" not in json.loads(req.data)


def test_wire_protocol_reroutes_official_openai_tools_only():
    def rt(base):
        return {"providers": {"p": {"protocol": "openai", "base_url": base}}}
    entry = {"provider": "p", "model": "m"}
    official = rt("https://api.openai.com")
    assert wire_protocol(official, entry, ALL_TOOLS) == "openai-responses"
    assert wire_protocol(official, entry, None) == "openai"  # plain chat: completions
    # Custom gateways may not implement /v1/responses — keep chat completions.
    assert wire_protocol(rt("http://localhost:4000"), entry, ALL_TOOLS) == "openai"
    chatgpt = {"providers": {"p": {"protocol": "chatgpt", "base_url": "https://chatgpt.com/backend-api/codex"}}}
    assert wire_protocol(chatgpt, entry, ALL_TOOLS) == "chatgpt"


def test_attachments_ride_on_last_user_turn_not_tool_result():
    req = anthropic_request(CONF, [dict(m) for m in TURNS], "sys", "m",
                            pdf_b64s=["QUJD"], tools=ALL_TOOLS)
    body = json.loads(req.data)
    assert body["messages"][0]["content"][0]["type"] == "document"
    assert body["messages"][2]["content"][0]["type"] == "tool_result"


def test_sse_events_anthropic_tool_use():
    stream = sse(
        {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "ok "}},
        {"type": "content_block_start", "content_block": {"type": "tool_use", "id": "t1", "name": "rename_page"}},
        {"type": "content_block_delta", "delta": {"type": "input_json_delta", "partial_json": '{"page_id": "p1",'}},
        {"type": "content_block_delta", "delta": {"type": "input_json_delta", "partial_json": ' "title": "T"}'}},
        {"type": "content_block_stop"},
    )
    events = list(sse_events(stream, "anthropic"))
    # Argument deltas stream as they arrive (the raw JSON so far) so the UI
    # can preview a long argument; the parsed call follows at block stop.
    assert events == [("text", "ok "),
                      ("tool_delta", {"id": "t1", "name": "rename_page", "json": '{"page_id": "p1",'}),
                      ("tool_delta", {"id": "t1", "name": "rename_page",
                                      "json": '{"page_id": "p1", "title": "T"}'}),
                      ("tool", {"id": "t1", "name": "rename_page",
                                "arguments": {"page_id": "p1", "title": "T"}})]


def test_partial_tool_args_preview():
    from gamma.ai_client import partial_json_object, partial_json_strings
    # A streaming edit_block call: the id is complete, the content half-written.
    raw = '{"block_id": "b1", "content": "## Summary\\nThe paper sho'
    assert partial_json_object(raw) == {"block_id": "b1", "content": "## Summary\nThe paper sho"}
    # A trailing lone backslash / half escape is dropped, not an error.
    assert partial_json_object('{"a": "x\\')["a"] == "x"
    assert partial_json_object('{"a": "x\\u00')["a"] == "x"
    assert partial_json_object('{"a": "x\\\\')["a"] == "x\\"
    # Non-string values are skipped, later keys still read.
    assert partial_json_object('{"n": 3, "after_id": "z", "content": "hi') == {"after_id": "z", "content": "hi"}
    assert partial_json_object("") == {}
    assert partial_json_object('{"content": "ab"}') == {"content": "ab"}
    # Translation replies: a JSON array of strings, possibly fenced.
    assert partial_json_strings('```json\n["第一段", "第二') == ["第一段", "第二"]
    assert partial_json_strings('["a", "b"]') == ["a", "b"]
    assert partial_json_strings('["a", null, "b"]') == ["a"]
    assert partial_json_strings("Sure: [") == []


def test_sse_events_openai_tool_calls_accumulate():
    stream = sse(
        {"choices": [{"delta": {"tool_calls": [
            {"index": 0, "id": "c9", "function": {"name": "move_page", "arguments": '{"page_'}}]}}]},
        {"choices": [{"delta": {"tool_calls": [
            {"index": 0, "function": {"arguments": 'id": "p2", "folder": "x"}'}}]}, "finish_reason": "tool_calls"}]},
    )
    events = list(sse_events(stream, "openai"))
    assert events == [("tool_delta", {"id": "c9", "name": "move_page", "json": '{"page_'}),
                      ("tool_delta", {"id": "c9", "name": "move_page",
                                      "json": '{"page_id": "p2", "folder": "x"}'}),
                      ("tool", {"id": "c9", "name": "move_page",
                                "arguments": {"page_id": "p2", "folder": "x"}}),
                      ("stop", "tool_calls")]  # the provider's stop reason closes every stream


def test_sse_events_responses_argument_deltas():
    stream = sse(
        {"type": "response.output_item.added", "item": {
            "type": "function_call", "id": "fc_1", "call_id": "f3", "name": "edit_block"}},
        {"type": "response.function_call_arguments.delta", "item_id": "fc_1", "delta": '{"block_id": "b",'},
        {"type": "response.function_call_arguments.delta", "item_id": "fc_1", "delta": ' "content": "x"}'},
        {"type": "response.output_item.done", "item": {
            "type": "function_call", "id": "fc_1", "call_id": "f3", "name": "edit_block",
            "arguments": '{"block_id": "b", "content": "x"}'}},
        {"type": "response.completed", "response": {"status": "completed"}},
    )
    events = list(sse_events(stream, "chatgpt"))
    assert events == [("tool_delta", {"id": "f3", "name": "edit_block", "json": '{"block_id": "b",'}),
                      ("tool_delta", {"id": "f3", "name": "edit_block",
                                      "json": '{"block_id": "b", "content": "x"}'}),
                      ("tool", {"id": "f3", "name": "edit_block",
                                "arguments": {"block_id": "b", "content": "x"}}),
                      ("stop", "completed")]


def test_sse_events_chatgpt_function_call_item():
    stream = sse(
        {"type": "response.output_text.delta", "delta": "hi"},
        {"type": "response.output_item.done", "item": {
            "type": "function_call", "call_id": "f1", "name": "list_pages", "arguments": "{}"}},
        {"type": "response.completed", "response": {"status": "completed"}},
    )
    events = list(sse_events(stream, "chatgpt"))
    assert events == [("text", "hi"), ("tool", {"id": "f1", "name": "list_pages", "arguments": {}}),
                      ("stop", "completed")]


def test_sse_events_openai_responses_dialect():
    stream = sse(
        {"type": "response.output_text.delta", "delta": "hi"},
        {"type": "response.output_item.done", "item": {
            "type": "function_call", "call_id": "f2", "name": "move_page",
            "arguments": '{"page_id": "p", "folder": "x"}'}},
        {"type": "response.completed", "response": {"status": "completed"}},
    )
    events = list(sse_events(stream, "openai-responses"))
    assert events == [("text", "hi"), ("tool", {"id": "f2", "name": "move_page",
                                                "arguments": {"page_id": "p", "folder": "x"}}),
                      ("stop", "completed")]


def test_sse_events_report_a_cut_off_reply():
    """The stop reason tells the chat a reply hit the output cap: Anthropic's
    max_tokens, Chat Completions' length, the Responses API's incomplete
    status — whose usage still counts."""
    from gamma.ai_protocols.base import truncated_stop

    stream = sse({"type": "message_start", "message": {"usage": {"input_tokens": 5}}},
                 {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "cut"}},
                 {"type": "message_delta", "delta": {"stop_reason": "max_tokens"},
                  "usage": {"output_tokens": 8}})
    events = list(sse_events(stream, "anthropic"))
    assert events[-1] == ("stop", "max_tokens") and truncated_stop("max_tokens")
    stream = sse({"type": "response.output_text.delta", "delta": "cut"},
                 {"type": "response.incomplete", "response": {
                     "status": "incomplete", "usage": {"input_tokens": 5, "output_tokens": 8}}})
    events = list(sse_events(stream, "openai-responses"))
    assert ("usage", {"input": 5, "output": 8, "cache_read": 0, "cache_write": 0}) in events
    assert events[-1] == ("stop", "incomplete") and truncated_stop("incomplete")
    assert not truncated_stop("end_turn") and not truncated_stop("tool_calls") and not truncated_stop("")


def test_build_messages_replays_tool_history():
    history = [
        {"role": "user", "text": "rename them"},
        {"role": "ai", "text": "Done.", "actions": [
            {"kind": "list", "tool": "list_pages", "args": {}, "result": "Pages (2): …"},
            {"kind": "rename", "tool": "rename_page",
             "args": {"page_id": "p1", "title": "New"}, "result": "ok — renamed"},
        ]},
    ]
    messages = build_messages(payload(history), "", with_tools=True)
    roles = [m["role"] for m in messages]
    assert roles == ["user", "assistant", "tool", "tool", "assistant", "user"]
    calls = messages[1]["tool_calls"]
    assert [c["name"] for c in calls] == ["list_pages", "rename_page"]
    assert calls[1]["arguments"] == {"page_id": "p1", "title": "New"}
    # Result turns pair with the synthesized call ids.
    # Replayed results are flagged as snapshots from an earlier turn.
    assert messages[2]["call_id"] == calls[0]["id"]
    assert messages[2]["content"].endswith("\nPages (2): …")
    assert messages[2]["content"].startswith("[result from an earlier turn")
    assert messages[4]["content"] == "Done."
    # Plain chats must not replay tool turns (providers reject them untooled).
    plain = build_messages(payload(history), "", with_tools=False)
    assert [m["role"] for m in plain] == ["user", "assistant", "user"]


def test_build_messages_replay_edge_cases():
    # Tool-only reply (no prose) still leaves its calls; chips saved before
    # tool recording existed (no "tool" field) are skipped entirely.
    history = [
        {"role": "user", "text": "go"},
        {"role": "ai", "text": "", "actions": [
            {"kind": "rename", "tool": "rename_page", "args": {"page_id": "p"}, "result": "ok"}]},
        {"role": "user", "text": "and this old one"},
        {"role": "ai", "text": "old reply", "actions": [{"kind": "list", "summary": "Listed 68"}]},
    ]
    messages = build_messages(payload(history), "", with_tools=True)
    roles = [m["role"] for m in messages]
    assert roles == ["user", "assistant", "tool", "user", "assistant", "user"]
    assert not messages[4].get("tool_calls")


def test_build_messages_replays_renamed_tools_under_current_name():
    history = [
        {"role": "user", "text": "where is X"},
        {"role": "ai", "text": "p.3", "actions": [
            {"kind": "search", "tool": "search_pdfs", "args": {"query": "x"}, "result": "hit"}]},
    ]
    messages = build_messages(payload(history), "", with_tools=True)
    assert messages[1]["tool_calls"][0]["name"] == "search_library"


def test_build_messages_elides_old_results_over_budget():
    big = "x" * (TOOL_REPLAY_BUDGET - 100)
    history = [
        {"role": "user", "text": "a"},
        {"role": "ai", "text": "one", "actions": [
            {"kind": "read", "tool": "read_page", "args": {"page_id": "p1"}, "result": big}]},
        {"role": "user", "text": "b"},
        {"role": "ai", "text": "two", "actions": [
            {"kind": "read", "tool": "read_page", "args": {"page_id": "p2"}, "result": big}]},
    ]
    messages = build_messages(payload(history), "", with_tools=True)
    tool_turns = [m for m in messages if m["role"] == "tool"]
    assert len(tool_turns) == 2
    assert "elided" in tool_turns[0]["content"]  # older result dropped…
    assert tool_turns[1]["content"].endswith(big)  # …newest kept in full (after the snapshot note)


def test_build_messages_keeps_the_document_context_stable_across_turns():
    """The document part rides on the oldest user turn (the same text every
    turn — the prefix the prompt caches key on); what belongs to this message
    alone (the text around a selection, the cursor block) goes with the
    question, after a "Context for this message" line."""
    history = [{"role": "user", "text": "q1"}, {"role": "ai", "text": "a1"}]
    messages = build_messages(payload(history), "DOC", message_context="AROUND")
    assert messages[0]["content"].startswith("Context — pages from the user's knowledge base")
    assert "DOC" in messages[0]["content"] and "AROUND" not in messages[0]["content"]
    assert messages[0]["content"].endswith("User question: q1")
    last = messages[-1]["content"]
    assert "Context for this message" in last and "AROUND" in last and "DOC" not in last
    assert last.endswith("User question: now do it")
    # Without history both parts share the one turn, the document part first.
    (only,) = build_messages(payload([]), "DOC", message_context="AROUND")
    assert only["content"].index("DOC") < only["content"].index("AROUND")
    assert only["content"].endswith("User question: now do it")
    # Nothing to point at: the question stands alone after the history.
    assert build_messages(payload(history), "")[-1]["content"] == "now do it"


def test_build_messages_leaves_out_the_oldest_turns():
    """drop_turns fits a long conversation to the window: the oldest items
    go, the document context moves to the oldest kept question, and the
    kept history never opens on a reply."""
    history = [{"role": "user", "text": "q1"}, {"role": "ai", "text": "a1"},
               {"role": "user", "text": "q2"}, {"role": "ai", "text": "a2"}]
    messages = build_messages(payload(history), "DOC", drop_turns=2)
    assert [m["content"] for m in messages[1:]] == ["a2", "now do it"]
    assert "DOC" in messages[0]["content"] and messages[0]["content"].endswith("User question: q2")
    messages = build_messages(payload(history), "", drop_turns=1)
    assert [m["content"] for m in messages] == ["q2", "a2", "now do it"]
    assert [m["content"] for m in build_messages(payload(history), "", drop_turns=9)] == ["now do it"]


def test_elide_live_results_is_a_valve_that_keeps_the_last_rounds():
    """Within one reply the rounds' results stay whole until they outgrow
    the budget; then the oldest become the replay's stub (pictures dropped),
    the last rounds untouched — and a retry after a too-long round keeps
    only the last one."""
    from gamma.ai_context import _ELIDED_RESULT, _IMAGE_CHARS, elide_live_results

    def round_(i, size):
        return [{"role": "assistant", "content": "", "tool_calls": [
                    {"id": f"c{i}", "name": "read_page", "arguments": {}}]},
                {"role": "tool", "call_id": f"c{i}", "content": "x" * size,
                 **({"images": [("image/png", "AA")]} if i == 1 else {})}]
    messages = [{"role": "user", "content": "q"}] + sum((round_(i, 1000) for i in range(4)), [])
    assert elide_live_results(messages, keep_rounds=2, budget=100_000) == 0
    assert all(m["content"] == "x" * 1000 for m in messages if m["role"] == "tool")
    # 1000 + (1000 + a picture) + 1000 + 1000 over a 2500 budget: the first
    # two rounds go (the picture with them), the last two stay.
    assert _IMAGE_CHARS > 2500
    assert elide_live_results(messages, keep_rounds=2, budget=2500) == 2
    results = [m for m in messages if m["role"] == "tool"]
    assert [r["content"] == _ELIDED_RESULT for r in results] == [True, True, False, False]
    assert "images" not in results[1]
    assert elide_live_results(messages, keep_rounds=2, budget=2500) == 0  # already there
    assert elide_live_results(messages, keep_rounds=1, budget=0) == 1
    assert results[2]["content"] == _ELIDED_RESULT and results[3]["content"] == "x" * 1000


def test_prompt_token_estimate():
    from gamma.ai_context import estimate_tokens, prompt_tokens

    assert estimate_tokens("") == 0
    assert estimate_tokens("a" * 400) == 100          # four ASCII chars per token
    assert estimate_tokens("量" * 100) == 100          # one per CJK character
    assert estimate_tokens("ab" * 200 + "量" * 10) == 110
    messages = [{"role": "user", "content": "a" * 400},
                {"role": "assistant", "content": "", "tool_calls": [{"id": "c", "name": "n", "arguments": {}}]},
                {"role": "tool", "call_id": "c", "content": "b" * 400, "images": [("image/png", "AA")]}]
    plain = prompt_tokens(messages)
    assert 100 + 100 + 1600 < plain < 100 + 100 + 1600 + 40  # + the call's JSON
    assert prompt_tokens(messages, system="s" * 400, images=[("image/png", "AA")]) == plain + 100 + 1600


def test_anthropic_folds_user_turn_after_tool_only_reply():
    messages = [
        {"role": "user", "content": "go"},
        {"role": "assistant", "content": "", "tool_calls": [
            {"id": "c1", "name": "rename_page", "arguments": {"page_id": "p"}}]},
        {"role": "tool", "call_id": "c1", "content": "ok"},
        {"role": "user", "content": "thanks, next"},
    ]
    req = anthropic_request(CONF, messages, "sys", "m", tools=ALL_TOOLS)
    wire = json.loads(req.data)["messages"]
    assert [m["role"] for m in wire] == ["user", "assistant", "user"]  # roles alternate
    assert wire[2]["content"] == [
        {"type": "tool_result", "tool_use_id": "c1", "content": "ok"},
        {"type": "text", "text": "thanks, next"}]


PICTURE_TURNS = [
    {"role": "user", "content": "what does page 3 show?"},
    {"role": "assistant", "content": "", "tool_calls": [
        {"id": "c1", "name": "view_pdf_page", "arguments": {"page_id": "p", "pdf_page": 3}},
        {"id": "c2", "name": "read_page", "arguments": {"page_id": "p"}}]},
    {"role": "tool", "call_id": "c1", "content": "PDF page 3 attached", "images": [("image/png", "QUJD")]},
    {"role": "tool", "call_id": "c2", "content": "Title…"},
]


def test_tool_result_pictures_on_each_wire():
    """A tool result's `images` reach the model on every wire: inside the
    Anthropic tool_result, and — the OpenAI wires take only text there — as
    one user turn after the round's results, in call order."""
    body = json.loads(anthropic_request(CONF, [dict(m) for m in PICTURE_TURNS], "sys", "m",
                                        tools=ALL_TOOLS).data)
    results = body["messages"][2]["content"]
    assert [r["type"] for r in results] == ["tool_result", "tool_result"]
    assert results[0]["content"] == [
        {"type": "text", "text": "PDF page 3 attached"},
        {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "QUJD"}}]
    assert results[1]["content"] == "Title…"

    body = json.loads(openai_request(CONF, [dict(m) for m in PICTURE_TURNS], "sys", "m",
                                     tools=ALL_TOOLS).data)
    roles = [m["role"] for m in body["messages"]]
    assert roles == ["system", "user", "assistant", "tool", "tool", "user"]
    assert body["messages"][3]["content"] == "PDF page 3 attached"
    picture = body["messages"][5]["content"]
    assert picture[0]["type"] == "text" and picture[1] == {
        "type": "image_url", "image_url": {"url": "data:image/png;base64,QUJD"}}

    for build in (chatgpt_request, openai_responses_request):
        body = json.loads(build(CONF, [dict(m) for m in PICTURE_TURNS], "sys", "m",
                                tools=ALL_TOOLS).data)
        kinds = [i["type"] for i in body["input"]]
        assert kinds == ["message", "function_call", "function_call", "function_call_output",
                         "function_call_output", "message"], build.__name__
        assert body["input"][3]["output"] == "PDF page 3 attached"
        assert body["input"][5]["role"] == "user"
        assert body["input"][5]["content"][1] == {
            "type": "input_image", "image_url": "data:image/png;base64,QUJD"}


def test_user_attachments_never_ride_on_a_tool_picture_turn():
    """The user's own attachments belong to their prompt, not to the picture
    turn the OpenAI wires append after a round's tool results."""
    body = json.loads(chatgpt_request(CONF, [dict(m) for m in PICTURE_TURNS], "sys", "m",
                                      pdf_b64s=["QUJD"], tools=ALL_TOOLS).data)
    assert body["input"][0]["content"][0]["type"] == "input_file"
    assert [c["type"] for c in body["input"][5]["content"]] == ["input_text", "input_image"]
    body = json.loads(openai_request(CONF, [dict(m) for m in PICTURE_TURNS], "sys", "m",
                                     pdf_b64s=["QUJD"], tools=ALL_TOOLS).data)
    assert body["messages"][1]["content"][0]["type"] == "file"
    assert [c["type"] for c in body["messages"][5]["content"]] == ["text", "image_url"]


# ------------------------------------------------------------- hosted web search

HOSTED = {"name": "web_search", "description": "", "parameters": {}}


def test_hosted_web_search_is_the_wire_tool_and_goes_out_as_is():
    responses, anthropic = WIRES["openai-responses"], WIRES["anthropic"]
    spec = responses.hosted_web_search(CONF)
    assert spec == {"type": "web_search"} and WIRES["chatgpt"].hosted_web_search(CONF) == spec
    # Anthropic's server tool on Anthropic itself only; a gateway has none.
    assert anthropic.hosted_web_search(CONF) is None
    platform = {**CONF, "base_url": "https://api.anthropic.com"}
    assert anthropic.hosted_web_search(platform)["type"] == "web_search_20250305"
    # OpenAI's platform routes a tool call to the Responses wire, which hosts it.
    openai_platform = {**CONF, "base_url": "https://api.openai.com"}
    assert WIRES["openai"].wire(openai_platform, [HOSTED]) is responses
    assert WIRES["openai"].wire(CONF, [HOSTED]).hosted_web_search(CONF) is None

    body = json.loads(openai_responses_request(CONF, [{"role": "user", "content": "q"}], "sys", "m",
                                               tools=[{**HOSTED, "hosted": spec}]).data)
    assert body["tools"] == [spec] and body["include"] == ["web_search_call.action.sources"]
    body = json.loads(chatgpt_request(CONF, [{"role": "user", "content": "q"}], "sys", "m",
                                      tools=[{**HOSTED, "hosted": spec}]).data)
    assert body["tools"] == [spec] and body["include"] == []
    server_tool = anthropic.hosted_web_search(platform)
    body = json.loads(anthropic_request(CONF, [{"role": "user", "content": "q"}], "sys", "m",
                                        tools=[*ALL_TOOLS[:1], {**HOSTED, "hosted": server_tool}]).data)
    assert body["tools"][1] == server_tool and body["tools"][0]["name"] == "list_pages"
    # A wire without hosted tools never sends one.
    body = json.loads(openai_request(CONF, [{"role": "user", "content": "q"}], "sys", "m",
                                     tools=[{**HOSTED, "hosted": spec}]).data)
    assert "tools" not in body


def test_hosted_search_streams_report_the_pages_found():
    stream = sse(
        {"type": "response.output_item.done", "item": {"type": "web_search_call", "action": {
            "type": "search", "query": "q", "sources": [{"type": "url", "url": "https://lab.example.edu/p"}]}}},
        {"type": "response.output_text.delta", "delta": "Lab page | https://lab.example.edu/p | papers"},
        {"type": "response.output_item.done", "item": {"type": "message", "content": [{
            "type": "output_text", "text": "…", "annotations": [
                {"type": "url_citation", "url": "https://lab.example.edu/p", "title": "Lab"}]}]}},
        {"type": "response.completed", "response": {"status": "completed"}},
    )
    events = list(sse_events(stream, "chatgpt"))
    assert events[0] == ("web_sources", [{"url": "https://lab.example.edu/p", "title": ""}])
    assert events[2] == ("web_sources", [{"url": "https://lab.example.edu/p", "title": "Lab"}])

    stream = sse(
        {"type": "content_block_start", "content_block": {"type": "server_tool_use", "id": "s1",
                                                          "name": "web_search"}},
        {"type": "content_block_delta", "delta": {"type": "input_json_delta", "partial_json": '{"query": "q"}'}},
        {"type": "content_block_stop"},
        {"type": "content_block_start", "content_block": {"type": "web_search_tool_result", "content": [
            {"type": "web_search_result", "url": "https://arxiv.org/abs/1", "title": "Preprint",
             "encrypted_content": "…"}]}},
        {"type": "content_block_delta", "delta": {"type": "citations_delta", "citation": {
            "type": "web_search_result_location", "url": "https://arxiv.org/abs/1", "title": "Preprint",
            "cited_text": "…"}}},
        {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "done"}},
    )
    events = list(sse_events(stream, "anthropic"))
    # The server tool's own call is not a tool call for Gamma to run.
    assert events == [("web_sources", [{"url": "https://arxiv.org/abs/1", "title": "Preprint"}]),
                      ("web_sources", [{"url": "https://arxiv.org/abs/1", "title": "Preprint"}]),
                      ("text", "done")]


def test_streams_report_the_speed_the_provider_served():
    """The tier that actually served a turn rides on its token report as
    ``speed`` (a SPEED_ORDER name, "" for the usual routing): Anthropic's
    usage.speed, Chat Completions' per-chunk service_tier, the Responses
    API's response.service_tier. A provider that names none leaves the key
    out, so the caller keeps what it asked for."""
    def reported(stream, wire):
        return [u for k, u in sse_events(stream, wire) if k == "usage"][0]

    # Anthropic: asked for fast, served standard — on either usage object.
    stream = sse({"type": "message_start", "message": {"usage": {"input_tokens": 5, "speed": "standard"}}},
                 {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "hi"}},
                 {"type": "message_delta", "delta": {"stop_reason": "end_turn"}, "usage": {"output_tokens": 2}})
    assert reported(stream, "anthropic") == {"input": 5, "output": 2, "cache_read": 0, "cache_write": 0, "speed": ""}
    stream = sse({"type": "message_start", "message": {"usage": {"input_tokens": 5}}},
                 {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "hi"}},
                 {"type": "message_delta", "delta": {"stop_reason": "end_turn"},
                  "usage": {"output_tokens": 2, "speed": "fast"}})
    assert reported(stream, "anthropic")["speed"] == "fast"
    # Chat Completions: every chunk names the tier; "priority" is fast mode, "default" the usual route.
    stream = sse({"choices": [{"delta": {"content": "hi"}}], "service_tier": "priority"},
                 {"choices": [], "usage": {"prompt_tokens": 3, "completion_tokens": 1}, "service_tier": "priority"})
    assert reported(stream, "openai")["speed"] == "fast"
    stream = sse({"choices": [{"delta": {"content": "hi"}}], "service_tier": "default"},
                 {"choices": [], "usage": {"prompt_tokens": 3, "completion_tokens": 1}, "service_tier": "default"})
    assert reported(stream, "openai")["speed"] == ""
    # Responses API (OpenAI's and the Codex backend): the finished response says.
    for wire in ("openai-responses", "chatgpt"):
        stream = sse({"type": "response.output_text.delta", "delta": "hi"},
                     {"type": "response.completed", "response": {
                         "status": "completed", "service_tier": "flex",
                         "usage": {"input_tokens": 3, "output_tokens": 2}}})
        assert reported(stream, wire)["speed"] == "flex"
    # Nothing said: no key — not even "" — on any wire.
    stream = sse({"type": "message_start", "message": {"usage": {"input_tokens": 5}}},
                 {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "hi"}},
                 {"type": "message_delta", "delta": {"stop_reason": "end_turn"}, "usage": {"output_tokens": 2}})
    assert "speed" not in reported(stream, "anthropic")
    stream = sse({"choices": [{"delta": {"content": "hi"}}]},
                 {"choices": [], "usage": {"prompt_tokens": 3, "completion_tokens": 1}})
    assert "speed" not in reported(stream, "openai")
    stream = sse({"type": "response.output_text.delta", "delta": "hi"},
                 {"type": "response.completed", "response": {
                     "status": "completed", "usage": {"input_tokens": 3, "output_tokens": 2}}})
    assert "speed" not in reported(stream, "openai-responses")


def test_whole_replies_report_the_speed_the_provider_served():
    """The non-streamed body too: Anthropic's usage.speed, Chat Completions'
    top-level service_tier (a compatible server's body, without it, names
    no speed). A tier Gamma has no name for counts as the usual routing."""
    from gamma.ai_protocols.base import served_speed_name

    class Body:
        def __init__(self, data):
            self._data = json.dumps(data).encode()

        def read(self):
            return self._data

    heard = []
    text = WIRES["anthropic"].read_reply(Body({
        "content": [{"type": "text", "text": "hi"}], "stop_reason": "end_turn",
        "usage": {"input_tokens": 4, "output_tokens": 1, "speed": "fast"}}), heard.append)
    assert text == "hi" and heard == [{"input": 4, "output": 1, "cache_read": 0, "cache_write": 0, "speed": "fast"}]
    heard = []
    WIRES["openai"].read_reply(Body({
        "choices": [{"message": {"content": "hi"}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 4, "completion_tokens": 1}, "service_tier": "default"}), heard.append)
    assert heard[0]["speed"] == ""
    heard = []
    WIRES["openai"].read_reply(Body({
        "choices": [{"message": {"content": "hi"}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 4, "completion_tokens": 1}}), heard.append)
    assert "speed" not in heard[0]
    # The mapping itself: Gamma's names, the providers' spellings, the rest.
    assert served_speed_name("fast") == "fast" and served_speed_name("priority") == "fast"
    assert served_speed_name("flex") == "flex" and served_speed_name("Priority ") == "fast"
    assert served_speed_name("standard") == "" and served_speed_name("default") == ""
    assert served_speed_name("scale") == ""
    assert served_speed_name("") is None and served_speed_name(None) is None and served_speed_name(3) is None


# --- other OpenAI-compatible services --------------------------------------------

def test_openai_paths_follow_a_versioned_base_url():
    """A base URL that already ends in a version keeps it: a pasted /v1 stays
    one, and GLM's /api/paas/v4 is reachable as documented. The Anthropic
    wire keeps its own fixed path."""
    msgs = [{"role": "user", "content": "hi"}]
    url = lambda base: openai_request({**CONF, "base_url": base}, msgs, "", "m").full_url  # noqa: E731
    assert url("https://api.openai.com") == "https://api.openai.com/v1/chat/completions"
    assert url("https://api.moonshot.ai/v1") == "https://api.moonshot.ai/v1/chat/completions"
    assert url("https://api.z.ai/api/paas/v4") == "https://api.z.ai/api/paas/v4/chat/completions"
    listing = WIRES["openai"].models_request({**CONF, "base_url": "https://api.z.ai/api/paas/v4"})
    assert listing.full_url == "https://api.z.ai/api/paas/v4/models"
    assert anthropic_request(CONF, msgs, "", "m").full_url == "https://example.test/v1/messages"


def test_openai_stream_reports_the_thinking_ahead_of_the_calls():
    stream = sse(
        {"choices": [{"delta": {"reasoning_content": "Look "}}]},
        {"choices": [{"delta": {"reasoning_content": "it up."}}]},
        {"choices": [{"delta": {"tool_calls": [
            {"index": 0, "id": "c1", "function": {"name": "list_pages", "arguments": "{}"}}]},
            "finish_reason": "tool_calls"}]},
    )
    events = [(kind, data) for kind, data in sse_events(stream, "openai") if kind != "tool_delta"]
    assert events == [("reasoning", {"reasoning_content": "Look it up."}),
                      ("tool", {"id": "c1", "name": "list_pages", "arguments": {}}),
                      ("stop", "tool_calls")]


THOUGHT_TURNS = [
    {"role": "user", "content": "tidy up"},
    {"role": "assistant", "content": "", "tool_calls": [{"id": "c1", "name": "list_pages", "arguments": {}}],
     "reasoning": {"reasoning_content": "List first."}},
    {"role": "tool", "call_id": "c1", "content": "Pages…"},
    {"role": "assistant", "content": "Done."},
    {"role": "user", "content": "and now?"},
]


def test_thinking_goes_back_to_a_compatible_server_only():
    body = json.loads(openai_request({**CONF, "base_url": "https://api.deepseek.com"},
                                     THOUGHT_TURNS, "", "m").data)
    called, answered = [m for m in body["messages"] if m["role"] == "assistant"]
    assert called["reasoning_content"] == "List first."
    # Every assistant turn carries the field, empty where none was kept.
    assert answered == {"role": "assistant", "content": "Done.", "reasoning_content": ""}
    # OpenAI itself and the other wires send exactly what they sent before.
    plain = [{k: v for k, v in m.items() if k != "reasoning"} for m in THOUGHT_TURNS]
    official = {**CONF, "base_url": "https://api.openai.com"}
    assert openai_request(official, THOUGHT_TURNS, "", "m").data == openai_request(official, plain, "", "m").data
    for request in (anthropic_request, openai_responses_request, chatgpt_request):
        assert json.loads(request(CONF, THOUGHT_TURNS, "", "m").data) == json.loads(request(CONF, plain, "", "m").data)


def test_openrouter_thinking_details_are_kept_whole_and_sent_back():
    stream = sse(
        {"choices": [{"delta": {"reasoning": "Hm", "reasoning_details": [
            {"type": "reasoning.text", "index": 0, "text": "Hm"}]}}]},
        {"choices": [{"delta": {"reasoning": "m.", "reasoning_details": [
            {"type": "reasoning.text", "index": 0, "text": "m.", "signature": "sig"}]}}]},
        {"choices": [{"delta": {"content": "Hi"}, "finish_reason": "stop"}]},
    )
    (kept,) = [data for kind, data in sse_events(stream, "openai") if kind == "reasoning"]
    assert kept == {"reasoning": "Hmm.", "reasoning_details": [
        {"type": "reasoning.text", "index": 0, "text": "Hmm.", "signature": "sig"}]}
    turns = [{**m, "reasoning": kept} if m.get("tool_calls") else m for m in THOUGHT_TURNS]
    body = json.loads(openai_request({**CONF, "base_url": "https://openrouter.ai/api/v1"}, turns, "", "m").data)
    called, answered = [m for m in body["messages"] if m["role"] == "assistant"]
    assert called["reasoning"] == "Hmm." and called["reasoning_details"] == kept["reasoning_details"]
    assert answered["reasoning"] == "" and "reasoning_details" not in answered


def test_openrouter_listing_names_windows_efforts_and_pictures():
    data = {"data": [
        {"id": "deepseek/deepseek-v4-pro", "context_length": 1_048_576,
         "supported_parameters": ["reasoning", "tools"], "reasoning": {"supported_efforts": ["xhigh", "high"]},
         "architecture": {"input_modalities": ["text"]}},
        {"id": "meta/plain", "context_length": 8192, "supported_parameters": ["tools"],
         "architecture": {"input_modalities": ["text", "image"]}},
    ]}
    rows = {m["id"]: m for m in WIRES["openai"].models(data, {**CONF, "base_url": "https://openrouter.ai/api/v1"})}
    assert rows["deepseek/deepseek-v4-pro"] == {"id": "deepseek/deepseek-v4-pro", "context_window": 1_048_576,
                                                "efforts": ["xhigh", "high"], "speeds": None, "images": False}
    assert rows["meta/plain"]["efforts"] == [] and rows["meta/plain"]["images"] is True


def test_a_named_service_that_takes_the_cache_key_gets_it():
    msgs = [{"role": "user", "content": "hi"}]
    body = lambda base: json.loads(openai_request(  # noqa: E731
        {**CONF, "protocol": "openai", "base_url": base}, msgs, "", "m", cache_key="k1").data)
    assert body("https://api.moonshot.ai")["prompt_cache_key"] == "k1"
    assert "prompt_cache_key" not in body("https://api.deepseek.com")


def test_build_messages_sends_a_replys_thinking_back():
    """A saved reply's thinking rides on its first replayed assistant turn:
    the tool calls it led to, else the reply itself. Anything but text is
    dropped."""
    from gamma.ai_context import prompt_tokens

    history = [{"role": "user", "text": "tidy"},
               {"role": "ai", "text": "Done.", "reasoning": {"reasoning_content": "List first.", "junk": 3},
                "actions": [{"tool": "list_pages", "args": {}, "result": "Pages…"}]},
               {"role": "user", "text": "thanks"},
               {"role": "ai", "text": "Welcome.", "reasoning": {"reasoning_content": "Be brief."}},
               {"role": "user", "text": "again"},
               {"role": "ai", "text": "Sure.", "reasoning": "not a mapping"}]
    messages = build_messages(payload(history), "", with_tools=True)
    called = next(m for m in messages if m.get("tool_calls"))
    assert called["reasoning"] == {"reasoning_content": "List first."}
    replies = {m["content"]: m.get("reasoning") for m in messages
               if m["role"] == "assistant" and not m.get("tool_calls")}
    assert replies == {"Done.": None, "Welcome.": {"reasoning_content": "Be brief."}, "Sure.": None}
    # The thinking counts toward the prompt's size.
    assert prompt_tokens([{"role": "assistant", "content": "", "reasoning": {"reasoning_content": "x" * 400}}]) == 100
