"""A folder kept on disk before synced vaults, taken onto the engine at its
first round (gamma/vault_upgrade.py, docs/research/vaults.md "In steps" 4): a
directory the gamma-sync client wrote (notes changed on disk and not, a paper
and its PDF, a sub-page, a picture, a subfolder), its marker written and said
in the status, its files and directories in vaults.db, its state file gone,
its notes written once more by the lens's encoder and its changed note kept,
a kill at each step, push turned on afterwards, a folder kept with its PDFs
alone, one of another server, and ``gamma_id`` read where a file has it; and
what an older link may have left: notes it let go, a state file gone or held
open, nested or overlapping links, a PDF changed on both sides; a disk
unplugged during the upgrade, a state file read-only or held through a
removal, anchors other notes link to, a PDF annotated on disk whose page
leaves, a cloud placeholder, a kill past the commit point; an anchor asked of
a kept note, and a sub-page's note where the client put it."""

import base64
import json
import os
import secrets
import stat
import subprocess
import sys
import time

import pytest

from conftest import account_of, login, make_folder, make_page, make_user, workspace_of
from gamma import folder_links, gamma_sync, md_lens, vault_disk, vault_store, vault_sync
from gamma.db import connect_pages_db, connect_users_db
from gamma.integrations import create_token
from gamma.obsidian_export import anchor_marker
from gamma.ops import commit_ops
from test_folder_links import _create, _sync, disk, over_http  # noqa: F401: the disk and over_http fixtures
from test_pdf_export import _blank_pdf, _position

OWNER = "vu_owner"
SERVER = "http://testserver"
PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg==")
KEPT = "changed on this computer and not pushed; a force pull replaces it"


class Killed(BaseException):
    """The process dying at a step of the upgrade: nothing below catches it."""


@pytest.fixture(scope="module")
def owner(client):
    make_user(OWNER, "pw")
    return login(OWNER, "pw")


@pytest.fixture
def settled(monkeypatch):
    """Files the client wrote a moment ago read as settled, and a missing one
    removed at its second scan."""
    monkeypatch.setattr(vault_sync, "SETTLE_NOTE_S", 0)
    monkeypatch.setattr(gamma_sync, "SETTLE_S", 0)
    monkeypatch.setattr(vault_store, "MISSING_S", 0)


def _children(c, page_id, blocks):
    r = c.put(f"/api/blocks/{page_id}/children", json={"blocks": blocks})
    assert r.status_code == 200, r.text


def _lab(c, name) -> dict:
    """Folder ``name``: a paper with its PDF, a note and a highlight; a notes
    page with a sub-page; in ``Sub`` a page whose note shows a picture."""
    folder, sub = make_folder(c, name), make_folder(c, f"{name}/Sub")
    pdf = _blank_pdf(pages=1) + name.encode()
    up = c.post("/api/uploads", files={"file": ("p.pdf", pdf, "application/pdf")}).json()
    paper = make_page(c, "UP paper", {"doc_id": up["doc_id"], "original_filename": "p.pdf", "folders": [folder]})
    _children(c, paper["id"], [
        {"id": f"up-hl-{paper['id']}", "content": "why it matters", "children": [],
         "properties": {"quote": "the quoted words", "color": "rgba(255, 226, 143, 0.65)", "pdf_position": _position()}},
        {"id": f"up-n-{paper['id']}", "content": "a note on the paper", "properties": {}, "children": []}])
    plain = make_page(c, "UP plain", {"folders": [folder]})
    _children(c, plain["id"], [{"id": f"up-p-{plain['id']}", "content": "plain words", "properties": {}, "children": []}])
    child = make_page(c, "UP child", {"parent_page": plain["id"]})
    _children(c, child["id"], [{"id": f"up-c-{child['id']}", "content": "a child's words", "properties": {}, "children": []}])
    png = c.post("/api/upload-image", files={"file": ("p.png", PNG, "image/png")}).json()["url"].rsplit("/", 1)[-1]
    deep = make_page(c, "UP deep", {"folders": [sub]})
    _children(c, deep["id"], [{"id": f"up-d-{deep['id']}", "content": f"a figure ![pic](/api/uploads/{png})",
                               "properties": {}, "children": []}])
    return {"folder": folder, "sub": sub, "doc": up["doc_id"], "pdf": pdf, "paper": paper["id"], "plain": plain["id"],
            "child": child["id"], "deep": deep["id"], "png": png}


def _kept(lab, dest, token, *, local=True, notes=True) -> dict:
    """The lab's folder written into ``dest`` by the gamma-sync client (over
    HTTP to this server), as the desktop's own server wrote it when ``local``:
    its state file's ``files``."""
    args = ["init", str(dest), "--server", SERVER, "--folder", lab["folder"], "--token", token]
    assert gamma_sync.main(args + ([] if notes else ["--no-notes"])) == 0
    assert gamma_sync.main(["pull", str(dest), "--token", token]) == 0
    link = gamma_sync.Link.load(dest)
    if local:
        link.state["server"] = "local"
        link.save()
    return link.state["files"]


_made = []    # the tokens a test made, revoked after it: an account holds twenty at most


@pytest.fixture(autouse=True)
def _tokens_revoked():
    yield
    with connect_users_db() as conn:
        conn.execute("DELETE FROM integration_tokens WHERE id IN (SELECT value FROM json_each(?))", (json.dumps(_made),))
    _made.clear()


def _token(scope="read"):
    token = create_token(account_of(OWNER), workspace_of(OWNER), f"vu-{scope}", 1,
                         **({"scope": "write"} if scope == "write" else {}))
    _made.append(token["id"])
    return token


def _quiet(c, link_id):
    status = _sync(c, link_id)
    moved = {k: v for k, v in status["counts"].items() if k in ("added", "updated", "renamed", "removed", "sent") and v}
    assert status["last_error"] == "" and not moved, status
    return status


def _texts(ws, page_id) -> list:
    with connect_pages_db(ws) as conn:
        return [r[0] for r in conn.execute("SELECT content FROM unified_blocks WHERE page_id = ? AND id != ?",
                                           (page_id, page_id))]


def _md(dest) -> list:
    return sorted(p.relative_to(dest).as_posix() for p in dest.rglob("*.md") if ".gamma" not in p.parts)


