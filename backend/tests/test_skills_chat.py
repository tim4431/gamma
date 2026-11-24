"""Skills in the library chat (docs/dev/skills.md): a message that ran one
reads its instructions on its turn, on every request, from the requester's
catalog by id; load_skill lets the agent load one itself, saves a stub, and
is rendered again on replay; titles and exports read ``/name args``."""

import json
from types import SimpleNamespace

import pytest

from ai_fixtures import FakeResp, ai_provider, org  # noqa: F401  (fixtures)
from conftest import account_of, fresh_client, login, make_user, token_for
from gamma import skills, workspaces
from gamma.ai_context import CONTEXT_INTRO, _ELIDED_RESULT, _REPLAYED_NOTE, build_messages, elide_live_results
from gamma.ai_tools.registry import agent_tools, available, mcp_tools, replayed, run_agent_tool
from gamma.ai_tools.skills import ELIDED_RESULT, SAVED_RESULT

SUMMARIZE = skills.BUILTIN_PREFIX + "summarize"


def _skill(name, body="Do $ARGUMENTS.", model=True, **more):
    return {"id": more.pop("id", "id-" + name), "name": name, "description": f"{name} things",
            "argument_hint": "", "body": body, "model": model, "source": "account", **more}


def _own(user_id, name, body="Mine: $ARGUMENTS.", model=True) -> dict:
    """An account skill made as Settings makes one."""
    return skills.create(user_id, skills.clean_fields(name, f"{name} things", "", body, model))


def _block(name, text) -> str:
    """The <skill> block a turn that ran ``name`` holds, ``text`` its rendered body."""
    return f'<skill name="{name}">\n{text}\n</skill>'


def _streamed(response) -> list[dict]:
    assert response.status_code == 200, response.text
    return [json.loads(line) for line in response.text.splitlines() if line.strip()]


@pytest.fixture
def provider(org, ai_provider, monkeypatch):
    """The provider faked: each call's messages and system prompt are kept,
    and it answers with the replies queued on it (plain text by default)."""
    import gamma.routers.ai as ai

    calls, replies = [], []

    def fake_open(messages, system, entry, rt, pdf_b64s=None, **kw):
        calls.append({"messages": [dict(m) for m in messages], "system": system, "tools": kw.get("tools")})
        return replies.pop(0) if replies else FakeResp(
            [{"type": "content_block_delta", "delta": {"type": "text_delta", "text": "ok"}}])

    monkeypatch.setattr(ai, "_open_ai", fake_open)
    return SimpleNamespace(calls=calls, replies=replies)


def _tool_call(name, args, call_id="t1"):
    return FakeResp([
        {"type": "content_block_start", "content_block": {"type": "tool_use", "id": call_id, "name": name}},
        {"type": "content_block_delta", "delta": {"type": "input_json_delta", "partial_json": json.dumps(args)}},
        {"type": "content_block_stop"},
    ])


# --- a message that ran a skill ----------------------------------------------

def test_the_turn_reads_the_instructions_and_replays_byte_identical(org, provider):
    """The current turn opens with the skill's block, $ARGUMENTS as the
    stored args; on the next request the same turn, replayed from history,
    is the same bytes, the document context still ahead of it."""
    c, ids = org
    invocation = {"id": SUMMARIZE, "name": "summarize", "args": "the methods"}
    first = {"prompt": "the methods", "page_id": ids["note"], "skill": invocation, "stream": True}
    _streamed(c.post("/api/ai/chat", json=first))
    turn = provider.calls[-1]["messages"][-1]["content"]
    rendered = skills.render(skills.find(skills.builtins(), SUMMARIZE), "the methods")
    # The message follows the block whole.
    assert turn.endswith(_block("summarize", rendered) + "\n\nthe methods")
    assert turn.index(CONTEXT_INTRO) < turn.index('<skill name="summarize">')  # the document context opens it

    second = {**first, "prompt": "and the results", "skill": None,
              "history": [{"role": "user", "text": "the methods", "skill": invocation},
                          {"role": "ai", "text": "A summary."}]}
    _streamed(c.post("/api/ai/chat", json=second))
    replayed = provider.calls[-1]["messages"]
    assert replayed[0]["content"] == turn
    assert "<skill" not in replayed[-1]["content"]


def test_a_skill_with_no_arguments_is_sent(org, provider):
    c, _ = org
    _streamed(c.post("/api/ai/chat", json={"prompt": "", "stream": True,
                                           "skill": {"id": SUMMARIZE, "name": "summarize", "args": ""}}))
    turn = provider.calls[-1]["messages"][-1]["content"]
    assert turn.endswith(_block("summarize", skills.render(skills.find(skills.builtins(), SUMMARIZE), "")))


