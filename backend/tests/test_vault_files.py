"""The class ``file`` of a work folder's round (gamma/vault/kinds.py ``FILE``,
docs/dev/work_folders.md "The round in a work folder"): every file the
selection takes is a page whose document is the file, read where it is or
listed without its bytes past the keep limit, titled by its leaf, filed in
its directory, out of the library whatever the switch; a change replaces the
document, a touch keeps serving, a move refiles and may settle the format
again, a removal and a deselection send the page to Recently deleted and
back; pull writes no note beside it and never moves it; a kept folder's
picture row becomes one page, once; a note's link to a picture keeps its
Gamma text; the rows go with a release; nothing of a file is stored when the
work folder is removed."""

import os
import uuid
from types import SimpleNamespace

import pytest

from conftest import account_of, login, make_folder, make_user, mark_work_folder, workspace_of
from gamma import folder_links, gamma_sync, linked_files, pdf_meta, storage
from gamma.vault import disk as vault_disk, kinds, store as vault_store
from gamma.blocks_store import page_for_file
from gamma.db import connect_pages_db, connect_vaults_db
from gamma.documents.model import stored_name
from gamma.ops import OpError, trash_page
from gamma.vault.local_remote import LocalRemote
from gamma.work_folders import takeover
from gamma.work_folders.library import library_document
from test_vault_sync import Vault, _pdf, insert

OWNER = "vf_owner"
PNG = (b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89"
       b"\x00\x00\x00\rIDATx\x9cc\xf8\x0f\x00\x00\x01\x01\x00\x05\x18\xd8N\x00\x00\x00\x00IEND\xaeB`\x82")
PNG2 = PNG + b"\x00"                       # another picture, as far as a hash goes
WEBM = b"\x1a\x45\xdf\xa3" + bytes(64)     # an EBML header: what storage.is_audio asks of a .webm
MB = 1024 * 1024


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


class FileVault(Vault):
    """``test_vault_sync.Vault`` of this module's account, its folder marked
    as a work folder's unless ``mark`` is off (a kept folder's round), with a
    ``.gammaignore`` that tracks every file (the default tracks notes, PDFs
    and pictures alone)."""

    def __init__(self, owner, root, *, pull=True, library=False, mark=True):
        self.owner, self.root = owner, root
        self.ws, self.user = workspace_of(OWNER), account_of(OWNER)
        self.folder = make_folder(owner, f"VF {uuid.uuid4().hex[:8]}")
        root.mkdir(parents=True, exist_ok=True)
        (root / ".gammaignore").write_text("# every file\n", encoding="utf-8")
        marker = vault_disk.make_marker(root)
        self.link = {"id": uuid.uuid4().hex, "workspace_id": self.ws, "folder_id": self.folder, "path": str(root),
                     "pull": int(pull), "push": folder_links.PUSH_ALL, "created_by": self.user,
                     "state": {"vault": marker["id"]}, "kind": "folder", "remote_url": "", "library": int(library)}
        self.remote = LocalRemote(self.ws, self.folder, self.user)
        if mark:
            mark_work_folder(owner, self.folder, pull=pull, library=library)

    def props(self, page_id) -> dict:
        return self.blocks(page_id)[page_id]["props"]

    def doc(self, page_id) -> dict:
        return self.props(page_id)["doc"]

    def folder_of(self, directory) -> str:
        return vault_store.dirs(self.link["id"])[directory]["folder_id"]

    def served(self, name) -> bytes | None:
        """What ``GET /api/uploads/<name>`` reads: the linked file's bytes,
        None when the name reads as nothing."""
        path = storage.open_path(self.ws, name)
        return path.read_bytes() if path else None

    def linked(self, name) -> list:
        """``(path, page id)`` of the rows that serve ``name``."""
        with connect_vaults_db() as conn:
            return conn.execute("SELECT path, page_id FROM linked_files WHERE ws = ? AND name = ? ORDER BY path",
                                (self.ws, name)).fetchall()


@pytest.fixture
def work(owner, tmp_path):
    made = []

    def make(name="w", **kw):
        v = FileVault(owner, tmp_path / name, **kw)
        made.append(v)
        return v
    yield make
    for v in made:
        vault_store.forget_link(v.link["id"])
        with connect_vaults_db() as conn:
            conn.execute("DELETE FROM linked_files WHERE ws = ? AND substr(path, 1, ?) = ?",
                         (v.ws, len(str(v.root)), str(v.root)))


