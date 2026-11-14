# HTTPS for a NAS

How Gamma behaves when one of its servers is reached over HTTPS: a NAS
behind a TLS proxy, a Tailscale name, a self-signed certificate. Three
clients talk to such a server: the desktop window (Chromium), the shell's
own requests (`session.fetch`), and the desktop app's local server, the
sidecar, which keeps the synced vaults, the computer's channel and the
offline copies over urllib. This page is where all three meet: which
certificates each trusts, why no Gamma-to-Gamma request follows a
redirect, how a server's address changes without losing its links, when
Gamma sends HSTS, and where plain http is still allowed. The survey and
the critiques behind it are in the research note
[https-nas.md](../research/https-nas.md).

| Side | Code |
|---|---|
| the trust store, the pins, the pinned connection's context | `gamma/tls_trust.py` |
| the pinned connection and the peek | `gamma/net_proxy.py` (`_HTTPSConnection`, `RoutedHTTPSHandler`, `install`) |
| redirects refused, failures named, status fields | `gamma/gamma_sync.py` (`opener`, `Redirected`, `Blocked`, `CertificateRefused`, `NotHttps`, `blocked`, `status_fields`) |
| where a host is | `gamma/host_scope.py`, `desktop/lib/hostScope.js` |
| HSTS | `gamma/auth.py` (`_hsts`) |
| the address move, the plain-http rule | `gamma/remote_servers.py`, `gamma/routers/remote_servers.py` |
| the shell's verify proc, the certificate card, the pins it keeps | `desktop/lib/certs.js`, `desktop/main.js`, `desktop/ui/launcher.html` ([desktop.md](desktop.md)) |
| users.db | `server_addresses`, `tls_pins` ([user_db.md](user_db.md)) |

## Setting up a NAS

- **At the root of its own host name.** Gamma is served at `/` of a
  name of its own (a subdomain such as `gamma.example.com`), never under
  a path: the Public URL refuses one (`server_settings.validate_public_url`)
  and the web app calls an absolute `/api`. A link stored with a path
  keeps it, and nothing else handles sub-paths.
