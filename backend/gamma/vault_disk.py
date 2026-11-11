"""A synced vault's directory on this computer, as files (docs/research/vaults.md
"Where everything lives"): the marker, the scan, the evidence that a file
moved, the write that replaces a note, the vault's trash. It knows nothing of
pages: the rounds (gamma/vault_sync.py) read and write through it.

**The marker** is ``.gamma/vault.json`` (``make_marker``, once, when the vault
is made; no round changes it), in a hidden directory: the vault's id, so a
directory moved or copied is told from the vault it was. A round checks it
before and after its scan (``scan``): no marker, or another id, stops the vault
(``Stopped``); a root that does not answer, a walk that fails anywhere, or a
marker gone by the end is ``Offline``, never a partial scan that would read as
files removed. A round never makes the root: ``mkdir`` on an empty mount point
would hide a disk that is not plugged in.

**The scan** (``walk``) follows no link or junction and leaves out
dot-directories, ``node_modules``, the marker's ``ignore`` globs and the
temporary names of editors and of Gamma (``.gamma-*``, ``*.gamma-sync-part``,
``~$*``, ``.~lock.*``, macOS's ``._*``); a round reads and writes nothing it
would not see either (``path``: no ``..``, nothing below a link). One stat a file: notes (``.md``,
``.markdown``) and papers (``.pdf``) are listed, pictures and the rest only
counted. A name Windows refuses, and two names one directory holds that differ
only by case, are reported and left. A cloud placeholder (Windows' offline and
recall-on-access attributes, macOS's dataless flag, an iCloud ``.x.icloud``
stub standing for ``x``) is listed as one: never read, never removed.

**A move** is told by evidence (``pair_moves``), in this order: the same file
id with the same size, hash, PDF ``/ID`` or a similar text; a hash no other file
on either side has; a PDF's trailer ``/ID``, the same way; a note's text, at
least ``MOVE_SIMILAR`` alike, the name only breaking ties, ``MOVE_COMPARES``
comparisons at most. A file whose time is within ``RACY_S`` of when it was read
counts as changed at the next scan, whatever its stat says (``moved``): a
coarse clock (exFAT, SMB) gives a save in the same tick the same time.

**A note is replaced** (``replace_note``) by a file written beside it under a
name the scan skips, flushed, its stat taken from the open file, the old file
kept by a hard link (a copy where there are none) for the bytes the history
keeps, then renamed over the note, tried again while Windows says another
program holds it; a caller with a journal holds that copy until its history
has it. A new file
(``write_new``) never replaces one that appeared meanwhile. The trash is
``.gamma/trash/<stamp>/<path>``, on the vault's volume so that a delete is a
rename (``trash_path``, ``move``), where a file stays ``TRASH_DAYS`` at least
(``trashed_at``); a path a file comes back to is made free the way every
vault name is (``free_name``).
"""

import calendar
import ctypes
import difflib
import fnmatch
import hashlib
import heapq
import itertools
import json
import os
import re
import secrets
import shutil
import stat
import sys
import time
import unicodedata
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from . import gamma_sync
from .documents.formats import IMAGE_MEDIA_TYPES
from .obsidian_export import unique_name

MARKER_DIR = ".gamma"
MARKER = ".gamma/vault.json"
TRASH = ".gamma/trash"
FORMAT = "gamma-vault-1"
TEMP = ".gamma-"                # what Gamma's own temporary files in a vault are called
NOTES = (".md", ".markdown")
PAPERS = (".pdf",)
RACY_S = 2.0                    # a file written this near when it was read may hide a later save in the same tick
MOVE_SIMILAR = 0.6              # how alike a removed note and a new one must be to be one note moved
ID_SIMILAR = 0.3                # how alike they must be when they have one file id
MOVE_COMPARES = 200             # the most texts a scan compares to find moves
STALE_TEMP_S = 60               # a temporary file of Gamma's older than this was left by a write that died
TRASH_DAYS = 30                 # a file stays in the vault's trash this long at least
REPLACE_ATTEMPTS = 10           # a rename over a note another program holds (Windows) is tried this often
REPLACE_RETRY_S = 0.1
ID_BYTES = 64 << 10             # how much of a PDF's head and tail is read for its /ID
OFFLINE = 0x1000                # FILE_ATTRIBUTE_OFFLINE
RECALL_ON_DATA_ACCESS = 0x400000
DATALESS = getattr(stat, "SF_DATALESS", 0x40000000)   # macOS: the bytes are in the cloud

