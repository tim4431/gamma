"""Highlights after a page's PDF is replaced: each one is found again in the
new file by its text and moved to where that text is now
(``PUT /pages/{id}/attachment`` starts the ``relocate-highlights`` job,
routers/pages.py; docs/dev/highlight_relocation.md).

A highlight's place is a page and rectangles in that page's frame
(``pdf_position``, gamma/highlights.py), good for the file it was made on
and for nothing else: a paper compiled again, or the published version in
place of the arXiv one, moves every line. What stays is the text. So each
highlight is looked for by its passage: the text under its rectangles in
the file they were measured on (the glyphs whose centre they cover, read
with PyMuPDF like the annotation import's quotes), with the forty
characters before and after it as context, or the quote the viewer stored
when that file is gone or the text under the rectangles is not the quote.

Which file the rectangles were measured on, the position says:
``pdf_position.doc_id`` names that stored file (a page ``doc_id``, the
content hash) when it is not the page's current PDF, and a position
without it was measured on the current one (what the viewer, the imports
and a move by hand write). A replacement writes the outgoing file's id on
every placed highlight that has none, in the transaction that changes the
attachment, so a run that never wrote (stopped, failed, refused) leaves
each highlight still pointing at the file its rectangles belong to. The
run writes moved and approx positions without it; a highlight it marks
lost keeps it, and so does an area nothing placed, so a later run still
reads that highlight from its own file. A text highlight read from the
page's current file whose quote is the text under it is where it belongs,
kept without a search.

A page's text, in both files, is MuPDF's lines joined with a space and
folded (dashes, quotes, ligatures, case), a word hyphenated at a line's
end joined whole, and a line made of a number alone left out: margin line
numbers and page numbers, so a numbered review copy and its camera-ready
read alike. The passage under a highlight is the text of the glyphs it
covers, the stretches it skips (a line's end, a caption the file draws
between two columns) read as one space.

The passage is searched for in the new file's text, every page. Exactly
first, on word edges ("ion" is not the end of "motion") unless the old
passage itself began or ended inside a word; then, from twelve characters
on, roughly: aligned word by word, a word read as another costing how
unlike the two are and a word added or dropped one, no word frozen (a
renumbered citation, a changed number, a stray symbol the old rectangles
took in), also when no exact copy stands in surroundings that read like
the old ones; when no page holds an exact copy, also across each page
break, its two parts searched apart so the running head or footer between
them costs nothing, the highlight going to the page with the larger part,
and, when the old file read the passage in pieces with text between (a
caption between two columns), piece by piece, each within a short reach
of the one before, the text between costing nothing. Each place has a
confidence, how alike the passage and the text found there read
(difflib's ratio, 1.0 for an exact copy), and the places are ranked by it
plus a fifth of how alike their surroundings read to the old file's, so a
passage reworded in place can beat a verbatim copy elsewhere, then by the
page nearest the old one and the place nearest the old rectangles (each
as fractions of its own page). The best is applied when its confidence
reaches ``ROUGH_APPLY`` (set by hand; the account may set its own when
the run starts, ``thresholds``), a passage under twelve characters only
when exact. The glyphs it covers there, line by line, are its new
rectangles, in the new page's frame (the crop box as pdf.js shows it,
rotation applied: pdf_export's mappers). An exact match is drawn solid, a
rough one dashed (``approx``, with its confidence as ``match``) for the
person to check. An area highlight is placed by the text inside it when
it has some (a caption, an axis), else by the picture it covers (the same
image data in the new file), else it rides along with the page's other
highlights when they all moved the same way (``approx``), else it stays,
marked ``approx`` when it was measured on another file. A dashed
placement keeps where the highlight was before (``prev``: the file, page,
rectangles and quote) for the person to compare, and stays dashed through
later runs until the person accepts it or moves it. A highlight whose
text is nowhere, or only in places too unlike it, keeps its old
rectangles and is marked ``lost`` for the viewer to show hollow, until
the person moves it or a later run finds it.

The matching is pure Python and holds the GIL, so it naps for a moment
every few milliseconds (``_breathe``) and the server's requests go on
meanwhile; it reports progress once per page it searches, so a stop
lands within a page. A page's text is kept compactly (``PageText``): a
run reads every page of the new file, a book's included.

Pure parts first (``PageText``, ``normalize_quote``, ``find_passage``,
``rects_for_span``, ``place_all``: tests/test_highlight_relocate.py), the
file reading and the writes under ``relocate``.
"""

import math
import re
import threading
import time
import unicodedata
from array import array
from bisect import bisect_left
from collections import Counter
from contextlib import ExitStack
from difflib import SequenceMatcher
from functools import lru_cache
from statistics import median
from typing import NamedTuple

from . import highlights as shape
from .blocks_store import attachment_file, load_json, page_attachment, write_lock
from .db import connect_pages_db, safe_doc_id
from .logbuf import log
from .ops import MAX_OPS, after_commits, apply_batches
from .pdf_export import display_size, pdf_point_to_viewer, viewer_rect_to_pdf
from .pdf_text import MAX_PAGES, GlyphPages, covers, has_box
from .storage import find_upload_file

CONTEXT_CHARS = 40        # of text before and after a passage, kept from the old file to tell places apart
CONTEXT_WEIGHT = 0.2      # what the context fit (0..1) adds to a place's confidence when places are ranked
# The context fit from which an exact copy stands so plainly in the
# passage's own surroundings that the rough search is not run: only a copy
# at least 98% like the passage, in surroundings that read still more like
# the old ones, could rank above it. Unrelated 40-character windows score
# 0.30 at the median and 0.475 at the 99th percentile, lightly edited ones
# 0.83 to 0.90.
EXACT_SURE = 0.9
# Set by hand, not calibrated: the confidence from which the best place a
# rough match found is applied (and drawn dashed), and the length of a
# passage under which only an exact copy is. The defaults of an account's
# own (``thresholds``).
ROUGH_APPLY = 0.8
ROUGH_MIN_CHARS = 12
# The account preferences that set them (the web app's PREFS, app/prefDefs.js,
# kept in the account's profile), each a whole number with its range and
# step: a percentage, and characters.
MIN_MATCH_PREF, MIN_MATCH_RANGE = "relocateMinMatch", (50, 100, 5)
EXACT_CHARS_PREF, EXACT_CHARS_RANGE = "relocateExactChars", (0, 60, 1)
MIN_NEEDLE = 3            # characters a passage needs to be looked for at all
# For speed only (the confidence decides): the rough alignment gives up
# past this cost per word of the passage...
ROUGH_BOUND = 0.4
# ...and aligns only where the words found unchanged (up to their
# punctuation) hold this share of the passage's letters.
ROUGH_PRESENT = 0.5
# What the rough alignment pays to drop a word of the passage, or to read it
# as a page word less than half like it: a hair more than adding a page word
# (1.0), so that of two runs that would cost the same, the one keeping the
# passage's last (first) word wins over the one ending (starting) short of
# it. "In the cryostat." read in "in a new cryostat." covers "cryostat.",
# not just "in". Far below any real difference of cost.
DROP = 1.0 + 1e-6
MAX_SEEDS = 6             # places on a page the rough search tries
JOIN_SLACK = 20           # words at a page break that may stand between a passage's two parts: a running head, a footnote line, a caption
AREA_MIN_CHARS = 12       # the text inside an area highlight that can place it
AREA_MIN_WORDS = 2
SAME_PLACE = 0.005        # of the page: a position found this close to the old one is "kept"
RIDE_SPREAD = 0.15        # of the page: the shifts of a page's highlights must agree within this to carry an area along
EDGE = 1e-6               # of the page: the rounding a box carried along may lie past the page's edge by
QUOTE_AGREES = 0.6        # the likeness from which the text under a highlight's rectangles is its stored quote (_agrees)
NAP_EVERY = 0.010         # seconds of matching between two naps (_breathe)...
NAP = 0.002               # ...and how long one lasts
INF = float("inf")

CLIENT = "relocate"       # the writer named on the batches
# The doc_id a replacement writes on the highlights of a page that showed its
# file from a URL alone (blocks_store.attachment_file gives ""; a duplicated
# page's /api/uploads/ address is its stored file): no stored file to read
# them from, and no upload has this name (uploads are named by content hash),
# so the run places them by their quotes, as for a stored file that is gone.
UNSTORED = "unstored"

_DASHES = "‐‑‒–—―−"
_QUOTES = {"‘": "'", "’": "'", "‚": "'", "′": "'",
           "“": '"', "”": '"', "„": '"', "″": '"'}
_SOFT_HYPHEN = "­"
_PUNCT = ".,;:!?()[]{}'\"«»<>"
_HYPHEN_BREAK_RE = re.compile(rf"(?<=\w)[-{_DASHES}][ \t]*\n\s*(?=\w)")
_SPACES_RE = re.compile(r" {2,}")
_WORD_RE = re.compile(r"\S+")
_LINE_NUMBER_RE = re.compile(r"[0-9]{1,5}")
_DIGIT_RE = re.compile(r"[0-9]")


def _fold(ch: str) -> str:
    """One character as the search sees it: dashes one hyphen, curly quotes
    straight, a soft hyphen nothing, any whitespace a space, ligatures
    their letters (NFKC), case folded."""
    if ch in _DASHES:
        return "-"
    if ch in _QUOTES:
        return _QUOTES[ch]
    if ch == _SOFT_HYPHEN:
        return ""
    if ch.isspace():
        return " "
    return "".join(" " if c.isspace() else c for c in unicodedata.normalize("NFKC", ch).casefold())


def normalize_quote(text: str) -> str:
    """A stored quote (the viewer's text of a selection) in the search's
    form: a word broken by a hyphen at a line's end joined, then every
    character folded and the whitespace collapsed."""
    text = _HYPHEN_BREAK_RE.sub("", text or "")
    return _SPACES_RE.sub(" ", "".join(_fold(c) for c in text)).strip()


