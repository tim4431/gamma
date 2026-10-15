"""gamma/colors.py — colours as stored (#rrggbbaa), every older spelling
read, the Logseq name mapping — and migration step 37, which gives every
stored highlight colour that spelling."""

import json
import sqlite3

from gamma import colors, migrations
from gamma.ink import parse_color as ink_parse_color
from gamma.logseq_graph_export import color_name
from gamma.logseq_import import map_color
from gamma.pdf_export import DEFAULT_COLOR, parse_css_color


def test_every_spelling_parses_and_the_stored_form_is_hex_with_alpha():
    assert colors.parse_color("rgba(255, 226, 143, 0.65)") == (1.0, 226 / 255, 143 / 255, 0.65)
    assert colors.parse_color("rgb(1,2,3)") == (1 / 255, 2 / 255, 3 / 255, 1.0)
    assert colors.parse_color("#abc") == (170 / 255, 187 / 255, 204 / 255, 1.0)
    assert colors.parse_color(" #FFE28F ") == (1.0, 226 / 255, 143 / 255, 1.0)
    assert colors.parse_color("#ffe28fa6") == (1.0, 226 / 255, 143 / 255, 166 / 255)
    assert colors.parse_color("rgba(300, 0, 0, 2)") == (1.0, 0.0, 0.0, 1.0)
    for bad in ("red", "", None, 3, "#ggg", "rgba(1, 2)", "hsl(1, 2%, 3%)"):
        assert colors.parse_color(bad) is None, bad

    # the legacy palette strings keep hue and alpha; only the spelling changes
    assert colors.normalize_color("rgba(255, 226, 143, 0.65)") == "#ffe28fa6"
    assert colors.normalize_color("rgba(170, 235, 170, 0.65)") == "#aaebaaa6"
    assert colors.normalize_color("#ffe28fa6") == "#ffe28fa6"
    assert colors.normalize_color("#FFE28F") == "#ffe28fff"
    assert colors.normalize_color("red") is None
    assert colors.with_alpha("#ffe28f", 0.5) == "#ffe28f80"
    assert colors.highlight_color(colors.WASH[0]) == colors.DEFAULT_HIGHLIGHT == "#ffe28fa6"
    assert colors.nearest("#e11d48", ("#dc2626", "#1d4ed8")) == "#dc2626"
    assert colors.nearest("#ffe28fa6", colors.WASH) == "#ffe28f"
    assert colors.nearest("nope", colors.WASH) is None


def test_the_export_and_ink_readers_take_both_spellings():
    assert parse_css_color("#ffe28fa6") == (1.0, 226 / 255, 143 / 255, 166 / 255)
    assert parse_css_color("rgba(255, 226, 143, 0.65)") == (1.0, 226 / 255, 143 / 255, 0.65)
    assert parse_css_color("#1f1f1f") == (31 / 255, 31 / 255, 31 / 255, 1.0)
    assert parse_css_color("nope") == DEFAULT_COLOR and parse_css_color(None) == DEFAULT_COLOR
    assert ink_parse_color("#dc2626") == (220 / 255, 38 / 255, 38 / 255, 1.0)
    assert ink_parse_color("rgba(255, 226, 143, 0.65)") == (1.0, 226 / 255, 143 / 255, 0.65)
    assert ink_parse_color("") == (0.12, 0.12, 0.12, 1.0)


def test_logseq_names_map_onto_the_wash_set_and_back():
    assert map_color("yellow") == "#ffe28fa6"
    assert map_color("GREEN ") == "#aaebaaa6"
    assert map_color("blue") == "#9bcdffa6"
    assert map_color("purple") == "#e6b4ffa6"
    assert map_color("orange") == "#fdba74a6"
    assert map_color("pink") == map_color("red") == "#f9a8d4a6"
    assert map_color("teal") == map_color(None) == "#ffe28fa6"
    # the export names the nearest Logseq colour, whatever the spelling or shade
    assert color_name("#aaebaaa6") == "green"
    assert color_name("rgba(170, 235, 170, 0.65)") == "green"
    assert color_name("#ffe28fa6") == "yellow"
    assert color_name("#fff3b0") == "yellow", "a paler yellow"
    assert color_name("#9bcdffa6") == "blue"
    assert color_name("#e6b4ffa6") == "purple"
    assert color_name("#f9a8d4a6") == "pink"
    assert color_name("#fdba74a6") == "orange"
    assert color_name(None) == color_name("nope") == "yellow"
    for name in ("yellow", "green", "blue", "purple", "pink", "orange"):
        assert color_name(map_color(name)) == name, name


def _pages_db(rows) -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE unified_blocks (id TEXT PRIMARY KEY, properties TEXT NOT NULL DEFAULT '{}', "
                 "updated_at TEXT NOT NULL DEFAULT 't0')")
    conn.executemany("INSERT INTO unified_blocks (id, properties) VALUES (?, ?)",
                     [(block_id, json.dumps(props)) for block_id, props in rows])
    return conn


def test_step_37_respells_every_stored_colour_and_nothing_else():
    rows = [
        ("legacy", {"color": "rgba(255, 226, 143, 0.65)", "quote": "q", "pdf_position": {"pageNumber": 1}}),
        ("hex6", {"color": "#9BCDFF"}),
        ("done", {"color": "#aaebaaa6"}),
        ("junk", {"color": "lime"}),
        ("none", {"quote": "no colour here"}),
        ("box", {"text_box": {"color": "#1f1f1f", "bg": None}}),  # a text box's colours are its own
    ]
    conn = _pages_db(rows)
    conn.execute("UPDATE unified_blocks SET properties = 'not json' WHERE id = 'none'")
    conn.execute("INSERT INTO unified_blocks (id, properties) VALUES ('broken', '{\"color\": ')")
    migrations._v37_highlight_colors("ws", conn, None)
    got = {block_id: raw for block_id, raw in conn.execute("SELECT id, properties FROM unified_blocks")}
    assert json.loads(got["legacy"]) == {"color": "#ffe28fa6", "quote": "q", "pdf_position": {"pageNumber": 1}}
    assert json.loads(got["hex6"]) == {"color": "#9bcdffff"}
    assert json.loads(got["done"]) == {"color": "#aaebaaa6"}
    assert json.loads(got["junk"]) == {}, "what is no colour goes; readers draw the default"
    assert json.loads(got["box"]) == rows[5][1]
    assert got["none"] == "not json" and got["broken"] == '{"color": ', "unreadable rows are left alone"
    assert {r[0] for r in conn.execute("SELECT updated_at FROM unified_blocks")} == {"t0"}, "a spelling is no edit"
    before = sorted(conn.execute("SELECT id, properties FROM unified_blocks"))
    migrations._v37_highlight_colors("ws", conn, None)  # re-runnable
    assert sorted(conn.execute("SELECT id, properties FROM unified_blocks")) == before

    empty = sqlite3.connect(":memory:")
    migrations._v37_highlight_colors("", empty, None)  # a copy without the table: nothing to do
    assert (37, "highlight_colors", migrations._v37_highlight_colors) in migrations.WORKSPACE_STEPS
    assert migrations.workspace_format() >= 37
