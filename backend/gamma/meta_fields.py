"""The record fields a citation needs beyond the year a paper record has
always held (``properties.meta``, docs/dev/paper_metadata.md): the issue,
the journal's abbreviation, the full date and the address.

``date`` is kept only when it says more than ``year`` (``YYYY-MM`` or
``YYYY-MM-DD``) and only while it agrees with it: the year stays the field
every reader has, a BibTeX key and a Zotero item are built from it, so a
date of another year is stale (a year pulled from Zotero, an edit of the
year alone) and goes where the year is written. ``url`` is an http(s)
address and nothing else, since a citation's HTML links it. Every writer
of the record goes through these: the lookups (gamma/routers/metadata.py),
a hand edit, a clip's page record, the Zotero link's pull
(gamma/zotero_local.py) and migration step 72. Pure, standard library
only.
"""

import re

# The keys a record holds only when they have a value: a record without
# them is the record of before them, its BibTeX entry byte for byte.
NEW_FIELDS = ("issue", "journal_abbr", "date", "url")

YEAR_MAX = 20     # a year that is no number ("in press") is kept verbatim, this long at most
URL_MAX = 2000
# The issue and the journal's abbreviation, at most this long (``clean_cited``).
CITED_MAX = {"issue": 40, "journal_abbr": 300}

# 2020, 2020-03, 2020-03-05, and the slashes and single digits of a
# Highwire tag or a hand edit (2020/3/5).
_DATE_RE = re.compile(r"(\d{4})(?:[-/](\d{1,2})(?:[-/](\d{1,2}))?)?")
_ISO_RE = re.compile(r"\d{4}-(0[1-9]|1[0-2])(?:-(0[1-9]|[12]\d|3[01]))?")
# A month by its English name or abbreviation, a period after it allowed.
_MONTH = (r"(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?|"
          r"sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\.?")
_DAY = r"(\d{1,2})(?:st|nd|rd|th)?"
# A date in words, as Zotero's strToDate reads one, and the parts it
# names in order: "August 1, 2020", "1 August 2020", "Aug 2020", and
# PubMed's "2020 Aug 1".
_WORDS_RES = (
    (re.compile(rf"{_MONTH}\s+{_DAY},?\s+(\d{{4}})", re.I), ("month", "day", "year")),
    (re.compile(rf"{_DAY}\s+{_MONTH},?\s+(\d{{4}})", re.I), ("day", "month", "year")),
    (re.compile(rf"{_MONTH},?\s+(\d{{4}})", re.I), ("month", "year")),
    (re.compile(rf"(\d{{4}}),?\s+{_MONTH}(?:\s+{_DAY})?", re.I), ("year", "month", "day")),
)
_MONTH_NAMES = ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec")


def _date_parts(text: str):
    """``(year, month, day)`` as strings ("" for a part not named) of a
    numeric date or one in words, None for anything else."""
    m = _DATE_RE.fullmatch(text)
    if m:
        return m.group(1), m.group(2) or "", m.group(3) or ""
    for rex, names in _WORDS_RES:
        m = rex.fullmatch(text)
        if m:
            parts = dict(zip(names, m.groups()))
            month = str(_MONTH_NAMES.index(parts["month"][:3].lower()) + 1)
            return parts["year"], month, parts.get("day") or ""
    return None


def parse_date(text) -> tuple[str, str]:
    """``(year, date)`` from what the Date row or a source wrote: a year,
    a month or a day sets the year and, when more precise, the ISO date
    (``"2020/3/5"`` → ``("2020", "2020-03-05")``, ``"2020-03"`` →
    ``("2020", "2020-03")``, ``"2020"`` → ``("2020", "")``), and so does a
    date in English words (``"August 1, 2020"``, ``"1 Aug 2020"``,
    ``"March 2020"``). Anything else (``in press``, ``n.d.``, a month that
    is none) is the year as written, at most ``YEAR_MAX`` characters, with
    no date."""
    text = str(text or "").strip()
    parts = _date_parts(text)
    if not parts:
        return text[:YEAR_MAX], ""
    year, month, day = parts
    if not month:
        return year, ""
    date = f"{year}-{int(month):02d}" + (f"-{int(day):02d}" if day else "")
    return (year, date) if _ISO_RE.fullmatch(date) else (text[:YEAR_MAX], "")


def date_agrees(meta: dict) -> str:
    """The record's date while it agrees with its year (an ISO month or
    day of that year), else "": what a citation, a BibTeX ``month`` and the
    popover's Date row read."""
    date = str((meta or {}).get("date") or "")
    year = str((meta or {}).get("year") or "").strip()
    return date if year and _ISO_RE.fullmatch(date) and date.startswith(year + "-") else ""


def clean_cited(key: str, value) -> str:
    """``value`` as a record's ``issue`` or ``journal_abbr`` (``key``):
    stripped, at most ``CITED_MAX`` characters; "" for none."""
    return str(value or "").strip()[:CITED_MAX[key]]


def clean_url(value) -> str:
    """``value`` as a record's address: an http(s) URL with a host, stripped,
    of at most ``URL_MAX`` characters and no whitespace inside; "" for
    anything else (``javascript:``, a relative path, a bare word)."""
    url = str(value or "").strip()
    if len(url) > URL_MAX or re.search(r"\s", url):
        return ""
    return url if re.match(r"https?://[^/?#\s]+", url, re.I) else ""
