"""Where a turn's conversation lives, as a turn of the runner sees it
(gamma/local_agents/runner.py ``_ChatTurn``; docs/dev/local_agents.md):
``chat_home_for`` gives a turn its home.

``LocalChatHome`` is a bucket of this server (a page, a folder, the
library), its conversation in this server's pages.db (``chats.reply_of``
and ``write_reply``, the checks its route made, where it works:
``chats.place_of``). ``HttpChatHome`` is a bucket of another Gamma server
(a NAS; step 4d of docs/research/agent-collab.md, step 4.2 of
docs/research/agents-as-participants-plan.md) whose conversation with this
computer's agent that server keeps and writes, spoken to over this
computer's channel there (gamma/computers/channel.py: its token of that
server, workspace and account):

- **The claim** (``POST /api/computers/chats/{turn}/claim {run, agent}``)
  takes the queued turn for this computer's run of it (``chat_run_id``: one
  id a turn, so a claim retried after a lost answer, or after this server
  restarted, holds it) → the message, its selections, the conversation's
  last messages, the model, effort and speed it asks for, what the bucket is
  (``about``), where its agent works by an id (``dir``, resolved here by
  ``far_place``: the vault of that server's work folder this channel's
  account keeps here, a directory this computer offers, or one of its own,
  ``far_dir``; anything else is refused with why, a path above all),
  Gamma's tools (``tools``: a turn token of that server and its MCP
  address, taken only where the address is that server's own origin,
  ``gamma``), and for a comment thread's turn the thread (its entry with
  its PDF place, ``_on_disk``: in a vault, named by its file's path on this
  disk, its source line computed here from the PDF and the sources on this
  disk, ``annotations.sources.source_in_vault``), its region's pictures
  fetched (``GET /api/computers/threads/{id}/picture?at=now|before``) into
  the vault's ``.gamma/annotations/img/``, where the prompt finds a local
  work folder's (``prompt.picture_paths``). That server decided whose turn
  it is (the token's account, an editor there); 409 or 404: nothing to run,
  nothing written. The agent answers a thread with its token there; a turn
  that wrote no reply is answered there by that server at its end
  (gamma/local_agents/threads.py ``settle``).
- **The writes** (``POST .../write {run, seq, fields}``): the agent's
  message as the turn goes, at most every ``WRITE_EVERY_S`` (a write lost
  on the way is carried by the next, which holds the whole message), the
  last tried again for ``RETRY_S`` and the turn left open past it (the
  channel's next announce, or the next start, fails it there). Each write's
  ``seq`` is above the last one sent and never below the clock in
  microseconds, so one late or retried, or sent by this computer before it
  restarted, never undoes a newer one. Only the message's fields go, its
  paths relative to the directory; a 409 or 404 (stopped there, taken by
  another run, the conversation gone) stops the turn.
- **Source lines** of the selections are computed here, in a vault, as the
  thread's (``annotations.sources.source_in_vault``).

A request that cannot reach that server (or that it fails, 5xx) is tried
again for ``RETRY_S`` where it is the turn's last; any other refusal raises
at once (``Refused``). A turn there asks on this computer (its agent's
``ask``, runner.py ``ask_refusal``): what it asks is drawn and answered in
the desktop app's window and, where the channel lets that server answer
(its ``asks``), written in the message there too (``asks``), whose answer
comes back over the channel (gamma/computers/channel.py ``_chats``); else
the message says only ``waiting`` while an ask waits. A write that did not
reach that server is said (``missed``), so the flush tries it again.
"""

import base64
import hashlib
import json
import re
import shutil
import time
from pathlib import Path
from urllib.parse import quote, urlencode, urlsplit

from fastapi import HTTPException

from .. import config, gamma_sync, remote_servers
from ..db import account_names, connect_pages_db, connect_users_db, page_now
from ..logbuf import log
from . import catalog, chats, events, store

RETRY_S = 180          # how long a request that cannot reach the other server is tried again
RETRY_FIRST_S = 2      # the first wait between two tries, doubling
RETRY_MAX_S = 30       # up to this
TIMEOUT_S = 60         # one request's
WRITE_EVERY_S = 1.0    # a chat turn's message is written to the other server at most this often
_KEY = re.compile(r"^[A-Za-z0-9_-]{1,64}$")   # a picture's key (``annotations.pictures.key``)
_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")    # a folder's, a directory's or a conversation's id there
_TOKEN = re.compile(r"^gamma_[A-Za-z0-9_-]{20,128}$")   # a turn's token (``integrations.mint_turn``)
# The agent message's fields a chat turn writes to the other server: ``asks`` those that server may answer
# (the channel relays them), ``waiting`` that one waits for an answer on this computer alone, ``steers`` the
# messages steered in that the agent took (runner.py ``_progress``).
CHAT_FIELDS = ("text", "steps", "asks", "changes", "changed", "built", "error", "fresh", "state", "ended_at", "model",
               "effort", "speed", "waiting", "steers")
