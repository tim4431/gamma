"""Math written as ``\\( … \\)`` and ``\\[ … \\]``, as GPT models and LaTeX
write it, turned into the ``$ … $`` and ``$$ … $$`` notes render.

Applied to text on its way into a note (the AI's ``edit_block`` and
``create_block`` content), never on screen: in Markdown ``\\[`` is also an
escaped bracket, which Gamma writes itself (``text_box.escape_markdown``,
``zotero_notes.escape``: "\\[n = 30\\]"), so a ``\\[ … \\]`` converts only
when it is shaped as a formula is written. frontend/src/editor/pasteMath.js
is the twin of the rules here (a paste); tests/shared/pastemath.json pins
both.
"""

import re

DELIM = re.compile(r"\\[\[\]()]")
ESCAPED = re.compile(r"\\[!-/:-@\[-`{-~]")
COMMAND = re.compile(r"\\[a-zA-Z]")
BLANK_LINE = re.compile(r"\n[ \t]*\n")
OPENS_LINE = re.compile(r"[ \t]*\n")
CLOSES_LINE = re.compile(r"\n[ \t>]*\Z")
# A line that ends a paragraph or starts a block of its own: blank, a list
# item, a heading, a table row.
BLOCK_START = re.compile(r"[ \t>]*(?:\n|\Z|(?:[-+*]|[0-9]+[.)])[ \t]|#{1,6}[ \t]|\|)")
CLOSING_MARK = re.compile(r"[)\]},.;:!?]")
# What a line can start, once its containers are read (frontend
# shared/lib/mdCode.js).
FENCE_OPEN = re.compile(r"(`{3,}|~{3,})([^\n]*)\Z")
FENCE_CLOSE = re.compile(r"(`{3,}|~{3,})[ \t]*\Z")
LIST_MARKER = re.compile(r"(?:[-+*]|[0-9]{1,9}[.)])(?=[ \t]|\Z)")
FOOTNOTE = re.compile(r"\[\^[^\][ \t\r\n]+\]:")
DIVIDER = re.compile(r"([-*_])(?:[ \t]*\1){2,}[ \t]*\Z")
HEADING = re.compile(r"#{1,6}(?:[ \t]|\Z)")
# A quote on the stack of containers; a list item is its width in columns, a
# footnote's definition 4. Past this many on one line the rest of it is text,
# which keeps a line of nothing but markers from costing its length squared.
QUOTE = 0
MAX_CONTAINERS = 32
DOUBLE_DOLLAR = re.compile(r"\$\$")
# All a line may hold before a \[ that stands alone on it.
LINE_PREFIX = re.compile(r"(?:[ \t]*>)*[ \t]*(?:(?:[-+*]|[0-9]+[.)])[ \t]+)?")
# A sign only math uses; Markdown inside brackets, no TeX: a link, an
# address, bold, a ==mark==, a tag.
SIGN = re.compile(r"[\^_={}]")
MARKDOWN = re.compile(r"\]\(|://|\*\*|==|</?[a-zA-Z]")
# Han, kana and Hangul, which set a formula against the text without a space.
CJK = re.compile("[\u1100-\u11ff\u2e80-\u2fdf\u3005-\u3007\u3021-\u3029\u3038-\u303b\u3040-\u30ff"
                 "\u3130-\u318f\u31f0-\u31ff\u3400-\u4dbf\u4e00-\u9fff\ua960-\ua97f\uac00-\ud7ff"
                 "\uf900-\ufaff\uff66-\uffdc\U00020000-\U0003ffff]")
SCHEME = re.compile(r"[a-zA-Z][a-zA-Z0-9+.-]{1,31}:")
BACKTICKS = re.compile(r"`+")
CODE_OR_MATH = re.compile(r"`+|\$\$?")
DOLLARS = re.compile(r"\$\$?")
RUNS = re.compile(r"\$+")
BACKSLASHED_DOLLAR = re.compile(r"(\\*)\$")
TEXT_DOLLAR = "\\text{\\textdollar}"
# JavaScript's whitespace (its \s and trim()), which Python's differs from.
SPACE = "\t\n\v\f\r \u00a0\u1680\u2000\u2001\u2002\u2003\u2004\u2005\u2006\u2007\u2008\u2009\u200a" \
        "\u2028\u2029\u202f\u205f\u3000\ufeff"


