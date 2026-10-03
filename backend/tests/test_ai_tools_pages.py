"""The page tools' executors — list_pages (filters, labels mode), rename_page,
move_page, read_page (windows through a long PDF, page anchors, the tunable
read cap) — and the folder-scope rules they share: folders named by path or
id, read inside the chat's folder."""

import secrets

from conftest import make_folder, make_label
from gamma.ai_tools import agent_tools, run_agent_tool

from ai_fixtures import folder, org, props  # noqa: F401  (org is a fixture)


def _tree_insert(c, name, parent="folders"):
    """A new folder called ``name`` under ``parent``, even beside one of the
    same name (make_folder reuses those)."""
    folder_id = secrets.token_urlsafe(9)
    r = c.post("/api/pages/folders/ops", json={"ops": [
        {"op": "insert", "id": folder_id, "parent": parent, "content": name}]})
    assert r.status_code == 200, r.text
    return folder_id


def test_list_pages_scoped_and_annotated(org):
    c, ids = org
    text, action = run_agent_tool(ids["ws"], folder(ids["readout"]), "list_pages", {})
    assert action["kind"] == "list" and "2 pages" in action["summary"]
    assert f"id={ids['a']}" in text and f"id={ids['b']}" in text
    assert ids["note"] not in text  # outside the folder
    assert "One et al., 2019, Nature" in text  # cached metadata surfaces
    # Root scope lists everything, including the loose note.
    root_text, _ = run_agent_tool(ids["ws"], folder(""), "list_pages", {})
    assert ids["note"] in root_text and "note" in root_text


def test_rename_page(org):
    c, ids = org
    text, action = run_agent_tool(ids["ws"], folder(ids["readout"]), "rename_page",
                                  {"page_id": ids["a"], "title": "  Ada2019 —  Cavity readout \n"})
    assert text.startswith("ok"), text
    assert action["kind"] == "rename" and "Ada2019 — Cavity readout" in action["summary"]
    assert props(c, ids["a"])["content"] == "Ada2019 — Cavity readout"
    # No-op rename mutates nothing, but still shows as a (non-error) chip.
    text, action = run_agent_tool(ids["ws"], folder(ids["readout"]), "rename_page",
                                  {"page_id": ids["a"], "title": "Ada2019 — Cavity readout"})
    assert action["kind"] == "rename" and not action.get("error")
    # Every chip carries the raw call so the chat can expand it.
    assert action["tool"] == "rename_page" and action["result"] == text
    assert action["args"]["title"] == "Ada2019 — Cavity readout"


def test_scope_blocks_outside_pages(org):
    c, ids = org
    text, action = run_agent_tool(ids["ws"], folder(ids["readout"]), "rename_page",
                                  {"page_id": ids["note"], "title": "hijack"})
    assert text.startswith("error") and action["error"] and action["kind"] == "error"
    assert props(c, ids["note"])["content"] == "loose note"
    text, _ = run_agent_tool(ids["ws"], folder(ids["readout"]), "rename_page",
                             {"page_id": "nope", "title": "x"})
    assert text.startswith("error")


def test_move_page_keeps_folders_outside_the_scope(org):
    c, ids = org
    # A relative path resolves inside the scope and is made when missing; the
    # "cooling" membership, outside the chat's folder, survives.
    text, action = run_agent_tool(ids["ws"], folder(ids["readout"]), "move_page",
                                  {"page_id": ids["b"], "folder": "fast"})
    assert text.startswith("ok") and "(a new folder)" in text, text
    assert action["kind"] == "move" and "readout / fast" in action["summary"]
    # The chat lists the change from structured fields, not the summary.
    assert action["title"] == "qec paper" and action["to"] == "readout / fast"
    assert action["from"] == "readout / nondestructive, cooling"
    fast = make_folder(c, "readout/fast")  # the one the call made
    assert props(c, ids["b"])["properties"]["folders"] == [ids["cooling"], fast]
    # "" files the page at the scope itself: a subfolder gives way to its parent.
    run_agent_tool(ids["ws"], folder(ids["readout"]), "move_page", {"page_id": ids["b"], "folder": ""})
    assert props(c, ids["b"])["properties"]["folders"] == [ids["cooling"], ids["readout"]]
    # The chat's own path names the chat's folder, not a subfolder of the same name.
    _, action = run_agent_tool(ids["ws"], folder(ids["readout"]), "move_page",
                               {"page_id": ids["b"], "folder": "Readout"})
    assert action["noop"] is True


