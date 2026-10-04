"""The store behind every stored file (gamma/storage.py): each workspace's
uploads/ directory. Its calls keep files where the rest of the app looks
for them, accept only one-segment names, write whole or not at all, sweep
what a dead write left in .partial/, store a file assembled there by a
rename, and keep the usage total current without walking the directory on
every read. The uploads route serves a stored file from there."""

import io
import os
import time

import pytest

from conftest import login, make_user
from gamma import storage
from gamma.db import ws_uploads_dir

PDF = b"%PDF-1.4 stored\n" + b"s" * 3000


def _name(data: bytes, ext: str = ".bin") -> str:
    return storage.content_digest(data) + ext


@pytest.fixture(scope="module")
def local_ws():
    return make_user("st_local", "pw-st-1")


def test_the_store_keeps_files_in_the_uploads_directory(local_ws):
    data = b"one file" * 50
    name = _name(data)
    storage.put(local_ws, name, data)
    path = ws_uploads_dir(local_ws) / name
    assert path.read_bytes() == data and storage.open_path(local_ws, name) == path
    assert storage.find_upload_file(name, local_ws) == path and storage.find_upload_file(name, "") is None
    assert storage.exists(local_ws, name) and storage.size(local_ws, name) == len(data)
    listed = {n: (size, mtime) for n, size, mtime in storage.list(local_ws)}
    assert listed[name] == (len(data), path.stat().st_mtime) == storage.stat(local_ws, name)
    assert storage.usage(local_ws) == sum(size for size, _ in listed.values())

    os.utime(path, (time.time() - 86400,) * 2)
    assert storage.touch(local_ws, name) and time.time() - path.stat().st_mtime < 60
    assert storage.stat(local_ws, name) == (len(data), path.stat().st_mtime)

    storage.delete(local_ws, name)
    assert storage.stat(local_ws, name) is None
    storage.delete(local_ws, name)  # gone already: no error
    assert not path.exists() and not storage.exists(local_ws, name)
    assert storage.size(local_ws, name) is None and storage.open_path(local_ws, name) is None
    assert storage.find_upload_file(name, local_ws) is None and not storage.touch(local_ws, name)


def test_a_name_is_one_path_segment(local_ws):
    for bad in ("", ".partial", "../users.db", "a/b.png", "a\\b.png", "x\0.png", "a" * 256):
        with pytest.raises(ValueError):
            storage.put(local_ws, bad, b"x")
        with pytest.raises(ValueError):
            storage.exists(local_ws, bad)
    assert storage.check_name("a" * 24 + ".pdf") == "a" * 24 + ".pdf"
    assert storage.find_upload_file("../users.db", local_ws) is None


def test_a_write_is_whole_or_nothing_and_dead_temp_files_go(local_ws, monkeypatch):
    data = b"whole" * 100
    name = _name(data)
    real_fsync = os.fsync

    def disk_full(fd):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(os, "fsync", disk_full)
    with pytest.raises(OSError):
        storage.put(local_ws, name, data)
    monkeypatch.setattr(os, "fsync", real_fsync)
    partial = ws_uploads_dir(local_ws) / ".partial"
    assert storage.partial_dir(local_ws) == partial
    assert not storage.exists(local_ws, name) and not any(partial.iterdir())

    storage.put(local_ws, name, data)
    storage.put(local_ws, name, b"replaced")  # a second put replaces the file at once
    assert storage.open_path(local_ws, name).read_bytes() == b"replaced"
    assert all(n != ".partial" for n, _, _ in storage.list(local_ws))  # the temp folder is no stored file

    old, fresh = partial / "dead", partial / "writing"
    old.write_bytes(b"x")
    fresh.write_bytes(b"y")
    os.utime(old, (time.time() - 2 * 86400,) * 2)
    assert storage.sweep_partial(local_ws, 86400) == 1
    assert not old.exists() and fresh.exists()
    fresh.unlink()
    storage.delete(local_ws, name)


def test_usage_is_walked_once_then_kept_current(tmp_path, monkeypatch):
    """Every quota check reads the usage (an ink merge's under the write
    lock): the directory is walked once, the store's own writes adjust the
    total, and a change made behind its back is seen at the next read."""
    from gamma import db

    monkeypatch.setattr(db, "WORKSPACES_DIR", tmp_path)
    monkeypatch.setattr(storage, "_usage", {})
    walks, real = [], storage.list
    monkeypatch.setattr(storage, "list", lambda ws: walks.append(ws) or real(ws))
    storage.put("st-use", "a.bin", b"a" * 100)
    assert storage.usage("st-use") == 100 and storage.usage("st-use") == 100 and len(walks) == 1
    storage.put("st-use", "b.bin", b"b" * 50)
    storage.put("st-use", "a.bin", b"a" * 30)  # replaced: 70 fewer
    storage.delete("st-use", "b.bin")
    storage.delete("st-use", "b.bin")
    assert storage.usage("st-use") == 30 and len(walks) == 1
    time.sleep(0.1)  # a directory's mtime moves by the clock's tick (Windows: up to 16 ms)
    (tmp_path / "st-use" / "uploads" / "c.bin").write_bytes(b"c" * 5)  # not through the store
    assert storage.usage("st-use") == 35 and len(walks) == 2
    storage.delete_workspace("st-use")
    assert storage.usage("st-use") == 0


def test_delete_workspace_removes_the_uploads_directory(tmp_path, monkeypatch):
    from gamma import db

    monkeypatch.setattr(db, "WORKSPACES_DIR", tmp_path)
    storage.put("st-gone", "a.png", b"a")
    assert (tmp_path / "st-gone" / "uploads" / "a.png").is_file()
    storage.delete_workspace("st-gone")
    assert not (tmp_path / "st-gone" / "uploads").exists() and storage.list("st-gone") == []


def test_a_file_assembled_in_the_partial_directory_is_stored_by_a_rename(local_ws):
    data = b"assembled" * 100
    name = _name(data)
    partial = storage.partial_dir(local_ws)
    partial.mkdir(parents=True, exist_ok=True)
    staged = partial / "parts-test"
    staged.write_bytes(data)
    before = storage.usage(local_ws)
    storage.put_path(local_ws, name, staged)
    assert not staged.exists() and storage.open_path(local_ws, name).read_bytes() == data
    assert storage.usage(local_ws) == before + len(data)
    assert storage.sweep_partial(local_ws, 0) == 0  # nothing left behind
    storage.delete(local_ws, name)


def test_the_uploads_route_serves_the_stored_file():
    """Whole, by range and as headers alone, from the uploads directory;
    never a redirect."""
    make_user("st_route", "pw-st-2")
    c = login("st_route", "pw-st-2")
    up = c.post("/api/uploads", files={"file": ("paper.pdf", io.BytesIO(PDF), "application/pdf")}).json()
    url = f"/api/uploads/{up['doc_id']}.pdf"
    r = c.get(url, follow_redirects=False)
    assert r.status_code == 200 and r.content == PDF
    part = c.get(url, headers={"Range": "bytes=0-9"})
    assert part.status_code == 206 and part.content == PDF[:10]
    head = c.head(url)
    assert head.status_code == 200 and head.headers["content-length"] == str(len(PDF))
    assert c.get(f"/api/uploads/{'0' * 24}.pdf", follow_redirects=False).status_code == 404
