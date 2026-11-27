"""The model each task uses: the account's reserved ``ai-models`` pref
({"chat", "metadata", "translate", "ink": a registry id, or "" for the chat
model; "favorites": [ids]; "chat_migrated"}), written only by set_roles and
import_roles (PUT /api/ai/models/roles, the one-time POST
/api/ai/models/import), and how a request settles the model it runs on.

Every model picked on any connection the account may use is offered
everywhere (ai_settings.ai_runtime's registry); each task names the one it
runs on, resolved into ``ai_runtime()["roles"]`` (resolve_roles). A stored
pick is never cleared because its model went away: it is ``missing`` (the
task runs on the chat model and says so) until the user picks again, and
comes back when its connection offers it again. The browser applies the
same rule (frontend/src/ai/aiModels.js resolveRoles, held together by
tests/shared/ai_models.json).
"""

import re

from fastapi import HTTPException

from . import translate_engines
from .ai_usage import SHARED_PREFIX
from .db import get_pref, update_pref

AI_MODELS_PREF_KEY = "ai-models"
ROLES = ("chat", "metadata", "translate", "ink")
MAX_FAVORITES = 50
MAX_PICK_LEN = 300
# An entry's id as ai_settings.new_provider_id makes it (8 URL-safe
# characters): what a registry id's model name follows (a model name may
# hold a colon of its own, Ollama's "qwen2.5:7b").
_ENTRY_PICK = re.compile(r"^[A-Za-z0-9_-]{8}:(.+)$")


def model_prefs(value) -> dict:
    """The ``ai-models`` pref as a clean object: each role's pick (a
    registry id, ``engine:<id>`` for translate, "" = the chat model; for
    chat "" = the first model offered), up to MAX_FAVORITES favorites, and
    ``chat_migrated`` (chat still holds what migration step 71 wrote)."""
    value = value if isinstance(value, dict) else {}

    def pick(v):
        return v if isinstance(v, str) and len(v) <= MAX_PICK_LEN else ""
    favorites = value.get("favorites") if isinstance(value.get("favorites"), list) else []
    return {**{role: pick(value.get(role)) for role in ROLES},
            "favorites": list(dict.fromkeys(f for f in favorites if pick(f)))[:MAX_FAVORITES],
            "chat_migrated": value.get("chat_migrated") is True}


def load_model_prefs(user_id: str) -> dict:
    return model_prefs(get_pref(user_id, AI_MODELS_PREF_KEY)[0] if user_id else None)


def resolve_roles(models: list, prefs: dict, engines: list) -> dict:
    """Each task's model, ``{role: {pick, model, state}}``: ``state`` is
    ``ok`` (the pick is offered), ``follows`` (no pick) or ``missing`` (a
    pick no longer offered: its connection removed, its model unpicked, a
    shared entry that no longer admits the account, an engine no longer
    set up); ``model`` is the pick when ok, else the chat model — for chat
    itself the first model offered — else "". Translation takes a
    configured engine (``engines``: translate_engines.configured) too, and
    with no model offered at all its target is the free engine."""
    offered = {m["id"] for m in models}
    engine_ids = {e["id"] for e in engines}
    first = models[0]["id"] if models else ""

    def resolved(role, fallback):
        pick = prefs.get(role) or ""
        if not pick:
            state = "follows"
        elif pick in offered or (role == "translate" and pick in engine_ids):
            state = "ok"
        else:
            state = "missing"
        model = pick if state == "ok" else fallback
        if role == "translate" and state != "ok" and not models:
            free = translate_engines.MODEL_PREFIX + translate_engines.FREE_ENGINE
            model = free if free in engine_ids else ""
        return {"pick": pick, "model": model, "state": state}
    roles = {"chat": resolved("chat", first)}
    for role in ROLES[1:]:
        roles[role] = resolved(role, roles["chat"]["model"])
    return roles


def pick_label(pick: str) -> str:
    """A pick as a sentence names it: a registry id's model name (a shared
    entry's ``server:<entry>:<name>`` too), an engine's label, anything
    else as it is."""
    engine = translate_engines.engine_of(pick)
    if engine:
        return translate_engines.ENGINES.get(engine, {}).get("label") or pick
    if str(pick or "").startswith(SHARED_PREFIX):
        parts = pick.split(":", 2)
        return parts[2] if len(parts) == 3 else pick
    found = _ENTRY_PICK.match(pick or "")
    return found.group(1) if found else pick


def _offered(rt: dict, role: str, pick: str, engines: list) -> bool:
    return any(m["id"] == pick for m in rt["models"]) or (role == "translate"
                                                         and any(e["id"] == pick for e in engines))


