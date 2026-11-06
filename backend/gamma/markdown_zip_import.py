"""Import a zip of Markdown notes as pages — Notion's "Markdown & CSV" export,
an Obsidian vault, Gamma's own Markdown export, or any zipped folder of
``.md`` files. One logic serves all of them, because they only differ in
naming and link conventions:

- every ``.md`` becomes a note page; its title is the front-matter ``title``,
  else the leading ``# H1``, else the filename (Notion's ``Title <32-hex id>``
  suffix stripped). In an Obsidian vault the filename IS the title, so there
  the H1 stays in the body unless it repeats the title;
- directories become folders, made below the destination folder where
  missing (``ops.ensure_filing``), each directory name a folder name; a
  front-matter ``folder:`` path (what Gamma's export writes, relative to
  the exported folder, names joined with "/") wins over the directory;
- a front-matter ``parent:`` (what Gamma's exports write for a sub-page:
  the parent's file as a relative link, or ``"[[Parent]]"`` in a vault)
  naming another note of the zip, resolved as a link from the note, makes
  its page a sub-page of that note's (``parent_page``), filed where its
  top is and so in no folder of its own. Without one, a note in Notion's
  page directory (``Title <id>/`` beside the page's ``Title <id>.md``, or
  a database's ``Title <id>.csv``; since late 2025 ``Title/``, or
  ``Title abcd-wxyz/`` for a second page of the title) is a sub-page of
  that page, so a Notion page tree comes in as a page tree and its
  directories make no folders. A flat export (every page in one
  directory) gets its tree from the pages' lines that link to their
  sub-pages and from the row names in its databases' CSVs;
- links to other notes in the zip become ``[[page]]`` mentions — Markdown
  links (relative paths, Notion's percent-encoded ones) and Obsidian
  wikilinks alike, both resolved by path first and by basename anywhere in
  the zip second, case-insensitively, the way Obsidian resolves them.
  ``[[Note#Heading]]`` and ``[[Note#^id]]`` point at that heading / anchored
  block, ``![[Note#^id]]`` becomes a synced block, whole-note and section
  embeds degrade to mentions (a Gamma embed shows one block), and the
  ``^id`` anchors themselves are removed from the text;
- links and embeds of bundled images / PDFs / files upload the file
  (content-hash dedup, the storage quota) and point at
  ``/api/uploads/…``; an Obsidian image embed's ``|300`` size becomes the
  ``![alt|300](url)`` form the editor renders;
- Obsidian properties: ``tags`` become labels (made by name where missing),
  ``aliases`` are kept in ``properties.aliases``; foldable callout markers
  stay; ``%%comments%%`` are removed (vaults only — the ``.obsidian/``
  folder marks one); ``.obsidian/``, ``.trash/`` and ``.canvas`` files are
  skipped;
- a Notion database (``Name <id>.csv`` — the ``_all`` variant when both exist,
  it carries every row, in the columns' order the view's CSV gives) becomes
  a page holding the table, its row pages (``Name <id>/Row <id>.md``)
  sub-pages of it; relations (``Title (…%20<id>.md)``, in a cell or a row's
  property line) become mentions of the page of that id; Notion's
  ``<aside>`` callouts become ``> [!info]`` callouts, and the empty ``>``
  line Notion ends a quote with goes;
- Gamma's front matter (``source`` → the bundled PDF or its URL, ``doi`` /
  ``authors`` / ``year`` → metadata) and BibTeX block are restored;
- a ``.md`` already imported (same bytes, or the same Notion page id) is
  skipped, so re-importing an export adds nothing; links to it still resolve
  to the existing page. Notion splits big exports into ``Part-N.zip`` members
  and wraps workspace exports in ``Export-<uuid>/``: nested zips are read in
  place, one common root directory (and any such wrapper) is dropped.

There is no cap on the size of the zip or its number of notes; what guards
the server is the guarded walk the library imports share (gamma/import_zip.py:
a zip-bomb rule, nested zips spooled to temporary files) and the free disk.
"""

import contextlib
import csv
import io
import itertools
import json
import posixpath
import re
import shutil
import unicodedata
import zipfile
from urllib.parse import unquote

from fastapi import HTTPException

from . import config, jobs
from .blocks_store import (FOLDERS, MAX_PAGE_DEPTH, PARENT_PAGE, filing, folder_inserts, folder_paths,
                           insert_page_tree, new_block_id, page_parents, split_path, top_of, write_lock)
from .import_review import archive_entries, destination, validate_selection
from .import_staging import MIN_FREE_BYTES
from .import_zip import check_ratio, new_walk, walk_zip
from .logbuf import log
from .markdown_import import (MAX_BLOCKS, MAX_MARKDOWN_BYTES, TooManyBlocks, fm_list, fm_text, md_to_blocks,
                              parse_frontmatter)
from .ops import after_commit, apply_batches, ensure_filing
from .storage import IMAGE_MEDIA_TYPES, content_digest, is_pdf, store_file_stream, upload_media_type

# A Notion database's rows, in tables of at most this many rows (and
# characters): each table one block the editor renders and can save
# (ops.MAX_CONTENT is 200,000 characters), but for a single row past that:
# it stays whole, to read, and takes shortening before it saves.
TABLE_ROWS = 500
TABLE_CHARS = 100_000
MAX_TABLE_COLS = 40
# (a cell can be as long as the file: Notion's relation cells run long)
csv.field_size_limit(max(csv.field_size_limit(), MAX_MARKDOWN_BYTES))
# New pages written in one transaction: few commits, and each hold of the
# write lock stays short (a page's files are stored before it is staged).
PAGES_PER_COMMIT = 50

_MD_EXTS = (".md", ".markdown")
_NOTION_ID_RE = re.compile(r"\s+[0-9a-f]{32}$", re.I)
# Since about November 2025 a page's directory is named as the page without
# its id, and a second one of the same name in a directory carries the
# page id's first and last four digits: ``Title abcd-wxyz/``.
_PARTIAL_ID_RE = re.compile(r"\s+([0-9a-f]{4})-([0-9a-f]{4})$", re.I)
# A relation in a Notion database cell or row property: ``Title (path)``,
# the path the related page's file (``…%20<id>.md``; Notion encodes the
# spaces, not the parentheses, which a title cut at 50 characters can
# leave unbalanced), relations joined by ", ".
_RELATION_RE = re.compile(r" \((\S*?%20([0-9a-f]{32})\.md)\)", re.I)
# A row page's property line, ``Name: value``, as Notion writes them under
# the title.
_PROPERTY_RE = re.compile(r"([^:|\n]{1,100}): ?(.*)$")
_TABLE_LINE_RE = re.compile(r"[ \t]*\|")
_CELL_BAR_RE = re.compile(r"(?<!\\)\|")
_EXPORT_WRAPPER_RE = re.compile(r"^Export-[0-9a-f-]{8,}$", re.I)
# (a closing run of # counts after a space only, as in CommonMark: "# C#" is "C#")
_H1_RE = re.compile(r"^#\s+(.+?)(?:\s+#+)?\s*$")
_HEADING_TEXT_RE = re.compile(r"^#{1,6}\s+(.+?)(?:\s+#+)?\s*$")
# What Obsidian reads as a space when it matches a link to a heading (its
# stripHeading): ``## Learn C#`` is linked as ``[[Note#Learn C]]``.
_HEADING_PUNCT_RE = re.compile(r"""[!"#$%&()*+,.:;<=>?@^`{|}~/\[\]\\\r\n]""")
_BIBTEX_RE = re.compile(r"^```bibtex[ \t]*\n(.*?)\n```[ \t]*\n?", re.S)
# [label](target "title") / ![alt](target) — label may hold one level of [],
# target balanced () as CommonMark allows (Notion leaves them unencoded:
# ``Tasks%20(grid%20view)%20<id>.csv``), else up to the first ")" (a name
# Notion cut at 50 characters inside a parenthesis: ``Plan%20(PHASE%20ST%20<id>.md``)
_LINK_RE = re.compile(r"(!?)\[((?:[^\[\]]|\[[^\]]*\])*)\]\(\s*(<[^>]*>|(?:[^()\s]|\([^()\s]*\))+|[^)\s]+)"
                      r"((?:\s+\"[^\"]*\")?)\s*\)")
