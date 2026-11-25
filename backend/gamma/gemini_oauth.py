"""Gemini through a Google account sign-in (OAuth, the flow Gemini CLI uses).

Instead of an API key, a provider entry can hold the tokens of a Google
sign-in made with Gemini CLI's public OAuth client. Calls then go to Google's
Code Assist backend (``ai_protocols/gemini.py``) and count against the
account's Gemini Code Assist license. Since June 2026 Google serves that
client only to Gemini Code Assist Standard and Enterprise, an organization's
license on a Google Cloud project the user names; individual accounts (free,
Google AI Pro and Ultra) are refused at setup with a pointer to Antigravity.
Google's terms forbid the client in third-party software, and accounts have
been suspended for it (docs/research/gemini-signin.md). The connect dialog
says so, and a Gemini API key is the supported way.

A sign-in in progress (``begin``) ends in one of two ways:

- caught: when the browser runs on this server's machine, a listener on a
  free loopback port takes Google's redirect, as Gemini CLI does (Google
  lets a desktop client come back to any loopback port);
- pasted: anywhere else Google ends the sign-in on
  codeassist.google.com/authcode, which shows a code the user pastes back.

The code is traded with the PKCE verifier and the client's secret, which
Google doesn't treat as one for an installed app. gamma/cloud_oauth.py makes
the OAuth calls, as for Drive. Then ``setup`` finds the account's Code
Assist project the way Gemini CLI does (loadCodeAssist with the project the
user names, and onboardUser the first time), and its id rides on the entry
as ``account_id``. The tokens wait in the sign-in until setup succeeds, so a
project typed after Google's refusal needs no second sign-in. Google
doesn't rotate refresh tokens. A refresh Google
refuses ends the sign-in; one that gets no verdict raises
RefreshUnavailable and the tokens are kept.
"""

import hashlib
import http.server
import json
import re
import secrets
import threading
import time
import urllib.parse

from . import chatgpt_oauth, cloud_http, cloud_oauth, config, html_page
from .chatgpt_oauth import RefreshUnavailable, _b64url, parse_callback
from .cloud_http import CloudConfigError, CloudError
from .logbuf import log

# The flow's renewal rule (Protocol.oauth): from 5 minutes before expiry.
needs_refresh = chatgpt_oauth.needs_refresh

PROTOCOL = "gemini-signin"
# Gemini CLI's OAuth client (packages/core/src/code_assist/oauth2.ts). A
# desktop client: its secret ships in the CLI's source, and Google says an
# installed app's secret "is obviously not treated as a secret". The secret
# is two literals so secret scanners, which take it for a leaked
# confidential one, pass it by (as config.py's Drive clients).
CLIENT = cloud_oauth.OAuthClient(id="681255809395-oo8ft2oprdrnp9e3aqf6av3hmdib135j.apps.googleusercontent.com",
                                 secret="GOCSPX-" "4uHgMPm-1o7Sk-geV6Cu5clXFsxl")
CLOUD_SCOPE = "https://www.googleapis.com/auth/cloud-platform"
GOOGLE = cloud_oauth.Service(
    name="Google",
    device_url=lambda: "",  # a desktop client gets no device codes from Google
    token_url=lambda: f"{config.google_oauth_base()}/token",
    authorize_url=lambda: f"{config.google_accounts_base()}/o/oauth2/v2/auth",
    scopes=(CLOUD_SCOPE, "https://www.googleapis.com/auth/userinfo.email",
            "https://www.googleapis.com/auth/userinfo.profile"),
    required=CLOUD_SCOPE,
    verification_url="",
    client_hint="Google no longer accepts Gemini CLI's sign-in client",
    # A refresh token every time, not only on the first consent.
    authorize_params=(("access_type", "offline"), ("prompt", "consent")),
)
# Where a sign-in from another machine ends: Google's page showing the code.
CODE_REDIRECT_URI = "https://codeassist.google.com/authcode"
CALLBACK_PATH = "/oauth2callback"
USER_AGENT = "Gamma"

