// The pages the library listing does not carry that the app still shows
// (docs/dev/work_folders.md "Pages the listing does not carry"): a work
// folder's file open in a tab, viewed lately or open now.
// Their rows come from POST /api/pages/summaries, as the listing's
// do, and App merges them into its lookups (explorer/knownPages.js
// mergeKnown, where the listing's row wins), so a file's tab keeps its kind
// icon, Recently viewed shows it and the chat names it.
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { API, apiJson } from "../shared/lib/utils";

// What one read asks for at most (the route's cap).
const MAX_IDS = 500;

// `{rows, patch(id, fields), drop(ids)}`: the summaries of the pages of
// `ids` that `known(id)` (the listing and the folder's rows) lacks, each
// read once and again after `version` changes (App's listing read again),
// none while `enabled` is false. A row read before stays until its page is
// read again; a page the server leaves out (in Recently deleted, or gone)
// goes, and is not asked for again until the next version. `patch` and
// `drop` follow a rename or a delete made here.
export function useSummaries({ ids, known, version, enabled }) {
  const [held, setHeld] = useState(() => new Map());
  // the ids asked for since `version` last changed
  const asked = useRef({ version, ids: new Set() });
  if (asked.current.version !== version) asked.current = { version, ids: new Set() };
  const wanted = enabled ? [...new Set(ids.filter(Boolean))] : [];
  const wantedKey = wanted.join(",");
  const missing = wanted.filter((id) => !known(id) && !asked.current.ids.has(id)).slice(0, MAX_IDS);
  const missingKey = missing.join(",");
  useEffect(() => {
    if (!missing.length) return undefined;
    const round = asked.current;
    for (const id of missing) round.ids.add(id);
    apiJson(`${API}/pages/summaries`, {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ ids: missing }),
    }).then((d) => {
      // an answer from before the listing was read again is read again itself
      if (round !== asked.current) return;
      const rows = new Map((d.pages || []).map((r) => [r.id, { ...r, _parentPage: r.parent || "" }]));
      setHeld((prev) => {
        const next = new Map(prev);
        for (const id of missing) { if (rows.has(id)) next.set(id, rows.get(id)); else next.delete(id); }
        return next;
      });
    }).catch(() => {}); // asked again with the next version, as a page left out is
    return undefined;
  }, [missingKey, version]); // eslint-disable-line react-hooks/exhaustive-deps
  const patch = useCallback((id, fields) => {
    setHeld((prev) => (prev.has(id) ? new Map(prev).set(id, { ...prev.get(id), ...fields }) : prev));
  }, []);
  const drop = useCallback((gone) => {
    setHeld((prev) => {
      if (!gone.some((id) => prev.has(id))) return prev;
      const next = new Map(prev);
      for (const id of gone) next.delete(id);
      return next;
    });
  }, []);
  const rows = useMemo(() => wanted.filter((id) => held.has(id)).map((id) => held.get(id)),
    [held, wantedKey]); // eslint-disable-line react-hooks/exhaustive-deps
  return { rows, patch, drop };
}
