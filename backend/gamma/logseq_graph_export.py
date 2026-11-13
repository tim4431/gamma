"""Export a page as a real Logseq file graph, in the structures Logseq's PDF
annotation system reads:

- ``pages/<stem>.md``          — the user's notes; highlights become ``((uuid))``
                                 block refs into the hls page.
- ``pages/hls__<stem>.md``     — the annotations page (``ls-type:: annotation``
                                 blocks with UUID ids), named after the PDF asset.
- ``assets/<stem>.pdf``        — the PDF, renamed from its sha to the page stem
                                 so the ``hls__`` naming convention holds.
- ``assets/<stem>.edn``        — highlight geometry (``{:highlights [...]}``).
- ``assets/<stem>/<page>_<uuid>_<stamp>.png`` — area-highlight crops, rendered
                                 with PyMuPDF (skipped silently if rendering
                                 fails; the annotation block still exports).
- ``logseq/config.edn``        — minimal marker so importers accept the folder.

File-based Logseq ("OG") reads this directly (drop into a graph / open as one);
the DB version converts it — hls pages and EDN included — via its built-in
"File to DB graph" importer, so this one format serves both apps.

Highlight UUIDs are uuid5 of the highlight's block id, so re-exports are stable.
Positions transfer as they are: Gamma's ``pdf_position`` uses the same
rects/bounding model as Logseq's EDN, its page size stored once where the EDN
repeats it in every rect (see ``logseq_import.edn_highlight_position``, the
way in).
"""

import uuid

from .documents.model import page_document, source_address
from .highlights import is_highlight, is_link, page_of
from .colors import logseq_name
from .pdf_text import page_rasters
from .picture import is_picture, markdown_of

CONFIG_EDN = "{:meta/version 1}\n"

_UUID_NS = uuid.uuid5(uuid.NAMESPACE_URL, "gamma-logseq-highlight")


def hl_uuid(block_id):
    return uuid.uuid5(_UUID_NS, str(block_id))


def hl_stamp(u):
    """Deterministic Logseq-style stamp (they use epoch ms; only consistency
    between the md property, the EDN entry and the image filename matters)."""
    return int.from_bytes(u.bytes[:6], "big") % 10**13


def color_name(color):
    """A stored highlight colour → the Logseq colour name nearest to it."""
    return logseq_name(color)


# --- highlight collection ----------------------------------------------------

def collect_highlights(page):
    """Walk the page tree in document order and describe every annotation-able
    highlight (link regions are navigation aids, not annotations)."""
    out = []

    def walk(node):
        props = node.get("properties") or {}
        if is_highlight(props) and not is_link(props):
            u = hl_uuid(node["id"])
            pos = props["pdf_position"]
            quote = (props.get("quote") or "").strip()
            out.append({
                "uuid": u,
                "quote": quote,
                "color": color_name(props.get("color")),
                "page": page_of(props) or 1,
                "position": pos,
                "area": bool(pos.get("area")) or (not quote and "boundingRect" in pos),
                "stamp": hl_stamp(u),
            })
        for child in node.get("children", []):
            walk(child)

    walk(page)
    return out


# --- hls__ page + EDN --------------------------------------------------------

def render_hls_md(stem, highlights):
    lines = [f"file-path:: ../assets/{stem}.pdf", ""]
    def top(h):
        br = ((h["position"] or {}).get("boundingRect") or {})
        return br.get("y1") or 0
    for h in sorted(highlights, key=lambda h: (h["page"], top(h))):
        lines.append(f"- {' '.join(h['quote'].split()) if h['quote'] else '[:span]'}")
        lines.append("  ls-type:: annotation")
        lines.append(f"  hl-page:: {h['page']}")
        lines.append(f"  hl-color:: {h['color']}")
        lines.append(f"  id:: {h['uuid']}")
        if h["area"]:
            lines.append("  hl-type:: area")
            lines.append(f"  hl-stamp:: {h['stamp']}")
    return "\n".join(lines).rstrip() + "\n"


def _edn_scalar(v):
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, float):
        return repr(v)
    if isinstance(v, int):
        return str(v)
    if v is None:
        return "nil"
    s = str(v).replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n").replace("\t", "\\t")
    return f'"{s}"'


def _edn_rect(r, pos):
    """One rect as the EDN has it: the position's page size in every rect."""
    values = {**r, "width": pos.get("width", 0), "height": pos.get("height", 0)}
    keys = ("x1", "y1", "x2", "y2", "width", "height")
    return "{" + " ".join(f":{k} {_edn_scalar(values.get(k, 0))}" for k in keys) + "}"


