"""The gate before a vault's write path is switched on (docs/research/vaults.md,
"The gate before any write is enabled"): the laws of ``gamma/md_lens.py`` over
every Markdown file the repository holds and an Obsidian-style corpus, with
random edits, inserts, deletes and moves through ``shape``, from a seeded
``random.Random`` so that a failure names what replays it.

The fixtures are stored with LF. ``core.autocrlf`` and the absence of a ``*.md``
rule in ``.gitattributes`` would rewrite a stored CRLF file on checkout, so the
line endings, the byte-order mark and a missing last newline are made here,
as bytes, from the LF text (``variants``).

The refusals allowed are ``Unwritable(OPEN_BLOCK)``, on a file that has an
unclosed fence or ``$$``, which takes nothing after it, or a change that would
put a block in HTML left open (``leaves_html_open``), and
``Unwritable(MEANING)``, a change after which another app would read a block it
left alone otherwise (``MATH_TAIL`` where a formula written has text after its
closing ``$$``): those are counted by the kind of change, and every one seen
stands on what CommonMark and this parser read apart in the file (a lazy line,
code by indentation this parser reads as text, a table whose head starts with
``- ``).
"""

import hashlib
import json
import random
import re
from collections import Counter
from pathlib import Path

import pytest

from gamma import md_lens
from gamma.markdown_import import block_class, md_to_blocks, parse_frontmatter
from gamma.md_lens import MATH_TAIL, MEANING, OPEN_BLOCK, Unwritable, flatten, parse, shape, tree_of, write

BACKEND = Path(__file__).resolve().parents[1]
REPO = BACKEND.parent
FIXTURES = BACKEND / "tests" / "vault_fixtures"
SPANS_OFF = FIXTURES / "spans_off_digests.json"

# A file's changes: the smaller the file the more, so that the whole gate stays
# under a minute. A round on a 300 KB file is a few parses.
BYTES_PER_FILE = 60_000
LEAST_ROUNDS = 2
MOST_ROUNDS = 14
CHAIN = 6
SEED = 20261106
WORDS = ("alpha", "bravo", "charlie", "delta", "echo")


def repo_files() -> list:
    """Every Markdown file of the gate but the fixtures, as read."""
    paths = [*sorted((REPO / "docs").rglob("*.md")), BACKEND / "gamma" / "onboarding" / "welcome.md",
             *sorted((BACKEND / "tests" / "notion_fixtures").rglob("*.md"))]
    return [(path.relative_to(REPO).as_posix(), path.read_bytes().decode("utf-8")) for path in paths]


def fixture_files() -> list:
    return [(path.relative_to(FIXTURES).as_posix(), path.read_bytes().decode("utf-8"))
            for path in sorted(FIXTURES.rglob("*.md"))]


def variants(name: str, text: str) -> list:
    """A fixture's LF text with its bytes changed the ways a real file's are."""
    lf = text.replace("\r\n", "\n")
    return [(name, lf), (f"{name} [crlf]", lf.replace("\n", "\r\n")), (f"{name} [cr]", lf.replace("\n", "\r")),
            (f"{name} [bom]", "\ufeff" + lf), (f"{name} [bom crlf]", "\ufeff" + lf.replace("\n", "\r\n")),
            (f"{name} [no last newline]", lf.rstrip("\n"))]


def corpus() -> list:
    return [*repo_files(), *(v for name, text in fixture_files() for v in variants(name, text))]


# --- the invariants of a parse ---------------------------------------------------

