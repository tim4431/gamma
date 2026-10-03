"""The store behind every stored file (gamma/blobs.py): the local driver's
contract, the S3 driver against moto's in-process bucket (skipped when moto
is not installed), the uploads route sending a browser to the bucket, the
mirror's fetch leaving its credentials behind on the way there, and the
startup refusing a store that cannot work."""

import io
import os
import time
import urllib.parse
import urllib.request

import pytest

from conftest import login, make_user
from gamma import blobs, storage, upload_gc
from gamma.db import ws_uploads_dir

PDF = b"%PDF-1.4 blobs\n" + b"b" * 3000
PNG = b"\x89PNG\r\n\x1a\n"
from conftest import S3_TEST_BUCKET as BUCKET


def _name(data: bytes, ext: str = ".bin") -> str:
    return storage.content_digest(data) + ext


# --- the local driver --------------------------------------------------------------

@pytest.fixture(scope="module")
def local_ws():
    return make_user("bl_local", "pw-bl-1")


def test_the_local_driver_keeps_files_in_the_uploads_directory(local_ws):
    store, data = blobs.LocalBlobs(), b"one file" * 50
    name = _name(data)
    store.put(local_ws, name, data)
    path = ws_uploads_dir(local_ws) / name
    assert path.read_bytes() == data and store.open_path(local_ws, name) == path
    assert store.exists(local_ws, name) and store.size(local_ws, name) == len(data)
    listed = {n: (size, mtime) for n, size, mtime in store.list(local_ws)}
    assert listed[name] == (len(data), path.stat().st_mtime) == store.stat(local_ws, name)
    assert store.usage(local_ws) == sum(size for size, _ in listed.values())
    assert store.url(local_ws, name, media_type="application/octet-stream") is None

    os.utime(path, (time.time() - 86400,) * 2)
    assert store.touch(local_ws, name) and time.time() - path.stat().st_mtime < 60
    assert store.stat(local_ws, name) == (len(data), path.stat().st_mtime)

    store.delete(local_ws, name)
    assert store.stat(local_ws, name) is None
    store.delete(local_ws, name)  # gone already: no error
    assert not path.exists() and not store.exists(local_ws, name)
    assert store.size(local_ws, name) is None and store.open_path(local_ws, name) is None
    assert not store.touch(local_ws, name)


def test_a_name_is_one_path_segment(local_ws):
    store = blobs.LocalBlobs()
    for bad in ("", ".partial", "../users.db", "a/b.png", "a\\b.png", "x\0.png", "a" * 256):
        with pytest.raises(ValueError):
            store.put(local_ws, bad, b"x")
        with pytest.raises(ValueError):
            store.exists(local_ws, bad)
    assert storage.find_upload_file("../users.db", local_ws) is None


def test_a_local_write_is_whole_or_nothing_and_dead_temp_files_go(local_ws, monkeypatch):
    store, data = blobs.LocalBlobs(), b"whole" * 100
    name = _name(data)
    real_fsync = os.fsync

    def disk_full(fd):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(os, "fsync", disk_full)
    with pytest.raises(OSError):
        store.put(local_ws, name, data)
    monkeypatch.setattr(os, "fsync", real_fsync)
    partial = ws_uploads_dir(local_ws) / ".partial"
    assert not store.exists(local_ws, name) and not any(partial.iterdir())

    store.put(local_ws, name, data)
    store.put(local_ws, name, b"replaced")  # a second put replaces the file at once
    assert store.open_path(local_ws, name).read_bytes() == b"replaced"
    assert all(n != ".partial" for n, _, _ in store.list(local_ws))  # the temp folder is no stored file

    old, fresh = partial / "dead", partial / "writing"
    old.write_bytes(b"x")
    fresh.write_bytes(b"y")
    os.utime(old, (time.time() - 2 * 86400,) * 2)
    assert store.sweep_partial(local_ws, 86400) == 1
    assert not old.exists() and fresh.exists()
    fresh.unlink()
    store.delete(local_ws, name)


