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
# GUI (Settings → AI → Connections) — a protocol + credential + optional label,
# base URL, and model list — stored server-side in users.db and resolved per
# request by gamma/ai_settings.ai_runtime(). Three protocols exist:
#   "anthropic" — Anthropic Messages API (Anthropic, Kimi, GLM, ...)
#   "openai"    — OpenAI Chat Completions API (OpenAI, DeepSeek and compatible)
#   "chatgpt"   — ChatGPT subscription sign-in (the Codex Responses backend)
# No model names live here: an entry offers the models picked for it from the
# provider's live listing. The env can only override each protocol's default
# base URL (shown as the placeholder in the GUI and used when an entry leaves
# it blank): GAMMA_AI_ANTHROPIC_BASE_URL / GAMMA_AI_OPENAI_BASE_URL /
# GAMMA_AI_CHATGPT_BASE_URL (legacy GAMMA_AI_BASE_URL / ANTHROPIC_BASE_URL
# alias the anthropic slot).

_legacy_url = os.environ.get("GAMMA_AI_BASE_URL", "") or os.environ.get("ANTHROPIC_BASE_URL", "")

AI_PROTOCOLS = {
    "anthropic": {
        "label": "Anthropic Messages API",
        "base_url": (os.environ.get("GAMMA_AI_ANTHROPIC_BASE_URL", "") or _legacy_url
                     or "https://api.anthropic.com").rstrip("/"),
    },
    "openai": {
        "label": "OpenAI Chat Completions API",
        "base_url": (os.environ.get("GAMMA_AI_OPENAI_BASE_URL", "")
                     or "https://api.openai.com").rstrip("/"),
    },
    # No API key: the entry holds OAuth tokens from signing in with a ChatGPT
    # account (Codex CLI's flow) — usage is billed to the subscription. The
    # base URL is the Codex Responses endpoint on the ChatGPT backend.
    # auth "oauth" marks sign-in protocols for the settings form and the
    # provider CRUD guards (default is "key").
    "chatgpt": {
        "label": "ChatGPT (subscription sign-in)",
        "base_url": (os.environ.get("GAMMA_AI_CHATGPT_BASE_URL", "")
                     or "https://chatgpt.com/backend-api/codex").rstrip("/"),
        "auth": "oauth",
    },
}

# Named services the settings form offers next to the raw protocols: one of
# the protocols above plus that service's endpoint. An entry made from one is
# just protocol + base URL; the preset only names it (form, provider label).
AI_SERVICES = [
    {"id": "deepseek", "label": "DeepSeek", "protocol": "openai",
     "base_url": "https://api.deepseek.com"},
]
