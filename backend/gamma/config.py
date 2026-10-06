"""Central configuration. Everything tunable comes from environment variables."""

import os
from pathlib import Path

# Where all persistent state lives: users.db (accounts, sessions, workspaces,
# memberships, shares, personal prefs) and workspaces/<id>/{pages.db,data.db,
# uploads/} — one directory per workspace (docs/dev/workspaces.md). Defaults
# to a data/ folder at the repo root — the local mirror of Docker's /data
# volume. Override with GAMMA_DATA_DIR (the Docker image sets it to /data).
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
DATA_DIR = Path(os.environ.get("GAMMA_DATA_DIR", "") or _REPO_ROOT / "data")
USERS_DB = DATA_DIR / "users.db"
WORKSPACES_DIR = DATA_DIR / "workspaces"
# The pre-workspace layout (users/<username>/...). Read ONLY by the schema
# migration that moves it into WORKSPACES_DIR (gamma/migrations.py).
LEGACY_USERS_DIR = DATA_DIR / "users"
# Snapshots and backup state: the migration runner's pre-upgrade copies
# (gamma/backups.py), workspace snapshots and their scheduled tasks
# (gamma/ws_backup.py, gamma/backup_schedule.py), the off-site copies'
# state (gamma/offsite.py).
BACKUPS_DIR = DATA_DIR / "backups"

# Built frontend (vite dist/). When set and the directory exists, the backend
# serves it as an SPA — no separate static file server or reverse proxy needed.
STATIC_DIR = os.environ.get("GAMMA_STATIC_DIR", "")


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
            "share_host": os.environ.get("GAMMA_CLOUD_SHARE_HOST", "").strip().lower() in ("1", "true", "yes", "on")}


def hosted() -> bool:
    """``GAMMA_HOSTED=1``: this server is a paid hosted container. It learns
    its plan's limits from the account server ``GAMMA_CLOUD_ISSUER`` names,
    with the client id and secret of the cloud variables (gamma/hosted.py)."""
    return os.environ.get("GAMMA_HOSTED", "").strip().lower() in ("1", "true", "yes", "on")


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


DEFAULT_GUEST_MAX = 500


def guest_max() -> int:
    """``GAMMA_GUEST_MAX``: how many guest accounts may live at once (a
    guest login past it is refused with 503); 0 turns guest logins off.
    Unset or unparseable = 500. A hosted container (``hosted``) takes no
    guests whatever the variable says: a guest would not count against the
    plan's accounts."""
    if hosted():
        return 0
    try:
        value = int(os.environ.get("GAMMA_GUEST_MAX", "").strip() or DEFAULT_GUEST_MAX)
    except ValueError:
        return DEFAULT_GUEST_MAX
    return value if value >= 0 else DEFAULT_GUEST_MAX


def guest_ttl_override() -> str:
    """``GAMMA_GUEST_TTL_HOURS``: the guest lifetime in hours, overriding the
    saved server setting (gamma/server_settings.py guest_ttl_settings)."""
    return os.environ.get("GAMMA_GUEST_TTL_HOURS", "").strip()


def demo_override() -> bool:
    """``GAMMA_DEMO`` truthy: demo mode on, whatever the saved setting says."""
    return os.environ.get("GAMMA_DEMO", "").strip().lower() in ("1", "true", "yes", "on")


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


MAX_UPLOAD_BYTES = 50 * 1024 * 1024  # 50 MB

# --- AI chat -----------------------------------------------------------------
# AI configuration is per-user, not env: each user adds provider entries in the
# GUI (Settings → AI → Connections), resolved per request by
# gamma/ai_settings.ai_runtime(); the protocols themselves are the adapters in
# gamma/ai_protocols/. The env can only override each protocol's default base
# URL (shown as the placeholder in the GUI and used when an entry leaves it
# blank). Legacy GAMMA_AI_BASE_URL / ANTHROPIC_BASE_URL alias the anthropic
# slot.

_legacy_url = os.environ.get("GAMMA_AI_BASE_URL", "") or os.environ.get("ANTHROPIC_BASE_URL", "")

AI_BASE_URLS = {
    "anthropic": (os.environ.get("GAMMA_AI_ANTHROPIC_BASE_URL", "") or _legacy_url
                  or "https://api.anthropic.com").rstrip("/"),
    "openai": (os.environ.get("GAMMA_AI_OPENAI_BASE_URL", "")
               or "https://api.openai.com").rstrip("/"),
    "chatgpt": (os.environ.get("GAMMA_AI_CHATGPT_BASE_URL", "")
                or "https://chatgpt.com/backend-api/codex").rstrip("/"),
}