def test_local_usage_is_walked_once_then_kept_current(tmp_path, monkeypatch):
    """Every quota check reads the usage (an ink merge's under the write
    lock): the directory is walked once, the driver's own writes adjust the
    total, and a change made behind its back is seen at the next read."""
    from gamma import db

    monkeypatch.setattr(db, "WORKSPACES_DIR", tmp_path)
    store, walks = blobs.LocalBlobs(), []
    real = store.list
    store.list = lambda ws: walks.append(ws) or real(ws)
    store.put("bl-use", "a.bin", b"a" * 100)
    assert store.usage("bl-use") == 100 and store.usage("bl-use") == 100 and len(walks) == 1
    store.put("bl-use", "b.bin", b"b" * 50)
    store.put("bl-use", "a.bin", b"a" * 30)  # replaced: 70 fewer
    store.delete("bl-use", "b.bin")
    store.delete("bl-use", "b.bin")
    assert store.usage("bl-use") == 30 and len(walks) == 1
    time.sleep(0.1)  # a directory's mtime moves by the clock's tick (Windows: up to 16 ms)
    (tmp_path / "bl-use" / "uploads" / "c.bin").write_bytes(b"c" * 5)  # not through the driver
    assert store.usage("bl-use") == 35 and len(walks) == 2
    store.delete_workspace("bl-use")
    assert store.usage("bl-use") == 0


def test_delete_workspace_removes_the_uploads_directory(tmp_path, monkeypatch):
    from gamma import db

    monkeypatch.setattr(db, "WORKSPACES_DIR", tmp_path)
    store = blobs.LocalBlobs()
    store.put("bl-gone", "a.png", b"a")
    assert (tmp_path / "bl-gone" / "uploads" / "a.png").is_file()
    store.delete_workspace("bl-gone")
    assert not (tmp_path / "bl-gone" / "uploads").exists() and store.list("bl-gone") == []


def test_a_stored_file_is_read_when_used(local_ws):
    data = b"read later" * 20
    name, _ = storage.store_file(local_ws, data, ".bin")
    later = storage.UploadDir(local_ws) / name
    assert later.is_file() and later.exists() and later.name == name
    assert later.read_bytes() == data and later.stat().st_size == len(data)
    assert os.fspath(later) == str(ws_uploads_dir(local_ws) / name)
    missing = storage.UploadDir(local_ws) / ("0" * 24 + ".png")
    assert not missing.is_file()
    with pytest.raises(FileNotFoundError):
        missing.read_bytes()
    with pytest.raises(FileNotFoundError):
        os.fspath(missing)
    blobs.delete(local_ws, name)


def test_a_file_assembled_in_the_partial_directory_is_stored_by_a_rename(local_ws):
    store, data = blobs.LocalBlobs(), b"assembled" * 100
    name = _name(data)
    partial = store.partial_dir(local_ws)
    assert partial == ws_uploads_dir(local_ws) / ".partial"
    partial.mkdir(parents=True, exist_ok=True)
    staged = partial / "parts-test"
    staged.write_bytes(data)
    before = store.usage(local_ws)
    store.put_path(local_ws, name, staged)
    assert not staged.exists() and store.open_path(local_ws, name).read_bytes() == data
    assert store.usage(local_ws) == before + len(data)
    assert store.sweep_partial(local_ws, 0) == 0  # nothing left behind
    store.delete(local_ws, name)


# --- the S3 driver -----------------------------------------------------------------

@pytest.fixture
def bucket(tmp_path, s3_bucket):
    """``make(**options)`` builds a driver on conftest's moto bucket with a
    cache of its own under tmp_path."""
    def make(cache="cache", **options):
        return blobs.S3Blobs(BUCKET, region="us-east-1", access_key="testing", secret_key="testing",
                             cache_dir=tmp_path / cache, **options)

    make.raw = s3_bucket
    return make


def _counting(store, method):
    calls = []
    real = getattr(store.client, method)

    def counted(**kwargs):
        calls.append(kwargs.get("Key", kwargs.get("Prefix")))  # a listing names its prefix
        return real(**kwargs)

    setattr(store.client, method, counted)
    return calls