def escaped_at(text: str, pos: int) -> bool:
    """Whether an odd run of backslashes escapes the character at ``pos``."""
    n = 0
    while pos > 0 and text[pos - 1] == "\\":
        pos -= 1
        n += 1
    return n % 2 == 1


def _space(c: str) -> bool:
    return c != "" and c in SPACE


def _word(c: str) -> bool:
    """A letter or digit of a script that writes words with spaces."""
    return c != "" and c.isalnum() and not CJK.match(c)


class _Line:
    """A line read from its start: ``col`` is the column at index ``i``,
    tabs going to the next stop of 4, ``at`` the column the innermost
    container's text starts at."""

    def __init__(self, text: str):
        self.text, self.i, self.col, self.at = text, 0, 0, 0

    def char(self) -> str:
        return self.text[self.i] if self.i < len(self.text) else ""

    def indent(self) -> int:
        """The columns of indent from here, moving past it."""
        while self.char() in (" ", "\t"):
            self.col += 4 - self.col % 4 if self.char() == "\t" else 1
            self.i += 1
        return self.col - self.at

    def enter_quote(self):
        """Past a ">" and the one column after it that belongs to the mark."""
        self.i += 1
        self.col += 1
        self.at = self.col
        if self.char() == " ":
            self.i += 1
            self.col += 1
            self.at += 1
        elif self.char() == "\t":
            self.at += 1

    def enter_item(self, indent: int, length: int) -> int:
        """Past a list marker of ``length`` characters, ``indent`` columns
        in. Its text starts behind the spaces after it, or one column on
        where there is none or they make it indented code. Its width."""
        self.i += length
        self.col += length
        i, col = self.i, self.col
        self.at = col
        spaces = self.indent()
        pad = 1 if self.i >= len(self.text) or spaces > 4 else spaces
        if spaces > 4:
            self.i, self.col = i, col
        self.at = col + pad
        return indent + length + pad

    def enter_note(self, length: int):
        """Past a footnote definition's label of ``length`` characters: its
        first line's text starts behind the spaces after it."""
        self.i += length
        self.col += length
        self.at = self.col
        self.indent()
        self.at = self.col


def _fences(text: str) -> list:
    """The code fences in ``text`` as ``(start, inner, mark, end)``, as the
    renderer reads them: the opening line's start and end, where the
    closing line's mark starts and that line's end (where the code ends
    while it is open: the text's end, or the last line its container
    held). The twin of the frontend's shared/lib/mdCode.codeFences."""
    if "```" not in text and "~~~" not in text:
        return []
    out, stack, opened, paragraph, off = [], [], None, False, 0
    for raw in text.split("\n"):
        line = raw[:-1] if raw.endswith("\r") else raw
        r = _Line(line)
        # The containers this line is in; a blank one stays in a list item.
        kept = 0
        while kept < len(stack):
            indent = r.indent()
            if stack[kept] == QUOTE:
                if indent > 3 or r.char() != ">":
                    break
                r.enter_quote()
            elif r.i < len(line):
                if indent < stack[kept]:
                    break
                r.at += stack[kept]
            kept += 1
        if opened:
            if kept == len(stack):
                close = FENCE_CLOSE.match(line, r.i) if r.indent() < 4 else None
                ticks = opened[2]
                if close and close.group(1)[0] == ticks[0] and len(close.group(1)) >= len(ticks):
                    out.append((opened[0], opened[1], off + r.i, off + len(raw)))
                    opened = None
                off += len(raw) + 1
                continue
            out.append((opened[0], opened[1], off - 1, off - 1))
            opened = None
        # The containers it opens.
        indent, rest = r.indent(), line[r.i:]
        while rest and indent < 4 and kept < MAX_CONTAINERS:
            marker = None if DIVIDER.match(rest) else LIST_MARKER.match(rest)
            note = FOOTNOTE.match(rest)
            if rest[0] == ">":
                r.enter_quote()
                width = QUOTE
            elif marker:
                at = r.at
                width = r.enter_item(indent, marker.end())
                r.at = at + width
            elif note:
                r.enter_note(note.end())
                width = 4
            else:
                break
            del stack[kept:]
            stack.append(width)
            kept += 1
            paragraph = False
            indent, rest = r.indent(), line[r.i:]
        # What the line itself is. A line of text with a paragraph open
        # stays in the containers it left (a lazy line); anything else
        # leaves them.
        fence = FENCE_OPEN.match(rest) if indent < 4 else None
        opens = fence and not (fence.group(1)[0] == "`" and "`" in fence.group(2))
        starts = bool(opens or (indent < 4 and (HEADING.match(rest) or DIVIDER.match(rest))))
        if not rest or starts or not paragraph:
            del stack[kept:]
        if opens:
            opened = (off, off + len(raw), fence.group(1))
        paragraph = bool(rest) and not starts and (paragraph or indent < 4)
        off += len(raw) + 1
    if opened:
        out.append((opened[0], opened[1], len(text), len(text)))
    return out


