"""gamma/vault_disk.py: a synced vault's directory as files, with real files in
``tmp_path``: the scan, moves, the write that replaces a note, the trash
(docs/dev/synced_vaults.md "A round", "Rules that keep files safe")."""

import os
import stat
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from gamma import vault_disk
from gamma.vault_disk import (MARKER, TRASH, Found, Known, Offline, Stopped, check_marker, clean_temps, free_name,
                              make_marker, move, moved, pair_moves, pdf_id, purge, racy, read_marker, refusal,
                              replace_note, scan, similarity, trash_path, walk, write_new)

WINDOWS = sys.platform == "win32"


def vault(tmp_path, **files) -> tuple[Path, str]:
    """A vault in ``tmp_path / "v"`` holding ``files`` (``a__b_md`` is
    ``a/b.md``, a value of bytes or text)."""
    root = tmp_path / "v"
    marker = make_marker(root)
    for name, data in files.items():
        write(root, name.replace("__", "/").replace("_md", ".md").replace("_pdf", ".pdf"), data)
    return root, marker["id"]


def write(root, rel, data):
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data if isinstance(data, bytes) else data.encode())
    return path


def pdf(trailer=b"/ID [<ABCDEF0123><99887766>]", head=b""):
    return b"%PDF-1.7\n" + head + b"\n1 0 obj << >> endobj\ntrailer\n<< /Size 2 " + trailer + b" >>\n%%EOF\n"


# --- the marker -----------------------------------------------------------------

def test_a_marker_is_made_once_in_a_hidden_directory(tmp_path):
    root = tmp_path / "new vault"
    marker = make_marker(root, notes_folder="Notes", ignore=["*.tmp"])
    assert read_marker(root) == marker and marker["format"] == "gamma-vault-1" and len(marker["id"]) == 32
    assert marker["notes_folder"] == "Notes" and marker["ignore"] == ["*.tmp"]
    if WINDOWS:
        assert os.stat(root / ".gamma").st_file_attributes & stat.FILE_ATTRIBUTE_HIDDEN
    with pytest.raises(FileExistsError):
        make_marker(root)
    assert check_marker(root, marker["id"]) == marker
    assert [p.name for p in (root / ".gamma").iterdir()] == ["vault.json"]


def test_a_directory_that_is_not_the_vault_stops_it(tmp_path):
    root, vault_id = vault(tmp_path)
    with pytest.raises(Stopped, match="another vault"):
        check_marker(root, "someone else")
    (root / MARKER).write_text("{not json")
    with pytest.raises(Stopped, match="cannot be read"):
        check_marker(root, vault_id)
    (root / MARKER).unlink()
    assert read_marker(root) is None
    with pytest.raises(Stopped, match="no vault marker"):
        check_marker(root, vault_id)


def test_a_root_that_is_gone_is_offline_and_never_made_again(tmp_path):
    root, vault_id = vault(tmp_path, a_md="a")
    gone = tmp_path / "unplugged"
    with pytest.raises(Offline):
        scan(gone, vault_id)
    gone.mkdir()   # an empty mount point
    with pytest.raises(Stopped):
        scan(gone, vault_id)
    with pytest.raises(Offline):
        write_new(gone, "a.md", b"x")
    with pytest.raises(Offline):
        move(gone, "a.md", ".gamma/trash/x/a.md")
    assert list(gone.iterdir()) == []


# --- the scan -------------------------------------------------------------------

def test_a_scan_lists_notes_and_papers_and_counts_the_rest(tmp_path):
    root, vault_id = vault(tmp_path, a_md="a", sub__b_md="b", sub__deep__p_pdf=pdf())
    write(root, "x.markdown", b"x")
    write(root, "pic.PNG", b"png")
    write(root, "data.csv", b"1,2")
    write(root, "empty/.keep", b"")
    found = scan(root, vault_id)
    assert sorted(found.files) == ["a.md", "sub/b.md", "sub/deep/p.pdf", "x.markdown"]
    assert found.files["sub/deep/p.pdf"].kind == "pdf" and found.files["a.md"].kind == "notes"
    st = os.stat(root / "a.md")
    assert found.files["a.md"] == Found("notes", st.st_size, st.st_mtime_ns, str(st.st_ino))
    assert sorted(found.dirs) == ["empty", "sub", "sub/deep"] and found.dirs["sub"] == str(os.stat(root / "sub").st_ino)
    assert (found.pictures, found.untracked, found.refused, found.temps) == (1, 2, [], [])


