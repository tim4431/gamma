"""Ranges of a text file after the file changes: each one is found again
in the new version by its quote and moved to where that text is now
(docs/dev/highlight_relocation.md "Text files").

A range's place is its lines and columns (``text_position``,
gamma/highlights.py), good for the version it was measured on. What stays
is the text, and a range carries its own: ``properties.quote`` and the
forty characters before and after it (``prefix``, ``suffix``). So the run
needs only the new file, never the old one, and keeps no version of it:
that is where it differs from the PDF's (gamma/highlight_relocate.py),
whose thresholds, flags, words and write it shares.

The text is compared folded (``fold_line``): a carriage return dropped,
composed (NFC, not NFKC: ``≠`` and ``!=`` are not one), every run of
whitespace one space and the line's ends stripped, case kept (an
identifier's case is its own). So a formatter's re-indentation, tabs for
spaces and CRLF for LF leave a range where it is; a renamed identifier
does not. A quote is folded line by line.

The ladder, each range from its reference (its own place, or a pending
one's ``prev``: the last place someone confirmed):

1. Exactly, near its old line: the quote's folded lines in a row, its
   first line the end of a line and its last the start of one unless the
   selection took them whole, on word edges unless it began or ended
   inside a word. Copies are ranked by how alike their surroundings read
   to the stored ``prefix`` and ``suffix`` (a fifth of it, as for PDFs),
   then by nearness to the old line. Found, it is solid at its new lines,
   its quote and context as the new file spells them.
2. Word by word, from ``exact_chars`` characters on: the windows around
   the lines that hold the quote's rarest words, each aligned with the
   quote (by characters, by tokens for a longer one, by whole lines for a
   longer one still: ``_blocks``, each within ``ROUGH_BUDGET``), the place
   set by the runs of one alignment, and read against it (difflib's
   ratio), ranked with the context and nearness. The best from
   ``min_match`` on is drawn dashed (``approx``, ``match``, ``prev``, the
   reference's span and whole quote).
3. Lost and kept: ``lost``, at its old place (a pending one back at its
   reference), with ``closest`` when the best window read at least
   ``CLOSEST_MIN`` alike. The viewer draws no mark for it over the text now
   at those lines.

A range already measured on the new version whose quote stands at its
lines is kept without a search, and nothing is written for it.

Pure parts first (``fold_line``, ``prepare``, ``find_range``, ``place_all``:
tests/test_text_anchors.py); the file and the write under ``relocate``.
"""

import re
import unicodedata
from bisect import bisect_left, bisect_right
from collections import Counter, defaultdict
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import NamedTuple

from .. import highlight_relocate as pdf_run
from .. import highlights as shape
from ..blocks_store import load_json
from ..db import connect_pages_db, safe_doc_id
from ..storage import content_digest, find_upload_file
from . import text as doctext
from .model import page_document, stored_name

TEXT_FLAGS = ("lost", "approx", "match", "prev", "closest")  # what a run writes beside the place (hash is the place's)
TEXT_INLINE_BYTES = 512 * 1024          # a file up to this size is placed in the replacement's own thread
TEXT_RELOCATE_MAX_BYTES = 16 * 1024 * 1024  # the most a run places in: what the viewer shows whole (textLoad.MAX_TEXT_BYTES,
                                            # both held to tests/shared/textposition.json max_text_bytes)
MAX_EXACT = 500           # exact copies of a quote ranked by their surroundings: the nearest to its old line
SEED_REACH = 32           # lines of a seed word taken on either side of the old line
MIN_BLOCK = 3             # characters a run of the rough alignment needs to set where a window's match starts or ends
# The most work one alignment of a quote with a window may take (_cost: the
# lookups difflib makes, about 150 ns each, a few times that on very
# repetitive text), in characters, else in tokens, past which they are
# aligned by whole lines (_blocks): a few milliseconds a window.
ROUGH_BUDGET = 20_000

_WORD_RE = re.compile(r"\w+")
_TOKEN_RE = re.compile(r"\w+|[^\w ]")


# --- the fold --------------------------------------------------------------------------

def _clusters(s: str):
    """``(start, end)`` of each character with the combining marks after it."""
    i, n = 0, len(s)
    while i < n:
        j = i + 1
        while j < n and unicodedata.combining(s[j]):
            j += 1
        yield i, j
        i = j


