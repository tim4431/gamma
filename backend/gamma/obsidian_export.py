"""Render pages as an Obsidian vault: one ``<Title>.md`` per page in a
directory tree that mirrors the folders, attachments (images and the
PDFs) in ``attachments/``, links in Obsidian's own syntax, and a minimal
``.obsidian/app.json`` so the folder opens as a vault and re-imports as one.

The shape follows what Obsidian expects from another app (see
docs/research/obsidian.md): the filename is the title (no H1), the outline is
flattened Logseq-importer style — top-level headings and paragraphs, deeper
blocks as nested lists — and metadata is front matter (``tags`` from the
labels, ``aliases``, ``source``, ``doi``, ``authors``, ``year``).

Links resolve against the export set: a ``[[id]]`` mention of a page in the
export becomes ``[[Title]]`` (``[[dir/Title]]`` when two exported pages share
a name), a mention of a block ``[[Title#^id]]`` and a ``![[embed]]``
``![[Title#^id]]`` — the target block gets `` ^id`` written after it (only
blocks that are actually linked carry an anchor). Highlights are
``[!quote]`` callouts whose title links the bundled PDF's page
(``[[Paper.pdf#page=3|p. 3]]``); the PDF is also the quoted
``source: "[[Paper.pdf]]"`` property. A text box on a PDF page is a note
followed by that page link, and a picture is its image line, followed by
the link when it is on a PDF page. A recording is its length, an
``![[…]]`` embed per audio file in ``attachments/`` and its title. Targets outside the
export degrade the way the readable Markdown export does (text, or the
materialized block).

A sub-page's file sits in a directory named for its parent, beside the
parent's file (``Parent.md``, ``Parent/Child.md``: Notion's and Obsidian's
layout), and its front matter names the parent as ``parent: "[[Parent]]"``;
a sub-page whose parent is not in the export goes where its top's folders
put it (``Filing``).

Upload references are rewritten by the caller with ``collect_and_rewrite``
(prefix ``attachments/``), as for the readable export.
"""

import json
import re

from . import bibtex as bibtex_mod
from .blocks_store import (FOLDERS, LABELS, PATH_SEP, filing, folder_paths, folder_subtree_ids, label_names,
                           load_props, page_attachment, page_parents, top_of)
from .highlights import is_highlight, is_link, page_of
from .markdown_export import _BLOCK_REF_RE, _link_label, block_text, resolve_block_links
from .markdown_import import _FENCE_LINE_RE, EMPTY_ITEM_RE, _fence_marks, shuts_code
from .note_markup import obsidian_image_sizes
from .notebook import is_sheet
from .picture import is_picture, markdown_of, picture_page
from .recording import heading as recording_heading, is_recording, segments_of
from .text_box import box_page

# What Obsidian refuses in a note name on any OS (link syntax) plus the
# Windows set, and control characters.
_INVALID_NAME = re.compile(r'[<>:"/\\|?*#^\[\]\x00-\x1f]')
_HEADING_RE = re.compile(r"^#{1,6}\s+\S")
# Blocks whose anchor must sit on its own line after them (Obsidian's rule
# for lists, quotes, callouts, tables and fences).
_COMPOUND_RE = re.compile(r"^(```|~~~|>|\||\$\$|[-*+] |\d+[.)] )")
# A block that is a list item as it stands (the importer's list marker).
_LIST_BLOCK_RE = re.compile(r"[-*+] |\d{1,4}[.)] ")
_MARKER_RE = re.compile(r"[^A-Za-z0-9-]")
_RESERVED_NAME = re.compile(r"(?i)(con|prn|aux|nul|com[1-9]|lpt[1-9])")
# How a note's first line opens front matter (``markdown_import._FRONTMATTER_RE``).
_OPENS_FRONT_MATTER_RE = re.compile(r"---[ \t]*\n")

APP_JSON = json.dumps({"attachmentFolderPath": "attachments"}, indent=2) + "\n"


def _code_closer(line, closer):
    """What closes the fence or ``$$`` block open at a block's left edge after
    ``line``, as the importer reads one: ``closer`` is that mark before it,
    None outside one."""
    if closer == "$$":
        return None if "$$" in line else closer
    if closer:
        return None if shuts_code(line, closer, 0) else closer
    if _FENCE_LINE_RE.match(line) and not line[0].isspace():
        return _fence_marks(line)
    return "$$" if line.startswith("$$") and "$$" not in line[2:] else None


