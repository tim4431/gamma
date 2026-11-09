"""A Unicode cmap for a composite font's program that has none, so a
translation can be set in the paper's own font by character
(``pdf_translated._paper_font``; docs/dev/translation.md "Exporting the
translation").

A composite (Type0) font with the Identity encoding names its glyphs by CID,
and its program often maps no character to a glyph: LuaLaTeX and XeLaTeX
embed an OpenType font as a bare CID-keyed CFF program (``cid``), and many
TrueType programs under a CIDFontType2 (``ttf``) carry no usable cmap.
MuPDF's HTML engine finds a letter's glyph only through the program's own
cmap, so such a font draws nothing. The file's ToUnicode CMap says which
character each CID is; ``unicode_program`` turns that into a cmap and
gives back a program that has it: a bare CID-keyed CFF written again with
each glyph at its CID and wrapped in an OpenType file, a TrueType program
with its cmap table replaced (its glyph index the CID through
``/CIDToGIDMap``). pdf.js does the same for the overlay from the same CMap
(``toUnicodeExtraMap``). Ligatures (a CID that names several characters)
and an OpenType program (``otf``, which keeps its own cmap) are left as
they are.

Called under ``pdf_text._lock``, with the PyMuPDF document of the file.
"""

import re
import struct

# (platform 3, encoding 10): Unicode, every plane, as format 12 groups.
_PLATFORM, _ENCODING = 3, 10
_IDENTITY = ("/Identity-H", "/Identity-V")


def unicode_program(mupdf, doc, xref: int, ext: str, program: bytes):
    """``program``, the font ``xref``'s, with a Unicode cmap built from the
    file's ToUnicode CMap, or None when the font is no composite font with
    the Identity encoding and a ToUnicode, the program is neither a bare
    CID-keyed CFF nor TrueType, or no CID is named, and for TrueType whose
    own cmap has every character named. → (bytes, ext)."""
    try:
        if _name(doc, xref, "Subtype") != "/Type0" or _name(doc, xref, "Encoding") not in _IDENTITY:
            return None
        to_unicode = _ref(doc, xref, "ToUnicode")
        descendant = _ref(doc, xref, "DescendantFonts")
        if to_unicode is None or descendant is None:
            return None
        low = mupdf.mupdf
        pdf = low.pdf_document_from_fz_document(doc.this)
        cmap = low.pdf_load_embedded_cmap(pdf, low.pdf_new_indirect(pdf, to_unicode, 0))

        def char(cid):
            count, code = low.pdf_lookup_cmap_full(cmap, cid)[:2]
            if count != 1 or not 0x20 <= code <= 0x10FFFF or 0xD800 <= code <= 0xDFFF:
                return None  # a ligature names several, a control none worth setting
            return code

        if ext == "cid" and program[:1] == b"\x01":
            cff = _cid_cff(program)
            if cff is None:
                return None
            cff, cids = cff
            font = mupdf.Font(fontbuffer=program)  # bare: FreeType indexes a CID-keyed program by CID
            chars = _chars((cid, char(cid)) for cid in cids)
            if not chars:
                return None
            advances = [0] * (cids[-1] + 1)
            for cid in cids:
                advances[cid] = round(low.fz_advance_glyph(font.this, cid, 0) * 1000)
            ascent = round(low.fz_font_ascender(font.this) * 1000)
            descent = round(low.fz_font_descender(font.this) * 1000)
            return _otf(cff, chars, advances, ascent, descent, _font_name(doc, xref)), "otf"
        if ext == "ttf":
            to_gid = _cid_to_gid(doc, descendant)
            tables = _sfnt_tables(program)
            if tables is None or b"maxp" not in tables:
                return None
            glyphs = struct.unpack(">H", tables[b"maxp"][4:6])[0]
            count = len(to_gid) if to_gid is not None else glyphs
            pairs = ((to_gid[cid] if to_gid is not None else cid, char(cid)) for cid in range(count))
            chars = _chars((gid, code) for gid, code in pairs if 0 < gid < glyphs)
            own = mupdf.Font(fontbuffer=program)
            if all(own.has_glyph(code) for code in chars):
                return None  # its own cmap names them all
            tables[b"cmap"] = _cmap(chars)
            return _sfnt(program[:4], tables), "ttf"
    except Exception:  # an unreadable CMap or program: the block keeps the serif
        return None
    return None


