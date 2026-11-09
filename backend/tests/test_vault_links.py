"""A synced vault's links and highlights, block by block
(``gamma/vault_links.py``): each syntax read into Gamma and written back,
the memo that tells two links to one target apart, the habit a new link
takes, and the counter-examples of a link's spelling and a highlight's
quote that once changed a block nobody edited. Over every block of the vault
fixtures: what ``to_gamma`` reads, ``to_file`` writes back byte for byte."""

import json
import unicodedata
from pathlib import Path

import pytest

from gamma import md_lens
from gamma.vault_links import Habit, LinkContext, habit_at, highlight, is_callout, read_habit, to_gamma, written

FIXTURES = Path(__file__).parent / "vault_fixtures"
ADDRESS = "http://127.0.0.1:9100/?ws=w1"
PDF = "Papers/Paper One.pdf"
# Gamma's side: every block and page the tests link, and its page.
PAGE_OF = {"pNote": "pNote", "hHead": "pNote", "bAnch": "pNote", "bPlain": "pNote",
           "pDir": "pDir", "pDeep": "pDeep", "pAB": "pAB", "pPaper": "pPaper", "pCafe": "pCafe",
           "pOut": "pOut", "bOut": "pOut", "bPaper": "pPaper"}
TITLES = {"pOut": "Outside [draft]"}


def vault(habit=None, **kw) -> LinkContext:
    """A small vault: notes in two directories, a heading and an anchored
    block, pictures in subfolders, a paper's PDF, a name in NFD."""
    ctx = LinkContext(habit, **{"address": ADDRESS, "page_of": PAGE_OF.get, "title_of": TITLES.get, **kw})
    ctx.add_note("Note.md", "pNote", [{"id": "hHead", "content": "# Heading", "children": [
        {"id": "bAnch", "content": "anchored", "anchor": "anchor", "children": []},
        {"id": "bPlain", "content": "plain", "children": []}]}])
    ctx.add_note("dir/Other.md", "pDir")
    ctx.add_note("Deep Note.md", "pDeep")
    ctx.add_note("a b.md", "pAB")
    ctx.add_note("Papers/Paper One.md", "pPaper", [{"id": "bPaper", "content": "x", "anchor": "p1", "children": []}])
    ctx.add_note(unicodedata.normalize("NFD", "Café.md"), "pCafe")
    ctx.add_file("fig.png", "f1.png")
    ctx.add_file("img/sub/chart.png", "c1.png")
    ctx.add_file("notes.txt", "t1.txt")
    ctx.add_file(PDF, "pdf1.pdf")
    return ctx


def note(ctx=None, path="Papers/Paper One.md", page="pPaper", pdf=PDF) -> LinkContext:
    return (ctx or vault()).at(path, page, pdf)


def read(text, memo=None, ctx=None, base=None):
    return to_gamma(text, memo or {}, ctx or note(), base)


def to_file(g, memo, ctx, base=None) -> str:
    """``written``'s file text alone."""
    return written(g, memo, ctx, base)[0]


def assert_identity(text, ctx):
    """What ``to_gamma`` reads, ``to_file`` writes back exactly, with the base
    and without; reading again with the memo gives the same; ``written``'s
    memo is ``to_gamma``'s."""
    g, memo = to_gamma(text, {}, ctx)
    assert to_file(g, memo, ctx, base=g) == text
    assert to_file(g, memo, ctx) == text
    assert to_gamma(text, memo, ctx) == (g, memo)
    assert written(g, memo, ctx, base=g) == (text, memo)
    return g, memo


# --- each syntax ----------------------------------------------------------------------

