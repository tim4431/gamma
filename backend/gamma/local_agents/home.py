"""Where a Send's request lives, as a run of the runner sees it
(gamma/local_agents/runner.py ``_Run``; docs/dev/local_agents.md): the
request, its claim, its progress and its end, its entries, the comments
said since an earlier run and the answers. ``home_for`` gives a run its
home.

``LocalHome`` is a work folder of this server: the runner's in-process reads
and writes of its pages.db, as they always were (``requests.claim`` and
``set_state``, ``requests.entries_of``, the comments, ``feedback.agent``'s
``reply`` and ``resolve`` signed ``local:<agent>``).

``HttpHome`` is a work folder of another Gamma server (a NAS; step 4c of
docs/research/agent-collab.md), spoken to with this computer's token of that
server, workspace and account (gamma/computers/channel.py ``of_vault``).
That server decides whose Send it is and writes the answers; this computer
runs it:

- **The claim** (``POST /api/computers/sends/{id}/claim {run, agent}``)
  takes the request for this run, its id minted here (``run_id``), so a
  claim retried after a lost answer is told it holds it; the answer is the
  request, its entries (each with its place's page, box, rects and
  ``doc_id``), ``seen`` (that server's clock when it read them: the
  requests made since are the next run's, whatever this computer's clock
  says) and each thread's last speaker. 409 (another run holds it, or it
  is no longer sent) or 404 (not this computer's): nothing to run.
- **The entries** are named by their files' paths on this disk (the vault's
  rows, ``feedback.entries.paths_of``), their source lines computed here
  from the PDF and the sources on this disk (``feedback.sources.
  source_in_vault``), the region pictures of the ones put to the agent
  fetched (``GET /api/computers/threads/{id}/picture?at=now|before``) into
  the directory's ``.gamma/feedback/img/``, where the prompt finds a local
  work folder's (``prompt.picture_paths``).
- **The progress** (``POST .../progress {run, detail}``) and **the end with
  the answers** (``POST .../answers {run, replies, state, summary, changed,
  error?}``): the replies, held until the end, are written by that server
  as the agent's (``feedback.agent.reply`` with ``local:<agent>`` and the
  claim's ``seen``), each with an id minted here once per run and thread
  (``reply_id``), so a retry writes none twice; ``changed`` names only the
  files the vault tracks, relative to the folder.

A request that server no longer gives this run (409, 404: withdrawn, failed
by its maker) is never written again (``lost``). A write that cannot reach
that server (or that it fails, 5xx) is tried again for ``RETRY_S``, then
raises: the run's row stays open (a claim never answered too: ``unsure``),
and the channel's next announce there, or the next start, fails it there
(``fail_left_open``). Any other refusal raises at once (``Refused``).

A turn of the chat's computer side has a home too (``chat_home_for``, the
runner's ``_ChatTurn``): ``LocalChatHome`` is a page of this server, its
conversation in this server's pages.db (``chats.reply_of`` and
``write_reply``, the checks its route made), as it always was;
``HttpChatHome`` a page of a work folder of another server (step 4d), whose
conversation that server keeps and writes:

- **The claim** (``POST /api/computers/chats/{turn}/claim {run, agent}``)
  takes the queued turn for this computer's run of it (``chat_run_id``: one
  id a turn, so a claim retried after a lost answer, or after this server
  restarted, holds it) → the message, its selections, the conversation's
  last messages, the model and effort it asks for, the page's path and
  title. That server decided whose turn it is (the token's account, an
  editor there); 409 or 404: nothing to run, nothing written.
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
- **Source lines** of the selections are computed here, as a Send's
  entries' (``feedback.sources.source_in_vault``).

A turn there never asks (the driver starts without ``ask``).
"""

import base64
import hashlib
import json
import re
import time
import uuid
from pathlib import Path
from urllib.parse import quote, urlencode

from .. import gamma_sync
from ..db import account_names, connect_pages_db, connect_users_db, page_now
from ..feedback import requests
from ..feedback.entries import FeedbackError
from ..logbuf import log
from . import catalog, chats, store

