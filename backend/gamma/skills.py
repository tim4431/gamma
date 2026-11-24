"""Skills: named instructions for one kind of chat request, loaded with
"/name" at the start of a message or by the library agent's load_skill tool
(docs/dev/skills.md).

A skill is an Agent Skills ``SKILL.md`` (agentskills.io): YAML front matter
with ``name`` and ``description``, plus Claude Code's ``argument-hint`` and
``disable-model-invocation``, then a Markdown body. Two sources make the
catalog a chat sees:

- the built-ins, ``builtin_skills/<name>/SKILL.md`` beside this module,
  read once;
- the account's own, in users.db ``user_prefs`` under the account-wide
  reserved key ``skills`` (/api/prefs refuses it; routers/skills.py is the
  only way in) as ``{"skills": [{id, name, description, argument_hint, body,
  model, created_at, updated_at}], "off": [built-in names turned off],
  "manual": [built-in names kept to "/"]}``.

``model`` (an account skill's field, a built-in's ``manual``) is the same
choice for both sources: whether the assistant may load the skill itself, or
it runs only from "/".

An account skill of a built-in's name replaces it. Messages store only
``{id, name, args}``; every request renders the instructions again from the
requester's catalog by id, so a conversation never holds a skill's body, a
member's private skill never reaches another member of a shared workspace,
and a deleted override does not fall back to the built-in (the ids differ).
"""

import re
import secrets
from functools import cache
from pathlib import Path

from .db import get_pref, page_now, stamp_after, update_pref
from .logbuf import log
from .markdown_import import parse_frontmatter

NAME_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
MAX_NAME = 64
MAX_SKILLS = 50
MAX_DESCRIPTION = 1024
MAX_HINT = 80
MAX_BODY = 20_000
MAX_IMPORT = 64 * 1024
MAX_RENDERED = 40_000
LISTING_SKILLS = 50
LISTING_CHARS = 8000
MAX_TITLE = 80
PREF_KEY = "skills"
BUILTIN_PREFIX = "builtin:"
MAX_ID = len(BUILTIN_PREFIX) + MAX_NAME
# What Settings' editor and import check before they send (GET
# /api/ai/skills), so the client holds no copy of its own.
LIMITS = {"name": MAX_NAME, "description": MAX_DESCRIPTION, "hint": MAX_HINT, "body": MAX_BODY,
          "import": MAX_IMPORT, "skills": MAX_SKILLS}

BUILTIN_DIR = Path(__file__).resolve().with_name("builtin_skills")

# What the listing says above the names. Most messages fit no skill, so the
# rule it states is to answer directly, loading one the exception. Put the
# other way round ("when a request fits one, call load_skill"), at the end of
# the system prompt, a model read every message as something to match and
# loaded a skill for each one (docs/dev/skills.md "Loading one is the
# exception").
LISTING_HEAD = ("Skills: instructions the user keeps for a few kinds of request. Most messages are not one of "
                "them — answer an ordinary question, a follow-up, or anything you can answer yourself, "
                "without a skill. Load one only when the message plainly asks for the whole piece of work "
                "its description names: call load_skill with that name before answering, then follow what it "
                "returns. A loaded skill's instructions stay in the conversation, so do not load the same "
                "skill again while you can still see them. A message that begins with “The user ran the "
                "skill …” already carries its skill.")


class SkillError(ValueError):
    """A refusal whose message is shown to the person as it is (400)."""


class SkillNotFound(SkillError):
    """No skill of that id in the account (404)."""


class NameTaken(SkillError):
    """Another of the account's skills has the name (409, kind "name");
    ``id`` is that skill's, so an import can offer to replace it."""

    def __init__(self, name: str, skill_id: str):
        super().__init__(f"You already have a skill named /{name}.")
        self.id = skill_id


class SkillGone(SkillError):
    """The skill a message runs is not in the requester's catalog (deleted,
    or turned off, since the chat read it): 400 ``{detail, kind: "skill"}``,
    which the chat answers by reading its catalog again and putting the
    message back as typed. A rename keeps the id, so it is never this."""

    def __init__(self, invocation: dict):
        super().__init__(f"The skill /{safe_name(invocation)} was deleted or turned off.")


