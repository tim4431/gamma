"""The Notability export (gamma/notability_export.py, gamma/notability_write.py;
docs/dev/import_export.md "Notability export"): a page out as a .note and
back in through Gamma's own Notability import is the page it was — its
sheets or its PDF, every stroke with every key and sample, its boxes,
pictures, notes and recordings — through what Notability holds and
Gamma's copy beside it; with the copy gone (Notability saved the note),
what Notability holds; and the .note is what Notability 16.1.5 writes,
class for class and key for key."""

import hashlib
import io
import math
import plistlib
import struct
import zipfile
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fractional_indexing import generate_n_keys_between

import notability_fixtures as fx
from conftest import login, make_user, workspace_of
from gamma import ink as inkmod
from gamma import jobs
from gamma import notability as nb
from gamma import notability_export as nexp
from gamma import notability_map as nmap
from gamma import notability_write as nw
from gamma import notebook, storage
from gamma.db import ws_uploads_dir
from gamma.text_box import normalize_text_box

SAMPLES = Path(__file__).parent / "samples" / "notability"
SAMPLE_NOTE = SAMPLES / "NOTE Note Apr 26, 2026.note"
A4 = notebook.normalize_paper(None)
T0 = 1_757_760_000_000


@pytest.fixture(scope="module")
def owner(client):
    make_user("nbx_owner", "nbx-password")
    return login("nbx_owner", "nbx-password")


@pytest.fixture(scope="module")
def ws(owner):
    return workspace_of("nbx_owner")


# --- making a page ---------------------------------------------------------------------------

def page(client, title: str, **props) -> str:
    r = client.post("/api/pages", json={"title": title})
    assert r.status_code == 200, r.text
    pid = r.json()["id"]
    if props:
        r = client.put(f"/api/blocks/{pid}", json={"properties": props})
        assert r.status_code == 200, r.text
    return pid


def insert(client, pid: str, parent: str, nodes: list[dict]) -> list[str]:
    """``nodes`` (``{id, content?, props?}``) under ``parent``, in order,
    after its children."""
    kids = client.get(f"/api/blocks/{parent}/children").json()["children"]
    last = max((k["position"] for k in kids), default=None)
    keys = generate_n_keys_between(last, None, n=len(nodes))
    r = client.post(f"/api/pages/{pid}/ops", json={"client": "t", "ops": [
        {"op": "insert", "id": n["id"], "parent": parent, "position": key, "content": n.get("content", ""),
         "props": n.get("props", {})} for n, key in zip(nodes, keys)]})
    assert r.status_code == 200, r.text
    return [n["id"] for n in nodes]


def ink_url(client, strokes: list[dict], space: dict) -> str:
    data = {"format": "gamma-ink", "version": 2, "space": space, "strokes": strokes}
    r = client.post("/api/upload-ink", content=inkmod.dumps(data), headers={"Content-Type": "application/json"})
    assert r.status_code == 200, r.text
    return r.json()["url"]


def stroke(sid: str, samples: list[dict], ch: str, **look) -> dict:
    return {"id": sid, "tool": "pen", "color": "#1f1f1f", "size": 1.6, "opacity": 1, "pen": True, "t0": T0,
            "ch": ch, **look, "pts": inkmod.encode_points(samples, ch)}


def trace(n: int, x0: float, y0: float, *, step=0.7, **channels) -> list[dict]:
    """``n`` samples along a wave from (x0, y0), on the 1/100 pt grid, each
    with ``channels`` (a function of the index, or a value)."""
    out = []
    for i in range(n):
        s = {"x": round(x0 + step * i, 2), "y": round(y0 + 6 * math.sin(i / 7), 2)}
        for name, v in channels.items():
            s[name] = v(i) if callable(v) else v
        out.append(s)
    return out


def shape_stroke(sid: str, kind: str, params: dict, **look) -> dict:
    samples = inkmod.shape_samples(kind, params)
    return {"id": sid, "tool": "pen", "color": "#2255aa", "size": 2, "opacity": 1, "pen": False, "t0": T0,
            "shape": {"kind": kind, "params": params}, **look, "ch": "xy",
            "pts": inkmod.encode_points([{"x": x, "y": y} for x, y in samples], "xy")}


def stored_url(ws: str, data: bytes, ext: str) -> str:
    name, _ = storage.store_file(ws, data, ext)
    return f"/api/uploads/{name}"


# --- out and back in --------------------------------------------------------------------------

def export(client, pid: str) -> bytes:
    r = client.get(f"/api/pages/{pid}/export", params={"mode": "notability"})
    assert r.status_code == 200, r.text
    assert r.headers["content-disposition"].endswith('.note"') or ".note" in r.headers["content-disposition"]
    return r.content


