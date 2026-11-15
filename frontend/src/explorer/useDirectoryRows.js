// The open directories of a tree of a work folder (docs/dev/work_folders.md
// "The Files window", "The folder view"): the files of each, a page of rows
// at a time from GET /api/folders/{id}/pages, in the tree's sort. The rules
// of when to read are directoryRows.js's; this holds them in React, once
// for the Files window (by name) and once for the folder view (its sort).
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { API, apiJson } from "../shared/lib/utils.js";
import { NONE, PAGE_ROWS, findStep, merged, readLimit, readRows, wantedReads } from "./directoryRows.js";

// `{of(id), more(id), retry(id), find(id, pageId), patch(id, fields),
// drop(ids)}` over the directories of `wanted` (the open ones, W
// included). `of(id)` is a directory's entry ({rows, total, next, loading,
// failed, loaded, count, seq}, NONE for one never read). Each is read when
// it is first wanted, and again from the top (every row it holds, a
// request per MAX_ROWS) when its count in `counts` (libTree.workCounts, a
// new object per listing read) changed or the listing's `changes`
// (libTree.workChanges) say it was written since, never merely because the
// listing was read again; a directory closed keeps its rows. Nothing is
// read while `enabled` is false. `more` reads the next page, `retry` the
// first again after a failure, `find` pages on until `pageId` is held (at
// most MAX_ROWS rows: the reveal); `patch` and `drop` follow a rename or a
// delete of a page (by its id) before the next read. `sort` and `order`
// are the server's keys (name and asc by default), the rows read again
// from the top when they change; `version`, when given (App's listing
// tree), reads each open directory again whenever it changes, as the
// folder view's columns (the open comments) want.
export function useDirectoryRows({ wanted, counts, changes, enabled, sort = "name", order = "asc", version }) {
  const sortKey = `${sort}|${order}`;
  const [held, setHeld] = useState(() => new Map());
  const heldRef = useRef(held);
  heldRef.current = held;
  // per directory, the last read asked: an older answer is dropped
  const asked = useRef(new Map());
  // directory → the page the reveal looks for in it
  const [seeking, setSeeking] = useState(() => new Map());
  const update = useCallback((id, entry) => {
    const next = new Map(heldRef.current);
    next.set(id, entry(next.get(id) || NONE));
    heldRef.current = next;
    setHeld(next);
  }, []);
  const read = useCallback(async (id, offset, limit, replace, count) => {
    const n = (asked.current.get(id) || 0) + 1;
    asked.current.set(id, n);
    update(id, (e) => ({ ...e, loading: true, failed: false, count, sort: sortKey, version: version ?? null }));
    const page = async (at, size) => {
      const q = new URLSearchParams({ sort, order, offset: String(at), limit: String(size) });
      const d = await apiJson(`${API}/folders/${encodeURIComponent(id)}/pages?${q}`);
      return asked.current.get(id) === n ? d : null;
    };
    try {
      const d = await readRows(page, offset, limit);
      if (d) update(id, (e) => merged(e, { ...d, count }, replace));
    } catch {
      if (asked.current.get(id) === n) update(id, (e) => ({ ...e, loading: false, failed: true }));
    }
  }, [update, sort, order, sortKey, version]);
  const wantedKey = wanted.join(",");
  useEffect(() => {
    for (const id of wantedReads(wanted, heldRef.current, counts, changes, enabled, { sort: sortKey, version: version ?? null })) {
      read(id, 0, readLimit(heldRef.current.get(id)), true, counts?.[id] || 0);
    }
  }, [wantedKey, counts, changes, enabled, sortKey, version]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    if (!seeking.size || !enabled) return;
    const done = [];
    for (const [id, pageId] of seeking) {
      const entry = held.get(id);
      if (!entry || entry.loading || !entry.loaded) continue;
      const offset = findStep(entry, pageId);
      if (offset == null) done.push(id);
      else read(id, offset, PAGE_ROWS, false, entry.count);
    }
    if (done.length) {
      setSeeking((prev) => {
        const next = new Map(prev);
        for (const id of done) next.delete(id);
        return next;
      });
    }
  }, [held, seeking, enabled, read]);
  const of = useCallback((id) => held.get(id) || NONE, [held]);
  const more = useCallback((id) => {
    const entry = heldRef.current.get(id);
    if (entry?.loaded && entry.next != null && !entry.loading) read(id, entry.next, PAGE_ROWS, false, entry.count);
  }, [read]);
  const retry = useCallback((id) => {
    const entry = heldRef.current.get(id) || NONE;
    if (!entry.loading) read(id, 0, readLimit(entry), true, counts?.[id] || 0);
  }, [read, counts]);
  const find = useCallback((id, pageId) => {
    setSeeking((prev) => (prev.get(id) === pageId ? prev : new Map(prev).set(id, pageId)));
  }, []);
  const patch = useCallback((pageId, fields) => {
    const next = new Map(heldRef.current);
    let changed = false;
    for (const [id, entry] of next) {
      if (!entry.rows.some((r) => r.id === pageId)) continue;
      next.set(id, { ...entry, rows: entry.rows.map((r) => (r.id === pageId ? { ...r, ...fields } : r)) });
      changed = true;
    }
    if (changed) { heldRef.current = next; setHeld(next); }
  }, []);
  const drop = useCallback((ids) => {
    const gone = new Set(ids);
    const next = new Map(heldRef.current);
    let changed = false;
    for (const [id, entry] of next) {
      const rows = entry.rows.filter((r) => !gone.has(r.id));
      if (rows.length === entry.rows.length) continue;
      next.set(id, { ...entry, rows, total: Math.max(0, entry.total - (entry.rows.length - rows.length)) });
      changed = true;
    }
    if (changed) { heldRef.current = next; setHeld(next); }
  }, []);
  return useMemo(() => ({ of, more, retry, find, patch, drop }), [of, more, retry, find, patch, drop]);
}
