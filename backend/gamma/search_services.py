"""The services behind the agent's online search (Settings → AI → Chat →
Online search).

General web search (the ``search_web`` tool) goes through one engine:

- ``ai`` — the chat's own AI connection, when its wire has a hosted search
  tool (OpenAI, the ChatGPT sign-in, Anthropic: what Codex and Claude use).
  One short call per search on that connection, counted as chat usage.
- ``brave`` — the Brave Search API, with the account's key.
- ``searxng`` — a SearXNG instance's JSON API: the account's own URL
  (through the SSRF guard, so a public host), else the server's
  ``GAMMA_SEARXNG_URL`` (the admin's instance, which may be private).

``engine`` picks one: "auto" (the default) takes Brave or SearXNG when set
up, else the AI connection; a named engine is used only when it is
available (a missing key never switches services silently); "off" turns
general web search off. With no engine the tool is not offered.

OpenAlex (``search_papers`` / ``related_papers``, gamma/openalex.py) works
without a key; the account's optional key lifts its budget.

Stored per account in users.db ``user_prefs`` under the reserved
``search-services`` key: {"engine", "brave": {api_key, updated_at},
"searxng": {url, updated_at}, "openalex": {api_key, updated_at}}. Like
``ai-settings`` the generic /api/prefs endpoints refuse the key; the read
path is the masked GET /api/ai/search-services.
"""

import html
import json
import os
import re
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit, urlunsplit
from urllib.request import Request, urlopen

from fastapi import HTTPException

from .db import get_pref, update_pref
from .logbuf import log
from .net_guard import guarded_urlopen
from .translate_engines import form_fields

PREF_KEY = "search-services"
ENGINES = ("auto", "ai", "brave", "searxng", "off")
TIMEOUT = 20
AI_TIMEOUT = 120
RESULTS_MAX = 20
_TITLE_MAX, _SNIPPET_MAX = 300, 400
BRAVE_URL = "https://api.search.brave.com/res/v1/web/search"
SERVER_SEARXNG_URL = os.environ.get("GAMMA_SEARXNG_URL", "").strip()

# The services a person can set up: their fields (secret ones are masked
# when listed) and whether they search the general web.
SERVICES = {
    "brave": {"label": "Brave Search", "fields": [{"id": "api_key", "secret": True}], "web": True},
    "searxng": {"label": "SearXNG", "fields": [{"id": "url", "secret": False}], "web": True},
    "openalex": {"label": "OpenAlex", "fields": [{"id": "api_key", "secret": True}], "web": False},
}
LABELS = {"ai": "your AI connection", "brave": "Brave Search", "searxng": "SearXNG"}


class SearchError(Exception):
    """The engine failed or refused the search; the message is for the model
    and the settings test."""


# --- stored settings ----------------------------------------------------------

def load(user_id: str) -> dict:
    value = get_pref(user_id, PREF_KEY)[0] if user_id else None
    if not isinstance(value, dict):
        return {}
    out = {k: v for k, v in value.items() if k in SERVICES and isinstance(v, dict)}
    if value.get("engine") in ENGINES:
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


def _searxng(saved: dict) -> tuple[str, bool]:
    """(instance URL, guarded): the account's own through the SSRF guard,
    else the server's, which the admin chose."""
    own = (saved.get("searxng") or {}).get("url") or ""
    if own:
        return own, True
    return SERVER_SEARXNG_URL, False


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
               "web": service["web"], "fields": fields, "updated_at": conf.get("updated_at", "")}
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
    if engine not in ENGINES:
        raise HTTPException(status_code=400, detail=f"engine must be one of {', '.join(ENGINES)}")

    def change(saved):
        saved["engine"] = engine
    _update(user_id, change)


# --- which engine a chat uses ---------------------------------------------------

def hosted_tool(runtime: dict | None, entry: dict | None) -> dict | None:
    """The hosted web-search tool of the wire a tool call on ``entry`` goes
    over (ai_protocols: Protocol.hosted_web_search), None without one."""
    from . import ai_protocols

    conf = ((runtime or {}).get("providers") or {}).get((entry or {}).get("provider"))
    if not conf:
        return None
    probe = [{"name": "probe", "description": "", "parameters": {"type": "object"}}]
    return ai_protocols.of(conf).wire(conf, probe).hosted_web_search(conf)


