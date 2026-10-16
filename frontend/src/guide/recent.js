// The newest thing of a kind the user made in this page load — a highlight —
// so the element showing it can carry data-guide-recent and an anchor with
// `pick: "recent"` points at it, not at the page's first (anchors.js). Read
// from the guide's own events, so no caller has to report it.
import { useSyncExternalStore } from "react";
import { guideEvents } from "./events.js";

const latest = new Map(); // kind → id
const listeners = new Set();
const KINDS = { "highlight.created": "highlight" };

guideEvents.subscribe((name, payload) => {
  const kind = KINDS[name];
  if (!kind || !payload?.id) return;
  latest.set(kind, payload.id);
  listeners.forEach((fn) => fn());
});

const subscribe = (fn) => { listeners.add(fn); return () => listeners.delete(fn); };

// Whether `id` is the newest `kind`: a row re-renders only when its own
// answer changes.
export const useIsRecent = (kind, id) => useSyncExternalStore(subscribe, () => latest.get(kind) === id);
// The newest `kind`'s id when it is one of `items` (each with an id), else
// null: a PDF page re-renders only when the newest highlight is on it.
export const useRecentIn = (kind, items) => useSyncExternalStore(subscribe, () => {
  const id = latest.get(kind);
  return id && items?.some((item) => item.id === id) ? id : null;
});
