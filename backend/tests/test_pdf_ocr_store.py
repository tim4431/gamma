"""The store of recognized text (gamma/pdf_ocr.py): a run's pages are staged,
read by nobody, until one apply makes them live together with a new stamp;
the finishing steps' flag; what a later run resumes from; rows of another
version read by nobody; the sweep; and how a reader's path is placed (a
workspace's stored PDF, a linked file, nothing else)."""

import os

import pymupdf
import pytest

from gamma import db, pdf_ocr

WS, DOC = "wocr", "a" * 24
# Two lines of stored words, in 1/10000 of the page as shown.
LINES = [[["Quantum", 1000, 1000, 3000, 1400], ["Zeno", 3200, 1000, 4500, 1400]],
         [["dynamics", 1000, 1600, 3500, 2000]]]
TEXT = "Quantum Zeno\ndynamics\n"


def _pdf(pages: int = 2) -> bytes:
    doc = pymupdf.open()
    for _ in range(pages):
        doc.new_page(width=612, height=792)
    return doc.tobytes()


def store(ws: str = WS, doc_id: str = DOC, data: bytes | None = None):
    """The file stored in the workspace's uploads, where the readers find it."""
    folder = db.ws_uploads_dir(ws)
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{doc_id}.pdf"
    path.write_bytes(data if data is not None else _pdf())
    return path


def test_the_page_text_rule():
    assert pdf_ocr.page_text(LINES) == TEXT, "every line ends in a line break, the last one too"
    assert pdf_ocr.page_text([]) == ""


def test_a_staged_page_is_read_by_nobody_until_the_apply(data_dir):
    path = store()
    pdf_ocr.set_scanned(WS, DOC, 2, 2)
    first = pdf_ocr.state(WS, DOC)
    assert (first["stamp"], first["rev"], first["pages"], first["scanned"], first["recognized"]) == ("", 0, 2, 2, [])
    assert pdf_ocr.stage_page(WS, DOC, 1, LINES, shared=0.5)
    assert pdf_ocr.state(WS, DOC)["staged"] == [1] and pdf_ocr.state(WS, DOC)["shared"] == {1: 0.5}
    assert pdf_ocr.view_of(str(path)) is None
    assert pdf_ocr.page_lines(WS, DOC, 1) is None
    assert pdf_ocr.pages_lines(WS, DOC, 1, 2) == {"stamp": "", "pages": {}}

    applied = pdf_ocr.apply(WS, DOC, "tesseract 5.5.4")
    assert applied["pages"] == [1] and applied["pending"] and applied["rev"] == 1 and len(applied["stamp"]) == 16
    view = pdf_ocr.view_of(str(path))
    assert (view.stamp, view.pages, view.total) == (applied["stamp"], {1}, 2)
    assert view.text(1) == TEXT and view.lines(1) == LINES and view.lines(2) is None
    state = pdf_ocr.state(WS, DOC)
    assert (state["recognized"], state["staged"], state["pending"], state["engine"]) == ([1], [], True,
                                                                                        "tesseract 5.5.4")
    assert pdf_ocr.page_lines(WS, DOC, 1) == LINES
    assert pdf_ocr.pages_lines(WS, DOC, 1, 2) == {"stamp": applied["stamp"], "pages": {1: LINES}}

    assert not pdf_ocr.finished(WS, DOC, "another stamp"), "an apply since: its own steps are still due"
    assert pdf_ocr.finished(WS, DOC, applied["stamp"]) and not pdf_ocr.state(WS, DOC)["pending"]
    assert pdf_ocr.apply(WS, DOC, "tesseract 5.5.4") is None, "nothing staged: no apply"
    assert not pdf_ocr.stage_page(WS, DOC, 1, [[["other", 0, 0, 10, 10]]]), "a live page is never restaged"
    assert pdf_ocr.page_lines(WS, DOC, 1) == LINES


