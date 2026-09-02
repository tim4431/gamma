"""Public read-only share tokens — one per (owner, page).

A share names a page's root block, so any page can be shared: papers (the
PDF, highlights and notes) and plain note pages alike. The token confines
unauthenticated reads to that page's subtree and assets (gamma/auth.py
share_grant / share_scope_page).
"""

import secrets
import sqlite3

from fastapi import APIRouter, HTTPException, Request

from ..auth import require_user, share_grant
from ..db import connect_users_db, page_now, user_db_path

router = APIRouter(prefix="/api", tags=["shares"])


@router.post("/share/{page_id}")
async def create_share(page_id: str, request: Request):
    user = require_user(request)
    with sqlite3.connect(user_db_path(user, "pages.db")) as conn:
        row = conn.execute(
            "SELECT parent_id, json_extract(properties, '$.doc_id') FROM unified_blocks WHERE id = ?",
            (page_id,),
        ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="page not found")
    if row[0] != "root":
        raise HTTPException(status_code=400, detail="only pages can be shared")
    doc_id = row[1] or ""
    with connect_users_db() as conn:
        # One link per page: hand back the existing token so re-sharing never
        # invalidates a link already sent around.
        existing = conn.execute(
            "SELECT token FROM shares WHERE username = ? AND page_id = ?",
            (user, page_id),
        ).fetchone()
        if existing:
            return {"token": existing[0]}
        token = secrets.token_urlsafe(12)
        conn.execute(
            "INSERT INTO shares (token, username, doc_id, page_id, created_at) VALUES (?, ?, ?, ?, ?)",
            (token, user, doc_id, page_id, page_now()),
        )
        conn.commit()
    return {"token": token}


@router.get("/share/{token}")
async def get_share(token: str, request: Request):
    """Resolve a token to its page. Goes through the same grant lookup the
    read endpoints use, so a row minted before shares were keyed by page is
    backfilled here too."""
    grant = share_grant(request, token)
    if not grant:
        raise HTTPException(status_code=404, detail="share not found")
    username, page_id = grant
    with connect_users_db() as conn:
        row = conn.execute("SELECT doc_id FROM shares WHERE token = ?", (token,)).fetchone()
    return {"page_id": page_id, "doc_id": (row[0] if row else "") or "", "username": username}
