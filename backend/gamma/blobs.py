"""Where stored files live: one interface, two drivers, chosen once from
``GAMMA_BLOBS`` (``config.blob_env``).

- ``local``, the default, needs nothing: ``workspaces/<id>/uploads/<name>``
  on the data directory's disk, each written whole through
  ``storage.write_atomic`` (a temp file in ``uploads/.partial/``, renamed
  over the name).
- ``s3``: an S3-compatible bucket (AWS, Cloudflare R2, MinIO), one object
  per file under ``<prefix>uploads/<workspace>/<name>``, through boto3,
  which only this driver imports (``requirements-s3.txt``). Code that needs
  a file on disk (pdfium, the zip writers, the PDF exporters) gets the
  node's copy from ``open_path``: a cache on local disk, filled from the
  bucket on a miss and kept under ``GAMMA_BLOB_CACHE_BYTES`` by evicting
  the copies used longest ago. The uploads route sends a browser to the
  bucket with a presigned URL (``url``), so the bucket serves the bytes and
  their Range requests.

The interface, every call naming a workspace and a file name (checked here
by ``check_name``; what a name says about its bytes is ``storage.matches_name``,
at the callers): ``put``, ``exists``, ``size``, ``open_path``, ``delete``,
``delete_workspace``, ``list`` (``(name, size, mtime)``), ``touch`` (the
upload GC's re-date), ``usage`` (bytes stored), ``url`` (a presigned GET,
None when the browser should ask the node) and ``sweep_partial`` (dead
temp files). A store that fails raises ``BlobError``, an OSError.
``check`` runs at startup (``app.create_app``) and refuses a store that
cannot work.

Beside the stored files, four object calls keep whole files of other kinds
under their own namespaces (``OBJECT_SPACES``; keys checked by
``check_key``): ``put_object`` (a local file, streamed up), ``get_object``
(down into a local file, written whole), ``list_objects`` (``(key, size,
mtime)`` under a prefix) and ``delete_object``. The bucket keeps an object
at ``<prefix><key>``, the local driver at ``<data dir>/<key>``. The
database copies are their user (gamma/db_copies.py, ``dbcopies/``).

Docs: docs/dev/user_db.md "Stored files"; deploying with a bucket:
docs/dev/debugging.md "Stored files in a bucket".
"""

from __future__ import annotations  # the drivers have a method named list

import os
import secrets
import shutil
import stat
import threading
import time
from collections import OrderedDict
from pathlib import Path

from . import config
from .db import safe_ws_id, ws_uploads_dir
from .logbuf import log

DEFAULT_CACHE_BYTES = 2 << 30  # the node's copies of bucket files, unless GAMMA_BLOB_CACHE_BYTES says otherwise
USAGE_TTL_S = 60               # a workspace's stored bytes are listed again after this long
KNOWN_TTL_S = 600              # a file this process saw in the bucket is taken to be there this long
KNOWN_MAX = 20000
FETCH_LOCKS = 32               # one download at a time per file: two readers of a miss share it
OBJECT_SPACES = ("dbcopies",)  # the object calls' namespaces, beside the stored files' uploads/


class BlobError(OSError):
    """The store refused a call or could not be reached. An OSError, so the
    callers that handle a failing disk handle a failing bucket alike."""


class BlobConfigError(ValueError):
    """The environment names no store that can work; the server does not
    start (``check``)."""


def check_name(name: str) -> str:
    """``name`` when it can name a stored file: one path segment, at most
    255 characters, not a dot file (``.partial/`` lives beside the files).
    ValueError otherwise."""
    if (not isinstance(name, str) or not name or len(name) > 255 or name.startswith(".")
            or any(c in name for c in "/\\\0")):
        raise ValueError(f"unsafe stored file name: {name!r}")
    return name


