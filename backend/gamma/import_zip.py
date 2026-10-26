"""The guarded zip walk the library imports share: the Markdown zip import
(gamma/markdown_zip_import.py) and the Notability import
(gamma/notability_import.py) read an untrusted archive through it.

There is no cap on the size of an archive or its number of files. What
guards the server instead:

- a zip-bomb rule (Apache POI's): an entry past RATIO_GRACE bytes that
  unpacks to more than MAX_RATIO times its packed size is not read, and an
  archive that does so as a whole is refused (``check_ratio``). Notes pack
  about 4:1 and photos or PDFs 1:1, a zip bomb 1000:1;
- the central directory's size, read from the end record before zipfile
  parses it (``open_zip``): zipfile reads the whole directory into memory
  and builds an object per entry until those bytes run out, and never
  reads the entry count the end record states, so the directory's size is
  what bounds that work;
- zips inside the zip are opened in place, each spooled to an anonymous
  temporary file its walk's ExitStack closes, to MAX_ZIP_DEPTH levels and
  at most MAX_NESTED_ZIPS of them, and only while the disk keeps
  ``import_staging.MIN_FREE_BYTES`` free (507), as a staged upload must.

Nothing is extracted by name: a member is read into memory or streamed,
and its name is only a lookup key.
"""

import contextlib
import posixpath
import shutil
import struct
import tempfile
import unicodedata
import zipfile
import zlib

from fastapi import HTTPException

from .import_staging import MIN_FREE_BYTES

MAX_RATIO = 100
RATIO_GRACE = 1 << 20
# Zips inside the zip are opened (Notion's Part-N.zip, a zipped export) to
# this depth. Each one opened holds a temporary file until its walk ends,
# hence a ceiling on how many, far above Notion's parts.
MAX_ZIP_DEPTH = 2
MAX_NESTED_ZIPS = 500
# What reading one damaged, encrypted or oddly compressed member raises.
READ_ERRORS = (zipfile.BadZipFile, zlib.error, EOFError, OSError, RuntimeError, NotImplementedError)

_EOCD = b"PK\x05\x06"
_EOCD_SIZE = 22
_ZIP64_LOCATOR = b"PK\x06\x07"
_ZIP64_LOCATOR_SIZE = 20
_ZIP64_EOCD = b"PK\x06\x06"
_ZIP64_EOCD_SIZE = 56
_MAX_COMMENT = 0xFFFF


class ZipEntry:
    """One member of a walked archive: ``path`` (its place in the walk, a
    nested zip's members below the zip's own directory), ``source_path``
    (where it was found), ``outer`` (the upload's own member it came from:
    itself, or the zip in the upload that holds it, however deep it lies
    in there), ``size`` (its unpacked bytes, as
    the directory says) and ``depth`` (that of the zip holding it, 0 for
    the upload)."""

    __slots__ = ("path", "source_path", "outer", "size", "depth", "_zf", "_info")

    def __init__(self, path, zf, info, depth=0, outer=None):
        self.path = path
        self.source_path = path
        self.outer = outer or path
        self.size = info.file_size
        self.depth = depth
        self._zf, self._info = zf, info

    def read(self) -> bytes:
        return self._zf.read(self._info)

    def head(self, n: int) -> bytes:
        with self._zf.open(self._info) as f:
            return f.read(n)

    def open(self):
        """The member as a binary file, seekable when its archive is: for
        streaming it into storage and for a walk over its boxes."""
        return self._zf.open(self._info)


def new_walk(stack: contextlib.ExitStack, *, max_directory: int | None = None) -> dict:
    """The state of one walk: the ExitStack the zips it opens are closed
    by, how many it opened, ``[path, reason]`` for what it left out, the
    sizes the whole archive's ratio is checked on, and the largest central
    directory a nested zip may have (None: no cap)."""
    return {"stack": stack, "zips": 0, "left_out": [], "unpacked": 0, "packed": 0,
            "max_directory": max_directory}


def is_zip(name: str) -> bool:
    """The Markdown import's rule: a member named ``*.zip`` is a zip to open
    in place."""
    return name.lower().endswith(".zip")


def entry_name(zi) -> str:
    name = zi.filename
    if not (zi.flag_bits & 0x800):  # no UTF-8 flag: zipfile decoded cp437
        try:
            name = name.encode("cp437").decode("utf-8")
        except (UnicodeEncodeError, UnicodeDecodeError):
            pass
    return unicodedata.normalize("NFC", name.replace("\\", "/")).lstrip("/")


def walk_zip(zf, prefix: str, out: list, walk: dict, depth: int = 0, *, nested=is_zip, outer=None) -> None:
    """Collect ``zf``'s files as ZipEntry objects in ``out``. ``nested(path)``
    says whether a member is a zip to open in place (``open_nested``): its
    members then become its siblings (Notion's Part-N.zip), each keeping
    the upload's member it was found in (``outer``). macOS litter
    (``__MACOSX/``, ``.DS_Store``, ``._*``) and members with an empty name
    or path segment are passed by; a member that unpacks like a zip bomb
    is left out with the reason in ``walk``."""
    for zi in zf.infolist():
        if zi.is_dir():
            continue
        name = entry_name(zi)
        parts = name.split("/")
        if any(p in ("__MACOSX", ".DS_Store", "") or p.startswith("._") for p in parts):
            continue
        full = f"{prefix}{name}"
        if zi.file_size > RATIO_GRACE and zi.file_size > MAX_RATIO * zi.compress_size:
            walk["left_out"].append([full, f"unpacks to over {MAX_RATIO} times its packed size, "
                                           "like a zip bomb: not read"])
            continue
        walk["unpacked"] += zi.file_size
        walk["packed"] += zi.compress_size
        entry = ZipEntry(full, zf, zi, depth, outer)
        if nested(full):
            inner = open_nested(entry, walk)
            if inner is not None:
                walk_zip(inner, posixpath.dirname(full) + "/" if "/" in full else "", out, walk, depth + 1,
                         nested=nested, outer=entry.outer)
                continue
        out.append(entry)