def test_move_page_refines_an_ancestor_and_makes_nested_folders(org):
    c, ids = org
    page = c.post("/api/blocks", json={"parent_id": "root", "content": "refined page", "properties": {
        "folders": [ids["readout"], ids["nondestructive"]]}}).json()["id"]
    # Filed in a folder and the one above it: the level above gives way,
    # even though it is outside the chat's folder.
    text, _ = run_agent_tool(ids["ws"], folder(ids["nondestructive"]), "move_page",
                             {"page_id": page, "folder": ""})
    assert text.startswith("ok"), text
    assert props(c, page)["properties"]["folders"] == [ids["nondestructive"]]
    # A missing path makes every missing level, once.
    text, action = run_agent_tool(ids["ws"], folder(""), "move_page",
                                  {"page_id": page, "folder": "cooling / laser / doppler"})
    assert text.startswith("ok") and action["to"] == "cooling / laser / doppler", text
    assert props(c, page)["properties"]["folders"] == [make_folder(c, "cooling/laser/doppler")]
    _, action = run_agent_tool(ids["ws"], folder(""), "move_page", {"page_id": page, "folder": "cooling/laser/doppler"})
    assert action["noop"] is True
    tree = c.get("/api/blocks/folders/subtree").json()["block"]
    cooling = next(n for n in tree["children"] if n["id"] == ids["cooling"])
    assert [n["content"] for n in cooling["children"]] == ["laser"]
    assert [n["content"] for n in cooling["children"][0]["children"]] == ["doppler"]


def test_folder_arguments_resolve_by_path_or_id(org):
    """A folder argument is a path as the tools show it ("a / b"), a bare
    "a/b", or an id. Each name matches exactly, else ignoring case; a folder
    chat reads it below its own folder (its own path read from there); a
    path several folders share is refused, naming their ids."""
    c, ids = org
    top = make_folder(c, "Resolve")
    sub = make_folder(c, "Resolve/Sub")
    slashed = _tree_insert(c, "I/O", top)  # a name may hold a "/"
    twins = [_tree_insert(c, "Twin", top) for _ in range(2)]
    page = c.post("/api/blocks", json={"parent_id": "root", "content": "resolved page",
                                       "properties": {"folders": [slashed, sub]}}).json()["id"]

    def listed(scope, where):
        return run_agent_tool(ids["ws"], scope, "list_pages", {"folder": where})[0]

    for where in ("Resolve / I/O", "resolve / i/o", slashed, "Resolve / Sub", "Resolve/Sub", "RESOLVE/SUB"):
        assert f"id={page}" in listed(folder(""), where), where
    assert 'folders=["Resolve / I/O", "Resolve / Sub"]' in listed(folder(""), "Resolve")
    for where in ("Sub", "sub", "Resolve / Sub", "I/O"):
        assert f"id={page}" in listed(folder(top), where), where
    for scope, where in ((folder(""), "Resolve / Twin"), (folder(top), "twin")):
        text = listed(scope, where)
        assert text.startswith("error") and twins[0] in text and twins[1] in text, text
        assert '"Resolve / Twin"' in text
    # An id names that one folder; outside the chat's folder it is refused.
    run_agent_tool(ids["ws"], folder(""), "move_page", {"page_id": page, "folder": twins[1]})
    assert props(c, page)["properties"]["folders"] == [twins[1]]
    text = listed(folder(top), ids["readout"])
    assert text.startswith("error") and "outside this chat's folder" in text
    text, _ = run_agent_tool(ids["ws"], folder(top), "move_page", {"page_id": page, "folder": ids["readout"]})
    assert text.startswith("error") and props(c, page)["properties"]["folders"] == [twins[1]]
    # A read names no folder into being.
    assert listed(folder(""), "Resolve / Nowhere").startswith('error: there is no folder "Resolve / Nowhere"')


def test_move_at_root_replaces_all_folders(org):
    c, ids = org
    run_agent_tool(ids["ws"], folder(""), "move_page", {"page_id": ids["b"], "folder": "archive / 2019"})
    assert props(c, ids["b"])["properties"]["folders"] == [make_folder(c, "archive/2019")]
    # Root + "" = out of every folder.
    _, action = run_agent_tool(ids["ws"], folder(""), "move_page", {"page_id": ids["b"], "folder": ""})
    assert "folders" not in props(c, ids["b"])["properties"]
    assert action["from"] == "archive / 2019" and action["to"] == ""
    # Moving it where it already is changes nothing, and says so.
    _, action = run_agent_tool(ids["ws"], folder(""), "move_page", {"page_id": ids["b"], "folder": ""})
    assert action["noop"] is True and not action.get("error")
    # Restore for later tests.
    run_agent_tool(ids["ws"], folder(""), "move_page", {"page_id": ids["b"], "folder": "readout"})