def test_a_file_goes_to_the_bucket_and_back(bucket):
    store, data = bucket(), PDF
    name = _name(data, ".pdf")
    store.put("ws-a", name, data)
    got = bucket.raw.get_object(Bucket=BUCKET, Key=f"uploads/ws-a/{name}")
    assert got["Body"].read() == data
    # a name never holds other bytes: a browser may keep what a presigned URL gave it
    assert (got["ContentType"], got["CacheControl"]) == ("application/pdf", "private, max-age=31536000, immutable")
    assert store.exists("ws-a", name) and store.size("ws-a", name) == len(data)
    assert store.open_path("ws-a", name).read_bytes() == data  # the copy put left in the cache
    assert not store.exists("ws-a", "0" * 24 + ".pdf") and store.open_path("ws-a", "0" * 24 + ".pdf") is None

    # another node (an empty cache): the first read downloads, the second is the cached copy
    other = bucket(cache="other-cache")
    gets = _counting(other, "get_object")
    path = other.open_path("ws-a", name)
    assert path.read_bytes() == data and path.is_relative_to(other.cache_dir)
    assert other.open_path("ws-a", name) == path and len(gets) == 1
    # a restart finds the cached copy again
    assert bucket(cache="other-cache").open_path("ws-a", name) == path


def test_the_cache_keeps_the_copies_used_last_within_its_bytes(bucket):
    store = bucket(cache_bytes=2500)
    files = [bytes([65 + i]) * 1000 for i in range(4)]
    names = [_name(f) for f in files]
    for name, data in zip(names[:3], files[:3]):
        store.put("ws-c", name, data)
    cached = {p.name for p in (store.cache_dir / "ws-c").iterdir() if p.is_file()}
    assert cached == set(names[1:3])  # the oldest went past 2500 bytes
    assert store.open_path("ws-c", names[0]).read_bytes() == files[0]  # fetched again
    assert not (store.cache_dir / "ws-c" / names[1]).exists()
    store.open_path("ws-c", names[2])  # used now: the newest
    store.put("ws-c", names[3], files[3])
    cached = {p.name for p in (store.cache_dir / "ws-c").iterdir() if p.is_file()}
    assert cached == {names[2], names[3]} and store.cache_used == 2000


def test_a_delete_removes_the_object_and_the_copy(bucket):
    store, data = bucket(), b"to delete" * 30
    name = _name(data)
    store.put("ws-d", name, data)
    copy = store.open_path("ws-d", name)
    store.delete("ws-d", name)
    store.delete("ws-d", name)  # gone already: no error
    assert not copy.exists() and not store.exists("ws-d", name)
    assert "Contents" not in bucket.raw.list_objects_v2(Bucket=BUCKET, Prefix="uploads/ws-d/")


def test_usage_is_listed_once_then_kept_current(bucket):
    store = bucket()
    one, two = b"1" * 100, b"2" * 250
    store.put("ws-u", _name(one), one)
    assert store.usage("ws-u") == 100
    lists = []
    real = store._keys
    store._keys = lambda prefix: lists.append(prefix) or real(prefix)
    store.put("ws-u", _name(two), two)
    assert store.usage("ws-u") == 350
    store.delete("ws-u", _name(one))
    assert store.usage("ws-u") == 250 and lists == []  # no listing within USAGE_TTL_S
    assert {n for n, _, _ in store.list("ws-u")} == {_name(two)} and len(lists) == 1