def _composed(s: str) -> str:
    if unicodedata.is_normalized("NFC", s):
        return s
    return "".join(unicodedata.normalize("NFC", s[i:j]) for i, j in _clusters(s))


def fold_line(s: str) -> str:
    """A line (or any text) as the run compares it: ``\\r`` removed, each
    character composed with the marks after it (NFC), every run of
    whitespace one space, leading and trailing whitespace stripped. Case
    and every other character are kept."""
    if "\r" in s:
        s = s.replace("\r", "")
    if not s.isascii():
        s = _composed(s)
    return " ".join(s.split())


class _Folded(NamedTuple):
    """A line folded, with where each folded character came from in it:
    ``starts[i]`` the raw offset its source starts at, ``ends[i]`` the one
    after it ends."""
    text: str
    starts: list
    ends: list


def _fold_map(line: str) -> _Folded:
    """``fold_line(line)`` and the raw offsets of each of its characters."""
    keep = [i for i, c in enumerate(line) if c != "\r"]
    bare = "".join(line[i] for i in keep)
    chars, starts, ends = [], [], []
    for i, j in _clusters(bare):
        piece = bare[i:j]
        piece = piece if piece.isascii() else _composed(piece)
        for c in piece:
            chars.append(c)
            starts.append(keep[i])
            ends.append(keep[j - 1] + 1)
    out, out_starts, out_ends = [], [], []
    for c, a, b in zip(chars, starts, ends):
        if c.isspace():
            if out and out[-1] == " ":
                out_ends[-1] = b
                continue
            c = " "
        out.append(c)
        out_starts.append(a)
        out_ends.append(b)
    while out and out[0] == " ":
        del out[0], out_starts[0], out_ends[0]
    while out and out[-1] == " ":
        out.pop(), out_starts.pop(), out_ends.pop()
    return _Folded("".join(out), out_starts, out_ends)


def _start_col(line: str, folded: _Folded, at: int) -> int:
    """The raw column a match starting at folded column ``at`` starts at;
    the line's end for one starting past its text (a quote that begins with
    the line break)."""
    return folded.starts[at] if at < len(folded.text) else len(line)


def _end_col(line: str, folded: _Folded, at: int) -> int:
    """The raw column a match ending at folded column ``at`` ends at; for
    one ending before any text (the quote's last line was whitespace), the
    line's indentation."""
    if at > 0:
        return folded.ends[at - 1]
    return folded.starts[0] if folded.text else len(line)


def _word(c: str) -> bool:
    return c.isalnum() or c == "_"


# --- the new file ------------------------------------------------------------------------

@dataclass(frozen=True)
class Text:
    """The new file, folded once: its raw ``lines``; ``folded`` each one's
    ``fold_line``; ``where`` a folded line's 1-based line numbers; ``words``
    a word's (``\\w+`` of a folded line, case kept), the rough search's
    seeds, in order; ``joined`` the folded lines joined with ``\\n``, where
    a quote's folded lines are looked for as one string, and ``starts``
    where each line starts in it."""
    lines: list
    folded: list
    where: dict
    words: dict
    joined: str
    starts: list


def prepare(lines: list[str]) -> Text:
    """``Text`` of the decoded file's ``lines`` (documents/text.split_lines):
    linear in its characters."""
    folded = [fold_line(line) for line in lines]
    where, words, starts, at = defaultdict(list), defaultdict(list), [], 0
    find = _WORD_RE.findall
    for no, f in enumerate(folded, start=1):
        where[f].append(no)
        for w in set(find(f)):
            words[w].append(no)
        starts.append(at)
        at += len(f) + 1
    return Text(lines, folded, dict(where), dict(words), "\n".join(folded), starts)


class Found(NamedTuple):
    """Where ``find_range`` placed a range: its span in the new file's raw
    lines, its quote as the new file spells it (``highlights.span_text``),
    that text's own context, how alike the quote and it read (1.0 exact)
    and whether the copy was exact. ``below``: the best place found, under
    the threshold a match is applied from, given only to a caller that asks
    (``closest``)."""
    line: int
    col: int
    end: int
    end_col: int
    quote: str
    match: float
    exact: bool
    prefix: str = ""
    suffix: str = ""
    below: bool = False


