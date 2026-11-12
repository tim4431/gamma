"""A synced vault's rounds (gamma/vault_sync.py): real directories in tmp_path
and a real workspace, every step of push and pull (docs/dev/synced_vaults.md
"A round"), a kill at each point of the journal followed by a round that
converges with nothing lost and nothing doubled, the echo (a second round
writes nothing), counter-examples that once lost or doubled something, and a
vault of 2,000 notes."""

import json
import os
import pathlib
import shutil
import sqlite3
import subprocess
import sys
import time
import unicodedata
import uuid
from contextlib import closing

import pymupdf
import pytest

from conftest import (_tree_block, account_of, doc_props, login, make_folder, make_page, make_user,
                      mark_work_folder, thread_on, workspace_of)
from gamma import (comments, config, folder_links, gamma_sync, jobs, linked_files, md_lens, pdf_meta, storage,
                   vault_disk, vault_merge, vault_store, vault_sync)
from gamma.blocks_store import LABELS, block_kind, new_block_id, newest_change_seq, write_lock
from gamma.db import connect_pages_db, connect_users_db, connect_vaults_db
from gamma.feedback import kept
from gamma.ops import commit_ops, move_across_pages, restore_page, trash_page
from gamma.routers import pages as pages_mod
from gamma.routers.pages import replace_attachment
from gamma.trash import purge as purge_page
from gamma.vault_sync import LocalRemote

OWNER = "vs_owner"


class Killed(BaseException):
    """The process dying at a point of the journal: nothing below catches it."""


@pytest.fixture(scope="module")
def owner(client):
    make_user(OWNER, "pw")
    return login(OWNER, "pw")


@pytest.fixture(autouse=True)
def quick(monkeypatch):
    """Links kept here (linked files read), files settled at once and a
    missing file removed at its second scan, no manifest walk holding a PDF
    open, no stat remembered."""
    monkeypatch.setenv("GAMMA_FOLDER_LINKS", "1")
    monkeypatch.setattr(vault_sync, "SETTLE_NOTE_S", 0)
    monkeypatch.setattr(gamma_sync, "SETTLE_S", 0)
    monkeypatch.setattr(vault_store, "MISSING_S", 0)
    monkeypatch.setattr(pdf_meta, "schedule", lambda ws, doc_id: None)
    monkeypatch.setattr(storage, "linked_moved", [])
    monkeypatch.setattr(storage, "LINKED_STAT_S", 0)
    monkeypatch.setattr(storage, "_stats", {})


class Vault:
    """A directory with its marker, a new folder of the owner's workspace, and
    the link between them, kept in memory between rounds as its row would be."""

    def __init__(self, owner, root, *, push=folder_links.PUSH_ALL, pull=1, notes_folder="", folder=None):
        self.owner, self.root = owner, root
        self.ws, self.user = workspace_of(OWNER), account_of(OWNER)
        self.folder = folder or make_folder(owner, f"VS {uuid.uuid4().hex[:8]}")
        root.mkdir(parents=True, exist_ok=True)
        marker = vault_disk.make_marker(root, notes_folder)
        self.link = {"id": uuid.uuid4().hex, "workspace_id": self.ws, "folder_id": self.folder, "path": str(root),
                     "pull": pull, "push": push, "created_by": self.user, "state": {"vault": marker["id"]},
                     "kind": "folder", "remote_url": ""}
        self.remote = LocalRemote(self.ws, self.folder, self.user)

    def sync(self, *, ok=True, **kw) -> dict:
        result = vault_sync.sync(self.link, remote=self.remote, **kw)
        self.link["state"] = result.state
        if ok:
            assert result.status["last_error"] == "" and not result.status["kept"], result.status
        return result.status

    def sync_page(self, page_id) -> dict:
        return vault_sync.sync_page(self.link, page_id, remote=self.remote).status

    def quiet(self):
        """A round that changes nothing anywhere: no file written, no op, no
        conflict."""
        before = self.footprint()
        status = self.sync()
        moved = {k: v for k, v in status["counts"].items() if k in ("added", "updated", "renamed", "removed", "sent") and v}
        assert not moved, status
        assert self.footprint() == before

    def footprint(self):
        files = {}
        for top, _dirs, names in os.walk(self.root):
            for name in names:
                path = os.path.join(top, name)
                files[os.path.relpath(path, self.root)] = (os.stat(path).st_mtime_ns, open(path, "rb").read())
        with connect_pages_db(self.ws) as conn:
            ops = conn.execute("SELECT COUNT(*), COALESCE(SUM(seq), 0) FROM page_ops").fetchone()
            conflicts = conn.execute("SELECT COUNT(*) FROM sync_conflicts").fetchone()[0]
        return files, tuple(ops), conflicts

    def write(self, rel, text, age=None):
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(text.encode("utf-8") if isinstance(text, str) else text)
        if age is not None:
            then = time.time() - age
            os.utime(path, (then, then))
        return path

    def text(self, rel) -> str:
        return (self.root / rel).read_bytes().decode("utf-8")

    def rows(self) -> dict:
        return vault_store.files(self.link["id"])

    def page_of(self, rel) -> str:
        return self.rows()[rel]["page_id"]

    def layout(self):
        return self.remote.layout()

    def info(self, page_id):
        return self.layout().pages[page_id]

    def blocks(self, page_id) -> dict:
        return self.remote.page(page_id).blocks

    def tree(self, page_id) -> list:
        """The page's blocks with a span as ``(content, children)``."""
        def out(nodes):
            return [(n["content"], out(n["children"])) for n in nodes]
        blocks = self.blocks(page_id)
        return out(md_lens.project(blocks, lambda b: block_kind(b["parent"], page_id, b["props"])))

    def texts(self, page_id) -> list:
        blocks = self.blocks(page_id)
        return [n["content"] for n in md_lens.flatten(md_lens.project(blocks, lambda b: block_kind(b["parent"], page_id,
                                                                                                    b["props"])))]

    def find(self, page_id, content) -> str:
        return next(b for b, block in self.blocks(page_id).items() if block["content"] == content and b != page_id)

    def edit(self, page_id, *ops):
        return commit_ops(self.ws, page_id, list(ops), actor=self.user)

    def ops_count(self, page_id) -> int:
        with connect_pages_db(self.ws) as conn:
            return conn.execute("SELECT COUNT(*) FROM page_ops WHERE page_id = ?", (page_id,)).fetchone()[0]

    def conflicts(self, page_id) -> list:
        with connect_pages_db(self.ws) as conn:
            return conn.execute("SELECT block_id, kind, mine, theirs, result, base, resolved FROM sync_conflicts "
                                "WHERE page_id = ? ORDER BY id", (page_id,)).fetchall()

    def state(self, page_id):
        return self.remote.state(page_id)

    def page_count(self) -> int:
        return len(self.layout().pages)


def insert(parent, content, props=None, block_id=None):
    return {"op": "insert", "id": block_id or new_block_id(), "parent": parent, "content": content, "props": props or {}}


def set_(block_id, content):
    return {"op": "set", "id": block_id, "content": content}


def _pdf(text: str) -> bytes:
    doc = pymupdf.open()
    doc.new_page().insert_text((72, 72), text)
    return doc.tobytes()


@pytest.fixture
def vault(owner, tmp_path):
    made = []

    def make(name="v", **kw):
        v = Vault(owner, tmp_path / name, **kw)
        made.append(v)
        return v
    yield make
    for v in made:
        vault_store.forget_link(v.link["id"])
        with connect_vaults_db() as conn:     # the suite's vaults.db is shared: no row may outlive its directory
            conn.execute("DELETE FROM linked_files WHERE ws = ? AND substr(path, 1, ?) = ?",
                         (v.ws, len(str(v.root)), str(v.root)))


# --- push: the disk's changes ------------------------------------------------------------------

def test_a_new_note_is_a_page_titled_labelled_and_filed_by_its_file(vault):
    """The stem or the front matter's title, no automatic title,
    tags as labels, aliases kept, the directory's folder, the blocks."""
    v = vault()
    v.write("Topics/Graphs.md", "---\ntitle: Graph theory\ntags: [maths, '#graphs']\naliases: [Graphs]\ndoi: 10.1/g\n"
                                "authors: [Euler]\nyear: 1736\n---\n# Intro\n\nA graph is a pair.\n\n- edges\n- nodes\n")
    v.write("Loose.md", "just text\n")
    status = v.sync()
    assert status["counts"]["sent"] == 2
    graphs = v.page_of("Topics/Graphs.md")
    info = v.info(graphs)
    assert info.title == "Graph theory" and info.labels == ["maths", "graphs"] and info.aliases == ["Graphs"]
    props = v.blocks(graphs)[graphs]["props"]
    assert "auto_title" not in props and props["meta"]["doi"] == "10.1/g" and props["meta"]["authors"] == ["Euler"]
    topics = v.layout().folders[info.folders[0]]
    assert topics == (v.folder, "Topics")
    assert v.tree(graphs) == [("# Intro", [("A graph is a pair.", []), ("- edges", []), ("- nodes", [])])]
    loose = v.page_of("Loose.md")
    assert v.info(loose).title == "Loose" and v.info(loose).folders == [v.folder]
    v.quiet()


def test_a_child_typed_in_gamma_survives_a_change_on_disk(vault):
    """The target is the page with the file's change, and the
    pull then writes Gamma's child into the file."""
    v = vault()
    v.write("Doc.md", "# H\n\npara one\n")
    v.sync()
    page = v.page_of("Doc.md")
    v.edit(page, insert(v.find(page, "# H"), "my thought"))
    v.write("Doc.md", "# H\n\npara two\n")
    v.sync()
    assert v.tree(page) == [("# H", [("para two", []), ("my thought", [])])]
    assert v.text("Doc.md") == "# H\n\npara two\n\nmy thought\n"
    v.quiet()


def test_a_drag_and_a_delete_in_gamma_are_not_undone_by_an_edit_on_disk(vault):
    v = vault()
    v.write("D.md", "a\n\nb\n\nc\n")
    v.sync()
    page = v.page_of("D.md")
    c, b = v.find(page, "c"), v.find(page, "b")
    v.edit(page, {"op": "move", "id": c, "parent": page, "position": "Zz"}, {"op": "delete", "id": b})
    v.write("D.md", "a!\n\nb\n\nc\n")
    v.sync()
    assert v.texts(page) == ["c", "a!"]
    assert v.text("D.md") == "c\n\na!\n"
    v.quiet()


def test_the_file_wins_a_clash_and_gammas_text_is_a_conflict_in_gamma_space(vault):
    """By characters this merge reads clean and writes
    garbage; by words it clashes, the file's text stands, and Gamma's is a
    ``file_won`` row written with the ops."""
    v = vault()
    v.write("C.md", "The quick brown fox jumps over the dog\n")
    v.sync()
    page = v.page_of("C.md")
    v.edit(page, set_(v.find(page, "The quick brown fox jumps over the dog"),
                      "The quick brown fox jumps over the lazy dog today"))
    v.write("C.md", "A slow red fox leaps across a cat\n")
    status = v.sync()
    assert status["counts"]["conflicts"] == 1
    assert v.texts(page) == ["A slow red fox leaps across a cat"]
    block = v.find(page, "A slow red fox leaps across a cat")
    assert v.conflicts(page) == [(block, "file_won", "The quick brown fox jumps over the lazy dog today",
                                  "A slow red fox leaps across a cat", "A slow red fox leaps across a cat",
                                  "The quick brown fox jumps over the dog", 0)]
    assert v.text("C.md") == "A slow red fox leaps across a cat\n"
    v.quiet()


def test_a_word_gamma_inserted_inside_a_sentence_the_file_rewrote_is_a_clash(vault):
    """Gamma inserts one word into the sentence the file rewrites whole. By
    characters and phrases the merge read clean and pull wrote ``A slow red
    fox leaps across a catlazy`` into the person's file."""
    v = vault()
    v.write("G.md", "The quick brown fox jumps over the dog\n")
    v.sync()
    page = v.page_of("G.md")
    v.edit(page, set_(v.find(page, "The quick brown fox jumps over the dog"), "The quick brown fox jumps over the lazy dog"))
    v.write("G.md", "A slow red fox leaps across a cat\n")
    v.sync()
    assert v.text("G.md") == "A slow red fox leaps across a cat\n" and len(v.conflicts(page)) == 1
    v.quiet()


def test_one_word_both_sides_changed_is_a_clash_the_file_wins(vault):
    """``10:16`` made ``10:31`` in Gamma and ``10:56``
    on disk was merged by characters into ``10:35``, a time neither typed,
    clean, with no record of either edit."""
    v = vault()
    v.write("T.md", "meet at 10:16 today\n")
    v.sync()
    page = v.page_of("T.md")
    block = v.find(page, "meet at 10:16 today")
    v.edit(page, set_(block, "meet at 10:31 today"))
    v.write("T.md", "meet at 10:56 today\n")
    status = v.sync()
    assert status["counts"]["conflicts"] == 1
    assert v.texts(page) == ["meet at 10:56 today"] and v.text("T.md") == "meet at 10:56 today\n"
    assert v.conflicts(page) == [(block, "file_won", "meet at 10:31 today", "meet at 10:56 today", "meet at 10:56 today",
                                  "meet at 10:16 today", 0)]
    v.quiet()


def test_a_paragraph_deleted_and_the_next_edited_keeps_the_edited_ones_block(vault):
    """The edit took the deleted paragraph's block (its
    comments with it); with Gamma editing it too, both versions stayed and no
    conflict was kept."""
    v = vault()
    v.write("M.md", "# Notes\n\nCall the plumber about the leak.\n\nThe budget meeting moved to Thursday.\n")
    v.sync()
    page = v.page_of("M.md")
    plumber, budget = v.find(page, "Call the plumber about the leak."), v.find(page, "The budget meeting moved to Thursday.")
    v.write("M.md", "# Notes\n\nThe budget meeting moved to Friday.\n")
    v.sync()
    assert v.find(page, "The budget meeting moved to Friday.") == budget and plumber not in v.blocks(page)
    v.quiet()
    w = vault("w")
    w.write("M.md", "# Notes\n\nCall the plumber about the leak.\n\nThe budget meeting moved to Thursday.\n")
    w.sync()
    page = w.page_of("M.md")
    w.edit(page, set_(w.find(page, "The budget meeting moved to Thursday."), "The budget meeting moved to Thursday at noon."))
    w.write("M.md", "# Notes\n\nThe budget meeting moved to Friday.\n")
    w.sync()
    assert w.texts(page) == ["# Notes", "The budget meeting moved to Friday at noon."]
    assert w.text("M.md") == "# Notes\n\nThe budget meeting moved to Friday at noon.\n" and w.conflicts(page) == []
    w.quiet()


def test_a_list_item_made_a_paragraph_while_gamma_edited_it_is_one_block(vault):
    """The simulator's most frequent loss: the file's
    paragraph and Gamma's edited item both stayed, the text twice, no record."""
    v = vault()
    v.write("L.md", "- alpha beta\n- gamma delta\n")
    v.sync()
    page = v.page_of("L.md")
    block = v.find(page, "- alpha beta")
    v.edit(page, set_(block, "- alpha beta epsilon"))
    v.write("L.md", "alpha beta\n\n- gamma delta\n")
    v.sync()
    assert v.find(page, "alpha beta epsilon") == block and v.texts(page) == ["alpha beta epsilon", "- gamma delta"]
    assert v.text("L.md") == "alpha beta epsilon\n\n- gamma delta\n" and v.conflicts(page) == []
    v.quiet()


def test_a_paragraph_the_file_dropped_while_gamma_edited_it_goes_with_a_record(vault):
    v = vault()
    v.write("D.md", "keep this\n\ndrop this\n")
    v.sync()
    page = v.page_of("D.md")
    block = v.find(page, "drop this")
    v.edit(page, set_(block, "drop this, edited in Gamma"))
    v.write("D.md", "keep this\n")
    status = v.sync()
    assert status["counts"]["conflicts"] == 1 and v.texts(page) == ["keep this"] and v.text("D.md") == "keep this\n"
    assert v.conflicts(page) == [(block, "file_won", "drop this, edited in Gamma", "", "", "drop this", 0)]
    v.quiet()


def test_edits_of_different_words_on_both_sides_merge_and_reach_both(vault):
    v = vault()
    v.write("M.md", "one two three four\n")
    v.sync()
    page = v.page_of("M.md")
    v.edit(page, set_(v.find(page, "one two three four"), "one two three four five six"))
    v.write("M.md", "one TWO three four\n")
    v.sync()
    assert v.texts(page) == ["one TWO three four five six"]
    assert v.text("M.md") == "one TWO three four five six\n"
    assert v.conflicts(page) == []
    v.quiet()


def test_the_racy_rule_sees_a_save_in_the_same_tick(vault):
    """A same-size save that leaves size and time as they were
    (a coarse clock) is still read: the row was read within ``RACY_S`` of that
    time. Past it, an equal stat is trusted."""
    v = vault()
    path = v.write("R.md", "the cat sat\n")
    v.sync()
    st = os.stat(path)
    path.write_bytes(b"the dog sat\n")
    os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns))
    v.sync()
    assert v.texts(v.page_of("R.md")) == ["the dog sat"]
    row = v.rows()["R.md"]
    vault_store.put(v.link["id"], "R.md", read_at=row["mtime_ns"] / 1e9 + 10)
    path.write_bytes(b"the cow sat\n")
    os.utime(path, ns=(row["mtime_ns"], row["mtime_ns"]))
    v.sync()
    assert v.texts(v.page_of("R.md")) == ["the dog sat"]      # trusted, as the rule says


def test_a_note_renamed_or_moved_on_disk_keeps_its_page(vault):
    """A move by file id; the page is named by the new stem and
    filed in the new directory's folder in place of the old one."""
    v = vault()
    v.write("Old name.md", "kept text\n")
    v.write("Sub/Other.md", "other\n")
    v.sync()
    page = v.page_of("Old name.md")
    os.rename(v.root / "Old name.md", v.root / "New name.md")
    status = v.sync()
    assert status["counts"]["renamed"] == 1 and v.page_of("New name.md") == page
    assert v.info(page).title == "New name" and v.page_count() == 2
    os.makedirs(v.root / "Elsewhere")
    os.rename(v.root / "New name.md", v.root / "Elsewhere" / "New name.md")
    v.sync()
    info = v.info(page)
    assert v.layout().folders[info.folders[0]] == (v.folder, "Elsewhere") and len(info.folders) == 1
    v.quiet()


def test_a_file_copied_elsewhere_and_removed_is_a_move_by_hash_by_text_or_by_pdf_id(vault):
    """A move's evidence past the file id (a copy has another): a hash no
    other file has, a note's text at least ``MOVE_SIMILAR`` alike, a PDF's
    trailer ``/ID`` kept by another reader's save."""
    v = vault()
    v.write("same.md", "a note copied as it is, word for word\n")
    v.write("edited.md", "one two three four five six seven eight nine ten\n")
    pdf = v.write("paper.pdf", _pdf("the paper"), age=10)
    v.sync()
    pages = {name: v.page_of(name) for name in ("same.md", "edited.md", "paper.pdf")}
    os.makedirs(v.root / "moved")
    shutil.copyfile(v.root / "same.md", v.root / "moved" / "same.md")
    v.write("moved/edited.md", "one two three four five six seven eight nine eleven\n")
    doc = pymupdf.open(pdf)
    doc[0].insert_text((72, 144), "a reader's note")
    doc.save(v.root / "moved" / "paper.pdf", incremental=False)
    doc.close()
    before = vault_disk.pdf_id(pdf)
    for name in ("same.md", "edited.md", "paper.pdf"):
        os.remove(v.root / name)
    if vault_disk.pdf_id(v.root / "moved" / "paper.pdf") != before:
        pytest.skip("this pymupdf writes a new /ID on a full save")
    os.utime(v.root / "moved" / "paper.pdf", (time.time() - 10, time.time() - 10))
    v.sync()
    assert {name: v.page_of(f"moved/{name}") for name in pages} == pages and v.page_count() == 3
    assert v.texts(pages["edited.md"]) == ["one two three four five six seven eight nine eleven"]
    v.quiet()


def test_a_block_moved_in_the_file_moves_in_the_page(vault):
    """A block's place: an anchored block cut and pasted keeps its id and
    moves; one with no anchor is, to ``md_lens.match``, a delete and an
    insert, and lands where the file has it all the same."""
    v = vault()
    v.write("Mv.md", "# A\n\nunder a ^ua\n\nplain\n\n# B\n\nunder b\n")
    v.sync()
    page = v.page_of("Mv.md")
    under_a = v.find(page, "under a")
    v.write("Mv.md", "# A\n\n# B\n\nunder b\n\nunder a ^ua\n\nplain\n")
    v.sync()
    assert v.tree(page) == [("# A", []), ("# B", [("under b", []), ("under a", []), ("plain", [])])]
    assert v.find(page, "under a") == under_a
    v.quiet()


