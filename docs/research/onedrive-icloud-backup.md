# Backups to OneDrive and iCloud

Whether the backup tasks that copy their snapshots to a bucket and to
Google Drive can also copy them to OneDrive and to iCloud Drive, what
Microsoft and Apple offer an app that writes there, and the design that was
picked, with the shared layer the clouds now sit on. Surveyed in November
2026 from Microsoft's and Apple's documentation (sources below) and the
Google Drive backup ([google-drive-backup.md](google-drive-backup.md)). How
it works now is in [debugging.md](../dev/debugging.md#cloud-destinations).

## What Microsoft allows

- **A public client, no secret.** An app of the Microsoft identity platform
  registered as a public client ("Mobile and desktop applications") has no
  secret, and one that sends a secret is refused (AADSTS700025). So Gamma
  can ship its app's ID and nothing else; there is no "secret that is not a
  secret" to hide from scanners, as Google's installed clients have.
- **The device flow takes personal accounts.** `POST
  /{tenant}/oauth2/v2.0/devicecode` with the `common` tenant takes personal
  and work or school accounts alike, once the registration has *Allow
  public client flows* on. The answer names its page `verification_uri`
  (Google: `verification_url`) and a code lasts 15 minutes. Polling answers
  `authorization_pending`, `authorization_declined` (Google:
  `access_denied`), `bad_verification_code` and `expired_token`; Microsoft's
  page does not list `slow_down`, which RFC 8628 has, so it is handled as
  Google's is.
- **The sign-in page comes back to `localhost`, any port.** For a native
  app's redirect URI, the port of `http://localhost` is not matched, but the
  path is, and exactly. `http://127.0.0.1` is allowed too but only through
  the manifest, and whether its port is ignored as well is not said. So the
  app registers `http://localhost/api/admin/clouds/onedrive/callback` and
  the server sends the browser back to `localhost` on the port it was opened
  at, whatever host that was.
- **Refresh tokens rotate and cannot be revoked by the app.** One lasts 90
  days and is renewed on every use: each refresh answers a new one, and
  the old one is not revoked (Microsoft still says to keep the new one).
  The identity platform has no RFC 7009 endpoint; `revokeSignInSessions`
  revokes all of a person's sessions, is not for personal accounts and needs
  an admin permission. So Disconnect forgets the token, and the person
  removes the app from their account to revoke it. A token no longer
  honoured answers `invalid_grant` (AADSTS70008, 700082 inactivity, 50173 a
  password reset, 65001 consent removed) or `interaction_required`.
- **Scopes.** `Files.ReadWrite.AppFolder` would be the counterpart of
  Drive's `drive.file`: a folder of the app's own (`Apps/<app name>`), made
  on first use, visible to the person. But the Graph permissions reference
  and the OneDrive permissions page say it is valid for personal accounts
  only (a newer concept page says it works for work and school too, and
  community answers say it did not), and Microsoft calls the app folder a
  place for settings and drafts where "placing large files might cause
  issues". `Files.ReadWrite`, the person's own files, works on both kinds
  of account and needs no admin consent by default (a tenant may still
  restrict a user's consent). `User.Read` is needed for `/me` (the address:
  `mail`, which may be null, else `userPrincipalName`), `offline_access` for
  a refresh token.
- **Items by path.** `/me/drive/root:/a/b.zip:/content`,
  `…:/children`, `…:/createUploadSession`, each name percent-encoded on its
  own; names are unique in a folder, ignoring case. Personal OneDrive
  refuses `/ \ * < > ? : |` in a name, work OneDrive `#` and `%` too, and no
  name may end in a dot. Whether a write by path makes the folders above it
  is not documented: Gamma makes them.
- **Uploads.** One `PUT …/content` takes up to 250 MB, but Microsoft advises
  a session above 10 MiB. A session's chunks are under 60 MiB, each a
  multiple of 320 KiB, in order, and carry no Authorization header (one
  that does may be refused). A chunk is answered 202 with
  `nextExpectedRanges`, the last 200 or 201 with the item; a range the
  session has already is a 416; `GET` on the session's address says where
  it stands; a session gone answers 404 and the upload starts over.
- **Downloads** answer 302 to a pre-signed address that needs no token.
- **Deleting** sends an item to the recycle bin. `permanentDelete` is
  documented for personal accounts with `Files.ReadWrite`, but a personal
  OneDrive refused it with a 400 in the live run (below); whether the
  recycle bin counts against the storage is not said (the quota resource
  has a `deleted` figure beside `used`).
- **Throttling** answers 429 with `Retry-After`; 503 is retried with a
  pause too.

## What Apple allows

- **No API writes into iCloud Drive.** Nothing in Apple's documentation
  lets a server, or a web page, put a file in a person's iCloud Drive.
- **CloudKit is not iCloud Drive.** CloudKit Web Services and CloudKit JS
  reach the records of an app's own CloudKit container (made in Xcode, so
  under an Apple Developer Program account). A person's private database
  takes a `ckWebAuthToken` from Apple's sign-in in a browser, which is used
  once (each answer brings the next) and lasts 30 minutes, or two weeks with
  "Keep me signed in": no use to a server that copies every night.
  Server-to-server keys reach the public database only. Assets were capped
  at 50 MB a field when the reference was last updated, and they are data on
  records, which the person never sees in iCloud Drive or the Files app.
