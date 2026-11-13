"""gamma/work_folders/ignore_rules.py: a vault's ``.gammaignore`` read with
git's ignore rules, case-insensitive and NFC; the ``.gitignore`` hint; the
writer of the shortest file that says a selection (docs/dev/work_folders.md
"What a work folder tracks"). Where git is on PATH the matcher and the writer
are checked against it over random trees."""

import hashlib
import itertools
import logging
import os
import random
import re
import shutil
import subprocess
import unicodedata
from pathlib import Path

import pytest

from gamma.vault import disk as vault_disk, kinds
from gamma.documents.formats import EXTENSIONS
from gamma.work_folders import ignore_rules
from gamma.work_folders.ignore_rules import (ALL, DEFAULT, DEFAULT_TEXT, EMPTY, HEADER, MAX_BYTES, MIXED, NAME, NONE,
                                             Listing, Rules, Unreadable, excluded, hint_stack, read, tracks, write)

GIT = shutil.which("git")
F, D = False, True      # a file, a directory


# --- the rules, as gitignore(5) has them (survey-ignore.md 1.1-1.6) -------------

TABLE = [
    # lines
    ("a BOM is skipped", "\ufeffa.pdf", "a.pdf", F, False),
    ("CRLF", "a.pdf\r\nb.pdf\r\n", "a.pdf", F, False),
    ("CRLF, the last line", "a.pdf\r\nb.pdf", "b.pdf", F, False),
    ("a CR at the end", "a.pdf\r", "a.pdf", F, False),
    ("blank lines match nothing", "\n\n   \n", "a", F, True),
    ("a comment", "#a.pdf", "#a.pdf", F, True),
    ("an escaped #", "\\#a.pdf", "#a.pdf", F, False),
    ("leading spaces are kept", " #x", " #x", F, False),
    ("a leading space is the name's", " a.pdf", "a.pdf", F, True),
    ("trailing spaces are dropped", "a.pdf   ", "a.pdf", F, False),
    ("an escaped trailing space is kept", "a\\ ", "a ", F, False),
    ("an escaped trailing space is the name's", "a\\ ", "a", F, True),
    ("spaces after an escaped one go", "a\\   ", "a ", F, False),
    ("a tab is no space", "a.pdf\t", "a.pdf", F, True),
    ("a tab is the name's", "a.pdf\t", "a.pdf\t", F, False),
    ("a space inside stays", "a b.pdf", "a b.pdf", F, False),
    ("! takes back", "*.pdf\n!a.pdf", "a.pdf", F, True),
    ("! takes back what it names", "*.pdf\n!a.pdf", "b.pdf", F, False),
    ("an escaped !", "\\!a.pdf", "!a.pdf", F, False),
    ("! alone takes nothing back", "*\n!", "!", F, False),
    ("an escaped *", "\\*.pdf", "*.pdf", F, False),
    ("an escaped * is no wildcard", "\\*.pdf", "a.pdf", F, True),
    ("an escaped ?", "a\\?", "a?", F, False),
    ("an escaped ? is no wildcard", "a\\?", "ab", F, True),
    ("an escaped letter is the letter", "\\a.pdf", "a.pdf", F, False),
    ("an escaped backslash", "a\\\\b", "a\\b", F, False),
    ("a lone backslash at the end matches nothing", "a\\", "a", F, True),
    ("a lone backslash keeps the spaces before it", "a \\", "a ", F, True),
    # what a pattern is matched against
    ("a trailing / matches a directory", "frotz/", "frotz", D, False),
    ("a trailing / never matches a file", "frotz/", "frotz", F, True),
    ("a directory at any depth", "frotz/", "a/frotz", D, False),
    ("what that directory holds", "frotz/", "a/frotz/x.pdf", F, False),
    ("a name at any depth", "*.pdf", "a/b/c.pdf", F, False),
    ("hello.* takes a name that begins hello.", "hello.*", "a/hello.c", F, False),
    ("hello.* leaves hello", "hello.*", "hello", D, True),
    ("a / in the middle anchors", "doc/frotz/", "doc/frotz", D, False),
    ("a / in the middle anchors at the file's directory", "doc/frotz/", "a/doc/frotz", D, True),
    ("a leading / is the same", "/doc/frotz/", "doc/frotz", D, False),
    ("a leading / anchors the same", "/doc/frotz/", "a/doc/frotz", D, True),
    ("a leading / anchors a name", "/a.pdf", "a.pdf", F, False),
    ("a name anchored is the root's alone", "/a.pdf", "b/a.pdf", F, True),
    ("an anchored path takes a file too", "doc/frotz", "doc/frotz", F, False),
    ("foo/* takes a file in foo", "foo/*", "foo/test.json", F, False),
    ("foo/* takes a directory in foo", "foo/*", "foo/bar", D, False),
    ("foo/* leaves foo", "foo/*", "foo", D, True),
    # wildcards
    ("* never matches /", "/a*b", "a/b", F, True),
    ("* matches a run", "/a*b", "axyb", F, False),
    ("* matches none", "/a*b", "ab", F, False),
    ("? is one character", "a?b", "axb", F, False),
    ("? is never /", "/a?b", "a/b", F, True),
    ("? is never none", "a?b", "ab", F, True),
    ("a set", "[abc].pdf", "b.pdf", F, False),
    ("a set takes nothing else", "[abc].pdf", "d.pdf", F, True),
    ("a range", "[a-c].pdf", "b.pdf", F, False),
    ("a range ends", "[a-c].pdf", "d.pdf", F, True),
    ("[!...] is the complement", "[!a-c].pdf", "d.pdf", F, False),
    ("[!...] leaves the set", "[!a-c].pdf", "b.pdf", F, True),
    ("[^...] is the complement", "[^a-c].pdf", "d.pdf", F, False),
    ("[^...] leaves the set", "[^a-c].pdf", "a.pdf", F, True),
    ("a ] first is itself", "[]].pdf", "].pdf", F, False),
    ("a ] first beside others", "[]a].pdf", "a.pdf", F, False),
    ("a ] first in a complement", "[!]].pdf", "].pdf", F, True),
    ("a complement of ] takes the rest", "[!]].pdf", "x.pdf", F, False),
    ("a - last is itself", "[a-].pdf", "-.pdf", F, False),
    ("a - first is itself", "[-a].pdf", "-.pdf", F, False),
    ("an escaped - is itself", "[a\\-c].pdf", "-.pdf", F, False),
    ("an escaped - makes no range", "[a\\-c].pdf", "b.pdf", F, True),
    ("an escaped ] is in the set", "[\\]].pdf", "].pdf", F, False),
    ("a reversed range is its first character", "[z-a].pdf", "z.pdf", F, False),
    ("a reversed range takes nothing between", "[z-a].pdf", "m.pdf", F, True),
    ("[:digit:]", "[[:digit:]].pdf", "1.pdf", F, False),
    ("[:digit:] takes no letter", "[[:digit:]].pdf", "a.pdf", F, True),
    ("[:alpha:]", "[[:alpha:]]x", "Qx", F, False),
    ("[:upper:] takes lowercase", "[[:upper:]].pdf", "a.pdf", F, False),
    ("[:lower:] takes uppercase", "[[:lower:]].pdf", "A.pdf", F, False),
    ("[:space:]", "[[:space:]]x", " x", F, False),
    ("[:punct:]", "[[:punct:]]x", "!x", F, False),
    ("[:xdigit:]", "[[:xdigit:]]x", "fx", F, False),
    ("[:xdigit:] ends at f", "[[:xdigit:]]x", "gx", F, True),
    ("a class beside others", "[[:alnum:]_]x", "_x", F, False),
    ("a [: that names no class is a [", "[[:a]", "[", F, False),
    ("and the rest of a set", "[[:a]", ":", F, False),
    ("an unknown class matches nothing", "[[:foo:]].pdf", "f.pdf", F, True),
    ("an unclosed [ matches nothing", "[a.pdf", "[a.pdf", F, True),
    ("an unclosed [ matches nothing at all", "a[b", "ab", F, True),
    ("a set is never /", "/a[/]b", "a/b", F, True),
    ("a complement is never /", "/a[!x]b", "a/b", F, True),
    ("**/ at the start is anywhere", "**/foo", "foo", F, False),
    ("**/ at the start is at any depth", "**/foo", "a/b/foo", F, False),
    ("**/foo/bar: bar right under foo", "**/foo/bar", "x/foo/bar", F, False),
    ("**/foo/bar with foo at the top", "**/foo/bar", "foo/bar", F, False),
    ("**/foo/bar nowhere else", "**/foo/bar", "x/bar", F, True),
    ("/** at the end is what it holds", "abc/**", "abc/x", F, False),
    ("/** at the end at any depth", "abc/**", "abc/x/y", F, False),
    ("/** at the end is not the directory", "abc/**", "abc", D, True),
    ("/**/ is no directory", "a/**/b", "a/b", F, False),
    ("/**/ is one", "a/**/b", "a/x/b", F, False),
    ("/**/ is several", "a/**/b", "a/x/y/b", F, False),
    ("/**/ is anchored", "a/**/b", "x/a/b", F, True),
    ("another ** is one *", "/a**b", "axyb", F, False),
    ("another ** never matches /", "/a**b", "ax/yb", F, True),
    ("**.pdf is *.pdf", "**.pdf", "x/y.pdf", F, False),
    ("a/**b is a/*b", "a/**b", "a/xb", F, False),
    ("a/**b is one level", "a/**b", "a/x/b", F, True),
    # deciding one entry
    ("the last line that matches decides", "*.pdf\n!a.pdf\na.pdf", "a.pdf", F, False),
    ("a later ! decides", "a.pdf\n!*.pdf", "a.pdf", F, True),
    ("nothing comes back from under a directory left out", "build/\n!build/main.pdf", "build/main.pdf", F, False),
    ("build/* leaves build in", "build/*\n!build/main.pdf", "build/main.pdf", F, True),
    ("build/** leaves build in", "build/**\n!build/main.pdf", "build/main.pdf", F, True),
    ("build/** leaves out build/sub", "build/**\n!build/sub/x.pdf", "build/sub/x.pdf", F, False),
    ("build/** with its directories taken back", "build/**\n!build/**/\n!build/sub/x.pdf", "build/sub/x.pdf", F, True),
    ("! takes a directory back", "foo/\n!foo/", "foo/x.pdf", F, True),
    ("gitignore(5): all but foo/bar", "/*\n!/foo\n/foo/*\n!/foo/bar", "foo/bar/x.pdf", F, True),
    ("gitignore(5): foo's other files", "/*\n!/foo\n/foo/*\n!/foo/bar", "foo/baz.pdf", F, False),
    ("gitignore(5): the root's files", "/*\n!/foo\n/foo/*\n!/foo/bar", "top.md", F, False),
    ("gitignore(5): foo itself", "/*\n!/foo\n/foo/*\n!/foo/bar", "foo", D, True),
    # case and Unicode: the one way it differs from git
    ("*.pdf takes .PDF", "*.pdf", "Paper.PDF", F, False),
    ("a directory whatever its case", "/Build/", "build", D, False),
    ("a range whatever its case", "[a-c]x", "Bx", F, False),
    ("a complement whatever its case", "[!a]x", "Ax", F, True),
    ("case beyond ASCII", "É.md", "é.md", F, False),
    ("an NFD pattern, an NFC path", "e\u0301.md", "é.md", F, False),
    ("an NFC pattern, an NFD path", "é.md", "e\u0301.md", F, False),
    ("an NFD set", "[e\u0301]x", "éx", F, False),
]


