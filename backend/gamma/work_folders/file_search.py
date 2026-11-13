"""A work folder's files found by what a person types (quick open's Files
group, ``GET /api/files``; docs/dev/work_folders.md "Files by name"): the
one place the rule is stated. The client draws the rows in the order given
and marks the spans given, and ranks nothing itself.

The rule is basename first, as an editor's quick open: a query holding
``/`` or ``\\`` is one path term, spaces and all (``old drafts/v1``: the
segment ``old drafts``, the leaf ``v1``), its segments matched in order
against the file's directories and its leaf against the file's name; any
other query is name terms, split on whitespace, each matched against the
file's name alone, never its directories (``figures fig1`` finds nothing,
``figures/fig1`` finds ``figures/fig1.pdf``). So whatever must be in the
name is what the SQL prefilter (``runs``) tests. Both sides are folded
(``fold``): NFC, ``lower()`` (``blocks_store.named``'s rule, never
``casefold()``), and ``-``, ``_`` and ``.`` read as a space, so ``fig 1``
finds ``fig_1.png``. No typo pass: a LIKE cannot carry one. A folder whose
name holds ``/`` reads as two segments.

A name is a file's ``file_name``, or a note's title before its vault names
it (``db.SQL_FILE_NAME``); a directory is its path below W (``paths.
dir_path``), "" for W.
"""

import re
import unicodedata
from typing import Callable, NamedTuple

SEPARATORS = "-_."   # interchangeable with a space inside a name
MAX_RUNS = 8         # the prefilter's LIKEs at most
_FOLD = str.maketrans({c: " " for c in SEPARATORS})
_PATH = re.compile(r"[\\/]")
_RUN = re.compile(r"[A-Za-z0-9]+")


def fold(text: str) -> str:
    """``text`` as the rule compares it: NFC, ``lower()``, each of
    SEPARATORS a space. ``lower()`` may lengthen a string ("İ" is two code
    points lowered), so a span found in the folded text is a span of the
    text only when the two are as long: ``score`` reports none otherwise."""
    return unicodedata.normalize("NFC", text).lower().translate(_FOLD)


class Query(NamedTuple):
    names: list      # the name terms, folded
    path: list | None  # a path term's directory segments, folded; None for name terms
    leaf: str        # a path term's leaf, folded ("" for a directory alone)
    phrase: str      # the whole query folded, for name terms ("" for a path term)


def _term(text: str) -> str:
    folded = fold(text)
    return folded if folded.strip() else ""


def parse(q: str) -> Query | None:
    """The query ``q`` names, or None when it names nothing (empty, or
    only whitespace and separators): no rows, and no error."""
    q = unicodedata.normalize("NFC", str(q or "")).strip()
    if not q:
        return None
    if _PATH.search(q):
        *segments, last = _PATH.split(q)
        path, leaf = [t for t in (_term(p.strip()) for p in segments) if t], _term(last.strip())
        return Query([], path, leaf, "") if path or leaf else None
    names = [t for t in (_term(p) for p in q.split()) if t]
    if not names:
        return None
    return Query(names, None, "", " ".join(fold(q).split()))


def runs(query: Query) -> list[str]:
    """What the prefilter ANDs, one ``LIKE '%run%'`` each: the maximal runs
    of ASCII letters and digits in the name terms and the leaf, no repeats,
    longest first, at most MAX_RUNS of them (a pasted paragraph would
    otherwise pass SQLite's expression depth; the ranker still tests every
    term). LIKE folds ASCII case alone, which is all a run holds; the four
    letters Python lowers onto ASCII and LIKE does not
    (``textnorm.literal_runs``) are the one thing it drops. No run (a
    query in another script) is no LIKE: every file is read and ranked."""
    found = {r for t in [*query.names, query.leaf] for r in _RUN.findall(t)}
    return sorted(found, key=lambda r: (-len(r), r))[:MAX_RUNS]


