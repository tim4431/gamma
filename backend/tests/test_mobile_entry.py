"""The mobile apps' server entry (backend/gamma_mobile.py, the iPad's and
Android's): ``run``, which takes the app's environment and turns any failure
into a logged, ordinary error; the supervisor that runs uvicorn inside the
one interpreter an app has; and ``restart``, which the app calls to run the
server again after a failed start or a server that stopped answering
(docs/dev/ipad.md "Starting, and the log").

The supervisor is driven with a stand-in for uvicorn.Server here: ``run``
blocks until ``should_exit`` or fails like a bind that found its port taken
(a SystemExit, as uvicorn raises). ``run`` is driven with ``main`` replaced,
so nothing imports the server.
"""

import importlib.util
import io
import json
import os
import tempfile
import threading
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]


@pytest.fixture
def entry():
    spec = importlib.util.spec_from_file_location("gamma_mobile_under_test", REPO / "backend" / "gamma_mobile.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def environ(monkeypatch):
    """os.environ as a copy for one test, so what ``run`` sets stays in it,
    without the variables the tests look for."""
    copy = {name: value for name, value in os.environ.items()
            if name not in ("GAMMA_UPDATES", "GAMMA_UPDATE_CHECK", "CRYPTOGRAPHY_OPENSSL_NO_LEGACY", "TMPDIR")}
    monkeypatch.setattr(os, "environ", copy)
    return copy


@pytest.fixture
def seen(entry, monkeypatch):
    """``main`` replaced: the environment each time it ran."""
    seen = []
    monkeypatch.setattr(entry, "main", lambda: seen.append(dict(os.environ)))
    return seen


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


def test_run_takes_the_environment_as_a_dict(entry, environ, seen):
    entry.run({"GAMMA_DATA_DIR": "/data", "GAMMA_PORT": 50123})
    assert len(seen) == 1, "main runs once, after the environment is set"
    assert seen[0]["GAMMA_DATA_DIR"] == "/data"
    assert seen[0]["GAMMA_PORT"] == "50123", "every value becomes a string, as os.environ needs"


def test_run_takes_the_environment_as_json(entry, environ, seen):
    # What Android passes (PythonRuntime.kt): one string through Chaquopy.
    entry.run(json.dumps({"GAMMA_DATA_DIR": "/data", "GAMMA_VERSION": "0.4.0"}))
    assert seen[0]["GAMMA_DATA_DIR"] == "/data"
    assert seen[0]["GAMMA_VERSION"] == "0.4.0"


def test_without_an_environment_the_process_one_is_used(entry, environ, seen):
    # The iPad sets the variables in the process and calls run() alone.
    environ["GAMMA_DATA_DIR"] = "/set-before"
    entry.run()
    assert seen[0]["GAMMA_DATA_DIR"] == "/set-before"


def test_the_app_brings_its_updates_unless_the_environment_says_otherwise(entry, environ, seen):
    entry.run({})
    assert seen[0]["GAMMA_UPDATES"] == "app"
    assert seen[0]["GAMMA_UPDATE_CHECK"] == "off"
    assert seen[0]["CRYPTOGRAPHY_OPENSSL_NO_LEGACY"] == "1"
    entry.run({"GAMMA_UPDATES": "store", "GAMMA_UPDATE_CHECK": "on"})
    assert seen[1]["GAMMA_UPDATES"] == "store"
    assert seen[1]["GAMMA_UPDATE_CHECK"] == "on"


def test_tmpdir_is_read_again_once_it_is_set(entry, environ, seen, monkeypatch, tmp_path):
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path / "found-before"))
    entry.run({"TMPDIR": str(tmp_path)})
    assert tempfile.gettempdir() == str(tmp_path)


def test_a_failed_start_is_logged_and_becomes_an_ordinary_error(entry, environ, monkeypatch, tmp_path, capsys):
    def main():
        raise SystemExit(1)  # would end the app's process if it got out

    monkeypatch.setattr(entry, "main", main)
    with pytest.raises(RuntimeError, match="the server exited"):
        entry.run({"GAMMA_DATA_DIR": str(tmp_path)})
    log = (tmp_path / "logs" / "server.log").read_text(encoding="utf-8")
    assert "--- the server did not start ---" in log and "SystemExit" in log
    assert "SystemExit" in capsys.readouterr().err