def test_two_names_swapped_on_disk_each_keep_their_page(vault):
    """A swap has no path removed or added; the file ids say
    which file is which."""
    v = vault()
    v.write("A.md", "alpha text here\n")
    v.write("B.md", "beta words there\n")
    v.sync()
    a, b = v.page_of("A.md"), v.page_of("B.md")
    os.rename(v.root / "A.md", v.root / "tmp.md")
    os.rename(v.root / "B.md", v.root / "A.md")
    os.rename(v.root / "tmp.md", v.root / "B.md")
    v.sync()
    assert v.page_of("A.md") == b and v.page_of("B.md") == a
    assert v.texts(a) == ["alpha text here"] and v.texts(b) == ["beta words there"]
    assert v.info(a).title == "B" and v.info(b).title == "A"
    v.quiet()


def test_an_unrelated_note_of_the_same_name_elsewhere_is_no_move(vault):
    """The stem is a tie-break, never evidence."""
    v = vault()
    v.write("a/README.md", "how to build the first thing\n")
    v.sync()
    first = v.page_of("a/README.md")
    os.remove(v.root / "a" / "README.md")
    v.write("b/README.md", "an entirely different subject, many other words\n")
    v.sync()
    assert v.page_of("b/README.md") != first and v.state(first) == "live"   # removed only at the next scan
    v.sync()
    assert v.state(first) == "trashed"


def test_a_directory_renamed_on_disk_is_one_folder_renamed(vault):
    """One folder rename and one rewrite of the rows, the
    pages keeping their folder."""
    v = vault()
    for n in range(5):
        v.write(f"docs/n{n}.md", f"note {n}\n")
    v.sync()
    pages = {n: v.page_of(f"docs/n{n}.md") for n in range(5)}
    folder = v.info(pages[0]).folders[0]
    os.rename(v.root / "docs", v.root / "notes")
    v.sync()
    assert v.layout().folders[folder] == (v.folder, "notes")
    for n in range(5):
        assert v.page_of(f"notes/n{n}.md") == pages[n] and v.info(pages[n]).folders == [folder]
    v.quiet()


def test_a_removal_needs_two_scans_and_a_safe_save_is_no_removal(vault):
    """A file missing from one scan is only
    noted; one back before the next is no removal; one still gone at the
    second goes to Recently deleted, its row a tombstone."""
    v = vault()
    v.write("Gone.md", "soon gone\n")
    v.write("Saved.md", "safe save\n")
    v.sync()
    gone, saved = v.page_of("Gone.md"), v.page_of("Saved.md")
    os.remove(v.root / "Gone.md")
    os.remove(v.root / "Saved.md")
    v.sync()
    assert v.state(gone) == "live" and v.rows()["Gone.md"]["missing_since"]
    v.write("Saved.md", "safe save, edited\n")
    v.sync()
    assert v.state(gone) == "trashed" and v.state(saved) == "live" and v.texts(saved) == ["safe save, edited"]
    assert "Gone.md" not in v.rows() and [t["was"] for t in vault_store.tombstones(v.link["id"])] == ["Gone.md"]
    v.quiet()


def test_a_note_emptied_is_held_as_a_missing_one(vault):
    v = vault()
    v.write("E.md", "text that matters\n")
    v.sync()
    page = v.page_of("E.md")
    v.write("E.md", "")
    v.sync()
    assert v.texts(page) == ["text that matters"]
    v.sync()
    assert v.texts(page) == []
    assert [g["hash"] for g in v.rows()["E.md"]["gone"]] == [md_lens.content_hash("text that matters")]


def test_the_guard_holds_a_round_that_removes_most_files_until_it_is_confirmed(vault):
    """Removals since the last quiet round count,
    and nothing is written while held."""
    v = vault()
    for n in range(12):
        v.write(f"n{n}.md", f"note {n}\n")
    v.sync()
    pages = [v.page_of(f"n{n}.md") for n in range(12)]
    for n in range(7):
        os.remove(v.root / f"n{n}.md")
    v.sync()
    before = v.footprint()
    status = v.sync()
    assert status["held"] == {"removals": 7, "total": 12} and v.footprint() == before
    assert all(v.state(p) == "live" for p in pages)
    v.sync(confirm=True)
    assert [v.state(p) for p in pages] == ["trashed"] * 7 + ["live"] * 5


def test_a_file_put_back_brings_its_page_back_from_recently_deleted(vault):
    """A branch switched there and back finds its pages."""
    v = vault()
    v.write("Branch.md", "branch text\n")
    v.sync()
    page = v.page_of("Branch.md")
    data = (v.root / "Branch.md").read_bytes()
    os.remove(v.root / "Branch.md")
    v.sync()
    v.sync()
    assert v.state(page) == "trashed"
    v.write("Branch.md", data)
    v.sync()
    assert v.state(page) == "live" and v.page_of("Branch.md") == page and v.page_count() == 1
    v.quiet()


def test_a_note_still_being_written_waits(vault, monkeypatch):
    monkeypatch.setattr(vault_sync, "SETTLE_NOTE_S", 60)
    v = vault()
    v.write("Fresh.md", "just saved\n")
    status = v.sync()
    assert status["counts"]["waiting"] == 1 and v.page_count() == 0
    v.write("Fresh.md", "just saved\n", age=120)
    v.sync()
    assert v.page_count() == 1


def test_a_pdf_is_a_page_reading_it_in_place_with_the_note_of_its_stem(vault):
    """A linked page, one page for a PDF and its note, the round
    the one replacer of its file."""
    v = vault()
    data = _pdf("attention")
    v.write("Papers/Attention.pdf", data, age=10)
    v.write("Papers/Attention.md", "my reading notes\n")
    v.write("Papers/Alone.pdf", _pdf("alone"), age=10)
    v.sync()
    page = v.page_of("Papers/Attention.pdf")
    assert v.page_of("Papers/Attention.md") == page and v.page_count() == 2
    props = v.blocks(page)[page]["props"]
    assert props["doc"]["hash"] == storage.content_digest(data) and props["doc"]["at"] == "path"
    assert v.texts(page) == ["my reading notes"] and v.info(page).title == "Attention"
    assert linked_files._in_vault(page) and not storage.exists(v.ws, f"{props['doc']['hash']}.pdf")
    assert storage.open_path(v.ws, f"{props['doc']['hash']}.pdf") == v.root / "Papers" / "Attention.pdf"
    # new bytes: the page's PDF, the disk winning
    new = _pdf("attention, second edition")
    v.write("Papers/Attention.pdf", new, age=5)
    v.sync()
    assert v.blocks(page)[page]["props"]["doc"]["hash"] == storage.content_digest(new)
    v.quiet()


def test_a_pdf_with_an_open_thread_has_each_version_stored_as_it_comes(vault, owner, monkeypatch):
    """A comment thread on a vault's PDF keeps the bytes it shows stored
    (gamma/feedback/kept.py): stored at its open, every new file stored by
    the round as it comes, read in place all the same; once resolved, the
    next is read in place alone."""
    monkeypatch.setattr(comments, "write_listeners", [kept.after_comment])
    v = vault()
    first, second, third = _pdf("commented first"), _pdf("commented second"), _pdf("commented third")
    v.write("Paper.pdf", first, age=30)
    v.sync()
    page = v.page_of("Paper.pdf")
    thread = thread_on(owner, page, page, "the whole paper")
    assert kept.wait() and storage.exists(v.ws, f"{storage.content_digest(first)}.pdf")
    v.write("Paper.pdf", second, age=20)
    v.sync()
    doc = v.blocks(page)[page]["props"]["doc"]
    assert doc["hash"] == storage.content_digest(second) and doc["at"] == "path"
    assert storage.exists(v.ws, f"{doc['hash']}.pdf") and storage.exists(v.ws, f"{storage.content_digest(first)}.pdf")
    owner.post(f"/api/comments/{thread['id']}/resolve", json={"resolved": True}).raise_for_status()
    v.write("Paper.pdf", third, age=10)
    v.sync()
    assert not storage.exists(v.ws, f"{storage.content_digest(third)}.pdf")
    v.quiet()


def test_a_pdfs_notes_are_the_note_its_front_matter_names_or_the_one_of_its_stem_in_the_notes_folder(vault):
    """A PDF's notes: the note whose ``source`` names it, else the one of its
    stem in the notes folder."""
    v = vault(notes_folder="Notes")
    v.write("Papers/Kept.pdf", _pdf("kept"), age=10)
    v.write("Notes/Kept.md", "notes kept apart\n")
    v.write("Papers/Named.pdf", _pdf("named"), age=10)
    v.write("Reading.md", "---\nsource: Papers/Named.pdf\n---\nwhat I read\n")
    v.sync()
    assert v.page_of("Notes/Kept.md") == v.page_of("Papers/Kept.pdf")
    assert v.page_of("Reading.md") == v.page_of("Papers/Named.pdf") and v.page_count() == 2
    v.quiet()


def test_a_stored_pdf_pull_copied_out_is_followed_when_it_changes_on_disk(vault, owner):
    """A library page's PDF filed in the vault keeps its stored
    copy; the copy on disk is followed, the disk winning."""
    v = vault()
    data = _pdf("stored first")
    r = owner.post("/api/uploads", files={"file": ("paper.pdf", data, "application/pdf")})
    page = make_page(owner, "Followed", {"folders": [v.folder], **doc_props(r.json()["doc_id"])})["id"]
    v.sync()
    assert (v.root / "Followed.pdf").read_bytes() == data
    assert v.blocks(page)[page]["props"]["doc"]["at"] == "store"
    new = _pdf("rewritten on disk")
    v.write("Followed.pdf", new, age=5)
    v.sync()
    props = v.blocks(page)[page]["props"]       # the disk's bytes, read where they are from now on
    assert props["doc"] == {"format": "pdf", "name": "Followed.pdf", "hash": storage.content_digest(new), "ext": ".pdf",
                            "at": "path"}
    assert storage.exists(v.ws, f"{r.json()['doc_id']}.pdf")       # the stored copy stays
    v.quiet()


def test_a_pdf_gone_leaves_its_page_and_notes_gone_leave_the_highlights(vault):
    """A PDF gone leaves its page reading a missing file; a paper's notes gone
    take their plain blocks, its highlights staying."""
    v = vault()
    v.write("P.pdf", _pdf("p"), age=10)
    v.write("P.md", "a plain note\n")
    v.write("Q.pdf", _pdf("q"), age=10)
    v.write("Q.md", "note of q\n")
    v.sync()
    p, q = v.page_of("P.pdf"), v.page_of("Q.pdf")
    mark = {"pdf_position": {"pageNumber": 1, "boundingRect": {"x1": 0, "y1": 0, "x2": 1, "y2": 1},
                             "rects": [{"x1": 0, "y1": 0, "x2": 1, "y2": 1}], "width": 1, "height": 1}, "quote": "q text"}
    v.edit(q, insert(q, "my highlight", mark))
    v.sync()
    plain = v.find(q, "note of q")
    os.remove(v.root / "P.pdf")
    os.remove(v.root / "Q.md")
    v.sync()
    v.sync()
    assert v.state(p) == "live" and v.texts(p) == ["a plain note"] and "P.md" in v.rows() and "P.pdf" not in v.rows()
    assert v.state(q) == "live" and v.texts(q) == ["my highlight"]
    assert [g["id"] for g in v.rows()["Q.pdf"]["gone"]] == [plain]
    assert not (v.root / "Q.md").exists()      # no file until the page changes
    v.quiet()


def test_a_page_trashed_in_gamma_whose_file_changed_comes_back(vault):
    """The file is the authority. With pull off nothing moves its
    file to the trash meanwhile."""
    v = vault(pull=0)
    v.write("T.md", "first\n")
    v.sync()
    page = v.page_of("T.md")
    with connect_pages_db(v.ws) as conn:
        trash_page(v.ws, conn, page, actor=v.user)
    v.write("T.md", "second\n")
    v.sync()
    assert v.state(page) == "live" and v.texts(page) == ["second"]


# --- pull: Gamma's changes ------------------------------------------------------------------------

def test_a_page_made_in_gamma_gets_its_file_and_a_stored_pdf_is_copied_beside_it(vault, owner):
    """``<dir>/<title>.md``, a free name, the stored PDF copied out
    and followed (``ours``)."""
    v = vault()
    sub = _subfolder(owner, v, "Sub")
    page = make_page(owner, "A: new page", {"folders": [sub]})["id"]
    v.edit(page, insert(page, "written in Gamma"))
    data = _pdf("stored")
    r = owner.post("/api/uploads", files={"file": ("paper.pdf", data, "application/pdf")})
    paper = make_page(owner, "Stored paper", {"folders": [v.folder], **doc_props(r.json()["doc_id"])})["id"]
    v.sync()
    assert v.text("Sub/A new page.md") == "written in Gamma\n"
    assert (v.root / "Stored paper.pdf").read_bytes() == data
    assert v.rows()["Stored paper.pdf"]["ours"] == 1 and v.rows()["Stored paper.pdf"]["page_id"] == paper
    assert "Stored paper.md" not in v.rows()            # nothing made for a PDF only read
    assert v.rows()["Sub/A new page.md"]["ours"] == 1
    v.quiet()


def _subfolder(owner, v, name):
    path = [n for n in owner.get("/api/blocks/folders/subtree").json()["block"]["children"] if n["id"] == v.folder]
    return _tree_block(owner, "folders", [path[0]["content"], name])


def test_an_edit_in_gamma_changes_only_its_lines_and_keeps_the_dress(vault):
    """A patch, never a render: CRLF, ``*`` bullets, an
    unknown key and a comment stay byte for byte."""
    v = vault()
    raw = "---\nmood: calm\n---\r\n# Head\r\n\r\n*   first item\r\n*   second item\r\n\r\n%%hidden%%\r\n"
    v.write("Dress.md", raw)
    v.sync()
    page = v.page_of("Dress.md")
    v.edit(page, set_(v.find(page, "- second item"), "- second item, edited"))
    v.sync()
    assert v.text("Dress.md") == raw.replace("second item", "second item, edited")
    v.quiet()


def test_an_open_fence_typed_in_gamma_is_not_written_and_the_page_says_why(vault):
    """``Unwritable``, nothing written, the reason on the row."""
    v = vault()
    v.write("F.md", "one\n\ntwo\n")
    v.sync()
    page = v.page_of("F.md")
    v.edit(page, set_(v.find(page, "one"), "```\none"))
    status = v.sync(ok=False)
    assert v.text("F.md") == "one\n\ntwo\n"
    assert "open code block" in v.rows()["F.md"]["problem"] and status["kept"][0]["path"] == "F.md"
    v.edit(page, set_(v.find(page, "```\none"), "one again"))
    v.sync()
    assert v.text("F.md") == "one again\n\ntwo\n" and v.rows()["F.md"]["problem"] == ""


def test_shape_ops_go_to_gamma_only_where_the_vault_pushes_everything_and_no_caret_is_there(vault, monkeypatch):
    """A block holding a blank line reads back as
    two; Gamma takes that shape, but not under a caret, where the file waits
    for it (retried: a file of two blocks Gamma does not have would name one
    no block is), and never in a vault that does not push notes."""
    v = vault()
    v.write("S.md", "start\n")
    v.sync()
    page = v.page_of("S.md")
    block = v.find(page, "start")
    monkeypatch.setattr(v.remote, "carets", lambda page_id: {block})
    v.edit(page, set_(block, "one\n\ntwo"))
    v.sync()
    assert v.text("S.md") == "start\n" and v.texts(page) == ["one\n\ntwo"]
    monkeypatch.setattr(v.remote, "carets", lambda page_id: set())
    v.sync()
    assert v.texts(page) == ["one", "two"] and v.text("S.md") == "one\n\ntwo\n"
    v.quiet()
    pulled = vault("pulled", push=folder_links.PUSH_OFF)
    other = make_page(pulled.owner, "Kept", {"folders": [pulled.folder]})["id"]
    pulled.edit(other, insert(other, "x\n\ny"))
    pulled.sync()
    assert pulled.text("Kept.md") == "x\n\ny\n" and pulled.texts(other) == ["x\n\ny"]
    pulled.quiet()


def test_a_file_changed_under_a_pull_is_pushed_first_or_kept(vault):
    """With push on the disk's change is read first and the page
    written after; with push off it is kept, reported, and written only by a
    force pull, whose replaced bytes go to the history."""
    v = vault()
    v.write("U.md", "a\n\nb\n")
    v.sync()
    page = v.page_of("U.md")
    v.edit(page, set_(v.find(page, "b"), "b from gamma"))
    v.write("U.md", "a from disk\n\nb\n")
    v.sync()
    assert v.text("U.md") == "a from disk\n\nb from gamma\n" and v.texts(page) == ["a from disk", "b from gamma"]

    kept = vault("kept", push=folder_links.PUSH_OFF)
    other = make_page(kept.owner, "Kept note", {"folders": [kept.folder]})["id"]
    kept.edit(other, insert(other, "gamma's"))
    kept.sync()
    kept.edit(other, set_(kept.find(other, "gamma's"), "gamma's, edited"))
    kept.write("Kept note.md", "mine, edited on disk\n")
    status = kept.sync(ok=False)
    assert kept.text("Kept note.md") == "mine, edited on disk\n"
    assert status["kept"] == [{"path": "Kept note.md", "why": status["kept"][0]["why"], "force": True}]
    assert kept.texts(other) == ["gamma's, edited"]
    kept.sync(force="pull")
    assert kept.text("Kept note.md") == "gamma's, edited\n"
    assert vault_store.history(kept.link["id"], "Kept note.md")[0]["data"] == b"mine, edited on disk\n"
    assert [(row[1], row[3], row[6]) for row in kept.conflicts(other)] == [("file_won", "mine, edited on disk", 1)]
    kept.quiet()


def test_a_write_another_program_holds_saves_no_base_and_is_tried_again(vault, monkeypatch):
    """``replace_note`` with no same-size
    shortcut; a final PermissionError is the file's problem, not its base."""
    v = vault()
    v.write("H.md", "the cat sat\n")
    v.sync()
    page = v.page_of("H.md")
    v.edit(page, set_(v.find(page, "the cat sat"), "the dog sat"))
    real = vault_disk.replace_note

    def held(root, rel, data, ready=None, **kw):
        raise PermissionError("held by another program")
    monkeypatch.setattr(vault_disk, "replace_note", held)
    status = v.sync(ok=False)
    assert v.text("H.md") == "the cat sat\n" and "another program" in status["kept"][0]["why"]
    assert v.rows()["H.md"]["text"] == "the cat sat\n" and not vault_store.pending(v.link["id"])
    monkeypatch.setattr(vault_disk, "replace_note", real)
    v.sync()
    assert v.text("H.md") == "the dog sat\n" and v.texts(page) == ["the dog sat"]
    assert vault_store.history(v.link["id"], "H.md")[0]["data"] == b"the cat sat\n"
    v.quiet()


def test_a_title_renames_the_file_a_title_key_is_patched_and_a_case_change_works(vault):
    """A title changed in Gamma renames the note, but one a ``title:`` key
    holds is patched there; ``Plain`` to ``plain`` is a rename too."""
    v = vault()
    v.write("foo-bar.md", "---\ntitle: Foo Bar\n---\nbody\n")
    v.write("Plain.md", "plain\n")
    v.sync()
    keyed, plain = v.page_of("foo-bar.md"), v.page_of("Plain.md")
    assert v.info(keyed).title == "Foo Bar"
    v.quiet()                                            # the stem never renames a file nobody touched
    v.edit(keyed, set_(keyed, "Foo Baz"))
    v.edit(plain, set_(plain, "plain"))
    v.sync()
    assert v.text("foo-bar.md") == "---\ntitle: Foo Baz\n---\nbody\n"
    assert v.page_of("plain.md") == plain and os.listdir(v.root).count("plain.md") == 1
    v.quiet()


def test_two_pages_of_one_title_get_two_names_that_stay(vault, owner):
    v = vault()
    a = make_page(owner, "Foo", {"folders": [v.folder]})["id"]
    b = make_page(owner, "Foo", {"folders": [v.folder]})["id"]
    v.sync()
    assert {v.page_of("Foo.md"), v.page_of("Foo 2.md")} == {a, b}
    v.quiet()