def _in_name(term: str, name: str) -> tuple[int, int]:
    """(points, where) of ``term`` in the folded ``name``: at its start
    100, at a word's start 60, elsewhere 30; (0, -1) when it is not in."""
    if name.startswith(term):
        return 100, 0
    at = name.find(" " + term)
    if at >= 0:
        return 60, at + 1
    at = name.find(term)
    return (30, at) if at >= 0 else (0, -1)


def _merged(spans: list) -> list:
    out: list = []
    for a, b in sorted(spans):
        if out and a <= out[-1][1]:
            out[-1][1] = max(out[-1][1], b)
        else:
            out.append([a, b])
    return out


def score(query: Query, name: str, directory: str) -> tuple[int, list, list]:
    """``(points, name spans, directory spans)`` of a file named ``name`` in
    the directory ``directory`` (its path below W); 0 points is no match.
    Each name term, and a path's leaf, must be in the name (100 at its
    start, 60 at a word's start, 30 elsewhere). A path's segments must each
    be in one of the directory's segments, in order, not necessarily
    adjacent (20 at a segment's start, 10 elsewhere); a path with no leaf
    matches every file below. Name terms only: the whole query equal to
    the name +200, else at its start +40, elsewhere in it +20. The spans
    are half-open code-point ranges, sorted and merged."""
    return _score(query, name, fold(name), directory, fold(directory))


def _terms(query: Query) -> list[str]:
    """What must be in a file's folded name."""
    return [*query.names, *([query.leaf] if query.leaf else [])]


def _score(query: Query, name: str, fname: str, directory: str, fdir: str) -> tuple[int, list, list]:
    points, name_spans, dir_spans = 0, [], []
    for term in _terms(query):
        got, at = _in_name(term, fname)
        if not got:
            return 0, [], []
        points += got
        name_spans.append([at, at + len(term)])
    if query.path is not None:
        segments = fdir.split("/") if fdir else []
        offsets, start = [0], 0
        for seg in segments:
            offsets.append(offsets[-1] + len(seg) + 1)
        for wanted in query.path:
            while start < len(segments) and wanted not in segments[start]:
                start += 1
            if start == len(segments):
                return 0, [], []
            at = segments[start].find(wanted)
            points += 20 if at == 0 else 10
            dir_spans.append([offsets[start] + at, offsets[start] + at + len(wanted)])
            start += 1
    if query.names:
        if fname == query.phrase:
            points += 200
        elif fname.startswith(query.phrase):
            points += 40
        elif query.phrase in fname:
            points += 20
    if len(fname) != len(name):
        name_spans = []
    if len(fdir) != len(directory):
        dir_spans = []
    return points, _merged(name_spans), _merged(dir_spans)


def rank(rows, query: Query, dir_of: Callable[[str], str], limit: int) -> tuple[list, int]:
    """The rows ``(name, first_folder, id)`` that match, as ``(row, points,
    name spans, directory spans)``: by points (most first), the shorter
    name, the shallower directory, the folded name, the id; the first
    ``limit`` and how many matched. ``dir_of(first_folder)`` is the
    directory's path below W, asked once a directory and only for a row
    whose name holds every term."""
    terms, dirs, found = _terms(query), {}, []
    for row in rows:
        name, folder, page_id = row[0] or "", row[1] or "", row[2]
        fname = fold(name)
        if not all(t in fname for t in terms):
            continue
        if folder not in dirs:
            directory = dir_of(folder)
            dirs[folder] = (directory, fold(directory), directory.count("/") + 1 if directory else 0)
        directory, fdir, depth = dirs[folder]
        points, name_spans, dir_spans = _score(query, name, fname, directory, fdir)
        if points:
            found.append(((-points, len(name), depth, fname, page_id), (row, points, name_spans, dir_spans)))
    found.sort(key=lambda f: f[0])
    return [f[1] for f in found[:limit]], len(found)
