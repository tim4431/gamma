"""Build the SyncTeX samples of this folder (README.md) with TeX Live and
MuPDF, and compare gamma/feedback/synctex.py with TeX Live's ``synctex``.

    cd backend
    venv/Scripts/python.exe -I tests/samples/synctex/build.py [--compare] [--work DIR]

TeX Live's ``latexmk``, ``pdflatex``, ``xelatex``, ``lualatex`` (and, for
``--compare``, ``synctex``) must be on PATH. The builds run in ``--work``
(a new temporary directory by default); the Input lines of each
``.synctex.gz`` kept here name that directory ``/work/<name>`` instead, as
a build in a container would. Dates are fixed (SOURCE_DATE_EPOCH), so a
build is the same file each time. CI never runs this: the tests read what
it wrote. ``--compare`` prints how the reader and ``synctex edit`` /
``synctex view`` answer the truth corpus, word by word.
"""

import argparse
import collections
import gzip
import json
import os
import random
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[2]))

import pymupdf  # noqa: E402

from gamma.feedback import synctex  # noqa: E402

ENGINES = {"pdftex": "-pdf", "xetex": "-xelatex", "luatex": "-lualatex"}
FILES = {"a": "main.tex", "b": "body.tex", "c": "more.tex"}
WORD = re.compile(r"([abc])(\d+)[wemf]\d+")
ENV = {**os.environ, "SOURCE_DATE_EPOCH": "1767225600", "FORCE_SOURCE_DATE": "1"}

FIGURE = r"""\documentclass[tikz,border=2pt]{standalone}
\pdfcompresslevel=9
\pdfobjcompresslevel=2
\begin{document}
\begin{tikzpicture}[x=0.6cm,y=0.5cm]
  \draw[->] (0,0) -- (10.5,0) node[below left] {epoch};
  \draw[->] (0,0) -- (0,6.5) node[above right] {loss};
  \draw[thick] plot[smooth] coordinates {(0.5,6) (1.5,4.2) (3,3) (5,2.1) (7,1.6) (10,1.3)};
  \draw[dashed] plot[smooth] coordinates {(0.5,6.2) (1.5,4.8) (3,3.9) (5,3.4) (7,3.3) (10,3.4)};
  \draw[thick] (7,6) -- (8,6) node[right] {train};
  \draw[dashed] (7,5.2) -- (8,5.2) node[right] {test};
\end{tikzpicture}
\end{document}
"""

MAIN = r"""\documentclass[11pt]{article}
\usepackage[margin=1.25in]{geometry}
\usepackage{graphicx}
\usepackage{amsmath}
\pdfcompresslevel=9
\pdfobjcompresslevel=2
\begin{document}
\begin{center}
  {\large\bfseries Weight Decay on a Small Data Set}\\[1ex]
  A. Author
\end{center}

\section{Introduction}
A model with many parameters fits a small training set too well: the
training loss keeps falling while the loss on held-out data turns and
rises again. Weight decay is the oldest remedy, and the cheapest one to
try. This note measures how much it helps on a data set of a few hundred
examples, and how the penalty's weight should be chosen.

\begin{figure}[h]
  \centering
  \includegraphics[width=0.55\linewidth]{figures/fig1.pdf}
  \caption{Training and test loss per epoch, without weight decay.}
  \label{fig:loss}
\end{figure}

\input{sections/method}

\section{Results}
Figure~\ref{fig:loss} shows the run without the penalty: after the
fifth epoch the test loss no longer follows the training loss down.
With the penalty of equation~\eqref{eq:loss} the two curves stay
together, and the test loss ends lower by about a fifth.

The weight was chosen on a validation split, from a grid of six values
between one thousandth and one tenth. The best value was the same for
every seed we tried, and the results changed little when it was halved
or doubled, so the choice is not delicate.

Larger data sets need a smaller weight, and the benefit shrinks as the
data set grows: with ten thousand examples the two runs end within a
few hundredths of each other. The penalty is therefore worth its cost
mainly where data are scarce, which is the case this note set out to
study.

A last run kept the penalty but stopped training at the epoch where the
validation loss was lowest. Early stopping alone recovered most of the
gain, and the two together did slightly better than either one, which
agrees with what has been reported for larger models.

\section{Conclusion}
Weight decay is a simple and reliable way to keep a small model from
fitting the noise in a small training set. Its weight can be chosen
coarsely, and it combines well with early stopping. A natural next step
is to see whether the same holds for models whose parameters are
shared across layers, where the penalty acts on fewer weights.
\end{document}
"""

