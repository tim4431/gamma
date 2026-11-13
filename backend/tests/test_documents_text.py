"""The text reader of a document (gamma/documents/text.py,
docs/dev/documents.md "Text extraction"): the index's units by format, the
chat's streamed windows and outline, a hit's line. The decoding rule's
cases are tests/shared/textdecode.json's (test_shared_fixtures.py)."""

import builtins
import time
from pathlib import Path

import pytest

from gamma import pdf_text
from gamma.documents import text

PAPER = Path(__file__).parent / "samples" / "synctex" / "paper" / "v1" / "main.tex"


def _file(tmp_path, name, content, encoding="utf-8") -> Path:
    path = tmp_path / name
    path.write_bytes(content if isinstance(content, bytes) else content.encode(encoding))
    return path


def test_a_run_and_a_page_are_units_of_one_size():
    assert text.RUN_LINES * text.LINE_CHARS == text.PAGE_CHARS


# --- extract -------------------------------------------------------------------------

def test_a_tex_file_is_runs_of_lines(tmp_path):
    lines = [f"\\section{{Part {i}}}" if i % 10 == 1 else f"Line {i} of   the  text." for i in range(1, 101)]
    got = text.extract(_file(tmp_path, "main.tex", "\r\n".join(lines) + "\r\n"), "text", name="main.tex")
    assert [u.no for u in got.units] == [1, 2, 3]
    assert got.units[2].text.split("\n") == [f"Line {i} of the text." if i % 10 != 1 else f"\\section{{Part {i}}}"
                                             for i in range(81, 101)]
    assert text.unit_lines(3, got.lines) == (81, 100) and text.unit_lines(1, got.lines) == (1, 40)
    assert (got.format, got.lines, got.truncated, got.encoding, got.bad) == ("text", 100, False, "utf-8", False)
    assert got.chars == sum(len(u.text) for u in got.units)


def test_a_run_of_empty_lines_is_dropped_but_numbered(tmp_path):
    content = "first\n" + "\n" * 79 + "  \n" * 20 + "line 101\n"
    got = text.extract(_file(tmp_path, "notes.txt", content), "text", name="notes.txt")
    assert [u.no for u in got.units] == [1, 3] and got.lines == 101
    assert got.units[1].text.split("\n") == [""] * 20 + ["line 101"]
    assert text.unit_lines(3, got.lines) == (81, 101)


def test_a_long_line_is_cut_and_the_file_is_not_truncated(tmp_path):
    got = text.extract(_file(tmp_path, "min.js", "x" * 50_000), "text", name="min.js")
    assert [(u.no, len(u.text)) for u in got.units] == [(1, text.LINE_CHARS)]
    assert got.lines == 1 and not got.truncated


def test_a_file_past_the_line_cap_is_truncated(tmp_path):
    got = text.extract(_file(tmp_path, "run.log", "".join(f"step {i}\n" for i in range(60_000))), "text", name="run.log")
    assert len(got.units) == 1_250 and got.lines == text.MAX_TEXT_LINES and got.truncated
    assert got.units[-1].text.split("\n")[-1] == "step 49999"


def test_a_file_past_the_character_cap_ends_at_its_last_whole_line(tmp_path, monkeypatch):
    monkeypatch.setattr(text, "MAX_TEXT_CHARS", 1000)
    got = text.extract(_file(tmp_path, "data.csv", "".join(f"{i:04d},{'v' * 15}\n" for i in range(200))), "text")
    assert got.truncated and got.lines == 1000 // 21
    assert got.units[-1].text.split("\n")[-1] == f"{got.lines - 1:04d},{'v' * 15}"


def test_a_utf16_log_is_read(tmp_path):
    content = b"\xff\xfe" + "".join(f"PS C:\\> step {i}\r\n" for i in range(50)).encode("utf-16-le")
    got = text.extract(_file(tmp_path, "build.log", content), "text", name="build.log")
    assert got.encoding == "utf-16-le" and got.lines == 50 and [u.no for u in got.units] == [1, 2]
    assert got.units[0].text.split("\n")[0] == "PS C:\\> step 0"