def test_touch_writes_the_object_again(bucket):
    store, data = bucket(), b"redate" * 40
    name = _name(data)
    store.put("ws-t", name, data)
    before = bucket.raw.head_object(Bucket=BUCKET, Key=f"uploads/ws-t/{name}")
    assert store.touch("ws-t", name)
    after = bucket.raw.head_object(Bucket=BUCKET, Key=f"uploads/ws-t/{name}")
    assert after["Metadata"].get("touched") and after["LastModified"] >= before["LastModified"]
    assert after["ContentLength"] == len(data)
    # the copy onto itself replaces the metadata: the media type and Cache-Control stay
    assert (after["ContentType"], after["CacheControl"]) == ("application/octet-stream", blobs.OBJECT_CACHE_CONTROL)
    assert store.stat("ws-t", name) == (len(data), after["LastModified"].timestamp())
    assert not store.touch("ws-t", "0" * 24 + ".bin")
    assert store.stat("ws-t", "0" * 24 + ".bin") is None


def test_a_presigned_url_names_the_object_and_its_headers(bucket):
    store = bucket(prefix="/tenant-1/")
    url = store.url("ws-p", "a" * 24 + ".pdf", media_type="application/pdf",
                    disposition='attachment; filename="a.pdf"', ttl=300)
    parts = urllib.parse.urlsplit(url)
    query = urllib.parse.parse_qs(parts.query)
    assert parts.path.endswith(f"tenant-1/uploads/ws-p/{'a' * 24}.pdf")
    assert query["X-Amz-Expires"] == ["300"] and query["X-Amz-Algorithm"] == ["AWS4-HMAC-SHA256"]
    assert query["response-content-type"] == ["application/pdf"]
    assert query["response-content-disposition"] == ['attachment; filename="a.pdf"']
    assert bucket(presign=False).url("ws-p", "a.pdf", media_type="application/pdf") is None


def test_delete_workspace_removes_its_objects_only(bucket):
    store = bucket()
    for i in range(3):
        store.put("ws-gone", f"f{i}.bin", b"x" * 10)
    store.put("ws-kept", "k.bin", b"k")
    store.delete_workspace("ws-gone")
    assert store.list("ws-gone") == [] and [n for n, _, _ in store.list("ws-kept")] == ["k.bin"]
    assert not (store.cache_dir / "ws-gone").exists() and store.usage("ws-gone") == 0


def test_the_object_calls_stream_through_the_bucket(bucket, tmp_path):
    """put_object / get_object / list_objects / delete_object under the
    prefix, outside uploads/ and the cache; a file past the 8 MB threshold
    goes up in parts."""
    store = bucket(prefix="tenant-2")
    big = tmp_path / "big.db"
    big.write_bytes(os.urandom(1 << 16) * 150)  # 9.4 MB
    store.put_object("dbcopies/ws-o/20261003T100000Z-pages.db", big)
    store.put_object("dbcopies/users/20261003T100000Z.db", big)
    head = bucket.raw.head_object(Bucket=BUCKET, Key="tenant-2/dbcopies/ws-o/20261003T100000Z-pages.db")
    assert head["ContentLength"] == big.stat().st_size and "-" in head["ETag"]  # a multipart upload's ETag
    listed = store.list_objects("dbcopies/ws-o/")
    assert [(k, size) for k, size, _ in listed] == [("dbcopies/ws-o/20261003T100000Z-pages.db", big.stat().st_size)]
    assert len(store.list_objects("dbcopies/")) == 2 and store.list("ws-o") == []  # no stored file of ws-o
    back = tmp_path / "back" / "pages.db"
    assert store.get_object("dbcopies/ws-o/20261003T100000Z-pages.db", back)
    assert back.read_bytes() == big.read_bytes() and not any(store.cache_dir.rglob("*.db"))
    assert not store.get_object("dbcopies/ws-o/missing.db", tmp_path / "missing.db")
    assert not (tmp_path / "missing.db").exists() and not any(tmp_path.glob(".missing.db.*"))
    store.delete_object("dbcopies/ws-o/20261003T100000Z-pages.db")
    store.delete_object("dbcopies/ws-o/20261003T100000Z-pages.db")  # gone already: no error
    assert store.list_objects("dbcopies/ws-o/") == []
    for bad in ("uploads/ws-o/a.pdf", "dbcopies", "../dbcopies/x.db"):
        with pytest.raises(ValueError):
            store.put_object(bad, big)


