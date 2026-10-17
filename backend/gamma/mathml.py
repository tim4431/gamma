"""MathML inside registry text, as LaTeX math or as plain text.

Crossref keeps a publisher's inline MathML in titles ("spin-<mml:math>
<mml:mn>1</mml:mn><mml:mo>∕</mml:mo><mml:mn>2</mml:mn></mml:math>"),
pretty-printed over several lines, and doi.org's BibTeX rendering prints it
with curly quotes around the attribute values. So the fragment is read as a
sequence of tags without trusting it to be XML: attributes are ignored and a
tag that does not close is closed by its parent.

``to_latex`` is what ``bibtex`` puts between ``$…$``; ``to_text`` is what
``textnorm.plain_text`` stores (the title as the PDF prints it, which the
title match compares). ``SYMBOLS`` is the one table of characters that have
a math-mode LaTeX command, shared with ``bibtex``'s text-mode conversion.
"""

import html
import re
import unicodedata

# One inline formula, its namespace prefix optional (<mml:math>, <math>).
MATH_RE = re.compile(r"<(?:[\w.-]+:)?math\b[^>]*>.*?</(?:[\w.-]+:)?math\s*>", re.S | re.I)
_TAG_RE = re.compile(r"<(/?)(?:[\w.-]+:)?([A-Za-z][\w.-]*)(?:\s[^<>]*?)?(/?)>")

# Characters with a math-mode command. Greek letters come from their
# Unicode names (below); these are the rest a physics or chemistry title
# carries.
SYMBOLS = {
    "×": r"\times", "÷": r"\div", "±": r"\pm", "∓": r"\mp", "·": r"\cdot", "∙": r"\cdot",
    "−": "-", "∕": "/", "⁄": "/", "∗": "*", "′": "'", "″": "''",
    "≈": r"\approx", "≃": r"\simeq", "≅": r"\cong", "∼": r"\sim", "∝": r"\propto",
    "≠": r"\neq", "≡": r"\equiv", "≤": r"\leq", "≥": r"\geq", "≪": r"\ll", "≫": r"\gg",
    "→": r"\rightarrow", "←": r"\leftarrow", "↔": r"\leftrightarrow", "⇒": r"\Rightarrow",
    "⇔": r"\Leftrightarrow", "↑": r"\uparrow", "↓": r"\downarrow",
    "∞": r"\infty", "∂": r"\partial", "∇": r"\nabla", "√": r"\surd", "∑": r"\sum",
    "∏": r"\prod", "∫": r"\int", "∮": r"\oint", "∈": r"\in", "∉": r"\notin", "⊂": r"\subset",
    "⊗": r"\otimes", "⊕": r"\oplus", "†": r"\dagger", "‡": r"\ddagger", "ℏ": r"\hbar",
    "ℓ": r"\ell", "°": r"^\circ", "∘": r"\circ", "⟨": r"\langle", "⟩": r"\rangle",
    "〈": r"\langle", "〉": r"\rangle", "‖": r"\|", "∥": r"\parallel", "⊥": r"\perp",
    "∠": r"\angle", "∅": r"\emptyset", "∀": r"\forall", "∃": r"\exists", "¬": r"\neg",
    "∧": r"\wedge", "∨": r"\vee", "∩": r"\cap", "∪": r"\cup", "ℜ": r"\Re", "ℑ": r"\Im",
    "µ": r"\mu",
}
# Greek capitals LaTeX writes as Latin letters, and the Unicode names that
# differ from LaTeX's.
_GREEK_CAPITALS = {"Gamma", "Delta", "Theta", "Lambda", "Xi", "Pi", "Sigma", "Upsilon", "Phi", "Psi", "Omega"}
_GREEK_NAMES = {"lamda": "lambda", "final sigma": "varsigma", "epsilon": "varepsilon", "phi": "varphi",
                "theta symbol": "vartheta", "phi symbol": "phi", "pi symbol": "varpi", "rho symbol": "varrho",
                "lunate epsilon symbol": "epsilon", "kappa symbol": "varkappa"}
_GREEK_LATIN = {"Alpha": "A", "Beta": "B", "Epsilon": "E", "Zeta": "Z", "Eta": "H", "Iota": "I",
                "Kappa": "K", "Mu": "M", "Nu": "N", "Omicron": "O", "Rho": "P", "Tau": "T", "Chi": "X"}


def math_command(ch: str) -> str:
    """The math-mode LaTeX for one character, "" when it has none."""
    if ch in SYMBOLS:
        return SYMBOLS[ch]
    name = unicodedata.name(ch, "")
    m = re.match(r"GREEK (SMALL|CAPITAL) LETTER (.+)$", name) or re.match(r"GREEK ()(.+ SYMBOL)$", name)
    if not m:
        return ""
    letter = m.group(2).lower()
    if m.group(1) == "CAPITAL":
        title = letter.title()
        return f"\\{title}" if title in _GREEK_CAPITALS else _GREEK_LATIN.get(title, "")
    letter = _GREEK_NAMES.get(letter, letter)
    if letter == "omicron":
        return "o"
    return f"\\{letter}" if re.fullmatch(r"[a-z]+", letter) else ""


def _tree(fragment: str) -> list:
    """The fragment's elements as nested [name, children] lists (text
    children are strings), attributes dropped."""
    root = ["math", []]
    stack = [root]
    pos = 0
    for m in _TAG_RE.finditer(fragment):
        text = fragment[pos:m.start()]
        if text.strip():
            stack[-1][1].append(html.unescape(text.strip()))
        pos = m.end()
        name = m.group(2).lower()
        if m.group(1):  # a closing tag: close up to the element it names
            for depth in range(len(stack) - 1, 0, -1):
                if stack[depth][0] == name:
                    del stack[depth:]
                    break
        elif name != "math" or len(stack) > 1:
            node = [name, []]
            stack[-1][1].append(node)
            if not m.group(3):  # <mspace/> closes itself
                stack.append(node)
    tail = fragment[pos:]
    if tail.strip():
        stack[-1][1].append(html.unescape(tail.strip()))
    return root


