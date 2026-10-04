import { T } from "../../shared/i18n/i18n.js";
// Offered once a page gets its first share link, while the Share popover
// shows the access it now has. Started from the Tours menu on a page that
// is not shared yet, the first step has the user choose who can open it,
// which creates the link. The steps follow the popover top to bottom: the
// link, who has access, general access — worded for what an
// anyone-with-the-link share exposes when it is one (the `shareAudience`
// fact, judged when the tour starts) — which also names Stop sharing.
export default {
  id: "sharing",
  version: 2,
  title: T("Sharing a page"),
  requires: { onPage: true },
  trigger: { event: "share.created" },
  offerAnchor: "share.link",
  offerPlacement: "left",
  offer: { title: T("Your page has a link now"), line: T("See who can open it and how to change that.") },
  steps: [
    { id: "share-create", anchor: "share.access", placement: "left", creates: "share.link",
      title: T("Choose who can open it"), advanceOn: { event: "share.created" } },
    { id: "share-link", anchor: "share.link", placement: "left", title: T("Copy the link to send it") },
    { id: "share-people", anchor: "share.people", placement: "left",
      title: T("Invite people by name"), body: T("Each gets View or Edit.") },
    { id: "share-access-anyone", anchor: "share.access", placement: "left", requires: { shareAudience: "anyone" },
      title: T("Anyone with the link, no sign-in needed"),
      body: T("Change that here; **Stop sharing** turns the link off."), next: T("Done") },
    { id: "share-access", anchor: "share.access", placement: "left", requires: { shareAudience: ["", "users", "list"] },
      title: T("Change who can open or edit it"),
      body: T("**Stop sharing** turns the link off."), next: T("Done") },
  ],
};