_clock = threading.local()


def _breathe() -> None:
    """Let the server's other threads run during a long match. A stretch of
    pure Python holds the GIL, and a thread that wants it back (a request
    returning from its socket or its database) waits out the switch
    interval, 5 ms, at every handoff, so a request of a few milliseconds
    takes a second or more while a run matches. A short sleep once
    ``NAP_EVERY`` seconds of matching have run hands the GIL over at once.
    The clock is the thread's own, so two runs keep their own time. (A
    worker process would leave the GIL alone altogether, but the iPad's
    embedded Python cannot spawn one, and the frozen desktop backend would
    need freeze_support.)"""
    now = time.perf_counter()
    last = getattr(_clock, "last", None)
    if last is None:
        _clock.last = now
    elif now - last >= NAP_EVERY:
        time.sleep(NAP)
        _clock.last = time.perf_counter()


def _without_line_numbers(glyphs: list) -> list:
    """``glyphs`` without MuPDF's lines that are a number of one to five
    digits alone, each with its line break: margin line numbers (lineno's
    review copies) and a page number on a line of its own, which the other
    version of the paper does not have."""
    out, line = [], []
    for glyph in glyphs:
        line.append(glyph)
        if glyph[0] == "\n":
            if not _LINE_NUMBER_RE.fullmatch("".join(ch for ch, _box in line).strip()):
                out.extend(line)
            line = []
    out.extend(line)
    return out


class PageText:
    """A page's glyphs (``pdf_text.GlyphPages.page``) as one normalized
    string, each character remembering the glyph it came from: MuPDF's
    lines joined with a space, a word hyphenated across two lines joined
    whole, everything folded as ``normalize_quote`` folds a quote, and a
    line that is a number alone left out, its glyphs too
    (``_without_line_numbers``): every index here counts the glyphs that
    are left.

    A run reads every page of the new file and keeps them until it ends,
    so a page is kept compactly: the glyphs' chars as one string, their
    boxes as one flat array of four numbers each (``char``, ``box``,
    ``glyph_count``), the text's glyph indices and the words' edges as
    arrays."""

    def __init__(self, glyphs: list):
        glyphs = _without_line_numbers(glyphs)
        chars = [ch for ch, _box in glyphs]
        self.glyph_count = len(chars)
        self._chars = "".join(chars) if all(len(ch) == 1 for ch in chars) else chars
        self._boxes = array("d", (v for _ch, box in glyphs for v in box))
        out, src = [], array("I")
        pending = False  # a space owed before the next character
        space_glyph = 0
        for gi, ch in enumerate(chars):
            folded = _fold(ch)
            if folded == " ":
                # A break between MuPDF's lines joins a word hyphenated at
                # the line's end: "entangle-" + "ment" reads "entanglement".
                if ch == "\n" and len(out) >= 2 and out[-1] == "-" and out[-2].isalpha():
                    nxt = next((chars[j] for j in range(gi + 1, len(chars)) if not chars[j].isspace()), "")
                    if nxt and _fold(nxt)[:1].isalpha():
                        out.pop()
                        src.pop()
                        pending = False
                        continue
                if out and not pending:
                    pending = True
                    space_glyph = gi
                continue
            for c in folded:
                if pending:
                    out.append(" ")
                    src.append(space_glyph)
                    pending = False
                out.append(c)
                src.append(gi)
        self.text = "".join(out)
        self.glyph_at = src          # text index → glyph index
        spans = [m.span() for m in _WORD_RE.finditer(self.text)]
        self.word_starts = array("I", (s for s, _e in spans))
        self.word_ends = array("I", (e for _s, e in spans))
        self.word_texts = self.text.split()
        self._first_pos = None
        self._word_index = None

    def char(self, gi: int) -> str:
        return self._chars[gi]

    def box(self, gi: int) -> tuple:
        """Glyph ``gi``'s box, ``(left, bottom, right, top)`` in PDF user
        space; all zeros for a glyph without one (a line break)."""
        i = 4 * gi
        return tuple(self._boxes[i:i + 4])

    def pos_of_glyph(self, gi: int) -> int:
        """The text index of glyph ``gi``'s first character, or of the next
        glyph's that has one (a dropped hyphen, a break); ``len(text)``
        past the last."""
        if self._first_pos is None:
            end = len(self.text)
            first = array("I", [end]) * (self.glyph_count + 1)
            for pos in range(len(self.glyph_at) - 1, -1, -1):
                first[self.glyph_at[pos]] = pos
            for g in range(self.glyph_count - 1, -1, -1):
                if first[g] == end:
                    first[g] = first[g + 1]
            self._first_pos = first
        return self._first_pos[min(max(gi, 0), self.glyph_count)]

    def span_of_glyphs(self, first: int, last: int) -> tuple[int, int]:
        """The text span the glyphs ``first``..``last`` (inclusive) occupy."""
        return self.pos_of_glyph(first), self.pos_of_glyph(last + 1)

    def original(self, start: int, end: int) -> str:
        """The passage ``text[start:end]`` as the file spells it: the chars
        of the glyphs its characters were read from, in the file's case,
        whitespace and line breaks as one space. A word hyphenated at a
        line's end is one word, as the text reads it: its hyphen and the
        break after it are glyphs no character was read from."""
        end = min(end, len(self.glyph_at))
        if start >= end:
            return ""
        read = set(self.glyph_at[start:end])
        out = "".join(" " if ch.isspace() else ch
                      for ch in (self.char(gi) for gi in range(self.glyph_at[start], self.glyph_at[end - 1] + 1)
                                 if gi in read))
        return _SPACES_RE.sub(" ", out).strip()

    def word_index(self):
        """The page's words as the rough search looks them up, made on first
        use: ``(where, cores, present)``, each word without the punctuation
        at its edges (its core), the indices of the words with each core,
        and the set of the cores."""
        if self._word_index is None:
            cores = [w.strip(_PUNCT) for w in self.word_texts]
            where: dict = {}
            for j, core in enumerate(cores):
                where.setdefault(core, []).append(j)
            self._word_index = (where, cores, frozenset(cores))
        return self._word_index


def glyphs_under(page: PageText, quads) -> list[int]:
    """The indices of the page's glyphs whose centre lies in one of
    ``quads`` (``(x1, y1, x2, y2)`` boxes in the glyphs' space), in page
    order: pdf_text.covers, the rule of the annotation import's quotes."""
    return [gi for gi in range(page.glyph_count) if covers(quads, page.box(gi))]


# --- the search ----------------------------------------------------------------

def _similar(a: str, b: str) -> float:
    """How alike two folded texts read, 0 to 1: difflib's ratio, without its
    junk heuristic, so 1.0 only when they are equal; 0 when either is
    empty. A place's confidence (the passage against the text found there)
    and its context fit (the old file's surroundings against the new
    ones)."""
    return SequenceMatcher(None, a, b, autojunk=False).ratio() if a and b else 0.0


def _ends_alike(a: str, b: str) -> float:
    """How alike two words read by the characters they share at their two
    ends: twice their common start and, past it, their common end, over
    both lengths. For one change inside a word ("colour" for "color",
    "[27]," for "[25],") it is difflib's ratio, and a pure Python loop over
    a few characters, where difflib's takes twenty times as long."""
    n = min(len(a), len(b))
    start = 0
    while start < n and a[start] == b[start]:
        start += 1
    end = 0
    while end < n - start and a[-1 - end] == b[-1 - end]:
        end += 1
    return 2 * (start + end) / (len(a) + len(b))


# _word_cost is kept for the pairs of words it was last asked about: a search
# asks about the same few thousand words of a paper over and over, in every
# window of every page, for every highlight.

@lru_cache(maxsize=1 << 15)
def _word_cost(a: str, b: str) -> float:
    """What the rough alignment pays to read word ``a`` of the passage as
    word ``b`` of the page: 1 less how alike they are (``_ends_alike``, the
    digits of two words that both have some counting as half alike), so
    "[27]," for "[25]," costs 0.1 and "5" for "4" 0.5. A word less than
    half like the other costs ``DROP``, as dropping a word does."""
    if a == b:
        return 0.0
    like = _ends_alike(a, b)
    if _DIGIT_RE.search(a) and _DIGIT_RE.search(b):
        like = (like + _ends_alike(_DIGIT_RE.sub("0", a), _DIGIT_RE.sub("0", b))) / 2
    return 1.0 - like if like >= 0.5 else DROP


class _Words(NamedTuple):
    """A passage as the rough search aligns it: its ``words``, their
    ``cores`` (each without the punctuation at its edges, as
    ``PageText.word_index`` has the page's) and ``weight``, the letters of
    the cores."""
    words: list
    cores: list
    weight: int


def _words_of(text: str) -> _Words:
    words = text.split()
    cores = [w.strip(_PUNCT) for w in words]
    return _Words(words, cores, sum(map(len, cores)))


def _bound(words: int) -> float:
    """The cost past which the rough alignment of a passage of ``words``
    words gives up (``ROUGH_BOUND`` per word, at least one, a hair more as
    a dropped word's ``DROP`` is, so a word dropped where the bound is a
    whole number of words still fits): there for speed, generous enough
    that the confidence, not the bound, turns a place down."""
    return max(1.0, ROUGH_BOUND * words) * DROP


def _holds(words: _Words, present) -> bool:
    """Whether words whose cores are ``present`` (a set) are worth aligning
    the passage ``words`` against: its words found there unchanged up to
    their punctuation must hold ``ROUGH_PRESENT`` of its letters. A page or
    a window that does not is passed over, for speed: a place there reads,
    as a rule, far less like the passage than ``ROUGH_APPLY``."""
    return sum(len(c) for c in words.cores if c in present) >= ROUGH_PRESENT * words.weight


