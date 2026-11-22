# Stage 3 engines: formula detection and recognition

Set up 2026-11-19 on Ryzen 9 9900X (12 cores, 24 threads), RTX 4070 SUPER 12 GB (driver 591.86), Windows 11.
The code is in this folder (`tools/ocr-study/stage3/engines`). Everything else lives in the engines'
work folder, `$OCR_STUDY_DIR\stage3\engines` (`$E` below; by default `tools\ocr-study\.work\stage3\engines`),
and the paths below are relative to it: `..\` is `$OCR_STUDY_DIR\stage3`, and outputs go to `..\det\` and `..\rec\`.
Nothing in the work folder belongs in a repository.

## Layout

In this folder:

| path | what |
|---|---|
| `common.py` | paths (through `../common.py`), ONNX Runtime thread control, helpers |
| `detect.py`, `recognize.py`, `vlm.py` | the runners |
| `pageocr.py` | page OCR on Stage 2's 15 pages with the llama.cpp models: whole pages, or PP-DocLayoutV3 regions read one by one (into `$OCR_STUDY_DIR\stage2\vlm_raw`, `--timing-out` for CPU timings with the peak working set) |
| `tools\` | download lists (`fetch*.sh`, sha256-checked), crop cutter, conversion checks, `shrink_layout.py` / `shrink_fp16.py`, `summarize.py`, `smoke_rest.sh`; for `pageocr.py`, `run_pageocr_gpu.sh` (every engine on the GPU), `run_pageocr_cpu.sh` (CPU timings on an idle machine) and `pageocr_summary.py` (seconds, tokens, caps and odd answers per engine) |

In the work folder `$E`:

| path | what |
|---|---|
| `venv\` | Python 3.12.12 (uv): onnxruntime 1.32.0, rapid-layout 1.2.1, rapid-latex-ocr 0.0.9 (installed `--no-deps`, see below), numpy 2.5.5, opencv-python 5.1.0.95, pillow 13.0.0, tokenizers 0.23.3, ftfy 6.3.1, pyyaml |
| `venv-texteller\` | Python 3.12.12: texteller 1.0.2 with torch 2.15.0 (CPU wheel), transformers 4.47.0, optimum 2.1.0 / optimum-onnx 0.1.0. `recognize.py --engine texteller` re-runs itself here |
| `venv-p2o\` | Python 3.12.12: paddlepaddle 3.1.1 (CPU) + paddle2onnx 2.1.0, used once to convert PP-FormulaNet_plus-S and to check the conversion |
| `venv-quant\` | Python 3.12.12: onnx 1.23.2, onnxconverter-common 1.16.0, onnxruntime 1.32.0, for `tools\shrink_layout.py` and `tools\shrink_fp16.py` |
| `dl\<name>\` | one folder per download (untrusted data; Python reading them runs with `-I`) |
| `work\pp-formulanet_plus-s\inference.onnx` | the locally converted PP-FormulaNet_plus-S |
| `work\pp-doclayoutv3-small\` | `int8.onnx` from `tools\shrink_layout.py`, `fp16.onnx` from `tools\shrink_fp16.py` (the fp16 file `shrink_layout.py` writes does not load) |
| `crops\smoke\` | the 5 smoke-test crops from lounis-p8 (`tools\make_smoke_crops.py`, boxes in `crops.json`) |
| `logs\` | llama-server logs per run |
| `meta\` | API listings of the releases and repos at download time, install and download logs |

Run every runner from this folder with `-I` (the runners add their own folder to `sys.path`):

```powershell
cd tools\ocr-study\stage3\engines
$E = "$env:OCR_STUDY_DIR\stage3\engines"   # with OCR_STUDY_DIR set; else tools\ocr-study\.work\stage3\engines
```

## 1. Detection: `detect.py`

```powershell
& $E\venv\Scripts\python.exe -I detect.py --model pp-doclayoutv3 [page ...] [--threads 1|default|both] [--conf 0.3] [--keep-inline]
& $E\venv\Scripts\python.exe -I detect.py --model pp-doclayout-plus-l lounis-p8 holstein-p5
& $E\venv\Scripts\python.exe -I detect.py --model doclayout-yolo
```

* Pages are ids from `..\pages.json` (`..\img\<page>.png`); none = all 69 pages.
* Output `..\det\<model>\<page>.json` = `[{"cls", "box": [x0, y0, x1, y1], "score"}]` in 300-dpi page pixels, all classes
  (filter on `cls` for formulas). `..\det\<model>\_timing.json` has per-page seconds for each thread mode, load and warm-up time.
* Timing = the engine call on the decoded page (resize, inference, post-processing), after one warm-up call.
  `--threads both` (default) runs one-thread (`intra_op = inter_op = 1`, OpenCV 1 thread) then ONNX Runtime's default (12 intra-op threads here); the boxes were identical in both modes.
* `--conf` defaults to 0.3 (rapid-layout's default is 0.5); scores are kept, so filter later.
* All three go through rapid-layout's pre/post-processing (a port of PaddleX's). For the two PP models that includes
  PaddleX's overlap filter, which removes `inline_formula` boxes lying inside text paragraphs (and any box >70 % inside a larger one).
  On lounis-p8 that leaves 6 display formulas + 6 numbers; `--keep-inline` switches the filter off and V3 then also returns 31 `inline_formula` boxes
  (PP-DocLayout_plus-L has no inline class; its inline maths come back as `formula`).

| model | classes for formulas | file | licence |
|---|---|---|---|
| `pp-doclayoutv3` | display_formula, inline_formula, formula_number | `dl\rapid-layout-pp_doc_layoutv3\pp_doc_layoutv3.onnx` (130.5 MB, sha256 250dbad1…) from https://www.modelscope.cn/models/RapidAI/RapidLayout/resolve/v1.2.0/onnx/pp_doc_layout/pp_doc_layoutv3.onnx | Apache-2.0 (PaddlePaddle; RapidAI export) |
| `pp-doclayout-plus-l` | formula, formula_number | `dl\pp-doclayout_plus-l-onnx\inference.onnx` (129.7 MB) + `inference.yml`, PaddlePaddle's official ONNX: https://huggingface.co/PaddlePaddle/PP-DocLayout_plus-L_onnx (rev feb74619) | Apache-2.0 |
| `doclayout-yolo` | isolate_formula, formula_caption (the equation number) | `dl\rapid-layout-doclayout_docstructbench\doclayout_yolo_docstructbench_imgsz1024.onnx` (75.3 MB) from https://www.modelscope.cn/models/RapidAI/RapidLayout/resolve/v1.2.0/onnx/doclayout/doclayout_yolo_docstructbench_imgsz1024.onnx | **AGPL-3.0** (stated in the ONNX metadata and the DocLayout-YOLO code; the HF weights card says Apache-2.0) |

rapid-layout 1.2.1 also offers PP-DocLayoutV2 (MinerU-4's layout model) and DocLayout-YOLO D4LA/DocSynth; they are not downloaded.

## 2. Recognition: `recognize.py`

```powershell
& $E\venv\Scripts\python.exe -I recognize.py --engine <engine> <crops_dir> [--threads 1|default|both]
```

* Every `*.png` in `crops_dir` is read (crop id = file stem). Output `..\rec\<engine>\<crop_id>.json`:
  `{"latex", "seconds", "seconds_by_threads": {"1": s, "default": s}, "latex_raw", ...}`; `seconds` is the default-thread time.
  `latex` is the engine's own post-processed string with one outer `\[ \]` / `$$ $$` pair removed; `latex_raw` is the string before the
  engine's post-processing where the runner sees it. Summary: `..\rec\<engine>\_timing.json`.
* Timing = the engine call on the decoded crop, after model load and one warm-up call, batch size 1.

| engine | what runs | files | licence |
|---|---|---|---|
| `pp-formulanet-plus-m` | MinerU-4's ONNX of PP-FormulaNet_plus-M (exported from their Torch port; the decode loop is inside the graph, forced EOS at 1536 tokens). Pre/post-processing ported from PaddleX 3.7 (`UniMERNetImgDecode` 384x384, `UniMERNetTestTransform`, `UniMERNetDecode` with ftfy + `normalize`) | `dl\mineru4-onnx-formulanet_plus_m\PP-FormulaNet_plus-M.onnx` (591.3 MB) + yml (tokenizer), https://huggingface.co/opendatalab/MinerU-4_models_onnx (rev 358310b4) | Apache-2.0 |
| `pp-formulanet-plus-s` | No ONNX of plus-S is published (PaddlePaddle's `PP-FormulaNet_plus-L_onnx` repo is empty, none for S/M). Converted here from the official Paddle model with paddle2onnx 2.1.0 (paddlepaddle 3.1.1; 3.2.2/3.3.x fail to load paddle2onnx's DLL on Windows), opset 18, no constant folding. `tools\check_formulanet_s.py`: token ids identical to Paddle Inference on all 5 smoke crops | `work\pp-formulanet_plus-s\inference.onnx` (333.9 MB) from https://huggingface.co/PaddlePaddle/PP-FormulaNet_plus-S (rev 3d46f557) | Apache-2.0 |
| `pix2text-mfr-1.5` | The TrOCR encoder/decoder ONNX with pix2text's own steps reimplemented without torch: 384x384 bicubic resize, mean/std 0.5, greedy decode (no KV cache, as pix2text's `use_cache=False`), max 1024 new tokens, `LatexOCR.post_process` from pix2text 1.1.7. `tools\check_pix2text.py` (TrOCRProcessor + optimum `ORTModelForVision2Seq.generate`): identical raw output on all 5 crops | `dl\pix2text-mfr-1.5\` (119.5 MB), https://huggingface.co/breezedeus/pix2text-mfr-1.5 (rev 1cef9f0b) | MIT |
| `rapid-latex-ocr` | RapidLaTeXOCR 0.0.9 = pix2tex/LaTeX-OCR in ONNX, max 512 tokens, temperature 1e-5 sampling (seeded). The package pins numpy<2 but rapid-layout needs numpy>=2; the only numpy-2 break (`int()` of a 1-element array in `loop_image_resizer`) is replaced by a copy using `.item()` | `dl\rapid-latex-ocr-v0.0.0\` (179 MB: image_resizer, encoder, decoder, tokenizer; sha256 in `meta\`), https://github.com/RapidAI/RapidLaTeXOCR/releases/tag/v0.0.0 | MIT (repo; PyPI metadata says Apache-2.0) |
| `texteller` | TexTeller 3 (`OleehyO/TexTeller`, the 80M-pair model) through `texteller.img2latex`, PyTorch on CPU, greedy, max 1024 tokens; output comes wrapped in `\[ \]` | `dl\texteller-3\` (1.19 GB safetensors + tokenizer), https://huggingface.co/OleehyO/TexTeller (rev 7b96df06) | Apache-2.0 |

TexTeller installed cleanly with `uv pip install texteller==1.0.2` into its own venv (it pins transformers 4.47 and pulls ray[serve] and streamlit, 1.1 GB of packages);
its ONNX path wants the 2.1 GB of ONNX files from the hub, so the torch path is used.

## 3. VLMs through llama.cpp: `vlm.py`

```powershell
& $E\venv\Scripts\python.exe -I vlm.py --model <model> --device cpu|gpu <crops_dir> [--threads 1|default|both] [--max-tokens 1024]
& $E\venv\Scripts\python.exe -I vlm.py --model granite-docling --device gpu --page lounis-p8      # page mode
```

* llama.cpp: official release **b12359 = v1.0.0** (2026-11-13, commit b1b770d90), `llama-b12359-bin-win-cpu-x64.zip` in `dl\llamacpp-cpu-b12359\bin\`,
  `llama-b12359-bin-win-cuda-12.4-x64.zip` + `cudart-llama-bin-win-cuda-12.4-x64.zip` in `dl\llamacpp-cuda12-b12359\bin\`
  (sha256 checked against the GitHub release digests). Licence MIT.
* The runner starts `llama-server` on 127.0.0.1 at the first free port from 9100 (never 9001/5173/9002), waits for `/health`,
  posts each image as a PNG data URL plus the prompt to `/v1/chat/completions` with `temperature 0, top_k 1, max_tokens, cache_prompt false`,
  and stops the server at the end (also on error). Server flags: `-c 4096 -np 1 --temp 0 --no-webui`, `-ngl 999` on gpu, `-ngl 0` on cpu,
  `-t N -tb N` for the one-thread run (default = llama.cpp's own thread count). One warm-up request precedes the timed ones.
* Output `..\rec\<model>-<device>\<crop_id>.json`: `{"latex", "seconds", "raw", "seconds_by_threads", "tokens_in", "tokens_out", "finish_reason", "server": {llama.cpp timings}, "prompt"}`.
  `latex` = `raw` with (Granite) DocTags `<loc_…>` removed, then one markdown code fence, then one outer `$$ … $$` / `\[ … \]` / `$ … $` pair
  (GLM-OCR wraps in `$$`). LightOnOCR-2 and dots.ocr answer in markdown instead: a ```` ```latex ```` fence (LightOnOCR-2 on most crops, sometimes
  closed by `$$` instead of a fence), several `$$` blocks for a formula set on several lines, inline `$…$`. For these two, `md_math` removes fence lines
  and lines that are only `$$`/`\[`/`\]`, every other `$$`, every unescaped `$` and blank lines, and joins the pieces with newlines; any text or HTML
  (`<sub>`, `<sup>`) they wrote stays. `tools\check_to_latex.py [rec dirs]` tests the rules and lists any answer that still holds `$` or a fence after them (none).
  `_timing.json` has the server arguments, version, GPU and per-mode means. An empty prompt sends the image alone (LightOnOCR-2).