def test_an_unknown_skill_reads_a_note_in_history_and_is_refused_as_the_current_turn(org, provider):
    c, _ = org
    gone = {"id": "no-such-id", "name": "gone", "args": "x"}
    _streamed(c.post("/api/ai/chat", json={"prompt": "next", "stream": True, "history": [
        {"role": "user", "text": "x", "skill": gone}, {"role": "ai", "text": "done"}]}))
    assert provider.calls[-1]["messages"][0]["content"] == skills.missing_note(gone) + "\n\nx"
    calls = len(provider.calls)
    for stream in (True, False):
        r = c.post("/api/ai/chat", json={"prompt": "x", "stream": stream, "skill": gone})
        assert r.status_code == 400 and r.json()["kind"] == "skill" and "/gone" in r.json()["detail"]
    assert len(provider.calls) == calls  # no provider call for it


def test_a_deleted_override_does_not_fall_back_to_the_built_in(org, provider):
    """An account skill named like a built-in replaces it; once deleted,
    the messages that ran it read the note, not the built-in, whose id
    differs."""
    c, ids = org
    made = _own(ids["user_id"], "summarize", "My own summary rules for $ARGUMENTS.")
    try:
        invocation = {"id": made["id"], "name": "summarize", "args": "it"}
        _streamed(c.post("/api/ai/chat", json={"prompt": "it", "stream": True, "skill": invocation}))
        assert "My own summary rules for it." in provider.calls[-1]["messages"][-1]["content"]
    finally:
        skills.remove(ids["user_id"], made["id"], None)
    _streamed(c.post("/api/ai/chat", json={"prompt": "again", "stream": True, "history": [
        {"role": "user", "text": "it", "skill": invocation}, {"role": "ai", "text": "done"}]}))
    head = provider.calls[-1]["messages"][0]["content"]
    assert head.startswith(skills.missing_note(invocation)) and "<skill" not in head


def test_a_tokens_context_export_holds_the_built_ins_only(org):
    c, ids = org
    made = _own(ids["user_id"], "private-notes", "SECRET-INSTRUCTIONS $ARGUMENTS")
    try:
        bearer = fresh_client()
        bearer.headers["Authorization"] = f"Bearer {token_for(c)}"
        body = {"prompt": "go", "history": [
            {"role": "user", "text": "a", "skill": {"id": made["id"], "name": "private-notes", "args": "a"}},
            {"role": "ai", "text": "done"},
            {"role": "user", "text": "b", "skill": {"id": SUMMARIZE, "name": "summarize", "args": "b"}},
            {"role": "ai", "text": "done"}]}
        exported = bearer.post("/api/ai/chat/context", json=body)
        assert exported.status_code == 200, exported.text
        assert "SECRET-INSTRUCTIONS" not in exported.text
        assert skills.missing_note({"name": "private-notes"}) in exported.text
        assert '<skill name="summarize">' in exported.text
        refused = bearer.post("/api/ai/chat/context", json={
            "prompt": "a", "skill": {"id": made["id"], "name": "private-notes", "args": "a"}})
        assert refused.status_code == 400 and refused.json()["kind"] == "skill"
        # The account's own session reads its skill.
        own = c.post("/api/ai/chat/context", json=body)
        assert "SECRET-INSTRUCTIONS a" in own.text
    finally:
        skills.remove(ids["user_id"], made["id"], None)


def test_another_member_continuing_the_conversation_reads_the_note(provider):
    """In a shared workspace's chat, a member's message that ran their own
    skill names it by an id the next member's catalog does not hold: that
    member's request to the provider carries the note, never the body."""
    for name in ("skc_first", "skc_second"):
        make_user(name, "pw")
    first_id = account_of("skc_first")
    lab = workspaces.create("Skills lab", first_id, kind="shared")["id"]
    workspaces.set_member(lab, account_of("skc_second"), "editor", by=first_id)
    members = []
    for name in ("skc_first", "skc_second"):
        member = login(name, "pw")
        member.headers["X-Gamma-Workspace"] = lab
        member.post("/api/ai/providers", json={"protocol": "anthropic", "api_key": "sk-test-key-123",
                                               "models": "claude-solo"}).raise_for_status()
        members.append(member)
    made = _own(first_id, "lab-notes", "PRIVATE-LAB-RULES for $ARGUMENTS.")
    try:
        invocation = {"id": made["id"], "name": "lab-notes", "args": "the run"}
        _streamed(members[0].post("/api/ai/chat", json={"prompt": "the run", "stream": True, "skill": invocation}))
        assert "PRIVATE-LAB-RULES for the run." in provider.calls[-1]["messages"][-1]["content"]
        _streamed(members[1].post("/api/ai/chat", json={"prompt": "and now?", "stream": True, "history": [
            {"role": "user", "text": "the run", "skill": invocation}, {"role": "ai", "text": "done"}]}))
        sent = provider.calls[-1]["messages"]
        assert "PRIVATE-LAB-RULES" not in json.dumps(sent)
        assert sent[0]["content"] == skills.missing_note(invocation) + "\n\nthe run"
    finally:
        skills.remove(first_id, made["id"], None)


