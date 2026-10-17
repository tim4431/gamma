"""The iPad's server entry (ipad/python/gamma_ios.py): the supervisor that
runs uvicorn inside the one interpreter the app has, and ``restart``, which
the app calls to run the server again after a failed start or a reclaimed
socket (docs/dev/ipad.md "Starting, and the log").

The supervisor is driven with a stand-in for uvicorn.Server here: ``run``
blocks until ``should_exit`` or fails like a bind that found its port taken
(a SystemExit, as uvicorn raises).
"""

import importlib.util
import io
import json
import threading
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]


@pytest.fixture
def entry():
    spec = importlib.util.spec_from_file_location("gamma_ios_under_test", REPO / "ipad" / "python" / "gamma_ios.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FakeServer:
    """uvicorn.Server as the supervisor sees it."""

    def __init__(self, fail=False):
        self.fail = fail
        self.should_exit = False
        self.started = False
        self.running = threading.Event()
        self.stopped = threading.Event()

    def run(self):
        if self.fail:
            raise SystemExit(1)  # uvicorn: the port is taken
        self.started = True
        self.running.set()
        while not self.should_exit:
            time.sleep(0.005)
        self.stopped.set()


def _state(path):
    # The supervisor replaces the file whole; a read that lands on the
    # replace itself (Windows) is tried again.
    for _attempt in range(20):
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            time.sleep(0.005)
    return json.loads(path.read_text(encoding="utf-8"))


def _settle(check, seconds=5.0):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if check():
            return True
        time.sleep(0.01)
    return False


def test_a_failed_start_is_reported_and_retried_on_restart(entry, tmp_path):
    state = tmp_path / "server.state"
    log = io.StringIO()
    servers = [FakeServer(fail=True), FakeServer()]
    made = []

    def make_server():
        made.append(servers[len(made)])
        return made[-1]

    threading.Thread(target=entry.supervise, args=(make_server, str(state), log), daemon=True).start()

    # The bind failed: the state file says so, and the supervisor waits.
    assert _settle(lambda: state.exists() and _state(state)["state"] == "stopped")
    assert "could not listen" in _state(state)["detail"]
    assert "waiting for a restart" in log.getvalue()
    time.sleep(0.05)
    assert len(made) == 1, "nothing runs again until the app asks"

    # Try again: the state says "starting" at once, and the next server runs.
    entry.restart()
    assert _state(state)["state"] == "starting"
    assert _settle(lambda: len(made) == 2 and servers[1].running.is_set())
    assert "restart" in log.getvalue()


def test_a_restart_of_a_running_server_stops_it_and_runs_a_new_one(entry, tmp_path):
    state = tmp_path / "server.state"
    servers = [FakeServer(), FakeServer()]
    made = []

    def make_server():
        made.append(servers[len(made)])
        return made[-1]

    threading.Thread(target=entry.supervise, args=(make_server, str(state), io.StringIO()), daemon=True).start()
    assert _settle(lambda: servers[0].running.is_set())
    assert _state(state)["state"] == "starting"

    # The socket was reclaimed: the app asks for a restart. The first server
    # is told to exit, the second one runs on, and the state never says
    # "stopped" meanwhile, so the app does not take it for a failure.
    entry.restart()
    assert _settle(lambda: servers[0].stopped.is_set() and servers[1].running.is_set())
    assert servers[0].should_exit
    assert _state(state)["state"] == "starting"


def test_the_state_file_is_replaced_whole(entry, tmp_path):
    state = tmp_path / "server.state"
    assert entry.write_state(str(state), "stopped", "the server stopped")
    assert _state(state) == {"state": "stopped", "detail": "the server stopped", "at": _state(state)["at"]}
    assert not (tmp_path / "server.state.tmp").exists()
    # A path that cannot be written is no reason to stop supervising.
    assert entry.write_state(str(tmp_path / "no-such-dir" / "server.state"), "starting") is False
