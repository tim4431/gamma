// The computers that keep the open page in a synced vault of the account's
// (vaultState.js readComputers), read from the page's own server (GET
// /api/pages/{id}/computer, docs/dev/computers.md) as the page opens, again
// on the account's `{t: "computer"}` nudge, which a computer announcing
// itself or going offline sends, and on each (re)join of the page's socket
// or round of its poll, which may have missed one (App's usePageCollab
// `onComputer`). Every window of the maker asks, the desktop app's or not:
// unlike useVault's route, an answer of 404 (a server older than the route)
// or an error is "none" for that read alone, never for the window. Each
// vault names the agent that runs the maker's Sends there (`vault.agent`):
// the comment fields' agent where this computer has none (App's
// commentAgent, vaultState.js keeperAgent).
import { useCallback, useEffect, useRef, useState } from "react";
import { API, apiJson } from "../shared/lib/utils";
import { readComputers } from "./vaultState.js";

const NONE = [];

// `{computers, refresh}`:
//   computers  the page's computers, `[]` for none and wherever it is not
//              asked (`enabled` false: signed out, a guest, a share's
//              visitor). The answer is kept with the page it is of, as
//              useVault's is: the next page shows none until its own comes.
//   refresh    asks again for the page open when it runs (stable)
export function usePageComputer(pageId, enabled) {
  const [held, setHeld] = useState({ pageId: "", computers: NONE });
  const asked = useRef(0);
  const open = useRef({ pageId, enabled });
  open.current = { pageId, enabled };
  const refresh = useCallback(async () => {
    const { pageId, enabled } = open.current;
    const n = ++asked.current;
    if (!enabled || !pageId) { setHeld({ pageId, computers: NONE }); return; }
    try {
      const answer = await apiJson(`${API}/pages/${encodeURIComponent(pageId)}/computer`);
      if (n === asked.current) setHeld({ pageId, computers: readComputers(answer) });
    } catch {
      if (n === asked.current) setHeld({ pageId, computers: NONE });
    }
  }, []);
  useEffect(() => { refresh(); }, [pageId, enabled, refresh]);
  return { computers: held.pageId === pageId ? held.computers : NONE, refresh };
}
