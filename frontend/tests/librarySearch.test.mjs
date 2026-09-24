import test from "node:test";
import assert from "node:assert/strict";
import { createLibraryMatcher } from "../src/library/librarySearch.js";

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
