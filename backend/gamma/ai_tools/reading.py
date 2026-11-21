"""The reading tools of a page's notes: list_pages and list_folders (a
folder chat's), read_page (a page's notes, highlights and open comment
threads, and its document's text: a PDF's by pages, a text file's lines by
number, a work folder's file by its path too), read_block (a page's note
outline with block ids) and read_chats (the AI chat kept with a page or a
folder)."""

import json

from ..ai_pictures import parts as _parts, page_doc_id
from ..ai_context import (MAX_AREA_CROPS, SECRET_TEXT, area_highlight, chip_lines, handwriting_label, is_text_document,
                          page_report_section, paths_text, picture_text, placed_label, range_words, render_area_crops,
                          under_sheet)
from ..blocks_store import (FOLDERS, LABELS, PATH_SEP, fetch_subtree, filing, folder_paths, folder_subtree_ids,
                            label_by_name, label_names, load_props, page_parents, top_of, tree_parents)
from ..documents.model import file_hash, is_listed, is_pdf, page_document, pdf_file_hash
from ..annotations import anchors
from ..notebook import is_sheet
from ..work_folders import paths as work_paths
from ..work_folders.library import listed, work_folder_of
from ..work_folders.marks import Tree
from .base import (_LIST_CAP, _NOTE_SNIPPET, _PAGE_ID_ARG, _TRUNCATED_MARK, _filed_in, _find_folder,
                   _load_scoped_block, _load_scoped_page, _reach, _read_cap, _scope_folder, _where, _window_args,
                   gamma_link, notes_seen)


# How a passage is marked, as a page's outline names it: a highlight block's
# style (frontend shared/model/markStyle.js) → its word; the wash, no style,
# is a highlight.
_MARK_WORDS = {"underline": "underline", "squiggly": "wavy underline", "strikeout": "strikethrough"}


def _list_limit(value) -> int:
    """list_pages' ``limit``: the lines one call shows, 1 to ``_LIST_CAP``;
    ``_LIST_CAP`` when it is left out, 0 or no number."""
    try:
        n = int(value)
    except (TypeError, ValueError, OverflowError):
        return _LIST_CAP
    return min(n, _LIST_CAP) if n > 0 else _LIST_CAP


def _call_text(name: str, args: dict) -> str:
    """A call as the model writes one, for a result that names the call to
    make next: ``list_pages(label="ml", after="x1")``."""
    return f"{name}(" + ", ".join(f"{k}={json.dumps(v, ensure_ascii=False)}" for k, v in args.items()) + ")"


def _page_line(paths: dict, names: dict, page_id: str, content, props: dict, folders: list,
               page_labels: list, updated, parent: str, *, tree: Tree | None = None) -> str:
    """One list_pages line: the page's id, title, a work folder's file's
    path in it (``work_folders.paths.page_path`` over ``tree``), its
    document's format (a text file's with its language; ``listed`` for one
    listed without its bytes) and size, the page it is inside (``parent``,
    a sub-page's) or else its folder paths, labels, cached paper metadata
    and last-update date."""
    view = page_document(props)
    bits = [f"id={page_id}", f'title="{(content or "Untitled")[:120]}"']
    path = work_paths.page_path(tree, props) if tree and work_folder_of(props) else ""
    if path:
        bits.append(f"path={json.dumps(path[:300], ensure_ascii=False)}")
    if not view:
        bits.append("attachments=[]")
    else:
        kind = view["format"] + (f":{view['language']}" if view["language"] else "")
        bits.append(f"attachments=[{kind}{' listed' if is_listed(view) else ''}]")
        if view["size"] is not None:
            bits.append(f"size={view['size']}")
    if parent:
        bits.append(f"parent={parent}")
    elif folders:
        bits.append("folders=" + json.dumps(paths_text(paths, folders), ensure_ascii=False))
    if page_labels:
        bits.append("labels=" + json.dumps([names[lab] for lab in page_labels], ensure_ascii=False))
    meta = props.get("meta") or {}
    authors = [a for a in (meta.get("authors") or []) if str(a).strip()]
    if authors or meta.get("year") or meta.get("venue"):
        first = str(authors[0]).split()[-1] if authors else ""
        who = f"{first} et al." if len(authors) > 1 else first
        bits.append("meta: " + ", ".join(
            x for x in (who, str(meta.get("year") or ""), str(meta.get("venue") or "")[:40]) if x))
    if updated:
        bits.append(f"updated {str(updated)[:10]}")
    return "- " + " | ".join(bits)


