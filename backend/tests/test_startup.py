"""Startup stays SDK-free; simultaneous first MCP calls share one transport;
the app lifespan starts the background rounds."""

import os
from pathlib import Path
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor

from fastapi.testclient import TestClient

from conftest import account_of, make_user


def test_normal_startup_does_not_load_mcp_sdk(tmp_path):
    code = """
import sys
from fastapi.testclient import TestClient
from gamma.app import app
with TestClient(app, base_url='http://localhost') as client:
    assert client.get('/api/health').status_code == 200
    assert client.get('/.well-known/oauth-authorization-server').status_code == 200
    assert client.post('/mcp', json={}).status_code == 401
    assert not any(name == 'mcp' or name.startswith('mcp.') for name in sys.modules)
"""
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=Path(__file__).resolve().parents[1],
        env={**os.environ, "GAMMA_DATA_DIR": str(tmp_path)},
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stderr


def test_concurrent_first_mcp_requests_and_lifespan_restart():
    from gamma.app import app
    from gamma.integrations import create_token

    ws = make_user("startup-reader", "pw")
    token = create_token(account_of("startup-reader"), ws, "Startup test", 1)["token"]
    # Re-entering the same app must create a transport for the new event loop.
    for _ in range(2):
        with TestClient(app, base_url="http://localhost") as client:
            def request(index):
                return client.post("/mcp", headers={
                    "Authorization": "Bearer " + token,
                    "Accept": "application/json, text/event-stream",
                    "MCP-Protocol-Version": "2025-11-25",
                }, json={"jsonrpc": "2.0", "id": index, "method": "tools/list"})

            with ThreadPoolExecutor(max_workers=4) as pool:
                responses = list(pool.map(request, range(4)))
            for response in responses:
                assert response.status_code == 200, response.text
                assert "tools" in response.json()["result"]


def test_the_background_rounds_run_at_startup(monkeypatch):
    """The app lifespan runs the grant check, the guest sweeper, the trash
    sweeper and the leftover sweeps (snapshot temp files, deleted
    workspaces' directories) once at startup (then each at its interval)."""
    from gamma import cloud_sync, guests, trash, workspaces, ws_backup
    from gamma.app import app

    ran = []
    monkeypatch.setattr(cloud_sync, "check_all", lambda: ran.append("grant check"))
    monkeypatch.setattr(guests, "delete_expired", lambda: ran.append("guests"))
    monkeypatch.setattr(trash, "sweep", lambda: ran.append("trash"))
    monkeypatch.setattr(ws_backup, "sweep_stale_temp", lambda: ran.append("backup temp"))
    monkeypatch.setattr(workspaces, "remove_leftovers", lambda: ran.append("leftovers"))
    with TestClient(app):
        for _ in range(100):
            if len(ran) == 5:
                break
            time.sleep(0.01)
    assert sorted(ran) == ["backup temp", "grant check", "guests", "leftovers", "trash"]