def vault_name(title: str) -> str:
    """A page title as an Obsidian-safe file/link name: valid on every OS,
    which also means no Windows device name (``CON``, ``NUL``, ``COM1``…
    cannot be created there, whatever the extension)."""
    t = _INVALID_NAME.sub(" ", title or "")
    t = re.sub(r"\s+", " ", t).strip().lstrip(".").rstrip(". ")
    t = t[:80].rstrip(". ") or "Untitled"
    return t + "_" if _RESERVED_NAME.fullmatch(t) else t


class Filing:
    """The names an export writes for its pages' folders and labels, read
    from the trees once. ``scope`` is the exported folder's id (None: a page
    exported on its own): a page's folder paths are then the names below it,
    and its folders outside it are left out; ``title`` is the exported
    folder's path as people read it ("" for none). A sub-page is filed
    where its top is: its own ``folders`` are passed by, and ``parents``
    (``page_parents``: sub-page → its effective parent) is what puts its
    file inside its parent's directory (``parent_dir``)."""

    def __init__(self, conn, scope):
        paths = folder_paths(conn)
        self.title = ""
        if scope:
            top = paths.get(scope, [])
            self.title = PATH_SEP.join(top)
            inside = folder_subtree_ids(conn, scope)
            paths = {f: path[len(top):] for f, path in paths.items() if f in inside}
        self.paths = paths
        self.labels = label_names(conn)
        self.parents = page_parents(conn)
        tops = sorted({self.top(page) for page in self.parents})
        self.top_folders = {page: filing(load_props(props), FOLDERS) for page, props in conn.execute(
            "SELECT id, properties FROM unified_blocks WHERE id IN (SELECT value FROM json_each(?))",
            (json.dumps(tops),))}

    def top(self, page_id) -> str:
        """The page's first ancestor, itself for a library page."""
        return top_of(self.parents, page_id)

    def folders(self, page_id, props) -> list[list[str]]:
        """The page's folder paths (names) below the exported folder — that
        folder itself, the export's top, is none —, in the page's order; a
        sub-page's are its top's."""
        ids = self.top_folders.get(self.top(page_id), []) if page_id in self.parents else filing(props, FOLDERS)
        return [self.paths[f] for f in ids if self.paths.get(f)]

    def folder(self, page_id, props) -> list[str]:
        """The path a page's file goes under: its first folder below the
        exported folder ([]: the export's top)."""
        return next(iter(self.folders(page_id, props)), [])

    def tags(self, props) -> list[str]:
        """The names of the page's labels, in its order."""
        return [self.labels[i] for i in filing(props, LABELS) if i in self.labels]

    def parent_dir(self, page_id, stems) -> str:
        """The directory a sub-page's file goes in when its parent has a
        file in the export (``stems``: page id → that file's path without
        its extension, placed parents before children): one named for the
        parent, beside the parent's file (``"a/Parent/"``); "" for any
        other page."""
        parent = self.parents.get(page_id)
        return f"{stems[parent]}/" if parent in stems else ""

    def directory(self, page_id, props, stems) -> str:
        """The directory a page's file goes in where the folders are
        directories (the vault, the annotated PDFs, the folder sync): inside
        its parent's (``parent_dir``), else under its first folder below
        the exported folder (``page_dir``)."""
        return self.parent_dir(page_id, stems) or page_dir(self.folder(page_id, props))


def unique_name(used: set, directory: str, stem: str, ext: str) -> str:
    """``<directory><stem><ext>`` made unique among ``used`` — a `` 2``,
    `` 3``… after the stem while the path is taken, compared ignoring case
    since the file may land on a case-insensitive disk — and added to it.
    The one naming rule of the vault export, the annotated-PDF folder export
    and the folder sync (gamma/folder_sync.py)."""
    name, n = stem, 1
    while f"{directory}{name}{ext}".lower() in used:
        n += 1
        name = f"{stem} {n}"
    used.add(f"{directory}{name}{ext}".lower())
    return f"{directory}{name}{ext}"


