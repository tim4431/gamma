# Gamma Connector — the browser extension

A Zotero-Connector-style Chrome (Manifest V3) extension in `extension/`: one
click on a paper's landing page or PDF tab saves it into the user's library —
PDF stored, page created, folder/labels applied, metadata resolved — with
"already in your library → open" detection and a right-click clipper for links
and text selections. Server side: `gamma/routers/clip.py`. No build step
(plain ES modules, load unpacked). Users install it from the
[Chrome Web Store](https://chromewebstore.google.com/detail/gamma-connector/njpeklephhndnpakmlaancinpnabcomn);
install steps, unpacked included, in
[extension/README.md](../../extension/README.md).

## What it does

1. **Save a paper from its landing page.** arXiv abs or HTML, publisher page, DOI
   link, OpenReview… the toolbar badge shows `PDF` / `arX` / `DOI` (`?` for
   a page whose tags name no paper but whose text names one DOI: a news
   story, a page without tags). The popup shows the detected title, a
   folder and labels → **Save to Gamma** → *Open in Gamma*
   (`/?block=<id>`). The folder is typed as a path and **Tab**-completed,
   subfolder by subfolder (below, "Completing a folder path"). A page where
   nothing was detected is a web clip (item 3): the same folder and labels
   under its notice, with what of the page to keep over them, since filing
   is the same work either way. On a `?` page, *Save the web page instead*
   saves the story rather than the paper it names, as a web clip too.
2. **Save the PDF you are looking at.** The tab *is* a PDF, possibly behind
   an institutional login the server can't reach: the bytes are fetched with
   the browser's session → `POST /api/uploads` (in parts past 32 MiB:
   `/api/uploads/parts`, [api.md](api.md)) → `POST /api/clip {doc_id}`,
   automatically — browser-first on PDF tabs, and as a fallback on any page
   whose PDF the server fails to fetch (no checkbox; see the pipeline below).
3. **Clip a web page.** A page that names no paper (a blog post, a news
   story, a GitHub issue) saves as a page of its own carrying the tab as
   `web_url`, with *Save as* **Article** (the page's article as note
   blocks: headings, lists, quotes, code, tables, pictures, math),
   **Selection** (the text selected on it, likewise) or **Link** (only the
   title and the link). Below, "Web clips".
4. **Right-click**: *Save link to Gamma* (link), *Save page to Gamma* (page;
   a page naming no paper is a web clip, as last picked in the popup),
   *Clip selection to Gamma* (selection → a `> quote — [title](url)` block
   under the paper matching this tab, else under a "Web clips" note page).
   Results arrive as a notification whose click opens the page.
5. **Already in the library** — ✓ badge; the popup offers *Open in Gamma*
   and *Add to another folder…* instead of a duplicate save. A save that
   just finished shows the same block, with "Saved to your library" (plus
   the server's note, in amber, when a PDF the page named could not be
   had, or a web clip found nothing to clip), and *Undo this
   save* beside the refile, which moves that page to Recently deleted and
   brings the form back. The head already
   names the paper, so the notice doesn't repeat the title. When the
   library page's title differs from the paper the tab resolved to, the
   notice says "Already in your library as “…”", so a stale metadata record
   on that page is visible rather than shown as this paper's title.
6. **Preview before saving.** A PDF tab has no meta tags to read a title
   from, so for every detected DOI / arXiv id the worker asks
   `GET /api/library/preview` for the registry record. The popup head then
   shows the paper's title plus authors · year · venue. The previewed title
   also names the new page on save (`auto_title`, so the metadata lookup may
   still replace it).
7. **Ctrl+Shift+S** saves the current page with the default folder (a page
   naming no paper as a web clip, as last picked).
8. **Options**: saved-server switcher, server URL, sign in / out, default folder (the popup's own
   folder field) + labels, *prefer
   open-access fallback* and *keep a PDF copy* (the app's `oaFallback` /
   `pdfSaveLocal` prefs, sent as `allow_oa` / `save_copy`), and whether
   connected publisher sessions refresh automatically.
9. **Publisher sessions** — the cookie button in the popup's footer. On a
   supported journal host over HTTPS it offers to send the browser's cookies
   for that host to the signed-in Gamma account (`POST
   /api/publisher-sessions`), so the *server* can download that journal's
   PDFs later. Details below.
10. **Fetches for the AI chat.** When the chat's `fetch_paper` meets a
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
                                                         POST /api/pages/folders/ops
                                                         POST /api/clip/note
 the article (clip.js,      a web clip: the article,     POST /api/upload-image
 lib/Readability.js,        its pictures fetched and
 injected on demand)        uploaded
                                                         POST /api/uploads · GET /api/session
