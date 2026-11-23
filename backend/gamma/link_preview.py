"""What a web address's chip and link card say (GET /api/link-preview,
routers/links.py): the title of what it points to and who made it, read
server-side through the SSRF guard and kept in memory for a day.

- A site with an oEmbed endpoint (``OEMBED``: YouTube, X, Reddit, TikTok,
  Vimeo, Spotify) is asked there first. Its pages are built by scripts or
  turn servers away, while the endpoint answers with the video's title or
  the post's text, and its author.
- Any other address, and one its endpoint cannot answer, is fetched as a
  browser would (web_fetch.BROWSER_HEADERS, in the reader's language), and
  only its head is read (web_page.head): og:title, else twitter:title, else
  ``<title>``, and the author meta tag. The body is ungzipped as it comes
  (Bilibili sends gzip unasked) and decoded by its charset (a GBK page).
- The site's name comes off the title, after a separator or before one
  ("…_哔哩哔哩_bilibili", "… - YouTube", "GitHub - owner/repo"): og:site_name,
  the host, its domain's name and the names ``SITE_NAMES`` has for it.
- A page that is not the one asked for says nothing about the link, so it
  gives no title: an HTTP error, a redirect to a sign-in, visitor, check
  or 404 page or to the site's home page (Weibo's visitor system, Douban's
  check, Xiaohongshu's 404 for a note opened without its share token, a
  deleted post), and a title that is only the site's name or an error's.

Without a title the chip shows the address's host.
"""

import http.client
import json
import re
import threading
import time
import zlib
from html import unescape
from urllib.error import HTTPError
from urllib.parse import quote, urlsplit
from urllib.request import Request

from . import web_page
from .net_guard import BlockedUrlError, guarded_urlopen
from .web_fetch import BROWSER_HEADERS, FetchError, uri

_TIMEOUT = 8
_HEAD_BYTES = 262_144     # what is read of a page, decoded, at most: the head is in it
_OEMBED_BYTES = 65_536
_TITLE_CHARS = 300
_TTL = 24 * 3600          # a preview is kept this long
_MISS_TTL = 15 * 60       # and an address that gave no title this long: the site may answer later
_CACHE_MAX = 500

# A domain → the endpoint its links are asked at, the address appended.
OEMBED = {
    "youtube.com": "https://www.youtube.com/oembed?format=json&url=",
    "youtu.be": "https://www.youtube.com/oembed?format=json&url=",
    "x.com": "https://publish.twitter.com/oembed?omit_script=1&dnt=1&url=",
    "twitter.com": "https://publish.twitter.com/oembed?omit_script=1&dnt=1&url=",
    "reddit.com": "https://www.reddit.com/oembed?url=",
    "tiktok.com": "https://www.tiktok.com/oembed?url=",
    "vimeo.com": "https://vimeo.com/api/oembed.json?url=",
    "spotify.com": "https://open.spotify.com/oembed?url=",
}
# A domain → the names its pages' titles carry besides the domain's own.
SITE_NAMES = {
    "bilibili.com": ("哔哩哔哩",),
    "xiaohongshu.com": ("小红书", "rednote"),
    "zhihu.com": ("知乎",),
    "douban.com": ("豆瓣",),
    "weibo.com": ("微博",),
    "douyin.com": ("抖音",),
    "x.com": ("X", "Twitter"),
    "twitter.com": ("X", "Twitter"),
    "wikipedia.org": ("Wikipedia", "维基百科，自由的百科全书", "维基百科"),
    "stackoverflow.com": ("Stack Overflow",),
}

# Between a title and a site's name: a spaced dash or dot, a pipe, an underscore.
_SEP = r"(?:\s+[-–—·•]\s+|\s*[|_]\s*)"
# What an error or a check calls itself, once the site's name is off.
_ERROR_TITLE = re.compile(
    r"^\W*(?:just a moment|attention required|access denied|security check|are you a robot|"
    r"(?:40[34]\s*)?(?:page )?not found|40[34]|forbidden|error|出错啦|安全验证|访问异常|环境异常|"
    r"页面不存在|验证码)\W*$", re.I)
# Where a site sends a visitor it will not show the page to.
_GATE_HOST = re.compile(r"^(?:passport|login|signin|sso|auth|accounts?|sec|captcha|verify)\.", re.I)
_GATE_PATH = re.compile(r"^/(?:404|error|login|signin|sign-in|passport|visitor|captcha|verify)(?:[/._?-]|$)", re.I)

_clock = time.time
_cache: dict[tuple[str, str], tuple[float, dict]] = {}
_cache_lock = threading.Lock()


def _on(host: str, domain: str) -> bool:
    return host == domain or host.endswith("." + domain)


def _domain(host: str) -> str:
    """The key of OEMBED / SITE_NAMES ``host`` falls under, else ""."""
    return next((d for d in {**OEMBED, **SITE_NAMES} if _on(host, d)), "")


def preview(url: str, lang: str = "") -> dict:
    """``{url, host, title, author}`` for an http(s) ``url`` (title and
    author None when unknown), asked in ``lang`` (an Accept-Language)."""
    host = (urlsplit(url).hostname or "").lower()
    key, now = (url, lang), _clock()
    with _cache_lock:
        hit = _cache.get(key)
        if hit and hit[0] > now:
            return hit[1]
    found = {}
    try:
        found = _oembed(url, host, lang) or _page(url, host, lang)
    except (BlockedUrlError, FetchError, OSError, ValueError, http.client.HTTPException, zlib.error):
        pass  # unreachable, refused, blocked or garbled: the chip shows the host
    data = {"url": url, "host": host, "title": found.get("title"), "author": found.get("author")}
    with _cache_lock:
        if len(_cache) >= _CACHE_MAX:
            del _cache[min(_cache, key=lambda k: _cache[k][0])]
        _cache[key] = (now + (_TTL if data["title"] else _MISS_TTL), data)
    return data


