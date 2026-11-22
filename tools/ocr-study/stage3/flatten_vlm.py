"""Vision-language page OCR (engines/pageocr.py: $OCR_STUDY_DIR/stage2/vlm_raw/<engine>/<page>.json) as Stage 2
engine output ($OCR_STUDY_DIR/stage2/out/vlm-<engine>/<page>.json: {"lines": [{"text"}], "time"}), so
stage2/score.py scores it.

  python -I flatten_vlm.py [engine ...]      (none = every engine under vlm_raw)

The truth writes what is printed as plain text: subscripts and superscripts inline
("δ1", "σeest", "ωL1"), Greek and operators as Unicode, display math on "§M" lines in the
same flat way. So the models' Markdown, HTML, DocTags and LaTeX are flattened alike:
- Markdown: heading marks, emphasis, list bullets, images, table pipes and rules go;
- HTML: tags go (<br> and </p> break a line), entities decoded;
- DocTags (Granite): <loc_N> and element tags go, <nl> breaks a line;
- dots.ocr's layout JSON: each block's text in its order, pictures skipped;
- LaTeX, in $..$, $$..$$, \\(..\\), \\[..\\] or a bare formula line: commands to their
  Unicode character (Greek, relations, arrows, big operators), \\frac{a}{b} -> a/b,
  \\sqrt{x} -> √x, scripts, braces, font and sizing commands and spacing dropped.
Each output line is a line of the model's text; paragraphs stay one line (the scorer aligns
truth lines to the cheapest substring of the joined text, so line breaks cost nothing)."""

import html
import json
import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from common import WORK  # noqa: E402

S2 = WORK / "stage2"

GREEK = {
    "alpha": "α", "beta": "β", "gamma": "γ", "delta": "δ", "epsilon": "ε", "varepsilon": "ε", "zeta": "ζ",
    "eta": "η", "theta": "θ", "vartheta": "ϑ", "iota": "ι", "kappa": "κ", "lambda": "λ", "mu": "μ", "nu": "ν",
    "xi": "ξ", "pi": "π", "varpi": "ϖ", "rho": "ρ", "varrho": "ϱ", "sigma": "σ", "varsigma": "ς", "tau": "τ",
    "upsilon": "υ", "phi": "φ", "varphi": "φ", "chi": "χ", "psi": "ψ", "omega": "ω",
    "Gamma": "Γ", "Delta": "Δ", "Theta": "Θ", "Lambda": "Λ", "Xi": "Ξ", "Pi": "Π", "Sigma": "Σ",
    "Upsilon": "Υ", "Phi": "Φ", "Psi": "Ψ", "Omega": "Ω",
}
SYMBOLS = {
    "gg": "≫", "ll": "≪", "leq": "≤", "le": "≤", "geq": "≥", "ge": "≥", "neq": "≠", "ne": "≠",
    "approx": "≈", "simeq": "≃", "sim": "~", "cong": "≅", "equiv": "≡", "propto": "∝", "infty": "∞",
    "partial": "∂", "nabla": "∇", "int": "∫", "oint": "∮", "iint": "∬", "sum": "Σ", "prod": "Π",
    "times": "×", "cdot": "·", "cdots": "···", "ldots": "...", "dots": "...", "pm": "±", "mp": "∓",
    "rightarrow": "→", "to": "→", "leftarrow": "←", "leftrightarrow": "↔", "Rightarrow": "⇒",
    "Leftrightarrow": "⇔", "mapsto": "↦", "uparrow": "↑", "downarrow": "↓",
    "langle": "⟨", "rangle": "⟩", "prime": "′", "dagger": "†", "ddagger": "‡", "hbar": "ħ",
    "ell": "ℓ", "circ": "°", "degree": "°", "deg": "°", "in": "∈", "notin": "∉", "subset": "⊂",
    "supset": "⊃", "cup": "∪", "cap": "∩", "wedge": "∧", "vee": "∨", "otimes": "⊗", "oplus": "⊕",
    "perp": "⊥", "parallel": "∥", "angle": "∠", "forall": "∀", "exists": "∃", "neg": "¬",
    "div": "÷", "ast": "*", "star": "⋆", "bullet": "•", "vert": "|", "mid": "|", "Vert": "‖",
    "lbrace": "{", "rbrace": "}", "{": "{", "}": "}", "%": "%", "&": "&", "#": "#", "_": "_", "$": "$",
    "AA": "Å", "aa": "å", "o": "ø", "O": "Ø", "ss": "ß", "ae": "æ", "oe": "œ",
    "Re": "Re", "Im": "Im", "sin": "sin", "cos": "cos", "tan": "tan", "exp": "exp", "log": "log",
    "ln": "ln", "lim": "lim", "max": "max", "min": "min", "det": "det", "Tr": "Tr", "sinh": "sinh",
    "cosh": "cosh", "tanh": "tanh", "arctan": "arctan", "sup": "sup", "inf": "inf",
}
DROP_CMD = {"mathrm", "mathbf", "mathit", "mathcal", "mathscr", "mathbb", "mathsf", "mathtt", "boldsymbol",
            "bm", "text", "textrm", "textit", "textbf", "mbox", "operatorname", "rm", "bf", "it", "cal",
            "left", "right", "big", "Big", "bigg", "Bigg", "bigl", "bigr", "Bigl", "Bigr", "displaystyle",
            "textstyle", "scriptstyle", "limits", "nolimits", "quad", "qquad", "hat", "bar", "tilde",
            "vec", "dot", "ddot", "overline", "underline", "widehat", "widetilde", "overrightarrow",
            "mathring", "check", "breve", "acute", "grave", "begin", "end", "label", "tag", "nonumber",
            "notag", "hspace", "vspace", "phantom", "hfill", "stackrel", "overset", "underset", "mathop"}
