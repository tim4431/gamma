"""ChatGPT subscription sign-in (OAuth PKCE, the flow Codex CLI uses).

Instead of an API key, a provider entry can hold OAuth tokens obtained by
signing in with a ChatGPT account — usage is then covered by the user's
Plus/Pro subscription. The flow targets auth.openai.com with Codex CLI's
public client id, whose only registered redirect is
http://localhost:1455/auth/callback. A sign-in in progress (``begin``) ends
the first of three ways:

- caught: when the browser runs on this server's machine (the desktop app's
  own server, a localhost install), a listener on 127.0.0.1:1455 takes the
  redirect, as Codex CLI does;
- device code: the user enters a one-time code at auth.openai.com/codex/device
  and ``status`` polls for the result (Codex CLI's ``--device-auth``; the
  account, or its workspace's admin, must have turned it on);
- pasted: anywhere else the redirect page fails to load, and the user pastes
  its address back.

``redeem`` hands over the tokens, the code exchanged server-side with the
PKCE verifier. They are stored in the provider entry (users.db `ai-settings`
pref, same protection as API keys: never sent back to the browser). Access
tokens expire; ai_runtime() refreshes them lazily via the refresh token.
"""

import base64
import hashlib
import html
import http.server
import json
import os
import secrets
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

from .logbuf import log

AUTH_BASE = "https://auth.openai.com"
CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"  # Codex CLI's public client id
CALLBACK_PORT = 1455
REDIRECT_URI = f"http://localhost:{CALLBACK_PORT}/auth/callback"
SCOPE = "openid profile email offline_access"
# Codex CLI's device code sign-in (codex-rs/login/src/device_code_auth.rs).
DEVICE_API = f"{AUTH_BASE}/api/accounts/deviceauth"
DEVICE_PAGE = f"{AUTH_BASE}/codex/device"
DEVICE_REDIRECT_URI = f"{AUTH_BASE}/deviceauth/callback"

# How long a sign-in waits; OpenAI's device codes also expire after 15 min.
PENDING_TTL_S = 900
EXPIRED = "sign-in session expired — hit 'Open ChatGPT sign-in' again"

# Refresh slightly early so a token can't expire mid-request.
REFRESH_MARGIN_S = 300


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _jwt_claims(token: str) -> dict:
    """Unverified JWT payload (we only mine ids/expiry out of our own tokens)."""
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        return json.loads(base64.urlsafe_b64decode(payload))
    except Exception:
        return {}


def start_auth() -> tuple[str, str, str]:
    """(state, pkce_verifier, authorization_url) for a fresh sign-in."""
    verifier = _b64url(secrets.token_bytes(48))
    challenge = _b64url(hashlib.sha256(verifier.encode("ascii")).digest())
    state = _b64url(secrets.token_bytes(24))
    params = {
        "response_type": "code",
        "client_id": CLIENT_ID,
        "redirect_uri": REDIRECT_URI,
        "scope": SCOPE,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "state": state,
        # Same extras Codex CLI sends — the ChatGPT-account variant of the flow.
        "id_token_add_organizations": "true",
        "codex_cli_simplified_flow": "true",
        "originator": "codex_cli_rs",
    }
    url = f"{AUTH_BASE}/oauth/authorize?{urllib.parse.urlencode(params)}"
    return state, verifier, url


def parse_callback(pasted: str, expect_state: str) -> str:
    """Authorization code from a pasted callback URL (or a bare code).

    Users paste the full http://localhost:1455/auth/callback?code=…&state=…
    address their browser failed to load; tolerate a raw code too."""
    pasted = (pasted or "").strip()
    if not pasted:
        raise ValueError("paste the callback URL from the browser's address bar")
    if "://" in pasted or "?" in pasted or "code=" in pasted:
        q = urllib.parse.parse_qs(urllib.parse.urlsplit(pasted).query)
        code = (q.get("code") or [""])[0]
        state = (q.get("state") or [""])[0]
        if not code:
            raise ValueError("no ?code= in the pasted URL — copy the full address the browser was redirected to")
        if state and expect_state and state != expect_state:
            raise ValueError("state mismatch — start the sign-in again and paste the new URL")
        return code
    return pasted  # looks like a bare authorization code


def _token_request(form: dict) -> dict:
    req = urllib.request.Request(
        f"{AUTH_BASE}/oauth/token",
        data=urllib.parse.urlencode(form).encode(),
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read())


def _device_request(path: str, body: dict) -> dict:
    req = urllib.request.Request(
        f"{DEVICE_API}/{path}",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read())


def _oauth_entry(tokens: dict, previous: dict | None = None) -> dict:
    """Normalize a token response into what we persist on the provider entry."""
    prev = previous or {}
    access = tokens.get("access_token") or prev.get("access_token") or ""
    id_token = tokens.get("id_token") or ""
    id_claims = _jwt_claims(id_token)
    access_claims = _jwt_claims(access)
    auth_claims = (access_claims.get("https://api.openai.com/auth")
                   or id_claims.get("https://api.openai.com/auth") or {})
    account_id = (auth_claims.get("chatgpt_account_id")
                  or prev.get("account_id") or "")
    exp = access_claims.get("exp")
    expires_at = int(exp) if isinstance(exp, (int, float)) else int(time.time()) + 3600
    return {
        "access_token": access,
        "refresh_token": tokens.get("refresh_token") or prev.get("refresh_token") or "",
        "account_id": account_id,
        "email": id_claims.get("email") or prev.get("email") or "",
        "expires_at": expires_at,
    }


