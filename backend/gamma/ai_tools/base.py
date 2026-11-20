"""What every tool family shares: the message's budgets (``Tally``) and
the loop's limits (``MAX_TOOL_ROUNDS``, the change limit), the chat's
scope (what a folder or a page chat reaches; a folder named by its path or
its id; a page or a block loaded only when it is in reach), links into the
app (``gamma_link``), the read window (``Window``), the account and the
agent a writing tool writes as (``_writer``), and the diff an approval card
shows (``text_diff``). It imports no other module of the package."""

import json
import re
import threading
from difflib import SequenceMatcher
from typing import NamedTuple
from urllib.parse import urlencode

from fractional_indexing import generate_key_between

from ..ai_pictures import upload_name
from ..ai_context import NOTE_IMAGE_RE
from ..blocks_store import (FOLDERS, LABELS, PATH_SEP, TRASH, descend, filed_folders, folder_paths, folder_subtree_ids,
                            load_props, page_ancestors, page_descendants, page_root_id, root_pages, tree_children,
                            valid_block_id)
from ..documents.model import file_hash, page_document
from ..ops import ensure_filing
from ..work_folders import paths as work_paths
from ..work_folders.library import work_folder_of
from ..work_folders.marks import Tree


# Runaway guards for the tool loop, not workload caps: MAX_TOOL_ACTIONS bounds
# the real work (mutations only), while the round limit stops a loop that
# never converges. Rounds are generous because some models issue one call per
# round-trip even with parallel tool calls enabled. Unlimited rounds (the
# chat's tool_rounds_unlimited, a streamed reply only) lifts both.
MAX_TOOL_ROUNDS = 32    # provider round-trips per user message
MAX_TOOL_ACTIONS = 200  # mutations per user message (bulk renames are legit)
# What the model hears for a change past the message's change limit. A
# refusal at that limit starts with the prefix: the loop reads it as the
# reply stopping short there (ai_agent.AgentLoop), and ends it on Continue.
CHANGE_LIMIT_PREFIX = "error: the change limit for one message"
CHANGE_LIMIT = (CHANGE_LIMIT_PREFIX + " ({n} changes) is reached, so nothing was changed — stop, and tell "
                "the user what is done and what is left: they can continue in their next message")
_LIST_CAP = 400         # lines one listing call shows: list_pages' `limit` at most (pages, or labels
                        # with list_labels), list_folders', list_deleted's and read_chats' conversations
_TITLE_MAX = 300
# read_page's document-text window: what one call returns when the model
# doesn't ask (default) and the most it may ask for (cap). The cap is the
# Settings → AI → Tool usage "Read window" preference — requests carry it as
# read_char_limit and it rides in the scope dict; these are the fallbacks.
READ_CHARS_DEFAULT = 6000
READ_CHARS_CAP = 20000
READ_CHARS_MAX = 1_000_000  # sanity ceiling, matches the settings slider's range
_DETAIL_CAP = 4000      # chars of a tool's output kept for the chat's expandable chip
_ARG_CAP = 400          # chars per argument value in that chip
# read_block outlines: chars of one block's content shown per line (the
# requested block itself is never truncated), and the most markdown one
# edit_block/create_block call may write.
_NOTE_SNIPPET = 500


# What read_block puts where it snipped a long child (see _run_read_block):
# a replace carrying it was built from the snippet, not the block.
_TRUNCATED_MARK = "[truncated — read_block("


# --- one message's counters ----------------------------------------------------

