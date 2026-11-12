"""A SyncTeX reader: which source line made a place on a page of a TeX
build, read from the build's ``.synctex.gz`` (docs/dev/feedback.md
"SyncTeX"). Pure, stdlib only.

The file, as TeX Live 2024 writes it: a preamble (``Input:<tag>:<path>``
for each file TeX read, ``Magnification``, ``Unit``, ``X Offset``,
``Y Offset``), then ``Content:``, one sheet per physical page
(``{<page>`` … ``}<page>``) of records in scaled points (sp)::

    [tag,line:h,v:W,H,D   a vbox, ``]`` its end     (h its left edge, v its baseline)
    (tag,line:h,v:W,H,D   an hbox (a line of text), ``)`` its end
    vtag,line:h,v:W,H,D   a void vbox, ``h`` a void hbox
    rtag,line:h,v:W,H,D   a rule, at its left edge
    ktag,line:h,v:W       a kern, written where it ends: it spans [h - W, h]
    gtag,line:h,v         a glue, ``$`` math, ``x`` the end of a run of characters

``v`` may be ``=``, the record before's, and a link may carry a column
(``tag,line,column``); more ``Input`` lines may come inside Content, and
one file may have two tags. A ``Post scriptum`` (written by ``synctex
update`` after a DVI filter) overrides the magnification and the offsets.
The page's frame is TeX's: big points (bp) from the top-left of the media
box, y down.

Every word of a paragraph's lines sits in an hbox tagged with the line
where the paragraph ended, so a line is read from the leaves: a stretch of
text belongs to the line of the interword space after it (a glue, or a
kern of 1 pt or more: pdfTeX writes a line's set glue as kerns), else to
its ``x``'s; a leaf tagged with the sheet's first record (the line TeX was
reading when the page shipped out: the page number, the footnote rule,
LuaTeX's font kerns) says nothing while another leaf can (``_attr``).
Each hbox's ``ends`` are its children's ends as a running maximum in the
file's order, never sorted (LuaTeX writes kerns behind the ``x`` before
them, and a scaled figure's inner box ends past the glue after it).
Content TeX turned or scaled (pdflscape, ``\\rotatebox``) is recorded
untransformed, so a place on it meets no box and answers nothing.

``load`` reads a file whole and closes it at once (on Windows the next
build's rename of ``main.synctex.gz(busy)`` over it must not fail), at
most ``MAX_GZ`` bytes compressed and ``MAX_TEXT`` of text, and keeps the
last ``FILES`` files while their size and time hold. A sheet is parsed on
first use, at most ``SHEETS`` kept a file, with no recursion: boxes nested
deeper than ``MAX_DEPTH`` are left out.
"""

import bisect
import os
import re
import threading
import zlib
from collections import OrderedDict
from pathlib import Path

SP_PER_BP = 65781.76      # 65536 sp a TeX point, 72.27 points an inch, 72 bp an inch
MAX_GZ = 64 << 20         # bytes of a compressed file read at most
MAX_TEXT = 256 << 20      # bytes of SyncTeX text, plain or decompressed
MAX_DEPTH = 512           # boxes nested deeper are left out
FILES = 4                 # opened files kept
SHEETS = 32               # parsed sheets a file keeps
SPACE_SP = 65536          # a kern this wide or wider is an interword space
SHIPOUT_WEIGHT = 0.05     # the shipout line's weight in a query: it ranks last
MARGIN_BP = 12            # a place this near a line of text, and on none, is that line's

# synctex_parser.c's dimensions, in sp
_DIMENSIONS = {b"in": 72.27 * 65536, b"cm": 72.27 * 65536 / 2.54, b"mm": 72.27 * 65536 / 25.4, b"pt": 65536.0,
               b"bp": 72.27 / 72 * 65536, b"pc": 12.0 * 65536, b"sp": 1.0, b"dd": 1238 / 1157 * 65536,
               b"cc": 14856 / 1157 * 65536, b"nd": 685 / 642 * 65536, b"nc": 1370 / 107 * 65536}
