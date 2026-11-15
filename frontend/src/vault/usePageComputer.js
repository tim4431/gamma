// The open page's computers of the account's (vaultState.js readComputers):
// those that keep it in a synced vault and those with an agent on for it,
// read from the page's own server (GET /api/pages/{id}/computer,
// docs/dev/computers.md) as the page opens; where no page is open (a
// folder's or the library's chat), the account's computers (GET
// /api/computers), without another account's lent ones, which work only on
// a page of a work folder their computer keeps. Read again on the account's `{t:
// "computer"}` nudge, which a computer announcing itself or going offline
// sends, and on each (re)join of the page's socket or round of its poll,
// which may have missed one (App's usePageCollab `onComputer`). Every
// window of the account asks, the desktop app's or not: unlike useVault's
// route, an answer of 404 (a server older than the route) or an error is
// "none" for that read alone, never for the window. Their agents are the
// chat's and the comment fields' there (chat/agents.js farAgents and
// commentAgents, App's chatAgentList and commentAgentList), a vault that
// keeps a work folder (`vault.work`) naming where they work.
import { useCallback, useEffect, useRef, useState } from "react";
import { API, apiJson } from "../shared/lib/utils";
import { readAccountComputers, readComputers } from "./vaultState.js";

const NONE = [];

// `{computers, refresh}`:
//   computers  the page's computers (the account's with no page open: none
//              keeps a vault), `[]` for none and wherever it is not
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
    if (!enabled) { setHeld({ pageId, computers: NONE }); return; }
    try {
      const computers = pageId ? readComputers(await apiJson(`${API}/pages/${encodeURIComponent(pageId)}/computer`))
        : readAccountComputers(await apiJson(`${API}/computers`));
      if (n === asked.current) setHeld({ pageId, computers });
    } catch {
      if (n === asked.current) setHeld({ pageId, computers: NONE });
    }
  }, []);
  useEffect(() => { refresh(); }, [pageId, enabled, refresh]);
  return { computers: held.pageId === pageId ? held.computers : NONE, refresh };
}
