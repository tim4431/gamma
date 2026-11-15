"""What a server asks a computer of its agents' own sessions, over the
computer's channel (step 5 of docs/research/agents-as-participants-plan.md;
docs/dev/computers.md "Sessions over the channel"): History's *On TIM-PC*
on this server, for a far conversation's agent, lists the sessions its CLI
keeps of the directory it works in there, started in a terminal or an
editor, and opens one as a conversation here whose next turn resumes it
there.

This server never reaches the computer, which polls it: an ask waits here,
in memory, the computer's held poll is woken (``registry.wake``,
``sessions``), the computer takes the asks of its token
(``GET /api/computers/sessions``, ``taken``) and answers each by its id
(``POST /api/computers/sessions/{id}``, ``answered``: the token it was
asked of alone). The route that asked waits on the event loop for the
answer a while (``LIST_WAIT_S``, ``OPEN_WAIT_S``) and says when none came;
a listing's answer is kept ``KEEP_S``, so History opened again soon reads
it, and a listing asked while one of that agent and directory waits joins
it. Nothing is asked while History is closed, and nothing polls: the
client asks again when History opens again. An ask names the directory by
an id (``{kind: vault | offered, id}``), as a far turn's claim does; never a
path. Only a computer whose announce says it answers (``registry.answers``:
a release before this step says nothing) is asked.
"""

import asyncio
import secrets
import threading
import time

from . import registry

LIST_WAIT_S = 15      # how long a listing waits for the computer's answer
OPEN_WAIT_S = 30      # and an opening (a session read whole)
KEEP_S = 30           # a listing's answer is kept this long
GONE_S = 60           # an ask never answered is forgotten after this long
MAX_WAITING = 4       # asks one computer has unanswered at once


class _Ask:
    """One ask: what the computer is told (``body``), its answer once it
    came, and the routes that wait for it (``(loop, event)``)."""

    def __init__(self, token_id: str, body: dict):
        self.id = secrets.token_urlsafe(12)
        self.token_id, self.body = token_id, body
        self.at = time.monotonic()
        self.taken = False
        self.answer: dict | None = None
        self.waiters: list = []


_asks: dict[str, _Ask] = {}
_kept: dict[tuple, tuple[float, dict]] = {}   # a listing's answer by (token, agent, dir kind, dir id)
_lock = threading.Lock()


def _key(token_id: str, body: dict) -> tuple:
    return token_id, body["agent"], body["dir"]["kind"], body["dir"]["id"]


def _sweep() -> None:
    """The asks and the answers past their time forgotten. Under ``_lock``."""
    now = time.monotonic()
    for ask_id in [ask_id for ask_id, ask in _asks.items() if now - ask.at > GONE_S]:
        del _asks[ask_id]
    for key in [key for key, (at, _answer) in _kept.items() if now - at > KEEP_S]:
        del _kept[key]


def _new(token_id: str, body: dict) -> _Ask | None:
    """An ask of the computer behind ``token_id``, None while it has
    ``MAX_WAITING`` unanswered. Under ``_lock``."""
    if sum(1 for ask in _asks.values() if ask.token_id == token_id and ask.answer is None) >= MAX_WAITING:
        return None
    ask = _Ask(token_id, body)
    _asks[ask.id] = ask
    return ask


async def _wait(ask: _Ask | None, seconds: float, *, woken: bool) -> dict | None:
    """The answer to ``ask`` once it comes, within ``seconds``; None for
    none (or no ask). The computer is woken for a new one (``woken``)."""
    if ask is None:
        return None
    event = asyncio.Event()
    with _lock:
        if ask.answer is not None:
            return ask.answer
        ask.waiters.append((asyncio.get_running_loop(), event))
    if woken:
        registry.wake(ask.token_id, "sessions")
    try:
        await asyncio.wait_for(event.wait(), timeout=seconds)
    except asyncio.TimeoutError:
        pass
    with _lock:
        ask.waiters = [waiter for waiter in ask.waiters if waiter[1] is not event]
        return ask.answer