def _holds_split(words: _Words, tail, head) -> bool:
    """``_holds`` for a passage split by a page break: whether, for some cut,
    its words before the cut found among ``tail`` (the cores of the first
    page's last words) and its words after it among ``head`` (the next
    page's first) hold ``ROUGH_PRESENT`` of its letters."""
    cores = words.cores
    after = [0] * (len(cores) + 1)  # after[i]: the letters of cores[i:] found in head
    for i in range(len(cores) - 1, -1, -1):
        after[i] = after[i + 1] + (len(cores[i]) if cores[i] in head else 0)
    need, before = ROUGH_PRESENT * words.weight, 0
    for cut in range(1, len(cores)):
        before += len(cores[cut - 1]) if cores[cut - 1] in tail else 0
        if before + after[cut] >= need:
            return True
    return False


def _rows(needle: list[str], words: list[str], lo: int, hi: int):
    """The rough alignment of ``needle`` (a passage's words) against
    ``words[lo:hi]``, one row per needle word: ``(costs, starts)``, where
    ``costs[j]`` is the cheapest alignment of the needle's words so far
    against a run of the window ending before ``words[lo + j]`` (the run
    free to start anywhere) and ``starts[j]`` the word that run starts at.
    A word read as another costs ``_word_cost``, a page word added one, a
    passage word dropped ``DROP``, a hair more: a run that keeps the
    passage's first and last words beats one as cheap that stops short of
    them ("in the cryostat." against "in a new cryostat."). On a tie the
    later start wins, so a dropped first word never pulls in the word
    before it (a drop and a swap for an unlike word cost the same)."""
    m = hi - lo
    prev = [0.0] * (m + 1)
    start_prev = list(range(lo, hi + 1))
    for nw in needle:
        _breathe()
        cur = [prev[0] + DROP] + [INF] * m
        start_cur = [lo] * (m + 1)
        for j in range(1, m + 1):
            sub = prev[j - 1] + _word_cost(nw, words[lo + j - 1])
            dele = prev[j] + DROP
            ins = cur[j - 1] + 1.0
            best = min(sub, dele, ins)
            cur[j] = best
            start_cur[j] = start_prev[j] if best == dele else start_prev[j - 1] if best == sub else start_cur[j - 1]
        yield cur, start_cur
        prev, start_prev = cur, start_cur


def _align(needle: list[str], words: list[str], lo: int, hi: int, bound: float = INF):
    """The cheapest alignment of every word of ``needle`` against a run of
    ``words[lo:hi]`` (``_rows``; the run free to start and end anywhere in
    the window, the earliest end on a tie, so a dropped last word never
    pulls in the word after it): ``(cost, start, end)`` in word
    indices. Once a row costs more than ``bound`` everywhere, no later row
    costs less, and the answer is ``(INF, lo, lo)`` at once."""
    costs, starts = [0.0] * (hi - lo + 1), list(range(lo, hi + 1))
    for costs, starts in _rows(needle, words, lo, hi):
        if min(costs) > bound:
            return INF, lo, lo
    j = min(range(len(costs)), key=costs.__getitem__)
    return costs[j], starts[j], lo + j


def _seed_windows(page: PageText, words: _Words, bound: float) -> list[tuple[int, int]]:
    """Where on ``page`` the rough search aligns a passage (its ``_Words``):
    the page's words with the core of one of the passage's vote for where
    the passage would start, and around each of the ``MAX_SEEDS``
    best-voted starts (none within the slack of a better one) the passage's
    length and the slack either side, the slack being ``bound``:
    ``[(lo, hi)]`` in word indices."""
    where = page.word_index()[0]
    votes: dict = {}
    for i, core in enumerate(words.cores):
        if core:
            for j in where.get(core, ()):
                votes[j - i] = votes.get(j - i, 0) + 1
    slack = int(bound) + 1
    seeds = []
    for start, _count in sorted(votes.items(), key=lambda kv: (-kv[1], kv[0])):
        if any(abs(start - s) <= slack for s in seeds):
            continue
        seeds.append(start)
        if len(seeds) >= MAX_SEEDS:
            break
    windows = []
    for start in seeds:
        lo, hi = max(0, start - slack), min(len(page.word_texts), start + len(words.words) + slack)
        if hi > lo:
            windows.append((lo, hi))
    return windows


def _rough_on_page(page: PageText, words: _Words) -> list[tuple[float, int, int]]:
    """The places on ``page`` the rough search finds for a passage (its
    ``_Words``): ``[(cost, start_char, end_char)]``, the cheapest run of
    each seed window (``_seed_windows``) within the bound (``_bound``). A
    page, or a window, that does not hold enough of the passage's words
    (``_holds``) is passed over without aligning."""
    if not page.word_texts:
        return []
    _where, cores, present = page.word_index()
    if not _holds(words, present):
        return []
    bound = _bound(len(words.words))
    out = []
    for lo, hi in _seed_windows(page, words, bound):
        if not _holds(words, set(cores[lo:hi])):
            continue
        cost, a, b = _align(words.words, page.word_texts, lo, hi, bound)
        if cost <= bound and b > a:
            out.append((cost, *_trimmed(page, page.word_starts[a], page.word_ends[b - 1], words.words)))
    return out


def _row_bests(needle: list[str], words: list[str], lo: int, hi: int, bound: float) -> list[tuple]:
    """For each ``i``, the cheapest alignment of ``needle[:i]`` against a run
    of ``words[lo:hi]`` that starts and ends anywhere: ``(cost, start,
    end)`` in word indices, ``i`` from 0 (nothing to align) to the
    needle's length; INF once past ``bound``."""
    out = [(0.0, lo, lo)]
    for costs, starts in _rows(needle, words, lo, hi):
        j = min(range(len(costs)), key=costs.__getitem__)
        out.append((costs[j], starts[j], lo + j))
        if costs[j] > bound:
            break
    return out + [(INF, lo, lo)] * (len(needle) + 1 - len(out))


def _across(first: PageText, second: PageText, words: _Words):
    """The passage (its ``_Words``) split by the break between two pages
    that follow each other: ``(on_first, head, tail)``, whether its larger
    part is on the first page (the first on a tie) and the ``(start,
    end)`` of each part on its page, or None.

    The two parts are searched apart rather than as one string of the
    pages' joined text: what stands between them (the first page's footer
    and footnotes, the next one's running head, "Phys. Rev. A 104, 033101
    (2021)") would be words to drop, and here it costs nothing. The head
    of the passage is aligned against the last words of the first page,
    its tail against the first words of the second (reversed, so that it
    may start anywhere there), and the cut between them is the one that
    costs least in all, within the bound of the whole passage; each part
    must cover some words of its page."""
    n = len(words.words)
    bound = _bound(n)
    reach = n + JOIN_SLACK
    lo = max(0, len(first.word_texts) - reach)
    head = second.word_texts[:reach]
    if n < 2 or lo >= len(first.word_texts) or not head:
        return None
    if not _holds_split(words, set(first.word_index()[1][lo:]), set(second.word_index()[1][:reach])):
        return None
    pre = _row_bests(words.words, first.word_texts, lo, len(first.word_texts), bound)
    rev = head[::-1]
    suf = _row_bests(words.words[::-1], rev, 0, len(rev), bound)  # suf[k]: the passage's last k words
    cut = min(range(1, n), key=lambda i: pre[i][0] + suf[n - i][0])
    if pre[cut][0] + suf[n - cut][0] > bound:
        return None
    _c, a1, b1 = pre[cut]
    _c, ra, rb = suf[n - cut]
    a2, b2 = len(rev) - rb, len(rev) - ra
    if b1 <= a1 or b2 <= a2:
        return None
    return (cut >= n - cut,
            _trimmed(first, first.word_starts[a1], first.word_ends[b1 - 1], words.words[:cut]),
            _trimmed(second, second.word_starts[a2], second.word_ends[b2 - 1], words.words[cut:]))


def _trimmed(page: PageText, s: int, e: int, needle_words: list[str]) -> tuple[int, int]:
    """The match ``text[s:e]`` without the punctuation at its edges that the
    needle's first and last words do not end in ("cooled" matched
    "cooled.": the period stays outside)."""
    while e > s and page.text[e - 1] in _PUNCT and not needle_words[-1].endswith(page.text[e - 1]):
        e -= 1
    while s < e and page.text[s] in _PUNCT and not needle_words[0].startswith(page.text[s]):
        s += 1
    return s, e


class Match(NamedTuple):
    """Where ``find_passage`` found a passage: ``text[start:end]`` of page
    ``page``, ``confidence`` saying how alike the passage and the text
    found there read (``_similar``, 0 to 1; 1.0 for an exact copy).
    ``part``: only the larger part of the passage, the rest being across a
    page break. ``spans``: for a passage found piece by piece
    (``_by_pieces``), the ``(start, end)`` of each piece on ``page``, in
    order, the text between them not the passage's; empty when
    ``start``..``end`` is all of it."""
    page: int
    start: int
    end: int
    confidence: float
    part: bool = False
    spans: tuple = ()


class Piece(NamedTuple):
    """A part of a passage that the old file read without a word between
    (``_pieces``): its ``text``; the old file's text either side of it
    (``context``, which says whether it began or ended inside a word); and
    how many words the old file read between the piece before and this
    one (``skipped``: a caption between two columns, a footnote)."""
    text: str
    context: tuple
    skipped: int = 0


def _edges(needle: str, context) -> tuple[bool, bool]:
    """Whether an exact copy of ``needle`` must start and end on a word's
    edge: yes, unless its ``context`` (``(before, after)`` in the old file)
    shows the old passage itself began or ended inside a word."""
    before, after = context or ("", "")
    return (needle[0].isalnum() and not (before and before[-1].isalnum()),
            needle[-1].isalnum() and not (after and after[0].isalnum()))