NO_SUCH_DIR = "That server named no directory this computer offers it: Gamma ran nothing."
NOT_KEPT = "The synced folder is no longer kept on this computer."
NOT_OFFERED = "The directory this conversation works in is no longer offered on this computer."
UNLINKED = "This computer no longer answers that server."
FAR_TOOLS = ("Gamma's tools were not given to this turn: that server named their address at {origin}, not at "
             "its own address ({server}).")


class Refused(Exception):
    """The other server refused a request of the turn: ``status``, its
    ``detail``."""

    def __init__(self, status: int, detail: str):
        super().__init__(f"{status}: {detail}")
        self.status, self.detail = status, detail


# --- another server's conversation ------------------------------------------------------------

def reply_id(turn_id: str, thread_id: str) -> str:
    """The id of the reply a turn's end writes in its thread
    (gamma/local_agents/threads.py ``settle``), the same however often that
    end is written (a block id's shape: ``blocks_store.new_block_id``'s
    length)."""
    digest = hashlib.sha256(f"{turn_id}\n{thread_id}".encode("utf-8")).digest()
    return base64.urlsafe_b64encode(digest[:9]).decode("ascii")


def _place(entry: dict) -> dict:
    """The entry's place (that server's ``place``: ``{page, box, rects,
    doc_id}``) as ``sources.source_in_vault`` asks it."""
    place = entry.get("place") if isinstance(entry.get("place"), dict) else {}
    return {**place, "page_id": entry.get("page_id"), "kind": entry.get("kind")}


def _on_disk(found: dict, files: dict, paths: dict, root: Path) -> dict:
    """A thread turn's entry as that server's claim gave it, named by its
    file's path on this disk (the vault's rows, ``paths``) and,
    open and on the PDF as it is built here, with its source line computed
    from the PDF and the sources on this disk (``sources.source_in_vault``)."""
    from ..annotations import entries, sources

    entry = dict(found)
    there = entry.get("file")
    if entry.get("page_id") in paths:
        entry["file"] = paths[entry["page_id"]]
    source = entry.get("source") if isinstance(entry.get("source"), dict) else None
    if source and source.get("file") == there and there != entry.get("file"):
        entry["source"] = {**source, "file": entry["file"]}   # a range of a text file: its own path
    if entry.get("state") == "open" and entry.get("kind") in entries.PLACED \
            and (entry.get("where") or {}).get("placed") in entries.ON_THIS_BUILD:
        line = sources.source_in_vault(files, root, _place(entry))
        if line:
            entry["source"] = {**entries._source_fields(line), "version": "current"}
    return entry


def far_dir(link_id: str, conversation_id: str) -> Path:
    """The empty directory of its own a conversation of the channel
    ``link_id``'s server works in here (``far_place``'s ``own``): below the
    data directory, made at its first turn (the runner) and removed with the
    channel (``remove_far_dirs``; that server never says a conversation
    went), and at start once the channel is gone (``chats.sweep_dirs``, the
    channel's directory naming no workspace)."""
    return config.AGENTS_DIR / link_id / conversation_id


def remove_far_dirs(link_id: str) -> None:
    """The directories of their own (``far_dir``) of the channel
    ``link_id``'s conversations removed: it went. Never raises."""
    top = config.AGENTS_DIR / link_id
    if top.is_dir():
        try:
            shutil.rmtree(top)
        except OSError as e:
            log.warning(f"[agents] removing the directories of the conversations of channel {link_id}: {e}")