READS = [
    ("[[Note]]", "[[pNote]]"),
    ("[[Note|an alias]]", "[[pNote]]"),
    ("[[Note#Heading]]", "[[hHead]]"),
    ("[[Note#^anchor]]", "[[bAnch]]"),
    ("[[Note#^ANCHOR]]", "[[bAnch]]"),
    ("![[Note#^anchor]]", "![[bAnch]]"),
    ("![[Note]]", "[[pNote]]"),                          # a whole note embedded: a mention
    ("![[Note#Heading]]", "[[hHead]]"),
    ("[[Other]]", "[[pDir]]"),
    ("[[dir/Other]]", "[[pDir]]"),
    ("[[../dir/Other]]", "[[pDir]]"),
    ("[[NOTE]]", "[[pNote]]"),                           # case
    ("[[Café]]", "[[pCafe]]"),                           # NFC against a name in NFD
    ("[[Paper One#^p1]]", "[[bPaper]]"),
    ("[[#^p1]]", "[[bPaper]]"),                          # this note
    ("[[#Nowhere]]", "[[pPaper]]"),                      # a heading it lacks: the note
    ("[text](../Note.md)", "[[pNote]]"),
    ("[text](Note.md)", "[[pNote]]"),
    ("[spaces](<../a b.md>)", "[[pAB]]"),
    ("[escaped](../a%20b.md)", "[[pAB]]"),
    ('[title](../Note.md "A title")', "[[pNote]]"),
    ("[to a block](../Note.md#^anchor)", "[[bAnch]]"),
    ("[here](#^p1)", "[[bPaper]]"),
    ("![[fig.png]]", "![](/api/uploads/f1.png)"),
    ("![[fig.png|300]]", "![|300](/api/uploads/f1.png)"),
    ("![[fig.png|300x200]]", "![|300x200](/api/uploads/f1.png)"),
    ("![[fig.png|A diagram]]", "![A diagram](/api/uploads/f1.png)"),
    ("![[chart.png|200]]", "![|200](/api/uploads/c1.png)"),     # a subfolder, by name
    ("![a|300](../img/sub/chart.png)", "![a|300](/api/uploads/c1.png)"),
    ("![a](<../img/sub/chart.png> \"t\")", "![a](/api/uploads/c1.png \"t\")"),
    ("[[notes.txt]]", "[notes.txt](/api/uploads/t1.txt)"),
    ("[[notes.txt|the notes]]", "[the notes](/api/uploads/t1.txt)"),
    ("[[Paper One.pdf#page=3|p. 3]]", "[p. 3](/api/uploads/pdf1.pdf)"),
    ("[o](http://127.0.0.1:9100/?ws=w1&page=pOut)", "[[pOut]]"),
    ("[o](http://127.0.0.1:9100/?ws=w1&block=bOut)", "[[bOut]]"),
    ("[o](HTTP://127.0.0.1:9100/?ws=w1&page=pOut)", "[[pOut]]"),
]


@pytest.mark.parametrize("text,expected", READS)
def test_each_syntax_reads_and_comes_back(text, expected):
    ctx = note()
    g, _ = assert_identity(text, ctx)
    assert g == expected
    framed = f"Before {text} and after."
    assert assert_identity(framed, ctx)[0] == f"Before {expected} and after."


@pytest.mark.parametrize("text", [
    "[[Unresolved page]]", "[[Nothing]]", "![[missing.png]]", "[x](missing.md)", "[x](https://example.org/a_b)",
    "[o](http://127.0.0.1:9100/?ws=other&page=pOut)", "[o](http://elsewhere:9100/?ws=w1&page=pOut)",
    "a #tag and 2026-03-14", "[ref][x] and [x]: http://x",
])
def test_what_names_nothing_stays_as_typed(text):
    assert assert_identity(text, note()) == (text, {})


@pytest.mark.parametrize("text", [
    "`[[Note]]` and `![[fig.png]]`",
    "``a [[Note]] b``",
    "```\n[[Note]] ![[fig.png]]\n```",
    "~~~md\n[x](Note.md)\n~~~",
    "- item\n  ```\n  [[Note]]\n  ```",
    "> ```\n> [[Note]]\n> ```",
    "$$\n[[Note]]\n$$",
    "$a [[Note]] b$",
    r"\[[Note]]",
])
def test_nothing_in_code_or_math_is_a_link(text):
    assert assert_identity(text, note()) == (text, {})
    assert to_file(text, {}, note()) == text     # nor in Gamma's text


def test_a_fence_between_links_keeps_them_apart():
    text = "[[Note]]\n```\n[[Note]]\n```\n[[Deep Note]]"
    assert assert_identity(text, note())[0] == "[[pNote]]\n```\n[[Note]]\n```\n[[pDeep]]"


def test_an_escaped_pipe_in_a_table_comes_back():
    text = "| a | b |\n| --- | --- |\n| [[Note\\|alias]] | ![[fig.png\\|300]] |"
    g, _ = assert_identity(text, note())
    assert g == "| a | b |\n| --- | --- |\n| [[pNote]] | ![|300](/api/uploads/f1.png) |"


