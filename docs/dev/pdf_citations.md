# Clickable PDF citations

## Manual reference links

A highlight block can make a PDF region clickable through
`properties.link_url` or `link_page_id`; `link_block_id` additionally
targets a particular highlight on the destination page, by its block id (a
highlight's id is its block's, [api.md](api.md) "The highlight shape"):
"Copy as reference point" on a highlight keeps that id for another paper's
link dialog and copies the highlight's block link (`?block=<id>`). Pasted
into a note or the chat, that link lands on the passage as the deep link
does: App's `openBlockLink`, which every block link, `[[ref]]`, backlink and
inbox item opens through, shows the block's row in the notes and, for a
highlight or a note linked to one, scrolls the PDF to the passage and
flashes it (`landOnBlock` on the open page, `openBlock`'s `block` on
another, both through `pendingJumpRef`). The link dialog
(`pdf/LinkDialog.jsx`, from a selection's link button or a highlight's
"Link to page…" / "Change link…") is a page picker
(`library/PagePicker.jsx`): its field takes a DOI, an arXiv id or a web
address (`isLinkInput`; `normalizeLinkInput` makes it a URL), and "Link to …"
leads the list while it reads as one, or else searches the pages as Ctrl+P
does. With nothing typed it lists Remove link for a passage that links
already, the copied reference point, then the pages the passage names
(`scorePaperMatch` 6 or more: its DOI or arXiv id, the authors, title
words) as Best matches before the recent pages. "Change link…" opens with
a URL link's address in the field. App derives the viewer's `linkTarget`
(`{url, pageId, blockId}`) from those properties, and opening the link jumps to the block
once the page is up. `linkProps` writes them and drops (null) the keys the
target does not set, all three when the link is removed; an empty string,
which older clients stored, reads as unset everywhere. A new link region
is made in `LINK_COLOR` (`shared/model/palette.js`, a light blue wash).
The block table's `kind` calls such a block a `link`; the viewer draws it
as a highlight. `handleDocLink` resolves a DOI
or arXiv URL against the current library first, opening the existing page
when found; otherwise it offers fetching the paper or opening the browser.

## AI citation links

AI responses can link a passage using ordinary Markdown:

`[p. 3](/?page=GAMMA_PAGE_ID&pdf_page=3&quote=percent-encoded%20verbatim%20passage)`

The PDF page is physical and 1-based. Context and `read_page` label pages
`[PDF page N]` (blank pages counted, a mid-page continuation labelled
`; continued`) and each context section carries its Gamma page ID
([ai_context.md](ai_context.md)); `routers/ai.py` appends the citation
instruction (`ai_tools.citation_prompt`) whenever a document is in context,
with tools off and under a custom prompt too. The MCP server's instructions
carry the same text in its absolute form, and its results link every located
PDF page and block ([mcp.md](mcp.md) "Links in results"). A quote should fit
on one page and identify one passage. Copy it verbatim, including
parenthetical references. A shorter distinctive substring is also valid; do
not add a period where the source continues.

