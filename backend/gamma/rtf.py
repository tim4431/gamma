"""A small reader of the RTF that Cocoa's NSAttributedString writes, the
text of a GoodNotes legacy text box (gamma/goodnotes.py): its text as
spans, each with its bold, italic, underline, strikethrough, size and
colour. The font table and the colour table are read for the spans (a
font named ``…-Bold`` or ``…-Italic`` is bold or italic); every other
destination (``\\*`` groups, the stylesheet, a picture, a field's
instruction) is passed by, a field's result kept as text.

The input is untrusted: past MAX_RTF bytes it is not read, groups nest at
most MAX_DEPTH deep (deeper ones are passed by), and the text stops at
MAX_TEXT characters. Nothing here raises for damaged RTF: what does not
read is left out.
"""

import re
from dataclasses import dataclass, replace

MAX_RTF = 4 << 20
MAX_TEXT = 1_000_000
MAX_DEPTH = 64
REPLACEMENT = chr(0xFFFD)        # for a lone surrogate

_TOKEN = re.compile(rb"\\([a-zA-Z]{1,32})(-?\d{1,10})? ?|\\'([0-9a-fA-F]{2})|\\(.)|([{}])|([^\\{}\r\n]+)|[\r\n]+", re.S)
_SKIPPED = frozenset((b"fonttbl", b"colortbl", b"expandedcolortbl", b"stylesheet", b"info", b"listtable",
                      b"listoverridetable", b"pict", b"fldinst", b"themedata", b"colorschememapping",
                      b"latentstyles", b"datastore", b"xmlnstbl", b"header", b"footer", b"footnote"))
_CODEPAGES = {437: "cp437", 850: "cp850", 1250: "cp1250", 1251: "cp1251", 1252: "cp1252", 1253: "cp1253",
              1254: "cp1254", 1255: "cp1255", 1256: "cp1256", 1257: "cp1257", 1258: "cp1258", 10000: "mac_roman",
              932: "cp932", 936: "gbk", 949: "cp949", 950: "cp950"}


@dataclass
class Span:
    text: str
    bold: bool = False
    italic: bool = False
    underline: bool = False
    strike: bool = False
    size: float | None = None   # points, as \fs gives them (half-points halved)
    color: tuple | None = None  # (r, g, b), 0..255; None for the automatic colour
    font: str = ""


@dataclass
class _State:
    bold: bool = False
    italic: bool = False
    underline: bool = False
    strike: bool = False
    size: float | None = None
    color: int = 0
    font: int | None = None
    skip: bool = False          # inside a destination that is not text
    table: str = ""             # "fonttbl" or "colortbl" while one is read
    uc: int = 1                 # characters a \u's fallback takes


def read_rtf(data: bytes) -> list[Span]:
    """The spans of an RTF document, adjacent ones of one style merged;
    ``[]`` for anything that is not RTF or is past MAX_RTF."""
    if not isinstance(data, (bytes, bytearray)) or len(data) > MAX_RTF or not data.lstrip().startswith(b"{\\rtf"):
        return []
    return _Reader(bytes(data)).run()