- **A certificate every client trusts.** A public authority (Let's
  Encrypt, Synology's own) passes the window, the sidecar on every
  platform, Android and the iPad, provided the server sends the **full
  chain** (`fullchain.pem`; Synology's import takes the intermediate as
  its own file). A leaf sent alone works in Chromium, which fetches the
  intermediate, and on Windows and macOS, where the system verifies; it
  fails on Linux (a Docker NAS reaching another), in the mobile apps and
  in the CLI there. Tailscale's `tailscale cert` and `tailscale serve`
  give a `*.ts.net` certificate that passes every client.
- **Your own authority** works once it is in the operating system's
  store: the sidecar now verifies against that store too (below).
- **A self-signed certificate** works in the desktop app by its
  fingerprint ("Pinned certificates").
- **The proxy in front** passes WebSocket upgrades on `/api/ws/` (the
  README's reverse-proxy paragraph), and keeps an idle connection open
  longer than 25 s: the computer's poll (`/api/computers/poll`) is held
  up to `registry.POLL_S`. nginx and Synology (60 s) and Cloudflare (100 s)
  do. Set `FORWARDED_ALLOW_IPS` to the proxy's address and the Public URL
  to the https address, for the links Gamma writes ([api.md](api.md)
  "Auth model"); HSTS does not depend on either.

## Rules

- **R1. A token goes only to the address it was made for, over a
  connection that is that address.** No client of Gamma-to-Gamma traffic
  follows a redirect, the sidecar's or the shell's.
- **R2. Nothing a server writes becomes an address or a trust decision
  without the person.** A `Location`, a certificate's subject, a status
  field are shown, never acted on, except the one automatic move (the
  same host name, http to https, a certificate there that verifies).
- **R3. Identity is exact.** The shell treats a view as a given NAS only
  when its origin equals the link's stored origin.
- **R4. Server-written strings are text** in the launcher and the bar
  (`textContent`), capped where they are made: 500 characters in either
  `describe`, 300 for a redirect's address.
- **R5. users.db and the shell's registry file are inside the trust
  boundary.** Whatever can write them (the same OS user, a local agent)
  can already read every token there, so the pins they hold add no new
  exposure, and the pins are not cleared at start: a round that runs
  before the shell's push uses the last list.

## One trust store

`tls_trust.use_system_store()` injects [truststore](https://truststore.readthedocs.io/)
into the `ssl` module, so every outbound HTTPS connection of the server
verifies against the operating system's store: Windows' CryptoAPI (with
its fetching of a missing intermediate), macOS' Security framework (the
Keychain), Linux's OpenSSL paths. The server then trusts what the
window trusts. `net_proxy.install()` calls it first, before it installs
the routed opener and sets gamma_sync's (`gamma_sync.make_opener`), so
`create_app` and `manage.py` both get it, after app.py's certifi
fallback (`_verify_https_with_certifi_when_no_cas`). Its answer:

| Answer | When |
|---|---|
| `system` | injected, logged once: `[startup] verifying HTTPS with the system's trust store` |
| `certifi` | iOS and Android: nothing injected, the apps verify with certifi's bundle |
| `off` | `GAMMA_TRUSTSTORE=0`, an escape hatch for a truststore bug; read at start, so changing it takes a restart |
| `missing` | truststore not installed (a source checkout that predates it) |

Every client takes it: the routed handlers make their context at their
first https request, after the injection; the Zotero stream's
`ssl.create_default_context` builds truststore's class; boto3 is imported
when a copy first runs, and truststore patches urllib3's context too. On
Windows and macOS an `AWS_CA_BUNDLE` is then **added to** the system's
store rather than used alone; on Linux nothing changes.

- **truststore is `backend/requirements.txt`'s** (pinned), frozen into
  the desktop app with `--collect-submodules truststore`
  (`desktop/build_backend.py`: it picks its platform's module at
  import), and left out of `requirements-mobile.txt`
  (`tests/test_mobile_requirements.py` says why).
- **No server context after the injection.** A server-side context of
  truststore's class verifies the client's empty chain and fails every
  handshake. uvicorn builds its context in `Config.load()`, before it
  imports the app, so `uvicorn app:app --ssl-certfile …` and a string
  import (`uvicorn.run("app:app", ssl_certfile=…)`, as the browser tests'
  harness runs a TLS server) work, and `uvicorn.run(app_object, ssl_…)`
  after importing gamma does not.
- **A context of the plain class** (`tls_trust._PLAIN_CONTEXT`, the
  `ssl.SSLContext` the `ssl` module defines, found in the class's MRO) is
  what the pinned path and the tests use. Its own setters
  (`verify_mode`, `minimum_version`, `verify_flags`, `options`) name
  `ssl.SSLContext`, which is then truststore's, and call themselves
  without end: set them through `tls_trust.plain_context(protocol,
  **values)`, which goes through the base class.
- **The CLI.** `gamma_sync.py` stays standard library only; run as a
  script it injects truststore when it is installed (a guarded import,
  the only one `tests/test_gamma_sync_errors.py` allows), so a Mac user
  with a Keychain root is served as well.
- **`VERIFY_X509_STRICT`** (Python 3.13+: an authority whose
  basicConstraints is not critical, a leaf without an Authority Key
  Identifier) no longer applies on Windows and macOS, where the system
  verifies the chain; it stays on Linux and in the mobile apps. A pinned
  origin verifies no chain at all.

What it does not fix:
- **Linux and the mobile apps** still refuse a leaf sent without its
  intermediate: the status words it as the server's to fix (send the full
  chain), not as a matter of trust.
- **macOS' policy** applies to your own roots too: a leaf issued after
  2019-07 valid for more than 825 days, without the serverAuth extended
  key usage or a DNS or IP SAN, is refused there while Chromium loads it.
  **Trust it for syncing too** closes that gap, and Linux NSS's: it pins
  the exact certificate the window accepted.

## No redirects between Gamma servers

urllib follows a 3xx by itself: it forwards `Authorization: Bearer` to the
new URL, another host's too, turns a POST into a GET without its body, and
fails a PUT or a DELETE with a status that read as "older than this app".
So no Gamma-to-Gamma request follows one.

- **The sidecar.** Every `gamma_sync.Server` request goes through
  `gamma_sync.opener()`: `make_opener`'s opener (urllib's own in the CLI,
  `net_proxy.build_opener` in the app, so routing and pins stay) with
  `_RefuseRedirects` in place of urllib's redirect handler, built once per
  `make_opener`. A redirect raises before anything is sent to its target:
  - one whose `Location` keeps the request's path (the query not compared)
    is a proxy rewriting scheme, host or port: **`Redirected`**, with
    `location` (absolute, at most 300 characters), `to` (the server's
    address there: the Location's scheme, host and port only, sign-in,
    query and fragment dropped, plus the row's own path when it has one,
    no trailing slash, at most 300) and `upgrade` (the same host name,
    lowercase and punycode, http to https, any port: Synology's 5000 to
    5001). Its words: "<old> now redirects to <to>";
  - any other (a sign-in host's `/login?rd=…`, a proxy's error page) is
    **`Blocked`** of kind `signin`, `where` the Location's host. Authelia,
    Authentik, oauth2-proxy, Synology SSO and Cloudflare Access answer a
    request without their cookie this way; urllib used to follow it to an
    HTML 200 and read "did not answer JSON".

  Both are `Unreachable`, so whatever waits on an unreachable server waits
  on these too. The offline copies (`sync_engine`) take the same opener
  for their JSON and uploads, and name its failures with the same
  `gamma_sync.failure_of`; only a file download keeps its own handler,
  which follows a redirect to another origin without the credentials
  ([mirror.md](mirror.md)). The CLI says what to change: `<old> now
  redirects to <to>: if the server moved, set "server" to "<to>" in
  .gamma-sync.json`.
- **The shell.** `serverApi` passes `redirect: 'error'` for each call it
  makes (the mints, the revokes, the move, the pins, the check); a
  redirect then reads "<origin> answered with a redirect: Gamma does not
  send its tokens on to another address."

The other clients of the global opener (the AI services, Zotero) still
follow redirects: they are not Gamma-to-Gamma traffic.

## Failures worded

`gamma_sync` names a failure by class, all of them `Unreachable`:

| Class | When | Words |
|---|---|---|
| `Redirected` | a 3xx whose Location keeps the request's path | "<old> now redirects to <to>" |
| `Blocked` `signin` | a 3xx to another path | "a sign-in or proxy in front of <host> redirected this computer to <where>" |
| `Blocked` `proxy` | a 401, 403 or 407 that is not Gamma's refusal | "<host> answered <status> with a page that is not Gamma's: a proxy or firewall in front of it refused this computer" |
| `Blocked` `cloudflare` | a 401 or 403 with `cf-mitigated`, or with Cloudflare's headers and not Gamma's refusal | "Cloudflare in front of <host> refused this computer's requests" |
| `Blocked` `gateway` | a 502, 503 or 504 that is not Gamma's answer | "<host> answered <status> with a page that is not Gamma's: the proxy in front of it could not reach Gamma" |
| `CertificateRefused` | a `URLError` whose reason is an `ssl.SSLCertVerificationError` | "<host>'s certificate is not trusted on this computer (<why>)"; a pinned origin's other certificate: "The certificate of <host> changed" |
| `NotHttps` | any other `ssl.SSLError` (`WRONG_VERSION_NUMBER`: plain http at an https address), but a handshake the peer cut (`SSLEOFError`, `SSLZeroReturnError`, `SSLSyscallError`, `UNEXPECTED_EOF_WHILE_READING`: a server restarting behind a proxy that accepts), which is an ordinary `Unreachable` and backs off as one | "<host> does not speak HTTPS at this address" |

- **One rule for "something in front refused"**: `gamma_sync.blocked(status,
  headers, data, own=is_gamma_refusal)`, Gamma's own refusal being a JSON
  object with a `detail` key. `Server._json` raises `Denied` only for a
  401 or 403 of Gamma's; the channel and a vault's remote classify the
  answers they read themselves with the same function and
  `Server.last_headers`; `net_proxy.refused_network` calls it for its
  Cloudflare part, with "any JSON" as the service's own answer.
- **A missing intermediate** (`verify_code` 20, unable to get the
  issuer): when the peek saw a chain of one certificate (Python 3.13+),
  "<host> sends its certificate without the intermediate certificate that
  links it to a trusted authority: configure the server or its proxy to
  send the full chain (fullchain.pem)"; when the length is unknown (3.12),
  one sentence naming both causes, your own authority or the missing
  intermediate.
- **Status fields.** `gamma_sync.status_fields(e)` walks an error, its
  `__cause__` and `__context__` five deep to the first of these classes:
  `{error_kind: "redirected", move_to, upgrade}`, `{error_kind:
  "certificate" | "certificate_changed", cert}` (`cert` the served
  certificate's `{sha256, subject, issuer, not_before, not_after}`, or
  None), `{error_kind: "not_https"}`, `{error_kind: "blocked", blocked}`,
  else `{}`. A vault's, a computer link's and an offline copy's status
  carry them beside `last_error`, and drop `gamma_sync.STATUS_KEYS` when a
  round ends without them ([synced_vaults.md](synced_vaults.md),
  [computers.md](computers.md), [mirror.md](mirror.md)); the bar words
  them by `error_kind` ([desktop.md](desktop.md)).

## Pinned certificates

A NAS whose certificate this computer does not trust (self-signed, or
signed by an authority not installed) can be trusted by the exact
certificate, SSH `known_hosts` style. Only in the desktop app.

- **What is pinned**: the SHA-256 of the leaf certificate's DER, 64
  lowercase hex digits: what `openssl x509 -fingerprint -sha256` and a
  browser show, so the person can compare it with the NAS's own page. A
  renewed certificate is a changed one even with the same key: the NAS's
  yearly self-signed renewal asks once.
- **Per origin** `https://host[:port]` in a URL's origin form (lowercase
  host, punycode, IPv6 bracketed, the port left out when 443:
  `tls_trust.canonical_origin`). The shell keeps the list in its registry
  and pushes it whole to each local server it runs (`PUT
  /api/remote-servers/pins`, `tls_trust.replace_pins`, users.db
  `tls_pins`); `GET` answers `{pins}`. Electron's verify proc sees only
  the host name, so there a pin counts for any port of that host; the
  sidecar pins the exact origin.
