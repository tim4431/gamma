"""POST /api/jobs/metadata — Settings → Maintenance's batch lookup as a
background job (kind ``metadata``, docs/dev/tasks.md)."""

import threading

import pytest
from conftest import make_page


ARXIV_META = {
    "title": "Fetched Title", "authors": ["Ada Lovelace"], "year": "2026",
    "venue": "arXiv:2601.00001", "volume": "", "pages": "",
    "doi": "", "arxiv_id": "2601.00001", "source": "arxiv",
}


@pytest.fixture
def arxiv(monkeypatch):
    """The arXiv lookup, stubbed: ``calls`` lists the ids asked for, and
    ``hold`` (an Event, when set by the test) makes the first call wait for
    ``release``."""
    from gamma.routers import metadata

    state = {"calls": [], "hold": None, "started": threading.Event(), "release": threading.Event()}

    def fake_fetch_arxiv(arxiv_id):
        state["calls"].append(arxiv_id)
        if state["hold"] is not None and len(state["calls"]) == 1:
            state["started"].set()
            assert state["release"].wait(10), "the test never released the lookup"
        return dict(ARXIV_META, arxiv_id=arxiv_id)

    monkeypatch.setattr(metadata, "_fetch_arxiv", fake_fetch_arxiv)
    return state


def _paper(guest, title, arxiv_id=""):
    """A page whose lookup finds its arXiv record, or (no id) finds nothing."""
    props = {"source_url": f"https://arxiv.org/abs/{arxiv_id}"} if arxiv_id else None
    return make_page(guest, title, properties=props)["id"]


def test_a_batch_looks_every_paper_up_and_counts_what_it_could_not_find(guest, arxiv):
    from gamma import jobs

    found = _paper(guest, "Batch paper", "2601.00001")
    nothing = _paper(guest, "Batch paper without identifiers")
    r = guest.post("/api/jobs/metadata", json={"block_ids": [found, nothing, "no-such-page", found]})
    assert r.status_code == 200, r.text
    job = r.json()
    assert job["kind"] == "metadata" and job["params"] == {"papers": 2}

    done = jobs.wait(job["id"])
    assert done["state"] == "done", done
    assert guest.get(f"/api/jobs/{job['id']}").json()["result"] == {"papers": 2, "fetched": 1, "failed": 1}
    assert done["progress"] == {"done": 2, "total": 2, "unit": "papers", "item": ""}
    assert guest.get(f"/api/blocks/{found}").json()["properties"]["meta"]["title"] == "Fetched Title"
    assert "meta_error" in guest.get(f"/api/blocks/{nothing}").json()["properties"]


def test_a_start_while_a_batch_runs_adds_its_papers_to_it(guest, arxiv):
    from gamma import jobs

    first, second = _paper(guest, "Held paper", "2601.00002"), _paper(guest, "Second paper", "2601.00003")
    third = _paper(guest, "Uploaded meanwhile", "2601.00004")
    arxiv["hold"] = True
    job = guest.post("/api/jobs/metadata", json={"block_ids": [first, second]}).json()
    try:
        assert arxiv["started"].wait(10)
        running = guest.get(f"/api/jobs/{job['id']}").json()
        assert running["progress"]["item"] == "Held paper" and running["progress"]["done"] == 0
        # the waiting paper asked again is not looked up twice
        again = guest.post("/api/jobs/metadata", json={"block_ids": [second, third], "force": False})
        assert again.status_code == 200, again.text
        assert again.json()["id"] == job["id"]
    finally:
        arxiv["release"].set()
    done = jobs.wait(job["id"])
    assert done["state"] == "done" and done["progress"]["total"] == 3
    assert arxiv["calls"] == ["2601.00002", "2601.00003", "2601.00004"]
    assert guest.get(f"/api/jobs/{job['id']}").json()["result"] == {"papers": 3, "fetched": 3, "failed": 0}


def test_a_batch_stops_between_papers_and_a_later_start_begins_anew(guest, arxiv):
    from gamma import jobs

    first, second = _paper(guest, "Stopped paper", "2601.00005"), _paper(guest, "Never reached", "2601.00006")
    arxiv["hold"] = True
    job = guest.post("/api/jobs/metadata", json={"block_ids": [first, second]}).json()
    try:
        assert arxiv["started"].wait(10)
        assert guest.post(f"/api/jobs/{job['id']}/cancel").status_code == 200
        # a stopping batch takes no more papers
        after = guest.post("/api/jobs/metadata", json={"block_ids": [second]}).json()
        assert after["id"] != job["id"]
    finally:
        arxiv["release"].set()
    assert jobs.wait(job["id"])["state"] == "cancelled"
    assert jobs.wait(after["id"])["state"] == "done"
    assert arxiv["calls"] == ["2601.00005", "2601.00006"]  # the stopped batch never reached its second


def test_without_force_a_page_keeps_the_record_it_has(guest, arxiv):
    from gamma import jobs

    page = make_page(guest, "Already described", properties={
        "source_url": "https://arxiv.org/abs/2601.00007", "meta": {"title": "Kept Title", "source": "arxiv"}})["id"]
    job = guest.post("/api/jobs/metadata", json={"block_ids": [page], "force": False}).json()
    assert jobs.wait(job["id"])["state"] == "done"
    assert arxiv["calls"] == []
    assert guest.get(f"/api/blocks/{page}").json()["properties"]["meta"]["title"] == "Kept Title"


def test_a_batch_needs_papers_of_this_workspace(guest, anon):
    assert guest.post("/api/jobs/metadata", json={"block_ids": ["no-such-page"]}).status_code == 400
    assert anon.post("/api/jobs/metadata", json={"block_ids": ["x"]}).status_code in (401, 403)
