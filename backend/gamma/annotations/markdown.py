"""The Markdown form of an annotations document (docs/dev/annotations.md
"The Markdown"): what ``.gamma/annotations/open.md``, ``GET
/api/annotations?format=md`` and the MCP tools give an agent to read. A
heading per file, an item per highlight and thread with its place, source
and pictures, a thread's comments quoted line by line (a body's own
Markdown left as it is), and how to answer at the end.

The pictures are written as the reader can reach them: ``files`` the
``img/`` files beside ``open.md``, ``urls`` the routes that serve them,
``tool`` a line that names the tool to read them with. The text is English
whatever the person's language: it is for agents, the one text Gamma writes
untranslated (docs/dev/i18n.md).
"""

import re

from ..db import parse_stamp
from . import anchors

MODES = ("files", "urls", "tool")
HEADING_QUOTE = 80   # characters of a passage's quote in an item's heading
NOTE_LINE = 300      # characters of a note's text in its item

INTRO = ("Each item is a comment thread or a highlight. Answer a thread in Gamma: reply with what you changed\n"
         "and where, resolve it when it is done (see \"How to answer\" at the end). A highlight marks a place,\n"
         "with the note written on it: read it as context.")
PLACED = {"exact": "placed exactly", "rough": "placed roughly ({match})", "estimate": "placed by estimate",
          "lost": "not found in the current version", "pending": "not placed yet",
          "earlier": "drawn on an earlier version"}
REGION_PLACED = {"exact": "placed exactly", "earlier": "drawn on an earlier version of the picture",
                 "lost": "the file is no longer a picture"}
REBUILT = {"area": "the figure was rebuilt", "passage": "the PDF was rebuilt", "file": "the picture was rebuilt"}
STATE_WORDS = {"open": "open comment", "resolved": "resolved comment", "all": "comment"}


def _when(stamp) -> str:
    at = parse_stamp(stamp) if stamp else None
    return at.strftime("%Y-%m-%d %H:%M") if at else str(stamp or "")


def _line(text: str, cap: int) -> str:
    text = re.sub(r"\s+", " ", text or "").strip()
    return text if len(text) <= cap else text[:cap - 1].rstrip() + "…"


def heading(entry: dict) -> str:
    """What an item is on: ``Page 3 · “the loss is defined as”``,
    ``Page 5 · area``, ``Page 2 · handwriting``, ``Page 3 · note on “…”``,
    ``Region · 320×180 px at (40, 60)``, ``Note on a region · …``, ``Lines
    118–120 · “…”``, ``Line 118 · note on “…”``, ``A note``, ``The whole
    file``. A highlight's own note is no "note on": the note is the
    highlight's."""
    kind, where = entry["kind"], entry["where"]
    if kind == "file":
        return "The whole file"
    if kind == "note":
        return "A note"
    on = "note on " if where.get("note") and entry.get("type") != "highlight" else ""
    if kind == "lines":
        line = where.get("line")
        lines = anchors.line_words(line, where.get("end") or line).capitalize() + " · " if line else ""
        quote = _line(where.get("quote", ""), HEADING_QUOTE)
        return f"{lines}{on}“{quote}”" if quote else f"{lines}{on}passage"
    if kind == "region":
        what, rect = "Note on a region" if on else "Region", where.get("rect")
        return f"{what} · {rect[2] - rect[0]}×{rect[3] - rect[1]} px at ({rect[0]}, {rect[1]})" if rect else what
    page = f"Page {where['page']} · " if where.get("page") else ""
    if kind == "passage":
        quote = _line(where.get("quote", ""), HEADING_QUOTE)
        return f"{page}{on}“{quote}”" if quote else f"{page}{on}passage"
    if kind == "area":
        return f"{page}{'note on an area' if on else 'area'}"
    return f"{page}{on}handwriting"


def _facts(entry: dict) -> list[str]:
    where = entry["where"]
    facts = [f"id: `{entry['id']}`", *(["highlight"] if entry.get("type") == "highlight" else []), entry["kind"]]
    if where.get("placed"):
        words = REGION_PLACED if entry["kind"] == "region" else PLACED
        facts.append(words[where["placed"]].format(match=where.get("match", "")))
    if entry.get("before") and entry["kind"] in REBUILT:
        facts.append(REBUILT[entry["kind"]])
    if entry.get("state") == "resolved":
        facts.append("resolved")
    lines = ["- " + " · ".join(facts)]
    if where.get("note"):
        lines.append(f"- note: “{_line(where['note'], NOTE_LINE)}”")
    if where.get("was"):
        lines.append(f"- was: “{_line(where['was'], HEADING_QUOTE)}”")
    source = entry.get("source")
    if source:
        span = f"lines {source['line']}–{source['end']}" if source.get("end") else f"line {source['line']}"
        lines.append(f"- source: `{source['file']}`, {span}" + (" (as commented)" if source.get("version") == "commented"
                                                                 else ""))
    return lines


