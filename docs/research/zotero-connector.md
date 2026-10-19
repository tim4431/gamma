# How the Zotero Connector gets a saved paper's record, beside Gamma's

Surveyed 18 October 2026 from the source. Zotero's browser extension
(`zotero/zotero-connectors`, commit 174a437 of 15 October 2026), its
translation framework (`zotero/translate`, dd524ae), the translators
(`zotero/translators`, d3e38c1 of 16 October 2026), `zotero/utilities` and
`zotero/pdf-worker` were cloned and read; all are AGPL-3. The client's
`recognizeDocument.js`, `server_connector.js`, `attachments.js`,
`utilities_internal.js` and `lookup.js` were read on GitHub (`master`). The
recognizer service's code was published as `zotero/recognizer-server`, but
that repository no longer resolves (a clone on the survey date answered
"not found"), so what it does comes from the client's input and output and
from the Zotero developers' forum answers of 2018. Gamma's side is
[dev/extension.md](../dev/extension.md) and
[dev/paper_metadata.md](../dev/paper_metadata.md) and the code behind them;
this note does not repeat those mechanics.

The question asked: Gamma's Connector saves the PDF first and resolves the
record afterwards on the server, from the identifiers the page's meta tags
gave it. Does that step make sense beside what Zotero, the reference
manager everyone compares against, does?

## Short answer

