"""The CLIs' own sessions of a directory (docs/dev/local_agents.md "Sessions
started elsewhere"; step 5 of docs/research/agents-as-participants-plan.md,
read as its check C4 found them, Codex CLI 0.167.0 and Claude Code 2.1.318):
those a person started in a terminal, an editor or a script where a
conversation's agent works, listed for History (``listed``, ``shown``) and read into a
conversation of Gamma's (``read``, ``messages``), whose next turn resumes
the session (gamma/local_agents/chats.py ``adopt``).

- **Codex** keeps its threads itself: they are listed and read on a
  short-lived app-server (gamma/local_agents/codex.py ``threads``,
  ``thread``), named by their name or their first message, Gamma's own
  told apart by their originator. One is held while its writer's lock is
  (``<CODEX_HOME>/thread-writer-locks/<id>.lock``, locked by the process
  that has it open: a lock left by one that was killed is no writer's);
  a resume of a held one Codex refuses itself, which fails the turn.
- **Claude Code** keeps a directory's sessions as the files of
  ``<CLAUDE_CONFIG_DIR>/projects/<slug>/`` (``slug``), one JSON record a
  line; a file is the directory's only where its first user record's
  ``cwd`` is that directory (two paths can share a slug). Its title is its
  last ``ai-title``, else its first prompt; its current branch runs from
  the last ``last-prompt``'s ``leafUuid`` back through ``parentUuid``. It
  has no lock (two writers fork a file): one is held while
  ``<CLAUDE_CONFIG_DIR>/sessions/<pid>.json`` names it and that process
  lives.

Another server this computer is shown to asks for the sessions of the
directory a far conversation's agent works in over the channel, and for
one's turns to open it there (``answer_far``): the channel's account's
alone, the directory named by an id, never a path, and what goes there
capped too; the conversation it opens there is bound here, so its next far
turn resumes the session.

Whether Gamma has a session already is ``agent_conversations``'s
(``store.bound``), whatever the CLI says of it. What is read is capped (the
files looked at, the bytes of each, the turns), so a large store does not
hold the server; the routes that call this run off the event loop. A
session's text is its owner's data: shown as any message is, never put in a
prompt (the CLI keeps its own history: Gamma sends only the next message,
and ``from_session`` keeps these messages out of a turn's earlier ones).
"""

import json
import os
import re
import struct
import sys
from datetime import datetime, timezone
from pathlib import Path

from ..blocks_store import new_block_id
from ..db import format_stamp, page_now, parse_stamp
from ..logbuf import log
from . import catalog, chats, events, store

MAX_LISTED = 50                # sessions a directory lists, the newest
MAX_FILES = 200                # Claude Code's session files of a project looked at, the newest
HEAD_BYTES = 64 * 1024         # what a listing reads of a session file's start (its directory, its first prompt)
TAIL_BYTES = 64 * 1024         # and of its end (its title, its last time)
MAX_READ = 16 * 1024 * 1024    # what opening one reads of a file, its end
MAX_TURNS = 100                # turns an opened session brings, the newest
MAX_LIVE = 200                 # Claude Code's live processes read
SLUG_CHARS = 200               # where Claude Code cuts a project's name, a hash after
MAX_ERROR = 500                # characters of a turn's failure
FAR_MAX_BYTES = 2 * 1024 * 1024   # what an opened session sends another server at most, its newest messages
WIRE = ("role", "text", "steps", "state", "error", "at", "ended_at")   # a message's fields sent there
NO_SESSION = "No session of this directory has that id."
FAR_OFF = "That agent is not on for this account on this computer."

# What a session's source says it was started from, as the listing names it.
CODEX_ORIGINATORS = {"gamma": "gamma", "codex-tui": "cli", "codex_exec": "exec", "codex_vscode": "vscode",
                     "codex_work_desktop": "app"}
CLAUDE_ENTRYPOINTS = {"cli": "cli", "claude-vscode": "vscode", "sdk-cli": "exec", "sdk-ts": "exec",
                      "sdk-py": "exec", "claude-desktop": "app"}
