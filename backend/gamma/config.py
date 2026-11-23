"""The data directory's layout and most of the server's environment settings;
a few modules read their own GAMMA_* variable beside the code that uses it."""

import os
import sys
from pathlib import Path

# Where all persistent state lives: users.db (accounts, sessions, workspaces,
# memberships, shares, personal prefs) and workspaces/<id>/{pages.db,data.db,
# uploads/} — one directory per workspace (docs/dev/workspaces.md). Defaults
# to a data/ folder at the repo root — the local mirror of Docker's /data
# volume. Override with GAMMA_DATA_DIR (the Docker image sets it to /data).
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
DATA_DIR = Path(os.environ.get("GAMMA_DATA_DIR", "") or _REPO_ROOT / "data")
USERS_DB = DATA_DIR / "users.db"
# What this computer knows of its synced vaults and of the PDFs read where
# they are (gamma/linked_files.py): only the desktop app's own server has it
# (``folder_links_enabled``), and never in a workspace's files, so no backup of
# a workspace carries a path.
VAULTS_DB = DATA_DIR / "vaults.db"
WORKSPACES_DIR = DATA_DIR / "workspaces"
# The empty directories a computer agent's conversation of no work folder
# and no offered directory works in: agents/<ws>/<conversation id>/, made at
# its first turn and removed with it (gamma/local_agents/chats.py).
AGENTS_DIR = DATA_DIR / "agents"
# Snapshots and backup state: the migration runner's pre-upgrade copies
# (gamma/backups.py), workspace snapshots and their scheduled tasks
# (gamma/ws_backup.py, gamma/backup_schedule.py), the off-site copies'
# state (gamma/offsite.py).
BACKUPS_DIR = DATA_DIR / "backups"

# Built frontend (vite dist/). When set and the directory exists, the backend
# serves it as an SPA — no separate static file server or reverse proxy needed.
STATIC_DIR = os.environ.get("GAMMA_STATIC_DIR", "")


def _env_on(name: str, default: str = "") -> bool:
    """Whether the variable ``name`` is set to a truthy word (1/true/yes/on,
    any case); ``default`` stands in when it is unset."""
    return os.environ.get(name, default).strip().lower() in ("1", "true", "yes", "on")


def folder_links_enabled() -> bool:
    """Whether this server keeps folders on disk (gamma/folder_links.py):
    GAMMA_FOLDER_LINKS, which only the desktop app sets, for the local
    server it runs as the user on the user's own computer. Read when asked,
    so a test can turn it on."""
    return _env_on("GAMMA_FOLDER_LINKS")


def local_agents_setting() -> bool | None:
    """GAMMA_LOCAL_AGENTS as set: True on, False off (0/false/no/off), None
    unset or anything else, which leaves it to whether the desktop app
    started this server (gamma/local_agents ``enabled``). Read when asked,
    so a test can turn it on."""
    if _env_on("GAMMA_LOCAL_AGENTS"):
        return True
    if os.environ.get("GAMMA_LOCAL_AGENTS", "").strip().lower() in ("0", "false", "no", "off"):
        return False
    return None


SHELL_SECRET_VAR = "GAMMA_SHELL_SECRET"
_shell_secret: str | None = None   # once read; None until then


def shell_secret() -> str:
    """What the desktop app sends as ``X-Gamma-Shell`` on the routes that
    take a path on this computer or its word otherwise (routers/shell.py):
    a page of Gamma never names a path, the shell's native dialog does.
    GAMMA_SHELL_SECRET, made anew at each start of the app; empty (dev,
    tests) means no check on those routes, and refuses the ones that never
    pass unchecked (``shell_strict``). Read once, at the app's start (``create_app``),
    and taken off this process's environment then (``_take_env``), so no
    process this server starts, nor anything that reads its environment
    later, finds it. The variable set again is read again: how a test sets
    it (conftest forgets the one read after each test)."""
    global _shell_secret
    if _shell_secret is None or SHELL_SECRET_VAR in os.environ:
        _shell_secret = _take_env(SHELL_SECRET_VAR)
    return _shell_secret


