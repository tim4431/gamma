"""Where a turn's conversation lives, as a turn of the runner sees it
(gamma/local_agents/runner.py ``_ChatTurn``; docs/dev/local_agents.md):
``chat_home_for`` gives a turn its home.

``LocalChatHome`` is a bucket of this server (a page, a folder, the
library), its conversation in this server's pages.db (``chats.reply_of``
and ``write_reply``, the checks its route made, where it works:
``chats.place_of``). ``HttpChatHome`` is a page of a work folder of another
Gamma server (a NAS; step 4d of docs/research/agent-collab.md), spoken to
with this computer's token of that server, workspace and account
(gamma/computers/channel.py ``of_vault``), whose conversation that server
keeps and writes:

- **The claim** (``POST /api/computers/chats/{turn}/claim {run, agent}``)
  takes the queued turn for this computer's run of it (``chat_run_id``: one
  id a turn, so a claim retried after a lost answer, or after this server
  restarted, holds it) → the message, its selections, the conversation's
  last messages, the model and effort it asks for, the page's path and
  title, and for a comment thread's turn the thread (its entry with its PDF
  place, ``_on_disk``: named by its file's path on this disk, its source
  line computed here from the PDF and the sources on this disk,
  ``annotations.sources.source_in_vault``), its region's pictures fetched
  (``GET /api/computers/threads/{id}/picture?at=now|before``) into the
  directory's ``.gamma/annotations/img/``, where the prompt finds a local
  work folder's (``prompt.picture_paths``). That server decided whose turn
  it is (the token's account, an editor there); 409 or 404: nothing to run,
  nothing written. The turn's end written there answers the thread there
  (gamma/local_agents/threads.py ``settle``): this computer holds no token
  of that server (step 4.2).
- **The writes** (``POST .../write {run, seq, fields}``): the agent's
  message as the turn goes, at most every ``WRITE_EVERY_S`` (a write lost
  on the way is carried by the next, which holds the whole message), the
  last tried again for ``RETRY_S`` and the turn left open past it (the
  channel's next announce, or the next start, fails it there). Each write's
  ``seq`` is above the last one sent and never below the clock in
  microseconds, so one late or retried, or sent by this computer before it
  restarted, never undoes a newer one. Only the message's fields go, its
  paths relative to the folder; a 409 or 404 (stopped there, taken by
  another run, the conversation gone) stops the turn.
- **Source lines** of the selections are computed here, as the thread's
  (``annotations.sources.source_in_vault``).

A request that cannot reach that server (or that it fails, 5xx) is tried
again for ``RETRY_S`` where it is the turn's last; any other refusal raises
at once (``Refused``). A turn there asks only on this computer (its agent's
``ask``, runner.py ``ask_refusal``): what it asks is drawn and answered in
the desktop app's window, never written there; the message says
``waiting`` while an ask waits, and a write that did not reach that server
is said (``missed``), so the flush tries it again.
"""

import base64
import hashlib
import json
import re
import time
from pathlib import Path
from urllib.parse import quote, urlencode

from .. import gamma_sync
from ..db import account_names, connect_pages_db, connect_users_db, page_now
from ..logbuf import log
from . import catalog, chats, events, store

RETRY_S = 180          # how long a request that cannot reach the other server is tried again
RETRY_FIRST_S = 2      # the first wait between two tries, doubling
RETRY_MAX_S = 30       # up to this
TIMEOUT_S = 60         # one request's
WRITE_EVERY_S = 1.0    # a chat turn's message is written to the other server at most this often
_KEY = re.compile(r"^[A-Za-z0-9_-]{1,64}$")   # a picture's key (``annotations.pictures.key``)
# The agent message's fields a chat turn writes to the other server: never ``asks``, what a turn there asks
# stays on this computer; ``waiting`` says that one waits (runner.py ``_progress``).
CHAT_FIELDS = ("text", "steps", "changes", "changed", "built", "error", "fresh", "state", "ended_at", "model",
               "effort", "waiting")


class Refused(Exception):
    """The other server refused a request of the turn: ``status``, its
    ``detail``."""

    def __init__(self, status: int, detail: str):
        super().__init__(f"{status}: {detail}")
        self.status, self.detail = status, detail


# --- another server's work folder -------------------------------------------------------

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


