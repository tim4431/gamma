"""Where an agent's mark goes (docs/dev/annotations.md "The writing
tools"): the place a ``comment`` or a ``highlight`` names on a page, found
in the page's document as it is now.

- **A passage of a PDF**: a quote with its 1-based PDF page, found by its
  text (``highlight_relocate.place_quote``: that page and the ones either
  side, folded as a relocation reads a file) and drawn over the glyphs it
  covers. It is stored as the file spells it.
- **A range of lines of a text file**: its first and last line (1-based,
  as ``read_page`` numbers them), with the quote of those lines found near
  them (``documents/text_anchors.find_range``, folded as a range's run
  reads a file) when one comes, else the lines whole as they are now.

The quote is the agent's copy of the text, so only an exact copy places
it (as the finders fold text); a passage that reads only like it, however
alike, is the closest one.
- **A note**: its block, for a comment alone.
- **Nothing**: the page itself, its own thread, for a comment alone.

A quote found nowhere is refused, saying the closest passage the finder
saw when it saw one (its words, where it is and how alike it reads): a
mark is never placed by guess. A secrets file's text is never said, and a
quote on one is refused before any match, so no answer says whether the
text is there: its lines are named whole. A
passage or a range becomes a new highlight block, with the properties a
person's selection gives one (``Place.props``); the caller writes it.
"""

from typing import NamedTuple

from .. import highlight_relocate, highlights
from ..ai_context import pdf_path
from ..blocks_store import load_props
from ..documents import text as doctext
from ..documents.model import file_hash, page_document
from ..documents.text_anchors import TEXT_RELOCATE_MAX_BYTES, find_range, prepare
from ..storage import find_upload_file
from . import anchors

QUOTE_MAX = 2000      # characters of a quote an agent names
SAID = 160            # characters of the closest passage a refusal says


class Place(NamedTuple):
    """A place found: ``block_id`` the block a thread goes on ("" for a
    passage or a range, which a new highlight gives); ``props`` that
    highlight's properties (``{quote, pdf_position}`` or ``{quote,
    text_position}``), ``{}`` for a block; ``kind`` ``passage``, ``lines``,
    ``note`` or ``file``; ``page`` a passage's PDF page, ``lines`` a range's
    ``(first, last)``; ``words`` how an answer names it ("page 3, “the
    loss…”", "lines 4–6", "a note", "the whole file")."""
    block_id: str
    props: dict
    kind: str
    words: str
    page: int = 0
    lines: tuple = ()


def _int(args: dict, key: str) -> int | None:
    value = args.get(key)
    if value is None or value == "":
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or int(value) != value:
        raise ValueError(f"{key} is a whole number")
    return int(value)


def _quote(args: dict) -> str:
    quote = args.get("quote")
    if quote is None:
        return ""
    if not isinstance(quote, str):
        raise ValueError("quote is text")
    if len(quote) > QUOTE_MAX:
        raise ValueError(f"a quote holds at most {QUOTE_MAX} characters: quote less")
    return quote.strip()


def _said(text: str) -> str:
    words = " ".join(text.split())
    return f"“{words[:SAID]}{'…' if len(words) > SAID else ''}”"


def find(conn, ws: str, page_id: str, args: dict, *, mark: bool = False) -> Place:
    """The place ``args`` names on the page ``page_id`` (a page of the
    library, its row read from ``conn``): ``quote`` with ``pdf_page`` on a
    PDF, ``line`` (and ``end_line``, and a ``quote`` of those lines) in a
    text file, ``block_id`` a note of the page, or none of them for the page
    itself. ``mark``: for a highlight, which needs a passage or a range.
    ValueError with what to send instead, or with the closest passage for a
    quote found nowhere."""
    row = conn.execute("SELECT properties FROM unified_blocks WHERE id = ?", (page_id,)).fetchone()
    view = page_document(load_props(row[0]) if row else {})
    quote, pdf_page, line = _quote(args), _int(args, "pdf_page"), _int(args, "line")
    block_id = args.get("block_id") if isinstance(args.get("block_id"), str) else ""
    if block_id and block_id != page_id:
        if mark:
            raise ValueError("a highlight marks a passage: give its quote with pdf_page, or a text file's line")
        on = conn.execute("SELECT page_id FROM unified_blocks WHERE id = ?", (block_id,)).fetchone()
        if not on or on[0] != page_id:
            raise ValueError("that block is not a note of this page: read_block lists its notes")
        return Place(block_id, {}, "note", "a note")
    if line is not None:
        if not view or view["format"] != "text":
            raise ValueError("line names lines of a text file, and this page has none: "
                             + ("give the PDF passage's quote and pdf_page" if view and view["format"] == "pdf"
                                else "comment on the page or a note"))
        return _lines(ws, view, line, _int(args, "end_line") or line, quote)
    if quote or pdf_page is not None:
        if not view or view["format"] != "pdf":
            raise ValueError("quote and pdf_page name a passage of a PDF, and this page has none"
                             + (": name a text file's lines with line and end_line" if view and view["format"] == "text"
                                else ""))
        if not quote or pdf_page is None:
            raise ValueError("a PDF passage needs both its quote (verbatim) and its pdf_page (1-based)")
        return _passage(ws, view, quote, pdf_page)
    if mark:
        raise ValueError("a highlight marks a passage: give its quote with pdf_page, or a text file's line")
    return Place(page_id, {}, "file", "the whole file" if view else "the page")


