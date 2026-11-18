"""The services behind the agent's online search (Settings → AI → Tool usage →
Online search): the stored settings, the engines and their adapters in one
module, the way translate_engines.py keeps the translators.

General web search (the ``search_web`` tool) goes through one of ``ENGINES``:

- ``brave`` — the Brave Search API, with the account's key.
- ``tavily`` — Tavily's search API, with the account's key.
- ``searxng`` — a SearXNG instance's JSON API: the account's own URL
  (through the SSRF guard, so a public host), else the server's
  ``GAMMA_SEARXNG_URL`` (the admin's instance, which may be private).
- ``ai`` — the chat's own AI connection, when its wire has a hosted search
  tool (OpenAI, the ChatGPT sign-in, Anthropic: what Codex and Claude use).
  One short call per search on that connection, counted as chat usage.

The account's choice (``CHOICES``) picks one: "auto" (the default) takes
the first engine that is ready, in ENGINES' order; a named engine is used
only when it is available (a missing key never switches services
silently); "off" turns general web search off. With no engine the tool is
not offered.

Every engine takes the same request — a query, a limit, a ``recency``
(RECENCY) and a ``site`` (site_of) — and answers ``[{title, url, snippet,
date?}]``. A result's date comes only from a field the engine fills itself,
never from text a model wrote, so a page the AI engine's model dates from
memory carries none.

OpenAlex (``search_papers`` / ``related_papers``, gamma/openalex.py) works
without a key; the account's optional key lifts its budget. It is a service
the settings list, not a web engine; the AI connection is a choice, never a
service row.

Stored per account in users.db ``user_prefs`` under the reserved
``search-services`` key: {"engine", "brave": {api_key, updated_at},
"tavily": {api_key, updated_at}, "searxng": {url, updated_at}, "openalex":
{api_key, updated_at}}. Like ``ai-settings`` the generic /api/prefs
endpoints refuse the key; the read path is the masked GET
/api/ai/search-services. An older server keeps only the services it knows
and reads an engine it does not know as Automatic.
"""

import email.utils
import html
import json
import os
import re
from datetime import date, datetime
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit, urlunsplit
from urllib.request import Request, urlopen

from fastapi import HTTPException

from . import ai_client, ai_protocols, ai_usage, openalex
from .db import get_pref, page_now, update_pref
from .logbuf import log
from .net_guard import guarded_urlopen
from .translate_engines import form_fields

PREF_KEY = "search-services"
CHOICES = ("auto", "ai", "brave", "tavily", "searxng", "off")
RECENCY = ("day", "week", "month", "year")
TIMEOUT = 20
AI_TIMEOUT = 120
RESULTS_MAX = 20
_TITLE_MAX, _SNIPPET_MAX, _DATE_MAX = 300, 400, 40
BRAVE_URL = "https://api.search.brave.com/res/v1/web/search"
TAVILY_URL = "https://api.tavily.com/search"
SERVER_SEARXNG_URL = os.environ.get("GAMMA_SEARXNG_URL", "").strip()
TEST_QUERY = "Python programming language"


class SearchError(Exception):
    """The engine failed or refused the search; the message is for the model
    and the settings test."""


# --- the request and its results ------------------------------------------------

_HOST_RE = re.compile(r"(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+(?:[a-z]{2,63}|xn--[a-z0-9-]{1,59})")


def site_of(value) -> str:
    """The one site a search is limited to, as a bare IDNA host: a URL,
    ``site:x``, ``*.x`` or a host with a path all give the host; several
    sites, or anything that is not a domain name, give ""."""
    text = re.sub(r"^site:", "", str(value or "").strip().lower()).strip()
    if not text or re.search(r"[\s,;|]", text):
        return ""
    try:
        host = urlsplit(text if "://" in text else "//" + text).hostname or ""
        host = host.lstrip("*.").rstrip(".").encode("idna").decode("ascii")
    except (ValueError, UnicodeError):
        return ""
    return host if _HOST_RE.fullmatch(host) else ""


