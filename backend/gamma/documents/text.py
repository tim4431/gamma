"""A document's text, by its format (docs/dev/documents.md "Text
extraction"): the one reader the search index reads every document through
(``extract``: a PDF's pages, a text file's runs of lines, both normalized as
the index stores them), and what the chat reads a text file with
(``read_lines``, a window streamed from the file; ``outline``, where the rest
of it is; ``numbered``, the lines as the model sees them). ``locate`` finds
the line of a run a search hit matched, by the token rule FTS5 matched it
with.

The bytes are decoded one way here and in the viewer
(frontend/src/documents/viewers/textLoad.js): a byte-order mark picks UTF-8,
UTF-16LE or UTF-16BE, anything else is UTF-8 with replacement, and a NUL or
too many replacements say the file is no text (``decode``;
tests/shared/textdecode.json holds both sides to it). A file whose name
``is_secret`` keeps its text from the model: never indexed, never read into
a chat. The chat's PDF reading stays pdf_text's (``pdf_excerpt``: page
labels and a cache a library walk must not share).
"""

import codecs
import json
import re
import unicodedata
from dataclasses import dataclass
from functools import lru_cache

from ..textnorm import normalize_text

PAGE_CHARS = 20_000         # a PDF page's indexed text is cut here
RUN_LINES = 40              # lines per index unit of a text file: unit k is lines (k-1)*RUN_LINES+1 .. k*RUN_LINES
LINE_CHARS = 500            # a line is cut here for the index (a minified file is one long line); RUN_LINES *
                            # LINE_CHARS == PAGE_CHARS, so a run and a page are units of one size and bm25 ranks them together
VIEW_LINE_CHARS = 10_000    # a line is cut here for the chat and read_page (the viewer's LINE_CAP)
MAX_TEXT_LINES = 50_000     # a file is indexed, outlined and counted up to this many lines ...
MAX_TEXT_CHARS = 2_000_000  # ... or this many decoded characters, whichever comes first; the rest is ``truncated``
BAD_SHARE = 0.05            # more U+FFFD than this share of the characters: not text
INDEXED_FORMATS = ("pdf", "text")
SECRET_NAMES = (".env", ".netrc", ".npmrc", ".pypirc", ".htpasswd", "id_rsa", "id_dsa", "id_ecdsa", "id_ed25519")
SECRET_SUFFIXES = (".pem", ".key", ".p12", ".pfx")

LINE_CUT = "…[line cut]"
NOT_TEXT = "not a text file"
OUTLINE_MIN_LINES = 40      # a file shorter than this has no outline
OUTLINE_TEXT = 80           # an outline entry's text is cut here

_READ_CHUNK = 64 * 1024     # what read_lines decodes at a time
_COUNT_CHUNK = 1024 * 1024  # what it counts the rest of a file in
_BOMS = ((codecs.BOM_UTF8, "utf-8-sig", 0), (codecs.BOM_UTF16_LE, "utf-16-le", 2),
         (codecs.BOM_UTF16_BE, "utf-16-be", 2))


@dataclass(frozen=True)
class Unit:
    """One index row: ``no`` a PDF page or a run's ordinal (1-based);
    ``text`` normalized, a page's cut at PAGE_CHARS, a run's lines each cut
    at LINE_CHARS and joined with ``\\n``, empty lines kept, so its i-th line
    is line ``unit_lines(no, …)[0] + i``."""
    no: int
    text: str


@dataclass(frozen=True)
class Extracted:
    """What ``extract`` read: the units, the lines indexed (a text file's,
    after the cut), the characters of the units, whether the file went on
    past MAX_TEXT_LINES / MAX_TEXT_CHARS, the encoding and whether some
    bytes were no text in it."""
    format: str
    units: list
    lines: int
    chars: int
    truncated: bool
    encoding: str
    bad: bool


