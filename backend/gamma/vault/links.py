"""How a synced vault's links and highlights are written in its files and
held in Gamma (docs/research/vaults.md, "Links"): one block's text at a time,
between the file's space, where the lens (gamma/md_lens.py) works, and
Gamma's.

Gamma holds a link to a page or a block as ``[[id]]`` (``![[id]]`` embeds a
block) and a picture or a file as its upload's address; the file says
``[[Title]]``, ``[[dir/Title|alias]]``, ``[text](<a b.md>)``,
``![[fig.png|300]]``. In a work folder every file is a page, so a link to a
file that has one is a mention of that page (``LinkContext.add_page_file``),
written back by its path, except a picture's embed, which keeps its upload's
address to draw inline, and a note's link to its own PDF, which stays the
file's link beside the page it is on. ``to_gamma`` reads a block's file
text into Gamma's, with a memo of how each link was written, keyed by what
it resolved to (a page or block id, or a vault path: an upload's address
holds its hash, which changes when the picture does). ``written`` writes Gamma's text back, each
link as the memo remembers it where it can and in the vault's habit
otherwise, with the memo of what it wrote. The engine calls them only for a
block whose text changed on that side (identity first): an
unchanged block keeps its file text, so a link no reading gives back as
written (``%20``, a title, an alias) never makes a block read as changed.

Links resolve as a Markdown zip's do (``markdown_zip_links.resolve_path``):
relative to the note, then by vault path, then by name anywhere, ignoring
case, names NFC. A link that resolves to nothing stays as typed, unless the
memo remembers its spelling: ``[[Old]]`` whose note was renamed stays a
mention. A page outside the vault is its absolute Gamma address, read back
as a mention; a new link to a block asks for an anchor in its file
(``LinkContext.new_anchors``), and a picture pasted in Gamma goes to the
attachment folder under its upload's name (``LinkContext.new_files``; never a
kind of file Gamma does not store or serve, an executable): the engine writes
both. Nothing inside code or math is a link, here as on
Gamma's screen. A caret the lens escaped where the file would read an anchor
(``press \\^c``) is the caret Gamma holds; the lens escapes it again.

A highlight is the one block of its own dialect: a ``[!quote]`` callout whose
title links the page's PDF at the page, the quote, then the person's note,
and in Gamma ``highlight(note, quote, page)``. Its quote and page are
Gamma's: a block matched to a highlight stays one whatever the file now says
of them, and only its note is read, by taking the known quote's lines out; a
person's edit of the quote stays in the file until Gamma's quote or page
changes.
"""

import copy
import difflib
import json
import posixpath
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

from ..blocks_store import BLOCK_ID_RE
from ..documents.formats import IMAGE_MEDIA_TYPES, media_type
from ..markdown_export import _BLOCK_REF_RE
from ..markdown_import import _FENCE_LINE_RE, _fence_marks, shuts_fence
from ..markdown_zip_links import (_IMAGE_SIZE_RE, _LINK_RE, _MD_EXTS, _WIKILINK_RE, _norm_heading, _split_ext,
                                 named_block, resolve_path)
from ..md_lens import _ESCAPED_ANCHOR_RE, _opcodes, flatten
from ..note_markup import LITERAL_SPANS
from ..obsidian_export import anchor_marker
from ..storage import UPLOAD_REF_RE

FORMATS = ("shortest", "relative", "absolute")   # Obsidian's ``newLinkFormat``
# Where a vault with no ``.obsidian/app.json`` keeps a pasted picture: the
# folder Gamma's own vault export names (``obsidian_export.APP_JSON``). An
# app.json without the key means Obsidian's default, the vault's root.
ATTACHMENTS = "attachments"

# A file's links, and what holds none: code, math, an escape (Gamma's
# reading of a block, ``note_markup.LITERAL_SPANS``). A link comes first: code
# in its label is the label's.
_FILE_LINKS_RE = re.compile(f"(?P<wiki>{_WIKILINK_RE.pattern})|(?P<md>{_LINK_RE.pattern})|{LITERAL_SPANS}")
# Gamma's: a mention or an embed, a Markdown link (an upload's when its
# address is one).
_GAMMA_LINKS_RE = re.compile(f"(?P<ref>{_BLOCK_REF_RE.pattern})|(?P<md>{_LINK_RE.pattern})|{LITERAL_SPANS}")
# What stands before a line's text that a fence may follow: quote marks and
# list markers.
_LEAD_RE = re.compile(r"[ \t]*(?:>[ \t]?|(?:[-*+]|\d{1,9}[.)])[ \t]+)*")
# The one test of a highlight: its first line opens a quote callout, in any
# case, folded or not, ``>[!quote]`` too.
_CALLOUT_RE = re.compile(r"[ \t]{0,3}>[ \t]*\[!quote(?![\w-])", re.I)
_CALLOUT_TITLE_RE = re.compile(r"[ \t]{0,3}>[ \t]*\[!quote(?:\|[^\]\n]*)?\][-+]?[ \t]*(.*)", re.I)
# A callout's first line, of any type: a matched highlight's title, whatever
# it says now.
_TITLE_LINE_RE = re.compile(r"[ \t]{0,3}>[ \t]*\[![^\]\n]*\]")
_QUOTED_RE = re.compile(r"[ \t]{0,3}>[ \t]?")
_PAGE_RE = re.compile(r"(?:^|[#&])page=(\d{1,7})(?!\d)", re.I)
_PAGE_LABEL_RE = re.compile(r"\bp\.\s*(\d{1,7})\b")
# What a Markdown link's target is written with escaped: what would end it,
# open a fragment or a query, or read as an angle-bracket target.
_HREF_ESCAPES_RE = re.compile(r"[\x00-\x20%()<>\[\]#?^|\\]")
_LABEL_ESCAPES_RE = re.compile(r"([\\\[\]])")
_WORDS_RE = re.compile(r"\w+|\s+|.", re.S)
# How alike, word for word, a line must be to a line of the known quote to
# be that line as the person edited it: below, it is the note's.
QUOTE_ALIKE = 0.5
_HEADING_TEXT_RE = re.compile(r"#{1,6}[ \t]+(.*?)(?:[ \t]+#+)?[ \t]*$")


