"""The model each task uses (the account's ``ai-models`` pref): every model
picked on any connection is offered to every task; a task's pick is ok,
follows the chat model, or is missing (its connection removed, its model
unpicked, a shared entry that no longer admits the account) and is then
kept and stood in for by the chat model; a request that names a model gets
that model or model_unavailable, never another; and the writes (PUT
/api/ai/models/roles, the one-time import) are single read-modify-writes.
The resolution itself, when a key may be left out and the services a
connection is named by are tests/shared/ai_models.json's, which the client
passes too."""

import json
import threading
from pathlib import Path

import pytest

from ai_fixtures import org  # noqa: F401  (fixture)
from conftest import doc_props, login, make_page, make_user
from gamma import ai_settings
from gamma.db import connect_users_db

SHARED = json.loads((Path(__file__).resolve().parents[2] / "tests" / "shared" / "ai_models.json")
                    .read_text(encoding="utf-8"))


@pytest.mark.parametrize("case", SHARED["roles"]["cases"], ids=[c["note"] for c in SHARED["roles"]["cases"]])
def test_each_task_resolves_as_the_shared_cases_say(case):
    engines = [{"id": e} for e in case["engines"]]
    picks = ai_settings.model_prefs(case["picks"])
    assert ai_settings.resolve_roles(case["models"], picks, engines) == case["roles"]
    if "without" in case:
        left = [m for m in case["models"] if m["provider"] != case["without"]["provider"]]
        assert ai_settings.resolve_roles(left, picks, engines) == case["without"]["roles"]


def test_the_keyless_cases_use_the_server_s_own_choices():
    """The protocols and presets the keyless cases name are the server's,
    as GET /api/ai/settings gives them, so the client's rule reads the same."""
    choices = ai_settings.protocol_choices()
    protocols = {p["id"]: p for p in choices["protocols"]}
    services = {s["id"]: s for s in choices["services"]}
    for p in SHARED["keyless"]["choices"]["protocols"]:
        assert {k: protocols[p["id"]][k] for k in p} == p
    for s in SHARED["keyless"]["choices"]["services"]:
        assert {k: services[s["id"]][k] for k in s} == s


def test_the_service_ids_are_the_server_s():
    """The services the client's logos are held to are every one a
    connection can be named by (service_id): the server's protocols and
    presets, then custom."""
    choices = ai_settings.protocol_choices()
    ids = [p["id"] for p in choices["protocols"]] + [s["id"] for s in choices["services"]] + ["custom"]
    assert SHARED["services"]["ids"] == ids


@pytest.mark.parametrize("case", SHARED["keyless"]["cases"], ids=[c["note"] for c in SHARED["keyless"]["cases"]])
def test_a_key_may_be_left_out_as_the_shared_cases_say(case):
    assert ai_settings.key_optional({"protocol": case["protocol"], "base_url": case["base_url"]}) \
        is case["key_optional"]


def _add(c, models, **fields):
    r = c.post("/api/ai/providers", json={"protocol": "openai", "api_key": "sk-roles-test-1234",
                                          "base_url": fields.pop("base_url", "https://llm.example.org/v1"),
                                          "models": models, **fields})
    assert r.status_code == 200, r.text
    return r.json()["providers"][-1]["id"]


@pytest.fixture
def two(org):
    """Two connections of the module's account, A (a1, a2) and B (b1), and
    no task picked; both go at the end, and so does the ``ai-models`` pref
    (the picks, the favorites, ``chat_migrated``), whatever the test left."""
    c, ids = org
    a, b = _add(c, "a1, a2"), _add(c, "b1")
    yield c, ids, a, b
    for pid in (a, b):
        c.delete(f"/api/ai/providers/{pid}")
    with connect_users_db() as conn:
        conn.execute("DELETE FROM user_prefs WHERE user_id = ? AND key = ?",
                     (ids["user_id"], ai_settings.AI_MODELS_PREF_KEY))


def _roles(c):
    return c.get("/api/ai/models").json()["roles"]


