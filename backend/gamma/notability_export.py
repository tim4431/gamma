"""A Gamma page as a Notability note: the Notability export
(docs/dev/import_export.md "Notability export"). The page's PDF, or its
sheets of paper, become the note's pages; its handwriting, shapes, text
boxes, pictures, notes and recordings what Notability keeps of them; the
writing is gamma/notability_write.py's.

The export is the import's inverse (gamma/notability_import.py,
gamma/notability_map.py): every place is the import's ``Frame`` run
backwards, from the same ``frames`` the import computes, and every stroke
is written so the import reads its samples back as they are (a curve whose
UUID carries ``nb.GAMMA_UUID``: anchors, not a Bézier to flatten). What
Notability's model has no place for — a stroke's time, a pencil's baked
opacity, the brushes Notability lacks, Markdown it cannot format, empty
notes, the sheets' own papers, a recording's segments — rides along in the
note as Gamma's own copy (``Draft.private``, the root's
``nw.PRIVATE_KEY``), which Notability ignores and drops when it saves the
note, and which the import uses for each item it still matches. What a
note cannot hold at all (highlights on a PDF's text, a recording that is
no .m4a) is left out and counted, and the export says so.
"""

import io
import math
import re
import uuid as uuidlib
from collections import Counter
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

from PIL import Image

from . import ink as inkmod
from . import notability as nb
from . import notability_map as nmap
from . import notability_write as nw
from .ai_pictures import sniff
from .notebook import DEFAULT_PAPER, normalize_paper, sheets_of
from .pdf_export import ExportPdfReader, display_size, page_frame
from .pdf_typeset import BOLD, ITALIC, LINK, MARK, MONO, STRIKE, UNDERLINE
from .picture import MIN_SIDE, URL_RE, caption_text, image_size, is_picture, normalize_picture, picture_page
from .text_box import PAD, box_page, normalize_text_box, plain_text

PRIVATE_FORMAT = "gamma-notability"
PRIVATE_VERSION = 1
MARK_RGBA = (1.0, 0.93, 0.55, 1.0)       # a ==mark==, as the notes PDF paints one
STICKY_EDGE = nmap.STICKY_EDGE
_DASH_CODES = {name: code for code, name in nb.DASH_PATTERNS.items()}
_IMAGE_EXTS = {"image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp"}
_PICTURE = re.compile(r"^!\[(?P<alt>[^\]]*)\]\(\s*/api/uploads/(?P<name>[0-9A-Za-z_-]+\.(?:png|jpe?g|gif|webp))\s*\)$",
                      re.I)
_ALT_WIDTH = re.compile(r"(?P<alt>.*?)\|\d+(?:x\d+)?")  # an Obsidian size ending the alt text, as the editor reads it
_LIST_ITEM = re.compile(r"^(?P<lead>[ \t]*)(?P<marker>[-*+]|\d{1,9}[.)])[ \t]+"
                        r"(?:\[(?P<box>[ xX])\][ \t]+)?(?P<text>.*)$")
_HEADING = re.compile(r"^[ \t]{0,3}#{1,6}[ \t]+(?P<text>.*)$")
_QUOTE = re.compile(r"^[ \t]{0,3}>[ \t]?(?P<text>.*)$")
_NUMBERED = re.compile(r"^(?P<n>\d{1,6})\. (?P<text>.*)$", re.S)
_TODO = re.compile(r"^- \[(?P<box>[ x])\] (?P<text>.*)$", re.S)

# What a page loses on its way into a note, by kind, as the export's
# warnings say it once per page (``reasons``); ``{n}`` is the count.
LEFT_OUT = {
    "highlight": ("{n} highlight is not in the note: Notability keeps no highlights on a PDF's text",
                  "{n} highlights are not in the note: Notability keeps no highlights on a PDF's text"),
    "pdf sheet": ("{n} sheet of paper on a PDF page is left out", "{n} sheets of paper on a PDF page are left out"),
    "nowhere": ("{n} group of handwriting is on no page and is left out",
                "{n} groups of handwriting are on no page and are left out"),
    "picture nowhere": ("{n} picture is on no page and is left out", "{n} pictures are on no page and are left out"),
    "missing": ("{n} file of the page is missing on the server and is left out",
                "{n} files of the page are missing on the server and are left out"),
    "recording": ("{n} recording is not an .m4a file (Notability plays only those) and is left out",
                  "{n} recordings are not .m4a files (Notability plays only those) and are left out"),
    "recording live": ("{n} recording still being made is left out", "{n} recordings still being made are left out"),
    "recording notes": ("{n} note under a recording is left out", "{n} notes under recordings are left out"),
    "paper size": ("A sheet's size is not one of Notability's (letter, A4, legal): its page is {size}",
                   "{n} sheets' sizes are not Notability's (letter, A4, legal): their pages are {size}"),
    "paper": ("{n} sheet's paper differs from the first sheet's: Notability shows one paper for a note",
              "{n} sheets' papers differ from the first sheet's: Notability shows one paper for a note"),
    "pattern": ("Notability shows {pattern} paper as plain: a note keeps only ruled lines",
                "Notability shows {pattern} paper as plain: a note keeps only ruled lines"),
}


