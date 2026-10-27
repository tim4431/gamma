"""The three encodings a GoodNotes notebook is made of, read as untrusted
input (the format: docs/research/goodnotes-onenote-import.md; the reader
of a notebook built on them: gamma/goodnotes.py).

- **Protobuf.** Every ``.pb`` member and every page is a stream of
  ``<varint length><message>`` records (``records``). A message is parsed
  once into a ``Message``, whose accessors give a value of the type asked
  for or None: a damaged or unexpected field reads as missing, never as a
  crash. The group wire types never occur in GoodNotes files and are
  refused.
- **Apple's framed LZ4** (``lz4_frame``) wraps a stroke's geometry and a
  text element's runs: ``bv41`` compressed and ``bv4-`` stored blocks, then
  ``bv4$``. Each block's declared size is checked against what the frame
  may still produce before it is decoded, and a block never writes past
  it, so a block that lies about its size costs nothing.
- **tpl** (``tpl``), Troy Hanson's serialisation, is the geometry itself:
  a format string, then the values it describes. Every array's count is
  checked against the bytes left before anything is made of it, so a count
  of four billion is refused at once. GoodNotes stores every coordinate,
  radius and width as a ``u`` whose bits are a float32, so a ``u`` reads
  as that float.

Everything here is pure: bytes in, values out, CodecError for what does
not read.
"""

import math
import struct

MAX_RECORDS = 4_000_000     # records in one member (a page holds two per element)
MAX_FIELDS = 1_000_000      # fields in one message (a journal's page inventory holds one per element)
MAX_STRING = 1 << 20        # bytes of a string field worth decoding
MAX_FORMAT = 256            # characters of a tpl format string (GoodNotes' longest is 73)
MAX_NESTING = 8             # A( and S( nested in a tpl format

_TPL_SCALARS = {"c": "b", "j": "h", "v": "H", "i": "i", "u": "f", "I": "q", "U": "Q", "f": "d"}
_LZ4_MIN_MATCH = 4
_UUID_HEX = frozenset("0123456789ABCDEF")


class CodecError(ValueError):
    pass


class TooLarge(CodecError):
    """An LZ4 frame that would unpack past the limit it was read with."""


# --- protobuf -------------------------------------------------------------------------------

def _varint(data: bytes, pos: int) -> tuple[int, int]:
    """The varint at ``pos`` and the position after it."""
    value = shift = 0
    for i in range(pos, min(pos + 10, len(data))):
        byte = data[i]
        value |= (byte & 0x7F) << shift
        if byte < 0x80:
            return value, i + 1
        shift += 7
    raise CodecError("a varint runs past its end")


def records(data: bytes, limit: int = MAX_RECORDS) -> list[bytes]:
    """The records of a length-prefixed stream, in order. CodecError when a
    length runs past the data or the stream holds more than ``limit``."""
    return list(iter_records(data, limit))


def iter_records(data: bytes, limit: int = MAX_RECORDS):
    """The records of a length-prefixed stream one at a time, so a reader
    holds one record, not the stream's copy. CodecError, once the records
    before it are given, where a length runs past the data or past
    ``limit`` records."""
    pos, end, n = 0, len(data), 0
    while pos < end:
        if n >= limit:
            raise CodecError(f"more than {limit:,} records")
        length, pos = _varint(data, pos)
        if pos + length > end:
            raise CodecError("a record runs past the end of its member")
        yield data[pos:pos + length]
        pos += length
        n += 1