# --- the vault's habit ---------------------------------------------------------

@dataclass(frozen=True)
class Habit:
    """How the vault writes a new link: Markdown links or wikilinks, by the
    shortest path, relative to the note or from the vault's root, and where a
    pasted picture goes (Obsidian's ``.obsidian/app.json``: "/" its root, "./"
    beside the note)."""
    markdown: bool = False
    format: str = "shortest"
    attachments: str = ATTACHMENTS


def read_habit(text) -> Habit:
    """The habit an ``app.json``'s text says (None: the vault has none)."""
    if text is None:
        return Habit()
    try:
        data = json.loads(text)
    except ValueError:      # (a decoding error is one)
        return Habit()
    if not isinstance(data, dict):
        return Habit()
    folder = data.get("attachmentFolderPath")
    return Habit(markdown=data.get("useMarkdownLinks") is True,
                 format=data.get("newLinkFormat") if data.get("newLinkFormat") in FORMATS else "shortest",
                 attachments=folder.strip() if isinstance(folder, str) else "/")


def habit_at(root) -> Habit:
    """The habit of the vault at ``root``."""
    try:
        return read_habit((Path(root) / ".obsidian" / "app.json").read_bytes())
    except OSError:
        return Habit()


# --- the context -------------------------------------------------------------------

def _folded(path: str) -> str:
    return unicodedata.normalize("NFC", path).lower()


def named(value: str, note: str, paths) -> str:
    """The path among ``paths`` (vault paths) that a front matter value of the
    note at ``note`` names, written as a path, a wikilink or a Markdown link
    (``source: My%20Paper.pdf``, ``parent: "[[Plan]]"``), by the links' one
    rule (``resolve_path``); "" for none."""
    text = str(value or "").strip()
    wiki, md = _WIKILINK_RE.fullmatch(text), _LINK_RE.fullmatch(text)
    href = wiki[2] if wiki else (md[3] if md else text).strip("<>")
    lower, stems, by_name = {}, {}, {}
    for path in paths:
        lower.setdefault(_folded(path), path)
        stem, ext = _split_ext(path)
        if ext in _MD_EXTS:
            stems.setdefault(_folded(stem), []).append(path)
        else:
            by_name.setdefault(_folded(posixpath.basename(path)), []).append(path)
    return resolve_path(unicodedata.normalize("NFC", posixpath.dirname(note)), href, lower, stems, by_name) or ""


