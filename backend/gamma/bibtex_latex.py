"""A field value of a BibTeX entry as LaTeX.

The registries hand over titles and venues as the publisher wrote them:
JATS or HTML markup, HTML entities, MathML, and any character Unicode has.
An entry carries them in ASCII LaTeX (``latex_text``), which reads the same
when converted again. ``bibtex_format`` writes every text value through
it, and ``bibtex_case`` braces the capitals a sentence-case style must
keep. Pure, standard library only.
"""

import html
import re
import unicodedata

from . import mathml

# The markup registries leave in titles and venues — Crossref keeps the
# publisher's JATS/HTML: "<i>Planck</i> 2018 results", "CsPbCl<sub>3</sub>
# Nanocrystals", "Astronomy &amp; Astrophysics" — and the LaTeX macro each
# tag becomes in an entry. A tag not listed here is dropped.
_TAG_MACRO = {"i": "emph", "em": "emph", "b": "textbf", "strong": "textbf",
              "sub": "textsubscript", "sup": "textsuperscript", "scp": "textsc", "sc": "textsc"}
_TAG_RE = re.compile(r"<(/?)([A-Za-z][\w:.-]*)(?:\s[^<>]*)?/?>")
# Characters LaTeX gives a meaning; one already escaped (a hand edit typed
# `\&`) is left alone. A `$` that pairs up is math and is kept.
_ESCAPE_RE = re.compile(r"(?<!\\)([&%#_$])")
_CARET_RE = re.compile(r"(?<!\\)\^")
_MATH_RE = re.compile(r"(?<!\\)\$(?:[^$\\]|\\.)+?(?<!\\)\$")

# Text-mode LaTeX for the characters outside ASCII that have one and no
# decomposition (accented letters come from `_ACCENTS`; Greek letters and
# math symbols are `mathml.math_command` in `$…$`).
_TEXT_CHARS = {
    "–": "--", "—": "---", "―": "---", "‐": "-", "‑": "-", "‒": "-",
    "‘": "`", "’": "'", "‚": ",", "“": "``", "”": "''", "„": ",,",
    "«": r"{\guillemotleft}", "»": r"{\guillemotright}", "…": r"\ldots{}",
    " ": "~", " ": r"\,", " ": r"\,", " ": " ", " ": " ", "​": "",
    "ß": r"{\ss}", "æ": r"{\ae}", "Æ": r"{\AE}", "œ": r"{\oe}", "Œ": r"{\OE}", "ø": r"{\o}", "Ø": r"{\O}",
    "ł": r"{\l}", "Ł": r"{\L}", "ı": r"{\i}", "ȷ": r"{\j}", "đ": r"{\dj}", "Đ": r"{\DJ}",
    "ð": r"{\dh}", "Ð": r"{\DH}", "þ": r"{\th}", "Þ": r"{\TH}", "ŋ": r"{\ng}", "Ŋ": r"{\NG}",
    "©": r"{\textcopyright}", "®": r"{\textregistered}", "™": r"{\texttrademark}",
    "§": r"{\S}", "¶": r"{\P}", "£": r"{\pounds}", "€": r"{\texteuro}", "•": r"{\textbullet}",
    "¡": "{!`}", "¿": "{?`}", "‰": r"{\textperthousand}",
}
# Combining marks and the accent command each becomes: a symbol (`\"o`) or
# a letter that takes its argument in braces (`\c{c}`).
_ACCENTS = {"̀": "`", "́": "'", "̂": "^", "̃": "~", "̄": "=", "̆": "u",
            "̇": ".", "̈": '"', "̊": "r", "̋": "H", "̌": "v",
            "̣": "d", "̧": "c", "̨": "k", "̱": "b"}


def _accented(ch: str) -> str:
    """An accented Latin letter as BibTeX's special character
    (`{\\"o}`, `{\\c{c}}`, `{\\={\\"u}}`), "" when it is not one."""
    base, *marks = unicodedata.normalize("NFD", ch)
    if not marks or not base.isascii() or not base.isalpha() or any(m not in _ACCENTS for m in marks):
        return ""
    out = base
    for mark in marks:
        cmd = _ACCENTS[mark]
        out = f"\\{cmd}{out}" if not cmd.isalpha() and len(out) == 1 else f"\\{cmd}{{{out}}}"
    return "{" + out + "}"