class Tally:
    """What one user message has spent, shared by the calls of a round that
    run at the same time (``ai_agent.AgentLoop`` runs a batch of reads in
    threads): the papers saved, the web searches used, and the works a
    search already listed. Every check-then-spend goes through the lock, so
    two parallel calls cannot take the same last unit of a budget.
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._counts: dict[str, int] = {}
        self._shown: set[str] = set()

    def count(self, key: str) -> int:
        with self._lock:
            return self._counts.get(key, 0)

    def take(self, key: str, limit: int) -> bool:
        """Reserve one unit of the ``key`` budget; False once it is spent."""
        with self._lock:
            used = self._counts.get(key, 0)
            if used >= limit:
                return False
            self._counts[key] = used + 1
            return True

    def first_sight(self, keys) -> bool:
        """Whether this message has not listed the work yet, recording it
        either way. A second search that turns up the same paper shows one
        line pointing back instead of repeating the whole record."""
        keys = [key for key in keys if key]
        if not keys:
            return True
        with self._lock:
            fresh = not any(key in self._shown for key in keys)
            self._shown.update(keys)
            return fresh


def ensure_tally(scope: dict) -> Tally:
    """The message's counters, made once before any call can run in parallel
    (the loop does this when it starts; MCP and approval previews fall back
    to making one here)."""
    tally = scope.get("tally")
    if not isinstance(tally, Tally):
        tally = Tally()
        scope["tally"] = tally
    return tally


# --- scope ---------------------------------------------------------------------
# A folder chat names its folder by id and reaches the pages filed in it or
# below it. The model names folders by path or by the ids list_folders shows;
# _resolve_folder reads either inside the chat's folder.

def _scope_folder(scope: dict) -> str:
    """A folder chat's folder id; "" at the library root and in a page chat."""
    return str(scope.get("folder") or "") if scope.get("type") == "folder" else ""


def _reach(conn, scope: dict) -> set[str] | None:
    """The folders a folder chat reaches: its folder and every folder below
    it — None at the library root, which reaches every page."""
    folder = _scope_folder(scope)
    return folder_subtree_ids(conn, folder) if folder else None


def _filed_in(reach: set[str] | None, folders: list[str]) -> bool:
    """Whether a page filed in ``folders`` (ids) is within ``reach``."""
    return reach is None or any(f in reach for f in folders)


# A reach not worked out yet (``_page_in_scope``); None is the library root's.
_NO_REACH = object()


def _not_a_folder(conn, text: str) -> str | None:
    """The refusal for a ``folder`` argument that is an id but no folder's:
    another block's (a page's, a note's, a label's), or a folder's deleted
    lately — the folder tree's op log names it (it keeps a day,
    ``ops.KEEP_OPS_HOURS``, at most ``KEEP_OPS`` rows). None for anything
    else: a name, which a path that names no folder makes."""
    if not valid_block_id(text):
        return None
    row = conn.execute("SELECT page_id, parent_id FROM unified_blocks WHERE id = ?", (text,)).fetchone()
    if row:
        what = "label" if row[0] == LABELS else "page" if row[1] in ("root", TRASH) else "note"
        return f'error: "{text}" is the id of a {what}, not of a folder — list_folders shows the folders and their ids'
    if conn.execute("SELECT 1 FROM page_ops p, json_each(p.ops) o WHERE p.page_id = ? AND instr(p.ops, ?) > 0 "
                    "AND json_extract(o.value, '$.id') = ? LIMIT 1", (FOLDERS, json.dumps(text), text)).fetchone():
        return (f'error: no folder has the id "{text}" now — it was deleted; list_folders shows the folders '
                "and their ids")
    return None


def _resolve_folder(conn, scope: dict, raw, *, write: bool = False) -> tuple[dict | None, str | None]:
    """The folder a tool's ``folder`` argument names: a folder id, or a path
    as the tools show it ("Physics / QEC"; "Physics/QEC" too, where that is
    no folder's name). A folder chat reads it inside its folder — a path
    that starts with the folder's own path from there, any other as a path
    below it — and "" is the chat's folder itself; a page chat reads it from
    the top, "" being the library root. Each name matches exactly, else
    ignoring case (``blocks_store.named``). For a ``write`` (a page filed,
    a folder made) a path passes by every work folder with its directories
    (``descend``'s ``works``), so a name they share makes a library folder;
    a read reaches them.

    Returns ``(target, error)``: ``{"id", "path"}`` for a folder that exists
    (id "" = the library root), ``{"id": "", "path", "under", "names"}``
    for one that does not — ``names`` below the folder ``under``, what
    ``_made`` makes. ``error`` refuses a folder outside the chat's folder
    and a path several folders share, naming their ids, and an id that is
    no folder's (``_not_a_folder``)."""
    base = _scope_folder(scope)
    kids, paths = tree_children(conn, FOLDERS), folder_paths(conn)
    works = set(Tree.read(conn).roots()) if write else ()
    text = str(raw or "").strip()
    if not text:
        return {"id": base, "path": PATH_SEP.join(paths.get(base, []))}, None
    if text in paths:  # an id, as list_folders shows them
        if not _filed_in(_reach(conn, scope), [text]):
            return None, "error: that folder is outside this chat's folder"
        return {"id": text, "path": PATH_SEP.join(paths[text])}, None
    own = [name.lower() for name in paths.get(base, [])]  # as blocks_store.named compares
    missing = []
    for names in dict.fromkeys(tuple(n.strip() for n in text.split(sep) if n.strip())
                               for sep in (PATH_SEP, "/")):
        if own and [n.lower() for n in names[:len(own)]] == own:
            names = names[len(own):]  # the chat's own path, then below it
        level, rest = descend(kids, base or FOLDERS, names, works=works)
        if len(level) > 1:
            return None, (f'error: "{text}" names {len(level)} folders — pass the one you mean by its id: '
                          + ", ".join(f'{f} ("{PATH_SEP.join(paths[f])}")' for f in level))
        if not rest:
            return {"id": "" if level[0] == FOLDERS else level[0],
                    "path": PATH_SEP.join(paths.get(level[0], []))}, None
        missing.append({"id": "", "under": level[0], "names": rest,
                        "path": PATH_SEP.join([*paths.get(level[0], []), *rest])})
    # Nothing there. An id that names no folder is no name to make.
    gone = _not_a_folder(conn, text)
    if gone:
        return None, gone
    # A spaced path makes its names, a bare "a/b" nests.
    return missing[0 if PATH_SEP in text else -1], None


