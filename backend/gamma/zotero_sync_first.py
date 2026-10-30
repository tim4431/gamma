"""The first link of an existing workspace (see
docs/dev/zotero_sync.md#first-link-and-force): the pairs its pages and the
library's items make, written as rows before anything is reconciled
(``first_match``); the note pages paired with Zotero's standalone notes
when notes access comes after the link (``first_notes``); and the link
dialog's preview of a first link, by the same match
(``preview_first_link``).

Of the engine's parts it imports only gamma/zotero_sync_reads.py and
gamma/zotero_sync_rows.py.
"""

from . import storage, zotero_local, zotero_merge
from .blocks_store import (FOLDERS, LABELS, attachment_file, filing, label_names, load_props, page_attachment,
                           tree_rows, write_lock)
from .db import connect_pages_db
from .logbuf import log
from .zotero_annotations import file_md5
from .zotero_sync_reads import file_sig
from .zotero_sync_rows import Round, new_row, write_rows
from .zotero_web import item_uri

_is_pdf = zotero_merge.is_pdf_attachment  # a PDF attachment of a file link mode
_size = zotero_merge.stored_size          # the bytes of an attachment's stored file, as Zotero lists them
# A first link reads the whole library page after page in the order the objects were added, the trash with them
# (``ZoteroClient.children`` reads so too): an object edited or trashed while the pages are read keeps its place,
# so no page skips one or reads it twice, as Zotero's default order (the last modified first) would. What is in
# the trash takes no part (``match_inputs``, ``_note_pairs``)
WHOLE_READ = {"includeTrashed": 1, "sort": "dateAdded", "direction": "asc"}


def first_match(r: Round) -> None:
    """The pairs an existing workspace's pages and the library's items make
    (``zotero_merge.match``: one to one, by Zotero key, DOI, arXiv id, PDF
    MD5, then title and year), written as rows in one transaction before
    anything is reconciled, each marked ``first`` so its
    differences are decided by ``adopt``; a pair whose page holds one of
    the item's PDFs (MD5) has that attachment bound too. The library's
    top-level items and attachments are read whole (``WHOLE_READ``). Only
    pages and items without rows take part, so a match run again after an
    interruption pairs nothing twice."""
    items, _ = r.client.paged(f"/{r.library}/items/top", WHOLE_READ)
    attachments, _ = r.client.paged(f"/{r.library}/items", {"itemType": "attachment", **WHOLE_READ})
    with connect_pages_db(r.ws) as conn:
        known = {row[0] for row in conn.execute("SELECT key FROM zotero_objects")}
        pages, candidates, pdfs = match_inputs(conn, r.ws, items, attachments)
    found = zotero_merge.match(pages, candidates, library=r.library)
    notes = _note_pairs(r.library, items, pages, known, {m.page_id for m in found})
    if not found and not notes:
        return
    by_key = {c["key"]: c for c in candidates}
    by_page = {p["id"]: p for p in pages}
    rows = []
    for pair in found:
        data, page = by_key[pair.key]["data"], by_page[pair.page_id]
        detail = {"first": True, "matched": pair.reason}
        if data.get("itemType") == "attachment":
            rows.append(new_row(pair.key, "attachment", page_id=pair.page_id, block_id=pair.page_id,
                                zotero_type="attachment", detail={**detail, "md5": "", "gmd5": ""}))
            continue
        rows.append(new_row(pair.key, "item", page_id=pair.page_id, block_id=pair.page_id,
                            zotero_type=data.get("itemType") or "", detail=detail))
        same = next((a for a in pdfs.get(pair.key, []) if str(a["data"].get("md5") or "").lower() == page["md5"]), None)
        if same is not None and page["md5"]:
            doc_id = page["doc_id"]
            rows.append(new_row(same["key"], "attachment", page_id=pair.page_id, block_id=pair.page_id,
                                parent=pair.key, zotero_type="attachment",
                                detail={"md5": page["md5"], "gmd5": page["md5"], "doc_id": doc_id,
                                        "fsig": file_sig(r.ws, doc_id)}))
    rows += notes
    _write_pairs(r, rows)
    log.info(f"[zotero] {r.ws}: a first link matched {len(found) + len(notes)} pages with items")


