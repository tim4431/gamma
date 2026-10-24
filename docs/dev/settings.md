# Settings

Where every setting lives, and how the Settings dialog is built.

## Where settings are stored

| Layer | Storage | Examples |
|---|---|---|
| Per browser | `localStorage`, one `gamma-*` key per preference, all declared in `PREFS` ([frontend/src/app/prefDefs.js](../../frontend/src/app/prefDefs.js)) with scope `browser` — except `gamma-link-name`, the share view's display name for a visitor without an account, owned by `src/collaboration/linkName.js` because the fetch wrapper reads it outside React | what describes this device: the interface size (`gamma-ui-scale`, applied pre-paint by `index.html`), the status bar, the handwriting input rules, finger undo, the hover preview, the tool strip's eraser and lasso choices and, in the iPad app, what the Apple Pencil's gestures do and its haptics (`gamma-ink-*`: `gamma-ink-pen-only`, `gamma-ink-auto-pen`, `gamma-ink-pressure`, `gamma-ink-finger-undo`, `gamma-ink-hover-preview`, `gamma-ink-eraser`, `gamma-ink-eraser-size`, `gamma-ink-lasso`, `gamma-ink-pencil-tap`, `gamma-ink-pencil-squeeze`, `gamma-ink-haptics`, the ruler's units `gamma-ink-ruler-units` and the zoom window's height `gamma-ink-zoom-height`; the presets themselves, `gamma-ink-tools`, and the Shapes tool's kind and fill are the account's), the style of new text boxes (`gamma-text-box-style`), whether Inbox items ring as system notifications (`gamma-inbox-notifications`, `inboxSystemNotifications`: the browser's permission is this device's; Settings → Appearance › Interface › Inbox notifications, [comments.md](comments.md) "The Inbox") and how a recording plays back (`gamma-replay-mode`, `gamma-replay-rate`, `gamma-replay-follow`; its lead-in `gamma-replay-lead-in` and the automatic transcript `gamma-recording-transcribe` are the account's), the metadata and translation model picks and dictation (they name this server's provider entries, like the chat model `gamma-chat-model`), and whether a shown "Linked from" section is folded (`gamma-backlinks-collapsed`; whether it is shown at all is the account's `gamma-backlinks`); outside `PREFS`, diagnostics tracing (`gamma-debug-log`) |
| Per account, profile | the account-wide `profile` prefs key (`/api/prefs/profile`, one JSON object keyed by preference name), every `PREFS` entry with scope `account`; each also keeps its `gamma-*` localStorage key as the instant-paint cache, and `gamma-profile-seen` keeps the profile as this browser last took it from an account or sent it, whichever account that was (which local value is this browser's own, "Reading & editing" below) | appearance (theme — the pre-paint script in `index.html` reads `gamma-theme`, resolves System to light or dark the way App.jsx's theme effect does and sets `data-theme` with its `data-scheme`, so the first frame has the right tokens ([ui-design.md](ui-design.md#tokens)) — flip page colors, the colour palette (`palette`, the swatches highlights, pens and text boxes offer), whether tours are suggested, `suggestTours`, and which tours the account has seen, `tourProgress`, written by the guide and shown nowhere, [onboarding.md](onboarding.md#storage)), the interface language (`gamma-language`, read by `main.jsx` before the first render, [i18n.md](i18n.md)), reading and editing (imported annotations, smooth wheel scrolling, how search opens, the handwriting habits `gamma-ink-draw-hold`, `gamma-ink-scribble-erase`, `gamma-ink-eraser-return` and `gamma-ink-eraser-filter`, the tool strip's presets `gamma-ink-tools`, and the Shapes tool's kind and fill `gamma-ink-shape-kind` and `gamma-ink-shape-fill`), translation (button and language, selection translation, effort, parallel requests), the Enter key, library display and PDF fetching, the sync pill's scope (`syncPillScope`), chat behaviour (tools switch, per-kind tool permissions, reasoning effort, speed, the login connection check, tool limits, snapshot clearing), context budgets, prompts, keyboard shortcuts (`keybindings`: command id → chord or null, [hotkeys.md](hotkeys.md)) |
| Session only | React state, nothing stored | the Ctrl+scroll text size of the notes list and the chat transcript (`useTextScale` in [Widgets.jsx](../../frontend/src/shared/ui/Widgets.jsx)) — resets on reload |
| Per account, synced | `/api/prefs/{key}` (small JSON KV: the account-wide keys in `users.db` `user_prefs`, the others in the workspace's `pages.db` `workspace_prefs`, [user_db.md](user_db.md)) | per account AND workspace, kept in that workspace and left out of its exports and backups: open tabs (`open-tabs`), the recently-viewed queue (`recent-views`), reading positions (`read-pos`) — they name one workspace's pages (pins are no pref: a pinned page or folder carries `properties.pinned` on its block, [home_library.md](home_library.md) "Pinned"); account-wide: active AI key (`ai-provider`) and the preference profile (`profile`, previous row). Server wins on load, localStorage (keyed `user@workspace`) is the instant-paint cache. The recents-card cover thumbnails are workspace data, through their own `/api/page-snaps` store (`page_snaps` in the workspace's `data.db` — over the prefs size cap) |
| Per account, seen notices | the account-wide `notices-seen` prefs key (`db.NOTICES_SEEN_PREF_KEY`), `{notice id: fingerprint}`, written only by `POST /api/notices/{id}/seen` (below, "Notices") | which release and which log error the account has already looked at |
| Per account, done reminders | `users.db` `reminders_done`, a row per dismissed reminder (its key, the workspace it was last found in, when), written by `POST /api/reminders/done` and kept while the reminder exists ([mentions.md](mentions.md) "Reminders"); no pref, as there may be thousands (the `reminders-done` prefs key held them before migration step 39) | which of the account's reminders it has dismissed, in every browser |
| Per account, server-only | AI provider entries (keys/OAuth tokens) under the reserved `ai-settings` prefs key (account-wide), managed via `/api/ai/providers*`; the browser only ever sees a masked hint. The server's shared entries (next row) are listed after them read-only. Machine-translation keys live the same way under the reserved `translate-engines` key (`/api/translate/engines*`, [translation.md](translation.md)), and the online search settings under the reserved `search-services` key (`/api/ai/search-services*`, [ai_tools.md](ai_tools.md) "search_web") | API keys, ChatGPT OAuth, Google / Youdao translation keys, the web search engine with Brave / SearXNG / OpenAlex settings |
| Per workspace | `workspaces` / `workspace_members` in `users.db`, via `/api/workspaces*` ([workspaces.md](workspaces.md)) | name, kind (personal / shared), members and roles, access (private / public + the public role) and a shared workspace's own quota (admins), the account's default workspace, which workspace this tab works in (`?ws=` in the URL, `gamma-last-ws:<user>` remembers the last one) |
| Server-wide (admin) | `settings` KV in `users.db` via `GET/PUT /api/admin/settings`, plus nullable per-user override columns; the shared AI entries under the `ai_providers` key via `/api/admin/ai-providers*` (keys encrypted with the data directory's key, like the cloud client secret) | default max upload size, default storage quota, public URL, cloud sign-in (and whether this server is the share host), how long guest workspaces last (`guest_ttl_hours`) and demo mode (`demo_mode`, [guests.md](guests.md)), shared AI provider entries, whether guests may use them and the shared AI allowance per account / per guest, the off-site copies (the `offsite` key via `/api/admin/offsite`, its secret key encrypted the same way), a hosted container's last plan answer (`hosted_limits`, written only by the sync, [cloud_accounts.md](cloud_accounts.md) "Hosted containers") and the time of its last write (`hosted_last_write`, saved by the sync) |

Adding a preference = one entry in `PREFS` (key, scope, default, and a codec
if the value needs validation) plus a control in the matching settings pane;
`useAppPrefs()` ([prefs.js](../../frontend/src/app/prefs.js)) turns every
entry into state and a setter. Don't scatter `usePersistedState` calls
through App.jsx. Pick `account` unless the value only makes sense on this
device or names something that exists only on this server.

The profile syncs through `useProfileSync` in prefs.js. On sign-in the
server copy wins; an account without one is seeded from this browser.
The first screen does not wait for it: `/api/session` carries the
profile's `theme` and `language`, which `main.jsx` puts on as the session
lands (`app/boot.js`), so a browser new to the account draws in its theme
and language from the start ([i18n.md](i18n.md)); a theme or language this
app does not know (one a newer server synced through Gamma Cloud) is not
applied, as the profile's codecs drop it. The first load then reads the
profile with the other synced keys (open tabs, recents, reading positions,
the AI key) in one request, `GET /api/prefs?keys=`, sent right behind the
library listing (`preloadPrefs`); later reads ask for one key.
Entries are validated with the same codecs as localStorage, so unknown names
and bad values are dropped and a partial profile applies what it has.
A change is pushed one second after it settles (a slider sends one request)
as a `PATCH /api/prefs/profile` carrying only the entries that changed since
the copy this tab last saw. A tab's stale value of another entry therefore
never undoes a change synced in meanwhile. A pending push is flushed when
the page is hidden (with `keepalive` when the page's keepalive room allows,
at its normal priority: [collab.md](collab.md) "Keepalive"), focus pulls
at most every 15 seconds, and nothing is pushed before the first load
succeeds. Signed-out visitors, guests and share views work from
localStorage alone.

The profile carries no secrets; the provider entries and the active key
never join it. An account linked to Gamma Cloud carries it to every Gamma
server it signs in to, VS Code style: merged preference by preference
against the copy both sides last agreed on. The merge runs on a cloud
sign-in, when a browser reads the profile (at most once a minute), a few
seconds after a change and every hour. A first sync of two different copies
opens a dialog asking Fetch from cloud / Push to cloud; Settings → Account &
sync → Settings sync has one Sync now button (`gamma/cloud_sync.py`,
[cloud_accounts.md](cloud_accounts.md)). `PUT /api/prefs/profile`
(whole-object replace, kept for scripts and tests) requires an object. Both
share the prefs cap of 64 KB, enough for four long custom prompts. The
migration that folded the old `appearance` key into the profile is below
the upgrade floor now (`MIN_UPGRADABLE` in `gamma/migrations.py`).

Each account section's tag shows where its own settings stand. A section
names the preferences it holds: `Section`'s `prefs`, taken pane by pane
from `SECTION_PREFS` in
[settings/sectionPrefs.js](../../frontend/src/settings/sectionPrefs.js).
`accountPrefs` there throws on a name that is not an account-scoped entry
of `PREFS`. `tests/sectionPrefs.test.mjs` checks the table against `PREFS`
and against every `scope="account"` in the panes' sources, and that every
account preference is held by a section. A browser preference inside an
account section carries its own tag on its row (`Row`'s `scope="browser"`):
"Translate with" in Translation › Service is the one case.

`useProfileSync` returns its overall state (signed-out / loading
/ loaded / pending / pushing / failed) and, as sets of preference names:
`pending` (the value differs from the copy the server last confirmed),
`inflight` (sent in the PATCH now on its way), `failed` (a push of exactly
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

So changing the Enter key spins only Keyboard › Built in, for the second the
change settles plus its PATCH (and at least 700 ms, so a quick save is seen
rather than flickered). The server's acceptance is the commit: its own
push to Gamma Cloud is coalesced (5 s) and retried by the hourly check. The
tag never waits on it; it shows only a failure of that hop, never the
cloud's pending state. A cloud error from before any change this session
shows on no section. The Account pane's Settings sync row says it instead
(`CloudSyncRow` in `settings/SettingsCloudSignIn.jsx`, under the Gamma
Cloud row; its hint is `cloudSyncHint` in `settings/syncState.js`: "Synced
with Gamma Cloud at 14:37" / "Not synced: <error>"). The row's one Sync now
button opens the Fetch from cloud / Push to cloud dialog while a first sync
waits. Browser sections always show the monitor and "browser".

The tag is an icon and one muted word ("account" or "browser") in the
small caption size; the sentence is its hover `title` and its
`aria-label`, and `data-sync` carries the state for tests.

The last open page and viewer layout use `app/sessionState.js`, separately from
synced preferences. Its key is `gamma-session:<user>@<workspace>`. Reads wait
for workspace selection; changing scope cancels pending saves. Saves are
debounced (300 ms) and written early on `pagehide`, so a reload right after a
change (closing the notes window, say) keeps it. Old unscoped
session caches are ignored because their account owner cannot be determined.

## Periodic backup tasks

Settings → Backups opens with one task table (`BackupTasks.jsx`). A task
names the workspaces it backs up (all the account owns, or a chosen set),
whether uploads are included, a schedule and a retention rule. Several tasks
may cover one workspace.

The editor (a `SubDialog`) offers Hourly, Daily, Weekly (chosen weekdays),
Monthly (a day of the month) and a five-field cron expression. The stored
cron is always UTC, the time the server runs it in. Daily and Weekly times
are entered in the browser's own time ("Time (your time, PDT)", with
"= 10:00 UTC" under the box), and so is Hourly's minute. Hour and weekday
convert together with the browser's current offset
([settings/backupSchedule.js](../../frontend/src/settings/backupSchedule.js),
`tests/backupSchedule.test.mjs`). In a zone with daylight saving time the
editor says the run moves an hour when the clocks change. Monthly schedules
and cron expressions stay in UTC (a day of the month cannot cross a month's
end). The next three runs are previewed in the browser's time zone.
Retention is an age (days, weeks or 30-day months) or a snapshot count per
workspace. Only that task's snapshots expire, after a successful run; the
newest is always kept. Deleting a task keeps its snapshots.

The server bounds what tasks can pile up. A schedule runs at most once an
hour (the schedule preview refuses a cron with two minute values).
Retention is at most 90 days or 90 snapshots (the editor's box stops
there). An account keeps at most five tasks (at five, Add task and Duplicate
are disabled and a line under the table says so), and a run is refused while the
disk has less than 1 GB free. A refused run shows its reason in the row and
raises the `backup-failed` notice ([workspaces.md](workspaces.md) "Export
and backups").

The table fits the pane (the dialog keeps one width on every pane): the
task cell's second line holds its workspaces, retention and whether files
are included ("All workspaces I own · keeps 30 days · includes files"), the
schedule shows in your time over its UTC form, and the Last run cell is the
state as a dot and a word (Not run yet, Queued, Running, Finished, Failed),
when that run started, and for a finished run what it made, from the
task's `last_result` ("2 snapshots · 14.2 MB · 3.1s"). A failed run's
reason is its tooltip and a line under the table.

`GET/POST /api/backup-tasks`, `PUT/DELETE /api/backup-tasks/{id}`,
`POST /api/backup-tasks/{id}/run` and `POST /api/backup-tasks/preview`.
Guests and integration tokens cannot manage tasks. While a task runs, it also
shows as a read-only row in Background tasks ([tasks.md](tasks.md)).

Runtime and storage: [docs/dev/workspaces.md](workspaces.md#export-and-backups).

### Snapshots and restores as background jobs

Under the tasks, the pane's saved snapshots are taken and restored as
background jobs ([tasks.md](tasks.md)): Back up now and Back up all start a
`snapshot` job (one job for all of them), and Restore → Replace… or Merge…
starts a `restore-snapshot` job. The pane never waits on the request. A
workspace with a job under way shows its progress under its heading and
takes no second one. Its list reloads once the job ends, wherever the job
was started. A restore into the open workspace reloads the tab that started
it. Settings → Server's server backups work the same way (`server-backup`).
Settings → Workspaces → Data exports a workspace (a `workspace-export` job
whose zip downloads by itself once ready) and merges or restores a zip into
one (a `restore` job over the uploaded file); Export all is one
`workspace-export` job.

## Notices: the red dot

Something that wants a look once — a newer Gamma release, errors in the
server log — is a *notice* (`gamma/notices.py`): `{id, fingerprint, tone,
pane, title, message, params}`, where `pane` is the Settings pane that shows
it and `fingerprint` names what changed (the release version; the server
start time plus the newest error's seq). The sentence travels as `message`,
a stable English template that is also the key of the frontend's catalog,
plus its `params`; `title` is the same sentence filled in, for API readers.
A source builds its notice with `notice(id, fingerprint, tone, pane,
message, **params)`, and every message is listed in `NOTICE_MESSAGES`
(`app/notices.js`, whose node test reads notices.py to keep the two lists
equal), so each one has its translation. "Resolved" means the account has seen
that fingerprint, kept in the `notices-seen` pref; a new release or a fresh
error changes the fingerprint and the notice is back by itself. Nothing is
dismissed for good, and nothing is per browser.

Sources are functions `fn(user_id)` (the account's id) registered with `@source` in
`gamma/notices.py`; each returns a Notice or None and must be a cached,
in-memory or small database read, because `GET /api/notices` runs them on
every poll. Admin-only sources are skipped for members; guests, share views
and integration tokens get an empty list. The sources:

| id | who | pane | tone | fires when | fingerprint |
|---|---|---|---|---|---|
| `update` | admins | Server | warn | a newer GitHub release than this build, or for a `-dev` Docker build a newer build of its branch (`version.check`, six-hour cache; the endpoint is sync on purpose) | the newer version |
| `log-errors` | admins | Server | error | an error was logged since the last look (`logbuf.last_seq("error")`) | server start time + the newest error's seq |
| `db-damage` | admins | Server | error | the latest integrity check of a database file failed — a snapshot's copy, or Server → Databases → Check now (`integrity.failures()`, one small JSON file) | a digest of the damaged files and when each was found; a file that passes a later check drops out |
| `backup-failed` | everyone | Backups | error | a backup task of the account is in state `failed` (`backup_schedule.list_tasks`) | each failed task's id + its last run |
| `mirror-conflicts` | everyone | Workspaces | warn | a clone the account owns has open sync conflicts (`sync_engine.open_conflict_mark`) | a digest of, per clone, the count + the newest conflict id — resolving old ones never brings it back; a digest, so any number of clones fits the fingerprint's 200 characters |
| `publish-conflicts` | everyone | Account | warn | a workspace publishing pages to Gamma Cloud has open sync conflicts | per publication, as `mirror-conflicts` |
| `cloud-sync` | everyone | Account | warn | the account's Gamma Cloud sync is in its `error` state (`cloud_sync.profile_status`) | the failure's timestamp |
| `cloud-sync-choice` | everyone | Account | warn | the first settings sync with Gamma Cloud found two different copies and waits for Fetch from cloud / Push to cloud (state `choose`) | constant: seen once |
| `cloud-link` | everyone | Account | info | cloud sign-in works here (on, and not waiting for Connect) and the account has no Gamma Cloud identity (`cloud_auth.status_of`); not on the share host. The Account pane's Gamma Cloud row says what linking gives and links it | constant: seen once |
| `cloud-setup` | admins | Server | info | cloud sign-in is off, or on but the server still has to be connected (`cloud_auth.needs_connect`); Server → Sign-in turns it on | `off` / `connect`, so each state is seen once |
| `join-requests` | admins | Server | info | Gamma Cloud accounts ask to join this server (`join_requests.waiting`: the waiting requests, [cloud_accounts.md](cloud_accounts.md) "Requests to join"); Server → Sign-in lists them | when the newest waiting one was made: a new request brings it back, a decided one does not |
| `free-translate` | everyone who met the failures | Translation | warn | Microsoft's free translation service failed `FREE_ALERT_AFTER` (3) times in a row, in memory (`translate_engines.free_failing`); one success ends it, and the Microsoft row names the error | the streak's start time |
| `hosted` | admins (grace), everyone (read-only) | Server for admins, Account for members | warn / error | a hosted container's cached plan answer (`hosted.limits`, from memory) is in `grace` (admins only; names `grace_until`) or read-only (everyone) — [cloud_accounts.md](cloud_accounts.md) "Hosted containers" | the status and since when it holds (`status_since`), so each episode is seen once |
| `storage` | everyone | Account | warn / error | personal storage past 90 % of the quota / full; only computed for an account under a quota, and the upload walk is remembered ten minutes (`notices.forget_usage`) | `90` / `full` |

Warnings in the log are deliberately not a notice (too noisy for a dot).

The frontend: `app/useNotices.js` (one instance in App.jsx) polls every
five minutes and on window focus, and `app/notices.js` (pure,
`tests/notices.test.mjs`) folds the list into the strongest `tone`, the
strongest tone per `pane`, and `firstPane`. The `.noticeDot` (red; accent
for an `info` notice) sits on the account button, after the account menu's
"Settings…", and after each dotted pane's sidebar button. The account
button's hover title counts the notices ("Account & settings — 1 notice";
its accessible name stays "Account & settings"), and the account menu shows
one tinted row per notice under the identity card (itself a button into
Account & sync): `noticeText` (the
message translated with its params, or the server's title for a message
this build does not know) and a link (`noticeAction`, "Review storage →")
that opens the notice's pane. "Settings…" opens on `firstPane`; every other
opener keeps its pane. Showing a pane calls
`markSeen(pane)`: its notices leave the list at once and each ack is posted
to `POST /api/notices/{id}/seen`, so other tabs and browsers agree on their
next poll. The browser flow is the last step of `e2e/scenarios/settings.mjs`,
with a faked feed and real acks.

## The Settings dialog

One dialog, one sidebar
([SettingsDialog.jsx](../../frontend/src/settings/SettingsDialog.jsx)). On
top sits the account card (`NavAccountCard`: avatar initial, name, "Account
· 4 KB used" and the storage bar, from `/api/quota` like the account menu).
It opens the account pane, whose name "Account & sync" is its accessible
name, and carries that pane's notice dot. Four captioned groups follow:
`PREFERENCE_NAV` (Preferences), `AI_NAV` (AI), `LIBRARY_NAV` (Library) and
`ADMIN_NAV` (Administration). Administration carries an `admin` tag and
renders only for admins, since it changes the server for everyone.
`HELP_NAV`, Help & diagnostics, is pinned to the rail's bottom. Every pane
is one click from any other; the one page below a pane is a workspace's
Manage page, whose breadcrumb leads back. Panes carry no explanatory
subtitle: a section rule's right-hand tag (an icon plus "account" or "browser", `Section`'s `scope` prop, matching
the settings' scope in `PREFS`; an account tag's icon is the sync state of
that section's own settings, above) says where a setting lives, a row's
short hint what it does, and the hover `title` the rest.

The account card:

- **Account & sync** (pane id `account`; `sync` is an alias): the signed-in
  account's row, storage meter and Gamma Cloud link row.
  Under it, the sync section
  ([SettingsSync.jsx](../../frontend/src/settings/SettingsSync.jsx)):
  **Publishing** lists the workspaces that publish pages to Gamma Cloud
  (`PublishingSection` in SettingsMirrors.jsx: the count of pages, the
  state, Conflicts when any wait, a "more" menu with Sync now and Stop
  publishing all; an empty state for the signed-in with nothing published).
  It is hidden for the guest. Clones are listed under Workspaces, and the
  sync pill's scope is under Appearance.

Preferences:

- **Appearance** ([SettingsAppearance.jsx](../../frontend/src/settings/SettingsAppearance.jsx)):
  how things look. The eight theme cards (`PictureChoices`; each swatch
  is drawn under its theme's `data-theme` / `data-scheme`, so it paints
  from that theme's own tokens, `ThemePreview` in
  `shared/illustrations/`), the
  dark-page switch with its live PDF sample, **Colors**: the account's
  colour palette (`palette`, [shared/model/palette.js](../../frontend/src/shared/model/palette.js)):
  `{groups, pickers}`. A group is `{id, name, colors: [{hex, alpha}]}`,
  shown as a card — its name (an inline text box), its swatches drawn at
  their alpha and titled by their nearest colour name, a "+" swatch,
  Duplicate and Delete — and a selected swatch opens the editor under
  the row: the `ColorPicker` ([ui-design.md](ui-design.md), a saturation
  square, hue and alpha bars, a hex field that takes `#rrggbbaa`, an
  opacity field, 10–100%, and the eyedropper where the browser has one)
  with the colour's name and Remove beside it. Add group adds one; up to 12 groups of 24 colours, names up to
  40 characters. The defaults are three groups: Highlight (the four light
  wash colours Gamma always had, at 0.65), Ink highlight (those four and
  the four more the tool strip's highlighters always offered, at 0.6) and
  Pen (the 14 dark ink colours, opaque). Four picker rows — Text
  highlights (the selection popup and a highlight's recolour menu), Ink
  highlights and Ink pens (a tool strip preset's options row), Text boxes
  — each choose one group in a `MenuSelect` dropdown (`pickers`, a group
  id per picker; one naming a deleted group falls back to the picker's
  default group, else the first group with a colour, and `pickerColors`
  never comes back empty). Reset colors puts the
  defaults back. The pickers read the palette through `PaletteContext`
  (`shared/ui/paletteContext.js`): the selection popup and a highlight's
  recolour menu store the swatch at its alpha (a highlight's
  `properties.color` is `#rrggbbaa`, [api.md](api.md) "The highlight
  shape"), a pen or highlighter preset takes the swatch's alpha as its
  own `opacity` ([handwriting.md](handwriting.md)), a text box takes the
  hex alone. A changed swatch is what the pickers offer from then on;
  stored highlights and strokes keep the colour they have. The first
  shape of the preference, `{wash, ink}`, still reads: it becomes the two
  default groups, **Library**: the live card demo
  with the thumbnails / folders / labels switches
  ([SettingsLibraryDisplay.jsx](../../frontend/src/settings/SettingsLibraryDisplay.jsx)),
  **Interface** (this browser's): the interface size, the status bar and
  Inbox notifications (`inboxSystemNotifications`), **Sync status** (the header's sync
  pill: a `Segmented` of synced pages only, or every page of a workspace
  that syncs some — `syncPillScope`, [mirror.md](mirror.md) "Publishing"),
  and last **Suggest tours** (`suggestTours`: off, no tour or hint is
  offered by itself, [onboarding.md](onboarding.md)). The old `library`
  pane id is an alias of this pane.
- **Reading & editing**: how papers and notes behave, one section per
  subject. **PDFs**: imported annotations (a Keep / Remove segmented choice),
  open-access fallback, metadata auto-fetch and saving external PDFs.
  **Notes**: **Linked from** (`backlinksVisible`, off by default) shows or
  hides a page's "Linked from" section ([ui-design.md](ui-design.md); off,
  App fetches no backlinks at all). **Handwriting** (account scope,
  `SECTION_PREFS.reading.Handwriting`; the strip's own options, such as
  presets, smoothing and the straight highlighter, are set on the strip,
  [handwriting.md](handwriting.md) "What the user sees"): a *Reset
  presets* button (`uiBtn sm`, a confirm dialog, then the strip's
  `setInkTools`, `ink/useInkStrip.js`, back to `DEFAULT_TOOLS`);
  **Eraser returns to the last tool** (`Toggle`,
  `inkEraserReturn`, flag, default off; hint "After erasing, return to the
  tool you had"); **Erases** (`Segmented` All / Pens / Highlighters,
  `inkEraserFilter`, `oneOf(["all", "pen", "highlighter"])`, default
  `all`; hint "Everything, pens only or highlighters only"); **Draw and
  hold** (`Toggle`, `inkDrawHold`, flag, default on; hint "Hold the pen
  still to straighten a line or clean up a shape"); **Scribble to erase**
  (`Toggle`, `inkScribbleErase`, flag, default off; hint "A dense
  back-and-forth with the pen erases the ink under it"; the first switch
  on shows a status pill explaining the gesture). The presets themselves,
  **`inkTools`**, belong to this section too but are edited on the strip:

  - Scope `account`, codec `json(normalizeTools)` (`profileValue` accepts
    an array), up to 12 presets.
  - Each is `{id, kind, color, size}` with its optional `opacity`,
    `brush`, `dash`, `stabilize` and `straight`. A calligraphy pen adds
    `nib` (an integer from −60 to 60, omitted at 45) and `nibLock`
    (`true`, omitted when false).
  - `hidden: true` is a preset taken off the strip through its ⋯ menu's
    *Hide*, omitted when false.
  - Any other key a preset carries is kept verbatim as long as the preset
    serialises under 512 bytes of UTF-8. So an older build editing one
    preset never strips what a newer one wrote.
  - Sign-in follows the profile rules above: the server's list wins, and
    an account without one is seeded from the browser.
  - When the server's list replaces a list this browser changed itself,
    `useProfileSync`'s `onReplaced({name, local})` lets App ask once, on
    a load's first pull: "Use this browser's pens instead?" with *Use
    this browser's* ([handwriting.md](handwriting.md) "Presets follow the
    account").
  - A list this browser changed itself is neither the defaults nor the
    list as this browser last took it from an account or sent it.
    `useProfileSync` keeps that in `gamma-profile-seen`, whichever account
    it was.
  - App writes the list through `normalizeTools` every time, so the
    pushed value is the canonical one a pull reads back.
  - The pen a stylus writes with is session state. The eraser's mode and
    size and the lasso mode are browser preferences (`gamma-ink-eraser`,
    `gamma-ink-eraser-size`, `gamma-ink-lasso`).
  - The Shapes tool's, the ruler's and the zoom window's choices are set
    where they act and have no Settings row.

  **`inkShapeKind`** (scope `account`, `oneOf(["line", "arrow", "rect",
  "ellipse"])`, default `line`: what the Shapes tool draws) and
  **`inkShapeFill`** (account, flag, default off: whether it fills its
  rectangles and ellipses) are listed in `sectionPrefs.js` with the
  Handwriting rows, as `inkTools` is. **`inkRulerUnits`**
  (`oneOf(["pt", "cm"])`, default `pt`: the ruler's ticks) and
  **`inkZoomHeight`** (`intClamped(160, 320)` with `serialize: String`,
  default 200: the zoom window's height in CSS px, written when a drag of
  its top edge is let go and at each ↑ / ↓ on it) are browser
  preferences, like the eraser's. The ruler's place
  and angle and the zoom window's place, magnification and line height are session
  state (`inkRuler` in `ink/useInkRuler.js`, `inkZoom` in `ink/useInkZoom.js`;
  [handwriting.md](handwriting.md) "What the user sees"). **This device** (browser
  scope): two
  `IconChoices` tiles ("Draws with": pen only / pen and
  finger — the stored preference is `inkPenOnly`), the
  stylus-draws-right-away and pressure switches, **Undo with fingers**
  (`Segmented` Off / Tap / Double tap, `inkFingerUndo`, `oneOf(["off",
  "tap", "double"])`, default `double` where the primary pointer is coarse
  (`COARSE`, as `inkPenOnly`) and `off` elsewhere; hint "Two fingers undo,
  three redo"), and **Hover preview** (`Toggle`, `inkHoverPreview`, flag,
  default on; hint "The footprint over the page shows the pen's style";
  off, a mouse or a hovering pen shows a plain round ring,
  [handwriting.md](handwriting.md) "What the user sees"). **Apple
  Pencil** (browser scope) shows only in the iPad app
  (`nativeHost()?.pencil`, [ipad.md](ipad.md) "The pen"): **Double tap**
  (`MenuSelect`, `inkPencilTap`, `oneOf(["system", "eraser", "previous",
  "options", "undo", "none"])`, default `system`: System setting · Switch
  to eraser · Switch to last tool · Show tool options · Undo · Nothing;
  hint "What a double tap on the Pencil does"), **Squeeze** (`MenuSelect`,
  `inkPencilSqueeze`, the same values and `holdEraser`, `holdPrevious`:
  Eraser while held · Last tool while held, offered only under
  `nativeHost()?.pencilPhases` or while one is the stored choice; default
  `system`; hint "What squeezing
  Apple Pencil Pro does") and, under `nativeHost()?.haptics`,
  **Haptics** (`Toggle`, `inkHaptics`, flag, default on; hint "A tick in
  Apple Pencil Pro when a shape snaps, the lasso selects or the tool
  changes"). Its choices are `MenuSelect`s: the settings pane is no
  `InkTooltips` host, and six or eight choices are too many for a
  `Segmented`. Every row of these sections has a `settingsSearch.js`
  entry under its section's title. An Apple Pencil row is listed only
  under a host with the flags its row shows under (`hostOnly(flags,
  entry)`, beside `admin()`; *Haptics* needs `pencil` and `haptics`).
  **Search opens as**: on the
  home page and on a page (Full panel / Find bar).
- **Language and Translation** (pane id `translation`,
  [SettingsTranslation.jsx](../../frontend/src/settings/SettingsTranslation.jsx)),
  in four sections. **Language**: the interface language (a `MenuSelect`:
  System / English / 中文, [i18n.md](i18n.md)). **Viewer & selection**: the viewer's button and the
  language (`translateLang`; with none stored, the first of the browser's
  languages on offer — `defaultTranslateLang` in `app/prefDefs.js`, en-* →
  English — else Simplified Chinese), the selection popup's translate button
  and translate-on-select. **Service** (no scope tag: its keys are the
  account's, kept on the server): "Translate with" (a chat model or a set-up
  translation service; a browser pref, tagged on its row), the Microsoft
  (free) row with only Test (no key), the Google / Youdao credential rows
  with Test / Edit / remove. **Speed**: translation effort (hidden while a
  translation service is picked, or with no AI connection) and parallel
  requests.
- **Keyboard** ([SettingsKeyboard.jsx](../../frontend/src/settings/SettingsKeyboard.jsx),
  [hotkeys.md](hotkeys.md)): a filter box in the head, then **Shortcuts**
  (the one account section, "Reset all" as its action) listing every command
  of the catalog by group — each row a `KeyBinding`: the chord as key caps,
  click-then-press to rebind, Backspace unbinds, a reset button when it
  differs from the default, red caps and "Also used by …" when two commands
  share a chord — and **Built in**, the outliner's fixed keys read-only, led
  by the Enter key's one choice: "Enter makes" New note / New line
  (`enterNewNote`; the legacy `notes` pane id lands here).

AI:

- **Connections**: the provider list (empty state: one sentence and the Add
  button, which opens the connect dialog, "Connect an AI service": service
  tiles (`IconChoices`: ChatGPT, Anthropic, OpenAI API, Other — Other opens
  the named services by vendor, a Plan menu for a vendor with several, or a
  custom endpoint and its API format; [ai.md](ai.md#other-services)), the key with the
  provider's placeholder, a "Get a key at …" link and the live check, then
  the models with the name and test model under More options; its button is
  Connect, and the new connection is tested once saved; the server's shared entries follow the account's own as read-only
  rows tagged "Shared by this server", selectable as the active key but
  without Test / Manage / delete; with no key picked, or the picked one
  removed, the first connection that offers models is the active one, here
  and in the chat's model menu), the login connection check, the models
  (default chat, metadata, dictation) and the account's token usage
  ([ai.md](ai.md) "Token usage"). While a shared entry applies, the usage
  opens with a **Shared allowance** row ("12k of 50k tokens in the last
  24 h", a red "used up" tag once spent; "12k tokens in the last 24 h · no
  limit" without one, [guests.md](guests.md)). The account menu's card shows
  the same numbers under the storage meter. The check, models and usage
  sections appear only once a provider exists; a guest sees the usage too,
  without Reset. The chat's setup card (no AI connected, `ChatSetupCard` in
  `chat/ChatDock.jsx`) reads the masked key list alongside `/api/ai/models`
  and shows once both are in, tiles and all. Until the chat knows whether
  an AI is connected it is a plain frame, with no header tools and no
  empty-state text; when the `/api/ai/models` read fails it shows them as if
  one were (App's `aiInfoFailed`), and a send says what is wrong. The card
  opens this pane through `openAiKeysEditor({service})`: Settings is fetched as the pointer or the
  focus reaches a tile, and the add dialog, set to the tile's service, opens
  with Settings on the list the card read while the pane reads its own (a
  chat error card's fix opens an entry's form once the list has loaded,
  `openAiKeysEditor({entry})`); the card's tiles and the dialog's service tiles are one list
  (`aiServiceTiles` in `settings/providerEditor.js`). Once a connection made from the
  card is saved with a model, Settings closes, the status says "Connected —
  <model> ready" and the chat's message box takes the focus.
- **Chat**: three sections. **Chat** holds the default reasoning effort,
  the default speed and the snapshot-clearing switch. The effort menu
  lists every level, since each model gets the nearest one it takes
  ([ai.md](ai.md#reasoning-effort)). A speed reaches only models whose
  provider offers that service tier ([ai.md](ai.md#speed-service-tier)).
  **Prompts** is the accordion, with one Cancel / Save pair as its action.
  **Context size** holds the three text budgets and "Pictures per message"
  (the chat's picture budget, [ai.md](ai.md#pictures)), with the Standard /
  Larger / Custom preset as its action. The old `ai-advanced`, `context` and `prompts` pane ids are
  aliases of this pane, each jumping to its section.
- **Tool usage** (pane id `tools`): **Tools**, the master switch and, per chat
  kind (folder / PDF / notes), a permission table (`AgentToolMatrix` in
  `AssistantTools.jsx`). Rows explain library reading, web research and changes;
  each column's menu offers the Read library / Read & search / Ask before
  changes / Allow all presets. Web search, fetching and using connected
  journal sign-ins have separate rows. Under the table, two switches:
  **Fetch blocked papers in the background** (`fetchInBackground`, the
  handoff card hands a stopped fetch to Gamma Connector by itself) and
  **Read long papers with a helper** (`delegateReads`, the `read_paper`
  tool, [ai_tools.md](ai_tools.md)). Then
  **Online search** (`OnlineSearch.jsx`): which service general web search
  goes through (Automatic, your AI connection, Brave Search, SearXNG or Off)
  and the Brave, SearXNG and optional OpenAlex settings, stored on the server
  like the translation keys ([ai_tools.md](ai_tools.md) "search_web"). Last,
  **Tool limits**: tool rounds per message and the read window.
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

Library:

- **Workspaces**: storage meter, personal and shared workspaces, New
  workspace, Export all, and last the clones. Each row has Open, a Data menu (export, import)
  and Manage. Manage opens an inline page under one breadcrumb head,
  "Workspaces › <name>", with Open workspace at its right end: General,
  Access, Members and the Danger zone, every row saving at once, no footer.
  Rename and invite are small editor dialogs. A row's on-disk folder
  (`workspaces/<id>`) shows to admins only. The empty Shared section offers
  admins "New shared workspace" (a jump to Server). The account popover's
  "Workspaces…" opens this pane. Last, for everyone but the guest, come
  **Clones** (`MirrorsSection` in
  [SettingsMirrors.jsx](../../frontend/src/settings/SettingsMirrors.jsx)),
  the account's mirrors of remote workspaces in git's words, kept out of
  the Personal list. Each row has a status line, Open, Sync or Reattach,
  Conflicts (the conflict cards, each resolved there or opened on its block)
  and a "more" `ActionMenu` with Force pull / Force push, Detach and Remove
  origin. The section's action is "Clone a remote workspace" (a
  `SubDialog`: origin server, write token, into a new or an existing
  workspace, name, direction) — [mirror.md](mirror.md). The same state sits
  in the header as the sync pill (`collaboration/MirrorPopover.jsx`) while a
  clone is open, and the pill's "All clones in Settings" opens this pane.
  The clone's own settings (cadence, direction, force pull / push, detach /
  reattach, remove origin) live in that pill's gear view, stored on the
  server per mirror (`mirrors.poll_s`, `on_change`, `mode`).
- **Backups** ([SettingsBackups.jsx](../../frontend/src/settings/SettingsBackups.jsx)):
  the task table first ([BackupTasks.jsx](../../frontend/src/settings/BackupTasks.jsx):
  Add task opens the editor `SubDialog`; each row has an Enabled switch and
  a Run now / Edit / Duplicate / Delete `ActionMenu`), then for admins
  **Off-site copies** ([OffsiteCopies.jsx](../../frontend/src/settings/OffsiteCopies.jsx)),
  then the server-kept snapshots per workspace (Back up all, and per
  workspace: back up now, download, restore, delete). A row tags a task's snapshot "Automatic" and
  the one a replace restore keeps "Before restore", and says when files were
  missing or a database copy failed its check.
  Off-site copies sends the server's databases and uploaded files to an
  S3-compatible bucket, a backup target only: Gamma keeps running on its
  local files. The section has three rows. The Copy to a bucket switch
  saves at once; with no bucket saved, turning it on opens the set-up
  instead. The summary row is named after the bucket ("No bucket yet"
  before one is saved) and reads "s3://gamma-backups/prefix · every 1 h ·
  7 kept", with the endpoint's host in place of `s3://` for R2 or MinIO.
  Its Set up… or Edit… button opens the editor `SubDialog` ("Set up
  off-site copies" / "Edit off-site copies"), laid out like the shared AI
  key editor: the bucket, the endpoint (empty for AWS S3), the region, the
  access key ID, the write-only secret key (the box reads "secret set —
  type to replace" while one is stored, and a secret is sent only when
  typed), the prefix, how often (a `MenuSelect`: every 15 minutes, 1, 6 or
  24 hours) and the copies kept per database, then Test connection, which
  tries the dialog's values, saved or not, and answers beside its button.
  Save (or Enter) closes the dialog once the server takes the values and
  shows its refusal in the dialog otherwise. Saving the first bucket turns
  the copies on, as connecting an AI service does; later edits leave the
  switch as it is. Closing the dialog with unsaved edits asks first. While
  the copies are on, Status reads "last round … · 12 databases · 340
  uploads · 0 failed · next round …" (or "no round yet", with the round's
  error under it) beside Copy now, which is disabled while a round runs.
  With `GAMMA_S3_BUCKET` set, the environment supplies every field: the
  switch is disabled, the summary says "Set by GAMMA_S3_BUCKET in the
  server's environment" (the summary line is its hover title), and its one
  button is Test, which tries the environment's bucket. `GET/PUT
  /api/admin/offsite`, `POST /api/admin/offsite/test` and `…/run`; the
  rounds themselves: [debugging.md](debugging.md) "Off-site copies in a
  bucket".
- **Maintenance** (the pane's head says Library maintenance): workspace
  storage, search-index rebuilding and the per-paper metadata / fetched /
  text / index health table.

Administration (admins only):

- **Users**: accounts, each with its personal workspaces and
  labelled Storage / Edit buttons.
- **Server**: the dashboard (build, uptime, warnings, the update
  check), the public server URL, on a hosted container the **Plan**
  (`HostedPlan`, [cloud_accounts.md](cloud_accounts.md) "Hosted
  containers"), storage defaults (each box saves on Enter or blur),
  **Guests**, the shared AI provider, shared workspaces,
  **Databases** (Check databases: a quick check of every database file, the
  damaged ones listed under the row), server backups and the log with its
  level filter ([user_db.md](user_db.md)).

At the bottom of the rail:

- **Help & diagnostics** (pane id `diagnostics`): browser tracing, the
  browser session log and, under Help, the Report a problem button (the
  same dialog as the account menu's entry; [debugging.md](debugging.md)
  "Report a problem").

**Shared AI provider** (Server, `SettingsAi.jsx` `SharedAiProviderSettings`)
lists the server's shared connections with the same rows and the same
add/edit form as Connections (`ProviderRow`, `ProviderForm`; both forms'
state is `useProviderEditor`, here over `/api/admin/ai-providers`, for the
account's list over `/api/ai/providers` in App). An entry is an API-key service or a ChatGPT
subscription signed in with the account form's steps
(`/api/admin/ai-providers/chatgpt/*`; [ai.md](ai.md) "How a sign-in reaches
the server"); the dialog is titled "Connect a
shared AI service" and tests a new entry once it is saved. Its last step,
**Who may use it**, is the shared form's own: three `IconChoices` tiles —
Every account / Accounts and guests / Selected accounts — and, under the
last, a `categoryTag` chip per named account with a compact
`AccountPicker` to add one ([ai.md](ai.md) "Shared provider entries",
*Access*; administrators always have access, the step's hint says so). Each
row says who may use it on an Access line, and has Test, Manage and delete,
plus Usage (the subscription's windows) on a sign-in; "+ Add provider" is
the section's action.
While at least one shared entry exists, two `UnitInput` rows set the shared
allowance, **Allowance per account** and **Allowance per guest**: tokens per
rolling 24 hours on the shared entries, 0 = unlimited, each saved on Enter
or blur as
`PUT /api/admin/ai-providers {allowance: {accounts | guests}}`
([guests.md](guests.md) "The shared AI allowance"). The boxes count in
millions ("M tokens", decimals allowed: `0.1` is 100k, the 10^9 ceiling is
`1000`) because a real daily budget runs to eight or nine digits; the API
stays in whole tokens, so the row converts both ways and a figure out of
range snaps back to the stored one.

**Guests** (Server, [SettingsGuests.jsx](../../frontend/src/settings/SettingsGuests.jsx))
has three rows over `/api/admin/settings`: the **Guest sign-in** switch
(`guest_logins`, on unless an admin turns it off — off, the login page
shows no guest button and `POST /api/login-guest` is a 403), **Guest
workspaces last** (a `UnitInput` in hours, `guest_ttl_hours`, saved on Enter
or blur) and the **Demo mode** switch (`demo_mode`: the login page leads
with Try the demo and folds the password form behind Admin sign-in; the
guide offers the first-paper tour on arrival). The last two describe guests
only, so they are disabled while guest sign-in is off. Each reports its
source like the public URL; when it is `environment`
(`GAMMA_GUEST_TTL_HOURS`, `GAMMA_DEMO`) the control is disabled and the hint
names the variable ([guests.md](guests.md)). The section is left out where
no switch could bring guests back (`guest_logins_available: false` in
`GET /api/admin/settings`: a hosted container, a share host,
`GAMMA_GUEST_MAX=0`).

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
Unknown cloud accounts: a `Segmented` policy, Refuse / Claim / Provision /
Invited. On a hosted container the plan sets it (`policy_source: "plan"`)
and the row's hint says so. Under Invited (and under another policy while
some are left) the section lists the **Requests to join**
(`JoinRequests`, `GET /api/admin/join-requests`): a Row counting the
waiting ones, then one person row each — the cloud username, an
`approved` / `declined` tag once decided, and a line with the e-mail
address, where it came from (the login page, or a page someone shared —
the sharer named, never the page), who decided and when. A waiting
request has Approve and a Decline icon button, a decided one a Remove
icon button ([cloud_accounts.md](cloud_accounts.md) "Requests to join").
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
in with Gamma Cloud" while it is on (a share link's sign-in gate too), and
under the Invited policy a foot saying that a newcomer's sign-in asks to
join. The **Account** pane has a "Gamma
Cloud" row (`CloudIdentityRow`): the linked username and plan with an Unlink
button, or a "Link Gamma Cloud account" button that round-trips through the
account server ([cloud_accounts.md](cloud_accounts.md)). Unlinked, its hint
says what linking gives: free publishing and settings carried to the
person's other servers. While sign-in is off, or on but waiting for Connect,
the row stays with that hint. An admin gets a *Set up* button that opens
Server at the Account server row; anyone else gets an `off` tag, and the
hover says an admin turns it on.

Search is backed by [settingsSearch.js](../../frontend/src/settings/settingsSearch.js).
Each entry names its pane, the setting's label, its section, its one-line
hint (the row's own words) and English synonyms. A setting one level down
(in a workspace's Manage page, a row's menu) also names the `target` on the
pane itself that the jump focuses. Every query word must appear in the
label, synonyms, hint or section. Entries whose label holds them all come
first; inaccessible management pages are filtered out, and so is an
admin-only section of a pane everyone has (Backups › Off-site copies,
marked `admin()` in the catalog) unless the Server pane is offered. So is
a setting only a native host shows (Reading & editing › Apple Pencil,
marked `hostOnly(flags, entry)`, a flag or a list), unless `nativeHost()`
has every one of its flags (the dialog passes it as
`searchSettings(query, panes, {host})`). A result is a
row-like button: the pane's icon, the label with the query marked, the
hint, and "Pane › Section" on the right. Past six results they group under
one caption per pane. Picking one opens the pane and focuses the matching
`data-setting` element. Add an entry when adding a new setting:
`tests/settingsSearch.test.mjs` fails when an entry's target is no
`data-setting` its pane renders. The search box and the Keyboard filter
carry their own × (the browser's is hidden).
Legacy pane names resolve through `resolveSettingsPane`; old notes, search,
viewer, context, ai-advanced and prompts entry points also jump to their
section.

The desktop surface has a persistent search header and labeled sidebar. On
phones the Back button opens a labeled category list. All controls remain
reachable by keyboard and touch.

Most preferences apply immediately, the server storage defaults included
(each box saves when it commits). Prompts and the public server URL use a
draft with Cancel / Save (Confirm). Credential, account and workspace editor
dialogs protect unsaved drafts on Cancel, Escape and backdrop dismissal.
`useSettingsDraft` registers dirty editors with the settings navigation
guard.

### What a pane reads

The sections of a pane read the server through one store, made each time
the dialog opens and kept until it closes
([settingsReads.js](../../frontend/src/settings/settingsReads.js)): one
entry per path, shared by every section over it. The Server pane's five
sections over `/api/admin/settings` (the public URL, sign-in, the plan, the
storage defaults, guests) make one request, and what one of them saves the
others show. `useSettingsRead(path, {poll})` gives a section the entry
(`data`, `error`, `set` for what a write answered, `reload`); a read whose
answer is less than three seconds old, or one already on its way, is taken
rather than sent again, and only the newest read sent lands: one asked for
again after a change goes out at once, and an answer that lands after a
write to the same path is older than what the write brought, so it is
dropped. A form over
read values (`useDraftOf`, SettingsKit.jsx) follows them as they are read
again, unless it was edited since.

`PANE_READS` names each pane's reads, in the order they go out: what the
top of the pane shows first, since the browser keeps six requests in
flight to the server. A read may lead to others once it lands (Backups: each
workspace's snapshots), and one marked `later` (the server log, the account
pickers' directory) goes out with the rest without being waited for. They
start together from the pointer or the focus reaching a pane's sidebar
button, so a click mostly finds them in, and from the click itself. A
pane asked for from the sidebar or a search result keeps the one before on
screen, its own button already marked, until its reads are in or 250 ms
have passed (`HOLD_MS`); it then shows with what it has, its placeholders
fading in only after a moment (`.loadingHint`; `Empty`'s and `LogBox`'s
`loading`). A pane whose reads are in shows at once, and its reads go out
again to refresh it in place: a second visit is instant. Settings opened
straight onto a pane (a notice, the chat's AI tiles) shows it at once, its
reads starting together as it mounts. A test checks that every path a
section reads is named in `PANE_READS` (`tests/settingsReads.test.mjs`).
Measured at 150 ms of latency, the Server pane went from 12 requests in five
waves, its sections jumping down four times as the ones above them filled,
to 8 requests in two waves and one switch at 208 ms with every section in
place (at once after a hover).

## Chat settings are global

The chat's shortcuts edit the **same shared preferences** as Settings: the
composer's model chip (model, reasoning effort and speed, a `MenuSelect`
whose further sections list only the picked model's own levels and tiers)
and the header's ⚙ popover (single-paper context budget,
tool permissions, the token counts).
The Tools button and checkbox also edit the global `agentEnabled` preference;
there is no conversation-local tools override or reset on New chat.
Permissions remain scoped by chat kind (folder, PDF, notes), applying to all
chats of that kind. Each is a state menu: Allow, Ask (an approval card before
each call) or Off. Settings compares all three kinds in a table; the chat
popover shows grouped rows for its kind. They share one catalog: the keys,
groups, states, defaults and presets in `chat/chatSettings.js`, the labels and
icons in `AssistantTools.jsx`. The journal-sign-in choice is disabled while
fetching is off, with its value preserved. An approval card's **Always
allow** writes the same preference. **Allow in this chat** is not a setting:
it lasts for one conversation and stays in the browser
([ai.md](ai.md#asking-before-a-call-approvals)).

Reasoning effort, speed, the context budgets, the tools switch and the
permissions are account preferences: they live in the profile and follow the account to
every browser. The chat model stays with the browser, remembered per
provider entry; provider selection and credentials keep their own account
keys. Context presets change the three budgets together: Standard
is 60,000 / 6,000 / 120,000 characters, Larger doubles them, and Custom exposes
the exact values without changing them.

## Settings primitives

[SettingsKit.jsx](../../frontend/src/settings/SettingsKit.jsx) provides `PaneHead`,
`Section`, `Row`, `Toggle`, `SubDialog` and the shared controls. A page one
level down names its way back in `PaneHead`'s `crumbs`, and `action` puts a
control at the head's right end (the workspace Manage page's Open
workspace). Ordinary rows show a small icon, a label, a short hint and a
control, with the shared hover background. A section holds one subject;
its settings stay in it even when a few are stored differently (a row's own
`scope` tag says so) rather than splitting into a second section of the
same subject. Put consequences in the visible hint; supplementary `title`
text appears on hover, without a Details toggle.
Use the existing shared controls: `PictureChoices` for illustrated choices,
`IconChoices` for a small exclusive set pictured as icon tiles (the share
popover's audience, handwriting's "Draws with"), `Segmented` for two or three
short words, `ToggleGroup` for independent chips.
An irreversible action sits in the pane's or dialog's Danger zone
(`Section tone="danger"`; the rules are in [ui-design.md](ui-design.md)
"Dialogs"). Editor dialogs share one layout. A `SubDialog`'s title row
carries the × unless `closeButton={false}`. Each `Field` puts the label on
its own line, one hint line under it (a sentence, capital first) and the
control below. A
`DialogButtons` footer's `footnote` says what a disabled primary button
waits for ("Fill in the server and its token to continue."). Editor dialogs
accept a `draft` value for dismissal protection. See
[ui-design.md](ui-design.md) for shared control styling.

## Verification

`npm test` covers permission presets, search visibility, legacy pane aliases
and the panes' shared reads (`tests/settingsReads.test.mjs`).
After building, `npm run e2e -- --only settings` exercises the actual UI:
preferences and reload, management navigation, prompt/connection draft guards,
shared chat settings, mobile layout, administrator account separation, and
Backups › Off-site copies: an admin's alone, set up in its dialog and read
back there with its write-only secret, the unsaved-edit question on Cancel,
Test against an unreachable endpoint, Copy now against a stand-in bucket
that refuses, and the read-only summary of a second server started with
`GAMMA_S3_BUCKET`.
Use `--keep` to retain desktop/mobile screenshots. For concurrent development,
build into a private directory and set `GAMMA_E2E_DIST` to that directory so
another build cannot replace the assets while the suite runs.

## Storage limits

Two limits per account: max upload size per file (`max_upload_mb`, default
50) and total uploads quota (`quota_mb`, 0 = unlimited). Server-wide defaults
are admin-editable in Settings → Server; per-account overrides (NULL =
inherit) in the Users pane. They apply to the account's personal workspaces
together; a shared workspace has its own optional quota (admins, Settings →
Workspaces / Members & sharing — [workspaces.md](workspaces.md)). On a
hosted container the plan caps all of them, and the storage rows name the
cap ([cloud_accounts.md](cloud_accounts.md) "Hosted containers"). `GET
/api/quota` reports the limits that apply to the current workspace and its
usage — it feeds the pre-upload size check and the shared `QuotaMeter` bar
(account popover, Library maintenance, Users rows, the workspace manager).
Uploads are hard-gated (413 over per-file, 507 over quota); best-effort
caches (proxy save, AI re-download) just skip saving when full. Dedup'd
files (same hash) are always allowed.
