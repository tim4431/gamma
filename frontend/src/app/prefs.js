// The user preferences behind the Settings dialog as React state: one
// localStorage-backed state per entry of PREFS (app/prefDefs.js, where each
// preference declares its key, default, codec and scope), plus the sync of
// the account-scoped ones through the account's profile.
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { keepaliveFetch } from "../shared/lib/keepalive.js";
import { API, apiJson, usePersistedState } from "../shared/lib/utils";
import { PROFILE_SEEN_KEY } from "./boot.js";
import { ACCOUNT_PREFS, PREFS, profileOf, readProfile, setterName } from "./prefDefs.js";

export { FREE_TRANSLATE_ENGINE, TRANSLATE_LANGS, TRANSLATE_PARALLEL_MAX, UI_SCALE, themeScheme, translateModelFor } from "./prefDefs.js";

const PREF_NAMES = Object.keys(PREFS);

// {name, setName} for every preference. The loop runs the same hooks in the
// same order on every render (PREFS is a constant).
export function useAppPrefs() {
  const prefs = {};
  for (const name of PREF_NAMES) {
    const def = PREFS[name];
    const [value, setValue] = usePersistedState(def.key, def.default, def);
    prefs[name] = value;
    prefs[setterName(name)] = setValue;
  }
  return prefs;
}

const PROFILE_URL = `${API}/prefs/profile`;

// The first load's preference reads (the profile, open tabs, recents,
// reading positions, the AI key) go out as one request, GET /api/prefs?keys=,
// as soon as the session is known (App.jsx checkSession), right behind the
// library listing: five requests would queue for the browser's six
// connections to the server and hold the listing back. Each reader takes
// its key's answer once, for the account it was read for; any later read
// asks for its key alone. A guest's preferences stay in its browser, so it
// reads no profile.
const FIRST_LOAD_PREFS = ["profile", "open-tabs", "recent-views", "read-pos", "ai-provider"];
const preloads = new Map(); // key -> {user, read}
export function preloadPrefs(user, { guest = false } = {}) {
  const keys = FIRST_LOAD_PREFS.filter((key) => !guest || key !== "profile");
  preloads.clear();
  const all = apiJson(`${API}/prefs?keys=${keys.map(encodeURIComponent).join(",")}`);
  for (const key of keys) {
    const read = all.then((d) => d.prefs[key]);
    read.catch(() => {}); // its reader sees a failure; a key nobody reads is no error
    preloads.set(key, { user, read });
  }
}
export function readPref(key, user) {
  const preload = preloads.get(key);
  preloads.delete(key);
  return preload && preload.user === user ? preload.read : apiJson(`${API}/prefs/${key}`);
}
// Changes are pushed once they settle, so a slider does not send one per tick.
const PUSH_DELAY_MS = 1000;
// Focus flaps must not hammer the server: wake pulls at most this often.
const PULL_MIN_MS = 15000;