def test_an_escaped_caret_is_gammas_caret_and_the_lens_escapes_it_again():
    raw = "press \\^cmd\n"
    doc = md_lens.parse(raw)
    content = doc.flat[0].content
    g, memo = read(content)
    assert g == "press ^cmd"
    tree = md_lens.tree_of(doc, ["b0"])
    tree[0]["content"] = to_file(g, memo, note())
    assert md_lens.shape(doc, ["b0"], tree)[1] == raw
    assert read("`\\^c`")[0] == "`\\^c`"            # in code it is text as written


# --- the corpus: every block of the vault fixtures ----------------------------------------

def corpus():
    for path in sorted(FIXTURES.rglob("*.md")):
        doc = md_lens.parse(path.read_text(encoding="utf-8", errors="surrogateescape"))
        for node in doc.flat:
            yield path.relative_to(FIXTURES).as_posix(), node.content


def fixture_vault():
    """The fixtures as a vault: their notes, and a file for each picture and
    PDF their links name."""
    ctx = LinkContext(address=ADDRESS, page_of=PAGE_OF.get)
    for n, path in enumerate(sorted(FIXTURES.rglob("*.md"))):
        rel = path.relative_to(FIXTURES).as_posix()
        ctx.add_note(rel, f"p{n}", md_lens.tree_of(md_lens.parse(path.read_text(encoding="utf-8")),
                                                     [f"p{n}b{i}" for i in range(10_000)]))
    for name in ("fig.png", "Papers/Attention Is All You Need.pdf", "Note.md"):
        if name.endswith(".md"):
            ctx.add_note(name, "pNoteFixture")
        else:
            ctx.add_file(name, f"u{abs(hash(name)) % 10_000}.{name.rsplit('.', 1)[1]}")
    return ctx


def test_every_block_of_the_corpus_comes_back_exactly():
    ctx, seen, linked = fixture_vault(), 0, 0
    for rel, content in corpus():
        page = ctx.pages[rel]
        view = ctx.at(rel, page, "Papers/Attention Is All You Need.pdf" if rel.startswith("Papers/") else "")
        g, memo = to_gamma(content, {}, view)
        back = to_file(g, memo, view, base=g)
        if "\\^" in content:
            assert md_lens._normal(back) == md_lens._normal(content), (rel, content)
        else:
            assert back == content, (rel, content)
            assert to_file(g, memo, view) == content, (rel, content)
        assert to_gamma(back, memo, view) == (g, memo), (rel, content)
        seen += 1
        linked += bool(memo)
    assert seen > 200 and linked >= 9
    assert not ctx.new_files and not ctx.new_anchors


# --- the memo -------------------------------------------------------------------------------

def test_an_unchanged_link_keeps_its_spelling_through_an_edit_elsewhere():
    """A non-inverse spelling stays when the block changes
    around it."""
    ctx = note()
    text = 'See [s](<../a b.md>), [e](../a%20b.md), [t](../Note.md "T") and ![a|300](../fig.png).'
    g, memo = read(text, ctx=ctx)
    edited = "Now: " + g.replace("See ", "")
    assert to_file(edited, memo, ctx, base=g) == 'Now: [s](<../a b.md>), [e](../a%20b.md), [t](../Note.md "T") and ' \
                                                 "![a|300](../fig.png)."


def test_two_links_to_one_target_are_told_apart_by_alignment():
    """Deleting the first of two mentions of one page leaves
    the second its own spelling."""
    ctx = note()
    text = "[[Deep Note|the alias]] and [[deep note]]"
    g, memo = read(text, ctx=ctx)
    assert g == "[[pDeep]] and [[pDeep]]"
    assert to_file("and [[pDeep]]", memo, ctx, base=g) == "and [[deep note]]"
    assert to_file("[[pDeep]] and", memo, ctx, base=g) == "[[Deep Note|the alias]] and"
    assert to_file("[[pDeep]] and [[pDeep]] and [[pDeep]]", memo, ctx, base=g) == \
        "[[Deep Note|the alias]] and [[deep note]] and [[Deep Note]]"


def test_a_renamed_target_stays_a_mention():
    """``[[Old]]`` whose note was renamed resolves to the
    target the memo remembers; a note of that name now resolves to it."""
    old = vault()
    old.add_note("Old.md", "pOld")
    g, memo = read("see [[Old]]", ctx=note(old))
    assert g == "see [[pOld]]"
    renamed = vault()
    renamed.add_note("New.md", "pOld")
    assert read("see [[Old]] again", memo, note(renamed))[0] == "see [[pOld]] again"
    taken = vault()
    taken.add_note("Old.md", "pAnother")
    assert read("see [[Old]] again", memo, note(taken))[0] == "see [[pAnother]] again"