def _run_list_pages(conn, ws: str, scope: dict, args: dict):
    """The pages the chat reaches, one line each, most recently added first:
    library order backwards (``position``, which renaming, refiling,
    labelling or editing a page never changes). A call shows at most
    ``limit`` of them, and ``after`` (a page id) goes on after that page's
    place whatever the filters match now — so a long listing reads in
    batches that neither skip nor repeat a page while the agent changes
    them. Filters by label, folder, title and the page a sub-page is in;
    ``list_labels`` lists the labels with their page counts instead, the
    busiest ``limit`` of them. A sub-page is filed where its top page is
    (blocks_store's filing rule), so the folder filters and the chat's
    folder reach it through that page. At the library root the pages are
    those the library lists (``work_folders.library.listed``, by the top):
    a work folder's files come only where the chat's folder or ``folder``
    names one they are filed in, and the label counts and ``label=`` count
    listed pages."""
    reach = _reach(conn, scope)
    paths, names = folder_paths(conn), label_names(conn)
    path = PATH_SEP.join(paths.get(_scope_folder(scope), []))
    # Optional filters, so "papers labeled X" is one small call instead of a
    # full dump the model has to sift by eye.
    label = str(args.get("label") or "").strip()
    wanted = set(label_by_name(conn, label)) if label else None
    title_raw = str(args.get("title_contains") or "").strip()
    title_q = title_raw.lower()
    parent = str(args.get("parent") or "").strip()
    folder_raw = str(args.get("folder") or "").strip()
    sub, below = "", None
    if folder_raw:
        found, error = _find_folder(conn, scope, folder_raw)
        if error:
            return error, None
        if found[0]:
            sub, below = found[1], folder_subtree_ids(conn, found[0])
    want_labels = bool(args.get("list_labels"))
    limit = _list_limit(args.get("limit"))
    after = str(args.get("after") or "").strip()
    cursor = None
    if after:
        if want_labels:
            return ("error: `after` goes on through pages, not labels — list_labels shows the labels on "
                    'the most pages first, and list_pages(label="…") finds any label by its name'), None
        # The page's place, not its line: it may no longer match the
        # filters (the agent renamed or refiled it), and it still marks
        # where the last batch stopped.
        row = conn.execute("SELECT parent_id, position FROM unified_blocks WHERE id = ?", (after,)).fetchone()
        if not row or row[0] != "root":
            return (f'error: "{after}" is no page in the library now — pass `after` the id of the last '
                    "page listed that still is, or list again without it"), None
        cursor = (row[1], after)
    # Labels exist on their own: the library's every label counts, a
    # folder's only those its pages carry.
    label_counts: dict[str, int] = dict.fromkeys(names, 0) if reach is None else {}
    context = scope.get("context_pages") or []
    rows = conn.execute("SELECT id, content, properties, updated_at, position FROM unified_blocks "
                        "WHERE parent_id = 'root' ORDER BY position DESC, id DESC").fetchall()
    props_of = {row[0]: load_props(row[2]) for row in rows}
    parents = page_parents(conn)
    matches, earlier = [], 0
    for page_id, content, _, updated, position in rows:
        props = props_of[page_id]
        top = props_of[top_of(parents, page_id)]
        folders = [f for f in filing(top, FOLDERS) if f in paths]
        if page_id not in context and not _filed_in(reach, folders):
            continue
        # a file the library does not list: only where a folder named (the chat's, or `folder`) files it
        if page_id not in context and reach is None and not listed(top) and not (below and below & set(folders)):
            continue
        page_labels = [lab for lab in filing(props, LABELS) if lab in names]
        if want_labels:
            for lab in page_labels:
                label_counts[lab] = label_counts.get(lab, 0) + 1
            continue
        if wanted is not None and not wanted.intersection(page_labels):
            continue
        if below is not None and not below.intersection(folders):
            continue
        if title_q and title_q not in (content or "Untitled").lower():
            continue
        if parent and parents.get(page_id) != parent:
            continue
        if cursor and (position, page_id) >= cursor:
            earlier += 1  # listed already: at or before the page `after` names
            continue
        matches.append((page_id, content, props, folders, page_labels, updated, parents.get(page_id, "")))
    where = f"“{path}”" if path else "the library"
    if want_labels:
        ranked = sorted(label_counts.items(), key=lambda kv: (-kv[1], names[kv[0]]))
        out_lines = [f'- label "{names[lab]}": {n} page{"s" if n != 1 else ""}' for lab, n in ranked[:limit]]
        rest = len(ranked) - len(out_lines)
        action = {"kind": "list", "summary": (f"Listed {len(out_lines)} of {len(ranked)} labels in {where}"
                                              if rest else f"Listed {len(ranked)} labels in {where}")}
        if not out_lines:
            return "No labels in scope (folders: list_folders).", action
        fewest = ranked[len(out_lines)][1] if rest else 0
        more = (f'\n[… {rest} more label{"s" if rest != 1 else ""}, on {fewest} page{"s" if fewest != 1 else ""} '
                'or fewer each — list_pages(label="…") finds any label by its name]') if rest else ""
        return "Labels in scope (with page counts):\n" + "\n".join(out_lines) + more, action
    filters = "".join([f' labeled "{label}"' if label else "",
                       f' titled ~"{title_q}"' if title_q else "",
                       f" inside page {parent}" if parent else ""])
    where_full = (f"“{sub}”" if sub else where) + filters
    total, shown = earlier + len(matches), matches[:limit]
    rest = len(matches) - len(shown)
    partial = bool(earlier or rest)
    action = {"kind": "list",
              "summary": (f"Listed {len(shown)} of {total} pages in {where_full}" if partial
                          else f"Listed {total} page{'s' if total != 1 else ''} in {where_full}")}
    if not total:
        no = ("No pages match those filters." if (label or title_q or parent)
              else "The folder is empty." if (sub or path) else "The library lists no page.")
        return no, action
    if not shown:
        return f"No pages come after that one in {where_full} — it was the last of {total}.", action
    nxt = ""
    if rest:
        # The call for the next batch, with this call's own filters as the
        # model wrote them (a relative folder path stays relative).
        again = {k: v for k, v in (("label", label), ("folder", folder_raw), ("title_contains", title_raw),
                                   ("parent", parent)) if v}
        if limit != _LIST_CAP:
            again["limit"] = limit
        again["after"] = shown[-1][0]
        nxt = _call_text("list_pages", again)
    # The next call goes in the header too: a saved chip keeps only the
    # first _DETAIL_CAP characters, and Continue replays the saved chat.
    span = f"; {earlier + 1}–{earlier + len(shown)} shown" if partial else ""
    tree = Tree.read(conn) if any(work_folder_of(m[2]) for m in shown) else None
    text = (f"Pages in {where_full} ({total}, most recently added first{span}"
            + (f"; next: {nxt}" if nxt else "") + "):\n"
            + "\n".join(_page_line(paths, names, *m, tree=tree) for m in shown))
    if nxt:
        text += f"\n[… {rest} more page{'s' if rest != 1 else ''} — call {nxt} to continue]"
    return text, action


