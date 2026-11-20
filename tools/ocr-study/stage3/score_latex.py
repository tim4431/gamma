"""Score recognized LaTeX against ground truth.

  python -I score_latex.py --gt <dir of <id>.tex> --pred <dir of <id>.json {latex}> [--render <cache dir>] [--ids a,b]

Measures per equation and overall:
- ned: normalized token edit distance (edit distance between the token
  sequences after normalize(), over the truth's length; 0 is perfect).
- exact: token sequences equal after normalize().
- vis: the two renders compared (render_latex, ink cropped, the prediction
  scaled to the truth's size): ink IoU, 1 is identical; None when either
  does not compile (a prediction that does not compile scores 0).
normalize() removes what does not change the printed formula: sizing
(\\left \\right \\big…), spacing, \\displaystyle, \\limits, font wrappers
for upright and text (\\mathrm, \\text, \\operatorname, \\rm, \\it,
\\mathit), braces around a single token, and unifies synonyms (\\dfrac →
\\frac, \\le → \\leq, \\to → \\rightarrow, ^{\\prime} → ')."""

import argparse
import json
import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

TOKEN = re.compile(r"\\[a-zA-Z]+\*?|\\.|[^\s]")
DROP = {r"\left", r"\right", r"\big", r"\Big", r"\bigg", r"\Bigg", r"\bigl", r"\bigr", r"\Bigl", r"\Bigr",
        r"\biggl", r"\biggr", r"\Biggl", r"\Biggr", r"\displaystyle", r"\textstyle", r"\scriptstyle",
        r"\limits", r"\nolimits", r"\,", r"\;", r"\:", r"\!", r"\quad", r"\qquad", r"\ ", "~", r"\nonumber",
        r"\notag", r"\hfill", r"\hspace", r"\vspace", r"\mathstrut", r"\phantom"}
UNWRAP = {r"\mathrm", r"\text", r"\textrm", r"\operatorname", r"\mathit", r"\textit", r"\rm", r"\it", r"\mbox",
          r"\mathnormal"}
SYN = {r"\dfrac": r"\frac", r"\tfrac": r"\frac", r"\le": r"\leq", r"\ge": r"\geq", r"\ne": r"\neq",
       r"\to": r"\rightarrow", r"\lbrace": r"\{", r"\rbrace": r"\}", r"\lvert": "|", r"\rvert": "|",
       r"\vert": "|", r"\boldsymbol": r"\mathbf", r"\bm": r"\mathbf", r"\bf": r"\mathbf", r"\dots": r"\ldots",
       r"\cdots": r"\cdots", r"\lt": "<", r"\gt": ">", r"\varepsilon": r"\epsilon", r"\vartheta": r"\theta",
       r"\varphi": r"\phi", r"\overrightarrow": r"\vec", r"\cal": r"\mathcal", r"\mathbit": r"\mathbf",
       r"\bmod": r"\mod"}


def _close(out: list[str], i: int) -> int:
    """Index of the brace closing the one at i (len(out) if unbalanced)."""
    depth = 0
    for k in range(i, len(out)):
        depth += out[k] == "{"
        depth -= out[k] == "}"
        if depth == 0:
            return k
    return len(out)


def _switches(out: list[str]) -> list[str]:
    """Plain TeX's font switch over a group as the command over its argument:
    {\\bf AB} -> \\mathbf{AB} (\\bf and \\cal arrive as \\mathbf and \\mathcal)."""
    i = 0
    while i + 2 < len(out):
        if out[i] == "{" and out[i + 1] in (r"\mathbf", r"\mathcal") and out[i + 2] != "{":
            j = _close(out, i)
            out = out[:i] + [out[i + 1], "{"] + out[i + 2:j] + ["}"] + out[j + 1:]
        i += 1
    return out


def _over(out: list[str]) -> list[str]:
    """Plain TeX's {a \\over b} as \\frac{a}{b}; at the top level the whole
    sequence (or row, between \\\\) is the fraction."""
    while r"\over" in out:
        k = out.index(r"\over")
        depth, a = 0, -1
        for i in range(k - 1, -1, -1):
            if out[i] == "}":
                depth += 1
            elif out[i] == "{":
                if depth == 0:
                    a = i
                    break
                depth -= 1
            elif out[i] == "\\\\" and depth == 0:
                a = i
                break
        b = _close(out, a) if a >= 0 and out[a] == "{" else next(
            (i for i in range(k + 1, len(out)) if out[i] == "\\\\"), len(out))
        num, den = out[a + 1:k], out[k + 1:b]
        keep_left = out[:a + 1] if a >= 0 and out[a] == "\\\\" else out[:max(a, 0)]
        keep_right = out[b + 1:] if b < len(out) and out[b] == "}" else out[b:]
        out = keep_left + [r"\frac", "{"] + num + ["}", "{"] + den + ["}"] + keep_right
    return out