def test_a_picture_edited_on_disk_keeps_its_link():
    """The memo is keyed by the vault path, not by the upload
    whose name holds the old bytes' hash."""
    g, memo = read("![[fig.png|300]] and text")
    assert "fig.png" in memo["links"]
    ctx = vault()
    ctx.add_file("fig.png", "f2.png")
    ctx.names.pop("f1.png", None)
    edited = "![|300](/api/uploads/f2.png) and more text"
    assert to_file(edited, memo, note(ctx), base=g) == "![[fig.png|300]] and more text"
    assert not ctx.new_files
    assert read("![[fig.png|300]] and more text", memo, note(ctx))[0] == edited


def test_a_label_changed_in_gamma_keeps_the_target_as_written():
    ctx = note()
    g, memo = read("[Doc](../notes.txt) and [[notes.txt|Doc]]", ctx=ctx)
    assert g == "[Doc](/api/uploads/t1.txt) and [Doc](/api/uploads/t1.txt)"
    changed = "[Report](/api/uploads/t1.txt) and [Notes](/api/uploads/t1.txt)"
    assert to_file(changed, memo, ctx, base=g) == "[Report](../notes.txt) and [[notes.txt|Notes]]"
    g, memo = read("![[fig.png|300]]", ctx=ctx)
    assert to_file("![|450](/api/uploads/f1.png)", memo, ctx, base=g) == "![[fig.png|450]]"


def test_an_embed_gamma_holds_comes_back_from_a_link_that_cannot_say_it():
    """A block outside the vault is a link in the file; read back with its
    memo it is the embed it was."""
    ctx = note()
    text, memo = written("![[bOut]] and [[bOut]]", {}, ctx)
    assert text == ("[Outside \\[draft\\]](http://127.0.0.1:9100/?ws=w1&block=bOut) and "
                    "[Outside \\[draft\\]](http://127.0.0.1:9100/?ws=w1&block=bOut)")
    assert read(text, memo, ctx) == ("![[bOut]] and [[bOut]]", memo)
    assert read(text, {}, ctx)[0] == "[[bOut]] and [[bOut]]"


def test_a_mention_and_an_embed_of_one_block_trade_spellings():
    ctx = note()
    g, memo = read("[[Note#^anchor]]", ctx=ctx)
    assert to_file("![[bAnch]]", memo, ctx, base=g) == "![[Note#^anchor]]"


def test_written_reads_back_as_what_was_written():
    ctx = note()
    for g in ("[[pNote]] [[bAnch]] ![[bAnch]] [[pOut]] ![](/api/uploads/f1.png) [x](/api/uploads/t1.txt)",
              "[[bPlain]] and ![|300](/api/uploads/new.png) and [[zzz]]"):
        text, memo = written(g, {}, ctx)
        assert read(text, memo, ctx) == (g, memo)
        assert read(text, {}, ctx)[0] == g


# --- a new link: the vault's habit ---------------------------------------------------------

@pytest.mark.parametrize("habit,expected", [
    (Habit(), "[[Note]] [[Other]] ![[chart.png]] [[Note#^anchor]]"),
    (Habit(format="absolute"), "[[Note]] [[dir/Other]] ![[img/sub/chart.png]] [[Note#^anchor]]"),
    (Habit(format="relative"), "[[../Note]] [[../dir/Other]] ![[../img/sub/chart.png]] [[../Note#^anchor]]"),
    (Habit(markdown=True), "[Note](Note.md) [Other](Other.md) ![](chart.png) [Note](Note.md#^anchor)"),
    (Habit(markdown=True, format="relative"),
     "[Note](../Note.md) [Other](../dir/Other.md) ![](../img/sub/chart.png) [Note](../Note.md#^anchor)"),
])
def test_a_new_link_takes_the_vaults_habit(habit, expected):
    ctx = note(vault(habit))
    g = "[[pNote]] [[pDir]] ![](/api/uploads/c1.png) [[bAnch]]"
    text, memo = written(g, {}, ctx)
    assert text == expected
    assert read(text, memo, ctx)[0] == g
    assert read(text, {}, ctx)[0] == g


