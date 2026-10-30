"""The Zotero broker: Gamma Cloud's Zotero app, through which a Gamma server
or desktop sidecar without one of its own gets the Zotero API key its
Zotero links use (docs/dev/cloud_accounts.md "Connecting Zotero").

Zotero issues keys through OAuth 1.0a to an app registered on zotero.org
(``GAMMA_CLOUD_ZOTERO_CLIENT_KEY`` / ``_SECRET``), and a sidecar's loopback
port changes from run to run, so the broker holds the app and relays:
``start`` keeps the request token while the browser is at Zotero
(``zotero_requests``), ``issue`` keeps the person's approval under a
one-time code bound to the server's address and PKCE challenge
(``zotero_codes``), and ``redeem`` gives it to that server alone. Only then
is the approval traded for the key (``access_token``), so one never
redeemed never becomes a key.

A key goes only to a server this account server knows (``known``). Zotero's
consent page names the key after it (``key_name``), and the Gamma server
checks that the browser coming back is the one that started
(backend/gamma/routers/zotero.py). Rows are keyed by a hash and sealed with
a key derived from a token they do not hold, so cloud.db replays nothing.
"""

import base64
import hashlib
import hmac
import json
import secrets
import time
import urllib.error
import urllib.parse
import urllib.request
from urllib.parse import urlsplit

from cryptography.fernet import Fernet

from . import config, hosted, oidc
from .accounts import Problem
from .db import after, begin_write, new_token, now, token_hash
from .identities import derive
from .log import log
from .servers import allowed_for, is_local, norm_url

WEB_BASE = "https://www.zotero.org"
CALLBACK_PATH = "/zotero/callback"
RETURN_PATH = "/api/zotero/oauth/callback"   # the Gamma server's CALLBACK_PATH
# What a Zotero link needs: read and write every library the person has,
# notes included. The same as backend/gamma/zotero_oauth.py ``authorize_url``.
ACCESS = {"library_access": 1, "notes_access": 1, "write_access": 1, "all_groups": "write"}
TIMEOUT = 15
MAX_ANSWER = 64 * 1024   # Zotero's form answers are a few hundred bytes


def enabled() -> bool:
    return bool(config.ZOTERO_CLIENT_KEY and config.ZOTERO_CLIENT_SECRET)


# --- who may ask --------------------------------------------------------------

def _client_kinds(conn, origin: str) -> set[str]:
    """The kinds of the clients here with a redirect URI on ``origin``."""
    rows = conn.execute("SELECT kind, redirect_uris FROM oauth_clients WHERE instr(lower(redirect_uris), ?) > 0",
                        (urlsplit(origin).hostname,)).fetchall()
    return {r["kind"] for r in rows
            if allowed_for({"kind": r["kind"], "redirect_uris": json.loads(r["redirect_uris"])}, origin)}


def known(conn, origin: str) -> bool:
    """Whether a key may go to ``origin``: a loopback server (every sidecar),
    a live hosted server, or one with a client of its own here."""
    return is_local(origin) or hosted.is_server_url(conn, origin) or bool(_client_kinds(conn, origin))


def ours(conn, origin: str) -> bool:
    """Whether ``origin`` runs Gamma Cloud's own code (a live hosted server,
    a ``container`` or ``share-host`` client), whose check of the returning
    browser nobody can change: its keys are not capped."""
    return hosted.is_server_url(conn, origin) or bool(_client_kinds(conn, origin) & {"container", "share-host"})


def check(conn, server: str, state: str, challenge: str) -> str:
    """The normalized address of a connect request, or Problem 400 (a bad
    request) or 403 (a server this account server does not know)."""
    origin = norm_url(server)
    if not oidc.CHALLENGE_RE.match(challenge or ""):
        raise Problem(400, "The request carries no PKCE code challenge. Start again from Gamma.")
    if not state or len(state) > 512:
        raise Problem(400, "The request carries no state. Start again from Gamma.")
    if not known(conn, origin):
        raise Problem(403, f"Gamma Cloud connects Zotero only for a Gamma server it knows: one on this computer, a "
                           f"hosted server, or one connected to Gamma Cloud, which {origin} is not. Its admin can "
                           "connect it under Settings → Server → Sign-in; until then, paste a Zotero API key in Gamma.")
    return origin


