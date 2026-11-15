"""The annotation files below a work folder's ``.gamma/annotations/``
and beside a followed file (gamma/annotations/files.py,
docs/dev/annotations.md "The files"): written by every whole round and
within one hot pass of a comment, byte for byte the same on a quiet pass,
the ``.gamma/feedback/`` of before and the Sends' request files gone at the
first round, a highlight
without a thread listed, an entry on lines of a tracked source file naming
them, rewritten on a reply, a moved place and a resolve, emptied by the
last resolve, gone with the vault and when it stops being a work folder;
nothing written where the directory is not the vault's, below a junction,
or while the vault is paused or choosing its files; Gamma's stale
temporary files swept; a file a reader holds tried again and left for the
next pass. A vault of one file's beside the file, under its
name: written at its round, rewritten within a pass of a highlight or a
comment, never a vault's directory, not written inside a folder vault's,
and gone when the vault goes, follows its file elsewhere or is taken in."""

import json
import os
import shutil
import sys
import time

import pymupdf
import pytest

from conftest import account_of, doc_props, login, make_page, make_user, thread_on
from gamma import folder_links, gamma_sync, highlights, pdf_meta, storage
from gamma.vault import disk as vault_disk, kinds, store as vault_store
from gamma.blocks_store import new_block_id
from gamma.db import connect_pages_db, connect_vaults_db
from gamma.annotations import files, kept
from gamma.ops import commit_ops
from test_highlight_relocate import _position
from test_vault_routes import _create, _page_of, _put, _sync, _vault
from test_vault_sync import insert

OWNER = "fbf_owner"
WINDOWS = sys.platform == "win32"
AREA = {"x1": 66, "y1": 60, "x2": 260, "y2": 110}      # over the line of text
MOVED = {"x1": 66, "y1": 300, "x2": 260, "y2": 350}    # where a rebuild put it
JPEG = b"\xff\xd8\xff"


@pytest.fixture(scope="module")
def owner(client):
    make_user(OWNER, "pw")
    return login(OWNER, "pw")


@pytest.fixture
def disk(tmp_path, monkeypatch):
    """This server as the desktop app runs it, a place on "this computer",
    files settled at once, and no round or pass but the test's own: none in
    the background, none from the app's own tick (``due``) or hot loop
    (``HOT_S``: it runs beside the tests and must find nothing due), the
    feedback pass run by hand (``_pass``). The links and the marks a test
    leaves go with it."""
    monkeypatch.setenv("GAMMA_FOLDER_LINKS", "1")
    monkeypatch.setattr(folder_links, "run_in_background", lambda link_id, **kw: None)
    monkeypatch.setattr(folder_links, "due", lambda link: "")
    monkeypatch.setattr(folder_links, "HOT_S", 3600)
    monkeypatch.setattr(kinds, "SETTLE_NOTE_S", 0)
    monkeypatch.setattr(gamma_sync, "SETTLE_S", 0)
    monkeypatch.setattr(vault_store, "MISSING_S", 0)
    monkeypatch.setattr(pdf_meta, "schedule", lambda ws, doc_id: None)
    monkeypatch.setattr(storage, "linked_moved", [])
    monkeypatch.setattr(storage, "LINKED_STAT_S", 0)
    monkeypatch.setattr(storage, "_stats", {})
    monkeypatch.setattr(storage, "_offline", {})
    _clear_marks()
    yield tmp_path / "disk"
    for link in folder_links.all_links():
        if folder_links.inside(link["path"], str(tmp_path)):
            folder_links._forget(link)
    below = str(tmp_path).rstrip("\\/") + os.sep
    with connect_vaults_db() as conn:
        conn.execute("DELETE FROM linked_files WHERE substr(path, 1, ?) = ?", (len(below), below))
    with folder_links._dirty_guard:
        folder_links._dirty.clear()
    _clear_marks()


def _clear_marks():
    with files._guard:
        files._pages.clear()
        files._scopes.clear()


def _pass():
    """What the hot loop's tick does for the feedback files
    (``folder_links._annotations_pass`` for each work folder due, with no
    wait), once the kept version's worker is done (it stores a commented
    page's bytes, which the pictures read)."""
    assert kept.wait()
    links = [link for link in folder_links.all_links() if not link["remote_url"]]
    for ws, scope in files.due(0):
        folder_links._annotations_pass(ws, scope, links, files)