def far_place(link: dict, dir, conversation_id: str) -> dict:
    """Where a turn of the channel ``link``'s server works here, from the id
    that server named (``dir``, ``{kind, id}``: chats.py ``dir_of`` there),
    as ``chats.place_of`` answers a local one: ``{kind, dir, path, name,
    link}``,

    - ``vault``: the vault here of that server's work folder ``id`` the
      channel's account made (its server, workspace and account:
      ``channel.vaults_of``, ``folder_links.work_vault``), never another
      channel's, ``link`` the vault;
    - ``offered``: the directory this computer offers its agents of that id
      (``store.dirs``, while the channel's account has an agent on here:
      ``channel.offered``);
    - ``own``: an empty directory of its own (``far_dir``), the
      conversation ``conversation_id``'s.

    ValueError with why for anything else: a kind or an id of another shape
    (a path never names a directory here), a work folder no vault of this
    channel keeps, a directory not offered."""
    from .. import folder_links  # local: folder_links imports this package's modules inside functions
    from ..computers import channel

    kind, dir_id = (dir.get("kind"), dir.get("id")) if isinstance(dir, dict) else ("", None)
    if not isinstance(dir_id, str) or (kind in ("vault", "offered") and not _ID.match(dir_id)) or (
            kind == "own" and (dir_id or not _ID.match(conversation_id or ""))):
        raise ValueError(NO_SUCH_DIR)
    if kind == "vault":
        vault = next((v for v in channel.vaults_of(link) if v["kind"] == "folder" and v["folder_id"] == dir_id
                      and v["remote_url"] and folder_links.work_vault(v)), None)
        if vault is None:
            raise ValueError(NOT_KEPT)
        return {"kind": "vault", "dir": dir_id, "path": vault["path"], "name": Path(vault["path"]).name,
                "link": vault}
    if kind == "offered":
        named = {entry["id"] for entry in channel.offered(link)}
        entry = next((entry for entry in store.dirs() if entry["id"] == dir_id and dir_id in named), None)
        if entry is None:
            raise ValueError(NOT_OFFERED)
        return {"kind": "offered", "dir": dir_id, "path": entry["path"], "name": entry["name"], "link": None}
    if kind == "own":
        return {"kind": "own", "dir": "", "path": str(far_dir(link["id"], conversation_id)), "name": "", "link": None}
    raise ValueError(NO_SUCH_DIR)


# --- a turn of the chat's computer side ------------------------------------------------------

def chat_home_for(ws: str, turn_id: str, chat: dict):
    """The home of a turn keyed ``(ws, turn id)``, ``chat`` the runner's
    entry of it: a turn of another server's keys its channel in place of
    the workspace (``runner.queue_remote_chat``, ``chat["channel"]``), so it
    never reads as a turn of a workspace here."""
    return HttpChatHome(ws, turn_id, chat.get("dir")) if chat.get("channel") else LocalChatHome(ws)


def chat_run_id(turn_id: str) -> str:
    """The id of this computer's run of the chat turn ``turn_id`` of another
    server, the same on every claim of it (a retry, a restart), so a claim
    whose answer was lost holds it and a turn left open is failed there by
    the run that holds it; a run's id's shape (22 characters of
    ``[A-Za-z0-9_-]``)."""
    digest = hashlib.sha256(f"chat-turn\n{turn_id}".encode("utf-8")).digest()
    return base64.urlsafe_b64encode(digest[:16]).decode("ascii").rstrip("=")


