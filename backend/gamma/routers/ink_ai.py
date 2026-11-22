"""The handwriting selection's AI actions (docs/dev/handwriting_ai.md):
/ai/ink/shapes behind Redraw as shapes and /ai/ink/text behind Convert to
text. Each takes the selected strokes as the client's draft holds them —
saved or not — makes one call to the handwriting task's model and answers
with what the client applies: shape records for the strokes they replace,
or the Markdown of a text box. What is sent and how the reply is read is
gamma/ink_ai.py; nothing is stored here.

Both answer in NDJSON as /ai/translate/brief does: ``{"ping": 1}`` lines
while the model thinks (a reasoning model can be quiet past a proxy's idle
limit), then one line with the answer or ``{"error"}``."""

import json
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from .. import ai_catalog, ai_usage
from .. import ink as inkmod
from .. import ink_ai
from ..ai_client import call_ai as _call_ai, call_slot as _call_slot
from ..ai_protocols.base import truncated_stop
from ..ai_settings import require_ai_runtime, resolve_model as _resolve_model
from ..auth import require_user_id
from ..logbuf import log
from ..pdf_text import image_part
from .ai import failure_detail, keepalive_lines
from .translate import _check_door, _ndjson

router = APIRouter(prefix="/api", tags=["ink-ai"])

# A redraw's or a transcription's reply: room for a reasoning model's
# thinking before a few hundred shape records or a page of Markdown. On a
# service whose models count their thinking toward the cap, the chat's own
# larger one (ai_catalog.reply_cap): 8192 can go on the thinking alone.
_MAX_TOKENS = 8192
_TIMEOUT = 180
# Tries again once when the call failed before any of the reply came: the
# user waits on this one call.
_RETRIES = 1
CANNOT_SEE = ("This model cannot see pictures, which reading handwriting needs; "
              "pick another handwriting model in Settings → AI → Models.")
# Past the stroke file's own caps (ink.InkTooLarge): the redraw says its own
# sentence, which the client also gives for its 300 strokes.
TOO_MUCH_TO_CONVERT = "Too much handwriting to convert at once; select less."
CUT_OFF = "the AI's answer was cut off at its length limit; select less"


class InkAiRequest(BaseModel):
    """The selected strokes: ``space`` and ``strokes`` as a gamma-ink v2
    file holds them (``ink.parse_ink`` checks the pen strokes among them,
    its caps included; ``_pen_ink``). The call runs on the handwriting
    task's model: a request names none."""
    space: Any = None
    strokes: Any = None


def _pen_ink(payload: InkAiRequest, too_large: str):
    """The request's pen strokes as a file of their own, and each one's
    index among the request's strokes (as ``ink_ai.pen_strokes`` gives
    them); a 400 for strokes the file format refuses, or none to work on,
    and a 413 saying ``too_large`` past the file's caps (ink.InkTooLarge).
    Only the pen strokes are read: no other tool's stroke is ever offered,
    so a highlighter counts toward no cap, and a lasso across several
    groups is held to what one file takes of its pen strokes alone. The
    file goes through ``parse_ink`` as bytes, so its size cap and its
    refusal of NaN apply as to an upload."""
    strokes, kept = payload.strokes, None
    if isinstance(strokes, list):
        # Anything but a stroke of another tool stays, for parse_ink to refuse.
        kept = [i for i, s in enumerate(strokes) if not isinstance(s, dict) or s.get("tool", "pen") == "pen"]
        strokes = [strokes[i] for i in kept]
    doc = {"format": inkmod.FORMAT, "version": inkmod.VERSION, "space": payload.space, "strokes": strokes}
    try:
        ink = inkmod.parse_ink(json.dumps(doc, separators=(",", ":"), ensure_ascii=False).encode("utf-8"))
    except inkmod.InkTooLarge:
        raise HTTPException(status_code=413, detail=too_large)
    except inkmod.InkError as e:
        raise HTTPException(status_code=400, detail=f"invalid handwriting: {e}")
    pen, offered = ink_ai.pen_strokes(ink)
    if not offered:
        raise HTTPException(status_code=400, detail="the selection holds no pen strokes")
    return pen, offered if kept is None else [kept[i] for i in offered]


def _model(user_id: str):
    """``(runtime, entry, sees)``: the account's AI runtime (503 without a
    provider), the handwriting task's model (the chat model while it has
    none of its own), and whether it takes pictures — unless a listing says
    it does not, it is sent them, as chat does."""
    rt = require_ai_runtime(user_id)
    entry = _resolve_model(rt, "", "ink")
    conf = rt["providers"][entry["provider"]]
    return rt, entry, ai_catalog.image_input(entry["provider"], conf, entry["model"])[0] is not False


