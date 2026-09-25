// The guide engine: which tour is running and at which step, how a step
// advances (Next, or the step's event firing), demo steps that act on the
// UI themselves (`do: [...]`), which triggered tour or hint is offered
// (guide/triggers.js), and where progress is kept. The overlay only renders
// what this hook says; anchors are resolved by id. docs/dev/onboarding.md.
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ANCHORS, anchorElement } from "./anchors.js";
import { guideEvents, eventMatches } from "./events.js";
import { TOURS } from "./tours/index.js";
import { previewHighlight } from "./previewHighlight.js";
import { previewArea } from "./previewArea.js";
import { typeDemoNote } from "./typeDemoNote.js";
import { canOffer, createGuideProgress, factsMatch, retiresOffer, triggerMatches } from "./triggers.js";
import { t } from "../shared/i18n/i18n.js";

const VARS_KEY = "gamma-guide-vars"; // {name: value} overriding a tour's vars (tests, demos)
const ANCHOR_WAIT_MS = 4000;
const EVENT_WAIT_MS = 90000;         // a paper download can take a while

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

function readVars() {
  try { return JSON.parse(localStorage.getItem(VARS_KEY) || "{}") || {}; } catch { return {}; }
}
const fill = (text, vars) => String(text ?? "").replace(/\{(\w+)\}/g, (m, k) => (k in vars ? vars[k] : m));

async function waitAnchor(id, timeout = ANCHOR_WAIT_MS) {
  const started = performance.now();
  for (;;) {
    const el = anchorElement(id);
    if (el) return el;
    if (performance.now() - started > timeout) throw new Error(`anchor "${id}" not found`);
    await sleep(50);
  }
}

// Resolves on a matching event — including one already in `seen`, the
// events that fired since the demo step began (the thing an action triggers
// can finish before the next action starts waiting for it).
function waitEvent(spec, seen, timeout = EVENT_WAIT_MS) {
  const early = seen.find(([name, payload]) => eventMatches(spec, name, payload));
  if (early) return Promise.resolve(early[1]);
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => { off(); reject(new Error(`timed out waiting for ${spec.event}`)); }, timeout);
    const off = guideEvents.subscribe((name, payload) => {
      if (eventMatches(spec, name, payload)) { clearTimeout(timer); off(); resolve(payload); }
    });
  });
}

// React-controlled inputs only notice a value set through the native setter
// followed by an input event.
function setInputValue(el, value) {
  const proto = el instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
  Object.getOwnPropertyDescriptor(proto, "value").set.call(el, value);
  el.dispatchEvent(new Event("input", { bubbles: true }));
}

const centerOf = (el) => { const b = el.getBoundingClientRect(); return { x: b.left + b.width / 2, y: b.top + b.height / 2 }; };

// The demo vocabulary. Each action moves the spotlight (and the pointer) to
// the element it acts on, waits a beat so the eye can follow, then acts.
async function runAction(action, vars, live, cancelled, seen, onCleanup, services) {
  const check = () => { if (cancelled()) throw new Error("cancelled"); };
  if (action.previewHighlight) return previewHighlight(live, cancelled, onCleanup);
  if (action.previewArea) return previewArea(live, cancelled, onCleanup, action.context ? null : services.findEquation, action.context);
  if (action.note) return typeDemoNote(action.note, services.prepareNote, live, cancelled);
  if (action.wait) { await sleep(action.wait); return; }
  if (action.waitFor) { await waitEvent(action.waitFor, seen, action.timeout); return; }
  if (action.click) {
    const el = await waitAnchor(action.click);
    live({ anchor: action.click, cursor: centerOf(el) });
    await sleep(550); check();
    live({ anchor: action.click, cursor: { ...centerOf(el), pressed: true } });
    el.click();
    await sleep(350);
    return;
  }
  if (action.type) {
    const el = await waitAnchor(action.type);
    const text = fill(t(action.text), vars);
    const original = el.value;
    if (action.preserveDraft) onCleanup(() => {
      if (el.isConnected && text.startsWith(el.value) && (original || el.value !== text)) setInputValue(el, original);
    });
    live({ anchor: action.type, cursor: centerOf(el) });
    await sleep(450); check();
    el.focus();
    let value = "";
    for (const ch of text) {
      check();
      value += ch;
      setInputValue(el, value);
      await sleep(action.speed ?? 28);
    }
    await sleep(300);
    return;
  }
  if (action.press) {
    const el = action.on ? await waitAnchor(action.on) : document.activeElement;
    const init = { key: action.press, code: action.press, bubbles: true, cancelable: true };
    el.dispatchEvent(new KeyboardEvent("keydown", init));
    el.dispatchEvent(new KeyboardEvent("keyup", init));
    await sleep(200);
    return;
  }
  throw new Error(`unknown action ${JSON.stringify(action)}`);
}