- **Which failures a pin answers**: `ERR_CERT_AUTHORITY_INVALID`,
  `ERR_CERT_COMMON_NAME_INVALID` (offered only for a `lan` or `loopback`
  host: a public name can get a public certificate) and
  `ERR_CERT_DATE_INVALID`. Never a revoked, weak, intercepted or otherwise
  invalid certificate, nor any `ERR_SSL_*`. The same set decides the offer
  and the acceptance.
- **Never for the Gamma Cloud's domain**: the account server's host
  without its first label (`account.gammapdf.com` → `gammapdf.com`;
  hosted servers are `<label>-user.` under it), the issuer being
  `GAMMA_CLOUD_ISSUER`, else `GAMMA_CLOUD_DEFAULT_ISSUER`, else
  account.gammapdf.com (`tls_trust.cloud_domain`; an account server at an
  IP address or a single-label name names none). Neither side offers,
  accepts, keeps nor applies a pin for a host equal to or under it.
- **Never on a hosted server** (`tls_trust.enabled()`: the desktop app's
  own server, not `GAMMA_HOSTED`): the routes answer 404 and no
  connection reads a pin. **Never in the guarded fetches**
  (`net_guard` builds its opener with `cert_pins=False`): a pin is the
  person's trust in their own server, not in a link someone pastes.
