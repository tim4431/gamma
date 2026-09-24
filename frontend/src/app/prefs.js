// The user preferences behind the Settings dialog as React state: one
// localStorage-backed state per entry of PREFS (app/prefDefs.js, where each
// preference declares its key, default, codec and scope), plus the sync of
// the account-scoped ones through the account's profile.
import { useEffect, useRef, useState } from "react";
import { API, apiJson, usePersistedState } from "../shared/lib/utils";
import { PREFS, profileOf, readProfile, setterName } from "./prefDefs.js";

export { CHAT_KINDS, FILE_LABEL_MODES, THEMES, TRANSLATE_LANGS, UI_SCALE } from "./prefDefs.js";

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
// profile is seeded from this browser. Local changes push after they settle
// (last write wins), and a pending push is flushed when the page is hidden.
// Until the first load succeeds nothing is pushed, so a session can't
// overwrite a copy it never saw.
//
// Returns where that stands, {state, error} (the Settings dialog's section
// tags read it, settings/syncState.js): "signed-out" (localStorage only),
// "loading" (the first pull), "loaded" (in step with the server), "pending"
// (a change settling), "pushing" (its PUT in flight), "failed" (the last
// pull or push failed; `error` says why).
export function useProfileSync(prefs, user) {
  const snap = JSON.stringify(profileOf(prefs));
  const latest = useRef(null);
  latest.current = { prefs, snap, user };
  const syncedRef = useRef(null); // the server's profile as last seen/sent; null = not loaded
  const timerRef = useRef(null);
  const [status, setStatus] = useState(() => ({ state: user ? "loading" : "signed-out", error: "" }));
  const mark = (state, error = "") => setStatus((was) => (was.state === state && was.error === error ? was : { state, error }));

  function apply(value) {
    const values = readProfile(value);
    const current = latest.current.prefs;
    for (const [name, v] of Object.entries(values)) {
      if (JSON.stringify(current[name]) !== JSON.stringify(v)) current[setterName(name)](v);
    }
    return JSON.stringify(values);
  }

  function push(keepalive = false) {
    clearTimeout(timerRef.current);
    timerRef.current = null;
    const { snap: now, user: u } = latest.current;
    if (!u || syncedRef.current === null) return;
    if (now === syncedRef.current) { mark("loaded"); return; } // changed back before it went out
    const before = syncedRef.current;
    syncedRef.current = now;
    const request = {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: `{"value":${now}}`,
    };
    if (keepalive) { fetch(PROFILE_URL, { ...request, keepalive: true, credentials: "same-origin" }).catch(() => {}); return; }
    mark("pushing");
    apiJson(PROFILE_URL, request).then(() => {
      if (latest.current.user === u && !timerRef.current && syncedRef.current === now) mark("loaded");
    }, (err) => {
      // A failed PUT leaves the change pending, so a later pull doesn't revert it.
      if (syncedRef.current === now) syncedRef.current = before;
      if (latest.current.user === u && !timerRef.current) mark("failed", err?.message || "");
    });
  }

  function schedule() {
    if (syncedRef.current === null || latest.current.snap === syncedRef.current) return;
    clearTimeout(timerRef.current);
    timerRef.current = setTimeout(push, PUSH_DELAY_MS);
    mark("pending");
  }

  function pull(u) {
    return apiJson(PROFILE_URL).then((d) => {
      // A local change waiting to go out is newer than what the server holds.
      if (latest.current.user !== u || timerRef.current) return;
      if (!d.updated_at) {
        if (syncedRef.current === null) syncedRef.current = ""; // never saved: seed from here
      } else if (JSON.stringify(readProfile(d.value)) !== syncedRef.current) {
        syncedRef.current = apply(d.value);
      }
      mark("loaded");
      schedule();
    }).catch((err) => {
      if (latest.current.user === u && !timerRef.current) mark("failed", err?.message || "");
    });
  }

  useEffect(() => {
    syncedRef.current = null;
    clearTimeout(timerRef.current);
    timerRef.current = null;
    mark(user ? "loading" : "signed-out");
    if (user) pull(user);
  }, [user]);

  useEffect(() => { schedule(); }, [snap, user]);

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

  return status;
}