def check_read(doc, raw: str) -> list:
    """Law 1, law 4 and the tiling, for one file. What is wrong, in words."""
    wrong = []
    if doc.problem:
        return [f"a problem: {doc.problem}"]
    start = doc.front.last if doc.front else 0
    at = start
    for tile in doc.tiles:
        if tile.first != at:
            wrong.append(f"a line outside the tiles at {at}")
        at = tile.last
        if tile.kind == "gap" and any(line.strip(" \t\r\n") for line in doc.lines[tile.first:tile.last]):
            wrong.append(f"a gap with text at {tile.first}")
    if at != len(doc.lines):
        wrong.append("the tiles stop short")
    firsts = [node.span.first for node in doc.flat]
    if firsts != sorted(firsts):
        wrong.append("document order is not line order")
    for node in doc.flat:
        if len(node.content.split("\n")) > len(node.span.sources):
            wrong.append(f"line {node.span.first}: a line of content with no source")
    ids = [f"b{i}" for i in range(len(doc.flat))]
    tree = tree_of(doc, ids)
    if write(doc, ids, tree)[0] != raw:
        wrong.append("law 1: not the same bytes")
    shaped, text = shape(doc, ids, tree)
    if text != raw or shaped != tree:
        wrong.append("law 4: not the same tree and text")
    return wrong


# --- random changes ---------------------------------------------------------------

def clone(tree: list) -> list:
    return [{**node, "children": clone(node["children"])} for node in tree]


def edited_text(rng: random.Random, content: str) -> str:
    """``content`` with a word changed or a line added, as a person edits: the
    class of the block stays, nothing opens, no line is taken away."""
    lines = content.split("\n")
    word = rng.choice(WORDS)
    cls = block_class(content)
    if cls in ("fence", "math") or (lines[0].startswith("    ") and cls != "item"):
        at = max(1, len(lines) - 1) if len(lines) > 1 else 1
        if cls in ("fence", "math") and len(lines) > 2 and rng.random() < 0.5:
            lines[rng.randrange(1, len(lines) - 1)] += f" {word}"
        else:
            lines.insert(at, f"    {word}" if cls not in ("fence", "math") else word)
        return "\n".join(lines)
    if cls == "item" and len(lines) == 1 and not lines[0].strip(" -[]x0123456789.)"):
        return f"{lines[0].rstrip()} {word}"
    add = rng.random() < 0.3 and cls in ("plain", "quote", "item", "table")
    if add:
        lines.append({"quote": f"> {word}", "item": f"  {word}", "table": f"| {word} |"}.get(cls, word))
        return "\n".join(lines)
    at = rng.randrange(len(lines))
    if not lines[at].strip():
        at = len(lines) - 1
    if cls == "table" and at == 1:
        at = 0
    words = lines[at].split(" ")
    spot = rng.randrange(len(words))
    if rng.random() < 0.5 and re.fullmatch(r"[A-Za-z]{2,}", words[spot]):
        words[spot] = word
        lines[at] = " ".join(words)
    else:
        lines[at] += f" {word}"
    return "\n".join(lines)


def parent_map(tree: list) -> dict:
    out = {}

    def walk(nodes, parent):
        for node in nodes:
            out[node["id"]] = parent
            walk(node["children"], node["id"])

    walk(tree, None)
    return out


def find_list(tree: list, block_id):
    """The list holding the node ``block_id``, and its index there."""
    pending = [tree]
    while pending:
        nodes = pending.pop()
        for i, node in enumerate(nodes):
            if node["id"] == block_id:
                return nodes, i
            pending.append(node["children"])
    raise KeyError(block_id)


def random_place(rng: random.Random, tree: list, leave_out=()) -> tuple:
    """A list of the tree, an index in it, and the block that list is the
    children of: the tree's nodes outside ``leave_out``."""
    nodes = [n for n in flatten(tree) if n["id"] not in leave_out]
    if not nodes or rng.random() < 0.3:
        return tree, rng.randint(0, len(tree)), None
    node = rng.choice(nodes)
    if rng.random() < 0.3:
        return node["children"], rng.randint(0, len(node["children"])), node["id"]
    holder, i = find_list(tree, node["id"])
    return holder, i + rng.randint(0, 1), parent_map(tree)[node["id"]]


NEW_BLOCKS = ("a new paragraph", "- a new item", "- [ ] a new task", "1. a new number", "> a new quote",
              "```\nnew code\n```", "| a |\n|---|\n| 1 |", "two new\nlines", "## a new heading", "$$\nx\n$$",
              # what the line-break key and a paste make: one block that a file reads as several
              "- one\n- two", "para\n\nafter a gap", "para\n- a list under it", "# head\ntext under it",
              "text\n```\ncode\n```\nmore", "trailing space ", "a\n  indented line", "    code here", "---")
