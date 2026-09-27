import assert from "node:assert/strict";
import { test } from "node:test";
import { effortFor } from "../src/chat/effort.js";

test("a level the model takes is sent as it is", () => {
  assert.equal(effortFor("xhigh", ["low", "medium", "high", "xhigh", "max"]), "xhigh");
  assert.equal(effortFor("none", ["none", "low", "medium", "high"]), "none");
});

test("a level the model lacks becomes the nearest one it takes, the lower on a tie", () => {
  assert.equal(effortFor("xhigh", ["low", "medium", "high"]), "high");
  assert.equal(effortFor("max", ["minimal", "low", "medium", "high"]), "high");
  assert.equal(effortFor("none", ["low", "medium", "high"]), "low");
  assert.equal(effortFor("medium", ["low", "high"]), "low");
  assert.equal(effortFor("high", ["xhigh", "medium"]), "medium");
});

test("no preference, a model without levels or an unknown word sends nothing", () => {
  assert.equal(effortFor("", ["low", "high"]), "");
  assert.equal(effortFor("high", []), "");
  assert.equal(effortFor("high", null), "");
  assert.equal(effortFor("turbo", ["low", "high"]), "");
});