_CASE = "differs from another name here only by case"
_LINK = "is a link, which a vault does not follow"
_RESERVED_RE = re.compile(r"(?i)(con|prn|aux|nul|com[1-9¹²³]|lpt[1-9¹²³])(\..*)?")
_BAD_CHARS_RE = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_ICLOUD_RE = re.compile(r"\.(.+)\.icloud")
_ID_RE = re.compile(rb"/ID\s*\[\s*(?:<([0-9A-Fa-f\s]*)>|\(((?:\\.|[^\\)])*)\))", re.S)
_WORDS_RE = re.compile(r"\w+")
_FRONT_RE = re.compile(r"\A﻿?---[ \t]*\r?\n.*?^---[ \t]*(?:\r?\n|\Z)", re.S | re.M)


class Offline(Exception):
    """The vault's directory does not answer, or stopped answering during the
    scan: nothing it read stands, and nothing reads as removed."""


class Stopped(Exception):
    """The directory is not the vault's any more: no marker, another vault's,
    or one that cannot be read."""


class Outside(OSError):
    """A path that leaves the vault: absolute, through ``..``, or below a link
    or a junction, which the scan does not follow either. Nothing there is
    read or written."""


@dataclass(slots=True)
class Found:
    """A file as the scan found it. ``placeholder``: its bytes are in the
    cloud, so it is never read or removed."""
    kind: str               # "notes" or "pdf"
    size: int
    mtime_ns: int
    file_id: str
    placeholder: bool = False


@dataclass(slots=True)
class Scan:
    """One walk of a vault: its notes and papers (``files``), its directories
    with their file ids (``dirs``), paths relative to the root with ``/``;
    how many pictures and other files it holds, and where the pictures are
    (``images``, for the links of its notes); what it left and why
    (``refused``, ``(path, why)``); and Gamma's temporary files it met, for
    ``clean_temps``."""
    files: dict = field(default_factory=dict)
    dirs: dict = field(default_factory=dict)
    pictures: int = 0
    untracked: int = 0
    refused: list = field(default_factory=list)
    temps: list = field(default_factory=list)
    images: list = field(default_factory=list)


@dataclass(slots=True)
class Known:
    """A file as a round knows it, for ``pair_moves``: a base's row, or a file
    found on disk with what reading it gave."""
    path: str
    kind: str
    size: int = 0
    hash: str = ""
    file_id: str = ""
    pdf_id: str = ""
    text: str | None = None
    mtime_ns: int = 0


@dataclass(slots=True)
class Written:
    """A write's result: the new file's stat, as the scan will see it, the
    bytes it replaced (None for a new file), and ``kept``, the vault path of
    a copy of those bytes left on the disk for the caller (``replace_note``'s
    ``hold``; "" for none)."""
    size: int
    mtime_ns: int
    file_id: str
    replaced: bytes | None = None
    kept: str = ""


# --- the marker -----------------------------------------------------------------

def hide(path: Path) -> None:
    """Give ``path`` Windows' hidden attribute; elsewhere its dot name hides it."""
    if sys.platform != "win32":
        return
    kernel = ctypes.windll.kernel32
    kernel.GetFileAttributesW.restype = ctypes.c_uint32
    attrs = kernel.GetFileAttributesW(str(path))
    if attrs != 0xFFFFFFFF:
        kernel.SetFileAttributesW(str(path), attrs | stat.FILE_ATTRIBUTE_HIDDEN)


def read_marker(root: Path) -> dict | None:
    """The vault's marker, None when the directory has none. ``Offline`` when
    the root is not there or does not answer; ``Stopped`` for a marker that
    cannot be read. A ``notes_folder`` that leaves the vault (absolute, a
    drive, ``..``: a marker that came with a directory names what it likes)
    is none. No message names the directory: a viewer of the vault reads them."""
    try:
        if not root.is_dir():
            raise Offline("the vault's directory is not there")
        text = (root / MARKER).read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except OSError as e:
        raise Offline(f"the vault's directory does not answer: {_why(e)}") from e
    try:
        marker = json.loads(text)
    except ValueError:
        marker = None
    if not isinstance(marker, dict) or marker.get("format") != FORMAT or not isinstance(marker.get("id"), str):
        raise Stopped("the vault's marker cannot be read")
    ignore = marker.get("ignore")
    folder = str(marker.get("notes_folder") or "").replace("\\", "/")
    folder = "" if _leaves(folder) else folder.strip("/")
    return {**marker, "notes_folder": folder, "ignore": [str(g) for g in ignore] if isinstance(ignore, list) else []}


def _leaves(rel: str) -> bool:
    """Whether a path in the vault, ``/`` between its names, names a place
    outside it by its spelling: absolute, a drive, or through ``..``."""
    return rel.startswith("/") or os.path.isabs(rel) or bool(re.match(r"[A-Za-z]:", rel)) or ".." in rel.split("/")


