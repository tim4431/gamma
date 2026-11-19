"""A file of the disk into its page, push's steps by class (gamma/vault/kinds.py
``change`` and ``new``): a changed file's bytes (``_push_file``), the new
files (``_push_new``).

A new note is a page, a new PDF a page reading its file where it is, with
the note of its stem or the note whose front matter names it, and a new note
that pairs so with a paper whose notes went from where they were is those
notes moved; a new file the remote refuses is sent again once it changes,
and so are a PDF's new bytes. A changed note is merged into its page
(gamma/vault/merge.py): the page as it is with what the file changed since
the base, **the file winning** a clash and Gamma's text kept as a
``file_won`` conflict in the transaction of the ops; a block Gamma moved to
another page takes the file's change of it there, and the page's labels what
the note's ``tags`` changed, Gamma's own staying. A callout that links the
page's PDF and is no highlight Gamma knows is one of its quote and page
alone, which the relocation run then places, dashed. A note whose base names
none of its blocks (``_unknown``: a folder kept on disk before synced
vaults, gamma/vault/upgrade.py) is never merged, which would add every block
again: changed on disk it is kept until a force decides, and pull writes it
anew; a force push pairs its blocks with the page's as pull would write
them, an older client's highlight read as it wrote one (and pull then writes
that note anew). A new note whose ``gamma_id`` (an older client's line)
names a page of the vault is that page's notes, moved or found again, before
any pairing with a paper, and so is one pull finds where the page's new
notes would go, or where the client kept a sub-page's, beside its parent's.
A page in Recently deleted whose file changed comes back, and so does one
whose file is back where it lived or with its file id (``disk.came_back``).
A file pushed is its base then, and its seq the page's only when the page
says what the file says, its front matter too, so that pull writes the rest.
"""

from collections import Counter
from dataclasses import replace

from .. import md_lens
from ..blocks_store import FOLDERS, LABELS, PARENT_PAGE, new_block_id
from ..documents.model import document, stored_name
from ..markdown_import import block_class
from ..ops import CYCLE, MAX_CONTENT, OpError
from . import disk, journal, kinds, links, merge, store
from .remote import PLAN_TRIES, Changed, Edit, Gone, Page, Refused, Waiting
from .round import (Base, Read, _base, _dir, _fields, _front_props, _g, _gamma_id, _join, _kind_of, _marker, _named,
                    _Rows, _stem, _tags, _text, _token, _unknown, _why)


UNKNOWN = ("changed on this computer, and Gamma does not know which of its blocks the note held: a force pull "
           "writes Gamma's page here, a force push makes the page this file")


def _refused(e: BaseException) -> bool:
    """Whether the remote refused a write for what it is (a 4xx: malformed,
    too large for a proxy), so that the same bytes sent again would be
    refused again; not a clash with an edit made meanwhile, a timeout or a
    rate limit, which the next round tries again."""
    return isinstance(e, OpError) and not e.conflict and 400 <= e.status < 500 and e.status not in (408, 409, 429)


def _gamma_tree(doc: md_lens.Doc, entries: list, ids=None, props=None, skip=()) -> list:
    """``doc``'s tree with each block's text in Gamma (``entries``, by place),
    its id (``ids``, by place; None without) and what a new block of it
    takes (``props``, by place); the blocks of ``skip`` (places of blocks with
    no children) left out."""
    def node_of(node):
        out = {"id": ids[node.index] if ids else None, "content": _text(entries[node.index]["g"]),
               "children": [node_of(child) for child in node.children if child.index not in skip]}
        if node.anchor:
            out["anchor"] = node.anchor
        if props and props.get(node.index):
            out["props"] = props[node.index]
        return out
    return [node_of(node) for node in doc.nodes if node.index not in skip]


def _new_props(g) -> dict:
    """What a block the file brings takes: a highlight read from a callout
    that links the page's PDF has its quote and page, its place on the page
    not known yet (``pageNumber`` alone, as gamma/highlights.py allows), for
    the relocation run to find (``LocalRemote.place_highlights``)."""
    if not isinstance(g, dict):
        return {}
    return {"quote": g["quote"], "pdf_position": {"pageNumber": int(g["page"] or 1)}}


def _wrote(ops: list) -> dict:
    """What a plan's ``ops`` leave on the blocks they write, by which the
    journal knows the plan on the page when the log cannot say
    (``journal._plan_landed``): ``{id: the text set or inserted, None for one
    deleted}``."""
    out = {}
    for op in ops:
        if op["op"] == "delete":
            out[op["id"]] = None
        elif op["op"] in ("set", "insert") and "content" in op:
            out[op["id"]] = op["content"]
    return out


def _labels_with(page: list, base: list, file: list) -> list:
    """The labels a page takes when its note's ``tags`` changed since the
    base: the file's tags in its order, but one Gamma took off since (in the
    base, not on the page), then Gamma's own (on the page, in neither). Names
    compare ignoring case, as a tag is read (``Round._front``)."""
    def folded(names):
        return {name.casefold() for name in names}
    on, was, now = folded(page), folded(base), folded(file)
    return [tag for tag in file if tag.casefold() in on or tag.casefold() not in was] + \
        [name for name in page if name.casefold() not in now | was]


def _row_folder(directory: str, folder_id: str) -> str:
    """The first filing a row records for a file in ``directory``: its folder,
    "" for the vault's root, as pull reads a page's (``pull._pull_page``)."""
    return folder_id if directory else ""


def fits(texts) -> None:
    """``Unwritable`` for a note with a block past what the op path takes
    (``ops.MAX_CONTENT``), its file's problem: no page holds what no edit
    could change. Gamma's file editor asks it too."""
    longest = max(map(len, texts), default=0)
    if longest > MAX_CONTENT:
        raise md_lens.Unwritable(f"a block of {longest:,} characters is longer than Gamma takes ({MAX_CONTENT:,})")