class ExportError(ValueError):
    """The page cannot be a note at all: the reason."""


def reasons(counts: Counter, **names) -> list[str]:
    """One warning per kind of content left out (``LEFT_OUT``)."""
    out = []
    for kind, n in counts.items():
        if n > 0 and kind in LEFT_OUT:
            one, many = LEFT_OUT[kind]
            out.append((one if n == 1 else many).format(n=n, **names))
    return out


# --- the paper -------------------------------------------------------------------------------
# Notability's paper pages come in a few sizes only, each as tall as the
# import makes it (nmap.paper_page): the size, orientation and sizing, and
# the page in points. A sheet of one of them comes back as itself.

def _page_of(size: str, landscape: bool, sizing: str) -> tuple[float, float]:
    """The page in points of a paper of that kind, as the import sizes it."""
    width = nmap.A4_PT if size == "a4" else nmap.LETTER_PT
    stub = nb.Note(title="", width=width, paper=nb.PaperSpec(size=size, sizing=sizing, landscape=landscape))
    pw, ph, _height = nmap.paper_page(stub)
    return pw, ph


_SIZES = [(spec, *_page_of(*spec)) for spec in (
    ("a4", False, "lockedWidth"), ("letter", False, "lockedWidth"), ("letter", False, "staticWidth"),
    ("legal", False, "lockedWidth"), ("a4", True, "lockedWidth"), ("letter", True, "lockedWidth"),
    ("letter", True, "staticWidth"), ("legal", True, "lockedWidth"))]


def paper_size(width: float, height: float) -> tuple[tuple, float, float, bool]:
    """``((size, landscape, sizing), page width, page height, exact)`` for
    a sheet of ``width`` × ``height`` points: the Notability size it is
    (``exact``), else the one of its orientation nearest its shape."""
    rounded = normalize_paper({"width": width, "height": height})
    for spec, w, h in _SIZES:
        sized = normalize_paper({"width": w, "height": h})
        if (sized["width"], sized["height"]) == (rounded["width"], rounded["height"]):
            return spec, w, h, True
    landscape = width > height
    best = min((s for s in _SIZES if s[0][1] == landscape and s[0][2] == "lockedWidth"),
               key=lambda s: abs(s[2] / s[1] - height / width))
    return best[0], best[1], best[2], False


def _paper(sheet: dict) -> nw.Paper:
    """The note's paper of a (normalized) sheet's paper: its size, its
    colour, and a ruling every ``spacing`` points as the inches the import
    reads back (nmap.paper)."""
    (size, landscape, sizing), pw, _ph, _exact = paper_size(sheet["width"], sheet["height"])
    lines = sheet["spacing"] * nmap.LETTER_PT / (72 * pw) if sheet["pattern"] == "ruled" else None
    return nw.Paper(size=size, landscape=landscape, sizing=sizing, color=sheet["color"], lines=lines)


def _spec(paper: nw.Paper) -> nb.PaperSpec:
    """The reader's view of a written paper, for ``nmap.frames``."""
    return nb.PaperSpec(size=paper.size, sizing=paper.sizing, landscape=paper.landscape, color=paper.color,
                        lines=paper.lines)


# --- a page ------------------------------------------------------------------------------------

