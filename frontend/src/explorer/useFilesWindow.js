// The Files window's own state (docs/dev/work_folders.md "The Files
// window"): whether it is shown, which folders are open, the row the keys
// are on, the row selected, and the few file rows it hands App to merge
// into its page lookups (explorer/knownPages.js mergeKnown). App calls it
// before it builds those lookups; what needs App's later values (the reads,
// the reveal, the first slot) is useFilesTree.js's. Its state lives here,
// not in App (docs/dev/frontend-refactor.md "New state").
//
// It reads and writes localStorage itself, as shared/lib/utils.js's
// usePersistedFlag does (the flag "1" or "0"), so the module keeps clear of
// the browser-only utils and its pure layoutKeeping is node-tested
// (tests/useFilesWindow.test.mjs).
import { useCallback, useEffect, useMemo, useState } from "react";

// Open folders kept per account and workspace, and file rows handed over.
const MAX_OPEN = 200;
const MAX_KNOWN = 20;
const NO_ROWS = [];

const HIDDEN_KEY = "gamma-files-hidden";
function readHidden() {
  try { return localStorage.getItem(HIDDEN_KEY) !== "0"; } catch { return true; }
}

const openKey = (scopeKey) => (scopeKey ? `gamma-files-open:${scopeKey}` : "");
function readOpen(key) {
  if (!key) return [];
  try {
    const ids = JSON.parse(localStorage.getItem(key) || "[]");
    return Array.isArray(ids) ? ids.filter((id) => typeof id === "string").slice(-MAX_OPEN) : [];
  } catch { return []; }
}
function writeOpen(key, ids) {
  if (!key) return;
  try { localStorage.setItem(key, JSON.stringify(ids)); } catch {}
}

// A page's saved dock layout (`saved`, {left, right, bottom}: ids per slot)
// with the window `id` where the current layout (`current`) has it, or
// nowhere when it has none: that window's place is the browser's, not the
// page's, so a page laid out before it existed does not move it.
export function layoutKeeping(saved, current, id) {
  if (!saved) return current;
  const sides = ["left", "right", "bottom"];
  const out = Object.fromEntries(sides.map((side) => [side, (saved[side] || []).filter((w) => w !== id)]));
  const side = sides.find((s) => current?.[s]?.includes(id));
  if (side) {
    const arr = out[side];
    arr.splice(Math.min(current[side].indexOf(id), arr.length), 0, id);
  }
  return { ...saved, ...out };
}

// A raw page row (the server's page_summary, as a directory's read gives
// it) can be handed to App; a row of App's own list (pageBlocksOf) is known
// there already.
const isRaw = (row) => !!row?.id && !!row.properties && !("_pageId" in row);

// {hidden, setHidden, toggle, expanded, toggleExpanded(id), expand(ids),
// collapseAll(), active, setActive(key), selected, select(row), known,
// remember(row), patch(id, fields), forget(ids)}. `hidden`: closed to begin
// with, kept per browser. `expanded`: the open folders' ids, kept per
// `scopeKey` (account and workspace) and to MAX_OPEN, those the listing's
// tree (`libTree`) lacks left out once it has folders. `active`: the key of
// the row the keys move from ("" none). `selected`: the id of the row
// selected, which select() also remembers. `known`: the file rows selected,
// opened or given a menu here (MAX_KNOWN, the newest kept), so a file's
// tab, menu and rules know it as a file at once, `patch` and `forget`
// following a rename or a delete of one; none while `enabled` is false.
// `active`, `selected` and `known` are the `scopeKey`'s: another drops them.
export function useFilesWindow({ scopeKey, libTree, enabled }) {
  const [hidden, setHidden] = useState(readHidden);
  useEffect(() => {
    try { localStorage.setItem(HIDDEN_KEY, hidden ? "1" : "0"); } catch {}
  }, [hidden]);
  const toggle = useCallback(() => setHidden((h) => !h), [setHidden]);
  const key = openKey(scopeKey);
  const [open, setOpen] = useState(() => ({ key, ids: readOpen(key) }));
  const [active, setActive] = useState("");
  const [selected, setSelected] = useState("");
  const [known, setKnown] = useState(NO_ROWS);
  // Another account or workspace (a sign-out too): its own open folders,
  // and nothing of the last one's rows, selection or keys.
  let held = open;
  if (open.key !== key) {
    held = { key, ids: readOpen(key) };
    setOpen(held);
    setActive("");
    setSelected("");
    setKnown(NO_ROWS);
  }
  const change = useCallback((next) => {
    setOpen((cur) => {
      const ids = next(cur.ids).slice(-MAX_OPEN);
      writeOpen(cur.key, ids);
      return { key: cur.key, ids };
    });
  }, []);
  const folders = libTree?.folders;
  const expanded = useMemo(() => (folders?.size ? held.ids.filter((id) => folders.has(id)) : held.ids), [held.ids, folders]);
  const toggleExpanded = useCallback((id) => {
    change((ids) => (ids.includes(id) ? ids.filter((x) => x !== id) : [...ids, id]));
  }, [change]);
  const expand = useCallback((more) => {
    change((ids) => {
      const add = more.filter((id) => id && !ids.includes(id));
      return add.length ? [...ids, ...add] : ids;
    });
  }, [change]);
  const collapseAll = useCallback(() => change(() => []), [change]);
  const remember = useCallback((row) => {
    if (!isRaw(row)) return;
    setKnown((rows) => [...rows.filter((r) => r.id !== row.id), row].slice(-MAX_KNOWN));
  }, []);
  const patch = useCallback((id, fields) => {
    setKnown((rows) => (rows.some((r) => r.id === id) ? rows.map((r) => (r.id === id ? { ...r, ...fields } : r)) : rows));
  }, []);
  const forget = useCallback((ids) => {
    const gone = new Set(ids);
    setKnown((rows) => (rows.some((r) => gone.has(r.id)) ? rows.filter((r) => !gone.has(r.id)) : rows));
  }, []);
  const select = useCallback((row) => {
    setSelected(row?.id || row?._pageId || "");
    remember(row);
  }, [remember]);
  return {
    hidden, setHidden, toggle,
    expanded, toggleExpanded, expand, collapseAll,
    active, setActive,
    selected, select,
    known: enabled ? known : NO_ROWS, remember, patch, forget,
  };
}