@pytest.mark.parametrize("why, text, path, is_dir, tracked", TABLE, ids=[row[0] for row in TABLE])
def test_rules_read_as_git_reads_them(why, text, path, is_dir, tracked):
    assert Rules.parse(text).tracks(path, is_dir) is tracked


def test_patterns_and_paths_are_one_text_whatever_their_normal_form():
    nfd = unicodedata.normalize("NFD", "Café/Résumé.pdf")
    assert not Rules.parse("/café/résumé.PDF").tracks(nfd)
    assert not Rules.parse(unicodedata.normalize("NFD", "/CAFÉ/")).tracks("café/x.md")


@pytest.mark.parametrize("text", ["", "\n", "#x", "!", "/", "!/", "   ", "a\\", "a \\", "\\", "[a", "[[:foo:]]",
                                  "[[::]]"])
def test_a_line_that_matches_nothing_is_no_line(text):
    assert Rules.parse(text).lines == ()


def test_decide_answers_the_last_line_that_matches_or_none():
    rules = Rules.parse("*.pdf\n!keep.pdf\nfoo/*\nDocumentation/*.html")
    assert rules.decide("a.pdf", False) is True and rules.decide("keep.pdf", False) is False
    assert rules.decide("a.md", False) is None and Rules.parse("").decide("a", True) is None
    assert rules.decide("foo/bar/hello.c", False) is None       # foo/bar is left out; foo/* does not take this
    assert rules.decide("Documentation/git.html", False) is True
    assert rules.decide("Documentation/ppc/ppc.html", False) is None
    assert rules.decide("tools/perf/Documentation/perf.html", False) is None
    assert tracks(rules, "a.md") and not tracks(rules, "foo/bar/hello.c")