_REC = re.compile(rb"([\[\(vhkg$xr])(\d+),(\d+)(?:,-?\d+)?:(-?\d+),(=|-?\d+)(?::(-?\d+)(?:,(-?\d+),(-?\d+))?)?")
_SHEET = re.compile(rb"\n\{(\d+)\n")
_INPUT = re.compile(rb"\nInput:(\d+):([^\n]*)")
_NUMBER = re.compile(rb"\s*([-+]?(?:\d+\.?\d*|\.\d+))\s*([a-z]{2})?")


class SyncTeXError(ValueError):
    """A file that is not SyncTeX, or too large to read."""


class Box:
    __slots__ = ("kind", "tag", "line", "left", "top", "right", "bottom", "band", "kids", "ends")

    def __init__(self, kind, tag, line, h, v, w, ht, dp):
        self.kind, self.tag, self.line = kind, tag, line
        self.left, self.right = min(h, h + w), max(h, h + w)
        self.top, self.bottom = v - ht, v + dp
        self.band = (v - ht, v + dp)    # its own, which the visible top and bottom grow past
        self.kids = []      # leaves (start, end, kind, tag, line) and Boxes, in the file's order
        self.ends = None


def _end(kid) -> int:
    return kid.right if type(kid) is Box else kid[1]


def _space(leaf) -> bool:
    return leaf[2] == "g" or (leaf[2] == "k" and leaf[1] - leaf[0] >= SPACE_SP)


def _close(box: Box) -> None:
    """The box's visible extent, its own with its children's (an hbox's
    leaves included, synctex_parser.c's), and an hbox's ``ends``."""
    hbox = box.kind == "("
    for k in box.kids:
        if type(k) is Box:
            box.left, box.right = min(box.left, k.left), max(box.right, k.right)
            box.top, box.bottom = min(box.top, k.top), max(box.bottom, k.bottom)
        elif hbox:
            box.left, box.right = min(box.left, k[0]), max(box.right, k[1])
    if hbox:
        ends, far = [], box.left
        for k in box.kids:
            far = max(far, _end(k))
            ends.append(far)
        box.ends = ends


def _attr(box: Box, i: int, run: bool, shipout) -> tuple[int, int]:
    """``(tag, line)`` of what ends at child ``i`` of the hbox ``box``: the
    run of text before it (``run``), else the leaf's own width (a kern, a
    rule: its own tag and line).

    A run is the line of the first interword space after it, before the
    next ``x`` or box, that is not the line's end skip nor the shipout's;
    else an ``x``'s own, but the line's first ``x`` tagged like its box (the
    paragraph's end) takes the next space's; a space with no ``x`` before
    its run (XeTeX writes none) takes the space before. A leaf tagged with
    the shipout says nothing in a box that is not: the nearest other leaf
    answers."""
    kids = box.kids
    i = min(i, len(kids) - 1)
    k = kids[i]
    if type(k) is Box:
        return k.tag, k.line
    if run:
        for j in range(i, len(kids)):
            n = kids[j]
            if type(n) is Box or (j > i and n[2] == "x"):
                break
            if _space(n) and n[1] < box.right and (n[3], n[4]) != shipout:
                return n[3], n[4]
    if shipout is not None and (k[3], k[4]) == shipout and (box.tag, box.line) != shipout:
        for j in [*range(i - 1, -1, -1), *range(i + 1, len(kids))]:
            n = kids[j]
            if type(n) is not Box and (n[3], n[4]) != shipout:
                return n[3], n[4]
        return box.tag, box.line
    if not run:
        return k[3], k[4]
    if k[2] == "x":
        if i == 0 and (k[3], k[4]) == (box.tag, box.line):
            for n in kids[1:]:
                if type(n) is not Box and n[2] in "kg$":
                    return n[3], n[4]
        return k[3], k[4]
    if i > 0:
        p = kids[i - 1]
        if type(p) is not Box and p[2] in "kg":
            return p[3], p[4]
    return k[3], k[4]


