"""A work folder's rounds (gamma/vault/sync.py, docs/dev/work_folders.md,
docs/dev/synced_vaults.md "A work folder's round"): a vault whose folder
carries the mark (conftest ``mark_work_folder``) stamps the pages it makes,
files and names them by their files on every move on disk, follows a
person's renames, moves, trash and restore of a note where it pulls, never
copies a PDF out nor moves one into its trash, makes no sub-page, stores a
library file's versions, writes nothing its ``.gammaignore`` leaves out,
stops when that file vanishes or the selection passes its cap, and writes an
older marker's ``ignore`` globs once as its ``.gammaignore``."""

import json
import os
import shutil
import uuid

import pytest

from conftest import account_of, doc_props, login, make_folder, make_page, make_user, mark_work_folder, workspace_of
from gamma import folder_links, gamma_sync, pdf_meta, storage
from gamma.vault import disk as vault_disk, kinds, store as vault_store, sync as vault_sync
from gamma.blocks_store import FOLDERS
from gamma.db import connect_pages_db
from gamma.documents.model import stored_name
from gamma.feedback import files as feedback_files
from gamma.ops import commit_ops, restore_page, trash_page
from gamma.vault.local_remote import LocalRemote
from gamma.work_folders import marks, takeover
from test_vault_sync import Vault, _pdf, insert

OWNER = "wfround_owner"


@pytest.fixture(scope="module")
def owner(client):
    make_user(OWNER, "pw")
    return login(OWNER, "pw")


@pytest.fixture(autouse=True)
def quick(monkeypatch):
    monkeypatch.setenv("GAMMA_FOLDER_LINKS", "1")
    monkeypatch.setattr(kinds, "SETTLE_NOTE_S", 0)
    monkeypatch.setattr(gamma_sync, "SETTLE_S", 0)
    monkeypatch.setattr(vault_store, "MISSING_S", 0)
    monkeypatch.setattr(pdf_meta, "schedule", lambda ws, doc_id: None)
    monkeypatch.setattr(storage, "linked_moved", [])
    monkeypatch.setattr(storage, "LINKED_STAT_S", 0)
    monkeypatch.setattr(storage, "_stats", {})


class WorkVault(Vault):
    """``test_vault_sync.Vault`` of this module's account, its folder marked
    as a work folder's (``pull``, ``library`` the mark's switches)."""

    def __init__(self, owner, root, *, pull=True, library=False, mark=True):
        self.owner, self.root = owner, root
        self.ws, self.user = workspace_of(OWNER), account_of(OWNER)
        self.folder = make_folder(owner, f"WF {uuid.uuid4().hex[:8]}")
        root.mkdir(parents=True, exist_ok=True)
        marker = vault_disk.make_marker(root)
        self.link = {"id": uuid.uuid4().hex, "workspace_id": self.ws, "folder_id": self.folder, "path": str(root),
                     "pull": int(pull), "push": folder_links.PUSH_ALL, "created_by": self.user,
                     "state": {"vault": marker["id"]}, "kind": "folder", "remote_url": "", "library": int(library)}
        self.remote = LocalRemote(self.ws, self.folder, self.user)
        if mark:
            mark_work_folder(owner, self.folder, pull=pull, library=library)

    def props(self, page_id) -> dict:
        return self.blocks(page_id)[page_id]["props"]

    def folder_of(self, directory) -> str:
        return vault_store.dirs(self.link["id"])[directory]["folder_id"]


@pytest.fixture
def work(owner, tmp_path):
    made = []

    def make(name="w", **kw):
        v = WorkVault(owner, tmp_path / name, **kw)
        made.append(v)
        return v
    yield make
    for v in made:
        vault_store.forget_link(v.link["id"])


def _made(owner, title, folder) -> str:
    """A page a person makes in a directory of a work folder (the server
    stamps it the work folder's)."""
    r = owner.post("/api/pages", json={"title": title, "folders": [folder]})
    assert r.status_code == 200, r.text
    return r.json()["id"]


def _stat(path):
    st = os.stat(path)
    return path.read_bytes(), st.st_mtime_ns, st.st_ino


