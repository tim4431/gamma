// Undo with fingers (docs/dev/handwriting.md "Undo with fingers" and
// "Client"): two fingers tapped together undo, three redo. Pure: InkLayer
// feeds one detector the document's pointer events and acts on `onChord`.
// A chord is N = 2 or 3 touch contacts that land within CHORD_START_MS of
// the first, none moving more than CHORD_SLOP css px, all lifted by a
// pointerup within CHORD_LIFT_MS of the first down. A pointercancel is never
// a lift (a pinch cancels both touches, so it never undoes), a fourth or a
// late contact voids the chord, and touches while a pen is down are a palm.
// 'double' needs two chords of the same N within DOUBLE_MS and DOUBLE_SLOP
// of each other, shared/ui/press.js's values for a double tap; a chord that
// fails between two good ones breaks the pair.
// A contact whose lift never arrives (iPad Safari drops one now and then,
// and a finger a native gesture swallows gets no pointerup) is forgotten
// once it is CONTACT_STALE_MS old, so it never pins every later chord.
import { DOUBLE_MS, DOUBLE_SLOP } from "../shared/ui/press.js";

export { DOUBLE_MS, DOUBLE_SLOP };
export const CHORD_START_MS = 80;
export const CHORD_SLOP = 10;
export const CHORD_LIFT_MS = 250;
export const CONTACT_STALE_MS = 1000;
export const CHORD_MODES = ["off", "tap", "double"];

// onChord(kind): 'undo' for two fingers, 'redo' for three; mode 'off' |
// 'tap' | 'double'; now() the clock for an event without a timeStamp
// (tests). e: any object with pointerId, pointerType, clientX, clientY and
// timeStamp. → {down, up, cancel, move, palm, setMode, reset}.
export function makeChordDetector({ onChord, mode = "off", now = () => Date.now() } = {}) {
  const contacts = new Map();   // pointerId → {x, y, t, chord}
  const pens = new Set();       // pen pointerIds down
  let chord = null;             // {start, members: [ids], live: count still down, voided}
  let previous = null;          // in 'double' mode, the chord before: {n, t, x, y}
  const at = (e) => (e && e.timeStamp != null ? e.timeStamp : now());

  const endChord = () => { chord = null; };
  const voidChord = () => { if (chord) chord.voided = true; };
  // Contacts older than CONTACT_STALE_MS are lifts that never came: they
  // leave their chord, and a chord nobody is holding any more ends.
  const forgetStale = (time) => {
    for (const [id, c] of contacts) {
      if (time - c.t <= CONTACT_STALE_MS) continue;
      contacts.delete(id);
      c.chord.live--;
      if (c.chord === chord && chord.live <= 0) endChord();
    }
  };

  function complete(time) {
    const n = chord.members.length;
    const ok = !chord.voided && (n === 2 || n === 3) && time - chord.start <= CHORD_LIFT_MS;
    const done = chord;
    endChord();
    if (!ok) { previous = null; return; }
    let x = 0, y = 0;
    for (const m of done.members) { x += m.x; y += m.y; }
    x /= n; y /= n;
    const kind = n === 2 ? "undo" : "redo";
    if (mode === "tap") onChord?.(kind);
    else if (mode === "double") {
      if (previous && previous.n === n && time - previous.t <= DOUBLE_MS && Math.hypot(x - previous.x, y - previous.y) <= DOUBLE_SLOP) {
        previous = null;
        onChord?.(kind);
      } else previous = { n, t: time, x, y };
    }
  }

  return {
    down(e) {
      const time = at(e);
      if (e.pointerType === "pen") { pens.add(e.pointerId); voidChord(); return; }
      if (e.pointerType !== "touch") return;
      forgetStale(time);
      const contact = { x: e.clientX, y: e.clientY, t: time, chord: null };
      contacts.set(e.pointerId, contact);
      if (!chord) chord = { start: time, members: [], live: 0, voided: false };
      else if (time - chord.start > CHORD_START_MS || chord.members.length >= 3) chord.voided = true;
      chord.members.push(contact);
      chord.live++;
      contact.chord = chord;
      if (pens.size) chord.voided = true;
    },
    move(e) {
      const c = contacts.get(e.pointerId);
      if (!c) return;
      if (Math.hypot(e.clientX - c.x, e.clientY - c.y) > CHORD_SLOP) c.chord.voided = true;
    },
    up(e) {
      if (e.pointerType === "pen") { pens.delete(e.pointerId); return; }
      const c = contacts.get(e.pointerId);
      if (!c) return;
      contacts.delete(e.pointerId);
      const owner = c.chord;
      owner.live--;
      if (owner === chord && owner.live === 0) complete(at(e));
    },
    cancel(e) {
      if (e.pointerType === "pen") { pens.delete(e.pointerId); return; }
      const c = contacts.get(e.pointerId);
      if (!c) return;
      contacts.delete(e.pointerId);
      const owner = c.chord;
      owner.voided = true;
      owner.live--;
      if (owner === chord && owner.live === 0) { endChord(); previous = null; }
    },
    // The touches down are a palm (a pen the detector never saw is writing,
    // as under a native host that owns the Pencil): the chord is void.
    palm() { voidChord(); },
    setMode(next) { mode = CHORD_MODES.includes(next) ? next : "off"; if (mode !== "double") previous = null; },
    reset() { contacts.clear(); pens.clear(); chord = null; previous = null; },
  };
}
