"""Import a zip of Markdown notes as pages — Notion's "Markdown & CSV" or
HTML export, an Obsidian vault, Gamma's own Markdown export, or any zipped
folder of ``.md`` files. One logic serves all of them, in stages (``ZipRun``);
what is true of one kind only is its dialect's
(gamma/markdown_zip_dialects.py), and how a page is read out of its file its
``PageSource``'s (a page of Notion's HTML export is a note like any other, its
blocks read by gamma/notion_html.py and not parsed from Markdown):

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
  ``^id`` anchors themselves are removed from the text: all of a vault's,
  in other zips those a ``#^id`` link names (``E = m c ^2`` ends the same
  way), and none in a file with a Notion id, whose ``[[x]]`` is text too;
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
  line Notion ends a quote with goes. Any other CSV is a bundled file: one
  no new note links stays out, with a warning, and a zip of only those is no
  import;
- Gamma's front matter (``source`` → the bundled PDF or its URL, ``doi`` /
  ``authors`` / ``year`` → metadata) and BibTeX block are restored, and a
  note of the shape its readable export writes reads its bullets as blocks
  (``readable_outline``): any other note's are list blocks;
- a ``.md`` already imported (same bytes, or the same Notion page id) is
  skipped, so re-importing an export adds nothing; links to it still resolve
  to the existing page. Notion splits big exports into ``Part-N.zip`` members
  and wraps workspace exports in ``Export-<uuid>/``: nested zips are read in
  place, one common root directory (and any such wrapper) is dropped.

Links are worked out in gamma/markdown_zip_links.py, and what only Notion's
exports need, its dialect among it, in gamma/notion_import.py.

There is no cap on the size of the zip or its number of notes; what guards
the server is the guarded walk the library imports share (gamma/import_zip.py:
a zip-bomb rule, nested zips spooled to temporary files) and the free disk.
"""

import contextlib
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
from .import_zip import READ_ERRORS, check_ratio, new_walk, walk_zip
from .logbuf import log
from .markdown_import import (_WRAPPED_RE, MAX_BLOCKS, MAX_MARKDOWN_BYTES, TooManyBlocks, fm_list, fm_text,
                              md_to_blocks, parse_frontmatter, readable_outline)
from .markdown_zip_dialects import _ANCHOR_END_RE, _ANCHOR_LINE_RE, PageSource, _skipped, dialect_of, html_pages
from .markdown_zip_links import _MD_EXTS, _SCHEME_RE, Links, _clean_stem, _norm_heading, _split_ext, _walk
from .note_app_import import MAX_WARNINGS
from .ops import after_commit, apply_batches, ensure_filing
from .storage import content_digest, is_pdf, store_file_stream, upload_media_type

# New pages written in one transaction: few commits, and each hold of the
# write lock stays short (a page's files are stored before it is staged).
PAGES_PER_COMMIT = 50

_EXPORT_WRAPPER_RE = re.compile(r"^Export-[0-9a-f-]{8,}$", re.I)
_HEADING_TEXT_RE = re.compile(r"^#{1,6}\s++(.+?)(?<!\s)(?:\s+#+)?\s*$")  # (as ``_H1_RE``)
_BIBTEX_RE = re.compile(r"^```bibtex[ \t]*\n(.*?)\n```[ \t]*\n?", re.S)
# Bundled files that become uploads: anything Gamma would accept from
# POST /api/upload-file (not executable, well-formed extension) that is not
# a note or a canvas.
_NOT_ASSET_EXTS = {".canvas", ""}


def _is_asset_ext(ext: str) -> bool:
    return ext not in _MD_EXTS and ext not in _NOT_ASSET_EXTS and upload_media_type(ext) is not None


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


# --- body preparation --------------------------------------------------------

def _take_bibtex(body: str):
    """The BibTeX block a note opens with and the body without it. The vault
    writes it after a blank line, which a body with no H1 still has."""
    text = body.lstrip("\n")
    m = _BIBTEX_RE.match(text)
    if not m:
        return None, body
    return m.group(1).strip(), text[m.end():].lstrip("\n")