def _with_site(query: str, site: str) -> str:
    """The query with its site as the ``site:`` operator the engines read."""
    return f"{query} site:{site}" if site else query


def _plain(text, limit: int) -> str:
    text = re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", str(text or "")))).strip()
    return text if len(text) <= limit else text[:limit - 1].rstrip() + "…"


def _untracked(url: str) -> str:
    """``url`` without its ``utm_*`` parameters (OpenAI's citations add
    ``utm_source=openai``); every other parameter stays as written."""
    parts = urlsplit(url)
    if "utm_" not in parts.query.lower():
        return url
    query = "&".join(p for p in parts.query.split("&") if p and not p.lower().startswith("utm_"))
    return urlunsplit(parts._replace(query=query))


def _url_key(url: str) -> str:
    """What the results for one page share."""
    return _untracked(url.strip()).rstrip("/")


_DATE_FORMATS = ("%B %d, %Y", "%b %d, %Y", "%d %B %Y", "%d %b %Y")


def _date(value) -> str:
    """A result's date as YYYY-MM-DD when it parses (ISO 8601, RFC 2822,
    "April 30, 2025"), else as the engine wrote it (Brave's "3 days ago");
    "" for none or an impossible ISO date."""
    text = _plain(value, _DATE_MAX) if isinstance(value, str) else ""
    if not text:
        return ""
    iso = re.match(r"(\d{4})-(\d{2})-(\d{2})", text)
    try:
        if iso:
            return date(int(iso[1]), int(iso[2]), int(iso[3])).isoformat()
        for form in _DATE_FORMATS:
            try:
                return datetime.strptime(text, form).date().isoformat()
            except ValueError:
                continue
        return email.utils.parsedate_to_datetime(text).date().isoformat()
    except (TypeError, ValueError):
        return "" if iso else text


def _results(items, limit: int) -> list[dict]:
    """[{title, url, snippet, date?}] with non-http(s) and credentialed URLs
    dropped, ``utm_*`` parameters stripped and duplicates (by URL) removed,
    at most ``limit``."""
    out, seen = [], set()
    for item in items:
        url = _untracked(str(item.get("url") or "").strip())
        parts = urlsplit(url)
        if parts.scheme not in ("http", "https") or not parts.hostname or parts.username:
            continue
        key = url.rstrip("/")
        if key in seen:
            continue
        seen.add(key)
        result = {"title": _plain(item.get("title"), _TITLE_MAX) or parts.hostname,
                  "url": url, "snippet": _plain(item.get("snippet"), _SNIPPET_MAX)}
        when = _date(item.get("date"))
        if when:
            result["date"] = when
        out.append(result)
        if len(out) >= limit:
            break
    return out


def _fetch_json(req: Request, name: str, guarded: bool = False) -> dict:
    try:
        opener = guarded_urlopen if guarded else urlopen
        with opener(req, timeout=TIMEOUT) as resp:
            return json.loads(resp.read(2_000_000).decode("utf-8", "replace"))
    except HTTPError as e:
        # Tavily answers 432 / 433 for a used-up plan or pay-as-you-go limit.
        raise SearchError(f"{name} answered HTTP {e.code}"
                          + (" — check the key" if e.code in (401, 403) else "")
                          + (" — its rate limit or plan quota is reached" if e.code in (429, 432, 433)
                             else "")) from None
    except (URLError, TimeoutError, OSError) as e:
        raise SearchError(f"{name} did not answer ({getattr(e, 'reason', e)})") from None
    except ValueError:
        raise SearchError(f"{name} answered something other than JSON"
                          + (" — enable the json format in its settings.yml (search → formats)"
                             if name == "SearXNG" else "")) from None


def _items(data, *path) -> list[dict]:
    """The result objects under ``path`` in an engine's JSON answer."""
    for key in path:
        data = data.get(key) if isinstance(data, dict) else None
    return [i for i in data if isinstance(i, dict)] if isinstance(data, list) else []