def _insertable(node: dict) -> dict:
    """A lens tree node as ``insert_page_tree`` takes it."""
    return {"id": node["id"], "content": node["content"], "properties": node.get("props") or {},
            "children": [_insertable(child) for child in node["children"]]}


# --- push: a changed file ---------------------------------------------------------------------

def _push_file(rnd, path: str, found) -> None:
    """A file of the rows whose stat moved: the same bytes refresh its row
    and, for a document read in place, the stat of its linked row too
    (``remote.restat_doc``: a file touched goes on being served; a listed
    file's size is its bytes), new ones go to its page as its class sends
    them (``Kind.change``), a note's only where the vault pushes
    everything (else it is kept)."""
    row = rnd.rows.get(path)
    if row is None or found is None or found.placeholder:
        return
    read = rnd.reads.get(path) or rnd._read(path, found)
    same = read.hash == row["hash"] and (not read.listed or read.size == row["size"])
    if same and rnd.force != "push":
        rnd._put(path, row, **read.stat(), missing_since=0)
        cls = kinds.by_kind(read.kind)
        if cls.in_place and read.hash and row["page_id"]:
            rnd.remote.restat_doc(str(rnd.root / path), read.seen, cls.link_name(path, row))
        return
    kinds.by_kind(read.kind).change(rnd, path, read, row)


def _push_doc(rnd, path: str, read: Read, row: dict) -> None:
    """A document read in place with new bytes (a PDF, a work folder's
    file) is its page's: the disk wins (a followed file's rule,
    ``gamma_sync.Push``; a file replaced in Gamma stands until the file
    changes). A work folder's file that is listed, or was (past the keep
    limit either way), takes one set of its document instead
    (``_set_file_doc``). Bytes the remote refuses are sent again once they
    change, never each round."""
    cls, page_id, leaf = kinds.by_kind(read.kind), row["page_id"], path.rsplit("/", 1)[-1]
    state = rnd.remote.state(page_id) if page_id else None
    if state is None:
        store.forget(rnd.id, path)
        rnd.rows.pop(path, None)
        _making(rnd, read, cls.new, rnd, path, read)
        return
    doc = rnd.remote.doc_of(page_id)
    if state == "trashed":
        rnd.remote.restore(page_id)
    token = _token()
    fields = {**read.stat(), "missing_since": 0, "problem": "", "ours": 0}     # bytes of this computer's now
    store.put_pending(rnd.id, path, read.kind, {"op": "push", "token": token, "hash": read.hash,
                                                    "text": cls.pending_text(read), "nodes": None,
                                                    "page_id": page_id, "fields": fields})
    try:
        if cls is kinds.FILE and (read.listed or not row["hash"]):
            _set_file_doc(rnd, page_id, path, read, row)
        elif doc != read.hash:
            rnd.remote.replace_doc(page_id, str(rnd.root / path), read.seen, leaf, format=read.format, ext=read.ext)
        else:
            rnd.remote.link_doc(str(rnd.root / path), read.seen, page_id, read.ext)
    except BaseException as e:
        if read.listed or rnd.remote.doc_of(page_id) != read.hash:     # a listed read's hash says nothing
            store.drop(rnd.id, path, token)
            if isinstance(e, Exception) and not isinstance(e, (disk.Stopped, disk.Offline, Refused,
                                                               Waiting)):
                rnd._put(path, row, **read.stat(), problem=_why(e))     # refused: not again until it changes
        raise
    store.promote(rnd.id, path, token, **fields)
    rnd._promoted(path)
    rnd.counts["sent"] += 1


def _set_file_doc(rnd, page_id: str, path: str, read: Read, row: dict) -> None:
    """A work folder's file across the keep limit, either way, or listed
    with a new size: its page's document set to the file's shape now
    (``remote.set_doc``: listed with no bytes, or read in place), the
    linked row made first for one read in place, the row of the bytes
    before gone after."""
    at, leaf = str(rnd.root / path), path.rsplit("/", 1)[-1]
    if read.listed:
        doc = document("", name=leaf, at="listed", format=read.format, ext=read.ext, size=read.size)
    else:
        doc = document(read.hash, name=leaf, at="path", format=read.format, ext=read.ext, size=read.size)
        rnd.remote.link_doc(at, read.seen, page_id, read.ext)
    rnd.remote.set_doc(page_id, doc)
    if row["hash"] and row["hash"] != read.hash:
        rnd.remote.unlink_doc(at, stored_name(row["hash"], read.ext))


def _push_note(rnd, path: str, read: Read, row: dict) -> None:
    """A note with new bytes into its page (gamma/vault/merge.py); one whose
    page went for good is a new page, one in Recently deleted comes back.
    One that cannot be read as text keeps the base it had, so that pull
    finds it changed under it and never writes over it."""
    if read.problem:
        rnd._put(path, row, **read.stat(), problem=read.problem)
        rnd._keep(path, read.problem)
        return
    page_id = row["page_id"]
    state = rnd.remote.state(page_id) if page_id else None
    if state is None:
        store.forget(rnd.id, path)
        rnd.rows.pop(path, None)
        _making(rnd, read, kinds.NOTES.new, rnd, path, read)
        return
    if _unknown(row) and rnd.force != "push":
        rnd._hold(path)
        rnd._keep(path, UNKNOWN, True)
        return
    if state == "trashed":
        rnd.remote.restore(page_id)     # the file is the authority
        row = _settled_push(rnd, path, row)
    _merge_into(rnd, page_id, path, read, row)