def _run_list_folders(conn, ws: str, scope: dict, args: dict):
    """The folder tree from the chat's folder (or the `folder` asked for)
    down, that folder included and empty folders too: each folder's id,
    its full path, the pages filed directly in it and the pages anywhere
    below it. At the library root, also how many pages are in no folder.
    It counts library pages, as the home view does: a sub-page goes with
    the page it is inside."""
    found, error = _find_folder(conn, scope, args.get("folder"))
    if error:
        return error, None
    root, root_path = found
    parent_of = tree_parents(conn, FOLDERS)
    paths = folder_paths(conn)
    shown = folder_subtree_ids(conn, root) if root else set(parent_of)
    here: dict[str, set] = {}
    below: dict[str, set] = {}
    unfiled, inside = 0, page_parents(conn)
    for page_id, props_raw in conn.execute(
            "SELECT id, properties FROM unified_blocks WHERE parent_id = 'root'"):
        if page_id in inside:
            continue
        folders = [f for f in filing(load_props(props_raw), FOLDERS) if f in parent_of]
        if not folders:
            unfiled += 1
        for folder_id in folders:
            if folder_id in shown:
                here.setdefault(folder_id, set()).add(page_id)
            while folder_id in shown:  # it and the folders above it, up to the listing's top
                below.setdefault(folder_id, set()).add(page_id)
                folder_id = parent_of[folder_id]
    top = len(paths[root]) if root else 1
    lines = []
    for folder_id in parent_of:  # tree order
        if folder_id not in shown:
            continue
        n_here, n_below = len(here.get(folder_id, ())), len(below.get(folder_id, ()))
        counts = ("no pages" if not n_below else f"{n_here} page{'s' if n_here != 1 else ''}"
                  if n_here == n_below else f"{n_here} here, {n_below} with subfolders")
        lines.append("  " * (len(paths[folder_id]) - top)
                     + f'- [{folder_id}] "{PATH_SEP.join(paths[folder_id])}" ({counts})')
    where = f"“{root_path}”" if root else "the library"
    action = {"kind": "list",
              "summary": f"Listed {len(lines)} folder{'s' if len(lines) != 1 else ''} in {where}"}
    loose = (f"\n{unfiled} page{'s are' if unfiled != 1 else ' is'} in no folder."
             if unfiled and not root else "")
    if not lines:
        return f"No folders in {where}." + loose, action
    more = f"\n(+{len(lines) - _LIST_CAP} more not shown)" if len(lines) > _LIST_CAP else ""
    return (f"Folders in {where} (id in brackets, then the full path; subfolders indented):\n"
            + "\n".join(lines[:_LIST_CAP]) + more + loose
            + '\nList a folder\'s pages with list_pages(folder="<path or id>"), then read one with '
              "read_page."), action


def context_cover(scope: dict, page_id: str) -> dict | None:
    """What the conversation context already holds of a page (the chat's
    coverage report, riding in the scope): ``{format, pages_shown, pages,
    lines_shown, lines, partial, notes, native}`` — the head-excerpt span
    of its PDF text (none when the file went natively, or the page has no
    PDF), or of its text file's lines, and whether its notes are there.
    None for a page not in context."""
    for entry in scope.get("coverage") or ():
        if entry.get("page_id") == page_id:
            return entry
    return None


