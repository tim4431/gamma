"""Formula recognition: cropped equation image -> LaTeX, on the CPU.

    venv\\Scripts\\python.exe -I recognize.py --engine <name> <crops_dir> [--threads 1|default|both]

Engines:
  pp-formulanet-plus-m  PP-FormulaNet_plus-M, MinerU-4's ONNX (graph-internal decode loop), ONNX Runtime
  pp-formulanet-plus-s  PP-FormulaNet_plus-S, ONNX converted here with paddle2onnx (work/pp-formulanet_plus-s.onnx)
  pix2text-mfr-1.5      Pix2Text MFR 1.5 (TrOCR) ONNX encoder + decoder, greedy decode in numpy
  rapid-latex-ocr       RapidLaTeXOCR 0.0.9 (pix2tex/LaTeX-OCR ONNX port)
  texteller             TexTeller 3 (PyTorch, CPU); runs under venv-texteller (re-executes itself there)

Run from this folder; venv\\ is $OCR_STUDY_DIR\\stage3\\engines\\venv (common.py).
For every PNG in crops_dir (crop id = file stem) writes $OCR_STUDY_DIR/stage3/rec/<engine>/<crop_id>.json:
  {"latex", "seconds", "seconds_by_threads": {"1": s, "default": s}, "latex_raw", ...}
"latex" is the engine's own post-processed output with one outer \\[ \\] / $$ $$ pair removed;
"latex_raw" is the decoded string before the engine's post-processing where the runner sees it
(PP-FormulaNet, Pix2Text), else the engine output as returned.
"seconds" is the default-thread time when that mode ran, else the one-thread time. Each mode
loads the model and makes one warm-up call on the first crop before timing; seconds cover the
engine call on the decoded image (pre-processing + inference + decoding + post-processing).
A summary goes to rec/<engine>/_timing.json.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common  # noqa: E402

ENGINES = {
    "pp-formulanet-plus-m": {
        "files": ["mineru4-onnx-formulanet_plus_m/PP-FormulaNet_plus-M.onnx", "mineru4-onnx-formulanet_plus_m/PP-FormulaNet_plus-M_inference.yml"],
        "licence": "Apache-2.0 (PaddlePaddle PP-FormulaNet_plus-M; ONNX by opendatalab/MinerU-4_models_onnx)",
        "url": "https://huggingface.co/opendatalab/MinerU-4_models_onnx (rev 358310b4f64b95f9fefc372ad899356e4111f376) MFR/pp_formulanet_plus_m/",
    },
    "pp-formulanet-plus-s": {
        "files": ["../work/pp-formulanet_plus-s/inference.onnx", "pp-formulanet_plus-s-paddle/inference.yml"],
        "licence": "Apache-2.0 (PaddlePaddle PP-FormulaNet_plus-S; converted locally with paddle2onnx 2.1.0)",
        "url": "https://huggingface.co/PaddlePaddle/PP-FormulaNet_plus-S (rev 3d46f557e3a1752f4bf81202395af3b5ecfadfd2)",
    },
    "pix2text-mfr-1.5": {
        "files": ["pix2text-mfr-1.5"],
        "licence": "MIT",
        "url": "https://huggingface.co/breezedeus/pix2text-mfr-1.5 (rev 1cef9f0bdcd6a4c63df7de1311fb0894593340cc)",
    },
    "rapid-latex-ocr": {
        "files": ["rapid-latex-ocr-v0.0.0"],
        "licence": "MIT (GitHub repo; the PyPI metadata says Apache-2.0); weights from pix2tex/LaTeX-OCR, MIT",
        "url": "https://github.com/RapidAI/RapidLaTeXOCR/releases/tag/v0.0.0 (package rapid-latex-ocr 0.0.9)",
    },
    "texteller": {
        "files": ["texteller-3"],
        "licence": "Apache-2.0",
        "url": "https://huggingface.co/OleehyO/TexTeller (rev 7b96df06b9d81cdb129c3bef68b7250bc3e2b0ea; package texteller 1.0.2)",
    },
}


# ---------------------------------------------------------------------------
# PP-FormulaNet_plus (PaddleX FormulaRecognition pre/post-processing, ported from
# paddlex/inference/models/formula_recognition/processors.py and MinerU-4's ONNX wrapper)
# ---------------------------------------------------------------------------
class PPFormulaNet:
    EOS = 2

    def __init__(self, onnx_path: Path, yml_path: Path):
        import onnxruntime as ort
        import yaml
        from tokenizers import Tokenizer

        self.sess = ort.InferenceSession(str(onnx_path))
        self.inp = self.sess.get_inputs()[0].name
        cfg = yaml.safe_load(yml_path.read_text(encoding="utf-8"))
        self.size = tuple(cfg["PreProcess"]["transform_ops"][0]["UniMERNetImgDecode"]["input_size"])
        self.tok = Tokenizer.from_str(json.dumps(cfg["PostProcess"]["character_dict"]["fast_tokenizer_file"]))

    @staticmethod
    def _crop_margin(img):
        import numpy as np

        data = np.array(img.convert("L")).astype(np.uint8)
        mx, mn = data.max(), data.min()
        if mx == mn:
            return img
        data = (data - mn) / (mx - mn) * 255
        ys, xs = np.nonzero(data < 200)
        return img.crop((int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1))

    def preprocess(self, rgb):
        import cv2
        import numpy as np
        from PIL import Image, ImageOps

        img = self._crop_margin(Image.fromarray(rgb).convert("RGB"))
        h_in, w_in = self.size
        # resize the shorter side to min(input_size), bilinear; then fit inside input_size
        short = min(self.size)
        w, h = img.size
        if w <= h:
            nw, nh = short, int(short * h / w)
        else:
            nw, nh = int(short * w / h), short
        img = img.resize((nw, nh), resample=2)
        img.thumbnail((w_in, h_in))
        dw, dh = w_in - img.width, h_in - img.height
        img = np.array(ImageOps.expand(img, (dw // 2, dh // 2, dw - dw // 2, dh - dh // 2)))  # black fill, as PaddleX
        mean, std = np.float32(0.7931), np.float32(0.1738)
        img = (img.astype("float32") * np.float32(1 / 255.0) - mean) / std
        g = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        return g[np.newaxis, np.newaxis, :, :].astype(np.float32)

    def decode(self, ids) -> tuple[str, str, int]:
        from ftfy import fix_text

        ids = [int(x) for x in ids]
        if self.EOS in ids:
            ids = ids[: ids.index(self.EOS) + 1]
        raw = self.tok.decode(ids, skip_special_tokens=True)
        text = re.sub(r"\\text\s*{([^{}]*[\u4e00-\u9fff]+[^{}]*)}", r"\1", raw).replace('"', "")
        text = fix_text(text)
        return paddlex_normalize(text), raw, len(ids)

    def __call__(self, rgb):
        ids = self.sess.run(None, {self.inp: self.preprocess(rgb)})[0][0]
        latex, raw, n = self.decode(ids)
        return {"latex": latex, "latex_raw": raw, "tokens": n}


def paddlex_normalize(s: str) -> str:
    """PaddleX UniMERNetDecode.normalize: drop spaces LaTeX does not need."""
    text_reg = r"(\\(operatorname|mathrm|text|mathbf)\s?\*? {.*?})"
    letter = r"[a-zA-Z]"
    noletter = r"[\W_^\d]"
    names = []
    for x in re.findall(text_reg, s):
        for m in re.findall(r"(\\[a-zA-Z]+)\s(?=\w)|\\[a-zA-Z]+\s(?=})", x[0]):
            if m not in ["\\operatorname", "\\mathrm", "\\text", "\\mathbf"] and m.strip() != "":
                s = s.replace(m, m + "XXXXXXX")
                s = s.replace(" ", "")
                names.append(s)
    if names:
        s = re.sub(text_reg, lambda match: str(names.pop(0)), s)
    news = s
    while True:
        s = news
        news = re.sub(r"(?!\\ )(%s)\s+?(%s)" % (noletter, noletter), r"\1\2", s)
        news = re.sub(r"(?!\\ )(%s)\s+?(%s)" % (noletter, letter), r"\1\2", news)
        news = re.sub(r"(%s)\s+?(%s)" % (letter, noletter), r"\1\2", news)
        if news == s:
            break
    return s.replace("XXXXXXX", " ")


# ---------------------------------------------------------------------------
# Pix2Text MFR 1.5: what pix2text.LatexOCR does (TrOCRProcessor + ORTModelForVision2Seq.generate,
# greedy, use_cache=False, max_new_tokens=1024, then LatexOCR.post_process), without torch.
# ---------------------------------------------------------------------------
class Pix2TextMFR:
    def __init__(self, model_dir: Path, max_new_tokens: int = 1024):
        import onnxruntime as ort
        from tokenizers import Tokenizer

        self.enc = ort.InferenceSession(str(model_dir / "encoder_model.onnx"))
        self.dec = ort.InferenceSession(str(model_dir / "decoder_model.onnx"))
        pp = json.loads((model_dir / "preprocessor_config.json").read_text(encoding="utf-8"))
        gc = json.loads((model_dir / "generation_config.json").read_text(encoding="utf-8"))
        self.size = (pp["size"]["width"], pp["size"]["height"])
        self.resample = pp["resample"]
        self.mean, self.std, self.scale = pp["image_mean"][0], pp["image_std"][0], pp["rescale_factor"]
        self.start, self.eos = gc["decoder_start_token_id"], gc["eos_token_id"]
        self.max_new = max_new_tokens
        self.tok = Tokenizer.from_file(str(model_dir / "tokenizer.json"))

    def __call__(self, rgb):
        import numpy as np
        from PIL import Image

        img = Image.fromarray(rgb).convert("RGB").resize(self.size, resample=self.resample)
        x = (np.asarray(img, dtype=np.float32) * np.float32(self.scale) - np.float32(self.mean)) / np.float32(self.std)
        x = x.transpose(2, 0, 1)[np.newaxis]
        hid = self.enc.run(None, {"pixel_values": x})[0]
        ids = [self.start]
        for _ in range(self.max_new):
            logits = self.dec.run(None, {"input_ids": np.array([ids], dtype=np.int64), "encoder_hidden_states": hid})[0]
            nxt = int(logits[0, -1].argmax())
            ids.append(nxt)
            if nxt == self.eos:
                break
        raw = self.tok.decode(ids, skip_special_tokens=True)
        return {"latex": pix2text_post_process(raw), "latex_raw": raw, "tokens": len(ids) - 1}


def pix2text_post_process(text: str) -> str:
    """pix2text.latex_ocr.LatexOCR.post_process (v1.1.7)."""
    import string

    text = re.sub(r"^\^\s*{\s*(.*?)\s*}", r"\1", text)
    text = re.sub(r"^_\s*{\s*(.*?)\s*}", r"\1", text).strip()
    ws = [r"\\ +", r"\\quad\s*", r"\\qquad\s*", r"\\,\s*", r"\\:\s*", r"\\;\s*", r"\\enspace\s*", r"\\thinspace\s*", r"\\!\s*"]
    text = re.sub(r"(?:" + "|".join(ws) + r")+$", "", text).strip()
    for a, b in [(r"\\\.", r"\\ ."), (r"\\=", r"\\ ="), (r"\\-", r"\\ -"), (r"\\~", r"\\ ~")]:
        text = re.sub(a, b, text)
    empty = [r"\\hat\s*{\s*}", r"\^\s*{\s*}", r"_\s*{\s*}", r"\\text\s*{\s*}", r"\\tilde\s*{\s*}", r"\\bar\s*{\s*}",
             r"\\vec\s*{\s*}", r"\\acute\s*{\s*}", r"\\grave\s*{\s*}", r"\\breve\s*{\s*}", r"\\overline\s*{\s*}",
             r"\\dot\s*{\s*}", r"\\ddot\s*{\s*}", r"\\widehat\s*{\s*}", r"\\widetilde\s*{\s*}"]
    for _ in range(10):
        new = text
        for p in empty:
            new = re.sub(p, "", new)
        new = new.strip()
        if new == text:
            break
        text = new

    def find_lr(latex, lr):
        infos = []
        n = len(lr) + 1
        for m in re.finditer(rf"\\{lr}\s*\S", latex):
            start, end = m.span()
            if latex[end - 1] in string.ascii_letters:
                continue
            while latex[end - 1] in ("\\", " "):
                end += 1
                while end < len(latex) and latex[end].isalpha():
                    end += 1
            infos.append({"str": latex[start + n:end].strip(), "start": start, "end": end})
            infos.sort(key=lambda x: x["start"])
        return infos

    pairs = [("", ""), ("(", ")"), ("\\{", "."), ("⟮", "⟯"), ("[", "]"), ("⟨", "⟩"), ("{", "}"), ("⌈", "⌉"), ("┌", "┐"),
             ("⌊", "⌋"), ("└", "┘"), ("⎰", "⎱"), ("lt", "gt"), ("lang", "rang"), ("langle", "rangle"), ("lbrace", "rbrace"),
             ("lBrace", "rBrace"), ("lbracket", "rbracket"), ("lceil", "rceil"), ("lcorner", "rcorner"), ("lfloor", "rfloor"),
             ("lgroup", "rgroup"), ("lmoustache", "rmoustache"), ("lparen", "rparen"), ("lvert", "rvert"), ("lVert", "rVert")]

    def match_lr(left_str, right_str):
        left_str = left_str.strip().replace(" ", "")[len("left") + 1:]
        right_str = right_str.strip().replace(" ", "")[len("right") + 1:]
        while left_str and right_str and left_str[0] == right_str[0]:
            left_str, right_str = left_str[1:], right_str[1:]
        return (left_str, right_str) in pairs

    lefts, rights = find_lr(text, "left"), find_lr(text, "right")
    for li in lefts:
        for ri in rights:
            if not ri.get("matched") and ri["start"] > li["start"] and match_lr(ri["str"], li["str"]):
                li["matched"] = ri["matched"] = True
                break
    for li in lefts:
        if not li.get("matched"):
            text = text[: li["start"]] + " " * 5 + text[li["start"] + 5:]
    for ri in rights:
        if not ri.get("matched"):
            text = text[: ri["start"]] + " " * 6 + text[ri["start"] + 6:]
    text = re.sub(r"\s+", " ", text).strip()
    # remove_unnecessary_spaces
    text = re.sub(r"\\([a-zA-Z]+) (?=[a-zA-Z])", r"\\\1 ", text)
    text = re.sub(r"\\([a-zA-Z]+)\s+(?![a-zA-Z])", r"\\\1", text)
    text = re.sub(r"(\{)\s+", r"\1", text)
    text = re.sub(r"\s+(\})", r"\1", text)
    text = re.sub(r"(?<=[^\\])\s*([+\-=])\s*", r"\1", text)
    text = re.sub(r"\s*(\^|\_)\s*", r"\1", text)
    return text.strip()


# ---------------------------------------------------------------------------
class RapidLatexOCR:
    """rapid_latex_ocr 0.0.9 pins numpy<2 while rapid-layout needs numpy>=2. The only break under
    numpy 2.5 is int() of a 1-element array in LaTeXOCR.loop_image_resizer, so that method is
    replaced by a copy with .item(); everything else is the package's own code. Its decoder samples
    at temperature 1e-5 (effectively greedy); the RNG is seeded per call for repeatability."""

    def __init__(self, d: Path):
        import types

        import numpy as np
        from PIL import Image
        from rapid_latex_ocr import LaTeXOCR

        self.np = np
        self.eng = LaTeXOCR(
            image_resizer_path=str(d / "image_resizer.onnx"),
            encoder_path=str(d / "encoder.onnx"),
            decoder_path=str(d / "decoder.onnx"),
            tokenizer_json=str(d / "tokenizer.json"),
        )

        def loop_image_resizer(self, img):
            pillow_img = Image.fromarray(img)
            pad_img = self.pre_pro.pad(pillow_img)
            input_image = self.pre_pro.minmax_size(pad_img).convert("RGB")
            r, w, h = 1, input_image.size[0], input_image.size[1]
            for _ in range(10):
                h = int(h * r)
                final_img, pad_img = self.pre_process(input_image, r, w, h)
                resizer_res = self.image_resizer([final_img.astype(np.float32)])[0]
                argmax_idx = int(np.argmax(resizer_res, axis=-1).ravel()[0])
                w = (argmax_idx + 1) * 32
                if w == pad_img.size[0]:
                    break
                r = w / pad_img.size[0]
            return final_img

        self.eng.loop_image_resizer = types.MethodType(loop_image_resizer, self.eng)

    def __call__(self, rgb):
        self.np.random.seed(0)
        latex, _ = self.eng(rgb)
        return {"latex": latex}


class TexTeller:
    def __init__(self, d: Path):
        os.environ.setdefault("HF_HUB_OFFLINE", "1")
        os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
        import torch
        from texteller import img2latex, load_model, load_tokenizer

        self.torch = torch
        self.img2latex = img2latex
        self.model = load_model(model_dir=str(d))
        self.model.eval()
        self.tok = load_tokenizer(str(d))

    def __call__(self, rgb):
        with self.torch.inference_mode():
            out = self.img2latex(self.model, self.tok, [rgb], device=self.torch.device("cpu"), out_format="latex", max_tokens=1024, num_beams=1)
        return {"latex": out[0]}


def build(engine: str):
    f = ENGINES[engine]["files"]
    if engine.startswith("pp-formulanet"):
        return PPFormulaNet((common.DL / f[0]).resolve(), common.DL / f[1])
    if engine == "pix2text-mfr-1.5":
        return Pix2TextMFR(common.DL / f[0])
    if engine == "rapid-latex-ocr":
        return RapidLatexOCR(common.DL / f[0])
    if engine == "texteller":
        return TexTeller(common.DL / f[0])
    raise ValueError(engine)


def set_engine_threads(engine: str, n: int | None):
    common.set_threads(n)
    if engine == "texteller":
        import torch

        torch.set_num_threads(n if n else (os.cpu_count() or 2) // 2)  # torch default = physical cores


def load_rgb(path: Path):
    import numpy as np
    from PIL import Image

    return np.array(Image.open(path).convert("RGB"))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--engine", required=True, choices=sorted(ENGINES))
    ap.add_argument("crops_dir")
    ap.add_argument("--threads", default="both", choices=["1", "default", "both"])
    ap.add_argument("--out", default=str(common.REC_OUT))
    a = ap.parse_args(argv)

    if a.engine == "texteller":
        try:
            import torch  # noqa: F401
        except ImportError:  # re-run under the TexTeller venv
            py = common.ENGINES / "venv-texteller" / "Scripts" / "python.exe"
            sys.exit(subprocess.call([str(py), "-I", str(Path(__file__).resolve())] + (argv or sys.argv[1:])))
    if a.engine != "texteller":
        common.patch_onnxruntime()

    crops = sorted(Path(a.crops_dir).glob("*.png"))
    if not crops:
        sys.exit(f"no PNG in {a.crops_dir}")
    imgs = {c.stem: load_rgb(c) for c in crops}
    out_dir = Path(a.out) / a.engine
    files = [common.DL / f for f in ENGINES[a.engine]["files"]]
    summary = {
        "engine": a.engine,
        "licence": ENGINES[a.engine]["licence"],
        "url": ENGINES[a.engine]["url"],
        "size_bytes": common.dir_size(*files),
        "machine": common.machine(),
        "crops_dir": str(Path(a.crops_dir).resolve()),
        "modes": {},
    }
    res: dict[str, dict] = {k: {"seconds_by_threads": {}} for k in imgs}
    first = next(iter(imgs))
    for mode, n in common.thread_modes(a.threads):
        set_engine_threads(a.engine, n)
        t0 = common.now()
        eng = build(a.engine)
        load_s = common.now() - t0
        t0 = common.now()
        eng(imgs[first])
        warm_s = common.now() - t0
        secs = []
        for cid, rgb in imgs.items():
            t0 = common.now()
            r = eng(rgb)
            dt = common.now() - t0
            r.setdefault("latex_raw", r["latex"])
            r["latex"] = common.strip_math_delims(r["latex"])  # TexTeller wraps its output in \[ \]
            secs.append(dt)
            key = "1" if n == 1 else "default"
            prev = res[cid].get("latex")
            if prev is not None and prev != r["latex"]:
                print(f"[warn] {cid}: output differs between thread modes", file=sys.stderr)
            res[cid].update(r)
            res[cid]["seconds_by_threads"][key] = round(dt, 4)
            res[cid]["seconds"] = round(dt, 4)
            print(f"{a.engine} {mode} {cid} {dt:.3f}s  {r['latex'][:110]}", flush=True)
        summary["modes"][mode] = {
            "threads": n if n is not None else "runtime default",
            "load_s": round(load_s, 3),
            "warmup_s": round(warm_s, 3),
            "mean_s_per_crop": round(sum(secs) / len(secs), 4),
            "max_s_per_crop": round(max(secs), 4),
        }
        del eng
    for cid, r in res.items():
        r["engine"] = a.engine
        common.write_json(out_dir / f"{cid}.json", {"latex": r.pop("latex"), "seconds": r.pop("seconds"), **r})
    common.write_json(out_dir / "_timing.json", summary)
    print(json.dumps(summary["modes"], indent=1))


if __name__ == "__main__":
    main()
