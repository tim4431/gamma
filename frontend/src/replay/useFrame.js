// How React reads the replay store (replay/playback.js): a surface asks for
// the replay's state only if what it shows takes part, so the layers, cards
// and rows outside a replay never re-render for its frames.
import { useSyncExternalStore } from "react";
import { inkWallSpan } from "../ink/ink.js";
import * as playback from "./playback.js";

// The whole state, for the player bar.
export function useReplay() {
  return useSyncExternalStore(playback.subscribe, playback.now);
}

// Whether a handwriting group takes part in the replay: its own, the one
// of the sheet it is on (`surface`), or a recording whose span its timed
// strokes cross.
export function groupInScope(state, id, ink, surface = null) {
  if (!state) return false;
  const { scope } = state;
  if (scope.group || scope.sheet) return scope.group === id || (scope.sheet != null && scope.sheet === surface);
  const span = inkWallSpan(ink);
  return !!span && span[0] <= state.clock.span[1] && span[1] >= state.clock.span[0];
}

// The replay's state while one of `groups` ([{id, ink}]) on `surface`
// takes part, else null. A layer draws each group it has with frameOf.
export function useGroupFrame(groups, surface = null) {
  return useSyncExternalStore(playback.subscribe, () => {
    const state = playback.now();
    return state && groups.some((g) => groupInScope(state, g.id, g.ink, surface)) ? state : null;
  });
}

// The state while `id`'s own replay plays: a group's or a sheet's button,
// a recording's card.
export function useOwnReplay(id) {
  return useSyncExternalStore(playback.subscribe, () => {
    const state = playback.now();
    return state && (state.scope.group === id || state.scope.sheet === id || state.scope.recording === id) ? state : null;
  });
}

// What inkUntil needs from a state: the instant on the clock the strokes
// are placed by (`at`), that clock's table (`times`, none for a recording:
// the strokes' own wall clock places them) and the display mode.
export function frameOf(state) {
  if (!state) return null;
  const compressed = !!state.clock.times;
  return { at: compressed ? state.t : state.clock.toWall(state.t), times: compressed ? state.clock.times : null, mode: state.mode };
}

// A block stamped with `t0` under a playing recording: "pending" before
// the moment it was written, "reached" after, null when no recording that
// covers it plays.
export function useBlockFrame(t0) {
  return useSyncExternalStore(playback.subscribe, () => {
    const state = playback.now();
    if (!state?.scope.recording || !Number.isFinite(t0)) return null;
    const [from, to] = state.clock.span;
    if (t0 < from || t0 > to) return null;
    const wall = state.clock.toWall(state.t);
    return wall != null && t0 <= wall ? "reached" : "pending";
  });
}
