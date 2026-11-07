"""The workspace's glossary for the PDF translated view: ``source → target``
terms per target language that the workspace's members set (Settings →
Language and Translation › Glossary, the selection translator's Add to
glossary), kept in its pages.db as ``translation_terms`` (db.PAGES_SCHEMA):
what people write lives there, and it goes with the workspace's backups.

A chat-model translation request carries the terms its texts use, one
``Terms:`` line above the segments (``Glossary.line``,
translate_context.user_turn), and every text that uses a term is named by a
stamp of those terms (``Glossary.stamp``) in its memory key and its kept
row's name, so a changed term makes exactly the texts that use it miss. A
text that uses none has the stamp "", under which its keys are what they
were before the workspace had a glossary. A machine-translation service
takes no terms. docs/dev/translation.md "The glossary" describes it.
"""

import hashlib
import re

from .db import account_names, connect_pages_db, connect_users_db, page_now
from .logbuf import log

# Terms per language, and a term's length (either side), at most.
MAX_TERMS = 500
TERM_MAX_CHARS = 100

# A text's breaks and tokens ({br}, {v1}…, translate_context) are no words:
# a term matches across a break, never inside a token.
_MARKS = re.compile(r"\{(?:br|v\d+)\}")
_CJK = re.compile(r"[぀-ヿ㐀-䶿一-鿿가-힯豈-﫿ｦ-ﾟ]")


def clean(term) -> str:
    """A term as it is kept: its whitespace collapsed to single spaces."""
    return " ".join(str(term or "").split())


def _word_edge(ch: str) -> bool:
    """Whether a term that starts or ends with ``ch`` must meet no other word
    character there: a Latin (or any non-CJK) letter or digit does; a CJK
    character, which no space sets apart, and a symbol do not."""
    return bool(re.match(r"\w", ch)) and not _CJK.match(ch)


# What stands between the words of a term, any run of it as good as any
# other: a space, a hyphen ("Rydberg-blockade", "spin–orbit": the hyphen,
# the non-breaking one and the en dash PDFs set). And an apostrophe either
# way, straight as a term is typed or curly as a PDF sets it.
_SEPARATORS = r"\s\-" + "‐‑–"
_SEPARATED = re.compile(f"([{_SEPARATORS}]+)")
_APOSTROPHES = "['’]"
_APOSTROPHE = re.compile(_APOSTROPHES)


def _acronym(source: str) -> bool:
    """A term written in capitals (two or more, no lower case: LED, SET,
    US), which matches with its case: "led" and "set" are other words."""
    return sum(ch.isupper() for ch in source) >= 2 and not any(ch.islower() for ch in source)


def _key(text: str) -> str:
    """A matched text or a term as one compares to the other: lower case,
    its separators one space, its apostrophes straight."""
    return _APOSTROPHE.sub("'", " ".join(_SEPARATED.sub(" ", text).split())).lower()


def _pattern(source: str) -> str:
    """``source`` as a regex: escaped, any run of separators in it (spaces,
    hyphens, en dashes) matching any run in the text and either apostrophe
    the other, in its case when it is an acronym (``_acronym``); whole
    words at a Latin edge, a Latin last letter taking an English plural's
    "s" or "es" ("qubits", "LEDs")."""
    body = "".join(f"[{_SEPARATORS}]+" if _SEPARATED.fullmatch(part)
                   else _APOSTROPHE.sub(_APOSTROPHES, re.escape(part))
                   for part in _SEPARATED.split(source) if part)
    if _acronym(source):
        body = f"(?-i:{body})"
    head = r"(?<!\w)" if _word_edge(source[0]) else ""
    plural = r"(?:e?s)?" if source[-1].isalpha() and _word_edge(source[-1]) else ""
    tail = r"(?!\w)" if _word_edge(source[-1]) else ""
    return head + body + plural + tail


def matched(text: str, rows) -> list:
    """The ``(source, target)`` rows ``text`` uses, longest source first:
    case-insensitive but for an acronym, whole words where a term starts or
    ends with a Latin letter or digit (an English plural counted in), a
    plain substring where it is CJK. The text is read from its start: at
    each place the term that starts earliest is the one used, of those that
    start there the longest, and what it covers is no other term's. So of
    "quantum dot" and "dot" in "quantum dot", the first; of "neutral atom"
    and "atom array qubits" in "neutral atom array qubits", the first too,
    since it starts first."""
    return Glossary(rows).used(text)


