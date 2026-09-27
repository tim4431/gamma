import { T } from "../../shared/i18n/i18n.js";
// Offered once the account belongs to a shared workspace: someone added it,
// often without saying so.
export default {
  id: "workspaces",
  version: 1,
  title: T("Shared workspaces"),
  requires: { sharedWorkspace: true },
  trigger: {},
  offerAnchor: "header.account",
  offerPlacement: "bottom",
  offer: { title: T("You were added to a shared workspace"), line: T("See how to switch to it and what your role allows.") },
  steps: [
    { id: "ws-switch", anchor: "account.workspaces", placement: "left", title: T("Switch between your workspaces here") },
    { id: "ws-role", anchor: "account.card", placement: "left", title: T("The workspace you are in, and your role there"), next: T("Done") },
  ],
};