class _Page:
    """One Gamma page on its way into a note: its blocks, the note's frames
    (the import's), and what is being written."""

    def __init__(self, blocks: list[dict], page_id: str, uploads_dir: Path, resolve_ref=None):
        self.blocks, self.page_id, self.uploads = blocks, page_id, Path(uploads_dir)
        self.resolve_ref = resolve_ref  # [[ref]] → the block it names (markdown_export.block_ref_resolver)
        self.by_id = {b["id"]: b for b in blocks}
        self.kids: dict[str, list[dict]] = {}
        for b in blocks:
            self.kids.setdefault(b.get("parent_id"), []).append(b)
        for rows in self.kids.values():
            rows.sort(key=lambda b: (b.get("position") or "", b["id"]))
        self.root = self.by_id[page_id]
        self.left_out = Counter()
        self.names: dict = {}
        self.draft: nw.Draft | None = None
        self.private = {"format": PRIVATE_FORMAT, "version": PRIVATE_VERSION, "strokes": {}, "shapes": {},
                        "boxes": {}, "pictures": {}, "recordings": {}}
        self.order: list[str] = []  # the page's notes, sheets, recordings and PDF markup in document order
        self.z = 0

    # --- the walk -----------------------------------------------------------------------

    def ordered(self, parent: str):
        """The blocks below ``parent`` in document order."""
        stack = list(reversed(self.kids.get(parent, [])))
        while stack:
            b = stack.pop()
            yield b
            stack += reversed(self.kids.get(b["id"], []))

    def subtree(self, block_id: str) -> int:
        """How many blocks lie below ``block_id``."""
        return sum(1 for _ in self.ordered(block_id))

    # --- the whole page -----------------------------------------------------------------

    def build(self) -> nw.Draft:
        props = self.root.get("properties") or {}
        title = nb.note_title("", self.root.get("content") or "", None, "")
        created = _stamp(self.root.get("created_at")) or datetime.now(UTC)
        doc_id = props.get("doc_id")
        sheets = [] if doc_id else sheets_of(self.blocks, self.page_id)
        on_sheet = {b["id"]: n for n, sheet in enumerate(sheets) for b in sheet["blocks"]}
        if doc_id:
            surfaces = self._pdf(doc_id, title, created)
        else:
            surfaces = self._sheets(sheets, title, created)
        draft = self.draft
        draft.modified = _stamp(self.root.get("updated_at"))
        inks = {}      # surface index → [InkFile] in block order
        pictures = {}  # surface index → [block]
        boxes = {}     # surface index → [block]
        notes = []  # (block, its depth among the notes) of the flow
        depths: dict[str, int] = {}  # a note's depth among the notes: its parent's + 1 under a note, else 0
        hidden: set[str] = set()  # blocks left out with what holds them
        for b in self.ordered(self.page_id):
            if b["id"] in hidden:
                hidden.update(c["id"] for c in self.kids.get(b["id"], []))
                continue
            kind, p = b.get("kind"), b.get("properties") or {}
            if kind == "sheet":
                if doc_id:
                    self.left_out["pdf sheet"] += 1
                    hidden.update(c["id"] for c in self.kids.get(b["id"], []))
                else:
                    self.order.append("sheet")
                continue
            if kind == "highlight" or (kind == "link" and isinstance(p.get("pdf_position"), dict)):
                if kind == "highlight":
                    self.left_out["highlight"] += 1
                hidden.update(c["id"] for c in self.kids.get(b["id"], []))
                continue
            if kind == "recording":
                if self._recording(b):
                    self.order.append("recording")
                self.left_out["recording notes"] += self.subtree(b["id"])
                hidden.update(c["id"] for c in self.kids.get(b["id"], []))
                continue
            if kind == "ink":
                surface = self._ink_surface(b, on_sheet, doc_id, len(surfaces))
                ink = inkmod.read_upload(self.uploads, p.get("ink_url") or "")
                if ink is None:
                    self.left_out["missing"] += 1
                elif surface is None:
                    self.left_out["nowhere"] += 1
                else:
                    inks.setdefault(surface, []).append(ink)
                    self._markup_here(doc_id)
                continue
            if is_picture(p):  # also while the kind column is older than pictures
                surface = on_sheet.get(b["id"]) if not doc_id else (
                    page - 1 if (page := picture_page(p)) and page <= len(surfaces) else None)
                if surface is None:
                    self.left_out["picture nowhere"] += 1
                else:
                    pictures.setdefault(surface, []).append(b)
                    self._markup_here(doc_id)
                continue
            if kind == "text_box":
                surface = on_sheet.get(b["id"]) if not doc_id else (
                    page - 1 if (page := box_page(p, on_sheet=False)) and page <= len(surfaces) else None)
                if surface is not None:
                    boxes.setdefault(surface, []).append(b)
                    self._markup_here(doc_id)
                    continue
            parent = b.get("parent_id")
            depths[b["id"]] = depths[parent] + 1 if parent in depths else 0
            notes.append((b, depths[b["id"]]))
            if not depths[b["id"]]:
                self.order.append("note")
        for n, frame in enumerate(surfaces):  # a surface's pictures under its boxes, as the screen draws them
            self._ink(inks.get(n, []), frame)
            for b in pictures.get(n, []):
                self._picture_block(b, frame)
            for b in boxes.get(n, []):
                self._box(b, frame)
        self._flow(notes)
        draft.private = {**self.private, "order": self.order}
        return draft

    def _markup_here(self, doc_id) -> None:
        """A PDF page's handwriting, boxes and pictures come back together,
        where the first of them was among the page's notes and recordings."""
        if doc_id and "markup" not in self.order:
            self.order.append("markup")

    # --- surfaces -------------------------------------------------------------------------

    def _pdf(self, doc_id: str, title: str, created) -> list:
        """A PDF page's note: the PDF, all of its pages in order; W the
        first page's width, so its points are document units."""
        from .db import safe_doc_id
        try:
            path = self.uploads / f"{safe_doc_id(doc_id)}.pdf"
        except ValueError:
            raise ExportError("invalid document id") from None
        if not path.is_file():
            raise ExportError("PDF not stored on the server")
        try:
            reader = ExportPdfReader(str(path), strict=False)
            if reader.is_encrypted:
                reader.decrypt("")
            sizes = [display_size(*page_frame(page)) for page in reader.pages]
        except Exception as exc:  # noqa: BLE001 — a PDF PyPDF2 cannot read has no pages to place
            raise ExportError(f"the PDF could not be read ({exc.__class__.__name__})") from None
        if not sizes:
            raise ExportError("the PDF has no pages")
        name = f"{str(uuidlib.uuid4()).upper()}.pdf"  # the app names a note's PDF by a UUID
        width = float(sizes[0][0])
        paper = nw.Paper()
        stub = nb.Note(title=title, width=width, paper=_spec(paper),
                       layout=[nb.LayoutEntry(name, i) for i in range(1, len(sizes) + 1)],
                       pdfs={name: nb.PdfFile(name, f"PDFs/{name}", 2)})
        frames, _warnings = nmap.frames(stub, {name: sizes}, [])
        self.draft = nw.Draft(title=title, width=width, paper=paper, created=created,
                              layout=[(name, i) for i in range(1, len(sizes) + 1)], pdfs=[name],
                              files={f"PDFs/{name}": path})
        return frames

    def _sheets(self, sheets: list, title: str, created) -> list:
        """A notebook's note: a paper page per sheet, the first sheet's paper
        for all (Notability has one per note); a note of no sheet is the
        typed text alone, on letter paper."""
        papers = [s["paper"] for s in sheets] or [normalize_paper({**DEFAULT_PAPER, "width": nmap.LETTER_PT,
                                                                    "height": 803.25})]
        first = papers[0]
        (_size, _landscape, _sizing), pw, ph, exact = paper_size(first["width"], first["height"])
        paper = _paper(first)
        if sheets and not exact:
            self.left_out["paper size"] += len(sheets)
            self.names["size"] = f"{pw:g} × {ph:g} pt"
        if first["pattern"] in ("grid", "dots"):
            self.left_out["pattern"] += 1
            self.names["pattern"] = "dot" if first["pattern"] == "dots" else "grid"
        self.left_out["paper"] += sum(1 for p in papers[1:] if p != first)
        stub = nb.Note(title=title, width=pw, paper=_spec(paper), layout=[nb.LayoutEntry(None, None)] * len(papers))
        _pw, _ph, height = nmap.paper_page(stub)
        frames, _warnings = nmap.frames(stub, {}, [(i + 0.5) * height for i in range(len(papers))])
        self.draft = nw.Draft(title=title, width=pw, paper=paper, created=created)
        # a sheet comes back at the size its page has: each paper is kept at that size
        page = nmap.paper(stub)
        self.private["papers"] = [{**p, "width": page["width"], "height": page["height"]} for p in papers
                                  ] if sheets else []
        return frames if sheets else []

    def _ink_surface(self, b: dict, on_sheet: dict, doc_id, n_surfaces: int) -> int | None:
        """The surface an ink group draws on: its sheet in a notebook, on a
        PDF the page its file's space names; None for none."""
        if not doc_id:
            return on_sheet.get(b["id"])
        if b["id"] in on_sheet:
            return None
        ink = inkmod.read_upload(self.uploads, (b.get("properties") or {}).get("ink_url") or "")
        if ink is None or ink.space.kind != "pdf-page" or not 1 <= (ink.space.page or 0) <= n_surfaces:
            return None
        return ink.space.page - 1

    # --- ink --------------------------------------------------------------------------------

    def _ink(self, files: list, frame) -> None:
        """A surface's strokes in the order the screen draws them (band 0
        of every group, then band 1, each in file order), each written as
        a sticky, a shape or a stroke: stickies before any other band 0
        stroke, as the import draws them first."""
        strokes = [s for f in files for s in f.strokes if inkmod.band_of(s) == 0]
        strokes += [s for f in files for s in f.strokes if inkmod.band_of(s) == 1]
        leading = True
        for stroke in strokes:
            if leading and _sticky(stroke):
                self._sticky(stroke, frame)
                continue
            leading = False
            shape = _shape_of(stroke, frame)
            uuid = nb.gamma_uuid()
            copy = {k: v for k, v in inkmod.to_dict(stroke).items() if k not in ("id", "from")}
            if shape is not None:
                shape.uuid, shape.z = uuid, self._next()
                self.draft.shapes.append(shape)
                self.private["shapes"][uuid.hex()] = copy
                continue
            written = _stroke_of(stroke, frame)
            written.uuid, written.z = uuid, self._next()
            self.draft.strokes.append(written)
            self.private["strokes"][uuid.hex()] = copy

    def _next(self) -> int:
        self.z += 1
        return self.z - 1

    def _sticky(self, stroke: inkmod.Stroke, frame) -> None:
        p = stroke.shape.params
        k = frame.k
        r, g, b, _a = inkmod.parse_color(stroke.color)
        self.draft.media.append(nw.Sticky(
            x=(p["cx"] - p["w"] / 2) / k + frame.ox, y=(p["cy"] - p["h"] / 2) / k + frame.oy, w=p["w"] / k,
            h=p["h"] / k, rgba=(r, g, b, 1.0), rotation=math.radians(p.get("angle") or 0),
            z=len(self.draft.media), uuid=nw.new_uuid()))

    # --- boxes and pictures --------------------------------------------------------------

    def _box(self, b: dict, frame) -> None:
        box = normalize_text_box((b.get("properties") or {}).get("text_box"))
        content = b.get("content") or ""
        k = frame.k
        match = _PICTURE.match(content.strip())
        if match and self._boxed_picture(match, box, frame):
            return
        size = box["size"] / k
        rgba = inkmod.parse_color(box["color"])
        runs = box_runs(content, size, rgba, self.resolve_ref)
        uuid = nw.new_uuid()
        self.draft.media.append(nw.TextBox(
            x=box["x"] / k + frame.ox, y=box["y"] / k + frame.oy, w=box["w"] / k, h=box["h"] / k, runs=runs,
            background=inkmod.parse_color(box["bg"]) if box["bg"] else None, z=len(self.draft.media), uuid=uuid))
        self.private["boxes"][uuid.hex()] = {"content": content, "text_box": box,
                                             "text": "".join(r.text for r in runs)}

    def _boxed_picture(self, match, box: dict, frame) -> bool:
        """A box holding one uploaded picture, as imports made one before a
        picture was a block of its own: a picture in the box less its
        padding, as tall as the picture shown that wide, its alt text the
        caption without the Obsidian size the width grip ends it with
        (``![alt|300](url)``). False when its file cannot be read (the box
        is then written as text)."""
        url = f"/api/uploads/{match.group('name')}"
        image = self._image(url) if URL_RE.match(url) else None
        if image is None:
            self.left_out["missing"] += 1
            return False
        pw, ph = image[1]
        w = max(MIN_SIDE, box["w"] - 2 * PAD)
        pic = normalize_picture({"url": url, "x": box["x"] + PAD, "y": box["y"] + PAD, "w": w, "h": w * ph / pw})
        alt = match.group("alt")
        sized = _ALT_WIDTH.fullmatch(alt)
        self._picture(pic, sized.group("alt") if sized else alt, image, frame)
        return True

    def _picture_block(self, b: dict, frame) -> None:
        """A picture block as a picture of the note; one whose file is
        missing or does not read is left out (counted)."""
        pic = normalize_picture((b.get("properties") or {}).get("picture"))
        image = self._image(pic["url"]) if pic else None
        if image is None:
            self.left_out["missing"] += 1
            return
        self._picture(pic, b.get("content") or "", image, frame)

    def _picture(self, pic: dict, caption: str, image: tuple, frame) -> None:
        """A (normalized) picture as an ImageMediaObject, its file ``image``
        (``_image``): its frame through the note's, its crop in the file's
        pixels, its flips, its turn in radians, and its caption the text
        Notability shows under it (``caption``'s Markdown marks dropped, on
        one line). Gamma's copy keeps the caption's Markdown and the picture
        as they are."""
        name, (pw, ph), jpeg, asset = image
        k, crop = frame.k, pic["crop"]
        uuid = nw.new_uuid()
        self.draft.media.append(nw.Picture(
            x=pic["x"] / k + frame.ox, y=pic["y"] / k + frame.oy, w=pic["w"] / k, h=pic["h"] / k, image=name,
            pixels=(pw, ph), jpeg=jpeg, gif=asset, rotation=math.radians(pic["angle"]),
            crop=(crop["x"] * pw, crop["y"] * ph, crop["w"] * pw, crop["h"] * ph), flip_h=pic["flip_h"],
            flip_v=pic["flip_v"], caption=caption_text(plain_text(caption, self.resolve_ref)),
            z=len(self.draft.media), uuid=uuid))
        self.private["pictures"][uuid.hex()] = {"caption": caption, "picture": pic}

    def _image(self, url: str) -> tuple | None:
        """An uploaded picture's file in the note: ``(its path below the
        note's folder, its pixel size as shown, whether it is a JPEG, the
        asset id of a GIF's animation)``, or None when it is missing or no
        picture whose size reads (``picture.image_size``). A GIF is its
        still frame under Images/ and itself under Assets/<id>/media.gif,
        as Notability keeps one; any other file goes under Images/ as it
        is."""
        path = self.uploads / url.rsplit("/", 1)[-1]
        try:
            data = path.read_bytes()
        except OSError:
            return None
        pixels, kind = image_size(data), sniff(data)
        if pixels is None or not all(pixels):
            return None
        n = sum(1 for m in self.draft.media if isinstance(m, nw.Picture)) + 1
        if kind != "image/gif":
            name = f"Images/Image {n}{_IMAGE_EXTS[kind]}"
            self.draft.files[name] = path
            return name, pixels, kind == "image/jpeg", None
        try:
            with Image.open(io.BytesIO(data), formats=["GIF"]) as image:  # its first frame
                still = io.BytesIO()
                image.convert("RGBA").save(still, "PNG")
        except (OSError, ValueError, Image.DecompressionBombError, SyntaxError):
            return None
        name, asset = f"Images/Image {n}.png", str(uuidlib.uuid4()).upper()
        self.draft.files[name] = still.getvalue()
        self.draft.files[f"Assets/{asset}/media.gif"] = data
        return name, pixels, False, asset

    # --- the notes ------------------------------------------------------------------------

    def _flow(self, notes: list) -> None:
        """The notes, in order, as the text typed on the pages: a paragraph
        per block, its lines kept apart by nmap.LINE_SEPARATOR; a top-level
        note a plain paragraph, a deeper one a bullet at its depth, a note
        that reads "n. …" a numbered item and "- [ ] …" a checklist item, as
        the import reads them back (nmap.flow_blocks)."""
        runs: list[nw.Run] = []
        tree: list[dict] = []
        stack: list[tuple[int, dict]] = []
        list_id = 0  # a list's paragraphs share an id, which Notability groups them by
        for b, depth in notes:
            if runs:
                runs[-1] = replace(runs[-1], text=runs[-1].text + "\n")
            content = b.get("content") or ""
            para = flow_runs(content, depth, self.resolve_ref)
            if para[0].list_style:
                if not (runs and runs[-1].list_style):
                    list_id += 1
                para = [replace(r, list_id=list_id) for r in para]
            runs += para
            node = {"content": content, "children": []}
            while stack and stack[-1][0] >= depth:
                stack.pop()
            (stack[-1][1]["children"] if stack else tree).append(node)
            stack.append((depth, node))
        self.draft.flow = runs
        self.private["flow"] = {"text": "".join(r.text for r in runs), "blocks": tree}

    # --- recordings -------------------------------------------------------------------------

    def _recording(self, b: dict) -> bool:
        """A recording's segments, each an .m4a of Notability's recordings,
        named after the recording; one that is no .m4a, or is being made,
        is left out. Whether any segment was written."""
        rec = (b.get("properties") or {}).get("recording") or {}
        if rec.get("state") != "done":
            self.left_out["recording live"] += 1
            return False
        segments = [s for s in rec.get("segments") or [] if isinstance(s, dict)]
        if not segments or any(not str(s.get("url") or "").lower().endswith(".m4a") for s in segments):
            self.left_out["recording"] += 1
            return False
        block = len({c["block"] for c in self.private["recordings"].values()})
        title = (b.get("content") or "").strip() or "Recording"
        for s in segments:
            path = self.uploads / str(s["url"]).rsplit("/", 1)[-1]
            if not path.is_file():
                self.left_out["missing"] += 1
                continue
            n = len(self.draft.recordings) + 1
            name = f"Recording {n}.m4a"
            started = s.get("started") if isinstance(s.get("started"), int) else rec.get("started") or 0
            self.draft.recordings.append(nw.Recording(
                identifier=n, name=title, filepath=name, created=datetime.fromtimestamp(started / 1000, UTC),
                duration=(s.get("dur") or 0) / 1000, size=path.stat().st_size))
            self.draft.files[f"Recordings/{name}"] = path
            self.private["recordings"][name] = {"block": block, "content": b.get("content") or "",
                                                "recording": {k: v for k, v in rec.items() if k != "segments"},
                                                "segment": {k: v for k, v in s.items() if k not in ("url", "bytes")}}
        return any(c["block"] == block for c in self.private["recordings"].values())


