"""A vault from before work folders taken over as one, and a work folder let
go (gamma/work_folders/takeover.py, docs/dev/work_folders.md "Taking over
the vaults that exist"): the mark, each page's stamps, its folders from its
file's directory first, sub-pages flattened, the PDF's size, the rows' seq
and folder following, nothing reordered in the recents; again changes
nothing, a kill at each page resumes, the pages of a vault inside or beside
it left to that vault; ``release`` unstamps, and sends the files not in the
library to Recently deleted when the vault goes."""

import os
import uuid

import pytest

from conftest import account_of, doc_props, login, make_folder, make_page, make_user, workspace_of
from gamma import folder_links, gamma_sync, pdf_meta, storage
from gamma.vault import disk as vault_disk, kinds, local_remote, store as vault_store, sync as vault_sync
from gamma.auth import DISK_CLIENT
from gamma.db import connect_pages_db
from gamma.ops import commit_ops
from gamma.vault.local_remote import LocalRemote
from gamma.work_folders import takeover
from gamma.work_folders.selection import MAX_FILES
from test_vault_sync import Vault, _pdf

OWNER = "wft_owner"


class Killed(BaseException):
    """The process dying in the middle of the takeover."""


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
    monkeypatch.setattr(storage, "LINKED_STAT_S", 0)
    monkeypatch.setattr(storage, "_stats", {})


class OldVault(Vault):
    """A vault that pushes everything, of this module's account, made before
    work folders: its folder carries no mark."""

    def __init__(self, owner, root, *, folder=None):
        self.owner, self.root = owner, root
        self.ws, self.user = workspace_of(OWNER), account_of(OWNER)
        self.folder = folder or make_folder(owner, f"WT {uuid.uuid4().hex[:8]}")
        root.mkdir(parents=True, exist_ok=True)
        marker = vault_disk.make_marker(root)
        self.link = {"id": uuid.uuid4().hex, "workspace_id": self.ws, "folder_id": self.folder, "path": str(root),
                     "pull": 1, "push": folder_links.PUSH_ALL, "created_by": self.user, "state": {"vault": marker["id"]},
                     "kind": "folder", "remote_url": "", "library": 1}
        self.remote = LocalRemote(self.ws, self.folder, self.user)

    def props(self, page_id) -> dict:
        return self.blocks(page_id)[page_id]["props"]

    def take_over(self, remote=None) -> dict:
        self.link["state"] = takeover.take_over(self.link, remote or self.remote)
        return self.link["state"]


@pytest.fixture
def old(owner, tmp_path):
    made = []

    def make(name="v", **kw):
        v = OldVault(owner, tmp_path / name, **kw)
        made.append(v)
        return v
    yield make
    for v in made:
        vault_store.forget_link(v.link["id"])


def _updated(ws, ids) -> dict:
    with connect_pages_db(ws) as conn:
        return {i: conn.execute("SELECT updated_at FROM unified_blocks WHERE id = ?", (i,)).fetchone()[0] for i in ids}


def _ops(ws) -> int:
    with connect_pages_db(ws) as conn:
        return conn.execute("SELECT COUNT(*) FROM page_ops").fetchone()[0]


def _lab(v, owner):
    """Four pages: a paper (a PDF and its notes in ``Sub``), a note filed in a
    library folder too, a sub-page from its front matter, and a lone PDF at
    the top refiled into ``Sub`` in Gamma (its row recording that folder)."""
    library = make_folder(owner, f"WT library {uuid.uuid4().hex[:6]}")
    paper = v.write("Sub/Paper.pdf", _pdf("paper"), age=10)
    v.write("Sub/Paper.md", "notes on the paper\n")
    v.write("Top.md", "top\n")
    v.write("Kid.md", "---\nparent: \"[[Top]]\"\n---\nkid\n")
    v.write("Lone.pdf", _pdf("lone"), age=10)
    v.sync()
    pages = {rel: v.page_of(rel) for rel in ("Sub/Paper.pdf", "Top.md", "Kid.md", "Lone.pdf")}
    sub = vault_store.dirs(v.link["id"])["Sub"]["folder_id"]
    commit_ops(v.ws, pages["Top.md"], [{"op": "set", "id": pages["Top.md"], "props": {"folders": [v.folder, library]}}],
               actor=v.user)
    commit_ops(v.ws, pages["Lone.pdf"], [{"op": "set", "id": pages["Lone.pdf"], "props": {"folders": [sub]}}],
               actor=v.user)
    v.sync()
    assert v.props(pages["Kid.md"]).get("parent_page") == pages["Top.md"]
    assert v.rows()["Lone.pdf"]["folder"] == sub
    return pages, library, sub, paper