PENDING_TTL_S = 900
EXPIRED = "sign-in session expired — hit 'Open Google sign-in' again"
# What Gemini CLI tells the backend it is (setup.ts coreClientMetadata).
CLIENT_METADATA = {"ideType": "IDE_UNSPECIFIED", "platform": "PLATFORM_UNSPECIFIED", "pluginType": "GEMINI"}
ONBOARD_WAIT_S = 60
ONBOARD_POLL_S = 5
NEEDS_PROJECT = ("this Google account's Gemini plan needs its Google Cloud project: enter the project's ID "
                 "under Google Cloud project and connect again")
# Google's answer to an individual account since June 2026 names no way on;
# this says which ones are left.
RETIRED = ("Google retired this sign-in for individual accounts (free, Google AI Pro and Ultra) in June 2026. "
           "With Gemini Code Assist Standard or Enterprise, enter your organization's Google Cloud project and "
           "connect again; otherwise use a Gemini API key")
# A Google Cloud project ID (not its number, which Code Assist refuses).
PROJECT_RE = re.compile(r"[a-z][a-z0-9-]{4,28}[a-z0-9]")


def _google(method: str, url: str, token: str, body: dict | None = None) -> dict:
    """One JSON call to Google with the sign-in's access token, through
    gamma/cloud_http.py (the transport the Drive sign-in uses). ValueError
    with Google's own words for an error answer; CloudUnreachable (a
    CloudError) when none came."""
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json", "User-Agent": USER_AGENT}
    if body is not None:
        headers["Content-Type"] = "application/json"
    resp = cloud_http.request(method, url, headers, None if body is None else json.dumps(body).encode())
    if resp.status >= 400:
        message = cloud_http.error(resp)[1]
        raise ValueError(f"Google answered {resp.status}{f': {message[:200]}' if message else ''}")
    return resp.json()


def code_assist(token: str, method: str, body: dict | None = None) -> dict:
    """One call to the Code Assist backend: POST ``{base}:{method}``, or GET
    ``{base}/{method}`` for an operation's name (``operations/…``)."""
    base = config.AI_BASE_URLS[PROTOCOL]
    if method.startswith("operations/"):
        return _google("GET", f"{base}/{method}", token)
    return _google("POST", f"{base}:{method}", token, body or {})


def _project_id(value) -> str:
    """A Code Assist project as loadCodeAssist names it (a string) or as
    onboardUser does ({id, name})."""
    if isinstance(value, dict):
        value = value.get("id")
    return value.strip() if isinstance(value, str) else ""


def _ineligible(loaded: dict) -> str:
    """Why Google says the account can't use a tier, "" when it says nothing."""
    reasons = [str(t.get("reasonMessage") or "").strip() for t in loaded.get("ineligibleTiers") or []
               if isinstance(t, dict)]
    reasons = [r for r in reasons if r]
    return f"Google says this account can't use Gemini here: {'; '.join(reasons)}" if reasons else ""


def _refusal(loaded: dict, project: str) -> str:
    """What to tell the user when no tier is open to them: Google's reasons,
    and without a project the ways left (RETIRED), or that one is needed."""
    said = _ineligible(loaded)
    if project:
        return said or f"Google offers no Gemini Code Assist tier through the project {project}"
    return f"{said.rstrip('.')}. {RETIRED}" if said else NEEDS_PROJECT


def setup(token: str, project: str = "") -> dict:
    """The account's Code Assist project and plan, ``{account_id, plan}``,
    found as Gemini CLI finds them (setup.ts). ``project``: the Google Cloud
    project a Gemini Code Assist Standard or Enterprise license is on, which
    the user names (Gemini CLI's GOOGLE_CLOUD_PROJECT). Raises ValueError
    with what to tell the user, "Gemini setup failed: …"."""
    try:
        if project and not PROJECT_RE.fullmatch(project):
            raise ValueError(f"{project!r} is no Google Cloud project ID: one looks like my-project-123 "
                             "(its ID, not its number)")
        return _setup(token, project)
    except (ValueError, CloudError) as e:  # Google's refusal, or no answer
        raise ValueError(f"Gemini setup failed: {e}") from e


