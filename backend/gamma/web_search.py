"""Optional general-web discovery through configured search APIs.

Public result URLs are evidence to inspect, not verified paper identities.
Configuration is read per call so tests and server environments need no
provider-specific dependencies. API keys never travel in query strings.
"""

import ipaddress
import json
import os
import re
from html.parser import HTMLParser
from urllib.error import HTTPError
from urllib.parse import urlencode, urlsplit, urlunsplit
from urllib.request import Request

from .net_guard import guarded_urlopen

SEARCH_LIMIT_DEFAULT = 8
SEARCH_LIMIT_MAX = 20
RESPONSE_MAX_BYTES = 2_000_000
TITLE_CHARS = 300
SNIPPET_CHARS = 1200
_BRAVE_ENDPOINT = "https://api.search.brave.com/res/v1/web/search"


class WebSearchError(Exception):
    """A safe diagnostic distinct from a successful search with no results."""

    def __init__(self, message: str, *, code: str = "unavailable"):
        super().__init__(message)
        self.code = code


class _PlainText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.hidden = 0

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style"}:
            self.hidden += 1
        elif tag in {"p", "div", "br", "li"} and not self.hidden:
            self.parts.append(" ")

    def handle_endtag(self, tag):
        if tag in {"script", "style"}:
            self.hidden = max(0, self.hidden - 1)
        elif tag in {"p", "div", "li"} and not self.hidden:
            self.parts.append(" ")

    def handle_data(self, data):
        if not self.hidden:
            self.parts.append(data)


def _plain(value, limit: int) -> str:
    if not isinstance(value, str):
        return ""
    parser = _PlainText()
    parser.feed(value[:limit * 20])
    parser.close()
    text = re.sub(r"\s+", " ", "".join(parser.parts)).strip()
    return text if len(text) <= limit else text[:limit - 1].rstrip() + "…"


def _http_url(value) -> str:
    """Syntax validation only; the fetch guard checks DNS before any use."""
    if not isinstance(value, str):
        return ""
    value = value.strip()
    if not value or len(value) > 4096 or re.search(r"[\s\x00-\x1f\x7f\\]", value):
        return ""
    if re.search(r"%(?![0-9a-fA-F]{2})", value):
        return ""
    try:
        parsed = urlsplit(value)
        if (parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname
                or parsed.username is not None or parsed.password is not None):
            return ""
        # Evaluating port rejects malformed/out-of-range ports.
        if parsed.port == 0:
            return ""
        if ":" in parsed.hostname:
            ipaddress.ip_address(parsed.hostname)
        else:
            host = parsed.hostname.encode("idna").decode("ascii").rstrip(".")
            if len(host) > 253 or any(
                not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?", label)
                for label in host.split(".")
            ):
                return ""
    except (ValueError, UnicodeError):
        return ""
    return value


def _config() -> tuple[str, str, str]:
    provider = os.environ.get("GAMMA_WEB_SEARCH_PROVIDER", "").strip().lower()
    token = os.environ.get("GAMMA_BRAVE_SEARCH_API_KEY", "").strip()
    searx = os.environ.get("GAMMA_SEARXNG_URL", "").strip()
    provider = provider or ("brave" if token else "searxng" if searx else "")
    if not provider:
        raise WebSearchError(
            "General web search is not configured. Set GAMMA_BRAVE_SEARCH_API_KEY "
            "or GAMMA_SEARXNG_URL on the server; scholarly search remains available.",
            code="not_configured")
    if provider not in {"brave", "searxng"}:
        raise WebSearchError("GAMMA_WEB_SEARCH_PROVIDER must be brave or searxng.", code="configuration")
    if provider == "brave":
        if not token or re.search(r"[\x00-\x20\x7f]", token) or not token.isascii():
            raise WebSearchError("Brave web search requires a valid GAMMA_BRAVE_SEARCH_API_KEY.", code="configuration")
        return provider, _BRAVE_ENDPOINT, token
    if not _http_url(searx):
        raise WebSearchError("GAMMA_SEARXNG_URL must be a public HTTP(S) URL without credentials.", code="configuration")
    parsed = urlsplit(searx)
    if parsed.query or parsed.fragment:
        raise WebSearchError("GAMMA_SEARXNG_URL must not contain a query string or fragment.", code="configuration")
    path = parsed.path.rstrip("/")
    if not path.endswith("/search"):
        path += "/search"
    return provider, urlunsplit((parsed.scheme, parsed.netloc, path, "", "")), ""