def render_edn(highlights):
    entries = []
    for h in highlights:
        pos = h["position"]
        if not pos or not pos.get("boundingRect"):
            continue  # geometry unknown (e.g. imported without EDN) — md-only
        rects = " ".join(_edn_rect(r, pos) for r in pos.get("rects") or [])
        content = f':text {_edn_scalar(h["quote"])}'
        if h["area"]:
            content = f':text "[:span]" :image {h["stamp"]}'
        entries.append(
            "{"
            f':id #uuid "{h["uuid"]}" '
            f':page {h["page"]} '
            f':position {{:bounding {_edn_rect(pos["boundingRect"], pos)} :rects [{rects}] :page {h["page"]}}} '
            f":content {{{content}}} "
            f':properties {{:color "{h["color"]}"}}'
            "}"
        )
    return "{:highlights [" + "\n ".join(entries) + "]\n :extra {:page 1}}\n"


# --- user page ---------------------------------------------------------------

def render_graph_page_md(page, stem, has_pdf):
    """Logseq-dialect page whose highlight blocks are ``((uuid))`` refs into the
    hls page (the native shape of notes made from Logseq's PDF viewer)."""
    props = page.get("properties") or {}
    title = (page.get("content") or "").strip() or "Untitled"

    lines = [f"title:: {title}"]
    src = source_address(page_document(props))
    if src and not src.startswith("/api/uploads/"):
        lines.append(f"source:: {src}")
    lines.append("")
    if has_pdf:
        lines.append(f"- ![{title}](../assets/{stem}.pdf)")

    for child in page["children"]:
        _render_block(child, 0, lines, has_pdf)
    return "\n".join(lines).rstrip() + "\n"


def _oneline(text):
    return " ".join((text or "").split("\n")).strip()


def _render_block(node, depth, lines, has_pdf):
    props = node.get("properties") or {}
    content = (node.get("content") or "").strip()
    tabs = "\t" * depth

    if props.get("link_url"):
        label = content or (props.get("quote") or "").strip() or props["link_url"]
        lines.append(f"{tabs}- [{_oneline(label)}]({props['link_url']})")
    elif is_highlight(props) and not is_link(props):
        if has_pdf:
            bullet = f"(({hl_uuid(node['id'])}))"
        else:
            # No PDF asset in the graph → no hls page to point at; keep the
            # quote itself so the note still reads.
            bullet = _oneline(props.get("quote") or "") or _oneline(content) or "[:span]"
        lines.append(f"{tabs}- {bullet}")
        note = _oneline(content)
        if note and note != bullet:
            child_tabs = "\t" * (depth + 1)
            lines.append(f"{child_tabs}- {note}")
        for child in node["children"]:
            _render_block(child, depth + 1, lines, has_pdf)
        return
    elif is_picture(props) and (picture := markdown_of(content, props["picture"])):
        # A picture: its image line, bundled as a note's image is, and no
        # page, as a text box has none.
        lines.append(f"{tabs}- {picture}")
    elif content:
        # A note, a text box included: Logseq keeps a page (hl-page) only on
        # annotation blocks, and a text box is not one.
        lines.append(f"{tabs}- {_oneline(content)}")
    else:
        for child in node["children"]:
            _render_block(child, depth, lines, has_pdf)
        return
    for child in node["children"]:
        _render_block(child, depth + 1, lines, has_pdf)


# --- area-highlight images ---------------------------------------------------

def render_area_images(pdf_path, stem, highlights, scale=2.0):
    """Render each area highlight's crop → ``[(arcname, png_bytes)]``, in
    the highlights' order. Best-effort: any failure just drops the image,
    never the export."""
    on_page = {}
    for i, h in enumerate(highlights):
        if h["area"] and h["position"] and h["position"].get("boundingRect"):
            on_page.setdefault(h["page"], []).append(i)
    if not on_page:
        return []
    pngs = {}
    try:
        # The file opened once, and each page with an area highlight drawn
        # once, whole, under pdf_text's lock (PyMuPDF is not thread-safe);
        # the crops and their PNGs are made outside it. A file that will not
        # open (a password-locked one too) raises: no pictures. A page past
        # its end, or one that fails to draw, is skipped.
        for page_no, raster in page_rasters(pdf_path, on_page, scale):
            for i in on_page[page_no]:
                try:
                    pngs[i] = _area_png(raster, highlights[i]["position"])
                except Exception:
                    continue
    except Exception:
        pass
    return [(f"assets/{stem}/{h['page']}_{h['uuid']}_{h['stamp']}.png", pngs[i])
            for i, h in enumerate(highlights) if i in pngs]


def _area_png(raster, pos):
    """One area highlight's crop of its page's ``raster``, as PNG bytes."""
    W, H = raster.width, raster.height
    br = pos["boundingRect"]
    # br coords are relative to a capture-time render of size width×height —
    # rescale into this raster's pixel grid.
    sx, sy = W / (pos.get("width") or W), H / (pos.get("height") or H)
    x1, x2 = sorted((int(br["x1"] * sx), int(br["x2"] * sx)))
    y1, y2 = sorted((int(br["y1"] * sy), int(br["y2"] * sy)))
    x1, y1 = max(0, x1), max(0, y1)
    x2, y2 = min(W, max(x2, x1 + 1)), min(H, max(y2, y1 + 1))
    return raster.png((x1, y1, x2, y2))