RETRY_S = 180          # how long a request that cannot reach the other server is tried again
RETRY_FIRST_S = 2      # the first wait between two tries, doubling
RETRY_MAX_S = 30       # up to this
TIMEOUT_S = 60         # one request's
WRITE_EVERY_S = 1.0    # a chat turn's message is written to the other server at most this often
_KEY = re.compile(r"^[A-Za-z0-9_-]{1,64}$")   # a picture's key (``feedback.pictures.key``)
_MAX_PICTURE = 16 * 1024 * 1024               # bytes of one picture fetched
# The agent message's fields a chat turn writes to the other server: never ``asks`` (a turn there never asks).
CHAT_FIELDS = ("text", "steps", "changes", "changed", "built", "error", "fresh", "state", "ended_at", "model",
               "effort")


def home_for(ws: str, link_id: str):
    """The home of a run keyed ``(ws, "send", id)`` in the vault
    ``link_id``'s queue: a remote run's key names its vault in place of the
    workspace (``runner.queue_remote``), so it never reads as a local
    workspace's request."""
    return HttpHome(link_id) if ws == link_id else LocalHome(ws)


class Refused(Exception):
    """The other server refused a request of the run: ``status``, its
    ``detail``."""

    def __init__(self, status: int, detail: str):
        super().__init__(f"{status}: {detail}")
        self.status, self.detail = status, detail


# --- this server's work folder -------------------------------------------------------

class LocalHome:
    """A work folder of this server: the runner's in-process calls."""

    remote = False
    run_id = ""         # the store mints the run's id once it is claimed
    unsure = False      # a claim not taken is never this run's

    def __init__(self, ws: str):
        self.ws = ws

    def claim(self, run) -> dict | None:
        """The vault, its agent and its maker checked again, the request
        taken (``requests.claim``) → the request, None when it is not this
        run's (``run``'s link, agent, label and maker set on the way)."""
        from .. import workspaces
        from .runner import _ours, _request_row

        row = _request_row(self.ws, run.request_id)
        if row is None or row[0] != "sent":
            return None
        link = requests._local_link(self.ws, row[3])
        if link is None or link["id"] != run.link_id or not _ours(link, row[2], row[4]):
            return None
        agent = store.agent_of(link["id"])
        if agent is None or agent["agent"] not in catalog.AGENTS:
            return None
        if not workspaces.at_least(workspaces.role_of(self.ws, link["created_by"]), "editor"):
            return None
        run.link, run.agent, run.label = link, agent["agent"], catalog.label(agent["agent"])
        run.maker = link["created_by"]
        with connect_users_db() as users:
            run.maker_name = account_names(users, {run.maker}).get(run.maker, "")
        run.run["agent"] = run.agent
        with connect_pages_db(self.ws) as conn:
            return requests.claim(self.ws, conn, run.request_id, by_name=run.label, detail=f"Starting {run.label}")

    def withdrawn(self, run) -> bool:
        """Whether the request is no longer running."""
        from .runner import _request_row

        row = _request_row(self.ws, run.request_id)
        return row is None or row[0] != "running"

    def write(self, run, state: str, detail: str) -> bool:
        """The request ``state`` with ``detail``, as the agent, only while
        it is running → whether it was written (False: withdrawn
        meanwhile)."""
        try:
            with connect_pages_db(self.ws) as conn:
                requests.set_state(self.ws, conn, run.request_id, state, detail=detail, by_name=run.label,
                                   writer="agent", only_from=("running",))
            return True
        except FeedbackError as e:
            if e.status == 409 or e.status == 404:
                return False
            raise

    def cannot_write(self) -> str:
        from .runner import _cannot_write

        return _cannot_write(self.ws)

    def entries(self, run, files: dict) -> tuple[str, list[dict]]:
        """``(when the run read them, the request's entries)``."""
        from ..feedback import entries

        seen_at = page_now()
        with connect_pages_db(self.ws) as conn:
            return seen_at, requests.entries_of(self.ws, conn, run.record, paths=entries.paths_of(files))

    def spoken_after(self, since: dict[str, tuple[str, set[str]]]) -> set[str]:
        from .runner import _spoken_after

        return _spoken_after(self.ws, since)

    def pictures(self, todo: list[dict], root: Path) -> None:
        """The feedback files' pass writes a local work folder's."""

    def _connection(self, run):
        from .. import comments
        from ..integrations import Connection

        # Signed as the agent's label; its id ``local:<name>`` makes the
        # comment read as an agent on this computer's (comments.LOCAL_AGENT).
        return Connection(user_id=run.maker, workspaces=[self.ws], scope="feedback",
                          token_id=f"{comments.LOCAL_AGENT}{run.agent}", name=run.label, username=run.maker_name)

    def reply(self, run, thread_id: str, body: str) -> None:
        """The answer written as the agent's reply, saying when the run read
        the thread (``seen``), and its thread's session recorded.
        ValueError for a refusal."""
        from ..blocks_store import new_block_id
        from ..feedback import agent as feedback_agent

        _, out = feedback_agent.reply(self._connection(run), self.ws, "", {"thread_id": thread_id, "body": body,
                                                                           "id": new_block_id()}, seen=run.seen_at)
        run._answered(thread_id, out["comment_id"])

    def resolve(self, run, thread_id: str) -> None:
        """The fixed thread resolved, unless a person said in it what the
        run did not read. ValueError for a refusal."""
        from ..feedback import agent as feedback_agent
        from .runner import _spoken_after

        unread = _spoken_after(self.ws, {thread_id: (run.seen_at, set(run.read.get(thread_id, ())))})
        if not unread:
            feedback_agent.resolve(self._connection(run), self.ws, "", {"thread_id": thread_id}, seen=run.seen_at)