def test_a_shortest_name_two_notes_share_is_a_path():
    ctx = vault(page_of={**PAGE_OF, "pNote2": "pNote2"}.get)
    ctx.add_note("dir/Note.md", "pNote2")
    text = to_file("[[pNote]] [[pNote2]]", {}, note(ctx))
    assert text == "[[Note]] [[dir/Note]]"
    assert read(text, ctx=note(ctx))[0] == "[[pNote]] [[pNote2]]"


def test_a_new_name_with_spaces_is_encoded_in_a_markdown_link():
    ctx = note(vault(Habit(markdown=True)))
    text = to_file("[[pAB]] [[pPaper]] [x](/api/uploads/pdf1.pdf)", {}, ctx)
    assert text == "[a b](a%20b.md) [Paper One](Paper%20One.md) [x](Paper%20One.pdf)"
    assert read(text, {}, ctx)[0] == "[[pAB]] [[pPaper]] [x](/api/uploads/pdf1.pdf)"


def test_a_block_link_asks_for_an_anchor_in_its_file():
    ctx = vault()
    text = to_file("see [[bPlain]] and ![[bPlain]]", {}, note(ctx))
    assert text == "see [[Note#^bPlain]] and ![[Note#^bPlain]]"
    assert ctx.new_anchors == {"bPlain": "bPlain"}
    assert read(text, {}, note(ctx))[0] == "see [[bPlain]] and ![[bPlain]]"
    assert to_file("[[bPaper]] and [[bAnch]]", {}, note(ctx)) == "[[#^p1]] and [[Note#^anchor]]"
    assert ctx.new_anchors == {"bPlain": "bPlain"}           # (those have theirs)
    md = note(vault(Habit(markdown=True)))
    assert to_file("[[bPaper]]", {}, md) == "[Paper One](#^p1)"
    assert read("[Paper One](#^p1)", ctx=md)[0] == "[[bPaper]]"


def test_a_block_whose_file_takes_no_anchor_is_linked_by_its_page():
    ctx = vault()
    ctx.read_only.add("pNote")
    text, memo = written("see [[bPlain]]", {}, note(ctx))
    assert text == "see [[Note]]" and not ctx.new_anchors
    assert read(text, memo, note(ctx))[0] == "see [[bPlain]]"      # the memo keeps the block
    assert to_file("see [[bAnch]]", {}, note(ctx)) == "see [[Note#^anchor]]"   # an anchor it has


def test_an_anchor_marker_never_takes_another_blocks():
    ctx = vault(page_of={**PAGE_OF, "b_1": "pNote", "123": "pNote"}.get)
    ctx.add_note("Note.md", "pNote", [{"id": "x", "content": "a", "anchor": "b-1", "children": []}])
    assert to_file("[[b_1]] [[123]]", {}, note(ctx)) == "[[Note#^b-1-2]] [[Note#^b123]]"
    assert read("[[Note#^b-1-2]] [[Note#^b123]]", ctx=note(ctx))[0] == "[[b_1]] [[123]]"


def test_a_page_outside_the_vault_is_its_gamma_address():
    ctx = note()
    text = to_file("[[pOut]] and [[nothing]]", {}, ctx)
    assert text == "[Outside \\[draft\\]](http://127.0.0.1:9100/?ws=w1&page=pOut) and [[nothing]]"
    assert read(text, {}, ctx)[0] == "[[pOut]] and [[nothing]]"
    bare = LinkContext(page_of=PAGE_OF.get).at("x.md", "pX")
    assert to_file("[[pOut]]", {}, bare) == "[[pOut]]"       # no address: as typed


@pytest.mark.parametrize("folder,note_path,expected", [
    (None, "Papers/Paper One.md", "attachments/new.png"),
    ("/", "Papers/Paper One.md", "new.png"),
    ("", "Papers/Paper One.md", "new.png"),
    ("./", "Papers/Paper One.md", "Papers/new.png"),
    ("./img", "Papers/Paper One.md", "Papers/img/new.png"),
    ("./img", "Top.md", "img/new.png"),
    ("media/pics/", "Papers/Paper One.md", "media/pics/new.png"),
])
def test_a_picture_pasted_in_gamma_goes_to_the_attachment_folder(folder, note_path, expected):
    habit = read_habit(None if folder is None else json.dumps({"attachmentFolderPath": folder}))
    ctx = vault(habit)
    ctx.add_note("Top.md", "pTop")
    text, memo = written("![shot|300](/api/uploads/new.png)", {}, ctx.at(note_path, "pPaper"))
    assert ctx.new_files == {expected: "new.png"}
    assert text == "![[new.png|shot|300]]"
    assert read(text, memo, ctx.at(note_path, "pPaper"))[0] == "![shot|300](/api/uploads/new.png)"


