"""What only Notion's exports need of a Markdown zip's import
(gamma/markdown_zip_import.py): how its files name a page, how it writes
asides, quotes and relations, its database CSVs as tables, and the tree of a
flat export, as pure functions; ``NotionHtmlSource``, a page of its HTML export;
and ``NotionDialect``, the rules of such a zip on a ``ZipRun``
(gamma/markdown_zip_dialects.py).
"""

import bisect
import csv
import io
import itertools
import json
import posixpath
import re
import unicodedata
from urllib.parse import quote, unquote

from .import_zip import READ_ERRORS
from .markdown_import import _FENCE_LINE_RE, MAX_BLOCKS, MAX_MARKDOWN_BYTES, TooManyBlocks
from .markdown_zip_dialects import PageSource, Plain
from .markdown_zip_links import (_LINK_RE, _MD_EXTS, _NOTION_ID_RE, _SCHEME_RE, WRITTEN_LINKS_RE, _clean_stem,
                                 _notion_id, _split_ext, _walk)
from .notion_gamma import to_gamma
from .notion_html import plain_text, read_page

# A Notion database's rows, in tables of at most this many rows (and
# characters): each table one block the editor renders and can save
# (ops.MAX_CONTENT is 200,000 characters), but for a single row past that:
# it stays whole, to read, and takes shortening before it saves.
TABLE_ROWS = 500
TABLE_CHARS = 100_000
MAX_TABLE_COLS = 40
# (a cell can be as long as the file: Notion's relation cells run long)
csv.field_size_limit(max(csv.field_size_limit(), MAX_MARKDOWN_BYTES))
# Since about November 2025 a page's directory is named as the page without
# its id, and a second one of the same name in a directory carries the
# page id's first and last four digits: ``Title abcd-wxyz/``.
_PARTIAL_ID_RE = re.compile(r"(?<!\s)\s+([0-9a-f]{4})-([0-9a-f]{4})$", re.I)
# A relation in a Notion database cell or row property: ``Title (path)``,
# the path the related page's file (``…%20<id>.md``, or ``.html`` in a CSV of
# the HTML export; Notion encodes the spaces, not the parentheses, which a
# title cut at 50 characters can leave unbalanced), relations joined by ", ".
_RELATION_RE = re.compile(r" \((\S*?%20([0-9a-f]{32})\.(?:md|html))\)", re.I)
# A row page's property line, ``Name: value``, as Notion writes them under
# the title.
_PROPERTY_RE = re.compile(r"([^:|\n]{1,100}): ?(.*)$")
_TABLE_LINE_RE = re.compile(r"[ \t]*\|")
_CELL_BAR_RE = re.compile(r"(?<!\\)\|")
# In a line of Markdown, what ``_escape_line`` leaves whole (code, math, a
# link's target, Notion's own HTML), else the ``<`` of a tag. A target is only
# its "](": looking for its ")" here scans the rest of the line from every "]("
# with none after it. A code span's mark is at most 16 long: a shorter run that
# nothing closes scans the line again for each length a line can hold.
_TAG_RE = re.compile(r"(`{1,16}).+?\1(?!`)|\$\$.+?\$\$|\$[^\s$](?:[^$\n]*[^\s$])?\$|\]\(|</?u>|<img\b"
                     r"|(?<!\\)<(?=/?[A-Za-z])")
# What a line of a quote holds before its text.
_QUOTE_RE = re.compile(r"(?:[ \t]*>)*")
# A line, behind its quote, that opens a code fence, also on a list item's
# marker's line (``- ```py``); one that closes on its line is inline code.
_FENCE_OPEN_RE = re.compile(r"[ \t]*(?:(?:[-*+]|\d{1,4}[.)])[ \t]+)*(```|~~~)(?!.*\1)")
# A code block or a formula as ``to_gamma`` writes it, the whole of a block; in
# text it escapes, so nothing else has this shape.
_VERBATIM_RE = re.compile(r"(`{3,})[^\n]*\n.*\n\1|\$\$\n.*\n\$\$", re.S)


# --- names -------------------------------------------------------------------

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


# --- what Notion writes ------------------------------------------------------

def _without_icon(line: str) -> str:
    """A line with its ``<img>`` tags gone. Past the last ">" no tag closes,
    and looking for its end there reads the line again per ``<img``."""
    end = line.rfind(">") + 1
    return re.sub(r"<img\b[^>]*>", "", line[:end]) + line[end:]


