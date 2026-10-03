"""read_chats: the AI chat kept with a page or folder (routers/chats.py
buckets) read back as a transcript — the current conversation, the index of
earlier ones and their ids, windows through a long one, and the scope rules."""

from gamma.ai_tools import run_agent_tool

from ai_fixtures import folder, org  # noqa: F401  (org is a fixture)

CONVERSATION = [
    {"role": "user", "text": "What does it measure?", "pdfs": ["paper.pdf"],
     "contextPages": [{"id": "x", "title": "qec paper"}], "images": ["data:image/png;base64,AAAA"]},
    {"role": "ai", "text": "It measures T1.",
     "actions": [{"kind": "read", "summary": "Read “cavity paper”", "tool": "read_page"}]},
    {"role": "user", "text": "And T2?"},
    {"role": "ai", "text": "Provider timed out", "error": True},
]


def _save(c, bucket, messages, title=None):
    body = {"messages": messages} if title is None else {"messages": messages, "title": title}
    r = c.put(f"/api/chats/{bucket}", json=body)
    assert r.status_code == 200, r.text


def _archive(c, bucket, messages, title=""):
    r = c.post("/api/chat-history/archive", json={"bucket": bucket, "messages": messages, "title": title})
    assert r.status_code == 200, r.text
    return r.json()["id"]


def test_read_chats_returns_the_page_conversation_and_its_history(org):
    c, ids = org
    earlier = _archive(c, ids["a"], [{"role": "user", "text": "First look at the setup"},
                                     {"role": "ai", "text": "The setup is a 3D cavity."}])
    _save(c, ids["a"], CONVERSATION, title="Measurements")
    text, action = run_agent_tool(ids["ws"], folder(ids["readout"]), "read_chats", {"page_id": ids["a"]})
    assert action["kind"] == "read" and action["page_id"] == ids["a"]
    assert "cavity paper" in action["summary"]
    assert "the current conversation “Measurements”, 4 messages" in text
    assert "[1] User (with paper.pdf, “qec paper”, 1 image(s)): What does it measure?" in text
    assert "[2] AI: It measures T1.\n    Tools used: Read “cavity paper”" in text
    assert "[4] AI (failed reply): Provider timed out" in text
    assert "data:image" not in text  # attachments are named, never inlined
    # The earlier conversation is indexed by id, titled from its first message…
    assert f"chat_id={earlier} | “First look at the setup” | 2 messages" in text
    # …and read by that id.
    text, _ = run_agent_tool(ids["ws"], folder(ids["readout"]), "read_chats",
                             {"page_id": ids["a"], "chat_id": earlier})
    assert "an earlier conversation" in text and "[2] AI: The setup is a 3D cavity." in text
    assert "Earlier conversations" not in text
    # In a paper chat the page is the default.
    text, _ = run_agent_tool(ids["ws"], {"type": "page", "page_id": ids["a"]}, "read_chats", {})
    assert "“Measurements”" in text


def test_read_chats_windows_a_long_conversation(org):
    c, ids = org
    _save(c, ids["b"], [{"role": "user" if n % 2 else "ai", "text": f"message {n} " + "x" * 80}
                        for n in range(1, 11)])
    scope = {**folder(ids["readout"]), "read_chars": 250}  # two ~100-char messages per window
    text, _ = run_agent_tool(ids["ws"], scope, "read_chats", {"page_id": ids["b"]})
    assert "[1] User: message 1" in text and "[2] AI: message 2" in text and "[3]" not in text
    assert f'call read_chats(page_id="{ids["b"]}", start=3) to continue' in text
    text, _ = run_agent_tool(ids["ws"], scope, "read_chats", {"page_id": ids["b"], "start": 3})
    assert text.count("\n[") == 2 and "[3] User: message 3" in text
    text, action = run_agent_tool(ids["ws"], scope, "read_chats", {"page_id": ids["b"], "start": 11})
    assert action["error"] and "has 10 messages" in text


def test_read_chats_reaches_folder_chats_inside_the_scope(org):
    """A folder's chat is kept under the folder's id: named by path or by
    that id, ``home`` at the library root."""
    c, ids = org
    _save(c, ids["readout"], [{"role": "user", "text": "Which of these use a cavity?"}])
    _save(c, "home", [{"role": "user", "text": "Library-wide question"}])
    for where in ("readout", ids["readout"]):
        text, action = run_agent_tool(ids["ws"], folder(""), "read_chats", {"folder": where})
        assert "folder “readout”" in text and "Which of these use a cavity?" in text
        assert "page_id" not in action
    # No argument = the chat's own folder; a relative path resolves inside it.
    text, _ = run_agent_tool(ids["ws"], folder(ids["readout"]), "read_chats", {})
    assert "Which of these use a cavity?" in text
    text, _ = run_agent_tool(ids["ws"], folder(ids["readout"]), "read_chats", {"folder": "nondestructive"})
    assert text == "No AI chat is kept with folder “readout / nondestructive”."
    text, _ = run_agent_tool(ids["ws"], folder(""), "read_chats", {})
    assert "the library root" in text and "Library-wide question" in text
    # A folder that does not exist is an error, not an empty chat; a folder
    # beside the chat's own is out of reach.
    text, action = run_agent_tool(ids["ws"], folder(ids["readout"]), "read_chats", {"folder": "nowhere"})
    assert action["error"] and 'no folder "readout / nowhere"' in text
    text, action = run_agent_tool(ids["ws"], folder(ids["cooling"]), "read_chats", {"folder": ids["readout"]})
    assert action["error"] and "outside this chat's folder" in text and "cavity" not in text


def test_read_chats_respects_the_scope(org):
    c, ids = org
    _save(c, ids["note"], [{"role": "user", "text": "private to the loose note"}])
    for scope, args in [(folder(ids["readout"]), {"page_id": ids["note"]}),               # page outside the folder
                        ({"type": "page", "page_id": ids["a"]}, {"page_id": ids["note"]}),
                        ({"type": "page", "page_id": ids["a"]}, {"folder": "readout"})]:
        text, action = run_agent_tool(ids["ws"], scope, "read_chats", args)
        assert action["error"] and "scope" in text and "private" not in text
    # A chat_id only reads inside the bucket it belongs to.
    other = _archive(c, ids["note"], [{"role": "user", "text": "archived private note"}])
    text, action = run_agent_tool(ids["ws"], folder(""), "read_chats", {"page_id": ids["a"], "chat_id": other})
    assert action["error"] and "private" not in text
    # A page nobody chatted about says so plainly.
    r = c.post("/api/blocks", json={"parent_id": "root", "content": "quiet page", "properties": {}})
    text, action = run_agent_tool(ids["ws"], folder(""), "read_chats", {"page_id": r.json()["id"]})
    assert text.startswith("No AI chat is kept with page “quiet page”") and not action.get("error")
