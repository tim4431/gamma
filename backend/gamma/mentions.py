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
is the account-wide ``reminders-done`` pref. A time is a wall-clock time
with no zone: the browser that shows the reminder decides when it is due,
in the zone it runs in.
"""

import calendar
import re

from . import workspaces
from .db import REMINDERS_DONE_PREF_KEY, connect_pages_db, page_now, update_pref

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

DONE_CAP = 500  # done reminders remembered, the latest kept
_BLOCKS_PER_WORKSPACE = 500  # candidate blocks read per workspace


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
    moving the time makes it a new reminder."""
    return f"{block_id}/{token['date']}/{token.get('time', '')}"


def reminders_for(user_id: str, username: str) -> list[dict]:
    """The reminders for ``username`` in every workspace the account can
    open, soonest first: ``{key, workspace_id, page_id, page_title,
    block_id, date, time, text}`` (``time`` "" when the reminder has none,
    ``text`` the block's own). A page in Recently deleted reminds no one. A
    block a clone or a publication copies (same id) is listed once, from
    the workspace that is neither."""
    match = '"remind" "' + username.replace('"', '""') + '"'
    spaces = sorted(workspaces.list_for_user(user_id),
                    key=lambda w: bool(w.get("mirror_of") or w.get("publishing")))
    found, out = set(), []
    for w in spaces:
        with connect_pages_db(w["id"]) as conn:
            rows = conn.execute(
                "SELECT b.id, b.page_id, b.content, p.content FROM block_fts f "
                "JOIN unified_blocks b ON b.rowid = f.rowid JOIN unified_blocks p ON p.id = b.page_id "
                "WHERE block_fts MATCH ? AND p.parent_id = 'root' LIMIT ?",
                (match, _BLOCKS_PER_WORKSPACE)).fetchall()
        for block_id, page_id, content, title in rows:
            for token in reminders_in(content):
                key = reminder_key(block_id, token)
                if token["remind"] != username or key in found:
                    continue
                found.add(key)
                out.append({"key": key, "workspace_id": w["id"], "page_id": page_id,
                            "page_title": (title or "").split("\n", 1)[0], "block_id": block_id,
                            "date": token["date"], "time": token.get("time", ""), "text": content})
    out.sort(key=lambda r: (r["date"], r["time"] or "09:00", r["key"]))
    return out


def set_done(user_id: str, keys: list[str], done: bool) -> list[str]:
    """Mark reminders done (or not done again); returns the done keys. The
    list keeps the latest ``DONE_CAP``."""
    def change(value):
        marks = value if isinstance(value, dict) else {}
        for key in keys:
            if done:
                marks[key] = page_now()
            else:
                marks.pop(key, None)
        latest = sorted(marks.items(), key=lambda kv: kv[1], reverse=True)[:DONE_CAP]
        return dict(latest)
    return list(update_pref(user_id, REMINDERS_DONE_PREF_KEY, change))
