"""Environment configuration of the account server. Everything is an env
variable with a ``GAMMA_CLOUD_`` prefix; nothing is read from the request.

What is here is fixed for the life of the container. The sign-up gate
(registration mode, Turnstile, blocked mail domains) is not: an admin edits
it on the Admin page and it lives in cloud.db (``settings.py``).

- ``GAMMA_CLOUD_DATA_DIR`` — where ``cloud.db`` lives (default ``cloud/data``).
  The directory is the secret: it holds the signing keys and every hashed
  token, so it is never world-readable and always backed up encrypted.
- ``GAMMA_CLOUD_PUBLIC_URL`` — the issuer, e.g. ``https://account.gammapdf.com``.
  Every absolute URL (mail links, OIDC metadata) is built from it; the
  request's Host header is never trusted. Default ``http://127.0.0.1:9002``
  for a local run.
- ``GAMMA_CLOUD_MAIL`` — ``console`` (default; links are logged) / ``smtp``
  (``GAMMA_CLOUD_SMTP_HOST``, ``_PORT``, ``_USER``, ``_PASSWORD``,
  ``_STARTTLS``) / ``memory`` (tests). ``GAMMA_CLOUD_MAIL_FROM`` is the
  sender.
- ``GAMMA_CLOUD_DESKTOP_CLIENT_ID`` — the one public OIDC client every local
  Gamma sidecar is (default ``gamma-desktop``).
- ``GAMMA_CLOUD_GOOGLE_CLIENT_ID`` + ``_SECRET``, ``GAMMA_CLOUD_GITHUB_CLIENT_ID``
  + ``_SECRET`` — sign in with Google / GitHub (``providers.py``); a provider
  is offered only when both of its values are set. Their callback URLs are
  ``<public url>/oauth/<provider>/callback``. ``GAMMA_CLOUD_GOOGLE_ONE_TAP=0``
  turns off Google's sign-in prompt on the sign-in pages.
- ``GAMMA_CLOUD_SHARE_HOST_URL`` — the shared server's address (a Gamma
  with ``GAMMA_CLOUD_SHARE_HOST=1``): every account may sign in there, free
  accounts publish pages to it, and a Lite or Plus account's library lives
  on it. Handed to Gamma servers as ``share_host`` in ``GET /api/me`` and
  ``gamma_share_host`` in the discovery document, so they know where to
  publish pages. Empty = no shared server (and no Lite or Plus checkout).
"""

import os
from pathlib import Path
from urllib.parse import urlsplit

_REPO_ROOT = Path(__file__).resolve().parent.parent

DATA_DIR = Path(os.environ.get("GAMMA_CLOUD_DATA_DIR", "") or _REPO_ROOT / "data")
DB_PATH = DATA_DIR / "cloud.db"

PUBLIC_URL = (os.environ.get("GAMMA_CLOUD_PUBLIC_URL", "") or "http://127.0.0.1:9002").rstrip("/")

MAIL_BACKEND = os.environ.get("GAMMA_CLOUD_MAIL", "console").strip().lower() or "console"
# The sender defaults to no-reply at the public URL's host, so no domain is
# named anywhere unless it is set.
MAIL_FROM = (os.environ.get("GAMMA_CLOUD_MAIL_FROM", "")
             or f"Gamma Cloud <no-reply@{urlsplit(PUBLIC_URL).hostname or 'localhost'}>")
SMTP_HOST = os.environ.get("GAMMA_CLOUD_SMTP_HOST", "")
SMTP_PORT = int(os.environ.get("GAMMA_CLOUD_SMTP_PORT", "587") or 587)
SMTP_USER = os.environ.get("GAMMA_CLOUD_SMTP_USER", "")
SMTP_PASSWORD = os.environ.get("GAMMA_CLOUD_SMTP_PASSWORD", "")
SMTP_STARTTLS = os.environ.get("GAMMA_CLOUD_SMTP_STARTTLS", "1") not in ("0", "false", "no")

# Moved to settings.py. Listed only so app.py can warn a deployment that
# still sets one.
RETIRED_ENV = ("GAMMA_CLOUD_REGISTRATION", "GAMMA_CLOUD_TURNSTILE_SITEKEY",
               "GAMMA_CLOUD_TURNSTILE_SECRET", "GAMMA_CLOUD_BLOCKED_EMAIL_DOMAINS")

DESKTOP_CLIENT_ID = os.environ.get("GAMMA_CLOUD_DESKTOP_CLIENT_ID", "") or "gamma-desktop"

SHARE_HOST_URL = os.environ.get("GAMMA_CLOUD_SHARE_HOST_URL", "").strip().rstrip("/")

GOOGLE_CLIENT_ID = os.environ.get("GAMMA_CLOUD_GOOGLE_CLIENT_ID", "").strip()
GOOGLE_CLIENT_SECRET = os.environ.get("GAMMA_CLOUD_GOOGLE_CLIENT_SECRET", "").strip()
GOOGLE_ONE_TAP = os.environ.get("GAMMA_CLOUD_GOOGLE_ONE_TAP", "1") not in ("0", "false", "no")
GITHUB_CLIENT_ID = os.environ.get("GAMMA_CLOUD_GITHUB_CLIENT_ID", "").strip()
GITHUB_CLIENT_SECRET = os.environ.get("GAMMA_CLOUD_GITHUB_CLIENT_SECRET", "").strip()