def test_a_first_filing_moves_the_file_and_a_folder_renamed_renames_its_directory(vault, owner):
    """A file moved on disk takes the old
    filing's index, so pull never moves it back."""
    v = vault()
    one, two = _subfolder(owner, v, "One"), _subfolder(owner, v, "Two")
    page = make_page(owner, "Moving", {"folders": [one]})["id"]
    v.edit(page, insert(page, "text"))
    v.sync()
    assert v.text("One/Moving.md") == "text\n"
    v.edit(page, {"op": "set", "id": page, "props": {"folders": [two, one]}})
    v.sync()
    assert not (v.root / "One" / "Moving.md").exists() and v.text("Two/Moving.md") == "text\n"
    commit_ops(v.ws, "folders", [{"op": "set", "id": two, "content": "Deux"}], actor=v.user)
    v.sync()
    assert v.text("Deux/Moving.md") == "text\n" and not (v.root / "Two").exists()
    os.makedirs(v.root / "Three")
    os.rename(v.root / "Deux" / "Moving.md", v.root / "Three" / "Moving.md")
    v.sync()
    folders = v.info(page).folders
    assert v.layout().folders[folders[0]][1] == "Three" and folders[1] == one
    assert v.text("Three/Moving.md") == "text\n"
    v.quiet()


def test_a_folder_moved_in_gamma_moves_its_directory_and_a_deleted_one_takes_its_directory(vault, owner):
    """A folder moved is its directory moved; a folder deleted
    unfiles its pages, which leave, and the directory goes once empty."""
    v = vault()
    one, two = _subfolder(owner, v, "One"), _subfolder(owner, v, "Two")
    page = make_page(owner, "Inside", {"folders": [two]})["id"]
    v.edit(page, insert(page, "inside"))
    v.sync()
    commit_ops(v.ws, "folders", [{"op": "move", "id": two, "parent": one}], actor=v.user)
    v.sync()
    assert v.text("One/Two/Inside.md") == "inside\n" and not (v.root / "Two").exists()
    v.owner.delete(f"/api/folders/{two}")
    v.sync()
    assert not (v.root / "One" / "Two").exists() and (v.root / "One").is_dir() and v.state(page) == "live"
    assert not v.rows() and "One/Two" not in vault_store.dirs(v.link["id"])


def test_a_sub_pages_file_moved_on_disk_makes_it_a_library_page_there(vault, owner):
    """A sub-page is filed where its top is; its file moved
    on disk takes it out of its parent."""
    v = vault()
    top = make_page(owner, "Top", {"folders": [v.folder]})["id"]
    sub = make_page(owner, "Sub page", {"parent_page": top})["id"]
    v.edit(sub, insert(sub, "a sub-page"))
    v.sync()
    assert v.page_of("Sub page.md") == sub
    os.makedirs(v.root / "Away")
    os.rename(v.root / "Sub page.md", v.root / "Away" / "Sub page.md")
    v.sync()
    props = v.blocks(sub)[sub]["props"]
    assert "parent_page" not in props and v.layout().folders[props["folders"][0]] == (v.folder, "Away")
    v.quiet()


def test_a_page_filed_below_two_vaults_is_the_first_vaults(vault, owner):
    """A vault leaves out the pages of a vault below it and of one
    made before it that is not above it, and the second of two beside each
    other says which it left (``status.shared``); one above a vault says
    nothing of the pages below it."""
    outer = vault("outer")
    inner = vault("inner", folder=_subfolder(owner, outer, "Inner"))
    sibling = vault("sibling")
    shared = make_page(owner, "Shared", {"folders": [inner.folder, sibling.folder]})["id"]
    rows = [(outer, "2026-01-01"), (inner, "2026-01-02"), (sibling, "2026-01-03")]
    with connect_users_db() as conn:
        for v, at in rows:
            conn.execute("INSERT INTO folder_links (id, workspace_id, folder_id, path, notes, created_by, created_at, cursor, "
                         "status, remote_url, token, token_id, kind, page_id, push, state, pull) VALUES "
                         "(?, ?, ?, ?, 1, ?, ?, '', '{}', '', '', '', 'folder', '', 2, '{}', 1)",
                         (v.link["id"], v.ws, v.folder, str(v.root), v.user, at))
    try:
        assert shared not in vault_sync.remote_for(outer.link).layout().pages
        assert shared in vault_sync.remote_for(inner.link).layout().pages
        assert shared not in vault_sync.remote_for(sibling.link).layout().pages
        assert vault_sync.remote_for(sibling.link).layout().shared == [(shared, "Shared")]
        assert vault_sync.remote_for(outer.link).layout().shared == []
        assert vault_sync.remote_for(sibling.link).layout(shared).shared == []        # a page's round says nothing
        status = vault_sync.sync(sibling.link, remote=vault_sync.remote_for(sibling.link)).status
        assert status["shared"] == [{"page": shared, "title": "Shared"}] and not (sibling.root / "Shared.md").exists()
        assert "shared" not in vault_sync.sync(inner.link, remote=vault_sync.remote_for(inner.link)).status
        assert (inner.root / "Shared.md").is_file()
    finally:
        with connect_users_db() as conn:
            conn.executemany("DELETE FROM folder_links WHERE id = ?", [(v.link["id"],) for v, _at in rows])


@pytest.mark.parametrize("kept_before", [False, True])
def test_a_link_that_only_pulls_takes_no_page_from_a_vault_that_pushes(vault, owner, kept_before):
    """One that only pulls below a pushing vault took
    its pages, and the vault sent the person's notes to its trash. Between two
    vaults that only pull, a page below both is still the first's; so it is
    for a folder kept on disk before synced vaults, which is one."""
    outer = vault("outer")
    inner = vault("inner", folder=_subfolder(owner, outer, "Inner"))
    page = make_page(owner, "Below", {"folders": [inner.folder]})["id"]
    with connect_users_db() as conn:
        conn.execute("INSERT INTO folder_links (id, workspace_id, folder_id, path, notes, created_by, created_at, cursor, "
                     "status, remote_url, token, token_id, kind, page_id, push, state, pull) VALUES "
                     "(?, ?, ?, ?, 1, ?, '2026-01-01', '', '{}', '', '', '', 'folder', '', 0, ?, 1)",
                     (inner.link["id"], inner.ws, inner.folder, str(inner.root), inner.user,
                      json.dumps({} if kept_before else inner.link["state"])))
    try:
        assert page in vault_sync.remote_for(outer.link).layout().pages
        pulling = {**outer.link, "push": folder_links.PUSH_OFF}
        assert page not in vault_sync.remote_for(pulling).layout().pages
    finally:
        with connect_users_db() as conn:
            conn.execute("DELETE FROM folder_links WHERE id = ?", (inner.link["id"],))


def test_a_round_takes_the_cursor_first_and_cleans_stale_temporary_files(vault):
    v = vault()
    stale = v.write(".gamma-dead.tmp", "left by a write that died", age=vault_disk.STALE_TEMP_S + 5)
    fresh = v.write(".gamma-busy.tmp", "a write going on now")
    v.write("C.md", "c\n")
    with connect_pages_db(v.ws) as conn:
        seq = newest_change_seq(conn)
    result = vault_sync.sync(v.link, remote=v.remote)
    assert int(result.cursor) >= seq and not stale.exists() and fresh.exists()


def test_a_page_trashed_in_gamma_moves_its_files_to_the_trash_and_a_restore_brings_them_back(vault, owner):
    """The row records the trash path first;
    back under a free name when another file took the place."""
    v = vault()
    page = make_page(owner, "Trashed", {"folders": [v.folder]})["id"]
    v.edit(page, insert(page, "keep me"))
    v.sync()
    with connect_pages_db(v.ws) as conn:
        trash_page(v.ws, conn, page, actor=v.user)
    v.sync()
    assert not (v.root / "Trashed.md").exists()
    (tomb,) = vault_store.tombstones(v.link["id"])
    assert tomb["was"] == "Trashed.md" and (v.root / tomb["path"]).read_bytes() == b"keep me\n"
    v.write("Trashed.md", "a person's new file in its place\n")
    v.sync()
    with connect_pages_db(v.ws) as conn:
        restore_page(v.ws, conn, page, actor=v.user)
    v.sync()
    assert v.text("Trashed 2.md") == "keep me\n" and v.page_of("Trashed 2.md") == page
    assert v.text("Trashed.md") == "a person's new file in its place\n" and v.page_of("Trashed.md") != page
    v.quiet()


def test_a_page_that_leaves_the_vault_stores_its_pdf_first_and_its_files_go_to_the_trash(vault):
    v = vault()
    v.write("Leaving.pdf", _pdf("leaving"), age=10)
    v.write("Leaving.md", "notes\n")
    v.sync()
    page = v.page_of("Leaving.pdf")
    doc = v.blocks(page)[page]["props"]["doc"]["hash"]
    assert v.blocks(page)[page]["props"]["doc"] == {"format": "pdf", "name": "Leaving.pdf", "hash": doc, "ext": ".pdf",
                                                    "at": "path"}
    v.edit(page, {"op": "set", "id": page, "props": {"folders": None}})
    v.sync()
    # stored, the same file: its hash and name kept
    assert v.blocks(page)[page]["props"]["doc"] == {"format": "pdf", "name": "Leaving.pdf", "hash": doc, "ext": ".pdf",
                                                    "at": "store"}
    assert storage.exists(v.ws, f"{doc}.pdf")
    assert not (v.root / "Leaving.pdf").exists() and not (v.root / "Leaving.md").exists()
    assert v.state(page) == "live" and v.texts(page) == ["notes"] and not v.rows()
    assert sorted(os.listdir(v.root / vault_disk.TRASH / os.listdir(v.root / vault_disk.TRASH)[0])) == ["Leaving.md",
                                                                                                         "Leaving.pdf"]


def test_a_key_only_change_writes_no_front_matter_into_a_persons_note(vault):
    """A metadata lookup writes nothing to a note
    that had no front matter."""
    v = vault()
    v.write("K.md", "person's note\n")
    v.sync()
    page = v.page_of("K.md")
    v.edit(page, {"op": "set", "id": page, "props": {"meta": {"title": "K", "doi": "10.1/x"}}})
    v.quiet()
    assert v.text("K.md") == "person's note\n"


def test_a_highlight_is_a_callout_and_one_dropped_comes_back_with_its_rectangles(vault):
    """A callout gone from the file is a deleted highlight
    kept in ``gone`` with its properties; the same text back, by its anchor,
    is the same highlight, rectangles and all. A linter's ``>[!quote]`` keeps
    it a highlight."""
    v = vault()
    v.write("Paper.pdf", _pdf("paper"), age=10)
    v.write("Paper.md", "my notes\n")
    v.sync()
    page = v.page_of("Paper.pdf")
    mark = {"pdf_position": {"pageNumber": 3, "boundingRect": {"x1": 1, "y1": 2, "x2": 3, "y2": 4},
                             "rects": [{"x1": 1, "y1": 2, "x2": 3, "y2": 4}], "width": 10, "height": 10},
            "quote": "the quoted text"}
    hl = new_block_id()
    v.edit(page, insert(page, "what I think", mark, hl))
    v.sync()
    written = v.text("Paper.md")
    assert written.startswith("my notes\n\n> [!quote] [[Paper.pdf#page=3|p. 3]]\n> the quoted text\n>\n> what I think\n")
    assert f"^{vault_sync._marker(hl)}" in written
    v.write("Paper.md", "my notes\n")
    v.sync()
    assert hl not in v.blocks(page)
    v.write("Paper.md", written.replace("> [!quote]", ">[!quote]"))
    v.sync()
    assert v.blocks(page)[hl]["props"] == mark and v.blocks(page)[hl]["content"] == "what I think"
    v.quiet()


def test_a_callout_with_no_anchor_gamma_knows_is_a_highlight_the_relocation_run_places_dashed(vault):
    """A vault copied from another computer brings its
    highlights as callouts that link the page's PDF; each is a highlight with
    its quote and page, which the relocation run then places where its
    quote is, as a rough match for the person to check."""
    line = "the quick brown wombat digs a long burrow"
    doc = pymupdf.open()
    doc.new_page().insert_text((72, 72), "front matter")
    doc.new_page().insert_text((72, 300), line)
    v = vault()
    v.write("Paper.pdf", doc.tobytes(), age=10)
    v.write("Paper.md", f"my notes\n\n> [!quote] [[Paper.pdf#page=1|p. 1]]\n> {line}\n>\n> what I think\n\n^b3x9q\n")
    v.sync()
    page = v.page_of("Paper.pdf")
    hl = v.find(page, "what I think")
    job = jobs.latest(v.user, "relocate-highlights", f"{page}:{v.blocks(page)[page]['props']['doc']['hash']}")
    assert job is not None and jobs.wait(job["id"])["state"] == "done"
    props = v.blocks(page)[hl]["props"]
    assert props["quote"] == line and props["pdf_position"]["pageNumber"] == 2
    assert props["pdf_position"]["approx"] is True and props["pdf_position"]["rects"]
    assert props["pdf_position"]["prev"] == {"doc_id": job["params"]["doc_id"], "pageNumber": 1, "rects": [], "quote": line}
    v.sync()
    assert v.text("Paper.md") == f"my notes\n\n> [!quote] [[Paper.pdf#page=2|p. 2]]\n> {line}\n>\n> what I think\n\n^b3x9q\n"
    v.quiet()
    assert jobs.latest(v.user, "relocate-highlights", f"{page}:{job['params']['doc_id']}")["id"] == job["id"]


def test_highlights_whose_run_could_not_start_are_asked_again_at_the_next_round(vault, monkeypatch):
    """A vault copied over brings many papers at once: the account's
    background tasks fill, and a page whose run could not start waits in
    the link's state for the next whole round."""
    line = "the quick brown wombat digs a long burrow"
    v = vault()
    v.write("Paper.pdf", _pdf(line), age=10)
    v.write("Paper.md", f"> [!quote] [[Paper.pdf#page=1|p. 1]]\n> {line}\n")
    with monkeypatch.context() as patch:
        patch.setattr(pages_mod, "_start_relocation", lambda *a, **kw: (None, "the background tasks are full"))
        v.sync()
    page = v.page_of("Paper.pdf")
    assert v.link["state"]["placing"] == [page]
    v.sync()
    assert "placing" not in v.link["state"]
    job = jobs.latest(v.user, "relocate-highlights", f"{page}:{v.blocks(page)[page]['props']['doc']['hash']}")
    assert jobs.wait(job["id"])["state"] == "done"
    (hl,) = [b for b, block in v.blocks(page).items() if b != page]
    assert v.blocks(page)[hl]["props"]["pdf_position"]["rects"]


def test_a_block_link_writes_an_anchor_in_the_other_note(vault):
    """Linking block B from page A writes B's anchor in B's
    file, as a change Gamma made."""
    v = vault()
    v.write("Target.md", "first\n\nthe linked block\n")
    v.write("Source.md", "see below\n")
    v.sync()
    target, source = v.page_of("Target.md"), v.page_of("Source.md")
    block = v.find(target, "the linked block")
    v.edit(source, set_(v.find(source, "see below"), f"see [[{block}]]"))
    v.sync()
    marker = vault_sync._marker(block)
    assert v.text("Source.md") == f"see [[Target#^{marker}]]\n"
    assert v.text("Target.md") == f"first\n\nthe linked block ^{marker}\n"
    assert v.texts(source) == [f"see [[{block}]]"]
    v.quiet()
    # asked from the hot loop's round of the linking page: kept on the other
    # note's row, and written by the next round that reaches it
    other = v.find(target, "first")
    v.edit(source, set_(v.find(source, f"see [[{block}]]"), f"see [[{block}]] and [[{other}]]"))
    v.sync_page(source)
    assert v.text("Target.md") == f"first\n\nthe linked block ^{marker}\n"
    v.sync()
    assert v.text("Target.md") == f"first ^{vault_sync._marker(other)}\n\nthe linked block ^{marker}\n"
    assert v.texts(source) == [f"see [[{block}]] and [[{other}]]"]
    v.quiet()


def test_a_block_link_into_a_note_this_round_cannot_write_is_a_link_to_its_page(vault):
    """``read_only`` was never filled, so a link to a
    block of a note with a problem wrote an anchor that note never got."""
    v = vault()
    v.write("Target.md", "first\n\nthe linked block\n")
    v.write("Source.md", "see below\n")
    v.sync()
    target, source = v.page_of("Target.md"), v.page_of("Source.md")
    block = v.find(target, "the linked block")
    vault_store.put(v.link["id"], "Target.md", problem="not written to disk: an open code block")
    v.edit(source, set_(v.find(source, "see below"), f"see [[{block}]]"))
    v.sync(ok=False)
    assert v.text("Source.md") == "see [[Target]]\n" and v.text("Target.md") == "first\n\nthe linked block\n"


# --- force ------------------------------------------------------------------------------------------

def test_force_push_makes_each_page_its_file_and_records_what_it_replaced(vault):
    v = vault()
    v.write("FP.md", "a\n\nb\n")
    v.sync()
    page = v.page_of("FP.md")
    v.edit(page, set_(v.find(page, "a"), "a in gamma"), insert(page, "c in gamma"))
    v.sync(force="push")
    assert v.texts(page) == ["a", "b"]
    assert sorted((row[2], row[3], row[6]) for row in v.conflicts(page)) == [("a in gamma", "a", 1), ("c in gamma", "", 1)]
    v.quiet()


def test_a_note_that_names_its_page_by_gamma_id_is_that_page_s_until_a_force_decides(vault):
    """docs/research/vaults.md "What changes for today's links": ``gamma_id`` is
    read where a file has it. A new note naming a page of the vault that has no
    notes file is that page's notes, whose base names none of its blocks: kept,
    never merged (every block again) and never a second page, until a force
    push makes the page the file."""
    v = vault()
    v.write("Paper.pdf", _pdf("named by id"), age=10)
    v.sync()
    page = v.page_of("Paper.pdf")
    assert "notes" not in {row["kind"] for row in v.rows().values() if row["page_id"] == page}   # a paper with no notes
    v.write("Copied notes.md", f"---\ngamma_id: {page}\n---\nwhat the copy says\n", age=10)
    pages = v.page_count()
    status = v.sync(ok=False)
    assert status["kept"] == [{"path": "Copied notes.md", "why": vault_sync.UNKNOWN, "force": True}]
    assert v.page_of("Copied notes.md") == page and v.page_count() == pages and v.texts(page) == []
    assert v.sync(ok=False)["kept"] == status["kept"]                 # the same, round after round
    v.sync(force="push")
    assert v.texts(page) == ["what the copy says"] and v.rows()["Copied notes.md"]["nodes"]
    v.quiet()


@pytest.mark.parametrize("asked", [{"direction": "pul"}, {"force": "both"}])
def test_a_round_takes_only_a_direction_and_a_force_it_knows(asked):
    """A misspelt direction read as neither push nor pull: the round did
    nothing and said it was up to date."""
    with pytest.raises(ValueError):
        vault_sync.Round({"id": "x", "path": "x"}, None, **asked)


# --- crash safety: a kill at each point of the journal ------------------------------------------------

def _kill_once(monkeypatch, module, name):
    real = getattr(module, name)
    calls = []

    def dying(*args, **kwargs):
        if not calls:
            calls.append(1)
            raise Killed(name)
        return real(*args, **kwargs)
    monkeypatch.setattr(module, name, dying)


def _kill_after(monkeypatch, module, name):
    """``name`` runs, then the process dies before anything after it."""
    real = getattr(module, name)
    calls = []

    def dying(*args, **kwargs):
        out = real(*args, **kwargs)
        if not calls:
            calls.append(1)
            raise Killed(name)
        return out
    monkeypatch.setattr(module, name, dying)


@pytest.mark.parametrize("point", ["before", "after"])
def test_a_kill_around_a_push_converges_with_nothing_doubled(vault, monkeypatch, point):
    """Ops committed and the base not yet promoted, or
    nothing committed; the next round reads the page's log for the token."""
    v = vault()
    v.write("K.md", "one\n")
    v.sync()
    page = v.page_of("K.md")
    v.write("K.md", "one\n\ntwo\n")
    (_kill_once if point == "before" else _kill_after)(monkeypatch, v.remote, "apply")
    with pytest.raises(Killed):
        v.sync()
    monkeypatch.undo()
    _quick_again(monkeypatch)
    v.sync()
    assert v.texts(page) == ["one", "two"] and v.text("K.md") == "one\n\ntwo\n"
    v.quiet()


