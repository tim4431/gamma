# Guest accounts, the shared AI allowance and demo mode

Three server-wide behaviours that belong together: what a guest is (a
throwaway account that keeps nothing), how the admin's shared AI keys are
metered per account, and the demo switch that turns a server into a public
try-it box (demo.gammapdf.com). The workspace model itself is
[workspaces.md](workspaces.md); the AI stack is [ai.md](ai.md).

## Guests keep nothing

There is no shared `guest` account any more. `POST /api/login-guest` mints
a fresh account for the visitor:

- username `guest-<8 url-safe chars>`, `is_guest = 1`, empty
  `password_hash` (`gamma/guests.py new_guest()`), its own personal
  workspace through `workspaces.ensure_personal(name, welcome=True)` (the
  welcome page from `gamma/seed.py`, which names the lifetime: "deleted …
  24 hours after you started"), then, when `GAMMA_GUEST_SEED` names a
  workspace zip (the one backup format, [workspaces.md](workspaces.md)
  "Backups"), that zip restored into it (`ws_backup.restore_zip(ws, path,
  "replace")`) so every visitor starts from the admin's sample library. A
  seed that cannot be restored is logged as a warning and skipped: the
  visitor keeps the welcome page;
- a normal session row (`guest_date` = the creation date); the response is
  `{"ok": true, "username": ...}`;
- refused on a share host (unchanged), 503 once the live guest accounts
  reach `GAMMA_GUEST_MAX` (default 500, `0` turns guest logins off; the
  count and the insert are one statement), rate limited to 10 per IP per
  hour (a login creates a workspace directory, so the cap is tighter than
  the old one). The endpoint is a sync `def`: it writes files.

Everything already keyed on `is_guest` keeps applying to the new accounts:
no exports, no workspace creation, no cloud link, no integrations, no
notices, no provider editing, the bounded default quota
(`server_settings.user_limits` decides by `is_guest`, never by name), the
shared AI keys only while the admin's switch is on. `auth.is_guest_workspace`
(`workspaces.is_guest_workspace`) means "a personal workspace whose owner is
a guest": backup import and the reviewed Gamma import refuse it, it keeps no
snapshots, scheduled backups skip it. Admins may set a guest's storage
limits and delete it, never give it a password, the admin flag or a new
name.

**Expiry.** A guest account lives `guest_ttl_hours` (server setting, default
24; `GAMMA_GUEST_TTL_HOURS` overrides and the Settings row says so) after
`users.created_at`. Two enforcers, one helper:

- the session middleware treats an expired guest session as signed out,
  deletes the account on the spot (off the event loop) and clears the
  cookie; a tab that still sends `X-Gamma-User: guest-…` gets the usual 409
  "reload the tab". `auth.session_lookup` (the websocket handshake) answers
  None for an expired guest;
- `gamma/guests.py` runs a sweeper in the app lifespan (every 10 minutes,
  the same shape as `backup_schedule.lifespan`) that deletes every expired
  guest account nobody came back for;
- both call `workspaces.delete_account(username)`, the one account deletion
  (sessions, identities and the cloud grant, integration tokens, publisher
  sessions, the account's workspaces, its usage rows and prefs, the users
  row), which `DELETE /api/admin/users/{name}` and `manage.py delete-user`
  use too. An admin may delete a guest account like any other.

A guest's `POST /api/logout` deletes the account right away: nothing can
sign into it again. A shorter lifetime applies to existing guests at once
(expiry is computed from `created_at` on every check, not stored).

The old midnight rollover is gone: `sessions.guest_date` stays in the
schema (written with the creation date, read by nothing). Migration step
`guest_accounts` (v20) deletes the legacy shared `guest` account and its
workspace from existing data directories, and turns any other `is_guest`
row (`manage.py create-user` without a password used to make one) into a
normal password-less account, so the expiry never deletes it; `create-user`
without a password now makes exactly that. `manage.py setup` creates no
guest; `manage.py sweep-guests` deletes the expired ones now (`--all` every
guest).

`GET /api/session` adds `guest_expires_at` (UTC ISO) for a guest session;
`GET /api/server-config` adds `guest_ttl_hours` and `demo`. `GET/PUT
/api/admin/settings` carry `guest_ttl_hours` (1–720, `guest_ttl_source`
`environment` / `saved` / `default`, `guest_ttl_hours_range`) and
`demo_mode` (`demo_mode_source`); a PUT of either is 400 while its
environment variable decides (`gamma/server_settings.py guest_settings`).

## The shared AI allowance

The admin's shared provider entries (`gamma/ai_settings.py`, Settings →
Server → Shared AI; an API key or a ChatGPT subscription the admin signs in
to) are what "per-server AI access" means: every account gets them after
its own entries, the guest switch adds guests, and every
account that is not a guest may add its own keys on top. The allowance
meters the shared entries only, per account, over a rolling 24 hours:

- the shared config (`settings` KV `ai_providers`) gains
  `"allowance": {"accounts": N, "guests": N}` — tokens (input + output as
  the providers report them, `gamma/ai_usage.py`) per account per 24 h,
  `0` = unlimited (the default for both), at most 10^9; a guest account
  takes the `guests` limit, every other account (admins included) the
  `accounts` one;
- `GET/PUT /api/admin/ai-providers` read and write it next to `guests`
  (`PUT {"allowance": {"accounts": N}}` — either key alone; anything but a
  whole number in range is a 400);
- `ai_runtime(user)` reports `"allowance": {"limit", "used", "exhausted"}`
  (`null` while no shared entry applies or the limit is 0), `used` being
  `ai_usage.shared_used(user)`: the account's rows on `server:` provider
  ids in the last 24 h; it also puts `{"user", "limit"}` on every shared
  provider conf, and the shared models stay listed once it is used up;
- the choke point is `ai_client.open_ai` (every token-spending call goes
  through it): `check_allowance` re-reads the count on each call and raises
  `AllowanceExhausted`, a 429 whose `detail` reads "This server's shared AI
  allowance for your account is used up (U of L tokens in the last 24
  hours). Add your own key in Settings → AI, or try again later." A chat
  stream refused after its first round ends with that text as its `error`
  line; the Test probe and the login test report it in-body. Dictation
  reports no tokens: never metered, only refused while the allowance is
  used up. Model listings spend nothing and are never refused. An
  account's own entries are never marked, so never metered;
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
  nothing is kept; the password form and the Gamma Cloud button fold behind
  an **Admin sign-in** link, collapsed by default (the admin still signs in
  to add the shared key and set the allowance).
- The guide keeps its progress in `sessionStorage` instead of
  `localStorage` (`guideStorage(demo)` in `guide/triggers.js`), so every
  visit starts fresh, and **Your first paper** is offered on the library
  right after the guest lands: a state trigger whose own
  `requires: {demo: true, view: "home"}` gates only the offer, so the tour
  stays manually startable everywhere. Like every offer it waits for
  Suggest tours ([onboarding.md](onboarding.md)).
- The account card names when the workspace goes ("Temporary workspace ·
  gone in 5 hours", from `guest_expires_at`, `auth/guestExpiry.js`); that
  line is every guest's, demo server or not.

demo.gammapdf.com is the `demo` service next to the account server on the
VPS (`GAMMA_DEMO=1` in its `demo.env`), pinned to the `sha-<short>` tag of a
branch build (`docker.yml` dispatched on the branch, which never moves
`:latest`): setup in [cloud/deploy/README.md](../../cloud/deploy/README.md)
"The demo server", updates through the `update-demo-server` skill
(`.claude/skills/update-demo-server/SKILL.md`).