class LocalChatHome:
    """A bucket of this server: the conversation in its pages.db, the
    checks the turn's route made."""

    remote = False
    run_id = ""         # a local turn is this server's own
    unsure = False      # a turn not found is never this turn's
    missed = False      # a write here is written or raises

    def __init__(self, ws: str):
        self.ws = ws

    def claim(self, turning) -> dict | None:
        """The turn's context, ``{user_id, owner, text, selections,
        earlier, title, thread_id}`` (the conversation's account, the
        account whose agent it talks to, the person's message and its
        selections, the conversation's last messages before it, its title,
        the comment thread it answers, "" for a chat's), while its message
        reads queued; None when it is gone or ended meanwhile (a Stop while
        it waited for a slot ended its message and its row)."""
        conversation, reply = chats.reply_of(self.ws, turning.conversation_id, turning.turn_id)
        if reply is None or reply.get("state") != "queued":
            return None
        messages = conversation["messages"]
        at = next(i for i, m in enumerate(messages) if m.get("turn_id") == turning.turn_id)
        person = next((m for m in reversed(messages[:at]) if m.get("role") == "user"), None) or {}
        return {"user_id": conversation["user_id"], "owner": conversation["owner"] or conversation["user_id"],
                "text": person.get("text") or "",
                "selections": person.get("selections") or [], "earlier": chats.earlier_of(messages, at, person),
                "title": conversation["title"], "thread_id": conversation["thread_id"]}

    def place(self, turning, context: dict) -> dict:
        """Where the turn works (``chats.place_of`` for the conversation's
        account), which must still be where the conversation works: the
        turn fails when its bucket is gone, when the account may no longer
        talk to an agent here, when its work folder is no longer kept here
        or its directory no longer offered, and when its bucket moved into
        a work folder kept here."""
        place = chats.place_of(self.ws, turning.bucket, context["user_id"], turning.dir, turning.conversation_id)
        if place is None:
            with connect_pages_db(self.ws) as conn:
                gone = chats.about(conn, turning.bucket) is None
            turning._fail("What this conversation is about is gone." if gone
                          else f"{turning.label} was turned off on this computer.")
        if place["dir"] != turning.dir:
            if turning.dir and turning.dir == place["about"]["w"]:
                turning._fail("The synced folder is no longer kept on this computer.")
            turning._fail("The directory this conversation works in is no longer offered on this computer."
                          if turning.dir else "This page is in a synced folder now: start a new chat to work there.")
        return place

    def whose(self, link: dict | None, context: dict) -> str:
        """The account whose agent the turn runs: the conversation's owner
        (its own account, or the one that lends a co-author its agent)."""
        return context["owner"]

    def person(self, context: dict) -> tuple[str, str]:
        """``(the account the turn is for, its username)``: the
        conversation's."""
        return context["user_id"], self.owner_name(context["user_id"])

    def lent(self, context: dict) -> bool:
        """Whether the turn is a co-author's: its agent another account's."""
        return context["user_id"] != context["owner"]

    def serves(self, agent: str, person: str) -> bool:
        """Whether the agent's owner still lets ``person`` use it in this
        workspace (``store.named``)."""
        return store.named(agent, "", self.ws, person)

    def editor(self, user_id: str) -> bool:
        """Whether the account edits the workspace."""
        from .. import workspaces

        return workspaces.at_least(workspaces.role_of(self.ws, user_id), "editor")

    def cannot_write(self) -> str:
        """Why this server or the workspace takes no writes now, "" when
        they do: what fails a turn before its agent edits anything."""
        from .. import hosted, workspaces

        refused = hosted.read_only()
        if not refused:
            try:
                workspaces.refuse_writes(self.ws)
            except HTTPException as e:
                refused = str(e.detail)
        return refused

    def owner_name(self, user_id: str) -> str:
        with connect_users_db() as users:
            return account_names(users, {user_id}).get(user_id, "") or "the owner"

    def where(self, turning, place: dict, context: dict) -> tuple[dict, list[dict]]:
        """``(what the conversation is about, the person's selections for
        the prompt)``: the place's ``about``, a file's ``path`` as the
        vault's rows name it, and each selection ``{page, quote, source}``,
        ``source`` the line its passage maps to in the work folder's sources
        (``annotations.sources.source_for``) when its box is known, else None
        (always, outside a work folder kept here)."""
        from ..annotations import entries
        from ..vault import store as vault_store

        about, link, chosen = dict(place["about"]), place["link"], context["selections"]
        sources = [None] * len(chosen)
        if link is not None and about["kind"] == "page":
            about["path"] = entries.paths_of(vault_store.files(link["id"])).get(turning.bucket) or about["path"]
            with connect_pages_db(self.ws) as conn:
                sources = chats.sources_of(self.ws, conn, turning.bucket, chosen)
        selections = [{"page": c.get("page"), "quote": c.get("text") or "", "source": source}
                      for c, source in zip(chosen, sources)]
        return about, selections

    def thread(self, turning, place: dict, context: dict) -> dict | None:
        """The thread a thread's turn answers as it is now
        (``threads.entry_of``), its file named as the vault's rows name it,
        its source line looked up here; None for one gone."""
        from ..annotations import entries
        from ..vault import store as vault_store
        from . import threads

        link = place["link"]
        paths = entries.paths_of(vault_store.files(link["id"])) if link is not None else None
        with connect_pages_db(self.ws) as conn:
            return threads.entry_of(self.ws, conn, context["thread_id"], paths=paths)

    def write(self, turning, fields: dict, *, last: bool = False) -> bool:
        """The turn's agent message takes ``fields`` → whether it was
        written (False: the conversation or the message is gone)."""
        return chats.write_reply(self.ws, turning.conversation_id, turning.turn_id, fields)


def vault_file(turning, link: dict) -> str:
    """The page's path in the vault as this computer's rows of it name it,
    "" when they name none: what an ask on this computer names, never a
    word the other server gave (``HttpChatHome.where``'s fallback to the
    claim's path or title is the prompt's alone)."""
    from ..annotations import entries
    from ..vault import store as vault_store

    return entries.paths_of(vault_store.files(link["id"])).get(turning.bucket) or ""