def _pdf(text: str) -> bytes:
    doc = pymupdf.open()
    doc.new_page().insert_text((72, 72), text)
    return doc.tobytes()


def _paper(owner, root, **body) -> dict:
    """A work folder of ``root`` holding ``main.pdf``, after its first
    round, which pushes and never pulls."""
    return _vault(owner, root, {"main.pdf": _pdf("the method defines the loss")}, pull=False, **body)


def _area(ws, page, rect, block_id=None) -> str:
    """An area highlight over ``rect`` of the page's first PDF page."""
    block_id = block_id or new_block_id()
    commit_ops(ws, page, [insert(page, "", {"pdf_position": _position(1, rect, area=True), "color": "#ffe28fa6"},
                                 block_id)], actor=account_of(OWNER))
    return block_id


def _here(root):
    return root / files.DIR


def _open(root) -> dict:
    return json.loads((_here(root) / "open.json").read_text(encoding="utf-8"))


def _footprint(root, here=None) -> dict:
    """Every file below ``here`` (a work folder's ``.gamma/annotations/``)
    with its time and bytes."""
    out = {}
    for top, _dirs, names in os.walk(here or _here(root)):
        for name in names:
            path = os.path.join(top, name)
            out[os.path.relpath(path, root)] = (os.stat(path).st_mtime_ns, open(path, "rb").read())
    return out


def _leftovers(root) -> list:
    return sorted(p.name for p in _here(root).rglob(f"{vault_disk.TEMP}*"))


def _link_dir(target, link):
    """``link`` standing for the directory ``target``: a junction on
    Windows, a symlink elsewhere (skipped where the account may make none)."""
    if WINDOWS:
        import _winapi

        _winapi.CreateJunction(str(target), str(link))
        return
    try:
        os.symlink(target, link, target_is_directory=True)
    except OSError:
        pytest.skip("this account may not make symlinks")


def _resolve(owner, thread_id: str, resolved: bool = True) -> None:
    r = owner.post(f"/api/comments/{thread_id}/resolve", json={"resolved": resolved})
    assert r.status_code == 200, r.text


def test_the_files_are_written_by_every_round_within_a_pass_of_a_comment_and_left_alone_while_nothing_changes(owner, disk):
    root = disk / "paper"
    link = _paper(owner, root)
    ws, w, page = link["workspace_id"], link["folder_id"], _page_of(link, "main.pdf")
    here = _here(root)
    assert folder_links.get_link(link["id"])["status"]["annotations"] == {"open": 0}
    assert (here / ".gitignore").read_bytes() == b"*\n" and _open(root)["entries"] == []
    assert "No open comments and no highlights." in (here / "open.md").read_text(encoding="utf-8")

    area = _area(ws, page, AREA)
    thread = thread_on(owner, page, area, "move the legend inside the plot")
    assert files.due(0) == [(ws, w)]
    _pass()
    assert files.due(0) == []
    doc = _open(root)
    assert (doc["owner"], doc["work_folder"]["id"], doc["count"]) == (OWNER, w, 1)
    [entry] = doc["entries"]
    assert (entry["id"], entry["file"], entry["kind"], entry["where"]["page"], entry["where"]["placed"]) == (
        thread["id"], "main.pdf", "area", 1, "exact")
    assert entry["picture"].startswith("img/") and (here / entry["picture"]).read_bytes()[:3] == JPEG
    assert [(c["by"], c["body"]) for c in entry["comments"]] == [(OWNER, "move the legend inside the plot")]
    text = (here / "open.md").read_text(encoding="utf-8")
    assert "## main.pdf" in text and f"![Page 1, the comment's region]({entry['picture']})" in text
    assert "> move the legend inside the plot" in text

    # nothing marked, then a whole round: every byte and every time stay as they are
    before = _footprint(root)
    _pass()
    assert _sync(owner, link["id"])["annotations"] == {"open": 1}
    assert _footprint(root) == before and _leftovers(root) == []


