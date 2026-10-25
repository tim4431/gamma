"""Paper metadata + citations.

Lookup order: arXiv API (id from the source URL, the web page's tags or the
PDF text) → DOI via doi.org content negotiation (Crossref/DataCite) → ISBN
printed in the PDF via Open Library / Google Books (books) → Crossref
bibliographic search (the web page's title and author, the PDF's largest
type, the page title, the text head) → the web page's own citation record →
AI extraction from the first pages as a last resort, itself cross-checked
against Crossref and, for books, the book registries.
A registry record found via the *text* (not the source URL) is trusted
outright only when the PDF is its title page — its title is the document's
own and one of its authors is named; one the text merely names (an id
printed in it, a title search hit whose title is a phrase of the text) goes
to the AI, which picks the one that is this document, if any. The first DOI
on page 1 can belong to a cited paper, a short registry title can be a
phrase of a longer one, and AI output can be a plausible hallucination.
Whatever is stored carries ``meta.match`` (what tied it to the document) and
``meta.unverified`` so the UI can warn before the record ends up in a
paper's bibliography. There is deliberately no Google
Scholar call — Scholar has no official API and scraping it violates its ToS.
Results are cached on the page block (properties.meta / properties.bibtex /
properties.ppt_cite — the slide citation is generated in the same fetch).
"""

import html as html_lib
import json
import os
import re
import sqlite3
import threading
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from difflib import SequenceMatcher

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from .. import ai_usage, item_types, jobs, storage
from .. import bibtex as bibtex_mod
from ..ai_client import CallRefused, call_ai as _call_ai
from ..ai_context import ensure_indexed as _ensure_indexed
from ..ai_context import pdf_excerpt as _pdf_excerpt
from ..ai_context import pdf_path as _pdf_path
from ..ai_settings import ai_runtime, require_ai_runtime
from ..auth import require_user_id, require_ws
from ..blocks_store import page_attachment, write_lock
from ..ops import StorableBody, after_commit, apply_ops, props_patch
from ..db import connect_data_db, connect_pages_db, page_now
from ..logbuf import log
from ..pdf_index import doc_chars
from ..pdf_text import PDF_EXTRACT_FAILED
from ..pdf_text import page_count as _page_count
from ..pdf_text import title_guess as _title_guess
from ..textnorm import INDEX_VERSION, normalize_text, plain_text
from .ai import CITE_PROMPT, METADATA_PROMPT, _resolve_model, _translate_effort
from .pdf import ARXIV_ID, CONTACT_EMAIL

# Guards the doc-id → filename join below (defense in depth: doc ids come from
# block properties a user can set). Mirrors gamma.db._DOC_ID_RE.
_DOC_ID_OK = re.compile(r"^[A-Za-z0-9_.-]{1,128}$")

router = APIRouter(prefix="/api", tags=["metadata"])

# How much text identifier scans and title matching read. Decoupled from the
# AI context pref: an issue-clipped Science PDF starts with the *previous*
# article's tail, and the paper's own title/DOI can sit 7k+ chars in — a
# window sized for AI cost must not starve the free regex/matching steps.
SCAN_CHARS = 20000

_ARXIV_URL_RE = re.compile(r"arxiv\.org/(?:abs|pdf|html)/(" + ARXIV_ID + ")", re.I)
_ARXIV_TEXT_RE = re.compile(r"arXiv:\s*(" + ARXIV_ID + ")", re.I)
_DOI_RE = re.compile(r"\b(10\.\d{4,9}/[^\s\"'<>]+)")
_ATOM = "{http://www.w3.org/2005/Atom}"
_ARXIV_NS = "{http://arxiv.org/schemas/atom}"

# GAMMA_METADATA_LOOKUP=off keeps the server from asking the registries
# (arXiv, doi.org, Crossref, Open Library, Google Books — an offline server,
# the browser suite): every lookup fails at once and finds nothing, instead
# of waiting out its timeout.
METADATA_LOOKUP = os.environ.get("GAMMA_METADATA_LOOKUP", "").strip().lower() not in ("0", "off", "false", "no")


def _http_get(url: str, accept: str = "", timeout: int = 20, headers: dict | None = None) -> bytes:
    if not METADATA_LOOKUP:
        raise OSError("registry lookups are switched off (GAMMA_METADATA_LOOKUP)")
    headers = {"User-Agent": "gamma-pdf-annotator/1.0 (metadata lookup)", **(headers or {})}
    if accept:
        headers["Accept"] = accept
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def _find_arxiv_id(source_url: str, text: str) -> str:
    m = _ARXIV_URL_RE.search(source_url or "") or _ARXIV_TEXT_RE.search(text or "")
    return m.group(1) if m else ""


def _doi_variants(doi: str) -> list[str]:
    """A DOI plus a trimmed variant when PDF text extraction glued the next
    word straight onto it ("…-0478-8Physics Department"): a trailing
    all-letters run after a digit yields the second, trimmed candidate."""
    doi = doi.rstrip(".,;)]}’”")
    out = [doi]
    glued = re.match(r"^(.*\d)([A-Za-z]{3,})$", doi)
    if glued and glued.group(1) not in out:
        out.append(glued.group(1))
    return out


def _find_doi_candidates(source_url: str, text: str) -> list[str]:
    """Every DOI in the source URL and text head, most likely first (URL before
    text, then reading order), each followed by its glued-suffix trim."""
    cands: list[str] = []
    for hay in (source_url or "", text or ""):
        for m in _DOI_RE.finditer(hay):
            for cand in _doi_variants(m.group(1)):
                if cand not in cands:
                    cands.append(cand)
            if len(cands) >= 8:
                return cands
    return cands


def _norm_match(s: str) -> str:
    """Case/punctuation/ligature/line-break-insensitive form for title
    matching (normalize_text folds ligatures and unwraps hyphenated breaks)."""
    return " ".join(re.findall(r"[a-z0-9]+", normalize_text(s or "").lower()))


def _title_in_text(title: str, text: str) -> bool:
    """Does this (registry) title appear in the PDF's text head, as whole
    words? Evidence that the text names the work, not that the text IS the
    work: a cited title in a reference list, a phrase of a longer title or a
    section heading pass too (``_tie`` asks for more). Short titles are too
    generic to count. A title with a subtitle ("Planck 2018 results: VI.
    Cosmological parameters") also counts when both parts appear — the PDF
    sets them on separate lines, often with other text between."""
    head = f" {_norm_match((text or '')[:SCAN_CHARS])} "
    t = _norm_match(title)
    if len(t) >= 15 and f" {t} " in head:
        return True
    main, _, sub = (title or "").partition(": ")
    main, sub = _norm_match(main), _norm_match(sub)
    return len(main) >= 15 and len(sub) >= 8 and f" {main} " in head and f" {sub} " in head


def _years_compatible(a, b) -> bool:
    ma, mb = re.search(r"\d{4}", str(a or "")), re.search(r"\d{4}", str(b or ""))
    return not ma or not mb or abs(int(ma.group()) - int(mb.group())) <= 1


def _surname(author: str) -> str:
    """Last alphabetic token of an author string ("Anthony E. Siegman" →
    "siegman"), normalized like the text it is matched against."""
    parts = re.findall(r"[a-z]+", normalize_text(author or "").lower())
    return parts[-1] if parts and len(parts[-1]) >= 3 else ""


def _fold_spaced(norm: str) -> str:
    """A normalized text with letter-spaced runs folded back into words:
    title pages letter-space the title ("L A S E R S")."""
    return re.sub(r"\b(?:[a-z0-9] ){2,}[a-z0-9]\b", lambda m: m.group().replace(" ", ""), norm)


def _record_in_text(meta: dict, text: str) -> bool:
    """Whether the text names this book: a long title in it (the paper rule),
    or — book titles are short ("Lasers") — the whole title AND one author
    surname both in the head. A sentence citing the book passes too, so this
    is a phrase-level tie (``_tie`` asks for the title page)."""
    if _title_in_text(meta.get("title", ""), text):
        return True
    head = f" {_fold_spaced(_norm_match((text or '')[:SCAN_CHARS]))} "
    t = _norm_match(meta.get("title", ""))
    if len(t) < 4 or f" {t} " not in head:
        return False
    return any(f" {s} " in head for s in map(_surname, meta.get("authors") or []) if s)


# --- What ties a record to this document ----------------------------------------
# A looked-up record is stored with ``meta.match``, what tied it to the
# document, and ``meta.unverified`` follows from it (``_unconfirmed``):
#   source     an identifier from the paper's own address, the web page it was
#              saved from, or that page's tags
#   page       the web page's citation record, or a registry record whose title
#              and year agree with it
#   typed      an identifier the user typed into the popover
#   title      the PDF is the record's title page (``_tie``): its title is the
#              document's own, and one of its authors is printed by it
#   ai         the AI tied it to the document: it picked it among the records
#              the text names, or the registry holds the title it read
#   title_only its title is the document's own, but none of its authors was
#              found by it (an author list the text mangles, a record that
#              lists none, or another work under the same title) — unconfirmed
#   phrase     a title search hit whose title only occurs in the text (a phrase
#              of a longer title, a heading, a reference) — unconfirmed
#   id_only    the record of an identifier printed in the document (its title
#              may occur there too, as a cited work's) that nothing else ties to
#              it — unconfirmed
#   ai_reading the AI read the fields off the document; no registry confirmed
#              them (flagged for the kinds a registry could hold)
# The rule that confirms without the AI (``_tie`` → "title") fires only on the
# clear title page; anything less goes to the AI or stands flagged, since a
# record wrongly confirmed is cited unchecked and one wrongly flagged is not.
MATCH_CONFIRMED = frozenset({"source", "page", "typed", "title", "ai"})
TITLE_ALIKE = 0.92    # how alike two normalized titles must be to be the same
TITLE_LINES_MAX = 3   # a title wraps over at most this many lines
TITLE_MIN = 15        # a shorter title ("Introduction") is too generic to be the document's
SUBTITLE_MIN = 8      # a shorter subtitle is no evidence of anything
AUTHORS_BEFORE, AUTHORS_AFTER = 6, 14  # lines around a title where its authors are printed
FRONT_LINES = 80      # the lines that stand for the first page when the text marks no pages
OPENING_LINES = 25    # the first page's title block: where a title line counts beside the largest type
LARGEST = "largest type"  # _Document.title_at's place for the first page's largest type
# A numbered section heading ("1 Introduction", "1.2. Results", "IV. DISCUSSION")
# opens no title, however alike its words are to a registry title. "C. elegans"
# and "100 years of …" are titles.
_HEADING_RE = re.compile(r"^\s*(?:\d+(?:\.\d+)*\.?\s+[A-Z]|[IVX]+\.\s+[A-Z]{2})")
_PAGE_TWO_RE = re.compile(r"^\[PDF page 2\b")
_NAME_SUFFIXES = frozenset({"jr", "sr", "ii", "iii", "iv"})
# pdflatex's old (OT1) fonts set an accent as a mark of its own ("M¨uller",
# "Garc´ıa"); these letters have no decomposition to fold.
_SPACING_MARKS = dict.fromkeys(map(ord, "¨´`˜^ˇ˘˚¸˛˙˝"), None)
_UNDECOMPOSED = str.maketrans({"ı": "i", "ł": "l", "Ł": "L", "ø": "o", "Ø": "O", "đ": "d", "Đ": "D",
                               "ð": "d", "Ð": "D", "þ": "th", "Þ": "Th", "ß": "ss", "æ": "ae", "Æ": "Ae",
                               "œ": "oe", "Œ": "Oe"})
