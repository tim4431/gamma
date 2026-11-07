"""The model every Notion route meets in, and Notion's colours in Gamma's terms.

``Block`` and ``Page`` sit between Gamma's Markdown (``notion_gamma``) and what
Notion reads and writes. Text in the model is literal, as rich text: escaping
happens only at the edges, and images are blocks, never rich text, so a page
cannot hold what Notion's API refuses.

Notion colours a run by name: ``red`` is its text colour, ``red_background``
its background. Gamma colours a run with the span the editor writes
(mdMarks.js): a swatch of its palette, or that swatch's tint. Notion's palette
is Gamma's but for brown, which has no swatch and keeps the export's own value,
and ``yellow_background``, which is Gamma's ==mark==. The model's names are
Notion's short ones, ``red`` and ``red_bg``; a run with both colours holds the
pair, which only Gamma can show. tests/shared/notioncolors.json pins all of
this, and frontend/tests/notionColors.test.mjs holds it to mdMarks.js.

``lower_for_file`` takes a model down to what Notion's file importer can be
expected to read. ``CAPS`` has a row for each construct in doubt, so what a
probe finds changes one row: the block code stays as it is.
"""

from dataclasses import dataclass, field, replace

from .colors import nearest, parse_color


# rich text: a list of zotero_notes leaves ("text"|"code"|"math", s, marks), ("br",),
# plus ("raw", markdown, marks) for a page or date already rendered. A "math" s
# keeps its dollars, as a zotero_notes leaf does.
# marks: bold italic strike underline: True; link: url; color: "red"|"red_bg"|("red", "blue_bg")
@dataclass
class Block:
    type: str          # paragraph heading bullet number todo toggle quote callout
                       # code equation divider table image file page unknown
    rich: list = field(default_factory=list)
    children: list = field(default_factory=list)
    attrs: dict = field(default_factory=dict)  # level heading checked language text icon color
                                               # url media rows header ref database raw width
    id: str | None = None          # Gamma id


@dataclass
class Page:
    title: str
    blocks: list
    gamma_id: str | None = None
    notion_id: str | None = None


# mdMarks.js TEXT_COLORS, and the alpha of its tint().
TEXT_COLORS = {"red": "#e5484d", "orange": "#f76b15", "yellow": "#d4a017", "green": "#30a46c",
               "blue": "#3b82f6", "purple": "#8e4ec6", "pink": "#e93d82", "gray": "#8b8d98"}
BROWN = "#9f765a"
TINT = "55"

_HUES = {**TEXT_COLORS, "brown": BROWN}
_HUE_OF = {value: hue for hue, value in _HUES.items()}
# "teal" in the export's CSS is Notion's green.
_ALIASES = {"teal": "green", "grey": "gray"}

# A callout's type by its colour, and back. Several colours share a type, so
# the way back picks one; gray, Notion's default, never reads as a choice.
_CALLOUT_TYPES = {"gray": "info", "brown": "info", "red": "danger", "orange": "warning", "yellow": "warning",
                  "green": "tip", "blue": "note", "purple": "important", "pink": "important"}
_CALLOUT_COLORS = {"note": "blue_bg", "info": "gray_bg", "tip": "green_bg", "warning": "yellow_bg",
                   "danger": "red_bg", "important": "purple_bg"}


def color_name(name: str | None) -> str | None:
    """A colour as Notion names it (``red``, ``teal_background``, ``red_bg``)
    → the model's, None for what means nothing (``default``, ``default_background``)."""
    hue, _, ground = (name or "").strip().lower().partition("_")
    hue = _ALIASES.get(hue, hue)
    if hue not in _HUES or ground not in ("", "bg", "background"):
        return None
    return f"{hue}_bg" if ground else hue


def merge_colors(outer, inner):
    """The colour of a run inside another: a text colour and a background make
    a pair, and of two the same kind the inner wins."""
    text, background = None, None
    for color in (outer, inner):
        for one in color if isinstance(color, tuple) else (color,):
            if one and one.endswith("_bg"):
                background = one
            elif one:
                text = one
    return (text, background) if text and background else text or background


def _hue(value: str | None) -> str | None:
    """A style's colour value → the hue it is or is nearest to, alpha ignored.
    One with no hue in it is gray, or nothing near black and near white, the
    page's own ink and paper."""
    value = _ALIASES.get(value, value)
    if value in _HUES:
        return value
    rgb = (parse_color(value) or (0, 1, 0))[:3]
    if max(rgb) - min(rgb) < .1:
        return "gray" if .2 < max(rgb) < .9 else None
    return _HUE_OF.get(nearest(value, _HUE_OF))


def style_color(style: dict):
    """A run's style (``zotero_notes.css_style``) → the model's colour, or None
    for a value no hue can stand for."""
    text, background = _hue(style.get("color")), _hue(style.get("background"))
    return merge_colors(text, f"{background}_bg" if background else None)


def gamma_marks(marks: dict) -> dict:
    """A leaf's marks as ``zotero_notes.markdown`` takes them: a colour is the
    span's style pairs, in the order mdMarks.js writes them, a lone
    ``yellow_bg`` the ==mark==."""
    color = marks.get("color")
    out = {k: v for k, v in marks.items() if k != "color"}
    if color == "yellow_bg":
        out["mark"] = True
    elif color:
        pairs = []
        for one in color if isinstance(color, tuple) else (color,):
            hue = _HUES[one.removesuffix("_bg")]
            pairs.append(("background", hue + TINT) if one.endswith("_bg") else ("color", hue))
        out["color"] = tuple(pairs)
    return out