def tokens(latex: str) -> list[str]:
    s = latex.strip()
    for a, b in (("\\[", "\\]"), ("$$", "$$"), ("$", "$")):
        if s.startswith(a) and s.endswith(b) and len(s) > len(a) + len(b):
            s = s[len(a):-len(b)]
    s = re.sub(r"\\begin\{(equation|equation\*|align|align\*|aligned|gathered|split|eqnarray\*?|displaymath)\}", " ", s)
    s = re.sub(r"\\end\{(equation|equation\*|align|align\*|aligned|gathered|split|eqnarray\*?|displaymath)\}", " ", s)
    s = re.sub(r"\\(label|tag)\{[^}]*\}", " ", s)
    # an array is layout (its column spec too); a matrix environment is its delimiters, so
    # \left( \begin{array}{c} u \end{array} \right) and \begin{pmatrix} u \end{pmatrix} agree
    s = re.sub(r"\\begin\{array\}\s*\{[^{}]*\}", " ", s)
    s = s.replace(r"\end{array}", " ")
    for env, (o, c) in {"pmatrix": ("(", ")"), "bmatrix": ("[", "]"), "Bmatrix": (r"\{", r"\}"),
                        "vmatrix": ("|", "|"), "Vmatrix": (r"\|", r"\|"), "matrix": ("", ""),
                        "cases": (r"\{", ""), "smallmatrix": ("", "")}.items():
        s = s.replace(rf"\begin{{{env}}}", f" {o} ").replace(rf"\end{{{env}}}", f" {c} ")
    s = s.replace("&", " ")
    toks = TOKEN.findall(s)
    out = []
    i = 0
    while i < len(toks):
        t = SYN.get(toks[i], toks[i])
        if t in DROP:
            i += 1
            # \hspace{..}: drop its argument too
            if toks[i - 1] in (r"\hspace", r"\vspace", r"\phantom") and i < len(toks) and toks[i] == "{":
                depth = 0
                while i < len(toks):
                    depth += toks[i] == "{"
                    depth -= toks[i] == "}"
                    i += 1
                    if depth == 0:
                        break
            continue
        if t in UNWRAP:
            i += 1
            continue
        out.append(t)
        i += 1
    out = _switches(out)
    out = _over(out)
    # ^{\prime} and ^\prime -> '
    s2 = " ".join(out).replace("^ { \\prime }", "'").replace("^ \\prime", "'")
    out = s2.split(" ") if s2 else []
    # braces around one token, repeatedly
    changed = True
    while changed:
        changed = False
        res = []
        i = 0
        while i < len(out):
            if out[i] == "{" and i + 2 < len(out) and out[i + 2] == "}" and out[i + 1] not in "{}":
                res.append(out[i + 1])
                i += 3
                changed = True
            else:
                res.append(out[i])
                i += 1
        out = res
    joined = " ".join(out)
    while "{ }" in joined:  # empty groups, nested ones too ({{}})
        joined = joined.replace("{ }", " ").replace("  ", " ")
    out = [t for t in joined.split(" ") if t]
    # a bare group, one that is no argument (after no command, script, closing brace or
    # bracket): {=y} -> = y, as some readers brace every array cell
    while True:
        match, stack = {}, []
        for k, t in enumerate(out):
            if t == "{":
                stack.append(k)
            elif t == "}" and stack:
                match[stack.pop()] = k
        bare = next((i for i in sorted(match) if i == 0 or not (out[i - 1] in ("_", "^", "}", "]")
                                                                 or out[i - 1].startswith("\\") and out[i - 1][1:].isalpha())), None)
        if bare is None:
            break
        out = [t for k, t in enumerate(out) if k not in (bare, match[bare])]
    # a pair of braces wrapping exactly one braced group: {{x y}} -> {x y}
    while True:
        match, stack = {}, []
        for k, t in enumerate(out):
            if t == "{":
                stack.append(k)
            elif t == "}" and stack:
                match[stack.pop()] = k
        drop = next(((i, match[i]) for i in match
                     if out[i + 1:i + 2] == ["{"] and match.get(i + 1) == match[i] - 1), None)
        if not drop:
            return _scripts(out)
        out = [t for k, t in enumerate(out) if k not in drop]