def _passage(ws: str, view: dict, quote: str, pdf_page: int) -> Place:
    path = pdf_path(ws, file_hash(view))
    if path is None:
        raise ValueError("this page's PDF is not stored on the server, so no passage of it can be found")
    position, spelled, confidence = highlight_relocate.place_quote(path, quote, pdf_page)
    if position is None:
        near = (f"; the closest passage reads {_said(spelled)} ({round(confidence * 100)}% alike)" if spelled
                else "")
        raise ValueError(f"that quote is not on PDF page {pdf_page} or beside it{near}. Quote the page as read_page "
                         "gives its text, or look at it with view_pdf_page")
    page = position["pageNumber"]
    words = f"page {page}, {_said(spelled)}"
    return Place("", {"quote": spelled, "pdf_position": position}, "passage", words, page=page)


def _lines(ws: str, view: dict, line: int, end: int, quote: str) -> Place:
    if line < 1 or end < line:
        raise ValueError("line counts from 1, and end_line is not before it")
    path = find_upload_file(view["file"], ws) if view.get("file") else None
    if path is None:
        raise ValueError("this text file's bytes are not on the server, so no lines of it can be marked")
    with open(path, "rb") as f:
        data = f.read(TEXT_RELOCATE_MAX_BYTES + 1)
    if len(data) > TEXT_RELOCATE_MAX_BYTES:
        raise ValueError("this text file is too large to mark")
    decoded = doctext.decode(data)
    if decoded.binary:
        raise ValueError(doctext.NOT_TEXT)
    lines = doctext.split_lines(decoded.text)
    kept = anchors.text_kept(view)
    if line > len(lines):
        raise ValueError(f"the file has {len(lines)} lines")
    if quote and kept:  # refused before any match, so the answer never says whether the text is there
        raise ValueError("this file's text is kept from you: name its lines with line and end_line, without a quote")
    if quote:
        found = find_range(prepare(lines), {"quote": quote, "prefix": " ", "suffix": " ", "line": line,
                                            "end": min(end, len(lines))}, closest=highlight_relocate.CLOSEST_MIN)
        if found is None or not found.exact:
            near = ""
            if found:
                near = (f"; the closest text is at {anchors.line_words(found.line, found.end)}, {_said(found.quote)} "
                        f"({round(found.match * 100)}% alike)")
            raise ValueError(f"that quote is not in the file near {anchors.line_words(line, end)}{near}. Quote the "
                             "lines as read_page prints them, after the number and the tab")
        span = (found.line, found.col, found.end, found.end_col)
        text, prefix, suffix = found.quote, found.prefix, found.suffix
    else:
        span = highlights.clamp_span(lines, (line, 0, min(end, len(lines)), len(lines[min(end, len(lines)) - 1])))
        if span is None:
            raise ValueError(f"{anchors.line_words(line, end)} hold no text to mark")
        text, prefix, suffix = highlights.span_text(lines, span)
    position = highlights.text_position(*span, hash=file_hash(view), prefix=prefix, suffix=suffix)
    first, last = span[0], span[2]
    words = anchors.line_words(first, last) + ("" if kept else f", {_said(text)}")
    return Place("", {"quote": text, "text_position": position}, "lines", words, lines=(first, last))