def _wiki_source(value: str) -> str:
    """A front-matter ``source: "[[paper.pdf]]"`` → ``paper.pdf``."""
    m = re.match(r"^\[\[([^\]|#]+)(?:#[^\]|]*)?(?:\|[^\]]*)?\]\]$", value.strip())
    return m.group(1).strip() if m else value.strip()


# --- the block tree: ids, anchors, headings ----------------------------------

def _unwrapped(lines: list) -> list:
    """A bullet's lines, its first just rid of an anchor. The vault writes
    that anchor on the first line of what a bullet wraps (``- $$ ^id``), which
    hid the wrapper from ``md_to_blocks``: the marker goes, and its indent."""
    if not lines[0].startswith("- ") or not _WRAPPED_RE.match(lines[0][2:]):
        return lines
    return [line[2:] if not i or line.startswith("  ") else line for i, line in enumerate(lines)]


def _prepare_tree(nodes, plan, keeps, places=None):
    """Give every node an id, take Obsidian ``^id`` anchors out of the text,
    those ``keeps(marker)`` allows (``plan.anchors``: marker → block id; an
    anchor on its own line belongs to the block before it) and index heading
    texts (``plan.headings``: normalized text → block id, first wins) so
    ``[[Note#…]]`` links can target blocks. Those targets' ids are kept in ``plan.ids`` by their
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
        if alone and keeps(alone.group(1)) and kept and not node.get("children"):
            plan.anchors.setdefault(alone.group(1).lower(), target(*kept[-1]))
            continue
        place = next(places)
        node["id"] = plan.ids.get(place) or new_block_id()
        lines = content.split("\n")
        last = _ANCHOR_LINE_RE.match(lines[-1].strip())
        if len(lines) > 1 and last and keeps(last.group(1)):
            plan.anchors.setdefault(last.group(1).lower(), target(place, node))
            lines.pop()
        for idx in {len(lines) - 1, 0}:
            m = _ANCHOR_END_RE.search(lines[idx])
            if m and keeps(m.group(1)):
                plan.anchors.setdefault(m.group(1).lower(), target(place, node))
                # (``- - x ^id``: unwrapped once already. An anchor hides that of
                # ``- - $$x$$ ^id``, which reaches here just as ``- $$x$$ ^id`` does.)
                wrapped = _WRAPPED_RE.match(lines[idx][2:])
                lines[idx] = lines[idx][:m.start()]
                if not idx and not wrapped:
                    lines = _unwrapped(lines)
        node["content"] = "\n".join(lines).strip("\n")
        hm = _HEADING_TEXT_RE.match(node["content"].split("\n")[0])
        if hm:
            plan.headings.setdefault(_norm_heading(hm.group(1)), target(place, node))
        node["children"] = _prepare_tree(node.get("children") or [], plan, keeps, places)
        kept.append((place, node))
    return [node for _, node in kept]


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
        tree = md_to_blocks(body, outline=readable_outline(fields, body))
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


class Plan:
    __slots__ = ("entry", "page_id", "title", "path", "alone", "labels", "body", "props", "existing",
                 "tree", "anchors", "headings", "ids", "links", "rows", "fields",
                 "notion_id", "file_title", "parent_ref", "source_ref", "outline", "source")

    def __init__(self, entry, dialect):
        self.entry = entry
        self.page_id = new_block_id()
        stem, ext = _split_ext(entry.path)
        stem = re.sub(r"_all$", "", stem) if ext == ".csv" else stem
        self.notion_id = dialect.page_id(stem)  # the file name's, unless the page says its own
        self.file_title = _clean_stem(stem)
        self.title = ""
        self.path = []      # the folder path below the destination (names)
        self.alone = []     # a sub-page's: the path it takes when its parent is left out
        self.labels = []    # label names
        self.body = ""      # until the page is written, what ``source`` reads
        self.props = {}
        self.existing = False
        self.tree = []      # while the page is written
        self.anchors = {}
        self.headings = {}
        self.ids = {}       # place in the tree → id, of the blocks links can target
        self.links = []     # (label, href) of the lines that are one link (_link_lines)
        self.rows = []      # a database's: its CSV's rows, the header first
        self.fields = {}    # a Notion row page's property lines (_properties)
        self.parent_ref = None  # a front-matter ``parent:``: (a wikilink?, the note it names)
        self.source_ref = None  # a front-matter ``source:``: the bundled PDF or its URL
        self.outline = False    # Gamma's readable export: every ``- `` is a block (``readable_outline``)
        self.source = None      # the ``PageSource`` that reads it


class MarkdownSource(PageSource):
    """A note."""

    def read(self, run, plan, raw):
        try:
            text = raw.decode("utf-8-sig")
        except UnicodeDecodeError:
            run.warn(plan.entry.path, "not UTF-8")
            return False
        fm, body = parse_frontmatter(text)
        # (a vault's or a Notion page's bullets are lists, whatever the note's shape)
        plan.outline = run.dialect.name != "vault" and not plan.notion_id and readable_outline(fm, body)
        title, body = run.dialect.take_title(plan, body, fm_text(fm, "title") or None)
        plan.title = (title or plan.file_title).strip()[:500] or "Untitled note"
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
            plan.source_ref = _wiki_source(fm_text(fm, "source"))
        if fm_text(fm, "parent"):  # (a wikilink, the note it names)
            plan.parent_ref = (fm_text(fm, "parent").strip().startswith("[["), _wiki_source(fm_text(fm, "parent")))
        # Obsidian's built-in properties (and their pre-1.9 singular
        # names): tags are labels, aliases ride along.
        tags = (t.lstrip("#").strip() for t in fm_list(fm, "tags") + fm_list(fm, "tag"))
        plan.labels = list(dict.fromkeys(t for t in tags if t))
        aliases = [a for a in fm_list(fm, "aliases") + fm_list(fm, "alias") if a]
        if aliases:
            plan.props["aliases"] = list(dict.fromkeys(aliases))
        plan.body = run.dialect.read_body(plan, body)
        return True

    def blocks(self, run, plan):
        return md_to_blocks(plan.body, outline=plan.outline, flat_headings=run.dialect.flat_headings(plan))

    def link_text(self, plan):
        return plan.body


class CsvSource(MarkdownSource):
    """A database: the dialect makes its table's Markdown of the CSV."""

    def read(self, run, plan, raw):
        return run.dialect.read_table(run, plan, raw)