def test_unknown_or_out_of_scope_tools_error(org):
    c, ids = org
    text, action = run_agent_tool(ids["ws"], folder(""), "delete_page", {"page_id": "x"})
    assert text.startswith("error") and action["error"] and action["result"] == text
    text, action = run_agent_tool(ids["ws"], folder(""), "set_labels", {"page_id": "x"})
    assert text.startswith("error") and action["error"]
    # Write tools don't exist in page scope — same error as an unknown tool.
    text, _ = run_agent_tool(ids["ws"], {"type": "page", "page_id": "x"},
                             "rename_page", {"page_id": "x", "title": "y"})
    assert text.startswith("error: unknown tool")


def test_read_page_returns_notes_and_respects_scope(org):
    c, ids = org
    r = c.post("/api/blocks", json={"parent_id": ids["a"], "content": "important note"})
    assert r.status_code == 200
    text, action = run_agent_tool(ids["ws"], folder(ids["readout"]), "read_page", {"page_id": ids["a"]})
    assert action["kind"] == "read" and action["summary"].startswith("Read “")
    assert "important note" in text  # the user's notes ride along
    # A page outside the scope is unreadable, same rule as the write tools.
    text, _ = run_agent_tool(ids["ws"], folder(ids["readout"]), "read_page", {"page_id": ids["note"]})
    assert text.startswith("error")


def test_read_page_pdf_offset_pages_through_long_documents(org, monkeypatch):
    """A long paper is read in windows: pdf_offset starts the excerpt there and
    the excerpt names the next offset while more text remains."""
    c, ids = org
    doc = "".join(f"[{i:04d}]" for i in range(200))  # 1200 chars, self-locating

    def fake_extract(src, char_limit, empty_page_cap=50, start_page=1, label_pages=False):
        # Like the real extractor: stops after the "page" that crosses the
        # limit, so the result can overshoot char_limit a little.
        return (doc if len(doc) <= char_limit else doc[:char_limit + 7]), 1

    monkeypatch.setattr("gamma.ai_context.extract_text_pages", fake_extract)
    monkeypatch.setattr("gamma.ai_context.pdf_path", lambda u, d: "fake.pdf")
    scope = folder(ids["readout"])

    text, _ = run_agent_tool(ids["ws"], scope, "read_page",
                             {"page_id": ids["a"], "pdf_chars": 100})
    assert "[0000]" in text and "[0020]" not in text  # first window only
    assert "pdf_offset=100" in text  # continuation hint

    text, _ = run_agent_tool(ids["ws"], scope, "read_page",
                             {"page_id": ids["a"], "pdf_chars": 100, "pdf_offset": 100})
    assert "Document text (from char 100):" in text
    assert "[0017]" in text and "[0000]" not in text  # window slides
    assert "pdf_offset=200" in text

    # A window reaching the end has no continuation marker.
    text, _ = run_agent_tool(ids["ws"], scope, "read_page",
                             {"page_id": ids["a"], "pdf_chars": 20000})
    assert "[0199]" in text and "more text remains" not in text

    # An offset past the end reports the document's extracted length.
    text, _ = run_agent_tool(ids["ws"], scope, "read_page",
                             {"page_id": ids["a"], "pdf_offset": 5000})
    assert "past the end" in text and "1200" in text


def test_read_page_pdf_page_jumps_to_a_search_hit(org, monkeypatch):
    """pdf_page starts the excerpt at that PDF page (the shape search_library
    hits come in), so the agent can read around a match with a small window."""
    from gamma.pdf_text import extract_text

    c, ids = org
    pages = [f"(page {i}) " + f"p{i}-body " * 10 for i in range(1, 6)]
    monkeypatch.setattr(
        "gamma.pdf_text.iter_page_texts",
        lambda src, max_pages=None, start_page=1: iter(pages[start_page - 1:]))
    monkeypatch.setattr("gamma.ai_context.extract_text", extract_text)
    monkeypatch.setattr("gamma.ai_context.pdf_path", lambda u, d: "fake.pdf")
    scope = folder(ids["readout"])

    text, _ = run_agent_tool(ids["ws"], scope, "read_page",
                             {"page_id": ids["a"], "pdf_page": 4, "pdf_chars": 60})
    assert "Document text (from PDF page 4):" in text
    assert "(page 4)" in text and "(page 3)" not in text
    assert "pdf_page=4, pdf_offset=60" in text  # continuation keeps the page anchor

    # Continuing from that page with an offset labels and slices from there.
    text, _ = run_agent_tool(ids["ws"], scope, "read_page",
                             {"page_id": ids["a"], "pdf_page": 4, "pdf_offset": 60,
                              "pdf_chars": 20000})
    assert "from PDF page 4, from char 60" in text and "(page 5)" in text
    assert "more text remains" not in text  # pages 4-5 end inside the window

    # A page past the end of the document says so instead of going silent.
    text, _ = run_agent_tool(ids["ws"], scope, "read_page",
                             {"page_id": ids["a"], "pdf_page": 40})
    assert "no text at or after PDF page 40" in text


