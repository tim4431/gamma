"""AI chat persistence: the active conversation per bucket plus its history.

Bucket keys are a block id — the focused page's, or a folder's (the folder
view's chat, one conversation per folder) — or "home" (the library-root
chat). A folder's chat follows the folder through renames and moves without
anything to update.

Both tables live in the workspace's pages.db, beside the pages they are
about, so a backup, a restore and a Gamma export carry them with the pages
(a page's chats go when the page is deleted for good, ops.delete_page; a
deleted folder's are filed into the library's history, ``file_into_home``).

`chats` holds ONE active conversation per bucket (what ChatDock shows and
autosaves); `chat_history` holds the bucket's earlier conversations. "New
chat" archives the active one into history and opening a history entry swaps
it back — the client sends its current messages with both calls, so nothing
still sitting in the autosave debounce is lost. Titles are user-given, else
derived from the first user message when a conversation is archived.

Several tabs (or members) may hold the same conversation, so its writes are
conditional: the active row's ``updated_at`` is its version, a client sends
the one it last read or wrote, and a save made from an older copy is refused
with 409 and the stored conversation for the client to merge
(chat/chatSession.js). "New chat" or opening an entry from an older copy
archives the newer stored conversation too instead of deleting it. Writing
takes an editor (``require_ws(write=True)``): viewers and read-scope tokens
read chats, never change them.
"""

import json
import uuid

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from ..auth import require_ws, resolve_ws, share_scope
from ..db import connect_pages_db, page_now, stamp_after
from ..json_response import OrjsonResponse
from ..ops import StorableBody


router = APIRouter(prefix="/api/chats", tags=["chats"])
history_router = APIRouter(prefix="/api/chat-history", tags=["chats"])

TITLE_MAX = 80
HISTORY_LIST_CAP = 200


def _require_chat_writer(request: Request) -> str:
    if request.query_params.get("share"):
        raise HTTPException(status_code=403, detail="shared chat is read-only")
    return require_ws(request, write=True)


class ChatSaveRequest(StorableBody):
    messages: list | None = None   # None = keep the stored messages (a rename)
    title: str | None = None       # None = keep the stored title
    # The version (the active row's updated_at) the client's copy is based
    # on, "" = it saw no conversation; None = unconditional (older clients).
    updated_at: str | None = None


class ChatArchiveRequest(StorableBody):
    bucket: str
    messages: list = []   # the client's current conversation (authoritative)
    title: str = ""
    updated_at: str | None = None  # the version the client's copy is based on (see ChatSaveRequest)


class ChatTitleRequest(StorableBody):
    title: str


class ChatDeleteRequest(StorableBody):
    ids: list[str] = []


def _clean_title(raw) -> str:
    return " ".join(str(raw or "").split())[:TITLE_MAX]


def _first_user_text(messages: list) -> str:
    for m in messages or []:
        if isinstance(m, dict) and m.get("role") == "user":
            return str(m.get("text") or m.get("content") or "")
    return ""


def derive_title(messages: list) -> str:
    """The first user message's first line, quotes stripped, for a
    conversation the user never named."""
    for line in _first_user_text(messages).splitlines():
        line = line.strip()
        if line and not line.startswith(">"):
            return _clean_title(line[:TITLE_MAX])
    return ""


def _preview(messages: list) -> str:
    return " ".join(_first_user_text(messages).split())[:120]


def _message_key(message) -> str:
    """A message's identity: its client-minted ``id``, else its content."""
    if isinstance(message, dict) and message.get("id"):
        return str(message["id"])
    return json.dumps(message, sort_keys=True)


def _holds(messages: list, others: list) -> bool:
    """Every message of ``others`` is in ``messages``."""
    keys = {_message_key(m) for m in messages}
    return all(_message_key(m) in keys for m in others)


def _file(database, bucket: str, messages: list, title: str, now: str) -> str:
    entry_id = uuid.uuid4().hex
    database.execute(
        "INSERT INTO chat_history (id, bucket, title, messages, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (entry_id, bucket, title, json.dumps(messages), now, now),
    )
    return entry_id


