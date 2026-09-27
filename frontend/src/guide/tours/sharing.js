import { T } from "../../shared/i18n/i18n.js";
// Offered once a page gets its first share link, while the Share popover
// shows the access it now has. Started from the Tours menu on a page that
// is not shared yet, the first step has the user choose who can open it,
// which creates the link. The steps follow the popover top to bottom: the
// link, who has access, general access (worded for what an
// anyone-with-the-link share exposes when it is one — the `shareAudience`
// fact, judged when the tour starts), stop sharing.
export default {
  id: "sharing",
  version: 2,
  title: T("Sharing a page"),
  requires: { onPage: true },
  trigger: { event: "share.created" },
  offerAnchor: "share.link",
  offerPlacement: "left",
  offer: { title: T("Your page has a link now"), line: T("Anyone who gets it can read the page. See how to limit that.") },
  steps: [
    { id: "share-create", anchor: "share.access", placement: "left", creates: "share.link",
      title: T("Choose who can open it"), advanceOn: { event: "share.created" } },
    { id: "share-link", anchor: "share.link", placement: "left", title: T("Copy the link to send it") },
    { id: "share-people", anchor: "share.people", placement: "left", title: T("Invite people by name; each gets their own View or Edit") },
    { id: "share-access-anyone", anchor: "share.access", placement: "left", requires: { shareAudience: "anyone" },
      title: T("Anyone with the link can read this page, no sign-in needed. Change who, or allow editing.") },
    { id: "share-access", anchor: "share.access", placement: "left", requires: { shareAudience: ["", "users", "list"] },
      title: T("Who can open the link, and whether they can edit") },
    { id: "share-stop", anchor: "share.stop", placement: "left", title: T("Stop sharing turns the link off for everyone"), next: T("Done") },
  ],
};
