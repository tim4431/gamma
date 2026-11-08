"""Timing budgets on the server's hot paths (docs/dev/debugging.md
"Performance budgets"): a page of 1,000 blocks read, keystrokes and batches
committed, the text and ink merges. Each case checks what it computed, then
times it: 3 untimed warmups, 20 timed runs with a garbage collection before
each, and its nearest-rank p95 (of 20 runs the single slowest is left out)
held to a fixed budget, about 5x what an idle desktop measured.

Marked ``perf``, which pytest.ini leaves out of every run that does not ask
for it, and never under xdist: ``python -m pytest tests/test_perf.py -m perf
-q`` from backend/, on an idle machine (``-s`` prints each case's numbers)."""

import gc
import json
import math
import os
import random
import time

import orjson
import pytest

from conftest import account_of, login, make_user, workspace_of
from gamma import ink, textmerge
from gamma.blocks_store import fetch_subtree, insert_page_tree
from gamma.db import connect_pages_db
from gamma.json_response import OrjsonResponse
from gamma.markdown_export import build_tree
from gamma.ops import commit_ops, latest_seq

pytestmark = pytest.mark.perf

USER, PASSWORD = "pf_owner", "pw-pf-1"
PAGE = "pfpage000001"
BUDGETS_SET_ON = "a Ryzen 9 9950X3D, Python 3.12, idle (2026-11)"


@pytest.fixture(autouse=True, scope="module")
def _serial():
    """Timed alone: beside other xdist workers the numbers mean nothing."""
    if os.environ.get("PYTEST_XDIST_WORKER"):
        pytest.skip("timing budgets run serially: python -m pytest tests/test_perf.py -m perf")


def _rank(sorted_ms, q):
    """The nearest-rank ``q`` quantile of ascending samples."""
    return sorted_ms[min(len(sorted_ms), max(1, math.ceil(q * len(sorted_ms)))) - 1]


def measure(fn, *, warmup=3, runs=20, setup=None):
    """``fn(ctx)`` called ``warmup`` times untimed, then ``runs`` times timed,
    ``ctx`` being what ``setup()`` returns, called before each call outside
    the timed span. → ``{p50, p95, max}`` in milliseconds."""
    for _ in range(warmup):
        fn(setup() if setup else None)
    ms = []
    for _ in range(runs):
        ctx = setup() if setup else None
        gc.collect()
        t0 = time.perf_counter()
        fn(ctx)
        ms.append((time.perf_counter() - t0) * 1000)
    ms.sort()
    return {"p50": _rank(ms, 0.5), "p95": _rank(ms, 0.95), "max": ms[-1]}


def budget(request, result, limit):
    """The case held to ``limit`` ms at its p95: its numbers printed, and a
    row of the CI run's summary table when there is one. On GitHub Actions
    a case over its budget also prints a ``::warning`` line, which pytest
    repeats in the failure's captured output and the run shows as an
    annotation naming the case."""
    name = request.node.name
    p50, p95 = result["p50"], result["p95"]
    over = p95 > limit
    breach = f"{name}: p95 {p95:.2f} ms is over its budget of {limit} ms"
    print(f"\n{name}: p50 {p50:.2f} ms, p95 {p95:.2f} ms, budget {limit} ms")
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as f:
            f.write(f"| backend | {name} | {p50:.2f} | {p95:.2f} | {limit} | {'**over**' if over else 'ok'} |\n")
    if over and os.environ.get("GITHUB_ACTIONS") == "true":
        print(f"::warning title=perf budget::backend {breach}")
    assert not over, f"{breach} (budgets were set on {BUDGETS_SET_ON})"


WORDS = ("the of a spin field lattice measure state energy phase coupling transition model result "
         "figure data quantum mean order").split()


def _words(rnd, n):
    return " ".join(rnd.choice(WORDS) for _ in range(n))