def test_a_push_names_its_first_batch_with_the_journals_token(vault, monkeypatch):
    """Crash safety: ``ops.apply_batches(batch_id=)`` names
    the first of a push's batches (``MAX_OPS`` each) with its pending's token,
    which is what the journal looks for after a crash (``landed``)."""
    monkeypatch.setattr(vault_sync, "MAX_OPS", 2)
    v = vault()
    v.write("B.md", "a\n")
    v.sync()
    page = v.page_of("B.md")
    tokens, put = [], vault_store.put_pending
    monkeypatch.setattr(vault_store, "put_pending", lambda link_id, path, kind, pending: (
        tokens.append(pending["token"]) if pending.get("op") == "push" else None, put(link_id, path, kind, pending))[1])
    v.write("B.md", "a\n\nb\n\nc\n\nd\n\ne\n")
    v.sync()
    with connect_pages_db(v.ws) as conn:
        named = conn.execute("SELECT batch_id FROM page_ops WHERE page_id = ? AND client = ? ORDER BY seq",
                             (page, vault_sync.CLIENT)).fetchall()
    assert named[-2:] == [(tokens[-1],), ("",)] and v.remote.landed(page, tokens[-1])
    assert v.texts(page) == ["a", "b", "c", "d", "e"]


def test_a_kill_after_a_clash_was_written_leaves_one_conflict_with_the_ops(vault, monkeypatch):
    """The conflict row is in the ops' transaction, so a kill
    after the commit cannot lose Gamma's text, nor write it twice."""
    v = vault()
    v.write("X.md", "The quick brown fox jumps over the dog\n")
    v.sync()
    page = v.page_of("X.md")
    gammas = "The quick brown fox jumps over the lazy dog today"
    v.edit(page, set_(v.find(page, "The quick brown fox jumps over the dog"), gammas))
    v.write("X.md", "A slow red fox leaps across a cat\n")
    _kill_once(monkeypatch, vault_store, "promote")
    with pytest.raises(Killed):
        v.sync()
    assert [row[2] for row in v.conflicts(page)] == [gammas]
    monkeypatch.undo()
    _quick_again(monkeypatch)
    v.sync()
    assert len(v.conflicts(page)) == 1 and v.texts(page) == ["A slow red fox leaps across a cat"]
    v.quiet()


@pytest.mark.parametrize("point", ["before", "after"])
def test_a_kill_around_a_new_page_converges_with_one_page(vault, monkeypatch, point):
    v = vault()
    v.write("New.md", "fresh\n")
    (_kill_once if point == "before" else _kill_after)(monkeypatch, v.remote, "create")
    with pytest.raises(Killed):
        v.sync()
    monkeypatch.undo()
    _quick_again(monkeypatch)
    v.sync()
    assert v.page_count() == 1 and v.texts(v.page_of("New.md")) == ["fresh"]
    v.quiet()


@pytest.mark.parametrize("point", ["before", "after"])
def test_a_kill_around_a_pull_write_converges_with_no_line_doubled(vault, monkeypatch, point):
    """The file renamed and the base not saved
    must not read as a change on disk that inserts the line again."""
    v = vault()
    v.write("W.md", "x\n")
    v.sync()
    page = v.page_of("W.md")
    v.edit(page, insert(page, "y"))
    (_kill_once if point == "before" else _kill_after)(monkeypatch, vault_disk, "replace_note")
    with pytest.raises(Killed):
        v.sync()
    monkeypatch.undo()
    _quick_again(monkeypatch)
    v.sync()
    assert v.texts(page) == ["x", "y"] and v.text("W.md") == "x\n\ny\n"
    v.quiet()


def test_a_kill_after_a_new_file_was_written_makes_no_second_page(vault, monkeypatch, owner):
    """A file pull made with no row would read as
    added, a second page for one page."""
    v = vault()
    page = make_page(owner, "Made", {"folders": [v.folder]})["id"]
    v.edit(page, insert(page, "body"))
    _kill_after(monkeypatch, vault_disk, "write_new")
    with pytest.raises(Killed):
        v.sync()
    monkeypatch.undo()
    _quick_again(monkeypatch)
    v.sync()
    assert v.page_count() == 1 and v.page_of("Made.md") == page and v.text("Made.md") == "body\n"
    v.quiet()


def test_a_kill_on_the_way_to_the_trash_leaves_the_file_findable(vault, monkeypatch, owner):
    """The trash path is recorded before the move,
    and the next round finishes it."""
    v = vault()
    page = make_page(owner, "Bin", {"folders": [v.folder]})["id"]
    v.edit(page, insert(page, "body"))
    v.sync()
    with connect_pages_db(v.ws) as conn:
        trash_page(v.ws, conn, page, actor=v.user)
    _kill_once(monkeypatch, vault_disk, "move")
    with pytest.raises(Killed):
        v.sync()
    monkeypatch.undo()
    _quick_again(monkeypatch)
    v.sync()
    (tomb,) = vault_store.tombstones(v.link["id"])
    assert not (v.root / "Bin.md").exists() and (v.root / tomb["path"]).read_bytes() == b"body\n"
    assert not vault_store.pending(v.link["id"])
    v.quiet()


def test_a_kill_after_shape_ops_only_leaves_a_page_to_pull(vault, monkeypatch):
    """Shape ops go before the rename: a kill between leaves the page split and
    the file to write."""
    v = vault()
    v.write("Sh.md", "start\n")
    v.sync()
    page = v.page_of("Sh.md")
    v.edit(page, set_(v.find(page, "start"), "one\n\ntwo"))
    _kill_once(monkeypatch, vault_disk, "replace_note")
    with pytest.raises(Killed):
        v.sync()
    assert v.texts(page) == ["one", "two"]
    monkeypatch.undo()
    _quick_again(monkeypatch)
    v.sync()
    assert v.text("Sh.md") == "one\n\ntwo\n" and v.texts(page) == ["one", "two"]
    v.quiet()


def _quick_again(monkeypatch):
    monkeypatch.setenv("GAMMA_FOLDER_LINKS", "1")
    monkeypatch.setattr(vault_sync, "SETTLE_NOTE_S", 0)
    monkeypatch.setattr(gamma_sync, "SETTLE_S", 0)
    monkeypatch.setattr(vault_store, "MISSING_S", 0)
    monkeypatch.setattr(pdf_meta, "schedule", lambda ws, doc_id: None)
    monkeypatch.setattr(storage, "linked_moved", [])
    monkeypatch.setattr(storage, "LINKED_STAT_S", 0)


# --- other counter-examples -----------------------------------------------------------------------------

def test_two_empty_untitled_notes_made_in_a_row_are_two_pages(vault):
    """Random ids, minted once; no id comes from a file's
    path or bytes."""
    v = vault()
    v.write("Untitled.md", "")
    v.sync()
    os.rename(v.root / "Untitled.md", v.root / "Meeting.md")
    v.write("Untitled.md", "")
    v.sync()
    assert v.page_count() == 2 and v.page_of("Untitled.md") != v.page_of("Meeting.md")


def test_an_edit_in_gamma_is_written_though_a_push_moved_the_seq(vault):
    """Base seq 10, Gamma edits b2, the file changes b1; the
    push's seq is not the base's unless the page says what the file says."""
    v = vault()
    v.write("Seq.md", "b1\n\nb2\n")
    v.sync()
    page = v.page_of("Seq.md")
    v.edit(page, set_(v.find(page, "b2"), "b2 from gamma"))
    v.write("Seq.md", "b1 from disk\n\nb2\n")
    v.sync()
    assert v.text("Seq.md") == "b1 from disk\n\nb2 from gamma\n"
    v.quiet()


def test_one_line_added_at_the_top_of_a_long_page_is_one_insert(vault):
    """Keys are kept, not regenerated into a move per block."""
    v = vault()
    v.write("Long.md", "\n\n".join(f"line {n}" for n in range(3000)) + "\n")
    v.sync()
    page = v.page_of("Long.md")
    count = v.ops_count(page)
    v.write("Long.md", "new first line\n\n" + v.text("Long.md"))
    v.sync()
    with connect_pages_db(v.ws) as conn:
        rows = conn.execute("SELECT ops FROM page_ops WHERE page_id = ? ORDER BY seq", (page,)).fetchall()
    assert len(rows) == count + 1 and [op["op"] for op in json.loads(rows[-1][0])] == ["insert"]
    v.quiet()


def test_a_refused_file_is_that_files_problem_and_a_block_held_elsewhere_gets_a_fresh_id(vault, owner):
    """A block another page holds comes back under a fresh
    id; a block over the op path's limit is the file's problem and the other
    files go on."""
    v = vault()
    v.write("Held.md", "a\n\nb\n")
    v.write("Fine.md", "fine\n")
    v.sync()
    page = v.page_of("Held.md")
    b = v.find(page, "b")
    other = make_page(owner, "Elsewhere")["id"]
    with connect_pages_db(v.ws) as conn:
        write_lock(conn)
        move_across_pages(v.ws, conn, b, other, "a0", page, other, actor=v.user)
    v.write("Held.md", "a\n\nb, edited\n")
    v.write("Big.md", "x" * 200_001 + "\n")
    v.write("Fine.md", "fine, edited\n")
    status = v.sync(ok=False)
    assert v.texts(page) == ["a", "b, edited"] and v.find(page, "b, edited") != b
    assert v.texts(v.page_of("Fine.md")) == ["fine, edited"]
    assert [k["path"] for k in status["kept"]] == ["Big.md"] and status["last_error"] == ""


def test_an_account_that_may_no_longer_edit_stops_the_vault_not_a_file(vault, monkeypatch):
    v = vault()
    v.write("A.md", "a\n")
    v.sync()
    monkeypatch.setattr(vault_sync.workspaces, "role_of", lambda ws, user: "viewer")
    v.write("A.md", "a, edited\n")
    status = v.sync(ok=False)
    assert "may no longer edit" in status["last_error"] and v.texts(v.page_of("A.md")) == ["a"]
    monkeypatch.setattr(vault_sync.workspaces, "role_of", lambda ws, user: None)
    status = vault_sync.sync(v.link, remote=v.remote, direction="pull").status
    assert "no access" in status["last_error"]


def test_a_vault_with_no_marker_stops_and_one_whose_disk_is_gone_is_offline(vault, monkeypatch):
    """Nothing reads as removed, and a round never
    makes the root."""
    v = vault()
    v.write("N.md", "n\n")
    v.sync()
    page = v.page_of("N.md")
    os.remove(v.root / vault_disk.MARKER)
    status = v.sync(ok=False)
    assert status["stopped"] and v.state(page) == "live"
    vault_disk.make_marker(v.root)
    status = v.sync(ok=False)
    assert "another vault" in status["stopped"]

    w = vault("w")
    w.write("M.md", "m\n")
    w.sync()
    monkeypatch.setattr(vault_disk, "walk", lambda root, rules=None: (_ for _ in ()).throw(vault_disk.Offline("gone")))
    for _ in range(2):
        status = w.sync(ok=False)
        assert status["offline"] and w.state(w.page_of("M.md")) == "live"
    monkeypatch.undo()
    _quick_again(monkeypatch)
    shutil.rmtree(w.root)
    status = w.sync(ok=False)
    assert status["offline"] and not w.root.exists()


def test_a_cloud_placeholder_is_never_read_or_removed(vault, monkeypatch):
    v = vault()
    v.write("Cloud.md", "in the cloud\n")
    v.sync()
    page = v.page_of("Cloud.md")
    real = vault_disk.scan

    def placeholders(root, vault_id, rules=None):
        found = real(root, vault_id, rules)
        for f in found.files.values():
            f.placeholder = True
        return found
    monkeypatch.setattr(vault_disk, "scan", placeholders)
    os.remove(v.root / "Cloud.md")
    v.write("Cloud.md", "changed, but only a placeholder\n")
    v.edit(page, set_(v.find(page, "in the cloud"), "edited in Gamma"))
    v.sync()
    v.sync()
    assert v.state(page) == "live" and v.texts(page) == ["edited in Gamma"]
    assert v.text("Cloud.md") == "changed, but only a placeholder\n"


def test_a_vault_whose_folder_was_deleted_in_gamma_stops_and_trashes_nothing(vault, owner):
    v = vault()
    v.write("Kept.md", "kept\n")
    v.sync()
    page = v.page_of("Kept.md")
    v.owner.delete(f"/api/folders/{v.folder}")
    status = v.sync(ok=False)
    assert "no longer in Gamma" in status["stopped"] and (v.root / "Kept.md").is_file() and v.state(page) == "live"


def test_vaults_db_is_never_written_inside_the_pages_write_lock(vault, monkeypatch, owner):
    """Every vaults.db call of a round finds the workspace's
    pages.db free to lock: the store's, the round's own (a relink) and the
    linked files' (a PDF linked, replaced, moved to the trash and back)."""
    v = vault()
    pages_db = config.WORKSPACES_DIR / v.ws / "pages.db"
    real = vault_store.connect_vaults_db
    calls = []

    def checked():
        calls.append(1)
        with closing(sqlite3.connect(pages_db, timeout=0)) as probe:
            try:
                probe.execute("BEGIN IMMEDIATE")
                probe.rollback()
            except sqlite3.OperationalError as e:
                raise AssertionError("vaults.db written inside the pages lock") from e
        return real()
    for module in (vault_store, vault_sync, linked_files):
        monkeypatch.setattr(module, "connect_vaults_db", checked)
    v.write("L.md", "a\n\nb\n")
    v.write("L.pdf", _pdf("l"), age=10)
    v.sync()
    page = v.page_of("L.md")
    v.edit(page, set_(v.find(page, "a"), "a!"))
    v.write("L.md", "a\n\nb!\n")
    v.write("L.pdf", _pdf("l, saved again"), age=5)
    made = make_page(owner, "Lock", {"folders": [v.folder]})["id"]
    v.edit(made, insert(made, "made"))
    v.sync()
    assert v.text("L.md") == "a!\n\nb!\n" and v.text("Lock.md") == "made\n"
    with connect_pages_db(v.ws) as conn:
        trash_page(v.ws, conn, page, actor=v.user)
    v.sync()
    with connect_pages_db(v.ws) as conn:
        restore_page(v.ws, conn, page, actor=v.user)
    v.sync()
    assert (v.root / "L.pdf").is_file() and len(calls) > 20


# --- counter-examples, a first set -----------------------------------------------------------------

def test_a_pending_that_cannot_be_settled_waits_and_the_vault_goes_on(vault):
    """A rename journalled onto a path a row held
    raised ``IntegrityError`` out of the journal's recovery, every round."""
    v = vault()
    v.write("A.md", "a\n")
    v.write("B.md", "b\n")
    v.sync()
    vault_store.put_pending(v.link["id"], "B.md", "notes", {"op": "move", "token": "t", "path": "A.md", "hash": "x",
                                                         "text": "b\n", "nodes": [], "page_id": v.page_of("B.md")})
    os.remove(v.root / "B.md")
    assert v.sync(ok=False)["last_error"] == "" and v.texts(v.page_of("A.md")) == ["a"]


def test_a_save_in_the_moment_between_pulls_check_and_its_rename_is_kept(vault, monkeypatch):
    """The hard link's bytes were thrown away when pull had written that path
    in the last five minutes, which the hot loop nearly always has."""
    v = vault()
    v.write("W.md", "first\n")
    v.sync()
    page = v.page_of("W.md")
    v.edit(page, insert(page, "gamma one"))
    v.sync()                                   # the history keeps "first"
    v.edit(page, insert(page, "gamma two"))
    real, saved = vault_disk.replace_note, b"first\n\ngamma one\n\nthe person typed this in the window\n"

    def racing(root, rel, data, ready=None, **kw):
        (root / rel).write_bytes(saved)        # after pull's check, before its rename
        return real(root, rel, data, ready, **kw)
    monkeypatch.setattr(vault_disk, "replace_note", racing)
    v.sync()
    assert [h["data"] for h in vault_store.history(v.link["id"], "W.md")] == [saved, b"first\n"]


def test_a_short_note_renamed_and_edited_between_rounds_keeps_its_page(vault):
    """The same file id, and a text 0.57 alike: a new page, the old one with
    its comments and chats to Recently deleted, though the file never went."""
    v = vault()
    v.write("Idea.md", "buy more coffee\n")
    v.write("keep.md", "other\n")
    v.sync()
    page = v.page_of("Idea.md")
    os.rename(v.root / "Idea.md", v.root / "Shopping.md")
    v.write("Shopping.md", "buy more tea today\n")          # in place: the file id stays
    v.sync()
    v.sync()
    assert v.page_of("Shopping.md") == page and v.state(page) == "live" and v.page_count() == 2
    assert v.texts(page) == ["buy more tea today"] and v.info(page).title == "Shopping"
    v.quiet()


@pytest.mark.parametrize("save", ["after the round", "across the round"])
def test_a_note_saved_again_after_gamma_trashed_its_page_brings_that_page_back(vault, save):
    """The file is the authority: the note saved where it lived (by the editor
    that had it open, or by a safe save a round fell into) is its page's, not
    a second page, and no restore writes ``N 2.md`` later."""
    v = vault()
    v.write("N.md", "first line\n\nsecond line\n")
    v.write("keep.md", "other\n")
    v.sync()
    page = v.page_of("N.md")
    with connect_pages_db(v.ws) as conn:
        trash_page(v.ws, conn, page, actor=v.user)
    again = "first line\n\nsecond line\n\nthird line\n"
    if save == "after the round":
        v.sync()                                            # pull takes the note to the vault's trash
        assert not (v.root / "N.md").exists()
        v.write("N.md", again)
    else:
        temp = v.write("N.md.tmp~", again)
        os.remove(v.root / "N.md")
        v.sync()                                            # the round falls in the editor's gap
        os.rename(temp, v.root / "N.md")
    v.sync()
    assert v.state(page) == "live" and v.page_of("N.md") == page and v.page_count() == 2
    assert v.texts(page) == ["first line", "second line", "third line"]
    assert sorted(p for p in os.listdir(v.root) if p.endswith(".md")) == ["N.md", "keep.md"]
    v.quiet()


def test_a_new_note_with_a_trashed_notes_bytes_is_a_new_page(vault):
    """A template's copy took the trashed page of another day back, under its
    old title; the same bytes put back where they lived still do."""
    v = vault()
    template = "## Tasks\n\n- [ ] \n\n## Log\n"
    v.write("2026-11-01.md", template, age=10)
    v.write("other.md", "other\n", age=10)
    v.sync()
    old = v.page_of("2026-11-01.md")
    os.remove(v.root / "2026-11-01.md")
    v.sync()
    v.sync()
    assert v.state(old) == "trashed"
    v.write("2026-11-07.md", template, age=10)
    v.sync()
    new = v.page_of("2026-11-07.md")
    assert new != old and v.info(new).title == "2026-11-07" and v.state(old) == "trashed"
    v.write("2026-11-01.md", template, age=10)
    v.sync()
    assert v.page_of("2026-11-01.md") == old and v.state(old) == "live"
    v.quiet()


@pytest.mark.parametrize("then", ["the file deleted", "a page round"])
def test_a_kill_inside_a_swap_of_rows_leaves_no_row_parked(vault, monkeypatch, then):
    """A kill between the swap's two passes left a row at ``\\x00moving/0`` that
    failed every round for good, and put an ``lstat`` error on the moved
    file's row in a page round."""
    v = vault()
    v.write("a.md", "alpha text\n")
    v.write("keep.md", "other\n")
    v.sync()
    page = v.page_of("a.md")
    os.rename(v.root / "a.md", v.root / "b.md")
    _kill_after(monkeypatch, vault_store, "_repath")
    with pytest.raises(Killed):
        v.sync()
    monkeypatch.undo()
    _quick_again(monkeypatch)
    assert sorted(v.rows()) == ["a.md", "keep.md"]
    if then == "the file deleted":
        os.remove(v.root / "b.md")
        for _ in range(3):
            v.sync()
        assert v.state(page) == "trashed" and sorted(v.rows()) == ["keep.md"]
    else:
        assert v.sync_page(page)["kept"] == []
        v.sync()
        assert v.page_of("b.md") == page and not v.rows()["b.md"]["problem"]
    v.quiet()


def _safe_save(path, text):
    """An editor's atomic save: a file beside the note renamed over it, which
    gives the note a new file id (JetBrains' safe write, vim, Sublime)."""
    temp = path.with_name(path.name + ".tmp~")
    temp.write_bytes(text.encode("utf-8"))
    os.replace(temp, path)