- **What does reach iCloud Drive** is a native app's ubiquity container
  (`url(forUbiquityContainerIdentifier:)`) and the folder iCloud's own app
  syncs: `~/Library/Mobile Documents/com~apple~CloudDocs` on a Mac,
  `%USERPROFILE%\iCloudDrive` with iCloud for Windows.

## Options

1. **OneDrive through Graph with plain urllib** (picked), as Google Drive
   already was, rather than MSAL (about 1 MB with its dependencies) for a
   handful of calls. The device flow and the sign-in page are both standard
   OAuth, so they are written once for both clouds (below).
2. **`Files.ReadWrite.AppFolder`** instead of `Files.ReadWrite`: tighter,
   but documented for personal accounts only and advised against for large
   files. Not taken; the folder browser then sees every folder, so the copies
   may go in one the person made, which Drive's `drive.file` does not allow.
   It is one constant (`onedrive.SCOPES`) and the folder browser's top, if
   Microsoft settles it.
3. **iCloud through CloudKit**: needs Apple's sign-in in a browser every
   two weeks at best, an Apple Developer container, records not files, and
   the person cannot see the copies. Not taken.
4. **iCloud through a synced folder**: a store of a folder on the server's
   disk, which iCloud's app (or Dropbox's, or a network share) carries on.
   It was built and taken out again before it shipped. The store itself was
   small, but as an answer to iCloud it reaches iCloud only where the
   server's own computer runs iCloud's app (the desktop app on a Mac, or
   with iCloud for Windows), never the NAS, Docker or VPS servers Gamma
   mostly runs on; making it read as iCloud took guessing each platform's
   sync folders (`~/Library/Mobile Documents/com~apple~CloudDocs`,
   `~/Library/CloudStorage/*`, `%OneDrive%`), and choosing it meant typing
   a path on the server's computer into a page often opened from another.
   A local-disk destination presented as iCloud is not one; not taken.
5. **The iPad app's ubiquity container**: the iPad app could write the
   copies into its own iCloud container, shown in iCloud Drive. Left open:
   it is a native change, and the server there runs on the iPad itself.

## Design

- **One layer for the clouds.** `gamma/cloud_http.py` (the transport, the
  retries, the errors), `gamma/cloud_oauth.py` (a `Service` per provider;
  the device flow, the page with PKCE, refresh with rotation, revocation
  where there is one) and `gamma/cloud_account.py` (one `CloudAccount` per
  cloud: connect, poll, the folder, browse, Test, disconnect; `CLOUDS`
  describes each) were drawn out of the Google Drive code, which shrank to
  Google's `Service` and its Drive client. OneDrive is a `Service`, a Graph
  client of the same seven `Store` methods and an entry in `CLOUDS`. A store
  client takes an `auth(renew)` function rather than a refresh token, so the
  account alone renews tokens and saves a rotated one.
- **One set of routes**, `/api/admin/clouds/{store}`, in place of Drive's
  `/api/admin/gdrive`; `manage.py cloud-client <store>` in place of
  `gdrive-client`. The users.db keys of Drive's connection stayed as they
  were (`gdrive`, `gdrive_client`, `gdrive_desktop_client`).