def test_a_file_assembled_on_the_node_goes_to_the_bucket_and_into_the_cache(bucket):
    store, data = bucket(), PDF
    name = _name(data, ".pdf")
    partial = store.partial_dir("ws-p")
    assert partial == store.cache_dir / "ws-p" / ".partial"
    partial.mkdir(parents=True, exist_ok=True)
    staged = partial / "parts-test"
    staged.write_bytes(data)
    store.put_path("ws-p", name, staged)
    assert not staged.exists()
    assert bucket.raw.get_object(Bucket=BUCKET, Key=f"uploads/ws-p/{name}")["Body"].read() == data
    assert store.size("ws-p", name) == len(data) and store.usage("ws-p") == len(data)
    assert (store.cache_dir / "ws-p" / name).read_bytes() == data and store._index[("ws-p", name)] == len(data)
    gets = _counting(store, "get_object")
    assert store.open_path("ws-p", name).read_bytes() == data and gets == []  # the copy, no download


# --- the uploads route with the files in a bucket ------------------------------------

class _OneWorkspace:
    """A bucket for one workspace, the suite's local store for every other
    one: the background passes of the rest of the suite never see it."""

    def __init__(self, s3, ws):
        self.s3, self.ws, self.local = s3, ws, blobs.LocalBlobs()

    def __getattr__(self, attr):
        def call(ws, *args, **kwargs):
            return getattr(self.s3 if ws == self.ws else self.local, attr)(ws, *args, **kwargs)
        return call


@pytest.fixture
def in_bucket(bucket):
    ws = make_user("bl_s3", "pw-bl-2")
    c = login("bl_s3", "pw-bl-2")

    def to(**options):
        blobs.use(_OneWorkspace(bucket(**options), ws))
        return c, ws

    before = blobs.driver()
    yield to
    blobs.use(before)


def test_the_uploads_route_redirects_to_the_bucket(in_bucket):
    c, ws = in_bucket()
    up = c.post("/api/uploads", files={"file": ("paper.pdf", io.BytesIO(PDF), "application/pdf")}).json()
    assert not (ws_uploads_dir(ws) / f"{up['doc_id']}.pdf").exists()  # in the bucket, not the directory
    r = c.get(f"/api/uploads/{up['doc_id']}.pdf", follow_redirects=False)
    # kept by the browser a minute less than the URL is valid
    assert r.status_code == 302 and r.headers["cache-control"] == "private, max-age=240"
    query = urllib.parse.parse_qs(urllib.parse.urlsplit(r.headers["location"]).query)
    assert query["X-Amz-Expires"] == ["300"]
    assert query["response-content-type"] == ["application/pdf"] and "response-content-disposition" not in query
    head = c.head(f"/api/uploads/{up['doc_id']}.pdf")
    assert head.status_code == 200 and head.headers["content-length"] == str(len(PDF))
    assert head.headers["accept-ranges"] == "bytes"

    data = b"a data file"
    sent = c.post("/api/upload-file", files={"file": ("data.csv", data, "text/csv")}).json()
    stored = blobs.driver().s3.client.head_object(Bucket=BUCKET, Key=f"uploads/{ws}/{sent['url'].rsplit('/', 1)[1]}")
    assert stored["ContentType"] == "text/csv; charset=utf-8"
    r = c.get(sent["url"], follow_redirects=False)
    query = urllib.parse.parse_qs(urllib.parse.urlsplit(r.headers["location"]).query)
    assert query["response-content-disposition"] == [f'attachment; filename="{sent["url"].rsplit("/", 1)[1]}"']
    assert c.get(f"/api/uploads/{'0' * 24}.pdf", follow_redirects=False).status_code == 404
    assert c.get("/api/quota").json()["workspace_bytes"] == len(PDF) + len(data)


def test_the_uploads_route_streams_from_the_cache_with_presigning_off(in_bucket):
    c, ws = in_bucket(presign=False)
    up = c.post("/api/uploads", files={"file": ("paper.pdf", io.BytesIO(PDF), "application/pdf")}).json()
    r = c.get(f"/api/uploads/{up['doc_id']}.pdf", follow_redirects=False)
    assert r.status_code == 200 and r.content == PDF
    part = c.get(f"/api/uploads/{up['doc_id']}.pdf", headers={"Range": "bytes=0-9"})
    assert part.status_code == 206 and part.content == PDF[:10]


