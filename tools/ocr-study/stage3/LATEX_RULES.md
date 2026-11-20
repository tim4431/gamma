# Transcribing display equations to LaTeX (OCR study, Stage 3)

You write the LaTeX of scanned display equations, as ground truth for
scoring formula readers. Write what is PRINTED, as a careful physicist
typing the equation would: the same symbols, sub- and superscripts,
fractions, brackets and layout — not a corrected or simplified equation,
and never anything that is not on the page.

## Output

One file per equation: `<folder>/<id>.tex` (the folder you are given, such as
`$OCR_STUDY_DIR/stage3/gtA/`) holding only the math (no
`$`, no `\[ \]`, no equation number, no `\label`). One equation per file,
as named by its crop `<id>.png`.

## Conventions (so readings of the same print agree)

- Plain LaTeX with amsmath: `\frac`, `\sqrt`, `^`, `_`, `\left( … \right)`
  for brackets that grow with their contents (or plain brackets: both
  score the same), `\begin{pmatrix}`/`bmatrix`/`vmatrix` for matrices,
  `\begin{cases}` for braces of cases, `\\` between printed rows of a
  display with several rows (aligned rows: `&` before the relation sign).
- Greek and symbols as their commands (`\alpha`, `\Omega`, `\hbar`,
  `\partial`, `\nabla`, `\int`, `\sum`, `\prod`, `\langle`, `\rangle`,
  `\dagger`, `\otimes`, `\approx`, `\simeq`, `\propto`, `\infty`, `\pm`).
  Upright multi-letter names: `\exp`, `\sin`, `\cos`, `\ln`, `\log`,
  `\det`, `\mathrm{Re}`, `\mathrm{Im}`, `\mathrm{Tr}`; an upright subscript
  word as `\mathrm{eff}`.
- Bold vectors `\mathbf{v}`; calligraphic `\mathcal{D}`; script `\mathscr`
  only if clearly script; accents `\hat`, `\tilde`, `\bar`, `\dot`, `\ddot`,
  `\vec` as printed.
- Primes as `'`; dots as `\cdot` when centered, `\ldots`/`\cdots` for
  ellipses as printed.
- A printing error or an unusual symbol stays as printed. If a glyph is
  truly unreadable (damage), write your best reading and say so in the
  notes file.
- No spacing commands, no `\displaystyle`, no colour or size commands.

## Checking

Render your LaTeX and compare it with the crop:
`python -I tools/ocr-study/stage3/render_latex.py <out.png> < <file.tex>`
(the plain Python environment, with `OCR_STUDY_PDFLATEX` set if pdflatex is
not on PATH)
prints ok (and writes the PNG) or failed. Fix what does not compile; look
at the PNG beside the crop and fix any symbol that differs. Look at the
crop at full size (Read it again if a sub/superscript is small).

## Notes

`<folder>/notes.txt`: one line per doubt (`<id>: …`), and any crop that is
not one complete display equation (cut off, two equations, text inside):
say so, transcribe what is there.
