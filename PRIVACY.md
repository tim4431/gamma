# Gamma privacy policy

_Last updated: 2026-10-06._

Gamma is an open-source PDF annotation and note-taking application
([github.com/tim4431/Gamma](https://github.com/tim4431/Gamma)). This
policy covers the Gamma desktop app (Windows, macOS, Linux, including the
Microsoft Store edition "Gamma PDF"), the self-hosted Gamma server, the
Gamma Connector browser extension, and Gamma Cloud: the optional account
at account.gammapdf.com and the paid plans that add a library we keep
running for you. It is published by the developer of Gamma, referred to
below as "we".

## The short version

- **The software collects nothing.** Gamma has no telemetry, analytics,
  crash reporting or advertising. No usage data is sent to the developer.
- **Your data stays where you put it.** Everything you create in Gamma
  (PDFs, highlights, notes, chats, settings) is stored in a data directory
  on your own computer, on a server that you or your organisation run, or,
  on a Gamma Cloud plan, on a server we run for you.
- **Network requests only happen for features you use**, and go to the
  services listed below. Only a Gamma Cloud account, which is optional,
  sends anything to us; what it holds is listed under "Gamma Cloud".

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

## Gamma Cloud

Gamma Cloud is optional. Nothing in this section applies until you create
an account at account.gammapdf.com; the desktop app and a server you host
work without one. The account server is open source, in the `cloud/`
folder of the repository, so what follows can be checked against the
code. The paid plans have their own [terms](TERMS.md).

**Your account.** The account server stores your e-mail address, your
username, a salted hash of your password (bcrypt), when the account was
created and confirmed, and any plan we have granted it by hand. If you
sign in with Google or GitHub, it stores the identifier that provider
gives us for you. Sign-in sessions, sign-in and reset codes, invitation
codes and the tokens issued to Gamma servers are stored as hashes of
random secrets, never in clear. Signing up and resetting a password may
show a Cloudflare Turnstile check. We keep an audit log of account events
(a sign-in, a password reset request, a plan change, a job on a server);
for sign-ins and reset requests it records the IP address the request
came from. Sign-ups and sign-in attempts are rate limited by address; the
counters live in memory and are not kept.

**Signing in to a Gamma server.** When you sign in to a Gamma server with
your Gamma Cloud account, that server receives your username, e-mail
address, display name and plan, and a token it keeps to check the sign-in
once an hour. The account server records which servers you have signed in
to (their address, name, software version and when they last checked in)
and shows them on your account's Devices page, where you can sign a server
out. It also keeps the preference profile the Gamma app syncs between
your servers: settings only, never notes, highlights or files. A Gamma
server you run yourself holds your library as before; nothing of it
reaches us.

**Plans and payment.** Payments are handled by Stripe. We never see or
store card numbers. We store your Stripe customer id, the plan, billing
period and status of your subscription, and the events Stripe sends us
about it. Invoices and the payment method are managed on Stripe's pages,
under Stripe's privacy policy.

**A Pro server.** A Pro plan gives you a Gamma server of your own, run as
a container on a host we operate. Its library, and the accounts you make
on it, are stored in that container's data directory, and you administer
the server: we have no account inside it. Our fleet agent reports counts
and sizes to the account server every five minutes (memory, disk and
storage used, the number of accounts and how many were active in the
last week, the software version and whether it is healthy), never
content, and fetches the last 200 lines of the server's log when an
operator asks for them. Copies of the server's data directory are written
to an S3-compatible storage bucket on the schedule the plan lists, as the
server's off-site backup, and are deleted with the server. When the plan
ends the server becomes read-only at once, is stopped after 30 days and
is deleted with its files and backups 90 days after the plan ended, as
the terms describe.

**The shared server.** Lite and Plus plans, and the pages a free account
publishes, live on one Gamma server we run, app.gammapdf.com. Your
library there is stored as on any Gamma server, under your account. We
administer that server, so we can technically reach what is stored on it;
we do not read it except as the terms say (to run the service, make
backups, and fix a fault you ask us about). When a Lite or Plus plan ends
the library is closed but kept; files beyond the free allowance may be
deleted 90 days later, with notice by e-mail.

**Mail.** We send mail to confirm your address, to sign in or reset a
password, to the old address when your e-mail address is changed, for
invitations, and about your plan: a failed payment, a server turning
read-only, stopping or being deleted, and changes to prices or terms.
Mail goes out through an SMTP provider, which sees the address and the
message.

**What we keep, and for how long.** Usage samples of our hosts and
servers (memory, disk, storage, counts) and resolved operator alerts are
kept for 30 days. The audit log is kept. Deleting your account, on its
Settings page, cancels any subscription at Stripe, removes the password,
the Google or GitHub links, the preference profile and the server list at
once and signs out every server; 30 days later the account's remaining
records are removed, and a Pro server still running then is deleted with
its files and backups. A library on the shared server is closed when the
account's sign-in ends and is then handled as the terms describe for a
plan that ended.

**Third parties.** Stripe (payments), Cloudflare (DNS, the proxy and
certificate in front of every request to our servers, and the Turnstile
check), the hosting provider of our servers, an S3-compatible storage
provider for the off-site copies, and an e-mail delivery provider. Each
sees what its role requires and no more. We sell or share your data with
no one else.

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
