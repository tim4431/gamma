import { T } from "../../shared/i18n/i18n.js";
// Offered after the first page made inside another (/page, or New page
// inside in a tab's or a library card's menu), as it opens with its crumbs
// over the title (docs/dev/home_library.md "Sub-pages"): the event comes
// before the page opens, so the offer is judged where it was made (the
// library, for a card's) and waits for the crumbs. The crumbs
// lead back up and take a dropped page; a tab's menu moves any page inside
// another, or back out to the library. From the Tours menu on a page inside
// none, the first step has the user make one. The tab step is desktop only
// (a phone has no tab strip) and `optional`.
export default {
  id: "sub-pages",
  version: 1,
  title: T("Pages inside pages"),
  requires: { editable: true },
  trigger: { event: "subpage.created" },
  offerAnchor: "page.crumbs",
  offerPlacement: "bottom",
  offer: { title: T("You made a page inside a page"), line: T("Keep a project's pages together, as deep as you like.") },
  steps: [
    { id: "sub-make", anchor: "dock.notes", placement: "left", creates: "page.crumbs",
      title: T("Type `/page` in a note"), body: T("It makes a page inside this one and opens it."),
      advanceOn: { event: "subpage.created" } },
    { id: "sub-crumbs", anchor: "page.crumbs", placement: "bottom",
      title: T("The crumbs lead back up"), body: T("Drop a page on one to move it inside."),
      scene: [{ click: "page.crumbs", at: [0.15, 0.5] }] },
    { id: "sub-move", anchor: "header.tabs", placement: "bottom", requires: { phone: false }, optional: true,
      title: T("Right-click a tab: **Move into page…**"), body: T("**Move to library** takes a page back out."),
      scene: [{ click: "header.tabs", at: [0.06, 0.5], button: "right" }] },
  ],
};