def _setup(token: str, project: str) -> dict:
    """loadCodeAssist names the project and plan once the account is set up
    (an organization's license may leave the project to the one named).
    Otherwise the default tier is set up with onboardUser, a long-running
    operation polled for up to ONBOARD_WAIT_S: on the named project for a
    tier that needs the user's own, with none for the free tier, whose
    project is Google's (naming one fails the call)."""
    metadata = {**CLIENT_METADATA, **({"duetProject": project} if project else {})}
    loaded = code_assist(token, "loadCodeAssist",
                         {**({"cloudaicompanionProject": project} if project else {}), "metadata": metadata})
    current = loaded.get("currentTier") if isinstance(loaded.get("currentTier"), dict) else None
    paid = loaded.get("paidTier") if isinstance(loaded.get("paidTier"), dict) else {}
    if current:
        found = _project_id(loaded.get("cloudaicompanionProject")) or project
        if not found:
            raise ValueError(_refusal(loaded, project))
        return {"account_id": found, "plan": str(paid.get("name") or current.get("name") or "")}
    tiers = [t for t in loaded.get("ineligibleTiers") or [] if isinstance(t, dict)]
    check = next((t for t in tiers if t.get("reasonCode") == "VALIDATION_REQUIRED" and t.get("validationUrl")), None)
    if check:
        raise ValueError(f"Google asks to verify this account first: open {check['validationUrl']}, then sign in again")
    tier = next((t for t in loaded.get("allowedTiers") or [] if isinstance(t, dict) and t.get("isDefault")), None)
    own = bool(tier and tier.get("userDefinedCloudaicompanionProject"))
    if not tier or not tier.get("id") or (own and not project):
        raise ValueError(_refusal(loaded, project))
    onboard = ({"tierId": tier["id"], "cloudaicompanionProject": project, "metadata": metadata} if own
               else {"tierId": tier["id"], "metadata": CLIENT_METADATA})
    operation = code_assist(token, "onboardUser", onboard)
    deadline = time.time() + ONBOARD_WAIT_S
    while not operation.get("done"):
        if time.time() > deadline:
            raise ValueError("Google is still setting up Gemini for this account; connect again in a minute")
        time.sleep(ONBOARD_POLL_S)
        name = str(operation.get("name") or "")
        operation = code_assist(token, name) if name.startswith("operations/") else code_assist(token, "onboardUser", onboard)
    found = _project_id((operation.get("response") or {}).get("cloudaicompanionProject")) or project
    if not found:
        raise ValueError(_refusal(loaded, project))
    return {"account_id": found, "plan": str(tier.get("name") or "")}


def _email(token: str) -> str:
    """The signed-in account's e-mail, "" when Google doesn't say (the
    sign-in works without it)."""
    try:
        return str(_google("GET", f"{config.google_api_base()}/oauth2/v2/userinfo", token).get("email") or "")
    except (ValueError, CloudError) as e:
        log.info(f"[gemini-oauth] no e-mail for the sign-in: {e}")
        return ""


def exchange_code(code: str, verifier: str, redirect_uri: str) -> dict:
    """Redeem an authorization code: the sign-in's tokens ({access_token,
    refresh_token, expires_at, email}), which ``setup`` then completes into
    the dict stored on the entry. Raises ValueError with what to tell the
    user when Google refuses."""
    try:
        got = cloud_oauth.exchange_code(GOOGLE, CLIENT, code, verifier, redirect_uri)
    except (CloudError, CloudConfigError) as e:
        raise ValueError(f"token exchange failed: {e}") from e
    if got.get("state") != "done":
        raise ValueError("the sign-in didn't allow Gemini: sign in again and allow every permission Google asks for")
    token = got["access_token"]
    return {"access_token": token, "refresh_token": got["refresh_token"],
            "expires_at": int(time.time()) + int(got["expires_in"]), "email": _email(token)}