@pytest.mark.parametrize("name", ["deadbeef.exe", "Report.BAT", "x.lnk"])
def test_an_upload_gamma_never_stores_or_serves_goes_into_no_vault(name):
    """A link to an executable an import or another server put in the
    workspace: pull copied it into the vault's attachment folder, next to the
    person's notes, under the name the page chose. It stays as Gamma wrote
    it, and nothing is copied."""
    ctx = vault()
    text = f"see [the report](/api/uploads/{name})"
    assert written(text, {}, note(ctx)) == (text, {})
    assert not ctx.new_files
    text = to_file("| a |\n| --- |\n| [x](/api/uploads/t1.txt) |", {}, note())
    assert text == "| a |\n| --- |\n| [[notes.txt\\|x]] |"


def test_the_habit_is_read_from_app_json(tmp_path):
    assert read_habit(None) == Habit() == habit_at(tmp_path)
    assert read_habit("{not json") == Habit() and read_habit(b"\xff") == Habit() and read_habit("[]") == Habit()
    assert read_habit("{}") == Habit(attachments="/")
    (tmp_path / ".obsidian").mkdir()
    (tmp_path / ".obsidian" / "app.json").write_text(json.dumps(
        {"useMarkdownLinks": True, "newLinkFormat": "relative", "attachmentFolderPath": "./assets"}))
    assert habit_at(tmp_path) == Habit(markdown=True, format="relative", attachments="./assets")
    assert read_habit('{"newLinkFormat": "odd", "useMarkdownLinks": "yes"}') == Habit(attachments="/")


def test_a_picture_gamma_has_not_stored_is_stored_once_a_link_names_it():
    asked = []

    def store(path):
        asked.append(path)
        return "s1.png" if path == "new/pic.png" else None
    ctx = vault(store=store)
    ctx.add_file("new/pic.png")
    ctx.add_file("new/broken.png")
    g, memo = read("![[pic.png]] ![[pic.png]] ![[broken.png]]", ctx=note(ctx))
    assert g == "![](/api/uploads/s1.png) ![](/api/uploads/s1.png) ![[broken.png]]"
    assert asked == ["new/pic.png", "new/broken.png"]
    assert to_file(g, memo, note(ctx), base=g) == "![[pic.png]] ![[pic.png]] ![[broken.png]]"


def test_a_round_that_has_not_listed_the_pictures_lists_them_once_a_link_names_one():
    """A page's round has no scan: ``more`` is asked once, for a picture the
    tables do not hold, and never for a note."""
    asked = []

    def more():
        asked.append(1)
        return ["shots/new.png"]
    ctx = vault(store=lambda path: "n1.png" if path == "shots/new.png" else None, more=more)
    assert read("[[Nowhere]] ![[new.png]] ![[gone.png]]", ctx=note(ctx))[0] == \
        "[[Nowhere]] ![](/api/uploads/n1.png) ![[gone.png]]"
    assert asked == [1]


def test_an_upload_read_before_names_its_file_and_the_file_is_read_afresh():
    """``add_upload``: Gamma's link to a picture the vault reads in place (a
    block moved from another note) names that file, never a copy in the
    attachment folder; the file's own link stores it again (its bytes may
    have changed)."""
    ctx = vault(store=lambda path: "p2.png" if path == "img/pic.png" else None)
    ctx.add_upload("img/pic.png", "p1.png")
    assert to_file("moved ![](/api/uploads/p1.png)", {}, note(ctx, "Note.md", "pNote", "")) == "moved ![[pic.png]]"
    assert not ctx.new_files
    assert read("![[pic.png]]", ctx=note(ctx))[0] == "![](/api/uploads/p2.png)"


# --- highlights -----------------------------------------------------------------------------------

CALLOUT = ("> [!quote] [[Paper One.pdf#page=3|p. 3]]\n"
           "> the quoted text\n"
           "> on two lines\n"
           ">\n"
           "> the person's note, with [[Note]]\n"
           "> and a second line")
KNOWN = highlight("the person's note, with [[pNote]]\nand a second line", "the quoted text\non two lines", 3)