async def listing(token_id: str, agent: str, dir: dict) -> dict | None:
    """The sessions of the agent ``agent`` of the computer behind
    ``token_id`` in the directory ``dir`` as it answers (``{sessions}`` or
    ``{error, status}``: routers/computers.py ``_session_answer``): one kept
    from the last ``KEEP_S``, else the answer to the ask of them asked in
    the last ``LIST_WAIT_S``, else to a new one; None when it did not answer within
    ``LIST_WAIT_S`` (its answer is kept when it comes)."""
    body = {"kind": "list", "agent": agent, "dir": dir}
    with _lock:
        _sweep()
        kept = _kept.get(_key(token_id, body))
        if kept is not None:
            return kept[1]
        ask = next((ask for ask in _asks.values() if ask.body["kind"] == "list" and ask.answer is None
                    and _key(ask.token_id, ask.body) == _key(token_id, body)
                    and time.monotonic() - ask.at < LIST_WAIT_S), None)
        woken = ask is None
        ask = ask or _new(token_id, body)
    return await _wait(ask, LIST_WAIT_S, woken=woken)


async def opening(token_id: str, agent: str, dir: dict, session: str, conversation_id: str,
                  rebind: str = "") -> dict | None:
    """The session ``session`` of the agent ``agent`` of the computer
    behind ``token_id``, in the directory ``dir``, opened there for this
    server's new conversation ``conversation_id`` (``rebind`` a
    conversation that computer named as the session's that this server
    does not have): its answer (``{title, messages}``, ``{conversation}``,
    ``{busy}`` or ``{error, status}``), None when it did not answer within
    ``OPEN_WAIT_S`` (the ask then goes, and a late answer with it). The
    computer's listings kept are forgotten: that session's conversation is
    another now."""
    body = {"kind": "open", "agent": agent, "dir": dir, "session": session, "conversation_id": conversation_id,
            "rebind": rebind}
    with _lock:
        _sweep()
        ask = _new(token_id, body)
    try:
        return await _wait(ask, OPEN_WAIT_S, woken=True)
    finally:
        with _lock:
            if ask is not None:
                _asks.pop(ask.id, None)
            for key in [key for key in _kept if key[0] == token_id]:
                del _kept[key]


def taken(token_id: str) -> list[dict]:
    """The asks of the computer behind ``token_id`` it has not taken yet,
    oldest first, each ``{id, kind, agent, dir, ...}``, taken now."""
    with _lock:
        _sweep()
        found = [ask for ask in _asks.values() if ask.token_id == token_id and not ask.taken and ask.answer is None]
        for ask in found:
            ask.taken = True
        return [{"id": ask.id, **ask.body} for ask in sorted(found, key=lambda ask: ask.at)]


def pending(token_id: str, ask_id: str) -> dict | None:
    """What the ask ``ask_id`` of the computer behind ``token_id`` asked,
    while it waits for its answer; None for any other."""
    with _lock:
        ask = _asks.get(ask_id)
        return dict(ask.body) if ask is not None and ask.token_id == token_id and ask.answer is None else None


def answered(token_id: str, ask_id: str, answer: dict) -> bool:
    """The computer's ``answer`` to its ask ``ask_id``, handed to the routes
    that wait for it, and a listing's kept ``KEEP_S`` → whether that ask
    waited for it."""
    with _lock:
        ask = _asks.get(ask_id)
        if ask is None or ask.token_id != token_id or ask.answer is not None:
            return False
        ask.answer = answer
        if ask.body["kind"] == "list":
            _kept[_key(token_id, ask.body)] = (time.monotonic(), answer)
            del _asks[ask_id]
        waiters = list(ask.waiters)
    for loop, event in waiters:
        try:
            loop.call_soon_threadsafe(event.set)
        except RuntimeError:  # the loop closed
            pass
    return True


def reset() -> None:
    """Every ask and answer forgotten (tests)."""
    with _lock:
        _asks.clear()
        _kept.clear()
