// A workspace being deleted takes no more changes (docs/dev/workspaces.md
// "Deleting a workspace"): from the moment its delete's job runs, the
// server refuses them with a 423 naming it, on which the fetch wrapper and
// xhrUpload raise gamma-workspace-deleting (shared/lib/utils.js), and the
// session's listing marks it `deleting` for a tab that opens it meanwhile.
// The tab then acts as a viewer (workspaceRoles.js actingRole: the tree,
// the library and the chats read-only) and says why on a pill that stays,
// with a Reload, which brings editing back if the delete was stopped. A
// share view turns read-only the same way, and keeps it on its share
// (workspaceRoles.js shareDeleting), so the next page it opens stays
// read-only and says so too; one opened during the delete is told by the
// share itself (GET /api/share/{token}: deleting, no can_edit).
//
// App passes its setters; the returned `sayDeleting` is what App's
// applyWorkspace calls for a workspace the session lists as `deleting`.
import { useCallback, useEffect } from "react";
import { t } from "../shared/i18n/i18n.js";
import { getCurrentWorkspace } from "../shared/lib/utils";
import { markDeleting, shareDeleting } from "../settings/workspaceRoles.js";

export function useWorkspaceDeleting({ shareMode, setStatus, setWorkspace, setWorkspaces, setReadOnly, setShareInfo }) {
  const sayDeleting = useCallback(() => setStatus(t("This workspace is being deleted, so changes are no longer saved."),
    { label: t("Reload"), run: () => window.location.reload() }, { sticky: true }), [setStatus]);
  useEffect(() => {
    function onDeleting(e) {
      const id = e.detail?.workspace || "";
      // a refusal for another workspace (Settings acting on one) leaves this tab as it is
      if (!shareMode && id !== getCurrentWorkspace()) return;
      setWorkspace((w) => markDeleting(w, id));
      setWorkspaces((list) => list.map((w) => markDeleting(w, id)));
      if (shareMode) setShareInfo(shareDeleting);
      setReadOnly(true);
      sayDeleting();
    }
    window.addEventListener("gamma-workspace-deleting", onDeleting);
    return () => window.removeEventListener("gamma-workspace-deleting", onDeleting);
  }, [shareMode, sayDeleting, setWorkspace, setWorkspaces, setReadOnly, setShareInfo]);
  return sayDeleting;
}