def test_new_pages_are_stamped_files_of_the_work_folder(work):
    v = work("plain")
    v.write("Dir/a.md", "a note\n")
    paper = v.write("b.pdf", _pdf("b"), age=10)
    v.sync()
    note, pdf = v.page_of("Dir/a.md"), v.page_of("b.pdf")
    props = v.props(note)
    assert props["work_folder"] == v.folder and props["file_name"] == "a.md" and "library" not in props
    assert props["folders"] == [v.folder_of("Dir")]
    props = v.props(pdf)
    assert props["work_folder"] == v.folder and props["file_name"] == "b.pdf" and props["folders"] == [v.folder]
    assert props["doc"]["size"] == paper.stat().st_size and props["doc"]["at"] == "path"
    v.quiet()

    lib = work("library", library=True)
    lib.write("c.md", "in the library\n")
    lib.sync()
    assert lib.props(lib.page_of("c.md"))["library"] is True


def test_a_move_on_disk_files_and_names_the_page_by_its_file(work):
    v = work()
    v.write("Dir/a.md", "a note\n")
    v.write("Lib/p.pdf", _pdf("p"), age=10)
    v.sync()
    note, pdf = v.page_of("Dir/a.md"), v.page_of("Lib/p.pdf")
    os.makedirs(v.root / "Other")
    os.replace(v.root / "Dir/a.md", v.root / "Other/a2.md")
    os.replace(v.root / "Lib/p.pdf", v.root / "Lib/p2.pdf")
    v.sync()
    props = v.props(note)
    assert props["folders"] == [v.folder_of("Other")] and props["file_name"] == "a2.md" and v.info(note).title == "a2"
    assert v.props(pdf)["file_name"] == "p2.pdf" and v.props(pdf)["folders"] == [v.folder_of("Lib")]
    v.quiet()


def test_a_library_files_title_is_free_while_its_file_keeps_its_name(work):
    v = work(library=True)
    v.write("p.pdf", _pdf("paper"), age=10)
    v.sync()
    page = v.page_of("p.pdf")
    commit_ops(v.ws, page, [{"op": "set", "id": page, "content": "A paper's title"}], actor=v.user)
    v.sync()
    assert (v.root / "p.pdf").exists() and v.info(page).title == "A paper's title"
    os.replace(v.root / "p.pdf", v.root / "q.pdf")
    v.sync()
    assert v.info(page).title == "A paper's title" and v.props(page)["file_name"] == "q.pdf"
    v.quiet()


def test_a_note_follows_a_persons_rename_move_trash_and_restore_where_the_vault_pulls(work, owner):
    v = work()
    v.write("Dir/notes.md", "my notes\n")
    v.write("Other/keep.md", "kept\n")
    v.sync()
    page = v.page_of("Dir/notes.md")
    commit_ops(v.ws, page, [{"op": "set", "id": page, "content": "Plan"}], actor=v.user)
    v.sync()
    assert (v.root / "Dir/Plan.md").read_text(encoding="utf-8") == "my notes\n" and not (v.root / "Dir/notes.md").exists()
    assert v.props(page)["file_name"] == "Plan.md"
    v.quiet()

    commit_ops(v.ws, page, [{"op": "set", "id": page, "props": {"folders": [v.folder_of("Other")]}}], actor=v.user)
    v.sync()
    assert (v.root / "Other/Plan.md").exists() and not (v.root / "Dir/Plan.md").exists()
    v.quiet()

    with connect_pages_db(v.ws) as conn:
        trash_page(v.ws, conn, page, actor=v.user)
    v.sync()
    assert not (v.root / "Other/Plan.md").exists()
    (tomb,) = vault_store.tombstones(v.link["id"])
    assert tomb["path"].startswith(vault_disk.TRASH) and (v.root / tomb["path"]).read_bytes() == b"my notes\n"
    with connect_pages_db(v.ws) as conn:
        restore_page(v.ws, conn, page, actor=v.user)
    v.sync()
    assert v.text("Other/Plan.md") == "my notes\n" and v.page_of("Other/Plan.md") == page
    v.quiet()


def test_a_note_made_in_gamma_is_written_where_tracked_and_reported_where_not(work, owner):
    v = work()
    v.write("Dir/seed.md", "seed\n")
    v.write("Hidden/paper.pdf", _pdf("hidden"), age=10)
    v.sync()
    fresh = _made(owner, "Fresh", v.folder_of("Dir"))
    assert v.props(fresh)["work_folder"] == v.folder
    commit_ops(v.ws, fresh, [insert(fresh, "fresh words")], actor=v.user)
    v.sync()
    assert v.text("Dir/Fresh.md") == "fresh words\n" and v.props(fresh)["file_name"] == "Fresh.md"
    v.quiet()

    v.write(".gammaignore", "/Hidden/*.md\n")
    v.sync()
    hidden = _made(owner, "Unwritten", v.folder_of("Hidden"))
    commit_ops(v.ws, hidden, [insert(hidden, "never on disk")], actor=v.user)
    status = v.sync(ok=False)
    assert not (v.root / "Hidden/Unwritten.md").exists() and "file_name" not in v.props(hidden)
    assert [k for k in status["kept"] if "leaves out Hidden/Unwritten.md" in k["why"]], status


