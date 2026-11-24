"""Skills (gamma/skills.py, routers/skills.py): the SKILL.md parser, the
built-ins that ship, the Settings routes with their version and
name checks, who may use them, how a skill's instructions are rendered for
the model, and how a message that ran one reads in titles and exports."""

import json
from pathlib import Path

import pytest

from conftest import account_of, fresh_client, login, make_user, token_for
from gamma import skills


def _md(front: str, body: str = "Do the thing.") -> str:
    return f"---\n{front}\n---\n\n{body}\n"


# --- the parser --------------------------------------------------------------

def test_every_shipped_skill_parses():
    shipped = sorted(p.parent.name for p in skills.BUILTIN_DIR.glob("*/SKILL.md"))
    found = skills.builtins()
    assert shipped and [s["name"] for s in found] == shipped
    for skill in found:
        assert skill["id"] == skills.BUILTIN_PREFIX + skill["name"] and skill["source"] == "builtin"
        assert skill["description"] and "$ARGUMENTS" in skill["body"] and skill["model"] is True


def test_crlf_and_block_scalar_descriptions():
    folded = _md("name: fold\ndescription: >\n  One line\n  and the next.\n\n  A new paragraph.").replace("\n", "\r\n")
    skill = skills.parse_skill_md(folded)
    assert skill["description"] == "One line and the next.\nA new paragraph."
    assert skill["body"] == "Do the thing."
    kept = skills.parse_skill_md(_md("name: keep\ndescription: |-\n  First line\n    indented\n  last"))
    assert kept["description"] == "First line\n  indented\nlast"


def test_quotes_comments_and_unknown_keys():
    skill = skills.parse_skill_md(_md(
        'name: quoted # the name\n'
        'description: "Use # signs: \\"quoted\\"" # a comment\n'
        "argument-hint: '[it''s optional]'\n"
        "allowed-tools:\n  - Read\n  - Grep\n"
        "metadata:\n  author: someone\n"
        "license: MIT"))
    assert skill == {"name": "quoted", "description": 'Use # signs: "quoted"', "argument_hint": "[it's optional]",
                     "body": "Do the thing.", "model": True}
    # A plain value keeps a # that no blank precedes, and an unquoted [hint].
    plain = skills.parse_skill_md(_md("name: plain\ndescription: Issue#4 fixes\nargument-hint: [number]"))
    assert plain["description"] == "Issue#4 fixes" and plain["argument_hint"] == "[number]"


@pytest.mark.parametrize("word,model", [("true", False), ("Yes", False), ("on", False), ("1", False),
                                        ("false", True), ("no", True), ("OFF", True), ("0", True)])
def test_disable_model_invocation_booleans(word, model):
    skill = skills.parse_skill_md(_md(f"name: flag\ndescription: d\ndisable-model-invocation: {word}"))
    assert skill["model"] is model


def test_a_missing_description_is_the_body_first_paragraph():
    skill = skills.parse_skill_md(_md("name: bare", "Summarize it\nbriefly.\n\nMore here."))
    assert skill["description"] == "Summarize it briefly."


@pytest.mark.parametrize("text", [
    _md("name: Bad Name\ndescription: d"),
    _md("name: double--hyphen\ndescription: d"),
    _md(f"name: {'a' * 65}\ndescription: d"),
    _md("description: no name"),
    _md("name: x\ndescription: d\ndisable-model-invocation: maybe"),
    _md("name: x\ndescription: d", ""),
    "no front matter at all",
    "---\nname: x\ndescription: d\n",
])
def test_a_bad_skill_md_is_refused(text):
    with pytest.raises(skills.SkillError):
        skills.parse_skill_md(text)


def test_over_64_kb_is_refused():
    with pytest.raises(skills.SkillError, match="64 KB"):
        skills.parse_skill_md(_md("name: big\ndescription: d", "x" * (64 * 1024)))


# --- rendering ---------------------------------------------------------------

def test_render_replaces_arguments_and_keeps_an_escaped_one():
    skill = {"body": "Focus on $ARGUMENTS. Write \\$ARGUMENTS literally; again: $ARGUMENTS"}
    assert skills.render(skill, " methods ") == "Focus on methods. Write $ARGUMENTS literally; again: methods"
    assert skills.render({"body": "Summarize."}, "one paragraph") == "Summarize."


