"""The three-way merge of a push: a note changed on disk brought into its page
(docs/research/vaults.md "Notes both ways"). Pure: no database, no disk.

``merge_page`` takes the page as it is now (a ``sync_tree`` snapshot), the
base (the tree the file read as when the vault last took it, with its blocks'
ids and their text in Gamma), the file as it reads now, and ``md_lens.match``'s
pairs between the two. The target starts from the page, never from the file,
and takes only what the file changed since the base:

- a block whose text the file changed takes the change, merged into what
  Gamma typed since (``file_wins``); where both changed the same words the
  file's text stands, and Gamma's is a conflict to record, and a change the
  text holds already (the merge read again) is made once;
- a block the file moved (another parent, or out of the order it had among
  the siblings it still has: the longest run that kept its order stays) goes
  where the file has it, after the nearest block before it in the file that
  stands under the same parent, else first;
- a block new in the file is inserted the same way;
- a block the file dropped is deleted, unless it has no span now (an emptied
  note). One Gamma changed since the base is a clash the file wins: Gamma's
  text is a conflict to record, and a new block of the file that reads like
  its base text is that block with its class changed (an item made a
  paragraph, a heading moved down as one), its id kept and the two changes
  merged; so is a line the lens paired by its text with a block the file
  dropped earlier (``gone``) that the page no longer holds, rather than that
  one coming back beside it (a marker dropped, then typed again). What stands under a deleted block and is not deleted with it is
  lifted to the nearest block above that stays, where the deleted one stood,
  before the delete (a delete takes a subtree);
- a block the base does not name stays, wherever it is: Gamma's own blocks,
  and every block with no span. A block deleted in Gamma comes back only when
  the file changed its text, never because the file still has it; one Gamma
  moved to another page stays there, and the file's change of it goes with it
  (``Merged.away``), its class changed too (a new block of the file that
  reads like its base text, as above, or one the lens paired by its place
  alone with a block it reads little like: a line moved past blocks the file
  deleted takes the id of one of them).

Where Gamma's places and the file's would put blocks inside each other, the
file's win. A block keeps its sibling key unless its order among the siblings
it stays with changed (the longest run of keys still rising keeps theirs); the
rest get keys between their neighbours', clear of every key the parent holds,
so the ops land as sent and an insert at the top of a long page is one insert,
not a move per block. The ops are ``sync_tree.diff(page, target, page_id,
with_base=False)``: no ``set`` carries a ``base``, the merge being done.

Trees are ``md_lens``'s, ``{id, content, children}``, in Gamma space: the
caller turns the file's text into Gamma's (``links``) and gives the base
its blocks' text in Gamma. A file block may carry ``props``, for when it is new
(a quote callout read as a highlight).
"""

import bisect
import difflib
import re
from dataclasses import dataclass

from fractional_indexing import FIError, generate_key_between, generate_n_keys_between, validate_order_key

from .. import md_lens, sync_tree
from ..blocks_store import new_block_id

ALIKE = 0.6     # how alike a new block of the file must read to a dropped one Gamma changed to be it

_TOKENS_RE = re.compile(r"\w+|\s+|[^\w\s]")
_WORD_RE = re.compile(r"\w")


@dataclass(slots=True)
class Merged:
    """What a push writes: ``target``, the page as it should be, and ``ops``
    to get there; ``ids``, each file block's id in document order (the new
    base's: a block deleted in Gamma that the file only kept keeps its id
    there, and pull takes it out of the file); ``conflicts``, ``{block_id,
    mine, theirs, result, base}`` in Gamma space, each a ``file_won`` row;
    ``deleted``, every block the file dropped, for the base's ``gone``;
    ``level``, whether the page then says what the file says: when it does
    not, pull has something to write; and ``away``, ``{block_id, base,
    file}`` of each block Gamma moved to another page whose text the file
    changed, for that page to take."""
    target: dict
    ops: list
    ids: list
    conflicts: list
    deleted: list
    level: bool
    away: list = ()