# --- another server's work folder -------------------------------------------------------

def reply_id(run_id: str, thread_id: str) -> str:
    """The id of the run's reply in the thread, the same on every try (a
    block id's shape: ``blocks_store.new_block_id``'s length)."""
    digest = hashlib.sha256(f"{run_id}\n{thread_id}".encode("utf-8")).digest()
    return base64.urlsafe_b64encode(digest[:9]).decode("ascii")


def _place(entry: dict) -> dict:
    """The entry's place (that server's ``place``: ``{page, box, rects,
    doc_id}``) as ``sources.source_in_vault`` asks it."""
    place = entry.get("place") if isinstance(entry.get("place"), dict) else {}
    return {**place, "page_id": entry.get("page_id"), "kind": entry.get("kind")}


def _speakers(threads) -> dict[str, dict]:
    """The claim's ``threads`` (``[{id, by_agent, at, people: [{id,
    at}]}]``, who spoke last in each and the comments its people wrote) by
    thread id."""
    return {last["id"]: last for last in threads if isinstance(last, dict) and isinstance(last.get("id"), str)} \
        if isinstance(threads, list) else {}


class _Remote:
    """What the homes of another server's work folder share: the vault
    ``link_id``'s channel there, its requests tried again while that server
    cannot be reached."""

    remote = True

    def __init__(self, link_id: str):
        from .. import folder_links  # local: folder_links imports this package's modules inside functions

        link = folder_links.get_link(link_id)
        self.link_id = link_id
        self.ws = link["workspace_id"] if link else ""
        self.server: gamma_sync.Server | None = None
        self.username = ""               # the account there the channel is of

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
        self.username = keyed["username"]
        return True