def web_engine(user_id: str, runtime: dict | None = None, entry: dict | None = None) -> str:
    """The engine a chat on ``entry`` searches the web with, "" for none."""
    saved = load(user_id)
    ready = {"brave": _complete("brave", saved.get("brave")), "searxng": bool(_searxng(saved)[0]),
             "ai": hosted_tool(runtime, entry) is not None}
    choice = saved.get("engine") or "auto"
    if choice == "auto":
        return next((e for e in ("brave", "searxng", "ai") if ready[e]), "")
    return choice if ready.get(choice) else ""


# --- searching --------------------------------------------------------------

def _plain(text, limit: int) -> str:
    text = re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", str(text or "")))).strip()
    return text if len(text) <= limit else text[:limit - 1].rstrip() + "…"


def _results(items, limit: int) -> list[dict]:
    """[{title, url, snippet}] with non-http(s) and credentialed URLs dropped
    and duplicates (by URL) removed, at most ``limit``."""
    out, seen = [], set()
    for item in items:
        url = str(item.get("url") or "").strip()
        parts = urlsplit(url)
        if parts.scheme not in ("http", "https") or not parts.hostname or parts.username:
            continue
        key = url.rstrip("/")
        if key in seen:
            continue
        seen.add(key)
        out.append({"title": _plain(item.get("title"), _TITLE_MAX) or parts.hostname,
                    "url": url, "snippet": _plain(item.get("snippet"), _SNIPPET_MAX)})
        if len(out) >= limit:
            break
    return out


def _fetch_json(req: Request, name: str, guarded: bool = False) -> dict:
    try:
        opener = guarded_urlopen if guarded else urlopen
        with opener(req, timeout=TIMEOUT) as resp:
            return json.loads(resp.read(2_000_000).decode("utf-8", "replace"))
    except HTTPError as e:
        raise SearchError(f"{name} answered HTTP {e.code}"
                          + (" — check the key" if e.code in (401, 403) else "")
                          + (" — its rate limit or plan quota is reached" if e.code == 429 else "")) from None
    except (URLError, TimeoutError, OSError) as e:
        raise SearchError(f"{name} did not answer ({getattr(e, 'reason', e)})") from None
    except ValueError:
        raise SearchError(f"{name} answered something other than JSON"
                          + (" — enable the json format in its settings.yml (search → formats)"
                             if name == "SearXNG" else "")) from None


def _brave(key: str, query: str, limit: int) -> list[dict]:
    req = Request(f"{BRAVE_URL}?{urlencode({'q': query, 'count': min(limit, 20)})}", headers={
        "Accept": "application/json", "X-Subscription-Token": key})
    data = _fetch_json(req, "Brave Search")
    items = ((data.get("web") or {}).get("results") or []) if isinstance(data, dict) else []
    return _results(({"title": i.get("title"), "url": i.get("url"), "snippet": i.get("description")}
                     for i in items if isinstance(i, dict)), limit)


def _searxng_search(base: str, guarded: bool, query: str, limit: int) -> list[dict]:
    base = re.sub(r"/search$", "", base.rstrip("/"))
    req = Request(f"{base}/search?{urlencode({'q': query, 'format': 'json'})}", headers={
        "Accept": "application/json", "User-Agent": "gamma-pdf-annotator/1.0 (web search)"})
    data = _fetch_json(req, "SearXNG", guarded=guarded)
    items = data.get("results") or [] if isinstance(data, dict) else []
    return _results(({"title": i.get("title"), "url": i.get("url"), "snippet": i.get("content")}
                     for i in items if isinstance(i, dict)), limit)


_AI_SYSTEM = ("You find web pages with your web search tool. Report only pages the search "
              "returned, never a URL from memory.")
_AI_PROMPT = (
    "Search the web for: {query}\n\n"
    "List up to {limit} pages that best match, most relevant first. Prefer primary sources: "
    "publisher and arXiv pages, author and lab publication lists, institutional repositories. "
    "One line per page, exactly:\n<title> | <url> | <one sentence on what the page holds>\n"
    "No other text.")
_LINE_RE = re.compile(r"^\s*(?:[-*]|\d+[.)])?\s*(.+?)\s*\|\s*<?(https?://[^\s|>]+)>?\s*(?:\|\s*(.*))?$")
_MD_LINK_RE = re.compile(r"^\s*(?:[-*]|\d+[.)])?\s*\[([^\]]+)\]\((https?://[^)\s]+)\)\s*[—–:-]?\s*(.*)$")