def test_a_thread_on_lines_of_a_source_file_is_an_entry_naming_them(owner, disk):
    """A work folder that tracks ``*.tex``: a range of ``main.tex`` and a
    thread on it give an entry with the file's own path and lines, no
    picture, and the version stays where it is read (nothing stored)."""
    root = disk / "paper"
    tex = "\\section{Method}\nWeight decay is the oldest remedy.\nThis note measures how much it helps.\n"
    link = _vault(owner, root, {".gammaignore": "*\n!*/\n!*.tex\n!*.pdf\n", "main.tex": tex}, pull=False)
    ws, page = link["workspace_id"], _page_of(link, "main.tex")
    with connect_pages_db(ws) as conn:
        doc = json.loads(conn.execute("SELECT properties FROM unified_blocks WHERE id = ?", (page,)).fetchone()[0])["doc"]
    assert doc["format"] == "text"
    lines = tex.split("\n")
    span = (3, 0, 3, len(lines[2]))
    quote, prefix, suffix = highlights.span_text(lines, span)
    block = new_block_id()
    commit_ops(ws, page, [insert(page, "", {"text_position": highlights.text_position(
        *span, hash=doc["hash"], prefix=prefix, suffix=suffix), "quote": quote}, block)], actor=account_of(OWNER))
    thread = thread_on(owner, page, block, "say by how much")
    _pass()
    [entry] = _open(root)["entries"]
    assert {k: entry[k] for k in ("id", "file", "format", "kind", "where", "source", "picture", "before")} == {
        "id": thread["id"], "file": "main.tex", "format": "text", "kind": "lines",
        "where": {"line": 3, "quote": "This note measures how much it helps.", "placed": "exact"},
        "source": {"file": "main.tex", "line": 3, "version": "current"}, "picture": None, "before": None}
    text = (_here(root) / "open.md").read_text(encoding="utf-8")
    assert "### 1. Line 3 · “This note measures how much it helps.”\n" in text
    assert "- source: `main.tex`, line 3\n" in text
    assert not storage.exists(ws, f"{doc['hash']}.tex")


def test_a_reply_a_moved_place_and_a_resolve_rewrite_the_files_and_the_last_resolve_empties_them(owner, disk):
    root = disk / "paper"
    link = _paper(owner, root)
    ws, page = link["workspace_id"], _page_of(link, "main.pdf")
    here = _here(root)
    area = _area(ws, page, AREA)
    thread = thread_on(owner, page, area, "move the legend")
    _pass()
    first = _open(root)["entries"][0]["picture"]

    r = owner.post(f"/api/pages/{page}/comments", json={"block_id": area, "thread_id": thread["id"], "body": "done in 3.1"})
    assert r.status_code == 201, r.text
    _pass()
    [entry] = _open(root)["entries"]
    assert [c["body"] for c in entry["comments"]] == ["move the legend", "done in 3.1"]
    assert "> done in 3.1" in (here / "open.md").read_text(encoding="utf-8")

    # the place moved (what a relocation does): the region is drawn again; the old picture stays one
    # pass for the file it replaced, then goes
    commit_ops(ws, page, [{"op": "set", "id": area, "props": {"pdf_position": _position(1, MOVED, area=True)}}],
               actor=account_of(OWNER))
    _pass()
    [entry] = _open(root)["entries"]
    assert entry["picture"] != first and (here / entry["picture"]).is_file() and (here / first).is_file()
    files.mark(ws, page)
    _pass()
    assert not (here / first).exists() and (here / entry["picture"]).is_file()

    _resolve(owner, thread["id"])
    _pass()
    doc = _open(root)
    assert doc["count"] == 0 and doc["entries"] == [] and doc["updated_at"] is None
    assert "No open comments and no highlights." in (here / "open.md").read_text(encoding="utf-8")
    files.mark(ws, page)
    _pass()
    assert list((here / "img").iterdir()) == []

    _resolve(owner, thread["id"], False)
    _pass()
    assert [e["id"] for e in _open(root)["entries"]] == [thread["id"]] and (here / entry["picture"]).is_file()