# --- the engines ---------------------------------------------------------------
# Each takes the stored settings, the query and the limit, and as keywords
# ``recency`` (a RECENCY value or "") and ``site`` (site_of's host or "");
# the AI engine also reads ``ai`` and ``meter`` (search()).

def _key(saved: dict, engine: str) -> str:
    key = ((saved.get(engine) or {}).get("api_key") or "").strip()
    if not key:
        raise SearchError(f"{ENGINES[engine]['label']} has no key set up")
    return key


_BRAVE_FRESHNESS = {"day": "pd", "week": "pw", "month": "pm", "year": "py"}


def _brave(saved, query, limit, *, recency, site, **_):
    params = {"q": _with_site(query, site), "count": min(limit, 20)}
    if recency:
        params["freshness"] = _BRAVE_FRESHNESS[recency]
    req = Request(f"{BRAVE_URL}?{urlencode(params)}", headers={
        "Accept": "application/json", "X-Subscription-Token": _key(saved, "brave")})
    data = _fetch_json(req, "Brave Search")
    # ``page_age`` is the page's own date (ISO); ``age`` a display form of
    # it, sometimes relative ("3 days ago").
    return _results(({"title": i.get("title"), "url": i.get("url"), "snippet": i.get("description"),
                      "date": i.get("page_age") or i.get("age")}
                     for i in _items(data, "web", "results")), limit)


def _tavily(saved, query, limit, *, recency, site, **_):
    body = {"query": query, "max_results": limit, "search_depth": "basic",
            # Off unless asked for (or the news topic): each result's date.
            "include_published_date": True}
    if recency:
        body["time_range"] = recency
    if site:
        body["include_domains"] = [site]
    req = Request(TAVILY_URL, data=json.dumps(body).encode(), headers={
        "Accept": "application/json", "Content-Type": "application/json",
        "Authorization": f"Bearer {_key(saved, 'tavily')}"})
    data = _fetch_json(req, "Tavily")
    return _results(({"title": i.get("title"), "url": i.get("url"), "snippet": i.get("content"),
                      "date": i.get("published_date")} for i in _items(data, "results")), limit)


def _searxng(saved: dict) -> tuple[str, bool]:
    """(instance URL, guarded): the account's own through the SSRF guard,
    else the server's, which the admin chose."""
    own = (saved.get("searxng") or {}).get("url") or ""
    if own:
        return own, True
    return SERVER_SEARXNG_URL, False


def _searxng_search(saved, query, limit, *, recency, site, **_):
    base, guarded = _searxng(saved)
    if not base:
        raise SearchError("no SearXNG instance is set up")
    base = re.sub(r"/search$", "", base.rstrip("/"))
    params = {"q": _with_site(query, site), "format": "json"}
    if recency:
        params["time_range"] = recency
    req = Request(f"{base}/search?{urlencode(params)}", headers={
        "Accept": "application/json", "User-Agent": "gamma-pdf-annotator/1.0 (web search)"})
    data = _fetch_json(req, "SearXNG", guarded=guarded)
    return _results(({"title": i.get("title"), "url": i.get("url"), "snippet": i.get("content"),
                      "date": i.get("publishedDate")} for i in _items(data, "results")), limit)


def hosted_tool(runtime: dict | None, entry: dict | None, site: str = "") -> dict | None:
    """The hosted web-search tool of the wire a tool call on ``entry`` goes
    over (ai_protocols: Protocol.tool_wire, hosted_web_search), limited to
    ``site`` when one is given; None without one."""
    conf = ((runtime or {}).get("providers") or {}).get((entry or {}).get("provider"))
    if not conf:
        return None
    return ai_protocols.of(conf).tool_wire(conf).hosted_web_search(conf, site)


_AI_SYSTEM = ("You find web pages with your web search tool. Report only pages the search "
              "returned, never a URL from memory.")
