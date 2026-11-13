"""What a vault tracks: ``.gammaignore`` at its root, read with git's ignore
rules (gitignore(5)), and the shortest such file that says a selection
(docs/dev/work_folders.md "What a work folder tracks").

**The rules** (``Rules``) are git's, line by line: a UTF-8 BOM skipped, the CR
of a CRLF dropped, blank lines and ``#`` comments skipped, trailing spaces
dropped unless escaped (``\\ ``), a backslash escaping the next character,
``!`` taking back what an earlier line left out, a trailing ``/`` for
directories only, and a ``/`` at the start or in the middle anchoring the
pattern at its file's directory, where one without matches a name at any
depth. ``*`` and ``?`` never match a ``/``; ``[...]`` is a set, with ranges,
``!`` or ``^`` for its complement and the POSIX classes; ``**`` as a whole
name matches any depth (``**/x``, ``x/**``, ``a/**/b``) and any other is one
``*``. The last line that matches decides, and nothing below a directory left
out is looked at (``tracks`` decides each directory above a path first), so
no line takes a file back from under one. It differs from git in one way:
matching is always case-insensitive, with patterns and paths NFC, so one file
reads the same on every system and ``*.pdf`` takes ``Paper.PDF``. Only the
root's file is read, and the scan's own rules come before it whatever it says
(gamma/vault/disk.py ``walk``).

**No file** is ``DEFAULT``: notes, PDFs and pictures, the classes a vault has
always read. ``read`` tells a file that is not there from one that cannot be
read (``Unreadable``), which a round never takes for the default.

**The writer** (``write``) says a selection of a listing's files in anchored
lines, a group for each directory it reaches: either the directory's unticked
files and wholly unticked subdirectories, or ``/D/*`` and its ticked files
and the subdirectories that hold one taken back, whichever is fewer lines (in
on a tie), ``*.e`` standing for two files or more of an extension when every
file of it there is one. A directory ticked whole gets no line, so a file
made in it later is tracked; one unticked whole is one line, so the scan
never enters it. Each directory's form is chosen on its own, which makes the
whole the shortest of its kind. What it wrote is read back over the listing,
and the form without extension groups, exact by construction, written if the
two differ.

**The hint** (``hint_stack``) is what git ignores: the vault's ``.gitignore``
files, those of the directories above it up to the repository's top and
``.git/info/exclude``, read with the same matcher, each file's patterns from
its own directory and the deepest file that matches deciding. It marks rows
of the tracking dialog and filters nothing.
"""

import hashlib
import os
import re
import stat
import unicodedata
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from ..documents.formats import EXTENSIONS
from ..logbuf import log

MAX_BYTES = 256 * 1024           # the largest .gammaignore a vault reads
NAME = ".gammaignore"
GITIGNORE = ".gitignore"
HEADER = "# What Gamma tracks in this folder, in .gitignore's syntax. Edit it here or with Choose files… in Gamma."
# What a vault tracks when its directory holds no .gammaignore: every
# directory walked, its notes, PDFs and pictures, the three classes a vault
# has always read (gamma/vault/kinds.py class_of; in a work folder a picture
# is a file with a page of its own).
DEFAULT_TEXT = "*\n!*/\n" + "".join(f"!*{ext}\n" for kind in ("markdown", "pdf", "image")
                                    for ext, row in EXTENSIONS.items() if row.format == kind)

ALL, NONE, MIXED, EMPTY = "all", "none", "mixed", "empty"   # a directory's files under a selection

