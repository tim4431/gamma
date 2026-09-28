"""Attached library pages enter both prompt context and the read-tool scope."""
import json

import pytest

from ai_fixtures import FakeResp, ai_provider, indexed_pdf, org, props  # noqa: F401
from gamma.ai_tools import run_agent_tool


@pytest.mark.parametrize("scope_type", ["page", "folder"])
def test_references_expand_reads_but_not_writes(org, scope_type):
    c, ids = org
    child = c.post("/api/blocks", json={"parent_id": ids["note"], "content": "mentionneedle"}).json()["id"]
    scope = {"type": scope_type, "page_id": ids["b"], "folder": "readout",
             "context_pages": [ids["note"], "missing"]}
    for tool, args in [("read_page", {"page_id": ids["note"]}),
                       ("read_block", {"block_id": child}),
                       ("search_library", {"query": "mentionneedle"})]:
        text, action = run_agent_tool(ids["ws"], scope, tool, args)
        assert not action.get("error"), text
        assert "mentionneedle" in text
    for tool, args in [("edit_block", {"block_id": child, "content": "changed"}),
                       ("create_block", {"parent_id": ids["note"], "content": "changed"}),
                       ("move_block", {"block_id": child, "parent_id": ids["b"]})]:
        text, action = run_agent_tool(ids["ws"], scope, tool, args)
        assert action.get("error"), text
    assert props(c, child)["content"] == "mentionneedle"
    # Removing the attachment removes access, too.
    text, action = run_agent_tool(ids["ws"], {**scope, "context_pages": []}, "read_page", {"page_id": ids["note"]})
    assert action.get("error"), text


def test_chat_resolves_reference_context_and_tools(org, ai_provider, monkeypatch):
    c, ids = org
    import gamma.routers.ai as ai
    calls = []

    def fake_open(messages, system, entry, rt, pdf_b64s=None, **kw):
        calls.append(messages)
        if len(calls) == 1:
            content = "\n".join(m["content"] for m in messages)
            assert f"Gamma page ID: {ids['note']}" in content
            assert f"Gamma page ID: {ids['b']}" in content
            assert ids["note"] in system and "read-only" in system
            return FakeResp([
                {"type": "content_block_start", "content_block": {"type": "tool_use", "id": "r1", "name": "read_page"}},
                {"type": "content_block_delta", "delta": {"type": "input_json_delta", "partial_json": json.dumps({"page_id": ids["note"]})}},
                {"type": "content_block_stop"},
            ])
        assert messages[-1]["role"] == "tool"
        assert "loose note" in messages[-1]["content"]
        return FakeResp([{"type": "content_block_delta", "delta": {"type": "text_delta", "text": "Read the reference."}}])

    monkeypatch.setattr(ai, "_open_ai", fake_open)
    response = c.post("/api/ai/chat", json={"prompt": "Compare @loose note", "agent_scope": "page",
        "page_id": ids["b"], "pages": [ids["b"], ids["note"]], "stream": True})
    assert response.status_code == 200, response.text
    assert "Read the reference." in response.text
    assert len(calls) == 2


def test_chat_sends_selection_place_and_formula_picture(org, ai_provider, monkeypatch):
    """A structured selection reaches the provider labelled with its page
    and section, and a formula's region goes along as a rendered picture."""
    from gamma import ai_context
    import gamma.routers.ai as ai
    c, ids = org
    pages = ["Title\nAbstract\nWe study cats.", "2 Methods\nThe Hamiltonian is\nH = ∑ ω σ\nwhere ω is the drive."]
    monkeypatch.setattr(ai_context, "pdf_path", lambda ws, doc: "fake.pdf")
    monkeypatch.setattr(ai_context, "extract_pages", lambda src: pages)
    monkeypatch.setattr(ai_context, "outline", lambda src: [])
    monkeypatch.setattr(ai_context, "ensure_indexed", lambda *args: None)
    monkeypatch.setattr(ai_context, "render_page",
                        lambda src, page_no, max_side, box=None: ((b"png", "image/png", 4, 4), 2))
    seen = {}

    def fake_open(messages, system, entry, rt, pdf_b64s=None, **kw):
        seen.update(messages=messages, images=kw.get("images"))
        return FakeResp([{"type": "content_block_delta", "delta": {"type": "text_delta", "text": "ok"}}])

    monkeypatch.setattr(ai, "_open_ai", fake_open)
    response = c.post("/api/ai/chat", json={"prompt": "explain", "page_id": ids["a"], "stream": True,
        "selections": [{"text": "H = ∑ ω σ", "page": 2, "box": [0.1, 0.4, 0.5, 0.45]}]})
    assert response.status_code == 200, response.text
    question = seen["messages"][-1]["content"]
    assert 'Selected passage (PDF page 2; section "2 Methods"; a picture' in question
    assert "Text around the selected passage (PDF page 2" in question
    assert seen["images"] == [("image/png", "cG5n")]
    first = json.loads(response.text.splitlines()[0])["context"][0]
    # Too short to anchor on text: placed on the viewer's page by its box.
    # The crop box goes along so the chat can show the picture again.
    assert first["selection"]["passages"] == [{"page": 2, "section": "2 Methods", "found": False, "crop": True,
                                               "box": [0.09, 0.39, 0.51, 0.46]}]


