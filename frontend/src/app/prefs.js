// The user preferences behind the Settings dialog as React state: one
// localStorage-backed state per entry of PREFS (app/prefDefs.js, where each
// preference declares its key, default, codec and scope), plus the sync of
// the account-scoped ones through the account's profile.
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { API, apiJson, usePersistedState } from "../shared/lib/utils";
import { ACCOUNT_PREFS, PREFS, profileOf, readProfile, setterName } from "./prefDefs.js";

export { CHAT_KINDS, FILE_LABEL_MODES, FREE_TRANSLATE_ENGINE, THEMES, TRANSLATE_LANGS, UI_SCALE, translateModelFor } from "./prefDefs.js";

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
// Changes are pushed once they settle, so a slider does not PUT per tick.
const PUSH_DELAY_MS = 1000;
// Focus flaps must not hammer the server: wake pulls at most this often.
const PULL_MIN_MS = 15000;

// Keeps the account-scoped preferences in step with the account's profile
// (/api/prefs/profile, one object keyed by preference name). `user` is the
// signed-in account, or "" for signed out, guest (one shared account) and
// share views — those keep working from localStorage alone.
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
//   "pushing" (its PUT in flight), "failed" (the last pull or push failed;
//   `error` says why);
// - Sets of preference names: `pending` (the value differs from the copy
//   the server last confirmed), `inflight` (sent in the PUT now on its
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
//   different copies and waits for the person's choice (Settings → Account);
// - `reload()`: sends a pending change, then pulls — after a sync from
//   Settings replaced entries on the server.
const NONE = new Set();
const perName = (values) => Object.fromEntries(ACCOUNT_PREFS.map((name) => [name, JSON.stringify(values[name])]));
// failed: {name: the value whose push failed}; awaitingAt: the server time of the last push
const IDLE = { inflight: NONE, failed: {}, awaiting: NONE, awaitingAt: "" };

export function useProfileSync(prefs, user) {
  const values = profileOf(prefs);
  const snap = JSON.stringify(values);
  const latest = useRef(null);
  latest.current = { prefs, snap, user };
  const syncedRef = useRef(null); // the server's profile as last seen/sent; null = not loaded
  const confirmedRef = useRef(null); // {name: JSON} the server confirmed holding; null = not loaded
  const timerRef = useRef(null);
  const pushIdRef = useRef(0);
  const sendingRef = useRef(0); // PUTs not answered yet
  const lastPushRef = useRef(Promise.resolve()); // the newest PUT, for flush()
  const [status, setStatus] = useState(() => ({ state: user ? "loading" : "signed-out", error: "" }));
  const [flight, setFlight] = useState(IDLE);
  const [cloudChoice, setCloudChoice] = useState(false);
  const mark = (state, error = "") => setStatus((was) => (was.state === state && was.error === error ? was : { state, error }));

  function apply(value) {
    const read = readProfile(value);
    const current = latest.current.prefs;
    for (const [name, v] of Object.entries(read)) {
      if (JSON.stringify(current[name]) !== JSON.stringify(v)) current[setterName(name)](v);
    }
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
    if (keepalive) { fetch(PROFILE_URL, { ...request, keepalive: true, credentials: "same-origin" }).catch(() => {}); return Promise.resolve(); }
    const sent = perName(JSON.parse(now));
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
      settle((f) => ({ ...f, failed: {}, awaiting: new Set([...f.awaiting, ...names]), awaitingAt: d?.updated_at || f.awaitingAt }));
      if (!timerRef.current && syncedRef.current === now) mark("loaded");
    }, (err) => {
      sendingRef.current -= 1;
      // A failed PUT leaves the change pending, so a later pull doesn't revert it.
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
    return apiJson(PROFILE_URL).then((d) => {
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
        if (JSON.stringify(read) !== syncedRef.current) { syncedRef.current = apply(d.value); settled = true; }
        confirmedRef.current = perName(read);
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

  // Unmounting (main.jsx remounts the app on a language change) must not
  // drop a change that was still settling.
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
