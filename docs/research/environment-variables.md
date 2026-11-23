# Environment variables

What the Gamma server reads from its environment, sorted by whether it
belongs there. The rule this note measures against: a setting belongs in
Settings; an environment variable is for what a server needs before anyone
can sign in, or for a server-wide service that runs with nobody attending.
Read from the code (`backend/gamma/`, `backend/app.py`, `manage.py`,
`gamma_mobile.py`, `docker-entrypoint.sh`, the `Dockerfile`) in November
2026: 61 `GAMMA_*` variables and a few standard ones. The text recognition
of scanned PDFs, merged after the survey, measured against the same rule:
its three switches are server options (`ocr`, `ocr_auto`, `ocr_workers`,
Server → Text recognition), and three variables stay, in the tables below
(`GAMMA_OCR`, `GAMMA_TESSDATA_DIR`, `GAMMA_OCR_ENGINE`).

What was decided: everything a person might want to change moved into
Settings, except what the public demo, the account server (and its fleet)
and the share host set for themselves, which are the unattended services
the rule allows. Moved: Google Drive's OAuth client (now Gamma's own,
shipped with the code, so connecting Drive is signing in;
[google-drive-backup.md](google-drive-backup.md); OneDrive's the same,
[onedrive-icloud-backup.md](onedrive-icloud-backup.md)) and the Zotero app
(Settings → Server → Zotero sign-in, each server's admin registering
their own); and the feature settings below ("Moved into
Settings"), now server options (`gamma/server_options.py`, one users.db
record, `manage.py set-option` for a script or the browser suite). The
overrides were not limited to hosted containers, as first proposed: the
demo (`GAMMA_DEMO`, `GAMMA_GUEST_TTL_HOURS`, `GAMMA_GUEST_MAX`,
`GAMMA_GUEST_SEED`) and the share host (`GAMMA_CLOUD_SHARE_HOST`) set
theirs in their own environment, outside the fleet.

## Infrastructure and unattended services (fit the rule)

