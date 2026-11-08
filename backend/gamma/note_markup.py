"""Spans the PDF writers draw, and LaTeX approximated as them.

A span here is ``(TEXT, string, level)`` — level 0, +1 superscript, -1
subscript — or ``(MATH, latex, 0)`` for inline math, which ``vector_text``
typesets; ``pdf_typeset`` adds a style to each. The notes are parsed from
their Markdown by ``pdf_document.chunks``.

``latex_spans`` is the *fallback* for when that renderer is unavailable or
chokes on an expression: commands become their unicode symbol (drawn with the
base-14 Symbol font, which every viewer has), ``^``/``_`` become genuinely
raised/lowered runs, and structures collapse to inline forms
(``\\frac{a}{b}`` → ``a/b``, ``\\sqrt x`` → ``√x``). Unknown commands fall back
to their own name, which is what you want for ``\\sin``, ``\\log``, ``\\max``.
It also keeps the inline grammar (``INLINE_RE``) that both ``pdf_document``
and the Zotero notes (``zotero_notes``) read a block's text with, and the
image-size syntax every backend reader of an image shares.
"""

import re

from .paste_math import map_prose

SUP, SUB = 1, -1
# A span is (kind, payload, level): TEXT carries a string, MATH the LaTeX source
# of an inline expression that vector_text typesets.
TEXT, MATH = "t", "m"

# One pass over a paragraph of markdown. Order matters: ** before *, ![[ before [[,
# \( math before a backslash escape (CommonMark's: a backslash before ASCII
# punctuation is that character, so "\*" or "\$" reads as typed, as on
# screen). A closing mark never follows a backslash, which escapes it. The
# marks and a link's label may cross the paragraph's line breaks, as on
# screen ("**a⏎b**" is bold on both lines); ==mark== and the rest stay
# within a line, and every line break stays in the text. LITERAL_SPANS are
# its first alternatives, the spans read as written (code, math, an escape):
# nothing inside one is markup, which a reader that takes images out first
# (zotero_notes) has to know too.
#
# A link's target, an image's source and a bare web address hold a "(" only
# as one of a balanced pair (one level deep), as on screen: an Elsevier DOI
# "…/S0140-6736(20)30183-5" or "…/Foo_(bar)" reads whole, and "(see
# https://x.org)" leaves its ")" out. A title follows the target after a
# space, so a target never stops short at a "(" and LINK_PARTS, which reads
# the target alone, takes the one the link was read with.
LINK_TARGET = r"(?:[^()\s]|\([^()\s]*\))+"
BARE_URL = r"https?://(?:[^\s<>()\[\]]|\([^\s<>()\[\]]*\))+"
LITERAL_SPANS = (
    r"(?P<code>``[^`\n]+``|`[^`\n]+`)"
    r"|(?P<dmath>\$\$[^\n]+?\$\$)"
    r"|(?P<math>\$[^$\n]+?\$|\\\([^\n]+?\\\))"
    r"|(?P<esc>\\[!-/:-@\[-`{-~])"
)
INLINE_RE = re.compile(
    LITERAL_SPANS
    + r"|(?P<underline><u>(?s:.+?)</u>)"
    r'|(?P<color><span style="[^"<>]*">(?s:.*?)</span>)'
    r"|(?P<bold>\*\*(?=\S)(?s:.+?)(?<=[^\s\\])\*\*|__(?=\S)(?s:.+?)(?<=[^\s\\])__)"
    r"|(?P<italic>\*(?=\S)[^*]+?(?<=[^\s\\])\*|(?<![\w_])_(?=\S)[^_]+?(?<=[^\s\\])_(?![\w_]))"
    r"|(?P<strike>~~(?=\S)(?s:.+?)(?<=[^\s\\])~~)"
    r"|(?P<mark>==(?=\S).+?(?<=[^\s\\])==)"
    r"|(?P<embed>!\[\[[^\]\n]+\]\])"
    r"|(?P<ref>\[\[[^\]\n]+\]\])"
    r"|(?P<link>\[[^\]]*\]\([ \t]*" + LINK_TARGET + r"(?:[ \t][^)\n]*)?\))"
    r"|(?P<url>" + BARE_URL + ")"
)
LINK_PARTS = re.compile(r"\[([^\]]*)\]\(\s*(" + LINK_TARGET + ")")

# Image sizes. Gamma keeps an image's display width at the end of its alt,
# as Obsidian does and the editor writes it: ``![alt|300](url)``
# (``|300x200``: the height is ignored), ALT_SIZE_RE. Logseq writes it after
# the image, ``![alt](url){:width 300}`` (WIDTH_SUFFIX). The Markdown and
# Logseq imports and the Markdown exports rewrite that to the alt's size
# (``obsidian_image_sizes``). Stored rows keep it: a paste, the API or a
# mirror peer store text as sent, an earlier release's imports stored it
# as written, and a restore rewrites it only on a copy stamped below
# ``migrations.WS_VERSION_BASE`` (a backup from before the stamps, or a
# scoped Gamma export; gamma/normalize.py), leaving a newer copy's rows
# as they were. So every reader of an image takes both, through
# ``alt_and_width``. The editor's twin is mdMarks.js scanImageSyntax.
ALT_SIZE_RE = re.compile(r"(.*?)\|(\d+)(?:x\d+)?", re.S)
WIDTH_SUFFIX = r"\{:width\s+(?P<lw>\d+)\}"
_SIZED_AFTER_RE = re.compile(
    r"!\[(?P<alt>[^\]\n]*)\]\((?P<src>[ \t]*" + LINK_TARGET + r"(?:[ \t][^)\n]*)?)\)" + WIDTH_SUFFIX)


