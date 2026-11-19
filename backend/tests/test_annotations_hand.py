"""The writing tools, the agent's hand (gamma/annotations/hand.py, places.py;
the registry's ``comment``, ``resolve`` and ``highlight``, docs/dev/
annotations.md "The writing tools"): a comment on a PDF quote (found by its
text, highlighted, a thread on it), on a text file's lines (with their
quote, or whole), on a note and on the page itself; a quote that is not
there refused with the closest passage; a reply and a resolve; a highlight
in a colour and a style; a call made again writing nothing twice; who may
write: Gamma AI's permission, a viewer's chat, and the Comment level of a
read, a commenting, a write and a turn token over MCP and the HTTP API.
The rest of MCP is test_mcp.py's."""

import pytest

from conftest import account_of, doc_props, login, make_page, make_user, thread_on, workspace_of
from gamma import agents, ai_permissions, integrations, storage
from gamma.ai_tools import agent_tools, approval_preview, run_agent_tool
from gamma.db import connect_pages_db
from gamma.documents.model import stored_name
from gamma.blocks_store import load_props
from test_mcp import call, rpc

USER = "hand_tim"
LINE_ONE = "The loss is defined as the mean squared error of the fit."
LINE_TWO = "We train the network by gradient descent on the data."
TEX = ["\\section{Method}", "We fit a small network to the training set by gradient descent.",
       "The learning rate is set to $10^{-3}$.", "", "\\section{Results}", "The error falls by half."]


def _pdf() -> bytes:
    import pymupdf

    doc = pymupdf.open()
    for n, text in enumerate((LINE_ONE, LINE_TWO)):
        page = doc.new_page(width=600, height=800)
        page.insert_text((72, 100), text, fontsize=11)
        page.insert_text((72, 140), f"A second sentence on page {n + 1} of the paper.", fontsize=11)
    return doc.tobytes()


@pytest.fixture(scope="module")
def tim(client):
    make_user(USER, "pw")
    c = login(USER, "pw")
    up = c.post("/api/uploads", files={"file": ("paper.pdf", _pdf(), "application/pdf")})
    assert up.status_code == 200, up.text
    pdf = make_page(c, "The paper", properties=doc_props(up.json()["doc_id"]))["id"]
    data = ("\n".join(TEX) + "\n").encode()
    stem = storage.content_digest(data)
    storage.put_upload(workspace_of(USER), stored_name(stem, ".tex"), data)
    tex = make_page(c, "method.tex", properties=doc_props(stem, name="method.tex", format="text", ext=".tex",
                                                          size=len(data)))["id"]
    notes = make_page(c, "Notes")["id"]
    note = c.post("/api/blocks", json={"parent_id": notes, "content": "A note to talk about"}).json()["id"]
    c.pages = {"pdf": pdf, "tex": tex, "notes": notes, "note": note}
    yield c
    c.close()


def _chat(page_id=None, **more):
    """Gamma AI's scope, as routers/ai.py builds it: every tool allowed."""
    where = {"type": "page", "page_id": page_id} if page_id else {"type": "folder", "folder": ""}
    return {**where, "actor": account_of(USER), "can_write": True, "can_comment": True,
            "agent": agents.stamp(agents.gamma(), USER), **more}


def _run(name, args, scope=None):
    return run_agent_tool(workspace_of(USER), scope or _chat(), name, args)


def _threads(c, page_id):
    return {t["id"]: t for t in c.get(f"/api/pages/{page_id}/comments", params={"state": "all"}).json()["threads"]}


def _block(block_id):
    with connect_pages_db(workspace_of(USER)) as conn:
        row = conn.execute("SELECT parent_id, content, properties FROM unified_blocks WHERE id = ?",
                           (block_id,)).fetchone()
    return row[0], row[1], load_props(row[2])