class LinkContext:
    """What a block's links need of the vault: its notes (``add_note``), the
    files Gamma holds as uploads (``add_file``), a work folder's files that
    are pages (``add_page_file``), the anchors and headings of
    its blocks, its habit; and of Gamma: ``page_of`` (a block or page id →
    its page's id, None for none), ``title_of`` (a page id → its title),
    ``address`` (``https://host/?ws=<id>``, the base of an absolute link; ""
    for none) and ``store(path)``, which stores a vault file as an upload and
    gives its name (None: it cannot).

    One per round; ``at`` gives the view of one note, which shares the
    tables. ``read_only``: the vault's pages whose files take no new anchor
    this round. What ``written`` asks of the vault lands in ``new_anchors``
    (block id → marker, to write after that block in its file) and
    ``new_files`` (vault path → upload name, to copy there). ``more()``,
    for a round that has not listed the vault's pictures (a page's), lists
    them, asked once when a link names a picture the tables do not hold."""

    def __init__(self, habit=None, *, address="", page_of=None, title_of=None, store=None, more=None):
        self.habit = habit or Habit()
        self.address = address
        self.page_of = page_of or (lambda block_id: None)
        self.title_of = title_of or (lambda page_id: "")
        self.store = store or (lambda path: None)
        self.pages, self.page_path = {}, {}      # vault path ↔ page id
        self.file_pages = {}                     # a work folder's file's vault path → its page id
        self.files, self.names = {}, {}          # vault path ↔ upload name
        self.anchors, self.marks, self.headings = {}, {}, {}
        self.read_only = set()
        self.new_anchors, self.new_files = {}, {}
        self.note, self.page_id, self.pdf = "", "", ""
        self._tables = {}
        self._more = [more] if more else []      # one list the views share: asked once

    def add_note(self, path: str, page_id: str, tree=()):
        """A note of the vault, and its blocks as the lens reads them (a tree
        of ``{id, content, anchor?, children}``): their anchors and headings
        are what a link's ``#^id`` and ``#Heading`` name."""
        self.pages[path] = page_id
        self.page_path[page_id] = path
        headings = self.headings.setdefault(page_id, {})
        for node in flatten(list(tree)):
            if node.get("anchor"):
                self._mark(page_id, node["id"], node["anchor"])
            found = _HEADING_TEXT_RE.match(node.get("content") or "")
            if found:
                headings.setdefault(_norm_heading(found[1]), node["id"])
        self._tables.clear()

    def add_file(self, path: str, name: str = ""):
        """A file of the vault (no note), which Gamma holds as the upload
        ``name`` ("": not yet; ``store`` does when a link names it)."""
        self.files[path] = name
        if name:
            self.names.setdefault(name, path)
        self._tables.clear()

    def add_upload(self, path: str, name: str):
        """A file of the vault Gamma read before as the upload ``name``: a
        link of Gamma's to that upload names the file, while a link of the
        file's to it is read afresh (``store``), its bytes perhaps changed
        since."""
        self.names.setdefault(name, path)
        self.files.setdefault(path, "")
        self._tables.clear()

    def add_page_file(self, path: str, page_id: str):
        """A work folder's file that is the page ``page_id``: a link to it is
        a mention of the page, found by its name too, as a file is. The page
        is spelled back by this path unless it has a note (a PDF paired with
        one), whose path it keeps; a page with no note takes no anchor
        (``_anchor``): its notes are never written to disk."""
        self.file_pages[path] = page_id
        self.page_path.setdefault(page_id, path)
        self.files.setdefault(path, "")
        self._tables.clear()

    def at(self, note: str, page_id: str, pdf: str = "") -> "LinkContext":
        """The view of the note at the vault path ``note``, the page
        ``page_id``'s, whose PDF is the vault path ``pdf`` ("" for none)."""
        view = copy.copy(self)
        view.note, view.page_id, view.pdf = note, page_id, pdf
        return view

    # --- reading the tables --------------------------------------------------------

    def _mark(self, page_id, block_id, marker):
        self.anchors.setdefault(block_id, marker)
        self.marks.setdefault(page_id, {}).setdefault(marker.lower(), block_id)

    def _lookup(self):
        """``(lower, stems, files by name)`` as ``resolve_path`` reads them."""
        if not self._tables:
            lower, stems, by_name = {}, {}, {}
            for path in (*self.files, *self.pages):
                lower.setdefault(_folded(path), path)
            for path in self.pages:
                stems.setdefault(_folded(_split_ext(path)[0]), []).append(path)
            for path in self.files:
                by_name.setdefault(_folded(posixpath.basename(path)), []).append(path)
            self._tables.update(lower=lower, stems=stems, by_name=by_name)
        return self._tables["lower"], self._tables["stems"], self._tables["by_name"]

    def _resolve(self, href: str):
        """The vault path a link's target names, or None; for a picture no
        table holds, once ``more`` has listed the vault's."""
        found = self._found(href)
        if found is None and self._more and _split_ext(re.split(r"[#?]", href, maxsplit=1)[0])[1] in IMAGE_MEDIA_TYPES:
            for path in self._more.pop()():
                self.files.setdefault(path, "")
            self._tables.clear()
            found = self._found(href)
        return found

    def _found(self, href: str):
        lower, stems, by_name = self._lookup()
        return resolve_path(unicodedata.normalize("NFC", posixpath.dirname(self.note)), href, lower, stems, by_name)

    def _block(self, page_id, sub):
        """The block ``#Heading`` / ``#^id`` names in the page, else the page."""
        return named_block(sub, self.marks.get(page_id, {}), self.headings.get(page_id, {})) or page_id

    def _upload(self, path):
        """The upload name of the vault file at ``path``, stored now if need be."""
        name = self.files.get(path)
        if not name:
            name = self.store(path)
            if name:
                self.add_file(path, name)
        return name

    def _file_path(self, name):
        """The vault path of Gamma's upload ``name``, placed in the attachment
        folder when the vault does not hold it yet; None for a kind of file
        Gamma never stores or serves (``documents.formats.media_type``: an
        executable a .gamma import or another server holds), which goes into
        no vault."""
        path = self.names.get(name)
        if path is None:
            if media_type(posixpath.splitext(name)[1].lower()) is None:
                return None
            path = posixpath.join(self._attachments(), name)
            self.add_file(path, name)
            self.new_files[path] = name
        return path

    def _attachments(self) -> str:
        """The attachment folder for this note, as a vault path ("" the root)."""
        folder = self.habit.attachments.strip().replace("\\", "/")
        if folder == "." or folder.startswith("./"):
            joined = posixpath.join(posixpath.dirname(self.note), folder[2:].strip("/"))
            return posixpath.normpath(joined) if joined else ""
        return folder.strip("/")

    def _anchor(self, block_id, page_id):
        """The marker of a block of another note, asked for when it has none;
        None when its file takes none this round, or when the page's file is
        no note (a work folder's file page)."""
        marker = self.anchors.get(block_id)
        if marker is None and page_id not in self.read_only and self.page_path.get(page_id) in self.pages:
            marker = anchor_marker(block_id)
            if not re.search("[A-Za-z]", marker):      # (an anchor needs a letter)
                marker = f"b{marker}"
            taken, base, n = self.marks.get(page_id, {}), marker, 1
            while taken.get(marker.lower(), block_id) != block_id:
                n += 1
                marker = f"{base}-{n}"
            self._mark(page_id, block_id, marker)
            self.new_anchors[block_id] = marker
        return marker

    def _gamma_target(self, href):
        """The page or block a link to this server's workspace names, or None."""
        if not self.address or not href.lower().startswith(("http://", "https://")):
            return None
        mine, theirs = urlsplit(self.address), urlsplit(href)
        if (mine.scheme.lower(), mine.netloc.lower()) != (theirs.scheme.lower(), theirs.netloc.lower()) \
                or theirs.path not in ("", "/"):
            return None
        query, wanted = parse_qs(theirs.query), parse_qs(mine.query)
        if wanted.get("ws") and query.get("ws") != wanted["ws"]:
            return None
        target = (query.get("block") or query.get("page") or [""])[0]
        return target if BLOCK_ID_RE.match(target) else None

    def _address(self, block_id, page_id):
        sep = "&" if "?" in self.address else "?"
        return f"{self.address}{sep}page={page_id}" if block_id == page_id else f"{self.address}{sep}block={block_id}"