```

**Thin client, fat endpoint.** The extension only *detects* and *asks*; one
server call, `POST /api/clip`, runs the ingest that `openPdf` in App.jsx
orchestrates client-side (resolve → probe → cache → page → metadata), through
the same helpers the app's endpoints use. Keep the ingest logic in those
helpers — never re-implement it in the extension.

| File | Role |
|---|---|
| `manifest.json` | MV3: module service worker, `<all_urls>` content scripts (`ids.js`, `detect.js`, `bridge.js`), popup, options, `save-to-gamma` command. `host_permissions: ["<all_urls>"]` — the same install warning the content script already carries, and it makes cookie-carrying fetches to the (user-configured) server origin and the PDF-from-tab fetch work without runtime permission prompts. `scripting` (no install warning of its own) injects the web clipper when a clip starts |
| `clip.js`, `lib/Readability.js` | the web clipper, injected into the tab only when a clip starts (`chrome.scripting`; `worker.js` `clipTab`): Mozilla's Readability 0.6.0 (Apache-2.0, `lib/Readability-LICENSE.md`, vendored unchanged) and `globalThis.gammaClip(mode)`, which answers the article or the selection as a clean fragment (below, "Web clips") |
| `worker.js` | per-tab state in `chrome.storage.session` (`tab:<id>` → `{candidate, hit, preview, auth, saving, error, log, origin, ws, pageLooked}`), badge/icon, `lookup` + `preview` (and `lookupWebPage`, a page naming no paper looked up once the popup opens on it), the save pipeline (a web clip's too: `webClip`, `uploadPictures`) and its log (`log:saves`, the last five), context menus, keyboard command, notifications, the publisher-session status cache + automatic refresh (`publisher:auto`, `publisher:attempts` in session storage), the tabs fetching for the chat (`handoffs`: tab id → request, `handoff:queue`), and the message API (`get-state`, `save`, `clip-selection`, `auth-changed`, `publisher-status`, `open`, and bridge.js's `connector-hello` / `connector-probe` / `connector-tab`) |
| `handoff.js` | the chat-fetch rules: a tab's `/go` address → the request id (`handoffIdFrom`), whether the tab's paper can be the requested one (`sameWork`), which URLs to try in it (`harvestUrls`, a save's list too, with an Atypon reader's file: `readerPdfUrl`), which one to open in the tab when downloads fail (`nextToOpen`, `needsSignIn`, `signInUrl`), whether the tab shows a bot check (`checkPage`), and whether every background turn is taken (`backgroundBusy`, `MAX_BACKGROUND`, `NEEDS_YOU`) — pure, tested in `tests/` |
| `bridge.js` | content script between the Gamma app and the worker: a `connector-probe` window message gets the worker's verdict on one request (`ok` / `signed-out` / `other-account` / `unreachable`), a `connector-tab` one (`open`, `show`, `close`) the worker's answer (`opened` / `queued` / `shown` / `none` / `closed`), and a `connector-hello` (no request) answers `connector-here` to the Connector's own server's app only — how Gamma knows not to suggest the extension to a browser that has it (`shared/lib/connector.js`, [onboarding.md](onboarding.md)); nothing to a page the worker gives no answer for; a question a second per request and kind |
| `ids.js` | the identifier rules — a DOI used as a URL path (`gammaDoiFromPath`), a DOI cleaned of what a sentence or a link puts after it (`gammaCleanDoi`), the distinct DOIs a page prints or links to through doi.org (`gammaTextDois`), and the arXiv id (`gammaArxivId`) — one file loaded by the content script and imported by the worker, tested in `tests/` |
| `pageRecord.js` | the page's own citation record (`gammaPageRecord`): the Highwire `citation_*` tags, Dublin Core and PRISM filling gaps, read as Zotero's Embedded Metadata translator reads them (below, "Detection") — a classic script before `detect.js`, pure, tested in `tests/` |
| `saveLog.js` | a save's log (below, "The save log"): the header, each timed step, the detection and a download's answer as lines, the last five kept — pure, tested in `tests/` |
| `detect.js` | content script (`document_idle`): identifier extraction, re-run when a single-page app changes the address or the title (a page's title often lands after its address), reported only when the detection changed; answers `get-detection` / `get-selection` / `fetch-pdf` (downloads a PDF from inside the page and relays it base64, with the answer's status, type and size — publisher bot checks that 403 the worker's fetch accept the page's own same-origin request) |
| `api.js` | settings (`chrome.storage.sync`: `server, servers, workspaces, defaultFolders, folder, labels, allowOa, saveCopy, autoRefreshSessions, webClip`), `api()` fetch wrapper (`credentials: "include"`, the chosen workspace as `X-Gamma-Workspace`, JSON `detail` → `ApiError{status}`), `login/logout/whoAmI`, the workspace and default folder, and the folder tree (`folderTree`, `namedKid`, `makeFolder`; below) |
| `publisherSessions.js` | Publisher-host validation and the connection flow (checks the active tab and account, then sends a snapshot to Gamma with the browser's `navigator.userAgent`); the automatic-refresh rule (`shouldAutoRefresh`, `REFRESH_AFTER` / `RETRY_AFTER`) and the status text (`describeSession`) — pure, tested in `tests/` |
| `popup.html/js/css` | setup (no server) → offline (server unreachable, with Retry) → sign-in → main view; the footer shows a connection dot (green signed in / amber signed out / red unreachable) beside `host · user`, the publisher-session **cookie button** and an options gear (the app's SettingsIcon). The folder and the labels are one control (`chipField`, `ui.js`): the app's `categoryTag` chip input over a ctxMenu of suggestions — typing filters them, arrow keys + Enter pick one, Enter commits what was typed, Backspace in an empty input removes the last chip, and text typed but not committed still counts at Save. Labels take any number (a comma commits one too) and suggest the library's labels holding the text. When there are some, *New label “…”* follows for text no label has, or with nothing typed *New label…*, a name input in place; alone, either would lie over Save, which takes typed text as it is. A new label is a chip like any, which the save makes by its name. The folder holds one, since a clip files into one folder (none is the library root). With nothing typed its menu is the tree (below, "The folder tree"); typed, it suggests every other folder whose path holds the typed names in order, each within one name of the path (`phys/c` finds *Physics / Clocks*), those whose own name the last typed one begins first, and a path no folder has as *New folder “…”*, sent as `folder_path`. **Tab completes** what is being typed (below, "Completing a folder path"). A save remembers its folder (the worker, once it succeeds) but not its labels — each popup prefills only the options-page default labels. `popup.css` reads the app's design tokens and repeats `shared/styles/app.css`'s control recipes (buttons, fields, the switch, the menu surface, the focus ring) — keep those in step when the app's recipes change. The main view between the head and the footer is one `.body` on one vertical rhythm: 12 px between its groups (the workspace row, a notice, the form or the saved page, the progress line or a result, the tab's tools), the form's rows 8 apart and 12 over its button. It shows one of two halves: `#found`, the folder and labels (`#filing`) with the one action under them — *Save to Gamma*, or, under the `#nothing` notice when no paper was detected, *Save as* Article · Selection · Link (`#mode-row`, the app's Segmented) over the folder and labels and the save named after the mode (*Save article*, *Save selection*, *Save link*; below, "Web clips"), since a page with none files like any other — and `#existing`, the page once it is in the library, with *Open in Gamma* over a quiet row of *Add to another folder…* and *Undo this save* 8 px under it (below, "Undo"). *Save the web page instead*, *Clip selected text* and *Copy the save log* (whenever the tab has one) share one row of quiet links at the bottom (`.tools`), 16 px under what is above so the two rows of links don't read as one; the log link copies the tab's last save log. `?tab=<id>` targets a specific tab when opened as a page (tests) |
| `tokens.css`, `fonts/` | committed copies of the app's `shared/styles/tokens.css` and the Latin subset of Inter, like the desktop shell's ([ui-design.md](ui-design.md#the-desktop-shell-and-the-extension)): `npm run copy-tokens` in `frontend/` refreshes them, and `frontend/tests/themes.test.mjs` fails while a copy differs from its source |
| `theme.js` | a classic script in the head of both pages, before the stylesheets: the app's pinned theme isn't knowable here, so it sets `data-theme` / `data-scheme` to Light or Dark from `prefers-color-scheme`, live |
| `options.html/js` | server + host permission, account, saving defaults (the workspace picked from the signed-in account's libraries, the popup's folder field, whose choice is stored as it is made), *Copy recent saves* (the last five save logs) |
| `ui.js` | controls shared by the popup and options: the icons (mirroring `Icons.jsx`), ctxMenu rows (`menuRow`, a tree's row too), `placeMenu` (a menu opens under its field, or over it when it would not fit below and there is more room above, cut to the room it has: Chrome sizes a popup to its content, so a menu hanging past the footer would stretch the window), `menuSelect` (the app's MenuSelect over `[{value, label, icon}]`, a value no item carries falling back to the first) with `workspacePicker` (the libraries by name; it empties the folder field as a switch starts) over it, `chipField`, and `folderChipField` (the folder field: a `chipField` over the library's folders, with the tree) |
| `serverList.js` | shared saved-server rows for options and the popup footer: active checkmark, switch action and remove button, using the existing menu/close-button styles |
| `assets/icons/` | blue tile (paper detected) and grey tile (nothing) at 16/32/48/128, generated with Pillow |

## Detection

`detect.js` yields one candidate per page:

```js
{ kind: "pdf" | "arxiv" | "doi" | "maybe" | "none",
  source_url, pdf_url, arxiv_id, doi, title, is_pdf_tab,
  dois_in_text, record, pdf_links, check }
```

| Signal | Yields |
|---|---|
| `arxiv.org/abs|pdf|html/<id>` in the URL, `citation_arxiv_id` — new-style (`2310.06825`) and old-style (`cond-mat/0402216`) ids, the server's `pdf.ARXIV_ID` shape | `arxiv_id` (version stripped) |
| a DOI used as a path — `doi.org/<doi>`, `/doi/…/10.…` (Atypon, Wiley), publisher PDF paths built on it (APS `/prl/pdf/<doi>`, Springer `/content/pdf/<doi>.pdf`, IOP `/article/<doi>/pdf`; the view/file suffix stripped by `ids.js`, the same rule as the server's `norm_doi`) — then `citation_doi`, `dc.identifier`, `prism.doi`, JSON-LD `*Article` identifiers | `doi` |
| `contentType === application/pdf` / `.pdf` URL, `citation_pdf_url`, `<link rel=alternate type=application/pdf>` (a page advertising none has no `pdf_url` — never itself) | `pdf_url` |
| `citation_title`, JSON-LD headline, `dc.title`, `og:title`, `document.title` | `title` |
| the distinct DOIs in the first 30 k chars of visible text and in the page's `doi.org` links (only when nothing else matched; `gammaTextDois`, case-insensitive, at most ten) | `kind: "maybe"` with that `doi` when there is exactly one; several are a reference list or a list of publications and name none (`kind: "none"`); the count is `dois_in_text` |
| `citation_title` with `citation_author` (or `citation_authors`, else `dc.creator`), the date tags, `citation_journal_title` / `citation_conference_title` / `citation_inbook_title`, `citation_volume`, `citation_firstpage`/`lastpage`, `citation_publisher`, `citation_dissertation_institution`, `citation_technical_report_institution`, `citation_isbn` (`pageRecord.js`; none on a PDF tab) | `record`: `{title, authors, year, venue, volume, pages, publisher, isbn, kind}`, the item type by Embedded Metadata's rules (thesis, report, conference paper, chapter, journal article, book, most specific first), "Last, First" names turned around; `null` without a Highwire title and an author, so a blog post's Dublin Core makes none |

`kind` priority: pdf > arxiv > doi > maybe. A "maybe" DOI is the paper the
page is about, the way Zotero's DOI translator takes a page's one DOI: the
save sends it with `doi_from_text`, and the server finds that paper in the
library and resolves its PDF with it but confirms its record against the
PDF (below). Chrome's PDF viewer runs no
content scripts, so the worker also derives a URL-only candidate on every tab
load (`candidateFromUrl`, `from_url: true`) and merges it field-by-field under
the content script's result; URL-looking tab titles are dropped there.

A page Chrome prerenders into a tab shares the tab's id but isn't what the
tab shows. Chrome's search warmup (`www.google.com/warmup.html`, titled
"Warmup Page") and a site's speculation rules both do this. `detect.js`
neither reports nor answers while `document.prerendering` is true, and it
reports on `prerenderingchange` once the page is shown. The worker also
drops a `detected` message whose `sender.documentLifecycle` isn't `active`.
Without these checks the warmup page replaces the paper's detection, and
the popup shows "Warmup Page" with "No paper or PDF detected".

Every detection triggers `GET /api/library/lookup` (skipped when signed out)
and sets the badge: `PDF`/`arX`/`DOI` blue, `?` grey, `✓` green (in the
library), `!` red (not signed in). A detection with a DOI or arXiv id then
fetches `GET /api/library/preview` off the badge's critical path (doi.org
can take a second or two) and stores the record as `preview`. A popup that
is already open re-renders its head from `storage.onChanged`. State is
cleared when the tab navigates.

A "possible paper" heads the popup with the registry's title of the paper
its DOI names, since under the page's own title (a news story's) the
registry's authors · year · venue would read as the story's; the save names
the page by it too. *Save the web page instead*, a quiet link under the
actions, saves the tab as a web clip (the clip without the DOI, below,
"Web clips"), and the tab then reads as that page. The "nothing found"
notice says why: no paper at all, several DOIs naming none, or no PDF or
DOI but citation tags naming a work, which the web clip sends along.

The popup head otherwise shows the first of: the page's own title (meta tags), the
previewed registry title, the library page's title, the host. The chip +
identifier line says what the detection rests on, and the registry's
authors · year · venue sit under it.

## The save pipeline

Popup → `save` message → `savePaper()` in the worker (so it survives the popup
closing; progress is written to the tab state and the popup renders it):

```
urls = what the browser could download itself (browserPdfUrls)
PDF tab?  fetch bytes in the browser → %PDF check → POST /api/uploads → doc_id   (best-effort; past 32 MiB in parts, /api/uploads/parts)
POST /api/clip { source_url, pdf_url, doi, doi_from_text, arxiv_id, record, doc_id?, title, selection?, folder | folder_path,
                 labels, allow_oa, save_copy, web_fallback: false while urls are left to try }
  └─ 400? → fetch bytes in the browser → POST /api/uploads → POST /api/clip with doc_id
            browser fails too → POST /api/clip { web_fallback: true } → the page with its web source
→ { block_id, doc_id, title, existed, open_url, folders, labels, note? }
   doc_id "" = no PDF: the clip became a page with properties.web_url
```

Browser-side downloads are automatic, no checkbox. PDF tabs upload their
bytes up front (the browser already has them; the server may be paywalled
out). Any other save first lets the server try, but with `web_fallback:
false`: when the server can't fetch the PDF, `/api/clip` answers with its
400 instead of saving the page with its web source, and the worker downloads
the PDF itself, uploads it and sends the clip again with the `doc_id`. Only
when the browser fails too does a last `/api/clip` with `web_fallback: true`
save the page with its web source, its `note` saying the browser couldn't
download it either.

What the browser tries (`browserPdfUrls`, handoff.js `harvestUrls`, the
list a chat's fetch uses), only on a page that names a paper (kind `pdf`,
`arxiv` or `doi`; a "maybe" DOI is often a reference's): the tab when it is
a PDF, the advertised `pdf_url`, the file of an Atypon reader
(`readerPdfUrl`: `/doi/epdf/<doi>` → `/doi/pdf/<doi>?download=true` on
science.org, Wiley, ACS, Taylor & Francis — the reader advertises no PDF),
then the page's own PDF links on its site (`pdf_links`). Each URL is
itself two attempts per variant (`pdfUrlVariants`, which also asks Atypon's
`/doi/pdf/` for `?download=true`): the worker's direct `fetch(url,
{credentials:"include"})` first, then — publisher bot checks (science.org
& co.) 403 requests with an extension origin and no Referer — the tab's
content script via `fetch-pdf`, a same-origin fetch from the page's own
context, indistinguishable from the reader loading the PDF, relayed back
base64 (capped at 60 MB) with its status, type and size. Raw PDF tabs have
no content script, so there the direct fetch is the only (and working) path.

**The save log** (`saveLog.js`). Each save writes what it rests on (the
Connector's version, the browser, the server and workspace, the tab, the
detection) and then each step with its time since the start: every
`/api/clip` and its answer or error, every download with its status, content
type, size and where it ended, the upload's size and `doc_id`. The text is
kept in the tab's state (`log`, gone when the tab navigates) and among the
last five saves in session storage (`log:saves`, gone when the browser
closes). The popup's *Copy the save log* (at the bottom, also after the
popup was reopened) copies the tab's last one; the options page's *Copy
recent saves* copies all five, the shortcut's and the context menu's
included, since those only notify. Each step also goes to the service
worker's console as `[gamma] …`. The log carries the page's address and
title, never cookies or the PDF's contents; it only leaves the browser when
the user pastes it somewhere.

**The workspace** a save lands in is a choice per server (`workspaces`:
origin → workspace id), sent as the `X-Gamma-Workspace` header the app's
own fetch wrapper uses ([workspaces.md](workspaces.md)). `""` names no
workspace, so the server uses the account's default library. Both pickers
(the popup's, under the head over every view: the save form, "already
in …" and "no paper"; the options page's) list the workspaces
`GET /api/session` returns that this account may write to (a viewer's
would refuse the clip). The account's default is listed as `""`, so the
choice follows a later change of default. The row is hidden when there is
only one.

Everything the Connector asks of the library — the folder and label
pickers, "already in your library", the clip, the uploaded PDF bytes —
resolves against that workspace, and the link back into the app carries
`?ws=`. A save pins the workspace it started in, as it pins the server, so
its uploads and its clip cannot be split across two libraries by a switch
mid-save. Switching in the popup looks the tab up again: the ✓ badge and
"Already in …" are one library's answer. A chosen workspace the account
can no longer write to is dropped at the next session check
(`checkedWorkspace`). Saves then go to the default instead of failing with
a 403.

**The default folder** is a folder id per server and workspace
(`defaultFolders`: origin, or `origin#workspace` for a named one → id,
`""` the library root), since an id means nothing in another library. The
popup sends the folder picked; the shortcut and the context menu send the
default (`defaultFolder`). After a successful save the
worker makes that save's folder the default (`rememberFolder`). A folder
named by path (a typed new folder) is looked up in
`GET /api/library/folders` for its id. An id the library no longer lists
leaves the folder field empty, the library root; a save without the popup
checks for it (`checkedDefaultFolder`), forgets it and saves to the root.
A folder list that could not be read says nothing of that id: the field
shows a *Default folder* chip, a save goes to the id, and the default stays.