def test_a_comment_on_a_pdf_quote_highlights_it_and_opens_a_thread(tim):
    pdf = tim.pages["pdf"]
    text, action = _run("comment", {"page_id": pdf, "quote": "mean squared  error of the fit", "pdf_page": 1,
                                    "body": "Say which error this is."})
    assert not action.get("error"), text
    thread_id, highlight = action["thread_id"], action["block_id"]
    assert text.startswith("ok — commented on page 1, “mean squared error of the fit” of “The paper”: "
                           f"thread {thread_id}, comment {action['comment_id']}, on the new highlight {highlight}.")
    link = f"?ws={workspace_of(USER)}&page={pdf}&block={highlight}&thread={thread_id}"
    assert text.endswith(f"Link: {link}") and action["link"] == link
    # the passage is a highlight block like a person's, stamped as Gamma AI's, in the comment's wash
    parent, note, props = _block(highlight)
    assert (parent, note, props["quote"]) == (pdf, "", "mean squared error of the fit")
    assert props["agent"] == {"id": "gamma", "name": "Gamma AI", "for": USER}
    assert props["pdf_position"]["pageNumber"] == 1 and props["pdf_position"]["rects"]
    assert "style" not in props and props["color"] == "#ffe28fa6"
    # the thread is Gamma AI's, for its account, opened on that passage
    thread = _threads(tim, pdf)[thread_id]
    assert thread["block_id"] == highlight
    assert [(c["author"], c["agent"], c["agent_kind"]) for c in thread["comments"]] == [(USER, "Gamma AI", "gamma")]
    # list_annotations reads it back, and read_annotation by the link it answered with
    listed, _ = _run("list_annotations", {"scope": pdf})
    assert f"### 1. Page 1 · “mean squared error of the fit”\n\n- id: `{thread_id}` · passage" in listed
    assert f'"link": "{link}"' in _run("read_annotation", {"id": thread_id})[0]


def test_a_quote_that_is_not_there_is_refused_with_the_closest(tim):
    pdf = tim.pages["pdf"]
    before = len(_threads(tim, pdf))
    text, action = _run("comment", {"page_id": pdf, "quote": "The loss is defined as the mean square error of a fit.",
                                    "pdf_page": 1, "body": "?"})
    assert action.get("error") and text.startswith("error: that quote is not on PDF page 1 or beside it")
    assert "the closest passage reads “The loss is defined as the mean" in text and "% alike)" in text
    text, action = _run("comment", {"page_id": pdf, "quote": "Quantum chromodynamics in the infrared", "pdf_page": 1,
                                    "body": "?"})
    assert action.get("error") and "closest" not in text
    # on page 2's neighbour too, never another place by guess; a page the file lacks; half a place
    text, _ = _run("highlight", {"page_id": pdf, "quote": "gradient descent on the data", "pdf_page": 1})
    assert text.startswith("ok — highlighted page 2, “gradient descent on the data”")
    assert _run("comment", {"page_id": pdf, "quote": "anything", "pdf_page": 9, "body": "?"})[0] == (
        "error: the PDF has no page 9 (it has 2)")
    assert "needs both its quote" in _run("comment", {"page_id": pdf, "pdf_page": 1, "body": "?"})[0]
    assert "line names lines of a text file" in _run("comment", {"page_id": pdf, "line": 3, "body": "?"})[0]
    assert len(_threads(tim, pdf)) == before


def test_a_comment_on_lines_of_a_text_file(tim):
    tex = tim.pages["tex"]
    # with their quote, found near them, written as the file spells it
    text, action = _run("comment", {"page_id": tex, "line": 1, "end_line": 1,
                                    "quote": "The learning rate is set to $10^{-3}$.", "body": "Why this rate?"})
    assert not action.get("error"), text
    assert text.startswith("ok — commented on line 3, “The learning rate is set to $10^{-3}$.” of “method.tex”")
    _, _, props = _block(action["block_id"])
    assert (props["text_position"]["line"], props["text_position"]["end"]) == (3, 3)
    assert props["quote"] == "The learning rate is set to $10^{-3}$."
    # without a quote, the lines whole as they are now
    text, action = _run("highlight", {"page_id": tex, "line": 5, "end_line": 6, "color": "green", "style": "underline",
                                      "note": "the results"})
    assert text.startswith("ok — highlighted lines 5–6, “\\section{Results} The error falls by half.”"), text
    _, note, props = _block(action["block_id"])
    assert note == "the results" and props["style"] == "underline" and props["color"] == "#aaebaaa6"
    assert props["quote"] == "\\section{Results}\nThe error falls by half."
    # a quote not in the file, with the closest text and its lines
    text, _ = _run("comment", {"page_id": tex, "line": 2, "quote": "We fit a large network to the test set by "
                                                                    "gradient descent.", "body": "?"})
    assert text.startswith("error: that quote is not in the file near line 2; the closest text is at line 2, "
                           "“We fit a small network"), text
    assert _run("comment", {"page_id": tex, "line": 40, "body": "?"})[0] == "error: the file has 6 lines"


