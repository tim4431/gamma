"""Convert a plain Markdown document into Gamma's nested note blocks.

Used by ``POST /api/import/markdown`` (uploading a .md file, alone or inside a
folder upload, turns it into a note page) and by the zip importer
(``markdown_zip_import.py``). The parser is deliberately shallow: block
contents keep their raw markdown — the block editor renders headings, math,
callouts etc. live — so all that matters here is how the document splits into
blocks and how they nest:

- headings nest by level (## under #, …) and keep their ``#`` markup;
- list items nest by indentation under the nearest heading, bullet markers are
  dropped (every block already renders as a bullet), numbered markers stay;
- lines indented under a list item continue that item: text directly below
  it (or, after a blank line, text aligned with the item's own text column)
  joins the item's content — which is exactly how Gamma's Markdown export
  writes a multi-line block — while text indented deeper after a blank line
  becomes a child block (Notion's toggles export that way);
- everything else groups into one block per paragraph (consecutive non-blank
  lines — which also keeps quote runs and tables together);
- fenced code blocks and multi-line ``$$`` display math are kept whole,
  delimiters included (a math row like ``- x + y &= 3 \\`` must not be
  mistaken for a list item), also when they sit inside a list item.
"""

import re

# blocks per document — a runaway guard, far above any real notes file
MAX_BLOCKS = 5000
# one .md file (upload or zip member)
MAX_MARKDOWN_BYTES = 5 * 1024 * 1024

_FRONTMATTER_RE = re.compile(r"^---[ \t]*\n(.*?)\n(?:---|\.\.\.)[ \t]*(?:\n|$)", re.S)
_HEADING_RE = re.compile(r"^(#{1,6})\s+\S")
_LIST_RE = re.compile(r"^([ \t]*)([-*+]|\d{1,4}[.)])\s+(.*)$")
_FENCE_LINE_RE = re.compile(r"^[ \t]*(```|~~~)")


def parse_frontmatter(text: str):
    """Return ``(fields, body)`` — YAML front matter is dropped and its
    top-level scalar ``key: value`` lines are returned as a dict (keys
    lower-cased, quotes stripped; nested keys and multi-line values are
    ignored)."""
    match = _FRONTMATTER_RE.match(text)
    if not match:
        return {}, text
    fields = {}
    for line in match.group(1).splitlines():
        if not line.strip() or line[:1].isspace() or line.lstrip().startswith("#"):
            continue
        key, sep, value = line.partition(":")
        if not sep:
            continue
        fields[key.strip().lower()] = value.strip().strip("'\"")
    return fields, text[match.end():]


def split_frontmatter(text: str):
    """Return (title_or_None, body) — only the ``title:`` field is read."""
    fields, body = parse_frontmatter(text)
    return fields.get("title") or None, body


def _open_construct(content: str) -> bool:
    """True while ``content`` has an unclosed ``` fence or ``$$`` block, so the
    next lines belong to it whatever they look like."""
    fences = sum(1 for line in content.split("\n") if _FENCE_LINE_RE.match(line))
    if fences % 2:
        return True
    in_fence = False
    dollars = 0
    for line in content.split("\n"):
        if _FENCE_LINE_RE.match(line):
            in_fence = not in_fence
        elif not in_fence:
            dollars += line.count("$$")
    return dollars % 2 == 1


def _dedent(raw: str, col: int) -> str:
    """Drop up to ``col`` columns of leading whitespace (tabs count 4)."""
    line = raw.replace("\t", "    ").rstrip()
    lead = len(line) - len(line.lstrip(" "))
    return line[min(lead, col):]


