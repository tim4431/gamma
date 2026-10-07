"""Pictures in the AI chat (gamma/ai_pictures.py, docs/dev/ai.md
"Pictures"): one size for every picture, the chat's picture store and the
picture routes, a request's pictures and their budget, the earlier turns'
pictures kept in the conversation, the label lines, the view_image /
clip_region / delete_block tools, and the upload GC counting a chat's
pictures as references."""

import base64
import io
import json
from types import SimpleNamespace

import pytest
from PIL import Image

from gamma import ai_pictures, ink as inkmod, upload_gc
from gamma.ai_context import build_messages
from gamma.ai_protocols import WIRES
from gamma.ai_revert import RevertError, revert_change
from gamma.ai_tools import approval_preview, run_agent_tool
from gamma.db import connect_pages_db

from ai_fixtures import ALLOW_ALL, CONF, FakeResp, ai_provider, children, folder, org, props  # noqa: F401  (fixtures)

PAGE_W, PAGE_H = 300.0, 400.0


@pytest.fixture(scope="module", autouse=True)
def _provider(ai_provider):
    """The chat tests need a provider entry on the module's account."""


@pytest.fixture(autouse=True)
def _offline_model_facts(monkeypatch):
    monkeypatch.setattr("gamma.ai_catalog.context_window", lambda *args: (0, ""))
    monkeypatch.setattr("gamma.ai_catalog.image_input", lambda *args: (None, ""))


def _png(width, height, color="white", alpha=None, fmt="PNG"):
    image = Image.new("RGBA" if alpha is not None else "RGB", (width, height), color)
    if alpha is not None:
        image.putpixel((0, 0), (255, 0, 0, alpha))
    out = io.BytesIO()
    image.save(out, fmt)
    return out.getvalue()


def _data_url(data, media="image/png"):
    return f"data:{media};base64," + base64.b64encode(data).decode()


def _ink(points=((40, 60), (260, 340))):
    return {"format": "gamma-ink", "version": 1,
            "space": {"kind": "pdf-page", "page": 1, "width": PAGE_W, "height": PAGE_H},
            "strokes": [{"id": "s1", "color": "#ff0000", "size": 6, "ch": "xy",
                         "pts": inkmod.encode_points([{"x": x, "y": y} for x, y in points], "xy")}]}


def _pdf(path, pages=2):
    from PyPDF2 import PdfWriter
    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=PAGE_W, height=PAGE_H)
    with open(path, "wb") as f:
        writer.write(f)
    return path


@pytest.fixture(scope="module")
def pictured(org, tmp_path_factory):
    """A paper page (a two-page blank PDF, patched in per test) carrying a
    handwriting group, a note that embeds a stored picture, and a plain
    note; plus the picture's URL and the PDF's path."""
    c, ids = org

    def block(parent, content, props=None):
        r = c.post("/api/blocks", json={"parent_id": parent, "content": content, "properties": props or {}})
        assert r.status_code == 200, r.text
        return r.json()["id"]

    paper = block("root", "figures paper", {"folders": [ids["readout"]], "doc_id": "f" * 24})
    r = c.post("/api/upload-ink", json=_ink())
    assert r.status_code == 200, r.text
    drawn = r.json()
    group = block(paper, "circled a figure", {"ink_url": drawn["url"], "pdf_position": drawn["pdf_position"],
                                              "ink_strokes": 1})
    r = c.post("/api/ai/pictures", files={"file": ("fig.png", _png(900, 600, "navy"), "image/png")})
    assert r.status_code == 200, r.text
    stored = r.json()
    figure = block(paper, f"The setup, see ![the setup|300]({stored['url']}) and the text around it")
    plain = block(paper, "a typed note")
    pdf = _pdf(tmp_path_factory.mktemp("pictures") / "figures.pdf")
    return c, {**ids, "paper": paper, "group": group, "figure": figure, "plain": plain,
               "picture": stored, "pdf": pdf}


@pytest.fixture
def with_pdf(pictured, monkeypatch):
    _, ids = pictured
    monkeypatch.setattr("gamma.ai_context.pdf_path", lambda ws, doc: ids["pdf"] if doc == "f" * 24 else None)
    return pictured