def _boxes(root: Box):
    """Every box below ``root``, in the file's order, without recursion."""
    stack = [root]
    while stack:
        box = stack.pop()
        yield box
        stack.extend(k for k in reversed(box.kids) if type(k) is Box)


def _met(box: Box, r) -> bool:
    return not (box.right < r[0] or box.left > r[2] or box.bottom < r[1] or box.top > r[3])


def _walk(root: Box, r, weights: dict, shipout) -> None:
    """Add to ``weights`` each ``(tag, line)``'s share of the rect ``r``
    (sp): in every hbox it meets, each stretch's horizontal overlap times
    the rect's overlap with the hbox's band; an hbox with no leaves (a
    picture) its own area."""
    x0, y0, x1, y1 = r
    stack = [root]
    while stack:
        box = stack.pop()
        if not _met(box, r):
            continue
        if box.kind != "(":
            stack.extend(k for k in box.kids if type(k) is Box)
            continue
        top, bottom = box.band if box.band[1] > box.band[0] else (box.top, box.bottom)
        dy = min(y1, bottom) - max(y0, top)
        if dy <= 0:
            continue
        prev, got = box.left, False
        for i, k in enumerate(box.kids):
            if type(k) is Box:
                if k.kind in "[(" and k.kids:
                    stack.append(k)
                    got, prev = True, max(prev, k.right)
                    continue
                parts = ((prev, max(prev, k.right), True),)
            else:
                parts = ((prev, max(prev, k[0]), True), (max(prev, k[0]), max(prev, k[1]), False))
            for seg0, seg1, run in parts:
                ov = min(x1, seg1) - max(x0, seg0)
                if ov > 0:
                    key = _attr(box, i, run, shipout)
                    weights[key] = weights.get(key, 0) + ov * dy
                    got = True
            prev = parts[-1][1]
        if not got:
            ov = min(x1, box.right) - max(x0, box.left)
            if ov > 0:
                weights[(box.tag, box.line)] = weights.get((box.tag, box.line), 0) + ov * dy
        elif box.right > prev:
            ov = min(x1, box.right) - max(x0, prev)
            if ov > 0:
                key = _attr(box, len(box.kids) - 1, False, shipout)
                weights[key] = weights.get(key, 0) + ov * dy


def _gap(box: Box, r) -> float:
    return max(0, box.left - r[2], r[0] - box.right) + max(0, box.top - r[3], r[1] - box.bottom)


def _nearest(root: Box, r) -> Box | None:
    """The hbox with leaves nearest ``r`` (Manhattan distance between the
    rects), the first in the file's order of the nearest."""
    best, far = None, None
    for box in _boxes(root):
        if box.kind == "(" and box.kids:
            d = _gap(box, r)
            if far is None or d < far:
                best, far = box, d
    return best


def _deepest(root: Box, h, v) -> Box | None:
    """The deepest hbox with children holding the point, through vboxes;
    None when it is in none."""
    stack = [[root, 0]]
    while stack:
        frame = stack[-1]
        box, i = frame
        found = None
        while i < len(box.kids):
            k = box.kids[i]
            i += 1
            if type(k) is Box and k.kind in "[(" and k.left <= h <= k.right and k.top <= v <= k.bottom:
                found = k
                break
        frame[1] = i
        if found is not None:
            stack.append([found, 0])
            continue
        stack.pop()
        if stack and box.kind == "(" and box.kids:
            return box
    return None


class Sheet:
    __slots__ = ("root", "shipout")

    def __init__(self, root, shipout):
        self.root, self.shipout = root, shipout


def _decode(raw: bytes) -> str:
    for codec in ("utf-8", "mbcs") if os.name == "nt" else ("utf-8",):
        try:
            return raw.decode(codec)
        except UnicodeDecodeError:
            continue
    return raw.decode("latin-1")


def _int(text: bytes, default: int) -> int:
    try:
        return int(text.strip())
    except ValueError:
        return default