def test_a_folder_kept_before_is_taken_onto_the_engine_at_its_first_round(owner, disk, over_http, settled):
    lab, dest, ws = _lab(owner, "UP lab"), disk / "Kept lab", workspace_of(OWNER)
    files = _kept(lab, dest, _token()["token"])
    assert {"UP paper.pdf", "UP paper.md", "UP plain.md", "UP plain/UP child.md", "Sub/UP deep.md",
            f"attachments/{lab['png']}"} <= set(files)
    plain = dest / "UP plain.md"                     # a person's line in one note; the others as the client wrote them
    plain.write_text(plain.read_text(encoding="utf-8") + "\nmy own line\n", encoding="utf-8")
    then = time.time() - 30
    os.utime(plain, (then, then))
    before = {rel: (dest / rel).read_bytes() for rel in ("UP paper.md", "UP paper.pdf", "UP plain.md")}
    assert b"gamma_id" in before["UP paper.md"]

    link = _create(owner, folder=lab["folder"], path=str(dest), push="pdfs")
    assert folder_links.older(folder_links.get_link(link["id"])) and not (dest / vault_disk.MARKER).exists()
    status = _sync(owner, link["id"])
    assert status["last_error"] == "" and status["upgraded_at"]
    assert (dest / vault_disk.MARKER).is_file() and not (dest / gamma_sync.STATE).exists()
    stored = folder_links.get_link(link["id"])
    assert not folder_links.older(stored) and stored["state"]["vault"] == vault_disk.read_marker(dest)["id"]
    assert status["kept"] == [{"path": "UP plain.md", "why": KEPT, "force": True}]
    assert status["counts"]["updated"] == 3 and status["counts"]["added"] == 0

    # its notes as the lens writes them, read back as their pages; what they held kept in the history
    text = (dest / "UP paper.md").read_text(encoding="utf-8")
    assert "gamma_id" not in text and "a note on the paper" in text and "> [!quote]" in text and not md_lens.parse(text).problem
    assert vault_store.history(link["id"], "UP paper.md")[0]["data"] == before["UP paper.md"]
    assert "gamma_id" not in (dest / "UP plain" / "UP child.md").read_text(encoding="utf-8")
    assert f"![[{lab['png']}|pic]]" in (dest / "Sub" / "UP deep.md").read_text(encoding="utf-8")   # the vault's habit
    assert plain.read_bytes() == before["UP plain.md"] and (dest / "UP paper.pdf").read_bytes() == before["UP paper.pdf"]
    rows = vault_store.files(link["id"])
    assert rows["UP paper.pdf"]["hash"] == lab["doc"] and rows["UP paper.pdf"]["page_id"] == lab["paper"]
    assert rows["UP paper.md"]["nodes"] and rows["UP plain.md"]["nodes"] is None and rows["UP plain.md"]["page_id"] == lab["plain"]
    assert rows[f"attachments/{lab['png']}"]["kind"] == "picture"
    assert {path: d["folder_id"] for path, d in vault_store.dirs(link["id"]).items()} == {"Sub": lab["sub"]}
    assert "my own line" not in " ".join(_texts(ws, lab["plain"]))
    status = _quiet(owner, link["id"])                 # the next round writes nothing, and keeps the person's note
    assert status["kept"] == [{"path": "UP plain.md", "why": KEPT, "force": True}] and "upgraded_at" in status

    # push turned on: a note the lens wrote is pushed; the person's, whose base names no block, waits for a force
    r = owner.patch(f"/api/folder-links/{link['id']}", json={"push": "everything"})
    assert r.status_code == 200 and r.json()["push"] == "everything"
    paper_md = dest / "UP paper.md"
    paper_md.write_text(paper_md.read_text(encoding="utf-8") + "\na line from disk\n", encoding="utf-8")
    os.utime(paper_md, (then, then))
    status = _sync(owner, link["id"])
    assert status["counts"]["sent"] == 1 and "a line from disk" in _texts(ws, lab["paper"])
    assert status["kept"] == [{"path": "UP plain.md", "why": vault_sync.UNKNOWN, "force": True}]
    assert "my own line" not in " ".join(_texts(ws, lab["plain"]))

    # renamed on disk, it is still that page's, by the gamma_id it carries: never a second page
    pages = len(vault_sync.LocalRemote(ws, lab["folder"], account_of(OWNER)).layout().pages)
    plain.rename(dest / "Renamed plain.md")
    status = _sync(owner, link["id"])
    rows = vault_store.files(link["id"])
    assert "UP plain.md" not in rows and rows["Renamed plain.md"]["page_id"] == lab["plain"]
    assert len(vault_sync.LocalRemote(ws, lab["folder"], account_of(OWNER)).layout().pages) == pages
    assert status["kept"] == [{"path": "Renamed plain.md", "why": vault_sync.UNKNOWN, "force": True}]

    # a force pull makes it Gamma's page, with no gamma_id, its bytes in the history
    status = _sync(owner, link["id"], force="pull")
    renamed = (dest / "Renamed plain.md").read_text(encoding="utf-8")
    assert status["kept"] == [] and "plain words" in renamed and "my own line" not in renamed and "gamma_id" not in renamed
    assert b"my own line" in vault_store.history(link["id"], "Renamed plain.md")[0]["data"]
    _quiet(owner, link["id"])
    owner.delete(f"/api/folder-links/{link['id']}").raise_for_status()