# --- strokes ---------------------------------------------------------------------------------

def _sticky(stroke: inkmod.Stroke) -> bool:
    """Whether a stroke is a sticky note as the import makes one
    (nmap.sticky_stroke): a filled rectangle of one colour in band 0, its
    edge STICKY_EDGE wide."""
    shape, fill = stroke.shape, stroke.fill
    return (stroke.band == 0 and shape is not None and shape.kind == "rect" and fill is not None
            and fill.color.lower() == stroke.color.lower() and fill.opacity == 1 and stroke.size == STICKY_EDGE
            and not stroke.pen and stroke.tool == "pen" and stroke.brush is None and stroke.dash is None
            and stroke.opacity == 1 and stroke.ch == "xy"
            and all(k in shape.params for k in ("cx", "cy", "w", "h")))


def _style(stroke: inkmod.Stroke) -> int:
    if stroke.tool == "highlighter":
        return nb.STYLE_HIGHLIGHTER
    return nb.STYLE_PENCIL if stroke.tool == "pen" and stroke.brush == "pencil" else nb.STYLE_PEN


def _alpha(stroke: inkmod.Stroke) -> float:
    """The opacity the stroke is drawn at (``ink.drawn_alpha``)."""
    return inkmod.drawn_alpha(stroke, inkmod.parse_color(stroke.color)[3])


