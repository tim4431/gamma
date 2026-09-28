"""AI chat persistence: the active conversation per bucket plus its history.

Bucket keys are the focused page's block id, "home" (the library-root chat),
or "home:<folder path>" (per-folder chats — one conversation per folder view).
Folder paths nest on "/", so the key routes use the :path converter (uvicorn
decodes %2F before routing, a plain {block_id} would 404 on nested folders).

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
from pydantic import BaseModel

from ..auth import require_ws, resolve_ws, share_scope
from ..db import connect_data_db, connect_pages_db, page_now, stamp_after


router = APIRouter(prefix="/api/chats", tags=["chats"])
history_router = APIRouter(prefix="/api/chat-history", tags=["chats"])

TITLE_MAX = 80
HISTORY_LIST_CAP = 200


def _require_chat_writer(request: Request) -> str:
    if request.query_params.get("share"):
        raise HTTPException(status_code=403, detail="shared chat is read-only")
    return require_ws(request, write=True)


class ChatSaveRequest(BaseModel):
    messages: list | None = None   # None = keep the stored messages (a rename)
    title: str | None = None       # None = keep the stored title
    # The version (the active row's updated_at) the client's copy is based
    # on, "" = it saw no conversation; None = unconditional (older clients).
    updated_at: str | None = None


class ChatArchiveRequest(BaseModel):
    bucket: str
    messages: list = []   # the client's current conversation (authoritative)
    title: str = ""
    updated_at: str | None = None  # the version the client's copy is based on (see ChatSaveRequest)


class ChatTitleRequest(BaseModel):
    title: str


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
    row = database.execute("SELECT title, messages, updated_at FROM chats WHERE block_id = ?",
                           (bucket,)).fetchone()
    database.execute("DELETE FROM chats WHERE block_id = ?", (bucket,))
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


def move_folder_buckets(ws: str, src: str, dst: str) -> dict:
    """Follow a folder rename/move/delete (POST /folders/rename,
    gamma/routers/folders.py): per-folder buckets embed the path in their
    key, so path rewrites must carry the conversations along — the same
    src → dst prefix mapping the frontend applies to the pages' folder tags
    (subfolders ride along). When the destination already holds a real
    conversation it wins and the source is dropped; an empty destination
    row (a save-effect echo) is overwritten. History entries simply follow
    their bucket (ids never collide). ``dst`` "" drops the conversations."""
    src_key = f"home:{src}"
    prefix_match = "(bucket = ? OR substr(bucket, 1, ?) = ?)"
    with connect_data_db(ws) as database:
        database.execute("BEGIN IMMEDIATE")  # a save landing meanwhile must not be left behind
        rows = database.execute(
            "SELECT block_id FROM chats WHERE block_id = ? OR substr(block_id, 1, ?) = ?",
            (src_key, len(src_key) + 1, src_key + "/"),
        ).fetchall()
        for (old_id,) in rows:
            if not dst:
                database.execute("DELETE FROM chats WHERE block_id = ?", (old_id,))
                continue
            new_id = f"home:{dst}" + old_id[len(src_key):]
            existing = database.execute(
                "SELECT messages FROM chats WHERE block_id = ?", (new_id,)
            ).fetchone()
            if existing and json.loads(existing[0] or "[]"):
                database.execute("DELETE FROM chats WHERE block_id = ?", (old_id,))
            else:
                database.execute("DELETE FROM chats WHERE block_id = ?", (new_id,))
                database.execute("UPDATE chats SET block_id = ? WHERE block_id = ?",
                                 (new_id, old_id))
        hist = database.execute(
            f"SELECT id, bucket FROM chat_history WHERE {prefix_match}",
            (src_key, len(src_key) + 1, src_key + "/"),
        ).fetchall()
        for entry_id, bucket in hist:
            if not dst:
                database.execute("DELETE FROM chat_history WHERE id = ?", (entry_id,))
            else:
                database.execute("UPDATE chat_history SET bucket = ? WHERE id = ?",
                                 (f"home:{dst}" + bucket[len(src_key):], entry_id))
        database.commit()
    return {"moved": len(rows), "history_moved": len(hist)}


@router.get("/{block_id:path}")
def get_chat(block_id: str, request: Request):
    """``{messages, title, updated_at}`` — ``updated_at`` the version a
    later save names ("" when there is no conversation); a share view reads
    ``{messages, title}`` only."""
    ws = resolve_ws(request)
    scope = share_scope(request)
    if scope is not None:
        with connect_pages_db(ws) as conn:
            if not scope.allows_page(conn, block_id):
                raise HTTPException(status_code=403, detail="chat is outside the shared page")
    with connect_data_db(ws) as database:
        row = database.execute(
            "SELECT messages, title, updated_at FROM chats WHERE block_id = ?", (block_id,)
        ).fetchone()
    out = {"messages": json.loads(row[0]) if row else [], "title": (row[1] if row else "") or ""}
    if scope is None:
        out["updated_at"] = row[2] if row else ""
    return out


@router.put("/{block_id:path}")
def save_chat(block_id: str, payload: ChatSaveRequest, request: Request):
    """Save the active conversation: ``{messages?, title?, updated_at?}`` →
    ``{ok, updated_at}``. A save whose ``updated_at`` is not the stored
    version is refused with 409 ``{detail, messages, title, updated_at}``
    (the stored conversation) and changes nothing. Without ``messages`` only
    the title changes (a rename), unconditionally."""
    ws = _require_chat_writer(request)
    with connect_data_db(ws) as database:
        database.execute("BEGIN IMMEDIATE")  # read, compare and write as one step
        row = database.execute(
            "SELECT messages, title, updated_at FROM chats WHERE block_id = ?", (block_id,)
        ).fetchone()
        stored_at = row[2] if row else ""
        if payload.messages is None:
            at = stored_at or page_now()
            if payload.title is not None:
                database.execute(
                    "INSERT INTO chats (block_id, messages, updated_at, title) VALUES (?, '[]', ?, ?) "
                    "ON CONFLICT(block_id) DO UPDATE SET title = excluded.title",
                    (block_id, at, _clean_title(payload.title)))
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
                "INSERT INTO chats (block_id, messages, updated_at, title) VALUES (?, ?, ?, ?) "
                "ON CONFLICT(block_id) DO UPDATE SET "
                "messages = excluded.messages, updated_at = excluded.updated_at, "
                "title = CASE WHEN ? THEN excluded.title ELSE chats.title END",
                (block_id, json.dumps(payload.messages), at, _clean_title(payload.title),
                 payload.title is not None),
            )
        database.commit()
    return {"ok": True, "updated_at": at}


@router.delete("/{block_id:path}")
def delete_chat(block_id: str, request: Request):
    ws = _require_chat_writer(request)
    with connect_data_db(ws) as database:
        database.execute("DELETE FROM chats WHERE block_id = ?", (block_id,))
        database.commit()
    return {"ok": True}


# --- history ------------------------------------------------------------------

@history_router.get("")
def list_history(request: Request, bucket: str = ""):
    """The bucket's earlier conversations, newest first, without messages
    (`count` + `preview` are enough for a list row)."""
    ws = require_ws(request)
    with connect_data_db(ws) as database:
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
    with connect_data_db(ws) as database:
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
    with connect_data_db(ws) as database:
        database.execute("BEGIN IMMEDIATE")
        row = database.execute(
            "SELECT title, messages FROM chat_history WHERE id = ?", (entry_id,)
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="conversation not found")
        active = database.execute("SELECT updated_at FROM chats WHERE block_id = ?",
                                  (payload.bucket,)).fetchone()
        _archive(database, payload.bucket, payload.messages, payload.title, payload.updated_at)
        database.execute("DELETE FROM chat_history WHERE id = ?", (entry_id,))
        at = stamp_after(active[0] if active else "")
        database.execute(
            "INSERT OR REPLACE INTO chats (block_id, messages, updated_at, title) VALUES (?, ?, ?, ?)",
            (payload.bucket, row[1], at, row[0] or ""),
        )
        database.commit()
    return {"messages": json.loads(row[1] or "[]"), "title": row[0] or "", "updated_at": at}


@history_router.put("/{entry_id}")
def rename_history(entry_id: str, payload: ChatTitleRequest, request: Request):
    ws = _require_chat_writer(request)
    with connect_data_db(ws) as database:
        cur = database.execute("UPDATE chat_history SET title = ? WHERE id = ?",
                               (_clean_title(payload.title), entry_id))
        database.commit()
    if not cur.rowcount:
        raise HTTPException(status_code=404, detail="conversation not found")
    return {"ok": True}


@history_router.delete("/{entry_id}")
def delete_history(entry_id: str, request: Request):
    ws = _require_chat_writer(request)
    with connect_data_db(ws) as database:
        database.execute("DELETE FROM chat_history WHERE id = ?", (entry_id,))
        database.commit()
    return {"ok": True}