def test_a_note_made_in_gamma_links_a_pdfs_page_by_its_file(work, owner):
    """A mention of a work folder's PDF in a note pull writes is the PDF's
    link by its path, where a page with no note was its absolute address;
    read back, it is the mention."""
    v = work()
    v.write("Papers/p.pdf", _pdf("a paper"), age=10)
    v.sync()
    paper = v.page_of("Papers/p.pdf")
    fresh = _made(owner, "Reading", v.folder)
    commit_ops(v.ws, fresh, [insert(fresh, f"read [[{paper}]] first")], actor=v.user)
    v.sync()
    assert v.text("Reading.md") == "read [[p.pdf]] first\n" and v.texts(fresh) == [f"read [[{paper}]] first"]
    v.quiet()
    v.write("Reading.md", "read [[p.pdf]] first, then again\n", age=5)
    v.sync()
    assert v.texts(fresh) == [f"read [[{paper}]] first, then again"]
    v.quiet()


def test_a_pdf_files_page_trashed_by_the_mirror_leaves_the_pdf_where_it_is(work):
    v = work()
    paper = v.write("Sub/p.pdf", _pdf("mine"), age=10)
    v.sync()
    page = v.page_of("Sub/p.pdf")
    before = _stat(paper)
    with connect_pages_db(v.ws) as conn:
        trash_page(v.ws, conn, page, actor=v.user, client="sync")
    v.sync()
    assert _stat(paper) == before and v.state(page) == "trashed"
    (tomb,) = vault_store.tombstones(v.link["id"])
    assert vault_store.in_place(tomb) and tomb["was"] == "Sub/p.pdf" and not (v.root / vault_disk.TRASH).exists()
    v.sync()        # the file is there and tracked: the disk says the page is
    assert v.state(page) == "live" and v.page_of("Sub/p.pdf") == page and _stat(paper) == before
    v.quiet()


def test_a_stored_pdf_filed_here_is_never_copied_out(work, owner):
    v = work()
    v.write("seed.md", "seed\n")
    v.sync()
    up = owner.post("/api/uploads", files={"file": ("x.pdf", _pdf("stored"), "application/pdf")}).json()
    page = make_page(owner, "Stored", doc_props(up["doc_id"], name="x.pdf"))["id"]
    commit_ops(v.ws, page, [{"op": "set", "id": page, "props": {"folders": [v.folder]}}], actor=v.user, client="sync")
    status = v.sync(ok=False)
    assert not list(v.root.glob("*.pdf"))
    assert [k for k in status["kept"] if "not written into the directory" in k["why"]], status


def test_a_parent_in_the_front_matter_makes_no_sub_page(work):
    v = work()
    v.write("Top.md", "top\n")
    v.write("Kid.md", "---\nparent: \"[[Top]]\"\n---\nkid\n")
    v.sync()
    kid = v.page_of("Kid.md")
    assert "parent_page" not in v.props(kid) and v.props(kid)["folders"] == [v.folder]
    v.quiet()


def test_a_library_files_versions_are_stored_as_they_come(work):
    v = work(library=True)
    paper = v.write("p.pdf", _pdf("first"), age=10)
    v.sync()
    page = v.page_of("p.pdf")
    v.write("p.pdf", _pdf("second version"), age=5)
    v.sync()
    doc = v.props(page)["doc"]
    assert doc["at"] == "path" and doc["size"] == paper.stat().st_size
    assert storage.exists(v.ws, stored_name(doc["hash"], ".pdf"))
    v.quiet()


def test_a_gammaignore_that_vanishes_stops_the_vault(work):
    v = work()
    v.write("a.md", "a\n")
    v.write(".gammaignore", "/b.md\n")
    v.sync()
    assert v.link["state"]["selection"]
    os.remove(v.root / ".gammaignore")
    v.write("b.md", "b\n")
    status = v.sync(ok=False)
    assert "is gone" in status["stopped"] and status["last_error"]
    assert "b.md" not in v.rows() and v.state(v.page_of("a.md")) == "live"