def reimport(client, data: bytes, name: str) -> dict:
    r = client.post("/api/import/notability", files={"file": (name, data, "application/zip")}, data={"folder": ""})
    assert r.status_code == 200, r.text
    report = r.json()
    assert report["pages_created"] == 1, report
    return report


def tree(client, pid: str) -> dict:
    return client.get(f"/api/blocks/{pid}/subtree").json()["block"]


def view(client, ws: str, pid: str) -> dict:
    """What a page is, to compare two: its title and PDF; its notes as a
    tree of contents; per surface (a sheet, a PDF page) its paper, its
    strokes in drawing order with every key but the id, its text boxes
    and pictures (a picture's file by its digest); its recordings (each
    file by its digest). Block ids, stroke ids and how the strokes are
    grouped into files are not the page's content and are left out."""
    root = tree(client, pid)
    uploads = ws_uploads_dir(ws)

    def digest(url: str) -> str:
        return hashlib.sha256((uploads / url.rsplit("/", 1)[-1]).read_bytes()).hexdigest()

    def notes(nodes):
        return [{"content": n["content"], "children": notes(n.get("children") or [])} for n in nodes
                if (n.get("kind") or "note") == "note"]

    def surface_of(blocks):
        files = [inkmod.read_upload(uploads, b["properties"]["ink_url"]) for b in blocks if b["kind"] == "ink"]
        strokes = [s for f in files for s in inkmod.in_draw_order(f.strokes)]
        out = {"strokes": [{k: v for k, v in inkmod.to_dict(s).items() if k not in ("id", "from")} for s in strokes],
               "boxes": [], "pictures": []}
        for b in blocks:
            if b["kind"] != "text_box":
                continue
            box = normalize_text_box(b["properties"]["text_box"])  # as every reader reads one
            picture = nexp._PICTURE.match(b["content"].strip())
            if picture:
                out["pictures"].append({"alt": picture.group("alt"), "box": box,
                                        "file": digest(picture.group("name"))})
            else:
                out["boxes"].append({"content": b["content"], "box": box})
        return out

    children = root.get("children") or []
    props = root["properties"]
    surfaces = []
    if props.get("doc_id"):
        by_page = {}
        for b in children:
            if b["kind"] == "ink":
                page_no = inkmod.read_upload(uploads, b["properties"]["ink_url"]).space.page
            elif b["kind"] == "text_box":
                page_no = b["properties"].get("pdf_page")
            else:
                continue
            by_page.setdefault(page_no, []).append(b)
        surfaces = [{"page": n, **surface_of(by_page[n])} for n in sorted(by_page)]
    else:
        surfaces = [{"paper": b["properties"]["sheet"], **surface_of(b.get("children") or [])}
                    for b in children if b["kind"] == "sheet"]
    recordings = []
    for b in children:
        if b["kind"] == "recording":
            rec = dict(b["properties"]["recording"])
            rec["segments"] = [{**{k: v for k, v in s.items() if k != "url"}, "file": digest(s["url"])}
                               for s in rec["segments"]]
            recordings.append({"content": b["content"], "recording": rec})
    order = []  # the page's notes, sheets and recordings in order; a PDF's handwriting and boxes as one
    for b in children:
        kind = b.get("kind") or "note"
        kind = "markup" if kind in ("ink", "text_box") else kind
        if kind in ("note", "sheet", "recording") or (kind == "markup" and "markup" not in order):
            order.append(kind)
    return {"title": root["content"], "doc_id": props.get("doc_id"), "notes": notes(children),
            "surfaces": surfaces, "recordings": recordings, "order": order}


def round_trip(client, ws: str, pid: str) -> tuple[dict, dict, bytes]:
    """The page's view, and the view of what its .note imports as."""
    data = export(client, pid)
    report = reimport(client, data, "Round trip.note")
    return view(client, ws, pid), view(client, ws, report["pages"][0]["id"]), data


# --- the round trips ---------------------------------------------------------------------------

