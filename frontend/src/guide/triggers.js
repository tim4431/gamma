// When the guide offers a tour by itself, and what it remembers about it.
// Pure, so node tests it (tests/guide.test.mjs).
//
// A tour with a `trigger` is offered at most once per version:
//   trigger: { event, match?, count?, doneOn?, requires?, anyOf? }
// - event (+ match): the moment to offer — right after the thing happened,
//   never on mere contact with a control. Without an event, the tour is
//   offered once the tour's `requires` hold (something that happened to the
//   user, e.g. being added to a shared workspace).
// - requires: facts that gate the OFFER only, never a manual start (the
//   first-run tour is offered on the library, and stays startable from the
//   Tours menu everywhere). anyOf: a list of such fact sets, at least one of
//   which must hold too (a demo server's library, or an empty one).
// - count: offer on the count-th matching event of this page load.
// - doneOn: an event ({event, match?}) showing the user already knows the
//   feature; it retires the tour's offer without showing anything.
// The tour-level `requires` gates offers and manual starts alike.
import { eventMatches } from "./events.js";

// A required value that is an array means "one of these".
export function factsMatch(requires, facts) {
  return Object.entries(requires || {}).every(([key, value]) => (Array.isArray(value) ? value.includes(facts[key]) : facts[key] === value));
}

// Does `step` belong in a run started with these facts? Its `requires` must
// hold. A tour with a `sibling` teaches some of the same controls (the PDF
// and the notebook viewer share their zoom and pen): its `shared` steps are
// for a user who has not finished the sibling.
export function stepApplies(step, facts, siblingDone = false) {
  return factsMatch(step.requires, facts) && !(step.shared && siblingDone);
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
  if (!factsMatch(tour.requires, facts) || !factsMatch(trigger.requires, facts)) return false;
  if (trigger.anyOf && !trigger.anyOf.some((requires) => factsMatch(requires, facts))) return false;
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

// Where progress is kept: localStorage, or sessionStorage on a demo server
// (facts.demo), so every visit starts fresh.
export const guideStorage = (demo) => (demo ? () => globalThis.sessionStorage : () => globalThis.localStorage);

// The account's copy of the progress, as stored in its `tourProgress`
// preference: tour id → {state, version}, without the step. Anything else
// is dropped (app/prefDefs.js reads the preference through this).
const STATES = ["offered", "running", "dismissed", "done"];
export function normalizeTourProgress(value) {
  if (!value || typeof value !== "object" || Array.isArray(value)) return undefined;
  const out = {};
  for (const [id, entry] of Object.entries(value)) {
    if (STATES.includes(entry?.state) && Number.isInteger(entry.version) && entry.version >= 0) {
      out[id] = { state: entry.state, version: entry.version };
    }
  }
  return out;
}

// Of two records of one tour, the one to believe: the higher version, and
// of one version a "done" (a sibling tour asks whether the other was finished).
function newer(a, b) {
  if (!a || !b) return a || b;
  if (a.version !== b.version) return a.version > b.version ? a : b;
  return b.state === "done" && a.state !== "done" ? b : a;
}

// Memory also remembers offers if browser storage is unavailable. A fresh
// read observes dismissals from other tabs; progress never stores chat text.
//
// `synced` is the account's copy (the `tourProgress` preference, which
// travels with the profile to the account's other browsers and, through
// Gamma Cloud, to its other servers): `get()` returns it, or null until the
// profile has loaded; `set(fn)` updates it. A read takes the newer of the
// two, so a tour seen anywhere is not offered here again. `seed` copies up
// what only this browser knows: progress from before the copy existed, or
// written before the profile loaded.
export function createGuideProgress(storage = () => globalThis.localStorage, synced = null) {
  const memory = new Map();
  const local = (tour, scope) => {
    const key = guideProgressKey(tour, scope);
    try {
      const value = JSON.parse(storage().getItem(key) || "null");
      if (value && Number.isInteger(value.version) && value.version >= (memory.get(key)?.version ?? 0)) return value;
    } catch { /* unavailable or malformed storage */ }
    return memory.get(key) || null;
  };
  const shared = (tour) => normalizeTourProgress(synced?.get())?.[tour.id] || null;
  const share = (records) => {
    if (!synced?.get() || !records.length) return;
    synced.set((all) => {
      const changed = records.filter(([id, r]) => all?.[id]?.state !== r.state || all?.[id]?.version !== r.version);
      return changed.length ? { ...all, ...Object.fromEntries(changed) } : all;
    });
  };
  return {
    read(tour, scope) {
      return newer(local(tour, scope), shared(tour));
    },
    write(tour, scope, value) {
      const key = guideProgressKey(tour, scope);
      const progress = { ...value, version: tour.version };
      memory.set(key, progress);
      try { storage().setItem(key, JSON.stringify(progress)); } catch { /* private mode */ }
      share([[tour.id, { state: progress.state, version: progress.version }]]);
    },
    seed(tours, scope) {
      share(tours.flatMap((tour) => {
        const mine = local(tour, scope), theirs = shared(tour);
        return mine && newer(theirs, mine) === mine ? [[tour.id, { state: mine.state, version: mine.version }]] : [];
      }));
    },
  };
}
