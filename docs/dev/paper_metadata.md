# Paper metadata and PDF resolution

How a page block learns what paper it holds, and how a link or DOI becomes a
stored PDF. Code: `gamma/routers/metadata.py`, `gamma/pdf.py`.

## Metadata fetch / edit / cite

`/api/metadata/fetch` resolves a page's paper via arXiv API → DOI content
negotiation (doi.org, with glued-suffix DOI candidates) → AI extraction from
the first pages; result + BibTeX cached on the page block (`properties.meta` /
`properties.bibtex`). `/api/metadata/update` saves hand-edited fields from the
metadata popover (rebuilds BibTeX, source `manual`, invalidates the cached
citation). `/api/metadata/cite` turns the BibTeX into a PPT-style markdown
citation via AI.

`GET /api/metadata/status` reports library-wide health (per paper: metadata
present/failed, extracted-text chars and index state from the FTS tables) — it
feeds the Settings → Library pane's status table and its adaptive batch retry
(selected, else missing; plus "Refetch all" → sequential `metadata/fetch` with
`force`).

No Google Scholar — it has no API and blocks scraping.

## PDF resolution

`/api/resolve-pdf`: arXiv abs→pdf rewrite → direct fetch → HTML pages inspected
for the `citation_pdf_url` meta tag → Unpaywall open-access fallback for DOIs
(prefers published > accepted > submitted version; disabled when the request
sends `allow_oa: false`; identifies itself with a fixed project email in
`pdf.py` — no config). Non-published substitutions return a `note` the frontend
surfaces.
