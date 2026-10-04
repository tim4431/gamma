# Publishing to the Chrome Web Store

Each release's `gamma-connector-<version>.zip` (built by `desktop.yml`)
goes to the store on its own through `.github/workflows/chrome-store.yml`
(Chrome Web Store API V2). Two things stay manual and happen once: creating
the store item, which the API cannot do, and letting GitHub Actions sign in
as your publisher. Google reviews every version (usually 1–3 days, longer
for `<all_urls>` extensions).

## 1. Create the store item (once, by hand)

1. Register at <https://chrome.google.com/webstore/devconsole>: pay the
   US$5 fee, verify the contact e-mail, and turn on 2-Step Verification on
   the Google account (the dashboard requires it to publish).
2. Take the zip from the newest release
   (<https://github.com/tim4431/Gamma/releases/latest>). Or build it
   locally: `cd extension && zip -r ../gamma-connector.zip . -x STORE.md README.md "store/*"`.
3. Dashboard → **New item** → upload the zip.
4. **Store listing**: the copy below, category **Productivity**, language
   English, and the images (all opaque PNGs, as the store requires):

   | Field | File |
   |---|---|
   | Store icon, 128×128 | `assets/icons/icon128.png` |
   | Screenshots, 1280×800 | `store/screenshot-1-paper.png`, `store/screenshot-2-library.png`, `store/screenshot-3-search.png` |
   | Small promo tile, 440×280 | `store/promo-440x280.png` |
   | Marquee, 1400×560 (optional) | `store/marquee-1400x560.png` |

   The promo tile and marquee come from the brand build
   (`tools/branding/store-layouts.mjs`, `node tools/branding/build.mjs`).
   The screenshots are Gamma itself: the curated demo library on an
   isolated server, shot by `node tools/readme-media/shoot-store.mjs`
   (setup in [tools/readme-media/README.md](../tools/readme-media/README.md)).
   `store/` stays out of the release zip.
5. **Privacy**: single purpose and permission justifications (below);
   disclose *website content* and *authentication information*, certify
   "not sold, not used for unrelated purposes"; privacy policy
   <https://gammapdf.com/privacy> ([PRIVACY.md](../PRIVACY.md)).
6. **Distribution**: **Public**, or **Unlisted** for an install link and
   auto-updates without a searchable listing. The API cannot change this
   later; only the dashboard can.
7. **Submit for review.** Note the item's 32-letter ID (on its dashboard
   page and in the store URL) and the **Publisher ID** (the dashboard's
   publisher settings / Account page).

## 2. Let GitHub Actions publish (once)

The workflow signs in without a stored key: GitHub's OIDC token is
exchanged for an access token of a Google Cloud service account (Workload
Identity Federation), and that service account is linked to the publisher.
In [Google Cloud Shell](https://console.cloud.google.com/?cloudshell=true)
(or any shell with `gcloud`), with a project ID of your choice (IDs are
global, so pick an unused one). Two things learned on the first setup:
being the project's owner is not enough to create a workload identity
pool (step 2 grants *Workload Identity Pool Admin*), and a pool just
created can still answer `describe` with NOT_FOUND, so its full name is
built from the project number instead. Each `&&` stops the chain at the
first failure, so nothing runs with an empty value.

```bash
PROJECT_ID=gamma-cws-$RANDOM   # write it down
REPO=tim4431/gamma             # GitHub's canonical name, lowercase: the OIDC token carries it and the condition is case-sensitive
# 1. The project, its APIs and the service account.
gcloud projects create "$PROJECT_ID" &&
gcloud config set project "$PROJECT_ID" &&
gcloud services enable chromewebstore.googleapis.com iamcredentials.googleapis.com sts.googleapis.com &&
gcloud iam service-accounts create cws-publisher --display-name="Gamma Connector publisher" &&
# 2. The right to create the pool; wait about a minute after this.
gcloud projects add-iam-policy-binding "$PROJECT_ID" \
  --member="user:$(gcloud config get-value account)" \
  --role=roles/iam.workloadIdentityPoolAdmin --condition=None
```

```bash
# 3. The pool, its GitHub provider, and the service account opened to this repository.
PROJECT_NUMBER=$(gcloud projects describe "$PROJECT_ID" --format='value(projectNumber)') &&
POOL="projects/$PROJECT_NUMBER/locations/global/workloadIdentityPools/github" &&
gcloud iam workload-identity-pools create github --location=global --display-name="GitHub Actions" &&
gcloud iam workload-identity-pools providers create-oidc gamma \
  --location=global --workload-identity-pool=github \
  --issuer-uri="https://token.actions.githubusercontent.com" \
  --attribute-mapping="google.subject=assertion.sub,attribute.repository=assertion.repository" \
  --attribute-condition="assertion.repository == '$REPO'" &&
gcloud iam service-accounts add-iam-policy-binding "cws-publisher@$PROJECT_ID.iam.gserviceaccount.com" \
  --role=roles/iam.workloadIdentityUser \
  --member="principalSet://iam.googleapis.com/$POOL/attribute.repository/$REPO" &&
echo "CWS_SERVICE_ACCOUNT = cws-publisher@$PROJECT_ID.iam.gserviceaccount.com" &&
echo "CWS_WORKLOAD_IDENTITY_PROVIDER = $POOL/providers/gamma"
```

Then:

1. Developer Dashboard → **Account** → **Service account**: add
   `cws-publisher@<project>.iam.gserviceaccount.com` (one per publisher).
2. Set the four repository variables. They are variables, not secrets, since none of them is a
   credential:

   ```bash
   gh variable set CWS_PUBLISHER_ID --repo tim4431/gamma --body "<publisher id>"
   gh variable set CWS_ITEM_ID --repo tim4431/gamma --body "<item id>"
   gh variable set CWS_SERVICE_ACCOUNT --repo tim4431/gamma --body "cws-publisher@<project>.iam.gserviceaccount.com"
   gh variable set CWS_WORKLOAD_IDENTITY_PROVIDER --repo tim4431/gamma --body "projects/<number>/locations/global/workloadIdentityPools/github/providers/gamma"
   ```

3. Check the sign-in: `gh workflow run chrome-store.yml --repo tim4431/gamma --ref main -f tag=<a release with a gamma-connector zip> -f publish=false`
   (a blank tag means the latest release, which needs that zip). While a
   version is in review the run signs in, reads the item's status, and ends
   green with "The store takes no new package now (You may not edit or
   publish an item that is in review.)", which proves the chain works. After
   the review it uploads a draft without submitting it.