SOURCES = ("cli", "vscode", "exec", "app", "gamma", "other")
_COMMAND = re.compile(r"^\s*<(?:command-|local-command)")   # a slash command's record, no prompt


# --- where the CLIs keep them ----------------------------------------------------------------------

def _codex_home() -> Path:
    return Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex")


def _claude_home() -> Path:
    return Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude")


def _long(path) -> str:
    """``path`` as the file system opens it however long: on Windows with
    the ``\\\\?\\`` prefix, as a project's directory there can pass 260
    characters."""
    text = os.path.abspath(str(path))
    if sys.platform != "win32" or text.startswith("\\\\?\\"):
        return text
    return "\\\\?\\UNC\\" + text[2:] if text.startswith("\\\\") else "\\\\?\\" + text


def _same_dir(a: str, b: str) -> bool:
    """Whether two paths name one directory, as the file system compares
    them (separators, a trailing one and, on Windows, case aside)."""
    if not a or not b:
        return False
    return os.path.normcase(os.path.normpath(a)) == os.path.normcase(os.path.normpath(b))


def slug(path: str) -> str:
    """The name of Claude Code's directory of the project ``path``: every
    UTF-16 unit outside ``A-Za-z0-9`` a ``-`` (case kept, runs not
    collapsed); past ``SLUG_CHARS`` cut there, then ``-`` and the base-36
    of the Java string hash of the whole path, its sign dropped."""
    out = "".join(ch if ch.isascii() and ch.isalnum() else "--" if ord(ch) > 0xFFFF else "-" for ch in path)
    if len(out) <= SLUG_CHARS:
        return out
    data = path.encode("utf-16-le", "surrogatepass")
    h = 0
    for unit in struct.unpack(f"<{len(data) // 2}H", data):
        h = (h * 31 + unit) & 0xFFFFFFFF
    h = abs(h - (1 << 32) if h >= 1 << 31 else h)
    digits = ""
    while True:
        h, rest = divmod(h, 36)
        digits = "0123456789abcdefghijklmnopqrstuvwxyz"[rest] + digits
        if not h:
            break
    return f"{out[:SLUG_CHARS]}-{digits}"


# --- held by another process ---------------------------------------------------------------------------

if sys.platform == "win32":
    import ctypes
    from ctypes import wintypes

    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    _kernel32.OpenProcess.restype = wintypes.HANDLE
    _kernel32.GetExitCodeProcess.argtypes = (wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD))
    _kernel32.GetExitCodeProcess.restype = wintypes.BOOL
    _kernel32.GetProcessTimes.argtypes = (wintypes.HANDLE, *[ctypes.POINTER(wintypes.FILETIME)] * 4)
    _kernel32.GetProcessTimes.restype = wintypes.BOOL
    _kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)