def test_a_nested_files_patterns_are_from_its_directory():
    names, paths = Rules.parse("a.pdf", base="sub"), Rules.parse("/a.pdf\nx/b.pdf", base="sub/")
    assert names.decide("sub/a.pdf", False) and names.decide("sub/x/a.pdf", False)
    assert names.decide("a.pdf", False) is None
    assert paths.decide("sub/a.pdf", False) and paths.decide("sub/x/a.pdf", False) is None
    assert paths.decide("sub/x/b.pdf", False) and paths.decide("x/b.pdf", False) is None


def test_the_deepest_file_with_a_line_that_matches_decides():
    root = Rules.parse("*.html\n*.[oa]")
    docs = Rules.parse("*.html\n!foo.html", base="Documentation")
    stack = [docs, root]
    assert not excluded(stack, "Documentation/foo.html", False)
    assert excluded(stack, "Documentation/bar.html", False) and excluded(stack, "foo.html", False)
    assert excluded(stack, "Documentation/x.o", False)       # no line of the deeper file matches: the root's decides
    assert not excluded(stack, "Documentation/x.c", False) and not excluded([], "x", False)
    assert not excluded([Rules.parse("!*.o", base="sub"), root], "sub/x.o", False)


def test_rules_know_their_text_and_whether_gamma_wrote_them():
    text = HEADER + "\n/build/\n"
    rules = Rules.parse(text)
    assert rules.ours and rules.text == text and not rules.exists
    assert rules.hash == hashlib.sha256(text.encode()).hexdigest()
    assert Rules.parse("\ufeff" + HEADER + "\r\n/x\r\n").ours
    assert not Rules.parse("/x\n" + HEADER).ours and not Rules.parse(HEADER + " edited").ours
    assert DEFAULT.hash == "" and not DEFAULT.exists and not DEFAULT.ours and DEFAULT.text == DEFAULT_TEXT


def test_the_default_tracks_the_classes_a_vault_reads_and_walks_every_directory():
    for ext in [*EXTENSIONS, ".JPEG", ".Md", ".exe", ".tar.gz", ""]:
        for path in (f"x{ext}", f"a/b/x{ext}"):
            assert DEFAULT.tracks(path) == (kinds.class_of(path) is not None), path
    assert DEFAULT.tracks("a/b", True) and DEFAULT.tracks("node_modules.md") and not DEFAULT.tracks(".env")