_NOTE, _TABLE = MarkdownSource(), CsvSource()


class ZipRun:
    """One import of one zip, stage by stage (``run``). Each stage reads what
    the ones before it left on the run: the walk ``entries``; ``classify``
    ``dialect`` (gamma/markdown_zip_dialects.py), what is a note, a table or a
    bundled file, and ``order``; ``read_library`` the pages
    imported before; ``plan_files`` ``plans``, ``plan_of``, ``linked_anchors``
    and ``targets`` (zip path → page id); ``select`` ``chosen`` and
    ``excluded_ids``; ``build_links`` ``links``; ``find_parents`` ``parent_of``;
    ``file`` the folders and labels made."""

    def __init__(self, ws, zf, conn, folder, unpacked, *, actor, preview, selected, progress):
        self.ws, self.zf, self.conn, self.folder, self.unpacked = ws, zf, conn, folder, unpacked
        self.actor, self.preview, self.selected = actor, preview, selected
        self.progress = progress or jobs.no_progress
        self.under, self.under_path = destination(conn, folder)
        self.report = {"pages_created": 0, "pages_skipped": 0, "blocks_imported": 0,
                       "assets_stored": 0, "links_resolved": 0, "notion": False, "obsidian": False,
                       "pages": [], "warnings": [], "entries": [], "folder": folder}
        self.selection = None  # the note at hand: its warnings name it

    def run(self) -> dict:
        self.walk()
        self.classify()
        self.read_library()
        self.plan_files()
        self.dialect.fold(self)
        self.select()
        self.build_links()
        self.find_parents()
        self.dialect.titles(self)
        self.dialect.layout(self)
        self.close_parents()
        self.file()
        self.write()
        return self.report

    def warn(self, title, reason):
        if len(self.report["warnings"]) < MAX_WARNINGS:
            self.report["warnings"].append({"title": title, "reason": reason, "selection_id": self.selection})

    # --- what the zip holds ---------------------------------------------------

    def walk(self):
        walk = new_walk(self.unpacked)
        self.entries = []
        walk_zip(self.zf, "", self.entries, walk)
        check_ratio(walk)
        _strip_wrappers(self.entries)
        self.report["entries"] = archive_entries(self.zf)
        for path, reason in walk["left_out"]:
            self.warn(path, reason)

    def classify(self):
        self.dialect = dialect_of([e.path for e in self.entries], html_pages(self.entries, self.warn))
        self.report["obsidian"] = self.dialect.name == "vault"
        self.entries = [e for e in self.entries if not _skipped(e.path)]
        self.order = {e.path: i for i, e in enumerate(self.entries)}
        for e in self.entries:
            if _split_ext(e.path)[1] == ".canvas":
                self.warn(e.path, "Obsidian canvas files are not imported")

        self.notes = sorted((e for e in self.entries if self.dialect.is_page(e.path)),
                            key=lambda e: (e.path.count("/"), e.path.lower()))
        csvs = [e for e in self.entries if _split_ext(e.path)[1] == ".csv"]
        self.assets = {e.path: e for e in self.entries
                       if _is_asset_ext(_split_ext(e.path)[1]) and not self.dialect.is_page(e.path)}
        self.tables, self.loose_csvs = self.dialect.tables(csvs)
        if not self.notes and not self.tables:
            raise HTTPException(status_code=400, detail="no .md files in the zip")

    def read_library(self):
        """Already-imported pages: same bytes, or the same Notion page."""
        self.by_digest, self.by_notion, self.existing_pages = {}, {}, {}
        for pid, raw_props, title in self.conn.execute(
                "SELECT id, properties, content FROM unified_blocks WHERE parent_id = 'root'"):
            try:
                props = json.loads(raw_props or "{}")
            except (TypeError, ValueError):
                continue
            self.existing_pages[pid] = (title, props)
            if props.get("markdown_import"):
                self.by_digest.setdefault(props["markdown_import"], []).append(pid)
            if props.get("notion_id"):
                self.by_notion.setdefault(props["notion_id"], pid)

    # --- a plan per file ------------------------------------------------------

    def source_of(self, path):
        ext = _split_ext(path)[1]
        return _TABLE if ext == ".csv" else _NOTE if ext in _MD_EXTS else self.dialect.html_source

    def plan_files(self):
        self.plans, self.targets, self.plan_of = [], {}, {}
        self.taken = set()  # pages a Notion note of the zip found by their bytes
        self.linked_anchors = set()  # the ``#^id`` the notes' links name (``Plain``)
        planned = []
        for e in self.notes + self.tables:
            if e.size > MAX_MARKDOWN_BYTES:
                self.warn(e.path, "file exceeds 5 MB")
                continue
            try:
                raw = e.read()
            except READ_ERRORS:  # (a head that read tells nothing of the rest)
                self.warn(e.path, "this file could not be read")
                continue
            plan = Plan(e, self.dialect)
            digest = content_digest(raw)
            plan.props = {"original_filename": posixpath.basename(e.path), "markdown_import": digest}
            if plan.notion_id:
                plan.props["notion_id"] = plan.notion_id
                self.report["notion"] = True
            plan.path = _dir_path(e.path)
            plan.source = self.source_of(e.path)
            if not plan.source.read(self, plan, raw):
                continue
            self.dialect.scan(self, plan)
            existing = self.existing_page(plan, digest)
            if existing:
                plan.existing = True
                plan.page_id = existing
                plan.title, plan.props = self.existing_pages[existing]
                plan.props = dict(plan.props)
                plan.body = ""
            planned.append(plan)
        # Every body is read before the first is parsed: what a parse keeps
        # can hang on a link in a note read later.
        for plan in planned:
            if plan.body.strip() and (not self.preview or len(plan.body) >= 2 * MAX_BLOCKS):
                # The ids links may target (the tree itself is parsed again when
                # the page is written). A preview needs none: it parses only a
                # body long enough to hold too many blocks (a block takes a line
                # of its own, two characters at least).
                try:
                    self.blocks(plan)
                except TooManyBlocks as exc:
                    self.warn(plan.entry.path, f"has {exc}: not imported")
                    continue
            self.plans.append(plan)
            self.targets[plan.entry.path] = plan.page_id
            self.plan_of[plan.page_id] = plan

    def existing_page(self, plan, digest):
        """The page this file was imported as before, or None. A Notion page
        is the page of its id: two pages of one template (``# Untitled`` and
        a status line) have the same bytes. Its bytes find only the page made
        from this file alone (POST /import/markdown: no Notion id, the file's
        name), once."""
        existing = self.by_notion.get(plan.notion_id) if plan.notion_id else None
        for pid in self.by_digest.get(digest, ()) if existing is None else ():
            known = self.existing_pages[pid][1]
            if not plan.notion_id:
                existing = pid
                break
            if (pid not in self.taken and not known.get("notion_id")
                    and known.get("original_filename") == posixpath.basename(plan.entry.path)):
                existing = pid
                self.taken.add(pid)
                break
        return existing

    def parse(self, plan) -> list:
        """The one place a body is parsed: the ids in ``plan.ids`` go by place
        in the tree, so every parse of a body has to give the same tree or
        the links made from the first miss their blocks."""
        return plan.source.blocks(self, plan)

    def blocks(self, plan) -> list:
        """The plan's tree, its ids and anchors taken."""
        return _prepare_tree(self.parse(plan), plan, lambda marker: self.dialect.keeps_anchor(self, plan, marker))

    def too_many(self, plan):
        """The ``TooManyBlocks`` of a body that grew since its first parse
        (a fold), or None: the write stage's parse must not meet one."""
        try:
            if len(plan.body) >= 2 * MAX_BLOCKS:
                self.parse(plan)
        except TooManyBlocks as exc:
            return exc
        return None

    def select(self):
        validate_selection(self.selected, (p.entry.path for p in self.plans))
        excluded = {p.entry.path for p in self.plans
                    if self.selected is not None and p.entry.path not in self.selected and not p.existing}
        # Unselected notes must not become dangling mentions to newly generated IDs.
        self.excluded_ids = {p.page_id for p in self.plans if p.entry.path in excluded}
        self.chosen = [plan for plan in self.plans if self.selected is None or plan.entry.path in self.selected]

    # --- links ----------------------------------------------------------------

    def build_links(self):
        self.links = Links(self)
        self.dialect.build_links(self)

    def store_asset(self, path):
        """Upload a bundled file once; None when it can't be (type, quota).
        A file is read only to be stored, streamed into storage (a vault's
        video can be gigabytes); a preview reads no more than a PDF's
        header."""
        if path in self.stored:
            return self.stored[path]
        url = None
        entry = self.assets.get(path)
        if entry is not None:
            ext = _split_ext(path)[1]
            try:
                if ext == ".pdf" and not is_pdf(entry.head(4)):
                    self.warn(path, "not a valid PDF")
                elif self.preview:
                    url = f"/api/uploads/preview{ext}"
                    self.report["assets_stored"] += 1
                elif shutil.disk_usage(config.DATA_DIR).free - entry.size < MIN_FREE_BYTES:
                    self.warn(path, "the server's disk has no room for this file")
                else:
                    with entry.open() as src:
                        filename, _ = store_file_stream(self.ws, src, ext)
                    url = f"/api/uploads/{filename}"
                    self.report["assets_stored"] += 1
            except HTTPException as exc:
                self.warn(path, str(exc.detail))
            except ValueError as exc:  # not what its extension says (storage.content_refusal)
                self.warn(path, str(exc))
            except READ_ERRORS:
                self.warn(path, "this file could not be read")
        self.stored[path] = url
        return url

    def rewrite(self, body, base_dir, plan):
        """A body's links (``Links.rewrite_links``), then its dialect's."""
        body = self.links.rewrite_links(body, base_dir, plan)
        return self.dialect.rewrite(self, body, base_dir) if plan.source.relations else body

    # --- the tree -------------------------------------------------------------

    def parent_note(self, note, wiki, ref):
        """The note of the import a ``parent:`` value of the note ``note``
        names, or None: the note beside the note's directory when its name
        is the value's (the layout the exports write: ``A.md`` beside
        ``A/B.md``), else a wikilink's exact vault path (Obsidian reads
        ``[[a/b]]`` from the vault's top), else as a link (``resolve``) —
        never the note itself. Read off every note, chosen or not."""
        every, lower = self.links.every, self.links.every_lookup[0]
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
        path = self.links.resolve(base, ref, self.links.every_lookup)
        return path if path != note and path in every else None

    def find_parents(self):
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
        self.dialect.pair_directories(self)
        self.library_parents = page_parents(self.conn)
        self.parent_of = {}
        for plan in self.plans:
            if plan.existing:
                continue
            wiki, ref = plan.parent_ref or (False, "")
            note = self.parent_note(plan.entry.path, wiki, ref) if ref else None
            parent = self.links.every[note] if note else self.dialect.paired_page(self, plan.entry.path)
            if parent and parent != plan.page_id:
                self.parent_of[plan.page_id] = parent

    def above(self, page_id):
        """The parent of a page of a walk up: the import's, else the library's."""
        return self.parent_of.get(page_id) or self.library_parents.get(page_id)

    def close_parents(self):
        for page_id in list(self.parent_of):
            walk = [page_id]
            while (up := self.above(walk[-1])) is not None and up not in walk and len(walk) <= MAX_PAGE_DEPTH:
                walk.append(up)
            if up in walk:
                for page in walk[walk.index(up):]:
                    self.parent_of.pop(page, None)
            elif len(walk) > MAX_PAGE_DEPTH:
                self.parent_of.pop(page_id, None)
        self.parent_of = {page_id: up for page_id, up in self.parent_of.items() if up not in self.excluded_ids}
        for plan in self.chosen:
            if plan.page_id in self.parent_of:
                plan.props[PARENT_PAGE] = self.parent_of[plan.page_id]
                plan.alone, plan.path = plan.path, []

    def depth(self, page_id):
        """How many pages of the import a page sits below."""
        n = 0
        while page_id in self.parent_of:
            page_id, n = self.parent_of[page_id], n + 1
        return n

    # --- filing and writing ---------------------------------------------------

    def file(self):
        # Each parent is written before the pages inside it, so a stopped or
        # failed import leaves no page inside one it never wrote (a database's
        # rows come after its table, though its CSV is read after every note);
        # within a depth the notes keep their order.
        self.chosen.sort(key=lambda plan: self.depth(plan.page_id))
        self.paths = folder_paths(self.conn)
        self.folder_ids, self.label_ids = {}, {}
        self.stored = {}
        self.staged = []  # pages whose files are stored, written PAGES_PER_COMMIT at a time
        new = [plan for plan in self.chosen if not plan.existing]
        if new and not self.preview:
            self.folder_ids, self.label_ids = ensure_filing(self.ws, self.conn, paths=[p.path for p in new],
                                                            labels=[name for p in new for name in p.labels],
                                                            under=self.under, actor=self.actor)

    def shown(self, plan):
        """The paths (names from the top) the page is, or would be, filed
        in: a sub-page's are its top's, up through the new pages (a parent
        is a page of the import) to a new library page or a page already
        in the library, whose top is the library's."""
        while not plan.existing and PARENT_PAGE in plan.props:
            plan = self.plan_of[plan.props[PARENT_PAGE]]
        if plan.existing:
            top = self.existing_pages.get(top_of(self.library_parents, plan.page_id), ("", {}))[1]
            return [self.paths[f] for f in filing(top, FOLDERS) if f in self.paths]
        return [self.under_path + plan.path] if self.under_path or plan.path else []

    def alone(self, plan):
        """A new sub-page's paths when the page it is inside is left out of
        the selection (it is then filed by its directory, as a library page
        is): what the review shows for a page ticked without its parent."""
        if plan.existing or PARENT_PAGE not in plan.props:
            return {}
        return {"folders_alone": [self.under_path + plan.alone] if self.under_path or plan.alone else []}

    def inside(self, plan):
        """The id of the page the page is, or would be, inside; "" for a
        library page."""
        return self.library_parents.get(plan.page_id, "") if plan.existing else plan.props.get(PARENT_PAGE, "")

    def flush(self):
        """Write the staged pages in one short transaction."""
        if not self.staged:
            return
        try:
            for plan in self.staged:
                self.report["blocks_imported"] += insert_page_tree(self.conn, plan.page_id, plan.title, plan.props,
                                                                   plan.tree, self.actor)
            self.conn.commit()
        except BaseException:
            self.conn.rollback()
            raise
        for plan in self.staged:
            plan.tree = []
        self.staged.clear()

    def write(self):
        report = self.report
        for n, plan in enumerate(self.chosen):
            try:
                self.progress(done=n, total=len(self.chosen), unit="items", item=plan.title)
            except BaseException:  # stopped: the staged notes' files are stored, so they go in too
                self.flush()
                raise
            self.selection = plan.entry.path
            if plan.existing:
                self.skip_page(plan)
            else:
                self.write_page(plan)
        self.flush()
        self.selection = None
        for path in self.loose_csvs:  # (``stored`` holds what a new note linked; a skipped note's are not read)
            if path not in self.stored:
                self.warn(path, "no new note links this CSV: not imported")
        log.info(f"[markdown-zip] {report['pages_created']} pages, {report['pages_skipped']} skipped, "
                 f"{report['assets_stored']} files, {report['links_resolved']} links"
                 f"{' (Notion)' if report['notion'] else ''}{' (Obsidian vault)' if report['obsidian'] else ''}")

    def skip_page(self, plan):
        self.report["pages_skipped"] += 1
        self.report["pages"].append({"id": plan.page_id, "title": plan.title,
                                     "folders": self.shown(plan), "parent": self.inside(plan),
                                     "selection_ids": [plan.entry.path],
                                     "source_path": plan.entry.source_path, "source_paths": [plan.entry.outer],
                                     "kind": "pdf" if plan.props.get("doc_id") else "page", "created": False,
                                     "action": "skip", "warnings": [], "missing": False})

    def write_page(self, plan):
        warning_start = len(self.report["warnings"])
        base_dir = posixpath.dirname(plan.entry.path)
        source = plan.source_ref
        if source:
            path = self.links.resolve(base_dir, source)
            if path and _split_ext(path)[1] == ".pdf":
                url = self.store_asset(path)
                if url:
                    plan.props["doc_id"] = url.rsplit("/", 1)[1][:-4]
            elif _SCHEME_RE.match(source) and source.lower().startswith(("http://", "https://")):
                plan.props["source_url"] = source
            else:
                self.warn(plan.title, f"Missing PDF attachment: {source}")
        if not self.preview:
            filed = _filed_in(self.under, self.folder_ids, plan.path)
            if filed and PARENT_PAGE not in plan.props:
                plan.props["folders"] = [filed]
            if plan.labels:
                plan.props["labels"] = list(dict.fromkeys(self.label_ids[name] for name in plan.labels))
        # The page's bundled files are stored while its links are rewritten —
        # before its rows, outside any transaction. A preview, which writes
        # no tree, reads the links off the body.
        if self.preview:
            self.rewrite(plan.source.link_text(plan), base_dir, plan)
        elif plan.body.strip():
            plan.tree = self.blocks(plan)
            for node in _walk(plan.tree):
                if plan.source.holds_links(node["content"]):
                    node["content"] = self.rewrite(node["content"], base_dir, plan)
        plan.body = ""
        if not self.preview:
            self.staged.append(plan)
            if len(self.staged) >= PAGES_PER_COMMIT:
                self.flush()
        self.report["pages_created"] += 1
        warnings = self.report["warnings"][warning_start:]
        self.report["pages"].append({"id": plan.page_id, "title": plan.title,
                                     "folders": self.shown(plan), "parent": self.inside(plan), **self.alone(plan),
                                     "selection_ids": [plan.entry.path],
                                     "source_path": plan.entry.source_path, "source_paths": [plan.entry.outer],
                                     "kind": "pdf" if plan.props.get("doc_id") else "page", "created": True,
                                     "action": "create", "warnings": warnings,
                                     "missing": any("Missing" in w["reason"] for w in warnings)})


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
        return ZipRun(ws, zf, conn, folder, unpacked, actor=actor, preview=preview, selected=selected,
                      progress=progress).run()