def _run_read_page(conn, ws: str, scope: dict, args: dict):
    """A page's text in windows — minus what the conversation already
    holds: PDF pages the head excerpt shows in full, and a text file's
    lines its head shows, are never sent again (a read of them continues
    from the page the excerpt cut short, or the first line not shown), a
    document the context holds whole is not re-read at all, and the notes
    and open threads come once — with the first window of a read, or on
    ``notes: true`` — and not when the chat's context already carries
    them. A text file reads by lines (``from_line``, ``to_line``), a PDF by
    pages and offsets; the other kind's arguments are answered with a line
    and ignored. The page may be named by a work folder's ``path``."""
    loaded, error = _load_scoped_page(conn, scope, args)
    if error:
        return error, None
    page_id, title, props, _ = loaded
    view = page_document(props)
    text_file = is_text_document(view)
    budget, offset, page, from_line, to_line = _window_args(scope, args)
    lead = []
    if text_file:
        if args.get("pdf_offset") or args.get("pdf_page") not in (None, "", 1, "1"):
            lead.append(f'"{title}" is a text file: use from_line (and to_line) — pdf_page and pdf_offset '
                        "are ignored.")
        offset, page = 0, 1
        first_window = from_line <= 1
    else:
        if args.get("from_line") or args.get("to_line"):
            lead.append(f'"{title}" is ' + ("a PDF: use pdf_page and pdf_offset" if is_pdf(view) else
                                            "no text file") + " — from_line and to_line are ignored.")
        from_line, to_line = 1, None
        first_window = offset == 0 and page == 1
    cover = context_cover(scope, page_id)
    notes_wanted = bool(args.get("notes")) or first_window
    if text_file and cover and cover.get("format") == "text" and cover.get("lines_shown"):
        shown, total = cover["lines_shown"], cover.get("lines") or 0
        if not cover.get("partial"):
            lead.append(f'The whole text of "{title}" ({total:,} lines) is in the conversation context above '
                        "— answer from it; nothing more to read.")
            budget = 0
        elif to_line is not None and to_line <= shown:
            lead.append(f'Lines {from_line:,}–{to_line:,} of "{title}" are in the conversation context above '
                        "— answer from them; nothing more to read.")
            budget = 0
        elif from_line <= shown:
            lead.append(f'Lines 1–{shown:,} of "{title}" are in the conversation context above and are not '
                        f"repeated; this read continues from line {shown + 1:,}.")
            from_line = shown + 1
    elif (not text_file and cover and cover.get("pages_shown") and cover.get("doc_id")
          and not cover.get("native")):
        shown, total = cover["pages_shown"], cover.get("pages") or 0
        if not cover.get("partial"):
            lead.append(f'The whole PDF text of "{title}" is in the conversation context above '
                        f"({total or shown} pages) — answer from it; nothing more to read.")
            budget = 0
        elif offset == 0 and page < shown:
            lead.append(f"PDF pages 1–{shown - 1} of \"{title}\" are in the conversation context "
                        f"above and are not repeated; this read continues from page {shown}, "
                        f"the page the excerpt cut short.")
            page = shown
    notes_in_context = bool(cover and cover.get("notes"))
    include_notes = notes_wanted and not notes_in_context
    report: dict = {}
    section = page_report_section(conn, ws, page_id, budget, offset, page,
                                  include_notes=include_notes,
                                  notes_budget=_read_cap(scope.get("read_chars")), report=report,
                                  notes_seen=notes_seen(scope), listing=scope.get("type") != "page",
                                  from_line=from_line, to_line=to_line, threads=notes_wanted and cover is None)
    if not section:
        return f'"{title}" has no readable content', None
    if notes_wanted and notes_in_context:
        lead.append("(The page's notes and highlights are in the conversation context above.)")
    elif not include_notes:
        lead.append("(Notes, highlights and open comment threads come with the first window of a read, "
                    "or with notes: true.)")
    text = ("\n".join(lead) + "\n\n" + section) if lead else section
    chip = {"kind": "read", "page_id": page_id, "summary": f"Read “{title[:60]}”"}
    first, last = report.get("pdf_pages") or (0, 0)
    if last:
        chip["pdf_pages"] = [first, last]
        chip["summary"] = f"Read “{title[:60]}” p. {first}" + (f"–{last}" if last > first else "")
    line_first, line_last = report.get("lines") or (0, 0)
    if line_last:
        chip["lines"] = [line_first, line_last]
        chip["summary"] = f"Read “{title[:60]}” l. {line_first}" + (f"–{line_last}" if line_last > line_first else "")
    if work_folder_of(props):
        path = work_paths.page_path(Tree.read(conn), props)
        if path:
            chip["path"] = path
    url = gamma_link(scope, page_id)
    if url:
        text += f"\n[Links: page {url}"
        if last:
            text += (f"; PDF page {first}: {gamma_link(scope, page_id, pdf_page=first)}, the other pages "
                     "likewise — add &quote=<percent-encoded verbatim passage> to open at the passage")
        if line_last:
            text += (f"; line {line_first}: {gamma_link(scope, page_id, line=line_first)} (a range: "
                     "&line=N-M; add &quote=<the first line's verbatim text, percent-encoded> to open at it)")
        text += "]"
    # The area highlights' pictures go with the result (the loop moves
    # them onto the tool message, like view_pdf_page's page).
    images = (render_area_crops(ws, pdf_file_hash(view), report["areas"], title, page_id)
              if view and report.get("areas") else [])
    if images:
        chip["images"] = _parts(images)
    return text, chip