def test_the_files_go_with_the_vault_and_when_it_stops_being_a_work_folder(owner, disk):
    root = disk / "paper"
    link = _paper(owner, root)
    assert (_here(root) / "open.json").is_file()
    owner.delete(f"/api/folder-links/{link['id']}").raise_for_status()
    assert not _here(root).exists() and not (root / vault_disk.MARKER_DIR).exists()     # .gamma/ too, left empty

    # a work folder whose push came down, its files in the library: let go at its next round, its files with it
    other = disk / "library"
    link = _paper(owner, other, library=True)
    assert (_here(other) / "open.json").is_file()
    r = owner.patch(f"/api/folder-links/{link['id']}", json={"push": "pdfs"})
    assert r.status_code == 200, r.text
    status = _sync(owner, link["id"])
    assert "annotations" not in status and not _here(other).exists() and (other / vault_disk.MARKER).is_file()


def test_removing_the_files_never_enters_a_link_or_a_junction_below_them(tmp_path):
    root = tmp_path / "vault"
    here = root / files.DIR
    (here / "img").mkdir(parents=True)
    (here / "open.json").write_bytes(b"{}")
    (here / "img" / f"{'a' * 16}.jpg").write_bytes(JPEG)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / "precious.txt").write_bytes(b"keep me")
    _link_dir(elsewhere, here / "pictures")
    files.remove({"kind": "folder", "path": str(root)})
    assert not here.exists() and (root / vault_disk.MARKER_DIR).is_dir()
    assert (elsewhere / "precious.txt").read_bytes() == b"keep me"


def test_a_picture_that_cannot_be_drawn_takes_no_slot_of_the_cap_and_holds_no_mark(owner, disk, monkeypatch):
    root = disk / "paper"
    _paper(owner, root)
    monkeypatch.setattr(files, "PICTURES_PER_WRITE", 1)
    broken, fine, a, b = "0" * 16, "1" * 16, "2" * 16, "3" * 16

    def draw(key):
        return None if key == broken else JPEG + key.encode()
    # the broken one first in the order: the one behind it is drawn all the same, and nothing waits
    assert files._pictures(root, files.DIR, "", [broken, fine], draw) == ({fine}, 0)
    # one drawn is the cap: the next waits for the next pass, the broken one not
    assert files._pictures(root, files.DIR, "", [broken, a, b], draw) == ({fine, a}, 1)
    assert sorted(p.name for p in (_here(root) / "img").iterdir()) == [f"{fine}.jpg", f"{a}.jpg"]


def test_nothing_is_written_where_the_directory_is_not_the_vaults_below_a_junction_or_while_the_vault_rests(owner, disk):
    root = disk / "paper"
    link = _paper(owner, root)
    ws, w, page = link["workspace_id"], link["folder_id"], _page_of(link, "main.pdf")
    row, here = folder_links.get_link(link["id"]), _here(root)
    marker = (root / vault_disk.MARKER).read_bytes()
    before = _footprint(root)
    thread_on(owner, page, page, "the whole file")

    # the marker gone (a copy of the directory, an emptied one) or another vault's
    (root / vault_disk.MARKER).unlink()
    assert files.write(row) == {"open": 0, "error": "the directory has no vault marker: it is not the vault's any more"}
    (root / vault_disk.MARKER).write_bytes(json.dumps({"format": vault_disk.FORMAT, "id": "f" * 32, "notes_folder": ""}).encode())
    assert files.write(row) == {"open": 0, "error": "the directory is another vault's"}
    assert _footprint(root) == before
    (root / vault_disk.MARKER).write_bytes(marker)

    # below a junction: nothing is read or written through it
    elsewhere = disk / "elsewhere"
    elsewhere.mkdir()
    shutil.rmtree(here)
    _link_dir(elsewhere, here)
    written = files.write(row)
    assert written["open"] == 1 and "below a link" in written["error"]
    assert list(elsewhere.iterdir()) == []
    # rmdir takes a junction away; a symlink is a file to rmdir (ENOTDIR)
    os.rmdir(here) if WINDOWS else os.unlink(here)
    _pass()
    assert _open(root)["count"] == 1

    # paused: the mark is dropped, the files stay as they are
    owner.patch(f"/api/folder-links/{link['id']}", json={"paused": True}).raise_for_status()
    thread_on(owner, page, page, "while paused")
    assert files.due(0) == [(ws, w)]
    _pass()
    assert files.due(0) == [] and _open(root)["count"] == 1
    owner.patch(f"/api/folder-links/{link['id']}", json={"paused": False}).raise_for_status()

    # a work folder choosing its files: no files, the mark dropped
    chosen = disk / "choosing"
    _put(chosen / "a.md", "a\n")
    choosing = _create(owner, path=str(chosen), new_folder=True, push="everything", choose=True)
    with files._guard:
        files._scopes[(ws, choosing["folder_id"])] = time.monotonic()
    _pass()
    assert files.due(0) == [] and not _here(chosen).exists()

    # a page of no work folder marks nothing
    library = make_page(owner, "fbf library paper")["id"]
    thread_on(owner, library, library, "not feedback")
    assert files.due(0) == []


