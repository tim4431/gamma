"""Cases flatten_vlm must turn into the truth's flat text: python -I test_flatten.py"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from flatten_vlm import latex_flat, md_flat  # noqa: E402

cases = [
    (latex_flat, r"\delta_{1}", "δ1"),
    (latex_flat, r"\Omega_2 \gg \Omega_1", "Ω2 ≫ Ω1"),
    (latex_flat, r"\sigma_{ee}^{st}", "σeest"),
    (latex_flat, r"\omega_{L1}", "ωL1"),
    (latex_flat, r"\delta_{2} = -1.5\Gamma", "δ2 = -1.5Γ"),
    (latex_flat, r"\frac{1}{2}", "1/2"),
    (latex_flat, r"\mathrm{H}_{0}", "H0"),
    (latex_flat, r"\left| \varphi_{e} \right\rangle", "| φe ⟩"),
    (md_flat, "where $\\delta_1$ and $\\Omega_2 \\gg \\Omega_1$ are", "where δ1 and Ω2 ≫ Ω1 are"),
    (md_flat, "## 3. Calculation\n\n**Fig. 2.** — Steady", "3. Calculation\nFig. 2. — Steady"),
    (md_flat, "| a | b |\n|---|---|\n| 1 | 2 |", "a b\n1 2"),
    (md_flat, "x<sub>1</sub> and <loc_12><text>word</text>", "x1 and word"),
    (md_flat, "$$\nE = mc^2\n$$", "E = mc2"),
]
bad = 0
for f, src, want in cases:
    got = f(src)
    if got != want:
        bad += 1
        print("BAD", f.__name__, repr(src), "->", repr(got), "want", repr(want))
print("all ok" if not bad else f"{bad} bad")