* Page mode (`--page [page ...]`): the model's page prompt, `max_tokens 8192`, `-c 16384`; output `..\rec\<model>-<device>-page\<page>.json` with `text`
  (the raw page output). For granite-docling the server gets `--special`, so the DocTags keep their element tags (`<text>`, `<formula>`, `<loc_N>`…);
  without it llama.cpp drops them as special tokens. `--special` can be forced for any run.

| model | files | crop prompt (from the model card) | page prompt | licence |
|---|---|---|---|---|
| `paddleocr-vl-1.6` | `PaddleOCR-VL-1.6-GGUF.gguf` 935.8 MB + `PaddleOCR-VL-1.6-GGUF-mmproj.gguf` 881.8 MB, https://huggingface.co/PaddlePaddle/PaddleOCR-VL-1.6-GGUF (rev 511b0964) | `Formula Recognition:` | `OCR:` | Apache-2.0 |
| `glm-ocr` | `GLM-OCR-Q8_0.gguf` 950.4 MB + `mmproj-GLM-OCR-Q8_0.gguf` 484.4 MB, https://huggingface.co/ggml-org/GLM-OCR-GGUF (rev 65a42de1) | `Formula Recognition:` | `Text Recognition:` | MIT |
| `granite-docling` | `granite-docling-258M-BF16.gguf` 331.6 MB + `mmproj-model-f16.gguf` 190.0 MB, https://huggingface.co/ibm-granite/granite-docling-258M-GGUF (rev e1e344cb; the repo also has an older `…-bf16.gguf` of the same size) | `Convert formula to LaTeX.` | `Convert this page to docling.` | Apache-2.0 |
| `lightonocr-2` | `LightOnOCR-2-1B-Q8_0.gguf` 639.4 MB + `mmproj-LightOnOCR-2-1B-Q8_0.gguf` 449.5 MB, https://huggingface.co/ggml-org/LightOnOCR-2-1B-GGUF (rev 7d4daf70; ggml-org, the llama.cpp maintainers; no README in the repo), converted from https://huggingface.co/lightonai/LightOnOCR-2-1B (rev be7c4131) | none: the image alone, as the card's transformers and vLLM examples (the card samples at temperature 0.2, top_p 0.9; the runner is greedy like the others) | none | Apache-2.0 |
| `dots-ocr` | `dots.ocr-Q8_0.gguf` 1894.5 MB + `mmproj-dots.ocr-Q8_0.gguf` 1344.1 MB, https://huggingface.co/ggml-org/dots.ocr-GGUF (rev 2c093a32; its README names llama.cpp PR #17575), from https://huggingface.co/rednote-hilab/dots.ocr (now dots-studio/dots.ocr, rev c0111ce6) | `Extract the text content from this image.` = `prompt_ocr` in `dots_ocr/utils/prompts.py` (github.com/studio-dots-ai/dots.ocr @ 36d72488), the text-only mode; the layout JSON modes are not used | same | MIT |