def _rgb_bytes(stroke: inkmod.Stroke) -> tuple:
    r, g, b, _a = inkmod.parse_color(stroke.color)
    alpha = max(1, min(255, inkmod.js_round(_alpha(stroke) * 255)))
    return tuple(max(0, min(255, inkmod.js_round(v * 255))) for v in (r, g, b)) + (alpha,)


def _dash(stroke: inkmod.Stroke) -> int:
    return _DASH_CODES.get(stroke.dash or "", 0)


def _stroke_of(stroke: inkmod.Stroke, frame) -> nw.Stroke:
    """A stroke as a curve whose anchors are its samples, in document
    units, each anchor with the sample's width, force, tilt and azimuth
    where the stroke has them."""
    k, ox, oy = frame.k, frame.ox, frame.oy
    samples = inkmod.decode_stroke(stroke)
    ch = stroke.ch
    return nw.Stroke(
        anchors=[(s["x"] / k + ox, s["y"] / k + oy) for s in samples], width=stroke.size / k,
        rgba=_rgb_bytes(stroke),
        widths=[s["w"] for s in samples] if "w" in ch else None,
        forces=[s["p"] * nmap.MAX_FORCE for s in samples] if "p" in ch else None,
        altitudes=[math.radians(s["a"]) for s in samples] if "a" in ch else None,
        azimuths=[(math.cos(math.radians(s["z"])), math.sin(math.radians(s["z"]))) for s in samples]
        if "z" in ch else None,
        style=_style(stroke), dash=_dash(stroke))