def test_a_callout_linking_the_pages_pdf_is_a_highlight():
    g, memo = assert_identity(CALLOUT, note())
    assert g == KNOWN
    assert read(CALLOUT, base=KNOWN) == (g, memo)


@pytest.mark.parametrize("first", [
    "> [!quote] [[Paper One.pdf#page=3|p. 3]]", "> [!QUOTE]- [[Paper One.pdf#page=3|p. 3]]",
    "> [!Quote]+ [[Paper One.pdf#page=3]]", ">[!quote] [[Paper One.pdf#page=3|p. 3]]",
    "> [!quote] [p. 3](Paper%20One.pdf#page=3)", "> [!quote] [p. 3](<Paper One.pdf#page=3>)",
    "> [!quote|wide] [[Papers/Paper One.pdf#page=3|p. 3]]",
])
def test_the_one_test_of_a_callout_any_case_any_fold(first):
    """A fold, a case, a linter's
    ``>[!quote]``, Obsidian's rewrite to a Markdown link."""
    text = first + "\n> the quoted text\n> on two lines"
    assert is_callout(text)
    g, _ = assert_identity(text, note())
    assert g == highlight("", "the quoted text\non two lines", 3)


@pytest.mark.parametrize("text", [
    "> [!quote] [[Other.pdf#page=3|p. 3]]\n> someone's words",      # another PDF, or one renamed
    "> [!quote] Einstein\n> imagination",
    "> [!quote] p. 3\n> words",
    "> [!quotes] [[Paper One.pdf#page=3|p. 3]]\n> words",
    "> [!note] [[Paper One.pdf#page=3|p. 3]]\n> words",
])
def test_a_new_callout_that_names_no_page_of_this_pdf_is_a_note(text):
    g, _ = assert_identity(text, note())
    assert isinstance(g, str)
    assert isinstance(read(text, ctx=note(pdf=""))[0], str)


@pytest.mark.parametrize("title", [
    "> [!quote] [[Paper One.pdf#page=9|p. 9]]", "> [!QUOTE]- whatever now", ">[!quote]", "> [!note] renamed",
    "> [!quote] [[Renamed.pdf#page=3|p. 3]]",
])
def test_a_matched_highlight_stays_one_whatever_its_title_says(title):
    """The quote and page are Gamma's, the note is read."""
    text = title + "\n> the quoted text\n> on two lines\n>\n> the person's note, with [[Note]]\n> and a second line"
    g, memo = read(text, base=KNOWN)
    assert g == KNOWN
    assert to_file(g, memo, note(), base=g) == text


def test_a_highlight_whose_anchor_ran_into_typed_text_is_still_one():
    """``^a1b2c3\\nnext typed`` is one paragraph, the anchor
    gone; the callout, matched by its text, reads as the highlight it was,
    and the paragraph is text with its caret."""
    assert read(CALLOUT, base=KNOWN)[0] == KNOWN
    assert read("^a1b2c3\nnext typed") == ("^a1b2c3\nnext typed", {})


def test_a_matched_highlight_with_no_callout_left_keeps_its_note():
    text = "the quoted text\non two lines\n\nmy note"
    g, _ = read(text, base=KNOWN)
    assert g == highlight("my note", KNOWN["quote"], 3)


def test_text_typed_under_a_callout_is_its_note():
    """A line with no ``>`` under the callout is a lazy line of
    it, and the note."""
    text = "> [!quote] [[Paper One.pdf#page=3|p. 3]]\n> the quoted text\n> on two lines\ntyped under it\nand more"
    g, memo = read(text, base=highlight("", KNOWN["quote"], 3))
    assert g == highlight("typed under it\nand more", KNOWN["quote"], 3)
    assert to_file(g, memo, note(), base=g) == text
    edited = highlight("typed under it, edited\nand more", KNOWN["quote"], 3)
    assert to_file(edited, memo, note(), base=g) == text.replace("it\n", "it, edited\n")
    text = "> [!quote] [[Paper One.pdf#page=3|p. 3]]\n> the quoted text\n> on two lines\n>\n> my note\nlazy line"
    assert read(text, base=highlight("", KNOWN["quote"], 3))[0]["note"] == "my note\nlazy line"


