"""Collaboration endpoints: the op batch write, the op-log catch-up read,
and the per-page websocket (presence + fan-out). See gamma/ops.py for the
op vocabulary and gamma/collab.py for the rooms; docs/dev/collab.md for the
whole picture."""

import json
import secrets

from fastapi import APIRouter, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse

from .. import collab
from ..auth import (ANONYMOUS_NAME, SESSION_COOKIE, actor_of, is_link_visitor, link_name, link_ratelimit,
                    note_share_miss, require_ws_writer, requested_ws, resolve_ws, session_lookup,
                    share_lookup, share_scope, workspace_access)
from ..db import connect_pages_db
from ..ops import OpError, OpsRequest, commit_ops, latest_seq, ops_since

router = APIRouter(prefix="/api", tags=["collab"])

# Link visitors (an anyone-with-the-link edit share) are rate limited per IP:
# typing flushes a batch every few hundred ms at most, so this stops floods
# without touching a real editor.
LINK_OPS_PER_MINUTE = 600


def _scope_page(request: Request, ws: str, page_id: str):
    """The request's ShareScope (None for a member), 403 unless it reaches ``page_id``."""
    scope = share_scope(request)
    if scope is not None:
        with connect_pages_db(ws) as conn:
            if not scope.allows_page(conn, page_id):
                raise HTTPException(status_code=403, detail="not accessible via this share link")
    return scope


@router.post("/pages/{page_id}/ops")
def post_ops(page_id: str, payload: OpsRequest, request: Request):
    """Apply a batch of block ops to a page: ``{client, batch?, ops,
    cursor?}`` → ``{seq, at, ops}`` with the ops as applied (positions the
    server had to re-key carry their final value). ``cursor`` (``{block,
    anchor, head}``, the writer's caret in the text after the batch) is
    fanned out with the batch and stored as the writer's presence. A batch
    id already applied for this client gets the same answer again, nothing
    re-applied. A refused batch: ``{detail, missing?, conflict?, index?}``
    (gamma/ops.py ``OpError``). A workspace editor or an edit share. Sync
    def, like every endpoint that touches a database: the batch may wait on
    the workspace's write lock, and the fan-out (``collab.publish``) is
    scheduled on the sockets' loop from the worker thread."""
    ws = require_ws_writer(request)
    link_ratelimit(request, "ops", LINK_OPS_PER_MINUTE, 60)
    scope = _scope_page(request, ws, page_id)
    ops = [op.model_dump(exclude_unset=True) for op in payload.ops]
    cursor = None
    if payload.cursor is not None:
        cursor = {"block": payload.cursor.block[:64], "anchor": payload.cursor.anchor,
                  "head": payload.cursor.head}
    try:
        result = commit_ops(ws, page_id, ops, actor=actor_of(request),
                            client=payload.client[:32], share_scoped=scope is not None,
                            cursor=cursor, batch_id=payload.batch[:64])
    except OpError as e:
        body = {"detail": e.detail, **({"missing": e.missing} if e.missing else {}),
                **({"conflict": e.conflict, "index": e.index} if e.conflict else {})}
        return JSONResponse(status_code=e.status, content=body)
    return {"seq": result["seq"], "at": result["at"], "ops": result["ops"]}


@router.get("/pages/{page_id}/ops")
def get_ops(page_id: str, request: Request, since: int = 0):
    """The page's op log after ``since`` (a reconnecting client's catch-up):
    ``{seq, batches: [{seq, actor, client, at, ops}]}``; 410 when the log was
    pruned past ``since`` or the batches after it are more than a catch-up
    carries (ops.CATCHUP_MAX_BATCHES / CATCHUP_MAX_BYTES) — reload the tree
    instead."""
    ws = resolve_ws(request)
    _scope_page(request, ws, page_id)
    with connect_pages_db(ws) as conn:
        row = conn.execute(
            "SELECT parent_id FROM unified_blocks WHERE id = ?", (page_id,)).fetchone()
        if not row or row[0] != "root":
            raise HTTPException(status_code=404, detail="page not found")
        batches, pruned = ops_since(conn, page_id, since)
        seq = latest_seq(conn, page_id)
    if pruned:
        raise HTTPException(status_code=410, detail="op log pruned — reload the page")
    return {"seq": seq, "batches": batches}