@dataclass(frozen=True)
class Decoded:
    """Bytes as text. ``encoding`` is ``utf-8``, ``utf-8-sig``,
    ``utf-16-le`` or ``utf-16-be``; ``bad`` some bytes were not the
    encoding's and became U+FFFD; ``binary`` a NUL anywhere, or U+FFFD past
    BAD_SHARE of the characters: no text (``text`` is then "" and ``bad``
    false)."""
    text: str
    encoding: str
    bad: bool
    binary: bool


@dataclass(frozen=True)
class Window:
    """Lines ``from_line`` .. ``last`` of a file, raw (not normalized),
    each cut at VIEW_LINE_CHARS with LINE_CUT; ``last`` is ``from_line - 1``
    for none; ``total`` the file's line count (a last line without a
    newline counts); ``decoded`` the encoding and ``bad`` of what was read
    (its ``text`` is "": the window is ``lines``)."""
    lines: list
    last: int
    total: int
    decoded: Decoded


# --- decoding ------------------------------------------------------------------------

def _encoding(head: bytes) -> tuple[str, int]:
    """The codec the byte-order mark at the start of ``head`` names and the
    bytes to skip for it (``utf-8-sig`` drops its own); ``utf-8`` for none."""
    for bom, encoding, skip in _BOMS:
        if head.startswith(bom):
            return encoding, skip
    return "utf-8", 0


def _decode(data: bytes, final: bool = True) -> Decoded:
    encoding, skip = _encoding(data[:4])
    body = data[skip:]
    text = codecs.getincrementaldecoder(encoding)(errors="replace").decode(body, final=final)
    bad = False
    if "\ufffd" in text:
        try:
            codecs.getincrementaldecoder(encoding)().decode(body, final=final)
        except UnicodeDecodeError:
            bad = True
    if "\x00" in text or text.count("\ufffd") > BAD_SHARE * len(text):
        return Decoded("", encoding, False, True)
    return Decoded(text, encoding, bad, False)


def decode(data: bytes) -> Decoded:
    """``data`` decoded by its byte-order mark, else as UTF-8 with
    replacement; no code-page guess. The viewer's ``decodeText`` applies the
    same rule (tests/shared/textdecode.json)."""
    return _decode(bytes(data))


def is_secret(name: str) -> bool:
    """Whether a file named ``name`` keeps its text from the model: its leaf,
    lowercased, is one of SECRET_NAMES, starts with ``.env.`` or ends with
    one of SECRET_SUFFIXES. The viewer still shows it."""
    leaf = str(name or "").replace("\\", "/").rsplit("/", 1)[-1].strip().lower()
    return leaf in SECRET_NAMES or leaf.startswith(".env.") or leaf.endswith(SECRET_SUFFIXES)


def _split(text: str) -> list[str]:
    """``text``'s lines, ``\\r\\n`` and ``\\r`` read as ``\\n``; a final line
    break ends the last line and starts none."""
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    if lines[-1] == "":
        lines.pop()
    return lines


def _normalized(lines: list[str]) -> list[str]:
    """``normalize_text`` of each line, cut at LINE_CHARS, in one call: the
    lines joined by NUL (no decoded text holds one, it is no whitespace and
    breaks no rule of normalize_text across it) and split again."""
    joined = normalize_text("\x00".join(lines))
    parts = joined.split("\x00") if lines else []
    return [p.strip()[:LINE_CHARS] for p in parts]


# --- the index's reader --------------------------------------------------------------