def _get_json(request: Request, provider: str) -> dict:
    try:
        with guarded_urlopen(request, timeout=20) as response:
            content_type = (response.headers.get("Content-Type") or "").split(";", 1)[0].strip().lower()
            if content_type != "application/json" and not content_type.endswith("+json"):
                raise WebSearchError("Web search returned a non-JSON response; check the provider configuration.", code="invalid_response")
            length = response.headers.get("Content-Length", "")
            if length.isdigit() and int(length) > RESPONSE_MAX_BYTES:
                raise WebSearchError("Web search response exceeded the 2 MB limit.", code="invalid_response")
            chunks, total = [], 0
            while True:
                chunk = response.read(min(65536, RESPONSE_MAX_BYTES - total + 1))
                if not chunk:
                    break
                total += len(chunk)
                if total > RESPONSE_MAX_BYTES:
                    raise WebSearchError("Web search response exceeded the 2 MB limit.", code="invalid_response")
                chunks.append(chunk)
            raw = b"".join(chunks)
    except HTTPError as exc:
        exc.close()
        if exc.code in {401, 403}:
            message = ("SearXNG denied search access; check that JSON output is enabled on the instance."
                       if provider == "searxng" else "Brave denied search access; check the server API key and plan.")
            raise WebSearchError(message, code="auth") from None
        if exc.code == 429:
            raise WebSearchError("Web search is rate limited; retry later.", code="rate_limit") from None
        raise WebSearchError("The web search provider is temporarily unavailable.", code="unavailable") from None
    except (OSError, ValueError):
        # URLError can contain the complete URL or a provider's error text.
        raise WebSearchError("The web search provider could not be reached; check its configuration or retry later.", code="unavailable") from None
    try:
        data = json.loads(raw)
    except (ValueError, UnicodeError, RecursionError):
        raise WebSearchError("Web search returned invalid JSON.", code="invalid_response") from None
    if not isinstance(data, dict) or data.get("error") or data.get("errors"):
        raise WebSearchError("Web search returned an invalid result envelope.", code="invalid_response")
    return data


def search_web(query: str, limit: int = SEARCH_LIMIT_DEFAULT) -> list[dict]:
    """Search public pages; raise WebSearchError for configuration/provider failures.

    Brave uses ``web.results``; SearXNG uses ``results``. Neither a missing
    provider nor a failed request is presented as an empty successful search.
    """
    if not isinstance(query, str) or not query.strip():
        raise WebSearchError("Web search requires a nonempty query.", code="invalid_query")
    query = " ".join(query.split())
    if len(query) > 600 or len(query.split()) > 75:
        raise WebSearchError("Use a web search query of at most 600 characters and 75 words.", code="invalid_query")
    try:
        limit = max(1, min(int(limit if limit is not None else SEARCH_LIMIT_DEFAULT), SEARCH_LIMIT_MAX))
    except (ValueError, TypeError, OverflowError):
        raise WebSearchError("Web search limit must be an integer.", code="invalid_query") from None
    provider, endpoint, token = _config()
    params = {"q": query, "count": limit} if provider == "brave" else {"format": "json", "q": query}
    request = Request(endpoint + "?" + urlencode(params), headers={
        "Accept": "application/json", "User-Agent": "gamma-pdf-annotator/1.0 (web search)",
    })
    if token:
        # urllib omits unredirected headers from redirected requests, including
        # cross-host redirects. Never forward the subscription token.
        request.add_unredirected_header("X-Subscription-Token", token)
    data = _get_json(request, provider)
    if provider == "brave":
        web = data.get("web")
        # Brave omits the optional web section when no web results exist.
        if "web" not in data and data.get("type") == "search" and isinstance(data.get("query"), dict):
            items = []
        else:
            items = web.get("results") if isinstance(web, dict) else None
    else:
        items = data.get("results")
    if not isinstance(items, list) or any(not isinstance(item, dict) for item in items):
        raise WebSearchError("Web search returned an invalid results list.", code="invalid_response")
    if provider == "searxng" and not items and data.get("unresponsive_engines"):
        raise WebSearchError("SearXNG search engines did not respond; retry later or check the instance.", code="unavailable")
    out, seen = [], set()
    for item in items:
        url = _http_url(item.get("url"))
        if not url:
            continue
        parsed = urlsplit(url)
        key = urlunsplit((parsed.scheme.lower(), parsed.netloc.lower(), parsed.path or "/", parsed.query, ""))
        if key in seen:
            continue
        seen.add(key)
        out.append({
            "title": _plain(item.get("title"), TITLE_CHARS) or parsed.hostname,
            "url": url,
            "snippet": _plain(item.get("description" if provider == "brave" else "content"), SNIPPET_CHARS),
            "provider": provider,
        })
        if len(out) >= limit:
            break
    return out
