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
lost keeps it, so a later run still reads that highlight from its own
file.

A page's text, in both files, is MuPDF's lines joined with a space and
folded (dashes, quotes, ligatures, case), a word hyphenated at a line's
end joined whole, and a line made of a number alone left out: margin line
numbers and page numbers, so a numbered review copy and its camera-ready
read alike. The passage under a highlight is the text of the glyphs it
covers, the stretches it skips (a line's end, a caption the file draws
between two columns) read as one space.

The passage is searched for in the new file's text, every page. Exactly
first, on word edges ("ion" is not the end of "motion") unless the old
passage itself began or ended inside a word; then, from four words on,
word by word with a few edits allowed (one per six words, never to a
number or a symbol, only to the punctuation around one), also when no
exact copy stands in surroundings that read
like the old ones, so a passage reworded in place beats a verbatim copy
elsewhere; and when no page holds it, across each page break, its two
parts searched apart so the running head or footer between them costs
nothing, the highlight going to the page with the larger part. Among
several places, one in surroundings like the old file's comes first, then
the fewest edits, the closest surroundings, the page nearest the old one,
and the place nearest the old rectangles (each as fractions of its own
page). The glyphs it covers there, line by line, are its new rectangles,
in the new page's frame (the crop box as pdf.js shows it, rotation
applied: pdf_export's mappers). An area highlight is placed by the text
inside it when it has some (a caption, an axis), else by the picture it
covers (the same image data in the new file), else it rides along with
the page's other highlights when they all moved the same way
(``approx``), else it stays. A highlight whose text is nowhere keeps its
old rectangles and is marked ``lost`` for the viewer to show hollow,
until the person moves it or a later run finds it.

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
# The context fit (0..1) from which a place stands in the passage's own
# surroundings: unrelated 40-character windows score 0.30 at the median and
# 0.475 at the 99th percentile, lightly edited ones 0.83 to 0.90.
CONTEXT_OK = 0.6
MIN_NEEDLE = 3            # characters a passage needs to be looked for at all
FUZZY_MIN_WORDS = 4       # a shorter passage is found exactly or not at all
FUZZY_MIN_CHARS = 12
WORDS_PER_EDIT = 6        # the budget of the word-by-word search: one edit per this many words...
MAX_EDITS = 8             # ...and never more than this
MAX_SEEDS = 6             # places on a page the word-by-word search tries
JOIN_SLACK = 20           # words at a page break that may stand between a passage's two parts: a running head, a footnote line, a caption
AREA_MIN_CHARS = 12       # the text inside an area highlight that can place it
AREA_MIN_WORDS = 2
SAME_PLACE = 0.005        # of the page: a position found this close to the old one is "kept"
RIDE_SPREAD = 0.15        # of the page: the shifts of a page's highlights must agree within this to carry an area along
EDGE = 1e-6               # of the page: the rounding a box carried along may lie past the page's edge by
QUOTE_AGREES = 0.6        # the likeness from which the text under a highlight's rectangles is its stored quote
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
# What a copyedit changes around a number or a variable ("Fig. 3," for
# "Fig. 3.", "(4)" for "4"): not ' or " (a prime folds to them), ! (a
# factorial) or < > (angle brackets), which belong to the symbol.
_PROSE_PUNCT = ".,;:?()[]{}"
_HYPHEN_BREAK_RE = re.compile(rf"(?<=\w)[-{_DASHES}][ \t]*\n\s*(?=\w)")
_SPACES_RE = re.compile(r" {2,}")
_WORD_RE = re.compile(r"\S+")
_LINE_NUMBER_RE = re.compile(r"[0-9]{1,5}")
_NEGATIONS = frozenset({"not", "no", "never", "none", "nor", "neither", "without", "cannot"})


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
        of the glyphs it spans, the line breaks as spaces."""
        if start >= end or not self.glyph_at:
            return ""
        g0, g1 = self.glyph_at[start], self.glyph_at[min(end, len(self.glyph_at)) - 1]
        out = "".join(" " if ch.isspace() else ch for ch in (self.char(gi) for gi in range(g0, g1 + 1)))
        return _SPACES_RE.sub(" ", out).strip()

    def word_index(self):
        """The page's words as the word-by-word search looks them up, made on
        first use: ``(where, stripped, present)``, the indices of each
        word, each word without the punctuation at its edges, and the set
        of those."""
        if self._word_index is None:
            where: dict = {}
            for j, w in enumerate(self.word_texts):
                where.setdefault(w, []).append(j)
            stripped = [w.strip(_PUNCT) for w in self.word_texts]
            self._word_index = (where, stripped, frozenset(stripped))
        return self._word_index


def glyphs_under(page: PageText, quads) -> list[int]:
    """The indices of the page's glyphs whose centre lies in one of
    ``quads`` (``(x1, y1, x2, y2)`` boxes in the glyphs' space), in page
    order: pdf_text.covers, the rule of the annotation import's quotes."""
    return [gi for gi in range(page.glyph_count) if covers(quads, page.box(gi))]


# --- the search ----------------------------------------------------------------

# _protected and _word_cost are kept for the words they were last asked
# about: a search asks about the same few thousand words of a paper over and
# over, in every window of every page, for every highlight.

@lru_cache(maxsize=1 << 15)
def _protected(word: str) -> bool:
    """A word the word-by-word search may not change, add or drop: one with
    a digit, a single letter or symbol (a variable, a Greek letter, an
    operator), a negation. Only the prose punctuation around it may change
    (``_word_cost``)."""
    core = word.strip(_PUNCT)
    if not core:
        return False
    if any(c.isdigit() for c in core):
        return True
    if len(core) == 1:
        return True
    return core in _NEGATIONS


@lru_cache(maxsize=1 << 15)
def _word_cost(a: str, b: str) -> float:
    if a == b:
        return 0.0
    if _protected(a) or _protected(b):
        # its punctuation may change ("3," for "3.", "(4)" for "4"), never the word itself
        return 0.25 if a.strip(_PROSE_PUNCT) == b.strip(_PROSE_PUNCT) else INF
    ca, cb = a.strip(_PUNCT), b.strip(_PUNCT)
    if ca == cb:
        return 0.25  # punctuation only
    if len(ca) >= 4 and len(cb) >= 4 and _spelled_alike(ca, cb):
        return 0.5   # a spelling, "colour" for "color"
    return 1.0


def _spelled_alike(a: str, b: str) -> bool:
    """Whether two words read as one spelled two ways: difflib's ratio of
    0.8 or more, after its cheaper upper bounds (the lengths alone, then
    the letters), which turn most pairs down without the full match."""
    if 2 * min(len(a), len(b)) < 0.8 * (len(a) + len(b)):
        return False
    matcher = SequenceMatcher(None, a, b)
    return matcher.quick_ratio() >= 0.8 and matcher.ratio() >= 0.8


def _rows(needle: list[str], words: list[str], lo: int, hi: int):
    """The word-by-word alignment of ``needle`` against ``words[lo:hi]``, one
    row per needle word: ``(costs, starts)``, where ``costs[j]`` is the
    cheapest alignment of the needle's words so far against a run of the
    window ending before ``words[lo + j]`` (the run free to start anywhere)
    and ``starts[j]`` the word that run starts at. A word changed, added or
    dropped costs one; a protected word (``_protected``) cannot be. On a
    tie the later start wins, so a dropped first word never pulls in the
    word before it."""
    m = hi - lo
    prev = [0.0] * (m + 1)
    start_prev = list(range(lo, hi + 1))
    add = [INF if _protected(w) else 1.0 for w in words[lo:hi]]
    for nw in needle:
        _breathe()
        drop = INF if _protected(nw) else 1.0
        cur = [prev[0] + drop] + [INF] * m
        start_cur = [lo] * (m + 1)
        for j in range(1, m + 1):
            sub = prev[j - 1] + _word_cost(nw, words[lo + j - 1])
            dele = prev[j] + drop
            ins = cur[j - 1] + add[j - 1]
            best = min(sub, dele, ins)
            cur[j] = best
            start_cur[j] = start_prev[j] if best == dele else start_prev[j - 1] if best == sub else start_cur[j - 1]
        yield cur, start_cur
        prev, start_prev = cur, start_cur


def _align(needle: list[str], words: list[str], lo: int, hi: int, budget: float = INF):
    """The cheapest alignment of every word of ``needle`` against a run of
    ``words[lo:hi]`` (``_rows``; the run free to start and end anywhere in
    the window, the earliest end on a tie): ``(cost, start, end)`` in word
    indices. Once a row costs more than ``budget`` everywhere, no later row
    costs less, and the answer is ``(INF, lo, lo)`` at once."""
    costs, starts = [0.0] * (hi - lo + 1), list(range(lo, hi + 1))
    for costs, starts in _rows(needle, words, lo, hi):
        if min(costs) > budget:
            return INF, lo, lo
    j = min(range(len(costs)), key=costs.__getitem__)
    return costs[j], starts[j], lo + j


def _budget(words: int) -> float:
    return min(float(MAX_EDITS), max(1.0, words / WORDS_PER_EDIT))


def _enough(cores: list[str], present, budget: float) -> bool:
    """Whether a run within ``budget`` could lie among words whose stripped
    forms are ``present``: every needle word not matched by its own word
    up to punctuation costs at least a half, so such a run matches at
    least ``len(cores) - 2 * budget`` of the needle's words (``cores``,
    stripped) to words equal to them."""
    return sum(c in present for c in cores) >= len(cores) - 2 * budget


def _seed_windows(page: PageText, needle_words: list[str], budget: float) -> list[tuple[int, int]]:
    """Where on ``page`` the word-by-word search aligns ``needle_words``: the
    page's words equal to the needle's vote for where the needle would
    start, and around each of the ``MAX_SEEDS`` best-voted starts (none
    within the slack of a better one) the needle's length and the slack
    either side, ``[(lo, hi)]`` in word indices."""
    where = page.word_index()[0]
    votes: dict = {}
    for i, w in enumerate(needle_words):
        for j in where.get(w, ()):
            votes[j - i] = votes.get(j - i, 0) + 1
    slack = int(budget) + 1
    seeds = []
    for start, _count in sorted(votes.items(), key=lambda kv: (-kv[1], kv[0])):
        if any(abs(start - s) <= slack for s in seeds):
            continue
        seeds.append(start)
        if len(seeds) >= MAX_SEEDS:
            break
    windows = []
    for start in seeds:
        lo, hi = max(0, start - slack), min(len(page.word_texts), start + len(needle_words) + slack)
        if hi > lo:
            windows.append((lo, hi))
    return windows


def _fuzzy_on_page(page: PageText, needle_words: list[str]):
    """The runs of the page that read like the needle within the budget,
    ``[(cost, start_char, end_char)]``: the seed windows
    (``_seed_windows``) aligned word by word. A page, or a window, that
    lacks too many of the needle's words to hold such a run (``_enough``)
    is passed over without aligning, which changes no answer."""
    n = len(needle_words)
    if n < FUZZY_MIN_WORDS or not page.word_texts:
        return []
    budget = _budget(n)
    _where, stripped, present = page.word_index()
    cores = [w.strip(_PUNCT) for w in needle_words]
    if not _enough(cores, present, budget):
        return []
    out = []
    for lo, hi in _seed_windows(page, needle_words, budget):
        if not _enough(cores, set(stripped[lo:hi]), budget):
            continue
        cost, a, b = _align(needle_words, page.word_texts, lo, hi, budget)
        if cost <= budget and b > a:
            out.append((cost, *_trimmed(page, page.word_starts[a], page.word_ends[b - 1], needle_words)))
    return out


def _row_bests(needle: list[str], words: list[str], lo: int, hi: int, budget: float) -> list[tuple]:
    """For each ``i``, the cheapest alignment of ``needle[:i]`` against a run
    of ``words[lo:hi]`` that starts and ends anywhere: ``(cost, start,
    end)`` in word indices, ``i`` from 0 (nothing to align) to the
    needle's length; INF once past ``budget``."""
    out = [(0.0, lo, lo)]
    for costs, starts in _rows(needle, words, lo, hi):
        j = min(range(len(costs)), key=costs.__getitem__)
        out.append((costs[j], starts[j], lo + j))
        if costs[j] > budget:
            break
    return out + [(INF, lo, lo)] * (len(needle) + 1 - len(out))


def _across(first: PageText, second: PageText, needle_words: list[str]):
    """The passage split by the break between two pages that follow each
    other: ``(cost, on_first, start, end)``, the larger part's span on its
    page (the first on a tie), or None.

    The two parts are searched apart rather than as one string of the
    pages' joined text: what stands between them (the first page's footer
    and footnotes, the next one's running head, "Phys. Rev. A 104, 033101
    (2021)") holds numbers the word-by-word search may not skip, and here
    it costs nothing. The head of the passage is aligned against the last
    words of the first page, its tail against the first words of the
    second (reversed, so that it may start anywhere there), and the cut
    between them is the one that costs least in all, within the budget of
    the whole passage; each part must cover some words of its page."""
    n = len(needle_words)
    budget = _budget(n)
    reach = n + JOIN_SLACK
    lo = max(0, len(first.word_texts) - reach)
    head = second.word_texts[:reach]
    if lo >= len(first.word_texts) or not head:
        return None
    nearby = set(first.word_index()[1][lo:]) | set(second.word_index()[1][:reach])
    if not _enough([w.strip(_PUNCT) for w in needle_words], nearby, budget):
        return None
    pre = _row_bests(needle_words, first.word_texts, lo, len(first.word_texts), budget)
    rev = head[::-1]
    suf = _row_bests(needle_words[::-1], rev, 0, len(rev), budget)  # suf[k]: the needle's last k words
    cut = min(range(1, n), key=lambda i: pre[i][0] + suf[n - i][0])
    cost = pre[cut][0] + suf[n - cut][0]
    if cost > budget:
        return None
    _c, a1, b1 = pre[cut]
    _c, ra, rb = suf[n - cut]
    a2, b2 = len(rev) - rb, len(rev) - ra
    if b1 <= a1 or b2 <= a2:
        return None
    if cut >= n - cut:
        return cost, True, *_trimmed(first, first.word_starts[a1], first.word_ends[b1 - 1], needle_words[:cut])
    return cost, False, *_trimmed(second, second.word_starts[a2], second.word_ends[b2 - 1], needle_words[cut:])


def _trimmed(page: PageText, s: int, e: int, needle_words: list[str]) -> tuple[int, int]:
    """The match ``text[s:e]`` without the punctuation at its edges that the
    needle's first and last words do not end in ("cooled" matched
    "cooled.": the period stays outside)."""
    while e > s and page.text[e - 1] in _PUNCT and not needle_words[-1].endswith(page.text[e - 1]):
        e -= 1
    while s < e and page.text[s] in _PUNCT and not needle_words[0].startswith(page.text[s]):
        s += 1
    return s, e


def _similar(a: str, b: str) -> float:
    return SequenceMatcher(None, a, b).ratio() if a and b else 0.0


class Match(NamedTuple):
    """Where ``find_passage`` found a passage: ``text[start:end]`` of page
    ``page``, after ``cost`` edits of the word-by-word search. ``part``:
    only the larger part of the passage, the rest being across a page
    break."""
    page: int
    start: int
    end: int
    cost: float
    part: bool = False


def find_passage(pages: dict, needle: str, *, hint_page: int = 1, context: tuple | None = None,
                 near=None, tick=None) -> Match | None:
    """Where ``needle`` (a normalized passage) is in ``pages`` (page number →
    ``PageText``): a ``Match``, or None.

    Every exact occurrence on word edges is a candidate: a hit inside a
    word ("ion" in "motion", "fig. 1" in "fig. 12") is not the passage,
    unless the old passage itself began or ended inside a word, as its
    ``context`` (``(before, after)``, the old file's text on either side)
    shows. The word-by-word search's runs (``_fuzzy_on_page``) are
    candidates too when there is no exact one, and when there is a context
    and no exact one stands in surroundings that read like it: a passage
    reworded in place then still beats a verbatim copy elsewhere. When no
    page holds the passage at all, it is looked for across each page break
    (``_across``), as a ``part``; that needs a passage the word-by-word
    search would take, since a few words found on either side of some
    page break mean nothing.

    How well a place's surroundings read like the context is the mean
    likeness of the sides the context has (0 to 1). Among the candidates,
    one whose surroundings reach ``CONTEXT_OK`` comes first, then the one
    with the fewest edits, the best context, the page nearest
    ``hint_page``, and the place nearest the old one by ``near(page,
    start, end)``, a distance the caller measures (``place_all`` measures
    it between the candidate's glyphs and the highlight's rectangles, each
    as fractions of its own page). ``tick()`` runs once per page the word
    by word and the page-break searches cover, so a stop lands within a
    page."""
    needle = (needle or "").strip()
    if len(needle) < MIN_NEEDLE:
        return None
    before, after = context or ("", "")
    has_context = bool(before or after)
    need_start = needle[0].isalnum() and not (before and before[-1].isalnum())
    need_end = needle[-1].isalnum() and not (after and after[0].isalnum())
    found = []
    for page_no, page in pages.items():
        _breathe()
        text = page.text
        at = text.find(needle)
        while at >= 0:
            end = at + len(needle)
            inside = ((need_start and at > 0 and text[at - 1].isalnum())
                      or (need_end and end < len(text) and text[end].isalnum()))
            if not inside:
                found.append(Match(page_no, at, end, 0.0))
            at = text.find(needle, at + 1)
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

    words = needle.split()
    wordy = len(needle) >= FUZZY_MIN_CHARS and len(words) >= FUZZY_MIN_WORDS
    if wordy and (not found or (has_context and all(fit(m) < CONTEXT_OK for m in found))):
        exact = {(m.page, m.start, m.end) for m in found}
        for page_no, page in pages.items():
            if tick:
                tick()
            _breathe()
            found.extend(Match(page_no, s, e, cost) for cost, s, e in _fuzzy_on_page(page, words)
                         if (page_no, s, e) not in exact)
    if wordy and not found:
        for page_no in pages:
            if page_no + 1 not in pages:
                continue
            if tick:
                tick()
            _breathe()
            split = _across(pages[page_no], pages[page_no + 1], words)
            if split:
                cost, on_first, s, e = split
                found.append(Match(page_no if on_first else page_no + 1, s, e, cost, part=True))
    if not found:
        return None

    def rank(m: Match):
        f = fit(m) if has_context else 0.0
        return (not (has_context and f >= CONTEXT_OK), m.cost, -f, abs(m.page - hint_page),
                near(m.page, m.start, m.end) if near else 0.0)

    return min(found, key=rank)


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


def _joined(page: PageText, stretches: list) -> str:
    """The text of ``stretches`` in a row, one space between two, as a
    selection reads: a line's end and the next line's start, or a column's
    end and the next column's top past the caption the file draws between
    them. A word hyphenated where the highlight skips text and a line break
    ("entangle-" ... "ment") is joined whole, PageText's rule across the
    text in between, whether or not the page's text kept the hyphen."""
    out = page.text[stretches[0][0]:stretches[0][1]]
    for (_a, _b, _first, last), (a, b, first, _last) in zip(stretches, stretches[1:]):
        part = page.text[a:b]
        hyphen = last
        while hyphen > 0 and page.char(hyphen).isspace():
            hyphen -= 1
        if (_fold(page.char(hyphen)) == "-" and out.removesuffix("-")[-1:].isalpha() and part[:1].isalpha()
                and any(page.char(g) == "\n" for g in range(last + 1, first))):
            out = out.removesuffix("-") + part
        else:
            out += " " + part
    return out


def _old_passage(src, pos: dict):
    """What ``src`` (the ``Doc`` the highlight's rectangles were measured
    on, or None) reads under a highlight: ``(needle, (before, after),
    anchor)``, or None when there is no such file or no text under the
    rectangles.

    A text highlight's needle is the text of the glyphs it covers alone,
    their stretches joined as a selection reads (``_joined``): a margin
    note, a caption or a column's foot between two of its lines is not in
    it. The context is the ``CONTEXT_CHARS`` before the first stretch and
    after the last.

    An area's needle is the text from its first covered glyph to its last,
    and its anchor that text's boxes (``rects_for_span``, as fractions of
    the page), measured as ``place_all`` measures the match in the new
    file, so an unchanged area is found where it was. The covered glyphs
    alone would not do: an area over a table's column, which the file
    draws row by row, covers glyphs that never follow each other in the
    text, not even in an identical file. Only when that span runs through
    much text the area does not cover (glyphs drawn far out of reading
    order) are the covered glyphs the needle, their box the anchor. A text
    highlight has no anchor."""
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
        return _joined(page, stretches), context, None
    if e - s > 2 * len(covered) + 10:
        return _joined(page, stretches), context, _norm_box(_bounds([page.box(gi) for gi in covered]), frame)
    return page.text[s:e], context, _norm_box(_bounds(rects_for_span(page, s, e)), frame)


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
    its stored ``quote``, both folded: one holds the other, or they read
    alike from ``QUOTE_AGREES`` on. When they do not, the rectangles were
    not measured on the file they were read from."""
    return (quote in needle or needle in quote
            or SequenceMatcher(None, needle, quote, autojunk=False).ratio() >= QUOTE_AGREES)


def place_all(new, items: list[tuple[str, dict]], progress=None, *, read_from=None) -> dict:
    """Each highlight's place in ``new`` (a ``Doc``, the page's PDF now):
    ``{"ops": {id: patch}, "kinds": {id: kind}, "highlights", "moved",
    "kept", "lost", "approx"}``. A patch is the properties to write on a
    highlight: its ``pdf_position``, and its ``quote`` as the new file
    spells it when the word-by-word search read the passage differently
    or the highlight now covers only the part of it on one side of a page
    break. A moved or approx position is a new one and names no file; a
    lost one is the old one marked ``lost`` (written once, its ``doc_id``
    kept); a kept one is written only to drop its ``doc_id`` or to respell
    its quote. ``kinds`` says which of the four counts each highlight went
    to.

    Each highlight is read (its passage, context, anchor and picture) from
    the file its rectangles were measured on: the one its position's
    ``doc_id`` names, as ``read_from(doc_id)`` gives it (a ``Doc``, or None
    for a file not stored; without ``read_from`` none is), else ``new``
    itself. A lost highlight without ``doc_id`` was not found in the file
    it was measured on, so only its quote is looked for, as for a text
    highlight whose quote is not the text under its rectangles. Among
    several places for a passage, the nearest is measured from the centre
    of the highlight's old rectangles to the centre of the candidate's
    glyphs, each as fractions of its own page.

    An area that nothing inside it places rides along with the highlights
    placed from its page, a kept one counting as no shift: when they all
    landed on one page and their shifts agree within ``RIDE_SPREAD``, it
    moves by their median shift (``approx``), unless that would carry it
    off the page; otherwise it stays. An area placed by estimate stays
    ``approx`` through later runs, also when the text or picture under its
    box places it: that is only what the estimate happened to cover. A
    move by hand clears it.

    ``progress`` gets a report per page read and per highlight, and is
    called bare (no counts: a place to stop) once per page the slower
    searches cover."""
    texts = new.all_texts(progress)
    if not any(page.text for page in texts.values()):
        raise ValueError("the new PDF has no text layer to find the highlights in")
    ops, kinds = {}, {}
    shifts: dict = {}     # old page → [(new page, (dx, dy))] of the highlights placed from it
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

    def keep(bid: str, pos: dict, quote: str = ""):
        kinds[bid] = "kept"
        patch = {"pdf_position": {k: v for k, v in pos.items() if k != "doc_id"}} if "doc_id" in pos else {}
        if quote:
            patch["quote"] = quote
        if patch:
            ops[bid] = patch

    def settle(bid: str, pos: dict, new_pos, *, approx: bool = False, quote: str = ""):
        if new_pos is None:
            kinds[bid] = "lost"
            if not pos.get("lost"):
                ops[bid] = {"pdf_position": {**pos, "lost": True}}
            return
        # A box placed by what an estimate covered is still an estimate.
        approx = approx or bool(pos.get("approx"))
        if approx:
            new_pos = {**new_pos, "approx": True}
        same = _same_place(pos, new_pos)
        if not approx:
            ca = _center(_norm_rect(pos["boundingRect"], _size(pos)))
            cb = _center(_norm_rect(new_pos["boundingRect"], _size(new_pos)))
            shift = (0.0, 0.0) if same else (cb[0] - ca[0], cb[1] - ca[1])
            shifts.setdefault(pos["pageNumber"], []).append((new_pos["pageNumber"], shift))
        if same:
            keep(bid, pos, quote)
            return
        kinds[bid] = "approx" if approx else "moved"
        ops[bid] = {"pdf_position": new_pos, **({"quote": quote} if quote else {})}

    for i, (bid, props) in enumerate(items):
        if progress:
            progress(done=i, total=len(items), unit="highlights", phase="matching")
        _breathe()
        pos = props["pdf_position"]
        area = bool(pos.get("area"))
        src = source(pos)
        quote = normalize_quote(props.get("quote") or "")
        old = _old_passage(src, pos)
        if old and quote and not area and not _agrees(old[0], quote):
            old = None  # the rectangles do not belong to that file: the quote is looked for
        needle, context, anchor = old or (quote, None, None)
        hint = min(max(pos["pageNumber"], 1), max(new.count, 1))
        near = near_to(pos)
        if not area:
            match = find_passage(texts, needle, hint_page=hint, context=context, near=near, tick=progress)
            boxes = rects_for_span(texts[match.page], match.start, match.end) if match else []
            if not boxes:
                settle(bid, pos, None)
                continue
            spelled = texts[match.page].original(match.start, match.end) if match.cost > 0 or match.part else ""
            settle(bid, pos, _position(match.page, new.frame(match.page), boxes),
                   quote=spelled if spelled != props.get("quote") else "")
            continue
        # An area: the text inside it, else the picture under it, else later with its neighbours.
        old_box = _norm_rect(pos["boundingRect"], _size(pos))
        if anchor is not None and len(needle) >= AREA_MIN_CHARS and len(needle.split()) >= AREA_MIN_WORDS:
            match = find_passage(texts, needle, hint_page=hint, context=context, near=near, tick=progress)
            # Only the whole of its text places an area: its part on one page would put the box askew.
            boxes = rects_for_span(texts[match.page], match.start, match.end) if match and not match.part else []
            if boxes:
                frame = new.frame(match.page)
                settle(bid, pos, _box_by_offsets(match.page, old_box, anchor, _norm_box(_bounds(boxes), frame), frame))
                continue
        picture = _image_under(src, pos)
        where = _find_image(new, picture[0], hint, progress) if picture else None
        if where:
            found_page, new_img = where
            settle(bid, pos, _box_by_offsets(found_page, old_box, picture[1], new_img, new.frame(found_page)))
            continue
        pending_areas.append((bid, pos, old_box))

    for bid, pos, old_box in pending_areas:
        moves = shifts.get(pos["pageNumber"]) or []
        pages_to = {p for p, _ in moves}
        if len(pages_to) != 1:
            keep(bid, pos)
            continue
        dxs, dys = [d[0] for _, d in moves], [d[1] for _, d in moves]
        if max(dxs) - min(dxs) > RIDE_SPREAD or max(dys) - min(dys) > RIDE_SPREAD:
            keep(bid, pos)
            continue
        to_page, dx, dy = pages_to.pop(), median(dxs), median(dys)
        if to_page == pos["pageNumber"] and abs(dx) <= SAME_PLACE and abs(dy) <= SAME_PLACE:
            keep(bid, pos)
            continue
        shifted = (old_box[0] + dx, old_box[1] + dy, old_box[2] + dx, old_box[3] + dy)
        if min(shifted) < -EDGE or max(shifted) > 1 + EDGE:
            keep(bid, pos)  # the shift would carry it off the page: a guess that far is no guess
            continue
        settle(bid, pos, _box_by_offsets(to_page, old_box, old_box, shifted, new.frame(to_page)), approx=True)
    counts = Counter(kinds.values())
    return {"ops": ops, "kinds": kinds, "highlights": len(items),
            **{kind: counts[kind] for kind in ("moved", "kept", "lost", "approx")}}


def relocate(ws: str, page_id: str, new_doc: str, *, actor: str, progress=None) -> dict:
    """Place the page's highlights in its PDF ``new_doc`` and write the ones
    that changed, under ``actor`` (``place_all``). Each highlight is read
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
    ``{highlights, moved, kept, lost, approx, changed}``, the last five
    adding up to the first, ``changed`` counting the highlights left as a
    person changed them meanwhile. ValueError when the new file is not
    stored or cannot be read, when it has no text layer, or from the
    write."""
    with connect_pages_db(ws) as conn:
        items = page_highlights(conn, page_id)
    if not items:
        return {"highlights": 0, "moved": 0, "kept": 0, "lost": 0, "approx": 0, "changed": 0}
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

        placed = place_all(new, items, progress, read_from=read_from)
    ops = [{"op": "set", "id": bid, "props": patch} for bid, patch in placed.pop("ops").items()]
    kinds = placed.pop("kinds")
    placed["changed"] = 0
    if progress:
        progress(done=placed["highlights"], total=placed["highlights"], unit="highlights", phase="matching")
        progress(stoppable=False)  # the write follows: from here on the run finishes
    if ops:
        _write(ws, page_id, new_doc, items, ops, kinds, placed, actor=actor)
    return placed


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