def _code_spans(text: str, beside_math: bool = False) -> list:
    """The inline code spans in ``text``, ``(start, end)`` in order, by
    CommonMark's rule kept to one line: a run of backticks opens a span that
    the next run of the same length closes ("``a`b``" is one span). A
    backslash escapes an opener's first backtick ("\\`x`" is no code), never
    a closer's: inside code a backslash is literal. ``beside_math`` reads
    them as the renderer does beside formulas, whichever starts first
    holding the other. The twin of the frontend's shared/lib/mdCode.codeSpans."""
    marks = CODE_OR_MATH if beside_math else BACKTICKS
    spans, off = [], 0
    for line in text.split("\n"):
        runs = [(m.start(), m.group()) for m in marks.finditer(line)]
        by_mark, seen = {}, {}
        for k, (_at, mark) in enumerate(runs):
            by_mark.setdefault(mark, []).append(k)
        floor, k = 0, 0
        while k < len(runs):
            at, mark = runs[k]
            dollar = mark[0] == "$"
            slashes = 0
            while at - slashes - 1 >= floor and line[at - slashes - 1] == "\\":
                slashes += 1
            if slashes % 2:
                if dollar:
                    k += 1
                    continue
                at, mark = at + 1, mark[1:]
            closers = by_mark.get(mark, [])
            i = seen.get(mark, 0)
            while i < len(closers) and closers[i] <= k:
                i += 1
            seen[mark] = i
            if mark and i < len(closers):
                close_at = runs[closers[i]][0]
                if not dollar:
                    spans.append((off + at, off + close_at + len(mark)))
                k = closers[i]
                floor = close_at + len(mark)
            k += 1
        off += len(line) + 1
    return spans


def _fences_beside_math(text: str) -> list:
    """The fences a formula leaves, ``(start, end)``: a line of ~~~ inside
    a $$ is TeX, and whatever opens in a formula is none to math."""
    in_span, kept = _code_test(text, False), []
    opened, pos = False, 0
    for f in _fences(text):
        for m in DOUBLE_DOLLAR.finditer(text, pos, f[0]):
            if not in_span(m.start()):
                opened = not opened
        pos = f[0] if opened else f[3]
        if not opened:
            kept.append((f[0], f[3]))
    return kept


def _code_test(text: str, fences: bool = True):
    """A test of whether a position in ``text`` is code: in a code span
    beside the formulas or, with ``fences``, in a fence outside them, its
    delimiters too. Asked in ascending order (the frontend's
    shared/lib/mdCode.codeTest)."""
    if "`" not in text and "~~~" not in text:
        return lambda pos: False
    ranges = sorted((_fences_beside_math(text) if fences else []) + _code_spans(text, True))
    k = [0]

    def test(pos: int) -> bool:
        while k[0] < len(ranges) and ranges[k[0]][1] <= pos:
            k[0] += 1
        return k[0] < len(ranges) and ranges[k[0]][0] <= pos
    return test


def _outside_code(s: str, fn) -> str:
    out, pos = [], 0
    for start, end in _code_spans(s):
        out.append(fn(s[pos:start]) + s[start:end])
        pos = end
    return "".join(out) + fn(s[pos:])