def _take_env(name: str) -> str:
    """The variable's value, taken out of ``os.environ`` ("" for none). On
    Linux and macOS its bytes in the environment the process began with are
    zeroed first: /proc/<pid>/environ (Linux) and KERN_PROCARGS2, what
    ``ps eww`` reads (macOS), show that block whatever unsetenv did since
    (Windows keeps no such copy). Never raises."""
    value = os.environ.get(name, "")
    if value and (sys.platform.startswith("linux") or sys.platform == "darwin"):
        try:
            import ctypes

            libc = ctypes.CDLL(None)
            if sys.platform == "darwin":                     # a dylib cannot reach the bare ``environ``: char ***
                ns_environ = libc._NSGetEnviron
                ns_environ.restype = ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))
                environ = ns_environ().contents
            else:
                environ = ctypes.POINTER(ctypes.c_void_p).in_dll(libc, "environ")
            prefix, i = f"{name}=".encode(), 0
            while environ[i]:
                entry = ctypes.string_at(environ[i])
                if entry.startswith(prefix):
                    ctypes.memset(environ[i] + len(prefix), 0, len(entry) - len(prefix))
                i += 1
        except (OSError, ValueError, AttributeError):
            pass
    os.environ.pop(name, None)
    return value


# Keep these reads lazy, as before: server settings and publisher sessions
# resolve their environment overrides when used, not at module import.
def public_url_override() -> str:
    return os.environ.get("GAMMA_PUBLIC_URL", "").strip()


def mcp_extra_hosts() -> list[str]:
    return [host.strip().lower() for host in os.environ.get("GAMMA_MCP_ALLOWED_HOSTS", "").split(",")
            if host.strip()]


def publisher_session_key() -> str:
    return os.environ.get("GAMMA_PUBLISHER_SESSION_KEY", "")


def cloud_env() -> dict:
    """Sign in with Gamma Cloud (gamma/cloud_auth.py) as a provisioned
    container gets it: ``GAMMA_CLOUD_ISSUER`` (the account server; set, it
    overrides the saved settings), ``GAMMA_CLOUD_CLIENT_ID`` /
    ``GAMMA_CLOUD_CLIENT_SECRET`` (a confidential client; unset = the public
    desktop client), ``GAMMA_CLOUD_POLICY`` (refuse / claim / provision /
    invited; on a hosted server the plan's policy replaces it) and
    ``GAMMA_CLOUD_ADMIN_SUBJECT`` (the cloud account that becomes this
    server's admin on first sign-in) and ``GAMMA_CLOUD_SHARE_HOST=1`` (this
    server is the free share host: it accepts published pages, refuses the
    guest and lists accounts only by exact name — gamma/publish.py).
    ``GAMMA_CLOUD_DEFAULT_ISSUER`` is the account server used until an admin
    saves one (the desktop app's sidecar sets it); unlike
    ``GAMMA_CLOUD_ISSUER`` it leaves the settings editable."""
    return {"issuer": os.environ.get("GAMMA_CLOUD_ISSUER", "").strip().rstrip("/"),
            "default_issuer": os.environ.get("GAMMA_CLOUD_DEFAULT_ISSUER", "").strip().rstrip("/"),
            "client_id": os.environ.get("GAMMA_CLOUD_CLIENT_ID", "").strip(),
            "client_secret": os.environ.get("GAMMA_CLOUD_CLIENT_SECRET", ""),
            "policy": os.environ.get("GAMMA_CLOUD_POLICY", "").strip().lower(),
            "admin_subject": os.environ.get("GAMMA_CLOUD_ADMIN_SUBJECT", "").strip(),
            "share_host": _env_on("GAMMA_CLOUD_SHARE_HOST")}


def zotero_api_base() -> str:
    """``GAMMA_ZOTERO_API_BASE``: Zotero's Web API (gamma/zotero_web.py),
    https://api.zotero.org unless a test or the browser e2e points it at a
    stand-in."""
    return (os.environ.get("GAMMA_ZOTERO_API_BASE", "").strip() or "https://api.zotero.org").rstrip("/")