- **The sidecar's connection** to a pinned origin (`net_proxy._HTTPSConnection`)
  handshakes with `tls_trust.pin_context()` (no chain, no host name, TLS
  1.2 at least) and takes the connection when the leaf's digest is the
  pin. Otherwise (another leaf, or none) it connects again with the
  verifying context, which covers a NAS that moved to a public
  certificate (logged once: "[tls] the pinned certificate of <origin> is
  no longer served; the one it serves now verifies"), and failing that
  raises `tls_trust.CertificateChanged`. http.client connects inside
  `send`, so the check runs before a request byte, `Authorization`
  included, leaves. A changed certificate is never trusted silently.
- **The peek.** On the desktop app's server, an unpinned certificate that
  does not verify is looked at through one more handshake through the
  same route (nothing sent) and described on the error (`seen`,
  `chain_length`), so the status carries `cert.sha256`: what the bar's
  **Trust it for syncing too** compares with the certificate the window
  accepted.
- **The shell** ([desktop.md](desktop.md)): `certs.decide` in each
  session's `setCertificateVerifyProc`, a `certificate-error` record per
  origin with a nonce, and the launcher's card (fingerprint, issued to,
  issued by, validity) with **Trust this certificate**, or **Trust the
  new certificate** after a confirm when it changed. The click carries
  the nonce and the digest the card showed, and main pins only its own
  record when both match. **Forget the trusted certificate** is in the
  server's edit form.