_SPECIAL = "*?[\\"               # what git's wildmatch reads as other than itself
_FLAGS = re.IGNORECASE | re.DOTALL
_SEEN_MAX = 100_000              # the directories a Rules remembers deciding before it forgets them all
_CHUNK = 64                      # the lines one regex holds: a match costs the square of a regex's groups
# The characters other than ASCII that IGNORECASE matches with an ASCII
# letter (dotted and dotless i, the long s, the Kelvin sign), as that letter.
_LIKE_ASCII = {chr(0x130): "i", chr(0x131): "i", chr(0x17F): "s", chr(0x212A): "k"}
# git's POSIX classes, ASCII only, as the inside of a regex set
_CLASSES = {"alnum": "0-9A-Za-z", "alpha": "A-Za-z", "blank": r" \t", "cntrl": r"\x00-\x1f\x7f", "digit": "0-9",
            "graph": "!-~", "lower": "a-z", "print": " -~", "punct": r"!-/:-@\[-`{-~", "space": r" \t\n\r",
            "upper": "A-Z", "xdigit": "0-9A-Fa-f"}
_MOUNT_POINT = getattr(stat, "IO_REPARSE_TAG_MOUNT_POINT", 0xA0000003)


class Unreadable(Exception):
    """A ``.gammaignore`` that is there and cannot be read; ``why`` says why,
    naming no path."""

    def __init__(self, why: str):
        super().__init__(why)
        self.why = why


class Inexact(ValueError):
    """A selection no ``.gammaignore`` the writer writes says exactly."""


@dataclass(frozen=True, slots=True)
class _Line:
    negated: bool
    directory: bool          # a trailing "/": it matches directories only
    rx: str
    at: str | None = None        # the directory (``_key``, "" the root) a path it matches lies in; None: any
    exact: str | None = None     # with no wildcard: the path it is (``_key``), a name pattern's name
    named: bool = False          # a name at any depth below ``at``


def _nfc(text: str) -> str:
    return text if text.isascii() else unicodedata.normalize("NFC", text)


def _trimmed(line: str) -> str:
    """``line`` without its trailing spaces, an escaped one (``\\ ``) kept, as
    git trims them; one that ends in a lone backslash keeps them all."""
    cut, i = None, 0
    while i < len(line):
        if line[i] == " ":
            cut = i if cut is None else cut
        else:
            if line[i] == "\\":
                i += 1
                if i == len(line):
                    return line
            cut = None
        i += 1
    return line if cut is None else line[:cut]


def _set(body: str, i: int):
    """The set ``[...]`` at ``body[i]``, as git's wildmatch reads one: its
    regex and the index after its ``]``. ``!`` or ``^`` first for the
    complement, a ``]`` first as itself, ``\\`` escaping, ranges (a range's
    first character matching as itself, so a reversed one matches it alone),
    the POSIX classes, a ``[:`` that names none as a ``[``; never a ``/``.
    None when it never closes or names an unknown class: the pattern then
    matches nothing."""
    n, j = len(body), i + 1
    negated = j < n and body[j] in "!^"
    j += negated
    items, prev, start = [], None, j
    while True:
        if j >= n:
            return None
        c = body[j]
        if c == "]" and j > start:
            break
        if c == "\\":
            j += 1
            if j >= n:
                return None
            items.append(re.escape(body[j]))
            prev = body[j]
        elif c == "-" and prev is not None and j + 1 < n and body[j + 1] != "]":
            j += 1
            if body[j] == "\\":
                j += 1
                if j >= n:
                    return None
            if prev <= body[j]:
                items.append(f"{re.escape(prev)}-{re.escape(body[j])}")
            prev = None
        elif c == "[" and body.startswith(":", j + 1):
            end = body.find("]", j + 2)
            if end < 0:
                return None
            name = body[j + 2:end]
            if name.endswith(":"):
                if name[:-1] not in _CLASSES:
                    return None
                items.append(_CLASSES[name[:-1]])
                prev, j = None, end
            else:
                items.append(r"\[")
                prev = "["
        else:
            items.append(re.escape(c))
            prev = c
        j += 1
    inside = "".join(items)
    return (f"[^/{inside}]" if negated else f"(?!/)[{inside}]"), j + 1