def _find_folder(conn, scope: dict, raw) -> tuple[tuple | None, str | None]:
    """``_resolve_folder`` for a read: ``((folder id, path), None)`` for a
    folder that exists ("" = the library root), else ``(None, error)``."""
    target, error = _resolve_folder(conn, scope, raw)
    if not error and target.get("names"):
        error = f'error: there is no folder "{target["path"]}" — list_folders shows the folders and their ids'
    return (None, error) if error else ((target["id"], target["path"]), None)


def _made(ws: str, conn, scope: dict, target: dict) -> str:
    """The id of ``target``'s folder (``_resolve_folder``), made first when
    it is missing (``ops.ensure_filing``: one committed batch on the folder
    tree)."""
    if not target.get("names"):
        return target["id"]
    made, _ = ensure_filing(ws, conn, paths=[target["names"]], under=target["under"],
                            actor=scope.get("actor", ""), client="ai")
    return made[tuple(target["names"])]


def _page_in_scope(conn, scope: dict, page_id: str, folders: list[str], reach=_NO_REACH) -> bool:
    if page_id in (scope.get("context_pages") or []):
        return True
    if scope.get("type") == "page":
        # a reading tool of a page chat reaches the pages inside the page too (run_agent_tool's ``inside``)
        return page_id == scope.get("page_id") or bool(
            scope.get("inside") and scope.get("page_id") in page_ancestors(conn, page_id))
    return _filed_in(_reach(conn, scope) if reach is _NO_REACH else reach, folders)


_NO_PAGE = "error: no such page — use exact page ids"  # a batch's id of no id's shape too (_batch)


def _page_by_path(conn, scope: dict, path: str) -> tuple[str | None, str | None]:
    """The page a work folder's file ``path`` names (``work_folders.paths``):
    in the work folder of the chat's folder, else of a page chat's page,
    else the one the path's first segment names. ``(page_id, error)``; the
    error names the pages when several share the path."""
    tree = Tree.read(conn)
    work, rest = tree.work_of(_scope_folder(scope)), path
    if not work and scope.get("type") == "page":
        row = conn.execute("SELECT properties FROM unified_blocks WHERE id = ?",
                           (str(scope.get("page_id") or ""),)).fetchone()
        work = work_folder_of(load_props(row[0])) if row else ""
    found = work_paths.page_at_path(conn, tree, work, path) if work else []
    if not found:
        try:
            named_work = work_paths.work_of_path(conn, tree, path)
        except ValueError as e:
            return None, f"error: {e}"
        if named_work:
            work, rest = named_work
            found = work_paths.page_at_path(conn, tree, work, rest)
        elif not work:
            first = str(path).replace("\\", "/").strip("/").split("/", 1)[0]
            return None, (f'error: no work folder is named "{first}" — list_folders shows them, '
                          "or pass a page id")
    if not found:
        return None, (f'error: no file is at "{path}" in the work folder "{tree.names.get(work, "")}" — '
                      'list_pages(folder="…") lists its files with their paths')
    if len(found) > 1:
        return None, (f'error: "{path}" names {len(found)} pages — pass the one you mean by its id: '
                      + ", ".join(found))
    return found[0], None