The setting `folder` is a path, the form a stored default had before
folders had ids. It belongs to the account's default workspace. While no
workspace is chosen and the server has no folder id, that path is the
default: the popup shows the folder of that path (else that path as a new
folder), the options page the folder of that path, and the
next save sends it as `folder_path` once, after which `rememberFolder`
stores the id and clears `folder`.

**The folder tree** is the folder field's menu with nothing typed, on both
surfaces (`folderChipField`). `folderTree` builds it from the list's `path`s,
keeping the list's order on each level: most recently used first, and a
folder takes its subfolders' times. A folder's parent is the folder at the
path above it. The list names no parents, and the paths of siblings sharing
a name say nothing of whose children are whose, so those show under the
first of them; that is only where a row sits, since each row files by its own
id. This needs nothing new of a server with folder ids. One from before them
(v0.2.15) lists bare paths, which read as a list that could not be read.

Only the folders above the chosen one start open, the chosen one checked and
scrolled to, and nothing is remembered between popups: the saved folder comes
back open anyway. A chevron folds a folder, a click on the row picks it. The
focus stays in the field: Up and Down move through the rows, Right opens a
folder and then goes into it, Left closes it and then goes up to its parent,
Enter picks, and `+` is the row's "+". Tab leaves for the next field, which
takes the rows and the scrolling menu out of the Tab order. A press anywhere
in the field opens the menu, the chip's × aside.

