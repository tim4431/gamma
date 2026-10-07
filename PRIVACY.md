# Gamma privacy policy

_Last updated: 2026-10-04._

Gamma is an open-source PDF annotation and note-taking application
([github.com/tim4431/Gamma](https://github.com/tim4431/Gamma)). This
policy covers the Gamma desktop app (Windows, macOS, Linux, including the
Microsoft Store edition "Gamma PDF"), the self-hosted Gamma server, and
the Gamma Connector browser extension. It is published by the developer of
Gamma, referred to below as "we".

## The short version

- **We collect nothing.** Gamma has no telemetry, analytics, crash
  reporting, advertising, or accounts hosted by us. No usage data is sent
  to the developer.
- **Your data stays where you put it.** Everything you create in Gamma
  (PDFs, highlights, notes, chats, settings) is stored in a data directory
  on your own computer, or on a server that you or your organisation run.
- **Network requests only happen for features you use**, and go to the
  services listed below, never to us.

## What Gamma stores, and where

The desktop app runs a local Gamma server on your machine. All of its
state lives in the app's data directory: your PDFs, highlights and notes,
AI chat history, preferences, and the credentials you enter for optional
services. Uninstalling the app or deleting that directory removes it. For
the Microsoft Store edition the default data location is inside the
package's virtualised AppData folder, and the launcher lets you move it
elsewhere; uninstalling the Store app deletes the default location.

If you connect the app to a remote Gamma server (for example one you run
on a home NAS), your data is stored on that server and governed by whoever
operates it. Gamma servers use their own local accounts; passwords are
stored as salted hashes. A server never contacts the developer.

## Network connections Gamma makes

Gamma talks to third-party services only when you use the feature that
needs them. What is sent is limited to what the feature requires:

- **Paper metadata and PDF lookup**: when you add a paper or fetch its
  metadata, identifiers or titles are sent to public scholarly services
  (arXiv, doi.org / Crossref / DataCite, Unpaywall, Open Library, Google
  Books) and the publisher's site that hosts the PDF.
- **Link previews**: pasting a link fetches that page's title and icon
  (icon lookups use DuckDuckGo's favicon service).
- **AI features**: entirely optional and off until you add a provider in
  Settings. When you use them, the text you select or ask about, and any
  document excerpts needed to answer, are sent to the provider you
  configured (for example Anthropic, OpenAI, or a server you host) using
  your own API key or account. Their privacy policies apply to that data.
  Your keys are stored only in your Gamma data directory. To learn each
  model's limits the server also downloads a public model list from
  models.dev; the request carries nothing about you, and
  `GAMMA_MODEL_CATALOG=off` turns it off.
- **Online search by the assistant**: when you ask the AI assistant to
  look for papers or read one from the web, your search words go to
  Crossref, arXiv and OpenAlex, and a general web search goes to your AI
  provider's own search or to a search service that you or the server's
  administrator set up (Brave Search or a SearXNG instance). The
  documents it reads are fetched from the sites that host them.
- **Translation**: when you translate a page or a selection, that text is
  sent to the translator chosen in Settings: your AI provider, Microsoft's
  translation endpoint (the default when no AI provider is set up), or
  Google Cloud Translation or Youdao with your own key.
- **External assistants (MCP), what the assistant reads**: when you approve a
  workspace in Gamma's browser sign-in flow, or create a manual token and give
  it to an MCP client, that client can read the workspace's pages, notes,
  highlights, metadata, and extracted PDF text. Retrieved content includes
  titles and page IDs returned by the optional paper picker before you select
  a paper. Selecting **Use this paper** sends its title and Gamma URL
  (including the page and workspace IDs) as a message in your assistant's
  conversation, along with any question you enter in the picker. The picker
  itself loads no third-party scripts and receives no connection tokens.
  Content is handled by the assistant and its provider under their policies.
  This does not send data to the Gamma developer or require configuring an AI
  provider inside Gamma.
- **External assistants (MCP), what Gamma stores**: only a hash of the
  connection token. Browser sign-in also stores client registration metadata
  and temporary authorization/consent records; authorization codes are stored
  as hashes and expire after two minutes. Tokens expire after 90 days and can
  be revoked in Settings → AI → Integrations.
- **Update checks**: the desktop app (non-Store installs) asks GitHub
  Releases for the latest version. The Microsoft Store edition never does
  this; the Store delivers updates. A self-hosted server asks GitHub's API
  for the latest release to show its administrators that a newer one
  exists; `GAMMA_UPDATE_CHECK=off` turns that off.
- **Browser extension (Gamma Connector)**: it keeps your server address
  and preferences in the browser's extension storage and talks only to the
  Gamma server you configured, never to us. It sends that server:
  - when you save or clip, the page's title and address, its identifiers
    (DOI, arXiv id), the PDF link or the PDF file, and any text you
    selected;
  - when your Gamma app asks it to fetch a PDF the server could not
    download (a publisher sign-in or bot check), the PDF from the page it
    opens for that in a browser tab;
  - for a publisher site you connect in its popup (optional), that site's
    cookies, so the server can download PDFs you have access to. While the
    site is connected, the cookies are sent again when you visit it and the
    server's copy is more than an hour old; this can be turned off in the
    extension's options. The server stores them encrypted, for your account
    only: session cookies for at most 24 hours, others for at most 30 days
    or until they expire. Disconnecting removes them from the server;
    uninstalling the extension does not, and full server backups keep older
    encrypted copies until those backups are deleted.

  Paper detection runs inside your browser: apart from the above, the
  pages you visit are not reported anywhere.

Each of these services receives your IP address as part of the request,
as any web request does. Gamma adds no identifiers of its own.

## Sharing

Gamma can create share links for pages. Anyone with a link can read (or,
if you allow it, edit) that page on your server. Sharing is under your
control and can be revoked at any time from the app.

## Children

Gamma is a general-purpose productivity tool and does not knowingly
collect any information from anyone, including children.

## Changes

This policy is maintained in the Gamma repository. Changes are visible in
its version history.

## Contact

Questions about this policy can be raised on the project's issue tracker:
[github.com/tim4431/Gamma/issues](https://github.com/tim4431/Gamma/issues).