class _Spot(NamedTuple):
    line: int
    col: int
    end: int
    end_col: int
    quote: str
    prefix: str
    suffix: str


class _Fit:
    """How alike a place's surroundings read to the stored ones (0 to 1,
    0.5 for a side stored empty): the mean of the two sides."""

    def __init__(self, prefix: str, suffix: str):
        self.prefix, self.suffix = fold_line(prefix), fold_line(suffix)

    def __call__(self, spot: _Spot) -> float:
        before = pdf_run._similar(self.prefix, fold_line(spot.prefix)) if self.prefix else 0.5
        after = pdf_run._similar(self.suffix, fold_line(spot.suffix)) if self.suffix else 0.5
        return (before + after) / 2


class _Reader:
    """The raw spans of folded offsets in ``joined``, each line's fold map
    made once."""

    def __init__(self, text: Text):
        self.text = text
        self.maps = {}

    def folded(self, no: int) -> _Folded:
        if no not in self.maps:
            self.maps[no] = _fold_map(self.text.lines[no - 1])
        return self.maps[no]

    def line_of(self, at: int) -> int:
        return bisect_right(self.text.starts, at)

    def spot(self, a: int, b: int, *, whole_first=False, whole_last=False) -> _Spot | None:
        """The raw span of ``joined[a:b]`` (``whole_first``: from its first
        line's start; ``whole_last``: to its last line's end), clamped and
        trimmed as it is stored, with its text and context."""
        text = self.text
        line, end = self.line_of(a), self.line_of(b)
        raw_line, raw_end = text.lines[line - 1], text.lines[end - 1]
        col = 0 if whole_first else _start_col(raw_line, self.folded(line), a - text.starts[line - 1])
        end_col = len(raw_end) if whole_last else _end_col(raw_end, self.folded(end), b - text.starts[end - 1])
        span = shape.clamp_span(text.lines, (line, col, end, end_col))
        if span is None:
            return None
        quote, prefix, suffix = shape.span_text(text.lines, span)
        return _Spot(*span, quote, prefix, suffix)


def _found(spot: _Spot, match: float, exact: bool, below: bool = False) -> Found:
    return Found(spot.line, spot.col, spot.end, spot.end_col, spot.quote, match, exact, spot.prefix, spot.suffix, below)


def _exact(text: Text, reader: _Reader, needle: dict, quote_lines: list[str], fit: _Fit) -> Found | None:
    """The best exact copy of the folded quote lines (``find_range``'s step 1)."""
    prefix, suffix, cut = needle.get("prefix") or "", needle.get("suffix") or "", bool(needle.get("cut"))
    whole_first = prefix == "" or prefix.endswith("\n")
    whole_last = not cut and (suffix == "" or suffix.startswith(("\n", "\r")))
    folded = "\n".join(quote_lines)
    edge_start = folded[:1] != "" and _word(folded[0]) and not (prefix and _word(prefix[-1]))
    edge_end = not cut and folded[-1:] != "" and _word(folded[-1]) and not (suffix and _word(suffix[0]))
    joined, n = text.joined, len(text.joined)
    hits = []
    if len(quote_lines) == 1 and whole_first and whole_last:
        hits = [text.starts[no - 1] for no in text.where.get(folded, ())]
    else:
        at = joined.find(folded)
        while at >= 0:
            stop = at + len(folded)
            if not ((whole_first and at > 0 and joined[at - 1] != "\n")
                    or (whole_last and stop < n and joined[stop] != "\n")
                    or (edge_start and at > 0 and _word(joined[at - 1]))
                    or (edge_end and stop < n and _word(joined[stop]))):
                hits.append(at)
            at = joined.find(folded, at + 1)
    if not hits:
        return None
    ref = needle.get("line") or 1
    if len(hits) > MAX_EXACT:
        hits = sorted(hits, key=lambda a: abs(reader.line_of(a) - ref))[:MAX_EXACT]
    best = None
    for at in hits:
        spot = reader.spot(at, at + len(folded), whole_first=whole_first, whole_last=whole_last)
        if spot is None:
            continue
        rank = (1.0 + pdf_run.CONTEXT_WEIGHT * fit(spot), -abs(spot.line - ref), -spot.line)
        if best is None or rank > best[0]:
            best = (rank, spot)
    return _found(best[1], 1.0, True) if best else None