@pytest.mark.parametrize("step", ["marker", "rows", "state"])
def test_a_kill_at_each_step_of_the_upgrade_leaves_the_old_state_or_the_new(owner, disk, over_http, settled, monkeypatch,
                                                                            step):
    lab, dest = _lab(owner, f"UP kill {step}"), disk / f"Killed {step}"
    _kept(lab, dest, _token()["token"])
    link = _create(owner, folder=lab["folder"], path=str(dest))
    take, keep_state = vault_store.take, folder_links.keep_state

    def killed(*args, **kw):
        raise Killed()

    def kept_then_killed(*args, **kw):
        keep_state(*args, **kw)
        raise Killed()
    if step == "marker":                                   # the marker written, not the rows
        monkeypatch.setattr(vault_store, "take", killed)
    else:                                                  # the rows written, the state not; the state, not the file's removal
        monkeypatch.setattr(folder_links, "keep_state", killed if step == "rows" else kept_then_killed)
    with pytest.raises(Killed):
        folder_links.run_link(link["id"])
    assert (dest / vault_disk.MARKER).is_file() and (dest / gamma_sync.STATE).is_file()
    assert folder_links.older(folder_links.get_link(link["id"])) is (step != "state")

    monkeypatch.setattr(vault_store, "take", take)
    monkeypatch.setattr(folder_links, "keep_state", keep_state)
    monkeypatch.setattr(vault_disk, "STALE_TEMP_S", 0)     # the scan's sweep takes the state file a kill left
    status = _sync(owner, link["id"])
    assert status["last_error"] == "" and status["counts"]["updated"] == 4 and status["kept"] == []
    assert not (dest / gamma_sync.STATE).exists()
    stored = folder_links.get_link(link["id"])
    assert stored["state"]["vault"] == vault_disk.read_marker(dest)["id"]
    rows = vault_store.files(link["id"])
    assert all(row["nodes"] for row in rows.values() if row["kind"] == "notes") and len(rows) == 6
    _quiet(owner, link["id"])
    owner.delete(f"/api/folder-links/{link['id']}").raise_for_status()


def test_a_folder_kept_with_its_pdfs_alone_stays_so_until_its_notes_are_turned_on(owner, disk, over_http, settled):
    lab, dest = _lab(owner, "UP pdfs alone"), disk / "PDFs alone"
    assert set(_kept(lab, dest, _token()["token"], notes=False)) == {"UP paper.pdf"}
    link = _create(owner, folder=lab["folder"], path=str(dest), notes=False)
    assert link["notes"] is False
    status = _sync(owner, link["id"])
    assert status["upgraded_at"] and status["counts"]["added"] == 0 and _md(dest) == []
    _quiet(owner, link["id"])
    r = owner.patch(f"/api/folder-links/{link['id']}", json={"notes": True})
    assert r.status_code == 200 and r.json()["notes"] is True
    status = _sync(owner, link["id"])
    assert _md(dest) == ["Sub/UP deep.md", "UP child.md", "UP paper.md", "UP plain.md"], status
    assert status["counts"]["added"] == 5                  # the four notes, and the picture one shows
    _quiet(owner, link["id"])
    owner.delete(f"/api/folder-links/{link['id']}").raise_for_status()


def test_a_folder_of_another_server_kept_before_is_taken_onto_the_engine_over_http(owner, disk, over_http, settled):
    lab, dest, token = _lab(owner, "UP far"), disk / "Far kept", _token()
    _kept(lab, dest, token["token"], local=False)
    r = owner.post("/api/folder-links", json={"folder": lab["folder"], "path": str(dest), "remote_url": SERVER,
                                              "token": token["token"], "token_id": token["id"]})
    assert r.status_code == 201, r.text
    link = r.json()
    assert folder_links.older(folder_links.get_link(link["id"]))
    status = _sync(owner, link["id"])
    assert status["last_error"] == "" and status["upgraded_at"] and status["counts"]["updated"] == 4
    assert (dest / vault_disk.MARKER).is_file() and not (dest / gamma_sync.STATE).exists()
    assert {path: d["folder_id"] for path, d in vault_store.dirs(link["id"]).items()} == {"Sub": lab["sub"]}
    assert "gamma_id" not in (dest / "UP paper.md").read_text(encoding="utf-8")
    _quiet(owner, link["id"])
    owner.delete(f"/api/folder-links/{link['id']}", params={"files": "trash"}).raise_for_status()
    assert _md(dest) == [] and not (dest / "UP paper.pdf").exists()


def test_removing_a_folder_kept_before_with_its_files_takes_what_the_client_wrote(owner, disk, over_http, settled):
    lab, dest = _lab(owner, "UP removed"), disk / "Removed"
    _kept(lab, dest, _token()["token"])
    (dest / "mine.md").write_text("a person's note\n", encoding="utf-8")
    link = _create(owner, folder=lab["folder"], path=str(dest))
    owner.delete(f"/api/folder-links/{link['id']}", params={"files": "trash"}).raise_for_status()
    assert _md(dest) == ["mine.md"] and not (dest / "UP paper.pdf").exists()
    assert not (dest / gamma_sync.STATE).exists() and not (dest / vault_disk.MARKER).exists()
    assert sorted(p.name for p in (dest / ".gamma" / "trash").rglob("*.md")) == ["UP child.md", "UP deep.md", "UP paper.md",
                                                                                "UP plain.md"]
    kept = disk / "Kept as it is"                          # with its files kept, the directory stays as the client left it
    _kept(_lab(owner, "UP kept"), kept, _token()["token"])
    state = (kept / gamma_sync.STATE).read_bytes()
    link = _create(owner, folder=make_folder(owner, "UP kept"), path=str(kept))
    owner.delete(f"/api/folder-links/{link['id']}").raise_for_status()
    assert (kept / gamma_sync.STATE).read_bytes() == state and not (kept / vault_disk.MARKER).exists()


# --- counter-examples on one engine, a first set --------------------------------------------------

def _files_of(dest) -> list:
    return sorted(p.relative_to(dest).as_posix() for p in dest.rglob("*") if p.is_file() and ".gamma" not in p.parts
                  and p.name != gamma_sync.STATE)


def _props_of(ws, page_id) -> list:
    with connect_pages_db(ws) as conn:
        return [(r[0], json.loads(r[1] or "{}")) for r in conn.execute(
            "SELECT content, properties FROM unified_blocks WHERE page_id = ? AND id != ?", (page_id, page_id))]


def _notes_let_go(lab, dest, token) -> None:
    """The lab kept with its notes, the paper's changed on disk, then its notes
    turned off in the client (which it allowed): the client takes away the
    notes it wrote and leaves the changed one, which its state no longer
    names."""
    _kept(lab, dest, token)
    paper_md = dest / "UP paper.md"
    paper_md.write_text(paper_md.read_text(encoding="utf-8") + "\nmy line on the paper\n", encoding="utf-8")
    then = time.time() - 30
    os.utime(paper_md, (then, then))
    client = gamma_sync.Link.load(dest)
    client.state["notes"] = False
    client.save()
    assert gamma_sync.main(["pull", str(dest), "--token", token]) == 0
    assert "UP paper.md" not in gamma_sync.Link.load(dest).state["files"] and paper_md.is_file()


