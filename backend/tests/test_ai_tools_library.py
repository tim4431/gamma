"""The agent's library tools beyond reading and filing: cite (the citation
records kept with pages), save_paper (the clip ingest, as the reply's Save
to library runs it) and Recently deleted (list_deleted, restore_page).
Upstream fetches are faked and the metadata thread is stubbed out."""

import io
import json

import pytest

import gamma.routers.clip as clip_mod
import gamma.routers.pdf as pdf_mod
from gamma.ai_tools import MAX_SAVES, Tally, approval_preview, run_agent_tool

from ai_fixtures import FakeResp, ai_provider, folder, org, props  # noqa: F401  (fixtures)

PDF_BYTES = b"%PDF-1.4 save test\n" + b"z" * 10_000


class _Upstream(io.BytesIO):
    def __init__(self, url, data, ctype):
        super().__init__(data)
        self._url = url
        self.headers = {"Content-Type": ctype, "Content-Length": str(len(data))}

    def geturl(self):
        return self._url

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False


@pytest.fixture
def upstream(monkeypatch):
    """PDFs for arXiv and *.pdf URLs, an empty web page otherwise; the
    metadata lookup is recorded, not run."""
    calls = {"fetched": [], "meta": []}

    def fake_urlopen(req, timeout=30):
        calls["fetched"].append(req.full_url)
        if req.full_url.endswith(".pdf") or "arxiv.org/pdf/" in req.full_url:
            return _Upstream(req.full_url, PDF_BYTES, "application/pdf")
        return _Upstream(req.full_url, b"<html><body>nothing here</body></html>", "text/html")

    monkeypatch.setattr(pdf_mod, "guarded_urlopen", fake_urlopen)
    monkeypatch.setattr(clip_mod, "_start_metadata",
                        lambda ws, actor, block_id, doi="", arxiv_id="": calls["meta"].append(block_id))
    return calls


def _page(c, title, properties):
    r = c.post("/api/blocks", json={"parent_id": "root", "content": title, "properties": properties})
    assert r.status_code == 200, r.text
    return r.json()["id"]


# --- cite ---------------------------------------------------------------------------

def test_cite_gives_records_and_bibtex(org):
    c, ids = org
    text, action = run_agent_tool(ids["ws"], folder(""), "cite", {"page_ids": [ids["a"], ids["b"]]})
    assert f'## "cavity paper" (page_id {ids["a"]})' in text
    assert "Authors: Ada One, Bo Two · Year: 2019 · Venue: Nature" in text
    assert "```bibtex\n@article{one2019," in text and "journal = {Nature}" in text  # built from the record
    assert "No paper metadata yet" in text and "do not make a record up" in text
    assert action["kind"] == "cite" and action["summary"] == "Cited “cavity paper” · 1 without metadata"
    # A page chat cites its own page by default.
    text, _ = run_agent_tool(ids["ws"], {"type": "page", "page_id": ids["a"]}, "cite", {})
    assert "@article{one2019," in text


def test_cite_prefers_the_stored_entry_and_flags_unverified_records(org):
    c, ids = org
    page = _page(c, "stored cite", {"folder": "readout", "bibtex": "@misc{kept,\n  title = {Kept}\n}",
                                    "ppt_cite": "K. Ept, Nature 1 (2020)",
                                    "meta": {"title": "Kept", "authors": ["K. Ept"], "unverified": True}})
    text, _ = run_agent_tool(ids["ws"], folder("readout"), "cite", {"page_ids": page})  # a bare id works too
    assert "@misc{kept," in text and "Slide citation: K. Ept, Nature 1 (2020)" in text
    assert "Unverified:" in text and "Paper title: Kept" in text
    # Pages outside the chat's folder are refused, and all refused is an error.
    text, action = run_agent_tool(ids["ws"], folder("cooling"), "cite", {"page_ids": [page]})
    assert text.startswith("error") and "outside" in text and action["error"]


# --- save_paper ------------------------------------------------------------------------