def first_notes(r: Round) -> None:
    """Notes access granted to a link whose key saw none (``first ==
    "notes"``), before anything is listed: Zotero's standalone notes (read
    whole, ``WHOLE_READ``) paired with the note pages that name them (a link's
    before it was made again, an import's), as a first link pairs them
    (``_note_pairs``), so none is made a page a second time; each pair
    takes Zotero's side, as a first link's does. Only notes and pages
    without rows take part, so a pass run again after an interruption
    pairs nothing twice."""
    notes, _ = r.client.paged(f"/{r.library}/items/top", {"itemType": "note", **WHOLE_READ})
    with connect_pages_db(r.ws) as conn:
        known = {row[0] for row in conn.execute("SELECT key FROM zotero_objects")}
        taken = {row[0] for row in conn.execute("SELECT page_id FROM zotero_objects WHERE page_id != '' "
                                                "AND block_id = page_id")}
        pages = [{"id": page_id, "props": load_props(raw)} for page_id, raw in conn.execute(
            "SELECT id, properties FROM unified_blocks WHERE parent_id = 'root' AND (json_extract(properties, "
            "'$.zotero_key') IS NOT NULL OR json_extract(properties, '$.zotero_note') IS NOT NULL)").fetchall()
            if page_id not in taken]
    rows = _note_pairs(r.library, notes, pages, known, set())
    if rows:
        _write_pairs(r, rows)
        log.info(f"[zotero] {r.ws}: notes access paired {len(rows)} note pages with their notes")


def _note_pairs(library: str, items, pages, known, paired: set) -> list[dict]:
    """The rows of the pairs note pages and standalone notes make, by the
    key a page names (an import's, a restore's, a link's before it was made
    again), each marked ``first``: ``items`` Zotero's top-level items,
    ``pages`` ``[{id, props}]``, ``known`` the keys that have rows,
    ``paired`` the pages paired already (each new pair's page added)."""
    by_uri: dict[str, list] = {}
    for page in pages:
        for uri in dict.fromkeys((page["props"].get("zotero_key"), page["props"].get("zotero_note"))):
            if uri and isinstance(uri, str):
                by_uri.setdefault(uri, []).append(page)
    rows = []
    for obj in items:
        data = obj.get("data") or {}
        if obj.get("key") in known or data.get("deleted") or data.get("itemType") != "note":
            continue
        page = next((p for p in by_uri.get(item_uri(library, obj["key"]), ()) if p["id"] not in paired), None)
        if page is not None:
            paired.add(page["id"])
            rows.append(new_row(obj["key"], "note", page_id=page["id"], block_id=page["id"], zotero_type="note",
                                detail={"first": True, "matched": "zotero_key"}))
    return rows


def _write_pairs(r: Round, rows: list[dict]) -> None:
    """Pairs' rows in one transaction, but for keys that got rows meanwhile."""
    with connect_pages_db(r.ws) as conn:
        write_lock(conn)
        present = {row[0] for row in conn.execute("SELECT key FROM zotero_objects")}
        write_rows(conn, [row for row in rows if row["key"] not in present])
        conn.commit()


# --- what the first link pairs, and the link dialog's preview of it ---------------------

def match_inputs(conn, ws: str, items, attachments, *, sizes=None) -> tuple[list, list, dict]:
    """What ``zotero_merge.match`` pairs at a first link (and the link
    dialog's preview of one): ``(pages, candidates, pdfs)``. ``pages``: the
    workspace's pages no row holds, each with its stored PDF (``doc_id``,
    ``path``, ``md5``; only a file whose size is in ``sizes`` is read when
    ``sizes`` is given, a size no Zotero PDF has meaning no MD5 can match);
    ``candidates``: the library's top-level items (``items``) no row holds,
    a regular item with its PDFs' MD5s, a standalone PDF with its own;
    ``pdfs``: ``{item key: [its PDF attachments]}`` of ``attachments``."""
    known = {row[0] for row in conn.execute("SELECT key FROM zotero_objects")}
    taken = {row[0] for row in conn.execute(
        "SELECT page_id FROM zotero_objects WHERE kind IN ('item', 'attachment') AND page_id != ''")}
    pages = []
    for page_id, created, title, raw in conn.execute(
            "SELECT id, created_at, content, properties FROM unified_blocks WHERE parent_id = 'root'").fetchall():
        if page_id in taken:
            continue
        props = load_props(raw)
        doc_id = attachment_file(page_attachment(props))
        path = storage.open_path(ws, f"{doc_id}.pdf") if doc_id else None
        hashed = path is not None and (sizes is None or path.stat().st_size in sizes)
        pages.append({"id": page_id, "created_at": created, "title": title or "", "props": props,
                      "doc_id": doc_id if path else "", "path": path, "md5": file_md5(path) if hashed else ""})
    pdfs: dict[str, list] = {}
    for obj in attachments:
        data = obj.get("data") or {}
        if _is_pdf(data) and not data.get("deleted") and data.get("md5") and data.get("parentItem"):
            pdfs.setdefault(data["parentItem"], []).append(obj)
    candidates = []
    for obj in items:
        data = obj.get("data") or {}
        if obj.get("key") in known or data.get("deleted") or data.get("itemType") == "note":
            continue
        if data.get("itemType") == "attachment":
            if _is_pdf(data) and data.get("md5"):
                candidates.append({"key": obj["key"], "data": data, "md5": data["md5"]})
            continue
        candidates.append({"key": obj["key"], "data": data,
                           "md5s": [a["data"]["md5"] for a in pdfs.get(obj["key"], [])]})
    return pages, candidates, pdfs