# A title's part number ("… chains. I" / "… II", "Planck 2018 results. VI"):
# titles that differ in one are different works, however alike the rest.
_NUMERAL_RE = re.compile(r"^(?:\d+|[ivx]{1,4})$")


def _unconfirmed(meta: dict) -> bool:
    """``meta.unverified`` for a looked-up record: nothing confirmed tied it
    to the document. An AI reading of a kind no registry holds (notes, a
    thesis, slides) is not flagged: there is nothing a warning could ask the
    user to check."""
    match = meta.get("match")
    if match == "ai_reading":
        return item_types.item_type(meta) in item_types.CHECKABLE
    return match not in MATCH_CONFIRMED


def _alike(a: str, b: str) -> bool:
    """Two normalized titles name the same work: equal, or near-identical
    with the same part numbers."""
    if not a or not b:
        return False
    if a == b:
        return True
    if abs(len(a) - len(b)) > 0.18 * max(len(a), len(b)):  # too far apart to reach TITLE_ALIKE
        return False
    if [w for w in a.split() if _NUMERAL_RE.match(w)] != [w for w in b.split() if _NUMERAL_RE.match(w)]:
        return False
    # One title inside the other with words more around it ("comment on x",
    # "x erratum", "x retracted", "x revisited", "x notes") is another work
    # about x; spaces are dropped for the test, since a hyphen a line break
    # cut joins words ("nitrogen-\ndoped" → "nitrogendoped"). A footnote
    # mark glued to the title is a character or two.
    short, long_ = sorted((a.replace(" ", ""), b.replace(" ", "")), key=len)
    if short in long_ and len(long_) - len(short) >= 4:
        return False
    return SequenceMatcher(None, a, b).ratio() >= TITLE_ALIKE


def _fold_name(s: str, keep_case: bool = False) -> str:
    """Text as author names are matched in it: letters only, accents folded
    ("García" → "garcia", "M¨uller" → "muller", "Yıldız" → "yildiz"),
    anything else a space, so an affiliation mark glued to a name ("Ofek1*")
    comes apart from it."""
    s = normalize_text((s or "").translate(_SPACING_MARKS).translate(_UNDECOMPOSED))
    s = "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))
    return " ".join(re.findall(r"[A-Za-z]+", s if keep_case else s.lower()))


def _name_key(author: str) -> str:
    """An author's surname as names are matched: the last word of the folded
    name, a suffix (Jr., III) passed over; two letters at least (Li, Wu)."""
    words = [w for w in _fold_name(author).split() if w not in _NAME_SUFFIXES]
    return words[-1] if words and len(words[-1]) >= 2 else ""


def _norm_title(title: str) -> str:
    return _fold_spaced(_norm_match(title))


def _main_title(title: str) -> str:
    return (title or "").partition(": ")[0]


class _Document:
    """What a lookup ties records to: the PDF's text head, where its own title
    is — the first page's largest type (``guess``, pdf_text.title_guess),
    and a run of up to TITLE_LINES_MAX whole lines of the first page's title
    block that opens with no numbered section heading (the whole first page
    when there is no largest type) — and the lines around it, where a
    title's authors are printed. Lines of later pages (slides, a table of
    contents, a reference list) are no title page. Where there is a largest
    type, only it confirms (``_tie``): a title-block line can be a heading,
    a CV's or a syllabus's entry. Built once per lookup."""

    def __init__(self, text: str, guess: str = ""):
        self.text = text or ""
        lines = [line for line in self.text[:SCAN_CHARS].splitlines() if line.strip()]
        page_two = next((i for i, line in enumerate(lines) if _PAGE_TWO_RE.match(line)), None)
        self.front = lines[:page_two] if page_two is not None else lines[:FRONT_LINES]
        marker = self.text.find("\n[PDF page 2")
        # the first page's characters, what the AI's check reads at least
        self.page_one = marker if marker >= 0 else len(self.text)
        self.guess = _norm_title(guess)
        # where a title line counts: the title block, or the whole first page
        # when there is no largest type to go by (then it can confirm)
        self.title_lines = OPENING_LINES if self.guess else len(self.front)
        # (normalized run, its first line, whether that line is a numbered heading)
        self.runs = []
        for i, line in enumerate(self.front):
            heading = bool(_HEADING_RE.match(line))
            for n in range(1, TITLE_LINES_MAX + 1):
                if i + n <= len(self.front):
                    norm = _norm_title("\n".join(self.front[i:i + n]))
                    if norm:
                        self.runs.append((norm, i, heading))
        # the largest type's line: a run alike to it, or where a title wrapped
        # over more lines than a run holds begins
        found = [i for norm, i, _ in self.runs if self.guess and (
            _alike(self.guess, norm) or (len(norm) >= TITLE_MIN and self.guess.startswith(f"{norm} ")))]
        self.guess_line = found[0] if found else None
        self._windows: dict = {}

    def _lines(self, t: str, headings: bool = False) -> list[int]:
        return [i for norm, i, heading in self.runs if (headings or not heading) and _alike(t, norm)]

    def _places(self, t: str, min_len: int) -> list:
        at = [LARGEST] if self.guess and _alike(t, self.guess) else []
        if len(t) >= min_len:
            at += [i for i in self._lines(t) if i < self.title_lines]
        return at

    def title_at(self, title: str, min_len: int = TITLE_MIN) -> list:
        """Where this title is the document's own: LARGEST (the first page's
        largest type) and the first lines of the
        title block's runs it is, ``min_len`` characters at least (15: a
        shorter title, "Introduction", is too generic to tell). A title with
        a subtitle (``"main: sub"``, as doi.org joins them) is also the
        document's where its main title is and its subtitle is a line of
        its own just after it, as PDFs set them apart; a main title alone is
        a series' ("Planck 2018 results"), every volume's."""
        t = _norm_title(title)
        if not t:
            return []
        at = self._places(t, min_len)
        main, _, sub = (title or "").partition(": ")
        main, sub = _norm_title(main), _norm_title(sub)
        if at or len(sub) < SUBTITLE_MIN:
            return at
        subs = self._lines(sub, headings=True)  # a volume's subtitle is numbered ("VI. …")
        near = TITLE_LINES_MAX + 3
        found = []
        for i in self._places(main, max(min_len, TITLE_MIN)):
            start = self.guess_line if i == LARGEST else i
            if start is None:
                ok = any(j < OPENING_LINES for j in subs)
            else:
                ok = any(start < j <= start + near for j in subs)
            if ok:
                found.append(i)
        return found

    def names(self, records: list[dict], at: list) -> bool:
        """One of the records' authors is printed by the title found ``at``:
        in the lines just before and after it (the largest type's line, or
        the title block when it is not found) — not anywhere in the head,
        whose reference list prints every cited work's authors. A surname
        matches a capitalized word, as names are printed ("Lee", "LEE",
        "McDonald"; never "sense" for Sen), or a capitalized word it opens
        with up to two letters more (an affiliation letter glued to it:
        "Johnsona,b"; surnames of three letters at least)."""
        keys = {k for r in records for k in map(_name_key, r.get("authors") or []) if k}
        if not keys:
            return False
        for i in at:
            if i == LARGEST:
                i = self.guess_line
            if i not in self._windows:
                lines = (self.front[max(0, i - AUTHORS_BEFORE):i + TITLE_LINES_MAX + AUTHORS_AFTER]
                         if i is not None else self.front[:OPENING_LINES])
                self._windows[i] = [w.lower() for w in _fold_name("\n".join(lines), keep_case=True).split()
                                    if w[:1].isupper()]
            for w in self._windows[i]:
                for k in keys:
                    if w == k or (len(k) >= 3 and w.startswith(k) and len(w) - len(k) <= 2):
                        return True
        return False


def _tie(doc: _Document, record: dict, also: dict | None = None, min_len: int = TITLE_MIN) -> str:
    """How the text ties a registry record to the document:
    - "title": the PDF is its title page — its title is the first page's
      largest type (a line of the first page when there is none,
      ``title_at``) and one of its authors is printed by it;
    - "title_only": its title is the document's own, no author found by it,
      or it is a line of the title block beside a largest type it is not (a
      record that lists no authors, or in another script, never gets
      further: a supplement's component, a front-matter page and a textbook
      chapter on the topic list none);
    - "phrase": its title only occurs in the text;
    - "": neither.
    ``also``: another record of the same work (a preprint's published
    version) whose title and authors count as well. ``min_len``: the
    shortest title a line of the title block can be (an ISBN printed in
    the PDF vouches for a short book title, "Lasers")."""
    records = [r for r in (record, also) if r]
    at = [i for r in records for i in doc.title_at(r.get("title", ""), min_len)]
    if at:
        # Where there is a largest type, a title-block line only ranks the
        # record: it may be a heading naming a cited work, a CV's or a
        # syllabus's entry, with that work's authors under it.
        sure = [i for i in at if i == LARGEST or not doc.guess]
        return "title" if sure and doc.names(records, sure) else "title_only"
    if any(_title_in_text(r.get("title", ""), doc.text) for r in records):
        return "phrase"
    return ""


def _named_tie(doc: _Document, record: dict, also: dict | None = None) -> str:
    """``_tie`` for a record the lookup found, with its main title tried too
    when its subtitle ties nothing (a subtitle the PDF does not print): the
    main title being the document's makes it "title_only" at most — a series'
    main title is every volume's — and occurring in the text, "phrase"."""
    how = _tie(doc, record, also)
    if how in ("title", "title_only") or ": " not in (record.get("title") or ""):
        return how
    main = _tie(doc, dict(record, title=_main_title(record["title"])))
    return "title_only" if main in ("title", "title_only") else how or main


def _same_work(record: dict, other: dict | None, main: bool = False) -> bool:
    """A registry record and a reading of the same work (the web page's
    record, the AI's): near-identical titles and compatible years. ``main``
    compares the main titles only (a search hit carries no subtitle), a
    reason to fetch the full record, not to take it."""
    if not other:
        return False
    a, b = record.get("title", ""), other.get("title", "")
    if main:
        a, b = _main_title(a), _main_title(b)
    t = _norm_match(a)
    return len(t) >= 15 and _alike(t, _norm_match(b)) and _years_compatible(record.get("year"), other.get("year"))


# --- Books ------------------------------------------------------------------
# Papers resolve through arXiv/DOI/Crossref; books mostly don't (Crossref only
# knows DOI-registered ones, and an old textbook has neither). Two keyless
# book registries cover them: Open Library and Google Books. Entry points: an
# ISBN printed in the PDF (copyright page, back cover), or the AI-read title +
# author of a book that predates ISBNs.

_ISBN_RE = re.compile(r"ISBN(?:-1[03])?\s*[:：]?\s*([0-9][0-9Xx\s‐-―-]{8,22}[0-9Xx])", re.I)
# Shorter than the paper registries: both book APIs are optional extras on a
# path that has already tried arXiv/DOI, and Open Library can be slow or
# unreachable from some networks — a fetch must not hang on them.
_BOOK_API_TIMEOUT = 8
# Google Books' keyless quota belongs to the server's address: once it answers
# 429, asking again at once (the next paper of a batch) spends what the quota
# grants next. It rests for the Retry-After it names, else this long; a book
# record then stands on Open Library and the AI read alone.
_GOOGLE_BOOKS_REST_S = 600
_google_books_rest_until = 0.0


