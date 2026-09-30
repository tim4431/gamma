// The replay of a drawing's writing (ink.js inkTimeline), under the id of
// what it shows: a handwriting group's, drawn as it stood on its page
// (ink/InkLayer.jsx InkLayer) and in its card in the notes (InkCard), or a
// sheet's whole handwriting (notebook/NoteSheet.jsx). One at a time:
// playing one ends the one before. Module state with listeners, like
// inkStore; React reads it through InkLayer.jsx useReplayOf.
import { inkAtTime, inkTimeline } from "./ink.js";

let current = null; // {id, frame}: what is replaying and the drawing as it stood
let raf = 0;
const listeners = new Set();
const emit = () => { for (const fn of listeners) fn(); };

export function subscribe(fn) { listeners.add(fn); return () => listeners.delete(fn); }
export function now() { return current; }

export function play(id, ink) {
  stop();
  if (!id || !ink?.strokes?.length) return;
  const timeline = inkTimeline(ink);
  const begin = performance.now();
  const tick = (at) => {
    const t = at - begin;
    if (t >= timeline.duration) { stop(); return; }
    current = { id, frame: inkAtTime(ink, timeline, t) };
    emit();
    raf = requestAnimationFrame(tick);
  };
  current = { id, frame: inkAtTime(ink, timeline, 0) };
  emit();
  raf = requestAnimationFrame(tick);
}

// End the replay — only group `id`'s, when given (its drawing changed).
export function stop(id) {
  if (id && current?.id !== id) return;
  cancelAnimationFrame(raf);
  raf = 0;
  if (current) { current = null; emit(); }
}