def test_gammas_stale_temporary_files_are_swept_and_a_write_going_on_stays(owner, disk):
    root = disk / "paper"
    link = _paper(owner, root)
    here = _here(root)
    then = time.time() - 3600
    old = [here / f"{vault_disk.TEMP}old.tmp", here / "img" / f"{vault_disk.TEMP}old.tmp"]
    for path in old:
        path.parent.mkdir(exist_ok=True)
        path.write_bytes(b"x")
        os.utime(path, (then, then))
    young = here / f"{vault_disk.TEMP}young.tmp"
    young.write_bytes(b"y")
    theirs = here / "mine.txt"
    theirs.write_bytes(b"z")
    os.utime(theirs, (then, then))
    assert _sync(owner, link["id"])["annotations"] == {"open": 0}
    assert not any(path.exists() for path in old) and young.exists() and theirs.exists()


def test_a_mark_resolved_beside_another_caller_is_found_by_both(owner, disk, monkeypatch):
    """The hot loop's tick runs ``due`` beside a caller of its own (the
    tests' pass, here): a mark taken by one is still there for the other
    until it is resolved, and a page marked again during the resolve keeps
    its newer mark."""
    root = disk / "paper"
    link = _paper(owner, root)
    ws, w, page = link["workspace_id"], link["folder_id"], _page_of(link, "main.pdf")
    real, seen, inner = files.connect_pages_db, [], []

    def connect(workspace):
        if not seen:
            seen.append(True)
            inner.append(files.due(0))      # the tick, between this call's snapshot and its resolve
            files.mark(ws, page)            # and a change that came meanwhile
        return real(workspace)
    monkeypatch.setattr(files, "connect_pages_db", connect)
    files.mark(ws, page)
    assert files.due(0) == [(ws, w)] and inner == [[(ws, w)]]
    files.settle(ws, w, time.monotonic())
    assert files.due(0) == [(ws, w)]        # the mark made during the resolve stands
    files.settle(ws, w, time.monotonic())
    assert files.due(0) == []


def test_a_file_a_reader_holds_is_tried_again_then_left_for_the_next_pass(owner, disk, monkeypatch):
    root = disk / "paper"
    link = _paper(owner, root)
    ws, w, page = link["workspace_id"], link["folder_id"], _page_of(link, "main.pdf")
    here = _here(root)
    monkeypatch.setattr(vault_disk, "REPLACE_RETRY_S", 0)
    real, refused = os.replace, []

    def held(src, dst):
        if os.path.basename(dst) == "open.json":
            refused.append(dst)
            raise PermissionError(13, "held by a reader")
        return real(src, dst)
    monkeypatch.setattr(os, "replace", held)
    thread_on(owner, page, page, "the whole file")
    _pass()
    assert len(refused) == vault_disk.REPLACE_ATTEMPTS and _open(root)["count"] == 0   # the old file stands
    assert files.due(0) == [(ws, w)] and _leftovers(root) == []                      # kept for the next pass
    assert "1 open comment" in (here / "open.md").read_text(encoding="utf-8")        # the rest was written
    assert _sync(owner, link["id"])["annotations"] == {"open": 1, "left": 1}            # no error of the vault's
    monkeypatch.setattr(os, "replace", real)
    _pass()
    assert _open(root)["count"] == 1 and files.due(0) == [] and _leftovers(root) == []