# What a person holds open for as long as they type it.
OPENING = ("```", "```python\ndef f():", "$$", "$$\nx +", "- ```\n  code")


def takes_what_follows(*texts) -> bool:
    """Whether a heading is in these texts, which then parents what follows it."""
    return any(block_class(text) == "heading" or re.search(r"^#{1,6}\s", text, re.M) for text in texts)


def mutate(rng: random.Random, tree: list, op: str) -> dict:
    """Change ``tree`` in place. What was done: the ids of the blocks changed,
    which take in a block given a child (a paragraph holding one becomes a list
    item, by the rule of the walk), and for a text edit its id and old text."""
    did = _mutated(rng, tree, op)
    did["op"] = op
    return did


def _mutated(rng: random.Random, tree: list, op: str) -> dict:
    nodes = flatten(tree)
    if op == "edit":
        node = rng.choice(nodes)
        was = node["content"]
        node["content"] = edited_text(rng, was)
        return {"touched": {node["id"]}, "edit": (node["id"], was)}
    if op == "retype":
        node = rng.choice(nodes)
        was, node["content"] = node["content"], rng.choice(NEW_BLOCKS)
        return {"touched": {node["id"]}, "headings": takes_what_follows(was, node["content"]), "retyped": node["id"]}
    if op == "open":
        node = rng.choice(nodes)
        was, node["content"] = node["content"], rng.choice(OPENING)
        return {"touched": {node["id"]}, "headings": takes_what_follows(was, node["content"]), "opens": True}
    if op == "anchor":
        node = rng.choice(nodes)
        node["anchor"] = "zz" + "".join(rng.choices("abcdef0123", k=6))
        return {"touched": {node["id"]}, "anchored": (node["id"], node["anchor"])}
    if op == "insert":
        holder, i, owner = random_place(rng, tree)
        content = rng.choice(NEW_BLOCKS)
        new_id = f"new{rng.randrange(10 ** 6)}"
        holder.insert(i, {"id": new_id, "content": content, "children": []})
        return {"touched": {owner}, "headings": takes_what_follows(content), "placed": (new_id, owner)}
    node = rng.choice(nodes)
    holder, i = find_list(tree, node["id"])
    subtree = [n for n in flatten([node])]
    ids = {n["id"] for n in subtree}
    del holder[i]
    if op == "delete":
        return {"touched": ids}
    holder, i, owner = random_place(rng, tree, ids)
    holder.insert(i, node)
    return {"touched": ids | {owner}, "headings": takes_what_follows(*(n["content"] for n in subtree)),
            "placed": (node["id"], owner)}


def own_lines(doc) -> dict:
    """``{node index: its raw lines}``, the last one with no terminator."""
    return {n.index: "".join(doc.lines[n.span.first:n.span.last]).rstrip("\r\n") for n in doc.flat}


def strip_lead(text: str) -> str:
    return "\n".join(line.strip() for line in re.split(r"\r\n|\r|\n", text))


def no_anchor(content: str) -> str:
    """A block's text with no anchor at its end: of two copies of an anchor, the
    first is the anchor and the other text, and which is first can change."""
    return md_lens._normal(re.sub(r"[ \t]+\^[A-Za-z0-9-]+$", "", content))


def is_fence_of(content: str, was: str, raw: str) -> bool:
    """Whether ``content`` is code by indentation made a fence: of its text, or
    of its raw lines where this parser read it as text."""
    return content in (md_lens._fenced(was), md_lens._fenced(re.sub(r"\r\n?", "\n", raw)))


def leaves_html_open(doc, ids, tree: list) -> bool:
    """Whether ``tree`` written over ``doc`` puts a block in HTML left open (a
    comment split over blocks, its closer taken away or a block put inside),
    which the file did not hold there: a refusal the lens owes."""
    text, starts = write(doc, ids, tree)
    read = parse(text)
    held = [starts.get(node.span.first) for node in read.flat]
    was = {ids[i] for i in md_lens._hidden(doc)}
    return any(held[i] is None or held[i] not in was for i in md_lens._hidden(read))