def file_wins(base: str, file_text: str, stored: str) -> tuple[str, bool]:
    """The file's change ``base → file_text`` merged into the text Gamma holds:
    ``(text, clean)``, word by word. Two changes clash when they touch one
    word, when one side's insertion falls inside words the other replaced, or
    when both insert at one place and the two would join into one word (a
    note typed on both sides into an empty highlight: ``great point`` and
    ``check later``); a side's changes with only spaces and punctuation
    between are one (a sentence rewritten is one change). By characters,
    ``10:16`` changed to ``10:56`` and to ``10:31`` reads clean as ``10:35``,
    and a word inserted into a sentence the file rewrote lands among the
    file's words: texts neither side typed. A change one side holds already
    is made once (``_within``): the text this merge gave, merged again from
    the same base (a round that died after it, or planned it again), is
    left as it is, and a file typed on past what Gamma took of it adds only
    what is new."""
    if stored in (base, file_text):
        return file_text, True
    if file_text == base:
        return stored, True
    words = _TOKENS_RE.findall(base)
    mine, theirs = _hunks(words, _TOKENS_RE.findall(file_text)), _hunks(words, _TOKENS_RE.findall(stored))
    mine = [h for h in mine if not any(_within(h, t) for t in theirs)]
    if not mine:
        return stored, True
    theirs = [t for t in theirs if not any(_within(t, h) for h in mine)]
    if _clashes(mine, theirs):
        return file_text, False
    # Both in base order; at one place an insertion before a replacement there,
    # and of two insertions Gamma's first.
    out, at = [], 0
    for start, _replaces, _side, end, insert in sorted([(s, e > s, 0, e, i) for s, e, i in theirs] +
                                                       [(s, e > s, 1, e, i) for s, e, i in mine]):
        out += words[at:start] if start > at else []
        out.append(insert)
        at = max(at, end)
    return "".join(out + words[at:]), True


def _hunks(base: list, text: list) -> list:
    """The change ``base → text`` (lists of words, spaces and punctuation) as
    ``(start, end, insert)`` in ``base``'s places, in order: ``base[start:end]``
    becomes ``insert``. Two changes with no word between are one."""
    out = []
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, base, text, autojunk=False).get_opcodes():
        if tag == "equal":
            continue
        insert = "".join(text[j1:j2])
        if out and not any(_WORD_RE.match(w) for w in base[out[-1][1]:i1]):
            start, end, before = out.pop()
            i1, insert = start, before + "".join(base[end:i1]) + insert
        out.append((i1, i2, insert))
    return out


def _within(hunk: tuple, other: tuple) -> bool:
    """Whether the change ``hunk`` is part of ``other`` (two ``_hunks``, of
    the two sides): the same change, or words of it that ``other`` holds at
    one end of its text, ``other`` replacing the base from that end as far
    as ``hunk`` does or further. So a merge read again finds the file's
    change beside Gamma's, which ``_hunks`` joined into one."""
    if hunk == other:
        return True
    (start, end, insert), (start2, end2, insert2) = hunk, other
    if not _WORD_RE.search(insert):
        return False
    part, whole = _TOKENS_RE.findall(insert), _TOKENS_RE.findall(insert2)
    return len(part) <= len(whole) and (start2 == start and end <= end2 and whole[:len(part)] == part
                                        or end2 == end and start2 <= start and whole[len(whole) - len(part):] == part)


def _clashes(mine: list, theirs: list) -> bool:
    """Whether a hunk of each side touches the same words: their spans share
    one, one's insertion falls strictly inside the other's span, or both
    insert at one place with no space or punctuation where they would meet
    (Gamma's first, ``file_wins``). Both lists are in order."""
    j = 0
    for s, e, insert in mine:
        while j < len(theirs) and theirs[j][1] < s:
            j += 1          # it ends before this one starts, and before every later one
        for s2, e2, insert2 in theirs[j:]:
            if s2 > e or s2 == e and e > s:
                break
            if max(s, s2) < min(e, e2) or s < s2 < e or s2 < s < e2:
                return True
            if s == e == s2 == e2 and _WORD_RE.match(insert2[-1:]) and _WORD_RE.match(insert[:1]):
                return True
    return False