def make_marker(root: Path, notes_folder: str = "", ignore=()) -> dict:
    """A new vault's marker, written once, with a new id, in a hidden
    ``.gamma``: the root is made when it is not there (making the vault is the
    one time it may be). FileExistsError when the directory has a marker."""
    if (root / MARKER).exists():
        raise FileExistsError(f"{root} is a vault already")
    marker = {"format": FORMAT, "id": secrets.token_hex(16), "notes_folder": notes_folder, "ignore": list(ignore)}
    folder = root / MARKER_DIR
    folder.mkdir(parents=True, exist_ok=True)
    hide(folder)
    tmp = folder / f"{TEMP}{secrets.token_hex(6)}.tmp"
    try:
        with open(tmp, "x", encoding="utf-8") as f:
            json.dump(marker, f, indent=2)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, root / MARKER)
    finally:
        tmp.unlink(missing_ok=True)
    return marker


def check_marker(root: Path, vault_id: str) -> dict:
    """The marker, when it is the vault ``vault_id``'s; ``Stopped`` when the
    directory holds none or another's, ``Offline`` when it does not answer."""
    marker = read_marker(root)
    if marker is None:
        raise Stopped("the directory has no vault marker: it is not the vault's any more")
    if marker["id"] != vault_id:
        raise Stopped("the directory is another vault's")
    return marker


def marker_file(root: Path) -> str:
    """The marker file's file id ("" where the filesystem gives none). No
    round rewrites the marker, so it keeps its id; a copy of the vault (a
    backup disk given the vault's drive letter) holds the same marker under
    another."""
    try:
        file_id = os.stat(root / MARKER).st_ino
    except OSError:
        return ""
    return str(file_id) if file_id else ""


# --- the scan -------------------------------------------------------------------

def kind_of(name: str) -> str:
    """What a file is to a vault by its name: ``notes``, ``pdf``, ``picture``,
    or "" for anything else."""
    ext = os.path.splitext(name)[1].lower()
    if ext in NOTES:
        return "notes"
    if ext in PAPERS:
        return "pdf"
    return "picture" if ext in IMAGE_MEDIA_TYPES else ""


def refusal(name: str) -> str:
    """Why Windows refuses ``name`` as a file or directory name ("" when it
    takes it): a device name with any extension, a character it reserves, or a
    dot or space at the end."""
    if _RESERVED_RE.fullmatch(name.rstrip(" .")):
        return "is a name Windows keeps for a device"
    if _BAD_CHARS_RE.search(name):
        return "holds a character Windows refuses in a name"
    if name.endswith((" ", ".")):
        return "ends in a dot or a space, which Windows drops"
    return ""


def temporary(name: str) -> bool:
    """Whether ``name`` is a file being written or an editor's lock: Gamma's
    own, LibreOffice's and Office's, and macOS's ``._`` companions on disks
    that are not its own."""
    return name.startswith((TEMP, "~$", ".~lock.", "._")) or name.endswith(gamma_sync.PART)


def _temp_of_gamma(rel: str, name: str) -> bool:
    """Whether a temporary name the walk met in the directory ``rel`` is one
    of Gamma's that a write may have left: its own temporary files, and the
    gamma-sync client's state at the vault's root, which a folder kept before
    leaves when its upgrade dies before taking it away (gamma/vault_upgrade.py).
    Below the root that file is another link's or a gamma-sync directory's:
    never swept."""
    if name == gamma_sync.STATE:
        return not rel
    return name.startswith(TEMP) or name.endswith(gamma_sync.PART)


def listed(name: str, directory: bool = False) -> bool:
    """Whether the scan lists an entry of this name (``walk``): no temporary
    name, and for a directory no dot directory or ``node_modules``. A name pull
    writes must be one, or its own scan would never see the file."""
    return not (temporary(name) or directory and (name.startswith(".") or name == "node_modules"))


def _ignored(path: str, name: str, ignore) -> bool:
    """Whether a glob of the marker's ``ignore`` takes the entry: one with a
    ``/`` matched against its path in the vault, else against its name."""
    for glob in ignore:
        glob = glob.strip().strip("/")
        if glob and fnmatch.fnmatch(path if "/" in glob else name, glob):
            return True
    return False


def _fold(name: str) -> str:
    return unicodedata.normalize("NFC", name).casefold()


def _entries(directory: Path) -> list:
    with os.scandir(directory) as found:
        return list(found)


def _why(e: OSError) -> str:
    """What went wrong, without the path an OSError names."""
    return e.strerror or type(e).__name__


