"""Zotero notes as block trees (gamma/zotero_notes.py): the export from a
block subtree to note HTML, the import back, each construct both ways, and
the round trip the two promise — ``note_tree(note_html(t)) == t`` for every
tree ``note_tree`` returns."""

import base64
import random

import pytest

from gamma.zotero_notes import MARK_STYLE, note_html, note_tree, store_images, title_text


def node(content, *children):
    return {"content": content, "children": list(children)}


def one(html):
    """The root content of a one-element note."""
    tree = note_tree(html)
    assert tree["children"] == []
    return tree["content"]


PNG = base64.b64encode(b"\x89PNG fake").decode()


def resolve_image(name):
    return ("image/png", PNG) if name == "abc123.png" else None


def resolve_ref(name):
    return {"content": "**Ref** text\nsecond line"} if name == "blk1" else None


# --- export: tree → HTML ---------------------------------------------------------

def test_the_root_is_its_element_and_its_children_a_list():
    tree = node("## Title", node("one"), node("two", node("inner")))
    assert note_html(tree) == "<h2>Title</h2><ul><li>one</li><li>two<ul><li>inner</li></ul></li></ul>"
    assert note_html(node("plain")) == "<p>plain</p>"


def test_numbered_siblings_are_ordered_runs():
    tree = node("root", node("1. a"), node("2) b"), node("x"), node("5. c"), node("6. d"), node("1. again"))
    assert note_html(tree) == ('<p>root</p><ol><li>a</li><li>b</li></ol><ul><li>x</li></ul>'
                               '<ol start="5"><li>c</li><li>d</li></ol><ol><li>again</li></ol>')


def test_empty_blocks_are_skipped():
    tree = node("root", node(""), node("  ", node("")), node("kept"))
    assert note_html(tree) == "<p>root</p><ul><li>kept</li></ul>"
    assert note_html(node("")) == ""
    # A root with no text of its own (a highlight's memo) is its children's list.
    assert note_html(node("", node("a"))) == "<ul><li>a</li></ul>"


def test_block_elements():
    assert note_html(node("```py\nx = 1\n  y < 2\n```")) == "<pre>x = 1\n  y &lt; 2</pre>"
    assert note_html(node("$$E = mc^2$$")) == '<pre class="math">$$E = mc^2$$</pre>'
    assert note_html(node("> quoted\n> *two*")) == "<blockquote><p>quoted<br/><em>two</em></p></blockquote>"
    assert note_html(node("---")) == "<hr/>"
    assert note_html(node("| a | **b** |\n| --- | :-: |\n| 1 | 2 \\| 3 |")) == (
        "<table><tr><th>a</th><th><strong>b</strong></th></tr><tr><td>1</td><td>2 | 3</td></tr></table>")
    assert note_html(node("line one\nline two")) == "<p>line one<br/>line two</p>"
    # Inside an item a paragraph is bare inline HTML.
    assert note_html(node("r", node("#### Small"), node("a\nb"))) == (
        "<p>r</p><ul><li><h4>Small</h4></li><li>a<br/>b</li></ul>")


def test_a_numbered_or_several_element_root_is_the_first_item():
    assert note_html(node("1. first", node("2. second"))) == "<ol><li>first</li><li>second</li></ol>"
    assert note_html(node("Energy:\n$$E = mc^2$$", node("next"))) == (
        '<ul><li><p>Energy:</p><pre class="math">$$E = mc^2$$</pre></li><li>next</li></ul>')


def test_inline_markup():
    html = note_html(node(r"`co<de` $x^2$ \(y\) $$z$$ \* <u>u</u> **b** *i* _j_ ~~s~~ ==m== "
                          r"[label](https://e.com/a) https://x.org/p"))
    assert html == ('<p><code>co&lt;de</code> <span class="math">$x^2$</span> <span class="math">$y$</span> '
                    '<span class="math">$$z$$</span> * <u>u</u> <strong>b</strong> <em>i</em> <em>j</em> '
                    f'<s>s</s> <span style="{MARK_STYLE}">m</span> <a href="https://e.com/a">label</a> '
                    '<a href="https://x.org/p">https://x.org/p</a></p>')
    assert note_html(node("**[a https://x.org link](https://e.com)**")) == (
        '<p><strong><a href="https://e.com">a https://x.org link</a></strong></p>')
    assert note_html(node("a < b & c")) == "<p>a &lt; b &amp; c</p>"


