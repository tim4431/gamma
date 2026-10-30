# API reference

All endpoints are same-origin under `/api`. The frontend never talks anywhere
else; in dev, Vite proxies `/api` → `127.0.0.1:9001`.

## Auth model

- A `session` cookie identifies the user (middleware sets
  `request.state.user_id`, the account's id, and `request.state.user`, its
  username). Account-level endpoints use `require_user_id` (or
  `require_user` for the username), which refuses an integration token
  ("Integrations" below). The API names people by username in both
  directions; the account ids storage keys on never need to reach a
  client ([user_db.md](user_db.md) "Accounts are named by id"). Where one
  does appear — the `actor` of an op-log batch or of a change-feed
  `deleted[]` entry, a job's `owner` — it is an opaque string a client may
  compare but never shows; no client reads them today.
- Every data endpoint works in a **workspace** ([workspaces.md](workspaces.md)):
  `?ws=` or the `X-Gamma-Workspace` header names it, nothing means the
  account's default personal workspace. `require_ws(request)` admits any member
  or signed-in non-guest account allowed by public workspace access,
  `require_ws(request, write=True)` editors and owners (a viewer gets 403);
  a caller without effective access gets 403. The returned workspace id is what the data helpers
  take; the account's id (`auth.actor_of`) is the actor.
- Share tokens (`?share=<token>`) are the ONLY unauthenticated **read** path.
  A token is `<workspace id>.<secret>`, split at the first dot (a workspace
  id never holds one; `db.share_token_workspace` is the one parser), so a
  router can place share traffic by the prefix without a lookup. It is
  stored whole in `shares.token`, and every lookup is by the whole token.
  `resolve_ws` returns the session's workspace, or the workspace of the share
  named by a valid `?share=` token. There is no `?user=` fallback: it would
  trust any username and leak whole accounts. A share names a PAGE of a
  workspace (its root block, so note pages share exactly like papers; the
  PDF is the page's `doc_id`/`source_url`) or a FOLDER (a folder block's
  id: the pages filed there or below it, read live, so pages filed later
  join, pages moved out leave and a folder moved in brings its pages). `auth.share_scope()` hands every
  share-enabled endpoint a `ShareScope`, and the endpoint asks it what is in
  reach (`allows_page`, `allows_block`, `allows_folder`, `lists_library`,
  `pages` and `block_ids` for a read that scans the workspace;
  `blocks_store.assert_block_in_scope()` for block reads) instead of
  branching on the share's kind. A token reaches only its own pages'
  subtrees and assets: their PDFs, the uploads their blocks reference (found
  by walking the shared pages' subtrees, never the workspace; a yes is
  remembered for `uploads.SHARE_READ_TTL_S`, 5 min), the files an edit
  share's visitor stores through `POST /upload-image` or
  `POST /upload-file` (`uploads._share_reads_own_upload` remembers that
  yes as the file is stored, so a picture dropped on a shared page shows
  before a saved block names it; the visitor sent those bytes), their
  own `source_url` through the proxy. Backlinks and other pages are refused
  (403). The root listing and folder export are refused for a page share;
  a folder share gets the pages in reach and exports its folder or a
  subfolder.
