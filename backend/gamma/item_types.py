"""What kind of work a paper record describes (``meta.kind``), under the
names Zotero gives its item types.

One vocabulary for every source and every output. The registries name a work
their own way (Crossref's ``proceedings-article``, CSL's ``paper-conference``),
the AI classifier picks from the list, a hand edit picks from the metadata
popover, and a Zotero import reads ``z:itemType``. All of them land on one of
``ITEM_TYPES``. The outputs translate from it: the BibTeX entry type
(``bibtex.build_entry``), the Zotero RDF item (``zotero_export``), and the
unverified-metadata warning (``CHECKABLE``).

The names are Zotero's because Zotero's item types are CSL's types under
another spelling, the vocabulary reference managers and citation styles
share, and because a Zotero round trip is then the identity. Only the types a
paper library holds are kept. Anything else Zotero names becomes
``document``; a registry type with no counterpart is left out, and the
record's shape decides (``infer``).

Records stored before these names (``paper``, ``notes``, ``slides``,
``other``) are read through ``normalize``, as is an AI reply written to a
customised prompt; nothing in storage is rewritten.
``frontend/src/shared/lib/itemTypes.js`` holds the same table for the
interface. Keep the two in sync.
"""

ITEM_TYPES = (
    "journalArticle", "conferencePaper", "preprint", "bookSection", "book",
    "thesis", "report", "presentation", "document",
)

# Types a registry can confirm. AI metadata for one of these that nothing
# tied to the PDF is flagged; a thesis, a report, slides or notes rarely
# have a registry record to check against, so they are not.
CHECKABLE = frozenset({"journalArticle", "conferencePaper", "preprint", "bookSection", "book"})

_BY_NAME = {t.lower(): t for t in ITEM_TYPES}

# The kinds stored before Zotero's names. "paper" meant any article
# (journal, conference or preprint), so the record's shape resolves it, as
# it does a record that names no kind.
_LEGACY = {"paper": "", "notes": "document", "slides": "presentation", "other": "document"}

# Crossref's work types, and CSL's (doi.org answers a DataCite DOI, arXiv's
# included, in CSL's). Containers (journal, journal-issue, proceedings,
# book-series) and types a paper library does not cite are left out.
_REGISTRY = {
    "journal-article": "journalArticle", "article-journal": "journalArticle",
    "proceedings-article": "conferencePaper", "paper-conference": "conferencePaper",
    "posted-content": "preprint", "article": "preprint",
    "book-chapter": "bookSection", "book-section": "bookSection", "book-part": "bookSection",
    "reference-entry": "bookSection", "chapter": "bookSection",
    "entry-encyclopedia": "bookSection", "entry-dictionary": "bookSection",
    "book": "book", "monograph": "book", "edited-book": "book", "reference-book": "book",
    "dissertation": "thesis", "thesis": "thesis",
    "report": "report",
    "speech": "presentation",
}


def normalize(value) -> str:
    """A stored or received kind as an item type, matched without regard to
    case. "" when it names none: missing, unknown, or the old ``paper``."""
    name = str(value or "").strip().lower()
    return _BY_NAME.get(name) or _LEGACY.get(name, "")


def infer(meta: dict) -> str:
    """The item type a record's fields imply when it names none: a publisher
    without a venue is a book, an arXiv id without a published venue a
    preprint, and anything else a journal article, so a record of unknown
    kind still gets the warning a paper gets."""
    venue = str(meta.get("venue") or "").strip()
    if meta.get("publisher") and not venue and not meta.get("arxiv_id"):
        return "book"
    if meta.get("arxiv_id") and (not venue or venue.lower().startswith("arxiv")):
        return "preprint"
    return "journalArticle"


def item_type(meta: dict | None) -> str:
    """The item type of a record: the one it names, else ``infer``."""
    meta = meta or {}
    return normalize(meta.get("kind")) or infer(meta)


def from_registry(work_type) -> str:
    """A Crossref or CSL work type as an item type, "" when it has none."""
    return _REGISTRY.get(str(work_type or "").strip().lower(), "")


def from_zotero(value) -> str:
    """A Zotero ``z:itemType`` as an item type: itself when it is one of
    ours, "" for an attachment or a missing type (a standalone file has no
    work type yet), and ``document`` for any other Zotero type."""
    name = str(value or "").strip()
    if not name or name == "attachment":
        return ""
    return name if name in ITEM_TYPES else "document"


def publisher(meta: dict) -> str:
    """Who published the work: the record's ``publisher``, or, for a thesis
    or a report that names none, its venue. The AI was asked for a
    publisher on books only before, so an older record of a thesis holds
    the university as its venue."""
    name = str(meta.get("publisher") or "").strip()
    if not name and item_type(meta) in ("thesis", "report"):
        name = str(meta.get("venue") or "").strip()
    return name
