"""Running an agent's CLI (gamma/local_agents/procs.py): the child's
environment, its pipes, no console window, the Job object (Windows) or the
process group (POSIX) that takes its whole tree down, run_quick's timeout
and stop_all. Every test leaves no process behind."""

import json
import os
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest

from fake_agents import alive, gone
from gamma.local_agents import procs

WINDOWS = sys.platform == "win32"
BACKEND = str(Path(__file__).resolve().parents[1])

# A child that starts a grandchild (sleeping), prints both pids, then sleeps.
PARENT = textwrap.dedent("""
    import subprocess, sys, time
    grandchild = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(600)"])
    print(f"{__import__('os').getpid()} {grandchild.pid}", flush=True)
    time.sleep(600)
""")


@pytest.fixture(autouse=True)
def _nothing_left():
    yield
    left = list(procs._live)
    procs.stop_all(timeout=1)
    assert not left, f"children left running: {[c.proc.pid for c in left]}"


def _pids(child) -> tuple[int, int]:
    line = None
    end = time.monotonic() + 30
    while line is None and time.monotonic() < end:
        line = child.line(0.5)
    assert line, "the child printed no pids"
    parent, grandchild = map(int, line.split())
    return parent, grandchild


def test_child_env_drops_the_server_and_the_parent_session(monkeypatch):
    session = ("CLAUDECODE", "CLAUDE_PID", "CLAUDE_EFFORT", "CLAUDE_AGENT_SDK_VERSION", "CLAUDE_CODE_ENTRYPOINT",
               "CLAUDE_CODE_SESSION_ID", "CLAUDE_CODE_MESSAGING_SOCKET", "CLAUDE_CODE_MESSAGING_TOKEN",
               "CLAUDE_CODE_CHILD_SESSION", "CODEX_THREAD_ID")
    for name in ("GAMMA_SHELL_SECRET", "GAMMA_DATA_DIR", "CODEX_SANDBOX_NETWORK_DISABLED", "CODEX_MANAGED_BY_NPM",
                 *session):
        monkeypatch.setenv(name, "x")
    # the user's own configuration, CLAUDE_CODE_* settings among it, stays
    kept = ("CODEX_HOME", "ANTHROPIC_API_KEY", "OPENAI_API_KEY", "PATH_EXTRA_FOR_TEST", "CLAUDE_CODE_USE_BEDROCK",
            "CLAUDE_CODE_OAUTH_TOKEN", "CLAUDE_CODE_GIT_BASH_PATH")
    for name in kept:
        monkeypatch.setenv(name, "kept")
    env = procs.child_env()
    assert not [name for name in env if name.upper().startswith(("GAMMA_", "CODEX_SANDBOX", "CODEX_MANAGED_BY_"))]
    assert not set(session) & set(env)
    assert all(env[name] == "kept" for name in kept)
    assert env["NO_COLOR"] == "1"


def test_child_env_restores_the_library_path_of_a_frozen_linux_build(monkeypatch):
    monkeypatch.setattr(procs.sys, "platform", "linux")
    monkeypatch.setattr(procs.sys, "frozen", True, raising=False)
    monkeypatch.setenv("LD_LIBRARY_PATH", "/tmp/_MEI123")
    monkeypatch.setenv("LD_LIBRARY_PATH_ORIG", "/usr/lib")
    assert procs.child_env()["LD_LIBRARY_PATH"] == "/usr/lib"
    monkeypatch.delenv("LD_LIBRARY_PATH_ORIG")
    assert "LD_LIBRARY_PATH" not in procs.child_env()


def test_a_child_talks_over_its_pipes(tmp_path, monkeypatch):
    monkeypatch.setenv("PYTHONIOENCODING", "utf-8")  # the child writes UTF-8, as the CLIs do
    code = ("import sys\nfor line in sys.stdin:\n    print(line.strip().upper(), flush=True)\n"
            "print('bye é', file=sys.stderr, flush=True)\n")
    child = procs.spawn([sys.executable, "-c", code], cwd=str(tmp_path))
    try:
        assert child.line(0.05) is None  # nothing yet
        assert child.send("hello\n")
        assert child.line(30) == "HELLO"
        child.close_stdin()
        assert child.wait(30) == 0
        with pytest.raises(EOFError):
            child.line(5)
        with pytest.raises(EOFError):  # and on every call after
            child.line(0)
        assert child.stderr_tail(wait=5).strip() == "bye é"
        assert not child.send("late\n")
    finally:
        procs.kill_tree(child)
    procs.kill_tree(child)  # idempotent


def test_the_stderr_tail_keeps_the_last_8_kb(tmp_path):
    code = "import sys\nfor i in range(2000):\n    sys.stderr.write(f'line {i:05d}\\n')\n"
    child = procs.spawn([sys.executable, "-c", code], cwd=str(tmp_path))
    try:
        assert child.wait(30) == 0
        tail = child.stderr_tail(wait=5)
        assert len(tail) <= procs.STDERR_TAIL and tail.endswith("line 01999\n")
    finally:
        procs.kill_tree(child)


