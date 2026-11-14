"""What the stand-ins share of Gamma's tools (fake_codex.py, fake_claude.py),
standard library only: one ``tools/call`` to Gamma's ``/mcp`` with a bearer
token, as the stateless JSON transport takes it, through no proxy."""

import json
import urllib.error
import urllib.request


def call(url: str, token: str, tool: str, arguments: dict | None = None) -> dict:
    """``{"http", "error", "text"}``: the answer's status, whether the call
    failed (a refusal or a tool's error), the first text it gave back."""
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                       "params": {"name": tool, "arguments": arguments or {}}}).encode()
    request = urllib.request.Request(url, data=body, method="POST", headers={
        "Content-Type": "application/json", "Accept": "application/json, text/event-stream",
        "MCP-Protocol-Version": "2025-11-25", "Authorization": f"Bearer {token}"})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(request, timeout=20) as answer:
            status, found = answer.status, json.loads(answer.read() or b"{}")
    except urllib.error.HTTPError as e:
        return {"http": e.code, "error": True, "text": e.read().decode("utf-8", "replace")[:500]}
    except OSError as e:
        return {"http": 0, "error": True, "text": str(e)}
    result = found.get("result") or {}
    text = next((part.get("text", "") for part in result.get("content") or [] if part.get("type") == "text"), "")
    return {"http": status, "error": bool(result.get("isError")) or "error" in found, "text": text[:2000]}