def _run_read_block(conn, ws: str, scope: dict, args: dict):
    """The note outline under a block (or a whole page), every line prefixed
    with its block id — the ids the editing tools take. The requested block's
    own text is never truncated; children are snipped per line and the listing
    stops at the read budget. A text file a note's chip links shows its
    first lines under it (``ai_context.chip_lines``)."""
    loaded, error = _load_scoped_block(conn, scope, args.get("block_id"))
    if error:
        return error, None
    block, page_id, page_title = loaded
    rows = fetch_subtree(conn, block["id"])
    page = conn.execute("SELECT properties FROM unified_blocks WHERE id = ?", (page_id,)).fetchone()
    view = page_document(load_props(page[0]) if page else {})
    current, kept = file_hash(view) or "", anchors.text_kept(view)
    by_parent: dict = {}
    for row in rows:
        if row[0] != block["id"]:
            by_parent.setdefault(row[1], []).append(row)
    for children in by_parent.values():
        children.sort(key=lambda row: row[2])

    areas: list = []  # (page, box) of the area highlights whose picture goes along
    per_page: dict = {}
    chips: dict = {}  # the file chips whose text is shown (ai_context.chip_lines)

    def line(block_id, content, props, depth, on_sheet, full=False):
        quote = (props.get("quote") or "").strip()
        text, more = (content or "").strip(), ""
        if not full and len(text) > _NOTE_SNIPPET:
            text, more = text[:_NOTE_SNIPPET], f'… {_TRUNCATED_MARK}block_id="{block_id}") for the full text]'
        text = (picture_text(props, text) or text) + more
        bits = [f"[{block_id}]"]
        if quote:
            # how the reader marked it: a wash, or a line (the block's style)
            mark, place = _MARK_WORDS.get(props.get("style"), "highlight"), range_words(props, current)
            if kept and place:
                bits.append(f"({mark} on {place}; {SECRET_TEXT[1:-1]})")
            else:
                bits.append(f'({mark}: "{quote[:200]}"' + (f"; {place})" if place else ")"))
        elif area := area_highlight(props):
            page, box = area
            per_page[page] = per_page.get(page, 0) + 1
            if per_page[page] <= MAX_AREA_CROPS:
                areas.append((page, box))
                bits.append(f"(area highlight: a rectangle on PDF page {page}; picture {len(areas)} attached)")
            else:
                bits.append(f"(area highlight: a rectangle on PDF page {page}; no picture: more than "
                            "the limit on this page)")
        if label := placed_label(props, on_sheet, above=True) or handwriting_label(props):
            bits.append(label)
        bits.append(text or "(empty)")
        linked = chip_lines(conn, ws, content or "", chips)
        if linked:
            bits[-1] += "\n" + linked
        pad = "  " * depth
        return pad + "- " + "\n".join(
            l if i == 0 else pad + "  " + l
            for i, l in enumerate(" ".join(bits).split("\n")))

    budget = _read_cap(scope.get("read_chars"))
    lines, used, skipped = [], 0, 0
    seen = notes_seen(scope)

    def walk(parent, depth, on_sheet):
        nonlocal used, skipped
        for row in by_parent.get(parent, []):
            try:
                props = json.loads(row[4] or "{}")
            except ValueError:
                props = {}
            entry = line(row[0], row[3], props, depth, on_sheet)
            if used + len(entry) > budget:
                skipped += len(fetch_subtree(conn, row[0]))  # block + descendants
                continue
            used += len(entry)
            lines.append(entry)
            if len((row[3] or "").strip()) <= _NOTE_SNIPPET:  # shown whole, not snipped
                seen[row[0]] = row[3] or ""
            walk(row[0], depth + 1, on_sheet or is_sheet(props))

    is_page = block["parent_id"] == "root"
    on_sheet = not is_page and under_sheet(conn, block["id"])
    if is_page:
        head = f'Note outline of page "{page_title}" ({_where(scope, page_id)}):'
    else:
        head = (f'Block [{block["id"]}] in page "{page_title}" ({_where(scope, page_id, block_id=block["id"])}):\n'
                + line(block["id"], block["content"], block["properties"], 0, on_sheet, full=True))
        seen[block["id"]] = block["content"]
    walk(block["id"], 0 if is_page else 1, on_sheet or is_sheet(block["properties"]))
    if not lines and is_page:
        lines = ["(no notes on this page yet)"]
    tail = (f"\n(+{skipped} more block(s) not shown — read_block a nested id to continue)"
            if skipped else "")
    out = (head + "\n" + "\n".join(lines) + tail
           + "\nBlock ids are in [brackets] — pass them to edit_block/create_block/move_block.")
    what = f'“{page_title[:60]}”' if is_page else f'a block in “{page_title[:60]}”'
    # block_id lets the notes panel light up the block (or, for a page id,
    # the whole outline) the agent is reading.
    chip = {"kind": "read", "page_id": page_id, "block_id": block["id"],
            "summary": f"Read notes of {what}"}
    images = render_area_crops(ws, page_doc_id(conn, page_id), areas, page_title, page_id) if areas else []
    if images:
        chip["images"] = _parts(images)
    return out, chip


