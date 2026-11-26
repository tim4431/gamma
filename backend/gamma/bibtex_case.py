"""The capitals of a title that a sentence-case style must keep.

A style that sets titles in sentence case (plainnat, biblatex's) lowercases
every letter of a title that braces do not protect. ``protect_case``
braces the words whose capitals must stay, by Zotero's BibLaTeX rule, in
the fields such a style sets (``CASE_FIELDS``). ``bibtex_format`` applies
it in both dialects. Pure, standard library only.
"""

import re

from .bibtex_latex import closing_brace

# The fields a style may set in sentence case, whose capitals a word keeps
# only in braces.
CASE_FIELDS = frozenset({"title", "booktitle", "shorttitle", "series"})
# A command's name, or the one character a control symbol (`\&`) escapes.
_COMMAND_RE = re.compile(r"\\(?:[A-Za-z]+|.)", re.S)


def _special_upper(group: str) -> bool:
    """Whether a special character (`{\\"U}`, `{\\c{C}}`, `{\\L}`) is a
    capital: an accent command and its letter, or a letter's own command."""
    return any(c.isupper() for c in re.sub(r"\\[A-Za-z]+(?=\{)|\\[^A-Za-z]", "", group))


def protect_case(latex: str) -> str:
    """``latex`` (a value as ``bibtex_latex.latex_text`` writes it) with
    every word that has a capital after its first character braced
    ("{GI}-type", "{KiDS}", "{eBOSS}", "{LLM}s"), as Zotero's BibLaTeX
    export protects them, so a style that sets titles in sentence case
    keeps them; a word is letters and digits, so one of both with a
    capital is braced too ("{Y3}", "{3D}", "{GNN2R}", which the BibLaTeX
    rule leaves to be lowercased).
    Only the top level is read: what is braced already, math and command
    names stay as they are, so a value protected twice reads the same; an
    accented letter (`{\\'E}`) is a letter of its word."""
    # (text, unit: "A" a capital, "a" a lowercase letter, "9" a digit, ""
    # none; and, for none, whether it ends as a word does: `\_`, a command)
    tokens, i, n = [], 0, len(latex)
    while i < n:
        c = latex[i]
        if c == "{":
            j = closing_brace(latex, i)
            j = n - 1 if j < 0 else j
            group = latex[i:j + 1]
            special = group.startswith("{\\")
            tokens.append((group, ("A" if _special_upper(group) else "a") if special else "", False))
            i = j + 1
        elif c == "\\":
            command = _COMMAND_RE.match(latex, i).group(0)
            tokens.append((command, "", command[-1:].isalnum() or command[-1:] == "_"))
            i += len(command)
        elif c == "$":
            j = latex.find("$", i + 1)
            while j > 0 and latex[j - 1] == "\\":
                j = latex.find("$", j + 1)
            j = n - 1 if j < 0 else j
            tokens.append((latex[i:j + 1], "", False))
            i = j + 1
        else:
            unit = ("A" if c.isupper() else "a") if c.isalpha() else "9" if c.isdigit() else ""
            tokens.append((c, unit, c == "_"))
            i += 1
    out, k = [], 0
    while k < len(tokens):
        if not tokens[k][1]:
            out.append(tokens[k][0])
            k += 1
            continue
        end = k
        while end < len(tokens) and tokens[end][1]:
            end += 1
        units = "".join(t[1] for t in tokens[k:end])
        word = "".join(t[0] for t in tokens[k:end])
        cased = "A" in units[1:] or ("A" in units and "9" in units)
        out.append("{" + word + "}" if cased and not (k and tokens[k - 1][2]) else word)
        k = end
    return "".join(out)