def test_render_cuts_the_arguments_first():
    body = "x" * 15_000 + " $ARGUMENTS $ARGUMENTS"
    text = skills.render({"body": body}, "y" * 30_000)
    assert len(text) <= skills.MAX_RENDERED and text.startswith("x" * 15_000) and "…" in text


def test_turn_text_renders_by_id_and_notes_a_missing_skill():
    catalog = [{"id": "abc", "name": "sum", "body": "Do $ARGUMENTS.", "model": True}]
    message = "> this\n\nthis\n\nSelected: p. 2"
    # By id: the catalog's name, the body rendered with the stored args, then the message whole.
    turn = skills.turn_text({"id": "abc", "name": "old-name", "args": "this"}, message, catalog)
    assert turn.endswith('<skill name="sum">\nDo this.\n</skill>\n\n' + message)
    gone = skills.turn_text({"id": "zzz", "name": "sum", "args": "this"}, "hello", catalog)
    assert "/sum" in gone and "<skill" not in gone and "Do " not in gone and gone.endswith("\n\nhello")


def test_invocation_keeps_strings_and_caps_the_arguments():
    assert skills.invocation(None) is None and skills.invocation({}) is None
    kept = skills.invocation({"id": "abc", "name": " sum ", "args": "y" * (skills.MAX_RENDERED + 5), "x": 1})
    assert kept == {"id": "abc", "name": "sum", "args": "y" * skills.MAX_RENDERED}
    assert skills.invocation({"id": 3, "name": ["sum"]}) == {"id": "", "name": "", "args": ""}
    assert skills.safe_name({"name": "Not A Name"}) == "skill" and skills.safe_name({"name": "sum"}) == "sum"


# The rule the client's skillText and skillTitle (chat/skills.js) follow too.
SKILLTITLES = json.loads((Path(__file__).resolve().parents[2] / "tests" / "shared" / "skilltitles.json")
                         .read_text(encoding="utf-8"))


@pytest.mark.parametrize("case", SKILLTITLES["cases"], ids=lambda case: repr(case["text"])[:40])
def test_display_and_title_follow_the_shared_cases(case):
    assert skills.display(case["skill"], case["text"]) == case["display"]
    assert skills.title(case["skill"], case["text"]) == case["title"]


def test_a_title_is_cut():
    assert len(skills.title({"name": "sum"}, "word " * 50)) == skills.MAX_TITLE


def test_listing_is_sorted_model_only_and_capped():
    assert skills.listing([{"id": "1", "name": "a", "description": "d", "model": False}]) == ""
    many = [{"id": str(i), "name": f"s{i:03d}", "description": "word " * 100, "model": True} for i in range(60)]
    text = skills.listing(list(reversed(many)))
    rows = [line for line in text.split("\n") if line.startswith("- ")]
    assert "load_skill" in text.split("\n")[0]
    assert len(rows) == skills.LISTING_SKILLS and len(text) <= skills.LISTING_CHARS
    assert [r.split(":")[0] for r in rows] == [f"- s{i:03d}" for i in range(50)]
    assert all(r.endswith("…") for r in rows)  # the descriptions were cut, every one alike


# --- the routes --------------------------------------------------------------

@pytest.fixture(scope="module")
def owner():
    make_user("sk_owner", "pw")
    return login("sk_owner", "pw")


@pytest.fixture
def fresh_owner():
    """An account with no skills yet, for the tests that count them."""
    make_user("sk_counter", "pw")
    skills.update(account_of("sk_counter"), lambda v: v.update(skills=[], off=[]))
    return login("sk_counter", "pw")


def _fields(name, **more):
    return {"name": name, "description": f"The {name} skill.", "body": "Do $ARGUMENTS.", **more}


def _listed(client, skill_id):
    return next(r for r in client.get("/api/ai/skills").json()["skills"] if r["id"] == skill_id)


