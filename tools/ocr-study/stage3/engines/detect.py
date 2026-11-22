"""Formula (layout) detection: page image -> boxes, through ONNX Runtime on the CPU.

    venv\\Scripts\\python.exe -I detect.py --model <name> [page ...] [--threads 1|default|both] [--conf 0.3]

Models (all ONNX, all run through rapid-layout 1.2.1's pre/post-processing):
  pp-doclayoutv3       PP-DocLayoutV3 (rapid-layout export; display_formula / inline_formula / formula_number)
  pp-doclayout-plus-l  PP-DocLayout_plus-L (PaddlePaddle's official ONNX; formula / formula_number)
  doclayout-yolo       DocLayout-YOLO DocStructBench, imgsz 1024 (rapid-layout export; isolate_formula / formula_caption)

Run from this folder; venv\\ is $OCR_STUDY_DIR\\stage3\\engines\\venv (common.py).
Pages are ids from pages.json ($OCR_STUDY_DIR/stage3/img/<page>.png); none given = every page in pages.json.
Writes $OCR_STUDY_DIR/stage3/det/<model>/<page>.json = [{"cls", "box": [x0, y0, x1, y1] (300-dpi page pixels), "score"}]
and det/<model>/_timing.json with per-page wall seconds for each thread mode (engine call
only: pre-processing + inference + post-processing on the decoded page; one warm-up call first).
Boxes are written from the last thread mode run; a mismatch between modes is reported.
By default the two PP models use rapid-layout's (PaddleX's) overlap filter, which drops an
inline_formula box more than half inside another box and any box more than 70% inside a larger
one, so inline formulas inside text paragraphs disappear (PP-DocLayout_plus-L has no inline class:
its inline formulas are 'formula' boxes inside text). --keep-inline turns the filter off.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common  # noqa: E402

common.patch_onnxruntime()  # before rapid_layout imports InferenceSession

import numpy as np  # noqa: E402

MODELS = {
    "pp-doclayoutv3": {
        "rapid": "pp_doc_layoutv3",
        "files": ["rapid-layout-pp_doc_layoutv3/pp_doc_layoutv3.onnx"],
        "licence": "Apache-2.0 (PaddlePaddle PP-DocLayoutV3; ONNX export by RapidAI/rapid-layout, Apache-2.0)",
        "url": "https://www.modelscope.cn/models/RapidAI/RapidLayout/resolve/v1.2.0/onnx/pp_doc_layout/pp_doc_layoutv3.onnx",
    },
    # PP-DocLayoutV3 made smaller by tools/shrink_layout.py (venv-quant): fp16 weights with fp32
    # inputs/outputs (62.5 MB), and dynamic int8 of its MatMul/Gemm only (103.6 MB)
    "pp-doclayoutv3-fp16": {
        "rapid": "pp_doc_layoutv3",
        "files": ["../work/pp-doclayoutv3-small/fp16.onnx"],
        "licence": "Apache-2.0 (derived from PP-DocLayoutV3)",
        "url": "tools/shrink_layout.py",
    },
    "pp-doclayoutv3-int8": {
        "rapid": "pp_doc_layoutv3",
        "files": ["../work/pp-doclayoutv3-small/int8.onnx"],
        "licence": "Apache-2.0 (derived from PP-DocLayoutV3)",
        "url": "tools/shrink_layout.py",
    },
    "pp-doclayout-plus-l": {
        "rapid": None,
        "files": ["pp-doclayout_plus-l-onnx/inference.onnx", "pp-doclayout_plus-l-onnx/inference.yml"],
        "licence": "Apache-2.0 (PaddlePaddle official ONNX)",
        "url": "https://huggingface.co/PaddlePaddle/PP-DocLayout_plus-L_onnx (rev feb74619326f634e0e883218598096a3733ad9f7)",
    },
    "doclayout-yolo": {
        "rapid": "doclayout_docstructbench",
        "files": ["rapid-layout-doclayout_docstructbench/doclayout_yolo_docstructbench_imgsz1024.onnx"],
        "licence": "AGPL-3.0 (ONNX metadata and DocLayout-YOLO code; the juliozhao HF weights card says Apache-2.0)",
        "url": "https://www.modelscope.cn/models/RapidAI/RapidLayout/resolve/v1.2.0/onnx/doclayout/doclayout_yolo_docstructbench_imgsz1024.onnx",
    },
}


class _NamedSession:
    """Session adapter for PPDocLayoutModelHandler: maps its [im_shape, image, scale_factor] list by name."""

    def __init__(self, path: Path):
        import onnxruntime as ort

        self.s = ort.InferenceSession(str(path))
        self.names = [i.name for i in self.s.get_inputs()]

    def __call__(self, inputs):
        by_name = {"im_shape": inputs[0], "image": inputs[1], "scale_factor": inputs[2]}
        return self.s.run(None, {n: by_name[n] for n in self.names})


def _pp_runner(handler, conf: float, keep_inline: bool):
    """PPDocLayoutModelHandler.__call__ with the overlap filter made optional.

    rapid-layout (like PaddleX's PaddleOCR-VL pipeline) drops an inline_formula box that lies
    more than half inside another box, i.e. inside its text paragraph; --keep-inline turns that
    filter off so inline formulas stay in the output."""

    def run(img):
        ori_data, ort_inputs = handler.preprocess(img)
        preds = handler.format_output(handler.session(ort_inputs))
        return handler.postprocess(
            batch_outputs=preds,
            datas=[ori_data],
            threshold=conf,
            layout_nms=True,
            layout_shape_mode="auto",
            filter_overlap_boxes=not keep_inline,
            skip_order_labels=None,
        )

    return run


def build(model: str, conf: float, iou: float, keep_inline: bool = False):
    """Return a callable bgr_image -> (boxes, scores, class_names)."""
    spec = MODELS[model]
    path = common.DL / spec["files"][0]
    if spec["rapid"]:
        from rapid_layout import RapidLayout

        eng = RapidLayout(model_type=spec["rapid"], model_dir_or_path=str(path), conf_thresh=conf, iou_thresh=iou)
        if spec["rapid"].startswith("pp_doc_layout"):
            return _pp_runner(eng.model_handler.model_processors, conf, keep_inline)

        def run(img):
            r = eng.model_handler(img)
            return r.boxes, r.scores, r.class_names

        return run

    import yaml
    from rapid_layout.model_handler.pp_doc_layout import PPDocLayoutModelHandler

    labels = yaml.safe_load((common.DL / spec["files"][1]).read_text(encoding="utf-8"))["label_list"]
    handler = PPDocLayoutModelHandler(labels, conf, iou, _NamedSession(path))
    return _pp_runner(handler, conf, keep_inline)


def load_page(page: str) -> np.ndarray:
    from rapid_layout.utils.load_image import LoadImage

    return LoadImage()(str(common.IMG / f"{page}.png"))


def to_records(boxes, scores, names):
    out = []
    if boxes is None or len(boxes) == 0:
        return out
    for b, s, c in zip(boxes, scores, names):
        out.append({"cls": str(c), "box": [round(float(v), 1) for v in b], "score": round(float(s), 4)})
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--model", required=True, choices=sorted(MODELS))
    ap.add_argument("pages", nargs="*")
    ap.add_argument("--threads", default="both", choices=["1", "default", "both"])
    ap.add_argument("--conf", type=float, default=0.3, help="score threshold (default 0.3; rapid-layout's own default is 0.5)")
    ap.add_argument("--iou", type=float, default=0.5)
    ap.add_argument("--keep-inline", action="store_true", help="PP models: turn off the overlap filter, keeping formula boxes inside text boxes")
    ap.add_argument("--out", default=str(common.DET_OUT))
    a = ap.parse_args(argv)

    pages = a.pages or [p["id"] for p in json.loads((common.S3 / "pages.json").read_text(encoding="utf-8"))]
    out_dir = Path(a.out) / a.model
    images = {p: load_page(p) for p in pages}
    timing = {
        "model": a.model,
        "files": {f: (common.DL / f).stat().st_size for f in MODELS[a.model]["files"]},
        "licence": MODELS[a.model]["licence"],
        "url": MODELS[a.model]["url"],
        "conf": a.conf,
        "iou": a.iou,
        "keep_inline": a.keep_inline,
        "machine": common.machine(),
        "modes": {},
        "pages": {p: {} for p in pages},
    }
    results: dict[str, list] = {}
    for mode, n in common.thread_modes(a.threads):
        common.set_threads(n)
        t0 = common.now()
        run = build(a.model, a.conf, a.iou, a.keep_inline)
        load_s = common.now() - t0
        t0 = common.now()
        run(images[pages[0]])
        warm_s = common.now() - t0
        secs = []
        for p in pages:
            t0 = common.now()
            boxes, scores, names = run(images[p])
            dt = common.now() - t0
            secs.append(dt)
            recs = to_records(boxes, scores, names)
            if p in results and results[p] != recs:
                print(f"[warn] {p}: boxes differ between thread modes", file=sys.stderr)
            results[p] = recs
            timing["pages"][p][mode] = round(dt, 4)
            nf = sum(1 for r in recs if "formula" in r["cls"])
            print(f"{a.model} {mode} {p}: {len(recs)} boxes ({nf} formula-class) {dt:.3f}s", flush=True)
        timing["modes"][mode] = {
            "ort_threads": n if n is not None else "onnxruntime default",
            "load_s": round(load_s, 3),
            "warmup_s": round(warm_s, 3),
            "mean_s_per_page": round(float(np.mean(secs)), 4),
            "median_s_per_page": round(float(np.median(secs)), 4),
        }
    for p, recs in results.items():
        common.write_json(out_dir / f"{p}.json", recs)
    common.write_json(out_dir / "_timing.json", timing)
    print(json.dumps(timing["modes"], indent=1))


if __name__ == "__main__":
    main()