def _load_scoped_page(conn, scope: dict, args: dict, reach=_NO_REACH):
    """Fetch the target page and enforce the scope. Returns
    ``((page_id, title, props, folders), error)`` — ``folders`` the ids it
    is filed in, which for a sub-page are its top page's (blocks_store's
    filing rule); exactly one side is set. ``reach``: the chat's
    (``_reach``) when the caller worked it out already. Without a
    ``page_id``, a ``path`` names a work folder's file (``_page_by_path``)."""
    page_id = str(args.get("page_id") or "").strip()
    path = args.get("path")
    if not page_id and isinstance(path, str) and path.strip():
        page_id, error = _page_by_path(conn, scope, path.strip())
        if error:
            return None, error
    row = conn.execute(
        "SELECT parent_id, content, properties FROM unified_blocks WHERE id = ?",
        (page_id,),
    ).fetchone()
    if not row or row[0] != "root":
        return None, _NO_PAGE
    props = json.loads(row[2] or "{}")
    folders = filed_folders(conn, page_id)
    if not _page_in_scope(conn, scope, page_id, folders, reach):
        return None, "error: page is outside this chat's scope"
    return (page_id, row[1] or "Untitled", props, folders), None


def _scope_pages(conn, scope: dict) -> dict:
    """Titles and documents for the base scope plus the attached context
    pages: a page chat's page, with the pages inside it for a reading tool
    (``inside``), or a folder chat's pages (``root_pages``), in
    ``root_pages``' shape ({page_id: {title, doc_id, format, ext, at, name}}).
    Every page gives the document it shows (``file_hash``), a duplicated
    page's included."""
    page_ids = list(scope.get("context_pages") or [])
    if scope.get("type") == "page":
        pages = {}
        page_id = str(scope.get("page_id") or "")
        page_ids[:0] = [page_id] + (page_descendants(conn, page_id) if scope.get("inside") else [])
    else:
        pages = root_pages(conn, _scope_folder(scope))
        # root_pages answers the documents the pages own: a copy's is the file it shows
        bare = [page_id for page_id, page in pages.items() if not page["doc_id"]]
        for page_id, raw in conn.execute(
                "SELECT id, properties FROM unified_blocks WHERE id IN (SELECT value FROM json_each(?)) "
                "AND instr(properties, '\"copy\"') > 0", (json.dumps(bare),)):
            pages[page_id] = _scope_page(pages[page_id]["title"], page_document(load_props(raw)))
    for page_id in page_ids:
        loaded, error = _load_scoped_page(conn, scope, {"page_id": page_id})
        if not error:
            page_id, title, props, _ = loaded
            pages[page_id] = _scope_page(title, page_document(props))
    return pages


def _scope_page(title: str, view) -> dict:
    """A ``_scope_pages`` entry: the page's title and the document it
    shows."""
    return {"title": title, "doc_id": file_hash(view), "format": view["format"] if view else "",
            "ext": view["ext"] if view else "", "at": view["at"] if view else "",
            "name": view["name"] if view else ""}


def _load_scoped_block(conn, scope: dict, block_id) -> tuple:
    """Fetch any block (a page root or a nested note block) and enforce the
    scope via the page it lives in. Returns
    ``((row_dict, page_id, page_title), error)`` — exactly one side is set."""
    block_id = str(block_id or "").strip()
    row = conn.execute(
        "SELECT id, parent_id, position, content, properties FROM unified_blocks WHERE id = ?",
        (block_id,),
    ).fetchone()
    if not row:
        return None, "error: no such block — use exact ids from read_block/list_pages"
    page_id = block_id if row[1] == "root" else page_root_id(conn, block_id)
    loaded, error = _load_scoped_page(conn, scope, {"page_id": page_id})
    if error:
        return None, "error: block is outside this chat's scope"
    _, page_title, _, _ = loaded
    try:
        props = json.loads(row[4] or "{}")
    except ValueError:
        props = {}
    block = {"id": row[0], "parent_id": row[1], "position": row[2],
             "content": row[3] or "", "properties": props}
    return (block, page_id, page_title), None


