# The Gamma Cloud account server

The one new service in the Gamma Cloud plan ([todos/gamma-cloud-plan.md](../../todos/gamma-cloud-plan.md)):
a small web service at `account.gammapdf.com` that owns who a person is,
signs them in to any Gamma server through OpenID Connect, and (later)
their plan, billing and hosted container. It holds no pages, files or
notes, and a Gamma server never calls it on a data request: an ID token is
verified locally with the published keys. Code: `cloud/` (package
`gammacloud`, entry `cloud/app.py`, CLI `cloud/manage.py`), tests
`cloud/tests/`. It imports nothing from `backend/`. The three small pieces
it shares with Gamma are copies, so the account server can move to its own
repository without a change: the fixed-window rate limiter, the PKCE rules
and the public-URL rules (`servers.norm_url`, from
`server_settings.validate_public_url`). The rate limiter has drifted from `backend/`'s: it drops
`on_first_exceed` and prunes stale keys.

Status: **v0**, plus the preference profile, the linked-server list and
the username lookup (the account server's half of steps 7 and 8 of the
plan). Not built: Stripe and provisioning (v1). Plans are a column an
admin sets. The consumer side, "Sign in with Gamma Cloud" on every Gamma
server, is under "The Gamma side" below.

## What it owns

| | |
|---|---|
| accounts | e-mail, username, password (bcrypt; none for an account made through Google/GitHub), display name, plan, admin flag, soft deletion; the random account id is the identity, e-mail and username both change |
| outside sign-in | Google (OIDC + the one-tap prompt) and GitHub (OAuth), linked to accounts |
| portal | sign in / register / verify / reset, then Overview, Devices, Settings and (admins) Admin — server-rendered HTML over the JSON API |
| identity for Gamma servers | an OIDC provider: authorize (PKCE), token, userinfo, JWKS, revoke, discovery; a self-hosted server connects itself for a client of its own |
| preference profile | a person's settings as a few JSON values the Gamma servers they sign in to pull and push |
| server list | the Gamma servers a person linked their identity on |
| admin | accounts, invites, OIDC clients, the audit log — API and `manage.py` |

## Running

```bash
cd cloud
pip install -r requirements.txt -r requirements-dev.txt   # the backend venv already has them all
python manage.py setup                                     # cloud.db + the signing key
python manage.py create-account you@example.org you --admin --verified
uvicorn app:app --port 9002 --reload                       # http://127.0.0.1:9002
python -m pytest tests -q
```

Configuration is env only, `GAMMA_CLOUD_*` (`cloud/gammacloud/config.py`
lists every variable):

- the data directory;
- the public URL — the OIDC issuer; the request's Host is never trusted;
- the registration mode: `open` / `invite` / `closed`, default `invite`;
- the mail backend: `console` logs the links, `smtp` sends them;
- the Turnstile secret, off until set;
- the desktop client id;
- the Google and GitHub OAuth clients — a provider is off until both its
  id and secret are set; setup in the deploy README;
- the free share host's address (`GAMMA_CLOUD_SHARE_HOST_URL`, empty = none),
  which Gamma servers read to know where pages are published (below).