def refresh(oauth: dict) -> dict | None:
    """Refreshed copy of the oauth dict, or None when Google refuses the
    grant (revoked, unused for months, the client refused): the user signs
    in again. Any other failure raises RefreshUnavailable and the caller
    keeps the tokens. One try: it runs in front of a chat request."""
    token = oauth.get("refresh_token")
    if not token:
        return None
    try:
        access, lifetime, kept = cloud_oauth.refresh(GOOGLE, CLIENT, token, attempts=1)
    except CloudConfigError as e:
        log.warning(f"[gemini-oauth] refresh refused: {e}")
        return None
    except Exception as e:
        reason = f"{type(e).__name__}: {e}"
        log.warning(f"[gemini-oauth] refresh failed, will retry: {reason}")
        raise RefreshUnavailable(reason) from e
    kept_fields = {k: v for k, v in oauth.items() if k not in ("refresh_failed_at", "refresh_unreachable")}
    return {**kept_fields, "access_token": access, "refresh_token": kept,
            "expires_at": int(time.time()) + int(lifetime)}


# --- sign-ins in progress ------------------------------------------------------
# state -> {"verifier", "redirect", "owner", "at", "local", "tokens", "busy",
# "error"}, in memory: a restart forgets them. `owner` is who may redeem it,
# as in chatgpt_oauth. `tokens` holds the sign-in's tokens once the code is
# traded (by the listener, or by the first redeem of a pasted one), and they
# stay until ``setup`` succeeds: a setup Google refuses for want of a
# project is tried again with one, without signing in again. `busy`: a
# redeem under way, so a second one waits its turn rather than trade the
# code twice.

_PENDING: dict = {}
_LOCK = threading.Lock()  # _PENDING and the callback listener
_server = None            # the running listener, under _LOCK


def _live(rec, now: float) -> bool:
    return rec is not None and now - rec["at"] <= PENDING_TTL_S


def begin(owner, *, local: bool = False, device: bool = False) -> dict:
    """Start a sign-in for ``owner``: {auth_url, state, local, device}.
    ``local``: the browser runs on this machine, so listen on a loopback
    port for Google's redirect; the answer's ``local`` says whether that
    worked. Otherwise Google ends on its code page. ``device`` is ignored:
    Google gives this client no device codes, so ``device`` is always None."""
    state = _b64url(secrets.token_bytes(24))
    verifier = _b64url(secrets.token_bytes(48))
    challenge = _b64url(hashlib.sha256(verifier.encode("ascii")).digest())
    now = time.time()
    rec = {"verifier": verifier, "redirect": CODE_REDIRECT_URI, "owner": owner, "at": now, "local": local,
           "tokens": None, "busy": False, "error": ""}
    # Waiting before the listener opens, so it never closes for want of one.
    with _LOCK:
        for k in [k for k, v in _PENDING.items() if not _live(v, now)]:
            del _PENDING[k]
        _PENDING[state] = rec
    port = _listen() if local else 0
    with _LOCK:
        rec["local"] = bool(port)
        if port:
            rec["redirect"] = f"http://127.0.0.1:{port}{CALLBACK_PATH}"
    return {"auth_url": cloud_oauth.authorize_url(GOOGLE, CLIENT, rec["redirect"], state, challenge),
            "state": state, "local": rec["local"], "device": None}


def status(owner, state: str) -> dict:
    """{ready, error} of a sign-in ``owner`` started: ready once the
    listener has its tokens."""
    with _LOCK:
        rec = _PENDING.get(state)
        if not _live(rec, time.time()) or rec["owner"] != owner:
            return {"ready": False, "error": EXPIRED}
        return {"ready": bool(rec["tokens"]), "error": "" if rec["tokens"] else rec["error"]}