def _exact(text: str, needle: str, edges: tuple[bool, bool], lo: int = 0, hi: int | None = None):
    """The starts of ``needle``'s copies in ``text`` that start from ``lo``
    to ``hi`` and lie on word edges where ``edges`` (``_edges``) asks:
    "ion" in "motion" or "fig. 1" in "fig. 12" is not the passage."""
    need_start, need_end = edges
    hi = len(text) if hi is None else hi
    at = text.find(needle, lo)
    while 0 <= at <= hi:
        end = at + len(needle)
        if not ((need_start and at > 0 and text[at - 1].isalnum())
                or (need_end and end < len(text) and text[end].isalnum())):
            yield at
        at = text.find(needle, at + 1)


def _piece_hits(page: PageText, piece: Piece, wlo: int, whi: int, exact_chars: int) -> list[tuple]:
    """Where ``piece`` lies among the words ``wlo`` to ``whi`` (exclusive) of
    ``page``: ``[(cost, start, end)]``, its exact copies there on word
    edges, else, for a piece of ``exact_chars`` (``find_passage``'s) or
    more, the cheapest run there within the rough alignment's bound. A
    shorter piece is found exactly or not at all, as a shorter passage
    is."""
    wlo, whi = max(0, wlo), min(len(page.word_texts), whi)
    if whi <= wlo:
        return []
    text = piece.text
    lo, hi = page.word_starts[wlo], page.word_ends[whi - 1]
    hits = [(0.0, at, at + len(text)) for at in _exact(page.text, text, _edges(text, piece.context), lo, hi - len(text))]
    if hits or len(text) < exact_chars:
        return hits
    words = text.split()
    bound = _bound(len(words))
    cost, a, b = _align(words, page.word_texts, wlo, whi, bound)
    if cost > bound or b <= a:
        return []
    return [(cost, *_trimmed(page, page.word_starts[a], page.word_ends[b - 1], words))]


def _beside(pages: dict, page_no: int, at: int, piece: Piece, skipped: int, forward: bool, exact_chars: int):
    """Where ``piece`` lies next to a part of the passage found on page
    ``page_no``: after it (``forward``, ``at`` being that part's end) or
    before it (``at`` its start), at most ``skipped + JOIN_SLACK`` words
    away, ``skipped`` being the words the old file read in between. On
    the same page, or, from a part within that many words of its page's
    end (its start, backward), within as many of the next page's first
    words (the previous page's last), the footer and running head between
    costing nothing. ``(page, start, end)``: the hit the rough alignment
    found cheapest (an exact copy first), then one on the same page, then
    the one with a gap nearest ``skipped``; None when there is none."""
    reach = skipped + JOIN_SLACK
    n = len(piece.text.split())
    pad = n + int(_bound(n)) + 1  # the words a hit within reach may run on for
    page = pages[page_no]
    w = bisect_left(page.word_starts, at)  # the page's words before ``at``
    found = []
    if forward:
        for cost, s, e in _piece_hits(page, piece, w, w + reach + pad, exact_chars):
            gap = bisect_left(page.word_starts, s) - w
            if gap <= reach:
                found.append((cost, False, abs(gap - skipped), page_no, s, e))
        rest, nxt = len(page.word_texts) - w, pages.get(page_no + 1)
        if nxt is not None and rest <= reach:
            for cost, s, e in _piece_hits(nxt, piece, 0, reach + pad, exact_chars):
                gap = bisect_left(nxt.word_starts, s)
                if gap <= reach:
                    found.append((cost, True, abs(rest + gap - skipped), page_no + 1, s, e))
    else:
        for cost, s, e in _piece_hits(page, piece, w - reach - pad, w, exact_chars):
            gap = w - bisect_left(page.word_starts, e)
            if gap <= reach:
                found.append((cost, False, abs(gap - skipped), page_no, s, e))
        prev = pages.get(page_no - 1)
        if prev is not None and w <= reach:
            count = len(prev.word_texts)
            for cost, s, e in _piece_hits(prev, piece, count - reach - pad, count, exact_chars):
                gap = count - bisect_left(prev.word_starts, e)
                if gap <= reach:
                    found.append((cost, True, abs(w + gap - skipped), page_no - 1, s, e))
    if not found:
        return None
    _cost, _crossed, _off, hit_page, s, e = min(found)
    return hit_page, s, e


def _by_pieces(pages: dict, pieces: list[Piece], tick=None, *, exact_chars: int = ROUGH_MIN_CHARS) -> list[Match]:
    """A passage the old file read in several pieces (``Piece``), found
    piece by piece: its longest piece (the first of the longest) wherever
    it is, exactly, else roughly (``_rough_on_page``) when no exact copy
    leads anywhere; from there the pieces after it, each beginning shortly
    after the one before, and the pieces before it, each ending shortly
    before the next (``_beside``). A piece under ``exact_chars`` is found
    exactly only. A chain may cross one page break; it is
    then a ``part`` on the page holding more of the passage's words (the
    first on a tie), covering the pieces there. ``[Match]``, each with its
    pieces' ``spans`` and, as its confidence, how alike the old pieces and
    the ones found read, each joined with a space; ``tick()`` runs once
    per page the rough search for the longest piece covers."""
    lengths = [len(p.text.split()) for p in pieces]
    k = lengths.index(max(lengths))
    anchor = pieces[k]
    whole = " ".join(p.text for p in pieces)

    def chain(page_no: int, s: int, e: int):
        _breathe()
        where = {k: (page_no, s, e)}  # piece → (page, start, end)
        for i in range(k + 1, len(pieces)):
            where[i] = _beside(pages, where[i - 1][0], where[i - 1][2], pieces[i], pieces[i].skipped, True, exact_chars)
            if where[i] is None:
                return None
        for i in range(k - 1, -1, -1):
            where[i] = _beside(pages, where[i + 1][0], where[i + 1][1], pieces[i], pieces[i + 1].skipped, False,
                               exact_chars)
            if where[i] is None:
                return None
        placed = [where[i] for i in range(len(pieces))]
        on = sorted({p for p, _s, _e in placed})
        if len(on) > 2:
            return None
        confidence = _similar(whole, " ".join(pages[p].text[s:e] for p, s, e in placed))
        words = {p: sum(lengths[i] for i, place in enumerate(placed) if place[0] == p) for p in on}
        best = max(on, key=lambda p: (words[p], -p))
        spans = tuple((s, e) for p, s, e in placed if p == best)
        return Match(best, spans[0][0], spans[-1][1], confidence, part=len(on) > 1, spans=spans)

    edges = _edges(anchor.text, anchor.context)
    found, tried = [], set()
    for page_no, page in pages.items():
        _breathe()
        for at in _exact(page.text, anchor.text, edges):
            tried.add((page_no, at, at + len(anchor.text)))
            found.append(chain(page_no, at, at + len(anchor.text)))
    if any(found) or len(anchor.text) < exact_chars:
        return [m for m in found if m]
    words = _words_of(anchor.text)
    for page_no, page in pages.items():
        if tick:
            tick()
        _breathe()
        found.extend(chain(page_no, s, e) for _cost, s, e in _rough_on_page(page, words) if (page_no, s, e) not in tried)
    return [m for m in found if m]


def find_passage(pages: dict, needle: str, *, hint_page: int = 1, context: tuple | None = None,
                 near=None, tick=None, pieces=None, min_match: float | None = None,
                 exact_chars: int | None = None) -> Match | None:
    """Where ``needle`` (a normalized passage) is in ``pages`` (page number →
    ``PageText``): a ``Match``, or None. ``min_match`` and ``exact_chars``
    are the run's thresholds (``thresholds``), ``ROUGH_APPLY`` and
    ``ROUGH_MIN_CHARS`` when not given.

    The candidates. Every exact copy on word edges, of confidence 1.0: a
    hit inside a word ("ion" in "motion", "fig. 1" in "fig. 12") is not the
    passage, unless the old passage itself began or ended inside a word, as
    its ``context`` (``(before, after)``, the old file's text on either
    side) shows. From ``exact_chars`` characters on, the places the rough
    search finds on every page (``_rough_on_page``), when there is no exact copy,
    or there is a context and no exact copy's surroundings read like it
    (``EXACT_SURE``), so a passage reworded in place may beat a verbatim
    copy elsewhere. When there is no exact copy, also the passage split by
    each page break (``_across``), as a ``part``, and, when the old file
    read it in several ``pieces`` (a column's foot and the next column's
    top, a caption between them), the passage found piece by piece
    (``_by_pieces``). A place's confidence is how alike the needle and the
    text found there read (``_similar``): over both parts across a page
    break, over the pieces joined for a passage found piece by piece.

    The ranking. A candidate's score is its confidence, plus
    ``CONTEXT_WEIGHT`` times its context fit when there is a context (the
    mean likeness of the sides the context has to the text beside the
    candidate); on a tie, the page nearest ``hint_page`` wins, then the
    place nearest the old one by ``near(page, start, end)``, a distance the
    caller measures (``place_all`` measures it between the candidate's
    glyphs and the highlight's rectangles, each as fractions of its own
    page).

    The best candidate is the answer when its confidence reaches
    ``min_match`` (1.0 for a needle shorter than ``exact_chars``), else
    there is none. ``tick()`` runs once per page the rough, the
    page-break and the piece-by-piece searches cover, so a stop lands
    within a page."""
    min_match = ROUGH_APPLY if min_match is None else min_match
    exact_chars = ROUGH_MIN_CHARS if exact_chars is None else exact_chars
    needle = (needle or "").strip()
    if len(needle) < MIN_NEEDLE:
        return None
    before, after = context or ("", "")
    has_context = bool(before or after)
    edges = _edges(needle, context)
    found = []
    for page_no, page in pages.items():
        _breathe()
        found.extend(Match(page_no, at, at + len(needle), 1.0) for at in _exact(page.text, needle, edges))
    fits: dict = {}

    def fit(m: Match) -> float:
        if m not in fits:
            text = pages[m.page].text
            sides = []
            if before:
                sides.append(_similar(before, text[max(0, m.start - CONTEXT_CHARS):m.start]))
            if after:
                sides.append(_similar(after, text[m.end:m.end + CONTEXT_CHARS]))
            fits[m] = sum(sides) / len(sides) if sides else 0.0
        return fits[m]

    rough = len(needle) >= exact_chars
    exact = {(m.page, m.start, m.end) for m in found}
    words = _words_of(needle)
    if rough and (not exact or (has_context and all(fit(m) < EXACT_SURE for m in found))):
        for page_no, page in pages.items():
            if tick:
                tick()
            _breathe()
            found.extend(Match(page_no, s, e, _similar(needle, page.text[s:e]))
                         for _cost, s, e in _rough_on_page(page, words) if (page_no, s, e) not in exact)
    if rough and not exact:
        for page_no in pages:
            if page_no + 1 not in pages:
                continue
            if tick:
                tick()
            _breathe()
            split = _across(pages[page_no], pages[page_no + 1], words)
            if split:
                on_first, (s1, e1), (s2, e2) = split
                confidence = _similar(needle, f"{pages[page_no].text[s1:e1]} {pages[page_no + 1].text[s2:e2]}")
                found.append(Match(page_no, s1, e1, confidence, part=True) if on_first
                             else Match(page_no + 1, s2, e2, confidence, part=True))
    if not exact and pieces and len(pieces) > 1:
        found.extend(_by_pieces(pages, pieces, tick, exact_chars=exact_chars))
    if not found:
        return None

    def rank(m: Match):
        score = m.confidence + CONTEXT_WEIGHT * fit(m) if has_context else m.confidence
        return -score, abs(m.page - hint_page), near(m.page, m.start, m.end) if near else 0.0

    best = min(found, key=rank)
    return best if best.confidence >= (min_match if rough else 1.0) else None