def test_what_a_work_folder_held_before_goes_at_the_first_round_and_with_the_vault(owner, disk):
    """``.gamma/feedback/`` (the files before step 3.2) and the Sends'
    ``.gamma/annotations/requests/`` (before step 3.7)."""
    root = disk / "paper"
    link = _paper(owner, root)
    feedback, requests = (root / rel for rel in files.FORMER)
    (feedback / "img").mkdir(parents=True)
    (feedback / "open.json").write_bytes(b"{}\n")
    (feedback / "img" / f"{'a' * 16}.jpg").write_bytes(JPEG)
    requests.mkdir(parents=True)
    (requests / "r1.json").write_bytes(b"{}\n")
    assert _sync(owner, link["id"])["annotations"] == {"open": 0}
    assert not feedback.exists() and not requests.exists() and (_here(root) / "open.json").is_file()
    (feedback / "requests").mkdir(parents=True)         # one the removal meets goes with the rest
    owner.delete(f"/api/folder-links/{link['id']}").raise_for_status()
    assert not (root / vault_disk.MARKER_DIR).exists()


def test_a_highlight_without_a_thread_is_listed_until_a_thread_is_opened_on_it(owner, disk):
    root = disk / "paper"
    link = _paper(owner, root)
    ws, page = link["workspace_id"], _page_of(link, "main.pdf")
    here = _here(root)
    area = _area(ws, page, AREA)
    assert files.due(0) == [(ws, link["folder_id"])]      # the op commit marked it
    _pass()
    doc = _open(root)
    [entry] = doc["entries"]
    assert (entry["id"], entry["type"], entry["kind"], entry["state"], entry["comments"]) == (
        area, "highlight", "area", None, [])
    assert doc["count"] == 1 and entry["picture"] and (here / entry["picture"]).is_file()
    text = (here / "open.md").read_text(encoding="utf-8")
    assert f"1 highlight · owner {OWNER}" in text and f"- id: `{area}` · highlight · area · placed exactly" in text
    assert f"![Page 1, the highlight's region]({entry['picture']})" in text
    thread = thread_on(owner, page, area, "move the legend")
    _pass()
    assert [(e["id"], e["type"]) for e in _open(root)["entries"]] == [(thread["id"], "thread")]
    assert _sync(owner, link["id"])["annotations"] == {"open": 1}


# --- a vault of one file ---------------------------------------------------------------

def _followed(owner, disk, name="main.pdf") -> tuple[dict, str, object]:
    """A library page that follows ``disk/build/<name>``, after its first
    round (the file's bytes the page's already: nothing sent): ``(the link,
    the page's id, the file)``."""
    data = _pdf(f"the {name} defines the loss")
    r = owner.post("/api/uploads", files={"file": ("paper.pdf", data, "application/pdf")})
    assert r.status_code == 200, r.text
    page = make_page(owner, f"fbf {name}", doc_props(r.json()["doc_id"], name="paper.pdf"))["id"]
    file = _put(disk / "build" / name, data)
    link = _create(owner, kind="file", page=page, path=str(file))
    assert _sync(owner, link["id"])["last_error"] == ""
    return folder_links.get_link(link["id"]), page, file


def _where(c, path) -> dict:
    r = c.post("/api/folder-links/where", json={"path": str(path)})
    assert r.status_code == 200, r.text
    return r.json()