_AI_PROMPT = (
    "Search the web for: {query}\n\n{recency}"
    "List up to {limit} pages that best match, most relevant first. Prefer primary and authoritative "
    "sources — the official site or documentation, the original publication, announcement or data, "
    "the people or organisation concerned — over aggregators, reposts and listicles. "
    "One line per page, exactly:\n<title> | <url> | <one sentence on what the page holds>\n"
    "No other text.")
_AI_RECENCY = "Only pages published or updated in the past {recency}: today is {today} (UTC).\n\n"
_LINE_RE = re.compile(r"^\s*(?:[-*]|\d+[.)])?\s*(.+?)\s*\|\s*<?(https?://[^\s|>]+)>?\s*(?:\|\s*(.*))?$")
_MD_LINK_RE = re.compile(r"^\s*(?:[-*]|\d+[.)])?\s*\[([^\]]+)\]\((https?://[^)\s]+)\)\s*[—–:-]?\s*(.*)$")


def _ai_prompt(query: str, limit: int, recency: str, site: str) -> str:
    return _AI_PROMPT.format(query=_with_site(query, site), limit=limit,
                             recency=_AI_RECENCY.format(recency=recency, today=page_now()[:10]) if recency else "")


def _ai_search(saved, query, limit, *, recency, site, ai, meter):
    """One call on the chat's connection with its hosted search tool: the
    model's result lines, kept only for pages the search itself returned
    when the provider lists them (its sources and citations; a list of none
    keeps none). A reply that never searched, or whose search failed and
    found nothing, is a SearchError: its lines could only be URLs from
    memory. Only a wire that lists nothing (the ChatGPT backend, asked for
    no sources) has its lines taken as the model wrote them."""
    ai = ai or {}
    runtime, entry = ai.get("runtime"), ai.get("entry")
    tool = hosted_tool(runtime, entry, site)
    if tool is None:
        raise SearchError("this chat's AI connection has no web search of its own")
    tools = [{"name": "web_search", "description": "", "parameters": {}, "hosted": tool}]
    text, sources = [], {}  # sources: url key -> {url, title, date}
    listed = False  # the provider listed what its search found (even nothing)
    searched, failure = False, ""  # failure: the last error code a search reported
    record = ai_usage.recorder("chat", entry, runtime)

    def on_usage(usage):
        record(usage)
        if meter is not None:
            for key, value in usage.items():
                if isinstance(value, int):
                    meter[key] = meter.get(key, 0) + value

    try:
        # Only a connection that took an effort for the chat is sent one;
        # the search needs little thinking whatever the chat uses.
        with ai_client.open_ai([{"role": "user", "content": _ai_prompt(query, limit, recency, site)}],
                               _AI_SYSTEM, entry, runtime, effort="low" if ai.get("effort") else "",
                               max_tokens=2000, timeout=AI_TIMEOUT, stream=True, tools=tools) as resp:
            for kind, data in ai_client.sse_events(resp, ai_client.wire_protocol(runtime, entry, tools)):
                if kind == "text":
                    text.append(data)
                elif kind == "web_searched":
                    searched, failure = True, data.get("error") or failure
                elif kind == "web_sources":
                    listed = True
                    for s in data:
                        found = sources.setdefault(_url_key(s["url"]), {"url": s["url"], "title": "", "date": ""})
                        found["title"] = found["title"] or s.get("title") or ""
                        found["date"] = found["date"] or s.get("date") or ""
                elif kind == "usage":
                    on_usage(data)
    except ai_client.CallRefused as e:
        raise SearchError(str(e)) from None
    except ai_client.UpstreamError as e:
        raise SearchError(f"the AI connection refused the search ({e})") from None
    except RuntimeError as e:
        # A reply cut short (no text, a paused turn) still leaves its sources.
        if not sources:
            raise SearchError(f"the AI connection's search failed ({e})") from None
    except OSError as e:
        raise SearchError(f"the AI connection did not answer ({e})") from None
    if not searched:
        raise SearchError("the AI connection answered without searching the web")
    if failure and not sources:
        raise SearchError(f"the AI connection's web search did not complete ({failure})")
    lines = []
    for raw in "".join(text).splitlines():
        m = _LINE_RE.match(raw) or _MD_LINK_RE.match(raw)
        if m:
            lines.append({"title": m.group(1).strip("*` "), "url": m.group(2).rstrip(".,;"),
                          "snippet": (m.group(3) or "").strip()})
    if listed:
        lines = [{**line, "date": sources[_url_key(line["url"])]["date"]} for line in lines
                 if _url_key(line["url"]) in sources]
        listed = {_url_key(line["url"]) for line in lines}
        lines += [{**s, "snippet": ""} for key, s in sources.items() if key not in listed]
    return _results(lines, limit)


