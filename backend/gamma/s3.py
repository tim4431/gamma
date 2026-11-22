"""The S3-compatible bucket the backup tasks' snapshots and the off-site
copies go to (gamma/snapshot_copies.py, gamma/offsite.py,
docs/dev/debugging.md "Snapshots and copies in a bucket"): AWS S3,
Cloudflare R2, MinIO, through boto3, which is imported only when a
``Client`` is made (``requirements-s3.txt``: the Docker image and the
desktop app have it, a plain checkout does not).

A ``Client`` is a backup store (gamma/backup_store.py ``Store``) that
keeps whole files under the bucket's prefix: ``put_object`` (a
local file, streamed up by boto3's managed transfer, in parts past 8 MB),
``get_object`` (down into a local file, written whole), ``put_bytes`` and
``get_bytes`` (a small object in one request), ``list_objects``
(``(key, size, mtime)`` under a prefix), ``delete_object``, and
``browse`` (a folder's listing, its keys split at ``/``). ``check``
lists the prefix once and says why a bucket cannot work. Keys are relative
to the prefix and checked by ``backup_store.check_key``. A call the bucket
refuses, or cannot be reached for, raises ``S3Error``, a ``StoreError``;
settings that name no bucket that can work raise ``S3ConfigError``, a
``StoreConfigError``.
"""

from __future__ import annotations

import os
import secrets
from datetime import timezone
from pathlib import Path

from . import net_proxy
from .backup_store import StoreConfigError, StoreError, check_key

CONNECT_TIMEOUT_S = 10
READ_TIMEOUT_S = 60
LIST_PAGE = 1000  # what one listing returns at most, and what ``check`` counts up to


class S3Error(StoreError):
    """The bucket refused a call or could not be reached."""


class S3ConfigError(StoreConfigError):
    """Settings that name no bucket that can work: none named, boto3 not
    installed, half a key pair, no credentials, an endpoint that is no URL,
    or (``check``) a bucket that is not there, keys it refuses, an endpoint
    out of reach."""


def key_prefix(prefix: str) -> str:
    """The prefix as the keys begin with it: ``tenant-1/`` for ``tenant-1``
    or ``/tenant-1/``, "" for none."""
    prefix = (prefix or "").strip().strip("/")
    return prefix + "/" if prefix else ""


def where(bucket: str, endpoint: str = "", prefix: str = "") -> str:
    """The bucket and prefix as the log, the state file and the restore's
    messages name them: ``s3://<bucket>/<prefix>`` and the endpoint."""
    return f"s3://{bucket}/{key_prefix(prefix)}" + (f" at {endpoint}" if endpoint else "")