def test_a_directory_not_yet_a_vault_is_walked_and_nothing_written(tmp_path):
    """The preview: what a directory holds, before it has a marker."""
    root = tmp_path / "plain"
    write(root, "a.md", "a")
    write(root, "p.pdf", pdf())
    before = sorted(p.name for p in root.rglob("*"))
    found = walk(root, ["p.*"])
    assert list(found.files) == ["a.md"] and sorted(p.name for p in root.rglob("*")) == before


def test_a_scan_leaves_out_what_is_no_one_s_note(tmp_path):
    root, vault_id = vault(tmp_path, a_md="a")
    for rel in (".obsidian/workspace.md", ".git/x.md", ".trash/old.md", "node_modules/pkg/readme.md",
                "build/out.md", "drafts/keep.md", "drafts/skip.tmp.md", "~$report.md", ".~lock.a.md#", "._a.pdf"):
        write(root, rel, "x")
    write(root, ".gamma-1234.tmp", "x")
    write(root, "sub/b.pdf.gamma-sync-part", "x")
    (root / MARKER).write_text((root / MARKER).read_text().replace('"ignore": []', '"ignore": ["build/", "*.tmp.md"]'))
    found = scan(root, vault_id)
    assert sorted(found.files) == ["a.md", "drafts/keep.md"]
    assert sorted(found.temps) == [".gamma-1234.tmp", "sub/b.pdf.gamma-sync-part"]


def test_a_cloud_placeholder_is_listed_and_never_read(tmp_path):
    """A OneDrive placeholder hangs on read, an iCloud stub
    stands for the file it names."""
    root, vault_id = vault(tmp_path, a_md="a")
    write(root, "sub/.Paper.pdf.icloud", b"bplist00")
    write(root, "._underscore.md.icloud", b"bplist00")   # the stub of _underscore.md, no ._ companion
    if WINDOWS:
        import ctypes
        write(root, "online.md", "in the cloud")
        assert ctypes.windll.kernel32.SetFileAttributesW(str(root / "online.md"), vault_disk.OFFLINE)
    found = scan(root, vault_id)
    assert found.files["sub/Paper.pdf"] == Found("pdf", 0, 0, "", True)
    assert found.files["_underscore.md"].placeholder and not found.files["a.md"].placeholder
    if WINDOWS:
        assert found.files["online.md"].placeholder and found.files["online.md"].file_id == ""


@pytest.mark.skipif(not WINDOWS, reason="junctions are Windows'")
def test_a_junction_is_reported_and_never_followed(tmp_path):
    """A junction is no symlink, and is a directory unfollowed."""
    import _winapi
    root, vault_id = vault(tmp_path, a_md="a")
    elsewhere = tmp_path / "elsewhere"
    write(elsewhere, "secret.md", "not the vault's")
    _winapi.CreateJunction(str(elsewhere), str(root / "j"))
    found = scan(root, vault_id)
    assert sorted(found.files) == ["a.md"] and "j" not in found.dirs
    assert found.refused == [("j", vault_disk._LINK)]


def test_a_symlink_is_reported_and_never_followed(tmp_path):
    root, vault_id = vault(tmp_path, a_md="a")
    write(tmp_path, "outside.md", "x")
    try:
        os.symlink(tmp_path / "outside.md", root / "link.md")
        os.symlink(tmp_path, root / "dirlink", target_is_directory=True)
    except OSError:
        pytest.skip("this account may not make symlinks")
    found = scan(root, vault_id)
    assert sorted(found.files) == ["a.md"]
    assert sorted(found.refused) == [("dirlink", vault_disk._LINK), ("link.md", vault_disk._LINK)]


