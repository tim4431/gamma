"""Messages the user sends into a chat reply while it runs (Steer).

While a streamed reply runs, the chat queues what the user sends next
(frontend ``chat/chatQueue.js``) and sends it once the reply ends. Steer
hands one queued message to the running reply instead:
``POST /api/ai/chat/steer/{reply}``, the id the stream's ``{"steer"}``
line gave, right after its head. As Codex's instant interrupt does, the
reply then cuts the provider turn it is reading short at its next streamed
event (``pending``): what the model wrote so far is its turn, and the calls
of a round not started yet are skipped; calls already running finish. The
message goes into the conversation as the user's turn, the stream says so
with a ``{"steered": {"ids"}}`` line, and the reply goes on with it
(``ai_agent.AgentLoop``, and the plain chat in ``routers/ai.py``). A reply
that ends before it takes a message, stopped, failed or simply done, leaves
the message to the client, which puts it back at the front of its queue.

A reply's inbox lives in memory for as long as its stream runs, bound to
the account whose chat it is, as a waiting approval is
(``ai_permissions``); a restart forgets it.
"""

import secrets
import threading

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

    def pending(self) -> bool:
        """Whether a message waits to be taken in: the reply then cuts short
        what it is reading or about to run."""
        with _lock:
            return bool(self._waiting)

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
        return True
