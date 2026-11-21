# OCR study harness

The scripts behind [the OCR study](../../docs/research/ocr.md), which has the
method, the measures and the results. Stage 1 scores how well a repair of a
scan's TextContent lines the text layer up with the ink: the ink truth is
taken from the rendered scan, and the evaluator runs the repo's
`repairTextContent` (`frontend/src/pdf/scanText.js`), the unrepaired layer
and an oracle built from the truth. It also holds the study's scan detector
as standalone scripts to run over a corpus (`detect.mjs`, the prototype of
`frontend/src/pdf/scanDetect.js`, and its PyMuPDF twin `detect.py`).
Stage 2 runs OCR engines over 15 rendered pages and scores their text
against a transcribed truth. Stage 3 finds the display formulas on 69
scanned pages and reads them as LaTeX, scoring layout models and formula
readers against marked boxes and transcribed or synthetic truth.

None of it is app code: nothing imports `backend/gamma`, and no server is
needed.

## Working directory and corpus

Everything the scripts read or write outside this folder lives in one
working directory, `$OCR_STUDY_DIR`, by default `tools/ocr-study/.work`
(ignored by git). The corpus goes in it as `corpus/<id>.pdf`, one file per id
of [corpus.json](corpus.json), which names each scan's citation, where the
public ones can be fetched, its producer and page count. The APS, Annals of
Physics, SPIE and Siegman files are under copyright: any library copy from
the same producer serves. Nothing in the working directory may be
committed: the pages are under copyright, and the transcriptions are made
from them.

| Under `$OCR_STUDY_DIR` | Written by |
|---|---|
| `corpus/<id>.pdf` | you |
| `stage1/words/<file>-p<N>.json` | `dump_words.mjs`: the page's TextContent, viewport and words |
| `stage1/truth/<file>-p<N>.json`, `summary.json` | `truth.py`: line ink bands and word ink extents |
| `stage1/overlays/<file>-p<N>.png` | `truth.py --overlay` |
| `stage2/img/<page>.png`, `.json` | `render.py`: the 300-dpi grey page and its size |
| `stage2/out/<engine>/<page>.json` | `render.py` (the `layer` engine), every `run_*` script, `relayout.py` |
| `stage2/logs/<engine>.log` | `run_all.sh`, `run_t1.sh`, `run_lang.sh` |
| `stage2/gtprep/<page>/tiles/`, `draft_<tag>.txt` | `prep_gt.py` |
| `stage2/gtprep/gt_A/`, `gt_B/` | the two transcription passes |
| `stage2/gtprep/<page>/diff.txt`, `diff/<n>.png` | `diff_gt.py` |
| `stage2/gt/<page>.txt` | the settled truth |
| `stage3/pages.json`, `stage3/img/<page>.png` | `render_pages.py`: the 300-dpi grey pages |
| `stage3/lines/<page>.json` | `tess_lines.py`: Tesseract's lines and words |
| `stage3/grid/<page>/<tile>.png`, `grid/index.json` | `make_grid_tiles.py` (`tiles/<page>/` from `make_tiles.py`) |
| `stage3/marks/<page>.json` | the marking, under [MARK_RULES.md](stage3/MARK_RULES.md) |
| `stage3/eq/<id>.png`, `eq/index.json` | `snap.py`: the display crops and their boxes |
| `stage3/synth/` | the synthetic set ([synth/README.md](stage3/synth/README.md)) |
| `stage3/gt_synth/<id>.tex` | the synthetic truth, the `latex` of `synth/gt/<id>.json` |
| `stage3/gt_sample.json`, `crops_real/<id>.png` | the 152 sampled displays and their crops from `eq/` |
| `stage3/gtA/`, `gtB/` | the two transcription passes, under [LATEX_RULES.md](stage3/LATEX_RULES.md) |
| `stage3/adj/` | `compare_gt.py`: both readings rendered, and the list to settle |
| `stage3/gt_latex/<id>.tex` | `compare_gt.py` and the settling: the real truth |
| `stage3/rec_passes/gtA/`, `gtB/` | the two passes as `{latex}` records, scored as readers |
| `stage3/engines/` | the engines' venvs, downloads (`dl/`), converted models (`work/`), smoke crops, `logs/`, `meta/` |
| `stage3/det/<model>/<page>.json` | `engines/detect.py`, `detect_tess.py` (`tess-rules`) |
| `stage3/det_inline/pp-doclayoutv3/` | `engines/detect.py --keep-inline` |
| `stage3/rec/<engine>/`, `rec_synth/<engine>/` | `engines/recognize.py`, `engines/vlm.py` (`run_cpu.sh`, `run_gpu.sh`), the frontier pass |
| `stage3/logs_<engine>_<set>.txt` | `run_cpu.sh`, `run_gpu.sh` |
| `stage3/crops_lounis/`, `crops_inline/`, `rec_inline/` | the 30 Lounis crops of the sample; `cut_inline.py`; `engines/vlm.py` on the inline crops |
| `stage3/render_cache/` | `prerender.py`, `score_latex.py --render` |
| `stage3/scores/<set>-<engine>.json`, `guard-<set>.json` | `score_all.sh`, `guard.py` |
| `stage3/timing/` | `run_timing.sh`, with `peaks.json` from `mem_sampler.ps1` |

