"""The Gamma servers a person linked their identity on: each registers its
confirmed public URL under the account when the link is made
(``POST /api/me/servers``) and removes it on unlink, so the portal and the
desktop launcher list the lab server someone was invited to, not only
what the account server provisioned. The account's hosted server
(``hosted_servers``, docs/dev/hosted.md) joins the same list in
``of_account`` while it is not deleted, as ``kind`` ``hosted``: it comes
from that table, so an unlink or a Remove never takes it off.

A URL is normalized with the rules Gamma's own public-URL setting uses
(``backend/gamma/server_settings.py`` ``validate_public_url``, copied):
``https://host[:port]``, or plain HTTP for a loopback host only — the
desktop sidecar, which the list shows as *this computer*.

A confidential client (share host, container, server) may only register
an address on the origin of one of its redirect URIs; the public desktop
client may register any address, since every sidecar is that client.

A server also reports its build (``version``, the label it shows in its
own admin dashboard) and its data directory's schema version (``schema``),
so the Devices page shows which installs are behind. A call that sends
neither (an older server) leaves what the row has.

Each row remembers the grant of the token that registered it
(``grant_id``), so the portal shows a server and its sign-in as one row
(``merge``); signing that grant out on the portal takes the row off.
"""

import re
from ipaddress import IPv6Address
from urllib.parse import urlsplit

from . import config
from .accounts import Problem
from .db import json_dict, now
from .hosted import url_of
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


def norm_version(raw) -> str | None:
    """The build label a server reports, cut like a name; None when absent."""
    return norm_name(raw, "") or None


def link(conn, account_id: str, url: str, name: str, grant_id: str = "", version: str | None = None,
         schema: int | None = None) -> dict:
    """Add or refresh a server; its row. ``version`` and ``schema`` (None =
    not reported) replace what the row had only when given."""
    ts = now()
    exists = conn.execute("SELECT 1 FROM servers_linked WHERE account_id = ? AND url = ?", (account_id, url)).fetchone()
    if not exists and conn.execute("SELECT COUNT(*) FROM servers_linked WHERE account_id = ?",
                                   (account_id,)).fetchone()[0] >= MAX_SERVERS:
        raise Problem(400, f"An account lists at most {MAX_SERVERS} servers.")
    conn.execute("INSERT INTO servers_linked (account_id, url, name, linked_at, last_seen_at, grant_id, version, schema) "
                 "VALUES (?, ?, ?, ?, ?, ?, COALESCE(?, ''), ?) ON CONFLICT (account_id, url) DO UPDATE SET "
                 "name = excluded.name, last_seen_at = excluded.last_seen_at, grant_id = excluded.grant_id, "
                 "version = COALESCE(?, version), schema = COALESCE(?, schema)",
                 (account_id, url, name, ts, ts, grant_id, version, schema, version, schema))
    return _public(conn.execute("SELECT * FROM servers_linked WHERE account_id = ? AND url = ?",
                                (account_id, url)).fetchone())


def unlink(conn, account_id: str, url: str) -> bool:
    return bool(conn.execute("DELETE FROM servers_linked WHERE account_id = ? AND url = ?", (account_id, url)).rowcount)


HOSTED_NAME = "Your hosted Gamma"


def _public(row) -> dict:
    return {"url": row["url"], "name": row["name"], "kind": "linked", "hosted": False, "local": is_local(row["url"]),
            "linked_at": row["linked_at"], "last_seen_at": row["last_seen_at"], "version": row["version"],
            "schema": row["schema"]}


def _hosted(conn, account_id: str) -> dict | None:
    """The account's hosted server as a list row, or None (none, deleted,
    or hosting off). ``version``/``schema`` and ``last_seen_at`` come from
    the container's last sync."""
    if not config.HOSTED_DOMAIN:
        return None
    row = conn.execute("SELECT * FROM hosted_servers WHERE account_id = ? AND state != 'deleted'",
                       (account_id,)).fetchone()
    if row is None:
        return None
    report = json_dict(row["report"])
    schema = report.get("schema")
    return {"url": url_of(row["label"]), "name": HOSTED_NAME, "kind": "hosted",
            "hosted": True, "local": False, "linked_at": row["created_at"],
            "last_seen_at": row["synced_at"] or row["reported_at"] or row["created_at"],
            "version": str(report.get("version") or ""), "schema": schema if isinstance(schema, int) else None,
            "state": row["state"]}


def of_account(conn, account_id: str, *, grants: bool = False) -> list[dict]:
    """Every server of an account: its hosted one first, then the linked
    ones, the latest seen first. The hosted server, once its owner signed
    in there, has a linked row of its own at the same address: the two are
    one entry (the hosted one, with that row's grant). ``grants`` adds each
    row's ``grant_id`` (the portal's, never the API's)."""
    rows = conn.execute("SELECT * FROM servers_linked WHERE account_id = ? ORDER BY last_seen_at DESC",
                        (account_id,)).fetchall()
    hosted = _hosted(conn, account_id)
    out = []
    if hosted is not None:
        twin = next((r for r in rows if r["url"] == hosted["url"]), None)
        if twin is not None:
            rows = [r for r in rows if r is not twin]
            hosted["version"] = hosted["version"] or twin["version"]
            hosted["schema"] = hosted["schema"] if hosted["schema"] is not None else twin["schema"]
            hosted["last_seen_at"] = max(hosted["last_seen_at"], twin["last_seen_at"])
        out.append({**hosted, "grant_id": twin["grant_id"] if twin is not None else ""} if grants else hosted)
    return out + [{**_public(r), "grant_id": r["grant_id"]} if grants else _public(r) for r in rows]


def merge(devices: list[dict], linked: list[dict]) -> list[dict]:
    """The portal's one list of Gamma servers: each linked server with the
    live grant it registered with (``grant``, None once signed out), then
    every live grant no listed server names (a server without a public
    address, one that has not checked in since the upgrade) with ``server``
    None. The most recently active first."""
    by_grant = {d["id"]: d for d in devices}
    out = [{"server": s, "grant": by_grant.pop(s.get("grant_id") or "", None)} for s in linked]
    out += [{"server": None, "grant": g} for g in by_grant.values()]

    def active(e):
        return max((e["server"] or {}).get("last_seen_at", ""), (e["grant"] or {}).get("last_used_at", ""))
    return sorted(out, key=active, reverse=True)


def drop_grant(conn, account_id: str, grant_id: str) -> None:
    """A grant was signed out on the portal: its server leaves the list."""
    if grant_id:
        conn.execute("DELETE FROM servers_linked WHERE account_id = ? AND grant_id = ?", (account_id, grant_id))
