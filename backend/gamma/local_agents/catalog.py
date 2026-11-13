"""The agents Gamma can start on this computer, where each one is, and
whether it is ready: signed in and, for Codex on Windows, its sandbox set
up.

Finding one (``command``): ``GAMMA_AGENT_CODEX`` / ``GAMMA_AGENT_CLAUDE``
(a JSON list, an argv prefix, or a path) first, then the PATH, then the
places the installers use (``FALLBACKS``: an app opened from the Dock or
the Start menu has no login shell's PATH). An npm shim (a Windows ``.cmd``)
is started as ``node <the package's script>``, never through ``cmd /c``,
which parses the arguments again.

Asking (``probe``) runs ``--version`` and the status command, which use no
model; the answer is kept ``PROBE_TTL_S``. A read path (a listing, the
feedback dialog's poll) uses ``cached``, which never runs a process: a
stale or missing answer starts one probe in the background.
"""

import json
import os
import shutil
import sys
import threading
import time
from pathlib import Path
from typing import NamedTuple

from ..logbuf import log
from . import procs


class Agent(NamedTuple):
    name: str          # "codex" | "claude"
    label: str         # "Codex" | "Claude Code"
    exe: str           # "codex" | "claude"
    status: tuple      # the status command: ("login", "status") | ("auth", "status")
    npm_script: str    # the package's script under an npm shim's node_modules


AGENTS: dict[str, Agent] = {
    "codex": Agent("codex", "Codex", "codex", ("login", "status"), "@openai/codex/bin/codex.js"),
    "claude": Agent("claude", "Claude Code", "claude", ("auth", "status"), "@anthropic-ai/claude-code/cli.js"),
}
PROBE_TTL_S = 600
PROBE_TIMEOUT_S = 15

NOT_SIGNED_IN = {
    "codex": "Codex is not signed in on this computer: run `codex login` in a terminal.",
    "claude": "Claude Code is not signed in on this computer: run `claude` in a terminal and sign in.",
}


def _fallbacks(exe: str) -> list[str]:
    home = Path.home()
    if sys.platform == "win32":
        local = Path(os.environ.get("LOCALAPPDATA") or home / "AppData" / "Local")
        profile = Path(os.environ.get("USERPROFILE") or home)
        return {"codex": [str(local / "Programs" / "OpenAI" / "Codex" / "bin" / "codex.exe")],
                "claude": [str(profile / ".local" / "bin" / "claude.exe")]}[exe]
    return [str(home / ".local" / "bin" / exe), f"/opt/homebrew/bin/{exe}", f"/usr/local/bin/{exe}",
            str(home / ".npm-global" / "bin" / exe)]


FALLBACKS: dict[str, list[str]] = {name: _fallbacks(agent.exe) for name, agent in AGENTS.items()}

_cache: dict[str, dict] = {}
_locks = {name: threading.Lock() for name in AGENTS}
_refreshing: set[str] = set()
_refreshing_lock = threading.Lock()


def label(name: str) -> str:
    agent = AGENTS.get(name)
    return agent.label if agent else name


def _runnable(path: str) -> bool:
    return os.path.isfile(path) and (sys.platform == "win32" or os.access(path, os.X_OK))


def _argv(agent: Agent, path: str) -> tuple[list[str] | None, str]:
    """The argv prefix that starts ``path``: a Windows npm shim becomes
    ``node <script>``; ``(None, problem)`` when that script is missing."""
    if sys.platform == "win32" and path.lower().endswith((".cmd", ".bat")):
        shim = Path(path).parent
        script = shim / "node_modules" / Path(agent.npm_script)
        node = shim / "node.exe"
        node_path = str(node) if node.is_file() else shutil.which("node")
        if not (script.is_file() and node_path):
            return None, (f"{agent.label} is installed through npm in a way Gamma cannot start: "
                          f"install the standalone {agent.label}.")
        return [node_path, str(script)], ""
    return [path], ""


def _resolve(name: str) -> tuple[list[str] | None, str]:
    """``(argv prefix, "")``, or ``(None, problem)`` when the agent cannot
    be started here."""
    agent = AGENTS.get(name)
    if agent is None:
        return None, f"Gamma knows no agent named {name}."
    missing = f"{agent.label} is not on this computer."
    override = os.environ.get(f"GAMMA_AGENT_{name.upper()}", "").strip()
    if override:
        if override.startswith("["):
            try:
                argv = json.loads(override)
            except ValueError:
                argv = None
            if isinstance(argv, list) and argv and all(isinstance(part, str) and part for part in argv):
                return argv, ""
            log.warning(f"[agents] GAMMA_AGENT_{name.upper()} is not a JSON list of strings")
            return None, missing
        return _argv(agent, override) if _runnable(override) else (None, missing)
    found = shutil.which(agent.exe)
    if found:
        return _argv(agent, found)
    for path in FALLBACKS.get(name) or []:
        if _runnable(path):
            return _argv(agent, path)
    return None, missing


