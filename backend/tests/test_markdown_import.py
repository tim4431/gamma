"""Plain .md uploads become note pages, including inside folder uploads."""

import io
import json
from conftest import login, make_folder, make_user, workspace_of
import sqlite3
import time
import zipfile

import pytest

from gamma import markdown_import
from gamma.db import register_functions, ws_db_path
from gamma.markdown_import import TooManyBlocks, md_to_blocks, parse_frontmatter, readable_outline


def test_markdown_upload_creates_nested_note_page(guest):
    source = b"""---
title: Reading notes
---
# Overview

Opening paragraph.

- first
  - nested

## Details

More text.
"""
    week = make_folder(guest, "papers/week 1")
    r = guest.post(
        "/api/import/markdown",
        files={"file": ("paper-notes.md", source, "text/markdown")},
        data={"folder": week},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["title"] == "Reading notes"
    assert body["original_filename"] == "paper-notes.md"
    assert body["folders"] == [week]

    page = guest.get(f"/api/blocks/{body['block_id']}/subtree").json()["block"]
    assert page["content"] == "Reading notes"
    assert page["properties"]["folders"] == [week]
    assert page["properties"]["original_filename"] == "paper-notes.md"
    overview = page["children"][0]
    assert overview["content"] == "# Overview"
    assert [child["content"] for child in overview["children"]] == [
        "Opening paragraph.", "- first", "## Details",
    ]
    assert overview["children"][1]["children"][0]["content"] == "- nested"
    assert overview["children"][2]["children"][0]["content"] == "More text."


def test_markdown_upload_uses_filename_without_extension(guest):
    r = guest.post(
        "/api/import/markdown",
        files={"file": ("standalone.markdown", b"One paragraph", "text/plain")},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["title"] == "standalone"
    page = guest.get(f"/api/blocks/{body['block_id']}/subtree").json()["block"]
    assert [child["content"] for child in page["children"]] == ["One paragraph"]


def test_markdown_upload_keeps_a_slash_in_its_name(guest):
    """A "/" in the multipart filename is the name's, never a directory:
    a directory upload's folders come apart, and the web app sends each
    file's own name (fileRouting.js uploadName drops a picker's leaked
    path)."""
    folder = make_folder(guest, "spectrum_analyzer_data")
    r = guest.post(
        "/api/import/markdown",
        files={"file": ("I/O notes.md", b"Index body", "text/markdown")},
        data={"folder": folder},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["title"] == "I/O notes"
    assert body["original_filename"] == "I/O notes.md"
    page = guest.get(f"/api/blocks/{body['block_id']}").json()
    assert page["content"] == "I/O notes"
    assert page["properties"]["folders"] == [folder]


def test_normalizer_repairs_old_automatic_path_title():
    """An old markdown import whose title kept the leaked directory path is
    repaired by the content normalizer (a migration step / backup restore,
    gamma/normalize.py) — a library listing is a pure read. An account of
    its own: the normalizer reads the whole workspace, where other tests'
    names hold a "/" of their own."""
    from gamma.blocks_store import touch_page
    from gamma.normalize import normalize_pages_db

    make_user("md-normalize", "pw-md-normalize")
    guest = login("md-normalize", "pw-md-normalize")
    created = guest.post(
        "/api/import/markdown",
        files={"file": ("CODE_INDEX.md", b"Index body", "text/markdown")},
    ).json()
    with sqlite3.connect(ws_db_path(workspace_of("md-normalize"), "pages.db")) as conn:
        register_functions(conn)  # the notes index's triggers call textnorm
        row = conn.execute(
            "SELECT properties FROM unified_blocks WHERE id=?", (created["block_id"],)
        ).fetchone()
        props = json.loads(row[0])
        props["original_filename"] = "spectrum_analyzer_data/CODE_INDEX.md"
        conn.execute(
            "UPDATE unified_blocks SET content=?, properties=? WHERE id=?",
            ("spectrum_analyzer_data/CODE_INDEX", json.dumps(props), created["block_id"]),
        )
        conn.commit()

    children = guest.get("/api/blocks/root/children").json()["children"]
    listed = next(page for page in children if page["id"] == created["block_id"])
    assert listed["content"] == "spectrum_analyzer_data/CODE_INDEX"

    with sqlite3.connect(ws_db_path(workspace_of("md-normalize"), "pages.db")) as conn:
        register_functions(conn)  # the notes index's triggers call textnorm
        assert normalize_pages_db(conn)["upload_path_titles"] == 1
        touch_page(conn, created["block_id"], "")  # as the restore that runs it touches what it puts back
    repaired = guest.get(f"/api/blocks/{created['block_id']}").json()
    assert repaired["content"] == "CODE_INDEX"
    assert repaired["properties"]["original_filename"] == "CODE_INDEX.md"


def test_markdown_upload_rejects_non_utf8(guest):
    r = guest.post(
        "/api/import/markdown",
        files={"file": ("bad.md", b"\xff\xfe\xfa", "text/markdown")},
    )
    assert r.status_code == 400
    assert "UTF-8" in r.json()["detail"]


def test_markdown_blocks_endpoint_parses_without_storing(guest):
    """POST /api/markdown-blocks: the paste-as-blocks helper returns the parsed
    tree and writes nothing."""
    r = guest.post("/api/markdown-blocks", json={
        "text": "# Head\n\n- pasted-first\n  - pasted-nested\n- pasted-second"})
    assert r.status_code == 200, r.text
    tree = r.json()["blocks"]
    assert tree[0]["content"] == "# Head"
    items = tree[0]["children"]
    assert [b["content"] for b in items] == ["- pasted-first", "- pasted-second"]
    assert items[0]["children"][0]["content"] == "- pasted-nested"

    with sqlite3.connect(ws_db_path(workspace_of("md-normalize"), "pages.db")) as conn:
        n = conn.execute("SELECT COUNT(*) FROM unified_blocks "
                         "WHERE content LIKE 'pasted-%'").fetchone()[0]
    assert n == 0


def test_markdown_blocks_reads_gamma_s_own_outline_when_told(guest):
    """Gamma's copy of a subtree is an outline, every ``- `` a block, so a
    block that was ``x`` and one that was ``- y`` come back as they were; the
    same text from anywhere else is a list."""
    text = "- x\n  - - y\n  - 1. z"
    r = guest.post("/api/markdown-blocks", json={"text": text, "outline": True})
    assert r.status_code == 200, r.text
    assert _shape(r.json()["blocks"]) == [("x", ["- y", "1. z"])]
    r = guest.post("/api/markdown-blocks", json={"text": text})
    assert _shape(r.json()["blocks"]) == [("- x", ["- y", "1. z"])]


def test_markdown_blocks_endpoint_caps_size(guest):
    r = guest.post("/api/markdown-blocks", json={"text": "x" * (5 * 1024 * 1024 + 10)})
    assert r.status_code == 413


def test_md_to_blocks_counts_every_block_against_the_cap(monkeypatch):
    """Nested blocks count; past MAX_BLOCKS the parse stops with
    TooManyBlocks rather than returning a tree cut short."""
    monkeypatch.setattr(markdown_import, "MAX_BLOCKS", 3)
    tree = md_to_blocks("# a\n- b\n  - c")
    assert tree[0]["children"][0]["children"][0]["content"] == "- c"
    with pytest.raises(TooManyBlocks, match="more than 3 blocks"):
        md_to_blocks("# a\n- b\n  - c\n\npara")


def test_a_long_document_imports_whole_and_one_past_the_cap_is_refused(guest, monkeypatch):
    """6,000 blocks (past the 5,000 the parser used to keep) import whole;
    a document past MAX_BLOCKS is refused with the reason, never cut short."""
    text = "".join(f"- long-doc item {i}\n" for i in range(6000)).encode()
    r = guest.post("/api/import/markdown", files={"file": ("long.md", text, "text/markdown")})
    assert r.status_code == 200, r.text
    assert r.json()["imported"] == 6000
    page = guest.get(f"/api/blocks/{r.json()['block_id']}/subtree").json()["block"]
    assert page["children"][-1]["content"] == "- long-doc item 5999"

    monkeypatch.setattr(markdown_import, "MAX_BLOCKS", 5999)
    r = guest.post("/api/import/markdown", files={"file": ("longer.md", text + b"- one more\n", "text/markdown")})
    assert r.status_code == 413
    assert r.json()["detail"] == "Markdown file has more than 5,999 blocks"


def test_markdown_blocks_splits_a_long_paste_whole_or_refuses_it(guest, monkeypatch):
    """Paste as blocks: every block comes back, or a 413 (the editor keeps
    the paste as text and says why) — never the first N."""
    text = "\n".join(f"- pasted-long {i}" for i in range(6000))
    r = guest.post("/api/markdown-blocks", json={"text": text})
    assert r.status_code == 200, r.text
    assert len(r.json()["blocks"]) == 6000
    monkeypatch.setattr(markdown_import, "MAX_BLOCKS", 5999)
    r = guest.post("/api/markdown-blocks", json={"text": text})
    assert r.status_code == 413
    assert r.json()["detail"] == "text has more than 5,999 blocks"


def test_markdown_blocks_keeps_display_math_whole(guest):
    r"""Multi-line $$ math stays one block even when its rows look like list
    items or numbered lines (a shattered \begin{array} never renders)."""
    math = r"""$$
\begin{aligned}
- x + y &= 3 \\
1. & \text{numbered-looking row} \\
\end{aligned}
$$"""
    text = "Result:\n" + math + "\nDone."
    r = guest.post("/api/markdown-blocks", json={"text": text})
    assert r.status_code == 200, r.text
    tree = r.json()["blocks"]
    assert [b["content"] for b in tree] == ["Result:", math, "Done."]


def test_a_logseq_image_size_comes_in_as_the_alts(guest):
    """A Logseq ``{:width N}`` becomes the ``![alt|N]`` Gamma stores in every
    import that parses with md_to_blocks (here the paste as blocks); code
    keeps it as written."""
    text = ("- ![image.png](../assets/image_1.png){:width 300}\n"
            "- ```md\n  ![x](u){:width 4}\n  ```\n"
            "- keep `![y](u){:width 5}` as code")
    r = guest.post("/api/markdown-blocks", json={"text": text})
    assert r.status_code == 200, r.text
    assert [b["content"] for b in r.json()["blocks"]] == [
        "- ![image.png|300](../assets/image_1.png)",
        "```md\n![x](u){:width 4}\n```",
        "- keep `![y](u){:width 5}` as code",
    ]
    assert md_to_blocks("![a](/api/uploads/ab12.png){:width 240}\r\n")[0]["content"] == (
        "![a|240](/api/uploads/ab12.png)")


def test_md_to_blocks_keeps_a_task_box():
    """A task item keeps its ``- [ ]`` (a bare ``[ ]`` renders as text):
    Notion's two spaces after the box, ``*`` bullets and an upper-case X
    all come in as the editor's own form; a plain bullet and a number stay."""
    tree = md_to_blocks("- [ ]  open\n- [X] done\n* [x] star\n  - [ ] nested\n- plain\n1. one")
    assert [b["content"] for b in tree] == ["- [ ] open", "- [x] done", "- [x] star", "- plain", "1. one"]
    assert tree[2]["children"][0]["content"] == "- [ ] nested"


def _shape(blocks):
    """A tree as ``content``, or ``(content, children)`` where it has any."""
    return [(b["content"], _shape(b["children"])) if b["children"] else b["content"] for b in blocks]


# Block shapes the way ``render_readable`` writes them, with the tree the
# parser gave them before it had options.
OUTLINE_TREES = [
    ("- x", ["x"]),
    ("- x\n  - y", [("x", ["y"])]),
    ("- - x", ["- x"]),
    ("- - x\n  - - y", [("- x", ["- y"])]),
    ("- 1. x", ["1. x"]),
    ("- - [ ] x\n  - z", [("- [ ] x", ["z"])]),
    ("- - [x]", ["- [x]"]),
    ("- # H\n  - p", [("# H", ["p"])]),
    ("- > q\n  > r", ["> q\n> r"]),
    ("- > [!info] 💡\n  > body", ["> [!info] 💡\n> body"]),
    ("- ```py\n  x = 1\n  \n  - y\n  ```", ["```py\nx = 1\n\n- y\n```"]),
    ("- $$\n  a\n  $$", ["$$\na\n$$"]),
    ("- ---", ["---"]),
    ("- | a |\n  | --- |\n  | b |", ["| a |\n| --- |\n| b |"]),
    ("- a\n  b", ["a\nb"]),
    ("- a\n  \n  b", ["a\n\nb"]),
    ("- p\n  \n  - a\n  - b", [("p", ["a", "b"])]),
    ("- - a\n  - b", [("- a", ["b"])]),
    ("- - code\n    ```js\n    a();\n    ```", ["- code\n  ```js\n  a();\n  ```"]),
    ("- 1. ```py\n     x\n     ```", ["1. ```py\n   x\n   ```"]),
]


@pytest.mark.parametrize("text,tree", OUTLINE_TREES)
def test_outline_reads_every_bullet_as_a_separator(text, tree):
    assert _shape(md_to_blocks(text, outline=True)) == tree


@pytest.mark.parametrize("text,tree", [
    ("- item one\n  continues\n  - nested", [("- item one\n  continues", ["- nested"])]),
    ("* star\n+ plus", ["- star", "- plus"]),
    ("- ```python\n  x = 1\n\n  - y\n  ```\n- next", ["```python\nx = 1\n\n- y\n```", "- next"]),
    ("- code item\n  ```js\n  a();\n\n  b();\n  ```\n- next", [("- code item", ["```js\na();\n\nb();\n```"]), "- next"]),
    ("- T\n\n    ```py\n    x = 1\n    ```", [("- T", ["```py\nx = 1\n```"])]),
    ("- a\n  $$\n  x\n  $$\n- b", [("- a", ["$$\nx\n$$"]), "- b"]),
    ("- a\n  $$x$$\n- b", ["- a\n  $$x$$", "- b"]),
    ("1. a\n   ```py\n   x\n   ```\n2. b", [("1. a", ["```py\nx\n```"]), "2. b"]),
    ("- [ ] t\n  cont", ["- [ ] t\n  cont"]),
    ("1. a\n   b", ["1. a\n   b"]),
    ("- para\n\n  second", ["- para\n\n  second"]),
    ("- $$x$$ is it\n- |x| is it\n- >5", ["- $$x$$ is it", "- |x| is it", "- >5"]),
    ("- | a |\n  | - |\n  | 1 |\n- $$\n  a\n  $$\n- $$x$$", ["| a |\n| - |\n| 1 |", "$$\na\n$$", "$$x$$"]),
    ("- - x\n  - - [ ] y", [("- x", ["- [ ] y"])]),
    ("- Toggle\n\n    Hidden\n\n    > q", [("- Toggle", ["Hidden", "> q"])]),
    ("- Toggle\n\n    > one\n    > two\n\n    Last\n    line", [("- Toggle", ["> one\n> two", "Last\nline"])]),
    ("- [ ]\n- [x]", ["- [ ] ", "- [x] "]),
    ("- # H\n- ---\n- 1. x\n- 2020. A year\n- >\n- > q", ["# H", "---", "1. x", "2020. A year", ">", "> q"]),
])
def test_by_default_a_bullet_keeps_its_marker(text, tree):
    assert _shape(md_to_blocks(text)) == tree


def _note(body, *front):
    return parse_frontmatter("\n".join(["---", *front, "---", "", body]))


@pytest.mark.parametrize("body", [
    "# T\n",
    "# T\n\n- a\n- b\n",
    "# T\n\n- a\n  more\n  \n  more\n  - b\n",
    "# T\n\n- ```py\n  x\n  ```\n",
    "# T\n\n```bibtex\n@article{a,\n  title={T}\n}\n```\n\n- a\n",
    "\n\n# T\n- a",
    "# A title of its own\n\n- a\n",     # (a title with quotes differs from its H1)
])
def test_readable_outline_knows_the_shape_the_readable_export_writes(body):
    assert readable_outline(*_note(body, "title: T"))


@pytest.mark.parametrize("front,body", [
    (["tags: x"], "# T\n\n- a\n"),               # no title
    (["title: T"], "- a\n"),                     # no H1
    (["title: T"], "## T\n\n- a\n"),
    (["title: T"], "text\n\n# T\n\n- a\n"),
    (["title: T"], "# T\n\n- a\n\ntext\n"),      # an other line at the start of a line
    (["title: T"], "# T\n\n1. a\n"),
    (["title: T"], "# T\n\n* a\n"),
    (["title: T"], "# T\n\n```py\nx\n```\n\n- a\n"),
    (["title: T"], "# T\n\n```bibtex\n@a{b}\n"),  # a BibTeX block that never closes
])
def test_readable_outline_turns_down_any_other_note(front, body):
    assert not readable_outline(*_note(body, *front))


def test_a_note_with_no_front_matter_is_no_readable_export():
    assert not readable_outline(*parse_frontmatter("# T\n\n- a\n"))


@pytest.mark.parametrize("text,content", [
    ("- - x\n    y", "- x\n  y"),            # (on shape alone this gains indent every cycle)
    ("- - [ ] x\n    y", "- [ ] x\n  y"),
    ("- 1. x\n     y", "1. x\n   y"),
    ("  - - x\n      y", "- x\n  y"),
])
def test_an_unwrapped_block_s_next_lines_lose_the_text_column(text, content):
    assert _shape(md_to_blocks(text, outline=False)) == [content]


def test_a_kept_item_s_child_fence_stays_where_it_is_among_its_children():
    text = "- a\n  ```py\n  x\n  ```\n  after\n- b\n  - c\n  d\n"
    assert _shape(md_to_blocks(text, outline=False)) == [("- a", ["```py\nx\n```", "after"]), ("- b", ["- c", "d"])]


@pytest.mark.parametrize("outline", [True, False])
def test_a_fence_under_an_item_comes_without_its_indent(outline):
    item = "T" if outline else "- T"
    assert _shape(md_to_blocks("- T\n\n    ```py\n    if x:\n\ty = 1\n      z\n    ```\n", outline=outline)) == [
        (item, ["```py\nif x:\ny = 1\n  z\n```"])]
    # past the fence's indent a line is the code's, tabs and all
    assert _shape(md_to_blocks("- T\n\n\t```py\n\t\ty\n\t```\n", outline=outline)) == [(item, ["```py\n\ty\n```"])]
    assert _shape(md_to_blocks("```\n\tkept\n    too\n```", outline=outline)) == ["```\n\tkept\n    too\n```"]


@pytest.mark.parametrize("outline,block", [(True, "1. ```py\nx\n\n- y\n```"), (False, "1. ```py\n   x\n\n   - y\n   ```")])
def test_a_number_can_open_a_fence(outline, block):
    assert _shape(md_to_blocks("1. ```py\n   x\n\n   - y\n   ```\n2. next", outline=outline)) == [block, "2. next"]


@pytest.mark.parametrize("outline", [True, False])
@pytest.mark.parametrize("item", ["1. ```x``` y", "1.  ```x``` y", "1) ~~~x~~~ y"])
def test_a_number_s_inline_span_opens_no_fence(item, outline):
    assert _shape(md_to_blocks(f"{item}\n2. b\n\n# Next\n\nc", outline=outline)) == [
        " ".join(item.split()), "2. b", ("# Next", ["c"])]


@pytest.mark.parametrize("outline,content", [(True, "```x``` y"), (False, "- ```x``` y")])
def test_a_bullet_s_inline_span_opens_no_fence(outline, content):
    """A line that closes what it opens is inline code, so what follows it
    stays out of it."""
    tree = [content, ("- b" if not outline else "b"), ("# Next", ["c"])]
    assert _shape(md_to_blocks("- ```x``` y\n- b\n\n# Next\n\nc", outline=outline)) == tree
    tilde = content.replace("```", "~~~")
    assert _shape(md_to_blocks("- ~~~x~~~ y\n- b\n\n# Next\n\nc", outline=outline)) == [tilde, *tree[1:]]


@pytest.mark.parametrize("outline", [True, False])
def test_a_line_that_closes_its_own_fence_opens_none_anywhere(outline):
    # at the top, and on the next line of an item or below one
    assert _shape(md_to_blocks("```x``` y\nmore\n\nnext", outline=outline)) == ["```x``` y\nmore", "next"]
    item = "- a\n  ```x``` y" if not outline else "a\n```x``` y"
    assert _shape(md_to_blocks("- a\n  ```x``` y\n\nnext", outline=outline)) == [item, "next"]


@pytest.mark.parametrize("line", ["````", "```` py", "~~~~~", "```~~~", "``` py"])
def test_a_run_of_marks_opens_a_fence(line):
    assert markdown_import._FENCE_LINE_RE.match(line)


@pytest.mark.parametrize("line", ["```x``` y", "```` ```", "~~~x~~~", "`` x", "x ```"])
def test_a_line_with_no_fence_in_it_or_one_that_closes_opens_none(line):
    assert not markdown_import._FENCE_LINE_RE.match(line)


@pytest.mark.parametrize("line", [
    "`" * 6000 + " " + "`" * 6000,        # a run looked for along itself is cubic
    "~" * 6000 + " " + "~" * 6000,
    "`" * markdown_import.MAX_MARKDOWN_BYTES,
    "```" + " " * markdown_import.MAX_MARKDOWN_BYTES,
], ids=["backticks", "tildes", "one-run", "spaces"])
@pytest.mark.parametrize("wrap", ["{}", "- {}", "- a\n  {}"], ids=["top", "bullet", "next-line"])
def test_a_long_line_of_fence_marks_takes_no_time(line, wrap):
    """The two short lines are what a cubic pattern chokes on, short enough
    that one fails here (it holds the GIL, so every request waits) and does
    not hang the run; the two at the file cap are linear either way."""
    start = time.perf_counter()
    md_to_blocks(wrap.format(line))
    assert time.perf_counter() - start < 2


def test_a_longer_fence_is_a_fence_and_a_real_one_still_opens():
    assert _shape(md_to_blocks("- ````\n  a\n  ````\n- b")) == ["````\na\n````", "- b"]
    assert _shape(md_to_blocks("- ```py\n  x\n  ```\n- b")) == ["```py\nx\n```", "- b"]


@pytest.mark.parametrize("outline", [True, False])
@pytest.mark.parametrize("head,block", [
    ("- costs $$ and more", ("costs $$ and more", "- costs $$ and more")),
    ("- a\n  costs $$ and more", ("a\ncosts $$ and more", "- a\n  costs $$ and more")),
    ("1. costs $$ and more", ("1. costs $$ and more", "1. costs $$ and more")),
    ("- [ ] costs $$ and more", ("- [ ] costs $$ and more", "- [ ] costs $$ and more")),
    ("- $$ a $$ b $$", ("$$ a $$ b $$", "$$ a $$ b $$")),       # (its pair is on the line)
    ("- $$x$$ is it", ("$$x$$ is it", "- $$x$$ is it")),
], ids=["bullet", "next-line", "number", "task", "three", "pair"])
def test_a_dollar_pair_mid_line_opens_nothing(head, block, outline):
    """Only a line that starts with ``$$`` opens display math, in remark-math
    and here, so one ``$$`` in a sentence keeps the rest of the document out."""
    tree = [block[not outline], "next" if outline else "- next", ("# Head", ["text"])]
    assert _shape(md_to_blocks(f"{head}\n- next\n\n# Head\n\ntext", outline=outline)) == tree


def test_a_dollar_pair_mid_line_opens_nothing_at_the_top():
    assert _shape(md_to_blocks("costs $$ and more\n- next")) == ["costs $$ and more", "- next"]


@pytest.mark.parametrize("text,outline_block,kept_block,tail", [
    ("- $$\n  - x + y &= 3 \\\\\n  1. & a\n  $$", "$$\n- x + y &= 3 \\\\\n1. & a\n$$", None, "- next"),
    ("1. $$\n   - x\n   1. & a\n   $$", "1. $$\n- x\n1. & a\n$$", "1. $$\n   - x\n   1. & a\n   $$", "- next"),
    ("- $$ x\n  - y\n  $$", "$$ x\n- y\n$$", "- $$ x\n  - y\n  $$", "- next"),
    ("- [ ] $$\n  - y\n  $$", "- [ ] $$\n- y\n$$", "- [ ] $$\n  - y\n  $$", "- next"),
    ("- ```\n  $$\n  ```", "```\n$$\n```", None, "- next"),    # (a $$ in a fence is the code's)
    ("- $$\n  ```\n  $$", "$$\n```\n$$", None, "- next"),      # (a fence mark in math is the math's)
    ("- $$\n  $$y$$\n  $$", "$$\n$$y$$\n$$", None, "- next"),
], ids=["rows", "number", "text-after", "task", "dollars-in-fence", "fence-in-math", "pair-inside"])
@pytest.mark.parametrize("outline", [True, False])
def test_multi_line_math_in_an_item_stays_whole_to_its_close(text, outline_block, kept_block, tail, outline):
    block = outline_block if outline else (kept_block or outline_block)
    assert _shape(md_to_blocks(f"{text}\n{tail}", outline=outline)) == [block, "next" if outline else tail]


@pytest.mark.parametrize("outline,tree", [
    (True, ["a\n$$\n- x &= 3 \\\\\n1. & b\n$$", "next"]),
    (False, [("- a", ["$$\n- x &= 3 \\\\\n1. & b\n$$"]), "- next"]),
])
def test_math_on_an_item_s_next_line_stays_whole(outline, tree):
    assert _shape(md_to_blocks("- a\n  $$\n  - x &= 3 \\\\\n  1. & b\n  $$\n- next", outline=outline)) == tree


@pytest.mark.parametrize("close", ["  $$\n", "$$\n", "\n  $$\n", "\n$$\n"], ids=["aligned", "column-0", "blank", "blank-column-0"])
@pytest.mark.parametrize("outline", [True, False])
def test_a_line_s_dollars_close_one_left_mid_line_in_the_item(close, outline):
    """The block is one text to the editor, where ``scanMathSpans`` pairs the
    two wherever they stand: the second is no opener, or it would take all
    that follows."""
    tree = _shape(md_to_blocks(f"- is $$\n{close}- next\n\n# Head\n\ntext", outline=outline))
    assert tree[0].count("$$") == 2 and tree[1:] == ["next" if outline else "- next", ("# Head", ["text"])]


@pytest.mark.parametrize("outline,tree", [
    (True, ["costs $$ and more", "$$\nx\n$$", ("# Head", ["text", "next"])]),
    (False, ["- costs $$ and more", "$$\nx\n$$", ("# Head", ["text", "- next"])]),
])
def test_a_formula_at_the_top_is_no_close_for_one_left_mid_line(outline, tree):
    """Read as remark-math reads it: the formula's real close would open
    otherwise, with nothing to close it."""
    text = "- costs $$ and more\n\n$$\nx\n$$\n\n# Head\n\ntext\n\n- next\n"
    assert _shape(md_to_blocks(text, outline=outline)) == tree


@pytest.mark.parametrize("head,gap", [("- costs $$ and more", ""), ("- Use `$$` for it:", "\n")], ids=["below", "blank"])
def test_a_formula_in_the_item_is_no_close_for_one_left_mid_line(head, gap):
    text = f"{head}\n{gap}  $$\n  - x\n  $$\n- next\n\n# Head\n\ntext\n"
    tail = [("# Head", ["text"])]
    bare = head[2:]
    assert _shape(md_to_blocks(text, outline=True)) == [f"{bare}\n{gap}$$\n- x\n$$", "next"] + tail
    assert _shape(md_to_blocks(text)) == [(head, ["$$\n- x\n$$"]), "- next"] + tail


@pytest.mark.parametrize("outline,block,tail", [
    (True, "is $$\nx\n$$", "next"),
    (False, "- is $$\n  x\n  $$", "- next"),
])
def test_a_formula_past_the_item_leaves_the_close_in_the_item(outline, block, tail):
    """The search for a formula's own close stops where the item does."""
    text = "- is $$\n  x\n  $$\n- next\n\n$$\ny\n$$\n"
    assert _shape(md_to_blocks(text, outline=outline)) == [block, tail, "$$\ny\n$$"]


@pytest.mark.parametrize("outline,tree", [
    (True, ["a $$\n```\nc $$\n```\n$$", "next", "# Head"]),
    (False, [("- a $$", ["```\nc $$\n```", "$$"]), "- next", "# Head"]),
])
def test_a_fence_keeps_what_it_interrupted(outline, tree):
    """Its close gives the ``$$`` left mid-line back, so the last ``$$`` closes
    that one, after the fence in both modes."""
    assert _shape(md_to_blocks("- a $$\n  ```\n  c $$\n  ```\n  $$\n- next\n# Head\n", outline=outline)) == tree


@pytest.mark.parametrize("head,block", [
    ("- - - $$", "- - $$"),
    ("- * $$", "* $$"),
    ("- 1. $$", "1. $$"),
    ("- - [ ] $$", "- [ ] $$"),
], ids=["bullets", "star", "number", "task"])
@pytest.mark.parametrize("outline", [True, False])
def test_math_after_wrapped_bullets_stays_whole(head, block, outline):
    text = f"{head}\n  - x\n  $$\n- next\n"
    assert _shape(md_to_blocks(text, outline=outline)) == [f"{block}\n- x\n$$", "next" if outline else "- next"]


@pytest.mark.parametrize("outline", [True, False])
def test_many_items_left_mid_line_take_no_time(outline):
    """Each ``$$`` looks below itself, as far as the next one."""
    start = time.perf_counter()
    blocks = md_to_blocks("- a $$\n  $$\n\n" * 20000, outline=outline)
    assert time.perf_counter() - start < 2
    assert len(blocks) == 20000


@pytest.mark.parametrize("head", ["- a", "1. a", "- ```py", "1. ```py", "- $$", "- a $$ b"])
@pytest.mark.parametrize("outline", [True, False])
def test_an_item_of_many_lines_takes_no_time(head, outline):
    """Linear in lines and in bytes. A scan of the item on each line takes a
    minute on this, and a content grown by ``+=`` seven seconds, short enough
    that either fails here and does not hang the run."""
    text = head + "\n" + f"  {'x' * 1000}\n" * 8000
    start = time.perf_counter()
    blocks = md_to_blocks(text, outline=outline)
    assert time.perf_counter() - start < 2
    assert blocks[0]["content"].count("x" * 1000) == 8000


@pytest.mark.parametrize("outline", [True, False])
def test_an_empty_task_keeps_its_box(outline):
    assert _shape(md_to_blocks("- [ ]\n- [X]", outline=outline)) == ["- [ ] ", "- [x] "]
    assert _shape(md_to_blocks("- [x]y", outline=outline)) == ["[x]y" if outline else "- [x]y"]


def test_flat_headings_hold_nothing():
    text = "# One\n\nunder\n\n## Two\n\n- item\n  - nested\n\n# Three\n"
    assert _shape(md_to_blocks(text, flat_headings=True)) == ["# One", "under", "## Two", ("- item", ["- nested"]), "# Three"]
    assert _shape(md_to_blocks(text)) == [("# One", ["under", ("## Two", [("- item", ["- nested"])])]), "# Three"]


@pytest.mark.parametrize("value,text", [
    ('"Say \\"hi\\" [now]"', 'Say "hi" [now]'),
    ('"\\"double\\""', '"double"'),
    ('"back\\\\slash: x"', "back\\slash: x"),
    ('"C:\\foo"', "C:\\foo"),                       # by hand: a path, not a form feed
    ('"C:\\users"', "C:\\users"),                   # no JSON
    ('"a "pair" inside"', 'a "pair" inside'),
    ('""', ""),
    ("'it''s'", "it's"),
    ("'" * 4, "'"),
    ("'Tis", "'Tis"),
    ('Say "hi"', 'Say "hi"'),
    ('"open', '"open'),
    ('"', '"'),
])
def test_a_front_matter_value_loses_one_pair_of_quotes(value, text):
    assert parse_frontmatter(f"---\nkey: {value}\n---\n")[0]["key"] == text


@pytest.mark.parametrize("value", ['"\\ud83d"', '"a\\udc00b"'])
def test_a_front_matter_value_is_no_lone_surrogate(value):
    assert parse_frontmatter(f"---\nkey: {value}\n---\n")[0]["key"] == value[1:-1]
    assert parse_frontmatter('---\nkey: "\\ud83d\\ude00"\n---\n')[0]["key"] == "\U0001f600"


@pytest.mark.parametrize("route,name,media", [
    ("/api/import/markdown", "x.md", "text/markdown"),
    ("/api/import/markdown-zip", "x.zip", "application/zip"),
])
def test_a_lone_surrogate_in_a_title_fails_no_import(guest, route, name, media):
    note = b'---\ntitle: "\\ud83d"\n---\nbody'
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("a.md", note)
        zf.writestr("b.md", "good")
    r = guest.post(route, files={"file": (name, note if name == "x.md" else buf.getvalue(), media)})
    assert r.status_code == 200, r.text


def test_a_front_matter_list_reads_its_quotes_as_a_value_does():
    front = """---
aliases: ["Smith, J", 'it''s, so', plain, "q\\"r", it's]
authors:
  - "A \\"B\\""
  - 'C'
---
"""
    fields = parse_frontmatter(front)[0]
    assert fields["aliases"] == ["Smith, J", "it's, so", "plain", 'q"r', "it's"]
    assert fields["authors"] == ['A "B"', "C"]


@pytest.mark.parametrize("code", [
    "````md\n```\ninner\n```\n````",
    "~~~~\n~~~\ninner\n~~~\n~~~~",
    "```\n```py\ninner\n```",        # a line with more on it closes nothing
    "```\n~~~\n```",
    "~~~\n```\n~~~",
    "```\ninner\n`````",             # a longer one closes
])
def test_a_fence_closes_on_its_own_mark_at_least_as_long(code):
    assert _shape(md_to_blocks(f"{code}\n\n    after\n\nnext\n")) == [code, "after", "next"]
    assert _shape(md_to_blocks(f"- ````\n  ```\n  - x\n  ```\n  ````\n\n{code}\n")) == ["````\n```\n- x\n```\n````", code]


@pytest.mark.parametrize("code", [
    "```py\ndef f():\n    \"\"\"\n    ```\n    x\n    ```\n    \"\"\"\n```",
    "~~~\n\t~~~\n~~~",
])
def test_a_line_four_columns_past_its_fence_is_code(code):
    assert _shape(md_to_blocks(f"{code}\n\nnext\n")) == [code, "next"]
    moved = "\n".join(f"  {line}" for line in code.split("\n"))
    assert _shape(md_to_blocks(f"{moved}\n\nnext\n")) == [code, "next"]


def test_a_fence_closes_up_to_three_columns_past_its_own():
    assert _shape(md_to_blocks("```\ncode\n   ```\n\nnext\n")) == ["```\ncode\n   ```", "next"]


@pytest.mark.parametrize("text,tree", [
    ("- ````\n  ```\n  - b\n  ```\n  ````\n- c\n", ["````\n```\n- b\n```\n````", "c"]),
    ("- ~~~~\n  ~~~\n  - b\n  ~~~\n  ~~~~\n- c\n", ["~~~~\n~~~\n- b\n~~~\n~~~~", "c"]),
    ("- a\n  ````\n  ```\n  - b\n  ```\n  ````\n- c\n", ["a\n````\n```\n- b\n```\n````", "c"]),
    ("- is $$\n  ````\n  ```\n  - b\n  ```\n  ````\n  $$\n- c\n", ["is $$\n````\n```\n- b\n```\n````\n$$", "c"]),
    ("- ```\n  ```py\n  - b\n  ```\n- c\n", ["```\n```py\n- b\n```", "c"]),
])
def test_a_fence_in_an_item_closes_on_its_own_mark_too(text, tree):
    assert _shape(md_to_blocks(text, outline=True)) == tree


@pytest.mark.parametrize("line,block", [
    ("- ", "- "), ("1. ", "1. "), ("2) ", "2) "), ("- [ ] ", "- [ ] "), ("- [x]", "- [x] "),
    ("- - ", "- "), ("- 1. ", "1. "),          # wrapped, as an old vault wrote them
])
def test_an_empty_list_item_keeps_its_space(line, block):
    assert _shape(md_to_blocks(f"{line}\n", outline=False)) == [block]


@pytest.mark.parametrize("line,block", [("- - ", "- "), ("- 1. ", "1. "), ("- -", "-"), ("- 1.", "1."),
                                        ("- - [ ] ", "- [ ] "), ("- - [x] ", "- [x] ")])
def test_an_outline_keeps_the_space_of_an_empty_list_block(line, block):
    assert _shape(md_to_blocks(f"{line}\n", outline=True)) == [block]


def test_a_folder_sync_note_is_no_readable_export(guest):
    source = "---\ngamma_id: x\ntitle: What is X?\n---\n\n# Intro\n\n- a\n- b\n  - c\n"
    assert not readable_outline(*parse_frontmatter(source))
    r = guest.post("/api/import/markdown", files={"file": ("x.md", source.encode(), "text/markdown")})
    assert r.status_code == 200, r.text
    page = guest.get(f"/api/blocks/{r.json()['block_id']}/subtree").json()["block"]
    assert _shape(page["children"]) == [("# Intro", ["- a", ("- b", ["- c"])])]