def test_colored_runs_keep_only_their_colors():
    assert note_html(node('<span style="color:#e5484d; background:#fde04766">c</span>')) == (
        '<p><span style="color: #e5484d; background-color: #fde04766">c</span></p>')
    assert note_html(node('<span style="background:url(x); font-weight:bold">c</span>')) == "<p>c</p>"


def test_refs_read_as_the_text_they_name():
    html = note_html(node("see [[blk1]] and ![[gone]]"), resolve_ref=resolve_ref)
    assert html == "<p>see Ref text and gone</p>"


def test_images():
    html = note_html(node("![a|300](/api/uploads/abc123.png) ![b](/api/uploads/deadbeef.png)"),
                     resolve_image=resolve_image)
    assert html == f'<p><img src="data:image/png;base64,{PNG}" alt="a" width="300"/> (image: deadbeef.png)</p>'
    # The alt and Gamma's display width come back from the <img>, the legacy
    # Logseq width too.
    assert note_tree(html)["content"] == f"![a|300](data:image/png;base64,{PNG}) (image: deadbeef.png)"
    assert note_html(node("![x](https://e.com/i.png){:width 120}")) == (
        '<p><img src="https://e.com/i.png" alt="x" width="120"/></p>')
    assert note_html(node("![](https://e.com/i.png) ![](data:image/gif;base64,R0lG)")) == (
        '<p><img src="https://e.com/i.png"/> <img src="data:image/gif;base64,R0lG"/></p>')
    # Marks and links around an image still read as one, an escaped "!" is a link,
    # and image syntax inside code is code.
    assert note_html(node("**![](https://e.com/i.png)**")) == '<p><strong><img src="https://e.com/i.png"/></strong></p>'
    assert note_html(node("[![](https://e.com/i.png)](https://e.com)")) == (
        '<p><a href="https://e.com"><img src="https://e.com/i.png"/></a></p>')
    assert note_html(node(r"\![a](https://e.com)")) == '<p>!<a href="https://e.com">a</a></p>'
    assert note_html(node("`![a](b)`")) == "<p><code>![a](b)</code></p>"


DOI = "https://doi.org/10.1016/S0140-6736(20)30183-5"   # an Elsevier DOI, parentheses and all


def test_addresses_hold_balanced_parentheses():
    # As Gamma's renderer reads them: a link's target, an image's source and
    # a bare address keep a balanced pair, and a parenthesis around one is text.
    assert note_html(node(f"[the Lancet paper]({DOI}) is key")) == (
        f'<p><a href="{DOI}">the Lancet paper</a> is key</p>')
    assert note_html(node(f"Lancet paper {DOI} is key")) == f'<p>Lancet paper <a href="{DOI}">{DOI}</a> is key</p>'
    assert note_html(node("![plot](https://e.com/Foo_(bar).png)")) == (
        '<p><img src="https://e.com/Foo_(bar).png" alt="plot"/></p>')
    assert note_html(node("(see https://x.org/a) and [a](https://x.org/b) (c)")) == (
        '<p>(see <a href="https://x.org/a">https://x.org/a</a>) and <a href="https://x.org/b">a</a> (c)</p>')
    assert note_html(node('[t](https://x.org/a(b) "title") x')) == '<p><a href="https://x.org/a(b)">t</a> x</p>'
    # A plain-text address in a Zotero note stays the bare address it was.
    assert one(f"<p>Lancet paper {DOI} is key</p>") == f"Lancet paper {DOI} is key"
    assert one(f'<p>see <a href="{DOI}">{DOI}</a></p>') == f"see {DOI}"
    assert one("<p>(see https://x.org/a)</p>") == "(see [https://x.org/a](https://x.org/a))"


def test_a_blocks_note_text_has_one_rule():
    from gamma import zotero_notes
    node = {"content": "\r\n  Self-attention\u00a0\t\r", "children": []}
    assert zotero_notes.block_text(node) == "Self-attention\u00a0"  # a no-break space is text


# --- import: HTML → tree -----------------------------------------------------------