- **Chromium keeps its answers for the session.** The network service
  caches the verify proc's answer per certificate, so a certificate
  refused before it was pinned stays refused there whatever the proc says
  next (setting it again, `closeAllConnections` and `clearCache` do not
  help). The shell therefore also accepts a pinned certificate in
  `certificate-error`, by the same rule (`certs.decide`), and Chromium
  keeps that for the page's requests. The other way round, a forgotten
  pin stops answering for any certificate not yet accepted, while one
  accepted this session stays accepted until the app restarts: forgetting
  takes full effect at the next start.

## Moving a server's address

A link's `remote_url` is its identity: a vault, a computer link and an
offline copy each name it, and the computer's id for that server is
computed from it. Changing a server's address in the launcher moves them
with it (`POST /api/remote-servers/move`, strict shell; the shell's
`moveServer`):

1. **A dry run** names the rows that would move (this account's vaults,
   computer links and offline copies at that address) and sends nothing.
   None: the address changes at once.
2. **A confirm** says whose tokens go where: "Gamma will send the tokens
   of 2 synced vaults, this computer and 1 offline copy to <to>…", with
   the unencrypted line for a plain-http address (`plain_ok`). A plain
   http address to a public host is refused outright. Before it, an
   `https://` address's certificate is settled in the shell
   (`trustMoveTarget`): one that verifies needs nothing; one the window
   accepts through a pin of the same host at another port has that pin
   copied to the new address; one a pin may answer is shown with its
   fingerprint and **Trust this certificate** (Cancel moves nothing). The
   pins go to the local servers before the move, and a pin made for a move
   that fails or is cancelled is dropped again. The automatic upgrade
   (below) never asks.
3. **A probe without credentials**: `GET <to>/api/health` must answer
   Gamma's JSON, redirects refused, the certificate verified or pinned.
   For another host name it is the certificate that proves who `<to>` is.