def _isbn_valid(digits: str) -> bool:
    if len(digits) == 10 and re.fullmatch(r"\d{9}[\dX]", digits):
        return sum((10 - i) * (10 if c == "X" else int(c)) for i, c in enumerate(digits)) % 11 == 0
    if len(digits) == 13 and digits.isdigit():
        return sum((1 if i % 2 == 0 else 3) * int(c) for i, c in enumerate(digits)) % 10 == 0
    return False


def _find_isbns(text: str) -> list[str]:
    """Checksum-valid ISBNs labelled "ISBN" in the text, reading order. Only
    labelled ones — a bare 10/13-digit run is a phone number or a DOI tail.
    Extraction glues the next number on ("ISBN 0-935702-11-3 1986"), so the
    13- (978/979-prefixed) and 10-character prefixes are both tried."""
    out: list[str] = []
    for m in _ISBN_RE.finditer(text or ""):
        digits = re.sub(r"[^0-9Xx]", "", m.group(1)).upper()
        cands = ([digits[:13]] if digits[:3] in ("978", "979") else []) + [digits[:10]]
        for cand in cands:
            if _isbn_valid(cand) and cand not in out:
                out.append(cand)
                break
        if len(out) >= 4:
            break
    return out


def _book_record(title: str, authors: list, year, publisher: str, isbns: list,
                 source: str, years: list | None = None) -> dict | None:
    title = re.sub(r"\s+", " ", str(title or "")).strip()
    if not title:
        return None
    ym = re.search(r"\d{4}", str(year or ""))
    isbns = [re.sub(r"[^0-9Xx]", "", str(i)).upper() for i in (isbns or [])]
    isbns = [i for i in isbns if _isbn_valid(i)]
    return {
        "title": title,
        "authors": [str(a).strip() for a in (authors or []) if str(a).strip()],
        "year": ym.group() if ym else "",
        "venue": "", "volume": "", "pages": "",
        "doi": "", "arxiv_id": "",
        "publisher": re.sub(r"\s+", " ", str(publisher or "")).strip(),
        # Edition-specific: only meaningful when the PDF in hand printed it
        # (the ISBN step) — search hits keep the candidates in "isbns" and
        # _pick_book_match adopts one only if the text contains it.
        "isbn": "",
        "isbns": isbns,
        "years": [str(y) for y in (years or []) if str(y).strip()],
        "kind": "book",
        "source": source,
    }


def _gb_record(info: dict, source: str) -> dict | None:
    """One Google Books volumeInfo → record. Subtitles stay separate (a
    citation wants the main title)."""
    return _book_record(
        info.get("title"), info.get("authors") or [], info.get("publishedDate"),
        info.get("publisher"),
        [i.get("identifier") for i in (info.get("industryIdentifiers") or []) if str(i.get("type", "")).startswith("ISBN")],
        source, years=[str(info.get("publishedDate") or "")[:4]])


def _google_books(query: str, what: str) -> list[dict]:
    """Google Books volumes for the ``q`` query ``query``, or [] when it fails
    or rests after a 429. ``what`` names the lookup in the log."""
    global _google_books_rest_until
    if time.monotonic() < _google_books_rest_until:
        return []
    try:
        return json.loads(_http_get("https://www.googleapis.com/books/v1/volumes?" + query,
                                    timeout=_BOOK_API_TIMEOUT)).get("items") or []
    except urllib.error.HTTPError as e:
        if e.code != 429:
            log.warning(f"[metadata] google books {what} failed: {e}")
            return []
        retry_after = str(e.headers.get("Retry-After") or "").strip()
        rest = int(retry_after) if retry_after.isdigit() else _GOOGLE_BOOKS_REST_S
        _google_books_rest_until = time.monotonic() + rest
        log.warning(f"[metadata] google books is rate-limiting this server (429): not asked for {max(1, rest // 60)} min")
    except Exception as e:
        log.warning(f"[metadata] google books {what} failed: {e}")
    return []


def _fetch_isbn(isbn: str) -> dict | None:
    """Resolve one ISBN: Open Library's books API, then Google Books."""
    try:
        data = json.loads(_http_get(
            f"https://openlibrary.org/api/books?bibkeys=ISBN:{isbn}&format=json&jscmd=data",
            timeout=_BOOK_API_TIMEOUT,
        )).get(f"ISBN:{isbn}") or {}
    except Exception as e:
        log.warning(f"[metadata] openlibrary isbn lookup failed: {e}")
        data = {}
    rec = _book_record(
        data.get("title"), [a.get("name") for a in (data.get("authors") or [])],
        data.get("publish_date"), (data.get("publishers") or [{}])[0].get("name"),
        [isbn], "isbn") if data else None
    if not rec:
        items = _google_books("q=isbn:" + isbn, "isbn lookup")
        rec = next((r for r in (_gb_record(it.get("volumeInfo") or {}, "isbn") for it in items) if r), None)
    if rec:
        rec["isbn"] = isbn
        rec.pop("isbns", None)
        rec.pop("years", None)
    return rec


def _book_search(title: str, author: str = "") -> list[dict]:
    """Title(+author) search over Open Library then Google Books, up to five
    candidates each in that order. Not trusted as-is — _pick_book_match
    decides."""
    if not (title or "").strip():
        return []
    out: list[dict] = []
    q = {"title": title[:200], "limit": "5",
         "fields": "title,author_name,first_publish_year,publish_year,publisher,isbn"}
    if author:
        q["author"] = author[:100]
    try:
        docs = json.loads(_http_get("https://openlibrary.org/search.json?" + urllib.parse.urlencode(q),
                                    timeout=_BOOK_API_TIMEOUT)).get("docs") or []
    except Exception as e:
        log.warning(f"[metadata] openlibrary search failed: {e}")
        docs = []
    for d in docs:
        rec = _book_record(d.get("title"), d.get("author_name") or [], d.get("first_publish_year"),
                           (d.get("publisher") or [""])[0], d.get("isbn") or [], "openlibrary",
                           years=d.get("publish_year") or [])
        if rec:
            out.append(rec)
    gq = f'intitle:"{title[:200]}"' + (f' inauthor:"{author[:100]}"' if author else "")
    for it in _google_books("maxResults=5&q=" + urllib.parse.quote(gq), "search"):
        rec = _gb_record(it.get("volumeInfo") or {}, "googlebooks")
        if rec:
            out.append(rec)
    return out


def _titles_alike(a: str, b: str) -> bool:
    """Normalized titles near-identical, or one is the other plus a subtitle."""
    if not a or not b:
        return False
    short, long_ = sorted((a, b), key=len)
    return a == b or SequenceMatcher(None, a, b).ratio() >= 0.9 or (len(short) >= 15 and long_.startswith(short))


def _pick_book_match(cands: list[dict], text: str, ai_meta: dict | None = None) -> dict | None:
    """Accept a book-registry hit only on strong evidence: its title matches
    the AI-read title AND an author surname agrees (both were read off the
    PDF, the registry confirms they name a real book), or the title and an
    author appear in the text (``_record_in_text``). The AI-read year wins
    over the registry's first-publication year: it names the edition in
    hand. An ISBN is adopted only when the PDF text prints it."""
    ai_title = _norm_match((ai_meta or {}).get("title", ""))
    ai_names = {s for s in map(_surname, (ai_meta or {}).get("authors") or []) if s}
    ai_year = str((ai_meta or {}).get("year") or "").strip()
    text_digits = re.sub(r"[^0-9Xx]", "", (text or "")[:SCAN_CHARS]).upper()
    passes = ([lambda c: _titles_alike(_norm_match(c["title"]), ai_title)
               and bool(ai_names & {s for s in map(_surname, c.get("authors") or []) if s})]
              if ai_title else []) + [lambda c: _record_in_text(c, text)]
    for accept in passes:
        for cand in cands:
            if not accept(cand):
                continue
            rec = {k: v for k, v in cand.items() if k not in ("isbns", "years")}
            rec["isbn"] = next((i for i in cand.get("isbns") or [] if i in text_digits), "")
            if ai_year:
                rec["year"] = ai_year
            return rec
    return None


_REGISTRY_CACHE: dict[str, dict] = {}
_REGISTRY_CACHE_MAX = 200


def registry_record(doi: str, arxiv_id: str) -> dict | None:
    """The registry record behind an identifier: arXiv first (its record
    carries the published DOI too), then doi.org. None when neither answers.
    Hits are cached in memory (the data is public); a registry timeout is
    transient and never remembered. Serves the extension's preview."""
    cache_key = f"arxiv:{arxiv_id}" if arxiv_id else f"doi:{doi}"
    if cache_key in _REGISTRY_CACHE:
        return _REGISTRY_CACHE[cache_key]
    meta = _fetch_arxiv(arxiv_id) if arxiv_id else None
    if not meta and doi:
        meta, _ = _fetch_doi(doi, with_bibtex=False)
    if meta:
        if len(_REGISTRY_CACHE) >= _REGISTRY_CACHE_MAX:
            _REGISTRY_CACHE.pop(next(iter(_REGISTRY_CACHE)))
        _REGISTRY_CACHE[cache_key] = meta
    return meta


_ABSTRACT_MAX = 4000  # chars of a search record's abstract kept


def plain_abstract(markup) -> str:
    """A registry abstract as plain text: Crossref's JATS tags and a leading
    "Abstract" heading dropped, entities unescaped, whitespace collapsed."""
    text = html_lib.unescape(re.sub(r"<[^>]+>", " ", str(markup or "")))
    text = re.sub(r"\s+", " ", text).strip()
    return re.sub(r"^abstract[:.]?\s+", "", text, flags=re.I)[:_ABSTRACT_MAX]


def _arxiv_entry_meta(entry, arxiv_id: str = "", detail: bool = False) -> dict | None:
    """One Atom entry of the arXiv API as a meta dict (None for the API's
    "Error" entry). arxiv_id defaults to the entry's own id, version
    stripped. ``detail`` adds the ``abstract`` a search result shows (the
    metadata lookup stores the record, so it asks without)."""
    title = re.sub(r"\s+", " ", entry.findtext(f"{_ATOM}title") or "").strip()
    if not title or title.lower() == "error":
        return None
    if not arxiv_id:
        m = re.search(r"arxiv\.org/abs/(.+?)(?:v\d+)?$", (entry.findtext(f"{_ATOM}id") or "").strip())
        arxiv_id = m.group(1) if m else ""
    authors = [
        (a.findtext(f"{_ATOM}name") or "").strip()
        for a in entry.findall(f"{_ATOM}author")
    ]
    journal_ref = (entry.findtext(f"{_ARXIV_NS}journal_ref") or "").strip()
    meta = {
        "title": title,
        "authors": [a for a in authors if a],
        "year": (entry.findtext(f"{_ATOM}published") or "")[:4],
        "venue": journal_ref or f"arXiv:{arxiv_id}",
        "volume": "",
        "pages": "",
        "doi": (entry.findtext(f"{_ARXIV_NS}doi") or "").strip(),
        "arxiv_id": arxiv_id,
        # A journal reference says the paper was published; without a DOI
        # to follow (_published_record) it is all the record has.
        "kind": "journalArticle" if journal_ref else "preprint",
        "source": "arxiv",
    }
    if detail:
        meta["abstract"] = plain_abstract(entry.findtext(f"{_ATOM}summary"))
    return meta


