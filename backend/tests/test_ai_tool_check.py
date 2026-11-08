"""Whether a model calls tools on its connection (routers/ai.py
_check_tools and _checked_tools, docs/dev/ai.md "Models that can't call
tools"): one streamed check turn over the chat's wire before a model's first
chat with tools, its verdict stored on the provider entry (ai_settings
tool_checks), a model that fails answering as a plain chat, no verdict
leaving the tools on for a while, an integration token applying a stored
verdict only, and a stale pass forgotten once the provider refuses a chat
with tools. conftest stubs the gate for every other test; this file puts
the real one back."""

import json
import threading
import time
import urllib.error
from types import SimpleNamespace

import pytest

import gamma.routers.ai as ai
from gamma import ai_settings
from gamma.ai_client import AllowanceExhausted, UpstreamError

from ai_fixtures import ALLOW_ALL, FakeResp, ai_provider, org  # noqa: F401  (fixtures)
from conftest import drop_user, fresh_client, login, make_user

REAL_GATE = ai._checked_tools
MODEL = "claude-solo"  # ai_provider's one model


@pytest.fixture(scope="module", autouse=True)
def _provider(ai_provider):
    """Every chat here needs a provider entry on the module's account."""


@pytest.fixture(autouse=True)
def _real_gate(monkeypatch, org):
    """The real gate, no lock or retry window left from another test, and no
    verdict stored on the account's entries."""
    _, ids = org
    monkeypatch.setattr(ai, "_checked_tools", REAL_GATE)
    ai._tool_check_locks.clear()
    ai._tool_check_retry.clear()
    ai_settings.update_provider_entries(ids["user_id"], lambda entries: [e.pop("tool_checks", None) for e in entries])
    monkeypatch.setattr("gamma.ai_catalog.context_window", lambda *args: (0, ""))
    monkeypatch.setattr("gamma.ai_catalog.image_input", lambda *args: (None, ""))


# --- the provider's answers ---------------------------------------------------

def anthropic_tool(name="report_ok"):
    return [{"type": "message_start", "message": {"usage": {"input_tokens": 12}}},
            {"type": "content_block_start", "content_block": {"type": "tool_use", "id": "c1", "name": name}},
            {"type": "content_block_delta", "delta": {"type": "input_json_delta", "partial_json": '{"status": "ok"}'}},
            {"type": "content_block_stop"},
            {"type": "message_delta", "delta": {"stop_reason": "tool_use"}, "usage": {"output_tokens": 7}}]


def anthropic_text(text, stop="end_turn"):
    return [{"type": "message_start", "message": {"usage": {"input_tokens": 12}}},
            {"type": "content_block_delta", "delta": {"type": "text_delta", "text": text}},
            {"type": "message_delta", "delta": {"stop_reason": stop}, "usage": {"output_tokens": 7}}]


def completions_tool():
    return [{"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "c1", "function": {
                "name": "report_ok", "arguments": '{"status": "ok"}'}}]}, "finish_reason": "tool_calls"}]},
            {"choices": [], "usage": {"prompt_tokens": 12, "completion_tokens": 7}}]


def responses_tool():
    return [{"type": "response.output_item.done", "item": {
                "type": "function_call", "call_id": "c1", "name": "report_ok", "arguments": '{"status": "ok"}'}},
            {"type": "response.completed", "response": {"status": "completed",
                                                        "usage": {"input_tokens": 12, "output_tokens": 7}}}]


class Provider:
    """A fake routers.ai._open_ai. The check's turns answer from ``check``:
    a list of events, an exception to raise, or a function of the call's
    keyword arguments giving either. The chat's own turns answer ``reply``,
    or raise ``turn_error``. ``calls`` keeps every call."""

    def __init__(self, check=None, reply="Done.", turn_error=None, wait=0.0):
        self.check, self.reply, self.turn_error, self.wait, self.calls = check, reply, turn_error, wait, []

    def __call__(self, messages, system, entry, rt, pdf_b64s=None, **kw):
        is_check = bool(messages) and messages[0].get("content") == ai.TOOL_CHECK_PROMPT
        self.calls.append({"check": is_check, "system": system, "messages": messages, **kw})
        if is_check:
            time.sleep(self.wait)
            answer = self.check(kw) if callable(self.check) else self.check
        else:
            answer = self.turn_error or anthropic_text(self.reply)
        if isinstance(answer, Exception):
            raise answer
        return FakeResp(answer)

    def checks(self):
        return [c for c in self.calls if c["check"]]

    def turns(self):
        return [c for c in self.calls if not c["check"]]


