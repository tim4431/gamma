// Workspace roles as the UI words them (docs/dev/workspaces.md), read by
// Settings → Workspaces and by the account menu's switcher in App.jsx. A
// module of its own so the switcher does not pull the Settings panes into
// the startup bundle (docs/dev/frontend-refactor.md, "Lazy boundaries").
import { t } from "../shared/i18n/i18n.js";

export const ROLE_OPTIONS = [["owner", t("Owner")], ["editor", t("Can edit")], ["viewer", t("View only")]];
export const ROLE_LABEL = { owner: t("owner"), editor: t("can edit"), viewer: t("view only") };

// One line under a switcher entry / workspace row: what kind it is and, for
// a shared one, your role.
export function workspaceMeta(w) {
  if (w.mirror_of) return t("clone of {mirror_of}", { mirror_of: w.mirror_of });
  if (w.personal) return w.default ? t("personal · default") : t("personal");
  return w.access === "public" ? t("public · {role}", { role: ROLE_LABEL[w.role] || w.role }) : ROLE_LABEL[w.role] || w.role;
}