def _is_link(st) -> bool:
    return stat.S_ISLNK(st.st_mode) or getattr(st, "st_reparse_tag", 0) == getattr(stat, "IO_REPARSE_TAG_MOUNT_POINT",
                                                                                   0xA0000003)


def path(root: Path, rel: str) -> Path:
    """The file ``rel`` of the vault ``root`` (``/`` between names), as every
    read and write of a round takes it: ``Outside`` for one whose spelling
    leaves the vault or that a link or a junction stands on the way to, or
    that is one (the scan follows none), so that nothing there is read or
    written. Directories not made yet are no link."""
    if not rel or _leaves(rel):
        raise Outside(f"{rel} is not a path inside the vault")
    here = root
    for name in rel.split("/"):
        here = here / name
        try:
            st = os.lstat(here)
        except FileNotFoundError:
            break
        if _is_link(st):
            raise Outside(f"{rel} is below a link, which a vault does not follow")
    return root / rel


def lstat(root: Path, rel: str):
    """``os.lstat`` of the vault's file ``rel`` (``path``), None when it is not there."""
    try:
        return os.lstat(path(root, rel))
    except FileNotFoundError:
        return None


def read(root: Path, rel: str) -> bytes:
    """The bytes of the vault's file ``rel`` (``path``)."""
    return path(root, rel).read_bytes()


def sha(data: bytes) -> str:
    """A note's hash, as its row keeps it."""
    return hashlib.sha256(data).hexdigest()


def _placeholder(st) -> bool:
    """Whether a file's bytes are in the cloud, by its stat (Windows'
    attributes, macOS's flags)."""
    attrs = getattr(st, "st_file_attributes", 0)
    return bool(attrs & (OFFLINE | RECALL_ON_DATA_ACCESS) or getattr(st, "st_flags", 0) & DATALESS)


def _junction(entry) -> bool:
    is_junction = getattr(entry, "is_junction", None)
    if is_junction is not None:
        return is_junction()
    tag = getattr(entry.stat(follow_symlinks=False), "st_reparse_tag", 0)   # Python 3.11 on Windows
    return tag == getattr(stat, "IO_REPARSE_TAG_MOUNT_POINT", 0xA0000003)


def walk(root: Path, ignore=()) -> Scan:
    """What the directory ``root`` holds, as the module doc says: ``Offline``
    for any error of the walk, so that what it returns is all of it. A file
    gone between its listing and its stat is gone."""
    out, pending = Scan(), [("", root)]
    while pending:
        rel, here = pending.pop()
        try:
            entries = _entries(here)
        except OSError as e:
            raise Offline(f"the vault's directory stopped answering at {rel or '.'}: {_why(e)}") from e
        kept = []
        for entry in entries:
            name = entry.name
            try:
                link = entry.is_symlink() or _junction(entry)
                directory = not link and entry.is_dir(follow_symlinks=False)
            except OSError as e:
                raise Offline(f"the vault's directory stopped answering at {rel}{name}: {_why(e)}") from e
            if directory and not listed(name, directory=True):
                continue
            stub = None if directory else _ICLOUD_RE.fullmatch(name)
            shown = stub[1] if stub else name
            if temporary(shown):
                if not stub and _temp_of_gamma(rel, name):
                    out.temps.append(f"{rel}{name}")
                continue
            if not _ignored(f"{rel}{shown}", shown, ignore):
                kept.append((entry, shown, link, directory, bool(stub)))
        names = Counter(_fold(shown) for _entry, shown, *_ in kept)
        for entry, shown, link, directory, stub in kept:
            path = f"{rel}{shown}"
            why = refusal(shown) or (_CASE if names[_fold(shown)] > 1 else "") or (_LINK if link else "")
            if why:
                out.refused.append((path, why))
            elif directory:
                try:
                    out.dirs[path] = str(entry.inode())
                except FileNotFoundError:
                    continue
                except OSError as e:
                    raise Offline(f"the vault's directory stopped answering at {path}: {_why(e)}") from e
                pending.append((f"{path}/", Path(entry.path)))
            else:
                _count(out, entry, path, stub, root)
    return out


def _count(out: Scan, entry, path: str, stub: bool, root: Path) -> None:
    """A file of the walk, listed or counted."""
    kind = kind_of(path)
    if kind == "picture":
        out.pictures += 1
        if not stub:
            out.images.append(path)
        return
    if not kind:
        out.untracked += 1
        return
    try:
        if not entry.is_file(follow_symlinks=False):
            return
        st = entry.stat(follow_symlinks=False)
        placeholder = stub or _placeholder(st)
        file_id = "" if placeholder else str(entry.inode())
    except FileNotFoundError:
        return
    except OSError as e:
        raise Offline(f"the vault's directory stopped answering at {path}: {_why(e)}") from e
    out.files[path] = Found(kind, 0 if stub else st.st_size, 0 if stub else st.st_mtime_ns, file_id, placeholder)