def _tex_char(ch: str) -> str:
    """One character as text-mode LaTeX. A character with no LaTeX form
    (CJK, most scripts) stays as it is: the file is UTF-8, and a Unicode
    engine (XeLaTeX, LuaLaTeX with biber) typesets it."""
    if ch.isascii():
        return ch
    if ch in _TEXT_CHARS:
        return _TEXT_CHARS[ch]
    accented = _accented(ch)
    if accented:
        return accented
    name = unicodedata.name(ch, "")
    plain = unicodedata.normalize("NFKD", ch)
    if name.startswith(("SUPERSCRIPT", "SUBSCRIPT")) and plain.isascii():
        return f"\\text{'superscript' if name.startswith('SUPER') else 'subscript'}{{{plain}}}"
    cmd = mathml.math_command(ch)
    if cmd:
        # A Greek capital LaTeX spells with a Latin letter (Alpha is A) is text.
        return cmd if cmd.isalpha() else f"${cmd}$"
    # Ligatures and full-width forms: "ﬁ" is "fi".
    return plain if plain != ch and plain.isascii() else ch


def balanced(text: str) -> str:
    """``text`` without the braces that pair with nothing: BibTeX counts
    every brace, escaped or not, and one unmatched brace breaks the file."""
    keep, opened = [], []
    for i, c in enumerate(text):
        if c == "{":
            opened.append(i)
        elif c == "}":
            if not opened:
                continue
            opened.pop()
        keep.append((i, c))
    drop = set(opened)
    return "".join(c for i, c in keep if i not in drop)


def closing_brace(text: str, at: int) -> int:
    """The index of the brace that closes the one at ``at``, -1 for none."""
    depth = 0
    for i in range(at, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                return i
    return -1


def latex_text(text: str) -> str:
    """A field value as LaTeX. Registry markup becomes macros (`<i>` →
    `\\emph{}`, `<sub>`/`<sup>` → `\\textsubscript{}`/`\\textsuperscript{}`,
    `<b>` → `\\textbf{}`, `<scp>` → `\\textsc{}`; other tags are dropped),
    a MathML formula becomes `$…$` (`mathml.to_latex`), HTML entities are
    decoded (`&amp;`), whitespace is collapsed, `& % # _ $ ^` outside math
    are escaped, and every other character outside ASCII becomes its LaTeX
    (`_tex_char`). What is already LaTeX (`\\&`, `{\\"o}`, `$x^2$`) is left
    as it is, so a value converted twice reads the same. Unbalanced markup
    falls back to dropping the tags, and an unmatched brace is dropped."""
    maths: list[str] = []

    def hold(latex: str) -> str:
        maths.append(latex)
        return f"\x00{len(maths) - 1}\x00"

    raw = mathml.replace(str(text or ""), lambda fragment: hold(mathml.to_latex(fragment)))

    def macro(m):
        name = _TAG_MACRO.get(m.group(2).lower().split(":")[-1])
        if not name:
            return ""
        return "}" if m.group(1) else f"\\{name}{{"
    stripped = _TAG_RE.sub("", raw)
    converted = _TAG_RE.sub(macro, raw)
    if converted.count("{") - converted.count("}") != stripped.count("{") - stripped.count("}"):
        converted = stripped
    converted = re.sub(r"\s+", " ", html.unescape(converted)).strip()
    converted = _MATH_RE.sub(lambda m: hold(m.group(0)[1:-1]), converted)
    converted = _CARET_RE.sub(r"\\^{}", _ESCAPE_RE.sub(r"\\\1", converted))
    # In LaTeX's default font encoding a text-mode < prints as ¡.
    converted = re.sub(r"[<>]", lambda m: f"${m.group(0)}$", converted)
    converted = "".join(_tex_char(c) for c in converted)
    converted = re.sub("\x00(\\d+)\x00", lambda m: f"${maths[int(m.group(1))]}$", converted)
    return re.sub(r"\s+", " ", balanced(converted)).strip()