def key_name(origin: str) -> str:
    """The new key's name on zotero.org, so the person can tell it apart."""
    return "Gamma desktop app" if is_local(origin) else f"Gamma at {urlsplit(origin).hostname}"


def return_url(origin: str, params: dict) -> str:
    return oidc.redirect_with(origin + RETURN_PATH, params)


# --- the round trip -----------------------------------------------------------

def request_token() -> tuple[str, str]:
    """A request token and its secret from zotero.org, which sends the
    browser back to ``CALLBACK_PATH``."""
    out = _signed_post("/oauth/request", oauth_callback=config.PUBLIC_URL + CALLBACK_PATH)
    token, secret = out.get("oauth_token", ""), out.get("oauth_token_secret", "")
    if not token or not secret:
        log.warning("zotero.org answered the request token call with %s", sorted(out))
        raise Problem(502, "Zotero answered without a request token. Try again in a moment.")
    return token, secret


def start(conn, token: str, secret: str, origin: str, state: str, challenge: str) -> str:
    """Keep the request while the browser is at Zotero; the authorize URL."""
    conn.execute("INSERT INTO zotero_requests (token_hash, secret, server, state, code_challenge, expires_at) "
                 "VALUES (?, ?, ?, ?, ?, ?)",
                 (token_hash(token), _seal(token, "zotero-request", secret), origin, state, challenge,
                  after(config.ZOTERO_REQUEST_TTL)))
    return WEB_BASE + "/oauth/authorize?" + urllib.parse.urlencode(
        {"oauth_token": token, **ACCESS, "name": key_name(origin)})


def take(conn, token: str) -> dict | None:
    """The request a Zotero callback names, removed (it works once) with its
    secret unsealed; None when unknown, used or expired. The caller commits
    either way."""
    if not token:
        return None
    begin_write(conn)
    row = conn.execute("SELECT * FROM zotero_requests WHERE token_hash = ?", (token_hash(token),)).fetchone()
    if not row:
        return None
    conn.execute("DELETE FROM zotero_requests WHERE token_hash = ?", (row["token_hash"],))
    if row["expires_at"] <= now():
        return None
    return {**dict(row), "secret": _unseal(token, "zotero-request", row["secret"])}


def issue(conn, flow: dict, token: str, verifier: str) -> str:
    """Keep the approval of ``flow`` (as ``take`` gave it) under a new
    one-time code; the URL that takes the browser back to its server."""
    code = new_token(32)
    approval = json.dumps({"token": token, "secret": flow["secret"], "verifier": verifier})
    conn.execute("INSERT INTO zotero_codes (code_hash, approval, server, code_challenge, expires_at) "
                 "VALUES (?, ?, ?, ?, ?)",
                 (token_hash(code), _seal(code, "zotero-approval", approval), flow["server"], flow["code_challenge"],
                  after(config.ZOTERO_CODE_TTL)))
    return return_url(flow["server"], {"code": code, "state": flow["state"]})


def redeem(conn, code: str, verifier: str, server: str) -> dict:
    """The approval a server's code holds, ``{token, secret, verifier,
    server}``; Problem 400 for a code that is unknown, used, expired, for
    another address or whose PKCE verifier does not match. The first try
    spends the code, so the caller commits even on a Problem."""
    begin_write(conn)
    row = conn.execute("SELECT * FROM zotero_codes WHERE code_hash = ?", (token_hash(code or ""),)).fetchone()
    if not row:
        raise Problem(400, "unknown or used code")
    conn.execute("DELETE FROM zotero_codes WHERE code_hash = ?", (row["code_hash"],))
    try:
        origin = norm_url(server)
    except Problem:
        origin = ""
    if row["expires_at"] <= now() or origin != row["server"] or not oidc._pkce_ok(verifier, row["code_challenge"]):
        raise Problem(400, "the code is expired or does not belong to this server")
    return {**json.loads(_unseal(code, "zotero-approval", row["approval"])), "server": origin}