def _windows(text: Text, words: set, k: int, ref: int) -> list[tuple[int, int]]:
    """The rough search's windows, ``(first, last)`` lines: around the lines
    that hold the quote's ``MAX_SEEDS`` rarest words the file knows (each
    word's ``SEED_REACH`` lines nearest the old line on either side), ``k +
    1`` lines either way of each, those holding the most of the seeds
    first, then the nearest; at most ``MAX_SEEDS``."""
    seeds = sorted((w for w in words if w in text.words), key=lambda w: (len(text.words[w]), w))[:pdf_run.MAX_SEEDS]
    lines = set()
    for w in seeds:
        found = text.words[w]
        at = bisect_left(found, ref)
        lines.update(found[max(0, at - SEED_REACH):at + SEED_REACH])
    n = len(text.lines)
    scored = []
    for s in lines:
        a, b = max(1, s - k - 1), min(n, s + k + 1)
        held = sum(1 for w in seeds if bisect_right(text.words[w], b) > bisect_left(text.words[w], a))
        scored.append((-held, abs(s - ref), s, (a, b)))
    out = []
    for *_, window in sorted(scored):
        if window not in out:
            out.append(window)
        if len(out) == pdf_run.MAX_SEEDS:
            break
    return out


class _Block(NamedTuple):
    """A run the quote and a window share, in characters: from ``a`` in the
    quote and ``b`` in the window, ``qsize`` and ``wsize`` long on each
    side (the same but where the two space their tokens otherwise)."""
    a: int
    b: int
    qsize: int
    wsize: int


def _units(s: str, pattern) -> tuple[list[str], list[int]]:
    """``s`` cut into units by ``pattern``: their texts and offsets."""
    found = list(pattern.finditer(s))
    return [m.group() for m in found], [m.start() for m in found]


def _unit_blocks(q: tuple[list[str], list[int]], w: tuple[list[str], list[int]]) -> list[_Block]:
    """The runs of equal units two texts share, in characters."""
    (qu, qo), (wu, wo) = q, w
    out = []
    for m in SequenceMatcher(None, qu, wu, autojunk=False).get_matching_blocks():
        if m.size:
            i, j = m.a + m.size - 1, m.b + m.size - 1
            out.append(_Block(qo[m.a], wo[m.b], qo[i] + len(qu[i]) - qo[m.a], wo[j] + len(wu[j]) - wo[m.b]))
    return out


def _line_units(lines: list[str]) -> tuple[list[str], list[int]]:
    at, starts = 0, []
    for line in lines:
        starts.append(at)
        at += len(line) + 1
    return lines, starts


def _cost(a, b) -> int:
    """What aligning ``a`` with ``b`` costs difflib, about: each unit of
    ``a`` is looked up among its copies in ``b``."""
    copies = Counter(b)
    return sum(n * copies[x] for x, n in Counter(a).items())


def _blocks(q: str, w: str, quote_lines: list[str], window_lines: list[str]) -> tuple[str, list[_Block]]:
    """The runs the quote ``q`` and the window ``w`` (their folded lines
    joined) share and what they were aligned by: by characters while that
    costs at most ``ROUGH_BUDGET`` (``_cost``), else by tokens
    (``_TOKEN_RE``) while those do, else by whole lines, blank ones left
    out, of which a range this long that still reads like its old text
    keeps some unchanged. ``("chars" | "tokens" | "lines", blocks)``."""
    if _cost(q, w) <= ROUGH_BUDGET:
        blocks = SequenceMatcher(None, q, w, autojunk=False).get_matching_blocks()
        return "chars", [_Block(m.a, m.b, m.size, m.size) for m in blocks if m.size]
    tq, tw = _units(q, _TOKEN_RE), _units(w, _TOKEN_RE)
    if _cost(tq[0], tw[0]) <= ROUGH_BUDGET:
        return "tokens", _unit_blocks(tq, tw)
    return "lines", [m for m in _unit_blocks(_line_units(quote_lines), _line_units(window_lines)) if m.qsize]