@pytest.mark.parametrize("save", ["in place", "a safe save"])
@pytest.mark.parametrize("made", [False, True])
def test_a_kill_after_a_pull_write_then_a_persons_edit_doubles_nothing(vault, monkeypatch, owner, made, save):
    """The write landed, the base was not saved, the person edited the file
    before the restart: by the file id the journal named, or by its text when
    a safe save gave the file another, the write is known to have landed and
    the edit is pushed as one, never Gamma's line twice nor a second page and
    ``X 2.md``."""
    v = vault()
    if made:
        page = make_page(owner, "Made", {"folders": [v.folder]})["id"]
        v.edit(page, insert(page, "body"))
        rel, point = "Made.md", "write_new"
    else:
        v.write("K.md", "- a\n- b\n")
        v.sync()
        page, rel, point = v.page_of("K.md"), "K.md", "replace_note"
        v.edit(page, insert(page, "- m"))
    _kill_after(monkeypatch, vault_disk, point)
    with pytest.raises(Killed):
        v.sync()
    monkeypatch.undo()
    _quick_again(monkeypatch)
    written = v.text(rel)
    edited = written.replace("- a", "- A") if not made else written + "\nmore from the person\n"
    if save == "in place":
        with open(v.root / rel, "r+b") as f:               # the file id stays
            f.write(edited.encode())
            f.truncate()
    else:
        _safe_save(v.root / rel, edited)
    v.sync()
    if made:
        assert v.page_count() == 1 and v.page_of(rel) == page and v.texts(page) == ["body", "more from the person"]
        assert sorted(os.listdir(v.root)) == [".gamma", "Made.md"]
    else:
        assert v.texts(page) == ["- A", "- b", "- m"] and v.text(rel) == "- A\n- b\n- m\n"
    v.quiet()


def test_the_page_round_never_reads_or_writes_over_a_cloud_placeholder(vault, monkeypatch):
    """The whole round checked by its scan; the page round has none, and read
    the placeholder (a recall) and wrote Gamma's text over it."""
    v = vault()
    v.write("c.md", "cloud text\n")
    v.sync()
    page = v.page_of("c.md")
    monkeypatch.setattr(vault_disk, "_placeholder", lambda st: True)
    v.edit(page, set_(v.find(page, "cloud text"), "gamma text"))
    read, real = [], vault_disk.read
    monkeypatch.setattr(vault_disk, "read", lambda root, rel: read.append(rel) or real(root, rel))
    v.sync_page(page)
    assert v.text("c.md") == "cloud text\n" and read == []


def test_a_pdf_attached_in_gamma_to_a_page_of_notes_is_copied_beside_them(vault, owner):
    v = vault()
    v.write("Reading.md", "notes before the paper\n", age=10)
    v.sync()
    page = v.page_of("Reading.md")
    up = owner.post("/api/uploads", files={"file": ("p.pdf", _pdf("attached later"), "application/pdf")}).json()
    owner.put(f"/api/blocks/{page}", json={"properties": {"folders": [v.folder], **doc_props(up["doc_id"])}}).raise_for_status()
    v.sync()
    assert sorted(os.listdir(v.root)) == [".gamma", "Reading.md", "Reading.pdf"]
    assert v.rows()["Reading.pdf"]["page_id"] == page and (v.root / "Reading.pdf").read_bytes()[:4] == b"%PDF"
    v.quiet()


def test_a_page_or_folder_whose_name_the_scan_leaves_out_is_written_where_it_sees_it(vault, owner):
    """``~$Plan.md`` and ``node_modules/`` were written, then never read: an
    edit on disk never came back, and the vault was held in the end."""
    v = vault()
    v.write("seed.md", "seed\n")
    v.sync()
    plan = make_page(owner, "~$Plan", {"folders": [v.folder]})["id"]
    v.edit(plan, insert(plan, "planning text"))
    folder = new_block_id()
    commit_ops(v.ws, "folders", [{"op": "insert", "id": folder, "parent": v.folder, "content": "node_modules"}], actor=v.user)
    dep = make_page(owner, "Dep", {"folders": [folder]})["id"]
    v.edit(dep, insert(dep, "dep text"))
    v.sync()
    names = {r["page_id"]: p for p, r in v.rows().items()}
    assert names[plan] == "_~$Plan.md" and names[dep] == "_node_modules/Dep.md"
    assert {names[plan], names[dep]} <= set(vault_disk.scan(v.root, v.link["state"]["vault"]).files)
    v.write(names[plan], "planning text\n\nmore typed on disk\n")
    v.sync()
    assert v.texts(plan) == ["planning text", "more typed on disk"]
    v.quiet()


def _work(v, owner):
    """The vault's folder marked as a work folder's (conftest
    ``mark_work_folder``): its rounds are a work folder's."""
    mark_work_folder(owner, v.folder)
    return v


def _seen(path):
    st = os.stat(path)
    return path.read_bytes(), st.st_mtime_ns, st.st_ino


def test_a_file_its_gammaignore_leaves_out_goes_to_recently_deleted_where_it_is(vault, owner):
    """A work folder's paper unticked since, its PDF and the notes pull wrote
    beside it: its page in Recently deleted, its rows in-place tombstones, the
    files' bytes, time and id untouched; ticked again, the page is back with
    its notes, whatever the PDF's bytes now."""
    v = _work(vault(), owner)
    paper = v.write("Paper.pdf", _pdf("the paper"), age=10)
    v.write("Note.md", "a note\n")
    v.sync()
    page = v.page_of("Paper.pdf")
    v.edit(page, insert(page, "a thought on the paper"))
    v.sync()
    before, notes = _seen(paper), _seen(v.root / "Paper.md")
    v.write(".gammaignore", "/Paper.*\n")
    status = v.sync()
    assert status["counts"]["removed"] == 2 and v.state(page) == "trashed"
    assert _seen(paper) == before and _seen(v.root / "Paper.md") == notes
    tombs = vault_store.tombstones(v.link["id"])
    assert sorted(t["was"] for t in tombs) == ["Paper.md", "Paper.pdf"]
    assert all(vault_store.in_place(t) and not t["pending"] for t in tombs)
    assert not (v.root / vault_disk.TRASH).exists() and v.state(v.page_of("Note.md")) == "live"
    v.quiet()

    v.write("Paper.pdf", _pdf("the paper, built again"), age=5)
    v.write(".gammaignore", "/Other.pdf\n")
    v.sync()
    assert v.state(page) == "live" and v.page_of("Paper.pdf") == page and vault_store.tombstones(v.link["id"]) == []
    assert "a thought on the paper" in v.texts(page)
    assert v.blocks(page)[page]["props"]["doc"]["hash"] == v.rows()["Paper.pdf"]["hash"]
    v.quiet()


def test_unticking_half_a_work_folder_is_held_by_the_guard_and_goes_on_when_confirmed(vault, owner):
    v = _work(vault(), owner)
    for n in range(12):
        v.write(f"n{n}.md", f"note {n}\n")
    v.sync()
    v.write(".gammaignore", "/n[0-6].md\n")
    status = v.sync(ok=False)
    assert status["held"] == {"removals": 7, "total": 12} and all(v.state(v.page_of(f"n{n}.md")) == "live"
                                                                  for n in range(12))
    status = v.sync(confirm=True)
    assert status["counts"]["removed"] == 7 and len(v.rows()) == 5
    assert all((v.root / f"n{n}.md").exists() for n in range(12))


def test_a_kill_between_the_trash_and_the_bury_never_moves_the_file(vault, owner, monkeypatch):
    """The page goes first: a round that dies then leaves a live row the next
    deselects again; one whose page the mirror brings back sends it back,
    the file still where it is."""
    v = _work(vault(), owner)
    paper = v.write("Paper.pdf", _pdf("kill"), age=10)
    v.sync()
    page = v.page_of("Paper.pdf")
    before = _seen(paper)
    v.write(".gammaignore", "/Paper.pdf\n")

    def killed(*_a):
        raise Killed()
    with monkeypatch.context() as m:
        m.setattr(vault_store, "bury_in_place", killed)
        with pytest.raises(Killed):
            v.sync(ok=False)
    assert v.state(page) == "trashed" and "Paper.pdf" in v.rows() and _seen(paper) == before
    v.sync()
    assert v.state(page) == "trashed" and vault_store.in_place(vault_store.tombstones(v.link["id"])[0])
    with connect_pages_db(v.ws) as conn:
        restore_page(v.ws, conn, page, actor=v.user, client="sync")
    v.sync()
    assert v.state(page) == "trashed" and _seen(paper) == before and not (v.root / vault_disk.TRASH).exists()


@pytest.mark.parametrize("work", [False, True])
def test_a_file_a_link_above_leaves_out_is_never_a_removal(vault, owner, tmp_path, work):
    """The scan does not follow the link, and the file below it is there: no
    removal, and the guard never holds a vault where nothing went."""
    v = vault(f"link-{work}")
    if work:
        _work(v, owner)
    for n in range(10):
        v.write(f"n{n}.md", f"note {n}\n")
    v.write("Sub/Plan.md", "plan\n")
    v.sync()
    page = v.page_of("Sub/Plan.md")
    outside = tmp_path / f"outside-{work}"
    shutil.move(str(v.root / "Sub"), str(outside))
    if sys.platform == "win32":
        subprocess.run(["cmd", "/c", "mklink", "/J", str(v.root / "Sub"), str(outside)], check=True, capture_output=True)
    else:
        os.symlink(outside, v.root / "Sub", target_is_directory=True)
    try:
        for _ in range(6):
            status = v.sync(ok=False)
            assert not status.get("held") and status["counts"]["removed"] == 0
        assert v.state(page) == "live" and not v.rows()["Sub/Plan.md"]["missing_since"]
    finally:
        os.rmdir(v.root / "Sub") if sys.platform == "win32" else os.unlink(v.root / "Sub")


@pytest.mark.skipif(sys.platform != "win32", reason="a junction is Windows'")
@pytest.mark.parametrize("whole", [True, False])
def test_nothing_below_a_junction_is_read_or_written(vault, tmp_path, whole):
    """The scan refused the junction, but pull and the page round read and
    wrote through it: Gamma's text over a file outside the vault, the outside
    file's text into the page."""
    v = vault()
    v.write("Sub/x.md", "inside text\n")
    v.sync()
    page = v.page_of("Sub/x.md")
    outside = tmp_path / "outside"
    shutil.move(str(v.root / "Sub"), str(outside))
    (outside / "x.md").write_bytes(b"SECRET outside text\n")
    subprocess.run(["cmd", "/c", "mklink", "/J", str(v.root / "Sub"), str(outside)], check=True, capture_output=True)
    try:
        v.edit(page, set_(v.find(page, "inside text"), "gamma edit"))
        for status in [v.sync(ok=False), v.sync(ok=False)] if whole else [v.sync_page(page)]:
            assert status["last_error"] == ""
        assert (outside / "x.md").read_bytes() == b"SECRET outside text\n" and v.texts(page) == ["gamma edit"]
        assert v.state(page) == "live" and "below a link" in v.rows()["Sub/x.md"]["problem"]
    finally:
        os.rmdir(v.root / "Sub")


@pytest.mark.skipif(sys.platform != "win32", reason="a junction is Windows'")
def test_a_junction_where_a_folders_directory_was_stays_when_the_folder_goes(vault, owner, tmp_path):
    """A recorded directory the person replaced by a junction: when its
    folder went in Gamma, the round removed the directory as an empty one of
    its own, and a junction goes whatever it points to."""
    v = vault()
    two = _subfolder(owner, v, "Two")
    page = make_page(owner, "Inside", {"folders": [two]})["id"]
    v.edit(page, insert(page, "inside"))
    v.sync()
    outside = tmp_path / "outside"
    shutil.move(str(v.root / "Two"), str(outside))
    subprocess.run(["cmd", "/c", "mklink", "/J", str(v.root / "Two"), str(outside)], check=True, capture_output=True)
    try:
        v.owner.delete(f"/api/folders/{two}")
        v.sync(ok=False)
        assert os.path.lexists(v.root / "Two") and (outside / "Inside.md").read_bytes() == b"inside\n"
    finally:
        if os.path.lexists(v.root / "Two"):
            os.rmdir(v.root / "Two")


def test_a_disk_unplugged_between_the_scan_and_pull_keeps_its_rows(vault, monkeypatch, tmp_path):
    """Pull read a file not there as gone and dropped the rows of a page that
    left the vault: the file then came back as a second page."""
    v = vault()
    v.write("keep.md", "kept\n")
    v.write("gone.md", "this page is moved out in gamma\n")
    v.sync()
    page = v.page_of("gone.md")
    v.edit(page, {"op": "set", "id": page, "props": {"folders": None}})
    real = vault_sync.Round._pull

    def unplug(self):
        os.rename(v.root, tmp_path / "away")
        return real(self)
    monkeypatch.setattr(vault_sync.Round, "_pull", unplug)
    status = v.sync(ok=False)
    assert status.get("offline") and "gone.md" in v.rows()
    monkeypatch.setattr(vault_sync.Round, "_pull", real)
    os.rename(tmp_path / "away", v.root)
    v.sync()
    v.sync()
    assert not (v.root / "gone.md").exists() and v.page_count() == 1 and v.state(page) == "live"
    v.quiet()


def test_a_name_a_missing_files_row_holds_is_taken_by_no_other(vault, owner):
    """A.md deleted on disk keeps its row until the second scan: page B renamed
    A, and a new page A, took ``A.md`` on the disk's word; promote hit the
    row's path (a UNIQUE failure every round after), and the new page took
    A's row, so the deleted note came back."""
    v = vault()
    v.write("A.md", "a text\n")
    v.write("B.md", "b text\n")
    v.sync()
    a, b = v.page_of("A.md"), v.page_of("B.md")
    os.remove(v.root / "A.md")
    v.edit(b, {"op": "set", "id": b, "content": "A"})
    c = make_page(owner, "A", {"folders": [v.folder]})["id"]
    v.edit(c, insert(c, "c text"))
    for _ in range(3):
        v.sync()
    assert v.state(a) == "trashed" and not vault_store.pending(v.link["id"])
    files = {r["page_id"]: p for p, r in v.rows().items()}
    assert set(files) == {b, c} and "A.md" not in files.values()
    assert v.text(files[b]) == "b text\n" and v.text(files[c]) == "c text\n"
    v.quiet()


def test_a_note_saved_in_another_encoding_is_never_written_over(vault):
    """Its row took the new hash with the old text, so the next pull found the
    file to be its base and wrote Gamma's text over the person's save."""
    v = vault()
    v.write("L.md", "café notes\n")
    v.sync()
    page = v.page_of("L.md")
    latin = "café notes, saved by another editor\n".encode("latin-1")
    v.write("L.md", latin)
    status = v.sync(ok=False)
    assert "not UTF-8" in status["kept"][0]["why"]
    v.edit(page, set_(v.find(page, "café notes"), "edited in Gamma"))
    v.sync_page(page)
    v.sync(ok=False)
    assert (v.root / "L.md").read_bytes() == latin and "not UTF-8" in v.rows()["L.md"]["problem"]


@pytest.mark.parametrize("push", [folder_links.PUSH_OFF, folder_links.PUSH_PDFS])
def test_a_quiet_round_of_a_vault_that_pushes_no_notes_reads_no_note(vault, owner, push):
    """A note pull wrote stayed racy for ever there: every round read and
    hashed every one."""
    v = vault(push=push)
    for n in range(3):
        page = make_page(owner, f"P{n}", {"folders": [v.folder]})["id"]
        v.edit(page, insert(page, f"text {n}"))
    v.sync()
    then = time.time() - 10
    for rel in v.rows():
        os.utime(v.root / rel, (then, then))
    v.sync()                                    # read once, the same bytes: their stat taken
    assert v.sync()["counts"]["read"] == 0


def test_a_removal_waits_for_two_scans_ten_seconds_apart(vault, monkeypatch):
    """``MISSING_S`` is 0 in the other round tests."""
    monkeypatch.setattr(vault_store, "MISSING_S", 10)
    v = vault()
    v.write("R.md", "r\n")
    v.write("keep.md", "k\n")
    v.sync()
    page = v.page_of("R.md")
    os.remove(v.root / "R.md")
    v.sync()
    v.sync()
    assert v.state(page) == "live" and v.rows()["R.md"]["missing_since"]
    vault_store.put(v.link["id"], "R.md", missing_since=time.time() - 11)
    v.sync()
    assert v.state(page) == "trashed"


def test_a_picture_a_note_links_is_read_where_it_is(vault):
    """Pictures were only counted by the scan, so a
    note's ``![[pic.png]]`` stayed as typed, a picture Gamma could not show."""
    v = vault()
    png = (b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89"
           b"\x00\x00\x00\rIDATx\x9cc\xf8\x0f\x00\x00\x01\x01\x00\x05\x18\xd8N\x00\x00\x00\x00IEND\xaeB`\x82")
    v.write("img/pic.png", png)
    v.write("P.md", "look: ![[pic.png]]\n")
    v.sync()
    (text,) = v.texts(v.page_of("P.md"))
    name = f"{storage.content_digest(png)}.png"
    assert text == f"look: ![](/api/uploads/{name})" and storage.open_path(v.ws, name) == v.root / "img" / "pic.png"
    assert v.text("P.md") == "look: ![[pic.png]]\n"
    v.quiet()


PNG = (b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89"
       b"\x00\x00\x00\rIDATx\x9cc\xf8\x0f\x00\x00\x01\x01\x00\x05\x18\xd8N\x00\x00\x00\x00IEND\xaeB`\x82")


def _linked_at(v, name) -> list:
    with connect_vaults_db() as conn:
        return [r[0] for r in conn.execute("SELECT path FROM linked_files WHERE ws = ? AND name = ?", (v.ws, name))]


def test_a_picture_pasted_in_gamma_goes_to_the_attachment_folder_and_is_read_there_from_then_on(vault):
    """Under its upload's name, and a linked file from then on; the next
    round writes nothing."""
    v = vault()
    v.write("P.md", "text\n")
    v.sync()
    page = v.page_of("P.md")
    name = f"{storage.content_digest(PNG)}.png"
    storage.put_upload(v.ws, name, PNG)
    v.edit(page, insert(page, f"look ![](/api/uploads/{name})"))
    v.sync()
    assert (v.root / "attachments" / name).read_bytes() == PNG
    assert v.text("P.md") == f"text\n\nlook ![[{name}]]\n"
    assert _linked_at(v, name) == [str(v.root / "attachments" / name)]
    assert v.rows()[f"attachments/{name}"]["kind"] == "picture"
    v.quiet()


def test_a_page_round_reads_a_picture_put_in_the_vault_since_the_last_scan(vault):
    """A page's round has no scan, so a picture its note now links stayed as
    typed until that block changed."""
    v = vault()
    v.write("P.md", "start\n")
    v.sync()
    page = v.page_of("P.md")
    v.write("img/new.png", PNG)
    v.write("P.md", "start ![[new.png]]\n")
    v.sync_page(page)
    name = f"{storage.content_digest(PNG)}.png"
    assert v.texts(page) == [f"start ![](/api/uploads/{name})"]
    assert str(v.root / "img" / "new.png") in _linked_at(v, name)
    v.quiet()


def test_a_picture_the_vault_reads_in_place_is_linked_where_it_is_from_another_note(vault, owner):
    """A block of Gamma's that shows a picture the vault reads in place (one
    moved or copied from another note) links that file, and no second copy
    goes to the attachment folder."""
    v = vault()
    v.write("img/pic.png", PNG)
    v.write("P.md", "look: ![[pic.png]]\n")
    v.sync()
    name = f"{storage.content_digest(PNG)}.png"
    other = make_page(owner, "Other", {"folders": [v.folder]})["id"]
    v.edit(other, insert(other, f"the same ![](/api/uploads/{name})"))
    v.sync()
    assert v.text("Other.md") == "the same ![[pic.png]]\n"
    assert not (v.root / "attachments").exists()
    v.quiet()


def test_a_link_to_a_page_outside_the_vault_is_its_gamma_address(vault, owner, monkeypatch):
    """``https://host/?ws=<id>&page=<id>``, read back as the mention."""
    monkeypatch.setenv("GAMMA_PUBLIC_URL", "https://gamma.example")
    v = vault()
    v.write("Here.md", "here\n")
    v.sync()
    page = v.page_of("Here.md")
    elsewhere = make_page(owner, "Elsewhere page")["id"]
    v.edit(page, set_(v.find(page, "here"), f"see [[{elsewhere}]]"))
    v.sync()
    assert v.text("Here.md") == f"see [Elsewhere page](https://gamma.example/?ws={v.ws}&page={elsewhere})\n"
    v.write("Here.md", f"see [Elsewhere page](https://gamma.example/?ws={v.ws}&page={elsewhere}) again\n")
    v.sync()
    assert v.texts(page) == [f"see [[{elsewhere}]] again"]


# --- counter-examples, a second set ----------------------------------------------------------------

