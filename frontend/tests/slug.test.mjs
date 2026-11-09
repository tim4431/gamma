// The published-page slug and the page-host match against the cases the
// backend runs too (tests/shared/slug.json at the repository root).
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

test("pageHostUser matches publish.page_host_user on every shared case", () => {
  for (const c of shared.page_host) assert.equal(pageHostUser(c.pattern, c.host), c.user, c.note);
});