class Glossary:
    """One language's terms, as one request matches its texts against them."""

    def __init__(self, rows=()):
        rows = [(clean(s), clean(t)) for s, t in rows]
        self.rows = sorted({s.lower(): (s, t) for s, t in rows if s and t}.values(),
                           key=lambda r: (-len(r[0]), r[0].lower()))
        self._by = {_key(s): (s, t) for s, t in self.rows}
        self._re = re.compile("|".join(_pattern(s) for s, _ in self.rows), re.IGNORECASE) if self.rows else None
        self._stamps = {}

    def __bool__(self) -> bool:
        return bool(self.rows)

    def used(self, text: str) -> list:
        """The rows ``text`` uses, longest source first (``matched``)."""
        if not self._re or not text:
            return []
        found = {}
        for m in self._re.finditer(_MARKS.sub(" ", text)):
            row = self._by.get(_key(m.group(0)))
            if row is None:  # a plural, or a case IGNORECASE folds that lower() does not
                row = next(r for r in self.rows if re.fullmatch(_pattern(r[0]), m.group(0), re.IGNORECASE))
            found[row[0].lower()] = row
        return sorted(found.values(), key=lambda r: (-len(r[0]), r[0].lower()))

    def stamp(self, text: str) -> str:
        """The stamp of the terms ``text`` uses: a short hash of the pairs,
        whatever their order and the case of their source; "" when it uses
        none. Worked out once per text: a request names a text by it for
        memory and for its kept row."""
        if text not in self._stamps:
            pairs = sorted(f"{s.lower()}\x00{t}" for s, t in self.used(text))
            self._stamps[text] = hashlib.sha256("\x01".join(pairs).encode()).hexdigest()[:16] if pairs else ""
        return self._stamps[text]

    def line(self, texts) -> str:
        """The terms the ``texts`` use, rendered for the user turn's
        ``Terms:`` line ("a → b; c → d", longest first); "" for none."""
        rows = {}
        for text in texts:
            for s, t in self.used(text):
                rows[s.lower()] = (s, t)
        ordered = sorted(rows.values(), key=lambda r: (-len(r[0]), r[0].lower()))
        return "; ".join(f"{s} → {t}" for s, t in ordered)


def glossary(ws: str, lang: str) -> Glossary:
    """The workspace's terms in ``lang``; empty without a workspace, and when
    they cannot be read (logged: a glossary never fails a translation)."""
    if not ws:
        return Glossary()
    try:
        with connect_pages_db(ws) as conn:
            rows = conn.execute("SELECT source, target FROM translation_terms WHERE lang = ?", (lang,)).fetchall()
    except Exception as e:  # noqa: BLE001 — translate as if there were no terms
        log.warning(f"[translate_terms] reading the glossary failed: {e}")
        return Glossary()
    return Glossary(rows)


def listing(ws: str, lang: str) -> list:
    """The terms in ``lang`` as Settings shows them, by source:
    ``{source, target, by (username, "" when the account is gone), at}``."""
    with connect_pages_db(ws) as conn:
        rows = conn.execute("SELECT source, target, by, at FROM translation_terms WHERE lang = ? "
                            "ORDER BY source", (lang,)).fetchall()
    with connect_users_db() as users:
        names = account_names(users, [r[2] for r in rows])
    return [{"source": s, "target": t, "by": names.get(by, ""), "at": at} for s, t, by, at in rows]


class TermError(ValueError):
    """A write the glossary refuses: ``status`` is the HTTP status it maps to."""

    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def _checked(source, target) -> tuple[str, str]:
    source, target = clean(source), clean(target)
    if not source or not target:
        raise TermError("a term needs its source and its translation")
    if len(source) > TERM_MAX_CHARS or len(target) > TERM_MAX_CHARS:
        raise TermError(f"a term is at most {TERM_MAX_CHARS} characters on either side")
    return source, target


def _taken(conn, lang: str, source: str, but: str | None = None) -> bool:
    """Whether the language has ``source`` in any case, the term ``but``
    aside. Folded here as the matcher folds (``Glossary``): the column's
    NOCASE folds ASCII only, so "Ångström" and "ångström" differ to it."""
    key = source.lower()
    return any(s.lower() == key and s != but
               for (s,) in conn.execute("SELECT source FROM translation_terms WHERE lang = ?", (lang,)))


def add(ws: str, lang: str, source, target, by: str) -> None:
    """Add a term; refused when the language has it already (whatever its
    case: one source has one translation) or holds MAX_TERMS."""
    source, target = _checked(source, target)
    with connect_pages_db(ws) as conn:
        if _taken(conn, lang, source):
            raise TermError("the glossary has that term already", 409)
        count = conn.execute("SELECT COUNT(*) FROM translation_terms WHERE lang = ?", (lang,)).fetchone()[0]
        if count >= MAX_TERMS:
            raise TermError(f"the glossary holds {MAX_TERMS} terms in this language at most", 409)
        conn.execute("INSERT INTO translation_terms (lang, source, target, by, at) VALUES (?, ?, ?, ?, ?)",
                     (lang, source, target, by, page_now()))


def edit(ws: str, lang: str, was, source, target, by: str) -> None:
    """Change the term ``was`` to ``source → target``; refused when ``was``
    is not there, or ``source`` is another term the language has."""
    source, target = _checked(source, target)
    was = clean(was)
    with connect_pages_db(ws) as conn:
        row = conn.execute("SELECT source FROM translation_terms WHERE lang = ? AND source = ?", (lang, was)).fetchone()
        if not row:
            raise TermError("the glossary has no such term", 404)
        if _taken(conn, lang, source, but=row[0]):
            raise TermError("the glossary has that term already", 409)
        conn.execute("DELETE FROM translation_terms WHERE lang = ? AND source = ?", (lang, was))
        conn.execute("INSERT INTO translation_terms (lang, source, target, by, at) VALUES (?, ?, ?, ?, ?)",
                     (lang, source, target, by, page_now()))


def remove(ws: str, lang: str, source) -> None:
    with connect_pages_db(ws) as conn:
        conn.execute("DELETE FROM translation_terms WHERE lang = ? AND source = ?", (lang, clean(source)))