def _name(doc, xref, key):
    kind, value = doc.xref_get_key(xref, key)
    return value if kind == "name" else None


def _ref(doc, xref, key):
    kind, value = doc.xref_get_key(xref, key)
    found = re.search(r"(\d+) 0 R", value) if kind in ("xref", "array") else None
    return int(found.group(1)) if found else None


def _font_name(doc, xref) -> str:
    name = (_name(doc, xref, "BaseFont") or "/Paper").lstrip("/")
    return re.sub(r"^[A-Z]{6}\+|-Identity-[HV]$", "", name)[:31] or "Paper"


def _cid_to_gid(doc, descendant):
    """The descendant's ``/CIDToGIDMap`` as a list (CID → GID), or None for
    Identity."""
    stream = _ref(doc, descendant, "CIDToGIDMap")
    if stream is None:
        return None
    data = doc.xref_stream(stream) or b""
    return list(struct.unpack(f">{len(data) // 2}H", data[: len(data) // 2 * 2]))


def _chars(pairs) -> dict:
    """{code point: glyph} from (glyph, code point or None), the first glyph
    named for a character kept."""
    out = {}
    for gid, code in pairs:
        if gid and code is not None:
            out.setdefault(code, gid)
    return out


# --- CFF: each glyph at its CID ----------------------------------------
#
# In an OpenType file a CID-keyed CFF's glyph index is its GID, but MuPDF
# reads a CIDFontType0's codes as CIDs and finds the glyph through the
# charset, so the text it writes in the font (by glyph index) draws empty
# glyphs wherever GID and CID differ. The program is written again with
# every glyph at its CID (an empty one where the subset has none), so the
# two are the same.


def _index(data: bytes, at: int):
    """A CFF INDEX at ``at`` → (its items, where it ends)."""
    count = struct.unpack(">H", data[at:at + 2])[0]
    if count == 0:
        return [], at + 2
    size = data[at + 2]
    offsets = [int.from_bytes(data[at + 3 + k * size:at + 3 + (k + 1) * size], "big") for k in range(count + 1)]
    base = at + 2 + (count + 1) * size
    return [data[base + offsets[k]:base + offsets[k + 1]] for k in range(count)], base + offsets[-1]


def _write_index(items) -> bytes:
    if not items:
        return b"\0\0"
    offsets, at = [1], 1
    for item in items:
        at += len(item)
        offsets.append(at)
    return struct.pack(f">HB{len(offsets)}I", len(items), 4, *offsets) + b"".join(items)


def _dict(data: bytes) -> dict:
    """A CFF DICT → {operator: (operands, its bytes)}, a two-byte operator
    as 1200 + its second byte, a real read as 0 (its bytes kept)."""
    out, operands, start, k = {}, [], 0, 0
    while k < len(data):
        b = data[k]
        if b <= 21:
            op = 1200 + data[k + 1] if b == 12 else b
            k += 2 if b == 12 else 1
            out[op], operands, start = (operands, data[start:k]), [], k
        elif b == 28:
            operands.append(struct.unpack(">h", data[k + 1:k + 3])[0])
            k += 3
        elif b == 29:
            operands.append(struct.unpack(">i", data[k + 1:k + 5])[0])
            k += 5
        elif b == 30:  # a real: read past its nibbles
            k += 1
            while k < len(data) and (data[k] & 0x0F) != 0x0F and (data[k] >> 4) != 0x0F:
                k += 1
            k += 1
            operands.append(0)
        elif 32 <= b <= 246:
            operands.append(b - 139)
            k += 1
        elif 247 <= b <= 250:
            operands.append((b - 247) * 256 + data[k + 1] + 108)
            k += 2
        elif 251 <= b <= 254:
            operands.append(-(b - 251) * 256 - data[k + 1] - 108)
            k += 2
        else:
            raise ValueError("CFF DICT")
    return out


def _op(op: int, *operands: int) -> bytes:
    """An operator with integer operands, each in five bytes, so a DICT's
    length does not hang on the offsets in it."""
    code = bytes((12, op - 1200)) if op >= 1200 else bytes((op,))
    return b"".join(b"\x1d" + struct.pack(">i", v) for v in operands) + code


_PRIVATE, _SUBRS, _CHARSET, _ENCODING_OP, _CHARSTRINGS = 18, 19, 15, 16, 17
_ROS, _CIDCOUNT, _FDARRAY, _FDSELECT = 1230, 1234, 1236, 1237
_ENDCHAR = b"\x0e"


def _cff_cids(cff: bytes, top: dict, glyphs: int):
    """The CID of each glyph, by GID, from the charset."""
    at = top[_CHARSET][0][0]
    form, at, cids = cff[at], at + 1, [0]
    if form == 0:
        cids += struct.unpack(f">{glyphs - 1}H", cff[at:at + 2 * (glyphs - 1)])
    elif form in (1, 2):
        left_size = 1 if form == 1 else 2
        while len(cids) < glyphs:
            first = struct.unpack(">H", cff[at:at + 2])[0]
            left = int.from_bytes(cff[at + 2:at + 2 + left_size], "big")
            cids += range(first, first + left + 1)
            at += 2 + left_size
    else:
        raise ValueError("CFF charset")
    return cids[:glyphs]


def _fd_select(cff: bytes, at: int, glyphs: int):
    """The Font DICT of each glyph, by GID."""
    form = cff[at]
    if form == 0:
        return list(cff[at + 1:at + 1 + glyphs])
    if form != 3:
        raise ValueError("CFF FDSelect")
    ranges = struct.unpack(">H", cff[at + 1:at + 3])[0]
    out = []
    for k in range(ranges):
        first, fd = struct.unpack(">HB", cff[at + 3 + 3 * k:at + 6 + 3 * k])
        end = struct.unpack(">H", cff[at + 6 + 3 * k:at + 8 + 3 * k])[0]
        out += [fd] * (end - first)
    return out[:glyphs] + [0] * (glyphs - len(out))


def _cid_cff(cff: bytes):
    """A CID-keyed CFF program written again with each glyph at its CID
    (the note above), or None for one that is not CID-keyed. → (the
    program, the CIDs it has)."""
    _, at = _index(cff, cff[2])  # the Name INDEX after the header
    names = cff[cff[2]:at]
    tops, at = _index(cff, at)
    top = _dict(tops[0])
    if any(op not in top for op in (_ROS, _CHARSTRINGS, _CHARSET, _FDARRAY, _FDSELECT)):
        return None
    _, after_strings = _index(cff, at)
    _, after_gsubrs = _index(cff, after_strings)
    strings_gsubrs = cff[at:after_gsubrs]
    charstrings, _ = _index(cff, top[_CHARSTRINGS][0][0])
    cids = _cff_cids(cff, top, len(charstrings))
    selects = _fd_select(cff, top[_FDSELECT][0][0], len(charstrings))
    count = max(cids) + 1
    glyphs, fds = [_ENDCHAR] * count, [0] * count
    for gid, cid in enumerate(cids):
        glyphs[cid], fds[cid] = charstrings[gid], selects[gid]
    privates = []  # each Font DICT's Private DICT and the local Subrs after it, as one piece
    fd_dicts, _ = _index(cff, top[_FDARRAY][0][0])
    for raw in fd_dicts:
        size, offset = _dict(raw)[_PRIVATE][0]
        end = offset + size
        subrs = _dict(cff[offset:end]).get(_SUBRS)
        if subrs:
            end = max(end, _index(cff, offset + subrs[0][0])[1])
        privates.append((raw, size, cff[offset:end]))
    kept = b"".join(raw for op, (_, raw) in top.items()
                    if op not in (_CHARSET, _ENCODING_OP, _CHARSTRINGS, _PRIVATE, _CIDCOUNT, _FDARRAY, _FDSELECT))

    def fd_dict(raw, size, offset):
        return b"".join(r for op, (_, r) in _dict(raw).items() if op != _PRIVATE) + _op(_PRIVATE, size, offset)

    def top_dict(charset, select, strings, array):
        return kept + _op(_CIDCOUNT, max(count, top.get(_CIDCOUNT, ([0],))[0][0])) + _op(_CHARSET, charset) + \
            _op(_FDSELECT, select) + _op(_CHARSTRINGS, strings) + _op(_FDARRAY, array)

    head = b"\x01\x00\x04\x04"
    charset = b"\x02" + struct.pack(">HH", 1, count - 2) if count > 1 else b"\x00"
    select = b"\x00" + bytes(fds)
    strings = _write_index(glyphs)
    at = len(head) + len(names) + len(_write_index([top_dict(0, 0, 0, 0)])) + len(strings_gsubrs)
    at_select = at + len(charset)
    at_strings = at_select + len(select)
    at_array = at_strings + len(strings)
    at_private = at_array + len(_write_index([fd_dict(raw, size, 0) for raw, size, _ in privates]))
    array, body = [], b""
    for raw, size, piece in privates:
        array.append(fd_dict(raw, size, at_private + len(body)))
        body += piece
    out = (head + names + _write_index([top_dict(at, at_select, at_strings, at_array)]) + strings_gsubrs
           + charset + select + strings + _write_index(array) + body)
    return out, sorted(set(cids))


# --- sfnt: the font file around it --------------------------------------


def _sfnt_tables(font: bytes):
    """A TrueType or OpenType file's tables, {tag: bytes}, or None."""
    if font[:4] not in (b"\x00\x01\x00\x00", b"true", b"OTTO"):
        return None
    count = struct.unpack(">H", font[4:6])[0]
    tables = {}
    for k in range(count):
        tag, _, offset, length = struct.unpack(">4sIII", font[12 + 16 * k:28 + 16 * k])
        tables[tag] = font[offset:offset + length]
    return tables


def _checksum(data: bytes) -> int:
    data += b"\0" * (-len(data) % 4)
    return sum(struct.unpack(f">{len(data) // 4}I", data)) & 0xFFFFFFFF


def _sfnt(version: bytes, tables: dict) -> bytes:
    """An sfnt file of ``tables``, each 4-byte aligned."""
    tags = sorted(tables)
    count = len(tags)
    power = 1 << (count.bit_length() - 1)
    head = version + struct.pack(">HHHH", count, power * 16, power.bit_length() - 1, count * 16 - power * 16)
    records, body, offset = b"", b"", 12 + 16 * count
    for tag in tags:
        data = tables[tag]
        records += struct.pack(">4sIII", tag, _checksum(data), offset + len(body), len(data))
        body += data + b"\0" * (-len(data) % 4)
    return head + records + body


def _cmap(chars: dict) -> bytes:
    """A cmap of one format 12 subtable, (3, 10)."""
    groups = []
    for code in sorted(chars):
        gid = chars[code]
        if groups and groups[-1][1] == code - 1 and groups[-1][2] + code - groups[-1][0] == gid:
            groups[-1][1] = code
        else:
            groups.append([code, code, gid])
    body = b"".join(struct.pack(">III", *g) for g in groups)
    table = struct.pack(">HHIII", 12, 0, 16 + len(body), 0, len(groups)) + body
    return struct.pack(">HHHHI", 0, 1, _PLATFORM, _ENCODING, 12) + table


def _names(name: str) -> bytes:
    """A name table giving the font's family, full and PostScript name, and
    no style, which MuPDF would add to the name."""
    text = name.encode("utf-16-be")
    ids = (1, 4, 6)
    values = [text, text, text]
    records, strings = b"", b""
    for nid, value in zip(ids, values):
        records += struct.pack(">HHHHHH", 3, 1, 0x409, nid, len(value), len(strings))
        strings += value
    return struct.pack(">HHH", 0, len(ids), 6 + 12 * len(ids)) + records + strings


def _otf(cff: bytes, chars: dict, advances, ascent: int, descent: int, name: str) -> bytes:
    """A bare CFF program as an OpenType file (1000 units an em, CFF's own),
    its cmap ``chars``, its advances by GID, its line metrics the bare
    program's as MuPDF reads them."""
    glyphs = len(advances)
    head = struct.pack(">IIIIHHqqhhhhHHhhh", 0x00010000, 0x00010000, 0, 0x5F0F3CF5, 0x000B, 1000, 0, 0,
                       0, descent, 1000, ascent, 0, 3, 2, 0, 0)
    hhea = struct.pack(">Ihhh" + "H" + "hhh" + "hhh" + "hhhh" + "h" + "H", 0x00010000, ascent, descent, 0,
                       max(advances, default=0), 0, 0, max(advances, default=0), 1, 0, 0, 0, 0, 0, 0, 0, glyphs)
    hmtx = b"".join(struct.pack(">Hh", max(0, a), 0) for a in advances)
    tables = {
        b"CFF ": cff,
        b"cmap": _cmap(chars),
        b"head": head,
        b"hhea": hhea,
        b"hmtx": hmtx,
        b"maxp": struct.pack(">IH", 0x00005000, glyphs),
        b"name": _names(name),
        b"post": struct.pack(">IIhhIIIII", 0x00030000, 0, -100, 50, 0, 0, 0, 0, 0),
    }
    return _sfnt(b"OTTO", tables)