def test_a_note_left_beside_its_papers_pdf_is_that_pages_kept_never_merged_from_no_base(owner, disk, over_http, settled):
    """The paper's note pairs with a PDF whose page has no notes
    row. Merged from no base, every block of the page went in twice; its
    ``gamma_id`` names the page, so it is that page's notes, kept until a force
    decides."""
    lab, dest, ws = _lab(owner, "UP let go"), disk / "Let go", workspace_of(OWNER)
    _notes_let_go(lab, dest, _token()["token"])
    before = sorted(_texts(ws, lab["paper"]))
    link = _create(owner, folder=lab["folder"], path=str(dest), notes=False)
    try:
        _sync(owner, link["id"])
        assert owner.patch(f"/api/folder-links/{link['id']}", json={"push": "everything"}).status_code == 200
        for _ in range(2):
            status = _sync(owner, link["id"])
        assert sorted(_texts(ws, lab["paper"])) == before
        assert {"path": "UP paper.md", "why": vault_sync.UNKNOWN, "force": True} in status["kept"]
        assert "UP paper 2.md" not in _files_of(dest)
    finally:
        owner.delete(f"/api/folder-links/{link['id']}").raise_for_status()


def test_notes_turned_on_take_the_note_that_names_its_page_and_write_none_beside_it(owner, disk, over_http, settled):
    """A folder kept with its PDFs alone, the paper's note the
    person kept beside it: notes turned on, pull wrote ``UP paper 2.md``. The
    note that names the page by ``gamma_id`` is that page's notes, kept."""
    lab, dest = _lab(owner, "UP in the way"), disk / "In the way"
    _notes_let_go(lab, dest, _token()["token"])
    mine = (dest / "UP paper.md").read_bytes()
    link = _create(owner, folder=lab["folder"], path=str(dest), notes=False)
    try:
        _sync(owner, link["id"])
        assert owner.patch(f"/api/folder-links/{link['id']}", json={"notes": True}).status_code == 200
        for _ in range(2):
            status = _sync(owner, link["id"])
        assert "UP paper 2.md" not in _files_of(dest) and (dest / "UP paper.md").read_bytes() == mine
        assert vault_store.files(link["id"])["UP paper.md"]["page_id"] == lab["paper"]
        assert any(k["path"] == "UP paper.md" and k["force"] for k in status["kept"])
    finally:
        owner.delete(f"/api/folder-links/{link['id']}").raise_for_status()


def test_a_force_push_of_a_kept_older_note_keeps_its_highlight(owner, disk, over_http, settled):
    """The older client writes a highlight's note after its callout.
    A force push of that note, kept, replaced the highlight by blocks with no
    properties, and the next pull wrote the quote away. The page becomes the
    file: the highlight stays one with its note, the person's line is added,
    and the file is written again by the lens."""
    lab, dest, ws = _lab(owner, "UP force kept"), disk / "Force kept", workspace_of(OWNER)
    _kept(lab, dest, _token()["token"])
    paper_md = dest / "UP paper.md"
    assert "> the quoted words\n\nwhy it matters\n" in paper_md.read_text(encoding="utf-8")
    paper_md.write_text(paper_md.read_text(encoding="utf-8") + "\nmy own line on the paper\n", encoding="utf-8")
    then = time.time() - 30
    os.utime(paper_md, (then, then))
    before = _props_of(ws, lab["paper"])
    link = _create(owner, folder=lab["folder"], path=str(dest))
    try:
        _sync(owner, link["id"])
        assert owner.patch(f"/api/folder-links/{link['id']}", json={"push": "everything"}).status_code == 200
        assert _sync(owner, link["id"])["kept"][0]["why"] == vault_sync.UNKNOWN
        _sync(owner, link["id"], force="push", confirm=1)
        status = _sync(owner, link["id"])
        after = _props_of(ws, lab["paper"])
        highlights = [(text, props) for text, props in after if props.get("pdf_position")]
        assert [(text, props["quote"]) for text, props in highlights] == [("why it matters", "the quoted words")]
        assert highlights[0][1]["color"] == "rgba(255, 226, 143, 0.65)"
        assert sorted(text for text, _props in after) == ["a note on the paper", "my own line on the paper",
                                                          "why it matters"]
        assert len(after) == len(before) + 1
        text = paper_md.read_text(encoding="utf-8")
        assert "> [!quote]" in text and "the quoted words" in text and "gamma_id" not in text
        assert status["kept"] == []
        _quiet(owner, link["id"])
    finally:
        owner.delete(f"/api/folder-links/{link['id']}").raise_for_status()


def test_a_folder_kept_before_whose_state_file_is_gone_writes_nothing_beside_its_files(owner, disk, over_http, settled):
    """With no state file the upgrade read an empty vault and the
    first pull wrote every file a second time. The files that name their page
    (``gamma_id``) or hold its PDF are that page's, a note kept until a force
    decides."""
    lab, dest = _lab(owner, "UP no state"), disk / "No state"
    _kept(lab, dest, _token()["token"])
    link = _create(owner, folder=lab["folder"], path=str(dest))
    (dest / gamma_sync.STATE).unlink()
    before = _files_of(dest)
    try:
        status = _sync(owner, link["id"])
        assert status["last_error"] == "" and status["upgraded_at"] and status["counts"]["added"] == 0, status
        assert _files_of(dest) == before
        rows = vault_store.files(link["id"])
        assert rows["UP paper.pdf"]["page_id"] == lab["paper"] and rows["UP paper.md"]["page_id"] == lab["paper"]
        assert rows["UP plain/UP child.md"]["page_id"] == lab["child"]
        _sync(owner, link["id"])
        assert _files_of(dest) == before
    finally:
        owner.delete(f"/api/folder-links/{link['id']}").raise_for_status()