# [[target#sub#sub|alias]] / ![[…]] — Obsidian's wikilink and embed; the
# alias pipe may be escaped (``\|``) inside a table cell. The same shape
# matches Gamma's own [[id]] refs, which simply resolve to nothing here.
_WIKILINK_RE = re.compile(r"(!?)\[\[([^\[\]|#]*)((?:#[^\[\]|]*)*)(?:\\?\|([^\[\]]*))?\]\]")
_SCHEME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*:")
# Bundled files that become uploads: anything Gamma would accept from
# POST /api/upload-file (not executable, well-formed extension) that is not
# a note or a canvas.
_NOT_ASSET_EXTS = {".canvas", ""}


def _is_asset_ext(ext: str) -> bool:
    return ext not in _MD_EXTS and ext not in _NOT_ASSET_EXTS and upload_media_type(ext) is not None
# Obsidian block anchors: `` ^id`` at the end of a line, or ``^id`` alone on
# a line after a list / quote / table / fence.
_ANCHOR_END_RE = re.compile(r"[ \t]+\^([A-Za-z0-9-]+)[ \t]*$")
_ANCHOR_LINE_RE = re.compile(r"^\^([A-Za-z0-9-]+)$")
_IMAGE_SIZE_RE = re.compile(r"^\d+(?:x\d+)?$")
_FENCE_LINE_RE = re.compile(r"^[ \t]*(```|~~~)")
_COMMENT_RE = re.compile(r"%%.*?%%", re.S)
_SKIP_DIRS = {".obsidian", ".trash"}


# --- the walked entries ------------------------------------------------------

def _strip_wrappers(entries):
    """Drop one common root directory (a zipped folder) plus any number of
    Notion ``Export-<uuid>`` wrappers so paths start at the notes."""
    stripped_plain = False
    while entries:
        tops = {e.path.split("/", 1)[0] for e in entries}
        if len(tops) != 1 or not all("/" in e.path for e in entries):
            break
        top = next(iter(tops))
        if _EXPORT_WRAPPER_RE.match(top):
            pass
        elif stripped_plain:
            break
        else:
            stripped_plain = True
        for e in entries:
            e.path = e.path.split("/", 1)[1]


# --- naming ------------------------------------------------------------------

def _notion_id(stem: str):
    m = _NOTION_ID_RE.search(stem)
    return m.group(0).strip().lower() if m else None


def _clean_stem(stem: str) -> str:
    return _NOTION_ID_RE.sub("", stem).strip() or stem.strip()


def _norm_name(name: str) -> str:
    """A title or file name as names are compared: NFC, spaces collapsed,
    case folded."""
    return re.sub(r"\s+", " ", unicodedata.normalize("NFC", name)).strip().casefold()


def _loose(name: str) -> str:
    """A title as Notion names its file, for comparing the two: letters and
    digits only (``GAOS 1.0`` is ``GAOS 1 0 <id>.md``, ``A: B`` ``A B``)."""
    return "".join(ch for ch in unicodedata.normalize("NFC", name).casefold() if ch.isalnum())


def _title_key(title: str) -> str:
    """A title as a flat export's titles and labels are matched: its letters
    and digits (marks, punctuation and what Notion drops from file names
    aside: ``[**Plan**](…)`` names ``Plan``), else the title itself when it
    has none (an emoji)."""
    return _loose(title) or _norm_name(title)


def _notion_file(path: str):
    """``(normalized title, Notion id or None)`` of a note's or database's
    file name."""
    stem, ext = _split_ext(path)
    stem = re.sub(r"_all$", "", stem) if ext == ".csv" else stem
    return _norm_name(_clean_stem(stem)), _notion_id(stem)


def _properties(body: str) -> dict:
    """A Notion row page's property lines: the ``Name: value`` lines its
    body starts with."""
    fields = {}
    for line in body.lstrip("\n").split("\n"):
        m = _PROPERTY_RE.fullmatch(line)
        if not m:
            break
        fields[m.group(1).strip()] = m.group(2).strip()
    return fields


def _dir_path(path: str) -> list[str]:
    """The folder path a note's directory makes: a folder per directory,
    named as the directory (Notion's id suffix dropped)."""
    dirname = posixpath.dirname(path)
    return [s for s in (_clean_stem(seg) for seg in dirname.split("/")) if s] if dirname else []


def _filed_in(under: str, ids: dict, path: list[str]) -> str:
    """The folder id a note is filed in: the folder at ``path`` below the
    destination (``ids``, from ``ops.ensure_filing`` / ``folder_inserts``),
    else the destination itself; "" for the library's top."""
    return ids.get(tuple(path)) or under


def _split_ext(path: str):
    leaf = posixpath.basename(path)
    dot = leaf.rfind(".")
    return (leaf[:dot], leaf[dot:].lower()) if dot > 0 else (leaf, "")


def _norm_heading(text: str) -> str:
    return re.sub(r"\s+", " ", _HEADING_PUNCT_RE.sub(" ", text)).strip().lower()


# --- body preparation --------------------------------------------------------

def _take_title(body: str, fm_title, vault_stem=None):
    """The title and the body without its title line. Outside a vault a
    leading ``# H1`` is the title when there is no front-matter title or it
    repeats it — Notion and Gamma both write one. In a vault the filename
    (``vault_stem``) is the title unless the front matter says otherwise, and
    the H1 is only stripped when it repeats that title."""
    lines = body.lstrip("\n").split("\n")
    m = _H1_RE.match(lines[0]) if lines else None
    if not m:
        return fm_title or vault_stem, body
    h1 = m.group(1).strip()
    whole = lines[0][1:].strip()  # (Gamma's export writes the title "Issue #" as ``# Issue #``)
    if vault_stem is not None:
        title = fm_title or vault_stem
        if title.casefold() not in (h1.casefold(), whole.casefold()):
            return title, body
        return title, "\n".join(lines[1:]).lstrip("\n")
    if fm_title and fm_title not in (h1, whole):
        return fm_title, body
    return fm_title or h1, "\n".join(lines[1:]).lstrip("\n")


def _take_bibtex(body: str):
    m = _BIBTEX_RE.match(body)
    if not m:
        return None, body
    return m.group(1).strip(), body[m.end():].lstrip("\n")


def _convert_asides(body: str) -> str:
    """Notion's ``<aside>`` (its tags alone on their lines, indented as the
    block is in a toggle or a list item) → a ``> [!info]`` callout, its
    first line the title. Since 2026 Notion writes the icon alone on that
    line and a blank line after it; that blank goes (after a first line of
    text it parts paragraphs). An ``<aside>`` in a code block is code."""
    lines, out, in_fence, i = body.split("\n"), [], False, 0
    while i < len(lines):
        line = lines[i]
        i += 1
        if _FENCE_LINE_RE.match(line):
            in_fence = not in_fence
        end = None
        if not in_fence and line.strip() == "<aside>":
            end = next((j for j in range(i, len(lines)) if lines[j].strip() == "</aside>"), None)
        if end is None:
            out.append(line)
            continue
        indent = line[:len(line) - len(line.lstrip())]
        inner = [(ln[len(indent):] if ln.startswith(indent) else ln.lstrip()).rstrip() for ln in lines[i:end]]
        inner = list(itertools.dropwhile(lambda ln: not ln, inner))
        while inner and not inner[-1]:
            inner.pop()
        i = end + 1
        if not inner:
            continue
        rest = inner[1:]
        if not re.search(r"\w", re.sub(r"<img\b[^>]*>", "", inner[0])):
            rest = list(itertools.dropwhile(lambda ln: not ln, rest))
        out += [f"{indent}> [!info] {inner[0]}"] + [f"{indent}> {ln}" if ln else f"{indent}>" for ln in rest]
    return "\n".join(out)


