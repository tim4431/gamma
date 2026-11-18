"""Reading a URL (gamma/web_fetch.py): one GET per page, caps by type, walls
for any web page and for a paper, meta refreshes, the page cache, IRIs and
the SSRF guard, and ``find``. HTTPS is the fake transport of test_net_guard;
the reader, resolver, guard and cookie path are real."""

import pytest

from gamma import ai_web, web_fetch
from gamma.web_fetch import FetchError
from test_ai_tools_web import _text_pdf
from test_net_guard import transport  # noqa: F401 -- fake urllib transport fixture

HTML = {"Content-Type": "text/html; charset=utf-8"}
ARTICLE = ("<html><head><title>Release notes</title><meta property='og:site_name' content='Docs'></head><body>"
           "<nav><a href='/a'>Home</a><a href='/b'>Blog</a></nav><main><h1>Release notes</h1>"
           "<h2>New features</h2><p>The <a href='/api'>API</a> gained streaming.</p>"
           + "<p>A paragraph about what changed in this release, long enough to read.</p>" * 40
           + "</main><footer>© Example</footer></body></html>").encode()
CHALLENGE = (b"<html><head><title>Just a moment...</title></head><body>"
             b"<script>window._cf_chl_opt={cvId:'3'}</script></body></html>")
FASTLY = (b"<!DOCTYPE html><html><head><title>Client Challenge</title>"
          b"<script src='/_fs-ch-1T1wmsGaOgGaSxcX/assets/script.js'></script></head><body>"
          b"<noscript>JavaScript is disabled in your browser.</noscript></body></html>")
SHELL = b"<html><head><script src=app.js></script></head><body><div id=root></div></body></html>"


@pytest.fixture
def web(transport):
    ai_web.clear_cache()
    yield transport
    ai_web.clear_cache()


def test_a_page_is_one_get_read_as_markdown_and_cached(web):
    routes, seen = web
    url = "https://docs.example.org/release"
    routes[url] = (200, HTML, ARTICLE)
    doc = web_fetch.fetch_page(url + "#new-features")
    assert len(seen) == 1 and doc["fresh"]
    assert (doc["kind"], doc["title"], doc["meta"]["site"], doc["truncated"]) == ("html", "Release notes", "Docs", False)
    text = doc["pages"][0]
    assert text.startswith("# Release notes\n\n## New features\n\nThe [API](https://docs.example.org/api) gained")
    assert "Home" not in text and "©" not in text
    assert [h for _, h in doc["outline"]] == ["# Release notes", "## New features"]
    # Read again (any fragment): from the cache, not a second GET.
    again = web_fetch.fetch_page(url)
    assert len(seen) == 1 and "fresh" not in again and again["pages"] == doc["pages"]


def test_a_pdf_served_as_octet_stream_is_read_by_pages(web):
    routes, _ = web
    url = "https://files.example.org/download?id=7"
    routes[url] = (200, {"Content-Type": "application/octet-stream"}, _text_pdf(["First page.", "Second page."]))
    for doc in (web_fetch.fetch_page(url), ai_web.fetch_document(url)):
        assert doc["kind"] == "pdf" and [p.strip() for p in doc["pages"]] == ["First page.", "Second page."]


def test_a_pdf_over_the_cap_is_refused_by_its_length_and_says_its_size(web):
    routes, seen = web
    url = "https://files.example.org/book.pdf"
    routes[url] = (200, {"Content-Type": "application/pdf", "Content-Length": "41234567"}, b"%PDF-1.4")
    with pytest.raises(FetchError, match=r"too large \(41\.2 MB, cap 40\.0 MB\)"):
        web_fetch.fetch_page(url)


def test_a_page_over_5_mb_is_cut_and_flagged_by_both_readers(web):
    routes, _ = web
    url = "https://data.example.org/dump.txt"
    routes[url] = (200, {"Content-Type": "text/plain"}, b"0123456789" * 600_000)
    for doc in (web_fetch.fetch_page(url), ai_web.fetch_document(url)):
        assert doc["kind"] == "text" and doc["truncated"] and doc["chars"] == web_fetch.PAGE_MAX_BYTES


@pytest.mark.parametrize("status,headers,body,wall", [
    (403, {"Content-Type": "text/html", "cf-mitigated": "challenge"}, CHALLENGE, "captcha"),
    (200, {"Content-Type": "text/html"}, FASTLY, "captcha"),
    (200, {"Content-Type": "text/html"}, SHELL, "script"),
])
def test_walls_on_a_web_page(web, status, headers, body, wall):
    routes, _ = web
    url = "https://site.example.org/page"
    routes[url] = (status, headers, body)
    with pytest.raises(FetchError) as caught:
        web_fetch.fetch_page(url)
    assert caught.value.wall == wall


