# Network proxy

How this server reaches the internet. An admin sets one proxy for the whole
server in Settings → Administration → Network proxy, and every outbound
connection the server process makes takes it: AI providers, the model
catalog, transcription, translation engines, paper metadata, PDF
downloads, link previews, the assistant's web reads, Zotero's API and
stream, Gamma Cloud, other Gamma servers and computers, S3 off-site
copies, the update check, and the environment of the local agents' CLIs.
It is not a library proxy (EZproxy), which rewrites publisher addresses.

There is one proxy, not one per purpose. Traffic is routed by destination:
a "Don't use the proxy for" list sends hosts, domains and address ranges
direct, and loopback and the proxy itself always go direct. Why one proxy,
and why guarded fetches are pinned through it, is in the research note
[network-proxy.md](../research/network-proxy.md).

| Side | Code |
|---|---|
| the setting: storage, checks, the API's view, the Test | `gamma/proxy_settings.py` |
| routing, tunnels, the urllib handlers, the other clients | `gamma/net_proxy.py` |
| guarded fetches over the same handlers | `gamma/net_guard.py` |
| the routes | `gamma/routers/admin.py` (`/api/admin/proxy*`) |
| the consumers | `app.create_app` and `manage.main` (`net_proxy.install`), `zotero_web._opener`, `sync_engine._file_opener`, `zotero_stream.connect`, `s3.Client`, `local_agents/procs.child_env`, the scrub rules of `logbuf.py` |
| the pane | `frontend/src/settings/SettingsProxy.jsx` ([settings.md](settings.md) "The Settings dialog") |

## The setting

Three modes:

- **Use system settings** (`system`, the default). The proxy is the one
  urllib finds (`urllib.request.getproxies()`): the `*_proxy` variables,
  else on Windows the registry and on macOS the system's network settings.
  Linux, iOS and Android read the variables only. The system's bypass list
  (`no_proxy`, Windows' `ProxyOverride`) applies through
  `urllib.request.proxy_bypass`. PAC and WPAD are never read. This is the
  proxy Gamma took before there was a setting, with these differences:
  the `all` entry (`ALL_PROXY`), which urllib, websockets and botocore
  ignored, is used for http and https when the scheme has no entry of its
  own; loopback always goes direct, where urllib sent it through
  `HTTP_PROXY` when `NO_PROXY` was unset; a SOCKS5 system proxy works; a
  `socks4` entry is ignored, so those connections go direct; guarded
  fetches go through the proxy, pinned ([below](#guarded-fetches)); and a
  proxy only the OS knows is passed to the local agents. An operator who
  does not want `ALL_PROXY` used sets `HTTPS_PROXY`/`HTTP_PROXY` or
  chooses No proxy.
- **No proxy** (`none`): every connection goes direct, whatever the
  environment or the OS sets.
- **Manual** (`manual`): the proxy typed in the pane, with its bypass list.

It is stored as one JSON object under the users.db `settings` key `proxy`:
`{mode, server, username, password, bypass, proxy_dns}`. The password is
sealed with the data directory's key (`publisher_sessions.seal`, the key
the off-site secret uses). `server` is `scheme://host:port` without a
sign-in. `bypass` is the normalized list, and `DEFAULT_BYPASS` until an
admin saves another:

```
localhost, 127.0.0.0/8, ::1, 10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16, 100.64.0.0/10,
169.254.0.0/16, fc00::/7, fe80::/10, *.local, *.lan, *.home.arpa, *.internal, <local>
```

`proxy_dns` ("Let the proxy look up names") only changes guarded fetches
(below).

**Applied at once.** `proxy_settings.policy()` is what every connection
routes by, read as the connection opens. It reads the saved value on a
short connection of its own that never creates users.db (about 130 µs,
`server_settings._peek_raw`, which `offsite._kv` reads through too), and
parses it again only when the text changed.
So a save applies to the next connection, with no restart. The read does
not use the cached connection of `server_settings`, because `policy` also
runs inside `manage.py` commands: an off-site restore into an empty data
directory must find users.db as it was and nothing holding it. A value that
cannot be read or parsed routes as `system`.

**Read defensively.** A missing or corrupt value reads as the defaults. A
password that no longer unseals (the data directory's key changed) reads
as none, with a warning in the log, as the off-site secret does.

**On a hosted container** (`config.hosted()`) the network is the
operator's. The setting reads as `system` there whatever is stored, a PUT
is refused with 409 ("Gamma Cloud manages this server's network."), and
the pane is read-only. The operator can still set `HTTPS_PROXY` and
`NO_PROXY` in the server's environment ([hosted.md](hosted.md), the
server's `extra_env`). A hosted owner may not choose a proxy because the
proxy's address may be private: on the fleet network that would let the
owner, and through guarded fetches every member, reach other tenants'
containers.

### The proxy's address

`parse_server` reads `scheme://[user:password@]host:port`:

- Schemes `http`, `socks5` and `socks5h`. A bare `host:port` is `http`.
  Both SOCKS schemes behave alike: the proxy resolves names, as in
  Chromium.
- `https://` (TLS to the proxy) is refused: "An https:// proxy isn't
  supported yet: use the proxy's http:// or socks5:// port". socks4 and
  any other scheme are refused, naming the supported ones.
- The port is required ("Add the proxy's port, as in
  http://127.0.0.1:7890"). A path, a query or a fragment is refused. IPv6
  goes in brackets.
- A sign-in in the address is percent-decoded and moved into the user name
  and password. The stored `server` never holds it, and no error message
  repeats the text typed.
- Basic sign-in for an HTTP proxy, RFC 1929 for SOCKS5. No NTLM or
  Kerberos: a local helper such as Px or cntlm can speak those. A SOCKS5
  user name or password takes at most 255 bytes, and control characters
  are refused in either.

A system proxy is read with the same parser, except that one without a
port takes 80 for http and 1080 for SOCKS, as urllib does. An `https://`
system entry is read as urllib read it, a plain HTTP proxy (CONNECT in the
clear), on port 443 when it names none. A system proxy Gamma cannot use
(a Windows `socks=` entry, which urllib reads as `socks4://`, or one that
does not parse) is ignored, so traffic goes direct, and the pane says so.
An unreadable entry is shown by its host and port alone, everything up to
its last `@` dropped, so no sign-in reaches the pane.

### "Don't use the proxy for"

`parse_bypass` splits on commas and white space. Each entry is one of:

- `*`: everything;
- `<local>`: a name without a dot (not an IP address), as in Chromium;
- an IP address, IPv6 with or without brackets;
- a range in CIDR form, `10.0.0.0/8`;
- a name: `example.com`, `.example.com` and `*.example.com` all match the
  name itself and every subdomain, at a label boundary.

Entries are lowercased, a trailing dot is dropped, and a repeated entry is
kept once. An entry with a port, a scheme, a path or another character is
refused with its text: "“…” isn’t a host, domain, IP address or IP range".
The list takes at most 20000 characters. The normalized list is the
entries joined by ", ".

## Routing

`net_proxy.route(host, scheme, policy)` gives a `Route(kind, server,
reason)`: `direct`, `http` or `socks5`, the proxy, and why a direct route
is direct. In order:

1. Loopback goes direct (`loopback`): `localhost`, `*.localhost`,
   127.0.0.0/8, `::1` and their IPv4-mapped forms.
2. `none` goes direct (`no proxy`).
3. The proxy is Manual's, or under `system` the system's entry for the
   scheme, else its `all` entry (`ws` and `wss` count as `http` and
   `https`). No proxy, or one Gamma cannot use, goes direct (`no proxy`).
4. The proxy's own host goes direct (`the proxy itself`).
5. Manual: the first bypass rule that matches sends it direct
   (`bypass: 192.168.0.0/16`). When the host is a name and the list holds
   a range, the name is looked up here, and it goes direct when any answer
   lies in a range. This catches `nas.example.com` answering 192.168.1.5.
   A fake-IP answer (`FAKE_IP`, [below](#guarded-fetches)) is skipped, as
   it says nothing of where the name is, even under a range that holds
   it. A name that does not resolve here takes the proxy.
   System: `proxy_bypass(host:port)` sends it direct (`system bypass`),
   with the port a URL names (the Zotero stream passes its own, 443 for a
   bare `wss` URL, as websockets did), so a `no_proxy` entry
   `git.example:8080` matches that port only, as urllib read it.
6. Otherwise the proxy.

A decision is kept 60 s per (policy, proxy, host, port), including the
outcome of a lookup, failed or not. At most 4096 are kept; past that they
are all dropped. The system's proxies are read again every 30 s, so a
change in the Windows or macOS proxy settings applies without a restart
(when no `*_proxy` variable is set). The variables come from the server's
own environment, so a change to them applies only when the server
restarts. In `system` mode with
no proxy found, routing costs nothing: no lookup, no cache entry. In
Manual with the default list, which holds ranges, each new name is looked
up once a minute; on a network where DNS hangs instead of failing, that
lookup can hold up the connection.

## Tunnels

`open_tunnel(route, target, port, timeout)` connects to the proxy and asks
it for the target. The socket keeps `timeout`, as
`socket.create_connection` leaves it.

- **HTTP proxy:** `CONNECT host:port HTTP/1.1` with the same `Host`, and
  `Proxy-Authorization: Basic …` when there is a sign-in. An IPv6 target is
  bracketed, a non-ASCII name sent as IDNA. The answer is read a byte at a
  time up to its blank line (64 KiB at most; a bare `\n` ends a line too,
  as `http.client` reads it), so nothing of the tunnel is consumed. 2xx
  opens the tunnel, 407 is a sign-in error, another status a refusal with
  it, and an answer whose first line is not `HTTP/… <digits>` "didn't
  answer as an HTTP proxy".
- **SOCKS5:** the greeting offers no sign-in, plus user name and password
  when there is one (RFC 1929). The CONNECT names an IP address as one
  (address type 1 or 4) and a name for the proxy to resolve (type 3, at
  most 255 bytes). A reply other than success is a refusal with RFC 1928's
  words ("connection refused", "host unreachable", …).

Errors are `ProxyError` (an `OSError`), with `ProxyUnreachable`,
`ProxyAuthError` and `ProxyRefused`. Their text is shown to people and
logged, so it names the proxy as `host:port` and never holds its sign-in:

- "Couldn't reach the proxy at 127.0.0.1:7890 (Connection refused). Is it running?"
- "The proxy at 127.0.0.1:7890 asks for a sign-in (407)" / "… refused the sign-in"
- "The proxy at 127.0.0.1:7890 refused the connection to example.com:443 (403 Forbidden)"

## How each client takes the route

**urllib.** `RoutedHTTPHandler` and `RoutedHTTPSHandler` decide the route
per request from the URL, without touching the request (no
`set_proxy`), and open it on a connection bound to that route:

- An https target, on any route, and any target through SOCKS5: the
  connection's socket is a tunnel (or a direct connection), and the stdlib
  then wraps TLS naming the URL's host, so the SNI and the certificate
  check are the host's.
- An http target through an HTTP proxy, when the proxy is asked for the
  URL's own host: the connection goes to the proxy and the request is sent
  in absolute form (`GET http://host[:port]/path`) with
  `Proxy-Authorization` when there is a sign-in. `Host` stays the URL's.
  A 407 answer is the proxy's: it raises the CONNECT path's
  `ProxyAuthError` (in a `URLError`) instead of reaching the caller as an
  HTTP error.
- An http target whose address is pinned in place of the host (`pin`,
  the guarded fetches): a tunnel, `CONNECT 8.8.8.8:80`, then `GET /path`
  with the URL's `Host`. A proxy sent the request in full would forward the
  `Host` the target names, here the address, which the origin may not
  serve.

`net_proxy.build_opener(*handlers, policy=, direct=, pin=)` is urllib's
`build_opener` with these two in place of its own and an empty
`ProxyHandler`, so urllib's environment proxies never apply. `policy`
fixes the setting to route by (the Test passes a draft's); by default the
saved one is read per request. `direct` opens a direct route, `pin` names
what a proxy is asked for: net_guard passes its own (below).

**Every bare `urlopen`.** `create_app` calls `net_proxy.install()`, which
makes this opener urllib's global one. Every `urlopen` of the process
reads it as it runs, `from urllib.request import urlopen` ones included.
`RoutedHTTPSHandler` makes its TLS context at its first https request
(once, unless one is passed), not as it is built, so an opener built at
import still loads the CAs that `_verify_https_with_certifi_when_no_cas`
names at startup.

**Openers of their own.** `zotero_web._opener` and
`sync_engine._file_opener` are built with `net_proxy.build_opener`. They
used to take the environment's proxies once, at import.

**The Zotero stream.** `zotero_stream.connect` opens its socket on the
route (`net_proxy.socket_for`) and hands it to websockets with
`proxy=None`. The dial runs on a pool of two threads of its own, so one
left hanging holds up neither the loop's executor nor the link reads; the
dial and the WebSocket's opening share one limit, `HELLO_S` (30 s), and
a socket a dial delivers after it was given up on is closed. For `wss`
websockets names the URL's host in TLS. The WebSocket closes with 1000
however the block ends.

**S3 off-site copies.** `s3.Client` passes
`Config(proxies=net_proxy.botocore_proxies(endpoint))`: the HTTP proxy of
the endpoint's route (AWS's `s3.amazonaws.com` when none is set), with its
sign-in, or `{}` for a direct route, which also stops botocore reading the
environment. boto3 cannot use SOCKS5, so under a SOCKS5 proxy the copies
connect directly. Nor can it send a sign-in that is not ASCII (it fails
every request) or a user name without a password: the proxy is then given
without its sign-in. Each is said once in the log.

**Local agents.** `procs.child_env` passes the agent's environment through
`net_proxy.agent_env`:

- Manual: for an HTTP proxy `HTTP_PROXY` and `HTTPS_PROXY`; for SOCKS5
  `ALL_PROXY` alone, as `socks5h://`, since a client reading
  `HTTP(S)_PROXY` speaks only to an HTTP proxy. The sign-in is
  percent-encoded. `NO_PROXY` is `localhost,127.0.0.1,::1` and the bypass
  list, each name as both `x` and `.x` (tools spell "the name and its
  subdomains" either way), addresses and ranges as they are, `<local>`
  left out. Every other `*_proxy` variable is removed.
- No proxy: every `*_proxy` variable is removed, in any case.
- System: the environment is left as it is, except that a proxy only the
  OS knows (no variable set) is passed on as Manual's would be, with
  `NO_PROXY` from `DEFAULT_BYPASS`. The OS's own exceptions (Windows'
  `ProxyOverride`, the macOS list) are not read, so an agent takes the
  proxy for a host there that the server itself reaches directly.

Each variable is set in upper and lower case, except on Windows, where a
variable has one name whatever its case and only the upper one is set.

**`manage.py`.** Its commands never call `create_app`, so `main()` calls
`net_proxy.install()` first: their bare `urlopen` calls (Gamma Cloud) take
the setting as the server's do, as do `s3.Client`, the Zotero and mirror
openers and an agent's environment.

## Guarded fetches

URLs that people or the assistant supply go through
`net_guard.guarded_urlopen` ([api.md](api.md) "Auth model"). They take
the same route as every connection, with the admin's proxy and bypass list
and never a proxy a member chooses, and keep their checks on each route:

- **Direct:** as before. The whole DNS answer is checked, and the socket
  connects to a checked numeric address without a second lookup
  (`_public_connection`).
- **Through the proxy** (the default): pinned. The host is resolved and
  the whole answer checked here, as for a direct fetch, and the proxy is
  asked for a checked address (`_target`): CONNECT or SOCKS5 to
  `8.8.8.8:443`, and for an http URL a tunnel too (`CONNECT 8.8.8.8:80`,
  then `GET /path`), never the absolute form, whose forwarded `Host` would
  be the address. An IPv4 answer is preferred, since many proxies have no
  IPv6 route while local DNS may list IPv6 first. `Host` and the TLS name
  stay the URL's host.
- **Through the proxy with `proxy_dns`:** the proxy is asked for the name
  (without a trailing dot), once `_proxy_resolvable` passes it. It refuses
  a blocked address written any way a proxy reads as one (`10.0.0.5.`,
  `127.1`, `0x7f.1`, `10.1`, `[::ffff:10.0.0.5]`), `localhost`, a name
  without a dot, and names under `.localhost`, `.local`, `.internal`
  (`metadata.google.internal` among them), `.home.arpa` and `.lan`. It
  then looks the name up here: any blocked answer outside `FAKE_IP`
  (198.18.0.0/15 and fc00::/18, the ranges Clash and sing-box give their
  fake-IP answers in; real unique-local addresses sit in fd00::/8) refuses
  the fetch. Public answers, fake-IP answers and a failed lookup all let
  the proxy resolve it. An http URL then goes in absolute form, the proxy
  being asked for the name.

`validate_public_url`, the early check that the redirect handler and
`zotero_web` also use, takes the same route-aware path, so a `proxy_dns`
name that does not resolve here is not refused before routing. Every
redirect is checked again on its own route. Cookies, the browser user
agent and `BlockedUrlError` are as before.

In `system` mode a guarded fetch used to ignore the system's proxy (the
`*_proxy` variables, or the OS settings on Windows and macOS) and now goes
through it, pinned; that proxy may now be `ALL_PROXY`'s, as for every
connection ([The setting](#the-setting)). A proxy that refuses CONNECT to
an IP address (some corporate Squids do), or to port 80 (a stock Squid
allows CONNECT to 443 only), fails such fetches with its refusal ("The
proxy at … refused the connection to 8.8.8.8:80 (403 Forbidden)") until
the admin turns on `proxy_dns`.

**What is left to the proxy.** Pinning keeps the address check, but the
proxy makes the connection: one that routes some public range into a
private network can still reach it. With `proxy_dns` the proxy also
resolves the name, so a name that resolves to a private address only at
the proxy, or a proxy client whose rules send private ranges direct, can
reach the proxy's own network. Once a proxy is set, the proxy decides
where these fetches can go. The hint under the switch says so.

## The API

All routes are admin-only and sync, in `routers/admin.py` beside the
off-site ones ([api.md](api.md) "Admin").

- `GET /api/admin/proxy` → `proxy_settings.public()`:

  ```json
  {"mode": "manual", "server": "http://127.0.0.1:7890", "username": "alice",
   "password_set": true, "bypass": "localhost, …", "default_bypass": "localhost, …",
   "proxy_dns": false, "managed": null,
   "system": {"proxy": "http://127.0.0.1:7897", "http": "", "from": "environment", "unsupported": ""}}
  ```

  `system` is what `system` mode finds, each proxy without its sign-in:
  `proxy`, the one an https destination takes (the system's https entry,
  else its `all` entry); `http`, the one http destinations take when it
  differs from `proxy` or `proxy` is empty (an http-only proxy carries
  http:// addresses alone, and https then connects directly), else "";
  `from` (`environment` when any `*_proxy` variable is set, else `os`);
  and `unsupported` (a system entry Gamma cannot use). `managed` is
  `"hosted"` on a hosted container, where `mode` is `system` and
  `proxy_dns` false. The password is never returned.
- `PUT /api/admin/proxy` takes any of `mode`, `server`, `username`,
  `password`, `bypass` and `proxy_dns`; a field left out stays as it is,
  so switching to `system` keeps the manual proxy for next time. Everything
  is checked before anything is written. A user name in `server` replaces
  the field's, and a password there (when not empty) the typed one. The
  saved password goes only to the proxy and the user name it was typed
  for: a password absent or empty keeps it while the proxy's host (any
  port or scheme) and the user name stay the saved ones, and otherwise
  the PUT is refused, "Type the proxy's password again: the proxy or its
  user name changed." An emptied user name drops the password, and a
  password without a user name is refused, "Add the user name this
  password goes with." Manual needs a server ("Enter the proxy's address,
  as in http://127.0.0.1:7890"). → the GET's shape. 400 with the reason,
  409 on a hosted container. Like the public URL's PUT it is refused
  cross-origin (403), because a proxy redirects all of the server's
  traffic.
- `POST /api/admin/proxy/test` takes the PUT's fields as a draft, laid
  over the saved setting as a save would, with the same password rules
  (a draft without a password uses the saved one only for the saved proxy
  host and user name), plus an optional `url`. Nothing is saved. It is
  refused cross-origin too, since it makes the server fetch through a
  proxy the request names. 400 for a draft that does not check or a `url`
  that is not http(s), or holds a sign-in.

### The Test

`proxy_settings.test` tries, in parallel threads, Gamma Cloud (the
configured issuer, first, only when cloud sign-in is on), then
`TEST_TARGETS`: OpenAI (`https://api.openai.com/v1/models`), ChatGPT
(`https://chatgpt.com/backend-api/codex/models`, the ChatGPT sign-in's
calls), Anthropic
(`https://api.anthropic.com/v1/models`), Zotero (`https://api.zotero.org/`)
and arXiv (`https://arxiv.org/`), then the admin's `url`. arXiv goes
through `net_guard.guarded_urlopen`, so the Test exercises the pinned or
`proxy_dns` path of links; the others through the trusted opener. Each is
one GET with a 10 s timeout on each socket operation (the Test as a whole
has no limit), and any HTTP status counts as reached, except one that
refuses the server's network (`net_proxy.refused_network`: OpenAI's
`unsupported_country_region_territory`, Cloudflare's challenge or block
page, told by `gamma_sync.blocked`, the one rule for a refusal by
something in front of a service, [https.md](https.md) "Failures worded"),
whose line fails with the reason ("403: it doesn't serve the country
or region this server connects from"). A result:

```json
{"name": "OpenAI", "url": "https://api.openai.com/v1/models", "route": "proxy",
 "via": "http://127.0.0.1:7890", "reason": "", "ok": true, "status": 401, "ms": 312, "error": ""}
```

`reason` is a direct route's (`loopback`, `the proxy itself`,
`bypass: 192.168.0.0/16`, `no proxy`, `system bypass`). `error` is a
`ProxyError`'s text, "Timed out after 10 s", "Couldn't look up {host}",
"The certificate wasn't trusted: a proxy that inspects HTTPS needs its
certificate installed on this server", "Blocked: {why}" for a guard's
refusal, or else the reason's own short text. `TEST_TARGETS` is a module
constant, `(name, url, guarded)`, so tests replace it.

## The pane

Settings → Administration → **Network proxy**, after Server, for admins
only ([settings.md](settings.md) "The Settings dialog"). The mode, then
under Manual the address, the sign-in and the bypass list, the name lookup
switch wherever a proxy is in use, and a Test of the unsaved draft. On the
desktop the intro adds that the app's windows and updates follow the
system's proxy settings. What it sends and how it reads is in
[settings.md](settings.md).

## Where the setting does not reach

- **The desktop shell.** Its own Chromium traffic (its windows, the remote
  health probes, the updater) keeps following the OS proxy, PAC included;
  the setting changes only the server's connections ([desktop.md](desktop.md)).
  The local server starts with the shell's environment, so `system` mode
  there reads its `*_proxy` variables, else the Windows or macOS settings.
- **Browsers and the extension.** Everything a browser loads itself
  (the app, favicons, external images, the Connector's fetches and the
  handoff of paywalled PDFs) follows that browser's proxy.
- **The iPad and Android apps.** Their embedded server's Python sees no
  device proxy (urllib reads only variables there, and the apps set none),
  so `system` connects directly. Manual works, since the setting is that
  server's own.
- **TLS-inspecting proxies.** The server verifies HTTPS against the
  operating system's trust store (truststore, [https.md](https.md) "One
  trust store"), so a proxy whose root is installed in the macOS Keychain
  or Windows' store works, as it does in the browser. On Linux and where
  truststore is off (`GAMMA_TRUSTSTORE=0`) it is OpenSSL's paths: give the
  server the root with `SSL_CERT_FILE` there, a file holding certifi's CAs
  plus the proxy's root, since the variable replaces the bundle. The
  mobile apps verify against certifi's bundle. Off-site copies (boto3)
  verify against certifi's bundle, or `AWS_CA_BUNDLE`: on Windows and
  macOS that bundle is added to the system's store, so an installed root
  works; on Linux it is used alone, so point `AWS_CA_BUNDLE` at certifi's
  CAs plus the proxy's root, or a bucket the bypass list sends direct
  fails. The proxy Test does not try S3; the Off-site copies Test shows
  this failure.

## Secrets and logs

The password is stored sealed and is write-only over the API. A
`ProxyError` never holds it. The admin Server log's scrub rules
(`logbuf.py`) mask a sign-in in any URL (`http://***@host:port`, up to the
last `@` before the host) and a Basic `Authorization` or
`Proxy-Authorization` value. Console output is not scrubbed, as before.
`publisher-sessions.key`, which seals the password, is not in an off-site
copy ([debugging.md](debugging.md#off-site-copies-in-a-bucket)); a server
restored without it reads the password as none.

## Tests

`backend/tests/test_proxy.py` covers parsing (the sign-in moved, no
password in any error, an unreadable system entry shown without its
sign-in), every route rule (loopback, the proxy itself, `none`, `system`
with stubbed `getproxies` and `proxy_bypass`, a port in `no_proxy`, an
`https://` system entry, a range matched by lookup and the lookup kept,
fake-IP answers skipped), CONNECT and SOCKS5 against stand-in proxies on
127.0.0.1 (success, 407, 403, sign-in, refusals, an unreachable proxy,
the answer's line endings, a non-HTTP answer), the trusted opener through
a proxy for https (tunnel, TLS name) and http (absolute form, Host,
Proxy-Authorization, a 407), guarded fetches pinned to the checked
address (http over `CONNECT ip:80`, the Host a forwarding proxy rebuilds)
and re-checked on a redirect, `proxy_dns`'s refusals (addresses written
as names among them) and fake-IP answers, the TLS context made at the
first https request, `system_view` (an http-only proxy), `agent_env` per
mode, `botocore_proxies`, the log scrub, and the API: admin only, the
round trip, the password sealed on disk and in no answer, the keep, drop
and type-again rules on the PUT and the Test, 400s, the hosted 409, a
cross-origin PUT, and a Test of a draft against 127.0.0.1:1.
`tests/test_net_guard.py` checks that the environment's proxy is asked
for the checked address (`CONNECT 8.8.8.8:443`, and `CONNECT 8.8.8.8:80`
for http, Host and TLS name `publisher.example`) and that `none` ignores
it. `tests/test_zotero_stream.py` checks the dial's time limit and that a
late socket is closed, `tests/test_manage_cli.py` that the commands
install the routed opener.

```
# From backend/
venv/bin/python -m pytest -q tests/test_proxy.py tests/test_net_guard.py tests/test_zotero_stream.py \
  tests/test_offsite.py tests/test_admin_logs.py tests/test_local_agents_procs.py tests/test_zotero_web.py \
  tests/test_admin_settings.py tests/test_manage_cli.py
```

The `settings` e2e scenario (`npm run e2e -- --only settings`) drives the
pane: a member has no pane, an admin's search lands on it, an `https://`
proxy is refused and the refusal clears on the next edit, a Test of
`http://127.0.0.1:1` fails every site through the proxy while a loopback
address goes direct, the save is read back after a reload with the
password never shown (emptying the user name turns the placeholder back
to "Password"), and the mode goes back to the system's.
`frontend/tests/e2e/select.mjs` runs every scenario for a change to
`proxy_settings.py` or `net_proxy.py`, as for any file under
`backend/gamma/`. No test reaches a real proxy, real certificates, the
desktop app or a hosted server.