## 3. Every release

`desktop.yml`'s publish job dispatches `chrome-store.yml` with the new tag
(pre-releases excluded). It uploads the zip, waits for the store to
process it, and submits it for review; the run's summary shows the
resulting state (`PENDING_REVIEW`, usually). While an earlier version is
still in review the store accepts no new package: the run skips with a
notice and never cancels the review; the next release, or
`gh workflow run chrome-store.yml --repo tim4431/gamma --ref main` (latest release) once the
review is over, carries the changes. Each release's zip has a new version,
so the store never sees a re-used one.

Store installs get a store-assigned extension ID, so a browser that had
the unpacked Connector loaded keeps both until the unpacked one is
removed; settings are per install (enter the server address again).

## Listing copy

**Name:** Gamma Connector

**Summary (132 chars max):**
Save papers and PDFs into your self-hosted Gamma library with one click — like the Zotero Connector, for Gamma.

**Description:**

Gamma Connector is the browser companion for Gamma, the self-hosted PDF
annotation and note server. On a paper's landing page (arXiv, a journal, a
DOI link, OpenReview…) or a PDF tab, the toolbar icon lights up; one click
saves the paper into your Gamma library: the PDF is stored, a note page is
created, it's filed into the folder you pick with your labels, and the
metadata (title, authors, venue, DOI) is resolved automatically.

- Detects papers via citation meta tags, arXiv and DOI links, and JSON-LD.
- Shows ✓ when the paper is already in your library and opens it instead of
  making a duplicate.
- Behind a paywall your browser can see through (institutional login)?
  The bytes your browser already has are uploaded automatically.
- Right-click a link, a page, or selected text: Save to Gamma / Clip to Gamma
  (a quoted block with its source link).
- Ctrl+Shift+S saves the current page.

You need your own Gamma server (github.com/tim4431/gamma). The extension
talks only to the server address you enter; nothing is sent anywhere else.

## Privacy tab

Paste these into the dashboard's Privacy tab; each matches what the code
does (`worker.js`, `publisherSessions.js`, `detect.js`).

**Single purpose:** Save research papers, web pages and PDFs from the
browser into the user's own self-hosted Gamma library.

**Permission justifications**

| Permission | Justification |
|---|---|
| `activeTab` | When the user limits the extension's site access to "on click", activeTab lets a click on the toolbar button, the context menu or the keyboard shortcut read the current page so it can be saved to the user's Gamma server. |
| `contextMenus` | Adds "Save link to Gamma", "Save page to Gamma" and "Clip selection to Gamma" to the right-click menu. |
| `cookies` (optional) | Requested only when the user clicks Connect in the popup's publisher-session drawer. It reads the cookies of that one publisher host and sends them to the user's own Gamma server, which uses them to download PDFs the user has access to. After a host is connected, its cookies are re-sent when the user visits that host and the server's copy is more than an hour old; this can be turned off in the options. Hosts the user never connected are never read. |
| `notifications` | Shows the result of a save started from the context menu or the keyboard shortcut, when no popup is open. |
| `storage` | Stores the server address, the saving defaults (folder, labels) and per-tab detection state. |
| `tabs` | Reads each tab's URL and title to detect papers and show the toolbar badge, and opens, focuses and closes tabs when the user's Gamma app asks the extension to fetch a PDF that the server could not download itself (a publisher sign-in or bot check), so the user's own browser session can download it. |
| Host permission (`<all_urls>`) | The Gamma server is self-hosted at an address only the user knows (home network, VPN or personal domain), so it cannot be listed in the manifest. The content script detects papers (DOI, arXiv id, PDF link) on any site, and saving downloads the PDF with the user's own browser session, including on paywalled publisher sites the user can access. Page data goes only to the user's Gamma server, only when they save or when their Gamma app asks for a PDF. |

**Remote code:** No, I am not using remote code. All code ships in the
package.

**Data usage:** tick *Website content* (page title, URL, DOI/arXiv id,
selected text, PDF files) and *Authentication information* (the cookies
of publisher hosts the user connected), and certify all three statements.
Everything goes only to the Gamma server the user configured: on a save,
on a request from the user's own Gamma app, or, for a publisher the user
connected by hand, on a visit to that site while the server's copy of its
cookies is over an hour old. Nothing is sent to the developer or any third
party. No analytics.

## Privacy policy

<https://gammapdf.com/privacy>, the site's copy of
[PRIVACY.md](../PRIVACY.md); its *Browser extension* entry covers the
Connector. Keep it in step with the Data usage answers above: reviewers
compare the two with the code.