class Client:
    """One bucket under one prefix. ``attempts``: how often a call is tried
    (boto3's standard retries); the settings' Test button tries once."""

    def __init__(self, bucket: str, *, endpoint: str = "", region: str = "", access_key: str = "",
                 secret_key: str = "", prefix: str = "", attempts: int = 3):
        if not bucket:
            raise S3ConfigError("no bucket is set")
        if bool(access_key) != bool(secret_key):
            raise S3ConfigError("give both the access key and the secret key, or neither")
        try:
            import boto3
            from boto3.exceptions import Boto3Error
            from botocore.config import Config
            from botocore.exceptions import BotoCoreError, ClientError
        except ImportError as e:
            raise S3ConfigError("the bucket needs boto3, which is not installed: "
                                "pip install -r requirements-s3.txt") from e
        session = boto3.session.Session(aws_access_key_id=access_key or None,
                                        aws_secret_access_key=secret_key or None, region_name=region or None)
        if session.get_credentials() is None:
            raise S3ConfigError("no credentials for the bucket: give its access key and secret key")
        try:
            self.client = session.client("s3", endpoint_url=endpoint or None, config=Config(
                signature_version="s3v4",
                # a bucket behind its own endpoint (MinIO, R2) is addressed by
                # path: bucket.minio:9000 resolves nowhere
                s3={"addressing_style": "path" if endpoint else "auto"},
                # the checksums boto3 adds by default are refused by some S3-compatible stores
                request_checksum_calculation="when_required", response_checksum_validation="when_required",
                connect_timeout=CONNECT_TIMEOUT_S, read_timeout=READ_TIMEOUT_S,
                retries={"max_attempts": max(1, attempts), "mode": "standard"},
                # the server's network route, never botocore's own reading of the environment
                proxies=net_proxy.botocore_proxies(endpoint or None)))
        except ValueError as e:  # an endpoint boto3 cannot use
            raise S3ConfigError(f"the endpoint {endpoint!r} cannot be used: {e}") from e
        self._client_error, self._boto_error = ClientError, BotoCoreError
        self._transfer_error = Boto3Error  # the managed transfers' own (an upload refused, retries run out)
        self.bucket = bucket
        self.prefix = key_prefix(prefix)
        self.where = where(bucket, endpoint, prefix)

    @classmethod
    def from_settings(cls, conf: dict, **kwargs) -> Client:
        """A client for the off-site settings (gamma/offsite.py ``settings``)."""
        return cls(conf["bucket"], endpoint=conf.get("endpoint", ""), region=conf.get("region", ""),
                   access_key=conf.get("access_key", ""), secret_key=conf.get("secret_key", ""),
                   prefix=conf.get("prefix", ""), **kwargs)

    def _missing(self, e) -> bool:
        """A key that is not there. A bucket that is not there answers 404
        too and is an error, never a missing object."""
        err = e.response.get("Error", {}).get("Code", "")
        if err == "NoSuchBucket":
            return False
        return err in ("404", "NoSuchKey", "NotFound") or \
            e.response.get("ResponseMetadata", {}).get("HTTPStatusCode") == 404

    def _listed(self, **params) -> dict:
        """One page of ListObjectsV2 under ``params``. S3ConfigError with
        the reason when the bucket cannot be used: none of that name, keys
        it refuses, an endpoint out of reach."""
        try:
            return self.client.list_objects_v2(Bucket=self.bucket, MaxKeys=LIST_PAGE, **params)
        except self._client_error as e:
            code = e.response.get("Error", {}).get("Code", "")
            status = e.response.get("ResponseMetadata", {}).get("HTTPStatusCode")
            if code in ("NoSuchBucket", "404") or status == 404:
                raise S3ConfigError(f"there is no bucket {self.bucket!r} ({self.where}): check the bucket's "
                                    "name and the endpoint") from e
            if status in (401, 403):
                raise S3ConfigError(f"{self.where} refused these credentials ({code}): check the access key, "
                                    "the secret key and what the key may do (list, read, write, delete)") from e
            raise S3ConfigError(f"{self.where} answered {code or status}: {e}") from e
        except self._boto_error as e:
            raise S3ConfigError(f"cannot reach {self.where}: {e}") from e

    def check(self) -> tuple[int, bool]:
        """One listing under the prefix (the copies list, so the key must be
        allowed to): how many objects it found, up to LIST_PAGE, and whether
        there are more. S3ConfigError with the reason when the bucket cannot
        be used."""
        page = self._listed(Prefix=self.prefix)
        return int(page.get("KeyCount", len(page.get("Contents", ())))), bool(page.get("IsTruncated"))

    def browse(self, path: str) -> dict:
        """What the folder ``path`` (a key's segments under the prefix; ""
        the prefix itself) holds right in it, for the pane's folder browser:
        ``{folders: [{name, modified: ""}], files: [{name, size, modified}],
        more}``, one listing split at ``/``: the names under it up to the
        next ``/`` are its folders, the objects right in it its files;
        ``more`` when the page was cut. A bucket has no folders of its own:
        one is there while a key is under it, and holds nothing before.
        S3ConfigError, as ``check``, when the bucket cannot be used."""
        full = self.prefix + (check_key(path) + "/" if path else "")
        page = self._listed(Prefix=full, Delimiter="/")
        folders = [{"name": name, "modified": ""} for p in page.get("CommonPrefixes", ())
                   if (name := p["Prefix"][len(full):-1])]  # "a//b" has a folder of no name, which no key can name
        files = [{"name": o["Key"][len(full):], "size": o["Size"],
                  "modified": o["LastModified"].astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}
                 for o in page.get("Contents", ()) if o["Key"] != full]  # a console's empty "folder/" object
        return {"folders": folders, "files": files, "more": bool(page.get("IsTruncated"))}

    def put_object(self, key: str, path: Path, tick=None) -> None:
        """Store the local file at ``path`` as ``key``, streamed; an object
        of the key is replaced whole. ``tick``, when given, is called with
        each run of bytes sent as they go (boto3's ``Callback``, from its
        transfer threads). A file that is not there raises
        FileNotFoundError, never an S3Error."""
        try:
            self.client.upload_file(str(path), self.bucket, self.prefix + check_key(key),
                                    **({"Callback": tick} if tick else {}))
        except (self._client_error, self._boto_error, self._transfer_error) as e:
            reason = str(e)
            if isinstance(e, self._transfer_error):  # "Failed to upload <local path> to <bucket>/<key>: <cause>"
                reason = reason.partition(": ")[2] or reason
            raise S3Error(f"PUT {key} in {self.where}: {reason}") from e

    def get_object(self, key: str, path: Path) -> bool:
        """Download ``key`` into ``path``, written whole (a dot-named temp
        file beside it, flushed and renamed over it); False when there is no
        such object."""
        path = Path(path)
        full = self.prefix + check_key(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f".{path.name}.{secrets.token_hex(4)}.part")
        try:
            with open(tmp, "xb") as f:
                self.client.download_fileobj(self.bucket, full, f)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, path)
        except self._client_error as e:
            tmp.unlink(missing_ok=True)
            if self._missing(e):
                return False
            raise S3Error(f"GET {key} in {self.where}: {e}") from e
        except (self._boto_error, self._transfer_error) as e:
            tmp.unlink(missing_ok=True)
            raise S3Error(f"GET {key} in {self.where}: {e}") from e
        except BaseException:
            tmp.unlink(missing_ok=True)
            raise
        return True

    def put_bytes(self, key: str, data: bytes) -> None:
        """Store ``data`` as ``key`` in one request (a small object)."""
        try:
            self.client.put_object(Bucket=self.bucket, Key=self.prefix + check_key(key), Body=data)
        except (self._client_error, self._boto_error) as e:
            raise S3Error(f"PUT {key} in {self.where}: {e}") from e

    def get_bytes(self, key: str) -> bytes | None:
        """A small object's bytes, read whole; None when there is no such
        object."""
        try:
            return self.client.get_object(Bucket=self.bucket, Key=self.prefix + check_key(key))["Body"].read()
        except self._client_error as e:
            if self._missing(e):
                return None
            raise S3Error(f"GET {key} in {self.where}: {e}") from e
        except self._boto_error as e:
            raise S3Error(f"GET {key} in {self.where}: {e}") from e

    def list_objects(self, prefix: str) -> list[tuple[str, int, float]]:
        """``(key, size, mtime)`` of each object whose key starts with
        ``prefix``, sorted by key; the keys relative to the client's prefix."""
        full = self.prefix + check_key(prefix, prefix=True)
        out = []
        try:
            for page in self.client.get_paginator("list_objects_v2").paginate(Bucket=self.bucket, Prefix=full):
                for obj in page.get("Contents", ()):
                    out.append((obj["Key"][len(self.prefix):], obj["Size"], obj["LastModified"].timestamp()))
        except (self._client_error, self._boto_error) as e:
            raise S3Error(f"LIST {prefix} in {self.where}: {e}") from e
        return sorted(out)

    def delete_object(self, key: str) -> None:
        """Remove the object; one already gone is no error."""
        try:
            self.client.delete_object(Bucket=self.bucket, Key=self.prefix + check_key(key))
        except self._client_error as e:
            if not self._missing(e):
                raise S3Error(f"DELETE {key} in {self.where}: {e}") from e
        except self._boto_error as e:
            raise S3Error(f"DELETE {key} in {self.where}: {e}") from e