def test_read_page_never_repeats_what_the_context_holds(org, monkeypatch):
    """The chat's coverage report rides in the scope: PDF pages the head
    excerpt shows in full are skipped (a read continues from the page it
    cut short), a document the excerpt holds whole is not re-read, and the
    notes come once — with the first window, or on notes: true — never
    when the context already carries them."""
    from gamma.ai_tools import agent_system, coverage_lines
    from gamma.pdf_text import extract_text_pages

    c, ids = org
    c.post("/api/blocks", json={"parent_id": ids["a"], "content": "margin remark"})
    pages = [f"(page {i}) " + f"p{i}-body " * 10 for i in range(1, 6)]
    monkeypatch.setattr("gamma.pdf_text.iter_page_texts",
                        lambda src, max_pages=None, start_page=1: iter(pages[start_page - 1:]))
    monkeypatch.setattr("gamma.ai_context.extract_text_pages", extract_text_pages)
    monkeypatch.setattr("gamma.ai_context.pdf_path", lambda u, d: "fake.pdf")
    cover = {"page_id": ids["a"], "doc_id": "d" * 24, "title": "cavity paper", "native": False,
             "partial": True, "pages": 5, "pages_shown": 3, "notes": False}
    scope = {**folder(ids["readout"]), "coverage": [cover]}

    # A plain read starts where the excerpt stopped, with the notes (first window).
    text, chip = run_agent_tool(ids["ws"], scope, "read_page", {"page_id": ids["a"]})
    assert "pages 1–2" in text and "not repeated" in text and "continues from page 3" in text
    assert "(page 3)" in text and "(page 5)" in text and "(page 1)" not in text
    assert "margin remark" in text
    assert chip["pdf_pages"] == [3, 5] and chip["summary"].endswith("p. 3–5")
    # So does an explicit read of a page the excerpt holds.
    text, _ = run_agent_tool(ids["ws"], scope, "read_page", {"page_id": ids["a"], "pdf_page": 2})
    assert "continues from page 3" in text and "(page 2)" not in text
    # A page past the excerpt reads as asked — without the notes again…
    text, chip = run_agent_tool(ids["ws"], scope, "read_page",
                                {"page_id": ids["a"], "pdf_page": 4, "pdf_chars": 60})
    assert "(page 4)" in text and "not repeated" not in text
    assert "margin remark" not in text and "first window of a read" in text
    assert chip["pdf_pages"] == [4, 4] and chip["summary"].endswith("p. 4")
    # …unless asked for.
    text, _ = run_agent_tool(ids["ws"], scope, "read_page",
                             {"page_id": ids["a"], "pdf_page": 4, "pdf_chars": 60, "notes": True})
    assert "margin remark" in text
    # Notes already in the context: named, not repeated.
    text, _ = run_agent_tool(ids["ws"], {**scope, "coverage": [{**cover, "notes": True}]},
                             "read_page", {"page_id": ids["a"]})
    assert "margin remark" not in text and "notes and highlights are in the conversation context" in text
    # The whole document in context: nothing to read.
    text, chip = run_agent_tool(ids["ws"], {**scope, "coverage": [{**cover, "partial": False, "pages_shown": 5}]},
                                "read_page", {"page_id": ids["a"]})
    assert "whole PDF text" in text and "Document text" not in text and "pdf_pages" not in chip
    # A page the context doesn't hold reads as before.
    text, _ = run_agent_tool(ids["ws"], folder(ids["readout"]), "read_page", {"page_id": ids["a"]})
    assert "(page 1)" in text and "margin remark" in text and "not repeated" not in text
    # A page whose file went natively: the text reads as before, the notes are skipped.
    text, _ = run_agent_tool(ids["ws"], {**scope, "coverage": [{**cover, "native": True, "notes": True}]},
                             "read_page", {"page_id": ids["a"]})
    assert "(page 1)" in text and "margin remark" not in text
    # A note page in the context (its notes always go) is not repeated either.
    c.post("/api/blocks", json={"parent_id": ids["note"], "content": "loose thought"})
    text, _ = run_agent_tool(ids["ws"], {"type": "page", "page_id": ids["note"], "coverage": [
        {"page_id": ids["note"], "doc_id": "", "title": "loose note", "native": False,
         "partial": False, "pages": 0, "pages_shown": 0, "notes": True}]},
        "read_page", {"page_id": ids["note"]})
    assert "loose thought" not in text and "notes and highlights are in the conversation context" in text
    text, _ = run_agent_tool(ids["ws"], {"type": "page", "page_id": ids["note"]},
                             "read_page", {"page_id": ids["note"]})
    assert "loose thought" in text

    # The agent prompt names what the context holds and where to read on.
    lines = coverage_lines([cover], True)
    assert 'PDF pages 1–3 of 5 of "cavity paper"' in lines and "page 3 cut short" in lines
    assert "read_page(page_id, pdf_page=N)" in lines
    assert "read_page" not in coverage_lines([cover], False)
    assert "whole PDF text" in coverage_lines([{**cover, "partial": False}], True)
    assert coverage_lines([{**cover, "native": True}], True) == ""
    system = agent_system({"type": "page", "page_id": ids["a"], "coverage": [cover]})
    assert "page 3 cut short" in system and "answer from the text in the context when it is there" in system
    assert "even if you think you know it" not in system


