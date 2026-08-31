# Paper metadata and PDF resolution

How a page block learns what paper it holds, and how a link or DOI becomes a
stored PDF. Code: `gamma/routers/metadata.py`, `gamma/pdf.py`.

## Metadata fetch / edit / cite

`/api/metadata/fetch` resolves a page's paper and caches result + BibTeX on
the page block (`properties.meta` / `properties.bibtex`). The lookup chain,
in order:

1. **arXiv API** — id from the source URL or the PDF text (new- and old-style
   ids).
2. **DOI content negotiation** (doi.org) — every DOI in the source URL and
   scan window, each with a glued-suffix trimmed variant.
3. **Crossref bibliographic search** — queried with the page title first
   (≥3 words; users title pages with the paper name), then the normalized
   text head. Deterministic; keeps most publisher PDFs off the AI fallback.
4. **AI extraction** — last resort, and its output is verified (below).

**What the steps read.** Identifier scans and title matching use a
`SCAN_CHARS` (20k) head window, deliberately decoupled from the AI-context
pref, **plus the last page** — an issue-clipped Science PDF opens with the
*previous* article's tail (title 7k+ chars in) and prints its own DOI only in
the end-of-article trailer. Only the AI call is capped at the pref
(`context_char_limit`).

**Trust rules** (the first DOI on page 1 can belong to a *cited* paper, and
AI output can be a plausible hallucination):

- Identifiers with **URL-level trust** are accepted outright: the stored
  `source_url`, the `web_url` the extension clipped from, and
  detector-supplied `doi`/`arxiv_id` hints (`fetch_page_metadata` kwargs —
  `/api/clip` forwards what `detect.js` read off the publisher page's own
  meta tags).
- A record found via the *text* counts as **confirmed** only when the
  registry's title appears in the PDF text (`_title_in_text` — normalized
  for case, ligatures, line-break hyphens). Unconfirmed resolutions are kept
  only as a fallback when nothing confirms; a Crossref search hit is accepted
  solely on title-in-text evidence.
- AI output goes through `_verify_ai_meta`: an identifier it produced is
  resolved and, on success, replaced by the registry record; one that
  resolves nowhere and doesn't occur in the PDF is dropped as fabricated; the
  AI title is cross-checked against Crossref (≥0.92 title similarity +
  compatible year upgrades it).

**Source and the unverified flag.** `meta.source` is `arxiv` / `doi` /
`crossref` (search hit whose doi.org fetch failed) / `ai` / `manual`. `ai`
means *unverified*: the metadata button shows a red "!" badge, the popover's
Source row warns, and the Settings → Library table marks the paper red
(shared predicate `isUnverifiedPaperMeta` in `frontend/src/utils.js`). The AI
extractor also classifies the document (`meta.kind`: `paper` / `notes` /
`slides` / `thesis` / `book` / `report` / `other`, unknown → `paper`); the
warning only fires for kind `paper` — course notes and the like have no
registry record to verify against, so they get a quiet "AI-extracted (notes)"
instead. Registry-sourced records carry no kind (papers by construction).

The fetch also kicks background search indexing for the paper
(`ai_context.ensure_indexed`) — the paper is being set up, so search, the AI
document map and library-wide Ctrl+F shouldn't wait for the first search to
discover it. `/api/metadata/update` saves hand-edited fields from the
metadata popover (rebuilds BibTeX, source `manual`, invalidates the cached
citation). `/api/metadata/cite` turns the BibTeX into a PPT-style markdown
citation via AI.

`GET /api/metadata/status` reports library-wide health (per paper: metadata
present/failed + source/kind, extracted-text chars and index state from the
FTS tables) — it feeds the Settings → Library pane's status table and its
adaptive batch retry (selected, else missing + unverified-AI; plus "Refetch
all"/"Refetch shown" → sequential `metadata/fetch` with `force`).

No Google Scholar — it has no API and blocks scraping.

PDF uploads use the browser-provided original filename as their initial page
title and enter a lazy sequential metadata queue after the upload UI completes.
The page stores an `auto_title` compare-and-swap marker: a successful lookup may
replace that filename with the paper title only while the page title still
matches the marker. Any explicit rename clears it, so a slow lookup cannot
overwrite the user's edit. The AI fallback model is selected in Settings →
Providers; arXiv and DOI resolutions do not call that model.

## PDF resolution

`/api/resolve-pdf`: arXiv abs→pdf rewrite → direct fetch → HTML pages inspected
for the `citation_pdf_url` meta tag → Unpaywall open-access fallback for DOIs
(prefers published > accepted > submitted version; disabled when the request
sends `allow_oa: false`; identifies itself with a fixed project email in
`pdf.py` — no config). Non-published substitutions return a `note` the frontend
surfaces.

Resolution only picks a candidate URL — the download behind it can still fail
(paywall, blocked server-side fetch, HTML behind the link). So `openPdf` in
`App.jsx` preflights the resolved URL with `probePdfUrl` (`utils.js`): it opens
`/api/pdf` without `save=1`, keeps the headers and cancels the body, and only
then creates the page. `/api/pdf` is the single arbiter of "is this a PDF" —
its 400 `detail` becomes the failure status, and no page is left behind. A URL
whose paper is already in the library skips the preflight, so an existing page
stays openable even after its source goes away.