METHOD = r"""\section{Method}
We fit a small network to the training set by gradient descent.
{inserted}The penalty keeps the weights small, which matters when the
training set is small compared with the number of parameters,
as it is in every experiment reported below.
The loss is defined as
\begin{equation}
  L(\theta) = E(\theta)
  + \lambda R(\theta) ,
  \label{eq:loss}
\end{equation}
where $E$ is the squared error of the fit, $R$ the squared norm of the
weights, and $\lambda$ weighs the penalty against the fit.
"""

INSERTED = ("Each step uses a batch of thirty-two examples drawn at random,\n"
            "and the learning rate is halved whenever the test loss stops\n"
            "falling for five epochs in a row.\n")
PASSAGE = ("The penalty keeps the weights small, which matters when the training set is small compared with the "
           "number of parameters, as it is in every experiment reported below.")


def truth_body(rng, f):
    """A file of the truth corpus: every word names its own file and line
    (``b17w3``: word 3 of line 17 of body.tex), through paragraphs,
    ``\\emph``, inline math, footnotes, a list and a table."""
    out = []
    ln = 0

    def add(text):
        nonlocal ln
        for t in text.split("\n"):
            ln += 1
            out.append(t)

    def words(line, n):
        return " ".join(f"{f}{line}w{k}" for k in range(1, n + 1))

    add("\\section{%s}" % words(1, 3))
    for p in range(10):
        for _ in range(rng.randint(3, 7)):
            n = rng.randint(4, 12)
            kind = rng.random()
            line_no = ln + 1
            text = words(line_no, n)
            if kind < 0.1:
                text += " \\emph{%s}" % words(line_no, 2).replace("w", "e")
            elif kind < 0.2:
                text += " $x_{%d}$ %s" % (line_no, words(line_no, 2).replace("w", "m"))
            elif kind < 0.25:
                text += "\\footnote{%s}" % words(line_no, 5).replace("w", "f")
            add(text)
        add("")
        if p == 3:
            add("\\begin{itemize}")
            for _ in range(3):
                add("  \\item " + words(ln + 1, rng.randint(3, 14)))
            add("\\end{itemize}")
        if p == 6:
            add("\\begin{table}[h]\n\\centering\n\\begin{tabular}{ll}")
            for _ in range(3):
                n = ln + 1
                add(f"{f}{n}w1 & {f}{n}w2 \\\\")
            add("\\end{tabular}")
            add("\\caption{%s}" % words(ln + 1, 6))
            add("\\end{table}")
    return "\n".join(out) + "\n"


