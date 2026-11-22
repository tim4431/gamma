"""Page OCR with the llama.cpp VLMs (Stage 2 text pages): whole pages (mode A) or layout regions (mode B).

    venv\\Scripts\\python.exe -I pageocr.py --engine <engine> --device gpu|cpu [page ...] [--threads default|1]
        [--pages-json <pages.json>] [--img-dir <dir>] [--out <dir>] [--timing-out <dir>]

Mode A, the whole page with the model's page prompt (max_tokens 8192; a page that stops on the cap is run again
with 16384 and both attempts are recorded):
  glm-page       GLM-OCR            "Text Recognition:"            (GLM-OCR SDK sampling: top_k 1, repetition penalty 1.1)
  paddle-page    PaddleOCR-VL-1.6   "OCR:"
  lighton-page   LightOnOCR-2       image alone
  dots-layout    dots.ocr           prompt_layout_all_en (JSON blocks with bbox, category, text, in reading order)
  dots-ocr-page  dots.ocr           prompt_ocr "Extract the text content from this image."
  granite-page   Granite-Docling    "Convert this page to docling."  (llama-server --special, DocTags kept)
Mode B, regions as GLM-OCR's SDK and PaddleOCR-VL's pipeline run them: PP-DocLayoutV3 (rapid-layout ONNX) with the
pipeline's own layout settings, the model's reading order, one crop per kept block (box + 4 px), prompt by block type:
  glm-regions    GLM-OCR            text / "Formula Recognition:" / "Table Recognition:" ("Text Recognition:" for text)
  paddle-regions PaddleOCR-VL-1.6   "OCR:" / "Formula Recognition:" / "Table Recognition:"

Run from this folder; venv\\ is $OCR_STUDY_DIR\\stage3\\engines\\venv (common.py). Pages are ids from Stage 2's
pages.json (tools/ocr-study/stage2/pages.json; none = all 15), images $OCR_STUDY_DIR/stage2/img/<page>.png.

Raw output (every byte the model wrote): <out>/<engine>/<page>.json, <out> by default $OCR_STUDY_DIR/stage2/vlm_raw
  {"engine", "mode", "model", "prompt", "text" | "regions", "seconds", "tokens_out", "finish_reason", "image_px", ...}
and the run's summary <out>/../vlm_timing/_run_<engine>-<device>[-t<N>].json.
With --timing-out the same raw JSON goes to <timing-out>/<engine>-t<N|default>-<page>.raw.json and
<engine>-t<..>-<page>.json holds seconds and the llama-server peak working set (a fresh server per page).
llama-server logs: $OCR_STUDY_DIR/stage3/engines/logs/pageocr-<engine>-<device>[...].log.
"""
from __future__ import annotations

import argparse
import base64
import ctypes
import ctypes.wintypes as wt
import io
import json
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common  # noqa: E402

common.patch_onnxruntime()
import vlm  # noqa: E402

PROMPT_LAYOUT_ALL_EN = (  # dots_ocr/utils/prompts.py @ studio-dots-ai/dots.ocr 36d72488, verbatim
    "Please output the layout information from the PDF image, including each layout element's bbox, its category, "
    "and the corresponding text content within the bbox.\n\n1. Bbox format: [x1, y1, x2, y2]\n\n2. Layout Categories: "
    "The possible categories are ['Caption', 'Footnote', 'Formula', 'List-item', 'Page-footer', 'Page-header', "
    "'Picture', 'Section-header', 'Table', 'Text', 'Title'].\n\n3. Text Extraction & Formatting Rules:\n"
    "    - Picture: For the 'Picture' category, the text field should be omitted.\n"
    "    - Formula: Format its text as LaTeX.\n    - Table: Format its text as HTML.\n"
    "    - All Others (Text, Title, etc.): Format their text as Markdown.\n\n4. Constraints:\n"
    "    - The output text must be the original text from the image, with no translation.\n"
    "    - All layout elements must be sorted according to human reading order.\n\n"
    "5. Final Output: The entire output must be a single JSON object.\n"
)
GREEDY = {"temperature": 0, "top_k": 1}
# glmocr/config.yaml page_loader: temperature 0, top_p 1e-5, top_k 1, repetition_penalty 1.1 (llama.cpp: repeat_penalty over
# the last repeat_last_n tokens; -1 is refused by the server, so the window is set to 32768, i.e. the whole context)
GLM_SDK = {"temperature": 0, "top_k": 1, "top_p": 0.00001, "repeat_penalty": 1.1, "repeat_last_n": 32768}