def _settled_push(rnd, path: str, row: dict) -> dict:
    """The row of a note whose push the journal could not settle while
    its page was in Recently deleted (``journal._plan_landed`` None), settled now
    that the page is back and its log reads again, so that the note is
    merged from the base that plan left."""
    pending = row.get("pending") or {}
    if pending.get("op") != "push":
        return row
    if journal._plan_landed(rnd, row["page_id"], pending):
        store.promote(rnd.id, path, pending["token"], **(pending.get("fields") or {}))
    else:
        store.drop(rnd.id, path, pending["token"])
    rnd.rows[path] = row = store.get(rnd.id, path)
    return row


def _merge_into(rnd, page_id: str, path: str, read: Read, row: dict | None, extra=None) -> None:
    """The note's text merged into its page: planned before the lock, for
    the journal, and written as planned, or planned again when Gamma wrote
    the page in between (``PLAN_TRIES``, then the next round); the file
    wins a clash (gamma/vault/merge.py), and a block Gamma moved to another page
    takes the file's change of it there (``_follow``). Then the labels and
    the seq (``_settled``). ``row`` None: a note new to a page that has a
    file already (a paper's), ``extra`` the fields its row starts with."""
    doc = md_lens.parse(read.text)
    if doc.problem:
        raise md_lens.Unwritable(doc.problem)
    base = _base(row)
    ctx = rnd._ctx_at(path, page_id)
    gone = store.kept_gone((row or {}).get("gone"))
    old, new = md_lens.flatten(md_lens.tree_of(base.doc, base.ids)) if base else [], md_lens.flatten(md_lens.tree_of(doc))
    pairs = _with_moves(old, new, md_lens.match(old, new, gone))
    entries, props, placing = [], {}, False
    for n, node in enumerate(doc.flat):
        i = pairs.get(n)
        was = base.nodes[i] if base and i is not None and i < len(base.nodes) else None
        if was is not None and base.doc.flat[i].content == node.content:
            g, memo = was["g"], was.get("m") or {}      # identity first
        else:
            g, memo = links.to_gamma(node.content, (was or {}).get("m") or {}, ctx,
                                           base=was["g"] if was else None)
        entries.append({"g": g, **({"m": memo} if memo else {}), **({"a": node.anchor} if node.anchor else {})})
        if isinstance(g, dict):          # what it takes if it is new, or back after Gamma deleted it
            props[n] = _new_props(g)
            placing = placing or not isinstance((was or {}).get("g"), dict)
    fits(_text(e["g"]) for e in entries)
    base_tree = _gamma_tree(base.doc, base.nodes, base.ids) if base else []
    file_tree = _gamma_tree(doc, entries, props=props)
    claimed = {(old[i]["id"] if i < len(old) else gone[i - len(old)]["id"]) for i in pairs.values()}
    known = set(base.ids if base else ()) | {e["id"] for e in gone}
    kind_of, force = _kind_of(page_id), rnd.force == "push"
    older = force and _unknown(row)      # an older folder's note forced: read against the page as pull writes it
    fm_ops, labels = _front_ops, _file_labels(rnd, page_id, doc, base)
    for _ in range(PLAN_TRIES):
        page = rnd.remote.page(page_id)
        if page is None or page.trashed:
            raise Waiting("the page went while its note was read")
        token = _token()
        premerged = merge.premerge(page.blocks, base_tree, file_tree, pairs)     # the slow part, before the lock
        folded = set()

        def plan(now, held):
            if force:
                projected = md_lens.project(now.blocks, kind_of)
                if older:
                    tree, by_page, folds = _older_tree(rnd, now, projected, doc, entries, props, ctx)
                    folded.update(folds)
                else:
                    tree, by_page = file_tree, _by_page(projected, base, pairs)
                edit = _forced(now, projected, tree, by_page, token, known, kind_of)
                return replace(edit, ops=edit.ops + _label_ops(now, labels))
            # the base's blocks the lens left unpaired too: one moved away may be the file's, its class changed
            holders = held((claimed | set(base.ids if base else ())) - set(now.blocks))
            merged = merge.merge_page(now.blocks, page_id, base_tree, file_tree, pairs, kind_of=kind_of,
                                            merge=premerged, gone=gone, held={b for b, live in holders.items() if not live},
                                            away={b for b, live in holders.items() if live})
            merged = _named(merged, token, known)
            return Edit(merged.ops + fm_ops(now, doc, base, labels), merged.conflicts, False, merged.ids, merged.level,
                        merged.deleted, merged.away)
        expected = plan(page, lambda ids: rnd.remote.held(page_id, ids))
        try:
            for moved in expected.away:
                _follow(rnd, moved)
        except Gone:
            continue          # that page went to Recently deleted meanwhile: planned again
        nodes = None if folded else [{"id": block_id, **entry} for block_id, entry in zip(expected.ids, entries)]
        fields = {**read.stat(), "missing_since": 0, "problem": "", "ours": 0, **(extra or {}),    # the person's bytes now
                  "gone": store.with_gone((row or {}).get("gone"), _dropped(base, page, expected.deleted))}
        pending = {"op": "push", "token": token, "hash": read.hash, "text": read.text, "nodes": nodes,
                   "page_id": page_id, "at": page.seq, "fields": fields, "wrote": _wrote(expected.ops)}
        store.put_pending(rnd.id, path, read.kind, pending)
        try:
            _before, seq, edit = rnd.remote.apply(page_id, token=token, built=(page.seq, expected))
            break
        except (Changed, Gone) as e:
            store.drop(rnd.id, path, token)
            if isinstance(e, Gone):
                raise Waiting("the page went while its note was read") from None
        except BaseException as e:
            if journal._plan_landed(rnd, page_id, pending) is False:
                store.drop(rnd.id, path, token)
                if row is not None and _refused(e):      # not again until the file changes
                    rnd._put(path, row, **read.stat(), problem=_why(e))
            raise
    else:
        raise Waiting("the page kept changing while its note was read; it is read again")
    # An older client's highlights forced in, two blocks of the file for one of the page: the note is as the
    # client wrote it now, and pull writes it once more by the lens, its bytes to the history.
    unknown = {"text": "", "nodes": None, "seq": -1} if folded else {}
    store.promote(rnd.id, path, token, **{**fields, "seq": row["seq"] if row else 0, **unknown})
    rnd._promoted(path)
    rnd._known_note(path, page_id, nodes)
    _settled(rnd, page_id, path, read.text, doc, seq, edit.level and not folded)
    if placing:
        rnd._find_highlights(page_id)
    rnd.counts["sent"] += 1
    if not edit.resolved:
        rnd.counts["conflicts"] += len(edit.conflicts)