def test_save_paper_saves_files_and_never_duplicates(org, upstream):
    c, ids = org
    scope = folder("readout")
    text, action = run_agent_tool(ids["ws"], scope, "save_paper",
                                  {"source": "arXiv:2601.04321", "title": "Saved From Chat"})
    assert text.startswith("ok — saved [Saved From Chat](/?page=") and "with its PDF" in text, text
    page = action["page_id"]
    assert action["kind"] == "save" and action["to"] == "readout" and action["pdf"] and not action["existed"]
    saved = props(c, page)
    assert saved["content"] == "Saved From Chat" and saved["properties"]["folder"] == "readout"
    assert saved["properties"]["doc_id"] and upstream["meta"] == [page]
    assert any("arxiv.org/pdf/2601.04321" in u for u in upstream["fetched"])
    # The same paper again, into a subfolder: filed there, not duplicated.
    text, action = run_agent_tool(ids["ws"], scope, "save_paper",
                                  {"source": "https://arxiv.org/abs/2601.04321v2", "folder": "fast"})
    assert "already in the library" in text and "readout/fast" in text
    assert action["page_id"] == page and action["existed"] and action["to"] == "readout/fast"
    # And once more where it already is: nothing changed, and the chip says so.
    text, action = run_agent_tool(ids["ws"], scope, "save_paper",
                                  {"source": "2601.04321", "folder": "fast"})
    assert "nothing changed" in text and action["noop"] and not action.get("error")


def test_the_approval_card_shows_what_a_save_would_do(org, upstream):
    """save_paper's preview (the card of an asking Save papers) names the
    paper, the folder and the source; a paper filed there already, or a
    source that is none, is answered without a card. Nothing is fetched."""
    c, ids = org
    scope = folder("readout")
    preview, answer = approval_preview(ids["ws"], scope, "save_paper",
                                       {"source": "arXiv:2601.05555", "title": "  A New One "})
    assert answer is None
    assert preview == {"title": "A New One", "to": "readout", "diff": [["ctx", "arXiv:2601.05555"]]}
    assert upstream["fetched"] == []
    _, action = run_agent_tool(ids["ws"], scope, "save_paper", {"source": "arXiv:2601.05555"})
    page, fetched = action["page_id"], len(upstream["fetched"])
    preview, answer = approval_preview(ids["ws"], scope, "save_paper",
                                       {"source": "https://arxiv.org/abs/2601.05555", "folder": "fast"})
    assert answer is None and preview["existed"] and preview["page_id"] == page
    assert preview["to"] == "readout/fast"
    preview, answer = approval_preview(ids["ws"], scope, "save_paper", {"source": "2601.05555"})
    assert preview is None and "already in the library" in answer and "nothing changed" in answer
    preview, answer = approval_preview(ids["ws"], scope, "save_paper", {"source": "cats"})
    assert preview is None and answer.startswith("error")
    assert len(upstream["fetched"]) == fetched, "a preview fetches nothing"


def test_save_paper_in_a_page_chat_files_with_the_open_page(org, upstream):
    c, ids = org
    text, action = run_agent_tool(ids["ws"], {"type": "page", "page_id": ids["a"]}, "save_paper",
                                  {"source": "https://example.org/papers/cited-ref.pdf"})
    assert text.startswith("ok — saved") and "outside this chat's reach" in text, text
    assert action["to"] == "readout"  # page a's first folder
    assert props(c, action["page_id"])["properties"]["folder"] == "readout"


def test_save_paper_refusals(org, upstream):
    c, ids = org
    text, action = run_agent_tool(ids["ws"], folder(""), "save_paper", {"source": "a paper about cats"})
    assert text.startswith("error") and "DOI, an arXiv id" in text and action["error"]
    spent = Tally()
    for _ in range(MAX_SAVES):
        spent.take("saves", MAX_SAVES)
    text, _ = run_agent_tool(ids["ws"], {**folder(""), "tally": spent}, "save_paper",
                             {"source": "arXiv:2601.09999"})
    assert text.startswith("error") and "most one message may save" in text
    text, _ = run_agent_tool(ids["ws"], {**folder(""), "can_write": False}, "save_paper",
                             {"source": "arXiv:2601.09999"})
    assert "only view" in text
    text, _ = run_agent_tool(ids["ws"], folder(""), "save_paper", {"source": "arXiv:2601.09999"},
                             allowed_tools={"fetch_paper"})
    assert "not enabled" in text
    assert upstream["fetched"] == []  # refused before anything was fetched