def test_area_highlights_reach_the_model_as_pictures(org, monkeypatch):
    """An area highlight (a Ctrl+drag rectangle: no quote, pdf_position with
    area: true) has no text to show — read_page, read_block and the chat
    context name the rectangle and its page and attach a crop of the region,
    at most MAX_AREA_CROPS per page; the rest are named only."""
    from gamma import ai_context
    from gamma.ai_context import MAX_AREA_CROPS, area_highlight
    from gamma.routers.ai import AIChatRequest

    c, ids = org
    rendered = []

    def render(src, page_no, max_side, box=None):
        rendered.append((page_no, box))
        return (b"png", "image/png", 4, 4), 9

    monkeypatch.setattr(ai_context, "render_page", render)
    monkeypatch.setattr(ai_context, "pdf_path", lambda ws, doc: "fake.pdf")
    monkeypatch.setattr(ai_context, "extract_text_pages", lambda *a, **kw: ("(page 1) text", 1))
    monkeypatch.setattr(ai_context, "ensure_indexed", lambda *a: None)

    rect = {"x1": 80, "y1": 100, "x2": 400, "y2": 300}
    position = {"pageNumber": 2, "width": 800, "height": 1000, "boundingRect": rect, "rects": [rect]}
    props = {"quote": "", "pdf_position": {**position, "area": True}}
    assert area_highlight(props) == (2, (0.095, 0.095, 0.505, 0.305))
    assert area_highlight({"quote": "some text", "pdf_position": position}) is None  # a text highlight
    assert area_highlight({"quote": "", "pdf_position": {"pageNumber": 2}}) is None  # its page alone
    assert area_highlight({"quote": ""}) is None
    assert area_highlight({"ink_url": "/api/uploads/a.ink", "pdf_position": position}) is None  # ink
    page = c.post("/api/blocks", json={"parent_id": "root", "content": "figure notes",
                                       "properties": {"folders": [ids["readout"]], "doc_id": "a" * 24}}).json()["id"]
    ids_made = [c.post("/api/blocks", json={"parent_id": page, "content": f"box {n}" if n == 0 else "",
                                            "properties": props}).json()["id"]
                for n in range(MAX_AREA_CROPS + 1)]

    text, chip = run_agent_tool(ids["ws"], folder(ids["readout"]), "read_page", {"page_id": page})
    assert "Area highlight (a rectangle on PDF page 2; picture 1 attached)\n  User note: box 0" in text
    assert f"picture {MAX_AREA_CROPS} attached" in text and "no picture: more than the limit" in text
    assert len(chip["images"]) == MAX_AREA_CROPS and chip["images"][0][0] == "image/png"
    assert rendered[0] == (2, (0.095, 0.095, 0.505, 0.305)) and len(rendered) == MAX_AREA_CROPS

    rendered.clear()
    text, chip = run_agent_tool(ids["ws"], folder(ids["readout"]), "read_block", {"block_id": page})
    assert f"[{ids_made[0]}] (area highlight: a rectangle on PDF page 2; picture 1 attached) box 0" in text
    assert "no picture: more than the limit" in text
    assert len(chip["images"]) == MAX_AREA_CROPS
    # A single block read carries its own picture.
    text, chip = run_agent_tool(ids["ws"], folder(ids["readout"]), "read_block", {"block_id": ids_made[1]})
    assert "picture 1 attached" in text and len(chip["images"]) == 1

    # The chat context: the pictures ride with the message's images, and
    # the coverage says how many went.
    rendered.clear()
    crops = []
    payload = AIChatRequest(prompt="what is in the boxes?", page_id=page, include_notes=True)
    _, context, coverage, _ = ai_context.gather_inputs(ids["ws"], payload, False, crops=crops)
    assert "Area highlight (a rectangle on PDF page 2; picture 1 attached)" in context
    assert len(crops) == MAX_AREA_CROPS and coverage[0]["area_pictures"] == MAX_AREA_CROPS
    # Notes left out of the context: no pictures either.
    crops = []
    payload = AIChatRequest(prompt="hi", page_id=page, include_notes=False)
    _, context, coverage, _ = ai_context.gather_inputs(ids["ws"], payload, False, crops=crops)
    assert "Area highlight" not in context and not crops and "area_pictures" not in coverage[0]
    c.delete(f"/api/blocks/{page}")