def _drop_quote_tails(body: str) -> str:
    """Notion's Markdown export ends every quote with an empty ``>`` line,
    indented as the quote is (in a list item, a toggle) and inside a
    callout's ``>`` too; drop it (a ``>`` between two quoted lines stays: it
    parts paragraphs)."""
    lines, out, in_fence = body.split("\n"), [], False
    for i, line in enumerate(lines):
        tail = None
        if _FENCE_LINE_RE.match(line):
            in_fence = not in_fence
        elif not in_fence:
            tail = re.fullmatch(r"([ \t]*(?:> ?)*?)>\s*", line)
        if tail and out:
            quote = tail.group(1) + ">"
            if out[-1].startswith(quote) and (i + 1 == len(lines) or not lines[i + 1].startswith(quote)):
                continue
        out.append(line)
    return "\n".join(out)


def _link_lines(body: str) -> list:
    """The ``(label, href)`` of each line that is one Markdown link and
    nothing else, outside fences: how Notion writes a sub-page, a database
    and a link to a page."""
    out, in_fence = [], False
    for line in body.split("\n"):
        if _FENCE_LINE_RE.match(line):
            in_fence = not in_fence
            continue
        m = None if in_fence else _LINK_RE.fullmatch(line.strip())
        if m and not m.group(1):
            out.append((m.group(2), m.group(3).strip("<>").strip()))
    return out


def _link_relations(text: str, page_of) -> tuple[str, int]:
    """Notion relations (``Title (…%20<id>.md)``, in a database cell or a
    row page's property line) → ``[[page]]`` mentions (``page_of(id,
    path)``: the related page's id, or None). A relation's title
    runs from the end of the relation before it (and its ", "), the cell's
    ``|`` or the property line's ``Name: `` to the path; it is the related
    page's when it starts as the file's name does, compared as
    ``_title_key`` compares (Notion drops some characters there and cuts it
    at 50). Returns the text
    and how many were linked."""
    out, pos, last, line, n = [], 0, 0, 0, 0
    for m in _RELATION_RE.finditer(text):
        nl = text.rfind("\n", last, m.start())  # (else on the line of the relation before)
        if nl >= 0:
            line = nl + 1
        start = max(line, last)
        if _TABLE_LINE_RE.match(text, line):
            for bar in _CELL_BAR_RE.finditer(text, start, m.start()):
                start = bar.end()
        elif line >= last and (prop := _PROPERTY_RE.match(text, line, m.start())):
            start = prop.start(2)
        last = m.end()
        label = text[start:m.start()]
        start += len(label) - len(label.lstrip(" ,"))
        pid = page_of(m.group(2).lower(), m.group(1))
        name = _title_key(_clean_stem(_split_ext(unquote(posixpath.basename(m.group(1))))[0]))
        if not pid or not name or not _title_key(text[start:m.start()]).startswith(name):
            continue
        out += [text[pos:start], f"[[{pid}]]"]
        pos, n = m.end(), n + 1
    out.append(text[pos:])
    return "".join(out), n


def _strip_comments(body: str) -> str:
    """Remove Obsidian ``%%…%%`` comments (inline or block) outside fences."""
    out, run, in_fence = [], [], False

    def flush():
        if run:
            out.append(_COMMENT_RE.sub("", "\n".join(run)))
            run.clear()

    for line in body.split("\n"):
        if _FENCE_LINE_RE.match(line):
            flush()
            out.append(line)
            in_fence = not in_fence
        elif in_fence:
            out.append(line)
        else:
            run.append(line)
    flush()
    return "\n".join(out)


def _wiki_source(value: str) -> str:
    """A front-matter ``source: "[[paper.pdf]]"`` → ``paper.pdf``."""
    m = re.match(r"^\[\[([^\]|#]+)(?:#[^\]|]*)?(?:\|[^\]]*)?\]\]$", value.strip())
    return m.group(1).strip() if m else value.strip()


def _csv_rows(data: bytes, blank=False) -> list:
    """A CSV's rows, without the wholly blank ones; ``blank`` keeps those
    after the first row (a row page can stand for one: an untitled row of
    no values). Lines end as the file ends them, a bare CR too."""
    text = data.decode("utf-8-sig", errors="replace")
    rows = list(itertools.dropwhile(lambda r: not any(c.strip() for c in r),
                                    csv.reader(io.StringIO(text, newline=""))))
    return rows if blank else [r for r in rows if any(c.strip() for c in r)]


def _csv_to_markdown(data: bytes, order=None) -> tuple[str, int]:
    """A Notion database CSV → GFM tables, cells pipe-escaped: every row,
    TABLE_ROWS (or TABLE_CHARS) to a table, each table under the header and
    a block of its own. ``order``: column names to put first, in that order
    (the view's, whose CSV lists them as the view shows them while the
    ``_all`` one sorts them). Returns the Markdown and the number of columns
    left out past MAX_TABLE_COLS."""
    rows = _csv_rows(data)
    if not rows:
        return "", 0
    if order:
        rank = {name: i for i, name in enumerate(order)}
        cols = sorted(range(len(rows[0])), key=lambda i: (rank.get(rows[0][i], len(rank)), i))
        rows = [[r[i] if i < len(r) else "" for i in cols] + r[len(rows[0]):] for r in rows]
    columns = max(len(r) for r in rows)
    width = min(columns, MAX_TABLE_COLS)

    def cell(v):
        return re.sub(r"\s*\n\s*", "<br>", (v or "").strip()).replace("|", "\\|") or " "

    def line(r):
        return "| " + " | ".join(cell(c) for c in (r + [""] * width)[:width]) + " |"

    head = [line(rows[0]), "|" + "|".join(" --- " for _ in range(width)) + "|"]
    tables, table, size = [], [], 0
    for r in rows[1:]:
        row = line(r)
        if table and (len(table) >= TABLE_ROWS or size + len(row) > TABLE_CHARS):
            tables.append(table)
            table, size = [], 0
        table.append(row)
        size += len(row) + 1
    tables.append(table)
    return "\n\n".join("\n".join(head + t) for t in tables), columns - width


# --- a flat Notion export's tree ---------------------------------------------

def _row_names(plan) -> list:
    """A database's row names (its CSV's first column), normalized; an empty
    one is Notion's "Untitled"."""
    return [_norm_name(r[0] if r else "") or "untitled" for r in plan.rows[1:]]


def _row_fit(header, same):
    """``fit(note)``: how many of the note's property values the best of the
    rows ``same`` (of one name) holds. Its rarest values first, a row scored
    once, and no further than another row could still win: rows of one name
    can share every value."""
    rows_with = {}  # (column, value) → the rows holding it
    for i, cells in enumerate(same):
        for col, cell in zip(header[1:], cells[1:]):
            rows_with.setdefault((col, cell.strip()), []).append(i)

    def fit(note):
        items = sorted((item for item in note.fields.items() if item in rows_with),
                       key=lambda item: len(rows_with[item]))
        best, seen = 0, set()
        for left, item in zip(range(len(items), 0, -1), items):
            if best >= left:
                break
            for i in rows_with[item]:
                if i not in seen:
                    seen.add(i)
                    row = dict(zip(header[1:], (cell.strip() for cell in same[i][1:])))
                    best = max(best, sum(row.get(col) == value for col, value in items))
                    if best == len(items):
                        return best
        return best

    return fit