# The web engines, in the order Automatic tries them: the label, the fields
# the settings form asks for (secret ones are masked when listed; none:
# nothing to set up) and the adapter.
ENGINES = {
    "brave": {"label": "Brave Search", "fields": [{"id": "api_key", "secret": True}], "search": _brave},
    "tavily": {"label": "Tavily", "fields": [{"id": "api_key", "secret": True}], "search": _tavily},
    "searxng": {"label": "SearXNG", "fields": [{"id": "url", "secret": False}], "search": _searxng_search},
    "ai": {"label": "your AI connection", "fields": [], "search": _ai_search},
}
# The services a person can set up: the engines with fields, and OpenAlex.
SERVICES = {
    **{eid: {"label": e["label"], "fields": e["fields"]} for eid, e in ENGINES.items() if e["fields"]},
    "openalex": {"label": "OpenAlex", "fields": [{"id": "api_key", "secret": True}]},
}
LABELS = {eid: e["label"] for eid, e in ENGINES.items()}


# --- stored settings ----------------------------------------------------------

def load(user_id: str) -> dict:
    value = get_pref(user_id, PREF_KEY)[0] if user_id else None
    if not isinstance(value, dict):
        return {}
    out = {k: v for k, v in value.items() if k in SERVICES and isinstance(v, dict)}
    if value.get("engine") in CHOICES:
        out["engine"] = value["engine"]
    return out


def _update(user_id: str, change) -> None:
    """Read-modify-write in one transaction (db.update_pref), so saving one
    service never drops another saved meanwhile."""
    def apply(value):
        saved = value if isinstance(value, dict) else {}
        change(saved)
        return saved
    update_pref(user_id, PREF_KEY, apply)


def _complete(service: str, conf) -> bool:
    return isinstance(conf, dict) and all((conf.get(f["id"]) or "").strip()
                                          for f in SERVICES[service]["fields"])


def openalex_key(user_id: str) -> str:
    return ((load(user_id).get("openalex") or {}).get("api_key") or "").strip()


def masked(user_id: str, can_edit: bool) -> dict:
    """The settings view: the engine choice and every service, its fields
    (secrets as a last-4 hint) and whether it is ready."""
    saved = load(user_id)
    rows = []
    for sid, service in SERVICES.items():
        conf = saved.get(sid) or {}
        fields = {}
        for f in service["fields"]:
            value = (conf.get(f["id"]) or "").strip()
            fields[f["id"]] = ("…" + value[-4:] if len(value) > 8 else "set") if f["secret"] and value else value
        row = {"id": sid, "label": service["label"], "configured": _complete(sid, conf),
               "web": sid in ENGINES, "fields": fields, "updated_at": conf.get("updated_at", "")}
        if sid == "searxng" and not row["configured"] and SERVER_SEARXNG_URL:
            row["server"] = True  # the admin's instance serves this account
        rows.append(row)
    return {"engine": saved.get("engine") or "auto", "services": rows, "can_edit": can_edit}


def _validated_url(value: str) -> str:
    parts = urlsplit(value)
    if parts.scheme not in ("http", "https") or not parts.hostname or parts.username or parts.password:
        raise HTTPException(status_code=400, detail="url must be an http(s) address without credentials")
    return urlunsplit((parts.scheme, parts.netloc, parts.path.rstrip("/"), "", ""))