def _pictures(entry: dict, mode: str) -> list[str]:
    now, before = entry.get("picture"), entry.get("before")
    if not now and not before:
        return []
    if mode == "tool":
        return [f"Picture: read_annotation(id=\"{entry['id']}\")"]
    page = entry["where"].get("page")
    if entry["kind"] == "file":
        what, was = "The picture", "The picture as it was commented on"
    else:
        at = f"Page {page}, the" if page else "The"
        mark = "highlight" if entry.get("type") == "highlight" else "comment"
        what, was = f"{at} {mark}'s region", f"{at} region as it was commented on"
    if mode == "urls":
        now, before = entry.get("picture_url") or now, entry.get("before_url") or before
    lines = []
    if now:
        lines.append(f"![{what}{' now' if before else ''}]({now})")
    if before:
        lines.append(f"![{was}]({before})")
    return lines


def _comment(comment: dict) -> list[str]:
    by = comment.get("by") or "someone"
    who = f"**{comment['agent']}**, for {by}" if comment.get("agent") else f"**{by}**"
    body = "*(deleted)*" if comment.get("deleted") else comment.get("body", "")
    return [f"> {who} · {_when(comment.get('at'))}",
            *(f"> {line}" if line else ">" for line in body.replace("\r\n", "\n").split("\n"))]


def render_entry(entry: dict, *, pictures: str = "tool", number: int | None = None) -> str:
    """One item: its heading (numbered when ``number``), its facts, its
    pictures (``pictures``: one of ``MODES``) and its comments, each part
    followed by a blank line."""
    if pictures not in MODES:
        raise ValueError(f"pictures is one of {', '.join(MODES)}")
    parts = [[f"### {f'{number}. ' if number else ''}{heading(entry)}"], _facts(entry), _pictures(entry, pictures),
             *map(_comment, entry["comments"])]
    return "".join("\n".join(part) + "\n\n" for part in parts if part)


def _how_to_answer(doc: dict, mode: str) -> list[str]:
    last = {"files": "- The JSON form with every id is `open.json` beside this file.",
            "urls": f"- The JSON form with every id is `GET /api/annotations?scope={doc['scope']['id']}"
                    f"&state={doc.get('state', 'open')}`.",
            "tool": "- `read_annotation(id)` reads one item with its pictures and its JSON form."}[mode]
    return ["---", "", "## How to answer", "",
            "- Reply: `POST /api/pages/{page_id}/comments` with `{\"block_id\", \"thread_id\", \"body\"}`, or the "
            "`comment` tool with `thread_id`.",
            "- Resolve: `POST /api/comments/{id}/resolve` with `{\"resolved\": true}`, or the `resolve` tool.",
            "- Ask or point something out: the `comment` tool on a place (a PDF quote with its page, a file's lines, "
            "a note), and `highlight` to mark a passage.",
            last]


def render(doc: dict, *, pictures: str = "files") -> str:
    """The document (``entries.document``) as Markdown; ``pictures`` one
    of ``MODES``. An unchanged document gives the same bytes."""
    if pictures not in MODES:
        raise ValueError(f"pictures is one of {', '.join(MODES)}")
    words = STATE_WORDS.get(doc.get("state", "open"), "comment")
    out = [f"# Annotations on {doc['scope']['name'] or 'the library'}", ""]
    entries = doc["entries"]
    if not entries:
        out += [f"No {words}s and no highlights.", ""]
    else:
        threads = sum(1 for e in entries if e.get("type") != "highlight")
        marks = len(entries) - threads
        facts = [f"{threads} {words}{'' if threads == 1 else 's'}"] if threads else []
        if marks:
            facts.append(f"{marks} highlight{'' if marks == 1 else 's'}")
        if doc.get("owner"):
            facts.append(f"owner {doc['owner']}")
        if doc.get("updated_at"):
            facts.append(f"updated {_when(doc['updated_at'])}")
        if doc.get("truncated"):
            facts.append(f"{doc['truncated']} more not listed")
        out += [" · ".join(facts), "", INTRO, ""]
    text = "\n".join(out) + "\n"
    file = None
    for number, entry in enumerate(entries, 1):
        if entry["file"] != file:
            file = entry["file"]
            text += f"## {file}\n\n"
        text += render_entry(entry, pictures=pictures, number=number)
    return text + "\n".join(_how_to_answer(doc, pictures)) + "\n"