# --- rectangles and frames -------------------------------------------------------

def _union(a, b):
    return (min(a[0], b[0]), min(a[1], b[1]), max(a[2], b[2]), max(a[3], b[3]))


def _bounds(boxes) -> tuple:
    """The box around ``boxes`` (at least one)."""
    out = boxes[0]
    for box in boxes[1:]:
        out = _union(out, box)
    return out


def rects_for_span(page: PageText, start: int, end: int) -> list[tuple]:
    """The boxes of the glyphs ``text[start:end]`` spans, one per line (in
    the glyphs' space): MuPDF's lines end at its breaks, and two of its
    lines that share a baseline (a span of math inside a sentence) merge
    into one box."""
    if start >= end or not page.glyph_at:
        return []
    g0, g1 = page.glyph_at[start], page.glyph_at[min(end, len(page.glyph_at)) - 1]
    lines, cur = [], None
    for gi in range(g0, g1 + 1):
        box = page.box(gi)
        if not has_box(box):
            if page.char(gi) == "\n":
                cur = None
            continue
        if cur is None:
            cur = box
            lines.append(cur)
        else:
            cur = _union(cur, box)
            lines[-1] = cur
    merged = []
    for box in lines:
        if merged:
            last = merged[-1]
            overlap = min(last[3], box[3]) - max(last[1], box[1])
            if overlap > 0.5 * min(last[3] - last[1], box[3] - box[1]):
                merged[-1] = _union(last, box)
                continue
        merged.append(box)
    return merged


def to_viewer_rect(box, frame) -> tuple:
    """A box in the glyphs' space → ``(x1, y1, x2, y2)`` in the page's
    display frame (``frame``: the view box and rotation; the frame's size
    is ``display_size`` of them), top-left origin."""
    view, rotation = frame
    w, h = display_size(view, rotation)
    x0, y0, x1, y1 = box
    pts = [pdf_point_to_viewer(x, y, rotation, view) for x, y in ((x0, y0), (x1, y1), (x0, y1), (x1, y0))]
    us, vs = [u * w for u, _ in pts], [v * h for _, v in pts]
    return (round(min(us), 2), round(min(vs), 2), round(max(us), 2), round(max(vs), 2))


def _position(page_no: int, frame, boxes, area: bool = False) -> dict:
    view, rotation = frame
    w, h = display_size(view, rotation)
    return shape.position(page_no, round(w, 2), round(h, 2), [to_viewer_rect(b, frame) for b in boxes], area=area)


def _norm_rect(rect: dict, size) -> tuple:
    """A stored rect as fractions of its page."""
    w, h = (float(v or 0) or 1.0 for v in size)
    return (rect["x1"] / w, rect["y1"] / h, rect["x2"] / w, rect["y2"] / h)


def _size(pos: dict):
    return (pos.get("width") or 1.0, pos.get("height") or 1.0)


def _same_place(old: dict, new: dict) -> bool:
    if old.get("pageNumber") != new.get("pageNumber") or old.get("lost"):
        return False
    a, b = _norm_rect(old["boundingRect"], _size(old)), _norm_rect(new["boundingRect"], _size(new))
    return all(abs(x - y) <= SAME_PLACE for x, y in zip(a, b))


def _clamp01(v: float) -> float:
    return min(1.0, max(0.0, v))


def _box_by_offsets(page_no: int, old_box, old_anchor, new_anchor, new_frame) -> dict:
    """An area highlight's position on ``page_no``: the old box (fractions
    of its page) placed around ``new_anchor`` (fractions of the new page)
    as it sat around ``old_anchor`` — the anchor being the text or picture
    inside the area that was found again, or the box itself shifted —
    clamped to the page, and the anchor alone (clamped too) when nothing
    of the box is left on it."""
    dx1, dy1 = old_box[0] - old_anchor[0], old_box[1] - old_anchor[1]
    dx2, dy2 = old_box[2] - old_anchor[2], old_box[3] - old_anchor[3]
    view, rotation = new_frame
    w, h = display_size(view, rotation)
    x1, y1 = _clamp01(new_anchor[0] + dx1), _clamp01(new_anchor[1] + dy1)
    x2, y2 = _clamp01(new_anchor[2] + dx2), _clamp01(new_anchor[3] + dy2)
    if x2 - x1 < 0.002 or y2 - y1 < 0.002:
        x1, y1, x2, y2 = (_clamp01(v) for v in new_anchor)
    rect = (round(x1 * w, 2), round(y1 * h, 2), round(x2 * w, 2), round(y2 * h, 2))
    return shape.position(page_no, round(w, 2), round(h, 2), [rect], area=True)


def _norm_box(box, frame) -> tuple:
    """A box in the glyphs' space as fractions of its page's display frame."""
    view, rotation = frame
    w, h = display_size(view, rotation)
    x1, y1, x2, y2 = to_viewer_rect(box, frame)
    return (x1 / w, y1 / h, x2 / w, y2 / h)


# --- the files ---------------------------------------------------------------------

class Doc:
    """One file's pages, read as they are asked for: the text of each, its
    frame, its pictures. ``src`` is a path or PDF bytes, as ``GlyphPages``
    takes; a file that can't be opened has no pages, and the pages past
    pdf_text's ``MAX_PAGES`` are not read, so a highlight there is a
    passage not found. A page's glyphs are kept only as its ``PageText``.
    A context manager: closing it lets go of the file and of what was
    read from it."""

    def __init__(self, src):
        self.glyphs = GlyphPages(src)
        pages = self.glyphs.page_count()
        self.count = min(pages, MAX_PAGES)
        if pages > self.count:
            log.info(f"[relocate] a file of {pages} pages: only the first {self.count} are read")
        self._texts: dict = {}
        self._frames: dict = {}
        self._images: dict = {}

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def close(self):
        self.glyphs.close()
        self._texts.clear()
        self._frames.clear()
        self._images.clear()

    def text(self, page_no: int) -> PageText:
        if page_no not in self._texts:
            _breathe()
            self._texts[page_no] = PageText(self.glyphs.page(page_no, cache=False))
        return self._texts[page_no]

    def frame(self, page_no: int):
        if page_no not in self._frames:
            self._frames[page_no] = self.glyphs.frame(page_no)
        return self._frames[page_no]

    def images(self, page_no: int, tick=None) -> list:
        """The pictures of page ``page_no``, read on first use; ``tick()``
        runs before such a read, so a stop can land between two pages."""
        if page_no not in self._images:
            if tick:
                tick()
            self._images[page_no] = self.glyphs.images(page_no)
        return self._images[page_no]

    def all_texts(self, progress=None) -> dict:
        for n in range(1, self.count + 1):
            if progress:
                progress(done=n - 1, total=self.count, unit="pages", phase="reading")
            self.text(n)
        return dict(self._texts)


def page_highlights(conn, page_id: str) -> list[tuple[str, dict]]:
    """``(id, properties)`` of the page's blocks placed on a PDF page by
    rectangles: highlights and link regions (``highlights.is_highlight``),
    whatever their depth in the notes."""
    rows = conn.execute(
        "WITH RECURSIVE sub(id) AS (SELECT id FROM unified_blocks WHERE parent_id = ? "
        "UNION ALL SELECT b.id FROM unified_blocks b JOIN sub ON b.parent_id = sub.id) "
        "SELECT b.id, b.properties FROM unified_blocks b JOIN sub ON b.id = sub.id "
        "WHERE json_extract(b.properties, '$.pdf_position.boundingRect') IS NOT NULL ORDER BY b.id",
        (page_id,)).fetchall()
    out = []
    for bid, raw in rows:
        try:
            props = load_json(raw or "{}") or {}
        except ValueError:
            continue
        pos = props.get("pdf_position")
        if shape.is_highlight(props) and isinstance(pos, dict) and pos.get("rects") and shape.page_of(props):
            out.append((bid, props))
    return out


