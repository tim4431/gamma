"""The library rule (docs/dev/work_folders.md "The library rule"), stated
once over a page's properties:

    work_folder(P)      = P.work_folder when it is a non-empty string, else none
    listed(P)           = work_folder(P) is none, or P.library is true
    in_listing(P)       = listed(P), or P.pinned is a non-empty string
    library_document(P) = P.library is true                 for a work folder's file
                          not in_place(page_document(P))     for any other page
    is_library_pdf(P)   = a PDF P owns (own_hash), and library_document(P)

``listed`` is what the library lists (its listing, the root feed, label
counts, the pickers' defaults); ``in_listing`` what the listing route
carries, pinned files included; ``library_document`` what is a paper: one
page per document, the record's lookup, the clipper's match, a backup
merge's match. A page outside every work folder is a library document
unless its document is read in place (an Opened files page). The stamps
(``STAMPS``) are the vault's: ``work_folder`` the work folder's root
folder, ``library`` true or absent, and only beside it, ``file_name`` the
leaf of the page's file.

The ``SQL_*`` conditions say the same over a ``unified_blocks`` row, read
through the generated ``work_folder`` column (db.BLOCK_HOT_COLUMNS), to
splice into WHERE clauses; ``SQL_LIBRARY_PDF`` goes with the ``doc_id``
column, which leaves copies out. Twin of frontend/src/documents/library.js;
tests/shared/library.json holds the two, and the SQL, to the same answers.
"""

from ..db import SQL_IN_LISTING
from ..documents.model import SQL_PDF, in_place, is_pdf, own_hash, page_document

STAMPS = ("work_folder", "library", "file_name")

SQL_LISTED = "(work_folder IS NULL OR json_type(properties, '$.library') = 'true')"
SQL_OUTSIDE_LIBRARY = "json_type(properties, '$.library') IS NOT 'true'"     # of a work folder's file: not listed
SQL_LIBRARY_DOC = ("(CASE WHEN work_folder IS NULL THEN json_extract(properties, '$.doc.at') IS NOT 'path' "
                   "ELSE json_type(properties, '$.library') = 'true' END)")
SQL_LIBRARY_PDF = f"({SQL_PDF} AND {SQL_LIBRARY_DOC})"

__all__ = ["STAMPS", "SQL_IN_LISTING", "SQL_LISTED", "SQL_OUTSIDE_LIBRARY", "SQL_LIBRARY_DOC", "SQL_LIBRARY_PDF", "work_folder_of",
           "listed", "in_listing", "library_document", "is_library_pdf", "follows"]


def work_folder_of(props) -> str:
    """The work folder a page is a file of, "" for none."""
    value = props.get("work_folder") if isinstance(props, dict) else None
    return value if isinstance(value, str) else ""


def listed(props) -> bool:
    return not work_folder_of(props) or props.get("library") is True


def in_listing(props) -> bool:
    pinned = props.get("pinned") if isinstance(props, dict) else None
    return listed(props) or (isinstance(pinned, str) and pinned != "")


def library_document(props) -> bool:
    if work_folder_of(props):
        return props.get("library") is True
    return not in_place(page_document(props))


def is_library_pdf(props) -> bool:
    view = page_document(props)
    return is_pdf(view) and own_hash(view) != "" and library_document(props)


def follows(props, mark: dict | None) -> bool:
    """Whether a person's renames, moves, trash and restore of the page
    reach the disk: a work folder's note (no document) where the vault
    pulls (its mark's ``pull``). Everything else in a work folder follows
    the disk alone."""
    return (bool(work_folder_of(props)) and props.get("doc") is None
            and isinstance(mark, dict) and mark.get("pull") is True)