def _corners(cx: float, cy: float, w: float, h: float, angle: float) -> list:
    """A turned rectangle's corners, the first side along ``angle`` (y
    down), in the order the import reads one (nmap.shape_stroke)."""
    rad = math.radians(angle)
    c, s = math.cos(rad), math.sin(rad)
    return [(cx + u * c - v * s, cy + u * s + v * c) for u, v in ((-w / 2, -h / 2), (w / 2, -h / 2), (w / 2, h / 2),
                                                                  (-w / 2, h / 2))]


def _shape_of(stroke: inkmod.Stroke, frame) -> nw.Shape | None:
    """A stroke with a shape record as a shape of Notability's, its
    geometry in document units; None for one Notability has no shape for
    (a curve, a record whose params are not all there), which is written
    as a stroke."""
    shape = stroke.shape
    if shape is None:
        return None
    p, kind = shape.params, shape.kind
    try:
        if kind in ("line", "arrow"):
            points, out = [(p["x1"], p["y1"]), (p["x2"], p["y2"])], "line"
        elif kind == "rect":
            points, out = _corners(p["cx"], p["cy"], p["w"], p["h"], p.get("angle") or 0), "rectangle"
        elif kind == "ellipse":
            points, out = _corners(p["cx"], p["cy"], 2 * p["rx"], 2 * p["ry"], p.get("angle") or 0), "ellipse"
        elif kind in ("triangle", "polygon"):
            points, out = inkmod._shape_corners(kind, p), kind
        else:
            return None
    except (KeyError, TypeError):
        return None
    if not points or not all(isinstance(v, (int, float)) and math.isfinite(v) for pt in points for v in pt):
        return None
    k, ox, oy = frame.k, frame.ox, frame.oy
    r, g, b, a = inkmod.parse_color(stroke.color)
    fill = stroke.fill if kind in ("rect", "ellipse", "triangle", "polygon") else None
    return nw.Shape(kind=out, points=[(x / k + ox, y / k + oy) for x, y in points], width=stroke.size / k,
                    rgba=(r, g, b, _alpha(stroke)), arrow=kind == "arrow",
                    fill=(*inkmod.parse_color(fill.color)[:3], fill.opacity) if fill is not None else None,
                    style=_style(stroke), dash=_dash(stroke))