def _fetch_arxiv(arxiv_id: str) -> dict | None:
    try:
        raw = _http_get(f"https://export.arxiv.org/api/query?id_list={urllib.parse.quote(arxiv_id)}")
        entry = ET.fromstring(raw).find(f"{_ATOM}entry")
        if entry is None:
            return None
        return _arxiv_entry_meta(entry, arxiv_id)
    except Exception as e:
        log.warning(f"[metadata] arxiv lookup failed: {e}")
        return None


# A descriptive query ANDs every word, so past this many words a query that
# found nothing is asked again with its longest ones (the distinctive terms).
_ARXIV_RELAX_WORDS = 5


def _arxiv_query(search: str, rows: int, detail: bool) -> list[dict]:
    url = ("https://export.arxiv.org/api/query?max_results=%d&search_query=" % rows
           + urllib.parse.quote(search))
    try:
        entries = ET.fromstring(_http_get(url)).findall(f"{_ATOM}entry")
    except Exception as e:
        log.warning(f"[metadata] arxiv search failed: {e}")
        return []
    return [meta for meta in (_arxiv_entry_meta(e, detail=detail) for e in entries) if meta]


def _arxiv_search(query: str, rows: int = 5, detail: bool = False) -> list[dict]:
    """Title-phrase OR full-record search, in arXiv's relevance order.

    An all-fields AND alone can miss an exact title containing stopwords
    (e.g. Bahdanau's "... by Jointly Learning to Align and Translate").
    The phrase branch keeps those citations findable in the same request.
    A long descriptive query that matches nothing ("Raman sideband cooling
    density limit in a 785 nm lattice") is asked once more with only its
    longest words, so it degrades to near matches instead of nothing.
    """
    tokens = re.findall(r"[\w-]+", query or "")[:12]
    words = [w for w in tokens if len(w) > 1]
    if not words:
        return []
    search = 'ti:"' + " ".join(tokens) + '" OR (' + " AND ".join(f"all:{w}" for w in words) + ")"
    found = _arxiv_query(search, rows, detail)
    distinct = list(dict.fromkeys(w.lower() for w in words))
    if found or len(distinct) <= _ARXIV_RELAX_WORDS:
        return found
    longest = sorted(distinct, key=len, reverse=True)[:_ARXIV_RELAX_WORDS]
    return _arxiv_query(" AND ".join(f"all:{w}" for w in longest), rows, detail)


# The item types whose citation names a publisher (a book's, a conference
# proceedings', a thesis's university, a report's institution); doi.org's
# publisher is kept on these and dropped on an article, which cites a journal.
_PUBLISHED_BY = ("book", "bookSection", "conferencePaper", "thesis", "report")


def _csl_authors(entries) -> list[str]:
    """Author names from a CSL JSON author list: "given family", or the name
    an organisation goes by (Crossref's ``name``, CSL's ``literal`` — the
    Planck Collaboration is such a first author), blanks dropped."""
    out = []
    for a in entries or []:
        if not isinstance(a, dict):
            continue
        name = (" ".join(filter(None, [a.get("given"), a.get("family")])).strip()
                or str(a.get("literal") or a.get("name") or ""))
        name = plain_text(name)
        if name:
            out.append(name)
    return out


def _csl_year(obj: dict) -> str:
    """The year of a CSL ``issued`` date, "" when the registry has none."""
    parts = (obj.get("issued") or {}).get("date-parts") or []
    first = parts[0] if parts and isinstance(parts[0], list) else []
    return str(first[0]) if first and first[0] else ""


def _csl_pages(obj: dict) -> str:
    """The pages of a CSL record, or its article number: APS, IOP and other
    journals that number articles instead of paging them (Phys. Rev. A 78,
    052101) carry the number as ``article-number`` and no ``page``."""
    return str(obj.get("page") or obj.get("article-number") or "")


def _csl_text(value) -> str:
    """A CSL title/container-title (a string, or a one-element list in the
    REST API) as plain text — Crossref keeps the publisher's markup and
    entities (``<i>``, ``<sub>``, ``&amp;``), which neither the UI nor the
    text match wants."""
    if isinstance(value, list):
        value = value[0] if value else ""
    return plain_text(value)


def _fetch_doi(doi: str, with_bibtex: bool = True) -> tuple[dict | None, str]:
    """Metadata via doi.org content negotiation (works for Crossref and DataCite),
    plus the registrar's own BibTeX rendering (a second round trip — skipped
    by callers that only want the record, e.g. the extension's preview). The
    registry's work type becomes the record's ``kind`` (``item_types``), and
    the publisher is kept for the kinds whose citation names one."""
    url = f"https://doi.org/{urllib.parse.quote(doi)}"
    try:
        data = json.loads(_http_get(url, accept="application/vnd.citationstyles.csl+json"))
    except Exception as e:
        # A 404 is "no such DOI" — routine when a paper's text, or a model,
        # names one that does not exist; the caller answers "not found"
        # either way. Anything else (doi.org down, a timeout) is worth an
        # admin's attention in the warnings filter.
        if getattr(e, "code", 0) == 404:
            log.info(f"[metadata] no DOI record for {doi}")
        else:
            log.warning(f"[metadata] doi lookup failed: {e}")
        return None, ""
    title = _csl_text(data.get("title"))
    if not title:
        return None, ""
    # Crossref keeps a subtitle apart ("Planck 2018 results" / "VI.
    # Cosmological parameters"); the citation needs both.
    subtitle = _csl_text(data.get("subtitle"))
    if subtitle and subtitle.lower() not in title.lower():
        title = f"{title}: {subtitle}"
    meta = {
        "title": title,
        "authors": _csl_authors(data.get("author")),
        "year": _csl_year(data),
        "venue": _csl_text(data.get("container-title")),
        "volume": str(data.get("volume") or ""),
        "pages": _csl_pages(data),
        "doi": doi,
        "arxiv_id": "",
        "source": "doi",
    }
    kind = item_types.from_registry(data.get("type"))
    if kind:
        meta["kind"] = kind
    if kind in _PUBLISHED_BY and data.get("publisher"):
        meta["publisher"] = plain_text(data.get("publisher"))
    if kind in ("book", "bookSection"):
        isbns = data.get("ISBN") or []
        meta["isbn"] = str(isbns[0] if isinstance(isbns, list) and isbns else "")
    bibtex = ""
    if with_bibtex:
        try:
            bibtex = _http_get(url, accept="application/x-bibtex").decode("utf-8", "replace").strip()
        except Exception:
            pass
    # doi.org prints Crossref's markup, entities and MathML into its BibTeX as
    # they are ("Astronomy &amp; Astrophysics", "CsPbCl<sub>3</sub>"), writes
    # months no style defines (Sept, July) and leaves out an APS article
    # number: stored as valid BibTeX, the number taken from the record.
    return meta, bibtex_mod.normalize_entry(bibtex, meta)


def _published_record(meta: dict) -> tuple[dict, str]:
    """An arXiv record as a bibliography should cite it. When arXiv names the
    DOI of the published version, the registrar's record (venue, volume,
    pages, its own BibTeX) replaces the preprint's: arXiv's ``journal_ref``
    is one free-text line ("Phys. Rev. Lett. 116, 061102 (2016)") that
    would otherwise be typeset as the journal's name. The arXiv id stays on
    the record, so the entry keeps its ``eprint`` and the popover its arXiv
    row. Taken only when the registrar's title is the preprint's (authors
    do mistype the DOI), never for arXiv's own DataCite DOIs, and the
    preprint record stands when doi.org has nothing."""
    doi = str(meta.get("doi") or "").strip()
    if not doi or doi.lower().startswith("10.48550/"):
        return meta, ""
    better, bib = _fetch_doi(doi)
    if not better:
        return meta, ""
    # The registrar's title may carry a subtitle the preprint's does not.
    mine = _norm_match(meta.get("title", ""))
    theirs = [_norm_match(t) for t in (better.get("title", ""), better.get("title", "").partition(": ")[0])]
    if not mine or max(SequenceMatcher(None, mine, t).ratio() for t in theirs) < 0.9:
        log.info(f"[metadata] arXiv {meta.get('arxiv_id')} names DOI {doi} of a different title; keeping the preprint record")
        return meta, ""
    return {**better, "arxiv_id": meta.get("arxiv_id", "")}, bib


def _crossref_search(query: str, rows: int = 5, detail: bool = False, from_year: int = 0,
                     work_type: str = "") -> list[dict]:
    """Bibliographic search against the Crossref REST API, returning candidate
    meta dicts in Crossref's relevance order. Candidates are NOT trusted as-is
    — the lookup ties them to the document (``_tie``, ``_same_work``), on
    their doi.org record, whose title carries the subtitle. ``detail``
    adds the ``abstract`` and ``cited_by`` count a search result shows;
    ``from_year`` keeps works published that year or later, and ``work_type``
    (a Crossref type such as ``journal-article``) keeps that kind only."""
    if not (query or "").strip():
        return []
    fields = "DOI,type,title,author,container-title,volume,page,article-number,issued" + (
        ",abstract,is-referenced-by-count" if detail else "")
    narrow = ([f"from-pub-date:{int(from_year)}"] if from_year else []) + (
        [f"type:{work_type}"] if work_type else [])
    url = ("https://api.crossref.org/works?rows=%d" % rows
           + "&select=" + fields
           + (("&filter=" + ",".join(narrow)) if narrow else "")
           + "&mailto=" + urllib.parse.quote(CONTACT_EMAIL)
           + "&query.bibliographic=" + urllib.parse.quote(query[:400]))
    try:
        items = json.loads(_http_get(url)).get("message", {}).get("items", [])
    except Exception as e:
        log.warning(f"[metadata] crossref search failed: {e}")
        return []
    out = []
    for it in items:
        title = _csl_text(it.get("title"))
        # A supplement's file, a peer review or a grant shares the work's
        # title but is never the document's own record.
        if not title or it.get("type") in ("component", "peer-review", "grant"):
            continue
        out.append({
            "title": title,
            "authors": _csl_authors(it.get("author")),
            "year": _csl_year(it),
            "venue": _csl_text(it.get("container-title")),
            "volume": str(it.get("volume") or ""),
            "pages": _csl_pages(it),
            "doi": str(it.get("DOI") or ""),
            "arxiv_id": "",
            "source": "crossref",
        })
        kind = item_types.from_registry(it.get("type"))
        if kind:
            out[-1]["kind"] = kind
        if detail:
            out[-1].update(abstract=plain_abstract(it.get("abstract")),
                           cited_by=it.get("is-referenced-by-count"))
    return out