def test_a_quote_on_a_secrets_file_is_refused_whatever_it_reads(tim):
    """A secrets file's text is kept from the model: a quote on its lines
    is refused before any match, the same words whether it is there or
    not; its lines are named whole."""
    data = b"API_KEY=sk-abc123\nDEBUG=1\n"
    stem = storage.content_digest(data)
    storage.put_upload(workspace_of(USER), stored_name(stem, ".local"), data)
    env = make_page(tim, ".env.local", properties=doc_props(stem, name=".env.local", format="text", ext=".local",
                                                             size=len(data)))["id"]
    answers = set()
    for name in ("comment", "highlight"):
        for quote in ("API_KEY=sk-abc123", "API_KEY=sk-zzz"):
            preview, answer = approval_preview(workspace_of(USER), _chat(), name,
                                               {"page_id": env, "line": 1, "quote": quote, "body": "?"})
            assert preview is None
            answers.add(answer)
    assert answers == {"error: this file's text is kept from you: name its lines with line and end_line, "
                       "without a quote"}
    preview, answer = approval_preview(workspace_of(USER), _chat(), "highlight", {"page_id": env, "line": 1})
    assert answer is None and (preview["place"]["kind"], preview["place"]["lines"]) == ("lines", [1, 1])


def test_a_comment_on_a_note_and_on_the_page(tim):
    notes, note = tim.pages["notes"], tim.pages["note"]
    text, action = _run("comment", {"block_id": note, "body": "On this note."})
    assert text.startswith("ok — commented on a note of “Notes”") and action["block_id"] == note
    text, action = _run("comment", {"page_id": notes, "body": "On the whole page."})
    assert text.startswith("ok — commented on the page of “Notes”") and action["block_id"] == notes
    # a page chat writes on its own page alone
    page_chat = _chat(notes)
    assert not _run("comment", {"body": "Here."}, page_chat)[1].get("error")
    assert _run("comment", {"page_id": tim.pages["pdf"], "body": "There."}, page_chat)[0] == (
        "error: page is outside this chat's scope")
    assert "is not a note of this page" in _run("comment", {"page_id": notes, "block_id": tim.pages["tex"],
                                                            "body": "?"})[0]
    assert "a highlight marks a passage" in _run("highlight", {"block_id": note})[0]


def test_a_reply_and_a_resolve(tim):
    notes = tim.pages["notes"]
    asked = thread_on(tim, notes, tim.pages["note"], "Can you check this?")
    text, action = _run("comment", {"thread_id": asked["id"], "body": "Checked: it holds."})
    assert text.startswith(f"ok — replied on Notes, a note: thread {asked['id']}, comment "), text
    assert action["reply"] is True and action["summary"] == "Replied in a thread in “Notes”"
    # the same words moments later write nothing twice
    again, action = _run("comment", {"thread_id": asked["id"], "body": "Checked: it holds."})
    assert again.startswith("error: you wrote this reply on Notes, a note moments ago") and action.get("error")
    assert [c["agent"] for c in _threads(tim, notes)[asked["id"]]["comments"]] == ["", "Gamma AI"]
    text, action = _run("resolve", {"thread_id": asked["id"]})
    assert text.startswith(f"ok — resolved thread {asked['id']} on Notes, a note.") and action["kind"] == "resolve"
    stored = _threads(tim, notes)[asked["id"]]
    assert (stored["resolved_by"], stored["resolved_agent"]) == (USER, {"id": "gamma", "kind": "gamma", "name": "Gamma AI"})
    text, action = _run("resolve", {"thread_id": asked["id"]})
    assert text.startswith(f"ok — thread {asked['id']} on Notes, a note was resolved already") and action["noop"]
    assert _run("resolve", {"thread_id": "nosuchthread"})[0] == (
        "error: No comment thread has that id: list_annotations lists them.")
    # the inbox tells the thread's author, naming Gamma AI
    items = tim.get("/api/inbox", params={"filter": "all"}).json()["items"]
    assert any(i["thread_id"] == asked["id"] and i["kind"] == "reply" and i["agent"]["id"] == "gamma" for i in items)