class Stale(SkillError):
    """The skill changed since the client read it (409, kind "changed");
    ``skill`` is the stored one, body included, for "Load the newer
    version"."""

    def __init__(self, skill: dict):
        super().__init__(f"/{skill.get('name', '')} was changed elsewhere.")
        self.skill = skill


# --- SKILL.md ---------------------------------------------------------------

_TRUE = {"true", "yes", "on", "1"}
_FALSE = {"false", "no", "off", "0"}


def _text(value) -> str:
    """A front matter value as the text a skill's field takes: an unquoted
    ``[number]`` hint, which parse_frontmatter reads as a flow list, as it
    was written."""
    return f"[{', '.join(value)}]" if isinstance(value, list) else value


def _flag(value: str, key: str) -> bool:
    word = value.strip().lower()
    if word in _TRUE:
        return True
    if word in _FALSE:
        return False
    raise SkillError(f"The front matter's {key} should be true or false, not “{value[:40]}”.")


def clean_fields(name, description, argument_hint, body, model) -> dict:
    """The editable fields of a skill, checked as the editor checks them:
    ``{name, description, argument_hint, body, model}``, else SkillError."""
    name = str(name or "").strip()
    description = str(description or "").strip()
    argument_hint = " ".join(str(argument_hint or "").split())
    body = str(body or "").strip()
    if not name:
        raise SkillError("Give the skill a name.")
    if len(name) > MAX_NAME or not NAME_RE.match(name):
        raise SkillError(f"A skill's name is lowercase letters, digits and single hyphens, "
                         f"{MAX_NAME} characters at most: “{name[:MAX_NAME]}” is not.")
    if not description:
        raise SkillError("Give the skill a description: it says when the skill is for.")
    if len(description) > MAX_DESCRIPTION:
        raise SkillError(f"The description is longer than {MAX_DESCRIPTION:,} characters.")
    if len(argument_hint) > MAX_HINT:
        raise SkillError(f"The argument hint is longer than {MAX_HINT} characters.")
    if not body:
        raise SkillError("Write the skill's instructions.")
    if len(body) > MAX_BODY:
        raise SkillError(f"The instructions are longer than {MAX_BODY:,} characters.")
    return {"name": name, "description": description, "argument_hint": argument_hint,
            "body": body, "model": bool(model)}


def parse_skill_md(text: str) -> dict:
    """A SKILL.md's ``{name, description, argument_hint, body, model}``, else
    SkillError. The front matter is read as markdown_import reads a note's,
    with block scalars and comments (``blocks``); keys it doesn't know
    (allowed-tools, metadata, license, ...) are ignored, as Claude Code
    ignores them. With no description, the body's first paragraph is it,
    as in Claude Code."""
    if len(str(text).encode("utf-8")) > MAX_IMPORT:
        raise SkillError(f"A SKILL.md is {MAX_IMPORT // 1024} KB at most.")
    text = str(text).lstrip("\ufeff").replace("\r\n", "\n").replace("\r", "\n")
    meta, body = parse_frontmatter(text, blocks=True)
    if not meta and body == text:
        raise SkillError("A SKILL.md starts with front matter between --- lines that names the skill.")
    meta = {key: _text(value) for key, value in meta.items()}
    body = body.strip()
    description = meta.get("description", "")
    if not description.strip():
        description = " ".join(body.split("\n\n", 1)[0].split())[:MAX_DESCRIPTION]
    hidden = meta.get("disable-model-invocation", "")
    return clean_fields(meta.get("name", ""), description, meta.get("argument-hint", ""), body,
                        not (_flag(hidden, "disable-model-invocation") if hidden.strip() else False))


# --- the two sources ---------------------------------------------------------

@cache
def _builtins() -> tuple:
    found = []
    for path in sorted(BUILTIN_DIR.glob("*/SKILL.md")):
        try:
            skill = parse_skill_md(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, SkillError) as e:
            log.warning(f"[skills] built-in {path.parent.name} skipped: {e}")
            continue
        if skill["name"] != path.parent.name:
            log.warning(f"[skills] built-in {path.parent.name} skipped: it names itself {skill['name']}")
            continue
        found.append({"id": BUILTIN_PREFIX + skill["name"], **skill, "source": "builtin"})
    return tuple(found)