def save(user_id: str, service: str, fields: dict) -> None:
    """Set a service's fields. A secret left empty keeps the stored value
    (the form never sees it); a plain field is taken as given."""
    if service not in SERVICES:
        raise HTTPException(status_code=404, detail="unknown search service")

    def change(saved):
        conf = form_fields(SERVICES[service]["fields"], saved.get(service) or {}, fields)
        if "url" in conf:
            conf["url"] = _validated_url(conf["url"])
        saved[service] = conf
    _update(user_id, change)


def remove(user_id: str, service: str) -> None:
    if service not in SERVICES:
        raise HTTPException(status_code=404, detail="unknown search service")
    _update(user_id, lambda saved: saved.pop(service, None))


def set_engine(user_id: str, engine: str) -> None:
    if engine not in CHOICES:
        raise HTTPException(status_code=400, detail=f"engine must be one of {', '.join(CHOICES)}")

    def change(saved):
        saved["engine"] = engine
    _update(user_id, change)


# --- which engine a chat uses ---------------------------------------------------

def web_engine(user_id: str, runtime: dict | None = None, entry: dict | None = None) -> str:
    """The engine a chat on ``entry`` searches the web with, "" for none."""
    saved = load(user_id)
    ready = {eid: _complete(eid, saved.get(eid)) for eid in ENGINES if eid in SERVICES}
    ready["searxng"] = bool(_searxng(saved)[0])  # the admin's instance too
    ready["ai"] = hosted_tool(runtime, entry) is not None
    choice = saved.get("engine") or "auto"
    if choice == "auto":
        return next((eid for eid in ENGINES if ready[eid]), "")
    return choice if ready.get(choice) else ""


# --- searching --------------------------------------------------------------

def search(engine: str, user_id: str, query: str, limit: int, *, recency: str = "", site: str = "",
           ai: dict | None = None, meter: dict | None = None) -> list[dict]:
    """[{title, url, snippet, date?}] from ``engine`` (web_engine's pick).
    ``recency`` keeps pages of the past day, week, month or year (RECENCY;
    anything else is none), ``site`` one domain (normalized by site_of).
    ``ai`` carries the chat's ``runtime``, ``entry`` and ``effort`` for the
    "ai" engine, and ``meter``, a dict, receives that engine's integer token
    counts ({input, output, cache_read, cache_write}, added to what it
    holds; never the served ``speed``) as the provider reports them — a
    caller that passes ``meter={}`` reads what the search spent once it
    returned or raised alike. Raises SearchError."""
    if engine not in ENGINES:
        raise SearchError("general web search is not set up")
    limit = max(1, min(int(limit), RESULTS_MAX))
    return ENGINES[engine]["search"](load(user_id), query, limit, recency=recency if recency in RECENCY else "",
                                     site=site_of(site), ai=ai, meter=meter)


def test(user_id: str, service: str) -> dict:
    """Check a service with the stored settings: {ok, text} or {ok: false,
    error} (in the body, like the AI provider test)."""
    if service not in SERVICES:
        raise HTTPException(status_code=404, detail="unknown search service")
    try:
        if service == "openalex":
            key = openalex_key(user_id)
            if not key:
                return {"ok": False, "error": "no key set up"}
            found = openalex.search("Degenerate Raman sideband cooling", 1, key=key)
            return {"ok": True, "text": "key accepted" + (f" — {found[0]['title'][:60]}" if found else "")}
        results = search(service, user_id, TEST_QUERY, 3)
    except SearchError as e:
        return {"ok": False, "error": str(e)}
    except Exception as e:  # openalex.OpenAlexError and transport failures
        log.warning(f"[search] {service} test failed: {e}")
        return {"ok": False, "error": str(e)}
    if not results:
        return {"ok": False, "error": "answered, but found nothing"}
    found = f"{len(results)} result{'s' if len(results) != 1 else ''}"
    return {"ok": True, "text": f"{found} — {results[0]['title'][:60]}"}