def test_a_call_made_again_doubles_nothing(tim):
    pdf = tim.pages["pdf"]
    args = {"page_id": pdf, "quote": "A second sentence on page 1", "pdf_page": 1, "body": "Is this needed?"}
    first, action = _run("comment", args)
    assert not action.get("error"), first
    again, repeat = _run("comment", args)
    assert again.startswith("error: you wrote this comment on page 1, “A second sentence on page 1” of “The paper” "
                            f"moments ago (thread {action['thread_id']}") and repeat.get("error")
    # other words on the same place are a new thread on a passage of its own
    assert not _run("comment", {**args, "body": "And this?"})[1].get("error")
    marked, made = _run("highlight", {"page_id": pdf, "quote": "of the paper", "pdf_page": 1})
    assert not made.get("error"), marked
    again, _ = _run("highlight", {"page_id": pdf, "quote": "of the paper", "pdf_page": 1, "color": "blue"})
    assert again.startswith(f"error: you highlighted page 1, “of the paper” of “The paper” already "
                            f"(highlight {made['block_id']}")


def _marks(page_id):
    with connect_pages_db(workspace_of(USER)) as conn:
        return conn.execute("SELECT COUNT(*) FROM unified_blocks WHERE page_id = ? AND kind = 'highlight'",
                            (page_id,)).fetchone()[0]


def _roots(conn, page_id):
    return conn.execute("SELECT id FROM comments WHERE page_id = ? AND id = thread_id", (page_id,)).fetchall()


def test_a_viewers_agent_comments_but_marks_nothing(tim, monkeypatch):
    """A new highlight is a block, an edit: a viewer's agent makes none, a
    highlight or a comment on a passage, and writes nothing for it; its
    comment on the page goes in."""
    from gamma import workspaces
    from gamma.annotations import hand, places

    monkeypatch.setattr(workspaces, "role_of", lambda ws, user: "viewer")
    ws, pdf = workspace_of(USER), tim.pages["pdf"]
    writer = hand.Writer(account_of(USER), USER, agents.gamma())
    before = _marks(pdf)
    with connect_pages_db(ws) as conn:
        passage = places.find(conn, ws, pdf, {"quote": "the mean squared error", "pdf_page": 1})
        threads = len(_roots(conn, pdf))
        with pytest.raises(ValueError, match="can only view this workspace"):
            hand.highlight(conn, writer, ws, pdf, passage)
        with pytest.raises(ValueError, match="can only view this workspace"):
            hand.open_thread(conn, writer, ws, pdf, passage, "On this passage.")
        assert (_marks(pdf), len(_roots(conn, pdf))) == (before, threads)
        page = places.find(conn, ws, pdf, {})
        assert hand.open_thread(conn, writer, ws, pdf, page, "On the whole paper.")["created"]


