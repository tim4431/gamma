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
   locally: `cd extension && zip -r ../gamma-connector.zip . -x STORE.md README.md`.
3. Dashboard → **New item** → upload the zip.
4. **Store listing**: the copy below, category **Productivity**, language
   English, and the required images: the 128×128 icon is in the zip, plus
   a 440×280 small promo tile and at least one 1280×800 (or 640×400)
   screenshot (the popup on an arXiv page, the popup on a PDF tab, the
   options page).
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
(or any shell with `gcloud`), with a project ID of your choice:

```bash
PROJECT_ID=gamma-connector-publish        # new or existing project
REPO=tim4431/Gamma                        # exactly as GitHub spells it
gcloud projects create "$PROJECT_ID"      # skip for an existing project
gcloud config set project "$PROJECT_ID"
gcloud services enable chromewebstore.googleapis.com iamcredentials.googleapis.com sts.googleapis.com
gcloud iam service-accounts create cws-publisher --display-name="Gamma Connector publisher"
gcloud iam workload-identity-pools create github --location=global --display-name="GitHub Actions"
gcloud iam workload-identity-pools providers create-oidc gamma \
  --location=global --workload-identity-pool=github \
  --issuer-uri="https://token.actions.githubusercontent.com" \
  --attribute-mapping="google.subject=assertion.sub,attribute.repository=assertion.repository" \
  --attribute-condition="assertion.repository == '$REPO'"
POOL=$(gcloud iam workload-identity-pools describe github --location=global --format='value(name)')
gcloud iam service-accounts add-iam-policy-binding "cws-publisher@$PROJECT_ID.iam.gserviceaccount.com" \
  --role=roles/iam.workloadIdentityUser \
  --member="principalSet://iam.googleapis.com/$POOL/attribute.repository/$REPO"
gcloud iam workload-identity-pools providers describe gamma \
  --location=global --workload-identity-pool=github --format='value(name)'   # → CWS_WORKLOAD_IDENTITY_PROVIDER
```

Then:

1. Developer Dashboard → **Account** → **Service account**: add
   `cws-publisher@<project>.iam.gserviceaccount.com` (one per publisher).
2. Set the four repository variables. They are variables, not secrets, since none of them is a
   credential:

   ```bash
   gh variable set CWS_PUBLISHER_ID --body "<publisher id>"
   gh variable set CWS_ITEM_ID --body "<item id>"
   gh variable set CWS_SERVICE_ACCOUNT --body "cws-publisher@<project>.iam.gserviceaccount.com"
   gh variable set CWS_WORKLOAD_IDENTITY_PROVIDER --body "projects/<number>/locations/global/workloadIdentityPools/github/providers/gamma"
   ```

3. Check the sign-in: `gh workflow run chrome-store.yml --ref main -f publish=false`.
   While the first version is in review this reads the item's status and
   stops with "still in review", which proves the chain works. After that it
   uploads a draft without submitting it.

## 3. Every release

`desktop.yml`'s publish job dispatches `chrome-store.yml` with the new tag
(pre-releases excluded). It uploads the zip, waits for the store to
process it, and submits it for review; the run's summary shows the
resulting state (`PENDING_REVIEW`, usually). While an earlier version is
still in review the store accepts no new package: the run skips with a
notice and never cancels the review; the next release, or
`gh workflow run chrome-store.yml --ref main` (latest release) once the
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

**Single purpose:** Save web pages, papers and PDFs into the user's own
Gamma server.

**Permission justifications**

| Permission | Justification |
|---|---|
| `host_permissions: <all_urls>` | The user's Gamma server is self-hosted at an address only they know (LAN, Tailscale, or a personal domain), so it cannot be listed in the manifest; the same permission lets the content script detect papers on any site and lets the save flow fetch a paywalled PDF with the user's own browser session. Page data is only sent, to the user's server, when the user clicks Save. |
| `storage` | Remembers the server address, the default folder/labels, and per-tab detection state. |
| `contextMenus` | "Save link / page / selection to Gamma" items. |
| `activeTab`, `tabs` | Read the current tab's URL/title for detection and the badge. |
| `notifications` | Result of a context-menu or keyboard-shortcut save when no popup is open. |
| Optional `cookies` | Requested only when the user clicks Connect / Refresh in the popup's publisher-session drawer. Reads applicable cookies for the selected publisher host and sends a snapshot to the displayed Gamma server/account for later PDF downloads. Once a host has been connected this way, the Connector re-sends that host's cookies when the user visits it and the server's copy is over an hour old (can be turned off in the options); hosts the user never connected are never read. Normal saves do not read or transfer cookie values. |

**Data usage:** website content (page title, DOI/arXiv id, PDF URL, selected
text, the PDF file when the user chooses to upload it) is transmitted only to
the Gamma server the user configured, only on the user's explicit action, and
is not sold, shared, or used for any other purpose. No analytics, no third
parties. If the user explicitly connects a publisher session, authentication
cookies for that publisher host are also transferred to their configured server
over HTTPS (or localhost), encrypted there, and reused only for that account's
PDF requests. The user can refresh or disconnect the session in the popup.

**Remote code:** none — all code ships in the package.

## Privacy policy (host it and link it)

Gamma Connector stores your server address and preferences in your browser's
extension storage. When you click Save or Clip, it sends the current page's
title, identifiers (DOI / arXiv id), PDF link or PDF file, and any text you
selected to the Gamma server address you configured, and nowhere else. It
does not collect analytics, does not use third-party services, and does not
transmit anything without your action. Removing the extension deletes its local
settings. Publisher cookie snapshots explicitly connected to
Gamma remain on that server until disconnected or expired; uninstalling the
extension does not revoke them. Session cookies expire within 24 hours and
persistent cookies within 30 days or their original expiry, whichever is sooner.
Use Publisher sessions → Disconnect to remove a live server snapshot. Full server
backups may retain encrypted older snapshots until those backups are removed.