# --- where links can be -----------------------------------------------------------

def _prose(text: str) -> list:
    """The stretches of ``text`` outside fenced code and ``$$`` blocks, as
    ``(start, end)``: whole lines, a fence in a quote or an item too."""
    out, start, at, closer = [], 0, 0, None
    for line in text.split("\n"):
        end = at + len(line)
        bare = line[_LEAD_RE.match(line).end():]
        if closer:
            if (closer == "$$" and "$$" in bare) or (closer != "$$" and shuts_fence(bare, closer)):
                closer, start = None, end + 1
        elif _FENCE_LINE_RE.match(bare) or (bare.startswith("$$") and "$$" not in bare[2:]):
            if at > start:
                out.append((start, at - 1))
            closer = _fence_marks(bare) if _FENCE_LINE_RE.match(bare) else "$$"
        at = end + 1
    if not closer and start < len(text):
        out.append((start, len(text)))
    return out


def _in_table(text: str, at: int) -> bool:
    """Whether the line ``at`` stands in is a table's row, where a pipe needs
    its backslash."""
    return text[text.rfind("\n", 0, at) + 1:at].lstrip(" \t").startswith("|")


def _cut(line: str) -> int:
    """Where a callout's line's text starts, past its quote mark."""
    found = _QUOTED_RE.match(line)
    return found.end() if found else 0


def _wiki_parts(text: str):
    """A wikilink's ``(bang, target, sub, alias)``, the backslash of a pipe
    escaped in a table's cell its pipe's, not the target's."""
    bang, target, sub, alias = _WIKILINK_RE.fullmatch(text).groups()
    if alias is not None and sub.endswith("\\"):
        sub = sub[:-1]
    elif alias is not None and not sub and target.endswith("\\"):
        target = target[:-1]
    return bang, target, sub, alias


# --- file to Gamma --------------------------------------------------------------------

def _file_link(ctx: LinkContext, m):
    """``(key, Gamma's text)`` of a file's link, or None for one that names
    nothing Gamma knows."""
    if m["wiki"]:
        bang, target, sub, alias = _wiki_parts(m.group())
        target = target.strip()
        if not target:
            return _mention(ctx, bang, ctx.page_id, sub) if sub and ctx.page_id else None
        path = ctx._resolve(target)
        if path is None:
            return None
        if path in ctx.pages:
            return _mention(ctx, bang, ctx.pages[path], sub)
        if _file_page(ctx, path, bang):
            return _mention(ctx, "", ctx.file_pages[path], "")
        name = ctx._upload(path)
        if not name:
            return None
        alias = (alias or "").strip()
        if bang and _split_ext(path)[1] in IMAGE_MEDIA_TYPES:
            size = alias if _IMAGE_SIZE_RE.match(alias) else ""
            return path, (f"![|{size}](/api/uploads/{name})" if size else f"![{alias}](/api/uploads/{name})")
        return path, f"[{alias or posixpath.basename(path)}](/api/uploads/{name})"
    bang, label, href, title = _LINK_RE.fullmatch(m.group()).groups()
    href = href.strip("<>").strip()
    if href.startswith("#"):
        return _mention(ctx, "", ctx.page_id, unquote(href)) if ctx.page_id and href[1:] else None
    target = ctx._gamma_target(href)
    if target:
        return target, f"[[{target}]]"
    path = ctx._resolve(href)
    if path is None:
        return None
    if path in ctx.pages:
        sub = unquote(href.partition("#")[2])
        return _mention(ctx, "", ctx.pages[path], f"#{sub}" if sub else "")
    if _file_page(ctx, path, bang):
        return _mention(ctx, "", ctx.file_pages[path], "")
    name = ctx._upload(path)
    return (path, f"{bang}[{label}](/api/uploads/{name}{title})") if name else None


def _file_page(ctx, path: str, bang) -> bool:
    """Whether a link to the file at ``path`` is a mention of its page: a
    work folder's file that is one, unless it is the note's own PDF or a
    picture embedded (its upload's address draws it). A ``#…`` after a
    file's name names nothing in the page and is dropped."""
    return (path in ctx.file_pages and path != ctx.pdf
            and not (bang and _split_ext(path)[1] in IMAGE_MEDIA_TYPES))