class Entry:
    """A ``DirEntry`` for names this filesystem would not hold."""

    def __init__(self, directory, name, *, folder=False, gone=False):
        self.name, self.path, self.folder, self.gone = name, os.path.join(directory, name), folder, gone

    def is_symlink(self):
        return False

    def is_junction(self):
        return False

    def is_dir(self, follow_symlinks=True):
        return self.folder

    def is_file(self, follow_symlinks=True):
        return not self.folder

    def stat(self, follow_symlinks=True):
        if self.gone:
            raise FileNotFoundError(self.path)
        return SimpleNamespace(st_size=5, st_mtime_ns=0, st_file_attributes=0, st_flags=0)

    def inode(self):
        if self.gone:
            raise FileNotFoundError(self.path)
        return 77


def test_names_windows_refuses_and_names_alike_but_for_case_are_left(tmp_path, monkeypatch):
    root, vault_id = vault(tmp_path)
    listing = [Entry(root, n) for n in ("CON.md", "a?.md", "trail. .md ", "Same.md", "same.md", "fine.md")]
    listing += [Entry(root, "Dir", folder=True), Entry(root, "dir", folder=True), Entry(root, "vanished.md", gone=True)]
    monkeypatch.setattr(vault_disk, "_entries", lambda directory: listing if directory == root else [])
    found = scan(root, vault_id)
    assert list(found.files) == ["fine.md"] and found.dirs == {}
    assert sorted(path for path, _why in found.refused) == ["CON.md", "Dir", "Same.md", "a?.md", "dir", "same.md",
                                                            "trail. .md "]
    assert dict(found.refused)["same.md"] == vault_disk._CASE


def test_refusal_names_what_windows_cannot_hold():
    assert refusal("CON") and refusal("nul.txt") and refusal("Lpt1.tar.gz") and refusal("com¹.md")
    assert all(refusal(name) for name in ("a:b.md", 'say "hi".md', "tab\there.md", "dot.", "sp "))
    assert not refusal("console.md") and not refusal("conference notes.md") and not refusal(".hidden.md")


def test_any_error_of_the_walk_is_offline_never_a_partial_scan(tmp_path, monkeypatch):
    root, vault_id = vault(tmp_path, a_md="a", sub__b_md="b")
    real = vault_disk._entries

    def failing(directory):
        if Path(directory).name == "sub":
            raise PermissionError(13, "denied", str(directory))
        return real(directory)

    monkeypatch.setattr(vault_disk, "_entries", failing)
    with pytest.raises(Offline, match="sub"):
        scan(root, vault_id)


def test_a_marker_gone_by_the_end_of_the_walk_is_offline(tmp_path, monkeypatch):
    """A disk pulled out mid-walk reads as files removed."""
    root, vault_id = vault(tmp_path, a_md="a")
    real = vault_disk._entries

    def unplugging(directory):
        (root / MARKER).unlink(missing_ok=True)
        return real(directory)

    monkeypatch.setattr(vault_disk, "_entries", unplugging)
    with pytest.raises(Offline, match="marker"):
        scan(root, vault_id)


# --- changed, and moved ---------------------------------------------------------------

def test_a_time_near_the_read_counts_as_changed():
    """Gamma writes and the person saves the same size in one
    tick of a coarse clock; only the hash can tell."""
    row = {"size": 10, "mtime_ns": 1_000 * 10**9, "file_id": "7", "read_at": 1_001.5}
    assert racy(row["mtime_ns"], row["read_at"]) and moved(row, Found("notes", 10, 1_000 * 10**9, "7"))
    row["read_at"] = 1_002.5
    assert not racy(row["mtime_ns"], row["read_at"]) and not moved(row, Found("notes", 10, 1_000 * 10**9, "7"))
    assert moved(row, Found("notes", 11, 1_000 * 10**9, "7")) and moved(row, Found("notes", 10, 1, "7"))
    assert moved(row, Found("notes", 10, 1_000 * 10**9, "8")) and not moved(row, Found("notes", 10, 1_000 * 10**9, ""))