def _folder(v, name):
    return next(f for f, (_parent, n) in v.layout().folders.items() if n == name)


def _mark(quote):
    rect = {"x1": 1, "y1": 2, "x2": 3, "y2": 4}
    return {"pdf_position": {"pageNumber": 1, "boundingRect": rect, "rects": [rect], "width": 10, "height": 10},
            "quote": quote}


def _paper(v):
    """A paper and its notes, synced: their page."""
    v.write("Paper.pdf", _pdf("paper"), age=10)
    v.write("Paper.md", "my notes\n")
    v.write("other.md", "other\n")
    v.sync()
    return v.page_of("Paper.pdf")


def test_a_directory_moved_on_disk_into_one_gamma_moved_under_it_goes_where_the_disk_has_it(vault):
    """Gamma moved folder B under A while the disk moved A into B: the folder
    move was a loop, refused, and every round of the vault failed on it. The
    disk's places win, as the file's do for blocks, and the rest goes on."""
    v = vault()
    v.write("A/a.md", "alpha\n")
    v.write("B/b.md", "beta\n")
    v.sync()
    a, b = _folder(v, "A"), _folder(v, "B")
    commit_ops(v.ws, "folders", [{"op": "move", "id": b, "parent": a}], actor=v.user)
    os.rename(v.root / "A", v.root / "B" / "A")
    v.write("new.md", "written meanwhile\n")
    v.sync()
    folders = v.layout().folders
    assert folders[b][0] == v.folder and folders[a][0] == b
    assert sorted(v.rows()) == ["B/A/a.md", "B/b.md", "new.md"] and v.page_count() == 3
    v.quiet()


def test_notes_typed_on_both_sides_into_an_empty_highlight_clash_and_the_file_wins(vault):
    """Two insertions at one place on an empty base were glued into one word
    (``great pointcheck later``), called clean and written to page and file."""
    v = vault()
    page = _paper(v)
    hl = new_block_id()
    v.edit(page, insert(page, "", _mark("the quoted text"), hl))
    v.sync()
    written = v.text("Paper.md")
    v.edit(page, set_(hl, "great point"))
    v.write("Paper.md", written.replace("> the quoted text\n", "> the quoted text\n>\n> check later\n"))
    assert v.sync()["counts"]["conflicts"] == 1
    assert v.blocks(page)[hl]["content"] == "check later" and "check later" in v.text("Paper.md")
    assert [(row[0], row[2], row[3]) for row in v.conflicts(page)] == [(hl, "great point", "check later")]
    v.quiet()


def test_a_save_in_pulls_write_window_survives_a_kill_before_the_history(vault, monkeypatch):
    """The person saved after pull's temp was on the disk and before the hard
    link; the process died before the history had those bytes, the link
    already gone: the save was in no file, page or history."""
    v = vault()
    v.write("n.md", "first line\n")
    v.write("other.md", "other\n")
    v.sync()
    page = v.page_of("n.md")
    v.edit(page, set_(v.find(page, "first line"), "first line from gamma"))
    real, saved = vault_disk.replace_note, b"first line\n\nthe person's own paragraph\n"

    def racing(root, rel, data, ready=None, **kw):
        def journal_then_save(written):
            ready(written)
            (root / rel).write_bytes(saved)
        return real(root, rel, data, journal_then_save, **kw)
    monkeypatch.setattr(vault_disk, "replace_note", racing)
    _kill_once(monkeypatch, vault_store, "keep")
    with pytest.raises(Killed):
        v.sync()
    monkeypatch.undo()
    _quick_again(monkeypatch)
    v.sync()
    assert [h["data"] for h in vault_store.history(v.link["id"], "n.md")] == [saved]
    assert v.text("n.md") == "first line from gamma\n" and not list(v.root.glob(".gamma-*"))
    v.quiet()


@pytest.mark.parametrize("point", ["write_new", "replace_note"])
def test_a_kill_after_pulls_write_then_the_file_moved_keeps_one_page_and_one_file(vault, monkeypatch, owner, point):
    """The journal looked for the written file at its path only: moved before
    the restart, a new page's file was read as a second page and written
    again for the first, and a changed page's file doubled Gamma's block."""
    v = vault()
    v.write("old.md", "an old note of the vault\n")
    v.write("other.md", "other\n")
    v.sync()
    if point == "write_new":
        page, name = make_page(owner, "fresh", {"folders": [v.folder]})["id"], "fresh.md"
    else:
        page, name = v.page_of("old.md"), "old.md"
    v.edit(page, insert(page, "typed in gamma"))
    _kill_after(monkeypatch, vault_disk, point)
    with pytest.raises(Killed):
        v.sync()
    monkeypatch.undo()
    _quick_again(monkeypatch)
    os.makedirs(v.root / "sub")
    os.rename(v.root / name, v.root / "sub" / name)
    v.sync()
    notes = sorted(p.relative_to(v.root).as_posix() for p in v.root.rglob("*.md") if ".gamma" not in p.parts)
    assert v.page_of(f"sub/{name}") == page and v.page_count() == len(notes) == (3 if point == "write_new" else 2)
    assert v.texts(page).count("typed in gamma") == 1 and v.text(f"sub/{name}").count("typed in gamma") == 1
    v.quiet()


@pytest.mark.parametrize("gamma_edit", [True, False])
def test_a_paper_and_its_notes_renamed_together_and_edited_stay_one_page(vault, gamma_edit):
    """The notes kept their file id but changed past ``ID_SIMILAR``: they were
    a new page, and pull wrote ``sub/Moved 2.md`` for the paper's. The note
    that pairs with the moved paper is that paper's notes moved."""
    v = vault()
    v.write("Paper.pdf", _pdf("paper"), age=10)
    v.write("Paper.md", "alpha beta\n")
    v.write("other.md", "other\n")
    v.sync()
    page = v.page_of("Paper.md")
    if gamma_edit:
        v.edit(page, set_(v.find(page, "alpha beta"), "kappa beta"))
    v.write("Paper.md", "omega beta\n")
    os.makedirs(v.root / "sub")
    os.rename(v.root / "Paper.pdf", v.root / "sub" / "Moved.pdf")
    os.rename(v.root / "Paper.md", v.root / "sub" / "Moved.md")
    with open(v.root / "sub" / "Moved.md", "a", encoding="utf-8") as f:
        f.write("\n1. delta epsilon\n")
    v.sync()
    assert v.page_of("sub/Moved.md") == v.page_of("sub/Moved.pdf") == page and v.page_count() == 2
    assert v.texts(page) == ["omega beta", "1. delta epsilon"] and sorted(os.listdir(v.root / "sub")) == ["Moved.md",
                                                                                                         "Moved.pdf"]
    v.quiet()


def test_a_removal_waits_while_a_note_that_may_be_it_waits_to_be_read(vault, monkeypatch):
    """A safe save's gap took ``a.md`` from one scan, and ``b.md``, that note
    renamed, was saved again while the next round read it: that round trashed
    the page with ``b.md`` unread, and the one after made ``b.md`` another."""
    v = vault()
    body = "".join(f"line number {k} of a long enough note\n\n" for k in range(8))
    v.write("a.md", body)
    v.write("other.md", "other\n")
    v.sync()
    page = v.page_of("a.md")
    os.remove(v.root / "a.md")
    v.sync()
    v.write("b.md", body + "one more line\n")
    real, saved = pathlib.Path.read_bytes, []

    def read_bytes(p):
        data = real(p)
        if p.name == "b.md" and not saved:
            saved.append(1)
            p.write_bytes(data + b"\nand another\n")
        return data
    monkeypatch.setattr(pathlib.Path, "read_bytes", read_bytes)
    assert v.sync()["counts"]["waiting"] == 1 and v.state(page) == "live"
    monkeypatch.setattr(pathlib.Path, "read_bytes", real)
    v.sync()
    assert v.page_of("b.md") == page and v.state(page) == "live" and v.page_count() == 2
    v.quiet()


def test_a_page_round_makes_nothing_in_a_directory_renamed_since_the_last_scan(vault, owner):
    """The hot loop wrote a new page of folder D as ``D/fresh.md`` after the
    person renamed D to E: D came back, and the next scan read E as a new
    folder, the person's rename lost."""
    v = vault()
    v.write("D/x.md", "inside\n")
    v.write("top.md", "top\n")
    v.sync()
    d = _folder(v, "D")
    os.rename(v.root / "D", v.root / "E")
    page = make_page(owner, "fresh", {"folders": [d]})["id"]
    v.edit(page, insert(page, "fresh text"))
    assert v.sync_page(page)["counts"]["waiting"] == 1 and not (v.root / "D").exists()
    v.sync()
    assert v.layout().folders[d] == (v.folder, "E") and v.text("E/fresh.md") == "fresh text\n"
    assert sorted(v.rows()) == ["E/fresh.md", "E/x.md", "top.md"]
    v.quiet()


def test_a_force_push_keeps_a_highlight_it_deletes_with_its_quote_and_rectangles(vault):
    """The force's record held the highlight's note alone: its quote, its page
    and its rectangles were in no row and no file."""
    v = vault()
    page = _paper(v)
    hl = new_block_id()
    v.edit(page, insert(page, "why it matters", _mark("a sentence worth keeping"), hl))
    v.sync(force="push", confirm=True)
    assert hl not in v.blocks(page)
    (kept,) = [g for g in v.rows()["Paper.md"]["gone"] if g["id"] == hl]
    assert kept["props"]["quote"] == "a sentence worth keeping" and kept["props"]["pdf_position"]["rects"]
    assert kept["anchor"] == vault_sync._marker(hl) and kept["hash"] == md_lens.content_hash("why it matters")
    v.quiet()


@pytest.mark.parametrize("left", ["", "\n", "\r\n", "﻿", "﻿\r\n", " \n\t\n"])
def test_every_note_emptied_is_held_by_the_guard_as_removals_are(vault, left):
    """Emptying a note waited two scans, but the guard counted removals only:
    all ten pages lost their notes at once. A note left with only a line end,
    blanks or a byte order mark (Notepad's empty "UTF-8 with BOM") was no
    emptied one at all."""
    v = vault()
    for n in range(10):
        v.write(f"n{n}.md", f"note {n}, a paragraph of its own\n")
    v.sync()
    pages = [v.page_of(f"n{n}.md") for n in range(10)]
    for n in range(10):
        v.write(f"n{n}.md", left)
    v.sync()
    before = v.footprint()
    status = v.sync()
    assert status["held"] == {"removals": 10, "total": 10} and v.footprint() == before
    assert all(v.texts(p) for p in pages)
    v.sync(confirm=True)
    assert all(v.texts(p) == [] for p in pages)


def test_directories_named_as_no_vault_name_keep_their_names(vault):
    """Pull compared each directory with ``vault_name`` of its folder's name:
    ``[Archive]``, ``Week #3`` and ``My  Notes`` were renamed under the person
    after the first round, and so was a directory renamed to such a name."""
    v = vault()
    for rel in ("[Archive]/old.md", "Week #3/w.md", "My  Notes/n.md"):
        v.write(rel, "some text\n")
    v.sync()
    assert sorted(os.listdir(v.root)) == [".gamma", "My  Notes", "Week #3", "[Archive]"]
    v.quiet()
    os.rename(v.root / "Week #3", v.root / "Week #4")
    v.sync()
    assert sorted(os.listdir(v.root)) == [".gamma", "My  Notes", "Week #4", "[Archive]"] and _folder(v, "Week #4")
    commit_ops(v.ws, "folders", [{"op": "set", "id": _folder(v, "[Archive]"), "content": "Old things"}], actor=v.user)
    v.sync()
    assert sorted(os.listdir(v.root)) == [".gamma", "My  Notes", "Old things", "Week #4"]
    v.quiet()


def test_pull_rewrites_no_front_matter_key_gamma_did_not_change(vault):
    """A tag in another case than its label (``Physics`` for ``physics``) and
    a scalar ``aliases: Fox`` were rewritten by any edit of the page in Gamma."""
    v = vault()
    v.write("a.md", "---\ntags: [physics]\n---\nfirst\n")
    files = {"b.md": ("---\ntags: [Physics]\n---\nsecond\n", "second"),
             "c.md": ("---\naliases: Fox\ntags: physics\n---\nbody\n", "body")}
    for rel, (text, _word) in files.items():
        v.write(rel, text)
    v.sync()
    for rel, (_text, word) in files.items():
        page = v.page_of(rel)
        v.edit(page, set_(v.find(page, word), f"{word}, edited"))
    v.sync()
    for rel, (text, word) in files.items():
        assert v.text(rel) == text.replace(word, f"{word}, edited")
    v.quiet()


def test_a_file_moved_on_disk_to_the_vaults_top_stays_there(vault, owner):
    """The page filed in One and Two had its file at One; moved to the top it
    was filed in the vault's folder and Two, and pull, reading the first
    filing among the folders below the vault's, moved it into Two."""
    v = vault()
    one, two = _subfolder(owner, v, "One"), _subfolder(owner, v, "Two")
    page = make_page(owner, "Twice", {"folders": [one, two]})["id"]
    v.edit(page, insert(page, "text"))
    v.sync()
    assert v.text("One/Twice.md") == "text\n"
    os.rename(v.root / "One" / "Twice.md", v.root / "Twice.md")
    v.sync()
    assert v.page_of("Twice.md") == page and v.info(page).folders == [v.folder, two]
    v.quiet()


def test_a_pdf_pull_copied_out_follows_a_replacement_made_in_gamma(vault, owner):
    """The copy kept the old bytes for good, and a later save of it on disk
    put them back over Gamma's correction. One of the disk's is never
    written: its problem says the page's PDF is another now."""
    v = vault()
    up = owner.post("/api/uploads", files={"file": ("paper.pdf", _pdf("stored first"), "application/pdf")}).json()
    page = make_page(owner, "Copied", {"folders": [v.folder], **doc_props(up["doc_id"])})["id"]
    v.sync()
    second = _pdf("replaced in Gamma, a corrected version")
    up = owner.post("/api/uploads", files={"file": ("paper v2.pdf", second, "application/pdf")}).json()
    replace_attachment(v.ws, page, up["doc_id"], "", "paper v2.pdf", actor=v.user, owner=v.user)
    v.sync()
    assert (v.root / "Copied.pdf").read_bytes() == second and v.rows()["Copied.pdf"]["hash"] == up["doc_id"]
    v.quiet()
    own = _pdf("the person's own paper")
    v.write("Own.pdf", own, age=10)
    v.sync()
    mine = v.page_of("Own.pdf")
    up = owner.post("/api/uploads", files={"file": ("other.pdf", _pdf("put in Gamma"), "application/pdf")}).json()
    replace_attachment(v.ws, mine, up["doc_id"], "", "other.pdf", actor=v.user, owner=v.user)
    status = v.sync(ok=False)
    assert (v.root / "Own.pdf").read_bytes() == own and "replaced in Gamma" in v.rows()["Own.pdf"]["problem"]
    assert [k["path"] for k in status["kept"]] == ["Own.pdf"]


def test_a_pdf_rewritten_on_disk_keeps_one_linked_row(vault):
    """Each version left its row: the sweep queued every stale one each tick,
    and one whose stat matched by chance would serve the new bytes under an
    old name."""
    v = vault()
    v.write("P.pdf", _pdf("one"), age=10)
    v.sync()
    page = v.page_of("P.pdf")
    for n in range(3):
        v.write("P.pdf", _pdf(f"version {n}"), age=5 - n)
        v.sync()
    with connect_vaults_db() as conn:
        names = conn.execute("SELECT name FROM linked_files WHERE path = ?", (str(v.root / "P.pdf"),)).fetchall()
    assert names == [(f"{v.blocks(page)[page]['props']['doc']['hash']}.pdf",)]


def test_the_vaults_trash_holds_no_file_without_a_row(vault, monkeypatch):
    """A file put back at its path left the trash's copy behind, and a page
    moved out of the vault left its files there with no row: nothing swept
    them, ever. (The thirty days a file stays there: the next test.)"""
    monkeypatch.setattr(vault_disk, "TRASH_DAYS", 0)
    v = vault()
    for rel in ("n.md", "out.md", "keep.md"):
        v.write(rel, f"{rel} text\n")
    v.sync()
    n, out = v.page_of("n.md"), v.page_of("out.md")
    with connect_pages_db(v.ws) as conn:
        trash_page(v.ws, conn, n, actor=v.user)
    v.edit(out, {"op": "set", "id": out, "props": {"folders": None}})
    v.sync()

    def trashed():
        return sorted(p.name for p in (v.root / vault_disk.TRASH).rglob("*.md"))
    assert trashed() == ["n.md", "out.md"]
    v.write("n.md", "n.md text\n")                     # put back, by git
    v.sync()
    assert v.state(n) == "live" and v.page_of("n.md") == n and trashed() == ["out.md"]
    with connect_pages_db(v.ws) as conn:
        trash_page(v.ws, conn, out, actor=v.user)
    purge_page(v.ws, out, actor=v.user)
    v.sync()
    assert trashed() == [] and not vault_store.tombstones(v.link["id"])
    v.quiet()


def test_a_file_stays_in_the_vaults_trash_thirty_days_whatever_became_of_its_page(vault, monkeypatch):
    """docs/research/vaults.md, rule 3: Gamma never deletes a file the trash
    has held less than thirty days. A page purged at once leaves its file
    there until then; a note come back edited leaves the copy the trash took,
    with a row of no page, for the same sweep."""
    v = vault()
    for rel in ("gone.md", "back.md", "keep.md"):
        v.write(rel, f"{rel} has a few words in it\n")
    v.sync()
    gone, back = v.page_of("gone.md"), v.page_of("back.md")
    with connect_pages_db(v.ws) as conn:
        trash_page(v.ws, conn, gone, actor=v.user)
        trash_page(v.ws, conn, back, actor=v.user)
    v.sync()

    def trashed():
        return sorted(p.name for p in (v.root / vault_disk.TRASH).rglob("*.md"))
    assert trashed() == ["back.md", "gone.md"]
    purge_page(v.ws, gone, actor=v.user)
    v.write("back.md", "back.md has a few more words in it\n")      # put back, edited
    v.sync()
    assert v.state(back) == "live" and v.texts(back) == ["back.md has a few more words in it"]
    assert trashed() == ["back.md", "gone.md"]
    copy = next(t for t in vault_store.tombstones(v.link["id"]) if t["was"] == "back.md")
    assert copy["page_id"] == "" and vault_disk.trashed_at(copy["path"]) <= time.time()
    v.quiet()
    monkeypatch.setattr(vault_disk, "TRASH_DAYS", 0)                # thirty days later
    v.sync()
    assert trashed() == [] and not vault_store.tombstones(v.link["id"])
    v.quiet()


# --- counter-examples, a third set -----------------------------------------------------------------

def test_pull_alone_never_trashes_a_note_changed_on_disk_and_a_purge_takes_none_of_it(vault):
    """A round of pull alone (*Pull now*) moved a note changed on disk, whose
    page was in Recently deleted, to the vault's trash, and the page purged
    then took the file: the person's words were in no file, block or row."""
    v = vault()
    v.write("keep.md", "keep\n")
    v.write("n.md", "a note\n")
    v.sync()
    page = v.page_of("n.md")
    with connect_pages_db(v.ws) as conn:
        trash_page(v.ws, conn, page, actor=v.user)
    v.write("n.md", "a note\n\nwords typed on disk meanwhile\n")
    status = v.sync(ok=False, direction="pull")
    assert [k["path"] for k in status["kept"]] == ["n.md"] and v.text("n.md").endswith("meanwhile\n")
    purge_page(v.ws, page, actor=v.user)
    v.sync()
    assert v.texts(v.page_of("n.md")) == ["a note", "words typed on disk meanwhile"]
    v.quiet()


def test_a_note_saved_as_a_round_starts_whose_page_went_for_good_is_a_page_again(vault, monkeypatch):
    """Push waited for the note still being written; pull found its page gone
    for good, moved the file to the trash and swept it in the same round."""
    monkeypatch.setattr(vault_sync, "SETTLE_NOTE_S", 60)
    v = vault()
    v.write("keep.md", "keep\n", age=120)
    v.write("n.md", "a note\n", age=120)
    v.sync()
    page = v.page_of("n.md")
    with connect_pages_db(v.ws) as conn:
        trash_page(v.ws, conn, page, actor=v.user)
    purge_page(v.ws, page, actor=v.user)
    v.write("n.md", "a note\n\nwords saved a moment ago\n")
    v.sync(ok=False)
    assert v.text("n.md") == "a note\n\nwords saved a moment ago\n"
    monkeypatch.setattr(vault_sync, "SETTLE_NOTE_S", 0)
    v.sync()
    assert v.texts(v.page_of("n.md")) == ["a note", "words saved a moment ago"]
    v.quiet()