ENGINES = {
    "glm-page": {"model": "glm-ocr", "mode": "page", "prompt": "Text Recognition:", "sampling": GLM_SDK},
    "paddle-page": {"model": "paddleocr-vl-1.6", "mode": "page", "prompt": "OCR:", "sampling": GREEDY},
    "lighton-page": {"model": "lightonocr-2", "mode": "page", "prompt": "", "sampling": GREEDY},
    "dots-layout": {"model": "dots-ocr", "mode": "page", "prompt": PROMPT_LAYOUT_ALL_EN, "sampling": GREEDY},
    "dots-ocr-page": {"model": "dots-ocr", "mode": "page", "prompt": "Extract the text content from this image.", "sampling": GREEDY},
    "granite-page": {"model": "granite-docling", "mode": "page", "prompt": "Convert this page to docling.", "sampling": GREEDY, "special": True},
    "glm-regions": {
        "model": "glm-ocr", "mode": "regions", "sampling": GLM_SDK, "layout": "glm", "max_tokens": 8192,
        "prompts": {"text": "Text Recognition:", "formula": "Formula Recognition:", "table": "Table Recognition:"},
    },
    "paddle-regions": {
        "model": "paddleocr-vl-1.6", "mode": "regions", "sampling": GREEDY, "layout": "paddle", "max_tokens": 4096,
        "prompts": {"text": "OCR:", "formula": "Formula Recognition:", "table": "Table Recognition:"},
    },
}

# PP-DocLayoutV3 labels in the ONNX's order (= GLM-OCR's id2label)
V3_LABELS = ["abstract", "algorithm", "aside_text", "chart", "content", "display_formula", "doc_title", "figure_title",
             "footer", "footer_image", "footnote", "formula_number", "header", "header_image", "image", "inline_formula",
             "number", "paragraph_title", "reference", "reference_content", "seal", "table", "text", "vertical_text",
             "vision_footnote"]
# Layout settings of each pipeline: GLM-OCR glmocr/config.yaml @ 0288f96b; PaddleX configs/pipelines/PaddleOCR-VL-1.6.yaml @ ffb64904.
LAYOUT = {
    "glm": {"threshold": 0.3, "merge": {i: ("small" if l == "reference" else "large") for i, l in enumerate(V3_LABELS)},
            "filter_overlap": False},
    "paddle": {"threshold": 0.3,
               "merge": {i: ("large" if l in ("chart", "display_formula", "doc_title", "inline_formula", "paragraph_title") else "union")
                         for i, l in enumerate(V3_LABELS)},
               "filter_overlap": True},
}
# Blocks sent to the VLM (the coordinator's list plus the other text-bearing V3 classes); image/chart/seal/reference
# containers and image headers/footers are skipped.
KEEP = {"text", "paragraph_title", "doc_title", "abstract", "header", "footer", "number", "footnote", "figure_title",
        "reference_content", "display_formula", "formula_number", "table",
        "inline_formula", "algorithm", "content", "aside_text", "vertical_text", "vision_footnote"}
FORMULA = {"display_formula", "inline_formula"}
MARGIN = 4
WARMUP_IMG = common.ENGINES / "crops" / "smoke" / "lounis-p8-e3_19.png"
S2 = common.S3.parent / "stage2"  # Stage 2's part of the working directory
S2_PAGES = common.CODE.parents[1] / "stage2" / "pages.json"  # Stage 2's page list, kept with its code


