"""Render pages for Notion's file importer: one ``<Title>.md`` per page, a
sub-page's inside a directory named for its parent, folders as directories,
and a page's images in a directory named for it (``Paper.md``,
``Paper/<sha>.png``, ``Paper/Child.md``). Notion's own layout, with no id.

A page goes ``export_walk.walk`` → ``notion_gamma.from_gamma`` →
``notion_blocks.lower_for_file`` → ``notion_nfm.write``, so what a probe learns
of the importer changes a row of ``notion_blocks.CAPS``, not this module.
``PageLinks`` is that writer's view of the zip, one for each page, as the links
in a page are relative to its file.

The file name is the title (``vault_name``). A page opens with a heading holding
the title only if its name had to change, or if an H1 of its notes would open
the file: whichever of the two Notion's importer takes for the title, and a
read back takes the H1, no title is lost. There is no front matter, which
Notion would show as text: the paper's ``bibtex`` fence comes first, as the
zip import reads one only at a body's start, then its ``DOI:`` lines, and a
divider opening the file is ``***``, which opens no front matter. What
Notion's importer leaves out stays out of the zip: a PDF, a recording or any
other file is its name, a link if it has a web address, and a warning under
its page in the job's result, where the limits of the import warn too. So does
a page nested too deep for the pipeline, which recurses a level at a time: it
is left out.
"""

import posixpath
import re
from urllib.parse import quote

from . import bibtex as bibtex_mod
from .documents.model import page_document
from .export_walk import walk
from .markdown_export import UPLOAD_RE
from .notion_blocks import Block, lower_for_file
from .notion_gamma import from_gamma
from .notion_nfm import write
from .obsidian_export import name_files

# What Notion's help page gives as the limits of an import: a file on the Free
# plan, and how many files are "likely to fail".
MAX_FILE_BYTES = 5 * 1024 * 1024
MAX_FILES = 10_000


class NotionContext:
    """What a page render needs from the export set. ``resolve_ref`` is
    ``markdown_export.block_ref_resolver``'s; ``bundle(name, stem)`` puts the
    upload ``name`` in the zip beside the page file ``stem`` and answers its
    size, None for a file that is gone."""

    def __init__(self, resolve_ref, bundle, highlights=True, notes=True):
        self.resolve_ref, self.bundle = resolve_ref, bundle
        self.highlights, self.notes = highlights, notes
        self.files = {}         # page id → arcname (dir/Title.md)
        self.sub_pages = {}     # page id → (id, title) of the pages inside it that have a file, in export order
        self.warnings = []      # what the job's result lists

    def name_pages(self, fil, pages, directory):
        """``pages`` as ``obsidian_export.name_files`` takes them, each file
        placed by ``directory``. Every name comes first: A and B can mention
        each other."""
        stems = name_files(fil, pages, directory=directory)
        self.files = {pid: f"{stem}.md" for pid, stem in stems.items()}
        for pid, title, _ in pages:
            if (parent := fil.parents.get(pid)) in stems:
                self.sub_pages.setdefault(parent, []).append((pid, _title(title)))


class PageLinks:
    """``notion_nfm.Links`` for one page of the zip, with the ``ref`` that
    ``from_gamma`` also asks for. Targets are relative to the page's file and
    percent-encoded, the form the writer puts in a link: Notion's own links
    encode spaces and non-ASCII too."""

    def __init__(self, ctx: NotionContext, page_id: str, title: str):
        self.ctx, self.page_id, self.title = ctx, page_id, title
        self.stem = ctx.files[page_id].removesuffix(".md")
        self.here = self.stem.rpartition("/")[0]
        self.warned = set()

    def _relative(self, arcname: str) -> str:
        return quote(posixpath.relpath(arcname, self.here or "."))

    def page(self, ref) -> str | None:
        arcname = self.ctx.files.get(ref)
        return self._relative(arcname) if arcname else None

    def asset(self, url, kind) -> str | None:
        """An image: its upload in the zip, or a web address as it is. Any
        other kind (``from_gamma`` says ``file`` for an upload linked in text,
        the writer ``pdf``, ``audio`` or ``video`` for a block) stays out: the
        importer would drop it."""
        if kind != "image":
            return None
        upload = UPLOAD_RE.search(url or "")
        size = self.ctx.bundle(upload[1], self.stem) if upload else None
        if size is None:
            return url if re.match(r"https?://", url or "") else None
        if size > MAX_FILE_BYTES:
            self.warn(upload[1], "over 5 MB, too big for Notion's Free plan")
        return self._relative(f"{self.stem}/{upload[1]}")

    def warn(self, title, reason) -> None:
        reason = f"{title}: {reason}"
        if reason not in self.warned:
            self.warned.add(reason)
            self.ctx.warnings.append({"page_id": self.page_id, "title": self.title, "reason": reason})

    def ref(self, block_id):
        return self.ctx.resolve_ref(block_id)


