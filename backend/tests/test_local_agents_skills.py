"""Skills on the chat's computer side (gamma/skills.py; routers/local_agents.py
``/chat/turns``, gamma/local_agents/chats.py ``begin`` and ``skill_of``,
home.py's claims, prompt.py ``build_chat``, runner.py ``_ChatTurn``;
docs/dev/skills.md, docs/dev/local_agents.md "The prompts"): a message
that runs a skill of the account's keeps ``{id, name, args}`` on the
person's message, its text the arguments, and titles the conversation
``/name args``; its turn's prompt carries the instructions in a section of
their own before the message, ``$ARGUMENTS`` replaced and their fences
broken; a skill run with no arguments is a message; a skill the account no
longer has is refused (400, kind "skill"), and one deleted before the turn
starts fails it rather than run without it; a fresh session's earlier
messages show it as ``/name args``; a message with a skill never steers.
Against the stand-in Codex (tests/fake_agents/) on the sample paper as a
work folder of this computer (test_local_agents_runner's fixtures). The far
side (the claim's instructions, a computer that does not announce skills)
is test_computer_chats.py's and test_remote_chat.py's."""

import json
from types import SimpleNamespace

import pytest

from conftest import account_of, login, make_user
from fake_agents import requests_in
from gamma import skills, workspaces
from gamma.local_agents import chats, home, runner
from gamma.routers import local_agents as routes
from test_local_agents_runner import _folder, disk  # noqa: F401: the disk fixture

OWNER = "lsk_owner"
BODY = "Tighten $ARGUMENTS, keeping every claim.\n\n```latex\n\\section{Method}\n```\n\nNever touch \\$ARGUMENTS."


@pytest.fixture(scope="module")
def lab(client):
    make_user(OWNER, f"pw-{OWNER}")
    owner = login(OWNER, f"pw-{OWNER}")
    ws = workspaces.create("Agents skills", account_of(OWNER), kind="shared")["id"]
    owner.headers["X-Gamma-Workspace"] = ws
    return SimpleNamespace(ws=ws, owner=owner, user=account_of(OWNER))


@pytest.fixture
def skill(lab):
    """The account's skill /tighten, whose body takes its arguments, and
    /check-refs, whose body does not; gone after the test."""
    made = {name: skills.create(lab.user, skills.clean_fields(name, f"The {name} skill.", "", body, True))
            for name, body in (("tighten", BODY), ("check-refs", "Check that every reference resolves."))}
    yield made
    skills.update(lab.user, lambda value: value["skills"].clear())


def _say(who, bucket, text="", status=202, **body) -> dict:
    r = who.post("/api/local-agents/chat/turns", json={"bucket": bucket, "text": text, **body})
    assert r.status_code == status, r.text
    return r.json()


def _ran(made: dict, args: str = "", name: str = "") -> dict:
    return {"id": made["id"], "name": name or made["name"], "args": args}


def _messages(lab, bucket) -> list[dict]:
    r = lab.owner.get("/api/local-agents/chat", params={"bucket": bucket})
    assert r.status_code == 200, r.text
    return r.json()["conversation"]["messages"]


def _prompts(disk) -> list[str]:
    return [m["params"]["input"][0]["text"] for m in requests_in(disk.log) if m.get("method") == "turn/start"]


def _section(prompt: str, name: str) -> str:
    """The prompt's section of the skill ``name``, up to the message."""
    assert prompt.index(f"## The skill /{name}\n") < prompt.index("## The message\n")
    return prompt.split(f"## The skill /{name}\n", 1)[1].split("## The message\n", 1)[0]


def _message(prompt: str) -> str:
    return json.loads(prompt.split("```json\n", 1)[1].split("\n```", 1)[0])["message"]


