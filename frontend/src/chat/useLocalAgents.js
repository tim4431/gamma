// The agents this computer runs, as its server lists them (GET
// /api/local-agents: {here, computer, agents, dirs}; `here` false on any
// server but the desktop app's own, which lists none), read once for the
// window and shared by every reader: the chat's picker and the comment
// fields (through chat/agents.js) and Settings → AI → Agents. Read again
// when the window comes back (Settings in another window, or the desktop
// app's own question about turning one on, may have changed them), and by
// readLocalAgents() after a change made here.
import { useEffect, useSyncExternalStore } from "react";
import { API, apiJson } from "../shared/lib/utils";

let held = null;        // the last answer, null until one came
let onItsWay = null;    // the read on its way
let failed = "";        // why the last read failed, "" once one landed
const heard = new Set();
const tell = () => { for (const fn of heard) fn(); };

// Reads the listing (`fresh`: each agent asked anew, its version and
// whether it is signed in; the server does that at most every ten
// seconds), unless a read is on its way; resolves to the answer, the one
// held when it fails.
export function readLocalAgents({ fresh = false } = {}) {
  if (onItsWay && !fresh) return onItsWay;
  const read = apiJson(`${API}/local-agents${fresh ? "?fresh=1" : ""}`).then((answer) => {
    if (onItsWay === read) { held = answer; failed = ""; tell(); }
    return answer;
  }, (err) => {
    if (onItsWay === read) { failed = err.message || String(err); tell(); }
    return held;
  }).finally(() => { if (onItsWay === read) onItsWay = null; });
  onItsWay = read;
  return read;
}

const subscribe = (fn) => { heard.add(fn); return () => heard.delete(fn); };
const snapshot = () => held;
const failure = () => failed;

// The listing for a signed-in account's window (`enabled`; null while
// unread and wherever it is not asked: signed out, a guest, a share), and
// with `withError` {listing, error}, `error` why the last read failed.
export function useLocalAgents(enabled, { withError = false } = {}) {
  const listing = useSyncExternalStore(subscribe, snapshot);
  const error = useSyncExternalStore(subscribe, failure);
  useEffect(() => {
    if (!enabled) return undefined;
    if (!held) readLocalAgents();
    const again = () => { if (document.visibilityState !== "hidden") readLocalAgents(); };
    window.addEventListener("focus", again);
    return () => window.removeEventListener("focus", again);
  }, [enabled]);
  const shown = enabled ? listing : null;
  return withError ? { listing: shown, error: enabled ? error : "" } : shown;
}
