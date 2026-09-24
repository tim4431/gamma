"""Publishing a page to the free share host, so its share link works while
the machine that holds the library is off (docs/dev/mirror.md
"Publishing", docs/dev/cloud_accounts.md "The share host").

Both halves live in every Gamma; which one runs depends on the server:

- **The share host** (cloud sign-in on, the ``cloud_share_host`` switch on)
  answers ``exchange``: a person's Gamma Cloud access token, checked at the
  account server's ``/userinfo``, becomes a write-scope integration token on
  their default personal workspace there. The account is resolved (and, under
  the ``provision`` policy, created) exactly as a first sign-in would.
- **The publishing server** (a desktop sidecar, usually) keeps one filtered
  mirror of that workspace (``sync_engine``'s ``page_filter``): publishing a
  page adds it to the filter, runs a round and makes the share there;
  unpublishing stops the share, deletes the copy there and drops the page
  from the filter. Edits made through an edit share come back by the
  mirror's two-way sync.

A workspace that already mirrors another server (a lab NAS) cannot publish:
one remote per copy, and the page's home is that other server.
"""

import json
import re
from urllib.parse import urlsplit

from . import cloud_auth, integrations, ratelimit, sync_engine, workspaces
from .auth import SHARE_AUDIENCES, SHARE_ROLES
from .cloud_auth import CloudAuthError
from .db import connect_pages_db, connect_users_db
from .logbuf import log
from .sync_engine import Remote, RemoteError

TOKEN_DAYS = 365
MIRROR_NAME = "Gamma Cloud"
SIGN_IN = "Sign in with Gamma Cloud to publish."
EXCHANGE_PATH = "/api/auth/cloud/exchange"


class PublishError(Exception):
    """A refusal with the HTTP status the router answers."""

    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


def this_is_share_host() -> bool:
    """Whether this server accepts published pages."""
    return cloud_auth.settings()["share_host"]


def publishing_blocked() -> bool:
    """A share host publishes nothing: its pages are shared from it directly."""
    return this_is_share_host()


# --- the share host ---------------------------------------------------------------

def token_name(caller: str) -> str:
    caller = re.sub(r"[\x00-\x1f\x7f]", "", caller or "").strip()[:80] or "a Gamma server"
    return f"Published pages from {caller}"


def exchange(access_token: str, caller: str, base_url: str) -> dict:
    """``{token, workspace_id, username, url}`` for the person the Gamma
    Cloud ``access_token`` belongs to: a fresh write-scope integration token
    (``TOKEN_DAYS``) on their default personal workspace here, replacing the
    live one of the same name (one per calling server), and this server's
    address. The account is resolved under the server's sign-in policy, and
    the invitations waiting for the subject are claimed, as on a sign-in."""
    if not this_is_share_host():
        raise PublishError(403, "This server does not accept published pages.")
    try:
        claims = cloud_auth.userinfo(access_token)
    except CloudAuthError as e:
        if e.status in (400, 401, 403):
            raise PublishError(401, "Gamma Cloud did not accept the token.") from e
        raise PublishError(503, f"Cannot check the token with Gamma Cloud: {e}") from e
    ratelimit.check(f"cloud-exchange:sub:{claims['sub']}", 10, 600)
    if not claims.get("email_verified"):
        raise PublishError(403, "Confirm your e-mail address on your Gamma Cloud account first.")
    if not claims.get("preferred_username"):
        raise PublishError(403, "Gamma Cloud names no username for this account.")
    # the account server's claims only: the internal "_link_user" / "_refresh_token" keys never come from it
    claims = {k: v for k, v in claims.items() if not str(k).startswith("_")}
    try:
        username = cloud_auth.resolve_account(claims)
    except CloudAuthError as e:
        raise PublishError(403, str(e)) from e
    ws = workspaces.ensure_personal(username)
    name = token_name(caller)
    with connect_users_db() as conn:
        conn.execute("DELETE FROM integration_tokens WHERE username = ? AND name = ? AND scope = 'write'",
                     (username, name))
        conn.commit()
    item = integrations.create_token(username, ws, name, TOKEN_DAYS, scope="write")
    log.info(f"share host: {username} got a publishing token for {name[len('Published pages from '):]}")
    return {"token": item["token"], "workspace_id": ws, "username": username, "url": base_url}


# --- the publishing server ---------------------------------------------------------

def _same(a: str, b: str) -> bool:
    return (a or "").rstrip("/").lower() == (b or "").rstrip("/").lower()


def _host_of(url: str) -> str:
    return urlsplit(url).netloc or url


def share_host() -> str:
    """The share host's address from the account server, or PublishError."""
    try:
        url = cloud_auth.share_host_url()
    except CloudAuthError as e:
        raise PublishError(503, f"Cannot reach Gamma Cloud: {e}") from e
    if not url:
        raise PublishError(409, "Gamma Cloud has no share host to publish to.")
    return url


