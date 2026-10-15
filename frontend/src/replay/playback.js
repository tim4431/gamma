// The one replay store (docs/dev/recording.md "One clock for every
// replay"): what is replaying, on which clock (replay/clock.js), how far
// along, and how it is shown. A group's replay button, a sheet's and a
// recording's player all play through here, so there is one way to pause,
// seek and set the speed, and one way for a surface to draw itself as of
// the instant: InkLayer, InkCard, NoteSheet, the notes rows and the text
// boxes read it through replay/useFrame.js. Module state with listeners,
// like ink/inkStore.js; one replay at a time.
//
// state: null, or {clock, scope, source, t, playing, rate, mode, onEnd}
//   scope: what takes part — {group: id} | {sheet: id} | {recording: id}
//   source: what keeps the time — {start(t, rate), pause(), seek(t),
//           setRate(r), now() → t, close()}: a stopwatch for a compressed
//           clock, the audio for a recording (recording/audioSource.js)
//   mode: how unreached strokes are drawn — "spotlight" (faded), "reveal"
//         (hidden, the stroke in progress growing) or "static" (as they are)
//   onEnd: "stop" (the replay is over and gone) or "hold" (paused at the end)
//   leadIn: ms a seek to a written moment starts before it (seekBefore)
import { setWallClock } from "../shared/model/wallClock.js";

export const MODES = ["spotlight", "reveal", "static"];

let state = null;
let raf = 0;
const listeners = new Set();
const emit = () => { for (const fn of listeners) fn(); };

export function subscribe(fn) { listeners.add(fn); return () => listeners.delete(fn); }
export function now() { return state; }

// A source that just counts, at the rate asked.
export function stopwatchSource() {
  let base = 0, origin = 0, rate = 1, running = false;
  const read = () => (running ? base + (performance.now() - origin) * rate : base);
  return {
    start(t, r) { base = t; rate = r; origin = performance.now(); running = true; },
    pause() { base = read(); running = false; },
    seek(t) { base = t; origin = performance.now(); },
    setRate(r) { base = read(); origin = performance.now(); rate = r; },
    now: read,
    close() { running = false; },
  };
}

function tick() {
  raf = 0;
  if (!state) return;
  const t = state.source.now();
  if (t >= state.clock.duration) {
    if (state.onEnd === "stop") { stop(); return; }
    state.source.pause();
    state = { ...state, t: state.clock.duration, playing: false };
    emit();
    return;
  }
  state = { ...state, t };
  emit();
  raf = requestAnimationFrame(tick);
}

// While a recording plays, a new note is stamped with the moment heard.
function stampClock() {
  setWallClock(state?.scope.recording ? () => state?.clock.toWall(state.t) ?? Date.now() : null);
}

// Start a replay, ending the one before.
export function play(clock, scope, { source = stopwatchSource(), at = 0, rate = 1, mode = "reveal", onEnd = "stop", leadIn = 3000 } = {}) {
  stop();
  if (!clock || !(clock.duration > 0)) return;
  const t = Math.max(0, Math.min(clock.duration, at));
  state = { clock, scope, source, t, playing: true, rate, mode, onEnd, leadIn };
  source.start(t, rate);
  stampClock();
  emit();
  raf = requestAnimationFrame(tick);
}

export function pause() {
  if (!state?.playing) return;
  cancelAnimationFrame(raf);
  raf = 0;
  state.source.pause();
  state = { ...state, t: state.source.now(), playing: false };
  emit();
}

export function resume() {
  if (!state || state.playing) return;
  const t = state.t >= state.clock.duration ? 0 : state.t;
  state.source.start(t, state.rate);
  state = { ...state, t, playing: true };
  emit();
  raf = requestAnimationFrame(tick);
}

export function toggle() { if (state?.playing) pause(); else resume(); }

export function seek(t) {
  if (!state) return;
  const to = Math.max(0, Math.min(state.clock.duration, t));
  state.source.seek(to);
  state = { ...state, t: to };
  emit();
}

// Seek to a wall-clock moment; false when the clock does not cover it.
export function seekWall(wall) {
  const t = state?.clock.toT(wall);
  if (t == null) return false;
  seek(t);
  return true;
}

// Play from just before a written moment (a tapped stroke, a note's play
// glyph): the lead-in gives the words that led up to it. False when the
// clock does not cover the moment.
export function seekBefore(wall) {
  const t = state?.clock.toT(wall);
  if (t == null) return false;
  seek(Math.max(0, t - state.leadIn));
  if (!state.playing) resume();
  return true;
}

export function setLeadIn(leadIn) {
  if (state && state.leadIn !== leadIn) { state = { ...state, leadIn }; emit(); }
}

export function setRate(rate) {
  if (!state) return;
  state.source.setRate(rate);
  state = { ...state, rate };
  emit();
}

export function setMode(mode) {
  if (!state || !MODES.includes(mode) || state.mode === mode) return;
  state = { ...state, mode };
  emit();
}

// End the replay — only the one of `id` (a group's or a sheet's whose
// drawing changed, a recording that was deleted), when given.
export function stop(id) {
  if (!state) return;
  if (id && state.scope.group !== id && state.scope.sheet !== id && state.scope.recording !== id) return;
  cancelAnimationFrame(raf);
  raf = 0;
  state.source.close();
  state = null;
  stampClock();
  emit();
}