def page_tree(rnd):
    """The frontend's budget page (frontend/tests/perf/fixtures.mjs
    annotatedPage): 400 highlights, 225 notes under them, 100 ink groups,
    10 sheets of 5 ink groups and 5 notes, 30 text boxes, 60 notes with
    two [[refs]], 75 notes (2 of them recordings, 20 stamped with t0).
    → (tree, highlight ids, the last note's id)"""
    n = [0]

    def bid(kind):
        n[0] += 1
        return f"pf{kind}{n[0]:06d}"

    def place(page):
        rects = []
        for _ in range(1 + rnd.randrange(4)):
            x1, y1 = rnd.random() * 500, rnd.random() * 700
            rects.append({"x1": x1, "y1": y1, "x2": x1 + 40 + rnd.random() * 60, "y2": y1 + 12,
                          "width": 612, "height": 792, "pageNumber": page})
        return {"pageNumber": page, "boundingRect": rects[0], "rects": rects}

    def block(kind, content, props=None, children=None):
        return {"id": bid(kind), "content": content, "properties": props or {}, "children": children or []}

    top, highlights = [], []
    for i in range(400):
        under = [] if i % 2 else [block("n", _words(rnd, 14), children=[] if i % 16 else [block("g", _words(rnd, 12))])]
        h = block("h", "" if i % 3 else _words(rnd, 6),
                  {"pdf_position": place(1 + i % 30), "quote": _words(rnd, 25), "color": "#ffe066aa"}, under)
        highlights.append(h["id"])
        top.append(h)
    for i in range(100):
        top.append(block("i", "", {"ink_url": f"/api/uploads/ink{i}.ink", "ink_strokes": 12, "pdf_position": place(1 + i % 30)}))
    for s in range(10):
        under = [block("k", "", {"ink_url": f"/api/uploads/sheet{s}_{k}.ink", "ink_strokes": 30}) for k in range(5)]
        under += [block("m", _words(rnd, 10)) for _ in range(5)]
        top.append(block("s", "", {"sheet": {"width": 612, "height": 792, "pattern": "lined", "spacing": 24}}, under))
    for i in range(30):
        top.append(block("t", _words(rnd, 8), {"text_box": {"x": rnd.random() * 400, "y": rnd.random() * 600, "w": 160, "h": 40,
                                                            "size": 12}, "pdf_page": 1 + i % 30}))
    for i in range(60):
        top.append(block("r", f"{_words(rnd, 6)} see [[{highlights[i * 7 % 400]}]] and [[{highlights[(i * 7 + 3) % 400]}]]"))
    t0 = 1757760000000
    for i in range(75):
        if i in (10, 40):
            props = {"recording": {"started": t0 + i * 60000, "state": "done", "segments": [
                {"url": f"/api/uploads/rec{i}.webm", "started": t0 + i * 60000, "dur": 300000, "bytes": 2400000, "mime": "audio/webm"}]}}
        else:
            props = {"t0": t0 + i * 1000} if i < 20 else {}
        top.append(block("f", _words(rnd, 16), props))
    return top, highlights, top[-1]["id"]


@pytest.fixture(scope="module")
def page():
    """The page written to the owner's workspace once for the module."""
    make_user(USER, PASSWORD)
    ws = workspace_of(USER)
    tree, highlights, last = page_tree(random.Random(7))
    with connect_pages_db(ws) as conn:
        assert insert_page_tree(conn, PAGE, "Timing budgets", {}, tree, actor=account_of(USER)) == 1000
        conn.commit()
    return {"ws": ws, "highlights": highlights, "last": last, "actor": account_of(USER)}


def _read(ws):
    with connect_pages_db(ws) as conn:
        rows = fetch_subtree(conn, PAGE)
        conn.rollback()
    return OrjsonResponse({"block": build_tree(rows, PAGE)})


def _walk(node):
    yield node
    for child in node["children"]:
        yield from _walk(child)


def test_subtree_read_of_a_1000_block_page(page, request):
    body = orjson.loads(_read(page["ws"]).body)["block"]
    kinds = {}
    for node in list(_walk(body))[1:]:
        kinds[node["kind"]] = kinds.get(node["kind"], 0) + 1
    assert kinds == {"highlight": 400, "note": 408, "ink": 150, "sheet": 10, "text_box": 30, "recording": 2}
    budget(request, measure(lambda _: _read(page["ws"])), 15)


def test_subtree_get_through_the_api(page, request):
    c = login(USER, PASSWORD)
    r = c.get(f"/api/blocks/{PAGE}/subtree")
    assert r.status_code == 200, r.text
    assert isinstance(r.json()["seq"], int) and len(r.json()["block"]["children"]) == 675
    budget(request, measure(lambda _: c.get(f"/api/blocks/{PAGE}/subtree").raise_for_status()), 35)


def _seq(ws):
    with connect_pages_db(ws) as conn:
        return latest_seq(conn, PAGE)


def _text(ws, block_id):
    with connect_pages_db(ws) as conn:
        return conn.execute("SELECT content FROM unified_blocks WHERE id = ?", (block_id,)).fetchone()[0]


def test_commit_ops_twenty_keystrokes(page, request):
    ws, target = page["ws"], page["last"]

    def type_keys(_):
        text = _text(ws, target)
        for _ in range(20):
            commit_ops(ws, PAGE, [{"op": "set", "id": target, "content": text + "x", "base": text}],
                       actor=page["actor"], client="pfkeys")
            text += "x"

    before, typed = _seq(ws), _text(ws, target)
    type_keys(None)
    assert _seq(ws) == before + 20
    assert _text(ws, target) == typed + "x" * 20
    budget(request, measure(type_keys), 12)


def test_commit_ops_a_50_op_batch(page, request):
    ws, highlights = page["ws"], page["highlights"]
    colors = iter(["#ff0000aa", "#00ff00aa"] * 20)

    def batch(_):
        color = next(colors)
        commit_ops(ws, PAGE, [{"op": "set", "id": h, "props": {"color": color}} for h in highlights[:50]],
                   actor=page["actor"], client="pfbatch")

    before = _seq(ws)
    batch(None)
    assert _seq(ws) == before + 1
    budget(request, measure(batch), 8)


_long = random.Random(11)
LONG = "\n\n".join(_words(_long, 80) for _ in range(40))  # 20,024 characters