# --- text ------------------------------------------------------------------------------------

def _font(bits: int) -> str:
    face = "Menlo" if bits & MONO else nw.FONT
    style = {BOLD: "Bold", ITALIC: "Italic", BOLD | ITALIC: "BoldItalic"}.get(bits & (BOLD | ITALIC))
    return f"{face}-{style}" if style else (f"{face}-Regular" if face == "Menlo" else face)


def inline_runs(text: str, base: nw.Run, resolve_ref=None) -> list[nw.Run]:
    """One line of Markdown as runs of ``base``'s look: bold and italic in
    the font's name, code in Menlo, strikes, underlines and marks as
    Notability keeps them, a link underlined, a ``[[ref]]`` as the text of the block it
    names (``resolve_ref``), math as its TeX between dollars (Notability
    has no inline math)."""
    from .note_markup import TEXT
    from .pdf_document import inline
    from .pdf_typeset import PLAIN
    out = []
    for kind, payload, _tail, style in inline(text, PLAIN, resolve_ref):
        shown = payload if kind == TEXT else f"${payload}$"
        if not shown:
            continue
        bits = style.bits
        out.append(replace(base, text=shown, font=_font(bits), strike=bool(bits & STRIKE),
                           underline=bool(bits & (LINK | UNDERLINE)), mark=MARK_RGBA if bits & MARK else None))
    return out