def builtins() -> list[dict]:
    """The built-in skills, read from disk once per process (a catalog is
    built on every chat request): ``{id: "builtin:<name>", name,
    description, argument_hint, body, model, source: "builtin"}``."""
    return [dict(s) for s in _builtins()]


def _names(value, key: str) -> list[str]:
    """One of the stored lists of built-in names (``off``, ``manual``),
    sorted and without duplicates; [] for anything else, so a copy written
    before the key existed reads as none."""
    names = value.get(key) if isinstance(value.get(key), list) else []
    return sorted({n for n in names if isinstance(n, str)})


def _clean(value) -> dict:
    """The stored value as ``{"skills": [...], "off": [...], "manual":
    [...]}``, whatever an older or damaged copy holds."""
    value = value if isinstance(value, dict) else {}
    entries = value.get("skills") if isinstance(value.get("skills"), list) else []
    return {"skills": [e for e in entries if isinstance(e, dict) and isinstance(e.get("id"), str)
                       and isinstance(e.get("name"), str) and isinstance(e.get("body"), str)],
            "off": _names(value, "off"), "manual": _names(value, "manual")}


def load(user_id: str) -> dict:
    """The account's stored skills, ``{"skills": [...], "off": [...],
    "manual": [...]}``."""
    return _clean(get_pref(user_id, PREF_KEY)[0])


def _nothing_stored() -> dict:
    """The stored value of an account that keeps no skills: what a guest, a
    share link or an integration token is read as."""
    return _clean({})


def update(user_id: str, change) -> dict:
    """Read-modify-write the account's skills in one transaction
    (db.update_pref): ``change(value)`` edits the cleaned value in place and
    may raise to abort with nothing written, so a version check made in it
    sees the stored copy. Returns the saved value."""
    def apply(value):
        value = _clean(value)
        change(value)
        return value
    return update_pref(user_id, PREF_KEY, apply)


def _account_skill(entry: dict) -> dict:
    return {"id": entry["id"], "name": entry["name"], "description": entry.get("description") or "",
            "argument_hint": entry.get("argument_hint") or "", "body": entry["body"],
            "model": entry.get("model") is not False, "source": "account",
            "created_at": entry.get("created_at") or "", "updated_at": entry.get("updated_at") or ""}


def _builtin(skill: dict, manual: set) -> dict:
    """A built-in as the account has it: ``model`` false where the account
    keeps it to "/" (``manual``), as an account skill's own ``model`` false
    does. A built-in that ships ``disable-model-invocation`` stays that way
    whatever the account says."""
    return {**skill, "model": skill["model"] and skill["name"] not in manual}


def catalog(user_id: str | None, personal: bool) -> list[dict]:
    """The skills a request may use, sorted by name: the account's own when
    ``personal`` (a signed-in session that is no guest, share link or
    integration token), then the built-ins it has not turned off or
    replaced with a skill of the same name, each with the account's choice
    of whether the assistant may load it. Otherwise the built-ins alone, so
    a token's request or a context export never reads private
    instructions."""
    stored = load(user_id) if personal and user_id else _nothing_stored()
    own = [_account_skill(e) for e in stored["skills"]]
    taken = {s["name"] for s in own} | set(stored["off"])
    manual = set(stored["manual"])
    built = [_builtin(s, manual) for s in builtins() if s["name"] not in taken]
    return sorted(own + built, key=lambda s: s["name"])


def find(catalog: list[dict], skill_id) -> dict | None:
    """The catalog's skill of ``skill_id``: by id only, never by name."""
    return next((s for s in catalog if s["id"] == skill_id), None) if skill_id else None


# --- what the model reads ----------------------------------------------------