The link is plain Markdown, so it persists with the reply.
`shared/model/gammaLinks.js` classifies one link into this library
(`parseGammaLink` → `block` / `page` / `citation` / `line`, the last for a
text file's lines, [below](#line-links)). A citation renders as a
compact pill (`CitationPill`, `shared/ui/Widgets.jsx`) in chat answers, notes
and a note's preview on the PDF. It shows "p. 2" on the text's baseline,
sized from the surrounding text. When the reply or note cites several
sources (`citesSeveralSources`: papers and files alike), a pill whose label
names only the place gets its source in front ("Vaswani · p. 2").
Hovering, focusing or long-pressing it previews the paper title, the PDF page
and the quote; App's `citeSource` (on `GammaNavContext`) names the paper.
The pointer can move from the pill into the preview, across the text between
them, without the preview closing ([menuAim.js](../../frontend/src/shared/ui/menuAim.js),
[ui-design.md](ui-design.md)). Its **Open in PDF** link opens the passage the
way the pill does. A
page or block link is a card (`GammaLinkCard`). Clicking a citation calls
`openPage(id, citation)` from `GammaNavContext`, which App provides once: the library page
opens in place, waits for that PDF and the cited page's text layer, then
matches the quote in the browser. A pasted link on a cold load works the same
way (the same classifier on the initial URL). Only the cited page is
force-rendered. No document scan and no server-side coordinates.

The quote is optional: `?page=…&pdf_page=N` alone opens the paper at that
page with nothing highlighted.

**The origin is not part of the test.** A server moves — a desktop sidecar on
`127.0.0.1`, the NAS URL, a workspace mirrored onto another server — and
links written before the move still name this library's pages. A link whose
host differs from the tab's (`foreign`) is claimed only when its id resolves
locally, which the note renderer checks through the same `[[ref]]` batch
lookup it uses for the card's title. An id that doesn't resolve stays an
ordinary external link, which is what a link to somebody else's Gamma should
be. Pasting normalises the other way: the "Paste as" chooser offers
**Citation** / **Line link** / **Page link** for a recognised link and stores
it host- and workspace-free (`relativeGammaLink`), so new notes never learn a
host.

`pdf/pdfCitation.js` keeps source offsets through Unicode ligature folding,
dehyphenation and whitespace normalization. Fallback passes tolerate differing
spaces and compound-word hyphens that cross line wraps. PDF.js text items are
mapped directly to their rendered spans, preserving empty EOL items and font
geometry. Small, raised multi-digit reference numbers after prose and before
punctuation can be omitted when matching an otherwise exact quote. Baseline
numbers, mathematical signs and single-digit exponents are not discarded.
A typography fallback permits omitted standalone section pointers: `(Methods)`,
`(Online Methods)`, and `(Supplementary Information/Methods/Material)` (one
of those three names). Wrapped pointers such as `(Meth-\nods)` work too.
Other parentheticals must still match in this pass. Omitted pointers are
excluded from the highlight, even when they share a text run with the quote;
exact matches take priority.

If those passes fail, `pdf/fuzzyCitation.js` aligns the quote's words against
the cited page with bounded edit distance. It tolerates punctuation changes,
minor spelling/OCR errors, and a few inserted, omitted or substituted words,
including arbitrary parenthetical prose. This requires at least eight words
and 40 non-space characters. The budget is one word edit per eight words,
capped at eight edits; one or two character typos in longer words cost a
quarter or half of a word edit. Numbers, mathematical symbols, Greek letters,
single-letter variables and negations cannot be inserted, deleted or changed
by this pass (the existing case folding still applies). A second qualifying
passage is ambiguous. Exact matches always win over approximate ones.

Fuzzy matches highlight the source passage, including internal words
omitted by the quote, and show “Highlighted an approximate text match.” The
matcher preserves source offsets and stops after at most one million word
comparison cells; unusually dense pages fall back to the unresolved notice
when exact matching fails. Missing or ambiguous passages show a message on the
cited page without a highlight. Scans without a text layer
therefore open the page but cannot highlight text.

`PdfCitationOverlay` draws DOM `Range` rectangles from the real pdf.js text
nodes (partial runs included) instead of estimating glyph widths or trusting
offsets into the server's extracted text (MuPDF's text is not pdf.js's).
The rectangles are percentages of the page, and a finished
text-layer render recomputes them after a zoom; stale document/render work is
cancelled. The marks (`.pdfCitationMark`, the translation shimmer) ignore
pointer input and never touch the annotation or note store. A click outside
the passage or Escape removes them; there is no close button.

Coverage: `frontend/tests/gammaLinks.test.mjs` (the classifier, the line
kind), `frontend/tests/coverage.test.mjs` (the context pill in pages and in
lines), `frontend/tests/pdfCitation.test.mjs`, the citation scenario in
`frontend/tests/e2e/scenarios/pdf.mjs`, and `backend/tests/test_pdf_citations.py`.
The frontend fixture includes the failing PDF.js runs from physical
pages 20–22 of Krantz et al., *A quantum engineer's guide to superconducting
qubits*. `node tests/e2e/pdfCitationPaper.mjs /path/to/1904.06560.pdf` runs the
saved citations against that complete PDF in an isolated browser test.
`pdfCitationTransduction.json` captures the page 4 regression from
*Quantum-enabled millimetre wave to optical transduction using neutral atoms*:
the quote omitted `(Methods)` before the period. To check another complete PDF,
pass its fixture file after the screenshot directory:
`node tests/e2e/pdfCitationPaper.mjs /path/to/transduction.pdf /tmp/citation-shots tests/fixtures/pdfCitationTransduction.json`.

## Line links

A text file's page (a `.tex`, a script, a log; [documents.md](documents.md))
is cited by its lines, with one key for one line and for a range:

`[method.tex:2](/?page=GAMMA_PAGE_ID&line=2&quote=We%20fit%20a%20small%20network…)`,
`[method.tex:2–4](/?page=GAMMA_PAGE_ID&line=2-4&quote=…)`

`line` is `N` or `N-M`, 1-based, each of one to eight ASCII digits,
`1 ≤ N ≤ M ≤ 10,000,000`; a link that names `line` and `pdf_page` both is
malformed, and so are `M < N` and anything else the key holds.
`parseGammaLink` gives `{kind: "line", pageId, line, to, quote}`, `to` null
for one line; the MCP server's `resolve_link` reads the same key with the
same rule (`mcp_links.line_span`, [mcp.md](mcp.md)), and
`tests/shared/linelinks.json` holds the two to it. The chat's context and
`read_page` print each line's number before it, and a search hit names its
`l.N`, which is where N comes from. **The quote** is the first line's
verbatim text, percent-encoded: the rule is stated once, in
`ai_tools.citation_prompt` ([ai_tools.md](ai_tools.md)), which tells the
model to always add it, because the file may change under its links and
the viewer finds the line again by it. A link a person pastes may leave it
out, or leave it empty for a blank line (`&quote=`, read as none on both
sides); the viewer then marks the number alone. Unlike a PDF passage's, the
quote has no least length (a code line is often `}` or `\fi`), and 4,000
characters at most (a long code line's whole text; a PDF passage's stays
2,000).

A line link is the same pill, `gammaLink-line` beside `gammaLink-citation`.
With no label of the author's it reads "line 7" or "lines 3–5"; the model
writes the file's name in the link text itself (`method.tex:7`), as the
citation rule asks. The preview names the file, "Line 7" or "Lines 3–5",
the quote and **Open in the file**. A click calls `openPage(id, {kind:
"line", pageId, line, to, quote})` (a PDF citation passes `{kind:
"citation", pageId, page, quote}`): the page opens in place and the code
view scrolls to the line and marks it ([documents.md](documents.md)
"Viewers"). `citeSource` names a file only when the client knows its page
(the listing, a tab, a recent page, a note's linked ids), so a cited file
the reader never opened has no source name in front of its pill and
"This file" in the preview. The citation tour is the PDF's: `chat.cited`
fires for a `citation` link alone, and a line pill carries no
`chat.citation` anchor. Pasted, **Line link** stores
`[<file title>:N](/?page=…&line=N…)`, the title from the page's row.

**Coverage in lines.** A reply's context pill (`chat/coverage.js`
`coverageNote`) counts a text file's entry (`format: "text"`, with
`lines` and `lines_shown`) in lines: "Model saw lines 1–400 of 2,310 · read
lines 401–800 with tools", the tools' part from the `read_page` chips'
`lines: [first, last]` (`readSpans(actions, pageId, "lines")`), and its long
form names the lines nobody saw. A PDF's entry keeps its pages.