def test_a_zotero_note_becomes_one_subtree():
    html = ('<div data-schema-version="9"><h1>Title</h1><p>Body <strong>bold</strong></p>'
            '<ul><li><p>one</p></li><li>outer<ul><li>inner</li></ul></li></ul>'
            '<ol start="3"><li>c</li><li>d</li></ol><blockquote><p>q</p></blockquote><hr></div>')
    assert note_tree(html) == node("# Title", node("Body **bold**"), node("one"),
                                   node("outer", node("inner")), node("3. c"), node("4. d"),
                                   node("> q"), node("---"))


def test_a_note_that_starts_with_a_list_is_rooted_at_its_first_item():
    html = "<ul><li>first<ul><li>sub</li></ul></li><li>second</li></ul><p>after</p>"
    assert note_tree(html) == node("first", node("sub"), node("second"), node("after"))
    # A first item with no text of its own gives way to the first that has some.
    assert note_tree("<ul><li><ul><li>a</li><li>b</li></ul></li><li>c</li></ul>") == node("a", node("b"), node("c"))


def test_list_items():
    assert note_tree("<ul><li><p>a</p><p>b</p></li></ul>") == node("a\nb")
    assert note_tree("<p>r</p><ul><li><h2>x</h2><pre>code</pre></li></ul>") == node("r", node("## x\n```\ncode\n```"))
    # An element after an item's nested list is one more child, in order.
    assert note_tree("<p>r</p><ul><li>a<ul><li>b</li></ul><p>c</p></li></ul>") == (
        node("r", node("a", node("b"), node("c"))))
    # A list straight inside a list belongs to the item before it.
    assert note_tree("<p>r</p><ul><li>a</li><ul><li>b</li></ul></ul>") == node("r", node("a", node("b")))
    # Numbers go by position; an empty item still takes one.
    assert note_tree('<p>r</p><ol start="7"><li>a</li><li></li><li>c</li></ol>') == (
        node("r", node("7. a"), node("9. c")))


def test_images_and_empty_paragraphs():
    assert one('<p><img data-attachment-key="ABCD1234" width="200"></p>') == "(image)"
    assert one('<p>see <img src="data:image/png;base64,iVBO Rw0K"></p>') == "see ![](data:image/png;base64,iVBORw0K)"
    assert one('<p><img src="https://e.com/a b(1).png"></p>') == "![](https://e.com/a%20b%281%29.png)"
    # Zotero's width becomes Gamma's; an alt keeps what reads back as typed.
    assert one('<p><img src="https://e.com/i.png" alt="Fig [1]*" width="613" height="345"></p>') == (
        "![Fig 1|613](https://e.com/i.png)")
    assert one('<p><img src="https://e.com/i.png" alt="a|300" width="50%"></p>') == "![a 300](https://e.com/i.png)"
    assert note_tree("<p>text</p><p><br></p><p>more</p>") == node("text", node("more"))
    assert note_tree('<div data-schema-version="9"><p><br></p></div>') is None
    assert note_tree("") is None


def test_a_line_of_only_no_break_spaces_is_blank():
    # Zotero 5's editor wrote each empty line as <p>&nbsp;</p>.
    html = ("<p><strong>Summary</strong></p>\n<p>&nbsp;</p>\n<p>First point.</p>\n<p>&nbsp;</p>\n"
            "<p>Second point.</p>\n<p>&nbsp;&nbsp;</p><h2>&nbsp;</h2><ul><li>&nbsp;</li></ul>")
    assert note_tree(html) == node("**Summary**", node("First point."), node("Second point."))
    assert note_tree("<p>&nbsp;</p>") is None
    assert one("<p>a&nbsp;b</p>") == "a\xa0b"


def test_inline_markup_comes_in_as_markdown():
    assert one("<p><strong>b</strong> <b>b</b> <em>i</em> <i>i</i> <u>u</u> <s>s</s> <strike>s</strike> "
               '<del>s</del> <span style="text-decoration: line-through">s</span> <code>c</code> '
               "x<sub>2</sub> <sup>n</sup></p>") == "**b** **b** *i* *i* <u>u</u> ~~s~~ ~~s~~ ~~s~~ ~~s~~ `c` x2 n"
    assert one(f'<p><span style="{MARK_STYLE}">m</span> <span style="color: #FF2020">r</span> '
               '<span style="background-color: #aabbcc">t</span> <span class="citation">(Smith, 2020)</span></p>') == (
        '==m== <span style="color:#ff2020">r</span> <span style="background:#aabbcc">t</span> (Smith, 2020)')
    assert one('<p><span class="math">$x^2$</span> <span class="math">y</span></p>') == "$x^2$ $y$"
    assert one('<pre class="math">$$E=mc^2$$</pre>') == "$$E=mc^2$$"
    assert one("<p><script>alert(1)</script><style>p {}</style>kept</p>") == "kept"


