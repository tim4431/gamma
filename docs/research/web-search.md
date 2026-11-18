# General web search: reading the web for a question that is not about a paper

November 2026 (surveyed on the 17th). The chat's web tools were built to find
papers, and a general question paid for it: a release, a price, a person,
this week's news or a docs page went through a search described as a paper
finder and a fetch that ran the paper resolver first and read the page with a
regex tag stripper. This note records what was measured on those tools, what
the providers, the search APIs and two agent CLIs offered at the time, which
HTML libraries were weighed for the page reader, and what was built and left
out. How the tools work now is in
[dev/ai_tools.md](../dev/ai_tools.md#the-web-tools-both-scopes).

## What a general question met

Ten requests were traced through the tool surface as it stood, with real
fetches from a throwaway data directory: what's new in Python 3.14; a blog
post's summary; the most popular implementation of *Attention Is All You
Need* on GitHub; a Thorlabs part's price; who leads an MIT group now; this
week's news on EU AI regulation; `asyncio.TaskGroup` in the docs; a dataset's
licence; Chinese sources on a physics topic; and the paper-discovery recipe,
which had to stay as it was. What they had in common:

- **Nothing told the model to look a fact up**, or what day it was. The web
  paragraph of the prompt triggered for works the pages cite and for "find
  papers"; the chat's built-in prompt allowed general background from memory,
  and no prompt carried today's date, so "now", "this week" and "the latest"
  had nothing to stand on.
- **`search_web` was a paper finder.** Its spec, its result head ("leads, not
  verified papers … read a page with fetch_paper(source=URL, title=the paper's
  title)") and the AI engine's prompt ("publisher and arXiv pages, author and
  lab publication lists") were all about papers. It had no recency and no
  site filter, and it dropped the dates every engine returned (Brave's
  `page_age`, SearXNG's `publishedDate`, Anthropic's `page_age`).
- **Every URL went through the paper resolver first.** `resolve_source` read
  up to 600 KB looking for a PDF, then `ai_web._fetch` downloaded the page a
  second time. A page with `citation_doi` or `dc.identifier` metadata would
  set off Unpaywall and the open-access attempts and come back as an
  `abstract` wall, as a Zenodo dataset's DOI did (its record page is a
  script with 34 characters of text). And the resolver's message to a person
  clipping, "Download it in your browser and drop it onto Gamma.", became the
  head of a successful read of a docs page.
- **A wall on any page opened a handoff card** that could only bring back a
  PDF and, with the reply waiting on it, held the reply up to five minutes
  for a product page or a news site.
- **Plain text and JSON were refused** ("not a web page"), so a `LICENSE` on
  `raw.githubusercontent.com` and an API answer such as `api.github.com`'s
  repository record could not be read.
- **The page cache had no expiry**: a price or a news index read once was
  served from memory until crowded out.
- **The windows were blind.** A 46,540-character docs page was one text with
  no headings, read 6,000 characters at a time by offset, its sidebar first.

## The old page reader, measured

`ai_web.html_text` dropped the head, scripts, styles, `noscript`, SVG and
templates with regexes, turned block tags into line breaks and every other
tag into a space. It kept the words and lost every structure the model needs:

- Links lost their addresses, so the model could not follow a link on a
  general page (the only links it had were the PDF-looking ones).
- Headings were bare lines, lists lost their markers, a table came out one
  cell per line, and code lost its indentation, its syntax-highlighting spans
  becoming spaces between the tokens (`print ( f "started at { time ...`).
- `<[^>]+>` ends at a `>` inside a quoted attribute. Wikipedia's Parsoid
  markup carries `data-mw='…&lt;ref>…'`, so the attribute's tail spilled into
  the text: the lead sentence of *Photosynthesis* and every chemical equation
  on the page became `{{Cite web |title=…` and `dioxide"},"2":{"wt":…` JSON.
- The charset came from the Content-Type only. A Shift_JIS novel on
  aozora.gr.jp, declared in its `<meta>` and `<?xml encoding>` and served
  as `text/html` without a charset, came out as 318,799 characters with
  208,872 U+FFFD and a mojibake title.
- A page over 2 MB was refused, and the message was wrong ("2 MB, cap 2 MB"
  for 2.98 MB of *United States* on Wikipedia).
- Fastly's "Client Challenge" (pypi.org), served as 200, was read as a
  209-character document.

Eighteen saved pages, side by side. "Top" and "bottom" are the share of the
text before the content's first sentence and after its last (anchor phrases
chosen per page), measured on the text with link addresses, emphasis and
picture alt text taken out, so a reader that writes links is not charged for
them. The as-built reader's character counts include its Markdown links.