def test_a_scan_sweeps_no_state_file_below_the_vaults_root(owner, disk, over_http, settled, monkeypatch):
    """The sweep of Gamma's temporary names took any
    ``.gamma-sync.json`` below the vault, a nested older link's before its
    upgrade (which then wrote its directory a second time) and a gamma-sync
    directory's. Only the vault's own, at its root, is ever swept."""
    lab, other, cli = _lab(owner, "UP nest outer"), _lab(owner, "UP nest inner"), _lab(owner, "UP nest cli")
    token = _token()["token"]
    outer, inner = disk / "Nest outer", disk / "Nest outer" / "Inner"
    _kept(lab, outer, token)
    _kept(other, inner, token)
    _kept(cli, outer / "cli copy", token, local=False)
    then = time.time() - 600
    for state in (inner / gamma_sync.STATE, outer / "cli copy" / gamma_sync.STATE):
        os.utime(state, (then, then))
    monkeypatch.setattr(vault_disk, "STALE_TEMP_S", 0)
    a = _create(owner, folder=lab["folder"], path=str(outer))
    b = secrets.token_hex(8)
    with connect_users_db() as conn:          # a nested link made before synced vaults, as migration 48 leaves it
        row = conn.execute("SELECT * FROM folder_links WHERE id = ?", (a["id"],)).fetchone()
        values = dict(zip([d[1] for d in conn.execute("PRAGMA table_info(folder_links)")], row))
        values.update(id=b, folder_id=other["folder"], path=str(inner.resolve()), created_at=values["created_at"] + "z",
                      status="{}", state="{}")
        conn.execute(f"INSERT INTO folder_links ({', '.join(values)}) VALUES ({', '.join('?' * len(values))})",
                     list(values.values()))
    try:
        _sync(owner, a["id"])
        assert (inner / gamma_sync.STATE).is_file() and (outer / "cli copy" / gamma_sync.STATE).is_file()
        assert not (outer / gamma_sync.STATE).exists()
        inner_files = _files_of(inner)
        owner.delete(f"/api/folder-links/{a['id']}").raise_for_status()
        status = _sync(owner, b)
        assert status["last_error"] == "" and status["counts"]["added"] == 0 and _files_of(inner) == inner_files
    finally:
        owner.delete(f"/api/folder-links/{a['id']}")
        owner.delete(f"/api/folder-links/{b}").raise_for_status()


def test_two_folders_kept_before_one_inside_the_others_folder_each_keep_their_copies(owner, disk, over_http, settled):
    """Older links of a folder and of its subfolder in two
    directories each held full copies. At the upgrade the first moved its
    copies of the subfolder's notes into its trash, saying nothing. Two vaults
    that only pull each write their own copies."""
    lab = _lab(owner, "UP two copies")
    a, b = disk / "Copies A", disk / "Copies B"
    token = _token()["token"]
    _kept(lab, a, token)
    _kept({**lab, "folder": lab["sub"]}, b, token)
    before_a, before_b = _files_of(a), _files_of(b)
    la = _create(owner, folder=lab["folder"], path=str(a))
    lb = _create(owner, folder=lab["sub"], path=str(b))
    try:
        for _ in range(2):
            assert _sync(owner, la["id"])["counts"]["removed"] == 0
            assert _sync(owner, lb["id"])["counts"]["removed"] == 0
        assert _files_of(a) == before_a and _files_of(b) == before_b
        assert not (a / ".gamma" / "trash").exists()
    finally:
        owner.delete(f"/api/folder-links/{la['id']}").raise_for_status()
        owner.delete(f"/api/folder-links/{lb['id']}").raise_for_status()


def test_a_pdf_changed_on_disk_and_in_gamma_is_kept_until_a_force_decides(owner, disk, over_http, settled):
    """The older client keeps a PDF changed here and in Gamma; the
    first round pushed the disk's over Gamma's newer one. It is kept, as the
    client keeps it: Gamma's stands until the file changes again (a vault's
    rule, test_disk_push.py) or a force decides."""
    lab, dest = _lab(owner, "UP both pdf"), disk / "Both pdf"
    _kept(lab, dest, _token()["token"])
    disk_pdf = _blank_pdf(pages=2) + b"the disk's version"
    (dest / "UP paper.pdf").write_bytes(disk_pdf)
    then = time.time() - 30
    os.utime(dest / "UP paper.pdf", (then, then))
    up = owner.post("/api/uploads", files={"file": ("g.pdf", _blank_pdf(pages=3) + b"Gamma's", "application/pdf")}).json()
    owner.put(f"/api/pages/{lab['paper']}/attachment", json={"doc_id": up["doc_id"], "original_filename": "p.pdf"}
              ).raise_for_status()
    link = _create(owner, folder=lab["folder"], path=str(dest), push="pdfs")
    try:
        status = _sync(owner, link["id"])
        assert status["counts"]["sent"] == 0
        assert {"path": "UP paper.pdf", "why": vault_sync.BOTH_PDF, "force": True} in status["kept"]
        assert _sync(owner, link["id"])["counts"]["sent"] == 0       # the page's chip says why from then on
        assert vault_store.files(link["id"])["UP paper.pdf"]["problem"] == vault_sync.BOTH_PDF
        assert owner.get(f"/api/blocks/{lab['paper']}").json()["properties"]["doc_id"] == up["doc_id"]
        assert (dest / "UP paper.pdf").read_bytes() == disk_pdf
        _sync(owner, link["id"], force="pull", confirm=1)
        assert gamma_sync.doc_of(dest / "UP paper.pdf") == up["doc_id"]
        assert not any(k["path"] == "UP paper.pdf" for k in _sync(owner, link["id"])["kept"])
    finally:
        owner.delete(f"/api/folder-links/{link['id']}").raise_for_status()