def _verify_ai_meta(meta: dict, text: str, hints: str = "", doc: _Document | None = None,
                    fetch_doi=None, rejected: frozenset = frozenset()) -> tuple[dict, str, str]:
    """AI extraction is a last resort and hallucinates plausible records
    (blended author lists, wrong volumes, fabricated DOIs — the prompt's
    "never invent" is not enforcement). Upgrade to the authoritative registry
    record when an identifier the AI produced resolves to the work it read
    (the registry's title is the AI-read title, or the text is the record's
    title page); otherwise cross-check the AI title against a Crossref
    search, accepting a hit on the same evidence. An identifier that resolves
    to another work is dropped, and so is one that resolves nowhere and
    doesn't occur in the PDF text — a missing DOI is recoverable, a
    fabricated or cited one poisons BibTeX. → ``(record, bibtex, match)``,
    ``match`` as in ``MATCH_CONFIRMED``'s table: "title", "ai", or
    "ai_reading" when the AI's reading stands. ``doc``: the lookup's
    document, built from ``text`` when not given; ``fetch_doi``: the
    lookup's doi.org fetch, which answers what it fetched already;
    ``rejected``: the DOIs of the records the AI's check said are not this
    document, which this check does not take back."""
    doc = doc or _Document(text)
    fetch_doi = fetch_doi or _fetch_doi
    hay = ((hints or "") + "\n" + (text or "")[:SCAN_CHARS + 4000]).lower()

    def tied(record, also=None) -> str:
        if _tie(doc, record, also) == "title":
            return "title"
        return "ai" if any(_same_work(r, meta) for r in (record, also) if r) else ""

    if meta.get("arxiv_id"):
        found = _fetch_arxiv(meta["arxiv_id"])
        if found:
            published, bib = _published_record(found)
            how = "" if str(published.get("doi") or "").lower() in rejected else tied(found, published)
            if how:
                return published, bib, how
            log.info(f"[metadata] dropping AI arXiv id {meta['arxiv_id']!r}: it names another work")
            meta["arxiv_id"] = ""
        elif meta["arxiv_id"].lower() not in hay:
            log.info(f"[metadata] dropping unresolvable AI arXiv id {meta['arxiv_id']!r}")
            meta["arxiv_id"] = ""
    if meta.get("doi"):
        other = False
        for doi in _doi_variants(meta["doi"]):
            better, bib = fetch_doi(doi)
            if better:
                how = "" if doi.lower() in rejected else tied(better)
                if how:
                    return better, bib, how
                other = True
        if other:
            log.info(f"[metadata] dropping AI DOI {meta['doi']!r}: it names another work")
            meta["doi"] = ""
        elif meta["doi"].lower() not in hay:
            log.info(f"[metadata] dropping unresolvable AI DOI {meta['doi']!r}")
            meta["doi"] = ""
    # A search hit carries no subtitle: one whose main title is the AI's
    # is tied on its doi.org record (a few at most), so a subtitled paper is
    # found and a series title takes no other volume. Without that record
    # (doi.org failing) only the AI's own title can tie a hit.
    query = " ".join(filter(None, [meta.get("title"), *(meta.get("authors") or [])[:3],
                                   meta.get("year")]))
    fetched = 0
    for cand in _crossref_search(query):
        doi = str(cand.get("doi") or "")
        if not doi or doi.lower() in rejected or fetched >= AI_CHECK_FETCHES or not _same_work(cand, meta, main=True):
            continue
        fetched += 1
        better, bib = fetch_doi(doi)
        how = tied(better) if better else ("ai" if _same_work(cand, meta) else "")
        if how:
            return better or cand, bib, how
    if item_types.item_type(meta) in ("book", "document"):
        # Crossref doesn't know most books. The AI read a title and an author
        # off the title page — let the book registries confirm they name a
        # real book (and supply publisher, canonical title, ISBN). A
        # "document" is asked too: the AI files a book it is unsure of there,
        # and _pick_book_match accepts nothing short of a title-and-author
        # match.
        first_author = (meta.get("authors") or [""])[0]
        cand = _pick_book_match(_book_search(meta.get("title", ""), first_author), text, ai_meta=meta)
        if cand:
            return cand, "", ("title" if _tie(doc, cand) == "title" else "ai")
    return meta, "", "ai_reading"


def _ai_extract_meta(text: str, prompt: str, model: str, rt: dict) -> dict | None:
    system = (prompt or METADATA_PROMPT).strip()[:4000]
    try:
        entry = _resolve_model(rt, model)
        raw = _call_ai(
            [{"role": "user", "content": f"First pages of the paper:\n\n{text}"}],
            # Generous cap: reasoning models spend invisible tokens before the JSON
            system, entry, rt, max_tokens=8000, timeout=120,
            on_usage=ai_usage.recorder("metadata", entry, rt),
        )
        m = re.search(r"\{[\s\S]*\}", raw)
        if not m:
            return None
        data = json.loads(m.group(0))
    except CallRefused:
        # Not "nothing found" (a used-up allowance, too many calls at once):
        # the lookup answers 429 and leaves no negative cache, so it runs
        # again later.
        raise
    except Exception as e:
        log.warning(f"[metadata] AI extraction failed: {e}")
        return None
    if not isinstance(data, dict) or not str(data.get("title") or "").strip():
        return None
    authors = data.get("authors") or []
    if isinstance(authors, str):
        authors = [a.strip() for a in re.split(r",| and ", authors) if a.strip()]
    elif not isinstance(authors, list):
        authors = []
    meta = {
        "title": str(data.get("title") or "").strip(),
        "authors": [str(a).strip() for a in authors if str(a).strip()],
        "year": str(data.get("year") or "").strip(),
        "venue": str(data.get("venue") or "").strip(),
        "volume": str(data.get("volume") or "").strip(),
        "pages": str(data.get("pages") or "").strip(),
        "doi": str(data.get("doi") or "").strip(),
        "arxiv_id": str(data.get("arxiv_id") or "").strip(),
        "publisher": str(data.get("publisher") or "").strip(),
        "isbn": re.sub(r"[^0-9Xx]", "", str(data.get("isbn") or "")).upper(),
        "source": "ai",
    }
    # A customised prompt may still answer the old kinds ("paper", "notes");
    # an answer naming no type is read from the record's shape.
    meta["kind"] = item_types.normalize(data.get("kind")) or item_types.infer(meta)
    return meta


# The AI's check of the records a document's text names (``_ai_pick``). Not
# user-editable: the lookup depends on the shape of its answer.
MATCH_PROMPT = (
    "You check which bibliographic record, if any, describes the document whose opening text "
    "you are given. The document may be a published paper, a preprint, a book, a thesis, a "
    "report, lecture notes or someone's own manuscript. A record describes it only when it is "
    "the same work: the same title and authors, as this version or another one of it (a "
    "preprint, the published article, the paper a supplement belongs to). A work the document "
    "cites or discusses, or one that shares its topic or a phrase of its title, does not. "
    "The opening text may start with the end of the previous article in the same issue. "
    'Reply with ONLY a JSON object (no code fences): {"record": the number of the record that '
    'describes the document, or 0 when none does, "reason": one short sentence}.'
)
AI_CHECK_FETCHES = 3     # search hits _verify_ai_meta fetches from doi.org to tie on their full record
NAMED_MAX = 5            # the records the text names that a lookup gathers and the AI is shown
AI_PICK_FAILED = -1      # _ai_pick could not ask, or got no answer


def _record_lines(n: int, record: dict) -> str:
    """One record as the AI sees it in ``_ai_pick``."""
    authors = record.get("authors") or []
    rows = [f"{n}. Title: {record.get('title', '')}",
            "   Authors: " + (", ".join(authors[:8]) + (f" and {len(authors) - 8} more" if len(authors) > 8 else "")
                              if authors else "(none listed)")]
    for label, key in (("Year", "year"), ("Type", "kind"), ("Published in", "venue"),
                       ("Publisher", "publisher"), ("DOI", "doi"), ("arXiv", "arxiv_id"), ("ISBN", "isbn")):
        if str(record.get(key) or "").strip():
            rows.append(f"   {label}: {record[key]}")
    return "\n".join(rows)


def _lowest_effort(rt: dict, entry) -> str:
    """The model's lowest reasoning effort ("" when unknown): a yes/no check
    needs no thinking."""
    try:
        return _translate_effort("", rt, entry)
    except Exception:  # noqa: BLE001 — an unknown model just takes its default
        return ""


def _answer(raw: str):
    """The JSON object a model's reply carries that names a ``record``: the
    last one, wherever it sits (reasoning text, braces of its own and code
    fences around it are passed over); None when there is none."""
    decoder, found = json.JSONDecoder(), None
    for m in re.finditer(r"\{", raw or ""):
        try:
            value, _ = decoder.raw_decode(raw, m.start())
        except ValueError:
            continue
        if isinstance(value, dict) and "record" in value:
            found = value
    return found


def _picked(answer) -> int | None:
    """The record number an ``_ai_pick`` answer names: 0 for none (0, null,
    "none", ""), None when it names nothing readable."""
    if not isinstance(answer, dict) or "record" not in answer:
        return None
    value = answer["record"]
    if value is None or str(value).strip().lower() in ("", "0", "none", "null", "no", "false"):
        return 0
    try:
        return int(float(str(value).strip()))
    except (ValueError, OverflowError):
        return None


def _ai_pick(text: str, records: list[dict], rt: dict, model: str = "") -> int | None:
    """Ask the AI which of ``records`` — records the document's text names
    without tying them to it — describes the document, whose opening
    ``text`` the caller sizes: its index, None when
    the AI says none does, AI_PICK_FAILED when it could not be asked (a
    refused call, a used-up allowance, a provider error, no answer). One
    call, the metadata model at its lowest effort, counted as metadata
    usage."""
    listing = "\n".join(_record_lines(i + 1, r) for i, r in enumerate(records))
    try:
        entry = _resolve_model(rt, model)
        raw = _call_ai(
            [{"role": "user", "content": f"Opening text of the document:\n\n{text}"
                                         f"\n\nRecords:\n{listing}"}],
            MATCH_PROMPT, entry, rt, effort=_lowest_effort(rt, entry),
            # headroom for a reasoning model's invisible tokens before the JSON
            max_tokens=4000, timeout=60, on_usage=ai_usage.recorder("metadata", entry, rt),
        )
        answer = _answer(raw)
    except Exception as e:  # noqa: BLE001 — CallRefused too: the records stand, flagged
        log.warning(f"[metadata] AI record check failed: {e}")
        return AI_PICK_FAILED
    n = _picked(answer)
    if n is None:
        log.warning("[metadata] AI record check gave no answer")
        return AI_PICK_FAILED
    reason = str(answer.get("reason") or "")[:200]
    if 1 <= n <= len(records):
        log.info(f"[metadata] AI matched record {n} of {len(records)}: {reason}")
        return n - 1
    log.info(f"[metadata] AI matched none of {len(records)} records: {reason}")
    return None


def _make_ppt_cite(rt: dict, meta: dict | None, bibtex: str, prompt: str = "", model: str = "") -> str:
    """The minimal slide-deck citation, one AI call over the BibTeX (else the
    meta JSON). Shared by the metadata fetch (generated alongside the record)
    and POST /metadata/cite (regenerate)."""
    system = (prompt or CITE_PROMPT).strip()[:4000]
    source = bibtex or json.dumps(meta, indent=2)
    entry = _resolve_model(rt, model)
    return _call_ai([{"role": "user", "content": source}], system, entry, rt,
                    max_tokens=4000, timeout=120,
                    on_usage=ai_usage.recorder("cite", entry, rt)).strip()


def _load_page(ws: str, block_id: str):
    with connect_pages_db(ws) as conn:
        row = conn.execute(
            "SELECT content, properties FROM unified_blocks WHERE id = ? AND parent_id = 'root'", (block_id,)
        ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="page not found")
    return row[0] or "", json.loads(row[1] or "{}")