def check_key(key: str, *, prefix: bool = False) -> str:
    """``key`` when it can name an object of the object calls: segments
    ``check_name`` accepts, joined by ``/``, the first one of
    OBJECT_SPACES, so no key reaches the stored files or, locally, anything
    else in the data directory. ``prefix``: a listing's prefix, which may
    end in ``/`` or part way into a segment. ValueError otherwise."""
    if not isinstance(key, str) or len(key) > 1024:
        raise ValueError(f"unsafe object key: {key!r}")
    parts = (key[:-1] if prefix and key.endswith("/") else key).split("/")
    try:
        for part in parts:
            check_name(part)
    except ValueError:
        raise ValueError(f"unsafe object key: {key!r}") from None
    if parts[0] not in OBJECT_SPACES or (not prefix and len(parts) < 2):
        raise ValueError(f"object key outside {', '.join(OBJECT_SPACES)}: {key!r}")
    return key


def key_prefix(prefix: str) -> str:
    """``GAMMA_S3_PREFIX`` as the keys begin with it: ``tenant-1/`` for
    ``tenant-1`` or ``/tenant-1/``, "" for none."""
    prefix = prefix.strip("/")
    return prefix + "/" if prefix else ""


def _write_whole(dest: Path, fill, partial: Path | None = None) -> None:
    """Write ``dest`` at once: ``fill(f)`` writes the bytes into a temp file
    in ``partial`` (default: beside ``dest``, on its filesystem), which is
    flushed to disk and renamed over the name, so a write cut short leaves
    nothing under it."""
    partial = partial or dest.parent
    partial.mkdir(parents=True, exist_ok=True)
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = partial / f".{dest.name}.{secrets.token_hex(4)}.part"
    try:
        with open(tmp, "xb") as f:
            fill(f)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, dest)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def _copy_from(path: Path):
    def fill(f):
        with open(path, "rb") as src:
            shutil.copyfileobj(src, f, 1 << 20)
    return fill


def _sweep(partial: Path, max_age_s: float) -> int:
    """Remove the files in ``partial`` older than ``max_age_s``: writes that
    never finished. Returns how many went."""
    if not partial.is_dir():
        return 0
    cutoff, removed = time.time() - max_age_s, 0
    for f in partial.iterdir():
        try:
            if f.is_file() and f.stat().st_mtime < cutoff:
                f.unlink()
                removed += 1
        except OSError:
            pass
    return removed


def _files(folder: Path):
    """``(name, stat)`` of each regular file in ``folder``, none when it is
    missing; the dot files (``.partial/``, temp files) are left out."""
    if not folder.is_dir():
        return
    for f in folder.iterdir():
        if f.name.startswith("."):
            continue
        try:
            st = f.stat()
        except OSError:
            continue
        if stat.S_ISREG(st.st_mode):
            yield f.name, st


# --- local ---------------------------------------------------------------------------