def _locked(path: Path) -> bool:
    """Whether a process holds the lock of the file ``path``: on Windows a
    byte-range lock fails a read of it, elsewhere a shared ``flock`` is
    refused while another holds it (taken and let go at once). A file that
    is not there, or that nobody holds (its writer killed), is no lock."""
    try:
        if sys.platform == "win32":
            with open(_long(path), "rb") as f:
                f.read(1)
            return False
        import fcntl

        fd = os.open(path, os.O_RDONLY)
        try:
            fcntl.flock(fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
            fcntl.flock(fd, fcntl.LOCK_UN)
            return False
        finally:
            os.close(fd)
    except FileNotFoundError:
        return False
    except OSError:
        return True


def _alive(pid, started) -> bool:
    """Whether the process ``pid`` runs, and on Windows is the one that
    started at ``started`` (Claude Code's ``procStart``, the process's
    creation time), so a number used again is not taken for it."""
    if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
        return False
    if sys.platform != "win32":
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        except OSError:
            return False
        return True
    handle = _kernel32.OpenProcess(0x1000, False, pid)   # PROCESS_QUERY_LIMITED_INFORMATION
    if not handle:
        return False
    try:
        code = wintypes.DWORD()
        if not _kernel32.GetExitCodeProcess(handle, ctypes.byref(code)) or code.value != 259:   # STILL_ACTIVE
            return False
        if not str(started or "").isdigit():
            return True
        times = [wintypes.FILETIME() for _ in range(4)]
        if not _kernel32.GetProcessTimes(handle, *(ctypes.byref(t) for t in times)):
            return True
        return (times[0].dwHighDateTime << 32 | times[0].dwLowDateTime) == int(started)
    finally:
        _kernel32.CloseHandle(handle)


def _claude_live() -> set[str]:
    """The sessions Claude Code's live processes have open (at most
    ``MAX_LIVE`` entries of ``sessions/``)."""
    try:
        entries = [e.path for e in os.scandir(_long(_claude_home() / "sessions")) if e.name.endswith(".json")]
    except OSError:
        return set()
    out = set()
    for path in entries[:MAX_LIVE]:
        try:
            with open(path, encoding="utf-8") as f:
                entry = json.load(f)
        except (OSError, ValueError):
            continue
        if isinstance(entry, dict) and isinstance(entry.get("sessionId"), str) and _alive(
                entry.get("pid"), entry.get("procStart")):
            out.add(entry["sessionId"])
    return out


def held(agent: str, session: str) -> str:
    """Why ``agent`` may not take up ``session`` now, "" when it may: a
    terminal or an editor has it open (Codex's writer lock held, a live
    process of Claude Code's naming it)."""
    if agent == "codex":
        found = _locked(_codex_home() / "thread-writer-locks" / f"{session}.lock")
    else:
        found = session in _claude_live()
    return events.BUSY.format(agent=catalog.label(agent)) if found else ""


# --- listed -------------------------------------------------------------------------------------------

def _stamp(seconds) -> str:
    """Unix seconds in the ``page_now`` shape, "" for none."""
    if not isinstance(seconds, (int, float)) or isinstance(seconds, bool):
        return ""
    return format_stamp(datetime.fromtimestamp(seconds, timezone.utc))


def _iso(text) -> str:
    """An ISO time (a record's ``timestamp``) in the ``page_now`` shape, ""
    for none."""
    found = parse_stamp(text) if isinstance(text, str) and text else None
    return format_stamp(found.astimezone(timezone.utc)) if found and found.tzinfo else ""


def _codex_source(thread: dict) -> str:
    originator, source = thread.get("originator"), thread.get("source")
    found = CODEX_ORIGINATORS.get(originator) if isinstance(originator, str) else None
    return found or (source if source in ("cli", "vscode", "exec") else "other")


def _codex_argv() -> list[str]:
    argv = catalog.command("codex")
    if argv is None:
        raise chats.ChatError(409, "Codex is not on this computer.")
    return argv


def _codex_listed(path: str) -> list[dict]:
    from . import codex

    try:
        found = codex.threads(_codex_argv(), path, MAX_LISTED)
    except events.TurnFailed as e:
        raise chats.ChatError(409, f"Codex could not list its sessions: {e}") from None
    out = []
    for thread in found:
        session = str(thread.get("id") or "")
        if not events.SESSION.match(session) or not _same_dir(str(thread.get("cwd") or ""), path):
            continue
        out.append({"id": session, "title": chats.title_of(str(thread.get("name") or thread.get("preview") or "")),
                    "at": _stamp(thread.get("updatedAt")), "created": _stamp(thread.get("createdAt")),
                    "source": _codex_source(thread), "busy": bool(held("codex", session))})
    return out


def _lines(data: bytes, *, whole_first: bool, whole_last: bool) -> list[dict]:
    """The records of a part of a session file: a line cut by the part's
    edges left out (``whole_first``, ``whole_last``: the part starts or
    ends the file), a line that is not a JSON object too."""
    lines = data.split(b"\n")
    if not whole_first:
        lines = lines[1:]
    if not whole_last:
        lines = lines[:-1]
    out = []
    for line in lines:
        try:
            record = json.loads(line)
        except ValueError:
            continue
        if isinstance(record, dict):
            out.append(record)
    return out


def _read_part(path: str, *, start: int, size: int, total: int) -> list[dict]:
    with open(path, "rb") as f:
        f.seek(start)
        data = f.read(size)
    return _lines(data, whole_first=start == 0, whole_last=start + len(data) >= total)


def _prompt(record: dict) -> str | None:
    """The person's prompt a ``user`` record is, None for any other (a tool's
    result, a slash command's record, one of Gamma's own or Claude Code's
    marked ``isMeta``, the summary a compaction wrote in the person's place:
    ``isCompactSummary``, ``isVisibleInTranscriptOnly``)."""
    if (record.get("type") != "user" or record.get("isSidechain") or record.get("isMeta")
            or record.get("isCompactSummary") or record.get("isVisibleInTranscriptOnly")):
        return None
    content = (record.get("message") or {}).get("content") if isinstance(record.get("message"), dict) else None
    if isinstance(content, str):
        text = content
    elif isinstance(content, list) and not any(isinstance(b, dict) and b.get("type") == "tool_result" for b in content):
        text = "\n".join(str(b.get("text") or "") for b in content if isinstance(b, dict) and b.get("type") == "text")
    else:
        return None
    return None if _COMMAND.match(text) else text


def _head(records: list[dict]) -> dict:
    """What a session file's first records say: its directory (its first
    user record's ``cwd``), where it was started from, when, and its first
    prompt."""
    out = {"cwd": "", "entrypoint": "", "created": "", "prompt": ""}
    for record in records:
        if record.get("type") != "user" or record.get("isSidechain"):
            continue
        if not out["cwd"]:
            out.update(cwd=str(record.get("cwd") or ""), entrypoint=str(record.get("entrypoint") or ""),
                       created=_iso(record.get("timestamp")))
        said = _prompt(record)
        if said and said.strip():
            out["prompt"] = said
            break
    return out


def _session_file(path: str, session: str) -> str:
    return _long(_claude_home() / "projects" / slug(path) / f"{session}.jsonl")


def _claude_listed(path: str) -> list[dict]:
    try:
        entries = [e for e in os.scandir(_long(_claude_home() / "projects" / slug(path)))
                   if e.name.endswith(".jsonl") and e.is_file()]
    except OSError:
        return []
    found = []
    for entry in entries:
        try:
            stat = entry.stat()
        except OSError:
            continue
        found.append((stat.st_mtime, stat.st_size, entry.name[:-len(".jsonl")], entry.path))
    live = _claude_live()
    out = []
    for mtime, size, session, file in sorted(found, reverse=True)[:MAX_FILES]:
        if not events.SESSION.match(session):
            continue
        try:
            head = _head(_read_part(file, start=0, size=HEAD_BYTES, total=size))
            tail = _read_part(file, start=max(0, size - TAIL_BYTES), size=TAIL_BYTES, total=size)
        except OSError as e:
            log.info(f"[agents] reading Claude Code's session {session}: {e}")
            continue
        if not _same_dir(head["cwd"], path):
            continue
        title = next((str(r.get("aiTitle") or "") for r in reversed(tail) if r.get("type") == "ai-title"), "")
        at = next((_iso(r.get("timestamp")) for r in reversed(tail) if _iso(r.get("timestamp"))), "") or _stamp(mtime)
        out.append({"id": session, "title": chats.title_of(title or head["prompt"]), "at": at,
                    "created": head["created"] or at, "source": CLAUDE_ENTRYPOINTS.get(head["entrypoint"], "other"),
                    "busy": session in live})
        if len(out) >= MAX_LISTED:
            break
    return out


def listed(agent: str, path: str) -> list[dict]:
    """The sessions of ``agent`` whose directory is ``path``, the most
    recently used first: ``{id, title, at, created, source, busy,
    conversation}``, ``source`` what it was started from (``SOURCES``: a
    terminal, an editor, a script, the CLI's desktop app, Gamma), ``busy``
    whether a process has it open (``held``), ``conversation`` the
    conversation of Gamma's that is its session now (``store.bound``), ""
    for none. ChatError 409 when Codex is not here or cannot list them."""
    rows = _codex_listed(path) if agent == "codex" else _claude_listed(path)
    bound = store.bound(agent, [row["id"] for row in rows])
    return [{**row, "conversation": bound.get(row["id"], "")} for row in rows]


def shown(rows: list[dict], ours: dict[str, str]) -> list[dict]:
    """The sessions ``listed`` gave as History lists them: ``{id, title,
    at, created, source, ours, busy}``, ``ours`` whether its conversation
    is one of ``ours`` (an id → its title, "" for none known), then titled
    as that conversation is and never busy (that conversation's own turn
    holds it)."""
    out = []
    for row in rows:
        conversation = row.get("conversation") or ""
        mine = bool(conversation) and conversation in ours
        out.append({**{k: row[k] for k in ("id", "title", "at", "created", "source", "busy")}, "ours": mine,
                    **({"title": ours[conversation] or row["title"], "busy": False} if mine else {})})
    return out


# --- read ---------------------------------------------------------------------------------------------

def _turn(prompt: str, at: str) -> dict:
    return {"prompt": prompt, "at": at, "paragraphs": [], "steps": [], "state": "done", "error": "", "ended_at": at}


def _codex_read(path: str, session: str) -> dict:
    from . import codex

    try:
        thread, turns = codex.thread(_codex_argv(), session, MAX_TURNS)
    except codex.RequestError:
        raise chats.ChatError(404, NO_SESSION) from None
    except events.TurnFailed as e:
        raise chats.ChatError(409, f"Codex could not read the session: {e}") from None
    if not _same_dir(str(thread.get("cwd") or ""), path):
        raise chats.ChatError(404, NO_SESSION)
    out = []
    for turn in turns:
        items = [item for item in turn.get("items") or [] if isinstance(item, dict)]
        prompt = "\n".join(str(part.get("text") or "") for item in items if item.get("type") == "userMessage"
                           for part in item.get("content") or [] if isinstance(part, dict) and part.get("type") == "text")
        read = _turn(prompt, _stamp(turn.get("startedAt")))
        read["ended_at"] = _stamp(turn.get("completedAt")) or read["at"]
        for item in items:
            if item.get("type") == "agentMessage":
                read["paragraphs"].append(str(item.get("text") or ""))
            else:
                read["steps"] += codex.item_steps(item, path, started=False, gamma=True)
        status = turn.get("status")
        if status == "failed":
            read.update(state="failed", error=str((turn.get("error") or {}).get("message") or "Codex's turn failed."))
        elif status != "completed":
            read["state"] = "stopped"
        out.append(read)
    return {"title": chats.title_of(str(thread.get("name") or thread.get("preview") or "")), "turns": out}


def _branch(records: list[dict]) -> list[dict]:
    """The records of a session's current branch, oldest first: from the
    last ``last-prompt``'s ``leafUuid`` (else the last record of the
    conversation, a sidechain's aside) back through ``parentUuid`` (``logicalParentUuid`` across
    a compaction), its people's and its agent's records alone."""
    by_id = {r["uuid"]: r for r in records if isinstance(r.get("uuid"), str)}
    leaf = next((r.get("leafUuid") for r in reversed(records) if r.get("type") == "last-prompt"
                 and r.get("leafUuid") in by_id), None)
    if leaf is None:
        leaf = next((r["uuid"] for r in reversed(records) if r.get("type") in ("user", "assistant")
                     and not r.get("isSidechain") and isinstance(r.get("uuid"), str)), None)
    chain, seen = [], set()
    while leaf in by_id and leaf not in seen:
        seen.add(leaf)
        record = by_id[leaf]
        chain.append(record)
        leaf = record.get("parentUuid") or record.get("logicalParentUuid")
    return [r for r in reversed(chain) if r.get("type") in ("user", "assistant") and not r.get("isSidechain")]


def _claude_read(path: str, session: str) -> dict:
    from . import claude

    file = _session_file(path, session)
    try:
        size = os.path.getsize(file)
        records = _read_part(file, start=max(0, size - MAX_READ), size=MAX_READ, total=size)
        head = _head(_read_part(file, start=0, size=HEAD_BYTES, total=size))
    except OSError:
        raise chats.ChatError(404, NO_SESSION) from None
    if not _same_dir(head["cwd"], path):
        raise chats.ChatError(404, NO_SESSION)
    turns, begun = [], {}
    for record in _branch(records):
        at = _iso(record.get("timestamp"))
        said = _prompt(record)
        if said is not None:
            if said.strip():
                turns.append(_turn(said, at))
            continue
        if not turns:
            turns.append(_turn("", at))
        turn = turns[-1]
        turn["ended_at"] = at or turn["ended_at"]
        content = (record.get("message") or {}).get("content") if isinstance(record.get("message"), dict) else None
        agent = record.get("type") == "assistant"   # a user record here is a tool's result
        for block in content if isinstance(content, list) else []:
            if not isinstance(block, dict):
                continue
            if agent and block.get("type") == "text":
                turn["paragraphs"].append(str(block.get("text") or ""))
            elif agent and block.get("type") == "tool_use":
                step = claude.tool_started(block, path, gamma=True)
                begun[step["id"]] = (turn, len(turn["steps"]))
                turn["steps"].append(step)
            elif block.get("type") == "tool_result" and str(block.get("tool_use_id") or "") in begun:
                owner, at_step = begun.pop(str(block["tool_use_id"]))
                owner["steps"][at_step] = claude.tool_ended(owner["steps"][at_step], block)
    title = next((str(r.get("aiTitle") or "") for r in reversed(records) if r.get("type") == "ai-title"), "")
    return {"title": chats.title_of(title or head["prompt"]), "turns": turns[-MAX_TURNS:]}


def read(agent: str, path: str, session: str) -> dict:
    """The session ``session`` of ``agent`` whose directory is ``path``:
    ``{title, turns: [{prompt, at, paragraphs, steps, state, error,
    ended_at}]}``, its last ``MAX_TURNS`` turns, each the person's prompt
    and what the agent wrote and did (its steps as tool events; no
    thinking). ChatError 404 for no such session of that directory, 409 when
    Codex is not here or cannot read it."""
    if not events.SESSION.match(session or ""):
        raise chats.ChatError(404, NO_SESSION)
    return _codex_read(path, session) if agent == "codex" else _claude_read(path, session)


def messages(agent: str, turns: list[dict], root: str) -> list[dict]:
    """The turns ``read`` gave as a conversation's messages, in the shapes
    chats.py keeps (the agent's text and steps as the runner writes a
    turn's: runner ``reply_text``, ``step_of``), each ``from_session``: the
    session's own history, which no turn's prompt repeats."""
    from . import runner   # local: the runner imports this module

    out = []
    for turn in turns:
        at = turn["at"] or page_now()
        if turn["prompt"].strip():
            out.append(user_message(turn["prompt"].strip()[:chats.MAX_TEXT], at))
        steps = [runner.step_of(event) for event in turn["steps"]][:store.MAX_STEPS]
        text = runner.reply_text(turn["paragraphs"], root)
        if not text and not steps and turn["state"] == "done":
            continue
        error = events.one_line(runner.reply_text([turn["error"]], root), MAX_ERROR) if turn["error"] else ""
        out.append(agent_message(agent, text=text, steps=steps, state=turn["state"], error=error, at=at,
                                 ended_at=turn["ended_at"] or at))
    return out


def user_message(text: str, at: str) -> dict:
    """The person's message of a session's turn, as chats.py keeps one."""
    return {"id": new_block_id(), "role": "user", "text": text, "selections": [], "at": at, "from_session": True}


def agent_message(agent: str, *, text: str, steps: list[dict], state: str, error: str, at: str,
                  ended_at: str) -> dict:
    """The agent's message of a session's turn, as chats.py keeps one (the
    runner's shape of a turn's, ended ``state``)."""
    return {"id": new_block_id(), "role": "agent", "agent": agent, "label": catalog.label(agent),
            "turn_id": new_block_id(), "state": state, "text": text, "steps": steps, "asks": [], "changes": [],
            "changed": 0, "built": False, "error": error, "fresh": False, "model": None, "effort": None,
            "speed": None, "at": at, "ended_at": ended_at, "from_session": True}


# --- asked by another server ----------------------------------------------------------------------------

def answer_far(link: dict, ask: dict) -> dict:
    """What this computer answers the server of its channel ``link`` that
    asks of its agent's sessions (gamma/computers/channel.py ``_sessions``;
    step 5 of docs/research/agents-as-participants-plan.md), for the
    channel's own account alone (its agent on: ``store.owns``), in the
    directory the ask names by an id as a far turn's claim does
    (``home.far_place``: the vault of that server's work folder, a
    directory offered it; never a path, nor a directory of its own):

    - ``{kind: "list", agent, dir}`` → ``{sessions}``, as ``listed`` gives
      them (the conversation each is the session of named by its id, which
      that server knows when it is its own);
    - ``{kind: "open", agent, dir, session, conversation_id, rebind}`` →
      ``{conversation}`` for a session a conversation of Gamma's is the
      session of (unless it is ``rebind``, one that server does not have),
      ``{busy: true}`` while a process holds it (``held``), else ``{title,
      messages}``, its turns as messages (``messages``, without their ids:
      that server makes its own; the newest within ``FAR_MAX_BYTES``), the
      session then bound to that server's new conversation
      ``conversation_id`` here (``store.ran_conversation``, as a far turn
      binds one), so its next turn's claim resumes it; one ``rebind`` let
      go of it first (``store.unbound``: its next turn starts afresh), so
      no two conversations resume it. A session is opened only where
      ``read`` finds it in that directory: one this computer lists there.

    ``{error, status}`` (404, 409) for anything refused."""
    from . import home  # local: home reaches the channel, which imports this module inside functions

    agent = ask.get("agent")
    if agent not in catalog.AGENTS or not store.owns(link["created_by"], agent):
        return {"error": FAR_OFF, "status": 409}
    try:
        place = home.far_place(link, ask.get("dir"), "")
    except ValueError as e:
        return {"error": str(e), "status": 409}
    try:
        if ask.get("kind") == "list":
            return {"sessions": listed(agent, place["path"])}
        session, conversation = ask.get("session"), ask.get("conversation_id")
        if ask.get("kind") != "open" or not isinstance(session, str) or not events.SESSION.match(session):
            return {"error": NO_SESSION, "status": 404}
        if not isinstance(conversation, str) or not chats.MESSAGE_ID.match(conversation) \
                or store.conversation_of(conversation) is not None:
            return {"error": "That server named a conversation this computer knows already.", "status": 409}
        bound = store.bound(agent, [session]).get(session)
        if bound and bound != ask.get("rebind"):
            return {"conversation": bound}
        if held(agent, session):
            return {"busy": True}
        found = read(agent, place["path"], session)
    except chats.ChatError as e:
        return {"error": e.detail, "status": e.status}
    sent = _newest([{k: m[k] for k in WIRE if k in m} for m in messages(agent, found["turns"], place["path"])],
                   FAR_MAX_BYTES)
    if bound:
        store.unbound(agent, session)
    store.ran_conversation(conversation,link_id=place["link"]["id"] if place["link"] is not None else "",
                           agent=agent, session=session, cwd=place["path"])
    return {"title": found["title"], "messages": sent}


def _newest(messages: list[dict], limit: int) -> list[dict]:
    """The newest of ``messages`` whose JSON fits in ``limit`` bytes."""
    kept, size = [], 2
    for message in reversed(messages):
        size += len(json.dumps(message, ensure_ascii=False).encode("utf-8")) + 1
        if size > limit:
            break
        kept.append(message)
    return kept[::-1]