def plainly(rules, rel, is_dir):
    """``decide`` with no index: each line's regex, the last first."""
    for x in reversed(rules.lines):
        if (is_dir or not x.directory) and re.match(x.rx, unicodedata.normalize("NFC", rel), re.I | re.S):
            return not x.negated
    return None


LIKE_ASCII = [chr(0x130), chr(0x131), chr(0x17F), chr(0x212A)]
INDEXED = ["a", "B", "ab", "/a", "a/b", "/a/b", "s", "k", "i", "I", "S", "K", *LIKE_ASCII, "é", "É", "e\u0301", "x y",
           "\\*", "\\[", "*", "?", "[ab]", "**", "/**/", "**/", "/", "/", ".pdf", "\\#"]


def test_the_index_finds_what_every_line_tried_in_turn_finds(monkeypatch):
    monkeypatch.setattr(ignore_rules, "_CHUNK", 3)
    rng = random.Random(2026)
    names = ["a", "ab", "b", "s", "k", "i", "é", "x y", "*", "[", "a.pdf", *LIKE_ASCII, "S", "K", "I", "É"]
    def line():
        body = "".join(rng.choice(INDEXED) for _ in range(rng.randint(1, 4)))
        return ("!" if rng.random() < 0.3 else "") + body + ("/" if rng.random() < 0.2 else "")

    for _ in range(400):
        text = "\n".join(line() for _ in range(rng.randint(1, 12)))
        base = rng.choice(["", "", "a", "a/s", "é", chr(0x17F)])
        rules = Rules.parse(text, base=base)
        for _ in range(30):
            rel = "/".join(rng.choice(names) for _ in range(rng.randint(1, 4)))
            rel = f"{base}/{rel}" if base and rng.random() < 0.7 else rel
            is_dir = rng.random() < 0.4
            assert rules.decide(rel, is_dir) is plainly(rules, rel, is_dir), (text, base, rel, is_dir)


def test_only_the_letters_it_folds_are_ones_ignorecase_takes_for_ascii():
    letter = re.compile("[a-z]", re.I)
    like = [c for c in map(chr, range(0x80, 0x110000)) if letter.fullmatch(c)]
    assert like == sorted(ignore_rules._LIKE_ASCII) == LIKE_ASCII
    assert all(re.fullmatch(ignore_rules._LIKE_ASCII[c], c, re.I) for c in like)


def test_a_long_file_is_read_by_its_index():
    lines = [f"/d{i}/sub/f{i}.pdf" for i in range(5000)] + ["/d7/*.md", "*.log", "!/d9/sub/f9.pdf"]
    rules = Rules.parse("\n".join(lines))
    assert rules.decide("d7/sub/f7.pdf", False) and rules.decide("D7/Sub/F7.PDF", False)
    assert rules.decide("d9/sub/f9.pdf", False) is False and rules.decide("d7/x.md", False)
    assert rules.decide("d8/x.md", False) is None and rules.decide("x/y.log", False)
    assert len(rules._exact) == 5000 and set(rules._wild) == {"", "d7/"}


def test_tracks_stays_right_when_it_forgets_the_directories_it_decided(monkeypatch):
    monkeypatch.setattr(ignore_rules, "_SEEN_MAX", 2)
    rules = Rules.parse("/a/\n/b/c/")
    paths = ["a/x.md", "b/x.md", "b/c/x.md", "d/e/f/x.md", "a/y.md", "b/c/d/x.md", "b/d/x.md"] * 3
    assert [rules.tracks(p) for p in paths] == [False, True, False, True, False, False, True] * 3


# --- reading the file ----------------------------------------------------------

def test_read_tells_no_file_from_one_it_cannot_read(tmp_path):
    assert read(tmp_path) is DEFAULT
    data = b"\xef\xbb\xbf" + HEADER.encode() + b"\r\n/build/\r\n"
    (tmp_path / NAME).write_bytes(data)
    rules = read(tmp_path)
    assert rules.exists and rules.ours and not rules.tracks("build/x.pdf") and rules.tracks("x.pdf")
    assert rules.hash == hashlib.sha256(data.decode("utf-8").encode()).hexdigest()
    (tmp_path / NAME).write_bytes(b"/x\n\xff\n")
    with pytest.raises(Unreadable, match="UTF-8"):
        read(tmp_path)
    (tmp_path / NAME).write_bytes(b"#" * MAX_BYTES)
    assert read(tmp_path).exists
    (tmp_path / NAME).write_bytes(b"#" * (MAX_BYTES + 1))
    with pytest.raises(Unreadable, match="larger"):
        read(tmp_path)
    (tmp_path / NAME).unlink()
    (tmp_path / NAME).mkdir()
    with pytest.raises(Unreadable, match="directory"):
        read(tmp_path)


def test_a_file_the_disk_will_not_give_is_unreadable_and_names_no_path(tmp_path, monkeypatch):
    (tmp_path / NAME).write_text("/x")

    def refused(*args, **kwargs):
        raise PermissionError(13, "Permission denied", str(tmp_path / NAME))

    monkeypatch.setattr(ignore_rules, "open", refused, raising=False)
    with pytest.raises(Unreadable) as e:
        read(tmp_path)
    assert e.value.why == "Permission denied" and str(tmp_path) not in str(e.value)


