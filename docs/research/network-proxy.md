# One network proxy for the server

Surveyed November 2026, against the code at the time and the products
named below. The question: should Gamma let an admin set an outbound
proxy, one for everything or one per purpose (AI, web reads, paper
downloads, translation, Zotero, Gamma Cloud), and how can the guarded
fetches of links people open go through it without losing their SSRF
checks? The setting as built is in [network_proxy.md](../dev/network_proxy.md),
which this note does not repeat.

## What Gamma did before

- **Plain `urlopen` calls followed whatever urllib found.**
  `getproxies()` reads the `*_proxy` variables, then on Windows the
  registry and on macOS the system settings; Linux, iOS and Android read
  the variables only. No Python path reads PAC or WPAD. So the desktop
  app's server on Windows and macOS already followed a manually set OS
  proxy (Clash's "system proxy" on 127.0.0.1:7897, say), and on Linux only
  the variables.
- **urllib has no SOCKS.** An https request through a `socks5://` proxy is
  sent as an HTTP CONNECT to the SOCKS port and fails; a Windows `socks=`
  entry becomes `socks4://`, which urllib cannot use. Clash's and v2rayN's
  "mixed" ports also speak HTTP, which hid this.
- **urllib's `no_proxy` matches suffixes only.** `192.168.0.0/16` in
  `NO_PROXY` does nothing, and no loopback is bypassed by default: a
  Docker container given `HTTP_PROXY` without `NO_PROXY` sends its own
  health check to the proxy.
- **Two openers were built at import** (`zotero_web._opener`,
  `sync_engine._file_opener`), so they kept the environment of the
  moment. The Zotero WebSocket read `getproxies()` itself and would have
  needed `python-socks` for SOCKS. boto3 reads the environment its own
  way. Local agents inherited the server's environment.
- **Guarded fetches ignored every proxy.** The October security review
  had them connect only to a checked numeric address, and dropped proxies
  because "the application's checks cannot constrain a proxy's
  independent DNS resolution", leaving "a separately designed trusted
  proxy policy" for later ([security-review-2026-10-31.md](security-review-2026-10-31.md)
  "DNS rebinding in guarded fetches"). Behind a mandatory corporate proxy
  every PDF download, link preview, AI web read and update check failed.
- **Fake-IP VPNs broke guarded fetches outright.** Clash, mihomo, sing-box
  and Surge in TUN mode answer DNS with addresses in 198.18.0.0/15, which
  Python counts as not global, so the guard refused every host.
- **The desktop shell's Chromium follows the OS proxy, PAC included**, and
  the sidecar does not read PAC, so the two already disagreed on PAC
  networks.

## Prior art

Every mainstream product ships one global proxy plus a bypass list:

