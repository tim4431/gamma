"""Exporting an AI chat conversation with its pictures (gamma/chat_export.py,
docs/dev/chat_history.md "Exporting a conversation"): every picture the chat
shows packed beside the transcript, a picture that is gone said so, the
bare .md when there are none, an archived conversation by its id, and the
workspace's walls."""

import base64
import io
import re
import zipfile

import pytest

from gamma import ai_pictures
from gamma.db import connect_pages_db

from ai_fixtures import org  # noqa: F401  (fixture)
from conftest import login, make_user
from test_ai_pictures import _ink, _pdf, _png

MISSING_UPLOAD = f"/api/uploads/{'0' * 64}.png"


@pytest.fixture(scope="module")
def chatted(org, tmp_path_factory):
    """A paper page (doc id f…f, a blank two-page PDF patched in per test)
    with a handwriting group, and a stored chat picture."""
    c, ids = org

    def block(parent, content, props=None):
        r = c.post("/api/blocks", json={"parent_id": parent, "content": content, "properties": props or {}})
        assert r.status_code == 200, r.text
        return r.json()["id"]

    paper = block("root", "export paper", {"doc_id": "f" * 24})
    drawn = c.post("/api/upload-ink", json=_ink()).json()
    group = block(paper, "circled", {"ink_url": drawn["url"], "pdf_position": drawn["pdf_position"], "ink_strokes": 1})
    r = c.post("/api/ai/pictures", files={"file": ("fig.png", _png(900, 600, "navy"), "image/png")})
    assert r.status_code == 200, r.text
    pdf = _pdf(tmp_path_factory.mktemp("chat-export") / "paper.pdf")
    return c, {**ids, "paper": paper, "group": group, "picture": r.json()["url"], "pdf": pdf}


@pytest.fixture
def with_pdf(chatted, monkeypatch):
    _, ids = chatted
    monkeypatch.setattr("gamma.ai_context.pdf_path", lambda ws, doc: ids["pdf"] if doc == "f" * 24 else None)
    return chatted


def _export(c, messages, title="", bucket="", **kwargs):
    r = c.post("/api/chats/export", json={"bucket": bucket, "title": title, "messages": messages}, **kwargs)
    assert r.status_code == 200, r.text
    return r


def _unzip(r):
    assert r.headers["content-type"] == "application/zip"
    z = zipfile.ZipFile(io.BytesIO(r.content))
    names = z.namelist()
    md = next(n for n in names if n.endswith(".md"))
    return z.read(md).decode(), {n: z.read(n) for n in names if n.startswith("assets/")}


def _linked(md):
    return re.findall(r"!\[[^\]]*\]\((assets/[^)]+)\)", md)


def test_a_conversation_without_pictures_is_a_bare_markdown_file(chatted):
    c, ids = chatted
    r = _export(c, [{"role": "user", "text": "What is the noise model?"},
                    {"role": "ai", "text": "A **dephasing** channel.", "model": "claude-opus-5-5", "effort": "high"}],
                bucket=ids["paper"])
    assert r.headers["content-type"].startswith("text/markdown")
    assert "filename*=UTF-8''What%20is%20the%20noise%20model.md" in r.headers["content-disposition"]
    md = r.text
    assert md.startswith("# What is the noise model?\n")  # no title given: the first question names it
    assert "AI chat of “export paper” · 2 messages · exported " in md
    assert "## You\n\nWhat is the noise model?" in md
    assert "## AI · claude-opus-5-5 · high\n\nA **dephasing** channel." in md
    # A folder's chat and the library's say whose they are.
    assert "AI chat of the folder “readout”" in _export(c, [], "x", ids["readout"]).text
    assert "AI chat of the library · 0 messages" in _export(c, [], "x", "home").text


