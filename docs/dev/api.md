# API reference

All endpoints are same-origin under `/api`. The frontend never talks anywhere
else; in dev, Vite proxies `/api` → `127.0.0.1:9001`.

## Auth model

- A `session` cookie identifies the user (middleware sets
  `request.state.user`). Account-level endpoints use `require_user`, which refuses an integration token ("Integrations" below).
- Every data endpoint works in a **workspace** ([workspaces.md](workspaces.md)):
  `?ws=` or the `X-Gamma-Workspace` header names it, nothing means the
  account's default personal workspace. `require_ws(request)` admits any member
  or signed-in non-guest account allowed by public workspace access,
  `require_ws(request, write=True)` editors and owners (a viewer gets 403);
  a caller without effective access gets 403. The returned workspace id is what the data helpers
  take; `request.state.user` stays the actor.
- Share tokens (`?share=<token>`) are the ONLY unauthenticated **read** path.
  `resolve_ws` returns the session's workspace, or the workspace of the share
  named by a valid `?share=` token. There is no `?user=` fallback: it would
  trust any username and leak whole accounts. A share names a PAGE of a
  workspace (its root block, so note pages share exactly like papers; the
  PDF is the page's `doc_id`/`source_url`) or a FOLDER (a folder-label
  path: the pages filed there or below it, read live, so pages filed later
  join and pages moved out leave). `auth.share_scope()` hands every
  share-enabled endpoint a `ShareScope`, and the endpoint asks it what is in
  reach (`allows_page`, `allows_block`, `allows_folder`, `lists_library`,
  `pages` and `block_ids` for a read that scans the workspace;
  `blocks_store.assert_block_in_scope()` for block reads) instead of
  branching on the share's kind. A token reaches only its own pages'
  subtrees and assets: their PDFs, the uploads their blocks reference (found
  by walking the shared pages' subtrees, never the workspace; a yes is
  remembered for `uploads.SHARE_READ_TTL_S`, 5 min), their
  own `source_url` through the proxy. Backlinks and other pages are refused
  (403). The root listing and folder export are refused for a page share;
  a folder share gets the pages in reach and exports its folder or a
  subfolder.
- Share reads are readable **cross-origin**: a GET carrying `?share=` or
  resolving `/share/{token}` answers `Access-Control-Allow-Origin: *`
  (`auth._apply_share_cors`), so another Gamma's frontend can pull a shared
  page into its own library browser-side (a share link pasted into the "+"
  box, the share view's "Add to my library" —
  [import_export.md](import_export.md)). `*` makes browsers drop credentials,
  so a cross-origin fetch is a stranger's: only `anyone` links open that way.
  Writes and every other endpoint keep the same-origin default.
- **Share permissions** (`shares.audience` / `role` / `allowed_users`,
  `auth.share_access`) are Notion-shaped and additive: members of the page's
  workspace keep their workspace role (editors/owners edit, viewers view);
  the sharer INVITES people (`users: [{name, role}]`, stored as
  `carol:edit,dave:view`) who get in with their own `view`/`edit` whatever
  general access says; everyone else goes through general access —
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
  into or out of its folder). Everything else stays session-only.
- **Link visitors.** An `anyone` + `edit` share makes the link itself the
  key: whoever opens it edits the page, without an account. Such a writer
  (no session, or a guest account — `auth.is_link_visitor`) is recorded
  as `link:<name>` (`auth.actor_of`): the display name the share view keeps
  per browser (`src/collaboration/linkName.js`, generated "Curious Otter"
  style, renamable from the topbar tag), sent percent-encoded as the
  `X-Gamma-Name` header on every API call (`utils.js` injects it) and as
  `?name=` on the page websocket; cleaned server-side (`auth.link_name`:
  control characters out, 40 chars, `Anonymous` when empty). A label, not
  an identity — usernames cannot contain `:`, so the log never confuses the
  two. Link visitors alone are rate limited per IP (`auth.link_ratelimit`:
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
  for links. Tokens are 96 random bits, so guessing one is hopeless; the
  throttle is about noise and visibility, not about protecting the space.
  The link-visitor throttles above log the same way.
  Keep that read/write + scope distinction when adding endpoints.
- Outbound fetches of user-supplied URLs (PDF proxy/resolver, AI PDF
  re-download) go through `gamma.net_guard.guarded_urlopen`, which blocks
  non-http(s) schemes (`file:`, `ftp:`, …) and hosts that resolve to
  loopback/private/link-local/metadata addresses (SSRF), re-checking on every
  redirect.
- Workspace ids and doc ids are validated (`db.safe_ws_id` / `db.safe_doc_id`,
  used by `ws_db_path` / `ws_uploads_dir` / `pdf_upload_path`) before they
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
  endpoint returns is encoded on the event loop, so `GET
  /blocks/{id}/subtree` answers a `JSONResponse` it serialized in its
  worker thread (a 5,000-block page stalled the loop ~175 ms otherwise).
- Every response carries baseline hardening headers (`X-Content-Type-Options`,
  `X-Frame-Options: SAMEORIGIN`, `Referrer-Policy`, `Content-Security-Policy:
  frame-ancestors 'self'`, and HSTS on HTTPS). SVG uploads are served
  `Content-Disposition: attachment` + `CSP: sandbox` so they can't run inline as
  stored XSS.
- `/api/admin/*` additionally requires the `is_admin` flag.

## Endpoints

### Session & account (`auth.py`)
| Method | Path | Purpose |
|---|---|---|
| POST | `/login`, `/login-guest`, `/logout` | session management (`/login` refuses an account with an empty password hash — one only its cloud identity signs in, and every guest). `/login-guest` mints a fresh guest account with its own workspace (`gamma/guests.py`, [guests.md](guests.md)) → `{ok, username}` (`guest-<8 chars>`); 403 on a share host, 503 once `GAMMA_GUEST_MAX` guests are live. A guest's `/logout` deletes the account |
| GET | `/server-config` | public: what the login page offers besides a password — `{cloud: {enabled, issuer}, password_login, registration, guest, guest_ttl_hours, guest_seeded, demo, page_host}` (`cloud.enabled` false while the server still has to be connected; `guest` false on a share host; `guest_ttl_hours` how long a guest account lives; `guest_seeded` whether a guest starts with the `GAMMA_GUEST_SEED` library; `demo` whether demo mode is on — [guests.md](guests.md); `page_host` the per-account page hostname pattern from `GAMMA_PAGE_HOST`, e.g. `{username}-pages.gammapdf.com`, "" when unset — how the app knows it was opened on a page host) ([cloud_accounts.md](cloud_accounts.md)) |
| POST | `/auth/cloud/exchange` | the share host's half of publishing ([mirror.md](mirror.md) "Publishing"): `Authorization: Bearer <Gamma Cloud access token>`, body `{server?}` (the calling server's name) → `{token, workspace_id, username, url}` — a write-scope integration token (365 days, named "Published pages from <server>", replacing the live one of that name) on the person's default personal workspace here, the account resolved under the sign-in policy like a first sign-in (provisioned under `provision`, pending invitations claimed), and this server's address. 403 unless the server accepts published pages (`cloud_share_host`), for an unconfirmed cloud e-mail, or when the policy refuses the account; 401 for a token the account server does not know; 503 when it cannot be asked; 429 past 20 per IP or 10 per cloud account in 10 minutes |
| GET | `/auth/cloud/start?next=&link=1` | Sign in with Gamma Cloud: stores the pending PKCE sign-in and redirects to the account server; `link=1` needs a session and attaches the cloud identity to that account. A server that still has to be connected (a public URL on the desktop client) goes back to `next` with `?cloud_error=` instead |
| GET | `/auth/cloud/connect/start?next=`, `/auth/cloud/connect/callback?code=&state=` | admins: connect this server to Gamma Cloud — `start` stores a pending PKCE connection and redirects to the account server's approval page (`gamma_server_connect_endpoint`) with this server's confirmed public URL; `callback` trades the code for a client id and secret (`gamma_server_connect_token_endpoint`), saves them as the cloud sign-in client and returns to `next` with `?cloud_connect=ok` or `?cloud_connect_error=` ([cloud_accounts.md](cloud_accounts.md) "Connecting a server") |
| GET | `/auth/cloud/callback?code=&state=` | the account server's return: verifies the ID token, resolves or creates the local account per the policy (`gamma/cloud_auth.py`), pulls the preference profile, registers this server on the person's server list (`gamma/cloud_sync.py`), mints a session and redirects to `next`; a refusal goes back to `/?cloud_error=` |
| GET | `/auth/cloud/sync-status` | the signed-in account's own preference profile sync (session only; guests and integration tokens get 403): `{profile: {state, at, error}, identity: {linked, username?}}`. `state` is `off` (cloud sign-in off, or no identity holding a token), `pending` (a push is scheduled, or failed and waits for the next check; `error` then says why), `synced` (the last pull or push agreed, at `at`), `error` (the last attempt failed) or `choose` (the first sync found two different copies and waits for the person's choice). Read from memory (`cloud_sync.profile_status`), no network; the Settings dialog polls it while open |
| POST | `/auth/cloud/sync` | sync the caller's profile with Gamma Cloud now (session only): `{action, defaults?}` with `action` `sync` (the automatic merge; answers `choose` while a first sync waits), `merge` (also settles the choice; `defaults` — the web app's default profile — is the base of a first merge), `fetch` (the cloud's copy replaces this server's; 409 when the cloud has none) or `push` (this server's replaces the cloud's). Answers `{outcome: pulled / pushed / merged / same / choose, profile}`; 400 without a linked identity holding a token, 502 when the account server could not be reached |
| GET / POST | `/auth/cloud/status`, `/auth/cloud/unlink` | the signed-in account's own cloud identity (username, plan, e-mail, linked at, `offline` — a refresh token is held — and `revoked_at`) plus `enabled`, the `issuer` (the account server's address, which the Account pane's "Open account" button opens) and `connected` (false while an admin still has to connect this server; the Link button waits); unlink is refused for an account without a password, and takes this server off the person's server list before revoking the grant |
| GET | `/session` | who am I, plus `workspaces: [{id, name, kind, role, access, public_role, personal, default, members, mirror_of, publishing}]` (memberships + every public workspace) and `default_workspace` (quota lives in `/quota`); `build` (`version`, `commit`, `label`, `frozen`) is what a problem report names the server by, sent to the login page too; a guest session adds `guest_expires_at` (UTC ISO: when the account and its workspace are deleted) |
| GET | `/accounts[?q=]` | the account directory for the invite / owner pickers: `{accounts: [{username, is_admin}]}`, non-guest accounts only (signed-in non-guest callers). On a share host only admins get the list; anyone else gets the one account named exactly `q`, or none |

