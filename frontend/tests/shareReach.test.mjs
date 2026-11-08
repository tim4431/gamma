// Who a page is open to, in words (sharing/shareReach.js): the Share
// popover's line under a page another link opens, and what Move into page…
// says before a move into a shared or published page.
import assert from "node:assert/strict";
import { test } from "node:test";
import { moveExposure, moveExposureMessage, viaLine } from "../src/sharing/shareReach.js";

const pageShare = (page_id, title, audience = "anyone", role = "view") => ({ page_id, folder: "", title, audience, role });
const folderShare = (folder, title, audience = "users", role = "view") => ({ page_id: "", folder, title, audience, role });

test("the via line names the nearest link and counts the others", () => {
  assert.equal(viaLine([]), "");
  assert.equal(viaLine(undefined), "");
  assert.equal(viaLine([pageShare("top", "Project", "anyone", "edit")]),
    "The link of “Project” opens this page too, as a page inside it: anyone with the link can edit it.");
  assert.equal(viaLine([folderShare("f", "Lab")]), "The link of the folder “Lab” opens this page too: anyone signed in can read it.");
  // an invited-only link grants the invited their own role: "read" says the least
  assert.match(viaLine([pageShare("top", "Project", "list", "edit"), folderShare("f", "Lab")]),
    /^The link of “Project” opens this page too, as a page inside it: the people invited to it can read it\. 1 more link opens it as well\.$/);
});

test("a move says only what is new for the moved pages", () => {
  const reach = { shares: [pageShare("top", "Project", "anyone", "edit"), folderShare("f", "Lab")], published: { page_id: "top", title: "Project" } };
  // a and b sit outside everything: both links and the publication are new to them
  const outside = () => false;
  const all = moveExposure(reach, ["a", "b"], outside);
  assert.deepEqual(all.shares.map((s) => s.page_id || s.folder), ["top", "f"]);
  assert.equal(all.published.page_id, "top");
  assert.equal(moveExposureMessage(all, 2),
    "The link of “Project” opens it: anyone with the link can edit the 2 pages you move, too. 1 more link opens it as well. "
    + "“Project” is published to Gamma Cloud: the 2 pages you move go there at the next sync and count against your plan.");
  // already inside the shared, published page (moved between two of its pages): nothing to say
  const inside = (id, entry) => entry.page_id === "top";
  const none = moveExposure({ ...reach, shares: [reach.shares[0]] }, ["a"], inside);
  assert.deepEqual(none, { shares: [], published: null });
  assert.equal(moveExposureMessage(none, 1), "");
  // one of the moved pages is new to the link: it is said
  const some = moveExposure({ shares: [reach.shares[0]], published: null }, ["a", "b"], (id) => id === "a");
  assert.equal(some.shares.length, 1);
  assert.equal(moveExposureMessage(moveExposure({ shares: [], published: reach.published }, ["a"], outside), 1),
    "“Project” is published to Gamma Cloud: the page you move goes there at the next sync and counts against your plan.");
  // no reach read (a refusal, an older server): the move goes ahead without a word
  assert.equal(moveExposureMessage(moveExposure(null, ["a"], outside), 1), "");
});