def in_list(node) -> bool:
    """Whether ``node`` stands in a list item."""
    while node is not None:
        if node.span.kind in md_lens._ITEMS:
            return True
        node = node.parent
    return False


def after_a_list(read, node) -> bool:
    """Whether a list stands before ``node``, in the flow of ``read``, which is
    what moves a block's indent there."""
    k = node.index - 1
    while k > 0 and not in_list(read.flat[k]) and read.lines[read.flat[k].span.first - 1].strip():
        k -= 1                                    # a lazy line: the list before it is still open
    return k >= 0 and in_list(read.flat[k])


def moved_as_needed(doc, old, read, new) -> bool:
    """Whether a block in a list item whose indent changed, ``old`` in ``doc``
    and ``new`` in ``read``, moved with the text of the item it is in (code
    by indentation counts from its innermost marker), or had to: with its old
    lines back, the file reads as something else."""
    def text_of(d, n):
        return md_lens._code_column(md_lens._bare(d.lines[n.parent.span.first])) if n.parent else 0

    shift = md_lens.text_column(read.lines[new.span.first]) - md_lens.text_column(doc.lines[old.span.first])
    if shift == text_of(read, new) - text_of(doc, old):
        return True
    lines = [*read.lines[:new.span.first], *doc.lines[old.span.first:old.span.last], *read.lines[new.span.last:]]
    return strip_ids(tree_of(parse("".join(lines)))) != strip_ids(tree_of(read))