def _shared(q: str, w: str) -> int:
    """How many characters two stretches share, by the finest alignment
    ``_blocks`` affords them (0 past its budget)."""
    if not q or not w:
        return 0
    if _cost(q, w) <= ROUGH_BUDGET:
        return sum(m.size for m in SequenceMatcher(None, q, w, autojunk=False).get_matching_blocks())
    tq, tw = _units(q, _TOKEN_RE), _units(w, _TOKEN_RE)
    if _cost(tq[0], tw[0]) > ROUGH_BUDGET:
        return 0
    return sum(min(m.qsize, m.wsize) for m in _unit_blocks(tq, tw))


def _aligned(blocks: list[_Block]) -> list[_Block]:
    """The blocks of one alignment: the longest, and on either side each
    next one while the window's gap to it is longer than the quote's by no
    more than half the block itself, so a stray few characters lines
    away (the quote's edited head matched in a line above) do not stretch
    the place."""
    i = max(range(len(blocks)), key=lambda n: (min(blocks[n].qsize, blocks[n].wsize), -n))
    lo = hi = i
    while lo > 0:
        p, c = blocks[lo - 1], blocks[lo]
        if 2 * ((c.b - p.b - p.wsize) - (c.a - p.a - p.qsize)) > min(p.qsize, p.wsize):
            break
        lo -= 1
    while hi < len(blocks) - 1:
        c, n = blocks[hi], blocks[hi + 1]
        if 2 * ((n.b - c.b - c.wsize) - (n.a - c.a - c.qsize)) > min(n.qsize, n.wsize):
            break
        hi += 1
    return blocks[lo:hi + 1]


def _rough(text: Text, reader: _Reader, needle: dict, quote_lines: list[str], fit: _Fit,
           min_match: float, closest: float | None) -> Found | None:
    """The best window that reads like the quote (``find_range``'s step 2).
    In each window the runs the two share (``_blocks``) of one alignment
    (``_aligned``) set where the place starts and ends; the quote's unmatched
    head and tail stretch it over as many lines as they hold, and an edge
    the quote had on a word or a line is kept. How alike the two read is
    difflib's ratio, or, for a pair too long to align by characters, the
    same ratio of the characters the runs and the stretches between them
    share (``_shared``), so a place's cost is bounded by ``ROUGH_BUDGET``
    whatever the quote's length."""
    folded = "\n".join(quote_lines)
    ref = needle.get("line") or 1
    prefix, suffix, cut = needle.get("prefix") or "", needle.get("suffix") or "", bool(needle.get("cut"))
    whole_first = prefix == "" or prefix.endswith("\n")
    whole_last = not cut and (suffix == "" or suffix.startswith(("\n", "\r")))
    edge_start = _word(folded[0]) and not (prefix and _word(prefix[-1]))
    edge_end = not cut and _word(folded[-1]) and not (suffix and _word(suffix[0]))
    best = below = None
    for a, b in _windows(text, set(_WORD_RE.findall(folded)), len(quote_lines), ref):
        pdf_run._breathe()
        base = text.starts[a - 1]
        window = text.joined[base:text.starts[b - 1] + len(text.folded[b - 1])]
        window_lines = text.folded[a - 1:b]
        starts = [text.starts[no - 1] - base for no in range(a, b + 1)]
        ends = [at + len(line) for at, line in zip(starts, window_lines)]
        by, blocks = _blocks(folded, window, quote_lines, window_lines)
        blocks = [m for m in blocks if m.qsize >= MIN_BLOCK] or blocks
        if not blocks:
            continue
        kept = _aligned(blocks)
        first, last = kept[0], kept[-1]
        head, tail = folded[:first.a], folded[last.a + last.qsize:]
        j = max(0, bisect_right(starts, first.b) - 1 - head.count("\n"))
        if whole_first:
            s = starts[j]
        elif "\n" in head:
            s = max(starts[j], ends[j] - len(quote_lines[0]))
        else:
            s = max(starts[j], first.b - first.a)
        s = min(s, first.b)
        stop = last.b + last.wsize
        j = min(len(starts) - 1, bisect_right(starts, max(last.b, stop - 1)) - 1 + tail.count("\n"))
        if whole_last:
            e = ends[j]
        elif "\n" in tail:
            e = min(ends[j], starts[j] + len(quote_lines[-1]))
        else:
            e = min(ends[j], stop + len(tail))
        e = max(e, stop)
        if edge_start:
            while 0 < s < len(window) and _word(window[s - 1]) and _word(window[s]):
                s -= 1
        if edge_end:
            while 0 < e < len(window) and _word(window[e - 1]) and _word(window[e]):
                e += 1
        while s < e and window[s] in " \n":
            s += 1
        while e > s and window[e - 1] in " \n":
            e -= 1
        if e <= s:
            continue
        if by == "chars":
            match = pdf_run._similar(folded, window[s:e])
        else:
            same = sum(min(m.qsize, m.wsize) for m in kept)
            same += sum(_shared(folded[p.a + p.qsize:n.a], window[p.b + p.wsize:n.b]) for p, n in zip(kept, kept[1:]))
            same += _shared(head, window[s:max(s, first.b)]) + _shared(tail, window[min(e, stop):e])
            match = min(1.0, 2 * same / (len(folded) + e - s))
        spot = reader.spot(base + s, base + e)
        if spot is None:
            continue
        rank = (match + pdf_run.CONTEXT_WEIGHT * fit(spot), -abs(spot.line - ref), -spot.line)
        if match >= min_match:
            if best is None or rank > best[0]:
                best = (rank, spot, match)
        elif closest is not None and match >= closest and (below is None or rank > below[0]):
            below = (rank, spot, match)
    if best:
        return _found(best[1], best[2], False)
    return _found(below[1], below[2], False, below=True) if below else None