The shape is sound and in two places stricter than Zotero's: Gamma
verifies every record against the PDF's own text and marks what it could
not verify, which Zotero does not do, and it dedups on save, which Zotero
leaves to a later "Duplicate Items" view. The order (PDF first, record in
the background, the page's identifiers trusted) is the order Zotero's
client itself uses for a bare PDF. Three things came out of the
comparison:

1. **A DOI merely found in the page's text is trusted like one from a meta
   tag.** The Connector marks it `?` ("possible paper") and the chat
   handoff discounts it, but the save sends it as `doi`, so the server
   dedups on it (a page that cites a library paper reads as "already in
   your library" and the clip files the *cited* paper), resolves the cited
   paper's PDF when the page advertises none, and confirms its record
   without the title check. Zotero's DOI translator is also a text scan,
   but it is the lowest-priority fallback, it collects every DOI on the
   page and asks the user to choose when there are several, and its icon
   says a journal article will be saved. The fix as built (see "Outcome"):
   one DOI names the page's paper, several name none, and the server keeps
   the one apart from a meta-tag DOI, confirming its record against the
   PDF.
2. **Gamma throws away the record the page carries.** Zotero's Embedded
   Metadata translator turns the `citation_*` tags into a complete item
   (authors, venue, volume, pages, date, type, publisher, institution)
   without any registry call, so a thesis on a repository or a report with
   no DOI is saved with a full record. Gamma's detector reads only the
   title and the identifiers, and for a page without a DOI or arXiv id the
   server reconstructs the record from Crossref's title search and then
   the AI, which needs a configured provider and is flagged unverified.
   For DOI and arXiv pages the registry's record is better than the tags
   (Crossref's canonical title, doi.org's BibTeX), so the choice is right
   there; it is wrong only where no registry knows the work. Carrying the
   page's record as a second hint closes that.
3. **Zotero's recognizer reads layout, Gamma reads the text head.** For a
   bare PDF Zotero's client sends the first five pages' words with their
   font sizes and positions to its service, which is what finds the title
   on a page that opens with a running header. Gamma queries Crossref with
   the first 1,500 characters of extracted text. PyMuPDF gives font sizes
   for free, so the largest-font lines of page one are a better query
   than the head, with the same title-in-text acceptance.

The rest of the note is the evidence.

## What the Zotero Connector does

### Detection is translator selection

The background script matches each tab's URL (and its de-proxied variants)
against every web translator's `target` regex
(`getWebTranslatorsForLocation`). Translators with no target are
"generic" and always qualify, as long as they can run in the browser.
The matching translators' code is fetched, the page is injected, and
`detectWeb` of each candidate runs in turn (`Translate.getTranslators(true)`:
load one, run it, collect, next). Every translator that returns an item
type is kept, sorted by `priority` (ties favour the ones that run in the
browser), and the first decides the toolbar icon. The numbers today:
site-specific translators 100 (APS, Springer, Wiley, IEEE, ScienceDirect,
Semantic Scholar, Google Books…), Atypon Journals 270, unAPI 300, COinS
310, Embedded Metadata 320, DOI 400. The same list is the fallback chain:
a save runs the chosen translator, and on failure each later one
(`TranslateWeb.translate`, `onTranslatorFallback`), and when all fail it
saves the page as a `webpage` item with a SingleFile snapshot
(`saveAsWebpage`). A translator may ask to be re-run on DOM changes
(`monitorDOMChanges`), and a frame's translators replace the tab's only
when they outrank them (`onTranslators`).

Nothing is fetched from a registry during detection. The icon is the
item type the translator guessed; no title is shown before the save.

### The record comes from the page, not a registry

Embedded Metadata is the generic translator that does the work on most
publisher and repository pages. It maps the Highwire `citation_*` tags
(title, author(s), journal title and abbreviation, volume, issue, first
and last page, date in four spellings, DOI, ISSN, ISBN, publisher,
abstract, language, keywords, PMID, conference, book title, dissertation
institution, technical report institution and number) plus Dublin Core,
PRISM, OpenGraph and RDFa into one item. The item type is read off the
tags: `citation_journal_title` → journal article,
`citation_technical_report_institution` → report,
`citation_conference_title` → conference paper, `citation_book_title` or
`citation_inbook_title` → book section, `citation_dissertation_institution`
→ thesis, `citation_isbn` → book, a bare `citation_title` → journal
article. `citation_pdf_url` becomes a "Full Text PDF" attachment; without
it the page itself is attached as a snapshot. `addLowQualityMetadata`
fills what is still missing from `document.title` (with the publication's
name stripped off the end), `meta[name=author]`, OpenGraph authors, a
byline, `keywords`, `description`, the canonical link and the page's
language. The translator's one `loadTranslator` call is the RDF import
translator for RDFa; it never queries Crossref or doi.org, and the client
never cross-checks a saved item against a registry afterwards.

### Identifiers as a last resort

The DOI translator (priority 400, so it runs only when nothing better
matched) collects DOIs from the URL (split on `#` and `?`), from every text
node outside `script` and `style`, and from every link's `href`, cleaned
of a trailing `)` or `}` and of a `/pdf…` tail. When the URL holds a DOI
and the document none, or the same one, that one is used; otherwise every
distinct DOI is listed and `detectWeb` answers `multiple`, which opens the
"select items" dialog. Each chosen DOI goes through the DOI Content
Negotiation search translator (`doi.org/<doi>` with `Accept:
application/vnd.datacite.datacite+json, application/vnd.crossref.unixref+xml,
application/vnd.citationstyles.csl+json`, then the matching import
translator); a record without a title is saved as "[No Title]". A
Crossref REST search translator exists (priority 90), but its
`detectSearch` answers false, so it runs only when another translator
names it; a DOI the client resolves itself goes through content
negotiation too. Google and ScienceDirect's advertising frames are
blacklisted.

The arXiv translator (web and search, priority 100) reads
`export.arxiv.org/api/query`, makes a `preprint` with `arXiv:<id>` in
Extra, a "Preprint PDF" attachment and a snapshot, and when the entry
names a DOI it runs content negotiation on it and copies the item type,
volume, issue, pages, date, ISSN and publication title onto the record,
keeping arXiv's title and authors and never checking that the two
describe the same work. Without a DOI the record gets arXiv's own
`10.48550/arXiv.<id>`. Gamma's `_published_record` does the same
substitution the other way round: the registrar's whole record replaces
the preprint's, only when the titles agree to 0.9, and never for a
`10.48550/` DOI.

### The PDF: fetched in the browser, then the client's resolvers

Items go to the client first (`POST /connector/saveItems`, which creates
them at once from the page's data), and the attachments follow. The
Connector downloads PDFs and EPUBs in its background page
(`_fetchAttachment`): an XHR with the attachment's referrer and the tab's
cookies read through `cookies.getAll({partitionKey: {}})`, because, as the
code's comment says, Chrome does not send partitioned cookies such as
Cloudflare's clearance cookie on extension requests (Chromium issue
458071621). The answer is checked against the declared type by its
`Content-Type` header (`application/octet-stream` is tolerated, an empty
body is not); the Connector reads no magic bytes. On failure
`BotBypass` decides: an Amazon WAF challenge header gets one retry with
`Sec-Fetch-Site: same-origin`; on a short whitelist (`sciencedirect.com`,
`pdf.sciencedirectassets.com`, `ncbi.nlm.nih.gov`) the URL is loaded in a
hidden iframe inside the tab, a monitor page captures the URL the
navigation ends on (five seconds), and failing that a popup window opens
for the user to pass the check; the captured URL is then fetched again.
The bytes are posted to `/connector/saveAttachment`. When no primary
attachment arrived, the Connector asks the client whether it has
resolvers (`hasAttachmentResolvers`) and lets it try them
(`saveAttachmentFromResolver`).

The client's resolvers (`getFileResolvers`, default order `doi`, `url`,
`oa`, `custom`): the DOI's landing page, the item's URL, open-access
copies from Zotero's own `oa/search` service (a private API over
Unpaywall; the comment asks other projects to use Unpaywall directly),
PubMed Central when a PMCID is known, and user-configured resolvers. Each
URL is tried directly (three attempts), then as a page: redirects followed
by hand up to ten, `<meta http-equiv="refresh">` followed, a known
challenge host's clearance cleared, a PDF answer saved outright, else the
page is handed to the web translators (`getFileFromDocument` runs
`Translate.Web` and takes the first file-typed attachment, which is
Embedded Metadata's `citation_pdf_url` on most sites) and the file is
fetched with the page as referrer. A domain is asked at most once a second
and dropped after five failures in a row; 429 and 503 honour `Retry-After`
up to an hour. This is the same chain Gamma's `resolve_source` walks
(arXiv rewrite, `citation_pdf_url`, meta refresh, Unpaywall) with the
difference that Gamma walks it on the server inside the clip, before the
page exists, and asks Unpaywall itself.

### A bare PDF: the client's recognizer

A PDF tab has no translators. The Connector knows the tab is a PDF from
the response headers (`webRequest` `headersReceived`, `application/pdf`),
shows the PDF icon, and a click saves the tab as a standalone attachment:
the bytes fetched as above, the title `document.title` or the URL's last
path segment, posted to `/connector/saveStandaloneAttachment`. (Firefox
cannot inject into its PDF viewer, so there the whole save runs in the
background page and the icon turns into a tick.) The client imports the
stream and, when `autoRecognizeFiles` is on, runs `RecognizeDocument` on
it at once; the Connector asks `getRecognizedItem`, which waits on that
run, so the parent item appears in its popup when it is done.

Recognition: `pdf-worker`'s `getRecognizerData` takes the PDF's Info
dictionary strings, the page count and the first five pages as lines of
words, each word with its box, font size, baseline, bold and italic flags
and a font index. A document with no text layer stops here
(`recognizePDF.noOCR`). The JSON is posted to Zotero's service
(`<services>/recognizer/recognize`). The developers described it in 2018
as operating "on the first few pages of text using extraction algorithms
and known metadata from CrossRef, paired with CrossRef and ISBN lookups
in the client", said it does not use the PDF's own metadata ("generally
of quite poor quality"), that Zotero does not log the content or the
results, and that a DataCite blind spot was fixed in 5.0.36. The answer
carries `arxiv`, `doi`, `isbn`, `title`, `authors`, `type`, `abstract`,
`language`, `year`, `pages`, `volume`, `issue`, `url`, `container`,
`publisher` and `issn`. The client then runs the search translators in
that order, arXiv, then DOI, then ISBN, taking the first result and
filling the abstract and language from the service; with none of the
three it builds the item from the service's title and authors (a
`bookSection` for `book-chapter`, else a `journalArticle`) with
`libraryCatalog: Zotero`. Nothing is checked against the text afterwards.
The file is renamed from the record, and `unrecognize` undoes everything
within 24 hours. The forum threads record the failure modes: scanned
PDFs, a first page that cites another work (an ISBN on the first pages is
assumed the book's own), several versions of one paper, "NIH-PA Author
Manuscript" read as a title.

### Add by identifier

`Utilities.extractIdentifiers` splits the text on whitespace and takes
every token `cleanDOI` accepts; only when there is no DOI does it look for
ISBNs (dashes removed, then spaces), then arXiv ids (old and new style,
version stripped), then ADS bibcodes, then PMIDs, which is any run of one
to nine digits. `Translate.Search` turns each into a search object and
runs the search translators that accept it by priority, moving to the
next when one returns nothing ("No items returned from any translator").
An arXiv id, then, is answered by the arXiv translator with its DOI
supplement; a DOI by content negotiation; an ISBN by the Library of
Congress, K10plus and WorldCat translators in turn. Attachments the translator
names (arXiv's PDF) are saved with the item; a Crossref record names none
and the PDF is a later "Find Available PDF".

## Gamma, for the comparison

Gamma's Connector detects with one content script and sends identifiers
(`detect.js` → `candidate`): a DOI used as a URL path, `citation_doi`,
`dc.identifier`, `prism.doi` or JSON-LD, an arXiv id from the URL or
`citation_arxiv_id`, `citation_pdf_url` or an alternate link, and a title
from `citation_title`, JSON-LD, `dc.title`, `og:title` or
`document.title`; with none of those, the first DOI in the visible text as
kind `maybe`. Every detection asks its own server whether the paper is in
the library and, off the badge's path, for the registry record behind the
identifier (`/api/library/preview`, arXiv then doi.org, cached), which the
popup shows as the paper's title, authors, year and venue. The save is one
server call (`/api/clip`): dedup by DOI, arXiv id and URL; resolve and
download the PDF (the browser's session first on PDF tabs and whenever
the server is refused); the page, filed; then `fetch_page_metadata` in a
daemon thread with the detector's DOI and arXiv id as hints trusted
without a title check. The app's own open of that page starts the same
lookup, and the two race; uploads go through the metadata job; the chat's
`save_paper` passes the registry's own identifiers. The lookup chain is
arXiv, DOI, ISBN, Crossref title search, AI, and a record found through
the text counts only when the registry's title appears in the PDF
(`_title_in_text`); what nothing tied to the document is stored
`unverified` and shown with a red mark.

## Where the two agree

- The page's own identifiers are the primary signal, and a text DOI the
  last resort. Zotero's Embedded Metadata outranks its DOI translator;
  Gamma's `kind` order is pdf, arxiv, doi, maybe.
- The record arrives after the thing the user wanted. Zotero creates the
  item with the page's data and downloads the PDF after; for a bare PDF it
  imports the file and recognizes it after. Gamma stores the PDF and
  creates the page inside the request and resolves the record after.
- An arXiv record that names a published DOI is completed from the
  registrar; both keep the arXiv id on the record.
- The PDF chain is the same walk (direct link, `citation_pdf_url`,
  redirects and meta refresh, open access, the browser's own session for
  walls) with the same end state: a page or item with no PDF rather than
  a broken attachment.
- Neither asks Google Scholar, and neither reads the PDF's Info
  dictionary for the record. Gamma's AI step reads the text head instead.

## Where they differ, and what each difference means

**1. A text DOI is trusted at save time.** `detect.js` sets `kind:
"maybe"` on the first DOI the regex finds in the first 30,000 characters
of visible text. The worker looks up every kind but `none`, so
`/api/library/lookup` matches that DOI against `properties.meta.doi` and
a blog post citing a library paper gets the green tick, with the popup
offering "Open in Gamma" instead of Save (the notice does say "Already in
your library as …", so the mismatch is at least visible). `savePaper`
sends `doi: cand.doi` whatever the kind; `save_clip` then dedups on it
and files the cited paper into the chosen folder, or, when the page
advertises no PDF and has no arXiv id, resolves the cited paper's PDF
(`candidate = pdf_url or arxiv_id or doi or source_url`) and saves it as
the page, and `_start_metadata(doi=…)` puts the DOI among the hints, so
`fetch_page_metadata` confirms the cited paper's record without the
title check that a text DOI gets everywhere else. Only `browserPdfUrls`
and the handoff's `sameWork` discount a `maybe` DOI. Zotero's text scan
is the lowest-priority translator, lists every DOI it found and asks when
there are several, and its icon names what will be saved; a page with one
DOI in its text is saved as that paper, on purpose, with no tick and no
dedup. The decision for Gamma: keep saving the cited paper when the user
asks (the chip says "possible paper" and the preview shows which), but
send the DOI as a guess, not as `doi`: the lookup should not answer with
a tick for it, `find_page` should not match on it, and the metadata
lookup should treat it as a text candidate that needs the title in the
PDF. The preview's authors, year and venue under the *page's* title are
the one place the current popup can mislead; a "cites" wording for the
`maybe` kind would fix it.

**2. The page's record is dropped on the floor.** For a DOI or arXiv page
the registry is the better source, and Gamma's BibTeX comes from doi.org
rather than from tags a publisher filled by hand, so the choice to send
identifiers only is right for the common case. It is wrong for the pages
Zotero handles with Embedded Metadata alone: a thesis on DSpace, EPrints
or HAL, a technical report, a conference paper on a site without DOIs,
OpenReview, a working paper, all of which carry `citation_author`,
`citation_publication_date`, `citation_dissertation_institution`,
`citation_technical_report_institution`, `citation_conference_title`,
`citation_publisher` and the rest. Gamma's server then has no identifier,
runs the Crossref title search (which knows none of them), and falls to
the AI, which needs a provider, costs a call, and is marked unverified
for the checkable types; a thesis's university ends up wherever the AI
put it. The decision: `detect.js` should also collect the Highwire record
(authors, date, venue, volume, issue, pages, publisher, institution,
ISBN, and the item type by Embedded Metadata's own rules) and the clip
should send it as a second hint, which `fetch_page_metadata` uses when
the registries find nothing or do not confirm: stored with a `page`
source, confirmed by the same title-in-text rule, typed from the tags.
It also makes a better Crossref query than the text head (title plus
first author) and settles the item type without the AI classifier.

**3. Verification.** Gamma's title-in-text rule, the `unverified` flag and
the AI cross-check (`_verify_ai_meta`) have no counterpart in Zotero,
whose client accepts the first search result for whatever identifier the
service returned and offers a 24-hour undo instead. Keep Gamma's rule; it
is the reason a wrong DOI on page one does not become the record. The one
place Zotero verifies at all is the EPUB path, which discards an ISBN
lookup whose ISBN differs from the one searched.

**4. The title guess for a bare PDF.** Zotero's recognizer works from
words with font sizes and positions, which is how a service can find the
title under a running header or a "Downloaded from" banner; it is also
why Zotero needs a service and sends the first five pages of every PDF to
it. Gamma's deterministic step queries Crossref with the page title (when
it has three words) and then with the first 1,500 characters of text,
which on an issue-clipped or header-heavy PDF is not the title. PyMuPDF
already returns spans with sizes; querying Crossref with the
largest-font line or two of page one, then the head, keeps the step
local and deterministic and should take more PDFs off the AI fallback.
Measure before and after on the library's own files, as
[registry-records.md](registry-records.md) did.

**5. The preview.** Zotero fetches nothing before a save and shows an
icon; Gamma fetches the registry record for every detected tab and shows
the title, authors, year and venue, which is what names a PDF tab's page
before the lookup has read the text. The cost is one doi.org or arXiv
round trip per tab, off the badge's path and cached; it is worth it. It
could be skipped for the `maybe` kind (see 1).

**6. Cookies the browser does not send.** Zotero's background fetch adds
the tab's partitioned cookies by hand because Chrome drops them on
extension requests; that is the Cloudflare clearance cookie, and it is a
plausible reason the worker's `fetch(url, {credentials: "include"})` is
refused by the publishers Gamma's docs list (science.org and the other
Atypon sites) while the same fetch from the page's own context succeeds.
Gamma already covers the case with the content-script fetch, so this is
a lead rather than a gap: reading `cookies.getAll({url, partitionKey:
{}})` into a `Cookie` header on the worker's fetch (the optional
`cookies` permission the publisher-session drawer already requests) may
make the first attempt succeed on those sites. Untested.

**7. What leaves the machine.** Zotero sends the words and layout of the
first five pages of every bare PDF to its service, and item records to
zotero.org when the client is not running. Gamma's Connector sends only
identifiers, a title and the bytes to the user's own server; the server
sends up to 500 normalized characters of the text head to Crossref as a
search query, and the configured head to the user's AI provider only as
a last resort. Both send nothing to Google Scholar. The one privacy
detail Gamma could state in its docs is the Crossref query text, which
the recognizer threads show users ask about.

**8. Dedup and ISBNs.** Zotero does not dedup on save; Gamma does, by
DOI, arXiv id and URL, which is better except for the text DOI above.
Zotero assumes an ISBN on the first pages is the book's own; Gamma takes
only an ISBN labelled as such, checksum-valid, and confirmed by the title
and an author surname in the text. Keep Gamma's rule.

## Outcome

Built on 19 October 2026; the mechanics are in
[dev/extension.md](../dev/extension.md#detection) and
[dev/paper_metadata.md](../dev/paper_metadata.md).

1. **The text DOI, revised.** The first plan (send the DOI as a guess that
   neither the library lookup nor `find_page` matches) would have made a
   duplicate whenever the user did want the cited paper and it was
   already saved under another URL. What was built follows Zotero's line
   instead: the page's text DOIs and its `doi.org` links are collected
   distinct, one of them is the paper the page is about, several name
   none (the page saves as a web page). The one is sent with
   `doi_from_text`; it still finds the paper in the library and its PDF,
   but it is kept on the page as `web_meta.text_doi`, never as the page's
   own DOI, and its record counts only when its title is in the PDF (on a
   page without a PDF it is stored flagged). The popup heads a "possible
   paper" with that paper's title, and *Save the web page instead* saves
   the story rather than the paper.
2. **The page's record.** `pageRecord.js` reads the Highwire tags (Dublin
   Core and PRISM filling gaps) with Embedded Metadata's type rules; the
   clip keeps it with the tags' identifiers on the page as `web_meta`. The
   lookup asks Crossref with its title and first author first, and when no
   registry confirms anything the record itself is stored (`source:
   "page"`, not flagged: the same tags' word as their `citation_doi`,
   which was already trusted outright). That settles the open question
   above.
3. **The largest type.** `pdf_text.title_guess`, queried after the page's
   record and before the page title and the text head. The first version
   carried the stored title in 23 of the library's 66 registry-confirmed
   PDFs, against 59 for the head: arXiv's id stamp up the margin is
   rotated text in bigger type, many PDFs set each word as a span with no
   space between, and an 8% size tolerance let a journal header in. With
   horizontal lines only, spans joined across gaps and a 3% tolerance it
   carried the title in 53 and came close in 57. Several of the rest are
   records that do not match their file (a supplement filed under its
   paper's record), where the guess reads the true title.

Found and fixed on the way:

- The app's lookup on open raced the clip's own and ran without the
  detector's identifiers, so a slower AI record could overwrite the
  registry record the clip's lookup had stored. Both now read `web_meta`
  off the page.
- Dedup matched identifiers only in `properties.meta`, so a second clip
  of the same paper from another page, before the first lookup landed or
  after it failed, made a duplicate. `find_page` also reads `web_meta`'s
  identifiers (never a text DOI).
- *Save this URL anyway* built its own candidate and dropped the page's
  record; it sends it now.
- A `doi.org` link that encodes the DOI's slash (`%2F`) was not read.

Declined: the partitioned-cookie lead (6). Chrome ignores a `Cookie`
header set on `fetch`, so the change needs the `declarativeNetRequest`
permission and a session rule that rewrites the request's cookies, at the
risk of replacing the ordinary ones; the in-page fetch the Connector makes
right after the worker's already succeeds in exactly these cases, and
nothing here can test against a real challenge.

Still open: the recognizer service's own matching, whose code could not
be read; a picker for a page naming several DOIs, as Zotero offers; a
journal header set larger than the title (Elsevier's) and titles under
three words, which the largest-type guess misses; a check of Zotero's
Firefox build, which this note did not run.