def test_a_link_in_its_place_is_unreadable(tmp_path):
    (tmp_path / "elsewhere").write_text("*")
    (tmp_path / "v").mkdir()
    try:
        os.symlink(tmp_path / "elsewhere", tmp_path / "v" / NAME)
    except OSError:
        pytest.skip("this account may not make symlinks")
    with pytest.raises(Unreadable, match="link"):
        read(tmp_path / "v")


# --- the .gitignore hint -------------------------------------------------------

def put(root: Path, rel: str, text: str = "") -> None:
    (root / rel).parent.mkdir(parents=True, exist_ok=True)
    (root / rel).write_text(text, encoding="utf-8")


def test_the_hint_reads_the_vaults_gitignore_files_those_above_it_and_info_exclude(tmp_path):
    top = tmp_path / "repo"
    put(top, ".git/info/exclude", "# git's template\n*.log\n")
    put(top, ".gitignore", "paper/build/\nsecret.txt\n")
    put(top, "paper/.gitignore", "*.pdf\n!keep.pdf\nout/\n")
    put(top, "paper/sub/.gitignore", "!*.pdf\n")
    put(top, "paper/out/.gitignore", "!*\n")
    put(top, "paper/d/.gitignore", "d\n")
    hint = hint_stack(top / "paper")
    assert hint.ignored("main.pdf") and not hint.in_ignored_dir("main.pdf")
    assert not hint.ignored("keep.pdf") and not hint.ignored("notes.md")
    assert not hint.ignored("sub/x.pdf")                     # the deeper file decides
    assert hint.ignored("build", True) and hint.in_ignored_dir("build/main.pdf") and hint.ignored("build/main.pdf")
    assert hint.in_ignored_dir("out/x.md") and hint.ignored("out/x.md")     # out/.gitignore is never read
    assert hint.ignored("a.log") and hint.ignored("sub/secret.txt")
    assert not hint.ignored("d", True) and hint.ignored("d/d", True)       # a directory's own file judges what it holds
    assert not hint.ignored("build.md") and not hint.in_ignored_dir("d/x.md")


def test_the_hint_never_judges_the_vaults_own_directory(tmp_path):
    top = tmp_path / "repo"
    put(top, ".gitignore", "paper/\n")
    (top / ".git").mkdir()
    hint = hint_stack(top / "paper")
    assert not hint.ignored("x.pdf") and not hint.in_ignored_dir("x/y.pdf")


def test_a_vault_in_no_repository_has_its_own_files_alone(tmp_path):
    if any((p / ".git").exists() for p in tmp_path.parents):
        pytest.skip("the test's directory is in a repository")
    put(tmp_path, ".gitignore", "*.pdf\n")
    put(tmp_path, "v/.gitignore", "*.md\n")
    put(tmp_path, "v/x/.gitignore", "*.tex\n")
    hint = hint_stack(tmp_path / "v")
    assert hint.ignored("a.md") and hint.ignored("x/a.tex") and not hint.ignored("a.tex") and not hint.ignored("a.pdf")


def test_a_git_file_is_a_repositorys_top_without_info_exclude(tmp_path):
    put(tmp_path, "repo/.git", "gitdir: elsewhere\n")
    put(tmp_path, "repo/.gitignore", "*.aux\n")
    put(tmp_path, ".gitignore", "*.md\n")
    hint = hint_stack(tmp_path / "repo" / "v")
    assert hint.ignored("a.aux") and not hint.ignored("a.md")


# --- the writer ----------------------------------------------------------------

PAPER = ["main.tex", "main.pdf", "refs.bib", "README.md",
         "figures/fig1.pdf", "figures/fig2.pdf", "figures/old.pdf", "figures/plot.py",
         "build/main.pdf", "build/main.aux", "build/main.log", "build/sub/x.pdf",
         "data/a.csv", "data/b.csv", "data/raw/c.bin",
         "notes/plan.md", "notes/todo.md",
         "venv/lib/site.py", "venv/lib/x.py"]


def lines_of(text: str) -> list:
    head, *lines = text.split("\n")
    assert head == HEADER and lines[-1] == "" and "\r" not in text and not text.startswith("\ufeff")
    return lines[:-1]


def every_but(*left):
    return {f for f in PAPER if not any(f == x or f.startswith(x) for x in left)}


WRITTEN = [
    ("everything but a folder", every_but("data/"), ["/data/"]),
    ("nothing but three files", {"main.pdf", "figures/fig1.pdf", "notes/plan.md"},
     ["/*", "!/main.pdf", "!/figures/", "!/notes/", "/figures/*", "!/figures/fig1.pdf", "/notes/todo.md"]),
    ("a folder ticked but one file", {"main.pdf", "figures/fig1.pdf", "figures/fig2.pdf", "figures/plot.py"},
     ["/*", "!/main.pdf", "!/figures/", "/figures/old.pdf"]),
    ("a file inside an unticked folder", every_but("build/") | {"build/main.pdf"}, ["/build/*", "!/build/main.pdf"]),
    ("that file alone", {"build/main.pdf"}, ["/*", "!/build/", "/build/*", "!/build/main.pdf"]),
    ("a deeper file alone", {"build/sub/x.pdf"}, ["/*", "!/build/", "/build/*", "!/build/sub/"]),
    ("PDFs and notes", {f for f in PAPER if f.endswith((".pdf", ".md"))},
     ["/main.tex", "/refs.bib", "/data/", "/venv/", "/build/main.aux", "/build/main.log", "/figures/plot.py"]),
    ("everything", set(PAPER), []),
    ("nothing", set(), ["/*"]),
    ("a folder's PDFs left out", every_but("figures/fig", "figures/old"), ["/figures/*.pdf"]),
]


