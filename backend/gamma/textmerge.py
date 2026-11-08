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

A pure insertion or deletion next to text it repeats can be read at more
than one offset: typing ``cool `` in front of ``cat`` in ``the cat sat`` is
also typing ``ool c`` after its ``c``. The readings give the same text on
their own but not merged: against a delete of ``cat `` the second one keeps
``ool c`` and deletes the ``c`` typed in front of it, ``the ool csat``. So
such an edit is read at a word boundary where it can be
(``diff_cleanupSemanticLossless``), and one that still cuts into the other
side's change is read where it cuts into none of it (``_align``).

This is a stateless merge at apply time over diff-match-patch's diffs, not
OT or a CRDT: SQLite stays the only source of truth and writers without a
``base`` replace the text as before.
"""

from diff_match_patch import diff_match_patch

_dmp = diff_match_patch()
_dmp.Diff_Timeout = 0.2  # seconds before the diff settles for a coarser answer


def _hunks(base: str, text: str, semantic: bool = False) -> list[tuple[int, int, str]]:
    """The change ``base → text`` as ``(start, end, insert)`` hunks in
    ``base`` offsets, in order: ``base[start:end]`` becomes ``insert``.
    ``semantic``: whole words and phrases, not the scattered characters two
    texts happen to share (``diff_cleanupSemantic``). Without it a pure
    insertion or deletion still sits on a word boundary where it can."""
    out: list[tuple[int, int, str]] = []
    at = 0
    diffs = _dmp.diff_main(base, text, False)
    if semantic:
        _dmp.diff_cleanupSemantic(diffs)
    else:
        _dmp.diff_cleanupSemanticLossless(diffs)
    for kind, chunk in diffs:
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


def _shifted(base: str, hunk: tuple[int, int, str], to: int) -> tuple[int, int, str] | None:
    """The pure insertion or deletion ``hunk`` read at offset ``to``, or None
    when the text it passes over is not what it repeats: ``cool `` typed at
    4 in ``the cat`` is ``ool c`` typed at 5, and ``cat `` deleted from
    ``cat cat`` is either word."""
    start, end, insert = hunk
    if end > start and insert:
        return None  # a replacement has the one reading
    while start > to:
        c = base[start - 1]
        if c != (insert[-1] if insert else base[end - 1]):
            return None
        start, end, insert = start - 1, end - 1, c + insert[:-1] if insert else ""
    while start < to:
        c = base[start]
        if c != (insert[0] if insert else base[end]):
            return None
        start, end, insert = start + 1, end + 1, insert[1:] + c if insert else ""
    return start, end, insert


def _cuts(hunk: tuple[int, int, str], others: list[tuple[int, int, str]]) -> bool:
    """Whether ``hunk`` is an insertion strictly inside a span one of
    ``others`` replaces, or replaces a span with one of their insertions
    strictly inside."""
    start, end, _ = hunk
    if start == end:
        return any(s < start < e for s, e, _ in others)
    return any(s == e and start < s < end for s, e, _ in others)


def _align(base: str, hunks: list[tuple[int, int, str]],
           others: list[tuple[int, int, str]]) -> list[tuple[int, int, str]]:
    """``hunks`` with each pure insertion or deletion that cuts into
    ``others`` (``_cuts``) read at the nearest offset where it neither cuts
    into nor overlaps any of them, when it has one (``_shifted``, not past
    its own neighbours). An insertion that no reading takes out of a span
    the other side deleted stays inside it: it was typed there."""
    out = list(hunks)
    for i, hunk in enumerate(out):
        start, end, _ = hunk
        if not _cuts(hunk, others):
            continue
        low = out[i - 1][1] if i else 0
        high = out[i + 1][0] if i + 1 < len(out) else len(base)
        if start == end:  # to either end of the span it is in
            spots = {to for s, e, _ in others if s < start < e for to in (s, e)}
        else:  # so that the insertion inside starts or ends it
            spots = {to for s, e, _ in others if s == e and start < s < end for to in (s, s - (end - start))}
        for to in sorted(spots, key=lambda to: (abs(to - start), to)):
            if low <= to and to + end - start <= high:
                moved = _shifted(base, hunk, to)
                if moved and not _cuts(moved, others) \
                        and not any(max(moved[0], s) < min(moved[1], e) for s, e, _ in others):
                    out[i] = moved
                    break
    return out


def merge(base: str, ours: str, theirs: str, semantic: bool = False) -> tuple[str, bool]:
    """Merge the change ``base → ours`` into ``theirs`` (the text stored now:
    ``base`` changed by someone else). Returns ``(text, clean)``; ``clean``
    is False when a hunk of ours was dropped for changing characters theirs
    changed too. Typing merges character by character. ``semantic`` merges
    word and phrase hunks instead: two rewrites of one sentence then clash
    as wholes, where characters would interleave them (a revert of the
    agent's edit, gamma/ai_revert.py)."""
    if theirs == base:
        return ours, True
    if ours == base or ours == theirs:
        return theirs, True
    stored, mine = _hunks(base, theirs, semantic), _hunks(base, ours, semantic)
    mine = _align(base, mine, stored)
    stored = _align(base, stored, mine)
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
    # right after that side's replacement text. Each side's hunks keep their
    # order (``_align`` can read an insertion at its neighbour's offset).
    hunks = sorted([(s, e > s, 0, n, e, i) for n, (s, e, i) in enumerate(stored)]
                   + [(s, e > s, 1, n, e, i) for n, (s, e, i) in enumerate(kept)])
    out, at = [], 0
    for start, _replaces, _side, _n, end, insert in hunks:
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