def _older_tree(rnd, page: Page, projected: list, doc: md_lens.Doc, entries: list, props: dict, ctx):
    """A force push's file tree, and its blocks' places in the page's
    projection, for a note whose base names none of its blocks (an older
    folder's): the file's blocks paired with the page's as pull would
    write them (``md_lens.match``), so that a block the note kept keeps its
    id and its properties, a highlight its quote, rectangles and colour;
    and the older client's highlight read as it wrote one, the callout
    then its note as the paragraph after, where the page's highlight it
    pairs with has a note. → (tree, {file index: projected index}, the
    places of the paragraphs read so)."""
    pairs = md_lens.match(md_lens.flatten(rnd._file_tree(page, projected, None, ctx, {})),
                          md_lens.flatten(md_lens.tree_of(doc)))
    flat, entries, folded = md_lens.flatten(projected), list(entries), set()
    for n, node in enumerate(doc.flat):
        g, k = entries[n]["g"], pairs.get(n)
        siblings = node.parent.children if node.parent else doc.nodes
        at = next(i for i, s in enumerate(siblings) if s is node) + 1
        note = siblings[at] if at < len(siblings) else None
        if (isinstance(g, dict) and not g["note"] and k is not None and page.blocks[flat[k]["id"]]["content"]
                and note is not None and not node.children and not note.children and note.index not in pairs
                and block_class(note.content) == "plain"):
            entries[n] = {**entries[n], "g": {**g, "note": _text(entries[note.index]["g"])}}
            folded.add(note.index)
    place = {i: k for k, i in enumerate(i for i in range(len(doc.flat)) if i not in folded)}
    tree = _gamma_tree(doc, entries, props=props, skip=folded)
    return tree, {place[n]: k for n, k in pairs.items() if n in place}, folded


def _file_labels(rnd, page_id: str, doc: md_lens.Doc, base: Base | None) -> list | None:
    """The ids of the labels the page takes with the push of its note when
    the note's ``tags`` changed since the base (``_labels_with``: Gamma's
    own stay), made first where missing (a push that then does not land
    leaves an unused label); None when they did not change. They go in the
    plan with the blocks, so that no round that dies in between leaves a
    base that names a tag the page does not have, which the next pull
    would take out of the file."""
    tags = _tags(doc.front.fields if doc.front else {})
    was = _tags(base.doc.front.fields if base and base.doc.front else {})
    if tags == was:
        return None
    info = rnd.remote.layout(page_id).pages.get(page_id)
    names = _labels_with(info.labels if info else was, was, tags)
    return rnd.remote.label_ids(names) if names else []


def _settled(rnd, page_id: str, path: str, text: str, doc: md_lens.Doc, seq: int, level: bool) -> None:
    """After a push of the note at ``path``: the base takes the page's seq
    only when the page then says what the file says, its blocks
    (``level``) and its front matter, its labels among it (they went with
    the blocks, ``_file_labels``), so that pull writes a title, a label or
    an alias Gamma gave it."""
    if level and doc.front is not None:      # a note with none never takes Gamma's title, labels or aliases
        info = rnd.remote.layout(page_id).pages.get(page_id)
        level = info is not None and info.seq == seq and rnd._front(text, info, new=False) == text
    if not level:
        return
    store.put(rnd.id, path, seq=seq)
    if path in rnd.rows:
        rnd.rows[path]["seq"] = seq


def _follow(rnd, moved: dict) -> None:
    """A block Gamma moved to another page whose text the file changed
    (``Merged.away``): the change goes to that page, merged into what it
    holds there (``merge.file_wins``; a clash the file wins, Gamma's
    text a conflict of that page's), before this note's page is written,
    so that a round that dies in between, or a plan made again, follows it
    again to the same text: ``file_wins`` makes a change once that the
    block holds already. The line then leaves this note's file, its block
    living there."""
    page_id = rnd.remote.page_of(moved["block_id"])
    if page_id is None:
        return

    def plan(page, _held):
        block = page.blocks.get(moved["block_id"])
        if block is None:
            return Edit([])
        text, clean = merge.file_wins(moved["base"], moved["file"], block["content"])
        if text == block["content"]:
            return Edit([])
        conflicts = [] if clean else [{"block_id": moved["block_id"], "mine": block["content"], "theirs": text,
                                       "result": text, "base": moved["base"]}]
        return Edit([{"op": "set", "id": moved["block_id"], "content": text}], conflicts)
    rnd.remote.apply(page_id, plan)


def _front_ops(page: Page, doc: md_lens.Doc, base: Base | None, labels=None) -> list:
    """The page's title and aliases, when the note's front matter changed
    them since the base, and its labels, ``labels`` their ids
    (``_file_labels``; None: as they are)."""
    now = doc.front.fields if doc.front else {}
    was = base.doc.front.fields if base and base.doc.front else {}
    root = page.blocks[page.id]
    op = {"op": "set", "id": page.id}
    if now != was:
        title, props, _labels = _front_props(now, root["content"])
        if now.get("title") and now.get("title") != was.get("title") and title != root["content"]:
            op["content"] = title
        if now.get("aliases") != was.get("aliases") and props.get("aliases") != root["props"].get("aliases"):
            op["props"] = {"aliases": props.get("aliases")}
    for labelled in _label_ops(page, labels):
        op["props"] = {**op.get("props", {}), **labelled["props"]}
    return [op] if len(op) > 2 else []


