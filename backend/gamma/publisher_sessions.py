"""Private, encrypted publisher-cookie snapshots imported by the Connector.

PDF requests and AI paper fetches opt into an authenticated user's snapshots.
A connection authorizes one exact HTTPS host; cookies never authorize sibling hosts.
A snapshot also keeps the connecting browser's User-Agent: requests to that
host present it, so a cookie the site bound to that browser (a bot-check
clearance) is sent the way it was issued.
"""

import hashlib
import json
import os
import re
import tempfile
import time
from contextvars import ContextVar
from http.cookiejar import Cookie, CookieJar, DefaultCookiePolicy
from urllib.parse import urlsplit

from cryptography.fernet import Fernet, InvalidToken

from . import config
from .db import connect_users_db, page_now

# Explicit publisher boundaries also prevent importing university SSO, Gamma,
# public-suffix or arbitrary website sessions. Add publishers here as needed.
PUBLISHER_ROOTS = (
    "aps.org", "nature.com", "springer.com", "springernature.com",
    "wiley.com", "sciencedirect.com", "science.org", "acs.org",
    "iop.org", "ieee.org", "aip.org", "oup.com", "cambridge.org",
    "tandfonline.com", "pnas.org", "rsc.org", "optica.org",
)
current_user = ContextVar("publisher_session_user", default=None)
# Interactive PDF operations bind current_user in auth.py. AI fetch_paper
# binds it inside the tool worker, from the chat's authenticated scope.
PDF_PATHS = ("/api/pdf", "/api/resolve-pdf", "/api/clip")
MAX_AGE = 30 * 24 * 3600
SESSION_AGE = 24 * 3600


def publisher_root(host: str) -> str:
    return next((root for root in PUBLISHER_ROOTS
                 if host == root or host.endswith("." + root)), "")


def valid_host(host: str) -> str:
    if not isinstance(host, str) or len(host) > 253:
        raise ValueError("Unsupported publisher host")
    host = host.lower()
    if not re.fullmatch(r"[a-z0-9]+(?:[a-z0-9.-]*[a-z0-9])?", host) or not publisher_root(host):
        raise ValueError("Unsupported publisher host")
    return host


def cipher() -> Fernet:
    """The data directory's Fernet key (``GAMMA_PUBLISHER_SESSION_KEY`` or
    ``publisher-sessions.key`` next to users.db) — shared with the mirror
    tokens (gamma/sync_engine.py)."""
    configured = config.publisher_session_key()
    if configured:
        return Fernet(configured.encode("ascii"))
    path = config.DATA_DIR / "publisher-sessions.key"
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        # Publish a complete key atomically without replacing another process's
        # key. mkstemp creates a private file (0600 on POSIX).
        fd, temporary = tempfile.mkstemp(prefix=".publisher-key-", dir=path.parent)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(Fernet.generate_key())
            try:
                os.link(temporary, path)
            except FileExistsError:
                pass
        finally:
            os.unlink(temporary)
    return Fernet(path.read_bytes())


def normalize_cookies(host: str, cookies: list) -> list[dict]:
    if not isinstance(cookies, list) or not 1 <= len(cookies) <= 200:
        raise ValueError("Send between 1 and 200 publisher cookies")
    root = publisher_root(host)
    now = int(time.time())
    normalized = {}
    for item in cookies:
        if not isinstance(item, dict):
            raise ValueError("Invalid cookie")
        name, value = item.get("name"), item.get("value")
        domain, path = item.get("domain", ""), item.get("path", "/")
        host_only = item.get("hostOnly", False)
        if (not isinstance(name, str) or not re.fullmatch(r"[!#$%&'*+.^_`|~0-9A-Za-z-]{1,256}", name)
                or not isinstance(value, str) or len(value) > 8192
                or any(ord(c) < 32 or ord(c) > 126 or c == ";" for c in value)
                or not isinstance(path, str) or not path.startswith("/") or len(path) > 2048
                or any(ord(c) < 32 or ord(c) == 127 for c in path)
                or not isinstance(domain, str) or type(host_only) is not bool):
            raise ValueError("Invalid cookie fields")
        domain = domain.lstrip(".").lower()
        if (not domain or publisher_root(domain) != root
                or (host != domain and (host_only or not host.endswith("." + domain)))):
            raise ValueError("Cookie does not belong to this publisher host")
        if item.get("partitionKey"):
            raise ValueError("Partitioned cookies cannot be transferred")
        expiration = item.get("expirationDate")
        if expiration is None:
            expires = now + SESSION_AGE
        else:
            if type(expiration) not in (float, int) or not 0 <= expiration < 10**12:
                raise ValueError("Invalid cookie expiration")
            expires = min(int(expiration), now + MAX_AGE)
        if expires <= now:
            continue
        normalized[(name, domain, path)] = {
            "name": name, "value": value, "domain": domain, "path": path,
            "hostOnly": host_only, "expires": expires,
        }
    if not normalized:
        raise ValueError("No unexpired cookies to connect")
    return list(normalized.values())


def browser_agent(value) -> str:
    """The Connector's ``navigator.userAgent``, or "" when absent or not a
    plain header value (optional metadata: never a reason to refuse)."""
    if not isinstance(value, str) or not 0 < len(value) <= 512:
        return ""
    return value if all(32 <= ord(c) < 127 for c in value) else ""