def _scripts(out: list[str]) -> list[str]:
    """A base's superscript before its subscript as the other way round:
    x^2_M -> x_M^2 (TeX prints them the same)."""
    def arg_end(i):  # the end (exclusive) of the script argument starting at i
        if i < len(out) and out[i] == "{":
            return _close(out, i) + 1
        return i + 1
    i = 0
    while i < len(out):
        if out[i] == "^":
            e1 = arg_end(i + 1)
            if e1 < len(out) and out[e1] == "_":
                e2 = arg_end(e1 + 1)
                out = out[:i] + out[e1:e2] + out[i:e1] + out[e2:]
        i += 1
    return out


def edit(a: list[str], b: list[str]) -> int:
    prev = list(range(len(b) + 1))
    for i, x in enumerate(a, 1):
        cur = [i] + [0] * len(b)
        for j, y in enumerate(b, 1):
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (x != y))
        prev = cur
    return prev[-1]


def ink(png):
    import numpy as np
    from PIL import Image
    a = np.asarray(Image.open(png).convert("L")) < 128
    rows, cols = np.where(a.any(axis=1))[0], np.where(a.any(axis=0))[0]
    if not len(rows):
        return None
    return a[rows[0]:rows[-1] + 1, cols[0]:cols[-1] + 1]


def visual(truth_png, pred_png):
    import numpy as np
    from PIL import Image
    t, p = ink(truth_png), ink(pred_png)
    if t is None or p is None:
        return 0.0
    p = np.asarray(Image.fromarray(p.astype("uint8") * 255).resize((t.shape[1], t.shape[0]), Image.BILINEAR)) > 96
    # thicken both a little so a one-pixel shift is not a miss
    def grow(m):
        g = m.copy()
        g[1:, :] |= m[:-1, :]
        g[:-1, :] |= m[1:, :]
        g[:, 1:] |= m[:, :-1]
        g[:, :-1] |= m[:, 1:]
        return g
    t2, p2 = grow(t), grow(p)
    inter = (t2 & p2).sum()
    union = (t2 | p2).sum()
    return float(inter / union) if union else 0.0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--gt", required=True)
    ap.add_argument("--pred", required=True)
    ap.add_argument("--render", default="")
    ap.add_argument("--ids", default="")
    ap.add_argument("--json", default="")
    a = ap.parse_args()
    gt_dir, pred_dir = pathlib.Path(a.gt), pathlib.Path(a.pred)
    ids = [i for i in a.ids.split(",") if i] or sorted(p.stem for p in gt_dir.glob("*.tex"))
    rows = []
    if a.render:
        from render_latex import render
        cache = pathlib.Path(a.render)
        cache.mkdir(parents=True, exist_ok=True)
        # renders prerender.py found not to compile
        failed = set((cache / "_failed.txt").read_text(encoding="utf-8").split()) if (cache / "_failed.txt").exists() else set()
    for i in ids:
        truth = (gt_dir / f"{i}.tex").read_text(encoding="utf-8")
        pf = pred_dir / f"{i}.json"
        pred = json.loads(pf.read_text(encoding="utf-8")).get("latex", "") if pf.exists() else ""
        tt, pt = tokens(truth), tokens(pred or "")
        ned = edit(tt, pt) / max(1, len(tt))
        row = {"id": i, "ned": round(min(ned, 1.0), 4), "exact": tt == pt, "truth_tokens": len(tt), "missing": not pf.exists()}
        if a.render:
            tp = cache / f"truth-{i}.png"
            if not tp.exists():
                render(truth, str(tp))
            pp = cache / f"{pred_dir.name}-{i}.png"
            ok = pp.exists() or (pp.name not in failed and bool(pred) and render(pred, str(pp)))
            row["compiles"] = bool(ok)
            row["vis"] = round(visual(tp, pp), 4) if ok and tp.exists() else 0.0
        rows.append(row)
    n = len(rows)
    summary = {"n": n, "ned_mean": round(sum(r["ned"] for r in rows) / max(1, n), 4),
               "exact": round(sum(r["exact"] for r in rows) / max(1, n), 4),
               "missing": sum(r["missing"] for r in rows)}
    if a.render:
        summary["compiles"] = round(sum(r["compiles"] for r in rows) / max(1, n), 4)
        summary["vis_mean"] = round(sum(r["vis"] for r in rows) / max(1, n), 4)
    print(json.dumps(summary))
    if a.json:
        pathlib.Path(a.json).write_text(json.dumps({"summary": summary, "rows": rows}, indent=0), encoding="utf-8")


if __name__ == "__main__":
    main()