def preview_first_link(conn, ws: str, library: str, items, attachments, collections) -> dict:
    """What a first link of the workspace would pair and write to Zotero,
    for the link dialog (``zotero_sync.preview``), by the first link's own
    match (``match_inputs``: a page's PDF is read only when a Zotero PDF has
    its size): ``matches`` (pages that pair with an item), ``to_send`` and
    ``to_send_bytes`` (paper pages that go as new items, with their PDFs'
    bytes); and what the first rounds add on Zotero's side whatever
    ``adopt`` says (a value or member only one side has goes to the other):
    ``collections_to_add`` (folders no collection's path names),
    ``tags_to_add`` (labels of paired pages their items lack), ``notes_to_send``
    (paired or new pages whose own notes go as a "Notes from Gamma" note);
    of the highlights no Zotero annotation names, ``highlights_to_send``
    those on paired pages holding their item's PDF (they go whatever the
    uploads) and ``highlights_new`` those on pages whose PDF the link sends
    (a new page's, a paired page's whose item has no PDF in Zotero), which
    go once their PDF does, so only with uploads on."""
    sizes = {_size(a) for a in attachments
             if _is_pdf(a.get("data") or {}) and (a.get("data") or {}).get("md5")}
    pages, candidates, pdfs = match_inputs(conn, ws, items, attachments, sizes=None if 0 in sizes else sizes)
    found = zotero_merge.match(pages, candidates, library=library)
    by_page, by_key = {p["id"]: p for p in pages}, {c["key"]: c for c in candidates}
    matched = {m.page_id: m.key for m in found}
    to_send = [p for p in pages if p["id"] not in matched and zotero_merge.is_paper(p["props"])]
    with_pdf = {(a.get("data") or {}).get("parentItem") for a in attachments
                if _is_pdf(a.get("data") or {}) and not (a.get("data") or {}).get("deleted")}
    known = label_names(conn)
    tags = 0
    for page_id, key in matched.items():
        have = set(zotero_merge.tag_names(by_key[key]["data"], auto_tags=True))
        tags += len({zotero_merge.fold_tag(known[lid]) for lid in filing(by_page[page_id]["props"], LABELS)
                     if lid in known and zotero_merge.fold_tag(known[lid])} - have)
    notes = highlights = new_highlights = 0
    for page in [by_page[pid] for pid in matched] + to_send:
        view = zotero_local.read_page(conn, page["id"])
        if view is None:
            continue
        notes += view.gamma_notes is not None
        if not page["doc_id"]:
            continue
        sent = False  # a new item's PDF goes with it (uploads on), its highlights after it
        if page["id"] in matched:
            item = by_key[matched[page["id"]]]
            md5s = {str(m).lower() for m in item.get("md5s") or [item.get("md5")] if m}
            sent = bool(page["md5"]) and page["md5"] in md5s
            if not sent and (item["data"].get("itemType") == "attachment" or item["key"] in with_pdf):
                continue  # Zotero's PDF is another file: Gamma's highlights stay off it
        n = sum(1 for a in view.annotations if not any(
            (a.block.get("properties") or {}).get(k) for k in ("zotero_annotation", "imported_annot")))
        if sent:
            highlights += n
        else:
            new_highlights += n
    folders = {fid: (name, parent) for fid, parent, name, _ in tree_rows(conn, FOLDERS)}
    names = {c.get("key"): ((c.get("data") or {}).get("name") or "", (c.get("data") or {}).get("parentCollection"))
             for c in collections if not (c.get("data") or {}).get("deleted")}
    paired = zotero_merge.match_paths(_paths(folders, FOLDERS), _paths(names, False))
    return {"matches": len(found), "to_send": len(to_send),
            "to_send_bytes": sum(p["path"].stat().st_size for p in to_send if p["path"]),
            "collections_to_add": len(folders) - len(paired), "tags_to_add": tags, "notes_to_send": notes,
            "highlights_to_send": highlights, "highlights_new": new_highlights}


def _paths(tree: dict, top) -> dict:
    """``{id: (names from the top)}`` of ``{id: (name, parent id)}`` (a
    parent of ``top``, "" or None is the top), oldest first as given."""
    out = {}
    for node in tree:
        names, at, seen = [], node, set()
        while at in tree and at not in seen:
            seen.add(at)
            names.append(tree[at][0])
            at = tree[at][1]
            if at in (top, "", None):
                break
        out[node] = tuple(reversed(names))
    return out