def _digest(data: bytes) -> str:
    return storage.content_digest(data)


def _touch(path, age: float) -> None:
    then = os.stat(path).st_mtime - age
    os.utime(path, (then, then))


# --- new pages -----------------------------------------------------------------------------------

def test_every_file_the_selection_takes_is_a_page_of_its_document(work):
    v = work(library=True)
    blob = os.urandom(300)
    files = {"paper/main.tex": b"\\documentclass{article}\n", "data.json": b'{"a": 1}\n', "figures/fig.png": PNG,
             "blob.bin": blob, "Makefile": b"all:\n\techo hi\n", "run-local.bat": b"@echo off\r\n"}
    for rel, data in files.items():
        v.write(rel, data, age=10)
    status = v.sync()
    assert status["counts"]["sent"] == 6
    shapes = {"paper/main.tex": ("text", ".tex"), "data.json": ("text", ".json"), "figures/fig.png": ("image", ".png"),
              "blob.bin": ("binary", ".bin"), "Makefile": ("text", ".bin"), "run-local.bat": ("text", ".bin")}
    for rel, (fmt, ext) in shapes.items():
        page, leaf, data = v.page_of(rel), rel.rsplit("/", 1)[-1], files[rel]
        props = v.props(page)
        assert props["doc"] == {"format": fmt, "name": leaf, "hash": _digest(data), "ext": ext, "at": "path",
                                "size": len(data)}, rel
        assert v.info(page).title == leaf and props["work_folder"] == v.folder and props["file_name"] == leaf
        assert "library" not in props and "auto_title" not in props, rel        # the switch is for notes and PDFs
        directory = rel.rpartition("/")[0]
        assert props["folders"] == [v.folder_of(directory) if directory else v.folder]
        assert v.rows()[rel]["kind"] == "file" and v.rows()[rel]["hash"] == _digest(data)
        assert v.served(stored_name(_digest(data), ext)) == data, rel
    v.quiet()
    note = v.write("c.md", "in the library\n")
    v.sync()
    assert v.props(v.page_of("c.md"))["library"] is True and note.exists()


def test_a_pdf_by_its_bytes_alone_is_a_binary_file(work):
    v = work()
    data = _pdf("no name")
    v.write("paper", data, age=10)
    v.sync()
    page = v.page_of("paper")
    assert v.doc(page)["format"] == "binary" and v.doc(page)["ext"] == ".bin" and v.rows()["paper"]["kind"] == "file"
    assert v.served(f"{_digest(data)}.bin") == data
    v.quiet()


# --- changes -----------------------------------------------------------------------------------

def test_a_change_replaces_the_document_and_serves_the_new_bytes(work):
    v = work()
    v.write("main.tex", "one\n", age=10)
    v.sync()
    page = v.page_of("main.tex")
    old = v.doc(page)["hash"]
    v.write("main.tex", "two\n", age=5)
    status = v.sync()
    assert status["counts"]["sent"] == 1
    doc = v.doc(page)
    assert doc["hash"] == _digest(b"two\n") != old and doc["size"] == 4 and doc["at"] == "path"
    assert v.served(f"{doc['hash']}.tex") == b"two\n" and v.served(f"{old}.tex") is None
    assert v.rows()["main.tex"]["hash"] == doc["hash"] and v.linked(f"{doc['hash']}.tex") == [(str(v.root / "main.tex"), page)]
    v.quiet()


def test_a_touch_with_the_same_bytes_keeps_serving_the_file_and_the_pdf(work):
    v = work()
    v.write("notes.txt", "same\n", age=10)
    paper = _pdf("same paper")
    v.write("p.pdf", paper, age=10)
    v.sync()
    for rel, data in (("notes.txt", b"same\n"), ("p.pdf", paper)):
        name = f"{_digest(data)}{os.path.splitext(rel)[1]}"
        assert v.served(name) == data
        _touch(v.root / rel, 5)
        assert v.served(name) is None, "the stat moved: served as nothing until the round looks"
        status = v.sync()
        assert status["counts"]["sent"] == 0 and v.served(name) == data
        assert v.rows()[rel]["mtime_ns"] == os.stat(v.root / rel).st_mtime_ns
    v.quiet()