# --- load_skill ----------------------------------------------------------------

CATALOG = [_skill("visible", "Visible rules. $ARGUMENTS"), _skill("hidden", "Hidden rules.", model=False)]


def test_load_skill_is_armed_only_with_a_loadable_skill_and_the_permission_on():
    def armed(catalog, perms=None):
        scope = {"type": "folder", "folder": "", "skills": catalog}
        return "load_skill" in {t["name"] for t in agent_tools("folder", perms, has=available(scope))}

    assert armed(CATALOG)
    assert not armed(CATALOG, {"skills": "off"})
    assert not armed([CATALOG[1]])  # only a skill run from "/"
    assert not armed([])
    assert "load_skill" not in mcp_tools()


def test_load_skill_returns_the_instructions_and_saves_a_stub(org):
    _, ids = org
    scope = {"type": "folder", "folder": "", "skills": CATALOG}
    text, action = run_agent_tool(ids["ws"], scope, "load_skill", {"name": "/visible"})
    assert text == "Visible rules."
    assert action["result"] == SAVED_RESULT and action["skill_id"] == "id-visible"
    assert action["args"] == {"name": "/visible"} and action["kind"] == "skill" and not action.get("error")
    assert "Visible rules" not in json.dumps(action)
    for name in ("hidden", "nothing"):
        text, action = run_agent_tool(ids["ws"], scope, "load_skill", {"name": name})
        assert text.startswith(f"error: no skill /{name}") and action["error"]
        assert "Hidden rules" not in text + json.dumps(action)


def test_the_agent_loads_a_skill_and_lists_them_in_its_prompt(org, provider):
    """Through the chat: the session's own skill is listed and loaded, the
    model reads its instructions as the call's result, and the streamed
    chip carries the stub."""
    c, ids = org
    made = _own(ids["user_id"], "tidy-notes", "TIDY-BODY: group by topic.")
    try:
        provider.replies.append(_tool_call("load_skill", {"name": "tidy-notes"}))
        lines = _streamed(c.post("/api/ai/chat", json={
            "prompt": "tidy my notes", "agent_scope": "folder", "folder": "", "stream": True}))
        system = provider.calls[0]["system"]
        assert "Skills: instructions the user keeps" in system and "- tidy-notes: tidy-notes things" in system
        assert "load_skill" in {t["name"] for t in provider.calls[0]["tools"]}
        assert provider.calls[1]["messages"][-1] == {"role": "tool", "call_id": "t1",
                                                     "content": "TIDY-BODY: group by topic."}
        action = next(line["action"] for line in lines if "action" in line)
        assert action["result"] == SAVED_RESULT and action["skill_id"] == made["id"]
        assert "TIDY-BODY" not in json.dumps(lines)
        step = next(line["step"] for line in lines if "step" in line)
        assert step["args"] == {"name": "tidy-notes"}
        # Off in the permissions: neither the listing nor the tool.
        provider.calls.clear()
        _streamed(c.post("/api/ai/chat", json={"prompt": "tidy", "agent_scope": "folder", "folder": "",
                                               "stream": True, "permissions": {"skills": "off"}}))
        assert "Skills: instructions" not in provider.calls[0]["system"]
        assert "load_skill" not in {t["name"] for t in provider.calls[0]["tools"]}
    finally:
        skills.remove(ids["user_id"], made["id"], None)


def test_a_built_in_kept_to_a_slash_leaves_the_listing_and_still_runs_from_it(org, provider):
    """Settings' Only from / for a built-in (PUT .../state {model: false}):
    the prompt stops naming it, load_skill refuses it, and a message that
    runs it from "/" still carries its instructions."""
    c, ids = org
    c.put("/api/ai/skills/builtin:explain/state", json={"model": False}).raise_for_status()
    try:
        _streamed(c.post("/api/ai/chat", json={"prompt": "what does eq. 3 mean", "agent_scope": "folder",
                                               "folder": "", "stream": True}))
        system = provider.calls[0]["system"]
        assert "- summarize:" in system and "- explain:" not in system
        catalog = skills.catalog(ids["user_id"], True)
        assert [s["model"] for s in catalog if s["name"] == "explain"] == [False]
        text, action = run_agent_tool(ids["ws"], {"type": "folder", "folder": "", "skills": catalog},
                                      "load_skill", {"name": "explain"})
        assert text.startswith("error: no skill /explain") and action["error"]

        # "/" is untouched: the message still reads the instructions.
        explain = skills.BUILTIN_PREFIX + "explain"
        _streamed(c.post("/api/ai/chat", json={
            "prompt": "eq. 3", "agent_scope": "folder", "folder": "", "stream": True,
            "skill": {"id": explain, "name": "explain", "args": "eq. 3"}}))
        turn = provider.calls[-1]["messages"][-1]["content"]
        assert turn.endswith(_block("explain", skills.render(skills.find(skills.builtins(), explain), "eq. 3"))
                             + "\n\neq. 3")
    finally:
        c.put("/api/ai/skills/builtin:explain/state", json={"model": True}).raise_for_status()


