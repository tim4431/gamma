// The clock a new block is stamped with (`properties.t0`, wall-clock ms):
// the same clock a stroke's `t0` reads, so a recording places notes and
// handwriting on one timeline (docs/dev/recording.md). Usually the real
// time; while a recording plays, its player sets the moment being heard,
// so a note written during playback attaches to it (replay/playback.js).
let clock = null;

export const wallNow = () => (clock ? clock() : Date.now());

// `fn` → the instant to stamp, or null for the real time again.
export function setWallClock(fn) {
  clock = typeof fn === "function" ? fn : null;
}