def _dropped(base: Base | None, page: Page, deleted: list) -> list:
    """The blocks the file dropped as ``gone`` keeps them: the hash of
    their file text, their anchor, their properties (a highlight's quote,
    page and rectangles). One the base never held (a force push takes
    Gamma's blocks too) has its text in Gamma and, for a highlight, the
    anchor pull would have given it."""
    at = {block_id: i for i, block_id in enumerate(base.ids)} if base else {}
    out = []
    for block_id in deleted:
        block = page.blocks.get(block_id)
        if block is None:
            continue
        if block_id in at:
            node = base.doc.flat[at[block_id]]
            text, anchor = node.content, node.anchor
        else:
            g = _g(block, page.id)
            text, anchor = _text(g), _marker(block_id) if isinstance(g, dict) else ""
        out.append({"id": block_id, "hash": md_lens.content_hash(text), "anchor": anchor, "props": block["props"]})
    return out


# --- push: new files ----------------------------------------------------------------------

def _push_new(rnd, paths: list) -> None:
    """New files: one a page in Recently deleted had brings it back
    (``disk.came_back``'s evidence: where it lived, or by its file
    id), and so does one of a page of the vault whose file went to the
    trash, but never into a page that left the vault; the rest are pages,
    a PDF and the note that pairs with it as one, then a work folder's
    files of other formats (before the notes, so that a note made in the
    same round links a picture's row), and a note of a paper that has a
    page already its notes. A note whose ``gamma_id`` names a page is
    that page's before any pairing: one an older client wrote beside its
    paper (its notes turned off since) holds the page's blocks already,
    and merged from no base it would add each again. A file the remote
    refuses waits until it changes (``_making``). A file at the place of
    an in-place tombstone (a file the selection left, ticked again) is
    that page back first, whatever its bytes."""
    tombs = [t for t in store.tombstones(rnd.id) if t["page_id"] and not t["pending"]]
    kept = {t["was"].casefold(): t for t in tombs if store.in_place(t)}
    restored = set()
    for path in paths:
        tomb = kept.get(path.casefold())
        if tomb is not None and rnd.remote.state(tomb["page_id"]) is not None:
            rnd._counted(path, _unbury, rnd, path, tomb)
            restored.add(path)
    tombs = [t for t in tombs if not store.in_place(t)]
    left = [p for p in paths if p not in restored]
    back = disk.came_back([rnd._known(t["was"], t) for t in tombs],
                                [rnd._known_read(rnd.reads[p]) for p in left]) if tombs else {}
    for path, k in back.items():
        page_id = tombs[k]["page_id"]
        state = rnd.remote.state(page_id)
        if state == "trashed" or state == "live" and page_id in rnd.remote.layout(page_id).pages:
            rnd._counted(path, _unbury, rnd, path, tombs[k])
            restored.add(path)
    left = [p for p in paths if p not in restored]
    pdfs = [p for p in left if rnd.reads[p].kind == kinds.PDF.name]
    notes = [p for p in left if rnd.reads[p].kind == kinds.NOTES.name]
    partner, attach = {}, {}
    for note in notes:
        pdf = _pdf_of(rnd, note, pdfs)
        if pdf in pdfs and pdf not in partner:
            partner[pdf] = note
        elif (pdf in rnd.rows and rnd.rows[pdf]["page_id"]
              and kinds.NOTES.name not in rnd.rows.of(rnd.rows[pdf]["page_id"])):
            attach[note] = pdf
    for pdf in pdfs:
        rnd._counted(pdf, _making, rnd, rnd.reads[pdf], kinds.PDF.new, rnd, pdf, rnd.reads[pdf], partner.get(pdf),
                     n=1 + (pdf in partner))
    for path in (p for p in left if rnd.reads[p].kind == kinds.FILE.name):
        rnd._counted(path, _making, rnd, rnd.reads[path], kinds.FILE.new, rnd, path, rnd.reads[path])
    for note in (p for p in notes if p not in partner.values()):
        named = _named_page(rnd, note)      # first: an older client's note beside its paper is no new one
        if named:
            rnd._counted(note, _adopt, rnd, note, named)
        elif note in attach:
            rnd._counted(note, _attach_note, rnd, note, attach[note])
        else:
            rnd._counted(note, _making, rnd, rnd.reads[note], kinds.NOTES.new, rnd, note, rnd.reads[note])
    for note, value in sorted(rnd.parents.items()):        # once every new note is a page: a parent may be one
        rnd._each(note, _sub_page, rnd, note, value)


def _making(rnd, read: Read, make, *args) -> None:
    """``make(*args)``, a page made of the new file ``read``: one the
    remote refused keeps a row of no page with the file's stat and why,
    so that it is read and sent again once it changes, never each round
    (a 300 MB PDF behind a proxy's 1 MB limit). A page made without its
    blocks keeps its journal instead (``journal._recover``)."""
    try:
        make(*args)
    except (disk.Stopped, disk.Offline, Refused, Waiting, md_lens.Unwritable):
        raise
    except Exception as e:
        if store.get(rnd.id, read.path) is None:
            store.put(rnd.id, read.path, kind=read.kind, hash="", problem=_why(e), **read.stat())
        raise