def notebook_page(client, ws: str, title: str) -> str:
    """A notebook with a stroke of every kind Gamma draws, boxes, pictures,
    notes and recordings."""
    pid = page(client, title)
    tag = pid[:6]  # block ids of this page's own
    space = {"kind": "canvas", "width": A4["width"], "height": A4["height"]}
    tinted = notebook.normalize_paper({**A4, "color": "#fbf7ec", "pattern": "ruled", "spacing": 24})
    ruled = {**A4, "pattern": "ruled", "spacing": 24}
    sheets = insert(client, pid, pid, [{"id": f"{tag}S1", "props": {"sheet": ruled}},
                                       {"id": f"{tag}S2", "props": {"sheet": tinted}},
                                       {"id": f"{tag}S3", "props": {"sheet": A4}}])
    strokes = [
        stroke("pen", trace(40, 60, 80, p=lambda i: (300 + 15 * i) / 1000, t=lambda i: 8 * i, a=lambda i: 50 + i % 30,
                            z=lambda i: (90 + 7 * i) % 360, w=lambda i: 0.8 + (i % 9) / 20), "xyptazw"),
        stroke("mouse", trace(30, 60, 140, t=lambda i: 16 * i), "xyt", pen=False),
        stroke("hi", trace(25, 60, 200, p=0.5, t=lambda i: 10 * i), "xypt", tool="highlighter", color="#ffd400",
               size=14, opacity=0.6),
        stroke("pencil", trace(30, 60, 260, p=lambda i: (200 + 20 * i) / 1000, t=lambda i: 9 * i, a=60, z=120,
                               w=lambda i: 1 + i / 40, o=lambda i: 0.3 + i / 50), "xyptazwo", brush="pencil",
               color="#555555", size=1.2),
        stroke("dash", trace(20, 60, 320, t=lambda i: 12 * i), "xyt", pen=False, dash="dashed", color="#aa2222"),
        stroke("dot", trace(20, 60, 340, t=lambda i: 12 * i), "xyt", pen=False, dash="dotted", opacity=0.33),
        stroke("nib", trace(30, 60, 380, p=0.5, t=lambda i: 9 * i, w=lambda i: 0.3 + (i % 5) / 6), "xyptw",
               brush="calligraphy", nib=30, size=3),
        stroke("under", trace(15, 300, 80, t=lambda i: 9 * i), "xyt", pen=False, band=0,
               color="rgba(10, 120, 40, 0.5)"),
        stroke("extra", trace(10, 300, 120, t=lambda i: 9 * i), "xyt", pen=False, label="kept"),
        stroke("dotonly", [{"x": 320.5, "y": 160.25, "t": 0}], "xyt", pen=False),
        shape_stroke("line", "line", {"x1": 300, "y1": 200, "x2": 380.5, "y2": 240.25}),
        shape_stroke("arrow", "arrow", {"x1": 300, "y1": 260, "x2": 400, "y2": 230}, color="#cc0000"),
        shape_stroke("rect", "rect", {"cx": 350, "cy": 320, "w": 80, "h": 40, "angle": 30},
                     fill={"color": "#ffeeaa", "opacity": 0.5}),
        shape_stroke("ell", "ellipse", {"cx": 450, "cy": 320, "rx": 40, "ry": 20, "angle": 15}),
        shape_stroke("tri", "triangle", {"x1": 300, "y1": 420, "x2": 360, "y2": 400, "x3": 330, "y3": 460},
                     dash="dashed"),
        shape_stroke("poly", "polygon", {"cx": 460, "cy": 440, "r": 30, "n": 5, "angle": 10}),
        shape_stroke("hline", "line", {"x1": 60, "y1": 500, "x2": 260, "y2": 500}, tool="highlighter",
                     color="#88ccff", size=12, opacity=0.6),
    ]
    picture = fx.png(120, 80)
    animated = fx.gif(48, 32, frames=3)
    insert(client, pid, sheets[0], [
        {"id": f"{tag}Ink1", "props": {"ink_url": ink_url(client, strokes, space), "ink_strokes": len(strokes)}},
        {"id": f"{tag}Box1", "content": "Plain **bold** and *italic*, ~~gone~~ ==marked==",
         "props": {"text_box": {"x": 60, "y": 560, "w": 220, "h": 40, "auto": False, "size": 12,
                                "color": "#1f1f1f", "bg": None}}},
        {"id": f"{tag}Box2",
         "content": "# A title\n- one\n    - two\n1. first\n- [x] done\n[a link](https://example.org) $x^2$",
         "props": {"text_box": {"x": 300, "y": 560, "w": 240, "h": 120, "auto": True, "size": 16,
                                "color": "#aa0000", "bg": "#fff4b8"}}},
        {"id": f"{tag}Pic", "content": f"![a red block]({stored_url(ws, picture, '.png')})",
         "props": {"text_box": {"x": 60, "y": 640, "w": 128, "h": 88, "auto": False}}},
    ])
    long_stroke = stroke("long", trace(4000, 40, 120, step=0.12, p=0.5, t=lambda i: 4 * i), "xypt")
    insert(client, pid, sheets[1], [
        {"id": f"{tag}Ink2", "props": {"ink_url": ink_url(client, [long_stroke], space), "ink_strokes": 1}},
        {"id": f"{tag}Gif", "content": f"![]({stored_url(ws, animated, '.gif')})",
         "props": {"text_box": {"x": 200, "y": 300, "w": 56, "h": 40, "auto": False}}},
    ])
    first, second = fx.m4a(2500), fx.m4a(1800)
    one, two = stored_url(ws, first, ".m4a"), stored_url(ws, second, ".m4a")
    insert(client, pid, pid, [
        {"id": f"{tag}N1", "content": "Intro note"},
        {"id": f"{tag}N2", "content": "1. first point"},
        {"id": f"{tag}N3", "content": "- [ ] a task"},
        {"id": f"{tag}N4", "content": "line one\nline two"},
        {"id": f"{tag}N5", "content": ""},
        {"id": f"{tag}N6", "content": f"## A heading with [[{tag}N1]] and `code`"},
        {"id": f"{tag}Rec", "content": "Lecture audio", "props": {"recording": {
            "started": T0, "state": "done", "dur": 4300, "segments": [
                {"url": one, "started": T0, "dur": 2500, "bytes": len(first), "mime": "audio/mp4"},
                {"url": two, "started": T0 + 9000, "dur": 1800, "bytes": len(second), "mime": "audio/mp4"}]}}},
    ])
    insert(client, pid, f"{tag}N1", [{"id": f"{tag}N1a", "content": "a detail"},
                                     {"id": f"{tag}N1b", "content": "another"}])
    insert(client, pid, f"{tag}N1a", [{"id": f"{tag}N1a1", "content": "deeper still"}])
    return pid