def test_a_chat_request_carries_its_reading_choices_to_the_ingest(org, ai_provider, monkeypatch):
    """The chat request's paper_save rides in the tool scope, the choices it
    leaves out stay on, and save_paper hands them to the clip ingest."""
    c, ids = org
    import gamma.routers.ai as ai_mod
    got = []
    monkeypatch.setattr(clip_mod, "save_clip", lambda ws, actor, payload: got.append(payload) or {
        "block_id": ids["a"], "title": "cavity paper", "existed": True, "doc_id": ""})
    rounds = []

    def fake_open(messages, system, entry, rt, pdf_b64s=None, **kw):
        rounds.append(1)
        if len(rounds) == 1:
            return FakeResp([
                {"type": "content_block_start", "content_block":
                    {"type": "tool_use", "id": "s1", "name": "save_paper"}},
                {"type": "content_block_delta", "delta": {"type": "input_json_delta",
                    "partial_json": json.dumps({"source": "arXiv:2601.07777"})}},
                {"type": "content_block_stop"},
            ])
        return FakeResp([{"type": "content_block_delta", "delta": {"type": "text_delta", "text": "done"}}])

    monkeypatch.setattr(ai_mod, "_open_ai", fake_open)
    r = c.post("/api/ai/chat", json={"prompt": "save it", "agent_scope": "folder", "folder": "",
                                     "stream": True, "permissions": {"save": "allow"},
                                     "paper_save": {"save_copy": False}})
    assert r.status_code == 200, r.text
    (payload,) = got
    assert (payload.save_copy, payload.allow_oa, payload.fetch_metadata) == (False, True, True)
    assert payload.arxiv_id == "2601.07777" and payload.source_url == "https://arxiv.org/abs/2601.07777"


def test_the_save_count_spans_the_calls_of_one_message(org, upstream):
    _, ids = org
    scope = folder("")
    for n in range(2):
        run_agent_tool(ids["ws"], scope, "save_paper", {"source": f"https://example.org/p/count-{n}.pdf"})
    assert scope["tally"].count("saves") == 2


# --- Recently deleted -----------------------------------------------------------------------

def test_list_and_restore_deleted_pages_within_the_folder(org):
    c, ids = org
    page = _page(c, "old readout draft", {"folder": "readout/old"})
    assert c.delete(f"/api/blocks/{page}").status_code == 200
    text, action = run_agent_tool(ids["ws"], folder("readout"), "list_deleted", {})
    assert f'id={page} | "old readout draft" | was in readout/old' in text
    assert action["kind"] == "list" and "Recently deleted" in action["summary"]
    # Another folder's chat sees none of it, and cannot bring it back.
    text, _ = run_agent_tool(ids["ws"], folder("cooling"), "list_deleted", {})
    assert page not in text and "holds no pages" in text
    text, _ = run_agent_tool(ids["ws"], folder("cooling"), "restore_page", {"page_id": page})
    assert text.startswith("error") and "not filed in this chat's folder" in text
    # The title filter.
    text, _ = run_agent_tool(ids["ws"], folder(""), "list_deleted", {"title_contains": "nothing like it"})
    assert page not in text

    text, action = run_agent_tool(ids["ws"], folder("readout"), "restore_page", {"page_id": page})
    assert text.startswith("ok — restored [old readout draft]") and '"readout/old"' in text
    assert action["kind"] == "restore" and action["page_id"] == page and action["to"] == "readout/old"
    back = props(c, page)
    assert back["parent_id"] == "root" and back["properties"]["folder"] == "readout/old"
    # Restored already: nothing to do.
    text, action = run_agent_tool(ids["ws"], folder("readout"), "restore_page", {"page_id": page})
    assert "not deleted" in text and action["noop"]
    text, action = run_agent_tool(ids["ws"], folder("readout"), "restore_page", {"page_id": "nope"})
    assert text.startswith("error") and action["error"]


def test_the_approval_card_shows_what_a_restore_would_bring_back(org):
    c, ids = org
    page = _page(c, "a card draft", {"folder": "readout/cards"})
    assert c.delete(f"/api/blocks/{page}").status_code == 200
    scope = folder("readout")
    preview, answer = approval_preview(ids["ws"], scope, "restore_page", {"page_id": page})
    assert answer is None and preview == {"title": "a card draft", "to": "readout/cards"}
    assert c.get(f"/api/blocks/{page}").status_code == 404, "a preview restores nothing"
    preview, answer = approval_preview(ids["ws"], scope, "restore_page", {"page_id": ids["a"]})
    assert preview is None and "not deleted" in answer
    preview, answer = approval_preview(ids["ws"], folder("cooling"), "restore_page", {"page_id": page})
    assert preview is None and answer.startswith("error")


def test_recently_deleted_is_a_folder_chat_tool(org):
    _, ids = org
    text, action = run_agent_tool(ids["ws"], {"type": "page", "page_id": ids["a"]}, "list_deleted", {})
    assert "unknown tool" in text and action["error"]
    text, _ = run_agent_tool(ids["ws"], folder(""), "list_deleted", {}, allowed_tools={"list_pages"})
    assert "not enabled" in text