4. **The rows checked one at a time**, each with its own token, the
   computer link first (its token opens only `/api/computers/`; it
   announces what the channel announces, its vaults included, since at
   the same server its row there is updated, and an announce naming no
   vault would end its work folders' agents and their queued turns), stopping
   at the first failure: 409 for a token refused or another workspace
   there, 502 for an address not reached, redirected, blocked or not
   trusted. Nothing moves then. A row whose sealed token no longer opens
   (the data directory's key changed) moves unchecked: it has nothing to
   send.
5. **One transaction** re-points the rows; the channels restart. A moved
   row starts afresh: a channel is `connecting` again, and a vault's or a
   copy's status drops what the old address's failure left
   (`gamma_sync.STATUS_KEYS`, `failed_at`), so its next round runs at once
   rather than after the retry wait. A computer link this computer already
   had at the new address (shown there first) gives way to the moved one,
   and its token is in the answer's `revoke`, for the shell to revoke
   there.

What the checks prove: a token accepted at the new address catches
mistakes (another honest server, a revoked token, the wrong workspace),
not an impostor, which accepts any token and answers the ids a NAS's
pages show. Only the certificate proves `<to>`, which is why another
host name needs one that verifies or is pinned.

**The computer id stays.** users.db `server_addresses` keeps, per
address, the origin the id was first computed from (`id_origin`), and
`moved_to` once an address was left; a move carries the id across, a move
back gives the first one again, and a new link at an address a server
left gets a fresh generation, so another server there never gets this
computer's id for the one that left.

**The automatic upgrade.** A link whose status says `redirected` with a
same-host http→https `move_to` (recomputed by the shell from the row
itself, never taken from a page) is moved by the shell without a
question when the https side verifies, with a notice; otherwise the bar
offers **Use <to>**, which asks about a self-signed certificate as an
address change does. An `http://nas:5000` link behind Synology's
"redirect HTTP to HTTPS" or Cloudflare's "Always Use HTTPS" keeps working
this way.

**Offline copies kept by another local server.** A move re-points the
host's rows only (the local server the shell keeps its links on, where
the shell makes its offline copies too). A copy of the same NAS that
another local server keeps (one made in that server's own Settings)
stays at the old address, its status saying why its rounds fail: the
shell runs the move on the host alone, and no form changes a copy's
address. The route is every local server's, for its own rows, so a
later step can run it there.

## HSTS

`auth._hsts`: `Strict-Transport-Security: max-age=31536000` on a response
over https (the scheme, or `X-Forwarded-Proto: https` from any peer: HSTS
received over plain http is ignored, so believing the header costs
nothing, and the usual Docker deployment never has its scheme rewritten)
to a **public DNS name**: `host_scope.literal(host) == "name"`, not an IP
address, a single label, `localhost` or a local suffix. Nothing elsewhere.

- **No `includeSubDomains`.** HSTS covers every port of the name, and the
  next header without it releases the subdomains a stored policy held.
- **Never `max-age=0`**, which would cancel the HSTS a TLS proxy adds
  after the upstream's (Nginx Proxy Manager's "HSTS Enabled", Synology's
  "Enable HSTS"; browsers act on the first header), and would let any
  page clear the host's policy through another https port.
- Multi-host servers keep HSTS on every name; hosted servers and the
  demo are unaffected either way, since their Caddy replaces the header.
  The Secure cookie still follows `_is_https`.
- **A LAN port of the https name** (`http://nas.example.com:9001` beside
  `https://nas.example.com`) stays HTTPS-only in a browser that visited
  the https name: serve the LAN port by IP or a `.lan` name instead. The
  launcher says so for an http address that fails this way ("This name is
  HTTPS-only in this window", **Use https://<host>**).

## Plain http

`host_scope` reads where a host is, the same table as the shell's
`hostScope.js` (both test `backend/tests/data/host_scope_cases.json`):

| Scope | `literal` (no DNS) |
|---|---|
| `loopback` | 127.0.0.0/8, ::1, `localhost` |
| `lan` | an address in 10/8, 172.16/12, 192.168/16, 100.64/10 (Tailscale and carrier NAT), 169.254/16, fc00::/7, fe80::/10; a single-label name; `.local`, `.lan`, `.home.arpa`, `.internal`; `*.localhost` |
| `far` | any other address |
| `name` | any other dotted name |

Every entry is in the proxy's default bypass list
(`proxy_settings.DEFAULT_BYPASS`), `*.localhost` aside. `resolved(host)`
decides a name by DNS, for the places that decide whether a token may go:
all answers loopback is `loopback`, all private `lan`, any public one
`far` whatever the suffix (a search domain can make `nas.lan` public), but
for global IPv6 addresses beside a private IPv4 one, which are a
dual-stack LAN host's (a home router's DNS and mDNS answer both), a
name that does not resolve its literal scope with `name` as `far`, and a
lookup slower than 2 s `lan` with `unsure` (a warning, never a refusal).
Fake-IP answers (Clash, sing-box) count as none. Answers are kept 60 s.

- **New links to a public host over plain http are refused** (a vault, a
  work folder, "Show this computer", an offline copy, a move's `to`):
  "<host> is on the internet and this address is http://: Gamma sends its
  tokens there only over https://." The tokens are 365-day write and
  computer tokens.
- **Existing ones keep working**, their upkeep included (a renewed token,
  a computer link re-tokened, an offline copy relinked at the same
  address), with a warning instead of a refusal. Upkeep is that link's
  own: another vault or offline copy beside an existing link there, or a
  channel in another workspace, is a new link and refused.
- **Lists carry `plain`** (`""`, `lan` or `name`) without DNS, so a list
  never waits on a resolver.
- **The shell confirms once per origin** before it mints a token for a
  plain-http LAN address (`POST /api/remote-servers/check`), and every
  time for an existing public one; **Ask again before connecting over
  http://** in the server's form clears it. An origin a link of before
  this release already used counts as confirmed (`existing`: a link of
  any kind there). `refused` counts upkeep as the route that takes the
  token does, so no token is minted in clear for a link that route then
  refuses: the shell names the renewed vault (`vault`) or the channel's
  workspace (`workspace`), and a check naming neither is for a new vault
  or offline copy. `check` asks DNS for an
  http:// address only (an https:// one's scope is its literal one, a
  dotted name `far`); its `warning` is the question's text without the
  unsure line, which the shell adds when `unsure`.
- **Tailscale** (100.64/10, MagicDNS `*.ts.net`) is `lan`, not exempt:
  WireGuard encrypts it, but the range is also carrier NAT. Use
  `tailscale serve` for https.
- A NAS reached over loopback (an `ssh -L` tunnel, a local proxy) is the
  person's own tunnel: no warning.

## Routes

`routers/remote_servers.py`, the desktop app's own server only
(`config.folder_links_enabled()`, else 404 for every route), a signed-in
session; the shell calls them through `serverApi` (`redirect: 'error'`).
"Strict shell" is `routers/folder_links._shell_strict`: the shell's word
(`X-Gamma-Shell`) is required, and a server started with no secret
refuses (403) rather than passing.

| Route | Auth | Body → answer |
|---|---|---|
| `POST /api/remote-servers/check` | session | `{url, vault?, workspace?}` → `{origin, scope: loopback\|lan\|far, plain, existing, unsure, refused, warning}` (`vault` the vault whose token is renewed, `workspace` the channel's there); 400 for an address that does not do |
| `POST /api/remote-servers/move` | strict shell | `{from, to, dry_run?, plain_ok?}` → dry run `{from, to, kind, plain, moved: {vaults, computers, mirrors}}` (no request made); move `{from, to, kind, moved, revoke: [{workspace_id, token_id}]}`; 400 an address that does not do, the same address, plain http to a public host, plain http without `plain_ok`; 409 a token refused or another workspace there; 502 not reached, redirected, blocked or not trusted |
| `GET /api/remote-servers/pins` | strict shell | → `{pins: [{origin, sha256, subject, issuer, not_before, not_after, pinned_at}]}` |
| `PUT /api/remote-servers/pins` | strict shell | `{pins: [{origin, sha256, subject, issuer, not_before, not_after}]}`, the whole list → `{pins}`, `pinned_at` kept for an unchanged pin, and what a certificate failure rests (a vault's retry wait, a channel's longest rest) tries again at once (`folder_links.certificates_changed`); 400 with words for one that does not do, the list kept |

The pins routes answer 404 where `tls_trust.enabled()` is false (a
hosted server). The shell pushes the list to each local server as it
starts, an empty one included, so a table never keeps a pin dropped while
its server was down.

## Mobile apps

Android trusts user-installed authorities, the Gamma Cloud sign-in's
WebView included ([android.md](android.md)); the iPad's notes are in
[ipad.md](ipad.md). Both apps' embedded server verifies with certifi, and
neither pins a certificate.

## Tests

| What | Where |
|---|---|
| the trust store's answers, the plain class, pins against a real TLS server on 127.0.0.1 with certificates made by `cryptography` (pinned, changed, hosted, the guarded fetches' opener built without pins, the strict flags, a stale pin, no certificate, a leaf sent alone, the list's validation) | `tests/test_tls_trust.py` |
| redirects (every 3xx and method, upgrade, the address kept, sign-in redirects), `blocked`, `NotHttps` and a handshake cut short, the certificate words, `status_fields`, the URL-aware seam, the CLI's words, the standard-library rule | `tests/test_gamma_sync_errors.py` |
| the scope table and `resolved` (a dual-stack LAN host among them) | `tests/test_host_scope.py`, `desktop/test/hostScope.test.js` |
| HSTS | `tests/test_auth.py` |
| the move (the channel's announce whole), the pins routes (the certificate waits ended), the plain-http refusals (`check`'s upkeep as each route counts it) | `tests/test_remote_servers.py` |
| end to end over TLS | the browser group `https` (`frontend/tests/e2e/scenarios/https.mjs`) |

Every verifying context in those tests is of the plain class and trusts
only the test's own authority, so no test asks this computer's store
(Windows may fetch roots for an unknown one), except the one case that
checks truststore refuses an unknown certificate.
