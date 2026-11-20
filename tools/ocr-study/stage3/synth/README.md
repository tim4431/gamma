# Synthetic scanned display equations with exact LaTeX ground truth

191 display equations from 8 arXiv physics papers (atomic physics / quantum
optics / condensed matter / nuclear theory, 1997–2013), each rendered alone in a
Times-like (newtx) font at the width of a two-column journal column, as a clean
300 dpi grey image and as a degraded bi-level "old scan". The LaTeX that was
rendered is the ground truth. Built for a study of formula recognition on old
scanned physics papers; not for redistribution beyond that study (see Licences).

## Layout

The scripts and this file are in `tools/ocr-study/stage3/synth/`; the set
itself is built under `$OCR_STUDY_DIR/stage3/synth/` and stays out of git.

```
$OCR_STUDY_DIR/stage3/synth/
  index.json             {"equations": [{id, paper, latex, clean_png, scan_png, width_px, height_px, gt}], "papers": [...]}
  gt/eqNNNN.json         one record per equation (fields below)
  clean/eqNNNN.png       8-bit grey, 300 dpi, cropped to the ink + 10 px
  scan/eqNNNN.png        1-bit bi-level, degraded, cropped to the ink + 10 px
  meta/papers.json       paper metadata (id, title, authors, licence, counts)
  meta/*.xml             arXiv OAI-PMH records the metadata came from
  dl/<id>/eprint.bin     the arXiv e-prints as downloaded (gzip/tar, read as data only)
  work/candidates.json   every extracted equation (1777) before sampling
  logs/stats.json        counts per paper; logs/compile_failures.json the skipped ones
  logs/check_rerender.json  the re-render check; logs/*.log the run output
tools/ocr-study/stage3/synth/
  README.md              this file
  extract.py, render.py, degrade.py, build.py, check.py
```