@pytest.mark.parametrize("why, selected, lines", WRITTEN, ids=[row[0] for row in WRITTEN])
def test_the_writer_says_a_selection_in_few_lines(why, selected, lines):
    listing = Listing(set(PAPER))
    text = write(listing, selected)
    assert lines_of(text) == lines
    assert {f for f in PAPER if tracks(Rules.parse(text), f)} == selected


def test_a_group_by_extension_needs_every_file_of_it_and_no_directory_it_would_take():
    files = {"scans/p1.pdf", "scans/p2.pdf", "scans/p3.pdf", "scans/a.txt", "scans/b.txt", "scans/c.jpg",
             "scans/d.jpg", "scans/e.png"}
    pdfs = {f for f in files if f.endswith(".pdf")}
    assert lines_of(write(Listing(files), pdfs)) == ["/scans/*", "!/scans/*.pdf"]
    with_dir = files | {"scans/old.pdf/z.txt"}
    assert lines_of(write(Listing(with_dir), pdfs)) == ["/scans/*.jpg", "/scans/*.txt", "/scans/e.png",
                                                        "/scans/old.pdf/"]
    assert lines_of(write(Listing({"a.pdf", "b.md"}), {"b.md"})) == ["/a.pdf"]       # one file is its own name


def test_a_name_is_escaped_so_that_its_line_matches_it_alone():
    names = ["[x].pdf", "a*b.md", "q?.md", "b\\s.md", "n!ot.md", "#h.md", "sp ace .md", "t  ", "é.md"]
    kept = {f"k{i}.md" for i in range(12)}
    listing = Listing({*names, *kept, "e\u0301/x.md", "sp ace /z.md"})
    text = write(listing, kept)
    assert lines_of(text) == ["/#h.md", "/\\[x].pdf", "/a\\*b.md", "/b\\\\s.md", "/n!ot.md", "/q\\?.md", "/sp ace .md",
                              "/t\\ \\ ", "/é.md", "/é/", "/sp ace\\ /"]
    assert {f for f in listing.files if tracks(Rules.parse(text), f)} == kept


def test_a_selection_the_shortest_form_gets_wrong_is_written_without_groups(monkeypatch, caplog):
    real = ignore_rules._Plan.lines
    monkeypatch.setattr(ignore_rules._Plan, "lines",
                        lambda self, *, groups=True, modes=None: ["/*"] if groups else real(self, groups=False))
    with caplog.at_level(logging.WARNING, logger="gamma"):
        text = write(Listing(set(PAPER)), {"build/main.pdf"})
    assert lines_of(text) == ["/*", "!/build/", "/build/*", "!/build/main.pdf"]
    assert any("without extension groups" in r.message for r in caplog.records)


def test_a_selection_no_form_says_is_refused_and_the_scan_tells_such_names_apart_by_neither():
    with pytest.raises(ignore_rules.Inexact):
        write(Listing({"Iota.md", "ıota.md"}), {"Iota.md"})
    for a, b in (("Iota.md", "ıota.md"), ("iota.md", "İota.md"), ("Kelvin.md", "Kelvin.md"),
                 ("site.md", "ſite.md")):
        assert vault_disk._fold(a) == vault_disk._fold(b), (a, b)     # a case clash, as one line takes both


AWKWARD = ["a", "b", "Ab", "c.d", "[x]", "sp ace ", "n!ot", "#h", "a*b", "q?", "b\\s", "é", "u\u0308", "x.PDF"]
EXTS = ["", ".md", ".pdf", ".PDF", ".txt", ".tex", ".c"]


def fold(name: str) -> str:
    return unicodedata.normalize("NFC", name).casefold()


def random_tree(rng, names, max_dirs=12, depth=3, exts=EXTS):
    """Files and directories, vault paths, no two names in one directory alike
    but for case."""
    files, dirs = set(), set()

    def fill(at, level):
        taken = set()
        for _ in range(rng.randint(1, 5)):
            name = rng.choice(names)
            if level < depth and len(dirs) < max_dirs and rng.random() < 0.35:
                if fold(name) not in taken:
                    taken.add(fold(name))
                    here = f"{at}/{name}" if at else name
                    dirs.add(here)
                    fill(here, level + 1)
                continue
            name += rng.choice(exts)
            if fold(name) not in taken:
                taken.add(fold(name))
                files.add(f"{at}/{name}" if at else name)

    fill("", 0)
    return files, dirs


def random_selection(rng, files, dirs):
    """Ticked at random, a folder at a time, or by format with a few changes."""
    shape = rng.choice(("random", "folders", "formats"))
    if shape == "random":
        p = rng.random()
        return {f for f in files if rng.random() < p}
    if shape == "folders":
        on = set(files) if rng.random() < 0.5 else set()
        for d in rng.sample(sorted(dirs), min(len(dirs), rng.randint(1, 4))):
            below = {f for f in files if f.startswith(d + "/")}
            on = on | below if rng.random() < 0.5 else on - below
        return on
    exts = {e for e in EXTS if rng.random() < 0.5}
    on = {f for f in files if os.path.splitext(f)[1] in exts}
    for f in rng.sample(sorted(files), min(len(files), 2)):
        on ^= {f}
    return on