| Page | Old reader, chars | Old top / bottom % | As built, chars | As built top / bottom % |
|---|---:|---|---:|---|
| Wikipedia, *Photosynthesis* | 104,615 | 3.6 / 12.0 | 160,956 | 0.0 / 0.7 |
| Wikipedia (zh), 光合作用 | 23,273 | 1.2 / 51.2 | 22,422 | 0 / 0 |
| Wikipedia, countries by population | 73,275 | 3.8 / 9.1 | 120,141 | 0.1 / 0 |
| docs.python.org, asyncio tasks | 46,540 | 1.5 / 2.4 | 53,652 | 0.1 / 0 |
| GitHub, fastapi/fastapi | 17,310 | 20.3 / 4.3 | 24,690 | 8.8 / 2.8 |
| BBC News article | 11,018 | 5.7 / 29.1 | 10,323 | 3.6 / 2.7 |
| The Guardian article | 11,435 | 14.6 / 14.6 | 11,287 | 3.0 / 11.2 |
| BBC News front page | 9,794 | 5.2 / 18.1 | 12,449 | 1.2 / 0.2 |
| simonwillison.net post | 42,783 | 0.6 / 3.7 | about the same | 0.1–0.2 / 0 |
| martinfowler.com article | 50,957 | 2.1 / 0.6 | about the same | 0.1–0.2 / 0 |
| Stanford NLP people page | 24,934 | 0.8 / 1.8 | about the same | 0.1–0.2 / 0 |
| MIT PDOS publications | 443,753 | 0 / 65.4 | 467,934 | 0 / 0 |
| arXiv abstract page | 5,079 | 6.1 / 62.6 | 7,297 | 4.4 / 57.2 |
| MDN, `Array.map` | 12,650 | 17.1 / 14.3 | 11,458 | 0.6 / 0.1 |
| Read the Docs, requests quickstart | 18,075 | 0.1 / 4.2 | about the same | 0.1–0.2 / 0 |
| text.npr.org | 1,572 | 5.9 / 5.9 | 2,226 | 4.2 / 0 |
| LinkedIn company page | 13,116 | 2.6 / 2.6 | 20,246 | 2.2 / 18.7 |
| x.com profile | 2,836 | 19.6 / 6.7 | 3,962 | 2.0 / 0.3 |

What filled the old reader's top and bottom: Wikipedia's main menu, its
contents and a 160-language list; GitHub's whole product mega-menu (20 %);
MDN's HTML, CSS and JavaScript navigation tree (17 %); the Guardian's edition
and section menus; the BBC's list of every World Service language (29 %);
Chinese Wikipedia's navboxes and footer (51 %); arXiv's arXivLabs and
Bibliographic Tools blurbs (63 %). Cookie banners were not a problem in
server HTML: they are injected by script.

The MIT list needs a word. The page has a stray `</html>` inside a 2015
abstract, where the prototype stopped (159,691 characters). The as-built
reader ignores `</body>` and `</html>` as a browser does and reads every year
back to 1993; against an end anchor at the page's real end its top and bottom
are 0 %, and much of what the first anchor counted as the old reader's 65 %
"bottom" is those years. Beyond the
table: the Shift_JIS novel reads as 184,164 characters with no U+FFFD and the
title 夏目漱石 こころ; *United States* (2.98 MB) reads as 459,339 characters
in 294 ms; the bot-check and JavaScript-shell pages read the same as in the
prototype, and a single-page app whose only text is in `<noscript>` reads as
nothing, which the fetch reports as the `script` wall. The reader takes
10–300 ms a page, 1.2 to 1.4 times the prototype's time, against 0.1–1.4 s of
network per fetch.

## What a fetch of a non-paper URL cost

`fetch_document` end to end on non-paper URLs, cache cleared between runs,
before the change:

| Source | Seconds | Requests | What came back |
|---|---:|---:|---|
| en.wikipedia.org/wiki/Photosynthesis | 0.16 | 2 | 104,615 chars, six "PDF links" (the first `Special:DownloadAsPdf`) |
| docs.python.org, asyncio tasks | 0.33 | 2 | 46,540 chars |
| github.com/fastapi/fastapi | 1.36 | 2 | 17,310 chars, the README starting about 5,400 characters in |
| bbc.com article | 0.15 | 2 | 11,018 chars |
| simonwillison.net post | 0.12 | 2 | 42,783 chars |
| en.wikipedia.org/wiki/United_States | 0.05 | 2 | refused: "document too large (2 MB, cap 2 MB)" |
| pypi.org/project/requests | 0.30 | 2 | 209 chars titled "Client Challenge", no wall |
| stackoverflow.com question | 0.06 | 2 | the `captcha` wall (Cloudflare 403) |
| x.com/karpathy | 0.72 | 2 | 2,836 chars |

Every page cost two GETs, the resolver's and the page's. The time was cheap
where the page advertised neither a PDF nor a DOI; the cost was the framing,
the walls and the refusals.

## Walls by site

What a server-side GET with browser headers got, and how the old rules read
it:

| Site | Response | Read as |
|---|---|---|
| stackoverflow.com, unix.stackexchange.com, superuser.com, apnews.com, medium.com, quora.com | Cloudflare "Just a moment..." 403 | `captcha` (right) |
| nytimes.com (403), reuters.com (401) | DataDome pages of about 770 bytes | `captcha` (right) |
| discuss.python.org | 403 "Checking your browser..." | `denied` from the status only; the title was not a known marker |
| zhihu.com | 403 | `denied` |
| pypi.org | 200, Fastly "Client Challenge" (`/_fs-ch-…` assets, "JavaScript is disabled") | not seen: returned as a document |
| old.reddit.com | redirect to `/login/?dest=…` | `login` (right) |

Pages with nothing to read without their scripts: www.reddit.com (an 8 KB
shell), app.diagrams.net, a Thorlabs product page, a Zenodo record (34
characters). x.com renders server-side but streams its posts into `<div
hidden id="S:0">` segments that a script moves into place, so a reader that
drops `[hidden]` loses them. GitHub's repository pages and LinkedIn's guest
view render on the server.

## The landscape in November 2026

### Search hosted by the provider, per wire