def anchor_marker(block_id: str) -> str:
    """A block id as an Obsidian block identifier (letters, digits, dashes)."""
    return _MARKER_RE.sub("-", block_id or "")


def _yaml_text(value: str) -> str:
    """A scalar for the front matter, quoted when YAML would misread it."""
    v = str(value)
    if re.search(r'[:#\[\]{}&*!|>\'"%@`,]|^\s|\s$', v) or v.lower() in ("true", "false", "null", "yes", "no") \
            or re.match(r"^[\d.+-]", v) and not re.match(r"^\d{4}$", v):
        return json.dumps(v, ensure_ascii=False)
    return v


def _yaml_list(key: str, items) -> list:
    return [f"{key}:"] + [f"  - {_yaml_text(i)}" for i in items]


class VaultContext:
    """Everything a page render needs from the export set: filenames, link
    texts, bundled PDF names, the anchors of linked blocks, and a block
    resolver (``id → {content, page_title, page_id} | None``)."""

    def __init__(self, resolve_ref, include_pdf=True):
        self.resolve_ref = resolve_ref
        self.include_pdf = include_pdf
        self.page_file = {}     # page id → arcname (dir/Title.md)
        self.link_text = {}     # page id → [[link text]] (Title or dir/Title)
        self.pdf_name = {}      # page id → (the PDF's arcname, doc_id)
        self.anchors = {}       # block id → ^marker (blocks linked from the export)
        self.parents = {}       # sub-page id → its parent, for the sub-pages filed inside their parent's directory
        self.id_key = None      # a front-matter key to write the page's id under (the folder sync's)

    # --- naming (built once, before any page renders) ------------------------

    def name_pages(self, fil: Filing, pages):
        """``pages``: (page id, title, properties) in export order, parents
        before children; each file goes in the directory ``fil`` gives it
        (``Filing.directory``), named by ``name_files``."""
        stems = name_files(fil, pages)
        self.place_pages({pid: f"{stem}.md" for pid, stem in stems.items()})
        self.parents = {pid: fil.parents[pid] for pid in stems if fil.parents.get(pid) in stems}

    def place_pages(self, files: dict):
        """The pages' files as decided elsewhere (``{page id: dir/Title.md}``,
        the folder sync's layout), and the text each is linked by: Obsidian
        resolves a bare [[Name]] anywhere in the vault, so when two pages
        share a name the folder path disambiguates."""
        self.page_file = dict(files)
        self.link_text.update(link_texts(self.page_file))

    def name_pdf(self, page_id, title, doc_id):
        """The bundled PDF's arcname for a page (shared by pages of one document)."""
        for pid, arc in self.pdf_name.items():
            if arc[1] == doc_id:
                self.pdf_name[page_id] = arc
                return arc[0]
        taken = {a[0].lower() for a in self.pdf_name.values()}
        return self.place_pdf(page_id, unique_name(taken, "attachments/", vault_name(title), ".pdf"), doc_id)

    def place_pdf(self, page_id, arcname, doc_id):
        """The page's PDF is the file ``arcname`` (the folder sync puts it
        beside the note); its leaf is what the note links."""
        self.pdf_name[page_id] = (arcname, doc_id)
        return arcname

    def pdf_leaf(self, page_id):
        arc = self.pdf_name.get(page_id)
        return arc[0].rsplit("/", 1)[-1] if arc else None

    def note_anchor(self, block_id):
        """Register a linked block; returns its marker."""
        marker = self.anchors.get(block_id)
        if marker is None:
            marker = self.anchors[block_id] = anchor_marker(block_id)
        return marker


def name_files(fil: Filing, pages, directory=None) -> dict:
    """``{page id: stem}``, each page's file path without its extension,
    for ``pages`` ((page id, title, properties), parents before children,
    in the order given): unique ignoring case (``unique_name``) in the
    directory ``directory`` gives it (called as ``Filing.directory``, which
    is the default). The library pages are named first, so a sub-page,
    whose directory may be a folder's of the same name, never takes a
    library page's name; then the sub-pages, each after its parent."""
    place = directory or fil.directory
    used, stems = set(), {}
    for library in (True, False):
        for pid, title, props in pages:
            if (pid not in fil.parents) == library:
                stems[pid] = unique_name(used, place(pid, props, stems), vault_name(title), "")
    return {pid: stems[pid] for pid, _, _ in pages}