def notes_seen(scope: dict) -> dict:
    """``{block_id: text}``: the full text of every note block this turn's
    reads showed the model (read_block, read_page, the chat's own context),
    as stored when it was read. An edit_block replace goes out with it as
    ``base``, so the three-way merge in ops.py keeps what the user typed
    meanwhile. The scope dict lives for one request, so an earlier turn's
    read never counts."""
    return scope.setdefault("read_texts", {})


def _sibling_gap(conn, parent_id: str, after_id, block_id: str = "") -> tuple:
    """The keys a (re)inserted child of parent_id goes between: after
    `after_id` when given (it must be a child of the same parent; the
    parent's own id puts it first), else at the end. Returns ``((lo, hi),
    error)``, either key None for an open end; block_id is excluded from
    the neighbour lookup so moving a block after its current predecessor
    works."""
    if after_id and str(after_id) == parent_id:
        row = conn.execute(
            "SELECT position FROM unified_blocks WHERE parent_id = ? AND id != ? ORDER BY position LIMIT 1",
            (parent_id, block_id)).fetchone()
        return (None, row[0] if row else None), None
    if after_id:
        row = conn.execute(
            "SELECT position FROM unified_blocks WHERE id = ? AND parent_id = ?",
            (str(after_id), parent_id),
        ).fetchone()
        if not row:
            return None, "error: after_id is not a child of that parent"
        nxt = conn.execute(
            "SELECT position FROM unified_blocks WHERE parent_id = ? AND position > ? "
            "AND id != ? ORDER BY position LIMIT 1",
            (parent_id, row[0], block_id),
        ).fetchone()
        lo, hi = row[0], nxt[0] if nxt else None
    else:
        # Append at the end; a moving block is excluded so re-appending a block
        # already sitting last doesn't step past its own position.
        row = conn.execute(
            "SELECT position FROM unified_blocks WHERE parent_id = ? AND id != ? "
            "ORDER BY position DESC LIMIT 1", (parent_id, block_id)).fetchone()
        lo, hi = (row[0] if row else None), None
    return (lo, hi), None


def _sibling_position(conn, parent_id: str, after_id, block_id: str = "") -> tuple:
    """The one key in ``_sibling_gap``: ``(position, error)``."""
    gap, error = _sibling_gap(conn, parent_id, after_id, block_id)
    if error:
        return None, error
    try:
        return generate_key_between(*gap), None
    except Exception as e:
        return None, f"error: could not place the block: {e}"


def _read_cap(value) -> int:
    """The effective per-call document-text cap: the request's user preference
    clamped to READ_CHARS_MAX; READ_CHARS_CAP when unset (0/None/garbage)."""
    try:
        cap = int(value or 0)
    except (TypeError, ValueError):
        cap = 0
    return min(cap, READ_CHARS_MAX) if cap > 0 else READ_CHARS_CAP


class Window(NamedTuple):
    """The document-text window a read asks for (``_window_args``):
    ``budget`` characters, a PDF's ``offset`` and 1-based ``page``, a text
    file's ``from_line`` and ``to_line`` (1-based, inclusive; None: as many
    as fit ``budget``)."""
    budget: int
    offset: int
    page: int
    from_line: int
    to_line: int | None


def _window_args(scope: dict, args: dict) -> Window:
    """The document-text window a read asks for, from the call's
    ``pdf_chars`` (default and cap from the read-window preference),
    ``pdf_offset``, 1-based ``pdf_page``, ``from_line`` and ``to_line``.
    Shared by read_page and fetch_paper (which reads the first three), and
    fetch_url (its ``chars`` and ``offset`` passed under the first two); a
    malformed value falls back to its default. A scope may set its own
    default window (``read_default``: a helper reads in the widest one)."""
    cap = _read_cap(scope.get("read_chars"))
    default = min(scope.get("read_default") or READ_CHARS_DEFAULT, cap)
    try:
        budget = max(0, min(int(args.get("pdf_chars", default)), cap))
    except (TypeError, ValueError):
        budget = default
    try:
        offset = max(0, int(args.get("pdf_offset", 0)))
    except (TypeError, ValueError):
        offset = 0
    try:
        page = max(1, int(args.get("pdf_page", 1)))
    except (TypeError, ValueError):
        page = 1
    try:
        from_line = max(1, int(args.get("from_line") or 1))
    except (TypeError, ValueError):
        from_line = 1
    try:
        to_line = int(args["to_line"]) if args.get("to_line") not in (None, "") else None
    except (TypeError, ValueError):
        to_line = None
    return Window(budget, offset, page, from_line, max(to_line, from_line) if to_line is not None else None)


