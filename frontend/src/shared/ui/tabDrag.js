// The tab being dragged, for every strip and group frame to read
// (TabGroups.jsx, the strip in shared/ui/Widgets.jsx): the browser's drag
// data cannot be read while a drag passes over, only on the drop, and the
// drop zones must know what is coming before that. {id, from: the group it
// is in} while a drag is on, else null. A drag ends on its source's dragend
// as a rule; the window's drop and dragend end it too, since a source the
// drop unmounts (its tab moved to another strip) sends no dragend of its own.
import { useSyncExternalStore } from "react";

let current = null;
const subscribers = new Set();
const emit = () => { for (const fn of subscribers) fn(); };
const stop = () => { if (current) { current = null; emit(); } };
let listening = false;
function listen() {
  if (listening || typeof window === "undefined") return;
  listening = true;
  // after React's own handlers, which read the drag as they take the drop
  window.addEventListener("dragend", stop);
  window.addEventListener("drop", stop);
}

export const tabDrag = {
  begin(id, from) { listen(); current = { id, from }; emit(); },
  end: stop,
  get: () => current,
  subscribe(fn) { subscribers.add(fn); return () => subscribers.delete(fn); },
};
export const useTabDrag = () => useSyncExternalStore(tabDrag.subscribe, tabDrag.get, () => null);
