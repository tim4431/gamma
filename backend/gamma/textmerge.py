"""Three-way merge of one block's text (gamma/ops.py ``set`` with ``base``).

A client's content change is computed against the text it last saw (its
``base``). When someone else changed the block in between, replacing the
block with the client's text would silently drop that other change — so the
two changes are merged in ``base`` coordinates instead: each side's edit
``base → text`` is a list of hunks (a span of ``base`` replaced by new text;
a pure insertion replaces an empty span), and both lists are applied to
``base`` together. Edits to different spans both survive, and so do two
insertions at the same or neighbouring offsets (the one stored first comes
first): two people typing at one caret keep both people's keystrokes. Only
a hunk of the client's that replaces or deletes characters the stored
change also replaced or deleted is dropped; for that span the text already
stored stands.

This is a stateless merge at apply time over diff-match-patch's diffs, not
OT or a CRDT: SQLite stays the only source of truth and writers without a
``base`` replace the text as before.
"""

from diff_match_patch import diff_match_patch

_dmp = diff_match_patch()
_dmp.Diff_Timeout = 0.2  # seconds before the diff settles for a coarser answer


def _hunks(base: str, text: str) -> list[tuple[int, int, str]]:
    """The change ``base → text`` as ``(start, end, insert)`` hunks in
    ``base`` offsets, in order: ``base[start:end]`` becomes ``insert``."""
    out: list[tuple[int, int, str]] = []
    at = 0
    for kind, chunk in _dmp.diff_main(base, text, False):
        if kind == 0:
            at += len(chunk)
            continue
        start, end, insert = (at, at, chunk) if kind > 0 else (at, at + len(chunk), "")
        if out and out[-1][1] == start:  # a delete and an insert side by side: one hunk
            first, _end, before = out.pop()
            start, insert = first, before + insert
        out.append((start, end, insert))
        at = end
    return out


def merge(base: str, ours: str, theirs: str) -> tuple[str, bool]:
    """Merge the change ``base → ours`` into ``theirs`` (the text stored now:
    ``base`` changed by someone else). Returns ``(text, clean)``; ``clean``
    is False when a hunk of ours was dropped for changing characters theirs
    changed too."""
    if theirs == base:
        return ours, True
    if ours == base or ours == theirs:
        return theirs, True
    stored, mine = _hunks(base, theirs), _hunks(base, ours)
    kept, j = [], 0
    for start, end, insert in mine:
        # stored hunks ending before this one starts can't share a character
        # with it, nor with any later one
        while j < len(stored) and stored[j][1] <= start:
            j += 1
        clash = False
        for s, e, _ in stored[j:]:
            if s >= end:
                break
            if max(start, s) < min(end, e):
                clash = True
                break
        if not clash:
            kept.append((start, end, insert))
    # Both lists in base order. At one offset a pure insertion comes before
    # a replacement of the text there (it was typed in front of that text),
    # and of two insertions theirs comes first. Their deleted spans share no
    # character, so an insertion inside the other side's deleted span lands
    # right after that side's replacement text.
    hunks = sorted([(s, e > s, 0, e, i) for s, e, i in stored] + [(s, e > s, 1, e, i) for s, e, i in kept])
    out, at = [], 0
    for start, _replaces, _side, end, insert in hunks:
        if start > at:
            out.append(base[at:start])
            at = start
        out.append(insert)
        at = max(at, end)
    out.append(base[at:])
    return "".join(out), len(kept) == len(mine)


def _edits(base: str, text: str) -> tuple[dict, set]:
    """The change ``base → text`` as ``(inserted text before each base
    offset, the deleted base offsets)``."""
    inserted, deleted, at = {}, set(), 0
    for kind, chunk in _dmp.diff_main(base, text, False):
        if kind > 0:
            inserted[at] = inserted.get(at, "") + chunk
            continue
        if kind < 0:
            deleted.update(range(at, at + len(chunk)))
        at += len(chunk)
    return inserted, deleted


def contains(base: str, ours: str, theirs: str) -> bool:
    """Whether ``theirs`` already holds the change ``base → ours``: every
    span it deletes is deleted there too, and every insertion is there at
    the same place (inside a longer one when more was typed around it).
    ``merge`` would apply such a change a second time."""
    ours_in, ours_del = _edits(base, ours)
    theirs_in, theirs_del = _edits(base, theirs)
    return ours_del <= theirs_del and all(s in theirs_in.get(at, "") for at, s in ours_in.items())


def map_offset(src: str, dst: str, offset: int) -> int:
    """Where the caret at ``offset`` in ``src`` sits in ``dst``."""
    if src == dst:
        return offset
    offset = max(0, min(int(offset), len(src)))
    diffs = _dmp.diff_main(src, dst)
    return _dmp.diff_xIndex(diffs, offset)