def test_the_second_of_two_older_links_pushing_one_folders_pdfs_pulls_on_with_push_off(owner, disk, over_http, settled):
    """Two links on one folder made before synced vaults both push its PDFs;
    the later was stopped for good, neither pulled nor upgraded. Its push goes off,
    which the status says, and it is taken onto the engine."""
    lab = _lab(owner, "UP two pushing")
    a, b = disk / "Push A", disk / "Push B"
    token = _token()["token"]
    _kept(lab, a, token)
    _kept(lab, b, token)
    la = _create(owner, folder=lab["folder"], path=str(a), push="pdfs")
    lb = _create(owner, folder=lab["folder"], path=str(b))
    with connect_users_db() as conn:          # as migration 48 leaves two such links
        conn.execute("UPDATE folder_links SET push = 1 WHERE id = ?", (lb["id"],))
    try:
        _sync(owner, la["id"])
        status = _sync(owner, lb["id"])
        assert not status.get("stopped") and status["last_error"] == "" and status["upgraded_at"], status
        assert "pushes into too" in status["push_off"]
        assert folder_links.get_link(lb["id"])["push"] == folder_links.PUSH_OFF
        assert not folder_links.older(folder_links.get_link(lb["id"])) and not (b / gamma_sync.STATE).exists()
        assert "push_off" in _sync(owner, lb["id"])
    finally:
        owner.delete(f"/api/folder-links/{la['id']}").raise_for_status()
        owner.delete(f"/api/folder-links/{lb['id']}").raise_for_status()


def test_a_state_file_another_program_holds_is_left_to_the_sweep(owner, disk, over_http, settled, monkeypatch):
    """The state file open elsewhere failed the round after its
    commit point with the raw error, and ``upgraded_at`` was never said."""
    lab, dest = _lab(owner, "UP held"), disk / "Held"
    _kept(lab, dest, _token()["token"])
    link = _create(owner, folder=lab["folder"], path=str(dest))
    held = open(dest / gamma_sync.STATE, "rb")       # Windows: no delete while it is open
    try:
        status = _sync(owner, link["id"])
        assert status["last_error"] == "" and status["upgraded_at"] and status["folder_path"], status
        assert not folder_links.older(folder_links.get_link(link["id"]))
    finally:
        held.close()
    monkeypatch.setattr(vault_disk, "STALE_TEMP_S", 0)
    try:
        status = _sync(owner, link["id"])
        assert status["upgraded_at"] and not (dest / gamma_sync.STATE).exists()
    finally:
        owner.delete(f"/api/folder-links/{link['id']}").raise_for_status()


# --- counter-examples on one engine, a second set --------------------------------------------------

def _pages_of(folder) -> set:
    return set(vault_sync.LocalRemote(workspace_of(OWNER), folder, account_of(OWNER)).layout().pages)


@pytest.mark.parametrize("at", ["UP plain.md", "UP paper.pdf"])
def test_a_disk_unplugged_during_the_upgrades_pass_takes_nothing(owner, disk, over_http, settled, monkeypatch, at):
    """The disk pulled out while the upgrade looked at the client's
    files read as files not there: the upgrade committed a vault holding the
    rest, the next pull wrote the client's files a second time, and pushing
    everything made pages of them. A pass the disk left is offline, nothing
    taken, as a scan's is."""
    lab, dest = _lab(owner, f"UP unplug {at}"), disk / f"Unplug {at}"
    _kept(lab, dest, _token()["token"])
    plain = dest / "UP plain.md"
    plain.write_text(plain.read_text(encoding="utf-8") + "\nmy own line\n", encoding="utf-8")
    then = time.time() - 30
    os.utime(plain, (then, then))
    files, pages = _files_of(dest), _pages_of(lab["folder"])
    link = _create(owner, folder=lab["folder"], path=str(dest), push="pdfs")
    away, lstat = disk / f"Unplug {at} away", vault_disk.lstat

    def unplugged(root, rel):
        if rel == at and dest.exists():
            dest.rename(away)
        return lstat(root, rel)
    monkeypatch.setattr(vault_disk, "lstat", unplugged)
    try:
        status = _sync(owner, link["id"])
        monkeypatch.setattr(vault_disk, "lstat", lstat)
        assert status.get("offline") and status["last_error"], status
        assert folder_links.older(folder_links.get_link(link["id"])) and vault_store.files(link["id"]) == {}
        away.rename(dest)
        assert (dest / gamma_sync.STATE).is_file()
        status = _sync(owner, link["id"])
        assert status["last_error"] == "" and status["upgraded_at"] and status["counts"]["added"] == 0, status
        assert owner.patch(f"/api/folder-links/{link['id']}", json={"push": "everything"}).status_code == 200
        for _ in range(2):
            _sync(owner, link["id"])
        assert _files_of(dest) == files and _pages_of(lab["folder"]) == pages
        assert "my own line" in plain.read_text(encoding="utf-8")
    finally:
        owner.delete(f"/api/folder-links/{link['id']}").raise_for_status()


def test_a_state_file_held_open_through_a_removal_stops_it_and_goes_with_it(owner, disk, over_http, settled):
    """A state file the upgrade could not take away outlived the
    vault: removed with its files kept and made again there, the folder read
    as kept before from the months-old state, every note the engine wrote
    kept as changed on disk and a page's file written a second time. The
    removal takes it away first; one another program holds stops it, nothing
    done."""
    lab, dest = _lab(owner, "UP held through"), disk / "Held through"
    _kept(lab, dest, _token()["token"])
    state = dest / gamma_sync.STATE
    link = _create(owner, folder=lab["folder"], path=str(dest))
    held = open(state, "rb")
    try:
        assert _sync(owner, link["id"])["upgraded_at"] and state.is_file()
        r = owner.delete(f"/api/folder-links/{link['id']}")
        assert r.status_code == 409 and "open in another program" in r.json()["detail"], r.text
        assert folder_links.get_link(link["id"]) and (dest / vault_disk.MARKER).is_file()
    finally:
        held.close()
    owner.delete(f"/api/folder-links/{link['id']}").raise_for_status()
    assert not state.exists() and not (dest / vault_disk.MARKER).exists()
    again = _create(owner, folder=lab["folder"], path=str(dest))     # made again: the files the engine wrote are taken
    try:
        assert not folder_links.older(folder_links.get_link(again["id"]))
        status = _sync(owner, again["id"])
        assert status["last_error"] == "" and status["kept"] == [], status
        assert not [p for p in _files_of(dest) if " 2." in p], _files_of(dest)
        rows = vault_store.files(again["id"])
        assert rows["UP paper.pdf"]["page_id"] == rows["UP paper.md"]["page_id"] == lab["paper"]
        assert rows["UP plain.md"]["page_id"] == lab["plain"] and rows["Sub/UP deep.md"]["page_id"] == lab["deep"]
    finally:
        owner.delete(f"/api/folder-links/{again['id']}").raise_for_status()


