# Gamma Connector — the browser extension

A Zotero-Connector-style Chrome (Manifest V3) extension in `extension/`: one
click on a paper's landing page or PDF tab saves it into the user's library —
PDF stored, page created, folder/labels applied, metadata resolved — with
"already in your library → open" detection and a right-click clipper for links
and text selections. Server side: `gamma/routers/clip.py`. No build step
(plain ES modules, load unpacked); install steps in
[extension/README.md](../../extension/README.md).

## What it does

1. **Save a paper from its landing page.** arXiv abs or HTML, publisher page, DOI
   link, OpenReview… the toolbar badge shows `PDF` / `arX` / `DOI` (`?` for
   a DOI merely found in the text). The popup shows the detected title, a
   folder picker and labels → **Save to Gamma** → *Open in Gamma*
   (`/?block=<id>`).
2. **Save the PDF you are looking at.** The tab *is* a PDF, possibly behind
   an institutional login the server can't reach: the bytes are fetched with
   the browser's session → `POST /api/uploads` → `POST /api/clip {doc_id}`,
   automatically — browser-first on PDF tabs, and as a fallback on any page
   whose PDF the server fails to fetch (no checkbox; see the pipeline below).
3. **Right-click**: *Save link to Gamma* (link), *Save page to Gamma* (page),
   *Clip selection to Gamma* (selection → a `> quote — [title](url)` block
   under the paper matching this tab, else under a "Web clips" note page).
   Results arrive as a notification whose click opens the page.
   A page with no PDF at all still saves: it becomes a page of its own
   carrying the tab as `web_url` (see the pipeline below).
4. **Already in the library** — ✓ badge; the popup offers *Open in Gamma*
   and *Add to another folder…* instead of a duplicate save. When the
   library page's title differs from the paper the tab resolved to, the
   notice says "Already in your library as “…”", so a stale metadata record
   on that page is visible rather than shown as this paper's title.
5. **Preview before saving.** A PDF tab has no meta tags to read a title
   from, so for every detected DOI / arXiv id the worker asks
   `GET /api/library/preview` for the registry record. The popup head then
   shows the paper's title plus authors · year · venue. The previewed title
   also names the new page on save (`auto_title`, so the metadata lookup may
   still replace it).
6. **Ctrl+Shift+S** saves the current page with the default folder.
7. **Options**: saved-server switcher, server URL, sign in / out, default folder + labels, *prefer
   open-access fallback* and *keep a PDF copy* (the app's `oaFallback` /
   `pdfSaveLocal` prefs, sent as `allow_oa` / `save_copy`), and whether
   connected publisher sessions refresh automatically.
8. **Publisher sessions** — the cookie button in the popup's footer. On a
   supported journal host over HTTPS it offers to send the browser's cookies
   for that host to the signed-in Gamma account (`POST
   /api/publisher-sessions`), so the *server* can download that journal's
   PDFs later. Details below.
9. **Fetches for the AI chat.** When the chat's `fetch_paper` meets a
   sign-in, a bot check or a paywall, its card opens the page in a tab the
   Connector watches; once the user is through, the Connector sends the PDF
   back to the chat on its own. Details below.

Non-goals: reading or annotating inside the extension, a local library,
syncing highlights back to the source page.

## Architecture

```
browser tab ──detect.js──▶ worker.js ──fetch, cookies──▶ Gamma server
 meta tags, URL,            per-tab state + badge        POST /api/clip
 JSON-LD, DOI regex,        save pipeline                GET  /api/library/lookup
 selection                  context menus, command       GET  /api/library/preview
                            popup.html · options.html    GET  /api/library/folders
                                                         POST /api/clip/note
                                                         POST /api/uploads · GET /api/session
```

**Thin client, fat endpoint.** The extension only *detects* and *asks*; one
server call, `POST /api/clip`, runs the ingest that `openPdf` in App.jsx
orchestrates client-side (resolve → probe → cache → page → metadata), through
the same helpers the app's endpoints use. Keep the ingest logic in those
helpers — never re-implement it in the extension.