def test_code_with_a_backtick_keeps_the_backtick_beside_it():
    # INLINE_RE's code span holds no backtick, so the backtick is written out of it.
    assert one("<p><code>a`b</code></p>") == "`a`\\``b`"
    assert note_html(node("`a`\\``b`")) == "<p><code>a</code>`<code>b</code></p>"


def test_links():
    assert one('<p><a href="https://e.com/a b">label</a></p>') == "[label](https://e.com/a%20b)"
    assert one('<p>see <a href="https://x.org/p">https://x.org/p</a> now</p>') == "see https://x.org/p now"
    # Bare only where it ends where it ends: before a full stop it keeps its brackets.
    assert one('<p><a href="https://x.org/p">https://x.org/p</a>.</p>') == "[https://x.org/p](https://x.org/p)."
    # A web address in text is the link Gamma reads it as, and one inside a
    # label stays text.
    assert one("<p>at https://x.org/a_b see</p>") == "at https://x.org/a_b see"
    assert one('<p><a href="https://e.com">from https://x.org</a></p>') == "[from https:\\//x.org](https://e.com)"
    assert one('<p><a href="javascript:alert(1)">no</a></p>') == "no"


def test_whitespace_collapses_and_marks_keep_off_it():
    assert one("<p>  a   <strong> b </strong>  c </p>") == "a **b** c"
    assert one("<p>a<strong> b</strong></p>") == "a **b**"
    assert one("<p><em> </em>x<strong></strong></p>") == "x"
    assert one("<p>a&nbsp;&nbsp;b</p>") == "a\xa0\xa0b"
    assert one("<p>one<br>  two<br></p>") == "one\ntwo"


def test_nested_bold_and_italic():
    assert one("<p><strong><em>both</em></strong></p>") == "**_both_**"
    assert one("<p><strong>a <em>b</em> c</strong></p>") == "**a *b* c**"
    assert one("<p><em>a <strong>b</strong></em></p>") == "*a* **_b_**"
    # An italic run that fits neither delimiter keeps its text.
    assert one("<p><strong>a<em>b</em></strong></p>") == "**ab**"


@pytest.mark.parametrize("text, md", [
    ("*not bold*", r"\*not bold\*"),
    ("snake_case and _x_", r"snake_case and \_x\_"),
    ("[1] see", r"\[1\] see"),
    ("# not a heading", r"\# not a heading"),
    ("1. not a list", r"1\. not a list"),
    ("- not a list", r"\- not a list"),
    ("&gt; not a quote", r"\> not a quote"),
    ("---", r"\---"),
    ("costs $5", "costs $5"),
    ("$5 and $6", r"\$5 and \$6"),
    ("a ~~ b == c ~ d", r"a \~\~ b \=\= c ~ d"),
    ("x &lt; y &lt;b", r"x < y \<b"),
    ("back\\slash `tick`", r"back\\slash \`tick\`"),
    ("&amp;copy;", r"\&copy;"),
    ("!<a href='https://e.com'>x</a>", r"\![x](https://e.com)"),
])
def test_literal_markdown_characters_read_as_typed(text, md):
    assert one(f"<p>{text}</p>") == md


def test_tables_quotes_code_and_headings():
    assert one("<table><tbody><tr><th>a</th><th>b|c</th></tr><tr><td><p>1</p><p>2</p></td></tr></tbody></table>") == (
        "| a | b\\|c |\n| --- | --- |\n| 1 2 |  |")
    assert one("<blockquote><p>a</p><p><br></p><p>b</p></blockquote>") == "> a\n> b"
    assert one("<blockquote><p>a<br><br>b</p></blockquote>") == "> a\n>\n> b"
    assert one("<pre>```\nx\n```</pre>") == "````\n```\nx\n```\n````"
    assert one("<pre>\n  indented</pre>") == "```\n  indented\n```"
    # A heading is one line, so its line breaks are spaces.
    assert one("<h1>Annotations<br>(10/26/2026)</h1>") == "# Annotations (10/26/2026)"