def _ask(user_id: str, rt: dict, entry: dict, system: str, text: str, image) -> str:
    """The one call: ``text`` as the user turn, the picture on it. A reply
    the output cap cut off is an error, never an answer: a transcription
    stopped partway would replace all of the strokes, and a shapes object
    left open would read as no list of shapes at all."""
    cap = max(_MAX_TOKENS, ai_catalog.reply_cap(rt["providers"][entry["provider"]], entry["model"]))
    stops: list[str] = []
    with _call_slot(user_id) as slot:
        reply = _call_ai([{"role": "user", "content": text}], system, entry, rt,
                         images=[image_part(image)] if image else None, max_tokens=cap,
                         timeout=_TIMEOUT, on_usage=ai_usage.recorder("ink", entry, rt), slot=slot,
                         retries=_RETRIES, on_stop=stops.append)
    if any(truncated_stop(s) for s in stops):
        raise RuntimeError(CUT_OFF)
    return reply


# Sync def: the AI call runs in the threadpool.
@router.post("/ai/ink/shapes")
def ai_ink_shapes(payload: InkAiRequest, request: Request):
    """Redraw as shapes: the pen strokes go to the model as a digest
    (``ink_ai.digest``) and a picture of them, when the model takes one,
    and its figures come back as ``{"shapes": [{kind, params, strokes}]}``
    — params in page points, as the stroke file stores them; strokes the
    0-based indices of the request's strokes each replaces, never a
    highlighter's (``ink_ai.read_shapes``). ``[]`` when nothing is a
    figure. 413 past MAX_REDRAW_STROKES pen strokes, or what one stroke
    file holds."""
    user_id = require_user_id(request)
    pen, offered = _pen_ink(payload, ink_ai.TOO_MANY_STROKES)
    if len(offered) > ink_ai.MAX_REDRAW_STROKES:
        raise HTTPException(status_code=413, detail=ink_ai.TOO_MANY_STROKES)
    rt, entry, sees = _model(user_id)
    # The call opens inside the stream: a refusal is a 429 while it can be.
    _check_door(rt, entry, user_id)

    def ndjson():
        try:
            drawn = ink_ai.picture(pen) if sees else None
            image, shown = drawn if drawn else (None, None)
            reply = _ask(user_id, rt, entry, ink_ai.SHAPES_SYSTEM, ink_ai.digest(pen, shown), image)
            line = {"shapes": ink_ai.read_shapes(reply, offered, inkmod.bounding_box(pen))}
        except Exception as e:
            log.warning(f"[ai_ink] redraw: {e}")
            line = {"error": failure_detail(e, "redrawing failed")}
        yield _ndjson(line)

    return StreamingResponse(keepalive_lines(ndjson(), "ai_ink_shapes"), media_type="application/x-ndjson")


# Sync def: the AI call runs in the threadpool.
@router.post("/ai/ink/text")
def ai_ink_text(payload: InkAiRequest, request: Request):
    """Convert to text: a picture of the pen strokes goes to the model, and
    its transcription comes back as ``{"text": markdown}`` (math as $…$,
    ``[illegible]`` for a word it cannot read; ``""`` when the picture
    holds no writing). 400 for a model that reads text only, 413 past
    what one stroke file holds."""
    user_id = require_user_id(request)
    pen, _ = _pen_ink(payload, TOO_MUCH_TO_CONVERT)
    rt, entry, sees = _model(user_id)
    if not sees:
        raise HTTPException(status_code=400, detail=CANNOT_SEE)
    _check_door(rt, entry, user_id)

    def ndjson():
        try:
            drawn = ink_ai.picture(pen)
            if not drawn:
                raise RuntimeError("the handwriting could not be drawn as a picture")
            reply = _ask(user_id, rt, entry, ink_ai.TEXT_SYSTEM, ink_ai.TEXT_TURN, drawn[0])
            line = {"text": ink_ai.read_text(reply)}
        except Exception as e:
            log.warning(f"[ai_ink] transcription: {e}")
            line = {"error": failure_detail(e, "reading the handwriting failed")}
        yield _ndjson(line)

    return StreamingResponse(keepalive_lines(ndjson(), "ai_ink_text"), media_type="application/x-ndjson")