def test_text_boxes_are_notes_never_area_highlights(org, monkeypatch):
    """A text box has no quote, like an area highlight, but is a note:
    area_highlight never takes it for a rectangle (not even with a stray
    position), and read_page and the chat context list it with the notes,
    labelled with its page, with no picture."""
    from gamma import ai_context
    from gamma.ai_context import area_highlight
    from gamma.routers.ai import AIChatRequest

    c, ids = org
    rendered = []
    monkeypatch.setattr(ai_context, "render_page", lambda *a, **kw: rendered.append(a))
    monkeypatch.setattr(ai_context, "pdf_path", lambda ws, doc: "fake.pdf")
    monkeypatch.setattr(ai_context, "extract_text_pages", lambda *a, **kw: ("(page 1) text", 1))
    monkeypatch.setattr(ai_context, "ensure_indexed", lambda *a: None)

    rect = {"x1": 80, "y1": 100, "x2": 400, "y2": 300}
    box = {"text_box": {"x": 40, "y": 60, "w": 180}, "pdf_page": 2}
    stray = {**box, "pdf_position": {"pageNumber": 2, "width": 800, "height": 1000, "boundingRect": rect,
                                     "rects": [rect], "area": True}}
    assert area_highlight(box) is None and area_highlight(stray) is None
    page = c.post("/api/blocks", json={"parent_id": "root", "content": "boxed paper",
                                       "properties": {"folders": [ids["readout"]], "doc_id": "b" * 24}}).json()["id"]
    c.post("/api/blocks", json={"parent_id": page, "content": "typed on the figure", "properties": stray})
    # Under a sheet the sheet holds a box whatever its pdf_page says; with neither it is on no page.
    sheet = c.post("/api/blocks", json={"parent_id": page, "content": "",
                                        "properties": {"sheet": {}}}).json()["id"]
    c.post("/api/blocks", json={"parent_id": sheet, "content": "moved onto the sheet", "properties": box})
    c.post("/api/blocks", json={"parent_id": page, "content": "on no page", "properties": {"text_box": {}}})

    text, chip = run_agent_tool(ids["ws"], folder(ids["readout"]), "read_page", {"page_id": page})
    assert "User's notes:\n- (text box on p. 2) typed on the figure" in text
    assert "  - (text box on a page of paper) moved onto the sheet" in text
    assert "- (text box, not placed on a page) on no page" in text
    assert "Area highlight" not in text and not chip.get("images") and not rendered

    payload = AIChatRequest(prompt="what did I write?", page_id=page, include_notes=True)
    crops = []
    _, context, coverage, _ = ai_context.gather_inputs(ids["ws"], payload, False, crops=crops)
    assert "- (text box on p. 2) typed on the figure" in context
    assert "Area highlight" not in context and not crops and "area_pictures" not in coverage[0]
    c.delete(f"/api/blocks/{page}")


def test_document_map_starts_after_the_excerpt(org):
    """The map lists the pages the excerpt doesn't show in full — the
    model picks the next page to read from it, not one it already has."""
    from gamma.ai_context import document_map
    from gamma.db import connect_data_db
    from gamma.pdf_index import store_doc

    _, ids = org
    doc = "f" * 24
    # stored the way the indexer stores a paper (the rowid side table too)
    with connect_data_db(ids["ws"]) as db:
        store_doc(db, doc, [(n, f"Section {n} opens here with words") for n in range(1, 31)])
    whole = document_map(ids["ws"], doc)
    assert "30-page PDF" in whole and "  p.1: Section 1" in whole and "from page" not in whole
    later = document_map(ids["ws"], doc, from_page=10)
    assert "from page 10" in later and "  p.10: Section 10" in later and "p.9:" not in later
    assert document_map(ids["ws"], doc, from_page=31) == ""
    # A tight budget samples every nth page (rounded up, so it stays under).
    sampled = document_map(ids["ws"], doc, budget=600)
    assert len(sampled) < 600 + 200 and "every " in sampled