def test_an_environment_that_is_not_json_is_an_error_too(entry, environ, seen, capsys):
    with pytest.raises(RuntimeError, match="the server exited"):
        entry.run("{not json")
    assert seen == [], "the server never runs on half an environment"
    assert "JSONDecodeError" in capsys.readouterr().err


def test_threads_get_a_larger_stack_on_android_only(entry, monkeypatch):
    sizes = []
    monkeypatch.setattr(entry.threading, "stack_size", sizes.append)
    monkeypatch.setattr(entry, "_on_android", lambda: False)
    entry._thread_stacks()
    assert sizes == [], "the iPad's threads, and a computer's, keep their own"
    monkeypatch.setattr(entry, "_on_android", lambda: True)
    entry._thread_stacks()
    assert sizes == [8 << 20]


def test_wheel_libraries_load_on_android_in_any_order(entry, monkeypatch, tmp_path):
    # Flet's library wheels keep their .so files in <site>/opt/lib; one that
    # needs another loads once the other has, whatever the listing's order.
    lib = tmp_path / "site" / "opt" / "lib"
    lib.mkdir(parents=True)
    for name in ("libtiff.so", "libjpeg.so", "libc++_shared.so", "README"):
        (lib / name).write_text("")
    loaded, needs = [], {"libtiff.so": "libjpeg.so", "libjpeg.so": "libc++_shared.so"}
    def dlopen(path, mode=0):
        name = os.path.basename(path)
        if needs.get(name) and needs[name] not in loaded:
            raise OSError(f"dlopen failed: library {needs[name]!r} not found")
        loaded.append(name)
    import ctypes
    monkeypatch.setattr(ctypes, "CDLL", dlopen)
    monkeypatch.setattr(entry.sys, "path", [str(tmp_path / "missing"), str(tmp_path / "site")])
    monkeypatch.setattr(entry.sys, "meta_path", list(entry.sys.meta_path))
    monkeypatch.setattr(entry, "_on_android", lambda: False)
    entry._load_wheel_libraries()
    assert loaded == []
    monkeypatch.setattr(entry, "_on_android", lambda: True)
    entry._load_wheel_libraries()
    assert loaded == ["libc++_shared.so", "libjpeg.so", "libtiff.so"]


def test_a_wheel_library_that_never_loads_is_left_to_its_import(entry, monkeypatch, tmp_path):
    lib = tmp_path / "opt" / "lib"
    lib.mkdir(parents=True)
    (lib / "libbroken.so").write_text("")
    tries = []
    def dlopen(path, mode=0):
        tries.append(path)
        raise OSError("not an ELF file")
    import ctypes
    monkeypatch.setattr(ctypes, "CDLL", dlopen)
    monkeypatch.setattr(entry.sys, "path", [str(tmp_path)])
    monkeypatch.setattr(entry.sys, "meta_path", list(entry.sys.meta_path))
    monkeypatch.setattr(entry, "_on_android", lambda: True)
    entry._load_wheel_libraries()
    assert len(tries) == 1


def test_an_untagged_extension_is_found_where_nothing_else_finds_it(entry, monkeypatch, tmp_path):
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / f"_ext{entry.UntaggedExtensions.SUFFIX}").write_bytes(b"")
    spec = entry.UntaggedExtensions.find_spec("pkg._ext", [str(pkg)])
    assert spec is not None and spec.origin == str(pkg / f"_ext{entry.UntaggedExtensions.SUFFIX}")
    assert spec.loader.__class__.__name__ == "ExtensionFileLoader"
    assert entry.UntaggedExtensions.find_spec("pkg._other", [str(pkg)]) is None
    monkeypatch.setattr(entry.sys, "meta_path", [])
    monkeypatch.setattr(entry.sys, "path", [])
    monkeypatch.setattr(entry, "_on_android", lambda: True)
    entry._load_wheel_libraries()
    entry._load_wheel_libraries()
    assert entry.sys.meta_path == [entry.UntaggedExtensions], "appended once, consulted last"


def test_this_host_is_not_android(entry):
    assert entry._on_android() is False


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
    # The supervisor writes the log line after the state file: wait for it too.
    assert _settle(lambda: "waiting for a restart" in log.getvalue())
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

    # The server stopped answering: the app asks for a restart. The first
    # server is told to exit, the second one runs on, and the state never
    # says "stopped" meanwhile, so the app does not take it for a failure.
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
