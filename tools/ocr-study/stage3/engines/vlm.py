"""Small document VLMs through llama.cpp's llama-server (official b12359 = v1.0.0 Windows builds).

Crop mode (equation crop -> LaTeX):
    venv\\Scripts\\python.exe -I vlm.py --model <name> --device cpu|gpu <crops_dir> [--threads 1|default|both]
Page mode (whole page -> the model's own page output, for later):
    venv\\Scripts\\python.exe -I vlm.py --model <name> --device cpu|gpu --page [page ...]

Models (GGUF + mmproj, both from the publishers' Hugging Face repos):
  paddleocr-vl-1.6  PaddlePaddle/PaddleOCR-VL-1.6-GGUF (BF16 model + mmproj)  crop prompt "Formula Recognition:"
  glm-ocr           ggml-org/GLM-OCR-GGUF Q8_0 + mmproj Q8_0                   crop prompt "Formula Recognition:"
  granite-docling   ibm-granite/granite-docling-258M-GGUF BF16 + mmproj f16      crop prompt "Convert formula to LaTeX."
  lightonocr-2      ggml-org/LightOnOCR-2-1B-GGUF Q8_0 + mmproj Q8_0             no text prompt (image only, as its card)
  dots-ocr          ggml-org/dots.ocr-GGUF Q8_0 + mmproj Q8_0                    crop prompt "Extract the text content from this image." (prompt_ocr)

The runner starts llama-server on 127.0.0.1 at the first free port from 9100 (never 9001/5173/9002),
waits for /health, sends each image as a data-URL to /v1/chat/completions with the prompt,
temperature 0, top_k 1, max_tokens (default 1024 for crops, 8192 for pages), cache_prompt false,
then stops the server. One warm-up request precedes the timed ones.
Run from this folder; venv\\ is $OCR_STUDY_DIR\\stage3\\engines\\venv, and llama.cpp and the
GGUF files are under its dl\\ (common.py).
Writes $OCR_STUDY_DIR/stage3/rec/<model>-<device>/<crop_id>.json  {"latex", "seconds", "raw", "seconds_by_threads", "tokens_in",
"tokens_out", "finish_reason", "server": {...llama.cpp timings}}  (page mode: <model>-<device>-page/<page>.json
with "text" instead of "latex") and _timing.json. On cpu, --threads both (default) runs the server
twice: -t 1 -tb 1, then llama.cpp's default thread count; on gpu all layers and the mmproj are offloaded.
"""
from __future__ import annotations

import argparse
import base64
import json
import re
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common  # noqa: E402

BUILD = "b12359"  # default llama.cpp build; a model can name another one with "build"


def exe(device: str, build: str = BUILD) -> Path:
    return common.DL / f"llamacpp-{'cpu' if device == 'cpu' else 'cuda12'}-{build}" / "bin" / "llama-server.exe"


