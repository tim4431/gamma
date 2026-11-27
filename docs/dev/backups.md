# Backup destinations

Where the backup tasks' snapshots are copied off this server: an
S3-compatible bucket, Google Drive and OneDrive. The tasks' table and
editor are in [settings.md](settings.md#periodic-backup-tasks), the
snapshots in [workspaces.md](workspaces.md#export-and-backups).

## Snapshots and copies in a bucket

Gamma always works on the files in its data directory. An S3-compatible
bucket (AWS S3, Cloudflare R2, MinIO, Backblaze B2) can hold copies of it,
so a lost disk does not take the library with it (`gamma/s3.py`). The
bucket is a backup target only: nothing reads from it except a restore. It
is filled in one of two ways:

- **By backup tasks** (`gamma/snapshot_copies.py`): an admin sets up the
  bucket in Settings → Backups → Backup destinations, and each backup task
  that copies to it (the Bucket chip under *Copy off this server*) sends it
  the workspace snapshots it takes. The bucket has no schedule of its own.
- **By the off-site rounds** (`gamma/offsite.py`,
  [debugging.md](debugging.md#the-off-site-rounds)): when the server's
  environment sets the bucket (`GAMMA_S3_BUCKET`), the server also copies
  every database and uploaded file to it every interval, with no account
  and no task. This is how an operator or a hosted container backs up the
  whole server.

Google Drive and OneDrive can take the backup tasks' snapshots too, beside
the bucket or in its place ([Cloud destinations](#cloud-destinations)
below); the off-site rounds go to the bucket only.

The bucket needs boto3 ([debugging.md](debugging.md#off-site-copies-in-a-bucket)).

**Settings.** Admins set up the bucket in Settings → Backups → Backup destinations
([settings.md](settings.md)): the bucket, the endpoint (empty for AWS; R2's
`https://<account>.r2.cloudflarestorage.com`, MinIO's address; a bucket
behind its own endpoint is addressed by path, and an endpoint pasted with
the bucket's name on its end, as R2's dashboard shows its S3 API, has it
taken off), the region (R2: `auto`), the access key and secret key (both
empty: boto3's own chain, the `AWS_*` variables or an instance role) and
the prefix (put before every key, for a bucket several servers share).
Test lists the bucket once with the form's values, saved or not. The
settings are stored in users.db under the `settings` key `offsite`, the
secret key encrypted with the data directory's key like the shared AI
provider keys. A task reads them as it copies.

**From the environment.** When `GAMMA_S3_BUCKET` is set, every setting
comes from the environment and the pane shows them read-only:
`GAMMA_S3_BUCKET`, `GAMMA_S3_ENDPOINT`, `GAMMA_S3_REGION`,
`GAMMA_S3_ACCESS_KEY`, `GAMMA_S3_SECRET_KEY`, `GAMMA_S3_PREFIX`,
`GAMMA_OFFSITE` (the rounds, on unless `0`, `false`, `no` or `off`),
`GAMMA_OFFSITE_INTERVAL` (seconds, default 3600, at least 60) and
`GAMMA_OFFSITE_KEEP` (default 7). Backup tasks copy to that bucket too.

## The backup tasks' snapshots

A task that copies to the bucket (only an admin's: the bucket is the
server's) uploads each snapshot it takes, whole, to
`<prefix>snapshots/<workspace>/<name>.zip`, the snapshot's own name, and
writes `<prefix>snapshots/<workspace>/workspace.json` (`{id, name, kind}`)
beside it, which names a workspace a new server does not have. The task's
retention then prunes its copies in the bucket as it prunes its snapshots
on the server (`snapshot_copies.prune`): the newest are kept, the copies
of other tasks are never touched. A copy that fails (the bucket refuses,
or no bucket is set up any more) leaves the snapshots on the server and
fails the run with the reason (`last_error`, the `backup-failed` notice);
the task keeps its schedule rather than retrying an hour later, since the
snapshots were taken.

Settings → Backups › Saved snapshots lists what the bucket holds for an
admin (`GET /api/admin/offsite/snapshots`, one listing of `snapshots/`): a
snapshot also in the bucket is tagged *in the bucket*, and a copy only the
bucket holds (its snapshot deleted or pruned here) is a row of its own,
which is downloaded (the `bucket-download` job), restored into its
workspace (`bucket-restore`, replace or merge, as a stored snapshot
restores) or deleted from the bucket. Backups › Cloud copies
(`CloudCopies.jsx`) lists what each store holds and the workspaces this
server does not have, under the name `workspace.json` recorded; each comes
back as a new workspace (`bucket-restore` with `into: "new"`): the copy's
own id when it is free, its name and kind from the copy's manifest, the
admin its owner. Restore all… brings back the ones picked, each from its
newest copy, in one job (`bucket-restore-many`, kind `copies-restore`):
one after another, a copy that fails reported while the rest go on (its
workspace removed again), a stop taking effect between workspaces, never
inside one. That is the way back after a lost disk: set up the same
destination (the bucket, Google Drive, OneDrive) on the new server and
restore all; members are
invited again. Its Browse… lists every copy by workspace, by store and
found by name. Deleting a task, or a workspace, leaves its copies in the
bucket.

## Cloud destinations

The backup tasks' snapshots can also go to Google Drive and to OneDrive
(why each is built so, and why iCloud Drive cannot take them:
[google-drive-backup.md](../research/google-drive-backup.md),
[onedrive-icloud-backup.md](../research/onedrive-icloud-backup.md)). A task
copies to either as it copies to the bucket, and to several at once.
Every store's client is a backup store (`gamma/backup_store.py`: the
`Store` interface of seven methods, `StoreError` for a call refused or
unanswered, `StoreConfigError` for none that can work, the rule for a key),
and `snapshot_copies.DESTINATIONS` names them (`bucket`, `drive`,
`onedrive`) with what a message calls each, why nothing can be
copied there while it is not set up, whether it is and its client. The
scheduler, the routes and the jobs read a store from there; a task has one
switch per store (`TaskInput`), each checked when it is saved and copied
to in turn when it runs. A copy that fails fails the run with
`last_error` naming that store, after the other stores were tried. The
pane's words for each store are `frontend/src/settings/backupStores.js`:
one entry per store (its name as a label, inside a sentence and at a
sentence's start; the read that says how it is set up; where that puts
its copies), and every sentence about a store written once with its name
in it. The off-site rounds go to the bucket only.

Google Drive and OneDrive are signed in to with OAuth, and share all of it:

- `gamma/cloud_http.py`: the one transport (plain urllib on the server's
  network route, [network_proxy.md](network_proxy.md); `_request`, which
  the tests replace), how a refusal reads (Google's, Graph's and OAuth's
  error shapes), and the retries: a 429, a 5xx, a rate-limit refusal or no
  answer is tried again after an exponential pause with jitter, or the
  service's `Retry-After`, at most 5 times (Test: once). A redirect to
  another host drops the Authorization header. `call` renews the access
  token once on a 401. A refusal raises `CloudError` (an OSError), no
  connection that can work `CloudConfigError` (a ValueError), as the
  bucket's errors are.
- `gamma/cloud_oauth.py`: a provider is a `Service` (its endpoints, the
  scopes asked for and the one a grant must hold, whether its client sends
  a secret, whether it can revoke). The device flow (RFC 8628), the
  authorization page with PKCE for a browser on the server's own computer,
  the refresh (a new refresh token kept where the service rotates them)
  and the revocation are written once for both.
- `gamma/cloud_account.py`: one `CloudAccount` per cloud (`CLOUDS`
  describes each, `ACCOUNTS` holds them by store key) keeps the connection:
  connect, poll, the folder, browse, Test, disconnect, the server's own
  client from `manage.py cloud-client`. The routes are one set for every
  cloud, `/api/admin/clouds/{store}` (`routers/admin.py`).

**Connecting.** Only an admin connects a cloud, in Settings → Backups →
Backup destinations, and only an admin's tasks copy to it, as with the
bucket. Copies go to the folder `Gamma backups` (Edit… chooses another).
Where Gamma is opened on its own computer — the desktop app, or any server
opened at a loopback address over plain HTTP — Connect… opens the service's
authorization page (`POST /api/admin/clouds/{store}/authorize`, PKCE): the
person signs in, allows, and the service sends the browser back to
`/api/admin/clouds/{store}/callback` at a loopback address (Google: the one
Gamma was opened at; Microsoft: `localhost`, the address its app
registers, on the same port), which needs no session (the desktop app's
system browser has none): the state, used once within 10 minutes, names
the sign-in, and the code is traded with its verifier. The dialog reads
the row again every 2 s until the connection is there. Anywhere else a
service sends a sign-in back only to an address registered in advance, so
Connect… starts its device flow: the service answers a code and its
address (`google.com/device`, `microsoft.com/devicelogin`), which the
dialog shows with one button that copies the code and opens the page. The
person enters the code there on any device, signed in to the account to
back up to, and allows access. While the dialog is open it posts
`/api/admin/clouds/{store}/poll`, which asks the service at most once per
the interval it gave (5 s, 5 more on each `slow_down`). No answer or a 5xx
waits for the next interval. A refusal, a grant without the scope needed
or an expired code ends the attempt with the reason. Closing the dialog
stops waiting. The code in progress is kept in memory only, so a restart
forgets it.

**What is saved.** Once the service says yes, the account's address is
read and the connection is stored in users.db under the cloud's
`settings` key (`gdrive`, `onedrive`): the refresh token, encrypted with
the data directory's key as the bucket's secret key is; the address and
name; the folder; when it was made; and the OAuth client's ID. An access
token lasts an hour; one is kept in memory per cloud and renewed when
under a minute is left. A new connection replaces the old one and revokes
its token where the service can. Edit… changes the folder new copies go to
(copies already in the old folder stay there) or disconnects, which
revokes the token where the service can and forgets it. The copies stay.

**The OAuth clients.** Gamma ships its own app for each cloud, so an admin
only signs in and allows it; a build without one reads *Not available*. A
server may use its own instead, given on the command line (`manage.py
cloud-client drive [--desktop] <id> <secret>`, `manage.py cloud-client
onedrive <id>`, `--remove` to go back; users.db `settings` keys
`gdrive_client`, `gdrive_desktop_client` and `onedrive_client`, a secret
encrypted as the token is), which the browser suite does for its stand-in
services. A refresh token works only with the client that made it, so a
connection made through another client reads "connected through another
OAuth client" and must be made again; a change of client disconnects at
once and ends a code waiting.

**The folder.** A cloud's folder is a path of names from its top (`Gamma
backups`, or `Backups/Gamma` for a folder in a folder;
`CloudAccount.check_folder`: names trimmed, none `.`, `..` or holding a
backslash or (OneDrive) one of `" * : < > ? |` or a dot at the end, at
most 16 deep, never the top itself). A key's path is a folder path under
it: `snapshots/<ws>/<name>.zip` is `Gamma backups/snapshots/<ws>/<name>.zip`,
so the person can find and download a copy there. Folders are made as a
write needs them. The Edit dialog's folder browser lists a folder from the
top (`GET /api/admin/clouds/{store}/browse`, `Client.browse`: one page) and
makes one (`POST /api/admin/clouds/{store}/folders`, `Client.make_folder`).
A bucket's prefix is chosen the same way (`POST
/api/admin/offsite/browse`, `s3.Client.browse`: one listing split at `/`
from the bucket's top, with the dialog's settings, saved or not); a bucket
has no folders of its own, so a new one is only drawn until a copy is
saved under it.

**Tests.** `tests/test_gdrive.py` (Google, and what every cloud does
alike) and `tests/test_onedrive.py` (what Microsoft does differently),
against in-memory services
(`tests/gdrive_fake.py`, `tests/onedrive_fake.py`) that
`tests/cloud_fake.py` routes the transport to by address. The transport
itself is tested against a local HTTP server. The browser suite runs the
same stand-ins over HTTP: Google's code on a server served over HTTPS, as a
NAS opened from another computer is; Google's page and Microsoft's page
(back at `localhost`) on one at 127.0.0.1; a task copying to OneDrive.

## Google Drive

A folder in a Google account's My Drive (`gamma/gdrive.py`). The scope is
`drive.file` alone: Gamma sees and changes only the files it made, never
the rest of the Drive. Gamma ships two clients of one registration:
`config.GOOGLE_DRIVE_CLIENT`, of Google's "TVs and Limited Input devices"
type, for the code, and `config.GOOGLE_DRIVE_DESKTOP_CLIENT`, a "Desktop
app" one, for Google's page. Gamma's developer registered them once, in the
Google Cloud project Gamma Cloud's Google sign-in uses, whose consent
screen shows Gamma PDF, its logo, and gammapdf.com's privacy policy and
terms. Google does not treat either secret as secret; each is two literals
in the source so secret scanners pass it by. The client's Google Cloud
project must be *In production*: one still in *Testing* gets refresh tokens
that expire after 7 days. A project without the Drive API enabled is
refused with Google's own message, which links the page that enables it.
`GAMMA_GOOGLE_ACCOUNTS_BASE`, `GAMMA_GOOGLE_OAUTH_BASE` and
`GAMMA_GOOGLE_API_BASE` point at stand-ins for a test.

Drive reaches a file by id, not by path, so folders are walked a name at a
time and their ids kept for the client's life (one task run or one
listing). Drive names are not unique, so a write first looks for its name
in its folder and replaces that file's content (the oldest of several);
the folder browser shows the oldest of several folders of one name. With
`drive.file`, a folder of the same name the person made is not seen, and
one they trashed is not used: a new one is made; the folder browser lists
only what Gamma made. An upload is a resumable session sent in 8 MiB
chunks; after a chunk got no answer or a 5xx, Drive is asked how much it
holds and the upload resumes there, and a session Drive forgot starts over
once. Deleting a copy deletes it for good, not to the trash, which would
still count against the account's storage. A full Drive fails the copy
with "the Google account's storage is full"; a token Google no longer
honours (revoked by the person, or unused for six months) fails with
"connect Google Drive again". Because `drive.file` reaches only the
client's own files, a new server finds the copies (the way back after a
lost disk) only when connected through the same client. What was tried
against Google itself is in
[google-drive-backup.md](../research/google-drive-backup.md#still-open).

## OneDrive

A folder in a Microsoft account's OneDrive, personal or work and school
(`gamma/onedrive.py`, through Microsoft Graph). Gamma's app,
`config.ONEDRIVE_CLIENT_ID`, is one public client of the Microsoft
identity platform, which has no secret and does both ways of connecting
(the `common` tenant). Gamma's developer registered it once, as *Gamma
PDF*, in the Microsoft Entra admin center of a directory of their own
(Entra ID → App registrations → New registration): any Entra ID tenant
and personal Microsoft accounts; a "Public client/native (mobile &
desktop)" redirect URI `http://localhost/api/admin/clouds/onedrive/callback`
(the port is not matched; the path is); Authentication → Settings →
*Allow public client flows* on, for the code; API permissions, delegated:
`Files.ReadWrite`, `User.Read`, `offline_access`, no admin consent; no
secret. A build without it reads *Not available*, and a server may use its
own app (`manage.py cloud-client onedrive <id>`). `GAMMA_MICROSOFT_LOGIN_BASE` and
`GAMMA_MICROSOFT_GRAPH_BASE` point at stand-ins for a test.

The scope is `Files.ReadWrite`, the person's OneDrive, of which Gamma
touches only the folder chosen: `Files.ReadWrite.AppFolder` (an
`Apps/Gamma` folder of its own) is documented for personal accounts alone,
and Microsoft warns against large files in it. So the folder browser lists
every folder, and the copies may go in one the person made. Microsoft
renews a refresh token on every use, the old one still good: the new one
is saved in place of the old as it comes. It has no endpoint an app may
revoke a token at, so Disconnect forgets the token and its question says
to remove Gamma from the account's apps to revoke it too (the read's
`revokes` is false).

Graph reaches a file by its path (`/me/drive/root:/Gamma
backups/snapshots/<ws>/x.zip`), and a folder's names are unique ignoring
case, so a write replaces what is there (`conflictBehavior=replace`).
Folders are made a name at a time before a write (Microsoft does not say
that a write by path makes them), one in the way that is a file refused.
A file up to 4 MiB goes in one PUT; a larger one in an upload session, in
10 MiB chunks (a multiple of the 320 KiB Graph asks for), sent to the
session's address without the token; after a chunk got no answer, a 5xx or
a 416, the session is asked which byte it expects next, and a session
Graph forgot starts over once. A download is redirected to a pre-signed
address on another host, reached without the token. Deleting a copy asks
for a permanent delete (`permanentDelete`); a drive that does not take it
(Graph answers 400, 403, 405 or 501) gets a plain delete instead, to the
recycle bin, which empties itself, and the client asks no more for the
rest of its run. A personal OneDrive refuses it (400), so there a copy the
retention prunes sits in the recycle bin until it empties. Retries honour
Graph's `Retry-After`. A full OneDrive fails the copy with "the Microsoft
account's storage is full"; a token Microsoft no longer honours
(`invalid_grant`, `interaction_required`: the app removed, unused for 90
days, a password reset) fails with "connect OneDrive again". What was
tried against Microsoft itself, and what was not (a work or school
account), is in
[onedrive-icloud-backup.md](../research/onedrive-icloud-backup.md#still-open).