def _mention(ctx, bang, page_id, sub):
    """A link to a note, ``#Heading`` / ``#^id`` naming one of its blocks: an
    embed of one anchored block is Gamma's embed, anything else a mention
    (``markdown_zip_links.Links.rewrite_links``'s rule)."""
    block_id = ctx._block(page_id, sub)
    anchored = (sub or "").split("#")[-1].strip().startswith("^") and block_id != page_id
    return block_id, (f"![[{block_id}]]" if bang and anchored else f"[[{block_id}]]")


def _remembered(ctx, memo, found) -> bool:
    """Whether a spelling the memo remembers keeps the Gamma text it had
    there although it resolves now: a mention or an embed of the same block,
    or of its page, since a file's link can say less than Gamma (an embed of
    a block outside the vault is a link). A link to a file takes what it
    resolves to: its upload's name changes with its bytes."""
    key, token = memo
    return bool(_BLOCK_REF_RE.fullmatch(token)) and found[0] in (key, ctx.page_of(key))


def _links_to_gamma(text: str, remembered: dict, ctx: LinkContext) -> tuple[str, dict]:
    """``text``'s links in Gamma's form, and how each was written:
    ``{key: [[Gamma's text, the file's, where Gamma's starts], ...]}``. A
    spelling the memo has more than once takes its Gamma texts in order."""
    spelled = {}
    for _, spelling, key, token in sorted((entry[2], entry[1], key, entry[0])
                                           for key, entries in remembered.items() for entry in entries):
        spelled.setdefault(spelling, []).append((key, token))
    out, size, links, done = [], 0, {}, 0

    def emit(piece):
        nonlocal size
        out.append(piece)
        size += len(piece)

    for start, end in _prose(text):
        emit(text[done:start])
        escaped = {found.start(2) - 1 for found in _ESCAPED_ANCHOR_RE.finditer(text, start, end)}
        for m in _FILE_LINKS_RE.finditer(text, start, end):
            emit(text[start:m.start()])
            start = m.end()
            if m["esc"]:
                emit(m.group()[1:] if m.start() in escaped else m.group())
                continue
            if not (m["wiki"] or m["md"]):
                emit(m.group())
                continue
            spelling = m.group()
            queue = spelled.get(spelling)
            found, memo = _file_link(ctx, m), (queue.pop(0) if len(queue) > 1 else queue[0]) if queue else None
            if memo and (found is None or _remembered(ctx, memo, found)):
                found = memo
            if found is not None:
                links.setdefault(found[0], []).append([found[1], spelling, size])
            emit(spelling if found is None else found[1])
        emit(text[start:end])
        done = end
    emit(text[done:])
    return "".join(out), links


# --- Gamma to file -----------------------------------------------------------------------

class _Link:
    """A link in Gamma's text: a mention or an embed (``ref``) of ``target``,
    or an upload's (``file``) by its ``name``; ``key`` its vault path when the
    vault holds it."""

    def __init__(self, m, ctx):
        self.start, self.end, self.token = m.start(), m.end(), m.group()
        if m["ref"]:
            self.bang, self.target = _BLOCK_REF_RE.fullmatch(self.token).groups()
            self.kind, self.key = "ref", self.target
            return
        self.bang, self.label, href, self.title = _LINK_RE.fullmatch(self.token).groups()
        found = UPLOAD_REF_RE.fullmatch(href.strip("<>").strip())
        self.kind = "file" if found else ""
        self.name = found[1] if found else ""
        self.key = ctx.names.get(self.name) if found else None


def _gamma_links(text: str, ctx: LinkContext) -> list:
    """The links of Gamma's text a file writes its own way."""
    out = []
    for start, end in _prose(text):
        for m in _GAMMA_LINKS_RE.finditer(text, start, end):
            if m["ref"] or m["md"]:
                link = _Link(m, ctx)
                if link.kind:
                    out.append(link)
    return out


def _pieces(text: str, links: list) -> list:
    """``text`` as the words and links ``difflib`` aligns."""
    out, at = [], 0
    for link in links:
        out += _WORDS_RE.findall(text[at:link.start])
        out.append(f"\x00{link.token}")
        at = link.end
    return out + _WORDS_RE.findall(text[at:])


