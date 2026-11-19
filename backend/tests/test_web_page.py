"""gamma/web_page.py: a response body as the text the model reads — a web
page's main content as Markdown, its metadata and outline, any other text as
it is. The fixtures are cut down from the pages the reader was measured on."""

from gamma.web_page import decode, read, visible_chars

URL = "https://docs.example.org/3/library/asyncio-task.html"


def page(body: str, head: str = "<title>T</title>", url: str = URL):
    return read(f"<!doctype html><html lang='en'><head>{head}</head><body>{body}</body></html>".encode(), url,
                "text/html; charset=utf-8")


def test_a_docs_page_reads_as_its_content_without_menus_sidebars_and_footers():
    p = page("""
        <div class="skip-link"><a href="#main">Skip to main content</a></div>
        <header class="AppHeader"><h2 class="sr-only">Navigation Menu</h2>
          <ul><li><button>Product</button><div class="HeaderMenu-dropdown">
            <a href="/copilot">Copilot</a><a href="/actions">Actions</a></div></li></ul></header>
        <nav class="breadcrumbs"><a href="/">Home</a> » <a href="/3/">Docs</a></nav>
        <div class="sphinxsidebar"><h3>Table of contents</h3><ul><li><a href="#a">Coroutines</a></li></ul></div>
        <main><h1>Coroutines and tasks<a class="headerlink" href="#coroutines" title="Link here">¶</a></h1>
          <p>Coroutines declared with the async/await syntax are the preferred way of writing asyncio
          applications.</p><p>Run one with <code>asyncio.run()</code>.</p></main>
        <footer><ul class="languages"><li><a href="/de/">Deutsch</a></li><li><a href="/fr/">Français</a></li></ul>
          <p>© Copyright 2001 Python Software Foundation.</p></footer>""")
    assert p.kind == "html"
    assert p.text == ("# Coroutines and tasks\n\nCoroutines declared with the async/await syntax are the preferred "
                      "way of writing asyncio applications.\n\nRun one with `asyncio.run()`.")
    # arXiv wraps its whole page in a div.flex-wrap-footer: it holds the
    # main, so it is no footer.
    p = page("""<div class="flex-wrap-footer"><header><a href="/">arXiv</a> &gt; cs &gt; arXiv:1706.03762</header>
        <main><div id="abs"><h1 class="title">Attention Is All You Need</h1>
          <blockquote class="abstract">The dominant sequence transduction models are based on complex recurrent
          or convolutional neural networks in an encoder-decoder configuration.</blockquote></div></main>
        <footer><a href="/help">About</a> <a href="/help/contact">Contact</a></footer></div>""")
    assert p.text.startswith("# Attention Is All You Need\n\n> The dominant sequence")
    assert "Contact" not in p.text and "cs >" not in p.text


def test_an_aside_in_the_article_stays_and_one_beside_it_goes():
    p = page("""<main><article><h1>Crimean Bridge partly collapses</h1>
        <p>Ukraine says the missile and drone attack on the strategic bridge took weeks to plan, and its
        officials described the operation in detail to reporters on Monday morning.</p>
        <aside class="callout"><p>The bridge reopened to cars on Tuesday.</p></aside></article>
        <aside><h2>Related</h2><a href="/news/1">'It was really scary'</a></aside>
        <aside><h2>More from the BBC</h2><a href="/news/2">Plane crashes in Kemerovo</a></aside></main>""")
    assert "The bridge reopened to cars on Tuesday." in p.text
    assert "Related" not in p.text and "Kemerovo" not in p.text
    # A main without an article is the article: its asides are its own.
    p = page("<main><h1>Install</h1><p>Run the installer and follow the prompts on screen until it finishes, "
             "then restart.</p><aside><p>Tip: keep the defaults.</p></aside></main>")
    assert p.text.endswith("Tip: keep the defaults.")


def test_a_page_without_a_main_loses_its_link_lists_unless_they_are_the_page():
    links = "".join(f'<li><a href="/{i}">Archive {i}</a></li>' for i in range(12))
    p = page(f"""<div id="wrap"><div id="content"><h2>Things we learned about LLMs</h2>
        <p>{"A lot has happened in the world of Large Language Models this year. " * 6}</p></div>
        <div id="side"><ul>{links}</ul></div></div>""")
    assert "Archive" not in p.text and p.text.startswith("## Things we learned")
    # A portal is nothing but headline lists: they stay.
    p = page(f'<div id="wrap"><div class="col"><ul>{links}</ul></div><div class="col"><ul>{links}</ul></div></div>')
    assert p.text.count("Archive 3") == 2


