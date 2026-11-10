"""Page exports for MCP clients: the Export dialog's page formats, handed
over in the tool result — Markdown as text, a PDF as an embedded file (a
client such as Claude Code saves it to disk and gives the model its path).
Rendering is gamma/routers/export.py's; nothing is stored.
"""

from urllib.parse import urlencode

from fastapi import HTTPException

from .blocks_store import sub_pages, with_sub_pages
from .db import account_name, connect_pages_db, connect_users_db
from .markdown_export import UPLOAD_RE

FORMATS = ("markdown", "pdf", "notes_pdf")
# A bigger file is refused rather than base64-inflated into one JSON
# response; the Export dialog downloads it.
EXPORT_MAX_BYTES = 50 * 1024 * 1024

EXPORT_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "properties": {
        "page_id": {"type": "string", "minLength": 1, "maxLength": 128},
        "format": {"type": "string", "enum": list(FORMATS),
                   "description": "markdown (text), pdf (the page's PDF, annotated) or notes_pdf "
                                  "(the notes typeset as a PDF)"},
        "highlights": {"type": "boolean",
                       "description": "include the highlights — quoted passages, or annotations "
                                      "in the pdf format (default true)"},
        "notes": {"type": "boolean",
                  "description": "include the user's notes — in the pdf format, written on the "
                                 "pages beside their highlights (default true)"},
        "sub_pages": {"type": "boolean",
                      "description": "also export the pages inside this page, at any depth, as the "
                                     "Export dialog's \"Include pages inside\" does (default false)"},
    },
    "required": ["page_id", "format"],
}

EXPORT_DESCRIPTION = (
    "Export one page the way Gamma's Export dialog does. `markdown` returns the page as "
    "Markdown text (title, metadata, highlights as quotes with their PDF page, nested notes); "
    "images and files link to this Gamma server. `pdf` returns the page's PDF with the "
    "highlights as standard annotations and the notes written beside them (both off = the "
    "original PDF); only a page carrying a PDF has one, or one with sheets of paper to write "
    "on (their handwriting drawn, one PDF page each). `notes_pdf` returns the page's notes "
    "typeset as their own PDF document, for any page. A PDF is attached to the result as an "
    "embedded file (application/pdf); save it where the user asked. With sub_pages the pages "
    "inside the page come too: `markdown` returns each page's Markdown in turn, `pdf` a zip of "
    "their annotated PDFs nested as the pages are (application/zip; one PDF among them is that "
    "PDF), `notes_pdf` one PDF with every page. Use read_page or read_block to read a page — "
    "export only when the user wants a file."
)


def export_page(ws: str, base: str, user_id: str, args: dict) -> tuple[str, tuple | None]:
    """One page in one format: ``(text, file)``, where ``file`` is
    ``(uri, media type, bytes)`` for a PDF and None for Markdown (the text is
    the document). ``uri`` is the HTTP export giving the same file to a
    signed-in browser. An annotated PDF's notes are signed with the token
    account's username. Raises ValueError with a message for the model."""
    page_id, fmt = args["page_id"], args["format"]
    highlights, notes = bool(args.get("highlights", True)), bool(args.get("notes", True))
    with connect_pages_db(ws) as conn:
        row = conn.execute("SELECT content FROM unified_blocks WHERE id = ? AND parent_id = 'root'",
                           (page_id,)).fetchone()
        # with ``sub_pages``, the page and every page inside it, each after its parent
        pages = with_sub_pages(conn, [page_id]) if row and args.get("sub_pages") and sub_pages(conn, page_id) \
            else [page_id]
    if not row:
        raise ValueError("No such page in the connected workspace — use a page_id from list_pages or search_library.")
    title = row[0] or "Untitled"
    inside = len(pages) - 1
    along = f" and the {inside} page{'s' if inside != 1 else ''} inside it" if inside else ""
    # Local import: the export router loads the PDF writers, needed only here.
    from .routers.export import annotated_pdf, page_export_file, page_markdown, page_notes_pdf

    flags = {"highlights": int(highlights), "notes": int(notes), **({"sub_pages": 1} if inside else {})}
    media_type = "application/pdf"
    try:
        if fmt == "markdown":
            parts = [page_markdown(ws, page, highlights=highlights, notes=notes) for page in pages]
            query = urlencode({"ws": ws})
            md = "\n\n---\n\n".join(UPLOAD_RE.sub(lambda m: f"{base}/api/uploads/{m.group(1)}?{query}", text)
                                    for text, _ in parts)
            names = ", ".join(name for _, name in parts)
            return (f"Markdown export of “{title}”{along} (file name{'s' if inside else ''} {names}, "
                    f"{'one page after another, parents first, ' if inside else ''}images and files link to "
                    f"this Gamma server and open signed in):\n\n{md}"), None
        with connect_users_db() as conn:
            author = account_name(conn, user_id)
        if fmt == "pdf" and inside:  # GET /pages/{id}/export-pdf?sub_pages=1 gives a browser the same file
            data, name, media_type = page_export_file(ws, page_id, "annotated-pdf", highlights=highlights,
                                                      notes=notes, author=author, sub_pages=True)
            what = "the annotated PDFs" if media_type != "application/pdf" else "the one annotated PDF"
            path = f"/api/pages/{page_id}/export-pdf?" + urlencode({"ws": ws, **flags})
        elif fmt == "pdf":
            data, name, _, _ = annotated_pdf(ws, page_id, highlights=highlights, notes=notes, author=author)
            what = "the annotated PDF" if highlights or notes else "the original PDF"
            path = f"/api/pages/{page_id}/export-pdf?" + urlencode({"ws": ws, **flags})
        else:
            if inside:
                data, name, _ = page_export_file(ws, page_id, "notes-pdf", highlights=highlights, notes=notes,
                                                 sub_pages=True)
            else:
                data, name = page_notes_pdf(ws, page_id, highlights=highlights, notes=notes)
            what = "the notes as a PDF"
            path = f"/api/pages/{page_id}/export?" + urlencode({"ws": ws, "mode": "notes-pdf", **flags})
    except HTTPException as e:
        raise ValueError(f"Could not export “{title}”{along}: {e.detail}.") from None
    if len(data) > EXPORT_MAX_BYTES:
        raise ValueError(f"“{title}”{along} exports to {len(data) // (1024 * 1024)} MB, over this connection's "
                         f"{EXPORT_MAX_BYTES // (1024 * 1024)} MB limit — export it from Gamma's Export dialog.")
    text = (f"Exported {what} of “{title}”{along}: {name} ({max(1, len(data) // 1024)} KB), attached as an "
            f"embedded {media_type} file. Save it under that name unless the user asked for another.")
    return text, (base + path, media_type, data)