# --- round trips --------------------------------------------------------------------

TREES = [
    node("# Heading", node("## Sub"), node("###### Six")),
    node("root", node("a", node("b", node("c")), node("d")), node("e")),
    node("1. first", node("2. second", node("1. inner")), node("plain"), node("7. seventh")),
    node("> quote\n> two lines\n>\n> after a gap"),
    node("```\ndef f():\n    return 1\n```", node("````\n```\nnested fence\n```\n````")),
    node("$$E = mc^2$$", node("$$\n\\int f\n$$")),
    node("| a | b |\n| --- | --- |\n| 1 | 2\\|3 |\n| x |  |"),
    node("see [label](https://e.com/a%20b) and https://x.org/p now", node("[https://x.org/p](https://x.org/p).")),
    node(f"[the Lancet paper](https://doi.org/10.1016/S0140-6736%2820%2930183-5) and {DOI} now",
         node("[https://x.org/a(b)](https://x.org/a%28b%29). ![](https://e.com/Foo_%28bar%29.png)")),
    node("==marked== <span style=\"color:#e5484d\">red</span> <span style=\"background:#fde04766\">tint</span>"),
    node("<u>under</u> ~~strike~~ `code` $x^2$ $$y$$"),
    node("**bold *nested* bold** *a* **_b_**"),
    node(r"\*a\* \_b\_ \[c\] # \$1 \$2 \~\~ \=\= \\ \<b snake_case"),
    node(r"\# hash", node(r"1\. one"), node(r"\- dash"), node(r"\> gt"), node(r"\---")),
    node("line one\nline two\n\nafter blank"),
    node("text\n$$E = mc^2$$", node("## x\n```\ncode\n```")),
    node("![](/api/uploads/abc123.png) and **![](https://e.com/i.png)**"),
    node("(image)", node("`a`\\``b`")),
]


def test_note_tree_inverts_note_html():
    def store(mime, raw):
        assert (mime, raw) == ("image/png", base64.b64decode(PNG))
        return "/api/uploads/abc123.png"

    for tree in TREES:
        back = note_tree(note_html(tree, resolve_image=resolve_image))
        store_images(back, store)                 # as the import stores a note's pictures
        assert back == tree, tree


ZOTERO_NOTES = [
    '<div data-schema-version="9"><h1>Reading notes</h1><p>The <strong>key</strong> claim, see '
    '<a href="https://doi.org/10.1/x">the paper</a>.</p><ul><li><p>first point</p></li><li><p>second</p>'
    '<ul><li><p>detail with <span class="math">$\\alpha$</span></p></li></ul></li></ul>'
    '<ol><li><p>step</p></li><li><p>step</p></li></ol><pre>code here</pre>'
    '<p><img data-attachment-key="ABCD1234" width="300" height="200"></p></div>',
    '<div data-citation-items="%5B%5D" data-schema-version="8"><h1>Annotations<br>(26/10/2026)</h1>'
    '<p><span class="highlight" data-annotation="%7B%7D">“A <span style="background-color: #ffd40080">quoted</span>'
    ' passage”</span> <span class="citation" data-citation="%7B%7D">(<span class="citation-item">Smith, 2020, p. 3'
    '</span>)</span> my comment</p></div>',
    "<ul><li>starts with a list<ul><li>nested</li></ul></li><li>more *stars* and [brackets]</li></ul>",
    '<p><span style="color: #ff2020">red <strong>bold red</strong></span> plain $5 and $6 <u>und</u></p>'
    "<table><tr><th>h</th></tr><tr><td>c|d</td></tr></table><blockquote><p>q1</p><p>q2</p></blockquote>",
    f'<p>Lancet paper {DOI} is key, see <a href="https://en.wikipedia.org/wiki/Foo_(bar)">Foo</a></p>'
    "<p>&nbsp;</p><p>after a blank line</p>",
]


@pytest.mark.parametrize("html", ZOTERO_NOTES)
def test_a_zotero_note_comes_back_as_it_went(html):
    tree = note_tree(html)
    assert tree and note_tree(note_html(tree)) == tree