def test_gamma_ais_permission_and_a_viewers_chat(tim, monkeypatch):
    from gamma import workspaces

    names = {t["name"] for t in agent_tools("page", can_write=False)}
    assert {"comment", "resolve", "highlight"} <= names and "edit_block" not in names  # a viewer comments
    assert not {"comment", "resolve", "highlight"} & {t["name"] for t in agent_tools("page", can_comment=False)}
    text, _ = run_agent_tool(workspace_of(USER), _chat(can_comment=False), "comment",
                             {"page_id": tim.pages["notes"], "body": "x"})
    assert text == "error: you cannot comment in this workspace"
    with monkeypatch.context() as viewer:
        viewer.setattr(workspaces, "role_of", lambda ws, user: "viewer")
        text, _ = run_agent_tool(workspace_of(USER), _chat(can_write=False), "comment",
                                 {"page_id": tim.pages["notes"], "body": "A viewer's agent comments."})
        assert text.startswith("ok — commented"), text
        # a viewer's chat highlights nothing, nor comments on a passage: refused before it asks
        before = _marks(tim.pages["pdf"])
        for name, args in (("highlight", {}), ("comment", {"body": "On this passage."})):
            asked = {"page_id": tim.pages["pdf"], "quote": "the mean squared error", "pdf_page": 1, **args}
            text, action = run_agent_tool(workspace_of(USER), _chat(can_write=False), name, asked)
            assert action.get("error") and "can only view this workspace" in text, text
            preview, answer = approval_preview(workspace_of(USER), _chat(can_write=False), name, asked)
            assert preview is None and "can only view this workspace" in answer
        assert _marks(tim.pages["pdf"]) == before
    text, _ = run_agent_tool(workspace_of(USER), _chat(), "comment", {"page_id": tim.pages["notes"], "body": "x"},
                             allowed_tools={"read_page"})
    assert text == "error: tool not enabled — the user's permission settings do not allow it"
    # Comments and highlights asks by default: the card names the page and the place; a quote not there asks nobody
    assert ai_permissions.permission_state({}, "comment", True) == "ask"
    preview, answer = approval_preview(workspace_of(USER), _chat(), "comment", {
        "page_id": tim.pages["pdf"], "quote": "A second sentence on page 2", "pdf_page": 2, "body": "Why?"})
    assert answer is None and (preview["title"], preview["reply"]) == ("The paper", False)
    assert preview["place"] == {"kind": "passage", "page": 2, "quote": "A second sentence on page 2"}
    preview, answer = approval_preview(workspace_of(USER), _chat(), "highlight", {
        "page_id": tim.pages["pdf"], "quote": "nowhere in this paper at all", "pdf_page": 1})
    assert preview is None and answer.startswith("error: that quote is not on PDF page 1")


# --- the Comment level -------------------------------------------------------------

WRITING = {"comment", "resolve", "highlight"}


def _token(c, **access):
    made = c.post("/api/integrations/tokens", json={"name": "Claude Code", **access})
    assert made.status_code == 201, made.text
    return made.json()


def _listed(client, token):
    return {t["name"] for t in rpc(client, token, "tools/list").json()["result"]["tools"]}


def test_the_level_gates_the_tools(client, tim, anon):
    ws, notes = workspace_of(USER), tim.pages["notes"]
    reader = _token(tim)
    commenter = _token(tim, comment_workspaces=[ws])
    assert (commenter["comment_all"], commenter["comment_workspaces"]) == (False, [ws])
    writer = _token(tim, scope="write")
    try:
        # a read token: no writing tool, over MCP or the HTTP API, even called by name
        assert not WRITING & _listed(client, reader["token"])
        refused = call(client, reader["token"], "comment", {"page_id": notes, "body": "sneaky"})
        assert refused["isError"] and "give it the Comment level" in refused["content"][0]["text"]
        http = anon.post(f"/api/pages/{notes}/comments", json={"block_id": notes, "body": "sneaky"},
                         headers={"Authorization": f"Bearer {reader['token']}"})
        assert http.status_code == 403 and http.json()["detail"] == "this token is read-only"
        # the Comment level: the three tools, written as the connection, and the HTTP API comments too
        assert WRITING <= _listed(client, commenter["token"])
        made = call(client, commenter["token"], "comment", {"page_id": notes, "body": "From a connection."})
        assert not made["isError"], made
        assert set(made["structuredContent"]) >= {"page_id", "block_id", "thread_id", "comment_id", "link"}
        thread = _threads(tim, notes)[made["structuredContent"]["thread_id"]]
        assert made["structuredContent"]["link"] == (
            f"http://localhost/?ws={ws}&page={notes}&block={notes}&thread={thread['id']}")
        assert [(c["agent"], c["agent_kind"]) for c in thread["comments"]] == [("Claude Code", "connection")]
        with connect_pages_db(ws) as conn:
            assert conn.execute("SELECT agent_id FROM comments WHERE id = ?",
                                (thread["id"],)).fetchone()[0] == commenter["id"]
        http = anon.post(f"/api/pages/{notes}/comments", json={"block_id": notes, "body": "Over HTTP."},
                         headers={"Authorization": f"Bearer {commenter['token']}"})
        assert http.status_code == 201, http.text
        # a write token comments in its one workspace, as it writes there
        assert WRITING <= _listed(client, writer["token"])
        assert not call(client, writer["token"], "resolve", {"thread_id": thread["id"]})["isError"]
        # the level taken away: refused at once
        assert tim.patch(f"/api/integrations/tokens/{commenter['id']}", json={"workspaces": [ws]}).status_code == 200
        assert call(client, commenter["token"], "comment", {"page_id": notes, "body": "x"})["isError"]
    finally:
        for item in (reader, commenter, writer):
            tim.delete(f"/api/integrations/tokens/{item['id']}")