def _links_to_file(text: str, remembered: dict, ctx: LinkContext, base=None) -> tuple[str, dict]:
    """``text``'s links in the file's form, and the memo of that text. A link
    in a run ``difflib`` finds unchanged since ``base`` keeps the spelling it
    had there; another takes an unused spelling the memo has of the same
    Gamma text, then the form of an unused one of the same target (a label
    edited in Gamma), then the habit."""
    links = _gamma_links(text, ctx)
    if not links:
        return text, {}
    entries = [(key, n, entry) for key, items in remembered.items() for n, entry in enumerate(items)]
    used, chosen = set(), {}
    if base is not None:
        held = {entry[2]: (key, n) for key, n, entry in entries}
        olds = _gamma_links(base, ctx)
        old_pieces, new_pieces = _pieces(base, olds), _pieces(text, links)
        old_at = {k: n for n, k in enumerate(k for k, piece in enumerate(old_pieces) if piece[:1] == "\x00")}
        new_at = {k: n for n, k in enumerate(k for k, piece in enumerate(new_pieces) if piece[:1] == "\x00")}
        for tag, i1, i2, j1, _ in _opcodes(old_pieces, new_pieces):
            for k in range(i2 - i1 if tag == "equal" else 0):
                if i1 + k in old_at:
                    old = olds[old_at[i1 + k]]
                    spot = held.get(old.start)
                    if spot and spot not in used and remembered[spot[0]][spot[1]][0] == old.token:
                        chosen[new_at[j1 + k]] = (spot[0], remembered[spot[0]][spot[1]][1])
                        used.add(spot)
    out, memo, done = [], {}, 0
    for n, link in enumerate(links):
        out.append(text[done:link.start])
        done = link.end
        key, spelling = chosen.get(n) or _spelling(link, remembered, entries, used, ctx, _in_table(text, link.start))
        if key is not None:
            memo.setdefault(key, []).append([link.token, spelling, link.start])
        out.append(spelling)
    out.append(text[done:])
    return "".join(out), memo


def _spelling(link: _Link, remembered, entries, used, ctx, in_table) -> tuple:
    """``(key, spelling)`` of a link no unchanged run holds; key None for one
    written as Gamma typed it."""
    key = link.key
    if key is None and link.kind == "file":
        key = next((k for k, _, entry in entries if entry[0] == link.token), None)
    same = [(key, n) for n in range(len(remembered.get(key) or ()))]
    exact = next((spot for spot in same if spot not in used and remembered[key][spot[1]][0] == link.token), None)
    if exact:
        used.add(exact)
        return key, remembered[key][exact[1]][1]
    like = next((spot for spot in same if spot not in used), None)
    if like:
        used.add(like)
        return key, _respelled(link, key, remembered[key][like[1]][1])
    if link.kind == "file":
        key = key or ctx._file_path(link.name)
        return (key, _new_file_link(link, key, ctx, in_table)) if key else (None, link.token)
    spelling = _new_mention(link, ctx)
    return (None if spelling == link.token else key), spelling


def _respelled(link: _Link, key, was: str) -> str:
    """``was``, a spelling of the link's target, with the rest of the link
    as Gamma now holds it: the target as written, the label, the size."""
    wiki = _WIKILINK_RE.fullmatch(was)
    if link.kind == "ref":
        return link.bang + was.lstrip("!") if wiki else was
    image = link.bang and _split_ext(key)[1] in IMAGE_MEDIA_TYPES
    if wiki:
        bang, target, sub, _ = _wiki_parts(was)
        pipe = "\\|" if "\\|" in was else "|"
        return f"{'!' if image else bang}[[{target}{sub}{_alias(link, key, image, pipe)}]]"
    md = _LINK_RE.fullmatch(was)
    href = was[md.start(3):md.end(3)] if md else _href(key)
    return f"{link.bang}[{link.label}]({href}{link.title})"


def _alias(link: _Link, path: str, image: bool, pipe: str) -> str:
    """A wikilink's alias for Gamma's label: a picture's size or its text, a
    file's label unless it is the file's name."""
    if image:
        alias = link.label[1:] if link.label.startswith("|") else link.label
    else:
        alias = "" if link.label == posixpath.basename(path) else link.label
    return pipe + alias if alias else ""


def _target_text(ctx, path: str, wiki: bool, form: str) -> str:
    """How ``form`` names the vault path ``path``: by the shortest path that
    is unique, from the note, or from the vault's root; a wikilink's note
    without its extension."""
    is_note = path in ctx.pages
    if form == "relative":
        text = posixpath.relpath(path, posixpath.dirname(ctx.note) or ".")
    elif form == "absolute":
        text = path
    else:
        _, stems, by_name = ctx._lookup()
        leaf = posixpath.basename(path)
        alike = stems.get(_folded(_split_ext(leaf)[0])) if is_note else by_name.get(_folded(leaf))
        text = leaf if not alike or len(alike) == 1 else path
    ext = _split_ext(text)[1]
    return text[:-len(ext)] if wiki and is_note and ext in _MD_EXTS else text


def _href(text: str) -> str:
    return _HREF_ESCAPES_RE.sub(lambda m: "".join(f"%{b:02X}" for b in m.group().encode()), text)


def _label(text: str) -> str:
    return _LABEL_ESCAPES_RE.sub(r"\\\1", text)


def _named(ctx, path: str, make) -> str:
    """The first spelling ``make(target text, wiki)`` gives that reads back
    as ``path``: the habit's, then from the vault's root, then a Markdown
    link from it."""
    wiki = not ctx.habit.markdown
    tries = [(_target_text(ctx, path, wiki, ctx.habit.format), wiki),
             (_target_text(ctx, path, wiki, "absolute"), wiki), (path, False)]
    spellings = [make(text, as_wiki) for text, as_wiki in tries]
    for spelling in spellings:
        wikilink, md = _WIKILINK_RE.fullmatch(spelling), _LINK_RE.fullmatch(spelling)
        target = _wiki_parts(spelling)[1].strip() if wikilink else md[3].strip("<>").strip() if md else ""
        if target and ctx._resolve(target) == path:
            return spelling
    return spellings[0]