def map_prose(text: str, fn) -> str:
    """``text`` with ``fn`` applied to what lies outside its fences and code
    spans, as the frontend's shared/lib/mdCode.mapProse does; note_markup's
    image-size rewrite skips code with it too."""
    out, pos = [], 0
    for start, _inner, _to, end in _fences(text):
        out.append(_outside_code(text[pos:start], fn) + text[start:end])
        pos = end
    return "".join(out) + _outside_code(text[pos:], fn)


class _UrlScan:
    """Whether a position in ``s`` is inside a web address or a link's
    target, for positions asked in increasing order (it reads ``s`` once).
    An address runs from "://" or a "www." to the next space, a link's
    target from "](" to its balanced ")", an autolink from "<scheme:" to
    ">". ``skip(to)`` steps over a formula, which ends an address."""

    def __init__(self, s: str):
        self.s, self.i, self.mode, self.depth = s, 0, None, 0

    def __call__(self, at: int) -> bool:
        s = self.s
        while self.i < at:
            i, c = self.i, s[self.i]
            if self.mode and _space(c):
                self.mode = None
            elif self.mode == "target":
                if c == "(" and not escaped_at(s, i):
                    self.depth += 1
                elif c == ")" and not escaped_at(s, i):
                    self.depth -= 1
                    if self.depth == 0:
                        self.mode = None
            elif self.mode == "auto":
                if c == ">":
                    self.mode = None
            elif not self.mode:
                if c == "(" and i >= 1 and s[i - 1] == "]":
                    self.mode, self.depth = "target", 1
                elif (c == "/" and i >= 2 and s[i - 2:i] == ":/") or (
                        c == "." and i >= 3 and s[i - 3:i] == "www" and not _word(s[i - 4:i - 3])):
                    self.mode = "address"
                elif c == "<" and SCHEME.match(s[i + 1:i + 34]):
                    self.mode = "auto"
            self.i += 1
        return self.mode is not None

    def skip(self, to: int):
        self.i, self.mode = max(self.i, to), None


def _raw_pairs(s: str):
    """The ``\\( … \\)`` and ``\\[ … \\]`` pairs as written in ``s``,
    ``(from, to, inner, display)``, matched opener to closer; a second
    opener before the close starts afresh."""
    open_at, display = None, False
    for m in DELIM.finditer(s):
        if escaped_at(s, m.start()):
            continue
        c = m.group()[1]
        if c in "[(":
            open_at, display = m.start(), c == "["
        elif open_at is not None and display == (c == "]"):
            yield open_at, m.end(), s[open_at + 2:m.start()], display
            open_at = None


def _bare_dollars(tex: str) -> int:
    """The unescaped dollars in ``tex``."""
    return sum(1 for i, c in enumerate(tex) if c == "$" and not escaped_at(tex, i))


def _display_formula(s: str, start: int, to: int, inner: str) -> bool:
    """A display pair is shaped as a formula is written, not as a bracketed
    phrase ("\\[1\\]", "\\[n = 30\\]", "\\[x\\](y)", "\\[ see appendix \\]"):
    alone on lines of its own, or holding a \\command (an escaped backslash
    is none), or padded with spaces as ChatGPT writes it
    ("\\[ E = mc^2 \\]") around a sign only math uses and no Markdown of its
    own (another app's "Copy as Markdown" escapes "[ Reply ]" so too);
    never empty or across a blank line. Gamma writes a literal bracket as
    "\\[x]" (text_box.escape_markdown), which has no closer to pair; an older
    note's "\\[x\\]" is unpadded."""
    if s[to:to + 1] == "(" or not inner.strip(SPACE) or BLANK_LINE.search(inner):
        return False
    line_start = s.rfind("\n", 0, start) + 1
    if OPENS_LINE.match(inner) and CLOSES_LINE.search(inner) and LINE_PREFIX.fullmatch(s[line_start:start]):
        return True
    tex = ESCAPED.sub("", inner)
    padded = inner[:1] in " \t" and inner[-1:] in " \t"
    return bool(COMMAND.search(tex)) or (padded and bool(SIGN.search(tex)) and not MARKDOWN.search(inner))