def test_a_pdfs_id_is_its_trailers_first_half(tmp_path):
    assert pdf_id(write(tmp_path, "a.pdf", pdf())) == "abcdef0123"
    later = pdf() + b"trailer\n<< /Prev 9 /ID [<1111> <2222>] >>\n%%EOF\n"
    assert pdf_id(write(tmp_path, "b.pdf", later)) == "1111"
    linearized = pdf(trailer=b"", head=b"trailer << /ID [ <AB CD> <00> ] >>" + b" " * (vault_disk.ID_BYTES + 10))
    assert pdf_id(write(tmp_path, "c.pdf", linearized)) == "abcd"
    assert pdf_id(write(tmp_path, "d.pdf", pdf(trailer=b""))) == ""
    assert pdf_id(write(tmp_path, "e.pdf", pdf(trailer=b"/ID [(abc) (def)]"))) == b"abc".hex()


def test_similarity_is_by_words():
    text = "the quick brown fox jumps over the lazy dog " * 5
    assert similarity(text, text) == 1.0
    assert similarity(text, text.replace("lazy", "sleepy", 2)) >= 0.6
    assert similarity(text, "something else entirely") == 0.0 and similarity("", text) == 0.0


def note(path, text, file_id="", **kw):
    return Known(path, "notes", size=len(text), text=text, file_id=file_id, **kw)


def test_a_file_id_is_evidence_only_with_its_size_or_its_text(tmp_path):
    """Ext4 hands a freed inode to the next file made."""
    same = [note("a.md", "same words here", "5")], [note("b.md", "same words here", "5")]
    assert pair_moves(*same) == ({"b.md": "a.md"}, False)
    edited = [note("a.md", "same words here and a few more", "5")], [note("b.md", "same words here and a few more "
                                                                                 "of them, typed later", "5")]
    assert pair_moves(*edited)[0] == {"b.md": "a.md"}
    reused = [note("a.md", "old note about one thing", "5")], [note("b.md", "a new note on something else entirely", "5")]
    assert pair_moves(*reused) == ({}, False)


def test_with_the_same_file_id_a_short_note_renamed_and_edited_is_one_note():
    """``buy more coffee`` saved as ``buy more tea
    today`` under a new name (0.57 alike by words) was a new page."""
    assert similarity("buy more coffee", "buy more tea today") < vault_disk.MOVE_SIMILAR
    assert pair_moves([note("Idea.md", "buy more coffee", "7")], [note("Shopping.md", "buy more tea today", "7")])[0] \
        == {"Shopping.md": "Idea.md"}
    assert pair_moves([note("Idea.md", "buy more coffee", "")], [note("Shopping.md", "buy more tea today", "")])[0] == {}


def test_a_file_comes_back_to_its_page_where_it_lived_or_by_its_file_id_never_by_a_hash_elsewhere():
    """A tombstone matched any equal hash, so a daily
    note made from a template brought back another day's trashed page."""
    tombs = [Known("2026-11-01.md", "notes", 30, "h-template", "41", text="## Tasks"),
             note("N.md", "first line second line", "42")]
    assert vault_disk.came_back(tombs, [Known("2026-11-07.md", "notes", 30, "h-template", "99", text="## Tasks")]) == {}
    assert vault_disk.came_back(tombs, [Known("2026-11-01.md", "notes", 30, "h-template", "99", text="## Tasks")]) == \
        {"2026-11-01.md": 0}                                            # a branch switched back
    assert vault_disk.came_back(tombs, [note("N.md", "first line second line third line", "77")]) == {"N.md": 1}
    assert vault_disk.came_back(tombs, [note("Elsewhere.md", "first line second line", "42")]) == {"Elsewhere.md": 1}
    assert vault_disk.came_back(tombs, [note("Elsewhere.md", "something new entirely here", "42")]) == {}
    assert vault_disk.came_back(tombs, [note("n.md", "a different note under the old name", "77")]) == {}


def test_nothing_is_read_or_written_out_of_the_vault_by_a_path(tmp_path):
    root, _id = vault(tmp_path, a__b_md="b")
    assert vault_disk.path(root, "a/b.md") == root / "a" / "b.md" and vault_disk.read(root, "a/b.md") == b"b"
    assert vault_disk.lstat(root, "a/gone.md") is None and vault_disk.path(root, "new/dir/c.md") == root / "new/dir/c.md"
    for rel in ("../x.md", "a/../../x.md", "/x.md", "C:/x.md", ""):
        with pytest.raises(vault_disk.Outside):
            vault_disk.path(root, rel)