class LocalBlobs:
    """The workspace's ``uploads/`` directory.

    ``usage`` walks the directory once and keeps the total for USAGE_TTL_S
    while the directory's mtime stays what it was; this driver's own puts
    and deletes adjust it. A file added or removed by anything else (a
    restore of a server backup, an admin's copy) moves the mtime and is
    counted at the next read, or after USAGE_TTL_S when it landed within
    the clock tick of one of the driver's writes. Every quota check reads
    it (``server_settings``), an ink merge's under the workspace's write
    lock, so it must not walk a directory that grows with the library on
    every stored write.

    The object calls keep their files under ``root``, the data directory:
    on the same disk as the databases, so what they hold there is no copy
    against losing it (tests, trying the copies out)."""

    kind = "local"
    where = "workspaces/<id>/uploads/"

    def __init__(self):
        self._lock = threading.Lock()
        self._usage: dict = {}  # ws -> [monotonic expiry, the directory's mtime_ns, bytes]

    @property
    def root(self) -> Path:
        return config.DATA_DIR

    def _path(self, ws: str, name: str) -> Path:
        return ws_uploads_dir(ws) / check_name(name)

    def _stamp(self, ws: str) -> int | None:
        try:
            return ws_uploads_dir(ws).stat().st_mtime_ns
        except FileNotFoundError:
            return None

    def _adjust(self, ws: str, before: int | None, delta: int) -> None:
        """After this driver changed the directory, which was dated
        ``before``: the cached total follows, dated by the directory as it
        is now; one the directory changed under since is dropped."""
        with self._lock:
            hit = self._usage.get(ws)
            if hit and hit[1] == before:
                hit[1], hit[2] = self._stamp(ws), hit[2] + delta
            elif hit:
                del self._usage[ws]

    def check(self) -> None:
        pass

    def put(self, ws: str, name: str, data: bytes) -> None:
        from .storage import write_atomic  # local: storage imports this module

        stamp, before = self._stamp(ws), self.size(ws, name) or 0
        write_atomic(self._path(ws, name), data)
        self._adjust(ws, stamp, len(data) - before)

    def exists(self, ws: str, name: str) -> bool:
        return self._path(ws, name).is_file()

    def size(self, ws: str, name: str) -> int | None:
        try:
            st = self._path(ws, name).stat()
        except FileNotFoundError:
            return None
        return st.st_size if stat.S_ISREG(st.st_mode) else None

    def open_path(self, ws: str, name: str) -> Path | None:
        path = self._path(ws, name)
        return path if path.is_file() else None

    def delete(self, ws: str, name: str) -> None:
        stamp, size = self._stamp(ws), self.size(ws, name)
        try:
            self._path(ws, name).unlink()
        except FileNotFoundError:
            return
        self._adjust(ws, stamp, -(size or 0))

    def delete_workspace(self, ws: str) -> None:
        shutil.rmtree(ws_uploads_dir(ws), ignore_errors=True)
        with self._lock:
            self._usage.pop(ws, None)

    def list(self, ws: str):
        return [(name, st.st_size, st.st_mtime) for name, st in _files(ws_uploads_dir(ws))]

    def touch(self, ws: str, name: str) -> bool:
        try:
            os.utime(self._path(ws, name))
        except FileNotFoundError:
            return False
        return True

    def usage(self, ws: str) -> int:
        stamp = self._stamp(ws)
        with self._lock:
            hit = self._usage.get(ws)
            if hit and hit[0] > time.monotonic() and hit[1] == stamp:
                return hit[2]
        total = sum(size for _, size, _ in self.list(ws))
        with self._lock:
            self._usage[ws] = [time.monotonic() + USAGE_TTL_S, stamp, total]
        return total

    def url(self, ws: str, name: str, *, media_type: str, disposition: str | None = None,
            ttl: int = 300) -> str | None:
        return None

    def sweep_partial(self, ws: str, max_age_s: float) -> int:
        return _sweep(ws_uploads_dir(ws) / ".partial", max_age_s)

    # -- the object calls: files under root

    def _object_path(self, key: str) -> Path:
        return self.root / check_key(key)

    def put_object(self, key: str, path: Path) -> None:
        _write_whole(self._object_path(key), _copy_from(Path(path)), self.root / key.split("/")[0] / ".partial")

    def get_object(self, key: str, path: Path) -> bool:
        src = self._object_path(key)
        if not src.is_file():
            return False
        _write_whole(Path(path), _copy_from(src))
        return True

    def list_objects(self, prefix: str):
        check_key(prefix, prefix=True)
        top, out = self.root / (prefix.rsplit("/", 1)[0] if "/" in prefix else prefix), []
        if top.is_dir():
            for f in top.rglob("*"):
                key = f.relative_to(self.root).as_posix()
                if not key.startswith(prefix) or any(part.startswith(".") for part in key.split("/")):
                    continue  # .partial/: writes in progress
                try:
                    st = f.stat()
                except OSError:
                    continue
                if stat.S_ISREG(st.st_mode):
                    out.append((key, st.st_size, st.st_mtime))
        return sorted(out)

    def delete_object(self, key: str) -> None:
        self._object_path(key).unlink(missing_ok=True)


# --- S3-compatible -------------------------------------------------------------------