def test_read_window_cap_is_user_tunable(org, monkeypatch):
    """The scope's read_chars (the Settings "Read window" preference) caps
    pdf_chars per call, and the armed spec advertises the effective cap."""
    c, ids = org
    doc = "".join(f"[{i:04d}]" for i in range(200))
    monkeypatch.setattr("gamma.ai_context.extract_text_pages",
                        lambda src, char_limit, empty_page_cap=50, start_page=1, label_pages=False: (doc[:char_limit + 7], 1))
    monkeypatch.setattr("gamma.ai_context.pdf_path", lambda u, d: "fake.pdf")

    scope = {**folder(ids["readout"]), "read_chars": 150}
    text, _ = run_agent_tool(ids["ws"], scope, "read_page",
                             {"page_id": ids["a"], "pdf_chars": 99999})
    assert "[0020]" in text and "[0030]" not in text  # clamped to ~150 chars
    assert "pdf_offset=150" in text
    # Unset / absurd values fall back to the stock 20000 cap.
    for bad in ({}, {"read_chars": 0}, {"read_chars": "x"}, {"read_chars": 10**9}):
        text, _ = run_agent_tool(ids["ws"], {**folder(ids["readout"]), **bad},
                                 "read_page", {"page_id": ids["a"], "pdf_chars": 99999})
        assert "[0199]" in text  # the whole 1200-char doc fits under 20000

    # The armed tool spec names the effective cap (and a default under it).
    spec = next(t for t in agent_tools("folder", read_chars=50000) if t["name"] == "read_page")
    assert "up to 50000" in spec["description"] and "default 6000" in spec["description"]
    spec = next(t for t in agent_tools("folder", read_chars=1500) if t["name"] == "read_page")
    assert "up to 1500" in spec["description"] and "default 1500" in spec["description"]
    spec = next(t for t in agent_tools("folder") if t["name"] == "read_page")
    assert "up to 20000" in spec["description"]
    assert "{read_cap}" not in spec["description"]  # template never leaks


def test_list_pages_filters_and_labels_mode(org):
    c, ids = org

    def page(content, props):
        r = c.post("/api/blocks", json={"parent_id": "root", "content": content, "properties": props})
        assert r.status_code == 200, r.text
        return r.json()["id"]

    labtest = make_folder(c, "labtest")
    jeff1 = page("erasure paper", {"folders": [labtest], "labels": [make_label(c, "Jeff"), make_label(c, "Yb")]})
    jeff2 = page("tweezer gates", {"folders": [make_folder(c, "labtest/sub")], "labels": [make_label(c, "jeff")]})
    other = page("ldpc paper", {"folders": [labtest], "labels": [make_label(c, "qec")]})
    make_label(c, "unused")
    # A label's name: exactly, else ignoring case; only matching pages come back.
    text, action = run_agent_tool(ids["ws"], folder(labtest), "list_pages", {"label": "jeff"})
    assert "1 page" in action["summary"] and "jeff" in action["summary"]
    assert jeff2 in text and jeff1 not in text
    text, action = run_agent_tool(ids["ws"], folder(labtest), "list_pages", {"label": "JEFF"})
    assert "2 pages" in action["summary"]
    assert jeff1 in text and jeff2 in text and other not in text
    assert 'labels=["Jeff", "Yb"]' in text and 'folders=["labtest / sub"]' in text
    # Title substring filter.
    text, _ = run_agent_tool(ids["ws"], folder(labtest), "list_pages",
                             {"title_contains": "LDPC"})
    assert other in text and jeff1 not in text
    # Relative subfolder filter resolves inside the scope.
    text, _ = run_agent_tool(ids["ws"], folder(labtest), "list_pages", {"folder": "sub"})
    assert jeff2 in text and jeff1 not in text
    # No matches is a clear answer, not an empty-library claim.
    text, _ = run_agent_tool(ids["ws"], folder(labtest), "list_pages", {"label": "nope"})
    assert "No pages match" in text
    # Labels mode: the vocabulary with counts, not page lines — a folder's
    # labels are its pages', the library's every label, unused ones too.
    text, action = run_agent_tool(ids["ws"], folder(labtest), "list_pages",
                                  {"list_labels": True})
    assert "labels" in action["summary"]
    assert '- label "Jeff": 1 page' in text and '- label "jeff": 1 page' in text
    assert '- label "qec": 1 page' in text and "folder" not in text  # folders: list_folders
    assert jeff1 not in text and "unused" not in text
    text, _ = run_agent_tool(ids["ws"], folder(""), "list_pages", {"list_labels": True})
    assert '- label "unused": 0 pages' in text and '- label "Yb": 1 page' in text