def _socket_access(sock: WebSocket, page_id: str) -> tuple[str, collab.Peer] | None:
    """Who may join a page's room: ``(workspace, the peer to be)`` — not in
    a room yet, its client id and colour still to come — or None. The HTTP
    middleware never sees a websocket, so the session cookie, the workspace
    (``?ws=`` or the account's default) and the share token are resolved
    here; whether they admit the viewer is ``collab.peer_access`` — the rule
    ``revalidate`` applies again when access changes. A visitor without an
    account shows under the display name in ``?name=`` (else Anonymous).
    Reads users.db and pages.db: the handshake runs it in a worker thread."""
    sess = session_lookup(sock.cookies.get(SESSION_COOKIE))
    sock.state.user = sess[0] if sess else None
    sock.state.is_guest = bool(sess and sess[1])
    sock.state.is_admin = bool(sess and sess[2])
    token = sock.query_params.get("share") or ""
    if token:
        share = share_lookup(token)
        if not share:
            try:
                note_share_miss(sock)
            except HTTPException:
                pass  # the socket is closed either way
            return None
        ws = share["workspace_id"]
    elif sock.state.user:
        ws, _role = workspace_access(sock.state.user, requested_ws(sock), sess[3])
    else:
        return None
    account, is_guest = sock.state.user or "", sock.state.is_guest
    can_edit = collab.peer_access(ws, page_id, account, is_guest, token)
    if can_edit is None:
        return None
    if is_link_visitor(sock):
        user, name = "", link_name(sock.query_params.get("name", ""))
    else:
        user, name = account, account or ANONYMOUS_NAME
    return ws, collab.Peer(ws=sock, client="", user=user, name=name, color=0, can_edit=can_edit,
                           account=account, is_guest=is_guest, token=token)


@router.websocket("/ws/page/{page_id}")
async def page_socket(sock: WebSocket, page_id: str):
    """The page's live channel. Server → client: ``hello {client, color,
    seq, peers}`` on join, ``join {peer}`` / ``leave {client}``, ``cursor
    {client, block, anchor, head}``, ``ops {seq, actor, client, at, ops}``
    for every applied batch, ``reload`` for changes ops can't express (a
    restore included), ``trashed`` when the page went to Recently deleted.
    Client → server: ``cursor {block, anchor, head}`` only — writes are
    ``POST /pages/{id}/ops``. Closed with 4403 when access is refused or
    revoked (``collab.revalidate``), 4409 when the same tab joined again.
    The database reads (the access check, the log position) run in worker
    threads; the room itself lives on the loop. A client gone at any point
    of the handshake (navigated away as the socket opened) is an ordinary
    close, never an error."""
    access = await run_in_threadpool(_socket_access, sock, page_id)
    if not access:
        await collab.close_quietly(sock, collab.CLOSE_REVOKED)
        return
    ws, peer = access
    room = None
    try:
        await sock.accept()
        client = peer.client = (sock.query_params.get("client") or secrets.token_urlsafe(6))[:32]
        room = collab.room_for(ws, page_id)
        same_tab = room.peers.get(client) if room else None  # a reconnect keeps its colour
        peer.color = same_tab.color if same_tab else room.next_color() if room else 0
        room = collab.join(ws, page_id, peer)
        # The log position is read only now that the peer is in the room: a
        # batch committed before this read is counted in the hello (the client
        # catches up on it), one after it reaches the peer as it is fanned out —
        # possibly just before the hello, which the client's ordered inbox takes
        # as it takes any batch.
        def log_position() -> int:
            with connect_pages_db(ws) as conn:
                return latest_seq(conn, page_id)
        seq = await run_in_threadpool(log_position)
        await sock.send_text(json.dumps({"t": "hello", "client": client, "color": peer.color, "seq": seq,
                                         "peers": room.presence()}))
        await room.broadcast(json.dumps({"t": "join", "peer": peer.public()}), exclude=client)
        # Access once more now that the peer is in the room (after the hello,
        # which stays the first message a socket gets): a revoke that landed
        # between the handshake's check and the join found no peer to close
        # (revalidate walks the rooms); this finds it.
        can_edit = await run_in_threadpool(collab.peer_access, ws, page_id, peer.account, peer.is_guest, peer.token)
        if can_edit is None:
            if collab.leave(room, peer):
                collab.publish(ws, page_id, {"t": "leave", "client": client})
            await collab.close_quietly(sock, collab.CLOSE_REVOKED)
            return
        if can_edit != peer.can_edit:
            peer.can_edit = can_edit
            collab.publish(ws, page_id, {"t": "join", "peer": peer.public()}, exclude=client)
        while True:
            msg = await sock.receive_json()
            if not isinstance(msg, dict):
                continue
            if msg.get("t") == "cursor":
                peer.block = str(msg.get("block") or "")[:64]
                peer.anchor = int(msg.get("anchor", -1)) if msg.get("anchor") is not None else -1
                peer.head = int(msg.get("head", -1)) if msg.get("head") is not None else -1
                await room.broadcast(json.dumps({
                    "t": "cursor", "client": client, "block": peer.block,
                    "anchor": peer.anchor, "head": peer.head}), exclude=client)
    except (WebSocketDisconnect, RuntimeError, ValueError, TypeError):
        pass
    finally:
        # Announce through publish (its own task on the loop), never by
        # awaiting here: a handler being torn down may be inside a cancelled
        # scope, and a cancelled send would leave the others with a ghost peer.
        # Nothing to announce when a newer socket of this tab took its place,
        # access was revoked (revalidate announced it) or a failed send
        # dropped it (the room announced it).
        if room is not None and collab.leave(room, peer):
            collab.publish(ws, page_id, {"t": "leave", "client": peer.client})
