"""Check: every GT record is consistent with its PNGs, and 10 random GT LaTeX
strings re-rendered from the JSON reproduce the clean PNG pixel for pixel
(the set is $OCR_STUDY_DIR/stage3/synth)."""
import json, random, sys
from pathlib import Path
import numpy as np
from PIL import Image

sys.path[:0] = [str(Path(__file__).resolve().parent), str(Path(__file__).resolve().parent.parent)]
import render
from common import SYNTH

BASE = SYNTH
idx = json.loads((BASE / "index.json").read_text(encoding="utf-8"))["equations"]
bad = 0
for e in idx:
    g = json.loads((BASE / e["gt"]).read_text(encoding="utf-8"))
    c = Image.open(BASE / g["clean_png"]); s = Image.open(BASE / g["scan_png"])
    if c.size != (g["width_px"], g["height_px"]) or s.size != (g["scan_width_px"], g["scan_height_px"]) or s.mode != "1":
        bad += 1; print("inconsistent", g["id"])
print("records", len(idx), "inconsistent", bad)

random.seed(int(sys.argv[1]) if len(sys.argv) > 1 else 42)
res = []
for e in random.sample(idx, 10):
    g = json.loads((BASE / e["gt"]).read_text(encoding="utf-8"))
    a, err = render.render(g["latex"], BASE / "work" / "check" / g["id"])
    ref = np.asarray(Image.open(BASE / g["clean_png"]))
    same = a is not None and a.shape == ref.shape and bool((a == ref).all())
    diff = None if a is None or a.shape != ref.shape else int((a != ref).sum())
    res.append({"id": g["id"], "identical": same, "shape": None if a is None else list(a.shape),
                "ref_shape": list(ref.shape), "differing_pixels": diff, "error": err})
    print(g["id"], "identical" if same else "DIFFERENT", diff, err)
(BASE / "logs" / "check_rerender.json").write_text(json.dumps(res, indent=1), encoding="utf-8")

# the degraded PNGs are reproducible from the clean PNG and the recorded seed
import degrade
mism = 0
for e in idx:
    g = json.loads((BASE / e["gt"]).read_text(encoding="utf-8"))
    s, _ = degrade.degrade(np.asarray(Image.open(BASE / g["clean_png"])), g["degradation"]["seed"])
    if not (s == np.asarray(Image.open(BASE / g["scan_png"]).convert("L"))).all():
        mism += 1
print("scan reproducible from seed:", len(idx) - mism, "/", len(idx))
