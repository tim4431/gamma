# Keeping a ChatGPT sign-in

Why a ChatGPT sign-in seemed to need signing in again after each update
(issue #138), and how Codex CLI renews the same tokens. Surveyed in October
2026 from Gamma's code, scratch tests against a faked token endpoint, and
Codex CLI's source (openai/codex `970111f6`: `codex-rs/login/src/auth/manager.rs`
and `oauth/`). How Gamma renews a sign-in now is in
[ai.md](../dev/ai.md#the-chatgpt-protocol-oauth).

## What was found in Gamma

- **No update erases a sign-in.** An account's entries are plain JSON in
  its users.db `ai-settings` pref. The generic prefs API refuses that key,
  cloud sync skips it, and no migration or `manage.py setup` touches it.
  The data directory survives an update everywhere: the Docker `/data`
  volume, the desktop app's workspace folder, the iPad's
  `Documents/Gamma/local`. Only shared entries depend on
  `publisher-sessions.key`. Settings shows "not connected" only when the
  access token is empty, which a restart never makes it.
- **Why updates line up with it.** An update restarts the server and
  reloads the page. The login check runs 1.5 s after the load, and it is
  usually the first ChatGPT call since the access token expired, so it is
  when the token is refreshed.
- **Every failed refresh read as an expired sign-in.** `refresh` caught
  every exception and returned None: a DNS or proxy error, the 30 s
  timeout, a 5xx, a 429, a Cloudflare page, and a real refusal alike. The
  login check then pinged with the stale token, got a 401 and said "The
  ChatGPT sign-in has expired — Sign in again". The usage line and the Test
  button said the same, and chats sent the stale token for the next 5
  minutes. The log kept only `HTTP Error 400: Bad Request`, never OpenAI's
  code. Signing in again "fixed" it by replacing a grant that was still
  good. A scratch test reproduced it: with the token endpoint unreachable
  the check said "expired" while the stored refresh token was still valid,
  and the next check renewed it with no reconnect.
- **Three ways a good grant was lost.** A failed refresh wrote back the
  dict it had read before its network call, so a sign-in made again during
  a slow refresh was overwritten with the dead tokens. A save that failed
  after a successful refresh (users.db locked past the 10 s busy timeout)
  dropped the rotated refresh token, and the next refresh presented the
  spent one. A 401 on a token that had not expired by the server's clock
  never led to a refresh at all.
- **Not fixable.** OpenAI rotated the refresh token but its answer never
  arrived (a timeout, a proxy reset, the server killed mid-refresh by an
  update), or OpenAI expired a refresh token left unused. Both need a new
  sign-in, which is now reported as such, with OpenAI's code in the log.

## How Codex CLI does it

| | Codex CLI | Gamma before | Gamma now |
|---|---|---|---|
| Refused | 401; 400 `invalid_grant`; `refresh_token_expired`, `refresh_token_reused` or `refresh_token_invalidated` at any status | every failure | as Codex |
| No verdict | everything else, kept transient: transport errors, 5xx, 429, other 4xx, an answer that doesn't parse | read as refused | `RefreshUnavailable`, retried after 60 s |
| After a refusal | cached for that login until it changes | 300 s backoff | 300 s backoff |
| Log | the status; the body is not logged, since it may echo a credential | `str(e)` | status and OpenAI's code |
| Answer without a refresh token | keeps the old one | keeps it silently | keeps it, with a warning |
| Saving the result | `persist_tokens` refuses when the stored login changed during the refresh ("Account credentials changed during refresh") | always overwrote | only over the refresh token it started from |
| A 401 from the API | `UnauthorizedRecovery`: reload the auth file, then refresh, then retry; each step once | none | the login check, Test and usage renew once and retry, but not a token a refresh got less than 5 minutes ago |
| When it refreshes | on use, from 5 minutes before the access token's `exp` (8 days after the last refresh for a token without one) | on use, from 5 minutes before expiry | unchanged |

Codex reads the error code from `error` (a string), else `error.code`, else
a top-level `code`; Gamma reads the first two. Codex's first 401 recovery
step, reloading the auth file, covers another process that refreshed
meanwhile. Gamma runs one process per data directory, so its equivalent is
the re-read under the refresh lock: a renewal finds the token another
request already renewed and uses it.

## The refresh `scope`

Gamma's refresh request sends `scope=openid profile email`. Codex CLI sent
the same for about a year, and dropped it in commit `8a59386` (#12383,
March 2026). That change added connector scopes to the login, and its
message gives the reason: "Removing scopes from the refresh request until we
have upgrade flow in place", having "Confirmed that tokens refresh with
existing scopes". So a scoped refresh was not failing. A probe with a bogus
refresh token reached token validation both ways: Gamma's form body with
the scope and Codex's JSON body both got `401 invalid_refresh_token`.
The scope is left as it is; dropping it would be a separate change.

## Still open

- The reporter's deployment, whether a proxy sits between it and OpenAI,
  and what the new `[chatgpt-oauth] refresh …` line says next time.
- A chat's own 401 is not renewed, as Codex's is; the next login check,
  Test or usage query renews it.
- A 403 challenge page from chatgpt.com on the ping still reads as `auth`.
- The Codex client's real access and refresh token lifetimes, and whether
  an always-on server whose AI goes unused needs a keep-alive refresh.
  Codex, too, renews only on use.
- The desktop app can start a second server on one data directory when
  the first misses its health probe, and each process has its own refresh
  lock (reported separately).
