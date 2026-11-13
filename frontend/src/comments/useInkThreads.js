// What a comment thread on an ink group does to the ink
// (docs/dev/handwriting.md "Comments on handwriting"): the lasso selecting
// one whole group on a PDF page that has open threads opens its thread, its
// margin card the active one, and outlines the group; a group with a
// thread, open or resolved, is closed: it takes no new strokes
// (ink/useInkStrokes.js), keeps its block when it is erased empty
// (ink/inkSaver.js) and no merge takes its strokes or adds to them
// (ink/selectionEdits.js mergePlan). App composes it after the lasso's
// selection and hands its predicates, which never change identity, to the
// ink owners it composed before it.
import { useCallback, useEffect, useRef } from "react";
import { wholeGroup } from "../ink/selectionEdits.js";

// threadsByBlock, countByBlock, loaded: the page's threads by block, how
// many of them are open, and whether they have come (useComments.js).
// inkSelection, inkOf: the lasso's selection (ink/useInkSelection.js) and a
// group's strokes now (ink/useInkDocument.js). openThread(id, anchor, opts),
// triggerFlash(id): App's. → {closedGroup(id), keeps(id), onComment(id)}:
// closedGroup is the predicate the group has a thread, read over the
// threads as they are when it is asked; keeps is the same, and true while
// the page's threads are not known (the page just opened, or the saver's
// own flush on leaving, which runs once the next page's empty threads
// show): nothing is deleted on a guess. onComment(id) opens a new thread's
// card on the group, its field focused.
export function useInkThreads({ threadsByBlock, countByBlock, loaded, inkSelection, inkOf, openThread, triggerFlash }) {
  const live = useRef(null);
  live.current = { threadsByBlock, countByBlock, loaded, inkOf, openThread, triggerFlash };
  // Once for each selection: the threads loading later, or a thread made
  // on the group while it stays selected, open nothing.
  useEffect(() => {
    if (typeof inkSelection?.page !== "number") return;
    const now = live.current, id = wholeGroup(inkSelection, now.inkOf);
    if (!id || !(now.countByBlock.get(id) > 0)) return;
    now.openThread(id, null, { pdf: true, toggle: false });
    now.triggerFlash(id);
  }, [inkSelection]);
  const closedGroup = useCallback((id) => (live.current.threadsByBlock.get(id) || []).length > 0, []);
  const keeps = useCallback((id) => !live.current.loaded || closedGroup(id), [closedGroup]);
  const onComment = useCallback((id) => live.current.openThread(id, null, { compose: true, pdf: true }), []);
  return { closedGroup, keeps, onComment };
}
