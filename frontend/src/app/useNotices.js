// The red dot's feed: polls GET /api/notices slowly (and on window focus,
// which is when someone comes back to a tab), and records the ack when a
// Settings pane a notice points at is visited. One instance, in App.jsx;
// SettingsDialog gets the value as a prop. Off in share views and for
// guests (the server answers an empty list for them anyway).
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { API, apiJson } from "../shared/lib/utils";
import { summarizeNotices } from "./notices";

const POLL_MS = 5 * 60 * 1000;

export function useNotices(enabled) {
  const [list, setList] = useState([]);
  const listRef = useRef(list);
  listRef.current = list;
  const refresh = useCallback(async () => {
    if (!enabled) return;
    try {
      const data = await apiJson(`${API}/notices`);
      setList(Array.isArray(data.notices) ? data.notices : []);
    } catch {
      // stays as it was: the dot is a hint, never an error surface
    }
  }, [enabled]);
  useEffect(() => {
    if (!enabled) { setList([]); return undefined; }
    refresh();
    const timer = setInterval(refresh, POLL_MS);
    const onFocus = () => { if (document.visibilityState !== "hidden") refresh(); };
    window.addEventListener("focus", onFocus);
    return () => { clearInterval(timer); window.removeEventListener("focus", onFocus); };
  }, [enabled, refresh]);
  // Visiting a pane resolves every notice pointing at it: the dot goes
  // right away, the server remembers the fingerprints so other tabs and
  // browsers agree on the next poll.
  const markSeen = useCallback((pane) => {
    const due = listRef.current.filter((n) => n.pane === pane);
    if (!due.length) return;
    setList((current) => current.filter((n) => n.pane !== pane));
    for (const notice of due) {
      apiJson(`${API}/notices/${encodeURIComponent(notice.id)}/seen`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ fingerprint: notice.fingerprint }),
      }).catch(() => {});
    }
  }, []);
  const summary = useMemo(() => summarizeNotices(list), [list]);
  return useMemo(() => ({ list, ...summary, markSeen, refresh }), [list, summary, markSeen, refresh]);
}