class HttpHome(_Remote):
    """A work folder of another server, over this computer's channel there
    (the module doc)."""

    def __init__(self, link_id: str):
        super().__init__(link_id)
        self.run_id = uuid.uuid4().hex   # what the claim is keyed by there
        self.seen = ""                   # that server's clock when the claim read the entries
        self.lost = False                # that server no longer gives the request to this run
        self.gave_up = False             # the end could not reach it
        self.unsure = False              # the claim was sent but never answered: that server may hold it
        self.found: list[dict] = []      # the claim's entries
        self.speakers: dict[str, dict] = {}
        self.staged: list[dict] = []     # the replies the end carries

    # --- the requests ----------------------------------------------------------------

    def _sends(self, request_id: str, what: str) -> str:
        return f"/api/computers/sends/{quote(request_id, safe='')}/{what}"

    # --- the run's steps ---------------------------------------------------------------

    def claim(self, run) -> dict | None:
        """The vault and its agent checked again, the request taken there
        for this run → the request, None when it is not this run's: the
        vault paused, choosing its files, no work folder of that server any
        more, its agent off, its computer not shown there ("Show this
        computer on NAS first."), the request taken by another run or no
        longer sent, that server not reached (``unsure``: it may have taken
        the request for this run, which the runner records open for it to
        be failed there later)."""
        from .. import folder_links  # local: folder_links imports this package's modules inside functions

        link = folder_links.get_link(self.link_id)
        if (link is None or not link["remote_url"] or not folder_links.work_vault(link)
                or link["status"].get(folder_links.PAUSED) or folder_links.choosing(link)):
            return None
        agent = store.agent_of(link["id"])
        if agent is None or agent["agent"] not in catalog.AGENTS:
            return None
        if not self._connect(link):
            log.warning(f"[agents] a Send of vault {self.link_id} not taken: show this computer on "
                        f"{folder_links.server_name(link)} first")
            return None
        run.link, run.agent, run.label = link, agent["agent"], catalog.label(agent["agent"])
        run.run["agent"] = run.agent
        run.run["run_id"] = self.run_id
        try:
            answer = self._json("POST", self._sends(run.request_id, "claim"), {"run": self.run_id, "agent": run.agent})
        except Refused as e:
            if e.status not in (404, 409):
                log.warning(f"[agents] the claim of request {run.request_id} refused there: {e}")
            return None
        except gamma_sync.SyncError as e:
            log.warning(f"[agents] the claim of request {run.request_id}: {e}")
            self.unsure = True
            return None
        record = answer.get("request")
        if not isinstance(record, dict):
            return None
        self.found = [e for e in answer.get("entries") or [] if isinstance(e, dict) and isinstance(e.get("id"), str)]
        self.speakers = _speakers(answer.get("threads"))
        self.seen = str(answer.get("seen") or "")
        run.maker = link["created_by"]
        run.maker_name = str(record.get("by") or self.username or "")
        return record

    def withdrawn(self, run) -> bool:
        """Whether that server refused a write of the run: it no longer
        gives the request to it (a withdraw the channel heard of sets the
        run's reason itself)."""
        return self.lost

    def cannot_write(self) -> str:
        """That server refuses the answers itself (read-only, a workspace
        being deleted)."""
        return ""

    def write(self, run, state: str, detail: str) -> bool:
        """``running``: the progress ``detail``, while the run holds the
        request (a refusal: False, the run stops; that server not reached:
        the line is lost, the run goes on). ``done`` or ``failed``: the
        end, with the replies held (``reply``), ``detail`` its summary or
        its error; each reply's thread recorded once it is written
        (``run._answered``) → whether it was written (False: the run no
        longer holds the request). ``gamma_sync.Unreachable`` past
        ``RETRY_S``, ``Refused`` for another refusal."""
        if self.lost:
            return False
        if state == "running":
            try:
                self._json("POST", self._sends(run.request_id, "progress"), {"run": self.run_id, "detail": detail},
                           retry=False)
                return True
            except Refused as e:
                if e.status in (404, 409):
                    self.lost = True
                    return False
                log.warning(f"[agents] the progress of request {run.request_id} refused there: {e}")
                return True
            except gamma_sync.SyncError as e:
                log.info(f"[agents] the progress of request {run.request_id}: {e}")
                return True
        # That server names the files changed after the summary (the first
        # five): only those its members can see, the ones the vault tracks.
        changed = [path for path in run.changes if path.casefold() in run.tracked]
        body = {"run": self.run_id, "replies": self.staged, "state": state,
                "summary": (run.summary or detail) if state == "done" else "", "changed": changed}
        if state != "done":
            body["error"] = detail
        if self.gave_up:   # the end was tried for RETRY_S already: the next announce or start fails it
            raise gamma_sync.Unreachable("the other server was not reached")
        try:
            self._json("POST", self._sends(run.request_id, "answers"), body)
        except Refused as e:
            if e.status in (404, 409):
                self.lost = True
                return False
            raise
        except gamma_sync.SyncError:
            self.gave_up = True
            raise
        for staged in self.staged:
            run._answered(staged["thread_id"], staged["id"])
        self.staged = []
        return True

    def entries(self, run, files: dict) -> tuple[str, list[dict]]:
        """``(the claim's seen, its entries)``, each named by its file's path
        on this disk and, open, with its source line computed here."""
        from ..feedback import entries, sources

        paths = entries.paths_of(files)
        root = Path(run.link["path"])
        out = []
        for found in self.found:
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
            out.append(entry)
        return self.seen, out

    def spoken_after(self, since: dict[str, tuple[str, set[str]]]) -> set[str]:
        """``runner._spoken_after`` from the claim's ``threads``: those of
        ``since`` in which a person (no token, no agent) has a live comment
        the earlier run did not read, or edited one after it read (both
        times that server's)."""
        out = set()
        for thread_id, (at, read) in since.items():
            people = (self.speakers.get(thread_id) or {}).get("people")
            if at and isinstance(people, list) and any(
                    isinstance(p, dict) and (p.get("id") not in read or str(p.get("at") or "") > at) for p in people):
                out.add(thread_id)
        return out

    def pictures(self, todo: list[dict], root: Path) -> None:
        """The region pictures of ``todo`` fetched into the directory's
        ``.gamma/feedback/img/<key>.jpg``, each once (write-once by key, as
        the feedback files' are), the ``.gamma/feedback/.gitignore`` a
        local work folder's feedback files carry written first, so a
        repository there never lists them; one not fetched is left out of
        the prompt."""
        from ..feedback import entries, files
        from ..vault import disk as vault_disk

        folder = Path(root) / files.IMG
        ignored = (Path(root) / files.DIR / ".gitignore").is_file()
        for entry in todo:
            for at, field in (("now", "picture"), ("before", "before")):
                key = entries.key_of(entry.get(field) if isinstance(entry.get(field), str) else None)
                if not key or not _KEY.match(key) or (folder / f"{key}.jpg").is_file():
                    continue
                try:
                    status, data = self._ask("GET", f"/api/computers/threads/{quote(entry['id'], safe='')}/picture?"
                                             + urlencode({"at": at}), retry=False)
                except gamma_sync.SyncError as e:
                    log.info(f"[agents] the picture of thread {entry['id']}: {e}")
                    continue
                if status != 200 or not data or len(data) > _MAX_PICTURE:
                    continue
                if not ignored:
                    try:
                        ignored = vault_disk.write_own(Path(root), f"{files.DIR}/.gitignore", files.GITIGNORE)
                    except (OSError, vault_disk.Offline) as e:
                        log.info(f"[agents] the feedback files' .gitignore of vault {self.link_id}: {e}")
                        ignored = True   # asked once a run
                folder.mkdir(parents=True, exist_ok=True)
                part = folder / f".{key}.{uuid.uuid4().hex[:8]}.part"
                part.write_bytes(data)
                part.replace(folder / f"{key}.jpg")

    def reply(self, run, thread_id: str, body: str) -> None:
        """The answer held for the end (``write``), under an id minted once
        for the run and the thread."""
        self.staged = [r for r in self.staged if r["thread_id"] != thread_id]
        self.staged.append({"id": reply_id(self.run_id, thread_id), "thread_id": thread_id, "text": body,
                            "resolve": False})

    def resolve(self, run, thread_id: str) -> None:
        """The fixed thread's reply asks that server to resolve it, which it
        does unless a person said in it what the run did not read (that
        server knows: ``seen``)."""
        for staged in self.staged:
            if staged["thread_id"] == thread_id:
                staged["resolve"] = True

    # --- the next start ------------------------------------------------------------------

    def fail_left_open(self, run: dict) -> bool:
        """The run ``run`` (``store.open_runs``' row) a server that stopped
        left open fails there, its request too ("Gamma stopped before Codex
        finished.") → whether it is settled: failed, or no longer the run's
        there, or its vault or its computer's channel gone (forgotten);
        False when that server cannot be reached (the channel's next
        announce there, or the next start, tries again)."""
        from .. import folder_links  # local: folder_links imports this package's modules inside functions

        link = folder_links.get_link(self.link_id)
        if link is None or not link["remote_url"] or not self._connect(link):
            return True
        error = f"Gamma stopped before {catalog.label(run['agent'])} finished."
        try:
            self._json("POST", self._sends(run["request_id"], "answers"),
                       {"run": run["id"], "replies": [], "state": "failed", "summary": "", "changed": [],
                        "error": error}, retry=False)
        except Refused as e:
            log.info(f"[agents] the run {run['id']} left open: {e}")
        except gamma_sync.SyncError as e:
            log.warning(f"[agents] the run {run['id']} left open, its server not reached: {e}")
            return False
        return True