## Settings

| Variable | What it is |
|---|---|
| `OCR_STUDY_DIR` | the working directory (default `tools/ocr-study/.work`) |
| `OCR_STUDY_TESSERACT` | the Tesseract 5.5 executable for `run_tess.py` (default `tesseract` on PATH) |
| `OCR_STUDY_TESSDATA_FAST`, `OCR_STUDY_TESSDATA_BEST` | the tessdata_fast and tessdata_best folders |
| `OCR_STUDY_TJS` | the folder tesseract.js is installed in (default `$OCR_STUDY_DIR/tjs`) |
| `OCR_STUDY_TJS_CACHE` | where tesseract.js keeps its language data (default `$OCR_STUDY_DIR/tjs-cache`) |
| `OCR_STUDY_PYTHON` | the Python the Stage 2 shell scripts run the engines with, and Stage 3's `score_all.sh` scores with (default `python`) |
| `OCR_STUDY_MUPDF_PYTHON` | the Python for `run_mupdf.py` (default `OCR_STUDY_PYTHON`) |
| `OCR_STUDY_PDFLATEX` | TeX Live 2024's pdflatex for Stage 3's renders (default `pdflatex` on PATH) |
| `OCR_STUDY_ENGINES_PYTHON` | the Python the Stage 3 shell scripts run the engines with (default `$OCR_STUDY_DIR/stage3/engines/venv/Scripts/python.exe`) |

## Setup

- Node 24 and the frontend's dependencies (`npm --prefix frontend ci`):
  Stage 1 loads pdf.js 4.4.168's legacy build from `frontend/node_modules`
  and `frontend/src/pdf` directly.
- A Python 3.13 environment with `pymupdf`, `numpy`, `pillow` and
  `rapidfuzz` for both stages, and for the engines `rapidocr==3.10.*`,
  `onnxruntime`, `onnxtr==0.9.*` (with its ONNX Runtime extra) and
  `tesserocr==2.13.*`. The study ran `run_mupdf.py` with PyMuPDF 1.28.2, the
  server's pin; set `OCR_STUDY_MUPDF_PYTHON` to a Python that has it if the
  engine environment has another.