def zotero_web_base() -> str:
    """``GAMMA_ZOTERO_WEB_BASE``: zotero.org itself, where the OAuth steps
    live (gamma/zotero_oauth.py)."""
    return (os.environ.get("GAMMA_ZOTERO_WEB_BASE", "").strip() or "https://www.zotero.org").rstrip("/")


def zotero_stream_url() -> str:
    """``GAMMA_ZOTERO_STREAM_URL``: Zotero's streaming API, the socket that
    says when a linked library changed (gamma/zotero_stream.py);
    wss://stream.zotero.org unless a test points it at a stand-in."""
    return (os.environ.get("GAMMA_ZOTERO_STREAM_URL", "").strip() or "wss://stream.zotero.org").rstrip("/")


# Gamma's own Google OAuth clients for Drive backups (gamma/cloud_account.py),
# both of one registration, asking for drive.file alone, so connecting
# Drive is signing in to Google and allowing it: a "TVs and Limited Input
# devices" client for the code any server shows (the device flow), and a
# "Desktop app" client for Google's page sending the browser back to a
# Gamma opened on its own computer (a loopback address). Google treats such
# clients' secrets as public, as it does any installed app's
# (docs/research/google-drive-backup.md). A server may use its own instead
# (manage.py cloud-client drive [--desktop]). Each secret is two literals so
# secret scanners, which take it for a leaked confidential one, pass it by.
GOOGLE_DRIVE_CLIENT = (
    "229361230329-p8vn304cus6mn8vnhodfh7jslrvoe59d.apps.googleusercontent.com",
    "GOCSPX-" "ivyKQLkR7kWqdMkJM1mwg3IPmXIp",
)
GOOGLE_DRIVE_DESKTOP_CLIENT = (
    "229361230329-4n2h2d4lkdobcf1tsb32dukcjj6h168v.apps.googleusercontent.com",
    "GOCSPX-" "AY2MNXkih3x_F1f-Re9E4on_e0CJ",
)


def google_accounts_base() -> str:
    """``GAMMA_GOOGLE_ACCOUNTS_BASE``: Google's sign-in pages (the
    authorization page a browser opens); https://accounts.google.com unless
    the browser e2e points it at a stand-in."""
    return (os.environ.get("GAMMA_GOOGLE_ACCOUNTS_BASE", "").strip() or "https://accounts.google.com").rstrip("/")


def google_oauth_base() -> str:
    """``GAMMA_GOOGLE_OAUTH_BASE``: Google's OAuth endpoints (device codes,
    tokens, revocation); https://oauth2.googleapis.com unless the browser
    e2e points it at a stand-in."""
    return (os.environ.get("GAMMA_GOOGLE_OAUTH_BASE", "").strip() or "https://oauth2.googleapis.com").rstrip("/")


def google_api_base() -> str:
    """``GAMMA_GOOGLE_API_BASE``: the Drive API and its upload endpoint;
    https://www.googleapis.com unless the browser e2e points it at a
    stand-in."""
    return (os.environ.get("GAMMA_GOOGLE_API_BASE", "").strip() or "https://www.googleapis.com").rstrip("/")


# Gamma's own Microsoft app for OneDrive backups (gamma/onedrive.py,
# gamma/cloud_account.py): one public client of the Microsoft identity
# platform, for personal and work or school accounts, with "Allow public
# client flows" on (the code any server shows) and the redirect URI
# http://localhost/api/admin/clouds/onedrive/callback under "Mobile and
# desktop applications" (Microsoft's page, on the server's own computer;
# the port is not matched), asking for Files.ReadWrite, User.Read and
# offline_access. A public client has no secret. Registered as "Gamma PDF"
# in the xwtim directory (docs/dev/debugging.md "OneDrive"). A server may
# use its own instead (manage.py cloud-client onedrive <id>); a build with
# none reads "Not available".
ONEDRIVE_CLIENT_ID = "180e2624-e809-4621-b4c9-6271859dc18e"