def _quads(pos: dict, frame) -> list:
    """A position's rectangles as boxes in the glyphs' space of a page of
    ``frame`` (pdf_export's mapping)."""
    view, rotation = frame
    return [viewer_rect_to_pdf(r, _size(pos), rotation, view) for r in pos["rects"] if shape.placed(r)]


def _trim(text: str, a: int, b: int) -> tuple[int, int]:
    """``text[a:b]``'s edges moved in past spaces."""
    while a < b and text[a] == " ":
        a += 1
    while b > a and text[b - 1] == " ":
        b -= 1
    return a, b


def _stretches(page: PageText, covered: list[int]) -> list[tuple[int, int, int, int]]:
    """The glyphs ``covered`` (``glyphs_under``) as stretches of the page's
    text, ``[(start, end, first glyph, last glyph)]``, each stretch's edges
    moved in past spaces. Covered glyphs make one stretch while nothing
    but marks without a box lies between them, and two stretches the text
    already joins (a word hyphenated at a line's end, a line that ends in
    a covered space) are one."""
    runs: list = []
    for gi in covered:
        if runs and all(page.char(g) != "\n" and not has_box(page.box(g)) for g in range(runs[-1][1] + 1, gi)):
            runs[-1][1] = gi
        else:
            runs.append([gi, gi])
    raw: list = []
    for first, last in runs:
        a, b = page.pos_of_glyph(first), page.pos_of_glyph(last + 1)
        if raw and raw[-1][1] == a:
            raw[-1] = (raw[-1][0], b, raw[-1][2], last)
        else:
            raw.append((a, b, first, last))
    out = []
    for a, b, first, last in raw:
        a, b = _trim(page.text, a, b)
        if a < b:
            out.append((a, b, first, last))
    return out


def _joined(page: PageText, stretches: list, spell: bool = False) -> str:
    """The text of ``stretches`` (``(start, end, first glyph, last glyph)``)
    in a row, one space between two, as a selection reads: a line's end
    and the next line's start, or a column's end and the next column's
    top past the caption the file draws between them. A word hyphenated
    where the highlight skips text and a line break ("entangle-" ...
    "ment") is joined whole, PageText's rule across the text in between,
    whether or not the page's text kept the hyphen. Folded, or with
    ``spell`` as the file spells it (``PageText.original``)."""
    def piece(a: int, b: int) -> str:
        return page.original(a, b) if spell else page.text[a:b]

    out = piece(*stretches[0][:2])
    for (_a, _b, _first, last), (a, b, first, _last) in zip(stretches, stretches[1:]):
        part = piece(a, b)
        hyphen = last
        while hyphen > 0 and page.char(hyphen).isspace():
            hyphen -= 1
        head = out[:-1] if _fold(out[-1:]) == "-" else out
        if (_fold(page.char(hyphen)) == "-" and head[-1:].isalpha() and part[:1].isalpha()
                and any(page.char(g) == "\n" for g in range(last + 1, first))):
            out = head + part
        else:
            out += " " + part
    return out


def _spelled(page: PageText, spans) -> str:
    """The text of a match's ``spans`` (``(start, end)`` in order) as the
    file spells it, joined as ``_joined`` joins a highlight's stretches."""
    return _joined(page, [(s, e, page.glyph_at[s], page.glyph_at[e - 1]) for s, e in spans], spell=True)


def _pieces(page: PageText, stretches: list) -> list[Piece]:
    """A text highlight's ``stretches`` as the pieces the old file read
    without a word between (``Piece``): the stretches a line's end alone
    parts are one piece, and a new piece begins where the file read words
    the highlight does not cover (a caption drawn between two columns, a
    footnote, a mark the rectangles miss). Each with its text as a
    selection reads it (``_joined``) and the ``CONTEXT_CHARS`` either
    side."""
    groups, skipped = [[stretches[0]]], [0]
    for prev, cur in zip(stretches, stretches[1:]):
        words = len(page.text[prev[1]:cur[0]].split())
        if words:
            groups.append([cur])
            skipped.append(words)
        else:
            groups[-1].append(cur)
    out = []
    for group, words in zip(groups, skipped):
        a, b = group[0][0], group[-1][1]
        out.append(Piece(_joined(page, group), (page.text[max(0, a - CONTEXT_CHARS):a], page.text[b:b + CONTEXT_CHARS]),
                         words))
    return out


def _old_passage(src, pos: dict):
    """What ``src`` (the ``Doc`` the highlight's rectangles were measured
    on, or None) reads under a highlight: ``(needle, (before, after),
    anchor, pieces)``, or None when there is no such file or no text under
    the rectangles.

    A text highlight's needle is the text of the glyphs it covers alone,
    their stretches joined as a selection reads (``_joined``): a margin
    note, a caption or a column's foot between two of its lines is not in
    it. The context is the ``CONTEXT_CHARS`` before the first stretch and
    after the last. Its pieces (``_pieces``) are the parts the file read
    without a word between, for the search to find one by one when the
    needle is nowhere whole: an unchanged two-column page still reads the
    caption between them.

    An area's needle is the text from its first covered glyph to its last,
    and its anchor that text's boxes (``rects_for_span``, as fractions of
    the page), measured as ``place_all`` measures the match in the new
    file, so an unchanged area is found where it was. The covered glyphs
    alone would not do: an area over a table's column, which the file
    draws row by row, covers glyphs that never follow each other in the
    text, not even in an identical file. Only when that span runs through
    much text the area does not cover (glyphs drawn far out of reading
    order) are the covered glyphs the needle, their box the anchor. A text
    highlight has no anchor, an area no pieces."""
    page_no = pos["pageNumber"]
    if src is None or not 1 <= page_no <= src.count:
        return None
    page = src.text(page_no)
    frame = src.frame(page_no)
    covered = glyphs_under(page, _quads(pos, frame))
    stretches = _stretches(page, covered) if covered else []
    if not stretches:
        return None
    s, e = stretches[0][0], stretches[-1][1]
    context = (page.text[max(0, s - CONTEXT_CHARS):s], page.text[e:e + CONTEXT_CHARS])
    if not pos.get("area"):
        return _joined(page, stretches), context, None, _pieces(page, stretches)
    if e - s > 2 * len(covered) + 10:
        return _joined(page, stretches), context, _norm_box(_bounds([page.box(gi) for gi in covered]), frame), ()
    return page.text[s:e], context, _norm_box(_bounds(rects_for_span(page, s, e)), frame), ()


def _image_under(src, pos: dict):
    """The picture an area highlight covers in ``src`` (the ``Doc`` its
    rectangles were measured on, or None): ``(key, box)``, the image's
    identity and its place as fractions of the page; None without one.
    The picture covered most (by the share of it inside the highlight, or
    of the highlight inside it) counts, from half on."""
    page_no = pos["pageNumber"]
    if src is None or not 1 <= page_no <= src.count:
        return None
    frame = src.frame(page_no)
    quads = _quads(pos, frame)
    if not quads:
        return None
    hx1, hy1 = min(q[0] for q in quads), min(q[1] for q in quads)
    hx2, hy2 = max(q[2] for q in quads), max(q[3] for q in quads)
    best, best_score = None, 0.0
    for key, box in src.images(page_no):
        ix = max(0.0, min(hx2, box[2]) - max(hx1, box[0]))
        iy = max(0.0, min(hy2, box[3]) - max(hy1, box[1]))
        inter = ix * iy
        if inter <= 0:
            continue
        area_img = (box[2] - box[0]) * (box[3] - box[1])
        area_hl = (hx2 - hx1) * (hy2 - hy1)
        score = max(inter / area_img if area_img else 0.0, inter / area_hl if area_hl else 0.0)
        if score > best_score and score >= 0.5:
            best, best_score = (key, _norm_box(box, frame)), score
    return best


def _find_image(new, key, hint_page: int, tick=None):
    """Where the new file draws the picture ``key``: ``(page, box)`` with
    the box as fractions of the page, on the page nearest ``hint_page``
    when it is drawn on several; None when it is gone. Every page is
    looked at, nearest first, ``tick()`` before each page's first read."""
    for page_no in sorted(range(1, new.count + 1), key=lambda n: (abs(n - hint_page), n)):
        _breathe()
        for k, box in new.images(page_no, tick):
            if k == key:
                return page_no, _norm_box(box, new.frame(page_no))
    return None


def _center(norm_box) -> tuple:
    return ((norm_box[0] + norm_box[2]) / 2, (norm_box[1] + norm_box[3]) / 2)


def _agrees(needle: str, quote: str) -> bool:
    """Whether the text read under a highlight's rectangles (``needle``) is
    its stored ``quote``, both folded: they read alike from
    ``QUOTE_AGREES`` on. One that holds the other and is at least
    ``QUOTE_AGREES`` of its length always does, and is told at once; a
    shorter one does not count by holding: a rectangle now over a short
    line that happens to be a word of the quote is not the quote. When
    they do not agree, the rectangles were not measured on the file they
    were read from."""
    short, long = sorted((needle, quote), key=len)
    if short in long and len(short) >= QUOTE_AGREES * len(long):
        return True
    return _similar(needle, quote) >= QUOTE_AGREES