def alt_and_width(alt, width=None):
    """An image's alt and display width (a string of digits, or None): the
    size closing the alt taken off it, and a Logseq ``width`` read after
    the image winning over it, as on screen."""
    size = ALT_SIZE_RE.fullmatch(alt or "")
    if size:
        return size.group(1), width or size.group(2)
    return alt, width


def obsidian_image_sizes(md: str) -> str:
    """``![a](u){:width N}`` → ``![a|N](u)``, outside code fences and code
    spans; text without the suffix is returned unchanged."""
    md = md or ""
    if "{:width" not in md:
        return md

    def one(m):
        alt, width = alt_and_width(m.group("alt"), m.group("lw"))
        return f"![{alt}|{width}]({m.group('src')})"
    return map_prose(md, lambda prose: _SIZED_AFTER_RE.sub(one, prose))


# LaTeX command → unicode. Every value here is either WinAnsi- or Symbol-font
# encodable (see pdf_typeset.font_of), so it can actually be drawn.
SYMBOLS = {
    "alpha": "α", "beta": "β", "gamma": "γ", "delta": "δ", "epsilon": "ε",
    "varepsilon": "ε", "zeta": "ζ", "eta": "η", "theta": "θ", "vartheta": "ϑ",
    "iota": "ι", "kappa": "κ", "lambda": "λ", "mu": "μ", "nu": "ν", "xi": "ξ",
    "pi": "π", "varpi": "ϖ", "rho": "ρ", "sigma": "σ", "varsigma": "ς",
    "tau": "τ", "upsilon": "υ", "phi": "φ", "varphi": "ϕ", "chi": "χ",
    "psi": "ψ", "omega": "ω",
    "Gamma": "Γ", "Delta": "∆", "Theta": "Θ", "Lambda": "Λ", "Xi": "Ξ",
    "Pi": "Π", "Sigma": "Σ", "Upsilon": "Υ", "Phi": "Φ", "Psi": "Ψ",
    "Omega": "Ω",
    "sum": "∑", "prod": "∏", "int": "∫", "infty": "∞", "partial": "∂",
    "nabla": "∇", "surd": "√", "pm": "±", "times": "×", "div": "÷",
    "cdot": "⋅", "bullet": "•", "ast": "∗", "star": "∗", "circ": "°",
    "leq": "≤", "le": "≤", "geq": "≥", "ge": "≥", "neq": "≠", "ne": "≠",
    "approx": "≈", "equiv": "≡", "cong": "≅", "sim": "∼", "simeq": "≅",
    "propto": "∝", "perp": "⊥", "angle": "∠", "degree": "°", "prime": "′",
    "in": "∈", "notin": "∉", "ni": "∋", "subset": "⊂", "subseteq": "⊆",
    "supset": "⊃", "supseteq": "⊇", "cup": "∪", "cap": "∩", "emptyset": "∅",
    "varnothing": "∅", "forall": "∀", "exists": "∃", "neg": "¬", "lnot": "¬",
    "land": "∧", "wedge": "∧", "lor": "∨", "vee": "∨", "therefore": "∴",
    "oplus": "⊕", "otimes": "⊗", "aleph": "ℵ",
    "to": "→", "rightarrow": "→", "gets": "←", "leftarrow": "←",
    "uparrow": "↑", "downarrow": "↓", "leftrightarrow": "↔",
    "Rightarrow": "⇒", "Leftarrow": "⇐", "Leftrightarrow": "⇔", "iff": "⇔",
    "implies": "⇒", "mapsto": "→",
    "ldots": "…", "cdots": "…", "dots": "…", "vdots": "…", "hbar": "h",
    "Re": "ℜ", "Im": "ℑ", "wp": "℘", "langle": "〈", "rangle": "〉",
    "lvert": "|", "rvert": "|", "vert": "|", "mid": "|", "|": "|",
    "{": "{", "}": "}", "$": "$", "%": "%", "&": "&", "#": "#", "_": "_",
}

# Commands that only affect spacing/size — dropped, or reduced to one space.
_SPACERS = {"left", "right", "big", "Big", "bigg", "Bigg", "bigl", "bigr",
            "Bigl", "Bigr", "displaystyle", "textstyle", "scriptstyle",
            "limits", "nolimits", "!", "@"}