def premerge(page: dict, base: list, file: list, pairs: dict):
    """``merge_page``'s ``merge``, with the merges done now, before the write
    lock (``ops._premerge``'s reason): each block both sides changed, merged
    once. Under the lock the page may have moved on; a text not met here is
    merged then."""
    olds, done = md_lens.flatten(base), {}
    for n, node in enumerate(md_lens.flatten(file)):
        i = pairs.get(n)
        if i is None or i >= len(olds) or olds[i]["id"] not in page:
            continue
        texts = (olds[i]["content"], node["content"], page[olds[i]["id"]]["content"])
        if texts[1] != texts[0] and texts[2] not in texts[:2]:
            done[texts] = file_wins(*texts)
    return lambda *texts: done.get(texts) or file_wins(*texts)


def merge_page(page: dict, page_id: str, base: list, file: list, pairs: dict, *, kind_of, merge=file_wins,
               gone=(), held=frozenset(), away=frozenset()) -> Merged:
    """The page with the file's changes since ``base`` (the module doc).
    ``pairs`` is ``md_lens.match``'s over ``md_lens.flatten`` of the two trees,
    an index past the base's being ``gone``'s: a block the file dropped
    earlier, back with its id and properties. ``kind_of(block)`` tells a
    snapshot block's kind (``blocks_store.block_kind``); ``held``, the ids
    another page holds now, which come back under fresh ones, but for those
    of ``away``, which a page that is not in Recently deleted holds: Gamma
    moved them there. ``merge`` is ``file_wins`` or ``premerge``'s."""
    olds, news = _flat(base), _flat(file)
    base_parent = {node["id"]: olds[up][0]["id"] if up is not None else page_id for node, up in olds}
    base_text = {node["id"]: node["content"] for node, _up in olds}
    base_at = {node["id"]: i for i, (node, _up) in enumerate(olds)}
    spanned = {node["id"] for node in md_lens.flatten(md_lens.project(page, kind_of))}
    target = {block_id: {**block, "props": dict(block["props"])} for block_id, block in page.items()}

    claims, claimed = [], set()
    for n, (node, _up) in enumerate(news):
        claims.append(_claim(pairs.get(n), node, olds, gone, claimed))
        claimed.add(claims[-1][0])
    edited = [b for b, text in base_text.items() if b not in claimed and b in page and b in spanned
              and page[b]["content"] != text]
    moved_away = [b for b in base_text if b not in claimed and b in away]
    # A line given by its text the id of a block the file dropped earlier may be one of these, its class changed
    # back (a marker dropped, then typed again): a block the page holds or Gamma moved, before one it deleted.
    fresh = [n for n in range(len(news)) if pairs.get(n) is None
             or pairs[n] >= len(olds) and not news[n][0].get("anchor") and claims[n][0] not in page]
    # One Gamma moved may be a line the lens paired by its place alone, reading little like its pair: the leftovers
    # of one class pair in order, and a line moved past blocks the file deleted took one's id.
    placed = [n for n, (node, _up) in enumerate(news) if moved_away and pairs.get(n, len(olds)) < len(olds)
              and claims[n][2] and not node.get("anchor") and _alike(base_text[claims[n][0]], node["content"]) < ALIKE]
    _class_changed(edited + moved_away, news, claims, base_text, fresh, placed, away)
    claimed = {claim[0] for claim in claims}

    ids, fates, conflicts, moved = [], [], [], []
    for n, (node, _up) in enumerate(news):
        block_id, props, back = claims[n]
        if block_id in page:
            stored = page[block_id]["content"]
            was = base_text.get(block_id, stored)
            if node["content"] != was:
                text, clean = merge(was, node["content"], stored)
                if not clean:
                    text = node["content"]
                    conflicts.append({"block_id": block_id, "mine": stored, "theirs": text, "result": text, "base": was})
                target[block_id]["content"] = text
            fates.append("kept")
        elif back and node["content"] == base_text.get(block_id):
            fates.append("absent")
        elif back and block_id in away and block_id in base_text:
            moved.append({"block_id": block_id, "base": base_text[block_id], "file": node["content"]})
            fates.append("absent")
        else:
            if block_id in held:
                block_id = new_block_id()
            target[block_id] = {"parent": None, "position": "", "content": node["content"], "props": dict(props)}
            fates.append("new")
        ids.append(block_id)

    deleted = [block_id for block_id in base_text if block_id not in claimed and block_id in page and block_id in spanned]
    for block_id in deleted:
        del target[block_id]
        if page[block_id]["content"] != base_text[block_id]:     # the file dropped what Gamma changed: it wins
            conflicts.append({"block_id": block_id, "mine": page[block_id]["content"], "theirs": "", "result": "",
                              "base": base_text[block_id]})

    parents, above = _parents(page, page_id, news, ids, fates, base_parent, base_at, target)
    _order(page, page_id, target, parents, ids, above, spanned | set(above))
    ops = sync_tree.diff(page, target, page_id, with_base=False)
    return Merged(target, ops, ids, conflicts, deleted, _level(md_lens.project(target, kind_of), news, ids), moved)