The Docker image (`cloud/Dockerfile`) runs uvicorn on 9002. The client
address (rate limits, the address on a device or browser row) is
Cloudflare's `CF-Connecting-IP`, else the connection's peer
(`ratelimit.client_ip`); `X-Forwarded-For` is never read, since its first
hop is whatever the client wrote and behind Caddy it only names
Cloudflare. The header is only as good as the rule that the origin answers
Cloudflare alone (the deploy README's origin lock-down).

## Data

One SQLite file, `cloud.db`, WAL, opened through `db.connect()`. Its
`PRAGMA user_version` is `db.SCHEMA_VERSION`; `db.SCHEMA` is always the
current shape, applied on connect to a fresh file, and an existing file is
upgraded by the numbered steps in `db.STEPS` (`ensure_current()` at
startup and `manage.py migrate`) with a copy taken first and a newer file
refused — the same rules as Gamma's data directory
([migrations.md](migrations.md)). The data directory is the secret: it holds
the signing keys and every token hash.

| table | what |
|---|---|
| `accounts` | `id` (random, the OIDC `sub`; never changes), `username` (unique; the username on every Gamma server, a paid container's hostname label — `accounts.RESERVED_USERNAMES` keeps the names Gamma and the web use), `email` (unique), `email_verified_at`, `password_hash`, `display_name`, `plan`, `is_admin`, `deleted_at`, `app_signed_in_at` (the first sign-in to a Gamma app or server; step 3) |
| `identities` | an account's Google/GitHub link: (`provider`, `subject`) → account, the provider's address at the last sign-in |
| `external_logins` | one Google/GitHub sign-in in flight (15 min): `redirect` while at the provider, `signup` while the username form waits; keyed by the hash of the `gc_ext` cookie (step 2) |
| `portal_sessions` | the portal cookie's hash; sliding 30 days, newest 20 per account |
| `email_tokens` | verify / reset / change-email links: hash, kind, expiry, `used_at`; one live link per (account, kind) |
| `invites` | codes with uses left and the plan they grant |
| `oauth_clients` | confidential OIDC clients with exact redirect URIs: share-host and container ones an admin made, and `server` ones a person connected (`owner_account_id`, step 5); the desktop client is built in, not a row |
| `server_connects` | a server connection a person approved, waiting for the server to fetch its client: the code's hash, the account, the server's address, the PKCE challenge (2 min, single use; step 5) |
| `oauth_requests` | a sign-in in progress on the authorize page (10 min) |
| `oauth_codes` | authorization codes (2 min, single use) and what the exchange issued (`grant_id`, `access_hash`), so a replay revokes exactly that |
| `grants` | one per device: the rotating refresh token's hash, 90 days from the last rotation, `revoked_at`, the client's `device_id` and `device_name` (a new sign-in with the same `device_id` replaces the grant) |
| `refresh_history` | every refresh token a live grant rotated away from, with when: the retry window and reuse detection |
| `access_tokens` | opaque bearer tokens (1 h), tied to their grant |
| `signing_keys` | Ed25519 private keys; the newest unretired one signs, a retired one stays published a week |
| `audit` | every account-changing event |
| `prefs` | the preference profile: (`account_id`, `key`) → `value` (JSON text) and `updated_at`, the version (step 4) |
| `servers_linked` | a Gamma server an account linked its identity on: (`account_id`, `url`) → `name`, `linked_at`, `last_seen_at` (step 4), `grant_id` — the grant of the token it last registered with (step 5) |

Every secret at rest is a SHA-256 of a long random token
(`db.token_hash`); nothing in the file can be replayed. Timestamps are
fixed-width UTC strings with a `Z`, so they compare as strings.

## Deploying

[cloud/deploy/README.md](../../cloud/deploy/README.md): the `gamma-cloud`
image on a VPS, `compose.yml` running it with Caddy for TLS behind
Cloudflare DNS; `compose.tunnel.yml` swaps Caddy for a Cloudflare Tunnel on
a host without a public address. The state is the `data/` folder. The
README covers `.env.example` (public URL, SMTP, Turnstile, hostname), the
first admin and invites, the Cloudflare rate rules, updating and rollback.
The website links here: the
header's **Sign in** and the `/login`, `/account`, `/signup` short links
([sites/README.md](../../sites/README.md)).

## Registration and the portal

`routers/accounts.py` is the JSON API under `/api`; `routers/portal.py`
serves the pages from `pages.py`: small server-rendered shells whose
inline script posts JSON to the API — no framework, no build. The CSRF
protection is `app.same_origin`: a request that changes anything (every
method but GET/HEAD/OPTIONS, except the OAuth `/token` and `/revoke`) must
carry `Sec-Fetch-Site: same-origin`, or, from an older browser, an
`Origin` equal to the public URL; a request with neither is not from a
browser. `SameSite=Lax` alone would not do: it does not tell
account.gammapdf.com from a sibling `*.gammapdf.com` page such as a hosted
container, and a POST without a JSON body needs no CORS preflight. There is
no separate token. The pages' buttons go through one script helper, `act`:
a failure is shown next to the button and the button comes back; a lost
session goes to `/login?next=` and returns to the page.

Two shells in the gammapdf.com palette (`sites/site/styles.css`), light
and dark, in the quiet bordered look of a workspace tool: the **auth**
shell (a centred card: sign in, register, verify, reset, the authorize
page) and the **app** shell (a sidebar and a content column):

- **Overview** (`/`): a greeting with username, plan and admin tags.
  A *Get started* checklist (account created, e-mail confirmed, signed in
  from a Gamma app — `app_signed_in_at`, so signing everything out does not
  undo it) with a progress bar, hidden once all three are done. Then
  *Gamma servers*, the first five of the Devices page's list (below)
  without its buttons. Beside them sit a plan
  card (a placeholder pointing at self-hosting until hosted servers exist)
  and an account summary: username, e-mail state, member since, and the
  account id with a copy button. The id is what Gamma servers key on; it
  never changes. `?mail=failed` (registration could not send the mail)
  changes the verify notice.
- **Devices** (`/devices`): up to three lists.
  - *Gamma servers*: one row per server, the server list and the sign-ins
    merged (`servers.merge`). A listed server carries the live grant it
    registered with (`servers_linked.grant_id`); a live grant no listed
    server names (a server without a public address) is a row of its own,
    titled by the machine's name when it sent one (`device_name`), else the
    client's name. A public address's name links to it; the desktop app's
    loopback address is not a link, and its row is named after the machine
    ("Gamma desktop app" under it). Then the system and version from the
    agent (a Gamma server sends `Gamma/<version> (<system>; <its address>)`),
    the linked or sign-in date and the last address. At the row's end the
    last activity — the later of the server's last check-in and the grant's
    last refresh or access-token use, the latter written at most every 10
    minutes (`config.LAST_ACTIVE_TOUCH`) — and *Sign out*, which revokes the
    grant and takes the server off the list. A listed server without a
    live grant shows *Signed out* and *Remove* (`POST /api/servers/remove`).
  - *Servers you connected* (only when there are any): the `server`
    clients this account owns ("Connecting a server" below), each with
    *Disconnect* (`POST /api/connected/{client_id}/disconnect`, behind a
    confirm), which deletes the client and with it every grant it holds.
  - *Browsers*: the portal sessions (`sessions.of_account`), this one
    marked, the others with *Sign out* (`POST /api/sessions/{id}/revoke`,
    the id being the head of the token's hash).

  A row signed out leaves in place. *Sign out everywhere else* is
  `accounts.revoke_everything` behind a confirm; it also drops every
  listed server that registered with a grant. The page says what
  signing a server out does: its key stops working, and the server
  ends the sessions it opened with that key at its next hourly grant check
  ("The Gamma side" below). Dates are UTC on the server and shown in the
  viewer's time zone by the page's script (`<time datetime>`, `data-at`).
  On a phone the rows stack: the details take the width, the time and the button go
  under them.
- **Settings** (`/settings`): labelled rows.
  - Display name.
  - **Username**: the password field and the button appear once the name
    is edited. The rules are those of registration; a taken or reserved
    name is refused. Gamma servers keep their own account rows and pick
    the new name up as a claim on the next sign-in.
  - E-mail change, confirmed at the new address; the password field is
    revealed the same way.
  - Password.
  - Deletion in a danger zone, its form revealed by a first click.
- **Admin** (`/admin`, `is_admin` only, 404 otherwise): four tabs.
  - Accounts: search by username, e-mail or id, paged; plan select,
    verify, resend, admin on/off, rename, delete. A deleted account (the
    `deleted` pill) offers only Restore and Purge now.
  - Invites: create with uses, plan and note; delete.
  - Clients: the OIDC clients of hosted servers — create (the secret is
    shown once as the two env lines a container needs) and delete. A
    `server` client shows the account id that owns it.
  - The audit log.

  All of it is the `/api/admin/*` API below; `manage.py` does the same
  from the shell.

- **Register** (`POST /api/register`): e-mail, username, password, an invite
  code in `invite` mode, a Turnstile token when configured. Rejected
  attempts count toward the per-IP limit. The account starts unverified,
  the verify mail goes out, and the browser is signed in so the account page
  can resend the mail. Taken e-mail or username answers 409 with a message —
  a deleted account keeps both through the grace period. Every mail goes
  out after the commit, so a slow mail server never holds cloud.db's write
  lock; a registration whose mail fails still succeeds, answering
  `mailed: false`, and the page lands on `/?mail=failed`. The other mails
  (resend, reset, e-mail change) answer 503 when they fail.
- **Verified e-mail is the gate.** An unverified account can use the
  portal but the authorize page refuses to sign it in to any Gamma server
  and shows the verify notice instead. That is the one abuse control a
  hosted Gamma relies on.
- **Sign in** (`POST /api/login`): e-mail or username plus password; limits
  per IP and per name, reset on success. An account without a password is
  refused like a wrong password.
- **Reset** (`/api/reset/request` → mail → `/api/reset/confirm`): the
  request answers the same whether the address exists. Confirming sets the
  password, marks the e-mail verified (the mail reached them), signs every
  session and device out, and signs this browser in.
- **Change e-mail**: the link goes to the new address; the old one is told
  afterwards. **Change username** (`POST /api/me/username`, password
  required, a few times a day): the account id stays, so nothing linked to
  it moves.
- **Change password** and **sign out everywhere** revoke every portal
  session, every grant and every code not yet exchanged
  (`accounts.revoke_everything`), keeping only the browser that asked.
- **Delete** (`/api/me/delete`, password required) is soft: it sets
  `deleted_at`, clears the password, revokes everything and drops the
  preference profile and the server list. `manage.py purge-deleted --days
  30` removes the rows later (nothing schedules it). Within the grace
  period an admin can **restore** it (`POST /api/admin/accounts/{id}/restore`,
  `manage.py restore-account`): the row comes back with its id, name and
  e-mail, but not what the delete dropped, so the person signs back in
  through a password reset or Google/GitHub on the same e-mail. **Purge
  now** (`POST /api/admin/accounts/{id}/purge`, `manage.py purge-account`)
  takes only a deleted account and frees its name and e-mail at once. Tearing down a paid container is the
  provisioner's job (v1).
- `PATCH /api/admin/accounts/{id}` takes `plan`, `is_admin`, `verified`
  and `username`; the rest of the admin API is listed under "Admin".
- `GET /api/me`: the account, the signed-in devices (portal session only),
  `share_host` (the share host's address, "" when none is configured) and
  `servers`: the provisioned ones (none until v1), then the linked ones,
  latest seen first, each `{url, name, kind: "linked", local, linked_at,
  last_seen_at}`. It also accepts any bearer access token, which is how a
  Gamma sidecar discovers the person's servers.
- The account itself (profile, password, e-mail, username, deletion,
  devices) and all of `/api/admin` are portal session only: a token minted
  for a Gamma server can never change the account. What a token may do is
  the next section.

### What Gamma servers call with a token

`routers/profile.py`, over `prefs.py` and `servers.py`. A state change
here from a browser still has to pass `app.same_origin`; a Gamma server's
own call carries neither `Sec-Fetch-Site` nor `Origin` and passes.

| method | path | auth | answer |
|---|---|---|---|
| GET | `/api/me/prefs` | token with `prefs`, or portal session | `{prefs: [{key, updated_at}]}`, by key |
| GET | `/api/me/prefs/{key}` | same | `{value, updated_at}`; 404 when unset |
| PUT | `/api/me/prefs/{key}` | same | body `{value, updated_at?}` → `{updated_at}`; 409 `{detail, value, updated_at}` when the stored one is newer |
| DELETE | `/api/me/prefs/{key}` | same | `{ok, removed}` |
| POST | `/api/me/servers` | any access token | body `{url, name}` → `{server}` |
| DELETE | `/api/me/servers` | any access token | body `{url}` (or `?url=`) → `{ok, removed}` |
| GET | `/api/lookup/username?u=` | any access token | `{sub, username}`; 404 otherwise |

**The preference profile** is one JSON value per key: at most 64 KB
serialized (413 past it), 20 keys per account (a 21st is a 400), keys of
1–40 `[a-z0-9-]` (a 400 otherwise). `updated_at` is the version,
per-key last-writer-wins. A write may carry the time the change was made,
any ISO 8601 time; no time means now. It is stored in the database's
fixed-width UTC form, and a time in the future is clamped to now so a fast
clock cannot pin a value. A write older than the stored time is refused
with a 409 carrying the stored value, and the writer takes that side. The
same time wins, so a retry is harmless. The answer's `updated_at` is what was stored, the one the
writer keeps. Deletion is not versioned: the key is gone, and a server
still holding an older copy can write it back. Writes (PUT and DELETE) are
limited to 120 per 10 minutes per account (429 with `Retry-After`) and are
not audited, since a preference is not an account change. A token without
the scope answers 403 with
`WWW-Authenticate: Bearer error="insufficient_scope", scope="prefs"`.
Deleting an account drops its profile at once.

**The server list.** A Gamma server registers itself when a person links
their identity there and removes itself on unlink. Each registration
records the grant of the token it came with (`grant_id`), which is how the
Devices page shows a server and its sign-in as one row; signing that grant
out on the portal deletes the row, and so does *Sign out everywhere else*
for every row that has a grant. It sends its confirmed
public URL and its display name (at most 80 printable characters; empty
means the host). Posting again refreshes the name and `last_seen_at`, so a
server may check in periodically. The URL follows Gamma's own public-URL
rules (`servers.norm_url`): `https://host[:port]` with no path, query or
fragment, or plain HTTP for a loopback host. A trailing slash is dropped,
the host lowercased and a default port removed; anything else is a 400.
Both calls take any access token (every token carries `openid`) and never
the portal session: registering is what a server does, not a person. A
confidential client may only name an address on the origin of one of its
redirect URIs (a 403 otherwise, on POST and DELETE alike), so a container
cannot put another site on someone's list. The public desktop client may
name any address, since every sidecar is that client. A loopback address
is one row per account whatever the machine: two laptops on the same port
share it. At most 50 servers per account, 60 writes an hour. Deleting an
account drops its list at once.

**The username lookup** answers the account id for an exact username,
which is how a container admin invites `alice` before she ever signed in
there. Only a verified, live account is found; a deleted, unconfirmed or
unknown name, a prefix and an e-mail address are all the same 404. It is
an enumeration surface: anyone holding an access token can test whether a
username exists. Usernames are semi-public already, since they label a
paid container's hostname. So the limits are tight: 30 per 10 minutes per
IP (counted before the token is checked), 20 per 10 minutes per token and
60 an hour per account. The answer carries only the id and the name, never
the e-mail, display name or plan.

### Sign in with Google and GitHub

`providers.py` is the wire (authorize URLs, the code exchange, verifying
Google's ID token against Google's keys, reading GitHub's `/user` and
`/user/emails`), `identities.py` the rules, `routers/external.py` the
endpoints. The sign-in, register and authorize pages show the providers as
tiles under the password form ("or continue with"); with Google on, its
one-tap prompt opens as well (FedCM in Chrome: "Sign in to … with
google.com"). The pages that load Google's script send
`Referrer-Policy: strict-origin-when-cross-origin`, which it needs; every
other page keeps `no-referrer`.

- **The round trip.** A tile posts `POST /api/oauth/{provider}/start`
  (JSON, so no other site can start a sign-in for the browser) with
  `next`, a pending authorize `request_id`, or `link`; the answer is the
  provider URL plus the `gc_ext` cookie. The provider returns the browser to
  `GET /oauth/{provider}/callback`. The OAuth `state`, the PKCE verifier and
  the OIDC nonce are all derived from the cookie's token
  (`identities.derive`), so the row stores only the token's hash, the state
  the provider sees reveals nothing, and a callback in another browser
  fails. Cancelling at the provider returns to where it started.
- **One tap** (`POST /api/oauth/google/one-tap`): the credential is a
  Google ID token; its nonce must be the one derived from the page's
  `gc_tap` cookie, so a token lifted from elsewhere cannot be replayed.
- **Which account** (`identities.resolve`): a linked identity signs in to
  its account. Otherwise an address the provider is authoritative for
  (Google: Gmail or a Workspace account, the `hd` claim — Google's own
  rule; GitHub: the verified primary address) links to the account that
  has it. If that account never confirmed its address, its password was
  chosen by someone who never proved they own it: the password is cleared,
  everything signed out, the address marked confirmed. An address the
  provider is not authoritative for never attaches to an existing account
  (the page says to sign in and connect from Settings). A provider without
  a verified address is refused.
- **A new person** lands on `/signup/finish`: the provider's address, a
  suggested username (the GitHub login or the address's local part, made
  valid and free), the invite code in `invite` mode; `POST
  /api/oauth/signup` creates the account with that address confirmed, no
  password, and the identity linked. `closed` registration refuses instead.
- **A Gamma server's sign-in** that started on the authorize page finishes
  right after: the code goes to the server without another click (the flow
  was started by this browser's own JSON call). When it cannot — the request
  expired, the address is unconfirmed, the person cancelled — the browser
  goes to `/authorize/resume?request_id=`, the authorize page again.
- **Settings → Connected accounts**: connect (the same round trip with
  `link`, back to `/settings?connected=`) or disconnect (`POST
  /api/me/identities/{provider}/unlink`, refused when it would leave no way
  in). One identity per provider per account, one account per identity.
  An account without a password sees *Set a password* instead of *Change*,
  and confirms the username, e-mail and delete forms with its session alone
  (`accounts.confirm_ok`). Deleting an account drops its links at once.

Rate limits are the in-process fixed windows of `ratelimit.py` (per IP —
`client_ip`, see "Running" — per name, per account; the `oauth-callback:ip`
window is shared by `one_tap`); Cloudflare's rate rules in front are the
first line. Mail (`mail.py`) has three backends; every message is plain text plus
an HTML alternative from `mail.compose` (portal palette, a button for the
link with the raw URL under it, inline styles only).

## The OIDC provider

`oidc.py` is the logic, `routers/oidc.py` the wire: discovery at
`/.well-known/openid-configuration`, `/jwks`, `/authorize`, `/token`,
`/userinfo`, `/revoke`. Authorization code with PKCE S256, required for
every client; `response_type=code` only. The discovery document carries one
key outside OIDC, `gamma_share_host`: the share host's address (""
without one), which is how a Gamma server learns where to publish without a
token.

**Clients.** The *desktop* client (`GAMMA_CLOUD_DESKTOP_CLIENT_ID`,
default `gamma-desktop`) is public and built in: every local Gamma sidecar
is this client, nothing is registered per install, and its redirect URI
must be `http://127.0.0.1:<any port>/api/auth/cloud/callback` (or
`localhost`, `[::1]`), a loopback the sidecar itself serves; it may always
ask for `offline_access`. A Gamma server at a public address cannot be
this client: it needs a confidential one. *share-host* and *container*
clients are confidential rows an admin makes (`manage.py create-client` or
`POST /api/admin/clients`, the secret shown once); a *server* client is
one a person made by connecting their own server (below). All three have
exact `https` redirect URIs and authenticate with `client_secret_post` or
`client_secret_basic`. They may ask for
`offline_access` only together with `prefs`, since a server syncing
someone's preference profile between sign-ins needs a refresh token;
without it the request is an `invalid_scope` error.

**Scopes.** `openid` (required), `email`, `profile` (OIDC's: the display
name), `offline_access` (a refresh token) and `prefs` (the preference
profile under `/api/me/prefs`). An access token keeps the scope it was
issued with (`access_tokens.scope`; a refreshed one gets the grant's),
which is what the profile endpoints check.

**The authorize page.** `GET /authorize` validates the client and redirect
URI first (a bad one is shown, never followed), the rest is redirected
back as an OAuth error, and a valid request is stored as pending. A
signed-in, verified person sees a confirm card (`pages._consent_page`)
under a Gamma Cloud brand strip. It reads "Sign in to *client name*", with
the asker's address under it: a hosted server's redirect-URI origin, or
"on this computer" for the desktop app. Below come the account as an
avatar row, what the requested scopes give in plain words
(`pages.SCOPE_WORDS`, never scope ids), a primary *Continue* with *use
another account* and *cancel* as text links, and a footnote pointing at
Devices. A signed-out person signs in on the page
(`POST /authorize/login`, which also sets the portal cookie so the next
server is one click); an unverified person sees the verify notice. Nothing
is granted per scope: every client is first party, and the card only says
who asks and what it gets.

**Tokens.** `POST /token` with `authorization_code` checks the code's
client, redirect URI, expiry and PKCE verifier; a replayed code revokes
what its first exchange issued — that grant or that access token, nothing
else of the account. The answer is an opaque access token, an ID token and,
with `offline_access`, a refresh token. A client may send `device_id`
(8–64 of `[A-Za-z0-9_-]`, else dropped) and `device_name` with the code; a
new grant revokes the live grant of the
same account, client and `device_id`, so a machine that signs in again is
one row on the Devices page, not two.

`refresh_token` rotates: the refresh token moves to `refresh_history`, the
grant's access tokens die, a new pair is issued. For
`config.REFRESH_REUSE_GRACE` (60 s) the replaced token still rotates once
more, for a client whose answer was lost; used later, it means two parties
hold the device's key, and the grant is revoked (`grant.reuse` in the
audit). A client must therefore refresh one request at a time and keep the
newest token. Revoking a device on the account page, changing the password
or deleting the account revokes the grant; `/revoke` accepts the current
refresh token or any one the grant rotated away from.

A code exchange and a refresh take cloud.db's write lock before they read
(`db.begin_write`), so a revoke cannot land between the check and the new
tokens. `/token` and `/revoke` parse the form on the event loop and do the
rest in the threadpool: a request waiting for the lock never holds up the
server. A lock held past `db.BUSY_TIMEOUT` (10 s) answers 503 — on `/token`
as `temporarily_unavailable`, elsewhere with `Retry-After` — never a stack
trace.

**The ID token** is signed EdDSA with the active key (`kid` in the header)
and carries `iss`, `sub` (the account id), `aud` (the client id), `exp`
(10 min), `iat`, `auth_time`, `nonce`, and the identity claims:
`preferred_username` and `plan` always (a Gamma server needs the username
and the quota),
`email` + `email_verified` for the `email` scope, `name` for `profile`.
`/userinfo` answers the same claims for an access token. Keys are Ed25519
in `signing_keys`; `manage.py rotate-key` retires the active one, which
stays in the JWKS for a week so tokens it signed still verify.

**What a Gamma server does with it** is the client side below.

### Connecting a server

`connect.py` + `routers/connect.py`. A self-hosted Gamma server at a
public address gets its confidential client without anyone copying an id
and secret by hand:

1. The server's admin presses *Connect* in Gamma's Settings → Server →
   Sign-in. The browser comes to
   `/connect-server?server=<origin>&state=…&code_challenge=…` (both
   addresses are in the discovery document as
   `gamma_server_connect_endpoint` and
   `gamma_server_connect_token_endpoint`).
2. Signed out goes to `/login?next=` and comes back; an unconfirmed e-mail
   sees the verify notice. A signed-in, verified person sees a card in the
   authorize page's style: "Connect *host*", the address, the account the
   connection will belong to, what connecting does, then *Connect*
   (`POST /connect-server/continue`, 20 an hour per account) or *Cancel*
   (back to the server with `error=access_denied`).
3. *Connect* stores a one-time code (`server_connects`, 2 min) and sends
   the browser to `<origin>/api/auth/cloud/connect/callback?code=&state=`.
4. The server posts the code, its PKCE verifier and its own address to
   `POST /api/servers/connect/token` (a form, from anywhere, no cookie; 30
   per 10 minutes per IP) and gets `{client_id, client_secret}`. The code
   is spent by its first try, right or wrong; an expired code, another
   address or a verifier that does not match is a 400.

The code only ever travels to the address being connected, so finishing
the flow proves control of that address, which is what an admin checked
when making a client by hand. Any verified account may connect a server.
The client is kind `server`, named after the host, registered for
`<origin>/api/auth/cloud/callback` and owned by the approving account
(`owner_account_id`), whose Devices page lists it with *Disconnect*.
Connecting the same address again from the same account keeps the client
id and rotates the secret (`oidc.rotate_secret`), so people signed in there
stay signed in; the old secret stops working at once. An account owns at
most `connect.MAX_CLIENTS` (10) server clients. The origin must be `https`
and not a loopback one: a server on this computer is the desktop client.
A client outlives its owner's deletion; admins see the owner in the
Clients tab and can delete it there.

## Admin

`manage.py`: `setup`, `migrate`, `backup`, `list-accounts`,
`create-account`, `set-password`, `set-admin`, `set-plan`, `verify`,
`delete-account`, `restore-account`, `purge-account`, `purge-deleted`, `invite`, `invites`, `create-client`,
`clients`, `delete-client`, `rotate-key`. Every command but `setup` and
`migrate` refuses an outdated `cloud.db`. `/api/admin/*`
(`routers/admin.py`, admins through a portal session only): search and
patch accounts (plan, admin, verified), resend a verify mail, delete;
invites; OIDC clients; the audit log. The portal's Admin page, the API and
`manage.py` are one surface: the page and the CLI call the same functions.

## Tests

`cloud/tests/`:

- `test_accounts.py`: the registration, verify, reset, e-mail change,
  deletion and rate-limit flows, the pages.
- `test_oidc.py`: discovery and JWKS, the full desktop PKCE flow with a
  decoded ID token, refresh rotation, code replay, redirect and PKCE
  checks, the unverified gate, sign-in on the authorize page, cancel, a
  confidential client with basic auth and revoke, key rotation.
- `test_admin.py`: gating, the admin flows, that a bearer token never
  reaches the admin API.
- `test_manage.py`: the CLI, purge, the newer-file refusal.
- `test_external.py`: Google/GitHub with the provider stubbed — signup,
  linking by a trusted address, claiming an unconfirmed account, the
  authorize page's path, state and `next` checks, one tap with a real
  RS256 token, connect/disconnect, accounts without a password, the
  step-2 upgrade.
- `test_devices.py`: the grants behind the Devices page — a revoke landing
  while a refresh waits for the lock, `/api/health` answering while a
  `/token` request waits, code replay touching only its own grant,
  sign-out-everywhere voiding pending codes, the refresh retry window and
  reuse detection, one grant per `device_id`, last activity, the page,
  signing one browser out, the 503, the same-origin check, the client
  address, the audit, the checklist, `/login?next=`, the step-3 upgrade.
- `test_connect.py`: connecting a server — the approval page and its
  refusals, the code's single use and PKCE, the client it makes, reconnect
  rotating the secret while grants live on, the per-account limit,
  disconnect, and the Devices page's one list of servers.
- `test_profile.py`: the preference profile (the scope or the portal
  session, last-writer-wins and its 409, time normalization and clamping,
  the caps, the write limit), a confidential client's refresh token with
  `prefs` (rotation, one grant per device, revocation, the Devices row),
  the server list (normalization, loopback, the client's own origin, the
  Overview), the username lookup and its limits, deletion, the step-4
  upgrade, and the share host's address in `/api/me` and discovery.

`conftest.py` points the data directory at a temp folder and the mail
backend at the in-memory outbox before the package is imported. CI runs
the tests in the `test` job of `cloud.yml` on a PR that touches `cloud/`.
Dispatching `cloud.yml` from any branch (the `update-account-server`
skill) tests and publishes `ghcr.io/<owner>/gamma-cloud:latest`; no merge
to `main` is involved. The Gamma app's `check.yml` / `docker.yml` skip
changes that only touch the account server
([github_actions.md](github_actions.md)).

## The Gamma side: Sign in with Gamma Cloud

`backend/gamma/cloud_auth.py` (the client, the identity seam and access
tokens), `backend/gamma/cloud_sync.py` (the grant check, the preference
profile, the server list), `backend/gamma/routers/cloud_auth.py` (the
endpoints), `frontend/src/settings/SettingsCloudSignIn.jsx` (the Server
pane's Sign-in section and the Account pane's row), the login page's
button. Tests: `backend/tests/test_cloud_auth.py` (a fake account server
signing real Ed25519 tokens, rotating refresh tokens and answering the
profile, server-list and lookup endpoints) and the `cloudSignIn` browser
scenario.

**Nothing downstream changes.** The callback mints the same `sessions` row
the password login does (`routers/auth.py` `new_session`) and sets the
same cookie; every other module keeps reading `request.state.user`. The
row is marked `via = 'cloud'` (migration step 18; a password login leaves
it empty), which only the grant check below reads. What is new is one
table in users.db, `identities` (migration step 14): which account server
subject is which local account, the last verified claims
(`preferred_username`, `plan`, `email`), the refresh token,
Fernet-encrypted with the data directory's key, and `revoked_at`, set when
the account server refused that grant and cleared by the next sign-in.

**The device.** The desktop client names this install on the code
exchange (`cloud_auth.device`): `device_id`, made once and kept in the
`settings` KV as `cloud_device_id`, and `device_name`, the machine's host
name. The user agent is `Gamma/<version> (<system>; <address>)`. Every
call this server makes to another (the account server, the username
lookup, a mirror's remote) carries it, since Cloudflare in front of the
account server blocks the bare Python-urllib signature. A refresh token
this server stops holding is revoked at the account server
(`revoke_later`, RFC 7009, on a background thread, failures logged as
warnings): the one a newer sign-in replaced (`link` returns it), the one a
refused sign-in or a failed ID-token check leaves behind, and the one an
unlink (`/api/auth/cloud/unlink`, `manage.py unlink-identity`) or an
account deletion (Settings → Users, `manage.py delete-user`) drops. So a
row on the Devices page always stands for a key this server holds.

**Configuration.** Settings → Server → Sign-in, stored in the `settings`
KV: the account server's address (`cloud_issuer`; empty = off), the client
(`cloud_client_id`, default the public desktop client `gamma-desktop`;
`cloud_client_secret` encrypted, write-only) and the policy. A provisioned
container gets the same through the environment — `GAMMA_CLOUD_ISSUER`,
`GAMMA_CLOUD_CLIENT_ID`, `GAMMA_CLOUD_CLIENT_SECRET`, `GAMMA_CLOUD_POLICY`,
`GAMMA_CLOUD_ADMIN_SUBJECT` — which makes the pane read-only. Under the
`provision` policy the pane also offers *Accept published pages*
(`cloud_share_host`, env `GAMMA_CLOUD_SHARE_HOST=1`), which makes the server
the share host (below).
`GET /api/server-config` tells the login page whether to show the button.

A server whose confirmed public URL is not a loopback one cannot sign in
as the desktop client (`cloud_auth.needs_connect`). Until it has a client
of its own the login page shows no cloud button, `start` sends the browser
back with "not connected yet" as `?cloud_error=`, the Account pane's Link
row says an admin connects the server first, and the Server pane's *Server
client* row offers *Connect* — the round trip under "Connecting a server"
above, through `GET /api/auth/cloud/connect/start?next=` and
`…/connect/callback` (admins only). The callback trades the code and saves
the client id and secret as if typed into the row, then returns to `next`
with `?cloud_connect=ok` or `?cloud_connect_error=`, which the pane shows
once; the Settings dialog reopens on the Server pane
(`REOPEN_SETTINGS_KEY`). Switching a server that already had people
signed in to a new client id ends their grants: they link again.

**The flow.** `GET /api/auth/cloud/start?next=` stores the pending sign-in
(state, PKCE verifier, nonce, the callback URL — this server's confirmed
public URL, else the request's own origin, which for a local sidecar is the
loopback the account server's desktop client allows) in the `mcp_oauth`
table and redirects to the account server's authorize endpoint. Every
client asks for `openid email profile offline_access prefs`, so a
container holds a refresh token as well as a sidecar does. An account
server older than the `prefs` scope refuses a confidential client's sign-in
with `invalid_scope`, so the account server must be updated before the
containers.
`GET /api/auth/cloud/callback` consumes the state, exchanges the code
(client secret included for a confidential client), verifies the ID token
against the discovery document and the JWKS (both cached in memory, the
JWKS refetched on an unknown key id): issuer, audience, expiry, nonce, and
`email_verified` — an unverified cloud account is refused here as well as
on the account server. A refusal is a message on the login page
(`/?cloud_error=`), never a stack trace.

**Which local account** (`cloud_auth.resolve_account`):

| the identity is… | policy `refuse` (default) | `claim` | `provision` |
|---|---|---|---|
| linked already | that account | that account | that account |
| unknown, username = username exists, unlinked (exact, else one case-insensitive match — usernames are lowercase, usernames need not be) | refused with "sign in with its password and link it" | linked to it | linked to it |
| unknown, username = username taken (guest, or linked to another subject) | refused | refused | refused |
| unknown, no such username | refused | refused | a new account under the username, empty password hash, personal workspace |

`GAMMA_CLOUD_ADMIN_SUBJECT` names one subject that becomes the server
admin whatever the policy (creating or claiming the account under its
username) — how a provisioned container gets its first admin with no
password on the wire. A signed-in account can link its own identity
(`start?link=1`, the Account pane's button) whatever the policy: one
cloud account per local account and one local account per cloud account.
Unlinking (`POST /api/auth/cloud/unlink`) is refused while the account has
no password, since nothing else could sign it in. `manage.py
list-identities` / `link-identity` / `unlink-identity` are the shell
equivalents; renaming and deleting an account carry or drop its identity.
Once the account is known, every sign-in claims the shared-workspace
invitations waiting for its subject (`workspaces.claim_pending_memberships`,
[workspaces.md](workspaces.md) "Pending invitations").

**Access tokens.** `cloud_auth.access_token_for(username)` turns the
stored refresh token into an access token for the endpoints under "What
Gamma servers call with a token": a refresh-token grant at the token
endpoint (client secret included for a confidential client). The rotated
refresh token is saved before the access token is used, and a lock per
account keeps it to one refresh at a time, as the reuse detection
requires. A refresh that loses a race with a new sign-in or an unlink
leaves their token in place and revokes the one it got. The access token stays in memory until
two minutes before its `expires_in`; the sign-in's own access token
starts the cache. A 401 from the account server drops the cached token.

**The grant check** (`cloud_sync.check_all`, run by `cloud_sync.lifespan`
at startup and then every hour) refreshes every identity holding a refresh
token. `invalid_grant` means the grant is gone: the person signed this
server out on the Devices page, changed their password or deleted the
account. Then the refresh token is dropped, the identity gets
`revoked_at`, and the account's `via = 'cloud'` sessions are deleted.
Password sessions stay, and so do sessions from before step 18, which
count as password sessions. Every other failure (no network, a 5xx,
`invalid_client`) is a warning in the server log and is retried an hour
later. That is the offline grace: a laptop without network stays signed
in on its year-long session. The same refusal found by any other refresh,
such as an invitation's lookup, signs out the same way. The check then
syncs the profile and refreshes the server list entry.

**The preference profile.** The account-wide `profile` pref
([settings.md](settings.md)) is kept in step with the account server's
`profile` key the way VS Code's settings sync works: merged preference by
preference, with explicit fetch and push when you want one side to win
(`cloud_sync.sync_profile`):

- **Merge.** `profile-base` (a `users.db` pref never served by
  `/api/prefs`) holds the copy both sides last agreed on, with the cloud
  subject it was agreed with (a base agreed with another cloud account is
  no base). Each sync merges the two copies against it: a preference changed
  on one side only takes that side's value; one changed on both takes the
  newer profile's (`updated_at` compared to the millisecond, the account
  server's precision). The result is written here and pushed there as
  needed, then becomes the new base.
- **First sync.** Without a base, one side missing takes the other, and
  equal copies just record the base. Two different copies wait for the
  person (state `choose`): nothing is replaced, the notice
  `cloud-sync-choice` lights the dot, the profile's `GET` answers
  `cloud_choice: true` and the app opens Settings → Account once per page
  load. There the Settings sync row opens a dialog once, "Settings differ
  from Gamma Cloud", with **Fetch from cloud** (the cloud's copy replaces
  this server's) and **Push to cloud** (the other way round); cancelled,
  the row's **Sync now** asks again while the state lasts. The `merge`
  action (the web app's defaults as the base, so each side keeps what it
  changed from them) stays on the API but has no button. An account linked
  before this merge existed gets the same question once if its two copies
  differ.
- **By hand.** The same row, once synced, offers only **Sync now** (the
  merge, through `POST /api/auth/cloud/sync`); an answer of `choose` opens
  the same dialog. Fetch and push exist only inside a conflict.
- **When.** The callback syncs before it redirects (5 s timeout), so the
  browser's first load already sees the synced profile. A browser reading
  the profile (a load, a refocused tab) syncs first when the last attempt
  is over a minute old (`sync_if_stale`), so a change made on another
  server shows up as soon as you switch to this one. A change made here
  syncs once changes have settled for 5 seconds, on a timer thread that
  never blocks the request. Every grant check syncs again.
- **Browsers.** The web app saves with `PATCH /api/prefs/profile`, only the
  entries it changed (`db.patch_profile`): a tab loaded before a cloud
  change arrived still holds the old value of every other entry, and a
  whole-object save would read to the merge as a local change back to it.
- **Races.** One sync per account at a time. A push the account server
  refuses as older (409, another server pushed meanwhile) and a local write
  that finds the profile changed since it was read
  (`db.replace_profile_if`) both read the two sides again and merge anew,
  against the base that is still the old one.
- **Clocks.** A local edit is stamped at least a millisecond after the
  stored time. A push is stamped now or a millisecond past the cloud's
  copy, whichever is later; when the account server stores it under
  another time (clamped from the future, or cut to the millisecond), the
  local row takes that time.
- **Never.** `ai-settings` and `ai-provider` never sync. Nothing syncs
  without cloud sign-in or without a linked identity holding a token, so a
  server without cloud sign-in makes no call at all (a test asserts it).

The last outcome per account (`synced` / `pending` / `error` / `choose`, `off`
worked out on each read) is kept in memory by `cloud_sync` and read, with
no network, from `GET /api/auth/cloud/sync-status`, which the Settings
dialog's account tags show ([settings.md](settings.md)).

**The server list.** After a sign-in, and at every grant check, the server
posts itself to `POST /api/me/servers` (an upsert, which refreshes
`last_seen_at`). The address is the confirmed public URL, named by its
host. A local sidecar has no public URL: it sends the loopback origin the
sign-in came in on (remembered in the `settings` KV as `cloud_server_url`
for the hourly check) and names itself after the machine. A plain LAN
address is not listed. An unlink (`/api/auth/cloud/unlink`, `manage.py
unlink-identity`) or an account deletion takes the server off the list
with `DELETE /api/me/servers`, then revokes the grant
(`cloud_sync.release`). If no access token is cached, the release refreshes
once first. Failures are warnings, never errors to the person.

**The username lookup** for invitations
([workspaces.md](workspaces.md) "Pending invitations") runs on the
inviter's own grant (`workspaces._cloud_access_token` →
`access_token_for`). An inviter whose account here is not linked gets
"Link your own Gamma Cloud account" (503).

**The share host.** One Gamma in cloud mode holds every free account's
published pages (the plan's step 5): cloud sign-in on, policy `provision`,
and the `cloud_share_host` switch on (Settings → Server → Sign-in, *Accept
published pages*; `GAMMA_CLOUD_SHARE_HOST=1` on a provisioned one; only in
effect while cloud sign-in is on). The account server hands its address to
every Gamma server (`GAMMA_CLOUD_SHARE_HOST_URL`, surfaced as
`gamma_share_host` in discovery and `share_host` in `/api/me`). The switch
does three things:

- **The exchange.** `POST /api/auth/cloud/exchange` trades a person's
  Gamma Cloud access token for a write token on their workspace here
  (below).
- **No guest.** `/api/login-guest` answers 403 and `/api/server-config`
  says `guest: false`; the login page then hides its guest button.
- **No account directory.** `GET /api/accounts` answers admins in full and
  anyone else only with the account named exactly `?q=`. The pickers
  (`AccountPicker`) take an empty directory as a hidden one and look the
  typed name up with `?q=`.

**The exchange** (`gamma/publish.py` `exchange`, `routers/publish.py`)
takes a Gamma Cloud access token as `Authorization: Bearer`: the token a
publishing server got for the person through `access_token_for`. It checks
the token at the account server's `/userinfo` (the endpoint from the cached
discovery document; nothing about the token or its answer is cached) and
refuses an unconfirmed e-mail. The account is resolved by
`resolve_account` under the server's policy exactly as a first sign-in
would: provisioned with its personal workspace under `provision`, pending
invitations claimed. No session is minted and no refresh token is stored.
The answer is `{token, workspace_id, username, url}`: a write-scope
integration token for 365 days on the account's default personal
workspace, and this server's address. The token is named "Published pages
from <server>" after the caller's `{server}`, one live per account and
name (the previous one is revoked). Limits: 20 per IP and 10 per cloud
account in 10 minutes. While the switch is off the exchange is a 403,
before the account server is asked. The publishing side is
[mirror.md](mirror.md) "Publishing".

Two more settings shape what a share host serves, both environment only
([mirror.md](mirror.md) "Publishing" has the mechanics).

**The plan's cap.** How many pages each plan may publish is
`config.PLAN_PAGE_LIMITS` in `gamma/config.py`:

| plan | published pages |
|---|---|
| `free` | 5 (`GAMMA_FREE_PAGE_LIMIT` overrides it; 0 lifts the cap) |
| `plus`, `pro`, anything else | unlimited |

The plan is the `plan` claim the share host stored for the identity at its
last exchange or sign-in. A publish that finds the workspace full
exchanges once more, so an upgrade counts at once.

**Page hosts.** `GAMMA_PAGE_HOST` is the hostname pattern of the
per-account page hosts, with one `{username}` placeholder:
`{username}-pages.gammapdf.com` gives every published page the address
`https://<username>-pages.gammapdf.com/<slug>-<page id>`. Empty (the
default) means token links only. The server refuses to start on a pattern
without exactly one `{username}`, or on one that is otherwise not a
hostname. Cloud usernames are single DNS labels (`[a-z0-9-]`, 3 to 32
characters), and the `-pages` suffix keeps every page host apart from a
service hostname, so `accounts.RESERVED_USERNAMES` need not change; name no
service with the suffix.

Deploying page hosts takes a wildcard DNS record, `*.gammapdf.com` pointing
at the share host (named records such as `account` keep precedence over
the wildcard), and a wildcard site in front of the container. Behind
Cloudflare in "Full" mode Caddy's internal certificate covers the
wildcard, so no DNS challenge is needed (`cloud/deploy/Caddyfile`, the
`share` service of `cloud/deploy/compose.yml`, its settings in
`share.env.example`):

```
*.gammapdf.com {
	tls internal
	@pages header_regexp Host ^[a-z0-9-]+-pages\.gammapdf\.com$
	@share host share.gammapdf.com
	handle @share { reverse_proxy share:9001 }
	handle @pages { reverse_proxy share:9001 }
	handle { respond 404 }
}
```

The proxy passes the `Host` header through (Caddy does by default); the
app reads the username out of it.

The rest of the plan's cloud mode is configuration, not code: registration
is already off on every Gamma (accounts come from the admin or the cloud),
the default quota is the storage setting, and per-IP limits belong to the
edge.

**Not built yet** (step 6 of the plan): the desktop shell's first-run
sign-in and reading `/api/me` for the person's servers in the launcher. The
grant check refreshes every identity every hour, which is fine for a
sidecar or a container but will need spreading out on a share host with
many accounts (the exchange stores no refresh token, so an account that only
publishes adds nothing to it).