def test_every_picture_the_chat_shows_is_packed(with_pdf):
    c, ids = with_pdf
    paper, picture = ids["paper"], ids["picture"]
    data_url = "data:image/png;base64," + base64.b64encode(_png(40, 30, "olive")).decode()
    messages = [
        {"role": "user", "text": "What are these?", "pdfs": ["paper.pdf"],
         "contextPages": [{"id": ids["a"], "title": "cavity paper"}],
         "images": [{"kind": "file", "url": picture, "name": "fig.png", "width": 900, "height": 600},
                    {"kind": "area", "page_id": paper, "page": 2, "box": [0.1, 0.1, 0.5, 0.3]},
                    data_url]},
        {"role": "ai", "model": "m",
         "context": [{"doc_id": "f" * 24, "selection": {"passages": [
             {"crop": True, "page": 1, "box": [0.1, 0.2, 0.6, 0.4]}, {"crop": False, "page": 1, "box": [0, 0, 1, 1]}]}}],
         "actions": [{"kind": "view", "summary": "Viewed p. 1 of “export paper”", "page_id": paper, "pdf_page": 1},
                     {"kind": "ink", "summary": "Looked at your handwriting", "block_id": ids["group"],
                      "picture": ai_pictures.ink_picture_url(ids["group"])},
                     {"kind": "read", "summary": "Read notes of “export paper”"}],
         "text": (f"The figure ![fig|300]({picture}) and page one ![p. 1](/api/ai/page-image/{paper}?page=1&ink=1).\n\n"
                  f"```markdown\n![kept as written]({picture})\n```")},
    ]
    r = _export(c, messages, "Figures")
    assert "filename*=UTF-8''Figures.zip" in r.headers["content-disposition"]
    md, assets = _unzip(r)
    linked = _linked(md)
    assert linked and set(linked) <= set(assets), (linked, sorted(assets))
    assert "no longer available" not in md
    # The stored picture goes as stored, once, however often it is shown.
    stored = c.get(picture).content
    assert sum(data == stored for data in assets.values()) == 1
    assert md.count(next(n for n, data in assets.items() if data == stored)) == 2
    # The user's pictures, what rode along, and the text, in the bubble's order.
    user = md.split("## You", 1)[1].split("## AI", 1)[0]
    assert user.count("](assets/") == 3
    assert "![Image file fig.png](assets/" in user and "![Region of PDF page 2](assets/" in user
    assert "*Attached: paper.pdf*" in user and "*Pages in context: “cavity paper”*" in user
    assert user.rstrip().endswith("What are these?")
    # The reply: the selection crop the model got, the steps with what their
    # tools looked at, then the text with its pictures pointing at assets/.
    reply = md.split("## AI · m", 1)[1]
    assert reply.index("*Picture of the selection sent:*") < reply.index("**Steps**") < reply.index("The figure")
    assert reply.count("![Picture of the selection sent](assets/") == 1  # crop: False sent none
    assert "- Viewed p. 1 of “export paper”\n\n  ![Viewed p. 1 of “export paper”](assets/" in reply
    assert "  ![Looked at your handwriting](assets/" in reply
    assert "- Read notes of “export paper”\n" in reply
    assert re.search(r"!\[fig\|300\]\(assets/\w+\.png\) and page one !\[p\. 1\]\(assets/\w+\.(jpg|png)\)", reply)
    assert f"```markdown\n![kept as written]({picture})\n```" in reply  # fenced code is left alone
    assert "/api/" not in reply.replace(f"![kept as written]({picture})", "")
    for name, data in assets.items():
        assert ai_pictures.sniff(data), name  # every asset is a picture


def test_a_picture_that_is_gone_says_so(with_pdf):
    c, ids = with_pdf
    r = _export(c, [
        {"role": "user", "text": "look", "images": [{"kind": "pasted", "url": MISSING_UPLOAD},
                                                   {"kind": "area", "page_id": ids["note"], "page": 1, "box": [0, 0, 1, 1]}]},
        {"role": "ai", "text": f"It was ![the plot]({MISSING_UPLOAD}).",
         "actions": [{"kind": "view", "summary": "Viewed p. 9", "page_id": ids["paper"], "pdf_page": 9}]},
        {"role": "ai", "error": True, "errorKind": "network", "text": "Couldn't reach the provider"},
    ], "Gone")
    assert r.headers["content-type"].startswith("text/markdown")  # nothing left to pack
    md = r.text
    assert "*(picture no longer available: Pasted image)*" in md
    assert "*(picture no longer available: Region of PDF page 1)*" in md  # the note page has no PDF
    assert "It was *(picture no longer available: the plot)*." in md
    assert "  *(picture no longer available: Viewed p. 9)*" in md  # past the PDF's end
    assert "*This reply failed: Couldn't reach the provider*" in md


