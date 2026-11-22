"""Check of vlm.to_latex wrapper stripping (no model). With a folder argument, also lists what
to_latex makes of every raw answer in it that still contains $, ``` or a blank line afterwards."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import vlm  # noqa: E402

cases = [
    ("glm-ocr", "\n$$\nz\n$$", "z"),
    ("paddleocr-vl-1.6", "\\[ a+b \\]", "a+b"),
    ("granite-docling", "<loc_0><loc_0><loc_500><loc_500>a _ { 1 }", "a _ { 1 }"),
    ("dots-ocr", "$$\nx^2\n$$", "x^2"),
    ("lightonocr-2", "```latex\n\\frac{a}{b}\n```", "\\frac{a}{b}"),
    ("lightonocr-2", "```latex\na = b,\n$$\n\n$$\nc = d\n$$", "a = b,\nc = d"),
    ("lightonocr-2", "$h_{t_1} = h_{t_2}$  [adiab]", "h_{t_1} = h_{t_2}  [adiab]"),
    ("dots-ocr", "$$ =C_{6} $$\n\n$$ =4CO_{2} $$", "=C_{6}\n=4CO_{2}"),
    ("dots-ocr", "\\$5 and $x$", "\\$5 and x"),
]
bad = 0
for m, raw, want in cases:
    got = vlm.to_latex(m, raw)
    bad += got != want
    print("ok " if got == want else "BAD", m, repr(raw), "->", repr(got))
print("all ok" if not bad else f"{bad} bad")

for d in sys.argv[1:]:
    model = Path(d).name.rsplit("-", 1)[0]
    for f in sorted(Path(d).glob("*.json")):
        if f.name.startswith("_"):
            continue
        raw = json.loads(f.read_text(encoding="utf-8"))["raw"]
        out = vlm.to_latex(model, raw)
        if "$" in out.replace("\\$", "") or "```" in out or "\n\n" in out:
            print("LEFT", f.name, ascii(out[:160]))