def check_shape(doc, ids, before: list, tree: list, did: dict, raw_of: dict, stats: Counter) -> list:
    """The laws after one change. ``before`` is the tree read, ``tree`` the tree
    changed. What is wrong, in words."""
    known = {n["id"] for n in flatten(tree)}
    try:
        shaped, text = shape(doc, ids, tree)
    except Unwritable as exc:
        if str(exc) == OPEN_BLOCK and did.get("opens"):
            stats["refused: a block held open"] += 1
            return []
        if str(exc) == OPEN_BLOCK and any(n.span.open for n in doc.flat):
            stats["refused: after an unclosed block"] += 1
            return []
        if str(exc) == OPEN_BLOCK and leaves_html_open(doc, ids, tree):
            stats["refused: a block HTML left open would take in"] += 1
            return []
        if str(exc) in (MEANING, MATH_TAIL):
            stats[f"refused: another app would read a block left alone otherwise, {did['op']}"] += 1
            return []
        return [f"refused: {exc}"]
    read = parse(text)
    wrong = []
    for i, node in enumerate(flatten(shaped)):                                    # as the engine mints them
        node["id"] = node["id"] or f"split{i}"
    held = [n["id"] for n in flatten(shaped)]
    lost = known - set(held)
    if lost:                                                                      # law 7
        wrong.append(f"law 7: ids lost {sorted(lost)[:5]}")
    # Law 3. ``shape`` reads ``shaped`` off the text it wrote, so that half of
    # the law is its making; what can fail is that it settles there.
    again, same = shape(read, held, shaped)
    if same != text or again != shaped:
        wrong.append("law 3: a second shape moves")
    if [i for i in held if i in known] != [n["id"] for n in flatten(tree)]:
        wrong.append("the blocks are not in the order asked for")
    wrong += check_landed(tree, shaped, did, stats)

    # Law 5, but for what Markdown cannot do. Each exception is one thing, and
    # is counted by name:
    # - a heading moved, added or given a new class takes what follows it, in
    #   any reader: the bytes stay and the parents do not;
    # - a block a list now stands before cannot stay indented past that list:
    #   only its indent changes, and a heading's text, which column 0 would
    #   make a heading, goes under the list; text four columns in that a blank
    #   line comes to stand above goes to column 3; in a list item a block
    #   moves with the item's text, or where its old indent would read as
    #   something else (``moved_as_needed``); no other indent may change;
    # - a block retyped into two takes its children to the last of them;
    # - code by indentation that has to move becomes a fence, of its raw lines
    #   where this parser read it as text;
    # - a ``---`` the file now opens with, followed by what front matter holds,
    #   would be front matter: it becomes ``***``;
    # - text the file would now read as an anchor (``^x`` after a block, a
    #   copy of an anchor that comes first) has its caret escaped, which
    #   ``tree_equal`` does not see, so Gamma's text stays.
    now = {node_id: read.flat[i] for i, node_id in enumerate(held)}
    place_of = {node_id: i for i, node_id in enumerate(ids)}
    was_parent, now_parent = parent_map(before), parent_map(shaped)
    for node in flatten(before):
        node_id = node["id"]
        if node_id not in now:
            continue
        new = now[node_id]
        anchor = node.get("anchor", "").lower()
        if anchor and new.anchor.lower() != anchor and did.get("anchored", (None,))[0] != node_id:
            # What a link hangs on. Of a block that split it stays on the text it
            # ended, and of two copies the first has it, which a move can change.
            elsewhere = any(n.anchor.lower() == anchor for n in read.flat)
            if elsewhere and node_id in (did.get("retyped"), *did.get("edit", ())):
                stats["an anchor gone with the last part of a split"] += 1
            elif not (elsewhere and doc.raw.lower().count(f"^{anchor}") > 1):
                wrong.append(f"the anchor of {node_id} is {new.anchor!r}, not {anchor!r}")
        if node_id in did["touched"]:
            continue
        lines = "".join(read.lines[new.span.first:new.span.last]).rstrip("\r\n")
        if lines != raw_of[node_id]:
            if is_fence_of(new.content, node["content"], raw_of[node_id]):
                stats["code by indentation become a fence"] += 1
                continue
            if lines.startswith("***") and raw_of[node_id].startswith("---") and new.index == 0:
                stats["a divider kept from opening front matter"] += 1
                continue
            if md_lens._ESCAPED_ANCHOR_RE.sub(r"\1\2", lines) == raw_of[node_id]:
                stats["a caret kept from reading as an anchor"] += 1
                continue
            four_in = md_lens.text_column(raw_of[node_id]) >= 4 and md_lens.text_column(lines) == 3
            needed = (moved_as_needed(doc, doc.flat[place_of[node_id]], read, new) if in_list(new)
                      else four_in or after_a_list(read, new))
            if strip_lead(lines) != strip_lead(raw_of[node_id]) or not needed:
                wrong.append(f"law 5: bytes of {node_id} {raw_of[node_id][:50]!r} -> {lines[:50]!r}")
                continue
            stats["moved over by its indent"] += 1
        if no_anchor(new.content) != no_anchor(node["content"]):
            wrong.append(f"law 5: content of {node_id} {node['content'][:50]!r} -> {new.content[:50]!r}")
        if was_parent[node_id] != now_parent[node_id]:
            if did["headings"]:
                stats["a parent changed by a heading"] += 1
            elif was_parent[node_id] == did.get("retyped") and now_parent[node_id] not in was_parent:
                stats["children gone with the last part of a split"] += 1
            elif block_class(node["content"]) == "heading" and raw_of[node_id][:1].isspace():
                stats["a heading's text under a list"] += 1
            else:
                wrong.append(f"law 5: parent of {node_id} {node['content'][:50]!r}: "
                             f"{was_parent[node_id]} -> {now_parent[node_id]}")
    return wrong