class Message:
    """One protobuf message's fields, as ``(number, wire type, value)``:
    a varint an int, fixed64 and fixed32 their raw bytes, a length-delimited
    field its bytes. Constructing one raises CodecError for a message that
    does not parse; ``read`` answers None instead."""

    __slots__ = ("fields",)

    def __init__(self, data: bytes):
        fields, pos, end = [], 0, len(data)
        while pos < end:
            if len(fields) >= MAX_FIELDS:
                raise CodecError(f"a message of more than {MAX_FIELDS:,} fields")
            tag, pos = _varint(data, pos)
            number, wire = tag >> 3, tag & 7
            if number == 0:
                raise CodecError("a field numbered 0")
            if wire == 0:
                value, pos = _varint(data, pos)
            elif wire in (1, 5):
                size = 8 if wire == 1 else 4
                if pos + size > end:
                    raise CodecError("a fixed field runs past its message")
                value, pos = data[pos:pos + size], pos + size
            elif wire == 2:
                length, pos = _varint(data, pos)
                if pos + length > end:
                    raise CodecError("a field runs past its message")
                value, pos = data[pos:pos + length], pos + length
            else:
                raise CodecError(f"wire type {wire}")
            fields.append((number, wire, value))
        self.fields = fields

    @classmethod
    def read(cls, data) -> "Message | None":
        if not isinstance(data, (bytes, bytearray)):
            return None
        try:
            return cls(bytes(data))
        except CodecError:
            return None

    def _last(self, number: int, wire: int):
        for n, w, value in reversed(self.fields):
            if n == number and w == wire:
                return value
        return None

    def has(self, number: int) -> bool:
        return any(n == number for n, _w, _v in self.fields)

    def int(self, number: int) -> int | None:
        return self._last(number, 0)

    def signed(self, number: int) -> int | None:
        """A varint read as a 64-bit two's complement integer."""
        value = self._last(number, 0)
        return None if value is None else (value - (1 << 64) if value >= 1 << 63 else value)

    def f32(self, number: int) -> float | None:
        """A fixed32 float; None when absent or not finite."""
        raw = self._last(number, 5)
        value = struct.unpack("<f", raw)[0] if raw is not None else None
        return value if value is not None and math.isfinite(value) else None

    def nan(self, number: int) -> bool:
        """Whether the fixed32 float is NaN (an erased fill's rect)."""
        raw = self._last(number, 5)
        return raw is not None and math.isnan(struct.unpack("<f", raw)[0])

    def f64(self, number: int) -> float | None:
        raw = self._last(number, 1)
        value = struct.unpack("<d", raw)[0] if raw is not None else None
        return value if value is not None and math.isfinite(value) else None

    def bytes(self, number: int) -> bytes | None:
        return self._last(number, 2)

    def str(self, number: int) -> str | None:
        raw = self._last(number, 2)
        if raw is None or len(raw) > MAX_STRING:
            return None
        try:
            return raw.decode("utf-8")
        except UnicodeDecodeError:
            return None

    def uuid(self, number: int) -> str | None:
        """A UUID string, upper case; None for anything else."""
        value = self.str(number)
        if value is None or len(value) != 36:
            return None
        value = value.upper()
        if any(value[i] != "-" for i in (8, 13, 18, 23)) or not set(value.replace("-", "")) <= _UUID_HEX:
            return None
        return value

    def msg(self, number: int) -> "Message | None":
        return Message.read(self._last(number, 2))

    def msgs(self, number: int) -> list["Message"]:
        """Every occurrence of a repeated message field that parses, in order."""
        found = (Message.read(v) for n, w, v in self.fields if n == number and w == 2)
        return [m for m in found if m is not None]

    def only(self) -> tuple[int, "Message"] | None:
        """``(number, message)`` of a message whose one field is a message
        (a page's content record: its number is the element's kind)."""
        if len(self.fields) != 1 or self.fields[0][1] != 2:
            return None
        body = Message.read(self.fields[0][2])
        return (self.fields[0][0], body) if body is not None else None


# --- Apple's framed LZ4 ---------------------------------------------------------------------

def _lz4_length(src: bytes, pos: int, value: int) -> tuple[int, int]:
    """A nibble of 15 extended by the bytes after it (each added, until one is not 255)."""
    end = len(src)
    while True:
        if pos >= end:
            raise CodecError("an LZ4 length runs past its block")
        byte = src[pos]
        pos += 1
        value += byte
        if byte != 255:
            return value, pos