ACCENT_TEXT = {"'": "\u0301", "`": "\u0300", "^": "\u0302", '"': "\u0308", "~": "\u0303", "c": "\u0327"}


def _group(s, i):
    """The braced group starting at s[i] == '{': (content, index after it)."""
    depth = 0
    for k in range(i, len(s)):
        depth += s[k] == "{"
        depth -= s[k] == "}"
        if depth == 0:
            return s[i + 1:k], k + 1
    return s[i + 1:], len(s)


def _arg(s, i):
    """One argument at s[i] (skipping spaces): a braced group or one character/command."""
    while i < len(s) and s[i] == " ":
        i += 1
    if i < len(s) and s[i] == "{":
        return _group(s, i)
    if i < len(s) and s[i] == "\\":
        m = re.match(r"\\([A-Za-z]+|.)", s[i:])
        return s[i:i + m.end()], i + m.end()
    return s[i:i + 1], i + 1


def latex_flat(s: str) -> str:
    """A formula's LaTeX as the flat text the truth writes."""
    s = re.sub(r"\\(begin|end)\{[^}]*\}(\{[^}]*\})?", " ", s)
    out, i = [], 0
    while i < len(s):
        c = s[i]
        if c == "\\":
            m = re.match(r"\\([A-Za-z]+|.)", s[i:])
            name = m.group(1)
            i += m.end()
            if name == "frac" or name in ("dfrac", "tfrac"):
                a, i = _arg(s, i)
                b, i = _arg(s, i)
                out.append(latex_flat(a) + "/" + latex_flat(b))
            elif name == "sqrt":
                if i < len(s) and s[i] == "[":
                    j = s.find("]", i)
                    i = j + 1 if j > 0 else i
                a, i = _arg(s, i)
                out.append("√" + latex_flat(a))
            elif name in GREEK:
                out.append(GREEK[name])
            elif name in SYMBOLS:
                out.append(SYMBOLS[name])
            elif name in (",", ";", ":", "!", " "):
                out.append(" " if name == " " else "")
            elif name == "\\":
                out.append(" ")
            elif name in ACCENT_TEXT and name not in ("c",):
                a, i = _arg(s, i)
                out.append(latex_flat(a))
            elif name in DROP_CMD:
                pass  # the argument, if any, is read on as plain text
            else:
                pass  # an unknown command: its name dropped
        elif c in "{}^_&":
            i += 1
        elif c == "~":
            out.append(" ")
            i += 1
        elif c == "$":
            i += 1
        else:
            out.append(c)
            i += 1
    return re.sub(r"\s+", " ", "".join(out)).strip()