def test_a_file_with_a_nul_has_no_text(tmp_path):
    path = _file(tmp_path, "data.txt", b"a" * 100 + b"\x00" + b"b" * 100)
    assert text.extract(path, "text", name="data.txt").units == []
    assert text.decode(path.read_bytes()).binary


def test_a_secrets_file_is_never_read(tmp_path, monkeypatch):
    path = _file(tmp_path, ".env", "API_KEY=sk-123\n" * 50)
    monkeypatch.setattr(text, "open", lambda *a, **k: pytest.fail("a secrets file was opened"), raising=False)
    got = text.extract(path, "text", name="sub/.env")
    assert got.units == [] and got.lines == 0
    for name in (".env", ".ENV", ".env.local", "deploy/id_rsa", "C:\\keys\\server.PEM", "cert.p12", ".netrc"):
        assert text.is_secret(name), name
    for name in ("env.py", "main.tex", "id_rsa.pub", ".envrc", "keys.txt", ""):
        assert not text.is_secret(name), name


def test_a_pdf_is_its_pages(tmp_path, monkeypatch):
    seen = {}

    def pages(src, max_pages=0, cache=True):
        seen.update(src=src, cache=cache)
        return ["Page  one:\n3,000 qubits", "", "x" * 30_000]
    monkeypatch.setattr(pdf_text, "extract_pages", pages)
    got = text.extract(tmp_path / "a.pdf", "pdf")
    assert seen == {"src": str(tmp_path / "a.pdf"), "cache": False}
    assert [(u.no, u.text[:20]) for u in got.units] == [(1, "Page one: 3000 qubit"), (3, "x" * 20)]
    assert len(got.units[1].text) == text.PAGE_CHARS
    assert (got.format, got.lines, got.truncated, got.encoding) == ("pdf", 0, False, "")
    assert got.chars == sum(len(u.text) for u in got.units) > text.PAGE_CHARS


def test_a_picture_has_no_text(tmp_path):
    with pytest.raises(ValueError):
        text.extract(_file(tmp_path, "fig.png", b"\x89PNG"), "image")
    assert "image" not in text.INDEXED_FORMATS and set(text.INDEXED_FORMATS) == {"pdf", "text"}


# --- read_lines ----------------------------------------------------------------------

def test_a_window_of_lines(tmp_path):
    path = _file(tmp_path, "a.txt", "".join(f"line {i}\n" for i in range(1, 101)))
    w = text.read_lines(path, 10, 12)
    assert (w.lines, w.last, w.total) == (["line 10", "line 11", "line 12"], 12, 100)
    assert w.decoded.encoding == "utf-8" and not w.decoded.bad
    w = text.read_lines(path, 95)
    assert (w.lines[-1], w.last, w.total) == ("line 100", 100, 100)


def test_a_window_clamps(tmp_path):
    path = _file(tmp_path, "a.txt", "one\ntwo\nthree\n")
    assert text.read_lines(path, -5, 1).lines == ["one"]
    w = text.read_lines(path, 10)
    assert (w.lines, w.last, w.total) == ([], 9, 3)
    w = text.read_lines(path, 2, 1)
    assert (w.lines, w.last, w.total) == ([], 1, 3)


def test_a_window_stops_at_its_budget(tmp_path):
    path = _file(tmp_path, "a.txt", "".join(f"{i:09d}\n" for i in range(1, 1001)))
    w = text.read_lines(path, 1, budget=35)
    assert w.lines == ["000000001", "000000002", "000000003", "000000004"] and w.total == 1000
    long = _file(tmp_path, "b.txt", "short\n" + "y" * 30_000 + "\nafter\n")
    w = text.read_lines(long, 2, budget=10)
    assert w.lines == ["y" * text.VIEW_LINE_CHARS + text.LINE_CUT] and (w.last, w.total) == (2, 3)