def test_a_skill_turn_keeps_the_skill_and_its_prompt_carries_the_instructions(lab, disk, skill):
    f = _folder(lab, disk)
    tighten, refs = skill["tighten"], skill["check-refs"]
    # the name the chat read may be older: the catalog's is kept, found by the id
    made = _say(lab.owner, f.page, "the methods section", skill=_ran(tighten, "the methods section", "old-name"))
    person = made["conversation"]["messages"][0]
    assert (person["text"], person["skill"]) == ("the methods section", _ran(tighten, "the methods section"))
    assert made["conversation"]["title"] == "/tighten the methods section"
    assert runner.wait_idle(60)
    assert _messages(lab, f.page)[1]["state"] == "done"
    [prompt] = _prompts(disk)
    section = _section(prompt, "tighten")
    assert OWNER in section.split("```markdown\n", 1)[0]                  # whose request the instructions are
    assert "Tighten the methods section, keeping every claim." in section   # $ARGUMENTS is the arguments
    assert "Never touch $ARGUMENTS." in section                              # \$ARGUMENTS is the word
    assert "``​`latex" in section and section.count("```") == 2        # its own fence cannot close the block
    assert _message(prompt) == "the methods section"

    # no arguments is a message too; a body that names none reads them in the message alone
    _say(lab.owner, f.page, "  ", skill=_ran(refs))
    assert runner.wait_idle(60)
    _say(lab.owner, f.page, "Mind the appendix.", skill=_ran(refs, "Mind the appendix."))
    assert runner.wait_idle(60)
    first, second = _prompts(disk)[1:]
    assert "Check that every reference resolves.\n```" in _section(first, "check-refs") and _message(first) == ""
    assert "Mind the appendix." not in _section(second, "check-refs") and _message(second) == "Mind the appendix."
    assert [m["state"] for m in _messages(lab, f.page) if m["role"] == "agent"] == ["done"] * 3


def test_a_skill_the_account_no_longer_has_is_refused_and_one_deleted_before_the_start_fails(
        lab, disk, skill, monkeypatch):
    f = _folder(lab, disk)
    r = lab.owner.post("/api/local-agents/chat/turns",
                       json={"bucket": f.page, "text": "x", "skill": {"id": "noSuchSkill", "name": "gone-one"}})
    assert (r.status_code, r.json()["kind"]) == (400, "skill") and "/gone-one" in r.json()["detail"]
    assert _say(lab.owner, f.page, " ", status=400)["detail"] == "Write a message first."

    queue = runner.queue_chat

    def deleted_meanwhile(*args, **kwargs):
        skills.remove(lab.user, skill["tighten"]["id"], None)
        return queue(*args, **kwargs)

    monkeypatch.setattr(runner, "queue_chat", deleted_meanwhile)
    _say(lab.owner, f.page, "the abstract", skill=_ran(skill["tighten"], "the abstract"))
    assert runner.wait_idle(60)
    reply = _messages(lab, f.page)[-1]
    assert (reply["state"], reply["error"]) == ("failed", chats.SKILL_GONE.format(name="tighten"))
    assert _prompts(disk) == []                                              # the agent never ran


def test_a_message_with_a_skill_never_steers(lab, skill):
    r = lab.owner.post("/api/local-agents/chat/turns/someTurn1/steer",
                       json={"id": "steer1", "text": "and the title", "skill": _ran(skill["tighten"], "and the title")})
    assert (r.status_code, r.json()["detail"]) == (409, routes.SKILL_STEERS)


def test_the_earlier_messages_show_a_skill_message_as_its_command():
    messages = [{"role": "user", "text": "the methods", "skill": {"id": "a", "name": "tighten", "args": "the methods"}},
                {"role": "agent", "text": "Tightened."},
                {"role": "user", "text": "", "skill": {"id": "b", "name": "check-refs", "args": ""}},
                {"role": "agent", "text": "All resolve."},
                {"role": "user", "text": "Now the title."}]
    assert chats.earlier_of(messages, 4, messages[4]) == [
        {"role": "owner", "text": "/tighten the methods"}, {"role": "agent", "text": "Tightened."},
        {"role": "owner", "text": "/check-refs"}, {"role": "agent", "text": "All resolve."}]


def test_a_far_claims_skill_is_kept_in_shape():
    assert home._skill(None) is None
    assert home._skill({"name": "tighten", "instructions": "Do it."}) == {"name": "tighten", "instructions": "Do it."}
    # a name no skill has reads as none in the heading; instructions of another shape fail the turn
    assert home._skill({"name": "../## x", "instructions": "Do it."})["name"] == "skill"
    assert home._skill({"name": "tighten", "instructions": 3}) == {"name": "tighten", "instructions": None}
    long = home._skill({"name": "tighten", "instructions": "x" * (skills.MAX_RENDERED + 10)})
    assert len(long["instructions"]) == skills.MAX_RENDERED
