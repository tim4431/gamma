"""A text file's ranges placed again when the file changes (routers/pages.py
``replace_attachment``, gamma/documents/text_anchors.py ``relocate``,
docs/dev/highlight_relocation.md "Text files"): a library text document
replaced through PUT /attachment and a work folder's file rewritten on disk
have their ranges found by their quotes at once, before the replacement
answers; a large file by the ``relocate-highlights`` job; the run refuses
bytes that changed under it, and a file that turned binary or listed starts
nothing. ``highlight_relocate._write`` serves both runs, its words by the
position key. The pure ladder is tests/test_text_anchors.py's."""

import io
import os
import time
import uuid

import pytest

from conftest import account_of, guest_name, login, make_folder, make_user, mark_work_folder, workspace_of
from gamma import folder_links, gamma_sync, jobs, pdf_meta, storage
from gamma import highlight_relocate as hr
from gamma.blocks_store import new_block_id
from gamma.db import connect_pages_db
from gamma.documents import text_anchors
from gamma.documents.model import stored_name
from gamma.highlights import span_text, text_position
from gamma.ops import commit_ops
from gamma.routers import pages as pages_router
from gamma.vault import disk as vault_disk, kinds, store as vault_store
from gamma.vault.local_remote import LocalRemote
from test_vault_sync import Vault, _pdf

DEFAULTS = {"min_match": hr.ROUGH_APPLY, "exact_chars": hr.ROUGH_MIN_CHARS}
NOTHING = {"format": "text", "highlights": 0, "moved": 0, "kept": 0, "approx": 0, "lost": 0, "changed": 0, **DEFAULTS}

INTRO = "Quantum dots confine single electrons in a small island."
SPECTRUM = "The spectrum shows a doublet at the resonance frequency."
FIRST_ONLY = "This sentence is only in the first version of the paper."
MAIN = ["\\documentclass{article}", "\\begin{document}", "\\section{Intro}", INTRO, SPECTRUM, FIRST_ONLY,
        "\\end{document}"]
ABOVE = ["% a preamble added", "\\usepackage{amsmath}", "\\usepackage{graphicx}"]


def _bytes(lines) -> bytes:
    return ("\n".join(lines) + "\n").encode("utf-8")


def _own(lines) -> list:
    """``lines`` with a last line no other file has: two library pages never
    share a file, and a test that rewrites a stored file rewrites its own."""
    return [*lines, f"% {uuid.uuid4().hex}"]


def _store(client, data, name="main.tex") -> str:
    """``data`` stored as a file of ``name``: its stored name."""
    r = client.post("/api/upload-file", files={"file": (name, io.BytesIO(data), "application/octet-stream")})
    assert r.status_code == 200, r.text
    return r.json()["url"].rsplit("/", 1)[-1]


def _range(lines, span, hash):
    """A text range's properties over ``lines`` at ``span`` measured on ``hash``."""
    quote, prefix, suffix = span_text(lines, span)
    return {"text_position": text_position(*span, hash=hash, prefix=prefix, suffix=suffix), "quote": quote,
            "color": "#ffe28fa6"}


def _add(client, page_id, props) -> str:
    r = client.post("/api/blocks", json={"parent_id": page_id, "content": "", "properties": props})
    assert r.status_code == 200, r.text
    return r.json()["id"]


def _text_page(client, lines, spans, name="main.tex"):
    """A library page carrying ``lines`` as the text file ``name`` with a
    range at each of ``spans`` (and a last line of its own, ``_own``):
    ``(page_id, its file's hash, [block ids])``."""
    lines = _own(lines)
    stored = _store(client, _bytes(lines), name)
    page = client.post("/api/pages", json={"title": "Paper source"}).json()
    r = client.post(f"/api/pages/{page['id']}/attachment", json={"file": stored, "original_filename": name})
    assert r.status_code == 200, r.text
    doc = r.json()["properties"]["doc"]
    assert doc["format"] == "text", doc
    return page["id"], doc["hash"], [_add(client, page["id"], _range(lines, span, doc["hash"])) for span in spans]