def test_a_task_s_pick_is_ok_follows_or_missing_and_is_kept(two):
    c, ids, a, b = two
    r = c.put("/api/ai/models/roles", json={"metadata": f"{b}:b1", "ink": f"{a}:a2"})
    assert r.status_code == 200, r.text
    roles = r.json()["roles"]
    assert roles["chat"] == {"pick": "", "model": f"{a}:a1", "state": "follows"}  # the first model offered
    assert roles["metadata"] == {"pick": f"{b}:b1", "model": f"{b}:b1", "state": "ok"}
    assert roles["translate"] == {"pick": "", "model": f"{a}:a1", "state": "follows"}
    assert roles["ink"]["state"] == "ok"
    # The connection removed: missing, the pick kept, the chat model stands in.
    c.delete(f"/api/ai/providers/{b}")
    assert _roles(c)["metadata"] == {"pick": f"{b}:b1", "model": f"{a}:a1", "state": "missing"}
    # The model unpicked on its connection: missing too.
    assert c.put(f"/api/ai/providers/{a}", json={"models": "a1"}).status_code == 200
    assert _roles(c)["ink"] == {"pick": f"{a}:a2", "model": f"{a}:a1", "state": "missing"}
    # What names each: its model, and its connection while that still exists.
    label = next(p["label"] for p in c.get("/api/ai/settings").json()["providers"] if p["id"] == a)
    assert c.get("/api/ai/models").json()["missing"] == {f"{b}:b1": {"model": "b1", "connection": ""},
                                                         f"{a}:a2": {"model": "a2", "connection": label}}
    # Picked again on the same entry: it comes back.
    assert c.put(f"/api/ai/providers/{a}", json={"models": "a1, a2"}).status_code == 200
    assert _roles(c)["ink"]["state"] == "ok"
    # A missing pick is told once, by its own notice, fingerprinted by the pick.
    notices = {n["id"]: n for n in c.get("/api/notices").json()["notices"]}
    assert notices["ai-model-metadata"]["fingerprint"] == f"{b}:b1"
    assert notices["ai-model-metadata"]["params"] == {"model": "b1"} and notices["ai-model-metadata"]["pane"] == "ai"
    assert "ai-model-ink" not in notices and "ai-model-chat" not in notices


def test_a_shared_pick_goes_missing_when_its_access_does(two):
    c, ids, a, b = two
    sid = ai_settings.new_server_provider_id()

    def access(who, users=()):
        def change(config):
            config["providers"] = [e for e in config["providers"] if e["id"] != sid] + [
                {"id": sid, "protocol": "openai", "api_key": "sk-lab-123456", "base_url": "https://lab.example.org/v1",
                 "models": "lab-m", "access": {"who": who, "users": list(users), "guests": False}}]
        ai_settings.edit_server_ai(change)
    try:
        access("everyone")
        assert c.put("/api/ai/models/roles", json={"chat": f"{sid}:lab-m"}).status_code == 200
        assert _roles(c)["chat"]["state"] == "ok"
        access("selected")  # nobody listed: the account no longer has it
        models = c.get("/api/ai/models").json()
        assert models["roles"]["chat"] == {"pick": f"{sid}:lab-m", "model": f"{a}:a1", "state": "missing"}
        assert models["roles"]["metadata"]["model"] == f"{a}:a1"  # following the chat model, which stands in
        # Its connection still exists, so the pick is named with it.
        assert models["missing"][f"{sid}:lab-m"]["model"] == "lab-m"
        assert models["missing"][f"{sid}:lab-m"]["connection"]
    finally:
        ai_settings.edit_server_ai(lambda config: config.update(
            providers=[e for e in config["providers"] if e["id"] != sid]))


def test_a_request_that_names_a_model_gets_it_or_model_unavailable(two):
    c, ids, a, b = two
    extra = _add(c, "b1")  # a second b1, on another connection
    rt = ai_settings.ai_runtime(ids["user_id"])
    assert ai_settings.resolve_model(rt, f"{b}:b1", "metadata")["id"] == f"{b}:b1"
    assert ai_settings.resolve_model(rt, "a2", "chat")["id"] == f"{a}:a2"  # a bare name one model has
    for gone in ("b1", f"{b}:gone", "nowhere"):  # two have b1; the others are offered by none
        with pytest.raises(ai_settings.ModelUnavailable):
            ai_settings.resolve_model(rt, gone, "metadata")
    # No model named: the task's model.
    c.put("/api/ai/models/roles", json={"ink": f"{a}:a2"})
    rt = ai_settings.ai_runtime(ids["user_id"])
    assert ai_settings.resolve_model(rt, "", "ink")["id"] == f"{a}:a2"
    assert ai_settings.resolve_model(rt, "", "metadata")["id"] == f"{a}:a1"
    # The chat refuses a model no longer offered with its error card's kind.
    r = c.post("/api/ai/chat", json={"prompt": "hi", "model": f"{b}:gone", "stream": True})
    assert r.status_code == 400 and r.json() == {"detail": "gone is no longer offered", "kind": "model_unavailable"}
    c.delete(f"/api/ai/providers/{extra}")