def _convert_asides(body: str) -> str:
    """Notion's ``<aside>`` (its tags alone on their lines, indented as the
    block is in a toggle or a list item) → a ``> [!info]`` callout, its
    first line the title. Since 2026 Notion writes the icon alone on that
    line and a blank line after it; that blank goes (after a first line of
    text it parts paragraphs). An ``<aside>`` in a code block is code."""
    lines, out, in_fence, i = body.split("\n"), [], False, 0
    closes = [j for j, line in enumerate(lines) if line.strip() == "</aside>"]
    while i < len(lines):
        line = lines[i]
        i += 1
        if _FENCE_LINE_RE.match(line):
            in_fence = not in_fence
        end = None
        if not in_fence and line.strip() == "<aside>":
            at = bisect.bisect_left(closes, i)
            end = closes[at] if at < len(closes) else None
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
        if not re.search(r"\w", _without_icon(inner[0])):
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


def _escape_line(line: str) -> str:
    """A line of text with the ``<`` of its tags escaped (``_TAG_RE``). A
    link's target runs to the first ")" after its "](", if the line has one."""
    out, done, pos, last = [], 0, 0, line.rfind(")")
    while m := _TAG_RE.search(line, pos):
        pos = m.end()
        if m.group(0) == "<":
            out += [line[done:m.start()], "\\"]
            done = m.start()
        elif m.group(0) == "](" and pos <= last:
            pos = line.index(")", pos) + 1
    return "".join(out) + line[done:]


def _escape_tags(body: str) -> str:
    """Notion escapes nothing, so a ``<tag>`` in its Markdown is text, which a
    note would read as HTML and drop: ``\\<tag>``. Code and math stay as they
    are, and a link's target, and what Notion means as HTML: ``<u>`` and the
    ``<img>`` icon of a callout. A fence belongs to its quote: only a line of
    that quote closes it, and the quote's end does too."""
    out, fence, in_math = [], None, False   # fence: the open fence's quote depth
    for line in body.split("\n"):
        quote = _QUOTE_RE.match(line).group(0)
        rest, depth = line[len(quote):], quote.count(">")
        if fence is not None and depth < fence:
            fence = None
        if fence is None and _FENCE_OPEN_RE.match(rest):
            fence = depth
        elif fence == depth and _FENCE_LINE_RE.match(rest):
            fence = None
        elif fence is None and line.count("$$") % 2:
            in_math = not in_math
        elif fence is None and not in_math:
            line = _escape_line(line)
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
    out, pos, last, line, n, table = [], 0, 0, 0, 0, None  # table: whether the line is one, read once a line
    for m in _RELATION_RE.finditer(text):
        nl = text.rfind("\n", last, m.start())  # (else on the line of the relation before)
        if nl >= 0:
            line, table = nl + 1, None
        start = max(line, last)
        if table is None:
            table = bool(_TABLE_LINE_RE.match(text, line))
        if table:
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


# --- a database's CSV --------------------------------------------------------

def _csv_rows(data: bytes, blank=False) -> list:
    """A CSV's rows, without the wholly blank ones; ``blank`` keeps those
    after the first row (a row page can stand for one: an untitled row of
    no values). Lines end as the file ends them, a bare CR too."""
    text = data.decode("utf-8-sig", errors="replace")
    rows = list(itertools.dropwhile(lambda r: not any(c.strip() for c in r),
                                    csv.reader(io.StringIO(text, newline=""))))
    return rows if blank else [r for r in rows if any(c.strip() for c in r)]