def place_all(new, items: list[tuple[str, dict]], progress=None, *, read_from=None, new_doc: str = "",
              min_match: float | None = None, exact_chars: int | None = None) -> dict:
    """Each highlight's place in ``new`` (a ``Doc``, the page's PDF now, the
    stored file ``new_doc``): ``{"ops": {id: patch}, "kinds": {id: kind},
    "highlights", "moved", "kept", "approx", "lost"}``. A patch is the
    properties to write on a highlight: its ``pdf_position``, and its
    ``quote`` as the new file spells it when the passage was found roughly
    or the highlight now covers only the part of it on one side of a page
    break. ``kinds`` says which of the four counts each highlight went to,
    by how the run leaves it: ``moved`` (solid, in a new place), ``kept``
    (solid, where it was), ``approx`` (dashed: a rough match, or an area
    placed by estimate) or ``lost`` (not found, hollow). ``min_match`` and
    ``exact_chars`` are ``find_passage``'s thresholds.

    A placement by an exact match (``find_passage``'s confidence 1.0) is
    solid: its position carries no ``approx``, ``match`` or ``prev``, and
    stale ones from an earlier run are dropped. One by a rough match is
    dashed, ``approx`` with ``match``, the confidence rounded to two
    decimals (at most 0.99: 1.0 is an exact match's), also when it lands
    where the highlight was (a sentence reworded in place). An area takes
    the rule of the match of the text inside it, is solid when its picture
    placed it, and is ``approx`` without ``match`` when an estimate did.
    Every placement drawn dashed also carries ``prev``
    (``highlights.previous_place``), where the highlight was before for the
    person to compare: the file it was read from (its ``doc_id``, else
    ``new_doc``; written even when that file is no longer stored), its page
    and rectangles there, and its quote before the run respelled it (""
    for an area).

    A highlight pending when the run starts (``approx``: dashed, and no
    person has accepted it) stays pending whatever the run finds, an exact
    copy included, with its ``match`` and its ``prev`` as they are (one
    placed anew without a ``prev`` gets one): only the person resolves it,
    by accepting it or moving it by hand, either of which writes a position
    without them. A position written in a new place names no file, except
    an area nothing placed (below); a lost one is the old one marked
    ``lost`` (written once, its ``doc_id`` kept, no ``prev`` added); one
    where it was is written only to drop its ``doc_id`` or, unless it is
    pending, a stale ``match`` or ``prev``, or to respell its quote.

    Each highlight is read (its passage, context, anchor and picture) from
    the file its rectangles were measured on: the one its position's
    ``doc_id`` names, as ``read_from(doc_id)`` gives it (a ``Doc``, or None
    for a file not stored; without ``read_from`` none is), else ``new``
    itself. A text highlight read from ``new`` whose quote agrees with the
    text under its rectangles is where it belongs and is kept without a
    search. A lost highlight without ``doc_id`` was not found in the file
    it was measured on, so only its quote is looked for, as for a text
    highlight whose quote is not the text under its rectangles. Among
    several places for a passage, the nearest is measured from the centre
    of the highlight's old rectangles to the centre of the candidate's
    glyphs, each as fractions of its own page.

    An area that nothing inside it places rides along with the highlights
    placed from its page of the same file, a kept one counting as no
    shift: when they all landed on one page and their shifts agree within
    ``RIDE_SPREAD``, it moves by their median shift (``approx``), unless
    that would carry it off the page. Otherwise nothing placed it: one
    read from ``new`` is where it was made and is kept; one measured on
    another file keeps its rectangles and its ``doc_id``, still that
    file's measurement, and is marked ``approx`` without ``match``, its
    ``prev`` those same rectangles (written once), for the person to check;
    a pending one is left as it is. An area placed by estimate (``approx``
    without ``match``) stays one through later runs, also when the text or
    picture under its box places it: that is only what the estimate
    happened to cover.

    ``progress`` gets a report per page read and per highlight, and is
    called bare (no counts: a place to stop) once per page the slower
    searches cover."""
    texts = new.all_texts(progress)
    if not any(page.text for page in texts.values()):
        raise ValueError("the new PDF has no text layer to find the highlights in")
    ops, kinds = {}, {}
    # (the file measured on, old page) → [(new page, (dx, dy))] of the highlights placed from it; the
    # file is the doc_id, None for the new file: a shift measured from another file's page says nothing
    shifts: dict = {}
    pending_areas = []    # area highlights nothing anchored

    def source(pos: dict):
        if pos.get("doc_id"):
            return read_from(pos["doc_id"]) if read_from else None
        return None if pos.get("lost") else new

    def near_to(pos: dict):
        target = _center(_norm_rect(pos["boundingRect"], _size(pos)))

        def near(page_no: int, s: int, e: int) -> float:
            boxes = rects_for_span(texts[page_no], s, e)
            if not boxes:
                return INF
            cx, cy = _center(_norm_box(_bounds(boxes), new.frame(page_no)))
            return math.hypot(cx - target[0], cy - target[1])

        return near

    def unflagged(pos: dict) -> dict:
        """``pos`` without what only a pending highlight carries, unless it is
        one: a stale ``match`` or ``prev``."""
        return pos if pos.get("approx") else {k: v for k, v in pos.items() if k not in ("match", "prev")}

    def keep(bid: str, pos: dict, quote: str = ""):
        """Where it was: written only to drop its ``doc_id`` (its rects are
        this file's) and a stale ``match`` or ``prev``, or to respell its
        quote."""
        kept = {k: v for k, v in unflagged(pos).items() if k != "doc_id"}
        patch = {"pdf_position": kept} if kept != pos else {}
        if quote:
            patch["quote"] = quote
        if patch:
            ops[bid] = patch
        kinds[bid] = "approx" if kept.get("approx") else "kept"

    def flagged(pos: dict, new_pos: dict, src_id: str, before: str, match: float, estimate: bool) -> dict:
        """``new_pos`` with the flags the run leaves on it: none for an
        exact match, ``approx`` (with ``match`` for a rough one) and ``prev``
        for a rough match or an estimate; a pending highlight's own."""
        out = {k: v for k, v in new_pos.items() if k not in ("approx", "match", "prev")}
        rough = None if estimate or match >= 1.0 else min(round(match, 2), 0.99)
        if pos.get("approx"):  # pending: only the person resolves it
            out["approx"] = True
            if "match" in pos:
                out["match"] = pos["match"]
            elif rough is not None:
                out["match"] = rough
            out["prev"] = shape.valid_prev(pos.get("prev")) or shape.previous_place(pos, src_id, before)
        elif estimate or rough is not None:
            out["approx"] = True
            if rough is not None:
                out["match"] = rough
            out["prev"] = shape.previous_place(pos, src_id, before)
        return out

    def settle(bid: str, pos: dict, new_pos, group, src_id: str, *, match: float = 1.0,
               estimate: bool = False, quote: str = "", before: str = ""):
        """Write what the run found for a highlight: ``new_pos`` (None: not
        found), by a match of confidence ``match`` or an ``estimate``,
        ``quote`` its new spelling; ``src_id`` names the file its old
        rectangles were measured on, ``before`` its quote before the run
        (``flagged``)."""
        if new_pos is None:
            kinds[bid] = "lost"
            if not pos.get("lost"):
                ops[bid] = {"pdf_position": {**unflagged(pos), "lost": True}}
            return
        # A box placed by what an estimate covered is still an estimate.
        estimate = estimate or bool(pos.get("area") and pos.get("approx") and "match" not in pos)
        same = _same_place(pos, new_pos)
        if not estimate:
            ca = _center(_norm_rect(pos["boundingRect"], _size(pos)))
            cb = _center(_norm_rect(new_pos["boundingRect"], _size(new_pos)))
            shift = (0.0, 0.0) if same else (cb[0] - ca[0], cb[1] - ca[1])
            shifts.setdefault(group, []).append((new_pos["pageNumber"], shift))
        if same and (estimate or match >= 1.0 or pos.get("approx")):
            # Where it was: solid when found exactly; an estimate, or a pending highlight, keeps its flags.
            keep(bid, pos, quote)
            return
        new_pos = flagged(pos, new_pos, src_id, before, match, estimate)
        kinds[bid] = "approx" if new_pos.get("approx") else "moved"
        ops[bid] = {"pdf_position": new_pos, **({"quote": quote} if quote else {})}

    def unplaced(bid: str, pos: dict, src, src_id: str):
        if src is new:
            keep(bid, pos)  # measured on this very file: where it was made
            return
        # Measured on another file: its rects and doc_id stay that file's, an estimate for the person to check
        # (a pending one as it is).
        kinds[bid] = "approx"
        if not pos.get("approx"):
            ops[bid] = {"pdf_position": {**unflagged(pos), "approx": True, "prev": shape.previous_place(pos, src_id)}}

    for i, (bid, props) in enumerate(items):
        if progress:
            progress(done=i, total=len(items), unit="highlights", phase="matching")
        _breathe()
        pos = props["pdf_position"]
        area = bool(pos.get("area"))
        src = source(pos)
        src_id = pos.get("doc_id") or new_doc  # the file its rectangles were measured on, stored or not
        group = (None if src is new else pos.get("doc_id"), pos["pageNumber"])
        quote = normalize_quote(props.get("quote") or "")
        before = "" if area or not isinstance(props.get("quote"), str) else props["quote"]
        old = _old_passage(src, pos)
        if old and quote and not area:
            if not _agrees(old[0], quote):
                old = None  # the rectangles do not belong to that file: the quote is looked for
            elif src is new:
                # Measured on this very file and still over its quote: where it belongs, without a search
                # (written only to drop a doc_id naming this file, or a lost mark; a pending one stays pending).
                settle(bid, pos, {k: v for k, v in pos.items() if k not in ("doc_id", "lost")}, group, src_id)
                continue
        needle, context, anchor, pieces = old or (quote, None, None, ())
        hint = min(max(pos["pageNumber"], 1), max(new.count, 1))
        near = near_to(pos)
        if not area:
            match = find_passage(texts, needle, hint_page=hint, context=context, near=near, tick=progress,
                                 pieces=pieces, min_match=min_match, exact_chars=exact_chars)
            spans = (match.spans or ((match.start, match.end),)) if match else ()
            boxes = [box for s, e in spans for box in rects_for_span(texts[match.page], s, e)]
            if not boxes:
                settle(bid, pos, None, group, src_id)
                continue
            spelled = _spelled(texts[match.page], spans) if match.confidence < 1.0 or match.part else ""
            settle(bid, pos, _position(match.page, new.frame(match.page), boxes), group, src_id,
                   match=match.confidence, quote=spelled if spelled != props.get("quote") else "", before=before)
            continue
        # An area: the text inside it, else the picture under it, else later with its neighbours.
        old_box = _norm_rect(pos["boundingRect"], _size(pos))
        if anchor is not None and len(needle) >= AREA_MIN_CHARS and len(needle.split()) >= AREA_MIN_WORDS:
            match = find_passage(texts, needle, hint_page=hint, context=context, near=near, tick=progress,
                                 min_match=min_match, exact_chars=exact_chars)
            # Only the whole of its text places an area: its part on one page would put the box askew.
            boxes = rects_for_span(texts[match.page], match.start, match.end) if match and not match.part else []
            if boxes:
                frame = new.frame(match.page)
                settle(bid, pos, _box_by_offsets(match.page, old_box, anchor, _norm_box(_bounds(boxes), frame), frame),
                       group, src_id, match=match.confidence)
                continue
        picture = _image_under(src, pos)
        where = _find_image(new, picture[0], hint, progress) if picture else None
        if where:
            found_page, new_img = where
            settle(bid, pos, _box_by_offsets(found_page, old_box, picture[1], new_img, new.frame(found_page)), group,
                   src_id)
            continue
        pending_areas.append((bid, pos, old_box, src, group, src_id))

    for bid, pos, old_box, src, group, src_id in pending_areas:
        moves = shifts.get(group) or []
        pages_to = {p for p, _ in moves}
        if len(pages_to) != 1:
            unplaced(bid, pos, src, src_id)
            continue
        dxs, dys = [d[0] for _, d in moves], [d[1] for _, d in moves]
        if max(dxs) - min(dxs) > RIDE_SPREAD or max(dys) - min(dys) > RIDE_SPREAD:
            unplaced(bid, pos, src, src_id)
            continue
        to_page, dx, dy = pages_to.pop(), median(dxs), median(dys)
        if to_page == pos["pageNumber"] and abs(dx) <= SAME_PLACE and abs(dy) <= SAME_PLACE:
            keep(bid, pos)  # its neighbours all stayed where they were: so does it
            continue
        shifted = (old_box[0] + dx, old_box[1] + dy, old_box[2] + dx, old_box[3] + dy)
        if min(shifted) < -EDGE or max(shifted) > 1 + EDGE:
            unplaced(bid, pos, src, src_id)  # the shift would carry it off the page: a guess that far is no guess
            continue
        settle(bid, pos, _box_by_offsets(to_page, old_box, old_box, shifted, new.frame(to_page)), group, src_id,
               estimate=True)
    counts = Counter(kinds.values())
    return {"ops": ops, "kinds": kinds, "highlights": len(items),
            **{kind: counts[kind] for kind in ("moved", "kept", "lost", "approx")}}


