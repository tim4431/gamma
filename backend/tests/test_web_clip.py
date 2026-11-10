"""gamma/web_clip.py: the HTML a web clip carries (the Connector's clip.js —
a page's article, or its selection) as Markdown, and as the block tree a
clipped page is written with."""

from gamma.web_clip import article_blocks, source_line, to_markdown

BASE = "https://blog.example/posts/clocks"


def md(html, images=None):
    return to_markdown(html, BASE, images)[0]


def test_blocks_keep_their_shape():
    assert md("<h2>Intro</h2><p>One <b>bold</b> and <em>soft</em> word.</p><p>Line one<br>Line two</p>") == (
        "## Intro\n\nOne **bold** and *soft* word.\n\nLine one\nLine two")
    assert md("<ul><li>One</li><li>Two<ul><li>Two.a</li></ul></li></ul><ol start='3'><li>c</li><li>d</li></ol>") == (
        "- One\n- Two\n  - Two.a\n\n3. c\n4. d")
    assert md("<blockquote><p>First</p><p>Second</p></blockquote>") == "> First\n>\n> Second"
    assert md("<pre data-lang='python'>def f():\n    return 1\n</pre>") == "```python\ndef f():\n    return 1\n```"
    assert md("<p>Run <code>x = `y`</code> now</p>") == "Run `` x = `y` `` now"
    assert md("<hr><figure><img src='/a.png' alt='A'><figcaption>The caption</figcaption></figure>") == (
        "---\n\n![A](https://blog.example/a.png)\n\n*The caption*")


def test_a_table_of_plain_cells_is_a_table_and_a_layout_table_its_content():
    assert md("<table><tr><th>A</th><th>B</th></tr><tr><td>1 | x</td><td><i>2</i></td></tr></table>") == (
        "| A | B |\n| --- | --- |\n| 1 \\| x | *2* |")
    assert md("<table><tr><td><p>Left</p></td><td><ul><li>Right</li></ul></td></tr></table>") == "Left\n\n- Right"


def test_links_and_pictures_point_where_they_should():
    html = ("<p><a href='/about'>About</a>, <a href='#note-1'>[1]</a>, <a href='javascript:x()'>run</a>, "
            "<a href='mailto:a@b.example'>mail</a></p>"
            "<p><img src='https://cdn.example/up.png' alt='kept'><img src='https://cdn.example/far.png'>"
            "<img src='https://t.example/p.gif' width='1' height='1'><img src='gamma-inline:0'><img src='gamma-inline:1'></p>"
            "<p><a href='https://cdn.example/big.png'><img src='https://cdn.example/far.png'></a></p>")
    images = {"https://cdn.example/up.png": "/api/uploads/abc.png", "gamma-inline:0": "/api/uploads/def.gif",
              "https://cdn.example/evil.png": "javascript:alert(1)"}
    out, shown = to_markdown(html, BASE, images)
    assert out == ("[About](https://blog.example/about), [1], run, [mail](mailto:a@b.example)\n\n"
                   "![kept](/api/uploads/abc.png)![](https://cdn.example/far.png)![](/api/uploads/def.gif)\n\n"
                   "![](https://cdn.example/far.png)")
    assert shown == {"abc.png", "def.gif"}


def test_math_is_tex():
    assert md("<p>So <span data-gamma-math='inline' data-tex='a^2+b^2'>a2</span> holds.</p>") == "So $a^2+b^2$ holds."
    assert md("<p>Then<span data-gamma-math='display' data-tex='{\\displaystyle E=mc^2}'>E</span>done</p>") == (
        "Then\n\n$$\nE=mc^2\n$$\n\ndone")
    mathml = "<math><mfrac><mn>1</mn><mn>2</mn></mfrac></math>"
    assert md(f"<p><span data-gamma-math='inline' data-mathml='{mathml}'>½</span></p>") == "$\\frac{1}{2}$"
    assert md("<p><math alttext='x_1'><mi>x</mi></math> and <math><msup><mi>e</mi><mi>x</mi></msup></math></p>") == (
        "$x_1$ and $e^x$")


def test_text_that_reads_as_markup_is_escaped():
    out = md("<p>It costs $5, 2*3, snake_case, _x_, &lt;b&gt;bold&lt;/b&gt; and &amp;amp;.</p>"
             "<p># not a heading</p><p>1. not a list</p><p>- nor this</p>")
    assert out == ("It costs \\$5, 2\\*3, snake_case, \\_x\\_, \\<b>bold\\</b> and \\&amp;.\n\n"
                   "\\# not a heading\n\n1\\. not a list\n\n\\- nor this")


def test_what_a_note_cannot_show_says_nothing():
    assert md("<script>x()</script><style>p{}</style><p>Kept<button>Share</button></p><svg><text>no</text></svg>"
              "<form><input value='q'><p>Form text</p></form>") == "Kept\n\nForm text"
    assert md("<iframe src='https://video.example/embed/1'></iframe>") == (
        "[Embedded: video.example](https://video.example/embed/1)")
    assert md("") == "" and md("   <div> </div> ") == ""


def test_an_article_is_a_tree_under_its_source_line():
    html = "<h2>Clocks</h2><p>Body text.</p><h3>Ions</h3><p>More.</p>"
    tree, shown = article_blocks(html, base_url=BASE, title="Clocks", site="The Blog", byline="Ada",
                                 published="2025-03-04T10:00:00Z")
    assert [n["content"] for n in tree] == ["[The Blog](https://blog.example/posts/clocks) · Ada · 2025-03-04",
                                            "Body text.", "### Ions"]
    assert [c["content"] for c in tree[2]["children"]] == ["More."]
    assert shown == set()
    # The heading that only repeats the title is the page's title already.
    tree, _ = article_blocks("<h2>Other</h2><p>x</p>", base_url=BASE, title="Clocks")
    assert tree[1]["content"] == "## Other"
    assert article_blocks("<div></div>", base_url=BASE, title="Clocks") == ([], set())
    assert source_line(BASE) == "[blog.example](https://blog.example/posts/clocks)"
