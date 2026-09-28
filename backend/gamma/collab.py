"""Live rooms: who is on a page right now, and the fan-out of applied
operations to them.

One room per ``(workspace, page_id)``, in memory — Gamma runs as one uvicorn
process everywhere (Docker, the desktop sidecar), so nothing needs to be
shared across workers. A room holds the websocket peers
(``routers/collab.py`` accepts them) with their identity, colour and last
cursor; ``publish`` sends a message to every peer and is safe to call from
the event loop AND from threadpool code (the sync AI chat endpoint runs the
agent's tools there): sends are always scheduled on the loop the sockets
live on.

Presence is never persisted. Document changes never travel over the socket
towards the server — writes are ``POST /api/pages/{id}/ops`` (gamma/ops.py),
the socket only carries the applied batches back out.

Access is decided by ``peer_access`` — at the handshake, and again for
every peer of a workspace by ``revalidate`` whenever who may see what
changes there (a share stopped or narrowed, a member removed or re-roled):
a peer that lost access is dropped and its socket closed with 4403.
"""

import asyncio
import json
from dataclasses import dataclass, field

from .logbuf import log

PALETTE = 8  # colour indexes handed out per room (CSS: --peer-0 … --peer-7)
CLOSE_REVOKED = 4403   # the peer lost access to the page
CLOSE_REPLACED = 4409  # the same tab (client id) joined again on a newer socket

_rooms: dict[tuple[str, str], "Room"] = {}
_loop: asyncio.AbstractEventLoop | None = None  # the loop the sockets live on


@dataclass
class Peer:
    ws: object
    client: str
    user: str          # session username, "" for an anonymous share viewer
    name: str
    color: int
    can_edit: bool
    block: str = ""    # block the peer is on (focused row or open editor)
    anchor: int = -1   # selection inside the open editor, -1 = no editor open
    head: int = -1
    # What admitted it, re-checked by revalidate (never sent to the others):
    account: str = ""  # the session's account, guests included ("" without a session)
    is_guest: bool = False
    token: str = ""    # the share token it came through, "" for a member

    def public(self) -> dict:
        return {"client": self.client, "user": self.user, "name": self.name,
                "color": self.color, "can_edit": self.can_edit,
                "block": self.block, "anchor": self.anchor, "head": self.head}


@dataclass
class Room:
    key: tuple[str, str]
    peers: dict[str, Peer] = field(default_factory=dict)

    def next_color(self) -> int:
        used = {p.color for p in self.peers.values()}
        for i in range(PALETTE):
            if i not in used:
                return i
        return len(self.peers) % PALETTE

    def presence(self) -> list[dict]:
        return [p.public() for p in self.peers.values()]

    async def broadcast(self, text: str, exclude: str = "") -> None:
        targets = [p for p in list(self.peers.values()) if p.client != exclude]
        if not targets:
            return
        results = await asyncio.gather(*(p.ws.send_text(text) for p in targets),
                                       return_exceptions=True)
        for peer, res in zip(targets, results):
            if isinstance(res, BaseException) and self.peers.get(peer.client) is peer:
                # A dead socket: its handler's finally block removes the peer
                # too, but don't wait for that to keep it out of the next send.
                del self.peers[peer.client]


def join(ws: str, page_id: str, peer: Peer) -> Room:
    """Add ``peer`` to the page's room. The same tab joining again (its
    client id — a reconnect before the server noticed the old socket drop)
    replaces its earlier peer, whose socket is closed; that socket's own
    teardown then finds nothing of its to remove (``leave``)."""
    global _loop
    _loop = asyncio.get_running_loop()
    room = _rooms.setdefault((ws, page_id), Room((ws, page_id)))
    old = room.peers.get(peer.client)
    room.peers[peer.client] = peer
    if old is not None and old.ws is not peer.ws:
        _schedule(_close(old.ws, CLOSE_REPLACED))
    return room


def leave(room: Room, peer: Peer) -> bool:
    """Take ``peer`` out of its room — only that very peer (a newer socket of
    the same tab may hold its client id by now), and the room out of the
    registry only while it is still the registered one. True when the peer
    was removed, i.e. when the others should hear it left."""
    if room.peers.get(peer.client) is not peer:
        return False
    del room.peers[peer.client]
    if not room.peers and _rooms.get(room.key) is room:
        del _rooms[room.key]
    return True


async def _close(sock, code: int) -> None:
    try:
        await sock.close(code=code)
    except Exception:  # noqa: BLE001 — already closed / torn down
        pass


def room_for(ws: str, page_id: str) -> Room | None:
    return _rooms.get((ws, page_id))


def _schedule(coro) -> None:
    """Run a coroutine on the sockets' loop from anywhere."""
    if _loop is None or _loop.is_closed():
        coro.close()
        return
    try:
        running = asyncio.get_running_loop()
    except RuntimeError:
        running = None
    if running is _loop:
        _loop.create_task(coro)
    else:
        asyncio.run_coroutine_threadsafe(coro, _loop)