def exchange_code(code: str, verifier: str, redirect_uri: str = REDIRECT_URI) -> dict:
    """Redeem an authorization code; returns the dict stored on the entry."""
    tokens = _token_request({
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": redirect_uri,
        "client_id": CLIENT_ID,
        "code_verifier": verifier,
    })
    oauth = _oauth_entry(tokens)
    if not oauth["access_token"]:
        raise ValueError("token exchange returned no access token")
    if not oauth["account_id"]:
        raise ValueError("no ChatGPT account id in the token — is this a ChatGPT (not platform) account?")
    return oauth


def needs_refresh(oauth: dict) -> bool:
    exp = oauth.get("expires_at") or 0
    return time.time() > exp - REFRESH_MARGIN_S


def refresh(oauth: dict) -> dict | None:
    """Refreshed copy of the oauth dict, or None when refresh fails (expired /
    revoked grant — the user has to sign in again)."""
    token = oauth.get("refresh_token")
    if not token:
        return None
    try:
        tokens = _token_request({
            "grant_type": "refresh_token",
            "refresh_token": token,
            "client_id": CLIENT_ID,
            "scope": "openid profile email",
        })
        return _oauth_entry(tokens, previous=oauth)
    except Exception as e:
        log.warning(f"[chatgpt-oauth] refresh failed: {e}")
        return None


# --- the device code -----------------------------------------------------------

def request_device_code() -> dict:
    """A one-time code for the device sign-in: {device_auth_id, user_code,
    interval}. Raises when OpenAI offers none."""
    data = _device_request("usercode", {"client_id": CLIENT_ID})
    user_code = data.get("user_code") or data.get("usercode") or ""
    if not data.get("device_auth_id") or not user_code:
        raise ValueError("no device code in the answer")
    try:
        interval = int(str(data.get("interval") or "0").strip())
    except ValueError:
        interval = 0
    return {"device_auth_id": data["device_auth_id"], "user_code": user_code,
            "interval": interval if interval > 0 else 5}


def poll_device(device: dict) -> dict | None:
    """The tokens once the user has entered the code; None while they have
    not (OpenAI answers 403 or 404 until then)."""
    try:
        data = _device_request("token", {"device_auth_id": device["device_auth_id"],
                                         "user_code": device["user_code"]})
    except urllib.error.HTTPError as e:
        if e.code in (403, 404):
            return None
        raise
    return exchange_code(data.get("authorization_code") or "", data.get("code_verifier") or "",
                         redirect_uri=DEVICE_REDIRECT_URI)


# --- sign-ins in progress ------------------------------------------------------
# state -> {"verifier", "owner", "at", "local", "device", "oauth", "error"}, in
# memory: a restart forgets them and the user starts again. `owner` is who
# may redeem it (routers/ai.py: an account name, or ("server", <admin>) for a
# shared entry); `oauth` holds the tokens once the server has them itself.

_PENDING: dict = {}
_LOCK = threading.Lock()  # _PENDING and the callback listener


def _live(rec, now: float) -> bool:
    return rec is not None and now - rec["at"] <= PENDING_TTL_S


def begin(owner, *, local: bool = False, device: bool = False) -> dict:
    """Start a sign-in for ``owner``: {auth_url, state, local, device}.

    ``local``: the browser runs on this machine, so listen on localhost:1455
    for its redirect; the answer's ``local`` says whether the port was free.
    ``device``: when nothing listens, also ask for a one-time code — the
    answer's ``device`` is {user_code, verification_url}, or None when OpenAI
    offers none."""
    state, verifier, url = start_auth()
    now = time.time()
    rec = {"verifier": verifier, "owner": owner, "at": now, "local": local,
           "device": None, "oauth": None, "error": ""}
    with _LOCK:
        for k in [k for k, v in _PENDING.items() if not _live(v, now)]:
            del _PENDING[k]
        _PENDING[state] = rec
    if local and not _listen():
        with _LOCK:
            rec["local"] = False
    if device and not rec["local"]:
        try:
            code = request_device_code()
            with _LOCK:
                rec["device"] = {**code, "next_poll": now + code["interval"], "polling": False}
        except Exception as e:
            log.info(f"[chatgpt-oauth] no device code: {e}")
    shown = rec["device"]
    return {"auth_url": url, "state": state, "local": rec["local"],
            "device": {"user_code": shown["user_code"], "verification_url": DEVICE_PAGE} if shown else None}