def link_texts(files: dict) -> dict:
    """``{page id: the text a [[link]] to it is written with}`` for the
    files ``files`` (``{page id: dir/Title.md}``): Obsidian resolves a bare
    [[Name]] anywhere in the vault, so the title alone while no other file
    has that name (ignoring case), else the path without the extension."""
    counts = {}
    for arc in files.values():
        leaf = arc.rsplit("/", 1)[-1].lower()
        counts[leaf] = counts.get(leaf, 0) + 1
    return {pid: arc[:-3].rsplit("/", 1)[-1] if counts[arc.rsplit("/", 1)[-1].lower()] == 1 else arc[:-3]
            for pid, arc in files.items()}


def page_dir(folder) -> str:
    """The directory a page's file goes in (``"a/b/"``, or ``""``) for its
    folder path ``folder`` (names; the export's choice — the first folder
    below the exported folder), each name made a valid file name. Shared
    with the annotated-PDF folder export."""
    return "".join(f"{vault_name(name)}/" for name in folder)


# --- links -------------------------------------------------------------------

def resolve_links(md, ctx: VaultContext, page_id):
    """``[[id]]`` / ``![[id]]`` → Obsidian links into the export set; targets
    outside it fall back to the readable export's rendering."""
    def repl(m):
        is_embed, block_id = m.group(1), m.group(2)
        ref = ctx.resolve_ref(block_id) if ctx.resolve_ref else None
        target_page = ref.get("page_id") if ref else None
        if not ref or target_page not in ctx.page_file:
            return resolve_block_links(m.group(0), ctx.resolve_ref, None, page_id)
        text = "" if target_page == page_id else ctx.link_text[target_page]
        if block_id == target_page:                 # the page itself
            return f"{is_embed}[[{text or ctx.link_text[target_page]}]]"
        return f"{is_embed}[[{text}#^{ctx.note_anchor(block_id)}]]"

    return _BLOCK_REF_RE.sub(repl, md)


def referenced_blocks(texts, ctx: VaultContext):
    """Register anchors for every ``[[id]]`` / ``![[id]]`` in ``texts`` whose
    target block lives inside an exported page (a page's own root needs
    none — it is linked by title)."""
    for text in texts:
        for m in _BLOCK_REF_RE.finditer(text or ""):
            block_id = m.group(2)
            ref = ctx.resolve_ref(block_id)
            if ref and ref.get("page_id") in ctx.page_file and block_id != ref["page_id"]:
                ctx.note_anchor(block_id)


# --- rendering ---------------------------------------------------------------

def render_vault_page(page, ctx: VaultContext, tags=(), highlights=True, notes=True):
    """One page as a vault note; ``tags`` are its labels' names."""
    props = page.get("properties") or {}
    title = (page.get("content") or "").strip() or "Untitled"
    page_id = page["id"]

    fm = []
    if ctx.id_key:
        fm.append(f"{ctx.id_key}: {page_id}")
    if vault_name(title) != title:
        fm.append(f"title: {_yaml_text(title)}")
    if parent := ctx.parents.get(page_id):
        fm.append(f'parent: "[[{ctx.link_text[parent]}]]"')
    if tags:
        fm += _yaml_list("tags", dict.fromkeys(tags))
    aliases = props.get("aliases")
    if isinstance(aliases, list) and aliases:
        fm += _yaml_list("aliases", [str(a) for a in aliases if str(a).strip()])
    pdf_leaf = ctx.pdf_leaf(page_id)
    if pdf_leaf:
        fm.append(f'source: "[[{pdf_leaf}]]"')
    elif attachment := page_attachment(props):
        fm.append(f"source: {_yaml_text(attachment['url'])}")
    meta = props.get("meta")
    if isinstance(meta, dict):
        if meta.get("doi"):
            fm.append(f"doi: {_yaml_text(meta['doi'])}")
        authors = meta.get("authors")
        if isinstance(authors, list) and authors:
            fm += _yaml_list("authors", [str(a) for a in authors])
        elif authors:
            fm += _yaml_list("authors", [a.strip() for a in str(authors).split(",") if a.strip()])
        if meta.get("year"):
            fm.append(f"year: {meta['year']}")

    lines = ["---", *fm, "---", ""] if fm else []
    entry = bibtex_mod.page_entry(props)
    if entry:
        lines += ["```bibtex", entry, "```", ""]

    r = _Renderer(ctx, page_id, pdf_leaf, highlights, notes)
    for child in page["children"]:
        r.top(child, lines)
    text = "\n".join(lines)
    text = re.sub(r"\n{3,}", "\n\n", text).strip("\n")
    if not fm and _OPENS_FRONT_MATTER_RE.match(text):    # a divider, but it would open front matter
        text = "***" + text[3:]
    return text + "\n"