def invocation(raw) -> dict | None:
    """The skill a request's message runs, as it is kept: ``{id, name,
    args}``, each a string, ``args`` what followed "/name", trimmed and at
    most MAX_RENDERED characters (no rendering holds more); None for
    none."""
    if not isinstance(raw, dict) or not raw:
        return None

    def field(key, limit):
        value = raw.get(key)
        return value.strip()[:limit] if isinstance(value, str) else ""
    return {"id": field("id", MAX_ID), "name": field("name", MAX_NAME), "args": field("args", MAX_RENDERED)}


def safe_name(invocation: dict | None) -> str:
    """The skill's name as a text shown to the person or the model may
    carry it: a name a skill could have, else "skill"."""
    name = (invocation or {}).get("name")
    return name if isinstance(name, str) and len(name) <= MAX_NAME and NAME_RE.match(name) else "skill"


_PLACEHOLDER = re.compile(r"\\?\$ARGUMENTS")


def _expand(body: str, args: str, room: int) -> str:
    """``body`` with each ``$ARGUMENTS`` replaced by ``args`` (``\\$ARGUMENTS``
    is the literal word), the arguments cut first so the result fits
    ``room`` characters."""
    uses = sum(1 for m in _PLACEHOLDER.finditer(body) if not m.group().startswith("\\"))
    if uses:
        bare = len(_PLACEHOLDER.sub(lambda m: "$ARGUMENTS" if m.group().startswith("\\") else "", body))
        fit = max(0, (room - bare) // uses)
        if len(args) > fit:
            args = args[:max(0, fit - 1)] + "…" if fit else ""
    return _PLACEHOLDER.sub(lambda m: "$ARGUMENTS" if m.group().startswith("\\") else args, body)[:room]


def render(skill: dict, args: str) -> str:
    """The skill's instructions for this run: its body with every
    ``$ARGUMENTS`` (not ``\\$ARGUMENTS``) replaced by ``args``, at most
    MAX_RENDERED characters, the arguments cut first. A body without the
    placeholder is as written: the message after it carries the
    arguments."""
    return _expand(skill.get("body") or "", str(args or "").strip(), MAX_RENDERED)


def missing_note(invocation: dict) -> str:
    """What a turn whose skill the requester's catalog does not hold reads
    in place of the instructions."""
    return (f"[This message used the skill /{safe_name(invocation)}, which is not available here; "
            f"its instructions are left out.]")


def turn_text(invocation: dict, text: str, catalog: list[dict]) -> str:
    """A user turn that ran a skill as the model reads it: a line saying so,
    the instructions in a ``<skill>`` block (``$ARGUMENTS`` as the stored
    ``args``, so the turn reads the same when it is replayed), then
    ``text``, the message as sent (the quotes, its arguments, what it
    points at), whole, as a computer agent's prompt has it. A skill the
    catalog does not hold by id gives missing_note() before the text."""
    text = text or ""
    skill = find(catalog, (invocation or {}).get("id"))
    if not skill:
        head = missing_note(invocation)
    else:
        frame = (f"The user ran the skill /{skill['name']} with this message. Follow its instructions.\n\n"
                 f"<skill name=\"{skill['name']}\">\n{{}}\n</skill>")
        args = str((invocation or {}).get("args") or "").strip()
        head = frame.format(_expand(skill["body"], args, MAX_RENDERED - len(frame) + 2))
    return f"{head}\n\n{text}" if text.strip() else head


def listing(catalog: list[dict]) -> str:
    """The agent prompt's block naming the skills the model may load
    (``model: true``), sorted by name, one ``- name: description`` line
    each; at most LISTING_SKILLS of them in LISTING_CHARS characters, the
    descriptions cut first (Codex's rule). "" when there is none."""
    skills = sorted((s for s in catalog if s.get("model")), key=lambda s: s["name"])[:LISTING_SKILLS]
    if not skills:
        return ""
    descriptions = [" ".join((s.get("description") or "").split()) for s in skills]

    def line(name, description, cut):
        if len(description) > cut:
            description = description[:max(0, cut - 1)] + "…" if cut else ""
        return f"- {name}: {description}" if description else f"- {name}"

    def lines(cut):
        return [line(s["name"], d, cut) for s, d in zip(skills, descriptions)]

    def size(kept):
        return len(LISTING_HEAD) + sum(len(r) + 1 for r in kept) + 1

    longest = max(len(d) for d in descriptions)
    kept = lines(longest)
    if size(kept) > LISTING_CHARS:
        # The longest cut that fits, each description cut to it.
        low, high = 0, longest
        while low < high:
            mid = (low + high + 1) // 2
            if size(lines(mid)) <= LISTING_CHARS:
                low = mid
            else:
                high = mid - 1
        kept = lines(low)
        while kept and size(kept) > LISTING_CHARS:  # the names alone are too many
            kept.pop()
    return "\n".join([LISTING_HEAD, "", *kept])


def display(invocation: dict | None, text: str) -> str:
    """A message's text as Copy message, an export and an agent's earlier
    messages show it: ``/name``, then a space and the text, or a blank line
    and the text when it opens with a quoted passage; the text alone
    without a skill. tests/shared/skilltitles.json holds the rule, which
    the client's skillText (chat/skills.js) follows too."""
    text = text or ""
    if not invocation or not invocation.get("name"):
        return text
    rest = text.strip()
    if not rest:
        return f"/{invocation['name']}"
    gap = "\n\n" if rest.startswith(">") else " "
    return f"/{invocation['name']}{gap}{rest}"


def title(invocation: dict | None, text: str) -> str:
    """A conversation's title when the person never gave one: the message's
    first non-blank line that is not a quote, after ``/name`` when it ran a
    skill, its blanks folded and cut to MAX_TITLE characters
    (tests/shared/skilltitles.json, the client's skillTitle)."""
    line = next((line for line in (text or "").splitlines()
                 if line.strip() and not line.lstrip().startswith(">")), "")
    if invocation and invocation.get("name"):
        line = f"/{invocation['name']} {line}"
    return " ".join(line.split())[:MAX_TITLE]


# --- Settings → AI → Chat, the skills sections (routers/skills.py) -----------

def _row(skill: dict, body: bool = False) -> dict:
    row = {k: skill.get(k) for k in ("id", "name", "description", "argument_hint", "model", "source")}
    if skill.get("source") == "account":
        row.update(effective=True, created_at=skill.get("created_at") or "",
                   updated_at=skill.get("updated_at") or "")
    if body:
        row["body"] = skill.get("body") or ""
    return row


def rows(user_id: str, personal: bool) -> list[dict]:
    """What GET /api/ai/skills lists, without bodies: every account skill
    (``personal`` only) and every built-in, with whether each takes part in
    the catalog (``effective``), whether the assistant may load it
    (``model``, the account's choice for a built-in too) and, for a built-in,
    whether it is turned ``off`` or ``replaced`` by an account skill of its
    name. Sorted by name, an account skill before the built-in it
    replaces."""
    stored = load(user_id) if personal else _nothing_stored()
    own = [_row(_account_skill(e)) for e in stored["skills"]]
    names = {r["name"] for r in own}
    manual = set(stored["manual"])
    out = list(own)
    for skill in builtins():
        off, replaced = skill["name"] in stored["off"], skill["name"] in names
        out.append({**_row(_builtin(skill, manual)), "effective": not off and not replaced,
                    "off": off, "replaced": replaced})
    return sorted(out, key=lambda r: (r["name"], r["source"] != "account"))


def row(user_id: str, personal: bool, skill_id: str) -> dict:
    """One skill with its body (GET /api/ai/skills/{id}), else SkillNotFound."""
    if skill_id.startswith(BUILTIN_PREFIX):
        skill = find(builtins(), skill_id)
        if skill:
            listed = next(r for r in rows(user_id, personal) if r["id"] == skill_id)
            return {**listed, "body": skill["body"]}
    elif personal:
        entry = next((e for e in load(user_id)["skills"] if e["id"] == skill_id), None)
        if entry:
            return _row(_account_skill(entry), body=True)
    raise SkillNotFound("There is no such skill.")


def _entry(value: dict, skill_id: str) -> dict:
    entry = next((e for e in value["skills"] if e["id"] == skill_id), None)
    if not entry:
        raise SkillNotFound("There is no such skill.")
    return entry


def _check_name(value: dict, name: str, keep: str = ""):
    clash = next((e for e in value["skills"] if e["name"] == name and e["id"] != keep), None)
    if clash:
        raise NameTaken(name, clash["id"])


def _check_version(entry: dict, seen: str | None):
    if seen is not None and seen != (entry.get("updated_at") or ""):
        raise Stale(_row(_account_skill(entry), body=True))


def _add(value: dict, fields: dict) -> dict:
    if len(value["skills"]) >= MAX_SKILLS:
        raise SkillError(f"An account keeps {MAX_SKILLS} skills at most: delete one first.")
    now = page_now()
    entry = {"id": secrets.token_urlsafe(9), **fields, "created_at": now, "updated_at": now}
    value["skills"].append(entry)
    return entry


def _set(entry: dict, fields: dict):
    entry.update(fields, updated_at=stamp_after(entry.get("updated_at") or ""))


def create(user_id: str, fields: dict) -> dict:
    """Add a skill (``clean_fields``' dict); NameTaken when the account has
    one of that name. Returns its row."""
    made = {}

    def change(value):
        _check_name(value, fields["name"])
        made.update(_add(value, fields))
    update(user_id, change)
    return _row(_account_skill(made))


def import_text(user_id: str, text: str, replace: bool = False) -> tuple[dict, bool]:
    """Add the skill a SKILL.md describes; ``(row, created)``. A name the
    account has already is NameTaken, unless ``replace``: then that skill is
    overwritten in place and keeps its id."""
    fields = parse_skill_md(text)
    done = {}

    def change(value):
        clash = next((e for e in value["skills"] if e["name"] == fields["name"]), None)
        if clash and not replace:
            raise NameTaken(fields["name"], clash["id"])
        if clash:
            _set(clash, fields)
            done.update(clash, created=False)
        else:
            done.update(_add(value, fields), created=True)
    update(user_id, change)
    created = done.pop("created")
    return _row(_account_skill(done)), created


def edit(user_id: str, skill_id: str, fields: dict, seen: str | None) -> dict:
    """Change an account skill; Stale when ``seen`` (the ``updated_at`` the
    editor read) is not the stored version, checked inside the write."""
    if skill_id.startswith(BUILTIN_PREFIX):
        raise SkillError("A built-in skill cannot be edited: copy it to your skills instead.")
    done = {}

    def change(value):
        entry = _entry(value, skill_id)
        _check_version(entry, seen)
        _check_name(value, fields["name"], keep=skill_id)
        _set(entry, fields)
        done.update(entry)
    update(user_id, change)
    return _row(_account_skill(done))


def remove(user_id: str, skill_id: str, seen: str | None):
    """Delete an account skill; Stale as for edit(). Its id is never given
    out again (token_urlsafe), so a message that ran it reads the
    unavailable note from now on."""
    if skill_id.startswith(BUILTIN_PREFIX):
        raise SkillError("A built-in skill cannot be deleted: turn it off instead.")

    def change(value):
        entry = _entry(value, skill_id)
        _check_version(entry, seen)
        value["skills"].remove(entry)
    update(user_id, change)


def _listed(names: list, name: str, present: bool) -> list:
    rest = set(names) - {name}
    return sorted((rest | {name}) if present else rest)


def set_builtin_state(user_id: str, name: str, off: bool | None = None, model: bool | None = None) -> dict:
    """How a built-in is offered to the account, the two choices an account
    skill has as well: ``off`` whether it is left out of the catalog
    altogether (and so out of "/"), ``model`` whether the assistant may load
    it on its own. Either left out stays as it stands, so turning a built-in
    off and on again keeps the choice the account made for it. Its row."""
    if not find(builtins(), BUILTIN_PREFIX + name):
        raise SkillNotFound("There is no such built-in skill.")

    def change(value):
        if off is not None:
            value["off"] = _listed(value["off"], name, off)
        if model is not None:
            value["manual"] = _listed(value["manual"], name, not model)
    update(user_id, change)
    return next(r for r in rows(user_id, True) if r["id"] == BUILTIN_PREFIX + name)