def test_a_hot_round_pushes_an_open_tex_pages_change(work):
    v = work()
    v.write("main.tex", "one\n", age=10)
    v.sync()
    page = v.page_of("main.tex")
    v.write("main.tex", "two\n", age=5)
    status = v.sync_page(page)
    assert status["last_error"] == "" and status["counts"]["sent"] == 1
    two = _digest(b"two\n")
    assert v.doc(page)["hash"] == two and v.served(f"{two}.tex") == b"two\n"
    v.quiet()


# --- moves -------------------------------------------------------------------------------------

def test_a_move_refiles_and_retitles_and_a_new_name_settles_the_document_again(work):
    v = work()
    v.write("a/notes.txt", "n\n", age=10)
    v.sync()
    page = v.page_of("a/notes.txt")
    h = _digest(b"n\n")
    os.makedirs(v.root / "b")
    os.replace(v.root / "a/notes.txt", v.root / "b/notes.log")
    status = v.sync()
    assert status["counts"]["renamed"] == 1 and v.page_of("b/notes.log") == page
    props = v.props(page)
    assert props["folders"] == [v.folder_of("b")] and props["file_name"] == "notes.log" and v.info(page).title == "notes.log"
    assert props["doc"] == {"format": "text", "name": "notes.log", "hash": h, "ext": ".log", "at": "path", "size": 2}
    assert v.served(f"{h}.log") == b"n\n" and v.linked(f"{h}.txt") == []
    v.quiet()

    v.write("a.txt", PNG, age=10)
    v.sync()
    picture = v.page_of("a.txt")
    assert v.doc(picture)["format"] == "text"
    os.replace(v.root / "a.txt", v.root / "a.png")
    v.sync()
    doc = v.doc(picture)
    assert doc["format"] == "image" and doc["ext"] == ".png" and doc["hash"] == _digest(PNG) and doc["name"] == "a.png"
    assert v.served(f"{_digest(PNG)}.png") == PNG and v.linked(f"{_digest(PNG)}.txt") == []
    assert v.page_of("a.png") == picture and v.info(picture).title == "a.png"
    v.quiet()


def test_a_rename_that_changes_the_class_is_a_removal_and_a_new_page(work):
    v = work()
    v.write("a.txt", "hello\n", age=10)
    v.sync()
    page = v.page_of("a.txt")
    os.replace(v.root / "a.txt", v.root / "a.md")
    v.sync()
    v.sync()
    assert v.state(page) == "trashed" and v.page_of("a.md") != page and v.rows()["a.md"]["kind"] == "notes"


# --- gone, deselected, left -----------------------------------------------------------------------------

def test_a_removal_sends_the_page_to_recently_deleted_and_the_file_back_brings_it_back(work):
    v = work()
    v.write("data.csv", "1,2\n", age=10)
    v.sync()
    page = v.page_of("data.csv")
    os.remove(v.root / "data.csv")
    v.sync()
    v.sync()
    assert v.state(page) == "trashed" and "data.csv" not in v.rows()
    (tomb,) = vault_store.tombstones(v.link["id"])
    assert tomb["kind"] == "file" and tomb["was"] == "data.csv" and not (v.root / vault_disk.TRASH).exists()
    v.write("data.csv", "1,2\n", age=10)
    v.sync()
    assert v.state(page) == "live" and v.page_of("data.csv") == page and vault_store.tombstones(v.link["id"]) == []
    assert v.served(f"{_digest(b'1,2' + os.linesep.encode()[-1:])}.csv") == b"1,2\n"
    v.quiet()


def test_a_deselected_file_stays_where_it_is_its_page_in_recently_deleted(work):
    v = work()
    v.write("keep.md", "k\n")
    data = v.write("data.csv", "1,2\n", age=10)
    v.sync()
    page = v.page_of("data.csv")
    before = (data.read_bytes(), os.stat(data).st_mtime_ns)
    v.write(".gammaignore", "/data.csv\n")
    v.sync()
    assert v.state(page) == "trashed" and (data.read_bytes(), os.stat(data).st_mtime_ns) == before
    (tomb,) = vault_store.tombstones(v.link["id"])
    assert vault_store.in_place(tomb) and tomb["kind"] == "file" and not (v.root / vault_disk.TRASH).exists()
    v.write(".gammaignore", "")
    v.sync()
    assert v.state(page) == "live" and v.page_of("data.csv") == page and (data.read_bytes(), os.stat(data).st_mtime_ns) == before
    v.quiet()