def test_a_note_renamed_only_in_case_and_rewritten_keeps_one_row_and_its_page(vault):
    """``a.md`` renamed ``A.md`` and rewritten was no move by its text, so it
    was a new page, while ``a.md``'s row found "its" file by a case-blind
    ``lstat`` and lived on: two rows on one file, two pages fighting over it."""
    v = vault()
    v.write("a.md", "alpha beta gamma delta epsilon\n")
    v.sync()
    page = v.page_of("a.md")
    os.rename(v.root / "a.md", v.root / "A.md")
    v.write("A.md", "a completely different text now\n\n- with a list\n")
    v.sync()
    assert sorted(v.rows()) == ["A.md"] and v.page_of("A.md") == page and v.page_count() == 1
    assert v.info(page).title == "A" and v.texts(page) == ["a completely different text now", "- with a list"]
    v.quiet()


@pytest.mark.parametrize("hot", [True, False])
def test_a_file_back_where_its_page_lived_is_that_pages_in_a_page_round_too(vault, hot):
    """The page left the vault while its file was off the disk, so its
    tombstone held no file; the file came back, then the page: the hot loop's
    page round, which has no scan, wrote the page as ``n 2.md``, and the next
    whole round made the person's ``n.md`` a second page."""
    v = vault()
    v.write("other.md", "other\n")
    v.write("n.md", "alpha beta gamma\n\n- delta epsilon\n")
    v.sync()
    page = v.page_of("n.md")
    os.remove(v.root / "n.md")
    away = new_block_id()
    commit_ops(v.ws, "folders", [{"op": "insert", "id": away, "parent": "folders", "content": "Away"}], actor=v.user)
    v.edit(page, {"op": "set", "id": page, "props": {"folders": [away]}})
    v.sync()
    v.write("n.md", "alpha beta gamma\n\n- delta epsilon\n\nzeta typed meanwhile\n")
    v.edit(page, {"op": "set", "id": page, "props": {"folders": [v.folder]}})
    if hot:
        v.sync_page(page)
    v.sync()
    assert sorted(p.name for p in v.root.glob("*.md")) == ["n.md", "other.md"] and v.page_count() == 2
    assert v.page_of("n.md") == page and v.texts(page)[-1] == "zeta typed meanwhile"
    v.quiet()


def test_a_block_gamma_moved_to_another_page_takes_the_edit_made_on_disk_with_it(vault):
    """The block read as deleted from its old page, and a block deleted in
    Gamma whose text changed on disk comes back: under a fresh id, so its text
    stood on both pages and in both files."""
    v = vault()
    v.write("a.md", "first of a\n\nthe block that moves\n")
    v.write("b.md", "first of b\n")
    v.sync()
    a, b = v.page_of("a.md"), v.page_of("b.md")
    block = v.find(a, "the block that moves")
    with connect_pages_db(v.ws) as conn:
        write_lock(conn)
        move_across_pages(v.ws, conn, block, b, "a0", a, b, actor=v.user)
    v.write("a.md", "first of a\n\nthe block that moves, edited on disk\n")
    v.sync()
    assert v.texts(a) == ["first of a"] and v.text("a.md") == "first of a\n"
    both = ["first of b", "the block that moves, edited on disk"]
    assert sorted(v.texts(b)) == both and sorted(v.text("b.md").strip().split("\n\n")) == both
    assert v.blocks(b)[block]["content"] == "the block that moves, edited on disk"
    v.quiet()


def test_a_note_whose_time_is_ahead_of_this_clock_is_read_and_holds_back_no_removal(vault, monkeypatch):
    """A note saved through a share whose clock runs ahead waited as one still
    being written until this clock passed its time: not pushed, not pulled,
    and every removal of a note in the vault waited with it."""
    monkeypatch.setattr(vault_sync, "SETTLE_NOTE_S", 1.0)
    v = vault()
    v.write("n.md", "first line\n\nsecond line\n", age=30)
    v.write("sub/gone.md", "a note the person deletes\n", age=30)
    v.sync()
    page, gone = v.page_of("n.md"), v.page_of("sub/gone.md")
    path = v.write("n.md", "first line edited on disk\n\nsecond line\n")
    ahead = time.time() + 3600
    os.utime(path, (ahead, ahead))
    os.remove(v.root / "sub" / "gone.md")
    v.sync()
    v.sync()
    assert v.texts(page) == ["first line edited on disk", "second line"] and v.state(gone) == "trashed"
    v.edit(page, set_(v.find(page, "second line"), "second line edited in gamma"))
    v.sync()
    assert v.text("n.md") == "first line edited on disk\n\nsecond line edited in gamma\n"


def _label(v, name):
    label = new_block_id()
    commit_ops(v.ws, LABELS, [{"op": "insert", "id": label, "parent": LABELS, "content": name}], actor=v.user)
    return label


def test_a_label_gamma_added_before_an_edit_on_disk_reaches_the_tags_and_stays(vault):
    """Push called the page level with its file by its blocks alone, so a
    label Gamma added never reached ``tags``; and the next change of ``tags``
    on disk set the page's labels to the file's, dropping Gamma's."""
    v = vault()
    v.write("n.md", "---\ntags: [physics]\n---\nbody text\n")
    v.sync()
    page = v.page_of("n.md")
    v.edit(page, {"op": "set", "id": page, "props": {LABELS: [*v.blocks(page)[page]["props"][LABELS], _label(v, "quantum")]}})
    v.write("n.md", "---\ntags: [physics]\n---\nbody text, edited on disk\n")
    v.sync()
    assert v.text("n.md") == "---\ntags: [physics, quantum]\n---\nbody text, edited on disk\n"
    v.quiet()


def test_tags_changed_on_both_sides_keep_both_changes(vault):
    v = vault()
    v.write("n.md", "---\ntags: [physics, old]\n---\nbody text\n")
    v.sync()
    page = v.page_of("n.md")
    v.edit(page, {"op": "set", "id": page, "props": {LABELS: [*v.blocks(page)[page]["props"][LABELS], _label(v, "quantum")]}})
    v.write("n.md", "---\ntags: [physics, maths]\n---\nbody text\n")
    v.sync()
    assert v.info(page).labels == ["physics", "maths", "quantum"]
    assert v.text("n.md") == "---\ntags: [physics, maths, quantum]\n---\nbody text\n"
    v.quiet()


@pytest.mark.parametrize("key", ["aliases", "title"])
def test_an_alias_or_a_title_gamma_changed_before_an_edit_on_disk_reaches_the_front_matter(vault, key):
    v = vault()
    front = "aliases: [one]" if key == "aliases" else "title: Old title"
    v.write("n.md", f"---\n{front}\n---\nbody text\n")
    v.sync()
    page = v.page_of("n.md")
    v.edit(page, {"op": "set", "id": page, "props": {"aliases": ["one", "two"]}} if key == "aliases"
           else {"op": "set", "id": page, "content": "New title"})
    v.write("n.md", f"---\n{front}\n---\nbody text, edited on disk\n")
    v.sync()
    want = "aliases: [one, two]" if key == "aliases" else "title: New title"
    assert v.text("n.md") == f"---\n{want}\n---\nbody text, edited on disk\n"
    v.quiet()


def test_a_label_renamed_in_gamma_reaches_the_tags_of_its_notes(vault):
    """A label's name is in the labels tree: renaming it moves no page's seq,
    so pull passed its pages by, and the next tags edit on disk made the old
    name again and took the renamed label off the page."""
    v = vault()
    v.write("n.md", "---\ntags: [physics]\n---\nbody text\n")
    v.write("plain.md", "no front matter\n")
    v.sync()
    page = v.page_of("n.md")
    (label,) = v.blocks(page)[page]["props"][LABELS]
    commit_ops(v.ws, LABELS, [{"op": "set", "id": label, "content": "quantum"}], actor=v.user)
    v.sync()
    assert v.text("n.md") == "---\ntags: [quantum]\n---\nbody text\n" and v.text("plain.md") == "no front matter\n"
    v.quiet()


def test_a_kill_between_the_rows_moving_and_the_pages_refiled_finishes_the_move(vault, monkeypatch):
    """The rows went to the new paths in one write and the pages were filed
    and named after: a kill between left every later round seeing no move."""
    v = vault()
    v.write("first.md", "the first note, which moves too\n")
    v.write("note.md", "the note the person moves\n")
    v.write("Elsewhere/other.md", "other\n")
    v.sync()
    first, page = v.page_of("first.md"), v.page_of("note.md")
    os.rename(v.root / "first.md", v.root / "Elsewhere" / "first moved.md")
    os.rename(v.root / "note.md", v.root / "Elsewhere" / "renamed.md")
    _kill_after(monkeypatch, LocalRemote, "set_page")
    with pytest.raises(Killed):
        v.sync()
    monkeypatch.undo()
    _quick_again(monkeypatch)
    v.sync()
    elsewhere = _folder(v, "Elsewhere")
    assert {p: (v.info(p).title, v.info(p).folders) for p in (first, page)} == {first: ("first moved", [elsewhere]),
                                                                              page: ("renamed", [elsewhere])}
    v.quiet()


@pytest.mark.parametrize("how", ["renamed", "deleted", "its directory renamed"])
def test_a_vault_that_pushes_no_notes_follows_a_note_moved_on_disk_and_reports_one_gone(vault, owner, how):
    """With push off nothing on disk read as moved or gone: the row stayed
    where pull wrote the note, its page's edits reached no file, nothing said
    so, and a force pull wrote nothing either."""
    v = vault(push=folder_links.PUSH_OFF)
    page = make_page(owner, "Pulled", {"folders": [_subfolder(owner, v, "Sub")]})["id"]
    v.edit(page, insert(page, "text"))
    v.sync()
    where = {"renamed": "Sub/Renamed.md"}.get(how, "Sub/Pulled.md")
    if how == "renamed":
        os.rename(v.root / "Sub" / "Pulled.md", v.root / where)
    elif how == "deleted":
        os.remove(v.root / where)
    else:
        os.rename(v.root / "Sub", v.root / "Sub2")       # a pull-only vault's directories are its folders' names
    v.sync(ok=False)
    v.edit(page, set_(v.find(page, "text"), "text edited"))
    status = v.sync(ok=False)
    if how == "deleted":
        assert [k["path"] for k in status["kept"]] == [where] and not (v.root / where).exists()
        v.sync(force="pull")
    else:
        assert v.rows()[where]["page_id"] == page and status["kept"] == []
    assert v.text(where) == "text edited\n" and sorted(v.rows()) == [where]
    v.quiet()


@pytest.mark.parametrize("push", [folder_links.PUSH_OFF, folder_links.PUSH_PDFS, folder_links.PUSH_ALL])
def test_a_kill_between_pull_moving_a_directory_and_its_rows_converges(vault, owner, monkeypatch, push):
    """A folder renamed in Gamma renames its directory, then the rows: a kill
    between left the rows on the old path, for good where the vault does not
    push notes."""
    v = vault(push=push)
    two = _subfolder(owner, v, "Two")
    page = make_page(owner, "Moving", {"folders": [two]})["id"]
    v.edit(page, insert(page, "text"))
    v.sync()
    commit_ops(v.ws, "folders", [{"op": "set", "id": two, "content": "Deux"}], actor=v.user)
    _kill_after(monkeypatch, vault_disk, "move")
    with pytest.raises(Killed):
        v.sync()
    monkeypatch.undo()
    _quick_again(monkeypatch)
    v.sync()
    v.edit(page, set_(v.find(page, "text"), "text edited"))
    v.sync()
    assert v.text("Deux/Moving.md") == "text edited\n" and sorted(v.rows()) == ["Deux/Moving.md"]
    assert sorted(vault_store.dirs(v.link["id"])) == ["Deux"]
    v.quiet()


def test_a_note_renamed_while_its_page_is_in_recently_deleted_comes_back_named_by_it(vault):
    """The move passed by a page that was not live, and the push of the note
    then brought it back with its old title and filing."""
    v = vault()
    v.write("Sub/x.md", "x\n")
    v.write("old name.md", "a note the person renames\n")
    v.sync()
    page = v.page_of("old name.md")
    with connect_pages_db(v.ws) as conn:
        trash_page(v.ws, conn, page, actor=v.user)
    os.rename(v.root / "old name.md", v.root / "Sub" / "new name.md")
    v.write("Sub/new name.md", "a note the person renames, and edits\n")
    v.sync()
    assert v.state(page) == "live" and v.page_of("Sub/new name.md") == page
    assert (v.info(page).title, v.info(page).folders) == ("new name", [_folder(v, "Sub")])
    v.quiet()


def test_a_highlight_gamma_deleted_whose_note_was_edited_on_disk_comes_back_a_highlight(vault):
    """It came back under its id as a plain note, while the file kept the
    callout: the page and the file never agreed again."""
    v = vault()
    page = _paper(v)
    hl = new_block_id()
    v.edit(page, insert(page, "", _mark("the quoted words"), hl))
    v.sync()
    v.edit(page, {"op": "delete", "id": hl})
    v.write("Paper.md", v.text("Paper.md").replace("> the quoted words\n", "> the quoted words\n>\n> my note on it\n"))
    v.sync()
    block = v.blocks(page)[hl]
    assert block["content"] == "my note on it" and block_kind(block["parent"], page, block["props"]) == "highlight"
    assert block["props"]["quote"] == "the quoted words" and block["props"]["pdf_position"]["pageNumber"] == 1
    v.quiet()


def test_two_files_of_one_name_trashed_in_one_second_take_two_trash_paths(vault, monkeypatch):
    """The trash's free name asked the disk alone: a tombstone whose file had
    left the disk held its path with nothing there, and the next file of that
    name trashed in that second took it, refused by the row's key."""
    monkeypatch.setattr(vault_disk, "stamp", lambda now=None: "20261107-120000")
    v = vault()
    v.write("keep.md", "keep\n")
    v.write("n.md", "the first note of this name\n")
    v.sync()
    first = v.page_of("n.md")
    os.remove(v.root / "n.md")
    with connect_pages_db(v.ws) as conn:
        trash_page(v.ws, conn, first, actor=v.user)
    v.sync()
    v.write("n.md", "another note of the same name, other text\n")
    v.sync()
    second = v.page_of("n.md")
    with connect_pages_db(v.ws) as conn:
        trash_page(v.ws, conn, second, actor=v.user)
    v.sync()
    tombs = {t["page_id"]: t["path"] for t in vault_store.tombstones(v.link["id"])}
    assert len(set(tombs.values())) == 2 and v.text(tombs[second]).startswith("another note")
    v.quiet()


def test_the_guard_counts_the_removals_since_the_last_quiet_round(vault):
    """A slow deletion: three notes removed, then four, each
    under half of twelve, together more."""
    v = vault()
    for n in range(12):
        v.write(f"n{n}.md", f"note {n}\n")
    v.sync()
    pages = [v.page_of(f"n{n}.md") for n in range(12)]
    for n in range(3):
        os.remove(v.root / f"n{n}.md")
    v.sync()
    v.sync()
    for n in range(3, 7):
        os.remove(v.root / f"n{n}.md")
    v.sync()
    status = v.sync()
    assert status["held"] == {"removals": 7, "total": 12}
    assert [v.state(p) for p in pages] == ["trashed"] * 3 + ["live"] * 9


def test_a_gamma_edit_landing_between_the_plan_and_the_lock_is_planned_again_outside_it(vault, monkeypatch):
    """The plan was built again under the write lock: 0.9 s held for a note of
    20,000 blocks while every other writer of the workspace waited."""
    v = vault()
    v.write("R.md", "one\n\ntwo\n")
    v.sync()
    page = v.page_of("R.md")
    two = v.find(page, "two")
    real_apply, real_merge = LocalRemote.apply, vault_merge.merge_page
    inside, under_lock, raced = [False], [], []

    def racing(self, page_id, build=None, **kw):
        if kw.get("built") is not None and not raced:
            raced.append(1)
            v.edit(page_id, set_(two, "two, typed in Gamma"))
        inside[0] = True
        try:
            return real_apply(self, page_id, build, **kw)
        finally:
            inside[0] = False

    def merging(*args, **kwargs):
        under_lock.append(inside[0])
        return real_merge(*args, **kwargs)
    monkeypatch.setattr(LocalRemote, "apply", racing)
    monkeypatch.setattr(vault_merge, "merge_page", merging)
    v.write("R.md", "one\n\ntwo\n\nthree\n")
    v.sync()
    assert raced and len(under_lock) >= 2 and not any(under_lock)
    assert v.texts(page) == ["one", "two, typed in Gamma", "three"] and v.text("R.md") == "one\n\ntwo, typed in Gamma\n\nthree\n"


def test_a_notes_source_names_its_pdf_as_its_links_would(vault):
    """The PDF a note's ``source`` named had a rule of its own: no
    ``%20``, no NFC, so the link resolved and the pairing did not."""
    v = vault()
    cafe = unicodedata.normalize("NFD", "Café.pdf")
    v.write("Papers/My Paper.pdf", _pdf("mine"), age=10)
    v.write(f"Papers/{cafe}", _pdf("the café"), age=10)
    v.write("notes/a.md", "---\nsource: My%20Paper.pdf\n---\nnotes on mine\n")
    v.write("notes/b.md", "---\nsource: \"[[Café.pdf]]\"\n---\nnotes on the café\n")
    v.sync()
    assert v.page_of("notes/a.md") == v.page_of("Papers/My Paper.pdf")
    assert v.page_of("notes/b.md") == v.page_of(f"Papers/{cafe}") and v.page_count() == 2


def test_a_notes_parent_in_its_front_matter_makes_it_that_notes_sub_page(vault):
    """A new note whose front matter names its ``parent`` is that note's
    sub-page, as the vault import reads it, where it was a library page. Its
    file stays where the person keeps it."""
    v = vault()
    v.write("Parent.md", "parent text\n")
    v.write("Child.md", "---\nparent: \"[[Parent]]\"\n---\nchild text\n")
    v.write("Sub/Kid.md", "---\nparent: \"[[Parent]]\"\n---\nkid text\n")
    v.sync()
    parent = v.page_of("Parent.md")
    assert [v.remote.filing(v.page_of(rel))[1] for rel in ("Child.md", "Sub/Kid.md")] == [parent, parent]
    assert sorted(v.rows()) == ["Child.md", "Parent.md", "Sub/Kid.md"]
    v.quiet()


# --- the hot loop -----------------------------------------------------------------------------

def test_the_page_round_reads_and_writes_only_that_pages_files(vault):
    v = vault()
    v.write("Hot.md", "hot\n")
    v.write("Cold.md", "cold\n")
    v.sync()
    hot, cold = v.page_of("Hot.md"), v.page_of("Cold.md")
    v.write("Cold.md", "cold, changed\n")
    v.edit(hot, set_(v.find(hot, "hot"), "hot, typed"))
    status = v.sync_page(hot)
    assert status["last_error"] == "" and v.text("Hot.md") == "hot, typed\n" and v.texts(cold) == ["cold"]
    v.write("Hot.md", "hot, saved on disk\n")
    v.sync_page(hot)
    assert v.texts(hot) == ["hot, saved on disk"]
    os.remove(v.root / "Hot.md")
    v.sync_page(hot)
    v.sync_page(hot)
    assert v.state(hot) == "live"                  # no scan, no removal
    v.sync()
    assert v.texts(cold) == ["cold, changed"]


# --- counter-examples on one engine, a first set -----------------------------------------------------

@pytest.mark.parametrize("what", ["directory", "file"])
def test_a_force_pull_after_a_move_on_disk_writes_gammas_text_where_the_note_now_is(vault, what):
    """A force pull reads nothing of the disk's side, so it wrote
    the note again where Gamma files it and left the moved one to become a
    second page. The rows follow the move first, as in a vault that does not
    push notes."""
    v = vault()
    v.write("Sub/Note.md", "force pulled words\n")
    v.sync()
    page = v.page_of("Sub/Note.md")
    v.edit(page, set_(v.find(page, "force pulled words"), "Gamma's words"))
    if what == "directory":
        os.rename(v.root / "Sub", v.root / "Renamed")
    else:
        os.rename(v.root / "Sub" / "Note.md", v.root / "Sub" / "Renamed.md")
    v.sync(force="pull", confirm=True)
    for _ in range(2):
        v.sync()
    files = sorted(p.relative_to(v.root).as_posix() for p in v.root.rglob("*.md") if ".gamma" not in p.parts)
    assert len(files) == 1 and v.text(files[0]) == "Gamma's words\n", files
    assert {r["page_id"] for r in v.rows().values() if r["kind"] == "notes"} == {page} and v.page_count() == 1
    v.quiet()