def _sub_page(rnd, path: str, value) -> None:
    """A new note whose front matter names its ``parent`` (``[[Plan]]``, a
    path), a note of the vault: its page that note's page's sub-page, as
    the vault import makes it, filed where its top is in Gamma while its
    file stays where the person keeps it (the rows record that filing, so
    pull moves nothing). A loop of parents leaves it a library page."""
    row = rnd.rows.get(path)
    notes = [p for p, r in rnd.rows.items() if r["kind"] == kinds.NOTES.name and r["page_id"] and p != path]
    hit = links.named(value, path, notes) if row and isinstance(value, str) else ""
    parent = rnd.rows[hit]["page_id"] if hit else ""
    if not parent or parent == row["page_id"] or parent not in rnd.remote.layout(parent).pages:
        return
    try:
        before, after = rnd.remote.set_page(row["page_id"], props={PARENT_PAGE: parent})
    except OpError as e:
        if e.conflict != CYCLE:
            raise
        return
    layout = rnd.remote.layout(row["page_id"])
    info = layout.pages.get(row["page_id"])
    folder = rnd._first(info.folders, layout.folders) if info else row["folder"]
    for at, at_row in rnd.rows.of(row["page_id"]).values():
        rnd._put(at, at_row, folder=folder, seq=after if at_row["seq"] == before else at_row["seq"])


def _unbury(rnd, path: str, tomb: dict) -> None:
    """A file whose page is in Recently deleted came back: the page too.
    Its row is this file's now. The copy the vault's trash kept of it goes
    when it holds the same bytes; one that does not (a note come back
    edited) keeps a row of no page, for the sweep to take once the trash
    has held it ``disk.TRASH_DAYS`` (``pull._sweep``)."""
    rnd.remote.restore(tomb["page_id"])
    store.unbury(rnd.id, tomb["path"], path)
    kinds.by_kind(tomb["kind"]).moved(rnd, path, store.on_disk(tomb), tomb)
    if tomb["path"].startswith(f"{disk.TRASH}/") and rnd._there(tomb["path"]):
        if tomb["hash"] == rnd.reads[path].hash:
            disk.purge(rnd.root, tomb["path"])
        else:
            store.put(rnd.id, tomb["path"], kind=tomb["kind"], hash=tomb["hash"], was=tomb["was"])
    rnd.rows = _Rows(store.files(rnd.id))
    if rnd.work:
        _filed_back(rnd, tomb["page_id"], path)
    _push_file(rnd, path, rnd.scan.files[path])
    rnd.counts["sent"] += 1


def _filed_back(rnd, page_id: str, path: str) -> None:
    """A work folder's page come back with its file ``path``: filed first in
    that file's directory's folder, made where missing (a directory gone
    with its pages, back with them), then in its folders outside every
    work folder that are still there, as ``push._moved`` files it."""
    folders, _parent = rnd.remote.filing(page_id)
    tree = rnd.remote.work_tree()
    want = [rnd._folder_for(_dir(path))] + [f for f in folders if f in tree and not tree.work_of(f)]
    if want == list(folders):
        return
    before, after = rnd.remote.set_page(page_id, props={FOLDERS: want})
    folder = rnd._first(want, rnd.remote.layout(page_id).folders)
    for at, at_row in rnd.rows.of(page_id).values():
        rnd._put(at, at_row, folder=folder, seq=after if at_row["seq"] == before else at_row["seq"])


def _pdf_of(rnd, note: str, pdfs: list) -> str:
    """The PDF a new note is the notes of: the one its front matter's
    ``source`` names, else the one of its stem beside it, else, for a note
    in the vault's notes folder, the one of its stem anywhere ("" for
    none). New PDFs and those the rows hold count."""
    source = str(_fields(rnd.reads[note].text).get("source") or "").strip()
    candidates = [*pdfs, *(p for p, r in rnd.rows.items() if r["kind"] == kinds.PDF.name)]
    if source:
        hit = links.named(source, note, candidates)
        if hit:
            return hit
    beside = _join(_dir(note), f"{_stem(note)}.pdf").lower()
    hit = next((pdf for pdf in candidates if pdf.lower() == beside), "")
    folder = (rnd.marker.get("notes_folder") or "").strip("/")
    if not hit and folder and _dir(note) == folder:
        same = [pdf for pdf in candidates if _stem(pdf).lower() == _stem(note).lower()]
        hit = same[0] if len(same) == 1 else ""
    return hit


def _paired_page(rnd, pdf: str) -> str:
    """The page a note is already, without a PDF, that a new PDF is the
    file of: by the note's ``source`` or its stem beside ("" for none)."""
    if rnd._sources is None:
        rnd._sources = {}
        for path, row in rnd.rows.items():
            if row["kind"] == kinds.NOTES.name and row["page_id"]:
                source = str(_fields(row["text"]).get("source") or "").strip()
                if source:
                    rnd._sources[path] = source
    note = next((path for path, source in rnd._sources.items() if links.named(source, path, [pdf])), None)
    if note is None:
        note = next((p for p in rnd.rows if p.lower() == _join(_dir(pdf), f"{_stem(pdf)}.md").lower()), None)
    row = rnd.rows.get(note) if note else None
    if row and row["kind"] == kinds.NOTES.name and row["page_id"] and kinds.PDF.name not in rnd.rows.of(row["page_id"]):
        return row["page_id"]
    return ""


def _new_note(rnd, path: str, read: Read) -> None:
    """A note that is no page yet: a new page, titled by its stem or its
    front matter's ``title`` (no automatic title: a lookup never renames
    it), filed in its directory's folder, with its blocks; a note that
    cannot be read as text is a page with none and its reason."""
    doc = md_lens.parse(read.text) if read.text is not None else None
    title, props, labels = _front_props(doc.front.fields if doc and doc.front else {}, _stem(path))
    folder = rnd._folder_for(_dir(path))
    if folder:
        props[FOLDERS] = [folder]
    problem = read.problem or (doc.problem if doc else "")
    _create(rnd, path, read, new_block_id(), title, props, labels, _row_folder(_dir(path), folder),
            note=None if problem else (path, read, doc), problem=problem)