def test_a_stopped_runs_pages_wait_for_the_next_runs_apply(data_dir):
    path = store()
    pdf_ocr.set_scanned(WS, DOC, 2, 2)
    pdf_ocr.stage_page(WS, DOC, 1, LINES)            # the first run stops here
    assert pdf_ocr.view_of(str(path)) is None
    state = pdf_ocr.state(WS, DOC)
    assert state["recognized"] + state["kept"] + state["staged"] == [1], "what the next run skips"
    pdf_ocr.stage_page(WS, DOC, 2, LINES[:1])        # the next run goes on
    applied = pdf_ocr.apply(WS, DOC, "tesseract 5.5.4")
    assert applied["pages"] == [1, 2] and pdf_ocr.view_of(str(path)).pages == {1, 2}


def test_pages_that_keep_their_layer_change_nothing_anyone_reads(data_dir):
    path = store()
    pdf_ocr.set_scanned(WS, DOC, 2, 2)
    assert pdf_ocr.stage_page(WS, DOC, 1, LINES, shared=0.98, kept=True)
    applied = pdf_ocr.apply(WS, DOC, "tesseract 5.5.4")
    assert applied == {"stamp": "", "rev": 0, "pages": [], "pending": False}
    state = pdf_ocr.state(WS, DOC)
    assert (state["kept"], state["recognized"], state["shared"]) == ([1], [], {1: 0.98})
    assert pdf_ocr.view_of(str(path)) is None


def test_every_apply_takes_a_new_stamp_and_marks_the_index_stale(data_dir):
    from gamma.search import index
    from gamma.textnorm import INDEX_VERSION

    path = store()
    with db.connect_data_db(WS) as conn:
        index.store_docs(conn, [(DOC, [(1, "garbled layer")], {"format": "pdf"})])
        assert index.missing(conn, [DOC]) == []
    pdf_ocr.set_scanned(WS, DOC, 2, 2)
    pdf_ocr.stage_page(WS, DOC, 1, LINES)
    first = pdf_ocr.apply(WS, DOC, "tesseract 5.5.4")
    with db.connect_data_db(WS) as conn:
        assert index.missing(conn, [DOC]) == [DOC], "a crash before the re-index leaves it to the lazy indexer"
        index.store_docs(conn, [(DOC, [(1, "recognized")], {"format": "pdf"})])
        assert conn.execute("SELECT ver FROM text_fts_docs WHERE doc_id = ?", (DOC,)).fetchone()[0] == INDEX_VERSION
    pdf_ocr.stage_page(WS, DOC, 2, LINES)
    second = pdf_ocr.apply(WS, DOC, "tesseract 5.5.4")
    assert second["rev"] == 2 and second["stamp"] not in ("", first["stamp"]) and second["pages"] == [2]
    assert pdf_ocr.view_of(str(path)).pages == {1, 2}


def test_an_apply_before_any_search_made_the_index(data_dir):
    store()
    pdf_ocr.set_scanned(WS, DOC, 2, 2)
    pdf_ocr.stage_page(WS, DOC, 1, LINES)
    assert pdf_ocr.apply(WS, DOC, "tesseract 5.5.4")["pages"] == [1]


def test_a_page_needs_its_files_row_and_lines_of_words(data_dir):
    store()
    with pytest.raises(ValueError, match="set_scanned first"):
        pdf_ocr.stage_page(WS, DOC, 1, LINES)
    pdf_ocr.set_scanned(WS, DOC, 2, 2)
    with pytest.raises(ValueError):
        pdf_ocr.stage_page(WS, DOC, 1, [[["word", 1, 2]]])
    pdf_ocr.stage_page(WS, DOC, 1, [[["  ", 0, 0, 1, 1], ["Ze no", 9000, -5, 12000.4, 30], ("d", 5, 5, 1, 1, 96.0)],
                                    []])
    pdf_ocr.apply(WS, DOC, "tesseract 5.5.4")
    assert pdf_ocr.page_lines(WS, DOC, 1) == [[["Zeno", 9000, 0, 10000, 30], ["d", 1, 1, 5, 5]]], \
        "white space and empty words go, boxes stay on the page with their edges in order, a confidence goes"


