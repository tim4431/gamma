# Guest accounts, the shared AI allowance and demo mode

Three server-wide behaviours that belong together: what a guest is (a
throwaway account that keeps nothing), how the admin's shared AI keys are
metered per account, and the demo switch that turns a server into a public
try-it box (demo.gammapdf.com). The workspace model itself is
[workspaces.md](workspaces.md); the AI stack is [ai.md](ai.md).

## Guests keep nothing

Every guest login is a throwaway account of its own. `POST /api/login-guest`
(`gamma/guests.py new_guest()`) makes:

- the account: username `guest-<8 url-safe chars>`, `is_guest = 1`, an
  empty `password_hash`;
- its personal workspace, through `workspaces.ensure_personal(name,
  welcome=True)`, with the welcome page from `gamma/seed.py`, which names
  the lifetime ("It stays for 24 hours after you started"). When
  `GAMMA_GUEST_SEED` names a workspace zip (the one backup format,
  [workspaces.md](workspaces.md) "Export and backups"), that zip is
  restored into it (`ws_backup.restore_zip(ws, path, "replace")`), so every
  visitor starts from the admin's sample library. A seed that cannot be
  restored is logged as a warning and skipped; the visitor keeps the
  welcome page;
- a normal session row (`guest_date` = the creation date). The response is
  `{"ok": true, "username": ...}`.

The endpoint is a sync `def` because it writes files. It is refused on a
share host and answers 503 once the live guest accounts reach
`GAMMA_GUEST_MAX` (default 500, `0` turns guest logins off; the count and
the insert are one statement). Each login creates a workspace directory, so
it is rate limited to 10 per IP per hour.

Everything keyed on `is_guest` applies: no exports, no workspace creation,
no cloud link, no integrations, no notices, no provider editing, the
bounded default quota (`server_settings.user_limits` decides by `is_guest`,
never by name), and the shared AI keys only while the admin's switch is on.
`workspaces.is_guest_workspace` means "a personal workspace whose owner is
a guest": backup import and the reviewed Gamma import refuse it, it keeps
no snapshots, and scheduled backups skip it. Admins may set a guest's
storage limits and delete it, but never give it a password, the admin flag
or a new name.

**Expiry.** A guest account lives `guest_ttl_hours` after
`users.created_at` (a server setting, default 24; `GAMMA_GUEST_TTL_HOURS`
overrides it and the Settings row says so). Expiry is computed from
`created_at` on every check, never stored, so a shorter lifetime applies to
existing guests at once. Two places enforce it:

- the session middleware treats an expired guest session as signed out,
  deletes the account on the spot (off the event loop) and clears the
  cookie. A tab that still sends `X-Gamma-User: guest-…` gets the usual 409
  "reload the tab". `auth.session_lookup` (the websocket handshake) answers
  None for an expired guest;
- the sweeper `gamma/guests.py` runs in the app lifespan (every 10 minutes,
  shaped like `backup_schedule.lifespan`) deletes the expired guest
  accounts nobody came back for.

Both call `workspaces.delete_account(username)`, the one account deletion:
sessions, identities and the cloud grant, integration tokens, publisher
sessions, the account's workspaces, its usage rows and prefs, the users
row. `DELETE /api/admin/users/{name}` and `manage.py delete-user` use it
too, so an admin may delete a guest account like any other.

A guest's `POST /api/logout` deletes the account right away, since nothing
can sign into it again. For a guest, the account menu's Log out asks first
("Log out and delete").

`sessions.guest_date` stays in the schema, written with the creation date
and read by nothing. Migration step `guest_accounts` (v20) deletes the
legacy shared `guest` account and its workspace. It also turns every other
`is_guest` row (older builds made one for `manage.py create-user` without a
password) into a normal password-less account, which is what `create-user`
without a password makes, so the expiry never deletes it. `manage.py setup`
creates no guest; `manage.py sweep-guests` deletes the expired ones now
(`--all`: every guest).

`GET /api/session` adds `guest_expires_at` (UTC ISO) for a guest session,
and `GET /api/server-config` adds `guest_ttl_hours` and `demo`.
`GET/PUT /api/admin/settings` carry `guest_ttl_hours` (1–720, with
`guest_ttl_source` `environment` / `saved` / `default` and
`guest_ttl_hours_range`) and `demo_mode` (with `demo_mode_source`). A PUT
of either is 400 while its environment variable decides
(`gamma/server_settings.py guest_settings`).

## The shared AI allowance