def test_the_head_gives_the_title_site_author_dates_and_canonical_address():
    p = page("", head="""<title>How Ukraine struck the Kerch bridge | The Guardian</title>
        <meta property="og:site_name" content="the Guardian">
        <meta name="description" content="With the SpaceX satellite system faltering,
          Ukraine&#x2019;s military launched a surprise attack.">
        <meta property="article:author" content="https://www.theguardian.com/profile/lukeharding">
        <meta name="author" content="Luke Harding">
        <meta property="og:updated_time" content="1735668451">
        <link rel="canonical" href="/world/2026/nov/17/kerch">
        <script type="application/ld+json">{"@type":"NewsArticle",
          "datePublished":"2026-11-17T17:30:40.000Z"}</script>""",
        url="https://www.theguardian.com/world/2026/nov/17/kerch?utm_source=x")
    assert p.title == "How Ukraine struck the Kerch bridge | The Guardian"
    assert p.meta == {"lang": "en", "site": "the Guardian", "author": "Luke Harding",
                      "description": "With the SpaceX satellite system faltering, Ukraine’s military launched a "
                                     "surprise attack.",
                      "published": "2026-11-17", "modified": "2024-12-31",
                      "canonical": "https://www.theguardian.com/world/2026/nov/17/kerch"}


def test_links_resolve_against_base_href_and_show_their_characters():
    p = page('<main><p>See <a href="api/tasks.html">the API</a> and '
             '<a href="https://zh.wikipedia.org/wiki/%E5%85%89%E5%90%88%E4%BD%9C%E7%94%A8">光合作用</a>, '
             '<a href="https://xn--bcher-kva.example/a%2Fb%20c?q=%3F%25#x%23y%E2%80%8B">a file</a>, '
             '<a href="mailto:docs@example.org">mail us</a> or <a href="javascript:void(0)">open</a>.</p></main>',
             head='<base href="https://docs.example.org/v2/">')
    assert p.text == ("See [the API](https://docs.example.org/v2/api/tasks.html) and "
                      "[光合作用](https://zh.wikipedia.org/wiki/光合作用), "
                      # The host, the reserved ASCII escapes and an invisible character stay encoded.
                      "[a file](https://xn--bcher-kva.example/a%2Fb%20c?q=%3F%25#x%23y%E2%80%8B), "
                      "mail us or open.")


def test_a_teaser_card_links_its_headline_and_a_badge_its_picture():
    p = page('<main><a href="/news/articles/c6m25"><div><img src="p.jpg" alt=""><h2>Zelensky urges US to lift '
             'Starlink limits</h2><p>The president spoke on Monday.</p></div></a>'
             '<p><a href="https://ci.example/fastapi"><img src="badge.svg" alt="Test"></a> builds pass, as the '
             'project has said in every release note so far this year.</p></main>', url="https://www.bbc.com/news")
    assert p.text.startswith("## [Zelensky urges US to lift Starlink limits](https://www.bbc.com/news/articles/c6m25)"
                             "\n\nThe president spoke on Monday.\n\n[image: Test](https://ci.example/fastapi) builds")


def test_a_head_left_open_or_a_stray_end_of_page_hides_nothing():
    raw = (b"<html><head><title>Lab page</title><meta charset='utf-8'>"
           b"<div id='content'><h1>People</h1><p>Chris Manning, Professor of Linguistics</p></div></html>")
    p = read(raw, URL, "text/html")
    assert p.title == "Lab page" and p.text == "# People\n\nChris Manning, Professor of Linguistics"
    # A </html> pasted into an abstract, as on PDOS's publication list.
    p = page("<h2>2015</h2><div class='pub'><p>Vuvuzela is a new scalable messaging system.</p></html></div>"
             "<h2>2014</h2><div class='pub'><p>Ironclad apps are verified end to end.</p></div>")
    assert p.text.endswith("## 2014\n\nIronclad apps are verified end to end.")


def test_a_table_is_written_as_rows():
    p = page("""<main><h2>By country</h2><table class="wikitable"><tr><th>Location</th><th>Population</th>
        <th>% of world</th></tr>
        <tr><td><span class="flagicon"><img src="cn.png" alt=""></span> <a href="/wiki/China">China</a></td>
          <td>1,404,890,000</td><td><div class="center">17.0%</div></td></tr>
        <tr><td>India<td>1,463,870,000<td><div class="center">17.7%</div></table></main>""",
             url="https://en.wikipedia.org/wiki/List")
    assert p.text.endswith("| Location | Population | % of world |\n| --- | --- | --- |\n"
                           "| [China](https://en.wikipedia.org/wiki/China) | 1,404,890,000 | 17.0% |\n"
                           "| India | 1,463,870,000 | 17.7% |")


