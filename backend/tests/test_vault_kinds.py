"""gamma/vault/kinds.py: the class registry a synced vault sorts its files by
(docs/dev/synced_vaults.md "The classes"): a class decided by the name alone,
a row's and a page's class, and what each class says of its files."""

import json
from pathlib import Path

import pytest

from gamma.documents import formats
from gamma.vault import disk, kinds, sync

NAMES = json.loads((Path(__file__).resolve().parents[2] / "tests" / "shared" / "formats.json").read_text(encoding="utf-8"))["names"]
IMAGES = {ext for ext, row in formats.EXTENSIONS.items() if row.format == "image"}


def _kind_before(name: str):
    """The rule the engine sorted files by before the registry (``vault_disk.kind_of``):
    the extension's word, pictures by the serving table's image rows."""
    ext = name.rsplit("/", 1)[-1]
    ext = ext[ext.rfind("."):].lower() if ext.rfind(".") > 0 else ""
    if ext in (".md", ".markdown"):
        return kinds.NOTES
    if ext == ".pdf":
        return kinds.PDF
    return kinds.PICTURE if ext in formats.IMAGE_MEDIA_TYPES else None


@pytest.mark.parametrize("name", [*(f"x{ext}" for ext in formats.EXTENSIONS), *(c["name"] for c in NAMES),
                                  "x.JPEG", "x.Md", "x.exe", "x.tar.gz", "x", ".md", "x.md "])
def test_a_class_is_decided_by_the_name_as_the_old_rule_decided_it(name):
    for path in (name, f"a/b/{name}"):
        cls = kinds.class_of(path)
        assert cls is _kind_before(path), path


def test_the_picture_class_is_the_formats_tables_image_rows():
    assert {ext for ext in formats.EXTENSIONS if kinds.class_of(f"x{ext}") is kinds.PICTURE} == IMAGES == set(formats.IMAGE_MEDIA_TYPES)
    assert {ext for ext in formats.EXTENSIONS if kinds.class_of(f"x{ext}") is kinds.NOTES} == {".md", ".markdown"}
    assert {ext for ext in formats.EXTENSIONS if kinds.class_of(f"x{ext}") is kinds.PDF} == {".pdf"}


def test_each_class_names_its_rows_kind_and_what_its_files_are_to_a_round():
    assert list(kinds.CLASSES) == ["notes", "pdf", "picture", "file"]
    for cls in kinds.CLASSES.values():
        assert kinds.by_kind(cls.name) is cls
    assert kinds.by_kind("") is None and kinds.by_kind("image") is None
    flags = {cls.name: (cls.compared, cls.in_place, cls.in_library, cls.makes_pages) for cls in kinds.CLASSES.values()}
    assert flags == {"notes": (True, False, True, True), "pdf": (True, True, True, True),
                     "picture": (False, False, False, False), "file": (True, True, False, True)}


def test_a_pages_class_is_its_pdf_else_its_notes():
    notes, pdf = ("n.md", {"kind": "notes"}), ("p.pdf", {"kind": "pdf"})
    assert kinds.main({"notes": notes, "pdf": pdf}) is kinds.PDF
    assert kinds.main({"pdf": pdf}) is kinds.PDF and kinds.main({"notes": notes}) is kinds.NOTES
    assert kinds.main({}) is None and kinds.main({"picture": ("f.png", {"kind": "picture"})}) is None
    assert kinds.main({"file": ("a.tex", {"kind": "file"})}) is kinds.FILE
    assert kinds.main({"file": ("a.tex", {"kind": "file"}), "notes": notes}) is kinds.NOTES


def test_what_a_class_reads_off_a_row_and_a_read():
    row = {"kind": "pdf", "size": 10, "hash": "h" * 24, "file_id": "7", "text": "abcd", "mtime_ns": 5}
    assert kinds.PDF.known("p.pdf", row) == disk.Known("p.pdf", "pdf", 10, "h" * 24, "7", "abcd", None, 5)
    assert kinds.NOTES.known("n.md", {**row, "kind": "notes"}) == disk.Known("n.md", "notes", 10, "h" * 24, "7", "", "abcd", 5)
    assert kinds.PICTURE.known("f.png", {**row, "kind": "picture"}).text is None
    assert kinds.PDF.link_name("p.pdf", row) == f"{'h' * 24}.pdf" and kinds.PDF.link_name("p.pdf", {**row, "hash": ""}) is None
    assert kinds.PICTURE.link_name("f.png", {"hash": "abc.png"}) == "abc.png" and kinds.NOTES.link_name("n.md", row) is None
    found = disk.Found("pdf", 1234, 0, "1")
    assert kinds.PDF.weight(found) == 1234 and kinds.NOTES.weight(disk.Found("notes", 1234, 0, "1")) == 0
    read = sync.Read("p.pdf", "pdf", 10, 5, "7", 0.0, "h" * 24, text="t", pdf_id="id")
    assert kinds.PDF.pending_text(read) == "id" and kinds.NOTES.pending_text(read) == "t"
    assert kinds.PICTURE.pending_text(read) is None


def test_the_settle_time_is_the_registrys(monkeypatch):
    assert kinds.SETTLE_NOTE_S == 1.0
    with pytest.raises(AttributeError):
        monkeypatch.setattr(sync, "SETTLE_NOTE_S", 0)      # a patch of the old name fails loudly
