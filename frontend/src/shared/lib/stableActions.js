// One stable function per handler name for a component's life, each calling
// the latest handler of that name: the handlers may be rebuilt every render
// (inline closures), and a memoized consumer that takes the wrappers never
// re-renders for a new closure. A value that is not a function passes
// through as it is — data, or null / undefined for a feature the consumer
// gates on. The object itself keeps its identity while it reads the same, so
// handlers that are all functions give one object for the component's life.
import { useRef } from "react";
import { keepIfSame, sameObject } from "./keepIfSame.js";

// The wrapping function, holding the wrappers made so far: `stable(handlers)`
// on every render. For a render site that is not a hook's.
export function makeStableActions() {
  let latest = {};
  const fns = {};
  const prev = { current: null };
  return (handlers) => {
    latest = handlers;
    const out = {};
    for (const key of Object.keys(handlers)) {
      const value = handlers[key];
      if (typeof value !== "function") { out[key] = value; continue; }
      if (!fns[key]) fns[key] = (...args) => latest[key]?.(...args);
      out[key] = fns[key];
    }
    return keepIfSame(prev, out, sameObject);
  };
}

export function useStableActions(handlers) {
  const stable = useRef(null);
  if (!stable.current) stable.current = makeStableActions();
  return stable.current(handlers);
}