class S3Blobs:
    """One object per stored file in an S3-compatible bucket, and a bounded
    cache of them on the node's disk (``<cache dir>/<workspace>/<name>``,
    downloads in progress under ``<workspace>/.partial/``). The cache's
    index is rebuilt from the directory at startup, ordered by each copy's
    mtime, which every use renews; one process uses a cache directory. A
    copy may be evicted while a reader has it open: on POSIX the reader
    keeps reading, on Windows the eviction skips it until the next one.

    Whether a file is in the bucket is asked of the bucket (``size``, a
    HEAD), never of the cache, which may outlive a bucket it was filled
    from; what this process put, fetched or saw there is remembered for
    KNOWN_TTL_S, and every delete goes through here and forgets it."""

    kind = "s3"

    def __init__(self, bucket: str, *, endpoint: str = "", region: str = "", access_key: str = "",
                 secret_key: str = "", prefix: str = "", cache_dir: Path | None = None,
                 cache_bytes: int = DEFAULT_CACHE_BYTES, presign: bool = True):
        if not bucket:
            raise BlobConfigError("GAMMA_BLOBS=s3 needs the bucket's name in GAMMA_S3_BUCKET")
        if bool(access_key) != bool(secret_key):
            raise BlobConfigError("set both GAMMA_S3_ACCESS_KEY and GAMMA_S3_SECRET_KEY, or neither")
        try:
            import boto3
            from boto3.exceptions import Boto3Error
            from botocore.config import Config
            from botocore.exceptions import BotoCoreError, ClientError
        except ImportError as e:
            raise BlobConfigError("GAMMA_BLOBS=s3 needs boto3: pip install -r requirements-s3.txt") from e
        session = boto3.session.Session(aws_access_key_id=access_key or None,
                                        aws_secret_access_key=secret_key or None, region_name=region or None)
        if session.get_credentials() is None:
            raise BlobConfigError("no credentials for the bucket: set GAMMA_S3_ACCESS_KEY and GAMMA_S3_SECRET_KEY")
        self.client = session.client("s3", endpoint_url=endpoint or None, config=Config(
            signature_version="s3v4",
            # a bucket behind its own endpoint (MinIO, R2) is addressed by path:
            # bucket.minio:9000 resolves nowhere
            s3={"addressing_style": "path" if endpoint else "auto"},
            # the checksums boto3 adds by default are refused by some S3-compatible stores
            request_checksum_calculation="when_required", response_checksum_validation="when_required",
            retries={"max_attempts": 3, "mode": "standard"}))
        self._client_error, self._boto_error = ClientError, BotoCoreError
        self._transfer_error = Boto3Error  # the managed transfers' own (an upload refused, retries run out)
        self.bucket = bucket
        self.prefix = key_prefix(prefix)
        self.where = f"s3://{bucket}/{self.prefix}" + (f" at {endpoint}" if endpoint else "")
        self.cache_dir = Path(cache_dir) if cache_dir else config.DATA_DIR / "cache" / "uploads"
        self.cache_bytes = cache_bytes
        self.presign = presign
        self._lock = threading.Lock()
        self._index: OrderedDict = OrderedDict()  # (ws, name) -> bytes, least recently used first
        self._bytes = 0
        self._known: OrderedDict = OrderedDict()  # (ws, name) -> (monotonic expiry, size)
        self._usage: dict = {}                     # ws -> (monotonic expiry, {name: size})
        self._fetching = [threading.Lock() for _ in range(FETCH_LOCKS)]
        self._load_cache()

    @classmethod
    def from_env(cls, env: dict) -> S3Blobs:
        raw = env["cache_bytes"]
        try:
            cache_bytes = int(raw) if raw else DEFAULT_CACHE_BYTES
        except ValueError:
            cache_bytes = -1
        if cache_bytes < 0:
            raise BlobConfigError(f"GAMMA_BLOB_CACHE_BYTES={raw!r} is not a number of bytes")
        return cls(env["bucket"], endpoint=env["endpoint"], region=env["region"], access_key=env["access_key"],
                   secret_key=env["secret_key"], prefix=env["prefix"], cache_dir=env["cache_dir"] or None,
                   cache_bytes=cache_bytes, presign=env["presign"])

    # -- keys, errors

    def _ws_prefix(self, ws: str) -> str:
        return f"{self.prefix}uploads/{safe_ws_id(ws)}/"

    def _key(self, ws: str, name: str) -> str:
        return self._ws_prefix(ws) + check_name(name)

    def _missing(self, e) -> bool:
        """A key that is not there. A bucket that is not there answers 404
        too and is an error of the store, never a missing file."""
        err = e.response.get("Error", {}).get("Code", "")
        if err == "NoSuchBucket":
            return False
        return err in ("404", "NoSuchKey", "NotFound") or \
            e.response.get("ResponseMetadata", {}).get("HTTPStatusCode") == 404

    def _call(self, what: str, fn, **kwargs):
        """``fn(**kwargs)``: None when the key is not there, a BlobError for
        any other failure."""
        try:
            return fn(**kwargs)
        except self._client_error as e:
            if self._missing(e):
                return None
            raise BlobError(f"{what} in {self.where}: {e}") from e
        except self._boto_error as e:
            raise BlobError(f"{what} in {self.where}: {e}") from e

    def check(self) -> None:
        """One listing of the bucket under the prefix (the driver lists, so
        its key must be allowed to), and a cache directory it can write."""
        try:
            self.client.list_objects_v2(Bucket=self.bucket, Prefix=f"{self.prefix}uploads/", MaxKeys=1)
        except self._client_error as e:
            code = e.response.get("Error", {}).get("Code", "")
            status = e.response.get("ResponseMetadata", {}).get("HTTPStatusCode")
            if code in ("NoSuchBucket", "404") or status == 404:
                raise BlobConfigError(f"there is no bucket {self.bucket!r} ({self.where}): check GAMMA_S3_BUCKET "
                                      "and GAMMA_S3_ENDPOINT") from e
            if status in (401, 403):
                raise BlobConfigError(f"{self.where} refused these credentials ({code}): check GAMMA_S3_ACCESS_KEY, "
                                      "GAMMA_S3_SECRET_KEY and what the key may do (list, read, write, "
                                      "delete)") from e
            raise BlobConfigError(f"{self.where} answered {code or status}: {e}") from e
        except self._boto_error as e:
            raise BlobConfigError(f"cannot reach {self.where}: {e}") from e
        try:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            raise BlobConfigError(f"GAMMA_BLOB_CACHE_DIR {self.cache_dir} cannot be created: {e}") from e

    # -- what this process knows is in the bucket

    def _know(self, ws: str, name: str, size: int | None) -> None:
        with self._lock:
            self._known.pop((ws, name), None)
            if size is not None:
                self._known[(ws, name)] = (time.monotonic() + KNOWN_TTL_S, size)
                while len(self._known) > KNOWN_MAX:
                    self._known.popitem(last=False)

    def _known_size(self, ws: str, name: str) -> int | None:
        with self._lock:
            hit = self._known.get((ws, name))
        return hit[1] if hit and hit[0] > time.monotonic() else None

    def _usage_set(self, ws: str, name: str, size: int | None) -> None:
        with self._lock:
            hit = self._usage.get(ws)
            if hit:
                if size is None:
                    hit[1].pop(name, None)
                else:
                    hit[1][name] = size

    # -- the cache

    def _cache_path(self, ws: str, name: str) -> Path:
        return self.cache_dir / safe_ws_id(ws) / check_name(name)

    def _load_cache(self) -> None:
        entries = []
        if self.cache_dir.is_dir():
            for d in self.cache_dir.iterdir():
                if not d.is_dir() or d.name.startswith("."):
                    continue
                _sweep(d / ".partial", 0)  # what a killed download left
                entries += [(st.st_mtime, (d.name, name), st.st_size) for name, st in _files(d)]
        with self._lock:
            for _, key, size in sorted(entries):
                self._index[key] = size
                self._bytes += size
            self._evict(None)

    def _evict(self, keep) -> None:
        """Under the lock: remove the copies used longest ago until the cache
        is within its bytes (never ``keep``, the copy just admitted)."""
        if self._bytes <= self.cache_bytes:
            return
        for key in [k for k in self._index if k != keep]:
            if self._bytes <= self.cache_bytes:
                break
            try:
                self._cache_path(*key).unlink(missing_ok=True)
            except OSError:
                continue  # held open (Windows): the next eviction tries again
            self._bytes -= self._index.pop(key)

    def _admit(self, key, size: int) -> None:
        with self._lock:
            self._bytes += size - self._index.pop(key, 0)
            self._index[key] = size
            self._evict(key)

    def _uncache(self, key) -> None:
        with self._lock:
            self._bytes -= self._index.pop(key, 0)
        try:
            self._cache_path(*key).unlink(missing_ok=True)
        except OSError:
            pass

    def _cached(self, key, path: Path) -> bool:
        with self._lock:
            if key not in self._index:
                return False
            if not path.is_file():  # removed behind the cache's back
                self._bytes -= self._index.pop(key)
                return False
            self._index.move_to_end(key)
        try:
            os.utime(path)  # the last use, which orders the evictions after a restart too
        except OSError:
            pass
        return True

    # -- the interface

    def put(self, ws: str, name: str, data: bytes) -> None:
        from .storage import write_atomic  # local: storage imports this module

        self._call(f"PUT {name}", self.client.put_object, Bucket=self.bucket, Key=self._key(ws, name), Body=data)
        self._know(ws, name, len(data))
        self._usage_set(ws, name, len(data))
        key, path = (ws, name), self._cache_path(ws, name)
        try:
            write_atomic(path, data)  # read again soon, mostly: the manifest walk, an export
        except OSError as e:
            self._uncache(key)  # never an older copy of the name
            log.warning(f"[blobs] {name} is stored but not cached on this node: {e}")
            return
        self._admit(key, len(data))

    def size(self, ws: str, name: str) -> int | None:
        known = self._known_size(ws, name)
        if known is not None:
            return known
        head = self._call(f"HEAD {name}", self.client.head_object, Bucket=self.bucket, Key=self._key(ws, name))
        size = head["ContentLength"] if head else None
        self._know(ws, name, size)
        return size

    def exists(self, ws: str, name: str) -> bool:
        return self.size(ws, name) is not None

    def open_path(self, ws: str, name: str) -> Path | None:
        key, path = (ws, name), self._cache_path(ws, name)
        if self._cached(key, path):
            return path
        with self._fetching[hash(key) % FETCH_LOCKS]:
            if self._cached(key, path):
                return path
            got = self._call(f"GET {name}", self.client.get_object, Bucket=self.bucket, Key=self._key(ws, name))
            if got is None:
                self._know(ws, name, None)
                return None
            partial = path.parent / ".partial"
            partial.mkdir(parents=True, exist_ok=True)
            tmp = partial / secrets.token_hex(8)
            body = got["Body"]
            try:
                with open(tmp, "xb") as f:
                    shutil.copyfileobj(body, f, 1 << 20)  # botocore refuses a body shorter than announced
                os.replace(tmp, path)
            except self._boto_error as e:
                tmp.unlink(missing_ok=True)
                raise BlobError(f"GET {name} in {self.where}: {e}") from e
            except BaseException:
                tmp.unlink(missing_ok=True)
                raise
            finally:
                body.close()
            size = got["ContentLength"]
            self._admit(key, size)
            self._know(ws, name, size)
        return path

    def delete(self, ws: str, name: str) -> None:
        self._call(f"DELETE {name}", self.client.delete_object, Bucket=self.bucket, Key=self._key(ws, name))
        self._uncache((ws, name))
        self._know(ws, name, None)
        self._usage_set(ws, name, None)

    def _keys(self, prefix: str):
        try:
            for page in self.client.get_paginator("list_objects_v2").paginate(Bucket=self.bucket, Prefix=prefix):
                for obj in page.get("Contents", ()):
                    yield obj
        except (self._client_error, self._boto_error) as e:
            raise BlobError(f"LIST {prefix} in {self.where}: {e}") from e

    def delete_workspace(self, ws: str) -> None:
        keys = [obj["Key"] for obj in self._keys(self._ws_prefix(ws))]
        for i in range(0, len(keys), 1000):
            out = self._call(f"DELETE workspace {ws}", self.client.delete_objects, Bucket=self.bucket,
                             Delete={"Objects": [{"Key": k} for k in keys[i:i + 1000]], "Quiet": True})
            if out and out.get("Errors"):
                raise BlobError(f"DELETE workspace {ws} in {self.where}: {len(out['Errors'])} object(s) refused, "
                                f"the first {out['Errors'][0].get('Key')}: {out['Errors'][0].get('Message')}")
        with self._lock:
            for key in [k for k in self._index if k[0] == ws]:
                self._bytes -= self._index.pop(key)
            for key in [k for k in self._known if k[0] == ws]:
                del self._known[key]
            self._usage.pop(ws, None)
        shutil.rmtree(self.cache_dir / safe_ws_id(ws), ignore_errors=True)

    def list(self, ws: str):
        prefix = self._ws_prefix(ws)
        out = []
        for obj in self._keys(prefix):
            name = obj["Key"][len(prefix):]
            if name and "/" not in name and not name.startswith("."):
                out.append((name, obj["Size"], obj["LastModified"].timestamp()))
        with self._lock:
            self._usage[ws] = (time.monotonic() + USAGE_TTL_S, {n: size for n, size, _ in out})
        return out

    def touch(self, ws: str, name: str) -> bool:
        # an object's date is when it was written: a copy onto itself with
        # new metadata writes it again (the bytes never leave the bucket)
        key = self._key(ws, name)
        done = self._call(f"COPY {name}", self.client.copy_object, Bucket=self.bucket, Key=key,
                          CopySource={"Bucket": self.bucket, "Key": key}, MetadataDirective="REPLACE",
                          Metadata={"touched": str(int(time.time()))})
        if done is None:
            self._know(ws, name, None)
            return False
        return True

    def usage(self, ws: str) -> int:
        with self._lock:
            hit = self._usage.get(ws)
            if hit and hit[0] > time.monotonic():
                return sum(hit[1].values())
        return sum(size for _, size, _ in self.list(ws))

    def url(self, ws: str, name: str, *, media_type: str, disposition: str | None = None,
            ttl: int = 300) -> str | None:
        if not self.presign:
            return None
        params = {"Bucket": self.bucket, "Key": self._key(ws, name), "ResponseContentType": media_type}
        if disposition:
            params["ResponseContentDisposition"] = disposition
        return self.client.generate_presigned_url("get_object", Params=params, ExpiresIn=int(ttl))

    def sweep_partial(self, ws: str, max_age_s: float) -> int:
        return _sweep(self.cache_dir / safe_ws_id(ws) / ".partial", max_age_s)

    # -- the object calls: <prefix><key>, streamed by boto3's managed transfers
    #    (in parts past 8 MB), never through the cache

    def _object_key(self, key: str) -> str:
        return self.prefix + check_key(key)

    def put_object(self, key: str, path: Path) -> None:
        try:
            self.client.upload_file(str(path), self.bucket, self._object_key(key))
        except (self._client_error, self._boto_error, self._transfer_error) as e:
            raise BlobError(f"PUT {key} in {self.where}: {e}") from e

    def get_object(self, key: str, path: Path) -> bool:
        full = self._object_key(key)
        try:
            _write_whole(Path(path), lambda f: self.client.download_fileobj(self.bucket, full, f))
        except self._client_error as e:
            if self._missing(e):
                return False
            raise BlobError(f"GET {key} in {self.where}: {e}") from e
        except (self._boto_error, self._transfer_error) as e:
            raise BlobError(f"GET {key} in {self.where}: {e}") from e
        return True

    def list_objects(self, prefix: str):
        full = self.prefix + check_key(prefix, prefix=True)
        return [(obj["Key"][len(self.prefix):], obj["Size"], obj["LastModified"].timestamp())
                for obj in self._keys(full)]

    def delete_object(self, key: str) -> None:
        self._call(f"DELETE {key}", self.client.delete_object, Bucket=self.bucket, Key=self._object_key(key))


