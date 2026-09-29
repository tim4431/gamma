"""Search text normalization and fuzzy matching, shared by the FTS indexes
(gamma/block_index.py, gamma/pdf_index.py) and block search
(routers/blocks.py).

The frontend mirrors the normalization and fuzzy rules in
frontend/src/shared/lib/textnorm.js (used by search/SearchPanel.jsx and
pdf/PdfViewer.jsx), so a query matches the same way in the notes DB, the FTS
indexes and the live pdf.js viewer. The cases in tests/shared/textnorm.json
pin both sides; keep them in sync when changing a rule.
"""

import re
import unicodedata

# One bump has every workspace's search indexes (notes and PDF) rebuilt
# lazily (extraction or normalization changes make old rows stale).
INDEX_VERSION = 4

_DASHES = "‐‑‒–—―"
# Thousands separators PDFs use inside numbers: comma, nbsp, narrow nbsp, thin space
_DIGIT_SEPS = ",   "

_HYPHEN_BREAK_RE = re.compile(rf"(?<=\w)[-{_DASHES}]\s*\n\s*(?=\w)")
_DIGIT_SEP_RE = re.compile(rf"(?<=\d)[{_DIGIT_SEPS}](?=\d)")
_WS_RE = re.compile(r"\s+")


def normalize_text(s: str) -> str:
    """Canonical searchable form of extracted PDF text (and of queries):
    digit-group separators removed ("3,000" → "3000" — before NFKC, which
    would fold the no-break and thin spaces into plain ones), NFKC (folds
    ligatures like ﬁ), soft hyphens dropped, words re-joined across
    hyphenated line breaks, whitespace collapsed. Case is left alone — FTS5's
    tokenizer and regex flags handle that. The shared cases in
    tests/shared/textnorm.json pin these rules for the frontend mirrors too."""
    if not s:
        return ""
    s = _DIGIT_SEP_RE.sub("", s)
    s = unicodedata.normalize("NFKC", s)
    s = s.replace("­", "")
    s = _HYPHEN_BREAK_RE.sub("", s)
    return _WS_RE.sub(" ", s).strip()


def _is_separator(c: str) -> bool:
    return c.isspace() or c == "-" or c in _DASHES


def fuzzy_pattern(q: str, case: bool = False, whole: bool = False) -> re.Pattern | None:
    """Compile a search query into a regex (None = empty).

    The match is separator-tolerant: digits may be split by grouping
    separators ("3000" finds "3,000"), and spaces/hyphens are
    interchangeable ("3000 qubit" finds "3,000-qubit"). The query is always
    text, never a pattern of the caller's: a crafted one can backtrack for
    hours while holding the GIL, i.e. the whole server."""
    flags = 0 if case else re.IGNORECASE
    q = normalize_text(q)
    parts = []
    i = 0
    while i < len(q):
        c = q[i]
        if _is_separator(c):
            parts.append(rf"[\s\-{_DASHES}]+")
            while i + 1 < len(q) and _is_separator(q[i + 1]):
                i += 1
        else:
            parts.append(re.escape(c))
            if c.isdigit() and i + 1 < len(q) and q[i + 1].isdigit():
                parts.append(rf"[{_DIGIT_SEPS}\s]?")
        i += 1
    if not parts:
        return None
    body = "".join(parts)
    if whole:
        body = rf"\b(?:{body})\b"
    return re.compile(body, flags)


def literal_runs(q: str, case: bool = False) -> list[str]:
    """Text every ``fuzzy_pattern(q, case)`` match contains verbatim, so a
    search can skip non-matching rows in SQL (instr, or LIKE without
    ``case``) before running the pattern: the query's runs between
    separators, cut between two digits (a grouping separator may sit there).
    Without ``case`` a run also ends at a letter SQLite's LIKE does not
    fold (non-ASCII with case), since LIKE folds ASCII only. (Python's
    IGNORECASE also folds four non-ASCII letters onto ASCII ones — ſ, K, İ,
    ı — which LIKE does not: text spelling a query's s, k or i that way is
    the one thing the prefilter drops.) Longest first, no repeats."""
    q = normalize_text(q)
    runs, cur = [], ""
    for i, c in enumerate(q):
        if _is_separator(c) or (not case and not c.isascii() and (c.lower() != c or c.upper() != c)):
            runs.append(cur)
            cur = ""
        elif c.isdigit() and i and q[i - 1].isdigit():
            runs.append(cur)
            cur = c
        else:
            cur += c
    runs.append(cur)
    return sorted({r for r in runs if r}, key=lambda r: (-len(r), r))
