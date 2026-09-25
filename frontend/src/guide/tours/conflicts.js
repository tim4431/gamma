import { T } from "../../shared/i18n/i18n.js";
// Offered the first time a clone shows a conflict's versions (the block
// chip's popover, the sync pill's list).
export default {
  id: "conflicts",
  version: 1,
  title: T("Resolving a conflict"),
  trigger: { event: "conflict.shown" },
  steps: [
    { id: "conflict-versions", anchor: "merge.versions", placement: "left", title: T("What each side changed, and what the merge kept") },
    { id: "conflict-apply", anchor: "merge.apply", placement: "left", title: T("Pick a version, then Apply; the next sync sends it"), next: T("Done") },
  ],
};