A row's "+" (shown with its highlight, since one row per folder would double
what a menu of five or six rows holds) opens a name input under that folder,
and the last row, *New folder…*, stays in sight and becomes one in place. A
folder is made there and then, not by the save: one `insert` on the folder
tree (`POST /api/pages/folders/ops`, `makeFolder`), as the app's New folder
does ([home_library.md](home_library.md) "Folders and labels"), by an id the
Connector mints. Two things decide this. Filing by `folder_path` cannot name
a parent holding `/` or one of two siblings sharing a name, where a paper
would silently land in another folder; and the options page has no Save. So
an abandoned popup can leave an empty folder, as the app's New folder can.
The name is taken whole, `/` included, and one a sibling already has (matched
as the server's `blocks_store.named` does) picks that sibling and makes
nothing. Typing a path in the field itself is unchanged: *New folder “…”* goes
as `folder_path`, made by the save.

Enter commits the name, and so does leaving the input, as in the app, since
dropping a typed name would send the paper to the folder it was meant not to
go to. A blur with the input still the active element is the window losing
the focus, and ends nothing. Escape, an empty input, using another row of
the menu or a chip's × cancels it (those two being the user's choice, which the
field then shows). Enter belonging to an
input method's composition does nothing, here and in the chip fields. Escape
is `preventDefault`ed while a menu or a name input is open, or Chrome closes
the popup with it.

