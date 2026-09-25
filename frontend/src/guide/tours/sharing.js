import { T } from "../../shared/i18n/i18n.js";
// Offered once a page gets its first share link, while the Share popover
// shows the access it now has. Started from the Tours menu on a page that
// is not shared yet, the first step has the user create the link.
export default {
  id: "sharing",
  version: 2,
  title: T("Sharing a page"),
  requires: { onPage: true },
  trigger: { event: "share.created" },
  offerAnchor: "share.link",
  offerPlacement: "left",
  steps: [
    { id: "share-create", anchor: "share.link", placement: "left", creates: "share.access",
      title: T("Create a link for this page"), advanceOn: { event: "share.created" } },
    { id: "share-access", anchor: "share.access", placement: "left", title: T("Who can open the link, and whether they can edit") },
    { id: "share-people", anchor: "share.people", placement: "left", title: T("Invite people by name, each with their own access") },
    { id: "share-link", anchor: "share.link", placement: "left", title: T("Copy the link, or stop sharing to turn it off"), next: T("Done") },
  ],
};