def test_commit_ops_a_stale_set_merged_into_a_20000_character_block(page, request):
    ws, target = page["ws"], page["highlights"][-2]
    ours = LONG[:15000] + "OURS" + LONG[15000:]

    def theirs_first():
        # someone else's edit lands after ours was made from LONG
        commit_ops(ws, PAGE, [{"op": "set", "id": target, "content": LONG[:5000] + "THEIRS" + LONG[5000:]}],
                   actor=page["actor"], client="pftheirs")

    def stale(_):
        commit_ops(ws, PAGE, [{"op": "set", "id": target, "content": ours, "base": LONG}], actor=page["actor"], client="pfours")

    assert len(LONG) > 20000
    theirs_first()
    stale(None)
    assert _text(ws, target) == LONG[:5000] + "THEIRS" + LONG[5000:15000] + "OURS" + LONG[15000:]
    budget(request, measure(stale, setup=theirs_first), 10)


def _scattered(text, k, tag, rnd):
    out = list(text)
    for _ in range(k):
        out.insert(rnd.randrange(len(out)), tag)
    return "".join(out)


# an offline copy's round: 50 characters typed here and there on each side,
# each side's its own so the merge can be read back
_rnd = random.Random(3)
OURS_MANY, THEIRS_MANY = _scattered(LONG, 50, "\u00a7", _rnd), _scattered(LONG, 50, "\u00b6", _rnd)


def test_textmerge_50_scattered_edits_each_side(request):
    merged, clean = textmerge.merge(LONG, OURS_MANY, THEIRS_MANY)
    assert clean
    assert merged.replace("\u00b6", "") == OURS_MANY and merged.replace("\u00a7", "") == THEIRS_MANY
    budget(request, measure(lambda _: textmerge.merge(LONG, OURS_MANY, THEIRS_MANY)), 45)


def test_textmerge_map_offset_after_scattered_edits(request):
    merged, _ = textmerge.merge(LONG, OURS_MANY, THEIRS_MANY)
    at = textmerge.map_offset(OURS_MANY, merged, 12000)
    assert merged[:at].replace("\u00b6", "") == OURS_MANY[:12000], "the caret stays after the same text of ours"
    budget(request, measure(lambda _: textmerge.map_offset(OURS_MANY, merged, 12000)), 30)


def _drawing(strokes=500):
    """The frontend benches' drawing in kind: pens with baked widths and
    every tenth a highlighter, 60-140 samples each."""
    rnd = random.Random(7)
    out = []
    for i in range(strokes):
        hl, n = i % 10 == 9, 60 + rnd.randrange(80)
        x, y, dx, dy = 40 + rnd.random() * 520, 40 + rnd.random() * 700, rnd.random() - 0.5, rnd.random() - 0.5
        samples = []
        for j in range(n):
            dx += (rnd.random() - 0.5) * 0.4
            dy += (rnd.random() - 0.5) * 0.4
            x, y = x + dx * 0.6, y + dy * 0.6
            p = 0.3 + rnd.random() * 0.6
            samples.append({"x": x, "y": y, "p": p, "t": j * 8, "w": round(1 + (p - 0.5), 3)})
        ch = "xypt" if hl else "xyptw"
        out.append({"id": f"{'h' if hl else 's'}{i}", "tool": "highlighter" if hl else "pen",
                    "color": "#ffe066" if hl else "#1f1f1f", "size": 14 if hl else 1.6, "opacity": 0.6 if hl else 1,
                    "pen": True, "t0": 1757760000000, "ch": ch, "pts": ink.encode_points(samples, ch)})
    return {"format": "gamma-ink", "version": 2, "space": {"kind": "pdf-page", "page": 1, "width": 612, "height": 792},
            "strokes": out}


def test_ink_merge_of_500_strokes_changed_on_both_sides(request):
    base = _drawing()

    def recolour(strokes, lo, hi, color):
        return [dict(s, color=color) if lo <= i < hi else s for i, s in enumerate(strokes)]

    def added(prefix):
        return [dict(s, id=f"{prefix}{i}") for i, s in enumerate(base["strokes"][:5])]

    # ours recoloured the first 10 and drew 5; theirs recoloured 10 others,
    # erased the first 20 and drew 5
    ours = dict(base, strokes=recolour(base["strokes"], 0, 10, "#2463eb") + added("o"))
    theirs = dict(base, strokes=recolour(base["strokes"], 100, 110, "#c2255c")[20:] + added("t"))
    b, o, t = ink.parse_ink(base), ink.parse_ink(ours), ink.parse_ink(theirs)
    merged = ink.merge_ink(b, o, t)
    assert not merged.clean, "an edit beats a delete, and says so"
    assert len(merged.returned) == 10 and len(merged.ink.strokes) == 500
    budget(request, measure(lambda _: ink.merge_ink(b, o, t)), 110)


def test_ink_parse_of_500_strokes(request):
    raw = json.dumps(_drawing())
    assert len(ink.parse_ink(raw).strokes) == 500
    budget(request, measure(lambda _: ink.parse_ink(raw)), 140)