MATH = re.compile(r"\$\$(.+?)\$\$|\\\[(.+?)\\\]|\\\((.+?)\\\)|(?<!\\)\$(.+?)(?<!\\)\$", re.S)


def md_flat(t: str) -> str:
    """Markdown/HTML/DocTags text as plain lines, its formulas flattened."""
    t = re.sub(r"<loc_\d+>", "", t)
    t = re.sub(r"<nl>|<br\s*/?>|</p>|</tr>|</li>|</h\d>|</div>", "\n", t)
    t = re.sub(r"</?(fcel|ecel|lcel|ucel|xcel|ched|rhed|srow)>", " ", t)
    t = re.sub(r"<[^<>\n]{1,80}>", "", t)  # other tags, HTML and DocTags
    t = html.unescape(t)
    t = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", t)  # images
    t = re.sub(r"```[a-zA-Z]*", "", t)
    t = MATH.sub(lambda m: " " + latex_flat(next(g for g in m.groups() if g is not None)) + " ", t)
    lines = []
    for line in t.splitlines():
        line = line.strip()
        if re.fullmatch(r"\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)*\|?", line):
            continue  # a table rule
        if line.startswith("|"):
            line = " ".join(c.strip() for c in line.strip("|").split("|"))
        line = re.sub(r"^#{1,6}\s+", "", line)
        line = re.sub(r"^[-*+]\s+(?=\S)", "", line)
        line = re.sub(r"\*\*(.+?)\*\*|__(.+?)__", lambda m: m.group(1) or m.group(2), line)
        line = re.sub(r"(?<![\w*])\*(?!\s)(.+?)(?<!\s)\*(?![\w*])", r"\1", line)
        if "\\" in line:  # a bare formula line (a region read as LaTeX without delimiters)
            line = latex_flat(line)
        line = re.sub(r"\s+", " ", line).strip()
        if line:
            lines.append(line)
    return "\n".join(lines)


def page_text(rec: dict) -> str:
    if rec.get("regions") is not None:
        parts = []
        for r in rec["regions"]:
            txt = r.get("text") or ""
            parts.append(latex_flat(txt) if r.get("cls") in ("display_formula", "formula") and "\\" in txt else md_flat(txt))
        return "\n".join(p for p in parts if p)
    raw = rec.get("text") or ""
    s = raw.strip()
    if s.startswith("[") or s.startswith("```json"):
        try:
            blocks = json.loads(re.sub(r"^```json|```$", "", s).strip())
            parts = []
            for b in blocks:
                if str(b.get("category", "")).lower() in ("picture", "image"):
                    continue
                txt = b.get("text") or ""
                parts.append(latex_flat(txt) if str(b.get("category", "")).lower() == "formula" else md_flat(txt))
            return "\n".join(p for p in parts if p)
        except (ValueError, AttributeError):
            pass
    return md_flat(raw)


def main():
    root = S2
    engines = sys.argv[1:] or sorted(p.name for p in (root / "vlm_raw").iterdir() if p.is_dir())
    for e in engines:
        n = 0
        out = root / "out" / f"vlm-{e}"
        out.mkdir(parents=True, exist_ok=True)
        for f in sorted((root / "vlm_raw" / e).glob("*.json")):
            rec = json.loads(f.read_text(encoding="utf-8"))
            lines = [{"text": line} for line in page_text(rec).splitlines()]
            (out / f.name).write_text(json.dumps({"lines": lines, "time": rec.get("seconds")}, ensure_ascii=False), encoding="utf-8")
            n += 1
        print(e, n, "pages")


if __name__ == "__main__":
    main()
