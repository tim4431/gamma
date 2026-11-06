"""Gamma's Markdown and the Notion block model (``notion_blocks``).

``leaves_of`` reads a block's inline Markdown into rich text. It has its own
grammar: ``note_markup.INLINE_RE`` misreads ``***x***`` and ``*a **b** c*`` and
knows no ``<br>``, entity or mention, and changing it would move the PDF and
Zotero bytes. So the spans nothing inside of is markup, the ones
``LITERAL_SPANS`` starts, come out first, each swapped for a placeholder (as
``zotero_notes`` takes images out), and the marks are matched around them with
no need to know about a "*" in a formula or a "_" in an address.

``blocks_of`` reads one block's content into model blocks, of which it can hold
several: "p\\n\\n- a\\n- b" is three. ``from_gamma`` does that for a page's
``export_walk`` items, and settles what the walk leaves open (a block's kind,
its children, its [[refs]]). ``to_gamma`` is the way back, for a page read from
Notion: nodes for ``insert_page_tree``, in Markdown that reads as the model
does, so "$5", "<tag>" and "1. Intro" in a Notion paragraph stay what they are.
"""

import html as html_mod
import re
from dataclasses import replace
from datetime import datetime

from .markdown_import import _LIST_RE
from .note_markup import BARE_URL, LINK_TARGET, LITERAL_SPANS
from .notion_blocks import (_LIST_ITEMS, Block, _on_a_line, callout_color, callout_type, color_name, gamma_leaves,
                            merge_colors, style_color)
from .picture import is_picture, markdown_of
from .text_box import escape_markdown
from .zotero_notes import _ALT_WIDTH_RE, _UPLOAD_SRC_RE, _element_at, _url_md, css_style, markdown, title_text

# mentions.MENTION_RE, repeated since that module reaches the database and this
# one is pure; tests/shared/mentions.json holds this copy to it.
_NAME = r"[A-Za-z0-9_](?:[A-Za-z0-9_.-]*[A-Za-z0-9_])?"
_MENTION = (r"(?<![A-Za-z0-9_@./+\\-])@(?:"
            r"(?P<date>\d{4}-\d{2}-\d{2})(?: (?P<time>\d{2}:\d{2}))?"
            rf"(?: \(remind @{_NAME}\))?(?![A-Za-z0-9_:-])"
            rf"|(?!\d{{4}}-\d{{2}}-\d{{2}}){_NAME}(?![A-Za-z0-9_-]))")

# A target as LINK_TARGET reads it, or in the <...> that lets one hold a space.
_TARGET = r"<[^<>\n]*>|" + LINK_TARGET
# What comes out before the marks are read. A link's target goes without its
# label, whose marks are still to be read: "[" and "](x)" pair up later. A
# mention stays whole, a "_" at its end its own (the rendered view takes
# mentions out of the source first, so "_see @a_" is no italic on screen).
# A ref's name, an image's alt, a link's title and a \( \) are bounded: unbounded,
# each "[[" or "![" of a line with no "]", "](x y" with no ")" or "\(" with no
# "\)" scans to its end. INLINE_RE keeps its own \( \): PDF bytes depend on it.
_ATOM_RE = re.compile(
    LITERAL_SPANS.replace(r"\\\([^\n]+?", r"\\\([^\n]{1,1000}?")
    + r"|(?P<br><br[ \t]*/?>)"
    r"|(?P<entity>&#?[0-9A-Za-z]+;)"
    r"|(?P<embed>!\[\[[^\]\n]{1,256}\]\])"
    r"|(?P<ref>\[\[[^\]\n]{1,256}\]\])"
    r"|(?P<image>!\[(?P<alt>[^\]\n]{0,2000})\]\(\s*(?P<src>" + _TARGET + r")(?:[ \t][^)\n]{0,2000})?\)(?:\{:width\s+(?P<lw>\d+)\})?)"
    r"|(?P<target>\]\([ \t]*(?P<href>" + _TARGET + r")(?:[ \t][^)\n]{0,2000})?\))"
    r"|(?P<url>" + BARE_URL + ")"
    r"|(?P<mention>" + _MENTION + ")")
# A taken-out span in the text left behind; a link's target has its own opening.
_ATOM_AT = "\x00"
_TARGET_AT = "\x02"
_PLACE_RE = re.compile(f"[{_ATOM_AT}{_TARGET_AT}](\\d+)\x01")
# What in a target stands for another character.
_TARGET_CHAR_RE = re.compile(r"\\([!-/:-@\[-`{-~])|&#?[0-9A-Za-z]+;")
# What GFM leaves off the end of a bare address, so a sentence's full stop and
# a mark's closing stay out of it. BARE_URL keeps them: PDF and Zotero bytes
# depend on it.
_URL_TAIL = "!\"'*,.:;?_~"
# The marks, over text with no code, math or escape left in it. Italic goes
# before bold, so "***a** b*" is italic around a bold; a "**" closes on the
# last of a run of stars, so "**a *b***" is a bold around an italic. A run
# that goes on into a word may open a mark as it closes one, as the writer's
# "**a***b*" and "*a***b**" do, where what is closing holds no star of its own.
_MARK_RE = re.compile(
    r"(?P<underline><u>(?s:.+?)</u>)"
    r"|(?P<tag><(?P<name>sup|sub|kbd)>(?s:.+?)</(?P=name)>)"
    r'|(?P<color><span style="[^"<>]*">(?s:.*?)</span>)'
    r"|(?P<italic>\*(?!\s)(?:[^*]|\*\*(?=\S)[^*]+?(?<=\S)\*\*)+?(?<=\S)\*(?:(?!\*)|(?=\*\*\*?[^\s*]))"
    r"|(?<![\w_])_(?=\S)[^_]+?(?<=\S)_(?![\w_]))"
    r"|(?P<bold>\*\*(?=\S)(?:[^*]|\*(?=\S)[^*]+?(?<=\S)\*)+?(?<=\S)\*\*(?=\*[^\s*])"
    r"|\*\*(?=\S)(?s:.+?)(?<=\S)\*\*(?!\*)|(?<![\w_])__(?=\S)(?s:.+?)(?<=\S)__(?![\w_]))"
    r"|(?P<strike>~~(?=\S)(?s:.+?)(?<=\S)~~)"
    r"|(?P<mark>==(?=\S).+?(?<=\S)==)"
    rf"|(?P<link>\[(?:[^\[\]{_TARGET_AT}]|\[[^\[\]]*\])*{_TARGET_AT}\d+\x01)")
