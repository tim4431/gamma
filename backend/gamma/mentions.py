"""The @ mentions in note text (docs/dev/mentions.md) and the reminders
they carry.

A mention is plain text in a block's Markdown, so every reader that knows
nothing of it — the exports, the PDF typesetting, the AI's reads, search —
still shows something that reads right:

    @2026-10-09                         a date
    @2026-10-09 14:30                   a date with a time (the reader's wall clock)
    @2026-10-10 09:00 (remind @alice)   a reminder, for the account named alice
    @alice                              a person, by username

The editor draws them as chips (frontend/src/editor/mentions.js holds the
same syntax; tests/shared/mentions.json pins both). The server's one use is
the reminders: an account's are found through the notes index (a reminder
has the words "remind" and the username in it) and read back from the
blocks, in every workspace the account can open. Which reminders are done
is the account's rows of users.db ``reminders_done``, a mark each, kept for
as long as the reminder exists. A time is a wall-clock time with no zone:
the browser that shows the reminder decides when it is due, in the zone it
runs in.
"""

import calendar
import re

from . import workspaces
from .db import connect_pages_db, connect_users_db, page_now
from .logbuf import log

# A username as a mention spells it: what an account may be called
# (routers/admin.py), less a trailing "." or "-", which end the sentence.
_NAME = r"[A-Za-z0-9_](?:[A-Za-z0-9_.-]*[A-Za-z0-9_])?"
# The "@" starts a word: not after a letter or digit (an e-mail address), a
# path's "/", a "." or a backslash escape. ASCII classes, like the
# frontend's (JavaScript's \w is ASCII; Python's is not).
MENTION_RE = re.compile(
    r"(?<![A-Za-z0-9_@./+\\-])@(?:"
    r"(?P<date>\d{4}-\d{2}-\d{2})(?: (?P<time>\d{2}:\d{2}))?"
    rf"(?: \(remind @(?P<remind>{_NAME})\))?(?![A-Za-z0-9_:-])"
    rf"|(?!\d{{4}}-\d{{2}}-\d{{2}})(?P<user>{_NAME})(?![A-Za-z0-9_-]))")

# Code is not prose: a reminder written in a fence or inline code is an
# example, not a reminder (the rendered view shows it as code).
_CODE_RE = re.compile(r"```.*?(?:```|\Z)|`[^`\n]*`", re.S)


def _valid(day: str, time: str | None) -> bool:
    y, m, d = (int(x) for x in day.split("-"))
    if not (1 <= m <= 12 and 1 <= d <= calendar.monthrange(y, m)[1]):
        return False
    if time:
        hh, mm = (int(x) for x in time.split(":"))
        return hh <= 23 and mm <= 59
    return True


def scan(text: str) -> list[dict]:
    """Every mention in ``text``, in order: ``{kind: "date", raw, date[,
    time][, remind]}`` or ``{kind: "person", raw, user}``. An impossible
    date or time is no mention."""
    out = []
    for m in MENTION_RE.finditer(text or ""):
        if m["user"]:
            out.append({"kind": "person", "raw": m[0], "user": m["user"]})
            continue
        if not _valid(m["date"], m["time"]):
            continue
        token = {"kind": "date", "raw": m[0], "date": m["date"]}
        if m["time"]:
            token["time"] = m["time"]
        if m["remind"]:
            token["remind"] = m["remind"]
        out.append(token)
    return out


def reminders_in(content: str) -> list[dict]:
    """The reminders of a block's text, outside its code."""
    return [t for t in scan(_CODE_RE.sub(" ", content or "")) if t.get("remind")]


def reminder_key(block_id: str, token: dict) -> str:
    """What names one reminder in the done list: its block and its time, so
    moving the time makes it a new reminder. A block id has no "/"
    (blocks_store.BLOCK_ID_RE), so two reminders share a key only when one
    block reminds the same person twice at the same time; and a block keeps
    its id through edits and moves, so the key holds while the reminder's
    text does."""
    return f"{block_id}/{token['date']}/{token.get('time', '')}"