def _flat(tree: list) -> list:
    """``(node, the index of the node above or None)`` in document order
    (``md_lens.flatten``'s, which ``match``'s indexes count)."""
    out, pending = [], [(node, None) for node in reversed(tree)]
    while pending:
        node, up = pending.pop()
        out.append((node, up))
        pending.extend((child, len(out) - 1) for child in reversed(node["children"]))
    return out


def _claim(i, node: dict, olds: list, gone, claimed: set) -> tuple:
    """``(id, props, back)`` of the file block ``node`` matched to ``i``: its
    base block's id, or a dropped one's from ``gone`` with its properties, or a
    fresh one with the block's own ``props``. ``back``: an id the page may no
    longer hold. Two file blocks never take one id."""
    props = node.get("props") or {}
    if i is None:
        return new_block_id(), props, False
    if i < len(olds):
        block_id = olds[i][0]["id"]
    else:
        entry = gone[i - len(olds)]
        block_id, props = entry["id"], entry.get("props") or props
    if block_id in claimed:
        return new_block_id(), node.get("props") or {}, False
    return block_id, props, True


def _alike(a: str, b: str) -> float:
    """How alike two texts read (``SequenceMatcher.ratio``), 0 for two that
    cannot reach ``ALIKE``."""
    match = difflib.SequenceMatcher(None, a, b, autojunk=False)
    return match.ratio() if match.quick_ratio() >= ALIKE else 0.0


def _class_changed(dropped: list, news: list, claims: list, base_text: dict, fresh: list, placed: list,
                   away) -> None:
    """The blocks of ``dropped`` (dropped by the file, and changed in Gamma or
    moved by it to another page, ``away``) that a block of the file is, its
    class changed so that the lens could not pair them: the place of
    ``fresh`` (the file's new blocks), or for one of ``away`` of ``placed``
    too (blocks the lens paired by their place alone), whose block reads most
    like one's base text, at least ``ALIKE``, takes its id in ``claims``."""
    taken = set()
    for block_id in dropped:
        scored = []
        for n in [*fresh, *(placed if block_id in away else ())]:
            alike = 0.0 if n in taken else _alike(base_text[block_id], news[n][0]["content"])
            if alike >= ALIKE:
                scored.append((alike, n))
        if scored:
            n = max(scored)[1]
            claims[n] = (block_id, {}, True)
            taken.add(n)