_THIN = {",", ";", ":", " ", "quad", "qquad", "enspace", "thinspace", "hspace"}
# Contents kept, styling dropped.
_TRANSPARENT = {"text", "textrm", "textbf", "textit", "mathrm", "mathbf",
                "mathit", "mathsf", "mathtt", "mathcal", "mathbb", "mathfrak",
                "boldsymbol", "operatorname", "mbox", "hbox", "bm", "pmb",
                "overline", "underline", "tilde", "hat", "bar", "vec", "dot"}

def _read_group(tex: str, i: int):
    """The argument starting at ``i``: a {...} group, a \\command, or one char."""
    n = len(tex)
    while i < n and tex[i] == " ":
        i += 1
    if i >= n:
        return "", i
    if tex[i] == "{":
        depth, start = 1, i + 1
        j = start
        while j < n and depth:
            if tex[j] == "\\":
                j += 2
                continue
            depth += 1 if tex[j] == "{" else -1 if tex[j] == "}" else 0
            j += 1
        return tex[start:j - 1], j
    if tex[i] == "\\":
        j = i + 1
        while j < n and tex[j].isalpha():
            j += 1
        return tex[i:max(j, i + 2)], max(j, i + 2)
    return tex[i], i + 1


def _atomic(spans) -> bool:
    """True when the spans read as one symbol — no parentheses needed."""
    text = "".join(t for t, _ in spans)
    return len(text) <= 1 or (text.isalnum() and len(text) <= 2)


def _paren(spans, level):
    return spans if _atomic(spans) else [("(", level)] + spans + [(")", level)]


def latex_spans(tex: str, level: int = 0):
    """LaTeX fragment → text spans: the unicode approximation used when the
    math renderer isn't available (or chokes). Newlines survive as "\\n"."""
    return merge_spans([(TEXT, t, lv) for t, lv in _latex_pairs(tex, level)])


def _latex_pairs(tex: str, level: int = 0):
    """The approximation itself, as (text, level) pairs."""
    out, i, n = [], 0, len(tex)
    while i < n:
        ch = tex[i]
        if ch == "\\":
            j = i + 1
            while j < n and tex[j].isalpha():
                j += 1
            cmd = tex[i + 1:j] if j > i + 1 else tex[i + 1:i + 2]
            i = j if j > i + 1 else i + 2
            if cmd == "\\":
                out.append(("\n", level))
            elif cmd in SYMBOLS:
                out.append((SYMBOLS[cmd], level))
            elif cmd in _SPACERS:
                pass
            elif cmd in _THIN:
                out.append((" ", level))
            elif cmd in _TRANSPARENT:
                g, i = _read_group(tex, i)
                out.extend(_latex_pairs(g, level))
            elif cmd in ("frac", "dfrac", "tfrac", "binom"):
                a, i = _read_group(tex, i)
                b, i = _read_group(tex, i)
                out.extend(_paren(_latex_pairs(a, level), level))
                out.append(("/", level))
                out.extend(_paren(_latex_pairs(b, level), level))
            elif cmd == "sqrt":
                if i < n and tex[i] == "[":          # \sqrt[n]{…} — index dropped
                    i = tex.find("]", i) + 1 or i
                g, i = _read_group(tex, i)
                out.append(("√", level))
                out.extend(_paren(_latex_pairs(g, level), level))
            elif cmd in ("ket", "bra", "braket", "ketbra"):
                a, i = _read_group(tex, i)
                if cmd == "ket":
                    out += [("|", level)] + _latex_pairs(a, level) + [("〉", level)]
                elif cmd == "bra":
                    out += [("〈", level)] + _latex_pairs(a, level) + [("|", level)]
                else:
                    b, i = _read_group(tex, i)
                    out += ([("〈", level)] + _latex_pairs(a, level) + [("|", level)]
                            + _latex_pairs(b, level) + [("〉", level)])
            elif cmd in ("begin", "end"):
                _, i = _read_group(tex, i)           # environment name
                out.append(("\n", level))
            elif cmd == "over":
                out.append(("/", level))
            else:
                out.append((cmd, level))             # \sin, \log, \max …
        elif ch in "^_":
            g, i = _read_group(tex, i + 1)
            out.extend(_latex_pairs(g, SUP if ch == "^" else SUB))
        elif ch in "{}":
            i += 1
        elif ch in "&~":
            out.append((" ", level))
            i += 1
        elif ch == "%":
            i = tex.find("\n", i)
            if i < 0:
                break
        else:
            out.append((ch, level))
            i += 1
    return _merge_pairs(out)


def _merge_pairs(pairs):
    """Collapse neighbouring (text, level) pairs that share a level."""
    out = []
    for text, level in pairs:
        if out and out[-1][1] == level:
            out[-1] = (out[-1][0] + text, level)
        elif text:
            out.append((text, level))
    return [(t, lv) for t, lv in out if t]


def merge_spans(spans):
    """Collapse neighbouring text spans that share a level; math stays whole."""
    out = []
    for kind, payload, level in spans:
        if kind == TEXT and out and out[-1][0] == TEXT and out[-1][2] == level:
            out[-1] = (TEXT, out[-1][1] + payload, level)
        elif kind == MATH or payload:
            out.append((kind, payload, level))
    return [s for s in out if s[0] == MATH or s[1]]