def test_clip_metadata_runs_on_the_metadata_model(two, monkeypatch):
    """The extension's clip looks the paper up in the background and names
    no model: the lookup's AI calls go through the metadata model's
    connection, and the lookup says when the chat model stood in for one
    no longer offered."""
    from gamma.routers import clip, metadata

    c, ids, a, b = two
    assert c.put("/api/ai/models/roles", json={"metadata": f"{b}:b1"}).status_code == 200
    page = make_page(c, "paper.pdf", properties=doc_props("e" * 24))
    text = "A Study of Things\nX. Author\nWe study things at length in this text."
    monkeypatch.setattr(metadata, "_pdf_excerpt", lambda u, d, limit: (text, None, len(text)))
    monkeypatch.setattr(metadata, "_ensure_indexed", lambda u, d: True)
    monkeypatch.setattr(metadata, "_crossref_search", lambda q, rows=5: [])
    monkeypatch.setattr(metadata, "_book_search", lambda t, a="": [])
    monkeypatch.setattr(metadata, "_fetch_doi", lambda d: (None, ""))
    went = []

    def fake_call(messages, system, entry, rt, **kw):
        went.append(entry["id"])
        return '{"title": "A Study of Things", "authors": ["X. Author"], "kind": "journalArticle"}'
    monkeypatch.setattr(metadata, "_call_ai", fake_call)
    clip._start_metadata(ids["ws"], ids["user_id"], page["id"])
    for thread in threading.enumerate():
        if thread.name == f"clip-meta-{page['id']}":
            thread.join(30)
    assert went and set(went) == {f"{b}:b1"}  # the reading and the slide citation

    c.delete(f"/api/ai/providers/{b}")
    went.clear()
    r = c.post("/api/metadata/fetch", json={"block_id": page["id"], "force": True})
    assert r.status_code == 200, r.text
    assert set(went) == {f"{a}:a1"}
    assert r.json()["routed"] == {"role": "metadata", "model": f"{a}:a1", "fallback": True}


def test_an_account_with_services_only_translates_with_no_model_named(client, monkeypatch):
    make_user("roles_engines", "pw")
    c = login("roles_engines", "pw")
    asked = []
    monkeypatch.setattr("gamma.routers.translate.translate_engines.translate",
                        lambda engine, conf, texts, lang, user_id, form=0: asked.append(engine)
                        or [f"T:{t}" for t in texts])
    r = c.post("/api/ai/translate", json={"texts": ["Hello there."], "lang": "de", "model": ""})
    assert r.status_code == 200, r.text
    assert r.json()["translations"] == ["T:Hello there."] and asked == ["microsoft"]
    assert r.json()["routed"] == {"role": "translate", "model": "engine:microsoft", "fallback": False}


def test_a_pinned_translation_model_that_went_away_is_model_unavailable(two, monkeypatch):
    c, ids, a, b = two
    monkeypatch.setattr("gamma.routers.translate._call_ai", lambda *a, **k: pytest.fail("never another model"))
    r = c.post("/api/ai/translate", json={"texts": ["Hello there."], "lang": "de", "model": f"{b}:gone"})
    assert r.status_code == 400 and r.json() == {"detail": "gone is no longer offered", "kind": "model_unavailable"}


def test_put_roles_checks_each_pick_and_edits_favorites(two, guest):
    from gamma.db import set_pref

    c, ids, a, b = two
    assert c.put("/api/ai/models/roles", json={"metadata": "nope:x"}).status_code == 400
    assert c.put("/api/ai/models/roles", json={"chat": f"{b}:b1", "ink": "engine:microsoft"}).status_code == 400
    assert _roles(c)["chat"]["pick"] == ""  # a refused write writes nothing
    r = c.put("/api/ai/models/roles", json={"translate": "engine:microsoft"})
    assert r.status_code == 200 and r.json()["roles"]["translate"]["state"] == "ok"
    assert c.put("/api/ai/models/roles", json={"translate": "engine:google"}).status_code == 400  # not set up
    # Any chat pick clears what step 71 wrote.
    set_pref(ids["user_id"], "ai-models", {"chat": f"{a}:a1", "chat_migrated": True})
    assert c.get("/api/ai/models").json()["chat_migrated"] is True
    assert c.put("/api/ai/models/roles", json={"chat": f"{a}:a2"}).json()["chat_migrated"] is False
    # Favorites are added and removed, never written whole.
    r = c.put("/api/ai/models/roles", json={"favorites_add": [f"{a}:a1", f"{b}:b1"]})
    assert r.json()["favorites"] == [f"{a}:a1", f"{b}:b1"]
    r = c.put("/api/ai/models/roles", json={"favorites_add": [f"{a}:a2"], "favorites_remove": [f"{a}:a1"]})
    assert r.json()["favorites"] == [f"{b}:b1", f"{a}:a2"]
    many = [f"x{i}:m" for i in range(49)]
    assert c.put("/api/ai/models/roles", json={"favorites_add": many}).status_code == 400
    assert c.get("/api/ai/models").json()["favorites"] == [f"{b}:b1", f"{a}:a2"]
    # A favorite no longer offered is named too; the service translate picked is no registry id.
    c.delete(f"/api/ai/providers/{b}")
    assert c.get("/api/ai/models").json()["missing"] == {f"{b}:b1": {"model": "b1", "connection": ""}}
    # A guest account sets its own; a visitor without an account can't.
    assert guest.put("/api/ai/models/roles", json={"metadata": ""}).status_code == 200
    from conftest import fresh_client
    assert fresh_client().put("/api/ai/models/roles", json={"metadata": ""}).status_code == 401