def _pattern(body: str, nodir: bool) -> str | None:
    """A pattern's body (its ``!``, its trailing ``/`` and one leading ``/``
    taken off) as a regex, as git's wildmatch reads it; None for one that
    matches nothing (an unclosed ``[``, an unknown class, a lone ``\\`` at the
    end). A name pattern (``nodir``) has no ``**`` of its own. Git matches an
    anchored pattern's part before its first wildcard as a literal and the
    rest as a pattern of its own, so a ``**`` that is its first wildcard is
    one at the start: ``a**/b`` takes ``a/x/b``. A star followed by another in
    the same name takes the leftmost match of what lies between them, which
    is all a match ever needs, so that no run of stars backtracks."""
    tokens, i, n = [], 0, len(body)
    first = next((k for k, c in enumerate(body) if c in _SPECIAL), n)
    while i < n:
        c = body[i]
        if c == "*":
            j = i
            while j < n and body[j] == "*":
                j += 1
            edge = not nodir and j - i > 1 and (i == first or body[i - 1] == "/")
            if edge and j == n:
                tokens.append(("any", ".*"))
            elif edge and body[j] == "/":
                tokens.append(("dirs", "(?:.*/)?"))
                j += 1
            elif edge and body.startswith("\\/", j):
                tokens.append(("any", ".*"))
            else:
                tokens.append(("star", "[^/]*"))
            i = j
        elif c == "?":
            tokens.append(("one", "[^/]"))
            i += 1
        elif c == "[":
            found = _set(body, i)
            if found is None:
                return None
            rx, i = found
            tokens.append(("one", rx))
        else:
            if c == "\\":
                i += 1
                if i == n:
                    return None
                c = body[i]
            tokens.append(("slash" if c == "/" else "one", re.escape(c)))
            i += 1
    out, k = [], 0
    while k < len(tokens):
        kind, rx = tokens[k]
        j = k + 1
        if kind == "star":
            while j < len(tokens) and tokens[j][0] == "one":
                j += 1
            if j < len(tokens) and tokens[j][0] in ("star", "any"):
                out.append("(?>[^/]*?" + "".join(t for _kind, t in tokens[k + 1:j]) + ")")
                k = j
                continue
            j = k + 1
        out.append(rx)
        k = j
    return "".join(out)


def _key(text: str) -> str:
    """``text`` as the index of a ``Rules`` finds it: lowercase, a character
    IGNORECASE takes for an ASCII letter as that letter, any other one beyond
    ASCII as it is (the index holds ASCII alone, so it finds nothing there)."""
    if text.isascii():
        return text.lower()
    return "".join(c.lower() if c.isascii() else _LIKE_ASCII.get(c, c) for c in text)


def _literal(body: str) -> tuple[str, bool]:
    """What a pattern's body says before its first wildcard, its escapes
    read, and whether that is all of it."""
    out, i, n = [], 0, len(body)
    while i < n:
        c = body[i]
        if c in "*?[":
            return "".join(out), False
        if c == "\\":
            i += 1
            if i == n:
                return "".join(out), False
            c = body[i]
        out.append(c)
        i += 1
    return "".join(out), True


def _found(line: _Line, base: str | None, body: str, nodir: bool) -> _Line:
    """``line`` with where the index finds it: by the path or the name it is
    when it has no wildcard, else by the directory its literal part names
    (``base`` the file's, ``_key``; None for one beyond ASCII, whose lines are
    tried on every path)."""
    literal, whole = _literal(body)
    if base is None or not literal.isascii():
        return line
    if nodir:
        return _Line(line.negated, line.directory, line.rx, base, _key(literal) if whole else None, True)
    at = base + _key(literal[:literal.rfind("/") + 1])
    return _Line(line.negated, line.directory, line.rx, at, base + _key(literal) if whole else None)


def _chunks(lines) -> list:
    """Regexes for ``lines`` (``(index, _Line)``, in the file's order), the
    last lines first, each a group of its own so that the group that matches
    names the line, at most ``_CHUNK`` to a regex: ``(the highest index,
    regex, the indices by group)``."""
    out = []
    for end in range(len(lines), 0, -_CHUNK):
        part = lines[max(0, end - _CHUNK):end][::-1]
        rx = re.compile("|".join(f"({x.rx})" for _i, x in part), _FLAGS)
        out.append((part[0][0], rx, tuple(i for i, _x in part)))
    return out