def reads_back(text, files) -> set:
    rules = Rules.parse(text)
    return {f for f in files if rules.tracks(f)}


def with_header(lines) -> str:
    return HEADER + "\n" + "".join(f"{line}\n" for line in lines)


def test_whatever_it_is_given_the_writer_reads_back_exactly():
    rng = random.Random(20261111)
    for _ in range(500):
        files, dirs = random_tree(rng, AWKWARD)
        selected = random_selection(rng, files, dirs)
        listing = Listing(files, dirs)
        plan = ignore_rules._Plan(listing, selected)
        text = write(listing, selected)
        assert text == with_header(plan.lines()), (sorted(files), sorted(selected))
        assert reads_back(text, files) == selected, (sorted(files), sorted(selected), text)
        assert reads_back(with_header(plan.lines(groups=False)), files) == selected
        assert write(listing, reads_back(text, files)) == text
        rules = Rules.parse(text)
        for d in {""} | dirs:
            if d and plan.state(d) == NONE and plan.state(d.rpartition("/")[0]) != NONE:
                assert not rules.tracks(d, True), (d, text)         # one line, and the scan never enters it
            if plan.state(d) == ALL:
                assert rules.tracks(f"{d}/made later.xyz" if d else "made later.xyz"), (d, text)


def reached(plan) -> list:
    out, todo = [], [""]
    while todo:
        here = todo.pop()
        out.append(here)
        todo += [f"{here}/{c}" if here else c for c in plan.dirs[here]
                 if plan.state(f"{here}/{c}" if here else c) in (MIXED, ALL)]
    return out


def test_no_form_of_its_kind_is_shorter_than_the_writers():
    rng = random.Random(8)
    for _ in range(60):
        files, dirs = random_tree(rng, AWKWARD, max_dirs=7)
        selected = random_selection(rng, files, dirs)
        plan = ignore_rules._Plan(Listing(files, dirs), selected)
        at = reached(plan)
        assert len(at) <= 8
        for groups in (True, False):
            shortest = None
            for modes in itertools.product(("in", "out"), repeat=len(at)):
                lines = plan.lines(groups=groups, modes=dict(zip(at, modes)))
                assert reads_back(with_header(lines), files) == selected, (sorted(files), sorted(selected), modes)
                shortest = len(lines) if shortest is None else min(shortest, len(lines))
            assert len(plan.lines(groups=groups)) == shortest, (sorted(files), sorted(selected))


def test_a_listings_directories_with_no_file_still_count():
    listing = Listing({"a/x.md", "b.md"}, {"a", "empty", "a/deep/er"})
    assert reads_back(write(listing, {"a/x.md"}), listing.files) == {"a/x.md"}
    plan = ignore_rules._Plan(listing, {"a/x.md"})
    assert plan.dirs["a"] == ["deep"] and plan.state("empty") == EMPTY and plan.state("a") == ALL


# --- against git -----------------------------------------------------------------

needs_git = pytest.mark.skipif(GIT is None, reason="git is not on PATH")
SAFE = ["a", "b", "A2", "Bb", "foo", "Doc", "x y", "[x]", "#h", "n!ot", "b.c"]


class Repo:
    """A repository of its own in ``tmp_path``, its working tree made anew for
    each case, git asked with no configuration but the test's."""

    def __init__(self, tmp_path):
        self.root = tmp_path / "repo"
        self.root.mkdir()
        (tmp_path / "gitconfig").write_text("")
        self.env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
        self.env.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=str(tmp_path / "gitconfig"))
        self.git("init", "-q")

    def git(self, *args) -> bytes:
        return subprocess.run([GIT, "-c", "core.excludesFile=", "-c", "core.ignoreCase=true", *args], cwd=self.root,
                              env=self.env, capture_output=True, check=True).stdout

    def make(self, files, dirs, ignores=(), exclude=""):
        for entry in self.root.iterdir():
            if entry.name == ".git":
                continue
            if entry.is_dir():
                shutil.rmtree(entry)
            else:
                entry.unlink()
        for d in dirs:
            (self.root / d).mkdir(parents=True, exist_ok=True)
        for f in files:
            (self.root / f).write_bytes(b"")
        for rel, text in ignores:
            (self.root / rel).write_text(text, encoding="utf-8", newline="\n")
        (self.root / ".git" / "info").mkdir(exist_ok=True)
        (self.root / ".git" / "info" / "exclude").write_text(exclude, encoding="utf-8", newline="\n")

    def untracked(self) -> set:
        return {p for p in self.git("ls-files", "-o", "--exclude-standard", "-z").decode("utf-8").split("\0") if p}