def _flat_tree(flat, linked_to) -> dict:
    """The tree of a flat Notion export's pages and databases (``flat``, in
    zip order; a file of no Notion id is no page of it): {page id: parent's
    page id}. ``linked_to(plan, href)``: the plan a link of the plan's
    names, or None.

    A page names its sub-pages and databases by its lines that are one link
    to them under their title (as ``_title_key`` compares them, the file's
    name's or the H1's), or under no label (an untitled page). A database
    names its rows: as many notes of a title as it has rows of it. Every
    database's offers are ranked together, so the note goes where its values
    fit best, not to the first database read: the values that match a row's
    cells, then the columns shared, then notes no page links to, then zip
    order. Exact titles go first (``C`` is not ``C++``); then titles as
    ``_title_key`` compares them, where a note a page links to takes a
    matching value as well (a linked ``C++`` is no row ``C``). Both ask a
    note with property lines for one of the columns (a row page can show a
    property its CSV leaves out), then, for the rows still without a page,
    ask nothing: a first line of ``Attendees: Ada`` reads as a property. A
    note a database takes is its row whichever page links to it. Walked
    breadth-first from the files nothing names, then from the first file
    still not reached (a sub-page whose line links back to its top names
    it), the first to name a file is its parent: the page above beats a
    link to it from a sibling."""
    members = {plan.page_id for plan in flat}

    def titles(plan):
        return {_title_key(plan.title), _title_key(_notion_file(plan.entry.path)[0])}

    exact, close, links = {}, {}, {}
    for plan in flat:
        if _split_ext(plan.entry.path)[1] in _MD_EXTS:
            exact.setdefault(_norm_name(plan.title), []).append(plan)
            for key in titles(plan):
                close.setdefault(key, []).append(plan)
        kids = []
        for label, href in plan.links:
            kid = linked_to(plan, href)
            if (kid is not None and kid is not plan and kid.page_id in members
                    and (not label.strip() or _title_key(label) in titles(kid))):
                kids.append(kid)
        links[plan.page_id] = list(dict.fromkeys(kids))
    linked = {kid.page_id for kids in links.values() for kid in kids}

    databases = [db for db in flat if _split_ext(db.entry.path)[1] == ".csv" and db.rows]
    named_rows = {}  # database's page id → row name → the rows of that name
    for db in databases:
        for name, cells in zip(_row_names(db), db.rows[1:]):
            named_rows.setdefault(db.page_id, {}).setdefault(name, []).append(cells)

    row_of, rows, found = {}, {}, {}
    for index, key, strict in ((exact, _norm_name, True), (close, _title_key, True),
                               (exact, _norm_name, False), (close, _title_key, False)):
        offers = []
        for db in databases:
            header = [cell.strip() for cell in db.rows[0]]
            columns = set(header[1:])
            for name, same in named_rows.get(db.page_id, {}).items():
                if found.get((db.page_id, name), 0) >= len(same):
                    continue
                fit = _row_fit(header, same)
                for note in dict.fromkeys(index.get(key(name), [])):
                    if note.page_id in row_of or (strict and note.fields and not columns & note.fields.keys()):
                        continue
                    score = fit(note)
                    if index is exact or note.page_id not in linked or score:
                        offers.append((-score, -len(columns & note.fields.keys()), note.page_id in linked,
                                       db, name, len(same), note))
        offers.sort(key=lambda offer: offer[:3])
        for *_, db, name, count, note in offers:
            if note.page_id not in row_of and found.get((db.page_id, name), 0) < count:
                row_of[note.page_id] = db.page_id
                rows.setdefault(db.page_id, []).append(note)
                found[db.page_id, name] = found.get((db.page_id, name), 0) + 1

    named = {plan.page_id: rows.get(plan.page_id, []) + [kid for kid in links[plan.page_id] if kid.page_id not in row_of]
             for plan in flat}
    named_ids = {kid.page_id for kids in named.values() for kid in kids}
    parent, reached, walk = {}, set(), []
    for seed in [plan for plan in flat if plan.page_id not in named_ids] + flat:
        if seed.page_id in reached:
            continue
        reached.add(seed.page_id)
        walk.append(seed)
        i = len(walk) - 1
        while i < len(walk):
            plan, i = walk[i], i + 1
            for kid in named[plan.page_id]:
                if kid.page_id not in reached:
                    reached.add(kid.page_id)
                    walk.append(kid)
                    parent[kid.page_id] = plan.page_id
    return parent


# --- the block tree: ids, anchors, headings ----------------------------------

def _prepare_tree(nodes, plan, places=None):
    """Give every node an id, take Obsidian ``^id`` anchors out of the text
    (``plan.anchors``: marker → block id; an anchor on its own line belongs
    to the block before it) and index heading texts (``plan.headings``:
    normalized text → block id, first wins) so ``[[Note#…]]`` links can
    target blocks. Those targets' ids are kept in ``plan.ids`` by their
    place in the tree (``places`` counts them), so the note parsed again
    gets them again: the import parses each note once for its targets and
    once when it writes it, never holding every tree at once."""
    places = places if places is not None else itertools.count()
    kept = []  # (place, node)

    def target(place, node):
        return plan.ids.setdefault(place, node["id"])

    for node in nodes:
        content = node.get("content", "")
        alone = _ANCHOR_LINE_RE.match(content.strip())
        if alone and kept and not node.get("children"):
            plan.anchors.setdefault(alone.group(1).lower(), target(*kept[-1]))
            continue
        place = next(places)
        node["id"] = plan.ids.get(place) or new_block_id()
        lines = content.split("\n")
        if len(lines) > 1 and _ANCHOR_LINE_RE.match(lines[-1].strip()):
            plan.anchors.setdefault(lines[-1].strip()[1:].lower(), target(place, node))
            lines.pop()
        for idx in {len(lines) - 1, 0}:
            m = _ANCHOR_END_RE.search(lines[idx])
            if m:
                plan.anchors.setdefault(m.group(1).lower(), target(place, node))
                lines[idx] = lines[idx][:m.start()]
        node["content"] = "\n".join(lines).strip("\n")
        hm = _HEADING_TEXT_RE.match(node["content"].split("\n")[0])
        if hm:
            plan.headings.setdefault(_norm_heading(hm.group(1)), target(place, node))
        node["children"] = _prepare_tree(node.get("children") or [], plan, places)
        kept.append((place, node))
    return [node for _, node in kept]


def _walk(nodes):
    for node in nodes:
        yield node
        yield from _walk(node.get("children") or [])


# --- storing -----------------------------------------------------------------

def markdown_page(ws: str, conn, raw: bytes, original: str, folder: str = "", *, actor: str) -> dict:
    """One Markdown file → a note page made by ``actor``: title from front
    matter else the file name, blocks from the body, filed in the folder
    ``folder`` (an id; "" the library's top) or — with a front-matter
    ``folder:`` path (what Gamma's own export writes) — in the folder at
    that path below it, made where missing in the page's own transaction
    (``ops.apply_batches``: a caller may hold the write lock over a lookup
    of its own). The page records ``markdown_import`` = the content hash of
    ``raw`` — the same digest a stored upload of the file is named by, which
    is how a file chip finds the page made from it. Commits. Raises
    HTTPException 413/400 for an oversized (5 MB, MAX_BLOCKS) or non-UTF-8
    file, 400 for a ``folder`` that is no folder. Shared by POST
    /import/markdown (a fresh upload) and POST /pages/from-file (a stored
    one)."""
    if len(raw) > MAX_MARKDOWN_BYTES:
        raise HTTPException(status_code=413, detail="Markdown file exceeds 5 MB")
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise HTTPException(status_code=400, detail="Markdown file must be UTF-8")
    fields, body = parse_frontmatter(text)
    fallback = re.sub(r"\.(?:md|markdown)$", "", original, flags=re.I).strip() or "Untitled note"
    title = (fm_text(fields, "title") or fallback).strip()[:500]
    try:
        tree = md_to_blocks(body)
    except TooManyBlocks as exc:
        raise HTTPException(status_code=413, detail=f"Markdown file has {exc}")
    props = {"original_filename": original, "markdown_import": content_digest(raw)}
    page_id = new_block_id()
    write_lock(conn)
    try:
        under, _ = destination(conn, folder)
        path = split_path(fm_text(fields, "folder"))
        tree_ops, ids = folder_inserts(conn, [path], under)
        filed = _filed_in(under, ids, path)
        if filed:
            props["folders"] = [filed]
        results = apply_batches(conn, [(FOLDERS, tree_ops)], actor=actor) if tree_ops else []
        imported = insert_page_tree(conn, page_id, title, props, tree, actor)
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    for result in results:
        after_commit(ws, conn, result)
    return {"block_id": page_id, "title": title, "original_filename": original,
            "imported": imported, "folders": props.get("folders", [])}


