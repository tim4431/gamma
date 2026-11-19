"""Messages the user sends into a chat reply while it runs (Steer).

While a streamed reply runs, the chat queues what the user sends next
(frontend ``chat/chatQueue.js``) and sends it once the reply ends. Steer
hands one queued message to the running reply instead:
``POST /api/ai/chat/steer/{reply}``, the id the stream's ``{"steer"}``
line gave, right after its head. As Codex's instant interrupt does, the
reply then cuts the provider turn it is reading short at once
(``TurnRead``, even a model silent while it thinks, which streams no event
to cut at; ``pending`` says a message waits): what the model
wrote so far is its turn, and the calls of a round not started yet are
skipped; calls already running finish. The
message goes into the conversation as the user's turn, the stream says so
with a ``{"steered": {"ids"}}`` line, and the reply goes on with it
(``ai_agent.AgentLoop``, and the plain chat in ``routers/ai.py``). A reply
that ends before it takes a message, stopped, failed or simply done, leaves
the message to the client, which puts it back at the front of its queue.

A reply's inbox lives in memory for as long as its stream runs, bound to
the account whose chat it is, as a waiting approval is
(``ai_permissions``); a restart forgets it.
"""

import queue
import secrets
import threading
from contextlib import ExitStack, contextmanager

from .logbuf import log

MAX_OPEN_PER_USER = 20   # replies that take steered messages at once, per account
MAX_WAITING = 20         # messages waiting on one reply
MAX_TEXT = 32_000        # characters of one steered message

_open: dict[str, "Steering"] = {}
_lock = threading.Lock()


class Steering:
    """One streamed reply's inbox. ``turn(item)`` makes the conversation's
    user turn of a message the client sent (``{id, text, images}``); it runs
    in the steer request's thread, before the message waits."""

    def __init__(self, reply_id: str, user_id: str, turn):
        self.id = reply_id
        self.user = user_id
        self.turn = turn
        self._waiting: list[tuple[str, dict]] = []   # (client id, user turn)
        self._watcher = None   # called when a message arrives, while a turn that may be cut is read

    def pending(self) -> bool:
        """Whether a message waits to be taken in: the reply then cuts short
        what it is reading or about to run."""
        with _lock:
            return bool(self._waiting)

    @contextmanager
    def watch(self, fn):
        """While the block runs, ``fn`` is called (in the steer request's
        thread) when a message arrives, at once when one already waits."""
        with _lock:
            self._watcher = fn
            waits = bool(self._waiting)
        try:
            if waits:
                fn()
            yield
        finally:
            with _lock:
                self._watcher = None

    def take(self) -> dict | None:
        """What waits, as one user turn and the ids of the messages in it
        (``{"ids", "turn"}``), or None when nothing does. Several messages
        that arrived between two boundaries are one turn, their texts in
        the order they came, as the client shows them."""
        with _lock:
            waiting, self._waiting = self._waiting, []
        if not waiting:
            return None
        turns = [turn for _, turn in waiting]
        pictures = [part for turn in turns for part in turn.get("images") or []]
        joined = {"role": "user", "content": "\n\n".join(turn["content"] for turn in turns), "steered": True,
                  **({"images": pictures} if pictures else {})}
        return {"ids": [item_id for item_id, _ in waiting], "turn": joined}

    def close(self) -> None:
        """The reply ended: nothing more is taken in."""
        with _lock:
            if _open.get(self.id) is self:
                del _open[self.id]


def open_reply(user_id: str, turn) -> Steering | None:
    """A new inbox for a reply of ``user_id``'s, or None when the account
    has too many open (that reply then takes nothing in, and the client
    sends its queue when it ends)."""
    with _lock:
        if sum(1 for s in _open.values() if s.user == user_id) >= MAX_OPEN_PER_USER:
            return None
        steering = Steering(secrets.token_urlsafe(18), user_id, turn)
        _open[steering.id] = steering
        return steering


def push(reply_id: str, user_id: str, item: dict) -> bool:
    """Hand ``item`` to ``user_id``'s running reply. False when no reply of
    theirs runs under that id (it ended, or never was theirs) or too many
    of their messages already wait on it."""
    with _lock:
        steering = _open.get(reply_id)
        if not steering or steering.user != user_id or len(steering._waiting) >= MAX_WAITING:
            return False
    turn = steering.turn(item)
    with _lock:
        if _open.get(reply_id) is not steering or len(steering._waiting) >= MAX_WAITING:
            return False
        steering._waiting.append((str(item.get("id") or ""), turn))
        watcher = steering._watcher
    if watcher:
        watcher()
    return True