def runtime(protocol, base_url):
    """A runtime of one connection "p" (the transport is faked)."""
    return {"user": "", "providers": {"p": {"protocol": protocol, "base_url": base_url, "api_key": "k", "name": "P"}}}


ROW = {"id": "p:m", "provider": "p", "model": "m"}


def check(monkeypatch, answer, rt=None, deadline=30):
    """``_check_tools`` against a fake provider answering ``answer``:
    (verdict or the exception it raised, the provider, the usage heard)."""
    prov = Provider(check=answer)
    monkeypatch.setattr(ai, "_open_ai", prov)
    heard = []
    try:
        verdict = ai._check_tools(rt or runtime("anthropic", "https://api.anthropic.com"), ROW, heard.append, deadline)
    except Exception as e:
        verdict = e
    return verdict, prov, heard


# --- the check ----------------------------------------------------------------

@pytest.mark.parametrize("protocol, base_url, events", [
    ("anthropic", "https://api.anthropic.com", anthropic_tool()),
    ("openai", "https://llm.example.test/v1", completions_tool()),  # Chat Completions
    ("openai", "https://api.openai.com/v1", responses_tool()),  # OpenAI's own: Responses
    ("chatgpt", "https://chatgpt.com/backend-api/codex", responses_tool()),  # the Codex backend
])
def test_any_parsed_tool_call_passes_on_each_wire(monkeypatch, protocol, base_url, events):
    verdict, prov, heard = check(monkeypatch, events, runtime(protocol, base_url))
    assert verdict == {"ok": True}
    [call] = prov.calls
    assert call["tools"] == [ai.TOOL_CHECK] and call["stream"] is True
    assert heard and heard[0]["input"] == 12


def test_a_call_of_another_tool_passes_too(monkeypatch):
    verdict, _, _ = check(monkeypatch, anthropic_tool("list_pages"))
    assert verdict == {"ok": True}


@pytest.mark.parametrize("refusal", [
    UpstreamError(404, "upstream 404: No endpoints found that support tool use"),
    UpstreamError(400, "upstream 400: registry.ollama.ai/library/gemma does not support tools"),
])
def test_a_refused_tool_whose_plain_turn_answers_is_no_tools(monkeypatch, refusal):
    verdict, prov, _ = check(monkeypatch, lambda kw: refusal if kw["tools"] else anthropic_text("ok"))
    assert verdict == {"ok": False, "reason": str(refusal)}
    assert [(c["tools"], c["stream"]) for c in prov.calls] == [([ai.TOOL_CHECK], True), (None, True)]


def test_a_reply_in_text_is_no_tools_with_its_start_quoted(monkeypatch):
    said = 'report_ok({"status": "ok"}) ' + "and then a long explanation of what it would do " * 4
    verdict, _, _ = check(monkeypatch, anthropic_text(said))
    assert verdict["ok"] is False
    assert verdict["reason"].startswith("answered in text instead of calling the check tool: “report_ok(")
    quote = verdict["reason"].split("“", 1)[1].rstrip("”")
    assert len(quote) == 80 and quote.endswith("…")


class SlowResp(FakeResp):
    """Each event takes 20 s on the patched clock."""

    def __init__(self, events, clock):
        super().__init__(events)
        self.clock = clock

    def __iter__(self):
        for line in self._lines:
            self.clock[0] += 20
            yield line


@pytest.mark.parametrize("answer", [
    UpstreamError(401, "upstream 401: invalid x-api-key"),
    UpstreamError(403, "upstream 403: forbidden"),
    UpstreamError(429, "upstream 429: rate limited"),
    UpstreamError(529, "upstream 529: overloaded"),
    urllib.error.URLError("connection refused"),
    AllowanceExhausted(1000, 1000),
    [],  # nothing at all
    [{"type": "message_delta", "delta": {"stop_reason": "end_turn"}}],  # an empty reply
    anthropic_text("<think>The user wants me to call report_ok. Let me", stop="max_tokens"),  # cut off
    lambda kw: UpstreamError(404, "upstream 404: model not found"),  # the plain turn fails too
])
def test_anything_else_is_no_verdict(monkeypatch, answer):
    verdict, _, _ = check(monkeypatch, answer)
    assert isinstance(verdict, Exception)