def find_range(text: Text, needle: dict, *, min_match: float | None = None, exact_chars: int | None = None,
               closest: float | None = None) -> Found | None:
    """Where the range ``needle`` describes stands in ``text``: ``{quote,
    prefix, suffix, line, end}`` (its reference's quote and lines, its
    stored context; ``cut``: the quote is only the first part of the text,
    a quote at the cap, so its last line is a part of a line whatever the
    suffix says). Exactly first, then word by word for a quote of
    ``exact_chars`` folded characters or more, applied from ``min_match``
    (the module's ladder; ``thresholds`` of highlight_relocate, its
    defaults when not given). None when neither places it, or, with
    ``closest``, the best window from that likeness on as a ``Found`` with
    ``below``."""
    min_match = pdf_run.ROUGH_APPLY if min_match is None else min_match
    exact_chars = pdf_run.ROUGH_MIN_CHARS if exact_chars is None else exact_chars
    quote = needle.get("quote") or ""
    quote_lines = [fold_line(q) for q in quote.split("\n")]
    if not "".join(quote_lines) or not text.lines:
        return None
    reader = _Reader(text)
    fit = _Fit(needle.get("prefix") or "", needle.get("suffix") or "")
    found = _exact(text, reader, needle, quote_lines, fit)
    if found or len("\n".join(quote_lines)) < exact_chars:
        return found
    return _rough(text, reader, needle, quote_lines, fit, min_match, closest)


# --- a run over a page -----------------------------------------------------------------

def _stands(text: Text, quote: str, span: tuple) -> bool:
    """Whether ``quote`` (folded) stands at the lines of ``span`` in ``text``."""
    line, _col, end, _end_col = span
    quote_lines = [fold_line(q) for q in quote.split("\n")]
    if end > len(text.lines) or end - line + 1 != len(quote_lines) or not "".join(quote_lines):
        return False
    region = text.joined[text.starts[line - 1]:text.starts[end - 1] + len(text.folded[end - 1])]
    return "\n".join(quote_lines) in region


def _bare(pos: dict) -> dict:
    return {k: v for k, v in pos.items() if k not in TEXT_FLAGS}


def _str(value) -> str:
    return value if isinstance(value, str) else ""