def save(username: str, host: str, cookies: list, user_agent: str = "") -> dict:
    host = valid_host(host)
    cookies = normalize_cookies(host, cookies)
    payload = {"user": username, "host": host, "cookies": cookies}
    agent = browser_agent(user_agent)
    if agent:
        payload["agent"] = agent
    payload = json.dumps(payload).encode()
    encrypted = cipher().encrypt(payload).decode("ascii")
    updated = page_now()
    expires = max(c["expires"] for c in cookies)
    with connect_users_db() as conn:
        conn.execute(
            "INSERT INTO publisher_sessions (username, host, encrypted, expires_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?) ON CONFLICT(username, host) DO UPDATE SET "
            "encrypted=excluded.encrypted, expires_at=excluded.expires_at, updated_at=excluded.updated_at",
            (username, host, encrypted, expires, updated))
        conn.commit()
    return {"host": host, "expires_at": expires, "updated_at": updated}


def list_sessions(username: str) -> list[dict]:
    with connect_users_db() as conn:
        conn.execute("DELETE FROM publisher_sessions WHERE username=? AND expires_at<=?",
                     (username, time.time()))
        rows = conn.execute("SELECT host, expires_at, updated_at FROM publisher_sessions "
                            "WHERE username=? ORDER BY host", (username,)).fetchall()
        conn.commit()
    return [{"host": h, "expires_at": e, "updated_at": u} for h, e, u in rows]


def disconnect(username: str, host: str) -> None:
    with connect_users_db() as conn:
        conn.execute("DELETE FROM publisher_sessions WHERE username=? AND host=?", (username, host))
        conn.commit()


def rename_account(conn, old: str, new: str) -> None:
    """Carry an account's connections over to its new name (the account
    rename, ``routers/admin.rename_account_rows``). Every snapshot is sealed
    with the name it belongs to (``cookie_jar`` checks it), so each is
    re-sealed under the new one; a snapshot that no longer opens is dropped
    (it could never be used again). Caller commits."""
    rows = conn.execute("SELECT host, encrypted FROM publisher_sessions WHERE username=?",
                        (old,)).fetchall()
    if not rows:
        return
    box = cipher()
    for host, encrypted in rows:
        try:
            payload = json.loads(box.decrypt(encrypted.encode("ascii")))
        except (InvalidToken, ValueError):
            conn.execute("DELETE FROM publisher_sessions WHERE username=? AND host=?", (old, host))
            continue
        payload["user"] = new
        sealed = box.encrypt(json.dumps(payload).encode()).decode("ascii")
        conn.execute("UPDATE publisher_sessions SET username=?, encrypted=? WHERE username=? AND host=?",
                     (new, sealed, old, host))


class _PublisherPolicy(DefaultCookiePolicy):
    def __init__(self):
        super().__init__(strict_ns_domain=DefaultCookiePolicy.DomainStrictNonDomain)

    def return_ok(self, cookie, request):
        host = cookie.get_nonstandard_attr("gamma_host")
        target = urlsplit(request.full_url)
        if host and (target.scheme != "https" or target.hostname != host):
            return False
        return super().return_ok(cookie, request)


def _snapshots() -> list[dict]:
    """The current user's unexpired, decryptable snapshots (their payloads)."""
    username = current_user.get()
    if not username:
        return []
    with connect_users_db() as conn:
        rows = conn.execute("SELECT host, encrypted FROM publisher_sessions "
                            "WHERE username=? AND expires_at>?", (username, time.time())).fetchall()
    if not rows:
        return []
    try:
        box = cipher()
    except (OSError, ValueError):
        return []  # Missing/replaced key: user can reconnect; no plaintext fallback.
    out = []
    for host, encrypted in rows:
        try:
            payload = json.loads(box.decrypt(encrypted.encode("ascii")))
        except (InvalidToken, ValueError):
            continue
        if isinstance(payload, dict) and payload.get("user") == username and payload.get("host") == host:
            out.append(payload)
    return out


def _jar(snapshots: list[dict]) -> CookieJar:
    jar = CookieJar(policy=_PublisherPolicy())
    for payload in snapshots:
        host = payload["host"]
        try:
            # Narrow parent-domain cookies to the connected host. If narrowing
            # collapses duplicate names/paths, prefer the most specific domain.
            for c in sorted(payload["cookies"], key=lambda item: len(item["domain"])):
                if c["expires"] <= time.time():
                    continue
                jar.set_cookie(Cookie(
                    0, c["name"], c["value"], None, False,
                    host, False, False, c["path"], True,
                    True, c["expires"], False, None, None, {"gamma_host": host}, False))
        except (ValueError, KeyError, TypeError):
            continue
    return jar


def cookie_jar() -> CookieJar:
    return _jar(_snapshots())


def browser_profile() -> tuple[CookieJar, dict]:
    """``(cookie jar, {host: User-Agent})`` of the current user's connected
    sessions, from one read — what an outbound fetch presents per host."""
    snapshots = _snapshots()
    return _jar(snapshots), {p["host"]: p["agent"] for p in snapshots if browser_agent(p.get("agent"))}


def cache_scope() -> tuple[str | None, str]:
    """Partition fetched text by account and usable publisher cookies.

    Connecting, refreshing, disconnecting or expiring cookies changes the
    fingerprint, so an earlier abstract/blocked page cannot mask new access.
    Only the digest is kept in cache keys, never cookie values.
    """
    fingerprint = hashlib.sha256()
    for cookie in sorted(cookie_jar(), key=lambda c: (c.domain, c.path, c.name)):
        fingerprint.update(json.dumps([cookie.domain, cookie.path, cookie.name,
                                       cookie.value, cookie.expires]).encode())
    return current_user.get(), fingerprint.hexdigest()
