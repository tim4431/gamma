import test from "node:test";
import assert from "node:assert/strict";
import { createLibraryMatcher, createTitleScorer, rankLibraryPages } from "../src/library/librarySearch.js";
import { libraryTree } from "../src/library/libraryUtils.js";

test("title scorer: tight titles first, typos, and separator matching", () => {
  const pages = [{ id: "a", content: "Cavity readout" }, { id: "b", content: "Advances in cavity readout for sensors" }, { id: "c", content: "3,000-qubit processor" }];
  const score = createTitleScorer("cavity readout");
  assert(score(pages[0]) > score(pages[1]));
  assert.equal(score(pages[2]), 0);
  assert(createTitleScorer("cavtiy")(pages[0]) > 0);
  assert(createTitleScorer("3000")(pages[2]) > 0);
  assert.equal(createTitleScorer(""), null);
});

test("library matcher: typo-tolerant titles, folder/label chips, title hits first", () => {
  const match = createLibraryMatcher("cavity");
  assert(match("Cavity readout") > 0);
  assert(createLibraryMatcher("cavtiy")("Cavity readout") > 0, "a typo still hits");
  assert.equal(match("Atomic clocks"), 0);
  assert(match("Atomic clocks", ["cavity-qed"]) > 0, "a label chip hits");
  assert(match("Atomic clocks", ["papers/cavity"]) > 0, "a folder chip hits");
  assert(match("Cavity readout") > match("Atomic clocks", ["cavity"]), "title hits outrank chip-only hits");
  assert(createLibraryMatcher("cs229 attention")("Attention is all you need", ["cs229"]) > 0, "terms may split across title and chips");
  assert.equal(createLibraryMatcher("cs229 attention")("Attention is all you need"), 0);
  assert(createLibraryMatcher("resume")("Résumé tips") > 0, "diacritics fold");
  assert.equal(createLibraryMatcher("  "), null);
});

// What Ctrl+P, the chat's @ picker and its "+" page picker all list.
test("page pickers: recents, open tabs, then the rest; a query ranks titles and chips, recency breaking ties", () => {
  const tree = libraryTree({
    folders: { children: [{ id: "f1", content: "optics", children: [{ id: "f2", content: "cavity" }] }] },
    labels: { children: [{ id: "l1", content: "cs229" }] },
  });
  const page = (id, content, updated_at, properties) => ({ id, content, updated_at, properties });
  const pages = [
    page("a", "Cavity readout", "2026-01-01"),
    page("b", "Cavity sensors", "2026-01-05"),
    page("c", "Atomic clocks", "2026-01-03", { folders: ["f2"] }),
    page("d", "Attention is all you need", "2026-01-04", { labels: ["l1"] }),
    page("e", "3,000-qubit processor", "2026-01-02"),
  ];
  const opts = { recentViews: [{ id: "d", at: "2026-02-02" }, { id: "a", at: "2026-02-01" }], openTabs: [{ id: "e" }, { id: "a" }] };
  const ids = (rows) => rows.map((r) => r.page.id).join("");
  const sections = (rows) => rows.map((r) => r.section).join(" ");

  const all = rankLibraryPages(pages, tree, "", opts);
  assert.equal(ids(all), "daebc", "recents in viewing order, the other open tab, the rest by last edit");
  assert.equal(sections(all), "recent recent tabs rest rest");
  assert.equal(all[0].time, "2026-02-02", "a recent page shows when it was viewed");
  assert.equal(all[2].time, "2026-01-02", "any other page its last edit");
  assert.equal(ids(rankLibraryPages(pages, tree, "", { ...opts, limit: 2 })), "da");
  assert.equal(ids(rankLibraryPages(pages, tree, " ")), "bdcea", "nothing viewed or open: by last edit");

  const hits = rankLibraryPages(pages, tree, "cavity", opts);
  assert.equal(ids(hits), "abc", "title hits (the recent one first), then the page in the matching folder");
  assert.equal(sections(hits), "pages pages pages");
  assert.equal(hits[0].time, "2026-02-01");
  assert.equal(ids(rankLibraryPages(pages, tree, "cs229 attention", opts)), "d", "terms may split across title and label");
  assert.equal(ids(rankLibraryPages(pages, tree, "cavtiy readout", opts)), "a", "typos are forgiven");
  assert.equal(ids(rankLibraryPages(pages, tree, "3000", opts)), "e", "separators are skipped");
  assert.equal(ids(rankLibraryPages(pages, tree, "no-such-page", opts)), "");
});