def test_a_stream_past_the_deadline_is_no_verdict(monkeypatch):
    clock = [0.0]
    monkeypatch.setattr(ai, "time", SimpleNamespace(monotonic=lambda: clock[0], time=time.time, sleep=time.sleep))
    monkeypatch.setattr(ai, "_open_ai", lambda *a, **kw: SlowResp(anthropic_text("thinking " * 50), clock))
    with pytest.raises(TimeoutError):
        ai._check_tools(runtime("anthropic", "https://api.anthropic.com"), ROW, lambda u: None, 30)


def test_keep_alives_past_the_deadline_are_no_verdict(monkeypatch):
    """Lines that yield no event (Anthropic's ping, an SSE comment such as
    OpenRouter's while it queues) still run into the deadline."""
    clock = [0.0]
    monkeypatch.setattr(ai, "time", SimpleNamespace(monotonic=lambda: clock[0], time=time.time, sleep=time.sleep))
    resp = SlowResp(anthropic_tool(), clock)
    resp._lines = [b'data: {"type": "ping"}\n'] * 10 + [b": OPENROUTER PROCESSING\n"] * 5 + resp._lines
    monkeypatch.setattr(ai, "_open_ai", lambda *a, **kw: resp)
    with pytest.raises(TimeoutError):
        ai._check_tools(runtime("anthropic", "https://api.anthropic.com"), ROW, lambda u: None, 30)
    assert clock[0] == 40  # given up on the first line past it


def test_a_tool_call_before_the_deadline_still_passes(monkeypatch):
    clock = [0.0]
    monkeypatch.setattr(ai, "time", SimpleNamespace(monotonic=lambda: clock[0], time=time.time, sleep=time.sleep))
    resp = SlowResp(anthropic_tool(), clock)
    resp._lines[-1:-1] = [b'data: {"type": "ping"}\n'] * 5  # lines past the deadline after the call
    monkeypatch.setattr(ai, "_open_ai", lambda *a, **kw: resp)
    assert ai._check_tools(runtime("anthropic", "https://api.anthropic.com"), ROW, lambda u: None, 130) == {"ok": True}


# --- the chat -----------------------------------------------------------------

def chat(c, ids, **body):
    """A folder chat on "readout" whose tools may do anything: its lines."""
    r = c.post("/api/ai/chat", json={"prompt": "tidy up", "agent_scope": "folder", "folder": ids["readout"],
                                     "stream": True, "permissions": ALLOW_ALL, **body})
    assert r.status_code == 200, r.text
    return [json.loads(line) for line in r.text.splitlines() if line.strip()]


def model_line(lines):
    return next(line["model"] for line in lines if "model" in line)


def entry_id(c):
    return c.get("/api/ai/settings").json()["providers"][0]["id"]


def stored(ids, pid, model=MODEL):
    return ai_settings.stored_tool_check(ids["user_id"], pid, model)


def store(ids, pid, verdict, model=MODEL):
    conf = ai_settings.ai_runtime(ids["user_id"])["providers"][pid]
    return ai_settings.save_tool_check(ids["user_id"], pid, model, verdict, conf)


NO_TOOLS = {"ok": False, "reason": "upstream 404: No endpoints found that support tool use"}


def test_the_first_chat_with_tools_checks_the_model_once(org, monkeypatch):
    c, ids = org
    from gamma.db import connect_users_db

    prov = Provider(check=anthropic_tool())
    monkeypatch.setattr(ai, "_open_ai", prov)
    lines = chat(c, ids)
    assert len(prov.checks()) == 1
    assert prov.turns()[0]["tools"] and model_line(lines)["tools"] is True
    assert "tools_off" not in model_line(lines)
    pid = entry_id(c)
    assert stored(ids, pid)["ok"] is True
    # The check's tokens count as a test's.
    with connect_users_db() as conn:
        kinds = [r[0] for r in conn.execute("SELECT kind FROM ai_usage WHERE user_id = ?", (ids["user_id"],))]
    assert "test" in kinds
    chat(c, ids)
    assert len(prov.checks()) == 1 and len(prov.turns()) == 2
    row = next(m for m in c.get("/api/ai/models").json()["models"] if m["model"] == MODEL)
    assert row["tools"] is True and "tools_reason" not in row