def md_to_blocks(text: str) -> list:
    """Parse markdown into a tree of ``{"content": str, "children": [...]}``."""
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    root = {"content": "", "children": []}
    headings = [(0, root)]   # (heading level, node) — nesting for everything
    lists = []               # (indent width, node, text column) — the current list run
    para: list[str] = []     # pending paragraph lines
    para_target = root
    blank_before = True      # was the previous line blank?
    count = 0

    def flush_para():
        nonlocal para, count
        if para and count < MAX_BLOCKS:
            para_target["children"].append({"content": "\n".join(para).strip(), "children": []})
            count += 1
        para = []

    def add(parent, content):
        nonlocal count
        node = {"content": content, "children": []}
        if count < MAX_BLOCKS:
            parent["children"].append(node)
            count += 1
        return node

    i = 0
    while i < len(lines):
        raw = lines[i]
        stripped = raw.strip()
        indented = raw[:1] in (" ", "\t")

        # A fence / $$ block opened inside a list item swallows everything
        # (blank lines included) until it closes.
        if lists and _open_construct(lists[-1][1]["content"]):
            lists[-1][1]["content"] += "\n" + _dedent(raw, lists[-1][2])
            blank_before = False
            i += 1
            continue

        if not stripped:
            flush_para()
            blank_before = True
            i += 1
            continue

        if _HEADING_RE.match(stripped) and not indented:
            flush_para()
            lists.clear()
            level = len(stripped) - len(stripped.lstrip("#"))
            while len(headings) > 1 and headings[-1][0] >= level:
                headings.pop()
            headings.append((level, add(headings[-1][1], stripped)))
            blank_before = False
            i += 1
            continue

        list_match = _LIST_RE.match(raw)
        if list_match:
            flush_para()
            indent = len(list_match.group(1).replace("\t", "    "))
            marker, body = list_match.group(2), list_match.group(3).strip()
            while lists and lists[-1][0] >= indent:
                lists.pop()
            parent = lists[-1][1] if lists else headings[-1][1]
            content = body if len(marker) == 1 else f"{marker} {body}"
            lists.append((indent, add(parent, content), indent + len(marker) + 1))
            blank_before = False
            i += 1
            continue

        # Indented text belongs to the innermost list item whose bullet sits
        # left of it: directly below the item it continues the item's content
        # (a multi-line block — also when it opens a fence or $$ block); after
        # a blank line it does so only when aligned with the item's text,
        # deeper indentation becoming a child block instead (handled by the
        # branches below, which nest under the item). Unindented text ends
        # the list run.
        if indented:
            expanded = raw.replace("\t", "    ")
            indent = len(expanded) - len(expanded.lstrip(" "))
            while lists and lists[-1][0] >= indent:
                lists.pop()
            if lists and (not blank_before or indent <= lists[-1][2]):
                flush_para()
                node, col = lists[-1][1], lists[-1][2]
                node["content"] += ("\n\n" if blank_before else "\n") + _dedent(raw, col)
                blank_before = False
                i += 1
                continue
        else:
            lists.clear()

        if stripped.startswith("```") or stripped.startswith("~~~"):
            flush_para()
            fence = stripped[:3]
            buf = [raw.rstrip()]
            i += 1
            while i < len(lines):
                buf.append(lines[i].rstrip())
                if lines[i].strip().startswith(fence):
                    i += 1
                    break
                i += 1
            add(lists[-1][1] if lists else headings[-1][1], "\n".join(buf))
            blank_before = False
            continue

        # Multi-line $$ display math is kept whole like a fence — its rows
        # would otherwise hit the list rule (`- x + y &= 3 \\`, `1. & ...`)
        # and shatter the formula across blocks.
        if stripped.startswith("$$") and "$$" not in stripped[2:]:
            flush_para()
            buf = [stripped]
            i += 1
            while i < len(lines):
                buf.append(lines[i].strip())
                if "$$" in lines[i]:
                    i += 1
                    break
                i += 1
            add(lists[-1][1] if lists else headings[-1][1], "\n".join(buf))
            blank_before = False
            continue

        # Plain text: a paragraph under the current heading, or a child block
        # of the list item it is indented under.
        target = lists[-1][1] if lists else headings[-1][1]
        if para and para_target is not target:
            flush_para()
        para_target = target
        para.append(stripped)
        blank_before = False
        i += 1

    flush_para()
    return root["children"]