def extract(path, format: str, *, name: str = "") -> Extracted:
    """The units the index stores for the document at ``path``. A PDF: its
    pages through pdf_text (past its cache), empty ones dropped; ImportError
    and any other failure raise, for the indexer to decide what to record. A
    text file: ``name``'s secret files and binary bytes give no units; else
    its lines up to MAX_TEXT_LINES / MAX_TEXT_CHARS (at most MAX_TEXT_CHARS *
    4 + 3 bytes are read), RUN_LINES to a unit, a run of empty lines dropped
    but numbered. ValueError for any other format (callers test
    INDEXED_FORMATS)."""
    if format == "pdf":
        from .. import pdf_text  # local: a server without PyMuPDF still reads text files

        units = []
        for no, raw in enumerate(pdf_text.extract_pages(str(path), cache=False), start=1):
            text = normalize_text(raw)[:PAGE_CHARS]
            if text:
                units.append(Unit(no, text))
        return Extracted("pdf", units, 0, sum(len(u.text) for u in units), False, "", False)
    if format != "text":
        raise ValueError(f"no text for a {format} document")
    if is_secret(name):
        return Extracted("text", [], 0, 0, False, "", False)
    limit = MAX_TEXT_CHARS * 4 + 3
    with open(path, "rb") as f:
        data = f.read(limit + 1)
    truncated = len(data) > limit
    decoded = _decode(data[:limit], final=not truncated)
    if decoded.binary:
        return Extracted("text", [], 0, 0, False, decoded.encoding, decoded.bad)
    text = decoded.text.replace("\r\n", "\n").replace("\r", "\n")
    if len(text) > MAX_TEXT_CHARS:
        text, truncated = text[:MAX_TEXT_CHARS], True
    if truncated and "\n" in text:
        text = text[:text.rfind("\n") + 1]  # the line the cut went through is left out
    lines = _split(text)
    if len(lines) > MAX_TEXT_LINES:
        lines, truncated = lines[:MAX_TEXT_LINES], True
    units = []
    for start in range(0, len(lines), RUN_LINES):
        run = _normalized(lines[start:start + RUN_LINES])
        if any(run):
            units.append(Unit(start // RUN_LINES + 1, "\n".join(run)))
    return Extracted("text", units, len(lines), sum(len(u.text) for u in units), truncated,
                     decoded.encoding, decoded.bad)


def unit_lines(unit: int, lines: int) -> tuple[int, int]:
    """The first and last line of the run ``unit`` in a file of ``lines``
    lines indexed."""
    return (unit - 1) * RUN_LINES + 1, min(unit * RUN_LINES, lines)


# --- the chat's reader ---------------------------------------------------------------

def _cut_line(line: str) -> str:
    return line[:VIEW_LINE_CHARS] + LINE_CUT if len(line) > VIEW_LINE_CHARS else line


def _count_rest(f, encoding: str, after_cr: bool) -> tuple[int, bytes]:
    """The line breaks in the rest of ``f``, counted in its bytes without
    decoding them, and the last code unit read (b"" for none): a chunk's
    ``\\n`` and ``\\r``, a ``\\r\\n`` once (counts into one buffer, so a 64
    MB log counts in ~40 ms). ``after_cr``: the text before ended on a
    ``\\r``, so a ``\\n`` first is its pair, as across two chunks."""
    base = "utf-8" if encoding.startswith("utf-8") else encoding
    lf, cr = "\n".encode(base), "\r".encode(base)
    breaks, last, buf = 0, b"", bytearray(_COUNT_CHUNK)
    while True:
        got = f.readinto(buf)
        if not got:
            return breaks, last
        raw = buf if got == len(buf) else buf[:got]
        found = raw.count(lf)
        if cr in raw:
            found += raw.count(cr) - raw.count(cr + lf)
        if after_cr and raw.startswith(lf):
            found -= 1
        after_cr = raw.endswith(cr)
        breaks += found
        last = bytes(raw[-len(lf):])


def read_lines(path, from_line: int = 1, to_line: int | None = None, *, budget: int | None = None) -> Window:
    """Lines ``from_line`` .. ``to_line`` (inclusive, 1-based) of the text
    file at ``path``, streamed: decoded 64 KiB at a time by the byte-order
    mark's rule (``decode``), and kept until ``to_line`` or until their
    characters (a line break each) reach ``budget``, the line that reaches it
    kept whole; then the rest of the file is counted from its bytes, so a
    64 MB log read for its first lines reads a few hundred KB and counts the
    rest. ``from_line`` below 1 reads from 1; past the end gives no lines and
    the right ``total``. ValueError ``NOT_TEXT`` for a NUL in what was
    decoded, or U+FFFD past BAD_SHARE of it."""
    from_line = max(1, int(from_line or 1))
    lines: list[str] = []
    seen = kept = chars = replaced = 0
    bad, stopped, after_cr, tail = False, to_line is not None and to_line < from_line, False, False
    with open(path, "rb") as f:
        encoding, skip = _encoding(f.read(4))
        f.seek(skip)
        decoder = codecs.getincrementaldecoder(encoding)(errors="replace")
        strict = codecs.getincrementaldecoder(encoding)()
        buf = ""
        while not stopped:
            raw = f.read(_READ_CHUNK)
            text = decoder.decode(raw, final=not raw)
            if "\x00" in text:
                raise ValueError(NOT_TEXT)
            if not bad:
                try:
                    strict.decode(raw, final=not raw)
                except UnicodeDecodeError:
                    bad = True
            chars += len(text)
            replaced += text.count("\ufffd")
            buf += text
            held = bool(raw) and buf.endswith("\r")  # its \n may open the next chunk
            if held:
                buf = buf[:-1]
            parts = buf.replace("\r\n", "\n").replace("\r", "\n").split("\n")
            buf = parts.pop()
            if not raw and buf:
                parts.append(buf)
                buf = ""
            for i, line in enumerate(parts):
                seen += 1
                if seen < from_line:
                    continue
                line = _cut_line(line)
                lines.append(line)
                kept += len(line) + 1
                if (to_line is not None and seen >= to_line) or (budget is not None and kept >= budget):
                    seen += len(parts) - i - 1
                    stopped = True
                    break
            if stopped:
                if held:
                    seen, buf, after_cr = seen + 1, "", True
                tail = bool(buf) or bool(decoder.getstate()[0])
                break
            if not raw:
                break
            if len(buf) > VIEW_LINE_CHARS + 1:
                buf = buf[:VIEW_LINE_CHARS + 1]  # enough to cut it; the rest of the line is never shown
            if held:
                buf += "\r"
        if replaced > BAD_SHARE * chars:
            raise ValueError(NOT_TEXT)
        total = seen
        if stopped:
            breaks, last = _count_rest(f, encoding, after_cr)
            base = "utf-8" if encoding.startswith("utf-8") else encoding
            if last:
                tail = last not in ("\n".encode(base), "\r".encode(base))
            total += breaks + (1 if tail else 0)
    return Window(lines, from_line + len(lines) - 1, total, Decoded("", encoding, bad, False))


def numbered(lines: list[str], from_line: int) -> str:
    """The lines with their numbers, as the chat sends a file: ``cat -n``'s
    shape, the number right-aligned in six columns and a tab."""
    return "\n".join(f"{n:>6}\t{line}" for n, line in enumerate(lines, start=from_line))


# --- the outline ---------------------------------------------------------------------

_TEX_HEADING = re.compile(r"[ \t]*\\(chapter|section|subsection|subsubsection|paragraph)\*?[ \t]*(?:\[[^\]]*\][ \t]*)?\{")
_TEX_LABEL = re.compile(r"\\label\{([^{}]*)\}")
_TEX_BEGIN = re.compile(r"[ \t]*\\begin\{(abstract|figure\*?|table\*?)\}")
_TEX_CAPTION = re.compile(r"\\caption[ \t]*(?:\[[^\]]*\][ \t]*)?\{")
_TEX_FILE = re.compile(r"[ \t]*\\(input|include|bibliography)[ \t]*\{([^{}]*)\}")
_MD_HEADING = re.compile(r" {0,3}#{1,6}[ \t]+\S")
_MD_FENCE = re.compile(r" {0,3}(?:```|~~~)")
_UNDERLINE = re.compile(r"(?P<c>[=\-~^\"'`#*+])(?P=c)+[ \t]*$")
_TYPST_HEADING = re.compile(r"[ \t]*=+[ \t]+\S")
_PY_DEF = re.compile(r"([ \t]*)(?:async[ \t]+)?(?:def|class)[ \t]+\w")
_PY_MAIN = re.compile(r"if[ \t]+__name__[ \t]*==")
_JS_DECL = re.compile(r"[ \t]*(?:export[ \t]+(?:default[ \t]+)?)?(?:async[ \t]+|abstract[ \t]+)?(?:function\b|class\b)")
_JS_EXPORT = re.compile(r"export\b")
_JS_VALUE = re.compile(r"(?:const|let|var)[ \t]+[\w$]+[ \t]*(?::[^=]*)?=[ \t]*(?:async\b|function\b|\(|[\w$]+[ \t]*=>|\{)")
_JS_TEST = re.compile(r"[ \t]*(?:describe|test|it)(?:\.\w+)?[ \t]*\(")
_C_DECL = re.compile(r"[ \t]*(?:(?:pub(?:\([\w:]+\))?|export|public|private|protected|internal|static|final|abstract"
                     r"|async|unsafe|extern|data|sealed|open|override|inline|const|typedef)[ \t]+)*"
                     r"(?:fn|func|fun|class|struct|impl|enum|interface|trait|object|protocol|union|namespace)\b")
_C_IMPORT = re.compile(r"[ \t]*(?:#[ \t]*include\b|use[ \t]|import\b)")
_C_NOT_DEF = re.compile(r"(?:if|for|while|switch|else|do|return|catch|try|case|sizeof)\b")
_JSON_TOKEN = re.compile(r'"(?:[^"\\]|\\.)*"?|[{}\[\],:]')  # an unclosed string runs to the line's end
_KEY = r"""(?:[\w.\-/]+|"[^"]*"|'[^']*')[ \t]*"""
_KEY_LINES = {"yaml": re.compile(_KEY + r":(?:[ \t]|$)"), "toml": re.compile(_KEY + "="),
              "ini": re.compile(_KEY + r"(?:=|:(?:[ \t]|$))")}
_SECTION_LINE = re.compile(r"[ \t]*\[\[?[^\[\]]+\]\]?[ \t]*(?:#.*)?$")
_BIB_ENTRY = re.compile(r"[ \t]*@([A-Za-z]+)[ \t]*[{(][ \t]*([^,\s]*)")
_BIB_TITLE = re.compile(r"[ \t]*title[ \t]*=[ \t]*[{\"]", re.IGNORECASE)


class _Whole(tuple):
    """An outline entry a rule says of the whole file (a table's rows, an
    array's items): shown whatever ``from_line``."""


def _braced(line: str, start: int) -> str:
    """The text of the brace group opened just before ``start``, to its
    closing brace or the end of the line."""
    depth = 1
    for i in range(start, len(line)):
        if line[i] == "{":
            depth += 1
        elif line[i] == "}":
            depth -= 1
            if depth == 0:
                return line[start:i]
    return line[start:]


def _latex(lines: list[str]) -> list[tuple[int, str]]:
    out = []
    for i, line in enumerate(lines):
        m = _TEX_HEADING.match(line)
        if m:
            text = f"\\{m.group(1)}{{{_braced(line, m.end())}}}"
            label = _TEX_LABEL.search(line, m.end()) or (_TEX_LABEL.search(lines[i + 1]) if i + 1 < len(lines) else None)
            out.append((i + 1, f"{text} [{label.group(1)}]" if label else text))
            continue
        m = _TEX_BEGIN.match(line)
        if m:
            env, text = m.group(1), f"\\begin{{{m.group(1)}}}"
            if env != "abstract":
                for later in lines[i:i + 12]:
                    c = _TEX_CAPTION.search(later)
                    if c:
                        text += " … \\caption{"
                        room = min(60, OUTLINE_TEXT - len(text) - 1)
                        caption = " ".join(_braced(later, c.end()).split())
                        text += (caption if len(caption) <= room else caption[:room - 1] + "…") + "}"
                        break
                    if f"\\end{{{env}}}" in later:
                        break
            out.append((i + 1, text))
            continue
        m = _TEX_FILE.match(line)
        if m:
            out.append((i + 1, f"\\{m.group(1)}{{{m.group(2)}}}"))
    return out


def _headings(lines: list[str], language: str) -> list[tuple[int, str]]:
    out, fenced = [], False
    for i, line in enumerate(lines):
        if language == "markdown" and _MD_FENCE.match(line):
            fenced = not fenced
            continue
        if fenced:
            continue
        if language == "typst":
            if _TYPST_HEADING.match(line):
                out.append((i + 1, line.strip()))
            continue
        if language == "markdown" and _MD_HEADING.match(line):
            out.append((i + 1, line.strip()))
        elif i and _UNDERLINE.match(line) and lines[i - 1].strip() and not _UNDERLINE.match(lines[i - 1]):
            out.append((i, lines[i - 1].strip()))
    return out


def _python(lines: list[str]) -> list[tuple[int, str]]:
    out, stack = [], []
    for i, line in enumerate(lines):
        m = _PY_DEF.match(line)
        if m:
            indent = len(m.group(1).expandtabs(4))
            while stack and stack[-1] >= indent:
                stack.pop()
            out.append((i + 1, "  " * len(stack) + line.strip()))
            stack.append(indent)
        elif _PY_MAIN.match(line):
            out.append((i + 1, line.strip()))
    return out


def _javascript(lines: list[str]) -> list[tuple[int, str]]:
    return [(i + 1, line.strip()) for i, line in enumerate(lines)
            if _JS_DECL.match(line) or _JS_EXPORT.match(line) or _JS_VALUE.match(line) or _JS_TEST.match(line)]


def _defines(line: str) -> bool:
    """Whether ``line`` reads ``name(…) {`` at indentation 0-1: a function's
    definition in a C-like language. Plain tests before any regex, so a long
    line costs one pass."""
    body = line.lstrip(" \t")
    if len(line) - len(body) > 1 or not body.rstrip().endswith("{") or "(" not in body or ";" in body:
        return False
    if body[0] in "}#/*" or _C_NOT_DEF.match(body):
        return False
    head = body[:body.index("(")].rstrip()
    return bool(head) and (head[-1].isalnum() or head[-1] in "_~")


def _c_like(lines: list[str]) -> list[tuple[int, str]]:
    out, imports, first_import = [], 0, 0
    for i, line in enumerate(lines):
        if _C_IMPORT.match(line):
            imports += 1
            first_import = first_import or i + 1
        elif _C_DECL.match(line) or _defines(line):
            out.append((i + 1, line.strip()))
    if imports:
        out.append((first_import, f"{imports} imports"))
    return sorted(out)


def _json(lines: list[str]) -> list[tuple[int, str]]:
    """An object's top-level keys; an array's length and its first item's
    keys; a notebook's cells by type and first source line. The keys are
    found by a scan of strings and brackets, the counts by parsing."""
    stack, keys, cells = [], [], []
    last_string, items = None, 0
    for i, line in enumerate(lines):
        for m in _JSON_TOKEN.finditer(line):
            tok = m.group()
            if tok in "{[":
                stack.append(tok)
            elif tok in "}]":
                if stack:
                    stack.pop()
            elif tok == ",":
                if len(stack) == 1 and stack[0] == "[":
                    items += 1
            elif tok == ":":
                depth = len(stack)
                if last_string is not None and stack and stack[-1] == "{":
                    on = last_string[0] + 1
                    if (depth == 1 or (depth == 2 and stack[0] == "[" and items == 0)) and (not keys or keys[-1][0] != on):
                        keys.append((on, lines[last_string[0]].strip()))
                    if depth == 3 and last_string[1] == '"cell_type"':
                        cells.append(last_string[0] + 1)
            else:
                last_string = (i, tok)
                continue
            last_string = None
    try:
        data = json.loads("\n".join(lines))
    except ValueError:
        data = None
    if isinstance(data, dict) and isinstance(data.get("cells"), list):
        out = []
        for line_no, cell in zip(cells, data["cells"]):
            source = cell.get("source", "") if isinstance(cell, dict) else ""
            source = "".join(source) if isinstance(source, list) else str(source)
            first = next((s.strip() for s in source.split("\n") if s.strip()), "")
            out.append((line_no, f"[{cell.get('cell_type', '?')}] {first}"))
        return out
    if isinstance(data, list):
        return [_Whole((1, f"[{len(data):,} items]"))] + keys
    return keys


def _keyed(lines: list[str], language: str) -> list[tuple[int, str]]:
    """YAML's top-level keys; TOML's and INI's tables or sections, and their
    keys before the first one."""
    out, in_table, key = [], False, _KEY_LINES[language]
    for i, line in enumerate(lines):
        if not line or line[0] in " \t#;-":
            continue
        if language != "yaml" and _SECTION_LINE.match(line):
            in_table = True
            out.append((i + 1, line.strip()))
        elif not in_table and key.match(line):
            out.append((i + 1, line.strip()))
    return out


def _bibtex(lines: list[str]) -> list[tuple[int, str]]:
    out = []
    for i, line in enumerate(lines):
        m = _BIB_ENTRY.match(line)
        if not m or m.group(1).lower() in ("comment", "string", "preamble"):
            continue
        title = ""
        for j in range(i, min(i + 40, len(lines))):
            later = lines[j]
            if j > i and later.lstrip().startswith("@"):
                break
            t = _BIB_TITLE.match(later)
            if t:
                title = " ".join(later[t.end():].replace("{", "").replace("}", "").strip().rstrip(",\"").split())
                break
        out.append((i + 1, f"@{m.group(1)}{{{m.group(2).rstrip(',')}}} {title[:60]}".rstrip()))
    return out


def _table(lines: list[str]) -> list[tuple[int, str]]:
    rows = sum(1 for line in lines[1:] if line.strip())
    return [(1, lines[0].strip()), _Whole((2, f"{rows:,} rows"))] if lines else []


def _paragraphs(lines: list[str]) -> list[tuple[int, str]]:
    """The first line after each blank line (and the file's first); for a
    file with fewer than three such starts, every non-blank line, for the
    budget to sample."""
    starts, blank = [], True
    for i, line in enumerate(lines):
        text = line.strip()
        if text and blank:
            starts.append((i + 1, text))
        blank = not text
    if len(starts) < 3:
        return [(i + 1, line.strip()) for i, line in enumerate(lines) if line.strip()]
    return starts


_RULES = {
    "latex": lambda lines, _: _latex(lines),
    **{lang: _headings for lang in ("markdown", "rst", "typst")},
    "python": lambda lines, _: _python(lines),
    **{lang: (lambda lines, _: _javascript(lines)) for lang in ("javascript", "typescript", "jsx", "tsx")},
    **{lang: (lambda lines, _: _c_like(lines))
       for lang in ("c", "cpp", "rust", "go", "java", "kotlin", "swift", "scala")},
    "json": lambda lines, _: _json(lines),
    **{lang: _keyed for lang in ("yaml", "toml", "ini")},
    "bibtex": lambda lines, _: _bibtex(lines),
    **{lang: (lambda lines, _: _table(lines)) for lang in ("csv", "tsv")},
}


def _fit(entries: list[tuple[int, str]], budget: int) -> list[tuple[int, str]]:
    """Every k-th entry, k as small as fits ``budget`` (each entry costs its
    text and 10), the end cut if the sample still does not fit."""
    cost = sum(len(t) + 10 for _, t in entries)
    if cost > budget > 0:
        entries = entries[::-(-cost // budget)]
    out, used = [], 0
    for line, text in entries:
        used += len(text) + 10
        if used > budget:
            break
        out.append((line, text))
    return out


def outline_stop(line: int) -> str:
    """The entry that ends an outline of a file read only to ``line``."""
    return f"(the outline stops at line {line:,})"


def outline(lines: list[str], language: str | None, *, from_line: int = 1, budget: int = 2400,
            cut: bool = False) -> list[tuple[int, str]]:
    """Where things are in a text file, as ``[(line, text)]`` from
    ``from_line`` on, each text at most OUTLINE_TEXT characters, all within
    ``budget`` (an entry costs its text and 10): by ``language``'s rule (a
    LaTeX file's sections, figures and inputs; a program's definitions; a
    data file's keys …; what a rule says of the whole file, a table's rows
    or an array's items, whatever ``from_line``), the paragraph
    starts filling in when it finds fewer than three, in the budget its own
    entries leave (they are never sampled away for the fill); ``[]`` for a
    file under OUTLINE_MIN_LINES. ``cut``: ``lines`` are only the file's
    first lines (MAX_TEXT_LINES or MAX_TEXT_CHARS stopped the read), and
    the outline ends with ``outline_stop`` at the last of them. The rules'
    regexes are anchored and linear: a crafted file does not hold the
    thread."""
    if len(lines) < OUTLINE_MIN_LINES:
        return []
    rule = _RULES.get(language or "")
    found = rule(lines, language) if rule else []
    fill = []
    if len(found) < 3:
        own = {n for n, _ in found}
        fill = [(n, t) for n, t in _paragraphs(lines) if n not in own]

    def shown(entries, whole=False):
        return [(n, (" " * (len(t) - len(t.lstrip(" "))) + " ".join(t.split()))[:OUTLINE_TEXT])
                for n, t in entries if (whole or n >= from_line) and t.strip()]

    stop = outline_stop(len(lines))
    room = budget - (len(stop) + 10 if cut else 0)
    about = _fit(shown([e for e in found if isinstance(e, _Whole)], whole=True), room)
    room -= sum(len(t) + 10 for _, t in about)
    out = _fit(shown([e for e in found if not isinstance(e, _Whole)]), room)
    left = room - sum(len(t) + 10 for _, t in out)
    if fill and left > 0:
        out = sorted(out + _fit(shown(fill), left))
    out = sorted(about + out)
    return out + [(len(lines), stop)] if cut else out


# --- a hit's line --------------------------------------------------------------------

@lru_cache(maxsize=8192)
def _fold_char(c: str) -> str:
    decomposed = unicodedata.normalize("NFD", c)
    if len(decomposed) > 1 and all(unicodedata.combining(x) for x in decomposed[1:]):
        c = decomposed[0]
    low = c.lower()
    return low if len(low) == 1 else c


def _fold(s: str) -> str:
    """``s`` lowercased with its diacritics dropped, as unicode61 reads a
    token, one character for one so a match's place is the text's."""
    return s.lower() if s.isascii() else "".join(map(_fold_char, s))


def _around(line: str, at: int, width: int = 200) -> str:
    if len(line) <= width:
        return line
    start = max(0, min(at - width // 3, len(line) - width))
    return ("…" if start else "") + line[start:start + width] + ("…" if start + width < len(line) else "")


def locate(unit_text: str, query: str, first_line: int) -> tuple[int, str]:
    """The line of a run (``unit_text``, whose first line is ``first_line``)
    that a search for ``query`` matched, and its text cut to 200 characters
    around the match. FTS5's token model, the one the hit was found with:
    the normalized query's runs of letters and digits (an underscore splits
    them, as unicode61 splits ``load_config``), each a whole word on the
    line, the last a prefix (``fts_query``'s trailing ``*``), case and
    diacritics aside; the line holding the most of them wins, the earliest
    of equals; none on any line (the phrase crossed a line break, or a cut
    line) gives the run's first non-empty line."""
    tokens = list(dict.fromkeys(re.findall(r"[^\W_]+", _fold(normalize_text(query)))))
    last = tokens[-1] if tokens else ""
    patterns = [re.compile(r"(?<![^\W_])" + re.escape(t) + ("" if t == last else r"(?![^\W_])")) for t in tokens]
    lines = unit_text.split("\n")
    best, best_score, best_at = None, 0, 0
    for i, line in enumerate(lines):
        folded = _fold(line)
        found = [m.start() for m in (p.search(folded) for p in patterns) if m]
        if len(found) > best_score:
            best, best_score, best_at = i, len(found), min(found)
    if best is None:
        best = next((i for i, line in enumerate(lines) if line.strip()), 0)
    return first_line + best, _around(lines[best], best_at)