_ITEM, _END, _FAIL, _WOKEN = range(4)


class TurnRead:
    """One provider turn being read, which a message steered in
    (``steering``: given only where the turn may be cut) or the client
    leaving (``stopped``, an ``ai_client.Alarm``) ends at once, whatever the
    provider is doing: a model silent while it thinks sends no event to be
    cut at. Used as ``with TurnRead(resp, steering, stopped) as read: for
    event in read.events(source):``, ``source`` the turn's parsed events.

    The events are read in a thread of their own (``events``), so a wake
    ends the loop over them at once on every system; the response is then
    interrupted (``ai_client._OpenCall.interrupt``: its socket shut down,
    which ends the abandoned read at once on Linux and macOS and when the
    provider next sends on Windows, and its call slot freed) and closed by
    that thread when its read ends. ``woken`` says the turn was ended so,
    before the provider's end of the turn; ``ended()`` marks that end (its
    stop reason), after which nothing ends the read: the token report that
    follows is never cut off. A failure of a read it ended is no failure:
    the block ends quietly and the reader looks at ``woken``."""

    def __init__(self, resp, steering: Steering | None = None, stopped=None):
        self._resp = resp
        self._steering = steering
        self._stopped = stopped if callable(getattr(stopped, "watch", None)) else None
        self._lock = threading.Lock()
        self._queue = queue.SimpleQueue()
        self._asks = threading.Semaphore(0)   # items the loop asked the reading thread for
        self._stack = ExitStack()
        self._end = False       # the provider's end of the turn was read
        self._pumping = False   # the reading thread still has the response
        self._done = False      # the turn's reader is done with its events
        self.woken = False

    def __enter__(self):
        if self._steering:
            self._stack.enter_context(self._steering.watch(self.wake))
        if self._stopped:
            self._stack.enter_context(self._stopped.watch(self.wake))
        return self

    def __exit__(self, kind, error, tb):
        self._stack.close()
        with self._lock:
            self._done = True
            pumping, woken = self._pumping, self.woken
        if not pumping:
            self._finish()
        else:
            self._asks.release()     # a reading thread waiting to be asked ends
            if not woken:
                self._interrupt()    # the reading thread closes it when its read ends
        if kind is not None and issubclass(kind, Exception) and self.woken:
            log.info(f"[ai_steer] a provider turn ended early: {kind.__name__}")
            return True
        return False

    def ended(self) -> None:
        with self._lock:
            self._end = True

    def wake(self) -> None:
        with self._lock:
            if self._end or self.woken or self._done:
                return
            self.woken = True
        self._queue.put((_WOKEN, None))
        self._interrupt()

    def _interrupt(self) -> None:
        interrupt = getattr(self._resp, "interrupt", None)
        if callable(interrupt):
            interrupt()

    def _finish(self) -> None:
        """Close the response, given up or not (``_OpenCall.finish``; a
        test's response has close alone)."""
        finish = getattr(self._resp, "finish", None)
        (finish if callable(finish) else self._resp.close)()

    def events(self, source):
        """The items of ``source`` (an iterator over the response), read in
        a thread of their own one at a time, each when the loop asks for
        it (as reading ``source`` here would: nothing is read ahead of the
        reader), until they end, fail (raised here) or the turn is woken."""
        def pump():
            try:
                items = iter(source)
                while True:
                    self._asks.acquire()
                    with self._lock:
                        if self._done or self.woken:
                            break
                    try:
                        item = next(items)
                    except StopIteration:
                        self._queue.put((_END, None))
                        break
                    self._queue.put((_ITEM, item))
            except BaseException as e:   # handed to the reader, which raises it
                self._queue.put((_FAIL, e))
            finally:
                closing = getattr(source, "close", None)
                with self._lock:
                    self._pumping = False
                    late = self._done
                try:
                    if callable(closing):
                        closing()
                    if late:
                        self._finish()
                except Exception:
                    pass

        with self._lock:
            self._pumping = True
        threading.Thread(target=pump, name="ai-turn-read", daemon=True).start()
        while True:
            self._asks.release()
            tag, value = self._queue.get()
            if tag == _ITEM:
                yield value
            elif tag == _FAIL:
                raise value
            else:
                return   # the turn's end, or woken
