"""Pairs the normalizer must call equal (or not): python -I test_tokens.py"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from score_latex import tokens  # noqa: E402

same = [
    (r"\Phi_ {0} = \left( \begin{array}{c} u _ {0} \\ u _ {1} \end{array} \right),", r"\Phi_0 = \begin{pmatrix} u_0 \\ u_1 \end{pmatrix},"),
    (r"\begin{array}{l} a = b \\ c \end{array}", r"a=b\\c"),
    (r"\begin{array}{r l}{x}&{{}}\\ {=y}\end{array}", r"x\\=y"),
    (r"\overrightarrow{p}", r"\vec{p}"),
    (r"\frac{a}{b}", r"\dfrac{a}{b}"),
    (r"x^{\prime}", r"x'"),
    (r"\mathrm{d}x", r"dx"),
    (r"\left\{ a \right\}", r"\{a\}"),
    (r"{N \over 2}", r"\frac{N}{2}"),
    (r"a = {b+c \over d}", r"a=\frac{b+c}{d}"),
    (r"x \over y", r"\frac{x}{y}"),
    (r"a \\ x \over y", r"a \\ \frac{x}{y}"),
    (r"{\bf AB}", r"\mathbf{AB}"),
    (r"{\cal H}\Psi", r"\mathcal{H}\Psi"),
    (r"\omega^2_M", r"\omega_{M}^{2}"),
    (r"\omega^{2}_{\rm ho}", r"\omega_{\mathrm{ho}}^2"),
    (r"\omega^2_{\rm ho}{\langle r^2 \rangle \over \langle r^2 \rangle - {N\over 2}}",
     r"\omega_{\mathrm{ho}}^{2}\frac{\langle r^{2}\rangle}{\langle r^{2}\rangle-\frac{N}{2}}"),
]
differ = [(r"\frac{a}{b}", r"\frac{b}{a}"), (r"x_{12}", r"x_{1}2"), (r"\sqrt{x+1}", r"\sqrt{x}+1"), (r"a\\b", r"ab"),
          (r"\{a\}", r"a"), (r"{a \over b}", r"\frac{b}{a}"), (r"x^2_M", r"x^M_2"), (r"{\bf A}B", r"\mathbf{AB}")]
ok = True
for a, b in same:
    if tokens(a) != tokens(b):
        ok = False
        print("should agree:", tokens(a), tokens(b))
for a, b in differ:
    if tokens(a) == tokens(b):
        ok = False
        print("should differ:", a, b, tokens(a))
print("all ok" if ok else "FAILED")
