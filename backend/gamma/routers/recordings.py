"""Audio recordings (gamma/recording.py, docs/dev/recording.md): a
recording's transcript, made as a background job. The recording itself is
a block, written through the block endpoints and the page socket like any,
and its files are uploads (``/api/upload-file``)."""

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from .. import jobs
from ..ai_settings import transcriber_for
from ..auth import require_user_id, require_ws_writer, share_scope
from ..blocks_store import assert_block_in_scope, page_root_id
from ..db import connect_pages_db
from ..recording import recording_props, segments_of, transcribe_job

router = APIRouter(prefix="/api", tags=["recordings"])


class TranscribeRequest(BaseModel):
    block_id: str
    # what dictation takes (routers/ai.py ai_transcribe); "" = the job's choice, auto-detect
    model: str = Field("", pattern=r"^(?:[A-Za-z0-9][A-Za-z0-9._:/-]{0,79})?$")
    language: str = Field("", pattern=r"^(?:[a-z]{2,3}(?:-[a-z0-9]{2,8})?)?$")


# Sync def: the block read and the connection check (ai_runtime may refresh a
# token) run in the threadpool.
@router.post("/jobs/transcribe")
def transcribe(payload: TranscribeRequest, request: Request):
    """Start the ``transcribe`` job for the recording ``block_id``: its
    audio through the account's speech-to-text connection, written as the
    recording's transcript children (gamma/recording.py
    ``transcribe_job``). A workspace writer or an edit share's signed-in
    visitor; 404 for a block that is no recording, 400 for one without
    audio yet, 503 without a connection that transcribes, 409 (with the
    ``job``) while one runs for it. → the job."""
    ws = require_ws_writer(request)
    user_id = require_user_id(request)
    block_id = payload.block_id
    with connect_pages_db(ws) as conn:
        assert_block_in_scope(conn, block_id, share_scope(request))
        props = recording_props(conn, block_id)
        page_id = page_root_id(conn, block_id) if props else None
        if page_id is None:
            raise HTTPException(status_code=404, detail="no such recording")
        content = conn.execute("SELECT content FROM unified_blocks WHERE id = ?", (block_id,)).fetchone()[0]
    if not segments_of(props):
        raise HTTPException(status_code=400, detail="this recording has no audio yet")
    transcriber_for(user_id)  # its 503 now, not as the job's failure
    title = " ".join((content or "").split())[:80] or "Recording"
    try:
        return jobs.start("transcribe", owner=user_id, ws=ws, key=block_id, title=f"Transcribe “{title}”",
                          params={"block_id": block_id, "page_id": page_id, "title": title},
                          run=lambda job: transcribe_job(job, ws, user_id, block_id, payload.model,
                                                         payload.language))
    except jobs.Busy as busy:
        return JSONResponse(status_code=409, content={"detail": "this recording is being transcribed already",
                                                      "job": busy.job})
