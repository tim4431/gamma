# Backups to Google Drive

Whether the backup tasks that copy their snapshots to an S3-compatible
bucket can also copy them to Google Drive, what Google requires of an app
that writes there, and the design that was picked. Surveyed in November
2026 from Google's documentation and Gamma's backup code
(`gamma/s3.py`, `gamma/offsite.py`, `gamma/snapshot_copies.py`,
`gamma/backup_schedule.py`). How it works now is in
[debugging.md](../dev/debugging.md#google-drive).

## What Google allows

- **There is no key to paste.** Drive has no S3-compatible API, and an API
  key cannot write. A personal Drive is written only on behalf of its
  owner, through OAuth 2.0 with their consent.
- **A service account does not help.** Service accounts have no storage
  quota and cannot own files in a personal My Drive. Google's answer to
  `storageQuotaExceeded` from one is a Workspace shared drive, or OAuth
  for a person. So a JSON key would only work for Google Workspace
  customers with a shared drive.
- **`drive.file` is the scope.** It reaches only the files the app created,
  or the ones a person opened with it through the Picker. That is all a
  backup needs: Gamma makes a folder, fills it, lists it, prunes it,
  and sees nothing else in the person's Drive. Google lists it as
  non-sensitive. `drive` and `drive.readonly` are restricted, which means
  verification plus a yearly third-party security assessment.
  `drive.appdata` is non-sensitive too, but its folder is hidden from the
  person, who could not find or download a copy themselves.
- **The device flow takes `drive.file`.** Google's "TVs and Limited Input
  devices" flow (RFC 8628) accepts only a short list of scopes:
  `openid`, `email` and `profile`, `drive.appdata`, `drive.file`, and two
  YouTube ones. The server asks for a code, the person enters it at
  `google.com/device` on any device, and the server polls the token
  endpoint (`authorization_pending` as a 428, `slow_down`,
  `access_denied`, `invalid_grant` once the code expired). No redirect URI
  is involved.
- **Its client secret is not a secret.** The token request carries the
  client's secret, but Google treats an installed or device client's
  secret as public: "the client secret is obviously not treated as a
  secret". Such a client can ship inside the app, as gcloud's does and
  rclone's (lightly obscured, to keep secret scanners quiet).
- **Refresh tokens last, with exceptions.** One stops working when the
  person revokes it, after six months unused, or (Gmail scopes only) on a
  password change. Each Google account holds at most 100 per client, and
  the oldest goes without warning. A project whose consent screen is still
  *Testing* gets refresh tokens that expire after 7 days, so the project
  must be *In production*. The unverified-app warning and its 100-user cap
  come with sensitive and restricted scopes. Brand verification is
  separate: it decides whether the consent screen shows the app's name and
  logo. Whether it applies to a non-sensitive-only app was not confirmed;
  the consent screen's verification status in the Cloud console says.
- **Granular consent.** A person may grant only some of the scopes asked
  for, so the token response's `scope` must be checked.
- **Uploads resume.** A resumable session (`uploadType=resumable`, the
  session address in `Location`) takes chunks in multiples of 256 KiB but
  the last, lives a week, and answers an empty `PUT` with
  `Content-Range: bytes */<size>` with a `308` and the `Range` it holds.
  A `PATCH` on the upload endpoint replaces an existing file's content.
- **Limits.** 750 GB uploaded per account per day; per-minute quota units
  far above what backups use. A 403 `userRateLimitExceeded` or a 429 is
  retried with exponential backoff (up to 32–64 s).
- **Not confirmed:** whether two OAuth clients of one Cloud project see
  each other's `drive.file` files. Treat the files as belonging to the
  client that made them. A new server restores the copies only when it is
  connected through the same client.

## How the bucket is built

The bucket is a backup target only. `s3.Client` is one bucket under a
prefix, with seven methods: `put_object` (a file, streamed), `get_object`
(into a file, written whole), `put_bytes`, `get_bytes`, `list_objects`
(`(key, size, mtime)` under a prefix), `delete_object` and `check`. Two
features sit on it. The backup tasks' snapshots (`snapshot_copies.py`)
are a task's zips under `snapshots/<ws>/`, pruned to the task's retention.
Admins list, download and restore them, also as a new workspace after a
lost disk. The off-site rounds (`offsite.py`) copy every database and
uploaded file while `GAMMA_S3_BUCKET` is set in the environment.

## Options for the sign-in

1. **The device flow, straight to Google** (picked). The server holds the
   refresh token, encrypted like the bucket's secret key, and renews its
   access token with Google directly. It works the same on the desktop
   app's loopback server, a NAS at any address and a hosted container,
   since nothing comes back to the server's address. The client ID and
   secret ship with Gamma, so connecting is signing in and allowing it.
2. **A broker on Gamma Cloud**, as Zotero's is
   ([cloud_accounts.md](../dev/cloud_accounts.md#connecting-zotero)).
   Gamma Cloud already has a Google web client for sign-in. But a Zotero
   key never expires and a Google access token lasts an hour, and the
   refresh needs the client secret. Every backup would then renew its
   token through Gamma Cloud, which would see every access token and stop
   every server's backups whenever it is down.
3. **The admin's own web client** with the server's redirect URI: a Google
   Cloud project, a consent screen and a public HTTPS address for every
   server. Kept only as the environment override of option 1 (with a
   device client, not a web one).

The code-and-redirect flow of an installed app (loopback redirect, PKCE)
works only when the browser runs on the server's computer, and Google
ended the out-of-band copy-and-paste flow in 2023.

## Design

- **One more destination beside the bucket**, not a replacement.
  `gamma/gdrive.py` is a client with the same seven methods as
  `s3.Client`, written down as one interface (`gamma/backup_store.py`
  `Store`, with the errors both raise), and the module of the tasks'
  copies, renamed `snapshot_copies.py` from `bucket_snapshots.py`, names
  its stores in one table (`DESTINATIONS`); nothing else in it changes. A backup task has *Copy to Google Drive*
  beside *Copy to the bucket*, either or both. The off-site rounds stay
  S3-only: they are set in the environment, and a mirror of every
  uploaded file would fill a 15 GB Drive.
- **Keys become folders.** `snapshots/<ws>/<name>.zip` is the file
  `<name>.zip` in `<folder>/snapshots/<ws>/`, so the person can browse
  their copies in Drive and download one there. Drive names are not
  unique, so a write looks for the name in its folder first and replaces
  that file's content rather than adding a second one. Folder ids are
  cached per client.
- **The server's, like the bucket.** An admin connects it in Settings →
  Backups → Google Drive, and only an admin's tasks copy to it. The
  connection is saved in users.db with the refresh token encrypted by the
  data directory's key, with the account's address, the folder's name and
  the client ID it was made with. Disconnect revokes the token at Google
  and leaves the copies in Drive.
- **The pane drives the polling.** Connect starts the device flow and the
  pane shows the code. While it is open the pane polls the server, which
  asks Google at most once per Google's interval. A code that is never
  entered expires on Google's side. Nothing runs in the background.
- **Gamma's own client, nothing to set up.** Gamma's developer registers
  one "TVs and Limited Input devices" client (`drive.file` only, the app
  *In production*) and ships it (`config.GOOGLE_DRIVE_CLIENT`); an admin
  only clicks Connect…, signs in and allows. Having each server's admin
  register their own client in the Google Cloud console was built and
  dropped: it asked every admin for a developer's chore. A server may
  still use its own from the command line (`manage.py gdrive-client`), as
  the tests do for their stand-in Google.
- **Google's page where Gamma runs on the same computer, a code
  elsewhere.** Google sends a sign-in back only to an address registered
  in advance, except a loopback one for a "Desktop app" client. So the
  desktop app, and any server opened at `localhost`, opens Google's page
  and gets the browser back at its own loopback callback (PKCE); a NAS or
  a VPS opened from another computer shows the code. Google's page
  everywhere would need Gamma Cloud in the middle of every sign-in and
  every hourly renewal (option 2 above), so it was not taken.
- **Plain urllib** on the server's network route (`net_proxy`), through
  one `_request` the tests replace with a fake Drive, as the Zotero
  client's is. The Google client libraries would add about 15 MB for a
  dozen REST calls.

## Still open

- A connection per account, so that a non-admin's tasks copy their own
  workspaces to their own Drive.
- The off-site rounds to Drive, for a small library.
- Shared drives (a Workspace service account, `supportsAllDrives`).
- A run against Google itself: everything here was tested against a fake.

## Sources

- [OAuth 2.0 for TV and limited-input device applications](https://developers.google.com/identity/protocols/oauth2/limited-input-device)
- [Choose Google Drive API scopes](https://developers.google.com/workspace/drive/api/guides/api-specific-auth)
- [Using OAuth 2.0 to access Google APIs](https://developers.google.com/identity/protocols/oauth2) (refresh token expiry, installed apps' secrets)
- [Upload file data](https://developers.google.com/workspace/drive/api/guides/manage-uploads) (resumable uploads)
- [Drive API usage limits](https://developers.google.com/workspace/drive/api/guides/limits)
- [Unverified apps](https://support.google.com/cloud/answer/7454865)
- Service accounts and storage quota: [Google developer forum](https://discuss.google.dev/t/storagequotaexceeded-the-users-drive-storage-quota-has-been-exceeded-for-service-account/104375), [Fivetran's note](https://fivetran.com/docs/activations/destinations/available-destinations/google-drive/troubleshooting/service-accounts-storage-quota)