class Rules:
    """One ignore file's lines (``parse``). ``text`` is the file's, ``hash``
    its sha256 ("" for ``DEFAULT``); ``exists``: it is on the disk; ``ours``:
    its first line is ``HEADER``, so Gamma wrote it.

    The lines are indexed, so that a path is tried against the few that may
    match it however long the file: a line with no wildcard by the path or
    the name it is, any other by the directory its literal part names, and
    those in regexes of ``_CHUNK`` lines at most."""

    __slots__ = ("text", "hash", "exists", "ours", "lines", "_exact", "_names", "_wild", "_seen")

    def __init__(self, text: str = "", lines=(), *, digest: str = "", exists: bool = False, ours: bool = False):
        self.text, self.hash, self.exists, self.ours, self.lines = text, digest, exists, ours, tuple(lines)
        self._exact, self._names, wild = {}, {}, {}      # → [the last line for any entry, for directories only]
        for i, x in enumerate(self.lines):
            if x.exact is None:
                wild.setdefault(x.at, []).append((i, x))
            else:
                table, key = (self._names, (x.at, x.exact)) if x.named else (self._exact, x.exact)
                table.setdefault(key, [-1, -1])[x.directory] = i
        self._wild = {at: (_chunks([(i, x) for i, x in found if not x.directory]), _chunks(found))
                      for at, found in wild.items()}
        self._seen = {}

    @classmethod
    def parse(cls, text: str, base: str = "", *, exists: bool = False) -> "Rules":
        """The rules ``text`` says, its patterns relative to the directory
        ``base`` ("" the root; a nested ``.gitignore``'s for the hint)."""
        base = _nfc(base.strip("/"))
        prefix = re.escape(f"{base}/") if base else ""
        at = (_key(f"{base}/") if base else "") if base.isascii() else None
        source = _nfc(text.removeprefix("﻿"))
        lines = []
        for raw in source.split("\n"):
            raw = raw.removesuffix("\r")
            if not raw or raw.startswith("#"):
                continue
            body = _trimmed(raw)
            negated = body.startswith("!")
            body = body[negated:]
            directory = body.endswith("/")
            body = body[:-1] if directory else body
            nodir = "/" not in body
            body = body if nodir else body.removeprefix("/")
            rx = _pattern(body, nodir) if body else None
            if rx is not None:
                line = _Line(negated, directory, prefix + ("(?>(?:.*/)?)" if nodir else "") + rx + r"\Z")
                lines.append(_found(line, at, body, nodir))
        first = source.split("\n", 1)[0].removesuffix("\r")
        return cls(text, lines, digest=hashlib.sha256(text.encode("utf-8", "surrogatepass")).hexdigest(),
                   exists=exists, ours=first == HEADER)

    def decide(self, rel: str, is_dir: bool) -> bool | None:
        """Whether the last line that matches the vault path ``rel`` leaves it
        out (True) or takes it back (False); None when no line matches. The
        directories above it are not asked (``tracks``)."""
        rel = _nfc(rel)
        key = _key(rel)
        best = max(self._exact.get(key, (-1, -1))[:1 + is_dir], default=-1)
        dirs = [None, "", *(key[:i + 1] for i, c in enumerate(key) if c == "/")]
        if self._names:
            name = key.rpartition("/")[2]
            for at in dirs[1:]:
                best = max(best, *self._names.get((at, name), (-1, -1))[:1 + is_dir])
        for at in dirs:
            for top, rx, indices in self._wild.get(at, ((), ()))[is_dir]:
                if top <= best:
                    break
                m = rx.match(rel)
                if m:
                    best = max(best, indices[m.lastindex - 1])
                    break
        return None if best < 0 else not self.lines[best].negated

    def tracks(self, rel: str, is_dir: bool = False) -> bool:
        """Whether the rules track the vault path ``rel``: no directory above
        it left out, and it not left out either."""
        at = ""
        for name in rel.split("/")[:-1]:
            at = f"{at}{name}"
            out = self._seen.get(at)
            if out is None:
                if len(self._seen) > _SEEN_MAX:
                    self._seen.clear()
                out = self._seen[at] = self.decide(at, True) is True
            if out:
                return False
            at += "/"
        return self.decide(rel, is_dir) is not True


