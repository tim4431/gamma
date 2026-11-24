"""The skills sections of Settings → AI → Chat: the account's own skills and how its
built-ins are offered — on or off, and whether the assistant may load one on
its own (gamma/skills.py, docs/dev/skills.md).

Session only: an integration token is refused outright (require_user_id),
as on every account endpoint, so a token's holder never reads the account's
private instructions. A guest lists the built-ins and writes nothing
(require_personal_user_id). Every write is one read-modify-write of the
``skills`` pref; an edit or a delete names the ``updated_at`` it read, and
a stale one answers 409 with the stored skill, checked inside that write.
"""

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from .. import skills
from ..auth import require_personal_user_id, require_user_id

router = APIRouter(prefix="/api/ai/skills", tags=["skills"])

GUESTS_READ_ONLY = "Sign in with a personal account to keep skills."


class SkillFields(BaseModel):
    name: str = ""
    description: str = ""
    argument_hint: str = ""
    body: str = ""
    model: bool = True
    updated_at: str | None = None  # PUT: the version the editor read


class ImportRequest(BaseModel):
    text: str = ""
    replace: bool = False


class BuiltinState(BaseModel):
    """How a built-in is offered. Each is left as it stands when it is not
    sent, so turning one off keeps its ``model`` choice for when it comes
    back."""
    off: bool | None = None
    model: bool | None = None


def _refusal(e: skills.SkillError) -> JSONResponse:
    """A SkillError as its answer: 404, 409 with what the client offers next
    (the clashing skill's id, the stored skill), else 400."""
    if isinstance(e, skills.SkillNotFound):
        return JSONResponse(status_code=404, content={"detail": str(e)})
    if isinstance(e, skills.NameTaken):
        return JSONResponse(status_code=409, content={"detail": str(e), "kind": "name", "id": e.id})
    if isinstance(e, skills.Stale):
        return JSONResponse(status_code=409, content={"detail": str(e), "kind": "changed", "skill": e.skill})
    return JSONResponse(status_code=400, content={"detail": str(e)})


def _fields(payload: SkillFields) -> dict:
    return skills.clean_fields(payload.name, payload.description, payload.argument_hint, payload.body,
                               payload.model)


def _personal(request: Request) -> tuple[str, bool]:
    user_id = require_user_id(request)
    return user_id, not request.state.is_guest


# Sync defs: each reads or writes users.db (the routers README).
@router.get("")
def list_skills(request: Request):
    return {"skills": skills.rows(*_personal(request)), "limits": skills.LIMITS}


@router.get("/{skill_id}")
def read_skill(skill_id: str, request: Request):
    try:
        return skills.row(*_personal(request), skill_id)
    except skills.SkillError as e:
        return _refusal(e)


@router.post("", status_code=201)
def create_skill(payload: SkillFields, request: Request):
    user_id = require_personal_user_id(request, GUESTS_READ_ONLY)
    try:
        return skills.create(user_id, _fields(payload))
    except skills.SkillError as e:
        return _refusal(e)


@router.post("/import")
def import_skill(payload: ImportRequest, request: Request):
    """A SKILL.md's text (the browser reads the file): 201 with the new
    skill, 200 when ``replace`` overwrote the one of its name."""
    user_id = require_personal_user_id(request, GUESTS_READ_ONLY)
    try:
        made, created = skills.import_text(user_id, payload.text, payload.replace)
    except skills.SkillError as e:
        return _refusal(e)
    return JSONResponse(status_code=201 if created else 200, content=made)


@router.put("/builtin:{name}/state")
def set_builtin_state(name: str, payload: BuiltinState, request: Request):
    user_id = require_personal_user_id(request, GUESTS_READ_ONLY)
    try:
        return skills.set_builtin_state(user_id, name, payload.off, payload.model)
    except skills.SkillError as e:
        return _refusal(e)


@router.put("/{skill_id}")
def edit_skill(skill_id: str, payload: SkillFields, request: Request):
    user_id = require_personal_user_id(request, GUESTS_READ_ONLY)
    try:
        return skills.edit(user_id, skill_id, _fields(payload), payload.updated_at)
    except skills.SkillError as e:
        return _refusal(e)


@router.delete("/{skill_id}")
def delete_skill(skill_id: str, request: Request, updated_at: str | None = None):
    user_id = require_personal_user_id(request, GUESTS_READ_ONLY)
    try:
        skills.remove(user_id, skill_id, updated_at)
    except skills.SkillError as e:
        return _refusal(e)
    return {"ok": True}
