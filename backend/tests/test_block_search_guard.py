"""/api/block-search (the Ctrl+F notes group, the [[ref]] popup) can't hang
the server: a pattern of the caller's is refused (one catastrophic regex
holds the GIL, i.e. every request, for good), the query is always text, the
scan runs in the threadpool with SQLite dropping the blocks that can't match
(textnorm.literal_runs) and stops at a time budget — and for normal queries
it answers exactly what the old full Python scan did."""

import inspect
import re
import time
from contextlib import closing

import pytest

from conftest import login, make_user, workspace_of
from gamma.blocks_store import trashed_ids
from gamma.db import connect_pages_db
from gamma.routers import blocks as blocks_router
from gamma.textnorm import fuzzy_pattern, literal_runs

USER, PASSWORD = "bsg_owner", "pw-bsg-1"

TEXTS = [
    "Continuous operation of a coherent 3,000-qubit system",
    "3 000 atoms in a tweezer array",
    "x-ray crystallography of the sample",
    "Über café au lait",
    "ÜBER CAFÉ NOIR",
    "naïve résumé",
    "50% of the snake_case names",
    r"a path C:\data\x and a 100%_literal",
    "量子纠缠 and 量子计算",
    "The Qubit and the qubit",
    "refined fine-tuning",
    "(\\w+\\s?)+# is not a pattern here",
    "gate fidelity above 99.9 percent",
]


@pytest.fixture(scope="module")
def owner():
    make_user(USER, PASSWORD)
    c = login(USER, PASSWORD)
    page = c.post("/api/blocks", json={"parent_id": "root", "content": "Search fixture page"}).json()["id"]
    for text in TEXTS:
        r = c.post("/api/blocks", json={"parent_id": page, "content": text})
        assert r.status_code == 200, r.text
    return c


def _old_scan(q, case=False, whole=False, limit=20):
    """The old endpoint's notes scan: every block, newest first, the fuzzy
    pattern in Python — the reference the prefiltered scan must equal."""
    ws = workspace_of(USER)
    pattern = fuzzy_pattern(q, case, whole)
    with closing(connect_pages_db(ws)) as conn:
        gone = trashed_ids(conn)
        return [r[0] for r in conn.execute(
            "SELECT id, content FROM unified_blocks WHERE content != '' ORDER BY updated_at DESC")
            if r[0] not in gone and pattern.search(r[1] or "")][:limit]


QUERIES = [
    ("3000", {}), ("3,000 qubit", {}), ("3000-qubit", {}), ("3 000", {}), ("x ray", {}), ("x-ray", {}),
    ("über", {}), ("ÜBER", {}), ("café", {}), ("CAFÉ", {"case": 1}), ("cafe", {}), ("naïve", {}),
    ("50%", {}), ("snake_case", {}), ("_", {}), ("%", {}), (r"C:\data", {}), ("量子", {}),
    ("qubit", {"case": 1}), ("Qubit", {"case": 1}), ("QUBIT", {}), ("fine", {"whole": 1}),
    ("fine", {}), ("99.9", {}), (r"(\w+\s?)+#", {}), ("zzz-not-there", {}),
]


@pytest.mark.parametrize("q,flags", QUERIES)
def test_results_equal_the_full_python_scan(owner, q, flags):
    r = owner.get("/api/block-search", params={"q": q, "limit": 20, **flags})
    assert r.status_code == 200, r.text
    body = r.json()
    assert "partial" not in body
    assert [b["id"] for b in body["blocks"]] == _old_scan(q, bool(flags.get("case")), bool(flags.get("whole")))


def test_a_callers_pattern_is_refused(owner):
    r = owner.get("/api/block-search", params={"q": "plaino|hilite", "regex": 1})
    assert r.status_code == 400


def test_a_catastrophic_pattern_is_just_text(owner):
    """``(\\w+\\s?)+#`` over prose backtracks for hours as a regex; as the
    query text it is escaped and answers at once."""
    prose = ("quantum qubit entanglement coherence decoherence lattice photon laser cavity "
             "rydberg atom trap optical tweezer gate fidelity error correction surface code ") * 2
    page = owner.post("/api/blocks", json={"parent_id": "root", "content": "ReDoS page"}).json()["id"]
    owner.post("/api/blocks", json={"parent_id": page, "content": prose[:200]})
    started = time.monotonic()
    r = owner.get("/api/block-search", params={"q": r"(\w+\s?)+#", "limit": 20})
    assert r.status_code == 200 and time.monotonic() - started < 5
    assert [b["content"] for b in r.json()["blocks"]] == ["(\\w+\\s?)+# is not a pattern here"]


def test_the_scan_stops_at_its_budget(owner, monkeypatch):
    """Candidates the prefilter lets through but the pattern refuses
    ("refined" for the whole word "fine") keep the scan going; past the
    budget it answers with what it has, marked ``partial``."""
    page = owner.post("/api/blocks", json={"parent_id": "root", "content": "Budget page"}).json()["id"]
    r = owner.put(f"/api/blocks/{page}/children",
                  json={"blocks": [{"content": f"refined note {i}"} for i in range(1500)]})
    assert r.status_code == 200, r.text
    assert "partial" not in owner.get("/api/block-search", params={"q": "fine", "whole": 1}).json()
    monkeypatch.setattr(blocks_router, "BLOCK_SEARCH_BUDGET_S", 0)
    r = owner.get("/api/block-search", params={"q": "fine", "whole": 1, "limit": 20})
    assert r.status_code == 200
    assert r.json()["partial"] is True


def test_block_search_runs_in_the_threadpool():
    assert not inspect.iscoroutinefunction(blocks_router.block_search)


def _ascii_fold(s):
    return re.sub(r"[A-Z]", lambda m: m.group().lower(), s)


@pytest.mark.parametrize("text", TEXTS)
def test_every_match_contains_the_literal_runs(text):
    """The prefilter never drops a block the pattern would match: for every
    query built from the text's own words, each run SQLite looks for is in
    the text (exactly with ``case``, folding ASCII only without — LIKE)."""
    words = re.split(r"[\s\-]+", text)
    for i in range(len(words)):
        for j in range(i + 1, min(len(words), i + 3) + 1):
            q = " ".join(words[i:j])
            for case in (False, True):
                if fuzzy_pattern(q, case) is None or not fuzzy_pattern(q, case).search(text):
                    continue
                for run in literal_runs(q, case):
                    if case:
                        assert run in text, (q, run)
                    else:
                        assert _ascii_fold(run) in _ascii_fold(text), (q, run)


def test_literal_runs_shape():
    assert literal_runs("3,000-qubit system") == ["system", "qubit", "0", "3"]
    assert literal_runs("Über café") == ["ber", "caf"]           # LIKE folds ASCII only
    assert literal_runs("Über café", case=True) == ["café", "Über"]
    assert literal_runs("量子 计算") == ["计算", "量子"]            # no case: kept whole
    assert literal_runs("  - ") == []