def status(owner, state: str) -> dict:
    """{ready, error} of a sign-in ``owner`` started. A device code is polled
    here, at most once per OpenAI's interval: the form asks every few seconds
    while it waits, so nothing polls once it stops asking."""
    now = time.time()
    with _LOCK:
        rec = _PENDING.get(state)
        if not _live(rec, now) or rec["owner"] != owner:
            return {"ready": False, "error": EXPIRED}
        device = rec["device"]
        due = bool(device and not rec["oauth"] and not rec["error"]
                   and not device["polling"] and now >= device["next_poll"])
        if due:
            device["polling"] = True
    if due:
        oauth, error = None, ""
        try:
            oauth = poll_device(device)
        except (urllib.error.HTTPError, ValueError) as e:
            error = f"device code sign-in failed: {e}"
        except Exception as e:  # the network, not the sign-in: ask again next time
            log.info(f"[chatgpt-oauth] device poll: {e}")
        with _LOCK:
            device["polling"] = False
            device["next_poll"] = time.time() + device["interval"]
            if oauth and not rec["oauth"]:
                rec["oauth"] = oauth
            elif error and not rec["oauth"]:
                rec["error"] = error
    return {"ready": bool(rec["oauth"]), "error": "" if rec["oauth"] else rec["error"]}


def redeem(owner, state: str, callback: str = "") -> dict:
    """End a sign-in ``owner`` started and return its tokens: those the server
    already has (caught, or through the device code), else the pasted
    ``callback`` URL's code (or a bare code) exchanged with the PKCE verifier.
    Raises ValueError with what to tell the user; a paste that doesn't parse,
    or someone else's attempt, leaves the sign-in waiting."""
    callback = (callback or "").strip()
    with _LOCK:
        rec = _PENDING.get(state)
        if not _live(rec, time.time()) or rec["owner"] != owner:
            raise ValueError(EXPIRED)
        caught = rec["oauth"]
        if not caught and not callback:
            raise ValueError(rec["error"] or "not signed in yet — finish the sign-in, or paste the address it ended on")
    code = "" if caught else parse_callback(callback, state)
    with _LOCK:
        if _PENDING.pop(state, None) is None:
            raise ValueError(EXPIRED)  # redeemed meanwhile
    return caught or exchange_code(code, rec["verifier"])


# --- the redirect, caught on this machine --------------------------------------

_server = None  # the running listener, under _LOCK

_PAGE = """<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<title>Gamma · ChatGPT sign-in</title>
<body style="font:15px/1.5 system-ui,sans-serif;max-width:34em;margin:18vh auto;padding:0 16px">
<h1 style="font-size:20px">{title}</h1><p>{message}</p>"""


def _capture(query: dict) -> tuple[bool, str]:
    """Take a redirect's code for the sign-in its state names: (ok, what the
    browser tab says)."""
    state = query.get("state", "")
    with _LOCK:
        rec = _PENDING.get(state)
        if not _live(rec, time.time()):
            return False, ("Gamma is not waiting for this sign-in. Start it again in Gamma, "
                           "or paste this page's address into the sign-in form.")
        if rec["oauth"]:
            return True, ""
    if query.get("error"):
        error = f"OpenAI refused the sign-in: {query.get('error_description') or query['error']}"
    else:
        try:
            oauth = exchange_code(query.get("code", ""), rec["verifier"])
            with _LOCK:
                rec["oauth"] = oauth
            return True, ""
        except Exception as e:
            error = f"token exchange failed: {e}"
    with _LOCK:
        if not rec["oauth"]:
            rec["error"] = error
    return False, error


class _CallbackHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        parts = urllib.parse.urlsplit(self.path)
        if parts.path != "/auth/callback":
            self.send_error(404)
            return
        ok, message = _capture({k: v[0] for k, v in urllib.parse.parse_qs(parts.query).items()})
        body = _PAGE.format(
            title="Signed in to ChatGPT" if ok else "The ChatGPT sign-in did not finish",
            message=html.escape(message or "Gamma is connected. You can close this tab."),
        ).encode()
        self.send_response(200 if ok else 400)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):  # the server log is logbuf's
        pass


class _CallbackServer(http.server.HTTPServer):
    # Windows' SO_REUSEADDR would let this bind a port another program
    # (Codex CLI's own login) is listening on; elsewhere it only skips
    # TIME_WAIT, which a second sign-in soon after the first needs.
    allow_reuse_address = os.name != "nt"
    timeout = 1


def _listen() -> bool:
    """Listen on localhost:1455 while a sign-in on this machine waits; False
    when the port is taken (Codex CLI's own login, another Gamma server)."""
    global _server
    with _LOCK:
        if _server is not None:
            return True
        try:
            _server = _CallbackServer(("127.0.0.1", CALLBACK_PORT), _CallbackHandler)
        except OSError as e:
            log.info(f"[chatgpt-oauth] localhost:{CALLBACK_PORT} is taken ({e}); the address will be pasted")
            return False
        threading.Thread(target=_serve, args=(_server,), name="chatgpt-callback", daemon=True).start()
    return True


def _serve(server) -> None:
    global _server
    while True:
        server.handle_request()
        with _LOCK:
            now = time.time()
            if not any(r["local"] and not r["oauth"] and _live(r, now) for r in _PENDING.values()):
                _server = None
                server.server_close()
                return