def check_landed(tree: list, shaped: list, did: dict, stats: Counter) -> list:
    """That what was asked for is in the file, so that a walk that dropped a
    change could not pass: the text edited, the anchor on its block, the block
    under its new parent (a list item's parent is its own to give)."""
    wrong = []
    now = {n["id"]: n for n in flatten(shaped)}
    if "edit" in did and not did["new_class"]:
        node_id = did["edit"][0]
        want = next(n["content"] for n in flatten(tree) if n["id"] == node_id)
        landed = md_lens._normal(now[node_id]["content"])
        if landed != md_lens._normal(want):
            if md_lens._normal(want).startswith(landed + "\n"):               # the rest is blocks after it
                stats["an edit that split"] += 1
            else:
                wrong.append(f"the edit did not land: {want[:60]!r} is {now[node_id]['content'][:60]!r}")
    if "anchored" in did:
        node_id, anchor = did["anchored"]
        if now[node_id].get("anchor", "") != anchor:
            old = next(n for n in flatten(tree) if n["id"] == node_id).get("anchor")
            if old != anchor:
                wrong.append("the anchor did not land")
            elif not now[node_id].get("anchor"):
                wrong.append(f"the anchor did not land on {now[node_id]['content'][:60]!r}")
    if "placed" in did and not did["headings"]:
        node_id, owner = did["placed"]
        parents = parent_map(shaped)
        if owner is not None and now.get(owner) and block_class(now[owner]["content"]) != "heading":
            if parents[node_id] != owner:
                wrong.append(f"{node_id} is not under {owner}: {parents[node_id]}")
    return wrong


def mutations(rng: random.Random) -> str:
    return rng.choice(("edit", "edit", "insert", "delete", "move", "retype", "anchor"))


def gate_file(name: str, raw: str, stats: Counter, rounds: int, seed: int) -> list:
    """Every law on one file: what is wrong, each with what replays it."""
    doc = parse(raw)
    stats["files"] += 1
    stats["blocks"] += len(doc.flat)
    wrong = [f"{name}: {w}" for w in check_read(doc, raw)]
    if wrong or not doc.flat:
        return wrong
    ids = [f"b{i}" for i in range(len(doc.flat))]
    before = tree_of(doc, ids)
    raw_of = {ids[i]: text for i, text in own_lines(doc).items()}
    rng = random.Random(f"{seed}:{name}")
    for round_no in range(rounds):
        op = mutations(rng)
        tree = clone(before)
        did = mutate(rng, tree, op)
        stats[f"{op}s"] += 1
        tag = f"{name}: round {round_no} {op}"
        note_class(doc, ids, tree, did)
        problems = check_shape(doc, ids, before, tree, did, raw_of, stats)
        wrong += [f"{tag}: {p}" for p in problems]
        if op == "edit" and not problems:
            wrong += [f"{tag}: {p}" for p in check_edit(doc, ids, tree, did, raw, stats)]
    return wrong + [f"{name}: {w}" for w in gate_chain(raw, rng, stats, min(rounds, CHAIN))]


def note_class(doc, ids, tree: list, did: dict):
    """What a change did to classes: an edit that left its block's has a revert."""
    did["new_class"] = False
    did.setdefault("headings", False)
    if "edit" in did:
        node_id, was = did["edit"]
        new = find_content(tree, node_id)
        did["new_class"] = not md_lens._same_class(doc.flat[ids.index(node_id)], new)
        did["headings"] = did["new_class"] and takes_what_follows(was, new)


def gate_chain(raw: str, rng: random.Random, stats: Counter, length: int) -> list:
    """Changes one after another, each on the file the last one wrote, as a
    file lives through them: a block that is hard to write has to stay
    writable. Law 1 and law 4 hold of every file on the way."""
    doc = parse(raw)
    ids = [f"b{i}" for i in range(len(doc.flat))]
    wrong = []
    opened = False       # an opener that went in may be what a later block lands in
    for step in range(length):
        if not doc.flat:
            break
        op = rng.choice((*("edit", "insert", "delete", "move", "retype", "anchor"), "open"))
        tree = tree_of(doc, ids)
        before = clone(tree)
        did = mutate(rng, tree, op)
        note_class(doc, ids, tree, did)
        opened = opened or bool(did.get("opens"))
        did["opens"] = opened
        stats["chained"] += 1
        raw_of = {ids[i]: text for i, text in own_lines(doc).items()}
        problems = check_shape(doc, ids, before, tree, did, raw_of, stats)
        if problems:
            return [*wrong, *(f"chain step {step} {op}: {p}" for p in problems)]
        try:
            shaped, text = shape(doc, ids, tree)
        except Unwritable:
            continue
        for i, node in enumerate(flatten(shaped)):
            node["id"] = node["id"] or f"s{step}.{i}"
        doc, ids = parse(text), [n["id"] for n in flatten(shaped)]
        wrong += [f"chain step {step} {op}: {w}" for w in check_read(doc, text)]
    return wrong