# Past this length a text's marks go unread: each delimiter left open makes
# _MARK_RE scan to the end, so the cost is the square of the length. The text
# stays as it is written, less what _ATOM_RE took out, and a warning says so
# if it holds what could have been a mark. A link still pairs, by _LONG_LINK_RE,
# which stops at the next bracket: its label can hold none.
_LONGEST_MARKED = 20_000
_MARK_CHARACTERS = frozenset("*_~=<")
_LONG_LINK_RE = re.compile(rf"\[([^\[\]{_TARGET_AT}]*){_TARGET_AT}(\d+)\x01")
# Each wrapping mark's delimiter length, and what it adds.
_WRAPS = {"bold": (2, {"bold": True}), "italic": (1, {"italic": True}), "strike": (2, {"strike": True})}


def _real_moment(day: str, time: str | None) -> bool:
    """An impossible date or time is no mention (``mentions.scan``)."""
    try:
        datetime.strptime(f"{day} {time or '00:00'}", "%Y-%m-%d %H:%M")
    except ValueError:
        return False
    return True


def _unescaped(text: str) -> str:
    """A target's or an alt's characters, its escapes and entities read."""
    return _TARGET_CHAR_RE.sub(lambda m: m.group(1) or html_mod.unescape(m.group()), text)


def _address(target: str) -> str:
    """A link's target or an image's source as the address it stands for: out
    of its <...>, its escapes and entities read, a space percent-encoded so
    that the writer's plain target holds it."""
    if target[:1] == "<" and target[-1:] == ">":
        target = target[1:-1]
    return _unescaped(target).replace(" ", "%20")


def _bare_address(m: re.Match) -> str:
    """The address a bare URL holds; the rest of ``m`` is text. A "==" at its
    end closes a ==mark== open on the line, and is the address's otherwise."""
    url = m.group().rstrip(_URL_TAIL)
    line = m.string[m.string.rfind("\n", 0, m.start()) + 1:m.start()]
    if url.endswith("==") and line.count("==") % 2:
        url = url[:-2].rstrip(_URL_TAIL)
    return url


def _atom(m: re.Match) -> tuple:
    """A span ``_ATOM_RE`` took out, as ``_add`` puts it back."""
    which = m.lastgroup
    body = m.group(which)
    if which == "code":
        return ("code", body.strip("`"))
    if which == "dmath":
        return ("math", body)
    if which == "math":
        return ("math", body if body[0] == "$" else f"${body[2:-2]}$")
    if which == "esc":
        return ("text", body[1])
    if which == "br":
        return ("br",)
    if which == "entity":
        return ("text", html_mod.unescape(body))
    if which in ("embed", "ref"):
        return ("raw", body)
    if which == "image":
        size = _ALT_WIDTH_RE.fullmatch(m.group("alt"))
        alt, width = size.groups() if size else (m.group("alt"), m.group("lw"))
        return ("image", _unescaped(alt), _address(m.group("src")), width)
    if which == "target":
        return ("target", _address(m.group("href")), body)
    if which == "url":
        return ("url", _bare_address(m))
    if m.group("date") and not _real_moment(m.group("date"), m.group("time")):
        return ("text", body)
    return ("mention", body)


def _add(out: list, kind: str, text: str, marks: dict):
    if kind == "text" and out and out[-1][0] == "text" and out[-1][2] == marks:
        out[-1] = ("text", out[-1][1] + text, marks)
    elif text:
        out.append((kind, text, marks))


def _add_atom(out: list, atom: tuple, marks: dict):
    """A taken-out span as a leaf. An image is one only on its way out of
    here, ``("image", (alt, src, width), marks)``: ``blocks_of`` makes it a
    block and ``_image_link`` anything that has no room for a block."""
    kind = atom[0]
    if kind == "br":
        out.append(atom)
    elif kind == "target":                       # "](y)" with no "[" to go with it
        _add(out, "text", "]", marks)
        _read_inline(out, atom[2][1:], marks, targets=False)
    elif kind == "image":
        out.append(("image", atom[1:], marks))
    elif kind == "url":
        _add(out, "text", atom[1], {"link": _url_md(atom[1]), **marks})
    elif kind == "mention":                      # a chip would break the link around it
        _add(out, "text" if "link" in marks else "raw", atom[1], marks)
    else:
        _add(out, kind, atom[1], marks)


def _add_loose(out: list, text: str, marks: dict, atoms: list):
    """Text with no mark in it: its placeholders put back, its line breaks read."""
    for i, part in enumerate(_PLACE_RE.split(text)):
        if i % 2:
            _add_atom(out, atoms[int(part)], marks)
            continue
        *lines, last = part.split("\n")
        for line in lines:                       # the spaces or backslash that make a hard break go
            _add(out, "text", line.removesuffix("\\").rstrip(" \t"), marks)
            out.append(("br",))
        _add(out, "text", last, marks)