def _archive(database, bucket: str, messages: list, title: str, seen: str | None = None) -> str | None:
    """Move a non-empty conversation into the bucket's history and clear the
    active row. Returns the new history id, or None when there was nothing
    to keep (the active row is cleared either way).

    ``seen`` is the version the client's copy is based on. When the stored
    conversation moved on since (another tab or member kept talking), it is
    archived too instead of being deleted — unless the client's copy already
    holds all of it; and when the stored one holds all of the client's, only
    the stored one is kept."""
    now = page_now()
    row = database.execute("SELECT title, messages, updated_at FROM chats WHERE bucket = ?",
                           (bucket,)).fetchone()
    database.execute("DELETE FROM chats WHERE bucket = ?", (bucket,))
    stored_title = (row[0] if row else "") or ""
    entry_id = None
    if row and seen is not None and seen != row[2]:
        stored = json.loads(row[1] or "[]")
        if stored and not _holds(messages, stored):
            entry_id = _file(database, bucket, stored, stored_title or derive_title(stored), now)
            if _holds(stored, messages):
                messages = []
    if not messages:
        return entry_id
    title = _clean_title(title) or stored_title or derive_title(messages)
    return _file(database, bucket, messages, title, now)


def file_into_home(database, buckets) -> int:
    """The conversations of buckets no view opens any more (deleted
    folders): each active one filed into the library chat's history
    (``home``) and every archived one moved there, inside the caller's
    transaction — a conversation is never dropped with its folder. Returns
    how many conversations moved."""
    now = page_now()
    ids = json.dumps(sorted(buckets))
    moved = 0
    for bucket, title, raw in database.execute(
            "SELECT bucket, title, messages FROM chats WHERE bucket IN (SELECT value FROM json_each(?))",
            (ids,)).fetchall():
        database.execute("DELETE FROM chats WHERE bucket = ?", (bucket,))
        messages = json.loads(raw or "[]")
        if messages:
            _file(database, "home", messages, title or derive_title(messages), now)
            moved += 1
    return moved + database.execute(
        "UPDATE chat_history SET bucket = 'home' WHERE bucket IN (SELECT value FROM json_each(?))", (ids,)).rowcount


@router.get("/{bucket}")
def get_chat(bucket: str, request: Request):
    """``{messages, title, updated_at}`` — ``updated_at`` the version a
    later save names ("" when there is no conversation); a share view reads
    ``{messages, title}`` only."""
    ws = resolve_ws(request)
    scope = share_scope(request)
    with connect_pages_db(ws) as database:
        if scope is not None and not scope.allows_page(database, bucket):
            raise HTTPException(status_code=403, detail="chat is outside the shared page")
        row = database.execute(
            "SELECT messages, title, updated_at FROM chats WHERE bucket = ?", (bucket,)
        ).fetchone()
    out = {"messages": json.loads(row[0]) if row else [], "title": (row[1] if row else "") or ""}
    if scope is None:
        out["updated_at"] = row[2] if row else ""
    return OrjsonResponse(out)  # a long conversation: encoded here, not on the event loop


@router.put("/{bucket}")
def save_chat(bucket: str, payload: ChatSaveRequest, request: Request):
    """Save the active conversation: ``{messages?, title?, updated_at?}`` →
    ``{ok, updated_at}``. A save whose ``updated_at`` is not the stored
    version is refused with 409 ``{detail, messages, title, updated_at}``
    (the stored conversation) and changes nothing. Without ``messages`` only
    the title changes (a rename), unconditionally."""
    ws = _require_chat_writer(request)
    with connect_pages_db(ws) as database:
        database.execute("BEGIN IMMEDIATE")  # read, compare and write as one step
        row = database.execute(
            "SELECT messages, title, updated_at FROM chats WHERE bucket = ?", (bucket,)
        ).fetchone()
        stored_at = row[2] if row else ""
        if payload.messages is None:
            at = stored_at or page_now()
            if payload.title is not None:
                database.execute(
                    "INSERT INTO chats (bucket, messages, updated_at, title) VALUES (?, '[]', ?, ?) "
                    "ON CONFLICT(bucket) DO UPDATE SET title = excluded.title",
                    (bucket, at, _clean_title(payload.title)))
        elif payload.updated_at is not None and payload.updated_at != stored_at:
            database.rollback()
            return JSONResponse(status_code=409, content={
                "detail": "the conversation changed since this copy was read",
                "messages": json.loads(row[0]) if row else [],
                "title": (row[1] if row else "") or "", "updated_at": stored_at})
        else:
            at = stamp_after(stored_at)  # every write changes the version
            # A save without a title (the autosave) keeps the stored one.
            database.execute(
                "INSERT INTO chats (bucket, messages, updated_at, title) VALUES (?, ?, ?, ?) "
                "ON CONFLICT(bucket) DO UPDATE SET "
                "messages = excluded.messages, updated_at = excluded.updated_at, "
                "title = CASE WHEN ? THEN excluded.title ELSE chats.title END",
                (bucket, json.dumps(payload.messages), at, _clean_title(payload.title),
                 payload.title is not None),
            )
        database.commit()
    return {"ok": True, "updated_at": at}