def _new_mention(link: _Link, ctx) -> str:
    """A mention no memo knows, in the habit: of a note of the vault, of a
    block in one (``#^marker``, asked for when it has none; its page's when
    its file takes none), of a work folder's file page (by its path, the
    leaf its label), of a page outside the vault (its absolute address), or
    as typed."""
    target = link.target
    page_id = ctx.page_of(target) or (target if target in ctx.page_path else None)
    if page_id is None:
        return link.token
    if page_id not in ctx.page_path:
        if not ctx.address:
            return link.token
        return f"[{_label(ctx.title_of(page_id) or 'Untitled')}]({ctx._address(target, page_id)})"
    marker = ctx._anchor(target, page_id) if target != page_id else None
    sub = f"#^{marker}" if marker else ""
    path = ctx.page_path[page_id]
    leaf = posixpath.basename(path)
    shown = _split_ext(leaf)[0] if path in ctx.pages else leaf
    if sub and page_id == ctx.page_id:
        return f"[{_label(shown)}]({sub})" if ctx.habit.markdown else f"{link.bang}[[{sub}]]"

    def make(text, wiki):
        return f"{link.bang}[[{text}{sub}]]" if wiki else f"[{_label(shown)}]({_href(text)}{sub})"
    return _named(ctx, path, make)


def _new_file_link(link: _Link, path: str, ctx, in_table) -> str:
    """A picture's or a file's link no memo knows, in the habit."""
    image = link.bang and _split_ext(path)[1] in IMAGE_MEDIA_TYPES
    pipe = "\\|" if in_table else "|"

    def make(text, wiki):
        if wiki:
            return f"{link.bang}[[{text}{_alias(link, path, image, pipe)}]]"
        return f"{link.bang}[{link.label}]({_href(text)}{link.title})"
    return _named(ctx, path, make)


# --- highlights ------------------------------------------------------------------------------

def highlight(note: str, quote: str, page) -> dict:
    """A highlight's text in Gamma: its note (the block's content), its quote
    and its PDF page (None: none)."""
    return {"note": note, "quote": quote, "page": page}


def is_callout(text: str) -> bool:
    """The one test of a highlight in a file: its first line opens a
    ``[!quote]`` callout."""
    return bool(_CALLOUT_RE.match(text))


def _split_quote(lines: list, known) -> tuple[int, int]:
    """Where the quote's lines end among a callout's ``lines`` (those after
    its title), and where the note starts past the blank lines between. The
    quote ``known`` (a highlight's; None for a callout Gamma does not know)
    is taken out line by line, aligned: what reads like it, or stands between
    its lines, is the quote as the person edited it; the first line that does
    neither starts the note, a lazy one too. A blank line in the quote stays
    in it. A new callout's quote is its quoted lines up to a blank one."""
    texts = [line[_cut(line):].strip() for line in lines]
    end = 0
    if known is None:
        while end < len(lines) and texts[end] and _QUOTED_RE.match(lines[end]):
            end += 1
    elif known:
        quoted = [line.strip() for line in known.split("\n")]
        for tag, i1, i2, j1, j2 in _opcodes(quoted, texts):
            if tag == "equal":
                end = j2
                continue
            pairs = min(i2 - i1, j2 - j1)
            alike = next((k for k in range(pairs) if not _alike(quoted[i1 + k], texts[j1 + k])), pairs)
            end = j1 + alike
            if alike < pairs or j2 - j1 > i2 - i1:
                break
    start = end
    while start < len(lines) and not texts[start]:
        start += 1
    return end, start


def _alike(line: str, said: str) -> bool:
    """Whether ``said`` is the quote's ``line`` as a person edited it."""
    a, b = line.split(), said.split()
    return bool(a or b) and difflib.SequenceMatcher(None, a, b, autojunk=False).ratio() >= QUOTE_ALIKE


def _title_page(ctx, title: str):
    """Whether a callout's title links the page's own PDF, and at which page."""
    for start, end in _prose(title):
        for m in _FILE_LINKS_RE.finditer(title, start, end):
            if m["wiki"]:
                _, target, sub, alias = _wiki_parts(m.group())
                path, fragment, text = ctx._resolve(target.strip()), sub.lstrip("#"), alias or ""
            elif m["md"]:
                _, text, href, _ = _LINK_RE.fullmatch(m.group()).groups()
                href = href.strip("<>").strip()
                path, fragment = ctx._resolve(href), unquote(href.partition("#")[2])
            else:
                continue
            if path and path == ctx.pdf:
                found = _PAGE_RE.search(fragment) or _PAGE_LABEL_RE.search(text)
                return True, int(found[1]) if found else None
    return False, None