EXE = {"cpu": exe("cpu"), "gpu": exe("gpu")}
MODELS = {
    "paddleocr-vl-1.6": {
        "gguf": "paddleocr-vl-1.6-gguf/PaddleOCR-VL-1.6-GGUF.gguf",
        "mmproj": "paddleocr-vl-1.6-gguf/PaddleOCR-VL-1.6-GGUF-mmproj.gguf",
        "crop_prompt": "Formula Recognition:",
        "page_prompt": "OCR:",
        "licence": "Apache-2.0",
        "url": "https://huggingface.co/PaddlePaddle/PaddleOCR-VL-1.6-GGUF (rev 511b09642bb324401f15f97cc23bc67e8f0a291d)",
    },
    "glm-ocr": {
        "gguf": "glm-ocr-gguf/GLM-OCR-Q8_0.gguf",
        "mmproj": "glm-ocr-gguf/mmproj-GLM-OCR-Q8_0.gguf",
        "crop_prompt": "Formula Recognition:",
        "page_prompt": "Text Recognition:",
        "licence": "MIT (model card zai-org/GLM-OCR)",
        "url": "https://huggingface.co/ggml-org/GLM-OCR-GGUF (rev 65a42de1148dbed2297e922b5dbc7d9b70c36578)",
    },
    "granite-docling": {
        "gguf": "granite-docling-258m-gguf/granite-docling-258M-BF16.gguf",
        "mmproj": "granite-docling-258m-gguf/mmproj-model-f16.gguf",
        "crop_prompt": "Convert formula to LaTeX.",
        "page_prompt": "Convert this page to docling.",
        "licence": "Apache-2.0 (model card ibm-granite/granite-docling-258M)",
        "url": "https://huggingface.co/ibm-granite/granite-docling-258M-GGUF (rev e1e344cb19bae46a1d5edb4f04a27ebe0c268f72)",
    },
    "lightonocr-2": {
        "gguf": "lightonocr-2-1b-gguf/LightOnOCR-2-1B-Q8_0.gguf",
        "mmproj": "lightonocr-2-1b-gguf/mmproj-LightOnOCR-2-1B-Q8_0.gguf",
        # The card (lightonai/LightOnOCR-2-1B) sends the image alone, no text; its sampling is temperature 0.2,
        # top_p 0.9, which this runner replaces with greedy decoding like the other readers.
        "crop_prompt": "",
        "page_prompt": "",
        "licence": "Apache-2.0 (model card lightonai/LightOnOCR-2-1B)",
        "url": "https://huggingface.co/ggml-org/LightOnOCR-2-1B-GGUF (rev 7d4daf70c2856a155b1413def00f4ae59843a9cf), from lightonai/LightOnOCR-2-1B (rev be7c41313c30e940fe5571de59e5fabc779e00c8)",
    },
    "dots-ocr": {
        "gguf": "dots-ocr-gguf/dots.ocr-Q8_0.gguf",
        "mmproj": "dots-ocr-gguf/mmproj-dots.ocr-Q8_0.gguf",
        # dots_ocr/utils/prompts.py "prompt_ocr" (text only, no layout JSON); "prompt_layout_all_en" is the page mode.
        "crop_prompt": "Extract the text content from this image.",
        "page_prompt": "Extract the text content from this image.",
        "licence": "MIT (model card rednote-hilab/dots.ocr, repo studio-dots-ai/dots.ocr)",
        "url": "https://huggingface.co/ggml-org/dots.ocr-GGUF (rev 2c093a32ca360a396bc6d87d60408636130b9d9b), from rednote-hilab/dots.ocr (rev c0111ce6bc07803dbc267932ffef0ae3a51dc951)",
    },
}
FORBIDDEN_PORTS = {9001, 5173, 9002}


def free_port(start: int = 9100) -> int:
    for p in range(start, start + 200):
        if p in FORBIDDEN_PORTS:
            continue
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(("127.0.0.1", p))
                return p
            except OSError:
                continue
    raise RuntimeError("no free port from 9100")


class Server:
    def __init__(self, model: str, device: str, threads: int | None, port: int, ctx: int, log: Path, special: bool = False,
                 extra: list[str] | None = None):
        spec = MODELS[model]
        self.port = port
        args = [
            str(exe(device, spec.get("build", BUILD))),
            "-m", str(common.DL / spec["gguf"]),
            "--mmproj", str(common.DL / spec["mmproj"]),
            "--host", "127.0.0.1",
            "--port", str(port),
            "-c", str(ctx),
            "-np", "1",
            "--temp", "0",
            "--no-webui",
        ]
        if device == "gpu":
            args += ["-ngl", "999"]
        else:
            args += ["-ngl", "0"]
        if threads is not None:
            args += ["-t", str(threads), "-tb", str(threads)]
        if special:
            args += ["--special"]  # print special tokens (Granite-Docling's DocTags element tags are special tokens)
        args += list(extra or [])
        self.args = args
        log.parent.mkdir(parents=True, exist_ok=True)
        self.logf = open(log, "w", encoding="utf-8", errors="replace")
        self.proc = subprocess.Popen(args, stdout=self.logf, stderr=subprocess.STDOUT, cwd=str(common.ENGINES))
        self.base = f"http://127.0.0.1:{port}"

    def wait_ready(self, timeout: float = 600) -> float:
        t0 = time.perf_counter()
        while time.perf_counter() - t0 < timeout:
            if self.proc.poll() is not None:
                raise RuntimeError(f"llama-server exited with {self.proc.returncode}; see {self.logf.name}")
            try:
                with urllib.request.urlopen(self.base + "/health", timeout=2) as r:
                    if r.status == 200:
                        return time.perf_counter() - t0
            except (urllib.error.URLError, ConnectionError, TimeoutError, OSError):
                pass
            time.sleep(0.5)
        raise TimeoutError("llama-server did not become ready")

    def chat(self, png: bytes, prompt: str, max_tokens: int) -> dict:
        url = "data:image/png;base64," + base64.b64encode(png).decode("ascii")
        content = [{"type": "image_url", "image_url": {"url": url}}]
        if prompt:  # LightOnOCR-2 takes the image alone
            content.append({"type": "text", "text": prompt})
        body = {
            "messages": [{"role": "user", "content": content}],
            "temperature": 0,
            "top_k": 1,
            "max_tokens": max_tokens,
            "cache_prompt": False,
            "stream": False,
        }
        req = urllib.request.Request(self.base + "/v1/chat/completions", data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=3600) as r:
            return json.loads(r.read().decode("utf-8"))

    def stop(self):
        if self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=20)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait(timeout=20)
        self.logf.close()


