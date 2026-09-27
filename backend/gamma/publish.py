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

The share host also caps how many pages a plan may publish (``page_cap``,
``config.PLAN_PAGE_LIMITS``) and, with ``GAMMA_PAGE_HOST`` set, gives every
published page a pretty address on a hostname per account
(``https://<username>-pages.gammapdf.com/<slug>-<page id>``, resolved by
``resolve_public``; the slug is decoration, the trailing id routes).
"""

import json
import re
import unicodedata
from urllib.parse import unquote, urlsplit

from . import cloud_auth, config, integrations, ratelimit, sync_engine, workspaces
from .auth import SHARE_AUDIENCES, SHARE_ROLES
from .cloud_auth import CloudAuthError
from .db import connect_pages_db, connect_users_db
from .logbuf import log
from .sync_engine import Remote, RemoteError

TOKEN_DAYS = 365
MIRROR_NAME = "Gamma Cloud"
SIGN_IN = "Sign in with Gamma Cloud to publish."
IS_SHARE_HOST = "This server is a share host: share its pages directly."
EXCHANGE_PATH = "/api/auth/cloud/exchange"


class PublishError(Exception):
    """A refusal with the HTTP status the router answers (``extra``: more
    fields for the answer's body, next to ``detail``)."""

    def __init__(self, status: int, message: str, extra: dict | None = None):
        super().__init__(message)
        self.status = status
        self.message = message
        self.extra = extra or {}


def this_is_share_host() -> bool:
    """Whether this server accepts published pages."""
    return cloud_auth.settings()["share_host"]


def publishing_blocked() -> bool:
    """A share host publishes nothing: its pages are shared from it directly."""
    return this_is_share_host()


# --- slugs and page hosts ---------------------------------------------------------

SLUG_MAX = 60
PLACEHOLDER = "{username}"
_LABEL_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")


def slug(title: str) -> str:
    """The decorative part of a page's public path: the title ASCII-folded
    (NFKD, marks dropped), lowercased, every run of anything but ``[a-z0-9]``
    one ``-``, trimmed, at most ``SLUG_MAX`` characters. "" when nothing is
    left (a CJK title). Mirrored in frontend/src/shared/lib/slug.js;
    tests/shared/slug.json pins both."""
    text = unicodedata.normalize("NFKD", title or "")
    text = "".join(c for c in text if not unicodedata.category(c).startswith("M")).lower()
    text = re.sub(r"[^a-z0-9]+", "-", text).strip("-")
    return text[:SLUG_MAX].rstrip("-")


def _valid_host(host: str) -> bool:
    labels = host.split(".")
    return len(host) <= 253 and len(labels) >= 2 and all(_LABEL_RE.match(label) for label in labels)


def check_config() -> None:
    """Startup: ``GAMMA_PAGE_HOST`` names ``{username}`` exactly once and is
    a hostname otherwise; ``GAMMA_FREE_PAGE_LIMIT`` is a whole number.
    ValueError with the reason."""
    try:
        config.plan_page_limits()
    except ValueError:
        raise ValueError("GAMMA_FREE_PAGE_LIMIT must be a whole number (0 lifts the cap).") from None
    pattern = config.page_host_pattern()
    if not pattern:
        return
    if pattern.count(PLACEHOLDER) != 1:
        raise ValueError("GAMMA_PAGE_HOST must contain {username} exactly once, e.g. {username}-pages.example.org.")
    if not _valid_host(pattern.replace(PLACEHOLDER, "x")):
        raise ValueError("GAMMA_PAGE_HOST must be a hostname with {username} in it (no scheme, port or path), "
                         "e.g. {username}-pages.example.org.")


def page_host_user(pattern: str, host: str) -> str:
    """The username a page hostname names under ``pattern`` ("" when
    ``host`` — a Host header, port allowed — does not match it)."""
    if not pattern or PLACEHOLDER not in pattern:
        return ""
    head, tail = pattern.split(PLACEHOLDER, 1)
    host = re.sub(r":\d+$", "", (host or "").strip().lower())
    m = re.fullmatch(re.escape(head) + r"([a-z0-9](?:[a-z0-9-]*[a-z0-9])?)" + re.escape(tail), host)
    return m.group(1) if m and _valid_host(host) else ""


def public_url(base_url: str, pattern: str, username: str, title: str, page_id: str) -> str:
    """A published page's pretty address for the share host at ``base_url``
    (its scheme and port): ``<scheme>://<pattern with username>/<slug>-<id>``,
    just ``/<id>`` without a slug. "" when there is no pattern or the
    username cannot be a hostname label."""
    host = pattern.replace(PLACEHOLDER, (username or "").lower()) if pattern and username else ""
    if not host or not _valid_host(host):
        return ""
    parts = urlsplit(base_url)
    port = f":{parts.port}" if parts.port else ""
    name = slug(title)
    return f"{parts.scheme or 'https'}://{host}{port}/{name + '-' if name else ''}{page_id}"


def resolve_public(host: str, path: str) -> dict:
    """``{share, page_id}`` for a page host's path: ``host`` names the
    account (``page_host_user``), ``path`` is ``/<slug>-<id>`` or ``/<id>``,
    and only the trailing id counts — a root page of that account's default
    personal workspace that has a share. PublishError(404) otherwise. A page
    id may hold a ``-`` itself, so every tail of the path after a ``-`` is a
    candidate, the longest shared page winning."""
    missing = PublishError(404, "page not found")
    username = page_host_user(config.page_host_pattern(), host)
    segment = unquote(path or "").strip("/")
    if not username or not segment or "/" in segment or len(segment) > 200:
        raise missing
    with connect_users_db() as conn:
        rows = conn.execute("SELECT default_workspace FROM users WHERE LOWER(username) = ? AND is_guest = 0",
                            (username,)).fetchall()
    ws = rows[0][0] if len(rows) == 1 else ""
    if not ws or (workspaces.get(ws) or {}).get("kind") != "personal":
        raise missing
    candidates = [segment] + [segment[i + 1:] for i, c in enumerate(segment) if c == "-" and segment[i + 1:]]
    marks = ",".join("?" * len(candidates))
    with connect_users_db() as conn:
        shares = dict(conn.execute(f"SELECT page_id, token FROM shares WHERE workspace_id = ? AND page_id IN ({marks})",
                                   (ws, *candidates)).fetchall())
    if not shares:
        raise missing
    with connect_pages_db(ws) as conn:
        roots = {r[0] for r in conn.execute(
            f"SELECT id FROM unified_blocks WHERE parent_id = 'root' AND id IN ({marks})", candidates)}
    for page_id in candidates:
        if page_id in roots and page_id in shares:
            return {"share": shares[page_id], "page_id": page_id}
    raise missing


# --- the plan's page cap (the share host) -----------------------------------------

def cap_message(plan: str, limit: int) -> str:
    return (f"{(plan or 'Your').capitalize()} plan: up to {limit} published pages. "
            "Unpublish one, or upgrade your Gamma Cloud plan.")


def page_cap(ws: str) -> dict:
    """``{used, max, plan}`` of a workspace here: its root pages, and the
    cap its owner's plan (the linked identity's last ``plan`` claim, stored
    at every exchange and sign-in) puts on it. ``max`` is None when there is
    none: on a server that is not a share host, for a workspace that is not
    its owner's default personal one (the one the exchange publishes into),
    for an owner without a cloud identity, and for a plan
    ``config.PLAN_PAGE_LIMITS`` does not name."""
    owner = workspaces.personal_owner(ws)
    plan = str((cloud_auth.status_of(owner) or {}).get("plan") or "") if owner else ""
    cap = None
    if owner and this_is_share_host() and workspaces.default_workspace(owner) == ws:
        cap = config.plan_page_limits().get(plan)
    with connect_pages_db(ws) as conn:
        used = conn.execute("SELECT COUNT(*) FROM unified_blocks WHERE parent_id = 'root'").fetchone()[0]
    return {"used": used, "max": cap, "plan": plan}


def cap_refusal(ws: str) -> dict | None:
    """The 402 body (``{detail, limit, used, plan}``) when one more root page
    in ``ws`` would pass its plan's cap, else None. Reads nothing more unless
    this server is a share host."""
    if not this_is_share_host():
        return None
    cap = page_cap(ws)
    if cap["max"] is None or cap["used"] < cap["max"]:
        return None
    return {"detail": cap_message(cap["plan"], cap["max"]), "limit": cap["max"], "used": cap["used"],
            "plan": cap["plan"]}


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
    the invitations waiting for the subject are claimed, as on a sign-in.
    Only on a share host: the router refuses the call elsewhere."""
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


def _read_limit(remote: Remote) -> dict | None:
    """``{used, max, plan}`` of the person's workspace on the share host
    (``GET /api/publish/limit`` under the mirror's token), None when it
    cannot be read. Nothing is cached."""
    try:
        out = _json(remote, "GET", "/api/publish/limit")
    except (RemoteError, ValueError):
        return None
    if not isinstance(out, dict) or not isinstance(out.get("used"), int):
        return None
    cap = out.get("max")
    return {"used": out["used"], "max": cap if isinstance(cap, int) else None, "plan": str(out.get("plan") or "")}


def _page_host(host: str) -> str:
    """The share host's page-host pattern from its ``/api/server-config``
    ("" when it has none or cannot be read)."""
    try:
        cfg = Remote(host, "", "").get("/api/server-config") or {}
    except (RemoteError, ValueError):
        return ""
    return str(cfg.get("page_host") or "").strip().lower() if isinstance(cfg, dict) else ""


def _title(ws: str, page_id: str) -> str:
    with connect_pages_db(ws) as conn:
        row = conn.execute("SELECT content FROM unified_blocks WHERE id = ?", (page_id,)).fetchone()
    return row[0] if row else ""


def _addresses(mirror: dict, ws: str, page_id: str, token: str) -> dict:
    """The answer's ``url`` (the token link, which always works) and
    ``public_url`` (the pretty address when the share host has page hosts,
    else the token link)."""
    url = f"{mirror['remote_url']}/?share={token}"
    pretty = public_url(mirror["remote_url"], _page_host(mirror["remote_url"]),
                        mirror["status"].get("remote_user") or "", _title(ws, page_id), page_id)
    return {"url": url, "public_url": pretty or url}


def _cap_error(limit: dict, detail: str = "") -> PublishError:
    return PublishError(409, detail or cap_message(limit["plan"], limit["max"]), {"limit": limit})


def _check_cap(user: str, ws: str, page_id: str, host: str, server_name: str) -> None:
    """Before a page goes to the share host for the first time: refuse it
    (409, the cap message, ``limit``) when the person's workspace there is
    full, taking it back out of the filter. The share host's plan is the
    one its last exchange saw, so a full workspace is exchanged once more
    first: an upgrade counts at once."""
    mirror = sync_engine.get_mirror(ws, with_token=True)
    if _synced(ws, page_id) or mirror["page_filter"] is None:
        return
    limit = _read_limit(_remote(mirror))
    if limit and limit["max"] is not None and limit["used"] >= limit["max"]:
        try:
            _exchange(user, host, server_name)  # hands every publishing mirror of the account the new token
            limit = _read_limit(_remote(sync_engine.get_mirror(ws, with_token=True))) or limit
        except PublishError:
            pass
    if limit and limit["max"] is not None and limit["used"] >= limit["max"]:
        _drop(ws, page_id)
        raise _cap_error(limit)


def _drop(ws: str, page_id: str) -> None:
    with sync_engine.round_lock(ws):
        sync_engine.filter_remove(ws, [page_id])


def _synced(ws: str, page_id: str) -> bool:
    with connect_pages_db(ws) as conn:
        return bool(conn.execute("SELECT 1 FROM sync_pages WHERE page_id = ?", (page_id,)).fetchone())


def _mirror_view(mirror: dict) -> dict:
    ws = mirror["workspace_id"]
    return {"ws": ws, "status": mirror["status"], "page_filter": mirror["page_filter"],
            "mode": mirror["mode"], "detached": mirror["mode"] == "off",
            "conflicts_open": sync_engine.open_conflicts(ws),
            "pending_local": sync_engine.pending_local(mirror)}


def publish(user: str, ws: str, page_id: str, *, audience: str | None = None, role: str | None = None,
            server_name: str = "") -> dict:
    """Publish ``page_id``: into the filtered mirror (made when missing),
    one round now, then the share on the share host (default anyone / view;
    ``audience`` / ``role`` set it, on a new link or an existing one).
    ``{url, public_url, share, mirror: {ws, status, ...}}``. A page new to
    the share host must fit the person's plan there (``_check_cap``)."""
    if audience is not None and audience not in SHARE_AUDIENCES:
        raise PublishError(400, "audience must be anyone, users or list")
    if role is not None and role not in SHARE_ROLES:
        raise PublishError(400, "role must be view or edit")
    if publishing_blocked():
        raise PublishError(409, IS_SHARE_HOST)
    _require_page(ws, page_id)
    if not cloud_auth.settings()["enabled"] or not cloud_auth.grant_of(user)[1]:
        raise PublishError(409, SIGN_IN)
    host = share_host()
    _mirror_for(user, ws, page_id, host, server_name)
    _check_cap(user, ws, page_id, host, server_name)
    status = sync_engine.sync_workspace(ws)
    if not _synced(ws, page_id):
        error = status.get("last_error") or ""
        if error.startswith(f"{page_id}: 402: "):
            # the cap reached between the check and the round: the share host's own words
            _drop(ws, page_id)
            mirror = sync_engine.get_mirror(ws, with_token=True)
            if mirror["page_filter"]:
                sync_engine.sync_workspace(ws)  # a clean round, so the refusal does not stay the mirror's error
            limit = _read_limit(_remote(mirror)) or {"used": 0, "max": None, "plan": ""}
            raise _cap_error(limit, error[len(f"{page_id}: 402: "):])
        raise PublishError(502, f"The page did not reach the share host: {error or 'try again'}")
    mirror = sync_engine.get_mirror(ws, with_token=True)
    remote = _remote(mirror)
    wanted = {k: v for k, v in (("audience", audience), ("role", role)) if v is not None}
    try:
        share = _json(remote, "POST", f"/api/share/{page_id}", wanted or None)
        if any(share.get(k) != v for k, v in wanted.items()):
            share = _json(remote, "PUT", f"/api/share-settings/{page_id}", wanted)
    except RemoteError as e:
        raise PublishError(502, f"The share host refused the share: {e.detail or e}") from e
    return {**_addresses(mirror, ws, page_id, share["token"]), "share": share, "mirror": _mirror_view(mirror)}


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
    """``{published, can_publish, reason?, url?, public_url?, share?,
    status?, mirror?, limit?, error?}``: whether the page is published (in
    the filter of the workspace's mirror of the share host), its live share
    there and addresses, the mirror's raw status, and the person's page cap
    there (``limit: {used, max, plan}``, read whenever a publishing token
    exists). ``can_publish`` / ``reason`` say whether the Publish action
    would be refused before it is tried."""
    _require_page(ws, page_id)
    out: dict = {"published": False, "can_publish": True}
    reason, host = "", ""
    mirror = sync_engine.get_mirror(ws, with_token=True)
    if publishing_blocked():
        reason = IS_SHARE_HOST
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
    else:
        # the cap there, through this workspace's publishing token or another of the account's
        lender = mirror if mirror and mirror["page_filter"] is not None else next(iter(_publish_mirrors(user, host)), None)
        limit = _read_limit(_remote(lender)) if lender else None
        if limit:
            out["limit"] = limit
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
        out.update(published=True, share=share, **_addresses(mirror, ws, page_id, share["token"]))
    return out