def _read_long(text: str, marks: dict, atoms: list, out: list):
    """A text past ``_LONGEST_MARKED``: its links and nothing else."""
    pos = 0
    for m in _LONG_LINK_RE.finditer(text):
        _add_loose(out, text[pos:m.start()], marks, atoms)
        pos = m.end()
        href = atoms[int(m[2])][1]
        _add_loose(out, m[1] or href, {**marks, "link": href}, atoms)
    _add_loose(out, text[pos:], marks, atoms)


def _read(text: str, marks: dict, atoms: list, out: list):
    if len(text) > _LONGEST_MARKED:
        _read_long(text, marks if _MARK_CHARACTERS.isdisjoint(text) else {**marks, "marks_unread": True}, atoms, out)
        return
    pos = 0
    for m in _MARK_RE.finditer(text):
        _add_loose(out, text[pos:m.start()], marks, atoms)
        pos = m.end()
        which = m.lastgroup
        body = m.group(which)
        if which == "link":
            label, _, at = body[1:-1].rpartition(_TARGET_AT)
            href = atoms[int(at)][1]
            if label:
                _read(label, {**marks, "link": href}, atoms, out)
            else:
                _add(out, "text", href, {**marks, "link": href})
        elif which == "mark":
            _read(body[2:-2], {**marks, "color": merge_colors(marks.get("color"), "yellow_bg")}, atoms, out)
        elif which in _WRAPS:
            size, added = _WRAPS[which]
            _read(body[size:-size], {**marks, **added}, atoms, out)
        else:                                    # an HTML pair
            inner = body[body.index(">") + 1:body.rindex("<")]
            inside = marks
            if which == "underline":
                inside = {**marks, "underline": True}
            elif which == "color":
                color = merge_colors(marks.get("color"), style_color(css_style(body[13:body.index('">')])))
                inside = {**marks, "color": color} if color else marks
            _read(inner, inside, atoms, out)
    _add_loose(out, text[pos:], marks, atoms)


def _placed(text: str, atoms: list, targets: bool) -> str:
    """``text`` with each span ``_ATOM_RE`` finds in ``atoms`` and a placeholder
    where it was. A "](" with no "[" before it opens no target, which could
    hide a mark's closing, and what follows it is read on, as is the end of a
    bare address left out of it."""
    kept, pos, opened = [], 0, False
    while m := _ATOM_RE.search(text, pos):
        kept.append(text[pos:m.start()])
        opened = opened or "[" in kept[-1]
        pos = m.end()
        if m.lastgroup == "target" and not (targets and opened):
            kept.append("]")
            pos = m.start() + 1
            continue
        atoms.append(_atom(m))
        kept.append(f"{_TARGET_AT if m.lastgroup == 'target' else _ATOM_AT}{len(atoms) - 1}\x01")
        if m.lastgroup == "url":
            pos = m.start() + len(atoms[-1][1])
    kept.append(text[pos:])
    return "".join(kept)


def _read_inline(out: list, text: str, marks: dict, targets: bool = True):
    atoms = []
    _read(_placed(text, atoms, targets), marks, atoms, out)


def _rich(text: str) -> list:
    """Rich text with its images still in it."""
    out = []
    _read_inline(out, (text or "").translate({0: None, 1: None, 2: None}), {})
    return out


def _image_link(leaf: tuple) -> tuple:
    """An image where no block can stand, a table cell or a link's label: a
    link to itself. A link around it replaces that link, and a ``data:``
    address is no link, so in both the picture is lost: ``image_lost`` leaves
    ``_Mentions.lost_image`` to warn of it."""
    (alt, src, _), marks = leaf[1:]
    held = src.startswith("data:")
    if held or "link" in marks:
        return ("text", alt or ("image" if held else src), {**marks, "image_lost": True})
    return ("text", alt or src, {"link": src, **marks})


def leaves_of(text: str) -> list:
    """One block's inline Markdown → rich text (``notion_blocks``): literal
    text, with the marks on the leaves. ``<sup>``, ``<sub>`` and ``<kbd>`` keep
    their text and lose the tag, ``==x==`` is ``yellow_bg``, a colour span the
    hue it is or is nearest to; a [[ref]], an ![[embed]] and a mention stay as
    written, in a "raw" leaf; an image is a link to itself. A text past
    ``_LONGEST_MARKED`` keeps its marks as written, a link with a plain label
    aside, and ``marks_unread`` on its leaves, for ``_Mentions.long_text`` to
    warn of."""
    out = []
    for leaf in _rich(text):
        if leaf[0] == "br":
            out.append(leaf)
        else:
            _add(out, *(_image_link(leaf) if leaf[0] == "image" else leaf))
    return out


# --- one block's Markdown → model blocks ------------------------------------------

# How deep the text of one block nests lists and quotes; past it a line is text.
_DEEPEST = 32
_TASK_RE = re.compile(r"\[([ xX])\](?:[ \t]+|$)")
# The marker line of a callout (callouts.js CALLOUT_MARKER_RE).
_CALLOUT_RE = re.compile(r"\[!(\w+)\][-+]?[ \t]*")
# markdown_export's, whose ids are the editor's.
_BLOCK_REF_RE = re.compile(r"(!?)\[\[([A-Za-z0-9_-]+)\]\]")
_BLANK = " \t"
# A list marker with nothing after it, not even the space ``_LIST_RE`` wants.
_BARE_ITEM_RE = re.compile(r"[ \t]*(?:[-*+]|\d{1,4}[.)])")
# Notion's two languages of two words, one of them its name for none; any
# other fence's language is the first word of its info.
_SPACED_LANGUAGES = {"visual basic": "visual basic", "plain text": ""}
# storage.IMAGE_MEDIA_TYPES's, repeated since that module reaches the database
# and this one is pure.
_IMAGE_SUFFIXES = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg")