def test_a_backup_and_the_gc_keep_a_bucket(in_bucket, monkeypatch):
    """A snapshot packs the bucket's files and a restore puts a lost one
    back; an unreferenced one is recorded, then purged with its object."""
    from types import SimpleNamespace

    from gamma import ws_backup

    c, ws = in_bucket()
    s3 = blobs.driver().s3
    assert c.post("/api/pages", json={"title": "kept"}).status_code == 200  # the purge wants a library
    png = b"\x89PNG\r\n\x1a\n" + b"unreferenced" * 20
    name = c.post("/api/upload-image", files={"file": ("u.png", png, "image/png")}).json()["url"].rsplit("/", 1)[1]

    snap = ws_backup.create(ws, label="bucket")
    assert snap["upload_files"] == 1 and snap["missing_uploads"] == 0
    s3.delete(ws, name)
    restored = ws_backup.restore_zip(ws, ws_backup.backup_path(ws, snap["name"]), "merge")
    assert restored["uploads_added"] == 1 and s3.open_path(ws, name).read_bytes() == png
    ws_backup.delete(ws, snap["name"])

    now = [time.time() + 3600]  # past the upload grace
    monkeypatch.setattr(upload_gc, "time", SimpleNamespace(time=lambda: now[0], monotonic=time.monotonic))
    assert upload_gc.reconcile(ws)["recorded"] == [name]
    now[0] += upload_gc.RETAIN_S
    assert upload_gc.reconcile(ws)["purged"] == [name]
    assert not s3.exists(ws, name)
    assert "Contents" not in s3.client.list_objects_v2(Bucket=BUCKET, Prefix=f"uploads/{ws}/")


def test_the_purge_lists_nothing_under_the_write_lock(in_bucket, monkeypatch):
    """Under the write lock the purge dates each due file with a HEAD of
    its own, never a paginated listing of the workspace while writers wait,
    and asks no more once a refusal is certain."""
    from types import SimpleNamespace

    c, ws = in_bucket()
    s3 = blobs.driver().s3
    assert c.post("/api/pages", json={"title": "kept"}).status_code == 200  # the purge wants a library
    names = sorted(c.post("/api/upload-image", files={"file": (f"d{i}.png", PNG + b"due %d" % i, "image/png")})
                   .json()["url"].rsplit("/", 1)[1] for i in range(5))
    now = [time.time() + 3600]  # past the upload grace
    monkeypatch.setattr(upload_gc, "time", SimpleNamespace(time=lambda: now[0], monotonic=time.monotonic))
    assert upload_gc.reconcile(ws)["recorded"] == names
    now[0] += upload_gc.RETAIN_S

    lists, heads = _counting(s3, "list_objects_v2"), _counting(s3, "head_object")
    real = upload_gc._purge

    def purge(*args):  # everything _purge does, it does under the write lock
        lists.clear()
        heads.clear()
        return real(*args)

    monkeypatch.setattr(upload_gc, "_purge", purge)
    monkeypatch.setattr(upload_gc, "PURGE_MAX", 2)
    out = upload_gc.reconcile(ws)
    assert out["purged"] == [] and out["blocked"].startswith("5 of its 5 files at once")
    assert lists == [] and len(heads) == 3  # past the cap at the third: the last two never asked
    monkeypatch.setattr(upload_gc, "PURGE_MAX", 100)
    assert upload_gc.reconcile(ws)["purged"] == names
    assert lists == [] and sorted(heads) == [f"uploads/{ws}/{n}" for n in names]
    assert "Contents" not in s3.client.list_objects_v2(Bucket=BUCKET, Prefix=f"uploads/{ws}/")


# --- manage.py uploads-push ------------------------------------------------------------

