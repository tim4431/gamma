// The handwriting's save for the page's life (docs/dev/handwriting.md
// "Fences" and "Client"): one saver (inkSaver.js) over the real draft
// store, block API and keepalive ledger, flushed when the tab goes to the
// background, when the page goes away and when another page opens. App
// composes it after the page's collab session, so ink's pagehide and
// visibilitychange listeners register after collab's.
import { useEffect, useRef } from "react";
import { available as keepaliveRoom } from "../shared/lib/keepalive.js";
import { API, apiJson } from "../shared/lib/utils";
import { createInkSaver } from "./inkSaver.js";
import * as inkStore from "./inkStore";

// focusedBlockId: the open page; leaving it saves what is pending.
// blocksRef, setStatus, liveSaved: the saver's (inkSaver.js), taken once:
// the ref is read when a flush runs, and both callbacks are stable.
export function useInkSave({ focusedBlockId, blocksRef, setStatus, liveSaved }) {
  const saverRef = useRef(null);
  if (!saverRef.current) saverRef.current = createInkSaver({ blocksRef, setStatus, liveSaved, inkStore, apiJson, API, keepaliveRoom });
  const { flushInk, scheduleInk, pending } = saverRef.current;
  useEffect(() => {
    // Going to the background flushes at once, with requests that outlive
    // the page (a phone may freeze or end a hidden tab without a pagehide).
    // A tab closed while it shows fires pagehide, then visibilitychange in
    // the same task: pagehide's flush clears the timer, so it is the
    // close's only one. When Firefox stops the pagehide listener first, the
    // flush on going hidden sends what was waiting, the PUT after the
    // upload, and the draft journal keeps the drawing (inkStore). A tab
    // hidden before it closes flushed on going hidden, and pagehide sends
    // every draft still unsaved again, its PUT beside its upload: that
    // flush's requests may not be past their first step. A drawing that
    // flush is uploading with keepalive sends its PUT alone, beside that
    // upload (planFlush).
    const flush = () => { if (pending()) flushInk({ keepalive: document.visibilityState === "hidden" }); };
    const leave = () => { if (pending() || inkStore.dirtyDrafts().length) flushInk({ keepalive: true, leaving: true }); };
    window.addEventListener("pagehide", leave);
    document.addEventListener("visibilitychange", flush);
    return () => { window.removeEventListener("pagehide", leave); document.removeEventListener("visibilitychange", flush); };
  }, [flushInk]);
  useEffect(() => () => {
    // Leaving a page: pending strokes still save (through the block API,
    // which needs no open tree).
    if (pending()) flushInk();
  }, [focusedBlockId, flushInk]); // eslint-disable-line react-hooks/exhaustive-deps
  return { flushInk, scheduleInk };
}