def test_a_file_pages_trash_by_the_mirror_leaves_the_file_where_it_is(work):
    v = work()
    fig = v.write("fig.png", PNG, age=10)
    v.sync()
    page = v.page_of("fig.png")
    before = (fig.read_bytes(), os.stat(fig).st_mtime_ns)
    with connect_pages_db(v.ws) as conn:
        trash_page(v.ws, conn, page, actor=v.user, client="sync")
    v.sync()
    assert v.state(page) == "trashed" and (fig.read_bytes(), os.stat(fig).st_mtime_ns) == before
    (tomb,) = vault_store.tombstones(v.link["id"])
    assert vault_store.in_place(tomb) and tomb["was"] == "fig.png" and not (v.root / vault_disk.TRASH).exists()
    v.sync()        # the file is there and tracked: the disk says the page is
    assert v.state(page) == "live" and v.page_of("fig.png") == page


# --- pull -------------------------------------------------------------------------------------------

def test_pull_writes_no_note_beside_a_file_page_and_places_it_without_moving_it(work):
    v = work()
    fig = v.write("fig.png", PNG, age=10)
    v.sync()
    page = v.page_of("fig.png")
    v.edit(page, insert(page, "a thought on the figure"))
    v.sync()
    assert sorted(p.name for p in v.root.iterdir()) == [".gamma", ".gammaignore", "fig.png"]
    v.remote.set_page(page, title="Figure one")
    status = v.sync()
    assert status["last_error"] == "" and v.rows()["fig.png"]["title"] == "Figure one" and fig.exists()
    assert sorted(p.name for p in v.root.iterdir()) == [".gamma", ".gammaignore", "fig.png"]
    assert v.info(page).title == "Figure one"
    v.quiet()


def test_a_picture_a_note_made_in_gamma_links_is_written_and_becomes_a_page_at_the_next_round(work):
    v = work()
    v.write("N.md", "n\n")
    v.sync()
    page = v.page_of("N.md")
    name = f"{_digest(PNG)}.png"
    storage.put_upload(v.ws, name, PNG)
    v.edit(page, insert(page, f"look ![](/api/uploads/{name})"))
    v.sync()
    assert (v.root / "attachments" / name).read_bytes() == PNG and f"attachments/{name}" not in v.rows()
    assert v.text("N.md") == f"n\n\nlook ![[{name}]]\n"
    v.sync()
    row = v.rows()[f"attachments/{name}"]
    assert row["kind"] == "file" and row["page_id"] and v.doc(row["page_id"])["hash"] == _digest(PNG)
    v.quiet()


# --- the reader's side -------------------------------------------------------------------------------

def test_follow_and_the_sweep_leave_a_file_pages_row_alone(work):
    v = work()
    v.write("notes.txt", "n\n", age=10)
    v.sync()
    page = v.page_of("notes.txt")
    name = stored_name(_digest(b"n\n"), ".txt")
    assert linked_files._in_vault(page)
    with connect_vaults_db() as conn:
        rows = conn.execute("SELECT * FROM linked_files WHERE ws = ? AND page_id = ?", (v.ws, page)).fetchall()
    _touch(v.root / "notes.txt", 5)
    linked_files.follow(v.ws, name)
    with connect_vaults_db() as conn:
        assert conn.execute("SELECT * FROM linked_files WHERE ws = ? AND page_id = ?", (v.ws, page)).fetchall() == rows
    assert v.doc(page)["hash"] == _digest(b"n\n")
    v.sync()
    assert v.served(name) == b"n\n"


# --- a kept folder's pictures, a note's links -------------------------------------------------------------