| File | Role |
|---|---|
| `manifest.json` | MV3: module service worker, `<all_urls>` content script, popup, options, `save-to-gamma` command. `host_permissions: ["<all_urls>"]` — the same install warning the content script already carries, and it makes cookie-carrying fetches to the (user-configured) server origin and the PDF-from-tab fetch work without runtime permission prompts |
| `worker.js` | per-tab state in `chrome.storage.session` (`tab:<id>` → `{candidate, hit, preview, auth, saving, error}`), badge/icon, `lookup` + `preview`, the save pipeline, context menus, keyboard command, notifications, the publisher-session status cache + automatic refresh (`publisher:auto`, `publisher:attempts` in session storage), the tabs fetching for the chat (`handoffs`: tab id → request, `handoff:queue`), and the message API (`get-state`, `save`, `clip-selection`, `auth-changed`, `publisher-status`, `open`, and bridge.js's `connector-hello` / `connector-probe` / `connector-tab`) |
| `handoff.js` | the chat-fetch rules: a tab's `/go` address → the request id (`handoffIdFrom`), whether the tab's paper can be the requested one (`sameWork`), which URLs to try in it (`harvestUrls`), which one to open in the tab when downloads fail (`nextToOpen`, `needsSignIn`, `signInUrl`), whether the tab shows a bot check (`checkPage`), and whether every background turn is taken (`backgroundBusy`, `MAX_BACKGROUND`, `NEEDS_YOU`) — pure, tested in `tests/` |
| `bridge.js` | content script between the Gamma app and the worker: a `connector-probe` window message gets the worker's verdict on one request (`ok` / `signed-out` / `other-account` / `unreachable`), a `connector-tab` one (`open`, `show`, `close`) the worker's answer (`opened` / `queued` / `shown` / `none` / `closed`), and a `connector-hello` (no request) answers `connector-here` to the Connector's own server's app only — how Gamma knows not to suggest the extension to a browser that has it (`shared/lib/connector.js`, [onboarding.md](onboarding.md)); nothing to a page the worker gives no answer for; a question a second per request and kind |
| `ids.js` | the identifier rules — a DOI used as a URL path (`gammaDoiFromPath`) and the arXiv id (`gammaArxivId`) — one file loaded by the content script and imported by the worker, tested in `tests/` |
| `detect.js` | content script (`document_idle`): identifier extraction, re-run on SPA URL changes; answers `get-detection` / `get-selection` / `fetch-pdf` (downloads a PDF from inside the page and relays it base64 — publisher bot checks that 403 the worker's fetch accept the page's own same-origin request) |
| `api.js` | settings (`chrome.storage.sync`: `server, servers, folder, labels, allowOa, saveCopy`), `api()` fetch wrapper (`credentials: "include"`, JSON `detail` → `ApiError{status}`), `login/logout/whoAmI` |
| `publisherSessions.js` | Publisher-host validation and the connection flow (checks the active tab and account, then sends a snapshot to Gamma with the browser's `navigator.userAgent`); the automatic-refresh rule (`shouldAutoRefresh`, `REFRESH_AFTER` / `RETRY_AFTER`) and the status text (`describeSession`) — pure, tested in `tests/` |
| `popup.html/js/css` | setup (no server) → offline (server unreachable, with Retry) → sign-in → main view; the footer shows a connection dot (green signed in / amber signed out / red unreachable) beside `host · user`, the publisher-session **cookie button** and an options gear (the app's SettingsIcon). The folder picker and label suggestions are plain-JS menus mirroring the app's MenuSelect/ctxMenu recipes; labels are the app's `categoryTag` chip input (comma/Enter commits a chip, Backspace removes, arrow keys + Enter pick a suggestion). Saving remembers the folder but not the labels — each popup prefills only the options-page default labels. `popup.css` reads the app's design tokens and repeats `shared/styles/app.css`'s control recipes (buttons, fields, the switch, the menu surface, the focus ring) — keep those in step when the app's recipes change. `?tab=<id>` targets a specific tab when opened as a page (tests) |
| `tokens.css`, `fonts/` | committed copies of the app's `shared/styles/tokens.css` and the Latin subset of Inter, like the desktop shell's ([ui-design.md](ui-design.md#the-desktop-shell-and-the-extension)): `npm run copy-tokens` in `frontend/` refreshes them, and `frontend/tests/themes.test.mjs` fails while a copy differs from its source |
| `theme.js` | a classic script in the head of both pages, before the stylesheets: the app's pinned theme isn't knowable here, so it sets `data-theme` / `data-scheme` to Light or Dark from `prefers-color-scheme`, live |
| `options.html/js` | server + host permission, account, saving defaults |
| `serverList.js` | shared saved-server rows for options and the popup footer: active checkmark, switch action and remove button, using the existing menu/close-button styles |
| `icons/` | blue tile (paper detected) and grey tile (nothing) at 16/32/48/128, generated with Pillow |

## Detection

`detect.js` yields one candidate per page:

```js
{ kind: "pdf" | "arxiv" | "doi" | "maybe" | "none",
  source_url, pdf_url, arxiv_id, doi, title, is_pdf_tab }
```

| Signal | Yields |
|---|---|
| `arxiv.org/abs|pdf|html/<id>` in the URL, `citation_arxiv_id` — new-style (`2310.06825`) and old-style (`cond-mat/0402216`) ids, the server's `pdf.ARXIV_ID` shape | `arxiv_id` (version stripped) |
| a DOI used as a path — `doi.org/<doi>`, `/doi/…/10.…` (Atypon, Wiley), publisher PDF paths built on it (APS `/prl/pdf/<doi>`, Springer `/content/pdf/<doi>.pdf`, IOP `/article/<doi>/pdf`; the view/file suffix stripped by `ids.js`, the same rule as the server's `norm_doi`) — then `citation_doi`, `dc.identifier`, `prism.doi`, JSON-LD `*Article` identifiers | `doi` |
| `contentType === application/pdf` / `.pdf` URL, `citation_pdf_url`, `<link rel=alternate type=application/pdf>` (a page advertising none has no `pdf_url` — never itself) | `pdf_url` |
| `citation_title`, JSON-LD headline, `dc.title`, `og:title`, `document.title` | `title` |
| DOI regex over the first 30 k chars of visible text (only when nothing else matched) | `kind: "maybe"` |

`kind` priority: pdf > arxiv > doi > maybe. Chrome's PDF viewer runs no
content scripts, so the worker also derives a URL-only candidate on every tab
load (`candidateFromUrl`, `from_url: true`) and merges it field-by-field under
the content script's result; URL-looking tab titles are dropped there.

Every detection triggers `GET /api/library/lookup` (skipped when signed out)
and sets the badge: `PDF`/`arX`/`DOI` blue, `?` grey, `✓` green (in the
library), `!` red (not signed in). A detection with a DOI or arXiv id then
fetches `GET /api/library/preview` off the badge's critical path (doi.org
can take a second or two) and stores the record as `preview`. A popup that
is already open re-renders its head from `storage.onChanged`. State is
cleared when the tab navigates.

The popup head shows the first of: the page's own title (meta tags), the
previewed registry title, the library page's title, the host. The chip +
identifier line says what the detection rests on, and the registry's
authors · year · venue sit under it.

## The save pipeline

Popup → `save` message → `savePaper()` in the worker (so it survives the popup
closing; progress is written to the tab state and the popup renders it):

```
PDF tab?  fetch bytes in the browser → %PDF check → POST /api/uploads → doc_id   (best-effort)
POST /api/clip { source_url, pdf_url, doi, arxiv_id, doc_id?, title, selection?, folder, labels, allow_oa, save_copy }
  └─ 400 and no doc_id yet? → fetch bytes in the browser → POST /api/uploads → retry /api/clip with doc_id
→ { block_id, doc_id, title, existed, open_url, folder, labels, note? }
   doc_id "" = no PDF: the clip became a page with properties.web_url
```

Browser-side downloads are automatic, no checkbox: PDF tabs upload their bytes
up front (the browser already has them; the server may be paywalled out), and
any other save that fails server-side with a 400 retries through the browser
when a `pdf_url` was detected. If the browser fetch fails too, the server's
error (paywall explanation) is the one shown. "Fetch bytes in the browser"
itself is two attempts: the worker's direct `fetch(url,
{credentials:"include"})` first, then — publisher bot checks (science.org
& co.) 403 requests with an extension origin and no Referer — the tab's
content script via `fetch-pdf`, a same-origin fetch from the page's own
context, indistinguishable from the reader loading the PDF, relayed back
base64 (capped at 60 MB). Raw PDF tabs have no content script, so there the
direct fetch is the only (and working) path.

Server side (`clip.py`, sync `def` — it downloads):

1. **Dedup** — `find_page()` by DOI / arXiv id / URL against every root page:
   `properties.meta.doi|arxiv_id` (from the metadata lookup), `source_url`,
   `web_url`, and the proxy-cache hash `sha256(url)[:24]`. A hit returns
   `existed: true` and still *adds* the folder/labels (soft link; an ancestor
   folder is refined away, `foldertags.add_tag`).
2. **Resolve** — `pdf.resolve_source()` (extracted from `/api/resolve-pdf`) on
   the best identifier: `pdf_url` > `arxiv_id` > `doi` > `source_url`. arXiv
   rewrite, `citation_pdf_url` sniff, Unpaywall when `allow_oa`.
3. **Fetch + store** — `pdf.download_pdf()` through the SSRF guard with browser
   headers; the file lands at `uploads/<sha256(url)[:24]>.pdf`, the same id
   `/api/pdf?save=1` would use, so the app's viewer finds it. Over the storage
   limit → not stored, `note` says so, the page proxies on open. `save_copy:
   false` → headers-only probe. A dead or HTML link never creates a page with
   a broken attachment (the `openPdf` invariant) — instead the clip takes the
   **web-page path** (`_clip_web_page`): a page titled from the tab (else the
   URL's last segment / host) with `properties.web_url = source_url`, no
   `doc_id`/`source_url`, the request's `selection` (if any) as its first
   `> quote — [title](url)` block, folder/labels applied, `doc_id: ""` and a
   `note` in the response. A request that names no PDF at all (no `pdf_url`,
   DOI, arXiv id or uploaded bytes — *Save page to Gamma* on a blog post)
   skips the resolver and goes there directly. Re-clipping the same URL finds
   that page (`find_web_page`: `web_url` match on attachment-less pages) and
   only files it / appends the new selection. Only a request with nothing at
   all (no URL, title or selection) is a 400. With `doc_id` (uploaded bytes)
   this step is skipped; the file must exist.
4. **Page** — `blocks_store.get_or_create_doc_page()` (extracted from
   `POST /api/blocks/by-doc`) with `default_title = citation_title`, so
   `auto_title` is set and the metadata lookup may still replace it (never a
   user rename). The tab URL is kept as `properties.web_url` when it differs
   from the PDF URL — it feeds later lookups. The lookup and the insert run
   under the workspace's write lock (`blocks_store.write_lock`), and so do
   the web-page path's `find_web_page` and `/api/clip/note`'s "Web clips"
   page: a double-clicked Save, two tabs of one paper or a retry make one
   page.
5. **Folder + labels** — `properties.folder` / `properties.category` comma
   lists, cleaned by `foldertags`: an op batch on the page by the clipping
   account (`_apply_tags`), fanned out to the page's open tabs like any
   edit, also when the clip found the paper already saved.
6. **Metadata** — `metadata.fetch_page_metadata()` (extracted from
   `/api/metadata/fetch`) in a daemon thread; the detector's `doi`/`arxiv_id`
   ride along as trusted hints (they come from the publisher page's own meta
   tags, so the lookup resolves them directly instead of re-mining the PDF
   text); arXiv/DOI paths need no AI provider. Skipped when `meta` already
   exists or `fetch_metadata: false`. A web-page clip starts it only when a
   DOI/arXiv id was detected (the lookup needs no PDF for those — a note about
   a paywalled paper still gets its citation).

Companions:

- `GET /api/library/lookup?doi=&arxiv_id=&url=` — 404 when absent.
  Identifiers are also extracted from `url` (`norm_doi` drops a publisher
  path suffix like `/pdf` or `.pdf` glued onto the DOI); web-clip pages
  match by `web_url`.
- `GET /api/library/preview?doi=&arxiv_id=&url=` — the registry record
  `{title, authors, year, venue, doi, arxiv_id, source}` from
  `metadata.registry_record` (arXiv, then doi.org; a small in-memory cache,
  the data is public). 404 when neither registry answers.
- `GET /api/library/folders` → `{folders, labels}` (folder paths plus their
  ancestors). Folders rank by the account/workspace's latest `recent-views`
  page timestamp, then latest page modification, with names breaking ties;
  ancestors inherit their contained pages' timestamps. Labels stay alphabetical.
- `POST /api/clip/note {text, source_url, title,
page_id?}` — the explicit "clip selection INTO a page" append path (with
`generate_key_between`; without `page_id` it uses/creates the root page
flagged `properties.web_clips = 1`), as opposed to `/api/clip`'s "make a page
of this tab". All session-only, and — since the extension names no
workspace — they land in the account's personal workspace
([workspaces.md](workspaces.md)).

## Publisher sessions

The popup's footer has a cookie button (hidden for guests, incognito tabs
and servers without `/api/publisher-sessions`). Its state is this tab's
journal host: grey *off* (not a supported publisher — the drawer still lists
connected hosts), blue *ready* with a pulsing pip (a supported HTTPS host,
not connected yet), green *connected*, and while a snapshot uploads the
cookie wobbles inside a spinning ring, then a green check pops (*done*) or
the pip turns red (*err*). Clicking opens a drawer above the footer: a card
for this tab's host (status line "refreshed … ago · expires in …", **Connect
this journal** / **Refresh now**), the other connected hosts with a
disconnect ×, the **Refresh automatically** switch and a one-line privacy
note. The drawer's data comes from the worker's `publisher-status` message
(a 5-minute cache of `GET /api/publisher-sessions`; `force: true` after a
connect or disconnect). The connect itself runs in the popup — the optional
`cookies` permission must be requested from the user's click — through
`connectPublisher`, and the popup re-renders when the worker's
`publisher:auto` record changes.

**Automatic refresh** (the `autoRefreshSessions` setting, default on, also on
the options page). On every https tab load the worker checks whether the
host is one the user already connected; a host that was never connected by
hand is never imported on its own, and nothing happens without the
`cookies` permission (never a prompt). A connected host is re-imported once
the server's snapshot is `REFRESH_AFTER` (an hour) old, at most one attempt
per host per `RETRY_AFTER` (ten minutes). Rationale: the browser's cookies
are by definition the working ones and the server's copy can only be staler,
so replacing it is never worse — except right after signing out of the
publisher, which is why a fresh snapshot is left alone rather than rewritten
on every page view. The outcome lands in `chrome.storage.session`
(`publisher:auto` → `{host, at, ok, error}`); a failure shows in the drawer's
card for that host, a success just as a newer "refreshed … ago". Server
side and the security model: [paper_metadata.md](paper_metadata.md#connected-publisher-sessions).

## Fetches handed from the chat

The AI chat's `fetch_paper` hands a paper to the browser when a CAPTCHA, a
sign-in page or a paywall stopped the server
([ai_tools.md](ai_tools.md#walls-and-the-browser-handoff)). The reply's card
asks the Connector to open the publisher's page itself (`bridge.js` →
`connector-tab` `open`); when no Connector has answered it yet, or in the
desktop app, the card opens `<server>/api/ai/handoffs/<id>/go` instead, a
Gamma page that goes straight on to the publisher and whose address the
Connector knows the tab by:

1. **Bind.** On `open` the worker checks the request with its own server
   (`GET …/<id>`: another account's, an expired or a made-up request gets no
   answer), makes a blank tab next to the Gamma tab that asked, records it
   under `handoffs` in session storage with the address the Gamma page asked
   from (`app`) and the Gamma tab (`opener`), takes the request with `POST
   /api/ai/handoffs/<id>/watch`, and only then loads the publisher's page, so
   the binding exists before the page can finish loading. A page already
   open for the request is shown instead. On the `/go` path `tabs.onUpdated`
   (and `onCreated`, by the pending URL) sees the tab load a `/go` address
   (`handoffIdFrom`) — on any host, since one server is often reached as
   `localhost` and `127.0.0.1` or through a proxy — and the worker binds it
   the same way. A tab the bound tab opens (a "PDF" link with
   `target=_blank`) is bound to the same request.

   **In the background.** With the chat's "Fetch blocked papers in the
   background" setting (on by default) the card sends `open` with
   `background` by itself. The tab then loads next to the Gamma tab that
   asked, without the focus (`backgroundTab`): an ordinary tab the user can
   see working and switch to. `MAX_BACKGROUND` (3) requests run at a time;
   more wait in `handoff:queue` (the card reads `queued`) and start as turns
   free up. A tab waiting for
   the user (a `NEEDS_YOU` note: `signin`, `check`, `looking`, `refused`,
   `other`) holds no turn. The watch call says `background`, so the card can
   say where the page is. Switching to such a tab (`tabs.onActivated`) makes
   it the user's: it keeps its request but loses the `background` flag, so
   it frees its turn, is not closed under them when the PDF arrives, and
   nothing notifies them about the tab they are looking at.
2. **Harvest.** Each page a bound tab finishes loading off the Gamma server
   is a chance: the worker checks the request still waits (`GET …/<id>`;
   settled or gone releases the tab), reads the page's detection afresh
   (`get-detection` with `fresh`), skips a page that names another DOI or
   arXiv id in its metadata (`sameWork`; a DOI only guessed from the page's
   text, often a reference's, does not count; the note `other` says so), and tries, in order, the tab
   itself when it shows a PDF (Chrome's viewer runs no content script), the
   page's advertised PDF link, its "View PDF"-style links on the same site
   (`detect.js` `pdfLinks`: a `/pdf`, `/epdf`, `/pdfft` or `/article-pdf`
   route, or "PDF" in the link text; supplementary files left out), and the
   server's PDF link when the page is a paper page of that site
   (`harvestUrls`: a sign-in page costs no request). A PDF the page shows
   in a frame counts as a link (`iframe` / `embed` / `object` with "pdf" in
   its address — IEEE's `stamp.jsp` frames `stampPDF/getPDF.jsp`). A page
   that shows no link yet is looked at again after 3 and 6 s (publisher
   pages render the link after load), or after 3, 6, 9 and 12 s when it is
   a bot check (`checkPage`: a check host, a challenge title, or `detect.js`
   finding a challenge widget on a page with little else), which a real
   browser often passes by itself; then it is noted `looking` or `check`.
   Each URL goes through the save pipeline's
   `bytesFromTab` — the worker's credentialed fetch, then the tab's own
   same-origin fetch — and only a real `%PDF` counts; a failed attempt
   remembers where it ended (`landed`).
3. **Open.** When every download failed, the tab opens a link itself, as a
   click would (`nextToOpen`), and the PDF it ends on is taken from Chrome's
   viewer on the next load. A navigation gets what a script's download does
   not: ScienceDirect's `pdfft` is an HTML page whose script redirects to a
   signed `pdf.sciencedirectassets.com` URL; Silverchair (AIP) redirects to
   its watermark host, which the page's own fetch cannot follow across
   origins; bot rules refuse requests that are not navigations. A link whose
   download ended on a sign-in page, or back on the article itself, is not
   opened — the user has no access yet, and opening it would lead away from
   the page's sign-in buttons. Each link is opened once, again (at most
   three times) only after the tab has been on another site since, such as
   a university sign-in that sends the user back to the article.
4. **Deliver.** The first PDF goes to `POST /api/ai/handoffs/<id>/pdf` with
   the URL it came from. On HTTPS the worker refreshes that publisher's
   connected cookies right away (`autoRefreshPublisher(…, {force: true})`: the
   session just worked, so the server's copy should match it; still only for
   a host connected by hand, with the cookies permission). Then it releases
   every tab of the request. A background tab closes, with nothing else to
   see. After a tab the user saw, the worker shows a "Sent to your Gamma chat"
   notification and brings the Gamma tab forward (the tab that opened the
   request, else the most recently used Gamma page at the address it asked
   from, else at the Connector's own).

What the worker is doing reaches the card through `POST …/watch {note,
background}` (`noteHandoff`): `looking` (no PDF link on the page yet),
`check` (the page is a bot check or CAPTCHA), `signin` (the PDF link leads to
a sign-in), `opening` (the tab opens a link), `refused` (no link gave a PDF —
the user saves it from the tab and drops it on the card), `other` (the tab
shows another paper), `closed` (the request's last tab was closed before the
PDF came; the card offers Open again). The card's **Show the tab** sends
`show`: a background tab in another window moves next to the Gamma tab that asked and comes
forward (its window restored), and counts as seen from then on. Dismiss and a
PDF dropped on the card send `close`, which releases the request's tabs and
closes the background ones the user never switched to. Every change to the bindings and the queue runs
through one serialized section (`changeHandoffs`), since several requests
open, note and deliver at once.

A **background** tab that stops on something only the user can do also
says so in a notification, once per note (`handoff.needsYouMessage` names
the host and what stopped it: a bot check, a sign-in, another paper, a
site that refused, or no PDF link yet). A click brings that tab forward
through the same `showHandoff` the card's button uses. A tab the user is on
gets none: they can look at it. This matters because the chat's reply is
waiting on that card
([ai_tools.md](ai_tools.md#walls-and-the-browser-handoff)) and an unfocused
tab among the others is easy to miss.

The chat's card follows the request on the server; the reply it belongs to
is held open until the PDF arrives, so the fetch that asked for it returns
the document text. Before the user opens the page the card asks
whether the Connector can do this (`bridge.js` → the worker's
`connector-probe`, which asks its own server about the request): `ok` when it
can, from any page, since only the request's account knows its id; to the
Connector's own server's page also why not (`signed-out`, `other-account`,
`unreachable`); to any other page nothing, so a site learns neither that the
Connector is installed nor anything about the account. No answer reads as
"not in this browser, outdated, or set to another server" on the card.
Without the Connector (or signed in as another account) the user downloads
the PDF and drops it on the card.
The desktop app opens the link in the system browser, where a Connector
signed in to the same server works the same way.

## Auth and permissions

- **Sessions, not tokens.** The extension fetches with `credentials:
  "include"`; with the `<all_urls>` host permission Chrome sends the app's
  `HttpOnly; SameSite=Lax` session cookie on extension-initiated requests, and
  signing in from the popup/options (`POST /api/login`) or from the app tab
  signs in both — one cookie jar. No CORS middleware exists or is needed.
  Verified end-to-end (Playwright, headless Chromium, plain-HTTP origin).
- The server origin is user-configured (self-hosted); `normalizeServer()`
  keeps explicit schemes and adds `http://` when missing. Plain-HTTP LAN /
  Tailscale origins work — the cookie isn't `Secure` on http.
- Options remembers normalized origins in `servers`, including the existing
  `server` on upgrade. Connecting a new address adds it; choosing a saved
  address switches immediately and refreshes the account display. Sessions
  stay in the browser's cookie jar; switching does not log out. Saving
  defaults are shared. Library lookups are scoped to their server, old badges
  are cleared on a switch, and save uploads/clip requests reject a changed
  server instead of sending the old server's document IDs to the new one.
- Saved servers appear as individual rows with a checkmark on the active
  address and an accessible remove button on every row. The popup's footer
  opens the same controls above it, in all connection states; arrow keys
  move between servers and Escape closes the switcher and returns focus.
  Removing the active address disconnects without choosing another server
  or deleting browser cookies. Open options and popup views follow changes
  to the shared server settings.
- After a successful `/api/session` check, the Connector
  remembers an HTTP-to-HTTPS redirect to the exact same host, port and API path,
  with the standard port changing from 80 to 443. The worker and options page
  use that resolved origin. Other redirect destinations are never saved.
  Publisher-session requests reject redirects so cookie snapshots
  cannot be forwarded to another server. This avoids a misleading "Failed to
  fetch" when ordinary account checks followed an HTTPS redirect successfully.
- A 401 anywhere flips the tab state to `auth: false` (badge `!`) and the
  popup shows the sign-in view. Login rate limits apply unchanged; guest
  login is not offered.
- `frame-ancestors 'self'` means the popup can't iframe the app — it doesn't.

## Testing

- `backend/tests/test_clip.py` — the endpoints with faked upstream fetches
  (dedup + folder refinement, no PDF / dead link → web-page path with
  selection + re-clip dedup, `doc_id` path, `save_copy`, lookup by arXiv
  version / DOI / web_url, the preview with faked registries + its cache,
  `norm_doi` on publisher paths, `norm_arxiv` on HTML URLs and old-style ids,
  an arXiv HTML page saving its PDF, folders, clip notes, 401s).
- `extension/tests/*.test.mjs` (`node --test extension/tests/*.test.mjs`) —
  the pure modules: `ids.js`, `publisherSessions.js` and `handoff.js`, plus
  API settings and origin guards.
- `node extension/tests/servers.e2e.mjs` — full Chromium with the unpacked
  extension and two local test servers: remembered addresses, switching,
  account/offline states, denied permission, footer keyboard navigation,
  removing/reconnecting servers, library results and saves that
  are interrupted by a server switch. Uses `frontend/`'s Playwright install.
- `node extension/tests/handoff.e2e.mjs` — the same Chromium against a fake
  Gamma server (the handoff endpoints and an app page the bridge content
  script runs on) and a fake publisher whose PDF answers when the test says:
  two requests fetched "in the background" open as unfocused tabs beside the
  Gamma tab, in its window and with it as opener, while the Gamma tab stays
  the active one; `watch` reports them as background; switching to one makes
  it the user's (`background: false`), and when both PDFs arrive the tab
  never switched to closes, the switched-to one stays open and the Gamma
  tab comes forward. Tab activity is what is checked — headless Chromium
  does not hide an inactive tab's document, so `visibilityState` says
  nothing there.
- `frontend/tests/themes.test.mjs` — `tokens.css` and `fonts/` equal their
  sources, and both pages load `theme.js`, then `tokens.css`, then
  `popup.css`.
- End-to-end recipe (not checked in): Playwright `launchPersistentContext`
  with `--load-extension=extension --headless=new` on the cached ms-playwright
  Chromium, a throwaway backend (`GAMMA_DATA_DIR`, `GAMMA_ADMIN_USER/PASSWORD`,
  `GAMMA_STATIC_DIR=frontend/dist`), the service worker driven via
  `context.serviceWorkers()[0].evaluate(...)` (set `server` in
  `chrome.storage.sync`, read `chrome.storage.session`, `chrome.action.getBadgeText`),
  the popup opened as `chrome-extension://<id>/popup.html?tab=<tabId>`. Covers
  the real arXiv abs page → save → ✓ badge → clip selection → PDF tab upload
  → background metadata. Loading an extension needs Playwright's full
  Chromium (`npx playwright install chromium`, `channel: "chromium"`); the
  headless shell the browser suite uses cannot. The chat handoff is checked
  the same way against local fake publishers: one whose PDF needs a sign-in
  cookie (no delivery before the sign-in, the PDF after it, the chat
  continuing and the Gamma tab coming forward), the Connector on `127.0.0.1`
  with the app on `localhost`, a ScienceDirect-like page (a reference's DOI
  in the text, "View PDF" rendered after load, a PDF route that answers only
  a navigation with a script redirect to another host), a bot check that
  passes by itself and one that does not (`check`, then Show the tab), an
  IEEE-like page framing its PDF behind a sign-in (`signin`, then the framed
  PDF), and four background requests delivered without a click, never more
  than three tabs at once. What a background tab does on the desktop
  (opened without the focus, closed or kept with the delivery) is the
  checked-in `handoff.e2e.mjs` above; only how a headed Chrome paints it is
  left to the eye.

## Not done yet

- Firefox build (`background.scripts` + `webextension-polyfill`), Web Store
  listing (the release zip is built and published by
  `.github/workflows/extension.yml` on every push to main touching
  `extension/`, as `extension-v<version>` with the version computed from
  the tags; `manifest.json`'s version is only the floor for minor/major
  bumps — [github_actions.md](github_actions.md)).
- Detection is client-side only for the badge; `find_page` scans every root
  page per lookup (fine for personal libraries, index it if that changes).