Both later readers load and run on the b12359 binaries (CPU and CUDA 12.4), Q8_0 for model and mmproj like GLM-OCR; their sha256 are in
`tools/fetch_vlm.sh`. LightOnOCR-2's card recommends rendering pages at 200 dpi (longest side 1540 px); the crops are 300 dpi as for the other readers.

## 4. Smoke test (5 crops, 3 pages)

Crops (`crops\smoke\`, cut from `..\img\lounis-p8.png`, ink-tight + 12 px, equation number excluded):
`e3_16` 2x2 matrix equation (1403x203 px), `e3_17` T_fi fraction (381x112), `e3_19` D = (δ1−zI)(δ1−zII) (438x68),
`e3_20` long two-fraction line with brackets (1199x124), `e3_21` |T_fi|² nested fraction (957x171). Detection pages: lounis-p8, holstein-p5, feshbach-p10.

"1 thread" = ONNX Runtime intra/inter-op 1 (TexTeller: `torch.set_num_threads(1)`; llama.cpp: `-t 1 -tb 1`).
"default" = the runtime's own choice, 12 threads on this 12-core CPU for all three runtimes (ONNX Runtime physical cores,
torch set to 12, llama.cpp `n_threads = 12`). Seconds exclude model load and one warm-up call; GPU = RTX 4070 SUPER, CUDA 12.4 build, all layers + mmproj offloaded.

Detection, mean wall seconds per page over 3 pages (the boxes were identical in both modes):

| detector | size on disk | 1 thread s/page | default s/page | load s | formula-class boxes on lounis-p8 |
|---|---|---|---|---|---|
| pp-doclayoutv3 | 130.5 MB | 1.14 | 0.50 | 0.5 | 6 display_formula + 6 formula_number (31 inline_formula with `--keep-inline`) |
| pp-doclayout-plus-l | 129.7 MB | 0.81 | 0.22 | 0.6 | 6 formula + 6 formula_number |
| doclayout-yolo | 75.3 MB | 1.33 | 0.37 | 0.2 | 6 isolate_formula + 7 formula_caption (one number boxed twice, scores 0.82/0.37) |

All three found the same 6 display equations and their numbers on lounis-p8 (V3 and plus-L boxes agree within a few px of the hand-cut crops).

Recognition, seconds per crop over the 5 crops, mean / max:

| engine | size on disk | CPU 1 thread | CPU default | GPU | licence |
|---|---|---|---|---|---|
| pp-formulanet-plus-s (converted ONNX) | 334 MB | 0.70 / 1.13 | 0.39 / 0.65 | – | Apache-2.0 |
| pp-formulanet-plus-m (MinerU-4 ONNX) | 594 MB | 2.44 / 4.21 | 1.77 / 3.24 | – | Apache-2.0 |
| pix2text-mfr-1.5 (ONNX) | 120 MB | 3.62 / 7.79 | 1.46 / 3.16 | – | MIT |
| rapid-latex-ocr (pix2tex ONNX) | 179 MB | 25.9 / 46.7 | 26.8 / 45.7 | – | MIT |
| texteller 3 (torch) | 1194 MB (+1.1 GB venv) | 8.40 / 16.3 | 6.04 / 13.3 | – | Apache-2.0 |
| paddleocr-vl-1.6 (llama.cpp) | 1818 MB | 10.8 / 19.6 | 3.47 / 6.59 | 0.33 / 0.58 | Apache-2.0 |
| glm-ocr Q8_0 (llama.cpp) | 1435 MB | 11.4 / 22.7 | 3.35 / 6.02 | 0.38 / 0.56 | MIT |
| granite-docling-258M (llama.cpp) | 522 MB | 20.8 / 29.5 | 4.83 / 6.23 | 0.78 / 2.11 | Apache-2.0 |

llama.cpp itself: CPU build 61 MB unpacked (25 MB zip), CUDA 12.4 build 1.24 GB unpacked (ggml-cuda.dll 682 MB, cuBLASLt 474 MB).
Page mode, Granite-Docling on the GPU, lounis-p8: 4.0 s, 878 prompt / 1975 output tokens, full DocTags (`--special` keeps the
`<text>`, `<formula>`, `<page_header>` tags; `<loc_N>` are on a 0–500 grid of the page). Granite's training context is 8192, so llama.cpp caps the page slot at 8192.

Per-crop outputs and times are in `..\rec\<engine>\*.json`, the summaries in `..\rec\*\_timing.json` and `..\det\*\_timing.json`;
`tools\summarize.py` prints these tables (`meta\summary_tables.md`).

Notes from the smoke run:

* Every engine runs and writes its JSON for all 5 crops; nothing was scored yet. By eye: PP-FormulaNet plus-M, PaddleOCR-VL-1.6, GLM-OCR and TexTeller
  get the structure of all five right; the visible slips are in the subscript `fi` (a Monotype ligature: `T_8`, `T_\hbar`, `T_{\bf6}`, `T_n`, `T_1`, `T_{ff}`),
  in `z_I`/`z_{II}` (read as `z_1`, `z_{11}`, `z_H`), invented wrappers (GLM-OCR puts `\left| … \right|` around 3.17, PaddleOCR-VL doubles `\left|` in 3.21
  and adds `\overline` in 3.19), and the 2x2 matrix (3.16), which every engine garbles in some way (plus-S drops backslashes inside it).
  Granite-Docling loses `\mathcal` and reads I as 1; pix2tex (rapid-latex-ocr) misreads 3.17 (`\delta\Omega_1`).
* rapid-latex-ocr (pix2tex) falls into repetition loops on the three wide crops and runs to its 512-token cap, ~35–46 s each; that, not the model size, is its time.
* Granite-Docling on the GPU ran the matrix crop to the 1024-token cap (finish `length`); on the CPU it stopped at 266–272 tokens.
  Thread count and backend change llama.cpp's arithmetic, so VLM outputs can differ slightly between the one-thread, default and GPU runs (PaddleOCR-VL's matrix: 214 vs 200 tokens).
  The ONNX engines gave the same output in both thread modes.
* Granite-Docling's one-thread CPU time is dominated by the SigLIP2 image encoder (345–610 image tokens per crop at 512x512 tiles), not by generation.
* llama.cpp's per-image token count depends on the crop size: PaddleOCR-VL 168–363 prompt tokens, GLM-OCR 44–362, Granite-Docling 345–610.

## Problems met (and what was done)

* **PP-FormulaNet_plus-S has no published ONNX.** Converted with paddle2onnx 2.1.0. paddle2onnx's DLL fails to load next to paddlepaddle 3.2.2, 3.3.0 and 3.3.1 on Windows
  ("DLL load failed … procedure not found"); paddlepaddle 3.1.1 works. The converter's Polygraphy step tried to `pip install onnxruntime onnx_graphsurgeon` into
  `venv-p2o` (no pip there, so nothing was installed) and skipped constant folding. Paddle Inference's oneDNN `scale` kernel throws inside the decode loop,
  so the reference check runs with `disable_mkldnn()`. Result checked: identical token ids to Paddle Inference on the 5 crops.
* **rapid-latex-ocr pins numpy<2**, rapid-layout needs numpy>=2: installed with `--no-deps`, one method replaced (see above). Its long outputs are repetition loops to the 512-token cap.
* **rapid-layout's PP post-processing removes inline formulas** inside text boxes (PaddleX's overlap filter); `detect.py --keep-inline` turns it off.
* **DocLayout-YOLO is AGPL-3.0** per its code and the ONNX metadata (the HF weights card says Apache-2.0); it runs fine.
* **TexTeller's ONNX path** (`load_model(use_onnx=True)`) only loads from the hub repo (2.1 GB more ONNX files); the torch path with the safetensors weights is used instead.
  Installing texteller 1.0.2 pulls transformers 4.47, ray[serve] and streamlit, so it has its own venv.
* **llama.cpp drops special tokens** from the reply unless the server has `--special`; matters only for Granite-Docling's DocTags in page mode.
* VLM outputs are not bit-stable across thread counts and backends (see the token counts in the table above); the ONNX recognisers and detectors are.

## Re-running the smoke test

```powershell
& $E\venv\Scripts\python.exe -I tools\make_smoke_crops.py
foreach ($m in "pp-doclayoutv3","pp-doclayout-plus-l","doclayout-yolo") { & $E\venv\Scripts\python.exe -I detect.py --model $m lounis-p8 holstein-p5 feshbach-p10 }
foreach ($m in "paddleocr-vl-1.6","glm-ocr","granite-docling") { & $E\venv\Scripts\python.exe -I vlm.py --model $m --device gpu $E\crops\smoke }
bash tools/smoke_rest.sh      # VLMs on the CPU (1 thread + default), the five recognisers, Granite page mode
& $E\venv\Scripts\python.exe -I tools\summarize.py
```

## LightOnOCR-2 and dots.ocr (added later)

Both run through the same b12359 binaries, on the GPU in `run_gpu.sh` (real crops and synthetic scans) and on the CPU in
`run_timing.sh` (one model at a time, `--threads both`, the peak working set from `mem_sampler.ps1`).

| | LightOnOCR-2-1B | dots.ocr |
|---|---|---|
| GPU, 152 real crops, mean / median / max s | 0.31 / 0.23 / 3.04 | 0.47 / 0.36 / 3.02 |
| GPU, 191 synthetic scans, mean / median / max s | 0.20 / 0.19 / 0.75 | 0.35 / 0.29 / 4.87 |
| hit the 1024-token cap (GPU) | real: holstein-p7-e5 | synthetic: eq0058 (a `\ \ \ …` run) |
| CPU, 30 Lounis displays, 1 thread mean / median / max s | 4.71 / 3.18 / 19.1 | 11.2 / 7.84 / 48.1 |
| CPU, 30 Lounis displays, default (12 threads) mean / median / max s | 1.43 / 1.08 / 5.22 | 3.06 / 2.26 / 12.0 |
| CPU, 53 inline crops, 1 thread mean / median / max s | 9.83 / 1.05 / 125 | 1.92 / 1.64 / 5.53 |
| CPU, 53 inline crops, default mean / median / max s | 3.98 / 0.35 / 18.9 | 0.56 / 0.52 / 1.70 |
| hit the 1024-token cap (CPU, inline) | 9 of 53, 2 more over 700 tokens | none |
| peak llama-server working set, displays / inline | 2263 / 3110 MB | 3478 / 3245 MB |

* The working set grows with what a request touches (KV pages of long generations, vision buffers of large images), so LightOnOCR-2's
  peak is higher on the inline set, where its runaway answers (`H_0 , H_1 , H_2 , …`) fill the context.
* LightOnOCR-2 answers in ```` ```latex ```` fences (137 of 152 real crops), sometimes `$$`, and splits multi-line displays into several `$$` blocks.
* dots.ocr's `prompt_ocr` is a text-OCR mode: most displays come back as `$$ … $$` LaTeX, but 10 of 152 real crops (6 of 191 synthetic) come back as
  Unicode text (`H = H₀ + V`, `ħω`), 2 (1) with HTML `<sub>`/`<sup>`, and some as spaced LaTeX without delimiters. They are scored as they are.
* These CPU timings, like the earlier readers', ran beside about 3 of 24 logical CPUs of unrelated background load.