def _replace(client, page_id, lines, name="main.tex"):
    """The page's file replaced by ``lines`` (and a last line of its own):
    ``(the new hash, the PUT's answer)``."""
    data = lines + _bytes(_own([])) if isinstance(lines, bytes) else _bytes(_own(lines))
    stored = _store(client, data, name)
    r = client.put(f"/api/pages/{page_id}/attachment", json={"file": stored, "original_filename": name})
    assert r.status_code == 200, r.text
    return os.path.splitext(stored)[0], r.json()


def _props(client, block_id):
    r = client.get(f"/api/blocks/{block_id}")
    assert r.status_code == 200, r.text
    return r.json()["properties"]


def _done(client, job):
    assert job["kind"] == "relocate-highlights", job
    finished = jobs.wait(job["id"])
    assert finished["state"] == "done", finished
    return client.get(f"/api/jobs/{job['id']}").json()["result"]


SPANS = [(4, 0, 4, 37), (5, 21, 5, 45), (6, 0, 6, len(FIRST_ONLY))]  # "Quantum…electrons", "doublet…resonance", the line


# --- PUT /attachment: a library text document --------------------------------------------

def test_replacing_a_text_file_places_its_ranges_before_it_answers(guest):
    page_id, old, (intro, spectrum, gone) = _text_page(guest, MAIN, SPANS)
    new_lines = MAIN[:1] + ABOVE + MAIN[1:5] + MAIN[6:]
    new, body = _replace(guest, page_id, new_lines)
    assert body["properties"]["doc"]["hash"] == new and "job" not in body and "relocation_error" not in body
    assert body["relocation"] == {**NOTHING, "highlights": 3, "moved": 2, "lost": 1}
    a = _props(guest, intro)
    assert a["quote"] == "Quantum dots confine single electrons"
    assert {k: a["text_position"][k] for k in ("line", "col", "end", "end_col", "hash")} == {
        "line": 7, "col": 0, "end": 7, "end_col": 37, "hash": new}
    assert a["text_position"]["prefix"].endswith("\\section{Intro}\n") and "lost" not in a["text_position"]
    b = _props(guest, spectrum)["text_position"]
    assert (b["line"], b["col"], b["end"], b["end_col"], b["hash"]) == (8, 21, 8, 45, new)
    c = _props(guest, gone)
    # lost and kept: its lines and the version it was measured on as they were, its quote the old one's
    assert c["text_position"]["lost"] is True and c["text_position"]["hash"] == old
    assert (c["text_position"]["line"], c["text_position"]["end"]) == (6, 6) and c["quote"] == FIRST_ONLY
    assert jobs.latest(account_of(guest_name()), "relocate-highlights", f"{page_id}:{new}") is None
    # the same file again: nothing changes, nothing runs
    again = guest.put(f"/api/pages/{page_id}/attachment",
                      json={"file": stored_name(new, ".tex"), "original_filename": "main.tex"}).json()
    assert "relocation" not in again and "job" not in again


def test_a_range_rewritten_in_place_is_drawn_dashed_with_where_it_was(guest):
    page_id, old, (_intro, spectrum, _gone) = _text_page(guest, MAIN, SPANS)
    reworded = SPECTRUM.replace("doublet at the resonance", "doublet near the resonance")
    new, body = _replace(guest, page_id, MAIN[:4] + [reworded] + MAIN[5:])
    assert body["relocation"] == {**NOTHING, "highlights": 3, "kept": 2, "approx": 1}
    p = _props(guest, spectrum)
    pos = p["text_position"]
    assert pos["approx"] is True and 0.8 <= pos["match"] <= 0.99 and pos["hash"] == new and pos["line"] == 5
    assert pos["prev"] == {"hash": old, "line": 5, "col": 21, "end": 5, "end_col": 45, "quote": "doublet at the resonance"}
    assert p["quote"] == "doublet near the resonance"


