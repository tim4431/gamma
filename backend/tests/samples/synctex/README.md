# SyncTeX samples

Builds of TeX Live 2024 for the SyncTeX reader's tests
(`backend/tests/test_synctex.py`, `test_feedback_sources.py`,
docs/dev/feedback.md "SyncTeX"), all made here by `build.py` in this folder:

    cd backend
    venv/Scripts/python.exe -I tests/samples/synctex/build.py [--compare]

with `latexmk`, `pdflatex`, `xelatex` and `lualatex` on PATH (`--compare`
also runs TeX Live's `synctex` on them). CI never runs it. The builds'
dates are fixed, so building again gives the same files, and the `Input`
lines of each `.synctex.gz` name the build directory as `/work/<name>`, as
a build in a container would.

| File | What it is |
|---|---|
| `truth/{pdftex,xetex,luatex}/main.synctex.gz` | The truth corpus: a two-column article of 947 words in which every word names its own file and line (`b17w3` is word 3 of line 17 of `body.tex`; `a` is `main.tex`, `c` `more.tex`), through paragraphs, `\emph`, inline math, footnotes, a list and a table, built with pdfLaTeX, XeLaTeX and LuaLaTeX (`-synctex=1`) |
| `truth/cases.json` | Per engine, `pages` and what MuPDF read off its PDF: `words`, `[page, [x0, y0, x1, y1], file, line]` for each word (bp from the page's top-left), and `lines`, `[page, rect, [[file, line], …]]` for each line of text with the source lines its words carry. The tests need no PDF |
| `paper/v1/`, `paper/v2/` | A one-column two-page paper (`main.tex` inputs `sections/method.tex`; one figure, `figures/fig1.pdf`, drawn with TikZ; one equation) built with pdfLaTeX: its sources, `main.pdf` and `main.synctex.gz`. v2 inserts three lines above the commented passage in `method.tex` and is built again; the figure is unchanged |
| `paper/places.json` | Per version, the commented places on page 1 as MuPDF finds them, as fractions of the page (`rect`, `rects`) and in bp (`bp`), with the source file and line each comes from: the passage (`line` to `end`), the figure (its `\includegraphics` line), the equation (its `\end{equation}` line) and the page number |
| `edges/*.synctex` | Hand-written plain snippets of the format's edges: `=` for a record's `v` (`equals`), `tag,line,column` links (`column`), `Input` lines inside the content (`input_inside`), two tags for one file (`two_tags`), LuaTeX's negative font kern carrying the shipout's tag (`negative_kern`), XeTeX's offsets and no `x` records (`xetex_offsets`), a `Post scriptum` overriding the magnification and the offsets as `synctex update -m 2 -x 1in -y 0.5in` writes it (`post_scriptum`), no `Content:` (`no_content`) and a sheet the file ends inside (`truncated`) |

Measured with `build.py --compare` (`synctex` 1.5 of TeX Live 2024): at the
words' centres the reader answers 928 of the 947 exactly and the other 19
within one line, for each engine; `synctex edit` answers 870 (pdfTeX), 868
(XeTeX) and 337 (LuaTeX) exactly. On the paper the two agree on the figure,
the equation and the page number. Where `synctex edit` answers an edge
otherwise, the test says so.
