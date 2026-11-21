"""Shared paths and helpers for the Stage 3 engine runners (detect.py, recognize.py, vlm.py).

Runners are started with `venv\\Scripts\\python.exe -I <runner>.py ...`; -I keeps the
script folder off sys.path, so each runner adds this folder explicitly before importing.

The code lives here (CODE); what the engines read and write lives under the
study's working directory (../common.py, $OCR_STUDY_DIR/stage3 = S3): ENGINES is
$OCR_STUDY_DIR/stage3/engines, which holds the venvs (venv, venv-texteller,
venv-p2o, venv-quant), the downloads (dl), the converted models (work), the
llama-server logs (logs) and the smoke crops (crops/smoke).
"""
from __future__ import annotations

import importlib.util
import json
import os
import platform
import time
from pathlib import Path

CODE = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location("ocr_study_stage3_common", CODE.parent / "common.py")
_stage3 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_stage3)
S3 = _stage3.ROOT
ENGINES = _stage3.ENGINES
DL = ENGINES / "dl"
IMG = S3 / "img"
DET_OUT = S3 / "det"
REC_OUT = S3 / "rec"

# ---------------------------------------------------------------------------
# ONNX Runtime thread control. Packages such as rapid_layout and rapid_latex_ocr build
# their own SessionOptions, so the thread count is forced by wrapping InferenceSession
# before they import it. ORT_THREADS = None keeps ONNX Runtime's default (intra-op
# threads = physical cores, here 12); an int pins intra- and inter-op threads.
# ---------------------------------------------------------------------------
ORT_THREADS: int | None = None
_patched = False


def patch_onnxruntime() -> None:
    global _patched
    if _patched:
        return
    import onnxruntime as ort

    base = ort.InferenceSession

    class ThreadedSession(base):  # type: ignore[misc, valid-type]
        def __init__(self, path_or_bytes, sess_options=None, providers=None, provider_options=None, **kw):
            if sess_options is None:
                sess_options = ort.SessionOptions()
            sess_options.log_severity_level = 3  # errors only (paddle2onnx graphs warn a lot)
            if ORT_THREADS is not None:
                so = sess_options
                so.intra_op_num_threads = ORT_THREADS
                so.inter_op_num_threads = ORT_THREADS
                so.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
                sess_options = so
            if providers is None:
                providers = ["CPUExecutionProvider"]
            super().__init__(path_or_bytes, sess_options, providers, provider_options, **kw)

    ort.InferenceSession = ThreadedSession
    _patched = True


def set_threads(n: int | None) -> None:
    """n = 1 for the single-thread run, None for the runtime default."""
    global ORT_THREADS
    ORT_THREADS = n
    try:
        import cv2

        cv2.setNumThreads(1 if n == 1 else -1)
    except Exception:
        pass


def thread_modes(arg: str) -> list[tuple[str, int | None]]:
    return {"1": [("t1", 1)], "default": [("tdefault", None)], "both": [("t1", 1), ("tdefault", None)]}[arg]


def now() -> float:
    return time.perf_counter()


def write_json(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=1), encoding="utf-8")


def machine() -> dict:
    return {
        "cpu": "AMD Ryzen 9 9900X (12 cores / 24 threads)",
        "os": platform.platform(),
        "python": platform.python_version(),
        "logical_cpus": os.cpu_count(),
    }


def strip_math_delims(s: str) -> str:
    """Drop one outer pair of display/inline math delimiters (\\[ \\], $$ $$, \\( \\), $ $)."""
    t = (s or "").strip()
    for a, b in (("\\[", "\\]"), ("$$", "$$"), ("\\(", "\\)"), ("$", "$")):
        if t.startswith(a) and t.endswith(b) and len(t) >= len(a) + len(b):
            return t[len(a): len(t) - len(b)].strip()
    return t


def dir_size(*paths: Path) -> int:
    total = 0
    for p in paths:
        p = Path(p)
        if p.is_file():
            total += p.stat().st_size
        elif p.is_dir():
            total += sum(f.stat().st_size for f in p.rglob("*") if f.is_file())
    return total