def _ai_search(runtime: dict, entry: dict, effort: str, query: str, limit: int) -> list[dict]:
    """One call on the chat's connection with its hosted search tool: the
    model's result lines, kept only for pages the search itself returned
    when the provider reports them (its sources and citations)."""
    from . import ai_client, ai_usage

    tool = hosted_tool(runtime, entry)
    if tool is None:
        raise SearchError("this chat's AI connection has no web search of its own")
    tools = [{"name": "web_search", "description": "", "parameters": {}, "hosted": tool}]
    text, sources = [], {}
    on_usage = ai_usage.recorder("chat", entry, runtime)
    try:
        # Only a connection that took an effort for the chat is sent one;
        # the search needs little thinking whatever the chat uses.
        with ai_client.open_ai([{"role": "user", "content": _AI_PROMPT.format(query=query, limit=limit)}],
                               _AI_SYSTEM, entry, runtime, effort="low" if effort else "",
                               max_tokens=2000, timeout=AI_TIMEOUT, stream=True, tools=tools) as resp:
            for kind, data in ai_client.sse_events(resp, ai_client.wire_protocol(runtime, entry, tools)):
                if kind == "text":
                    text.append(data)
                elif kind == "web_sources":
                    for s in data:
                        sources.setdefault(s["url"].rstrip("/"), s.get("title") or "")
                elif kind == "usage":
                    on_usage(data)
    except ai_client.UpstreamError as e:
        raise SearchError(f"the AI connection refused the search ({e})") from None
    except RuntimeError as e:
        # A reply cut short (no text, a paused turn) still leaves its sources.
        if not sources:
            raise SearchError(f"the AI connection's search failed ({e})") from None
    except OSError as e:
        raise SearchError(f"the AI connection did not answer ({e})") from None
    lines = []
    for raw in "".join(text).splitlines():
        m = _LINE_RE.match(raw) or _MD_LINK_RE.match(raw)
        if m:
            lines.append({"title": m.group(1).strip("*` "), "url": m.group(2).rstrip(".,;"),
                          "snippet": (m.group(3) or "").strip()})
    if sources:
        lines = [line for line in lines if line["url"].rstrip("/") in sources]
        listed = {line["url"].rstrip("/") for line in lines}
        lines += [{"title": title, "url": url, "snippet": ""}
                  for url, title in sources.items() if url not in listed]
    return _results(lines, limit)


def search(engine: str, user_id: str, query: str, limit: int, ai: dict | None = None) -> list[dict]:
    """[{title, url, snippet}] from ``engine`` (web_engine's pick); ``ai``
    carries the chat's ``runtime``, ``entry`` and ``effort`` for the "ai"
    engine. Raises SearchError."""
    limit = max(1, min(int(limit), RESULTS_MAX))
    saved = load(user_id)
    if engine == "brave":
        key = ((saved.get("brave") or {}).get("api_key") or "").strip()
        if not key:
            raise SearchError("Brave Search has no key set up")
        return _brave(key, query, limit)
    if engine == "searxng":
        base, guarded = _searxng(saved)
        if not base:
            raise SearchError("no SearXNG instance is set up")
        return _searxng_search(base, guarded, query, limit)
    if engine == "ai" and ai:
        return _ai_search(ai["runtime"], ai["entry"], ai.get("effort") or "", query, limit)
    raise SearchError("general web search is not set up")


def test(user_id: str, service: str) -> dict:
    """Check a service with the stored settings: {ok, text} or {ok: false,
    error} (in the body, like the AI provider test)."""
    if service not in SERVICES:
        raise HTTPException(status_code=404, detail="unknown search service")
    try:
        if service == "openalex":
            from . import openalex

            key = openalex_key(user_id)
            if not key:
                return {"ok": False, "error": "no key set up"}
            found = openalex.search("Degenerate Raman sideband cooling", 1, key=key)
            return {"ok": True, "text": "key accepted" + (f" — {found[0]['title'][:60]}" if found else "")}
        results = search(service, user_id, "Attention Is All You Need arXiv", 3)
    except SearchError as e:
        return {"ok": False, "error": str(e)}
    except Exception as e:  # openalex.OpenAlexError and transport failures
        log.warning(f"[search] {service} test failed: {e}")
        return {"ok": False, "error": str(e)}
    if not results:
        return {"ok": False, "error": "answered, but found nothing"}
    found = f"{len(results)} result{'s' if len(results) != 1 else ''}"
    return {"ok": True, "text": f"{found} — {results[0]['title'][:60]}"}