- Tesseract 5.5 from conda-forge (`conda create -p <dir> -c conda-forge
  tesseract=5.5`) for `run_tess.py`, and the models from
  [tessdata_fast](https://github.com/tesseract-ocr/tessdata_fast) and
  [tessdata_best](https://github.com/tesseract-ocr/tessdata_best) (`eng`,
  `fra`, `deu`).
- tesseract.js 7.0.0 for `run_tjs.mjs`: `npm install tesseract.js@7.0.0` in
  `$OCR_STUDY_TJS`. It fetches its language data from jsdelivr on first use.

The Python scripts are run with `-I`; they put their own folder on
`sys.path` themselves.

## Stage 1: repairing a scan's text layer

From `stage1/`:

1. `python -I characterize.py --pages 4 --json <out.json> $OCR_STUDY_DIR/corpus/*.pdf`
   prints each file's producer and, per sampled page, image coverage, render
   modes, fonts, `Tz`, words per item, line size spread, shared baselines
   and box over ink width. `--screen` gives the cheap per-page facts for
   every page, for a set of born-digital negatives.
2. `node dump_words.mjs [--file <id>]` dumps the TextContent of the 46 pages
   in [pages.json](stage1/pages.json).
3. `python -I truth.py [--file <id>] [--page <N>] [--overlay] [--crop y0,y1]`
   builds the ink truth and prints per page and per file how many words and
   lines it could score. Look at the overlays of a page per producer before
   trusting it.
4. `node eval.mjs [repo|baseline|oracle|<candidate.mjs>] [--file <id>] [--page <N>] [--json <out.json>] [--quiet]`
   prints the measures per file, for the poor and good layers and for all
   pages, the first segmentation blocks of the pages `pages.json` names under
   `show`, and any contract violations. `repo` (the default) is the repo's
   `repairTextContent`; a candidate is any module that exports one. `--help`
   prints the measures' definitions.
5. The detector: `node detect.mjs --out <file.jsonl> [--max N] [--confirm all|pass] [--sample <sample.json>] (list.txt | pdf...)`
   runs the prescreen on every page and the confirm on the pages it passes
   (`--confirm all` on the positives, `--sample` for a random sample of
   negatives); `python -I detect.py [--max N] [--full] --out <file.jsonl> (list.txt | pdf...)`
   is the server's twin. Compare the two verdicts page by page.

## Stage 2: what real OCR adds

From `stage2/`, on the pages of [pages.json](stage2/pages.json):

1. `python -I render.py` renders each page at 300 dpi in grey and writes the
   existing layer's words as the `layer` engine.
2. `bash run_all.sh` runs every engine configuration once, one at a time;
   `bash run_t1.sh` the single-thread timings; `bash run_lang.sh` the
   language variants. Each runner also works alone on a page id or a corpus
   id: `run_tess.py`, `run_tesserocr.py` (run with `OMP_THREAD_LIMIT=1` for
   one thread), `run_mupdf.py`, `run_rapid.py`, `run_onnxtr.py`,
   `node run_tjs.mjs [--name <engine>] [--psm <N>]`. Their usage is at the
   top of each file.
3. `python -I relayout.py [--regroup] <engine>...` writes a reading-order
   variant of an engine as `<engine>+order`, or with `--regroup` lines rebuilt
   from the words alone as `<engine>+lines`.
4. The truth, under the rules in [RULES.md](stage2/RULES.md):
   `python -I prep_gt.py --draft tess-best --tag A --alts rapid-v6-small,onnxtr-db_resnet50-parseq`
   cuts each page into tiles and writes the draft with the other engines'
   readings. Transcribe each page twice into `gtprep/gt_A/<page>.txt`
   (proofreading the draft) and `gtprep/gt_B/<page>.txt` (from the tiles
   alone), then `python -I diff_gt.py <page>...` lists where they differ with
   a crop of each line. Settle those and write `gt/<page>.txt`.
5. `python -I score.py [--engines a,b] [--pages ids] [--gt <dir>] [--json <out.json>]`
   prints each engine's character error rate per page, and over all pages
   with word recall, word precision and continuity; `--json` keeps the
   per-page numbers, display equations scored apart.

## Stage 3: formulas on scanned pages

Setup, beyond the corpus:

- A plain Python environment with `numpy`, `pillow` and `pymupdf`, and
  `tesserocr` with a tessdata folder holding `eng`, `fra` and `deu` for
  `tess_lines.py` (Stage 2's environment has them). The study ran it on
  Python 3.12.
- The engines' venvs under `$OCR_STUDY_DIR/stage3/engines`, all Python
  3.12.12 made with uv ([engines/README.md](stage3/engines/README.md)):
  `venv` (onnxruntime 1.32.0, rapid-layout 1.2.1, rapid-latex-ocr 0.0.9
  installed `--no-deps`, numpy 2.5.5, opencv-python 5.1.0.95, pillow 13.0.0,
  tokenizers 0.23.3, ftfy 6.3.1, pyyaml); `venv-texteller` (texteller 1.0.2,
  torch 2.15.0 CPU, transformers 4.47.0, optimum 2.1.0, optimum-onnx 0.1.0);
  `venv-p2o` (paddlepaddle 3.1.1 CPU, paddle2onnx 2.1.0); `venv-quant` (onnx
  1.23.2, onnxconverter-common 1.16.0, onnxruntime 1.32.0). The runners
  expect Windows paths (`Scripts\python.exe`, `llama-server.exe`).
- TeX Live 2024's pdflatex with amsmath, amssymb, newtx, standalone and
  geometry (`OCR_STUDY_PDFLATEX`).
- llama.cpp b12359's Windows binaries (CPU, and CUDA 12.4 with its cudart),
  fetched by `engines/tools/fetch_vlm.sh` and unpacked into
  `dl/llamacpp-cpu-b12359/bin` and `dl/llamacpp-cuda12-b12359/bin`.
- A CUDA GPU is optional: only `run_gpu.sh` needs one.

The marks, both transcription passes, the settled truth, the synthetic set
(private: its papers are under no Creative Commons licence, see
[synth/README.md](stage3/synth/README.md)) and the readers' outputs stay in
the working directory, like the corpus pages.

From `stage3/`, with the plain Python unless an engine is named:

1. `python -I render_pages.py` renders Lounis p3–15, Holstein p2–16,
   Kitagawa p1–6, Feshbach p3–34, NACA p9, J. Phys. Radium p33 and Annalen
   p3 (`corpus/<id>.pdf`) and writes `pages.json`.
2. `python -I tess_lines.py <tessdata dir>` writes Tesseract's lines with
   their words (eng+fra+deu, ten processes of one thread).
3. `python -I make_grid_tiles.py` cuts each page into tiles with a 100 px
   grid in page pixels; mark every display under
   [MARK_RULES.md](stage3/MARK_RULES.md) into `marks/<page>.json`.
   (`make_tiles.py` boxes and numbers Tesseract's lines on the tiles
   instead.)
4. `python -I snap.py` snaps the marked boxes to the ink and cuts
   `eq/<id>.png` with an 8 px margin.
5. The synthetic set: [synth/README.md](stage3/synth/README.md)
   (`synth/extract.py`, `build.py`, `check.py`); its truth for scoring is
   `gt_synth/<id>.tex`. Not to be redistributed.
6. The real truth: a sample of 152 displays across the papers
   (`gt_sample.json`, crops in `crops_real/`), transcribed twice into `gtA/`
   and `gtB/` under [LATEX_RULES.md](stage3/LATEX_RULES.md). `python -I
   compare_gt.py <id>...` takes what agrees into `gt_latex/` and renders the
   rest into `adj/` to settle. `python -I test_tokens.py` checks the
   normalizer the comparison and the scores share (`score_latex.tokens`).
7. The engines ([engines/README.md](stage3/engines/README.md)): `bash
   engines/tools/fetch_onnx.sh`, `fetch_texteller.sh` and `fetch_vlm.sh`
   download into `engines/dl` (sha256-checked); PP-FormulaNet_plus-S is
   converted with paddle2onnx in `venv-p2o` and checked with
   `engines/tools/check_formulanet_s.py`. The shrunken detector, in
   `venv-quant`: `engines/tools/shrink_layout.py <pp_doc_layoutv3.onnx>
   <engines/work/pp-doclayoutv3-small>` (int8) and `shrink_fp16.py
   <pp_doc_layoutv3.onnx> <engines/work/pp-doclayoutv3-small/fp16.onnx>`
   (fp16). From `engines/`, `detect.py --model <model>` runs each finder
   over all pages into `det/`. Then, from `stage3/`, `bash run_cpu.sh` runs
   the dedicated readers and `bash run_gpu.sh` the vision-language models
   on the real crops and the synthetic scans.
8. `python -I prerender.py [--jobs 6]` renders every truth and reading into
   `render_cache/`, then `bash score_all.sh` scores each reader that read
   every crop of a set with `score_latex.py`.
9. Finding: `python -I score_detect.py <model>` (recall, precision, F1,
   coverage, swallowed numbers), `detect_misses.py <model>` (why, per
   paper), `region_cover.py <model>` (displays covered by half and 80%), and
   `detect_tess.py` for the rule baseline, scored as `tess-rules`.
10. `python -I by_paper.py` prints each reader's NED per paper;
    `guard.py --set real|synth` the signals that might tell a bad reading;
    `pair_guard.py` one reader checked by another.
11. The frontier pass:
    - `python -I frontier.py groups` splits the synthetic scans into six
      groups (`frontier_groups.json`).
    - Give six agents (Claude Opus 5.5, effort medium) the prompt of
      [FRONTIER_PROMPT.md](stage3/FRONTIER_PROMPT.md) for their group,
      `python -I frontier.py prompt <k>`. Each writes
      `rec_synth/claude-opus-5-5/_batch<k>.json`.
    - `python -I frontier.py split` turns the batches into one record per
      crop.
    - `python -I audit_readers.py <transcript dir>` counts the tools the
      agents' transcripts called: one Read per crop, one Write per batch.
12. Inline formulas: from `engines/`, `detect.py --model pp-doclayoutv3
    --keep-inline --out <det_inline>` on the Lounis pages. Then `python -I
    cut_inline.py lounis-p6 lounis-p8`, `overlay.py <det dir> <page>
    <out.png> [cls,...]` to look at the boxes, and from `engines/`
    `vlm.py --model glm-ocr --device gpu <crops_inline> --out <rec_inline>`.
13. `bash run_timing.sh`, on an otherwise idle machine, times the detector
    (fp32 and fp16), plus-M, GLM-OCR, PaddleOCR-VL, LightOnOCR-2 and
    dots.ocr on the CPU on one thread and the default. Meanwhile `mem_sampler.ps1` (Windows PowerShell)
    keeps each engine process's peak working set in `timing/peaks.json`, in
    MiB.