def scan(root: Path, vault_id: str) -> Scan:
    """The vault ``vault_id``'s directory walked, its marker checked before
    (``check_marker``) and after: one gone by the end means the disk went
    during the walk, ``Offline``."""
    marker = check_marker(root, vault_id)
    found = walk(root, marker["ignore"])
    after = read_marker(root)
    if after is None:
        raise Offline("the vault's directory lost its marker during the scan")
    if after["id"] != vault_id:
        raise Stopped("the directory is another vault's")
    return found


def racy(mtime_ns: int, read_at: float) -> bool:
    """Whether a file's time is too near the moment its base was read: a save
    in the same tick of a coarse clock (exFAT keeps 2 s, SMB about 1) would
    leave the same time."""
    return abs(mtime_ns / 1e9 - read_at) < RACY_S


def moved(row: dict, found: Found) -> bool:
    """Whether the file ``found`` may differ from what a base ``row`` (its
    ``size``, ``mtime_ns``, ``file_id``, ``read_at``) read: another stat, or a
    racy one (``racy``). The hash decides."""
    if (found.size, found.mtime_ns) != (row["size"], row["mtime_ns"]):
        return True
    if found.file_id and row["file_id"] and found.file_id != row["file_id"]:
        return True
    return racy(row["mtime_ns"], row["read_at"])


# --- evidence of a move -------------------------------------------------------------

def pdf_id(path: Path) -> str:
    """The first half of a PDF's trailer ``/ID``, the one it keeps across
    saves, as lowercase hex ("" for none): the last trailer's, else the first
    page's of a linearized file."""
    with open(path, "rb") as f:
        size = f.seek(0, os.SEEK_END)
        f.seek(max(0, size - ID_BYTES))
        tail = f.read()
        f.seek(0)
        head = f.read(min(ID_BYTES, size))
    found = list(_ID_RE.finditer(tail)) or list(_ID_RE.finditer(head))[:1]
    if not found:
        return ""
    hexed, literal = found[-1].groups()
    return re.sub(rb"\s", b"", hexed).decode("ascii").lower() if hexed is not None else literal.hex()


def _body_words(text: str) -> list:
    """A note's words, its front matter left out."""
    return _WORDS_RE.findall(_FRONT_RE.sub("", text, count=1))


def similarity(a: str, b: str, floor: float = MOVE_SIMILAR) -> float:
    """How alike two notes' texts are, word by word, 0 to 1; 0 at once for two
    that cannot reach ``floor``, and for an empty one. The words are the
    body's (``_body_words``): front matter and marks (``- [ ]``, ``>``, ``#``)
    say nothing of which note it is, and a short note saved under a new name
    with a ``title:`` key or a task added would read as another."""
    x, y = _body_words(a), _body_words(b)
    if not x or not y:
        return 0.0
    seq = difflib.SequenceMatcher(None, x, y)
    if seq.real_quick_ratio() < floor or seq.quick_ratio() < floor:
        return 0.0
    return seq.ratio()


def nearer(text: str, a: str, b: str) -> bool:
    """Whether ``text`` reads nearer ``a`` than ``b``, by the lines each would
    have to change: a note a write put ``a`` into and a person saved since
    (a safe save gives it another file id) is ``a`` edited, not ``b``."""
    def apart(x: str, y: str) -> int:
        ops = difflib.SequenceMatcher(None, x.split("\n"), y.split("\n"), autojunk=False).get_opcodes()
        return sum(i2 - i1 + j2 - j1 for tag, i1, i2, j1, j2 in ops if tag != "equal")
    return apart(a, text) < apart(b, text)


def _stem(path: str) -> str:
    return os.path.splitext(path.rsplit("/", 1)[-1])[0].casefold()


