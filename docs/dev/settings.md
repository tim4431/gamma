# Settings

Where every setting lives, and how the Settings dialog is built.

## Where settings are stored

| Layer | Storage | Examples |
|---|---|---|
| Per browser | `localStorage`, one `gamma-*` key per preference, all declared in `PREFS` ([frontend/src/app/prefDefs.js](../../frontend/src/app/prefDefs.js)) with scope `browser` — except `gamma-link-name`, the share view's display name for a visitor without an account, owned by `src/collaboration/linkName.js` because the fetch wrapper reads it outside React | what describes this device: the interface size (`gamma-ui-scale`, applied pre-paint by `index.html`), the status bar, the handwriting input rules and the tool strip's presets, eraser and lasso choices (`gamma-ink-*`), the metadata and translation model picks and dictation (they name this server's provider entries, like the chat model `gamma-chat-model`); outside `PREFS`, diagnostics tracing (`gamma-debug-log`) |
| Per account, profile | the account-wide `profile` prefs key (`/api/prefs/profile`, one JSON object keyed by preference name), every `PREFS` entry with scope `account`; each also keeps its `gamma-*` localStorage key as the instant-paint cache | appearance (theme — the pre-paint script still reads `gamma-theme` — flip page colors, and whether tours are suggested, `suggestTours`, [onboarding.md](onboarding.md)), the interface language (`gamma-language`, read by `main.jsx` before the first render, [i18n.md](i18n.md)), reading and editing (imported annotations, translation button and language, Enter key, how search opens), library display and PDF fetching, chat behaviour (tools switch, per-kind tool permissions, reasoning effort, the login connection check, tool limits, snapshot clearing), translation effort and parallel requests, context budgets, prompts, keyboard shortcuts (`keybindings`: command id → chord or null, [hotkeys.md](hotkeys.md)) |
| Session only | React state, nothing stored | the Ctrl+scroll text size of the notes list and the chat transcript (`useTextScale` in [Widgets.jsx](../../frontend/src/shared/ui/Widgets.jsx)) — resets on reload |
| Per account, synced | `/api/prefs/{key}` (small JSON KV, `user_prefs` in `users.db`) | per account AND workspace: open tabs (`open-tabs`), the recently-viewed queue (`recent-views`), pinned folders (`pinned-folders`; pinned pages are a page property), reading positions (`read-pos`) — they name one workspace's pages; account-wide: active AI key (`ai-provider`) and the preference profile (`profile`, previous row). Server wins on load, localStorage (keyed `user@workspace`) is the instant-paint cache. The recents-card cover thumbnails are workspace data, through their own `/api/page-snaps` store (`page_snaps` in the workspace's `data.db` — over the prefs size cap) |
| Per account, seen notices | the account-wide `notices-seen` prefs key (`db.NOTICES_SEEN_PREF_KEY`), `{notice id: fingerprint}`, written only by `POST /api/notices/{id}/seen` (below, "Notices") | which release and which log error the account has already looked at |
| Per account, server-only | AI provider entries (keys/OAuth tokens) under the reserved `ai-settings` prefs key (account-wide), managed via `/api/ai/providers*`; the browser only ever sees a masked hint. The server's shared entries (next row) are listed after them read-only. Machine-translation keys live the same way under the reserved `translate-engines` key (`/api/translate/engines*`, [ai.md](ai.md) "PDF translation") | API keys, ChatGPT OAuth, Google / Youdao translation keys |
| Per workspace | `workspaces` / `workspace_members` in `users.db`, via `/api/workspaces*` ([workspaces.md](workspaces.md)) | name, kind (personal / shared), members and roles, access (private / public + the public role) and a shared workspace's own quota (admins), the account's default workspace, which workspace this tab works in (`?ws=` in the URL, `gamma-last-ws:<user>` remembers the last one) |
| Server-wide (admin) | `settings` KV in `users.db` via `GET/PUT /api/admin/settings`, plus nullable per-user override columns; the shared AI entries under the `ai_providers` key via `/api/admin/ai-providers*` (keys encrypted with the data directory's key, like the cloud client secret) | default max upload size, default storage quota, public URL, cloud sign-in (and whether this server is the share host), how long guest workspaces last (`guest_ttl_hours`) and demo mode (`demo_mode`, [guests.md](guests.md)), shared AI provider entries, whether guests may use them and the shared AI allowance per account / per guest |

Adding a preference = one entry in `PREFS` (key, scope, default, and a codec
if the value needs validation) plus a control in the matching settings pane;
`useAppPrefs()` ([prefs.js](../../frontend/src/app/prefs.js)) turns every
entry into state and a setter. Don't scatter `usePersistedState` calls
through App.jsx. Pick `account` unless the value only makes sense on this
device or names something that exists only on this server.

The profile syncs through `useProfileSync` in prefs.js. On sign-in the
server copy wins; an account without one is seeded from this browser.
Entries are validated with the same codecs as localStorage, so unknown names
and bad values are dropped and a partial profile applies what it has.
Changes push one second after they settle (a slider sends one request) as
a `PATCH /api/prefs/profile` carrying only the entries that changed since
the copy this tab last saw, so a tab's stale value of another entry never
undoes a change synced in meanwhile; a pending push is flushed when the
page is hidden, and focus pulls at most every 15 seconds. Nothing is pushed
before the first load succeeds. Signed-out visitors, guests and share
views keep working from localStorage alone. The profile
carries no secrets, and an account linked to Gamma Cloud carries it to
every Gamma server it signs in to, VS Code style: merged preference by
preference against the copy both sides last agreed on, synced on a cloud
sign-in, when a browser reads the profile (at most once a minute), a few
seconds after a change and every hour; a first sync of two different
copies opens a dialog asking Fetch from cloud / Push to cloud, and Settings
→ Account & sync → Settings sync has one Sync now button
(`gamma/cloud_sync.py`, [cloud_accounts.md](cloud_accounts.md)). The
provider entries and the active key never join it. `PUT /api/prefs/profile`
(whole-object replace, kept for scripts and tests) requires an object;
both share the prefs cap of 64 KB, enough for four long custom prompts. Step 17 of the migrations turned the old `appearance` key into the
profile's first two entries.

Each account section's tag shows where its own settings stand. A section
names the preferences it holds: `Section`'s `prefs`, taken pane by pane
from `SECTION_PREFS` in
[settings/sectionPrefs.js](../../frontend/src/settings/sectionPrefs.js).
`accountPrefs` there throws on a name that is not an account-scoped entry
of `PREFS`. `tests/sectionPrefs.test.mjs` checks the table against `PREFS`
and against every `scope="account"` in the panes' sources. It also checks
that every account preference is held by a section or listed in
`UNTAGGED_PREFS`, which is empty today. A browser preference inside an
account section carries its own tag on its row (`Row`'s `scope="browser"`):
"Translate with" in Reading › Translation is the one case.

`useProfileSync` returns its overall state (signed-out / loading
/ loaded / pending / pushing / failed) and, as sets of preference names:
`pending` (the value differs from the copy the server last confirmed),
`inflight` (sent in the PUT now on its way), `failed` (a push of exactly
this value failed; changing it again makes it pending) and `awaitingCloud`
(pushed since Gamma Cloud last reported the profile synced). The dialog
adds `GET /api/auth/cloud/sync-status`, which is account-wide and polled
while the dialog is open (every 15 seconds, 6 seconds while the server's
push to Gamma Cloud is due, and again once a local change is saved), and
passes both through `SettingsSyncContext`. Every answer also goes to the
hook's `noteCloud`, which clears `awaitingCloud` once the cloud reports
synced at a time after the last push (the PATCH's `updated_at`).
`profileSyncState(local, cloud, names)` in
[settings/syncState.js](../../frontend/src/settings/syncState.js) reads
them for one section, first rule that applies:

| state | tag | hover |
|---|---|---|
| signed out, guest, share view | monitor + browser | kept in this browser only |
| one of the section's names pending or in flight | spinning refresh + account | saving these settings to your account |
| one of them failed to save on this server | warning + account, in red | the error |
| first load, or the server has not answered yet | account | saved with your account on this server |
| no Gamma Cloud identity (or its grant is gone) | check + account | "Saved on this server. Link a Gamma Cloud account to carry these settings to other servers." |
| a first sync waits for the person's choice | warning + account | "Saved on this server. Not synced with Gamma Cloud until you choose which settings to keep, in Account & sync." |
| one of them awaiting the cloud, and the cloud failed | warning + account, in red | the cloud's error |
| otherwise, linked | cloud-check + account | "Synced with Gamma Cloud at <time>" |

So changing the Enter key spins only the Notes section, for the second the
change settles plus its PATCH (and at least 700 ms, so a quick save is seen
rather than flickered). The server's acceptance is the commit: its own
push to Gamma Cloud is coalesced (5 s) and retried by the hourly check, and
the tag never waits on it — the cloud's pending state is not shown, only a
failure of that hop. A cloud error from before any change this
session shows on no section; the Account pane's Settings sync row (under
the Gamma Cloud row, `CloudSyncRow` in `settings/SettingsCloudSignIn.jsx`)
still says it (`cloudSyncHint` in the same module: "Synced with Gamma
Cloud at 14:37" / "Not synced: <error>"), next to its one Sync now button,
which, while a first sync waits, opens the Fetch from cloud / Push to cloud
dialog. Browser sections always show the monitor
and "browser".

The tag is an icon and one muted word ("account" or "browser") in the
small caption size; the sentence is its hover `title` and its
`aria-label`, and `data-sync` carries the state for tests.

The last open page and viewer layout use `app/sessionState.js`, separately from
synced preferences. Its key is `gamma-session:<user>@<workspace>`. Reads wait
for workspace selection; changing scope cancels pending saves. Old unscoped
session caches are ignored because their account owner cannot be determined.

## Periodic backup tasks

Settings → Backups opens with one task table (`BackupTasks.jsx`). A task
names the workspaces it backs up (all the account owns, or a chosen set),
whether uploads are included, a schedule and a retention rule. Several tasks
may cover one workspace.

The editor (a `SubDialog`) offers Hourly, Daily, Weekly (chosen weekdays),
Monthly (a day of the month) and a five-field cron expression. Schedules are
UTC; the next three runs are previewed in the browser's time zone. Retention
is an age (days, weeks or 30-day months) or a snapshot count per workspace.
Only that task's snapshots expire, after a successful run; the newest is
always kept. Deleting a task keeps its snapshots.

`GET/POST /api/backup-tasks`, `PUT/DELETE /api/backup-tasks/{id}`,
`POST /api/backup-tasks/{id}/run` and `POST /api/backup-tasks/preview`.
Guests and integration tokens cannot manage tasks.

Runtime and storage: [docs/dev/workspaces.md](workspaces.md#backups).

## Notices: the red dot

Something that wants a look once — a newer Gamma release, errors in the
server log — is a *notice* (`gamma/notices.py`): `{id, fingerprint, tone,
pane, title}`, where `pane` is the Settings pane that shows it and
`fingerprint` names what changed (the release version; the server start
time plus the newest error's seq). "Resolved" means the account has seen
that fingerprint, kept in the `notices-seen` pref; a new release or a fresh
error changes the fingerprint and the notice is back by itself. Nothing is
dismissed for good, and nothing is per browser.

Sources are functions `fn(username)` registered with `@source` in
`gamma/notices.py`; each returns a Notice or None and must be a cached,
in-memory or small database read, because `GET /api/notices` runs them on
every poll. Admin-only sources are skipped for members; guests, share views
and integration tokens get an empty list. The sources:

| id | who | pane | tone | fires when | fingerprint |
|---|---|---|---|---|---|
| `update` | admins | Server | warn | a newer GitHub release than this build (`version.latest_release`, six-hour cache; the endpoint is sync on purpose) | the release version |
| `log-errors` | admins | Server | error | an error was logged since the last look (`logbuf.last_seq("error")`) | server start time + the newest error's seq |
| `backup-failed` | everyone | Backups | error | a backup task of the account is in state `failed` (`backup_schedule.list_tasks`) | each failed task's id + its last run |
| `mirror-conflicts` | everyone | Account | warn | a clone the account owns has open sync conflicts (`sync_engine.open_conflict_mark`) | a digest of, per clone, the count + the newest conflict id — resolving old ones never brings it back; a digest, so any number of clones fits the fingerprint's 200 characters |
| `publish-conflicts` | everyone | Account | warn | a workspace publishing pages to Gamma Cloud has open sync conflicts | per publication, as `mirror-conflicts` |
| `cloud-sync` | everyone | Account | warn | the account's Gamma Cloud sync is in its `error` state (`cloud_sync.profile_status`) | the failure's timestamp |
| `cloud-sync-choice` | everyone | Account | warn | the first settings sync with Gamma Cloud found two different copies and waits for Fetch from cloud / Push to cloud (state `choose`) | constant: seen once |
| `free-translate` | everyone who met the failures | Reading & editing | warn | Microsoft's free translation service failed `FREE_ALERT_AFTER` (3) times in a row, in memory (`translate_engines.free_failing`); one success ends it, and the Microsoft row names the error | the streak's start time |
| `storage` | everyone | Account | warn / error | personal storage past 90 % of the quota / full; only computed for an account under a quota, and the upload walk is remembered ten minutes (`notices.forget_usage`) | `90` / `full` |

Warnings in the log are deliberately not a notice (too noisy for a dot).

The frontend: `app/useNotices.js` (one instance in App.jsx) polls every
five minutes and on window focus, and `app/notices.js` (pure,
`tests/notices.test.mjs`) folds the list into the strongest `tone`, the
strongest tone per `pane`, and `firstPane`. The `.noticeDot` (red; accent
for an `info` notice) sits on the account button, after the account menu's
"Settings…", and after each dotted pane's sidebar button. "Settings…" opens
on `firstPane`; every other opener keeps its pane. Showing a pane calls
`markSeen(pane)`: its notices leave the list at once and each ack is posted
to `POST /api/notices/{id}/seen`, so other tabs and browsers agree on their
next poll. The browser flow is the last step of `e2e/scenarios/settings.mjs`,
with a faked feed and real acks.

## The Settings dialog

One dialog, one sidebar in three groups, defined by `PREFERENCE_NAV`,
`AI_NAV` and `MANAGEMENT_NAV` in
[SettingsDialog.jsx](../../frontend/src/settings/SettingsDialog.jsx). Every
pane is one click from any other; nothing opens a second dialog or a
"back" link. Panes carry no explanatory subtitle: a section rule's right-hand
tag (an icon plus "account" or "browser", `Section`'s `scope` prop, matching
the settings' scope in `PREFS`; an account tag's icon is the sync state of
that section's own settings, above) says where a setting lives, a row's
short hint what it does, and the hover `title` the rest.

Preferences:

- **Appearance** ([SettingsAppearance.jsx](../../frontend/src/settings/SettingsAppearance.jsx)):
  how things look. The eight theme cards (`PictureChoices`), the interface
  language (a `MenuSelect`: System / English / 中文, [i18n.md](i18n.md)), the
  dark-page switch with its live PDF sample, **Library**: the live card demo
  with the thumbnails / folders / labels switches
  ([SettingsLibraryDisplay.jsx](../../frontend/src/settings/SettingsLibraryDisplay.jsx)),
  interface size and the status bar, and last **Suggest tours**
  (`suggestTours`: off, no tour or hint is offered by itself,
  [onboarding.md](onboarding.md)). The old `library` pane id is an alias of
  this pane.
- **Reading & editing**: how papers and notes behave, one section per
  subject. **PDFs**: imported annotations (a Keep / Remove segmented choice),
  open-access fallback, metadata auto-fetch and saving external PDFs.
  **Handwriting**: two `IconChoices` tiles ("Draws with": pen only / pen and
  finger — the stored preference is still `inkPenOnly`) plus the
  stylus-draws-right-away and pressure switches. **Translation**, everything
  translation in one section
  ([SettingsTranslation.jsx](../../frontend/src/settings/SettingsTranslation.jsx)):
  the viewer's button and the language, the selection popup's translate
  button and translate-on-select, "Translate with" (a chat model or a set-up
  translation service; a browser pref, tagged on its row), the Microsoft
  (free) row with only Test (no key), the Google / Youdao credential rows
  with Test / Edit / remove, then translation effort (hidden while a
  translation service is picked, or with no AI connection) and parallel
  requests.
  **Notes**: the Enter key. **Search opens as**: on the home page and on a
  page (Full panel / Find bar).
- **Keyboard** ([SettingsKeyboard.jsx](../../frontend/src/settings/SettingsKeyboard.jsx),
  [hotkeys.md](hotkeys.md)): a filter box in the head, then **Shortcuts**
  (the one account section, "Reset all" as its action) listing every command
  of the catalog by group — each row a `KeyBinding`: the chord as key caps,
  click-then-press to rebind, Backspace unbinds, a reset button when it
  differs from the default, red caps and "Also used by …" when two commands
  share a chord — and **Built in**, the outliner's fixed keys read-only.
- **Account & sync** (pane id `account`; `sync` is an alias): the signed-in
  account's row, storage meter and Gamma Cloud link row.
  Under it, the sync sections
  ([SettingsSync.jsx](../../frontend/src/settings/SettingsSync.jsx)):
  **Publishing** lists the workspaces that publish pages to Gamma Cloud
  (`PublishingSection` in SettingsMirrors.jsx: the count of pages, the
  state, Conflicts when any wait, a "more" menu with Sync now and Stop
  publishing all; an empty state for the signed-in with nothing published).
  **Clones** (`MirrorsSection` in
  [SettingsMirrors.jsx](../../frontend/src/settings/SettingsMirrors.jsx))
  lists the account's mirrors of remote workspaces in git's words (each row:
  status line, Open, Sync or Reattach, Conflicts — the conflict cards, each
  resolved there or opened on its block — and a "more" `ActionMenu` with
  Force pull / Force push, Detach, Remove origin) and offers "Clone a remote
  workspace" (a `SubDialog`: origin server, write token, into a new or an
  existing workspace, name, direction) — [mirror.md](mirror.md). Both
  sections are hidden for the guest. The same state sits in the header as
  the sync pill (`collaboration/MirrorPopover.jsx`) while a clone is open;
  the clone's own settings (cadence, direction, force pull / push, detach /
  reattach, remove origin) live in that pill's gear view, stored on the
  server per mirror (`mirrors.poll_s`, `on_change`, `mode`). Last comes
  **Sync status**, the sync pill's scope (a `Segmented`: synced pages only,
  or every page of a workspace that syncs some — `syncPillScope`,
  [mirror.md](mirror.md) "Publishing").

AI:

- **Connections**: the provider list (empty state: one sentence and the Add
  button; the server's shared entries follow the account's own as read-only
  rows tagged "Shared by this server", selectable as the active key but
  without Test / Manage / delete), the login connection check, the models (default chat, metadata,
  dictation) and the account's token usage
  ([ai.md](ai.md) "Token usage"), which opens with a **Shared allowance**
  row ("12k of 50k tokens in the last 24 h", a red "used up" tag once
  spent) while a shared entry with an allowance applies
  ([guests.md](guests.md)). The check, models and usage sections appear only
  once a provider exists; a guest sees the usage too, without Reset.
- **Chat**: **Chat** (the default reasoning effort and the
  snapshot-clearing switch), then **Tools**: the master switch and, per chat
  kind (folder / PDF / notes), the tool chips (`AgentToolPicker`, the same
  `ToggleGroup` the chat header's settings popover shows for the open chat).
  No presets.
- **Advanced**: tool limits and the context budgets (the section's action
  is the Standard / Larger / Custom preset).
- **Prompts**: the accordion with one Cancel / Save pair.
- **Integrations** ([SettingsIntegrations.jsx](../../frontend/src/settings/SettingsIntegrations.jsx)):
  the workspace's assistant connections, the MCP URL, Claude Code connection,
  plugin setup and address-change commands, the Codex setup
  command and the manual-token fallback ([mcp.md](mcp.md)) — a token's
  scope is a `Segmented` (read-only for assistants, read and write for an
  offline copy on another Gamma, [mirror.md](mirror.md)). Each command box
  carries its copy button in its corner (a check for two seconds after a
  copy); each connection row shows how it signed in (browser sign-in — the
  `(OAuth)` suffix the provider mints — or token), its scope, the connected
  and expiry dates, and an icon-only Disconnect.

Manage:

- **Workspaces**: storage meter, personal and shared workspaces (each row:
  Open, a Data menu with export and import, Manage — an inline detail page;
  rename and invite are small editor dialogs), New workspace, Export all.
  The empty Shared section offers admins "New shared workspace" (a jump to
  Server). The account popover's "Workspaces…" opens this pane. Clones are
  not listed here (they are under Account & sync).
- **Backups** ([SettingsBackups.jsx](../../frontend/src/settings/SettingsBackups.jsx)):
  the task table first ([BackupTasks.jsx](../../frontend/src/settings/BackupTasks.jsx):
  Add task opens the editor `SubDialog`; each row has an Enabled switch and
  a Run now / Edit / Duplicate / Delete `ActionMenu`), then the server-kept
  snapshots per workspace (Back up all, and per workspace: back up now,
  download, restore, delete).
- **Library maintenance**: workspace storage, search-index rebuilding and
  the per-paper metadata / text / index health table.
- **Users** (admins): accounts, each with its personal workspaces and
  labelled Storage / Edit buttons.
- **Server** (admins): the dashboard (build, uptime, warnings, the update
  check), the public server URL, storage defaults (each box saves on Enter
  or blur), **Guests**, the shared AI provider, shared workspaces, server
  backups and the log with its level filter ([user_db.md](user_db.md)).

**Shared AI provider** (Server, `SettingsAi.jsx` `SharedAiProviderSettings`)
lists the server's shared connections with the same rows and the same
add/edit form as Connections (`ProviderRow`, `ProviderForm`; the form's
state comes from `useProviderEditor` over `/api/admin/ai-providers`
instead of App's aiKeys group). An API-key service, or a ChatGPT
subscription signed in with the account form's paste-the-callback steps
(`/api/admin/ai-providers/chatgpt/*`). Each row has Test, Manage and delete,
plus Usage (the subscription's windows) on a sign-in; "+ Add provider" is
the section's action. A "Guests may
use it" switch (default off) decides whether guests get them
([ai.md](ai.md) "Shared provider entries"). While at least one shared entry
exists, two `UnitInput` rows set the shared allowance, **Allowance per
account** and **Allowance per guest**: tokens per rolling 24 hours on the
shared entries, 0 = unlimited, each saved on Enter or blur as
`PUT /api/admin/ai-providers {allowance: {accounts | guests}}`
([guests.md](guests.md) "The shared AI allowance").

**Guests** (Server, [SettingsGuests.jsx](../../frontend/src/settings/SettingsGuests.jsx))
has two rows over `/api/admin/settings`: **Guest workspaces last** (a
`UnitInput` in hours, `guest_ttl_hours`, saved on Enter or blur) and the
**Demo mode** switch (`demo_mode`: the login page leads with Try the demo
and folds the password form behind Admin sign-in; the guide offers the
first-paper tour on arrival). Each reports its source like the public URL;
when it is `environment` (`GAMMA_GUEST_TTL_HOURS`, `GAMMA_DEMO`) the control
is disabled and the hint names the variable ([guests.md](guests.md)).
- **Diagnostics**: browser tracing, the browser session log and, under
  Help, the Report a problem button (the same dialog as the account menu's
  entry; [debugging.md](debugging.md) "Report a problem").

Administrators confirm the **Public server URL** under Server: the row shows
a "confirmed" / "not confirmed" tag and, while the address is unconfirmed or
edited, one Confirm button. It is prefilled from the browser origin but saved
only on confirmation; the saved address immediately configures assistant
sign-in and the MCP host allowlist and persists in the server `settings`
table. An existing `GAMMA_PUBLIC_URL` environment override is shown
read-only.

**Sign-in** (Server, `SettingsCloudSignIn.jsx` `CloudSignInSettings`) has
three rows. The account server's address (empty = off, an on/off `uiTag`).
The **server client**: how this Gamma identifies itself to the account
server. Empty on a local machine (the built-in public desktop client); a
hosted server enters the client id and write-only secret it was given. The
secret field shows once an id is typed; the inputs refuse browser autofill.
Unknown cloud accounts: a `Segmented` policy, Refuse / Claim / Provision.
Under Provision a fourth row, the Toggle *Accept published pages*
(`cloud_share_host`), makes this server the share host people publish pages
to; it also turns the guest account off and limits the account list to
exact names ([cloud_accounts.md](cloud_accounts.md) "The share host").
A share host's page cap per plan (`GAMMA_FREE_PAGE_LIMIT`) and its
per-account page hosts (`GAMMA_PAGE_HOST`) are environment only, with no
row here.
Editing shows one Save button as the section's action. Values are stored in
the server `settings` table (`cloud_*`), read-only when `GAMMA_CLOUD_ISSUER`
manages them. The login page reads `GET /api/server-config` and shows "Sign
in with Gamma Cloud" while it is on. The **Account** pane gets a "Gamma
Cloud" row (`CloudIdentityRow`): the linked username and plan with an Unlink
button, or a "Link Gamma Cloud account" button that round-trips through the
account server ([cloud_accounts.md](cloud_accounts.md)).

Search is backed by [settingsNavigation.js](../../frontend/src/settings/settingsNavigation.js).
It searches labels and synonyms, filters out inaccessible management pages,
then opens the destination, focusing the
matching `data-setting` element. Add an entry when adding a new setting.
Legacy pane names resolve through `resolveSettingsPane`; old notes, search,
viewer and context entry points also jump to their section.

The desktop surface has a persistent search header and labeled sidebar. On
phones the Back button opens a labeled category list, replacing the old strip
of unlabeled icons. All controls remain reachable by keyboard and touch.

Most preferences apply immediately, the server storage defaults included
(each box saves when it commits). Prompts and the public server URL use a
draft with Cancel / Save (Confirm). Credential, account and workspace editor
dialogs protect unsaved drafts on Cancel, Escape and backdrop dismissal.
`useSettingsDraft` registers dirty editors with the settings navigation
guard.

## Chat settings are global

The chat header shortcut edits the **same shared preferences** as Settings:
model, reasoning effort, single-paper context budget and tool permissions.
The Tools button and checkbox also edit the global `agentEnabled` preference;
there is no conversation-local tools override or reset on New chat.
Permissions remain scoped by chat kind (folder, PDF, notes), applying to all
chats of that kind. Both surfaces show the same tool chips per chat kind;
there are no presets.

Reasoning effort, the context budgets, the tools switch and the permissions
are account preferences: they live in the profile and follow the account to
every browser. The chat model stays with the browser, remembered per
provider entry; provider selection and credentials keep their own account
keys. Context presets change the three budgets together: Standard
is 60,000 / 6,000 / 120,000 characters, Larger doubles them, and Custom exposes
the exact values without changing them.

## Settings primitives

[SettingsKit.jsx](../../frontend/src/settings/SettingsKit.jsx) provides `PaneHead`,
`Section`, `Row`, `Toggle`, `SubDialog` and the shared controls.
Ordinary rows show a small icon, a label, a short hint and a control, with the
shared hover background. A section holds one subject; its settings stay in
it even when a few are stored differently (a row's own `scope` tag says so)
rather than splitting into a second section of the same subject. Put consequences in the visible
hint; supplementary `title` text appears on hover, without a Details toggle.
Use the existing shared controls: `PictureChoices` for illustrated choices,
`IconChoices` for a small exclusive set pictured as icon tiles (the share
popover's audience, handwriting's "Draws with"), `Segmented` for two or three
short words, `ToggleGroup` for independent chips.
Editor dialogs accept a `draft` value for dismissal protection. See
[ui-design.md](ui-design.md) for shared control styling.

## Verification

`npm test` covers permission presets, search visibility and legacy pane aliases.
After building, `npm run e2e -- --only settings` exercises the actual UI:
preferences and reload, management navigation, prompt/connection draft guards,
shared chat settings, mobile layout and administrator account separation.
Use `--keep` to retain desktop/mobile screenshots. For concurrent development,
build into a private directory and set `GAMMA_E2E_DIST` to that directory so
another build cannot replace the assets while the suite runs.

## Storage limits

Two limits per account: max upload size per file (`max_upload_mb`, default
50) and total uploads quota (`quota_mb`, 0 = unlimited). Server-wide defaults
are admin-editable in Settings → Server; per-account overrides (NULL =
inherit) in the Users pane. They apply to the account's personal workspaces
together; a shared workspace has its own optional quota (admins, Settings →
Workspaces / Members & sharing — [workspaces.md](workspaces.md)). `GET
/api/quota` reports the limits that apply to the current workspace and its
usage — it feeds the pre-upload size check and the shared `QuotaMeter` bar
(account popover, Library maintenance, Users rows, the workspace manager).
Uploads are hard-gated (413 over per-file, 507 over quota); best-effort
caches (proxy save, AI re-download) just skip saving when full. Dedup'd
files (same hash) are always allowed.