def test_create_read_edit_and_delete(owner):
    r = owner.post("/api/ai/skills", json=_fields("notes", argument_hint="[topic]", model=False))
    assert r.status_code == 201, r.text
    made = r.json()
    assert made["source"] == "account" and made["model"] is False and "body" not in made
    assert _listed(owner, made["id"]) == made
    assert owner.get(f"/api/ai/skills/{made['id']}").json()["body"] == "Do $ARGUMENTS."

    dup = owner.post("/api/ai/skills", json=_fields("notes"))
    assert dup.status_code == 409 and dup.json()["kind"] == "name"

    edited = owner.put(f"/api/ai/skills/{made['id']}",
                       json={**_fields("notes-2"), "updated_at": made["updated_at"]})
    assert edited.status_code == 200, edited.text
    assert edited.json()["name"] == "notes-2" and edited.json()["updated_at"] > made["updated_at"]

    # The editor that read the first version is told, with the stored skill.
    stale = owner.put(f"/api/ai/skills/{made['id']}", json={**_fields("notes-3"), "updated_at": made["updated_at"]})
    assert stale.status_code == 409
    assert stale.json()["kind"] == "changed" and stale.json()["skill"]["name"] == "notes-2"
    assert stale.json()["skill"]["body"] == "Do $ARGUMENTS."
    assert owner.delete(f"/api/ai/skills/{made['id']}", params={"updated_at": made["updated_at"]}).status_code == 409

    gone = owner.delete(f"/api/ai/skills/{made['id']}", params={"updated_at": edited.json()["updated_at"]})
    assert gone.status_code == 200
    assert owner.get(f"/api/ai/skills/{made['id']}").status_code == 404
    assert all(r["id"] != made["id"] for r in owner.get("/api/ai/skills").json()["skills"])


def test_bad_fields_and_built_in_ids_are_refused(owner):
    # The limits the listing gives the editor are the ones a write is held to.
    limits = owner.get("/api/ai/skills").json()["limits"]
    assert owner.post("/api/ai/skills", json=_fields("Not OK")).status_code == 400
    assert owner.post("/api/ai/skills", json=_fields("ok", body="x" * (limits["body"] + 1))).status_code == 400
    assert owner.post("/api/ai/skills", json=_fields("o" * (limits["name"] + 1))).status_code == 400
    assert owner.post("/api/ai/skills", json=_fields("ok", argument_hint="h" * (limits["hint"] + 1))).status_code == 400
    assert owner.post("/api/ai/skills/import", json={
        "text": _md("name: ok\ndescription: d", "x" * limits["import"])}).status_code == 400
    assert owner.post("/api/ai/skills", json=_fields("ok", description="")).status_code == 400
    assert owner.put("/api/ai/skills/builtin:summarize", json=_fields("summarize")).status_code == 400
    assert owner.delete("/api/ai/skills/builtin:summarize").status_code == 400
    assert owner.put("/api/ai/skills/nope", json=_fields("nope")).status_code == 404


def test_import_with_and_without_replace(owner):
    text = _md("name: imported\ndescription: First\ndisable-model-invocation: true", "Body one")
    r = owner.post("/api/ai/skills/import", json={"text": text})
    assert r.status_code == 201, r.text
    first = r.json()
    assert first["model"] is False

    again = _md("name: imported\ndescription: Second", "Body two")
    clash = owner.post("/api/ai/skills/import", json={"text": again})
    assert clash.status_code == 409 and clash.json() == {"detail": clash.json()["detail"], "kind": "name",
                                                         "id": first["id"]}
    replaced = owner.post("/api/ai/skills/import", json={"text": again, "replace": True})
    assert replaced.status_code == 200 and replaced.json()["id"] == first["id"]
    assert replaced.json()["description"] == "Second" and replaced.json()["created_at"] == first["created_at"]
    assert owner.get(f"/api/ai/skills/{first['id']}").json()["body"] == "Body two"
    assert owner.post("/api/ai/skills/import", json={"text": "no front matter"}).status_code == 400


def test_built_in_toggle_and_an_account_skill_replacing_one(owner):
    me = account_of("sk_owner")
    assert _listed(owner, "builtin:review")["effective"] is True
    r = owner.put("/api/ai/skills/builtin:review/state", json={"off": True})
    assert r.status_code == 200 and r.json()["off"] is True and r.json()["effective"] is False
    assert "builtin:review" not in {s["id"] for s in skills.catalog(me, True)}
    owner.put("/api/ai/skills/builtin:review/state", json={"off": False}).raise_for_status()
    assert "builtin:review" in {s["id"] for s in skills.catalog(me, True)}
    assert owner.put("/api/ai/skills/builtin:nope/state", json={"off": True}).status_code == 404

    mine = owner.post("/api/ai/skills", json=_fields("explain")).json()
    row = _listed(owner, "builtin:explain")
    assert row["replaced"] is True and row["effective"] is False
    named = [s for s in skills.catalog(me, True) if s["name"] == "explain"]
    assert [s["id"] for s in named] == [mine["id"]]
    # Without a personal session the catalog is the built-ins alone.
    assert {s["source"] for s in skills.catalog(me, False)} == {"builtin"}
    # Deleting the override brings the built-in back under its own id.
    owner.delete(f"/api/ai/skills/{mine['id']}").raise_for_status()
    assert [s["id"] for s in skills.catalog(me, True) if s["name"] == "explain"] == ["builtin:explain"]