@pytest.mark.skipif(not WINDOWS, reason="junctions are Windows'")
def test_nothing_is_read_or_written_below_a_junction(tmp_path):
    """The scan refused the junction, pull wrote and the
    page round read through it."""
    import _winapi
    root, _id = vault(tmp_path, a_md="a")
    elsewhere = tmp_path / "elsewhere"
    write(elsewhere, "x.md", "SECRET")
    _winapi.CreateJunction(str(elsewhere), str(root / "Sub"))
    for call in (lambda: vault_disk.read(root, "Sub/x.md"), lambda: replace_note(root, "Sub/x.md", b"gamma"),
                 lambda: write_new(root, "Sub/new/y.md", b"gamma"), lambda: vault_disk.lstat(root, "Sub/x.md"),
                 lambda: move(root, "a.md", "Sub/a.md")):
        with pytest.raises(vault_disk.Outside):
            call()
    assert sorted(os.listdir(elsewhere)) == ["x.md"] and (elsewhere / "x.md").read_bytes() == b"SECRET"


def test_a_markers_notes_folder_outside_the_vault_is_none(tmp_path):
    root = tmp_path / "v"
    (root / ".gamma").mkdir(parents=True)
    for given, kept in (("../Outside", ""), ("C:/Outside", ""), ("/abs", ""), ("a/../../b", ""), ("Notes/", "Notes"),
                        ("Notes\\Sub", "Notes/Sub")):
        (root / MARKER).write_text(f'{{"format": "gamma-vault-1", "id": "x", "notes_folder": "{given.replace(chr(92), "/")}"}}')
        assert read_marker(root)["notes_folder"] == kept, given


def test_a_name_pull_writes_is_one_the_scan_lists():
    assert vault_disk.listed("Plan.md") and not vault_disk.listed("~$Plan.md") and not vault_disk.listed(".gamma-x.tmp")
    assert vault_disk.listed("node_modules.md") and not vault_disk.listed("node_modules", directory=True)
    assert not vault_disk.listed(".hidden", directory=True) and vault_disk.listed("_node_modules", directory=True)


def test_a_free_name_is_free_among_the_rows_too(tmp_path):
    """A file missing from the disk holds its path
    until two scans agree; a name the disk alone gave hit that row."""
    root, _id = vault(tmp_path, B_md="b")
    assert free_name(root, "A.md") == "A.md" and free_name(root, "A.md", taken={"a.md": {}}) == "A 2.md"
    assert free_name(root, "A.md", own="A.md", taken={"A.md": {}}) == "A.md"


def test_a_write_names_the_file_it_puts_there_before_the_rename(tmp_path):
    """``ready`` hears the stat the rename will put at the path, while the old
    bytes are still there: a journal written then can tell, after a crash, that
    the write landed though the file was edited since."""
    root, _id = vault(tmp_path, a_md="before")
    heard = []
    written = replace_note(root, "a.md", b"after", ready=lambda w: heard.append((w, (root / "a.md").read_bytes())))
    (first, then), = heard
    assert then == b"before" and (first.size, first.mtime_ns, first.file_id) == (written.size, written.mtime_ns,
                                                                                  written.file_id)
    assert str(os.stat(root / "a.md").st_ino) == written.file_id
    made = []
    new = write_new(root, "n/new.md", b"new", ready=lambda w: made.append(w.file_id))
    assert made == [new.file_id] == [str(os.stat(root / "n/new.md").st_ino)]


def test_a_hash_is_evidence_only_when_no_other_file_has_it():
    olds = [Known("a.md", "notes", 4, "h1"), Known("empty.md", "notes", 0, "h0")]
    moved_one = [Known("x/a.md", "notes", 4, "h1"), Known("y.md", "notes", 0, "h0")]
    assert pair_moves(olds, moved_one)[0] == {"x/a.md": "a.md"}
    copies = [Known("t1.md", "notes", 4, "h1"), Known("t2.md", "notes", 4, "h1")]   # a template copied twice
    assert pair_moves(olds, copies)[0] == {}


