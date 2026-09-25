// When the guide offers a tour by itself, and what it remembers about it.
// Pure, so node tests it (tests/guide.test.mjs).
//
// A tour with a `trigger` is offered at most once per version:
//   trigger: { event, match?, count?, doneOn? }
// - event (+ match): the moment to offer — right after the thing happened,
//   never on mere contact with a control. Without an event, the tour is
//   offered once the tour's `requires` hold (something that happened to the
//   user, e.g. being added to a shared workspace).
// - count: offer on the count-th matching event of this page load.
// - doneOn: an event ({event, match?}) showing the user already knows the
//   feature; it retires the tour's offer without showing anything.
// The tour-level `requires` gates offers and manual starts alike.
import { eventMatches } from "./events.js";

export function factsMatch(requires, facts) {
  return Object.entries(requires || {}).every(([key, value]) => facts[key] === value);
}

// Whether `event` is the tour's trigger event (its `count` is checked by
// canOffer).
export function triggerMatches(tour, event) {
  return !!tour.trigger?.event && !!event && eventMatches(tour.trigger, event.name, event.payload);
}

// Should `tour` be offered now? `event` is the event being considered (none
// for a state check), `seen` how many matching events this page load had.
export function canOffer(tour, { facts, progress, event = null, seen = 0 }) {
  const trigger = tour.trigger;
  if (!trigger || progress?.version >= tour.version) return false;
  if (!factsMatch(tour.requires, facts)) return false;
  if (!trigger.event) return !event;
  return triggerMatches(tour, event) && seen >= (trigger.count || 1);
}

// Does this event retire the tour's offer (the user found the feature)?
export function retiresOffer(tour, event) {
  return !!tour.trigger?.doneOn && !!event && eventMatches(tour.trigger.doneOn, event.name, event.payload);
}

export function guideProgressKey(tour, scope) {
  // Keep the existing manually launched first-run tour's storage compatible.
  return tour.id !== "first-run"
    ? `gamma-guide:${encodeURIComponent(scope)}:${tour.id}`
    : `gamma-guide:${tour.id}`;
}

// Memory also remembers offers if browser storage is unavailable. A fresh
// read observes dismissals from other tabs; progress never stores chat text.
export function createGuideProgress(storage = () => globalThis.localStorage) {
  const memory = new Map();
  return {
    read(tour, scope) {
      const key = guideProgressKey(tour, scope);
      try {
        const value = JSON.parse(storage().getItem(key) || "null");
        if (value && Number.isInteger(value.version) && value.version >= (memory.get(key)?.version ?? 0)) return value;
      } catch { /* unavailable or malformed storage */ }
      return memory.get(key) || null;
    },
    write(tour, scope, value) {
      const key = guideProgressKey(tour, scope);
      const progress = { ...value, version: tour.version };
      memory.set(key, progress);
      try { storage().setItem(key, JSON.stringify(progress)); } catch { /* private mode */ }
    },
  };
}
