"""Compare the two LaTeX transcription passes for a list of equation ids.

  python -I compare_gt.py <id> [<id> ...]

Two readings that agree after score_latex.normalize are accepted: the
final $OCR_STUDY_DIR/stage3/gt_latex/<id>.tex is pass A's (pass B's when
only it compiles). For each disagreement, both readings are rendered to
adj/<id>-A.png and -B.png beside the crop eq/<id>.png, and listed in
adj/<first id>.txt with the token difference, for a person to settle."""

import difflib
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from common import ROOT  # noqa: E402
from render_latex import render  # noqa: E402
from score_latex import tokens  # noqa: E402

S = ROOT
ids = sys.argv[1:]
A, B, FINAL, ADJ = S / "gtA", S / "gtB", S / "gt_latex", S / "adj"
FINAL.mkdir(exist_ok=True)
ADJ.mkdir(exist_ok=True)
agreed, todo, lines = 0, 0, []
for i in ids:
    a = (A / f"{i}.tex").read_text(encoding="utf-8").strip() if (A / f"{i}.tex").exists() else ""
    b = (B / f"{i}.tex").read_text(encoding="utf-8").strip() if (B / f"{i}.tex").exists() else ""
    ta, tb = tokens(a), tokens(b)
    if a and ta == tb:
        ok_a = render(a, str(ADJ / f"{i}-A.png"))
        (FINAL / f"{i}.tex").write_text(a if ok_a or not render(b, str(ADJ / f"{i}-B.png")) else b, encoding="utf-8")
        agreed += 1
        continue
    todo += 1
    ok_a = bool(a) and render(a, str(ADJ / f"{i}-A.png"))
    ok_b = bool(b) and render(b, str(ADJ / f"{i}-B.png"))
    diff = [d for d in difflib.ndiff(ta, tb) if d[:1] in "+-"]
    lines.append(f"[{i}] crop: eq\\{i}.png  A ({'compiles' if ok_a else 'does NOT compile'}): adj\\{i}-A.png  "
                 f"B ({'compiles' if ok_b else 'does NOT compile'}): adj\\{i}-B.png\n  A: {a}\n  B: {b}\n"
                 f"  token differences (- A only, + B only): {' '.join(diff[:60])}\n")
(ADJ / f"{ids[0]}.txt").write_text("".join(lines), encoding="utf-8")
print(f"{agreed} agreed, {todo} to settle; list: adj\\{ids[0]}.txt")
