// The owner of the PDF's wait (highlightWait.js): what its next press or
// selection is for while a note or a highlight waits for one. `docKey`
// changes with the page shown or its file, which ends a move or an
// extension (waitAfterDocument). Returns {wait, current(kind), startAttach,
// startMove, startExtend, startReviewMove, end, takeReviewMove}: `wait` the
// state, `current(kind)` the waiting block's id as of now (for the
// listeners that read it after the render), the commands stable.
//
// A move started from a highlight's review card (its Move…) is remembered,
// {id, prior} (prior: the card's step in effect, when it had one), for the
// move to open the card again on the highlight as it was, its Move…
// pressed ("Moved"); takeReviewMove() hands it over once. Any other start
// forgets it.
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { startWait, waitAfterDocument, waitingFor } from "./highlightWait.js";

export function useHighlightWait(docKey) {
  const [wait, setWait] = useState(null);
  const waitRef = useRef(wait);
  waitRef.current = wait;
  const fromReviewRef = useRef(null);
  const start = useCallback((kind, id) => {
    fromReviewRef.current = null;
    setWait(startWait(kind, id));
  }, []);
  const commands = useMemo(() => ({
    current: (kind) => waitingFor(waitRef.current, kind),
    startAttach: (id) => start("attach", id),
    startMove: (id) => start("move", id),
    startExtend: (id) => start("extend", id),
    startReviewMove: (id, prior = null) => {
      start("move", id);
      fromReviewRef.current = { id, prior };
    },
    end: () => setWait(null),
    takeReviewMove: () => {
      const from = fromReviewRef.current;
      fromReviewRef.current = null;
      return from;
    },
  }), [start]);
  const waiting = !!wait;
  useEffect(() => {
    if (!waiting) return undefined;
    const onKey = (e) => { if (e.key === "Escape") setWait(null); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [waiting]);
  useEffect(() => { setWait(waitAfterDocument); }, [docKey]);
  return { wait, ...commands };
}