def test_a_model_without_tools_answers_as_a_plain_chat(org, monkeypatch):
    c, ids = org
    pid = entry_id(c)
    history = [{"role": "user", "text": "list them"},
               {"role": "ai", "text": "Listed.", "actions": [
                   {"tool": "list_pages", "args": {}, "result": "Pages…", "kind": "read"}]}]
    prov = Provider(check=anthropic_tool())
    monkeypatch.setattr(ai, "_open_ai", prov)
    # The system prompt of the same message as a plain chat.
    plain = c.post("/api/ai/chat", json={"prompt": "tidy up", "stream": True, "history": history})
    assert plain.status_code == 200
    plain_system = prov.turns()[-1]["system"]
    assert store(ids, pid, NO_TOOLS)
    lines = chat(c, ids, history=history)
    assert not prov.checks()
    turn = prov.turns()[-1]
    assert turn["tools"] is None and turn["system"] == plain_system
    assert not any(m.get("tool_calls") or m.get("role") == "tool" for m in turn["messages"])
    line = model_line(lines)
    assert line["tools"] is False and line["tools_off"] == NO_TOOLS["reason"]
    row = next(m for m in c.get("/api/ai/models").json()["models"] if m["model"] == MODEL)
    assert row["tools"] is False and row["tools_reason"] == NO_TOOLS["reason"]
    checks = c.get("/api/ai/settings").json()["providers"][0]["tool_checks"]
    assert checks[MODEL]["ok"] is False and checks[MODEL]["reason"] == NO_TOOLS["reason"] and checks[MODEL]["at"]


def test_a_check_with_no_verdict_keeps_the_tools_and_waits_before_the_next(org, monkeypatch):
    c, ids = org
    prov = Provider(check=UpstreamError(529, "upstream 529: overloaded"))
    monkeypatch.setattr(ai, "_open_ai", prov)
    lines = chat(c, ids)
    assert len(prov.checks()) == 1 and prov.turns()[0]["tools"] and model_line(lines)["tools"] is True
    assert stored(ids, entry_id(c)) is None
    chat(c, ids)
    assert len(prov.checks()) == 1  # within the retry window
    for key in ai._tool_check_retry:
        ai._tool_check_retry[key] = 0
    chat(c, ids)
    assert len(prov.checks()) == 2
    # A plain chat never checks.
    for key in ai._tool_check_retry:
        ai._tool_check_retry[key] = 0
    assert c.post("/api/ai/chat", json={"prompt": "hello", "stream": True}).status_code == 200
    assert len(prov.checks()) == 2


def test_a_refusal_of_the_account_own_starts_no_retry_window(org, monkeypatch):
    """The allowance or the calls open are the account's, not the model's:
    a shared entry's other accounts must not wait them out."""
    _, ids = org
    prov = Provider(check=AllowanceExhausted(1000, 1000))
    monkeypatch.setattr(ai, "_open_ai", prov)
    rt = ai_settings.ai_runtime(ids["user_id"])
    entry = next(m for m in rt["models"] if m["model"] == MODEL)
    assert ai._checked_tools(ids["user_id"], rt, entry, True) == ""
    assert not ai._tool_check_retry and stored(ids, entry["provider"]) is None
    prov.check = anthropic_tool()
    assert ai._checked_tools(ids["user_id"], rt, entry, True) == ""
    assert len(prov.checks()) == 2 and stored(ids, entry["provider"])["ok"] is True


@pytest.mark.parametrize("broken", ["save_tool_check", "stored_tool_check"])
def test_a_storage_error_never_stops_the_chat(org, monkeypatch, broken):
    import sqlite3

    c, ids = org

    def fail(*args):
        raise sqlite3.OperationalError("database is locked")
    monkeypatch.setattr(ai, broken, fail)
    prov = Provider(check=anthropic_text("Sure: ok."))
    monkeypatch.setattr(ai, "_open_ai", prov)
    lines = chat(c, ids)
    if broken == "save_tool_check":
        # The check's verdict still applies to this chat.
        assert len(prov.checks()) == 1 and prov.turns()[-1]["tools"] is None
        assert model_line(lines)["tools_off"].startswith("answered in text")
    else:
        assert not prov.checks() and prov.turns()[-1]["tools"]
        assert ai._tool_check_retry