def _roles_view(rt: dict, prefs: dict, engines: list) -> dict:
    return {"roles": resolve_roles(rt["models"], prefs, engines), "favorites": prefs["favorites"],
            "chat_migrated": prefs["chat_migrated"]}


def set_roles(user_id: str, rt: dict, picks: dict, add=(), remove=()) -> dict:
    """Set some tasks' models and add or remove favorites, in one
    read-modify-write of the ``ai-models`` pref. Each pick is "" (the chat
    model) or an id ``rt`` (an ai_runtime) offers (translate: or a
    configured engine), else a 400; a chat pick clears ``chat_migrated``;
    more than MAX_FAVORITES favorites is a 400. Returns ``{roles,
    favorites, chat_migrated}``."""
    engines = translate_engines.configured(user_id)
    for role, pick in picks.items():
        if pick and not _offered(rt, role, pick, engines):
            raise HTTPException(status_code=400, detail=f"{pick_label(pick)} is not offered")
    add = [f for f in add if isinstance(f, str) and f and len(f) <= MAX_PICK_LEN]

    def change(value):
        prefs = model_prefs(value)
        for role, pick in picks.items():
            prefs[role] = pick
            if role == "chat":
                prefs["chat_migrated"] = False
        favorites = [f for f in prefs["favorites"] if f not in set(remove)]
        favorites += [f for f in dict.fromkeys(add) if f not in favorites]
        if len(favorites) > MAX_FAVORITES:
            raise HTTPException(status_code=400, detail=f"at most {MAX_FAVORITES} favorite models")
        prefs["favorites"] = favorites
        return prefs
    return _roles_view(rt, update_pref(user_id, AI_MODELS_PREF_KEY, change), engines)


def import_roles(user_id: str, rt: dict, picks: dict) -> dict:
    """A browser's picks from before they followed the account (its
    localStorage), taken in one read-modify-write: each only while the
    account's own is still unset — chat also while it holds what step 71
    wrote (``chat_migrated``), which taking it clears. A pick ``rt`` does
    not offer is skipped. Returns ``{taken: [role], roles, favorites,
    chat_migrated}``."""
    engines = translate_engines.configured(user_id)
    taken = []

    def change(value):
        prefs = model_prefs(value)
        taken.clear()
        for role in ("chat", "metadata", "translate"):
            pick = picks.get(role) or ""
            if not pick or not _offered(rt, role, pick, engines):
                continue
            if role == "chat" and (not prefs["chat"] or prefs["chat_migrated"]):
                prefs["chat"], prefs["chat_migrated"] = pick, False
            elif role != "chat" and not prefs[role]:
                prefs[role] = pick
            else:
                continue
            taken.append(role)
        return prefs
    prefs = update_pref(user_id, AI_MODELS_PREF_KEY, change)
    return {"taken": list(taken), **_roles_view(rt, prefs, engines)}


class ModelUnavailable(HTTPException):
    """A request named a model no longer offered; such a request is never
    routed to another model. Every route answers it the same way, the app's
    handler (gamma/app.py): a 400 ``{"detail": "<model> is no longer
    offered", "kind": "model_unavailable"}``; a stream's error line carries
    the same ``kind`` (ai_client.failure_kind)."""

    kind = "model_unavailable"

    def __init__(self, requested: str):
        super().__init__(status_code=400, detail=f"{pick_label(requested)} is no longer offered")


def resolve_model(runtime: dict, requested: str, role: str = "chat") -> dict | None:
    """The registry entry a request runs on. A request that names a model
    gets that one: its registry id (``<pid>:<model>``), or a bare model
    name exactly one offered model has; anything else raises
    ModelUnavailable. A request that names none gets its task's model
    (``runtime["roles"][role]``, which is the chat model while the task's
    own pick is unset or missing), None when nothing is offered."""
    models = runtime.get("models") or []
    if requested:
        found = next((m for m in models if m["id"] == requested), None)
        named = [m for m in models if m["model"] == requested]
        if found is None and len(named) == 1:
            found = named[0]
        if found is None:
            raise ModelUnavailable(requested)
        return found
    target = ((runtime.get("roles") or {}).get(role) or {}).get("model") or ""
    return next((m for m in models if m["id"] == target), None) or runtime.get("default")


def routed(runtime: dict, role: str, requested: str, entry) -> dict:
    """What a task's response says of the model it ran on: ``{role, model,
    fallback}``, ``fallback`` when the request named no model and the
    task's own pick is missing, so the chat model stood in."""
    state = ((runtime.get("roles") or {}).get(role) or {}).get("state")
    model = entry.get("id", "") if isinstance(entry, dict) else str(entry or "")
    return {"role": role, "model": model, "fallback": not requested and state == "missing"}