def test_uploads_push_puts_what_the_bucket_lacks(bucket, data_dir, capsys, monkeypatch):
    import manage
    from gamma import db

    for var in ("GAMMA_S3_ENDPOINT", "GAMMA_S3_PREFIX", "GAMMA_BLOB_CACHE_DIR", "GAMMA_BLOB_CACHE_BYTES"):
        monkeypatch.delenv(var, raising=False)
    for var, value in (("GAMMA_BLOBS", "s3"), ("GAMMA_S3_BUCKET", BUCKET), ("GAMMA_S3_REGION", "us-east-1"),
                       ("GAMMA_S3_ACCESS_KEY", "testing"), ("GAMMA_S3_SECRET_KEY", "testing")):
        monkeypatch.setenv(var, value)
    files = {"ws-push-a": [b"one" * 10, PNG + b"two"], "ws-push-b": [b"three" * 10]}
    for ws, contents in files.items():
        folder = db.ws_uploads_dir(ws)
        (folder / ".partial").mkdir(parents=True)
        (folder / ".partial" / "half-written").write_bytes(b"x")  # a write in progress: no stored file
        for data in contents:
            (folder / _name(data, ".png" if data.startswith(PNG) else ".bin")).write_bytes(data)
    (db.ws_uploads_dir("ws-empty")).mkdir(parents=True)
    store = bucket(cache="server-cache")  # the bucket as a server sees it
    there = _name(files["ws-push-a"][0])
    store.put("ws-push-a", there, files["ws-push-a"][0])

    manage.uploads_push(["--check"])
    out = capsys.readouterr().out
    assert "  ws-push-a: 1 of 2 file(s) missing from the bucket" in out
    assert "  ws-push-b: 1 of 1 file(s) missing from the bucket" in out and "ws-empty" not in out
    assert out.splitlines()[-1].startswith("2 file(s) (0.0 MB) missing from s3://gamma-test/.")
    assert store.list("ws-push-b") == []  # counted, not put

    manage.uploads_push([])
    out = capsys.readouterr().out
    assert "  ws-push-a: put 1 file(s), 1 there already" in out and "  ws-push-b: put 1 file(s), 0 there" in out
    for ws, contents in files.items():
        for data in contents:
            name = _name(data, ".png" if data.startswith(PNG) else ".bin")
            got = bucket.raw.get_object(Bucket=BUCKET, Key=f"uploads/{ws}/{name}")
            assert got["Body"].read() == data and got["CacheControl"] == blobs.OBJECT_CACHE_CONTROL
            assert got["ContentType"] == ("image/png" if name.endswith(".png") else "application/octet-stream")
            assert (db.ws_uploads_dir(ws) / name).read_bytes() == data  # the local file stays
    assert {n for n, _, _ in store.list("ws-push-a")} == {there, _name(PNG + b"two", ".png")}
    # no cache was filled: not the server's default one, not this test's server's
    assert not (data_dir / "cache" / "uploads").exists()
    assert [p.name for p in store.cache_dir.rglob("*") if p.is_file()] == [there]

    manage.uploads_push([])  # a second run puts nothing
    out = capsys.readouterr().out
    assert "put 0 file(s), 2 there already" in out and out.splitlines()[-1].startswith("Put 0 file(s)")

    monkeypatch.setenv("GAMMA_BLOBS", "local")
    with pytest.raises(SystemExit) as stop:
        manage.uploads_push([])
    assert stop.value.code == 2 and "GAMMA_BLOBS=s3" in capsys.readouterr().out
    monkeypatch.setenv("GAMMA_BLOBS", "s3")
    monkeypatch.setenv("GAMMA_S3_BUCKET", "missing-bucket")
    with pytest.raises(SystemExit) as stop:
        manage.uploads_push(["--check"])
    assert stop.value.code == 2 and "no bucket 'missing-bucket'" in capsys.readouterr().out