def test_a_notebook_comes_back_as_it_was(owner, ws):
    pid = notebook_page(owner, ws, "Round trip notebook")
    before, after, _data = round_trip(owner, ws, pid)
    assert after == before
    assert before["order"] == ["sheet"] * 3 + ["note"] * 6 + ["recording"]  # as the page has them, not as an
    # import of a note orders a page (notes, sheets, recordings)


def without_copy(data: bytes) -> bytes:
    """The .note as Notability leaves it once it has saved the note: what
    it holds, without Gamma's copy (the root's ``nb.PRIVATE_KEY``)."""
    source = zipfile.ZipFile(io.BytesIO(data))
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_STORED) as target:
        for info in source.infolist():
            body = source.read(info)
            if info.filename.endswith("/Session.plist"):
                top = plistlib.loads(body)
                del top["$objects"][top["$top"]["$0"].data][nb.PRIVATE_KEY]
                body = plistlib.dumps(top, fmt=plistlib.FMT_BINARY, sort_keys=False)
            target.writestr(info, body)
    return out.getvalue()


def decoded(stroke: dict) -> list[dict]:
    return inkmod.decode_stroke(inkmod.Stroke.model_validate({**stroke, "id": "n0"}))


def by_start(strokes: list[dict]) -> dict:
    """Strokes by their first sample, which a stroke keeps however its
    channels change."""
    return {(decoded(s)[0]["x"], decoded(s)[0]["y"]): s for s in strokes}


def held(stroke: dict) -> dict:
    """What Notability holds of a stroke Gamma wrote, as the import reads
    it back without Gamma's copy: its samples' place, force, tilt and
    width (``p``, ``a``/``z``, ``w``; never the time or a pencil's
    opacity), its colour as #rrggbb at its drawn opacity, its size, tool,
    dash and a pencil's brush; a stylus stroke (``pen``) is one with
    force. A shape Notability has keeps its record; a regular polygon is
    its outline."""
    keep = {k: v for k, v in stroke.items() if k in ("tool", "size", "dash", "shape", "fill")}
    if stroke.get("brush") == "pencil":
        keep["brush"] = "pencil"
    if keep.get("shape", {}).get("kind") == "polygon":
        del keep["shape"]
        keep.pop("fill", None)
    r, g, b, a = inkmod.parse_color(stroke["color"])
    keep["color"] = "#%02x%02x%02x" % tuple(round(v * 255) for v in (r, g, b))
    opacity = round(a * stroke.get("opacity", 1), 2)
    if opacity < 1:
        keep["opacity"] = opacity
    ch = "xy" + "".join(c for c in "paz" if c in stroke["ch"]) + ("w" if "w" in stroke["ch"] else "")
    keep["ch"] = ch
    if "p" not in ch:
        keep["pen"] = False
    samples = [{c: s[c] for c in ch if c in s} for s in decoded(stroke)]
    keep["pts"] = inkmod.encode_points(samples, ch)
    if keep.get("tool") == "pen":
        del keep["tool"]
    return keep