def _pairs(s: str, kept: set, formulas: list) -> list:
    """The pairs in a stretch of prose that convert: none inside a dollar
    formula it already holds (``formulas``, in order), part of an address,
    or one the text read already held. An inline one keeps to its line and
    holds no math of its own in dollars."""
    pairs, in_url, k = [], _UrlScan(s), 0
    for start, to, inner, display in _raw_pairs(s):
        while k < len(formulas) and formulas[k][1] <= start:
            k += 1
        if (k < len(formulas) and formulas[k][0] < start) or in_url(start) or s[start:to] in kept:
            continue
        if not (_display_formula(s, start, to, inner) if display
                else inner.strip(SPACE) and "\n" not in inner and _bare_dollars(inner) < 2):
            continue
        pairs.append((start, to, inner, display))
        in_url.skip(to)
    return pairs


def _next_dollar(s: str, at: int) -> int:
    """The next unescaped dollar after ``at`` in its paragraph (a formula
    may wrap, as LaTeX source does, but not into another block), or -1."""
    for j in range(at + 1, len(s)):
        if s[j] == "\n" and BLOCK_START.match(s[j + 1:j + 64]):
            return -1
        if s[j] == "$" and not escaped_at(s, j):
            return j
    return -1


def _opens_math(s: str, at: int, close: int) -> bool:
    """Whether a lone "$" at ``at`` opens a dollar formula that closes at
    ``close``, by Pandoc's rule made strict for a text that writes "$" as
    money: the opener before a non-space and no closing mark ("($)"), after
    no letter or digit; the closer lone, after a non-space (or an escaped
    one) and before no letter or digit. Han, kana and Hangul are no such
    letters: they set a formula without spaces."""
    nxt = s[at + 1:at + 2]
    if close < 0 or _space(nxt) or CLOSING_MARK.match(nxt) or _word(s[at - 1:at] if at else ""):
        return False
    after = s[close + 1:close + 2]
    return after != "$" and (not _space(s[close - 1]) or escaped_at(s, close - 1)) and not _word(after)


def _dollars_in(s: str, kept: set):
    """The dollar formulas ``s`` holds, ``(from, to)`` in order: a
    ``$$ … $$``, and a lone ``$ … $`` that _opens_math or ``kept`` takes for
    one; and its other lone dollars."""
    formulas, lone, display, i = [], [], -1, 0
    while m := RUNS.search(s, i):
        i = m.end()
        if escaped_at(s, m.start()):
            continue
        if len(m.group()) > 1:
            if display < 0:
                display = m.start()
            else:
                formulas.append((display, m.end()))
                display = -1
        elif display < 0:
            close = _next_dollar(s, m.start())
            if _opens_math(s, m.start(), close) or (close >= 0 and s[m.start():close + 1] in kept):
                formulas.append((m.start(), close + 1))
                i = close + 1
            else:
                lone.append(m.start())
    return formulas, lone


def _escape_dollars(s: str, kept: set) -> str:
    """A text that writes its formulas with backslashes writes "$" as a
    dollar ("$5", "($)", "US$"): escaped, so it can't pair with a converted
    formula's. A dollar formula of its own ("$2x$") or one the text read
    already held, a "$$" and a dollar in an address keep theirs."""
    out, pos, in_url = [], 0, _UrlScan(s)
    for at in _dollars_in(s, kept)[1]:
        if in_url(at):
            continue
        out.append(s[pos:at] + "\\$")
        pos = at + 1
    return "".join(out) + s[pos:]


def _tex_dollars(tex: str, display: bool) -> str:
    """A formula's dollars, which remark-math would end it at: a pair of
    them in a display formula (math in its \\text{…}) as ``\\( … \\)``,
    any other as KaTeX's text dollar."""
    bare = _bare_dollars(tex)
    paired = bare - bare % 2 if display else 0
    n = 0

    def one(m) -> str:
        nonlocal n
        bs = m.group(1)
        if len(bs) % 2:
            return bs[1:] + TEXT_DOLLAR
        k, n = n, n + 1
        return bs + (("\\)" if k % 2 else "\\(") if k < paired else TEXT_DOLLAR)

    return BACKSLASHED_DOLLAR.sub(one, tex)