# Lifetimes (seconds).
PORTAL_SESSION_TTL = 30 * 86400        # sliding: refreshed on use
VERIFY_TOKEN_TTL = 24 * 3600
RESET_TOKEN_TTL = 3600
AUTHORIZE_REQUEST_TTL = 600            # a pending sign-in on the authorize page
EXTERNAL_LOGIN_TTL = 900               # a Google/GitHub round trip, or the signup form after it
AUTH_CODE_TTL = 120
ACCESS_TOKEN_TTL = 3600
ID_TOKEN_TTL = 600
REFRESH_TOKEN_TTL = 90 * 86400
REFRESH_REUSE_GRACE = 60               # a refresh token just rotated away still works once more (a lost answer)
LAST_ACTIVE_TOUCH = 600                # a grant's last activity is written at most this often
RETIRED_KEY_GRACE = 7 * 86400          # a rotated-out key stays in the JWKS this long

PLANS = ("free", "lite", "plus", "pro")
PLAN_RANK = {p: i for i, p in enumerate(PLANS)}

# What each plan buys (docs/research/cloud-plans.md "The plans"), and where
# its library lives:
#
# - ``shared``: an account on the shared server (``SHARE_HOST_URL``), not
#   its admin. The plan's ``quota_mb`` and ``max_upload_mb`` travel to that
#   server in the ``limits`` claim (``shared_limits``).
# - ``hosted``: a container of its own (``hosted.py``), which the account
#   administers. ``policy`` is the Gamma sign-in policy it runs
#   (``invited`` admits only subjects holding a pending invitation there).
#   Off-site copies: interval and generations kept. ``memory_mb`` and
#   ``cpus`` size the container (its Docker memory limit and CPU share);
#   placement counts the memory against the host.
PLAN_LIMITS = {
    "free": {},
    "lite": {"shared": True, "quota_mb": 1024, "max_upload_mb": 50},
    "plus": {"shared": True, "quota_mb": 6 * 1024, "max_upload_mb": 100},
    "pro": {"hosted": True, "quota_mb": 100 * 1024, "max_upload_mb": 250, "max_accounts": 10,
            "policy": "invited", "offsite_interval_s": 3600, "offsite_keep": 30, "memory_mb": 1536, "cpus": 2.0},
}
# The limits a plan without ``shared`` gets on the shared server, where its
# account may sign in all the same: a Pro account keeps the library it had
# there under Plus's allowance.
SHARED_FALLBACK = {"pro": "plus"}


def shared_limits(plan: str) -> dict | None:
    """``{quota_mb, max_upload_mb}`` of an account on the shared server,
    None for a plan that gets the server's own defaults (free)."""
    limits = PLAN_LIMITS.get(SHARED_FALLBACK.get(plan, plan), {})
    return {"quota_mb": limits["quota_mb"], "max_upload_mb": limits["max_upload_mb"]} if limits.get("shared") else None
# Days. A failed payment keeps its plan for GRACE_DAYS. A container whose
# plan ended is read-only, stopped after READ_ONLY_DAYS and deleted
# DELETE_DAYS after the end.
GRACE_DAYS = 7
READ_ONLY_DAYS = 30
DELETE_DAYS = 90

# Hosted containers answer <label>.<HOSTED_DOMAIN>; empty = hosting is off
# (no Pro checkout, no provisioning). The fleet starts containers from
# FLEET_IMAGE at FLEET_IMAGE_TAG unless a server pins its own tag.
HOSTED_DOMAIN = os.environ.get("GAMMA_CLOUD_HOSTED_DOMAIN", "").strip().lower().strip(".")
FLEET_IMAGE = os.environ.get("GAMMA_CLOUD_FLEET_IMAGE", "").strip() or "ghcr.io/tim4431/gamma"
FLEET_IMAGE_TAG = os.environ.get("GAMMA_CLOUD_FLEET_IMAGE_TAG", "").strip() or "latest"

# Stripe (``billing.py``). Billing is off while the secret is empty. The
# price ids map a Stripe Price to the plan and the interval it buys.
STRIPE_SECRET = os.environ.get("GAMMA_CLOUD_STRIPE_SECRET", "").strip()
STRIPE_WEBHOOK_SECRET = os.environ.get("GAMMA_CLOUD_STRIPE_WEBHOOK_SECRET", "").strip()
STRIPE_PRICES = {  # key the checkout form sends -> (plan, interval, price id)
    "lite_month": ("lite", "month", os.environ.get("GAMMA_CLOUD_STRIPE_PRICE_LITE_MONTH", "").strip()),
    "lite_year": ("lite", "year", os.environ.get("GAMMA_CLOUD_STRIPE_PRICE_LITE_YEAR", "").strip()),
    "plus_month": ("plus", "month", os.environ.get("GAMMA_CLOUD_STRIPE_PRICE_PLUS_MONTH", "").strip()),
    "plus_year": ("plus", "year", os.environ.get("GAMMA_CLOUD_STRIPE_PRICE_PLUS_YEAR", "").strip()),
    "pro_month": ("pro", "month", os.environ.get("GAMMA_CLOUD_STRIPE_PRICE_PRO_MONTH", "").strip()),
    "pro_year": ("pro", "year", os.environ.get("GAMMA_CLOUD_STRIPE_PRICE_PRO_YEAR", "").strip()),
}
# Shown on the plan page and the website; billing itself uses the Prices.
PLAN_PRICES_USD = {"lite": {"month": 2, "year": 20}, "plus": {"month": 5, "year": 50}, "pro": {"month": 20, "year": 200}}
