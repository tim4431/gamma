"""Audio recordings synced with notes (docs/dev/recording.md).

A recording is a block whose ``properties.recording`` is an object:
``{started, segments: [{url, started, dur, bytes, mime}], state, by, at,
dur}``. ``started`` is the wall-clock ms its first segment's audio began;
each segment is a standalone audio file (``/api/uploads/<hash>.webm``,
``.m4a`` or ``.ogg``) with the wall-clock ms its own audio began, its
length in ms, its size and its container type, in order; ``state`` is
``recording`` or ``paused`` while a tab holds the microphone (``by``, with
its heartbeat ``at``) and ``done`` after; ``dur`` is the sum of the
segments' lengths. Its ``content`` is its title, and its children are
notes like any. A transcript's paragraphs are children with ``transcript:
true`` and ``t0``, the wall-clock ms the words were begun, the clock every
block's ``t0`` and every stroke's is read on. The files are named in the
properties, so the orphan collector, share scoping, mirrors, backups and
the quota treat them as every upload; the server checks only their
containers on upload (storage.is_audio) and reads the rest as given.

Here: what the exports and the agent call a recording, and the
``transcribe`` job, which turns its audio into those paragraphs.
"""

import json
import math
import re
import urllib.error
from urllib.request import urlopen

from fractional_indexing import generate_n_keys_between

from . import ai_catalog
from .ai_client import check_allowance, upstream_detail
from .ai_settings import transcriber_for
from .blocks_store import load_json, new_block_id, page_root_id, write_lock
from .db import connect_pages_db
from .logbuf import log
from .ops import MAX_OPS, after_commit, apply_ops
from .storage import UPLOAD_REF_RE, find_upload_file, upload_media_type

TRANSCRIBE_MAX_BYTES = 25 * 1024 * 1024  # OpenAI's audio upload limit
TRANSCRIBE_TIMEOUT_S = 300               # a five-minute segment, with room
# Answers with segment timestamps (verbose_json); gpt-4o-transcribe gives
# none. A diarizing model (``…-diarize``) times its segments too and is
# asked first when the connection lists one.
TIMESTAMP_MODEL = "whisper-1"
PARAGRAPH_CHARS = 400
_SENTENCE_END = re.compile(r"[.!?…。！？]['\"’”)\]]*$")
_CJK = re.compile(r"[　-ヿ㐀-鿿豈-﫿＀-￯]")


def is_recording(props) -> bool:
    return isinstance(props, dict) and isinstance(props.get("recording"), dict)


def _ms(value) -> int | None:
    """A stored count of milliseconds, None when it is no number."""
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        return None
    return int(value)


def segments_of(props) -> list[dict]:
    """The recording's segments that name a stored file, in order."""
    segments = props["recording"].get("segments") if is_recording(props) else None
    return [s for s in segments if isinstance(s, dict) and isinstance(s.get("url"), str)
            and UPLOAD_REF_RE.fullmatch(s["url"])] if isinstance(segments, list) else []


def duration_ms(props) -> int:
    """The recording's length: its ``dur``, else the sum of its segments'."""
    dur = _ms(props["recording"].get("dur")) if is_recording(props) else None
    if dur is None:
        dur = sum(_ms(s.get("dur")) or 0 for s in segments_of(props))
    return max(0, dur)


def fmt_duration(ms) -> str:
    """``m:ss``, ``h:mm:ss`` from an hour on, in whole seconds."""
    s = max(0, int(ms or 0)) // 1000
    h, m, s = s // 3600, s // 60 % 60, s % 60
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def heading(props) -> str:
    """The line the exports write for a recording: "Recording · 12:34"."""
    return f"Recording · {fmt_duration(duration_ms(props))}"


def recording_label(props) -> str:
    """How an outline line for the agent (read_block, the notes-focus
    section) names a recording, whose text is only its title, and a
    paragraph of a transcript; "" for any other block."""
    if is_recording(props):
        n = len(segments_of(props))
        live = ", still being recorded" if props["recording"].get("state") in ("recording", "paused") else ""
        return (f"(a recording, {fmt_duration(duration_ms(props))}, {n} segment{'' if n == 1 else 's'}{live}; "
                "the text is its title, and the notes under it marked (transcript) are what was said)")
    if isinstance(props, dict) and props.get("transcript") is True:
        return "(transcript)"
    return ""


def transcript_paragraphs(segments: list[dict], max_chars: int = PARAGRAPH_CHARS) -> list[dict]:
    """A transcription's timed segments (``[{start, end, text}]``, seconds
    into the audio) as paragraphs, ``[{start, text}]``: consecutive
    segments run together, and a paragraph ends at the first sentence end
    once it is ``max_chars`` long. ``start`` is its first segment's."""
    out, text, start = [], "", 0.0
    for seg in segments:
        piece = " ".join(str(seg.get("text") or "").split())
        if not piece:
            continue
        if not text:
            start = seg.get("start") or 0.0
        # Chinese and Japanese segments join without a space
        text += piece if not text or _CJK.match(text[-1]) or _CJK.match(piece[0]) else " " + piece
        if len(text) >= max_chars and _SENTENCE_END.search(text):
            out.append({"start": start, "text": text})
            text = ""
    if text:
        out.append({"start": start, "text": text})
    return out


# --- the transcribe job -------------------------------------------------------------------

def recording_props(conn, block_id: str) -> dict | None:
    """The block's properties when it is a recording, else None."""
    row = conn.execute("SELECT properties FROM unified_blocks WHERE id = ?", (block_id,)).fetchone()
    try:
        props = load_json(row[0] or "{}") if row else None
    except ValueError:
        return None
    return props if is_recording(props) else None


