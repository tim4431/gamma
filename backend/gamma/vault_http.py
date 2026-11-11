"""A synced vault of a folder on another Gamma server (docs/research/vaults.md
"In steps" 5, "What happens to what exists": a NAS): the remote a round of
gamma/vault_sync.py reads and writes Gamma through, ``HttpRemote``, in place
of ``vault_sync.LocalRemote``'s reads and writes in-process. It speaks to that
server with the link's token (``gamma_sync.Server``), through the routes the
browser uses: the library listing (``GET /api/blocks/root/children``: the
pages with their filing, the folder and label trees) and the change feed
(``GET /api/sync/changes``: each page's seq, and the cursor the tick asks it
about), a page's own block for its aliases (once a seq), its subtree, a block,
a stored file; ops (``POST /api/pages/{id}/ops``, client ``disk``, each batch
named), a page made (``POST /api/pages``, then its blocks, ``PUT
/api/blocks/{id}/children``), the folder and label trees' ops, the trash and
a restore, uploads and the attachment replaced; and one route of its own, a
page's plan (``POST /api/pages/{id}/plan``, gamma/ops.py ``commit_plan``). A
token that may read pulls; pushing takes one that may write, of an editor.

What a vault over HTTP does otherwise than one of this server:

- **No write lock.** A plan is written as the page stood when it was read
  again just before: its seq, else ``Changed``, and the round plans again. It
  goes in one request, which that server writes in one transaction or not at
  all, and refuses whole when the page moved past that seq: whatever someone
  typed there in between, a child under a block the file dropped among it, is
  seen by the plan made again, never written over or deleted unseen, and no
  part of a plan lands without the rest. The batch named by the journal's
  token, the first after the seq the plan was made at, or a block the plan
  made, tells a round that died whether it landed; a page in Recently deleted
  there, whose log is not served, tells it once the push of its note brings
  it back. The seq a write gives is the page's only when nobody wrote in
  between, else the next pull reads the page again.
- **Conflicts.** The file wins, as here. The text Gamma loses cannot go into
  the other server's conflict table, so it is kept in this computer's vault
  history (``vault_store.keep``) under the page's notes before the plan goes
  (a plan whose answer is lost has taken it), and the round counts it
  (``counts.conflicts``).
- **PDFs** are stored there (a NAS cannot read this disk): a new paper's
  bytes are uploaded before its page is made, a new version's before the
  attachment is replaced, and pictures a note links are uploads there too.
- **Not seen from here**: the carets in that server's rooms (a shape op may
  land on a block someone is typing in, which that editor merges), and the
  relocation run, which a token cannot start: the highlights a callout
  brings with no anchor wait for *Find highlights in this PDF* there.
- **The account.** The server not answering is ``Unreachable``, and so is a
  server error on a read the round needs; the token refused or no editor's,
  and a server that keeps its pages in another shape than this one
  (``_check_format``, before any other request), ``Refused``: the vault waits, never a file's problem. Any
  other refusal is the file's, a proxy's page in place of JSON among them (a
  413 on a body past its limit), and a new file the server refused is sent
  again once it changes.
"""

import json
import mimetypes
import urllib.parse
from dataclasses import replace
from pathlib import Path

from . import gamma_sync, migrations, vault_store, workspaces
from .blocks_store import FOLDERS, LABELS, PARENT_PAGE, TREES, filing, folder_inserts, label_inserts, top_of
from .blocks_store import with_sub_pages
from .documents.model import document_at, own_hash, page_document
from .folder_sync import ROOT
from .ops import MAX_OPS, OpError
from .sync_engine import FormatMismatch
from .sync_tree import snapshot_from_tree
from .vault_disk import Stopped
from .vault_sync import (CLIENT, PLAN_TRIES, Changed, Edit, Gone, Info, Layout, Page, Refused, Waiting, left_out,
                         set_page)

FEED_LIMIT = 2000      # the change feed's entries a request reads (routers/sync.py MAX_LIMIT)
_aliases: dict = {}    # (server, workspace, page) -> (its seq, its aliases): a page's own block read once a seq


class Unreachable(Refused):
    """The other server does not answer, or answers no JSON: the vault waits,
    as for its account."""


def _q(text: str) -> str:
    return urllib.parse.quote(text, safe="")