def gamma_link(scope: dict, page_id: str, *, pdf_page: int = 0, line: int = 0, end: int = 0, block_id: str = "",
               thread_id: str = "") -> str:
    """An absolute link into the library — a page, one PDF page of it, a
    line of its text file (``&line=N``, a range ``&line=N-M`` with ``end``),
    a note block, or with ``thread_id`` a comment thread on the block
    ``block_id`` of the page (``&page=&block=&thread=``, the address the
    Inbox opens) — for a caller whose scope sets ``link_base`` (the MCP
    adapter: ``https://host/?ws=<id>``, so an external assistant copies URLs
    out of results instead of assembling them). "" for the chat, whose model
    writes the relative ``/?page=<id>`` links the chat renders itself."""
    base = scope.get("link_base")
    if not base:
        return ""
    if thread_id:
        params = {"page": page_id, "block": block_id or page_id, "thread": thread_id}
    else:
        params = {"block": block_id} if block_id else {"page": page_id}
    if pdf_page:
        params["pdf_page"] = pdf_page
    if line:
        params["line"] = f"{line}-{end}" if end and end > line else str(line)
    return base + "&" + urlencode(params)


def host_free(scope: dict, text: str) -> str:
    """``text`` with the absolute links ``gamma_link`` hands an MCP
    assistant made relative (``https://host/?ws=<id>&page=…`` →
    ``/?page=…``), as the editor stores a pasted link of its own library
    (frontend shared/model/gammaLinks.js ``relativeGammaLink``), so a note
    keeps working when the library moves to another address. Another
    workspace's links and web links stay as written; the chat's scope has
    no ``link_base`` and its model writes relative links already."""
    base = scope.get("link_base")
    return text.replace(base + "&", "/?") if base and text else text


def _where(scope: dict, page_id: str, **link) -> str:
    """``page_id <id>``, as results name a page, with its link after a
    semicolon for a caller that wants links (``gamma_link``)."""
    url = gamma_link(scope, page_id, **link)
    return f"page_id {page_id}" + (f"; {url}" if url else "")


def _int_arg(args: dict, key: str, default: int, lo: int, hi: int) -> int:
    """An integer argument clamped to [lo, hi]; a missing or malformed one
    is ``default``."""
    try:
        return max(lo, min(int(args.get(key) or default), hi))
    except (TypeError, ValueError):
        return default


def note_pictures(content: str) -> list[tuple[str, str]]:
    """``[(alt, url)]`` of the pictures a block's markdown embeds
    (``ai_context.NOTE_IMAGE_RE``: the markdown images that name a stored
    upload), in order; the alt without its size suffix."""
    out = []
    for alt, url in NOTE_IMAGE_RE.findall(content or ""):
        if upload_name(url):
            out.append((alt.split("|", 1)[0].strip(), url))
    return out


# A deletion's revert record (every block of the subtree, parent first) is
# saved with the chat; a subtree past this many characters is deleted
# without one, and the result says so (delete_block); a folder's or label's
# deletion past it is refused (_tree_revert).
_DELETE_REVERT_MAX = 200_000


def _annotation_origin(scope: dict) -> str:
    """The server's address an entry's links start from, ``https://host/``
    for a caller with ``link_base`` (MCP), "" for the chat: an entry's link
    is its query alone, ``?ws=…&page=…&block=…`` (annotations.entries)."""
    return (scope.get("link_base") or "").partition("?")[0]


def _writer(scope: dict):
    """Who writes (``hand.Writer``): the scope's account, as the agent its
    stamp names; None for a scope with no agent."""
    from ..agents import of_stamp
    from ..annotations.hand import Writer

    made = scope.get("agent")
    return Writer(scope.get("actor") or "", made.get("for", ""), of_stamp(made)) if made else None


def _card_place(kind: str, page=None, lines=None, quote: str = "") -> dict:
    """A place as the approval card words it (chat/approvals.js
    ``placeText``): ``{kind, page?, lines?, quote?}``."""
    out = {"kind": kind}
    if page:
        out["page"] = page
    if lines:
        out["lines"] = list(lines)
    if quote:
        out["quote"] = _excerpt(quote, 200)
    return out