def open_nested(entry: ZipEntry, walk: dict) -> zipfile.ZipFile | None:
    """The zip ``entry`` holds, spooled to a temporary file, never held in
    memory (a Notion part is gigabytes) — or None, with the reason in
    ``walk``, when it is not opened: too deep, one too many, a central
    directory past ``walk["max_directory"]``, or no zip after all. It then
    stays a file, which a note may link. 507 when spooling it would leave
    the disk with less than MIN_FREE_BYTES free. The file and the ZipFile
    close with the walk's ExitStack."""
    if entry.depth >= MAX_ZIP_DEPTH:
        walk["left_out"].append([entry.path, f"a zip nested more than {MAX_ZIP_DEPTH} deep is not opened: "
                                             "its notes are not imported"])
        return None
    if walk["zips"] >= MAX_NESTED_ZIPS:
        walk["left_out"].append([entry.path, f"more than {MAX_NESTED_ZIPS} zips inside the zip: this one is not "
                                             "opened, its notes are not imported"])
        return None
    if shutil.disk_usage(tempfile.gettempdir()).free - entry.size < MIN_FREE_BYTES:
        raise HTTPException(status_code=507, detail="the server's disk has no room to unpack this zip")
    spool = tempfile.TemporaryFile()
    try:
        with entry.open() as src:
            shutil.copyfileobj(src, spool, 1 << 20)
        inner = open_zip(spool, max_directory=walk.get("max_directory"))
    except BaseException as exc:
        spool.close()
        if isinstance(exc, zipfile.BadZipFile):
            walk["left_out"].append([entry.path, "not a readable zip: not opened"])
            return None
        if isinstance(exc, DirectoryTooLarge):
            walk["left_out"].append([entry.path, "lists more files than this import reads: not opened"])
            return None
        raise
    walk["stack"].callback(spool.close)  # a ZipFile leaves a file it was handed open
    walk["stack"].enter_context(inner)
    walk["zips"] += 1
    return inner


def check_ratio(walk: dict) -> None:
    """413 when the archive as a whole unpacks like a zip bomb: past
    RATIO_GRACE and over MAX_RATIO times its packed size."""
    if walk["unpacked"] > RATIO_GRACE and walk["unpacked"] > MAX_RATIO * walk["packed"]:
        raise HTTPException(status_code=413, detail=f"this zip unpacks to over {MAX_RATIO} times its size, "
                                                    "like a zip bomb: refused")


class DirectoryTooLarge(ValueError):
    """``open_zip`` refused a zip whose central directory is past its cap."""


def directory_size(f) -> int | None:
    """The size in bytes of the central directory zipfile would read from
    ``f`` (a seekable binary file), from the end record in the file's last
    65,557 bytes, found as zipfile finds it: the ZIP64 record's size when a
    ZIP64 locator precedes the end record (zipfile then reads that one), else
    the classic record's. None when there is no end record. ``f`` is left
    at offset 0."""
    try:
        f.seek(0, 2)
        filesize = f.tell()
        start = max(0, filesize - _MAX_COMMENT - _EOCD_SIZE)
        f.seek(start)
        tail = f.read(filesize - start)
        at = len(tail) - _EOCD_SIZE
        if not (at >= 0 and tail[at:at + 4] == _EOCD and tail[-2:] == b"\0\0"):
            at = tail.rfind(_EOCD)
        if at < 0 or at + _EOCD_SIZE > len(tail):
            return None
        size = struct.unpack("<12xI6x", tail[at:at + _EOCD_SIZE])[0]
        record = start + at  # the end record's offset in the file
        if record >= _ZIP64_LOCATOR_SIZE:
            f.seek(record - _ZIP64_LOCATOR_SIZE)
            locator = f.read(_ZIP64_LOCATOR_SIZE)
            if len(locator) == _ZIP64_LOCATOR_SIZE and locator[:4] == _ZIP64_LOCATOR:
                reloff = struct.unpack("<4sIQI", locator)[2]
                # where the record says it is, else right before the locator
                for offset in (reloff, record - _ZIP64_LOCATOR_SIZE - _ZIP64_EOCD_SIZE):
                    if 0 <= offset <= filesize - _ZIP64_EOCD_SIZE:
                        f.seek(offset)
                        data = f.read(_ZIP64_EOCD_SIZE)
                        if data[:4] == _ZIP64_EOCD:
                            return struct.unpack("<40xQ8x", data)[0]
        return size
    finally:
        f.seek(0)


def open_zip(f, *, max_directory: int | None = None) -> zipfile.ZipFile:
    """``zipfile.ZipFile(f)``, refused first (``DirectoryTooLarge``, a
    ValueError) when ``max_directory`` is set and the central directory is
    larger."""
    if max_directory is not None:
        size = directory_size(f)
        if size is not None and size > max_directory:
            raise DirectoryTooLarge(f"a central directory of {size} bytes, past {max_directory}")
    return zipfile.ZipFile(f)