def _trim(rich: list) -> list:
    """Rich text with no line break or space on either edge."""
    rich = list(rich)
    for edge, strip in ((0, str.lstrip), (-1, str.rstrip)):
        while rich and (rich[edge][0] == "br" or rich[edge][0] == "text" and not rich[edge][1].strip(_BLANK)):
            rich.pop(edge)
        if rich and rich[edge][0] == "text":
            rich[edge] = ("text", strip(rich[edge][1], _BLANK), rich[edge][2])
    return rich


def _marked(rich: list, marks: dict) -> list:
    """Rich text with ``marks`` on every leaf, over any of the same name."""
    return [leaf if leaf[0] == "br" else (leaf[0], leaf[1], {**leaf[2], **marks}) for leaf in rich]


def _literal(text: str) -> list:
    """Text that is no Markdown as rich text, a line break a "br"."""
    out = []
    for i, line in enumerate(text.split("\n")):
        out += [("br",)] * bool(i) + [("text", line, {})] * bool(line)
    return out


def _paragraphs(text: str) -> list:
    """A paragraph's text as blocks: rich text holds no image, so one splits it."""
    blocks, rich = [], []

    def flush():
        kept = _trim(rich)
        rich.clear()
        if kept:
            blocks.append(Block("paragraph", kept))

    for leaf in _rich(text):
        if leaf[0] != "image":
            rich.append(leaf)
        elif "link" in leaf[2]:
            rich.append(_image_link(leaf))
        else:
            flush()
            alt, src, width = leaf[1]
            blocks.append(Block("image", _literal(alt), attrs={"url": src, **({"width": int(width)} if width else {})}))
    flush()
    return blocks


def _lead(line: str) -> int:
    """The columns a line is indented by (a tab counts 4, as in ``_dedent``)."""
    line = line.replace("\t", "    ")
    return len(line) - len(line.lstrip(" "))


def _dedented(line: str, col: int) -> str:
    """``line`` with up to ``col`` columns of its indent off (a tab counts 4)
    and the rest as it is, since it may be a line of a fence."""
    at, cut = 0, 0
    while at < len(line) and cut < col and line[at] in _BLANK:
        cut += 4 if line[at] == "\t" else 1
        at += 1
    return " " * max(cut - col, 0) + line[at:]


def _interrupts(lines: list, i: int) -> bool:
    """Whether line ``i`` ends the paragraph running into it. An item does,
    unless it is empty or numbered from past 1 (CommonMark's rule, for the
    line that starts "1986. A fine year")."""
    item = _LIST_RE.match(lines[i])
    if item and item[3].strip() and (not item[2][0].isdigit() or int(item[2][:-1]) == 1):
        return True
    return _element_at(lines, i) is not None


def _item_lines(lines: list, i: int, indent: int, col: int) -> tuple:
    """The lines after line ``i`` that belong to the list item it opens, the
    item's marker at ``indent`` and its text at ``col``, dedented; and the
    line after them. A line belongs at the item's text, after a blank line
    too; deeper than the marker, if it is an item; or lazily, a paragraph's
    next line with no indent."""
    kept, j = [], i + 1
    while j < len(lines):
        if not lines[j].strip():
            k = j
            while k < len(lines) and not lines[k].strip():
                k += 1
            if k == len(lines) or _lead(lines[k]) < col:
                break
            kept += [""] * (k - j)
            j = k
        lead, listed = _lead(lines[j]), _LIST_RE.match(lines[j])
        if lead >= col or listed and lead > indent:
            kept.append(_dedented(lines[j], col))
        elif listed or _element_at(lines, j):
            break
        else:
            kept.append(lines[j].strip())
        j += 1
    return kept, j


def _held(lines: list, depth: int) -> tuple:
    """What a list item, a quote or a callout holds: the rich text of the
    paragraph it opens with, and the blocks after that."""
    blocks = _flow(lines, depth + 1)
    return (blocks.pop(0).rich if blocks and blocks[0].type == "paragraph" else []), blocks


def _item(lines: list, i: int, depth: int) -> tuple:
    """The list item on line ``i`` as blocks, and the line after it. A bullet
    with no text of its own is a wrapper around what it holds, as in a vault's
    "- - x" and "- # H"."""
    raw = lines[i].replace("\t", "    ")
    m = _LIST_RE.match(raw)
    indent, marker, body = len(m[1]), m[2], m[3]
    rest, end = _item_lines(lines, i, indent, m.start(3) if body else indent + len(marker) + 1)
    kind, attrs = "bullet", {}
    task = _TASK_RE.match(body) if not marker[0].isdigit() else None
    if task:
        kind, attrs, body = "todo", {"checked": task[1] != " "}, body[task.end():]
    elif marker[0].isdigit():
        kind, attrs = "number", {"start": int(marker[:-1])}
    rich, kept = _held([body] + rest, depth)
    if kind == "bullet" and not rich and kept:
        return kept, end
    return [Block(kind, rich, kept, attrs)], end


def _quote(text: str, depth: int) -> Block:
    """A quote's text, "> " off: a callout when it opens with a marker. A
    title with no word character in it is the icon, the test
    ``_convert_asides`` uses; any other title is the text's bold first line."""
    first, _, rest = text.partition("\n")
    marker = _CALLOUT_RE.match(first)
    if not marker:
        return Block("quote", *_held(text.split("\n"), depth))
    rich, kept = _held(rest.split("\n"), depth)
    attrs = {"color": callout_color(marker[1].lower())}
    title = first[marker.end():].strip()
    if title and not re.search(r"\w", re.sub(r"<img\b[^>]*>", "", title)):
        attrs["icon"] = title
    elif title:
        rich = _marked(leaves_of(title), {"bold": True}) + ([("br",)] + rich if rich else [])
    return Block("callout", rich, kept, attrs)