# ---------------------------------------------------------------------------------------------- memory
class _PMC(ctypes.Structure):
    _fields_ = [("cb", wt.DWORD), ("PageFaultCount", wt.DWORD), ("PeakWorkingSetSize", ctypes.c_size_t),
                ("WorkingSetSize", ctypes.c_size_t), ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPagedPoolUsage", ctypes.c_size_t), ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                ("QuotaNonPagedPoolUsage", ctypes.c_size_t), ("PagefileUsage", ctypes.c_size_t),
                ("PeakPagefileUsage", ctypes.c_size_t)]


def proc_mem(handle: int) -> tuple[int, int]:
    """(peak working set, working set) in bytes, from GetProcessMemoryInfo."""
    pmc = _PMC()
    pmc.cb = ctypes.sizeof(pmc)
    ctypes.windll.psapi.GetProcessMemoryInfo(wt.HANDLE(handle), ctypes.byref(pmc), pmc.cb)
    return int(pmc.PeakWorkingSetSize), int(pmc.WorkingSetSize)


class Sampler(threading.Thread):
    """Working set every 0.5 s, max kept (the way ../mem_sampler.ps1 measures)."""

    def __init__(self, handle: int):
        super().__init__(daemon=True)
        self.handle, self.max, self.stop_ev = handle, 0, threading.Event()

    def run(self):
        while not self.stop_ev.is_set():
            try:
                self.max = max(self.max, proc_mem(self.handle)[1])
            except Exception:
                pass
            self.stop_ev.wait(0.5)


# ---------------------------------------------------------------------------------------------- llama-server
def chat(srv, png: bytes, prompt: str, max_tokens: int, sampling: dict) -> tuple[dict, float]:
    url = "data:image/png;base64," + base64.b64encode(png).decode("ascii")
    content = [{"type": "image_url", "image_url": {"url": url}}]
    if prompt:
        content.append({"type": "text", "text": prompt})
    body = {"messages": [{"role": "user", "content": content}], "max_tokens": max_tokens,
            "cache_prompt": False, "stream": False, **sampling}
    req = urllib.request.Request(srv.base + "/v1/chat/completions", data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    t0 = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=7200) as r:
            out = json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"llama-server {e.code}: {e.read()[:500]!r}") from e
    return out, time.perf_counter() - t0


def unpack(r: dict) -> dict:
    ch = r["choices"][0]
    u = r.get("usage") or {}
    t = r.get("timings") or {}
    return {"text": ch["message"].get("content") or "", "finish_reason": ch.get("finish_reason"),
            "tokens_in": u.get("prompt_tokens"), "tokens_out": u.get("completion_tokens"),
            "server": {k: t.get(k) for k in ("prompt_n", "prompt_ms", "predicted_n", "predicted_ms")}}


def png_bytes(img) -> bytes:
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


# ---------------------------------------------------------------------------------------------- layout
_DET = {}


def detect_blocks(bgr, layout: str):
    """PP-DocLayoutV3 with the pipeline's settings; boxes come back in the model's reading order
    (rapid-layout sorts by the 7th output column, V3's order head)."""
    import detect

    if "handler" not in _DET:
        from rapid_layout import RapidLayout

        spec = detect.MODELS["pp-doclayoutv3"]
        eng = RapidLayout(model_type=spec["rapid"], model_dir_or_path=str(common.DL / spec["files"][0]), conf_thresh=0.3, iou_thresh=0.5)
        _DET["handler"] = eng.model_handler.model_processors
    h = _DET["handler"]
    cfg = LAYOUT[layout]
    ori, inputs = h.preprocess(bgr)
    preds = h.format_output(h.session(inputs))
    boxes, scores, names = h.postprocess(batch_outputs=preds, datas=[ori], threshold=cfg["threshold"], layout_nms=True,
                                         layout_unclip_ratio=(1.0, 1.0), layout_merge_bboxes_mode=cfg["merge"],
                                         layout_shape_mode="rect", filter_overlap_boxes=cfg["filter_overlap"],
                                         skip_order_labels=None)
    return [(str(n), [float(v) for v in b], float(s)) for b, s, n in zip(boxes, scores, names)]


