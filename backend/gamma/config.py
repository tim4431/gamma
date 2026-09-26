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
# Database snapshots the migration runner takes before changing the data
# directory (gamma/migrations.py backup()).
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
    desktop client), ``GAMMA_CLOUD_POLICY`` (refuse / claim / provision) and
    ``GAMMA_CLOUD_ADMIN_SUBJECT`` (the cloud account that becomes this
    server's admin on first sign-in) and ``GAMMA_CLOUD_SHARE_HOST=1`` (this
    server is the free share host: it accepts published pages, refuses the
    guest and lists accounts only by exact name — gamma/publish.py)."""
    return {"issuer": os.environ.get("GAMMA_CLOUD_ISSUER", "").strip().rstrip("/"),
            "client_id": os.environ.get("GAMMA_CLOUD_CLIENT_ID", "").strip(),
            "client_secret": os.environ.get("GAMMA_CLOUD_CLIENT_SECRET", ""),
            "policy": os.environ.get("GAMMA_CLOUD_POLICY", "").strip().lower(),
            "admin_subject": os.environ.get("GAMMA_CLOUD_ADMIN_SUBJECT", "").strip(),
            "share_host": os.environ.get("GAMMA_CLOUD_SHARE_HOST", "").strip().lower() in ("1", "true", "yes", "on")}


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
    Unset or unparseable = 500."""
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