def test_a_kept_folders_picture_row_at_a_tracked_path_becomes_one_page_once(work):
    v = work(mark=False)                   # pushes everything into an unmarked folder: a kept folder's round
    v.write("img/pic.png", PNG)
    v.write("P.md", "look: ![[pic.png]]\n")
    v.sync()
    note = v.page_of("P.md")
    name = f"{_digest(PNG)}.png"
    assert v.rows()["img/pic.png"]["kind"] == "picture" and v.linked(name) == [(str(v.root / "img" / "pic.png"), "")]
    assert v.texts(note) == [f"look: ![](/api/uploads/{name})"]
    mark_work_folder(v.owner, v.folder)
    pages = v.page_count()
    for _ in range(3):
        v.sync()
    assert v.page_count() == pages + 1
    row = v.rows()["img/pic.png"]
    assert row["kind"] == "file" and row["page_id"] and v.doc(row["page_id"])["hash"] == _digest(PNG)
    assert v.texts(note) == [f"look: ![](/api/uploads/{name})"] and v.served(name) == PNG
    assert v.linked(name) == [(str(v.root / "img" / "pic.png"), row["page_id"])]     # the page-less row is gone
    v.quiet()


def test_a_note_linking_a_picture_new_in_the_same_round_reads_the_pictures_row(work):
    v = work()
    v.write("fig.png", PNG, age=10)
    v.write("N.md", "see ![[fig.png]]\n")
    v.sync()
    name = f"{_digest(PNG)}.png"
    assert v.texts(v.page_of("N.md")) == [f"see ![](/api/uploads/{name})"] and v.served(name) == PNG
    assert v.linked(name) == [(str(v.root / "fig.png"), v.page_of("fig.png"))]
    v.write("fig2.png", PNG2, age=10)
    v.write("N.md", "see ![[fig2.png]]\n", age=5)        # a changed note, pushed before the round's new files
    v.sync()
    other = f"{_digest(PNG2)}.png"
    assert v.texts(v.page_of("N.md")) == [f"see ![](/api/uploads/{other})"] and v.served(other) == PNG2
    assert v.linked(other) == [(str(v.root / "fig2.png"), v.page_of("fig2.png"))]
    v.quiet()


# --- the keep limit ------------------------------------------------------------------------------

def test_a_file_past_the_keep_limit_is_listed_and_hashed_once_it_shrinks(work, monkeypatch):
    monkeypatch.setenv("GAMMA_VAULT_KEEP_MB", "1")
    v = work()
    big = b"\x00" + os.urandom(MB)
    v.write("weights.ckpt", big, age=10)
    v.sync()
    page = v.page_of("weights.ckpt")
    assert v.doc(page) == {"format": "binary", "name": "weights.ckpt", "hash": "", "ext": ".ckpt", "at": "listed",
                           "size": len(big)}
    assert v.rows()["weights.ckpt"]["hash"] == "" and linked_files._rows_of(v.ws, page) == []
    v.quiet()
    v.write("weights.ckpt", big + b"\x00" * 10, age=5)              # listed with a new size: the document says so
    v.sync()
    assert v.doc(page)["size"] == len(big) + 10 and v.doc(page)["at"] == "listed"
    small = b"\x00small"
    v.write("weights.ckpt", small, age=3)
    v.sync()
    doc = v.doc(page)
    assert doc["at"] == "path" and doc["hash"] == _digest(small) and doc["size"] == len(small)
    assert v.served(f"{_digest(small)}.ckpt") == small and v.rows()["weights.ckpt"]["hash"] == _digest(small)
    v.write("weights.ckpt", big, age=1)
    v.sync()
    assert v.doc(page)["at"] == "listed" and v.doc(page)["hash"] == "" and linked_files._rows_of(v.ws, page) == []
    v.quiet()


# --- what is stored, and what is not -----------------------------------------------------------------

def test_a_files_version_is_stored_under_its_own_extension_when_kept(work):
    v = work()
    clip = v.write("clip.webm", WEBM, age=10)
    v.write("fig.png", PNG, age=10)
    v.sync()
    sink = folder_links.LocalSink(v.ws, v.user)
    assert sink.store(clip, ".webm") == _digest(WEBM) and storage.exists(v.ws, f"{_digest(WEBM)}.webm")
    odd = v.write("odd.webm", b"not a container", age=10)
    with pytest.raises(gamma_sync.SyncError, match="audio"):
        sink.store(odd, ".webm")
    fig = v.page_of("fig.png")
    assert linked_files.store_page_bytes(v.ws, fig, f"{_digest(PNG)}.png", _digest(PNG)) is True
    assert storage.exists(v.ws, f"{_digest(PNG)}.png")