class _Reader:
    def __init__(self, data: bytes):
        self.data = data
        self.codec = "cp1252"
        self.fonts: dict[int, str] = {}
        self.colors: list[tuple | None] = []
        self.font_entry: list[int] = []  # the font table entry being read: [its index]
        self.color_entry: dict = {}      # the colour table entry being read
        self.spans: list[Span] = []
        self.length = 0
        self.skip_chars = 0           # a \u's fallback characters still to pass by
        self.high: int | None = None  # a \u's high surrogate, waiting for its low one
        self.bytes = bytearray()      # \'hh bytes in a row, decoded together (a double-byte code page's)

    def run(self) -> list[Span]:
        stack, state, deeper = [], _State(), 0  # deeper: groups past MAX_DEPTH, passed by uncopied
        for m in _TOKEN.finditer(self.data):
            word, arg, hexa, symbol, brace, text = m.groups()
            if hexa is not None:
                if self.skip_chars:  # a \u's fallback
                    self.skip_chars -= 1
                elif not deeper:
                    self.bytes.append(int(hexa, 16))
                continue
            self._flush(state)
            if brace == b"{":
                if deeper or len(stack) >= MAX_DEPTH:
                    deeper += 1
                else:
                    stack.append(replace(state))
                continue
            if brace == b"}":
                if deeper:
                    deeper -= 1
                    continue
                if state.table == "fonttbl":
                    self.font_entry = []
                if stack:
                    state = stack.pop()
                continue
            if deeper:
                continue
            if word is not None:
                self._word(state, word, None if arg is None else int(arg))
                continue
            if symbol is not None:
                if symbol == b"*":  # a destination this reader need not know
                    state.skip = True
                elif symbol in (b"\\", b"{", b"}"):
                    self._text(state, symbol.decode())
                elif symbol == b"~":
                    self._text(state, "\u00a0")
                elif symbol in (b"\n", b"\r"):
                    self._text(state, "\n")  # Cocoa's line break inside a paragraph
                elif symbol == b"_":
                    self._text(state, "\u2011")
                continue
            if text is not None:
                self._text(state, text.decode(self.codec, "replace"))
        self._flush(state)
        return self.spans

    def _flush(self, state: _State) -> None:
        """The \'hh bytes read in a row, as text of the document's code page."""
        if self.bytes:
            raw, self.bytes = bytes(self.bytes), bytearray()
            self._text(state, raw.decode(self.codec, "replace"))

    def _word(self, state: _State, word: bytes, arg: int | None) -> None:
        if word in _SKIPPED:
            state.skip = True
            if word in (b"fonttbl", b"colortbl"):
                state.table = word.decode()
                state.skip = False
            return
        if word == b"ansicpg" and arg in _CODEPAGES:
            self.codec = _CODEPAGES[arg]
        elif state.table == "colortbl" and word in (b"red", b"green", b"blue") and arg is not None:
            self.color_entry[word] = max(0, min(255, arg))
        elif state.table == "fonttbl" and word == b"f" and arg is not None:
            self.font_entry = [arg]
        elif word == b"par" or word == b"line":
            self._text(state, "\n")
        elif word == b"tab":
            self._text(state, "\t")
        elif word == b"u" and arg is not None:
            code = (arg + 65536) % 65536
            if 0xD800 <= code < 0xDC00:  # a character past the BMP comes as two \u, its surrogates
                if self.high is not None:
                    self._text(state, REPLACEMENT)
                self.high = code
            elif 0xDC00 <= code < 0xE000:
                high, self.high = self.high, None
                self._text(state, chr(0x10000 + (high - 0xD800 << 10) + code - 0xDC00) if high is not None
                           else REPLACEMENT)
            else:
                self._text(state, chr(code))
            self.skip_chars = state.uc
        elif word == b"uc" and arg is not None:
            state.uc = max(0, min(8, arg))
        elif word == b"plain":
            state.bold = state.italic = state.underline = state.strike = False
            state.size, state.color, state.font = None, 0, None
        elif word == b"b":
            state.bold = arg != 0
        elif word == b"i":
            state.italic = arg != 0
        elif word == b"ul":
            state.underline = arg != 0
        elif word in (b"ulnone", b"ul0"):
            state.underline = False
        elif word == b"strike":
            state.strike = arg != 0
        elif word == b"fs" and arg is not None and arg > 0:
            state.size = arg / 2
        elif word == b"cf" and arg is not None:
            state.color = arg
        elif word == b"f" and arg is not None:
            state.font = arg
        elif word == b"fldrslt":
            state.skip = False

    def _text(self, state: _State, text: str) -> None:
        if self.skip_chars:
            take = min(self.skip_chars, len(text))
            self.skip_chars -= take
            text = text[take:]
        if self.high is not None and text and not 0xDC00 <= ord(text[0]) < 0xE000:
            self.high = None  # a high surrogate no low one follows
            text = REPLACEMENT + text
        if not text:
            return
        if state.table == "colortbl":
            for ch in text:
                if ch == ";":
                    entry = self.color_entry
                    self.colors.append((entry.get(b"red", 0), entry.get(b"green", 0), entry.get(b"blue", 0))
                                       if entry else None)
                    self.color_entry = {}
            return
        if state.table == "fonttbl":
            if self.font_entry:
                name, end, _rest = text.partition(";")
                index = self.font_entry[0]
                self.fonts[index] = (self.fonts.get(index, "") + name).strip()[:256]
                if end:
                    self.font_entry = []
            return
        if state.skip or self.length >= MAX_TEXT:
            return
        text = text[:MAX_TEXT - self.length]
        self.length += len(text)
        font = self.fonts.get(state.font, "") if state.font is not None else ""
        lowered = font.lower()
        color = self.colors[state.color] if 0 < state.color < len(self.colors) else None
        span = Span(text, state.bold or "bold" in lowered, state.italic or "italic" in lowered or "oblique" in lowered,
                    state.underline, state.strike, state.size, color, font)
        last = self.spans[-1] if self.spans else None
        if last is not None and replace(last, text="") == replace(span, text=""):
            last.text += text
        else:
            self.spans.append(span)
