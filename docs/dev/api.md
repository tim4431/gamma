# API reference

All endpoints are same-origin under `/api`. The frontend never talks anywhere
else; in dev, Vite proxies `/api` → `127.0.0.1:9001`.

## Auth model

- A `session` cookie identifies the user (middleware sets
  `request.state.user`). Identity-only endpoints use `require_user`.
- Every data endpoint works in a **workspace** ([workspaces.md](workspaces.md)):
  `?ws=` or the `X-Gamma-Workspace` header names it, nothing means the
  account's default personal workspace. `require_ws(request)` admits any member
  or signed-in non-guest account allowed by public workspace access,
  `require_ws(request, write=True)` editors and owners (a viewer gets 403);
  a caller without effective access gets 403. The returned workspace id is what the data helpers
  take; `request.state.user` stays the actor.
- Share tokens (`?share=<token>`) are the ONLY unauthenticated **read** path.
  `resolve_ws` returns the session's workspace, or the workspace of the share
  named by a valid `?share=` token — there is no `?user=` fallback (it used
  to trust any username and leaked whole accounts). A share is keyed by
  (workspace, PAGE) — the page's root block, so note pages without a PDF
  share exactly like papers; the PDF is just the page's `doc_id`/`source_url`
  — or by (workspace, FOLDER): a folder-label path, reaching the pages filed
  there or below it, read live (pages filed later join, pages moved out
  leave). Either way the token is scoped: `auth.share_scope()` hands every
  share-enabled endpoint a `ShareScope` (`allows_page` / `allows_block` /
  `allows_folder`; `blocks_store.assert_block_in_scope()` for block reads),
  so a token can only reach its own pages' subtrees and assets (their PDFs,
  uploads their blocks reference, their own `source_url` through the proxy)
  — root listing, backlinks and other pages are refused (403); folder export
  is refused for a page share and allowed for the shared folder and its
  subfolders. Nothing outside `ShareScope` branches on the share's kind.
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
  revokes the link's writes at once (the grant is re-read per request).
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
  one for large public deployments.
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
| GET | `/server-config` | public: what the login page offers besides a password — `{cloud: {enabled, issuer}, password_login, registration, guest, guest_ttl_hours, demo, page_host}` (`guest` false on a share host; `guest_ttl_hours` how long a guest account lives; `demo` whether demo mode is on — [guests.md](guests.md); `page_host` the per-account page hostname pattern from `GAMMA_PAGE_HOST`, e.g. `{username}-pages.gammapdf.com`, "" when unset — how the app knows it was opened on a page host) ([cloud_accounts.md](cloud_accounts.md)) |
| POST | `/auth/cloud/exchange` | the share host's half of publishing ([mirror.md](mirror.md) "Publishing"): `Authorization: Bearer <Gamma Cloud access token>`, body `{server?}` (the calling server's name) → `{token, workspace_id, username, url}` — a write-scope integration token (365 days, named "Published pages from <server>", replacing the live one of that name) on the person's default personal workspace here, the account resolved under the sign-in policy like a first sign-in (provisioned under `provision`, pending invitations claimed), and this server's address. 403 unless the server accepts published pages (`cloud_share_host`), for an unconfirmed cloud e-mail, or when the policy refuses the account; 401 for a token the account server does not know; 503 when it cannot be asked; 429 past 20 per IP or 10 per cloud account in 10 minutes |
| GET | `/auth/cloud/start?next=&link=1` | Sign in with Gamma Cloud: stores the pending PKCE sign-in and redirects to the account server; `link=1` needs a session and attaches the cloud identity to that account |
| GET | `/auth/cloud/callback?code=&state=` | the account server's return: verifies the ID token, resolves or creates the local account per the policy (`gamma/cloud_auth.py`), pulls the preference profile, registers this server on the person's server list (`gamma/cloud_sync.py`), mints a session and redirects to `next`; a refusal goes back to `/?cloud_error=` |
| GET | `/auth/cloud/sync-status` | the signed-in account's own preference profile sync (session only; guests and integration tokens get 403): `{profile: {state, at, error}, identity: {linked, username?}}`. `state` is `off` (cloud sign-in off, or no identity holding a token), `pending` (a push is scheduled, or failed and waits for the next check; `error` then says why), `synced` (the last pull or push agreed, at `at`), `error` (the last attempt failed) or `choose` (the first sync found two different copies and waits for the person's choice). Read from memory (`cloud_sync.profile_status`), no network; the Settings dialog polls it while open |
| POST | `/auth/cloud/sync` | sync the caller's profile with Gamma Cloud now (session only): `{action, defaults?}` with `action` `sync` (the automatic merge; answers `choose` while a first sync waits), `merge` (also settles the choice; `defaults` — the web app's default profile — is the base of a first merge), `fetch` (the cloud's copy replaces this server's; 409 when the cloud has none) or `push` (this server's replaces the cloud's). Answers `{outcome: pulled / pushed / merged / same / choose, profile}`; 400 without a linked identity holding a token, 502 when the account server could not be reached |
| GET / POST | `/auth/cloud/status`, `/auth/cloud/unlink` | the signed-in account's own cloud identity (username, plan, e-mail, linked at, `offline` — a refresh token is held — and `revoked_at`) plus `enabled` and the `issuer` (the account server's address, which the Account pane's "Open account" button opens); unlink is refused for an account without a password, and takes this server off the person's server list before revoking the grant |
| GET | `/session` | who am I, plus `workspaces: [{id, name, kind, role, access, public_role, personal, default, members}]` (memberships + every public workspace) and `default_workspace` (quota lives in `/quota`); `build` (`version`, `commit`, `label`, `frozen`) is what a problem report names the server by, sent to the login page too; a guest session adds `guest_expires_at` (UTC ISO: when the account and its workspace are deleted) |
| GET | `/accounts[?q=]` | the account directory for the invite / owner pickers: `{accounts: [{username, is_admin}]}`, non-guest accounts only (signed-in non-guest callers). On a share host only admins get the list; anyone else gets the one account named exactly `q`, or none |
| GET | `/export` (+ `/export-progress`) | backup zip of a workspace (everything or `uploads=0`; the `gamma-backup-1` zip of `gamma/ws_backup.py`): the request's, `?ws=` (any member), or — admins — `?user=` for an account's default workspace |
| GET | `/export-all` | every personal workspace of the account in one zip, one `/export` zip per workspace inside (`uploads=0` for databases only; guests 403) |
| POST | `/import-data` | restore (`mode=replace`, owners) / merge (`mode=merge`, editors) a backup zip into a workspace (same targeting); never into a guest's workspace |