def test_a_read_only_state_file_is_taken_away_by_the_upgrade_or_the_sweep(owner, disk, over_http, settled, monkeypatch):
    """The read-only attribute made every sweep fail silently, so
    the state file stayed for good."""
    lab, dest = _lab(owner, "UP read-only"), disk / "Read-only"
    _kept(lab, dest, _token()["token"])
    state = dest / gamma_sync.STATE
    os.chmod(state, stat.S_IREAD)
    link = _create(owner, folder=lab["folder"], path=str(dest))
    try:
        assert _sync(owner, link["id"])["upgraded_at"] and not state.exists()
        state.write_text("{}", encoding="utf-8")          # one a kill at the last step left, read-only too
        os.chmod(state, stat.S_IREAD)
        monkeypatch.setattr(vault_disk, "STALE_TEMP_S", 0)
        _sync(owner, link["id"])
        assert not state.exists()
    finally:
        if state.exists():
            os.chmod(state, stat.S_IREAD | stat.S_IWRITE)
        owner.delete(f"/api/folder-links/{link['id']}").raise_for_status()


def test_the_upgrades_rewrite_keeps_the_anchors_other_notes_link_to(owner, disk, over_http, settled):
    """An unchanged note written anew by the lens lost the anchor
    the older client gave a block another note links to, and a force push of
    that note, kept, made Gamma's block link a link to the page."""
    folder = make_folder(owner, "UP anchors")
    target = make_page(owner, "UP target", {"folders": [folder]})["id"]
    block = f"tb-{target}"
    _children(owner, target, [{"id": block, "content": "the target block", "properties": {}, "children": []}])
    rich = make_page(owner, "UP rich", {"folders": [folder]})["id"]
    link_text = f"a block link [[{block}]]"
    _children(owner, rich, [{"id": f"r-{rich}", "content": link_text, "properties": {}, "children": []}])
    dest = disk / "Anchors"
    _kept({"folder": folder}, dest, _token()["token"])
    marker = f"^{anchor_marker(block)}"
    assert marker in (dest / "UP target.md").read_text(encoding="utf-8")
    note = dest / "UP rich.md"
    note.write_text(note.read_text(encoding="utf-8") + "\nmy own line\n", encoding="utf-8")
    then = time.time() - 30
    os.utime(note, (then, then))
    link = _create(owner, folder=folder, path=str(dest))
    try:
        _sync(owner, link["id"])
        target_text = (dest / "UP target.md").read_text(encoding="utf-8")
        assert marker in target_text and "gamma_id" not in target_text
        assert owner.patch(f"/api/folder-links/{link['id']}", json={"push": "everything"}).status_code == 200
        _sync(owner, link["id"])
        _sync(owner, link["id"], force="push", confirm=1)
        _sync(owner, link["id"])
        assert link_text in _texts(workspace_of(OWNER), rich) and "my own line" in _texts(workspace_of(OWNER), rich)
        assert marker in (dest / "UP target.md").read_text(encoding="utf-8")
    finally:
        owner.delete(f"/api/folder-links/{link['id']}").raise_for_status()


@pytest.mark.parametrize("how", ["trash", "move"])
def test_a_pdf_annotated_in_a_pulled_folder_stays_where_it_is_when_its_page_leaves(owner, disk, over_http, settled, how):
    """The person's annotated copy of a paper went to the vault's
    trash when its page left the vault (deleted, or filed elsewhere), and was
    purged later. A PDF changed on disk stays where it is, no longer synced,
    and is reported, as the older client left it."""
    lab, dest = _lab(owner, f"UP annotated {how}"), disk / f"Annotated {how}"
    _kept(lab, dest, _token()["token"])
    annotated = _blank_pdf(pages=1) + b"my annotations, an hour of work"
    (dest / "UP paper.pdf").write_bytes(annotated)
    then = time.time() - 30
    os.utime(dest / "UP paper.pdf", (then, then))
    link = _create(owner, folder=lab["folder"], path=str(dest))     # pull only, as kept folders were
    try:
        _sync(owner, link["id"])
        if how == "trash":
            owner.delete(f"/api/blocks/{lab['paper']}").raise_for_status()
        else:
            owner.put(f"/api/blocks/{lab['paper']}", json={"properties": {"folders": [make_folder(owner, f"UP away {how}")]}}
                      ).raise_for_status()
        status = _sync(owner, link["id"])
        assert {"path": "UP paper.pdf", "why": vault_sync.LEFT, "force": False} in status["kept"], status
        assert (dest / "UP paper.pdf").read_bytes() == annotated and "UP paper.pdf" not in vault_store.files(link["id"])
        assert not list((dest / ".gamma").rglob("*.pdf")) and not (dest / "UP paper.md").exists()
        _sync(owner, link["id"])
        assert (dest / "UP paper.pdf").read_bytes() == annotated
    finally:
        owner.delete(f"/api/folder-links/{link['id']}").raise_for_status()


@pytest.mark.skipif(sys.platform != "win32", reason="Windows' offline attribute")
def test_a_cloud_placeholder_the_client_wrote_holds_back_no_upgrade_and_is_read_once_it_is_here(
        owner, disk, over_http, settled, monkeypatch):
    """A note of a folder on OneDrive whose bytes could not be had
    (the provider not running) failed every round: the upgrade read every
    note and PDF, a placeholder's too. It is taken by its stat, never read,
    and its hash is learnt once its bytes are here."""
    lab, dest = _lab(owner, "UP cloud"), disk / "Cloud"
    _kept(lab, dest, _token()["token"])
    plain = dest / "UP plain.md"
    before = plain.read_bytes()
    subprocess.run(["attrib", "+O", str(plain)], check=True, capture_output=True)
    read, reads = vault_disk.read, []

    def provider_down(root, rel):
        reads.append(rel)
        if os.stat(vault_disk.path(root, rel)).st_file_attributes & vault_disk.OFFLINE:
            raise OSError(362, "The cloud file provider is not running")
        return read(root, rel)
    monkeypatch.setattr(vault_disk, "read", provider_down)
    link = _create(owner, folder=lab["folder"], path=str(dest))
    try:
        status = _sync(owner, link["id"])
        assert status["last_error"] == "" and status["upgraded_at"], status
        assert not folder_links.older(folder_links.get_link(link["id"])) and "UP plain.md" not in reads
        assert vault_store.files(link["id"])["UP plain.md"]["hash"] == vault_sync.UNREAD
        assert "gamma_id" not in (dest / "UP paper.md").read_text(encoding="utf-8")     # the rest pulled
        subprocess.run(["attrib", "-O", str(plain)], check=True, capture_output=True)
        status = _sync(owner, link["id"])
        assert status["last_error"] == "" and status["kept"] == [] and plain.read_bytes() != before
        assert "gamma_id" not in plain.read_text(encoding="utf-8") and "plain words" in plain.read_text(encoding="utf-8")
        _quiet(owner, link["id"])
    finally:
        subprocess.run(["attrib", "-O", str(plain)], capture_output=True)
        owner.delete(f"/api/folder-links/{link['id']}").raise_for_status()


