// The open page's standing with this computer (vaultState.js), read in the
// desktop app from its own server (GET /api/folder-links/page/{id}) as the
// page opens and again on its socket's `{t: "vault"}`, which a vault's round
// sends after it touched the page, and on each (re)join of the socket, which
// may have missed one (App's usePageCollab `onVault`). Any other
// server answers that route 404: it is not asked again in this window.
import { useCallback, useEffect, useRef, useState } from "react";
import { API, apiJson, getCurrentWorkspace } from "../shared/lib/utils";
import { IS_DESKTOP } from "../shared/lib/connector.js";
import { readVault } from "./vaultState.js";

let elsewhere = false;

// `{state, refresh}`: `state` null for a page of no vault reading no file
// here, and wherever the route does not answer (outside the desktop app, a
// share's visitor: `enabled` false). The answer is kept with the page it is
// of: until the next page's comes, that page has none, so it never shows the
// last page's chip, banner or notes lock; the same page asked again (a
// `vault` message) keeps its own meanwhile.
export function useVault(pageId, enabled) {
  const [held, setHeld] = useState({ pageId: "", state: null });
  const asked = useRef(0);
  const refresh = useCallback(async () => {
    const n = ++asked.current;
    if (!IS_DESKTOP || elsewhere || !enabled || !pageId) { setHeld({ pageId, state: null }); return; }
    try {
      const answer = await apiJson(`${API}/folder-links/page/${encodeURIComponent(pageId)}`);
      if (n === asked.current) setHeld({ pageId, state: readVault(answer) });
    } catch (err) {
      if (err?.status === 404) elsewhere = true;
      if (n === asked.current) setHeld({ pageId, state: null });
    }
  }, [pageId, enabled]);
  useEffect(() => { refresh(); }, [refresh]);
  return { state: held.pageId === pageId ? held.state : null, refresh };
}

// A message to the desktop app's shell, which its page preload passes on
// (desktop/preload.js): `keep-folder-on-disk {folder}` (sync a folder with a
// directory), `follow-file-on-disk {page}` (push from a file), `reveal-on-disk
// {page}`, `relocate-file {page}`, `carry-to-server {page}` (an opened PDF to
// another server's library), each for the workspace open here. The first two
// keep their names from before synced vaults, the only ones a desktop app
// released before them passes on: it may show a page of a server updated
// since. The shell answers the page nothing: what it does reaches the page
// through the server.
export function askShell(type, fields) {
  window.postMessage({ source: "gamma-app", type, ws: getCurrentWorkspace(), ...fields }, window.location.origin);
}
