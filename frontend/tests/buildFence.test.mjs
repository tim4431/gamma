// node --test tests/buildFence.test.mjs (from frontend/) — the build
// fence's comparison (ink/buildFence.js, useBuildFence's pure part): a
// build's names, the build seen first, and the one trip that stops the
// page drawing until it is reloaded.
import assert from "node:assert/strict";
import { test } from "node:test";
import { buildNames, buildTrips, sameBuild } from "../src/ink/buildFence.js";

const fence = () => ({ seen: null, stale: false });

test("a build's names: its commit, label and version that are non-empty strings; a bare string is one; anything else none", () => {
  assert.deepEqual(buildNames({ commit: "abc123", label: "v1.4", version: "1.4.0" }), ["abc123", "v1.4", "1.4.0"]);
  assert.deepEqual(buildNames({ label: "v1.4" }), ["v1.4"], "a hello may carry any of them");
  assert.deepEqual(buildNames("abc123"), ["abc123"]);
  for (const garbage of [undefined, null, 42, {}, [], { commit: "", label: 7, version: null }, { other: "x" }]) {
    assert.deepEqual(buildNames(garbage), [], JSON.stringify(garbage));
  }
});

test("the same build shares at least one name with the one seen first", () => {
  const seen = new Set(["abc123", "v1.4", "1.4.0"]);
  assert.equal(sameBuild(seen, ["zzz", "1.4.0"]), true);
  assert.equal(sameBuild(seen, ["v1.4"]), true);
  assert.equal(sameBuild(seen, ["def456", "v1.5", "1.5.0"]), false);
  assert.equal(sameBuild(seen, []), false);
});

test("the first build with names is kept; one sharing none of them trips the fence; nameless news does nothing", () => {
  const f = fence();
  assert.equal(buildTrips(f, undefined), false, "a session without a build is not the first");
  assert.equal(f.seen, null);
  assert.equal(buildTrips(f, { commit: "abc123", label: "v1.4", version: "1.4.0" }), false);
  assert.deepEqual([...f.seen], ["abc123", "v1.4", "1.4.0"]);
  assert.equal(buildTrips(f, { label: "v1.4" }), false, "the same build, by one name");
  assert.equal(buildTrips(f, { commit: "def456" }), true, "another build: the fence trips");
  assert.equal(f.stale, true);
  assert.deepEqual([...f.seen], ["abc123", "v1.4", "1.4.0"], "the build seen first stays the measure");
});

test("a second trip does nothing", () => {
  const f = fence();
  buildTrips(f, "abc123");
  assert.equal(buildTrips(f, "def456"), true);
  assert.equal(buildTrips(f, "ghi789"), false, "the pill is up already");
  assert.equal(buildTrips(f, "abc123"), false, "nor does the first build coming back take the fence down");
  assert.equal(f.stale, true);
});
