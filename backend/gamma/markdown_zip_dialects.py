"""What differs between the kinds of Markdown zip (the import itself is
gamma/markdown_zip_import.py). A zip is read in one dialect, picked once
after the walk (``dialect_of``), and ``ZipRun`` asks it wherever the kinds
part:

- ``Vault``, a zip with ``.obsidian`` in it: the filename is the title,
  ``%%comments%%`` go, ``^id`` anchors are block targets;
- ``NotionDialect`` (gamma/notion_import.py), a zip with a Notion id in a
  file name or a page of Notion's HTML export in it (``html_pages``): page
  ids, databases, page directories, a flat export's tree, relations.
  ``[[x]]``, ``^2`` and ``<tag>`` are text in a file with an id, and its
  headings hold nothing; a note of no id, which no export of Notion's holds,
  is read as ``Plain`` reads it;
- ``Plain``, any other: a zipped folder, and Gamma's own exports. It keeps an
  ``^id`` only when a ``#^id`` link in the zip names it, because folder sync
  writes those without an ``.obsidian``, and ``E = m c ^2`` ends the same way.

``Dialect`` is what they share: a stage that is no dialect's business does
nothing here. Only Notion has table pages; any other CSV, Notion's too when
it has no id, is a file a note can link.

A dialect says what a file is; ``PageSource`` is how a page is read out of one.
"""

import re

from .import_zip import READ_ERRORS
from .markdown_import import _FENCE_LINE_RE, MAX_MARKDOWN_BYTES, _fence_marks, shuts_code, text_column
from .markdown_zip_links import _MD_EXTS, MD_LINKS_RE, _split_ext
from .notion_html import is_notion_page, opens_notion_page

# (a closing run of # counts after a space only, as in CommonMark: "# C#" is "C#";
# the text ends on no space, and the first run gives nothing back: a heading of
# only spaces would try the rest of the line from each of them)
_H1_RE = re.compile(r"^#\s++(.+?)(?<!\s)(?:\s+#+)?\s*$")
_ESCAPED_RE = re.compile(r"\\([!-/:-@\[-`{-~])")
# Obsidian block anchors: `` ^id`` at the end of a line, or ``^id`` alone on
# a line after a list / quote / table / fence.
_ANCHOR_END_RE = re.compile(r"(?<![ \t])[ \t]+\^([A-Za-z0-9-]+)[ \t]*$")
_ANCHOR_LINE_RE = re.compile(r"^\^([A-Za-z0-9-]+)$")
_ANCHOR_LINK_RE = re.compile(r"#(?:\^|%5E)([A-Za-z0-9-]+)", re.I)
_COMMENT_RE = re.compile(r"%%.*?%%", re.S)
_SKIP_DIRS = {".obsidian", ".trash"}
# How much of a .html it takes to see that it opens as a page of Notion's: its
# stylesheet comes first, and <article> follows at up to 17,696 bytes. The body
# comes after the page's properties, which run as long as they like.
_HTML_HEAD = 64 * 1024


def _skipped(path: str) -> bool:
    """A vault's settings folder and its trash hold no notes."""
    return bool(set(path.split("/")[:-1]) & _SKIP_DIRS)


def _take_title(body: str, fm_title, vault_stem=None):
    """The title and the body without its title line. Outside a vault a
    leading ``# H1`` is the title when there is no front-matter title or it
    repeats it — Notion and Gamma both write one. In a vault the filename
    (``vault_stem``) is the title unless the front matter says otherwise, and
    the H1 is only stripped when it repeats that title. An H1 taken for the
    title is Markdown, so ``\\[`` in it is "[", as the Notion export writes it."""
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
    return fm_title or _ESCAPED_RE.sub(r"\1", h1), "\n".join(lines[1:]).lstrip("\n")


def _strip_comments(body: str) -> str:
    """Remove Obsidian ``%%…%%`` comments (inline or block) outside fences."""
    out, run = [], []
    marks, col = "", 0  # the open fence's

    def flush():
        if run:
            out.append(_COMMENT_RE.sub("", "\n".join(run)))
            run.clear()

    for line in body.split("\n"):
        if marks:
            out.append(line)
            marks = "" if shuts_code(line, marks, col) else marks
        elif _FENCE_LINE_RE.match(line):
            flush()
            out.append(line)
            marks, col = _fence_marks(line), text_column(line)
        else:
            run.append(line)
    flush()
    return "\n".join(out)


class PageSource:
    """How a page is read out of its file (``Plan.source``), one of each per
    kind of file, holding nothing of a run. ``link_re`` is what
    ``Links.rewrite_links`` looks for in the page's text, ``relations`` whether
    that text writes Notion's relations as a CSV cell does."""
    link_re = MD_LINKS_RE
    relations = True

    def read(self, run, plan, raw: bytes) -> bool:
        """The plan's title, properties and body; False: left out, warned."""
        raise NotImplementedError

    def blocks(self, run, plan) -> list:
        """The page's tree, the same one every call: ``plan.ids`` go by place
        in it. Raises ``TooManyBlocks``."""
        raise NotImplementedError

    def link_text(self, plan) -> str:
        """The page's text as a preview scans it for links, and a stub folds."""
        raise NotImplementedError

    def holds_links(self, content: str) -> bool:
        """Whether a block's content is one ``rewrite_links`` reads."""
        return True