class HttpChatHome:
    """A bucket of another server, over this computer's channel ``link_id``
    there (the module doc): ``dir`` where the turn works as that server's
    listing named it (``far_place``; the claim's names it again)."""

    remote = True

    def __init__(self, link_id: str, turn_id: str, dir=None):
        self.link_id = link_id
        self.turn_id = turn_id
        self.run_id = chat_run_id(turn_id)
        self.dir = dir
        self.server: gamma_sync.Server | None = None
        self.channel: dict = {}          # the channel's row, as the claim found it
        self.username = ""               # the account there the channel is of
        self.account = ""                # its id there
        self.seq = 0                     # the last write's
        self.lost = False                # that server no longer gives the turn to this run
        self.gave_up = False             # the last write could not reach it
        self.unsure = False              # the claim was sent but never answered: that server may hold it
        self.missed = False              # the last write did not reach that server: the next one carries it
        self.about: dict = {}            # the claim's ``about``: what the bucket is there
        self.tools = None                # the claim's ``tools``: {url, token}, or None

    @property
    def remote_url(self) -> str:
        return self.channel.get("remote_url") or ""

    def _connect(self) -> bool:
        """The client of the channel, False for one gone (or whose token
        this computer cannot read)."""
        from ..computers import channel  # local: it imports folder_links, which imports this package

        link = channel.get(self.link_id, with_token=True)
        if link is None or not link["token"]:
            return False
        self.channel = link
        self.server = gamma_sync.Server(link["remote_url"], link["token"], timeout=TIMEOUT_S)
        self.username, self.account = link["username"], link["account"]
        return True

    def _ask(self, method: str, path: str, body=None, *, retry: bool = True) -> tuple[int, bytes]:
        """``(status, the answer's bytes)``; one that cannot reach that
        server, or a 5xx or 429, tried again for ``RETRY_S`` (``retry``),
        then ``gamma_sync.Unreachable``."""
        until, wait = time.monotonic() + (RETRY_S if retry else 0), RETRY_FIRST_S
        while True:
            try:
                status, data = self.server.ask(method, path, body)
                if status < 500 and status != 429:
                    return status, data
                error = gamma_sync.Unreachable(f"{path.split('?')[0]}: {status}")
            except gamma_sync.Unreachable as e:
                error = e
            if time.monotonic() + wait > until:
                raise error
            time.sleep(wait)
            wait = min(wait * 2, RETRY_MAX_S)

    def _json(self, method: str, path: str, body=None, *, retry: bool = True) -> dict:
        """The JSON answer of a 2xx; ``Refused`` for any other status."""
        status, data = self._ask(method, path, body, retry=retry)
        try:
            answer = json.loads(data.decode("utf-8")) if data else {}
        except ValueError:
            answer = None
        if not 200 <= status < 300:
            detail = answer.get("detail") if isinstance(answer, dict) else ""
            raise Refused(status, detail if isinstance(detail, str) else "")
        if not isinstance(answer, dict):
            raise gamma_sync.Unreachable(f"{path.split('?')[0]}: the server did not answer JSON")
        return answer

    def _chats(self, what: str) -> str:
        return f"/api/computers/chats/{quote(self.turn_id, safe='')}/{what}"

    def _next(self) -> int:
        """The next write's ``seq``: above the last one and never below the
        clock in microseconds, so a write this computer sent before it
        restarted is never newer."""
        self.seq = max(self.seq + 1, time.time_ns() // 1000)
        return self.seq

    def claim(self, turning) -> dict | None:
        """The channel and the turn's agent checked, the turn taken there
        for this computer's run → its context (``{text, selections,
        earlier, title, thread_id, thread, for}``, a thread's turn's thread
        as the claim gave it, ``for`` the account there the turn is for,
        ``{id, username}``; the turn's conversation, bucket, model, effort and
        speed set on ``turning``, what the bucket is, where it works and Gamma's
        tools kept here: ``about``, ``dir``, ``tools``), recorded here
        (``store.claimed_turn``); None when it is not this run's: stopped
        while it waited, the channel gone, its agent off for the channel's
        account (the turn failed there with why, ``_turned_off``), the turn
        taken by another run, no longer queued or not this computer's, that
        server not reached (``unsure``: it may hold the turn for this run,
        which stays recorded open for it to be failed there later)."""
        if turning.stop.is_set() or not self._connect():
            return None
        if not store.owns(self.channel["created_by"], turning.agent):
            self._turned_off(turning)
            return None
        try:
            answer = self._json("POST", self._chats("claim"), {"run": self.run_id, "agent": turning.agent})
            turn = answer.get("turn")
            if not isinstance(turn, dict) or turn.get("id") != self.turn_id:
                raise gamma_sync.Unreachable("/api/computers/chats: the claim named no turn")
        except Refused as e:
            if e.status not in (403, 404, 409):
                log.warning(f"[agents] the claim of chat turn {self.turn_id} refused there: {e}")
            return None
        except gamma_sync.SyncError as e:
            log.warning(f"[agents] the claim of chat turn {self.turn_id}: {e}")
            self.unsure = True
            store.claimed_turn(self.turn_id, link_id=self.link_id, conversation_id=turning.conversation_id,
                               page_id=turning.bucket, agent=turning.agent)
            return None
        turning.conversation_id = str(turn.get("conversation_id") or turning.conversation_id)
        turning.bucket = str(turn.get("bucket") or turning.bucket)
        turning.model, turning.effort = str(turn.get("model") or ""), str(turn.get("effort") or "")
        turning.speed = str(turn.get("speed") or "")   # an older server's claim names none: the default
        store.claimed_turn(self.turn_id, link_id=self.link_id, conversation_id=turning.conversation_id,
                           page_id=turning.bucket, agent=turning.agent)
        self.about = answer["about"] if isinstance(answer.get("about"), dict) else {}
        self.dir = answer["dir"] if "dir" in answer else self.dir   # an older server's names none: the listing's
        self.tools = answer.get("tools") if isinstance(answer.get("tools"), dict) else None
        history = answer.get("history") if isinstance(answer.get("history"), list) else []
        earlier = [{"role": "owner" if m.get("role") in ("owner", "user") else "agent", "text": m["text"]}
                   for m in history if isinstance(m, dict) and isinstance(m.get("text"), str) and m["text"].strip()]
        selections = []
        for raw in turn.get("selections") if isinstance(turn.get("selections"), list) else []:
            kept = chats.selections_of([raw])
            if kept:
                place = raw.get("place") if isinstance(raw.get("place"), dict) else {}
                selections.append({**kept[0], "doc_id": str(place.get("doc_id") or raw.get("doc_id") or "")})
        thread_id = str(turn.get("thread_id") or "")
        thread = answer.get("thread") if isinstance(answer.get("thread"), dict) else None
        person = turn.get("for") if isinstance(turn.get("for"), dict) else {}
        return {"text": str(turn.get("text") or ""), "selections": selections[:chats.MAX_SELECTIONS],
                "earlier": earlier[-chats.EARLIER:], "title": chats.title_of(str(turn.get("title") or "")),
                "thread_id": thread_id, "thread": thread if thread_id and thread and thread.get("id") == thread_id
                else None, "for": {"id": str(person.get("id") or self.account),
                                   "username": events.one_line(str(person.get("username") or self.username), 64)}}

    def _turned_off(self, turning) -> None:
        """The turn's agent was turned off while the turn waited here: the
        turn taken there by this run and failed with why ("Codex was turned
        off on this computer."), as a turn of this server's fails, never
        left waiting there; nothing when that server no longer gives it to
        this computer (its announce without the agent came first). Recorded
        here once taken, so an end that never reached that server is failed
        there later (``unsure``)."""
        if turning.agent not in catalog.AGENTS:
            return
        fields = {"state": "failed", "error": f"{catalog.label(turning.agent)} was turned off on this computer.",
                  "ended_at": page_now()}
        try:
            self._json("POST", self._chats("claim"), {"run": self.run_id, "agent": turning.agent}, retry=False)
        except Refused as e:
            log.info(f"[agents] the chat turn {self.turn_id}, its agent turned off, not taken there: {e}")
            return
        except gamma_sync.SyncError as e:
            log.warning(f"[agents] the claim of chat turn {self.turn_id}, its agent turned off: {e}")
            self.unsure = True
        store.claimed_turn(self.turn_id, link_id=self.link_id, conversation_id=turning.conversation_id,
                           page_id=turning.bucket, agent=turning.agent)
        if self.unsure:
            return
        try:
            self._json("POST", self._chats("write"), {"run": self.run_id, "seq": self._next(), "fields": fields},
                       retry=False)
        except Refused as e:
            log.info(f"[agents] the chat turn {self.turn_id}, its agent turned off: {e}")
        except gamma_sync.SyncError as e:
            log.warning(f"[agents] the chat turn {self.turn_id}, its agent turned off, left open: {e}")
            self.unsure = True
            return
        store.end_turn(self.turn_id, state="failed", error=fields["error"])

    def place(self, turning, context: dict) -> dict:
        """Where the turn works, from the id the claim named (``far_place``),
        while the channel is kept and the claim's conversation id has an
        id's shape (its session's key here: ``store.conversation_of``); else
        the turn fails, as it does with ``far_place``'s why."""
        from ..computers import channel

        link = channel.get(self.link_id)
        if link is None:
            turning._fail(UNLINKED)
        if not _ID.match(turning.conversation_id or ""):
            turning._fail(NO_SUCH_DIR)
        try:
            place = far_place(link, self.dir, turning.conversation_id)
        except ValueError as e:
            turning._fail(str(e))
        return {**place, "about": self.about}

    def gamma(self) -> tuple[events.Gamma | None, str]:
        """Gamma's tools for the turn as the claim handed them (``tools``:
        ``{url, token}``): ``(events.Gamma, "")`` where the address is that
        server's MCP endpoint at the origin this channel speaks to it at,
        else ``(None, why)``, the turn running without them and saying so
        (``FAR_TOOLS``): an address elsewhere is never given that server's
        token. ``(None, "")`` where that server gave none (it names no
        address this computer reaches, gamma/routers/computers.py
        ``_mcp_url``) or a token of another shape."""
        tools = self.tools or {}
        url, token = tools.get("url"), tools.get("token")
        if not isinstance(url, str) or not isinstance(token, str) or not _TOKEN.match(token):
            log.info(f"[agents] the chat turn {self.turn_id} of channel {self.link_id} runs without Gamma's tools: "
                     "that server gave none")
            return None, ""
        server = remote_servers.origin_of(self.remote_url)
        try:
            parts, origin = urlsplit(url), remote_servers.origin_of(url)
        except ValueError:
            parts, origin = None, ""
        if parts is None or origin != server or parts.path != "/mcp" or parts.query or parts.fragment:
            return None, FAR_TOOLS.format(origin=events.one_line(origin or url, 200), server=server)
        return events.Gamma(url, token), ""

    def whose(self, link: dict | None, context: dict) -> str:
        """The account here whose channel took the turn: that server decided
        at the claim it is a turn of the token's account's agent."""
        return self.channel.get("created_by") or ""

    def person(self, context: dict) -> tuple[str, str]:
        """``(the account there the turn is for, its username)``, as the
        claim named it (``for``): the channel's own account, or a co-author
        the agent serves there."""
        return context["for"]["id"], context["for"]["username"]

    def lent(self, context: dict) -> bool:
        """Whether the turn is a co-author's: for another account there than
        the channel's."""
        return context["for"]["id"] != self.account

    def serves(self, agent: str, person: str) -> bool:
        """Whether the agent's owner still lets ``person`` of that server's
        workspace use it (``store.named``)."""
        from ..computers import channel

        link = channel.get(self.link_id)
        return link is not None and store.named(agent, link["remote_url"], link["workspace_id"], person)

    def editor(self, user_id: str) -> bool:
        """That server refuses the claim of an account that cannot edit."""
        return True

    def cannot_write(self) -> str:
        """That server refuses the writes itself (read-only, a workspace
        being deleted)."""
        return ""

    def where(self, turning, place: dict, context: dict) -> tuple[dict, list[dict]]:
        """``(what the conversation is about, the person's selections for
        the prompt)``: what the claim said of the bucket, a page of a vault
        here named by the path the vault's rows give it (else the claim's),
        and in a vault each selection's source line computed here from the
        PDF and the sources on this disk (``annotations.sources.source_in_vault``),
        on the version the selection was made on (its ``doc_id`` when that
        server names it, else the one the vault read: the page as it is
        shown); none elsewhere."""
        from ..annotations import entries, sources
        from ..vault import kinds, store as vault_store

        about = {k: self.about[k] for k in ("kind", "id", "title", "name", "format", "path", "work_folder")
                 if isinstance(self.about.get(k), str)}
        about.setdefault("kind", "page")
        about["w"] = ""
        link = place["link"]
        if link is None:
            return about, [{"page": c.get("page"), "quote": c.get("text") or "", "source": None}
                           for c in context["selections"]]
        files = vault_store.files(link["id"])
        root = Path(link["path"])
        shown = [row.get("hash") for row in files.values() if row.get("page_id") == turning.bucket
                 and row.get("kind") == kinds.PDF.name and not row.get("was")]
        selections = []
        for chosen in context["selections"]:
            source = None
            if chosen.get("page") and chosen.get("box"):
                doc_id = chosen.get("doc_id") or (shown[0] if len(shown) == 1 else "")
                at = {"page_id": turning.bucket, "kind": "passage", "page": chosen["page"],
                      "rects": [chosen["box"]], "doc_id": doc_id}
                try:
                    source = sources.source_in_vault(files, root, at)
                except Exception as e:  # noqa: BLE001 — a selection without its line is searched for
                    log.warning(f"[agents] the source of a selection on page {turning.bucket}: {e}")
            selections.append({"page": chosen.get("page"), "quote": chosen.get("text") or "", "source": source})
        if about["kind"] == "page":
            about["path"] = entries.paths_of(files).get(turning.bucket) or about.get("path") or about.get("title", "")
            if not about["path"]:
                turning._fail("This page is no longer a file of the synced folder.")
        about["work_folder"] = about.get("work_folder") or root.name
        return about, selections

    def thread(self, turning, place: dict, context: dict) -> dict | None:
        """The thread a thread's turn answers, as the claim gave it: in a
        vault named by its file's path on this disk with its source line
        computed here (``_on_disk``), its region's pictures fetched
        (``_pictures``); None when the claim gave none."""
        from ..annotations import entries
        from ..vault import store as vault_store

        found = context.get("thread")
        if found is None or place["link"] is None:
            return found
        files = vault_store.files(place["link"]["id"])
        root = Path(place["path"])
        thread = _on_disk(found, files, entries.paths_of(files), root)
        self._pictures(thread, root)
        return thread

    def _pictures(self, thread: dict, root: Path) -> None:
        """The thread's region pictures (now, and as it was commented on)
        fetched into the directory's ``.gamma/annotations/img/<key>.jpg``,
        each once (write-once by key, as the annotation files' are), the
        ``.gamma/annotations/.gitignore`` a local work folder's annotation
        files carry written first, so a repository there never lists them;
        one not fetched is left out of the prompt."""
        from ..annotations import entries, files
        from ..vault import disk as vault_disk

        root = Path(root)
        ignored = (root / files.DIR / ".gitignore").is_file()
        for at, field in (("now", "picture"), ("before", "before")):
            key = entries.key_of(thread.get(field) if isinstance(thread.get(field), str) else None)
            if not key or not _KEY.match(key):
                continue
            rel = f"{files.IMG}/{key}.jpg"
            try:
                if vault_disk.lstat(root, rel) is not None:
                    continue
            except OSError as e:   # Outside: a link on the way
                log.info(f"[agents] the picture of thread {thread['id']}: {e}")
                continue
            try:
                status, data = self._ask("GET", f"/api/computers/threads/{quote(thread['id'], safe='')}/picture?"
                                         + urlencode({"at": at}), retry=False)
            except gamma_sync.SyncError as e:
                log.info(f"[agents] the picture of thread {thread['id']}: {e}")
                continue
            if status != 200 or not data or not data.startswith(files.JPEG) or len(data) > files.MAX_PICTURE:
                continue
            if not ignored:
                try:
                    ignored = vault_disk.write_own(root, f"{files.DIR}/.gitignore", files.GITIGNORE)
                except (OSError, vault_disk.Offline) as e:
                    log.info(f"[agents] the annotation files' .gitignore of channel {self.link_id}: {e}")
                    ignored = True   # asked once a turn
            try:
                vault_disk.write_own(root, rel, data)   # False: a reader held it, left out of this prompt
            except (OSError, vault_disk.Offline, vault_disk.Stopped) as e:   # Outside is an OSError
                log.info(f"[agents] the picture of thread {thread['id']}: {e}")

    def write(self, turning, fields: dict, *, last: bool = False) -> bool:
        """The turn's agent message takes ``fields`` there (``CHAT_FIELDS``
        only), under the next ``seq`` → whether that server still gives the
        turn to this run (False: stopped there, taken, its conversation
        gone: the turn stops). A write that cannot reach that server is
        lost (the next one carries the whole message; ``missed`` says so,
        until one is written; one refused there is not, nor tried again
        but by the next change), unless it is the ``last``: tried again for
        ``RETRY_S``, then ``gamma_sync.Unreachable`` (the turn stays open,
        failed there later); ``Refused`` for another refusal of the
        last."""
        if self.lost:
            return False
        if last and self.gave_up:
            raise gamma_sync.Unreachable("the other server was not reached")
        body = {"run": self.run_id, "seq": self._next(),
                "fields": {k: v for k, v in fields.items() if k in CHAT_FIELDS}}
        try:
            self._json("POST", self._chats("write"), body, retry=last)
        except Refused as e:
            if e.status in (404, 409):
                self.lost = True
                return False
            if last:
                raise
            self.missed = False          # refused, not lost: tried again it is refused again
            log.warning(f"[agents] a write of chat turn {self.turn_id} refused there: {e}")
            return True
        except gamma_sync.SyncError as e:
            if last:
                self.gave_up = True
                raise
            self.missed = True
            log.info(f"[agents] a write of chat turn {self.turn_id}: {e}")
            return True
        self.missed = False
        return True

    def fail_left_open(self, turn: dict, *, state: str = "failed") -> bool:
        """The turn ``turn`` (``store.open_turns``' row) a server that
        stopped left open, or whose end never reached that server, fails
        there ("Gamma stopped before Codex finished."), or, ``state``
        stopped, ends stopped there (a Stop there of a turn this computer
        no longer runs) → whether it is settled: ended, or no longer this
        run's there, or its channel gone (forgotten); False when that server
        cannot be reached (the channel's next announce or listing there, or
        the next start, tries again)."""
        if not self._connect():
            return True
        fields = {"state": state, "ended_at": page_now(), "waiting": False}
        if state == "failed":
            fields["error"] = f"Gamma stopped before {catalog.label(turn['agent'])} finished."
        try:
            self._json("POST", self._chats("write"), {"run": self.run_id, "seq": self._next(), "fields": fields},
                       retry=False)
        except Refused as e:
            log.info(f"[agents] the chat turn {self.turn_id} left open: {e}")
        except gamma_sync.SyncError as e:
            log.warning(f"[agents] the chat turn {self.turn_id} left open, its server not reached: {e}")
            return False
        return True
