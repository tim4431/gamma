"""cite, the citation records kept with pages, and search_library, a
full-text search of the reachable pages' notes and documents' text through
the two FTS indexes; a search with no hit is tried again with its longest
terms."""

import re

from .. import bibtex as bibtex_mod
from .. import item_types
from ..block_index import fts_query, search_blocks
from ..db import connect_data_db
from ..search import hits as search_hits, indexer
from .base import _load_scoped_page, _scope_pages, _where, gamma_link


# The zero-hit search retry: drop glue words shorter than this, keep at most
# this many of the longest remaining terms.
_RELAX_MIN_TERM_LEN = 3
_RELAX_MAX_TERMS = 3


_CITE_MAX = 50  # pages one cite call formats
_RECORD_FIELDS = (("authors", "Authors"), ("year", "Year"), ("venue", "Venue"), ("volume", "Volume"),
                  ("pages", "Pages"), ("publisher", "Publisher"), ("isbn", "ISBN"), ("doi", "DOI"),
                  ("arxiv_id", "arXiv"))
# What tied a record to its page's PDF (meta.match, routers/metadata.py), as
# the cite tool tells the model: the evidence of a confirmed record, and why
# an unverified one is.
_TIED_BY = {"source": "the paper's own link or its web page's tags", "page": "its web page's citation tags",
            "typed": "an identifier the user typed", "title": "the PDF is its title page (its title, an author by it)",
            "ai": "an AI check against the PDF"}
_UNVERIFIED_WHY = {
    "phrase": "a title search found it, but its title only appears in the PDF as a phrase, so it may be "
              "another work on the same topic",
    "id_only": "an identifier printed in the PDF names it, but nothing ties it to the PDF, so it may be a "
               "work the PDF cites",
    "title_only": "its title is on the PDF's first page, but no author of it was found by the title or the "
                  "title is not set as the PDF's title, so check the title and authors",
    "ai_reading": "an AI read it off the PDF and no registry confirmed it",
}


def _run_cite(conn, ws: str, scope: dict, args: dict):
    """The citation record of in-scope pages: the paper metadata the page
    keeps (properties.meta, from the metadata lookup or a hand edit), its
    BibTeX (the stored rendering, else built from the record as a hand edit
    builds it) and the slide citation when one was made. Nothing is looked
    up or stored — a page without metadata says so."""
    ids = args.get("page_ids") or args.get("page_id") or []
    ids = [ids] if isinstance(ids, str) else ids if isinstance(ids, list) else []
    ids = list(dict.fromkeys(str(i).strip() for i in ids if str(i).strip()))
    if not ids and scope.get("type") == "page":
        ids = [scope.get("page_id")]
    if not ids:
        return "error: name the pages to cite — page_ids from list_pages or search_library", None
    entries, cited, missing = [], [], 0
    for page_id in ids[:_CITE_MAX]:
        loaded, error = _load_scoped_page(conn, scope, {"page_id": page_id})
        if error:
            entries.append(f"- page_id {page_id}: {error}")
            continue
        page_id, title, props, _ = loaded
        meta = props.get("meta") if isinstance(props.get("meta"), dict) else None
        head = f'## "{title}" ({_where(scope, page_id)})'
        if not meta:
            missing += 1
            entries.append(head + "\nNo paper metadata yet — the user can look it up with the (i) "
                                  "button in the Notes panel; do not make a record up.")
            continue
        cited.append(title)
        # The item type (Zotero's name) tells a reference style what to print.
        record = [f"Type: {item_types.item_type(meta)}"]
        for key, label in _RECORD_FIELDS:
            value = meta.get(key)
            if isinstance(value, list):
                value = ", ".join(str(v) for v in value if str(v).strip())
            if str(value or "").strip():
                record.append(f"{label}: {value}")
        lines = [head, " · ".join(record)]
        if meta.get("title") and meta["title"] != title:
            lines.append(f"Paper title: {meta['title']}")
        if meta.get("unverified"):
            why = _UNVERIFIED_WHY.get(meta.get("match"),
                                      "nothing tied this record to the page's PDF (it may be a cited work's, "
                                      "or an AI reading)")
            lines.append(f"Unverified: {why} — say so and suggest checking it before it is cited.")
        elif meta.get("match") in _TIED_BY:
            lines.append(f"Matched by: {_TIED_BY[meta['match']]}")
        lines.append(f"```bibtex\n{bibtex_mod.page_entry(props)}\n```")
        if str(props.get("ppt_cite") or "").strip():
            lines.append("Slide citation: " + props["ppt_cite"].strip())
        entries.append("\n".join(lines))
    if not cited and not missing:
        return "error: none of those pages can be cited here\n" + "\n".join(entries), None
    more = (f"\n(+{len(ids) - _CITE_MAX} more pages not shown — call again with the rest)"
            if len(ids) > _CITE_MAX else "")
    out = ("Citation records kept with the pages (from the metadata lookup or the user's own "
           "edits). Format another style from the fields; never add a field the record lacks.\n\n"
           + "\n\n".join(entries) + more)
    summary = (f"Cited “{cited[0][:60]}”" if len(cited) == 1
               else f"Cited {len(cited)} pages" if cited else "No citation record")
    if missing:
        summary += f" · {missing} without metadata"
    return out, {"kind": "cite", **({"page_id": ids[0]} if len(ids) == 1 else {}), "summary": summary}