def test_without_gamma_s_copy_what_notability_holds_comes_back(owner, ws):
    """Once Notability has saved the note, Gamma's copy is gone: every
    sample comes back where it was, with its force, tilt and width, every
    shape Notability has as its record, every box and note Notability can
    format as it was; the rest as Notability shows it."""
    pid = notebook_page(owner, ws, "Notability alone")
    data = export(owner, pid)
    report = reimport(owner, without_copy(data), "Notability alone.note")
    before, after = view(owner, ws, pid), view(owner, ws, report["pages"][0]["id"])
    # the sheets that hold something, on the first one's paper (Notability has one per note)
    assert [s["paper"] for s in after["surfaces"]] == [before["surfaces"][0]["paper"]] * 2
    for was, now in zip(before["surfaces"], after["surfaces"]):
        mine, theirs = by_start(was["strokes"]), by_start(now["strokes"])
        assert theirs.keys() == mine.keys()
        for start, stroke in mine.items():
            assert theirs[start] == held(stroke), stroke
        assert [p["file"] for p in now["pictures"]] == [p["file"] for p in was["pictures"]]
        assert [p["box"] for p in now["pictures"]] == [p["box"] for p in was["pictures"]]
    plain, rich = before["surfaces"][0]["boxes"]
    assert after["surfaces"][0]["boxes"] == [plain, {
        "content": "**A title**\n- one\n    - two\n1. first\n- [x] done\n\na link \\$x^2\\$",
        "box": {**rich["box"], "auto": False}}]
    assert after["notes"] == [n for n in before["notes"] if n["content"]][:4] + [
        {"content": "**A heading with Intro note and code**", "children": []}]
    assert [(r["content"], r["recording"]["started"], r["recording"]["dur"]) for r in after["recordings"]] == [
        ("Lecture audio", T0, 2500), ("Lecture audio", T0 + 9000, 1800)]
    assert after["order"] == ["note"] * 5 + ["sheet"] * 2 + ["recording"] * 2  # an import's own order


def pdf_page(client, ws: str, title: str) -> tuple[str, bytes]:
    """A PDF page of three pages of three sizes, with handwriting and
    boxes on them, a highlight and notes."""
    pdf = fx.pdf(((612, 792), (595.28, 841.89), (792, 612)))
    r = client.post("/api/uploads", files={"file": ("paper.pdf", pdf, "application/pdf")})
    assert r.status_code == 200, r.text
    doc = r.json()["doc_id"]
    pid = client.post(f"/api/blocks/by-doc/{doc}", json={"default_title": title}).json()["id"]
    tag = pid[:6]
    one = ink_url(client, [stroke("a", trace(30, 100, 100, p=lambda i: i / 40, t=lambda i: 9 * i), "xypt"),
                           shape_stroke("b", "rect", {"cx": 300, "cy": 400, "w": 120, "h": 60, "angle": 0})],
                  {"kind": "pdf-page", "page": 1, "width": 612, "height": 792})
    three = ink_url(client, [stroke("c", trace(50, 500, 300, t=lambda i: 9 * i, w=lambda i: 1 + i / 100), "xytw",
                                    pen=False, brush="brush")],
                    {"kind": "pdf-page", "page": 3, "width": 792, "height": 612})
    rect = {"x1": 100, "y1": 72, "x2": 300, "y2": 92}
    insert(client, pid, pid, [
        {"id": f"{tag}H", "content": "", "props": {
            "pdf_position": {"pageNumber": 1, "width": 612, "height": 792, "boundingRect": rect, "rects": [rect]},
            "color": "rgba(255, 226, 52, 0.65)", "quote": "quoted text"}},
        {"id": f"{tag}I1", "props": {"ink_url": one, "ink_strokes": 2}},
        {"id": f"{tag}I3", "props": {"ink_url": three, "ink_strokes": 1}},
        {"id": f"{tag}B1", "content": "On the first page", "props": {
            "text_box": {"x": 40, "y": 700, "w": 200, "h": 30, "auto": False, "size": 12, "color": "#1f1f1f",
                         "bg": None}, "pdf_page": 1}},
        {"id": f"{tag}B2", "content": "Second page, *bigger*", "props": {
            "text_box": {"x": 300, "y": 50, "w": 240, "h": 40, "auto": False, "size": 18, "color": "#004488",
                         "bg": "#dcecff"}, "pdf_page": 2}},
        {"id": f"{tag}N", "content": "What I think of it"},
    ])
    insert(client, pid, f"{tag}H", [{"id": f"{tag}HN", "content": "a note on the highlight"}])
    return pid, pdf


def test_a_pdf_page_comes_back_as_it_was_but_for_its_highlights(owner, ws):
    pid, pdf = pdf_page(owner, ws, "Round trip paper")
    before, after, data = round_trip(owner, ws, pid)
    assert after == before  # the highlight is no note's: the view compares notes, strokes and boxes
    assert after["doc_id"] == storage.content_digest(pdf)
    names = zipfile.ZipFile(io.BytesIO(data)).namelist()
    assert sum(1 for n in names if "/PDFs/" in n and n.endswith(".pdf")) == 1
    root = tree(owner, pid)
    _draft, warnings = nexp.page_draft([{**root, "parent_id": "root"}, *flat(root)], pid, ws_uploads_dir(ws))
    assert warnings == ["1 highlight is not in the note: Notability keeps no highlights on a PDF's text"]