def _save_props(ws: str, block_id: str, updates: dict | None = None, remove: tuple = (),
                auto_title: str = "", actor: str = "") -> tuple[bool, str]:
    """Apply a delta to the page's properties, re-reading them inside the write.

    Lookups take seconds to minutes, and the user can label the page (which
    merges into properties via PUT /api/blocks/{id}) at any point during one.
    Writing back the dict we read before the lookup would silently drop that
    label, so only the keys metadata owns are touched here.

    Returns ``(renamed, title)``: whether THIS write replaced the automatic
    title, and the page's title as it stands after the write — which may
    already be the paper's, renamed by a concurrent lookup (the extension's
    background lookup races the one the app starts when the page opens)."""
    with connect_pages_db(ws) as conn:
        # Serialize the read/merge/write. Whichever wins the lock first is
        # safe: a later explicit rename wins after this commit, while a rename
        # that committed first is observed with auto_title already cleared.
        write_lock(conn)
        row = conn.execute(
            "SELECT content, properties FROM unified_blocks WHERE id = ? AND parent_id = 'root'", (block_id,)
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="page not found")
        content = row[0] or ""
        props = json.loads(row[1] or "{}")
        props.update(updates or {})
        for key in remove:
            props.pop(key, None)
        # Rename only while the current title still matches the server-side
        # automatic-title marker (an explicit PUT title edit clears auto_title
        # in blocks.py; pages from before the marker existed were given one by
        # gamma/normalize.py).
        rename = bool(auto_title and props.get("auto_title")
                      and props.get("auto_title") == content)
        if rename:
            props.pop("auto_title", None)
        op = {"op": "set", "id": block_id,
              "props": props_patch(json.loads(row[1] or "{}"), props)}
        if rename:
            op["content"] = auto_title
        # client "meta": the batch is the metadata worker's, not a person's
        # keystrokes — an activity view or a sync can tell them apart.
        after_commit(ws, conn, apply_ops(conn, block_id, [op], actor=actor, client="meta"))
        return rename, (auto_title if rename else content)


@router.get("/metadata/status")
def metadata_status(request: Request):
    """Library-wide health for the Settings "Paper metadata" pane: which pages
    have metadata, which yielded extractable text, and whether the search index
    covers them. Listed: every page with a PDF attachment, plus pages that carry
    ``properties.meta`` without one (a note about a paper whose PDF you don't
    own — it still cites; ``has_file`` is false, index fields stay unknown).
    Text/index state comes from the FTS index (data.db) — pages=0 rows are
    recorded extraction failures, ver != INDEX_VERSION means stale; papers the
    index never saw report text_chars = null (unknown until indexed)."""
    ws = require_ws(request)
    with connect_pages_db(ws) as conn:
        rows = conn.execute(
            "SELECT id, content, properties, updated_at FROM unified_blocks WHERE parent_id = 'root'"
        ).fetchall()
    index = {}
    try:
        with connect_data_db(ws) as conn:
            chars = doc_chars(conn)
            for doc_id, ver, pages in conn.execute("SELECT doc_id, ver, pages FROM pdf_fts_docs"):
                index[doc_id] = {"ver": ver, "pages": pages or 0, "chars": chars.get(doc_id) or 0}
    except sqlite3.OperationalError:
        pass  # index tables don't exist yet — search has never run
    stored = {name for name, _, _ in storage.list(ws)}  # one listing, not a lookup per paper
    papers = []
    for block_id, content, props_json, updated_at in rows:
        props = json.loads(props_json or "{}")
        attachment = page_attachment(props)
        if not attachment and not props.get("meta"):
            continue  # a plain note page: nothing to fetch metadata or text for
        doc_id = attachment["id"] if attachment else ""
        entry = index.get(doc_id)
        meta = props.get("meta") or None
        papers.append({
            "id": block_id,
            "title": (meta or {}).get("title") or content or "Untitled",
            "updated_at": updated_at,
            "doc_id": doc_id,
            "has_file": bool(doc_id and _DOC_ID_OK.match(doc_id) and f"{doc_id}.pdf" in stored),
            "has_meta": bool(meta),
            "meta_source": (meta or {}).get("source", ""),
            "meta_kind": (meta or {}).get("kind", ""),
            # None for records stored before the flag existed — the client
            # falls back to the source/kind rule (isUnverifiedPaperMeta).
            "meta_unverified": (meta or {}).get("unverified"),
            # What tied the record to the document ("" for records from
            # before ``match``, hand edits and imports).
            "meta_match": (meta or {}).get("match", ""),
            "meta_user_verified": bool((meta or {}).get("user_verified")),
            "meta_error": (props.get("meta_error") or {}).get("detail", ""),
            # When the record was looked up or edited ("" for records from
            # before the stamp, or imported), and when a lookup last failed.
            "meta_at": (meta or {}).get("at", ""),
            "meta_error_at": (props.get("meta_error") or {}).get("at", ""),
            "indexed": bool(entry and entry["ver"] == INDEX_VERSION),
            "index_stale": bool(entry and entry["ver"] != INDEX_VERSION),
            "text_chars": entry["chars"] if entry else None,
        })
    return {"papers": papers}


# --- What the web page said ---------------------------------------------------
# The Connector reads the page a paper is saved from (extension/detect.js,
# pageRecord.js): the identifiers in its own tags (citation_doi,
# citation_arxiv_id, a DOI in its address), a DOI it only found in its text,
# and its citation record, the Highwire citation_* tags (Dublin Core where
# those are missing) that Google Scholar and Zotero's Embedded Metadata
# translator read. The clip keeps them on the page as
# ``properties.web_meta`` (``web_record``), so every lookup of the page
# reads them: the clip's own, the one the app starts when the page is
# opened (which used to run beside it without the identifiers), a refetch.

_WEB_FIELDS = ("title", "year", "venue", "volume", "pages", "publisher", "isbn")
_WEB_AUTHORS_MAX = 100


def web_record(record: dict | None, doi: str = "", arxiv_id: str = "", text_doi: str = "") -> dict:
    """``properties.web_meta`` for a clip: the page's citation ``record``
    cleaned (plain strings of bounded length, at most 100 authors, a
    four-digit year, a checksum-valid ISBN, an item type by our names), the
    identifiers its tags carried (``doi``, ``arxiv_id``, normalized by the
    caller) and a DOI found only in its text (``text_doi``). {} when there
    is nothing to keep."""
    record = record if isinstance(record, dict) else {}
    out = {}
    for key in _WEB_FIELDS:
        value = re.sub(r"\s+", " ", str(record.get(key) or "")).strip()[:500]
        if value:
            out[key] = value
    year = re.search(r"\d{4}", out.pop("year", ""))
    if year:
        out["year"] = year.group()
    isbn = re.sub(r"[^0-9Xx]", "", out.pop("isbn", "")).upper()
    if _isbn_valid(isbn):
        out["isbn"] = isbn
    authors = record.get("authors") if isinstance(record.get("authors"), list) else []
    names = [re.sub(r"\s+", " ", str(a)).strip()[:200] for a in authors[:_WEB_AUTHORS_MAX]]
    if any(names):
        out["authors"] = [n for n in names if n]
    kind = item_types.normalize(record.get("kind"))
    if kind:
        out["kind"] = kind
    for key, value in (("doi", doi), ("arxiv_id", arxiv_id)):
        if value:
            out[key] = value
    if text_doi and text_doi.lower() != (doi or "").lower():
        out["text_doi"] = text_doi
    return out


def _page_record(web: dict) -> dict | None:
    """The web page's citation record as a metadata record (``source``
    ``page``): None unless it names a title and an author, the least a
    citation needs. The fields are the popover's; the identifiers are the
    ones the page's tags carried."""
    title = str(web.get("title") or "").strip()
    authors = [str(a).strip() for a in (web.get("authors") or []) if str(a).strip()] \
        if isinstance(web.get("authors"), list) else []
    if not title or not authors:
        return None
    rec = {"title": title, "authors": authors, "source": "page"}
    for key in ("year", "venue", "volume", "pages", "doi", "arxiv_id", "publisher", "isbn"):
        rec[key] = str(web.get(key) or "").strip()
    kind = item_types.normalize(web.get("kind"))
    if kind:
        rec["kind"] = kind
    return rec


def web_names_a_work(web) -> bool:
    """Whether what a web page said (``web_meta``) names a work a lookup can
    find without a PDF: an identifier from its tags, a DOI from its text,
    or a citation record with a title and an author."""
    web = web if isinstance(web, dict) else {}
    return bool(web.get("doi") or web.get("arxiv_id") or web.get("text_doi") or _page_record(web))


class MetaFetchRequest(BaseModel):
    block_id: str
    prompt: str = ""   # custom AI metadata-extraction prompt (empty = built-in)
    model: str = ""
    force: bool = False
    context_char_limit: int = Field(default=6000, ge=100, le=1_000_000)
    cite_prompt: str = ""  # custom PPT-citation prompt for the citation generated alongside
    cite_model: str = ""


# Sync endpoints: external lookups + PyPDF2 text extraction run in the threadpool.
@router.post("/metadata/fetch")
def metadata_fetch(payload: MetaFetchRequest, request: Request):
    ws = require_ws(request, write=True)
    return fetch_page_metadata(ws, payload.block_id, request.state.user_id, prompt=payload.prompt, model=payload.model,
                               force=payload.force, context_char_limit=payload.context_char_limit,
                               cite_prompt=payload.cite_prompt, cite_model=payload.cite_model)