def test_a_followed_files_annotations_are_written_beside_it_and_never_make_a_vault(owner, disk):
    link, page, file = _followed(owner, disk)
    ws, here = link["workspace_id"], file.parent / files.DIR / "main.pdf"
    assert link["status"]["annotations"] == {"open": 0}
    assert (here / ".gitignore").read_bytes() == b"*\n"
    doc = json.loads((here / "open.json").read_text(encoding="utf-8"))
    assert (doc["scope"], doc["work_folder"], doc["owner"], doc["entries"]) == (
        {"kind": "page", "id": page, "name": "fbf main.pdf"}, None, OWNER, [])
    assert (here / "open.md").read_text(encoding="utf-8").startswith("# Annotations on fbf main.pdf\n")

    # Gamma's own and hidden, never a vault: no marker, nothing of Gamma's to Open with Gamma, no vault of it
    assert os.listdir(file.parent / vault_disk.MARKER_DIR) == ["annotations"]
    assert _where(owner, file.parent)["is"] == "none"
    assert _where(owner, here)["refusal"] == folder_links.OWN_REFUSED
    r = owner.post("/api/folder-links", json={"path": str(here), "new_folder": True, "push": "everything"})
    assert r.status_code == 400 and r.json()["detail"] == folder_links.OWN_REFUSED

    # a highlight and a thread on the page: rewritten within a pass, named by the file's name
    area = _area(ws, page, AREA)
    thread = thread_on(owner, page, page, "the whole paper")
    assert files.due(0) == [(ws, page)]
    _pass()
    doc = json.loads((here / "open.json").read_text(encoding="utf-8"))
    assert [(e["id"], e["type"], e["file"]) for e in doc["entries"]] == [
        (thread["id"], "thread", "main.pdf"), (area, "highlight", "main.pdf")]
    assert (here / doc["entries"][1]["picture"]).is_file()
    assert "## main.pdf" in (here / "open.md").read_text(encoding="utf-8")

    # paused: a pass drops the mark and writes nothing; its next round writes
    owner.patch(f"/api/folder-links/{link['id']}", json={"paused": True}).raise_for_status()
    before = _footprint(file.parent, here)
    _resolve(owner, thread["id"])
    _pass()
    assert files.due(0) == [] and _footprint(file.parent, here) == before
    owner.patch(f"/api/folder-links/{link['id']}", json={"paused": False}).raise_for_status()
    assert _sync(owner, link["id"])["annotations"] == {"open": 0}

    # a second file of the directory has its own; a vault removed takes its own alone, the last one .gamma/ too
    other, _other_page, _ = _followed(owner, disk, "slides.pdf")
    assert (file.parent / files.DIR / "slides.pdf" / "open.json").is_file()
    owner.delete(f"/api/folder-links/{link['id']}").raise_for_status()
    assert not here.exists() and (file.parent / files.DIR / "slides.pdf").is_dir()
    owner.delete(f"/api/folder-links/{other['id']}").raise_for_status()
    assert not (file.parent / vault_disk.MARKER_DIR).exists() and file.is_file()


def test_a_followed_files_annotations_follow_it_and_go_when_a_folder_vault_takes_it_in(owner, disk):
    link, page, file = _followed(owner, disk)
    ws = link["workspace_id"]
    area = _area(ws, page, AREA)
    moved = _put(disk / "out" / "main.pdf", file.read_bytes())
    owner.post(f"/api/folder-links/{link['id']}/relink", json={"path": str(moved)}).raise_for_status()
    assert not (file.parent / vault_disk.MARKER_DIR).exists()     # gone from beside the file that was
    assert _sync(owner, link["id"])["annotations"] == {"open": 0}
    here = moved.parent / files.DIR / "main.pdf"
    assert [e["id"] for e in json.loads((here / "open.json").read_text(encoding="utf-8"))["entries"]] == [area]

    # a work folder made around it takes it in: the work folder's files list its page, and its own go
    work = _vault(owner, moved.parent)
    assert folder_links.get_link(link["id"]) is None and not here.exists()
    assert [(e["id"], e["file"]) for e in _open(moved.parent)["entries"]] == [(area, "main.pdf")]
    owner.delete(f"/api/folder-links/{work['id']}").raise_for_status()


def test_a_followed_file_a_folder_vault_could_not_take_in_writes_nothing_beside_it(owner, disk, monkeypatch):
    link, page, file = _followed(owner, disk)
    here = file.parent / files.DIR / "main.pdf"

    def held(*args):
        raise OSError("held by another program")
    monkeypatch.setattr(folder_links, "_take_in_one", held)
    work = _vault(owner, file.parent, {".gammaignore": "*\n!*/\n!*.md\n", "notes.md": "a thought\n"})
    assert folder_links.get_link(link["id"]) is not None          # it stays a vault of its own
    shutil.rmtree(here)
    thread_on(owner, page, page, "the whole paper")
    _pass()
    status = _sync(owner, link["id"])
    assert "annotations" not in status and not here.exists()
    assert (_here(file.parent) / "open.json").is_file()            # the work folder's own, which it writes
    owner.delete(f"/api/folder-links/{work['id']}").raise_for_status()