def test_a_pdf_saved_by_another_reader_is_found_by_its_id():
    old = Known("papers/a.pdf", "pdf", 100, "h1", pdf_id="abc")
    assert pair_moves([old], [Known("read/a.pdf", "pdf", 140, "h2", pdf_id="abc")])[0] == {"read/a.pdf": "papers/a.pdf"}
    twins = [Known("1.pdf", "pdf", 140, "h2", pdf_id="abc"), Known("2.pdf", "pdf", 150, "h3", pdf_id="abc")]
    assert pair_moves([old], twins)[0] == {}


def test_a_note_moved_and_edited_is_found_by_its_text_never_by_its_name():
    text = "Notes on the meeting: we agreed to ship on Friday and to write the docs first."
    gone = [note("a/README.md", text), note("a/index.md", "a list of links to other notes in the vault")]
    found = [note("b/README.md", "an unrelated readme for another project, with other words"),
             note("archive/meeting.md", text + " Later: it slipped a week.")]
    assert pair_moves(gone, found) == ({"archive/meeting.md": "a/README.md"}, False)


def test_the_name_breaks_a_tie():
    text = "one two three four five six seven eight nine ten"
    gone = [note("old/plan.md", text)]
    assert pair_moves(gone, [note("x/other.md", text), note("y/plan.md", text)])[0] == {"y/plan.md": "old/plan.md"}


def test_two_names_swapped_are_told_by_their_file_ids():
    """A swap leaves no path removed or added, so both read
    as changed and each would take the other's edits."""
    gone = [note("a.md", "the first note's words", "1"), note("b.md", "the second note's words", "2")]
    found = [note("a.md", "the second note's words", "2"), note("b.md", "the first note's words", "1")]
    assert pair_moves(gone, found)[0] == {"a.md": "b.md", "b.md": "a.md"}
    in_place = [note("a.md", "edited where it is", "1")], [note("a.md", "edited where it is, again", "1")]
    assert pair_moves(*in_place)[0] == {}


def test_the_search_by_text_stops_at_its_cap():
    gone = [note(f"old/{k}.md", f"note number {k} with words") for k in range(20)]
    found = [note(f"new/{k}x.md", f"something {k} completely different here") for k in range(11)]
    assert len(gone) * len(found) > vault_disk.MOVE_COMPARES
    assert pair_moves(gone, found) == ({}, True)


def test_a_rename_on_disk_keeps_the_file_id_a_scan_reads(tmp_path):
    root, vault_id = vault(tmp_path, a_md="the note")
    before = scan(root, vault_id).files["a.md"]
    move(root, "a.md", "sub/renamed.md")
    after = scan(root, vault_id).files["sub/renamed.md"]
    assert after.file_id == before.file_id
    gone, found = Known("a.md", "notes", before.size, file_id=before.file_id), Known("sub/renamed.md", "notes",
                                                                                     after.size, file_id=after.file_id)
    assert pair_moves([gone], [found])[0] == {"sub/renamed.md": "a.md"}


# --- writing ----------------------------------------------------------------------

def leftovers(root):
    return sorted(p.name for p in root.rglob(".gamma-*"))


def test_replace_note_writes_and_says_what_it_replaced(tmp_path):
    root, _id = vault(tmp_path, a_md="the cat sat\n")
    written = replace_note(root, "a.md", b"the dog sat\n")
    st = os.stat(root / "a.md")
    assert (root / "a.md").read_bytes() == b"the dog sat\n" and written.replaced == b"the cat sat\n"
    assert (written.size, written.mtime_ns, written.file_id) == (st.st_size, st.st_mtime_ns, str(st.st_ino))
    assert leftovers(root) == []


def test_replace_note_with_no_hard_links_reads_the_old_bytes_first(tmp_path, monkeypatch):
    root, _id = vault(tmp_path, a_md="before")
    monkeypatch.setattr(vault_disk.os, "link", lambda *a: (_ for _ in ()).throw(OSError(1, "not here")))
    assert replace_note(root, "a.md", b"after").replaced == b"before"
    assert (root / "a.md").read_bytes() == b"after" and leftovers(root) == []