def test_a_last_line_without_a_newline_counts(tmp_path):
    for content, total in (("a\nb", 2), ("a\nb\n", 2), ("", 0), ("\n", 1), ("a\r\nb\rc", 3)):
        path = _file(tmp_path, "a.txt", content)
        assert text.read_lines(path, 1).total == total, content
        assert text.read_lines(path, 1, 1).total == total, content


def test_the_count_past_the_window_is_the_files(tmp_path):
    body = "".join(f"line {i}\r\n" if i % 3 else f"line {i}\n" for i in range(1, 30_001))
    for encoding, mark in (("utf-8", b""), ("utf-16-le", b"\xff\xfe"), ("utf-16-be", b"\xfe\xff")):
        path = _file(tmp_path, "a.log", mark + body.encode(encoding) + "no newline".encode(encoding))
        w = text.read_lines(path, 1, 5)
        assert w.lines == [f"line {i}" for i in range(1, 6)] and w.total == 30_001, encoding
        assert text.read_lines(path, 29_999).lines == ["line 29999", "line 30000", "no newline"]


def test_a_lone_cr_past_the_window_counts_beside_the_lfs(tmp_path):
    """A progress bar's carriage returns in a log of \\n lines, and a \\r\\n
    split across the counted chunks: every break counted as the extractor
    and the viewer split them."""
    def lines_of(data: bytes) -> int:
        return len(text._split(data.decode("utf-8")))

    cases = [b"a\rb\n" * 50_000, b"\na\nb\n  \r\n\nb\na\r\n\r\n\r\n \r\nabba b\ra\n\r\n a\xc3\xa9"]
    # the count starts past the first chunk read; its first chunk ends on the \r of a \r\n
    split = b"head\n" + b"y" * (text._READ_CHUNK + text._COUNT_CHUNK - 6) + b"\r\nnext\rlast\n"
    assert split[text._READ_CHUNK + text._COUNT_CHUNK - 1:][:2] == b"\r\n"
    for data in cases + [split, split[:-1] + b"\r"]:
        path = _file(tmp_path, "mixed.log", data)
        assert text.read_lines(path, 1, 1).total == lines_of(data)
        assert text.read_lines(path, 1).total == lines_of(data)


def test_a_binary_file_is_refused(tmp_path):
    with pytest.raises(ValueError, match=text.NOT_TEXT):
        text.read_lines(_file(tmp_path, "a.bin", b"text\n" * 10 + b"\x00\x01"), 1)
    with pytest.raises(ValueError, match=text.NOT_TEXT):
        text.read_lines(_file(tmp_path, "b.bin", bytes(range(0x80, 0xc0)) * 20), 1)
    w = text.read_lines(_file(tmp_path, "c.txt", b"caf\xe9 au lait, and a line long enough to stay text\n"), 1)
    assert w.decoded.bad and w.lines == ["caf\ufffd au lait, and a line long enough to stay text"]


def test_a_long_file_is_counted_not_decoded(tmp_path, monkeypatch):
    """Lines 1-50 of 300,000 decode one chunk; the rest is counted from its bytes."""
    path = _file(tmp_path, "big.log", "".join(f"{i:06d} a log line of some forty bytes......\n" for i in range(300_000)))
    reads: list[tuple[int, int]] = []
    real_open = builtins.open

    class Counting:
        def __init__(self, f):
            self.f = f

        def read(self, n=-1):
            data = self.f.read(n)
            reads.append((n, len(data)))
            return data

        def readinto(self, buf):
            got = self.f.readinto(buf)
            reads.append((len(buf), got))
            return got

        def __getattr__(self, name):
            return getattr(self.f, name)

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            self.f.close()

    monkeypatch.setattr(text, "open", lambda *a, **k: Counting(real_open(*a, **k)), raising=False)
    best = float("inf")
    for _ in range(3):
        reads.clear()
        start = time.perf_counter()
        w = text.read_lines(path, 1, 50)
        best = min(best, time.perf_counter() - start)
    assert w.lines[-1].startswith("000049 ") and w.total == 300_000
    decoded = sum(got for n, got in reads if n == text._READ_CHUNK)
    counted = sum(got for n, got in reads if n == text._COUNT_CHUNK)
    assert decoded < 1_000_000 and decoded + counted == path.stat().st_size
    assert best < 0.05, f"{best * 1000:.1f} ms"