def test_uploads_push_runs_before_the_schema_guard(tmp_path):
    """Like backups and db-copies: a data directory of any version (here
    none at all), refused for want of a bucket, not of a schema."""
    import subprocess
    import sys
    from pathlib import Path

    env = {k: v for k, v in os.environ.items() if not k.startswith(("GAMMA_S3_", "GAMMA_BLOB"))}
    result = subprocess.run([sys.executable, "manage.py", "uploads-push", "--check"],
                            cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True, timeout=60,
                            env={**env, "GAMMA_DATA_DIR": str(tmp_path)})
    assert result.returncode == 2 and "Set GAMMA_BLOBS=s3" in result.stdout, result.stdout + result.stderr
    assert not (tmp_path / "users.db").exists()


# --- the mirror's fetch, redirected to a bucket ---------------------------------------

def test_a_redirect_off_the_remote_drops_its_credentials():
    from gamma.sync_engine import _OffOriginRedirect

    handler = _OffOriginRedirect()
    req = urllib.request.Request("https://gamma.example/api/uploads/a.pdf", headers={
        "Authorization": "Bearer secret", "X-Gamma-Workspace": "w1", "Accept": "application/json"})
    there = handler.redirect_request(req, None, 302, "Found", {}, "https://bucket.example/a.pdf?X-Amz-Signature=s")
    assert not there.has_header("Authorization") and not there.has_header("X-gamma-workspace")
    assert there.has_header("Accept")
    here = handler.redirect_request(req, None, 302, "Found", {}, "https://gamma.example/api/uploads/b.pdf")
    assert here.get_header("Authorization") == "Bearer secret"


# --- startup -----------------------------------------------------------------------

def test_a_store_that_cannot_work_is_refused(bucket, monkeypatch):
    with pytest.raises(blobs.BlobConfigError, match="no bucket 'missing-bucket'"):
        blobs.S3Blobs("missing-bucket", region="us-east-1", access_key="testing", secret_key="testing").check()
    with pytest.raises(blobs.BlobConfigError, match="GAMMA_S3_BUCKET"):
        blobs.S3Blobs("")
    with pytest.raises(blobs.BlobConfigError, match="both GAMMA_S3_ACCESS_KEY"):
        blobs.S3Blobs(BUCKET, access_key="only-half")
    bucket().check()  # the bucket that exists passes
    monkeypatch.setenv("GAMMA_BLOBS", "ftp")
    with pytest.raises(blobs.BlobConfigError, match="GAMMA_BLOBS='ftp'"):
        blobs._from_env()
    monkeypatch.setenv("GAMMA_BLOBS", "s3")
    monkeypatch.setenv("GAMMA_S3_BUCKET", BUCKET)
    monkeypatch.setenv("GAMMA_BLOB_CACHE_BYTES", "two gigs")
    with pytest.raises(blobs.BlobConfigError, match="GAMMA_BLOB_CACHE_BYTES"):
        blobs._from_env()
    # the server stopping on such a store: test_startup.py


def test_an_upload_in_parts_lands_in_the_bucket(in_bucket):
    c, ws = in_bucket()
    token = c.post("/api/uploads/parts", json={"size": len(PDF), "name": "paper.pdf"}).json()["token"]
    half = len(PDF) // 2
    for at, to in ((0, half), (half, len(PDF))):
        r = c.post(f"/api/uploads/parts/{token}", data={"offset": str(at)},
                   files={"part": ("part", io.BytesIO(PDF[at:to]), "application/octet-stream")})
        assert r.status_code == 200 and r.json()["received"] == to
    up = c.post(f"/api/uploads/parts/{token}/finish").json()
    assert up["doc_id"] == storage.content_digest(PDF) and up["already_existed"] is False
    assert not (ws_uploads_dir(ws) / f"{up['doc_id']}.pdf").exists()  # in the bucket, not the directory
    assert blobs.exists(ws, f"{up['doc_id']}.pdf") and not any(blobs.partial_dir(ws).iterdir())
    assert c.get(f"/api/uploads/{up['doc_id']}.pdf", follow_redirects=False).status_code == 302
    assert c.get("/api/quota").json()["workspace_bytes"] == len(PDF)