def test_removing_a_work_folder_stores_nothing_for_a_file_page(work):
    v = work()
    picture = PNG + b"\x00removed"
    v.write("fig.png", picture, age=10)
    v.write("p.pdf", _pdf("paper"), age=10)
    v.sync()
    fig, pdf = v.page_of("fig.png"), v.page_of("p.pdf")
    assert [rel for _page, rel, _doc in folder_links._in_place(v.link)] == ["p.pdf"]
    assert folder_links.removal(v.link)["in_place"] == 1
    folder_links._store_in_place(v.link, v.root)
    assert storage.exists(v.ws, f"{v.doc(pdf)['hash']}.pdf") and not storage.exists(v.ws, f"{_digest(picture)}.png")
    assert v.doc(fig)["at"] == "path" and v.doc(pdf)["at"] == "store"


def test_a_release_forgets_the_file_rows_and_their_pages_keep_their_documents(work):
    v = work()
    data = v.write("data.csv", "1,2\n", age=10)
    v.write("n.md", "n\n")
    v.sync()
    page = v.page_of("data.csv")
    v.link["push"] = folder_links.PUSH_PDFS
    takeover.release(v.link, v.remote, trash_files=False)
    assert "data.csv" not in v.rows() and "n.md" in v.rows()
    assert v.doc(page) == {"format": "text", "name": "data.csv", "hash": _digest(b"1,2\n"), "ext": ".csv", "at": "path",
                           "size": 4}
    assert "work_folder" not in v.props(page) and v.remote.mark() is None
    before = (data.read_bytes(), os.stat(data).st_mtime_ns)
    v.sync(ok=False)
    v.sync(ok=False)
    assert (data.read_bytes(), os.stat(data).st_mtime_ns) == before and v.state(page) == "live"
    assert "data.csv" not in v.rows() and not (v.root / vault_disk.TRASH).exists()


# --- the read, the journal, the removal and the chip against the file ----------------------------

def test_a_file_is_listed_or_hashed_by_the_stat_its_read_takes(work, monkeypatch):
    """The scan's size decided, so a file that shrank below the limit between
    the walk and its read was recorded listed with the small file's stat, and
    stayed listed until it changed again."""
    monkeypatch.setenv("GAMMA_VAULT_KEEP_MB", "1")
    v = work()
    small = v.write("build.log", b"small\n", age=10)
    st = os.stat(small)
    seen_large = vault_disk.Found("file", MB + 1, st.st_mtime_ns, str(st.st_ino))
    read = kinds.FILE.read(SimpleNamespace(root=v.root), "build.log", seen_large)
    assert not read.listed and read.hash == _digest(b"small\n") and read.size == 6
    big = v.write("weights.ckpt", b"\x00" + os.urandom(MB), age=10)
    st = os.stat(big)
    seen_small = vault_disk.Found("file", 6, st.st_mtime_ns, str(st.st_ino))
    read = kinds.FILE.read(SimpleNamespace(root=v.root), "weights.ckpt", seen_small)
    assert read.listed and read.hash == "" and read.size == MB + 1


def test_a_listed_files_failed_set_is_its_problem_and_never_a_push_that_landed(work, monkeypatch):
    """A listed read's hash is "", which the page's ``doc_of`` answered too, so
    a refused ``set_doc`` left its pending and the next round promoted it as
    done: the page's size stayed stale until the file changed again."""
    monkeypatch.setenv("GAMMA_VAULT_KEEP_MB", "1")
    v = work()
    big = b"\x00" + os.urandom(MB)
    v.write("weights.ckpt", big, age=10)
    v.sync()
    page = v.page_of("weights.ckpt")
    v.write("weights.ckpt", big + b"\x00" * 10, age=5)
    real = v.remote.set_doc

    def refused(*_args, **_kw):
        raise OpError(500, "the server fell over")
    monkeypatch.setattr(v.remote, "set_doc", refused)
    status = v.sync(ok=False)
    assert status["kept"] and v.doc(page)["size"] == len(big)
    row = v.rows()["weights.ckpt"]
    assert row["pending"] is None and "fell over" in row["problem"] and row["size"] == len(big) + 10
    monkeypatch.setattr(v.remote, "set_doc", real)
    v.sync(ok=False)
    assert v.doc(page)["size"] == len(big), "refused: not sent again until it changes"
    v.write("weights.ckpt", big + b"\x00" * 20, age=3)
    v.sync()
    assert v.doc(page)["size"] == len(big) + 20
    # a round that died with a set in its journal: the page's listed size says whether it landed
    row = v.rows()["weights.ckpt"]
    stat = {k: row[k] for k in ("size", "mtime_ns", "file_id", "read_at")}
    vault_store.put_pending(v.link["id"], "weights.ckpt", "file", {
        "op": "push", "token": "dead-round", "hash": "", "text": None, "nodes": None, "page_id": page,
        "fields": {**stat, "size": len(big) + 30, "missing_since": 0, "problem": "", "ours": 0}})
    status = v.sync()
    assert v.rows()["weights.ckpt"]["pending"] is None and v.rows()["weights.ckpt"]["size"] == len(big) + 20
    assert status["counts"]["read"] == 0, "not landed: the row stays the file's, which is not read again"
    then = stat["mtime_ns"] + 10_000_000
    os.utime(v.root / "weights.ckpt", ns=(then, then))
    vault_store.put_pending(v.link["id"], "weights.ckpt", "file", {
        "op": "push", "token": "dead-round-2", "hash": "", "text": None, "nodes": None, "page_id": page,
        "fields": {**stat, "mtime_ns": then, "missing_since": 0, "problem": "", "ours": 0}})
    status = v.sync()
    assert v.rows()["weights.ckpt"]["mtime_ns"] == then and status["counts"]["read"] == 0, \
        "landed: the page lists that size, and the row takes the stat"