# --- outline -------------------------------------------------------------------------

def _padded(lines, to=45, filler="filler text"):
    return lines + [filler] * max(0, to - len(lines))


def test_a_short_file_has_no_outline():
    assert text.outline(["\\section{A}"] * 39, "latex") == []


def test_the_sample_papers_outline():
    lines = text.read_lines(PAPER, 1, text.MAX_TEXT_LINES, budget=text.MAX_TEXT_CHARS).lines
    got = dict(text.outline(lines, "latex"))
    assert got[13] == "\\section{Introduction}"
    assert got[20] == "\\begin{figure} … \\caption{Training and test loss per epoch, without weight dec…}"
    assert len(got[20]) == text.OUTLINE_TEXT
    assert got[27] == "\\input{sections/method}" and got[29] == "\\section{Results}"
    assert [n for n in got if n >= 29] == [29, 51]
    assert dict(text.outline(lines, "latex", from_line=28)) == {29: "\\section{Results}", 51: "\\section{Conclusion}"}


def test_latex_headings_carry_their_labels():
    lines = _padded(["\\begin{abstract}", "We measure.", "\\end{abstract}", "\\section{Intro}\\label{sec:intro}",
                     "% \\section{Commented out}", "\\subsection*{Noise model}", "\\label{sec:noise}",
                     "\\begin{table}", "\\caption{Rates}", "\\end{table}", "\\begin{figure*}", "\\end{figure*}",
                     "\\include{appendix}", "\\bibliography{refs}"])
    assert text.outline(lines, "latex") == [
        (1, "\\begin{abstract}"), (4, "\\section{Intro} [sec:intro]"), (6, "\\subsection{Noise model} [sec:noise]"),
        (8, "\\begin{table} … \\caption{Rates}"), (11, "\\begin{figure*}"), (13, "\\include{appendix}"),
        (14, "\\bibliography{refs}")]


def test_headings_of_markdown_rst_and_typst():
    md = _padded(["# Title", "text", "```", "# not a heading", "```", "## Methods", "Setext", "------", "### Deep"])
    assert text.outline(md, "markdown") == [(1, "# Title"), (6, "## Methods"), (7, "Setext"), (9, "### Deep")]
    rst = _padded(["=====", "Title", "=====", "", "Usage", "~~~~~", "text", "Notes", "-----"])
    assert text.outline(rst, "rst") == [(2, "Title"), (5, "Usage"), (8, "Notes")]
    typ = _padded(["= Intro", "text", "== Method", "=== Detail", "a = b"])
    assert text.outline(typ, "typst") == [(1, "= Intro"), (3, "== Method"), (4, "=== Detail")]


def test_python_definitions_nest():
    lines = _padded(["import os", "class Model:", "    def fit(self):", "        def inner():", "            pass",
                     "    async def run(self):", "        pass", "def main():", "    pass", "if __name__ == '__main__':",
                     "    main()"], filler="x = 1")
    assert text.outline(lines, "python") == [
        (2, "class Model:"), (3, "  def fit(self):"), (4, "    def inner():"), (6, "  async def run(self):"),
        (8, "def main():"), (10, "if __name__ == '__main__':")]


def test_javascript_declarations_and_tests():
    lines = _padded(["import x from 'y';", "export function load(a) {", "const helper = (a) => a;",
                     "const table = {", "const n = 3;", "class View {", "  function nested() {}", "describe('load', () => {",
                     "  it('reads', () => {});", "export default App;"], filler="foo();")
    assert [n for n, _ in text.outline(lines, "javascript")] == [2, 3, 4, 6, 7, 8, 9, 10]


