"""The document text index at a work folder's scale (docs/dev/api.md
"Search", docs/research/file-explorer.md "Scale"): 20,000 text files of one
work folder beside 500 library pages, 2,000 of the files indexed and the
other 18,000 pending — read in place, their ``linked_files`` rows naming
paths that hold nothing now. A search scoped to the work folder costs one
query for what is linked and at most PROBE_LIMIT probes; the scope is
resolved on the server; the job over the 18,000 leaves the readers within
their budgets while it runs; a sweep, one hash's purge, a file's
extraction and a window of a 64 MB file are timed. The rows are inserted
directly, as a vault's would stand.

Timed like tests/test_perf.py, and marked ``perf`` with it: ``python -m
pytest tests/test_search_scale.py -m perf -p no:xdist -q`` from backend/,
alone, on an idle machine (``-s`` prints each case's numbers)."""

import json
import os
import secrets
import time
from types import SimpleNamespace

import pytest

from conftest import account_of, login, make_folder, make_user, mark_work_folder, workspace_of
from gamma import jobs, storage
from gamma.blocks_store import BLOCK_COLUMNS, FOLDERS, pages_in_scope, touch_page
from gamma.db import connect_data_db, connect_pages_db, connect_vaults_db, page_now
from gamma.documents import text
from gamma.search import index as text_index, indexer
from gamma.search.index import Doc
from test_perf import budget, measure

pytestmark = pytest.mark.perf

USER, PASSWORD = "sss_owner", "pw-sss-1"
LIBRARY_PAGES = 500
FILES = 20_000
DIRS = 10
INDEXED = 2_000
UNIT = ("the gradient of the loss is computed for every parameter of the model " * 45)[:3000]
# A search over the 20,000 files reads every file page's document per
# request (pages_in_scope ~70 ms, root_pages ~110 ms, pending ~65 ms, the
# MATCH ~40 ms at 2026-11): about twice the p95 then.
SCOPED_SEARCH_MS = 800


@pytest.fixture(autouse=True, scope="module")
def _serial():
    """Timed alone: beside other xdist workers the numbers mean nothing."""
    if os.environ.get("PYTEST_XDIST_WORKER"):
        pytest.skip("timing budgets run serially: python -m pytest tests/test_search_scale.py -m perf")


def _rows(conn, rows, now) -> None:
    """Root page rows and their change-log rows, inserted directly."""
    seq = conn.execute("SELECT COALESCE(MAX(seq), 0) FROM page_changes").fetchone()[0]
    conn.executemany(f"INSERT INTO unified_blocks ({BLOCK_COLUMNS}) VALUES (?, 'root', ?, ?, ?, ?, ?, ?)",
                     [(page_id, f"z{n:06d}", title, json.dumps(props), now, now, page_id)
                      for n, (page_id, title, props) in enumerate(rows)])
    conn.executemany("INSERT INTO page_changes (page_id, seq, kind, at, actor) VALUES (?, ?, 'live', ?, '')",
                     [(r[0], seq + k, now) for k, r in enumerate(rows, 1)])


@pytest.fixture(scope="module")
def big(tmp_path_factory):
    """W of 20,000 ``.tex`` files in 10 directories beside 500 library
    PDFs; the first 2,000 files indexed (a 3 KB unit each), the rest read
    in place from paths that are gone (pending, linked)."""
    make_user(USER, PASSWORD)
    c, ws = login(USER, PASSWORD), workspace_of(USER)
    w = make_folder(c, "sss-W")
    mark_work_folder(c, w)
    gone = tmp_path_factory.mktemp("gone")
    now = page_now()
    docs = {}
    with connect_pages_db(ws) as conn:
        dirs = [secrets.token_urlsafe(9) for _ in range(DIRS)]
        conn.executemany(f"INSERT INTO unified_blocks ({BLOCK_COLUMNS}) VALUES (?, ?, ?, ?, '{{}}', ?, ?, ?)",
                         [(d, w, f"a{n:06d}", f"dir {n}", now, now, FOLDERS) for n, d in enumerate(dirs)])
        library = [(secrets.token_urlsafe(9), f"library paper {n}",
                    {"doc": {"format": "pdf", "name": f"paper {n}.pdf", "hash": secrets.token_hex(32), "ext": ".pdf",
                             "at": "store"}}) for n in range(LIBRARY_PAGES)]
        files = []
        for n in range(FILES):
            h, leaf = secrets.token_hex(32), f"file-{n:05d}.tex"
            docs[h] = Doc(h, "text", ".tex", "path")
            files.append((secrets.token_urlsafe(9), leaf, {
                "work_folder": w, "folders": [dirs[n % DIRS]], "file_name": leaf,
                "doc": {"format": "text", "name": leaf, "hash": h, "ext": ".tex", "at": "path", "size": 3000}}))
        _rows(conn, library + files, now)
        touch_page(conn, FOLDERS, account_of(USER), now=now)
        conn.commit()
    hashes = list(docs)
    with connect_data_db(ws) as conn:
        text_index.store_docs(conn, [(h, [(1, UNIT)], {"format": "text", "lines": 40, "truncated": False})
                                     for h in hashes[:INDEXED]])
    with connect_vaults_db() as conn:
        conn.executemany("INSERT INTO linked_files (ws, name, path, size, mtime_ns, file_id, page_id, actor) "
                         "VALUES (?, ?, ?, 3000, 1, '', '', '')",
                         [(ws, docs[h].name, str(gone / f"{h}.tex")) for h in hashes[INDEXED:]])
    return SimpleNamespace(c=c, ws=ws, w=w, dirs=dirs, docs=docs, pending={h: docs[h] for h in hashes[INDEXED:]},
                           tmp=tmp_path_factory.mktemp("files"))