def publish(ws: str, page_id: str, message: dict, exclude: str = "") -> None:
    """Send ``message`` to everyone in the page's room (no room → no-op)."""
    room = _rooms.get((ws, page_id))
    if not room or not room.peers:
        return
    try:
        _schedule(room.broadcast(json.dumps(message), exclude))
    except Exception as e:  # never let a fan-out failure break the write
        log.warning(f"[collab] publish failed: {e}")


def publish_ops(ws: str, result: dict) -> None:
    """Fan out an applied batch (the dict ``ops.apply_ops`` returns). A
    ``cursor`` on the result (the writer's caret after the batch) rides
    along and becomes the writer's stored presence, so a later joiner's
    ``hello`` shows it too."""
    msg = {
        "t": "ops", "seq": result["seq"], "at": result["at"],
        "actor": result["actor"], "client": result["client"], "ops": result["ops"],
    }
    cursor = result.get("cursor")
    if cursor is not None:
        msg["cursor"] = cursor
        room = _rooms.get((ws, result["page_id"]))
        peer = room.peers.get(result["client"]) if room else None
        if peer is not None:
            peer.block = str(cursor.get("block") or "")[:64]
            peer.anchor = int(cursor.get("anchor", -1))
            peer.head = int(cursor.get("head", -1))
    publish(ws, result["page_id"], msg)


def publish_reload(ws: str, page_id: str, seq: int | None = None) -> None:
    """Tell the room the page changed in a way ops can't express (a whole
    subtree replace, an import): clients refetch the tree."""
    publish(ws, page_id, {"t": "reload", "seq": seq})


def publish_all(ws: str, message: dict) -> None:
    """Every room of one workspace (library-wide rewrites)."""
    for key in list(_rooms):
        if key[0] == ws:
            publish(ws, key[1], message)


# --- access ---------------------------------------------------------------------

def peer_access(ws: str, page_id: str, account: str, is_guest: bool, token: str) -> bool | None:
    """May this viewer be in the page's room, and edit there? True (edit),
    False (presence only) or None (no access) — the same rules as HTTP: a
    share token admits its audience (``auth.share_access``) to the pages in
    its scope; without one, a member joins with their workspace role (a
    viewer only watches). ``account`` is the session's account ("" without
    one; a guest counts as not signed in for shares)."""
    from . import workspaces  # local: they import db / seed, which must not import rooms
    from .auth import ShareScope, share_access, share_lookup
    from .db import connect_pages_db

    scope = None
    if token:
        share = share_lookup(token)
        if not share or share["workspace_id"] != ws:
            return None
        level, _reason = share_access(share, account or None, is_guest)
        if not level:
            return None
        can_edit, scope = level == "edit", ShareScope.of(share)
    elif account:
        role = workspaces.role_of(ws, account)
        if not role:
            return None
        can_edit = role != "viewer"
    else:
        return None
    with connect_pages_db(ws) as conn:
        row = conn.execute("SELECT parent_id FROM unified_blocks WHERE id = ?", (page_id,)).fetchone()
        if not row or row[0] != "root":
            return None
        if scope is not None and not scope.allows_page(conn, page_id):
            return None
    return can_edit


def revalidate(ws: str) -> None:
    """Who may see what in the workspace changed (a share stopped or
    narrowed, a member removed or re-roled, the workspace made private):
    run ``peer_access`` again for every peer in its rooms. A peer that lost
    access leaves the room at once and its socket is closed (4403); one
    whose edit right changed is announced again (a fresh ``join``). Runs on
    the sockets' loop, so it is safe from anywhere; a no-op without rooms.
    The access checks read the databases, so they run in a worker thread."""
    if any(key[0] == ws for key in list(_rooms)):
        _schedule(_revalidate(ws))


def _access_of(ws: str, targets: list) -> list:
    """``peer_access`` for each ``(page_id, peer)``; None (no access) for a
    check that fails — a workspace deleted meanwhile."""
    out = []
    for page_id, peer in targets:
        try:
            out.append(peer_access(ws, page_id, peer.account, peer.is_guest, peer.token))
        except Exception as e:  # noqa: BLE001
            log.warning(f"[collab] access check for {peer.client} failed: {e}")
            out.append(None)
    return out


async def _revalidate(ws: str) -> None:
    targets = [(key, room, peer) for key, room in list(_rooms.items()) if key[0] == ws
               for peer in list(room.peers.values())]
    if not targets:
        return
    decisions = await asyncio.to_thread(_access_of, ws, [(key[1], peer) for key, _room, peer in targets])
    for (key, room, peer), can_edit in zip(targets, decisions):
        if room.peers.get(peer.client) is not peer:
            continue  # left, or replaced by a newer socket of its tab, while the checks ran
        if can_edit is None:
            if leave(room, peer):
                publish(ws, key[1], {"t": "leave", "client": peer.client})
            await _close(peer.ws, CLOSE_REVOKED)
        elif can_edit != peer.can_edit:
            peer.can_edit = can_edit
            publish(ws, key[1], {"t": "join", "peer": peer.public()}, exclude=peer.client)