class _Remote:
    """A home of another server's work folder: the vault ``link_id``'s
    channel there, its requests tried again while that server cannot be
    reached."""

    remote = True

    def __init__(self, link_id: str):
        from .. import folder_links  # local: folder_links imports this package's modules inside functions

        link = folder_links.get_link(link_id)
        self.link_id = link_id
        self.ws = link["workspace_id"] if link else ""
        self.server: gamma_sync.Server | None = None
        self.username = ""               # the account there the channel is of
        self.account = ""                # its id there

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

    def _connect(self, link: dict) -> bool:
        """The client of the channel ``link`` is announced on, False for a
        vault whose computer is not shown there (no channel)."""
        from ..computers import channel  # local: it imports folder_links, which imports this package

        keyed = channel.of_vault(link)
        if keyed is None or not keyed["token"]:
            return False
        self.server = gamma_sync.Server(link["remote_url"], keyed["token"], timeout=TIMEOUT_S)
        self.username, self.account = keyed["username"], keyed["account"]
        return True


# --- a turn of the chat's computer side ------------------------------------------------------

def chat_home_for(ws: str, link_id: str, turn_id: str):
    """The home of a turn keyed ``(ws, turn id)``, of the vault ``link_id``
    ("" for none): a remote turn's key names its vault in place of the
    workspace (``runner.queue_remote_chat``), so it never reads as a turn of
    a workspace here."""
    return HttpChatHome(link_id, turn_id) if ws == link_id else LocalChatHome(ws)


def chat_run_id(turn_id: str) -> str:
    """The id of this computer's run of the chat turn ``turn_id`` of another
    server, the same on every claim of it (a retry, a restart), so a claim
    whose answer was lost holds it and a turn left open is failed there by
    the run that holds it; a run's id's shape (22 characters of
    ``[A-Za-z0-9_-]``)."""
    digest = hashlib.sha256(f"chat-turn\n{turn_id}".encode("utf-8")).digest()
    return base64.urlsafe_b64encode(digest[:16]).decode("ascii").rstrip("=")


def _earlier(messages: list[dict], at: int, person: dict) -> list[dict]:
    """The conversation's last six messages before the turn's (``at``), the
    person's own aside, as the prompt takes them: ``{role: owner | agent,
    text}``."""
    return [{"role": "owner" if m.get("role") == "user" else "agent", "text": m.get("text") or ""}
            for m in messages[:at] if m is not person and (m.get("text") or "").strip()][-6:]


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
                "selections": person.get("selections") or [], "earlier": _earlier(messages, at, person),
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
        from .runner import _cannot_write

        return _cannot_write(self.ws)

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

    def vault_file(self, turning, link: dict) -> str:
        return _vault_file(turning, link)

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


def _vault_file(turning, link: dict) -> str:
    """The page's path in the vault as this computer's rows of it name it,
    "" when they name none: what an ask on this computer names, never a
    word the other server gave."""
    from ..annotations import entries
    from ..vault import store as vault_store

    return entries.paths_of(vault_store.files(link["id"])).get(turning.bucket) or ""