def access_token(token: str, secret: str, verifier: str) -> dict:
    """Trade an approval for the key, which Zotero makes now: ``{api_key,
    user_id, username}`` (Zotero's answer carries the key as
    ``oauth_token_secret``)."""
    out = _signed_post("/oauth/access", secret, oauth_token=token, oauth_verifier=verifier)
    key, user_id = out.get("oauth_token_secret", ""), out.get("userID", "")
    if not key or not user_id.isdigit():
        log.warning("zotero.org answered the access token call with %s", sorted(out))
        raise Problem(502, "Zotero answered without an API key. Start again from Gamma.")
    return {"api_key": key, "user_id": int(user_id), "username": out.get("username", "")}


def purge_expired(conn) -> None:
    ts = now()
    conn.execute("DELETE FROM zotero_requests WHERE expires_at <= ?", (ts,))
    conn.execute("DELETE FROM zotero_codes WHERE expires_at <= ?", (ts,))


# --- sealing ------------------------------------------------------------------

def _fernet(token: str, purpose: str) -> Fernet:
    # ``derive`` drops the base64 padding Fernet wants back
    return Fernet(derive(token, purpose) + "=")


def _seal(token: str, purpose: str, value: str) -> str:
    return _fernet(token, purpose).encrypt(value.encode()).decode("ascii")


def _unseal(token: str, purpose: str, sealed: str) -> str:
    return _fernet(token, purpose).decrypt(sealed.encode("ascii")).decode()


# --- OAuth 1.0a ---------------------------------------------------------------

def _enc(value: str) -> str:
    """RFC 5849 §3.6: every byte but the unreserved characters percent-encoded."""
    return urllib.parse.quote(str(value), safe="")


def signature(method: str, url: str, params: dict, consumer_secret: str, token_secret: str = "") -> str:
    """HMAC-SHA1 over the signature base string (RFC 5849 §3.4). ``url`` is
    already a base string URI (lowercase, no default port, no query), so
    ``params`` are all the parameters."""
    pairs = sorted((_enc(k), _enc(v)) for k, v in params.items())
    base = "&".join((method.upper(), _enc(url), _enc("&".join(f"{k}={v}" for k, v in pairs))))
    key = f"{_enc(consumer_secret)}&{_enc(token_secret)}"
    return base64.b64encode(hmac.new(key.encode(), base.encode(), hashlib.sha1).digest()).decode()


def _signed_post(path: str, token_secret: str = "", **oauth: str) -> dict:
    """POST to one of Zotero's OAuth endpoints, signed with the app's secret
    (and the request token's, once there is one) in the Authorization header."""
    url = WEB_BASE + path
    params = {"oauth_consumer_key": config.ZOTERO_CLIENT_KEY, "oauth_nonce": secrets.token_hex(16),
              "oauth_signature_method": "HMAC-SHA1", "oauth_timestamp": str(int(time.time())),
              "oauth_version": "1.0", **oauth}
    params["oauth_signature"] = signature("POST", url, params, config.ZOTERO_CLIENT_SECRET, token_secret)
    return _post(url, "OAuth " + ", ".join(f'{_enc(k)}="{_enc(v)}"' for k, v in params.items()))


def _post(url: str, authorization: str) -> dict:
    """The one outbound call (tests replace it): an empty POST, and Zotero's
    form-encoded answer as a dict."""
    req = urllib.request.Request(url, data=b"", method="POST", headers={
        "Authorization": authorization, "User-Agent": "gamma-cloud",
        "Content-Type": "application/x-www-form-urlencoded"})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            return dict(urllib.parse.parse_qsl(resp.read(MAX_ANSWER).decode("utf-8", "replace")))
    except urllib.error.HTTPError as e:
        log.warning("%s answered %s", url, e.code)
        raise Problem(502, "Zotero did not accept the request. Start again from Gamma.") from e
    except (OSError, ValueError) as e:
        log.warning("%s failed: %s", url, e)
        raise Problem(502, "We could not reach Zotero. Try again in a moment.") from e
