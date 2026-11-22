"""Tesseract's lines (with words) for every Stage 3 page, in parallel processes.

  python -I tess_lines.py <tessdata dir>"""
import json, os, sys
from concurrent.futures import ProcessPoolExecutor
os.environ["OMP_THREAD_LIMIT"] = "1"
import sys as _s, pathlib as _p; _s.path.insert(0, str(_p.Path(__file__).resolve().parent))
from common import ROOT
TESS = sys.argv[1]

def one(pid):
    from PIL import Image
    from tesserocr import PSM, RIL, PyTessBaseAPI, iterate_level
    out = ROOT / "lines" / f"{pid}.json"
    if out.exists():
        return pid
    with PyTessBaseAPI(path=TESS, lang="eng+fra+deu", psm=PSM.AUTO) as api:
        api.SetImage(Image.open(ROOT / "img" / f"{pid}.png"))
        api.Recognize()
        lines, cur = [], None
        for w in iterate_level(api.GetIterator(), RIL.WORD):
            text = w.GetUTF8Text(RIL.WORD)
            if text is None:
                continue
            if cur is None or w.IsAtBeginningOf(RIL.TEXTLINE):
                cur = {"words": []}
                lines.append(cur)
            if text.strip():
                cur["words"].append({"text": text, "box": list(w.BoundingBox(RIL.WORD)), "conf": round(w.Confidence(RIL.WORD), 1)})
        lines = [l for l in lines if l["words"]]
        for l in lines:
            xs0, ys0, xs1, ys1 = zip(*(w["box"] for w in l["words"]))
            l["box"] = [min(xs0), min(ys0), max(xs1), max(ys1)]
            l["text"] = " ".join(w["text"] for w in l["words"])
    out.write_text(json.dumps(lines, ensure_ascii=False), encoding="utf-8")
    return pid

if __name__ == "__main__":
    (ROOT / "lines").mkdir(parents=True, exist_ok=True)
    pages = [p["id"] for p in json.loads((ROOT / "pages.json").read_text(encoding="utf-8"))]
    with ProcessPoolExecutor(10) as ex:
        for pid in ex.map(one, pages):
            pass
    print(len(pages), "pages read")