def test_a_selection_past_the_cap_stops_before_it_writes(work):
    v = work()
    for n in range(3):
        v.write(f"n{n}.md", f"note {n}\n")
    v.link["state"]["cap"] = 2
    status = v.sync(ok=False)
    assert status["too_many"] == {"files": 3, "cap": 2} and status["stopped"] and v.rows() == {}


def test_the_markers_old_ignore_globs_are_written_once_as_its_gammaignore(work):
    v = work(mark=False)
    marker = json.loads((v.root / vault_disk.MARKER).read_text(encoding="utf-8"))
    (v.root / vault_disk.MARKER).write_text(json.dumps({**marker, "ignore": ["drafts", "old/x.md"]}), encoding="utf-8")
    v.write("drafts/d.md", "draft\n")
    v.write("old/x.md", "old\n")
    v.write("old/y.md", "kept\n")
    v.sync()
    text = v.text(".gammaignore")
    assert text.startswith(vault_sync.ignore_rules.HEADER) and text.endswith("drafts\n/old/x.md\n")
    assert set(v.rows()) == {"old/y.md"} and v.link["state"]["selection"]
    (v.root / vault_disk.MARKER).write_text(json.dumps({**marker, "ignore": ["old"]}), encoding="utf-8")
    v.sync()
    assert v.text(".gammaignore") == text and set(v.rows()) == {"old/y.md"}


def test_the_old_globs_leave_out_what_the_old_scan_did_where_it_matched_by_case(work, monkeypatch):
    monkeypatch.setattr(vault_sync.fnmatch, "fnmatch", vault_sync.fnmatch.fnmatchcase)     # macOS's and Linux's
    v = work(mark=False)
    marker = json.loads((v.root / vault_disk.MARKER).read_text(encoding="utf-8"))
    (v.root / vault_disk.MARKER).write_text(json.dumps({**marker, "ignore": ["Drafts"]}), encoding="utf-8")
    v.write("Drafts/d.md", "left out\n")
    v.write("Old/drafts/e.md", "tracked by the old scan\n")
    v.sync()
    assert v.text(".gammaignore").endswith("Drafts\n!/Old/drafts/\n")
    assert set(v.rows()) == {"Old/drafts/e.md"}


def test_one_file_of_a_paper_deselected_whole_ticked_again_keeps_the_page(work):
    v = work()
    v.write("Paper.pdf", _pdf("the paper"), age=10)
    v.sync()
    page = v.page_of("Paper.pdf")
    v.edit(page, insert(page, "a thought on the paper"))
    v.sync()
    assert (v.root / "Paper.md").exists()
    v.write(".gammaignore", "/Paper.*\n")
    v.sync()
    assert v.state(page) == "trashed"
    v.write(".gammaignore", "/Paper.pdf\n")      # the notes ticked again, the PDF not
    for _ in range(3):
        status = v.sync(ok=False)
        assert v.state(page) == "live" and (v.root / "Paper.md").exists(), status
    assert set(v.rows()) == {"Paper.md"} and vault_store.tombstones(v.link["id"]) == []
    assert not (v.root / vault_disk.TRASH).exists()


def test_the_pdf_of_a_paper_deselected_whole_ticked_again_stays(work):
    v = work()
    v.write("Paper.pdf", _pdf("the paper"), age=10)
    v.sync()
    page = v.page_of("Paper.pdf")
    v.edit(page, insert(page, "a thought on the paper"))
    v.sync()
    v.write(".gammaignore", "/Paper.*\n")
    v.sync()
    v.write(".gammaignore", "/Paper.md\n")
    for _ in range(3):
        v.sync(ok=False)
        assert v.state(page) == "live"
    assert set(v.rows()) == {"Paper.pdf"} and (v.root / "Paper.md").exists()


def test_a_page_its_selection_trashed_and_a_person_restored_stays_once_the_push_is_lowered(work):
    v = work()
    v.write("Note.md", "keep\n")
    v.write("Draft.md", "my draft\n")
    v.sync()
    page = v.page_of("Draft.md")
    v.write(".gammaignore", "/Draft.md\n")
    v.sync()
    assert v.state(page) == "trashed"
    v.link["push"] = folder_links.PUSH_PDFS
    takeover.release(v.link, v.remote, trash_files=False)
    with connect_pages_db(v.ws) as conn:
        restore_page(v.ws, conn, page, actor=v.user)
    v.sync(ok=False)
    assert v.state(page) == "live" and "deleted_by_vault" not in v.props(page)