def flat(node: dict) -> list[dict]:
    """A subtree's blocks below its root, each with its parent, as
    ``block_to_dict`` gives them."""
    out = []
    for child in node.get("children") or []:
        out.append({**child, "parent_id": node["id"]})
        out += flat(child)
    return out


@pytest.fixture(scope="module")
def sample_trip(owner, ws):
    """The sample's Note export imported, its page out as a .note and in
    again: ``(the first page's view, the second's, the .note)``."""
    report = reimport(owner, SAMPLE_NOTE.read_bytes(), SAMPLE_NOTE.name)
    return round_trip(owner, ws, report["pages"][0]["id"])


def test_the_sample_note_comes_back_as_it_came_in(sample_trip):
    """Notability's own note, imported, then out as a .note and in again:
    the same page, stroke for stroke, its stickies, shapes, pictures, GIF
    and notes."""
    before, after, _data = sample_trip
    assert after == before
    assert sum(len(s["strokes"]) for s in after["surfaces"]) == 871
    assert len(after["notes"]) == 8 and sum(len(s["pictures"]) for s in after["surfaces"]) == 8


def session_of(data: bytes, member: str = "Session.plist") -> bytes:
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        return zf.read(next(n for n in zf.namelist() if n.endswith("/" + member)))


def key_shapes(plist: bytes) -> tuple[dict, dict]:
    """A keyed archive's shape: for each class, each key's value as inline
    (its type) or behind a UID (the class or type it points at), over every
    object of the class; and each class's ``$classes`` and ``$classhints``."""
    top = plistlib.loads(plist)
    objects = top["$objects"]
    shapes, classes = {}, {}
    for o in objects:
        if isinstance(o, dict) and "$classname" in o:
            classes[o["$classname"]] = (o.get("$classes"), o.get("$classhints"))
        if not (isinstance(o, dict) and "$class" in o):
            continue
        name = objects[o["$class"].data]["$classname"]
        for key, value in o.items():
            if key == "$class":
                continue
            if isinstance(value, plistlib.UID):
                target = objects[value.data]
                kind = ("uid", objects[target["$class"].data]["$classname"] if isinstance(target, dict) and
                        "$class" in target else "null" if target == "$null" else type(target).__name__)
            else:
                kind = ("inline", type(value).__name__)
            shapes.setdefault(name, {}).setdefault(key, set()).add(kind)
    return shapes, classes


def test_the_note_is_written_as_notability_writes_one(sample_trip):
    """Every class the .note shares with Notability's own export of the
    sample is written with the same class chain and the same keys, each
    inline or behind a UID as the app writes it, pointing at what the app's
    points at: what its unarchiver decodes is where it looks. The app
    writes three keys this note has no use for (a cropped picture's path,
    a GIF's web address and the colour that needs it), and the note one of
    Gamma's own."""
    _before, _after, data = sample_trip
    for member in ("Session.plist", "metadata.plist"):
        theirs, their_classes = key_shapes(session_of(SAMPLE_NOTE.read_bytes(), member))
        ours, our_classes = key_shapes(session_of(data, member))
        for name in theirs.keys() & ours.keys():
            assert our_classes[name] == their_classes[name], name
            keys = theirs[name].keys() | ours[name].keys()
            differ = {k for k in keys if theirs[name].get(k) != ours[name].get(k)}
            assert differ <= {"$0", "remoteUrl", "UIRed-Double", "UIGreen-Double", "UIBlue-Double",
                              nb.PRIVATE_KEY}, (name, differ)
        assert set(theirs) - set(ours) <= {"NSURL"}
    zf = zipfile.ZipFile(io.BytesIO(data))
    infos = zf.infolist()
    folder = infos[0].filename.split("/")[0]
    assert all(i.filename.startswith(folder + "/") and i.compress_type == zipfile.ZIP_STORED for i in infos)
    assert {f"{folder}/{d}/" for d in ("Images", "PDFs", "Recordings", "HandwritingIndex", "Assets")} <= {
        i.filename for i in infos}
    thumb = zf.read(f"{folder}/thumb12x.png")
    assert thumb[:8] == b"\x89PNG\r\n\x1a\n" and thumb[16:24] == bytes.fromhex("00000240000002f4")  # 576 × 756