@router.delete("/{bucket}")
def delete_chat(bucket: str, request: Request):
    ws = _require_chat_writer(request)
    with connect_pages_db(ws) as database:
        database.execute("DELETE FROM chats WHERE bucket = ?", (bucket,))
        database.commit()
    return {"ok": True}


# --- history ------------------------------------------------------------------

@history_router.get("")
def list_history(request: Request, bucket: str = ""):
    """The bucket's earlier conversations, newest first, without messages
    (`count` + `preview` are enough for a list row)."""
    ws = require_ws(request)
    with connect_pages_db(ws) as database:
        rows = database.execute(
            "SELECT id, title, messages, created_at, updated_at FROM chat_history "
            "WHERE bucket = ? ORDER BY updated_at DESC LIMIT ?",
            (bucket, HISTORY_LIST_CAP),
        ).fetchall()
    sessions = []
    for entry_id, title, raw, created_at, updated_at in rows:
        messages = json.loads(raw or "[]")
        sessions.append({
            "id": entry_id, "title": title or derive_title(messages),
            "preview": _preview(messages), "count": len(messages),
            "created_at": created_at, "updated_at": updated_at,
        })
    return {"sessions": sessions}


@history_router.post("/archive")
def archive_chat(payload: ChatArchiveRequest, request: Request):
    """"New chat": file the active conversation (the client's copy — the
    autosave may still be pending) into history and clear the bucket. A
    newer stored conversation (``updated_at`` not the stored version) is
    archived too, never dropped."""
    ws = _require_chat_writer(request)
    if not payload.bucket:
        raise HTTPException(status_code=400, detail="bucket required")
    with connect_pages_db(ws) as database:
        database.execute("BEGIN IMMEDIATE")
        entry_id = _archive(database, payload.bucket, payload.messages, payload.title, payload.updated_at)
        database.commit()
    return {"id": entry_id}


@history_router.post("/{entry_id}/open")
def open_history(entry_id: str, payload: ChatArchiveRequest, request: Request):
    """Make a history entry the bucket's active conversation: the current
    one (sent by the client) is archived first, then the entry moves back
    into `chats` — so a conversation is always in exactly one place."""
    ws = _require_chat_writer(request)
    if not payload.bucket:
        raise HTTPException(status_code=400, detail="bucket required")
    with connect_pages_db(ws) as database:
        database.execute("BEGIN IMMEDIATE")
        row = database.execute(
            "SELECT title, messages FROM chat_history WHERE id = ?", (entry_id,)
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="conversation not found")
        active = database.execute("SELECT updated_at FROM chats WHERE bucket = ?",
                                  (payload.bucket,)).fetchone()
        _archive(database, payload.bucket, payload.messages, payload.title, payload.updated_at)
        database.execute("DELETE FROM chat_history WHERE id = ?", (entry_id,))
        at = stamp_after(active[0] if active else "")
        database.execute(
            "INSERT OR REPLACE INTO chats (bucket, messages, updated_at, title) VALUES (?, ?, ?, ?)",
            (payload.bucket, row[1], at, row[0] or ""),
        )
        database.commit()
    return {"messages": json.loads(row[1] or "[]"), "title": row[0] or "", "updated_at": at}


@history_router.put("/{entry_id}")
def rename_history(entry_id: str, payload: ChatTitleRequest, request: Request):
    ws = _require_chat_writer(request)
    with connect_pages_db(ws) as database:
        cur = database.execute("UPDATE chat_history SET title = ? WHERE id = ?",
                               (_clean_title(payload.title), entry_id))
        database.commit()
    if not cur.rowcount:
        raise HTTPException(status_code=404, detail="conversation not found")
    return {"ok": True}


@history_router.post("/delete")
def delete_history_many(payload: ChatDeleteRequest, request: Request):
    """Several archived conversations at once (the history popover's
    selection) → ``{deleted}``, the rows that were still there. Ids the
    bucket does not hold are simply not found."""
    ws = _require_chat_writer(request)
    ids = [str(entry) for entry in payload.ids if entry][:HISTORY_LIST_CAP]
    if not ids:
        return {"deleted": 0}
    with connect_pages_db(ws) as database:
        cursor = database.execute(
            f"DELETE FROM chat_history WHERE id IN ({','.join('?' * len(ids))})", ids)
        database.commit()
    return {"deleted": cursor.rowcount}


@history_router.delete("/{entry_id}")
def delete_history(entry_id: str, request: Request):
    ws = _require_chat_writer(request)
    with connect_pages_db(ws) as database:
        database.execute("DELETE FROM chat_history WHERE id = ?", (entry_id,))
        database.commit()
    return {"ok": True}