def test_a_vault_from_before_is_stamped_filed_by_its_directories_and_flattened(old, owner):
    v = old()
    pages, library, sub, paper = _lab(v, owner)
    before = _updated(v.ws, pages.values())
    state = v.take_over()
    assert state[takeover.TAKEN_OVER] and state["cap"] == MAX_FILES and state["vault"] == v.link["state"]["vault"]
    assert v.remote.mark() == {"pull": True, "library": True}
    for rel, page in pages.items():
        props = v.props(page)
        assert props["work_folder"] == v.folder and props["library"] is True, rel
        assert props["file_name"] == rel.rsplit("/", 1)[-1] and "parent_page" not in props, rel
    assert v.props(pages["Sub/Paper.pdf"])["folders"] == [sub]
    assert v.props(pages["Sub/Paper.pdf"])["doc"]["size"] == paper.stat().st_size
    assert v.props(pages["Top.md"])["folders"] == [v.folder, library]
    assert v.props(pages["Kid.md"])["folders"] == [v.folder]
    assert v.props(pages["Lone.pdf"])["folders"] == [v.folder] and v.rows()["Lone.pdf"]["folder"] == ""
    assert _updated(v.ws, pages.values()) == before
    infos = v.layout().pages
    for rel, row in v.rows().items():
        if row["page_id"]:
            assert row["seq"] == infos[row["page_id"]].seq, rel
    v.sync()
    v.quiet()


def test_a_takeover_again_changes_nothing_and_one_killed_at_each_page_resumes(old, owner, monkeypatch):
    """A page a transaction: each try stamps one page more and dies at the
    next, until one finishes; a takeover once finished writes nothing."""
    v = old()
    pages, *_ = _lab(v, owner)
    real, tries, done = local_remote.apply_batches, {"n": 0}, []

    def dying(conn, batches, **kw):
        if any(op.get("quiet") for _page, ops in batches for op in ops):
            tries["n"] += 1
            if tries["n"] > 1:
                raise Killed()
        return real(conn, batches, **kw)
    with monkeypatch.context() as m:
        m.setattr(local_remote, "apply_batches", dying)
        m.setattr(local_remote, "MAX_OPS", 1)
        for _ in range(len(pages) + 1):
            tries["n"] = 0
            try:
                v.take_over()
                break
            except Killed:
                done.append(sum(v.props(p).get("work_folder") == v.folder for p in pages.values()))
    assert done == list(range(1, len(pages)))
    assert all(v.props(p).get("work_folder") == v.folder for p in pages.values())
    ops = _ops(v.ws)
    v.take_over()
    assert _ops(v.ws) == ops
    v.sync()
    v.quiet()


def test_the_pages_of_a_vault_inside_or_beside_it_are_left_to_that_vault(old, owner):
    outer = make_folder(owner, f"WT outer {uuid.uuid4().hex[:6]}")
    inner = make_folder(owner, f"WT outer {outer}/inner")
    beside = make_folder(owner, f"WT beside {uuid.uuid4().hex[:6]}")
    v = old("outer", folder=outer)
    v.write("mine.md", "mine\n")
    v.sync()
    theirs = make_page(owner, "Inner page", {"folders": [inner]})["id"]
    shared = make_page(owner, "Shared page", {"folders": [outer, beside]})["id"]
    remote = LocalRemote(v.ws, outer, v.user, leave_out=[inner, beside], siblings=[beside])
    v.take_over(remote)
    assert v.props(v.page_of("mine.md"))["work_folder"] == outer
    assert "work_folder" not in v.props(theirs) and "work_folder" not in v.props(shared)