def _load(skill_id, name, error=False, result=SAVED_RESULT):
    return {"tool": "load_skill", "kind": "error" if error else "skill", "args": {"name": name},
            "result": result, **({"error": True} if error else {"skill_id": skill_id})}


def test_replay_renders_each_load_as_it_was_and_keeps_a_failed_ones_error():
    catalog = [_skill("visible", "Visible rules."), _skill("other", "Other rules.")]
    history = [
        {"role": "user", "text": "one"},
        {"role": "ai", "text": "a", "actions": [_load("id-visible", "visible"),
                                                _load("", "nope", error=True, result="error: no skill /nope"),
                                                _load("id-gone", "gone")]},
        {"role": "user", "text": "two"},
        {"role": "ai", "text": "b", "actions": [_load("id-visible", "visible"), _load("id-other", "other")]},
    ]
    messages = build_messages(SimpleNamespace(history=history, prompt="three"), "", with_tools=True,
                              skills=catalog, replay=replayed)
    results = [m["content"] for m in messages if m["role"] == "tool"]
    # A skill loaded again later leaves the earlier load's bytes as they were.
    assert results == [
        "Visible rules.",
        _REPLAYED_NOTE + "error: no skill /nope",
        skills.missing_note({"name": "gone"}),
        "Visible rules.",
        "Other rules.",
    ]
    # Outside the replay budget: a long skill is replayed whole.
    long = [_skill("visible", "x" * 20_000)]
    messages = build_messages(SimpleNamespace(history=history[:2], prompt="three"), "", with_tools=True,
                              skills=long, replay=replayed)
    assert next(m["content"] for m in messages if m["role"] == "tool") == "x" * 20_000


def test_a_reply_keeps_its_loaded_skills_until_the_emergency_retry():
    def conversation():
        return [{"role": "user", "content": "go"},
                {"role": "assistant", "content": "", "tool_calls": [
                    {"id": "s", "name": "load_skill", "arguments": {"name": "v"}},
                    {"id": "r", "name": "read_page", "arguments": {}}]},
                {"role": "tool", "call_id": "s", "content": "S" * 50_000},
                {"role": "tool", "call_id": "r", "content": "R" * 50_000},
                *[m for k in range(3) for m in (
                    {"role": "assistant", "content": "", "tool_calls": [{"id": f"x{k}", "name": "read_page",
                                                                        "arguments": {}}]},
                    {"role": "tool", "call_id": f"x{k}", "content": "small"})]]

    kept = conversation()
    elide_live_results(kept, budget=10_000, replay=replayed)
    assert kept[2]["content"] == "S" * 50_000 and kept[3]["content"] == _ELIDED_RESULT
    emergency = conversation()
    elide_live_results(emergency, keep_rounds=1, budget=0, keep_replayed=False, replay=replayed)
    assert emergency[2]["content"] == ELIDED_RESULT and emergency[3]["content"] == _ELIDED_RESULT


# --- titles and exports ----------------------------------------------------------

def test_titles_and_exports_read_the_command(org):
    from gamma.chat_export import transcript
    from gamma.db import connect_pages_db
    from gamma.routers.chats import derive_title

    _, ids = org
    messages = [{"role": "user", "text": "the methods", "skill": {"id": SUMMARIZE, "name": "summarize",
                                                                  "args": "the methods"}},
                {"role": "ai", "text": "A summary."}]
    assert derive_title(messages) == "/summarize the methods"
    assert derive_title([{"role": "user", "text": "", "skill": {"id": SUMMARIZE, "name": "summarize",
                                                                "args": ""}}]) == "/summarize"
    with connect_pages_db(ids["ws"]) as conn:
        text, _ = transcript(ids["ws"], conn, messages, "Chat")
        asked, _ = transcript(ids["ws"], conn, messages, "Chat", agent="Codex")
    assert "## You\n\n/summarize the methods\n" in text
    assert "## You\n\n/summarize the methods\n" in asked