def _decoded(data: bytes) -> dict | None:
    try:
        value = json.loads(data.decode("utf-8")) if data else {}
    except ValueError:
        return None
    return value if isinstance(value, dict) else {}


def _refusal(status: int, answer: dict, path: str, method: str = "GET") -> Exception:
    """What a refusal is to a round: the token's (``Refused``), the server's
    on a read the round needs (``Unreachable``: a 5xx, the vault waits) or
    the request's (``OpError``, its ``conflict`` and ``index`` as ops raise
    them: that file's problem). A 413 with no word of its own is a body too
    large for the other server (a proxy's limit)."""
    detail = str(answer.get("detail") or ("too large for the other server (413)" if status == 413 else f"{path}: {status}"))
    if status in (401, 403):
        return Refused(f"the other server refused the link's token: {detail}")
    if status >= 500 and method == "GET":
        return Unreachable(f"the other server answered {status}: {detail}")
    e = OpError(status, detail, conflict=str(answer.get("conflict") or ""))
    e.index = answer.get("index")
    return e


def _tree(node: dict) -> dict:
    """``{id: (parent id, name)}`` of a tree's blocks (a nested ``subtree`` or
    listing node), parents before children, siblings in order."""
    out, pending = {}, list(reversed(node.get("children") or []))
    while pending:
        n = pending.pop()
        out[n["id"]] = (n.get("parent_id") or "", n.get("content") or "")
        pending.extend(reversed(n.get("children") or []))
    return out


def _kids(tree: dict) -> dict:
    """``blocks_store.tree_children`` of a ``_tree``."""
    kids = {}
    for block_id, (parent, name) in tree.items():
        kids.setdefault(parent, []).append((block_id, name))
    return kids


def _below(tree: dict, top: str) -> set:
    """``top`` and every folder below it in a ``_tree``."""
    kids, out, pending = _kids(tree), set(), [top]
    while pending:
        block_id = pending.pop()
        out.add(block_id)
        pending += [k for k, _name in kids.get(block_id, ())]
    return out


def _filed(pages: list, inside: set | None, parents: dict) -> list:
    """The listing's library pages filed in one of the folders ``inside``
    (None: every one), each with its sub-pages, in library order."""
    filed = [p["id"] for p in pages if not p.get("parent")
             and (inside is None or inside & set(filing(p.get("properties"), FOLDERS)))]
    return with_sub_pages(None, filed, parents)