DEFAULT = Rules(DEFAULT_TEXT, Rules.parse(DEFAULT_TEXT).lines)


def read(root: Path) -> Rules:
    """The rules of the vault ``root``'s ``.gammaignore``, ``DEFAULT`` when it
    has none; ``Unreadable`` for one that cannot be read: a directory or a
    link in its place, more than ``MAX_BYTES``, not UTF-8, or the disk's
    error. Read once, so a round decides with one text whatever an editor
    does meanwhile."""
    file = Path(root) / NAME
    try:
        st = os.lstat(file)
        if stat.S_ISLNK(st.st_mode) or getattr(st, "st_reparse_tag", 0) == _MOUNT_POINT:
            raise Unreadable("it is a link, which a vault does not follow")
        if stat.S_ISDIR(st.st_mode):
            raise Unreadable("it is a directory")
        with open(file, "rb") as f:
            data = f.read(MAX_BYTES + 1)
    except FileNotFoundError:
        return DEFAULT
    except OSError as e:
        raise Unreadable(e.strerror or type(e).__name__) from e
    if len(data) > MAX_BYTES:
        raise Unreadable(f"it is larger than {MAX_BYTES // 1024} KB")
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        raise Unreadable("it is not UTF-8") from None
    return Rules.parse(text, exists=True)


def excluded(stack: list, rel: str, is_dir: bool) -> bool:
    """Whether ``stack`` (``Rules``, the deepest file first) leaves out the
    path ``rel``: the first file with a line that matches decides, by its
    last such line, as git reads its ``.gitignore`` files."""
    for rules in stack:
        out = rules.decide(rel, is_dir)
        if out is not None:
            return out
    return False


def tracks(rules: Rules, rel: str) -> bool:
    """Whether ``rules`` track the file ``rel`` (``Rules.tracks``)."""
    return rules.tracks(rel)


def tracked(rules: Rules, files) -> set:
    """The files of ``files`` (vault paths) that ``rules`` track."""
    return {f for f in files if rules.tracks(f)}


def _above(rel: str) -> list:
    """The directories that hold the vault path ``rel``, the nearest first,
    "" for the root last."""
    parts = rel.split("/")[:-1]
    return ["/".join(parts[:k]) for k in range(len(parts), -1, -1)]


# --- the .gitignore hint ----------------------------------------------------------

def _hint_file(file: Path, base: str) -> Rules | None:
    """A ``.gitignore`` (or ``info/exclude``) as rules from the directory
    ``base``; None for none, a link (git follows none), one larger than
    ``MAX_BYTES`` or one that cannot be read: a hint goes without it."""
    try:
        st = os.lstat(file)
        if not stat.S_ISREG(st.st_mode) or st.st_size > MAX_BYTES:
            return None
        text = file.read_bytes().decode("utf-8", "replace")
    except OSError:
        return None
    rules = Rules.parse(text, base)
    return rules if rules.lines else None