def test_c_like_definitions_and_imports():
    lines = _padded(["#include <stdio.h>", "#include \"a.h\"", "struct point {", "int main(void) {",
                     "    if (x) {", "static int add(int a, int b) {", "int declared(void);", "pub fn run() -> u8 {",
                     "use std::io;", "impl Point {"], filler="  x++;")
    assert text.outline(lines, "c") == [
        (1, "3 imports"), (3, "struct point {"), (4, "int main(void) {"), (6, "static int add(int a, int b) {"),
        (8, "pub fn run() -> u8 {"), (10, "impl Point {")]


def test_json_keys_items_and_notebook_cells():
    obj = ["{", '  "name": "gamma",', '  "scripts": {', '    "test": "node --test"', "  },", '  "list": [1, 2],',
           '  "last": true'] + ["  "] * 40 + ["}"]
    assert text.outline(obj, "json") == [(2, '"name": "gamma",'), (3, '"scripts": {'), (6, '"list": [1, 2],'),
                                         (7, '"last": true')]
    arr = ["["] + [f'  {{"id": {i}, "label": "a"}},' for i in range(45)] + ['  {"id": 45, "label": "a"}', "]"]
    assert text.outline(arr, "json")[:2] == [(1, "[46 items]"), (2, '{"id": 0, "label": "a"},')]
    nb = ["{", ' "cells": [', "  {", '   "cell_type": "markdown",', '   "source": ["# Results\\n", "text"]', "  },",
          "  {", '   "cell_type": "code",', '   "source": ["\\n", "import numpy as np\\n"]', "  },",
          "  {", '   "cell_type": "code",', '   "source": "plot()"', "  }", " ],", ' "metadata": {}'] + [" "] * 40 + ["}"]
    assert text.outline(nb, "json") == [(4, "[markdown] # Results"), (8, "[code] import numpy as np"),
                                        (12, "[code] plot()")]


def test_keys_of_yaml_toml_and_ini():
    yaml = _padded(["# config", "name: gamma", "build:", "  steps: 3", "- item", "url: http://x"], filler="  more: 1")
    assert text.outline(yaml, "yaml") == [(2, "name: gamma"), (3, "build:"), (6, "url: http://x")]
    toml = _padded(['title = "x"', "[server]", "port = 1", "[[plugins]]", "[db.main]"], filler="k = 1")
    assert text.outline(toml, "toml") == [(1, 'title = "x"'), (2, "[server]"), (4, "[[plugins]]"), (5, "[db.main]")]
    ini = _padded(["[core]", "editor = vim", "[user]", "[alias]"], filler="a = b")
    assert text.outline(ini, "ini") == [(1, "[core]"), (3, "[user]"), (4, "[alias]")]


def test_bibtex_entries_with_their_titles():
    lines = _padded(["@comment{x}", "@article{smith2020,", "  author = {Smith},", "  title = {A {Q}uantum Study},",
                     "}", "@book{doe,", '  title = "Book",', "}", "@misc{bare,", "}"], filler="")
    assert text.outline(lines, "bibtex") == [(2, "@article{smith2020} A Quantum Study"), (6, "@book{doe} Book"),
                                             (9, "@misc{bare}")]


def test_a_table_is_its_header_and_rows():
    lines = ["a,b,c"] + [f"{i},{i},{i}" for i in range(60)]
    got = text.outline(lines, "csv")
    assert got[:2] == [(1, "a,b,c"), (2, "60 rows")]
    assert text.outline(lines, "csv", from_line=5)[0] == (2, "60 rows"), "the count is the whole file's"
    arr = ["["] + [f'  {{"id": {i}}},' for i in range(44)] + ['  {"id": 44}', "]"]
    assert text.outline(arr, "json", from_line=10)[0] == (1, "[45 items]")


def test_other_files_by_their_paragraphs_within_the_budget():
    lines = []
    for i in range(200):
        lines += [f"Paragraph {i} starts here", "and goes on", ""]
    got = text.outline(lines, "plaintext", budget=400)
    assert sum(len(t) + 10 for _, t in got) <= 400 and len(got) >= 10
    assert got[0] == (1, "Paragraph 0 starts here") and all(t.startswith("Paragraph") for _, t in got)
    steps = {b - a for (a, _), (b, _) in zip(got, got[1:])}
    assert len(steps) == 1 and steps.pop() > 3
    log = [f"2026-11-12 step {i}" for i in range(100)]
    assert len(text.outline(log, None, budget=300)) >= 5