def place_all(text: Text, items: list[tuple[str, dict]], new_hash: str, progress=None, *,
              min_match: float | None = None, exact_chars: int | None = None) -> dict:
    """Each text range's place in ``text``, the version ``new_hash`` of the
    file: ``{"ops": {id: patch}, "kinds": {id: kind}, "highlights",
    "moved", "kept", "approx", "lost"}``. A patch is the properties to
    write: its ``text_position``, and its ``quote`` when that is spelled
    otherwise now. ``kinds`` says which count each range went to:
    ``moved`` (solid at other lines), ``kept`` (solid at its reference's
    first line), ``approx`` (dashed) or ``lost``. ``items`` without a
    ``highlights.text_span`` are skipped and counted nowhere.

    A pending range (``approx`` with a ``prev``) is matched from its
    ``prev``, its own place being a guess nobody approved: found exactly,
    solid; roughly, dashed again with that ``prev``; not at all, lost back
    at it (its lines, quote and version), without ``prev``. Any other is
    matched from its own place; one the run leaves lost keeps it. A range
    measured on ``new_hash`` whose quote stands at its lines is where it
    belongs: nothing is written (a solid one that carries flags, a lost one
    whose version came back, is written solid).

    What a range stores is read as a selection writes it, whoever wrote it:
    its quote at most ``TEXT_QUOTE_MAX`` characters, its prefix and suffix
    their ``CONTEXT_CHARS`` nearest the quote. ``progress`` gets ``done``
    and ``total`` before each range, so a job can stop between two."""
    limits = {"min_match": min_match, "exact_chars": exact_chars}
    ops, kinds, placed = {}, {}, 0
    for i, (bid, props) in enumerate(items):
        if progress:
            progress(done=i, total=len(items), unit="highlights", phase="matching")
        pdf_run._breathe()
        span = shape.text_span(props)
        if span is None:
            continue
        placed += 1
        pos = props["text_position"]
        quote = _str(props.get("quote"))[:shape.TEXT_QUOTE_MAX]
        prev = shape.valid_text_prev(pos.get("prev")) if pos.get("approx") is True else None
        if pos.get("hash") == new_hash and _stands(text, quote, span):
            if prev is not None:
                kinds[bid] = "approx"
            else:
                kinds[bid] = "kept"
                if any(k in pos for k in TEXT_FLAGS):
                    ops[bid] = {"text_position": _bare(pos)}
            continue
        prefix = _str(pos.get("prefix"))[-shape.CONTEXT_CHARS:]
        suffix = _str(pos.get("suffix"))[:shape.CONTEXT_CHARS]
        ref_quote = prev["quote"] if prev else quote
        ref_span = tuple(prev[k] for k in shape.TEXT_SPAN) if prev else span
        ref_line, ref_end = ref_span[0], ref_span[2]
        needle = {"quote": ref_quote, "prefix": prefix, "suffix": suffix, "line": ref_line, "end": ref_end,
                  "cut": len(ref_quote) >= shape.TEXT_QUOTE_MAX}
        found = find_range(text, needle, closest=pdf_run.CLOSEST_MIN, **limits)
        span_of = (found.line, found.col, found.end, found.end_col) if found else None
        if found and not found.below:
            if found.exact:
                kinds[bid] = "kept" if found.line == ref_line else "moved"
                new = shape.text_position(*span_of, hash=new_hash, prefix=found.prefix, suffix=found.suffix)
            else:
                kinds[bid] = "approx"
                new = {**shape.text_position(*span_of, hash=new_hash, prefix=prefix, suffix=suffix), "approx": True,
                       "match": min(round(found.match, 2), 0.99),
                       "prev": prev or {"hash": _str(pos.get("hash")), **dict(zip(shape.TEXT_SPAN, span)),
                                        "quote": quote}}
            patch = {"text_position": new}
            if found.quote != quote:
                patch["quote"] = found.quote
            ops[bid] = patch
            continue
        kinds[bid] = "lost"
        if prev:
            new = {**shape.text_position(*ref_span, hash=prev["hash"], prefix=prefix, suffix=suffix), "lost": True}
        else:
            new = {**_bare(pos), "lost": True}
        if found:
            new["closest"] = {**dict(zip(shape.TEXT_SPAN, span_of)), "match": min(round(found.match, 2), 0.99),
                              "quote": found.quote, "hash": new_hash}
        patch = {"text_position": new}
        if ref_quote != quote:
            patch["quote"] = ref_quote
        if new != pos or "quote" in patch:
            ops[bid] = patch
    counts = Counter(kinds.values())
    return {"ops": ops, "kinds": kinds, "highlights": placed,
            **{kind: counts[kind] for kind in ("moved", "kept", "approx", "lost")}}


