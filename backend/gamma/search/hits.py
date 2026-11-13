"""The one hit builder of the document text index: what ``GET /api/search``,
``POST /api/document-search`` and the agent's ``search_library`` list for
a match in the index, in the shapes of docs/dev/api.md "Search":

    pdf:  {source: "pdf", block_id: page_id, page_id, doc_id, title, page, snippet}
    file: {source: "file", block_id: page_id, page_id, doc_id, title, path, work_folder,
           format: "text", language, line, lines: [first, last], snippet}

A PDF hit names the page of the PDF (FTS5's snippet); a file hit names the
line the query matched in its run (``text.locate``, FTS5's token rule) and
the run's span, with the matched line as its snippet."""

import json

from ..blocks_store import load_props
from ..documents import text
from ..documents.model import page_document
from ..work_folders import paths
from ..work_folders.library import work_folder_of
from ..work_folders.marks import Tree
from . import index
from .index import Doc


class Found(list):
    """The hits ``build`` made, in rank order; ``more`` when the index holds
    another hit past them (the next page's cursor is due)."""
    more = False


def docs_of(found: dict, *, keep=None, formats=text.INDEXED_FORMATS) -> tuple[dict, dict, dict]:
    """The maps ``build`` takes, from ``root_pages``-shaped ``found``
    ({page_id: {title, doc_id, format, ext, at, name}}): ``docs`` {hash:
    Doc} of the documents of ``formats`` the pages own, ``pages`` {hash:
    [page ids]} in ``found``'s order (every page of a hash: two files of
    equal bytes are two hits), ``titles`` {page_id: title}. ``keep`` (a set
    of page ids, None for all) leaves the other pages out, and a secrets
    file's page (``text.is_secret`` of its name) is always left out: the
    index is keyed by the bytes, the rule by the name, so a file renamed to
    ``.env`` after its text was indexed is still never a hit."""
    docs, pages, titles = {}, {}, {}
    for page_id, info in found.items():
        hash = info.get("doc_id") or ""
        if not hash or info.get("format") not in formats or (keep is not None and page_id not in keep):
            continue
        if info.get("format") == "text" and text.is_secret(info.get("name") or ""):
            continue
        docs.setdefault(hash, Doc(hash, info["format"], info.get("ext") or ".pdf", info.get("at") or "store"))
        pages.setdefault(hash, []).append(page_id)
        titles[page_id] = info.get("title") or "Untitled"
    return docs, pages, titles


def build(ws: str, pages_conn, data_conn, match: str, docs: dict, pages: dict, titles: dict, limit: int, *,
          offset: int = 0, per_doc: int = 0, query: str, tree: Tree | None = None, fan_out: bool = False) -> Found:
    """The hits of ``match`` (an FTS MATCH, ``block_index.fts_query(query)``)
    in the documents ``docs`` ({hash: Doc}), ``limit`` of the index's from
    the ``offset``-th on (``per_doc`` its rounds, ``index.search``): a PDF
    unit is the pdf hit of the hash's first page (``fan_out``: of every
    page of the hash), a text unit the file hit of every page of the hash
    (``pages`` {hash: [page ids]}, ``titles`` {page_id: title}), its line
    found with ``text.locate`` over the unit's text for ``query``. The file
    pages' properties (their path, work folder and language) are read in
    one query over the hit pages alone, the folder ``tree`` once (read here
    when not given); a page whose name is a secrets file's now
    (``text.is_secret``) gets no file hit, whatever the maps say. The only
    place the hit dicts are made."""
    hits = index.search(data_conn, match, limit + 1, docs, offset, per_doc)
    out = Found()
    out.more = len(hits) > limit
    hits = hits[:limit]
    files = [h for h in hits if docs[h.doc_id].format == "text"]
    props, lines = {}, {}
    if files:
        lines = index.doc_lines(data_conn, {h.doc_id for h in files})
        ids = [page_id for h in files for page_id in pages.get(h.doc_id, ())]
        props = {page_id: load_props(raw) for page_id, raw in pages_conn.execute(
            "SELECT id, properties FROM unified_blocks WHERE id IN (SELECT value FROM json_each(?))",
            (json.dumps(ids),))}
        if tree is None:
            tree = Tree.read(pages_conn)
    for hit in hits:
        ids = pages.get(hit.doc_id) or []
        if docs[hit.doc_id].format != "text":
            out += [{"source": "pdf", "block_id": page_id, "page_id": page_id, "doc_id": hit.doc_id,
                     "title": titles[page_id], "page": hit.unit, "snippet": hit.snippet}
                    for page_id in (ids if fan_out else ids[:1])]
            continue
        first, last = text.unit_lines(hit.unit, lines.get(hit.doc_id) or hit.unit * text.RUN_LINES)
        line, snippet = text.locate(hit.content, query, first)
        for page_id in ids:
            page_props = props.get(page_id) or {}
            view = page_document(page_props) or {}
            if text.is_secret(view.get("name") or ""):
                continue
            out.append({"source": "file", "block_id": page_id, "page_id": page_id, "doc_id": hit.doc_id,
                        "title": titles[page_id],
                        "path": paths.page_path(tree, page_props, title=titles[page_id]),
                        "work_folder": work_folder_of(page_props), "format": "text",
                        "language": view.get("language"), "line": line, "lines": [first, last],
                        "snippet": snippet})
    return out
