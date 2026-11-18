"""gamma/html_markdown.py: the forgiving HTML tree, and the Markdown writer the
web clipper and the web page reader share."""

import time

from gamma.html_markdown import DEPTH, Markdown, Parser, joined, walk

BASE = "https://example.org/guide/page"


class _Bounded(Parser):
    max_open = DEPTH


def tree(html, parser=None):
    parser = parser or _Bounded()
    parser.feed(html)
    parser.close()
    return parser.root


def md(html):
    return joined(Markdown(BASE).blocks(tree(html).children))[0]


def texts(root):
    return "".join(n for n in walk(root) if isinstance(n, str))


def test_an_unclosed_item_ends_at_the_next_one():
    assert md("<ul><li>one<li>two</ul>") == "- one\n- two"
    assert md("<ul><li>a<ul><li>b<li>c</ul><li>d</ul>") == "- a\n  - b\n  - c\n- d"


def test_a_wrapper_in_a_cell_keeps_the_table_a_table():
    # Wikipedia's {{center}} wraps a cell's text in a <div>; two of them
    # stay apart. A paragraph in a cell is a layout table's content.
    assert md("<table><tr><th>Country</th><th>Share</th></tr>"
              "<tr><td><div>China</div><div>(PRC)</div></td><td><div class=center>17%</div></td></tr></table>") == (
        "| Country | Share |\n| --- | --- |\n| China (PRC) | 17% |")
    assert md("<table><tr><td><div><p>Left</p></div></td><td>Right</td></tr></table>") == "Left\n\nRight"


def test_merged_cells_fill_every_grid_cell_they_cover():
    html = ("<table><tr><th rowspan=2>Name</th><th colspan=2>Score</th></tr><tr><th>A</th><th>B</th></tr>"
            "<tr><td rowspan='2'>Ada</td><td>1</td><td>2</td></tr><tr><td>3</td><td>4</td></tr></table>")
    assert md(html) == ("| Name | Score | Score |\n| --- | --- | --- |\n| Name | A | B |\n"
                        "| Ada | 1 | 2 |\n| Ada | 3 | 4 |")
    # A span that runs past the cells of its row still holds its column.
    assert md("<table><tr><td>a</td><td>b</td><td rowspan=2>c</td></tr><tr><td>d</td></tr></table>") == (
        "| a | b | c |\n| --- | --- | --- |\n| d |  | c |")


def test_a_table_inside_a_cell_keeps_its_own_rows():
    html = "<table><tr><td>x</td></tr><tr><td><table><tr><td>in</td></tr></table></td></tr></table>"
    assert md(html) == "x\n\n| in |\n| --- |"


def test_a_table_never_writes_far_more_than_its_markup():
    # One wide row over thousands of short ones (a page a model was sent
    # to): padding every row to the widest would be gigabytes at 5 MB.
    for first in ("<td>x</td>" * 1000, "<td colspan=1000>x</td>"):
        html = f"<table><tr>{first}</tr>" + "<tr><td>y</td></tr>" * 5000 + "</table>"
        start = time.perf_counter()
        out = md(html)
        assert len(out) < 2 * len(html) and time.perf_counter() - start < 5
        assert out.endswith("\n| y |")


def test_a_long_table_is_written_in_one_pass():
    html = "<table>" + "".join(f"<tr><td>{i}</td>" + "<td>x</td>" * 9 + "</tr>" for i in range(3000)) + "</table>"
    start = time.perf_counter()
    out = md(html)
    assert time.perf_counter() - start < 2
    assert out.count("\n") == 3000 and out.endswith("| 2999 |" + " x |" * 9)


def test_text_inside_a_hidden_element_past_the_bound_stays_out():
    class Shallow(Parser):
        max_open = 3

        def hides(self, tag, attrs):
            return "hidden" in attrs
    root = tree("<div><div><div hidden>secret <b>more</b><img src=x></div>shown</div></div>after", Shallow())
    assert texts(root) == "shownafter"
    assert not any(getattr(n, "tag", "") == "img" for n in walk(root))
    # An end tag that names no element past the bound closes the open one it
    # names, so an unclosed <p> down there does not keep the rest flat.
    root = tree("<div><div><p>one</div></div>after", Shallow())
    assert root.children[-1] == "after"


def test_a_deep_page_is_written_without_running_out_of_stack():
    assert md("<div>" * 3000 + "x" + "</div>" * 3000) == "x"
    assert md("<p>" + "<span>" * 1200 + "deep" + "</span>" * 1200 + "</p>") == "deep"


def test_a_code_block_says_its_language_from_its_classes():
    assert md('<div class="highlight-python notranslate"><div class="highlight"><pre>def f():\n    pass</pre>'
              "</div></div>") == "```python\ndef f():\n    pass\n```"
    assert md('<pre><code class="language-js">x()</code></pre>') == "```js\nx()\n```"
    # The page's language on a wrapper is no code's.
    assert md('<div class="lang-en"><pre>x</pre></div>') == "```\nx\n```"


def test_links_resolve_and_headings_are_found_by_what_wrote_them():
    assert md('<p><a href="next">Next</a>, <a href="#top">top</a>, <a href="mailto:a@b.org">mail</a>'
              '<img src="p.png" alt="P"></p>') == "[Next](https://example.org/guide/next), top, mail"
    text, headings = joined(Markdown(BASE).blocks(tree("<h2>A</h2><pre># not a heading</pre><h3>B</h3>").children))
    assert [(text[at:at + len(h)], h.level) for at, h in headings] == [("## A", 2), ("### B", 3)]