def box_runs(md: str, size: float, rgba: tuple, resolve_ref=None) -> list[nw.Run]:
    """A text box's Markdown as runs: a paragraph per line (a box shows a
    line per source line), list items as Notability's lists at the level
    their indent nests them, a heading bold, a quote's text as it reads;
    as the import's ``box_markdown`` reads them back."""
    base = nw.Run("", size=size, rgba=rgba)
    indents: list[int] = []  # the open list levels' indents: a deeper indent nests, a shallower one closes

    def level(indent: int) -> int:
        while indents and indents[-1] > indent:
            indents.pop()
        if not indents or indents[-1] < indent:
            indents.append(indent)
        return len(indents) - 1

    runs: list[nw.Run] = []
    list_id, listing = 0, False  # a list's paragraphs share an id, which Notability groups them by
    lines = md.split("\n")
    for i, line in enumerate(lines):
        para = base
        text = line
        item = _LIST_ITEM.match(line)
        if item:
            if not listing:
                list_id += 1
            listing = True
            marker, box = item.group("marker"), item.group("box")
            style = 3 if box else 2 if marker[0].isdigit() else 1
            para = replace(base, list_style=style, level=level(len(item.group("lead").expandtabs(4))),
                           number=int(marker[:-1]) if style == 2 else 1, checked=(box or " ").lower() == "x",
                           list_id=list_id)
            text = item.group("text")
        else:
            indents.clear()
            listing = False
            heading, quote = _HEADING.match(line), _QUOTE.match(line)
            if heading:
                text = f"**{heading.group('text')}**"
            elif quote:
                text = quote.group("text")
            text = text.strip()
        found = inline_runs(text, para, resolve_ref)
        if i < len(lines) - 1:
            if found:
                found[-1] = replace(found[-1], text=found[-1].text + "\n")
            else:
                found = [replace(para, text="\n")]
        runs += found
    return runs


def flow_runs(content: str, depth: int, resolve_ref=None) -> list[nw.Run]:
    """One note as one paragraph of the typed text: its lines joined by
    nmap.LINE_SEPARATOR; a bullet at ``depth`` below the top, a numbered item
    or a checklist item when it reads as one (the import's markers),
    else plain; a heading bold."""
    base = nw.Run("")
    heading = _HEADING.match(content.split("\n", 1)[0])
    if heading:
        content = f"**{heading.group('text')}**" + content[len(content.split("\n", 1)[0]):]
    numbered, todo = _NUMBERED.match(content), _TODO.match(content)
    if numbered:
        base = replace(base, list_style=2, number=int(numbered.group("n")), level=depth)
        content = numbered.group("text")
    elif todo:
        base = replace(base, list_style=3, checked=todo.group("box") == "x", level=depth)
        content = todo.group("text")
    elif depth:
        base = replace(base, list_style=1, level=depth)
    runs: list[nw.Run] = []
    for i, line in enumerate(content.split("\n")):
        if i:
            runs.append(replace(base, text=nmap.LINE_SEPARATOR))
        runs += inline_runs(line, base, resolve_ref)
    return runs or [replace(base, text="")]


def _stamp(value) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        when = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return when if when.tzinfo else when.replace(tzinfo=UTC)


# --- the entry point -------------------------------------------------------------------------

def page_draft(blocks: list[dict], page_id: str, uploads_dir, resolve_ref=None) -> tuple[nw.Draft, list[str]]:
    """The note of a page (its blocks as ``block_to_dict`` gives them, the
    root among them) and the warnings for what it leaves out;
    ``resolve_ref`` reads a ``[[ref]]`` as the block it names.
    ExportError when the page cannot be a note."""
    page = _Page(blocks, page_id, uploads_dir, resolve_ref)
    draft = page.build()
    return draft, reasons(page.left_out, size=page.names.get("size", ""), pattern=page.names.get("pattern", ""))


def write_page(blocks: list[dict], page_id: str, uploads_dir, out, resolve_ref=None) -> list[str]:
    """``page_draft`` written as a .note into ``out`` (a path or a binary
    file); the warnings."""
    draft, warnings = page_draft(blocks, page_id, uploads_dir, resolve_ref)
    nw.write_note(draft, out)
    return warnings
