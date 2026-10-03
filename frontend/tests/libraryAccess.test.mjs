// library/libraryAccess.js: the one object the home library asks before
// offering anything — what a member, a viewer and a share visitor may do.
import test from "node:test";
import assert from "node:assert/strict";
import { libraryAccess } from "../src/library/libraryAccess.js";
import { libraryTree } from "../src/library/libraryUtils.js";

const node = (id, content, children = []) => ({ id, content, children });

test("a member organizes the whole library", () => {
  const lib = libraryAccess({ role: "owner" });
  assert.equal(lib.root, "");
  assert.ok(lib.browse && lib.organize && lib.pin && lib.history);
  assert.ok(lib.contains("") && lib.contains("f1"));
  assert.equal(lib.clamp("f1"), "f1");
  assert.equal(lib.clamp(""), "");
});

test("a workspace viewer browses everything but changes nothing", () => {
  const lib = libraryAccess({ role: "viewer" });
  assert.ok(lib.browse && lib.history);
  assert.ok(!lib.organize && !lib.pin);
  assert.equal(lib.root, "");
});

test("a folder share visitor is confined to the folder and its subfolders", () => {
  // The share's listing carries the shared folder's own tree only.
  const tree = libraryTree({ folders: { children: [node("readout", "readout", [node("sub", "sub")])] } });
  const lib = libraryAccess({ shareMode: true, shareFolder: "readout", tree });
  assert.equal(lib.root, "readout");
  assert.ok(lib.browse);
  assert.ok(!lib.organize && !lib.pin && !lib.history);
  assert.ok(lib.contains("readout") && lib.contains("sub"));
  assert.ok(!lib.contains("lab") && !lib.contains(""));
  assert.equal(lib.clamp("sub"), "sub");
  assert.equal(lib.clamp("lab"), "readout");
  assert.equal(lib.clamp(""), "readout");
  assert.equal(lib.clamp(undefined), "readout");
  // Before the listing arrives the shared folder is still its own root.
  assert.equal(libraryAccess({ shareMode: true, shareFolder: "readout" }).clamp("readout"), "readout");
});

test("a page share has no library to browse", () => {
  const lib = libraryAccess({ shareMode: true });
  assert.ok(!lib.browse && !lib.organize);
});
