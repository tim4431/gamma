"""Startup stays SDK-free; simultaneous first MCP calls share one transport;
the app lifespan starts the background rounds and the walk over the
workspaces behind on their migration steps, which the startup pass leaves
alone."""

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
    # boto3 is imported only for the off-site copies' bucket (gamma/s3.py)
    assert not any(name in ('boto3', 'botocore') or name.startswith(('boto3.', 'botocore.')) for name in sys.modules)
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
    sweeper, the leftover sweeps (snapshot temp files, deleted
    workspaces' directories) and the off-site copies' round once at
    startup (then each at its interval)."""
    from gamma import cloud_sync, guests, offsite, trash, workspaces, ws_backup
    from gamma.app import app

    ran = []
    monkeypatch.setattr(cloud_sync, "check_all", lambda: ran.append("grant check"))
    monkeypatch.setattr(guests, "delete_expired", lambda: ran.append("guests"))
    monkeypatch.setattr(trash, "sweep", lambda: ran.append("trash"))
    monkeypatch.setattr(ws_backup, "sweep_stale_temp", lambda: ran.append("backup temp"))
    monkeypatch.setattr(workspaces, "remove_leftovers", lambda: ran.append("leftovers"))
    monkeypatch.setattr(offsite, "tick", lambda: ran.append("offsite"))
    with TestClient(app):
        for _ in range(100):
            if len(ran) == 6:
                break
            time.sleep(0.01)
    assert sorted(ran) == ["backup temp", "grant check", "guests", "leftovers", "offsite", "trash"]


def test_every_asks_a_callable_for_each_pause():
    """``every`` with a callable for its interval (the off-site copies'
    ``offsite.wait_s``) asks it after each round, off the event loop."""
    import asyncio
    import threading

    from gamma.app import every

    ran, asked = [], []

    def pause():
        asked.append(threading.current_thread() is threading.main_thread())
        return 0.01

    async def main():
        async with every(pause, lambda: ran.append(1), "[test] round failed"):
            for _ in range(500):
                if len(ran) >= 3:
                    break
                await asyncio.sleep(0.01)

    asyncio.run(main())
    assert len(ran) >= 3 and len(asked) >= 2 and not any(asked)


def test_the_workspaces_behind_are_walked_in_the_background_not_at_startup(data_dir, monkeypatch):
    """A workspace behind on its own migration steps is no part of the
    startup pass (``_startup_maintenance``): the lifespan's background walk
    (``migrations.warming``, gamma/migrations.py) upgrades it, or the first
    request that opens it. The walk is told to stop at shutdown."""
    from contextlib import contextmanager

    from gamma import app as app_mod, migrations
    from gamma.seed import create_workspace_files

    walks = []

    @contextmanager
    def warming():
        walks.append("started")
        yield
        walks.append("stopped")

    monkeypatch.setattr(migrations, "warming", warming)
    with TestClient(app_mod.app):
        assert walks == ["started"]
    assert walks == ["started", "stopped"]

    create_workspace_files("suBehind")
    create_workspace_files("suCurrent")
    opened = []
    monkeypatch.setattr(migrations, "is_behind", lambda ws: ws == "suBehind")
    monkeypatch.setattr(app_mod, "connect_pages_db", lambda ws: opened.append(ws) or _Closable())
    monkeypatch.setattr(app_mod, "connect_data_db", lambda ws: opened.append(ws) or _Closable())
    app_mod._startup_maintenance()  # (it seeds the first admin's workspace too)
    assert "suBehind" not in opened and opened.count("suCurrent") == 2  # pages.db and data.db


class _Closable:
    def close(self):
        pass