DOCTAG = re.compile(r"</?(doctag|formula|text|code|page_header|page_footer|section_header_level_\d|caption|paragraph)>|<loc_\d+>|<\|end_of_text\|>|<end_of_utterance>")


FENCE = re.compile(r"^```[A-Za-z]*\s*\n?(.*?)\n?```$", re.S)
MD_MODELS = {"lightonocr-2", "dots-ocr"}  # answer in markdown: fences, several $$ blocks, inline $…$


def md_math(t: str) -> str:
    """Markdown answer -> bare LaTeX, for LightOnOCR-2 and dots.ocr. Removes code-fence lines (```, ```latex),
    lines that are only $$ / \\[ / \\], every remaining $$, every unescaped $, and blank lines; the pieces of a
    formula split over several $$ blocks are joined by newlines (whitespace in math mode). Text and HTML
    (<sub>, <sup>) the model wrote are left as they are."""
    keep = []
    for ln in t.splitlines():
        s = ln.strip()
        if re.fullmatch(r"```[A-Za-z]*", s) or s in ("$$", "\\[", "\\]"):
            continue
        keep.append(ln)
    t = "\n".join(keep)
    t = re.sub(r"```[A-Za-z]*", "", t)
    t = t.replace("$$", "")
    t = re.sub(r"(?<!\\)\$", "", t)
    t = "\n".join(ln.strip() for ln in t.splitlines() if ln.strip())
    return t.strip()


def to_latex(model: str, raw: str) -> str:
    """raw -> LaTeX: drop Granite's DocTags, one markdown code fence, then one outer $$ / \\[ \\] / $ pair;
    LightOnOCR-2 and dots.ocr go through md_math instead."""
    t = raw.strip()
    if model in MD_MODELS:
        return md_math(t)
    if model == "granite-docling":
        t = DOCTAG.sub("", t).strip()
    m = FENCE.match(t)
    if m:
        t = m.group(1).strip()
    return common.strip_math_delims(t)