def test_a_large_text_file_is_placed_by_the_job(guest, monkeypatch):
    monkeypatch.setattr(text_anchors, "TEXT_INLINE_BYTES", 1024)
    filler = [f"% line {i} of the filler that makes the file large" for i in range(40)]
    page_id, _old, (intro, _spectrum, _gone) = _text_page(guest, MAIN, SPANS)
    new, body = _replace(guest, page_id, filler + MAIN)
    assert "relocation" not in body and "relocation_error" not in body
    job = body["job"]
    assert job["params"] == {"page_id": page_id, "title": "Paper source", "doc_id": new, "format": "text", **DEFAULTS}
    assert jobs.latest(account_of(guest_name()), "relocate-highlights", f"{page_id}:{new}")["id"] == job["id"]
    assert _done(guest, job) == {**NOTHING, "highlights": 3, "moved": 3}
    assert _props(guest, intro)["text_position"]["line"] == 44


def test_a_mebibyte_file_starts_the_job(guest):
    page_id, _old, _ids = _text_page(guest, MAIN, SPANS[:1])
    filler = [f"% {i:07d} " + "x" * 60 for i in range(17_000)]
    data = _bytes(filler + MAIN)
    assert len(data) > text_anchors.TEXT_INLINE_BYTES * 2
    new, body = _replace(guest, page_id, data)
    assert body["job"]["params"]["format"] == "text" and "relocation" not in body
    assert _done(guest, body["job"]) == {**NOTHING, "highlights": 1, "moved": 1}


def test_bytes_that_changed_under_the_run_place_nothing(guest, monkeypatch):
    """The run hashes what it read: a file rewritten between the replacement
    and the read is not the version the ranges would be stamped with."""
    page_id, old, ids = _text_page(guest, MAIN, SPANS)
    real = text_anchors.relocate

    def rewritten(ws_, page, new_doc, **kw):
        path = storage.find_upload_file(stored_name(new_doc, ".tex"), ws_)
        with open(path, "wb") as f:
            f.write(_bytes(["something else entirely"]))
        return real(ws_, page, new_doc, **kw)

    monkeypatch.setattr(text_anchors, "relocate", rewritten)
    new, body = _replace(guest, page_id, ABOVE + MAIN)
    assert body["relocation_error"] == "the file changed while it was read" and "relocation" not in body
    assert body["properties"]["doc"]["hash"] == new  # the replacement stands
    for bid in ids:  # pending: measured on another version than the page's, no flag
        pos = _props(guest, bid)["text_position"]
        assert pos["hash"] == old and not any(k in pos for k in text_anchors.TEXT_FLAGS)


def test_a_text_file_that_turns_binary_or_listed_starts_nothing(guest):
    ws, owner = workspace_of(guest_name()), account_of(guest_name())
    page_id, _old, ids = _text_page(guest, MAIN, SPANS)
    before = [_props(guest, bid) for bid in ids]
    blob = b"\x00\x01binary\x00" * 40
    stored = _store(guest, blob, "main.bin")
    done = pages_router.replace_attachment(ws, page_id, os.path.splitext(stored)[0], "", "main.bin", actor=owner,
                                           owner=owner, format="binary", ext=".bin", size=len(blob))
    assert done["results"] and done["relocation"] is None and done["job"] is None
    assert done["relocation_error"] == ""
    assert [_props(guest, bid) for bid in ids] == before

    page_id, old, ids = _text_page(guest, MAIN, SPANS, name="other.tex")
    before = [_props(guest, bid) for bid in ids]
    done = pages_router.replace_attachment(ws, page_id, "", "", "other.tex", at="listed", actor=owner, owner=owner,
                                           format="text", ext=".tex", size=60_000_000)
    assert done["results"] and done["relocation"] is None and done["job"] is None
    assert done["relocation_error"] == pages_router.LISTED
    assert [_props(guest, bid) for bid in ids] == before
    assert all(p["text_position"]["hash"] == old for p in before)