def test_rows_of_another_version_are_read_by_nobody(data_dir, monkeypatch):
    path = store()
    pdf_ocr.set_scanned(WS, DOC, 2, 2)
    pdf_ocr.stage_page(WS, DOC, 1, LINES)
    pdf_ocr.apply(WS, DOC, "tesseract 5.5.4")
    monkeypatch.setattr(pdf_ocr, "VERSION", pdf_ocr.VERSION + 1)
    assert pdf_ocr.view_of(str(path)) is None
    assert pdf_ocr.state(WS, DOC)["recognized"] == []
    assert pdf_ocr.stage_page(WS, DOC, 1, LINES[:1]), "a page of the old version is recognized again"
    assert pdf_ocr.apply(WS, DOC, "tesseract 5.5.4")["pages"] == [1]
    assert pdf_ocr.view_of(str(path)).lines(1) == LINES[:1]


def test_the_sweep_drops_the_text_of_files_no_longer_kept(data_dir):
    store()
    for doc_id in (DOC, "b" * 24):
        pdf_ocr.set_scanned(WS, doc_id, 2, 2)
        pdf_ocr.stage_page(WS, doc_id, 1, LINES)
    with db.connect_data_db(WS) as conn:
        assert pdf_ocr.purge(conn, {DOC}) == 1
        conn.commit()
    assert pdf_ocr.state(WS, DOC)["staged"] == [1] and pdf_ocr.state(WS, "b" * 24) is None
    with db.connect_data_db(WS) as conn:
        assert conn.execute("SELECT COUNT(*) FROM pdf_ocr_pages WHERE doc_id = ?", ("b" * 24,)).fetchone()[0] == 0


def test_the_sweep_asks_the_store_of_the_recognized_files_no_block_carries_alone(data_dir):
    store()
    for doc_id in (DOC, "b" * 24, "c" * 24):
        pdf_ocr.set_scanned(WS, doc_id, 2, 2)
    asked = []

    def stored(doc_id):
        asked.append(doc_id)
        return doc_id == "c" * 24

    with db.connect_data_db(WS) as conn:
        assert pdf_ocr.purge(conn, {DOC}, stored) == 1
        conn.commit()
    assert sorted(asked) == ["b" * 24, "c" * 24], "a file a block carries is not asked about"
    assert pdf_ocr.state(WS, "b" * 24) is None and pdf_ocr.state(WS, "c" * 24) is not None


def test_a_deleted_files_text_is_dropped_alone(data_dir):
    store()
    for doc_id in (DOC, "b" * 24):
        pdf_ocr.set_scanned(WS, doc_id, 2, 2)
        pdf_ocr.stage_page(WS, doc_id, 1, LINES)
    pdf_ocr.drop(WS, ["b" * 24, "f" * 24])
    assert pdf_ocr.state(WS, "b" * 24) is None and pdf_ocr.state(WS, DOC)["staged"] == [1]
    with db.connect_data_db(WS) as conn:
        assert conn.execute("SELECT COUNT(*) FROM pdf_ocr_pages WHERE doc_id = ?", ("b" * 24,)).fetchone()[0] == 0
    pdf_ocr.drop("wnone", [DOC])
    assert not os.path.exists(db.ws_db_path("wnone", "data.db")), "a workspace without data.db is not given one"


def test_the_stamp_the_readers_read_now(data_dir):
    import sqlite3

    assert pdf_ocr.stamp(WS, DOC) == "" and not os.path.exists(db.ws_db_path(WS, "data.db"))
    store()
    pdf_ocr.set_scanned(WS, DOC, 2, 2)
    pdf_ocr.stage_page(WS, DOC, 1, LINES)
    assert pdf_ocr.stamp(WS, DOC) == "", "staged: nobody reads it yet"
    applied = pdf_ocr.apply(WS, DOC, "tesseract 5.5.4")
    assert pdf_ocr.stamp(WS, DOC) == applied["stamp"] != ""
    bare = sqlite3.connect(":memory:")  # a data.db opened without the schema
    assert pdf_ocr.current_stamp(bare, DOC) == ""


def test_a_range_past_sqlites_integers_reads_as_unreadable_not_an_error(data_dir):
    store()
    pdf_ocr.set_scanned(WS, DOC, 2, 2)
    assert pdf_ocr.pages_lines(WS, DOC, 10 ** 20, 10 ** 20) is None