def _search(c, w):
    r = c.post("/api/document-search", json={"q": "gradient", "folders": [w]})
    r.raise_for_status()
    return r.json()


def test_a_scoped_search_with_thousands_pending_answers_at_once(big, monkeypatch, request):
    monkeypatch.setenv("GAMMA_FOLDER_LINKS", "1")  # the files are read in place: links are kept here
    monkeypatch.setattr(indexer, "index_missing_async", lambda ws, docs: True)
    probed = []
    real = storage.readable
    monkeypatch.setattr(storage, "readable", lambda ws, name: probed.append(name) or real(ws, name))
    body = _search(big.c, big.w)
    assert len(probed) == text_index.PROBE_LIMIT
    assert body["indexing"] == FILES - INDEXED - text_index.PROBE_LIMIT
    assert body["results"] and all(h["source"] == "file" for h in body["results"])
    budget(request, measure(lambda _: _search(big.c, big.w)), SCOPED_SEARCH_MS)


def test_a_work_folders_scope_resolves_at_once(big, request):
    def scope(_):
        with connect_pages_db(big.ws) as conn:
            return pages_in_scope(conn, [big.w])

    assert len(scope(None)) == FILES
    budget(request, measure(scope), 100)


def test_a_long_log_is_extracted_and_stored_at_once(big, request):
    lines = [f"2026-11-12 10:{n // 60 % 60:02d}:{n % 60:02d} worker {n % 17} step {n} loss 0.{n % 997:03d}"
             for n in range(60_000)]
    data = "\n".join(lines).encode()
    assert len(data) > 3_000_000
    h = secrets.token_hex(32)
    storage.put_upload(big.ws, f"{h}.log", data)
    doc = Doc(h, "text", ".log", "store")

    def run(_):
        found = indexer.index_doc(big.ws, doc)
        with connect_data_db(big.ws) as conn:
            text_index.store_docs(conn, [found])
        return found

    found = run(None)
    lines = found[2]["lines"]
    assert found[2]["truncated"] and lines < 60_000  # the cut: MAX_TEXT_CHARS of a 3 MB log
    assert len(found[1]) == -(-lines // text.RUN_LINES)
    budget(request, measure(run, warmup=1, runs=5), 1500)


def test_the_job_leaves_the_readers_within_their_budgets(big, request):
    """The 18,000 pending files, out of reach, become files with no path
    (no ``linked_files`` rows): each is recorded with no text. While the
    job runs, the scoped search and a directory's listing are timed."""
    with connect_vaults_db() as conn:
        conn.execute("DELETE FROM linked_files WHERE ws = ?", (big.ws,))
    t0 = time.perf_counter()
    assert indexer.index_missing_async(big.ws, big.pending) is True
    (job,) = [j for j in big.c.get("/api/jobs").json()["jobs"] if j["kind"] == "indexing" and j["state"] != "done"]
    searched = measure(lambda _: _search(big.c, big.w))
    listed = measure(lambda _: big.c.get(f"/api/folders/{big.dirs[0]}/pages").raise_for_status())
    still = jobs.get(job["id"])["state"] in ("queued", "running")
    done = jobs.wait(job["id"], timeout=600)
    print(f"\n{request.node.name}: the job over {len(big.pending)} files took {time.perf_counter() - t0:.1f} s "
          f"({'still running' if still else 'done'} when the readers were timed); "
          f"search p95 {searched['p95']:.2f} ms, listing p95 {listed['p95']:.2f} ms")
    assert done["state"] == "done" and done["result"] == {"documents": len(big.pending)}
    with connect_data_db(big.ws) as conn:
        assert text_index.missing(conn, big.pending) == []
    budget(request, searched, SCOPED_SEARCH_MS)
    budget(request, listed, 60)


def test_a_sweep_of_every_document_is_quick(big, request):
    def sweep(_):
        with connect_pages_db(big.ws) as conn:
            text_index.purge_unused(big.ws, conn)

    budget(request, measure(sweep), 300)


def test_one_hashs_purge_is_one_lookup(big, request):
    def setup():
        h = secrets.token_hex(32)
        with connect_data_db(big.ws) as conn:
            text_index.store_doc(conn, h, [(1, "a replaced file's text")], format="text", lines=1)
        return h

    def purge(h):
        with connect_pages_db(big.ws) as conn:
            text_index.purge_doc(big.ws, None, conn, h)

    budget(request, measure(purge, setup=setup), 5)


def test_a_large_tex_file_is_extracted_at_once(big, request):
    path = big.tmp / "big.tex"
    para = "\\section{Part}\nWe fit the model by gradient descent and report the loss.\n\n"
    path.write_text(para * (2_000_000 // len(para)), encoding="utf-8")
    assert path.stat().st_size >= 1_900_000
    budget(request, measure(lambda _: text.extract(path, "text", name="big.tex"), warmup=1, runs=5), 400)


def test_a_window_of_a_64_mb_file_reads_at_once(big, request):
    path = big.tmp / "big.log"
    line = b"2026-11-12 10:00:00 worker 3 step 123456 loss 0.123 and some more words to fill the line\n"
    with open(path, "wb") as f:
        f.write(line * (64 * 1024 * 1024 // len(line)))
    total = 64 * 1024 * 1024 // len(line)
    window = text.read_lines(path, 1, 400)
    assert len(window.lines) == 400 and window.total == total
    budget(request, measure(lambda _: text.read_lines(path, 1, 400), warmup=1, runs=10), 60)
