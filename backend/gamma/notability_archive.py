"""Reading the plists a Notability note is made of, as untrusted input
(docs/research/notability-format.md has the format).

A note's ``Session.plist`` and ``metadata.plist`` are keyed archives
(NSKeyedArchiver, or Ginger Labs' subclass, GLKeyedArchiver): a binary
plist holding a flat ``$objects`` table whose values point at each other by
UID. Inside them are plain plists (dash styles, shapes, ink groups) and, in
an ink group, whole archives again. Everything here is read behind guards:

- ``load_plist`` refuses a plist past its size limit unread, and checks a
  binary plist's 32-byte trailer before plistlib allocates the two tables
  its object count asks for, and the references its arrays and dictionaries
  hold before plistlib makes a tuple and a list slot of each;
- an ``Archive`` is never walked: every read is a named key from the root,
  each value dereferenced (UID chains followed at most MAX_UID_HOPS hops)
  and type-checked where it is read, so a cycle or a damaged object reads as
  a missing value, never as a loop or a crash;
- what an object reads as (a string, an array, a mapping) is made once, so
  an object the archive names a million times costs one read, not a million;
- numbers are finite or nothing, and geometry strings ("{x, y}", "{{x, y},
  {w, h}}") give exactly the count asked for or nothing.

Both container forms of NSArray and NSDictionary are read alike (a list
form, and the indexed ``NS.object.N`` form older notes use), since a reader
that knows one form sees an old note as empty.
"""

import datetime
import math
import plistlib
import re
import struct
import unicodedata
from xml.parsers.expat import ExpatError

MAX_PLIST_OBJECTS = 1_000_000   # a binary plist's object table, checked before plistlib allocates it
MAX_PLIST_REFS = 8_000_000      # references in a binary plist's arrays and dictionaries, in all
MAX_UID_HOPS = 64               # UID → UID chains followed in one lookup
MAX_NAME = 1024                 # characters in a name worth reading: a class, a font, a file
APPLE_EPOCH = 978307200         # 2001-01-01T00:00:00Z in Unix seconds
ARCHIVERS = ("NSKeyedArchiver", "GLKeyedArchiver")