class Dialect:
    """The rules of one kind of zip, for one run: the stages take the
    ``ZipRun``. Here, what a stage does in a dialect with no rule of its own."""
    name = ""
    page_exts = _MD_EXTS    # what a file of a page ends in, once it is known to be one
    html_source = None      # the ``PageSource`` of a page in HTML

    # --- reading the files ----------------------------------------------------

    def is_page(self, path: str) -> bool:
        """Whether the file at ``path`` is a page of text (not a table)."""
        return _split_ext(path)[1] in _MD_EXTS

    def page_id(self, stem: str):
        """The id of the page a file of the stem ``stem`` holds, or None."""
        return None

    def tables(self, csvs) -> tuple[list, list]:
        """The CSVs (entries) that are pages, and the paths of those that
        are files a note can link."""
        return [], [e.path for e in csvs]

    def take_title(self, plan, body: str, fm_title):
        return _take_title(body, fm_title)

    def read_body(self, plan, body: str) -> str:
        """A note's body, what the dialect takes out of it gone."""
        return body

    def scan(self, run, plan):
        """A note's body, a skipped note's too, before any body is parsed."""

    def flat_headings(self, plan) -> bool:
        """Whether the note's headings hold nothing under them."""
        return False

    def keeps_anchor(self, run, plan, marker: str) -> bool:
        """Whether an ``^id`` in the note's text is an anchor, not text."""
        return False

    # --- the stages -----------------------------------------------------------

    def fold(self, run):
        """Plans that are one page become one."""

    def build_links(self, run):
        """What ``rewrite`` needs, once ``Links`` knows where notes go."""

    def pair_directories(self, run):
        """What ``paired_page`` needs."""

    def paired_page(self, run, note: str):
        """The page whose directory holds the note ``note``, or None."""
        return None

    def titles(self, run):
        """Titles the links tell."""

    def layout(self, run):
        """The parents a zip with no ``parent:`` holds some other way."""

    # --- links ----------------------------------------------------------------

    def wikilinks(self, plan) -> bool:
        """Whether a ``[[Note]]`` in the note is a link, not text."""
        return True

    def absent_csv(self, leaf: str):
        """The stem (lower-cased) of the note that stands for a CSV the zip
        does not hold, ``leaf`` being the file a link names; else None."""
        return None

    def rewrite(self, run, body: str, base_dir: str) -> str:
        """A body whose links are done (``Links.rewrite_links``)."""
        return body


class Plain(Dialect):
    name = "plain"

    def scan(self, run, plan):
        run.linked_anchors.update(marker.lower() for marker in _ANCHOR_LINK_RE.findall(plan.body))

    def keeps_anchor(self, run, plan, marker):
        return marker.lower() in run.linked_anchors


class Vault(Dialect):
    name = "vault"

    def take_title(self, plan, body, fm_title):
        return _take_title(body, fm_title, vault_stem=plan.file_title)

    def read_body(self, plan, body):
        return _strip_comments(body)

    def keeps_anchor(self, run, plan, marker):
        return True


def _is_html_page(entry, warn) -> bool:
    """Whether a .html is a page of Notion's HTML export. One that opens as a
    page is read on for its body, as far as the import would read it; a file
    longer than that counts, for the import to say it left it out. One that
    cannot be read is none, and warned of: every .html of a zip comes here,
    linked or not."""
    try:
        head = entry.head(_HTML_HEAD)
        return is_notion_page(head) or (opens_notion_page(head) and (
            entry.size > MAX_MARKDOWN_BYTES or is_notion_page(entry.head(MAX_MARKDOWN_BYTES))))
    except READ_ERRORS:
        warn(entry.path, "this file could not be read")
        return False


def html_pages(entries, warn) -> set:
    """The paths of the entries that are pages of Notion's HTML export. Any
    other .html, an ``index.html`` too, stays a bundled file. ``warn(path,
    reason)`` hears of one that cannot be read."""
    return {e.path for e in entries
            if _split_ext(e.path)[1] == ".html" and not _skipped(e.path) and _is_html_page(e, warn)}


def dialect_of(paths, html=()) -> Dialect:
    """The dialect of a zip holding the files ``paths``, ``html`` being the
    pages of Notion's HTML export among them. An Obsidian vault carries its
    settings folder, whatever its files are called (a vault's ``Foo
    <32-hex>.md`` is a Notion leftover, nothing of an export)."""
    from .notion_import import NotionDialect, _notion_file  # (it builds on Dialect)
    if any(".obsidian" in path.split("/") for path in paths):
        return Vault()
    if html or any(_split_ext(path)[1] in (*_MD_EXTS, ".csv") and _notion_file(path)[1]
                   for path in paths if not _skipped(path)):
        return NotionDialect(html)
    return Plain()