def test_an_archived_conversation_exports_by_its_id(chatted):
    c, ids = chatted
    messages = [{"role": "user", "text": "Kept", "images": [{"kind": "pasted", "url": ids["picture"]}]},
                {"role": "ai", "text": "Indeed."}]
    r = c.post("/api/chat-history/archive", json={"bucket": ids["b"], "messages": messages, "title": "Old talk"})
    entry = r.json()["id"]
    md, assets = _unzip(c.get(f"/api/chat-history/{entry}/export"))
    assert md.startswith("# Old talk\n") and "AI chat of “qec paper” · 2 messages" in md
    assert list(assets.values()) == [c.get(ids["picture"]).content]
    assert c.get("/api/chat-history/nope/export").status_code == 404


def test_a_viewer_exports_and_another_workspace_reads_nothing(with_pdf):
    c, ids = with_pdf
    make_user("ce_boss", "ce-boss-pw", is_admin=1)
    make_user("ce_viewer", "ce-viewer-pw")
    make_user("ce_stranger", "ce-stranger-pw")
    boss, viewer = login("ce_boss", "ce-boss-pw"), login("ce_viewer", "ce-viewer-pw")
    ws = boss.post("/api/workspaces", json={"name": "Chat export lab", "kind": "shared", "owner": ids["user"]}).json()["id"]
    lab = {"X-Gamma-Workspace": ws}
    assert c.put(f"/api/workspaces/{ws}/members/ce_viewer", json={"role": "viewer"}, headers=lab).status_code == 200
    r = c.post("/api/ai/pictures", files={"file": ("lab.png", _png(300, 200, "purple"), "image/png")}, headers=lab)
    assert r.status_code == 200, r.text
    url = r.json()["url"]
    messages = [{"role": "user", "text": "lab figure", "images": [{"kind": "pasted", "url": url}]}]
    # A viewer can't save a chat, but reads the workspace's pictures into a file.
    assert viewer.put("/api/chats/home", json={"messages": messages}, headers=lab).status_code == 403
    _, assets = _unzip(_export(viewer, messages, "Lab", headers=lab))
    assert list(assets.values()) == [c.get(url, headers=lab).content]
    # The viewer's own workspace holds neither that picture nor the paper.
    md = _export(viewer, messages + [{"role": "ai", "text": f"![p](/api/ai/page-image/{ids['paper']}?page=1)"}], "Mine").text
    assert md.count("no longer available") == 2
    # Members only.
    stranger = login("ce_stranger", "ce-stranger-pw")
    assert stranger.post("/api/chats/export", json={"messages": messages}, headers=lab).status_code == 403


def test_picture_at_reads_only_the_chats_own_pictures(with_pdf):
    c, ids = with_pdf
    with connect_pages_db(ids["ws"]) as conn:
        read = lambda url: ai_pictures.picture_at(ids["ws"], conn, url)  # noqa: E731
        assert read(ids["picture"]) == (c.get(ids["picture"]).content, "image/png")  # as stored
        assert read(f"/api/ai/page-image/{ids['paper']}?page=1&box=0.1,0.1,0.5,0.5")[1].startswith("image/")
        assert read(f"/api/ai/selection-crop/{'f' * 24}?page=1&box=0.1,0.1,0.5,0.5")[1].startswith("image/")
        assert read(f"/api/ai/ink-image/{ids['group']}?area=page")[1].startswith("image/")
        for refused in (f"https://elsewhere.example{ids['picture']}", "/api/uploads/" + "a" * 24 + ".pdf",
                        MISSING_UPLOAD, f"/api/ai/page-image/{ids['paper']}?page=0",
                        f"/api/ai/page-image/{ids['paper']}?page=1&box=0.9,0,0.1,1",
                        f"/api/ai/selection-crop/{'f' * 24}?page=1", "/api/ai/selection-crop/../x?page=1&box=0,0,1,1",
                        f"/api/ai/ink-image/{ids['note']}", "data:image/png;base64,bm90IGEgcGljdHVyZQ==", "", "/elsewhere"):
            assert read(refused) is None, refused