def crop_margin_paddle(img):
    """PaddleOCR-VL pipeline's crop_margin for formula blocks: trim to the ink (normalised grey < 200)."""
    import numpy as np

    g = np.asarray(img.convert("L"))
    mx, mn = int(g.max()), int(g.min())
    if mx == mn:
        return img
    d = ((g.astype(np.int32) - mn) * 255 // (mx - mn)).astype(np.uint8)
    ys, xs = np.nonzero(d < 200)
    if xs.size == 0:
        return img
    c = img.crop((int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1))
    return c if c.width > 2 and c.height > 2 else img


# ---------------------------------------------------------------------------------------------- image size
# What llama.cpp b12359 feeds the vision encoder, from each mmproj's hparams (printed by llama-server -lv 4):
# qwen-style smart_resize to multiples of 28 px within [min, max] pixels (PaddleOCR-VL max 1003520, GLM-OCR max 3211264,
# dots.ocr max 11289600), LightOnOCR longest side <= 1540 then up to multiples of 28, Granite-Docling (idefics3)
# longest side 2048 split into 512 px tiles plus one global 512 px view (64 tokens each).
def _smart_resize(w, h, factor, min_px, max_px):
    import math

    hb, wb = max(factor, round(h / factor) * factor), max(factor, round(w / factor) * factor)
    if hb * wb > max_px:
        beta = math.sqrt(h * w / max_px)
        hb, wb = math.floor(h / beta / factor) * factor, math.floor(w / beta / factor) * factor
    elif hb * wb < min_px:
        beta = math.sqrt(min_px / (h * w))
        hb, wb = math.ceil(h * beta / factor) * factor, math.ceil(w * beta / factor) * factor
    return wb, hb


def llama_image(model: str, w: int, h: int) -> dict:
    import math

    if model in ("paddleocr-vl-1.6", "glm-ocr", "dots-ocr"):
        lim = {"paddleocr-vl-1.6": (112896, 1003520), "glm-ocr": (6272, 3211264), "dots-ocr": (3136, 11289600)}[model]
        wb, hb = _smart_resize(w, h, 28, *lim)
        return {"px": [wb, hb], "image_tokens": (wb // 28) * (hb // 28)}
    if model == "lightonocr-2":
        s = min(1.0, 1540 / max(w, h))
        cw, ch = math.ceil(w * s / 28), math.ceil(h * s / 28)
        return {"px": [cw * 28, ch * 28], "image_tokens": cw * ch}
    if model == "granite-docling":
        s = min(1.0, 2048 / max(w, h))
        tw, th = math.ceil(w * s / 512), math.ceil(h * s / 512)
        return {"px": [tw * 512, th * 512], "tiles": [tw, th], "image_tokens": (tw * th + 1) * 64,
                "note": "tiles of 512 px plus a 512 px global view, 64 tokens each"}
    return {}


# ---------------------------------------------------------------------------------------------- runs
def run_page(srv, spec: dict, page: str, img_path: Path) -> dict:
    from PIL import Image

    png = img_path.read_bytes()
    w, h = Image.open(img_path).size
    attempts = []
    for cap in (8192, 16384):
        r, dt = chat(srv, png, spec["prompt"], cap, spec["sampling"])
        a = unpack(r)
        a.update({"seconds": round(dt, 3), "max_tokens": cap})
        attempts.append(a)
        if a["finish_reason"] != "length":
            break
    final = attempts[-1]
    out = {"engine": spec["name"], "mode": "page", "model": spec["model"], "prompt": spec["prompt"], "text": final["text"],
           "seconds": final["seconds"], "tokens_out": final["tokens_out"], "tokens_in": final["tokens_in"],
           "finish_reason": final["finish_reason"], "max_tokens": final["max_tokens"], "image_px": [w, h],
           "image_px_llama": llama_image(spec["model"], w, h),
           "server": final["server"], "sampling": spec["sampling"]}
    if len(attempts) > 1:
        out["first_attempt"] = {k: attempts[0][k] for k in ("text", "seconds", "tokens_out", "finish_reason", "max_tokens")}
    return out


def run_regions(srv, spec: dict, page: str, img_path: Path) -> dict:
    import numpy as np
    from PIL import Image

    t_page = time.perf_counter()
    img = Image.open(img_path).convert("L")
    bgr = np.stack([np.asarray(img)] * 3, axis=-1)
    t0 = time.perf_counter()
    blocks = detect_blocks(bgr, spec["layout"])
    det_s = time.perf_counter() - t0
    regions, skipped = [], []
    for cls, box, score in blocks:
        if cls not in KEEP:
            skipped.append({"cls": cls, "box": [round(v, 1) for v in box], "score": round(score, 4)})
            continue
        kind = "formula" if cls in FORMULA else "table" if cls == "table" else "text"
        prompt = spec["prompts"][kind]
        x0, y0, x1, y1 = box
        cb = [max(0, int(x0) - MARGIN), max(0, int(y0) - MARGIN), min(img.width, int(round(x1)) + MARGIN), min(img.height, int(round(y1)) + MARGIN)]
        crop = img.crop(cb)
        if spec["layout"] == "paddle" and kind == "formula":
            crop = crop_margin_paddle(crop)
        r, dt = chat(srv, png_bytes(crop), prompt, spec["max_tokens"], spec["sampling"])
        a = unpack(r)
        regions.append({"cls": cls, "box": [round(v, 1) for v in box], "score": round(score, 4), "crop_box": cb,
                        "crop_px": [crop.width, crop.height], "image_px_llama": llama_image(spec["model"], crop.width, crop.height).get("px"),
                        "prompt": prompt, "text": a["text"], "seconds": round(dt, 3),
                        "tokens_out": a["tokens_out"], "tokens_in": a["tokens_in"], "finish_reason": a["finish_reason"]})
    secs = time.perf_counter() - t_page
    caps = [r for r in regions if r["finish_reason"] == "length"]
    return {"engine": spec["name"], "mode": "regions", "model": spec["model"], "prompt": spec["prompts"], "regions": regions,
            "skipped_blocks": skipped, "seconds": round(secs, 3), "detect_seconds": round(det_s, 3),
            "tokens_out": sum(r["tokens_out"] or 0 for r in regions),
            "finish_reason": "length" if caps else "stop", "n_length": len(caps), "image_px": [img.width, img.height],
            "reading_order": "PP-DocLayoutV3 order head (rapid-layout sorts boxes by it)",
            "layout": {**{k: v for k, v in LAYOUT[spec["layout"]].items() if k != "merge"},
                       "merge_large": [V3_LABELS[i] for i, m in LAYOUT[spec["layout"]]["merge"].items() if m == "large"],
                       "merge_small": [V3_LABELS[i] for i, m in LAYOUT[spec["layout"]]["merge"].items() if m == "small"],
                       "margin_px": MARGIN, "max_tokens": spec["max_tokens"]},
            "sampling": spec["sampling"]}


def start_server(spec, device, threads, ctx, log_name, extra=None):
    port = vlm.free_port()
    log = common.ENGINES / "logs" / log_name
    srv = vlm.Server(spec["model"], device, threads, port, ctx, log, spec.get("special", False), extra)
    srv.wait_ready()
    if spec["mode"] == "regions":  # build the layout model and run it once, so page timings exclude session creation
        import numpy as np

        detect_blocks(np.full((800, 600, 3), 255, np.uint8), spec["layout"])
    warm = spec["prompt"] if spec["mode"] == "page" else spec["prompts"]["text"]
    chat(srv, WARMUP_IMG.read_bytes(), warm, 64, spec["sampling"])  # small warm-up, not timed
    return srv


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--engine", required=True, choices=sorted(ENGINES))
    ap.add_argument("--device", required=True, choices=["cpu", "gpu"])
    ap.add_argument("pages", nargs="*")
    ap.add_argument("--threads", default="default", choices=["1", "default"])
    ap.add_argument("--pages-json", default=str(S2_PAGES))
    ap.add_argument("--img-dir", default=str(S2 / "img"))
    ap.add_argument("--out", default=str(S2 / "vlm_raw"))
    ap.add_argument("--timing-out", default=None, help="CPU timing mode: fresh server per page, peak memory, files <engine>-t<N>-<page>.json")
    ap.add_argument("--ctx", type=int, default=None)
    ap.add_argument("--server-arg", action="append", default=[], help="extra llama-server argument (repeatable), e.g. --server-arg=-lv --server-arg=4")
    a = ap.parse_args(argv)

    spec = {"name": a.engine, **ENGINES[a.engine]}
    pages = a.pages or [p["id"] for p in json.loads(Path(a.pages_json).read_text(encoding="utf-8"))["pages"]]
    img_dir = Path(a.img_dir)
    threads = 1 if a.threads == "1" else None
    if a.device == "cpu":
        common.set_threads(threads)  # ONNX detection follows the llama.cpp thread setting
    ctx = a.ctx or (32768 if spec["mode"] == "page" else 12288)
    run = run_page if spec["mode"] == "page" else run_regions
    tag = f"{a.engine}-{a.device}" + (f"-t{a.threads}" if a.device == "cpu" else "")

    if a.timing_out:
        tdir = Path(a.timing_out)
        for p in pages:
            srv = start_server(spec, a.device, threads, ctx, f"pageocr-{tag}-{p}.log", a.server_arg)
            samp = Sampler(srv.proc._handle)
            samp.start()
            try:
                res = run(srv, spec, p, img_dir / f"{p}.png")
                peak, _ = proc_mem(srv.proc._handle)
            finally:
                samp.stop_ev.set()
                samp.join()
                srv.stop()
            res.update({"device": a.device, "threads": a.threads, "build": vlm.BUILD})
            name = f"{a.engine}-t{a.threads}-{p}"
            common.write_json(tdir / f"{name}.raw.json", res)
            common.write_json(tdir / f"{name}.json", {
                "engine": a.engine, "page": p, "device": a.device, "threads": a.threads,
                "llama_threads": "-t 1 -tb 1" if threads == 1 else "llama.cpp default (12)",
                "seconds": res["seconds"], "tokens_out": res["tokens_out"], "finish_reason": res["finish_reason"],
                "peak_ws_MB": round(peak / 1e6, 1), "peak_ws_sampled_MB": round(samp.max / 1e6, 1),
                "peak_note": "peak_ws_MB = Windows PeakWorkingSetSize of llama-server over its life (load, warm-up, this page); "
                             "peak_ws_sampled_MB = max of 0.5 s samples during this page",
                "raw": f"{name}.raw.json", "machine": common.machine()})
            print(f"{name}: {res['seconds']:.1f}s {res['tokens_out']} tok [{res['finish_reason']}] peak {peak / 1e6:.0f} MB", flush=True)
        return

    out_dir = Path(a.out) / a.engine
    srv = start_server(spec, a.device, threads, ctx, f"pageocr-{tag}.log", a.server_arg)
    summary = {"engine": a.engine, "model": spec["model"], "device": a.device, "build": vlm.BUILD, "ctx": ctx,
               "server_args": srv.args[1:], "gpu": vlm.gpu_name() if a.device == "gpu" else None, "pages": {}}
    try:
        for p in pages:
            res = run(srv, spec, p, img_dir / f"{p}.png")
            res.update({"device": a.device, "threads": a.threads, "build": vlm.BUILD})
            common.write_json(out_dir / f"{p}.json", res)
            summary["pages"][p] = {k: res.get(k) for k in ("seconds", "tokens_out", "tokens_in", "finish_reason", "max_tokens", "n_length")}
            extra = f" ({len(res['regions'])} regions, det {res['detect_seconds']}s)" if "regions" in res else f" in {res['tokens_in']}"
            print(f"{a.engine} {p}: {res['seconds']:.1f}s {res['tokens_out']} tok [{res['finish_reason']}]{extra}", flush=True)
    finally:
        srv.stop()
    common.write_json(Path(a.out).parent / "vlm_timing" / f"_run_{tag}.json", summary)


if __name__ == "__main__":
    main()