def edited(data: bytes, change) -> bytes:
    """The .note with its Session.plist's objects changed by ``change``
    (``$objects``), the way an app that keeps Gamma's copy would edit it."""
    source = zipfile.ZipFile(io.BytesIO(data))
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_STORED) as target:
        for info in source.infolist():
            body = source.read(info)
            if info.filename.endswith("/Session.plist"):
                top = plistlib.loads(body)
                change(top["$objects"])
                body = plistlib.dumps(top, fmt=plistlib.FMT_BINARY, sort_keys=False)
            target.writestr(info, body)
    return out.getvalue()


def test_what_notability_changed_comes_in_as_notability_has_it(owner, ws):
    """Gamma's copy of an item counts only while the note still holds that
    item: a stroke moved in the note comes in moved, from what the note
    holds (no time), and the notes typed over come in as typed, while the
    rest keeps every key from the copy."""
    pid = notebook_page(owner, ws, "Edited in Notability")
    data = export(owner, pid)

    def change(objects):
        ink = next(o for o in objects if isinstance(o, dict) and "curvespoints" in o and o["curvespoints"])
        n = int.from_bytes(ink["curvesnumpoints"][:4], "little")  # the first stroke: the highlighter, band 0
        points = bytearray(ink["curvespoints"])
        for i in range(0, 2 * n, 2):
            x = struct.unpack_from("<f", points, 4 * i)[0]
            struct.pack_into("<f", points, 4 * i, x + 10)
        ink["curvespoints"] = bytes(points)
        rich = objects[objects[objects[1]["richText"].data]["attributedString"].data]
        text = objects[rich["NS.objects"][0].data]
        objects[rich["NS.objects"][0].data] = text.replace("Intro note", "Intro notes")

    report = reimport(owner, edited(data, change), "Edited.note")
    before, after = view(owner, ws, pid), view(owner, ws, report["pages"][0]["id"])
    mine = by_start(before["surfaces"][0]["strokes"])
    theirs = by_start(after["surfaces"][0]["strokes"])
    moved = [s for k, s in theirs.items() if k not in mine]
    assert len(moved) == 1 and moved[0]["tool"] == "highlighter" and "t" not in moved[0]["ch"]
    assert "t0" not in moved[0]
    for start, stroke in theirs.items():
        if start in mine:
            assert stroke == mine[start]
    assert after["notes"][0]["content"] == "Intro notes"  # the note's text: the copy no longer matches it
    assert after["surfaces"][0]["boxes"] == before["surfaces"][0]["boxes"]


def test_what_a_note_cannot_hold_is_said(owner, ws):
    """A page's highlights, a recording that is no .m4a or is still being
    made, grid paper and a sheet Notability has no size for are not in the
    note, and the export says so; the rest comes back."""
    pid = page(owner, "What a note cannot hold")
    tag = pid[:6]
    odd = notebook.normalize_paper({"width": 500, "height": 500, "pattern": "grid", "spacing": 20})
    insert(owner, pid, pid, [
        {"id": f"{tag}S", "props": {"sheet": odd}},
        {"id": f"{tag}W", "content": "Web audio", "props": {"recording": {
            "started": T0, "state": "done", "dur": 1000, "segments": [
                {"url": stored_url(ws, b"\x1aE\xdf\xa3webm", ".webm"), "started": T0, "dur": 1000, "bytes": 8,
                 "mime": "audio/webm"}]}}},
        {"id": f"{tag}L", "content": "Live", "props": {"recording": {"started": T0, "state": "recording",
                                                                     "segments": []}}},
    ])
    insert(owner, pid, f"{tag}W", [{"id": f"{tag}WT", "content": "a transcript line"}])
    r = owner.post("/api/jobs/export", json={"page_id": pid, "mode": "notability"})
    assert r.status_code == 200, r.text
    jobs.wait(r.json()["id"])
    job = owner.get(f"/api/jobs/{r.json()['id']}").json()
    assert job["state"] == "done", job.get("error")
    assert job["artifact"]["name"] == "What a note cannot hold.note"
    assert sorted(w["reason"] for w in job["result"]["warnings"]) == sorted([
        "1 recording is not an .m4a file (Notability plays only those) and is left out",
        "1 recording still being made is left out",
        "1 note under a recording is left out",
        "A sheet's size is not one of Notability's (letter, A4, legal): its page is 612 × 803.25 pt",
        "Notability shows grid paper as plain: a note keeps only ruled lines",
    ])


