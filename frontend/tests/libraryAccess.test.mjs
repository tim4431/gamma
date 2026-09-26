// library/libraryAccess.js: the one object the home library asks before
// offering anything — what a member, a viewer and a share visitor may do.
import test from "node:test";
import assert from "node:assert/strict";
import { libraryAccess } from "../src/library/libraryAccess.js";

test("a member organizes the whole library", () => {
  const lib = libraryAccess({ role: "owner" });
  assert.equal(lib.root, "");
  assert.ok(lib.browse && lib.organize && lib.pin && lib.history);
  assert.ok(lib.contains("") && lib.contains("a/b"));
  assert.equal(lib.clamp("a/b"), "a/b");
  assert.equal(lib.clamp(""), "");
});

test("a workspace viewer browses everything but changes nothing", () => {
  const lib = libraryAccess({ role: "viewer" });
  assert.ok(lib.browse && lib.history);
  assert.ok(!lib.organize && !lib.pin);
  assert.equal(lib.root, "");
});

test("a folder share visitor is confined to the folder and its subfolders", () => {
  const lib = libraryAccess({ shareMode: true, shareFolder: "lab/readout" });
  assert.equal(lib.root, "lab/readout");
  assert.ok(lib.browse);
  assert.ok(!lib.organize && !lib.pin && !lib.history);
  assert.ok(lib.contains("lab/readout") && lib.contains("lab/readout/sub"));
  assert.ok(!lib.contains("lab") && !lib.contains("lab/readouts") && !lib.contains(""));
  assert.equal(lib.clamp("lab/readout/sub"), "lab/readout/sub");
  assert.equal(lib.clamp("lab"), "lab/readout");
  assert.equal(lib.clamp(""), "lab/readout");
  assert.equal(lib.clamp(undefined), "lab/readout");
});

test("a page share has no library to browse", () => {
  const lib = libraryAccess({ shareMode: true });
  assert.ok(!lib.browse && !lib.organize);
});
