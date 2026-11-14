# HTTPS with a NAS

Surveyed November 2026 against `dev` (10920532, then e9ac3188 for the
design and its critiques). The question: is Gamma fully HTTPS compatible
when the server is a NAS, for the synced vaults and work folders, the
computer's channel, the offline copies and the desktop window, and if not,
what does it take? How it works now is in [dev/https.md](../dev/https.md),
which this note does not repeat.

## What the survey found

Gamma already worked over HTTPS with a certificate from a public
authority (Let's Encrypt, a Cloudflare edge certificate, Tailscale's
`*.ts.net`, Synology's Let's Encrypt one). Nothing hardcoded `http://` or
`ws://`: the link routes accepted either scheme, the shell's typed
addresses defaulted a public name to https, and the collab socket followed
the page's scheme. The session cookie turned Secure behind TLS, absolute
URLs came from the confirmed Public URL rather than the request, and the
computer's held poll (25 s) fitted the idle limits of nginx, Synology and
Cloudflare. A sign-in or captive page answering 200 HTML was already read
as unreachable, never as data.

Three TLS stacks checked one server, and they disagreed:

| Connection | Client | Trust |
|---|---|---|
| the window, its `/api`, the collab socket | Chromium | the OS store (and Chrome's root store) |
| the shell's mints and revokes | `session.fetch` | the same |
| the sidecar: vaults, the channel, the agents' writes, offline copies | urllib | Windows: the system's stores; macOS and Linux: OpenSSL's paths, or certifi where they are empty |
| Android's WebView | Android | system CAs only |
| the iPad's WKWebView | ATS | the iOS store, profile CAs with Full Trust included |
| the apps' embedded Python | urllib | certifi |

So it was not fully compatible:

- **A private authority or a self-signed certificate.** On macOS a root
  trusted in the Keychain loaded the page and minted the token, and then
  every sidecar request failed `CERTIFICATE_VERIFY_FAILED`; the route
  answered "could not be reached" and the shell revoked the token. The
  documented workaround, `SSL_CERT_FILE`, replaces the bundle: pointed at
  the NAS's root alone it broke every other HTTPS call. A self-signed
  certificate never loaded in the window at all, and the launcher's only
  offer was *Use http:// instead*, a downgrade.
- **A leaf without its intermediate**, common with hand-written nginx and
  Synology imports of a bare `cert.pem`, works in Chromium, which fetches
  the intermediate, and failed in urllib. Python 3.13's
  `VERIFY_X509_STRICT` (the Android and iPad builds; the desktop and
  Docker builds were 3.12) also refuses certificates many NAS tools make:
  no Authority Key Identifier, an authority whose basicConstraints is not
  critical.
- **Redirects.** A NAS redirecting http to https (Synology's option,
  Cloudflare's "Always Use HTTPS") made urllib follow: every request went
  out first over plain http with the bearer in it, urllib copied
  `Authorization` to the new URL even on another host, a POST became a
  GET without its body (a plan, an announce, an upload read as "the other
  server is older than this app", and the channel waited an hour), and a
  PUT or DELETE failed with a 3xx counted as the file's problem. The
  shell's `session.fetch` followed the Fetch rules instead, so the mint
  and the sidecar behaved differently against the same NAS.
- **The address was identity.** A vault, a computer link and an offline
  copy stored the URL as given, the channel paired a vault with its link
  by exact string, and the computer's id was an HMAC over the scheme and
  host. Nothing re-pointed the sidecar's rows when the shell's address
  changed: moving a NAS to https or to a Tailscale name left the old
  links failing, a new "Show this computer" made a second computer, and
  an agent's run found no channel.
- **Gamma's own HSTS** (`includeSubDomains`, a year) followed any https
  visit, the forwarded scheme believed from any peer. HSTS covers every
  port, so one visit to `https://nas.example.com` made
  `http://nas.example.com:9001` (split DNS to the bare container)
  unusable in the window, and *Use http:// instead* was upgraded again.
  The sidecar ignores HSTS and kept using http, so the two then reached
  the NAS at different addresses.
- **A refusal by something in front** (Cloudflare's WAF, a sign-in
  proxy's 403 page) read as the token refused: the vault said so and the
  channel waited ten minutes.
- **Android** ignores user-installed authorities in apps targeting API 24
  and later; **the iPad's** ATS refuses plain http to a dotted name while
  `.local` and single-label names load.

## The critic's corrections

A second read of the survey against the code changed a few claims:

- On plain http, a sniffed computer token lets someone pose as the
  **computer** (claim and answer the maker's Sends and turns), while
  posing as the NAS to the computer needs an active man in the middle and
  no token. The vault's 365-day write token travels the same way and is
  the larger prize.
- The takeover's per-request timeout is 120 s inside a round, not the
  15 s of the questions asked between rounds.
- The proxy needs an idle limit a little over 25 s, the server's hold,
  not the client's 40 s socket timeout.
- An unnormalised origin (`https://NAS` against `https://nas/`) bites only
  links made outside the shell and after an address change: the shell
  always posts a normalised `URL.origin`.
- **A NAS over loopback** (an `ssh -L` tunnel, a local proxy) counts as
  near, shares the host's site for Lax cookies, and both servers' cookie
  is called `session` on 127.0.0.1.
- **Sub-paths**: a link keeps a path, the shell's registry only the
  origin, and the computer's id drops it, so a NAS at `https://host/gamma`
  cannot be the window's server. The Public URL refuses a path and the
  web app calls an absolute `/api`: the server itself is never served
  under one.

## The design, and what the critiques changed

Step 5a took, in order of what each unblocks: the system trust store in
the server (truststore, certifi kept on mobile), no redirects between
Gamma servers, failures worded by kind, a self-signed or private-CA
certificate trusted by its digest per origin, an address move that keeps
the links and the computer's id, HSTS only where it is safe, and a rule
for plain http. Keep-alive and a poll that lowers its own wait went to
step 5c: reusing a connection needs a safe retry for requests that are
not idempotent, which urllib and `http.client` do not give, and the
takeover should be measured first.

A security critique and a compatibility critique of the first design
changed it before it was built:

- **The move's token check proved nothing against an impostor**, and sent
  every token before deciding. A hostile server accepts any token and
  answers the workspace and account ids a NAS's pages show. The move now
  runs a dry run, probes `/api/health` without credentials (redirects
  refused, the certificate verified or pinned: only the certificate
  proves who the new address is), checks the rows one at a time with the
  computer's token first (it opens only `/api/computers/`), stops at the
  first failure, and tells the person whose tokens go where before any
  leaves. Another host name needs a certificate that verifies.
- ***Use http:// instead* would downgrade every link in one click.** A
  plain-http address now needs the person's confirm with the
  unencrypted line, the button is never offered for a pinned origin, one
  that loaded over https before or a public host (those are worded as
  suspicious), and a public host over http is refused for new links.
- **The trust button pinned what main held at click time**, not what the
  card showed: each certificate error now has a nonce, and only a
  matching snapshot is pinned.
- **After a move the old origin kept the new one's identity.** Identity
  stays exact; navigation to a moved-away origin is rerouted instead.
- **The HSTS rule** first required a trusted scheme and a matching Public
  URL, which removed HSTS from the usual Docker deployment (the proxy
  reaches the container from the bridge gateway, outside
  `FORWARDED_ALLOW_IPS`), and allowed `max-age=0`, which cancels the HSTS
  a TLS proxy adds after the upstream's. It became one year on a public
  DNS name over https, nothing elsewhere, never `max-age=0`.
- **The offline copies' uploads followed redirects with the bearer**: the
  mirror engine's JSON and uploads now take gamma_sync's refusing opener;
  only file downloads keep their own handler, which drops credentials
  across origins.
- **A server-supplied `move_to` was offered as a button.** The shell
  recomputes the upgrade from the row itself, the address is built from
  scheme, host and port only, and a redirect that is not an upgrade is
  never worded "open <to>".
- **A pinned digest was accepted whatever else was wrong.** A pin answers
  only an unknown authority, another name (for a LAN host only) or a date,
  never a revoked or weak certificate, and never under the Gamma Cloud's
  domain.
- **`server_addresses` would have let a later server at a left address
  match this computer**: a left address keeps where it went, and a new
  link there gets a fresh generation of the id.
- **`plain` on every list ran DNS on a request path**, which the test
  suite forbids (its resolver guard raises `AssertionError`) and which
  stalls offline. Lists now read the scope without DNS; DNS runs only when
  a token is about to go, on a worker thread with a 2 s limit, a time-out
  counting as LAN with a warning.
- **truststore broke a startup test and could stall tests offline**
  (Windows fetching roots for an unknown certificate): the tests verify
  with contexts of the plain class that trust only their own authority,
  and one case tries truststore.
- **A sign-in proxy's 302 read as "the server moved"**: only a redirect
  that keeps the request's path is a move; any other is a sign-in in the
  way.
- **Existing public plain-http links were kept but their upkeep refused**:
  renewing a token, re-showing the computer and relinking at the same
  address now warn instead.
- **The macOS sidecar refused a certificate the window accepted**, with
  no card to show (Apple's policy for user roots: 825 days, serverAuth,
  a SAN). The verify proc records what Chromium accepted, the sidecar
  peeks at the leaf it refused, and the bar offers *Trust it for syncing
  too* when the two match; that closes Linux NSS's gap too.
- **Two rules for "a proxy refused this"** became one,
  `gamma_sync.blocked`, shared with the AI providers' region check.
- **A leaf without its intermediate** is worded as the server's to fix
  where Python can tell (3.13's unverified chain), and with both causes
  where it cannot.
- **Over-engineering cut**: the poll's self-lowering wait, a per-server
  queue of pending moves and *Change it here only* went; the move is
  idempotent and a links-free address change needs no request.

## Found while building

- With truststore injected, `ssl.SSLContext`'s own property setters
  (`verify_mode`, `minimum_version`, `verify_flags`, `options`) look the
  class up by its module name, which is then truststore's, and call
  themselves until the stack overflows. A context of the plain class (the
  pinned path's, the tests') has those set through the base class.
- Two cuts from the design: the "replaced" notice for a stale pin whose
  certificate now verifies (logged instead: on an IP or LAN name it can
  mean a TLS-inspecting root installed here), and the shell replaying a
  move on other local servers (an offline copy kept by another local
  server stays at the old address: the shell moves the host's rows only,
  and no form changes a copy's address yet).

## Left open

- Keep-alive and the poll's self-lowering wait (step 5c, measured first).
- Pinning in Android's WebView (`onReceivedSslError`) and the iPad's
  WKWebView (`didReceive challenge`); both apps' embedded server keeps
  certifi.
- A custom CA field for Linux, never for hosted servers.
- A Content-Security-Policy on the shell's launcher and bar, whose inline
  scripts and handlers must move first.
- Sub-path deployments, and the cookie clash of two servers on 127.0.0.1.
- Clearing Electron's stored HSTS: no API names its store.
- The CLI's `--server` flag (its message names the one-line edit of
  `.gamma-sync.json` instead), the User-Agent `gamma-sync/1`, and
  `X-Accel-Buffering: no` on the AI chat's streams.