# --- the active driver ---------------------------------------------------------------

_driver = None
_driver_lock = threading.Lock()


def _from_env():
    env = config.blob_env()
    if env["kind"] == "local":
        return LocalBlobs()
    if env["kind"] == "s3":
        return S3Blobs.from_env(env)
    raise BlobConfigError(f"GAMMA_BLOBS={env['kind']!r}: use local (the default) or s3")


def driver():
    """The store the environment names, made on first use. BlobConfigError
    when it names none that can work."""
    global _driver
    if _driver is None:
        with _driver_lock:
            if _driver is None:
                _driver = _from_env()
    return _driver


def use(store):
    """Make ``store`` the active driver (tests); returns the one before."""
    global _driver
    with _driver_lock:
        before, _driver = _driver, store
    return before


def check() -> str:
    """At startup: the driver made and, for a bucket, a listing of it, so a
    misconfigured store stops the server with its reason instead of failing
    the first upload. Returns the driver's kind; raises BlobConfigError."""
    store = driver()
    store.check()
    return store.kind


def put(ws: str, name: str, data: bytes) -> None:
    """Store ``data`` as ``name``, whole: an existing file of the name is
    replaced at once, never left half written."""
    driver().put(ws, name, data)


def exists(ws: str, name: str) -> bool:
    return driver().exists(ws, name)


