"""Search availability, account selection and replay through the real agent loop."""

import json

import pytest

from ai_fixtures import FakeResp, ai_provider, org  # noqa: F401
from gamma import ai_permissions, web_search
from gamma.routers import ai as ai_mod


@pytest.fixture(autouse=True)
def no_server_search_env(monkeypatch):
    for key in ("GAMMA_WEB_SEARCH_PROVIDER", "GAMMA_BRAVE_SEARCH_API_KEY", "GAMMA_SEARXNG_URL"):
        monkeypatch.delenv(key, raising=False)


@pytest.mark.parametrize("provider,available", [("off", False), ("brave", True)])
def test_saved_preference_gates_schema_prompt_execution_and_export(org, ai_provider, monkeypatch, provider, available):
    client, ids = org
    saved = client.put("/api/ai/web-search", json={"provider": provider, "brave_api_key": "private-search-key"})
    assert saved.status_code == 200, saved.text
    seen, requests = {}, []
    def search(query, limit, *, user):
        requests.append((query, user))
        return [{"title": "Lattice paper", "url": "https://lab.example/paper.pdf",
                 "snippet": "Cooling atoms", "provider": "brave"}]
    monkeypatch.setattr(web_search, "search_web", search)
    def model(messages, system, entry, rt, pdf_b64s=None, **kwargs):
        if "tools" not in seen:
            seen.update(tools=kwargs.get("tools"), system=system)
            return FakeResp([
                {"type": "content_block_start", "content_block":
                    {"type": "tool_use", "id": "s1", "name": "search_web"}},
                {"type": "content_block_delta", "delta": {"type": "input_json_delta",
                    "partial_json": json.dumps({"query": "lattice cooling", "user": "another-account",
                                                "provider": "openai", "api_key": "injected"})}},
                {"type": "content_block_stop"},
            ])
        seen["result"] = messages[-1]["content"]
        return FakeResp([{"type": "content_block_delta", "delta": {"type": "text_delta", "text": "Done."}}])
    monkeypatch.setattr(ai_mod, "_open_ai", model)
    response = client.post("/api/ai/chat", json={"prompt": "Find cooling papers", "agent_scope": "folder", "stream": True})
    assert response.status_code == 200, response.text
    names = {s["name"] for s in seen["tools"]}
    assert ("search_web" in names) is available
    assert "search_papers" in names
    assert "private-search-key" not in response.text + seen["system"] + json.dumps(seen["tools"])
    if available:
        assert requests == [("lattice cooling", ids["user"])]
        assert "https://lab.example/paper.pdf" in seen["result"]
        assert "clickable source links" in seen["result"]
    else:
        assert requests == []
        assert "tool not enabled" in seen["result"]
        assert "General web search is unavailable" in seen["system"]
    exported = client.post("/api/ai/chat/context", json={"prompt": "Find cooling papers", "agent_scope": "folder"})
    assert exported.status_code == 200, exported.text
    assert ("search_web" in exported.text) is available
    assert "private-search-key" not in exported.text


def test_web_search_permission_still_gates_a_configured_service(org, ai_provider, monkeypatch):
    client, _ = org
    assert client.put("/api/ai/web-search", json={"provider": "brave", "brave_api_key": "private-search-key"}).status_code == 200
    seen = []
    def model(messages, system, entry, rt, pdf_b64s=None, **kwargs):
        seen.extend(s["name"] for s in kwargs["tools"])
        return FakeResp([{"type": "content_block_delta", "delta": {"type": "text_delta", "text": "Done."}}])
    monkeypatch.setattr(ai_mod, "_open_ai", model)
    response = client.post("/api/ai/chat", json={"prompt": "Read the library", "agent_scope": "folder",
                         "stream": True, "permissions": {"web_search": False}})
    assert response.status_code == 200
    assert "search_web" not in seen and "search_papers" not in seen
    assert "fetch_paper" in seen


@pytest.mark.parametrize("decision", ["allow_once", "deny"])
def test_configured_search_waits_for_the_account_permission(org, ai_provider, monkeypatch, decision):
    client, ids = org
    assert client.put("/api/ai/web-search", json={
        "provider": "brave", "brave_api_key": "private-search-key"}).status_code == 200
    searches, approvals = [], []
    wait = ai_permissions.AccessRun.wait

    def decide(access, approval):
        assert searches == []
        assert approval.permission == "web_search" and approval.tool == "search_web"
        approvals.append(approval.id)
        assert client.post(f"/api/ai/approvals/{approval.id}", json={"decision": decision}).status_code == 200
        return wait(access, approval)

    monkeypatch.setattr(ai_permissions.AccessRun, "wait", decide)
    monkeypatch.setattr(web_search, "search_web", lambda query, limit, *, user:
                        searches.append((query, user)) or [])
    responses = iter([
        FakeResp([
            {"type": "content_block_start", "content_block":
                {"type": "tool_use", "id": "s1", "name": "search_web"}},
            {"type": "content_block_delta", "delta": {"type": "input_json_delta",
                "partial_json": json.dumps({"query": "lattice cooling"})}},
            {"type": "content_block_stop"},
        ]),
        FakeResp([{"type": "content_block_delta", "delta": {"type": "text_delta", "text": "Done."}}]),
    ])
    monkeypatch.setattr(ai_mod, "_open_ai", lambda *args, **kwargs: next(responses))
    response = client.post("/api/ai/chat", json={"prompt": "Find papers", "agent_scope": "folder",
                           "stream": True, "permissions": {"web_search": "ask"}})
    assert response.status_code == 200, response.text
    assert len(approvals) == 1
    assert searches == ([("lattice cooling", ids["user"])] if decision == "allow_once" else [])
    events = [json.loads(line) for line in response.text.splitlines()]
    action = next(event["action"] for event in events if "action" in event)
    assert bool(action.get("error")) == (decision == "deny")
    assert "private-search-key" not in response.text