def test_a_quote_holding_a_blank_line_does_not_leak_into_the_note():
    """``> a\\n>\\n> b`` is the quote; the note is what is left."""
    known = highlight("note", "first part\n\nsecond part", 2)
    text = "> [!quote] [[Paper One.pdf#page=2|p. 2]]\n> first part\n>\n> second part\n>\n> note"
    g, memo = read(text, base=known)
    assert g == known
    assert to_file(g, memo, note(), base=g) == text
    fresh = to_file(known, {}, note())
    assert fresh == text
    assert read(fresh, base=known)[0] == known


def test_a_person_edit_of_the_quote_stays_until_gammas_quote_or_page_changes():
    """The quote edit is the file's while Gamma's
    quote and page stand, through an edit of the note too; a new quote or
    page writes the head again."""
    text = CALLOUT.replace("the quoted text", "the quoted text [sic]")
    g, memo = read(text, base=KNOWN)
    assert g == KNOWN
    edited = dict(KNOWN, note=KNOWN["note"] + " more")
    assert to_file(edited, memo, note(), base=g) == text + " more"
    moved = dict(KNOWN, page=4)
    assert to_file(moved, memo, note(), base=g) == CALLOUT.replace("page=3|p. 3", "page=4|p. 4")
    relocated = dict(KNOWN, quote="the quoted text\non three\nlines")
    assert to_file(relocated, memo, note(), base=g).split("\n")[1:4] == ["> the quoted text", "> on three", "> lines"]


def test_a_retyped_quote_line_goes_to_the_note_never_away():
    """A line that reads like no line of the quote is the note's, so nothing
    a person typed is taken for the quote and dropped."""
    text = "> [!quote] [[Paper One.pdf#page=3|p. 3]]\n> my own words instead"
    g, _ = read(text, base=highlight("", "the quoted text", 3))
    assert g == highlight("my own words instead", "the quoted text", 3)


def test_a_highlight_written_new_takes_the_habit():
    g = highlight("my note", "a quote", 7)
    assert to_file(g, {}, note()) == "> [!quote] [[Paper One.pdf#page=7|p. 7]]\n> a quote\n>\n> my note"
    md = note(vault(Habit(markdown=True)))
    assert to_file(g, {}, md) == "> [!quote] [p. 7](Paper%20One.pdf#page=7)\n> a quote\n>\n> my note"
    assert read(to_file(g, {}, md), ctx=md)[0] == g
    assert to_file(highlight("", "q", None), {}, note()) == "> [!quote] [[Paper One.pdf]]\n> q"
    assert to_file(highlight("n", "", 2), {}, note()) == "> [!quote] [[Paper One.pdf#page=2|p. 2]]\n>\n> n"
    assert to_file(highlight("n", "q", 2), {}, note(pdf="")) == "> [!quote] p. 2\n> q\n>\n> n"


def test_a_highlights_note_keeps_its_lines_marks():
    text = "> [!quote] [[Paper One.pdf#page=3|p. 3]]\n> q\n>\n>tight\n>   wide\n> plain\n>\n>"
    g, memo = read(text, base=highlight("", "q", 3))
    assert g["note"] == "tight\n  wide\nplain"
    assert to_file(g, memo, note(), base=g) == text
    added = dict(g, note="tight\n  wide\nplain\nnew")
    assert to_file(added, memo, note(), base=g) == text.replace("> plain", "> plain\n> new")


def test_a_highlights_note_coming_and_going():
    bare = "> [!quote] [[Paper One.pdf#page=3|p. 3]]\n> q"
    g, memo = read(bare, base=highlight("", "q", 3))
    assert to_file(dict(g, note="new"), memo, note(), base=g) == bare + "\n>\n> new"
    g, memo = read(bare + "\n>\n> gone soon", base=highlight("gone soon", "q", 3))
    assert to_file(dict(g, note=""), memo, note(), base=g) == bare


def test_the_fixture_papers_highlights_read():
    path = "Papers/Attention Is All You Need.md"
    ctx = fixture_vault()
    view = ctx.at(path, ctx.pages[path], "Papers/Attention Is All You Need.pdf")
    doc = md_lens.parse((FIXTURES / path).read_text(encoding="utf-8"))
    read_back = [to_gamma(node.content, {}, view)[0] for node in doc.flat]
    hls = [g for g in read_back if isinstance(g, dict)]
    assert [g["page"] for g in hls] == [3, 5, 7, 9, 6]
    assert hls[0] == highlight("The sequence models were all recurrent until this.",
                               "We propose a new simple network architecture, the Transformer,\n"
                               "based solely on attention mechanisms.", 3)
    assert hls[3]["quote"] == "a quote\n^not-mine"