def _require_page(ws: str, page_id: str) -> None:
    with connect_pages_db(ws) as conn:
        row = conn.execute("SELECT parent_id FROM unified_blocks WHERE id = ?", (page_id,)).fetchone()
    if not row:
        raise PublishError(404, "page not found")
    if row[0] != "root":
        raise PublishError(400, "only pages can be published")


def _check_mirror(mirror: dict, host: str, user: str) -> None:
    """The workspace's mirror must be its publication to ``host``, owned by
    ``user``, two-way."""
    if not _same(mirror["remote_url"], host):
        raise PublishError(409, f"This workspace is a copy of {mirror['remote_name'] or 'a workspace'} on "
                                f"{_host_of(mirror['remote_url'])}; publish from there.")
    if mirror["owner"] != user:
        raise PublishError(409, f"This workspace publishes through {mirror['owner']}'s Gamma Cloud account.")
    if mirror["mode"] == "off":
        raise PublishError(409, "Publishing is detached for this workspace; reattach it first.")
    if mirror["mode"] != "two-way":
        raise PublishError(409, "This workspace only receives from the share host; set it to two-way first.")


def _publish_mirrors(user: str, host: str) -> list[dict]:
    """The caller's filtered two-way mirrors of the share host (one per
    local workspace that publishes), with their tokens."""
    return [sync_engine.get_mirror(m["workspace_id"], with_token=True) for m in sync_engine.list_mirrors(user)
            if m["page_filter"] is not None and _same(m["remote_url"], host) and m["mode"] == "two-way"]


def _exchange(user: str, host: str, server_name: str) -> str:
    """A write token on the person's share-host workspace, for the cloud
    identity linked to ``user``. The share host replaces the token it made
    for this server before, so every publishing mirror of the account gets
    the new one."""
    subject, _ = cloud_auth.grant_of(user)
    access = cloud_auth.access_token_for(user)
    if not access:
        raise PublishError(409, SIGN_IN)
    try:
        out = Remote(host, "", access).post(EXCHANGE_PATH, {"server": server_name})
    except RemoteError as e:
        if e.status == 401:
            cloud_auth.forget_access(subject)
        raise PublishError(429 if e.status == 429 else 409 if e.status == 403 else 502,
                           f"The share host refused: {e.detail or e}") from e
    token = (out or {}).get("token") or ""
    if not token.startswith("gamma_"):
        raise PublishError(502, "The share host answered no token.")
    for other in _publish_mirrors(user, host):
        sync_engine.replace_token(other["workspace_id"], token)
    return token


def _token_ok(host: str, token: str) -> bool:
    try:
        sync_engine.whoami(Remote(host, "", token))
        return True
    except RemoteError as e:
        if e.status in (401, 403):
            return False
        raise PublishError(502, f"Cannot reach the share host: {e}") from e


def _mirror_for(user: str, ws: str, page_id: str, host: str, server_name: str) -> dict:
    """The workspace's publishing mirror with ``page_id`` in its filter,
    made on the first publication (``adopt: mine``: the page here wins over
    anything the share host holds without a base)."""
    mirror = sync_engine.get_mirror(ws, with_token=True)
    if mirror:
        _check_mirror(mirror, host, user)
        if not _token_ok(host, mirror["token"]):
            # an expired or revoked token: a new one for this mirror and the account's other publishing ones
            sync_engine.replace_token(ws, _exchange(user, host, server_name))
        with sync_engine.round_lock(ws):
            # a full mirror of the share host moves every page already
            return sync_engine.filter_add(ws, page_id, adopt="mine" if mirror["page_filter"] is not None else "")
    others = _publish_mirrors(user, host)
    token = others[0]["token"] if others and _token_ok(host, others[0]["token"]) else ""
    token = token or _exchange(user, host, server_name)
    try:
        return sync_engine.create_mirror(user, host, token, name=MIRROR_NAME, mode="two-way", workspace_id=ws,
                                         adopt="mine", page_filter=[page_id])
    except ValueError as e:
        raise PublishError(409, str(e)) from e
    except RemoteError as e:
        raise PublishError(502, f"Cannot reach the share host: {e}") from e


def _remote(mirror: dict) -> Remote:
    return Remote(mirror["remote_url"], mirror["remote_ws"], mirror["token"])


def _json(remote: Remote, method: str, path: str, payload: dict | None = None) -> dict:
    body = json.dumps(payload).encode("utf-8") if payload is not None else None
    _, data = remote.request(method, path, body=body, content_type="application/json" if body else None)
    return json.loads(data) if data else {}


def _synced(ws: str, page_id: str) -> bool:
    with connect_pages_db(ws) as conn:
        return bool(conn.execute("SELECT 1 FROM sync_pages WHERE page_id = ?", (page_id,)).fetchone())


def _mirror_view(mirror: dict) -> dict:
    ws = mirror["workspace_id"]
    return {"ws": ws, "status": mirror["status"], "page_filter": mirror["page_filter"],
            "conflicts_open": sync_engine.open_conflicts(ws),
            "pending_local": mirror["mode"] == "two-way" and sync_engine.has_local_changes(ws)}