def _chat_bucket(conn, scope: dict, args: dict):
    """The AI chat a read_chats call names, under ChatDock's bucket keys: a
    page's (its id; in a page chat the page by default) or a folder's (its
    id, ``home`` at the library root). Returns ``({bucket, label, name,
    page_id}, error)`` — exactly one side is set."""
    if scope.get("type") == "page" and args.get("folder") and not args.get("page_id"):
        return None, "error: folder chats are outside this chat's scope"
    page_id = args.get("page_id") or (scope.get("page_id") if scope.get("type") == "page" else "")
    if page_id:
        loaded, error = _load_scoped_page(conn, scope, {"page_id": page_id})
        if error:
            return None, error
        page_id, title, _, _ = loaded
        return {"bucket": page_id, "label": f"page “{title[:80]}” (page_id {page_id})",
                "name": f"“{title[:60]}”", "page_id": page_id}, None
    found, error = _find_folder(conn, scope, args.get("folder"))
    if error:
        return None, error
    folder, path = found
    name = f"folder “{path}”" if folder else "the library root"
    return {"bucket": folder or "home", "label": name, "name": name, "page_id": ""}, None


def _chat_entry(n: int, message) -> str:
    """One stored ChatDock message as a transcript entry: who spoke, what
    rode along, the text, and the summaries of the tools the reply used."""
    if not isinstance(message, dict):
        return f"[{n}] (unreadable message)"
    who = "User" if message.get("role") == "user" else "AI"
    if message.get("error"):
        who += " (failed reply)"
    extras = [str(name) for name in message.get("pdfs") or []]
    extras += [f"“{p['title']}”" for p in message.get("contextPages") or []
               if isinstance(p, dict) and p.get("title")]
    if message.get("images"):
        extras.append(f"{len(message['images'])} image(s)")
    text = str(message.get("text") or message.get("content") or "").strip() or "(empty)"
    entry = f"[{n}] {who}" + (f" (with {', '.join(extras)})" if extras else "") + ": " + text
    tools = [str(a["summary"]) for a in message.get("actions") or []
             if isinstance(a, dict) and a.get("summary")]
    if tools:
        entry += "\n    Tools used: " + " · ".join(tools)
    return entry