def microsoft_login_base() -> str:
    """``GAMMA_MICROSOFT_LOGIN_BASE``: the Microsoft identity platform (device
    codes, tokens, the sign-in page); https://login.microsoftonline.com
    unless a test points it at a stand-in."""
    return (os.environ.get("GAMMA_MICROSOFT_LOGIN_BASE", "").strip() or "https://login.microsoftonline.com").rstrip("/")


def microsoft_graph_base() -> str:
    """``GAMMA_MICROSOFT_GRAPH_BASE``: Microsoft Graph, OneDrive's API;
    https://graph.microsoft.com unless a test points it at a stand-in."""
    return (os.environ.get("GAMMA_MICROSOFT_GRAPH_BASE", "").strip() or "https://graph.microsoft.com").rstrip("/")


def hosted() -> bool:
    """``GAMMA_HOSTED=1``: this server is a paid hosted container. It learns
    its plan's limits from the account server ``GAMMA_CLOUD_ISSUER`` names,
    with the client id and secret of the cloud variables (gamma/hosted.py)."""
    return _env_on("GAMMA_HOSTED")


# The share host's published-page cap per Gamma Cloud plan (gamma/publish.py
# page_cap): a plan missing here is unlimited. GAMMA_FREE_PAGE_LIMIT
# overrides the free plan's number (0 lifts the cap).
PLAN_PAGE_LIMITS = {"free": 5}


def plan_page_limits() -> dict:
    limits = dict(PLAN_PAGE_LIMITS)
    raw = os.environ.get("GAMMA_FREE_PAGE_LIMIT", "").strip()
    if raw:
        n = int(raw)  # checked at startup (publish.check_config)
        if n > 0:
            limits["free"] = n
        else:
            limits.pop("free", None)
    return limits


def page_host_pattern() -> str:
    """``GAMMA_PAGE_HOST``: the share host's per-account page hostname with
    a ``{username}`` placeholder, e.g. ``{username}-pages.gammapdf.com``
    ("" = no pretty addresses, token links only; gamma/publish.py)."""
    return os.environ.get("GAMMA_PAGE_HOST", "").strip().lower()


def guest_seed_path() -> str:
    """``GAMMA_GUEST_SEED``: a workspace backup zip (gamma/ws_backup.py)
    restored into every new guest's workspace, "" = the welcome page only
    (gamma/guests.py)."""
    return os.environ.get("GAMMA_GUEST_SEED", "").strip()


def guest_max_override() -> int | None:
    """``GAMMA_GUEST_MAX``: how many guest accounts may live at once, as the
    public demo's environment sets it (and the account server sets 0, no
    guests, on a hosted container); None unless it holds a whole number of
    0 or more. Elsewhere an admin sets it in Settings → Users → Guests
    (gamma/server_options.py ``guest_max``)."""
    try:
        value = int(os.environ.get("GAMMA_GUEST_MAX", "").strip())
    except ValueError:
        return None
    return value if value >= 0 else None


def guest_ttl_override() -> str:
    """``GAMMA_GUEST_TTL_HOURS``: the guest lifetime in hours, overriding the
    saved server setting (gamma/server_settings.py guest_ttl_settings); the
    public demo sets it."""
    return os.environ.get("GAMMA_GUEST_TTL_HOURS", "").strip()


def demo_override() -> bool:
    """``GAMMA_DEMO`` truthy: demo mode on, whatever the saved setting says."""
    return _env_on("GAMMA_DEMO")


# --- Text recognition (gamma/ocr_engine.py, gamma/pdf_ocr.py) -----------------

def tessdata_dir() -> Path:
    """Where the text recognition models are: ``GAMMA_TESSDATA_DIR`` (the
    Docker image's /app/tessdata, the desktop bundle's), else a tessdata
    folder beside the gamma package: backend/tessdata, where the repository
    keeps them, outside the package so they ride their own layer of the
    image, and the mobile apps' copy beside their gamma/ (Android's source
    set, the iPad's build-python.sh), which their server sends the window
    that reads the pages (ocr_engine ``capability``). Read when asked."""
    return Path(os.environ.get("GAMMA_TESSDATA_DIR", "").strip() or Path(__file__).resolve().parent.parent / "tessdata")