def _random_note(rnd):
    """A note in Zotero's shapes, with the characters markdown cares about."""
    chars = list("ab xy*_~=`$[]#!&\\|-+.()1") + ["&lt;b", "&amp;", "  ", " ", "1. ", "# ", "https://x.org/p_q"]

    def text():
        return "".join(rnd.choice(chars) for _ in range(rnd.randint(1, 5)))

    def inline(depth=0):
        parts = []
        for _ in range(rnd.randint(1, 3)):
            k = rnd.random()
            if depth > 2 or k < 0.4:
                parts.append(text())
            elif k < 0.5:
                parts.append(f"<code>{text()}</code>")
            elif k < 0.55:
                parts.append('<span class="math">$x_1$</span>')
            elif k < 0.6:
                parts.append("<br>")
            elif k < 0.67:
                parts.append(f'<a href="https://e.org/x(y)">{inline(depth + 1)}</a>')
            else:
                tag = rnd.choice(["strong", "em", "u", "s", 'span style="color: #ff2020"',
                                  f'span style="{MARK_STYLE}"'])
                parts.append(f"<{tag}>{inline(depth + 1)}</{tag.split()[0]}>")
        return "".join(parts)

    def block(depth=0):
        k = rnd.random()
        if k < 0.4:
            return f"<p>{inline()}</p>"
        if k < 0.5:
            return f"<h2>{inline()}</h2>"
        if k < 0.75 and depth < 2:
            items = "".join(f"<li><p>{inline()}</p>{block(depth + 1) if rnd.random() < 0.3 else ''}</li>"
                            for _ in range(rnd.randint(1, 3)))
            tag = rnd.choice(["ul", "ol"])
            return f"<{tag}>{items}</{tag}>"
        if k < 0.85:
            return f"<blockquote><p>{inline()}</p></blockquote>"
        if k < 0.92:
            return f"<pre>{text()}</pre>"
        return f"<table><tr><th>{inline()}</th></tr><tr><td>{inline()}</td></tr></table>"

    return '<div data-schema-version="9">' + "".join(block() for _ in range(rnd.randint(1, 4))) + "</div>"


def test_random_notes_come_back_as_they_went():
    rnd = random.Random(7)
    for _ in range(400):
        html = _random_note(rnd)
        tree = note_tree(html)
        if tree is not None:
            assert note_tree(note_html(tree)) == tree, html


# --- images and titles ---------------------------------------------------------------

def test_store_images_puts_each_data_uri_in_storage():
    stored = []

    def store(mime, raw):
        stored.append((mime, raw))
        return None if raw == b"refused" else f"/api/uploads/{len(stored)}.png"

    refused = base64.b64encode(b"refused").decode()
    tree = node("![alt](data:image/png;base64,iVBORw0K) and ![](data:image/png;base64,not*base64)",
                node("![](data:image/png;base64,%s)" % refused, node("![](data:image/svg+xml,%3Csvg%2F%3E)")),
                node("`![](data:image/png;base64,iVBORw0K)` stays code"))
    assert store_images(tree, store) == 2
    assert tree["content"] == "![alt](/api/uploads/1.png) and (image)"
    assert tree["children"][0]["content"] == "(image)"
    assert tree["children"][0]["children"][0]["content"] == "![](/api/uploads/3.png)"
    assert tree["children"][1]["content"] == "`![](data:image/png;base64,iVBORw0K)` stays code"
    assert stored == [("image/png", base64.b64decode("iVBORw0K")), ("image/png", b"refused"),
                      ("image/svg+xml", b"<svg/>")]


@pytest.mark.parametrize("content, title", [
    ("# **Bold** title", "Bold title"),
    ("Text with [a link](https://x.org) and ![img](/api/uploads/a.png) $x^2$", "Text with a link and x^2"),
    ("first line\n  second   line", "first line second line"),
    (r"\# escaped \*stars\*", "# escaped *stars*"),
    ("> quoted <u>words</u>", "quoted words"),
    ("```\ncode\n```", "code"),
    ("see [[blk1]] and https://x.org/p", "see blk1 and https://x.org/p"),
])
def test_title_text(content, title):
    assert title_text(content) == title
