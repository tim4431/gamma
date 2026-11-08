// Workspace roles as the UI words them (docs/dev/workspaces.md), read by
// Settings → Workspaces and by the account menu's switcher in App.jsx. A
// module of its own so the switcher does not pull the Settings panes into
// the startup bundle (docs/dev/frontend-refactor.md, "Lazy boundaries").
import { t } from "../shared/i18n/i18n.js";
import { zoteroLibraryLabel } from "../collaboration/syncLinks.js";

export const ROLE_OPTIONS = [["owner", t("Owner")], ["editor", t("Can edit")], ["viewer", t("View only")]];
export const ROLE_LABEL = { owner: t("owner"), editor: t("can edit"), viewer: t("view only") };

// The role a tab acts with in a workspace: its own, but a viewer's while
// the workspace is being deleted (`deleting`: the session's listing says
// so, or the server refused a change for it), since it takes no more
// changes (docs/dev/workspaces.md "Deleting a workspace").
export const actingRole = (w) => (w?.deleting ? "viewer" : w?.role || "");
// The workspace `w` marked as being deleted when it is `id` (the server
// refused a change for it), else `w` as it is.
export const markDeleting = (w, id) => (w && w.id === id && !w.deleting ? { ...w, deleting: true } : w);
// A share view's resolved share (App's shareInfo) once its workspace is
// being deleted: it edits no more, and every page it opens next says why
// (App's openSharedPage reads `canEdit` and `deleting`).
export const shareDeleting = (info) => (info && !info.deleting ? { ...info, canEdit: false, deleting: true } : info);

// One line under a switcher entry / workspace row: what kind it is and, for
// a shared one, your role.
export function workspaceMeta(w) {
  if (w.deleting) return t("being deleted");
  if (w.mirror_of) return t("clone of {mirror_of}", { mirror_of: w.mirror_of });
  if (w.zotero_of) return t("linked to Zotero · {library}", { library: zoteroLibraryLabel({ name: w.zotero_of }) });
  if (w.personal) return w.default ? t("personal · default") : t("personal");
  return w.access === "public" ? t("public · {role}", { role: ROLE_LABEL[w.role] || w.role }) : ROLE_LABEL[w.role] || w.role;
}