def test_a_work_folder_let_go_at_its_round_loses_its_feedback_files(work):
    """A vault whose push came down is released before its next round
    (``folder_links._work_turn``): its pages unstamped, its mark off, and
    the feedback files its rounds wrote gone (gamma/feedback/files.py)."""
    v = work(library=True)
    v.write("Note.md", "keep\n")
    v.sync()
    page = v.page_of("Note.md")
    here = v.root / feedback_files.DIR
    (here / "img").mkdir(parents=True)
    (here / "open.json").write_text("{}\n", encoding="utf-8")
    (here / "img" / "0123456789abcdef.jpg").write_bytes(b"\xff\xd8\xff")
    v.link["state"][takeover.TAKEN_OVER] = "2026-01-01T00:00:00Z"
    v.link["push"] = folder_links.PUSH_PDFS
    link = folder_links._work_turn(v.link)
    assert takeover.TAKEN_OVER not in link["state"] and not here.exists() and (v.root / vault_disk.MARKER).is_file()
    assert "work_folder" not in v.props(page) and v.remote.mark() is None


def test_a_directory_gone_and_back_files_its_pages_in_it_again(work):
    v = work()
    paper = v.write("figs/a.pdf", _pdf("figure"), age=10)
    data = paper.read_bytes()
    v.write("keep.md", "keep\n")
    v.sync()
    page = v.page_of("figs/a.pdf")
    shutil.rmtree(v.root / "figs")
    v.sync()
    v.sync()
    assert v.state(page) == "trashed" and "figs" not in vault_store.dirs(v.link["id"])
    v.write("figs/a.pdf", data, age=10)
    v.sync()
    v.sync()
    with connect_pages_db(v.ws) as conn:
        tree = marks.Tree.read(conn)
    assert v.state(page) == "live" and v.props(page)["folders"] == [v.folder_of("figs")]
    assert tree.work_of(v.folder_of("figs")) == v.folder
    v.quiet()


def test_a_directory_renamed_in_gamma_by_a_mirror_stays_as_it_is_on_disk(work):
    v = work()
    v.write("src/main.md", "main\n")
    v.sync()
    commit_ops(v.ws, FOLDERS, [{"op": "set", "id": v.folder_of("src"), "content": "source"}], actor=v.user,
               client="sync")
    v.sync()
    assert sorted(p.name for p in v.root.iterdir()) == [".gamma", "src"] and set(v.rows()) == {"src/main.md"}


def test_a_papers_notes_stay_where_they_are_when_a_mirror_trashes_it(work):
    v = work()
    v.write("Paper.pdf", _pdf("paper"), age=10)
    v.sync()
    page = v.page_of("Paper.pdf")
    v.edit(page, insert(page, "my thought"))
    v.sync()
    notes = _stat(v.root / "Paper.md")
    with connect_pages_db(v.ws) as conn:
        trash_page(v.ws, conn, page, actor=v.user, client="sync")
    v.sync()
    assert _stat(v.root / "Paper.md") == notes and not (v.root / vault_disk.TRASH).exists()
    assert all(vault_store.in_place(t) for t in vault_store.tombstones(v.link["id"]))


def test_a_work_folder_with_a_deselected_pdf_is_removed_its_bytes_stored(work):
    v = work()
    v.write("Paper.pdf", _pdf("the paper"), age=10)
    v.write("Note.md", "a note\n")
    v.sync()
    page = v.page_of("Paper.pdf")
    v.write(".gammaignore", "/Paper.pdf\n")
    v.sync()
    assert v.state(page) == "trashed"
    assert [rel for _page, rel, _doc in folder_links._in_place(v.link)] == ["Paper.pdf"]
    assert folder_links.removal(v.link)["bytes"] == (v.root / "Paper.pdf").stat().st_size
    folder_links._store_in_place(v.link, v.root)
    assert storage.exists(v.ws, stored_name(v.props(page)["doc"]["hash"], ".pdf"))


def test_a_kept_vault_leaves_out_a_work_folder_moved_into_its_folder(owner):
    ws, user, top = workspace_of(OWNER), account_of(OWNER), f"Kept {uuid.uuid4().hex[:8]}"
    kept, sub = make_folder(owner, top), make_folder(owner, f"{top}/sub")
    work, directory = make_folder(owner, f"{top}/Paper"), make_folder(owner, f"{top}/Paper/figures")
    mark_work_folder(owner, work)
    assert set(LocalRemote(ws, kept, user).layout().folders) == {sub}
    assert set(LocalRemote(ws, work, user).layout().folders) == {directory}
    assert not {work, directory} & set(LocalRemote(ws, vault_sync.ROOT, user).layout().folders)