### Workspaces (`workspaces.py`) — see [workspaces.md](workspaces.md)
| Method | Path | Purpose |
|---|---|---|
| POST | `/workspaces` | create a personal one (`{name}`; guests 403); admins may add `kind: "shared"`, `owner`, `access`, `public_role`, `quota_mb` |
| GET | `/workspaces/mine` | Settings → Workspaces: every workspace I can open with its `used_bytes` (and `mirror_of`, the remote workspace's name when it is an offline copy, `publishing` true while at least one of its pages is published to Gamma Cloud, where `mirror_of` stays empty — [mirror.md](mirror.md)), plus `account` (my limits and the usage of all my personal workspaces) |
| GET/PUT/DELETE | `/workspaces/{id}` | kind + members (pending invitations last, `pending: true` + `subject`) + quota + `personal_of` + `default` (any member; admins) / rename `{name}` (owner), `default: true` (a personal workspace's owner), kind, access + public role, workspace quota (admin) / delete (owner; not an account's last personal one): `{ok, warning, final_copy}` — a final copy goes to `backups/deleted/` first (`final_copy` its file name, "" for a guest's), and when it cannot be written nothing is deleted (507) |
| GET/POST | `/workspaces/{id}/backups` | the workspace's server-kept snapshots (any member; each with `scheduled`, `auto` — a restore's `pre-restore` one — `missing_uploads` and `damaged`) / take one now `{label?, uploads?}` (owner; at most `ws_backup.MAX_PER_WORKSPACE` manual ones; 400 while the disk has less than 1 GB free) |
| GET | `/workspaces/{id}/backups/{name}/download` | the snapshot as a zip — the same zip `/export` gives (any member) |
| POST | `/workspaces/{id}/backups/{name}/restore?mode=` | restore it in place: `replace` (owner) / `merge` (editor), the same rules and answer as `/import-data` |
| DELETE | `/workspaces/{id}/backups/{name}` | delete a snapshot (owner) |
| GET | `/export` | backup zip of a workspace (everything or `uploads=0`; the `gamma-backup-1` zip of `gamma/ws_backup.py`): the request's, `?ws=` (any member), or — admins — `?user=` for an account's default workspace (`routers/ws_backups.py`, like the next two; the web app starts the same work as the `workspace-export` job) |
| GET | `/export-all` | every personal workspace of the account in one zip, one `/export` zip per workspace inside (`uploads=0` for databases only; guests 403) |
| POST | `/import-data` | restore (`mode=replace`, owners) / merge (`mode=merge`, editors) a backup zip into a workspace (same targeting); never into a guest's workspace. A zip whose databases fail `PRAGMA quick_check` is refused (400) before anything changes; a replace first keeps the current state as a `pre-restore` snapshot (its name in `pre_restore`; 400 and nothing restored when it cannot be taken) and reports `pages_removed`; a merge reports `pages_added` / `pages_skipped` / `chats_added` / `from_trash` (pages that were only in Recently deleted, brought back as the backup has them). Every restored page gets a `reload` in its op log above any seq a client saw ([workspaces.md](workspaces.md) "Export and backups"). The web app restores through the `restore` job |
| GET/POST | `/backup-tasks` | the account's scheduled backup tasks (`routers/backup_tasks.py`, `gamma/backup_schedule.py`; signed-in non-guest) / create one `{name, enabled, scope: selected\|all_owned, workspaces[], cron, uploads, retention_mode: days\|count, retention_value}`; `cron` is five UTC fields firing at most hourly (one minute value; else 400), `retention_value` 1–90 (else 422), targets must be workspaces the owner owns (at most 5 tasks) |
| PUT/DELETE | `/backup-tasks/{id}` | replace the task (same body; 409 while it runs) / delete it, keeping its snapshots |
| POST | `/backup-tasks/{id}/run` | queue a run now, paused or not (409 while it runs) |
| POST | `/backup-tasks/preview` | `{cron}` → `{runs: [next three ISO times], timezone: "UTC"}`; 400 for a schedule that fires more than hourly |
| PUT/DELETE | `/workspaces/{id}/members/{user}` | shared workspaces: invite or set a role `{role}`, incl. owner (owner) / remove (owner) or leave (yourself) |
| GET/POST | `/workspaces/{id}/invites` | invitations by Gamma Cloud username still waiting for the person's first sign-in, `{invites: [{subject, username, role, invited_by, created_at}]}` (any member) / invite `{username, role}` — role `editor` (default) or `viewer`; the account server resolves the username to a subject; a person already linked here becomes a member at once (`invited: {member}`), anyone else gets a pending membership (`invited: {pending}`) their first cloud sign-in claims; returns the workspace like `GET /workspaces/{id}` (owner; admins). 400 on a personal workspace, when cloud sign-in is off, for an unknown username or an existing member; 429 past 30 lookups per account per 10 minutes; 503 when the account server cannot be asked ([workspaces.md](workspaces.md) "Pending invitations") |
| DELETE | `/workspaces/{id}/invites/{subject}` | withdraw a pending invitation (owner; admins); 404 when there is none |
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
| GET/POST | `/blocks/by-doc/{doc_id}` | lookup / create the page BY ATTACHMENT — the page whose PDF is `doc_id` (POST creates it: `{default_title, source_url?, original_filename?, folder?}`, `folder` files a NEW page only); the PDF-ingest + extension-dedup path, and what "Open as document" on a PDF file chip calls (the file is already stored under that hash). The lookup and the insert run under the workspace's write lock (`blocks_store.write_lock`), as in every get-or-create by attachment (clip, the "Web clips" page of `clip/note`, attach, `pages/from-file`, the Logseq import), so racing requests find one page; if older data holds two, the oldest answers. Text-only pages come from `POST /pages` |
| GET | `/blocks/{id}/children`, `/{id}/subtree`, `/{id}/backlinks` | tree reads; the root listing (`/blocks/root/children`) — through a folder share token, only the pages the share reaches (the share view's home library); 403 through a page share — additionally gives every page a `preview` — the first ~240 chars of its first non-highlight child blocks joined with ` · ` (one `idx_ub_parent` seek per listed page, so the cost follows the pages, not the blocks; `""` when empty) |
| POST/PUT/DELETE | `/blocks`, `/blocks/{id}` | CRUD — inside a page these are thin wrappers over the op path (`gamma/ops.py`): logged, fanned out to the page's room; `PUT` takes `content` and/or a properties PATCH (a null value deletes the key), and with `content` an optional `base` — the text the content was edited from: a block changed meanwhile gets the edit merged in (`gamma/textmerge.py`) instead of replaced, and the answer (`{ok, updated_at, seq, content}`) carries the text stored (an embed card editing another page's block sends it). With `properties` an optional `base_properties`, the values the patch was computed from: an ink group's `ink_url` whose base is not the stored one is merged into the stored drawing by stroke, a text box's `text_box` into the stored box key by key ([text_boxes.md](text_boxes.md) "Merge"), and the answer's `properties` is the patch applied (it names the merged file or box; [handwriting.md](handwriting.md) "Two writers, one group"). A new page (`parent_id: "root"`) is a plain insert (`blocks_store.create_page`): last in the library without `before`/`after`, and a key another page holds is re-keyed like an op's (`blocks_store.free_position`); deleting a page moves it to Recently deleted (`ops.trash_page`, below) → `{ok, id, trashed: {id, title, folder, deleted_at, deleted_by}}` — on a share host it is `ops.delete_page` at once: subtree + its op log gone, a `deleted_pages` tombstone left. A block of a page in Recently deleted reads as not found everywhere; for a member the 404 of `GET /blocks/{id}`, `/{id}/children` and `/{id}/subtree` carries `trashed` (the page's entry) |
| PUT | `/blocks/{id}/children` | replace the whole subtree (delete + reinsert, one transaction) — bulk paths only (duplicating a page, tests); the page's room gets a `reload`, the upload names the old subtree held and the new one lacks go to the orphan check ([user_db.md](user_db.md) "Stored files"). Node ids must have the block-id shape (a node without one gets a fresh id) and text is capped like an op's: 400 / 413; an id another block already has is a 409 and leaves the old children in place. 400 for `root`: the library's pages are created and deleted one by one. The editor itself sends ops |
| POST | `/blocks/{id}/reorder` | move within the page (an op) or, with `parent_id` on another page, across pages (the source room sees a `delete`, the target reloads); either way a key a sibling holds is re-keyed and the answer's `position` is the stored one |
| GET | `/block-search` | fuzzy note/page/highlight search (`case`, `whole` word), the newest `limit` matches; empty `q` returns recently edited blocks (feeds the `[[ref]]` popup's initial suggestions); `ids=a,b` looks blocks up (ref chips, Gamma-link titles). Through a share token every mode sees only the blocks the share reaches — others are dropped from the answer, never refused. The query is always text: `regex=1` is refused (400) — a catastrophic pattern would hold the GIL, i.e. the whole server. SQLite skips the blocks without the query's literal runs (`textnorm.literal_runs`), the fuzzy pattern runs over the rest; past 2 s the answer is what was found so far plus `"partial": true`, which the Ctrl+F panel's notes group (and its compact find bar) and the `[[` picker's footer show as a "stopped early" line. Sync `def` (threadpool) |

Route order matters: the static-prefix routes (`by-doc`, `children`,
`subtree`) must stay registered before `/blocks/{block_id}`.
`GET /blocks/{id}/subtree` on a page also returns `seq`, the op-log position
the tree reflects (the live session catches up from it).

### Collaboration (`collab.py`) — see [collab.md](collab.md)
| Method | Path | Purpose |
|---|---|---|
| POST | `/pages/{id}/ops` | apply a batch of block ops `{client, batch?, ops: [set / insert / move / delete], cursor?: {block, anchor, head}}` in one transaction → `{seq, at, ops}`, the ops as applied (re-keyed positions carry their final value). `batch`: the client's id for the batch, the same on every retry; a batch this process already applied for that client is answered again, not re-applied. An `insert` of a block the page already has leaves it as it is and echoes it. A `set` may carry `base`, the text its `content` was edited from: a block changed meanwhile gets the edit as a patch onto its current text (a three-way merge, `gamma/textmerge.py`), and the echoed op carries the merged text. `cursor`: the writer's caret in the text after the batch, fanned out with it and stored as the writer's presence. Lone UTF-16 surrogates in any string are stored as U+FFFD. A workspace editor or an edit share (confined to the shared page; the page root's properties stay the workspace's). A bad op fails the whole batch (400/403/404/413): a 404 for an unknown block or parent names it as `missing`, which the client re-sends as an insert when it holds that block; a refusal because someone changed the page meanwhile adds `conflict` (`missing` 404, `moved` 403, `cycle` 400) and `index`, the refused op's place in the batch |
| GET | `/pages/{id}/ops?since=` | the op log after a seq → `{seq, batches: [{seq, actor, client, at, ops}]}`; 410 (reload the tree) when the log is pruned past `since`, holds more after it than a catch-up carries (`ops.CATCHUP_MAX_BATCHES` / `CATCHUP_MAX_BYTES`), or cannot continue from it (a backup restore replaced the log) |
| GET | `/sync/whoami` | who the credential is on this server: `{user, workspace: {id, name}, role, scope}` — `scope` is an integration token's (`read` / `write`), `session` for a browser; what a mirror checks before it is created and at the start of every round |
| GET | `/sync/changes?since=&limit=` | the workspace change feed (`gamma/routers/sync.py`): pages whose root was stamped after the cursor (`pages: [{id, created_at, updated_at, seq}]`, `seq` the page's latest op) and pages deleted after it (`deleted: [{id, deleted_at, actor}]`, from `deleted_pages`), one time-ordered stream of at most `limit` (≤ 2000) entries → `{since, cursor, more, pages, deleted}`. `since=""` lists everything. The cursor is `<time>|<id>` while `more`, else the server time minus a 60 s grace, so the last minute is re-listed on every poll — the feed is a hint for a copy of the workspace (a mirror, a merge) to know which pages to look at; the page's own `seq` / `GET /pages/{id}/ops` is the truth, and the consumer must be idempotent. Any member (viewers too); no share tokens |
| WS | `/ws/page/{id}[?ws=&share=&client=]` | the page's live channel: `hello` / `join` / `leave` / `cursor` presence, every applied `ops` batch (with the writer's `cursor` when the batch carried one), `reload`; the client only ever sends `cursor`. Auth like HTTP (session cookie + `?ws=` (else the default workspace) or share token, resolved in the handler — the middleware doesn't run for websockets); viewers join too. Closed with 4403 when access is refused, or revoked while open (a share changed or stopped, a member removed — `collab.revalidate`), and with 4409 when the same tab (`client`) joins again on a newer socket |

### Pages (`pages.py`) — page first, PDF as an action on it
| Method | Path | Purpose |
|---|---|---|
| POST | `/pages` | create a text-only root page: body `{title?, folder?, id?, properties?}` (title defaults to `Untitled`, `folder` → `properties.folder`; `id` keeps a page's id when a mirror brings it over — 400 when malformed or reserved (`root`, `trash`), 409 when a live block has it (a page of that id in Recently deleted gives way); `properties` seeds the page's own) → the block dict. On a share host, 402 `{detail, limit, used, plan}` when the owner's plan allows no more pages in their default personal workspace ([mirror.md](mirror.md) "Publishing") |
| POST | `/pages/by-docs` | which pages these stored files became: body `{doc_ids: [<hash>, ...]}` (≤500) → `{pages: {hash: {id, title}}}` — a hash matches the page carrying it as its PDF (`doc_id`) or the note page imported from it (a markdown upload's `markdown_import`); hashes with no page absent; any member. The file chips ask once per page render for their "open page" button and the menu's "Open page" / "Add to library" |
| POST | `/pages/from-file` | "Add to library" on a markdown file chip: body `{filename: "<hash>.md", original?, folder?}` → `{page, created, imported?}` — the stored upload becomes a note page through the `/import/markdown` importer (title from front matter, else `original` minus its extension), filed in `folder`; idempotent (a page whose `markdown_import` is the hash is returned with `created: false`); the file is untouched, the page is a copy. 400 for anything but a stored markdown name, 404 when the file is not in the workspace; workspace editors |
| POST | `/pages/{page_id}/attachment` | attach a PDF to a page that has none: body `{doc_id?, source_url?, original_filename?}` (at least one of `doc_id`/`source_url`; `doc_id` is shape-validated only — a URL-opened PDF's id is the URL hash and the proxy fetches it lazily, like `by-doc`; `source_url` defaults to `/api/uploads/<doc_id>.pdf`). While the title is still automatic (`Untitled`/empty) it becomes the file name / URL tail and is marked `auto_title`. → the updated block. 400 bad input / not a root page, 404 unknown page, 409 `{"detail": "page already has an attachment"}`, 409 `{"detail": "attachment belongs to another page", "page_id"}` |
| DELETE | `/pages/{page_id}/attachment` | drop `doc_id`/`source_url`/`original_filename` (highlights keep their `pdf_position`; the file stays, and unless something else references it is purged 30 days on — a re-attach before then finds it, [user_db.md](user_db.md) "Stored files") → `{ok, block}`; 404 when the page has no attachment |

The writers need a workspace editor (`require_ws(write=True)`): a share token
never creates pages or touches a page's attachment. `GET /pages/{id}/export*` live
in `export.py`.

### Recently deleted (`routers/trash.py`, `gamma/trash.py`) — see [home_library.md](home_library.md) "Recently deleted"
| Method | Path | Purpose |
|---|---|---|
| GET | `/trash` | the pages deleted in the last 30 days, the last deleted first → `{pages: [{id, title, folder, deleted_at, deleted_by, purge_at}], keep_days}`; any member |
| POST | `/trash/{page_id}/restore` | back under `root`, last in the library, its folder labels as they were; the root is stamped and the tombstone cleared (the change feed shows the page again) → the page's block dict; 404 when it is not in the trash |
| DELETE | `/trash/{page_id}` | delete one page of the trash for good (`ops.delete_page`: blocks, op log, chats, index rows; its files go to the orphan check) → `{ok, id}`; the tombstone keeps its trashing's time; 404 when it is not in the trash |
| DELETE | `/trash` | delete every page of the trash for good → `{deleted: [ids]}` |

The writers need a workspace editor, like deleting a page; share tokens never
reach the trash. The sweeper in `gamma/trash.py` deletes pages trashed more
than 30 days ago the same way, every hour.

### PDFs & uploads (`pdf.py`, `uploads.py`)

Publisher connections use `routers/publisher_sessions.py` and require a personal
account; guest and share-token access is rejected.

| Method | Endpoint | Behavior |
|---|---|---|
| GET | `/publisher-sessions` | Connection metadata and supported `publisher_roots`; never cookie values |
| POST | `/publisher-sessions` | Save `{host, cookies, user_agent?}` for the signed-in account; requires HTTPS or localhost, JSON, and a body of at most 256 KiB |
| DELETE | `/publisher-sessions/{host}` | Disconnect that account's host; returns `{ok: true}` |

See [publisher sessions](paper_metadata.md#connected-publisher-sessions) for
encryption, expiry and request scoping. The PDF endpoints below use the same
guarded fetch path.

| Method | Path | Purpose |
|---|---|---|
| POST | `/resolve-pdf` | URL/arXiv/DOI → fetchable PDF (citation_pdf_url sniffing, Unpaywall OA fallback) |
| GET | `/pdf` | proxy/download a PDF (`save=1` caches it server-side); a copy already cached answers 302 to `/uploads/<id>.pdf`, carrying the request's `share` / `ws` query so the browser's follow-up stays in the same library |
| POST | `/uploads`, `/upload-image` | store a PDF / an image (content-hash names, dedup'd; quota-gated) |
| POST | `/upload-file` | store a file for a block to reference as `[name](/api/uploads/<hash>.<ext>)` — the file chip. Any extension except executables (`storage.BLOCKED_EXTENSIONS`: exe, msi, bat, dll, ps1, …; 400 "not accepted (executable)"); the extension comes from the uploaded name, lowercased, `.bin` when there is none; images route like `/upload-image`, a `.pdf` must be a real PDF and lands under the same `<hash>.pdf` the PDF ingest mints (so it can be opened as a document page later), an `.ink` must be a valid drawing (the route a mirror pushes drawings by); same hashing + limits → `{url, name, size, already_existed}` |
| POST | `/upload-ink` | store a handwriting group's `gamma-ink` JSON (the request body; validated against `gamma/ink.py`'s schema and limits, stored as it came, so the clients' `serializeInk` bytes dedup and a client can name a file by its hash) as `<hash>.ink` → `{url, size, strokes, bbox, pdf_position, already_existed}`; editors and edit shares. [handwriting.md](handwriting.md) |
| GET | `/pdf-info/{doc_id}` | the document manifest the viewer lays a PDF out from before pdf.js has parsed it (`gamma/pdf_meta.py`, [pdf_loading.md](pdf_loading.md)): `{doc_id, bytes, pages, dims: [[w, h], …]}` in PDF points, rotation applied; same access rule as the file; computed in pdfium on first request when the upload-time background walk has not run (`pages: 0` for an unreadable file, not cached); 400 malformed id, 404 no such file |
| GET, HEAD | `/uploads/{filename}` | serve stored files (HEAD: the headers alone, which is how the viewer learns a file's size before choosing its transport); with their media type (`storage.FILE_MEDIA_TYPES`, else `application/octet-stream`); pdf / images / txt / md render inline, everything else is `Content-Disposition: attachment` (html additionally sandboxed like svg); blocked or malformed extensions 400. Sync `def`: a share visitor's access check reads the shared pages in the threadpool |
| GET | `/quota` | the limits that apply to uploads into the request's workspace — the account's for a personal one (`used_bytes` = all its personal workspaces), the workspace's own for a shared one — with `workspace_bytes` and `account` (the person, or "") |

### Shares (`shares.py`)

One share link per page or per folder of a workspace. What a token reaches and who gets in: "Auth model" above.

| Method | Path | Purpose |
|---|---|---|
| POST | `/share/{page_id}` | create the page's share link (defaults `anyone`/`view`; optional body `{audience, role, users}` applies to a NEW link, validated like a PUT — the Share popover always sends one: the audience tile picked, or `list` with the first person invited) or return the existing one unchanged — root blocks only (400 otherwise); workspace editors and owners |
| GET/PUT/DELETE | `/share-settings/{page_id}` | read settings (`{token: null}` when unshared; any member) / change `audience`, `role`, `users` (`["carol"]` or `[{name, role}]`; validated: unknown usernames or roles → 400; the token stays; `edit`+`anyone` is allowed — see "Link visitors" above) / stop sharing (the token dies) — editors and owners |
| POST | `/share/folder?name=` | the same for a folder (`name` a folder-label path): 400 for an empty path, 404 when no page is filed in the folder |
| GET/PUT/DELETE | `/share-settings/folder?name=` | the folder share's settings, changes and stop, as for a page. The share follows the folder's renames (`POST /folders/rename`) and dies with the folder |
| GET | `/share/{token}` | resolve a link for this viewer → `{page_id, folder, username (who shared it), workspace_id, audience, role, can_edit, viewer, viewer_is_guest}` plus, for a page share, `doc_id` (the page's PDF attachment id via `page_attachment`, `""` without one); a folder share's listing is `GET /blocks/root/children` through the token; `viewer`/`viewer_is_guest` let the share view offer "Open in my library" or "Add to my library"; 404 unknown (or a page share whose page is deleted or in Recently deleted — a restore brings the link back), 401 sign in first, 403 signed in but not allowed |

### Search (`search.py`, `gamma/block_index.py`, `gamma/pdf_index.py`)
| Method | Path | Purpose |
|---|---|---|
| GET | `/search?q=&limit=&scope=` | one search over the knowledge base: notes (`block_fts`) + PDF text (`pdf_fts`). `scope` = `""` (library) or a folder path (that folder and its subfolders). → `{"results": [...], "indexing": n}`; results are notes hits first (bm25 order) then PDF hits, each capped at `limit` (default 20, max 100). A notes hit is `{"source": "notes", "block_id", "page_id", "title", "snippet"}` (`block_id` = the matched block, `page_id` its page root, `title` the page's); a PDF hit is `{"source": "pdf", "block_id", "page_id", "doc_id", "title", "page", "snippet"}` (`block_id` = `page_id` = the page carrying the PDF, `page` the 1-based PDF page). `indexing` = note pages the background refresher hasn't rebuilt yet + PDFs the background extractor hasn't reached. Any member |
| GET | `/pdf-search` | the PDF-only predecessor (same `pdf_fts` index; hits `{block_id, doc_id, title, page, snippet}`) — the Ctrl+F panel's library group still uses it (with `/block-search` for notes: substring matching + case / whole-word flags that FTS does not offer) |
| POST | `/search-reindex` | full rebuild (PDF text re-extracted in the background, every note page stamped stale and rebuilt in the background), or just `doc_ids` from the body → `{scheduled, busy}`. The extraction is the workspace's `indexing` job ([tasks.md](tasks.md)): its members see it in `GET /api/jobs`, and its editors stop it with `POST /api/jobs/{id}/cancel` (it finishes the paper it is on; the rest stay stale and index on the next search) |

The notes index is rebuilt per page, each page in its own short data.db
transaction. A page is stale when its `block_fts_meta` row is missing, older
than `textnorm.INDEX_VERSION` (`ver` 0), or no longer matches the page root's
`updated_at`; the writers that change a child without touching the root (`PUT
/blocks/{id}/children` on a nested block, a cross-page `reorder`) call
`block_index.mark_page_dirty`. A search rebuilds stale pages itself for up to
`REFRESH_BUDGET_S` (0.2 s — so a page edited a moment ago is found) and hands
the rest to the background refresher (one thread per process), which also
re-indexes every page an op batch wrote once it has been quiet for `QUIET_S`
(an `ops.commit_listeners` entry `search.py` registers). FTS5 finds rows only
by rowid or MATCH, so `block_fts_rows` / `pdf_fts_rows` map each page / paper
to its rows' rowids and every delete goes by rowid; an index written before
those tables existed is taken over once when they are created.
Deleting a page for good or detaching its PDF prunes its rows (`block_index.purge_page_data`,
which also drops the `pdf_fts` rows of papers no block carries and the deleted
blocks' chats; pruning reads `block_fts_meta` alone). The `pdf_fts` schema and its shared queries (`pdf_missing`,
`search_pdf`, `store_doc`, `doc_pages`) live in `gamma/pdf_index.py`; extraction and the background
indexer in `search.py` (a paper's rows are written a few hundred pages per transaction).

#### Search matching and display

Search normalization lives in `gamma/textnorm.py` and its frontend mirror,
`shared/lib/textnorm.js`: ligatures, hyphenated line breaks, and digit
separators should match consistently. Bump `textnorm.INDEX_VERSION` when
extraction or normalization changes so old indexes rebuild. Add shared cases
to `tests/shared/textnorm.json` and run both Python and Node coverage.

`search/SearchPanel.jsx` groups titles, the current page's notes and PDF,
other notes, reference links, and library PDF hits. It can collapse into a
compact find bar. `buildSearchRegex` supplies the frontend matcher; the
server's `/block-search` uses its own bounded fuzzy scan after an SQL
prefilter and refuses caller-supplied regexes. There is no replace UI.

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
| POST | `/clip` | one-shot "save this page": dedup by DOI/arXiv/URL → resolve → fetch + store (`save_copy`) → page (`get_or_create_doc_page`) → folder/labels → metadata in a background thread. Body: `source_url, pdf_url, doi, arxiv_id, doc_id (pre-uploaded bytes), title, selection, folder, labels, allow_oa, save_copy`. Returns `{block_id, doc_id, title, existed, open_url, folder, labels, note?}` (`existed`: the page was there already — found by identifier, URL or its PDF). **No PDF resolvable** (a plain web page, or a dead/HTML link) → a page titled from `title` (else the URL tail) with `properties.web_url = source_url` and the `selection` (if any) as its first `> quote — [title](url)` block; `doc_id` is `""` and `note` says so. Re-clipping that URL finds the page (`find_web_page`, by `web_url` on attachment-less pages), files it and appends the new selection. Only a request with nothing at all (no URL, title or selection) is a 400 |
| GET | `/library/lookup?doi=&arxiv_id=&url=` | is this page in the library (`properties.meta`, `source_url`, `web_url`, URL hash — web-clip pages by `web_url`)? 404 when not |
| GET | `/library/preview?doi=&arxiv_id=&url=` | the registry record behind an identifier (arXiv API, then doi.org): `{title, authors, year, venue, doi, arxiv_id, source}` — the popup's title for a PDF tab before anything is saved. Identifiers are also extracted from `url`; 404 when no registry answers, 400 without an identifier. Cached in memory per identifier |
| GET | `/library/folders` | `{folders, labels}` in use (folder paths include their ancestors) — the popup's pickers |
| POST | `/clip/note` | the explicit "clip into page" append: `> quote — [title](url)` as the last block of `page_id`, or of the "Web clips" page (created on first use) |

All five are session-only, never share-token readable; the clip lands in
the request's workspace — the extension names none, so its personal one.

### Metadata (`metadata.py`)
| Method | Path | Purpose |
|---|---|---|
| POST | `/metadata/fetch` | resolve a paper or book (arXiv → DOI → ISBN via Open Library/Google Books → Crossref search → AI extraction, verified against Crossref / the book registries), cache meta + BibTeX + the slide citation on the page. Body also takes `cite_prompt`/`cite_model`; returns `meta` (with `unverified`), `bibtex`, `ppt_cite` (`""` when AI is off or that call failed), `source`, `cached`, `page_title` (the page's title after the write — always, since a concurrent lookup may have renamed it) and `title_updated` (this call replaced the automatic title) |
| POST | `/metadata/update` | save hand-edited fields incl. `publisher`/`isbn` (rebuilds BibTeX, keeps the document kind, drops the cached citation) |
| POST | `/metadata/cite` | BibTeX → PPT-style citation via AI (regenerate / fallback; the fetch already produces one) |
| GET | `/metadata/status` | library-wide health table (feeds Settings → Maintenance): every page with a PDF attachment plus pages carrying `properties.meta` without one (`has_file: false`); per paper `meta_source`, `meta_kind`, `meta_unverified` (null for pre-flag records) |

### AI (`ai.py`) — all config is GUI entries (each account's own plus the server's shared ones), no env API keys
| Method | Path | Purpose |
|---|---|---|
| POST | `/ai/chat` | chat; NDJSON stream of `{context}` (first line: per-page coverage — `page_id`, native/text, pages shown of total, `notes` when the page's notes are in it; `doc_id` `""` for a page without a PDF; the open paper's entry adds `selection: {passages: [{page, section, found, crop}]}` when passages were selected), `{model: {id, name, effort, tools}}` (which model answers, at what effort — `""` = the provider's default — and whether tools were armed; non-stream replies carry the same `model` field), `{trimmed: {turns}}` when the oldest history items were left out to fit the model's window (non-stream: a `trimmed` field), then `{delta}`/`{step}`/`{approval}`/`{handoff}`/`{action}`/`{progress}`/`{usage}`/`{truncated}`/`{error}` (`truncated: true` = the provider cut the reply off at its output limit; a read action carries `pdf_pages: [first, last]`) (`step` = `{id, tool, args}` announcing a tool call before it runs, plus `batch: n` (and `tools`) when the round's reads run side by side; `approval` = `{id, call_id, tool, perm, args, preview, timeout}`, the call waits for `POST /ai/approvals/{id}`; `handoff` = `{id, call_id, host, wall, source, timeout}`, the reply waits for the PDF from the user's browser until it arrives, is skipped (`DELETE /ai/handoffs/{id}`) or the wait gives up; a rename/move `action` adds `title`, `from`, `to`, a note tool's its page's `title`, a change that changed nothing `noop`, a `fetch_paper` stopped by a wall `handoff: {id, host, wall, source}`, one the browser then delivered `delivered: true` and one the user skipped `skipped: true`, a fetch its `version` and `probe`, a `read_paper` its helper's calls as `children`, every action `ms`; a call that waited on its card `approval` (the decision), one not made `declined: true`); a failure, as an HTTP error body or the stream's closing `{error}` line, carries `kind` (`not_configured` / `allowance` / `auth` / `rate` / `overloaded` / `unreachable` / `bad_endpoint` / `too_long` / `other`), the upstream `status` and `provider_id` / `provider_name` / `provider_auth` beside the plain-string `detail` / `error`; `usage` is the provider's token report `{input, output, cache_read, cache_write}`, one line per provider turn (the client sums an agent reply's rounds; non-stream replies carry one summed `usage` field); `progress` previews an edit_block/create_block call still being written (target id + markdown so far). Context is `pages` (up to 7 page ids, de-duplicated; they also become the tool scope's `context_pages`) or `page_id` (one; its PDF attachment derived server-side; `doc_id` is accepted as a compatibility input and resolves to its page), plus `chat_key` (the conversation's bucket, hashed with the account and workspace into the provider's prompt-cache id), model id, effort, images, files, the agent scope, the selected PDF passages `selections` (`[{text, page, box}]`, box `[x0, y0, x1, y1]` page fractions; the older `"---"`-joined `selection` string is still read), and the notes pointers `focus_block_id` (cursor block), `context_blocks` (attached block ids), `note_selections` (selected note text as exact source ranges `[{block_id, from, to, text}]`, what `edit_block` mode `"selection"` rewrites). `permissions` maps each tool permission to `"allow"`, `"ask"` or `"off"` (left out: reading allows, a change asks; `true` / `false` are allow / off), and `granted` lists the permissions the conversation allowed on a card; only a streamed request arms asking tools. `paper_wait` (default true) decides whether a blocked fetch holds the reply open on its card, and `delegate_reads` (default true) whether `read_paper` is offered. See [ai.md](ai.md) |
| POST | `/ai/revert` | take back one note change an agent reply made: `{kind: edit \| create \| move, block_id, revert, force?}` (`revert` as the note tool recorded it on the action) → `{page_id, noop}` (`noop`: the note already reads as before); 409 `{detail, conflict, preview?}` when it stops — `changed`, `filled` or `moved` (the note changed since; `preview.diff` is what `force: true` would do, `preview.children` the notes under a new block) or `gone` (can't go back, not forcible); 404 when the note is gone; workspace editors only. The write is the user's, logged with client `revert`. See [ai_tools.md](ai_tools.md) "Reverting a note change" |
| POST | `/ai/approvals/{id}` | the answer to a tool call waiting on its approval card: `{decision: once \| chat \| always \| deny, note?}` → `{ok: true}` (`note`, up to 2000 chars, is what to do instead, kept with `deny` only); only the account whose chat asked; 404 when nothing of theirs waits under that id (answered, timed out, or the chat stopped), 422 for another decision |
| POST | `/ai/chat/context` | the same body as `/ai/chat` (plus `title`) → `text/markdown`: what the chat would send the model — pages in context, system prompt, tools, every turn with the draft `prompt` last; PDFs as extracted text, pictures counted; calls no provider, so it works with none set up |
| GET | `/ai/models` | model registry (each model carries `native_pdf`: whether its provider accepts the PDF file itself, and `shared`: it comes from a server entry, `server:<id>:<model>`) + default prompts (feeds the model chip and prompt editor) + `transcribe` (some connection takes dictation: the chat's mic shows) + `efforts` (the reasoning efforts offered for a model whose own levels are unknown) + `allowance` (`{limit, used, exhausted}` for the shared entries, `limit` 0 = unlimited; null when none applies — [guests.md](guests.md)) |
| GET | `/ai/settings` | masked provider list (key hints only, each with its display `label`), then the server's shared entries the account may use as read-only rows (`shared: true`, key hint for admins only), plus the `protocols` and named `services` (e.g. DeepSeek) the add form offers, each with the key field's `key_placeholder` and `key_url` ("" for a sign-in protocol) |
| POST/PUT/DELETE | `/ai/providers[/{id}]` | manage the account's own provider entries (a shared `server:` id is a 404 here) |
| POST | `/ai/providers/{id}/test` | live probe of one credential (model: the entry's `test_model`, else the request's `model` — the client sends its metadata model — else the first model); failures carry an `auth` flag for expired/rejected credentials and the failure's `kind` (as on `/ai/chat`). Admins may name a shared entry (`server:<id>`) |
| POST | `/ai/providers/{id}/usage` | ChatGPT subscription allowance windows; explicitly unavailable for generic API-key providers; an expired sign-in returns `{available: false, auth: true}` in-body |
| GET | `/ai/usage` | the account's token usage as the providers reported it: `windows` (today / week / month / all → calls + the four counts), the 30-day split by `kinds` and by `models`, plus the shared `allowance` object; see [ai.md](ai.md) "Token usage" |
| DELETE | `/ai/usage` | forget the account's usage rows (the shared-entry rows of the last 24 h stay: the allowance still counts them) |
| POST | `/ai/health` | login connection check of one entry the account can use, its own or shared (`{provider_id, mode}`; `""` = the first): `mode: "ping"` is the free credential check (OAuth → usage endpoint, API key → `/v1/models`), `"test"` the tiny live completion; always answers in-body `{configured, ok, auth?, kind?, error?, provider_name, provider_auth}` (`kind` as on `/ai/chat` failures) |
| POST | `/ai/model-catalog` | list models available to a credential: the typed key, or a saved entry's (`provider_id`; admins may name a shared `server:<id>`) |
| GET | `/ai/model-info?model=<pid>:<model>` | what the chat needs about a model (`""` = the default one): `{model, context_window, source, efforts, efforts_source}` — its context window for the context ring and the reasoning-effort levels it takes, lowest first, each from the entry's own model listing (`source` `"provider"`), else the models.dev catalog (`"models.dev"`); `context_window` null when neither knows it; `efforts` `[]` for a model without effort control, null when unknown (the chat then offers `/ai/models`' `efforts`) |
| POST | `/ai/oauth/chatgpt/start`, `/status`, `/complete` | ChatGPT OAuth (PKCE; [ai.md](ai.md) "The chatgpt protocol"): `start` `{local?, device?}` → `{auth_url, state, local, device}` — `local` true when the server listens on localhost:1455 for the redirect (the page claims a loopback address and the request came from loopback), `device` `{user_code, verification_url}` when a device code was asked for and OpenAI gave one; `status` `{state}` → `{ready, error}`, polling a due device code with OpenAI; `complete` `{state, callback, provider_id?, name?, models?}` with the pasted redirect URL, or `callback: ""` once `status` is ready |
| POST | `/ai/transcribe` | voice dictation |
| POST | `/ai/translate` | translate paragraph texts for the viewer's translated view (`{texts, lang, model, effort, stream}` → `{translations}`; with `stream: true` an NDJSON stream of `{i: [indices], text}` partials as each paragraph is written, then the same final object; in-memory per-paragraph cache). `model: "engine:<id>"` (`microsoft`, `google`, `youdao`) translates with that machine-translation service instead — no AI provider needed, 503 when it isn't set up, never streams partials |
| GET | `/translate/engines` | the account's machine-translation services (`{engines: [{id, label, configured, needs_key, fields, updated_at, failing}], can_edit}`; secret fields as a `…last4` hint; `microsoft` needs no key and is always configured, and its `failing` is `{since, error}` during a failure streak, else null) |
| PUT / DELETE | `/translate/engines/{id}` | set (`{fields: {…}}`, an empty secret keeps the stored one) or remove a service's credentials (400 for a service that needs no key); guests 403; answers the GET shape |
| POST | `/translate/engines/{id}/test` | translate one sentence into `{lang}` with the stored credentials; in-body `{ok, text}` / `{ok: false, error}` |
| GET | `/ai/search-services` | the account's online search settings (`{engine, services: [{id, label, configured, web, fields, updated_at, server?}], can_edit}`): `engine` is `auto` / `ai` / `brave` / `searxng` / `off`, the services are `brave` (`api_key`), `searxng` (`url`) and `openalex` (`api_key`); secret fields as a `…last4` hint; `server: true` when the server's `GAMMA_SEARXNG_URL` serves an account without its own ([ai_tools.md](ai_tools.md) "search_web") |
| PUT | `/ai/search-services/engine` | `{engine}`: which service general web search goes through; 400 for an unknown one; guests 403; answers the GET shape |
| PUT / DELETE | `/ai/search-services/{id}` | set (`{fields: {…}}`, an empty secret keeps the stored one; `url` must be http(s) without credentials) or remove a service's settings; 404 for an unknown service; guests 403; answers the GET shape |
| POST | `/ai/search-services/{id}/test` | one small search with the stored settings; in-body `{ok, text}` / `{ok: false, error}` |
| GET | `/pdf-text-status` | whether a doc has extractable text |
| GET | `/ai/selection-crop/{doc_id}?page=&box=x0,y0,x1,y1` | the picture of a selected region a chat reply sent the model, drawn again from its saved page + crop box (page fractions); any workspace member |
| GET | `/ai/handoffs/{id}` | a fetch handed to the browser (`routers/ai_handoffs.py`, [ai_tools.md](ai_tools.md#walls-and-the-browser-handoff)): `{id, source, url, pdf_url, host, wall, detail, status: waiting \| done \| dismissed \| expired, watched, note, background, pages, held, from_url}` — never the text or the PDF; 404 for another account's or a forgotten request |
| POST | `/ai/handoffs/{id}/watch` | Gamma Connector took the request's tab (`watched: true`); optional body `{note, background}`: what it is doing there (`looking`, `check`, `signin`, `opening`, `refused`, `other`, `closed`; anything else clears it) and whether it is a background tab |
| POST | `/ai/handoffs/{id}/pdf` | multipart `file` (+ optional `url` it came from): the PDF for the request, read at once and kept in memory for the account; 400 not a PDF / no text layer, 413 over 40 MB, 409 once the request is settled |
| POST | `/ai/handoffs/{id}/store` | writable workspace: the delivered PDF (while held, `held: true`) into the workspace's uploads, content-hash deduped like `POST /uploads` → `{doc_id, source_url, already_existed, url}` (`url`: where the browser got it), for `POST /clip {doc_id}`; 404 when nothing is held |
| DELETE | `/ai/handoffs/{id}` | settle the request without a PDF. Optional body `{note}` — what the user wants the assistant to do instead, which only the model reads: the card's **Skip** while the reply waits on it |
| GET | `/ai/handoffs/{id}/go` | no auth: the HTML page the card's Open leads to when it cannot hand the tab to Gamma Connector (desktop app, no answer yet) — for the request's owner it goes straight on to `url`, anyone else gets a "Continue to host?" link; 404 page when expired |

### Chats (`chats.py`, prefix `/api/chats`)

`GET /chats/{page_id}?share=<token>` exposes only the shared page's active
saved conversation, subject to the link's audience. Shared pages show this in
a read-only AI chat window on desktop and mobile, with search and copy.
Chat mutations reject share tokens, including links that allow page editing;
archived conversation browsing remains session-only. Every chat write takes
a workspace editor (`require_ws(write=True)`): a viewer or a read-scope
token reads chats and history, and gets 403 on each write below.
| Method | Path | Purpose |
|---|---|---|
| GET/PUT/DELETE | `/chats/{key:path}` | the ACTIVE conversation per bucket: page id, `home`, or `home:<folder>` (hence `:path`); GET → `{messages, title, updated_at}` (`updated_at` the conversation's version, `""` without one; a share read gets `{messages, title}`), PUT `{messages?, title?, updated_at?}` → `{ok, updated_at}` — title omitted = keep, messages omitted = a rename (title only, unconditional); with `updated_at` (the version the copy is based on, `""` = none) a save from an older copy is refused with 409 `{detail, messages, title, updated_at}`, the stored conversation, and nothing changes ([ai.md](ai.md) "Chat history buckets") |
| GET | `/chat-history?bucket=` | the bucket's archived conversations, newest first (`{sessions: [{id, title, preview, count, created_at, updated_at}]}`) |
| POST | `/chat-history/archive` | "New chat": file `{bucket, messages, title, updated_at?}` into history and clear the active row (→ `{id}`, null when empty); when `updated_at` isn't the stored version, the newer stored conversation is archived too (unless the copy holds all of it; alone when it holds all of the copy) |
| POST | `/chat-history/{id}/open` | make an entry the active conversation; the body's `{bucket, messages, title, updated_at?}` (the current one) is archived first, as for `archive` (→ `{messages, title, updated_at}`) |
| PUT/DELETE | `/chat-history/{id}` | rename (`{title}`) / delete an archived conversation |
| POST | `/chat-history/delete` | delete several at once (the history popover's ticked rows): `{ids}` → `{deleted}`, the rows that were still there |

### Import & export (`imports.py`, `export.py`)
| Method | Path | Purpose |
|---|---|---|
| POST | `/import/logseq` | Logseq .pdf + .edn (+ optional .md) import into the page carrying that PDF, created when absent (under the write lock, like `by-doc`); highlights already there are skipped by quote and notes by text, so a re-run adds nothing twice; into an existing page it is logged as a `reload` by the importing account and its room reloads → `{ok, block_id, doc_id, source_url, imported}` |
| POST | `/import/markdown` | UTF-8 `.md`/`.markdown` file → note page and nested blocks (optional `folder`; a front-matter `folder:` files it below that) |
| POST | `/import/markdown-zip` | zip of Markdown notes → one page per `.md` (multipart `file`, optional `folder` prefix): Obsidian vaults (wikilinks/embeds → mentions and synced blocks, `^id` anchors and headings as link targets, `tags` → labels, `aliases` kept, comments and fold markers dropped, `.obsidian/` skipped), Notion "Markdown & CSV" exports (subpage folders → folder labels, databases → table pages, links → mentions, images uploaded), Gamma Markdown / Obsidian exports (folder/source/meta/bibtex restored) or any zipped notes. Idempotent by file digest / `notion_id`; the report says `obsidian: true` for a vault |
| POST | `/markdown-blocks` | parse markdown text into a `{content, children}` tree without storing anything (the editor's paste-as-blocks helper; same parser as `/import/markdown`, 5 MB cap) |
| POST | `/import/pdf-annotations` | import annotations embedded in the PDF (idempotent; optional `strip`) |
| POST | `/import/zotero/preview` | Read-only import plan (multipart `file`, optional `folder` prefix; workspace writer). Archive entries, destination pages with PDF/page and create/merge status, folder paths, attachment warnings. Uses the same planner as import; stores no pages or files |
| POST | `/import/review` | Shared staged upload/review for `zotero`, `markdown-zip`, `markdown-file`, `gamma` (multipart `file`, `source`, optional `folder`, `strip`). Returns `review_id`, archive entries, destinations, source selection IDs and warnings; writes no library content. The import itself is the `import` job (`POST /api/jobs/import`, below) |
| DELETE | `/import/review/{id}` | Discard the staged upload/review; 409 while its import job reads it |
| POST | `/import/markdown-zip/preview`, `/import/markdown-file/preview`, `/import/gamma/preview` | Format-specific read-only review adapters. Markdown single-file review shares the ZIP engine. Gamma requires a non-guest workspace |
| POST | `/import/markdown-file`, `/import/gamma` | Reviewed single-note or additive Gamma import; multipart `file`, `selected` JSON source IDs (required for Gamma). ZIP Markdown/Zotero imports also accept optional `selected` (omitted = all, empty = none) |
| POST | `/import/zotero` | Zotero library import: zip of a "Zotero RDF" export (multipart `file`; `strip`, optional `folder` prefix). Items and standalone/additional PDFs→pages+metadata, collections→folders, tags→labels, notes→blocks; embedded annotations via the same importer. Idempotent by file hash / `zotero_key`; returns page destinations and warnings |
| GET | `/pages/{id}/export` | page export (`?mode=readable|obsidian|notes-pdf|annotated-pdf|logseq-graph|zotero-rdf|gamma` + `highlights=&notes=&pdf=`); `obsidian` = a vault zip (`<folder>/<Title>.md`, wikilinks, `attachments/`, `.obsidian/app.json`); `notes-pdf` = the notes typeset as their own PDF (works without a paper); `annotated-pdf` = what `/export-pdf` answers; `gamma` = scoped backup for `/import-data?mode=merge`. The share view's download; the web app exports through the `export` job |
| GET | `/pages/{id}/export-pdf` | the page's own PDF with annotations written back (`?highlights=&notes=`); for a page with sheets of paper and no PDF, a PDF of the sheets with the paper and the handwriting drawn ([notebooks.md](notebooks.md)) |
| POST | `/folders/rename` | follow a folder rename/move/delete for what names a folder by its path — the per-folder chat buckets (active + history, `chats.move_folder_buckets`) and folder shares (`shares.move_folder_shares`; one already at the destination wins): `{src, dst}`, dst `""` for a delete (the shares go; each active conversation is filed into its own bucket's history, and a merge into a folder with its own conversation files the moved-in one into that history — a conversation is never dropped) → `{ok, moved, history_moved, archived, shares_moved}`; workspace editors, never through a share link (`gamma/routers/folders.py`) |
| GET | `/folders/export` | whole-folder export, same modes/flags (`?name=` + `mode=`); subfolders become Zotero collections or vault directories, `notes-pdf` one PDF for the whole folder, `annotated-pdf` a zip of each paper's annotated PDF (`<subfolder>/<Title>.pdf`; pages without a PDF left out) |

### Background jobs (`routers/jobs.py`, `gamma/jobs.py`) — see [tasks.md](tasks.md)
Session accounts only (401 without one; integration tokens 403). A job is its starter's; a workspace's own jobs (the indexer, `owner: ""`) are its members', in the workspace the request works in. A job: `{id, kind, owner, workspace, title, params, state: queued|running|done|failed|cancelled, progress: {done, total, unit, phase, item}, error, created_at, started_at, finished_at, artifact: {name, type, size} | null, downloaded, stoppable, stopping}`, `result` only from `GET /jobs/{id}`.

| Method | Path | Purpose |
|---|---|---|
| GET | `/jobs` | `{jobs}`: the account's newest 100, the request workspace's own, and two read-only kinds from subsystems that keep their own state — the account's scheduled backup tasks while one runs (`kind: "scheduled-backup"`) and its blocked paper fetches while one waits for the browser (`kind: "paper-handoff"`, [ai_tools.md](ai_tools.md#walls-and-the-browser-handoff)) |
| GET | `/jobs/{id}` | one job with its `result` (404 for a job the account may not see) |
| POST | `/jobs/{id}/cancel` | stop it: a queued job ends at once, a running one at its next step; 409 once it can no longer stop (a restore swapping the databases); a workspace's job needs an editor |
| DELETE | `/jobs/{id}` | remove a finished job and its file (409 while it runs) |
| POST | `/jobs/clear` | remove every finished job the account may remove → `{removed}` |
| GET | `/jobs/{id}/download` | the file a finished job wrote (its starter only; 404 once it expired, 24 h after it ended) |
| POST | `/jobs/export` | `{page_id \| folder, mode, pdf, highlights, notes}` — a page or folder export in any `/pages/{id}/export` mode (`routers/export.py`; any member); its file is the download, its result `{pages, skipped: [{title, reason}]}` |
| POST | `/jobs/import` | `{review_id, selected}` — the staged review's selected items imported (`routers/imports.py`; workspace writer); its result is the import report. The same review asked again answers its job (409 with another selection) |
| POST | `/jobs/workspace-export` | `{ws?, user?, all?, uploads}` — `/export` or `/export-all` as a job whose file is the zip |
| POST | `/jobs/research` | `{question, folder, model, read_char_limit}` — research the question in the background and file a report page (kind `research`, [tasks.md](tasks.md)); writable workspace and a personal account, 400 without a question. The user starts this, never the model |
| POST | `/jobs/snapshot` | `{workspaces, uploads, label}` — a snapshot of each (their owner); result `{snapshots, failed}`, failed only when every one did |
| POST | `/jobs/restore` | multipart `file`, `mode`, `ws?`, `user?` — `/import-data` as a job; the upload is kept until the job read it |
| POST | `/jobs/restore-snapshot` | `{ws, name, mode}` — a stored snapshot restored (owner) or merged (editor) in place |
| POST | `/jobs/server-backup` | `{label, uploads}` — `/admin/backups` as a job (admins, `routers/admin.py`) |

Starting a job answers 429 when the account already has 20 queued or running, 507 for a file-writing job when the disk or the account's finished files (10 GB) are full.

### Prefs (`prefs.py`)
| Method | Path | Purpose |
|---|---|---|
| GET/PUT | `/prefs/{key}` | small synced JSON KV per account: `open-tabs`, `recent-views`, `pinned-folders`, `read-pos` are stored per workspace (the request's), `profile` / `ai-provider` account-wide (`db.USER_PREF_KEYS`); `profile` is the web app's account-scoped settings as one object keyed by preference name (400 unless an object; `db.get_profile` / `db.set_profile`, [settings.md](settings.md)); reading `profile` first syncs it with Gamma Cloud when the last sync is over a minute old, and its answer carries `cloud_choice` (a first sync waits for the person's choice); values over 64 KB get 413; refuses the reserved `ai-settings`, `translate-engines` and `profile-base` keys |
| PATCH | `/prefs/profile` | `{set: {name: value}}`: sets those entries of the profile and keeps every other one as stored (`db.patch_profile`) — how the web app saves, so a tab's stale copy of an entry it did not touch never undoes one synced from elsewhere; answers `{key, value, updated_at}` with the whole profile; 413 over 64 KB |
| GET | `/page-snaps` | all recents-card cover thumbnails `{snaps: {pageId: {img, at}}}`; `?after=<iso>` returns only newer ones (the focus-pull delta) |
| PUT | `/page-snaps/{page_id}` | store a cover (JPEG data URL body `{img, at}`; per-page newest-`at` wins, count-capped server-side); the covers are the workspace's, so workspace editors only (a viewer's stay in its browser) |
| DELETE | `/page-snaps/{page_id}` | drop a cover (the recents card's ×); workspace editors |

### Notices (`notices.py`, `gamma/notices.py`) — see [settings.md](settings.md) "Notices"
| Method | Path | Purpose |
|---|---|---|
| GET | `/notices` | `{notices: [{id, fingerprint, tone, pane, title}]}` the account has not looked at yet, strongest `tone` (`info` / `warn` / `error`) first; `pane` is the Settings pane that resolves it. The sources (`update` and `log-errors` for admins; `backup-failed`, `mirror-conflicts`, `publish-conflicts`, `cloud-sync`, `cloud-sync-choice`, `free-translate`, `storage` for everyone) are the table in [settings.md](settings.md) "Notices". Guests and integration tokens get `[]`. Sync: the release check may hit the network when its cache is stale |
| POST | `/notices/{id}/seen` | `{fingerprint}` — the account has seen this version of the notice (kept in the account-wide `notices-seen` pref); it stays quiet until the fingerprint changes. 403 for guests and tokens, 400 for a malformed id or fingerprint |

### Integrations and MCP (`routers/integrations.py`, `mcp_oauth.py`, `mcp_server.py`) — see [mcp.md](mcp.md)
| Method | Path | Purpose |
|---|---|---|
| GET | `/integrations/tokens` | the current workspace's assistant connections (manual tokens and OAuth grants: id, name, dates), the MCP URL and whether browser sign-in is available; session only, never a guest |
| POST | `/integrations/tokens` | mint a manual token `{name, scope?: read \| write, expires_in_days?}` (shown once); at most 20 unexpired per account; `write` (a mirror's push credential, [mirror.md](mirror.md)) is refused to a viewer |
| DELETE | `/integrations/tokens/{id}` | revoke a connection |
| GET | `/integrations/oauth/request?request_id=` | the pending consent (client name, the account's workspaces) for the consent screen |
| POST | `/integrations/oauth/consent` | approve or deny a pending sign-in for one workspace |
| GET | `/.well-known/oauth-authorization-server`, `/.well-known/oauth-protected-resource` | OAuth discovery for MCP clients (no `/api` prefix) |
| POST | `/oauth/register`; GET `/oauth/authorize`; POST `/oauth/token` | dynamic client registration, the authorization redirect, the PKCE code exchange (no `/api` prefix) |
| POST | `/mcp` | the Streamable HTTP MCP endpoint (bearer token or OAuth access token; no `/api` prefix, browser origins refused) |

A manual token (`gamma_…`, not an OAuth one) is also accepted on every `/api/*` route as `Authorization: Bearer` (`auth.py`): the request runs as the account behind it, in the token's workspace only (`?ws=` / the header may only repeat it — 403 otherwise), writes only with the `write` scope (403 "this token is read-only"), never as an admin, and never on an ACCOUNT-level endpoint: everything behind `auth.require_user` alone — the AI provider entries (whose base URL decides where the stored key goes), models, usage and ChatGPT sign-in, translation keys, `/prefs/*` (the profile and keybindings included), `/session`, `/workspaces/*` and backups, export and import, backup tasks, publisher sessions, the chat's fetch handoffs, cloud sign-in, integration tokens and mirrors — answers 403 "an integration token cannot do this — sign in" whatever the token's scope. A token reaches only what resolves a workspace (`require_ws` / `resolve_ws` / `require_ws_writer`: blocks, pages and their ops, uploads, the change feed, search, chats, cover snapshots, shares, `POST /ai/chat`); `/notices` answers it `[]`. `auth.can_write` is the same rule for an endpoint that offers less instead of refusing: `POST /ai/chat` through a read token (or for a workspace viewer) arms the reading tools only.

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
| GET | `/mirrors/{ws}/conflicts[?resolved=1][&page=]` | the merges the engine decided on its own (kinds `merged`, `diverged`, `dropped`, `kept_local_edit`, `restored_remote_edit`, `page_restored`, `page_restored_from_remote`) |
| POST | `/mirrors/{ws}/conflicts/{id}` | `{choice: keep \| mine \| theirs}` — the text is written into the block's page as it is now, then the conflict is marked resolved; 409 (the conflict stays open) when the block refuses the write |

Session only, the mirror's owner — the account recorded as its owner that
also owns the copy's workspace (anyone else gets 404 and an empty list) —
never a guest.

### Publishing (`routers/publish.py`, `gamma/publish.py`) — see [mirror.md](mirror.md) "Publishing"

| Method | Path | Purpose |
|---|---|---|
| POST | `/pages/{id}/publish` | publish the page to the share host Gamma Cloud names: body `{audience?: anyone \| users \| list, role?: view \| edit}` (optional; the share there, default anyone / view, applied to a new or an existing link) → `{url: "<share host>/?share=<token>", public_url, share: {token, page_id, audience, role, users, created_by}, mirror: {ws, status, page_filter, mode, detached, conflicts_open, pending_local}}` — `public_url` the page's pretty address (`https://<username>-pages.gammapdf.com/<slug>-<id>`) when the share host reports a `page_host`, else the same as `url`. Adds the page to the workspace's filtered mirror of the share host (made on the first publication through `/auth/cloud/exchange` there), runs one round and makes the share. Workspace editors, session only, never a guest. 409 with a message when it cannot: no linked Gamma Cloud identity with a token ("Sign in with Gamma Cloud to publish."), no share host named, a workspace that is a copy of another server, a mirror owned by someone else, detached or receive-only, or this server is itself a share host, or the plan's cap there (the share host's words — "Free plan: up to 5 published pages. Unpublish one, or upgrade your Gamma Cloud plan." — plus `limit: {used, max, plan}`); 400 for a block that is not a page; 502 when the share host refused or the page did not reach it; 503 when the account server cannot be read |
| DELETE | `/pages/{id}/publish` | unpublish: the share there stops, the copy there is deleted, the page leaves the filter; the page here is untouched → `{published: false, mirror}`. 409 when the page is not published; 502 (nothing changed) when the share host cannot be reached |
| GET | `/pages/{id}/publish` | `{published, can_publish, reason?, url?, public_url?, share?, status?, mirror?, limit?, error?}` — `can_publish` / `reason` say whether publishing would be refused and why (the same messages as POST), `status` is the mirror's raw status (the pill's reading is the frontend's), `share` the live share settings there (`url` and `public_url` with them), `limit` `{used, max, plan}` read from the share host whenever the account holds a publishing token (`max` null = no cap), `error` when the share host could not be read. Any member |
| GET | `/publish/limit` | the share host's half: `{used, max, plan}` — the root pages of the request's workspace (a publishing mirror's token names it) and the cap its owner's plan puts on them (`max` null = none). 404 on a server that is not a share host. Nothing cached |
| GET | `/pages/resolve-public?host=&path=` | no auth: a page host's pretty address → `{share, page_id}`, the share token the share view opens with (audience and role its own). `host` must match `GAMMA_PAGE_HOST` (the username read out of it), `path` is `/<slug>-<id>` or `/<id>`; only the trailing id counts, a root page with a share in that account's default personal workspace. 404 otherwise (counted like an unknown share token); 429 past 120 per IP in 5 minutes |

### Publisher sessions (`routers/publisher_sessions.py`) — see [extension.md](extension.md)
| Method | Path | Purpose |
|---|---|---|
| GET | `/publisher-sessions` | the account's connected publisher hosts (metadata only) and the supported roots |
| POST | `/publisher-sessions` | store a cookie snapshot for one host (JSON, 256 KiB cap; HTTPS or localhost, personal accounts only) |
| DELETE | `/publisher-sessions/{host}` | forget a host |

### Admin (`admin.py`, prefix `/api/admin`)
| Method | Path | Purpose |
|---|---|---|
| GET/POST | `/admin/users` | list (with usage and `default_workspace`) / create accounts (+ personal workspace) |
| PUT/DELETE | `/admin/users/{name}` | password, admin flag, storage overrides / delete through `workspaces.delete_account` (+ the workspaces only they owned, listed as `deleted_workspaces`, each copied to `backups/deleted/` first — 507 and nothing deleted when a copy cannot be written; guest accounts too, without copies) |
| POST | `/admin/users/{name}/rename` | rename (rows only — no files move; sessions survive): everything that names the account follows it, its offline copies, backup tasks, token usage, publisher connections and the invitations it sent included, so nothing passes to a later account of the old name; 409 while one of its backup tasks runs |
| GET | `/admin/workspaces` | every workspace (kind, access, public role, quota, `personal` = its account or "", `default`, members, upload size), plus orphan directories — Settings → Workspaces |
| GET/POST | `/admin/backups` | list the whole-data-directory snapshots under `backups/` (each with its manifest: `auto` for the migration runner's — for an old manifest without the flag, what pruning reads from its `v<N>` label —, `integrity` per database copy, `damaged`) / take one now (`{label?, uploads?}` — databases, plus every upload with `uploads: true`; 507 when it cannot be written; the web app takes one as the `server-backup` job); per-workspace snapshots are `/workspaces/{id}/backups` |
| GET | `/admin/backups/{name}/download` | the snapshot as a zip |
| DELETE | `/admin/backups/{name}` | delete a snapshot (restoring is `manage.py backups --restore`, server stopped — [migrations.md](migrations.md)) |
| POST | `/admin/check-databases` | `PRAGMA quick_check` of `users.db` and every workspace's `pages.db` and `data.db`, changing nothing: `{ok, checked_at, files: [{file, workspace, name, result, ok}]}`; a failed file raises the admins' `db-damage` notice until a later check of it passes (`gamma/integrity.py`) |
| GET/PUT | `/admin/settings` | server-wide storage defaults, plus `public_url` / `public_url_source` (the admin-confirmed public server URL, [mcp.md](mcp.md)), `guest_ttl_hours` / `guest_ttl_source` (1–720 hours, `guest_ttl_hours_range`; `environment` when `GAMMA_GUEST_TTL_HOURS` decides) and `demo_mode` / `demo_mode_source` (`environment` when `GAMMA_DEMO` is on) — a PUT of either is 400 while the environment decides ([guests.md](guests.md)) — and `cloud` (the cloud sign-in settings, written as `cloud_issuer`, `cloud_client_id`, `cloud_client_secret`, `cloud_policy`, `cloud_share_host`, plus the read-only `needs_connect` — [cloud_accounts.md](cloud_accounts.md)) |
| GET | `/admin/logs?after=<seq>` | scrubbed in-memory server log |
| GET/PUT | `/admin/ai-providers` | the server's shared AI entries, masked like `/ai/settings` (key hint, never the key; the same `protocols` and `services`), `guests` and `allowance` (`{accounts, guests}`: tokens per account per rolling 24 h, 0 = unlimited); PUT `{guests?, allowance?: {accounts?, guests?}}` (whole numbers 0..10^9, else 400). Chat, translate, metadata fetch/cite and transcribe answer 429 with a human `detail` once an account's allowance is used up; streams end with `{error: detail}` |
| POST/PUT/DELETE | `/admin/ai-providers[/{id}]` | add / edit / remove a shared entry (the `/ai/providers` fields and validation, at most 20; a POST takes API-key protocols only; ids are `server:<id>`; the key is write-only and stored encrypted). Admin session only: an integration token is refused. Test, model listing and a sign-in's usage go through `/ai/providers/{id}/test`, `/ai/model-catalog` and `/ai/providers/{id}/usage` |
| POST | `/admin/ai-providers/chatgpt/start` · `/status` · `/complete` | a shared ChatGPT subscription: the `/ai/oauth/chatgpt/*` flow (same bodies) for the server's list — `complete` adds a shared sign-in entry, or with `provider_id` reconnects one, and returns the shared view; the tokens are stored encrypted; a state from one flow never redeems on the other. Admin session only |
| GET | `/admin/server-info?refresh=` | the Server dashboard (`gamma/version.py`): `version` / `commit` / `branch` / `label` (from `GAMMA_VERSION` / `GAMMA_COMMIT` / `GAMMA_BRANCH` — the Docker build and the desktop shell set them, a non-release Docker build as `<tag>-dev.<n>` with its branch; a checkout is a "development build"), `started_at`, `uptime_seconds`, `python`, `platform`, `schema_version`, `frozen`, `log_counts` `{info, warning, error}` since startup, `latest` (`{version, url, published_at}` from the GitHub Releases API, cached six hours, ten minutes after a failure, `refresh=1` refetches; `GAMMA_UPDATE_CHECK=off` disables) or `latest_error`, `latest_build` (a `-dev` build only: `{version, branch, ahead_by, url}`, its branch's newest build from GitHub's compare API, same cache), `update` (`{kind: release|build, version, url}` or null; a newer release wins), `update_available` (True/False, None without a version to compare), `image`, `releases_url`. Sync: it may hit the network |

Rails: a guest account takes storage limits and deletion but no password,
admin flag or new name; no self-delete; the last admin can't be demoted or
deleted.