def test_the_paragraph_fill_never_samples_away_the_languages_entries():
    lines = [f"Line {i} of plain prose that runs on for a while" for i in range(120)]
    lines[9], lines[99] = "\\section{Start}", "\\subsection{Late}"
    got = text.outline(lines, "latex", budget=300)
    assert (10, "\\section{Start}") in got and (100, "\\subsection{Late}") in got
    assert len(got) > 2 and sum(len(t) + 10 for _, t in got) <= 300
    assert got == sorted(got)


def test_a_long_file_says_where_its_outline_stops():
    lines = ["\\section{A}"] + ["x"] * (text.MAX_TEXT_LINES - 1)
    got = text.outline(lines, "latex", cut=True)
    assert got[-1] == (text.MAX_TEXT_LINES, "(the outline stops at line 50,000)") and got[0] == (1, "\\section{A}")
    assert text.outline(lines, "latex")[-1][1] != text.outline_stop(text.MAX_TEXT_LINES), "a whole file has no stop"
    got = text.outline(["\\section{A}"] + ["x"] * 19_999, "latex", from_line=400, cut=True)
    assert got[-1] == (20_000, text.outline_stop(20_000)), "a read stopped on characters says where"


def test_a_crafted_file_does_not_hold_the_outline():
    lines = ["static " * 2000 + "x", "(" * 9000 + "{", "a" * 9000 + " b(", "\\section{" + "{" * 9000,
             '"' + '\\"' * 4000, "[" * 9000, "-" * 9000 + "x", "@a{" + " " * 9000] * 10
    start = time.perf_counter()
    for language in ("c", "rust", "latex", "json", "javascript", "toml", "bibtex", "python", "markdown"):
        text.outline(lines, language)
    assert time.perf_counter() - start < 2


# --- locate, numbered ----------------------------------------------------------------

def test_a_hit_is_on_the_line_of_the_whole_word():
    unit = "\\subsection{Noise}\nnoise is low\n\\section{Results}\nthe results"
    assert text.locate(unit, "section", 81) == (83, "\\section{Results}")


def test_a_hit_follows_the_tokens_fts_matched():
    unit = "Figure 1 shows the loss\nsee \\ref{fig: loss} below\nthe figure"
    assert text.locate(unit, "fig:loss", 1) == (2, "see \\ref{fig: loss} below")
    assert text.locate("plain\nwith regularization\n", "regular", 41)[0] == 42
    assert text.locate("the café opens", "cafe", 1) == (1, "the café opens")
    assert text.locate("one two\nthree two one", "one two three", 1)[0] == 2


def test_an_underscore_splits_words_as_fts_does():
    unit = "import argparse\n\ndef main(p):\n    cfg = load_config_file(p)"
    assert text.locate(unit, "config", 1) == (4, "    cfg = load_config_file(p)")
    assert text.locate(unit, "config file", 1)[0] == 4
    assert text.locate("import x\nload-config here\nload.config", "load_config", 1) == (2, "load-config here")
    assert text.locate("a\nmy_var = 1", "my_var", 1)[0] == 2


def test_no_token_on_a_line_gives_the_runs_first_line():
    assert text.locate("\n\nfirst words\nsecond", "absent", 41) == (43, "first words")
    assert text.locate("a\nb", "!!", 1) == (1, "a")


def test_a_hits_line_is_cut_around_the_match():
    line = "a" * 500 + " needle " + "b" * 500
    n, snippet = text.locate(line, "needle", 1)
    assert n == 1 and "needle" in snippet and snippet.startswith("…") and snippet.endswith("…")
    assert len(snippet) <= 202


def test_numbered_lines():
    assert text.numbered(["a", "b"], 9) == "     9\ta\n    10\tb"
    assert text.numbered([], 1) == ""