def size(ws: str, name: str) -> int | None:
    """The stored file's bytes, None when there is none."""
    return driver().size(ws, name)


def open_path(ws: str, name: str) -> Path | None:
    """A local file holding the stored file's bytes, None when there is
    none: the file itself (local) or the node's cached copy, downloaded on
    a miss (s3). Read it soon; a cached copy may be evicted later."""
    return driver().open_path(ws, name)


def delete(ws: str, name: str) -> None:
    """Remove the stored file; one already gone is no error."""
    driver().delete(ws, name)


def delete_workspace(ws: str) -> None:
    """Remove every stored file of the workspace (it is being deleted)."""
    driver().delete_workspace(ws)


def touch(ws: str, name: str) -> bool:
    """Date the stored file now (the upload GC's clocks start over);
    False when there is no such file."""
    return driver().touch(ws, name)


def usage(ws: str) -> int:
    """Bytes the workspace's stored files take: listed once, then remembered
    for USAGE_TTL_S and kept current by this process's puts and deletes
    (locally also listed again once the directory changed under it)."""
    return driver().usage(ws)


def url(ws: str, name: str, *, media_type: str, disposition: str | None = None, ttl: int = 300) -> str | None:
    """A presigned GET of the stored file that answers with ``media_type``
    (and ``disposition``) and expires after ``ttl`` seconds; None when the
    node serves the bytes itself (local, or GAMMA_S3_PRESIGN off)."""
    return driver().url(ws, name, media_type=media_type, disposition=disposition, ttl=ttl)


def sweep_partial(ws: str, max_age_s: float) -> int:
    """Remove the workspace's temp files of writes or downloads older than
    ``max_age_s`` (a killed process left them); how many went."""
    return driver().sweep_partial(ws, max_age_s)


def put_object(key: str, path: Path) -> None:
    """Store the local file at ``path`` as the object ``key`` (``check_key``:
    under one of OBJECT_SPACES), streamed; an object of the key is
    replaced whole."""
    driver().put_object(key, path)


def get_object(key: str, path: Path) -> bool:
    """Download the object ``key`` into ``path``, written whole (a temp file
    beside it, renamed over it); False when there is no such object."""
    return driver().get_object(key, path)


def list_objects(prefix: str):
    """``(key, size, mtime)`` of each object whose key starts with
    ``prefix``, sorted by key."""
    return driver().list_objects(prefix)


def delete_object(key: str) -> None:
    """Remove the object; one already gone is no error."""
    driver().delete_object(key)


def list(ws: str):  # noqa: A001 — last in the module: it shadows the builtin below it
    """``(name, size, mtime)`` of each of the workspace's stored files."""
    return driver().list(ws)