class _Plan:
    __slots__ = ("entry", "page_id", "title", "path", "alone", "labels", "body", "props", "existing",
                 "tree", "anchors", "headings", "ids", "links", "rows", "fields")

    def __init__(self, entry):
        self.entry = entry
        self.page_id = new_block_id()
        self.title = ""
        self.path = []      # the folder path below the destination (names)
        self.alone = []     # a sub-page's: the path it takes when its parent is left out
        self.labels = []    # label names
        self.body = ""      # until the page is written
        self.props = {}
        self.existing = False
        self.tree = []      # while the page is written
        self.anchors = {}
        self.headings = {}
        self.ids = {}       # place in the tree → id, of the blocks links can target
        self.links = []     # (label, href) of the lines that are one link (_link_lines)
        self.rows = []      # a database's: its CSV's rows, the header first
        self.fields = {}    # a Notion row page's property lines (_properties)


def import_markdown_zip(ws: str, zf: zipfile.ZipFile, conn, folder: str = "", *, actor: str = "",
                        preview: bool = False, selected: set[str] | None = None, progress=None) -> dict:
    """Import every note in ``zf`` into workspace ``ws`` through the open ``pages.db``
    connection, its pages made by ``actor`` and filed below the folder
    ``folder`` (an id; "" the library's top; 400 when it is no folder). The
    folders and labels the chosen notes need are made first, one committed
    batch per tree (``ops.ensure_filing``: they stay if the import stops);
    then the pages are written a few at a time, each batch its own short
    transaction committed here once their bundled files are stored (a long
    import never holds the write lock). A ``preview`` writes nothing.
    ``progress`` (a background job's report, gamma/jobs.py) hears each
    note; a stopped job keeps the notes it got to. Returns the report
    dict: each page entry's ``folders`` are the paths (lists of names from
    the top) the page is, or would be, filed in, and its ``parent`` the id
    of the page it is, or would be, inside ("" for a library page)."""
    with contextlib.ExitStack() as unpacked:  # the zips opened inside it
        return _import_zip(ws, zf, conn, folder, unpacked, actor=actor, preview=preview, selected=selected,
                           progress=progress)