def _lz4_block(src: bytes, out: bytearray, size: int) -> None:
    """Decode one LZ4 block onto ``out`` (matches may reach into what the
    frame's earlier blocks wrote), which must grow by exactly ``size``."""
    stop = len(out) + size
    pos, end = 0, len(src)
    while pos < end:
        token = src[pos]
        pos += 1
        literals = token >> 4
        if literals == 15:
            literals, pos = _lz4_length(src, pos, 15)
        if pos + literals > end or len(out) + literals > stop:
            raise CodecError("LZ4 literals run past their block")
        out += src[pos:pos + literals]
        pos += literals
        if pos == end:
            break  # the last sequence has no match
        if pos + 2 > end:
            raise CodecError("an LZ4 match offset runs past its block")
        offset = src[pos] | src[pos + 1] << 8
        pos += 2
        if offset == 0 or offset > len(out):
            raise CodecError("an LZ4 match reaches outside the output")
        length = token & 15
        if length == 15:
            length, pos = _lz4_length(src, pos, 15)
        length += _LZ4_MIN_MATCH
        if len(out) + length > stop:
            raise CodecError("an LZ4 match runs past its block")
        start = len(out) - offset
        if offset >= length:
            out += out[start:start + length]
        else:  # the match overlaps what it writes: its offset's bytes repeat
            pattern = bytes(out[start:])
            out += (pattern * (length // offset + 1))[:length]
    if len(out) != stop:
        raise CodecError("an LZ4 block decodes to another size than it declares")


def lz4_frame(data: bytes, limit: int) -> bytes:
    """The bytes of an Apple LZ4 frame, at most ``limit`` of them.
    TooLarge for one that would exceed ``limit`` (checked block by block
    before a block is decoded), CodecError for one that does not read."""
    out = bytearray()
    pos, end = 0, len(data)
    while True:
        magic = data[pos:pos + 4]
        pos += 4
        if magic == b"bv4$":
            return bytes(out)
        if magic == b"bv41":
            if pos + 8 > end:
                raise CodecError("an LZ4 block header runs past the frame")
            size, packed = struct.unpack_from("<II", data, pos)
            pos += 8
        elif magic == b"bv4-":  # stored: one size word, the bytes as they are
            if pos + 4 > end:
                raise CodecError("a stored block header runs past the frame")
            size = packed = struct.unpack_from("<I", data, pos)[0]
            pos += 4
        else:
            raise CodecError("not an Apple LZ4 frame" if pos == 4 else "an Apple LZ4 frame without its end")
        if pos + packed > end:
            raise CodecError("an LZ4 block runs past the frame")
        if len(out) + size > limit:
            raise TooLarge(f"an LZ4 frame past {limit:,} bytes")
        if magic == b"bv41":
            _lz4_block(data[pos:pos + packed], out, size)
        else:
            out += data[pos:pos + size]
        pos += packed


def is_lz4_frame(data) -> bool:
    return isinstance(data, (bytes, bytearray)) and data[:4] in (b"bv41", b"bv4-", b"bv4$")


# --- tpl ------------------------------------------------------------------------------------

def _tpl_nodes(fmt: str) -> list:
    """A tpl format as a tree: a scalar letter, ``("A", [nodes])`` an array
    (a count, then that many of its nodes) or ``("S", [nodes])`` a struct."""
    stack, depth = [[]], 0
    i = 0
    while i < len(fmt):
        ch = fmt[i]
        if ch in "AS":
            if i + 1 >= len(fmt) or fmt[i + 1] != "(" or depth >= MAX_NESTING:
                raise CodecError("a tpl format that does not parse")
            node = (ch, [])
            stack[-1].append(node)
            stack.append(node[1])
            depth += 1
            i += 2
            continue
        if ch == ")":
            if depth == 0 or not stack[-1]:
                raise CodecError("a tpl format that does not parse")
            stack.pop()
            depth -= 1
        elif ch in _TPL_SCALARS:
            stack[-1].append(ch)
        else:
            raise CodecError("a tpl format that does not parse")
        i += 1
    if depth:
        raise CodecError("a tpl format that does not parse")
    return stack[0]


def _flat(nodes: list) -> str | None:
    """The struct letters of nodes that are scalars or structs of them, for
    one ``struct`` read; None when an array is among them."""
    out = []
    for node in nodes:
        if isinstance(node, str):
            out.append(_TPL_SCALARS[node])
        elif node[0] == "S" and (inner := _flat(node[1])) is not None:
            out.append(inner)
        else:
            return None
    return "".join(out)


def _least(node) -> int:
    """The fewest bytes a node takes: an array its count alone."""
    if isinstance(node, str):
        return struct.calcsize("<" + _TPL_SCALARS[node])
    return 4 if node[0] == "A" else sum(_least(n) for n in node[1])


def _tpl_value(node, data: bytes, pos: int):
    if isinstance(node, str):
        code = "<" + _TPL_SCALARS[node]
        size = struct.calcsize(code)
        if pos + size > len(data):
            raise CodecError("a tpl value runs past the image")
        return struct.unpack_from(code, data, pos)[0], pos + size
    kind, inner = node
    if kind == "S":
        values = []
        for sub in inner:
            value, pos = _tpl_value(sub, data, pos)
            values.append(value)
        return tuple(values), pos
    if pos + 4 > len(data):
        raise CodecError("a tpl count runs past the image")
    count = struct.unpack_from("<I", data, pos)[0]
    pos += 4
    if count * sum(_least(n) for n in inner) > len(data) - pos:
        raise CodecError("a tpl array longer than its image")
    letters = _flat(inner)
    if letters is not None:  # scalars or structs of them: one read for the whole array
        code = "<" + letters
        size = struct.calcsize(code)
        rows = list(struct.iter_unpack(code, data[pos:pos + size * count]))
        pos += size * count
        return ([row[0] for row in rows] if len(letters) == 1 else rows), pos
    items = []
    for _ in range(count):
        values = []
        for sub in inner:
            value, pos = _tpl_value(sub, data, pos)
            values.append(value)
        items.append(values[0] if len(inner) == 1 else tuple(values))
    return items, pos


def tpl(data: bytes, formats=None) -> tuple[str, list]:
    """``(format, values)`` of a tpl image, one value per top-level node: a
    number, a tuple for a struct, a list for an array (of numbers, or of
    tuples when its element is several values). CodecError for an image
    that does not read: a bad header, a big-endian image, a length that is
    not the image's, a format past MAX_FORMAT, one that does not parse or
    is none of ``formats`` (checked before a value is read), a value past
    the end, bytes left over."""
    if len(data) < 9 or data[:3] != b"tpl" or data[3] & 1:
        raise CodecError("not a tpl image")
    if struct.unpack_from("<I", data, 4)[0] != len(data):
        raise CodecError("a tpl image of another length than it says")
    stop = data.find(b"\0", 8, 8 + MAX_FORMAT + 1)
    if stop < 0:
        raise CodecError("a tpl format past its limit")
    try:
        fmt = data[8:stop].decode("ascii")
    except UnicodeDecodeError:
        raise CodecError("a tpl format that does not parse") from None
    if formats is not None and fmt not in formats:
        raise CodecError("a tpl format this reader does not know")
    pos, values = stop + 1, []
    for node in _tpl_nodes(fmt):
        value, pos = _tpl_value(node, data, pos)
        values.append(value)
    if pos != len(data):
        raise CodecError("bytes after a tpl image's values")
    return fmt, values