def _table(rows: list) -> Block:
    """A GFM table's rows. Its header row is empty when Notion's table had none."""
    header = any(rows[0])
    cells = [[leaves_of(cell) for cell in row] for row in rows[0 if header else 1:]]
    return Block("table", attrs={"rows": cells, "header": header})


def _element(element: tuple, opening: str, depth: int) -> Block:
    """``zotero_notes._element_at``'s element as a block; ``opening`` is its
    first line, where a fence keeps the language ``_element_at`` leaves out."""
    kind = element[0]
    if kind == "code":
        info = opening.strip().lstrip("`~").split()
        language = _SPACED_LANGUAGES.get(" ".join(info).lower(), info[0] if info else "")
        # A fence under a list's marker in a quote's text: its indent is on every line.
        text = "\n".join(_dedented(line, _lead(opening)) for line in element[1].split("\n"))
        return Block("code", attrs={"text": text, **({"language": language} if language else {})})
    if kind == "math":
        return Block("equation", attrs={"text": element[1][2:-2].strip()})
    if kind == "heading":
        return Block("heading", leaves_of(element[2].strip()), attrs={"level": element[1]})
    if kind == "quote":
        return _quote(element[1], depth)
    return _table(element[1]) if kind == "table" else Block("divider")


def _flow(lines: list, depth: int) -> list:
    """Lines of a block's text as blocks. Lines of text are a paragraph, a
    "\\n" in it a "br"; a blank line or another element ends it. A marker
    alone on the last line is an empty item, as on screen: the strip a block's
    text has been through leaves "- " a "-"."""
    if depth >= _DEEPEST:
        return _paragraphs("\n".join(lines))
    if _BARE_ITEM_RE.fullmatch(lines[-1]):
        lines = [*lines[:-1], lines[-1] + " "]
    out, i = [], 0
    while i < len(lines):
        found = _element_at(lines, i)
        if found:
            out.append(_element(found[0], lines[i], depth))
            i = found[1]
        elif not lines[i].strip():
            i += 1
        elif _LIST_RE.match(lines[i]):
            blocks, i = _item(lines, i, depth)
            out += blocks
        else:
            j = i + 1
            while j < len(lines) and lines[j].strip() and not _interrupts(lines, j):
                j += 1
            out += _paragraphs("\n".join(line.lstrip(_BLANK) for line in lines[i:j]))
            i = j
    return out


def _lone(block: Block) -> Block:
    """A paragraph that is nothing but a [[ref]] or a link to an upload is
    the page or the file it stands for. In a list or a quote the same text
    stays text."""
    leaf = block.rich[0] if block.type == "paragraph" and len(block.rich) == 1 else ("br",)
    if leaf[0] == "raw" and not leaf[2] and (ref := _BLOCK_REF_RE.fullmatch(leaf[1])) and not ref[1]:
        return Block("page", attrs={"ref": ref[2]})
    if leaf[0] == "text" and list(leaf[2]) == ["link"] and _UPLOAD_SRC_RE.search(leaf[2]["link"]):
        return Block("file", [("text", leaf[1], {})], attrs={"url": leaf[2]["link"]})
    return block


def blocks_of(content: str) -> list:
    """One block's content → the model blocks it holds, in order. Lists nest
    by their indent, a number keeps its ``start``; an image in text splits
    the paragraph around it. Links stay as they are: ``from_gamma`` resolves
    them."""
    lines = (content or "").replace("\r\n", "\n").replace("\r", "\n").split("\n")
    return [_lone(block) for block in _flow(lines, 0)]


# --- a page's items → model blocks ------------------------------------------------

# How much of a line a label reads: title_text costs the square of the length
# on delimiters left open, and a label keeps 80 characters.
_LONGEST_LABEL = 2000
# What opens a block's line and says nothing: list, quote, to-do, callout.
_LINE_LEAD_RE = re.compile(r"\A(?:[ \t]*(?:>|[-*+]|\d{1,4}[.)]|\[[ xX]\]|\[!\w+\][-+]?)(?:[ \t]+|$))+")
_WORD_RE = re.compile(r"[^\W_]")


def _label(text: str | None, fallback: str) -> str:
    """The first line of a target's content or a title, as a link's label."""
    return title_text((text or "").strip().split("\n")[0][:_LONGEST_LABEL])[:80] or fallback


def _mention_label(ref: dict, block_id: str) -> str:
    """What a mention of a block shows: its first line, without what opens the
    line or a [[ref]] in it, and if that holds no words, as a code block's or
    a formula's does, its page's title. A page's content is its title,
    markers and all."""
    line = (ref.get("content") or "").strip().split("\n")[0]
    if ref.get("page_id") != block_id:
        line = _BLOCK_REF_RE.sub("", _LINE_LEAD_RE.sub("", line))
    label = _label(line, "")
    return label if _WORD_RE.search(label) else _label(ref.get("page_title"), "Untitled")