def _drawing_hand(scope: dict, ws: str):
    """``(writer, None)``: who draws, the account's role asked as it is now
    (a viewer draws nothing, as it highlights nothing: ``hand.may_mark``);
    ``(None, answer)`` otherwise."""
    from ..annotations import hand

    writer = _writer(scope)
    if writer is None:
        return None, "error: this chat makes no changes under its own name"
    try:
        hand.may_write(writer, ws)
        hand.may_mark(writer, ws)
    except ValueError as e:
        return None, f"error: {e}"
    return writer, None


# What an approval card shows of a text change. Words (and each CJK
# character, which has no spaces around it) are compared, so a one-word fix
# in a long note shows as that word; unchanged stretches keep this much
# text on each side of a change, and the card holds at most the limit.
_DIFF_TOKEN = re.compile(r"\s+|[\u2e80-\u9fff\uac00-\ud7af\uf900-\ufaff\uff00-\uffef]"
                         r"|[^\s\u2e80-\u9fff\uac00-\ud7af\uf900-\ufaff\uff00-\uffef]+")
_DIFF_CONTEXT = 120
_DIFF_LIMIT = 6000
_DIFF_COMPARE_MAX = 40_000  # longer texts show whole, old then new


def text_diff(old: str, new: str) -> list:
    """``[[kind, text], …]`` from ``old`` to ``new``, kind "ctx" (kept),
    "del" (removed) or "ins" (added). Long kept stretches are cut to their
    ends around "…", and the whole list to :data:`_DIFF_LIMIT` chars."""
    if len(old) + len(new) > _DIFF_COMPARE_MAX:
        parts = [["del", old], ["ins", new]]
    else:
        a, b = _DIFF_TOKEN.findall(old), _DIFF_TOKEN.findall(new)
        parts = []
        for op, i1, i2, j1, j2 in SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
            if op == "equal":
                parts.append(["ctx", "".join(a[i1:i2])])
                continue
            if i2 > i1:
                parts.append(["del", "".join(a[i1:i2])])
            if j2 > j1:
                parts.append(["ins", "".join(b[j1:j2])])
    parts = [p for p in parts if p[1]]
    out, used = [], 0
    for n, (kind, text) in enumerate(parts):
        if kind == "ctx":
            first, last = n == 0, n == len(parts) - 1
            if first and len(text) > _DIFF_CONTEXT:
                text = "…" + text[-_DIFF_CONTEXT:]
            elif last and len(text) > _DIFF_CONTEXT:
                text = text[:_DIFF_CONTEXT] + "…"
            elif len(text) > 2 * _DIFF_CONTEXT + 3:
                text = text[:_DIFF_CONTEXT] + " … " + text[-_DIFF_CONTEXT:]
        if used + len(text) > _DIFF_LIMIT:
            out.append([kind, text[:max(0, _DIFF_LIMIT - used)] + "…"])
            break
        out.append([kind, text])
        used += len(text)
    return out


def _excerpt(text: str, limit: int = 80) -> str:
    """The start of a block's text on one line, for the approval card."""
    text = " ".join((text or "").split())
    return text if len(text) <= limit else text[:limit - 1].rstrip() + "…"


def _save_folder(conn, scope: dict, raw) -> tuple[dict | None, str | None]:
    """Where save_paper files a paper (``_resolve_folder``'s answer): a
    folder chat reads the argument inside its folder (move_page's rule); a
    page chat from the top, else the open page's first folder — where the
    reply's Save to library files it."""
    if scope.get("type") == "folder" or str(raw or "").strip():
        return _resolve_folder(conn, scope, raw, write=True)
    loaded, _ = _load_scoped_page(conn, scope, {"page_id": scope.get("page_id")})
    paths = folder_paths(conn)
    first = next((f for f in (loaded[3] if loaded else []) if f in paths), "")
    return {"id": first, "path": PATH_SEP.join(paths.get(first, []))}, None


# The names a card or result shows before it counts the rest: a deletion's
# subfolders, pages and chat folders, the pages a batch's card leaves out,
# the restored pages a batch's result links.
_PREVIEW_ITEMS = 20


def _one_line(value) -> str:
    """A name or title as the model wrote it, on one line: whitespace runs
    one space, trimmed, at most ``_TITLE_MAX`` characters."""
    return re.sub(r"\s+", " ", str(value or "")).strip()[:_TITLE_MAX]


_PAGE_ID_ARG = {"page_id": {"type": "string"}}
