"""The agents Gamma can start on this computer, where each one is, and
whether it is ready: signed in and, for Codex on Windows, its sandbox set
up.

Finding one (``command``): ``GAMMA_AGENT_CODEX`` / ``GAMMA_AGENT_CLAUDE``
(a JSON list, an argv prefix, or a path) first, then the PATH, then the
places the installers use (``FALLBACKS``: an app opened from the Dock or
the Start menu has no login shell's PATH). An npm shim (a Windows ``.cmd``)
is started as ``node <the package's script>``, never through ``cmd /c``,
which parses the arguments again.

Asking (``probe``) runs ``--version`` and the status command, then asks a
ready agent which models it offers (Codex's ``model/list`` on the
short-lived app-server that also checks its Windows sandbox, Claude Code's
``initialize``), none of which uses a model; the answer is kept
``PROBE_TTL_S``, and so is the list of models apart, since a run's start
asks only whether the agent is ready (``_listed``). A read path (a listing,
the feedback dialog's poll) uses ``cached``, which never runs a process: a
stale or missing answer, or models a ready agent has not listed within
``PROBE_TTL_S``, starts one probe in the background.
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
_listed: dict[str, float] = {}  # when a ready agent last listed its models (time.monotonic)
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


def _answer(name: str, *, found, version: str = "", ready, problem: str = "", at: float | None = None,
            models: list[dict] | None = None) -> dict:
    return {"name": name, "label": label(name), "found": found, "version": version, "ready": ready,
            "problem": problem, "models": list(models or []), "at": time.monotonic() if at is None else at}


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


def _probe(agent: Agent, models: bool = True) -> dict:
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
    offered: list[dict] = []
    if agent.name == "codex":
        from . import codex

        if models or codex.CHECK_SANDBOX:
            ready, problem, offered = codex.inspect(argv, models=models)
            if not ready:
                return _answer(agent.name, found=True, version=version, ready=False, problem=problem)
    elif models:
        from . import claude

        offered = claude.models(argv)
    return _answer(agent.name, found=True, version=version, ready=True, models=offered)


def refused_choice(name: str, models: list[dict], model: str, effort: str) -> str:
    """Why a turn of the agent ``name`` may not ask for ``model`` and
    ``effort`` ("" for none, the agent's default), "" when it may: the
    model one of ``models`` (the probe's), the effort one of the efforts of
    that model, or of the agent's default model when none is chosen."""
    by_id = {m["id"]: m for m in models}
    if model and model not in by_id:
        return f"{label(name)} does not offer that model on this computer."
    if effort:
        chosen = by_id.get(model) if model else next((m for m in models if m["default"]), None)
        if chosen is None or effort not in chosen["efforts"]:
            return f"{label(name)} does not offer that effort for this model."
    return ""


def _current(name: str, hit: dict, asked: float, fresh: bool, models: bool) -> bool:
    """Whether the kept ``hit`` answers a probe asked at ``asked``: one that
    finished after the call began does, fresh or not, one younger than
    ``PROBE_TTL_S`` when not fresh; asked with ``models``, a ready agent's
    models must be as recent."""
    def recent(at: float) -> bool:
        return at >= asked or (not fresh and asked - at < PROBE_TTL_S)
    return recent(hit["at"]) and not (models and hit["ready"] and not recent(_listed.get(name, float("-inf"))))


def probe(name: str, *, fresh: bool = False, models: bool = True) -> dict:
    """``{name, label, found, version, ready, problem, models, at}`` of the
    agent ``name`` (``models`` what it offers, ``[{id, label, efforts,
    effort, default}]``: events.model), asked anew when ``fresh`` or older
    than ``PROBE_TTL_S``. Without ``models`` (a run's start, which needs
    only whether the agent is ready) the models are not asked again: a
    ready agent keeps those of the last probe that listed them, and when
    they were listed (``cached`` lists them again once that is
    ``PROBE_TTL_S`` old, or when there was none). It runs processes: for a
    write and a run, never a read path. Callers at the same time share one
    probe. Never raises."""
    if name not in AGENTS:
        return _answer(name, found=False, ready=False, problem=f"Gamma knows no agent named {name}.")
    asked = time.monotonic()
    with _locks[name]:
        hit = _cache.get(name)
        if hit and _current(name, hit, asked, fresh, models):
            return dict(hit)
        try:
            answer = _probe(AGENTS[name], models=models)
        except Exception as e:  # noqa: BLE001 — never raises
            log.warning(f"[agents] probing {name}: {e}")
            answer = _answer(name, found=False, ready=False, problem=f"{label(name)} is not on this computer.")
        if not answer["ready"]:
            _listed.pop(name, None)
        elif models:
            _listed[name] = answer["at"]
        elif hit and name in _listed:
            answer["models"] = list(hit.get("models") or [])
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
    stale (or a ready agent's models listed too long ago, or never) starts a
    single probe in the background and the stale answer (or ``found: None,
    ready: None``: still checking) is given meanwhile."""
    hit = _cache.get(name)
    if (hit is None or not _current(name, hit, time.monotonic(), False, True)) and name in AGENTS:
        with _refreshing_lock:
            begin = name not in _refreshing
            _refreshing.add(name)
        if begin:
            threading.Thread(target=_refresh, args=(name,), name=f"agent-probe-{name}", daemon=True).start()
    if hit is not None:
        return dict(hit)
    return _answer(name, found=None, ready=None, at=0)


def known(name: str) -> dict:
    """The last probe of ``name`` however old, for a reader that comes back
    often and needs no fresh one (a computer's channel, every poll): only
    when none was ever made does it start one, as ``cached`` does; else
    nothing runs, so it never keeps the agents probed by itself."""
    hit = _cache.get(name)
    return dict(hit) if hit is not None else cached(name)


def probes(*, fresh: bool = False) -> list[dict]:
    return [probe(name, fresh=fresh) for name in AGENTS]


def forget() -> None:
    """Drop every kept probe (tests)."""
    _cache.clear()
    _listed.clear()
