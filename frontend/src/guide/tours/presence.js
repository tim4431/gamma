import { T } from "../../shared/i18n/i18n.js";
// Offered the first time someone else — another account or a link visitor,
// never this account's own second tab — comes onto the open page.
export default {
  id: "presence",
  version: 1,
  title: T("Working together"),
  trigger: { event: "peer.joined" },
  offer: { title: T("Someone else is on this page"), line: T("See where they are and how edits merge.") },
  steps: [
    { id: "presence-who", anchor: "page.presence", placement: "bottom", title: T("Someone else is on this page. Click a face to jump to them") },
    { id: "presence-where", anchor: "notes.peers", optional: true, placement: "left", title: T("Their face marks the block they are on") },
    { id: "presence-undo", anchor: "dock.notes", placement: "left", title: T("Edits save live for everyone; {key:app.undo} undoes only yours"), next: T("Done") },
  ],
};