def publish(user: str, ws: str, page_id: str, *, audience: str | None = None, role: str | None = None,
            server_name: str = "") -> dict:
    """Publish ``page_id``: into the filtered mirror (made when missing),
    one round now, then the share on the share host (default anyone / view;
    ``audience`` / ``role`` set it, on a new link or an existing one).
    ``{url, share, mirror: {ws, status, ...}}``."""
    if audience is not None and audience not in SHARE_AUDIENCES:
        raise PublishError(400, "audience must be anyone, users or list")
    if role is not None and role not in SHARE_ROLES:
        raise PublishError(400, "role must be view or edit")
    if publishing_blocked():
        raise PublishError(409, "This server is a share host: share its pages directly.")
    _require_page(ws, page_id)
    if not cloud_auth.settings()["enabled"] or not cloud_auth.grant_of(user)[1]:
        raise PublishError(409, SIGN_IN)
    host = share_host()
    _mirror_for(user, ws, page_id, host, server_name)
    status = sync_engine.sync_workspace(ws)
    if not _synced(ws, page_id):
        raise PublishError(502, f"The page did not reach the share host: {status.get('last_error') or 'try again'}")
    mirror = sync_engine.get_mirror(ws, with_token=True)
    remote = _remote(mirror)
    wanted = {k: v for k, v in (("audience", audience), ("role", role)) if v is not None}
    try:
        share = _json(remote, "POST", f"/api/share/{page_id}", wanted or None)
        if any(share.get(k) != v for k, v in wanted.items()):
            share = _json(remote, "PUT", f"/api/share-settings/{page_id}", wanted)
    except RemoteError as e:
        raise PublishError(502, f"The share host refused the share: {e.detail or e}") from e
    return {"url": f"{mirror['remote_url']}/?share={share['token']}", "share": share, "mirror": _mirror_view(mirror)}


def unpublish(user: str, ws: str, page_id: str) -> dict:
    """Stop the share on the share host, delete the copy there and drop the
    page from the filter, under the round lock so no round sees the copy's
    deletion as the page's. The page here is untouched. Nothing changes
    here when the share host cannot be reached (502)."""
    mirror = sync_engine.get_mirror(ws, with_token=True)
    if not mirror or (mirror["page_filter"] is not None and page_id not in mirror["page_filter"]):
        raise PublishError(409, "This page is not published.")
    if mirror["owner"] != user:
        raise PublishError(409, f"This workspace publishes through {mirror['owner']}'s Gamma Cloud account.")
    remote = _remote(mirror)
    with sync_engine.round_lock(ws):
        try:
            remote.request("DELETE", f"/api/share-settings/{page_id}")
            if mirror["page_filter"] is not None:
                # a full mirror of the share host keeps the page on both sides: only the share stops
                remote.delete(f"/api/blocks/{page_id}")
        except RemoteError as e:
            raise PublishError(502, f"Cannot reach the share host: {e}") from e
        mirror = sync_engine.filter_remove(ws, [page_id]) or mirror
    return {"published": False, "mirror": _mirror_view(mirror)}


def state(user: str, ws: str, page_id: str) -> dict:
    """``{published, can_publish, reason?, url?, share?, status?, mirror?,
    error?}``: whether the page is published (in the filter of the
    workspace's mirror of the share host), its live share there, and the
    mirror's raw status. ``can_publish`` / ``reason`` say whether the
    Publish action would be refused before it is tried."""
    _require_page(ws, page_id)
    out: dict = {"published": False, "can_publish": True}
    reason, host = "", ""
    mirror = sync_engine.get_mirror(ws, with_token=True)
    if publishing_blocked():
        reason = "This server is a share host: share its pages directly."
    elif not cloud_auth.settings()["enabled"] or not cloud_auth.grant_of(user)[1]:
        reason = SIGN_IN
    else:
        try:
            host = share_host()
            if mirror:
                _check_mirror(mirror, host, user)
        except PublishError as e:
            reason = e.message
    if reason:
        out.update(can_publish=False, reason=reason)
    # only publishing makes a filtered mirror; a full one counts when it follows the share host
    if not mirror or (mirror["page_filter"] is None and not (host and _same(mirror["remote_url"], host))):
        return out
    published = mirror["page_filter"] is None or page_id in mirror["page_filter"]
    out.update(status=mirror["status"], mirror=_mirror_view(mirror))
    if not published:
        return out
    try:
        share = _json(_remote(mirror), "GET", f"/api/share-settings/{page_id}")
    except RemoteError as e:
        if e.status != 404:
            out.update(published=mirror["page_filter"] is not None, error=f"Cannot reach the share host: {e}")
            return out
        share = {}
    if mirror["page_filter"] is not None:
        out["published"] = True
    if share.get("token"):
        out.update(published=True, share=share, url=f"{mirror['remote_url']}/?share={share['token']}")
    return out