def test_list_folders_shows_the_tree_with_counts(org):
    """list_folders: every folder — an empty one too — with its id and full
    path, subfolders indented under their folder, direct and total page
    counts, the loose pages at the root — and a folder chat or `folder`
    sees only its subtree."""
    c, ids = org
    tree, a, b, cc = (make_folder(c, path) for path in ("tree", "tree/a", "tree/a/b", "tree/c"))
    empty = make_folder(c, "tree/empty")
    for title, where in (("tree top", [tree]), ("deep one", [b]), ("deep two", [b, cc])):
        r = c.post("/api/blocks", json={"parent_id": "root", "content": title, "properties": {"folders": where}})
        assert r.status_code == 200, r.text
    text, action = run_agent_tool(ids["ws"], folder(""), "list_folders", {})
    assert action["kind"] == "list" and "folders in the library" in action["summary"]
    lines = text.splitlines()
    # A page in two subfolders counts once above them.
    assert f'- [{tree}] "tree" (1 here, 3 with subfolders)' in lines
    assert f'  - [{a}] "tree / a" (0 here, 2 with subfolders)' in lines
    assert f'    - [{b}] "tree / a / b" (2 pages)' in lines
    assert f'  - [{cc}] "tree / c" (1 page)' in lines
    assert f'  - [{empty}] "tree / empty" (no pages)' in lines
    assert "in no folder." in text.splitlines()[-2]  # the fixture's loose note
    assert 'list_pages(folder="<path or id>")' in text
    # Scoped to a folder: only its subtree, relative `folder` resolved inside it.
    text, _ = run_agent_tool(ids["ws"], folder(tree), "list_folders", {"folder": "a"})
    assert f'- [{a}] "tree / a" (0 here, 2 with subfolders)' in text.splitlines()
    assert '"tree / c"' not in text and '"readout"' not in text and "no folder" not in text
    text, _ = run_agent_tool(ids["ws"], folder(cc), "list_folders", {})
    assert f'- [{cc}] "tree / c" (1 page)' in text.splitlines()
    text, _ = run_agent_tool(ids["ws"], folder(""), "list_folders", {"folder": "nowhere"})
    assert text.startswith('error: there is no folder "nowhere"')
    # Paper chats have no folder tools.
    text, action = run_agent_tool(ids["ws"], {"type": "page", "page_id": ids["a"]}, "list_folders", {})
    assert action["error"] and "unknown tool" in text


def _blank_pdf(path, pages=2):
    from PyPDF2 import PdfWriter
    w = PdfWriter()
    for _ in range(pages):
        w.add_blank_page(width=200, height=300)
    with open(path, "wb") as f:
        w.write(f)
    return path


def test_view_pdf_page_renders_a_picture_for_the_model(org, monkeypatch, tmp_path):
    """view_pdf_page rasterizes one page through pdfium: the picture rides
    on the chip as `images` (the loop moves it to the tool result), the text
    names the page and the count, and bad pages/pages without a PDF refuse."""
    c, ids = org
    pdf = _blank_pdf(tmp_path / "two.pdf")
    monkeypatch.setattr("gamma.ai_tools.pdf_path", lambda ws, doc: pdf)
    text, action = run_agent_tool(ids["ws"], folder(ids["readout"]), "view_pdf_page",
                                  {"page_id": ids["a"], "pdf_page": 2})
    assert text.startswith("PDF page 2 of 2"), text
    assert action["kind"] == "view" and action["pdf_page"] == 2 and action["page_id"] == ids["a"]
    (media_type, data), = action["images"]
    assert media_type in ("image/png", "image/jpeg") and len(data) > 100
    assert "×" in text and "not kept" in text  # dimensions named; replay warning
    # Out of range: the count tells the model how far it may look.
    text, action = run_agent_tool(ids["ws"], folder(ids["readout"]), "view_pdf_page",
                                  {"page_id": ids["a"], "pdf_page": 3})
    assert text.startswith("error") and "has 2 pages" in text and action["error"]
    assert "images" not in action
    # A page without an attachment has nothing to draw.
    text, action = run_agent_tool(ids["ws"], folder(ids["readout"]), "view_pdf_page",
                                  {"page_id": ids["b"], "pdf_page": 1})
    assert "no PDF attachment" in text and not action.get("error")
    # Permission off: refused before any rendering.
    text, action = run_agent_tool(ids["ws"], folder(ids["readout"]), "view_pdf_page",
                                  {"page_id": ids["a"], "pdf_page": 1},
                                  allowed_tools={"read_page"})
    assert text.startswith("error") and "not enabled" in text