- Share reads are readable **cross-origin**: a GET carrying `?share=` or
  resolving `/share/{token}` answers `Access-Control-Allow-Origin: *`
  (`auth._apply_share_cors`), refusals included, so another Gamma's
  frontend can pull a shared page or folder into its own library
  browser-side (a share link pasted into the "+" box, the share view's "Add
  to my library" — [import_export.md](import_export.md)). A `mode=gamma`
  export through a token carries only what the share shows: a folder export
  the exported folder (the shared one, or a subfolder the link names) at the
  top level with no folder above or beside it, a page opened through a
  folder share its folders up to the shared folder, a page share no folder
  or label; and of the chats only the pages' active ones
  ([import_export.md](import_export.md#gamma-to-gamma-export)). `*` makes browsers drop credentials,
  so a cross-origin fetch is a stranger's: only `anyone` links open that way.
  Writes and every other endpoint keep the same-origin default.
- **Share permissions** (`shares.audience` / `role`, the invited people in
  `share_users`, `auth.share_access`) are Notion-shaped and additive:
  members of the page's workspace keep their workspace role (editors/owners
  edit, viewers view); the sharer INVITES people (`users: [{name, role}]`
  by username, stored as `share_users` rows by account id — a deleted
  account's go with it) who get in with their own `view`/`edit` whatever
  general access says — on a server with Gamma Cloud sign-in also people
  without an account here yet: named by their e-mail address, whose
  invitation (`email_invites`, `gamma/email_invites.py`) the account
  server mails and which waits until a cloud sign-in through its mailed
  link, and only that, makes it a `share_users` row; or named by
  their Gamma Cloud username, whose invitation waits
  (`pending_share_users`, `gamma/shares.py`) until their first cloud
  sign-in does the same; everyone else goes
  through general access —
  `audience` `anyone` (no session needed, with the share's `role`), `users`
  (any signed-in non-guest account, with the share's `role`), `list` (nobody
  beyond the invited). When a request carries `?share=`, the token decides
  WHICH WORKSPACE is read (the share's — a signed-in visitor sees the shared
  page or folder, not their own library) while the session decides whether the
  audience gate admits them; a refused token is 401 when signing in could
  help, else 403. `edit` shares let `require_ws_writer` resolve the
  workspace for the block writers — `POST /blocks`, `PUT /blocks/{id}`,
  `DELETE /blocks/{id}`, `PUT /blocks/{id}/children`, `POST /blocks/{id}/reorder`,
  `POST /pages/{id}/ops` (and the page websocket, view or edit),
  `POST /upload-image`, `POST /upload-file` — each of which confines the touched blocks to the
  shared pages (no new pages, no deleting/moving a page itself, no changes to
  a page root's properties — so a folder edit share can never re-file pages
  into or out of its folder). Comment writes take `auth.require_commenter`
  instead: any member (viewers too) and any signed-in account a share
  admits, view or edit, confined to the shared pages; never a link visitor
  or a read-scope token ([comments.md](comments.md) "Who may comment"). A
  share visitor therefore sees the usernames of the people who wrote or
  resolved comments on the shared page, as presence shows them. Everything
  else stays session-only.
- **Link visitors.** An `anyone` + `edit` share makes the link itself the
  key: whoever opens it edits the page, without an account. Such a writer
  (no session, or a guest account — `auth.is_link_visitor`) is recorded
  as `link:<name>` (`auth.actor_of`): the display name the share view keeps
  per browser (`src/collaboration/linkName.js`, generated "Curious Otter"
  style, renamable from the topbar tag), sent percent-encoded as the
  `X-Gamma-Name` header on every API call (`utils.js` injects it) and as
  `?name=` on the page websocket; cleaned server-side (`auth.link_name`:
  control characters out, 40 chars, `Anonymous` when empty). A label, not
  an identity — the log names an account by its id, never `link:…`, so it
  never confuses the two. Link visitors alone are rate limited per IP (`auth.link_ratelimit`:
  `collab.LINK_OPS_PER_MINUTE` op batches, `uploads.LINK_UPLOADS_PER_5_MIN`
  uploads); their uploads count against the page's workspace like any
  share editor's. Flipping the share back to `view`, or stopping it,
  revokes the link's writes at once (the grant is re-read per request, and
  open page sockets are re-checked by `collab.revalidate`).
- **Unknown tokens.** A `?share=` that names no share is counted per IP
  (`auth.note_share_miss`, from `share_grant`, `GET /share/{token}` and the
  page socket): past `auth.SHARE_MISSES_PER_5_MIN` (30) in five minutes the
  address gets 429 for the rest of the window and the server log carries
  one warning per window — the admin's only signal that someone is probing
  for links. A token without the `<workspace id>.<secret>` shape, or whose
  prefix is not its share's workspace, is unknown too (`auth.share_lookup`
  refuses it). So is every link minted before schema version 32, whose
  token is the bare secret: step `share_token_workspace` rewrote the
  stored tokens, and the Share popover shows the new link
  ([migrations.md](migrations.md)). The secret is 96 random bits, so
  guessing one is hopeless (the workspace prefix is no secret). The
  throttle is about noise and visibility, not about protecting the
  space.
  The link-visitor throttles above log the same way.
  Keep that read/write + scope distinction when adding endpoints.
- Outbound fetches of user-supplied URLs (PDF proxy/resolver, AI PDF
  re-download) go through `gamma.net_guard.guarded_urlopen`, which blocks
  non-http(s) schemes (`file:`, `ftp:`, …) and hosts that resolve to
  loopback/private/link-local/metadata addresses (SSRF), re-checking on every
  redirect.
- Outbound HTTPS is urllib's default context, verified against the CAs
  OpenSSL was built to find; outside Windows Python reads no system trust
  store, so the macOS Keychain never counts. Where those paths hold
  nothing, such as the desktop app's frozen server on a Mac without
  python.org's Python, `create_app` points `SSL_CERT_FILE` at certifi's
  bundle (`app._verify_https_with_certifi_when_no_cas`); otherwise every
  call (the AI, the ChatGPT sign-in, Gamma Cloud) fails with
  `CERTIFICATE_VERIFY_FAILED`. A proxy that re-signs HTTPS with a root
  that is only in the Keychain still fails: give the server that root
  with `SSL_CERT_FILE`.
- Workspace ids and doc ids are validated (`db.safe_ws_id` / `db.safe_doc_id`,
  used by `ws_db_path` / `ws_uploads_dir`) before they
  become filesystem paths — no traversal.
- The session cookie is `HttpOnly; SameSite=Lax`, and `Secure` when the request
  is HTTPS (auto via scheme / `X-Forwarded-Proto` — off on plain-HTTP LAN so
  login still works there). Sessions are enforced server-side against
  `SESSION_MAX_AGE` (expired rows are deleted in the middleware) and are revoked
  when the account's password is changed.
- A cloud identity (Sign in with Gamma Cloud, [cloud_accounts.md](cloud_accounts.md))
  ends in the same `sessions` row as a password login: the callback mints it,
  nothing downstream can tell the difference. The row is marked
  `via = 'cloud'` so the hourly grant check can end it when the account
  server revokes the grant.
- `/api/login` and `/api/login-guest` are rate-limited per IP/username
  (`gamma/ratelimit.py`, in-process fixed windows → 429; a guest login 10
  per IP per hour), as are share-link
  visitors' writes and unknown share tokens (above; those log a warning
  once per window through `check`'s `on_first_exceed`). Not an edge WAF; add
  one for large public deployments. "Per IP" is the connection's peer
  address (`ratelimit.client_ip`); `X-Forwarded-For` counts only when
  uvicorn itself rewrote the peer from it, which it does for the proxies
  `FORWARDED_ALLOW_IPS` lists (127.0.0.1 by default, the Docker image
  too: a deployment behind a proxy must name the proxy, or its compose
  network's pinned subnet — `cloud/deploy/` does for the demo and the share
  host — else every visitor shares the proxy's bucket). The counters are bounded
  (`ratelimit.MAX_KEYS`): expired windows are swept when the table fills,
  then the oldest.
- JSON answers of 1 KB or more are gzipped for clients that accept it
  (`gamma/compression.py`, level 3: a 5,000-block subtree goes from 2.1 MB
  to 0.47 MB for about 22 ms of compression, done in a worker thread above
  256 KB). Only a whole `application/json` 200 of known length is
  touched: streams (the AI chat and translation NDJSON, event streams),
  files and their range requests (uploads, PDFs, the app's assets) and
  anything already encoded pass through as they are. A dict a sync
  endpoint returns is encoded on the event loop, so the reads whose answer
  can be large — the tree reads (`GET /blocks/{id}/subtree`,
  `/blocks/{id}/children`, the library listing), a block search, the two
  searches, a conversation (`GET /chats/{bucket}`), the op-log catch-up
  (`GET /pages/{id}/ops`) and the covers (`GET /page-snaps`) — answer a
  `json_response.OrjsonResponse` they encoded in their worker thread (a 5,000-block page stalled
  the loop ~175 ms otherwise). It encodes with
  orjson, in about a tenth of the standard library's time, and
  `blocks_store.load_json` parses each row's `properties` with it in about
  a fifth. A page's subtree is read by its `page_id` (one indexed read;
  every block of a page carries its id) rather than the recursive walk
  down the parents, and `kind` is computed in Python from the parsed
  properties (`blocks_store.block_kind`, the generated column's rule)
  instead of being selected: the column costs up to six JSON functions per
  row. On an in-memory database, 5,000 highlight-like blocks of 1.3 KB
  each: 42 ms the old way, 8 ms this way. A stored NaN goes out as null and a stored lone surrogate as
  U+FFFD (both were a 500; a write refuses a new NaN, [collab.md](collab.md)
  "Ops"); what orjson refuses, such as nesting past 255
  levels (a tree about 125 blocks deep), falls back to the standard
  library. Writes keep `json.dumps`, so the stored text does not change.
- Every response carries baseline hardening headers (`X-Content-Type-Options`,
  `X-Frame-Options: SAMEORIGIN`, `Referrer-Policy`, `Content-Security-Policy:
  frame-ancestors 'self'`, and HSTS on HTTPS). SVG uploads are served
  `Content-Disposition: attachment` + `CSP: sandbox` so they can't run inline as
  stored XSS.
- `/api/admin/*` additionally requires the `is_admin` flag.
- A hosted container whose plan made it read-only answers every POST, PUT,
  PATCH and DELETE under `/api/` with 423 `{detail, read_only: true}`,
  except sign-in and sign-out, the export jobs, `/pages/by-docs`, marking
  notices seen, the Inbox's marks (`/inbox/…`) and `/admin/hosted/sync` (`hosted.READ_ONLY_ALLOWED`,
  [cloud_accounts.md](cloud_accounts.md) "Hosted containers").

## Endpoints

### Session & account (`auth.py`; the cloud routes in `cloud_auth.py`, `/auth/cloud/exchange` in `publish.py`)
| Method | Path | Purpose |
|---|---|---|
| GET | `/health` | liveness probe, `{ok: true}` (`app.py`; no session) |
| POST | `/login`, `/login-guest`, `/logout` | session management (`/login` refuses an account with an empty password hash — one only its cloud identity signs in, and every guest). `/login-guest` mints a fresh guest account with its own workspace (`gamma/guests.py`, [guests.md](guests.md)) → `{ok, username}` (`guest-<8 chars>`); 403 while `guests.logins_open()` is false (`GAMMA_GUEST_MAX=0`, a hosted container, a share host, or an admin's guest sign-in off), 503 once `GAMMA_GUEST_MAX` guests are live. A guest's `/logout` deletes the account |
| GET | `/server-config` | public: what the login page offers besides a password — `{cloud: {enabled, issuer, lead, join_requests, email_invites}, password_login, registration, guest, guest_ttl_hours, guest_seeded, demo, page_host}` (`cloud.enabled` false while the server still has to be connected; `cloud.lead` true where the page leads with the cloud button, on a hosted container and on a share host; `cloud.join_requests` true under the `invited` policy, where a newcomer's cloud sign-in asks to join and the login page says so ([cloud_accounts.md](cloud_accounts.md) "Requests to join"); `cloud.email_invites` `{server, members}`: whether an admin may invite to this server by e-mail, and a workspace's owner or a share's manager to theirs — cloud sign-in on and connected through a client of its own, and for `members` the `provision` or `invited` policy (`email_invites.available`, [cloud_accounts.md](cloud_accounts.md) "Invitations by e-mail"); `guest` false where guest logins are off — a share host, a hosted container, `GAMMA_GUEST_MAX=0` (`guests.logins_open`); `guest_ttl_hours` how long a guest account lives; `guest_seeded` whether a guest starts with the `GAMMA_GUEST_SEED` library; `demo` whether demo mode is on — [guests.md](guests.md); `page_host` the per-account page hostname pattern from `GAMMA_PAGE_HOST`, e.g. `{username}-pages.gammapdf.com`, "" when unset — how the app knows it was opened on a page host; `read_only` true while a hosted container's plan keeps it read-only, `hosted` `{plan, status}` on a hosted container that has synced, else null — "Hosted containers") ([cloud_accounts.md](cloud_accounts.md)) |
| POST | `/auth/cloud/exchange` | the share host's half of publishing ([mirror.md](mirror.md) "Publishing"): `Authorization: Bearer <Gamma Cloud access token>`, body `{server?}` (the calling server's name) → `{token, workspace_id, username, url}` — a write-scope integration token (365 days, named "Published pages from <server>", replacing the live one of that name) on the person's default personal workspace here, the account resolved under the sign-in policy like a first sign-in (provisioned under `provision`, pending invitations claimed), and this server's address. 403 unless the server accepts published pages (`cloud_share_host`), for an unconfirmed cloud e-mail, or when the policy refuses the account; 401 for a token the account server does not know; 503 when it cannot be asked; 429 past 20 per IP or 10 per cloud account in 10 minutes |
| GET | `/auth/cloud/start?next=&link=1&invite=` | Sign in with Gamma Cloud: stores the pending PKCE sign-in and redirects to the account server; `link=1` needs a session and attaches the cloud identity to that account; `invite` is the link id of an invitation by e-mail, which the account server's *Continue* sends and which admits the sign-in (one of another shape is dropped; [cloud_accounts.md](cloud_accounts.md) "Invitations by e-mail"). A server that still has to be connected (a public URL on the desktop client) goes back to `next` with `?cloud_error=` instead. 30 per 10 minutes per IP |
| GET | `/auth/cloud/connect/start?next=`, `/auth/cloud/connect/callback?code=&state=` | admins: connect this server to Gamma Cloud — `start` stores a pending PKCE connection and redirects to the account server's approval page (`gamma_server_connect_endpoint`) with this server's confirmed public URL; `callback` trades the code for a client id and secret (`gamma_server_connect_token_endpoint`), saves them as the cloud sign-in client and returns to `next` with `?cloud_connect=ok` or `?cloud_connect_error=` ([cloud_accounts.md](cloud_accounts.md) "Connecting a server") |
| GET | `/auth/cloud/callback?code=&state=` | the account server's return: verifies the ID token, resolves or creates the local account per the policy (`gamma/cloud_auth.py`), pulls the preference profile, registers this server on the person's server list (`gamma/cloud_sync.py`), mints a session and redirects to `next`. A refusal goes back to `next` signed out, its query kept (a share link's gate keeps its link), with `?cloud_error=`; one the `invited` policy refuses asks to join instead and goes back with `?cloud_join=sent`, `waiting` or `declined` (`gamma/join_requests.py`; too many waiting: `cloud_error`). `next` never carries the outcome of an earlier round trip (`cloud_auth.safe_next` drops it). A sign-in the desktop app started mints no session here: the browser goes to the app's loopback address with `?result=` (or `?error=`, which for a request to join is its sentence) instead |
| POST | `/auth/cloud/app-start` | the desktop app's sign-in, which runs in the system browser: `{next, link, return_to, challenge, invite?}` (`invite` as for `start`; `return_to` must be `http://127.0.0.1:<port>/cloud-signin`, or `localhost` / `[::1]`; `challenge` the S256 of a verifier the app keeps) → `{url}`, the authorize URL to open. 400 for another address or challenge, 401 for `link` without a session, 503 when sign-in is off or the server still has to be connected ([cloud_accounts.md](cloud_accounts.md) "The desktop app's sign-in") |
| POST | `/auth/cloud/app-claim` | the desktop app's window redeems the one-time result its loopback address was given: a form with `result` and `verifier`, and the header `X-Gamma-Desktop: claim` (403 without it, so no other site can post one). Mints a cloud session and redirects (303) to `next`; a used, expired or wrongly verified result goes to `/?cloud_error=`, and one wrong verifier uses the result up |
| GET | `/auth/cloud/sync-status` | the signed-in account's own preference profile sync (session only; guests and integration tokens get 403): `{profile: {state, at, error}, identity: {linked, username?}}`. `state` is `off` (cloud sign-in off, or no identity holding a token), `pending` (a push is scheduled, or failed and waits for the next check; `error` then says why), `synced` (the last pull or push agreed, at `at`), `error` (the last attempt failed) or `choose` (the first sync found two different copies and waits for the person's choice). Read from memory (`cloud_sync.profile_status`), no network; the Settings dialog polls it while open |
| POST | `/auth/cloud/sync` | sync the caller's profile with Gamma Cloud now (session only): `{action, defaults?}` with `action` `sync` (the automatic merge; answers `choose` while a first sync waits), `merge` (also settles the choice; `defaults` — the web app's default profile — is the base of a first merge), `fetch` (the cloud's copy replaces this server's; 409 when the cloud has none) or `push` (this server's replaces the cloud's). Answers `{outcome: pulled / pushed / merged / same / choose, profile}`; 400 without a linked identity holding a token, 502 when the account server could not be reached |
| GET / POST | `/auth/cloud/status`, `/auth/cloud/unlink` | the signed-in account's own cloud identity (username, plan, e-mail, linked at, `offline` — a refresh token is held — and `revoked_at`) plus `enabled`, the `issuer` (the account server's address, which the Account pane's "Open account" button opens) and `connected` (false while an admin still has to connect this server; the Link button waits); unlink is refused for an account without a password, and takes this server off the person's server list before revoking the grant |
| GET | `/admin/join-requests` | admins: every request to join (`gamma/join_requests.py`), the waiting ones first, newest first: `{requests: [{subject, username, email, status, from_share, shared_by, decided_by, created_at, updated_at}]}` — `status` `pending` / `approved` / `declined`; `from_share` whether it came through a share link, `shared_by` that share's maker (`""` from the login page or once the share is gone; the page itself is not named, an admin may not read it); `decided_by` who approved or declined it |
| PUT / DELETE | `/admin/join-requests/{subject}` | admins: `PUT {status: "approved" \| "declined"}` decides a request whatever it was (approved: the person's next sign-in gets them an account; declined: their sign-ins ask nothing more); `DELETE` removes it (a declined person may ask again, an approval not used yet goes). Both answer the list; 404 for no such request, 422 for another status |
| GET | `/session` | who am I, plus `workspaces: [{id, name, kind, role, access, public_role, personal, default, members, mirror_of, mirror_detached, publishing, zotero_of, zotero_detached}]` (memberships + every public workspace; the sync fields as `/workspaces/mine` gives them) and `default_workspace` (quota lives in `/quota`); `build` (`version`, `commit`, `label`, `frozen`) is what a problem report names the server by, sent to the login page too; a guest session adds `guest_expires_at` (UTC ISO: when the account and its workspace are deleted); any other account's adds `profile`, the entries of its preference profile the first screen is drawn with (`theme`, `language`; those it has), so a browser that has never seen the account paints in its theme and language without waiting for the profile ([settings.md](settings.md)) |
| GET | `/accounts[?q=]` | the account directory for the invite / owner pickers: `{accounts: [{username, is_admin}]}`, non-guest accounts only (signed-in non-guest callers). On a share host only admins get the list; anyone else gets the one account named exactly `q`, or none |

### Workspaces (`workspaces.py`) — see [workspaces.md](workspaces.md)
| Method | Path | Purpose |
|---|---|---|
| POST | `/workspaces` | create a personal one (`{name}`; guests 403); admins may add `kind: "shared"`, `owner`, `access`, `public_role`, `quota_mb` |
| GET | `/workspaces/mine` | Settings → Workspaces: every workspace I can open with its `used_bytes` (and `mirror_of`, the remote workspace's name when it is an offline copy, empty while the copy is detached, `mirror_detached` true for a detached whole-workspace copy, `publishing` true while at least one of its pages is published to Gamma Cloud, where `mirror_of` stays empty — [mirror.md](mirror.md); `zotero_of`, the library's name when a Zotero link keeps the workspace, empty while the link is detached, and `zotero_detached` true for a detached one — [zotero_sync.md](zotero_sync.md)), plus `account` (my limits and the usage of all my personal workspaces) |
| GET/PUT/DELETE | `/workspaces/{id}` | kind + members (pending invitations last, `pending: true` + `subject`; for owners and admins, then the invitations by e-mail, `{email, id, role, added_by, added_at, expires_at, pending: true}`; a member's Gamma Cloud username as `cloud` where it differs from the name here, [cloud_accounts.md](cloud_accounts.md) "Which local account") + quota + `personal_of` + `default` (any member; admins) / rename `{name}` (owner), `default: true` (a personal workspace's owner), kind (400 while a Zotero link, a clone or a publication with pages keeps the workspace, detached ones included), access + public role, workspace quota (admin) / delete (owner; not an account's last personal one): `{ok, warning, final_copy}` — a final copy goes to `backups/deleted/` first (`final_copy` its file name, "" for a guest's), and when it cannot be written nothing is deleted (507) |
| GET/POST | `/workspaces/{id}/backups` | the workspace's server-kept snapshots (any member; each with `scheduled`, `auto` — a restore's `pre-restore` one — `missing_uploads` and `damaged`) / take one now `{label?, uploads?}` (owner; at most `ws_backup.MAX_PER_WORKSPACE` manual ones; 400 while the disk has less than 1 GB free) |
| GET | `/workspaces/{id}/backups/{name}/download` | the snapshot as a zip — the same zip `/export` gives (any member) |
| POST | `/workspaces/{id}/backups/{name}/restore?mode=` | restore it in place: `replace` (owner) / `merge` (editor), the same rules and answer as `/import-data` |
| DELETE | `/workspaces/{id}/backups/{name}` | delete a snapshot (owner) |
| GET | `/export` | backup zip of a workspace (everything or `uploads=0`; the `gamma-backup-1` zip of `gamma/ws_backup.py`): the request's, `?ws=` (any member), or — admins — `?user=` for an account's default workspace (`routers/ws_backups.py`, like the next two; the web app starts the same work as the `workspace-export` job) |
| GET | `/export-all` | every personal workspace of the account in one zip, one `/export` zip per workspace inside (`uploads=0` for databases only; guests 403) |
| POST | `/import-data` | restore (`mode=replace`, owners) / merge (`mode=merge`, editors) a backup zip into a workspace (same targeting); never into a guest's workspace. A zip whose databases fail `PRAGMA quick_check` is refused (400) before anything changes; a replace first keeps the current state as a `pre-restore` snapshot (its name in `pre_restore`; 400 and nothing restored when it cannot be taken) and reports `pages_removed`; a merge reports `pages_added` / `pages_skipped` / `chats_added` / `from_trash` (pages that were only in Recently deleted, brought back as the backup has them), and for a folder's Gamma export (its manifest's `scope.folder`) `folder`, the workspace's id for that folder (`""` when it came nowhere). Every restored page gets a `reload` in its op log above any seq a client saw ([workspaces.md](workspaces.md) "Export and backups"). The web app restores through the `restore` job |
| GET/POST | `/backup-tasks` | the account's scheduled backup tasks (`routers/backup_tasks.py`, `gamma/backup_schedule.py`; signed-in non-guest) / create one `{name, enabled, scope: selected\|all_owned, workspaces[], cron, uploads, retention_mode: days\|count, retention_value}`; `cron` is five UTC fields firing at most hourly (one minute value; else 400), `retention_value` 1–90 (else 422), targets must be workspaces the owner owns (at most 5 tasks) |
| PUT/DELETE | `/backup-tasks/{id}` | replace the task (same body; 409 while it runs) / delete it, keeping its snapshots |
| POST | `/backup-tasks/{id}/run` | queue a run now, paused or not (409 while it runs) |
| POST | `/backup-tasks/preview` | `{cron}` → `{runs: [next three ISO times], timezone: "UTC"}`; 400 for a schedule that fires more than hourly |
| PUT/DELETE | `/workspaces/{id}/members/{user}` | shared workspaces: invite or set a role `{role}`, incl. owner (owner) / remove (owner) or leave (yourself) |
| GET/POST | `/workspaces/{id}/invites` | the invitations still waiting: by Gamma Cloud username, `invites: [{subject, username, role, invited_by, created_at}]` (any member), and by e-mail, `email_invites: [{id, email, role, invited_by, created_at, mailed_at, expires_at}]` (empty but for owners and admins) / invite, naming exactly one of three (400 otherwise; owner; admins) — role `editor` (default) or `viewer`. `{email, role}`: the account server mails the address, whoever it belongs to (an account here or a member too: only the mailed link admits, and a membership keeps its role), and the invitation waits (`invited: {pending: entry}`, the same answer for every address); inviting the address again keeps the invitation, changes its role and mails it again. `{username, role}`: the account server resolves the username to a subject; a person already linked here becomes a member at once (`invited: {member}`), anyone else gets a pending membership (`invited: {pending}`) their first cloud sign-in claims. `{invitation, role}`: a waiting invitation by e-mail (its `id`) gets that role, no mail (404 for none). Returns the workspace like `GET /workspaces/{id}`. 400 on a personal workspace, when cloud sign-in is off (an e-mail: where `email_invites.available` says no, with its reason), for an unknown username or one already a member, a bad address, 100 e-mail invitations waiting, or what the account server refused; 429 past 30 lookups per account per 10 minutes, or 20 e-mail sends and resends an hour, or when the account server says too many; 503 when the account server cannot be asked or did not mail it ([workspaces.md](workspaces.md) "Pending invitations") |
| DELETE | `/workspaces/{id}/invites/{subject}` | withdraw a pending invitation by username (owner; admins); 404 when there is none |
| DELETE | `/workspaces/{id}/email-invites/{invite_id}` | withdraw an invitation by e-mail (owner; admins): the row goes, and the account server is asked in the background to drop its link (on the first Gamma Cloud grant this server holds among the withdrawer, the inviter and an admin). Returns the workspace; 404 when there is none |
| POST | `/workspaces/{id}/email-invites/{invite_id}/resend` | mail an invitation by e-mail again (owner; admins): a new mail under the same link id, whose link replaces the one sent before, the sender as its inviter, 14 days from now. Returns the workspace; 404 when there is none; the send's 400, 429 and 503 as for an invitation |
| GET | `/workspaces/find-page/{page_id}` | which of my workspaces holds the page (deep links without `ws`) |

`PUT /workspaces/{id}` applies all supplied changes in one transaction.
Authorization or validation failure leaves the name, kind, access, quota and
account default unchanged. Creation limits count explicit memberships, not
public workspaces the account can merely open.

The generic preference endpoints scope `profile` and `ai-provider` to the
account without requiring workspace access. Other supported preference keys
require access to the named workspace. `ai-settings` remains reserved and is
never returned by the generic endpoint.

### Blocks (`blocks.py`) — the core data model
| Method | Path | Purpose |
|---|---|---|
| GET/POST | `/blocks/by-doc/{doc_id}` | lookup / create the page BY ATTACHMENT — the page whose PDF is `doc_id` (POST creates it: `{default_title, source_url?, original_filename?, folder?}`, `folder` a folder id that files a NEW page only); the PDF-ingest + extension-dedup path, and what "Open as document" on a PDF file chip calls (the file is already stored under that hash). The lookup and the insert run under the workspace's write lock (`blocks_store.write_lock`), as in every get-or-create by attachment (clip, the "Web clips" page of `clip/note`, attach, `pages/from-file`, the Logseq import), so racing requests find one page; if older data holds two, the oldest answers. Text-only pages come from `POST /pages` |
| GET | `/blocks/{id}/children`, `/{id}/subtree`, `/{id}/backlinks` | tree reads (block dicts, below); the root listing (`/blocks/root/children`) is the library: every page as a page summary and the folder and label trees (below) — through a folder share token, only the pages the share reaches and the shared folder's tree (the share view's home library); 403 through a page share. For a member, `/blocks/folders/subtree` and `/blocks/labels/subtree` are the whole trees with their `seq`, and a folder or label block reads like any block (`GET /blocks/{id}`, its children); no share token reaches a tree |
| POST/PUT/DELETE | `/blocks`, `/blocks/{id}` | CRUD — inside a page these are thin wrappers over the op path (`gamma/ops.py`): logged, fanned out to the page's room; `PUT` takes `content` and/or a properties PATCH (a null value deletes the key), and with `content` an optional `base` — the text the content was edited from: a block changed meanwhile gets the edit merged in (`gamma/textmerge.py`) instead of replaced, and the answer (`{ok, updated_at, seq, content}`) carries the text stored (an embed card editing another page's block sends it). With `properties` an optional `base_properties`, the values the patch was computed from: an ink group's `ink_url` whose base is not the stored one is merged into the stored drawing by stroke (with `base_ids`, the base file's stroke ids as the client loaded it, a base file that is gone merges by deletions instead of a union), a text box's `text_box` into the stored box key by key ([text_boxes.md](text_boxes.md) "Merge"), a picture's `picture` by its size and centre and its other keys one by one ([pictures.md](pictures.md#model) "Merge"), and the answer's `properties` is the patch applied (it names the merged file, box or picture); an ink merge also answers `ink_conflict: {clean, lost_ours, returned}`, the writer's changes the merge dropped and the strokes it brought back ([handwriting.md](handwriting.md) "Two writers, one group"). The same `base_ids` rides on a `set` op, and `POST /pages/{id}/ops`' answer carries each merge's report under its block's id (`ink_conflicts`), never in the echo. A mirror uses both there: its pushes send `base_ids` read from its own copy of the base file, and it records each unclean report as an `ink_merged` conflict row ([mirror.md](mirror.md) "What travels"). A new page (`parent_id: "root"`) is a plain insert (`blocks_store.create_page`): last in the library without `before`/`after`, and a key another page holds is re-keyed like an op's (`blocks_store.free_position`); deleting a page moves it to Recently deleted (`ops.trash_page`, below) → `{ok, id, trashed: {id, title, folders, deleted_at, deleted_by}}` — on a share host it is `ops.delete_page` at once: subtree + its op log gone, its row of the change log turned `deleted`. A block of a page in Recently deleted reads as not found everywhere; for a member the 404 of `GET /blocks/{id}`, `/{id}/children` and `/{id}/subtree` carries `trashed` (the page's entry). A `NaN` or infinite number anywhere in a create or update body is a 400 worded as an op batch's (`not a finite number: NaN`), and a lone surrogate is stored as U+FFFD ([collab.md](collab.md) "Ops") |
| PUT | `/blocks/{id}/children` | replace the whole subtree (delete + reinsert, one transaction) — bulk paths only (duplicating a page, tests); the page's room gets a `reload`, the upload names the old subtree held and the new one lacks go to the orphan check ([user_db.md](user_db.md) "Stored files"). Node ids must have the block-id shape (a node without one gets a fresh id) and text is capped like an op's: 400 / 413; an id another block already has is a 409 and leaves the old children in place. 400 for `root`: the library's pages are created and deleted one by one. The editor itself sends ops |
| POST | `/blocks/{id}/reorder` | move within the page (an op) or, with `parent_id` on another page, across pages (the source room sees a `delete`, the target reloads); either way a key a sibling holds is re-keyed and the answer's `position` is the stored one |
| GET | `/block-search` | fuzzy note/page/highlight search (`case`, `whole` word), the newest `limit` matches, each `{id, content, kind, page_root_id, page_title, ancestors?}` (`kind` the stored one, below); empty `q` returns recently edited blocks (feeds the `[[ref]]` popup's initial suggestions); `ids=a,b` looks blocks up (ref chips, Gamma-link titles). Through a share token every mode sees only the blocks the share reaches — others are dropped from the answer, never refused. The query is always text: `regex=1` is refused (400) — a catastrophic pattern would hold the GIL, i.e. the whole server. SQLite skips the blocks without the query's literal runs (`textnorm.literal_runs`), the fuzzy pattern runs over the rest; past 2 s the answer is what was found so far plus `"partial": true`, which the Ctrl+F panel's notes group (and its compact find bar) and the `[[` picker's footer show as a "stopped early" line. With `q`, `scope` (comma-separated folder and label ids, the panel's filter chips) keeps to the pages inside every one of them (a folder with its subfolders; an id that is neither finds nothing), before the `limit`. The scan runs newest first by `(updated_at, rowid)`, a total order, and an answer that stopped before the end carries `next`: after its last match when another follows, after the last block read when the 2 s ran out. Passed back as `cursor`, it continues the scan from there with a fresh 2 s, so pages never repeat or skip a block (400 for a malformed cursor). Sync `def` (threadpool) |

Route order matters: the static-prefix routes (`by-doc`, `children`,
`subtree`) must stay registered before `/blocks/{block_id}`.

**The block dict** (`blocks_store.block_to_dict`, what every block read
answers and every subtree node carries): `{id, parent_id, position, content,
properties, created_at, updated_at, page_id, kind}`. `page_id` and `kind`
are read-only, derived by the server, never taken from a write: `page_id` is
the page the block lives in (its own id on a page; `folders` / `labels` in
the trees), `kind` one of `page`, `folder`, `label`, `ink`, `text_box`,
`picture`, `sheet`, `recording`, `link`, `highlight`, `note`
([user_db.md](user_db.md) "pages.db"), the order the server tests them
in. `POST /blocks` answers the block as
stored (it reads it back).

**The highlight shape** (`gamma/highlights.py`, the frontend's
`shared/model/blockModel.js`). A block placed on a PDF page by rectangles
— a highlight, a link region, an ink group — carries
`properties.pdf_position`: `{pageNumber, width, height, boundingRect: {x1,
y1, x2, y2}, rects: [{x1, y1, x2, y2}, …]}`, the 1-based page, the page as
it was measured when the place was taken (the viewer's page at its zoom
then, an ink file's page in points, an imported annotation's page box) and
the rectangles in that frame, top-left origin, `boundingRect` their union;
`area: true` on an area highlight. After the page's PDF is replaced
([highlight_relocation.md](highlight_relocation.md)) a highlight's
position may also carry `doc_id`, the stored file (a page `doc_id`, the
content hash; a duplicated page's `/api/uploads/<doc_id>.pdf` address names
its stored file; `unstored` for a page that showed its file from another
URL alone) its rectangles were measured on, written by the replacement when
that is no longer the page's PDF; a position without it was measured on the
page's current PDF, which is what the viewer, the imports and a move by
hand write. `lost: true` marks one whose text the run did not find in the
new file (drawn hollow where it was; it keeps its `doc_id`). `approx: true`
marks one drawn dashed, pending until the person accepts it or moves it:
placed where its text was found roughly, with `match`, how alike the two
read (0 to 1, two decimals, at most 0.99), or, without `match`, an area
placed by estimate or one measured on another file that nothing placed
(left where it was; it keeps its `doc_id` until accepted). Beside them `prev` keeps where
it was before the run that first drew it dashed, for the review card:
`{doc_id, pageNumber, rects: [[x1, y1, x2, y2], …], quote}`, the file its
rectangles were measured on (also one no longer stored), its page there,
its rectangles as fractions of that page as shown (top-left origin, four
decimals, at most 200) and its quote before the run respelled it (`""` for
an area, at most 1000 characters). Accepting it (the web app's Accept)
drops `approx`, `match`, `prev` and `doc_id` and keeps the rectangles,
now the page's current PDF's. A lost text highlight may also carry
`closest`, the best place the run found for its text in the page's current
PDF that read too unlike it to be applied (under the account's threshold,
at least half alike): `{pageNumber, width, height, boundingRect, rects,
match, quote}`, a place in this same shape (at most 200 rects), how alike
it read (two decimals, at most 0.99) and its text as that file spells it
(at most 1000 characters); the web app's review card offers it, and its
Use closest match writes that place and quote, without `lost`, `closest`,
`doc_id` or the rest. A move by hand writes a position without any of
these; a run writes a placed one without `doc_id`, `lost` and `closest`,
replaces or removes a lost one's `closest` by what it found, leaves a
pending one pending with its own `match` and `prev`, and writes one it
places solid without `approx`, `match` or `prev`
([highlight_relocation.md](highlight_relocation.md#what-a-run-writes)); a
replacement of the PDF drops every `closest`, a place in the outgoing file.
A backup's restore keeps them all (`prev` and `closest` only in their own
form). A reader
scales a rectangle by its own page size over `width` / `height`. A highlight whose place on its page is
not known (a Logseq import without positions) has `pdf_position:
{pageNumber}` alone. The block id is the highlight's id: a link region
names a highlight on the page it links to by `link_block_id` (beside
`link_url` / `link_page_id`), and a highlight placed by its page alone
names the one on the PDF it was attached to in the notes (attach mode) by
`linked_highlight_id` (a block id, taking that one's page). A text box and a
picture have no position; the page of each is `pdf_page`
([text_boxes.md](text_boxes.md), [pictures.md](pictures.md)). A page stores
`source_url` only when it differs from its `doc_id`'s stored copy
(`/api/uploads/<doc_id>.pdf`); `blocks_store.page_attachment` and the
frontend's `pageAttachment` derive that URL. Page creation, the op path,
`POST /blocks/by-doc/{doc_id}` and `POST /pages/{id}/attachment` drop a
`source_url` equal to it. Migration step 30 converts older data
([migrations.md](migrations.md)). A highlight's `properties.color` is
its concrete colour as `#rrggbbaa` — a palette swatch at the highlight
alpha (0.65) when made in the viewer, an annotation's own `/C` and
`/GammaAlpha`, else `/CA`, when imported from a PDF — never a palette slot, so every member of a
workspace and every export sees the same colour whatever palette their
account keeps ([settings.md](settings.md) "Colors"). `gamma/colors.py`
(the frontend's `shared/model/palette.js`) reads the older `rgba(…)`
and `#rrggbb` spellings too, which an older server or export can still
send; workspace step 37 respells stored ones. Tests: `tests/test_highlight_shape.py` (the shape, its writers, the derived
URL), `test_block_columns.py` (the kinds), `test_migrations.py` and
`test_restore_consistency.py` (step 30 and an older backup),
`test_colors.py` (the colour spellings, the Logseq names, step 37), the browser
suite's `pdf.mjs` (a highlight made from a selection, its stored colour,
a changed palette swatch, a link region landing on its block).

**The library listing** (`GET /blocks/root/children`, `routers/blocks.page_summary`)
carries only what the home view reads — the cards, the folder and label
filters, quick open, the chat and link pickers, the DOI / arXiv matching of
an opened link — one summary per page, in library order: `{id, content,
position, created_at, updated_at, preview, properties}`. `properties` holds
those of `doc_id`, `source_url`, `original_filename`, `folders`, `labels`
(lists of folder and label ids), `pinned` and `seeded` the page has, and `meta` with those of its `title`,
`authors`, `year`, `venue`, `volume`, `doi` and `arxiv_id` it has
(`SUMMARY_PROPS` / `SUMMARY_META`); everything else — `bibtex`, `ppt_cite`,
`summary`, `auto_title`, the rest of `meta`, … — comes with the page itself
(`GET /blocks/{id}` or its subtree). `preview` is the first ~240 chars of the
page's first few child blocks that are no highlight, joined with ` · ` (one
`idx_ub_parent` seek per listed page, so the cost follows the pages, not the
blocks; `""` when there are none).
Beside `children` the listing carries the two trees, read in the same
snapshot:

```
{"children": [<page summary>, …],
 "folders": {"seq": 12, "children": [<folder node>, …]},
 "labels":  {"seq": 3,  "children": [<label node>, …]}}
```

A node is a block dict (`kind` `folder` / `label`, `content` the name,
`properties` with `pinned` when pinned) plus `children`, its subfolders in
order (`[]` for a label). `seq` is the tree's op-log position, where a
client following the tree's room (`/ws/page/folders`, `/ws/page/labels`)
catches up from, like an open page's; the web app's home view does not join
the rooms — it reads the listing again after its own writes and when the
window comes back ([home_library.md](home_library.md) "Folders and labels").
Through a folder share the trees carry no `seq`:
`folders.children` is the shared folder's own node (with what is below it)
and `labels.children` the labels its pages carry.
`GET /blocks/{id}/subtree` on a page (or a tree) also returns `seq`, the
op-log position the tree reflects (the live session catches up from it).

### Collaboration (`collab.py`) — see [collab.md](collab.md)
| Method | Path | Purpose |
|---|---|---|
| POST | `/pages/{id}/ops` | apply a batch of block ops `{client, batch?, ops: [set / insert / move / delete], cursor?: {block, anchor, head}}` in one transaction — `{id}` a page, or `folders` / `labels` for the folder and label trees (members only; a rename, move, new folder, pin; [collab.md](collab.md) "The folder and label trees") → `{seq, at, ops, ink_conflicts?}`, the ops as applied (re-keyed positions carry their final value). `batch`: the client's id for the batch, the same on every retry; a batch this process already applied for that client is answered again, not re-applied. An `insert` of a block the page already has leaves it as it is and echoes it. A `set` may carry `base`, the text its `content` was edited from: a block changed meanwhile gets the edit as a patch onto its current text (a three-way merge, `gamma/textmerge.py`), and the echoed op carries the merged text. A `set`'s `base_props` (with `base_ids` for a drawing) merges an ink group's `ink_url` by stroke, a text box's `text_box` key by key and a picture's `picture` by its size, centre and other keys, as `base_properties` does on `PUT /blocks/{id}`; `ink_conflicts` reports each ink merge by its block (`{block id: {clean, lost_ours, returned}}`), and a batch answered again from the log carries none. `cursor`: the writer's caret in the text after the batch, fanned out with it and stored as the writer's presence. Lone UTF-16 surrogates in any string are stored as U+FFFD; a `NaN` or infinite number fails the batch (400 `not a finite number: NaN`). A workspace editor or an edit share (confined to the shared page; the page root's properties stay the workspace's). A bad op fails the whole batch (400/403/404/413): a 404 for an unknown block or parent names it as `missing`, which the client re-sends as an insert when it holds that block; a refusal because someone changed the page meanwhile adds `conflict` (`missing` 404, `moved` 403, `cycle` 400) and `index`, the refused op's place in the batch |
| GET | `/pages/{id}/ops?since=` | the op log after a seq (a page's, or a tree's for a member) → `{seq, batches: [{seq, actor, client, at, ops}]}`; 410 (reload the tree) when the log is pruned past `since`, holds more after it than a catch-up carries (`ops.CATCHUP_MAX_BATCHES` / `CATCHUP_MAX_BYTES`), or cannot continue from it (a backup restore replaced the log) |
| GET | `/sync/whoami` | who the credential is on this server: `{user, workspace: {id, name}, role, scope}` — `scope` is an integration token's (`read` / `write`), `session` for a browser; what a mirror checks before it is created and at the start of every round |
| GET | `/sync/changes?since=&limit=` | the workspace change feed (`gamma/routers/sync.py`) over its change log (`page_changes`, [collab.md](collab.md) "The change feed"): the pages whose row took a seq above `since`, in seq order, at most `limit` (≤ 2000) entries — live ones in `pages: [{id, created_at, updated_at, seq}]` (`seq` the page's latest op), deleted ones (in Recently deleted, or gone for good) in `deleted: [{id, deleted_at, actor}]` → `{since, cursor, more, format, pages, deleted}`. `format` is the shape of the pages it lists, the newest migration step with a workspace part (`migrations.workspace_format`): a copy kept by a server whose own differs must not take them, and a mirror pauses ([mirror.md](mirror.md) "The data format"). `cursor` is the highest seq listed (with none listed, the seq it continued from) as a decimal string; keep it as it is and pass it back (`since=""` lists everything; a value the log never gave out — not a count, or above its newest seq — lists from the start too). Exact: each change is listed once and none is missed; a page written again reappears at its new seq, so its last entry in a walk is current. Which pages to look at, for a copy of the workspace (a mirror); the page's own `seq` / `GET /pages/{id}/ops` says what changed. Any member (viewers too); no share tokens |
| GET | `/sync/folders` | the folder tree for a client resolving a typed path: `{folders: [{id, path: [names]}]}` in tree order ([folder_sync.md](folder_sync.md)) |
| GET | `/sync/folders/{id}` | the folder as files, what the gamma-sync client compares with its last round (`gamma/folder_sync.py`): `{folder: {id, path}, cursor, dirs: [{id, path}], pages: [{id, title, stem, doc_id, pdf, pdf_size, notes, version}]}` — every folder below as a directory path, every page filed in it or below once, under its first folder below it, its PDF (`pdf`, null when none is stored) and its notes (`notes`) sharing the stem; `version` changes whenever the notes file would; `cursor` the change log's newest seq. `root` is the whole library; 404 for an id that is no folder |
| GET | `/sync/folders/{id}/notes?pages=` | the notes files of up to 200 of the folder's pages (ids, comma-separated; pages outside it left out): `{pages: {id: {markdown, attachments, version}}}` — the vault page with `gamma_id` first in its front matter, the PDF linked as the file beside it, uploads rewritten to `attachments/<name>` relative to the note, `attachments` the names to fetch from `/uploads/` |
| WS | `/ws/page/{id}[?ws=&share=&client=]` | the page's live channel (or a tree's, `folders` / `labels`, members only): `hello` / `join` / `leave` / `cursor` presence, every applied `ops` batch (with the writer's `cursor` when the batch carried one), `reload`, `ink-live {client, …}` (a peer's handwriting stroke in progress, relayed and never stored, [collab.md](collab.md) "ink-live"), `comments {block_id, thread_id}` (Comments below) and `inbox` (no fields: something new in the Inbox of this socket's account, [comments.md](comments.md) "Live delivery"); the client sends `cursor` and `ink-live` only. Auth like HTTP (session cookie + `?ws=` (else the default workspace) or share token, resolved in the handler — the middleware doesn't run for websockets); viewers join too. Closed with 4403 when access is refused, or revoked while open (a share changed or stopped, a member removed — `collab.revalidate`), and with 4409 when the same tab (`client`) joins again on a newer socket |

### Comments (`routers/comments.py`, `gamma/comments.py`) — see [comments.md](comments.md)
| Method | Path | Purpose |
|---|---|---|
| GET | `/pages/{id}/comments?state=open\|resolved\|all` | the page's threads, oldest first → `{threads: [{id, block_id, page_id, resolved_at, resolved_by, created_at, last_at, comments: [{id, thread_id, author, body, created_at, updated_at, deleted}]}]}`: a thread on a note, a highlight or the page itself (`block_id` the page's id); `author` and `resolved_by` are usernames (`author` the name stored with the comment once its account is gone, `resolved_by` `""`); a tombstone (a first comment deleted while it has replies) has `deleted: true` and no body; comments whose block was deleted are left out. Any member; a share visitor for a page in the share's reach. 404 for a page in Recently deleted or gone, 400 for another `state` |
| POST | `/pages/{id}/comments` | `{id?, block_id, body, thread_id?, client?}` → 201 `{comment, thread}`: a new thread on `block_id` (a block of the page, or the page), or with `thread_id` a reply (a root on that block). 400 for a block outside the page, a `thread_id` that is no root there, an empty text or a malformed `id`; 413 past 20,000 characters. `id` is the client's: a comment of that id by the same author on the same page is answered as stored, nothing written twice; 409 when another author's has it or it is on another page, 404 when it is hidden since. Who may: `auth.require_commenter` (Auth model above). Writes no block and touches no page |
| PATCH | `/comments/{id}` | `{body, client?}` → `{comment}`: its author only (403); 404 for a hidden comment or a tombstone |
| DELETE | `/comments/{id}?client=` | → `{deleted: true}`: its author's, or any for the workspace's owner signed in (an integration token deletes its own only); a first comment that has replies stays as a tombstone, and the last reply under a tombstone takes it along |
| POST | `/comments/{id}/resolve` | `{resolved: bool, client?}` on a thread's first comment (400 for a reply) → `{thread}`; anyone who may comment on the page |

Every write publishes `{t: "comments", block_id, thread_id}` to the page's room, to every tab but the writer's (`client`). It then tells the people it concerns in their Inbox (below).

### Inbox (`routers/inbox.py`, `gamma/inbox.py`) — see [comments.md](comments.md) "The inbox"
| Method | Path | Purpose |
|---|---|---|
| GET | `/inbox?filter=unread\|all\|archived&before=<at>&limit=50` | `{items: [{id, kind, actor, workspace_id, workspace_name, page_id, page_title, block_id, thread_id, comment_id, share_token, snippet, at, read_at, archived_at}], unread, more}`: the account's items across every workspace and share, newest first. `kind` is `mention`, `comment_mention`, `reply`, `resolved`, `shared`, `invited` or `join_request`; `actor` a username (`""` once gone, and for a `join_request`, whose `snippet` is the Gamma Cloud username asking); `share_token` is set when the account reaches the page only through a share (one that names it, or the one the event came through; the way in as it is now); `page_title` is a folder share's folder name, and `""` once the account can no longer read the page; `all` leaves out the archived ones; `before` is the `at` the last listing ended on, and one moment is never split across two listings (`limit` at most 100). The items that lead nowhere any more (page in Recently deleted or gone, block or comment gone, a note that names the account no more, access lost) are archived first. 400 for another `filter` |
| GET | `/inbox/unread` | `{unread}`: the count of unread, unarchived items, what the bell shows |
| POST | `/inbox/mark` | `{ids?: [id] (at most 100), all?: true, read?: bool, archived?: bool}` → `{unread}`: mark the items (or every one) read or unread, archived or back; a stamp already set is kept. 400 without items or a mark |

All three are for a signed-in account's session: 401 without one, 403 for a guest or an integration token. `/inbox/mark` passes a read-only hosted server's gate.

### Pages (`pages.py`) — page first, PDF as an action on it
| Method | Path | Purpose |
|---|---|---|
| POST | `/pages` | create a text-only root page: body `{title?, folders?, id?, properties?}` (title defaults to `Untitled`, `folders` (folder ids) → `properties.folders`, stored as given like any filing; `id` keeps a page's id when a mirror brings it over, or is the one the editor's `/page` already linked — 400 when malformed or reserved (`root`, `trash`, `folders`, `labels`), 409 when a live block has it (a page of that id in Recently deleted gives way); `properties` seeds the page's own) → the block dict. On a share host, 402 `{detail, limit, used, plan}` when the owner's plan allows no more pages in their default personal workspace ([mirror.md](mirror.md) "Publishing") |
| POST | `/pages/by-docs` | which pages these stored files became: body `{doc_ids: [<hash>, ...]}` (≤500) → `{pages: {hash: {id, title}}}` — a hash matches the page carrying it as its PDF (`doc_id`) or the note page imported from it (a markdown upload's `markdown_import`); hashes with no page absent; any member. The file chips ask once per page render for their "open page" button and the menu's "Open page" / "Add to library" |
| POST | `/pages/from-file` | "Add to library" on a markdown file chip: body `{filename: "<hash>.md", original?, folder?}` (`folder` a folder id) → `{page, created, imported?}` — the stored upload becomes a note page through the `/import/markdown` importer (title from front matter, else `original` minus its extension), filed in `folder`; idempotent (a page whose `markdown_import` is the hash is returned with `created: false`); the file is untouched, the page is a copy. 400 for anything but a stored markdown name, 404 when the file is not in the workspace; workspace editors |
| POST | `/pages/{page_id}/attachment` | attach a PDF to a page that has none: body `{doc_id?, source_url?, original_filename?}` (at least one of `doc_id`/`source_url`; `doc_id` is shape-validated only — a URL-opened PDF's id is the URL hash and the proxy fetches it lazily, like `by-doc`; `source_url` is stored only when it is not `doc_id`'s own copy, which `page_attachment` derives). While the title is still automatic (`Untitled`/empty) it becomes the file name / URL tail and is marked `auto_title`. → the updated block. 400 bad input / not a root page, 404 unknown page, 409 `{"detail": "page already has an attachment"}` (`PUT` replaces one), 409 `{"detail": "attachment belongs to another page", "page_id"}`, 409 `This page's PDF is in Zotero and comes from there: use Download now on the page` while something still brings the PDF the page's Zotero item holds (its `zotero_pdf` mark, in any state but `unavailable`): the workspace's Zotero link, detached included, or the origin of a whole-workspace clone ([zotero_sync.md](zotero_sync.md#pdfs)); a mark nothing honours any more goes with the attach |
| PUT | `/pages/{page_id}/attachment` | replace the page's PDF with another file (a new version: a paper compiled again from its TeX): body, title rule, the 409 `attachment belongs to another page` and the Zotero refusal as `POST`'s, and a page without an attachment gets this one as `POST` would give it. The work is `routers/pages.replace_attachment`'s, which a Zotero link calls too when it brings Zotero's file (client `zotero`, no check of another page holding the file, the relocation run owned by the link's owner). The page keeps its id, notes and highlights. When the stored file changes (a duplicated page's `/api/uploads/<doc_id>.pdf` address counts as that stored file) and the page has highlights placed on it, the same transaction writes the outgoing file's id as `pdf_position.doc_id` on each of them that names no file yet and drops a lost one's `closest` ("The highlight shape" above), and after the commit the `relocate-highlights` job starts, answered as `job` beside the block: each highlight is found again in the new file by its text and moved there, one found roughly drawn dashed (`approx`), one not found marked `lost` ([highlight_relocation.md](highlight_relocation.md)); until it ends they sit where they were. When no run starts the answer carries the reason as `relocation_error` instead and the replacement stands: the account's background tasks are full, or the last run for this file is still stopping (`POST /jobs/relocate-highlights` places the highlights later), or the new PDF is shown from a URL alone with no `doc_id` (`the PDF is not stored on the server`: there is no file to read). The same attachment again, or the same stored file from another address, changes nothing of the kind (no batch, no job). When the file changes (from a `doc_id`, or from a `source_url` alone as on a duplicated document page), the blocks imported from the old file's embedded annotations get `annot_stripped` in the same transaction ([import_export.md](import_export.md#importing-annotations-embedded-in-a-pdf)) and a failed metadata lookup's `meta_error` goes (it read the old file); the old file's search rows and manifest go, and the file is left to the orphan bookkeeping like a detached one. Other tabs on the page switch their viewer to the new file when the batch reaches them. → the updated block |
| DELETE | `/pages/{page_id}/attachment` | drop `doc_id`/`source_url`/`original_filename` (highlights keep their `pdf_position`; the file stays, and unless something else references it is purged 30 days on — a re-attach before then finds it, [user_db.md](user_db.md) "Stored files") → `{ok, block}`; 404 when the page has no attachment |

The writers need a workspace editor (`require_ws(write=True)`): a share token
never creates pages or touches a page's attachment. `GET /pages/{id}/export*` live
in `export.py`.

A build script can replace a page's PDF after each compile, with a
write-scope integration token ("Integrations and MCP" below) and the page's
id: upload the file, then `PUT` its `doc_id`; the highlights follow their
text into the new file ([highlight_relocation.md](highlight_relocation.md)).
That run is the token's account's `relocate-highlights` job, shown in the
web app's Background tasks; the token itself cannot read `/jobs` (403),
though the answer names the job. Re-running the script on an unchanged
file stores nothing new and changes nothing.

```sh
set -e  # a failed upload stops here, before the PUT
DOC=$(curl -fsS -H "Authorization: Bearer $GAMMA_TOKEN" -F file=@main.pdf "$GAMMA_URL/api/uploads" | jq -er .doc_id)
curl -fsS -X PUT -H "Authorization: Bearer $GAMMA_TOKEN" -H "Content-Type: application/json" \
  -d "{\"doc_id\": \"$DOC\", \"original_filename\": \"main.pdf\"}" "$GAMMA_URL/api/pages/$PAGE_ID/attachment"
```

### Recently deleted (`routers/trash.py`, `gamma/trash.py`) — see [home_library.md](home_library.md) "Recently deleted"
| Method | Path | Purpose |
|---|---|---|
| GET | `/trash` | the pages deleted in the last 30 days, the last deleted first → `{pages: [{id, title, folders, deleted_at, deleted_by, purge_at}], keep_days}` (`folders` the ids it was filed under); any member |
| POST | `/trash/{page_id}/restore` | back under `root`, last in the library, filed where it was; the page is touched `live` (the change feed shows the page again) → the page's block dict; 404 when it is not in the trash |
| DELETE | `/trash/{page_id}` | delete one page of the trash for good (`ops.delete_page`: blocks with their notes index rows, op log, chats — active and history; its files go to the orphan check) → `{ok, id}`; its `deleted` row of the change log stays as its trashing wrote it; 404 when it is not in the trash |
| DELETE | `/trash` | delete every page of the trash for good → `{deleted: [ids]}` |

The writers need a workspace editor, like deleting a page; share tokens never
reach the trash. The sweeper in `gamma/trash.py` deletes pages trashed more
than 30 days ago the same way, every hour.

### PDFs & uploads (`pdf.py`, `uploads.py`)

The PDF endpoints below fetch through the same guarded path as the saved
publisher connections ([Publisher sessions](#publisher-sessions-routerspublisher_sessionspy)).

| Method | Path | Purpose |
|---|---|---|
| POST | `/resolve-pdf` | URL/arXiv/DOI → fetchable PDF (citation_pdf_url sniffing, Unpaywall OA fallback) |
| GET | `/pdf` | proxy/download a PDF (`save=1` caches it server-side); a copy already cached answers 302 to `/uploads/<id>.pdf`, carrying the request's `share` / `ws` query so the browser's follow-up stays in the same library |
| POST | `/uploads`, `/upload-image` | store a PDF / an image (content-hash names, dedup'd; quota-gated) |
| POST, DELETE | `/uploads/parts`, `/uploads/parts/{token}`, `/uploads/parts/{token}/finish` | a PDF's upload in parts (`gamma/upload_parts.py`; [user_db.md](user_db.md) "Stored files"), for files a proxy in front of the server would refuse in one request (Cloudflare caps a body at 100 MB): `POST /uploads/parts {size, name, kind?}` opens it → `{token, part_bytes, received: 0, kind}`, running `/uploads`' quota check (507) before a byte travels (429 past 8 open per workspace); `kind` is `pdf` (the default) or `file`, any other file stored as `/upload-file` stores it (400 for an executable or a `.ink`), and is echoed so a client can tell an older server, which takes every upload for a PDF; `POST /uploads/parts/{token}` with multipart `part` + `offset` (the bytes held so far) appends one → `{received}`, a 409 carrying `received` when the offset is not where the upload stands (the client goes on from there); `POST …/finish` stores the whole → `{doc_id, source_url, size, already_existed}` like `/uploads`, a `file` → `{url, name, size, already_existed}` like `/upload-file` (400 while bytes are missing, or when it is not what its extension says); `DELETE` drops an unfinished one. Members with write access; the web client (`shared/lib/uploadParts.js`) and the extension send files past 32 MiB this way, in parts of 32 MiB (the server takes up to 64 MiB) |
| POST | `/upload-file` | store a file for a block to reference as `[name](/api/uploads/<hash>.<ext>)` — the file chip. Any extension except executables (`storage.BLOCKED_EXTENSIONS`: exe, msi, bat, dll, ps1, …; 400 "not accepted (executable)"); the extension comes from the uploaded name, lowercased, `.bin` when there is none; images route like `/upload-image`, a `.pdf` must be a real PDF and lands under the same `<hash>.pdf` the PDF ingest mints (so it can be opened as a document page later), an `.ink` must be a valid `gamma-ink` version 2 drawing (the route a mirror pushes drawings by; a version 1 file is 400 `update Gamma`), a `.webm` / `.m4a` / `.ogg` / `.opus` (a recording's segment) must open as that audio container (`storage.is_audio`, 400 "not a valid audio file"); same hashing + limits → `{url, name, size, already_existed}` |
| POST | `/upload-ink` | store a handwriting group's `gamma-ink` version 2 JSON (the request body; validated against `gamma/ink.py`'s schema and limits — 6 MB, finite numbers, coordinates within ±10 000 000 pt, the extras caps — stored as it came, so the clients' `serializeInk` bytes dedup and a client can name a file by its hash) as `<hash>.ink` → `{url, size, strokes, bbox, pdf_position, already_existed}`; a version 1 file is refused with 400 `update Gamma` (the one-time upgrade converted every stored file; [handwriting.md](handwriting.md) "Upgrading version 1"); editors and edit shares. [handwriting.md](handwriting.md) |
| GET | `/pdf-info/{doc_id}` | the document manifest the viewer lays a PDF out from before pdf.js has parsed it (`gamma/pdf_meta.py`, [pdf_loading.md](pdf_loading.md)): `{doc_id, bytes, pages, dims: [[w, h], …]}` in PDF points, rotation applied; same access rule as the file; computed in PyMuPDF on first request when the upload-time background walk has not run (`pages: 0` for an unreadable file, not cached); 400 malformed id, 404 no such file |
| GET, HEAD | `/uploads/{filename}` | serve stored files (HEAD: the headers alone, which is how the viewer learns a file's size before choosing its transport); with their media type (`storage.FILE_MEDIA_TYPES`, else `application/octet-stream`); pdf / images / txt / md and audio (`storage.AUDIO_EXTENSIONS`, so an `<audio>` element plays them) render inline, everything else is `Content-Disposition: attachment` (html additionally sandboxed like svg); blocked or malformed extensions 400. Range requests are answered from the stored file ([user_db.md](user_db.md) "Stored files"). Sync `def`: a share visitor's access check reads the shared pages in the threadpool, unless a yes is remembered (a file the pages reference, or the visitor's own fresh upload: "Auth model" above) |
| GET | `/quota` | the quota (`quota_mb`, 0 = unlimited) that applies to uploads into the request's workspace — the account's for a personal one (`used_bytes` = all its personal workspaces), the workspace's own for a shared one — with `workspace_bytes` and `account` (the person, or "") |

### Shares (`shares.py`)

One share link per page or per folder of a workspace. What a token reaches and who gets in: "Auth model" above. A new link's token is `<workspace id>.<secret>`; links minted before schema version 32 do not open ("Unknown tokens" above).

| Method | Path | Purpose |
|---|---|---|
| POST | `/share/{page_id}` | create the page's share link (defaults `anyone`/`view`; optional body `{audience, role, users}` applies to a NEW link, validated like a PUT — the Share popover always sends one: `users`/`view` from its Share button) or return the existing one unchanged — root blocks only (400 otherwise); workspace editors and owners |
| GET/PUT/DELETE | `/share-settings/{page_id}` | read settings (`{token: null}` when unshared; any member) / change `audience`, `role`, `users` (`["carol"]` or `[{name, role}]`; validated: unknown usernames or roles → 400; the token stays; `edit`+`anyone` is allowed — see "Link visitors" above) / stop sharing (the token dies, its invitations with it, and the account server is asked to drop the mailed links of those by e-mail) — editors and owners. The settings are `{token, page_id, folder, audience, role, users, pending, created_by}` plus, for a caller who manages the share (an editor or owner, no guest), `requests`: `pending` the invitations by Gamma Cloud username still waiting for a first sign-in (`[{subject, username, role, invited_by, created_at}]`), followed, for a manager signed in with a session (not an integration token), by the invitations by e-mail (`[{id, email, role, invited_by, created_at, mailed_at, expires_at}]`; nobody else gets the addresses), `requests` the requests to join that came through the link and wait (`[{subject, username, email, created_at, updated_at}]`, [cloud_accounts.md](cloud_accounts.md) "Requests to join"). A PUT's `users` replaces the accounts invited only. An invited account's entry carries `cloud`, its Gamma Cloud username, where that differs from its name here (a `tim-2`, [cloud_accounts.md](cloud_accounts.md) "Which local account") |
| POST | `/share-settings/{page_id}/invites` | invite someone who may have no account here, naming exactly one of four (400 otherwise; role `view` default). By e-mail (`gamma/email_invites.py`): `{email, role}` — the account server mails the address on the caller's own linked Gamma Cloud grant, whoever it belongs to (an account here, one the share lists, the caller: only the mailed link admits, and an entry keeps its role), and the invitation waits (`invited: {pending: entry}`, the same answer for every address); inviting the address again keeps the invitation, changes its role and mails it again; `{invitation, role}` gives a waiting invitation by e-mail (its `id`) that role, no mail (404 for none). By Gamma Cloud username (`gamma/shares.py`): `{username, role}` looks the name up at the account server on the caller's own grant; `{subject, role}` names a cloud account the share knows, with no lookup — a waiting invitation (its role changes) or a request to join through the link (approved). Someone whose cloud identity is linked to an account here is invited at once, as by username (`invited: {user, username}`); anyone else waits, `invited: {pending: {…}}`, and inviting again changes the role. Either way their request through this link goes. Answers the settings plus `invited`. Editors and owners with a session, never a guest or an integration token (403); 404 unshared; 400 for a bad role, username or address, no such cloud account, your own cloud account (by username), a subject the share does not know, 100 waiting already, cloud sign-in off (an e-mail: where `email_invites.available` says no, with its reason), or what the account server refused; 503 when the account server cannot be asked or did not mail it (your own account not linked); 429 past 30 lookups in 10 minutes per account, a workspace's invitations included, or 20 e-mail sends and resends an hour, or when the account server says too many |
| DELETE | `/share-settings/{page_id}/invites/{subject}`, `/share-settings/{page_id}/requests/{subject}` | withdraw a waiting invitation by username (404 for none); decline a request to join through the link (404 when none waits; a declined person's sign-ins ask nothing more until an admin removes it). Both answer the settings — editors and owners |
| DELETE / POST | `/share-settings/{page_id}/email-invites/{invite_id}`, `…/email-invites/{invite_id}/resend` | withdraw a waiting invitation by e-mail (the account server is asked in the background to drop its link) / mail it again (a new mail under the same link id, whose link replaces the one sent before, the sender as its inviter, 14 days from now; the send's 400, 429 and 503 as above). Both answer the settings; 404 for none. Editors and owners with a session, never a guest or an integration token |
| POST | `/share/folder/{folder_id}` | the same for a folder (an empty one too): 404 unless `folder_id` is a folder of the workspace |
| GET/PUT/DELETE | `/share-settings/folder/{folder_id}` | the folder share's settings, changes and stop, as for a page. The share names the folder by id: a rename or a move changes nothing; it dies with the folder, whichever path deletes it (`ops.after_commit`) |
| POST / DELETE | `/share-settings/folder/{folder_id}/invites`, `…/invites/{subject}`, `…/email-invites/{invite_id}` (and `…/resend`), `…/requests/{subject}` | a folder share's invitations by e-mail and by Gamma Cloud username and its requests to join, as for a page |
| GET | `/share/{token}` | resolve a link for this viewer → `{page_id, folder, username (who shared it), workspace_id, audience, role, can_edit, viewer, viewer_is_guest}` plus, for a page share, `doc_id` (the page's PDF attachment id via `page_attachment`, `""` without one), for a folder share `folder_name` (`folder` is its id); a folder share's listing is `GET /blocks/root/children` through the token; `viewer`/`viewer_is_guest` let the share view offer "Open in my library" or "Add to my library" (on a page, and on a folder share's listing); 404 unknown (a token of another shape or with another workspace's prefix included; or a page share whose page is deleted or in Recently deleted, until a restore; or a folder share whose folder is gone), 401 sign in first, 403 signed in but not allowed |

### Folders and labels (`folders.py`) — see [home_library.md](home_library.md) "Folders and labels"

Folders and labels are blocks of the `folders` / `labels` trees: they are
listed with the library (`GET /blocks/root/children`, above), read as one
tree (`GET /blocks/folders/subtree`), followed live (`WS /ws/page/folders`)
and changed by ops on the tree (`POST /pages/folders/ops`: `insert` a
folder under `folders` or a folder, `set` its `content` to rename or
`properties.pinned` to pin, `move` to move or reorder; labels under
`labels`, flat). A page is filed by `set`ting its root's `folders` /
`labels` (lists of ids) in a batch on the page. The one change that reaches
pages has an endpoint:

| Method | Path | Purpose |
|---|---|---|
| DELETE | `/folders/{folder_id}` | delete the folder with its subfolders and, in the same transaction, take their ids off every page filed under them (one op batch per page, logged and fanned out like any) → `{ids, pages, chats, shares}`: the folder ids deleted, the pages refiled (in library order), the conversations of the deleted folders filed into the library chat's history (`home`), the share links stopped. 404 unless it is a folder; workspace editors, never through a share link |
| DELETE | `/labels/{label_id}` | delete the label and take it off every page → `{ids, pages, chats: 0, shares: 0}` |

### Search (`search.py`, `gamma/block_index.py`, `gamma/pdf_index.py`)
| Method | Path | Purpose |
|---|---|---|
| GET | `/search?q=&limit=&scope=` | one search over the knowledge base: notes (`block_fts`) + PDF text (`pdf_fts`). `scope` = `""` (library) or a folder's id (the pages filed in that folder or below it; an id that is no folder finds nothing). → `{"results": [...], "indexing": n}`; results are notes hits first (bm25 order) then PDF hits, each capped at `limit` (default 20, max 100). A notes hit is `{"source": "notes", "block_id", "page_id", "title", "snippet"}` (`block_id` = the matched block, `page_id` its page root, `title` the page's); a PDF hit is `{"source": "pdf", "block_id", "page_id", "doc_id", "title", "page", "snippet"}` (`block_id` = `page_id` = the page carrying the PDF, `page` the 1-based PDF page). `indexing` = the PDFs the background extractor hasn't reached (the notes index is never behind: a note is found the moment its write commits). Any member |
| GET | `/pdf-search?q=&limit=&scope=&cursor=&exclude=` | the PDF-only predecessor (same `pdf_fts` index; hits `{block_id, doc_id, title, page, snippet}`) — the Ctrl+F panel's library group still uses it (with `/block-search` for notes: substring matching + case / whole-word flags that FTS does not offer). `scope` takes the chips as `/block-search` does, `exclude` a page whose PDF is left out (the open paper, whose matches the panel finds with pdf.js); both apply before the ranking. A paper lists at most its best 3 hits (`PDF_HITS_PER_PAPER`) before every other paper has listed its best 3, then its next 3, and so on, so one long book can't fill a page. `limit` (default 20, max 100) hits from `cursor` (the hits listed before, an offset) on; `next` is the following page's cursor, there when more hits follow (400 for a malformed cursor) |
| POST | `/search-reindex` | full rebuild (the notes index rebuilt from the blocks in the request, PDF text re-extracted in the background), or just `doc_ids` from the body → `{scheduled, busy}`. The extraction is the workspace's `indexing` job ([tasks.md](tasks.md)): its members see it in `GET /api/jobs`, and its editors stop it with `POST /api/jobs/{id}/cancel` (it finishes the paper it is on; the rest stay stale and index on the next search) |

The notes index is `block_fts` in pages.db, kept by triggers inside every
block write's own transaction ([user_db.md](user_db.md) "The notes index"):
every block inside a page, highlights included, never a page's own row. A
page in Recently deleted keeps its rows; `block_index.search_blocks` takes
the page ids the search reaches (the library, or the `scope` folder's) and
matches only those, in SQL, before the `limit`. Snippets come from FTS5's
`snippet()` over the normalized text. The PDF index (`pdf_fts` in data.db) is the one built lazily. FTS5 finds rows
only by rowid or MATCH, so `pdf_fts_rows` maps each paper to its rows'
rowids: every delete goes by rowid, and `search_pdf` keeps to its papers by
their rowids, in SQL before the ranking and its `limit` / `offset` (read
off the matches, not looked up one rowid at a time, which a prefix query
makes slow); an index written before that table
existed is taken over once when it is created. Deleting a page for good,
deleting a block that carries a PDF or detaching a PDF drops the `pdf_fts`
rows and manifests of papers no block carries
(`pdf_index.purge_unused`). The `pdf_fts` schema and its shared queries
(`pdf_missing`, `search_pdf`, `store_doc`, `doc_pages`) live in
`gamma/pdf_index.py`; extraction and the background indexer in `search.py`
(a paper's rows are written a few hundred pages per transaction).

#### Search matching and display

Search normalization lives in `gamma/textnorm.py` and its frontend mirror,
`shared/lib/textnorm.js`: ligatures, hyphenated line breaks, and digit
separators should match consistently. The extraction (`pdf_text`, through
PyMuPDF) hands over the page as it is — a hyphen at an end-of-line word
break stays, where pdfium joined some of them, and a ligature comes out as
its letters — and `textnorm` re-joins the word for the index and for
queries alike. Bump `textnorm.INDEX_VERSION` when
extraction or normalization changes so old indexes rebuild. Add shared cases
to `tests/shared/textnorm.json` and run both Python and Node coverage.

`search/SearchPanel.jsx` groups titles, the current page's notes and PDF,
other notes, reference links, and library PDF hits. It can collapse into a
compact find bar. Its folder and label chips go to `/block-search` and
`/pdf-search` as `scope`, so the server ranks and limits inside them and a
match in the chosen folder is found however many elsewhere outrank it. The
groups start at 8 titles, 20 notes and 15 PDF pages; each has a "Show more"
that appends the next page (`search/searchPages.js`): the notes and PDF
groups send the last answer's `next` back as `cursor`, and the titles,
matched in the browser, just show 8 more. A notes scan cut short by its 2 s
budget keeps its "stopped early" line, and its "Show more" goes on from
where it stopped. An answer for a search the panel has moved past (a new
query, flags or chips) is dropped. `buildSearchRegex` supplies the
frontend matcher; the server's `/block-search` uses its own bounded fuzzy
scan after an SQL prefilter and refuses caller-supplied regexes. There is
no replace UI.

Opening a library content hit pins the query across navigation. Once the PDF
renders, `PdfViewer` re-finds the query through `searchRef` over normalized
page text, maps character offsets back to rendered rectangles, and scrolls
to the match. Search indexes store text, not highlight coordinates: the
positions must come from pdf.js, which renders the page.

### Link previews (`links.py`)
| Method | Path | Purpose |
|---|---|---|
| GET | `/link-preview?url=` | webpage title for the frontend's link chips (`{url, host, title}`); fetch goes through the SSRF guard, results cached in-process (TTL 24 h) |

### Browser extension (`clip.py`) — see [extension.md](extension.md)
| Method | Path | Purpose |
|---|---|---|
| POST | `/clip` | one-shot "save this page": dedup by DOI/arXiv/URL → resolve → fetch + store (`save_copy`) → page (`get_or_create_doc_page`) → folder/labels → metadata in a background thread. Body: `source_url, pdf_url, doi, doi_from_text, arxiv_id, record, doc_id (pre-uploaded bytes), title, selection, folder, folder_path, labels, allow_oa, save_copy, web_fallback` — `doi_from_text` marks a DOI the page only printed in its text (it finds the paper and its PDF, but its record must be confirmed by the PDF), `record` the page's citation tags; both are kept on the page as `properties.web_meta` for every later lookup ([paper_metadata.md](paper_metadata.md)); `folder` a folder id (400 when it is no folder), else `folder_path` a folder by its names (`"a/b"`, made where missing: the popup's "New folder…"), `labels` label names (made when missing); filing adds (the page keeps its other folders, a folder above the new one gives way). Returns `{block_id, doc_id, title, existed, open_url, folders, labels, note?}` (`folders` / `labels`: the page's filing, as ids; `existed`: the page was there already, found by identifier, URL or its PDF). **No PDF resolvable** (a plain web page, or a dead/HTML link) → a page titled from `title` (else the URL tail) with `properties.web_url = source_url` and the `selection` (if any) as its first `> quote — [title](url)` block; `doc_id` is `""` and `note` says so. Re-clipping that URL finds the page (`find_web_page`, by `web_url` on attachment-less pages), files it and appends the new selection. Only a request with nothing at all (no URL, title or selection) is a 400; with `web_fallback: false` (the extension, while it can still download the PDF in the browser) a clip without a PDF is the 400 too, and no page is made |
| GET | `/library/lookup?doi=&arxiv_id=&url=` | is this page in the library (`properties.meta`, `source_url`, `web_url`, URL hash — web-clip pages by `web_url`)? 404 when not |
| GET | `/library/preview?doi=&arxiv_id=&url=` | the registry record behind an identifier (arXiv API, then doi.org): `{title, authors, year, venue, doi, arxiv_id, source}` — the popup's title for a PDF tab before anything is saved. Identifiers are also extracted from `url`; 404 when no registry answers, 400 without an identifier. Cached in memory per identifier |
| GET | `/library/folders` | the popup's pickers: `{folders: [{id, path: [names from the top]}], labels: [{id, name}]}` — every folder (empty ones too), recently viewed first (a folder takes its pages' and subfolders' view and edit times), every label by name |
| POST | `/clip/note` | the explicit "clip into page" append: `> quote — [title](url)` as the last block of `page_id`, or of the "Web clips" page (created on first use) |

All five are session-only, never share-token readable; the clip lands in
the request's workspace — the one the extension's workspace picker names
(`X-Gamma-Workspace`), else the account's default
([extension.md](extension.md)).

### Metadata (`metadata.py`)
| Method | Path | Purpose |
|---|---|---|
| POST | `/metadata/fetch` | resolve a paper or book (arXiv → DOI → ISBN via Open Library/Google Books → Crossref search → the web page's record → the AI's pick among the records the text names → AI extraction, verified against Crossref / the book registries), cache meta + BibTeX + the slide citation on the page. Body also takes `cite_prompt`/`cite_model`; returns `meta` (with `match`, what tied it to the document, and `unverified`, which follows from it, [paper_metadata.md](paper_metadata.md); and `kind`, its item type in Zotero's names, when a source named one), `bibtex` (the entry as every surface prints it, the pinned key applied), `ppt_cite` (`""` when AI is off or that call failed), `source`, `cite_key` (the page's pinned citation key, `""` when none), `cached`, `page_title` (the page's title after the write — always, since a concurrent lookup may have renamed it) and `title_updated` (this call replaced the automatic title; an unverified record never does) |
| POST | `/metadata/update` | save hand-edited fields incl. `publisher`/`isbn` and `kind`, the Type row's item type (rebuilds BibTeX, keeps the stored type when `kind` is not sent or names none, drops the cached citation). `cite_key` pins the citation key the BibTeX is written under: omitted leaves the page's pin alone, `""` unpins, and clearing the record clears it too. A `doi` or `arxiv_id` that differs from the stored record's is looked up, and the registry's record (with its BibTeX) replaces the sent fields; `lookup` is `{registry, id, found}`, or null when no identifier changed. → `{meta, bibtex, cite_key, source, cached, lookup}` |
| POST | `/metadata/verify` | the user vouches for the stored record (the metadata popover's Verify): `meta.unverified: false`, `meta.user_verified: true`; source and BibTeX stay. 409 without a record. → `{meta}` |
| POST | `/metadata/cite` | BibTeX → PPT-style citation via AI (regenerate / fallback; the fetch already produces one) |
| GET | `/metadata/status` | library-wide health table (feeds Settings → Maintenance): every page with a PDF attachment plus pages carrying `properties.meta` without one (`has_file: false`); per paper `meta_source`, `meta_kind`, `meta_unverified` (null for pre-flag records), `meta_match` (what tied the record to the document, [paper_metadata.md](paper_metadata.md); `""` for older records, hand edits and imports), `meta_user_verified`, `meta_at` (when the record was looked up or edited, `""` for older records) and `meta_error_at` (when a lookup last found nothing) |

### AI (`ai.py`) — all config is GUI entries (each account's own plus the server's shared ones), no env API keys
| Method | Path | Purpose |
|---|---|---|
| POST | `/ai/chat` | chat; NDJSON stream of `{context}` (first line: per-page coverage — `page_id`, native/text, pages shown of total, `notes` when the page's notes are in it; `doc_id` `""` for a page without a PDF; the open paper's entry adds `selection: {passages: [{page, section, found, crop}]}` when passages were selected), `{model: {id, name, effort, speed, tools}}` (which model answers, at what effort and speed — `""` = the provider's default — and whether tools were armed; `speed` is the tier the wire asked for, and when the provider's report says the turn ran at another the line comes again, corrected, right before that turn's `{usage}` line — the client keeps the latest; non-stream replies carry the same `model` field, with the served speed), `{trimmed: {turns}}` when the oldest history items were left out to fit the model's window (non-stream: a `trimmed` field), `{shortened: {items, budget, asked}}` when a selected passage or attached block went short — each `items` entry `{kind: "pdf" | "note" | "block", n, of, kept, total}`, `budget` the characters they shared, `asked` the request's `context_char_limit` (more than `budget` when the window fit shrank it; non-stream: a `shortened` field), then `{delta}`/`{step}`/`{approval}`/`{handoff}`/`{action}`/`{progress}`/`{usage}`/`{truncated}`/`{limit}`/`{error}` (`truncated: true` = the provider cut the reply off at its output limit; `limit: {kind: "rounds" | "changes" | "window", rounds, changes, withheld?}` = a tool-round, change or window limit stopped the reply, its last line (non-stream: a `limit` field); `withheld: true` = Unlimited was asked for but the shared connection keeps the limits; a read action carries `pdf_pages: [first, last]`) (`step` = `{id, tool, args}` announcing a tool call before it runs, plus `batch: n` (and `tools`) when the round's reads run side by side, and `items: n` for a call naming several pages (rename_page, move_page, label_page, restore_page); `approval` = `{id, call_id, tool, perm, args, preview, timeout}`, the call waits for `POST /ai/approvals/{id}`; `handoff` = `{id, call_id, host, wall, source, timeout}`, the reply waits for the PDF from the user's browser until it arrives, is skipped (`DELETE /ai/handoffs/{id}`) or the wait gives up; `helper` = `{id, label, state, steps, step?, blocked?}`, what a helper a running `read_paper` started is doing — its whole state each time it changes, `state` one of `reading`, `answering`, `done`, `failed`; a rename/move `action` adds `title`, `from`, `to` (rename, move and label: `revert` too), a call naming several pages `changes`, `items` (each page as the one-page action names it) and, but for a restore, `revert` instead ([ai_tools.md](ai_tools.md) "Several pages in one call"), a note tool's its page's `title`, a change that changed nothing `noop`, a `fetch_paper` stopped by a wall `handoff: {id, host, wall, source}`, one the browser then delivered `delivered: true` and one the user skipped `skipped: true`, a fetch its `version`, `probe` and the PDF pages its window read as `pdf_pages`, a `read_paper` (kind `helper`) its helper's calls as `children` and their count as `steps`, every action `ms`; a call that waited on its card `approval` (the decision), one not made `declined: true`, a batch's with its `count`); a failure, as an HTTP error body or the stream's closing `{error}` line, carries `kind` (`not_configured` / `allowance` / `auth` / `rate` / `overloaded` / `unreachable` / `bad_endpoint` / `too_long` / `other`), the upstream `status` and `provider_id` / `provider_name` / `provider_auth` beside the plain-string `detail` / `error`; `usage` is the provider's token report `{input, output, cache_read, cache_write}`, one line per provider turn (the client sums an agent reply's rounds; non-stream replies carry one summed `usage` field); `progress` previews an edit_block/create_block call still being written (target id + markdown so far). Context is `pages` (up to 7 page ids, de-duplicated; they also become the tool scope's `context_pages`) or `page_id` (one; its PDF attachment derived server-side; `doc_id` is accepted as a compatibility input and resolves to its page), plus `chat_key` (the conversation's bucket: a page id, a folder id or `home`; hashed with the account and workspace into the provider's prompt-cache id), model id, effort, `speed` (one of `ai_protocols.SPEED_ORDER`; anything else is ignored), images, files, the agent scope (`agent_scope` `folder` with `folder` the folder's id, `""` the library root — 404 "That folder no longer exists" for an id that is no folder — or `page`), the selected PDF passages `selections` (`[{text, page, box}]`, at most 12, each whole, box `[x0, y0, x1, y1]` page fractions; the older `"---"`-joined `selection` string is still read), and the notes pointers `focus_block_id` (cursor block), `context_blocks` (attached block ids, 12 served), `note_selections` (selected note text as exact source ranges `[{block_id, from, to, text}]`, at most 13, what `edit_block` mode `"selection"` rewrites) and `ink_selections` (handwriting selected on a page, `[{page_id, block_id, space, strokes}]`: the selected strokes of an ink group in the group's `space`, saved or not; at most 8 entries and 64 KB of JSON together, a 422 past either; an entry whose `page_id` is not a context page, or whose strokes do not parse, is dropped; each sends a picture of its strokes, [ai.md](ai.md) "Pointing the chat at notes"); the quoted passages share `context_char_limit`, and so do the attached blocks. `permissions` maps each tool permission to `"allow"`, `"ask"` or `"off"` (left out: reading allows, a change asks; `true` / `false` are allow / off), and `granted` lists the permissions the conversation allowed on a card; only a streamed request arms asking tools. `paper_wait` (default true) decides whether a blocked fetch holds the reply open on its card, and `delegate_reads` (default true) whether `read_paper` is offered. `tool_rounds_unlimited: true` on a streamed request lifts the round and change limits (not on a shared connection without a daily allowance, unless the user is an administrator). See [ai.md](ai.md) |
| POST | `/ai/revert` | take back one change an agent reply made to the notes, or a folder or label it deleted, or with `redo: true` put a reverted one back: `{kind: edit \| create \| move \| delete \| pages, block_id, revert, force?, redo?}` (`revert` as the tool recorded it on the action) → `{page_id, noop, kept?}` (`noop`: the note already reads as before); 409 `{detail, conflict, preview?}` when it stops — `changed`, `filled` or `moved` (the note changed since; `preview.diff` is what `force: true` would do, `preview.children` the notes under a new block) or `gone` (can't go back, not forcible); 404 when the note is gone; workspace editors only. A deleted folder's or label's `revert` carries `tree` (with `blocks` and `pages`); `page_id` is `""` then; its `gone` stops are the folder above gone, the name taken again, or (redo) a share on the folder. A page tool's `revert` (`kind: "pages"`, `block_id` `""`) carries `pages`; it is never forced: `kept` counts the pages changed since, deleted or in Recently deleted, which it leaves as they are, and when that is every page it answers 409 `{conflict: "kept", preview: {kept}}` with nothing written. The write is the user's, logged with client `revert`. See [ai_tools.md](ai_tools.md) "Reverting a note change" |
| POST | `/ai/approvals/{id}` | the answer to a tool call waiting on its approval card: `{decision: once \| chat \| always \| deny, note?}` → `{ok: true}` (`note`, up to 2000 chars, is what to do instead, kept with `deny` only); only the account whose chat asked; 404 when nothing of theirs waits under that id (answered, timed out, or the chat stopped), 422 for another decision |
| POST | `/ai/chat/context` | the same body as `/ai/chat` (plus `title`) → `text/markdown`: what the chat would send the model — pages in context, system prompt, tools, every turn with the draft `prompt` last; PDFs as extracted text, pictures counted; calls no provider, so it works with none set up (the conversation itself, with its pictures, is `POST /chats/export`) |
| GET | `/ai/models` | model registry (each model carries `native_pdf`: whether its provider accepts the PDF file itself, and `shared`: it comes from a server entry, `server:<id>:<model>`) + default prompts (feeds the model chip and prompt editor) + `transcribe` (some connection takes dictation: the chat's mic shows) + `efforts` (the reasoning efforts offered for a model whose own levels are unknown) + `allowance` (`{limit, used, exhausted}` for the shared entries, `limit` 0 = unlimited; null when none applies — [guests.md](guests.md)) |
| GET | `/ai/settings` | masked provider list (key hints only, each with its display `label`), then the server's shared entries the account may use as read-only rows (`shared: true`, key hint for admins only), plus the `protocols` and named `services` (e.g. DeepSeek) the add form offers, each with the key field's `key_placeholder` and `key_url` ("" for a sign-in protocol) |
| POST/PUT/DELETE | `/ai/providers[/{id}]` | manage the account's own provider entries (a shared `server:` id is a 404 here) |
| POST | `/ai/providers/{id}/test` | live probe of one credential (model: the entry's `test_model`, else the request's `model` — the client sends its metadata model — else the first model); failures carry an `auth` flag for expired/rejected credentials and the failure's `kind` (as on `/ai/chat`). Admins may name a shared entry (`server:<id>`) |
| POST | `/ai/providers/{id}/usage` | ChatGPT subscription allowance windows; explicitly unavailable for generic API-key providers; an expired sign-in returns `{available: false, auth: true}` in-body, and one whose renewal can't reach OpenAI `{available: false, auth: false}` with a reason |
| GET | `/ai/usage` | the account's token usage as the providers reported it: `windows` (today / week / month / all → calls + the four counts), the 30-day split by `kinds` and by `models`, plus the shared `allowance` object; see [ai.md](ai.md) "Token usage" |
| DELETE | `/ai/usage` | forget the account's usage rows (the shared-entry rows of the last 24 h stay: the allowance still counts them) |
| POST | `/ai/health` | login connection check of one entry the account can use, its own or shared (`{provider_id, mode}`; `""` = the first): `mode: "ping"` is the free credential check (OAuth → usage endpoint, API key → `/v1/models`), `"test"` the tiny live completion; always answers in-body `{configured, ok, auth?, kind?, error?, provider_name, provider_auth}` (`kind` as on `/ai/chat` failures) |
| POST | `/ai/model-catalog` | list models available to a credential: the typed key, or a saved entry's (`provider_id`; admins may name a shared `server:<id>`) |
| GET | `/ai/model-info?model=<pid>:<model>` | what the chat needs about a model (`""` = the default one): `{model, context_window, source, efforts, efforts_source, speeds, speeds_source, images, images_source}` — its context window for the context ring, the reasoning-effort levels it takes, lowest first, and the service tiers it may run at, cheapest first, each from the entry's own model listing (`source` `"provider"`), else the models.dev catalog (`"models.dev"`); `context_window` null when neither knows it; `efforts` `[]` for a model without effort control, null when unknown (the chat then offers `/ai/models`' `efforts`); `speeds` `[]` for no speed control, with `speeds_source` `"protocol"` when the tiers are the wire's own rather than the listing's ([ai.md](ai.md#speed-service-tier)); `images` false for a model that reads text only, null when unknown ([ai.md](ai.md#other-services)) |
| POST | `/ai/oauth/chatgpt/start`, `/status`, `/complete` | ChatGPT OAuth (PKCE; [ai.md](ai.md) "The chatgpt protocol"): `start` `{local?, device?}` → `{auth_url, state, local, device}` — `local` true when the server listens on localhost:1455 for the redirect (the page claims a loopback address and the request came from loopback), `device` `{user_code, verification_url}` when a device code was asked for and OpenAI gave one; `status` `{state}` → `{ready, error}`, polling a due device code with OpenAI; `complete` `{state, callback, provider_id?, name?, models?}` with the pasted redirect URL, or `callback: ""` once `status` is ready |
| POST | `/ai/transcribe` | voice dictation |
| GET | `/ai/search-services` | the account's online search settings (`{engine, services: [{id, label, configured, web, fields, updated_at, server?}], can_edit}`): `engine` is `auto` / `ai` / `brave` / `searxng` / `off`, the services are `brave` (`api_key`), `searxng` (`url`) and `openalex` (`api_key`); secret fields as a `…last4` hint; `server: true` when the server's `GAMMA_SEARXNG_URL` serves an account without its own ([ai_tools.md](ai_tools.md) "search_web") |
| PUT | `/ai/search-services/engine` | `{engine}`: which service general web search goes through; 400 for an unknown one; guests 403; answers the GET shape |
| PUT / DELETE | `/ai/search-services/{id}` | set (`{fields: {…}}`, an empty secret keeps the stored one; `url` must be http(s) without credentials) or remove a service's settings; 404 for an unknown service; guests 403; answers the GET shape |
| POST | `/ai/search-services/{id}/test` | one small search with the stored settings; in-body `{ok, text}` / `{ok: false, error}` |
| GET | `/pdf-text-status` | whether a doc has extractable text |
| GET | `/ai/selection-crop/{doc_id}?page=&box=x0,y0,x1,y1` | the picture of a selected region a chat reply sent the model, drawn again from its saved page + crop box (page fractions); also the review card's picture of where a highlight was before its PDF was replaced (`pdf_position.prev`, or a lost one's own rectangles and `doc_id`, [highlight_relocation.md](highlight_relocation.md#in-the-web-app)); 404 for a file no longer stored; any workspace member |
| POST | `/ai/pictures` | multipart `file`: store a picture for the chat, sized for the model (longer side 1568 px, JPEG unless small or translucent) under its content hash → `{url, width, height, size, already_existed}`; writers only; 400 for bytes that are no picture, 413 over 25 MB ([ai.md](ai.md#pictures)) |
| GET | `/ai/page-image/{page_id}?page=N[&box=x0,y0,x1,y1][&ink=1]` | a PDF page of the page, or its region (page fractions), as a picture, with the user's handwriting written on it when `ink`; what a reply's view chip expands to, a region attached to a message shows, and the model embeds to show the user a page it looked at; any workspace member; 404 for a page without a PDF or a page number past the end |
| GET | `/ai/ink-image/{block_id}[?area=page]` | the user's handwriting as the `view_ink` tool shows it: a group cropped to its strokes, or the whole page / sheet with all its handwriting; any workspace member |
| GET | `/ai/handoffs/{id}` | a fetch handed to the browser (`routers/ai_handoffs.py`, [ai_tools.md](ai_tools.md#walls-and-the-browser-handoff)): `{id, source, url, pdf_url, host, wall, detail, status: waiting \| done \| dismissed \| expired, watched, note, background, pages, held, from_url}` — never the text or the PDF; 404 for another account's or a forgotten request |
| POST | `/ai/handoffs/{id}/watch` | Gamma Connector took the request's tab (`watched: true`); optional body `{note, background}`: what it is doing there (`looking`, `check`, `signin`, `opening`, `refused`, `other`, `closed`; anything else clears it) and whether it is a background tab |
| POST | `/ai/handoffs/{id}/pdf` | multipart `file` (+ optional `url` it came from): the PDF for the request, read at once and kept in memory for the account; 400 not a PDF / no text layer, 413 over 40 MB, 409 once the request is settled |
| POST | `/ai/handoffs/{id}/store` | writable workspace: the delivered PDF (while held, `held: true`) into the workspace's uploads, content-hash deduped like `POST /uploads` → `{doc_id, source_url, already_existed, url}` (`url`: where the browser got it), for `POST /clip {doc_id}`; 404 when nothing is held |
| DELETE | `/ai/handoffs/{id}` | settle the request without a PDF. Optional body `{note}` — what the user wants the assistant to do instead, which only the model reads: the card's **Skip** while the reply waits on it |
| GET | `/ai/handoffs/{id}/go` | no auth: the HTML page the card's Open leads to when it cannot hand the tab to Gamma Connector (desktop app, no answer yet) — for the request's owner it goes straight on to `url`, anyone else gets a "Continue to host?" link; 404 page when expired |

### PDF translation (`routers/translate.py`) — see [translation.md](translation.md)
| Method | Path | Purpose |
|---|---|---|
| POST | `/ai/translate` | translate paragraph texts for the viewer's translated view (`{texts, lang, model, effort, speed, stream, page_id?, head?, wait_brief?, keep?, before?, after?, section?}` → `{translations, model, cached, redone, failed, effort, brief}`: `cached` when every text was found in memory or among the kept translations, `redone` counts the texts asked again for a segment the reply left out, doubled or emptied or for a broken token, `failed` lists the indices of the texts that came back as their source because they still would not translate, `effort` is the level asked for, `brief` whether the translations carry the paper's brief (true also when there is no paper to have one); a 429 for too many AI calls at once carries `Retry-After`; with `stream: true` an NDJSON stream: `{stage: "brief"}` and later `{stage: "translate"}` around a wait for the paper's brief, `{i: [indices], text}` partials as each paragraph is written, then the same final object). `page_id` (checked against the request's workspace, 403 outside it) names the paper, whose brief the prompt carries and whose kept translations are read and added to; `head` names it only when there is no page. This route never makes the brief: `wait_brief` (default true) waits for one that `/ai/translate/brief` is making, false uses one only when it is made already. `keep` (default true) lets the request keep its new translations in the workspace, when the account may write it; kept ones are read either way. `before`/`after`/`section` are context the model reads but does not translate; `{v1}` tokens and `{br}` markers in a text must come back. `effort` "" asks for the model's lowest level. `speed` is "", `flex`, or `fast`; unsupported tiers use the provider's usual routing and services ignore it. `model: "engine:<id>"` (`microsoft`, `google`, `youdao`) translates with that machine-translation service instead — no AI provider needed, no brief, 503 when it isn't set up, never streams partials |
| POST | `/ai/translate/brief` | make the paper's brief ahead of its translation requests, find it (memory, then the kept one), or wait for the request making it (`{page_id?, head?, lang, model, effort?, speed?, keep?}`). Always an NDJSON stream: `{stage: "brief"}` first when it is not ready at once, then `{ready, error?}`; `ready: false` when there is no paper, no text to read or the call failed (`error` says why), and at once for a machine-translation service. 429 at the door (the shared allowance, too many AI calls at once) before the stream starts. `keep` (default true) lets a made brief be kept with a stored paper, when the account may write the workspace |
| GET | `/translations/stats` | what the workspace keeps: `{documents, paragraphs, bytes}`, the bytes being the kept text's UTF-8 size (translations and briefs); any member |
| DELETE | `/translations` | drop the workspace's kept translations and briefs, and the server's memory of every document in it → `{removed}` (paragraph rows); writers only |
| DELETE | `/translations/{doc_id}` | "Forget this translation": one document's kept translations and briefs in every language and model, and the server's memory of them → `{removed}`; `?page=<id>` also resets the memory of a paper with no stored file (keyed by its page); 400 for an invalid id; writers only |
| GET | `/translate/engines` | the account's machine-translation services (`{engines: [{id, label, configured, needs_key, fields, updated_at, failing}], can_edit}`; secret fields as a `…last4` hint; `microsoft` needs no key and is always configured, and its `failing` is `{since, error}` during a failure streak, else null) |
| PUT / DELETE | `/translate/engines/{id}` | set (`{fields: {…}}`, an empty secret keeps the stored one) or remove a service's credentials (400 for a service that needs no key); guests 403; answers the GET shape |
| POST | `/translate/engines/{id}/test` | translate one sentence into `{lang}` with the stored credentials; in-body `{ok, text}` / `{ok: false, error}` |

### Chats (`chats.py`, prefix `/api/chats`)

`GET /chats/{page_id}?share=<token>` exposes only the shared page's active
saved conversation, subject to the link's audience. Shared pages show this in
a read-only AI chat window on desktop and mobile, with search and copy.
Chat mutations reject share tokens, including links that allow page editing;
archived conversation browsing remains session-only. Every chat write takes
a workspace editor (`require_ws(write=True)`): a viewer or a read-scope
token reads chats and history, and gets 403 on each write below. The
conversations live in the workspace's pages.db (`chats`, `chat_history`,
keyed by `bucket`, [user_db.md](user_db.md)). A bucket is a block id — a
page's, or a folder's for the folder view's chat — or `home` (the library
root); a deleted folder's conversations are filed into `home`'s history by the
tree's `delete` op (`DELETE /folders/{id}`, or a tree batch).
| Method | Path | Purpose |
|---|---|---|
| GET/PUT/DELETE | `/chats/{key}` | the ACTIVE conversation per bucket (a page id, a folder id or `home`); GET → `{messages, title, updated_at}` (`updated_at` the conversation's version, `""` without one; a share read gets `{messages, title}`), PUT `{messages?, title?, updated_at?}` → `{ok, updated_at}` — title omitted = keep, messages omitted = a rename (title only, unconditional); with `updated_at` (the version the copy is based on, `""` = none) a save from an older copy is refused with 409 `{detail, messages, title, updated_at}`, the stored conversation, and nothing changes ([chat_history.md](chat_history.md) "Buckets") |
| POST | `/chats/export` | a conversation as a file to keep, from the tab's copy `{bucket, messages, title}`: `<title>.zip` (the transcript `.md` plus `assets/`, every picture it shows read or drawn in the request's workspace), or the bare `<title>.md` when it shows none; any member, a viewer too ([chat_history.md](chat_history.md) "Exporting a conversation") |
| GET | `/chat-history/{id}/export` | an archived conversation as the same file; 404 for an unknown id |
| GET | `/chat-history?bucket=` | the bucket's archived conversations, newest first (`{sessions: [{id, title, preview, count, created_at, updated_at}]}`) |
| POST | `/chat-history/archive` | "New chat": file `{bucket, messages, title, updated_at?, discard?}` into history and clear the active row (→ `{id}`, null when empty); when `updated_at` isn't the stored version, the newer stored conversation is archived too (unless the copy holds all of it; alone when it holds all of the copy). `discard: true` (Alt+click) drops the copy instead of filing it; the newer stored one is still archived |
| POST | `/chat-history/{id}/open` | make an entry the active conversation; the body's `{bucket, messages, title, updated_at?}` (the current one) is archived first, as for `archive` (→ `{messages, title, updated_at, archived}`, `archived` the id it was filed under, null when empty) |
| PUT/DELETE | `/chat-history/{id}` | rename (`{title}`) / delete an archived conversation |
| POST | `/chat-history/delete` | delete several at once (the history popover's ticked rows): `{ids}` → `{deleted}`, the rows that were still there |

### Import & export (`imports.py`, `export.py`)
| Method | Path | Purpose |
|---|---|---|
| POST | `/import/logseq` | Logseq .pdf + .edn (+ optional .md) import into the page carrying that PDF, created when absent (under the write lock, like `by-doc`); highlights already there are skipped by quote and notes by text, so a re-run adds nothing twice; into an existing page it is logged as a `reload` by the importing account and its room reloads → `{ok, block_id, doc_id, source_url, imported}` |
| POST | `/import/markdown` | UTF-8 `.md`/`.markdown` file → note page and nested blocks (optional `folder`, the destination folder's id; a front-matter `folder:` path files it in the folder at that path below it, made where missing) → `{…, folders}` (the page's folder ids) |
| POST | `/import/markdown-zip` | zip of Markdown notes → one page per `.md` (multipart `file`, optional `folder`, the destination folder's id; directories become folders below it, made where missing): Obsidian vaults (wikilinks/embeds → mentions and synced blocks, `^id` anchors and headings as link targets, `tags` → labels, `aliases` kept, comments and fold markers dropped, `.obsidian/` skipped), Notion "Markdown & CSV" exports (subpage folders → folders, databases → table pages, links → mentions, images uploaded), Gamma Markdown / Obsidian exports (folder/source/meta/bibtex restored) or any zipped notes. Idempotent by file digest / `notion_id`; the report says `obsidian: true` for a vault |
| POST | `/markdown-blocks` | parse markdown text into a `{content, children}` tree without storing anything (the editor's paste-as-blocks helper; same parser as `/import/markdown`; 413 past 5 MB or `markdown_import.MAX_BLOCKS` blocks, and the editor keeps the paste as text) |
| POST | `/import/pdf-annotations` | import annotations embedded in the PDF (idempotent; optional `strip`); in a workspace a Zotero link keeps, an annotation whose `/Zotero:Key` the link holds makes no block ([import_export.md](import_export.md#zotero-library-import)) |
| POST | `/import/zotero/preview` | Read-only import plan (multipart `file`, optional `folder`, the destination folder's id; workspace writer; 409 `This workspace is kept in step with <library>; its items arrive by themselves.` in a workspace a Zotero link keeps, detached included, as for `/import/review` with source `zotero`, its upload in parts and `/import/zotero`). Archive entries, destination pages with PDF/page and create/merge status, folder paths (`folders`: lists of names from the top), `attachments` (how many go under the page's "Attachments" block), attachment warnings. Uses the same planner as import; stores no pages or files |
| POST | `/import/review` | Shared staged upload/review for `zotero`, `markdown-zip`, `markdown-file`, `notability`, `goodnotes`, `gamma` (multipart `file`, `source`, optional `folder` (the destination folder's id; 400 when it is no folder), `strip`). A destination page's `folders` are paths, each a list of names from the top; the report's `folder` is the destination's id. Returns `review_id`, archive entries, destinations, source selection IDs and warnings; writes no library content. The import itself is the `import` job (`POST /api/jobs/import`, below) |
| POST | `/import/review/parts`, `/import/review/parts/{id}`, `/import/review/parts/{id}/finish` | The same upload in parts, for files past 32 MiB (a proxy in front of the server caps a request's body), speaking `/uploads/parts`' protocol: `{source, size, name, folder?, strip?}` opens it → `{token, part_bytes, received: 0}` (507 when it would leave the server's disk with less than 1 GB free; 403 for a guest's `gamma` source); multipart `part` + `offset` appends one → `{received}` (409 carrying `received` at another offset); `finish` answers like `POST /import/review`, the token its `review_id` (400 while bytes are missing). `DELETE /import/review/{id}` drops an unfinished one, and the import job refuses one (409) until it is finished |
| DELETE | `/import/review/{id}` | Discard the staged upload/review; 409 while its import job reads it |
| POST | `/import/review/{id}/keepalive` | The review's dialog still shows it: its two hours start again, also once past them while no upload's sweep has removed it → `{ok}`; 404 once swept or when not the caller's, 409 while it is still arriving in parts. Starting its import counts the same way |
| POST | `/import/markdown-zip/preview`, `/import/markdown-file/preview`, `/import/notability/preview`, `/import/goodnotes/preview`, `/import/gamma/preview` | Format-specific read-only review adapters. Markdown single-file review shares the ZIP engine. Gamma requires a non-guest workspace |
| POST | `/import/markdown-file`, `/import/gamma` | Reviewed single-note or additive Gamma import; multipart `file`, `selected` JSON source IDs (required for Gamma). ZIP Markdown/Zotero imports also accept optional `selected` (omitted = all, empty = none) |
| POST | `/import/notability` | Notability notes ([import_export.md](import_export.md#notability-notes)): multipart `file` (one `.note` or `.ntb`, or a `.zip` of them), optional `folder` (the destination folder's id) and `selected` (JSON bundle paths; omitted = all). One page per note: a note on one PDF keeps it, a mixed note gets a composed PDF, a paper note becomes a notebook; handwriting as gamma-ink v2 groups, text boxes, math, pictures and recordings come along, the typed text as notes. Skips a note already imported (`properties.notability_import`, the digest of its Session.plist or noteBundle). 400 for a Supernote file or a zip without notes; 413 for a central directory past 16 MiB or a zip bomb → `{ok, pages_created, pages_skipped, pdfs_stored, strokes_imported, text_boxes_imported, pictures_imported, recordings_imported, pages, skipped, warnings, entries, folder}`; workspace writers. The web app imports through `/import/review` and the `import` job |
| POST | `/import/goodnotes` | GoodNotes notebooks ([import_export.md](import_export.md#goodnotes-notebooks)): multipart `file` (one `.goodnotes`, or a `.zip` of them: a folder's export, a `.goodnotes.zip` backup), optional `folder` (the destination folder's id) and `selected` (JSON bundle paths; omitted = all). One page per notebook: one on GoodNotes' papers becomes a notebook of sheets, one on a single PDF keeps it, any other a composed PDF; handwriting as gamma-ink v2, text boxes, pictures, recordings. The report is the Notability import's; a notebook already imported (`properties.goodnotes_import`) is skipped. 400 for an upload with no notebook |
| POST | `/import/zotero` | Zotero library import: zip of a "Zotero RDF" export (multipart `file`; `strip`, optional `folder`, the destination folder's id). Items and standalone attachments→pages+metadata (an item's first PDF the page's document, its other files and web links→file chips and links under the page's "Attachments" block), collections→folders (nested, below the destination, made where missing), tags→labels (by name), notes→block trees, standalone notes→note pages; embedded annotations via the same importer. Idempotent by file hash / `zotero_key` (a key local to one Zotero database, `#item_<n>`, only into a page of the same work), a Gamma export by the page it came from (`#gamma_page_<id>`); returns page destinations, `attachments_imported` and warnings |
| GET | `/pages/{id}/export` | page export (`?mode=readable|obsidian|notes-pdf|annotated-pdf|notability|logseq-graph|zotero-rdf|bibtex|gamma` + `highlights=&notes=&pdf=`); `obsidian` = a vault zip (`<folder>/<Title>.md`, wikilinks, `attachments/`, `.obsidian/app.json`); `notes-pdf` = the notes typeset as their own PDF (works without a paper); `annotated-pdf` = what `/export-pdf` answers; `notability` = the page as a Notability `.note` ([import_export.md](import_export.md#notability-export)), which `/import/notability` brings back as it was; `bibtex` = the page's citation entry as a `.bib`; `gamma` = scoped backup for `/import-data?mode=merge` (through a share token without the folders and labels the share does not show, and without archived chats). The share view's download; the web app exports through the `export` job |
| GET | `/pages/{id}/export-pdf` | the page's own PDF with annotations written back (`?highlights=&notes=&comments=&notes_at=beside|page`: the notes as cards beside the pages or on them, the open comment threads as cards beside them, [pdf_typesetting.md](pdf_typesetting.md#notes-and-comments-as-cards)); for a page with sheets of paper and no PDF, a PDF of the sheets with the paper, the pictures, the text boxes and the handwriting drawn ([notebooks.md](notebooks.md)) |
| GET | `/bibliography` | the bibliography a `mode=bibtex` export would write, as data for the Export dialog's review (`?page_id=` or `?folder=`, a folder id): `entries` (per citable page: `page_id`, `title`, the citation `key`, `pinned`, the entry `text`), `skipped` (pages left out, with the reason) and `text` (the file). Same builder as the download; an empty bibliography is data, not a refusal |
| GET | `/folders/{folder_id}/export` | whole-folder export, same modes/flags (`mode=` …): the pages filed in the folder or below it; 404 for an id that is no folder or a folder with no pages, 403 through a share that does not reach it (`scope.allows_folder`; a page share never); the download is named after the folder's path; subfolders become Zotero collections or vault directories, `notes-pdf` one PDF for the whole folder, `annotated-pdf` a zip of each paper's annotated PDF (`<subfolder>/<Title>.pdf`; pages without a PDF left out), `notability` a zip of each page's `.note` (`<subfolder>/<Title>.note`), `bibtex` one `.bib` of every paper's citation entry (pages without metadata left out), `gamma` the scoped backup another Gamma imports a shared folder by (through a token the folder and its subfolders, that folder at the top level with nothing above or beside it, the labels its pages carry, and only the pages' active chats). With a folder share token this is the fixed bibliography URL a LaTeX editor refreshes from ([import_export.md](import_export.md#keeping-a-bib-up-to-date)) |

### Background jobs (`routers/jobs.py`, `gamma/jobs.py`) — see [tasks.md](tasks.md)
Session accounts only (401 without one; integration tokens 403). One job starts for a token all the same: a token's `PUT /pages/{id}/attachment` starts its account's `relocate-highlights` run, which that account's sessions see. A job is its starter's; a workspace's own jobs (the indexer, `owner: ""`) are its members', in the workspace the request works in. A job: `{id, kind, owner, workspace, title, params, state: queued|running|done|failed|cancelled, progress: {done, total, unit, phase, item}, error, created_at, started_at, finished_at, artifact: {name, type, size} | null, downloaded, stoppable, stopping}`, `result` only from `GET /jobs/{id}`.

| Method | Path | Purpose |
|---|---|---|
| GET | `/jobs` | `{jobs}`: the account's newest 100, the request workspace's own, and two read-only kinds from subsystems that keep their own state — the account's scheduled backup tasks while one runs (`kind: "scheduled-backup"`) and its blocked paper fetches while one waits for the browser (`kind: "paper-handoff"`, [ai_tools.md](ai_tools.md#walls-and-the-browser-handoff)) |
| GET | `/jobs/{id}` | one job with its `result` (404 for a job the account may not see) |
| POST | `/jobs/{id}/cancel` | stop it: a queued job ends at once, a running one at its next step; 409 once it can no longer stop (a restore swapping the databases); a workspace's job needs an editor |
| DELETE | `/jobs/{id}` | remove a finished job and its file (409 while it runs) |
| POST | `/jobs/clear` | remove every finished job the account may remove → `{removed}` |
| GET | `/jobs/{id}/download` | the file a finished job wrote (its starter only; 404 once it expired, 24 h after it ended) |
| POST | `/jobs/export` | `{page_id \| folder, mode, pdf, highlights, notes, comments, notes_at}` (`folder` a folder id; the last two an annotated PDF's, as `/export-pdf` takes them) — a page or folder export in any `/pages/{id}/export` mode (`routers/export.py`; any member); its file is the download, its result `{pages, skipped: [{page_id, title, reason}]}`, and for `notability` `warnings: [{page_id, title, reason}]` too, what each note could not keep |
| POST | `/jobs/import` | `{review_id, selected}` — the staged review's selected items imported (`routers/imports.py`; workspace writer); its result is the import report. The same review asked again answers its job (409 with another selection). Starting is activity: a review past its two hours comes back unless swept (404) |
| POST | `/jobs/metadata` | `{block_ids, force?, prompt?, model?, context_char_limit?, cite_prompt?, cite_model?}` — `/metadata/fetch` for each of the workspace's pages named, one after the other (`routers/metadata.py`; workspace writer). `force` defaults to true; an upload's lookups send false, so a page that has its record keeps it. Result `{papers, fetched, failed}`. While the account's batch in this workspace is queued or running, the papers join it and the answer is that job. 400 when none of the ids is a page here |
| POST | `/jobs/transcribe` | `{block_id, model?, language?}` — the recording's audio transcribed segment by segment through the account's speech-to-text connection (`routers/recordings.py`, `gamma/recording.py`; workspace writer or edit share), its paragraphs written as the recording's children (`transcript: true`, `t0`) in place of the ones a run before wrote. `model` "" asks a `…-diarize` model the connection lists, else `whisper-1`; `language` as `/ai/transcribe`'s. Result `{paragraphs, model}`, progress in `segments`, phase `transcribing`. 404 for a block that is no recording, 400 for one without audio, 503 without a connection that transcribes, 409 with the running `job` while one runs for that recording |
| POST | `/jobs/relocate-highlights` | `{page_id}` — the page's highlights found again in its PDF by their text and moved there, the ones found roughly drawn dashed (`approx`), the ones not found marked `lost` (`routers/pages.py`, `gamma/highlight_relocate.py`, [highlight_relocation.md](highlight_relocation.md); workspace writer): what a replacement of the file starts by itself (`PUT /pages/{id}/attachment`, a token's included), here by hand. Each highlight is read from the file its position's `doc_id` names while that file is stored, else placed by its stored quote. Params `{page_id, title, doc_id, min_match, exact_chars}`, the last two the owner's thresholds when the run starts (the profile's `relocateMinMatch` as a fraction 0.5–1.0 and `relocateExactChars`, 0–60, else 0.8 and 12; [highlight_relocation.md](highlight_relocation.md#the-thresholds)); one run per page and file: a start while one runs for the page's current file answers that job (200), 409 only while it is still stopping. Result `{highlights, moved, kept, lost, approx, changed, min_match, exact_chars}` (`approx`: drawn dashed to check; `changed`: left as a person changed them meanwhile; then the thresholds it went by), progress in `pages` (phase `reading`) then `highlights` (`matching`); it can no longer stop once it writes. A duplicated page's `/api/uploads/<doc_id>.pdf` address is its stored file. 400 for a page without a PDF, with a PDF shown from a URL alone (`the PDF is not stored on the server`), or without highlights placed on it, 404 for no such page, 429 when the account's jobs are full |
| POST | `/jobs/workspace-export` | `{ws?, user?, all?, uploads}` — `/export` or `/export-all` as a job whose file is the zip |
| POST | `/jobs/snapshot` | `{workspaces, uploads, label}` — a snapshot of each (their owner); result `{snapshots, failed}`, failed only when every one did |
| POST | `/jobs/restore` | multipart `file`, `mode`, `ws?`, `user?` — `/import-data` as a job; the upload is kept until the job read it |
| POST, DELETE | `/jobs/restore/parts`, `/jobs/restore/parts/{token}`, `/jobs/restore/parts/{token}/finish` | the same zip in parts, for one past 32 MiB, speaking `/uploads/parts`' protocol: `{size, name, mode, ws?, user?}` opens it after the same permission check → `{token, part_bytes, received: 0}` (507 when it would leave the server's disk with less than 1 GB free); multipart `part` + `offset` appends one → `{received}` (409 carrying `received` at another offset); `finish` checks the target again and starts the job → the job, like `/jobs/restore` (400 while bytes are missing); `DELETE` drops an unfinished one |
| POST | `/jobs/restore-snapshot` | `{ws, name, mode}` — a stored snapshot restored (owner) or merged (editor) in place |
| POST | `/jobs/server-backup` | `{label, uploads}` — `/admin/backups` as a job (admins, `routers/admin.py`) |

Starting a job answers 429 when the account already has 20 queued or running, 507 for a file-writing job when the disk or the account's finished files (10 GB) are full.

### Prefs (`prefs.py`)
| Method | Path | Purpose |
|---|---|---|
| GET/PUT | `/prefs/{key}` | small synced JSON KV per account: `open-tabs`, `recent-views`, `read-pos` are stored per workspace (the request's), in its `pages.db` (`workspace_prefs`, which no export or backup of the workspace carries), `profile` / `ai-provider` account-wide in `users.db` (`db.USER_PREF_KEYS`); `profile` is the web app's account-scoped settings as one object keyed by preference name (400 unless an object; `db.get_profile` / `db.set_profile`, [settings.md](settings.md)); reading `profile` first syncs it with Gamma Cloud when the last sync is over a minute old, and its answer carries `cloud_choice` (a first sync waits for the person's choice); values over 64 KB get 413; refuses the reserved `ai-settings`, `translate-engines`, `search-services`, `profile-base` and `zotero-account` keys |
| GET | `/prefs?keys=a,b` | several keys in one answer, `{prefs: {key: what GET /prefs/{key} answers}}`, in the order asked, each once — how the web app's first load reads its preferences, as one request behind the library listing; 1 to 16 keys, and one the one-key read refuses refuses the whole read (400). The answer never waits on the network: for an account whose profile syncs through Gamma Cloud, `profile` is left out (its read may sync it first, a round trip to the account server that would hold every other key), and the caller reads it with `GET /prefs/profile` |
| PATCH | `/prefs/profile` | `{set: {name: value}}`: sets those entries of the profile and keeps every other one as stored (`db.patch_profile`) — how the web app saves, so a tab's stale copy of an entry it did not touch never undoes one synced from elsewhere; answers `{key, value, updated_at}` with the whole profile; 413 over 64 KB |
| GET | `/page-snaps` | all recents-card cover thumbnails `{snaps: {pageId: {img, at}}}`; `?after=<iso>` returns only newer ones (the focus-pull delta) |
| PUT | `/page-snaps/{page_id}` | store a cover (JPEG data URL body `{img, at}`; per-page newest-`at` wins, count-capped server-side); the covers are the workspace's, so workspace editors only (a viewer's stay in its browser) |
| DELETE | `/page-snaps/{page_id}` | drop a cover (the recents card's ×); workspace editors |

### Notices (`notices.py`, `gamma/notices.py`) — see [settings.md](settings.md) "Notices"
| Method | Path | Purpose |
|---|---|---|
| GET | `/notices` | `{notices: [{id, fingerprint, tone, pane, title}]}` the account has not looked at yet, strongest `tone` (`info` / `warn` / `error`) first; `pane` is the Settings pane that resolves it. The sources (`update`, `log-errors` and `db-damage` for admins; `backup-failed`, `mirror-conflicts`, `zotero-conflicts`, `publish-conflicts`, `cloud-sync`, `cloud-sync-choice`, `hosted`, `free-translate`, `storage` for everyone) are the table in [settings.md](settings.md) "Notices". Guests and integration tokens get `[]`. Sync: the release check may hit the network when its cache is stale |
| POST | `/notices/{id}/seen` | `{fingerprint}` — the account has seen this version of the notice (kept in the account-wide `notices-seen` pref); it stays quiet until the fingerprint changes. 403 for guests and tokens, 400 for a malformed id or fingerprint |

### Mentions and reminders (`routers/mentions.py`, `gamma/mentions.py`) — see [mentions.md](mentions.md)
| Method | Path | Purpose |
|---|---|---|
| GET | `/people` | `{people: [{username, role}]}`: the explicit members of the request's workspace, owners first, whom a note's @ menu offers (any member; 403 for a share link's visitor, who is no member) |
| GET | `/reminders` | `{reminders: [{key, workspace_id, page_id, page_title, block_id, date, time, text}], done: [key]}`: the account's reminders (`@YYYY-MM-DD HH:MM (remind @username)` in note text) in every workspace it can open, soonest first, found through each workspace's notes index (`time` "" when the reminder has none; a page in Recently deleted reminds no one), and the keys of those it has dismissed (`users.db` `reminders_done`; the listing drops the marks of reminders that are gone, [mentions.md](mentions.md) "Reminders"). Every matching block is read, however many; a workspace that cannot be read is left out. Session only (tokens get 403). Sync: one notes-index query per workspace, encoded off the event loop (`OrjsonResponse`) |
| POST | `/reminders/done` | `{keys: [key], done: true}` (at most 100 keys): dismiss reminders, or with `done: false` bring them back; answers `{done: [key]}`, those of the keys now done. No cap: a mark stays while its reminder exists |

### Integrations and MCP (`routers/integrations.py`, `mcp_oauth.py`, `mcp_server.py`) — see [mcp.md](mcp.md)
| Method | Path | Purpose |
|---|---|---|
| GET | `/integrations/tokens` | the current workspace's assistant connections (manual tokens and OAuth grants: id, name, dates), the MCP URL and whether browser sign-in is available; session only, never a guest |
| POST | `/integrations/tokens` | mint a manual token `{name, scope?: read \| write, expires_in_days?}` (shown once); at most 20 unexpired per account; `write` (a mirror's push credential, [mirror.md](mirror.md)) is refused to a viewer |
| DELETE | `/integrations/tokens/{id}` | revoke a connection |
| GET | `/integrations/oauth/request?request_id=` | the pending consent (client name, the account's workspaces) for the consent screen |
| POST | `/integrations/oauth/consent` | approve or deny a pending sign-in for one workspace |
| GET | `/.well-known/oauth-authorization-server`, `/.well-known/oauth-protected-resource` (and `…/oauth-protected-resource/mcp`) | OAuth discovery for MCP clients (no `/api` prefix) |
| POST | `/oauth/register`; GET `/oauth/authorize`; POST `/oauth/token` | dynamic client registration, the authorization redirect, the PKCE code exchange (no `/api` prefix) |
| POST | `/mcp` | the Streamable HTTP MCP endpoint (bearer token or OAuth access token; no `/api` prefix, browser origins refused) |

A manual token (`gamma_…`, not an OAuth one) is also accepted on every `/api/*` route as `Authorization: Bearer` (`auth.py`): the request runs as the account behind it, in the token's workspace only (`?ws=` / the header may only repeat it — 403 otherwise), writes only with the `write` scope (403 "this token is read-only"), never as an admin, and never on an ACCOUNT-level endpoint: everything behind `auth.require_user` / `require_user_id` alone — the AI provider entries (whose base URL decides where the stored key goes), models, usage and ChatGPT sign-in, translation keys, `/prefs` and `/prefs/*` (the profile and keybindings included), `/session`, `/workspaces/*` and backups, export and import, backup tasks, publisher sessions, the chat's fetch handoffs, cloud sign-in, integration tokens, mirrors and the Zotero connection and links — answers 403 "an integration token cannot do this — sign in" whatever the token's scope. A token reaches only what resolves a workspace (`require_ws` / `resolve_ws` / `require_ws_writer`: blocks, pages and their ops, uploads, the change feed, search, chats, cover snapshots, shares, `POST /ai/chat`); `/notices` answers it `[]`, and `/inbox*` 403. `auth.can_write` is the same rule for an endpoint that offers less instead of refusing: `POST /ai/chat` through a read token (or for a workspace viewer) arms the reading tools only.

### Mirrors (`routers/mirrors.py`, prefix `/api/mirrors`) — see [mirror.md](mirror.md)

| Method | Path | Purpose |
|---|---|---|
| GET | `/mirrors` | the caller's offline copies with their sync status |
| POST | `/mirrors` | `{remote_url, token, name?, mode?: two-way \| pull, workspace_id?, adopt?: theirs \| mine}` → the mirror: a new personal workspace that follows the remote workspace the token belongs to, or with `workspace_id` an existing personal workspace of the caller's whose pages adopt one side's version (validated against the remote's `/sync/whoami` first; a read token or a viewer's role gives `pull`); the first fill runs in the background |
| GET | `/mirrors/{ws}` | one mirror, with `conflicts_open` (unresolved merges) and `conflicts_newest` (the newest open one's id), `pending_local` (a local write no round has pushed yet; two-way copies only), `poll_s` / `on_change` (its cadence), `detached`, `interval_s` (0 = the loop is off), `page_filter` (null = every page travels, else the ids of the only pages that do — a publication); `status.progress` `{done, total, page, first, file?}` while a round runs |
| PATCH | `/mirrors/{ws}` | `{poll_s?, on_change?, mode?}` (`mode` on a detached copy: 400 — reattach first) |
| POST | `/mirrors/{ws}/detach` | detach, the link kept |
| POST | `/mirrors/{ws}/relink` | `{token?, remote_url?, adopt?}` — link again |
| POST | `/mirrors/{ws}/force` | `{direction: pull \| push}` — replace one side with the other; noted as `status.force` and applied by the next round |
| POST | `/mirrors/{ws}/sync[?wait=1]` | a sync round now (`wait=1` answers with the round's status) |
| DELETE | `/mirrors/{ws}` | stop mirroring; the workspace stays |
| GET | `/mirrors/{ws}/log?limit=` | what the last rounds did, page by page, newest first: `{changes: [{id, at, page_id, title, action, stats, changes, exists}]}` — `stats` the git-style block counts `{add, del, mod}` (`{}` on rows from before they were kept), `changes` what each edit did block by block (`[{k: add \| del \| mod \| props \| move, id, text, old?}]`) |
| GET | `/mirrors/{ws}/conflicts[?resolved=1][&page=]` | the merges the engine decided on its own (kinds `merged`, `diverged`, `dropped`, `kept_local_edit`, `restored_remote_edit`, `page_restored`, `page_restored_from_remote`, and for a drawing `file` and `ink_merged`) |
| POST | `/mirrors/{ws}/conflicts/{id}` | `{choice: keep \| mine \| theirs}` — on a `merged` or `diverged` row, `mine` / `theirs` writes that text into the block's page as it is now, then the conflict is marked resolved; 409 (the conflict stays open) when the block refuses the write. `keep`, and any choice on another kind, only marks it resolved |

Session only, the mirror's owner — the account recorded as its owner that
also owns the copy's workspace (anyone else gets 404 and an empty list) —
never a guest.

### Zotero (`routers/zotero.py`, `routers/zotero_sync.py`) — see [zotero_sync.md](zotero_sync.md)

The account's Zotero connection, which every Zotero link of the account
syncs with ([zotero_sync.md](zotero_sync.md#accounts-and-access)):

| Method | Path | Purpose |
|---|---|---|
| GET | `/zotero/account` | the connection, never the key: `{connected, username, user_id, hint, via (oauth \| key), connected_at, access, can_edit, oauth: {available, mode}}`; `oauth.mode` is `server` (this server's own Zotero app, `GAMMA_ZOTERO_CLIENT_KEY` / `_SECRET`), `cloud` (Gamma Cloud's broker, [cloud_accounts.md](cloud_accounts.md) "Connecting Zotero") or null (a key must be pasted: no app, no broker, or no address to come back to). A guest gets `{connected: false, can_edit: false, oauth: {available: false, mode: null}}` |
| PUT | `/zotero/account` | `{api_key}`: connect a pasted key, checked with Zotero first → the account answer. 400 when Zotero refuses the key or it cannot read the person's library, 502 when Zotero cannot be asked; guests 403 |
| DELETE | `/zotero/account` | forget the connection (a key Gamma had Zotero make is revoked there too) → `{ok: true}`. The links stay and pause until a connection comes back |
| GET | `/zotero/libraries` | `{libraries: [{id ("users/<id>" \| "groups/<id>"), name, kind (user \| group), user_id, read, write, files, notes}]}`: My Library and the groups the key reaches, with what it may do in each. 409 when nothing is connected or Zotero no longer takes the key, 502 when Zotero cannot be asked |
| POST | `/zotero/oauth/start` | begin signing in at zotero.org → `{state, url}` (the page opens `url`); unless the desktop app's own server is asked from this computer on a loopback address, it also sets a cookie that binds the round trip to this browser. 400 where signing in is not offered, 502 when zotero.org or the broker cannot be reached; 20 per account in 10 minutes |
| POST | `/zotero/oauth/status` | `{state}` → `{state: "pending"}`, `{state: "connected", account}` or `{state: "error", error}`; 404 for a round trip this account did not start, or one that expired unfinished |
| GET | `/zotero/oauth/callback?state=&oauth_token=&oauth_verifier=` (or `&code=`, or `&error=`) | the round trip's end, in the browser (no session needed): a small page that says how it went, or one that names the Gamma account and server and asks before connecting a browser it cannot tell is the starter's; 30 per IP in 10 minutes |
| POST | `/zotero/oauth/confirm` | that page's Connect (a form with its one-time `token`) |

Links (prefix `/zotero/links`):

| Method | Path | Purpose |
|---|---|---|
| GET | `/zotero/links` | `{links: [info…]}`, the caller's. `info` is `{kind: "zotero", workspace_id, name, library, library_name, remote_name, remote_url, mode, download, upload, auto_tags, send_unrecorded, poll_s, on_change, status, created_at, conflicts_open, conflicts_newest, pending_local, detached, interval_s}` |
| POST | `/zotero/links` | `{library, workspace_id?, name?, mode?: two-way \| pull, download?: all \| open, upload?, auto_tags?, send_unrecorded?, adopt?: theirs \| mine}` → 201 `info`; the first round runs in the background. Without `workspace_id` a new personal workspace is made (named `name`, else "Zotero" or the group's name); with it, an existing personal workspace of the caller's whose pages pair with the library's items, each pair taking `adopt`'s side where they differ. 400 for a library the key does not read, a second link of the same library (`you already link that Zotero library to a workspace`, from the check or from users.db's unique index), a workspace not the caller's, one that is a clone or publishes pages, or one linked already (a publication whose last page was unpublished is no bar: it is forgotten, its log and conflicts with it, once the link is made); 409 when no Zotero account is connected or Zotero refuses the key, 502 when Zotero cannot be asked |
| GET | `/zotero/links/preview?library=&workspace_id=` | what linking would move, for the link dialog: `{items, notes, attachments, pdfs, pdf_bytes, linked_files, partial}` (`partial`: the counts read a bounded part of a large library, so they are lower bounds) and, with `workspace_id`, `{matches, to_send, to_send_bytes, collections_to_add, tags_to_add, notes_to_send, highlights_to_send, highlights_new}`, each null past what the counts read. The same count again within two minutes, neither side changed, is answered from the last. 429 while the account's last count still runs, or past 30 counts in 10 minutes; 503 when Zotero kept it waiting 30 s; the other refusals (400, 409, 502) as `POST`'s |
| GET | `/zotero/links/{ws}` | `info` |
| PATCH | `/zotero/links/{ws}` | `{poll_s?, on_change?, mode?, download?, upload?, auto_tags?, send_unrecorded?}` → `info` (`poll_s` 5 is Live, 0 only by hand; `mode` on a detached link: 400, reattach first) |
| POST | `/zotero/links/{ws}/sync[?wait=1]` | a round now, after lifting a wait on Zotero's full storage → `{status, conflicts_open}` (with `wait=1` the round's status) |
| POST | `/zotero/links/{ws}/fetch` | `{page_id}`: the page's PDF in Zotero first, then the page (someone opened it) → `{queued: true}`. Any member of the workspace (it only reorders the link's work); 404 for no link or no such page |
| POST | `/zotero/links/{ws}/detach` | detach, the link and its rows kept → `info` |
| POST | `/zotero/links/{ws}/relink` | no body: link again, in the direction it had, a round in the background → `info` (an unfinished first link keeps its side); 409 while no Zotero account is connected |
| POST | `/zotero/links/{ws}/force` | `{direction: pull \| push}`: take one side's version of everything both sides have, its trash included, in the next round → `info`; what it replaces is written as resolved conflicts (`…/conflicts?resolved=1`) and counted in the log ([zotero_sync.md](zotero_sync.md#force)); 400 on a detached link, and for `push` on a receive-only one |
| DELETE | `/zotero/links/{ws}` | forget the link → `{ok: true}`: the workspace stays as an ordinary one, Zotero's library is not touched |
| GET | `/zotero/links/{ws}/log?limit=` | `{changes: [...]}`, as the mirror's log |
| GET | `/zotero/links/{ws}/conflicts[?resolved=1][&page=]` | `{conflicts: [{id, page_id, page_title, block_id, kind, mine, theirs, result, at, resolved, base}]}`, the kinds of `zotero_sync.CONFLICT_KINDS` |
| POST | `/zotero/links/{ws}/conflicts/{id}` | `{choice: keep \| mine \| theirs}` → `{id, resolved: true}`: the chosen side written first, then the row resolved ([zotero_sync.md](zotero_sync.md#conflicts-and-the-log)). 404 for no such conflict; 409, the row left open, for a resolved one, a choice its kind does not offer, a write the block refuses, or "theirs" once Zotero moved on from the value the row holds (`Zotero changed this again since: its newer value is here now`) |

Session only, never a guest. A link is its owner's: the account recorded
as its owner that still owns the workspace (anyone else gets 404 and an
empty list), but `fetch`, which any member may ask for.

### Folders on disk (`routers/folder_links.py`, prefix `/api/folder-links`; the desktop app's own server only, 404 elsewhere) — see [folder_sync.md](folder_sync.md) "Folders kept by the desktop app"

| Method | Path | Purpose |
|---|---|---|
| GET | `/folder-links` | every link the account may see, whatever workspace the request names: those of this server's workspaces it has a role in (`remote_url` empty) and its own with a remote source (`remote_url` the other server, `workspace_id` the workspace there, `token_id` the token's id — the token itself is never answered): `{links: [{id, workspace_id, folder_id, path, notes, created_by, created_at, cursor, status, dest, remote_url, token_id}]}` — `status` the last round's `{running, last_sync, last_error, counts: {added, updated, renamed, removed, kept, unchanged}, kept: [{path, why}], folder_path}`, with `paused_at` while the link is paused. The desktop app's sync panel lists them all |
| POST | `/folder-links` | `{folder (a folder block id, or root), path (the directory's full path on this computer), notes?}` → 201 the link; the directory gets its state file and the first round runs in the background. With `remote_url` and `token` (a token of that server; `token_id?` for revoking it later, `workspace?` the token's workspace, checked when given) the folder is one of another Gamma server's, read from there; the request's workspace plays no part and the link is the account's. 400 for a folder that does not exist, a path that is not a full one or is inside the data directory or another link's (ignoring case), a directory holding another folder's files, an eleventh link of the workspace, or a server that refuses the token |
| GET | `/folder-links/{id}` | one link |
| PATCH | `/folder-links/{id}` | `{notes?, paused?}` — notes files on or off (turned off, they leave the directory at the next round); paused, the tick leaves the link alone and only a sync asked for runs it (`status.paused_at`); resumed, a round runs in the background |
| POST | `/folder-links/{id}/sync[?wait=1&full=1&force=1]` | a round now, in the background or inline with `wait` (the answer then carries the round's status); `full` writes every file again, `force` replaces files changed on disk |
| DELETE | `/folder-links/{id}[?remove_files=1]` | forget the link; the directory stays, or with `remove_files` what the rounds wrote is taken back (changed files and files the rounds never wrote stay) |

A signed-in account's session only: an integration token is refused as for
backups, and so is the guest. Listing takes any member; the rest the editor
role. A link of another workspace is 404.

### Publishing (`routers/publish.py`, `gamma/publish.py`) — see [mirror.md](mirror.md) "Publishing"

| Method | Path | Purpose |
|---|---|---|
| POST | `/pages/{id}/publish` | publish the page to the share host Gamma Cloud names: body `{audience?: anyone \| users \| list, role?: view \| edit}` (optional; the share there, default anyone / view, applied to a new or an existing link) → `{url: "<share host>/?share=<token>", public_url, share: {token, page_id, audience, role, users, created_by}, mirror: {ws, status, page_filter, mode, detached, conflicts_open, pending_local}}` — `public_url` the page's pretty address (`https://<username>-pages.gammapdf.com/<slug>-<id>`) when the share host reports a `page_host`, else the same as `url`. Adds the page to the workspace's filtered mirror of the share host (made on the first publication through `/auth/cloud/exchange` there), runs one round and makes the share. Workspace editors, session only, never a guest. 409 with a message when it cannot: no linked Gamma Cloud identity with a token ("Sign in with Gamma Cloud to publish."), no share host named, a workspace that is a copy of another server, one a Zotero link keeps ("This workspace is kept in step with a Zotero library, so it cannot publish pages.", detached links included), a mirror owned by someone else, detached or receive-only, or this server is itself a share host, or the plan's cap there (the share host's words — "Free plan: up to 5 published pages. Unpublish one, or upgrade your Gamma Cloud plan." — plus `limit: {used, max, plan}`); 400 for a block that is not a page; 502 when the share host refused or the page did not reach it; 503 when the account server cannot be read |
| DELETE | `/pages/{id}/publish` | unpublish: the share there stops, the copy there is deleted, the page leaves the filter; the page here is untouched → `{published: false, mirror}`. 409 when the page is not published; 502 (nothing changed) when the share host cannot be reached |
| GET | `/pages/{id}/publish` | `{published, can_publish, reason?, url?, public_url?, share?, status?, mirror?, limit?, error?}` — `can_publish` / `reason` say whether publishing would be refused and why (the same messages as POST), `status` is the mirror's raw status (the pill's reading is the frontend's), `share` the live share settings there (`url` and `public_url` with them), `limit` `{used, max, plan}` read from the share host whenever the account holds a publishing token (`max` null = no cap), `error` when the share host could not be read. Any member |
| GET | `/publish/limit` | the share host's half: `{used, max, plan}` — the root pages of the request's workspace (a publishing mirror's token names it) and the cap its owner's plan puts on them (`max` null = none). 404 on a server that is not a share host. Nothing cached |
| GET | `/pages/resolve-public?host=&path=` | no auth: a page host's pretty address → `{share, page_id}`, the share token the share view opens with (audience and role its own). `host` must match `GAMMA_PAGE_HOST` (the username read out of it), `path` is `/<slug>-<id>` or `/<id>`; only the trailing id counts, a root page with a share in that account's default personal workspace. 404 otherwise (counted like an unknown share token); 429 past 120 per IP in 5 minutes |

### Publisher sessions (`routers/publisher_sessions.py`)

Personal accounts only; guest and share-token access is rejected. Encryption,
expiry and request scoping are in
[paper_metadata.md](paper_metadata.md#connected-publisher-sessions), the
Connector's side in [extension.md](extension.md).

| Method | Path | Purpose |
|---|---|---|
| GET | `/publisher-sessions` | the account's connected publisher hosts (metadata only, never cookie values) and the supported `publisher_roots` |
| POST | `/publisher-sessions` | save `{host, cookies, user_agent?}` for the signed-in account; requires HTTPS or localhost, JSON, and a body of at most 256 KiB |
| DELETE | `/publisher-sessions/{host}` | disconnect that account's host; returns `{ok: true}` |

### Admin (`admin.py`, prefix `/api/admin`)
| Method | Path | Purpose |
|---|---|---|
| GET/POST | `/admin/users` | list (with usage and `default_workspace`) / create accounts (+ personal workspace; 403 once a hosted plan's account cap is reached) |
| PUT/DELETE | `/admin/users/{name}` | password, admin flag, storage overrides / delete through `workspaces.delete_account` (+ the workspaces only they owned, listed as `deleted_workspaces`, each copied to `backups/deleted/` first — 507 and nothing deleted when a copy cannot be written; guest accounts too, without copies) |
| POST | `/admin/users/{name}/rename` | rename the account: its username, and its personal workspace's name when that was the old username. Everything else names the account by id, so sessions, share links, tokens, backup tasks and jobs keep working and no file moves; 409 when the name is taken |
| GET/POST | `/admin/invitations` | invitations by e-mail to this server, for someone with no account here (`gamma/email_invites.py`, [cloud_accounts.md](cloud_accounts.md) "Invitations by e-mail"): the ones still waiting / invite `{email}` — the account server mails a link to make a Gamma Cloud account or sign in with one, and the sign-in through it gets an account here whatever the sign-in policy. Every answer is `{invitations: [{id, email, role, invited_by, created_at, mailed_at, expires_at}], available, reason}` (`available` false, with the reason, where none can go out); the POST adds `invited: {pending: entry}`. 400 for an address that is none, one an account here is linked by already, 200 waiting, or when none can go out; 429 past 20 sends and resends an hour per admin; the account server's refusal as for a workspace's invitation |
| DELETE | `/admin/invitations/{invite_id}` | withdraw one; its link stops working. 404 for none |
| POST | `/admin/invitations/{invite_id}/resend` | mail it again: a new mail under the same link id, whose link replaces the one sent before, the sender as its inviter, 14 days from now. 404 for none; the send's 400, 429 and 503 as for an invitation |
| GET | `/admin/workspaces` | every workspace (kind, access, public role, quota, `personal` = its account or "", `default`, members, upload size), plus orphan directories — Settings → Workspaces |
| GET/POST | `/admin/backups` | list the whole-data-directory snapshots under `backups/` (each with its manifest: `auto` for the migration runner's — for an old manifest without the flag, what pruning reads from its `v<N>` label —, `integrity` per database copy, `damaged`) / take one now (`{label?, uploads?}` — databases, plus every upload with `uploads: true`; 507 when it cannot be written; the web app takes one as the `server-backup` job); per-workspace snapshots are `/workspaces/{id}/backups` |
| GET | `/admin/backups/{name}/download` | the snapshot as a zip |
| DELETE | `/admin/backups/{name}` | delete a snapshot (restoring is `manage.py backups --restore`, server stopped — [migrations.md](migrations.md)) |
| POST | `/admin/check-databases` | `PRAGMA quick_check` of `users.db` and every workspace's `pages.db` and `data.db`, changing nothing: `{ok, checked_at, files: [{file, workspace, name, result, ok}]}`; a failed file raises the admins' `db-damage` notice until a later check of it passes (`gamma/integrity.py`) |
| GET/PUT | `/admin/settings` | server-wide storage defaults, plus `public_url` / `public_url_source` (the admin-confirmed public server URL, [mcp.md](mcp.md)), `guest_ttl_hours` / `guest_ttl_source` (1–720 hours, `guest_ttl_hours_range`; `environment` when `GAMMA_GUEST_TTL_HOURS` decides) and `demo_mode` / `demo_mode_source` (`environment` when `GAMMA_DEMO` is on) — a PUT of either is 400 while the environment decides — and the guest sign-in switch `guest_logins` / `guest_logins_source` (on until an admin saves it off; a PUT is 400 where the server takes no guests either way) beside `guest_logins_available` (false there — a hosted container, a share host, `GAMMA_GUEST_MAX=0` — so the pane leaves the Guests rows out; [guests.md](guests.md)) — and `cloud` (the cloud sign-in settings, written as `cloud_issuer`, `cloud_client_id`, `cloud_client_secret`, `cloud_policy`, `cloud_share_host`, plus the read-only `needs_connect` and `policy_source` — [cloud_accounts.md](cloud_accounts.md)). The default quota carries `quota_mb_source` (`saved`, `default`, or `plan` when a hosted plan's cap decides) and `plan_caps` (`{quota_mb}`, {} off a hosted container); a PUT above a plan's cap, a per-account override above it included, is 400. There is no per-file cap: a `max_upload_mb` in a PUT is ignored. `hosted` is the Plan rows' block on a hosted container (`{limits, accounts, last_failure, issuer}`: the cached answer with `synced_at`, or null before the first sync), else null |
| POST | `/admin/hosted/sync` | a hosted container's Sync now (sync): one `/api/hosted/sync` call to the account server → `{ok, hosted, …the storage defaults}`; a failed call is `ok: false` with `hosted.last_failure` and the last answer unchanged; 400 off a hosted container |
| GET/PUT | `/admin/offsite` | the off-site copies (`gamma/offsite.py`, [debugging.md](debugging.md#off-site-copies-in-a-bucket)): `{settings: {enabled, bucket, endpoint, region, access_key, secret_set, prefix, interval_s, keep}, from_env, status}` — the secret key is never returned; `from_env` is true while `GAMMA_S3_BUCKET` sets every field; `status` is `{enabled, running, interval_s, keep, last_round_at, copied, uploads_copied, failed, error, next_round_at}` (`offsite.status`, from memory and the state file: the last round's UTC time, databases and files copied, failures and the first one's reason, all null before the first round). PUT takes the same fields, any left out unchanged; `secret_key` only to change it (absent or empty keeps the saved one; clearing `access_key` drops it). 400 with the reason (interval 60 s to 30 days, keep 1 to 1000, a bucket name's shape, an http(s) endpoint, both keys or neither, a bucket before `enabled`), 409 while the environment decides; answers the GET's shape |
| POST | `/admin/offsite/test` | one listing of the bucket, tried once (sync): optional body of unsaved settings, the PUT's fields laid over the saved ones (`secret_key` absent = the saved one; ignored while `from_env`) → `{ok: true, message: "<bucket>: <n> objects under <prefix>"}` or `{ok: false, message}` (the reason: no such bucket, credentials refused, endpoint out of reach, a field that does not validate), 200 either way |
| POST | `/admin/offsite/run` | Copy now: a round in a thread of its own → `{started: true}`, or `{started: false, message}` with the copies off or a round running ("a round is running"); not a job — the GET's `status.running` follows it |
| GET | `/admin/logs?after=<seq>` | scrubbed in-memory server log |
| GET/PUT | `/admin/ai-providers` | the server's shared AI entries, masked like `/ai/settings` (key hint, never the key; the same `protocols` and `services`), each with its `access` (`{who: "everyone" | "selected", users: [usernames], guests}`), and `allowance` (`{accounts, guests}`: tokens per account per rolling 24 h, 0 = unlimited); PUT `{allowance?: {accounts?, guests?}}` (whole numbers 0..10^9, else 400). Chat, translate, metadata fetch/cite and transcribe answer 429 with a human `detail` once an account's allowance is used up; streams end with `{error: detail}` |
| POST/PUT/DELETE | `/admin/ai-providers[/{id}]` | add / edit / remove a shared entry (the `/ai/providers` fields and validation plus `access?` — `{who, users?: [usernames], guests?}`, an unknown or guest username a 400; left out, an add is every account's but guests', an edit keeps it — at most 20; a POST takes API-key protocols only; ids are `server:<id>`; the key is write-only and stored encrypted). Admin session only: an integration token is refused. Test, model listing and a sign-in's usage go through `/ai/providers/{id}/test`, `/ai/model-catalog` and `/ai/providers/{id}/usage` |
| POST | `/admin/ai-providers/chatgpt/start` · `/status` · `/complete` | a shared ChatGPT subscription: the `/ai/oauth/chatgpt/*` flow (same bodies) for the server's list — `complete` adds a shared sign-in entry, or with `provider_id` reconnects one, and returns the shared view; the tokens are stored encrypted; a state from one flow never redeems on the other. Admin session only |
| GET | `/admin/server-info?refresh=` | the Server dashboard (`gamma/version.py`): `version` / `commit` / `branch` / `label` (from `GAMMA_VERSION` / `GAMMA_COMMIT` / `GAMMA_BRANCH` — the Docker build and the desktop shell set them, a non-release Docker build as `<tag>-dev.<n>` with its branch; a checkout is a "development build"), `started_at`, `uptime_seconds`, `python`, `platform`, `schema_version`, `frozen`, `log_counts` `{info, warning, error}` since startup, `latest` (`{version, url, published_at}` from the GitHub Releases API, cached six hours, ten minutes after a failure, `refresh=1` refetches; `GAMMA_UPDATE_CHECK=off` disables) or `latest_error`, `latest_build` (a `-dev` build only: `{version, branch, ahead_by, url}`, its branch's newest build from GitHub's compare API, same cache), `update` (`{kind: release|build, version, url}` or null; a newer release wins), `update_available` (True/False, None without a version to compare), `updates` (`GAMMA_UPDATES`: `desktop`, `store` in a Microsoft Store install or `mas` in the Mac App Store build — who installs a newer release; "" off the desktop app), `image`, `releases_url`. Sync: it may hit the network |

Rails: a guest account takes storage limits and deletion but no password,
admin flag or new name; no self-delete; the last admin can't be demoted or
deleted.