def test_a_pdf_page_never_runs_the_text_ladder(guest, monkeypatch):
    def refuse(*a, **kw):
        raise AssertionError("the text run started for a PDF")

    monkeypatch.setattr(text_anchors, "relocate", refuse)
    page = guest.post("/api/pages", json={"title": "A PDF"}).json()
    r = guest.post("/api/uploads", files={"file": ("a.pdf", io.BytesIO(_pdf("first")), "application/pdf")})
    assert guest.post(f"/api/pages/{page['id']}/attachment", json={"doc_id": r.json()["doc_id"]}).status_code == 200
    # a text range on a PDF's page (made elsewhere) is not the PDF run's either
    _add(guest, page["id"], _range(MAIN, SPANS[0], "a" * 24))
    r = guest.post("/api/uploads", files={"file": ("b.pdf", io.BytesIO(_pdf("second")), "application/pdf")})
    body = guest.put(f"/api/pages/{page['id']}/attachment", json={"doc_id": r.json()["doc_id"]}).json()
    assert "relocation" not in body and "job" not in body


def test_the_twenty_first_replacement_in_a_pass_still_places(guest):
    pages = [_text_page(guest, MAIN, SPANS[:1], name=f"part{i}.tex") for i in range(21)]
    for i, (page_id, _old, _ids) in enumerate(pages):
        _new, body = _replace(guest, page_id, ABOVE + MAIN, name=f"part{i}.tex")
        assert body["relocation"] == {**NOTHING, "highlights": 1, "moved": 1}, i
    assert all(_props(guest, ids[0])["text_position"]["line"] == 7 for _p, _o, ids in pages)


# --- the write's compare-and-set -------------------------------------------------------------

def test_a_range_changed_or_deleted_meanwhile_is_left_as_it_is(guest, monkeypatch):
    ws, owner = workspace_of(guest_name()), account_of(guest_name())
    page_id, _old, (intro, spectrum, gone) = _text_page(guest, MAIN, SPANS)
    real = text_anchors.place_all

    def meanwhile(*a, **kw):
        placed = real(*a, **kw)
        r = guest.put(f"/api/blocks/{intro}", json={"properties": {"color": "#ff0000"}})
        assert r.status_code == 200, r.text
        commit_ops(ws, page_id, [{"op": "set", "id": spectrum,
                                  "props": {"text_position": {**_props(guest, spectrum)["text_position"],
                                                              "line": 5, "end_col": 44}}}], actor=owner)
        assert guest.delete(f"/api/blocks/{gone}").status_code == 200
        return placed

    monkeypatch.setattr(text_anchors, "place_all", meanwhile)
    _new, body = _replace(guest, page_id, ABOVE + MAIN)
    # the colour is no place: written; the moved one and the deleted one: left
    assert body["relocation"] == {**NOTHING, "highlights": 3, "moved": 1, "changed": 2}
    assert _props(guest, intro)["text_position"]["line"] == 7
    assert _props(guest, spectrum)["text_position"]["line"] == 5
    assert guest.get(f"/api/blocks/{gone}").status_code == 404


def test_the_writes_refusals_name_the_file_the_page_shows(guest):
    ws, owner = workspace_of(guest_name()), account_of(guest_name())
    page_id, old, ids = _text_page(guest, MAIN, SPANS[:1])
    with connect_pages_db(ws) as conn:
        items = text_anchors.page_ranges(conn, page_id)
    ops = [{"op": "set", "id": ids[0], "props": {"quote": "x"}}]
    counts = {**NOTHING, "highlights": 1, "kept": 1}

    def write(new_doc, key):
        return hr._write(ws, page_id, new_doc, items, ops, {ids[0]: "kept"}, dict(counts), actor=owner, key=key)

    with pytest.raises(ValueError, match="the page's file changed again"):
        write("b" * 24, "text_position")
    with pytest.raises(ValueError, match="the page's PDF changed again"):
        write("b" * 24, "pdf_position")
    assert guest.delete(f"/api/blocks/{page_id}").status_code == 200
    with pytest.raises(ValueError, match="choose Find highlights in this file$"):
        write(old, "text_position")
    with pytest.raises(ValueError, match="choose Find highlights in this PDF$"):
        write(old, "pdf_position")


