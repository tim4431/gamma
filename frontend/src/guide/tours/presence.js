import { T } from "../../shared/i18n/i18n.js";
// Offered the first time someone else comes onto the open page.
export default {
  id: "presence",
  version: 1,
  title: T("Working together"),
  trigger: { event: "peer.joined" },
  steps: [
    { id: "presence-who", anchor: "page.presence", placement: "bottom", title: T("Who else is here; click a face to jump to them") },
    { id: "presence-where", anchor: "notes.peers", optional: true, placement: "left", title: T("The block they are on") },
    { id: "presence-undo", anchor: "dock.notes", placement: "left", title: T("Their edits appear live; undo takes back only yours"), next: T("Done") },
  ],
};