def test_kill_tree_takes_the_grandchild_too(tmp_path):
    child = procs.spawn([sys.executable, "-c", PARENT], cwd=str(tmp_path))
    parent, grandchild = _pids(child)
    assert alive(parent) and alive(grandchild)
    procs.kill_tree(child, grace=1)
    assert gone(parent) and gone(grandchild)
    assert child not in procs._live
    procs.kill_tree(child)  # idempotent, never raises


@pytest.mark.skipif(not WINDOWS, reason="the Job object and console windows are Windows'")
def test_a_windows_child_has_no_console_window_and_runs_in_a_job(tmp_path):
    import ctypes
    from ctypes import wintypes

    code = "import ctypes; print(ctypes.windll.kernel32.GetConsoleWindow() or 0, flush=True)"
    child = procs.spawn([sys.executable, "-c", code], cwd=str(tmp_path))
    try:
        assert child._job, "the child is in no Job object"
        in_job = wintypes.BOOL()
        assert ctypes.windll.kernel32.IsProcessInJob(wintypes.HANDLE(int(child.proc._handle)),
                                                     wintypes.HANDLE(child._job), ctypes.byref(in_job))
        assert in_job.value
        assert child.line(30) == "0"  # CREATE_NO_WINDOW: no console window
    finally:
        procs.kill_tree(child)
    code, out = procs.run_quick([sys.executable, "-c", code], timeout=30)
    assert (code, out.strip()) == (0, "0")


@pytest.mark.skipif(not (WINDOWS or sys.platform.startswith("linux")),
                    reason="only Windows' Job object and Linux's PDEATHSIG end a child with its server")
def test_the_tree_dies_with_the_server_however_it_ends(tmp_path):
    """A server that is killed outright (os._exit, no atexit) still takes
    its agent and the agent's own child with it."""
    server = textwrap.dedent(f"""
        import os, sys, time
        sys.path.insert(0, {BACKEND!r})
        from gamma.local_agents import procs
        child = procs.spawn([sys.executable, "-c", {PARENT!r}], cwd={str(tmp_path)!r})
        line = None
        while line is None:
            line = child.line(1)
        print(line, flush=True)
        os._exit(0)
    """)
    env = {**os.environ, "GAMMA_DATA_DIR": str(tmp_path / "data"), "PYTHONIOENCODING": "utf-8"}
    out = subprocess.run([sys.executable, "-c", server], capture_output=True, text=True, encoding="utf-8",
                         env=env, timeout=60)
    assert out.returncode == 0, out.stderr
    parent, grandchild = map(int, out.stdout.split())
    assert gone(parent)
    if WINDOWS:  # the Job holds the whole tree; Linux's signal reaches the agent alone
        assert gone(grandchild)
    else:
        os.kill(grandchild, 9)


def test_run_quick_answers_and_never_raises(tmp_path):
    code = "import sys; sys.stdout.buffer.write('é ok\\n'.encode()); sys.stderr.write('err'); sys.exit(3)"
    assert procs.run_quick([sys.executable, "-c", code], timeout=30) == (3, "é ok\nerr")
    code, out = procs.run_quick([str(tmp_path / "no-such-program")], timeout=5)
    assert code == -1 and out
    code, out = procs.run_quick([sys.executable, "-c", "import sys; print(sys.stdin.read() == '')"], timeout=30)
    assert out.strip() == "True"  # stdin is empty, never the server's


def test_run_quick_times_out_and_kills_the_tree(tmp_path):
    marker = tmp_path / "pids"
    code = textwrap.dedent(f"""
        import subprocess, sys, time, os
        grandchild = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(600)"],
                                      stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        open({str(marker)!r}, "w").write(f"{{os.getpid()}} {{grandchild.pid}}")
        time.sleep(600)
    """)
    started = time.monotonic()
    assert procs.run_quick([sys.executable, "-c", code], timeout=3) == (-1, "timed out")
    assert time.monotonic() - started < 20
    parent, grandchild = map(int, marker.read_text().split())
    assert gone(parent) and gone(grandchild)


def test_stop_all_ends_gracefully_then_kills(tmp_path):
    polite = procs.spawn([sys.executable, "-c", "import sys; sys.stdin.read()"], cwd=str(tmp_path))
    stubborn = procs.spawn([sys.executable, "-c", PARENT], cwd=str(tmp_path))
    parent, grandchild = _pids(stubborn)
    started = time.monotonic()
    procs.stop_all(timeout=1)
    assert time.monotonic() - started < 15
    assert polite.proc.returncode == 0  # it saw its stdin end and exited by itself
    assert gone(parent) and gone(grandchild)
    assert not procs._live


def test_spawn_raises_when_it_cannot_start(tmp_path):
    with pytest.raises(OSError):
        procs.spawn([str(tmp_path / "no-such-program")], cwd=str(tmp_path))
    assert not procs._live
    json.dumps(procs.child_env())  # plain strings