@pytest.mark.parametrize("links", [True, False])
def test_replace_note_holds_what_it_replaced_until_its_caller_drops_it(tmp_path, monkeypatch, links):
    """The hard link went before the round's history
    had its bytes, and a crash in between lost a person's save made in the
    moment before the rename. With ``hold`` the copy stays, named before it is
    made, a link or, with none, a copy."""
    root, _id = vault(tmp_path, sub__a_md="before")
    if not links:
        monkeypatch.setattr(vault_disk.os, "link", lambda *a: (_ for _ in ()).throw(OSError(1, "not here")))
    heard = []
    written = replace_note(root, "sub/a.md", b"after", ready=heard.append, hold=True)
    assert heard[0].kept == written.kept and written.kept.startswith("sub/.gamma-") and written.replaced == b"before"
    assert (root / written.kept).read_bytes() == b"before" and (root / "sub/a.md").read_bytes() == b"after"
    vault_disk.drop_temp(root, written.kept)
    vault_disk.drop_temp(root, "sub/a.md")              # no temporary name: never removed
    assert leftovers(root) == [] and (root / "sub/a.md").read_bytes() == b"after"


def test_replace_note_never_brings_back_a_note_that_went(tmp_path):
    root, _id = vault(tmp_path)
    with pytest.raises(FileNotFoundError):
        replace_note(root, "gone.md", b"x")
    assert not (root / "gone.md").exists() and leftovers(root) == []


@pytest.mark.skipif(not WINDOWS, reason="only Windows refuses a rename over an open file")
def test_a_note_another_program_holds_is_refused_never_taken_for_written(tmp_path, monkeypatch):
    """``storage._rename_over`` took a file of the same size for
    the write done, and the base said dog where the file said cat."""
    monkeypatch.setattr(vault_disk, "REPLACE_RETRY_S", 0.01)
    root, _id = vault(tmp_path, a_md="the cat sat\n")
    with open(root / "a.md", "rb"):
        with pytest.raises(PermissionError):
            replace_note(root, "a.md", b"the dog sat\n")
    assert (root / "a.md").read_bytes() == b"the cat sat\n" and leftovers(root) == []


@pytest.mark.skipif(not WINDOWS, reason="only Windows refuses a rename over an open file")
def test_a_note_held_for_a_moment_is_written_on_a_later_try(tmp_path):
    root, _id = vault(tmp_path, a_md="the cat sat\n")
    reader = open(root / "a.md", "rb")
    threading.Timer(0.25, reader.close).start()
    written = replace_note(root, "a.md", b"the dog sat\n")
    assert (root / "a.md").read_bytes() == b"the dog sat\n" and written.replaced == b"the cat sat\n"
    assert leftovers(root) == []


def test_write_new_never_replaces_a_file_that_took_the_name(tmp_path):
    root, _id = vault(tmp_path, taken_md="mine")
    written = write_new(root, "deep/new/page.md", b"# Page\n")
    assert (root / "deep/new/page.md").read_bytes() == b"# Page\n"
    assert written.file_id == str(os.stat(root / "deep/new/page.md").st_ino) and written.replaced is None
    with pytest.raises(FileExistsError):
        write_new(root, "taken.md", b"gamma's")
    if WINDOWS:
        with pytest.raises(FileExistsError):
            write_new(root, "TAKEN.md", b"gamma's")
    assert (root / "taken.md").read_bytes() == b"mine" and leftovers(root) == []


def test_stale_temps_go_and_a_write_going_on_stays(tmp_path):
    root, vault_id = vault(tmp_path)
    old, young = write(root, "sub/.gamma-old.tmp", "x"), write(root, ".gamma-young.tmp", "y")
    os.utime(old, (time.time() - 3600, time.time() - 3600))
    write(root, "~$theirs.md", "z")
    assert clean_temps(root, scan(root, vault_id).temps) == 1
    assert not old.exists() and young.exists() and (root / "~$theirs.md").exists()


# --- names, moves and the trash ---------------------------------------------------------