@pytest.mark.parametrize("why", ["caret", "changed"])
def test_a_split_pull_cannot_send_waits_and_never_names_a_block_gamma_lacks(vault, monkeypatch, why):
    """When the split of a block of two paragraphs could not go to
    Gamma (a caret on it, the page changed meanwhile), the file was written
    with the second paragraph under an id no block has; edited on disk it went
    in as a new block beside the one Gamma kept. The file waits for the split."""
    v = vault()
    v.write("Split.md", "alpha\n")
    v.sync()
    page = v.page_of("Split.md")
    block = v.find(page, "alpha")
    v.edit(page, set_(block, "alpha\n\nbeta"))
    with monkeypatch.context() as m:
        if why == "caret":
            m.setattr(v.remote, "carets", lambda page_id: {block})
        else:
            real = LocalRemote.apply

            def apply(self, page_id, build=None, *, token="", built=None):
                if built is not None:
                    raise vault_sync.Changed(page_id)
                return real(self, page_id, build, token=token, built=built)
            m.setattr(LocalRemote, "apply", apply)
        status = v.sync()
        assert status["counts"]["waiting"] == 1 and v.text("Split.md") == "alpha\n"
    v.sync()
    assert v.texts(page) == ["alpha", "beta"] and v.text("Split.md") == "alpha\n\nbeta\n"
    v.write("Split.md", "alpha\n\nbeta edited\n")
    for _ in range(2):
        v.sync()
    assert v.texts(page) == ["alpha", "beta edited"]
    v.quiet()


@pytest.mark.parametrize("how", ["renamed", "saved as"])
def test_a_papers_note_renamed_and_edited_on_both_sides_stays_the_papers(vault, how):
    """A paper's note renamed in place and edited on both sides,
    or saved under another name with a ``title:`` key, read as a new page,
    and pull wrote the paper's note again beside its PDF. Front matter and
    marks are no words of the note: it is one note moved, both edits kept."""
    v = vault()
    v.write("Paper.pdf", _pdf("a paper"), age=30)
    v.write("Paper.md", "alpha beta\n" if how == "saved as" else "alpha\n")
    v.sync()
    page = v.page_of("Paper.md")
    block = v.find(page, v.texts(page)[0])
    if how == "renamed":
        os.rename(v.root / "Paper.md", v.root / "Reading notes.md")
        with open(v.root / "Reading notes.md", "a", encoding="utf-8") as f:
            f.write("\n- [ ] beta gamma\n")
        v.edit(page, set_(block, "alpha delta"))
        new, want = "Reading notes.md", ["- [ ] beta gamma", "alpha delta"]
    else:
        os.remove(v.root / "Paper.md")
        v.write("Renamed.md", "---\ntitle: My reading\n---\nalpha beta\n\ngamma\n")
        v.edit(page, set_(block, "alpha beta eta"))
        new, want = "Renamed.md", ["alpha beta eta", "gamma"]
    for _ in range(3):
        v.sync()
    notes = sorted(p.name for p in v.root.glob("*.md"))
    assert notes == [new] and v.page_of(new) == page and v.page_of("Paper.pdf") == page, (notes, v.rows())
    assert sorted(v.texts(page)) == want and v.page_count() == 1
    v.quiet()


def test_a_papers_new_note_that_cannot_be_written_is_reported_under_its_own_name(vault):
    """The reason a paper's new note was not written was reported
    under the PDF's path ("Paper.pdf: an open code block")."""
    v = vault()
    v.write("Paper.pdf", _pdf("a paper"), age=30)
    v.sync()
    page = v.page_of("Paper.pdf")
    v.edit(page, {**insert(page, "```\nunclosed"), "position": "a0"}, {**insert(page, "after it"), "position": "a1"})
    status = v.sync(ok=False)
    assert [k["path"] for k in status["kept"]] == ["Paper.md"] and "open code block" in status["kept"][0]["why"]
    assert not (v.root / "Paper.md").exists()


# --- counter-examples on one engine, a second set ----------------------------------------------------

@pytest.mark.parametrize("front", ["tags: [mytag]", "title: Named\ntags: [mytag]"])
@pytest.mark.parametrize("point", ["landed", "before"])
def test_tags_typed_on_disk_reach_gamma_with_the_blocks_wherever_the_round_dies(vault, monkeypatch, front, point):
    """The labels were set after the base was the file, so a round
    that died in between left a base that named the tag and a page without
    the label, and the next pull took the tag out of the file. The labels go
    in the plan with the blocks."""
    v = vault()
    v.write("note.md", "alpha\n", age=10)
    v.write("other.md", "other\n", age=10)
    v.sync()
    page = v.page_of("note.md")
    v.write("note.md", f"---\n{front}\n---\nalpha\n", age=10)
    promote = vault_store.promote

    def killed_after(link_id, path, token, **fields):
        if path == "note.md":
            raise Killed()
        return promote(link_id, path, token, **fields)

    def killed_before(self, page_id, build=None, **kw):
        raise Killed()
    with monkeypatch.context() as m:
        if point == "landed":
            m.setattr(vault_store, "promote", killed_after)
        else:
            m.setattr(LocalRemote, "apply", killed_before)
        with pytest.raises(Killed):
            v.sync()
    for _ in range(2):
        v.sync()
    assert "mytag" in v.text("note.md") and [n.casefold() for n in v.info(page).labels] == ["mytag"]
    if "Named" in front:
        assert v.info(page).title == "Named" and "title: Named" in v.text("note.md")
    v.quiet()


def test_a_force_push_over_a_highlight_gamma_made_records_its_quote_and_page(vault):
    """The record a force push left of a highlight it deleted that
    had no note was an empty row: its quote was then in the base's ``gone``
    alone, which a note written anew empties. The record names the quote and
    the page."""
    v = vault()
    page = _paper(v)
    v.edit(page, insert(page, "", _mark("quotedwordonly")))
    v.sync(force="push", confirm=True)
    (row,) = [c for c in v.conflicts(page) if "quotedwordonly" in c[2]]
    assert row[2] == "p. 1\n> quotedwordonly" and row[6] == 1
    v.quiet()


@pytest.mark.parametrize("paper", [False, True])
def test_a_force_pull_after_a_note_was_written_on_and_moved_follows_it(vault, paper):
    """A short note written on at length and moved, keeping its file
    id, read as no move (alike below ``ID_SIMILAR``): the force pull wrote it
    again where it was and the moved one became a second page. Its old text
    kept in the new is evidence with the id."""
    v = vault()
    if paper:
        v.write("Paper.pdf", _pdf("paper words"), age=10)
    v.write("Paper.md", "uniqueword\n", age=10)
    v.write("other.md", "other\n", age=10)
    v.sync()
    page = v.page_of("Paper.md")
    v.write("Paper.md", "> quoted line here\n\nuniqueword\n\nthree more words added\n\n", age=10)
    (v.root / "sub").mkdir()
    os.rename(v.root / "Paper.md", v.root / "sub" / "moved.md")
    v.sync(force="pull", confirm=True)
    for _ in range(2):
        v.sync()
    assert not (v.root / "Paper.md").exists() and v.text("sub/moved.md") == "uniqueword\n"
    assert v.page_of("sub/moved.md") == page and v.page_count() == 2
    v.quiet()


def test_a_line_moved_in_a_note_brings_back_no_block_gamma_took_to_another_page(vault):
    """The lens pairs identical text in order, so a line moved past
    another read as one deleted and one new, and a block Gamma had moved to
    another page came back under a fresh id: its text on both pages. A line
    the file only moved is that block."""
    v = vault()
    v.write("a.md", "first\n\nmover\n\nlast\n")
    v.write("b.md", "bee\n")
    v.sync()
    a, b = v.page_of("a.md"), v.page_of("b.md")
    mover = v.find(a, "mover")
    with connect_pages_db(v.ws) as conn:
        write_lock(conn)
        move_across_pages(v.ws, conn, mover, b, "a0", a, b, actor=v.user)
    v.write("a.md", "mover\n\nfirst\n\nlast\n")
    for _ in range(2):
        v.sync()
    assert v.texts(a) == ["first", "last"] and v.text("a.md") == "first\n\nlast\n"
    assert sorted(v.texts(b)) == ["bee", "mover"] and mover in v.blocks(b)
    v.quiet()


def test_a_line_moved_with_its_marker_dropped_takes_the_change_to_the_page_gamma_moved_it_to(vault):
    """The same move, the line's list marker dropped too: the lens pairs one
    class only, and the line went in as a new block of the note's page while
    the other page kept the item, its text on both. It is that block, its
    class changed, and the change goes with it."""
    v = vault()
    v.write("a.md", "- alpha beta\n\ngamma delta\n\nepsilon\n")
    v.write("b.md", "zeta\n")
    v.sync()
    a, b = v.page_of("a.md"), v.page_of("b.md")
    mover = v.find(a, "- alpha beta")
    with connect_pages_db(v.ws) as conn:
        write_lock(conn)
        move_across_pages(v.ws, conn, mover, b, "a0", a, b, actor=v.user)
    v.write("a.md", "gamma delta\n\nepsilon\n\nalpha beta\n")
    for _ in range(2):
        v.sync()
    assert v.texts(a) == ["gamma delta", "epsilon"] and v.text("a.md") == "gamma delta\n\nepsilon\n"
    assert sorted(v.texts(b)) == ["alpha beta", "zeta"] and mover in v.blocks(b)
    v.quiet()


def test_a_marker_typed_back_on_a_line_gamma_moved_brings_back_no_block_the_file_dropped(vault):
    """The marker dropped (the item in the base's ``gone``, the line a new
    block), that block moved to another page in Gamma, and the marker typed
    back: the line's text matched the item in ``gone``, which came back on the
    note's page beside the moved block. The line is the moved block, its class
    changed again, and the change goes with it."""
    v = vault()
    v.write("a.md", "first\n\n- alpha beta gamma\n\nlast\n")
    v.write("b.md", "zeta\n")
    v.sync()
    a, b = v.page_of("a.md"), v.page_of("b.md")
    v.write("a.md", "first\n\nalpha beta gamma\n\nlast\n")
    v.sync()
    mover = v.find(a, "alpha beta gamma")
    with connect_pages_db(v.ws) as conn:
        write_lock(conn)
        move_across_pages(v.ws, conn, mover, b, "a0", a, b, actor=v.user)
    v.write("a.md", "first\n\n- alpha beta gamma\n\nlast\n")
    for _ in range(2):
        v.sync()
    assert v.texts(a) == ["first", "last"] and v.text("a.md") == "first\n\nlast\n"
    assert sorted(v.texts(b)) == ["- alpha beta gamma", "zeta"] and mover in v.blocks(b)
    v.quiet()


@pytest.mark.parametrize("how", ["killed", "planned again"])
def test_the_change_a_moved_block_took_on_its_page_goes_in_once(vault, monkeypatch, how):
    """Gamma moved a block to another page and edited it there, the file
    changed its line: ``_follow`` merged the change into that page before the
    note's own plan went, and the round died after it, or a write of the
    note's page made the plan go round again. The same change was merged into
    the text it gave: ``alpha beta gtyped ptyped ptyped``, then in the other
    note's file."""
    v = vault()
    v.write("a.md", "first of a\n\nalpha beta\n")
    v.write("b.md", "first of b\n")
    v.sync()
    a, b = v.page_of("a.md"), v.page_of("b.md")
    block = v.find(a, "alpha beta")
    with connect_pages_db(v.ws) as conn:
        write_lock(conn)
        move_across_pages(v.ws, conn, block, b, "a0", a, b, actor=v.user)
    v.edit(b, set_(block, "alpha beta gtyped"))
    v.write("a.md", "first of a\n\nalpha beta ptyped\n")
    follow, calls = vault_sync.Round._follow, []

    def following(self, moved):
        follow(self, moved)
        calls.append(moved)
        if len(calls) == 1 and how == "killed":
            raise Killed("after the follow")
        if len(calls) == 1:
            v.edit(a, insert(a, "typed in a meanwhile"))
    monkeypatch.setattr(vault_sync.Round, "_follow", following)
    if how == "killed":
        with pytest.raises(Killed):
            v.sync()
    for _ in range(2):
        v.sync()
    assert len(calls) > 1 and v.blocks(b)[block]["content"] == "alpha beta gtyped ptyped"
    assert v.text("b.md").count("ptyped") == 1 and "alpha beta" not in v.text("a.md")
    v.quiet()


def _pruned(v, page_id):
    """The page's log as pruning leaves it after more writes: its newest row."""
    with connect_pages_db(v.ws) as conn:
        newest = conn.execute("SELECT MAX(seq) FROM page_ops WHERE page_id = ?", (page_id,)).fetchone()[0]
        conn.execute("DELETE FROM page_ops WHERE page_id = ? AND seq < ?", (page_id, newest))


@pytest.mark.parametrize("change", ["a line added", "words added", "words added, then taken out"])
def test_a_plan_whose_batch_the_log_no_longer_holds_is_found_on_the_page(vault, monkeypatch, change):
    """The round died after its plan landed and before the base was saved, the
    vault was away a day while Gamma wrote the page on, and pruning took the
    plan's batch: the log said "not landed", the pending was dropped, and the
    merge from the old base put the note's new line in twice, or its new words
    (``alpha beta gamma gamma delta``), or took no notice of the person taking
    them out again, pull writing them back. Where the log no longer reaches
    the plan, the page says: a block the plan made, or the text it wrote."""
    v = vault()
    v.write("a.md", "alpha beta\n\nother\n\nkeep\n")
    v.sync()
    a = v.page_of("a.md")
    v.write("a.md", "alpha beta\n\nnew line here\n\nother\n\nkeep\n" if change == "a line added"
            else "alpha beta gamma\n\nother\n\nkeep\n")
    _kill_once(monkeypatch, vault_store, "promote")
    with pytest.raises(Killed):
        v.sync()
    monkeypatch.undo()
    _quick_again(monkeypatch)
    keep = v.find(a, "keep")
    for n in range(3):
        v.edit(a, set_(keep, f"keep {n}"))
    _pruned(v, a)
    want = {"a line added": "alpha beta\n\nnew line here\n\nother\n\nkeep 2\n",
            "words added": "alpha beta gamma delta\n\nother\n\nkeep 2\n",
            "words added, then taken out": "alpha beta\n\nother\n\nkeep 2\n"}[change]
    if change != "a line added":
        v.write("a.md", want.replace("keep 2", "keep"))
    for _ in range(2):
        v.sync()
    assert v.text("a.md") == want and v.texts(a) == want.strip().split("\n\n")
    v.quiet()


def test_a_moved_blocks_line_moved_past_lines_the_file_deleted_goes_with_it(vault):
    """Gamma moved an item to another page; on disk the person deleted the
    paragraph and the item above it, and moved the item's line down with its
    marker dropped. The lens paired that line with the deleted paragraph by
    its place, which took the text on the note's page: the text on both
    pages. The line is the moved block."""
    v = vault()
    v.write("a.md", "Opening remarks\n\n- second point\n- the mover sentence here\n\ngamma line\n- final item\n")
    v.write("b.md", "- other one\n\nother two\n")
    v.sync()
    a, b = v.page_of("a.md"), v.page_of("b.md")
    mover = v.find(a, "- the mover sentence here")
    with connect_pages_db(v.ws) as conn:
        write_lock(conn)
        move_across_pages(v.ws, conn, mover, b, "a0", a, b, actor=v.user)
    v.write("a.md", "\n\n\nthe mover sentence here\n\ngamma line\n- final item\n")
    for _ in range(2):
        v.sync()
    assert v.texts(a) == ["gamma line", "- final item"]
    assert sorted(v.texts(b)) == ["- other one", "other two", "the mover sentence here"]
    assert v.blocks(b)[mover]["content"] == "the mover sentence here"
    v.quiet()


def test_a_line_moved_in_a_note_moves_its_block(vault):
    """The same rule within a page: the block moves, its id and its
    properties kept, never one deleted and one made."""
    v = vault()
    v.write("a.md", "first\n\nmover\n\nlast\n")
    v.sync()
    a = v.page_of("a.md")
    last = v.find(a, "last")
    v.write("a.md", "last\n\nfirst\n\nmover\n")
    v.sync()
    assert v.texts(a) == ["last", "first", "mover"] and v.find(a, "last") == last
    v.quiet()


@pytest.mark.parametrize("paper", [False, True])
def test_a_sub_page_renamed_on_disk_while_the_page_it_is_under_is_trashed_takes_the_name(vault, paper):
    """The rename came before the change that brought the trashed
    page back, so the sub-page, trashed with it, was not named by its file,
    and the name was lost. The move waits for its page."""
    v = vault()
    if paper:
        v.write("Paper.pdf", _pdf("paper text"), age=10)
    v.write("Paper.md", "alpha\n", age=10)
    v.write("kid.md", "- one\n", age=10)
    v.write("other.md", "other\n", age=10)
    v.sync()
    parent, kid = v.page_of("Paper.md"), v.page_of("kid.md")
    v.edit(kid, {"op": "set", "id": kid, "props": {"parent_page": parent}})
    v.sync()
    with connect_pages_db(v.ws) as conn:
        trash_page(v.ws, conn, parent, actor=v.user)
    kid_md = next(p for p, r in v.rows().items() if r["page_id"] == kid)
    v.write("Paper.md", "alpha beta\n", age=10)
    os.rename(v.root / kid_md, v.root / os.path.dirname(kid_md) / "renamed.md")
    for _ in range(3):
        v.sync(ok=False)
    assert v.state(parent) == v.state(kid) == "live" and v.remote.title_of(kid) == "renamed"
    v.quiet()


def test_a_problem_of_a_papers_note_renamed_only_in_case_is_reported_under_the_note(vault):
    """A paper's note renamed only in case sorted after its PDF, and
    the reason it could not be written was reported under the PDF's path."""
    v = vault()
    v.write("Paper.pdf", _pdf("a paper"), age=30)
    v.write("Paper.md", "alpha\n", age=10)
    v.write("other.md", "other\n", age=10)
    v.sync()
    page = v.page_of("Paper.md")
    v.write("Paper.md", "$$\nx^2\n$$\n\nalpha\n", age=10)
    v.sync()
    v.edit(page, set_(v.find(page, "$$\nx^2\n$$"), "$$\nx^2\n$$ and words after"))
    os.rename(v.root / "Paper.md", v.root / "pAPER.md")
    status = v.sync(ok=False)
    assert [k["path"] for k in status["kept"]] == ["pAPER.md"] and "$$" in status["kept"][0]["why"], status


def test_a_title_with_outer_spaces_reads_back_as_it_was_written(vault):
    """Pull writes such a title quoted, which keeps its spaces, and
    push read it stripped: the file's title and the page's differed, and an
    edit of it on disk changed the page's spacing."""
    assert vault_sync._front_props({"title": "  spaced  9  "}, "stem")[0] == "  spaced  9  "
    assert vault_sync._front_props({"title": "   "}, "stem")[0] == "stem"
    v = vault()
    v.write("n.md", "---\ntitle: First\n---\nbody\n", age=10)
    v.sync()
    page = v.page_of("n.md")
    v.edit(page, {"op": "set", "id": page, "content": "  spaced  9  "})
    v.sync()
    assert 'title: "  spaced  9  "' in v.text("n.md")
    v.write("n.md", '---\ntitle: "  spaced  10  "\n---\nbody\n', age=10)
    v.sync()
    assert v.info(page).title == "  spaced  10  "
    v.quiet()


# --- size --------------------------------------------------------------------------------------------

def test_a_vault_of_two_thousand_small_notes(vault, capsys):
    """Timing, printed; the bounds are generous."""
    v = vault()
    for n in range(2000):
        v.write(f"d{n % 20}/note {n}.md", f"# Note {n}\n\nsome text of note {n}\n\n- a point\n- another\n")
    started = time.perf_counter()
    v.sync()
    first = time.perf_counter() - started
    started = time.perf_counter()
    status = v.sync()
    second = time.perf_counter() - started
    assert v.page_count() == 2000 and status["counts"]["sent"] == 0
    v.write("d3/note 3.md", "# Note 3\n\nsome text of note 3, edited\n\n- a point\n- another\n")
    started = time.perf_counter()
    v.sync()
    third = time.perf_counter() - started
    with capsys.disabled():
        print(f"\n2,000 notes: first round {first:.1f} s, a quiet one {second:.1f} s, one note changed {third:.1f} s")
    assert first < 600 and second < 120 and third < 120