`gt/eqNNNN.json` fields: `id`, `paper` (arXiv id), `latex` (ground truth, as
rendered), `clean_png`, `scan_png` (paths relative to the set's folder), `width_px`,
`height_px` (the clean image), plus `scan_width_px`, `scan_height_px`,
`source_env` (equation / eqnarray / align / ...), `source_latex` (the row before
macro expansion), `candidate_id`, and `degradation` (all parameters, the seed
used, and the scan's ink relative to the clean image).

## How it was built

Run with the plain Python environment (`python -I <name>.py`, from this folder):
`extract.py`, then `build.py`, then `check.py`. pdflatex is `OCR_STUDY_PDFLATEX`.

1. **Sources.** e-prints from `https://export.arxiv.org/e-print/<id>`, metadata
   from arXiv OAI-PMH (`oaipmh.arxiv.org`), a few seconds between requests,
   saved as `dl/<id>/eprint.bin` and `meta/<id>.xml` (`/` in the id as `_`; no
   download script is kept).
   Archives are read in memory with `tarfile`; only `.tex` members are decoded;
   nothing is extracted or executed.
2. **Extraction** (`extract.py`). Comments stripped; environment shortcuts
   (`\def\be{\begin{equation}}` etc.) substituted; `equation(*)`, `displaymath`,
   `\[...\]`, `$$...$$` taken whole if they have no top-level `\\`;
   `align(*)`, `eqnarray(*)`, `gather(*)` split into rows at top-level `\\`,
   alignment `&` replaced by a space, rows that start with `&` (continuations)
   and rows followed by a continuation (incomplete heads) skipped.
   `\label`, `\tag`, `\nonumber`, `\notag`, `\xspace`, `\ensuremath` removed.
   The paper's own `\newcommand`/`\renewcommand`/`\providecommand`/`\def`/
   `\DeclareMathOperator` macros (with or without arguments, no optional
   argument) are expanded recursively; whitespace normalized; exact duplicates
   (ignoring whitespace) dropped across all papers. Macros still undefined after
   this (from the paper's packages or class, e.g. REVTeX `\case`, Kitaev's
   `\figbox`) make the equation fail to compile, and it is dropped.
3. **Rendering** (`render.py`). Each equation alone in
   `\documentclass[10pt]{article}` + `amsmath,amssymb` + `newtxtext,newtxmath`
   + `geometry` (text width 3.4 in), `\pagestyle{empty}`, inside
   `\begin{equation*}...\end{equation*}`; pdflatex (TeX Live 2024,
   `-halt-on-error -no-shell-escape`); PyMuPDF renders the page at 300 dpi grey;
   crop to pixels < 250 plus 10 px. Equations wider than 1.03 × 3.4 in were
   dropped (they would have been broken over lines in a two-column journal).
4. **Sampling** (`build.py`, seed `20261119`). 45 random candidates per paper
   compiled; of those that compiled and fit the column, 24 per paper kept,
   evenly spaced through the paper's width ranking (short to full width).
5. **Degradation** (`degrade.py`), seeded per equation: seed `1950 + n` for
   `eqNNNN` with number n (a redraw k uses `+ 100000·k`, see the guard):
   - ink spread (blend with a 3×3 min filter, 0.25–0.8) or thinning (3×3 max
     filter, 0.1–0.35) or neither (p = 0.35 / 0.25 / 0.40);
   - rotation uniform ±0.6°, bicubic;
   - Gaussian blur σ 0.6–1.0 px;
   - binarization at a threshold 0.50–0.66 (of white) with per-pixel Gaussian
     noise σ 0.03–0.09 and low-frequency exposure variation (amplitude 0–0.06);
   - for ~40 %: JBIG2/CCITT-like resolution loss — box-downsample to 50–70 %,
     keep a cell black only if 45–60 % of it is ink, upsample nearest (thin
     strokes break, edges go blocky);
   - crop to the ink + 10 px; 2–12 dust speckles (r 0.6–1.6 px) and 0–6
     white pinholes in the ink.
   - Legibility guard: if the scan keeps < 75 % or > 220 % of the clean image's
     black pixels, the equation is redrawn with the next seed (22 of 191 needed
     one or two redraws). The final seed is in `degradation.seed`.
6. **Checks** (`check.py`): all records consistent with their PNGs; 10 random
   ground-truth strings re-rendered from the JSON give pixel-identical clean
   PNGs; every scan PNG is reproduced exactly from its clean PNG and recorded
   seed. 10 random scans plus the thinnest and boldest ones were inspected by eye.

## Counts

| | |
|---|---|
| papers | 8 |
| display equations/rows extracted (after macro expansion and dedup) | 1777 |
| sampled for compilation (45 per paper) | 360 |
| compiled | 341 (19 failed: undefined `\figbox` ×12, `\case` ×6, `\tensor` ×1) |
| compiled but wider than the column | 26 |
| kept | 191 (24 per paper; 23 from cond-mat/0506438) |

Clean widths: 79–1061 px (median 595; 25 near full column width > 900 px);
LaTeX length 18–426 characters (median 97).

## Papers and licences

| arXiv id | first submitted | title | authors | licence on arXiv |
|---|---|---|---|---|
| cond-mat/9806038 | 1998-06 | Theory of Bose-Einstein condensation in trapped gases | F. Dalfovo, S. Giorgini, L. P. Pitaevskii, S. Stringari | none listed |
| nucl-th/9705009 | 1997-05 | Quantum Monte Carlo calculations of nuclei with A ≤ 7 | B. S. Pudliner, V. R. Pandharipande, J. Carlson, S. C. Pieper, R. B. Wiringa | none listed |
| cond-mat/0506438 | 2005-06 | Anyons in an exactly solved model and beyond | A. Kitaev | none listed |
| 0704.3011 | 2007-04 | Many-Body Physics with Ultracold Gases | I. Bloch, J. Dalibard, W. Zwerger | none listed |
| 0807.3358 | 2008-07 | Quantum interface between light and atomic ensembles | K. Hammerer, A. S. Sorensen, E. S. Polzik | arXiv non-exclusive distribution 1.0 |
| 0810.4729 | 2008-10 | Introduction to Quantum Noise, Measurement and Amplification | A. A. Clerk, M. H. Devoret, S. M. Girvin, F. Marquardt, R. J. Schoelkopf | arXiv non-exclusive distribution 1.0 |
| 0811.1338 | 2008-11 | Modern Theory of Nuclear Forces | E. Epelbaum, H.-W. Hammer, U.-G. Meißner | arXiv non-exclusive distribution 1.0 |
| 1303.0733 | 2013-03 | Cavity Optomechanics | M. Aspelmeyer, T. J. Kippenberg, F. Marquardt | arXiv non-exclusive distribution 1.0 |

"none listed": submitted before arXiv recorded a licence choice; arXiv
distributes them under its default terms of the time. None of the eight is
under a Creative Commons licence: arXiv's non-exclusive licence lets arXiv
distribute the paper but grants others no reuse rights, and copyright stays with
the authors/publishers. The equations here are short excerpts re-typeset for
internal evaluation; keep this set private and do not redistribute it.
