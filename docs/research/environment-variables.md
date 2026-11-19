# Environment variables

What the Gamma server reads from its environment, sorted by whether it
belongs there. The rule this note measures against: a setting belongs in
Settings; an environment variable is for what a server needs before anyone
can sign in, or for a server-wide service that runs with nobody attending.
Read from the code (`backend/gamma/`, `backend/app.py`, `manage.py`,
`gamma_mobile.py`, `docker-entrypoint.sh`, the `Dockerfile`) in November
2026: 61 `GAMMA_*` variables and a few standard ones. Google Drive's
client is not among them: each server's admin registers one and saves it
in Settings → Backups → Google Drive
([google-drive-backup.md](google-drive-backup.md)).

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
| `GAMMA_DEMO` | Users → Guests → Demo mode | the saved one |
| `GAMMA_GUEST_TTL_HOURS` | Users → Guests → Guest workspaces last | the saved one |
| `GAMMA_S3_BUCKET`, `_ENDPOINT`, `_REGION`, `_ACCESS_KEY`, `_SECRET_KEY`, `_PREFIX` | Backups → Bucket (set, every field comes from here; also the off-site rounds' bucket) | the saved bucket |
| `GAMMA_AI_ANTHROPIC_BASE_URL`, `_OPENAI_`, `_CHATGPT_`, `GAMMA_AI_BASE_URL` (old name) | AI → Connections, a connection's blank base URL | the providers' own |

## Feature settings with no Settings control (at odds with the rule)

| Variable | What it does | When unset |
|---|---|---|
| `GAMMA_ZOTERO_CLIENT_KEY`, `_SECRET` | This server's own Zotero OAuth app, skipping Gamma Cloud's broker (the same shape as Drive's client was) | through the broker |
| `GAMMA_GUEST_MAX` | Most guest accounts at once; 0 turns guests off | 500 (0 when hosted) |
| `GAMMA_GUEST_SEED` | A workspace zip every new guest gets | the welcome page only |
| `GAMMA_METADATA_LOOKUP` | `off` stops paper-registry lookups | on |
| `GAMMA_MODEL_CATALOG` | `off` stops fetching the models.dev catalog | on |
| `GAMMA_OPENALEX_API_KEY` | Server-wide OpenAlex key (each account can set its own) | none |
| `GAMMA_SEARXNG_URL` | Server-wide SearXNG (each account can set its own) | none |
| `GAMMA_VAULT_KEEP_MB` | Largest work-folder file a vault hashes and serves | 64 |
| `GAMMA_AGENT_CLAUDE`, `GAMMA_AGENT_CODEX` | The command that starts Claude Code or Codex | found on PATH |
| `GAMMA_AGENT_RUN_TIMEOUT` | Seconds before a local agent's turn is stopped | 1800 |
| `GAMMA_COMPUTER_GRACE_S` | Seconds a computer counts as online after its last poll | 45 |
| `GAMMA_FREE_PAGE_LIMIT` | Published pages on the share host's free plan | 5 |
| `GAMMA_PAGE_HOST` | The share host's per-account page hostname pattern | token links only |

## Set by Gamma's own packaging, not a person

`GAMMA_VERSION`, `GAMMA_COMMIT`, `GAMMA_BRANCH` (the build),
`GAMMA_UPDATES` and `GAMMA_UPDATE_CHECK` (who installs updates),
`GAMMA_SHELL_SECRET` and `GAMMA_FOLDER_LINKS` (the desktop app),
`GAMMA_CLOUD_DEFAULT_ISSUER` (desktop, Android, iPad), `GAMMA_HOSTED` and
`GAMMA_CLOUD_ADMIN_SUBJECT` (the fleet). The shells also set the data
dir, static dir, port and first admin.

## Test and developer hooks

`GAMMA_GOOGLE_OAUTH_BASE`, `GAMMA_GOOGLE_API_BASE`, `GAMMA_ZOTERO_API_BASE`,
`GAMMA_ZOTERO_STREAM_URL`, `GAMMA_ZOTERO_WEB_BASE` (stand-ins for the real
services), `GAMMA_LOCAL_AGENTS` (computer agents forced on or off),
`GAMMA_SYNC_INTERVAL` (0 stops the sync loops).

## Standard variables

`SSL_CERT_FILE` / `SSL_CERT_DIR` (OpenSSL; `app.py` points the first at
certifi's bundle where the system has none), `HTTP_PROXY` / `HTTPS_PROXY`
/ `NO_PROXY` (urllib's proxy lookup), `AWS_*` (boto3, without
`GAMMA_S3_*` keys), `CLAUDE_CONFIG_DIR` and `CODEX_HOME` (where the agents
keep sessions), `LOCALAPPDATA`, `USERPROFILE` and the Windows system
folders (where agent CLIs are looked for, where a vault may not go).

The account server (`cloud/`) reads about 37 of its own, almost all
`GAMMA_CLOUD_*`, and the fleet agent 6 `GAMMA_FLEET_*` and an optional
`GAMMA_FLEET_S3_*` group ([dev/hosted.md](../dev/hosted.md)); both are
unattended services and outside this survey.