def _import_zip(ws, zf, conn, folder, unpacked, *, actor, preview, selected, progress) -> dict:
    progress = progress or jobs.no_progress
    under, under_path = destination(conn, folder)
    entries = []
    walk = new_walk(unpacked)
    walk_zip(zf, "", entries, walk)
    check_ratio(walk)
    _strip_wrappers(entries)
    report = {"pages_created": 0, "pages_skipped": 0, "blocks_imported": 0,
              "assets_stored": 0, "links_resolved": 0, "notion": False, "obsidian": False,
              "pages": [], "warnings": [], "entries": archive_entries(zf), "folder": folder}
    current_selection = None

    def warn(title, reason):
        if len(report["warnings"]) < 200:
            report["warnings"].append({"title": title, "reason": reason, "selection_id": current_selection})

    for path, reason in walk["left_out"]:
        warn(path, reason)

    # An Obsidian vault carries its settings folder; the folder itself (and
    # the vault's trash) holds no notes.
    vault = any(".obsidian" in e.path.split("/") for e in entries)
    report["obsidian"] = vault
    entries = [e for e in entries if not (set(e.path.split("/")[:-1]) & _SKIP_DIRS)]
    for e in entries:
        if _split_ext(e.path)[1] == ".canvas":
            warn(e.path, "Obsidian canvas files are not imported")

    notes = sorted((e for e in entries if _split_ext(e.path)[1] in _MD_EXTS),
                   key=lambda e: (e.path.count("/"), e.path.lower()))
    csvs = [e for e in entries if _split_ext(e.path)[1] == ".csv"]
    assets = {e.path: e for e in entries if _is_asset_ext(_split_ext(e.path)[1])}
    if not notes and not csvs:
        raise HTTPException(status_code=400, detail="no .md files in the zip")

    # Notion writes `Name <id>.csv` and `Name <id>_all.csv` for one database
    # (the latter has every row regardless of the view); keep one page per
    # database and let links to either file resolve to it. The id stays in
    # the key: two databases of one name in a page are two pages.
    databases, csv_alias = {}, {}
    for e in csvs:
        stem, _ = _split_ext(e.path)
        key = (posixpath.dirname(e.path), re.sub(r"_all$", "", stem).lower())
        cur = databases.get(key)
        if cur is None or (stem.endswith("_all") and not _split_ext(cur.path)[0].endswith("_all")):
            databases[key] = e
    for e in csvs:
        stem, _ = _split_ext(e.path)
        key = (posixpath.dirname(e.path), re.sub(r"_all$", "", stem).lower())
        csv_alias[e.path] = databases[key]
    views = {db.path: e for e in csvs if (db := csv_alias[e.path]) is not e}

    # Already-imported pages: same bytes, or the same Notion page.
    by_digest, by_notion, existing_pages = {}, {}, {}
    for pid, raw_props, title in conn.execute(
            "SELECT id, properties, content FROM unified_blocks WHERE parent_id = 'root'"):
        try:
            props = json.loads(raw_props or "{}")
        except (TypeError, ValueError):
            continue
        existing_pages[pid] = (title, props)
        if props.get("markdown_import"):
            by_digest.setdefault(props["markdown_import"], []).append(pid)
        if props.get("notion_id"):
            by_notion.setdefault(props["notion_id"], pid)

    plans, targets, plan_of = [], {}, {}
    taken = set()  # pages a Notion note of the zip found by their bytes
    for e in notes + list(databases.values()):
        if e.size > MAX_MARKDOWN_BYTES:
            warn(e.path, "file exceeds 5 MB")
            continue
        raw = e.read()
        plan = _Plan(e)
        stem, ext = _split_ext(e.path)
        notion_id = _notion_id(re.sub(r"_all$", "", stem)) if ext == ".csv" else _notion_id(stem)
        digest = content_digest(raw)
        plan.props = {"original_filename": posixpath.basename(e.path), "markdown_import": digest}
        if notion_id:
            plan.props["notion_id"] = notion_id
            report["notion"] = True
        plan.path = _dir_path(e.path)
        if ext == ".csv":
            plan.title = _clean_stem(re.sub(r"_all$", "", stem))[:500] or "Database"
            view = views.get(e.path)
            try:
                head = _csv_rows(view.read())[:1] if view is not None and view.size <= MAX_MARKDOWN_BYTES else []
                plan.body, cut = _csv_to_markdown(raw, head[0] if head else None)
                plan.rows = _csv_rows(raw, blank=True)
            except csv.Error as exc:
                warn(e.path, f"not a readable CSV: {exc}")
                continue
            if cut:
                warn(e.path, f"{cut} columns past the first {MAX_TABLE_COLS} are left out")
        else:
            try:
                text = raw.decode("utf-8-sig")
            except UnicodeDecodeError:
                warn(e.path, "not UTF-8")
                continue
            fm, body = parse_frontmatter(text)
            title, body = _take_title(body, fm_text(fm, "title") or None,
                                      vault_stem=_clean_stem(stem) if vault else None)
            plan.title = (title or _clean_stem(stem)).strip()[:500] or "Untitled note"
            if fm.get("folder") is not None:
                plan.path = split_path(fm_text(fm, "folder"))
            bibtex, body = _take_bibtex(body)
            if bibtex:
                plan.props["bibtex"] = bibtex
            if any(fm.get(k) for k in ("doi", "authors", "year")):
                plan.props["meta"] = {
                    "title": plan.title, "doi": fm_text(fm, "doi"),
                    "authors": fm_list(fm, "authors"),
                    "year": fm_text(fm, "year"), "source": "manual",
                }
            if fm_text(fm, "source"):
                plan.props["_source"] = _wiki_source(fm_text(fm, "source"))
            if fm_text(fm, "parent"):  # (a wikilink, the note it names)
                plan.props["_parent"] = (fm_text(fm, "parent").strip().startswith("[["),
                                         _wiki_source(fm_text(fm, "parent")))
            # Obsidian's built-in properties (and their pre-1.9 singular
            # names): tags are labels, aliases ride along.
            tags = (t.lstrip("#").strip() for t in fm_list(fm, "tags") + fm_list(fm, "tag"))
            plan.labels = list(dict.fromkeys(t for t in tags if t))
            aliases = [a for a in fm_list(fm, "aliases") + fm_list(fm, "alias") if a]
            if aliases:
                plan.props["aliases"] = list(dict.fromkeys(aliases))
            if vault:
                body = _strip_comments(body)
            plan.body = body
            if notion_id:
                plan.body = _drop_quote_tails(_convert_asides(body))
                plan.links = _link_lines(plan.body)
                plan.fields = _properties(plan.body)
        # A Notion page is the page of its id: two pages of one template
        # (``# Untitled`` and a status line) have the same bytes. Its bytes
        # find only the page made from this file alone (POST
        # /import/markdown: no Notion id, the file's name), once.
        existing = by_notion.get(notion_id) if notion_id else None
        for pid in by_digest.get(digest, ()) if existing is None else ():
            known = existing_pages[pid][1]
            if not notion_id:
                existing = pid
                break
            if (pid not in taken and not known.get("notion_id")
                    and known.get("original_filename") == posixpath.basename(e.path)):
                existing = pid
                taken.add(pid)
                break
        if existing:
            plan.existing = True
            plan.page_id = existing
            plan.title, plan.props = existing_pages[existing]
            plan.props = dict(plan.props)
            plan.body = ""
        elif plan.body.strip() and (not preview or len(plan.body) >= 2 * MAX_BLOCKS):
            # The ids links may target (the tree itself is parsed again when
            # the page is written). A preview needs none: it parses only a
            # body long enough to hold too many blocks (a block takes a line
            # of its own, two characters at least).
            try:
                _prepare_tree(md_to_blocks(plan.body), plan)
            except TooManyBlocks as exc:
                warn(e.path, f"has {exc}: not imported")
                continue
        plans.append(plan)
        targets[e.path] = plan.page_id
        plan_of[plan.page_id] = plan
    # A database's page can come as a note of the database's own id beside
    # its CSV (``People <id>.md``: a line linking to the CSV, the view's
    # filters and sort). It is the database's page: its other lines go
    # above the table, and links to it resolve to that page.
    database_of = {(posixpath.dirname(p.entry.path), _notion_file(p.entry.path)[1]): p
                   for p in plans if _split_ext(p.entry.path)[1] == ".csv" and _notion_file(p.entry.path)[1]}
    for stub in [p for p in plans if _split_ext(p.entry.path)[1] in _MD_EXTS]:
        nid = _notion_file(stub.entry.path)[1]
        db = database_of.get((posixpath.dirname(stub.entry.path), nid)) if nid else None
        if db is None:
            continue
        plans.remove(stub)
        if stub.page_id != db.page_id:  # (imported before, both are the database's page)
            del plan_of[stub.page_id]
        plan_of[db.page_id] = db
        targets[stub.entry.path] = db.page_id
        rest = "\n".join(line for line in stub.body.split("\n")
                         if not ((m := _LINK_RE.fullmatch(line.strip())) and nid in unquote(m.group(3)).lower()))
        if rest.strip() and not db.existing:
            db.body = rest.strip() + "\n\n" + db.body
            db.ids.clear()
        if not db.existing and len(stub.title) > len(db.title):  # (the CSV's name is cut at 50 characters)
            db.title = stub.title
    for path, db_entry in csv_alias.items():
        if db_entry.path in targets:
            targets[path] = targets[db_entry.path]

    validate_selection(selected, (p.entry.path for p in plans))
    excluded = {p.entry.path for p in plans if selected is not None and p.entry.path not in selected and not p.existing}
    # Unselected notes must not become dangling mentions to newly generated IDs.
    excluded_ids = {p.page_id for p in plans if p.entry.path in excluded}
    # A note's parent is read off every note of the zip, chosen or not (a
    # parent left out then names no page, and nothing else takes its place),
    # its links off the chosen ones only.
    every = dict(targets)
    targets = {path: pid for path, pid in targets.items() if pid not in excluded_ids}
    # Notion id → page, for relations (the pages of the import, then the
    # library's).
    notion_pages = {plan.props["notion_id"]: plan.page_id for plan in plans
                    if plan.props.get("notion_id") and plan.page_id not in excluded_ids}
    for nid, pid in by_notion.items():
        notion_pages.setdefault(nid, pid)

    # Basename lookup, the way Obsidian resolves a bare [[Note]] or
    # ![[image.png]] wherever the file sits: lower-cased stem (notes) or
    # filename (assets) → zip paths.
    assets_by_name = {}
    for path in assets:
        assets_by_name.setdefault(posixpath.basename(path).lower(), []).append(path)

    def lookup(notes):
        """``(lower-cased path → path, lower-cased stem → note paths)`` over
        the notes ``notes`` and the bundled files."""
        lower, stems = {path.lower(): path for path in assets}, {}
        for path in notes:
            lower[path.lower()] = path
            stems.setdefault(_split_ext(path)[0].lower(), []).append(path)
        return lower, stems

    by_lower, notes_by_stem = lookup(targets)
    every_lookup = lookup(every)

    def nearest(base_dir, paths):
        same = [p for p in paths if posixpath.dirname(p) == base_dir]
        return min(same or paths, key=lambda p: (p.count("/"), p.lower()))

    def resolve(base_dir, href, tables=None):
        """A link target → the zip path it names, or None. Relative to the
        note first (Markdown links), then by exact vault path, then by
        basename anywhere (wikilinks, "shortest path" Markdown links).
        ``tables``: a ``lookup`` to read instead of the chosen notes'."""
        lower, stems = tables or (by_lower, notes_by_stem)
        if not href or _SCHEME_RE.match(href) or href.startswith(("#", "/")):
            return None
        target = re.split(r"[#?]", href, maxsplit=1)[0].strip()
        if not target:
            return None
        cands = list(dict.fromkeys((unquote(target), target)))
        for cand in cands:
            cand = unicodedata.normalize("NFC", cand)
            for full in (posixpath.normpath(posixpath.join(base_dir, cand)) if base_dir else cand,
                         posixpath.normpath(cand)):
                for variant in (full, full + ".md"):
                    hit = lower.get(variant.lower())
                    if hit:
                        return hit
        for cand in cands:
            leaf = unicodedata.normalize("NFC", posixpath.basename(cand)).lower()
            stem, ext = _split_ext(leaf)
            paths = (stems.get(stem) if ext in _MD_EXTS or not ext else None) \
                or stems.get(leaf) or assets_by_name.get(leaf)
            if not paths and ext == ".csv" and _notion_id(re.sub(r"_all$", "", stem)):
                # (the view's CSV is left out of the export: the page of its id)
                paths = stems.get(re.sub(r"_all$", "", stem))
            if paths:
                return nearest(base_dir, paths)
        return None

    stored = {}

    def store_asset(path):
        """Upload a bundled file once; None when it can't be (type, quota).
        A file is read only to be stored, streamed into storage (a vault's
        video can be gigabytes); a preview reads no more than a PDF's
        header."""
        if path in stored:
            return stored[path]
        url = None
        entry = assets.get(path)
        if entry is not None:
            ext = _split_ext(path)[1]
            if ext == ".pdf" and not is_pdf(entry.head(4)):
                warn(path, "not a valid PDF")
            elif preview:
                url = f"/api/uploads/preview{ext}"
                report["assets_stored"] += 1
            elif shutil.disk_usage(config.DATA_DIR).free - entry.size < MIN_FREE_BYTES:
                warn(path, "the server's disk has no room for this file")
            else:
                try:
                    with entry.open() as src:
                        filename, _ = store_file_stream(ws, src, ext)
                    url = f"/api/uploads/{filename}"
                    report["assets_stored"] += 1
                except HTTPException as exc:
                    warn(path, str(exc.detail))
                except ValueError as exc:  # not what its extension says (storage.content_refusal)
                    warn(path, str(exc))
        stored[path] = url
        return url

    def block_target(page_id, sub):
        """``#Heading`` / ``#^id`` inside a link to ``page_id`` → the block it
        names, else the page itself."""
        plan = plan_of.get(page_id)
        last = next((part.strip() for part in reversed((sub or "").split("#")) if part.strip()), "")
        if plan is None or not last:
            return page_id
        if last.startswith("^"):
            return plan.anchors.get(last[1:].lower(), page_id)
        return plan.headings.get(_norm_heading(last), page_id)

    def rewrite_links(body, base_dir, page_id):
        def md_repl(m):
            bang, label, href, title = m.groups()
            href = href.strip("<>").strip()
            path = resolve(base_dir, href)
            if path is None:
                if href and not _SCHEME_RE.match(href) and not href.startswith(("#", "/")):
                    warn(current_selection, f"Missing or unselected linked file: {href}; the link is kept as written")
                return m.group(0)
            if (path in targets and targets[path] == page_id and _split_ext(path)[1] != ".csv"
                    and _split_ext(unquote(href))[1] == ".csv"):  # a page's own CSV is not in the zip
                warn(current_selection, f"Missing or unselected linked file: {href}; the link is kept as written")
                return m.group(0)
            if path in targets:
                report["links_resolved"] += 1
                sub = unquote(href.partition("#")[2])
                return f"[[{block_target(targets[path], '#' + sub if sub else '')}]]"
            url = store_asset(path)
            if not url:
                return m.group(0)
            return f"{bang}[{label}]({url}{title})"

        def wiki_repl(m):
            bang, target, sub, alias = m.groups()
            target = target.strip()
            if target:
                path = resolve(base_dir, target)
            else:
                path = None if not sub else "#self"      # [[#Heading]] — this note
            if path is None:
                if target and not _SCHEME_RE.match(target):
                    warn(current_selection, f"Missing or unselected note or attachment: {target}; the link is kept as written")
                return m.group(0)
            if path == "#self" or path in targets:
                report["links_resolved"] += 1
                pid = page_id if path == "#self" else targets[path]
                bid = block_target(pid, sub)
                # An embed of one anchored block is a synced block; a whole
                # note or a section has no single block to sync (a Gamma
                # embed shows one block), so those are mentions.
                anchored = sub.split("#")[-1].startswith("^") and bid != pid
                return f"![[{bid}]]" if bang and anchored else f"[[{bid}]]"
            url = store_asset(path)
            if not url:
                return m.group(0)
            leaf = posixpath.basename(path)
            alias = (alias or "").strip()
            if bang and _split_ext(path)[1] in IMAGE_MEDIA_TYPES:
                size = alias if _IMAGE_SIZE_RE.match(alias) else ""
                alt = "" if size else alias
                return f"![{alt}|{size}]({url})" if size else f"![{alt}]({url})"
            return f"[{alias or leaf}]({url})"

        def relation_page(nid, href):
            """The file a relation's path names when it has the relation's
            id (a zip can hold a page twice), else the page of the id."""
            path = resolve(base_dir, href)
            return targets[path] if path in targets and _notion_file(path)[1] == nid else notion_pages.get(nid)

        body = _LINK_RE.sub(md_repl, _WIKILINK_RE.sub(wiki_repl, body))
        if notion_pages:
            body, linked = _link_relations(body, relation_page)
            report["links_resolved"] += linked
        return body

    staged = []  # pages whose files are stored, written PAGES_PER_COMMIT at a time

    def flush():
        """Write the staged pages in one short transaction."""
        if not staged:
            return
        try:
            for plan in staged:
                report["blocks_imported"] += insert_page_tree(conn, plan.page_id, plan.title, plan.props,
                                                              plan.tree, actor)
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
        for plan in staged:
            plan.tree = []
        staged.clear()

    def parent_note(note, wiki, ref):
        """The note of the import a ``parent:`` value of the note ``note``
        names, or None: the note beside the note's directory when its name
        is the value's (the layout the exports write: ``A.md`` beside
        ``A/B.md``), else a wikilink's exact vault path (Obsidian reads
        ``[[a/b]]`` from the vault's top), else as a link (``resolve``) —
        never the note itself. Read off every note, chosen or not."""
        lower = every_lookup[0]
        base = posixpath.dirname(note)
        name = unicodedata.normalize("NFC", _split_ext(posixpath.basename(unquote(ref)))[0]).lower()
        beside = lower.get(f"{base}.md".lower()) if base else None
        if beside in every and _split_ext(posixpath.basename(beside))[0].lower() == name:
            return beside
        if wiki:
            exact = posixpath.normpath(unicodedata.normalize("NFC", ref))
            for variant in (exact, exact + ".md"):
                if lower.get(variant.lower()) in every and lower[variant.lower()] != note:
                    return lower[variant.lower()]
        path = resolve(base, ref, every_lookup)
        return path if path != note and path in every else None

    # Notion's page directories. Until about November 2025 one is named as
    # its page's file (``Title <id>/`` beside ``Title <id>.md``, a database's
    # beside its ``Title <id>.csv``); since, as the title alone (``Title/``),
    # and the second of one title in a directory with the page id's first
    # and last four digits (``Title abcd-wxyz/``).
    plan_at = {plan.entry.path: plan for plan in plans}
    beside = {}    # (directory, normalized title) → the Notion files of that title
    notes_in = {}  # directory → the notes directly in it
    for path, plan in plan_at.items():
        title, nid = _notion_file(path)
        if nid:
            beside.setdefault((posixpath.dirname(path), title), []).append(path)
        if _split_ext(path)[1] in _MD_EXTS:
            notes_in.setdefault(posixpath.dirname(path), []).append(plan)
    directories = {d for e in entries
                   for d in itertools.accumulate(e.path.split("/")[:-1], lambda a, b: f"{a}/{b}")}

    def partial_owner(base):
        m = _PARTIAL_ID_RE.search(posixpath.basename(base))
        if not m:
            return None
        title = _norm_name(posixpath.basename(base)[:m.start()])
        cands = [p for p in beside.get((posixpath.dirname(base), title), [])
                 if _notion_file(p)[1].startswith(m.group(1).lower()) and _notion_file(p)[1].endswith(m.group(2).lower())]
        return cands[0] if len(cands) == 1 else None

    claimed = {partial_owner(d) for d in directories} - {None}

    def owner_of(base):
        """The note or database (its path) whose directory ``base`` is, or
        None. A name that looks like a partial id (``Budget 2024-2025``) but
        names no page of the shorter title is a plain title. A plain
        ``Title/`` beside several files of that title is the one not claimed
        by a ``Title abcd-wxyz/``, else the one whose lines link into it,
        else the database whose rows the notes in it are."""
        name = posixpath.basename(base)
        if _NOTION_ID_RE.search(name):
            return next((p for p in [base + ext for ext in _MD_EXTS] + [f"{base}.csv", f"{base}_all.csv"]
                         if p in every), None)
        if _PARTIAL_ID_RE.search(name) and (owner := partial_owner(base)):
            return owner
        cands = [p for p in beside.get((posixpath.dirname(base), _norm_name(name)), []) if p not in claimed]
        if len(cands) > 1:
            into = [p for p in cands if any(
                posixpath.normpath(posixpath.join(posixpath.dirname(p), unquote(href))).startswith(base + "/")
                for _, href in plan_at[p].links)]
            if len(into) != 1:
                titles = {_norm_name(plan.title) for plan in notes_in.get(base, [])}
                into = [p for p in cands if _split_ext(p)[1] == ".csv" and titles
                        and titles <= set(_row_names(plan_at[p]))]
            cands = into
        return cands[0] if len(cands) == 1 else None

    owners = {}

    def paired_page(note):
        """The page whose directory, in Notion's layout, holds the note
        ``note``, or None."""
        base = posixpath.dirname(note)
        if base and base not in owners:
            owners[base] = owner_of(base)
        return every.get(owners.get(base))

    chosen = [plan for plan in plans if selected is None or plan.entry.path in selected]
    # A new note whose ``parent:`` names another note of the import, or
    # else that sits in a Notion page's directory, is a sub-page of that
    # note's page (new, or the one it was imported as before), written with
    # the key (the page ids are known before any page is): filed where its
    # top is, it needs no folder. Parents that close a cycle, or nest deeper
    # than MAX_PAGE_DEPTH with the library's pages above them, are dropped:
    # those notes are filed by their directories, as library pages. Both are
    # worked out over every note of the zip, then a parent left out of the
    # selection is dropped too, so the preview (every note chosen) tells
    # where any selection files each note (``folders_alone``).
    library_parents = page_parents(conn)
    parent_of = {}
    for plan in plans:
        if plan.existing:
            continue
        wiki, ref = plan.props.pop("_parent", None) or (False, "")
        note = parent_note(plan.entry.path, wiki, ref) if ref else None
        parent = every[note] if note else paired_page(plan.entry.path)
        if parent and parent != plan.page_id:
            parent_of[plan.page_id] = parent

    # A CSV has no H1 and Notion cuts a file's name at 50 characters: such a
    # database's title is the label of the line that links to it.
    cut = {plan.page_id: plan for plan in plans
           if _split_ext(plan.entry.path)[1] == ".csv" and len(plan.title) >= 49 and not plan.existing}
    for plan in plans if cut else ():
        for label, href in plan.links:
            db = cut.get(every.get(resolve(posixpath.dirname(plan.entry.path), href, every_lookup)))
            if db is not None and len(label) > len(db.title) and _loose(label).startswith(_loose(db.title)):
                db.title = label.strip()[:500]

    # A flat Notion export ("Create folders for subpages" off) has every page,
    # database and attachment in one directory and no directory in it; its
    # tree is in the pages themselves (_flat_tree).
    order = {e.path: i for i, e in enumerate(entries)}
    flat = sorted((plan for plan in plans if _notion_file(plan.entry.path)[1]),
                  key=lambda plan: order.get(plan.entry.path, 0))
    base_dir = posixpath.dirname(flat[0].entry.path) if flat else ""
    if (len(flat) > 1 and all(posixpath.dirname(plan.entry.path) == base_dir for plan in flat)
            and not any(posixpath.dirname(d) == base_dir for d in directories)):
        tree = _flat_tree(flat, lambda plan, href: plan_of.get(every.get(resolve(base_dir, href, every_lookup))))
        for page_id, up in tree.items():
            if not plan_of[page_id].existing:
                parent_of.setdefault(page_id, up)

    def above(page_id):
        """The parent of a page of a walk up: the import's, else the library's."""
        return parent_of.get(page_id) or library_parents.get(page_id)

    for page_id in list(parent_of):
        walk = [page_id]
        while (up := above(walk[-1])) is not None and up not in walk and len(walk) <= MAX_PAGE_DEPTH:
            walk.append(up)
        if up in walk:
            for page in walk[walk.index(up):]:
                parent_of.pop(page, None)
        elif len(walk) > MAX_PAGE_DEPTH:
            parent_of.pop(page_id, None)
    parent_of = {page_id: up for page_id, up in parent_of.items() if up not in excluded_ids}
    for plan in chosen:
        if plan.page_id in parent_of:
            plan.props[PARENT_PAGE] = parent_of[plan.page_id]
            plan.alone, plan.path = plan.path, []

    def depth(page_id):
        """How many pages of the import a page sits below."""
        n = 0
        while page_id in parent_of:
            page_id, n = parent_of[page_id], n + 1
        return n

    # Each parent is written before the pages inside it, so a stopped or
    # failed import leaves no page inside one it never wrote (a database's
    # rows come after its table, though its CSV is read after every note);
    # within a depth the notes keep their order.
    chosen.sort(key=lambda plan: depth(plan.page_id))
    paths = folder_paths(conn)
    folder_ids, label_ids = {}, {}
    new = [plan for plan in chosen if not plan.existing]
    if new and not preview:
        folder_ids, label_ids = ensure_filing(ws, conn, paths=[p.path for p in new],
                                              labels=[name for p in new for name in p.labels],
                                              under=under, actor=actor)

    def shown(plan):
        """The paths (names from the top) the page is, or would be, filed
        in: a sub-page's are its top's, up through the new pages (a parent
        is a page of the import) to a new library page or a page already
        in the library, whose top is the library's."""
        while not plan.existing and PARENT_PAGE in plan.props:
            plan = plan_of[plan.props[PARENT_PAGE]]
        if plan.existing:
            top = existing_pages.get(top_of(library_parents, plan.page_id), ("", {}))[1]
            return [paths[f] for f in filing(top, FOLDERS) if f in paths]
        return [under_path + plan.path] if under_path or plan.path else []

    def alone(plan):
        """A new sub-page's paths when the page it is inside is left out of
        the selection (it is then filed by its directory, as a library page
        is): what the review shows for a page ticked without its parent."""
        return {"folders_alone": [under_path + plan.alone] if under_path or plan.alone else []}             if not plan.existing and PARENT_PAGE in plan.props else {}

    def inside(plan):
        """The id of the page the page is, or would be, inside; "" for a
        library page."""
        return library_parents.get(plan.page_id, "") if plan.existing else plan.props.get(PARENT_PAGE, "")

    for n, plan in enumerate(chosen):
        try:
            progress(done=n, total=len(chosen), unit="items", item=plan.title)
        except BaseException:  # stopped: the staged notes' files are stored, so they go in too
            flush()
            raise
        current_selection = plan.entry.path
        warning_start = len(report["warnings"])
        if plan.existing:
            report["pages_skipped"] += 1
            report["pages"].append({"id": plan.page_id, "title": plan.title,
                                    "folders": shown(plan), "parent": inside(plan), "selection_ids": [plan.entry.path],
                                    "source_path": plan.entry.source_path, "source_paths": [plan.entry.outer],
                                    "kind": "pdf" if plan.props.get("doc_id") else "page", "created": False,
                                    "action": "skip", "warnings": [], "missing": False})
            continue
        base_dir = posixpath.dirname(plan.entry.path)
        source = plan.props.pop("_source", None)
        if source:
            path = resolve(base_dir, source)
            if path and _split_ext(path)[1] == ".pdf":
                url = store_asset(path)
                if url:
                    plan.props["doc_id"] = url.rsplit("/", 1)[1][:-4]
            elif _SCHEME_RE.match(source) and source.lower().startswith(("http://", "https://")):
                plan.props["source_url"] = source
            else:
                warn(plan.title, f"Missing PDF attachment: {source}")
        if not preview:
            filed = _filed_in(under, folder_ids, plan.path)
            if filed and PARENT_PAGE not in plan.props:
                plan.props["folders"] = [filed]
            if plan.labels:
                plan.props["labels"] = list(dict.fromkeys(label_ids[name] for name in plan.labels))
        # The page's bundled files are stored while its links are rewritten —
        # before its rows, outside any transaction. A preview, which writes
        # no tree, reads the links off the body.
        if preview:
            rewrite_links(plan.body, base_dir, plan.page_id)
        elif plan.body.strip():
            plan.tree = _prepare_tree(md_to_blocks(plan.body), plan)
            for node in _walk(plan.tree):
                node["content"] = rewrite_links(node["content"], base_dir, plan.page_id)
        plan.body = ""
        if not preview:
            staged.append(plan)
            if len(staged) >= PAGES_PER_COMMIT:
                flush()
        report["pages_created"] += 1
        warnings = report["warnings"][warning_start:]
        report["pages"].append({"id": plan.page_id, "title": plan.title,
                                "folders": shown(plan), "parent": inside(plan), **alone(plan),
                                "selection_ids": [plan.entry.path],
                                "source_path": plan.entry.source_path, "source_paths": [plan.entry.outer],
                                "kind": "pdf" if plan.props.get("doc_id") else "page", "created": not plan.existing,
                                "action": "skip" if plan.existing else "create", "warnings": warnings,
                                "missing": any("Missing" in w["reason"] for w in warnings)})

    flush()
    log.info(f"[markdown-zip] {report['pages_created']} pages, {report['pages_skipped']} skipped, "
             f"{report['assets_stored']} files, {report['links_resolved']} links"
             f"{' (Notion)' if report['notion'] else ''}{' (Obsidian vault)' if vault else ''}")
    return report
