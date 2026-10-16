"""Plain .md uploads become note pages, including inside folder uploads."""

import json
from conftest import make_folder, workspace_of, guest_name
import sqlite3

import pytest

from gamma import markdown_import
from gamma.db import register_functions, ws_db_path
from gamma.markdown_import import TooManyBlocks, md_to_blocks


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
        "Opening paragraph.", "first", "## Details",
    ]
    assert overview["children"][1]["children"][0]["content"] == "nested"
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


def test_markdown_upload_strips_directory_from_multipart_filename(guest):
    r = guest.post(
        "/api/import/markdown",
        files={"file": (
            "spectrum_analyzer_data/CODE_INDEX.md",
            b"Index body",
            "text/markdown",
        )},
        data={"folder": make_folder(guest, "spectrum_analyzer_data")},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["title"] == "CODE_INDEX"
    assert body["original_filename"] == "CODE_INDEX.md"
    page = guest.get(f"/api/blocks/{body['block_id']}").json()
    assert page["content"] == "CODE_INDEX"
    assert page["properties"]["folders"] == [make_folder(guest, "spectrum_analyzer_data")]


def test_normalizer_repairs_old_automatic_path_title(guest):
    """An old markdown import whose title kept the leaked directory path is
    repaired by the content normalizer (a migration step / backup restore,
    gamma/normalize.py) — a library listing is a pure read."""
    from gamma.blocks_store import touch_page
    from gamma.normalize import normalize_pages_db

    created = guest.post(
        "/api/import/markdown",
        files={"file": ("CODE_INDEX.md", b"Index body", "text/markdown")},
    ).json()
    with sqlite3.connect(ws_db_path(workspace_of(guest_name()), "pages.db")) as conn:
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

    with sqlite3.connect(ws_db_path(workspace_of(guest_name()), "pages.db")) as conn:
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
    assert [b["content"] for b in items] == ["pasted-first", "pasted-second"]
    assert items[0]["children"][0]["content"] == "pasted-nested"

    with sqlite3.connect(ws_db_path(workspace_of(guest_name()), "pages.db")) as conn:
        n = conn.execute("SELECT COUNT(*) FROM unified_blocks "
                         "WHERE content LIKE 'pasted-%'").fetchone()[0]
    assert n == 0


def test_markdown_blocks_endpoint_caps_size(guest):
    r = guest.post("/api/markdown-blocks", json={"text": "x" * (5 * 1024 * 1024 + 10)})
    assert r.status_code == 413


def test_md_to_blocks_counts_every_block_against_the_cap(monkeypatch):
    """Nested blocks count; past MAX_BLOCKS the parse stops with
    TooManyBlocks rather than returning a tree cut short."""
    monkeypatch.setattr(markdown_import, "MAX_BLOCKS", 3)
    tree = md_to_blocks("# a\n- b\n  - c")
    assert tree[0]["children"][0]["children"][0]["content"] == "c"
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
    assert page["children"][-1]["content"] == "long-doc item 5999"

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