class _Renderer:
    def __init__(self, ctx, page_id, pdf_leaf, highlights, notes):
        self.ctx, self.page_id, self.pdf_leaf = ctx, page_id, pdf_leaf
        self.highlights, self.notes = highlights, notes

    def _content(self, node):
        content = obsidian_image_sizes(block_text(node))
        content = resolve_links(content, self.ctx, self.page_id)
        return content if self.notes else ""

    def _picture(self, node, props) -> str:
        """A picture block's image line (``picture.markdown_of``), its file
        bundled by the caller's rewrite as an image's is; "" for any other
        block, and with the notes switch off, which takes a picture out with
        the notes as it takes a text box."""
        return markdown_of(node.get("content"), props["picture"]) if self.notes and is_picture(props) else ""

    def _props(self, node):
        props = node.get("properties") or {}
        if not self.highlights and (is_highlight(props) or is_link(props) or is_recording(props)):
            return {}
        return props

    def _recording(self, props, content) -> list[str]:
        """A recording → its lines: the length, an embed per audio file
        (``/api/uploads/`` names, which the caller's rewrite bundles as it
        bundles images), the title; [] for any other block."""
        if not is_recording(props):
            return []
        return [f"**{recording_heading(props)}**", *(f"![[{s['url']}]]" for s in segments_of(props)),
                *(content.split("\n") if content else [])]

    def _page_link(self, page_no):
        if page_no is None:
            return ""
        return f"[[{self.pdf_leaf}#page={page_no}|p. {page_no}]]" if self.pdf_leaf else f"p. {page_no}"

    def _box_link(self, props, on_sheet):
        """The page link of a text box or a picture on a PDF page; "" for
        any other block (one under a sheet, ``on_sheet``, included)."""
        return self._page_link(box_page(props, on_sheet) or picture_page(props, on_sheet))

    def _region(self, props, content):
        """A PDF link region → a link line, or None when it isn't one."""
        if not is_link(props):
            return None
        target = props.get("link_page_id")
        label = content or (props.get("quote") or "").strip()
        if target and target in self.ctx.page_file:
            text = self.ctx.link_text[target]
            return f"[[{text}|{label}]]" if label and label != text else f"[[{text}]]"
        if not label and self.ctx.resolve_ref and target:
            ref = self.ctx.resolve_ref(target)
            label = _link_label((ref or {}).get("content"), "")
        href = props.get("link_url") or ""
        return f"[{label or href}]({href})" if href else (label or None)

    def _marker(self, node):
        return self.ctx.anchors.get(node["id"])

    # --- document style (top level, and under a top-level heading) ---------
    # ``on_sheet``, here and in the list style: a sheet of paper is among
    # the block's ancestors, so a text box under it is on that sheet.

    def top(self, node, lines, on_sheet=False):
        props = self._props(node)
        under_sheet = on_sheet or is_sheet(props)
        content = self._content(node)
        marker = self._marker(node)
        region = self._region(props, content)
        quote = (props.get("quote") or "").strip() if is_highlight(props) else ""

        if region:
            lines += [region + (f" ^{marker}" if marker else ""), ""]
            self._children_as_list(node, lines, under_sheet)
        elif recording := self._recording(props, content):
            self._paragraph("\n".join(recording), marker, lines)
            self._children_as_list(node, lines, under_sheet)
        elif quote:
            title = self._page_link(page_of(props))
            lines.append(f"> [!quote] {title}".rstrip())
            lines += [f"> {q}" for q in quote.split("\n")]
            if marker:
                lines += ["", f"^{marker}"]
            lines.append("")
            if content:
                lines += [content, ""]
            self._children_as_list(node, lines, under_sheet)
        elif picture := self._picture(node, props):
            self._paragraph(picture, marker, lines)
            if link := self._box_link(props, on_sheet):
                lines += [link, ""]
            self._children_as_list(node, lines, under_sheet)
        elif content and (link := self._box_link(props, on_sheet)):
            # The link is a paragraph of its own: a line straight after a
            # list, a quote or a table would run into it.
            self._paragraph(content, marker, lines)
            lines += [link, ""]
            self._children_as_list(node, lines, under_sheet)
        elif content and _HEADING_RE.match(content):
            self._paragraph(content, marker, lines)
            for child in node["children"]:
                self.top(child, lines, under_sheet)
        elif content and node["children"]:
            # A block with children keeps its subtree as a nested list (a
            # paragraph can't own children in Markdown).
            self.item(node, 0, lines, on_sheet)
            lines.append("")
        elif content:
            self._paragraph(content, marker, lines)
        else:
            for child in node["children"]:
                self.top(child, lines, under_sheet)

    def _paragraph(self, content, marker, lines):
        # (text after a fence's closing line would leave the fence open)
        if marker and (_COMPOUND_RE.match(content) or _FENCE_LINE_RE.match(content.rpartition("\n")[2])):
            lines += [content, "", f"^{marker}", ""]
        elif marker:
            lines += [f"{content} ^{marker}", ""]
        else:
            lines += [content, ""]

    def _children_as_list(self, node, lines, on_sheet):
        if not node["children"]:
            return
        for child in node["children"]:
            self.item(child, 0, lines, on_sheet)
        lines.append("")

    # --- list style (everything below the top level) -------------------------

    def item(self, node, indent, lines, on_sheet=False):
        props = self._props(node)
        under_sheet = on_sheet or is_sheet(props)
        content = self._content(node)
        marker = self._marker(node)
        region = self._region(props, content)
        quote = (props.get("quote") or "").strip() if is_highlight(props) else ""
        pad = " " * indent
        emitted = True
        width = 2  # the text column under the marker

        if region:
            lines.append(f"{pad}- {region}" + (f" ^{marker}" if marker else ""))
        elif recording := self._recording(props, content):
            lines.append(f"{pad}- {recording[0]}" + (f" ^{marker}" if marker else ""))
            lines += [f"{pad}  {r}" for r in recording[1:]]
        elif quote:
            qlines = quote.split("\n")
            lines.append(f"{pad}- > {qlines[0]}" + (f" ^{marker}" if marker else ""))
            lines += [f"{pad}  > {q}" for q in qlines[1:]]
            link = self._page_link(page_of(props))
            if link:
                lines.append(f"{pad}  {link}")
            lines += [f"{pad}  {c}" for c in content.split("\n")] if content else []
        elif picture := self._picture(node, props):
            lines.append(f"{pad}- {picture}" + (f" ^{marker}" if marker else ""))
            if link := self._box_link(props, on_sheet):
                lines.append(f"{pad}  {link}")
        elif content:
            # A list block is its own item, with its one marker. Its next lines
            # are laid out already: only text at its left edge moves under
            # the marker, where it stays in the item, and a fence or $$ block
            # at that edge moves whole, whatever its lines look like.
            own = _LIST_BLOCK_RE.match(content)
            width = len(own.group()) if own else width
            under = " " * width
            clines = content.split("\n")
            # (the anchor of a block that is only a marker goes below it: the
            # import would read it as the item's text, the marker's space lost)
            alone = marker and EMPTY_ITEM_RE.fullmatch(content)
            lines.append(f"{pad}{'' if own else '- '}{clines[0]}" + (f" ^{marker}" if marker and not alone else ""))
            if alone:
                lines.append(f"{pad}{under}^{marker}")
            closer = None
            for c in clines[1:]:
                laid_out = own and not closer and (not c or c[0].isspace() or _LIST_BLOCK_RE.match(c))
                lines.append(f"{pad}{c}" if laid_out else f"{pad}{under}{c}")
                closer = _code_closer(c, closer)
            link = self._box_link(props, on_sheet)
            if link:
                lines.append(f"{pad}{under}{link}")
        else:
            emitted = False

        child_indent = indent + width if emitted else indent
        for child in node["children"]:
            self.item(child, child_indent, lines, under_sheet)