def _csv_to_markdown(data: bytes, order=None) -> tuple[str, int]:
    """A Notion database CSV → GFM tables, cells escaped as a note is
    (``_escape_tags``) and pipe-escaped, their lines joined by ``<br>``: every row,
    TABLE_ROWS (or TABLE_CHARS) to a table, each table under the header and
    a block of its own. ``order``: column names to put first, in that order
    (the view's, whose CSV lists them as the view shows them while the
    ``_all`` one sorts them). Returns the Markdown and the number of columns
    left out past MAX_TABLE_COLS."""
    rows = _csv_rows(data)
    if not rows:
        return "", 0
    columns = max(len(r) for r in rows)
    width = min(columns, MAX_TABLE_COLS)
    cols = range(width)
    if order:  # (cells past the header's stay after its columns; no row is widened to the header)
        header = rows[0]
        rank = {name: i for i, name in enumerate(order)}
        cols = sorted(range(len(header)), key=lambda i: (rank.get(header[i], len(rank)), i))
        cols = (cols + list(range(len(header), columns)))[:width]

    def cell(v):
        lines = (ln.strip() for ln in _escape_tags((v or "").strip()).split("\n"))
        return "<br>".join(filter(None, lines)).replace("|", "\\|") or " "

    def line(r):
        return "| " + " | ".join(cell(r[i] if i < len(r) else "") for i in cols) + " |"

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
        if _split_ext(plan.entry.path)[1] != ".csv":
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


# --- a page of the HTML export -------------------------------------------------

def _databases(blocks: list):
    """The blocks that stand for a database, in the page's order."""
    for block in blocks:
        if block.type == "page" and block.attrs.get("database"):
            yield block
        yield from _databases(block.children)


def _relative(path: str, base_dir: str) -> str:
    """A link from a file in ``base_dir`` to the file at ``path``."""
    return quote(posixpath.relpath(f"/{path}", f"/{base_dir}"))


def _into_zip(url: str | None) -> bool:
    """Whether a link names a file of the zip, a page's or a bundled one
    (``Links.resolve``)."""
    return bool(url) and not _SCHEME_RE.match(url) and not url.startswith(("#", "/"))


def _whole_label(leaf: tuple, linked: bool = False) -> tuple:
    """A leaf of rich text, its brackets gone if it is words of a link into the
    zip (or ``linked`` says it is). The writer puts a "]" out of a label,
    cutting the link in two around it, and such a link has to stay one for the
    import to find it: to a page it becomes a mention, which says the title
    whatever the label did, and to a file a link under a name short of them."""
    if leaf[0] == "text" and (linked or _into_zip(leaf[2].get("link"))):
        return ("text", re.sub(r"[\[\]]", "", leaf[1]), leaf[2])
    return leaf


def _whole_links(blocks: list, refs: dict):
    """Every link into the zip kept whole. A page's or a file's block is one
    link, to where ``to_gamma`` sends it, when it has somewhere to go."""
    for block in blocks:
        target = refs.get(block.attrs.get("database")) or block.attrs.get("url")
        linked = block.type in ("page", "file") and _into_zip(target)
        block.rich = [_whole_label(leaf, linked) for leaf in block.rich]
        if block.type == "table":
            block.attrs["rows"] = [[[_whole_label(leaf) for leaf in cell] for cell in row]
                                   for row in block.attrs["rows"]]
        _whole_links(block.children, refs)


class NotionHtmlSource(PageSource):
    """A page of the HTML export. It is read once, and ``plan.body`` is what
    ``to_gamma`` makes of it as JSON, so a plan holds no tree and every call
    gives the same one. That is Markdown in which a literal is escaped and a
    relation is a link, so its links are looked for around code and math, and
    a block of code or a formula is not looked in at all.

    A row's values are what its page shows, which ``_row_fit`` matches against
    its CSV's cells: a date here is ISO where the cell has the long form and a
    relation the title where the cell has ``Title (path)``, so neither counts
    towards the row in a flat export, and two rows of one name that differ in
    nothing else go by zip order. With directories no row is matched."""
    link_re = WRITTEN_LINKS_RE
    relations = False

    def read(self, run, plan, raw):
        try:
            page = read_page(raw.decode("utf-8-sig"))
        except UnicodeDecodeError:
            run.warn(plan.entry.path, "not UTF-8")
            return False
        for warning in page.warnings:
            run.warn(plan.entry.path, warning)
        plan.title = (page.title or plan.file_title).strip()[:500] or "Untitled note"
        if page.notion_id:
            plan.notion_id = plan.props["notion_id"] = page.notion_id
            run.report["notion"] = True
        plan.fields = {key: plain_text(value) for key, value in page.fields.items()}
        run.dialect.db_order.update(page.table_order)
        run.dialect.db_title.update(page.table_title)
        base_dir, refs = posixpath.dirname(plan.entry.path), {}
        plan.links = list(page.links)
        for block in _databases(page.blocks):
            csv_path = run.dialect.csv_by_id.get(block.attrs["database"])
            if csv_path:
                refs[block.attrs["database"]] = _relative(csv_path, base_dir)
                plan.links.append((plain_text(block.rich), refs[block.attrs["database"]]))
        _whole_links(page.blocks, refs)
        plan.body = json.dumps(to_gamma(page, refs), ensure_ascii=False)
        return True

    def blocks(self, run, plan):
        nodes = json.loads(plan.body)
        if sum(1 for _ in _walk(nodes)) > MAX_BLOCKS:
            raise TooManyBlocks()
        return nodes

    def link_text(self, plan):
        nodes = json.loads(plan.body or "[]")
        return "\n".join(node["content"] for node in _walk(nodes) if self.holds_links(node["content"]))

    def holds_links(self, content):
        return not _VERBATIM_RE.fullmatch(content)