def fetch_page_metadata(ws: str, block_id: str, actor: str, prompt: str = "", model: str = "",
                        force: bool = False, context_char_limit: int = 6000,
                        cite_prompt: str = "", cite_model: str = "") -> dict:
    """The lookup behind POST /api/metadata/fetch, callable off-request (the
    extension's /api/clip runs it in a background thread). ``actor`` is the
    id of the account on whose behalf it runs — its AI providers do the AI
    part, the op is logged under it. What the web page the paper was saved
    from said about it (``properties.web_meta``, ``web_record``) is read
    from the page, so this lookup and the one the app starts when the page
    is opened agree: its identifiers are trusted like URL-derived ones, a
    DOI only found in its text is a candidate the PDF must confirm, and its
    citation record stands when no registry confirms anything.
    A successful lookup also generates the slide citation (when AI is
    configured) so it is ready the moment the metadata is — not the first
    time someone opens the share popover. Raises HTTPException(404) when
    nothing was found (after negative-caching it)."""
    content, props = _load_page(ws, block_id)
    if props.get("meta") and not force:
        return {"meta": props["meta"], "bibtex": bibtex_mod.page_entry(props),
                "ppt_cite": props.get("ppt_cite", ""), "cite_key": props.get("cite_key", ""),
                "source": props["meta"].get("source", ""), "cached": True,
                "title_updated": False, "page_title": content}

    doc_id = props.get("doc_id") or ""
    source_url = props.get("source_url") or ""
    web = props.get("web_meta") if isinstance(props.get("web_meta"), dict) else {}
    arxiv_id = str(web.get("arxiv_id") or "").strip()
    text_doi = str(web.get("text_doi") or "").strip()
    record = _page_record(web)
    # Identifier sources trusted without a title check: the stored source URL,
    # the web page the extension clipped from, and the identifiers that page
    # carried in its own tags.
    hints = "\n".join(filter(None, [
        source_url, str(props.get("web_url") or ""), str(web.get("doi") or ""), arxiv_id]))
    # Raw head text (no excerpt label — this text feeds regexes and matching).
    # The scan window is decoupled from the AI-context pref (see SCAN_CHARS);
    # the AI later gets only the pref-sized head slice.
    text, tail = "", ""
    path = None
    if doc_id:
        text, next_offset, _ = _pdf_excerpt(ws, doc_id, max(context_char_limit, SCAN_CHARS))
        if text == PDF_EXTRACT_FAILED:  # nothing to read or match against
            text, next_offset = "", None
        path = _pdf_path(ws, doc_id)
        if next_offset is not None:
            # The document continues past the scan window. The paper's own DOI
            # is often printed only in the end-of-article trailer (Science
            # issue-clipped PDFs), so scan the last page too.
            npages = _page_count(str(path)) if path else 0
            if npages > 1:
                tail = _pdf_excerpt(ws, doc_id, 4000, start_page=npages)[0]
                if tail == PDF_EXTRACT_FAILED:
                    tail = ""
        # The paper is being set up — index it now (background) so search,
        # the AI document map and the library-wide Ctrl+F don't wait for the
        # first search to discover it. After our own head extraction: PyMuPDF
        # is serialized behind one lock and the lookup needs its text now.
        _ensure_indexed(ws, doc_id)
    # The first page's largest type: the title on most papers, where the
    # text head can open with a running header, a journal banner or a
    # "Downloaded from" line. A Crossref query, and a place the document's
    # own title can be.
    guess = _title_guess(str(path)) if text and path else ""
    doc = _Document(text, guess)

    # ``match``: what tied the record to the document (MATCH_CONFIRMED's
    # table). ``named``: records the text names without tying them to this
    # document, as ``(rank, record, bibtex, match)`` — rank 0 a record whose
    # title is the document's own (no author found by it), 1 an identifier
    # printed in it whose record's title the text prints too, 2 a title
    # search hit whose title is a phrase of the text, 3 an identifier and
    # nothing more. When nothing ties a record, the AI picks among them,
    # best ranked first; without it the best stands, flagged. ``seen``: the
    # DOIs of the records found so far, which a later step skips;
    # ``fetched``: the doi.org answers, which the AI's verification reuses.
    meta, bibtex, match = None, "", ""
    named, seen, fetched = [], set(), {}

    def name(record, bib, how, rank):
        named.append((rank, record, bib, how))
        if record.get("doi"):
            seen.add(record["doi"].lower())

    def fetch_doi(doi):
        if doi.lower() not in fetched:
            fetched[doi.lower()] = _fetch_doi(doi)
        return fetched[doi.lower()]

    def text_named(record, bib, how):
        """A record behind an identifier the text prints that does not tie."""
        if how == "title_only":
            name(record, bib, "title_only", 0)
        else:
            name(record, bib, "id_only", 1 if how else 3)

    trusted_arxiv = arxiv_id or _find_arxiv_id(hints, "")
    aid = trusted_arxiv or _find_arxiv_id("", text)
    if aid:
        found = _fetch_arxiv(aid)
        if found:
            # Published since: cite the journal's record, keep the arXiv id.
            # Its authors count for the tie too (arXiv may list a
            # collaboration where the page prints the names).
            published, pub_bib = _published_record(found)
            how = "source" if trusted_arxiv else _named_tie(doc, found, published)
            if how in ("source", "title"):
                meta, bibtex, match = published, pub_bib, how
            else:
                text_named(published, pub_bib, how)

    if not meta:
        # The first DOI in the text can belong to a *cited* paper (footnotes
        # on page 1, a previous article's trailer in an issue-clipped PDF), so
        # resolve candidates until one ties to the document. The tail rides
        # along so an own-DOI printed only in the end trailer is a candidate
        # too, and a DOI the web page only printed in its text goes first,
        # under the same check (it may be a work the page cites).
        for cand_doi in _find_doi_candidates(hints, "\n".join(filter(None, [text_doi, text, tail]))):
            if cand_doi.lower() in seen:
                continue
            m, b = fetch_doi(cand_doi)
            if not m:
                continue
            how = "source" if cand_doi.lower() in hints.lower() else _named_tie(doc, m)
            if how in ("source", "title"):
                meta, bibtex, match = m, b, how
                break
            seen.add(cand_doi.lower())
            text_named(m, b, how)

    if not meta:
        # Books: an ISBN printed in the PDF is tied by the book's title page
        # like any record, a short title ("Lasers") allowed as a line of the
        # title block, since the ISBN vouches for it. A reference list, a
        # syllabus or a review printing a book's ISBN with its title only
        # names it (``_record_in_text``).
        for isbn in _find_isbns(text + "\n" + tail):
            m = _fetch_isbn(isbn)
            if not m:
                continue
            how = _tie(doc, m, min_len=1)
            if how == "title":
                meta, bibtex, match = m, "", how
                break
            text_named(m, "", how or ("phrase" if _record_in_text(m, text) else ""))

    rt = ai_runtime(actor)
    can_pick = bool(rt["enabled"] and text)
    rejected = frozenset()  # the records the AI's check said are not this document
    if not meta:
        # No identifier ties a record — Crossref bibliographic search,
        # queried with the web page's title and first author (its own tags),
        # the first page's largest type, the page title (users title pages
        # with the paper name; an upload carries its file name), then the
        # text head; a query asked once is not asked again. A hit is taken
        # when the PDF is its title page, or, beside the web page's own
        # record, when the titles are near-identical and the years agree; a
        # hit whose title is only a phrase of the text is one more record
        # the text names — a few, five when the AI can pick among them and
        # three when it cannot (bounding the doi.org calls, while a later
        # hit's full record may still be the one that ties). A hit's doi.org
        # record (its full title, subtitle included) is fetched only for
        # those and for a hit whose title is the document's own, and a DOI
        # an earlier step found is not asked again. Only that full record
        # can tie: a hit whose fetch fails is a phrase at most.
        queries = []
        if record:
            queries.append(f"{record['title']} {record['authors'][0]}"[:300])
        if text:
            if len(guess.split()) >= 3:
                queries.append(guess)
            page_title = re.sub(r"\.pdf$", "", (content or "").strip(), flags=re.I)
            if len(page_title.split()) >= 3:
                queries.append(page_title[:300])
            queries.append(_norm_match(text[:1500])[:500])
        asked, phrases = set(), 0
        for q in queries:
            if meta:
                break
            if _norm_match(q) in asked:
                continue
            asked.add(_norm_match(q))
            for cand in _crossref_search(q):
                doi = str(cand.get("doi") or "").lower()
                if not doi or doi in seen:
                    continue
                seen.add(doi)
                near = _same_work(cand, record, main=True)
                how = _named_tie(doc, cand)
                if not near and how not in ("title", "title_only") \
                        and (not how or phrases >= (NAMED_MAX if can_pick else 3)):
                    continue
                # Tied on the full record: a search hit's title lacks its
                # subtitle, and a series title ("Planck 2018 results") is
                # every volume's.
                better, bib = fetch_doi(cand["doi"])
                if better:
                    found = better
                    how = "page" if _same_work(better, record) else _named_tie(doc, better)
                else:
                    found, how = cand, ("phrase" if near or how else "")
                if how in ("page", "title"):
                    meta, bibtex, match = found, bib, how
                    break
                if how == "title_only":
                    name(found, bib, "title_only", 0)
                elif how:
                    phrases += 1
                    name(found, bib, "phrase", 2)

    if not meta and record:
        # The web page's own record of the work: the tags its citation_doi
        # comes from, so it is trusted like that DOI. It beats the records
        # the text merely names and spares the AI calls for works no
        # registry knows: a thesis in a repository, a report, a conference
        # paper without a DOI.
        meta, bibtex, match = dict(record), "", "page"

    if not meta and named:
        # The AI tells which of the records the text names is this document
        # (none: they are dropped, and the AI reads the document itself).
        # Without it, or when it cannot answer, the best ranked stands,
        # flagged.
        named = [entry[1:] for entry in sorted(named, key=lambda entry: entry[0])][:NAMED_MAX]
        # It reads what AI extraction would (the pref-sized head), the whole
        # first page at least: an issue-clipped article's title sits after
        # the previous article's tail.
        opening = text[:min(SCAN_CHARS, max(context_char_limit, doc.page_one))]
        pick = _ai_pick(opening, [r for r, _, _ in named], rt, model) if can_pick else AI_PICK_FAILED
        if pick is None:
            rejected = frozenset(str(r.get("doi") or "").lower() for r, _, _ in named) - {""}
            named = []
        else:
            meta, bibtex, match = named[max(pick, 0)]
            if pick != AI_PICK_FAILED:
                match = "ai"
    if not meta and not named and rt["enabled"] and text:
        read = _ai_extract_meta(text[:context_char_limit], prompt, model, rt)
        if read:
            # Upgrade to a registry record where possible; drop identifiers
            # that resolve nowhere and aren't in the PDF (fabrications) or
            # that name another work.
            meta, bibtex, match = _verify_ai_meta(read, text + "\n" + tail, hints, doc,
                                                  fetch_doi=fetch_doi, rejected=rejected)
    if not meta:
        # Negative cache: remember the failed attempt on the page so clients
        # stop auto-retrying on every open. Manual ↻ (force) still retries,
        # and a success below clears the marker.
        _save_props(ws, block_id, {
            "meta_error": {"at": page_now(), "detail": "no arXiv id, DOI, Crossref, or AI match"}},
            actor=actor)
        raise HTTPException(status_code=404, detail="no metadata found (no arXiv id, DOI, Crossref, or AI match)")

    # A citation key pinned on the page outranks both the generated key and
    # the registrar's own (doi.org renders BibTeX keyed its own way), and it
    # survives this refetch: the user's .tex files cite it.
    cite_key = bibtex_mod.clean_key(props.get("cite_key") or "")
    if not bibtex:
        bibtex = bibtex_mod.build_entry(meta, cite_key)
    elif cite_key:
        bibtex = bibtex_mod.with_key(bibtex, cite_key)
    # Stored with the record so every surface can say what tied it to the
    # document, and warn before it is cited when nothing confirmed did (an
    # id or a title the text only names may be a cited work's; AI output may
    # be a plausible hallucination).
    meta["match"] = match
    meta["unverified"] = _unconfirmed(meta)
    # The slide citation rides along: one AI call now, instead of on first
    # share. A failure here never fails the fetch — /metadata/cite retries.
    ppt_cite = ""
    if rt["enabled"]:
        try:
            ppt_cite = _make_ppt_cite(rt, meta, bibtex, cite_prompt, cite_model)
        except Exception as e:
            log.warning(f"[metadata] slide citation failed: {e}")
    title = str(meta.get("title") or "").strip()
    meta["at"] = page_now()  # when this record was looked up (Settings → Maintenance, the popover)
    updates = {"meta": meta, "bibtex": bibtex}
    if ppt_cite:
        updates["ppt_cite"] = ppt_cite
    title_updated, page_title = _save_props(
        ws, block_id, updates,
        # A stale citation (generated from the previous record) is dropped.
        # An unconfirmed record does not rename the page: the automatic title
        # waits for a confirmed one, a typed identifier or a hand edit.
        remove=("meta_error",) + (() if ppt_cite else ("ppt_cite",)),
        auto_title="" if meta["unverified"] else title, actor=actor,
    )
    # page_title is ALWAYS the page's current title, not only when this call
    # renamed it: a concurrent lookup may have done the rename first, and the
    # client shows whatever comes back here.
    return {"meta": meta, "bibtex": bibtex, "ppt_cite": ppt_cite, "source": meta.get("source", ""),
            "cite_key": cite_key, "cached": False, "title_updated": title_updated, "page_title": page_title}


class MetaBatchRequest(BaseModel):
    block_ids: list[str]
    force: bool = True  # False: a page that has its record keeps it (the upload follow-up)
    prompt: str = ""
    model: str = ""
    context_char_limit: int = Field(default=6000, ge=100, le=1_000_000)
    cite_prompt: str = ""
    cite_model: str = ""