def _model_of(client, name, catalog_for):
    """``(the row's model, the catalog's model)`` for one built-in."""
    return (_listed(client, "builtin:" + name)["model"],
            next(s["model"] for s in skills.catalog(catalog_for, True) if s["name"] == name))


def test_a_built_in_kept_to_a_slash_stays_in_the_catalog_the_assistant_cannot_load_it(owner):
    me = account_of("sk_owner")
    assert _model_of(owner, "explain", me) == (True, True)
    r = owner.put("/api/ai/skills/builtin:explain/state", json={"model": False})
    assert r.status_code == 200 and r.json()["model"] is False
    # Still offered and still runnable from "/": only load_skill loses it.
    assert r.json()["effective"] is True and r.json()["off"] is False
    assert _model_of(owner, "explain", me) == (False, False)
    assert "builtin:explain" in {s["id"] for s in skills.catalog(me, True)}
    # The other built-ins are untouched, and so is "/" for this one.
    assert _model_of(owner, "review", me) == (True, True)

    # Off and on again keeps the choice; each write settles only its own half.
    owner.put("/api/ai/skills/builtin:explain/state", json={"off": True}).raise_for_status()
    assert _listed(owner, "builtin:explain")["model"] is False
    assert owner.put("/api/ai/skills/builtin:explain/state", json={"off": False}).json()["model"] is False
    assert owner.put("/api/ai/skills/builtin:explain/state", json={"model": True}).json()["model"] is True
    assert owner.put("/api/ai/skills/builtin:nope/state", json={"model": False}).status_code == 404


def test_a_guest_reads_the_built_ins_and_writes_nothing():
    guest = fresh_client()
    guest.post("/api/login-guest").raise_for_status()
    listed = guest.get("/api/ai/skills")
    assert listed.status_code == 200
    assert {r["source"] for r in listed.json()["skills"]} == {"builtin"}
    assert guest.get("/api/ai/skills/builtin:summarize").json()["body"]
    for method, path, body in (("POST", "/api/ai/skills", _fields("g")),
                               ("POST", "/api/ai/skills/import", {"text": _md("name: g\ndescription: d")}),
                               ("PUT", "/api/ai/skills/x", _fields("g")),
                               ("DELETE", "/api/ai/skills/x", None),
                               ("PUT", "/api/ai/skills/builtin:summarize/state", {"off": True})):
        assert guest.request(method, path, json=body).status_code == 403, (method, path)


def test_an_integration_token_is_refused(owner):
    bearer = fresh_client()
    bearer.headers["Authorization"] = f"Bearer {token_for(owner, 'write', name='Skills script')}"
    for method, path, body in (("GET", "/api/ai/skills", None),
                               ("GET", "/api/ai/skills/builtin:summarize", None),
                               ("POST", "/api/ai/skills", _fields("t")),
                               ("POST", "/api/ai/skills/import", {"text": _md("name: t\ndescription: d")}),
                               ("PUT", "/api/ai/skills/builtin:summarize/state", {"off": True})):
        assert bearer.request(method, path, json=body).status_code == 403, (method, path)


def test_an_account_keeps_at_most_fifty(fresh_owner):
    skills.update(account_of("sk_counter"), lambda v: v["skills"].extend(
        {"id": f"id{i}", "name": f"s{i}", "description": "d", "argument_hint": "", "body": "b", "model": True,
         "created_at": "", "updated_at": ""} for i in range(skills.MAX_SKILLS)))
    r = fresh_owner.post("/api/ai/skills", json=_fields("one-more"))
    assert r.status_code == 400 and "50" in r.json()["detail"]
    assert fresh_owner.post("/api/ai/skills/import",
                            json={"text": _md("name: one-more\ndescription: d")}).status_code == 400


def test_the_pref_key_is_reserved(owner):
    assert owner.get("/api/prefs/skills").status_code == 400
    assert owner.put("/api/prefs/skills", json={"value": {"skills": []}}).status_code == 400
    assert owner.get("/api/prefs", params={"keys": "skills"}).status_code == 400