def test_selection_crop_redraws_the_saved_region(org, monkeypatch):
    """The chat shows the picture a reply sent from its saved page + box."""
    from gamma import ai_context
    import gamma.routers.ai as ai
    c, _ = org
    rendered = []

    def render(src, page_no, max_side, box=None):
        rendered.append((page_no, box))
        return (b"png", "image/png", 4, 4), 2

    monkeypatch.setattr(ai, "_pdf_path", lambda ws, doc: "fake.pdf")
    monkeypatch.setattr(ai_context, "render_page", render)
    doc = "ab" * 12
    response = c.get(f"/api/ai/selection-crop/{doc}?page=2&box=0.09,0.39,0.51,0.46")
    assert response.status_code == 200 and response.content == b"png"
    assert response.headers["content-type"] == "image/png"
    assert rendered == [(2, (0.09, 0.39, 0.51, 0.46))]
    for bad in ("0.5,0.1,0.2,0.3", "0,0,1", "a,b,c,d", "0,0,1.5,1"):
        assert c.get(f"/api/ai/selection-crop/{doc}?page=2&box={bad}").status_code == 400
    assert c.get(f"/api/ai/selection-crop/not-hex?page=2&box=0,0,1,1").status_code == 400


def test_context_deduplicates_and_rejects_non_pages(org):
    from gamma.ai_context import gather_inputs
    from gamma.routers.ai import AIChatRequest
    c, ids = org
    child = c.post("/api/blocks", json={"parent_id": ids["note"], "content": "not a page"}).json()["id"]
    payload = AIChatRequest(prompt="read", pages=[ids["note"], ids["note"], child, "missing"])
    _, context, coverage, _ = gather_inputs(ids["ws"], payload, False)
    assert context.count(f"Gamma page ID: {ids['note']}") == 1
    assert f"Gamma page ID: {child}" not in context
    assert len(coverage) == 1


def test_pdf_references_preserve_selection_metadata_and_budget(org, monkeypatch):
    from gamma import ai_context
    from gamma.routers.ai import AIChatRequest
    c, ids = org
    c.post("/api/blocks", json={"parent_id": ids["a"], "content": "private annotation"})
    calls = []
    monkeypatch.setattr(ai_context, "ensure_indexed", lambda *args: None)
    monkeypatch.setattr(ai_context, "document_map", lambda *args, **kw: "p.3: cavity results")

    def selection(ws, doc, passages, budget, with_head=True):
        calls.append(([p["text"] for p in passages], budget, with_head))
        return "[PDF page 3]\nSelected cavity results", [{"page": 3, "section": "", "found": True, "crop": False}]

    monkeypatch.setattr(ai_context, "selection_context", selection)
    payload = AIChatRequest(prompt="Compare", page_id=ids["a"],
        pages=[ids["a"], ids["note"], "missing"], selection="cavity results",
        multi_context_char_limit=6000, agent_scope="page")
    _, context, coverage, message_context = ai_context.gather_inputs(ids["ws"], payload, False)
    # The windows around the selection skip the head slice (the document
    # context carries the head) and ride with the message, not the pages.
    assert calls == [(["cavity results"], 3000, False)]
    assert "Ada One" in context and "2019" in context and "Nature" in context
    assert "[PDF page 3]" in message_context and "[PDF page 3]" not in context
    assert "private annotation" not in context
    assert f"Document map for Gamma page ID: {ids['a']}" in context
    assert len(coverage) == 2 and coverage[0]["selection"]
    assert coverage[0]["page_id"] == ids["a"] and coverage[1]["page_id"] == ids["note"]
    assert not coverage[0]["notes"] and coverage[1]["notes"]
    payload.include_notes = True
    _, context, coverage, _ = ai_context.gather_inputs(ids["ws"], payload, False)
    assert "private annotation" in context and coverage[0]["notes"]


def test_reference_pdf_is_readable_and_searchable(org, indexed_pdf, monkeypatch):
    from gamma import ai_context
    monkeypatch.setattr(ai_context, "pdf_excerpt", lambda *args, **kw: (
        "[PDF page 3]\nquantum error correction with cat qubits", 0, 50, (3, 3)))
    _, ids = org
    scope = {"type": "page", "page_id": ids["note"], "context_pages": [ids["a"]]}
    for name, args in [("read_page", {"page_id": ids["a"], "pdf_page": 3}),
                       ("search_library", {"query": "cat qubits"})]:
        text, action = run_agent_tool(ids["ws"], scope, name, args)
        assert not action.get("error"), text
        assert "cat qubits" in text and ids["a"] in text


def test_context_export_is_the_prompt_the_chat_sends(org):
    """/ai/chat/context renders the request /ai/chat would send — system
    prompt, tools, replayed turns, the draft last — without a provider."""
    c, ids = org
    response = c.post("/api/ai/chat/context", json={
        "prompt": "What next?", "title": "My chat", "agent_scope": "page",
        "page_id": ids["b"], "pages": [ids["b"], ids["note"]],
        "history": [{"role": "user", "text": "Summarize ```code```"},
                    {"role": "ai", "text": "Done.", "actions": [
                        {"tool": "read_page", "args": {"page_id": ids["note"]}, "result": "loose note text"}]}]})
    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("text/markdown")
    text = response.text
    assert text.startswith("# My chat — context")
    assert "## System prompt" in text and "## Tools" in text and "`read_page`" in text
    assert f"Gamma page ID: {ids['note']}" in text
    assert "### 3. Tool result · read_page" in text and "loose note text" in text
    # A turn holding its own fence gets a longer one around it.
    assert "````text\n" in text
    # The document context rides on the oldest question, the draft is last.
    assert "User question: Summarize" in text
    assert text.rstrip().endswith("### 5. User\n\n```text\nWhat next?\n```")