def redeem(owner, state: str, callback: str = "", project: str = "") -> dict:
    """End a sign-in ``owner`` started and return the dict stored on the
    entry: its tokens (the listener's, else the pasted code, or the address
    the redirect ended on, traded with the PKCE verifier) set up with the
    account's Code Assist project (``setup``; ``project`` the one the user
    names). Raises ValueError with what to tell the user. A paste that
    doesn't parse, someone else's attempt and a setup that fails leave the
    sign-in waiting, its tokens kept for the next try."""
    callback, project = (callback or "").strip(), (project or "").strip()
    with _LOCK:
        rec = _PENDING.get(state)
        if not _live(rec, time.time()) or rec["owner"] != owner:
            raise ValueError(EXPIRED)
        if rec["busy"]:
            raise ValueError("the sign-in is being completed already")
        tokens = rec["tokens"]
        if not tokens and not callback:
            raise ValueError(rec["error"] or "not signed in yet — finish the sign-in, or paste the code Google shows")
        code = "" if tokens else parse_callback(callback, state)
        rec["busy"] = True
    try:
        if not tokens:
            tokens = exchange_code(code, rec["verifier"], rec["redirect"])  # the code is spent now
            with _LOCK:
                rec["tokens"] = tokens
        oauth = {**tokens, **setup(tokens["access_token"], project)}
    finally:
        with _LOCK:
            rec["busy"] = False
    with _LOCK:
        if _PENDING.pop(state, None) is None:
            raise ValueError(EXPIRED)  # ran out meanwhile
    return oauth


# --- the redirect, caught on this machine --------------------------------------

def _capture(query: dict) -> tuple[bool, str]:
    """Take a redirect's code for the sign-in its state names: (ok, what the
    browser tab says)."""
    state = query.get("state", "")
    with _LOCK:
        rec = _PENDING.get(state)
        if not _live(rec, time.time()) or not rec["local"]:
            return False, ("Gamma is not waiting for this sign-in. Start it again in Gamma, "
                           "or paste this page's address into the sign-in form.")
        if rec["tokens"]:
            return True, ""
    if query.get("error"):
        error = f"Google refused the sign-in: {query.get('error_description') or query['error']}"
    else:
        try:
            tokens = exchange_code(query.get("code", ""), rec["verifier"], rec["redirect"])
            with _LOCK:
                rec["tokens"] = tokens
            return True, ""
        except ValueError as e:
            error = str(e)
        except Exception as e:
            error = f"token exchange failed: {e}"
    with _LOCK:
        if not rec["tokens"]:
            rec["error"] = error
    return False, error


class _CallbackHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        parts = urllib.parse.urlsplit(self.path)
        if parts.path != CALLBACK_PATH:
            self.send_error(404)
            return
        ok, message = _capture({k: v[0] for k, v in urllib.parse.parse_qs(parts.query).items()})
        body = html_page.page("Signed in with Google" if ok else "The Google sign-in did not finish",
                              message or "You can close this tab: Gamma finishes connecting in its own window.").encode()
        self.send_response(200 if ok else 400)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        for name, value in html_page.headers().items():
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):  # the server log is logbuf's
        pass


class _CallbackServer(http.server.HTTPServer):
    timeout = 1


def _listen() -> int:
    """The loopback port Google's redirect comes back to while a sign-in on
    this machine waits (a free one, opened at the first such sign-in); 0
    when none could be opened."""
    global _server
    with _LOCK:
        if _server is None:
            try:
                _server = _CallbackServer(("127.0.0.1", 0), _CallbackHandler)
            except OSError as e:
                log.info(f"[gemini-oauth] no loopback port for the sign-in ({e}); the code will be pasted")
                return 0
            threading.Thread(target=_serve, args=(_server,), name="gemini-callback", daemon=True).start()
        return _server.server_address[1]


def _serve(server) -> None:
    global _server
    while True:
        server.handle_request()
        with _LOCK:
            now = time.time()
            if not any(r["local"] and not r["tokens"] and _live(r, now) for r in _PENDING.values()):
                _server = None
                server.server_close()
                return
