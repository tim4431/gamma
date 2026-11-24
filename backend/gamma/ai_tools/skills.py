"""load_skill: the library agent loads one of the user's skills itself
(docs/dev/skills.md), from the names and descriptions ``prompt.agent_system``
lists (``skills.listing``). The tool takes the skill's name and nothing
else, since the conversation is its input, and answers with the skill's
instructions.

Only a skill the user lets the assistant use on its own (``model: true``)
loads, so a skill meant to run only from "/" cannot be loaded by guessing
its name. The catalog is the request's (``scope["skills"]``,
routers/ai.py ``_chat_scope``): the account's own skills in a personal
session, the built-ins otherwise.

The saved action keeps the skill's id and a stub, never the instructions: a
conversation may be a shared workspace's, read by members who must not see
a private skill. Replay (``ai_context.build_messages``) renders the
instructions again from the requester's catalog by that id (``replay``)."""

from .. import skills

# What the saved action (the chip, stored with the conversation) keeps in
# place of the instructions the model got.
SAVED_RESULT = "(the skill's instructions)"
# What a load's result becomes when the agent loop's emergency retry elides
# it with every other result (ai_context.elide_live_results).
ELIDED_RESULT = "(skill instructions elided — call load_skill again if needed)"


def _loadable(catalog, name: str) -> dict | None:
    return next((s for s in catalog or () if s["name"] == name and s.get("model")), None)


def _instructions(skill: dict) -> str:
    """The skill's instructions as the model loads them: no arguments, so
    each $ARGUMENTS reads as nothing (the conversation is the input)."""
    return skills.render(skill, "").strip()


def _run_load_skill(conn, ws, scope, args):
    name = str(args.get("name") or "").strip().lstrip("/")
    skill = _loadable(scope.get("skills"), name)
    if not skill:
        return (f"error: no skill /{name[:skills.MAX_NAME]} to load — load only a skill the system prompt "
                "lists, by its name", None)
    return _instructions(skill), {"kind": "skill", "summary": f"Loaded skill {skill['name']}",
                                  "result": SAVED_RESULT, "skill_id": skill["id"]}


def _replay(catalog, action: dict) -> str:
    """A successful load as a later request replays it: the instructions
    rendered again from the requester's catalog by the saved id, or the
    note a message whose skill is gone reads."""
    skill = skills.find(catalog or [], action.get("skill_id"))
    if skill:
        return _instructions(skill)
    return skills.missing_note(action.get("args") or {})


TOOLS = [
    {
        # ``needs: "skills"``: armed only where the request's catalog holds a
        # skill the assistant may load (registry.available), and never over
        # MCP, whose clients have no such catalog.
        "perm": "skills", "kind": "skill", "scopes": ("folder", "page"), "mutating": False, "needs": "skills",
        "run": _run_load_skill, "replay": _replay, "elided": ELIDED_RESULT,
        "spec": {
            "name": "load_skill",
            # Says what the listing says (skills.LISTING_HEAD): answering
            # directly is the rule, loading a skill the exception, and a skill
            # already loaded is not loaded again.
            "description": (
                "Load one of the user's skills, listed under Skills in the system prompt: its instructions "
                "for one kind of request. Only for a message that plainly asks for the whole piece of work a "
                "skill's description names — answer an ordinary question or a follow-up directly instead, and "
                "do not load a skill whose instructions are already in this conversation. When one does fit, "
                "call it before answering, then follow the instructions it returns. A message that begins "
                "with \"The user ran the skill …\" already carries its skill."),
            "parameters": {
                "type": "object",
                "properties": {"name": {"type": "string", "description": "the skill's name, as listed"}},
                "required": ["name"],
            },
        },
    },
]