def _dimension(text: bytes) -> float | None:
    """A post scriptum's offset (``1in``, ``-2.5cm``, a bare number in sp)
    in sp, as synctex_parser.c reads it; None when it is no number."""
    m = _NUMBER.match(text)
    if not m:
        return None
    return float(m.group(1)) * _DIMENSIONS.get(m.group(2) or b"sp", 1.0)


class SyncTeX:
    """One opened SyncTeX file: ``inputs`` its tags' paths as TeX wrote them,
    ``pages`` its sheets."""

    def __init__(self, text: bytes):
        if b"\r\n" in text:
            text = text.replace(b"\r\n", b"\n")
        head = text.find(b"\nContent:")
        if not text.startswith(b"SyncTeX Version:") or head < 0:
            raise SyncTeXError("not a SyncTeX file")
        self._text = text
        mag, unit, xoff, yoff = 1000, 8192, 578, 578       # synctex_parser.c's defaults
        for ln in text[:head].split(b"\n"):
            if ln.startswith(b"Magnification:"):
                mag = _int(ln[14:], mag)
            elif ln.startswith(b"Unit:"):
                unit = _int(ln[5:], unit)
            elif ln.startswith(b"X Offset:"):
                xoff = _int(ln[9:], xoff)
            elif ln.startswith(b"Y Offset:"):
                yoff = _int(ln[9:], yoff)
        unit = unit if unit > 0 else 8192
        mag = mag if mag > 0 else 1000
        post_mag, post_x, post_y = 1.0, None, None
        ps = text.rfind(b"\nPost scriptum:")
        if ps > head:
            for ln in text[ps + 15:].split(b"\n"):
                if ln.startswith(b"Magnification:"):
                    m = _NUMBER.match(ln[14:])
                    if m and float(m.group(1)) > 0:
                        post_mag = float(m.group(1))
                elif ln.startswith(b"X Offset:"):
                    post_x = _dimension(ln[9:])
                elif ln.startswith(b"Y Offset:"):
                    post_y = _dimension(ln[9:])
        self._k = unit / SP_PER_BP * post_mag * mag / 1000.0
        self._xo = post_x / SP_PER_BP if post_x is not None else xoff * unit / SP_PER_BP
        self._yo = post_y / SP_PER_BP if post_y is not None else yoff * unit / SP_PER_BP
        self.inputs: dict[int, str] = {}
        for m in _INPUT.finditer(text):
            path = _decode(m.group(2)).strip()
            if len(path) > 1 and path[0] == path[-1] == '"':
                path = path[1:-1]
            self.inputs.setdefault(int(m.group(1)), path)
        self._offsets: dict[int, int] = {}
        self._starts: list[int] = []
        for m in _SHEET.finditer(text, head):
            self._offsets.setdefault(int(m.group(1)), m.end())
            self._starts.append(m.start())
        self.pages = max(self._offsets, default=0)
        self._sheets: OrderedDict = OrderedDict()
        self._lock = threading.Lock()

    # --- the frame -------------------------------------------------------------------

    def _to_sp(self, x, y):
        return (x - self._xo) / self._k, (y - self._yo) / self._k

    def _to_bp(self, h, v):
        return h * self._k + self._xo, v * self._k + self._yo

    def _rect(self, rect) -> tuple:
        a, b = self._to_sp(rect[0], rect[1])
        c, d = self._to_sp(rect[2], rect[3])
        return min(a, c), min(b, d), max(a, c), max(b, d)

    # --- sheets ----------------------------------------------------------------------

    def sheet(self, page: int) -> Sheet | None:
        with self._lock:
            hit = self._sheets.get(page)
            if hit is not None:
                self._sheets.move_to_end(page)
                return hit
        sheet = self._parse(page)
        if sheet is not None:
            with self._lock:
                self._sheets[page] = sheet
                while len(self._sheets) > SHEETS:
                    self._sheets.popitem(last=False)
        return sheet

    def _parse(self, page: int) -> Sheet | None:
        start = self._offsets.get(page)
        if start is None:
            return None
        text = self._text
        end = text.find(b"\n}%d\n" % page, start)
        if end < 0:
            after = bisect.bisect_right(self._starts, start)
            end = self._starts[after] if after < len(self._starts) else len(text)
        root = Box("[", 0, 0, 0, 0, 0, 0, 0)
        root.left, root.right, root.top, root.bottom = 1 << 60, -(1 << 60), 1 << 60, -(1 << 60)
        stack, skipped, shipout, lastv = [root], 0, None, 0
        for ln in text[start:end].split(b"\n"):
            c = ln[:1]
            if c == b"]" or c == b")":
                if skipped:
                    skipped -= 1
                elif len(stack) > 1:
                    _close(stack.pop())
                continue
            m = _REC.match(ln)
            if not m:
                continue                     # !, f, <, >, Input, and anything newer
            kind = chr(ln[0])
            tag, line, h = int(m.group(2)), int(m.group(3)), int(m.group(4))
            v = lastv if m.group(5) == b"=" else int(m.group(5))
            lastv = v
            if shipout is None:
                shipout = (tag, line)
            if skipped or (kind in "[(" and len(stack) > MAX_DEPTH):
                skipped += kind in "[("
                continue
            w, ht, dp = int(m.group(6) or 0), int(m.group(7) or 0), int(m.group(8) or 0)
            if kind in "[(":
                box = Box(kind, tag, line, h, v, w, ht, dp)
                stack[-1].kids.append(box)
                stack.append(box)
            elif kind in "vh":
                stack[-1].kids.append(Box(kind, tag, line, h, v, w, ht, dp))
            elif kind == "k":
                stack[-1].kids.append((min(h - w, h), max(h - w, h), kind, tag, line))
            elif kind == "r":
                stack[-1].kids.append((h, h + max(w, 0), kind, tag, line))
            else:
                stack[-1].kids.append((h, h, kind, tag, line))
        while stack:
            _close(stack.pop())
        return Sheet(root, shipout)

    # --- a point ---------------------------------------------------------------------

    def edit(self, page: int, x: float, y: float) -> tuple[int, int] | None:
        """``(tag, line)`` of the point (bp, SyncTeX's frame) on ``page``: in
        the deepest hbox holding it, else the nearest; None on a page with
        no text."""
        s = self.sheet(page)
        if not s:
            return None
        h, v = self._to_sp(x, y)
        box = _deepest(s.root, h, v) or _nearest(s.root, (h, v, h, v))
        while box is not None:
            kids, ends = box.kids, box.ends
            i = min(bisect.bisect_left(ends, h), len(kids) - 1)
            while i + 1 < len(kids) and ends[i + 1] == ends[i] and _end(kids[i]) < ends[i]:
                i += 1                       # a kid that ends behind the run (LuaTeX's kerns) answers for nothing
            k = kids[i]
            if type(k) is Box:
                if k.kind == "(" and k.kids and k.left <= h <= k.right:
                    box = k
                    continue
                return k.tag, k.line
            return _attr(box, i, not (k[0] <= h <= k[1] and k[1] > k[0]), s.shipout)
        return None

    # --- rectangles ------------------------------------------------------------------

    def query(self, page: int, rects, *, text: bool) -> list[tuple[int, int, float]]:
        """``[(tag, line, share)]`` of the rects (bp, SyncTeX's frame) on
        ``page``, heaviest first, the shares summing to 1; [] for nothing.

        ``text``: the rects are a text highlight's lines, each the font's
        box, taller than TeX's line: only its middle third, vertically,
        counts, which stays inside its own line's band. Otherwise their area
        does (an area, handwriting). The shipout line weighs
        ``SHIPOUT_WEIGHT`` (a footer alone still answers it). Rects that
        meet no text take the nearest hbox within ``MARGIN_BP`` (a mark in
        the margin beside a line)."""
        s = self.sheet(page)
        if not s:
            return []
        boxes = []
        for rect in rects:
            r = self._rect(rect)
            if text:
                third = (r[3] - r[1]) / 3
                r = (r[0], r[1] + third, r[2], r[3] - third)
            boxes.append(r)
        weights: dict = {}
        for r in boxes:
            _walk(s.root, r, weights, s.shipout)
        if not weights:
            margin = MARGIN_BP / self._k
            for r in boxes:
                near = _nearest(s.root, r)
                if near is not None and _gap(near, r) <= margin:
                    _walk(near, (r[0] - margin, r[1] - margin, r[2] + margin, r[3] + margin), weights, s.shipout)
        if s.shipout in weights:
            weights[s.shipout] *= SHIPOUT_WEIGHT
        total = sum(weights.values())
        if total <= 0:
            return []
        return sorted(((t, ln, w / total) for (t, ln), w in weights.items()), key=lambda e: (-e[2], e[0], e[1]))

    # --- the inverse -----------------------------------------------------------------

    def view(self, tag: int, line: int) -> list[tuple[int, float, float, float, float]]:
        """``[(page, x0, y0, x1, y1)]`` (bp, SyncTeX's frame): per hbox, the
        span of the stretches that came from ``(tag, line)``. The sheets
        are found by a byte search of the text, and only those parsed."""
        pages = set()
        for m in re.finditer(rb"\n[xkg$(\[hvr]%d,%d[:,]" % (tag, line), self._text):
            at = bisect.bisect_right(self._starts, m.start()) - 1
            if at >= 0:
                head = self._text[self._starts[at] + 2:self._text.find(b"\n", self._starts[at] + 1)]
                if head.isdigit():
                    pages.add(int(head))
        out = []
        for page in sorted(pages):
            s = self.sheet(page)
            if not s:
                continue
            for box in _boxes(s.root):
                if box.kind != "(":
                    continue
                prev, spans = box.left, []
                for i, k in enumerate(box.kids):
                    if type(k) is not Box and k[0] > prev and _attr(box, i, True, s.shipout) == (tag, line):
                        spans.append((prev, k[0]))
                    prev = max(prev, _end(k))
                if spans:
                    x0, y0 = self._to_bp(min(sp[0] for sp in spans), box.top)
                    x1, y1 = self._to_bp(max(sp[1] for sp in spans), box.bottom)
                    out.append((page, round(x0, 2), round(y0, 2), round(x1, 2), round(y1, 2)))
        return out