def gpu_name() -> str:
    try:
        return subprocess.run(["nvidia-smi", "--query-gpu=name,driver_version,memory.total", "--format=csv,noheader"],
                              capture_output=True, text=True, timeout=20).stdout.strip()
    except Exception:
        return "unknown"


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--model", required=True, choices=sorted(MODELS))
    ap.add_argument("--device", required=True, choices=["cpu", "gpu"])
    ap.add_argument("inputs", nargs="*", help="crop mode: one folder of PNGs; page mode: page ids (default: all of pages.json)")
    ap.add_argument("--page", action="store_true", help="whole-page mode with the model's page prompt")
    ap.add_argument("--threads", default=None, choices=["1", "default", "both"], help="cpu only (default both for crops, default for pages)")
    ap.add_argument("--max-tokens", type=int, default=None)
    ap.add_argument("--ctx", type=int, default=None)
    ap.add_argument("--prompt", default=None, help="override the model's documented prompt")
    ap.add_argument("--port", type=int, default=None)
    ap.add_argument("--special", action="store_true", help="llama-server --special (on by default for granite-docling --page, so DocTags keep their <text>/<formula> tags)")
    ap.add_argument("--out", default=str(common.REC_OUT))
    a = ap.parse_args(argv)

    spec = MODELS[a.model]
    if a.page:
        ids = a.inputs or [p["id"] for p in json.loads((common.S3 / "pages.json").read_text(encoding="utf-8"))]
        items = {p: common.IMG / f"{p}.png" for p in ids}
        prompt = a.prompt or spec["page_prompt"]
        max_tokens = a.max_tokens or 8192
        ctx = a.ctx or 16384
        out_dir = Path(a.out) / f"{a.model}-{a.device}-page"
        key = "text"
    else:
        if len(a.inputs) != 1:
            sys.exit("crop mode takes one crops folder")
        items = {p.stem: p for p in sorted(Path(a.inputs[0]).glob("*.png"))}
        prompt = a.prompt or spec["crop_prompt"]
        max_tokens = a.max_tokens or 1024
        ctx = a.ctx or 4096
        out_dir = Path(a.out) / f"{a.model}-{a.device}"
        key = "latex"
    if not items:
        sys.exit("nothing to do")
    threads = a.threads or ("default" if (a.page or a.device == "gpu") else "both")
    modes = [("gpu", None)] if a.device == "gpu" else common.thread_modes(threads)
    pngs = {k: p.read_bytes() for k, p in items.items()}

    build = spec.get("build", BUILD)
    ver = subprocess.run([str(exe(a.device, build)), "--version"], capture_output=True, text=True, timeout=60)
    summary = {
        "model": a.model,
        "device": a.device,
        "gpu": gpu_name() if a.device == "gpu" else None,
        "llama_cpp": (ver.stdout + ver.stderr).strip().splitlines()[0] if (ver.stdout + ver.stderr).strip() else BUILD,
        "build": build,
        "files": {f: (common.DL / f).stat().st_size for f in (spec["gguf"], spec["mmproj"])},
        "licence": spec["licence"],
        "url": spec["url"],
        "prompt": prompt,
        "max_tokens": max_tokens,
        "ctx": ctx,
        "machine": common.machine(),
        "modes": {},
    }
    res: dict[str, dict] = {k: {"seconds_by_threads": {}} for k in items}
    first = next(iter(items))
    for mode, n in modes:
        port = a.port or free_port()
        log = common.ENGINES / "logs" / f"{a.model}-{a.device}{'-page' if a.page else ''}-{mode}.log"
        special = a.special or (a.page and a.model == "granite-docling")
        srv = Server(a.model, a.device, n, port, ctx, log, special)
        try:
            ready_s = srv.wait_ready()
            t0 = time.perf_counter()
            srv.chat(pngs[first], prompt, max_tokens)
            warm_s = time.perf_counter() - t0
            secs, toks = [], []
            for k, png in pngs.items():
                t0 = time.perf_counter()
                r = srv.chat(png, prompt, max_tokens)
                dt = time.perf_counter() - t0
                ch = r["choices"][0]
                raw = ch["message"].get("content") or ""
                tim = r.get("timings", {})
                out = res[k]
                out.update({
                    key: to_latex(a.model, raw) if key == "latex" else raw,
                    "raw": raw,
                    "finish_reason": ch.get("finish_reason"),
                    "tokens_in": (r.get("usage") or {}).get("prompt_tokens"),
                    "tokens_out": (r.get("usage") or {}).get("completion_tokens"),
                    "server": {kk: tim.get(kk) for kk in ("prompt_n", "prompt_ms", "predicted_n", "predicted_ms", "predicted_per_second")},
                    "seconds": round(dt, 3),
                    "prompt": prompt,
                })
                out["seconds_by_threads"][{"t1": "1", "tdefault": "default"}.get(mode, mode)] = round(dt, 3)
                secs.append(dt)
                toks.append(out["tokens_out"] or 0)
                print(f"{a.model} {a.device} {mode} {k} {dt:.2f}s {out['tokens_out']} tok [{out['finish_reason']}]  {ascii(out[key][:100])}", flush=True)  # ascii: the console is GBK
            summary["modes"][mode] = {
                "threads": n if n is not None else ("llama.cpp default" if a.device == "cpu" else "-ngl 999"),
                "port": port,
                "server_args": srv.args[1:],
                "ready_s": round(ready_s, 2),
                "warmup_s": round(warm_s, 2),
                "mean_s": round(sum(secs) / len(secs), 3),
                "max_s": round(max(secs), 3),
                "mean_tokens_out": round(sum(toks) / len(toks), 1),
            }
        finally:
            srv.stop()
    for k, r in res.items():
        common.write_json(out_dir / f"{k}.json", {key: r.pop(key), "seconds": r.pop("seconds"), **r})
    common.write_json(out_dir / "_timing.json", summary)
    print(json.dumps(summary["modes"], indent=1))


if __name__ == "__main__":
    main()
