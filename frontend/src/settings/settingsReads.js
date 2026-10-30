// What the Settings dialog reads from the server (docs/dev/settings.md "What
// a pane reads"). One store per opening of the dialog holds an entry per
// path, shared by every section that reads it: the Server pane's five
// sections over /api/admin/settings make one request, and what one of them
// saves the others show. A pane's reads are named in PANE_READS, so they go
// out together, from the click on its sidebar button or, ahead of it, from
// the pointer or the focus reaching the button; the dialog keeps the pane
// before on screen until they are in (SettingsDialog `navigate`). A second
// visit shows what the first read at once and reads it again in place.
import React from "react";
import { API, apiJson } from "../shared/lib/utils";
import { LINK_BASES } from "../collaboration/syncLinks.js";

// An answer this recent is current: a section mounting this soon after its
// pane's reads went out (the click, the hover) takes it rather than asking
// again.
const FRESH_MS = 3000;
const NONE = Object.freeze({ data: undefined, error: "", at: 0 });

// The reads each pane makes as it opens, by the paths its sections pass to
// useSettingsRead, in the order they go out: what the top of the pane shows
// first, since the browser keeps six requests in flight to a server. A read
// may name the reads its answer leads to (`then`), which go out as soon as
// it lands: Backups lists each of the account's workspaces' snapshots. One
// marked `later` goes out with the others but is not waited for: what is
// far down the pane (the server log) or shows only in a dialog (the
// account pickers). `who`: {admin, signedIn}.
export const LOG_START = "/admin/logs?after=0";
export const PANE_READS = {
  account: ({ admin, signedIn }) => [admin && "/admin/users", admin && "/admin/settings",
    signedIn && "/auth/cloud/status", signedIn && LINK_BASES.mirror, signedIn && { path: "/zotero/account", later: true }],
  users: () => ["/admin/users", "/admin/settings", "/admin/workspaces", { path: "/admin/invitations", later: true }],
  workspaces: ({ signedIn }) => ["/workspaces/mine", signedIn && LINK_BASES.mirror, signedIn && LINK_BASES.zotero, { path: "/accounts", later: true },
    { path: "/server-config", later: true }],
  backups: ({ admin }) => [{ path: "/workspaces/mine", then: (d) => (d?.workspaces || []).map((w) => backupsOf(w.id)) },
    "/backup-tasks", admin && "/admin/offsite"],
  server: () => ["/admin/server-info", "/admin/settings", "/admin/join-requests", "/admin/ai-providers", "/admin/workspaces",
    "/admin/backups", { path: "/accounts", later: true }, { path: LOG_START, later: true }],
  translation: () => ["/translate/engines", "/translations/stats"],
  tools: () => ["/ai/search-services"],
  maintenance: () => ["/quota", "/metadata/status"],
};
export const backupsOf = (id) => `/workspaces/${encodeURIComponent(id)}/backups`;

// A pane's reads as [{path, then, later}].
function readsOf(pane, who) {
  return (PANE_READS[pane]?.(who) || []).filter(Boolean).map((read) => (typeof read === "string" ? { path: read } : read));
}