def test_code_keeps_its_lines_and_language_and_line_numbers_go():
    sphinx = ('<div class="highlight-python3 notranslate"><div class="highlight"><pre><span></span>'
              '<span class="k">async</span> <span class="k">def</span> <span class="nf">main</span>():\n'
              '    <span class="k">await</span> asyncio.sleep(1)\n</pre></div></div>')
    mdn = ('<div class="code-example"><div class="example-header"><span class="language-name">js</span></div>'
           '<pre class="brush: js notranslate"><code>const doubled = numbers.map((x) =&gt; x * 2);</code></pre></div>')
    pygments = ('<table class="highlighttable"><tr><td class="linenos"><div class="linenodiv"><pre>1\n2</pre></div>'
                '</td><td class="code"><div class="highlight"><pre>x = 1\ny = 2\n</pre></div></td></tr></table>')
    inline = '<pre><span class="linenos">1</span>a = 1\n<span class="linenos">2</span>b = 2</pre>'
    p = page(f"<main>{sphinx}{mdn}{pygments}{inline}</main>")
    assert p.text == ("```python3\nasync def main():\n    await asyncio.sleep(1)\n```\n\n"
                      "```js\nconst doubled = numbers.map((x) => x * 2);\n```\n\n"
                      "```\nx = 1\ny = 2\n```\n\n```\na = 1\nb = 2\n```")


def test_mathjax_scripts_become_formulas():
    p = page('<main><p>Energy <span class="MathJax_Preview">E=mc2</span><script type="math/tex">E = mc^2</script> '
             'is conserved.</p><script type="math/tex; mode=display">\\int_0^1 x\\,dx</script></main>')
    assert p.text == "Energy $E = mc^2$ is conserved.\n\n$$\n\\int_0^1 x\\,dx\n$$"


def test_an_attribute_holding_markup_says_nothing():
    p = page("""<main><p about="#mwt1" data-mw='{"parts":[{"template":{"wt":"Cite web |title=<ref>x</ref> |a=b>c"}}]}'>
        <b>Photosynthesis</b> is a system of biological processes.<sup class="reference"><a href="#cite_note-1">
        [1]</a></sup></p></main>""")
    assert p.text == "**Photosynthesis** is a system of biological processes."


def test_a_page_in_shift_jis_declared_in_its_meta_reads():
    html = ('<html><head><meta http-equiv="Content-Type" content="text/html;charset=Shift_JIS">'
            '<title>夏目漱石 こころ</title></head><body><h3>上　先生と私 ①</h3>'
            '<p>私はその人を常に先生と呼んでいた。</p></body></html>')
    # ① is Windows' Shift_JIS, which a browser reads every Shift_JIS page as.
    p = read(html.encode("cp932"), "https://www.aozora.gr.jp/cards/000148/files/773_14560.html", "text/html")
    assert p.title == "夏目漱石 こころ" and p.text == "### 上　先生と私 ①\n\n私はその人を常に先生と呼んでいた。"
    # A body cut in the middle of a character still reads.
    assert decode("café".encode()[:-1], "text/plain; charset=utf-8") == "caf�"


def test_a_deep_page_reads_without_running_out_of_stack():
    assert page("<main>" + "<div>" * 3000 + "deep text" + "</div>" * 3000 + "</main>").text == "deep text"
    assert page("<p>" + "<span>" * 1200 + "deep" + "</span>" * 1200 + "</p>").text == "deep"
    # What is hidden past the depth the tree keeps stays out with its text.
    assert page("<div>" * 150 + "<div hidden>secret <b>menu</b></div>shown" + "</div>" * 150).text == "shown"


def test_reacts_streamed_segments_stay_while_hidden_elements_go():
    p = page('<div id="root"><template id="B:0"></template></div><div hidden>Sign in to X</div>'
             '<div aria-hidden="true">Trending</div><div style="display: none">Cookie settings</div>'
             '<div hidden id="S:0"><div><span>Earlier this week Tristan published his top YouTube channels.</span>'
             '</div></div>')
    assert p.text == "Earlier this week Tristan published his top YouTube channels."


def test_the_outline_is_the_headings_written_never_a_comment_in_code():
    p = page("<main><h1>Guide</h1><pre># not a heading\nprint()</pre><h2>Install</h2><p>pip install x</p>"
             "<h4>Deep</h4><h3>Use</h3></main>")
    assert [(p.text[at:at + len(h)], h) for at, h in p.outline] == [
        ("# Guide", "# Guide"), ("## Install", "## Install"), ("### Use", "### Use")]
    # A headline that is a link is listed by its words (a news front page).
    p = page('<main><h2><a href="/news/1">Bridge reopens</a></h2><p>Story text that is long enough.</p>'
             "<h2>C<sub>4</sub> plants</h2><p>More text.</p></main>")
    assert [h for _, h in p.outline] == ["## Bridge reopens", "## C4 plants"]


def test_other_text_reads_as_it_is():
    body = b'{"items": [1, 2], "note": "<b>not markup</b>"}'
    assert read(body, "https://api.example.org/v1/items", "application/json") == (
        "", body.decode(), "text", {}, [], len(body), [])
    assert read(b"# Title\n\nplain", "https://example.org/README.md", "text/markdown").text == "# Title\n\nplain"


def test_visible_chars_counts_what_a_reader_is_given():
    shell = (b"<html><body><div id='root'></div><noscript>Please enable JavaScript to use this app.</noscript>"
             b"<script>start()</script></body></html>")
    assert visible_chars(shell, "text/html") == 0
    assert visible_chars(b"<html><body><main><p>Hello there</p></main></body></html>", "text/html") == 11