def test_a_stroke_is_its_samples_as_a_straight_chain_under_a_gamma_uuid():
    """A stroke is written as a Bézier chain whose segments are straight
    (Notability draws the polyline its samples make), its samples the
    anchors with their widths and forces; its UUID tells the reader so. One
    past MAX_ANCHORS is cut into curves of one UUID, each starting where the
    last ended, which the reader makes one curve again."""
    n = nw.MAX_ANCHORS + 1000
    anchors = [(10 + 0.25 * i, 20 + math.sin(i / 50)) for i in range(n)]
    draft = nw.Draft(title="Chain", width=612.0)
    widths = [1 + i / n for i in range(n)]
    draft.strokes.append(nw.Stroke(anchors=anchors, width=1.5, rgba=(10, 20, 30, 255), widths=widths,
                                   uuid=nb.gamma_uuid()))
    top = plistlib.loads(session_of(nw.note_bytes(draft)))
    ink = next(o for o in top["$objects"] if isinstance(o, dict) and "curvespoints" in o)
    counts = struct.unpack(f"<{len(ink['curvesnumpoints']) // 4}i", ink["curvesnumpoints"])
    assert counts == (3 * nw.MAX_ANCHORS - 2, 3 * (n - nw.MAX_ANCHORS + 1) - 2)  # the second starts on the first's end
    uuids = ink["curveUUIDs"]
    assert uuids[:16] == uuids[16:] and nb.is_gamma(uuids[:16])
    assert len(ink["curvesstyles"]) == 1 and len(ink["options"]) == 8  # per stroke, not per curve
    pts = struct.unpack(f"<{len(ink['curvespoints']) // 4}f", ink["curvespoints"])
    (x0, y0), (x1, y1) = anchors[0], anchors[1]
    for got, want in zip(pts[:8], (x0, y0, x0 + (x1 - x0) / 3, y0 + (y1 - y0) / 3,
                                   x0 + 2 * (x1 - x0) / 3, y0 + 2 * (y1 - y0) / 3, x1, y1)):
        assert got == pytest.approx(want, abs=1e-4)
    members = fx_members(nw.note_bytes(draft))
    note = nb.read_note(members, members.find("Session.plist").read(), path="Chain.note")
    [curve] = note.curves
    assert curve.gamma == uuids[:16] and curve.anchors == n
    frame = nmap.frames(note, {}, [100.0])[0][0]
    samples = decoded({**nmap.exact_stroke(curve, frame)})
    assert [(s["x"], s["y"]) for s in samples] == [(round(x - frame.ox, 2), round(y, 2)) for x, y in anchors]
    assert [s["w"] for s in samples] == [round(1 + i / n, 3) for i in range(n)]


def fx_members(data: bytes) -> nb.Members:
    """A .note's files as the import finds them."""
    import contextlib

    from gamma.import_zip import new_walk, walk_zip
    with contextlib.ExitStack() as stack:
        walk = new_walk(stack, max_directory=1 << 24)
        entries = []
        walk_zip(zipfile.ZipFile(io.BytesIO(data)), "", entries, walk, nested=nb.nested_zip)
        members = nb.note_members(entries)
        return nb.Members({k: _Read(e.read()) for k, e in members.entries.items()})


class _Read:
    """A member already read, as a ZipEntry answers ``read``."""

    def __init__(self, data: bytes):
        self.data, self.size = data, len(data)

    def read(self) -> bytes:
        return self.data


def test_the_metadata_names_the_note_unfiled():
    created = datetime(2026, 10, 1, 12, 30, tzinfo=UTC)
    draft = nw.Draft(title="Lecture: 7/9", width=612.0, created=created)
    data = nw.note_bytes(draft)
    assert zipfile.ZipFile(io.BytesIO(data)).namelist()[0] == "Lecture 7 9/"  # a folder's name holds no / or :
    meta = fx_members(data)
    title, subject, when = nb.read_meta(meta)
    assert (title, when) == ("Lecture: 7/9", created) and subject in nb.UNFILED


def test_a_folder_exports_each_page_as_a_note_in_one_zip(owner, ws):
    from conftest import make_folder
    folder = make_folder(owner, "Notability out")
    sub = make_folder(owner, "Notability out/Week 2")
    first, second = page(owner, "First note"), page(owner, "Second note")
    for pid, where in ((first, folder), (second, sub)):
        r = owner.put(f"/api/blocks/{pid}", json={"properties": {"folders": [where]}})
        assert r.status_code == 200, r.text
        insert(owner, pid, pid, [{"id": f"{pid[:6]}N", "content": f"a note of {pid}"}])
    r = owner.get(f"/api/folders/{folder}/export", params={"mode": "notability"})
    assert r.status_code == 200, r.text
    zf = zipfile.ZipFile(io.BytesIO(r.content))
    assert sorted(zf.namelist()) == ["First note.note", "Week 2/Second note.note"]
    note = zf.read("Week 2/Second note.note")
    assert reimport(owner, note, "Second note.note")["pages"][0]["title"] == "Second note"