| Variable | What it does | When unset |
|---|---|---|
| `GAMMA_ADMIN_PASSWORD` | Password of the admin a fresh instance creates | random, logged once |
| `GAMMA_ADMIN_USER` | Username of that first admin, while no real account exists | `admin` |
| `GAMMA_DATA_DIR` | Where users.db, workspaces, backups and agents live | `data/` (`/data` in Docker) |
| `GAMMA_MCP_ALLOWED_HOSTS` | Extra host names MCP accepts, comma-separated | none |
| `GAMMA_PORT` | Listen port (the entrypoint and `gamma_mobile.py`) | 9001 |
| `GAMMA_PUBLISHER_SESSION_KEY` | Key that encrypts stored publisher sessions | a key file in the data dir |
| `GAMMA_STATIC_DIR` | The built web app the server serves | API only (`/app/static` in Docker) |
| `GAMMA_TESSDATA_DIR` | Where the text recognition models are | `backend/tessdata` (`/app/tessdata` in Docker, the desktop bundle's) |
| `GAMMA_TRUSTSTORE` | `0` uses OpenSSL's authorities instead of the system's | on |
| `GAMMA_OFFSITE` | The off-site rounds (every database and upload to the bucket) on or off; no Settings control | on, once `GAMMA_S3_BUCKET` is set |
| `GAMMA_OFFSITE_INTERVAL` | Seconds between rounds | 3600 |
| `GAMMA_OFFSITE_KEEP` | Copies kept per database | 7 |
| `PUID` / `PGID` | Who owns the Docker data volume (entrypoint) | root |
| `FORWARDED_ALLOW_IPS` | Proxies whose `X-Forwarded-*` uvicorn trusts | 127.0.0.1 |

## Overrides of a Settings value (the setting turns read-only)

A hosted container gets most of these from the fleet, which is the
unattended case; on a self-hosted server each repeats something an admin
can set in Settings.

| Variable | Settings place | When unset |
|---|---|---|
| `GAMMA_PUBLIC_URL` | Server → Public server URL | the saved one |
| `GAMMA_CLOUD_ISSUER`, `_POLICY`, `_CLIENT_ID`, `_CLIENT_SECRET` | Users → Sign-in | the saved ones |
| `GAMMA_CLOUD_SHARE_HOST` | Sign-in → Accept published pages | off |
| `GAMMA_DEMO` | Users → Guests → Demo mode (the public demo) | the saved one |
| `GAMMA_GUEST_TTL_HOURS` | Users → Guests → Guest workspaces last (the public demo) | the saved one |
| `GAMMA_GUEST_MAX` | Users → Guests → Most guests at once (the public demo; 0 on every hosted container) | the saved one |
| `GAMMA_OCR` | Server → Text recognition → Recognize text in scanned PDFs (a hosted container's fleet variables turn it on there; the desktop's engine check, so it opens no users.db) | the saved one; off on a hosted container and in demo mode |
| `GAMMA_S3_BUCKET`, `_ENDPOINT`, `_REGION`, `_ACCESS_KEY`, `_SECRET_KEY`, `_PREFIX` | Backups → Bucket (set, every field comes from here; also the off-site rounds' bucket) | the saved bucket |

## Moved into Settings

| Was | Now | Default |
|---|---|---|
| `GAMMA_METADATA_LOOKUP` | Server → Online services → Paper lookups | on |
| `GAMMA_MODEL_CATALOG` | Server → Online services → AI model catalog | on |
| `GAMMA_VAULT_KEEP_MB` | Server → Work folders → Largest file a work folder keeps | 64 MB |
| `GAMMA_SEARXNG_URL` | AI → Tool usage → Online search for every account | none |
| `GAMMA_OPENALEX_API_KEY` | the same, the key write-only and encrypted | none |
| `GAMMA_AI_ANTHROPIC_BASE_URL`, `_OPENAI_`, `_CHATGPT_`, `GAMMA_AI_BASE_URL` | gone: a connection's own base URL (AI → Models) | the providers' own |
| `GAMMA_ZOTERO_CLIENT_KEY`, `_SECRET` | Server → Zotero sign-in | Gamma Cloud's broker |

## Kept in the environment, and why

| Variable | What it does | Why it stays |
|---|---|---|
| `GAMMA_GUEST_SEED` | A workspace zip every new guest gets | the public demo's |
| `GAMMA_FREE_PAGE_LIMIT` | Published pages on the share host's free plan | the share host's |
| `GAMMA_PAGE_HOST` | The share host's per-account page hostname pattern | the share host's |
| `GAMMA_AGENT_CLAUDE`, `GAMMA_AGENT_CODEX` | The command that starts Claude Code or Codex | which program the desktop runs is changed only through the desktop app, which asks the person at the computer; a field a page could save would let a web page choose it. People's agents are found on the PATH and in the installers' places; the variables are the tests' fakes |
| `GAMMA_AGENT_RUN_TIMEOUT` | Seconds before a local agent's turn is stopped | a test hook (the tests shorten it) |
| `GAMMA_COMPUTER_GRACE_S` | Seconds a computer counts as online after its last poll | a test hook |

## Set by Gamma's own packaging, not a person

`GAMMA_VERSION`, `GAMMA_COMMIT`, `GAMMA_BRANCH` (the build),
`GAMMA_UPDATES` and `GAMMA_UPDATE_CHECK` (who installs updates),
`GAMMA_SHELL_SECRET` and `GAMMA_FOLDER_LINKS` (the desktop app),
`GAMMA_CLOUD_DEFAULT_ISSUER` (desktop, Android, iPad), `GAMMA_HOSTED` and
`GAMMA_CLOUD_ADMIN_SUBJECT` (the fleet). The shells also set the data
dir, static dir, port and first admin.

## Test and developer hooks

`GAMMA_GOOGLE_OAUTH_BASE`, `GAMMA_GOOGLE_API_BASE`, `GAMMA_MICROSOFT_LOGIN_BASE`,
`GAMMA_MICROSOFT_GRAPH_BASE`, `GAMMA_ZOTERO_API_BASE`,
`GAMMA_ZOTERO_STREAM_URL`, `GAMMA_ZOTERO_WEB_BASE` (stand-ins for the real
services), `GAMMA_LOCAL_AGENTS` (computer agents forced on or off),
`GAMMA_SYNC_INTERVAL` (0 stops the sync loops), `GAMMA_OCR_ENGINE`
(`window`: a server with the recognition engine leaves every reading to
the window, as the apps do).

## Standard variables

`SSL_CERT_FILE` / `SSL_CERT_DIR` (OpenSSL; `app.py` points the first at
certifi's bundle where the system has none), `HTTP_PROXY` / `HTTPS_PROXY`
/ `NO_PROXY` (urllib's proxy lookup), `AWS_*` (boto3, without
`GAMMA_S3_*` keys), `OMP_THREAD_LIMIT` (Tesseract's threads a page; the
Docker image and the desktop app set 1), `CLAUDE_CONFIG_DIR` and `CODEX_HOME` (where the agents
keep sessions), `LOCALAPPDATA`, `USERPROFILE` and the Windows system
folders (where agent CLIs are looked for, where a vault may not go).

The account server (`cloud/`) reads about 37 of its own, almost all
`GAMMA_CLOUD_*`, and the fleet agent 6 `GAMMA_FLEET_*` and an optional
`GAMMA_FLEET_S3_*` group ([dev/hosted.md](../dev/hosted.md)); both are
unattended services and outside this survey.
