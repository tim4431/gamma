// Pure library rules: the folder-path functions against the cases
// gamma/foldertags.py runs too (tests/shared/foldertags.json), then the
// helpers only the frontend has.
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { test } from "node:test";
import {
  addFolderTag, cleanFolderPath, cleanFolderSegment, defaultPageTitle, findPageForUrl,
  formatFullDate, formatRelativeTime, formatShortDate, normalizeLinkInput, pageAttachment, parseFolderTags,
} from "../src/library/libraryUtils.js";

const shared = JSON.parse(await readFile(new URL("../../tests/shared/foldertags.json", import.meta.url), "utf8"));

test("folder tag rules match gamma/foldertags.py", () => {
  for (const c of shared.parse_tags) assert.deepEqual(parseFolderTags(c.input), c.output, JSON.stringify(c));
  for (const c of shared.clean_segment) assert.equal(cleanFolderSegment(c.input), c.output, JSON.stringify(c));
  for (const c of shared.clean_path) assert.equal(cleanFolderPath(c.input), c.output, JSON.stringify(c));
  for (const c of shared.add_tag) assert.deepEqual(addFolderTag([...c.tags], c.path), c.output, c.note);
});

test("formatShortDate: today with the time, yesterday, a day this year, else the year", () => {
  // Built in local time: the column speaks the reader's time zone.
  const now = new Date(2026, 8, 13, 15, 0).getTime();
  const iso = (...parts) => new Date(...parts).toISOString();
  assert.match(formatShortDate(iso(2026, 8, 13, 9, 41), now), /^Today, 9:41/);
  assert.equal(formatShortDate(iso(2026, 8, 12, 23, 59), now), "Yesterday");
  assert.equal(formatShortDate(iso(2026, 8, 1, 8, 0), now), "Sep 1");
  assert.equal(formatShortDate(iso(2026, 0, 2, 8, 0), now), "Jan 2");
  assert.equal(formatShortDate(iso(2023, 5, 1), now), "2023");
  assert.match(formatShortDate(iso(2026, 8, 13, 16, 0), now), /^Today/, "a clock a little ahead is still today");
  assert.equal(formatShortDate("", now), "");
  assert.equal(formatShortDate("not a date", now), "");
  // A bare server timestamp is UTC, like formatRelativeTime's.
  assert.equal(formatShortDate("2023-06-01T12:00:00", now), "2023");
  assert.match(formatFullDate(iso(2026, 8, 12, 9, 5)), /Sep 12, 2026/);
});

test("formatRelativeTime picks the coarsest unit and reads bare timestamps as UTC", () => {
  const now = Date.parse("2026-09-13T12:00:00Z");
  assert.equal(formatRelativeTime("2026-09-13T11:59:30Z", now), "30s ago");
  assert.equal(formatRelativeTime("2026-09-13T11:15:00", now), "45m ago");
  assert.equal(formatRelativeTime("2026-09-13T09:00:00Z", now), "3h ago");
  assert.equal(formatRelativeTime("2026-09-10T12:00:00Z", now), "3d ago");
  assert.equal(formatRelativeTime("2026-09-06T12:00:00Z", now), "1w ago");
  assert.equal(formatRelativeTime("2026-07-01T12:00:00Z", now), "2mo ago");
  assert.equal(formatRelativeTime("2025-09-13T12:00:00Z", now), "1y ago");
  assert.equal(formatRelativeTime("2026-09-13T12:00:05Z", now), "1s ago", "the future clamps to a second");
  assert.equal(formatRelativeTime("", now), "");
});

test("findPageForUrl resolves a DOI or arXiv id against the library", () => {
  const pages = [
    { id: "p1", properties: { meta: { doi: "10.1000/ABC.1" } } },
    { id: "p2", properties: { source_url: "https://arxiv.org/pdf/2101.00001v2.pdf" } },
    { id: "p3", properties: { meta: { arxiv_id: "2202.02222" } } },
  ];
  assert.equal(findPageForUrl("https://doi.org/10.1000/abc.1", pages), "p1");
  assert.equal(findPageForUrl("https://doi.org/10.1000/abc.1.", pages), "p1", "trailing punctuation dropped");
  assert.equal(findPageForUrl("https://arxiv.org/abs/2101.00001", pages), "p2");
  assert.equal(findPageForUrl("arXiv:2202.02222", pages), "p3");
  assert.equal(findPageForUrl("https://example.com/paper.pdf", pages), null);
  assert.equal(findPageForUrl("", pages), null);
});

test("normalizeLinkInput turns identifiers into URLs", () => {
  assert.equal(normalizeLinkInput("https://x.test/a"), "https://x.test/a");
  assert.equal(normalizeLinkInput("2101.00001"), "https://arxiv.org/abs/2101.00001");
  assert.equal(normalizeLinkInput("arXiv:2101.00001v2"), "https://arxiv.org/abs/2101.00001v2");
  assert.equal(normalizeLinkInput("doi: 10.1/x"), "https://doi.org/10.1/x");
  assert.equal(normalizeLinkInput("10.1/x"), "https://doi.org/10.1/x");
  assert.equal(normalizeLinkInput("  "), "");
});

test("pageAttachment and defaultPageTitle", () => {
  assert.equal(pageAttachment({ properties: {} }), null);
  assert.deepEqual(pageAttachment({ properties: { doc_id: "d1", original_filename: "paper.pdf" } }),
    { kind: "pdf", id: "d1", url: "", name: "paper.pdf" });
  assert.equal(defaultPageTitle(null), "Untitled");
  assert.equal(defaultPageTitle({ name: "paper.pdf" }), "paper.pdf");
  assert.equal(defaultPageTitle({ url: "https://x.test/dir/a%20b.pdf" }), "a b.pdf");
  assert.equal(defaultPageTitle({ url: "https://x.test/dir/" }), "Untitled");
});
