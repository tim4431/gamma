"""Check recognize.py's torch-free Pix2Text MFR decode against the library path pix2text uses
(TrOCRProcessor + optimum ORTModelForVision2Seq.generate, use_cache=False, greedy).

    venv-texteller\\Scripts\\python.exe -I tools\\check_pix2text.py crops\\smoke

(venv-texteller\\ and crops\\smoke are under $OCR_STUDY_DIR\\stage3\\engines.)
Compares the raw decoded string with latex_raw in $OCR_STUDY_DIR/stage3/rec/pix2text-mfr-1.5/<crop>.json.
"""
import json
import os
import sys
from pathlib import Path

os.environ["HF_HUB_OFFLINE"] = "1"
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import common  # noqa: E402

MODEL = common.DL / "pix2text-mfr-1.5"
REC = common.REC_OUT / "pix2text-mfr-1.5"


def main(crops_dir):
    from optimum.onnxruntime import ORTModelForVision2Seq
    from PIL import Image
    from transformers import TrOCRProcessor

    proc = TrOCRProcessor.from_pretrained(str(MODEL))
    model = ORTModelForVision2Seq.from_pretrained(str(MODEL), use_cache=False, use_io_binding=False, provider="CPUExecutionProvider")
    for c in sorted(Path(crops_dir).glob("*.png")):
        img = Image.open(c).convert("RGB")
        pv = proc(images=[img], return_tensors="pt").pixel_values
        out = model.generate(pv, max_new_tokens=1024)
        text = proc.batch_decode(out, skip_special_tokens=True)[0]
        mine = json.loads((REC / f"{c.stem}.json").read_text(encoding="utf-8"))["latex_raw"]
        print(c.stem, "identical" if text == mine else f"DIFFER\n  lib : {text[:200]}\n  mine: {mine[:200]}")


if __name__ == "__main__":
    main(sys.argv[1])