def gamma_leaves(leaves: list) -> list:
    """Rich text as ``zotero_notes.markdown`` takes it."""
    return [leaf if leaf[0] == "br" else (leaf[0], leaf[1], gamma_marks(leaf[2])) for leaf in leaves]


def callout_type(color: str | None) -> str:
    """A callout's colour, as Notion names it → Gamma's callout type."""
    return _CALLOUT_TYPES.get((color_name(color) or "").removesuffix("_bg"), "info")


def callout_color(kind: str) -> str:
    """A Gamma callout type → the colour it goes out as."""
    return _CALLOUT_COLORS.get(kind, "gray_bg")


# --- the file route ---------------------------------------------------------------

# What Notion's file importer takes, one row a construct. No probe has come
# back (docs/research/notion-roundtrip.md), so each row holds the safe form:
# False lowers the construct, True keeps it for the writer, which writes
# Notion's own tag. Underline stays on: Notion's Markdown export writes <u>.
CAPS = {
    "callout": False,             # else a quote opening with its icon
    "toggle": False,              # else a bullet
    "toggle_heading": False,      # else the heading, its children after it
    "paragraph_children": False,  # else a bullet, and so is a paragraph in a list
    "heading_level": 3,           # the help page: "H4 and below as H3"
    "color": False,               # else a background is bold and a text colour goes
    "underline": True,            # else it goes
    "date": False,                # else its @… text
    "page": False,                # else a link line
    "headerless_table": False,    # else an empty header row, as Gamma's own tables have
    "hard_break": "  \n",         # a line break inside a block
    "list_indent": 4,             # Notion's own
    "escape": True,               # else text goes as it is, as Notion's export writes it
}

_LIST_ITEMS = ("bullet", "number", "todo")
# Notion's default, for a callout with none: a quote with no icon is no callout.
_CALLOUT_ICON = "💡"


def _on_a_line(rich: list) -> list:
    """Rich text where a line break cannot stand: a heading, a table's cell."""
    return [("text", " ", {}) if leaf[0] == "br" else (leaf[0], leaf[1].replace("\n", " "), leaf[2])
            for leaf in rich]


def _lower_leaf(leaf: tuple, caps: dict) -> tuple:
    if leaf[0] == "br":
        return leaf
    kind, text, marks = leaf[0], leaf[1], dict(leaf[2])
    color = marks.pop("color", None) if not caps["color"] else None
    if any(one.endswith("_bg") for one in (color if isinstance(color, tuple) else (color or "",))):
        marks["bold"] = True
    if not caps["underline"]:
        marks.pop("underline", None)
    return ("text" if kind == "raw" and text[:1] == "@" and not caps["date"] else kind, text, marks)


def _lower_table(attrs: dict, caps: dict) -> dict:
    rows = [[[_lower_leaf(leaf, caps) for leaf in _on_a_line(cell)] for cell in row] for row in attrs.get("rows") or []]
    header = attrs.get("header", True)
    if rows and not header and not caps["headerless_table"]:
        rows, header = [[[] for _ in max(rows, key=len)], *rows], True
    return {**attrs, "rows": rows, "header": header}


def _callout_icon(attrs: dict) -> str:
    """An icon that is a picture (``<img>``, Notion's own icons) cannot go as text."""
    icon = attrs.get("icon")
    return icon if icon and "<" not in icon else _CALLOUT_ICON


def _lower(block: Block, caps: dict) -> list:
    kind, attrs = block.type, dict(block.attrs)
    rich, kids = [_lower_leaf(leaf, caps) for leaf in block.rich], lower_for_file(block.children, caps)
    if not caps["color"]:
        attrs.pop("color", None)
    if kind == "toggle" and "heading" in attrs and not (kids and caps["toggle_heading"]):
        kind, attrs = "heading", {"level": attrs["heading"]}
    elif kind == "toggle" and not kids:          # no children, no toggle
        kind = "paragraph"
    elif kind == "toggle" and "heading" not in attrs and not caps["toggle"]:
        kind = "bullet"
    elif kind == "paragraph" and kids and not caps["paragraph_children"]:
        kind = "bullet"
    elif kind == "callout" and not caps["callout"]:
        icon = _callout_icon(attrs)
        kind, rich, attrs = "quote", [("text", f"{icon} " if rich else icon, {}), *rich], {}
    elif kind == "page" and not caps["page"]:
        url = attrs.get("url")
        rich = rich or ([("text", url, {})] if url else [])
        kind, attrs = "paragraph", {}
        rich = [leaf if leaf[0] == "br" or not url else (leaf[0], leaf[1], {**leaf[2], "link": url}) for leaf in rich]
    elif kind == "table":
        attrs = _lower_table(attrs, caps)
    if kind in _LIST_ITEMS and not caps["paragraph_children"]:
        kids = [replace(kid, type="bullet") if kid.type == "paragraph" else kid for kid in kids]
    if kind == "heading":                        # a heading holds no children: they follow it
        attrs["level"] = min(attrs.get("level", 1), caps["heading_level"])
        return [replace(block, type=kind, rich=_on_a_line(rich), children=[], attrs=attrs), *kids]
    return [replace(block, type=kind, rich=rich, children=kids, attrs=attrs)]


def lower_for_file(blocks: list, caps: dict = CAPS) -> list:
    """Model blocks → the model blocks ``notion_nfm.write(form="file")`` is to
    write: each construct in ``CAPS`` turned off becomes what stands in for
    it. New blocks: the ones given stay as they are."""
    return [lowered for block in blocks for lowered in _lower(block, caps)]
