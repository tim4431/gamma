// A work folder's directory as the explorer lists it (docs/dev/work_folders.md
// "The folder's list view"): its files a page of rows at a time, from GET
// /api/folders/{id}/pages, sorted on the server. The library listing leaves
// a work folder's files out (unless they are in the library), so these rows
// are the second source of pages App's lookups merge in
// (explorer/knownPages.js), and the menus, the selection and the tabs find
// them there.
import { useCallback, useEffect, useRef, useState } from "react";
import { API, apiJson, usePersistedState } from "../shared/lib/utils";

// The sorts the list offers, the server's keys; Modified lists the newest
// first, the others from A.
export const WORK_SORTS = ["name", "format", "size", "modified"];
const orderOf = (sort) => (sort === "modified" ? "desc" : "asc");
const PAGE_ROWS = 200;
const MAX_ROWS = 500;
const NONE = { key: "", rows: [], total: 0, next: null, loading: false, failed: false, still: true };

// The list's sort, kept in this browser (`workSort`) and never taken from
// the library's own sort of its folders: {sort, order, setSort}.
export function useWorkSort() {
  const [stored, setSort] = usePersistedState("gamma-work-sort", "name");
  const sort = WORK_SORTS.includes(stored) ? stored : "name";
  return { sort, order: orderOf(sort), setSort };
}

// `{rows, total, next, loading, failed, still, more(), patch(id, fields),
// drop(ids)}` for the directory `folderId` ("" for none: no read), each row
// shaped as a listing row (`_parentPage`), in the server's order; `still`
// while the rows are as a read left them (the list moves nothing for a
// load: docs/dev/ui-design.md "Lists that change"). It reads again from
// the top when the folder, the sort or the order change, and when `version`
// does (App's listing, read again on coming home, on wake and after an Add
// or a Remove): a read again keeps as many rows as were loaded, so the
// list stays where it was. `more()` reads the next page of rows; `patch`
// and `drop` follow a rename or a delete made here before the next read.
export function useFolderRows({ folderId, sort, order, version }) {
  const [held, setHeld] = useState(NONE);
  const heldRef = useRef(held);
  heldRef.current = held;
  const asked = useRef(0);
  const key = folderId ? `${folderId}|${sort}|${order}` : "";
  const read = useCallback(async (offset, limit, replace) => {
    const n = ++asked.current;
    setHeld((h) => ({ ...(replace && h.key !== key ? { ...NONE } : h), key, loading: true, failed: false }));
    const q = new URLSearchParams({ sort, order, offset: String(offset), limit: String(limit) });
    try {
      const d = await apiJson(`${API}/folders/${encodeURIComponent(folderId)}/pages?${q}`);
      if (n !== asked.current) return;
      const rows = (d.pages || []).map((r) => ({ ...r, _parentPage: r.parent || "" }));
      setHeld((h) => ({
        key, rows: replace ? rows : [...h.rows, ...rows.filter((r) => !h.rows.some((x) => x.id === r.id))],
        total: d.total ?? rows.length, next: d.next ?? null, loading: false, failed: false, still: true,
      }));
    } catch {
      if (n === asked.current) setHeld((h) => ({ ...h, key, loading: false, failed: true }));
    }
  }, [key, folderId, sort, order]);
  useEffect(() => {
    if (!key) { asked.current++; setHeld(NONE); return; }
    const kept = heldRef.current.key === key ? heldRef.current.rows.length : 0;
    read(0, Math.min(MAX_ROWS, Math.max(PAGE_ROWS, kept)), true);
  }, [key, version]); // eslint-disable-line react-hooks/exhaustive-deps
  const more = useCallback(() => {
    const h = heldRef.current;
    if (h.key && h.next != null && !h.loading) read(h.next, PAGE_ROWS, false);
  }, [read]);
  const patch = useCallback((id, fields) => {
    setHeld((h) => (h.rows.some((r) => r.id === id) ? { ...h, still: false, rows: h.rows.map((r) => (r.id === id ? { ...r, ...fields } : r)) } : h));
  }, []);
  const drop = useCallback((ids) => {
    const gone = new Set(ids);
    setHeld((h) => {
      const rows = h.rows.filter((r) => !gone.has(r.id));
      return rows.length === h.rows.length ? h : { ...h, rows, still: false, total: Math.max(0, h.total - (h.rows.length - rows.length)) };
    });
  }, []);
  const shown = held.key === key ? held : NONE;
  return { rows: shown.rows, total: shown.total, next: shown.next, loading: shown.loading, failed: shown.failed, still: !!shown.still,
    more, patch, drop };
}
