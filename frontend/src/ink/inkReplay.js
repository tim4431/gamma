import { decodeStroke, encodeStroke } from "./ink.js";

const indexes = new WeakMap();
function eventIndex(events) {
  if (indexes.has(events)) return indexes.get(events);
  const blocks = new Map();
  for (const event of events) {
    if (event.kind !== "stroke") continue;
    if (!blocks.has(event.block_id)) blocks.set(event.block_id, new Map());
    blocks.get(event.block_id).set(event.stroke_id, event);
  }
  indexes.set(events, blocks);
  return blocks;
}

// Playback projects the CURRENT vectors. Erased ink stays erased; moved ink
// stays moved. Untimed/unrecorded strokes remain visible as background notes.
export function replayInk(ink, blockId, replay) {
  if (!replay) return ink;
  const events = eventIndex(replay.events).get(blockId);
  if (!events) return ink;
  const activeIndex = replay.segmentIds.indexOf(replay.segmentId);
  const strokes = [];
  for (const stroke of ink.strokes) {
    const event = events.get(stroke.id) || events.get(stroke.source_id);
    if (!event) { strokes.push(stroke); continue; }
    const eventIndex = replay.segmentIds.indexOf(event.segment_id);
    if (eventIndex < 0 || activeIndex < 0) { strokes.push(stroke); continue; }
    if (eventIndex < activeIndex) { strokes.push(stroke); continue; }
    if (eventIndex > activeIndex || replay.ms < event.start_ms) continue;
    if (replay.ms >= event.end_ms || !stroke.ch.includes("t")) { strokes.push(stroke); continue; }
    const elapsed = replay.ms - event.start_ms;
    // Fragments keep their original sample offsets, including the first one.
    const samples = decodeStroke(stroke).filter((sample) => sample.t <= elapsed);
    if (samples.length) strokes.push(encodeStroke({ ...stroke, samples }));
  }
  return { ...ink, strokes };
}

let current = null;
const listeners = new Set();
export const replayStore = {
  get: () => current,
  subscribe: (fn) => { listeners.add(fn); return () => listeners.delete(fn); },
  set: (value) => { current = value; for (const fn of listeners) fn(); },
  clear: (id) => { if (current?.audioId === id) replayStore.set(null); },
};