// Reveal an anchor inside a closed surface: click through its registered
// `open` path, skipping the parts that are already open.
async function revealAnchor(id, cancelled) {
  const path = ANCHORS[id]?.open || [];
  for (let i = 0; i < path.length; i++) {
    if (cancelled() || anchorElement(id)) return;
    if (anchorElement(path[i + 1] || id)) continue;
    const el = await waitAnchor(path[i]).catch(() => null);
    if (!el || cancelled()) return;
    el.click();
    await sleep(150);
  }
}

// Where a triggered tour's offer points: its own `offerAnchor` when the
// first step sits inside a surface that may be closed, else that step's.
const offerAnchor = (tour) => tour.offerAnchor || tour.steps[0]?.anchor || null;
const TRIGGERED = Object.values(TOURS).filter((tour) => tour.trigger);
const TRIGGER_EVENTS = new Set(TRIGGERED.flatMap((tour) => [tour.trigger.event, tour.trigger.doneOn?.event]).filter(Boolean));
const SETTLE_MS = 3000; // state-triggered offers wait for the app to settle after load

// enabled: the guide may run at all (signed in, not a share view). suggest:
// the account's "Suggest tours" preference — off, nothing is offered by
// itself; the Tours menu still works. facts: what App knows (view, hasPdf…),
// matched against `requires`. services: App's hands — show(surface) brings
// up a tour's `show` surface, plus the demo helpers. tidy: closes App's
// transient popovers when a step needs none of them.
export function useGuide({ enabled = true, suggest = true, scope = "", facts = {}, services = {}, tidy } = {}) {
  const servicesRef = useRef(services);
  servicesRef.current = services;
  // done: acknowledge the user's action before automatically advancing.
  const [run, setRun] = useState(null); // { tour, scope, steps, index, done } | null
  const [offer, setOffer] = useState(null); // { tour, scope } | null
  const progress = useRef(null);
  if (!progress.current) progress.current = createGuideProgress();
  // Synchronously reserves the one guide surface: "running" | "offered" | null.
  const activity = useRef(null);
  // One automatic offer per page load, whatever becomes of it.
  const offeredThisLoad = useRef(false);
  const seen = useRef(new Map()); // tour id → its trigger's events this page load
  const [settled, setSettled] = useState(false);
  // live: what a demo step is doing right now — the anchor it acts on, the
  // pointer's position, whether actions are still running.
  const [live, setLive] = useState({ anchor: null, cursor: null, busy: false });
  const factsRef = useRef(facts);
  factsRef.current = facts;

  // Steps whose `requires` don't hold are dropped from this run.
  const steps = run?.steps || [];
  const stepsFor = (tour) => tour.steps.filter((s) => factsMatch(s.requires, factsRef.current));

  const start = useCallback((tourId, at = 0) => {
    const tour = TOURS[tourId];
    if (!tour) { console.warn(`guide: no tour "${tourId}"`); return false; }
    if (!enabled || tour.hint || !factsMatch(tour.requires, factsRef.current)) return false;
    const steps = stepsFor(tour);
    if (!steps.length || at < 0 || at >= steps.length) return false;
    if (tour.show) servicesRef.current.show?.(tour.show);
    activity.current = "running";
    setOffer(null);
    setRun({ tour, scope, steps, index: at, done: false });
    progress.current.write(tour, scope, { state: "running", step: at });
    return true;
  }, [enabled, scope]);

  // Can this tour start where the user is? What the Tours menu lists: its
  // prerequisites hold and its first step's anchor (or the control that
  // reveals it) is on screen — or the tour brings up its own surface.
  const canStart = (tourId) => {
    const tour = TOURS[tourId];
    if (!tour || tour.hint || !enabled || !factsMatch(tour.requires, factsRef.current)) return false;
    const first = stepsFor(tour)[0];
    if (!first) return false;
    if (tour.show || !first.anchor) return true;
    return !!anchorElement([...(ANCHORS[first.anchor]?.open || []), first.anchor][0]);
  };

  const stop = useCallback((state) => {
    setRun((r) => {
      if (r) progress.current.write(r.tour, r.scope, { state, step: r.index });
      return null;
    });
    activity.current = null;
  }, []);

  const next = useCallback(() => {
    setRun((r) => {
      if (!r) return r;
      if (r.index + 1 >= steps.length) {
        progress.current.write(r.tour, r.scope, { state: "done" });
        activity.current = null;
        return null;
      }
      return { ...r, index: r.index + 1, done: false };
    });
  }, [steps.length]);

  const back = useCallback(() => {
    setRun((r) => (r && r.index > 0 ? { ...r, index: r.index - 1, done: false } : r));
  }, []);

  const dismiss = useCallback(() => stop("dismissed"), [stop]);

  const closeOffer = useCallback((state) => {
    setOffer((o) => {
      if (o) progress.current.write(o.tour, o.scope, { state });
      return null;
    });
    activity.current = null;
  }, []);
  const dismissOffer = useCallback(() => closeOffer("dismissed"), [closeOffer]);
  // A hint's card is the whole guide: accepting it is finishing it.
  const acceptOffer = useCallback(() => {
    if (!offer) return;
    if (offer.tour.hint) closeOffer("done");
    else start(offer.tour.id);
  }, [offer, start, closeOffer]);

  useEffect(() => {
    if (!enabled) return undefined;
    const timer = setTimeout(() => setSettled(true), SETTLE_MS);
    return () => clearTimeout(timer);
  }, [enabled]);

  // Offers: an event is considered once, right after it happened — never
  // queued behind another guide; state triggers whenever the facts change.
  const consider = useCallback((event) => {
    if (!enabled || !scope) return;
    const current = factsRef.current;
    if (event) {
      for (const tour of TRIGGERED) {
        if (retiresOffer(tour, event)) {
          const state = progress.current.read(tour, scope)?.state;
          if (!state || state === "offered") progress.current.write(tour, scope, { state: "done" });
          setOffer((o) => { if (o?.tour !== tour) return o; activity.current = null; return null; });
        }
        if (triggerMatches(tour, event) && factsMatch(tour.requires, current)) {
          seen.current.set(tour.id, (seen.current.get(tour.id) || 0) + 1);
        }
      }
    }
    if (!suggest || offeredThisLoad.current || activity.current || current.guideAvailable === false) return;
    if (!event && !settled) return;
    const tour = TRIGGERED.find((candidate) => canOffer(candidate, {
      facts: current, progress: progress.current.read(candidate, scope), event, seen: seen.current.get(candidate.id) || 0,
    }));
    if (!tour) return;
    offeredThisLoad.current = true;
    activity.current = "offered";
    progress.current.write(tour, scope, { state: "offered" });
    setOffer({ tour, scope });
  }, [enabled, suggest, scope, settled]);
  // An event is judged after the render it came with, so the facts include
  // what the same action changed (a new share link, then "share.created").
  const [pending, setPending] = useState([]);
  useEffect(() => guideEvents.subscribe((name, payload) => {
    if (TRIGGER_EVENTS.has(name)) setPending((q) => [...q, { name, payload }]);
  }), []);
  useEffect(() => {
    if (!pending.length) return;
    setPending([]);
    pending.forEach(consider);
  }, [pending, consider]);
  const factsKey = JSON.stringify(facts);
  useEffect(() => { consider(); }, [consider, factsKey]);

  // Losing a prerequisite, switching account or turning suggestions off
  // removes the guide. A shown offer stays remembered.
  const runAvailable = enabled && run?.scope === scope && factsMatch(run?.tour.requires, facts);
  const offerAvailable = enabled && suggest && offer?.scope === scope && facts.guideAvailable !== false
    && factsMatch(offer?.tour.requires, facts);
  useEffect(() => {
    if (run && !runAvailable) { setRun(null); activity.current = null; }
    if (offer && !offerAvailable) { setOffer(null); activity.current = null; }
  }, [run, runAvailable, offer, offerAvailable]);
  useEffect(() => {
    if (!offerAvailable) return undefined;
    const onKey = (e) => {
      if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); dismissOffer(); }
    };
    window.addEventListener("keydown", onKey, true);
    return () => window.removeEventListener("keydown", onKey, true);
  }, [offerAvailable, dismissOffer]);

  // Task-driven completion: the current step's event fires → the step is done.
  const step = run ? steps[run.index] : null;
  // Include the introductory pause: the demo owns navigation from its very
  // first frame, before its first action has started.
  const busy = !!step?.do && (live.stepId !== step.id || (!live.failed && !live.finished));
  useEffect(() => {
    if (!step?.advanceOn) return undefined;
    return guideEvents.subscribe((name, payload) => {
      if (eventMatches(step.advanceOn, name, payload)) setRun((r) => (r ? { ...r, done: true } : r));
    });
  }, [step]);

  // Demo steps: run the actions, then hand over (advanceOn) or move on.
  const nextRef = useRef(next);
  nextRef.current = next;
  // Let the acknowledgement register before moving on; cancelling or manually
  // navigating clears the timer so an old completion cannot skip a new step.
  useEffect(() => {
    if (!run?.done || !step?.advanceOn) return undefined;
    const timer = setTimeout(() => nextRef.current(), 1100);
    return () => clearTimeout(timer);
  }, [run?.done, step]);
  useEffect(() => {
    if (!step?.do) return undefined;
    let cancelled = false;
    const cleanups = [];
    const vars = { ...(run.tour.vars || {}), ...readVars() };
    const seen = [];
    const unsubscribe = guideEvents.subscribe((name, payload) => { seen.push([name, payload]); });
    (async () => {
      await sleep(step.delay ?? 900);
      if (cancelled) return;
      setLive({ anchor: null, cursor: null, busy: true, stepId: step.id });
      try {
        for (const action of step.do) {
          if (cancelled) return;
          await runAction(action, vars, (l) => { if (!cancelled) setLive({ ...l, busy: true, stepId: step.id }); }, () => cancelled, seen, (cleanup) => cleanups.push(cleanup), servicesRef.current);
        }
        if (cancelled) return;
        setLive({ anchor: null, cursor: null, busy: false, finished: true, stepId: step.id });
        if (!step.advanceOn) nextRef.current();
      } catch (err) {
        if (cancelled) return;
        console.warn(`guide: demo step "${step.id}" stopped: ${err.message}`);
        setLive({ anchor: null, cursor: null, busy: false, failed: true, stepId: step.id });
      }
    })();
    return () => { cancelled = true; cleanups.forEach((cleanup) => cleanup()); unsubscribe(); setLive({ anchor: null, cursor: null, busy: false }); };
  }, [step]);

  // Before a step shows: an anchor inside a closed surface is revealed
  // through its `open` path; any other anchor gets the app tidied (a popover
  // the previous step opened goes away). An `optional` step whose anchor is
  // not on screen — avatars on a block when nobody is on one — is passed
  // over silently.
  const tidyRef = useRef(tidy);
  tidyRef.current = tidy;
  useEffect(() => {
    if (!step) return undefined;
    let cancelled = false;
    if (ANCHORS[step.anchor]?.open) revealAnchor(step.anchor, () => cancelled);
    else tidyRef.current?.();
    const timer = step.optional
      ? setTimeout(() => { if (!anchorElement(step.anchor)) nextRef.current(); }, 300) : 0;
    return () => { cancelled = true; clearTimeout(timer); };
  }, [step]);

  // Keys: Esc leaves, → / Enter advance, ← goes back — never inside an editor.
  useEffect(() => {
    if (!run) return undefined;
    const onKey = (e) => {
      if (!e.isTrusted) return; // the demo's own synthetic keys
      if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); dismiss(); return; }
      if (busy) {
        if (["Escape", "ArrowRight", "ArrowLeft", "Enter"].includes(e.key)) {
          e.preventDefault();
          e.stopPropagation();
          if (e.key === "Escape") dismiss();
        }
        return;
      }
      const t = e.target;
      if (t && (t.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(t.tagName))) return;
      // Let Enter activate the focused Back/Close/Next button normally.
      if (e.key === "Enter" && t?.closest?.("button, a, [role=button]")) return;
      if (e.key === "ArrowRight" || e.key === "Enter") { e.preventDefault(); next(); }
      else if (e.key === "ArrowLeft") { e.preventDefault(); back(); }
    };
    window.addEventListener("keydown", onKey, true);
    return () => window.removeEventListener("keydown", onKey, true);
  }, [run, busy, next, back, dismiss]);

  // What the overlay shows for an offer: a tour's name and length, or a
  // hint's one card. Stable while it stays up, so the overlay keeps its
  // anchor tracking.
  const offerTour = offerAvailable ? offer.tour : null;
  const offerCard = useMemo(() => {
    if (!offerTour) return null;
    const steps = stepsFor(offerTour);
    return {
      id: offerTour.id,
      hint: !!offerTour.hint,
      title: offerTour.hint ? steps[0]?.title : offerTour.title,
      anchor: offerTour.hint ? steps[0]?.anchor || null : offerAnchor(offerTour),
      placement: offerTour.hint ? steps[0]?.placement : offerTour.offerPlacement,
      count: steps.length,
    };
  }, [offerTour, factsKey]);
  return {
    running: !!run && runAvailable,
    offer: offerCard,
    acceptOffer, dismissOffer,
    tour: run?.tour || null,
    step,
    index: run?.index ?? 0,
    done: !!run?.done,
    count: steps.length,
    live: { ...live, busy },
    start, next, back, dismiss,
    // The tours the Tours menu lists here, in registry order.
    startable: () => Object.values(TOURS).filter((tour) => canStart(tour.id)).map(({ id, title }) => ({ id, title })),
    progressOf: (id) => TOURS[id] ? progress.current.read(TOURS[id], scope) : null,
  };
}