def _rising(values: list) -> set:
    """The places in ``values`` of a longest run strictly rising: the ones
    that kept their order (the lens's own run, ``md_lens._rising``)."""
    return {k for k, _value in md_lens._rising(list(enumerate(values)))}


def _parents(page, page_id, news, ids, fates, base_parent, base_at, target) -> tuple[dict, dict]:
    """``{block: (parent, file index or None)}`` for every block of the target:
    where the file put it for a block it moved or made (its index then places
    it among its siblings), else where the page has it, lifted past the blocks
    above that go. Where the two would make a loop, the file's places win.
    With it ``{file block: the nearest block above it in the file that is in
    the target}``, the page for none: the parent a block the file placed
    takes."""
    file_parent, placed, groups = {}, set(), {}
    for n, (_node, up) in enumerate(news):
        file_parent[ids[n]] = ids[up] if up is not None else page_id
        if fates[n] == "new":
            placed.add(ids[n])
        elif fates[n] == "kept":
            if base_parent.get(ids[n]) != file_parent[ids[n]]:
                placed.add(ids[n])
            else:
                groups.setdefault(up, []).append(n)
    for members in groups.values():   # siblings in both: the ones out of the base's order moved
        keep = _rising([base_at[ids[n]] for n in members])
        placed.update(ids[n] for k, n in enumerate(members) if k not in keep)

    index = {ids[n]: n for n in range(len(news)) if fates[n] != "absent"}
    above = {}
    for block_id in index:
        up = file_parent[block_id]
        while up != page_id and up not in target:
            up = file_parent[up]
        above[block_id] = up
    top = set()
    while True:
        parents = {}
        for block_id in target:
            if block_id == page_id:
                continue
            if block_id in top:
                parents[block_id] = (page_id, None)
            elif block_id in placed:
                parents[block_id] = (above[block_id], index[block_id])
            else:
                up = page[block_id]["parent"]
                while up != page_id and up not in target:
                    up = page[up]["parent"]
                parents[block_id] = (up, None)
        loop = _loop(parents, page_id)
        if not loop:
            return parents, above
        stay = [block_id for block_id in loop if block_id in index and block_id not in placed]
        if stay:
            placed.update(stay)
        else:   # never met: a loop always holds a block of the file that kept Gamma's place
            top.add(loop[0])


def _loop(parents: dict, page_id: str) -> list:
    """The blocks of a loop in ``parents``, if there is one."""
    done = {page_id}
    for start in parents:
        chain, seen, at = [], set(), start
        while at not in done:
            if at in seen:
                return chain[chain.index(at):]
            seen.add(at)
            chain.append(at)
            at = parents[at][0]
        done |= seen
    return []


