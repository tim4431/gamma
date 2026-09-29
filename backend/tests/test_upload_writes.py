"""Stored files are written whole or not at all (storage.write_atomic), a
dedup hit repairs a copy an older write left short, and a mirror pull keeps
only bytes that are what their name says (storage.matches_name) — a captive
portal's page never becomes a stored PDF."""

import errno
import io
import os
import time

import pytest

from conftest import login, make_user, workspace_of
from gamma import storage, sync_engine
from gamma.db import ws_uploads_dir

PDF = b"%PDF-1.4\n" + b"0" * 200_000 + b"\n%%EOF\n"


@pytest.fixture
def cam():
    make_user("uw_cam", "pw")
    return login("uw_cam", "pw"), workspace_of("uw_cam")


def _upload(c, data, name="big.pdf"):
    return c.post("/api/uploads", files={"file": (name, io.BytesIO(data), "application/pdf")})


def test_a_write_that_dies_half_way_leaves_nothing(cam, monkeypatch):
    c, ws = cam
    data = PDF + b"dies"
    real_fsync = os.fsync

    def disk_full(fd):
        raise OSError(errno.ENOSPC, "No space left on device")

    monkeypatch.setattr(storage.os, "fsync", disk_full)
    with pytest.raises(OSError):
        _upload(c, data)
    monkeypatch.setattr(storage.os, "fsync", real_fsync)
    name = f"{storage.content_digest(data)}.pdf"
    uploads = ws_uploads_dir(ws)
    assert not (uploads / name).exists()
    assert not any((uploads / ".partial").iterdir())  # the temp file went too
    # space freed, the user retries: stored whole, not "already there"
    r = _upload(c, data)
    assert r.status_code == 200 and r.json()["already_existed"] is False
    assert (uploads / name).read_bytes() == data
    assert c.get(r.json()["source_url"]).content == data


def test_a_rename_refused_over_the_same_bytes_counts_as_stored(tmp_path, monkeypatch):
    # Windows: two threads storing one file at once — the second rename is
    # refused while the first lands. The name is the content's hash, so the
    # copy already there is the same bytes.
    path = tmp_path / f"{storage.content_digest(PDF)}.pdf"
    path.write_bytes(PDF)

    def refused(src, dst):
        raise PermissionError(5, "Access is denied")

    monkeypatch.setattr(storage.os, "replace", refused)
    storage.write_atomic(path, PDF)
    assert path.read_bytes() == PDF
    assert not any((tmp_path / ".partial").iterdir())
    with pytest.raises(PermissionError):  # a refusal with nothing like it stored is still an error
        storage.write_atomic(tmp_path / "other.pdf", PDF)


def test_a_dedup_hit_repairs_a_copy_cut_short(cam):
    c, ws = cam
    uploads = ws_uploads_dir(ws)
    data = PDF + b"short"
    name = f"{storage.content_digest(data)}.pdf"
    uploads.mkdir(parents=True, exist_ok=True)
    (uploads / name).write_bytes(data[:len(data) // 2])  # what an old in-place write left
    r = _upload(c, data)
    assert r.status_code == 200 and r.json()["already_existed"] is True
    assert (uploads / name).read_bytes() == data
    # any other kind of file whose size is wrong is not what its name says either
    png = b"\x89PNG\r\n\x1a\n" + b"p" * 300
    img = f"{storage.content_digest(png)}.png"
    (uploads / img).write_bytes(b"\x89PNG")
    assert storage.store_file(ws, png, ".png") == (img, True)
    assert (uploads / img).read_bytes() == png


def test_a_dedup_hit_keeps_a_stripped_pdf_and_redates_it(cam):
    c, ws = cam
    uploads = ws_uploads_dir(ws)
    data = PDF + b"annotated"
    doc_id = _upload(c, data).json()["doc_id"]
    path = uploads / f"{doc_id}.pdf"
    stripped = b"%PDF-1.7\n" + b"s" * 1000 + b"\n%%EOF\n"  # rewritten under its name (annotations stripped)
    path.write_bytes(stripped)
    old = time.time() - 40 * 86400
    os.utime(path, (old, old))
    assert _upload(c, data).json()["already_existed"] is True
    assert path.read_bytes() == stripped  # not undone by the re-upload
    assert time.time() - path.stat().st_mtime < 60  # re-dated: grace and retention start over


def test_matches_name():
    png = b"\x89PNG" + b"x" * 40
    name = f"{storage.content_digest(png)}.png"
    assert storage.matches_name(name, png)
    assert not storage.matches_name(name, b"<html>hotel wifi login</html>")
    # a PDF's name may hash its URL, so a PDF only has to be one
    assert storage.matches_name("a" * 24 + ".pdf", b"%PDF-1.4 whatever")
    assert not storage.matches_name("a" * 24 + ".pdf", b"<html>captive portal</html>")
    # a stem that is no content digest (older names) is not checked
    assert storage.matches_name("abcd1234.png", b"anything")


class _Remote:
    url = "https://origin.example"

    def __init__(self, files):
        self.files = files

    def get_bytes(self, path, progress=None):
        return self.files[path.rsplit("/", 1)[1]]


def test_a_mirror_pull_keeps_only_what_its_name_says(cam):
    _, ws = cam
    good = b"\x89PNG" + b"g" * 50
    good_name = f"{storage.content_digest(good)}.png"
    wrong_name = f"{storage.content_digest(b'the real picture')}.png"
    portal_name = "b" * 24 + ".pdf"
    report = {"files_pulled": 0}
    sync_engine._pull_files(ws, _Remote({good_name: good, wrong_name: b"not the picture",
                                         portal_name: b"<html>sign in to the hotel wifi</html>"}),
                            {good_name, wrong_name, portal_name}, report)
    uploads = ws_uploads_dir(ws)
    assert report["files_pulled"] == 1 and (uploads / good_name).read_bytes() == good
    assert not (uploads / wrong_name).exists() and not (uploads / portal_name).exists()