def _run_read_chats(conn, ws: str, scope: dict, args: dict):
    """The AI chat kept with a page or folder (gamma/routers/chats.py): its
    current conversation — or the earlier one `chat_id` names — as a
    transcript windowed by whole messages within the read budget, plus the
    index of the bucket's earlier conversations."""
    from ..routers.chats import derive_title  # local: keep gamma.* free of the routers package

    target, error = _chat_bucket(conn, scope, args)
    if error:
        return error, None
    chat_id = str(args.get("chat_id") or "current").strip()
    current = chat_id == "current"
    try:
        start = max(1, int(args.get("start") or 1))
    except (TypeError, ValueError):
        start = 1
    if current:
        row = conn.execute("SELECT title, messages, updated_at FROM chats WHERE bucket = ?",
                           (target["bucket"],)).fetchone()
        history = conn.execute(
            "SELECT id, title, messages, updated_at FROM chat_history WHERE bucket = ? "
            "ORDER BY updated_at DESC LIMIT ?", (target["bucket"], _LIST_CAP + 1)).fetchall()
    else:
        row = conn.execute("SELECT title, messages, updated_at FROM chat_history "
                           "WHERE id = ? AND bucket = ?", (chat_id, target["bucket"])).fetchone()
        history = []
        if not row:
            return (f"error: no conversation {chat_id} in the AI chat of {target['label']} — "
                    "use a chat_id this tool listed"), None
    messages = json.loads(row[1] or "[]") if row else []
    action = {"kind": "read", "summary": f"Read the AI chat of {target['name']}"}
    if target["page_id"]:
        action["page_id"] = target["page_id"]
    if not messages and not history:
        return f"No AI chat is kept with {target['label']}.", action
    if start > max(len(messages), 1):
        return f"error: start {start} is past the end — the conversation has {len(messages)} messages", None
    parts = []
    if messages:
        title = row[0] or derive_title(messages)
        which = "the current conversation" if current else "an earlier conversation"
        head = (f"AI chat of {target['label']} — {which}" + (f" “{title}”" if title else "")
                + f", {len(messages)} messages, last updated {str(row[2])[:10]}:")
        budget = _read_cap(scope.get("read_chars"))
        entries, used, next_start = [], 0, None
        for n in range(start, len(messages) + 1):
            entry = _chat_entry(n, messages[n - 1])
            if entries and used + len(entry) > budget:
                next_start = n
                break
            if len(entry) > budget:
                entry = entry[:budget] + " … [cut at the read window]"
            entries.append(entry)
            used += len(entry)
        parts.append(head + "\n" + "\n".join(entries))
        if next_start:
            call = {k: args[k] for k in ("page_id", "folder") if args.get(k)}
            if not current:
                call["chat_id"] = chat_id
            hint = "".join(f'{k}="{v}", ' for k, v in call.items())
            parts.append(f"(+{len(messages) - next_start + 1} more messages — call "
                         f"read_chats({hint}start={next_start}) to continue)")
    else:
        parts.append(f"No current conversation in the AI chat of {target['label']}.")
    if history:
        lines = []
        for entry_id, title, raw, updated in history[:_LIST_CAP]:
            earlier = json.loads(raw or "[]")
            lines.append(f"- chat_id={entry_id} | “{title or derive_title(earlier) or 'Untitled'}” | "
                         f"{len(earlier)} messages | updated {str(updated)[:10]}")
        more = "\n(older conversations not shown)" if len(history) > _LIST_CAP else ""
        parts.append("Earlier conversations (read one with chat_id):\n" + "\n".join(lines) + more)
    return "\n\n".join(parts), action