def _alike(gone: Known, found: Known) -> bool:
    """What makes the same file id evidence: the same size or hash and the
    file's own time with it, the same PDF ``/ID``, or a note's text alike,
    ``ID_SIMILAR`` being enough with the id (a short note renamed and edited:
    ``buy more coffee`` to ``buy more tea today``), where text alone needs
    ``MOVE_SIMILAR``, or the old text kept in the new, ``MOVE_SIMILAR`` of its
    words in their order at least (a short note moved and written on at
    length: ``kept``). Never the id alone: ext4 gives a freed inode to the
    next file made, so the file in its place answers to the old one's id.

    Bytes that match exactly are the one thing that file is as likely to have
    as the file that left (a template's copies share them, which is why a hash
    elsewhere is no evidence either), so they count only with ``mtime_ns``:
    a move keeps the file's time to the nanosecond, where a file written in
    the freed inode's place has its own. Text that merely resembles the old
    is evidence with the id still: nothing copies a file and then edits it
    into most of what the old one said."""
    if (gone.size == found.size or (gone.hash and gone.hash == found.hash)) and gone.mtime_ns == found.mtime_ns:
        return True
    if found.kind == "pdf":
        return bool(found.pdf_id and found.pdf_id == gone.pdf_id)
    old, new = gone.text or "", found.text or ""
    if old and old == new:
        return False
    return similarity(old, new, ID_SIMILAR) >= ID_SIMILAR or kept(old, new) >= MOVE_SIMILAR


def kept(a: str, b: str) -> float:
    """How much of note ``a`` note ``b`` keeps, word by word in their order, 0
    to 1 (0 for an empty one); the words are the body's, as ``similarity``
    reads them."""
    x, y = _body_words(a), _body_words(b)
    if not x or not y:
        return 0.0
    blocks =difflib.SequenceMatcher(None, x, y, autojunk=False).get_matching_blocks()
    return sum(block.size for block in blocks) / len(x)


def _unique(files, key) -> dict:
    counts = Counter(key(f) for f in files)
    return {key(f): f for f in files if key(f) and counts[key(f)] == 1}


def pair_moves(gone: list, found: list) -> tuple[dict, bool]:
    """Which files ``found`` on disk are files ``gone`` from where the base had
    them (``Known`` each): ``({found path: gone path}, capped)``. A path on both
    sides is a file that changed where it is: only its file id can say it is
    another's (two names swapped). ``capped``: ``MOVE_COMPARES`` ended the
    search by text, so a note moved and edited may read as removed and added."""
    both = {f.path for f in found} & {g.path for g in gone}
    pairs, taken = {}, set()

    def pair(f, g):
        pairs[f.path] = g.path
        taken.add(g.path)

    by_id = {}
    for g in gone:
        if g.file_id not in ("", "0"):
            by_id.setdefault((g.kind, g.file_id), []).append(g)
    for f in found:
        for g in by_id.get((f.kind, f.file_id), ()) if f.file_id not in ("", "0") else ():
            if g.path != f.path and g.path not in taken and _alike(g, f):
                pair(f, g)
                break

    def left():
        return ([g for g in gone if g.path not in taken and g.path not in both],
                [f for f in found if f.path not in pairs and f.path not in both])

    for key in (lambda f: (f.kind, f.hash) if f.hash and f.size else None,
                lambda f: f.pdf_id if f.kind == "pdf" and f.pdf_id else None):
        olds, news = left()
        olds = _unique(olds, key)
        for k, f in _unique(news, key).items():
            if k in olds:
                pair(f, olds[k])

    olds, news = (((x, _stem(x.path)) for x in side if x.kind == "notes" and x.text) for side in left())
    nearest = heapq.nsmallest(MOVE_COMPARES + 1, (
        (old_stem != new_stem, abs(g.size - f.size), k, g, f)
        for k, ((g, old_stem), (f, new_stem)) in enumerate(itertools.product(olds, news))))
    scored = []
    for other_name, _size, k, g, f in nearest[:MOVE_COMPARES]:     # the likeliest first: the same name, a near size
        alike = similarity(g.text, f.text)
        if alike >= MOVE_SIMILAR:
            scored.append((-round(alike, 2), other_name, k, g, f))
    for *_rank, g, f in sorted(scored, key=lambda s: s[:3]):
        if g.path not in taken and f.path not in pairs:
            pair(f, g)
    return pairs, len(nearest) > MOVE_COMPARES


def came_back(tombs: list, found: list) -> dict:
    """Which new files ``found`` are the files of pages in Recently deleted
    coming back (``Known`` each, a tombstone's ``path`` where it lived):
    ``{found path: index in tombs}``. Where it lived, the same bytes or a text
    alike (a branch switched there and back, an editor saving again the note it
    had open); anywhere, the same file id with a size or a text alike
    (``_alike``). A hash alone elsewhere is no evidence: a template's copies
    share one. A file at the very path the page's file had answers to that
    first evidence alone, whatever its id: the next note written under a name
    its page was deleted under is that name's new note, not the old one back,
    and a freed inode would otherwise say it was."""
    out, taken = {}, set()
    for f in found:
        for k, g in enumerate(tombs):
            if k in taken or g.kind != f.kind:
                continue
            same_id = f.file_id not in ("", "0") and f.file_id == g.file_id
            where_it_lived = _fold(g.path) == _fold(f.path)
            here = where_it_lived and bool(
                (g.hash and g.hash == f.hash) or (f.kind == "pdf" and f.pdf_id and f.pdf_id == g.pdf_id)
                or (f.kind == "notes" and similarity(g.text or "", f.text or "") >= MOVE_SIMILAR))
            if here or (not where_it_lived and same_id and _alike(g, f)):
                out[f.path] = k
                taken.add(k)
                break
    return out


