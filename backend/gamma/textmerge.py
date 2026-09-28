"""Three-way merge of one block's text (gamma/ops.py ``set`` with ``base``).

A client's content change is computed against the text it last saw (its
``base``). When someone else changed the block in between, replacing the
block with the client's text would silently drop that other change — so the
change is applied as a patch instead: the edit ``base → ours`` is located in
``theirs`` (the block's current text) and applied there. Edits to different
spans both survive; a hunk that no longer fits (the same span changed by
both) is dropped, and the text already stored stands for that span.

This is a stateless text patch at apply time (diff-match-patch, with its
fuzzy location matching), not OT or a CRDT: SQLite stays the only source of
truth and writers without a ``base`` replace the text as before.
"""

from diff_match_patch import diff_match_patch

_dmp = diff_match_patch()
_dmp.Diff_Timeout = 0.2      # seconds before the diff settles for a coarser answer
_dmp.Match_Threshold = 0.4   # how far a hunk's context may drift and still apply
_dmp.Patch_DeleteThreshold = 0.4


def merge(base: str, ours: str, theirs: str) -> tuple[str, bool]:
    """Apply the change ``base → ours`` onto ``theirs``. Returns ``(text,
    clean)``; ``clean`` is False when a hunk could not be placed."""
    if theirs == base:
        return ours, True
    if ours == base or ours == theirs:
        return theirs, True
    patches = _dmp.patch_make(base, ours)
    text, results = _dmp.patch_apply(patches, theirs)
    return text, all(results)


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