// Keeps the account-scoped preferences in step with the account's profile
// (/api/prefs/profile, one object keyed by preference name). `user` is the
// signed-in account, or "" for signed out, a guest and share views — those
// keep working from localStorage alone.
//
// The server copy wins on load and on a focus pull; an account without a
// profile is seeded from this browser. Local changes push after they settle,
// and a pending push is flushed when the page is hidden. A push (PATCH)
// carries only the entries that changed since the copy last seen, so this
// tab's stale value of another entry — one Gamma Cloud brought in from
// another server meanwhile — never undoes that change. Until the first load
// succeeds nothing is pushed, so a session can't overwrite a copy it never
// saw.
//
// Returns where that stands (the Settings dialog's section tags read it,
// settings/syncState.js):
// - `state`: "signed-out" (localStorage only), "loading" (the first pull),
//   "loaded" (in step with the server), "pending" (a change settling),
//   "pushing" (its PATCH in flight), "failed" (the last pull or push failed;
//   `error` says why);
// - Sets of preference names: `pending` (the value differs from the copy
//   the server last confirmed), `inflight` (sent in the PATCH now on its
//   way), `failed` (a push of exactly this value failed; the next change
//   sends it again), `awaitingCloud` (pushed since Gamma Cloud last
//   reported the profile synced);
// - `flush()`: sends a settled or pending change now and resolves once the
//   server has answered (at once when nothing is pending or signed out), for
//   a caller about to remount or reload — a fresh instance pulls "server
//   wins", so the change must be there first (the language switch);
// - `noteCloud(profile)`: the dialog hands it every sync-status answer; a
//   "synced" at a time after the last push clears `awaitingCloud`;
// - `cloudChoice`: the server's first sync with Gamma Cloud found two
//   different copies and waits for the person's choice (Settings → Account & sync);
// - `reload()`: sends a pending change, then pulls — after a sync from
//   Settings replaced entries on the server.
const NONE = new Set();
const perName = (values) => Object.fromEntries(ACCOUNT_PREFS.map((name) => [name, JSON.stringify(values[name])]));
// The profile as this browser last took it from the server or sent it
// there ({name: JSON}, perName's), whichever account it was
// (`gamma-profile-seen`, PROFILE_SEEN_KEY): a local value equal to it came
// from an account, not from this browser, even when another account signs
// in next. One record for the browser, as its settings are one: the next
// account's first pull writes over it. Without it (another browser
// profile, storage cleared) every local value that is not the default
// counts as this browser's. main.jsx reads it too: a stored language equal
// to the seen one is the account's, so the app need not wait for the session.
function seenOf() {
  try {
    const seen = JSON.parse(localStorage.getItem(PROFILE_SEEN_KEY) || "null");
    return seen && typeof seen === "object" && !Array.isArray(seen) ? seen : {};
  } catch { return {}; }
}
function rememberSeen(seen) {
  try { localStorage.setItem(PROFILE_SEEN_KEY, JSON.stringify(seen)); } catch { /* the next load asks as if none were kept */ }
}
// failed: {name: the value whose push failed}; awaitingAt: the server time of the last push
const IDLE = { inflight: NONE, failed: {}, awaiting: NONE, awaitingAt: "" };