class _Mentions:
    """A page's [[ref]]s and ![[embed]]s, read as ``resolve_block_links`` reads
    them, but in the rich text, where a fence holds none: a mention is the
    first line of what it names, linked when its page is in the export and
    not this one; an embed is what it names, with a line saying where from.
    In an embed, a further embed is a mention, so nothing can recurse. A ref
    no one knows stays as typed."""

    def __init__(self, links, page_id: str | None):
        self.links, self.page_id = links, page_id
        self.assets = {}

    def path(self, page_id: str | None) -> str | None:
        return self.links.page(page_id) if page_id and page_id != self.page_id else None

    def embed(self, ref: dict) -> tuple:
        """What an embed shows: its Markdown, and the leaves of its "(from …)"."""
        title = _label(ref.get("page_title"), "source")
        path = self.path(ref.get("page_id"))
        content = ref["content"].strip()
        if path:
            return content, [("text", "(from ", {"italic": True}), ("text", title, {"italic": True, "link": path}),
                             ("text", ")", {"italic": True})]
        if ref.get("page_id") != self.page_id and (ref.get("page_title") or "").strip():
            return content, [("text", f"(from {title})", {"italic": True})]
        return content, []

    def leaf(self, leaf: tuple, nested: bool) -> list:
        ref_at = _BLOCK_REF_RE.fullmatch(leaf[1])
        ref = self.links.ref(ref_at[2]) if ref_at else None
        if not ref:
            return [("text", leaf[1], leaf[2])]
        if ref_at[1] and not nested and (ref.get("content") or "").strip():
            content, source = self.embed(ref)
            shown = self.leaves(leaves_of(content), True) + [("text", " ", {})] * bool(source) + source
            return _marked(shown, leaf[2])
        path = self.path(ref.get("page_id"))
        return [("text", _mention_label(ref, ref_at[2]), {**leaf[2], "link": path} if path else leaf[2])]

    def lost_image(self, leaf: tuple) -> tuple:
        if leaf[0] == "br" or "image_lost" not in leaf[2]:
            return leaf
        self.links.warn(leaf[1], "image left out")
        return (leaf[0], leaf[1], {key: value for key, value in leaf[2].items() if key != "image_lost"})

    def long_text(self, leaf: tuple) -> tuple:
        if leaf[0] == "br" or "marks_unread" not in leaf[2]:
            return leaf
        self.links.warn("Long text", f"over {_LONGEST_MARKED:,} characters, so its marks stay as typed")
        return (leaf[0], leaf[1], {key: value for key, value in leaf[2].items() if key != "marks_unread"})

    def upload(self, leaf: tuple) -> tuple:
        """A leaf linking to an upload, which has no file block to stand as:
        the link goes where ``links.asset`` says, asked once a page per file.
        With nowhere to go a web address stays a link and any other is its
        words, with a warning, as ``_Writer.left_out`` has a file block."""
        upload = _UPLOAD_SRC_RE.search(leaf[2].get("link", "")) if leaf[0] != "br" else None
        if not upload:
            return leaf
        href = leaf[2]["link"]
        if href not in self.assets:
            kind = "image" if upload[1].lower().endswith(_IMAGE_SUFFIXES) else "file"
            self.assets[href] = self.links.asset(href, kind)
            if self.assets[href] is None:
                self.links.warn(leaf[1], f"{kind} left out")
        url = self.assets[href] or (href if re.match(r"https?://", href) else None)
        marks = {key: value for key, value in leaf[2].items() if key != "link"}
        return (leaf[0], leaf[1], {**marks, "link": url} if url else marks)

    def leaves(self, rich: list, nested: bool = False) -> list:
        """Rich text with its refs resolved and its uploads settled. A mention
        of a person or a date stays as it is."""
        out = []
        for leaf in rich:
            out += self.leaf(leaf, nested) if leaf[0] == "raw" and leaf[1][0] != "@" else [leaf]
        return [self.upload(self.long_text(self.lost_image(leaf))) for leaf in out]

    def lone_embed(self, block: Block) -> list | None:
        """A paragraph that is nothing but an embed: the blocks it shows, for
        a list or a formula reads as itself only as blocks."""
        leaf = block.rich[0] if len(block.rich) == 1 else ("br",)
        ref_at = _BLOCK_REF_RE.fullmatch(leaf[1]) if leaf[0] == "raw" and not leaf[2] else None
        ref = self.links.ref(ref_at[2]) if ref_at and ref_at[1] else None
        if not ref or not (ref.get("content") or "").strip():
            return None
        content, source = self.embed(ref)
        blocks = self.blocks(blocks_of(content), True)
        if source and blocks and blocks[-1].type == "paragraph":
            blocks[-1].rich += [("text", " ", {}), *source]
        elif source:
            blocks.append(Block("paragraph", source))
        return blocks

    def page(self, block: Block) -> Block:
        """A line that is nothing but a [[ref]]: a block mention is a link to
        its page, and with no such page the line is its label."""
        ref = self.links.ref(block.attrs["ref"])
        if not ref:
            return Block("paragraph", [("text", f"[[{block.attrs['ref']}]]", {})])
        title = [("text", _mention_label(ref, block.attrs["ref"]), {})]
        path = self.path(ref.get("page_id"))
        return Block("page", title, attrs={"ref": ref["page_id"], "url": path}) if path else Block("paragraph", title)

    def blocks(self, blocks: list, nested: bool = False) -> list:
        out = []
        for block in blocks:
            embedded = self.lone_embed(block) if block.type == "paragraph" and not nested else None
            if embedded is not None:
                out += embedded
            elif block.type == "page" and "ref" in block.attrs:
                out.append(self.page(block))
            else:
                block.rich = self.leaves(block.rich, nested)
                if block.type == "table":
                    block.attrs["rows"] = [[self.leaves(cell, nested) for cell in row] for row in block.attrs["rows"]]
                block.children = self.blocks(block.children, nested)
                out.append(block)
        return out


def _page_line(page_no: int) -> tuple:
    return ("text", f"p. {page_no}", {})


def _highlight(item) -> list:
    """The quote, a highlight's page line under it, then the writing on it."""
    if not item.quote:
        return blocks_of(item.text)
    page = [("br",), _page_line(item.page_no)] if item.page_no else []
    return [Block("quote", _literal(item.quote) + page), *blocks_of(item.text)]


def _target(item, mentions: _Mentions) -> str | None:
    """Where a link region goes, if that is still anywhere."""
    return mentions.path(item.props.get("link_page_id")) or item.props.get("link_url")