def _run_search_library(conn, ws: str, scope: dict, args: dict):
    """FTS snippets from the in-scope pages: their notes (block_fts, current
    with every write) and the text of their documents (text_fts, the PDFs'
    pages and the text files' runs of lines — the hits gamma/search/hits.py
    builds for /api/search too). Notes hits come first, with block ids the
    note tools take; then the documents' hits in the index's one rank
    order, a PDF's with its page number, a file's with its path and line.
    Un-indexed documents are kicked to the background indexer and
    reported, so the model knows results may be incomplete."""
    query = str(args.get("query") or "").strip()
    if not query:
        return "error: empty query", None
    try:
        limit = max(1, min(int(args.get("limit") or 12), 30))
    except (TypeError, ValueError):
        limit = 12
    pages = _scope_pages(conn, scope)
    if not pages:
        return ("No pages are reachable from this chat.",
                {"kind": "search", "summary": f"Searched library for “{query[:60]}” — no pages"})
    docs, doc_pages, titles = search_hits.docs_of(pages)

    def fts(database, text):
        match = fts_query(text)
        found = []
        if not match:
            return found
        for block_id, page_id, snippet in search_blocks(conn, match, limit, pages):
            found.append(f'- note [{block_id}] in "{pages[page_id]["title"][:80]}" '
                         f"({_where(scope, page_id, block_id=block_id)}): {snippet}")
        for hit in search_hits.build(ws, conn, database, match, docs, doc_pages, titles, limit, query=text):
            if hit["source"] == "file":
                url = gamma_link(scope, hit["page_id"], line=hit["line"])
                first, last = hit["lines"]
                found.append(f'- file "{(hit["path"] or hit["title"])[:200]}" l.{hit["line"]} (lines {first}–{last}; '
                             f'page_id {hit["page_id"]}{f"; {url}" if url else ""}): {hit["snippet"]}')
                continue
            ids = doc_pages[hit["doc_id"]]  # one PDF may hang on several pages: link the first
            url = gamma_link(scope, ids[0], pdf_page=hit["page"])
            found.append(f'- PDF "{hit["title"][:80]}" p.{hit["page"]} '
                         f'(page_id {", ".join(ids)}{f"; {url}" if url else ""}): {hit["snippet"]}')
        return found

    relaxed = ""
    missing: list = []
    with connect_data_db(ws) as database:
        if docs:
            missing = indexer.kick(ws, database, docs)
        lines = fts(database, query)
        if not lines:
            # The MATCH ANDs every term, and agents write long natural-language
            # queries — one word the page doesn't use turns a findable passage
            # into zero hits, which the model reads as "the paper is silent".
            # Retry with only the longest words (they carry the meaning) so a
            # miss still points somewhere, clearly labelled as approximate.
            tokens = [t for t in re.split(r"[\s,]+", query) if t]
            terms = sorted((t for t in tokens if len(t) >= _RELAX_MIN_TERM_LEN),
                           key=len, reverse=True)
            for keep in range(min(_RELAX_MAX_TERMS, len(terms)), 0, -1):
                if keep == len(tokens):
                    continue  # same term set as the query that just missed
                lines = fts(database, " ".join(terms[:keep]))
                if lines:
                    relaxed = " ".join(terms[:keep])
                    break
    if lines and relaxed:
        out = (f'Nothing contains all of "{query}". Closest hits for '
               f'"{relaxed}" ({len(lines)}) — verify them by reading before '
               "trusting them:\n" + "\n".join(lines))
    elif lines:
        out = (f'Matches for "{query}" ({len(lines)}; notes first, then the documents\' text, PDF pages '
               "and files' lines in rank order):\n" + "\n".join(lines))
    else:
        out = (f'No notes or document text match "{query}" or any part of it. Try the exact '
               "words the page would use, or read the likely pages directly. If you "
               "still cannot find it, tell the user it is not in their pages — do "
               "not answer from your own knowledge.")
    if missing:
        out += (f"\n({len(missing)} document(s) not indexed yet — indexing started, "
                "search again shortly for complete results)")
    about = "≈" if relaxed else ""
    return out, {"kind": "search",
                 "summary": f"Searched library for “{query[:60]}” — "
                            f"{about}{len(lines)} hit{'s' if len(lines) != 1 else ''}"}


