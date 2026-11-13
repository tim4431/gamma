"""Stand-ins for Codex CLI and Claude Code (fake_codex.py, fake_claude.py)
and what the local agents' tests share: the argv that starts each, scripting
it, reading its log, and whether a process is still alive."""

import json
import os
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
FAKE_CODEX = str(HERE / "fake_codex.py")
FAKE_CLAUDE = str(HERE / "fake_claude.py")
ARGV = {"codex": [sys.executable, FAKE_CODEX], "claude": [sys.executable, FAKE_CLAUDE]}


def use_fakes(monkeypatch, log: Path | None = None, **script) -> None:
    """Both agents are the fakes (``GAMMA_AGENT_CODEX`` / ``_CLAUDE``) and
    never the real CLIs on this computer (no PATH, no fallbacks), scripted by
    ``script`` (``FAKE_AGENT``), logging to ``log``."""
    from gamma.local_agents import catalog

    monkeypatch.setenv("GAMMA_AGENT_CODEX", json.dumps(ARGV["codex"]))
    monkeypatch.setenv("GAMMA_AGENT_CLAUDE", json.dumps(ARGV["claude"]))
    monkeypatch.setattr(catalog, "FALLBACKS", {name: [] for name in catalog.AGENTS})
    monkeypatch.setattr(catalog.shutil, "which", lambda *args, **kwargs: None)
    script_agent(monkeypatch, log, **script)


def script_agent(monkeypatch, log: Path | None = None, **script) -> None:
    if log is not None:
        script["log"] = str(log)
    monkeypatch.setenv("FAKE_AGENT", json.dumps(script))


def read_log(log: Path) -> list[dict]:
    if not log.exists():
        return []
    return [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines() if line.strip()]


def requests_in(log: Path) -> list[dict]:
    """The messages the fake read, in order."""
    return [entry["in"] for entry in read_log(log) if "in" in entry]


def turns_in(log: Path) -> list[dict]:
    """The log's ``{"argv", "cwd", "env"}`` of each process a turn started,
    in order: Codex's app-server in a folder, Claude Code's ``-p`` with its
    tools; never a probe's (``--version``, the status, the models: Codex's
    app-server in the temporary directory, Claude Code's without tools)."""
    probes = Path(tempfile.gettempdir()).resolve()
    out = []
    for entry in read_log(log):
        argv = entry.get("argv") or []
        if argv == ["app-server"] and Path(entry["cwd"]).resolve() != probes:
            out.append(entry)
        elif "-p" in argv and "--tools" in argv:
            out.append(entry)
    return out


def settle_probes(timeout: float = 30.0) -> None:
    """Wait for the probes ``catalog.cached`` started in the background,
    whose processes a test's end would otherwise find running."""
    from gamma.local_agents import catalog

    end = time.monotonic() + timeout
    while catalog._refreshing and time.monotonic() < end:
        time.sleep(0.05)


if sys.platform == "win32":
    import ctypes
    from ctypes import wintypes

    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    _kernel32.OpenProcess.restype = wintypes.HANDLE
    _kernel32.GetExitCodeProcess.argtypes = (wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD))
    _kernel32.GetExitCodeProcess.restype = wintypes.BOOL
    _kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)

    def alive(pid: int) -> bool:
        handle = _kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return False
        try:
            code = wintypes.DWORD()
            return bool(_kernel32.GetExitCodeProcess(handle, ctypes.byref(code))) and code.value == 259
        finally:
            _kernel32.CloseHandle(handle)
else:
    def alive(pid: int) -> bool:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        try:  # a zombie child of ours counts as gone
            done, _ = os.waitpid(pid, os.WNOHANG)
            return done == 0
        except ChildProcessError:
            return True


def gone(pid: int, timeout: float = 10.0) -> bool:
    """Whether ``pid`` ends within ``timeout`` seconds."""
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if not alive(pid):
            return True
        time.sleep(0.05)
    return not alive(pid)