def _named_page(rnd, note: str) -> str:
    """The page of the vault a new note's ``gamma_id`` names, when that
    page has no notes file here ("" for none)."""
    page_id = _gamma_id(rnd.reads[note].text)
    if not page_id or kinds.NOTES.name in rnd.rows.of(page_id):
        return ""
    return page_id if page_id in rnd.remote.layout(page_id).pages else ""


def _adopt(rnd, path: str, page_id: str, read: Read | None = None) -> None:
    """A new note whose ``gamma_id`` names a page of the vault: that page's
    notes, its base naming none of its blocks (``_unknown``), kept until a
    force decides. ``read``: its stat, where this round did not read it
    (pull found it where the page's new notes would go)."""
    read = read or rnd.reads[path]
    store.put(rnd.id, path, kind=kinds.NOTES.name, page_id=page_id, hash="", text="", nodes=None, title=None,
                    folder=None, seq=-1, ours=0, **read.stat())
    rnd.rows = _Rows(store.files(rnd.id))
    rnd._hold(path)
    rnd._keep(path, UNKNOWN, True)


def _attach_note(rnd, path: str, pdf: str) -> None:
    """A new note of a paper whose page has its PDF already: its blocks
    merged into that page from no base."""
    pdf_row = rnd.rows[pdf]
    _merge_into(rnd, pdf_row["page_id"], path, rnd.reads[path], None,
                {"title": pdf_row["title"], "folder": pdf_row["folder"], "ours": 0})


def _new_pdf(rnd, path: str, read: Read, note: str | None = None) -> None:
    """A PDF that is no page yet: a page reading it where it is, with the
    note it pairs with when there is one; or the file of a page a note is
    already."""
    paired = _paired_page(rnd, path)
    if paired:
        notes = rnd.rows.of(paired)[kinds.NOTES.name][1]
        token = _token()
        fields = {**read.stat(), "seq": notes["seq"], "title": notes["title"], "folder": notes["folder"], "problem": ""}
        store.put_pending(rnd.id, path, read.kind, {"op": "push", "token": token, "hash": read.hash,
                                                        "text": kinds.PDF.pending_text(read), "nodes": None,
                                                        "page_id": paired, "fields": fields})
        rnd.remote.replace_doc(paired, str(rnd.root / path), read.seen, path.rsplit("/", 1)[-1])
        store.promote(rnd.id, path, token, **fields)
        rnd._promoted(path)
        rnd.counts["sent"] += 1
        return
    page_id = new_block_id()
    note_read = rnd.reads.get(note) if note else None
    doc = md_lens.parse(note_read.text) if note_read and note_read.text is not None else None
    title, props, labels = _front_props(doc.front.fields if doc and doc.front else {}, _stem(note or path))
    props["doc"] = document(read.hash, name=path.rsplit("/", 1)[-1], at="path", size=read.size if rnd.work else None)
    folder = rnd._folder_for(_dir(path))
    if folder:
        props[FOLDERS] = [folder]
    rnd.remote.link_doc(str(rnd.root / path), read.seen, page_id, ".pdf")
    try:
        _create(rnd, path, read, page_id, title, props, labels, _row_folder(_dir(path), folder),
                note=(note, note_read, doc) if note and doc is not None and not doc.problem else None)
    except BaseException:
        if rnd.remote.state(page_id) is None:
            rnd.remote.unlink_doc(str(rnd.root / path), stored_name(read.hash, ".pdf"))
        raise


def _new_file(rnd, path: str, read: Read) -> None:
    """A work folder's file of another format that is no page yet: a page
    reading it where it is (its document at ``path``: the format read
    with it, its extension, its size), or listing it without its bytes
    (at ``listed``) past the keep limit; titled by its leaf, filed in its
    directory's folder, never in the library by the switch
    (``kinds.FILE``)."""
    page_id, leaf = new_block_id(), path.rsplit("/", 1)[-1]
    if read.listed:
        doc = document("", name=leaf, at="listed", format=read.format, ext=read.ext, size=read.size)
    else:
        doc = document(read.hash, name=leaf, at="path", format=read.format, ext=read.ext, size=read.size)
    props = {"doc": doc}
    folder = rnd._folder_for(_dir(path))
    if folder:
        props[FOLDERS] = [folder]
    if not read.listed:
        rnd.remote.link_doc(str(rnd.root / path), read.seen, page_id, read.ext)
    try:
        _create(rnd, path, read, page_id, leaf, props, [], _row_folder(_dir(path), folder))
    except BaseException:
        if not read.listed and rnd.remote.state(page_id) is None:
            rnd.remote.unlink_doc(str(rnd.root / path), stored_name(read.hash, read.ext))
        raise