# --- one size ---------------------------------------------------------------------------

def test_normalize_sizes_and_encodes_every_picture():
    data, media, width, height = ai_pictures.normalize(_png(3000, 1500, "gray"))
    assert (media, width, height) == ("image/jpeg", 1568, 784)
    assert Image.open(io.BytesIO(data)).size == (1568, 784)
    # A translucent picture stays PNG, and so does a small crisp one.
    _, media, *_ = ai_pictures.normalize(_png(2000, 100, "white", alpha=10))
    assert media == "image/png"
    _, media, width, _ = ai_pictures.normalize(_png(200, 100, "white"))
    assert (media, width) == ("image/png", 200)
    # A big JPEG photo stays JPEG; junk is no picture.
    _, media, *_ = ai_pictures.normalize(_png(1600, 1600, "white", fmt="JPEG"), "image/jpeg")
    assert media == "image/jpeg"
    assert ai_pictures.normalize(b"%PDF-1.4 not a picture") is None
    assert ai_pictures.normalize(b"") is None


def test_picture_store_route(org):
    c, ids = org
    r = c.post("/api/ai/pictures", files={"file": ("shot.png", _png(2500, 1000, "teal"), "image/png")})
    assert r.status_code == 200, r.text
    first = r.json()
    assert first["url"].startswith("/api/uploads/") and first["url"].endswith(".jpg")
    assert (first["width"], first["height"]) == (1568, 627) and not first["already_existed"]
    assert c.get(first["url"]).status_code == 200
    again = c.post("/api/ai/pictures", files={"file": ("shot2.png", _png(2500, 1000, "teal"), "image/png")}).json()
    assert again["url"] == first["url"] and again["already_existed"]
    r = c.post("/api/ai/pictures", files={"file": ("x.png", b"not a picture", "image/png")})
    assert r.status_code == 400