def _unlinked(item):
    """A link region with no target left as what it is without the link, by
    the walk's rules. What they find nothing in is a note with nothing in it."""
    from .export_walk import _kind, _page_no  # local: that module reaches the database
    props = {key: value for key, value in item.props.items() if key not in ("link_page_id", "link_url")}
    picture = is_picture(props) and markdown_of(item.node.get("content"), props["picture"])
    kind = _kind(props, item.text, item.quote, picture)
    return replace(item, kind=kind or "note", props=props, page_no=kind and _page_no(kind, props, item.on_sheet))


def _region(item, mentions: _Mentions) -> list:
    """A link region as a linked line."""
    target = item.props.get("link_page_id")
    href = _target(item, mentions)
    label = leaves_of(item.text) or _literal(item.quote)
    if not label and target:
        ref = mentions.links.ref(target) or {}
        label = _literal(_label(ref.get("content"), ""))
    return [Block("paragraph", _marked(label or _literal(href), {"link": href}))]


def _ink(item) -> list:
    """A handwriting group: its picture, which the export renders under the
    ink's own stem (``markdown_export.ink_svg_name``), and the caption."""
    label = f"Handwriting (p. {item.page_no})" if item.page_no else "Handwriting"
    url = re.sub(r"\.ink$", ".svg", item.props["ink_url"])
    return [Block("image", _literal(label), attrs={"url": url}), *blocks_of(item.text)]


def _recording(item) -> list:
    """Its length in bold, an audio file per segment, then its title."""
    from .recording import heading, segments_of  # local: that module reaches the database
    urls = [segment["url"] for segment in segments_of(item.props)]
    names = ["audio"] if len(urls) == 1 else [f"part {i}" for i in range(1, len(urls) + 1)]
    return [Block("paragraph", [("text", heading(item.props), {"bold": True})]),
            *(Block("file", _literal(name), attrs={"url": url, "media": "audio"}) for name, url in zip(names, urls)),
            *blocks_of(item.text)]


def _picture(item) -> list:
    blocks = blocks_of(markdown_of(item.node.get("content"), item.props["picture"]))
    return blocks + [Block("paragraph", [_page_line(item.page_no)])] * bool(item.page_no)


def _note(item) -> list:
    """A note's blocks; a text box's page line ends its last paragraph."""
    blocks = blocks_of(item.text)
    if item.page_no and blocks and blocks[-1].type == "paragraph":
        blocks[-1].rich += [("br",), _page_line(item.page_no)]
    elif item.page_no:
        blocks.append(Block("paragraph", [_page_line(item.page_no)]))
    return blocks


_HOLDERS = {"paragraph", "bullet", "number", "todo", "quote", "callout", "toggle"}
_KINDS = {"highlight": _highlight, "ink": _ink, "recording": _recording, "picture": _picture, "note": _note}


def _item_blocks(item, mentions: _Mentions) -> list:
    """An item and its children. Children go to the last block that can hold
    them and otherwise follow it: under a heading, which undoes
    ``md_to_blocks``' nesting, they are siblings as they are in Notion. What
    is folded is a toggle: a note's paragraph or heading with children, and
    only that, so an open one or a folded list item exports as what it is."""
    if item.kind == "region" and not _target(item, mentions):
        item = _unlinked(item)
    blocks = mentions.blocks(_region(item, mentions) if item.kind == "region" else _KINDS[item.kind](item))
    if blocks:
        blocks[0].id = item.node.get("id")
    kids = [block for child in item.children for block in _item_blocks(child, mentions)]
    if not kids or not blocks:
        return blocks + kids
    last = blocks[-1]
    if item.kind == "note" and item.props.get("collapsed") and last.type in ("paragraph", "heading"):
        level = {"heading": last.attrs["level"]} if last.type == "heading" else {}
        blocks[-1] = Block("toggle", last.rich, kids, level, last.id)
    elif last.type in _HOLDERS and item.kind != "picture":
        last.children += kids
    else:
        blocks += kids
    return blocks


def from_gamma(items: list, links, page_id: str | None = None) -> list:
    """A page's items (``export_walk.walk``) → its model blocks. ``links`` is
    a ``notion_nfm.Links`` that also has ``ref(block_id)``, the call of
    ``markdown_export.block_ref_resolver``, whose content and titles a mention
    and an embed show. Image and file blocks keep their addresses, for the
    writer to ask ``links.asset``; a link to an upload in text, which the
    writer takes as any link, asks here (``_Mentions.upload``). ``page_id`` is
    the page's own, so a mention of it links nowhere."""
    mentions = _Mentions(links, page_id)
    return [block for item in items for block in _item_blocks(item, mentions)]


# --- model blocks → a page's nodes -----------------------------------------------

def _node(content: str, children=(), properties: dict | None = None) -> dict:
    return {"content": content, "properties": properties or {}, "children": list(children)}


def _written(rich: list, color=None) -> str:
    """Rich text as Markdown that reads as it: literal text escaped, each
    link's target in the form a target holds, and a block's colour, which
    Gamma keeps on the runs, under the runs' own."""
    leaves = []
    for leaf in rich:
        if leaf[0] == "br":
            leaves.append(leaf)
            continue
        marks = {**leaf[2], "color": merge_colors(color, leaf[2].get("color"))}
        if "link" in marks:
            marks["link"] = _url_md(marks["link"])
        leaves.append((leaf[0], leaf[1], marks))
    return markdown(gamma_leaves(leaves), collapse=False, mentions=True)


def _text(block: Block) -> str:
    return _written(block.rich, None if block.type == "callout" else color_name(block.attrs.get("color")))


def _line(rich: list, color=None) -> str:
    return _written(_on_a_line(rich), color)


def _fence(attrs: dict) -> str:
    """A code block, in a fence longer than any fence in it (``_pre_md``)."""
    text = attrs.get("text") or ""
    runs = [len(m[1]) for m in re.finditer(r"^[ \t]{0,3}(`{3,})", text, re.M)]
    fence = "`" * max([3] + [n + 1 for n in runs])
    return f"{fence}{attrs.get('language') or ''}\n{text}\n{fence}"


