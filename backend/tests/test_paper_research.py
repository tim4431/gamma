"""Research in the background (gamma/paper_research.py): the job runs the
same agent loop headless, files its report as a page, and stops when asked.
The provider is faked; the tools, the loop and the page write are real."""

import json

import pytest

from ai_fixtures import FakeResp, ai_provider, org, props  # noqa: F401  (fixtures)
from gamma import jobs, paper_research
from gamma.db import connect_pages_db


@pytest.fixture(scope="module", autouse=True)
def _provider(ai_provider):
    """The module's account needs a connection to answer on."""


@pytest.fixture(autouse=True)
def _offline(monkeypatch):
    monkeypatch.setattr("gamma.ai_catalog.context_window", lambda *args: (0, ""))
    monkeypatch.setattr("gamma.ai_catalog.image_input", lambda *args: (None, ""))


def _reply(*blocks):
    return [{"type": "content_block_delta", "delta": {"type": "text_delta", "text": text}}
            for text in blocks]


def _tool_use(call_id, name, args):
    return [{"type": "content_block_start", "content_block": {"type": "tool_use", "id": call_id, "name": name}},
            {"type": "content_block_delta", "delta": {"type": "input_json_delta",
                                                      "partial_json": json.dumps(args)}},
            {"type": "content_block_stop"}]


def _page_blocks(ws, page_id):
    with connect_pages_db(ws) as conn:
        return [row[0] for row in conn.execute(
            "SELECT content FROM unified_blocks WHERE parent_id = ? ORDER BY position, id", (page_id,))]


def _page(ws, page_id):
    with connect_pages_db(ws) as conn:
        row = conn.execute("SELECT content, properties FROM unified_blocks WHERE id = ?",
                           (page_id,)).fetchone()
    return row[0], json.loads(row[1] or "{}")


def test_research_reads_the_library_and_files_its_report(org, monkeypatch):
    c, ids = org
    rounds = []

    def fake_open(messages, system, entry, rt, pdf_b64s=None, **kw):
        rounds.append({"system": system, "tools": [t["name"] for t in (kw.get("tools") or [])]})
        if len(rounds) == 1:
            # The prompt is the background one, and the changing tools are
            # not on the table at all.
            assert "becomes a page in their library" in system
            assert "Nobody is at the keyboard" in system
            assert f'the folder "readout" (id {ids["readout"]})' in system  # it reads where it started
            assert "rename_page" not in rounds[0]["tools"] and "save_paper" not in rounds[0]["tools"]
            assert "read_paper" in rounds[0]["tools"]
            return FakeResp(_tool_use("s1", "search_library", {"query": "cavity"}))
        return FakeResp(_reply("Density is limited by light-assisted collisions.",
                               "\n\nSee [the PRL](https://doi.org/10.1/x), p. 3."))

    monkeypatch.setattr("gamma.paper_research.open_ai", fake_open)
    job = paper_research.start(user_id=ids["user_id"], ws=ids["ws"],
                               question="what limits the density?", folder=ids["readout"])
    done = jobs.wait(job["id"], timeout=30)
    assert done["state"] == "done", done.get("error")
    result = jobs.get(job["id"], full=True)["result"]
    assert result["steps"] == 1
    title, page_props = _page(ids["ws"], result["page_id"])
    assert title == "Research: what limits the density?"
    assert page_props.get("folders") == [ids["readout"]], "filed where the user started it"
    blocks = _page_blocks(ids["ws"], result["page_id"])
    assert blocks[0] == "**Question.** what limits the density?"
    assert "light-assisted collisions" in blocks[1] and "p. 3" in blocks[1]


def test_research_lists_what_it_read_and_what_stayed_blocked(org, monkeypatch):
    _, ids = org
    calls = []

    def fake_open(messages, system, entry, rt, pdf_b64s=None, **kw):
        calls.append(1)
        if len(calls) == 1:
            return FakeResp(_tool_use("f1", "fetch_paper", {"source": "https://example.org/p.pdf"}))
        return FakeResp(_reply("Nothing conclusive."))

    monkeypatch.setattr("gamma.paper_research.open_ai", fake_open)
    monkeypatch.setattr("gamma.ai_web.fetch_document", lambda source, published_only=False: {
        "kind": "pdf", "url": source, "title": "A paper", "pages": ["[p. 1]\ntext"],
        "chars": 4, "version": "publisher", "note": ""})
    job = paper_research.start(user_id=ids["user_id"], ws=ids["ws"], question="anything?")
    done = jobs.wait(job["id"], timeout=30)
    assert done["state"] == "done", done.get("error")
    result = jobs.get(job["id"], full=True)["result"]
    blocks = _page_blocks(ids["ws"], result["page_id"])
    assert "**Read for this report**" in blocks[-1]
    assert "[A paper](https://example.org/p.pdf)" in blocks[-1]
    assert result["blocked"] == []


def test_a_report_whose_folder_went_meanwhile_lands_at_the_library_root(org):
    _, ids = org
    page_id, _ = paper_research._file_report(ids["ws"], ids["user_id"], "why?", "folder-gone",
                                             "Because.", [])
    assert "folders" not in _page(ids["ws"], page_id)[1]


def test_a_research_job_stops_when_asked(org, monkeypatch):
    _, ids = org
    rounds = []

    def fake_open(messages, system, entry, rt, pdf_b64s=None, **kw):
        rounds.append(1)
        # Never answers: one search after another, so the job only ends by
        # being stopped (or by its round budget).
        return FakeResp(_tool_use(f"s{len(rounds)}", "search_library", {"query": "again"}))

    monkeypatch.setattr("gamma.paper_research.open_ai", fake_open)
    job = paper_research.start(user_id=ids["user_id"], ws=ids["ws"], question="loop forever")
    jobs.cancel(job["id"])
    done = jobs.wait(job["id"], timeout=30)
    assert done["state"] == "cancelled"


def test_a_research_job_needs_a_question_and_answers_through_the_api(org, monkeypatch):
    c, ids = org

    def fake_open(messages, system, entry, rt, pdf_b64s=None, **kw):
        return FakeResp(_reply("Short answer."))

    monkeypatch.setattr("gamma.paper_research.open_ai", fake_open)
    r = c.post("/api/jobs/research", json={"question": "  ", "folder": ""})
    assert r.status_code == 400 and "needs a question" in r.text
    r = c.post("/api/jobs/research", json={"question": "what limits it?", "folder": "no-such-folder"})
    assert r.status_code == 400 and "no such folder" in r.text
    r = c.post("/api/jobs/research", json={"question": "what limits it?", "folder": ids["readout"]})
    assert r.status_code == 200, r.text
    done = jobs.wait(r.json()["id"], timeout=30)
    assert done["state"] == "done", done.get("error")
    # The tray lists it like any other job of the account.
    listed = c.get("/api/jobs").json()["jobs"]
    assert any(row["kind"] == "research" and row["title"] == "what limits it?" for row in listed)
