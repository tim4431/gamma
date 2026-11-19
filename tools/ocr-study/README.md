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
against a transcribed truth.

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

## Settings

| Variable | What it is |
|---|---|
| `OCR_STUDY_DIR` | the working directory (default `tools/ocr-study/.work`) |
| `OCR_STUDY_TESSERACT` | the Tesseract 5.5 executable for `run_tess.py` (default `tesseract` on PATH) |
| `OCR_STUDY_TESSDATA_FAST`, `OCR_STUDY_TESSDATA_BEST` | the tessdata_fast and tessdata_best folders |
| `OCR_STUDY_TJS` | the folder tesseract.js is installed in (default `$OCR_STUDY_DIR/tjs`) |
| `OCR_STUDY_TJS_CACHE` | where tesseract.js keeps its language data (default `$OCR_STUDY_DIR/tjs-cache`) |
| `OCR_STUDY_PYTHON` | the Python the shell scripts run the engines with (default `python`) |
| `OCR_STUDY_MUPDF_PYTHON` | the Python for `run_mupdf.py` (default `OCR_STUDY_PYTHON`) |

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