def ocr_override() -> bool | None:
    """``GAMMA_OCR``: True or False when it says on or off (1/true/yes/on,
    0/false/no/off), as a hosted container's environment turns text
    recognition on (docs/dev/hosted.md) or the desktop's engine check does;
    None when it is unset or says neither. Elsewhere an admin sets it in
    Settings → Server → Text recognition (gamma/server_options.py
    ``ocr_enabled``). Read when asked, so a test can turn it either way."""
    raw = os.environ.get("GAMMA_OCR", "").strip().lower()
    if raw in ("1", "true", "yes", "on"):
        return True
    if raw in ("0", "false", "no", "off"):
        return False
    return None


def ocr_in_window() -> bool:
    """``GAMMA_OCR_ENGINE=window``: this server reads no text itself, as on
    the mobile apps, and leaves every reading to the window that asks
    (ocr_engine ``capability``), for tests and development on a machine
    with the engine. Any other value, or none, changes nothing. Read when
    asked."""
    return os.environ.get("GAMMA_OCR_ENGINE", "").strip().lower() == "window"


def sync_interval_s() -> int:
    """Seconds between mirror sync rounds (gamma/sync_engine.py); 0 turns
    the background loop off (the API's "sync now" still works)."""
    try:
        return max(0, int(os.environ.get("GAMMA_SYNC_INTERVAL", "30") or 0))
    except ValueError:
        return 30


def offsite_env() -> dict:
    """The off-site copies as the environment sets them, read at each use
    by gamma/offsite.py, whose ``settings`` merges them with the saved
    settings. With ``GAMMA_S3_BUCKET`` set every field comes
    from here and the saved settings are not used: ``GAMMA_S3_ENDPOINT``
    (unset for AWS itself), ``GAMMA_S3_REGION``, ``GAMMA_S3_ACCESS_KEY`` /
    ``GAMMA_S3_SECRET_KEY`` (both unset: boto3's own chain, the ``AWS_*``
    variables or an instance role), ``GAMMA_S3_PREFIX`` (put before every
    key), ``GAMMA_OFFSITE`` (``enabled``: on unless 0/false/no/off),
    ``GAMMA_OFFSITE_INTERVAL`` (``interval``, seconds) and
    ``GAMMA_OFFSITE_KEEP`` (``keep``: copies per database). Strings as
    set; offsite.py parses the numbers."""
    env = os.environ.get
    return {"bucket": env("GAMMA_S3_BUCKET", "").strip(),
            "endpoint": env("GAMMA_S3_ENDPOINT", "").strip().rstrip("/"),
            "region": env("GAMMA_S3_REGION", "").strip(),
            "access_key": env("GAMMA_S3_ACCESS_KEY", "").strip(),
            "secret_key": env("GAMMA_S3_SECRET_KEY", "").strip(),
            "prefix": env("GAMMA_S3_PREFIX", "").strip(),
            "enabled": env("GAMMA_OFFSITE", "").strip(),
            "interval": env("GAMMA_OFFSITE_INTERVAL", "").strip(),
            "keep": env("GAMMA_OFFSITE_KEEP", "").strip()}


# --- AI chat -----------------------------------------------------------------
# AI configuration is per-user, not env: each user adds provider entries in the
# GUI (Settings → AI → Models), resolved per request by
# gamma/ai_settings.ai_runtime(); the protocols themselves are the adapters in
# gamma/ai_protocols/. Each protocol's default base URL is shown as the
# placeholder in the GUI and used when an entry leaves it blank; another
# endpoint (DeepSeek's Anthropic wire, a local OpenAI-compatible server) is
# the entry's own base URL.

AI_BASE_URLS = {
    "anthropic": "https://api.anthropic.com",
    "openai": "https://api.openai.com",
    "chatgpt": "https://chatgpt.com/backend-api/codex",
}