def check_edit(doc, ids, tree, did, raw: str, stats: Counter) -> list:
    """Laws 2 and 6 for a text edit that went through."""
    node_id, was = did["edit"]
    try:
        shaped, text = shape(doc, ids, tree)
    except Unwritable:
        return []
    node = doc.flat[ids.index(node_id)]
    wrong = []
    head = doc.bom + "".join(doc.lines[:node.span.first])
    tail = "".join(doc.lines[node.span.last:])
    if not text.startswith(head) or not text.endswith(tail):                      # law 2
        wrong.append("law 2: lines outside the block differ")
    elif (at := blank_beside(text, head, tail, node.span.first)) is not None:
        if blank_needed(parse(text), at):
            stats["a blank line an edit needed"] += 1
        else:
            wrong.append("law 2: a blank line the edit did not need beside the block")
    for i, block in enumerate(flatten(shaped)):
        block["id"] = block["id"] or f"split{i}"
    held = [block["id"] for block in flatten(shaped)]
    if len(held) != len(ids) or did["new_class"]:
        return wrong                                  # a split or a new class has no way back
    if node.span.kind == "item" and not node.span.marker[-1:].isspace():
        stats["an empty box with no space after it"] += 1    # the file written has not kept that
        return wrong
    read = parse(text)
    back = tree_of(read, held)
    holder, i = find_list(back, node_id)
    holder[i]["content"] = was
    reverted = shape(read, held, back)[1]
    if reverted != raw and reverted in blank_line_beside(doc, node):
        stats["a blank line an edit needed, kept on its revert"] += 1             # law 2 keeps it
    elif reverted != raw:                                                         # law 6
        wrong.append(f"law 6: edit then revert changed the bytes ({was[:40]!r})")
    stats["reverts"] += 1
    return wrong


def blank_beside(text: str, head: str, tail: str, first: int) -> int | None:
    """The line of ``text`` that is a blank line next to the block ``head``
    and ``tail`` round, which starts on line ``first``: no block's own lines
    start or end on one. None when there is none."""
    middle = md_lens._split(text[len(head):len(text) - len(tail)])
    if middle and not middle[0].strip():
        return first
    if len(middle) > 1 and not middle[-1].strip():
        return first + len(middle) - 1
    return None


def blank_needed(read, at: int) -> bool:
    """Whether Markdown needs the blank line ``at`` of the file ``read``: without
    it the file reads as something else here, or the line after it would join
    the block before it in CommonMark (a lazy line, which this parser does not
    see; a number that cannot break into a paragraph)."""
    text = "".join(read.lines[:at] + read.lines[at + 1:])
    if strip_ids(tree_of(parse(text))) != strip_ids(tree_of(read)):
        return True
    before = [n for n in read.flat if n.span.last == at]
    after = [n for n in read.flat if n.span.first == at + 1]
    if not before or not after:
        return False
    block = before[-1]
    end = md_lens._end_of(block.span.kind, read.lines[block.span.first:block.span.last])
    end = md_lens._within(end, after[0].parent is block)
    return md_lens._joins(end, after[0].span.kind, md_lens._bare(read.lines[at + 1]))


def strip_ids(nodes: list) -> list:
    return [(n["content"], n.get("anchor"), strip_ids(n["children"])) for n in nodes]


def blank_line_beside(doc, node) -> set:
    """The file with one blank line more right before or after ``node``: what
    an edit leaves that needed one there (a text line under a setext heading,
    before ``10.``, which breaks into no paragraph), which its revert cannot
    tell from one a person wrote."""
    head, body, tail = (doc.lines[:node.span.first], doc.lines[node.span.first:node.span.last],
                        doc.lines[node.span.last:])
    return {doc.bom + "".join([*head, *between]) for blank in ("\n", "\r\n", "\r")
            for between in ([blank, *body, *tail], [*body, blank, *tail])}