def _create(rnd, path: str, read: Read, page_id: str, title: str, props: dict, labels: list, folder: str, *,
            note=None, problem: str = "") -> None:
    """A new page, in the journal first: the file ``path`` its note, its
    PDF or, in a work folder, its file of another format, ``note`` (path,
    read, doc) the note that pairs with a PDF. A work folder's is its
    file: stamped with the work folder and the file's name, in the library
    while the vault's switch says so and its class joins it
    (``Kind.in_library``: notes and PDFs), and never a sub-page."""
    token = _token()
    tree, nodes = [], []
    cls = kinds.by_kind(read.kind)
    pdf = path if cls is kinds.PDF else ""
    if rnd.work:
        props = {**props, "work_folder": rnd.link["folder_id"], "file_name": path.rsplit("/", 1)[-1]}
        if rnd.work["library"] and cls.in_library:
            props["library"] = True
    if note is not None:
        notes_path, notes_read, notes_doc = note
        ctx = rnd._ctx_at(notes_path, page_id, pdf)
        if pdf:
            ctx.add_file(pdf, stored_name(read.hash, ".pdf"))     # its callouts link the paper this page is made of
        entries, by_place = [], {}
        for n, node in enumerate(notes_doc.flat):
            g, memo = links.to_gamma(node.content, {}, ctx)
            entries.append({"g": g, **({"m": memo} if memo else {}), **({"a": node.anchor} if node.anchor else {})})
            if isinstance(g, dict):
                by_place[n] = _new_props(g)
        fits(_text(e["g"]) for e in entries)
        ids = [f"{token}{n:x}" for n in range(len(notes_doc.flat))]
        tree = [_insertable(node) for node in _gamma_tree(notes_doc, entries, ids, by_place)]
        nodes = [{"id": block_id, **entry} for block_id, entry in zip(ids, entries)]
        if notes_doc.front and notes_doc.front.fields.get("parent") and not rnd.work:
            rnd.parents[notes_path] = notes_doc.front.fields["parent"]     # once every new page is made
    files = {path: (read, cls.pending_text(read))}
    if note is not None and note[0] != path:
        files[note[0]] = (note[1], note[1].text)
    stamp = {"seq": 0, "title": title, "folder": folder, "ours": 0}
    pendings = {}
    for at, (r, text) in files.items():
        notes = kinds.by_kind(r.kind) is kinds.NOTES
        pendings[at] = {
            "op": "create", "token": token, "hash": r.hash, "text": None if notes and problem else text,
            "nodes": (None if problem else nodes) if notes else None, "page_id": page_id,
            "fields": {**r.stat(), **stamp, "problem": problem if notes else ""}}
        store.put_pending(rnd.id, at, r.kind, pendings[at])
    try:
        seq = rnd.remote.create(page_id, title, props, tree, labels)
    except BaseException as e:
        made = rnd.remote.state(page_id) is not None
        for at in files:
            if not made:
                store.drop(rnd.id, at, token)
            elif isinstance(e, Exception):      # refused after the page was made: it stays the note's
                journal._settle_create(rnd, at, pendings[at])
        raise
    for at, (r, _text_) in files.items():
        store.promote(rnd.id, at, token, **{**r.stat(), **stamp, "seq": seq,
                                                 "problem": problem if kinds.by_kind(r.kind) is kinds.NOTES else ""})
        rnd._promoted(at)
    if note is not None:
        rnd._known_note(note[0], page_id, nodes)
        if any(isinstance(e["g"], dict) for e in nodes):
            rnd._find_highlights(page_id)
    if problem:
        rnd._keep(path, problem)
    rnd.counts["sent"] += 1


def _with_moves(old: list, new: list, pairs: dict) -> dict:
    """``md_lens.match``'s pairs of a file against its base, and each block
    the file only moved: one left unpaired whose text is that of one base
    block left unpaired, and of no other block left on either side. The lens
    pairs identical text in order, so a line moved past others reads as one
    deleted and one new, and a block Gamma had taken to another page or
    deleted would come back with it, which only a change of its text may
    bring (gamma/vault/merge.py)."""
    taken = set(pairs.values())
    olds = Counter(node["content"] for i, node in enumerate(old) if i not in taken)
    news = Counter(node["content"] for n, node in enumerate(new) if n not in pairs)
    at = {node["content"]: i for i, node in enumerate(old) if i not in taken and olds[node["content"]] == 1}
    out = dict(pairs)
    for n, node in enumerate(new):
        if n not in pairs and node["content"].strip() and news[node["content"]] == 1 and node["content"] in at:
            out[n] = at[node["content"]]
    return out


def _label_ops(page: Page, labels) -> list:
    """``labels``, the ids a push gives the page (``_file_labels``;
    None: as they are), as a ``set`` of its root; none when the page has
    them."""
    root = page.blocks[page.id]
    if labels is None or (root["props"].get(LABELS) or None) == (labels or None):
        return []
    return [{"op": "set", "id": page.id, "props": {LABELS: labels or None}}]


def _by_page(projected: list, base: Base | None, pairs: dict) -> dict:
    """``match``'s pairs of a file against its base as places in the page's
    projection: ``{file index: projected index}`` of each block the base
    names that the page holds with a span."""
    at = {node["id"]: k for k, node in enumerate(md_lens.flatten(projected))}
    return {n: at[base.ids[i]] for n, i in pairs.items() if base and i < len(base.ids) and base.ids[i] in at}


def _forced(page: Page, projected: list, file_tree: list, by_page: dict, token: str, known: set, kind_of) -> Edit:
    """``force=push``'s plan: the page's blocks with a span (``projected``)
    become ``file_tree``'s (its blocks' places among them ``by_page``), a new
    block taking what the file says of it (a callout its quote and page),
    every text the page had there that the file replaced recorded,
    resolved: a highlight it deletes by its quote and page as well as its
    note (``_said``), which may be none."""
    at = {node["id"]: k for k, node in enumerate(md_lens.flatten(projected))}
    merged = merge.merge_page(page.blocks, page.id, projected, file_tree, by_page, kind_of=kind_of,
                                    merge=lambda _base, theirs, _stored: (theirs, True))
    merged = _named(merged, token, known | set(at))
    replaced = []
    for block_id in at:
        after, block = merged.target.get(block_id), page.blocks[block_id]
        if after is None or after["content"] != block["content"]:
            text = after["content"] if after else ""
            mine = block["content"] if after else _said(block, page.id)
            replaced.append({"block_id": block_id, "mine": mine, "theirs": text, "result": text, "base": block["content"]})
    return Edit(merged.ops, replaced, True, merged.ids, merged.level, merged.deleted)


def _said(block: dict, page_id: str) -> str:
    """A block a force deletes as its record keeps it, for the person to read
    what went: its text, and a highlight's quote with its PDF page above its
    note."""
    g = _g(block, page_id)
    if not isinstance(g, dict):
        return g
    head = f"p. {g['page']}\n" if g.get("page") else ""
    quote = "\n".join(f"> {line}" for line in (g["quote"] or "").split("\n"))
    return head + quote + (f"\n\n{g['note']}" if g["note"] else "")
