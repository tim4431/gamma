"""Render the corpus pages with display math at 300 dpi grey for Stage 3."""
import json
import pymupdf
import sys as _s, pathlib as _p; _s.path.insert(0, str(_p.Path(__file__).resolve().parent))
from common import CORPUS, ROOT

FILES = {f: CORPUS / f"{f}.pdf" for f in ("lounis", "holstein", "kitagawa", "feshbach", "naca", "jdp", "annalen")}
PAGES = {
    "lounis": range(3, 16), "holstein": range(2, 17), "kitagawa": range(1, 7), "feshbach": range(3, 35),
    "naca": [9], "jdp": [33], "annalen": [3],
}
out = ROOT / "img"
out.mkdir(parents=True, exist_ok=True)
index = []
for f, pages in PAGES.items():
    doc = pymupdf.open(str(FILES[f]))
    for n in pages:
        if n > len(doc):
            continue
        page = doc[n - 1]
        pix = page.get_pixmap(dpi=300, colorspace=pymupdf.csGRAY, annots=False)
        name = f"{f}-p{n}"
        pix.save(out / f"{name}.png")
        index.append({"id": name, "file": f, "page": n, "w": page.rect.width, "h": page.rect.height, "px": [pix.width, pix.height]})
(ROOT / "pages.json").write_text(json.dumps(index, indent=0), encoding="utf-8")
print(len(index), "pages rendered")