# --- POST /jobs/relocate-highlights ----------------------------------------------------------

def test_finding_the_ranges_by_hand_starts_the_job(guest):
    page_id, old, (intro, *_rest) = _text_page(guest, MAIN, SPANS)
    # moved by hand to the wrong line of the same version: the run finds it again
    r = guest.put(f"/api/blocks/{intro}", json={"properties": {
        "text_position": {**_props(guest, intro)["text_position"], "line": 2, "end": 2, "end_col": 10,
                          "hash": "c" * 24}}})
    assert r.status_code == 200, r.text
    r = guest.post("/api/jobs/relocate-highlights", json={"page_id": page_id})
    assert r.status_code == 200, r.text
    assert r.json()["params"]["format"] == "text" and r.json()["params"]["doc_id"] == old
    assert _done(guest, r.json()) == {**NOTHING, "highlights": 3, "moved": 1, "kept": 2}
    assert _props(guest, intro)["text_position"]["line"] == 4

    bare, _hash, _ = _text_page(guest, MAIN, [], name="bare.tex")
    r = guest.post("/api/jobs/relocate-highlights", json={"page_id": bare})
    assert r.status_code == 400 and r.json()["detail"] == "the page has no highlights placed on its file"

    picture = guest.post("/api/pages", json={"title": "A picture"}).json()["id"]
    png = _store(guest, b"\x89PNG\r\n\x1a\n" + b"\x00" * 64, "a.png")
    assert guest.post(f"/api/pages/{picture}/attachment", json={"file": png}).status_code == 200
    r = guest.post("/api/jobs/relocate-highlights", json={"page_id": picture})
    assert r.status_code == 400 and r.json()["detail"] == "the page has no PDF or text file"


# --- a work folder's file rewritten on disk ------------------------------------------------

OWNER = "trl_owner"
TRACK_TEX = "*\n!*/\n!*.tex\n!*.pdf\n"


@pytest.fixture(scope="module")
def owner(client):
    make_user(OWNER, "pw")
    return login(OWNER, "pw")


@pytest.fixture
def quick(monkeypatch):
    monkeypatch.setenv("GAMMA_FOLDER_LINKS", "1")
    monkeypatch.setattr(kinds, "SETTLE_NOTE_S", 0)
    monkeypatch.setattr(gamma_sync, "SETTLE_S", 0)
    monkeypatch.setattr(vault_store, "MISSING_S", 0)
    monkeypatch.setattr(pdf_meta, "schedule", lambda ws, doc_id: None)
    monkeypatch.setattr(storage, "linked_moved", [])
    monkeypatch.setattr(storage, "LINKED_STAT_S", 0)
    monkeypatch.setattr(storage, "_stats", {})


class TexVault(Vault):
    """``test_vault_sync.Vault`` of this module's account, its folder a work
    folder's whose ``.gammaignore`` tracks ``.tex`` files."""

    def __init__(self, owner, root):
        self.owner, self.root = owner, root
        self.ws, self.user = workspace_of(OWNER), account_of(OWNER)
        self.folder = make_folder(owner, f"TR {uuid.uuid4().hex[:8]}")
        root.mkdir(parents=True, exist_ok=True)
        (root / ".gammaignore").write_text(TRACK_TEX, encoding="utf-8")
        marker = vault_disk.make_marker(root)
        self.link = {"id": uuid.uuid4().hex, "workspace_id": self.ws, "folder_id": self.folder, "path": str(root),
                     "pull": 1, "push": folder_links.PUSH_ALL, "created_by": self.user,
                     "state": {"vault": marker["id"]}, "kind": "folder", "remote_url": "", "library": 0}
        self.remote = LocalRemote(self.ws, self.folder, self.user)
        mark_work_folder(owner, self.folder)

    def props(self, page_id) -> dict:
        return self.blocks(page_id)[page_id]["props"]