class HttpChatHome(_Remote):
    """A page of a work folder of another server, over this computer's
    channel there (the module doc)."""

    def __init__(self, link_id: str, turn_id: str):
        super().__init__(link_id)
        self.turn_id = turn_id
        self.run_id = chat_run_id(turn_id)
        self.seq = 0                     # the last write's
        self.lost = False                # that server no longer gives the turn to this run
        self.gave_up = False             # the last write could not reach it
        self.unsure = False              # the claim was sent but never answered: that server may hold it
        self.missed = False              # the last write did not reach that server: the next one carries it
        self.page: dict = {}             # the claim's page: {path, title}

    def _chats(self, what: str) -> str:
        return f"/api/computers/chats/{quote(self.turn_id, safe='')}/{what}"

    def _next(self) -> int:
        """The next write's ``seq``: above the last one and never below the
        clock in microseconds, so a write this computer sent before it
        restarted is never newer."""
        self.seq = max(self.seq + 1, time.time_ns() // 1000)
        return self.seq

    def claim(self, turning) -> dict | None:
        """The vault and its agent checked, the turn taken there for this
        computer's run → its context (``{text, selections, earlier, title,
        thread_id, thread, for}``, a thread's turn's thread as the claim gave
        it, ``for`` the account there the turn is for, ``{id, username}``; the
        turn's agent, conversation, page, model and effort set on
        ``turning``), recorded here (``store.claimed_turn``); None when it is
        not this run's: stopped while it waited, the vault gone or no work
        folder of that server, its agent off (the turn failed there with
        why, ``_turned_off``), its computer not shown there,
        the turn taken by another run, no longer queued or not this
        computer's, that server not reached (``unsure``: it may hold the
        turn for this run, which stays recorded open for it to be failed
        there later)."""
        from .. import folder_links  # local: folder_links imports this package's modules inside functions

        if turning.stop.is_set():
            return None
        link = folder_links.get_link(self.link_id)
        if link is None or not self.kept(link):
            return None
        agent = store.agent_for(link["created_by"])
        if agent is None:
            self._turned_off(turning, link)
            return None
        if not self._connect(link):
            log.warning(f"[agents] a chat turn of vault {self.link_id} not taken: show this computer on "
                        f"{folder_links.server_name(link)} first")
            return None
        turning.agent, turning.label = agent["agent"], catalog.label(agent["agent"])
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
        turning.bucket = str(turn.get("page_id") or turning.bucket)
        turning.model, turning.effort = str(turn.get("model") or ""), str(turn.get("effort") or "")
        store.claimed_turn(self.turn_id, link_id=self.link_id, conversation_id=turning.conversation_id,
                           page_id=turning.bucket, agent=turning.agent)
        self.page = answer["page"] if isinstance(answer.get("page"), dict) else {}
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
                "earlier": earlier[-6:], "title": chats.title_of(str(turn.get("title") or "")),
                "thread_id": thread_id, "thread": thread if thread_id and thread and thread.get("id") == thread_id
                else None, "for": {"id": str(person.get("id") or self.account),
                                   "username": events.one_line(str(person.get("username") or self.username), 64)}}

    def _turned_off(self, turning, link: dict) -> None:
        """The turn's agent was turned off while the turn waited here: the
        turn taken there by this run and failed with why ("Codex was turned
        off on this computer."), as a turn of this server's page fails, never
        left waiting there; nothing when that server no longer gives it to
        this computer (its announce without the agent came first) or the
        vault's computer is not shown there. Recorded here once taken, so an
        end that never reached that server is failed there later
        (``unsure``)."""
        if turning.agent not in catalog.AGENTS or not self._connect(link):
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

    def kept(self, link: dict) -> bool:
        from .. import folder_links  # local: folder_links imports this package's modules inside functions

        return bool(link["remote_url"]) and folder_links.work_vault(link)

    def place(self, turning, context: dict) -> dict:
        """Where the turn works: the vault's directory, while it keeps a
        work folder of that server (else the turn fails)."""
        from .. import folder_links  # local: folder_links imports this package's modules inside functions

        link = folder_links.get_link(self.link_id)
        if link is None or not self.kept(link):
            turning._fail("The synced folder is no longer kept on this computer.")
        return {"kind": "vault", "dir": "", "path": link["path"], "name": Path(link["path"]).name, "link": link}

    def whose(self, link: dict, context: dict) -> str:
        """The account here whose channel took the turn, which made the
        vault: that server decided at the claim it is a turn for the token's
        account's agent."""
        return link["created_by"]

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
        from .. import folder_links  # local: folder_links imports this package's modules inside functions

        link = folder_links.get_link(self.link_id)
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
        the prompt)``: the page, its path the one the vault's rows give it
        (else the claim's), and each selection's source line computed here
        from the PDF and the sources on this disk
        (``annotations.sources.source_in_vault``), on the version the selection
        was made on (its ``doc_id`` when that server names it, else the one
        the vault read: the page as it is shown)."""
        from ..annotations import entries, sources
        from ..vault import kinds, store as vault_store

        link = place["link"]
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
        file = (entries.paths_of(files).get(turning.bucket) or str(self.page.get("path") or "")
                or str(self.page.get("title") or ""))
        if not file:
            turning._fail("This page is no longer a file of the synced folder.")
        about = {"kind": "page", "id": turning.bucket, "title": str(self.page.get("title") or ""), "w": "",
                 "path": file, "work_folder": str(self.page.get("folder") or "") or root.name}
        return about, selections

    def vault_file(self, turning, link: dict) -> str:
        """The page's path from this computer's vault rows alone, "" for
        none (``where``'s fallback to the claim's path or title is the
        prompt's): what an ask on this computer names."""
        return _vault_file(turning, link)

    def thread(self, turning, place: dict, context: dict) -> dict | None:
        """The thread a thread's turn answers, as the claim gave it, named
        by its file's path on this disk with its source line computed here
        (``_on_disk``), its region's pictures fetched (``_pictures``); None
        when the claim gave none."""
        from ..annotations import entries
        from ..vault import store as vault_store

        found = context.get("thread")
        if found is None:
            return None
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
                    log.info(f"[agents] the annotation files' .gitignore of vault {self.link_id}: {e}")
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
        run's there, or its vault or its computer's channel gone
        (forgotten); False when that server cannot be reached (the
        channel's next announce or listing there, or the next start, tries
        again)."""
        from .. import folder_links  # local: folder_links imports this package's modules inside functions

        link = folder_links.get_link(self.link_id)
        if link is None or not link["remote_url"] or not self._connect(link):
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