The chip changes at once and the write follows, with the chip's `owed` the
write not yet known to have landed. A save carries it (`make`), and `savePaper`
sends it first, under the save's own pin: so the popup may close, as with any
save, and a switch of library can't split the folder from the clip. It goes by
the same id, an insert being create-if-absent, so a write already on its way
or a lost answer still makes one folder. Refused, the save stops with the
reason and nothing is clipped, so a paper never goes to a folder other than the
chip's. A failure before Save shows the same way (`showFailure`), the chip
stays what was asked for, and the user retries or changes it. On the options
page a choice takes effect at once, so a new folder is stored once it exists
and a refusal brings back the stored default. On both surfaces the field
empties as a switch of workspace starts (`workspacePicker`), so what it held
stays with the library it was named in and an answer that lands during one
finds another chip and does nothing. The options page also refills when the
popup picks another library, even during a refill of its own, or a folder named
there would be made in one it isn't showing. A save tells the field its folder
landed, so a late answer to the field's own write says nothing, and a folder
only the save made joins the tree.

**Completing a folder path.** Typed, the field reads as a path and completes
like a shell's or VS Code's. The matches are every folder whose path holds the
typed names, the ones whose own name the last typed one begins first — that is
the folder being named, rather than one merely holding the text above it, and
so what Tab takes. **Tab** writes that folder out in full, and, when it holds
subfolders, leaves the separator behind: the field then reads `Lab / Clocks /`,
whose menu is what is inside that folder, so Tab after Tab walks down the tree.
Nothing is committed by it — Enter still does that, and typed text left
uncommitted still counts at Save. With an empty field, or with nothing left to
write (the completion is already there), Tab leaves for the next field as it
always has. `chipField` takes the rule as `complete(row)`, so the labels, which
pass none, are unchanged. The app's page header field completes the same way
over the library tree (`libraryUtils.folderMatches` / `folderCompletion`,
[home_library.md](home_library.md) "Folders and labels").