class _Batch:
    """The papers one account's ``metadata`` job looks up in one workspace,
    in the order asked. A start while the batch is open adds its papers to
    it; the run takes them one at a time and closes the batch once none is
    left. Both happen under ``_batches_lock``, so a paper is never added to
    a batch whose run already ended."""

    def __init__(self, key: tuple[str, str]):
        self.key = key
        self.job_id = ""
        self.items: list[list] = []      # [block_id, title, force, options]
        self.waiting: dict[str, int] = {}  # block_id → its item, while not taken yet
        self.taken = 0

    def add(self, papers: list[tuple[str, str]], force: bool, options: dict) -> None:
        for block_id, title in papers:
            at = self.waiting.get(block_id)
            if at is not None and at >= self.taken:  # asked again before its turn
                item = self.items[at]
                item[2], item[3] = item[2] or force, options
                continue
            self.waiting[block_id] = len(self.items)
            self.items.append([block_id, title, force, options])

    def _close(self) -> None:
        if _batches.get(self.key) is self:
            del _batches[self.key]

    def run(self, job, ws: str, actor: str) -> dict:
        fetched = failed = 0
        try:
            while True:
                with _batches_lock:
                    if self.taken >= len(self.items):
                        self._close()
                        break
                    block_id, title, force, options = self.items[self.taken]
                    done, total = self.taken, len(self.items)
                    self.taken += 1
                job.progress(done=done, total=total, unit="papers", item=title)
                try:
                    fetch_page_metadata(ws, block_id, actor, force=force, **options)
                    fetched += 1
                except HTTPException:  # nothing found (negative-cached on the page), or the page went
                    failed += 1
                except Exception as e:  # noqa: BLE001 — one paper's failure never ends the batch
                    log.warning(f"[metadata] batch lookup of {block_id} failed: {e}")
                    failed += 1
        finally:  # stopped or failed: a later start begins a new batch
            with _batches_lock:
                self._close()
        job.progress(done=len(self.items), total=len(self.items), unit="papers", item="")
        return {"papers": len(self.items), "fetched": fetched, "failed": failed}


_batches: dict[tuple[str, str], _Batch] = {}  # (account, workspace) → its open batch
_batches_lock = threading.Lock()


@router.post("/jobs/metadata")
def metadata_batch(payload: MetaBatchRequest, request: Request):
    """Look papers up as a background job (kind ``metadata``,
    docs/dev/tasks.md), one after the other, so the work goes on when the
    dialog or the tab closes: Settings → Maintenance's Fetch needed / Fetch
    selected / Refetch (``force``) and the lookups after an upload of several
    PDFs (``force: false``). An account has one batch per workspace: a start
    while it is queued or running adds its papers to it and answers that
    job. A paper nothing is found for is counted, not fatal; a stop lands
    between two papers."""
    user_id = require_user_id(request)
    ws = require_ws(request, write=True)
    wanted = list(dict.fromkeys(i for i in payload.block_ids if i))
    titles = {}
    with connect_pages_db(ws) as conn:
        for start in range(0, len(wanted), 500):
            chunk = wanted[start:start + 500]
            rows = conn.execute(
                "SELECT id, content, properties FROM unified_blocks WHERE parent_id = 'root' "
                f"AND id IN ({', '.join('?' for _ in chunk)})", chunk).fetchall()
            for block_id, content, props_json in rows:
                meta = json.loads(props_json or "{}").get("meta") or {}
                titles[block_id] = meta.get("title") or content or "Untitled"
    papers = [(i, titles[i]) for i in wanted if i in titles]  # own pages only, in the order asked
    if not papers:
        raise HTTPException(status_code=400, detail="no papers to look up")
    options = {"prompt": payload.prompt, "model": payload.model, "context_char_limit": payload.context_char_limit,
               "cite_prompt": payload.cite_prompt, "cite_model": payload.cite_model}
    key = (user_id, ws)
    with _batches_lock:
        batch = _batches.get(key)
        if batch is not None:
            job = jobs.get(batch.job_id)
            if job and job["state"] in jobs.ACTIVE and not job["stopping"]:
                batch.add(papers, payload.force, options)
                return job
            del _batches[key]  # stopped (while queued, its run never started): it takes no more
        batch = _Batch(key)
        batch.add(papers, payload.force, options)
        job = jobs.start("metadata", owner=user_id, ws=ws, run=lambda job: batch.run(job, ws, user_id),
                         title="Paper metadata lookup", params={"papers": len(papers)})
        batch.job_id = job["id"]
        _batches[key] = batch
    return job


def _typed_identifier_record(meta: dict, old: dict) -> tuple[dict | None, dict | None, str]:
    """The registry record behind a DOI or arXiv id the user typed into the
    popover, when it differs from the stored record's: ``(lookup, record,
    bibtex)``. The DOI goes first, since its record is the one a bibliography
    cites, and the arXiv id rides along on it; a new arXiv id alone goes
    through arXiv, then the published DOI arXiv names. ``lookup`` says what
    was asked and whether it answered (None when nothing changed); the record
    is None when nothing answered."""
    from .clip import norm_arxiv, norm_doi  # clip imports this module
    doi, aid = norm_doi(meta.get("doi", "")), norm_arxiv(meta.get("arxiv_id", ""))
    missed = None
    for registry, ident, before in (("doi", doi, norm_doi(str(old.get("doi") or ""))),
                                    ("arxiv", aid, norm_arxiv(str(old.get("arxiv_id") or "")))):
        if not ident or ident == before:
            continue
        if registry == "doi":
            rec, bib = _fetch_doi(ident)
            if rec:
                rec["arxiv_id"] = aid
        else:
            rec = _fetch_arxiv(ident)
            rec, bib = _published_record(rec) if rec else (None, "")
        if rec:
            return {"registry": registry, "id": ident, "found": True}, rec, bib
        missed = missed or {"registry": registry, "id": ident, "found": False}
    return missed, None, ""


class MetaUpdateRequest(StorableBody):
    block_id: str
    meta: dict = {}
    # The pinned citation key. None = not sent, leave the page's pin alone;
    # "" = unpin, so the key goes back to being generated from the record.
    cite_key: str | None = None


@router.post("/metadata/update")
def metadata_update(payload: MetaUpdateRequest, request: Request):
    """Save hand-edited metadata. BibTeX is rebuilt from the edited fields
    (under the pinned citation key, if any) and the cached slide citation is
    invalidated. ``meta.kind`` is the item type the popover's Type row
    chose; one not sent (or not an item type) keeps the stored one. All-blank
    fields clear the cached metadata entirely, the pinned key with it — the
    type alone is not a record. A DOI or arXiv id typed in place of the
    stored one is looked up instead (``_typed_identifier_record``): its
    registry record replaces the typed fields, trusted like a source URL's
    identifier, and ``lookup`` in the answer says what was asked. One that
    resolves nowhere leaves the fields as typed."""
    ws = require_ws(request, write=True)
    _, props = _load_page(ws, payload.block_id)  # 404 before validating the edit
    m = payload.meta or {}
    authors = m.get("authors") or []
    if isinstance(authors, str):
        authors = [a.strip() for a in re.split(r",| and ", authors) if a.strip()]
    meta = {
        "title": str(m.get("title") or "").strip()[:500],
        "authors": [str(a).strip()[:200] for a in authors if str(a).strip()],
        "year": str(m.get("year") or "").strip()[:20],
        "venue": str(m.get("venue") or "").strip()[:300],
        "volume": str(m.get("volume") or "").strip()[:40],
        "pages": str(m.get("pages") or "").strip()[:60],
        "doi": str(m.get("doi") or "").strip()[:200],
        "arxiv_id": str(m.get("arxiv_id") or "").strip()[:60],
        "publisher": str(m.get("publisher") or "").strip()[:300],
        "isbn": re.sub(r"[^0-9Xx]", "", str(m.get("isbn") or ""))[:13].upper(),
        "source": "manual",
    }
    # ppt_cite is stale (the metadata changed); meta_error is settled (or
    # reset) by the hand-edit either way
    stale = ("ppt_cite", "meta_error")
    if not any(v for k, v in meta.items() if k != "source"):
        _save_props(ws, payload.block_id, remove=stale + ("meta", "bibtex", "cite_key"),
                    actor=request.state.user_id)
        return {"meta": None, "bibtex": "", "cite_key": "", "source": "", "cached": False}
    lookup, record, bibtex = _typed_identifier_record(meta, props.get("meta") or {})
    if record:
        # The user named the paper, so its record needs no title check.
        meta = {**record, "match": "typed", "unverified": False}
    else:
        # The item type survives an edit that leaves it alone (a book stays
        # @book). A hand edit is the user vouching for the record, so it is
        # not "unverified".
        kind = item_types.normalize(m.get("kind")) or item_types.normalize((props.get("meta") or {}).get("kind"))
        if kind:
            meta["kind"] = kind
    meta["at"] = page_now()  # when it was edited, as a lookup stamps when it looked
    # An unsent cite_key leaves the page's pin as it is; "" unpins.
    cite_key = (bibtex_mod.clean_key(payload.cite_key) if payload.cite_key is not None
                else bibtex_mod.clean_key(props.get("cite_key") or ""))
    if not bibtex:
        bibtex = bibtex_mod.build_entry(meta, cite_key)
    elif cite_key:
        bibtex = bibtex_mod.with_key(bibtex, cite_key)
    updates = {"meta": meta, "bibtex": bibtex}
    if cite_key:
        updates["cite_key"] = cite_key
    _save_props(ws, payload.block_id, updates, remove=stale + (() if cite_key else ("cite_key",)),
                actor=request.state.user_id)
    return {"meta": meta, "bibtex": bibtex, "cite_key": cite_key, "source": meta["source"], "cached": False,
            "lookup": lookup}


class MetaVerifyRequest(BaseModel):
    block_id: str


@router.post("/metadata/verify")
def metadata_verify(payload: MetaVerifyRequest, request: Request):
    """The user checked the stored record against the paper and vouches for
    it: the unverified flag clears, the source stays (an AI reading is still
    one). A refetch replaces the record, and the mark goes with it."""
    ws = require_ws(request, write=True)
    _, props = _load_page(ws, payload.block_id)
    meta = props.get("meta")
    if not meta:
        raise HTTPException(status_code=409, detail="no metadata to verify")
    meta = {**meta, "unverified": False, "user_verified": True}
    _save_props(ws, payload.block_id, {"meta": meta}, actor=request.state.user_id)
    return {"meta": meta}


class CiteRequest(BaseModel):
    block_id: str
    prompt: str = ""   # custom PPT-citation prompt (empty = built-in)
    model: str = ""
    force: bool = False  # regenerate even when a cached citation exists


@router.post("/metadata/cite")
def metadata_cite(payload: CiteRequest, request: Request):
    ws = require_ws(request, write=True)
    _, props = _load_page(ws, payload.block_id)
    if props.get("ppt_cite") and not payload.force:
        return {"citation": props["ppt_cite"], "cached": True}
    rt = require_ai_runtime(request.state.user_id)
    meta = props.get("meta")
    bibtex = props.get("bibtex", "")
    if not meta and not bibtex:
        raise HTTPException(status_code=409, detail="no metadata yet — fetch metadata first")
    try:
        citation = _make_ppt_cite(rt, meta, bibtex, payload.prompt, payload.model)
    except CallRefused:
        raise
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"AI call failed: {e}")
    # cache alongside the rest of the metadata
    _save_props(ws, payload.block_id, {"ppt_cite": citation}, actor=request.state.user_id)
    return {"citation": citation, "cached": False}