def _highlight_to_gamma(text: str, remembered: dict, ctx, known):
    """A callout as a highlight, ``known`` being the one it matched (None for
    a new one); None when a new callout is no highlight of the page's PDF."""
    lines = text.split("\n")
    head = 1 if (is_callout if known is None else _TITLE_LINE_RE.match)(lines[0]) else 0
    if known is None:
        found = _CALLOUT_TITLE_RE.match(lines[0]) if head else None
        own, page = _title_page(ctx, found[1]) if found else (False, None)
        if not own:
            return None
    body = lines[head:]
    end, start = _split_quote(body, None if known is None else known["quote"])
    if known is None:
        quote = "\n".join(line[_cut(line):].strip() for line in body[:end])
    else:
        quote, page = known["quote"], known["page"]
    rest, last = body[start:], len(body) - start
    while last and not rest[last - 1][_cut(rest[last - 1]):].strip():
        last -= 1
    dress = [[line[:_cut(line)], line[_cut(line):]] for line in rest[:last]]
    note, links = _links_to_gamma("\n".join(said for _, said in dress), remembered.get("links") or {}, ctx)
    memo = {"head": "\n".join(lines[:head + end]), "for": [quote, page]}
    if rest:
        memo.update(sep=body[end:start], tail=rest[last:])
    else:
        memo["tail"] = body[end:]
    return highlight(note, quote, page), _with_dress(memo, dress, links)


def _with_dress(memo: dict, dress: list, links: dict) -> dict:
    """A highlight's memo with its note's lines' marks where they are not the
    usual ones, and its links."""
    if any(prefix != _quoted(said) for prefix, said in dress):
        memo["dress"] = dress
    if links:
        memo["links"] = links
    return {key: value for key, value in memo.items() if value != [] or key == "sep"}


def _quoted(said: str) -> str:
    """The marks a note's line takes in a callout."""
    return "> " if said else ">"


def _head(ctx, quote: str, page) -> str:
    """A highlight's title and quote, written new: the title links the page's
    PDF at the page, in the vault's habit."""
    if ctx.pdf:
        label, fragment = (f"p. {page}", f"#page={page}") if page else ("", "")

        def make(text, wiki):
            if wiki:
                return f"[[{text}{fragment}{'|' + label if label else ''}]]"
            return f"[{label or _label(posixpath.basename(ctx.pdf))}]({_href(text)}{fragment})"
        title = _named(ctx, ctx.pdf, make)
    else:
        title = f"p. {page}" if page else ""
    lines = [f"> [!quote] {title}".rstrip()]
    return "\n".join(lines + ([f"> {line}" if line else ">" for line in quote.split("\n")] if quote else []))


def _highlight_to_file(g: dict, memo: dict, ctx, base) -> tuple[str, dict]:
    """A highlight as its callout: the title and quote as the file had them
    while Gamma's quote and page are what they were, the note's lines with
    the marks they had."""
    note, links = _links_to_file(g["note"], memo.get("links") or {}, ctx,
                                 base["note"] if isinstance(base, dict) else None)
    kept = "head" in memo and memo.get("for") == [g["quote"], g["page"]]
    head = memo["head"] if kept else _head(ctx, g["quote"], g["page"])
    lines = head.split("\n") if head else []
    out = {"head": head, "for": [g["quote"], g["page"]]}
    if not note:
        out["tail"] = memo.get("tail", []) if "sep" not in memo else []
        return "\n".join(lines + out["tail"]), _with_dress(out, [], links)
    new = note.split("\n")
    old = memo.get("dress") or []
    prefixes = [_quoted(said) for said in new]
    for _, i1, i2, j1, j2 in _opcodes([said for _, said in old], new):
        for k in range(min(i2 - i1, j2 - j1)):        # (a line edited in place keeps its marks)
            if bool(old[i1 + k][1]) == bool(new[j1 + k]):
                prefixes[j1 + k] = old[i1 + k][0]
    out.update(sep=memo["sep"] if "sep" in memo else [">"], tail=memo.get("tail", []) if "sep" in memo else [])
    dress = [[prefix, said] for prefix, said in zip(prefixes, new)]
    lines += out["sep"] + [prefix + said for prefix, said in dress] + out["tail"]
    return "\n".join(lines), _with_dress(out, dress, links)


# --- per block -----------------------------------------------------------------------------------

def to_gamma(file_text: str, memo: dict, ctx: LinkContext, base=None) -> tuple:
    """A block's file text in Gamma's space: ``(g, memo)``. ``memo``: the
    base's, so a link that resolves to nothing now keeps the target it had;
    ``base``: the base's ``g`` where the block matched one. ``g`` is the
    block's text, or a ``highlight(...)``: for a block that matched one, and
    for a new callout that links this page's PDF (a vault copied to another
    computer brings its highlights so)."""
    memo = memo or {}
    if isinstance(base, dict) or (base is None and ctx.pdf and is_callout(file_text)):
        read = _highlight_to_gamma(file_text, memo, ctx, base if isinstance(base, dict) else None)
        if read is not None:
            return read
    text, links = _links_to_gamma(file_text, memo.get("links") or {}, ctx)
    return text, ({"links": links} if links else {})


def written(g, memo: dict, ctx: LinkContext, base=None) -> tuple:
    """``(file text, memo)``: a block's Gamma text ``g`` (a ``highlight(...)``
    too) in the file's space, ``memo`` being the memo of the file text
    ``base`` (the ``g`` the block had) was read from, and the memo of what it
    writes, which ``to_gamma`` reads it back with to ``g``. Called only for a
    ``g`` that changed: an unchanged block's file text is the base's."""
    memo = memo or {}
    if isinstance(g, dict):
        return _highlight_to_file(g, memo, ctx, base)
    text, links = _links_to_file(g, memo.get("links") or {}, ctx, base if isinstance(base, str) else None)
    return text, ({"links": links} if links else {})