def _models(proto, conf, requested: str) -> list[str]:
    """The models to ask, in order: the one asked for, else a diarizing
    model the connection lists; whisper-1 after either."""
    first = requested
    if not first:
        try:
            listed = ai_catalog.fetch_json(proto.models_request(conf)).get("data") or []
        except Exception:  # noqa: BLE001 — no listing: whisper-1 it is
            listed = []
        first = next((m["id"] for m in listed if isinstance(m, dict) and isinstance(m.get("id"), str)
                      and m["id"].endswith("-diarize")), "")
    return list(dict.fromkeys(m for m in (first, TIMESTAMP_MODEL) if m))


def _transcribe(proto, conf, models: list[str], language: str, name: str, audio: bytes):
    """One audio file's timed segments and the model that gave them: the
    models in turn while the provider answers as if one were unavailable
    (400, 403, 404), as dictation retries."""
    detail = ""
    for model in models:
        check_allowance(conf)  # read fresh: a long recording stops at the limit
        req = proto.transcription_request(conf, model, language, name, upload_media_type(name[name.rfind("."):]),
                                          audio, timestamps=True)
        try:
            with urlopen(req, timeout=TRANSCRIBE_TIMEOUT_S) as resp:
                return proto.transcript_segments(json.loads(resp.read())), model
        except urllib.error.HTTPError as error:
            detail = upstream_detail(error)
            log.warning(f"[transcribe] {model}: {detail}")
            if error.code not in (400, 403, 404):
                break
        except (OSError, ValueError) as error:
            detail = str(error)
            log.warning(f"[transcribe] {model}: {error}")
            break
    raise ValueError(f"transcription failed — {detail}")


def transcribe_job(job, ws: str, user_id: str, block_id: str, model: str = "", language: str = "") -> dict:
    """The ``transcribe`` job (routers/recordings.py): the recording's audio,
    segment by segment, through the account's speech-to-text connection
    (ai_settings.transcriber_for) with timestamps, written as its
    transcript in place of the one a run before wrote. A paragraph's ``t0``
    is its segment's ``started`` plus its start within that file (a
    segment without ``started`` begins where the one before it ended, the
    first one at the recording's ``started``). Nothing is written before
    the last segment is done, so a failure
    or a stop leaves the old transcript as it was. Not metered, like
    dictation, but a shared entry's allowance still refuses it."""
    with connect_pages_db(ws) as conn:
        props = recording_props(conn, block_id)
    if props is None:
        raise ValueError("this recording is gone")
    proto, conf = transcriber_for(user_id)
    models = _models(proto, conf, model)
    segments, began = segments_of(props), _ms(props["recording"].get("started")) or 0
    paragraphs = []
    for i, seg in enumerate(segments):
        job.progress(done=i, total=len(segments), unit="segments", phase="transcribing")
        name = UPLOAD_REF_RE.fullmatch(seg["url"]).group(1)
        path = find_upload_file(name, ws)
        if path is None:
            raise ValueError(f"part {i + 1} of the recording is missing on the server")
        if path.stat().st_size > TRANSCRIBE_MAX_BYTES:
            raise ValueError(f"part {i + 1} of the recording is over 25 MB, more than transcription takes")
        timed, used = _transcribe(proto, conf, models, language, name, path.read_bytes())
        models = [used]  # the model that answered answers the rest
        started = _ms(seg.get("started"))
        started = began if started is None else started
        paragraphs += [{"text": p["text"], "t0": started + round(p["start"] * 1000)} for p in transcript_paragraphs(timed)]
        began = started + (_ms(seg.get("dur")) or 0)
    job.progress(done=len(segments), total=len(segments), unit="segments", phase="transcribing")
    _write_transcript(ws, user_id, block_id, paragraphs)
    return {"paragraphs": len(paragraphs), "model": models[0]}


def _write_transcript(ws: str, user_id: str, block_id: str, paragraphs: list[dict]) -> None:
    """On the recording's page, as one batch: the transcript children go
    and the new paragraphs follow the recording's other children, in order.
    A transcript too long for one batch (``MAX_OPS``, hours of speech) is
    written in several."""
    with connect_pages_db(ws) as conn:
        write_lock(conn)  # the children read and the batch written as one step
        page_id = page_root_id(conn, block_id)
        if page_id is None or recording_props(conn, block_id) is None:
            raise ValueError("the recording was deleted while it was transcribed")
        old, last = [], None
        for child, position, raw in conn.execute(
                "SELECT id, position, properties FROM unified_blocks WHERE parent_id = ? ORDER BY position",
                (block_id,)).fetchall():
            try:
                transcript = (load_json(raw or "{}") or {}).get("transcript") is True
            except (ValueError, AttributeError):
                transcript = False
            if transcript:
                old.append(child)
            else:
                last = position
        keys = generate_n_keys_between(last, None, len(paragraphs)) if paragraphs else []
        ops = [{"op": "delete", "id": child} for child in old] + [
            {"op": "insert", "id": new_block_id(), "parent": block_id, "position": key, "content": p["text"],
             "props": {"transcript": True, "t0": p["t0"]}} for p, key in zip(paragraphs, keys)]
        for start in range(0, len(ops), MAX_OPS):
            after_commit(ws, conn, apply_ops(conn, page_id, ops[start:start + MAX_OPS], actor=user_id,
                                             client="transcribe"))