class GitHint:
    """What git ignores in a vault's directory (``hint_stack``): ``ignored``
    for a row's "git ignores it", ``in_ignored_dir`` for the tracking
    dialog's preset, which leaves out what lies in a directory git ignores
    and ticks a PDF git ignores by a pattern of files (``*.pdf``). The vault's
    own directory is never judged. A directory's ``.gitignore`` is read the
    first time a path below it is asked about, never one in a directory git
    ignores, as git reads none there."""

    def __init__(self, root: Path, prefix: str = "", outer=()):
        self.root, self.prefix, self.outer = Path(root), prefix, list(outer)   # prefix: the root's path from the top
        self._files, self._dirs = {}, {}

    def ignored(self, rel: str, is_dir: bool = False) -> bool:
        """Whether git ignores the vault path ``rel``: a directory above it,
        or it."""
        return self.in_ignored_dir(rel) or self._excludes(rel, is_dir)

    def in_ignored_dir(self, rel: str) -> bool:
        """Whether the vault path ``rel`` lies in a directory git ignores,
        below the vault's root."""
        at = ""
        for name in rel.split("/")[:-1]:
            at = f"{at}/{name}" if at else name
            if at not in self._dirs:
                self._dirs[at] = self._excludes(at, True)
            if self._dirs[at]:
                return True
        return False

    def _excludes(self, rel: str, is_dir: bool) -> bool:
        stack = [rules for rules in map(self._gitignore, _above(rel)) if rules is not None]
        return excluded([*stack, *self.outer], f"{self.prefix}{rel}", is_dir)

    def _gitignore(self, directory: str) -> Rules | None:
        if directory not in self._files:
            self._files[directory] = _hint_file(self.root / directory / GITIGNORE,
                                                f"{self.prefix}{directory}".rstrip("/"))
        return self._files[directory]


def hint_stack(root: Path) -> GitHint:
    """The ``.gitignore`` files that say what git ignores in the vault
    ``root``: its own (read as they are met), those of the directories above
    it up to the repository's top, the nearest holding ``.git``, the nearest
    first, then ``<top>/.git/info/exclude`` when ``.git`` is a directory. A
    vault in no repository has its own alone."""
    root = Path(root)
    top = next((d for d in (root, *root.parents) if os.path.exists(d / ".git")), None)
    if top is None:
        return GitHint(root)
    prefix = root.relative_to(top).as_posix()
    outer, here = [], root
    while here != top:
        here = here.parent
        base = here.relative_to(top).as_posix()
        outer.append(_hint_file(here / GITIGNORE, "" if base == "." else base))
    if (top / ".git").is_dir():
        outer.append(_hint_file(top / ".git" / "info" / "exclude", ""))
    return GitHint(root, "" if prefix == "." else f"{prefix}/", [rules for rules in outer if rules is not None])


# --- the writer ---------------------------------------------------------------------

@dataclass(slots=True)
class Listing:
    """A vault's directory as the writer reads it: its files and its
    directories, vault paths with ``/``, the entries the scan never tracks
    left out. A directory that holds a file is there whether ``dirs`` names
    it or not."""
    files: set = field(default_factory=set)
    dirs: set = field(default_factory=set)


def _join(directory: str, name: str) -> str:
    return f"{directory}/{name}" if directory else name


def _ext(name: str) -> str:
    """What ``*.e`` takes ``name`` by: what follows its last dot, lowercased;
    "" for a name with no dot."""
    return name.rpartition(".")[2].lower() if "." in name else ""


def _escape(name: str) -> str:
    """A name as a pattern that matches it alone: ``\\``, ``*``, ``?`` and
    ``[`` escaped, and each trailing space (a line starts with ``/`` or
    ``!/``, so a ``!`` or ``#`` of the name needs none); NFC, so that one
    selection reads the same text on every system."""
    body = re.sub(r"([\\*?\[])", r"\\\1", _nfc(name))
    kept = body.rstrip(" ")
    return kept + "\\ " * (len(body) - len(kept))


def anchored(path: str, directory: bool = False) -> str:
    """The line that names the vault path ``path`` alone, anchored at the
    root, a directory's with its ``/``."""
    return "".join(f"/{_escape(part)}" for part in path.split("/")) + ("/" if directory else "")


def _sorted(names) -> list:
    return sorted(names, key=lambda s: (s.casefold(), s))