The admin's shared provider entries (`gamma/ai_settings.py`, Settings →
Server → Shared AI; an API key or a ChatGPT subscription the admin signs in
to) are what "per-server AI access" means. Every account gets them after
its own entries, the guest switch adds guests, and every account that is
not a guest may add its own keys on top. The allowance meters the shared
entries only, per account, over a rolling 24 hours:

- the shared config (`settings` KV `ai_providers`) holds
  `"allowance": {"accounts": N, "guests": N}`: tokens (input + output as
  the providers report them, `gamma/ai_usage.py`) per account per 24 h,
  `0` = unlimited (the default for both), at most 10^9. A guest account
  takes the `guests` limit, every other account (admins included) the
  `accounts` one;
- `GET/PUT /api/admin/ai-providers` read and write it next to `guests`
  (`PUT {"allowance": {"accounts": N}}`, either key alone; anything but a
  whole number in range is a 400);
- `ai_runtime(user)` reports `"allowance": {"limit", "used", "exhausted"}`
  whenever a shared entry applies (`null` otherwise; `limit` 0 = unlimited,
  never exhausted). `used` is `ai_usage.shared_used(user)`, the account's
  rows on `server:` provider ids in the last 24 h. It also puts
  `{"user", "limit"}` on every shared provider conf; the shared models stay
  listed once the allowance is used up;
- the choke point is `ai_client.open_ai`, which every token-spending call
  goes through. `check_allowance` re-reads the count on each call and
  raises `AllowanceExhausted`, a 429 whose `detail` reads "This server's
  shared AI allowance for your account is used up (U of L tokens in the
  last 24 hours). Add your own key in Settings → AI, or try again later."
  A chat stream refused after its first round ends with that text as its
  `error` line; the Test probe and the login test report it in-body.
  Dictation reports no tokens, so it is never metered, only refused while
  the allowance is used up. Model listings spend nothing and are never
  refused. An account's own entries are never marked, so never metered;
- the Usage pane's Reset (`DELETE /api/ai/usage`) keeps the rows the
  allowance still counts, so it cannot refill it;
- `GET /api/ai/models` and `GET /api/ai/usage` carry the same `allowance`
  object for the pickers and the Usage pane.

## Demo mode

`demo_mode` is a server setting (`settings` KV, Settings → Server → Guests)
that `GAMMA_DEMO=1` overrides, reported with its source like the public
URL. It changes presentation, not rules: guests, their expiry and the
allowance work the same on every server.

- The login page (`auth/LoginPage.jsx`, while `GET /api/server-config`
  says `demo` and guests are allowed) leads with **Try the demo** (a guest
  login) and a line saying the workspace lasts `guest_ttl_hours` hours and
  nothing is kept. The password form and the Gamma Cloud button fold behind
  an **Admin sign-in** link, collapsed by default; the admin still signs in
  there to add the shared key and set the allowance.
- The guide keeps its progress in `sessionStorage` instead of
  `localStorage` (`guideStorage(demo)` in `guide/triggers.js`), so every
  visit starts fresh. **Your first paper** is offered on the library right
  after the guest lands: a state trigger whose own
  `requires: {demo: true, view: "home"}` gates only the offer, so the tour
  stays manually startable everywhere. Like every offer it waits for
  Suggest tours ([onboarding.md](onboarding.md)).
- The account card names when the workspace goes ("Temporary workspace ·
  deleted in 5 hours", from `guest_expires_at`, `auth/guestExpiry.js`),
  with the hint that it stays until then or until log-out. Every guest
  sees that line, demo server or not.
- The account card of any account a shared entry applies to shows what it
  spent through the shared entries (`AllowanceMeter` in
  `settings/SettingsKit.jsx`: "Server AI: 1.2k of 50k tokens in the last
  24 h" over the storage meter's bar, or the plain count with no limit),
  refreshed with `/api/ai/models` each time the menu opens.

demo.gammapdf.com is its own compose project on the VPS, in a folder next
to the account server's (`GAMMA_DEMO=1` in its `demo.env`), reached through
the account project's Caddy over the shared Docker network `gamma-edge`. It
runs the `sha-<short>` tag of a branch build (`docker.yml` dispatched on the
branch, which never moves `:latest`), named by `GAMMA_TAG` in the folder's
`.env`: setup in [cloud/deploy/demo/README.md](../../cloud/deploy/demo/README.md),
updates through the `update-demo-server` skill
(`.claude/skills/update-demo-server/SKILL.md`).
