// A work folder's files found by name or path (GET /api/files), for quick
// open's Files group (library/QuickOpen.jsx). The server ranks them and
// says what a query names (backend/gamma/work_folders/file_search.py), so
// the client sends whatever is typed: only an empty query, the palette's
// command mode (">") or `enabled` false ask nothing. A request goes out after
// QUIET_MS without a keystroke and is aborted by the next query or a close.
import { useEffect, useRef, useState } from "react";
import { API, apiJson } from "../shared/lib/utils";

const QUIET_MS = 120;
// The server's `limit`: at most this many file rows under the pages.
export const MAX_FILES = 20;
const NONE = { q: "", rows: [], loading: false, failed: false };
const NO_ROWS = [];

// `{rows, loading, failed}`: the server's rows for `query`, in its order.
// While a longer query is asked the last rows (`held.q` the query they
// answered) stay, `loading`; any other query has none until its answer
// comes. A failure clears them (`failed`). A stale answer is dropped
// (`asked`).
export function useFileSearch({ query, enabled }) {
  const q = enabled && !query.startsWith(">") ? query.trim() : "";
  const [held, setHeld] = useState(NONE);
  const asked = useRef(0);
  useEffect(() => {
    const n = ++asked.current;
    if (!q) { setHeld(NONE); return undefined; }
    setHeld((h) => ({ ...h, loading: true, failed: false }));
    const ctl = new AbortController();
    const timer = setTimeout(async () => {
      try {
        const params = new URLSearchParams({ q, limit: String(MAX_FILES) });
        const d = await apiJson(`${API}/files?${params}`, { signal: ctl.signal });
        if (n === asked.current) setHeld({ q, rows: d.files || [], loading: false, failed: false });
      } catch {
        if (n === asked.current && !ctl.signal.aborted) setHeld({ q, rows: [], loading: false, failed: true });
      }
    }, QUIET_MS);
    return () => { clearTimeout(timer); ctl.abort(); };
  }, [q]);
  if (!q) return NONE;
  const current = held.q === q;
  const kept = current || (!!held.q && q.startsWith(held.q));
  return { rows: kept ? held.rows : NO_ROWS, loading: !current || held.loading, failed: current && held.failed };
}