def _multiscripts(kids: list, render) -> tuple[str, list, list]:
    """An <mmultiscripts> rendered as its base, the (sub, sup) pairs after
    it and the pairs before it (past <mprescripts/>): APS writes an isotope,
    ¹⁷¹Yb, as the base Yb with the prescript 171. <none/> renders empty."""
    split = next((i for i, k in enumerate(kids) if not isinstance(k, str) and k[0] == "mprescripts"), len(kids))
    out = [render(k).strip() for k in kids]
    post, pre = out[1:split], out[split + 1:]
    return out[0], list(zip(post[0::2], post[1::2])), list(zip(pre[0::2], pre[1::2]))


def _tex_chars(text: str) -> str:
    out = []
    for c in text:
        cmd = math_command(c) if ord(c) > 127 else ""
        # A command spelled in letters needs a space before the next letter.
        out.append(cmd + " " if cmd[-1:].isalpha() else cmd or c)
    return "".join(out).strip()


def _tex(node) -> str:
    if isinstance(node, str):
        return _tex_chars(node)
    name, kids = node
    parts = [_tex(k).strip() for k in kids]

    def group(s: str) -> str:  # one character or one command needs no braces
        return s if len(s) == 1 or re.fullmatch(r"\\[A-Za-z]+", s) else "{" + s + "}"
    if name in ("msub", "msup", "msubsup", "mfrac", "mroot", "mover", "munder", "munderover") and len(parts) >= 2:
        base = group(parts[0]) if parts[0] else "{}"
        if name == "msub":
            return f"{base}_{group(parts[1])}"
        if name == "msup":
            return f"{base}^{group(parts[1])}"
        if name == "msubsup" and len(parts) >= 3:
            return f"{base}_{group(parts[1])}^{group(parts[2])}"
        if name == "mfrac":
            return f"\\frac{{{parts[0]}}}{{{parts[1]}}}"
        if name == "mroot":
            return f"\\sqrt[{parts[1]}]{{{parts[0]}}}"
        if name == "mover":
            accent = {"~": r"\tilde", "˜": r"\tilde", "^": r"\hat", "ˆ": r"\hat", "¯": r"\bar", "‾": r"\bar",
                      "-": r"\bar", "→": r"\vec", r"\rightarrow": r"\vec", "˙": r"\dot", "¨": r"\ddot",
                      ".": r"\dot"}.get(parts[1])
            return f"{accent}{{{parts[0]}}}" if accent else f"\\overset{{{parts[1]}}}{{{parts[0]}}}"
        return parts[0]  # munder / munderover: the base
    if name == "mmultiscripts" and kids:
        base, post, pre = _multiscripts(kids, _tex)

        def scripts(pairs) -> str:
            return "".join((f"_{group(sub)}" if sub else "") + (f"^{group(sup)}" if sup else "")
                           for sub, sup in pairs)
        before, after = scripts(pre), scripts(post)
        # Prescripts hang off an empty group, as {}^{87}\mathrm{Rb} does.
        return ("{}" + before if before else "") + (group(base) if after else base) + after
    if name == "msqrt":
        return f"\\sqrt{{{''.join(parts)}}}"
    if name in ("mi", "mtext", "ms") and kids and isinstance(kids[0], str):
        word = "".join(parts)
        # A one-letter identifier is italic; a name ("sin", "Rb") is upright.
        if name == "mi" and len(kids[0]) == 1:
            return word
        return word if word.startswith("\\") else f"\\mathrm{{{word}}}"
    if name == "mfenced":
        return "(" + ",".join(parts) + ")"
    if name in ("annotation", "annotation-xml"):
        return ""
    if name == "semantics":
        return parts[0] if parts else ""
    if name == "mspace":
        return r"\,"
    return "".join(parts)


def to_latex(fragment: str) -> str:
    """One MathML formula as LaTeX math, without the ``$`` around it."""
    return re.sub(r"\s+", " ", _tex(_tree(fragment))).strip()


def _text(node) -> str:
    if isinstance(node, str):
        return "".join({"∕": "/", "⁄": "/", "−": "-"}.get(c, c) for c in node)
    name, kids = node
    parts = [_text(k) for k in kids]
    if name in ("annotation", "annotation-xml"):
        return ""
    if name == "semantics":
        return parts[0] if parts else ""
    if name == "mfrac" and len(parts) >= 2:
        return f"{parts[0]}/{parts[1]}"
    if name == "mmultiscripts" and kids:
        # In reading order, prescripts first: the PDF prints "171Yb".
        base, post, pre = _multiscripts(kids, _text)
        return "".join(a + b for a, b in pre) + base + "".join(a + b for a, b in post)
    return "".join(parts)


def to_text(fragment: str) -> str:
    """One MathML formula as the plain text it reads as ("1/2", "87Rb")."""
    return _text(_tree(fragment)).strip()


def replace(text: str, render) -> str:
    """``text`` with each MathML formula replaced by ``render(fragment)``. A
    hyphen the pretty-printing separated from its formula ("spin-\\n
    <mml:math>") is joined back to it."""
    text = re.sub(r"-\s+(?=<(?:[\w.-]+:)?math\b)", "-", str(text or ""), flags=re.I)
    return MATH_RE.sub(lambda m: render(m.group(0)), text)