- **Anthropic Messages.** `web_search_20250305` is the basic tool, on every
  model and platform, and the only version on Vertex and on Foundry hosted on
  Azure. `web_search_20260209` adds dynamic filtering (Claude runs code over
  the results; Claude 4.6 and later), and `web_search_20260318` adds
  `response_inclusion`. From 20260209 on, `allowed_callers` defaults to the
  code-execution tool, and a model without programmatic tool calling gets a
  400 unless it is `["direct"]`. Parameters: `max_uses` per turn (failed
  searches count), `allowed_domains` or `blocked_domains` (never both),
  `user_location`. A result block is `web_search_tool_result` with
  `web_search_result {url, title, encrypted_content, page_age}` and no plain
  snippet; a failed search's content is `web_search_tool_result_error
  {error_code}` (`too_many_requests`, `invalid_tool_input`,
  `max_uses_exceeded`, `query_too_long`, `request_too_large`,
  `unavailable`); citations carry `cited_text` of up to 150 characters. $10
  per 1,000 searches plus tokens, failed searches not billed. The
  `web_fetch` server tool (`web_fetch_20250910` to `web_fetch_20260318`)
  fetches only URLs already in the conversation, runs no JavaScript, keeps to
  robots.txt and refuses private addresses.
- **OpenAI Responses.** `{"type": "web_search"}` is current and
  `web_search_preview` legacy. Parameters: `filters` (`allowed_domains`,
  `blocked_domains`, up to 100 each), `user_location`,
  `search_context_size`, `external_web_access` (false: the cached index
  only), `return_token_budget`, `search_content_types`. `include` can ask for
  `web_search_call.action.sources` and `web_search_call.results`. The output
  item is `web_search_call {id, status, action}`; citations are
  `url_citation` annotations. $10 per 1,000 calls plus the search content's
  tokens. On Chat Completions only `gpt-5-search-api` searches;
  `gpt-4o-search-preview` was shut down on 2026-07-23.
- **The ChatGPT sign-in (the Codex backend).** It takes the same hosted
  `web_search`: the Codex CLI sends it (`codex-rs/core/src/tools/hosted_spec.rs`)
  with `external_web_access` (false, cached, is Codex's default),
  `indexed_web_access` (Codex 0.142, 2026-06-22), `filters`,
  `user_location` and `search_context_size`. Sources arrive as
  `url_citation`s. Whether it honours `include` for the sources is unknown;
  Gamma sends `include: []` there. An independent comparison (kenimoto.dev,
  2026-10-14) scored OpenAI's cached mode 24 of 24 at a 6.2 s median, live
  22 of 24, and Claude 21 of 24 at 22 s.
- **OpenRouter** (Chat Completions). The `web` plugin and the `:online`
  suffix are deprecated in favour of server tools: `openrouter:web_search`
  (engine `auto`, `native`, `exa`, `firecrawl`, `parallel` or `perplexity`;
  `max_results` 1–25; allowed and excluded domains) and
  `openrouter:web_fetch`. Citations stream as `delta.annotations`
  (`url_citation`), usage as `server_tool_use_details`. Exa costs $0.007 a
  request; native search is passed through from the provider.
- **DeepSeek**, launched around 2026-11-13: `deepseek_web_search` and
  `deepseek_read_url` on Chat Completions, the Anthropic-compatible endpoint
  and Responses, for `deepseek-flash` only, with the annotations in the last
  chunk; and REST tools on the same key, `/v1/tools/search` (with `site:`,
  `after:` and `before:` operators) and `/v1/tools/fetch`, $0.75 per 1,000
  requests each.
- **Kimi.** The `$web_search` builtin was to be deprecated on 2026-10-20 and
  is gone from the guide; REST tools remain (`/v1/tools/search`,
  `search_pro` with up to five sites and a time window, `fetch`), and a
  Responses `web_search`.
- **GLM** (z.ai, bigmodel.cn). A Chat Completions `web_search` tool whose
  results come back in a top-level `web_search` array (where it sits in a
  stream is not documented), and a REST `/paas/v4/web_search` with
  `search_recency_filter` and `search_domain_filter`; on the Coding Plan,
  search is an MCP server billed from plan credits.
- **Qwen** (DashScope). Compatible mode's `enable_search` returns no sources
  ("the OpenAI-compatible protocol does not support returning search
  sources"), so a search that must name its pages cannot use it;
  `/compatible-mode/v1/responses` has `web_search` and `web_extractor`.

### Search APIs as engines

| API | Request | Recency and site | Dates | Price and limits | Notes |
|---|---|---|---|---|---|
| Brave Search | `X-Subscription-Token` | `freshness` `pd`/`pw`/`pm`/`py` or a range; `site:` in `q` | `page_age` (ISO), `age` (a display form) | $5 per 1,000 with $5 of monthly credit (the same page also says up to $20), card required, 50 QPS | Storage or training rights need a custom plan; an LLM Context endpoint returns page text |
| Tavily | `POST /search`, `Authorization: Bearer` | `time_range`, `start_date` / `end_date`; `include_domains` (up to 300), `exclude_domains` | `published_date` | 1,000 free credits a month, no card; $0.008 a credit; basic search 1 credit, advanced 2 | Built for server-side LLM use; optional `raw_content` |
| SearXNG | `/search?format=json` | `time_range` day, month, year (week in the code) | `publishedDate` | Free, self-hosted | JSON must be enabled in `settings.yml` (else 403); public instances turn it off and rate-limit |
| Exa | `x-api-key` | | | About $7 per 1,000 (auto), $4 (lite); $10 of monthly credit | Figures conflict between sources |
| Jina | `r.jina.ai/<url>` keyless at 20 a minute; `s.jina.ai` needs a key | | | 10 M free tokens a key | Sends the page's address to a third party; under Elastic since 2026-08 |
| Kagi | `Authorization: Bot` | | | About $12 per 1,000, billed apart from the consumer plan | Small index |
| Serper | `X-API-KEY` | | | 2,500 free; $1.00 to $0.30 per 1,000 | A Google results reseller, a terms-of-service grey zone |

Not available or not usable: Google's Custom Search JSON API closed to new
customers around 2026-01-20 and ends on 2027-01-01; Bing's Web Search API was
retired on 2025-08-11 (its keys answer 410, and its successor in Azure gives
no raw results); DuckDuckGo's HTML endpoint is against its terms for
automated use and has served a CAPTCHA per address since about October 2026;
Mojeek needs a paid key; Marginalia's index is niche and its terms
non-commercial. There is no practical keyless general search; a self-hosted
SearXNG is the keyless route.

### What Claude Code and Codex do

- **Claude Code's WebFetch** fetches locally (a URL of up to 2,000
  characters, http upgraded to https), follows a redirect on the same host and
  hands a cross-host one back to the model, caps the raw body at about 10 MB,
  converts HTML to Markdown and cuts it at about 100 KB, and caches a URL for
  15 minutes. A small model answers the caller's prompt over the page, with
  quotes of up to 125 characters, so the main model never sees the raw page.
- **Claude Code's WebSearch** is a separate call with `web_search_20250305`
  and `max_uses` 8; the main model gets the query, a list of title links, the
  sub-call's text and a reminder to cite the sources as links. That is the
  shape of Gamma's AI engine.
- **Codex** uses the hosted Responses `web_search`, cached by default, live
  with `--yolo` or `web_search = "live"`, configured under `tools.web_search`
  (context size, allowed domains, location).
- **Published practice.** Anthropic's multi-agent research write-up
  (2025-06-13): short, broad queries first, then narrower ones; source-quality
  heuristics, because agents favoured SEO farms over primary sources; several
  calls in parallel. "Writing effective tools for agents" (2025-09-11):
  high-signal fields, truncation with a way to narrow, actionable errors. The
  kenimoto.dev comparison: a model that believed it was 2024 rejected fresh
  results, so tell it the date.

## HTML libraries considered

Every target installs what `requirements.txt` or `requirements-mobile.txt`
pins: Docker, the PyInstaller desktop bundle, and the iPad and Android apps,
which run CPython 3.13 on Flet's wheels (`test_mobile_requirements.py` makes
every dependency a decision). The candidates were measured on five saved
pages (Wikipedia, the Python docs, FastAPI's tutorial, a GitHub README, the
BBC front page) in a throwaway virtualenv; the wheels were checked on PyPI
and pypi.flet.dev.

| Library | Licence | Needs | Quality | Why not |
|---|---|---|---|---|
| trafilatura 2.3.1 | Apache-2.0 | lxml (native) and about ten packages (courlan, htmldate, dateparser, regex, justext …) | The best of the libraries: clean Wikipedia with tables, FastAPI with fences, the README; poor on the BBC front page, as all were | Flet has lxml 6.1.3 for Android but 6.1.1 for the iPad, and 2.3.x needs 6.1.3, so the iPad would need 2.2.0 with skewed pins. "trafilatura when installed, the stdlib otherwise" is two code paths, and the apps would read pages differently from the server |
| readability-lxml 0.9 | Apache-2.0 | lxml, chardet, cssselect | Dropped Wikipedia's lead and began at "History"; HTML only | lxml on mobile; quality |
| newspaper4k 0.9.6 | MIT | bs4, lxml, feedparser, Pillow, nltk … | Built for news articles | Weight; scope |
| html2text 2025.4 | GPL-3.0-or-later (fits AGPL) | Pure Python | No content extraction (215 k characters of Wikipedia, 24 k of the BBC) | Duplicates the clipper's writer and still needs extraction |
| markdownify 1.2 with BeautifulSoup 4.15 | MIT | Pure Python | No content extraction (198 k characters of junk on the BBC) | Same |
| BeautifulSoup 4 alone | MIT | Pure Python | A tree only | `zotero_notes.Parser` already was one |
| justext 3.0 | BSD-2 | lxml | 0 characters on the BBC, 276 on FastAPI (it drops code) | Quality |
| inscriptis 2.7 | Apache-2.0 | lxml below 6.2, requests | Layout-kept text with no boilerplate removed | Quality |
| selectolax 1.0 (lexbor) | MIT | Native, with Flet wheels for iOS and Android | 1–3 ms, the fastest; a parser only | Worth it only if `html.parser` were too slow, which it is not: 2.98 MB of Wikipedia reads in under 300 ms |
| resiliparse 2.0 | Apache-2.0 | Native, no iOS or Android wheels | Plain text; 42 characters on the BBC | Platforms; quality |

Mozilla's Readability, which the extension vendors and runs in the browser
for the clipper, was the algorithm to learn from, not a dependency. The
choice was a reader in the repository on the standard library: no new pin,
no mobile decision, the same Markdown the clipper writes, and a quality the
prototype showed to match trafilatura's on docs pages once the heuristics
for Wikipedia and GitHub were tuned. `html.parser` costs 5–10 times the
regex stripper's time, which is little next to the network.

## What was built

- **One page GET for both readers** (`web_fetch.get`), with caps by what the
  body is: a PDF refused past 40 MB, anything else cut at 5 MB and flagged.
  `fetch_url` reads any page in one request; `fetch_paper` keeps the
  resolver and falls back to the same GET, so its page fallback is cut, not
  refused, and its note says what the resolver met in the model's words.
- **A page reader** (`web_page`) on a shared tree and writer
  (`html_markdown`): `zotero_notes.Parser` moved there and the clipper's
  writer with it, behind hooks so the clipper keeps its escaped output. The
  clipper's own tree builder went: it nested an unclosed `<li>` inside the one
  before it and ran out of stack at 1,200 nested `<span>`s. The heuristics
  are the prototype's, tuned on the pages above.
- **Links shown readable.** Wikipedia's links take *Photosynthesis* from 82 k
  characters of text to 157 k; Chinese Wikipedia's percent-encoded links were
  30.4 k inline and 21.8 k decoded. The reader shows a link's UTF-8 decoded
  and keeps every ASCII escape and the host, so the address still names the
  page, and `web_fetch.uri` turns an address copied that way back into the
  URI, before the SSRF guard looks at its host.
- **Walls as sentences on a general page**, with thin-page rules for body
  markers (a docs page under `/login/help` reads), Fastly's challenge and
  "Checking your browser" recognized, a meta refresh followed only from an
  interstitial, and no card: the handoff stays `fetch_paper`'s.
- **Text types read as text**, a 15-minute page cache counted from last use
  (Claude Code caches a URL for 15 minutes; a page read in windows stays the
  same), sections with offsets and a `find` for long pages.
- **`search_web` made general**: `recency` and `site` mapped per engine,
  dates from the engines' own fields only, `utm_*` taken off, Tavily added
  (free without a card, filters, dates), the AI engine's prompt asking for
  primary sources over aggregators, and its result refused when the reply
  never searched, or its search failed and reported no page (a model listing
  URLs from memory never passes). Anthropic's `max_uses` went from 3 to 5; Claude Code uses 8.
- **Today's date** in a chat that reads the web, on the oldest turn so the
  cached head reads the same all day.
- **The prompt**: the web paragraph says to look current, versioned or
  specific facts up instead of answering from memory, and sorts the armed
  tools into papers and the rest; the repeats of `fetch_paper`'s spec it
  dropped paid for that. With every web tool armed, the paragraph went from
  about 749 tokens to 668, and the web tools' specs from about 1,742 to
  1,993 (`fetch_url`'s own about 200).
- **The engines in one module**, a registry beside the settings as
  `translate_engines.py` keeps the translators, where an engine had touched
  six places; the hosted tool is asked of the wire a tool call goes over
  (`Protocol.tool_wire`) instead of a fake function tool.

## Left out, and why

- **Hosted search on Chat Completions gateways** (OpenRouter's server tool,
  DeepSeek's) and **provider REST search on the chat's key** (DeepSeek, Kimi,
  GLM): each is a request field and an annotation parser, but none could be
  checked without keys, DeepSeek's was days old, and whether coding-plan
  keys may call the REST tools is unclear.
- **Anthropic's `web_fetch` server tool.** Gamma fetches itself, with PDFs,
  walls, journal sign-ins and the handoff; the server tool refuses
  robots-blocked and private pages, and its only gain would be another IP
  address.
- **The newer `web_search` versions.** For a short sub-call that lists URLs,
  dynamic filtering adds code-execution latency and nested results, needs
  `allowed_callers: ["direct"]` on some models and is not on Vertex.
- **A JavaScript renderer.** A single-page app is the `script` wall; the
  model is told to work from the snippet or another source.
- **A `language` parameter**, an RSS renderer and JSON pretty-printing (text
  types are read as they are), Brave's `extra_snippets` and LLM Context,
  OpenAI's cached versus live search, `search_context_size` and
  `user_location`, and Anthropic's `cited_text` as a snippet.
- **Sources as links in the chat's interface**, and translated chip
  summaries.
- **Engines not to add**: Google's Custom Search and Bing (ending, ended),
  DuckDuckGo scraping (terms, CAPTCHA), Marginalia (non-commercial), Kimi's
  `$web_search` (deprecated), Qwen's `enable_search` (no sources).

## Still open

- **Unverified without live keys**: whether the ChatGPT backend honours
  `filters` (the Codex CLI sends them there) and `include` for the sources;
  Tavily's live answer (the body and `published_date` follow its docs);
  OpenRouter's and DeepSeek's hosted search; the `web_searched` events from a
  real stream on each wire, which the AI engine's refusal of an unsearched
  reply depends on; Brave's `age` and `page_age` (absent from the docs
  page read).
- **What the reader still keeps**: arXiv's abstract page keeps its arXivLabs
  and Bibliographic Tools tail (57 %), with no chrome marker to catch it
  short of a site rule; the Guardian keeps "More on this story" and "Most
  viewed" (11 %); LinkedIn its sign-in and jobs lists (19 %). GitHub's fork
  count in the repository header is lost (the visible copy is
  `aria-hidden`, the screen-reader copy `VisuallyHidden`); the About box
  still says it.
- **Choices to revisit**: a picture's `[image: alt]` everywhere costs about
  9 k characters on a news front page (the prototype showed pictures only
  inside `<figure>`); a table row whose cells are all empty is written.
- **`mathml.to_latex` writes `<mphantom>`'s content**, so Chinese
  Wikipedia's H₂O reads as `HA_2O`.

## Sources

- Anthropic, [Web search tool](https://platform.claude.com/docs/en/agents-and-tools/tool-use/web-search-tool)
  and [Web fetch tool](https://platform.claude.com/docs/en/agents-and-tools/tool-use/web-fetch-tool),
  read 2026-11-17.
- OpenAI, [Web search](https://developers.openai.com/api/docs/guides/tools-web-search)
  and [Pricing](https://developers.openai.com/api/docs/pricing), read 2026-11-17.
- OpenAI Codex, [`hosted_spec.rs`](https://github.com/openai/codex/blob/main/codex-rs/core/src/tools/hosted_spec.rs),
  read 2026-11-17.
- OpenRouter, [Web search](https://openrouter.ai/docs/guides/features/web-search)
  and [Agentic web tools](https://openrouter.ai/blog/announcements/agentic-web-tools/) (2026-05-07, updated 2026-06-24).
- DeepSeek, [Web tools](https://api-docs.deepseek.com/guides/web_tools) and
  [Search](https://api-docs.deepseek.com/guides/search/); Kimi, [Use web search](https://platform.kimi.ai/docs/guide/use-web-search);
  Z.ai, [Web search](https://docs.z.ai/guides/tools/web-search); Alibaba
  Cloud, [Model Studio web search](https://www.alibabacloud.com/help/en/model-studio/web-search)
  (updated 2026-11-16).
- Brave, [Search API](https://brave.com/search/api/); Tavily,
  [Search endpoint](https://docs.tavily.com/documentation/api-reference/endpoint/search);
  SearXNG, [Search API](https://docs.searxng.org/dev/search_api.html);
  Jina, [Reader](https://jina.ai/reader/); Google, [Custom Search JSON API](https://developers.google.com/custom-search/v1/overview);
  Microsoft, [Bing Search API retirement](https://learn.microsoft.com/en-us/lifecycle/announcements/bing-search-api-retirement).
- Claude Code's web tools, reverse-engineered: [mikhail.io](https://mikhail.io/2025/10/claude-code-web-tools/)
  (October 2025) and [quercle.dev](https://quercle.dev/blog/claude-code-web-tools).
- kenimoto.dev, [a comparison of the web search in Claude, Codex, Kimi and Qwen](https://kenimoto.dev/blog/llm-web-search-claude-codex-kimi-qwen/) (2026-10-14).
- Anthropic's engineering posts "How we built our multi-agent research
  system" (2025-06-13) and "Writing effective tools for agents" (2025-09-11).