### Workspaces (`workspaces.py`) — see [workspaces.md](workspaces.md)
| Method | Path | Purpose |
|---|---|---|
| POST | `/workspaces` | create a personal one (`{name}`; guests 403); admins may add `kind: "shared"`, `owner`, `access`, `public_role`, `quota_mb` |
| GET | `/workspaces/mine` | Settings → Workspaces: every workspace I can open with its `used_bytes` (and `mirror_of`, the remote workspace's name when it is an offline copy, `publishing` true while at least one of its pages is published to Gamma Cloud, where `mirror_of` stays empty — [mirror.md](mirror.md)), plus `account` (my limits and the usage of all my personal workspaces) |
| GET/PUT/DELETE | `/workspaces/{id}` | kind + members (pending invitations last, `pending: true` + `subject`) + quota + `personal_of` + `default` (any member; admins) / rename `{name}` (owner), `default: true` (a personal workspace's owner), kind, access + public role, workspace quota (admin) / delete (owner; not an account's last personal one) |
| GET/POST | `/workspaces/{id}/backups` | the workspace's server-kept snapshots (any member) / take one now `{label?, uploads?}` (owner; at most `ws_backup.MAX_PER_WORKSPACE`) |
| GET | `/workspaces/{id}/backups/{name}/download` | the snapshot as a zip — the same zip `/export` gives (any member) |
| POST | `/workspaces/{id}/backups/{name}/restore?mode=` | restore it in place: `replace` (owner) / `merge` (editor), the same rules as `/import-data` |
| DELETE | `/workspaces/{id}/backups/{name}` | delete a snapshot (owner) |
| GET/POST | `/backup-tasks` | the account's scheduled backup tasks (`routers/backup_tasks.py`, `gamma/backup_schedule.py`; signed-in non-guest) / create one `{name, enabled, scope: selected\|all_owned, workspaces[], cron, uploads, retention_mode: days\|count, retention_value}`; `cron` is five UTC fields, targets must be workspaces the owner owns (at most 100 tasks) |
| PUT/DELETE | `/backup-tasks/{id}` | replace the task (same body; 409 while it runs) / delete it, keeping its snapshots |
| POST | `/backup-tasks/{id}/run` | queue a run now, paused or not (409 while it runs) |
| POST | `/backup-tasks/preview` | `{cron}` → `{runs: [next three ISO times], timezone: "UTC"}` |
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
| GET/POST | `/blocks/by-doc/{doc_id}` | lookup / create the page BY ATTACHMENT — the page whose PDF is `doc_id` (POST creates it: `{default_title, source_url?, original_filename?, folder?}`, `folder` files a NEW page only); the PDF-ingest + extension-dedup path, and what "Open as document" on a PDF file chip calls (the file is already stored under that hash). Text-only pages come from `POST /pages` |
| GET | `/blocks/{id}/children`, `/{id}/subtree`, `/{id}/backlinks` | tree reads; the root listing (`/blocks/root/children`) — through a folder share token, only the pages the share reaches (the share view's home library); 403 through a page share — additionally gives every page a `preview` — the first ~240 chars of its first non-highlight child blocks joined with ` · ` (one window query, `""` when empty) |
| POST/PUT/DELETE | `/blocks`, `/blocks/{id}` | CRUD — inside a page these are thin wrappers over the op path (`gamma/ops.py`): logged, fanned out to the page's room; `PUT` takes `content` and/or a properties PATCH (a null value deletes the key). A new page (`parent_id: "root"`) is a plain insert (`blocks_store.create_page`); deleting a page is `ops.delete_page`: subtree + its op log gone, a `deleted_pages` tombstone left |
| PUT | `/blocks/{id}/children` | replace the whole subtree (delete + reinsert; triggers orphan-upload cleanup) — bulk paths only (imports, tests); the page's room gets a `reload`. The editor itself sends ops |
| POST | `/blocks/{id}/reorder` | move within the page (an op) or, with `parent_id` on another page, across pages (the source room sees a `delete`, the target reloads) |
| GET | `/block-search` | fuzzy note/page/highlight search; empty `q` returns recently edited blocks (feeds the `[[ref]]` popup's initial suggestions) |

Route order matters: the static-prefix routes (`by-doc`, `children`,
`subtree`) must stay registered before `/blocks/{block_id}`.
`GET /blocks/{id}/subtree` on a page also returns `seq`, the op-log position
the tree reflects (the live session catches up from it).

### Collaboration (`collab.py`) — see [collab.md](collab.md)
| Method | Path | Purpose |
|---|---|---|
| POST | `/pages/{id}/ops` | apply a batch of block ops `{client, ops: [set / insert / move / delete], cursor?: {block, anchor, head}}` (a `set` may carry `base`, the text its `content` was edited from: when the block changed meanwhile the edit is applied as a patch onto the current text — a three-way merge, `gamma/textmerge.py` — and the echoed op carries the merged text) (`cursor`: the writer's caret in the text after the batch, fanned out with it and stored as the writer's presence) in one transaction → `{seq, at, ops (as applied — re-keyed positions carry their final value), removed_uploads}`; a workspace editor or an edit share (confined to the shared page; the page root's properties stay the workspace's); a bad op fails the whole batch (400/403/404/413) |
| GET | `/pages/{id}/ops?since=` | the op log after a seq → `{seq, batches: [{seq, actor, client, at, ops}]}`; 410 when pruned past `since` (reload the tree) |
| GET | `/sync/whoami` | who the credential is on this server: `{user, workspace: {id, name}, role, scope}` — `scope` is an integration token's (`read` / `write`), `session` for a browser; what a mirror checks before it is created and at the start of every round |
| GET | `/sync/changes?since=&limit=` | the workspace change feed (`gamma/routers/sync.py`): pages whose root was stamped after the cursor (`pages: [{id, created_at, updated_at, seq}]`, `seq` the page's latest op) and pages deleted after it (`deleted: [{id, deleted_at, actor}]`, from `deleted_pages`), one time-ordered stream of at most `limit` (≤ 2000) entries → `{since, cursor, more, pages, deleted}`. `since=""` lists everything. The cursor is `<time>|<id>` while `more`, else the server time minus a 60 s grace, so the last minute is re-listed on every poll — the feed is a hint for a copy of the workspace (a mirror, a merge) to know which pages to look at; the page's own `seq` / `GET /pages/{id}/ops` is the truth, and the consumer must be idempotent. Any member (viewers too); no share tokens |
| WS | `/ws/page/{id}[?ws=&share=&client=]` | the page's live channel: `hello` / `join` / `leave` / `cursor` presence, every applied `ops` batch (with the writer's `cursor` when the batch carried one), `reload`; the client only ever sends `cursor`. Auth like HTTP (session cookie + `?ws=` (else the default workspace) or share token, resolved in the handler — the middleware doesn't run for websockets); viewers join too |

### Pages (`pages.py`) — page first, PDF as an action on it
| Method | Path | Purpose |
|---|---|---|
| POST | `/pages` | create a text-only root page: body `{title?, folder?, id?, properties?}` (title defaults to `Untitled`, `folder` → `properties.folder`; `id` keeps a page's id when a mirror brings it over — 400 when malformed, 409 when taken; `properties` seeds the page's own) → the block dict. On a share host, 402 `{detail, limit, used, plan}` when the owner's plan allows no more pages in their default personal workspace ([mirror.md](mirror.md) "Publishing") |
| POST | `/pages/by-docs` | which pages these stored files became: body `{doc_ids: [<hash>, ...]}` (≤500) → `{pages: {hash: {id, title}}}` — a hash matches the page carrying it as its PDF (`doc_id`) or the note page imported from it (a markdown upload's `markdown_import`); hashes with no page absent; any member. The file chips ask once per page render for their "open page" button and the menu's "Open page" / "Add to library" |
| POST | `/pages/from-file` | "Add to library" on a markdown file chip: body `{filename: "<hash>.md", original?, folder?}` → `{page, created, imported?}` — the stored upload becomes a note page through the `/import/markdown` importer (title from front matter, else `original` minus its extension), filed in `folder`; idempotent (a page whose `markdown_import` is the hash is returned with `created: false`); the file is untouched, the page is a copy. 400 for anything but a stored markdown name, 404 when the file is not in the workspace; workspace editors |
| POST | `/pages/{page_id}/attachment` | attach a PDF to a page that has none: body `{doc_id?, source_url?, original_filename?}` (at least one of `doc_id`/`source_url`; `doc_id` is shape-validated only — a URL-opened PDF's id is the URL hash and the proxy fetches it lazily, like `by-doc`; `source_url` defaults to `/api/uploads/<doc_id>.pdf`). While the title is still automatic (`Untitled`/empty) it becomes the file name / URL tail and is marked `auto_title`. → the updated block. 400 bad input / not a root page, 404 unknown page, 409 `{"detail": "page already has an attachment"}`, 409 `{"detail": "attachment belongs to another page", "page_id"}` |
| DELETE | `/pages/{page_id}/attachment` | drop `doc_id`/`source_url`/`original_filename` (highlights keep their `pdf_position`; the orphan sweep deletes the file unless another page references it) → `{ok, block, removed_uploads}`; 404 when the page has no attachment |

The writers need a workspace editor (`require_ws(write=True)`): a share token
never creates pages or touches a page's attachment. `GET /pages/{id}/export*` live
in `export.py`.

### PDFs & uploads (`pdf.py`, `uploads.py`, `shares.py`)

Publisher connections use `routers/publisher_sessions.py` and require a personal
account; guest and share-token access is rejected.

| Method | Endpoint | Behavior |
|---|---|---|
| GET | `/publisher-sessions` | Connection metadata and supported `publisher_roots`; never cookie values |
| POST | `/publisher-sessions` | Save `{host, cookies}` for the signed-in account; requires HTTPS or localhost, JSON, and a body of at most 256 KiB |
| DELETE | `/publisher-sessions/{host}` | Disconnect that account's host; returns `{ok: true}` |

See [publisher sessions](paper_metadata.md#connected-publisher-sessions) for
encryption, expiry and request scoping. The PDF endpoints below use the same
guarded fetch path.

| Method | Path | Purpose |
|---|---|---|
| POST | `/resolve-pdf` | URL/arXiv/DOI → fetchable PDF (citation_pdf_url sniffing, Unpaywall OA fallback) |
| GET | `/pdf` | proxy/download a PDF (`save=1` caches it server-side); a copy already cached answers 302 to `/uploads/<id>.pdf`, carrying the request's `share` / `ws` query so the browser's follow-up stays in the same library |
| POST | `/uploads`, `/upload-image` | store a PDF / an image (content-hash names, dedup'd; quota-gated) |
| POST | `/upload-file` | store a file for a block to reference as `[name](/api/uploads/<hash>.<ext>)` — the file chip. Any extension except executables (`storage.BLOCKED_EXTENSIONS`: exe, msi, bat, dll, ps1, …; 400 "not accepted (executable)"); the extension comes from the uploaded name, lowercased, `.bin` when there is none; images route like `/upload-image`, a `.pdf` must be a real PDF and lands under the same `<hash>.pdf` the PDF ingest mints (so it can be opened as a document page later); same hashing + limits → `{url, name, size, already_existed}` |
| POST | `/upload-ink` | store a handwriting group's `gamma-ink` JSON (the request body; validated against `gamma/ink.py`'s schema and limits, canonical bytes so identical strokes dedup) as `<hash>.ink` → `{url, size, strokes, bbox, pdf_position, already_existed}`; editors and edit shares. [handwriting.md](handwriting.md) |
| GET | `/pdf-info/{doc_id}` | the document manifest the viewer lays a PDF out from before pdf.js has parsed it (`gamma/pdf_meta.py`, [pdf_loading.md](pdf_loading.md)): `{doc_id, bytes, pages, dims: [[w, h], …]}` in PDF points, rotation applied; same access rule as the file; computed in pdfium on first request when the upload-time background walk has not run (`pages: 0` for an unreadable file, not cached); 400 malformed id, 404 no such file |
| GET, HEAD | `/uploads/{filename}` | serve stored files (HEAD: the headers alone, which is how the viewer learns a file's size before choosing its transport); with their media type (`storage.FILE_MEDIA_TYPES`, else `application/octet-stream`); pdf / images / txt / md render inline, everything else is `Content-Disposition: attachment` (html additionally sandboxed like svg); blocked or malformed extensions 400 |
| GET | `/quota` | the limits that apply to uploads into the request's workspace — the account's for a personal one (`used_bytes` = all its personal workspaces), the workspace's own for a shared one — with `workspace_bytes` and `account` (the person, or "") |
| POST | `/share/folder?name=` | create the FOLDER's share link (`name` a folder-label path; same defaults and optional body as a page's) or return the existing one unchanged; 400 for an empty path, 404 when no page is filed in the folder; workspace editors and owners |
| GET/PUT/DELETE | `/share-settings/folder?name=` | the folder share's settings (`{token: null}` when unshared; any member) / changes / stop — exactly like a page's; the share follows folder renames through `/folders/rename` |
| POST | `/share/{page_id}` | create the page's share link (defaults `anyone`/`view`; optional body `{audience, role, users}` applies to a NEW link) or return the existing one unchanged — root blocks only (400 otherwise); workspace editors and owners |
| GET/PUT/DELETE | `/share-settings/{page_id}` | read settings (`{token: null}` when unshared; any member) / change `audience`, `role`, `users` (`["carol"]` or `[{name, role}]`; validated: unknown usernames or roles → 400; the token stays; `edit`+`anyone` is allowed — see "Link visitors" above) / stop sharing (the token dies) — editors and owners |
| GET | `/share/{token}` | resolve a link for this viewer → `{page_id, folder, username (who shared it), workspace_id, audience, role, can_edit, viewer, viewer_is_guest}` plus, for a page share, `doc_id` (the page's PDF attachment id via `page_attachment`, `""` without one); a folder share's listing is `GET /blocks/root/children` through the token; `viewer`/`viewer_is_guest` let the share view offer "Open in my library" or "Add to my library"; 404 unknown, 401 sign in first, 403 signed in but not allowed |

### Search (`search.py`, `gamma/block_index.py`, `gamma/pdf_index.py`)
| Method | Path | Purpose |
|---|---|---|
| GET | `/search?q=&limit=&scope=` | one search over the knowledge base: notes (`block_fts`) + PDF text (`pdf_fts`). `scope` = `""` (library) or a folder path (that folder and its subfolders). → `{"results": [...], "indexing": n}`; results are notes hits first (bm25 order) then PDF hits, each capped at `limit` (default 20, max 100). A notes hit is `{"source": "notes", "block_id", "page_id", "title", "snippet"}` (`block_id` = the matched block, `page_id` its page root, `title` the page's); a PDF hit is `{"source": "pdf", "block_id", "page_id", "doc_id", "title", "page", "snippet"}` (`block_id` = `page_id` = the page carrying the PDF, `page` the 1-based PDF page). `indexing` = note pages still waiting for a rebuild batch + PDFs the background extractor hasn't reached. Owner-only |
| GET | `/pdf-search` | the PDF-only predecessor (same `pdf_fts` index; hits `{block_id, doc_id, title, page, snippet}`) — the Ctrl+F panel's library group still uses it (with `/block-search` for notes: fuzzy/regex + flags that FTS does not offer) |
| POST | `/search-reindex` | full rebuild (PDF text re-extracted in the background, every note page stamped stale for the next search), or just `doc_ids` from the body |
| GET | `/tasks` | background task progress (indexing, downloads) |
| DELETE | `/tasks/indexing` | stop the workspace's running indexer after the current paper (`{cancelled}`); the skipped papers stay stale and index on the next search; editors and owners |

The notes index is rebuilt lazily per page: a search first refreshes every
page whose `block_fts_meta` row is missing, older than `textnorm.INDEX_VERSION`,
or no longer matches the page root's `updated_at`; the block writers that
change a child without touching the root (`POST /blocks`, `PUT /blocks/{id}`,
`DELETE /blocks/{id}`, `PUT /blocks/{id}/children` on a nested block, a
re-parenting `reorder`) call `block_index.mark_page_dirty`.
Deleting a page or detaching its PDF prunes its rows (`block_index.purge_page_data`,
which also drops the `pdf_fts` rows of papers no page carries and the deleted
blocks' chats). The `pdf_fts` schema and its shared queries (`pdf_missing`,
`search_pdf`) live in `gamma/pdf_index.py`; extraction and the background
indexer in `search.py`.

### Link previews (`links.py`)
| Method | Path | Purpose |
|---|---|---|
| GET | `/link-preview?url=` | webpage title for the frontend's link chips (`{url, host, title}`); fetch goes through the SSRF guard, results cached in-process (TTL 24 h) |

### Browser extension (`clip.py`) — see [extension.md](extension.md)
| Method | Path | Purpose |
|---|---|---|
| POST | `/clip` | one-shot "save this page": dedup by DOI/arXiv/URL → resolve → fetch + store (`save_copy`) → page (`get_or_create_doc_page`) → folder/labels → metadata in a background thread. Body: `source_url, pdf_url, doi, arxiv_id, doc_id (pre-uploaded bytes), title, selection, folder, labels, allow_oa, save_copy`. Returns `{block_id, doc_id, title, existed, open_url, folder, labels, note?}`. **No PDF resolvable** (a plain web page, or a dead/HTML link) → a page titled from `title` (else the URL tail) with `properties.web_url = source_url` and the `selection` (if any) as its first `> quote — [title](url)` block; `doc_id` is `""` and `note` says so. Re-clipping that URL finds the page (`find_web_page`, by `web_url` on attachment-less pages), files it and appends the new selection. Only a request with nothing at all (no URL, title or selection) is a 400 |
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
| GET | `/metadata/status` | library-wide health table (feeds Settings → Library maintenance): every page with a PDF attachment plus pages carrying `properties.meta` without one (`has_file: false`); per paper `meta_source`, `meta_kind`, `meta_unverified` (null for pre-flag records) |

### AI (`ai.py`) — all config is GUI entries (each account's own plus the server's shared ones), no env API keys
| Method | Path | Purpose |
|---|---|---|
| POST | `/ai/chat` | chat; NDJSON stream of `{context}` (first line: per-page coverage — native/text, pages shown of total; `doc_id` `""` for a page without a PDF; the open paper's entry adds `selection: {passages: [{page, section, found, crop}]}` when passages were selected) then `{delta}`/`{action}`/`{progress}`/`{usage}`/`{error}`; `usage` is the provider's token report `{input, output, cache_read, cache_write}`, one line per provider turn (the client sums an agent reply's rounds; non-stream replies carry one summed `usage` field); `progress` previews an edit_block/create_block call still being written (target id + markdown so far). Context is `pages` (up to 7 page ids, de-duplicated; they also become the tool scope's `context_pages`) or `page_id` (one; its PDF attachment derived server-side; `doc_id` is accepted as a compatibility input and resolves to its page), plus model id, effort, images, files, the agent scope, the selected PDF passages `selections` (`[{text, page, box}]`, box `[x0, y0, x1, y1]` page fractions; the older `"---"`-joined `selection` string is still read), and the notes pointers `focus_block_id` (cursor block), `context_blocks` (attached block ids), `note_selections` (selected note text as exact source ranges `[{block_id, from, to, text}]`, what `edit_block` mode `"selection"` rewrites). See [ai.md](ai.md) |
| GET | `/ai/models` | model registry (each model carries `native_pdf`: whether its provider accepts the PDF file itself, and `shared`: it comes from a server entry, `server:<id>:<model>`) + default prompts (feeds the model switchers and prompt editor) + `allowance` (`{limit, used, exhausted}` for the shared entries, `limit` 0 = unlimited; null when none applies — [guests.md](guests.md)) |
| GET | `/ai/settings` | masked provider list (key hints only, each with its display `label`), then the server's shared entries the account may use as read-only rows (`shared: true`, key hint for admins only), plus the `protocols` and named `services` (e.g. DeepSeek) the add form offers |
| POST/PUT/DELETE | `/ai/providers[/{id}]` | manage the account's own provider entries (a shared `server:` id is a 404 here) |
| POST | `/ai/providers/{id}/test` | live probe of one credential (model: the entry's `test_model`, else the request's `model` — the client sends its metadata model — else the first model); failures carry an `auth` flag for expired/rejected credentials. Admins may name a shared entry (`server:<id>`) |
| POST | `/ai/providers/{id}/usage` | ChatGPT subscription allowance windows; explicitly unavailable for generic API-key providers; an expired sign-in returns `{available: false, auth: true}` in-body |
| GET | `/ai/usage` | the account's token usage as the providers reported it: `windows` (today / week / month / all → calls + the four counts), the 30-day split by `kinds` and by `models`, plus the shared `allowance` object; see [ai.md](ai.md) "Token usage" |
| DELETE | `/ai/usage` | forget the account's usage rows (the shared-entry rows of the last 24 h stay: the allowance still counts them) |
| POST | `/ai/health` | login connection check of one entry the account can use, its own or shared (`{provider_id, mode}`; `""` = the first): `mode: "ping"` is the free credential check (OAuth → usage endpoint, API key → `/v1/models`), `"test"` the tiny live completion; always answers in-body `{configured, ok, auth?, error?}` |
| POST | `/ai/model-catalog` | list models available to a credential: the typed key, or a saved entry's (`provider_id`; admins may name a shared `server:<id>`) |
| GET | `/ai/context-window?model=<pid>:<model>` | the chat model's context window for the context ring: `{model, context_window, source: "provider" \| "models.dev"}`, from the entry's own model listing, else the models.dev catalog; `context_window` null when neither knows it (`""` = the default model) |
| POST | `/ai/oauth/chatgpt/start`, `/complete` | ChatGPT OAuth (PKCE, pasted callback URL) |
| POST | `/ai/transcribe` | voice dictation |
| POST | `/ai/translate` | translate paragraph texts for the viewer's translated view (`{texts, lang, model, effort, stream}` → `{translations}`; with `stream: true` an NDJSON stream of `{i: [indices], text}` partials as each paragraph is written, then the same final object; in-memory per-paragraph cache). `model: "engine:google"` / `"engine:youdao"` translates with that machine-translation service instead — no AI provider needed, 503 when it isn't set up, never streams partials |
| GET | `/translate/engines` | the account's machine-translation services (`{engines: [{id, label, configured, needs_key, fields, updated_at, failing}], can_edit}`; secret fields as a `…last4` hint; `microsoft` needs no key and is always configured, and its `failing` is `{since, error}` during a failure streak, else null) |
| PUT / DELETE | `/translate/engines/{id}` | set (`{fields: {…}}`, an empty secret keeps the stored one) or remove a service's credentials (400 for a service that needs no key); guests 403; answers the GET shape |
| POST | `/translate/engines/{id}/test` | translate one sentence into `{lang}` with the stored credentials; in-body `{ok, text}` / `{ok: false, error}` |
| GET | `/pdf-text-status` | whether a doc has extractable text |

### Chats (`chats.py`, prefix `/api/chats`)

`GET /chats/{page_id}?share=<token>` exposes only the shared page's active
saved conversation, subject to the link's audience. Shared pages show this in
a read-only AI chat window on desktop and mobile, with search and copy.
Chat mutations reject share tokens, including links that allow page editing;
archived conversation browsing remains session-only.
| Method | Path | Purpose |
|---|---|---|
| GET/PUT/DELETE | `/chats/{key:path}` | the ACTIVE conversation per bucket: page id, `home`, or `home:<folder>` (hence `:path`); GET → `{messages, title}`, PUT `{messages, title?}` (title omitted = keep) |
| GET | `/chat-history?bucket=` | the bucket's archived conversations, newest first (`{sessions: [{id, title, preview, count, created_at, updated_at}]}`) |
| POST | `/chat-history/archive` | "New chat": file `{bucket, messages, title}` into history and clear the active row (→ `{id}`, null when empty) |
| POST | `/chat-history/{id}/open` | make an entry the active conversation; the body's `{bucket, messages, title}` (the current one) is archived first (→ `{messages, title}`) |
| PUT/DELETE | `/chat-history/{id}` | rename (`{title}`) / delete an archived conversation |

### Import & export (`imports.py`, `export.py`)
| Method | Path | Purpose |
|---|---|---|
| POST | `/import/logseq` | Logseq .pdf + .edn import |
| POST | `/import/markdown` | UTF-8 `.md`/`.markdown` file → note page and nested blocks (optional `folder`; a front-matter `folder:` files it below that) |
| POST | `/import/markdown-zip` | zip of Markdown notes → one page per `.md` (multipart `file`, optional `folder` prefix): Obsidian vaults (wikilinks/embeds → mentions and synced blocks, `^id` anchors and headings as link targets, `tags` → labels, `aliases` kept, comments and fold markers dropped, `.obsidian/` skipped), Notion "Markdown & CSV" exports (subpage folders → folder labels, databases → table pages, links → mentions, images uploaded), Gamma Markdown / Obsidian exports (folder/source/meta/bibtex restored) or any zipped notes. Idempotent by file digest / `notion_id`; the report says `obsidian: true` for a vault |
| POST | `/markdown-blocks` | parse markdown text into a `{content, children}` tree without storing anything (the editor's paste-as-blocks helper; same parser as `/import/markdown`, 5 MB cap) |
| POST | `/import/pdf-annotations` | import annotations embedded in the PDF (idempotent; optional `strip`) |
| POST | `/import/zotero/preview` | Read-only import plan (multipart `file`, optional `folder` prefix; workspace writer). Archive entries, destination pages with PDF/page and create/merge status, folder paths, attachment warnings. Uses the same planner as import; stores no pages or files |
| POST | `/import/review` | Shared staged upload/review for `zotero`, `markdown-zip`, `markdown-file`, `gamma` (multipart `file`, `source`, optional `folder`, `strip`). Returns `review_id`, archive entries, destinations, source selection IDs and warnings; writes no library content |
| POST | `/import/review/{id}` | Import the staged file with JSON `{selected: [source IDs]}`. Account/workspace bound, workspace write access rechecked, concurrent commit blocked, completed retries return the saved report; no second upload |
| DELETE | `/import/review/{id}` | Discard the staged upload/review; running imports cannot be discarded |
| POST | `/import/markdown-zip/preview`, `/import/markdown-file/preview`, `/import/gamma/preview` | Format-specific read-only review adapters. Markdown single-file review shares the ZIP engine. Gamma requires a non-guest workspace |
| POST | `/import/markdown-file`, `/import/gamma` | Reviewed single-note or additive Gamma import; multipart `file`, `selected` JSON source IDs (required for Gamma). ZIP Markdown/Zotero imports also accept optional `selected` (omitted = all, empty = none) |
| POST | `/import/zotero` | Zotero library import: zip of a "Zotero RDF" export (multipart `file`; `strip`, optional `folder` prefix). Items and standalone/additional PDFs→pages+metadata, collections→folders, tags→labels, notes→blocks; embedded annotations via the same importer. Idempotent by file hash / `zotero_key`; returns page destinations and warnings |
| GET | `/pages/{id}/export` | page export (`?mode=readable|obsidian|notes-pdf|logseq-graph|zotero-rdf|gamma` + `highlights=&notes=&pdf=`); `obsidian` = a vault zip (`<folder>/<Title>.md`, wikilinks, `attachments/`, `.obsidian/app.json`); `notes-pdf` = the notes typeset as their own PDF (works without a paper); `gamma` = scoped backup for `/import-data?mode=merge` |
| GET | `/pages/{id}/export-pdf` | the page's own PDF with annotations written back (`?highlights=&notes=`) |
| POST | `/folders/rename` | follow a folder rename/move/delete for what names a folder by its path — the per-folder chat buckets (active + history, `chats.move_folder_buckets`) and folder shares (`shares.move_folder_shares`; one already at the destination wins): `{src, dst}`, dst `""` deletes → `{ok, moved, history_moved, shares_moved}`; workspace editors, never through a share link (`gamma/routers/folders.py`) |
| GET | `/folders/export` | whole-folder export, same modes/flags (`?name=` + `mode=`); subfolders become Zotero collections or vault directories, `notes-pdf` one PDF for the whole folder |
| GET | `/folders/export-progress` | per-page progress of a running folder export (`{active, total, done, title}`) |

### Prefs (`prefs.py`)
| Method | Path | Purpose |
|---|---|---|
| GET/PUT | `/prefs/{key}` | small synced JSON KV per account: `open-tabs`, `recent-views`, `pinned-folders`, `read-pos` are stored per workspace (the request's), `profile` / `ai-provider` account-wide (`db.USER_PREF_KEYS`); `profile` is the web app's account-scoped settings as one object keyed by preference name (400 unless an object; `db.get_profile` / `db.set_profile`, [settings.md](settings.md)); reading `profile` first syncs it with Gamma Cloud when the last sync is over a minute old, and its answer carries `cloud_choice` (a first sync waits for the person's choice); values over 64 KB get 413; refuses the reserved `ai-settings`, `translate-engines` and `profile-base` keys |
| PATCH | `/prefs/profile` | `{set: {name: value}}`: sets those entries of the profile and keeps every other one as stored (`db.patch_profile`) — how the web app saves, so a tab's stale copy of an entry it did not touch never undoes one synced from elsewhere; answers `{key, value, updated_at}` with the whole profile; 413 over 64 KB |
| GET | `/page-snaps` | all recents-card cover thumbnails `{snaps: {pageId: {img, at}}}`; `?after=<iso>` returns only newer ones (the focus-pull delta) |
| PUT | `/page-snaps/{page_id}` | store a cover (JPEG data URL body `{img, at}`; per-page newest-`at` wins, count-capped server-side) |
| DELETE | `/page-snaps/{page_id}` | drop a cover (the recents card's ×) |

### Notices (`notices.py`, `gamma/notices.py`) — see [settings.md](settings.md) "Notices"
| Method | Path | Purpose |
|---|---|---|
| GET | `/notices` | `{notices: [{id, fingerprint, tone, pane, title}]}` the account has not looked at yet, strongest `tone` (`info` / `warn` / `error`) first; `pane` is the Settings pane that resolves it. Sources: `update` and `log-errors` (admins: a newer GitHub release, errors logged since the last look), `backup-failed`, `mirror-conflicts`, `cloud-sync`, `storage` (everyone: a failed backup task, open conflicts in an owned clone, a failed Gamma Cloud sync, personal storage past 90 % or full) — the table in [settings.md](settings.md); guests and integration tokens get `[]`. Sync: the release check may hit the network when its cache is stale |
| POST | `/notices/{id}/seen` | `{fingerprint}` — the account has seen this version of the notice (kept in the account-wide `notices-seen` pref); it stays quiet until the fingerprint changes. 403 for guests and tokens, 400 for a malformed id or fingerprint |

### Integrations and MCP (`routers/integrations.py`, `mcp_oauth.py`, `mcp_server.py`) — see [mcp.md](mcp.md)
| Method | Path | Purpose |
|---|---|---|
| GET | `/integrations/tokens` | the current workspace's assistant connections (manual tokens and OAuth grants: id, name, dates), the MCP URL and whether browser sign-in is available; session only, never a guest |
| POST | `/integrations/tokens` | mint a manual token `{name, scope?: read \| write, expires_in_days?}` (shown once); at most 20 unexpired per account; `write` (a mirror's push credential, [mirror.md](mirror.md)) is refused to a viewer |
| DELETE | `/integrations/tokens/{id}` | revoke a connection |

A manual token (`gamma_…`, not an OAuth one) is also accepted on every `/api/*` route as `Authorization: Bearer` (`auth.py`): the request runs as the account behind it, in the token's workspace only (`?ws=` / the header may only repeat it — 403 otherwise), writes only with the `write` scope (403 "this token is read-only"), never as an admin, and never as a session that manages tokens or accounts (`require_personal_user` refuses it with 403).

### Mirrors (`routers/mirrors.py`, prefix `/api/mirrors`) — see [mirror.md](mirror.md)

| method | path | what |
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
| GET | `/mirrors/{ws}/conflicts[?resolved=1][&page=]` | the merges the engine decided on its own (kinds `merged`, `diverged`, `kept_local_edit`, `restored_remote_edit`, `page_restored`, `page_restored_from_remote`) |
| POST | `/mirrors/{ws}/conflicts/{id}` | `{choice: keep \| mine \| theirs}` — the text is written into the block's page as it is now, then the conflict is marked resolved; 409 (the conflict stays open) when the block refuses the write |

Session only, the mirror's owner, never a guest.

### Publishing (`routers/publish.py`, `gamma/publish.py`) — see [mirror.md](mirror.md) "Publishing"

| method | path | what |
|---|---|---|
| POST | `/pages/{id}/publish` | publish the page to the share host Gamma Cloud names: body `{audience?: anyone \| users \| list, role?: view \| edit}` (optional; the share there, default anyone / view, applied to a new or an existing link) → `{url: "<share host>/?share=<token>", public_url, share: {token, page_id, audience, role, users, created_by}, mirror: {ws, status, page_filter, mode, detached, conflicts_open, pending_local}}` — `public_url` the page's pretty address (`https://<username>-pages.gammapdf.com/<slug>-<id>`) when the share host reports a `page_host`, else the same as `url`. Adds the page to the workspace's filtered mirror of the share host (made on the first publication through `/auth/cloud/exchange` there), runs one round and makes the share. Workspace editors, session only, never a guest. 409 with a message when it cannot: no linked Gamma Cloud identity with a token ("Sign in with Gamma Cloud to publish."), no share host named, a workspace that is a copy of another server, a mirror owned by someone else, detached or receive-only, or this server is itself a share host, or the plan's cap there (the share host's words — "Free plan: up to 5 published pages. Unpublish one, or upgrade your Gamma Cloud plan." — plus `limit: {used, max, plan}`); 400 for a block that is not a page; 502 when the share host refused or the page did not reach it; 503 when the account server cannot be read |
| DELETE | `/pages/{id}/publish` | unpublish: the share there stops, the copy there is deleted, the page leaves the filter; the page here is untouched → `{published: false, mirror}`. 409 when the page is not published; 502 (nothing changed) when the share host cannot be reached |
| GET | `/pages/{id}/publish` | `{published, can_publish, reason?, url?, public_url?, share?, status?, mirror?, limit?, error?}` — `can_publish` / `reason` say whether publishing would be refused and why (the same messages as POST), `status` is the mirror's raw status (the pill's reading is the frontend's), `share` the live share settings there (`url` and `public_url` with them), `limit` `{used, max, plan}` read from the share host whenever the account holds a publishing token (`max` null = no cap), `error` when the share host could not be read. Any member |
| GET | `/publish/limit` | the share host's half: `{used, max, plan}` — the root pages of the request's workspace (a publishing mirror's token names it) and the cap its owner's plan puts on them (`max` null = none). 404 on a server that is not a share host. Nothing cached |
| GET | `/pages/resolve-public?host=&path=` | no auth: a page host's pretty address → `{share, page_id}`, the share token the share view opens with (audience and role its own). `host` must match `GAMMA_PAGE_HOST` (the username read out of it), `path` is `/<slug>-<id>` or `/<id>`; only the trailing id counts, a root page with a share in that account's default personal workspace. 404 otherwise (counted like an unknown share token); 429 past 120 per IP in 5 minutes |
| GET | `/integrations/oauth/request?request_id=` | the pending consent (client name, the account's workspaces) for the consent screen |
| POST | `/integrations/oauth/consent` | approve or deny a pending sign-in for one workspace |
| GET | `/.well-known/oauth-authorization-server`, `/.well-known/oauth-protected-resource` | OAuth discovery for MCP clients (no `/api` prefix) |
| POST | `/oauth/register`; GET `/oauth/authorize`; POST `/oauth/token` | dynamic client registration, the authorization redirect, the PKCE code exchange (no `/api` prefix) |
| POST | `/mcp` | the Streamable HTTP MCP endpoint (bearer token or OAuth access token; no `/api` prefix, browser origins refused) |

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
| PUT/DELETE | `/admin/users/{name}` | password, admin flag, storage overrides / delete through `workspaces.delete_account` (+ the workspaces only they owned, listed as `deleted_workspaces`; guest accounts too) |
| POST | `/admin/users/{name}/rename` | rename (rows only — no files move; sessions survive) |
| GET | `/admin/workspaces` | every workspace (kind, access, public role, quota, `personal` = its account or "", `default`, members, upload size), plus orphan directories — Settings → Workspaces |
| GET/POST | `/admin/backups` | list the whole-data-directory snapshots under `backups/` / take one now (`{label?, uploads?}` — databases, plus every upload with `uploads: true`); per-workspace snapshots are `/workspaces/{id}/backups` |
| GET | `/admin/backups/{name}/download` | the snapshot as a zip |
| DELETE | `/admin/backups/{name}` | delete a snapshot (restoring is `manage.py backups --restore`, server stopped — [migrations.md](migrations.md)) |
| GET/PUT | `/admin/settings` | server-wide storage defaults, plus `public_url` / `public_url_source` (the admin-confirmed public server URL, [mcp.md](mcp.md)), `guest_ttl_hours` / `guest_ttl_source` (1–720 hours, `guest_ttl_hours_range`; `environment` when `GAMMA_GUEST_TTL_HOURS` decides) and `demo_mode` / `demo_mode_source` (`environment` when `GAMMA_DEMO` is on) — a PUT of either is 400 while the environment decides ([guests.md](guests.md)) — and `cloud` (the cloud sign-in settings, written as `cloud_issuer`, `cloud_client_id`, `cloud_client_secret`, `cloud_policy`, `cloud_share_host` — [cloud_accounts.md](cloud_accounts.md)) |
| GET | `/admin/logs?after=<seq>` | scrubbed in-memory server log |
| GET/PUT | `/admin/ai-providers` | the server's shared AI entries, masked like `/ai/settings` (key hint, never the key; API-key `protocols` and `services` only), `guests` and `allowance` (`{accounts, guests}`: tokens per account per rolling 24 h, 0 = unlimited); PUT `{guests?, allowance?: {accounts?, guests?}}` (whole numbers 0..10^9, else 400). Chat, translate, metadata fetch/cite and transcribe answer 429 with a human `detail` once an account's allowance is used up; streams end with `{error: detail}` |
| POST/PUT/DELETE | `/admin/ai-providers[/{id}]` | add / edit / remove a shared entry (the `/ai/providers` fields and validation, at most 20; a POST takes API-key protocols only; ids are `server:<id>`; the key is write-only and stored encrypted). Admin session only: an integration token is refused. Test, model listing and a sign-in's usage go through `/ai/providers/{id}/test`, `/ai/model-catalog` and `/ai/providers/{id}/usage` |
| POST | `/admin/ai-providers/chatgpt/start` · `/admin/ai-providers/chatgpt/complete` | a shared ChatGPT subscription: the `/ai/oauth/chatgpt/*` flow (same bodies) for the server's list — `complete` adds a shared sign-in entry, or with `provider_id` reconnects one, and returns the shared view; the tokens are stored encrypted; a state from one flow never redeems on the other. Admin session only |
| GET | `/admin/server-info?refresh=` | the Server dashboard (`gamma/version.py`): `version` / `commit` / `label` (from `GAMMA_VERSION` / `GAMMA_COMMIT` — the Docker build and the desktop shell set them; a checkout is a "development build"), `started_at`, `uptime_seconds`, `python`, `platform`, `schema_version`, `frozen`, `log_counts` `{info, warning, error}` since startup, `latest` (`{version, url, published_at}` from the GitHub Releases API, cached six hours, ten minutes after a failure, `refresh=1` refetches; `GAMMA_UPDATE_CHECK=off` disables) or `latest_error`, `update_available` (True/False, None without a version to compare), `image`, `releases_url`. Sync: it may hit the network |

Rails: a guest account takes storage limits and deletion but no password,
admin flag or new name; no self-delete; the last admin can't be demoted or
deleted.
