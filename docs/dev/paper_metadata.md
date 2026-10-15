# Paper metadata and PDF resolution

How a page block learns what paper it holds, and how a link or DOI becomes a
stored PDF. Code: `gamma/routers/metadata.py`, `gamma/routers/pdf.py`.

## Metadata fetch / edit / cite

`/api/metadata/fetch` resolves a page's paper and caches result + BibTeX +
the slide citation on the page block (`properties.meta` / `properties.bibtex`
/ `properties.ppt_cite`). The lookup chain, in order:

1. **arXiv API** — id from the source URL or the PDF text (new- and old-style
   ids). The record is a `preprint`, or a `journalArticle` when arXiv gives a
   journal reference. When the arXiv record names the DOI of the published version, the
   registrar's record replaces the preprint's (`_published_record`: venue,
   volume, pages and doi.org's own BibTeX; the arXiv id stays on the record,
   so the entry keeps its `eprint` and the popover its arXiv row) — arXiv's
   `journal_ref` is one free-text line ("Phys. Rev. Lett. 116, 061102
   (2016)") that would otherwise be typeset as the journal's name. Taken
   only when the registrar's title is the preprint's, never for arXiv's own
   `10.48550/` DOIs; the preprint record stands when doi.org has nothing.
2. **DOI content negotiation** (doi.org) — every DOI in the source URL and
   scan window, each with a glued-suffix trimmed variant. The CSL `type`
   becomes the record's item type (below); a book, a chapter, a conference
   paper, a thesis or a report keeps its publisher, a book or a chapter its
   ISBN.
3. **ISBN lookup** (books) — checksum-valid ISBNs *labelled* "ISBN" in the
   scan window or on the last page (copyright page, back cover), resolved via
   Open Library's books API, then Google Books. Both keyless.
4. **Crossref bibliographic search** — queried with the page title first
   (≥3 words; users title pages with the paper name), then the normalized
   text head. Deterministic; keeps most publisher PDFs off the AI fallback.
5. **AI extraction** — last resort, and its output is verified (below).

The registry helpers (`_fetch_arxiv` / `_arxiv_search` — both parse Atom
entries through `_arxiv_entry_meta` — `_fetch_doi`, `_crossref_search`) also
back the chat agent's `search_papers` tool, with OpenAlex as its third
registry (`gamma/openalex.py`; [ai_tools.md](ai_tools.md)).
Every registry call goes through `_http_get` (20 s timeout);
`GAMMA_METADATA_LOOKUP=off` makes it fail at once, so every lookup finds
nothing (an offline server; the browser suite sets it — a slow Crossref
otherwise held a page's first open, and a test's `networkidle` wait, for
up to 40 s).

**What the steps read.** Identifier scans and title matching use a
`SCAN_CHARS` (20k) head window, deliberately decoupled from the AI-context
pref, **plus the last page** — an issue-clipped Science PDF opens with the
*previous* article's tail (title 7k+ chars in) and prints its own DOI only in
the end-of-article trailer. Only the AI call is capped at the pref
(`context_char_limit`).

**What a registry record becomes.** Crossref keeps the publisher's markup
and entities in titles and venues (`<i>Planck</i> 2018 results`,
`CsPbCl<sub>3</sub>`, `Astronomy &amp; Astrophysics`) and inline MathML
(`spin-<mml:math>…1∕2…</mml:math>`), names an organisation author by `name`
rather than `given`/`family`, and keeps a subtitle apart from the title. A
journal that numbers its articles instead of paging them (APS, IOP) gives
the number as `article-number` and no `page`. `_fetch_doi` and
`_crossref_search` store plain text (`textnorm.plain_text`: tags dropped, a
formula read as its text through `gamma/mathml.py`, entities decoded; the
title then reads as the PDF prints it, `spin-1/2`, which is what the title
match and the popover need), join `title: subtitle`, keep organisation
authors, take the article number as `pages` (`_csl_pages`), and turn the
registry's work type into the record's item type. doi.org's own BibTeX
carries the same markup, writes months no style defines (`Sept`, `July`)
and leaves the article number out; `bibtex.normalize_entry` writes it as
valid BibTeX, the record's pages added, before it is cached
([import_export.md](import_export.md#bibtex-bibliography) lists the
rules). The quirks as observed live:
[research/registry-records.md](../research/registry-records.md).

**Item type.** `meta.kind` names what the record describes, under the names
Zotero gives its item types (`gamma/item_types.py`, mirrored for the
interface in `frontend/src/shared/lib/itemTypes.js`): `journalArticle`,
`conferencePaper`, `preprint`, `bookSection`, `book`, `thesis`, `report`,
`presentation`, `document`. Zotero's item types are CSL's types under
another spelling, the vocabulary reference managers and citation styles
share, and with Zotero's names a Zotero round trip is the identity. Only the
types a paper library holds are kept. Every source lands on one of them:

- doi.org's and Crossref's work type, through `item_types.from_registry`
  (Crossref's `proceedings-article` and CSL's `paper-conference` alike; a
  DataCite DOI answers in CSL's types, where `article` is a preprint).
  Containers (`journal-issue`, `proceedings`) and types a paper library does
  not cite are left out, and the record's fields decide.
- An arXiv record is a `preprint`, or a `journalArticle` with a journal
  reference; the book registries say `book`.
- The AI classifier picks from the list (its default prompt names it); the
  popover's **Type** row picks from the same list.
- A Zotero import reads `z:itemType`; any Zotero type outside the list
  becomes `document`, and an attachment-only item names none.

A record naming no type, or one of the kinds stored before these names, is
read through `item_types.item_type`, and nothing in storage is rewritten:
`notes` and `other` are `document`, `slides` is `presentation`, and `paper`
or nothing is decided by the fields. A publisher without a venue is a
`book`, an arXiv id without a published venue a `preprint`, anything else a
`journalArticle`, so a record of unknown type keeps the warning a paper gets.
An AI reply to a customised prompt that still answers the old kinds is read
the same way.

The type picks the BibTeX entry type
([import_export.md](import_export.md#bibtex-bibliography)), the Zotero RDF
item ([import_export.md](import_export.md#zotero-rdf-export)), the popover's
rows (a chapter's venue reads "Book title", a thesis's publisher
"University", a report's "Institution"; a field outside the type's rows
stays visible while it holds a value), whether AI metadata is flagged
(below) and the `Type:` line the AI `cite` tool prints. A thesis or a
report that names no publisher takes its venue as one
(`item_types.publisher`): the AI was asked for a publisher on books only
before, so an older thesis record holds the university as its venue.

**Trust rules** (the first DOI on page 1 can belong to a *cited* paper, and
AI output can be a plausible hallucination):

- Identifiers with **URL-level trust** are accepted outright: the stored
  `source_url`, the `web_url` the extension clipped from, and
  detector-supplied `doi`/`arxiv_id` hints (`fetch_page_metadata` kwargs —
  `/api/clip` forwards what `detect.js` read off the publisher page's own
  meta tags).
- A record found via the *text* counts as **confirmed** only when the
  registry's title appears in the PDF text (`_title_in_text` — normalized
  for case, ligatures, line-break hyphens; a title with a subtitle also
  counts when both parts appear, since the PDF sets them on separate
  lines). Unconfirmed resolutions are kept
  only as a fallback when nothing confirms; a Crossref search hit is accepted
  solely on title-in-text evidence.
- An ISBN record is confirmed by `_record_in_text`: the paper rule, or —
  book titles are short ("Lasers") — the whole title *and* one author
  surname in the text head. Unconfirmed → fallback, like a DOI.
- AI output goes through `_verify_ai_meta`: an identifier it produced is
  resolved and, on success, replaced by the registry record; one that
  resolves nowhere and doesn't occur in the PDF is dropped as fabricated; the
  AI title is cross-checked against Crossref (≥0.92 title similarity +
  compatible year upgrades it).
- **Book search.** When the AI classified the document as a `book` (or a
  `document`, where it files a book it is unsure of) and Crossref has
  nothing, the AI title + first author go to
  `_book_search` (Open Library `search.json`, then Google Books
  `intitle:/inauthor:`). `_pick_book_match` accepts a hit only when the
  titles are alike (≥0.9, or one is the other plus a subtitle) *and* an
  author surname agrees: both were read off the title page, and the
  registry confirms they name a real book. The registry supplies the
  canonical title, authors and publisher. The AI-read year stays, since it
  names the edition in hand while the registry's is the first publication.
  An ISBN is adopted only if the PDF text prints it, so books published
  before ISBNs resolve through this path too.

**Source and the unverified flag.** `meta.source` is `arxiv` / `doi` /
`isbn` / `crossref` (search hit whose doi.org fetch failed) / `openlibrary`
/ `googlebooks` / `ai` / `manual`. Every fetched record also stores
`meta.unverified`: true when nothing tied it to *this* document — an AI
record of a type a registry could confirm, or a DOI/ISBN fallback whose registry title
isn't in the text (it may belong to a work the document cites). Unverified
records get a red "!" badge on the metadata button, a red Source row in the
popover, a red "!" + source tag beside the slide citation in the share
popover, and a red cell in the Settings → Maintenance table. The wording lives
in one place, `metaSourceInfo` in `frontend/src/shared/lib/utils.js`; the predicate
`isUnverifiedPaperMeta(source, kind, unverified)` falls back to the
same type rule for records stored before the flag existed. For AI records
the warning fires only for the types a registry holds
(`item_types.CHECKABLE`: journal article, conference paper, preprint,
chapter, book). A thesis, a report, slides and course notes have no registry
record to verify against, so they get a quiet "AI-extracted (document)"
instead. A hand edit keeps the item type unless the Type row changed it, and
clears the flag (the user vouched for the record). So does **Verify** on a flagged Source row, without
an edit: `/api/metadata/verify` sets `meta.user_verified` and clears
`meta.unverified`, keeping the source, so the row reads "AI-extracted —
verified by hand" (Settings: "AI ✓"). With unsaved edits in the popover,
Verify saves them instead. A refetch replaces the record and drops the mark.

The fetch also kicks background search indexing for the paper
(`ai_context.ensure_indexed`) — the paper is being set up, so search, the AI
document map and library-wide Ctrl+F shouldn't wait for the first search to
discover it. `/api/metadata/update` saves hand-edited fields from the
metadata popover (rebuilds BibTeX, source `manual`, invalidates the cached
citation). The popover sends the Type row's choice as `meta.kind`; one not
sent, or not an item type, keeps the stored type, so a conference paper
stays an `@inproceedings` after its title is corrected. The type alone is not
a record: blanking every field clears the metadata. In the popover, the DOI and arXiv rows carry an open-on-registry
link and a copy button for that URL beside the field. A page with a PDF
also gets a read-only Source file row (the attachment's path, with a copy
button); replacing the PDF goes through the paperclip (detach, attach).

**Citation key.** The BibTeX key is generated from the first author's surname
and the year (`bibtex.default_key`). The popover's Cite key row pins one as
`properties.cite_key`, beside `meta` so a refetch keeps it. Empty means
generated, and the placeholder shows the key in use. A cached fetch answers
`bibtex.page_entry`, the entry every other surface prints, so the popover's
Copy BibTeX and the `.bib` export agree on the key. How a pin is cleaned
and who reads it: [import_export.md](import_export.md#bibtex-bibliography).

**Slide citation.** The PPT-style markdown citation is generated *in the
same fetch* as the metadata (`_make_ppt_cite`, one AI call over the BibTeX;
the client passes its `cite_prompt`/`cite_model` prefs, `/api/clip` uses the
defaults) and returned as `ppt_cite`, so it is ready the moment the record
is. A citation failure
never fails the fetch. `/api/metadata/cite` is the regenerate path (↻ in the
Share dialog's Citation section) and the fallback the client's citation effect uses on open
for pages whose record predates this, whose citation call failed, or whose
metadata was just edited — one attempt per page per session, only when AI
is configured.

`GET /api/metadata/status` reports library-wide health (per paper: metadata
present/failed + source/kind, extracted-text chars and index state from the
FTS tables) — it feeds the Settings → Maintenance pane's status table and its
adaptive batch retry (selected, else missing + unverified-AI; plus "Refetch
all"/"Refetch shown"). The batch is the `metadata` background job (`POST
/api/jobs/metadata`, [tasks.md](tasks.md)): the server looks the papers up
one after the other with `force`, through the same `fetch_page_metadata` as
`metadata/fetch`, so closing Settings or the tab does not stop it. The pane's
batch row shows the job's progress and Stop, and the table reads itself again
after each paper.

No Google Scholar — it has no API and blocks scraping. The book registries
(Open Library, Google Books' anonymous quota) are keyless public APIs;
neither needs configuration. Google Books' quota belongs to the server's
address, so a batch of book-like documents can run into its 429. A 429 is not
retried: Google Books then rests for the `Retry-After` it names, else 10
minutes (`_google_books`), and says so in one log line. In the meantime a
book lookup asks Open Library alone, and the AI-read record stands as it is.

PDF uploads use the browser-provided original filename as their initial page
title. Once the upload UI completes, a single file opens and gets the open
page's lookup; several files go to the same `metadata` job, without `force`.
That job is one per account and workspace, so a later upload adds its papers
to the job already running. The open page receives the record over its
socket: App's remote-op handler copies `meta`, `bibtex` and `ppt_cite` into
the metadata button's state, so a lookup run elsewhere (this job, the
extension's clip) shows without a reload.
The page stores an `auto_title` compare-and-swap marker: a successful lookup may
replace that filename with the paper title only while the page title still
matches the marker. Any explicit rename clears it, so a slow lookup cannot
overwrite the user's edit. The fetch response always carries `page_title`,
the page's title as it stands after the write (`title_updated` says whether
*this* call did the rename): two lookups can race — the extension's
background lookup after a clip and the one the app starts when the page is
opened — and the loser still has to show the winner's rename, so the client
(`mergeMetaResult` in App.jsx) takes the server's title over its own copy and
drops the marker once the title has moved off it. The AI fallback model is selected in Settings →
Providers; arXiv and DOI resolutions do not call that model.

## PDF resolution

Institutional subscriptions apply to the **backend's outbound network**, not
the browser opening Gamma. Run the backend on the subscribing institution's
network (or its supported full-tunnel VPN) to fetch subscribed PDFs directly.
There is no publisher allowlist: direct PDF links and article pages advertising
`citation_pdf_url` use the same resolution path. APS article pages use their
direct `/pdf/` route before the advertised legacy `link.aps.org` URL, which
can redirect back to the abstract.

Outbound fetches retain cookies across redirects in a fresh in-memory cookie
jar per fetch. This supports publisher handshakes such as Nature's without
sharing sessions between users. Every redirect
still passes the SSRF guard. This does not execute JavaScript or solve browser
challenges. If a publisher requires those, open its PDF in your browser and
use the Gamma Connector, or download the file and drop it into Gamma. The
Connector tries a browser upload first when saving from a PDF tab; on article
pages it falls back to the browser if the server's save fails.

`/api/resolve-pdf`: arXiv abs/html→pdf rewrite (new- and old-style ids,
`pdf.ARXIV_ID`) → direct fetch (an arXiv PDF that `arxiv.org` refuses, as it
does some to programs with a 406, is fetched from `export.arxiv.org`, arXiv's
host for automated clients) → HTML pages inspected for the `citation_pdf_url`
meta tag, a page that only redirects by `<meta http-equiv="refresh">` followed
first (up to two hops; Elsevier's DOI landing, linkinghub, is one) → Unpaywall
open-access fallback for DOIs (`_open_access_pdfs`: every location with a PDF,
published before accepted before submitted, the publisher's copy before a
repository's, each URL once). Up to four copies (`OA_TRIES`) are tried in that
order and the first that answers with a PDF wins, so one broken repository
link does not end the fallback. When none confirms, the best one is still
handed on for the proxy to try. The fallback is disabled when the
request sends `allow_oa: false` and identifies itself with a fixed project
email in `routers/pdf.py` (`CONTACT_EMAIL`, no config). Non-published
substitutions return a `note` the frontend surfaces.

The answer also carries a `version` when the walk knows it: `publisher` (the
article page's own PDF), `preprint` (an arXiv PDF), or an open-access copy's
`published` / `accepted` / `submitted`; a PDF the link itself served is of
unknown version. `resolve_source(published_only=True)` (the agent's
`fetch_paper` with `version: "published"`) substitutes only published
open-access copies; when the only copies are other versions it answers 400
naming them.

Resolution only picks a candidate URL — the download behind it can still fail
(paywall, blocked server-side fetch, HTML behind the link). So `openPdf` in
`app/App.jsx` preflights the resolved URL with `probePdfUrl` (`shared/lib/utils.js`): it opens
`/api/pdf` without `save=1`, keeps the headers and cancels the body, and only
then creates the page. `/api/pdf` is the single arbiter of "is this a PDF" —
its 400 `detail` becomes the failure status, and no page is left behind. A URL
whose paper is already in the library skips the preflight, so an existing page
stays openable even after its source goes away.

### Connected publisher sessions

The Connector's cookie button (the popup footer's **Publisher sessions**
drawer) imports a snapshot of cookies for the current HTTPS publisher host. `GET /api/publisher-sessions`
returns supported publisher roots and the caller's connection metadata;
`POST` replaces one host's snapshot and `DELETE /{host}` disconnects it.
Cookie values are never returned. Guest accounts and share links cannot use
these endpoints. Imports require HTTPS (including the existing trusted proxy
configuration) or localhost and are bounded to 256 KiB / 200 cookies.

`users.db.publisher_sessions` stores authenticated Fernet ciphertext
per `(user_id, host)`, each sealed with its account's id. The key is generated at `GAMMA_DATA_DIR/publisher-sessions.key`
with private file permissions where supported, or supplied as a Fernet key in
`GAMMA_PUBLISHER_SESSION_KEY`. Keep it stable across workers and restarts. Encryption
protects a database copy without the key; the server operator can access the key
and credentials. Workspace exports and backups do not include this account table.
Full server backups contain the encrypted rows but do not copy the key: retain
the key separately or reconnect after moving/restoring to another machine.

A snapshot also keeps the connecting browser's User-Agent (`user_agent` in the
`POST`, the Connector's `navigator.userAgent`; optional, dropped when it is not
a plain header value). Every request to that host then presents it
(`net_guard._BrowserAgent`, redirects included), so a cookie the site bound to
that browser — a bot check's clearance, which Cloudflare ties to the
User-Agent and the IP address — is sent the way it was issued. That helps
where the server shares the browser's network (the desktop app, a server on
the same machine); it cannot help a server behind another IP address.

Authenticated `/api/resolve-pdf`, `/api/pdf`, and `/api/clip` requests seed their
outbound cookie jar from the caller's snapshots via a request-local ContextVar.
Each guarded fetch gets its own jar, except inside `net_guard.browsing_session`:
`/api/clip` and the AI's `fetch_paper` run the resolver's walk and the download
in one, so a cookie the landing page sets reaches the PDF request, as in a
browser tab. The resolver fetches an advertised `citation_pdf_url` with the
article page as `Referer` and returns it (`referer`) for the download.
AI chat's `fetch_paper` tool also uses the caller's snapshots: `_chat_scope`
captures the authenticated account, and the tool binds and resets the ContextVar
inside its worker (streamed tools run in a separate thread). The model cannot
choose an account and never receives the cookie values. **Fetch documents**
controls the tool; the per-chat-kind **Use journal sign-ins** permission
(`publisher_cookies`, default on) controls cookie use. When off, `_chat_scope`
excludes the account from the fetch context, so it also bypasses authenticated
cached text. Stored connections and interactive PDF downloads are unchanged.
Host, path, HTTPS and expiry checks apply on every redirect. Parent-domain cookies
are narrowed to the exact connected host. Guest, anonymous and share requests,
other AI tools and background jobs do not use these credentials. Fetched text
and URL aliases are cached by account and a fingerprint of its usable cookies,
so one account cannot reuse another's authenticated document. Connecting,
refreshing, disconnecting or expiring cookies changes that cache partition,
so an abstract cached under the old access is fetched again under the new. Proxy responses are
private and not cached by shared HTTP caches. Saved PDFs retain the workspace's
normal access rules; connecting a session does not alter workspace permissions.

Session cookies are capped at 24 hours, others at 30 days or original expiry.
Upstream cookie changes are transient; the Connector re-imports a connected
host's snapshot on its own when the user visits that host and the copy is
over an hour old (its `autoRefreshSessions` setting, on by default; the rule
in [extension.md](extension.md#publisher-sessions)), and **Refresh now** in
its drawer does so by hand. Disconnect removes the live record; existing full server
backups may retain encrypted older snapshots. Deleting an account deletes its
connections. Supported roots are explicit in `publisher_sessions.py`; a different
publisher host (even a sibling) needs its own connection. CAPTCHA clearance tied
to a browser/IP may still fail, so the browser upload fallback remains available;
in the AI chat that fallback is the browser handoff
([ai_tools.md](ai_tools.md#walls-and-the-browser-handoff)), after which the
Connector refreshes the connected host's snapshot from the session that worked.