def reminders_for(user_id: str, username: str) -> dict:
    """``{reminders, done}``: the reminders for ``username`` in every
    workspace the account can open, soonest first — ``{key, workspace_id,
    page_id, page_title, block_id, date, time, text}`` (``time`` "" when
    the reminder has none, ``text`` the block's own) — and the keys of
    those the account has dismissed. A page in Recently deleted reminds no
    one. A block a clone or a publication copies (same id) is listed once,
    from the workspace that is neither. Each workspace's notes index is read
    whole, every block with both words in it; a workspace that cannot be
    read is logged and left out. The done marks are settled on the way
    (``_settle``)."""
    since = page_now()
    match = '"remind" "' + username.replace('"', '""') + '"'
    spaces = sorted(workspaces.list_for_user(user_id),
                    key=lambda w: bool(w.get("mirror_of") or w.get("publishing")))
    found, listed, read, out = {}, set(), set(), []
    for w in spaces:
        try:
            with connect_pages_db(w["id"]) as conn:
                rows = conn.execute(
                    "SELECT b.id, b.page_id, b.content, p.content, p.parent_id FROM block_fts f "
                    "JOIN unified_blocks b ON b.rowid = f.rowid JOIN unified_blocks p ON p.id = b.page_id "
                    "WHERE block_fts MATCH ?", (match,)).fetchall()
        except Exception as e:  # noqa: BLE001 — one workspace never hides the others' reminders
            log.warning(f"[mentions] the reminders in workspace {w['id']} could not be read: {e}")
            continue
        read.add(w["id"])
        for block_id, page_id, content, title, parent in rows:
            for token in reminders_in(content):
                key = reminder_key(block_id, token)
                if token["remind"] != username or key in listed:
                    continue
                if parent != "root":  # in Recently deleted: reminds no one, yet is still there
                    found.setdefault(key, w["id"])
                    continue
                listed.add(key)
                found[key] = w["id"]
                out.append({"key": key, "workspace_id": w["id"], "page_id": page_id,
                            "page_title": (title or "").split("\n", 1)[0], "block_id": block_id,
                            "date": token["date"], "time": token.get("time", ""), "text": content})
    done = _settle(user_id, found, read, len(read) == len(spaces), since)
    out.sort(key=lambda r: (r["date"], r["time"] or "09:00", r["key"]))
    return {"reminders": out, "done": [r["key"] for r in out if r["key"] in done]}


def _settle(user_id: str, found: dict, read: set, every: bool, since: str) -> set:
    """The account's done keys among ``found``, the reminders a listing saw
    (each key to its workspace; one in Recently deleted too, as it is still
    there), after the listing's bookkeeping. A mark takes the workspace its
    reminder was found in. A mark whose reminder was not found goes when it
    is older than the listing (``since``) and the listing read its
    workspace whole (``read``; a mark not yet placed, '', needs ``every``
    workspace read): its block was deleted, or its reminder edited away. A
    workspace the listing could not read, or the account can no longer
    open, keeps its marks."""
    with connect_users_db() as conn:
        marks = conn.execute("SELECT key, workspace_id, done_at FROM reminders_done WHERE user_id = ?",
                             (user_id,)).fetchall()
        placed = [(found[key], user_id, key) for key, ws, _ in marks if key in found and found[key] != ws]
        gone = [(user_id, key, since) for key, ws, at in marks
                if key not in found and at < since and (ws in read if ws else every)]
        conn.executemany("UPDATE reminders_done SET workspace_id = ? WHERE user_id = ? AND key = ?", placed)
        conn.executemany("DELETE FROM reminders_done WHERE user_id = ? AND key = ? AND done_at < ?", gone)
    return {key for key, _, _ in marks if key in found}


def set_done(user_id: str, keys: list[str], done: bool) -> list[str]:
    """Mark reminders done (or not done again); returns those of ``keys``
    now done. A mark stays while its reminder exists (``_settle``), however
    many the account has; the next listing places it in its workspace."""
    with connect_users_db() as conn:
        if done:
            now = page_now()
            conn.executemany("INSERT INTO reminders_done (user_id, key, done_at) VALUES (?, ?, ?) "
                             "ON CONFLICT(user_id, key) DO UPDATE SET done_at = excluded.done_at",
                             [(user_id, key, now) for key in keys])
        else:
            conn.executemany("DELETE FROM reminders_done WHERE user_id = ? AND key = ?",
                             [(user_id, key) for key in keys])
    return list(dict.fromkeys(keys)) if done else []