class _Plan:
    """A listing and a selection as the writer reads them: each directory's
    files and subdirectories, and how many files below each are there and
    ticked."""

    def __init__(self, listing: Listing, selected):
        self.every = set(listing.files)
        self.ticked = self.every & set(selected)
        self.files, self.dirs = defaultdict(list), defaultdict(list)
        self.total, self.on = Counter(), Counter()
        known = {""}
        for path in (*listing.dirs, *(f.rpartition("/")[0] for f in self.every)):
            while path not in known:
                known.add(path)
                parent, _, name = path.rpartition("/")
                self.dirs[parent].append(name)
                path = parent
        for f in self.every:
            parent, _, name = f.rpartition("/")
            self.files[parent].append(name)
            on = f in self.ticked
            for at in _above(f):
                self.total[at] += 1
                self.on[at] += on

    def state(self, directory: str) -> str:
        total, on = self.total[directory], self.on[directory]
        return EMPTY if not total else ALL if on == total else NONE if not on else MIXED

    def lines(self, *, groups: bool = True, modes: dict | None = None) -> list:
        """The lines, a group per directory reached, each in its cheaper mode
        or the one ``modes`` gives it ("in" or "out", by directory, for the
        tests' search of every form). A directory ticked whole needs no group,
        and is visited only when ``modes`` may give one."""
        out, todo = [], [""]
        while todo:
            directory = todo.pop()
            out += self._group(directory, groups, (modes or {}).get(directory))
            below = (_join(directory, c) for c in self.dirs[directory])
            todo += _sorted(c for c in below if self.state(c) == MIXED or modes is not None and self.state(c) == ALL)[::-1]
        return out

    def _group(self, directory: str, groups: bool, mode: str | None) -> list:
        at = "".join(f"/{_escape(part)}" for part in directory.split("/")) if directory else ""
        names, subdirs = self.files[directory], self.dirs[directory]
        ticked = [n for n in names if _join(directory, n) in self.ticked]
        unticked = [n for n in names if _join(directory, n) not in self.ticked]
        states = {c: self.state(_join(directory, c)) for c in subdirs}
        inward = [f"{at}/{x}" for x in _names(unticked, names, subdirs, groups)]
        inward += [f"{at}/{_escape(c)}/" for c in _sorted(c for c in subdirs if states[c] == NONE)]
        outward = [f"{at}/*", *(f"!{at}/{x}" for x in _names(ticked, names, subdirs, groups))]
        outward += [f"!{at}/{_escape(c)}/" for c in _sorted(c for c in subdirs if states[c] in (ALL, MIXED))]
        if mode is None:
            mode = "in" if len(inward) <= len(outward) else "out"
        return inward if mode == "in" else outward


def _names(these, names, subdirs, groups: bool) -> list:
    """What a group's lines name for ``these`` files of a directory holding
    ``names`` and ``subdirs``: each escaped, or ``*.e`` for two or more of an
    extension when every file of it there is one of ``these``, no
    subdirectory's name would match it and it holds no wildcard."""
    out, grouped = [], set()
    if groups:
        here, every, dirs = Counter(map(_ext, these)), Counter(map(_ext, names)), set(map(_ext, subdirs))
        for e, k in here.items():
            if e and k >= 2 and k == every[e] and e not in dirs and not any(c in e for c in _SPECIAL):
                grouped.add(e)
                out.append(f"*.{_escape(e)}")
    out += [_escape(n) for n in these if _ext(n) not in grouped]
    return _sorted(out)


def _text(lines) -> str:
    return HEADER + "\n" + "".join(f"{line}\n" for line in lines)


def write(listing: Listing, selected) -> str:
    """The ``.gammaignore`` that tracks exactly the files of ``listing`` that
    ``selected`` holds (vault paths), the shortest of its kind (the module
    doc): ``HEADER``, then the groups, LF, no BOM. Read back over the
    listing, and written without extension groups when the two differ;
    ``Inexact`` when that form too reads back otherwise (two names the
    matcher reads alike that the scan keeps apart), rather than a file that
    leaves out what was ticked."""
    plan = _Plan(listing, selected)
    text = _text(plan.lines())
    if tracked(Rules.parse(text), plan.every) != plan.ticked:
        log.warning("ignore_rules.write: the shortest .gammaignore read back otherwise; written without extension groups")
        text = _text(plan.lines(groups=False))
        if tracked(Rules.parse(text), plan.every) != plan.ticked:
            raise Inexact("no .gammaignore says this selection: two of its names read alike")
    return text