def test_page_image_route_draws_a_page_a_region_and_the_handwriting(with_pdf):
    c, ids = with_pdf
    r = c.get(f"/api/ai/page-image/{ids['paper']}", params={"page": 1})
    assert r.status_code == 200 and r.headers["content-type"].startswith("image/")
    assert "max-age=86400" in r.headers["cache-control"]
    page = Image.open(io.BytesIO(r.content))
    assert max(page.size) == 1568
    r = c.get(f"/api/ai/page-image/{ids['paper']}", params={"page": 2, "box": "0.1,0.1,0.5,0.3"})
    assert r.status_code == 200
    region = Image.open(io.BytesIO(r.content))
    assert region.size[0] > region.size[1]  # a wide box
    # With the handwriting: the stroke runs corner to corner through the middle.
    r = c.get(f"/api/ai/page-image/{ids['paper']}", params={"page": 1, "ink": 1})
    assert r.status_code == 200 and "max-age=60" in r.headers["cache-control"]
    inked = Image.open(io.BytesIO(r.content)).convert("RGB")
    middle = inked.getpixel((inked.width // 2, inked.height // 2))
    assert middle[0] > 180 and middle[1] < 100, middle  # red ink
    assert c.get(f"/api/ai/page-image/{ids['paper']}", params={"page": 9}).status_code == 404
    assert c.get(f"/api/ai/page-image/{ids['paper']}", params={"page": 1, "box": "0.9,0,0.1,1"}).status_code == 400
    assert c.get(f"/api/ai/page-image/{ids['note']}", params={"page": 1}).status_code == 404  # no PDF
    assert c.get("/api/ai/page-image/nope", params={"page": 1}).status_code == 404


def test_ink_image_route(with_pdf):
    c, ids = with_pdf
    r = c.get(f"/api/ai/ink-image/{ids['group']}")
    assert r.status_code == 200 and r.headers["content-type"].startswith("image/")
    assert c.get(f"/api/ai/ink-image/{ids['group']}", params={"area": "page"}).status_code == 200
    assert c.get(f"/api/ai/ink-image/{ids['plain']}").status_code == 404
    assert c.get("/api/ai/ink-image/nope").status_code == 404


# --- a request's pictures -----------------------------------------------------------

def test_request_pictures_are_resolved_and_labelled(with_pdf):
    c, ids = with_pdf
    url = ids["picture"]["url"]
    with connect_pages_db(ids["ws"]) as conn:
        shown = ai_pictures.request_pictures(ids["ws"], conn, [
            {"kind": "pasted", "url": url, "width": 1, "height": 1},
            {"kind": "file", "url": url, "name": "setup.png"},
            {"kind": "area", "page_id": ids["paper"], "page": 1, "box": [0.1, 0.1, 0.6, 0.4]},
            {"kind": "view", "page_id": ids["paper"], "page": 2, "box": [0, 0, 1, 0.5], "ink": True},
            _data_url(_png(400, 300, "olive")),
            {"kind": "pasted", "url": "/api/uploads/" + "0" * 24 + ".png"},   # no such file
            {"kind": "area", "page_id": ids["paper"], "page": 1, "box": [2, 0, 1, 1]},  # a bad box
            {"kind": "area", "page_id": ids["note"], "page": 1},               # a page without a PDF
            "junk", 7,
        ], budget=12)
    labels = [p["label"] for p in shown]
    assert labels[0] == f"Pasted image, 900×600 px, stored at {url} — to put it in a note, write ![…]({url})"
    assert labels[1].startswith("Image file “setup.png”, 900×600 px, stored at")
    assert labels[2].startswith("A region the user marked on PDF page 1 of “figures paper”")
    assert labels[3].startswith("What the user sees: PDF page 2 of “figures paper”, the visible part, with their handwriting on it")
    assert labels[4] == "Pasted image, 400×300 px"
    assert len(shown) == 5 and all(p["group"] == "user" for p in shown)
    assert shown[0]["url"] == url and shown[2]["page_id"] == ids["paper"] and shown[2]["page"] == 1


def test_fit_fills_the_budget_in_order_and_names_what_was_left_out():
    def pics(group, n):
        return [{"part": ("image/png", "QUJD"), "label": f"{group} {i}", "group": group} for i in range(n)]
    groups = [("user", pics("user", 2)), ("selection", pics("selection", 2)), ("ink", pics("ink", 1)),
              ("area", pics("area", 3))]
    kept, left_out = ai_pictures.fit(groups, 4)
    assert [p["label"] for p in kept] == ["user 0", "user 1", "selection 0", "selection 1"]
    assert left_out == {"ink": 1, "area": 3}
    lines = ai_pictures.label_lines(kept, left_out, 4)
    assert lines.splitlines()[0] == "Pictures attached to this message, in order:"
    assert "1. user 0." in lines and "4. selection 1." in lines
    assert lines.endswith("Left out, over the budget of 4 pictures per message: 1 picture of attached handwriting, "
                          "3 pictures of an area highlight.")
    assert ai_pictures.label_lines([], {}) == ""
    assert ai_pictures.fit([], 3) == ([], {})
    assert ai_pictures.budget_of("x") == ai_pictures.DEFAULT_BUDGET and ai_pictures.budget_of(999) == ai_pictures.MAX_BUDGET


def test_earlier_pictures_stay_in_the_conversation_newest_first(pictured):
    c, ids = pictured
    url = ids["picture"]["url"]
    history = [
        {"role": "user", "text": "first", "images": [{"kind": "pasted", "url": url}, {"kind": "pasted", "url": url}]},
        {"role": "ai", "text": "noted"},
        {"role": "user", "text": "second", "images": ["data:image/png;base64,AAAA", {"kind": "pasted", "url": url}]},
        {"role": "ai", "text": "and that"},
        {"role": "user", "text": "third, no picture"},
    ]
    with connect_pages_db(ids["ws"]) as conn:
        kept = ai_pictures.history_pictures(ids["ws"], conn, history, budget=2)
    assert kept == 2
    # The newest message keeps its picture (a data URL is never re-sent);
    # the oldest gets one of its two and says the other is not shown again.
    assert len(history[2]["pictures_sent"]["images"]) == 1
    assert history[2]["pictures_sent"]["lines"].startswith("Pictures attached to this message, in order:\n1. Pasted image")
    assert len(history[0]["pictures_sent"]["images"]) == 1
    assert history[0]["pictures_sent"]["lines"].endswith("(1 picture attached to this message is not shown again.)")
    assert "pictures_sent" not in history[4]
    payload = SimpleNamespace(history=history, prompt="now?", selection="", selections=[], note_selections=[])
    messages = build_messages(payload, "")
    assert messages[0]["role"] == "user" and len(messages[0]["images"]) == 1
    assert messages[0]["content"].startswith("first\n\nPictures attached to this message")
    assert "images" not in messages[4] and messages[4]["content"] == "third, no picture"
    # Every wire puts an earlier turn's pictures on its own turn.
    body = json.loads(WIRES["anthropic"].request(CONF, [dict(m) for m in messages], "sys", "m").data)
    assert [b["type"] for b in body["messages"][0]["content"]] == ["image", "text"]
    assert body["messages"][4]["content"] == "third, no picture"
    body = json.loads(WIRES["openai"].request(CONF, [dict(m) for m in messages], "sys", "m").data)
    assert [b["type"] for b in body["messages"][1]["content"]] == ["image_url", "text"]
    body = json.loads(WIRES["openai-responses"].request(CONF, [dict(m) for m in messages], "sys", "m").data)
    assert [b["type"] for b in body["input"][0]["content"]] == ["input_image", "input_text"]


def test_chat_sends_the_pictures_with_their_labels(with_pdf, monkeypatch):
    c, ids = with_pdf
    import gamma.routers.ai as ai_mod

    seen = {}

    def fake_open(messages, system, entry, rt, pdf_b64s=None, **kw):
        seen.update(kw, messages=[dict(m) for m in messages])
        return FakeResp([{"type": "content_block_delta", "delta": {"type": "text_delta", "text": "ok"}}])

    monkeypatch.setattr(ai_mod, "_open_ai", fake_open)
    url = ids["picture"]["url"]
    body = {"prompt": "what is in these?", "page_id": ids["paper"], "stream": True,
            "images": [{"kind": "pasted", "url": url},
                       {"kind": "area", "page_id": ids["paper"], "page": 1, "box": [0.1, 0.1, 0.9, 0.5]}],
            "history": [{"role": "user", "text": "earlier", "images": [{"kind": "pasted", "url": url}]},
                        {"role": "ai", "text": "noted"}]}
    r = c.post("/api/ai/chat", json=body)
    assert r.status_code == 200, r.text
    assert len(seen["images"]) == 2
    last = seen["messages"][-1]["content"]
    assert "Pictures attached to this message, in order:\n1. Pasted image, 900×600 px, stored at" in last
    assert "2. A region the user marked on PDF page 1" in last
    assert len(seen["messages"][0]["images"]) == 1 and "1. Pasted image" in seen["messages"][0]["content"]
    # The budget: one picture per message, the rest named as left out. The
    # earlier turns keep the newest pictures under a budget of their own.
    r = c.post("/api/ai/chat", json={**body, "max_pictures": 1})
    assert r.status_code == 200, r.text
    assert len(seen["images"]) == 1
    assert "Left out, over the budget of 1 pictures per message: 1 attached picture." in seen["messages"][-1]["content"]
    assert len(seen["messages"][0]["images"]) == 1


# --- the tools ---------------------------------------------------------------------------

def test_view_image_shows_a_notes_pictures(pictured):
    c, ids = pictured
    scope = folder(ids["readout"])
    text, action = run_agent_tool(ids["ws"], scope, "view_image", {"block_id": ids["figure"]})
    assert text.startswith(f'Pictures embedded in block [{ids["figure"]}] of "figures paper":'), text
    assert 'Picture 1 of 1, alt text "the setup", 900×600 px' in text and "attached." in text
    assert action["kind"] == "image" and action["block_id"] == ids["figure"] and action["picture"] == ids["picture"]["url"]
    (media, data), = action["images"]
    assert media.startswith("image/") and len(data) > 100
    assert action["summary"] == "Looked at 1 picture in a note of “figures paper”"
    text, action = run_agent_tool(ids["ws"], scope, "view_image", {"block_id": ids["figure"], "index": 3})
    assert text.startswith("error") and "index must be 1–1" in text
    text, action = run_agent_tool(ids["ws"], scope, "view_image", {"block_id": ids["plain"]})
    assert "embeds no picture" in text and action["error"]
    text, _ = run_agent_tool(ids["ws"], folder(ids["cooling"]), "view_image", {"block_id": ids["figure"]})
    assert "outside" in text
    # A model that reads text only is not offered it (PICTURE_TOOLS).
    from gamma.ai_tools import PICTURE_TOOLS
    assert "view_image" in PICTURE_TOOLS


def test_clip_region_stores_a_picture_the_notes_can_embed(with_pdf):
    c, ids = with_pdf
    scope = folder(ids["readout"])
    text, action = run_agent_tool(ids["ws"], scope, "clip_region",
                                  {"page_id": ids["paper"], "pdf_page": 2, "box": [0.1, 0.1, 0.6, 0.3]})
    assert text.startswith("ok — stored a"), text
    url = action["url"]
    assert url.startswith("/api/uploads/") and action["kind"] == "clip" and action["picture"] == url
    assert action["pdf_page"] == 2 and f"![<a short caption>]({url})" in text
    assert c.get(url).status_code == 200
    # The preview shows the region from the same URL the chat renders, without storing anything.
    preview, answer = approval_preview(ids["ws"], scope, "clip_region", {"page_id": ids["paper"], "pdf_page": 1, "ink": True})
    assert answer is None and preview["picture"] == f"/api/ai/page-image/{ids['paper']}?page=1&ink=1"
    assert preview["what"] == "PDF page 1 with the handwriting on it"
    # Handwriting clips too.
    text, action = run_agent_tool(ids["ws"], scope, "clip_region", {"block_id": ids["group"]})
    assert text.startswith("ok") and action["block_id"] == ids["group"]
    # The stored picture can go into a note; an invented URL cannot.
    text, action = run_agent_tool(ids["ws"], scope, "create_block",
                                  {"parent_id": ids["paper"], "content": f"![the region]({url})"})
    assert text.startswith("ok"), text
    preview, _ = approval_preview(ids["ws"], scope, "create_block",
                                  {"parent_id": ids["paper"], "content": f"a figure\n\n![fig]({url})"})
    assert preview["pictures"] == [url]
    fake = "/api/uploads/" + "1" * 24 + ".png"
    text, action = run_agent_tool(ids["ws"], scope, "create_block",
                                  {"parent_id": ids["paper"], "content": f"![nope]({fake})"})
    assert text.startswith("error: no such stored file: " + fake) and action["error"]
    text, _ = run_agent_tool(ids["ws"], scope, "edit_block",
                             {"block_id": ids["plain"], "mode": "append", "content": f"see ![x]({fake})"})
    assert text.startswith("error: no such stored file")
    assert props(c, ids["plain"])["content"] == "a typed note"
    # Refusals.
    text, _ = run_agent_tool(ids["ws"], scope, "clip_region", {"page_id": ids["paper"]})
    assert "pdf_page" in text
    text, _ = run_agent_tool(ids["ws"], scope, "clip_region", {"page_id": ids["paper"], "pdf_page": 9})
    assert "does not exist" in text
    text, _ = run_agent_tool(ids["ws"], scope, "clip_region", {"page_id": ids["paper"], "pdf_page": 1, "box": [1, 0, 0, 1]})
    assert "box must be" in text
    text, _ = run_agent_tool(ids["ws"], scope, "clip_region", {"block_id": ids["plain"]})
    assert "holds no handwriting" in text
    text, _ = run_agent_tool(ids["ws"], scope, "clip_region", {"page_id": ids["b"], "pdf_page": 1})
    assert "no PDF attachment" in text


def test_delete_block_takes_a_subtree_and_reverts_it(org):
    c, ids = org
    scope = folder(ids["readout"])

    def block(parent, content):
        r = c.post("/api/blocks", json={"parent_id": parent, "content": content, "properties": {}})
        assert r.status_code == 200, r.text
        return r.json()["id"]

    page = block("root", "delete playground")
    assert c.put(f"/api/blocks/{page}", json={"properties": {"folders": [ids["readout"]]}}).status_code == 200
    keep = block(page, "stays")
    top = block(page, "goes")
    kid = block(top, "goes too")
    grandkid = block(kid, "and this")
    preview, answer = approval_preview(ids["ws"], scope, "delete_block", {"block_id": top})
    assert answer is None
    assert preview["children"] == 2 and preview["what"] == "note" and preview["diff"] == [["del", "goes"]]
    text, action = run_agent_tool(ids["ws"], scope, "delete_block", {"block_id": top})
    assert text == f"ok — block [{top}] deleted with its 2 sub-block(s)", text
    assert action["kind"] == "delete" and action["block_id"] == top and action["page_id"] == page
    assert action["summary"] == "Deleted a note in “delete playground” with 2 sub-blocks"
    record = action["revert"]
    assert [r[0] for r in record["blocks"]] == [top, kid, grandkid]
    assert children(c, page) == [keep]
    assert c.get(f"/api/blocks/{grandkid}").status_code == 404
    # The revert puts the whole subtree back where it was, then a redo takes it again.
    out = revert_change(ids["ws"], "delete", top, record)
    assert out == {"page_id": page, "noop": False}
    assert children(c, page) == [keep, top] and children(c, top) == [kid] and children(c, kid) == [grandkid]
    assert props(c, grandkid)["content"] == "and this"
    assert revert_change(ids["ws"], "delete", top, record)["noop"]  # back already
    out = revert_change(ids["ws"], "delete", top, record, redo=True)
    assert out["noop"] is False and children(c, page) == [keep]
    assert revert_change(ids["ws"], "delete", top, record, redo=True)["noop"]
    # Its parent gone, it cannot come back; a malformed record is refused.
    run_agent_tool(ids["ws"], scope, "delete_block", {"block_id": keep})
    with pytest.raises(RevertError) as refused:
        revert_change(ids["ws"], "delete", "x", {"blocks": [["y", page, "a0", "", "{}"]]})
    assert refused.value.status == 400
    # Pages are not the agent's to delete; out of scope is out of scope.
    text, action = run_agent_tool(ids["ws"], scope, "delete_block", {"block_id": page})
    assert "cannot delete pages" in text and action["error"]
    text, _ = run_agent_tool(ids["ws"], folder(ids["cooling"]), "delete_block", {"block_id": top})
    assert text.startswith("error")
    # The revert endpoint takes the kind.
    r = c.post("/api/ai/revert", json={"kind": "delete", "block_id": top, "revert": record})
    assert r.status_code == 200, r.text
    assert children(c, page) == [top]


# --- the upload GC -----------------------------------------------------------------------

def test_a_chats_pictures_count_as_references(org, monkeypatch):
    c, ids = org
    monkeypatch.setattr(upload_gc, "UPLOAD_GRACE_S", 0)
    monkeypatch.setattr(upload_gc, "DEBOUNCE_S", 3600)
    r = c.post("/api/ai/pictures", files={"file": ("chat.png", _png(640, 480, "maroon"), "image/png")})
    assert r.status_code == 200, r.text
    url = r.json()["url"]
    name = url.rsplit("/", 1)[1]
    # Referenced from a saved conversation alone: in use.
    r = c.put(f"/api/chats/{ids['note']}", json={"messages": [
        {"role": "user", "text": "look", "images": [{"kind": "pasted", "url": url}]}], "updated_at": ""})
    assert r.status_code == 200, r.text
    out = upload_gc.reconcile(ids["ws"])
    assert name not in out["recorded"]
    with connect_pages_db(ids["ws"]) as conn:
        assert name.lower() in upload_gc.referenced(conn)
    # Archived into the history: still in use. Gone from both: an orphan, kept 30 days.
    r = c.post("/api/chat-history/archive", json={"bucket": ids["note"], "messages": [
        {"role": "user", "text": "look", "images": [{"kind": "pasted", "url": url}]}], "title": "", "updated_at": None})
    assert r.status_code == 200, r.text
    with connect_pages_db(ids["ws"]) as conn:
        assert name.lower() in upload_gc.referenced(conn)
        conn.execute("DELETE FROM chat_history")
        conn.execute("DELETE FROM chats")
        conn.commit()
    out = upload_gc.reconcile(ids["ws"])
    assert name in out["recorded"]
    assert c.get(url).status_code == 200  # still served