_NUMBER = re.compile(r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?")
_MAX_GEOMETRY = 256       # characters in a geometry string worth reading
_MAX_INDEX_DIGITS = 9     # an NS.object.N index


class ArchiveError(ValueError):
    pass


def load_plist(data: bytes, *, limit: int):
    """A plist's value, read behind guards; ArchiveError for anything off.

    - Over ``limit`` bytes: refused without being read.
    - A binary plist (``bplist00``): plistlib allocates two tables as long
      as the object count, so the trailer is checked first. The offset and
      reference sizes must be 1..8, the object count 1..MAX_PLIST_OBJECTS
      and the top object inside it, and the offset table must start at or
      past byte 8 and end before the trailer. Then every object must lie
      inside the plist, and its arrays and dictionaries may hold at most
      MAX_PLIST_REFS references in all (``_count_refs``).
    - Anything else must be an XML plist (plistlib refuses entity
      declarations).
    - plistlib's own errors, RecursionError (nesting deeper than Python's
      stack), UnicodeDecodeError and expat errors all become ArchiveError."""
    if not isinstance(data, (bytes, bytearray)):
        raise ArchiveError("not a plist")
    if len(data) > limit:
        raise ArchiveError(f"a plist of {len(data)} bytes, past {limit}")
    binary = data[:8] == b"bplist00"
    if binary:
        if len(data) < 8 + 32:
            raise ArchiveError("a binary plist cut short")
        offset_size, ref_size, count, top, table = struct.unpack(">6xBBQQQ", data[-32:])
        if not (1 <= offset_size <= 8 and 1 <= ref_size <= 8):
            raise ArchiveError("a binary plist with bad integer sizes")
        if not (1 <= count <= MAX_PLIST_OBJECTS and top < count):
            raise ArchiveError(f"a binary plist of {count} objects")
        if table < 8 or table + count * offset_size > len(data) - 32:
            raise ArchiveError("a binary plist whose offset table lies outside it")
        _count_refs(data, offset_size, count, table)
    try:  # dates come naive, in UTC (``aware_datetime`` is Python 3.13's; the server runs 3.12)
        return plistlib.loads(bytes(data), fmt=plistlib.FMT_BINARY if binary else None)
    except (plistlib.InvalidFileException, ValueError, TypeError, KeyError, IndexError, OverflowError,
            struct.error, RecursionError, ExpatError) as exc:
        raise ArchiveError(f"not a readable plist: {exc.__class__.__name__}") from None


def _count_refs(data: bytes, offset_size: int, count: int, table: int) -> None:
    """ArchiveError when a binary plist's objects do not all lie inside it,
    or when its arrays and dictionaries hold more than MAX_PLIST_REFS
    references in all. plistlib makes a tuple slot and a list slot of every
    reference (17 bytes of memory for a 1-byte one), so the count is read
    off each container's marker, as plistlib reads it, before it does."""
    refs, end = 0, len(data) - 32
    for i in range(count):
        at = table + i * offset_size
        offset = int.from_bytes(data[at:at + offset_size], "big")
        if not 8 <= offset < end:
            raise ArchiveError("a binary plist with an object outside it")
        marker = data[offset]
        if marker & 0xF0 not in (0xA0, 0xD0):  # an array, a dictionary
            continue
        n = marker & 0x0F
        if n == 0x0F:  # the count follows as an int of 1 << (its marker & 3) bytes
            width = 1 << (data[offset + 1] & 0x3) if offset + 1 < end else 1
            n = int.from_bytes(data[offset + 2:offset + 2 + width], "big")
        refs += 2 * n if marker & 0xF0 == 0xD0 else n
        if refs > MAX_PLIST_REFS:
            raise ArchiveError(f"a binary plist of more than {MAX_PLIST_REFS:,} references")


def parse_numbers(text, n: int) -> tuple[float, ...] | None:
    """The numbers of a geometry string ("{x, y}", "{{x, y}, {w, h}}",
    "{loc, len}") when there are exactly ``n`` of them and all are finite;
    None otherwise."""
    if not isinstance(text, str) or len(text) > _MAX_GEOMETRY:
        return None
    values = _NUMBER.findall(text)
    if len(values) != n:
        return None
    try:
        out = tuple(float(v) for v in values)
    except (ValueError, OverflowError):
        return None
    return out if all(math.isfinite(v) for v in out) else None


def finite(value) -> float | None:
    """A finite int or float as a float, never a bool; None otherwise."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        value = float(value)
    except OverflowError:
        return None
    return value if math.isfinite(value) else None


def whole(value) -> int | None:
    """An int, or a whole finite float, never a bool; None otherwise."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and math.isfinite(value) and value.is_integer():
        return int(value)
    return None


def _unit(value: float) -> float:
    return min(1.0, max(0.0, value))


def color_string(text) -> tuple[float, float, float, float] | None:
    """Notability's cross-platform colour, "r,g,b,a" with 15 decimals, as
    floats clamped to 0..1; None for anything else."""
    if not isinstance(text, str) or len(text) > _MAX_GEOMETRY:
        return None
    parts = text.split(",")
    if len(parts) != 4:
        return None
    try:
        values = [float(p) for p in parts]
    except ValueError:
        return None
    if not all(math.isfinite(v) for v in values):
        return None
    return tuple(_unit(v) for v in values)


class Archive:
    """One keyed archive: ``$archiver`` NSKeyedArchiver or GLKeyedArchiver,
    its ``$objects``, and the root taken from ``$top`` ("$0", else "root",
    else its first value). Every read is a named key from the root (the
    reader never walks the graph). Every value is dereferenced and
    type-checked where it is read, so a cycle or a damaged object reads as
    a missing value, never as a loop or a crash."""

    def __init__(self, data: bytes, *, limit: int):
        top = load_plist(data, limit=limit)
        if not isinstance(top, dict) or top.get("$archiver") not in ARCHIVERS:
            raise ArchiveError("not a keyed archive")
        objects, tops = top.get("$objects"), top.get("$top")
        if not isinstance(objects, list) or not isinstance(tops, dict) or not tops:
            raise ArchiveError("a keyed archive without objects")
        self.objects = objects
        self._made: dict = {}  # (kind, id) → (the object, what it reads as): see ``_once``
        ref = tops["$0"] if "$0" in tops else tops["root"] if "root" in tops else next(iter(tops.values()))
        root = self.deref(ref)
        if not isinstance(root, dict):
            raise ArchiveError("the archive's root is no object")
        self.root = root

    def deref(self, value):
        """``value`` with its UIDs followed (index-checked, at most
        MAX_UID_HOPS); "$null", an index out of range and a chain too long
        read as None."""
        for _ in range(MAX_UID_HOPS):
            if not isinstance(value, plistlib.UID):
                break
            index = value.data
            if not 0 <= index < len(self.objects):
                return None
            value = self.objects[index]
        else:
            return None
        return None if isinstance(value, str) and value == "$null" else value

    def get(self, obj, key, default=None):
        """``obj[key]`` dereferenced; ``default`` when ``obj`` is no dict,
        lacks the key, or the key holds nil."""
        if not isinstance(obj, dict) or key not in obj:
            return default
        value = self.deref(obj[key])
        return default if value is None else value

    def _once(self, kind: str, value, make):
        """``make(value)``, made once per object and kept, so what the
        archive names many times is read once. The entry holds ``value``
        too, so its id is not reused while the archive lives. Callers must
        not change what they get."""
        key = (kind, id(value))
        made = self._made.get(key)
        if made is None:
            made = self._made[key] = (value, make(value))
        return made[1]

    def class_name(self, obj) -> str:
        """The ``$classname`` of the object's ``$class``; "" when none, or
        when it is longer than MAX_NAME."""
        cls = self.deref(obj.get("$class")) if isinstance(obj, dict) else None
        name = cls.get("$classname") if isinstance(cls, dict) else None
        return name if isinstance(name, str) and len(name) <= MAX_NAME else ""

    def is_a(self, obj, *names: str) -> bool:
        """Whether the object's class is one of ``names``, matched without
        the module a Swift class carries (``Notability.``, ``GLModel.``)."""
        return self.class_name(obj).rsplit(".", 1)[-1] in names

    def array(self, value) -> list:
        """An NSArray's elements, dereferenced: ``NS.objects``, or
        ``NS.object.0`` … ``NS.object.n-1`` in any key order; a plain list
        as it is; [] for anything else. Made once per object (``_once``)."""
        return self._once("array", self.deref(value), self._array)

    def _array(self, value) -> list:
        if isinstance(value, list):
            return [self.deref(v) for v in value]
        if not isinstance(value, dict):
            return []
        objects = self.deref(value.get("NS.objects"))
        if isinstance(objects, list):
            return [self.deref(v) for v in objects]
        indexed = _indexed(value, "NS.object.")
        n = 0
        while n in indexed:
            n += 1
        return [self.deref(indexed[i]) for i in range(n)]

    def mapping(self, value) -> dict:
        """An NSDictionary as a dict of strings to dereferenced values:
        ``NS.keys`` / ``NS.objects``, or ``NS.key.N`` / ``NS.object.N``
        pairs; {} for anything else. A key that is no string or integer is
        left out. Made once per object (``_once``)."""
        return self._once("mapping", self.deref(value), self._mapping)

    def _mapping(self, value) -> dict:
        if not isinstance(value, dict):
            return {}
        out = {}
        keys, objects = self.deref(value.get("NS.keys")), self.deref(value.get("NS.objects"))
        if isinstance(keys, list) and isinstance(objects, list):
            pairs = zip(keys, objects)
        else:
            values = _indexed(value, "NS.object.")
            pairs = ((k, values.get(i)) for i, k in sorted(_indexed(value, "NS.key.").items()))
        for k, v in pairs:
            k = self.deref(k)
            if isinstance(k, int) and not isinstance(k, bool):
                k = str(k)
            else:
                k = self.string(k)
            if k is not None:
                out[k] = self.deref(v)
        return out

    def string(self, value) -> str | None:
        """A string in any of its three forms (a str, ``{NS.bytes: utf-8}``,
        ``{NS.string}``), NFC-normalized; None for anything else. Decoded
        and normalized once per object (``_once``)."""
        return self._once("string", self.deref(value), self._string)

    def _string(self, value) -> str | None:
        if isinstance(value, dict):
            if "NS.string" in value:
                value = self.deref(value["NS.string"])
            elif "NS.bytes" in value:
                raw = self.deref(value["NS.bytes"])
                value = raw.decode("utf-8", "replace") if isinstance(raw, (bytes, bytearray)) else None
        return unicodedata.normalize("NFC", value) if isinstance(value, str) else None

    def number(self, value) -> float | None:
        """``finite`` of the value, dereferenced."""
        return finite(self.deref(value))

    def integer(self, value) -> int | None:
        """``whole`` of the value, dereferenced."""
        return whole(self.deref(value))

    def flag(self, value) -> bool:
        """True only for True or a non-zero integer."""
        value = self.deref(value)
        return value is True or (isinstance(value, int) and not isinstance(value, bool) and value != 0)

    def data(self, value) -> bytes | None:
        """Bytes, or ``{NS.data: bytes}``; None otherwise."""
        value = self.deref(value)
        if isinstance(value, dict) and "NS.data" in value:
            value = self.deref(value["NS.data"])
        return bytes(value) if isinstance(value, (bytes, bytearray)) else None

    def date(self, value) -> datetime.datetime | None:
        """An NSDate (``{NS.time}``, seconds since 2001-01-01 UTC) as an
        aware UTC datetime; None when there is none or it is out of range."""
        value = self.deref(value)
        if isinstance(value, datetime.datetime):
            return utc(value)
        seconds = self.number(value.get("NS.time")) if isinstance(value, dict) else None
        if seconds is None:
            return None
        try:
            return datetime.datetime.fromtimestamp(APPLE_EPOCH + seconds, datetime.UTC)
        except (OverflowError, OSError, ValueError):
            return None

    def color(self, value) -> tuple[float, float, float, float] | None:
        """A colour, (r, g, b, a) clamped to 0..1: a UIColor's UIRed,
        UIGreen, UIBlue and UIAlpha (the ``-Double`` keys win when present),
        a greyscale UIColor's UIWhite and UIAlpha, or a Mac 4.x NSColor's
        NSRGB "r g b [a]" or NSWhite "w [a]" (ASCII, NUL-terminated). None
        when no colour reads."""
        value = self.deref(value)
        if not isinstance(value, dict):
            return None

        def component(key):
            v = self.number(value.get(f"{key}-Double"))
            return v if v is not None else self.number(value.get(key))

        alpha = component("UIAlpha")
        alpha = 1.0 if alpha is None else alpha
        rgb = [component(k) for k in ("UIRed", "UIGreen", "UIBlue")]
        if None not in rgb:
            return tuple(_unit(v) for v in (*rgb, alpha))
        white = component("UIWhite")
        if white is not None:
            return (_unit(white),) * 3 + (_unit(alpha),)
        for key, n in (("NSRGB", 3), ("NSWhite", 1)):
            raw = self.data(value.get(key))
            if raw is None or len(raw) > _MAX_GEOMETRY:
                continue
            try:
                parts = [float(p) for p in raw.split(b"\0", 1)[0].decode("ascii").split()]
            except (UnicodeDecodeError, ValueError):
                continue
            if len(parts) not in (n, n + 1) or not all(math.isfinite(p) for p in parts):
                continue
            rgb = parts[:n] * (3 // n)
            return tuple(_unit(v) for v in (*rgb, parts[n] if len(parts) > n else 1.0))
        return None


def utc(value: datetime.datetime) -> datetime.datetime:
    """A plist's date as an aware UTC datetime: plistlib reads one naive,
    in UTC."""
    return value if value.tzinfo else value.replace(tzinfo=datetime.UTC)


def _indexed(obj: dict, prefix: str) -> dict[int, object]:
    """``{N: value}`` of the keys ``<prefix>N`` of an indexed container."""
    out = {}
    for key, value in obj.items():
        if isinstance(key, str) and key.startswith(prefix):
            tail = key[len(prefix):]
            if tail.isascii() and tail.isdigit() and len(tail) <= _MAX_INDEX_DIGITS:
                out[int(tail)] = value
    return out