# --- a turn of the chat's computer side ------------------------------------------------------

def chat_home_for(ws: str, link_id: str, turn_id: str):
    """The home of a chat turn keyed ``(ws, "chat", turn id)`` in the vault
    ``link_id``'s queue: a remote turn's key names its vault in place of the
    workspace (``runner.queue_remote_chat``), as a remote Send's does."""
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
    """A page of this server: the conversation in its pages.db, the checks
    the turn's route made."""

    remote = False
    run_id = ""         # a local turn is this server's own
    unsure = False      # a turn not found is never this turn's

    def __init__(self, ws: str):
        self.ws = ws

    def claim(self, turning) -> dict | None:
        """The turn's context, ``{user_id, text, selections, earlier}``
        (the conversation's account, the person's message and its
        selections, the conversation's last messages before it), while its
        message reads queued; None when it is gone or ended meanwhile (a
        Stop while it waited for a slot ended its message and its row)."""
        conversation, reply = chats.reply_of(self.ws, turning.conversation_id, turning.turn_id)
        if reply is None or reply.get("state") != "queued":
            return None
        messages = conversation["messages"]
        at = next(i for i, m in enumerate(messages) if m.get("turn_id") == turning.turn_id)
        person = next((m for m in reversed(messages[:at]) if m.get("role") == "user"), None) or {}
        return {"user_id": conversation["user_id"], "text": person.get("text") or "",
                "selections": person.get("selections") or [], "earlier": _earlier(messages, at, person)}

    def kept(self, link: dict) -> bool:
        from .. import folder_links  # local: folder_links imports this package's modules inside functions

        return folder_links.work_folder_link(link)

    def ours(self, link: dict, context: dict) -> bool:
        """Whether the vault is the conversation's account's."""
        return link["created_by"] == context["user_id"]

    def editor(self, link: dict) -> bool:
        """Whether the vault's maker edits the workspace."""
        from .. import workspaces

        return workspaces.at_least(workspaces.role_of(self.ws, link["created_by"]), "editor")

    def cannot_write(self) -> str:
        from .runner import _cannot_write

        return _cannot_write(self.ws)

    def owner(self, link: dict) -> str:
        with connect_users_db() as users:
            return account_names(users, {link["created_by"]}).get(link["created_by"], "") or "the owner"

    def where(self, turning, link: dict, context: dict) -> tuple[str, str, list[dict]]:
        """``(the page's path in the work folder, the work folder's name,
        the person's selections for the prompt)``, each selection ``{page,
        quote, source}``, ``source`` the line its passage maps to in the
        sources (``feedback.sources.source_for``) when its box is known, else
        None."""
        from ..feedback import entries
        from ..vault import store as vault_store
        from ..work_folders.marks import Tree

        with connect_pages_db(self.ws) as conn:
            tree = Tree.read(conn)
            try:
                scope = entries.resolve_scope(conn, turning.page_id, tree)
            except FeedbackError:
                turning._fail("This page is no longer a file of the work folder.")
            chosen = context["selections"]
            selections = [{"page": c.get("page"), "quote": c.get("text") or "", "source": source}
                          for c, source in zip(chosen, chats.sources_of(self.ws, conn, turning.page_id, chosen))]
        file = entries.paths_of(vault_store.files(link["id"])).get(turning.page_id) or scope.name
        return file, tree.names.get(scope.work_folder, "") or Path(link["path"]).name, selections

    def write(self, turning, fields: dict, *, last: bool = False) -> bool:
        """The turn's agent message takes ``fields`` → whether it was
        written (False: the conversation or the message is gone)."""
        return chats.write_reply(self.ws, turning.conversation_id, turning.turn_id, fields)


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
        computer's run → its context (``{text, selections, earlier}``; the
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
        agent = store.agent_of(link["id"])
        if agent is None or agent["agent"] not in catalog.AGENTS:
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
                               page_id=turning.page_id, agent=turning.agent)
            return None
        turning.conversation_id = str(turn.get("conversation_id") or turning.conversation_id)
        turning.page_id = str(turn.get("page_id") or turning.page_id)
        turning.model, turning.effort = str(turn.get("model") or ""), str(turn.get("effort") or "")
        store.claimed_turn(self.turn_id, link_id=self.link_id, conversation_id=turning.conversation_id,
                           page_id=turning.page_id, agent=turning.agent)
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
        return {"text": str(turn.get("text") or ""), "selections": selections[:chats.MAX_SELECTIONS],
                "earlier": earlier[-6:]}

    def _turned_off(self, turning, link: dict) -> None:
        """The vault's agent was turned off while the turn waited here: the
        turn taken there by this run and failed with why ("Codex was turned
        off for this folder."), as a turn of this server's page fails, never
        left waiting there; nothing when that server no longer gives it to
        this computer (its announce without the agent came first) or the
        vault's computer is not shown there. Recorded here once taken, so an
        end that never reached that server is failed there later
        (``unsure``)."""
        if turning.agent not in catalog.AGENTS or not self._connect(link):
            return
        fields = {"state": "failed", "error": f"{catalog.label(turning.agent)} was turned off for this folder.",
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
                           page_id=turning.page_id, agent=turning.agent)
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

    def ours(self, link: dict, context: dict) -> bool:
        """That server decided at the claim: the token's account's turn."""
        return True

    def editor(self, link: dict) -> bool:
        """That server refuses the claim of an account that cannot edit."""
        return True

    def cannot_write(self) -> str:
        """That server refuses the writes itself (read-only, a workspace
        being deleted)."""
        return ""

    def owner(self, link: dict) -> str:
        return self.username or "the owner"

    def where(self, turning, link: dict, context: dict) -> tuple[str, str, list[dict]]:
        """``(the page's path in the work folder, the work folder's name,
        the person's selections for the prompt)``: the path the vault's rows
        give the page (else the claim's), and each selection's source line
        computed here from the PDF and the sources on this disk
        (``feedback.sources.source_in_vault``), on the version the selection
        was made on (its ``doc_id`` when that server names it, else the one
        the vault read: the page as it is shown)."""
        from ..feedback import entries, sources
        from ..vault import kinds, store as vault_store

        files = vault_store.files(link["id"])
        root = Path(link["path"])
        shown = [row.get("hash") for row in files.values() if row.get("page_id") == turning.page_id
                 and row.get("kind") == kinds.PDF.name and not row.get("was")]
        selections = []
        for chosen in context["selections"]:
            source = None
            if chosen.get("page") and chosen.get("box"):
                doc_id = chosen.get("doc_id") or (shown[0] if len(shown) == 1 else "")
                place = {"page_id": turning.page_id, "kind": "passage", "page": chosen["page"],
                         "rects": [chosen["box"]], "doc_id": doc_id}
                try:
                    source = sources.source_in_vault(files, root, place)
                except Exception as e:  # noqa: BLE001 — a selection without its line is searched for
                    log.warning(f"[agents] the source of a selection on page {turning.page_id}: {e}")
            selections.append({"page": chosen.get("page"), "quote": chosen.get("text") or "", "source": source})
        file = (entries.paths_of(files).get(turning.page_id) or str(self.page.get("path") or "")
                or str(self.page.get("title") or ""))
        if not file:
            turning._fail("This page is no longer a file of the work folder.")
        return file, str(self.page.get("folder") or "") or root.name, selections

    def write(self, turning, fields: dict, *, last: bool = False) -> bool:
        """The turn's agent message takes ``fields`` there (``CHAT_FIELDS``
        only), under the next ``seq`` → whether that server still gives the
        turn to this run (False: stopped there, taken, its conversation
        gone: the turn stops). A write that cannot reach that server is
        lost (the next one carries the whole message), unless it is the
        ``last``: tried again for ``RETRY_S``, then ``gamma_sync.Unreachable``
        (the turn stays open, failed there later); ``Refused`` for another
        refusal of the last."""
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
            log.warning(f"[agents] a write of chat turn {self.turn_id} refused there: {e}")
        except gamma_sync.SyncError as e:
            if last:
                self.gave_up = True
                raise
            log.info(f"[agents] a write of chat turn {self.turn_id}: {e}")
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
        fields = {"state": state, "ended_at": page_now()}
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