def _table_md(attrs: dict) -> str:
    """A GFM table, every row as wide as the widest; one with no header row
    has an empty one, since GFM cannot go without."""
    rows = [[_line(cell).replace("|", "\\|") for cell in row] for row in attrs.get("rows") or []]
    if not rows:
        return ""
    width = max(len(row) for row in rows)
    rows = [row + [""] * (width - len(row)) for row in rows]
    head = rows[0] if attrs.get("header") else [""] * width
    return "\n".join("| " + " | ".join(row) + " |"
                     for row in [head, ["---"] * width] + rows[1 if attrs.get("header") else 0:])


def _image_md(block: Block) -> str:
    """``![caption|W](src)``, the caption on one line, without the brackets
    no alt can hold and escaped, as ``picture.markdown_of`` writes one."""
    url = block.attrs.get("url")
    if not url:
        return _text(block)
    alt = "".join(" " if leaf[0] == "br" else leaf[1] for leaf in block.rich)
    alt = " ".join(re.sub(r"[\[\]]", "", alt).split())
    if _ALT_WIDTH_RE.fullmatch(alt):             # an alt ending "|300" is no width
        alt = alt.replace("|", " ")
    width = block.attrs.get("width")
    return f"![{escape_markdown(alt)}{f'|{round(width)}' if width else ''}]({_url_md(url)})"


def _linked(block: Block, url: str | None) -> str:
    """A file's or a page's name, as a link when it has somewhere to go, as
    text when not: never a link to nowhere."""
    rich = block.rich or _literal(url or "")
    return _written(_marked(rich, {"link": url}) if url else rich)


def _item_md(block: Block) -> str:
    """A list block's marker and text, its further lines at the text's column."""
    if block.type == "number":
        marker = f"{block.attrs.get('start', 1)}. "
    elif block.type == "todo":
        marker = f"- [{'x' if block.attrs.get('checked') else ' '}] "
    else:
        marker = "- "
    return marker + _text(block).replace("\n", "\n" + " " * (2 if block.type == "todo" else len(marker)))


def _content(block: Block, refs: dict) -> str:
    """The content of a block with no quote lines or marker of its own."""
    kind, attrs = block.type, block.attrs
    if kind == "code":
        return _fence(attrs)
    if kind == "equation":
        return f"$$\n{attrs.get('text') or ''}\n$$"
    if kind == "divider":
        return "---"
    if kind == "table":
        return _table_md(attrs)
    if kind == "image":
        return _image_md(block)
    if kind == "file":
        return _linked(block, attrs.get("url"))
    if kind == "page":
        ref = attrs.get("ref")
        return f"[[{ref}]]" if ref else _linked(block, refs.get(attrs.get("database")) or attrs.get("url"))
    if kind == "unknown" and not block.rich:
        return _written(_literal(attrs.get("raw") or attrs.get("text") or ""))
    return _text(block)


def _quote_md(block: Block) -> tuple:
    """A quote's or callout's content, and the children it leaves blocks.
    Its first children, while they are paragraphs and list items with none of
    their own, go in as ">" lines, as Notion's own text does."""
    text, kept = _text(block), list(block.children)
    lines, listed = text.split("\n") if text else [], False
    while kept and kept[0].type in ("paragraph", *_LIST_ITEMS) and not kept[0].children:
        child = kept.pop(0)
        item = child.type != "paragraph"
        child_text = _item_md(child) if item else _text(child)
        if child_text:
            lines += [""] * bool(lines and not (listed and item)) + child_text.split("\n")
            listed = item
    quoted = [f"> {line}" if line else ">" for line in lines]
    if block.type == "callout":
        icon = block.attrs.get("icon") or ""
        quoted.insert(0, f"> [!{callout_type(block.attrs.get('color'))}] {icon}".rstrip())
    return "\n".join(quoted), kept


def _nodes_of(block: Block, refs: dict) -> list:
    """A block as nodes: one and its children, or its children alone when it
    has nothing to show, as ``md_to_blocks`` drops an empty paragraph."""
    kind, attrs = block.type, block.attrs
    kids = block.children
    if kind in ("quote", "callout"):
        content, kids = _quote_md(block)
    elif kind in _LIST_ITEMS:
        return [_node(_item_md(block), _nodes(kids, refs))]
    elif kind == "heading" or kind == "toggle" and "heading" in attrs:
        level = attrs["level"] if kind == "heading" else attrs["heading"]
        content = "#" * min(max(level, 1), 6) + " " + _line(block.rich, color_name(attrs.get("color")))
        content = content if block.rich else ""
    else:
        content = _content(block, refs)
    kids = _nodes(kids, refs)
    if not content:
        return kids
    if kind == "toggle" and kids:
        return [_node(content, kids, {"collapsed": True})]
    if kind in _HOLDERS:
        return [_node(content, kids)]
    return [_node(content)] + kids


def _nodes(blocks: list, refs: dict) -> list:
    out = []
    for block in blocks:
        out += _nodes_of(block, refs)
    return out


def to_gamma(page, refs: dict | None = None) -> list:
    """A model page's blocks → ``{content, properties, children}`` nodes for
    ``insert_page_tree``, through the zip's ``_prepare_tree``. Text goes
    through the same writer as a Zotero note's, with no whitespace collapsed
    and "@name" kept literal. A heading is flat, its children after it; a
    toggle is a folded block with children, and with none a plain one. A
    ``page`` block is its [[ref]], or a link to what ``refs`` (database id →
    target) or its own ``url`` names: ``rewrite_links`` takes it from there."""
    return _nodes(page.blocks, refs or {})