def _formula(inner: str, display: bool) -> str:
    """The formula a pair becomes; inline math loses its padding. A control
    space ("\\ ") ending a one-line formula keeps its space: the trim takes
    the inline one's, and remark-math one off each side of a padded display
    one."""
    tex = _tex_dollars(inner if display else inner.strip(SPACE), display)
    tail = tex.rstrip(SPACE)
    space = " " if escaped_at(tail, len(tail)) and "\n" not in tex else ""
    return f"$${tex}{space}$$" if display else f"${tex}{space}$"


def _convert(s: str, kept: set, dollars: bool) -> str:
    def fix(gap: str) -> str:
        return _escape_dollars(gap, kept) if dollars else gap

    out, pos = [], 0
    for start, to, inner, display in _pairs(s, kept, _dollars_in(s, kept)[0]):
        gap = fix(s[pos:start])
        formula = _formula(inner, display)
        # A formula touching another dollar would read as "$$": a space between.
        if gap.endswith("$") if gap else pos > 0:
            formula = " " + formula
        if s[to:to + 1] == "$":
            formula += " "
        out.append(gap + formula)
        pos = to
    return "".join(out) + fix(s[pos:])


def _kept_in(text: str) -> set:
    """The ``\\( … \\)`` pairs and dollar spans ``text`` holds in its prose,
    as written: what a rewrite of it keeps."""
    kept = set()

    def collect(s: str) -> str:
        for start, to, _inner, _display in _raw_pairs(s):
            kept.add(s[start:to])
        for i, c in enumerate(s):
            if c == "$" and not escaped_at(s, i):
                close = _next_dollar(s, i)
                if close >= 0:
                    kept.add(s[i:close + 1])
        return s

    if text:
        map_prose(text, collect)
    return kept


def dollar_math(text: str, keep: str = "") -> str:
    """``text`` with its ``\\( … \\)`` and ``\\[ … \\]`` formulas as
    ``$ … $`` and ``$$ … $$``, in place (each line keeps its quote marks
    and indent), outside code. What ``keep`` already holds as written stays
    (an AI rewrite of the text it read). Lone dollars are escaped only when
    something converted."""
    if not text:
        return text
    kept = _kept_in(keep)
    plain = map_prose(text, lambda s: _convert(s, kept, False))
    return text if plain == text else map_prose(text, lambda s: _convert(s, kept, True))


def _in_math(doc: str, pos: int) -> bool:
    """Whether ``pos`` is in a ``$ … $`` or ``$$ … $$`` span, an unclosed
    one included (latexCompletion.findMathAtCursor). A dollar in code is
    none, and a lone $ ends with its paragraph."""
    in_code = _code_test(doc)
    open_at, open_len = None, 0
    for m in DOLLARS.finditer(doc):
        if (m.start() != pos and escaped_at(doc, m.start())) or in_code(m.start()):
            continue
        if open_at is not None and open_len == 1 and BLANK_LINE.search(doc, open_at, m.start()):
            if open_at + 1 <= pos <= doc.find("\n", open_at + 1):
                return True
            open_at = None
        if open_at is None:
            if m.start() >= pos:
                return False
            open_at, open_len = m.start(), len(m.group())
            continue
        if open_at + open_len <= pos <= m.start():
            return True
        open_at = None
        if m.end() > pos:
            return False
    if open_at is None:
        return False
    end = doc.find("\n", open_at + open_len) if open_len == 1 else -1
    return open_at + open_len <= pos <= (len(doc) if end < 0 else end)


def paste_math(doc: str, pos: int, text: str) -> str:
    """What ``text`` put in at ``pos`` in ``doc`` becomes: converted, unless
    it lands in source (a code fence, inline code or math), where it stays as
    written (pasteMath.pasteMath)."""
    if any(inner <= pos <= mark for _s, inner, mark, _e in _fences(doc)) \
            or any(a < pos < b for a, b in _code_spans(doc)):
        return text
    return text if _in_math(doc, pos) else dollar_math(text)
