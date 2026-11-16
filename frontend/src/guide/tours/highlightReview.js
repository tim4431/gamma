import { T } from "../../shared/i18n/i18n.js";
// Offered while the open PDF has highlights left to check after it was
// replaced by a new version (docs/dev/highlight_relocation.md "In the web
// app"): those matched roughly are drawn dashed, those whose text is gone
// hollow. The Document menu's Review opens the first one's card, whose
// buttons settle it and whose arrows walk the rest; Accept all takes every
// rough match where it is. A card already up passes the first step.
export default {
  id: "highlight-review",
  version: 1,
  title: T("Checking moved highlights"),
  requires: { highlightsToReview: true },
  trigger: {},
  offerAnchor: "page.document",
  offer: { title: T("Some highlights need a look"), line: T("Dashed ones matched roughly in the new PDF; hollow ones lost their text.") },
  steps: [
    { id: "review-open", anchor: "page.review", placement: "left", creates: "pdf.reviewCard",
      title: T("Click **Review** to check them one by one"), advanceOn: { event: "review.opened" } },
    { id: "review-card", anchor: "pdf.reviewCard", placement: "right",
      title: T("**Accept** it, or **Move…** it to its words"),
      body: T("**Discard** deletes one with no note; the arrows go to the next.") },
    { id: "review-all", anchor: "page.acceptAll", placement: "left", optional: true,
      title: T("**Accept all** keeps every rough match where it is") },
  ],
};