def write_truth(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    rng = random.Random(3)
    (root / "body.tex").write_text(truth_body(rng, "b"), encoding="utf-8", newline="\n")
    (root / "more.tex").write_text(truth_body(rng, "c"), encoding="utf-8", newline="\n")
    (root / "main.tex").write_text(
        "\\documentclass[11pt,twocolumn]{article}\n\\usepackage[margin=0.8in]{geometry}\n"
        "\\begin{document}\n\\input{body}\n\\input{more}\n\\end{document}\n", encoding="utf-8", newline="\n")


def latexmk(root: Path, mode: str) -> None:
    run = subprocess.run(["latexmk", mode, "-synctex=1", "-interaction=nonstopmode", "-halt-on-error", "-silent",
                          "main.tex"], cwd=root, env=ENV, capture_output=True, text=True, errors="replace")
    if run.returncode or not (root / "main.synctex.gz").is_file():
        sys.exit(f"latexmk {mode} failed in {root}:\n{run.stdout[-3000:]}")


def neutral(build: Path, name: str) -> bytes:
    """The build's ``main.synctex.gz`` with its Input lines naming the
    build directory ``/work/<name>``, gzipped with no date."""
    text = gzip.decompress((build / "main.synctex.gz").read_bytes())
    prefix = str(build).replace("\\", "/").rstrip("/") + "/"

    def swap(m):
        path = m.group(2).decode("utf-8")
        if path.lower().startswith(prefix.lower()):
            path = f"/work/{name}/" + path[len(prefix):]
        return b"\nInput:" + m.group(1) + b":" + path.encode("utf-8")
    return gzip.compress(re.sub(rb"\nInput:(\d+):([^\n]*)", swap, text), compresslevel=9, mtime=0)


def r2(rect, digits=2) -> list:
    return [round(v, digits) for v in rect]


def truth_cases(pdf: Path) -> dict:
    """``words``: ``[page, rect, file, line]`` per word, ``lines``: ``[page,
    rect, [[file, line], …]]`` per line of text MuPDF reads (bp, top-left)."""
    doc = pymupdf.open(pdf)
    words, lines = [], []
    for page in doc:
        for w in page.get_text("words"):
            m = WORD.search(w[4])
            if m:
                words.append([page.number + 1, r2(w[:4], 1), FILES[m.group(1)], int(m.group(2))])
        for block in page.get_text("dict")["blocks"]:
            for line in block.get("lines", ()):
                text = " ".join(sp["text"] for sp in line["spans"])
                want = sorted({(FILES[m.group(1)], int(m.group(2))) for m in WORD.finditer(text)})
                if want:
                    lines.append([page.number + 1, r2(line["bbox"], 1), [list(w) for w in want]])
    return {"pages": doc.page_count, "words": words, "lines": lines}


def build_truth(work: Path) -> dict:
    cases = {}
    for engine, mode in ENGINES.items():
        build = work / "truth" / engine
        write_truth(build)
        latexmk(build, mode)
        cases[engine] = truth_cases(build / "main.pdf")
        out = HERE / "truth" / engine
        out.mkdir(parents=True, exist_ok=True)
        (out / "main.synctex.gz").write_bytes(neutral(build, "truth"))
        print(f"truth/{engine}: {len(cases[engine]['words'])} words, {len(cases[engine]['lines'])} lines")
    with open(HERE / "truth" / "cases.json", "w", encoding="utf-8", newline="\n") as f:
        f.write("{\n")
        for n, (engine, c) in enumerate(cases.items()):
            f.write(f' "{engine}": {{"pages": {c["pages"]},\n  "words": [\n')
            f.write(",\n".join("   " + json.dumps(w, separators=(",", ":")) for w in c["words"]))
            f.write('\n  ],\n  "lines": [\n')
            f.write(",\n".join("   " + json.dumps(w, separators=(",", ":")) for w in c["lines"]))
            f.write("\n  ]}" + (",\n" if n < len(cases) - 1 else "\n"))
        f.write("}\n")
    return cases


def source_line(text: str, needle: str) -> int:
    return next(i for i, ln in enumerate(text.split("\n"), 1) if needle in ln)


def places(pdf: Path, main: str, method: str) -> dict:
    """Where the paper's commented places are on page 1, as fractions of
    the page and in bp, with the source lines they come from."""
    doc = pymupdf.open(pdf)
    page = doc[0]
    w, h = page.rect.width, page.rect.height

    def frac(rect):
        return [round(rect[0] / w, 4), round(rect[1] / h, 4), round(rect[2] / w, 4), round(rect[3] / h, 4)]

    quads = page.search_for(PASSAGE, quads=True)
    if not quads:
        sys.exit(f"{pdf}: the passage is not on page 1")
    passage = [tuple(q.rect) for q in quads]
    drawn = pymupdf.Rect()
    for d in page.get_drawings():
        drawn |= d["rect"]
    words = page.get_text("words")
    caption = {w[5] for w in words if w[4] == "Figure"}
    for word in words:
        if word[5] not in caption and pymupdf.Rect(word[:4]).intersects(drawn + (-15, -15, 15, 15)):
            drawn |= pymupdf.Rect(word[:4])
    number = page.search_for("(1)")
    if not number:
        sys.exit(f"{pdf}: the equation is not on page 1")
    number = max(number, key=lambda r: r.x1)           # the equation's number, flush right
    middle = (number.y0 + number.y1) / 2
    equation = pymupdf.Rect()
    for block in page.get_text("dict")["blocks"]:
        for line in block.get("lines", ()):
            if abs((line["bbox"][1] + line["bbox"][3]) / 2 - middle) < 14:
                equation |= pymupdf.Rect(line["bbox"])
    folio = max((wd for wd in page.get_text("words") if wd[4] == "1"), key=lambda wd: wd[1])
    return {
        "pages": doc.page_count, "width": round(w, 2), "height": round(h, 2),
        "passage": {"quote": PASSAGE, "rects": [frac(r) for r in passage], "bp": [r2(r) for r in passage],
                    "file": "sections/method.tex", "line": source_line(method, "The penalty keeps"),
                    "end": source_line(method, "as it is in every")},
        "figure": {"rect": frac(drawn), "bp": r2(drawn), "file": "main.tex",
                   "line": source_line(main, "\\includegraphics")},
        "equation": {"rect": frac(equation), "bp": r2(equation), "file": "sections/method.tex",
                     "line": source_line(method, "\\end{equation}")},
        "page_number": {"rect": frac(folio[:4]), "bp": r2(folio[:4])},
    }


def build_paper(work: Path) -> dict:
    figure = work / "figure"
    figure.mkdir(parents=True, exist_ok=True)
    (figure / "main.tex").write_text(FIGURE, encoding="utf-8", newline="\n")
    latexmk(figure, "-pdf")
    out = {}
    for version, inserted in (("v1", ""), ("v2", INSERTED)):
        build = work / version / "paper"
        method = METHOD.replace("{inserted}", inserted)
        (build / "sections").mkdir(parents=True, exist_ok=True)
        (build / "figures").mkdir(parents=True, exist_ok=True)
        (build / "main.tex").write_text(MAIN, encoding="utf-8", newline="\n")
        (build / "sections" / "method.tex").write_text(method, encoding="utf-8", newline="\n")
        shutil.copyfile(figure / "main.pdf", build / "figures" / "fig1.pdf")
        latexmk(build, "-pdf")
        dest = HERE / "paper" / version
        if dest.exists():
            shutil.rmtree(dest)
        for rel in ("main.tex", "sections/method.tex", "figures/fig1.pdf", "main.pdf"):
            (dest / rel).parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(build / rel, dest / rel)
        (dest / "main.synctex.gz").write_bytes(neutral(build, "paper"))
        out[version] = places(build / "main.pdf", MAIN, method)
        print(f"paper/{version}: main.pdf {(dest / 'main.pdf').stat().st_size} bytes, "
              f"{out[version]['pages']} pages")
    text = re.sub(r"\[[-\d.,\s]*\]", lambda m: json.dumps(json.loads(m.group(0))), json.dumps(out, indent=1))
    (HERE / "paper" / "places.json").write_text(text + "\n", encoding="utf-8", newline="\n")
    return out


# --- the comparison with TeX Live's synctex ----------------------------------------------

def cli_edit(pdf: Path, page: int, x: float, y: float):
    run = subprocess.run(["synctex", "edit", "-o", f"{page}:{x:.2f}:{y:.2f}:{pdf.name}"], cwd=pdf.parent,
                         capture_output=True, text=True, encoding="utf-8", errors="replace")
    first = run.stdout.split("Output:")[1:2]
    if not first:
        return None
    fields = dict(ln.split(":", 1) for ln in first[0].splitlines() if ":" in ln)
    return fields.get("Input", "").strip().replace("\\", "/").rsplit("/", 1)[-1], int(fields.get("Line", "0"))


def cli_view_pages(pdf: Path, name: str, line: int) -> set:
    run = subprocess.run(["synctex", "view", "-i", f"{line}:0:./{name}", "-o", pdf.name], cwd=pdf.parent,
                         capture_output=True, text=True, encoding="utf-8", errors="replace")
    return {int(p) for p in re.findall(r"^Page:(\d+)", run.stdout, re.M)}


def _score(tally, got, want):
    tally["exact" if got == want else "one off" if got and got[0] == want[0] and abs(got[1] - want[1]) <= 1
          else "wrong"] += 1


def compare(work: Path, cases: dict, papers: dict) -> None:
    for engine, c in cases.items():
        build = work / "truth" / engine
        syn = synctex.load(build / "main.synctex.gz")

        def name(tag):
            return syn.inputs.get(tag, "?").replace("\\", "/").rsplit("/", 1)[-1]
        mine, rect, theirs, agree = (collections.Counter() for _ in range(4))
        for page, r, file, line in c["words"]:
            want = (file, line)
            x, y = (r[0] + r[2]) / 2, (r[1] + r[3]) / 2
            a = syn.edit(page, x, y)
            a = (name(a[0]), a[1]) if a else None
            q = syn.query(page, [r], text=True)
            q = (name(q[0][0]), q[0][1]) if q else None
            b = cli_edit(build / "main.pdf", page, x, y)
            _score(mine, a, want)
            _score(rect, q, want)
            _score(theirs, b, want)
            agree["same" if a == b else "differ"] += 1
        print(f"{engine}: the reader's point {dict(mine)}, rect {dict(rect)}; synctex edit {dict(theirs)}; "
              f"the two {dict(agree)}")
        pages = collections.defaultdict(set)
        for page, _r, file, line in c["words"]:
            pages[(file, line)].add(page)
        views = collections.Counter()
        tags = {name(t): t for t in syn.inputs}
        for (file, line), on in sorted(pages.items()):
            got = {v[0] for v in syn.view(tags[file], line)}
            cli = cli_view_pages(build / "main.pdf", file, line)
            views["reader right" if got == on else "reader wrong"] += 1
            views["cli right" if cli == on else "cli wrong"] += 1
        print(f"{engine}: view, the pages a line's words are on: {dict(views)}")
    for version, p in papers.items():
        build = work / version / "paper"
        syn = synctex.load(build / "main.synctex.gz")
        for what in ("figure", "equation", "page_number"):
            r = p[what]["bp"]
            x, y = (r[0] + r[2]) / 2, (r[1] + r[3]) / 2
            q = syn.query(1, [r], text=False)
            mine = [(syn.inputs[t].replace("\\", "/").rsplit("/", 1)[-1], ln, round(s, 2)) for t, ln, s in q]
            print(f"paper/{version} {what}: the reader {mine}; synctex edit at its centre "
                  f"{cli_edit(build / 'main.pdf', 1, x, y)}")


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--compare", action="store_true")
    parser.add_argument("--work", type=Path)
    args = parser.parse_args()
    work = (args.work or Path(tempfile.mkdtemp(prefix="gamma-synctex-"))).resolve()
    cases = build_truth(work)
    papers = build_paper(work)
    if args.compare:
        compare(work, cases, papers)
    print(f"built in {work}")


if __name__ == "__main__":
    main()