def thresholds(profile) -> dict:
    """A run's thresholds from its owner's preference profile
    (``db.get_profile``): ``{"min_match", "exact_chars"}``, the confidence
    from which a rough match is applied (``relocateMinMatch``, a
    percentage from 50 to 100 in steps of 5, as a fraction 0.5 to 1.0)
    and the length under which a passage must match exactly
    (``relocateExactChars``, 0 to 60 characters). A number is read as the
    web app's codec reads it (prefDefs.js): cut to a whole number, put on
    its step and pulled into its range; a missing one, or one that is no
    number, is the default (``ROUGH_APPLY``, ``ROUGH_MIN_CHARS``)."""
    profile = profile if isinstance(profile, dict) else {}

    def whole(name, bounds):
        lo, hi, step = bounds
        value = profile.get(name)
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            return None
        return min(hi, max(lo, lo + round((int(value) - lo) / step) * step))

    percent, chars = whole(MIN_MATCH_PREF, MIN_MATCH_RANGE), whole(EXACT_CHARS_PREF, EXACT_CHARS_RANGE)
    return {"min_match": ROUGH_APPLY if percent is None else percent / 100,
            "exact_chars": ROUGH_MIN_CHARS if chars is None else chars}


def relocate(ws: str, page_id: str, new_doc: str, *, actor: str, progress=None, min_match: float | None = None,
             exact_chars: int | None = None) -> dict:
    """Place the page's highlights in its PDF ``new_doc`` and write the ones
    that changed, under ``actor`` (``place_all``), by the thresholds
    ``min_match`` and ``exact_chars`` (``find_passage``; the run's owner's,
    ``thresholds``; ``ROUGH_APPLY`` and ``ROUGH_MIN_CHARS`` when not
    given). Each highlight is read
    from the file its rectangles were measured on: the stored file its
    position's ``doc_id`` names while it is stored (looked for among the
    workspace's uploads only, never fetched; each file opened once), else
    the new file; a highlight whose file is gone is placed by its quote.
    The new file is the stored ``<new_doc>.pdf``, or a URL-opened PDF
    fetched as the indexer fetches it. The files, and all that was read
    from them, are let go of before the write.

    The write is one compare-and-set (``_write``): nothing is written when
    the page is no longer a live page carrying ``new_doc``, and a highlight
    changed or deleted since the run read it is left as it is. The result:
    ``{highlights, moved, kept, lost, approx, changed, min_match,
    exact_chars}``, the five after the first adding up to it: ``moved``
    found exactly in a new place, ``kept`` found exactly where it was,
    ``approx`` drawn dashed (found roughly, or an area placed by
    estimate, or pending already), ``lost`` not found, and ``changed``
    left as a person changed it meanwhile (``place_all``); then the
    thresholds the run went by. ValueError when the new file is not
    stored or cannot be read, when it has no text layer, or from the
    write."""
    limits = {"min_match": ROUGH_APPLY if min_match is None else min_match,
              "exact_chars": ROUGH_MIN_CHARS if exact_chars is None else exact_chars}
    with connect_pages_db(ws) as conn:
        items = page_highlights(conn, page_id)
    if not items:
        return {"highlights": 0, "moved": 0, "kept": 0, "lost": 0, "approx": 0, "changed": 0, **limits}
    from .ai_context import pdf_path  # a URL-opened PDF: fetched as the indexer fetches it
    new_path = pdf_path(ws, new_doc)
    if new_path is None:
        raise ValueError("the new PDF is not stored on the server")
    with ExitStack() as files:
        new = files.enter_context(Doc(new_path))
        if new.count == 0:
            raise ValueError("the new PDF cannot be read")
        opened = {}

        def read_from(doc_id):
            """The stored file ``doc_id`` as a ``Doc``, or None when it is no
            longer stored; a position's ``doc_id`` is data any client
            writes, so one that is no file name names no file."""
            if doc_id == new_doc:
                return new
            try:
                name = f"{safe_doc_id(doc_id)}.pdf"
            except ValueError:
                return None
            if doc_id not in opened:
                path = find_upload_file(name, ws)
                if path is None:
                    log.info(f"[relocate] {page_id}: the file {doc_id} is no longer stored; "
                             "its highlights are placed by their quotes")
                opened[doc_id] = files.enter_context(Doc(path)) if path else None
            return opened[doc_id]

        placed = place_all(new, items, progress, read_from=read_from, new_doc=new_doc, **limits)
    ops = [{"op": "set", "id": bid, "props": patch} for bid, patch in placed.pop("ops").items()]
    kinds = placed.pop("kinds")
    placed["changed"] = 0
    if progress:
        progress(done=placed["highlights"], total=placed["highlights"], unit="highlights", phase="matching")
        progress(stoppable=False)  # the write follows: from here on the run finishes
    if ops:
        _write(ws, page_id, new_doc, items, ops, kinds, placed, actor=actor)
    return {**placed, **limits}


def _write(ws: str, page_id: str, new_doc: str, items: list[tuple[str, dict]], ops: list[dict], kinds: dict,
           counts: dict, *, actor: str) -> None:
    """``relocate``'s write, in one transaction under the page's write lock.
    The page must still be a live page (not deleted, not in Recently
    deleted) and still carry ``new_doc`` (``attachment_file``: by its doc
    id, or a duplicated page by its upload's address), else ValueError and
    nothing is written. An op for a highlight deleted, or whose position or quote
    changed since ``items`` were read (a person moved it meanwhile), is
    left out and its count in ``counts`` moves to ``changed``. The rest go
    as batches of at most ``MAX_OPS`` on the page, client ``relocate``,
    committed together, then each batch's ``after_commit``."""
    def state(props: dict):
        return props.get("pdf_position"), props.get("quote")

    before = {bid: state(props) for bid, props in items}
    with connect_pages_db(ws) as conn:
        try:
            write_lock(conn)
            row = conn.execute("SELECT parent_id, properties FROM unified_blocks WHERE id = ?",
                               (page_id,)).fetchone()
            if not row or row[0] != "root":
                raise ValueError("the page was deleted before its highlights were placed — restore it and "
                                 "choose Find highlights in this PDF")
            if attachment_file(page_attachment(load_json(row[1] or "{}") or {})) != new_doc:
                raise ValueError("the page's PDF changed again before the highlights were placed")
            now = {bid: state(props) for bid, props in page_highlights(conn, page_id)}
            unchanged = []
            for op in ops:
                if now.get(op["id"]) == before[op["id"]]:
                    unchanged.append(op)
                else:
                    counts[kinds[op["id"]]] -= 1
                    counts["changed"] += 1
            batches = [(page_id, unchanged[i:i + MAX_OPS]) for i in range(0, len(unchanged), MAX_OPS)]
            results = apply_batches(conn, batches, actor=actor, client=CLIENT) if batches else []
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
        after_commits(ws, conn, results)
