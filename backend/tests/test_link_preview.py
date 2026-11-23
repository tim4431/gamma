"""Link previews (gamma/link_preview.py): the title and author a web
address's chip and card show, with every fetch faked."""

import gzip
import io
import json
from urllib.error import HTTPError

import pytest

from conftest import drop_user, login, make_user
from gamma import link_preview


class Upstream:
    def __init__(self, url, body, ctype="text/html; charset=utf-8", encoding=""):
        self._url, self._buf = url, io.BytesIO(body)
        self.headers = {"Content-Type": ctype, **({"Content-Encoding": encoding} if encoding else {})}

    def read(self, n=-1):
        return self._buf.read(n)

    def geturl(self):
        return self._url

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


@pytest.fixture
def web(monkeypatch):
    """``web[url] = response(asked_url)`` answers a GET of ``url``; any
    other address answers 404. ``web.asked`` lists what was fetched."""
    class Web(dict):
        asked = []

    routes = Web()

    def urlopen(req, timeout=30):
        url = req.full_url
        routes.asked.append(url)
        if url not in routes:
            raise HTTPError(url, 404, "Not Found", {}, io.BytesIO(b""))
        return routes[url](url)

    monkeypatch.setattr(link_preview, "guarded_urlopen", urlopen)
    link_preview.clear_cache()
    yield routes
    link_preview.clear_cache()


def page(head: str, *, landed=None, charset="utf-8", ctype=None, encoding=""):
    body = f"<html><head>{head}</head><body>…</body></html>".encode(charset)
    if encoding == "gzip":
        body = gzip.compress(body)
    return lambda url: Upstream(landed or url, body, ctype or f"text/html; charset={charset}", encoding)


def test_a_gzipped_page_gives_its_title_without_the_site_name_and_its_author(web):
    url = "https://www.bilibili.com/video/BV1gcUsBzEbW/"
    web[url] = page('<title>伊犁冬季冰雪游_哔哩哔哩_bilibili</title>'
                    '<meta name="author" content="开元心旅行">'
                    '<meta property="og:title" content="伊犁冬季冰雪游_哔哩哔哩_bilibili">'
                    '<meta property="og:site_name" content="哔哩哔哩">', encoding="gzip")
    assert link_preview.preview(url) == {"url": url, "host": "www.bilibili.com",
                                         "title": "伊犁冬季冰雪游", "author": "开元心旅行"}


def test_a_page_is_read_in_its_own_charset_and_a_named_og_title_counts(web):
    url = "https://www.xiaohongshu.com/discovery/item/1?xsec_token=t"
    web[url] = page('<meta name="og:title" content="冰岛自驾十日 - 小红书"><title>小红书</title>', charset="gbk")
    assert link_preview.preview(url)["title"] == "冰岛自驾十日"


@pytest.mark.parametrize("title, shown", [
    ("GitHub - python/cpython: The Python programming language", "python/cpython: The Python programming language"),
    ("Issue #1 · python/cpython · GitHub", "Issue #1 · python/cpython"),
    ("GitHub", None),
    ("出错啦! - github.com", None),
])
def test_the_site_name_comes_off_either_end_and_nothing_else_is_no_title(web, title, shown):
    url = "https://github.com/python/cpython"
    web[url] = page(f"<title>{title}</title>")
    assert link_preview.preview(url)["title"] == shown


@pytest.mark.parametrize("landed", [
    "https://www.rednote.com/404?source=/404/sec_x&error_code=300031",
    "https://passport.weibo.com/visitor/visitor?entry=miniblog",
    "https://sec.douban.com/c?r=https%3A%2F%2Fmovie.douban.com%2Fsubject%2F1%2F",
    "https://example.org/",
])
def test_a_redirect_to_a_gate_or_the_home_page_gives_no_title(web, landed):
    url = "https://example.org/notes/1"
    web[url] = page("<title>Lifestyle hub - global</title>", landed=landed)
    assert link_preview.preview(url)["title"] is None


def test_a_site_with_an_endpoint_is_asked_there_and_a_post_has_its_text_for_a_title(web):
    video = "https://www.youtube.com/watch?v=abc"
    web["https://www.youtube.com/oembed?format=json&url=" + "https%3A%2F%2Fwww.youtube.com%2Fwatch%3Fv%3Dabc"] = \
        lambda u: Upstream(u, json.dumps({"title": "Never Gonna", "author_name": "Rick"}).encode(), "application/json")
    post = "https://x.com/jack/status/20"
    web["https://publish.twitter.com/oembed?omit_script=1&dnt=1&url=https%3A%2F%2Fx.com%2Fjack%2Fstatus%2F20"] = \
        lambda u: Upstream(u, json.dumps({"author_name": "jack", "html": '<blockquote><p lang="en">just setting up'
                                          '<br>my &amp; twttr</p>&mdash; jack</blockquote>'}).encode())
    assert (link_preview.preview(video)["title"], link_preview.preview(video)["author"]) == ("Never Gonna", "Rick")
    assert link_preview.preview(post)["title"] == "just setting up my & twttr"
    assert video not in web.asked and post not in web.asked


def test_an_address_its_endpoint_cannot_answer_is_read_as_a_page(web):
    url = "https://www.youtube.com/@someone"
    web[url] = page('<meta property="og:title" content="Someone"><title>Someone - YouTube</title>')
    assert link_preview.preview(url)["title"] == "Someone"
    assert web.asked[-1] == url


def test_a_failed_fetch_gives_the_host_and_is_tried_again_after_a_while(web, monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(link_preview, "_clock", lambda: now[0])
    url = "https://example.org/later"
    assert link_preview.preview(url) == {"url": url, "host": "example.org", "title": None, "author": None}
    web[url] = page("<title>Back again</title>")
    assert link_preview.preview(url)["title"] is None
    now[0] += link_preview._MISS_TTL + 1
    assert link_preview.preview(url)["title"] == "Back again"


@pytest.fixture(scope="module")
def reader(client):
    make_user("linkreader", "linkreaderpw")
    yield login("linkreader", "linkreaderpw")
    drop_user("linkreader")


def test_the_route_is_for_members_and_web_addresses_only(anon, reader):
    assert anon.get("/api/link-preview", params={"url": "https://example.org/"}).status_code == 401
    assert reader.get("/api/link-preview", params={"url": "file:///etc/passwd"}).status_code == 400