def test_a_stored_record_without_a_reason_still_reads(org, monkeypatch):
    """A record a hand edit or another version left without its reason."""
    c, ids = org
    pid = entry_id(c)

    def put(entries):
        next(e for e in entries if e["id"] == pid)["tool_checks"] = {MODEL: {"ok": False}}
    ai_settings.update_provider_entries(ids["user_id"], put)
    row = next(m for m in c.get("/api/ai/models").json()["models"] if m["model"] == MODEL)
    assert row["tools"] is False and row["tools_reason"] == ""
    monkeypatch.setattr(ai, "_open_ai", Provider())
    assert model_line(chat(c, ids))["tools_off"] == ai._NO_REASON


def test_two_first_chats_at_once_run_one_check(org, monkeypatch):
    _, ids = org
    prov = Provider(check=anthropic_tool(), wait=0.3)
    monkeypatch.setattr(ai, "_open_ai", prov)
    rt = ai_settings.ai_runtime(ids["user_id"])
    entry = next(m for m in rt["models"] if m["model"] == MODEL)
    said = []
    threads = [threading.Thread(target=lambda: said.append(ai._checked_tools(ids["user_id"], rt, entry, True)))
               for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert said == ["", ""] and len(prov.checks()) == 1


def test_an_integration_token_only_applies_a_stored_verdict(org, monkeypatch):
    c, ids = org
    token = c.post("/api/integrations/tokens", json={"name": "Codex"}).json()["token"]
    bearer = fresh_client()
    prov = Provider(check=anthropic_tool())
    monkeypatch.setattr(ai, "_open_ai", prov)

    def token_chat():
        r = bearer.post("/api/ai/chat", headers={"Authorization": f"Bearer {token}"}, json={
            "prompt": "tidy up", "agent_scope": "folder", "folder": ids["readout"], "stream": True,
            "permissions": ALLOW_ALL})
        assert r.status_code == 200, r.text
        return [json.loads(line) for line in r.text.splitlines() if line.strip()]

    pid = entry_id(c)
    assert model_line(token_chat())["tools"] is True
    assert not prov.checks() and stored(ids, pid) is None
    assert store(ids, pid, NO_TOOLS)
    assert model_line(token_chat())["tools_off"] == NO_TOOLS["reason"]
    assert prov.turns()[-1]["tools"] is None


@pytest.mark.parametrize("error, forgets", [
    (UpstreamError(400, "upstream 400: this model does not support tools"), True),
    (UpstreamError(401, "upstream 401: invalid x-api-key"), False),
    (UpstreamError(429, "upstream 429: rate limited"), False),
])
def test_a_refused_chat_with_tools_forgets_a_stale_pass(org, monkeypatch, error, forgets):
    c, ids = org
    pid = entry_id(c)
    assert store(ids, pid, {"ok": True})
    prov = Provider(check=anthropic_tool(), turn_error=error)
    monkeypatch.setattr(ai, "_open_ai", prov)
    r = c.post("/api/ai/chat", json={"prompt": "tidy up", "agent_scope": "folder", "folder": ids["readout"],
                                     "stream": True, "permissions": ALLOW_ALL})
    assert r.status_code == 502
    assert (stored(ids, pid) is None) is forgets
    prov.turn_error = None
    chat(c, ids)
    assert len(prov.checks()) == (1 if forgets else 0)


def test_a_verdict_is_stored_only_on_the_connection_it_was_checked_on(org):
    c, ids = org
    pid = entry_id(c)
    conf = dict(ai_settings.ai_runtime(ids["user_id"])["providers"][pid])
    try:
        # The key changed while the check ran: its verdict is not the new key's.
        assert c.put(f"/api/ai/providers/{pid}", json={"api_key": "sk-test-key-other-456"}).status_code == 200
        assert ai_settings.save_tool_check(ids["user_id"], pid, MODEL, NO_TOOLS, conf) is None
        conf = dict(ai_settings.ai_runtime(ids["user_id"])["providers"][pid])
        assert c.put(f"/api/ai/providers/{pid}", json={"base_url": "https://llm.example.test"}).status_code == 200
        assert ai_settings.save_tool_check(ids["user_id"], pid, MODEL, NO_TOOLS, conf) is None
        conf = dict(ai_settings.ai_runtime(ids["user_id"])["providers"][pid])
        assert ai_settings.save_tool_check(ids["user_id"], pid, "not-picked", NO_TOOLS, conf) is None
        assert ai_settings.save_tool_check(ids["user_id"], pid, MODEL, NO_TOOLS, conf)["ok"] is False
    finally:
        c.put(f"/api/ai/providers/{pid}", json={"api_key": "sk-test-key-123", "base_url": ""})


def test_the_context_export_leaves_out_the_tools_of_a_model_without(org, monkeypatch):
    c, ids = org
    monkeypatch.setattr(ai, "_open_ai", lambda *a, **kw: pytest.fail("the export called the provider"))
    body = {"prompt": "tidy up", "agent_scope": "folder", "folder": ids["readout"], "permissions": ALLOW_ALL}
    with_tools = c.post("/api/ai/chat/context", json=body)
    assert with_tools.status_code == 200 and "list_pages" in with_tools.text
    assert store(ids, entry_id(c), NO_TOOLS)
    without = c.post("/api/ai/chat/context", json=body)
    assert without.status_code == 200 and "list_pages" not in without.text


# --- a shared entry -----------------------------------------------------------

@pytest.fixture(scope="module")
def admin(client):
    make_user("tc_admin", "pw", is_admin=1)
    try:
        yield login("tc_admin", "pw")
    finally:
        drop_user("tc_admin")


@pytest.fixture
def shared(admin):
    """One shared Anthropic entry; the server's shared config is emptied
    after the test (the whole run shares one users.db)."""
    r = admin.post("/api/admin/ai-providers", json={
        "protocol": "anthropic", "name": "Lab key", "api_key": "sk-ant-shared-tc-4242", "models": "lab-model"})
    assert r.status_code == 200, r.text
    try:
        yield r.json()["providers"][0]["id"]
    finally:
        ai_settings.save_server_ai({"providers": []})


def test_a_shared_entry_keeps_one_verdict_for_every_account(admin, shared, monkeypatch):
    make_user("tc_member", "pw")
    make_user("tc_other", "pw")
    member, other = login("tc_member", "pw"), login("tc_other", "pw")
    prov = Provider(check=anthropic_text("I can't call tools, but here is the status: ok."))
    monkeypatch.setattr(ai, "_open_ai", prov)
    r = member.post("/api/ai/chat", json={"prompt": "tidy up", "agent_scope": "folder", "folder": "",
                                          "stream": True, "permissions": ALLOW_ALL})
    assert r.status_code == 200, r.text
    assert len(prov.checks()) == 1 and prov.turns()[-1]["tools"] is None
    entry = next(e for e in ai_settings.load_server_ai()["providers"] if e["id"] == shared)
    assert entry["tool_checks"]["lab-model"]["ok"] is False
    row = other.get("/api/ai/models").json()["models"][0]
    assert row["tools"] is False and row["tools_reason"].startswith("answered in text")
    assert other.get("/api/ai/settings").json()["providers"][0]["tool_checks"]["lab-model"]["ok"] is False
    assert admin.get("/api/admin/ai-providers").json()["providers"][0]["tool_checks"]["lab-model"]["ok"] is False
    # Only an administrator checks a shared model again.
    assert member.post(f"/api/ai/providers/{shared}/test", json={"tools_model": "lab-model"}).status_code == 404
    prov.check = anthropic_tool()
    body = admin.post(f"/api/ai/providers/{shared}/test", json={"tools_model": "lab-model"}).json()
    assert body["ok"] is True and body["tools"]["ok"] is True
    assert other.get("/api/ai/models").json()["models"][0]["tools"] is True
    # A new base URL is another server: its verdicts go.
    r = admin.put(f"/api/admin/ai-providers/{shared}", json={"base_url": "https://lab.example.test"})
    assert r.status_code == 200, r.text
    entry = next(e for e in ai_settings.load_server_ai()["providers"] if e["id"] == shared)
    assert "tool_checks" not in entry
