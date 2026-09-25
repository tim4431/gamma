// The published-page slug mirror against the cases the backend runs too
// (tests/shared/slug.json at the repository root), and the page-host match.
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { test } from "node:test";
import { pageHostUser, publicPath, slugify } from "../src/shared/lib/slug.js";

const shared = JSON.parse(await readFile(new URL("../../tests/shared/slug.json", import.meta.url), "utf8"));

test("slugify matches publish.slug on every shared case", () => {
  for (const c of shared.slug) assert.equal(slugify(c.input), c.output, c.note);
});

test("publicPath drops the dash when there is no slug", () => {
  assert.equal(publicPath("My Paper", "abc-12_X"), "/my-paper-abc-12_X");
  assert.equal(publicPath("量子力学", "abc"), "/abc");
});

test("pageHostUser reads the username out of a page hostname", () => {
  const pattern = "{username}-pages.gammapdf.com";
  assert.equal(pageHostUser(pattern, "tim-pages.gammapdf.com"), "tim");
  assert.equal(pageHostUser(pattern, "Tim-Pages.GammaPDF.com:443"), "tim");
  assert.equal(pageHostUser(pattern, "a-pages-pages.gammapdf.com"), "a-pages");
  assert.equal(pageHostUser(pattern, "api.gammapdf.com"), "");
  assert.equal(pageHostUser(pattern, "x.tim-pages.gammapdf.com"), "");
  assert.equal(pageHostUser(pattern, "-pages.gammapdf.com"), "");
  assert.equal(pageHostUser("", "tim-pages.gammapdf.com"), "");
});