def test_a_kill_just_past_the_upgrades_commit_point_keeps_upgraded_at(owner, disk, over_http, settled, monkeypatch):
    """``upgraded_at`` lived only in the status written after the
    round, so a kill just after the row's state named the marker, or a first
    round that failed, lost it for good."""
    lab, dest = _lab(owner, "UP kill past"), disk / "Kill past"
    _kept(lab, dest, _token()["token"])
    link = _create(owner, folder=lab["folder"], path=str(dest))
    keep_state = folder_links.keep_state

    def kept_then_killed(*args, **kw):
        keep_state(*args, **kw)
        raise Killed()
    monkeypatch.setattr(folder_links, "keep_state", kept_then_killed)
    with pytest.raises(Killed):
        folder_links.run_link(link["id"])
    monkeypatch.setattr(folder_links, "keep_state", keep_state)
    try:
        status = _sync(owner, link["id"])
        assert status["last_error"] == "" and status["upgraded_at"], status
        assert _sync(owner, link["id"])["upgraded_at"] == status["upgraded_at"]
    finally:
        owner.delete(f"/api/folder-links/{link['id']}").raise_for_status()


@pytest.mark.parametrize("linked", ["before", "after"])
def test_an_anchor_asked_of_a_kept_older_note_leaves_its_base_naming_no_block(owner, disk, over_http, settled, linked):
    """A link to a block of an older note changed on disk, made in Gamma
    before the client wrote the folder or after the upgrade: the ask of an
    anchor wrote that note's base as one of no blocks, so the next push read
    every block of the file as new and the page held each twice, the note no
    longer kept. Its base keeps naming none until a force decides."""
    lab, dest, ws = _lab(owner, f"UP asked {linked}"), disk / f"Asked {linked}", workspace_of(OWNER)
    note = f"up-n-{lab['paper']}"
    link_to = {"op": "set", "id": note, "content": f"a note on the paper [[up-p-{lab['plain']}]]"}
    if linked == "before":
        commit_ops(ws, lab["paper"], [link_to], actor=account_of(OWNER))
    _kept(lab, dest, _token()["token"])
    plain = dest / "UP plain.md"
    plain.write_text(plain.read_text(encoding="utf-8") + "\nmy own line\n", encoding="utf-8")
    then = time.time() - 30
    os.utime(plain, (then, then))
    link = _create(owner, folder=lab["folder"], path=str(dest), push="pdfs")
    try:
        _sync(owner, link["id"])
        assert owner.patch(f"/api/folder-links/{link['id']}", json={"push": "everything"}).status_code == 200
        _sync(owner, link["id"])
        if linked == "after":
            commit_ops(ws, lab["paper"], [link_to], actor=account_of(OWNER))
            _sync(owner, link["id"])
        assert vault_store.files(link["id"])["UP plain.md"]["nodes"] is None
        plain.write_text(plain.read_text(encoding="utf-8") + "\nanother line\n", encoding="utf-8")
        os.utime(plain, (then + 5, then + 5))
        for _ in range(2):
            status = _sync(owner, link["id"])
        assert sorted(_texts(ws, lab["plain"])) == ["plain words"]
        assert plain.read_text(encoding="utf-8").count("plain words") == 1
        assert {"path": "UP plain.md", "why": vault_sync.UNKNOWN, "force": True} in status["kept"]
    finally:
        owner.delete(f"/api/folder-links/{link['id']}").raise_for_status()


@pytest.mark.parametrize("how", ["no state", "made again"])
def test_a_sub_pages_note_the_client_put_beside_its_parents_is_that_pages(owner, disk, over_http, settled, how):
    """The client keeps a sub-page's note in a directory named for its
    parent's (``UP plain/UP child.md``), where pull would put a new one at the
    top: a vault made over that directory with no state file, or made again
    over the files an upgraded one kept, wrote ``UP child.md`` beside it, left
    the client's file unread, and pushing everything then made it a second
    page. The note there is the page's, by its ``gamma_id`` or by the bytes
    pull would write."""
    lab, dest = _lab(owner, f"UP sub-page {how}"), disk / f"Sub-page {how}"
    _kept(lab, dest, _token()["token"])
    if how == "no state":
        (dest / gamma_sync.STATE).unlink()
    else:
        first = _create(owner, folder=lab["folder"], path=str(dest), push="pdfs")
        _sync(owner, first["id"])
        owner.delete(f"/api/folder-links/{first['id']}", params={"files": "keep"}).raise_for_status()
    files, pages = _files_of(dest), _pages_of(lab["folder"])
    link = _create(owner, folder=lab["folder"], path=str(dest), push="pdfs")
    try:
        _sync(owner, link["id"])
        assert vault_store.files(link["id"])["UP plain/UP child.md"]["page_id"] == lab["child"]
        assert owner.patch(f"/api/folder-links/{link['id']}", json={"push": "everything"}).status_code == 200
        for _ in range(2):
            _sync(owner, link["id"])
        assert _files_of(dest) == files and _pages_of(lab["folder"]) == pages
    finally:
        owner.delete(f"/api/folder-links/{link['id']}").raise_for_status()