@pytest.fixture
def tex(owner, tmp_path, quick):
    made = []

    def make(name="w"):
        v = TexVault(owner, tmp_path / name)
        made.append(v)
        return v
    yield make
    for v in made:
        vault_store.forget_link(v.link["id"])


def test_a_work_folders_file_rewritten_on_disk_has_its_ranges_placed_in_the_round(tex, owner):
    v = tex()
    v.write("main.tex", _bytes(MAIN), age=10)
    v.sync()
    page_id = v.page_of("main.tex")
    doc = v.props(page_id)["doc"]
    assert doc["format"] == "text" and doc["at"] == "path"
    ids = [_add(owner, page_id, _range(MAIN, span, doc["hash"])) for span in SPANS]
    v.write("main.tex", _bytes(MAIN[:1] + ABOVE + MAIN[1:]), age=5)
    v.sync()
    new = v.props(page_id)["doc"]["hash"]
    assert new != doc["hash"]
    lines = [(p["text_position"]["line"], p["text_position"]["hash"]) for p in map(lambda b: _props(owner, b), ids)]
    assert lines == [(7, new), (8, new), (9, new)]


def test_a_work_folders_file_that_turns_binary_keeps_its_ranges_pending(tex, owner):
    v = tex("binary")
    v.write("main.tex", _bytes(MAIN), age=10)
    v.sync()
    page_id = v.page_of("main.tex")
    old = v.props(page_id)["doc"]["hash"]
    bid = _add(owner, page_id, _range(MAIN, SPANS[0], old))
    before = _props(owner, bid)
    v.write("main.tex", b"\x00\x01\x02 not text any more \x00" * 20, age=5)
    v.sync()
    after = v.props(page_id)["doc"]
    assert after["hash"] != old
    assert _props(owner, bid) == before


# --- the cost on the vault's round ---------------------------------------------------------

@pytest.mark.perf
def test_fifty_inline_runs_take_under_a_second_and_a_half(guest, monkeypatch):
    """Fifty pages of 1,000 lines with ten ranges each, each file replaced
    once with lines added above: the fifty inline runs together, timed
    around ``text_anchors.relocate`` alone (the uploads and the PUTs are
    not the run's)."""
    ws, owner = workspace_of(guest_name()), account_of(guest_name())
    spent = []
    real = text_anchors.relocate

    def timed(*a, **kw):
        t = time.perf_counter()
        try:
            return real(*a, **kw)
        finally:
            spent.append(time.perf_counter() - t)

    monkeypatch.setattr(text_anchors, "relocate", timed)
    pages = []
    for p in range(50):
        lines = [f"\\item Line {i} of part {p}: the measured value was {i * 7 + p} units in run {i % 13}."
                 for i in range(1000)]
        page_id, hash, _ = _text_page(guest, lines, [], name=f"part{p}.tex")
        ops = [{"op": "insert", "id": new_block_id(), "parent": page_id, "content": "",
                "props": _range(lines, (n, 2, n + 1, 20), hash)} for n in range(50, 1000, 95)]
        commit_ops(ws, page_id, ops, actor=owner)
        pages.append((page_id, lines))
    for p, (page_id, lines) in enumerate(pages):
        _new, body = _replace(guest, page_id, ABOVE + lines, name=f"part{p}.tex")
        assert body["relocation"]["moved"] == 10, body.get("relocation")
    assert len(spent) == 50
    assert sum(spent) < 1.5, f"{sum(spent):.2f} s"