def _order(page: dict, page_id: str, target: dict, parents: dict, ids: list, above: dict, spanned: set) -> None:
    """Each block's parent and key in ``target``. A block that stays keeps its
    place in the page's order (a lifted one where the block it was lifted out
    of stood). A block the file placed goes among the blocks its parent holds
    as the file reads them (``md_lens.project``: past the blocks with no span,
    ``spanned`` being the rest), right after the nearest one before it in the
    file, beside that one, so inside an ink drawing a note stands under when
    that one does; with none, first. A block of Gamma's own there stands where
    the last block of the file it holds does."""
    kids = sync_tree.children_of(page)
    walk = {block_id: k for k, block_id in enumerate(_page_order(kids, page_id))}
    actual = {block_id: parent for block_id, (parent, n) in parents.items() if n is None}
    file_at = {block_id: n for n, block_id in enumerate(ids) if block_id in above}

    def projected(block_id):
        up = actual[block_id]
        while up != page_id and up not in spanned:
            up = actual[up]
        return up

    holds = {}
    for block_id in actual.keys() & file_at.keys():
        up = actual[block_id]
        while up in actual:
            if up in spanned and up not in file_at:
                holds[up] = max(holds.get(up, -1), file_at[block_id])
            up = actual[up]
    line = {}
    for block_id in actual.keys() & spanned:
        at = file_at.get(block_id, holds.get(block_id))
        if at is not None:
            line.setdefault(projected(block_id), []).append((at, block_id))
    for blocks in line.values():
        blocks.sort()
    first, after = {}, {}
    for n, block_id in sorted((n, b) for b, (_parent, n) in parents.items() if n is not None):
        blocks = line.setdefault(above[block_id], [])
        k = bisect.bisect_left(blocks, (n, ""))
        if k:
            sibling = blocks[k - 1][1]
            actual[block_id] = actual[sibling]
            after.setdefault(sibling, []).append(block_id)
        else:
            actual[block_id] = above[block_id]
            first.setdefault(above[block_id], []).append(block_id)
        blocks.insert(k, (n, block_id))
    staying = {}
    for block_id, (_parent, n) in parents.items():
        if n is None:
            staying.setdefault(actual[block_id], []).append(block_id)
    for parent in {*staying, *first}:
        order = _following(after, first.get(parent, ()))
        for block_id in sorted(staying.get(parent, ()), key=walk.__getitem__):
            order += _following(after, [block_id])
        for block_id, key in zip(order, _keys(page, kids.get(parent, ()), parent, order)):
            target[block_id]["parent"], target[block_id]["position"] = parent, key


def _page_order(kids: dict, page_id: str) -> list:
    """The page's blocks in document order."""
    out, pending = [], list(reversed(kids.get(page_id, [])))
    while pending:
        block_id = pending.pop()
        out.append(block_id)
        pending.extend(reversed(kids.get(block_id, [])))
    return out


def _following(after: dict, blocks) -> list:
    """``blocks``, each followed by what ``after`` puts after it, and so on."""
    out, pending = [], list(reversed(blocks))
    while pending:
        nxt = pending.pop()
        out.append(nxt)
        pending.extend(reversed(after.get(nxt, ())))
    return out


def _keys(page: dict, siblings, parent: str, order: list) -> list:
    """Keys for ``order``, the children of ``parent`` in the target: a block
    that was a child of it keeps its key while those keys rise in this order
    (the longest such run), the rest get keys between their neighbours', clear
    of every key a child of ``parent`` holds in the page (``siblings``)."""
    own = [k for k, b in enumerate(order) if b in page and page[b]["parent"] == parent and _valid(page[b]["position"])]
    keep = {own[k] for k in _rising([page[order[k]]["position"] for k in own])}
    taken = {page[b]["position"] for b in siblings}
    keys, k = [None] * len(order), 0
    while k < len(order):
        if k in keep:
            keys[k] = page[order[k]]["position"]
            k += 1
            continue
        end = k
        while end < len(order) and end not in keep:
            end += 1
        low = keys[k - 1] if k else None
        run = generate_n_keys_between(low, page[order[end]]["position"] if end < len(order) else None, end - k)
        for j, key in enumerate(run):
            while key in taken:
                key = generate_key_between(run[j - 1] if j else low, key)
            run[j] = key
        taken.update(run)
        keys[k:end] = run
        k = end
    return keys


def _valid(key) -> bool:
    try:
        validate_order_key(key)
    except (FIError, TypeError):
        return False
    return True


def _level(projected: list, news: list, ids: list) -> bool:
    """Whether the projected target says what the file says: the same blocks
    under the same parents, in the same order, with the same text."""
    of = {id(node): block_id for (node, _up), block_id in zip(news, ids)}
    pending = [(projected, [node for node, up in news if up is None])]
    while pending:
        mine, theirs = pending.pop()
        if len(mine) != len(theirs):
            return False
        for a, b in zip(mine, theirs):
            if a["id"] != of[id(b)] or a["content"] != b["content"]:
                return False
            pending.append((a["children"], b["children"]))
    return True
