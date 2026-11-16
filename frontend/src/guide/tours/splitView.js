import { T } from "../../shared/i18n/i18n.js";
// Offered after the first split: two pages side by side, each pane with its
// own tabs (docs/dev/tab_groups.md). Pages open in the ringed pane, and a
// click on the other makes it that one; a tab goes across by its menu or a
// drag, and Join tab groups makes one pane again. Desktop only: a phone
// draws one pane. From the Tours menu with one pane, the first step has the
// user split; the whole-pane steps tuck their card into the pane's corner.
export default {
  id: "split-view",
  version: 1,
  title: T("Two pages side by side"),
  requires: { phone: false, onPage: true },
  trigger: { event: "tabs.split" },
  offerAnchor: "tabs.live",
  offerPlacement: "inside",
  offer: { title: T("Two pages, side by side"), line: T("Each side keeps its own tabs.") },
  steps: [
    { id: "split-make", anchor: "header.tabs", placement: "bottom", creates: "tabs.live",
      title: T("Right-click a tab, then **Split right**"), body: T("Or drag a tab to the right edge of the page."),
      scene: [{ click: "header.tabs", at: [0.06, 0.5], button: "right" }], advanceOn: { event: "tabs.split" } },
    { id: "split-live", anchor: "tabs.live", placement: "inside",
      title: T("Pages open on the ringed side"), body: T("Click the other side to work there."),
      scene: [{ click: "tabs.other", at: [0.5, 0.4] }] },
    { id: "split-move", anchor: "header.tabs", placement: "bottom",
      title: T("Right-click a tab: **Move to the other pane**"), body: T("Or drag it across."),
      scene: [{ click: "header.tabs", at: [0.1, 0.5], button: "right" }] },
    { id: "split-join", anchor: "header.tabs", placement: "bottom",
      title: T("**Join tab groups** makes one pane again"), body: T("Closing a side's last tab closes that side too.") },
  ],
};