- **Three destinations in one list.** `snapshot_copies.DESTINATIONS` holds
  the bucket and the clouds; a task has one switch per store. In the pane, Backup destinations lists a row per store (state,
  where, Connect… / Set up…, Test, Edit…), a task's editor has one *Copy
  off this server* row of chips, and `frontend/src/settings/backupStores.js`
  says every sentence of a store once, with its name in it, rather than once
  per store.
- **No iCloud.** Settings offers no iCloud destination (option 4 above);
  the user guide says Apple gives no server a way to write there.

## Still open

- **Publisher verification.** Gamma's Microsoft app is registered
  ([debugging.md](../dev/debugging.md#onedrive)) but not verified: a
  verified publisher (a Microsoft Partner Network ID and a verified domain)
  would keep work tenants from showing it as unverified.
- **A work or school account.** The live run (2026-11-22) used a personal
  account: the code, Microsoft's page back at `localhost`, Test, a task's
  copy listed, downloaded and restored, an upload session of three chunks
  to the pre-signed host without the token, and Disconnect all worked;
  `permanentDelete` answered 400, so pruned copies go to the recycle bin.
  Not yet seen: a work tenant's consent to `Files.ReadWrite` (its policy
  may ask an admin), whether a write by path makes its folders (Gamma
  makes them first), `slow_down`.
- **A slow address.** On the developer's network one of
  `graph.microsoft.com`'s eight addresses did not answer, and Python's
  connection, trying them in DNS order, waited the 21 s Windows gives a
  connect before moving on, so some calls took over 20 s. Any outbound call
  meets it; trying the next address sooner, or remembering one that failed,
  belongs in `gamma/net_proxy.py`, the server's one network route.
- iCloud from the iPad app's own ubiquity container.
- A connection per account, as for Google Drive.

## Sources

- [OAuth 2.0 device authorization grant](https://learn.microsoft.com/en-us/entra/identity-platform/v2-oauth2-device-code)
- [Public and confidential client apps](https://learn.microsoft.com/en-us/entra/identity-platform/msal-client-applications)
- [Redirect URI (reply URL) best practices and limitations](https://learn.microsoft.com/en-us/entra/identity-platform/reply-url)
- [OAuth 2.0 authorization code flow](https://learn.microsoft.com/en-us/entra/identity-platform/v2-oauth2-auth-code-flow)
- [Refresh tokens in the Microsoft identity platform](https://learn.microsoft.com/en-us/entra/identity-platform/refresh-tokens)
- [Microsoft Entra authentication and authorization error codes](https://learn.microsoft.com/en-us/entra/identity-platform/reference-error-codes)
- [Microsoft Graph permissions reference](https://learn.microsoft.com/en-us/graph/permissions-reference), [OneDrive permissions](https://learn.microsoft.com/en-us/onedrive/developer/rest-api/concepts/permissions_reference?view=odsp-graph-online), [App folder](https://learn.microsoft.com/en-us/graph/onedrive-sharepoint-appfolder)
- [Create an upload session](https://learn.microsoft.com/en-us/graph/api/driveitem-createuploadsession?view=graph-rest-1.0), [Upload small files](https://learn.microsoft.com/en-us/graph/api/driveitem-put-content?view=graph-rest-1.0)
- [Download a file](https://learn.microsoft.com/en-us/graph/api/driveitem-get-content?view=graph-rest-1.0), [Delete](https://learn.microsoft.com/en-us/graph/api/driveitem-delete?view=graph-rest-1.0), [permanentDelete](https://learn.microsoft.com/en-us/graph/api/driveitem-permanentdelete?view=graph-rest-1.0), [quota](https://learn.microsoft.com/en-us/graph/api/resources/quota?view=graph-rest-1.0)
- [Addressing a driveItem](https://learn.microsoft.com/en-us/onedrive/developer/rest-api/concepts/addressing-driveitems?view=odsp-graph-online)
- [Microsoft Graph throttling](https://learn.microsoft.com/en-us/graph/throttling)
- [Get a user](https://learn.microsoft.com/en-us/graph/api/user-get?view=graph-rest-1.0)
- [CloudKit Web Services Reference](https://developer.apple.com/library/archive/documentation/DataManagement/Conceptual/CloudKitWebServicesReference/index.html), [CloudKit JS](https://developer.apple.com/documentation/cloudkitjs), [CKAsset](https://developer.apple.com/documentation/cloudkit/ckasset), [Configuring iCloud services](https://developer.apple.com/documentation/xcode/configuring-icloud-services)
