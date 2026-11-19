# Clickable PDF citations

## Manual reference links

A highlight block can make a PDF region clickable through
`properties.link_url` or `link_page_id`; `link_block_id` additionally
targets a particular highlight on the destination page, by its block id (a
highlight's id is its block's, [api.md](api.md) "The highlight shape"):
"Copy as reference point" on a highlight keeps that id for the link dialog
(another highlight's, on this paper or another) and copies the highlight's
block link (`?block=<id>`). Pasted
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
does. A Gamma link in the field (`parseGammaLink`, whatever host it names:
a copied highlight's `?block=`, a page's `?page=`) that App's
`gammaLinkPlace` finds in this workspace (`GET /blocks/{id}` for a block,
the library listing for a page) leads instead as the highlight or page it
names, and picking it links there (`{pageId, blockId}`), not to the
address; a highlight on this same paper is offered too (a cross-reference
to a figure), the one being linked and this page itself are not. One
another server or workspace holds stays a "Link to …" address. With
nothing typed it lists Remove link for a passage that links already, the
copied reference point (any highlight but the one being linked), then the
pages the passage names (`scorePaperMatch` 6 or more: its DOI or arXiv id,
the authors, title words) as Best matches before the recent pages.
"Change link…" opens with a URL link's address in the field, so a Gamma
link stored as an address is turned into a highlight link by picking the
row it resolves to. App derives the viewer's `linkTarget`
(`{url, pageId, blockId}`) from those properties, and `openLinkTarget`
opens one, the PDF region's press and its note's link alike: a block
through `openBlockLink` (on this page it scrolls there, Back returning;
on another the page opens and lands on it), a page through `openBlock`, an
address through `handleDocLink`. `linkProps` writes them and drops (null) the keys the
target does not set, all three when the link is removed; an empty string,
which older clients stored, reads as unset everywhere. A new link region
is made in `LINK_COLOR` (`shared/model/palette.js`, a light blue wash).
The block table's `kind` calls such a block a `link`; the viewer draws it
as a highlight. `handleDocLink` opens a Gamma link whose block or page
`gammaLinkPlace` finds here in place (a citation at its passage), resolves
a DOI or arXiv URL against the current library, opening the existing page
when found, and otherwise offers fetching the paper or opening the browser.

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
Hovering or focusing it previews the paper title, the PDF page and the
quote, and a right-click or a held finger pins that card: the link card
every link has ([mentions.md](mentions.md#the-link-card)), whose Edit, Copy
link and Remove link a note that may be edited adds. App's `citeSource` (on
`GammaNavContext`) names the paper. The pointer can move from the pill into
the preview, across the text between them, without the preview closing
([menuAim.js](../../frontend/src/shared/ui/menuAim.js),
[ui-design.md](ui-design.md)). Its **Open in PDF** link opens the passage the
way the pill does. A
page or block link is a card (`GammaLinkCard`). Clicking a citation calls
`openPage(id, citation)` from `GammaNavContext`, which App provides once: the library page
opens in place, waits for that PDF, scrolls to the cited page and asks the
server where the quote stands ([Finding the quote](#finding-the-quote)). A
pasted link on a cold load works the same way (the same classifier on the
initial URL). Only the page the passage is found on is force-rendered.

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

### Finding the quote

The server finds the quote, with the finder a replaced PDF's highlights are
placed by and an agent's `highlight` tool marks by
([highlight_relocation.md](highlight_relocation.md#finding-a-passage)):
`GET /api/pages/{id}/citation?pdf_page=N&quote=…` (`routers/pages.py`,
`highlight_relocate.cite` over `find_quote`). The model quoted MuPDF's text
of the page (`read_page`, the context), and the finder reads the file with
MuPDF too, so the two sides read alike. `pdf/pdfCitation.js`
(`locateCitation`) asks, and the viewer's citation effect (`PdfViewer`'s
`activeCitation`) waits for the answer and for the scroll to the cited page.

- **Where.** The cited page is read first, and the pages either side only
  when it holds no exact copy, so a model that names the printed page one off
  the physical one is still found. Reading a page is most of a lookup's time
  (about 150 ms; the search a few).
- **Exact** is exact as the finder folds text: case, dashes, quotes,
  ligatures, a word hyphenated at a line's end and the spaces. A hyphen inside
  a word may be missing from the page ("Carr-Purcell-Meiboom" where a line's
  end joined "Purcell-" and "Meiboom"). A reference mark the quote leaves
  out does not count either: "sequence." for "sequence¹⁵⁹˒¹⁶⁰." The mark
  must be several digits, a list or a range, set smaller and raised after a
  word of three letters and before its punctuation. A mark at the quote's
  end is left out of the highlight, one inside it is covered.
- **Rough.** With no exact copy, the place reading most alike from 80%
  (`ROUGH_APPLY`, difflib's ratio of the folded texts) is marked, and the
  notice says how alike it reads: "Highlighted an approximate text match
  (97% alike)." Nothing is frozen. A misquoted number or an editorial
  pointer the quote leaves out (`(Methods)`) reads nearly alike, and the
  passage the model meant is shown with that notice. A quote under 12
  characters is found exactly or not at all.
- **Ambiguous.** When another place of that page reads exactly as alike
  (`Match.twin`: two copies, exact or rough), nothing is marked and the
  notice says "More than one passage on this page matches this quote." A
  citation has no context to tell them apart.
- **Missing.** Nothing alike enough on the three pages, a PDF the server
  does not hold, or a failed request: the page stays open with "Opened the
  cited page; the quote could not be found on it or the pages beside it." A
  scan without a text layer opens its page this way.

The answer is `{status: "matched", page, match, rects}` (`match` 1 for an
exact copy, else two decimals up to 0.99; `rects` fractions of the page as
shown, one per line), `{status: "ambiguous", page}` or `{status: "missing"}`
([api.md](api.md) "Pages"). A member's request fetches a PDF shown from a URL
that the server has not stored yet, as the chat does when it reads one. A
share's request reads only what is stored, so a share link never starts a
download.

`PdfCitationOverlay` draws the rectangles as percentages of the page, so a
zoom carries them without a lookup. They are MuPDF's glyph boxes, one per
line, as an agent's highlight is drawn. The marks (`.pdfCitationMark`, the
translation shimmer) ignore pointer input and never touch the annotation or
note store. A click outside the passage or Escape removes them; there is no
close button.

Coverage: `frontend/tests/gammaLinks.test.mjs` (the classifier, the line
kind), `frontend/tests/coverage.test.mjs` (the context pill in pages and in
lines), the citation scenarios in `frontend/tests/e2e/scenarios/pdf.mjs`, and
`backend/tests/test_pdf_citations.py` (the finder's cases and the route).
`node tests/e2e/pdfCitationPaper.mjs /path/to/1904.06560.pdf` runs the saved
citations of `tests/fixtures/pdfCitationPaper.json` (physical pages 20–22 of
Krantz et al., *A quantum engineer's guide to superconducting qubits*: wrapped
compounds and left-out superscript references, each exact) against that
complete PDF in an isolated browser test. `pdfCitationTransduction.json`
holds the page 4 citation from the journal version of *Quantum-enabled
millimetre wave to optical transduction using neutral atoms*, whose quote
left out `(Methods)` before the period: found at 99%, so `approximate`. To
check another complete PDF, pass
its fixture file after the screenshot directory:
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