# --- the dialect -------------------------------------------------------------

class NotionDialect(Plain):
    """A zip of Notion's. A file of no id beside an export's is no page of
    it, and ``Plain``'s to read (a folder with a Notion leftover in it):
    ``read_body``, ``tables``, the tree, wikilinks and anchors go by each
    file's own id. The rest, relations, is the zip's."""
    name = "notion"
    page_exts = (*_MD_EXTS, ".html")
    html_source = NotionHtmlSource()

    def __init__(self, html=()):
        self.html = html        # the pages of its HTML export (``html_pages``)
        self.csv_alias = {}     # a database's every CSV → the one read
        self.csv_by_id = {}     # database id → the path of the CSV read
        self.views = {}         # the one read → its view's CSV
        self.notion_pages = {}  # Notion id → page
        self.db_order = {}      # database id → its columns, as an HTML page's view shows them
        self.db_title = {}      # database id → its name, as an HTML page has it uncut

    def is_page(self, path):
        return path in self.html or super().is_page(path)

    def page_id(self, stem):
        return _notion_id(stem)

    def tables(self, csvs):
        # Notion writes `Name <id>.csv` and `Name <id>_all.csv` for one database
        # (the latter has every row regardless of the view); keep one page per
        # database and let links to either file resolve to it. The id stays in
        # the key: two databases of one name in a page are two pages.
        databases, own = {}, [e for e in csvs if _notion_file(e.path)[1]]
        for e in own:
            stem, _ = _split_ext(e.path)
            key = (posixpath.dirname(e.path), re.sub(r"_all$", "", stem).lower())
            cur = databases.get(key)
            if cur is None or (stem.endswith("_all") and not _split_ext(cur.path)[0].endswith("_all")):
                databases[key] = e
        for e in own:
            stem, _ = _split_ext(e.path)
            key = (posixpath.dirname(e.path), re.sub(r"_all$", "", stem).lower())
            self.csv_alias[e.path] = databases[key]
        self.views = {db.path: e for e in own if (db := self.csv_alias[e.path]) is not e}
        self.csv_by_id = {_notion_file(e.path)[1]: self.csv_alias[e.path].path for e in own}
        return list(databases.values()), [e.path for e in csvs if e.path not in self.csv_alias]

    def view_columns(self, run, path):
        """The columns of the view's CSV beside the database's CSV at ``path``,
        as the view lists them, or None: the table does without them."""
        view = self.views.get(path)
        if view is None or view.size > MAX_MARKDOWN_BYTES:
            return None
        try:
            rows = _csv_rows(view.read())
        except READ_ERRORS:
            run.warn(view.path, "this file could not be read")
            return None
        return rows[0] if rows else None

    def read_table(self, run, plan, raw) -> bool:
        """A database's title, table and rows; False: left out, warned."""
        path = plan.entry.path
        # (HTML pages read first: what they know of it is there by now)
        plan.title = (self.db_title.get(plan.notion_id) or plan.file_title)[:500] or "Database"
        try:
            plan.body, cut = _csv_to_markdown(raw, self.view_columns(run, path) or self.db_order.get(plan.notion_id))
            plan.rows = _csv_rows(raw, blank=True)
        except csv.Error as exc:
            run.warn(path, f"not a readable CSV: {exc}")
            return False
        if cut:
            run.warn(path, f"{cut} columns past the first {MAX_TABLE_COLS} are left out")
        return True

    def read_body(self, plan, body):
        if plan.notion_id:
            body = _drop_quote_tails(_convert_asides(body))
            plan.links = _link_lines(body)
            plan.fields = _properties(body)
            body = _escape_tags(body)
        return body

    def scan(self, run, plan):
        if not plan.notion_id:
            super().scan(run, plan)

    def flat_headings(self, plan):
        return bool(plan.notion_id)

    def keeps_anchor(self, run, plan, marker):
        return not plan.notion_id and super().keeps_anchor(run, plan, marker)

    def wikilinks(self, plan):
        return not plan.notion_id

    def fold(self, run):
        # A database's page can come as a note of the database's own id beside
        # its CSV (``People <id>.md``: a line linking to the CSV, the view's
        # filters and sort). It is the database's page: its other lines go
        # above the table, and links to it resolve to that page. Text that
        # takes the page past MAX_BLOCKS stays out: the rows are the database.
        database_of = {(posixpath.dirname(p.entry.path), p.notion_id): p
                       for p in run.plans if _split_ext(p.entry.path)[1] == ".csv" and p.notion_id}
        for stub in [p for p in run.plans if _split_ext(p.entry.path)[1] in self.page_exts]:
            nid = stub.notion_id
            db = database_of.get((posixpath.dirname(stub.entry.path), nid)) if nid else None
            if db is None:
                continue
            run.plans.remove(stub)
            if stub.page_id != db.page_id:  # (imported before, both are the database's page)
                del run.plan_of[stub.page_id]
            run.plan_of[db.page_id] = db
            run.targets[stub.entry.path] = db.page_id
            rest = "\n".join(line for line in stub.source.link_text(stub).split("\n")
                             if not ((m := _LINK_RE.fullmatch(line.strip())) and nid in unquote(m.group(3)).lower()))
            if rest.strip() and not db.existing:
                table, db.body = db.body, rest.strip() + "\n\n" + db.body
                if exc := run.too_many(db):
                    db.body = table
                    run.warn(stub.entry.path, f"has {exc} with its table: its text is not imported")
            if not db.existing and len(stub.title) > len(db.title):  # (the CSV's name is cut at 50 characters)
                db.title = stub.title
        for path, db_entry in self.csv_alias.items():
            if db_entry.path in run.targets:
                run.targets[path] = run.targets[db_entry.path]

    def build_links(self, run):
        # Notion id → page, for relations (the pages of the import, then the
        # library's).
        self.notion_pages = {plan.props["notion_id"]: plan.page_id for plan in run.plans
                             if plan.props.get("notion_id") and plan.page_id not in run.excluded_ids}
        for nid, pid in run.by_notion.items():
            self.notion_pages.setdefault(nid, pid)

    def absent_csv(self, leaf):
        # (the view's CSV is left out of the export: the page of its id)
        stem, ext = _split_ext(leaf)
        stem = re.sub(r"_all$", "", stem)
        return stem if ext == ".csv" and _notion_id(stem) else None

    def rewrite(self, run, body, base_dir):
        """A body's Notion relations."""
        links, targets = run.links, run.links.targets

        def relation_page(nid, href):
            """The file a relation's path names when it has the relation's
            id (a zip can hold a page twice), else the page of the id."""
            path = links.resolve(base_dir, href)
            return targets[path] if path in targets and _notion_file(path)[1] == nid else self.notion_pages.get(nid)

        if self.notion_pages:
            body, linked = _link_relations(body, relation_page)
            run.report["links_resolved"] += linked
        return body

    # --- the tree -------------------------------------------------------------

    def pair_directories(self, run):
        # Notion's page directories. Until about November 2025 one is named as
        # its page's file (``Title <id>/`` beside ``Title <id>.md``, a database's
        # beside its ``Title <id>.csv``); since, as the title alone (``Title/``),
        # and the second of one title in a directory with the page id's first
        # and last four digits (``Title abcd-wxyz/``).
        self.plan_at = {plan.entry.path: plan for plan in run.plans}
        self.beside = {}    # (directory, normalized title) → the Notion files of that title
        self.notes_in = {}  # directory → the notes directly in it
        for path, plan in self.plan_at.items():
            title, nid = _notion_file(path)
            if nid:
                self.beside.setdefault((posixpath.dirname(path), title), []).append(path)
            if _split_ext(path)[1] in self.page_exts:
                self.notes_in.setdefault(posixpath.dirname(path), []).append(plan)
        self.claimed = {self.partial_owner(d) for d in self.partial_directories(run.order)} - {None}
        self.owners = {}

    @staticmethod
    def partial_directories(paths):
        """The directories above the files ``paths`` named as ``Title
        abcd-wxyz`` is, one that holds only directories too. Read off each
        path and not kept: all of a long path's directories, held, take the
        square of its length."""
        for path in paths:
            start = 0
            while (end := path.find("/", start)) >= 0:
                if _PARTIAL_ID_RE.search(path, start, end):
                    yield path[:end]
                start = end + 1

    def partial_owner(self, base):
        m = _PARTIAL_ID_RE.search(posixpath.basename(base))
        if not m:
            return None
        title = _norm_name(posixpath.basename(base)[:m.start()])
        cands = [p for p in self.beside.get((posixpath.dirname(base), title), [])
                 if _notion_file(p)[1].startswith(m.group(1).lower()) and _notion_file(p)[1].endswith(m.group(2).lower())]
        return cands[0] if len(cands) == 1 else None

    def owner_of(self, run, base):
        """The note or database (its path) whose directory ``base`` is, or
        None. A name that looks like a partial id (``Budget 2024-2025``) but
        names no page of the shorter title is a plain title. A plain
        ``Title/`` beside several files of that title is the one not claimed
        by a ``Title abcd-wxyz/``, else the one whose lines link into it,
        else the database whose rows the notes in it are."""
        name = posixpath.basename(base)
        if _NOTION_ID_RE.search(name):
            return next((p for p in [base + ext for ext in self.page_exts] + [f"{base}.csv", f"{base}_all.csv"]
                         if p in run.links.every), None)
        if _PARTIAL_ID_RE.search(name) and (owner := self.partial_owner(base)):
            return owner
        cands = [p for p in self.beside.get((posixpath.dirname(base), _norm_name(name)), [])
                 if p not in self.claimed]
        if len(cands) > 1:
            into = [p for p in cands if any(
                posixpath.normpath(posixpath.join(posixpath.dirname(p), unquote(href))).startswith(base + "/")
                for _, href in self.plan_at[p].links)]
            if len(into) != 1:
                titles = {_norm_name(plan.title) for plan in self.notes_in.get(base, [])}
                into = [p for p in cands if _split_ext(p)[1] == ".csv" and titles
                        and titles <= set(_row_names(self.plan_at[p]))]
            cands = into
        return cands[0] if len(cands) == 1 else None

    def paired_page(self, run, note):
        base = posixpath.dirname(note)
        if base and base not in self.owners:
            self.owners[base] = self.owner_of(run, base)
        return run.links.every.get(self.owners.get(base))

    def titles(self, run):
        # A CSV has no H1 and Notion cuts a file's name at 50 characters: such a
        # database's title is the label of the line that links to it.
        cut = {plan.page_id: plan for plan in run.plans
               if _split_ext(plan.entry.path)[1] == ".csv" and len(plan.title) >= 49 and not plan.existing}
        every, resolve, lookup = run.links.every, run.links.resolve, run.links.every_lookup
        for plan in run.plans if cut else ():
            for label, href in plan.links:
                db = cut.get(every.get(resolve(posixpath.dirname(plan.entry.path), href, lookup)))
                if db is not None and len(label) > len(db.title) and _loose(label).startswith(_loose(db.title)):
                    db.title = label.strip()[:500]

    def layout(self, run):
        # A flat Notion export ("Create folders for subpages" off) has every page,
        # database and attachment in one directory and no directory in it; its
        # tree is in the pages themselves (_flat_tree).
        flat = sorted((plan for plan in run.plans if plan.notion_id),
                      key=lambda plan: run.order.get(plan.entry.path, 0))
        base_dir = posixpath.dirname(flat[0].entry.path) if flat else ""
        inside = f"{base_dir}/" if base_dir else ""
        if (len(flat) > 1 and all(posixpath.dirname(plan.entry.path) == base_dir for plan in flat)
                and not any(path.startswith(inside) and path.find("/", len(inside)) >= 0 for path in run.order)):
            links = run.links
            tree = _flat_tree(flat, lambda plan, href: run.plan_of.get(
                links.every.get(links.resolve(base_dir, href, links.every_lookup))))
            for page_id, up in tree.items():
                if not run.plan_of[page_id].existing:
                    run.parent_of.setdefault(page_id, up)