def test_a_web_page_is_walled_only_when_thin_and_html_but_a_papers_page_as_before(web):
    routes, _ = web
    help_page = "https://site.example.org/login/help"
    routes[help_page] = (200, HTML, b"<html><body><h1>Trouble signing in?</h1>"
                         + b"<p>Reset your password from the account page.</p>" * 60 + b"</body></html>")
    script = "https://cdn.example.org/challenge.js"
    routes[script] = (200, {"Content-Type": "text/javascript"}, b"if (window._cf_chl_opt) { retry(); }")
    assert "Reset your password" in web_fetch.fetch_page(help_page)["pages"][0]
    assert web_fetch.fetch_page(script)["pages"] == ["if (window._cf_chl_opt) { retry(); }"]
    # fetch_paper still reads a sign-in address as a sign-in page.
    with pytest.raises(FetchError) as caught:
        ai_web.fetch_document(help_page)
    assert caught.value.wall == "login"
    # A short article is thin by its main content, not by its page: the
    # sign-in box in its header and its menus are no wall, for either reader.
    abstract = "https://journal.example.org/abs/7"
    routes[abstract] = (200, HTML, b"<html><body><header><form><input type=password name=p></form><nav>"
                        + b"<a href='/s'>Section</a>" * 150 + b"</nav></header><main><article><h1>Abstract</h1><p>"
                        + b"We measure the loss. " * 60 + b"</p></article></main></body></html>")
    assert "We measure the loss." in web_fetch.fetch_page(abstract)["pages"][0]
    assert "We measure the loss." in ai_web.fetch_document(abstract)["pages"][0]


def test_an_address_that_does_not_parse_is_passed_over(web):
    routes, _ = web
    url = "https://docs.example.org/broken"
    routes[url] = (200, HTML, b"<html><head><base href='http://[oops/'><link rel=canonical href='https://[x/'>"
                   b"<meta http-equiv=refresh content='0; url=http://[bad/'></head><body><main><p>"
                   + b"Still readable. " * 40 + b"</p></main></body></html>")
    doc = web_fetch.fetch_page(url)
    assert doc["url"] == url and "canonical" not in doc["meta"] and "Still readable." in doc["pages"][0]


def test_a_meta_refresh_is_followed_from_an_interstitial_only(web):
    routes, seen = web
    hub, article = "https://hub.example.org/retrieve/1", "https://journal.example.org/article/1"
    routes[hub] = (200, HTML, b"<html><head><meta http-equiv='refresh' content=\"2; url='https://journal.example.org"
                   b"/article/1'\"><title>Redirecting</title></head><body></body></html>")
    routes[article] = (200, HTML, ARTICLE)
    assert web_fetch.fetch_page(hub)["url"] == article and [u for u, _ in seen] == [hub, article]
    readable = "https://news.example.org/story"
    routes[readable] = (200, HTML, ARTICLE.replace(b"<title>", b"<meta http-equiv='refresh' "
                                                    b"content='300; url=/signin'><title>"))
    doc = web_fetch.fetch_page(readable)
    assert doc["url"] == readable and seen[-1][0] == readable


def test_the_page_cache_forgets_a_page_idle_for_15_minutes(web, monkeypatch):
    routes, seen = web
    now = [1000.0]
    monkeypatch.setattr(web_fetch, "_clock", lambda: now[0])
    url = "https://docs.example.org/release"
    routes[url] = (200, HTML, ARTICLE)
    assert web_fetch.fetch_page(url)["fresh"]
    for _ in range(3):  # every use keeps it another 15 minutes
        now[0] += 10 * 60
        assert "fresh" not in web_fetch.fetch_page(url)
    assert len(seen) == 1
    now[0] += 16 * 60
    # Read anew: the document says so, for a continuation to tell the model.
    assert web_fetch.fetch_page(url)["fresh"] and len(seen) == 2


def test_fetch_url_and_fetch_paper_keep_their_own_entries(web):
    routes, seen = web
    url = "https://docs.example.org/release"
    routes[url] = (200, HTML, ARTICLE)
    web_fetch.fetch_page(url)
    pages_only = len(seen)
    paper = ai_web.fetch_document(url)
    assert len(seen) > pages_only and paper["note"] == "the address serves text/html, not a PDF"
    both = len(seen)
    web_fetch.fetch_page(url)
    ai_web.fetch_document(url)
    assert len(seen) == both
    ai_web.clear_cache()  # clears both
    web_fetch.fetch_page(url)
    assert len(seen) == both + 1


def test_an_iri_is_read_as_the_uri_it_spells():
    assert (web_fetch.uri(" https://例え.jp/光?q=a b&r=%2F#章 ")
            == "https://xn--r8jz45g.jp/%E5%85%89?q=a%20b&r=%2F#%E7%AB%A0")
    assert web_fetch.uri("https://x.org/a%2Fb?c=%23&d=1") == "https://x.org/a%2Fb?c=%23&d=1"


@pytest.mark.parametrize("host", ["127。0。0。1", "①②⑦.0.0.1"])
def test_a_loopback_address_in_other_digits_and_dots_is_refused(web, host):
    _, seen = web
    for read in (web_fetch.fetch_page, ai_web.fetch_document):
        with pytest.raises(FetchError, match="blocked internal address"):
            read(f"https://{host}/admin")
    assert seen == []


def test_find_starts_a_pdf_window_at_the_line_holding_a_phrase():
    doc = {"kind": "pdf", "pages": ["Intro text.", "", "Methods\nWe cooled the  ATOMS to 1 uK.\nMore."]}
    at = web_fetch.find(doc, "cooled the atoms")
    assert web_fetch.window(doc, 20, at)[0] == "We cooled the  ATOMS"
    assert web_fetch.window_pages(doc, 20, at) == (3, 3)
    from_page = web_fetch.find(doc, "COOLED", start_page=3)
    assert web_fetch.window(doc, 9, from_page, 3)[0] == "We cooled"
    assert web_fetch.find(doc, "intro", offset=at) is None
    # A match deep in a long line starts at most 300 characters before it.
    line = {"kind": "html", "pages": ["x" * 1000 + " needle"]}
    assert web_fetch.find(line, "needle") == 1001 - 300