def _title(title: str) -> str:
    return (title or "").strip() or "Untitled"


def _text(line: str) -> Block:
    return Block("paragraph", [("text", line, {})])


def _opens_with_h1(blocks: list) -> bool:
    """Asked of the file's first block: a folded heading is a toggle until it
    is lowered."""
    first = lower_for_file(blocks[:1])
    return bool(first) and first[0].type == "heading" and first[0].attrs.get("level") == 1


def _head(props: dict, title: str, links: PageLinks, tags, body: list) -> list:
    """What a page says of itself before its notes (``body``): its ``bibtex``
    fence, the paper's record, its PDF's name, under its title if the file
    name cannot hold that or an H1 of the notes would open the file."""
    head = []
    if entry := bibtex_mod.page_entry(props):
        head.append(Block("code", attrs={"text": entry, "language": "bibtex"}))
    meta = props.get("meta") if isinstance(props.get("meta"), dict) else {}
    authors = meta.get("authors")
    authors = ", ".join(map(str, authors)) if isinstance(authors, list) else authors
    record = [("DOI", meta.get("doi")), ("Authors", authors), ("Year", meta.get("year")),
              ("Tags", ", ".join(dict.fromkeys(tags)))]
    head += [_text(f"{key}: {value}") for key, value in record if value]
    if view := page_document(props):
        head.append(Block("file", [("text", view["name"], {})] if view["name"] else [],
                          attrs={"url": view["url"], "media": view["format"]}))
    if links.stem.rpartition("/")[2] != title or _opens_with_h1(head + body):
        head.insert(0, Block("heading", [("text", title, {})], attrs={"level": 1}))
    return head


def _lines_to(blocks: list) -> set:
    """The pages a line of ``blocks`` is nothing but a link to."""
    found = set()
    for block in blocks:
        if block.type == "page":
            found.add(block.attrs.get("ref"))
        found |= _lines_to(block.children)
    return found


def _sub_page_lines(page_id: str, blocks: list, links: PageLinks) -> list:
    """A link line for each page inside this one that no line of it stands
    for already: a [[child]] alone stays where it is."""
    there = _lines_to(blocks)
    return [Block("page", [("text", title, {})], attrs={"ref": pid, "url": links.page(pid)})
            for pid, title in links.ctx.sub_pages.get(page_id, []) if pid not in there]


def render_notion_page(page, ctx: NotionContext, tags=()) -> str | None:
    """One page as its file's text; ``tags`` are its labels' names. Its images
    reach the zip and its warnings ``ctx`` as it renders. None for a page
    nested too deep to write, which a warning says."""
    props, title, page_id = page.get("properties") or {}, _title(page.get("content")), page["id"]
    links = PageLinks(ctx, page_id, title)
    try:
        body = from_gamma(walk(page, highlights=ctx.highlights, notes=ctx.notes), links, page_id)
        blocks = [*_head(props, title, links, tags, body), *body, *_sub_page_lines(page_id, body, links)]
        text = write(lower_for_file(blocks), links, form="file") + "\n"
    except RecursionError:
        links.warn(ctx.files[page_id], "nested too deep to write, left out")
        return None
    if text.startswith("---\n"):                 # a divider, but it would open front matter
        text = "***" + text[3:]
    if len(text.encode("utf-8")) > MAX_FILE_BYTES:
        links.warn(ctx.files[page_id], "over 5 MB, too big for Notion's Free plan")
    return text
