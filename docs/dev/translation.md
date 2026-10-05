# PDF translation

The viewer's translated view: the button and its states, the request, the
engines and their limits. Provider configuration and the chat are in
[ai.md](ai.md); the engine preferences are in [settings.md](settings.md).

`POST /api/ai/translate` backs the viewer's translated view. ONE 文A button
in the PDF zoom column does everything by state:

- On the page being read, a click translates it unless it is already fully
  translated under the current language and model. On such a page a click
  toggles show/hide for ALL pages (the viewer reports `current` next to
  `pages` in its state).
- A page a halted job left half-done counts as untranslated, so a click
  finishes it from the cache. Switching language or model in Settings makes
  the button translate afresh.
- Hidden = slashed icon; holding Alt peeks. A click during a job halts it.
- Right-click (long-press on touch) opens the option menu: Translate this
  page / Translate whole document / Show original·translation (Stop
  translating while running).

A whole-document job queues pages nearest the current page first (forward before backward at
equal distance), so the page being read paints immediately. The queue lives
in `pdf/PdfViewer.jsx` (`translateCtl`), producer/consumer style: the producer
segments queued pages in order and feeds one flat list of ~6-paragraph /
1200-char chunks, while N workers (Settings → Translation → parallel
requests, 1–`TRANSLATE_PARALLEL_MAX` = 4 in `app/prefDefs.js`, below the six
AI calls an account may have open at once; a larger stored value reads as 4)
stream through it across page boundaries — the first request is
in flight while later pages are still segmenting, chunks paint as they land,
char-weighted progress shows under the button and as a background-tasks row.
Halting aborts the in-flight requests (each job carries an AbortController)
and keeps finished chunks; re-running skips done pages and re-fills partial
ones from the server cache. Reliability: each chunk gets one client-side
retry, and the server salvages a miscounted model reply ("expected 5, got
4") by re-translating that batch paragraph by paragraph, concurrently — a
paragraph that still fails comes back verbatim (shown as original, uncached)
instead of failing the request.

The viewer requests `stream: true`. The reply is then NDJSON: `{"i":
[request indices], "text": partial}` lines as the model writes each
paragraph, then the same final `{translations, model, cached}` object a
plain call returns. `ai_client.partial_json_strings` reads the complete
elements of the half-written JSON array plus the one in progress. Lines are
throttled to ~20/s (`_TRANSLATE_STREAM_INTERVAL`), and an element maps to
every request index that shares its source text. `stream: false` (the
default) still answers in one JSON body. The salvage path never streams. An
upstream failure mid-stream is an in-band `{"error"}` line.

On the page, a paragraph whose translation is queued gets a faint accent
wash over its original lines, an in-flight one shimmers, streamed text
types onto the page behind a caret (masking the original as soon as there
is something to show), and a landed paragraph fades in. That is
`TransPending`/`TransPara` in `pdf/PdfViewer.jsx`, driven by the entry's
`queued`/`busy`/`partial` fields, which the job clears when it ends, halts
or fails.

Geometry never leaves the client: `frontend/src/pdf/pdfTranslate.js` segments
pdf.js text runs into paragraph blocks (columns via whitespace-river
detection, paragraphs via indents/font changes, figure-wrap via sustained
width changes; math-heavy/numeric blocks are skipped), each carrying
PER-LINE rects. The overlay masks exactly those original lines (plus the
leading between them) and lays the translation over them with an inline
cloned background — so figures a paragraph brushes against are never painted
over, and the layout never moves. Translated text is selectable/copyable;
while shown, the invisible original text layer stands down.

Targets are the allowlisted `TRANSLATE_LANGS` codes
(`gamma/translate_engines.py`, shared by both translation paths; mirrored in
`frontend/src/app/prefDefs.js`). What translates is Settings → Translation ›
"Translate with" (`translateModel`, a browser pref; "" is the default).
`translateModelFor` (`app/prefDefs.js`) turns the pick into what is sent:
the pick while it is still offered, the free Microsoft service when there
is no chat model at all, else the chat model. Reasoning `effort` and
parallel requests sit in the same Translation section. Effort is omitted
unless picked; Low/Minimal is the speed lever for reasoning models. The
"Translation button" switch hides the viewer's button; the selection
translator has its own switch.

The server keeps an **in-memory only** LRU (~5k entries, lock-guarded
because requests run in the threadpool) per (user, language, bare model
name, source text). Nothing goes to disk; the cache makes
halts/retries/re-shows free until a restart. Duplicate paragraphs within a
request go upstream once. Caps: 200 texts / 60k chars per request.

**Selection translation.** The text-selection popup (`PlainTip` in
`pdf/PdfViewer.jsx`, the highlight colors + link) carries a 文A button
when Settings → Translation › "Translate a selection" is on (`selTranslate`,
account pref, default on).

- It sends the selection as ONE text through the page translator's request
  (`translateChunk`: same model or service, language, server cache,
  streamed partials). Lines are rejoined by `selectionParagraphs`
  (`pdf/pdfTranslate.js`, the page blocks' hyphen/CJK rules), capped at
  5000 characters.
- The result shows under the colors as a fold-out panel: a header with the
  language, spinner and copy button, then selectable text. The button
  refolds it.
- "Translate on select" (`selTranslateAuto`, default off) starts it as soon
  as the popup opens.
- The popup is keyed by the selection, so a new selection starts over and
  aborts the previous request. A click or selection inside the popup keeps
  it open (the viewer's selection sync ignores a selection anchored in
  `.plainTip`).
- `TipFrame` flips the popup above the selection when it would run past the
  window's bottom. Like the popup itself, it needs edit rights on the page.

**Machine-translation services.** "Translate with" also offers the services
in `gamma/translate_engines.py`. The viewer then sends `model:
"engine:<id>"`, and `/api/ai/translate` hands the misses to
`translate_engines.translate` instead of a chat model.

- **Microsoft (free)** needs no setup. It is the endpoint Edge's own page
  translation calls: `POST
  edge.microsoft.com/translate/translatetext?to=<code>&isEnterpriseClient=false`
  with a JSON array of strings and no key or token. The reply has
  Translator v3's shape (`[{detectedLanguage, translations: [{text, to}]}]`);
  the source language is detected per text.
- The endpoint is unofficial and undocumented, so it can change or throttle
  without notice; Google and Youdao are the fallback. The older
  `/translate/auth` token flow answers 404 since July 2026.
- Microsoft refuses requests past about 50k characters (measured), so
  batches stay at 100 texts / 20k characters.
- Its consecutive failures are counted in memory. From `FREE_ALERT_AFTER`
  (3) on, the server log gets one warning per streak and each account that
  met them gets the `free-translate` notice ([settings.md](settings.md)
  "Notices"); the Settings row shows the error. One success ends the streak.
- **Google Cloud Translation**: v2 basic, the API key sent as
  `X-Goog-Api-Key`, `format: "text"`.
- **Youdao**: the `openapi.youdao.com/v2/api` batch, with a v3 SHA-256
  signature over the concatenated queries. A query listed in `errorIndex`
  comes back verbatim and uncached.
- Each service maps the target codes to its own (Microsoft `zh-Hans`,
  Youdao `zh-CHS`) and splits a request by its batch limits.
- The service path needs no AI provider. It has no effort, no streamed
  partials (the stream is just the final line), no token usage and nothing
  to salvage, since the APIs answer aligned lists.
- Cache, validation and dedup are the LLM path's, keyed on `engine:<id>`.

Credentials are per account under the reserved `translate-engines` pref.
Like `ai-settings`, `/api/prefs` refuses it and the only read path is the
masked `GET /api/translate/engines`; guests can't store any.
`PUT` / `DELETE /api/translate/engines/{id}` set or drop a service's
credentials, and `POST /api/translate/engines/{id}/test` translates one
sentence for the row's Test button (in-body result). Unlike the LLM
prompt, nothing tells these services to leave math, `[12]` citation markers
or URLs alone. PDF text carries no LaTeX and math-heavy paragraphs are
skipped client-side, so the risk is small.
