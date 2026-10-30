"""OAuth 1.0a with zotero.org (RFC 5849, HMAC-SHA1): how a person connects
their Zotero account without pasting an API key (docs/dev/zotero_sync.md).

Zotero's OAuth hands out an ordinary API key that never expires: the
access step's ``oauth_token_secret`` *is* the key. The steps are
``request_token`` (naming the callback), the person's approval at
``authorize_url``, then ``access_token`` with the verifier Zotero sent to
the callback. The consumer is this server's own Zotero application
(``config.zotero_client``); without one, Gamma Cloud's broker runs the
same steps with its own (cloud/gammacloud/zotero.py; the two ask for the
same access in ``authorize_url``). Calls go to ``config.zotero_web_base()``
through ``zotero_web._request``, the tests' single patch point.
"""

import base64
import hashlib
import hmac
import secrets
import time
import urllib.parse

from . import config, zotero_web
from .zotero_web import ZoteroAuthError, ZoteroError

_DEFAULT_PORTS = {"http": 80, "https": 443}


def _enc(value) -> str:
    """RFC 5849 §3.6 percent-encoding: all but the unreserved characters
    (letters, digits, ``-._~``), as UTF-8."""
    return urllib.parse.quote(str(value), safe="")


def base_string(method: str, url: str, params: dict) -> str:
    """The signature base string (RFC 5849 §3.4.1): the method, the base
    URI (lower-case scheme and host, no default port, no query) and the
    normalized parameters — ``url``'s query and ``params`` (the oauth_*
    ones and any form fields) but ``oauth_signature``, encoded, then sorted
    by name and value."""
    parts = urllib.parse.urlsplit(url)
    scheme = parts.scheme.lower()
    netloc = (parts.hostname or "").lower()
    if parts.port is not None and parts.port != _DEFAULT_PORTS.get(scheme):
        netloc = f"{netloc}:{parts.port}"
    pairs = urllib.parse.parse_qsl(parts.query, keep_blank_values=True)
    pairs += [(name, value) for name, value in params.items() if name != "oauth_signature"]
    normalized = "&".join(f"{name}={value}" for name, value in sorted((_enc(n), _enc(v)) for n, v in pairs))
    return "&".join((method.upper(), _enc(f"{scheme}://{netloc}{parts.path or '/'}"), _enc(normalized)))


def signature(method: str, url: str, params: dict, consumer_secret: str, token_secret: str = "") -> str:
    """The HMAC-SHA1 signature (RFC 5849 §3.4.2) of a request: base64 of the
    HMAC of ``base_string`` keyed by both secrets, each encoded."""
    key = f"{_enc(consumer_secret)}&{_enc(token_secret)}".encode("ascii")
    digest = hmac.new(key, base_string(method, url, params).encode("ascii"), hashlib.sha1).digest()
    return base64.b64encode(digest).decode("ascii")


def authorization_header(method: str, url: str, oauth_params: dict, consumer_secret: str,
                         token_secret: str = "", extra: dict | None = None) -> str:
    """The ``Authorization: OAuth …`` value carrying ``oauth_params`` and
    their signature; ``extra`` are the request's other signed parameters
    (form fields), which travel in the body, not here."""
    sig = signature(method, url, {**(extra or {}), **oauth_params}, consumer_secret, token_secret)
    fields = {**oauth_params, "oauth_signature": sig}
    return "OAuth " + ", ".join(f'{_enc(name)}="{_enc(value)}"' for name, value in fields.items())


def _oauth(consumer_key: str, **fields) -> dict:
    """A request's oauth_* parameters: the consumer, a fresh nonce and
    timestamp, plus ``fields`` (named without the ``oauth_`` prefix)."""
    return {"oauth_consumer_key": consumer_key, "oauth_nonce": secrets.token_hex(16),
            "oauth_signature_method": "HMAC-SHA1", "oauth_timestamp": str(int(time.time())),
            "oauth_version": "1.0", **{f"oauth_{name}": value for name, value in fields.items()}}


def _post(path: str, oauth: dict, consumer_secret: str, token_secret: str, refused: str) -> dict:
    """POST a signed step to zotero.org; the fields of its form-encoded
    answer. ``refused`` is the sentence for a refusal."""
    url = config.zotero_web_base() + path
    headers = {"Authorization": authorization_header("POST", url, oauth, consumer_secret, token_secret),
               "Content-Type": "application/x-www-form-urlencoded", "User-Agent": zotero_web.user_agent()}
    resp = zotero_web._request("POST", url, headers, b"", zotero_web.TIMEOUT)
    if resp.status != 200:
        said = resp.body.decode("utf-8", "replace").strip()
        detail = f" (Zotero said: {said})" if said and len(said) <= 200 and not said.startswith("<") else ""
        error = ZoteroAuthError if resp.status in (401, 403) else ZoteroError
        raise error(f"{refused}{detail}: start again", resp.status)
    return dict(urllib.parse.parse_qsl(resp.body.decode("utf-8", "replace"), keep_blank_values=True))


def request_token(consumer_key: str, consumer_secret: str, callback: str) -> tuple[str, str]:
    """Step 1: a temporary ``(token, token_secret)`` for the approval that
    returns to ``callback``."""
    fields = _post("/oauth/request", _oauth(consumer_key, callback=callback), consumer_secret, "",
                   "Zotero refused to start connecting")
    if not fields.get("oauth_token") or not fields.get("oauth_token_secret"):
        raise ZoteroError("Zotero sent an unreadable answer when connecting: start again")
    return fields["oauth_token"], fields["oauth_token_secret"]


def authorize_url(token: str, *, name: str) -> str:
    """Step 2: where the person approves, with every box Gamma uses
    pre-ticked (library, notes and write access, write access to all
    groups); ``name`` labels the key in their zotero.org settings."""
    query = urllib.parse.urlencode({"oauth_token": token, "library_access": 1, "notes_access": 1,
                                    "write_access": 1, "all_groups": "write", "name": name})
    return f"{config.zotero_web_base()}/oauth/authorize?{query}"


def access_token(consumer_key: str, consumer_secret: str, token: str, token_secret: str,
                 verifier: str) -> dict:
    """Step 3: trade the approved token and its verifier for the API key;
    ``{"api_key", "user_id", "username"}``."""
    fields = _post("/oauth/access", _oauth(consumer_key, token=token, verifier=verifier), consumer_secret,
                   token_secret, "Zotero refused the connection")
    try:
        user_id = int(fields.get("userID", ""))
    except ValueError:
        user_id = 0
    if not fields.get("oauth_token_secret") or user_id <= 0:
        raise ZoteroError("Zotero sent an unreadable answer when connecting: start again")
    return {"api_key": fields["oauth_token_secret"], "user_id": user_id, "username": fields.get("username", "")}