def test_a_range_of_pages_is_read_in_one_snapshot_and_bounded(data_dir):
    store(data=_pdf(100))
    pdf_ocr.set_scanned(WS, DOC, 100, 100)
    for page in range(1, 101):
        pdf_ocr.stage_page(WS, DOC, page, [[[f"p{page}", 0, 0, 10, 10]]])
    stamp = pdf_ocr.apply(WS, DOC, "tesseract 5.5.4")["stamp"]
    found = pdf_ocr.pages_lines(WS, DOC, 10, 500)
    assert found["stamp"] == stamp and sorted(found["pages"]) == list(range(10, 10 + pdf_ocr.MAX_RANGE))
    view = pdf_ocr.view_of(str(db.ws_uploads_dir(WS) / f"{DOC}.pdf"))
    assert [view.text(n) for n in (1, 50, 100)] == ["p1\n", "p50\n", "p100\n"]


# --- Placing a reader's path ---------------------------------------------------

def _recognized(path=None, ws=WS, doc_id=DOC):
    path = path or store(ws, doc_id)
    pdf_ocr.set_scanned(ws, doc_id, 2, 2)
    pdf_ocr.stage_page(ws, doc_id, 1, LINES)
    pdf_ocr.apply(ws, doc_id, "tesseract 5.5.4")
    return path


def test_only_a_workspaces_stored_pdf_is_placed_by_its_path(data_dir, tmp_path):
    path = _recognized()
    assert pdf_ocr.view_of(path) is not None and pdf_ocr.view_of(str(path)) is not None
    assert pdf_ocr.view_of(path.read_bytes()) is None, "bytes have no recognized text"
    elsewhere = tmp_path / "copy" / WS / "uploads" / f"{DOC}.pdf"  # the same names, outside the data directory
    elsewhere.parent.mkdir(parents=True)
    elsewhere.write_bytes(path.read_bytes())
    assert pdf_ocr.view_of(str(elsewhere)) is None
    assert pdf_ocr.view_of(str(path.with_name(f"{DOC}.txt"))) is None
    assert pdf_ocr.view_of(str(path.parent / ".." / "uploads" / path.name)) is not None, "a path is a path"


def test_a_workspace_without_data_db_is_not_given_one(data_dir):
    path = store("wnew")
    assert pdf_ocr.view_of(str(path)) is None
    assert pdf_ocr.state("wnew", DOC) is None
    assert not os.path.exists(db.ws_db_path("wnew", "data.db"))


def _link(ws: str, name: str, path) -> None:
    st = os.stat(path)
    with db.connect_vaults_db() as conn:
        conn.execute("INSERT INTO linked_files (ws, name, path, size, mtime_ns) VALUES (?, ?, ?, ?, ?)",
                     (ws, name, str(path), st.st_size, st.st_mtime_ns))
        conn.commit()


def test_a_linked_file_is_placed_through_vaults_db(data_dir, tmp_path, monkeypatch):
    linked = tmp_path / "Papers" / "scan.pdf"
    linked.parent.mkdir()
    linked.write_bytes(_pdf())
    db.ws_uploads_dir(WS).mkdir(parents=True)
    _recognized(linked)
    assert pdf_ocr.view_of(str(linked)) is None, "a server that keeps no links reads no vaults.db"
    monkeypatch.setenv("GAMMA_FOLDER_LINKS", "1")
    assert pdf_ocr.view_of(str(linked)) is None and not os.path.exists(db.VAULTS_DB), "nor makes one"
    _link(WS, f"{DOC}.pdf", linked)
    assert pdf_ocr.view_of(str(linked)).pages == {1}
    stat = linked.stat()
    os.utime(linked, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000))
    assert pdf_ocr.view_of(str(linked)) is None, "changed since it was hashed: not the file the text describes"
    os.utime(linked, ns=(stat.st_atime_ns, stat.st_mtime_ns))
    _link("wother", f"{'c' * 24}.pdf", linked)
    assert pdf_ocr.view_of(str(linked)) is None, "linked in two workspaces: the path alone cannot tell which"


def test_the_path_index_exists(data_dir, monkeypatch):
    monkeypatch.setenv("GAMMA_FOLDER_LINKS", "1")
    with db.connect_vaults_db() as conn:
        plan = " ".join(r[-1] for r in conn.execute(
            "EXPLAIN QUERY PLAN SELECT ws, name FROM linked_files WHERE path = ?", ("x",)))
    assert "idx_linked_files_path" in plan