PNG = bytes.fromhex("89504e470d0a1a0a0000000d4948445200000001000000010806000000"
                    "1f15c4890000000a49444154789c6360000000020001e221bc330000000049454e44ae426082")


def test_the_cap_counts_what_the_round_counts_pictures_included(old, monkeypatch):
    monkeypatch.setattr(takeover, "MAX_FILES", 3)
    monkeypatch.setattr(vault_sync, "MAX_FILES", 3)
    v = old()
    for n in range(3):
        v.write(f"n{n}.md", f"note {n}\n")
    v.write("img/a.png", PNG)
    v.write("img/b.png", PNG)
    v.sync()
    assert v.take_over()["cap"] == 5
    status = v.sync(ok=False)
    assert not status.get("stopped") and not status.get("too_many"), status


def test_a_page_with_no_file_on_disk_is_a_file_only_where_it_follows(old, owner):
    v = old()
    library = make_folder(owner, f"WT library {uuid.uuid4().hex[:6]}")
    v.write("seed.md", "seed\n")
    v.sync()
    up = owner.post("/api/uploads", files={"file": ("x.pdf", _pdf("stored here"), "application/pdf")}).json()
    stored = make_page(owner, "Stored", {**doc_props(up["doc_id"], name="x.pdf"), "folders": [v.folder, library]})["id"]
    note = make_page(owner, "Not written yet", {"folders": [v.folder]})["id"]
    kid = make_page(owner, "Kid", {"parent_page": stored})["id"]
    v.take_over()
    assert not {"work_folder", "library", "file_name"} & set(v.props(stored)) and v.props(stored)["folders"] == [library]
    assert v.props(kid).get("parent_page") == stored, "a library page keeps its sub-pages"
    assert v.props(note)["work_folder"] == v.folder and v.props(note)["folders"] == [v.folder]

    kept = old("nopull")
    kept.link["pull"] = 0
    kept.write("seed.md", "seed\n")
    kept.sync()
    gamma_note = make_page(owner, "Gamma's", {"folders": [kept.folder]})["id"]
    kept.take_over()
    assert "work_folder" not in kept.props(gamma_note) and "folders" not in kept.props(gamma_note)


def test_release_unstamps_and_sends_the_files_not_in_the_library_to_recently_deleted(old, owner):
    v = old()
    v.write("kept.md", "in the library\n")
    v.write("gone.md", "not in the library\n")
    v.sync()
    v.take_over()
    kept, gone = v.page_of("kept.md"), v.page_of("gone.md")
    commit_ops(v.ws, gone, [{"op": "set", "id": gone, "props": {"library": None}}], actor=v.user, client=DISK_CLIENT)
    takeover.release(v.link, v.remote, trash_files=True)
    assert v.remote.mark() is None
    assert v.state(kept) == "live" and not {"work_folder", "library", "file_name"} & set(v.props(kept))
    assert v.state(gone) == "trashed"
    with connect_pages_db(v.ws) as conn:
        trashed = conn.execute("SELECT json_extract(properties, '$.work_folder') FROM unified_blocks WHERE id = ?",
                               (gone,)).fetchone()[0]
    assert trashed == v.folder

    w = old("w")
    w.write("a.md", "a\n")
    w.sync()
    w.take_over()
    page = w.page_of("a.md")
    commit_ops(w.ws, page, [{"op": "set", "id": page, "props": {"library": None}}], actor=w.user, client=DISK_CLIENT)
    takeover.release(w.link, w.remote, trash_files=False)
    assert w.state(page) == "live" and "work_folder" not in w.props(page) and w.remote.mark() is None
    assert os.path.exists(w.root / "a.md")