class HttpRemote:
    """The workspace ``link["workspace_id"]`` of the server ``server`` (with
    the link's token), the vault's folder ``link["folder_id"]`` in it: the
    interface of ``vault_sync.LocalRemote`` over HTTP (the module doc). The
    library is listed once, and the page of each block a note links read
    once, until this remote writes (``_wrote``)."""

    def __init__(self, link: dict, server: gamma_sync.Server):
        self.link, self.link_id, self.server, self.root = link, link["id"], server, Path(link["path"])
        self.ws, self.folder = link["workspace_id"], link["folder_id"]
        self.touched = set()
        self._writer = None     # whether the token may write there: asked once (``may_write``)
        self._format_ok = False  # the server's pages have this one's shape: asked once (``_check_format``)
        self._listed = None     # the listing, until this remote writes
        self._sent = set()      # the PDFs this remote uploaded
        self._held_by = {}      # block id -> its page, as read: a note's links ask it of each block they name

    # --- asking ----------------------------------------------------------------------------

    def _ask(self, method: str, path: str, obj=None) -> tuple[int, dict]:
        """``(status, the JSON answer)``, its ``detail`` without the link's
        token, whatever its shape (a server that echoes it:
        ``gamma_sync.scrubbed``). ``Unreachable`` when the server does not
        answer, or answers no JSON with a success (a sign-in portal's page), a
        5xx, a 404, a 408 or a 429 (a proxy with no server behind it); no JSON
        with another refusal (a proxy's 413 on a body past its limit) is that
        request's own, ``{}`` with its status. The first request asks the
        server's format first (``_check_format``)."""
        self._check_format()
        return self._asked(method, path, obj)

    def _check_format(self) -> None:
        """``Refused`` when the server keeps its pages in another shape than
        this one (its change feed's ``format``, ``migrations.workspace_format``),
        as a clone pauses (``sync_engine._feed_all``): asked before this
        remote reads or writes anything else, so neither side reads what the
        other wrote, and the vault waits until both run releases that agree.
        A feed that names no format is taken as it is. Asked once."""
        if self._format_ok:
            return
        status, feed = self._asked("GET", "/api/sync/changes?limit=1")
        if not 200 <= status < 300:
            raise _refusal(status, feed, "/api/sync/changes")
        there, here = feed.get("format"), migrations.workspace_format()
        if isinstance(there, int) and there != here:
            raise Refused(str(FormatMismatch(here, there)))
        self._format_ok = True

    def _asked(self, method: str, path: str, obj=None) -> tuple[int, dict]:
        """``_ask`` without the format's check."""
        try:
            status, data = self.server.ask(method, path, obj)
        except gamma_sync.Unreachable as e:
            raise Unreachable(str(e)) from e
        answer = _decoded(data)
        if answer is None:
            if not 400 <= status < 500 or status in (404, 408, 429):
                raise Unreachable(f"{path}: the other server did not answer JSON")
            answer = {}
        return status, self._scrubbed(answer)

    def _scrubbed(self, answer: dict) -> dict:
        if "detail" in answer:
            answer["detail"] = gamma_sync.scrubbed(answer["detail"], self.server.token)
        return answer

    def _call(self, method: str, path: str, obj=None) -> dict:
        """The answer of a request that must succeed: a refusal raised
        (``_refusal``)."""
        status, answer = self._ask(method, path, obj)
        if not 200 <= status < 300:
            raise _refusal(status, answer, path, method)
        return answer

    def _block(self, block_id: str) -> tuple[str, dict]:
        """``("live", the block)``, ``("trashed", {})`` for one in Recently
        deleted, ``("", {})`` for none."""
        status, answer = self._ask("GET", f"/api/blocks/{_q(block_id)}")
        if status == 404:
            return ("trashed" if answer.get("trashed") else ""), {}
        if not 200 <= status < 300:
            raise _refusal(status, answer, f"/api/blocks/{block_id}")
        return "live", answer

    def _wrote(self, page_id: str = "") -> None:
        """What this remote read of the library is read again after its write."""
        self._listed = None
        self._held_by.clear()
        if page_id:
            self.touched.add(page_id)

    # --- reading ---------------------------------------------------------------------------

    def may_write(self) -> None:
        """``Refused`` unless the token may write there, as an editor: asked
        once (``/api/sync/whoami``)."""
        if self._writer is None:
            who = self._call("GET", "/api/sync/whoami")
            self._writer = who.get("scope") == "write" and workspaces.at_least(who.get("role"), "editor")
        if not self._writer:
            raise Refused("the link's token may not write in that workspace: pushing takes a token that may write")

    def _listing(self) -> dict:
        """The feed's seqs and cursor (read first), then the library listing:
        ``{seqs, cursor, pages (summaries, in library order), folders
        (``_tree``), labels ({id: name}), labels_seq}``. A feed that says
        there is more but does not move its cursor (a cache that ignores the
        query) is ``Unreachable``, never a loop holding the link."""
        if self._listed is None:
            seqs, since = {}, ""
            while True:
                feed = self._call("GET", f"/api/sync/changes?since={_q(since)}&limit={FEED_LIMIT}")
                seqs.update({p["id"]: int(p.get("seq") or 0) for p in feed.get("pages") or ()})
                for gone in feed.get("deleted") or ():
                    seqs.pop(gone["id"], None)
                was, since = since, str(feed.get("cursor") or since)
                if not feed.get("more"):
                    break
                if since == was:
                    raise Unreachable("the other server's change feed says there is more but does not move")
            root = self._call("GET", "/api/blocks/root/children")
            labels = root.get(LABELS) or {}
            self._listed = {"seqs": seqs, "cursor": int(since) if since.isdigit() else 0,
                            "pages": root.get("children") or [], "folders": _tree(root.get(FOLDERS) or {}),
                            "labels": {i: name for i, (_p, name) in _tree(labels).items()},
                            "labels_seq": int(labels.get("seq") or 0)}
        return self._listed

    def _aliases_of(self, page_id: str, seq: int) -> list:
        """The page's aliases, which the listing does not carry: its own block,
        read once a seq."""
        key = (self.server.url, self.ws, page_id)
        if key not in _aliases or _aliases[key][0] != seq:
            _state, block = self._block(page_id)
            aliases = (block.get("properties") or {}).get("aliases") or []
            _aliases[key] = (seq, [str(a) for a in aliases if isinstance(a, str)])
        return _aliases[key][1]

    def layout(self, page_id: str = "") -> Layout:
        """``LocalRemote.layout`` from the listing (``pages_in_folder``'s rule:
        the library pages filed in the folder or below, each with its
        sub-pages); with ``page_id`` that page alone, when it is one of them.
        The pages of the vaults of this computer that may take a page from it
        in that workspace are left out, as on this server
        (``vault_sync.left_out``), and those of a vault beside it reported
        (``Layout.shared``). ``Stopped`` when the vault's folder is gone
        there."""
        listed = self._listing()
        folders = listed["folders"]
        top = None if self.folder == ROOT else self.folder
        if top and top not in folders:
            raise Stopped("the vault's folder is no longer in Gamma")
        summaries = {p["id"]: p for p in listed["pages"]}
        parents = {p["id"]: p["parent"] for p in listed["pages"] if p.get("parent")}
        inside = _below(folders, top) if top else None
        ids = _filed(listed["pages"], inside, parents)
        leave_out, siblings = left_out(self.link, lambda folder_id: _below(folders, folder_id))
        held = {other: set(_filed(listed["pages"], _below(folders, other), parents)) for other in leave_out}
        away = set().union(*held.values())
        beside = {pid for other in siblings for pid in held[other]}
        shared = [(pid, summaries[pid].get("content") or "Untitled") for pid in ids if pid in away and pid in beside]
        ids = [pid for pid in ids if pid not in away]
        if page_id:
            ids, shared = [page_id] if page_id in ids else [], []
        names, pages = listed["labels"], {}
        for pid in ids:
            props, seq = summaries[pid].get("properties") or {}, listed["seqs"].get(pid, 0)
            top_props = summaries.get(top_of(parents, pid), {}).get("properties")
            pages[pid] = Info(pid, summaries[pid].get("content") or "Untitled", seq, filing(top_props, FOLDERS),
                              own_hash(page_document(props)), False,
                              [names[i] for i in filing(props, LABELS) if i in names], self._aliases_of(pid, seq),
                              parents.get(pid, ""))
        below = {f: entry for f, entry in folders.items() if (inside is None or f in inside) and f != top}
        return Layout(pages, below, listed["cursor"], listed["labels_seq"], shared)

    def page(self, page_id: str) -> Page | None:
        """The page's seq and its blocks, read together; one in Recently
        deleted has none; None for a page gone for good."""
        status, answer = self._ask("GET", f"/api/blocks/{_q(page_id)}/subtree")
        if status == 404:
            return Page(page_id, 0, {}, True) if answer.get("trashed") else None
        if not 200 <= status < 300:
            raise _refusal(status, answer, f"/api/blocks/{page_id}/subtree")
        block = answer.get("block") or {}
        if block.get("parent_id") != "root":
            return None
        return Page(page_id, int(answer.get("seq") or 0), snapshot_from_tree(block))

    def state(self, page_id: str) -> str | None:
        state, block = self._block(page_id)
        if state == "live" and block.get("parent_id") != "root":
            return None
        return state or None

    def filing(self, page_id: str) -> tuple[list, str]:
        _state, block = self._block(page_id)
        props = block.get("properties") or {}
        return filing(props, FOLDERS), str(props.get(PARENT_PAGE) or "")

    def doc_of(self, page_id: str) -> str:
        _state, block = self._block(page_id)
        return own_hash(page_document(block.get("properties") or {}))

    def landed(self, page_id: str, token: str, at: int | None = None, since: float = 0.0) -> bool | None:
        """Whether the page's log holds a batch of the vault's named ``token``
        (the log's ``batch``): the first batch after ``at``, the seq the plan
        was made at, which is the plan's if it landed (``ops.commit_plan``
        writes it right after that seq or not at all), so that a log pruned
        before it or long before it still says; without ``at``, any batch of
        the log. None where the log cannot say: the page in Recently deleted
        (its log is not served), or a 410 (the batch pruned, or more after it
        than a catch-up carries), and the round asks the page itself
        (``Round._plan_landed``). ``since`` is for this server's own log
        (``LocalRemote.landed``)."""
        path = f"/api/pages/{_q(page_id)}/ops?since={at or 0}"
        status, answer = self._ask("GET", path)
        if status == 404:
            return None if self._block(page_id)[0] == "trashed" else False
        if status == 410:
            return None
        if not 200 <= status < 300:
            raise _refusal(status, answer, path)
        batches = (answer.get("batches") or [])[:1 if at is not None else None]
        return any(b.get("client") == CLIENT and b.get("batch") == token for b in batches)

    def held(self, page_id: str, ids) -> dict:
        """The ``ids`` another page holds, Recently deleted's too: ``{id: whether
        that page is live}``."""
        out = {}
        for block_id in sorted({i for i in ids if i}):
            state, block = self._block(block_id)
            if state == "trashed" or state == "live" and block.get("page_id") != page_id:
                out[block_id] = state == "live"
        return out

    def page_of(self, block_id: str) -> str | None:
        if block_id not in self._held_by:
            state, block = self._block(block_id)
            found = block.get("page_id") if state == "live" else None
            self._held_by[block_id] = found if found and found not in TREES else None
        return self._held_by[block_id]

    def title_of(self, page_id: str) -> str:
        found = next((p for p in self._listing()["pages"] if p["id"] == page_id), None)
        return (found.get("content") or "") if found else ""

    def address(self) -> str:
        """The workspace's absolute address there, the base of a link to a
        page outside the vault."""
        return f"{self.server.url}/?ws={self.ws}"

    def carets(self, page_id: str) -> set:
        return set()     # that server's rooms are not seen from here

    def read_upload(self, name: str) -> bytes | None:
        self._check_format()
        try:
            status, data = self.server.ask("GET", f"/api/uploads/{_q(name)}")
        except gamma_sync.Unreachable as e:
            raise Unreachable(str(e)) from e
        if status == 404:
            return None
        if not 200 <= status < 300:
            raise _refusal(status, self._scrubbed(_decoded(data) or {}), f"/api/uploads/{name}")
        return data

    # --- writing ---------------------------------------------------------------------------

    def apply(self, page_id: str, build=None, *, token: str = "", built=None) -> tuple[int, int, Edit]:
        """``LocalRemote.apply`` over HTTP (the module doc): the page read
        again, ``built`` written while its seq is still the plan's (else
        ``Changed``) or ``build(page, held)`` built from it, planned again
        ``PLAN_TRIES`` times when the page moved before it landed; the text a
        clash took from Gamma kept in the vault's history before the plan
        goes, then the plan in one request (``_send_plan``). ``Gone`` for a
        page that is not there. → (the seq before, the seq after, the edit)."""
        self.may_write()
        kept = set()
        for attempt in range(PLAN_TRIES):
            now = self.page(page_id)
            if now is None or now.trashed:
                raise Gone(page_id)
            if built is not None and built[0] != now.seq:
                raise Changed(page_id)
            edit = built[1] if built is not None else build(now, lambda ids: self.held(page_id, ids))
            self._lost(page_id, edit.conflicts, kept)    # first: a plan that lands with no answer back took them
            try:
                seq = self._send_plan(page_id, edit.ops, token, now.seq)
                break
            except OpError as e:
                if not e.conflict:
                    raise
                if built is not None or attempt == PLAN_TRIES - 1:
                    raise Changed(page_id) from e
        if seq != now.seq + (len(edit.ops) + MAX_OPS - 1) // MAX_OPS:
            edit = replace(edit, level=False)     # another writer came in between: the next pull reads the page
        return now.seq, seq, edit

    def _send_plan(self, page_id: str, ops: list, token: str, seq: int) -> int:
        """The ops in one request, ``POST /api/pages/{id}/plan``
        (``ops.commit_plan``): all of them in one transaction there or none,
        refused whole (``OpError`` with its conflict) when the page moved past
        ``seq``, the seq they were planned at, so that nothing typed there
        meanwhile is written over or deleted unseen; its first batch named
        ``token``. → the page's seq after. A server older than this app has
        no such route: ``Refused``, the vault waits for it."""
        if not ops:
            return seq
        status, answer = self._ask("POST", f"/api/pages/{_q(page_id)}/plan",
                                   {"client": CLIENT, "batch": token, "seq": seq, "ops": ops})
        if status in (404, 405) and answer.get("detail") in ("Not Found", "Method Not Allowed"):
            raise Refused("the other server is older than this app and cannot take a vault's changes: update it")
        if not 200 <= status < 300:
            raise _refusal(status, answer, f"/api/pages/{page_id}/plan", "POST")
        self._wrote(page_id)
        return int(answer.get("seq") or seq)

    def _lost(self, page_id: str, conflicts, kept: set) -> None:
        """Gamma's side of each clash the file won, which that server's
        conflict table cannot take: into the vault's history under the page's
        notes (its id when it has none here), each text once (``kept``: those
        an earlier try of the plan kept)."""
        texts = [c["mine"] for c in conflicts if c.get("mine") and c["mine"] not in kept]
        if not texts:
            return
        rows = vault_store.files(self.link_id)
        path = next((p for p, r in rows.items() if r["page_id"] == page_id and r["kind"] == "notes"), page_id)
        for text in texts:
            vault_store.keep(self.link_id, path, text.encode("utf-8"), always=True)
            kept.add(text)

    def create(self, page_id: str, title: str, props: dict, tree: list, labels=()) -> int:
        """A new page ``page_id`` with its blocks: the page, then its blocks
        (a crash or a refusal between leaves a page with none, which the
        journal keeps as the note's page, its blocks merged into it from no
        base: ``Round._made``). A PDF it names was uploaded first
        (``link_pdf``), and is stored there, never read in place (its
        document at ``store``). → its seq. OpError 409 when a block holds the
        id."""
        self.may_write()
        props = {**props, "doc": document_at(props["doc"], "store")} if props.get("doc") else dict(props)
        if labels:
            props[LABELS] = self.label_ids(labels)
        self._call("POST", "/api/pages", {"id": page_id, "title": title, "properties": props})
        self._wrote(page_id)
        if tree:
            self._call("PUT", f"/api/blocks/{_q(page_id)}/children", {"blocks": tree})
        status, answer = self._ask("GET", f"/api/pages/{_q(page_id)}/ops?since=0")
        if 200 <= status < 300:
            return int(answer.get("seq") or 0)
        page = self.page(page_id)
        return page.seq if page else 0

    def set_page(self, page_id: str, *, title=None, props=None) -> tuple[int, int]:
        return set_page(self, page_id, title=title, props=props)

    def label_ids(self, names) -> list:
        """The labels of ``names``' ids, made there where missing."""
        node = self._call("GET", f"/api/blocks/{LABELS}/subtree").get("block") or {}
        have = [(block_id, name) for block_id, (_parent, name) in _tree(node).items()]
        ops, ids = label_inserts(None, names, have)
        if ops:
            self._call("POST", f"/api/pages/{LABELS}/ops", {"client": CLIENT, "ops": ops})
            self._wrote()
        return list(dict.fromkeys(ids[n] for n in names if n in ids))

    def trash(self, page_id: str) -> None:
        self.may_write()
        if self.state(page_id) != "live":
            return
        status, answer = self._ask("DELETE", f"/api/blocks/{_q(page_id)}")
        if status != 404 and not 200 <= status < 300:
            raise _refusal(status, answer, f"/api/blocks/{page_id}", "DELETE")
        self._wrote(page_id)

    def restore(self, page_id: str) -> None:
        self.may_write()
        if self.state(page_id) != "trashed":
            return
        status, answer = self._ask("POST", f"/api/trash/{_q(page_id)}/restore")
        if status != 404 and not 200 <= status < 300:
            raise _refusal(status, answer, f"/api/trash/{page_id}/restore", "POST")
        self._wrote(page_id)

    def folders(self, paths) -> dict:
        """``LocalRemote.folders``: the folders at ``paths``, made there where
        missing."""
        self.may_write()
        node = self._call("GET", f"/api/blocks/{FOLDERS}/subtree").get("block") or {}
        under = "" if self.folder == ROOT else self.folder
        ops, ids = folder_inserts(None, [list(p) for p in paths], under, kids=_kids(_tree(node)))
        if ops:
            self._call("POST", f"/api/pages/{FOLDERS}/ops", {"client": CLIENT, "ops": ops})
            self._wrote()
        return ids

    def folder_op(self, op: dict) -> None:
        """``LocalRemote.folder_op``; a move into a folder below itself is the
        OpError a loop is (``CYCLE``)."""
        self.may_write()
        if op["op"] == "move" and not op.get("parent"):
            op = {**op, "parent": FOLDERS if self.folder == ROOT else self.folder}
        self._call("POST", f"/api/pages/{FOLDERS}/ops", {"client": CLIENT, "ops": [op]})
        self._wrote()

    def drop_folder(self, folder_id: str) -> bool:
        """The folder deleted when no page is filed in it or below (as the
        listing has it); whether it is gone."""
        self.may_write()
        listed = self._listing()
        if folder_id not in listed["folders"]:
            return True
        inside = _below(listed["folders"], folder_id)
        if any(inside & set(filing(p.get("properties"), FOLDERS)) for p in listed["pages"] if not p.get("parent")):
            return False
        status, answer = self._ask("DELETE", f"/api/folders/{_q(folder_id)}")
        if status != 404 and not 200 <= status < 300:
            raise _refusal(status, answer, f"/api/folders/{folder_id}", "DELETE")
        self._wrote()
        return True

    # --- files -------------------------------------------------------------------------------

    def _upload(self, path: Path, route: str, content_type: str) -> dict:
        self._check_format()
        try:
            return self.server.upload(path, route, content_type)
        except gamma_sync.Unreachable as e:
            raise Unreachable(str(e)) from e
        except gamma_sync.Denied as e:
            raise Refused(str(e)) from e
        except gamma_sync.Changed as e:
            raise Waiting(str(e)) from e

    def link_pdf(self, path: str, seen: tuple, page_id: str) -> None:
        """The bytes ``seen`` at ``path`` stored there (a NAS reads no file of
        this disk), once a round; ``Waiting`` for a file that changed while it
        was sent."""
        self.may_write()
        if seen[0] in self._sent:
            return
        stored = self._upload(Path(path), "/api/uploads", "application/pdf").get("doc_id")
        if stored != seen[0]:
            raise Waiting("the file changed while it was sent")
        self._sent.add(stored)

    def unlink_pdf(self, path: str, name: str) -> None:
        pass             # nothing is read in place there

    def relink(self, old: str, new: str) -> None:
        pass

    def spare(self, path: str, name: str, page_id: str) -> None:
        pass             # no page there reads a file of this disk

    def replace_pdf(self, page_id: str, path: str, seen: tuple, filename: str) -> dict:
        """The page takes the bytes ``seen`` as its PDF, stored there first:
        that server's own replacement (``PUT /api/pages/{id}/attachment``), its
        relocation run included. Bytes another page there carries are the
        file's problem (409)."""
        self.link_pdf(path, seen, page_id)
        answer = self._call("PUT", f"/api/pages/{_q(page_id)}/attachment",
                            {"doc_id": seen[0], "original_filename": filename, "keep_old_days": gamma_sync.KEEP_OLD_DAYS})
        self._wrote(page_id)
        return answer

    def store_pdf(self, path: Path, page_id: str) -> str:
        return self.doc_of(page_id)      # stored there already

    def place_highlights(self, page_id: str) -> bool:
        return True      # a token starts no job there: the highlights wait for Find highlights in this PDF

    def pictures(self, root: Path) -> dict:
        return {}

    def link_file(self, path: Path, name: str = "") -> str:
        """A picture of the vault a note links, stored there: its upload name,
        kept on its row for the links of the rounds after (a picture pull
        wrote, ``name``, is stored there already)."""
        if name:
            return name
        self.may_write()
        content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        answer = self._upload(path, "/api/upload-file", content_type)
        name = str(answer.get("url") or "").rsplit("/", 1)[-1]
        st = path.stat()
        vault_store.put(self.link_id, path.relative_to(self.root).as_posix(), kind="picture", hash=name, size=st.st_size,
                        mtime_ns=st.st_mtime_ns, file_id=str(st.st_ino))
        return name

    def tell(self) -> None:
        self.touched.clear()     # that server's rooms hear the ops themselves