def find_content(tree: list, node_id) -> str:
    holder, i = find_list(tree, node_id)
    return holder[i]["content"]


# --- the gate ----------------------------------------------------------------------

def rounds_for(raw: str) -> int:
    return max(LEAST_ROUNDS, min(MOST_ROUNDS, BYTES_PER_FILE // max(1, len(raw))))


def test_the_laws_hold_over_the_corpus():
    stats, wrong = Counter(), []
    for name, raw in corpus():
        wrong += gate_file(name, raw, stats, rounds_for(raw), SEED)
    print("\ngate:", dict(stats))
    assert not wrong, f"{len(wrong)} failures, the first:\n" + "\n".join(wrong[:30])


def test_law_one_on_random_bytes():
    """Any text comes back: random bytes decoded, heavy with what the parser
    looks at."""
    rng = random.Random(SEED)
    pieces = ["\r\n", "\r", "\n", "\n\n", "\t", "    ", "  ", "- ", "* ", "1. ", "# ", "> ", "```", "~~~", "$$", "---",
              "\ufeff", "^abc", " ^abc", "[ ] ", "|", "\x0b", "\x0c", " ", "\x85", "a", "b c", "<!--", "%%"]
    for _ in range(4000):
        raw = "".join(rng.choice(pieces) for _ in range(rng.randrange(40)))
        doc = parse(raw)
        ids = list(range(len(doc.flat)))
        assert write(doc, ids, tree_of(doc, ids))[0] == raw, repr(raw)
    for _ in range(300):
        data = bytes(rng.randrange(256) for _ in range(rng.randrange(300)))
        for raw in (data.decode("latin-1"), data.decode("utf-8", "surrogateescape")):
            doc = parse(raw)
            ids = list(range(len(doc.flat)))
            assert write(doc, ids, tree_of(doc, ids))[0] == raw, repr(raw)


def test_a_file_past_the_limits_has_a_problem_and_its_bytes():
    raw = "- x\n" * (200_001)
    doc = parse(raw)
    assert doc.problem
    assert write(doc, [], tree_of(doc))[0] == raw
    with pytest.raises(Unwritable):
        write(doc, [], [{"id": "a", "content": "b", "children": []}])
    deep = "".join(" " * (2 * i) + "- x\n" for i in range(md_lens.MAX_DEPTH + 2))
    assert parse(deep).problem
    assert write(parse(deep), [], [])[0] == deep


# --- the parser with spans off -----------------------------------------------------

def spans_off_digest(text: str) -> str:
    """What ``md_to_blocks`` and ``parse_frontmatter`` make of ``text``, however
    the importers call them, reduced to a digest."""
    fields, body = parse_frontmatter(text.replace("\r\n", "\n").replace("\r", "\n"))
    made = [md_to_blocks(text), md_to_blocks(body, outline=True), md_to_blocks(body, flat_headings=True), fields]
    return hashlib.sha256(json.dumps(made, ensure_ascii=False, sort_keys=True).encode("utf-8", "surrogatepass")).hexdigest()


def input_digest(text: str) -> str:
    return hashlib.sha256(text.replace("\r\n", "\n").encode("utf-8")).hexdigest()


def test_spans_off_is_the_parser_as_it_was():
    """``spans_off_digests.json`` holds, per input, the digest of what the parser
    made before ``spans`` existed (made by running that code, on every file the
    gate reads). An input edited since has no entry, so a doc's edit never fails
    this; the fixtures cannot change without the digest of a new parser's output
    being asked for here."""
    stored = json.loads(SPANS_OFF.read_text(encoding="utf-8"))
    for name, text in fixture_files():
        assert input_digest(text) in stored, f"{name}: no digest of what the parser made of it before spans"
    for name, text in [*repo_files(), *fixture_files()]:
        want = stored.get(input_digest(text))
        if want is not None:
            assert spans_off_digest(text) == want, name


def test_no_span_is_left_when_spans_are_off():
    for _, text in fixture_files():
        pending = md_to_blocks(text)
        while pending:
            node = pending.pop()
            assert set(node) == {"content", "children"}
            pending += node["children"]
