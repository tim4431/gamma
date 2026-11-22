"""What a backup store is: a place off this server that holds whole files
under keys, which the backup tasks' snapshot copies go to
(gamma/snapshot_copies.py) and the off-site rounds use (gamma/offsite.py).
Two kinds implement it, alike: ``s3.Client`` (an S3-compatible bucket) and
``gdrive.Client`` (a folder of a Google Drive). This module holds what they
share and nothing of either: the ``Store`` interface, the two errors, the
rule for a key, and the shape of a folder's listing (``listing``), which
the pane's folder browser reads (explorer/FolderBrowser.jsx).

A store keeps files under keys: segments joined by ``/``
(``snapshots/<ws>/<name>.zip``), relative to the store's own place (the
bucket's prefix, the Drive folder). A call the store refuses, or cannot be
reached for, raises ``StoreError``, an OSError, so the callers that handle
a failing disk handle a failing store alike; settings that name no store
that can work (none set up, a library missing, a connection Google no
longer honours) raise ``StoreConfigError``, a ValueError.
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable, Protocol


class StoreError(OSError):
    """The store refused a call or could not be reached."""


class StoreConfigError(ValueError):
    """No store that can work is set up."""


class Store(Protocol):
    """What a backup store's client does; ``where`` names it in the log and
    in messages."""

    where: str

    def check(self) -> tuple[int, bool]:
        """How many things it holds, up to a page, and whether there are
        more; StoreConfigError with the reason when it cannot be used."""

    def put_object(self, key: str, path: Path, tick: Callable[[int], None] | None = None) -> None:
        """Store the local file at ``path`` as ``key``, streamed, replacing
        what the key held; ``tick`` hears each run of bytes sent.
        FileNotFoundError, never a StoreError, for a file that is not there."""

    def get_object(self, key: str, path: Path) -> bool:
        """Download ``key`` into ``path``, written whole; False when there is
        no such file."""

    def put_bytes(self, key: str, data: bytes) -> None:
        """Store a small file in one go."""

    def get_bytes(self, key: str) -> bytes | None:
        """A small file's bytes; None when there is no such file."""

    def list_objects(self, prefix: str) -> list[tuple[str, int, float]]:
        """``(key, size, mtime)`` of each file whose key starts with
        ``prefix``, sorted by key."""

    def delete_object(self, key: str) -> None:
        """Remove the file; one already gone is no error."""

    def browse(self, path: str) -> dict:
        """What the folder ``path`` (a key's segments; "" the store's own
        place) holds right in it, as ``listing`` shapes it; a folder that
        is not there holds nothing."""


def check_key(key: str, *, prefix: bool = False) -> str:
    """``key`` when it can name a file: segments joined by ``/``, none
    empty, ``.`` or ``..``, none holding a backslash or a control character,
    at most 1024 characters. ``prefix``: a listing's, which may end in
    ``/``. ValueError otherwise."""
    if not isinstance(key, str) or not key or len(key) > 1024:
        raise ValueError(f"unsafe object key: {key!r}")
    parts = (key[:-1] if prefix and key.endswith("/") else key).split("/")
    for part in parts:
        if not part or part in (".", "..") or "\\" in part or any(ord(c) < 32 or ord(c) == 127 for c in part):
            raise ValueError(f"unsafe object key: {key!r}")
    return key


def listing(folders: list[dict], files: list[dict], *, more: bool = False) -> dict:
    """A folder's listing as the folder browser reads it: ``{folders:
    [{name, modified}], files: [{name, size, modified}], more}``, each list
    by name (``modified`` an ISO time, "" when the store keeps none);
    ``more`` when the store held more than one page and the rest were left
    out."""
    def by(entry):
        return entry["name"].casefold(), entry["name"]
    return {"folders": sorted(folders, key=by), "files": sorted(files, key=by), "more": more}