def command(name: str) -> list[str] | None:
    """The argv prefix that starts the agent ``name``, None when it is not
    on this computer (or cannot be started: ``probe`` says why)."""
    return _resolve(name)[0]


def _answer(name: str, *, found, version: str = "", ready, problem: str = "", at: float | None = None) -> dict:
    return {"name": name, "label": label(name), "found": found, "version": version, "ready": ready,
            "problem": problem, "at": time.monotonic() if at is None else at}


def _signed_in(agent: Agent, code: int, output: str) -> bool:
    text = output.lower()
    if agent.name == "codex":
        return code == 0 and "logged in" in text and "not logged in" not in text
    start = output.find("{")
    if start >= 0:
        try:
            status, _ = json.JSONDecoder().raw_decode(output[start:])
            if isinstance(status, dict) and "loggedIn" in status:
                return status["loggedIn"] is True
        except ValueError:
            pass
    return code == 0 and "login method" in text


def _probe(agent: Agent) -> dict:
    argv, problem = _resolve(agent.name)
    if argv is None:
        return _answer(agent.name, found=False, ready=False, problem=problem)
    code, output = procs.run_quick(argv + ["--version"], timeout=PROBE_TIMEOUT_S)
    if code != 0:
        return _answer(agent.name, found=True, ready=False,
                       problem=f"{agent.label} did not answer `{agent.exe} --version`.")
    version = next((line.strip() for line in output.splitlines() if line.strip()), "")
    code, output = procs.run_quick(argv + list(agent.status), timeout=PROBE_TIMEOUT_S)
    if not _signed_in(agent, code, output):
        return _answer(agent.name, found=True, version=version, ready=False, problem=NOT_SIGNED_IN[agent.name])
    if agent.name == "codex":
        from . import codex

        ready, problem = codex.sandbox_ready(argv)
        if not ready:
            return _answer(agent.name, found=True, version=version, ready=False, problem=problem)
    return _answer(agent.name, found=True, version=version, ready=True)


def probe(name: str, *, fresh: bool = False) -> dict:
    """``{name, label, found, version, ready, problem, at}`` of the agent
    ``name``, asked anew when ``fresh`` or older than ``PROBE_TTL_S``. It
    runs processes: for a write and a run, never a read path. Callers at the
    same time share one probe. Never raises."""
    if name not in AGENTS:
        return _answer(name, found=False, ready=False, problem=f"Gamma knows no agent named {name}.")
    asked = time.monotonic()
    with _locks[name]:
        hit = _cache.get(name)
        # One that finished after this call began answers it too, fresh or not.
        if hit and (hit["at"] >= asked or (not fresh and asked - hit["at"] < PROBE_TTL_S)):
            return dict(hit)
        try:
            answer = _probe(AGENTS[name])
        except Exception as e:  # noqa: BLE001 — never raises
            log.warning(f"[agents] probing {name}: {e}")
            answer = _answer(name, found=False, ready=False, problem=f"{label(name)} is not on this computer.")
        _cache[name] = answer
        return dict(answer)


def _refresh(name: str) -> None:
    try:
        probe(name)
    finally:
        with _refreshing_lock:
            _refreshing.discard(name)


def cached(name: str) -> dict:
    """The last probe of ``name`` without running anything; one missing or
    stale starts a single probe in the background and the stale answer (or
    ``found: None, ready: None``: still checking) is given meanwhile."""
    hit = _cache.get(name)
    if (hit is None or time.monotonic() - hit["at"] >= PROBE_TTL_S) and name in AGENTS:
        with _refreshing_lock:
            begin = name not in _refreshing
            _refreshing.add(name)
        if begin:
            threading.Thread(target=_refresh, args=(name,), name=f"agent-probe-{name}", daemon=True).start()
    if hit is not None:
        return dict(hit)
    return _answer(name, found=None, ready=None, at=0)


def probes(*, fresh: bool = False) -> list[dict]:
    return [probe(name, fresh=fresh) for name in AGENTS]


def forget() -> None:
    """Drop every kept probe (tests)."""
    _cache.clear()
