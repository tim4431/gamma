# TextFold study

Study date: 2026-10-16. A read-only audit of [chenxiachan/textfold](https://github.com/chenxiachan/textfold) at `2cd77e0` (v0.10.7, October 16) against Gamma's `dev` at `cc32382f`. Findings only, not an approved plan. TextFold links below point at that commit; Gamma references are `path:line` at that `dev`.

## What TextFold is

A local reader: a Python server with no framework, a reader in plain JavaScript, and an Electron app around both. It shows a paper, a web page or a file at five levels. Full is the author's text. Brief is one to three of each paragraph's own sentences, chosen by a model. Three levels written by the model go down to a map of section cards. Around the levels it has:

- questions answered with paragraph citations;
- highlights and margin notes;
- translation into twelve languages;
- a Zotero library read through Zotero Desktop's local API;
- a Claude Code mod and a Pi extension that fold an agent's replies the same way, with no second model.

It is about 18,500 lines. The levels are TextFold's product and are not proposed here.

**Method.** Read:

- model access: `llm.py`, `llm_openai.py`, `models.py`, `agents.py`, `bridges.py`, `providers.py`;
- questions and grounding: `notes.py`, `fold.py`, `ladder.py`, `translate.py`, `docs/format.md`;
- imports: `webpage.py`, `semantic.py`, `pdftext.py`, `zotero.py`, `epmc.py`, `biorxiv.py`;
- the two agent plugins, the desktop build and the tests.

Each lesson below was checked against Gamma's code at the lines cited. One thing was run: an in-memory SQLite FTS5 table fed through Gamma's own `normalize_text` and `fts_query` (item 3). No server was started.

## Ranked lessons

### 1. Wait out a rate limit or a busy provider, then ask again

**Priority:** high; about 25 lines plus a test.

**TextFold** waits on a 429, 502, 503 or 529, then sends the same request again, at most four times ([`llm_openai.py` L20](https://github.com/chenxiachan/textfold/blob/2cd77e0/textfold/llm_openai.py#L20), [L79–L83](https://github.com/chenxiachan/textfold/blob/2cd77e0/textfold/llm_openai.py#L79-L83)). The wait is the numeric `Retry-After` (at most 60 s), else 5, 15, 30 and 45 s. That schedule is sized for OpenRouter's free models, which allow 20 requests a minute.

**Here:**

- `ai_client.open_ai` (`backend/gamma/ai_client.py:238-269`) raises `UpstreamError` on the first HTTP error. `failure_kind` only names the error for the UI.
- The PDF translation tries a chunk twice, with no wait between tries (`frontend/src/pdf/PdfViewer.jsx:1319`). So three parallel chunks sent to a 20-a-minute model fail together within a second.

**To take:** a retry loop around the `urlopen` in `open_ai`, where no byte has reached the client yet. Rules for it:

- Free the call slot while waiting.
- Never wait out a quota 429, such as OpenAI's `insufficient_quota`.
- Cap the wait at about 15 s for chat; translation, metadata and cite use the full schedule.
- Leave errors that arrive inside a stream as they are.

### 2. A reasoning level is a request, not a condition

**Priority:** high; about 30 lines for (a) and 40 for (b). The vendor switches in (b) are item 6 of [open-weight-providers.md](open-weight-providers.md), still open.

**TextFold:**

- Any call that fails with a level set is made once more without one, and the stored reply records why ([`llm.py` L66–L78](https://github.com/chenxiachan/textfold/blob/2cd77e0/textfold/llm.py#L66-L78)).
- Inside the OpenAI-compatible caller, each request shape is tried in order, each with and without JSON mode: the vendor's own field, then `reasoning_effort`, then none. A 400, 404, 415 or 422 moves to the next shape. The shape that worked is remembered per (base URL, model, level) ([L29–L51](https://github.com/chenxiachan/textfold/blob/2cd77e0/textfold/llm_openai.py#L29-L51), [L84–L98](https://github.com/chenxiachan/textfold/blob/2cd77e0/textfold/llm_openai.py#L84-L98)).

The vendor fields:

| Vendor | Field |
|---|---|
| OpenRouter | `reasoning: {effort}`, or `{enabled: false}` for off |
| GLM, DeepSeek, Kimi | `thinking: {type}` |
| Qwen | `enable_thinking`, with `thinking_budget` |
| MiniMax | none: it always thinks |

**Here:** effort goes out unconditionally, and a 400 caused by the field fails the call:

- `ai_protocols/openai.py:152-153` sends `reasoning_effort`;
- `anthropic.py:136-139` sends `output_config.effort`;
- `responses.py:99-100` sends `reasoning.effort`.

The fallback pattern already exists for one case: `_NATIVE_PDF_REJECTED` (`routers/ai.py:1543`, `:1904-1912`) retries a refused native PDF as text and remembers the provider.

**To take:**

- **(a)** On a 400 or 422 with effort set, unless the error is too-long or auth, retry once without effort. Remember (entry, model) for the process, and correct the stream's model line, the way a corrected speed is re-sent.
- **(b)** A `thinking_fields(service, level)` table keyed by the `services.py` preset, which knows the vendor better than a host match does. Qwen's non-streamed calls need `enable_thinking: false` (gap 4 of the same note). (b) is only safe once (a) is in place.

### 3. Chinese and Japanese words inside a sentence are not found

**Priority:** high for CJK readers; about 50 lines plus an index rebuild.

**TextFold**'s own retrieval cuts CJK runs into character pairs and drops question pairs such as 什么 and 怎么 ([`notes.py` L106–L120](https://github.com/chenxiachan/textfold/blob/2cd77e0/textfold/notes.py#L106-L120)).

**Here:** both FTS5 tables use the default `unicode61` tokenizer (`pdf_index.py:24`, `db.py:442`), which keeps a run of CJK characters as one token.

Checked: 我们提出一种量子纠错码的新方法 was stored through `normalize_text` and queried through `fts_query`.

- 我们, the start of the run, is found through the prefix match.
- 纠错, 量子纠错码 and 新方法 are not.

This affects `search_library`, the agent's search and the global PDF search (`routers/search.py`). The notes search in the UI scans with a regex and is not affected (`routers/blocks.py:169-172`).

**To take:**

- At index and query time, space CJK characters apart. Use a function of its own, since the frontend mirrors `normalize_text`.
- Query a CJK run as a phrase of its characters.
- Join the characters again in snippets.
- Bump `INDEX_VERSION` so the indexes rebuild.

FTS5's `trigram` tokenizer is the alternative, but it misses two-character words, the most common kind.

### 4. A search that misses keeps the telling words

**Priority:** medium; about 20 lines.

**TextFold** drops a stop list that includes question words, keeps symbols such as `D=4` whole, and scores passages by IDF ([`notes.py` L106–L134](https://github.com/chenxiachan/textfold/blob/2cd77e0/textfold/notes.py#L106-L134)).

**Here:** `fts_query` ANDs every term (`block_index.py:27-36`). On a miss, `ai_tools.py:1767-1783` retries with the three longest words of three or more characters, with no stop list. "what is the threshold for D=4 surface code" falls back to `threshold surface what`.

**To take:** on a miss, drop stop words, keep tokens with digits, `=` or Greek letters, and run an OR query ordered by FTS5's `rank`. Its bm25 already weights terms by IDF. The results are already labelled approximate.

### 5. Check an answer's citations on the server when it ends

**Priority:** medium; about 80 lines plus a pill state.

**TextFold** has the model cite paragraphs by short numbers, because "a number is copied right far more often than an id" ([`notes.py` L138–L143](https://github.com/chenxiachan/textfold/blob/2cd77e0/textfold/notes.py#L138-L143)). `_cited` maps each number back to its paragraph. A number that matches nothing is kept, marked as broken ([L266–L283](https://github.com/chenxiachan/textfold/blob/2cd77e0/textfold/notes.py#L266-L283)).

**Here:** the model writes a page id, the physical page and a verbatim quote (`ai_tools.py:116`, [pdf_citations.md](../dev/pdf_citations.md)). Nothing checks them until a click, when the viewer runs its exact and fuzzy passes.

**To take:** when a reply ends, parse its citation links and check that:

- the page id is in the chat's scope;
- the page number is within the page count;
- the normalized quote is on that page, in the cached page text (`pdf_text.iter_page_texts`).

If the quote is on a neighbouring page, correct the page number. Otherwise mark the pill unverified before anyone clicks. pdfium's text and pdf.js's text differ, so flag softly and never rewrite a quote.

### 6. Fold long chat replies without a second model

**Priority:** medium; about 200 lines plus tests.

**TextFold**'s Claude Code mod adds one section to the system prompt ([`register.tsx` L13–L31](https://github.com/chenxiachan/textfold/blob/2cd77e0/plugins/textfold/hooks/register.tsx#L13-L31)). It asks for:

- the answer in the first sentence;
- every paragraph and list item opening with its point;
- headings in a reply longer than four paragraphs.

It ends: "This changes the order of what you write, not its content or its length."

The display then keeps prefixes of the reply ([`fold.ts` L125–L208](https://github.com/chenxiachan/textfold/blob/2cd77e0/plugins/textfold/hooks/fold.ts#L125-L208)):

| Level | What a reply keeps |
|---|---|
| Brief | two sentences a paragraph |
| Points | one sentence a paragraph and per top-level item; code becomes "⋯ N lines of code" |
| Outline | the headings and their first sentence |
| Gist | the first sentence |

A reply under 80 words stays whole, and so does one a fold would shorten by less than a quarter. A CJK character counts as 1/1.6 of a word. The Pi port shares `fold.ts` byte for byte, and a test keeps it that way ([`test_pi_textfold_same_fold.py`](https://github.com/chenxiachan/textfold/blob/2cd77e0/tests/test_pi_textfold_same_fold.py)).

**Here:** replies render whole (`ChatMarkdown` at `frontend/src/chat/ChatDock.jsx:1978`). `FIGURE_PROMPT` is already appended to every chat request (`routers/ai.py:440`, `:1709`). A fold section would sit beside it, the same text on every request, so the prompt cache is not broken.

**To take:**

- the prompt section;
- a port of `fold.ts` as a chat helper;
- a level control in the chat header and "show all" on each reply.

Fold finished replies only; copying and saving to a note use the full text. Gamma's own markdown needs care:

- the `p.` in a `[p. 3](…)` citation must not end a sentence;
- links and `$…$` must not be cut;
- `mermaid` and `svg` fences need a placeholder.

### 7. A section map for long PDFs, also for MCP agents

**Priority:** medium; about 40 lines.

**TextFold:** `python3 -m textfold get` gives the coarsest view first. It ends with the command that unfolds one section a level further ([`fold.py` L93–L135](https://github.com/chenxiachan/textfold/blob/2cd77e0/textfold/fold.py#L93-L135)).

**Here:**

- `document_map` lists how each page starts (`ai_context.py:759-786`). It has one caller, the chat's context (`:1635`).
- An MCP agent's `read_page` gets a 6,000-character window (`ai_tools.py:97`) with no page count and no map.
- The PDF's bookmarks (`pdf_text.outline`, `pdf_text.py:445`) and the detected headings exist, but only label selected passages.

**To take:** build map lines from bookmarks or detected headings ("§3 Results — p. 7"), with page starts as the fallback. When there is no chat context, put the page count and the map before the first window.

### 8. Exercise the frozen backend in CI

**Priority:** medium; about 80 lines.

**TextFold:** before packing, CI runs [`desktop/scripts/smoke.py`](https://github.com/chenxiachan/textfold/blob/2cd77e0/desktop/scripts/smoke.py) with the payload's own Python ([`desktop.yml` L93–L99](https://github.com/chenxiachan/textfold/blob/2cd77e0/.github/workflows/desktop.yml#L93-L99)). It runs the unit tests, reads three files with no model, starts the server and checks its listing.

**Here:**

- The PyInstaller bundle needs `--collect-all` and `--collect-data` for packages that load lazily, such as pypdfium2 and ziamath (`desktop/build_backend.py:56-62`).
- CI only curls `/api/health` (`.github/workflows/desktop.yml:191`) and runs the shell's `--smoke`.
- `npm run e2e:packaged`, which uploads a PDF and exports, is manual. It last ran on 2026-09-02, on Windows ([checklist](../../desktop/docs/checklist.md)).

**To take:** a script after the health step on all three runners. It signs in, uploads a minimal PDF, searches, and exports an annotated PDF, a notes PDF and an Obsidian vault, checking each file. A missing collect flag then fails CI, not a user.

### Smaller ones

- **Thinking off for background work.** TextFold sets a default per task: levels and translation off, links and questions low ([`models.py` L22](https://github.com/chenxiachan/textfold/blob/2cd77e0/textfold/models.py#L22)). Its bench found that thinking took 3 to 9 times as long for little quality.
  - Here, translation sends the raw `translateEffort` (`App.jsx:3236`) without `effortFor`; metadata and cite send none.
  - To take: the model's lowest level for background work, with the preference as the override.
- **Translation context.** TextFold's prompt gives the paper's title, the outline and "the standard terms, the same way throughout" ([`translate.py` L64–L82](https://github.com/chenxiachan/textfold/blob/2cd77e0/textfold/translate.py#L64-L82)). It also counts formula and citation markers lost in translation (L134–L136).
  - Here, `_TRANSLATE_PROMPT` (`routers/ai.py:993`) sends paragraphs only, in chunks spread across parallel workers, so terms can drift between chunks.
  - To take: the title and a terminology rule in the prompt, and a paragraph that lost its `[12]` markers sent to the existing per-paragraph salvage.
- **Pacing per host.** TextFold keeps bioRxiv's 7-second crawl delay under one lock ([`biorxiv.py` L118–L143](https://github.com/chenxiachan/textfold/blob/2cd77e0/textfold/biorxiv.py#L118-L143)).
  - Here, `routers/metadata.py:434` and `:450` call arXiv's export API with no pacing. arXiv asks for one request every 3 seconds, and a hosted server shares one address.
  - To take: a `pace(host, seconds)` beside `net_guard`.
- **Identifiers from the PDF's own metadata.** TextFold reads the Info dictionary and XMP (`prism:doi`, `dc:identifier`) before page 1 ([`app.js` L509–L529](https://github.com/chenxiachan/textfold/blob/2cd77e0/textfold/web/app.js#L509-L529)).
  - Here, `_find_doi_candidates` (`routers/metadata.py:106`) reads the text only.
  - To take: XMP and Info DOIs as the first candidates, still behind the title check.
- **Whose words a selection is.** TextFold tells the model when the selected text is the model's wording, not the author's ([`notes.py` L170–L174](https://github.com/chenxiachan/textfold/blob/2cd77e0/textfold/notes.py#L170-L174)).
  - Here, translated text in the viewer is selectable ([translation.md](../dev/translation.md)). A selection there reaches the chat as if it came from the PDF, so a citation quoting it can never match.
  - To take: tag the selection with its language, so the prompt asks for answers and quotes from the original.
- **Figures found by their caption.** TextFold's method ([`pdftext.py` L204–L438](https://github.com/chenxiachan/textfold/blob/2cd77e0/textfold/pdftext.py#L204-L438)): a caption pattern, a column frame from the prose lines, and the page objects on the caption's side up to the nearest prose line.
  - Here, `clip_region` crops a box the model guesses after viewing the page.
  - To take: a `figure: "Figure 3"` argument on `clip_region`, with the model's box as the fallback.
- **A frame benchmark kept in the repository.** [`tools/zoombench.py`](https://github.com/chenxiachan/textfold/blob/2cd77e0/tools/zoombench.py) reads Chrome's frame trace at 2x with the GPU. Its reason: requestAnimationFrame intervals in a headless browser "looked perfect" while a real GPU dropped one frame in five.
  - Gamma's method is as good ([pdf-wheel-spin.md](pdf-wheel-spin.md)), but each probe was a one-off script.
  - To take: a committed bench on the e2e harness, so the next change can measure before and after.
- **Zotero on this computer.** Zotero Desktop's local API at `localhost:23119` ([`zotero.py`](https://github.com/chenxiachan/textfold/blob/2cd77e0/textfold/zotero.py)) needs no export step, so it avoids the unwritten-PDF race of the RDF import ([import_export.md](../dev/import_export.md)). Desktop app only: a hosted server cannot reach the user's computer.
- **Europe PMC and bioRxiv full text** as JATS for the agent's reading ([`epmc.py`](https://github.com/chenxiachan/textfold/blob/2cd77e0/textfold/epmc.py), [`biorxiv.py`](https://github.com/chenxiachan/textfold/blob/2cd77e0/textfold/biorxiv.py)). Worth it for biomedical readers.
- **Obsidian export.** TextFold puts a link back to the reader on every note and writes the conversations as `[!question]` callouts ([`reader.js` L2444–L2470](https://github.com/chenxiachan/textfold/blob/2cd77e0/textfold/web/reader.js#L2444-L2470)). Gamma's vault is richer otherwise ([obsidian.md](obsidian.md)).
- **The plugin's `userConfig`.** It shows in Claude Code's `/config` and could carry the server address, so Gamma's Claude Code plugin would install its MCP connection in one step ([mcp.md](../dev/mcp.md)).
- **OpenRouter in one click.** OAuth with PKCE mints an ordinary key; free models are listed first, and a "stealth" model is never picked by default ([`bridges.py` L77–L102](https://github.com/chenxiachan/textfold/blob/2cd77e0/textfold/bridges.py#L77-L102), [`providers.py` L164–L195](https://github.com/chenxiachan/textfold/blob/2cd77e0/textfold/providers.py#L164-L195)). It needs item 1 first, since free models allow 20 requests a minute.

## Already as good or better here

- **ChatGPT plan.** TextFold runs an npm bridge on 127.0.0.1:10531. Gamma's `chatgpt` protocol signs in natively, with no daemon ([ai.md](../dev/ai.md)).
- **Stopping a question.** TextFold discards the answer when it arrives. Gamma aborts the upstream call and frees its slot.
- **Long translations that come back short.** TextFold pads and logs a warning. Gamma checks the paragraph count and re-translates paragraph by paragraph.
- **What the model has not seen, and long conversations.** Gamma's excerpt label, coverage chip and token-budgeted trimming go further than TextFold's two measures: a 60,000-character map cut without saying so, and the last six turns.
- **Stable anchors.** TextFold numbers formulas and citations in reading order, so a parser change renumbers the stored levels. Gamma's anchors are a page plus a quote.
- **No third-party requests.** TextFold vendors its fonts so pages ask no other server; Gamma also bundles Inter and loads nothing from a font service or CDN ([ui-design.md](../dev/ui-design.md)).
- **Trash.** TextFold moves the folder aside, with no restore in the app. Gamma has Recently deleted, with restore and expiry ([home_library.md](../dev/home_library.md)).
- **Fetching.** Gamma routes a PDF link, tells walls apart, can hand a fetch to the browser, and guards against SSRF. TextFold's fetches have no SSRF guard.
- **Also as good or better:** desktop signing, onboarding tours, and separate models for translation and metadata.

## Not taken

- **Local agent CLIs as model backends** (`claude -p`, `codex exec`, `pi -p`, found on the PATH of a login shell, with Windows `.cmd` shims run through node). Three reasons:
  - [ai.md](../dev/ai.md) declines routing calls through Anthropic subscription credentials;
  - on a server, the CLI would run under the operator's login for every account;
  - Claude Code already reaches Gamma through its MCP connector.
- **The five levels themselves.** They take a model call per section, written ahead of time; they are TextFold's product. Their no-model fallback, each paragraph's first sentence, is a cheap source for item 7's map.
- **A single-file HTML export** with the PDF and PDF.js inside. The annotated PDF and the notes PDF already open anywhere.