def _text(data: bytes) -> bytes:
    """The SyncTeX text of a file's bytes: gunzipped (``MAX_GZ`` bytes at
    most, ``MAX_TEXT`` of text), or as they are."""
    if data[:2] != b"\x1f\x8b":
        if len(data) > MAX_TEXT:
            raise SyncTeXError("too large")
        return data
    if len(data) > MAX_GZ:
        raise SyncTeXError("too large")
    try:
        text = zlib.decompressobj(31).decompress(data, MAX_TEXT + 1)
    except zlib.error as e:
        raise SyncTeXError(f"not a gzip file: {e}") from e
    if len(text) > MAX_TEXT:
        raise SyncTeXError("too large")
    return text


_cache: OrderedDict = OrderedDict()   # resolved path -> ((st_size, st_mtime_ns), SyncTeX)
_cache_lock = threading.Lock()


def load(path) -> SyncTeX:
    """The SyncTeX file at ``path``, opened once while its size and time
    hold (the last ``FILES`` kept). SyncTeXError for a file that is not
    one or is too large; OSError for one that cannot be read."""
    path = Path(path).resolve()
    st = path.stat()
    key, stamp = str(path), (st.st_size, st.st_mtime_ns)
    with _cache_lock:
        hit = _cache.get(key)
        if hit is not None and hit[0] == stamp:
            _cache.move_to_end(key)
            return hit[1]
    if st.st_size > max(MAX_GZ, MAX_TEXT):
        raise SyncTeXError("too large")
    syn = SyncTeX(_text(path.read_bytes()))
    with _cache_lock:
        _cache[key] = (stamp, syn)
        _cache.move_to_end(key)
        while len(_cache) > FILES:
            _cache.popitem(last=False)
    return syn