def clear_cache():
    with _cache_lock:
        _cache.clear()


# --- reading -----------------------------------------------------------------------

def _get(url: str, lang: str, accept: str, limit: int, *, head: bool = False) -> tuple[str, str, bytes]:
    """``(where it ended, Content-Type, body)`` of one guarded GET, the body
    decompressed and cut at ``limit`` bytes, or at the end of the head
    (``head``). urllib's errors propagate."""
    headers = {**BROWSER_HEADERS, "Accept": accept, "Accept-Encoding": "gzip, deflate"}
    if lang:
        headers["Accept-Language"] = lang
    with guarded_urlopen(Request(uri(url), headers=headers), timeout=_TIMEOUT) as resp:
        ctype = (resp.headers.get("Content-Type") or "").lower()
        coding = (resp.headers.get("Content-Encoding") or "").lower()
        inflate = zlib.decompressobj(zlib.MAX_WBITS | 32) if coding in ("gzip", "x-gzip", "deflate") else None
        body = bytearray()
        while len(body) < limit:
            chunk = resp.read(16384)
            if not chunk:
                break
            seen = max(0, len(body) - 8)
            body += inflate.decompress(chunk, limit - len(body)) if inflate else chunk
            if head and (body.find(b"</head", seen) >= 0 or body.find(b"<body", seen) >= 0):
                break
            if inflate and inflate.unconsumed_tail:
                break
        return resp.geturl(), ctype, bytes(body[:limit])


def _oembed(url: str, host: str, lang: str) -> dict | None:
    """The title and author a site's oEmbed endpoint gives, or None (no
    endpoint, or none it answers): a post's text stands for the title of
    a post that has none (X)."""
    endpoint = OEMBED.get(_domain(host))
    if not endpoint:
        return None
    try:
        _, _, body = _get(endpoint + quote(url, safe=""), lang, "application/json", _OEMBED_BYTES)
        data = json.loads(body.decode("utf-8", "replace"))
    except (HTTPError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    title = _text(str(data.get("title") or "")) or _post_text(str(data.get("html") or ""))
    if not title:
        return None
    return {"title": title[:_TITLE_CHARS], "author": _text(str(data.get("author_name") or "")) or None}


def _page(url: str, host: str, lang: str) -> dict:
    landed, ctype, body = _get(url, lang, "text/html,application/xhtml+xml;q=0.9,*/*;q=0.5", _HEAD_BYTES, head=True)
    if not ("html" in ctype or "xml" in ctype) or _elsewhere(url, landed):
        return {}
    head = web_page.head(web_page.decode(body, ctype), landed)
    raw = head.names.get("og:title") or head.names.get("twitter:title") or head.title
    names = [head.meta.get("site", ""), *_site_names(host), *_site_names((urlsplit(landed).hostname or "").lower())]
    title = _title(raw, names)
    if not title:
        return {}
    return {"title": title, "author": head.meta.get("author") or None}


def _elsewhere(asked: str, landed: str) -> bool:
    """Whether a fetch of ``asked`` ended on a page that is not it: a
    sign-in, visitor, check or 404 page, or the site's home page."""
    if not landed or landed == asked:
        return False
    to, wanted = urlsplit(landed), urlsplit(asked)
    if _GATE_HOST.match(to.hostname or "") or _GATE_PATH.match(to.path or "/"):
        return True
    return to.path in ("", "/") and wanted.path not in ("", "/")


# --- titles -------------------------------------------------------------------------

def _text(value: str) -> str:
    return re.sub(r"\s+", " ", unescape(value)).strip()


def _post_text(html: str) -> str:
    """The words of an embedded post's first paragraph (X's blockquote)."""
    m = re.search(r"<p\b[^>]*>(.*?)</p>", html, re.S | re.I)
    return _text(re.sub(r"<br\s*/?>", " ", re.sub(r"<(?!br)[^>]+>", "", m.group(1)))) if m else ""


def _site_names(host: str) -> list[str]:
    """What titles call the site of ``host``: the host without www, its
    domain's name (bilibili for www.bilibili.com) and SITE_NAMES'."""
    if not host:
        return []
    bare = host.removeprefix("www.")
    labels = bare.split(".")
    name = labels[-2] if len(labels) >= 2 else labels[0]
    return [bare, name, *SITE_NAMES.get(_domain(host), ())]


def _title(raw: str, names: list[str]) -> str | None:
    """``raw`` without the site's ``names`` before or after it, or None when
    nothing else is left or what is left names an error."""
    title = _text(raw or "")
    names = sorted({n.strip() for n in names if n and n.strip()}, key=len, reverse=True)
    trimmed = True
    while trimmed and title:
        trimmed = False
        for name in names:
            n = re.escape(name)
            m = (re.match(rf"^(.+?){_SEP}{n}$", title, re.I)
                 or re.match(rf"^{n}\s+[-–—|]\s+(.+)$", title, re.I))
            if m and m.group(1).strip():
                title, trimmed = m.group(1).strip(), True
                break
    if not title or title.casefold() in {n.casefold() for n in names} or _ERROR_TITLE.match(title):
        return None
    return title[:_TITLE_CHARS]