# --- writing ----------------------------------------------------------------------

def _temp_beside(target: Path, data: bytes) -> tuple[Path, os.stat_result]:
    """``data`` written whole to a temporary name beside ``target`` that the
    scan skips, flushed to the disk, and its stat from the open file: what the
    file will show once renamed (a rename keeps the time, the size and the file
    id)."""
    tmp = target.with_name(f"{TEMP}{secrets.token_hex(6)}.tmp")
    try:
        with open(tmp, "xb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
            st = os.fstat(f.fileno())
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    return tmp, st


def _rename_over(tmp: Path, target: Path) -> None:
    """``tmp`` renamed over ``target``, tried again while Windows says another
    program has the note open; the last refusal is raised. Never taken for
    done because a file of that size is there: these names hash nothing."""
    for attempt in range(REPLACE_ATTEMPTS):
        try:
            os.replace(tmp, target)
            return
        except PermissionError:
            if attempt == REPLACE_ATTEMPTS - 1:
                raise
            time.sleep(REPLACE_RETRY_S)


def replace_note(root: Path, rel: str, data: bytes, ready=None, *, hold: bool = False) -> Written:
    """The note ``rel`` of the vault ``root`` (``path``) replaced by ``data``
    (the module doc): the bytes it held are kept beside it under a name the
    scan skips before the rename, by a hard link where the filesystem has
    them, so they are exactly the bytes the rename replaced, else by a copy.
    FileNotFoundError when the note is gone, and nothing is written: it is
    read again first. PermissionError when another program held it to the
    last try. ``ready(written)`` hears the new file's stat and where the old
    bytes will be kept once the bytes are on the disk under the temporary
    name and before anything else: a journal that names them can tell, after
    a crash and a person's edit, that the write landed, and still find what
    it replaced. ``hold``: that copy stays (``Written.kept``) for the caller
    to drop once its history has it (``drop_temp``); else it goes now."""
    target = path(root, rel)
    tmp, st = _temp_beside(target, data)
    keep = target.with_name(f"{TEMP}{secrets.token_hex(6)}.old")
    kept = f"{rel.rpartition('/')[0]}/{keep.name}".lstrip("/")
    try:
        if ready is not None:
            ready(Written(st.st_size, st.st_mtime_ns, str(st.st_ino), kept=kept))
        try:
            try:
                os.link(target, keep)
            except FileNotFoundError:
                raise
            except OSError:          # FAT, exFAT, some shares: no hard links
                shutil.copyfile(target, keep)
            _rename_over(tmp, target)
        except BaseException:
            keep.unlink(missing_ok=True)     # nothing was replaced
            raise
        replaced = keep.read_bytes()
        if not hold:
            keep.unlink(missing_ok=True)
        return Written(st.st_size, st.st_mtime_ns, str(st.st_ino), replaced, kept if hold else "")
    finally:
        tmp.unlink(missing_ok=True)


def drop_temp(root: Path, rel: str) -> None:
    """A temporary file of Gamma's in the vault removed, once what it held is
    kept elsewhere; a path that is none is left alone."""
    if rel and temporary(rel.rsplit("/", 1)[-1]):
        path(root, rel).unlink(missing_ok=True)


def write_new(root: Path, rel: str, data: bytes, ready=None) -> Written:
    """A new note ``rel`` of the vault ``root`` holding ``data``, written as
    ``replace_note`` writes (``ready`` too), the directories it needs made.
    FileExistsError when a file took the name meanwhile: nothing a person made
    is replaced."""
    _ours(root)
    target = path(root, rel)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp, st = _temp_beside(target, data)
    try:
        if ready is not None:
            ready(Written(st.st_size, st.st_mtime_ns, str(st.st_ino)))
        if sys.platform == "win32":
            os.rename(tmp, target)       # refuses a name that is taken
        else:
            try:
                os.link(tmp, target)     # so does a link, where the filesystem has them
            except FileExistsError:
                raise
            except OSError:
                if os.path.lexists(target):
                    raise FileExistsError(str(target)) from None
                os.rename(tmp, target)
        return Written(st.st_size, st.st_mtime_ns, str(st.st_ino))
    finally:
        tmp.unlink(missing_ok=True)


def clean_temps(root: Path, temps) -> int:
    """Gamma's temporary files a scan met (``Scan.temps``) that a write left
    when it died, ``STALE_TEMP_S`` old, removed (``remove``): how many went. A
    write going on now is younger."""
    now, gone = time.time(), 0
    for rel in temps:
        temp = root / rel
        try:
            if now - os.lstat(temp).st_mtime >= STALE_TEMP_S:
                remove(temp)
                gone += 1
        except OSError:
            continue
    return gone


def remove(file: Path) -> None:
    """A file of Gamma's own in a vault deleted, its read-only attribute taken
    off first where Windows refuses for it (a copy, a restore or a sync tool
    may set it). Not there is done; OSError while another program holds it."""
    try:
        file.unlink(missing_ok=True)
    except PermissionError:
        os.chmod(file, stat.S_IREAD | stat.S_IWRITE)
        file.unlink(missing_ok=True)


# --- names, moves and the trash ---------------------------------------------------------

def stamp(now: float | None = None) -> str:
    """A trash directory's name: when, in UTC, to the second."""
    return time.strftime("%Y%m%d-%H%M%S", time.gmtime(time.time() if now is None else now))


def trashed_at(rel: str) -> float | None:
    """When the file at ``rel`` went to the vault's trash, by the stamp of its
    directory there (``stamp``); None for a path outside the trash or a stamp
    that says no time."""
    parts = rel.split("/")
    if not rel.startswith(f"{TRASH}/") or len(parts) < 4:
        return None
    try:
        return float(calendar.timegm(time.strptime(parts[2], "%Y%m%d-%H%M%S")))
    except ValueError:
        return None


def free_name(root: Path, rel: str, own: str = "", taken=()) -> str:
    """``rel``, or the first `` 2``, `` 3``… of it no entry of its directory
    holds, ignoring case (``obsidian_export.unique_name``, the one naming rule),
    nor any path of ``taken`` (a round's rows: a file missing from the disk
    still holds its path until two scans agree it went). ``own``: the path of
    the file being renamed, which never blocks itself, so that ``Foo.md`` can
    become ``foo.md``."""
    directory, _, name = rel.rpartition("/")
    prefix = f"{directory}/" if directory else ""
    try:
        used = {f"{prefix}{entry}".lower() for entry in os.listdir(root / directory)}
    except FileNotFoundError:
        used = set()
    used |= {p.lower() for p in taken}
    used.discard(own.lower())
    stem, ext = os.path.splitext(name)
    return unique_name(used, prefix, stem, ext)


def trash_path(root: Path, rel: str, when: str, taken=()) -> str:
    """Where the trash takes the file ``rel`` in a round of ``when``
    (``stamp``): ``.gamma/trash/<when>/<rel>``, free on the disk and among
    ``taken``, the tombstones' paths (one whose file had left the disk holds
    its path with nothing there). Recorded before the move, so that a crash
    between the two leaves the file findable."""
    return free_name(root, f"{TRASH}/{when}/{rel}", taken=taken)


def _ours(root: Path) -> None:
    """Directories are made only inside a vault that is there: an empty mount
    point holds no marker."""
    if not (root / MARKER).is_file():
        raise Offline("the vault's directory has no marker: the disk may not be there")


def move(root: Path, src: str, dst: str) -> None:
    """The file or directory ``src`` renamed to ``dst``, both in the vault, the
    directories ``dst`` needs made. FileExistsError when another file holds
    ``dst`` (a rename that only changes case is not one)."""
    _ours(root)
    source, target = path(root, src), path(root, dst)
    if os.path.lexists(target) and not os.path.samefile(source, target):
        raise FileExistsError(dst)
    target.parent.mkdir(parents=True, exist_ok=True)
    os.rename(source, target)


def purge(root: Path, rel: str) -> None:
    """A file in the trash removed for good (its page was, and it has been
    there ``TRASH_DAYS``: the caller's to know), and the trash's directories
    it leaves empty. ValueError for a path outside the trash: nothing else of
    a vault is ever deleted."""
    if not rel.startswith(f"{TRASH}/") or ".." in rel.split("/"):
        raise ValueError(f"{rel} is not in the vault's trash")
    gone = path(root, rel)
    gone.unlink(missing_ok=True)
    trash, up = root / TRASH, gone.parent
    while up != trash:
        try:
            up.rmdir()
        except OSError:
            return
        up = up.parent