- **VS Code:** `http.proxy` (else the environment), `http.noProxy`, strict
  SSL, a raw `http.proxyAuthorization`, and `http.proxySupport`
  (off / on / fallback / override) for extensions. The window itself is
  Chromium and follows the system, PAC included.
  [network](https://code.visualstudio.com/docs/setup/network),
  [request.ts](https://raw.githubusercontent.com/microsoft/vscode/main/src/vs/platform/request/common/request.ts)
- **JetBrains:** No proxy, Auto-detect (system or PAC URL), Manual (HTTP or
  SOCKS, host, port, "No proxy for"), optional login, and Check
  connection, which tests the settings as typed against a URL.
  [HTTP Proxy](https://www.jetbrains.com/help/idea/settings-http-proxy.html)
- **Firefox:** No proxy, Auto-detect, System, Manual, PAC URL; `<local>`
  for dotless names; "Proxy DNS when using SOCKS v5"; localhost never
  proxied. [connection settings](https://support.mozilla.org/en-US/kb/connection-settings-firefox)
- **Chromium and Electron:** modes direct, auto_detect, pac_script,
  fixed_servers, system; bypass rules with globs, `.suffix`, IP literals,
  CIDR (which matches IP literals only, never names resolving into it)
  and `<local>`; loopback and link-local bypassed implicitly; SOCKS5
  always resolves at the proxy.
  [ProxyConfig](https://www.electronjs.org/docs/latest/api/structures/proxy-config),
  [proxy.md](https://chromium.googlesource.com/chromium/src/+/HEAD/net/docs/proxy.md)
- **Zotero:** follows the system; its "Proxies" preference means
  institutional URL rewriting (EZproxy), not a network proxy, a name clash
  that matters to Gamma's readers.
  [connection error](https://www.zotero.org/support/kb/connection_error),
  [Connector preferences](https://www.zotero.org/support/connector_preferences)
- **Obsidian, Discord:** no setting; the system and `--proxy-server`.
  [forum request](https://forum.obsidian.md/t/request-to-add-http-socks-proxy-as-built-in-feature/39670)
- **Telegram Desktop:** Disable, System, or a list of saved proxies with
  their state, and one per-purpose switch, "Use proxy for calls", because
  calls are UDP. [guide](https://onlineproxy.io/blog/the-ultimate-guide-to-using-a-telegram-proxy-for-security-privacy-and-automation)
- **Logseq:** System, Direct, HTTP, SOCKS5, host, port and a Test URL; its
  issues show people typing `http://…` into the host field, Sync ignoring
  the proxy, and no proxy sign-in.
  [#8824](https://github.com/logseq/logseq/issues/8824),
  [#7581](https://github.com/logseq/logseq/issues/7581),
  [#11647](https://github.com/logseq/logseq/issues/11647)
- **Joplin:** a proxy URL under Synchronization only.
  [PR #6537](https://github.com/laurent22/joplin/pull/6537)
- **Calibre:** OS settings or the variables, HTTP only, and the detected
  proxies listed in its preferences. [FAQ](https://manual.calibre-ebook.com/faq.html)
- **Cherry Studio** (a widely used Chinese AI client): System (default),
  Custom (`http://127.0.0.1:7890`), None; a bypass list defaulting to
  loopback that takes `*.x` and CIDR. Its bugs are the ones to avoid:
  loopback sent through the proxy (local MCP broke), bare IPv6 rules never
  matching, the agent runtime ignoring the proxy until it was passed in.
  A test button was asked for; a per-mini-app proxy is unshipped.
  [settings](https://docs.cherryai.com.cn/docs/en-us/pre-basic/settings/general),
  [#13007](https://github.com/CherryHQ/cherry-studio/issues/13007),
  [#20481](https://github.com/CherryHQ/cherry-studio/issues/20481),
  [#20933](https://github.com/CherryHQ/cherry-studio/issues/20933),
  [PR #21030](https://github.com/CherryHQ/cherry-studio/pull/21030)
- **Thunderbird, Nextcloud desktop:** one global setting; a per-account
  proxy requested since 2012 and never built in Thunderbird, and built in
  Nextcloud with bugs.
  [bug 812838](https://bugzilla.mozilla.org/show_bug.cgi?id=812838),
  [#7438](https://github.com/nextcloud/desktop/issues/7438)
- **Self-hosted servers:** Gitea's `PROXY_URL` with a `PROXY_HOSTS`
  allow-list, and in its docs: "restricting the proxied target is the
  proxy server's responsibility", its host allow-list "enforced on direct
  connections only". LibreChat has one `PROXY`; Open WebUI honours the
  environment, with a separate trust switch for its web loader.
  [Gitea](https://docs.gitea.com/next/administration/config-cheat-sheet/),
  [LibreChat](https://github.com/danny-avila/LibreChat/blob/main/.env.example),
  [Open WebUI](https://docs.openwebui.com/troubleshooting/web-search/)
- **Python clients:** httpx takes a proxy or mounts, SOCKS through an
  extra; requests matches CIDR in `no_proxy`.
  [httpx](https://www.python-httpx.org/advanced/proxies/),
  [openai-python](https://github.com/openai/openai-python/blob/main/README.md)
- **Rule-based clients in China:** Clash Verge Rev's system proxy on port
  7897 (older Clash 7890), a default bypass of localhost and the private
  ranges, intranet names a known gap; mihomo's `fake-ip` DNS with a
  filter for `*.lan` and `*.local`.
  [bypass](https://clashvergerev.com/en/guide/bypass),
  [#8407](https://github.com/clash-verge-rev/clash-verge-rev/issues/8407),
  [mihomo DNS](https://wiki.metacubex.one/en/config/dns/)

Products split a proxy by purpose for three reasons only: the transport
cannot go through it (Telegram's UDP calls), a security posture (Open
WebUI's web loader, Gitea's allow-list), or the setting belongs to a
remote account (Nextcloud). Splitting by destination is otherwise left to
a rule engine: PAC, FoxyProxy, Clash's rules, git's per-URL config.

## One proxy, or one per purpose

Each proposed purpose mixes destinations that want different routes:

| Purpose | Verdict | Why |
|---|---|---|
| AI providers | No | DeepSeek, Kimi CN, Qwen CN and GLM CN want a direct route from China; OpenAI, Anthropic and OpenRouter need the proxy. A local Ollama is loopback, always direct. The need is per host. |
| Web search and reads | No | Destinations are arbitrary, so only the proxy's own rules can split them. It is also the SSRF path, which wants one clear policy, not a switch. |
| Paper downloads | Not now | The one case tied to its purpose: a campus address for subscriptions. People meet it in their browser too and fix it in the proxy client with a direct rule for publishers, which fixes Gamma as well. If reports come, one switch, "Fetch papers directly", would do. |
| Translation | No | Youdao (CN), Google (blocked in China) and Microsoft sit in one purpose. |
| Zotero | No | One destination; a bypass entry does the same. |
| Gamma Cloud and other servers | No | A NAS on the LAN or Tailscale is covered by the private ranges in the default bypass list and by a range matching names that resolve into it. Public servers work through the proxy. |

A researcher in mainland China points Gamma at Clash's port, or leaves
it on the system's settings when Clash sets the OS proxy, and Clash's
GEOIP and GEOSITE rules send domestic services direct. A corporate user
must send everything through the proxy, so per-purpose switches could
only break things. One policy is also easier to make safe than six.
**Decision: one proxy per server with a bypass list, routed by
destination.** It was built that way, without the per-call purpose tags
the survey also suggested.

## Guarded fetches through a proxy

What `guarded_urlopen` guaranteed: http(s) only; the whole DNS answer
global; a connection to a checked numeric address with no second lookup;
Host and TLS name kept; every redirect checked; no proxies; cookies per
fetch or browsing session; refusals as `BlockedUrlError`. A trusted proxy
must keep the address check where the name is actually resolved, keep the
hostname for Host and TLS, treat each redirect the same, take its address
from admin configuration only (it may be private, so it is exempt from the
check), and send its sign-in only to the proxy.

The four options:

- **A. Pinned tunnel.** Resolve and check here as before, then CONNECT (or
  SOCKS5 with an address type, never a name) to the checked address, with
  TLS naming the original host. Keeps every guarantee. Left to the proxy:
  one that routes a public range internally. Costs: corporate Squids may
  refuse CONNECT to IP literals or non-443 ports, and it cannot work where
  local DNS is poisoned, proxy-only or fake-IP.
- **B. The proxy resolves.** Lexical checks and a local check where
  resolution works, then CONNECT by name. The address check then rests on
  the proxy, which is fine for an egress-filtering proxy (Smokescreen,
  Squid with deny-private ACLs) but not for a local Clash, whose default
  rules send private ranges direct: a rebinding name reaches the server's
  own LAN. Gitea's stance.
- **C. Resolve through the proxy, then pin.** DNS over HTTPS to a fixed
  resolver through the tunnel, check the answer, then A. Keeps the pin
  where local DNS fails, at the cost of a round trip, a resolver
  dependency and rules by domain that may not match IP-literal CONNECTs.
- **D. Status quo.** Guarded fetches stay direct and the pane says so,
  with a specific error for fake-IP answers.

The surveys disagreed: one recommended B with checks ("never pin an IP
through the proxy"), another D by default with A as an opt-in. **Built: A
for every guarded fetch the route sends through the proxy, and a checked
B as the admin's opt-in**, "Let the proxy look up names" (`proxy_dns`):
dotless and local-only names refused by form, a local lookup that refuses
private answers outside the fake-IP range, and a name that does not
resolve here left to the proxy. A keeps the October guarantees by default
and makes guarded fetches work behind corporate proxies that allow IP
CONNECTs; D would have left those users where they were. The opt-in
covers proxy-only DNS and fake-IP VPNs, and its hint says that the proxy
then decides what these fetches reach. C was not built. A pinned http URL
goes through CONNECT to the checked address on port 80, as option A
proposed. Sending it in absolute form naming the address was tried
first and dropped: a proxy forwards the Host the request-target names,
so the origin got the address instead of its name. The cost is a stock
Squid, which allows CONNECT to 443 only: such fetches fail there with
the proxy's refusal until `proxy_dns` is on.

## Other decisions

- **Storage like Off-site copies:** a module of its own
  (`proxy_settings.py`), its own routes and users.db key, a write-only
  sealed password and a Test that tries unsaved values, rather than more
  fields on `/api/admin/settings`.
- **Never through `os.environ`.** Writing the variables would leak the
  sign-in into every child's environment and would not reach openers
  already built. Every client reads the policy as it connects instead.
- **SOCKS5 written in `net_proxy`** (the greeting, RFC 1929 and the
  CONNECT are small), so no new dependency (`python-socks`, PySocks).
- **No `https://` proxies, PAC or socks4** for now; each is refused with a
  message (PAC in the pane's hint). A system `https://` entry is read as
  urllib read it, a plain HTTP proxy. Proxy sign-in is Basic (HTTP) or
  RFC 1929 (SOCKS5) only. NTLM and Kerberos are not detected: such a
  proxy's 407 reads as a refused sign-in, and
  [network_proxy.md](../dev/network_proxy.md) points to a local helper
  such as Px or cntlm.
- **`ALL_PROXY` counts under "Use system settings"**, as in curl: urllib,
  websockets and botocore ignored it, so a server whose environment sets
  only `ALL_PROXY` now sends its connections through that proxy.
- **Hosted containers locked to the operator's environment.** A private
  proxy address on the fleet network would let an owner, and through
  guarded fetches every member, reach other tenants; a custom CA would
  let one read the container's own account-server traffic.
- **The desktop shell left alone.** Its Chromium sessions keep the OS
  proxy. Applying the setting there needs `session.setProxy` on the
  default and updater sessions, a `login` handler for proxy sign-ins, and
  a two-way channel from the web Settings that a remote server's page
  must not be able to use, since it could route the whole app through a
  proxy of its choosing.

## Still open

- **The desktop shell's own sessions:** the setting applied to Chromium
  (default session, `electron-updater`), proxy sign-in through `login`,
  LAN names of registered servers added to Chromium's bypass (its CIDR
  rules match no names), and shell error messages for
  `ERR_PROXY_CONNECTION_FAILED` and its kin.
- **PAC:** Python cannot run it. The shell could pass
  `session.resolveProxy()` answers to its local server.
- **TLS-inspecting proxies:** done with `truststore`: the server verifies
  against the operating system's store, so a root kept only in the macOS
  Keychain counts ([https.md](../dev/https.md) "One trust store"). A
  "Custom CA certificate" field stays open, for Linux, where the store is
  OpenSSL's paths; hosted servers must never get it.
- **Fake-IP on the direct route:** a fake-IP answer is accepted only when
  the fetch goes through the proxy with `proxy_dns`. A guarded fetch the
  route sends direct on a machine with a TUN-mode VPN is still refused as
  an internal address, and gives no error naming the cause.
- **S3 over SOCKS:** boto3 has no SOCKS support, so off-site copies go
  direct under a SOCKS5 proxy.
- **The OS's bypass list for the agents:** a proxy only the OS knows
  reaches the local agents with `NO_PROXY` from the default list, not
  Windows' `ProxyOverride` or the macOS exceptions, which `NO_PROXY`
  cannot always express.
- **Zotero storage URLs** are checked, then fetched by the trusted opener,
  which through a proxy asks for the name: the check-then-connect gap
  that existed before, now through the proxy.
- **Slow DNS:** under Manual with ranges in the list, a new name is
  looked up locally once a minute, which can stall where DNS hangs, and
  the Test has no overall time limit.
- **Purpose tags** on every outbound call, for the Test, error messages
  ("Bypass the proxy for api.deepseek.com") and a later "Fetch papers
  directly" switch.
- **Console logs** (Docker, the desktop server log, the mobile
  `server.log`) are not scrubbed; only the admin Server log is.
- **The device's proxy on the iPad and Android:** the embedded server sees
  none; the shells could pass it in.
- **Verified only against stand-in proxies** on 127.0.0.1. No real proxy,
  real certificates, desktop app or hosted server has been tried.