def test_a_turns_token_comments_as_its_agent(client, tim, anon):
    ws, pdf = workspace_of(USER), tim.pages["pdf"]
    agent = agents.computer_agent(agents.here_id(), "codex", "TIM-PC")
    token_id, token = integrations.mint_turn(account_of(USER), ws, agent.id, 60)
    try:
        assert WRITING <= _listed(client, token)
        marked = call(client, token, "highlight", {"page_id": pdf, "quote": "We train the network", "pdf_page": 2,
                                                   "note": "the method"})
        assert not marked["isError"], marked
        _, note, props = _block(marked["structuredContent"]["block_id"])
        assert note == "the method" and props["agent"]["id"] == agent.id and props["agent"]["name"] == "Codex"
        asked = call(client, token, "comment", {"page_id": pdf, "quote": "gradient descent", "pdf_page": 2,
                                                "body": "Which optimizer settings?"})
        assert not asked["isError"], asked
        thread = _threads(tim, pdf)[asked["structuredContent"]["thread_id"]]
        assert [(c["agent"], c["agent_kind"]) for c in thread["comments"]] == [("Codex", "computer")]
        # its workspace alone, and never the HTTP API
        http = anon.post(f"/api/pages/{pdf}/comments", json={"block_id": pdf, "body": "x"},
                         headers={"Authorization": f"Bearer {token}"})
        assert http.status_code == 403
    finally:
        integrations.drop(token_id)


def test_an_area_highlight_and_a_comment_on_an_area(tim):
    pdf = tim.pages["pdf"]
    box = [0.1, 0.2, 0.6, 0.45]
    text, action = _run("highlight", {"page_id": pdf, "pdf_page": 2, "box": box, "color": "green"})
    assert not action.get("error"), text
    assert text.startswith("ok — highlighted an area of page 2 of “The paper”") and action["area"] is True
    parent, _, props = _block(action["block_id"])
    pos = props["pdf_position"]
    # the rectangle a person's Ctrl+drag makes: no quote, in the page's own 600 × 800 frame
    assert (parent, props["quote"], pos["area"], pos["pageNumber"]) == (pdf, "", True, 2)
    assert pos["rects"] == [{"x1": 60, "y1": 160, "x2": 360, "y2": 360}] and "style" not in props
    # the same area again answers with the first; an area takes the wash alone
    again, act = _run("highlight", {"page_id": pdf, "pdf_page": 2, "box": box})
    assert act["error"] and f"highlight {action['block_id']}" in again
    styled, act = _run("highlight", {"page_id": pdf, "pdf_page": 2, "box": [0.1, 0.5, 0.2, 0.6], "style": "underline"})
    assert act["error"] and "takes no other style" in styled
    # a comment on an area opens its thread on a new area highlight, and is not written twice
    asked = {"page_id": pdf, "pdf_page": 2, "box": [0.1, 0.6, 0.9, 0.9], "body": "Explain this figure."}
    text, action = _run("comment", asked)
    assert not action.get("error"), text
    assert _block(action["block_id"])[2]["pdf_position"]["area"] is True
    assert _threads(tim, pdf)[action["thread_id"]]["block_id"] == action["block_id"]
    again, act = _run("comment", asked)
    assert act["error"] and "moments ago" in again