def test_the_trash_holds_a_file_at_its_path_and_gives_it_back_beside_a_new_one(tmp_path):
    """The trash path is known before the move, and a
    restore onto a taken path takes the next free name."""
    root, vault_id = vault(tmp_path, notes__a_md="old a")
    when = vault_disk.stamp(0)
    assert when == "19700101-000000"
    there = trash_path(root, "notes/a.md", when)
    assert there == f"{TRASH}/{when}/notes/a.md"
    move(root, "notes/a.md", there)
    assert (root / there).read_text() == "old a" and "notes/a.md" not in scan(root, vault_id).files
    write(root, "notes/a.md", "a new a")
    back = free_name(root, "notes/a.md")
    assert back == "notes/a 2.md"
    move(root, there, back)
    assert (root / "notes/a 2.md").read_text() == "old a" and (root / "notes/a.md").read_text() == "a new a"
    write(root, f"{TRASH}/{when}/x.md", "")
    assert trash_path(root, "x.md", when) == f"{TRASH}/{when}/x 2.md"
    assert trash_path(root, "y.md", when, taken={f"{TRASH}/{when}/Y.md"}) == f"{TRASH}/{when}/y 2.md"   # a tombstone's


def test_the_trash_says_when_it_took_a_file():
    """The stamp of its directory there: what the sweep's thirty days count from."""
    now = 1_790_000_123
    assert vault_disk.trashed_at(f"{TRASH}/{vault_disk.stamp(now)}/notes/a 2.md") == now
    assert vault_disk.trashed_at("notes/a.md") is None and vault_disk.trashed_at(f"{TRASH}/a.md") is None
    assert vault_disk.trashed_at(f"{TRASH}/not-a-stamp/a.md") is None


def test_a_text_reads_nearer_the_write_it_holds_than_the_base_before_it():
    """Round 3: a pull's write, then a person's safe save (a new file id) before
    the restart: the text still says which base stands."""
    base, wrote = "x\n", "x\n\ny from gamma\n"
    assert vault_disk.nearer("x\n\ny from gamma\n\nz typed on disk\n", wrote, base)
    assert not vault_disk.nearer("x\n\nz typed on disk\n", wrote, base)       # the write never landed
    assert not vault_disk.nearer(base, wrote, base) and not vault_disk.nearer("x\n\nz\n", "", "")


def test_the_markers_file_id_tells_a_copy_of_the_vault(tmp_path):
    root, _id = vault(tmp_path)
    copy = tmp_path / "copy"
    copy.mkdir()
    (copy / ".gamma").mkdir()
    (copy / MARKER).write_bytes((root / MARKER).read_bytes())
    assert vault_disk.marker_file(root) and vault_disk.marker_file(copy) not in ("", vault_disk.marker_file(root))
    assert vault_disk.marker_file(tmp_path / "none") == ""


def test_a_rename_that_only_changes_case_is_its_own_name(tmp_path):
    root, _id = vault(tmp_path, Foo_md="foo")
    assert free_name(root, "foo.md") == "foo 2.md"      # the one naming rule ignores case, whatever the disk does
    assert free_name(root, "foo.md", own="Foo.md") == "foo.md"
    move(root, "Foo.md", "foo.md")
    assert [p.name for p in root.iterdir() if p.suffix == ".md"] == ["foo.md"]


def test_a_move_never_lands_on_another_file(tmp_path):
    root, _id = vault(tmp_path, a_md="a", b_md="b")
    with pytest.raises(FileExistsError):
        move(root, "a.md", "b.md")
    assert (root / "a.md").read_text() == "a" and (root / "b.md").read_text() == "b"
    move(root, "a.md", "sub/dir/a.md")
    move(root, "sub", "moved")
    assert (root / "moved/dir/a.md").read_text() == "a"


def test_purge_takes_only_what_is_in_the_trash(tmp_path):
    root, _id = vault(tmp_path, a_md="a")
    there = trash_path(root, "x/y/a.md", "20260101-000000")
    move(root, "a.md", there)
    with pytest.raises(ValueError):
        purge(root, "a.md")
    with pytest.raises(ValueError):
        purge(root, f"{TRASH}/../vault.json")
    purge(root, there)
    assert (root / TRASH).is_dir() and list((root / TRASH).iterdir()) == []
    assert (root / MARKER).is_file()