def page_ranges(conn, page_id: str) -> list[tuple[str, dict]]:
    """``(id, properties)`` of the page's text ranges whose place can be
    read (``highlights.is_text_range`` and ``text_span``), whatever their
    depth in the notes: the twin of highlight_relocate.page_highlights."""
    rows = conn.execute(
        "WITH RECURSIVE sub(id) AS (SELECT id FROM unified_blocks WHERE parent_id = ? "
        "UNION ALL SELECT b.id FROM unified_blocks b JOIN sub ON b.parent_id = sub.id) "
        "SELECT b.id, b.properties FROM unified_blocks b JOIN sub ON b.id = sub.id "
        "WHERE json_extract(b.properties, '$.text_position.line') IS NOT NULL ORDER BY b.id",
        (page_id,)).fetchall()
    out = []
    for bid, raw in rows:
        try:
            props = load_json(raw or "{}") or {}
        except ValueError:
            continue
        if shape.text_span(props):
            out.append((bid, props))
    return out


def relocate(ws: str, page_id: str, new_doc: str, *, actor: str, progress=None, min_match: float | None = None,
             exact_chars: int | None = None) -> dict:
    """Place the page's text ranges in its file ``new_doc`` and write the
    ones that changed, under ``actor`` (``place_all``), by the thresholds
    ``min_match`` and ``exact_chars`` (the run's owner's,
    highlight_relocate.thresholds; its defaults when not given). The new
    bytes are the stored ``<new_doc><ext>``, else the linked file the page
    reads in place (``find_upload_file``), at most
    ``TEXT_RELOCATE_MAX_BYTES``; they must hash to ``new_doc`` (a position
    stamped with it is measured on those bytes), else the run refuses, and
    the next replacement runs again. The write is highlight_relocate's
    compare-and-set under the page's write lock (``_write``).

    The result: ``{format: "text", highlights, moved, kept, approx, lost,
    changed, min_match, exact_chars}``, as the PDF's run answers.
    ValueError when the page shows no text file, the file is not stored,
    too large, changed while it was read, no text or empty, or from the
    write; every range then stays as it was."""
    limits = {"min_match": pdf_run.ROUGH_APPLY if min_match is None else min_match,
              "exact_chars": pdf_run.ROUGH_MIN_CHARS if exact_chars is None else exact_chars}
    with connect_pages_db(ws) as conn:
        items = page_ranges(conn, page_id)
        row = conn.execute("SELECT properties FROM unified_blocks WHERE id = ?", (page_id,)).fetchone()
    empty = {"format": "text", "highlights": 0, "moved": 0, "kept": 0, "approx": 0, "lost": 0, "changed": 0}
    if not items:
        return {**empty, **limits}
    view = page_document(load_json(row[0] or "{}")) if row else None
    if view is None or view["format"] != "text":
        raise ValueError("the page shows no text file")
    path = find_upload_file(stored_name(safe_doc_id(new_doc), view["ext"]), ws)
    if path is None:
        raise ValueError("the new file is not stored on the server")
    with open(path, "rb") as f:
        data = f.read(TEXT_RELOCATE_MAX_BYTES + 1)
    if len(data) > TEXT_RELOCATE_MAX_BYTES:
        raise ValueError("the file is too large to place its highlights")
    if content_digest(data) != new_doc:
        raise ValueError("the file changed while it was read")
    decoded = doctext.decode(data)
    if decoded.binary:
        raise ValueError(doctext.NOT_TEXT)
    lines = doctext.split_lines(decoded.text)
    if not lines:
        raise ValueError("the file is empty")
    placed = place_all(prepare(lines), items, new_doc, progress, **limits)
    ops = [{"op": "set", "id": bid, "props": patch} for bid, patch in placed.pop("ops").items()]
    kinds = placed.pop("kinds")
    placed["changed"] = 0
    if progress:
        progress(done=placed["highlights"], total=placed["highlights"], unit="highlights", phase="matching")
        progress(stoppable=False)  # the write follows: from here on the run finishes
    if ops:
        pdf_run._write(ws, page_id, new_doc, items, ops, kinds, placed, actor=actor, key="text_position")
    return {"format": "text", **placed, **limits}