def test_removing_a_work_folder_lets_a_library_files_page_show_its_stored_bytes(work, owner):
    """A library picture and a replaced library PDF, whose bytes were stored
    as they came, were left at ``path`` with no row to read: no longer
    library documents, Replace and Duplicate gone, and a second upload of
    the same bytes made another page."""
    v = work(library=True)
    v.write("fig.png", PNG, age=10)
    v.write("p.pdf", _pdf("one"), age=10)
    v.write("data.csv", b"1,2\n", age=10)
    v.sync()
    fig, pdf, data = (v.page_of(rel) for rel in ("fig.png", "p.pdf", "data.csv"))
    assert owner.post(f"/api/pages/{fig}/library", json={}).status_code == 200
    v.write("p.pdf", _pdf("two"), age=5)
    v.sync()
    assert storage.exists(v.ws, f"{v.doc(pdf)['hash']}.pdf") and storage.exists(v.ws, f"{_digest(PNG)}.png")
    assert folder_links._in_place(v.link) == []
    folder_links._store_in_place(v.link, v.root)
    for page in (fig, pdf):
        assert v.doc(page)["at"] == "store" and library_document(v.props(page)), page
    with connect_pages_db(v.ws) as conn:
        assert page_for_file(conn, _digest(PNG), ".png")[0] == fig
        assert page_for_file(conn, v.doc(pdf)["hash"], ".pdf")[0] == pdf
    assert v.doc(data)["at"] == "path", "nothing stored, nothing to show from the store"


def test_add_to_library_on_a_chip_of_a_work_folders_file_stores_the_bytes_it_names(work, owner):
    """A work folder's note links its sibling file by its stored name, which
    only the file on this computer held: ``by-file`` made a library page at
    ``store`` of bytes that were never stored and read 404 once the file
    changed."""
    v = work()
    data = b'{"a": 1}\n'
    v.write("data.json", data, age=10)
    v.sync()
    name = stored_name(_digest(data), ".json")
    assert not storage.exists(v.ws, name) and v.served(name) == data
    r = owner.post("/api/pages/by-file", json={"file": name, "name": "data.json"})
    assert r.status_code == 200, r.text
    assert r.json()["created"] is True
    library = r.json()["page"]["id"]
    assert r.json()["page"]["properties"]["doc"] =={"format": "text", "name": "data.json", "hash": _digest(data),
                                                      "ext": ".json", "at": "store", "size": len(data)}
    assert storage.exists(v.ws, name)
    v.write("data.json", b'{"a": 2}\n', age=5)
    v.sync()
    assert owner.get(f"/api/uploads/{name}").content == data
    fresh = owner.post("/api/pages", json={"title": "fresh", "properties": {}}).json()["id"]
    r = owner.post(f"/api/pages/{fresh}/attachment", json={"file": name})
    assert r.status_code == 409 and r.json()["page_id"] == library      # the library page owns the bytes
    assert owner.post("/api/pages/by-file", json={"file": f"{'0' * 24}.json"}).status_code == 404