export function createSettingsReads() {
  const entries = new Map(); // path → {data, error, at}
  const pending = new Map(); // path → the read on its way
  const writes = new Map();  // path → how many times set() has written it
  const listeners = new Map(); // path → Set of callbacks
  let version = 0;
  const all = new Set(); // callbacks for any change (useSettingsReads)
  const put = (path, entry) => {
    entries.set(path, entry);
    version += 1;
    for (const fn of listeners.get(path) || []) fn();
    for (const fn of all) fn();
  };
  const store = {
    get: (path) => entries.get(path) || NONE,
    // whether the path has an answer, data or an error
    has: (path) => { const e = entries.get(path); return !!e && (e.data !== undefined || !!e.error); },
    version: () => version,
    // Reads `path`, unless a read of it is on its way or an answer is
    // fresh; `force` reads again anyway (after a change the read on its way
    // may predate). Only the newest read sent lands, and not after set()
    // has written the path since it was sent: it would bring an older copy
    // than the one shown. A failed one keeps the data shown and says why.
    read(path, { force = false } = {}) {
      if (!force && pending.has(path)) return pending.get(path);
      const now = store.get(path);
      if (!force && now.at && Date.now() - now.at < FRESH_MS) {
        return now.error ? Promise.reject(new Error(now.error)) : Promise.resolve(now.data);
      }
      const written = writes.get(path) || 0;
      // whether this read is still the newest, as it lands; it is then no longer on its way
      const settle = () => {
        if (pending.get(path) !== read) return false;
        pending.delete(path);
        return (writes.get(path) || 0) === written;
      };
      const read = apiJson(`${API}${path}`).then((data) => {
        if (!settle()) return store.get(path).data;
        put(path, { data, error: "", at: Date.now() });
        return data;
      }, (err) => {
        if (settle()) put(path, { data: store.get(path).data, error: err?.message || String(err), at: Date.now() });
        throw err;
      });
      read.catch(() => {}); // its caller sees a failure; the entry says it to everyone else
      pending.set(path, read);
      return read;
    },
    // What a write answered with (or the change it makes), shown at once.
    set(path, data) {
      writes.set(path, (writes.get(path) || 0) + 1);
      put(path, { data, error: "", at: Date.now() });
    },
    subscribe(path, fn) {
      if (!listeners.has(path)) listeners.set(path, new Set());
      listeners.get(path).add(fn);
      return () => listeners.get(path)?.delete(fn);
    },
    subscribeAll(fn) { all.add(fn); return () => all.delete(fn); },
    // A pane's reads, each with what its answer leads to; resolves once all
    // but the `later` ones have answered. `missing` reads only what has no
    // answer yet (a prefetch), else every read not fresh is read again (a
    // visit).
    pane(pane, who, { missing = false } = {}) {
      const go = ({ path, then }) => {
        const read = missing && store.has(path) ? Promise.resolve(store.get(path).data) : store.read(path);
        return read.then((data) => (then ? Promise.allSettled(then(data).map((next) => go({ path: next }))) : null), () => null);
      };
      const all = readsOf(pane, who).map((read) => ({ read, done: go(read) }));
      return Promise.allSettled(all.filter(({ read }) => !read.later).map(({ done }) => done));
    },
    // Whether every read of the pane but the `later` ones (and what they
    // lead to) has an answer.
    ready(pane, who) {
      return readsOf(pane, who).filter((read) => !read.later).every(({ path, then }) => store.has(path)
        && (!then || then(store.get(path).data).every((next) => store.has(next))));
    },
  };
  return store;
}

export const SettingsReadsContext = React.createContext(null);
// A reader used outside the dialog (the share popover's account directory)
// reads through a store of its own, as it would without one: once as it
// mounts, kept no longer than it stays.
export function useSettingsStore() {
  const shared = React.useContext(SettingsReadsContext);
  const own = React.useRef(null);
  if (shared) return shared;
  own.current ||= createSettingsReads();
  return own.current;
}

// One path's answer for a section: {data (null until read), error, set,
// reload}. Reading it on mount takes a fresh answer or one on its way
// instead of asking again; `poll` reads it again every so many ms while
// mounted. A null path reads nothing.
export function useSettingsRead(path, { poll = 0 } = {}) {
  const store = useSettingsStore();
  const subscribe = React.useCallback((fn) => (path ? store.subscribe(path, fn) : () => {}), [store, path]);
  const entry = React.useSyncExternalStore(subscribe, () => (path ? store.get(path) : NONE));
  React.useEffect(() => {
    if (!path) return undefined;
    store.read(path).catch(() => {});
    if (!poll) return undefined;
    const timer = setInterval(() => store.read(path, { force: true }).catch(() => {}), poll);
    return () => clearInterval(timer);
  }, [store, path, poll]);
  const set = React.useCallback((next) => {
    if (path) store.set(path, typeof next === "function" ? next(store.get(path).data ?? null) : next);
  }, [store, path]);
  const reload = React.useCallback(() => (path ? store.read(path, { force: true }) : Promise.resolve(null)), [store, path]);
  return { data: entry.data ?? null, error: entry.error, set, reload };
}

// The store itself, for a section whose paths are not known in advance (a
// list per workspace); it renders again on any change.
export function useSettingsReads() {
  const store = useSettingsStore();
  React.useSyncExternalStore(store.subscribeAll, store.version);
  return store;
}