def test_the_import_takes_only_unset_picks_in_one_write(two):
    from gamma.db import set_pref

    c, ids, a, b = two
    set_pref(ids["user_id"], "ai-models", {"chat": f"{a}:a1", "chat_migrated": True})
    r = c.post("/api/ai/models/import", json={"chat": f"{b}:b1", "metadata": f"{a}:a2", "translate": "gone:x"})
    assert r.status_code == 200, r.text
    assert r.json()["taken"] == ["chat", "metadata"]  # chat over step 71's pick; a pick not offered is skipped
    assert r.json()["roles"]["chat"]["pick"] == f"{b}:b1" and r.json()["chat_migrated"] is False
    # Another browser's import later takes nothing that is set.
    r = c.post("/api/ai/models/import", json={"chat": f"{a}:a2", "metadata": f"{b}:b1", "translate": f"{b}:b1"})
    assert r.json()["taken"] == ["translate"]
    assert r.json()["roles"]["metadata"]["pick"] == f"{a}:a2"
    # Two browsers importing at once: one of them takes the ink-free task.
    c.put("/api/ai/models/roles", json={"metadata": ""})
    rt = ai_settings.ai_runtime(ids["user_id"])
    taken, start = [], threading.Barrier(2)

    def one(pick):
        start.wait()
        taken.append((pick, ai_settings.import_roles(ids["user_id"], rt, {"metadata": pick})["taken"]))
    threads = [threading.Thread(target=one, args=(p,)) for p in (f"{a}:a1", f"{b}:b1")]
    for t in threads:
        t.start()
    for t in threads:
        t.join(10)
    winners = [pick for pick, got in taken if got == ["metadata"]]
    assert len(winners) == 1 and _roles(c)["metadata"]["pick"] == winners[0]


def test_keyless_local_servers_are_offered(org, monkeypatch):
    """A local server's preset and a custom endpoint may be saved without a
    key, are offered like any other, and go without an Authorization
    header; the provider's own endpoint still needs its key."""
    from gamma import ai_catalog
    from gamma.ai_protocols import WIRES

    c, ids = org
    assert c.post("/api/ai/providers", json={"protocol": "openai", "models": "m"}).status_code == 400
    r = c.post("/api/ai/providers", json={"protocol": "openai", "base_url": "http://localhost:11434/v1",
                                          "models": "qwen2.5:7b"})
    assert r.status_code == 200, r.text
    ollama = r.json()["providers"][-1]
    assert ollama["label"] == "Ollama" and ollama["key_hint"] == "" and ollama["billing"] == "local"
    custom = _add(c, "served", api_key="", base_url="http://10.0.0.5:8000/v1")
    models = {m["id"]: m for m in c.get("/api/ai/models").json()["models"]}
    assert models[f"{ollama['id']}:qwen2.5:7b"]["billing"] == "local" and f"{custom}:served" in models
    assert models[f"{custom}:served"]["billing"] == "api" and models[f"{custom}:served"]["price"] is None
    seen = []
    monkeypatch.setattr(ai_catalog, "fetch_json", lambda req: seen.append(req) or {"data": [{"id": "qwen2.5:7b"}]})
    r = c.post("/api/ai/model-catalog", json={"provider_id": ollama["id"]})
    assert r.status_code == 200 and r.json()["models"] == ["qwen2.5:7b"]
    assert r.json()["facts"]["qwen2.5:7b"]["price"] is None  # local: nothing per token
    assert seen[0].full_url == "http://localhost:11434/v1/models" and not seen[0].has_header("Authorization")
    conf = ai_settings.ai_runtime(ids["user_id"])["providers"][ollama["id"]]
    request = WIRES["openai"].request(conf, [{"role": "user", "content": "hi"}], "", "qwen2.5:7b")
    assert not request.has_header("Authorization") and json.loads(request.data)["model"] == "qwen2.5:7b"
    for pid in (ollama["id"], custom):
        c.delete(f"/api/ai/providers/{pid}")