def wild(rng, name: str) -> str:
    """``name`` as a person may write it in a pattern: escaped, a wildcard or
    a set in place of part of it. Sets hold lowercase letters only: under
    core.ignoreCase git takes ``[A]x`` for no name, not even ``Ax``, where the
    matcher takes ``Ax`` and ``ax``. Names are ASCII: git's ``?`` and sets
    match a byte, the matcher's a character."""
    chars = [("\\" + c if c in "\\*?[" else c) for c in name]
    letters = [i for i, c in enumerate(name) if c.isascii() and c.isalnum()]
    r = rng.random()
    if r < 0.12:
        return "*"
    if r < 0.22:
        return "*" + "".join(chars[len(chars) // 2:])
    if r < 0.3:
        return chars[0] + "*"
    if letters and r < 0.7:
        i = rng.choice(letters)
        c = name[i].lower()
        chars[i] = rng.choice(["?", f"[{c}q]", f"[!{c}]", "[!q]", "[a-m]", "[n-z0-9]", "[[:alpha:]]",
                               "[[:digit:]]", "[[:upper:]]", f"[{c}-a]", "[]a]"])
    return "".join(chars)


def random_line(rng, paths) -> str:
    path = rng.choice(paths) if paths and rng.random() < 0.85 else rng.choice(SAFE)
    parts = path.split("/")
    r = rng.random()
    parts = parts[-1:] if r < 0.45 else parts[:rng.randint(1, len(parts))]
    out = [wild(rng, p) for p in parts]
    r = rng.random()
    if r < 0.15:
        out.insert(0, "**")
    elif r < 0.25 and len(out) > 1:
        out.insert(rng.randint(1, len(out) - 1), "**")
    elif r < 0.32:
        out.append("**")
    line = "/".join(out)
    if rng.random() < 0.25:
        line = "/" + line
    if rng.random() < 0.2:
        line += "/"
    if line[0] in "#!":
        line = "\\" + line
    return ("!" + line) if rng.random() < 0.3 else line


def random_file(rng, paths) -> str:
    return "".join(f"{random_line(rng, paths)}\n" for _ in range(rng.randint(1, 6)))


@needs_git
def test_the_rules_and_the_hint_read_as_git_does(tmp_path):
    repo = Repo(tmp_path)
    rng = random.Random(51)
    for case in range(60):
        files, dirs = random_tree(rng, SAFE, max_dirs=8)
        every = sorted(files | dirs)
        root_text = random_file(rng, every)
        ignores = [(".gitignore", root_text)]
        nested = rng.choice(sorted(dirs)) if dirs and rng.random() < 0.6 else None
        if nested:
            inside = [p[len(nested) + 1:] for p in every if p.startswith(nested + "/")]
            ignores.append((f"{nested}/.gitignore", random_file(rng, inside)))
        exclude = random_file(rng, every) if rng.random() < 0.3 else ""
        repo.make(files, dirs, ignores, exclude)
        seen = files | {rel for rel, _text in ignores}
        hint = hint_stack(repo.root)
        theirs = repo.untracked()
        ours = {f for f in seen if not hint.ignored(f)}
        assert ours == theirs, (case, ignores, exclude, sorted(ours ^ theirs))
        if not nested and not exclude:
            assert {f for f in seen if tracks(Rules.parse(root_text), f)} == theirs, (case, root_text)
        top = [d for d in dirs if "/" not in d and not hint.ignored(d, True)]
        if top:
            vault = rng.choice(sorted(top))
            inner = hint_stack(repo.root / vault)
            below = {f for f in seen if f.startswith(vault + "/")}
            ours = {f for f in below if not inner.ignored(f[len(vault) + 1:])}
            assert ours == theirs & below, (case, vault, ignores, exclude, sorted(ours ^ (theirs & below)))


@needs_git
def test_git_reads_the_writers_file_as_the_selection(tmp_path):
    repo = Repo(tmp_path)
    rng = random.Random(1111)
    for case in range(40):
        files, dirs = random_tree(rng, SAFE, max_dirs=10)
        selected = random_selection(rng, files, dirs)
        text = write(Listing(files, dirs), selected)
        repo.make(files, dirs, [(".gitignore", text)])
        assert repo.untracked() - {".gitignore"} == selected, (case, sorted(files), sorted(selected), text)


PIECES = ["a", "b", "A", ".", "*", "**", "?", "/", "/", "[ab]", "[!a]", "[a-b]", "\\a", "\\*", "**/", "/**", "/**/",
          "[[:alpha:]]", "\\/", "[]a]", "[b-a]"]


@needs_git
def test_wildcards_side_by_side_read_as_git_does(tmp_path):
    """Lines of random pieces over names of two letters, so that the corners
    (``a**/b``, ``a**``, ``*a*b*``, ``**\\/x``, a set beside a star) meet names
    they match."""
    repo = Repo(tmp_path)
    rng = random.Random(3)
    for case in range(100):
        files, dirs = random_tree(rng, ["a", "b", "ab", "ba", "aab", "A", "Ba", "abab", "a.b", "b.a"], max_dirs=8,
                                  exts=("",))
        lines = []
        for _ in range(rng.randint(1, 5)):
            line = "".join(rng.choice(PIECES) for _ in range(rng.randint(1, 5)))
            line = "\\" + line if line[0] in "#!" else line
            lines.append("!" + line if rng.random() < 0.3 else line)
        text = "".join(f"{line}\n" for line in lines)
        repo.make(files, dirs, [(".gitignore", text)])
        theirs = repo.untracked()
        ours = {f for f in files | {".gitignore"} if tracks(Rules.parse(text), f)}
        assert ours == theirs, (case, text, sorted(ours ^ theirs))
