"""The Gamma servers a person linked their identity on: each registers its
confirmed public URL under the account when the link is made
(``POST /api/me/servers``) and removes it on unlink, so the portal and the
desktop launcher list the lab server someone was invited to, not only
what the account server provisioned. Provisioned servers (v1) join the
same list in ``of_account``; there are none yet.

A URL is normalized with the rules Gamma's own public-URL setting uses
(``backend/gamma/server_settings.py`` ``validate_public_url``, copied):
``https://host[:port]``, or plain HTTP for a loopback host only — the
desktop sidecar, which the list shows as *this computer*.

A confidential client (share host, container) may only register an
address on the origin of one of its redirect URIs; the public desktop
client may register any address, since every sidecar is that client.
"""

import re
from ipaddress import IPv6Address
from urllib.parse import urlsplit

from .accounts import Problem
from .db import now
from .oidc import LOOPBACK_HOSTS

MAX_NAME = 80
MAX_SERVERS = 50


def norm_url(value) -> str:
    """``scheme://host[:port]`` — lowercase, IDNA host, default port
    dropped, a trailing slash allowed; anything else refused."""
    value = (value or "").strip() if isinstance(value, str) else ""
    if value.endswith("/"):
        value = value[:-1]
    try:
        url = urlsplit(value)
        host = url.hostname or ""
        port = url.port
        if (not value or len(value) > 2048 or re.search(r"[\s\\\x00-\x1f\x7f]", value)
                or url.scheme not in {"http", "https"} or not host
                or (url.scheme != "https" and host not in LOOPBACK_HOSTS)
                or url.username is not None or url.password is not None
                or url.path not in ("", "/") or "?" in value or "#" in value
                or (port is not None and port < 1)):
            raise ValueError
        if ":" in host:
            host = str(IPv6Address(host))
            if "%" in host:
                raise ValueError
        else:
            host = host.encode("idna").decode("ascii")
            if len(host) > 253 or not all(re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label)
                                          for label in host.split(".")):
                raise ValueError
        authority = f"[{host}]" if ":" in host else host
        if port is not None and port != (443 if url.scheme == "https" else 80):
            authority += f":{port}"
        return f"{url.scheme}://{authority}"
    except (ValueError, UnicodeError):
        raise Problem(400, "A server address is https://host[:port] without a path, query or fragment; "
                           "plain http only for this computer (localhost).") from None


def is_local(url: str) -> bool:
    return urlsplit(url).hostname in LOOPBACK_HOSTS


def norm_name(raw, url: str) -> str:
    name = " ".join("".join(c for c in str(raw or "") if c.isprintable()).split())[:MAX_NAME]
    return name or urlsplit(url).netloc


def allowed_for(client: dict, url: str) -> bool:
    """Whether a token's client may register ``url``: a confidential client
    only on the origin of one of its redirect URIs."""
    if client["kind"] == "desktop":
        return True
    origins = set()
    for uri in client["redirect_uris"]:
        try:
            origins.add(norm_url(f"{urlsplit(uri).scheme}://{urlsplit(uri).netloc}"))
        except Problem:
            continue
    return url in origins


def link(conn, account_id: str, url: str, name: str) -> dict:
    """Add or refresh a server; its row."""
    ts = now()
    exists = conn.execute("SELECT 1 FROM servers_linked WHERE account_id = ? AND url = ?", (account_id, url)).fetchone()
    if not exists and conn.execute("SELECT COUNT(*) FROM servers_linked WHERE account_id = ?",
                                   (account_id,)).fetchone()[0] >= MAX_SERVERS:
        raise Problem(400, f"An account lists at most {MAX_SERVERS} servers.")
    conn.execute("INSERT INTO servers_linked (account_id, url, name, linked_at, last_seen_at) VALUES (?, ?, ?, ?, ?) "
                 "ON CONFLICT (account_id, url) DO UPDATE SET name = excluded.name, last_seen_at = excluded.last_seen_at",
                 (account_id, url, name, ts, ts))
    return _public(conn.execute("SELECT * FROM servers_linked WHERE account_id = ? AND url = ?",
                                (account_id, url)).fetchone())


def unlink(conn, account_id: str, url: str) -> bool:
    return bool(conn.execute("DELETE FROM servers_linked WHERE account_id = ? AND url = ?", (account_id, url)).rowcount)


def _public(row) -> dict:
    return {"url": row["url"], "name": row["name"], "kind": "linked", "local": is_local(row["url"]),
            "linked_at": row["linked_at"], "last_seen_at": row["last_seen_at"]}


def of_account(conn, account_id: str) -> list[dict]:
    """Every server of an account: provisioned ones (none until v1), then
    the linked ones, the latest seen first."""
    rows = conn.execute("SELECT * FROM servers_linked WHERE account_id = ? ORDER BY last_seen_at DESC",
                        (account_id,)).fetchall()
    return [_public(r) for r in rows]