// `onReplaced({name, local})`, when given, is told on a load's first pull of
// an account (once per entry) of an entry whose server value replaced a
// local one this browser made itself: neither the default nor the value as
// this browser last took it from an account or sent it (`seenOf` above).
// The browser had its own and the account's won, so the caller can offer
// to push the local value back (the strip's presets: "Use this browser's
// pens instead?"). A later pull, or a later load, that finds the account
// changed since is another device's change, so it never asks.
export function useProfileSync(prefs, user, { onReplaced } = {}) {
  const values = profileOf(prefs);
  const snap = JSON.stringify(values);
  const latest = useRef(null);
  latest.current = { prefs, snap, user, onReplaced };
  const syncedRef = useRef(null); // the server's profile as last seen/sent; null = not loaded
  const confirmedRef = useRef(null); // {name: JSON} the server confirmed holding; null = not loaded
  const timerRef = useRef(null);
  const pushIdRef = useRef(0);
  const sendingRef = useRef(0); // pushes not answered yet
  const lastPushRef = useRef(Promise.resolve()); // the newest push, for flush()
  const [status, setStatus] = useState(() => ({ state: user ? "loading" : "signed-out", error: "" }));
  const [flight, setFlight] = useState(IDLE);
  const [cloudChoice, setCloudChoice] = useState(false);
  const mark = (state, error = "") => setStatus((was) => (was.state === state && was.error === error ? was : { state, error }));

  // The server's profile onto the settings, except the names in `keep`;
  // `first` on the account's first pull of this load, the only one that can
  // replace a value this browser had on its own.
  function apply(value, keep = NONE, first = false) {
    const read = readProfile(value);
    const current = latest.current.prefs;
    const seen = first ? seenOf() : {};
    const replaced = [];
    for (const [name, v] of Object.entries(read)) {
      if (keep.has(name)) continue;
      const local = JSON.stringify(current[name]);
      if (local === JSON.stringify(v)) continue;
      if (first && local !== JSON.stringify(PREFS[name].default) && local !== seen[name]) replaced.push({ name, local: current[name] });
      current[setterName(name)](v);
    }
    for (const entry of replaced) latest.current.onReplaced?.(entry);
    return JSON.stringify(read);
  }

  function push(keepalive = false) {
    clearTimeout(timerRef.current);
    timerRef.current = null;
    const { snap: now, user: u } = latest.current;
    if (!u || syncedRef.current === null) return Promise.resolve();
    if (now === syncedRef.current) { mark("loaded"); return lastPushRef.current; } // changed back, or already on its way
    const before = syncedRef.current;
    syncedRef.current = now;
    const was = before ? JSON.parse(before) : {};
    const changed = Object.entries(JSON.parse(now)).filter(([name, v]) => JSON.stringify(v) !== JSON.stringify(was[name]));
    const request = {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ set: Object.fromEntries(changed) }),
    };
    const sent = perName(JSON.parse(now));
    // The page is going away: keepalive when the page's keepalive room has
    // space for it at the normal priority (shared/lib/keepalive.js), since
    // it is small and losing it costs only the last change of a setting.
    // An answer the page lives to read is remembered as a push's is.
    if (keepalive) {
      keepaliveFetch(PROFILE_URL, { ...request, credentials: "same-origin" }, { priority: "normal" })
        .then((r) => { if (r.ok && latest.current.user === u) rememberSeen(sent); }).catch(() => {});
      return Promise.resolve();
    }
    const confirmed = confirmedRef.current || {};
    const names = ACCOUNT_PREFS.filter((name) => sent[name] !== confirmed[name]);
    const id = ++pushIdRef.current;
    sendingRef.current += 1;
    setFlight((f) => ({ ...f, inflight: new Set(names) }));
    mark("pushing");
    // Only the latest push's answer ends `inflight`.
    const settle = (next) => setFlight((f) => ({ ...next(f), inflight: id === pushIdRef.current ? NONE : f.inflight }));
    const done = apiJson(PROFILE_URL, request).then((d) => {
      sendingRef.current -= 1;
      if (latest.current.user !== u) return;
      confirmedRef.current = sent;
      rememberSeen(sent);
      settle((f) => ({ ...f, failed: {}, awaiting: new Set([...f.awaiting, ...names]), awaitingAt: d?.updated_at || f.awaitingAt }));
      if (!timerRef.current && syncedRef.current === now) mark("loaded");
    }, (err) => {
      sendingRef.current -= 1;
      // A failed push leaves the change pending, so a later pull doesn't revert it.
      if (syncedRef.current === now) syncedRef.current = before;
      if (latest.current.user !== u) return;
      settle((f) => ({ ...f, failed: { ...f.failed, ...Object.fromEntries(names.map((name) => [name, sent[name]])) } }));
      if (!timerRef.current) mark("failed", err?.message || "");
    });
    lastPushRef.current = done;
    return done;
  }
  const pushRef = useRef(push);
  pushRef.current = push;
  const flush = useCallback(() => pushRef.current(), []);
  const pullRef = useRef(null);
  const reload = useCallback(() => pushRef.current().then(() => {
    const u = latest.current.user;
    return u ? pullRef.current(u) : undefined;
  }), []);

  function schedule() {
    if (syncedRef.current === null || latest.current.snap === syncedRef.current) return;
    clearTimeout(timerRef.current);
    timerRef.current = setTimeout(push, PUSH_DELAY_MS);
    mark("pending");
  }

  function pull(u) {
    const startSnap = latest.current.snap;
    return readPref("profile", u).then((d) => {
      // A local change waiting to go out, or on its way, is newer than what the server holds.
      if (latest.current.user !== u) return;
      setCloudChoice(!!d.cloud_choice);
      if (timerRef.current || sendingRef.current) return;
      // After a first load, or once the server's copy replaced this one, nothing is failed.
      let settled = confirmedRef.current === null;
      if (!d.updated_at) {
        if (settled) { syncedRef.current = ""; confirmedRef.current = {}; } // never saved: seed from here
      } else {
        const read = readProfile(d.value);
        if (JSON.stringify(read) !== syncedRef.current) {
          // A setting changed here while this pull was on its way (before the
          // first load lands, nothing schedules a push yet) is newer than the
          // server's copy: it stays, and goes out as the next push.
          const was = JSON.parse(startSnap), now = JSON.parse(latest.current.snap);
          const keep = new Set(Object.keys(now).filter((name) => JSON.stringify(now[name]) !== JSON.stringify(was[name])));
          syncedRef.current = apply(d.value, keep, confirmedRef.current === null);
          settled = true;
        }
        confirmedRef.current = perName(read);
        rememberSeen(confirmedRef.current);
      }
      if (settled) setFlight((f) => ({ ...f, failed: {} }));
      mark("loaded");
      schedule();
    }).catch((err) => {
      if (latest.current.user !== u || timerRef.current) return;
      // The first load failed: none of these settings are the account's copy yet.
      if (confirmedRef.current === null) setFlight((f) => ({ ...f, failed: perName(profileOf(latest.current.prefs)) }));
      mark("failed", err?.message || "");
    });
  }

  pullRef.current = pull;

  useEffect(() => {
    syncedRef.current = null;
    confirmedRef.current = null;
    clearTimeout(timerRef.current);
    timerRef.current = null;
    setFlight(IDLE);
    setCloudChoice(false);
    mark(user ? "loading" : "signed-out");
    if (user) pull(user);
  }, [user]);

  useEffect(() => { schedule(); }, [snap, user]);

  // Unmounting must not drop a change that was still settling.
  useEffect(() => () => { if (timerRef.current) pushRef.current(true); }, []);

  useEffect(() => {
    let lastPull = 0;
    const onWake = () => {
      const u = latest.current.user;
      if (!u || document.hidden || Date.now() - lastPull < PULL_MIN_MS) return;
      lastPull = Date.now();
      pull(u);
    };
    const flush = () => { if (timerRef.current) push(true); };
    const onVisibility = () => (document.hidden ? flush() : onWake());
    window.addEventListener("focus", onWake);
    window.addEventListener("pagehide", flush);
    document.addEventListener("visibilitychange", onVisibility);
    return () => {
      window.removeEventListener("focus", onWake);
      window.removeEventListener("pagehide", flush);
      document.removeEventListener("visibilitychange", onVisibility);
    };
  }, []);

  const noteCloud = useCallback((profile) => {
    if (profile?.state !== "synced" || !profile.at) return;
    setFlight((f) => (f.awaiting.size && profile.at > f.awaitingAt ? { ...f, awaiting: NONE, awaitingAt: "" } : f));
  }, []);

  // A name whose push of exactly this value failed reads as failed, not pending.
  const confirmed = confirmedRef.current;
  const pendingKey = confirmed ? ACCOUNT_PREFS.filter((name) => {
    const now = JSON.stringify(values[name]);
    return now !== confirmed[name] && now !== flight.failed[name];
  }).join(",") : "";
  const failedKey = Object.keys(flight.failed).filter((name) => JSON.stringify(values[name]) === flight.failed[name]).join(",");
  const pending = useMemo(() => (pendingKey ? new Set(pendingKey.split(",")) : NONE), [pendingKey]);
  const failed = useMemo(() => (failedKey ? new Set(failedKey.split(",")) : NONE), [failedKey]);
  return useMemo(() => ({
    ...status, pending, inflight: flight.inflight, failed, awaitingCloud: flight.awaiting, cloudChoice, noteCloud, flush, reload,
  }), [status, pending, flight.inflight, failed, flight.awaiting, cloudChoice, noteCloud, flush, reload]);
}