**Undo.** A save that *created* a page (`existed: false`) can be taken back
from the popup: *Undo this save*, under *Open in Gamma*, sends `undo-save` to
the worker, which moves that page to Recently deleted (`DELETE
/api/blocks/<page>`, what the app's Delete sends) and clears the tab's `hit`,
so the form comes back with its folder and labels still set. The worker runs
it for the same reason it runs a save: the popup may close mid-way. A page
the look-up *found* is never offered — it was the user's before this tab, and
a second save into another folder only adds filing (`existed: true`), which
Undo would be the wrong word for.

## Web clips

A page that names no paper (detection's `kind: "none"`) is saved as a web
clip: a page of its own with the tab as `web_url`, and as its blocks the
part of the page the popup's *Save as* says. The survey of other clippers
behind this is [research/web-clippers.md](../research/web-clippers.md).

- **Article** — the page's article, as Firefox's Reader View finds it:
  Mozilla's Readability on a copy of the page, so navigation, sidebars,
  comments and footers stay out. The default.
- **Selection** — the text selected on the page, with its formatting. It is
  the mode picked when text is selected as the popup opens, and the button
  is disabled while nothing is (`get-selection`, detect.js).
- **Link** — only the title and the link, which is what every page naming no
  paper saved before.

The pick between Article and Link is remembered (`webClip`) and used by the
shortcut and *Save page to Gamma*. A selection is never remembered, since it
belongs to that page. *Save the web page instead* on a `?` page clips the
story the same way. *Add to another folder…* on a page already saved files
it again and clips nothing.

**In the browser** (`worker.js` `webClip`). The worker injects
`lib/Readability.js` and `clip.js` into the tab (`chrome.scripting`, only
then: Readability is 90 KB) and asks `gammaClip(mode)`. Before Readability
runs, `clip.js` writes onto the copy what only the live page knows, because
Readability would otherwise lose it:

- an image's real address: what the browser shows (`currentSrc`), unless
  that is a lazy loader's stand-in, then `data-src` and its kin, then the
  largest of a srcset;
- each formula as a `span[data-gamma-math]` with its TeX (KaTeX's
  annotation, MathJax 2's script, Wikipedia's alt text, LaTeXML's) or, for
  MathJax 3, its MathML;
- a code block's language (`language-*`, GitHub's `highlight-source-*`)
  and its text without the highlighter's spans or a line-number gutter.

A tracking pixel goes, and an emoji drawn as a picture becomes its
character. A selection keeps being a code block or a list when it lies
inside one. The answer is `{html, title, byline, site, published, images,
inline}`: the fragment, the article's title (a trailing "— Site" dropped,
which Readability keeps), the source line's parts, and the pictures, a
`data:` one swapped for a `gamma-inline:<n>` token so the fragment stays
small.

The worker then fetches each picture with the browser's session, which a
site's hotlink rules let through. It sends each to `POST /api/upload-image`,
four at a time and at most 60 per clip, keeping a PNG, JPEG, GIF, WebP or
SVG by its first bytes when the site sends no type or the wrong one. A
picture that can't be fetched or stored keeps its address and still shows
while the site serves it; the save log says which and why. An answer that
ends the save itself (signed out, another server, the server unreachable, no
room) ends it there. The clip goes as `article: {kind, html, site, byline,
published, images}`, `images` mapping a picture's address (or token) to its
stored copy. A page the browser can't read (a browser page, the Web Store) or
with nothing to clip sends `html: ""`. The server then keeps the link and its
note says why.

**On the server** (`gamma/web_clip.py`). The fragment is read with
`html.parser` into a tree and walked once into Markdown:

- headings keep their level, and lists nest by indentation;
- quotes, fenced code with its language, GFM tables, rules, figures and
  their captions keep their form, while a layout table becomes its cells'
  content;
- emphasis, links (absolute; an anchor into the page is plain text),
  `<sup>`/`<sub>` and pictures (the stored copy, else the web address,
  never a `data:` one) stay inline;
- math is `$…$` / `$$…$$`, from the TeX or through `mathml.to_latex`;
- text Markdown would read as markup is escaped (`$`, `*`, `_`, raw HTML,
  a line opening with `#`, `>`, `-` or `1.`);
- scripts, styles, form controls, inline SVG and navigation say nothing,
  and a frame is a link to it.

`markdown_import.md_to_blocks`, the .md import's parser, makes the blocks,
which nest under their headings. A first heading that only repeats the
title is dropped. A source line, `[site](url) · author · date`, comes first.
`insert_page_tree` writes the page with its tree in one transaction, the
import's bulk writer, and claims the uploads it shows (`upload_gc.claim`). A
fragment past 5 MB is a 413 before anything is filed, as is one past
`MAX_BLOCKS` blocks. Clipping a page already saved finds it by `web_url`
and only files it.

**Already clipped.** The badge's look-up runs on every page load, so it skips
a page naming no paper. Otherwise every address the browser visits would
reach the server. Once the popup opens on such a page, the worker looks it up
by its address, once per page and library (`lookupWebPage`, `pageLooked`).
The popup then says *Already in your library* rather than offering to clip it
again.

## The server's clip

Server side (`clip.py`, sync `def` — it downloads):

1. **Dedup** — `find_page()` by DOI / arXiv id / URL against every root page
   that owns its PDF in the library (a duplicated page, which only shows
   another's file, and a page reading its PDF from this computer are none):
   `properties.meta.doi|arxiv_id` (from the metadata lookup),
   `properties.web_meta.doi|arxiv_id` (what an earlier clip's page said in
   its tags, there before that clip's lookup lands and after one that
   failed; never a DOI that page only printed in its text), the
   document's address (`doc.url`, else its stored copy's, so a tab on that
   URL finds its page; `documents.model.page_document`), `web_url`, and the
   proxy-cache hash `sha256(url)[:24]` (`documents.model.url_hash`) against
   the PDF the page owns (`doc.hash`). A hit returns
   `existed: true` and still *adds* the folder/labels (soft link; a folder
   above the new one is refined away), and the clip's `web_meta` when the
   page has none; a page with no record yet gets its lookup started again.
   A "maybe" DOI (`doi_from_text`) dedups too: it names the paper the save
   is of.
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
   `doc`, a web clip's `article` as its blocks (below, "Web
   clips") or else the request's `selection` (if any) as its first
   `> quote — [title](url)` block, folder/labels applied, `doc_id: ""`, and
   a `note` in the response saying why there is no PDF when the request
   named one. A request that names no PDF at all (no `pdf_url`,
   DOI, arXiv id or uploaded bytes — *Save page to Gamma* on a blog post)
   skips the resolver and goes there directly, with no note: nothing was
   missed. Its note says only that a web clip found nothing to clip. Re-clipping the same URL finds
   that page (`find_web_page`: `web_url` match on attachment-less pages) and
   only files it / appends the new selection. Only a request with nothing at
   all (no URL, title or selection) is a 400 — and, with `web_fallback:
   false`, one whose PDF can't be had: then no page is made, and the
   extension tries the browser's own download first. With `doc_id` (uploaded bytes)
   this step is skipped; the file must exist.
4. **Page** — `blocks_store.get_or_create_doc_page()` (extracted from
   `POST /api/blocks/by-doc`) with `default_title = citation_title`, so
   `auto_title` is set and the metadata lookup may still replace it (never a
   user rename). The page's document (`properties.doc`, [api.md](api.md)
   "A page's document") is the PDF at the resolved address (`at: "url"`, its
   `hash` the address's, whether or not a copy was stored) or, from
   uploaded bytes, the stored file (`at: "store"`); its `name` is empty. The
   clip's answer `doc_id` is that `hash`. The tab URL is kept as `properties.web_url` when it differs
   from the PDF URL, and what the tab said about the paper as
   `properties.web_meta` (`metadata.web_record`: the identifiers its tags
   carried, a DOI only in its text as `text_doi`, its cleaned citation
   `record`), in the filing's op batch; both feed every later lookup and
   neither is overwritten by a later clip. The lookup and the insert run
   under the workspace's write lock (`blocks_store.write_lock`), and so do
   the web-page path's `find_web_page` and `/api/clip/note`'s "Web clips"
   page: a double-clicked Save, two tabs of one paper or a retry make one
   page.
5. **Folder + labels** — folders and labels are blocks
   ([home_library.md](home_library.md) "Folders and labels"); the page is
   filed by id in `properties.folders` / `properties.labels`. The request
   names the folder by its id (`folder`, picked from the tree; 400 when it
   is no folder) or by its names (`folder_path`, `"a/b"`: a path typed in the
   popup, *New folder “…”*, made where missing), and the labels by name (made when
   missing) — resolved and made first, through the trees' ops
   (`_filing`, `ops.ensure_filing`). Then one op batch on the page by the
   clipping account (`_file_page`), fanned out to the page's open tabs like
   any edit, also when the clip found the paper already saved. The answer's
   `folders` / `labels` are the page's filing, ids.
6. **Metadata** — `metadata.fetch_page_metadata()` (extracted from
   `/api/metadata/fetch`) in a daemon thread. It reads `web_meta` off the
   page, as does the lookup the app starts when the page is opened, which
   used to race this one without the detector's identifiers: those are
   trusted like the URL's (they come from the publisher page's own meta
   tags, so the lookup resolves them directly instead of re-mining the PDF
   text), a `text_doi` must be tied to the PDF (it is the record's title
   page, [paper_metadata.md](paper_metadata.md)), and the citation
   record stands when no registry confirms anything
   ([paper_metadata.md](paper_metadata.md)); arXiv/DOI paths need no AI
   provider. Skipped when `meta` already exists or `fetch_metadata: false`.
   A web-page clip starts it only when the page names a work
   (`web_names_a_work`: an identifier, a text DOI, or a record with a title
   and an author; the lookup needs no PDF for those — a note about a
   paywalled paper or a repository's thesis still gets its citation, a text
   DOI's record flagged unconfirmed).

Companions:

- `GET /api/library/lookup?doi=&arxiv_id=&url=` — 404 when absent.
  Identifiers are also extracted from `url` (`norm_doi` drops a publisher
  path suffix like `/pdf` or `.pdf` glued onto the DOI); web-clip pages
  match by `web_url`.
- `GET /api/library/preview?doi=&arxiv_id=&url=` — the registry record
  `{title, authors, year, venue, doi, arxiv_id, source}` from
  `metadata.registry_record` (arXiv, then doi.org; a small in-memory cache,
  the data is public). 404 when neither registry answers.
- `GET /api/library/folders` → `{folders: [{id, path}], labels: [{id,
  name}]}` — every folder (`path` its names from the top; empty folders
  too) and every label. Folders rank by the account/workspace's latest
  `recent-views` page timestamp, then latest page modification, with names
  breaking ties; a folder takes the times of the pages filed in it and in
  its subfolders, a page inside another page counting for its top's
  folders (`blocks_store.filed_folders`' rule). Labels stay alphabetical.
- `POST /api/clip/note {text, source_url, title,
page_id?}` — the explicit "clip selection INTO a page" append path (with
`generate_key_between`; without `page_id` it uses/creates the root page
flagged `properties.web_clips = 1`), as opposed to `/api/clip`'s "make a page
of this tab". All session-only, and all in the workspace the request
names (`X-Gamma-Workspace`, above), else the account's default
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
- **The desktop app's local servers.** A local server of the desktop app is
  `http://127.0.0.1:<port>`, a plain-HTTP loopback origin the Connector
  treats like any other. The publisher-session check counts it as secure.
  The shell keeps each local server on the port it had last time, so the
  address saved in the Connector keeps working across launches. The bar
  menu and the launcher card show the address
  ([desktop architecture](../../desktop/docs/architecture.md) "A local
  server's address"). The Connector signs in with the server's seeded admin
  (the card's *Show credentials*). The window's own session is in
  Electron's cookie jar, not the browser's, and the Connector reaches the
  server only while the app runs it. Cookies are scoped to the host, not
  the port, so in one browser every Gamma on `127.0.0.1` shares one
  `session` cookie: signing in to the desktop server signs that browser out
  of another Gamma on `127.0.0.1:<other port>`, whose open tabs then get the
  409 session mismatch. `http://localhost:<port>` reaches the same sidecar
  with a cookie of its own (checked in Chromium).
- A 401 anywhere flips the tab state to `auth: false` (badge `!`) and the
  popup shows the sign-in view. Login rate limits apply unchanged; guest
  login is not offered.
- `frame-ancestors 'self'` means the popup can't iframe the app — it doesn't.

## Testing

- `backend/tests/test_workspaces.py` — that the header the Connector sends
  picks the library a clip lands in, and that a viewer is refused it.
- `backend/tests/test_clip.py` — the endpoints with faked upstream fetches
  (dedup + folder refinement, no PDF / dead link → web-page path with
  selection + re-clip dedup, `doc_id` path, `save_copy`, lookup by arXiv
  version / DOI / web_url, the preview with faked registries + its cache,
  `norm_doi` on publisher paths, `norm_arxiv` on HTML URLs and old-style ids,
  an arXiv HTML page saving its PDF, `web_meta` kept cleaned and never
  overwritten, a text DOI that finds the paper but is kept as `text_doi`,
  a web page whose record names a work starting its lookup, an existing
  page without a record gaining one, a web clip's article as the page's
  blocks under their headings with its uploaded picture and its source
  line, a clip that found nothing keeping the link and saying so, the 413
  of a fragment too large, folders, clip notes, 401s).
- `backend/tests/test_web_clip.py` — `gamma/web_clip.py`: blocks keeping
  their shape (headings, nested lists, quotes, code with its language,
  tables and layout tables, figures), links and pictures (absolute, an
  anchor as text, the stored copy, a tracking pixel and a `data:` picture
  dropped), math from TeX, MathML and Wikipedia's alt text, the escaping,
  what says nothing, and the tree under its source line.
- `extension/tests/*.test.mjs` (`node --test extension/tests/*.test.mjs`) —
  the pure modules: `ids.js`, `pageRecord.js`, `publisherSessions.js`,
  `handoff.js` and `saveLog.js`, plus
  API settings, the default folder, folder paths, the folder tree, origin guards, and the
  chosen workspace (the header, a save's pin, a default folder per
  workspace, forgetting one the account can no longer write to). The
  `check` workflow runs them on every PR ([github_actions.md](github_actions.md#checkyml));
  the `.e2e.mjs` files below are run by hand, headless unless `--headed`.
- `node extension/tests/folders.e2e.mjs` — full Chromium against a fake
  server answering `/api/library/folders`, `/api/clip` and
  `/api/pages/folders/ops`, the last of which can hold its answer or refuse:
  both surfaces show the tree open above the chosen folder and the folder
  field suggests folders as you type (name by name), a save sends the picked
  or typed folder's id, a typed new folder's path and label names, Backspace
  leaves the library root, the default is remembered by id per server, a path
  stored by an older version is sent once and replaced by its id, and in a
  window sized like the popup a long menu opens on the side with more room
  and stays inside it. The tree: chevrons pick nothing, the keys walk it, a
  name input's op names its parent by id (one holding `/`, the second of two
  of a name) and a save files by the new id; a sibling's name makes nothing,
  and neither does Escape, an empty input, another row or the window's blur;
  a composing Enter does nothing; Tab with nothing typed reaches the labels,
  and typed it completes — the first match written out in full, the menu then
  listing what is inside it, the highlighted row next, and the field left when
  there is nothing more to write; leaving the input
  commits; a chip's × cancels a name; a save waits for a held op, also with
  the popup closed, and sends no clip for a refused one, sending the same id
  again; an unread list, or one of bare paths, keeps the default; the options
  page stores a new folder once it exists; a stored path no folder has can be
  cleared; a label named in place is a chip the save makes, and a label no one
  has leaves Save uncovered. Escape is the one thing a popup opened as a tab cannot
  show: Chrome closing the real popup on an Escape left unhandled was checked
  by hand over DevTools (`chrome.action.openPopup`), as Playwright does not
  surface that target.
- `node extension/tests/save.e2e.mjs` — full Chromium against a fake
  server whose `/api/clip` answers a clip without bytes with the paywall
  400 when `web_fallback` is off, and a fake Atypon publisher whose ePDF
  reader advertises no PDF and whose `/doi/pdf/…?download=true` answers only
  a same-origin fetch: the popup's save gets the 400, the worker's fetch is
  refused, the page's own fetch is answered, the bytes are uploaded and the
  clip sent again with the `doc_id`; a paper the browser can't get either is
  saved with its web source and a note saying so; the popup's *Copy the save
  log* copies the tab's log (which names each of those steps) and the options
  page's *Copy recent saves* both, newest first.
- `node extension/tests/detect.e2e.mjs` — full Chromium against a fake
  site and a fake server: a news story whose one DOI is in its text and its
  doi.org link is a `?` "possible paper", headed with the previewed paper's
  title and saved with `doi_from_text`; another story saved as the web page
  instead (no DOI sent, the tab then reads as the page); a list of three
  DOIs names none and says so; a repository's thesis page with tags but no
  PDF or DOI shows the folder and labels under its notice, files by them and
  sends its record through the web clip's save, and *Undo this save*
  deletes the page it made and brings that form back; a publisher
  page's record rides along with its own DOI, not one it cites.
- `node extension/tests/webclip.e2e.mjs` — full Chromium against a fake
  blog and a fake server: on an article the popup offers *Save as* with
  Article picked and Selection disabled, and *Save article* sends the
  article — the lazy picture by its real address, KaTeX's TeX as a formula,
  the code block's language, the page's navigation and footer left out, the
  title without the site's name — with the picture fetched and uploaded as
  a PNG and the one the site refuses keeping its address (the save log says
  so); the saved page's links sit 8 px under its button and the tab's tools
  16 px further; text selected on a page picks Selection, which sends only
  that; Link sends no article and is the mode the next page opens with; a
  page clipped before is not looked up as it loads, the popup finds it once
  it opens, and filing it again clips nothing.
- `node extension/tests/prerender.e2e.mjs` — a fake paper page whose
  speculation rules prerender a "Warmup Page" with its own DOI. While the
  warmup page is hidden, the tab's detection, the page's `get-detection`
  answers and the popup's head stay the paper's. When the paper navigates
  to it, the shown page reports its own DOI. Chromium never prerenders in a
  tab Playwright is attached to (`PrerenderingDisabledByDevTools`), so this
  test launches Playwright's Chromium itself. It uses DevTools only on the
  extension's worker and popup, and waits for the worker's `chrome` before
  calling it: a worker just started is listed a moment before Chrome gives it
  the extension API.
- `node extension/tests/workspaces.e2e.mjs` — full Chromium against a fake
  server whose `/api/session` lists three libraries (one read-only): both
  pickers offer the two writable ones, picking one stores it and refills
  the folder field from that library, a folder still being made across a
  switch moves neither library's default, a save pressed in one library
  stays there through a switch, an options page left open follows the popup's
  choice, saves carry `X-Gamma-Workspace` and
  the folder remembered for that workspace, the popup names the library
  holding an already-saved paper, keeps the picker over that notice and
  looks the paper up again on a switch, the
  link back into the app carries `?ws=`, a workspace the account is demoted
  in is forgotten at the next session check, and a single-library account
  sees no row.
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

- Firefox build (`background.scripts` + `webextension-polyfill`). Chrome
  is released: `.github/workflows/desktop.yml` zips
  `gamma-connector-<version>.zip` with the release's version in its
  manifest and `chrome-store.yml` submits it to the Chrome Web Store
  ([extension/README.md](../../extension/README.md),
  [extension/STORE.md](../../extension/STORE.md)).
- Detection is client-side only for the badge; `find_page` scans every root
  page per lookup (fine for personal libraries, index it if that changes).
