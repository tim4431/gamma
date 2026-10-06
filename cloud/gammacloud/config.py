"""Environment configuration of the account server. Everything is an env
variable with a ``GAMMA_CLOUD_`` prefix; nothing is read from the request.

What is here is fixed for the life of the container, and the Admin page
shows it read-only (``admin_view``). The sign-up gate and the plans on sale
are not: an admin edits them on the Admin page and they live in cloud.db
(``settings.py``).

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
- ``GAMMA_CLOUD_SHARE_HOST_URL`` — where Gamma servers publish pages: the
  shared server (a Gamma with ``GAMMA_CLOUD_SHARE_HOST=1``) under the
  address publishing has always used. Handed to Gamma servers as
  ``share_host`` in ``GET /api/me`` and ``gamma_share_host`` in the
  discovery document. A server that has published keeps this address in its
  mirror and refuses another, so it does not change. Empty = no share host.
- ``GAMMA_CLOUD_APP_URL`` — the same shared server under the address people
  use, e.g. ``https://app.gammapdf.com``: the entrance (``entrance.py``),
  where every account may sign in and a Lite or Plus library lives.
  Defaults to the share host's address. Empty with no share host = no
  shared server, and no Lite or Plus checkout.
- ``GAMMA_CLOUD_CF_API_TOKEN`` + ``GAMMA_CLOUD_CF_ZONE_ID`` — a Cloudflare API
  token with DNS edit rights on the hosting domain's zone, and that zone's
  id (``dns.py``). With both set (and hosting on), a hosted server placed
  on a host with a public address of its own gets a DNS record of its own.
  Without them such a host takes no servers.
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
APP_URL = os.environ.get("GAMMA_CLOUD_APP_URL", "").strip().rstrip("/") or SHARE_HOST_URL

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
# - ``shared``: an account on the shared server (``APP_URL``), not its
#   admin. The plan's ``quota_mb`` and ``max_upload_mb`` travel to that
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
# A deleted account keeps its username and address this many days, during
# which an admin can restore it; then the hourly purge removes it.
PURGE_DELETED_DAYS = 30

# Hosted containers answer <label><HOSTED_SUFFIX>.<HOSTED_DOMAIN>, the
# label being the owner's username; empty domain = hosting is off (no Pro
# checkout, no provisioning). The suffix keeps every such name apart from
# the service names in the same zone (account, app, share, demo, and the
# <username>-pages hosts), whatever a person calls themselves. The fleet starts containers from
# FLEET_IMAGE at FLEET_IMAGE_TAG unless a server pins its own tag.
HOSTED_DOMAIN = os.environ.get("GAMMA_CLOUD_HOSTED_DOMAIN", "").strip().lower().strip(".")
HOSTED_SUFFIX = os.environ.get("GAMMA_CLOUD_HOSTED_SUFFIX", "-user").strip().lower()
FLEET_IMAGE = os.environ.get("GAMMA_CLOUD_FLEET_IMAGE", "").strip() or "ghcr.io/tim4431/gamma"
FLEET_IMAGE_TAG = os.environ.get("GAMMA_CLOUD_FLEET_IMAGE_TAG", "").strip() or "latest"
# Cloudflare DNS (``dns.py``): a server on a host with its own public address
# gets a proxied record <label><HOSTED_SUFFIX>.<HOSTED_DOMAIN> -> that address.
CF_API_TOKEN = os.environ.get("GAMMA_CLOUD_CF_API_TOKEN", "").strip()
CF_ZONE_ID = os.environ.get("GAMMA_CLOUD_CF_ZONE_ID", "").strip()

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


# --- the Admin page's Configuration section -----------------------------------

def stripe_mode() -> str:
    """``test`` or ``live`` from the secret key's prefix, ``unknown`` for a
    key without one, "" without a key."""
    for mode in ("test", "live"):
        if STRIPE_SECRET.startswith((f"sk_{mode}_", f"rk_{mode}_")):
            return mode
    return "unknown" if STRIPE_SECRET else ""


def _item(name: str, env: str, state: str, value: str = "", note: str = "") -> dict:
    item = {"name": name, "env": env, "state": state}
    if value:
        item["value"] = value
    if note:
        item["note"] = note
    return item


def _value(name: str, env: str, value: str, note: str = "") -> dict:
    """A setting shown with its value, ``set`` or ``missing``."""
    return _item(name, env, "set" if value else "missing", value, note)


def _secret(name: str, env: str, value: str, note: str = "") -> dict:
    """A secret: whether it is set, never what it is."""
    return _item(name, env, "set" if value else "missing", note=note)


def _switch(name: str, env: str, on: bool, note: str = "") -> dict:
    return _item(name, env, "on" if on else "off", note=note)


def admin_view(schema_version: int) -> list[dict]:
    """The values above as the Admin page shows them, read-only: groups of
    items ``{name, env, state, value?, note?}``, where ``state`` is ``set`` /
    ``missing`` for a value and ``on`` / ``off`` for a switch. A secret shows
    only whether it is set."""
    smtp = MAIL_BACKEND == "smtp"
    not_used = "" if smtp else f"not used by the {MAIL_BACKEND} backend"
    billing = bool(STRIPE_SECRET)
    stripe = [_switch("Billing", "GAMMA_CLOUD_STRIPE_SECRET", billing, "" if billing else "no paid plan can be bought")]
    if billing:
        stripe.append(_item("Mode", "", "set", stripe_mode(), "the secret key's prefix"))
    stripe.append(_secret("Webhook secret", "GAMMA_CLOUD_STRIPE_WEBHOOK_SECRET", STRIPE_WEBHOOK_SECRET,
                          "" if STRIPE_WEBHOOK_SECRET or not billing else "Stripe's events are refused"))
    for key, (plan, interval, price_id) in STRIPE_PRICES.items():
        stripe.append(_value(f"{plan.capitalize()} {'monthly' if interval == 'month' else 'yearly'} price",
                             f"GAMMA_CLOUD_STRIPE_PRICE_{key.upper()}", price_id))
    google = bool(GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET)
    half = "needs both the client id and the secret"
    return [
        {"name": "Server", "items": [
            _value("Public URL", "GAMMA_CLOUD_PUBLIC_URL", PUBLIC_URL,
                   "" if PUBLIC_URL.startswith("https://") else "plain HTTP: for a local run only"),
            _value("Data directory", "GAMMA_CLOUD_DATA_DIR", str(DATA_DIR)),
            _value("Schema version", "", str(schema_version)),
        ]},
        {"name": "Mail", "items": [
            _value("Backend", "GAMMA_CLOUD_MAIL", MAIL_BACKEND,
                   {"console": "messages are logged, not sent", "memory": "messages are kept in memory (tests)"}
                   .get(MAIL_BACKEND, "")),
            _value("Sender", "GAMMA_CLOUD_MAIL_FROM", MAIL_FROM),
            _value("SMTP host", "GAMMA_CLOUD_SMTP_HOST", f"{SMTP_HOST}:{SMTP_PORT}" if SMTP_HOST else "",
                   not_used or ("" if SMTP_HOST else "the smtp backend cannot send without it")),
            _secret("SMTP user", "GAMMA_CLOUD_SMTP_USER", SMTP_USER, not_used),
            _secret("SMTP password", "GAMMA_CLOUD_SMTP_PASSWORD", SMTP_PASSWORD, not_used),
            _switch("STARTTLS", "GAMMA_CLOUD_SMTP_STARTTLS", SMTP_STARTTLS, not_used),
        ]},
        {"name": "Stripe", "items": stripe},
        {"name": "Shared server", "items": [
            _value("Address for people", "GAMMA_CLOUD_APP_URL", APP_URL,
                   "" if APP_URL else "Lite and Plus cannot be sold"),
            _value("Share host", "GAMMA_CLOUD_SHARE_HOST_URL", SHARE_HOST_URL,
                   "" if SHARE_HOST_URL else "Gamma servers have nowhere to publish"),
        ]},
        {"name": "Hosting", "items": [
            _value("Domain", "GAMMA_CLOUD_HOSTED_DOMAIN", HOSTED_DOMAIN,
                   "" if HOSTED_DOMAIN else "hosting is off: Pro cannot be sold and no server is made"),
            _value("Name suffix", "GAMMA_CLOUD_HOSTED_SUFFIX", HOSTED_SUFFIX),
            _value("Fleet image", "GAMMA_CLOUD_FLEET_IMAGE", FLEET_IMAGE),
            _value("Image tag", "GAMMA_CLOUD_FLEET_IMAGE_TAG", FLEET_IMAGE_TAG,
                   "used while the Admin page stores none"),
            _switch("DNS records", "GAMMA_CLOUD_CF_API_TOKEN + _ZONE_ID", bool(CF_API_TOKEN and CF_ZONE_ID),
                    "needs both the token and the zone id" if bool(CF_API_TOKEN) != bool(CF_ZONE_ID)
                    else "" if CF_API_TOKEN else "a host with a public IP takes no servers"),
        ]},
        {"name": "Sign-in", "items": [
            _switch("Google", "GAMMA_CLOUD_GOOGLE_CLIENT_ID + _SECRET", google,
                    half if bool(GOOGLE_CLIENT_ID) != bool(GOOGLE_CLIENT_SECRET) else ""),
            _switch("Google one tap", "GAMMA_CLOUD_GOOGLE_ONE_TAP", google and GOOGLE_ONE_TAP),
            _switch("GitHub", "GAMMA_CLOUD_GITHUB_CLIENT_ID + _SECRET", bool(GITHUB_CLIENT_ID and GITHUB_CLIENT_SECRET),
                    half if bool(GITHUB_CLIENT_ID) != bool(GITHUB_CLIENT_SECRET) else ""),
            _value("Desktop client id", "GAMMA_CLOUD_DESKTOP_CLIENT_ID", DESKTOP_CLIENT_ID),
        ]},
    ]