TOOLS = [
    {
        "perm": "read", "kind": "cite", "scopes": ("folder", "page"), "mutating": False, "run": _run_cite,
        "spec": {
            "name": "cite",
            "description": (
                "Citation records of pages: the paper metadata Gamma keeps with each page "
                "(authors, year, venue, volume, pages, publisher, ISBN, DOI, arXiv id), its "
                "BibTeX entry, and the slide citation when one was made. Use it for a "
                "bibliography, a reference list in a given style or BibTeX — format other "
                "styles from the fields — instead of writing records from memory. `page_ids`: "
                f"up to {_CITE_MAX} page ids (in a page chat, default this page). A record may "
                "be marked unverified, and a page may have none."),
            "parameters": {
                "type": "object",
                "properties": {"page_ids": {"type": "array", "items": {"type": "string"}}},
                "required": [],
            },
        },
    },
    {
        "perm": "search", "kind": "search", "scopes": ("folder", "page"), "mutating": False, "run": _run_search_library,
        "spec": {
            "name": "search_library",
            "description": (
                "Full-text search over the pages this chat can reach: the user's notes "
                "(hits name the block id and its page) and the text of their documents "
                "— PDFs (hits name the PDF page number) and text files (hits name the "
                "file's path and the line, l.N, in its run of lines). Pre-built index; "
                "snippets. Matching is literal — a note, a PDF page or a file's run of "
                "lines must contain every word of the query — so prefer 2-4 words the "
                "text would actually use. When nothing matches all words, the closest "
                "hits for a subset of them are returned, clearly labelled. Use it to "
                "find where a topic is discussed, then read_page (with pdf_page set to a "
                "PDF hit's page number, or a file hit as read_page(page_id, from_line=N), "
                "or by the file's path) or read_block (a note hit's block id) to read "
                "the passage in context."),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "limit": {"type": "integer", "description": "max hits, default 12"},
                },
                "required": ["query"],
            },
        },
    },
]