TOOLS = [
    {
        "perm": "list", "kind": "list", "scopes": ("folder",), "mutating": False, "run": _run_list_pages,
        "spec": {
            "name": "list_pages",
            "description": (
                "List the pages in the folder the user is viewing, one per line: id, "
                "title, attachments (its document's format: `[pdf]` for a PDF, `[text:latex]` "
                "for a text file with its language, `[binary listed]` for a file listed "
                "without its bytes, `[]` for a page of notes alone) with the document's "
                "`size` in bytes, the paths of its folders, its labels, cached paper "
                "metadata (first author, year, venue) and last-update date. A work "
                "folder's file carries `path=`, its path inside the work folder, and "
                "read_page(path=…) reads it; at the library root a work folder's files "
                "are listed only inside a folder that files them (`folder=`). A page can "
                "sit inside another page as its sub-page: its line shows "
                "`parent=<id>`, the id of the page it is inside, in place of folders "
                "(it is filed where the outermost page around it is). Call this "
                "before any other tool — never guess page ids. Prefer the filters over "
                "listing everything: `label` (a label's name — exact, else ignoring "
                "case), `folder` (a folder's path or id — its pages and its "
                "subfolders'), `title_contains` (title substring), `parent` (a page "
                "id — the pages right inside it). Pages come most recently added first "
                "(library order, which renaming, filing, labelling or editing a page "
                "never changes), at most `limit` a call "
                "(default and most 400). When more match, the result names the call "
                "that lists the next batch: `after` is the id of the last page listed, "
                "and the listing goes on from there whatever changed since. "
                "`list_labels: true` instead returns the labels with page counts, the "
                "most used first (at the library root every label, unused ones too; in "
                "a folder those its pages carry) — use it to answer questions about the "
                "labels themselves or to find a label's exact spelling; the folders are "
                "list_folders'."),
            "parameters": {
                "type": "object",
                "properties": {
                    "label": {"type": "string"},
                    "folder": {"type": "string"},
                    "title_contains": {"type": "string"},
                    "parent": {"type": "string", "description": "a page id: list the pages right inside it"},
                    "list_labels": {"type": "boolean"},
                    "limit": {"type": "integer",
                              "description": "lines per call, 1–400 (default 400): pages, or labels with "
                                             "list_labels"},
                    "after": {"type": "string",
                              "description": "a page id from an earlier listing: list the pages after it"},
                },
                "required": [],
            },
        },
    },
    {
        "perm": "list", "kind": "list", "scopes": ("folder",), "mutating": False, "run": _run_list_folders,
        "spec": {
            "name": "list_folders",
            "description": (
                "Show how the pages are organized: the folder tree from the current "
                "folder, or from `folder` (a path or id), down — one folder per line, "
                "its id in brackets and its full path (the names from the top joined "
                "with ' / ', e.g. \"Physics / QEC\"), subfolders indented under it, and "
                "per folder the pages filed directly in it and in total with its "
                "subfolders (a sub-page, a page inside another page, is not counted). "
                "Folders exist on their own, so an empty one is listed too; "
                "a page may be in several, and a name may hold any character. At the "
                "library root it also counts the pages in no folder. Walk a folder with "
                "list_pages(folder=…), then open a page with read_page."),
            "parameters": {
                "type": "object",
                "properties": {"folder": {"type": "string",
                                          "description": "the folder to start from, a path or id "
                                                         "(default: the current one)"}},
                "required": [],
            },
        },
    },
    {
        "perm": "read", "kind": "read", "scopes": ("folder", "page"), "mutating": False, "run": _run_read_page,
        "spec": {
            "name": "read_page",
            "description": (
                "Read one page this chat can reach: its title and properties, the "
                "pages it is inside and the pages inside it (its sub-pages, with their "
                "ids), the user's highlighted passages and notes, its open comment "
                "threads, and — when the page carries a document — its text: a PDF's "
                "extracted text, or a text file's lines, each line numbered (the number "
                "and tab before it are no text). A page without a document returns its "
                "notes (they are its content). Use it to answer questions about specific "
                "pages or to write summaries/reports across several. Name the page by "
                "`page_id`, or a work folder's file by its `path` inside the work folder "
                "('sections/method.tex'; in a work folder's chat relative to it, else "
                "starting with the work folder's name). `pdf_chars` sets the window's "
                "characters — for a text file, as many lines as fit from `from_line` "
                "(default {read_default}, up to {read_cap}; 0 = notes only — ask only "
                "for what you need). A long document doesn't fit in one call. A PDF: "
                "`pdf_page` (1-based) starts the excerpt at that PDF page — pass a "
                "search_library hit's page number to read around the match — and "
                "`pdf_offset` starts it that many characters further in; when more "
                "text remains the excerpt ends by naming the next offset. A text file: "
                "`from_line` (1-based) starts the window at that line — pass a file "
                "hit's l.N — and `to_line` ends it there (inclusive); when more lines "
                "remain the window ends by naming the next from_line, and the first "
                "window of a long file ends with an outline saying where the rest is. "
                "Keep calling to read as far as you need. What the conversation context "
                "already holds is not repeated: PDF pages and lines shown there in full "
                "are skipped (a read of them continues from the first one not in "
                "context) and the notes come once, with the first window of a read, or "
                "with `notes: true`."),
            "parameters": {
                "type": "object",
                "properties": {"page_id": {"type": "string", "description": "one of page_id or path"},
                               "path": {"type": "string",
                                        "description": "a work folder's file by its path, in place of page_id"},
                               "pdf_chars": {"type": "integer"},
                               "pdf_offset": {"type": "integer"},
                               "pdf_page": {"type": "integer"},
                               "from_line": {"type": "integer",
                                             "description": "a text file's first line to read, 1-based"},
                               "to_line": {"type": "integer",
                                           "description": "a text file's last line to read, inclusive"},
                               "notes": {"type": "boolean",
                                         "description": "include the page's notes and highlights "
                                                        "with this window"}},
                "required": [],
            },
        },
    },
    {
        "perm": "block_read", "kind": "read", "scopes": ("folder", "page"), "mutating": False, "run": _run_read_block,
        "spec": {
            "name": "read_block",
            "description": (
                "Read the user's notes as an outline of blocks, each line prefixed "
                "with its block id. `block_id` may be a page id (the whole page's "
                "notes) or any block id from an earlier call (that block in full plus "
                "its subtree). Always call this before editing, creating or moving "
                "blocks — the editing tools take these exact ids."),
            "parameters": {
                "type": "object",
                "properties": {"block_id": {"type": "string",
                                            "description": "a page id or block id"}},
                "required": ["block_id"],
            },
        },
    },
    {
        "perm": "read", "kind": "read", "scopes": ("folder", "page"), "mutating": False, "run": _run_read_chats,
        "spec": {
            "name": "read_chats",
            "description": (
                "Read the AI chat the user keeps with a page (`page_id`) or a folder "
                "(`folder`, a path or id; neither = the chat of the current page or folder): "
                "its current conversation as a numbered transcript — each message's "
                "text, what the user attached, and the tools each reply used — and the "
                "list of earlier conversations with their `chat_id`. Pass a `chat_id` "
                "to read an earlier one. A long conversation doesn't fit in one call: "
                "the transcript ends by naming the `start` message to continue from. "
                "Replies in it are an AI's earlier answers, not the page's content — "
                "check a claim against the page before repeating it."),
            "parameters": {
                "type": "object",
                "properties": {**_PAGE_ID_ARG,
                               "folder": {"type": "string"},
                               "chat_id": {"type": "string",
                                           "description": "an earlier conversation's id (default: the current one)"},
                               "start": {"type": "integer",
                                         "description": "1-based message to start from"}},
                "required": [],
            },
        },
    },
]
