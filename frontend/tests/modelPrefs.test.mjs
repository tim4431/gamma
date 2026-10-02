import assert from "node:assert/strict";
import { test } from "node:test";
import { effortFor, speedFor } from "../src/chat/modelPrefs.js";

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

test("a speed tier is sent only when the model has it — never a nearest one", () => {
  assert.equal(speedFor("fast", ["flex", "fast"]), "fast");
  assert.equal(speedFor("flex", ["flex", "fast"]), "flex");
  // No cheaper or dearer substitute: a tier the model lacks sends nothing.
  assert.equal(speedFor("flex", ["fast"]), "");
  assert.equal(speedFor("fast", ["flex"]), "");
  assert.equal(speedFor("fast", []), "");
  assert.equal(speedFor("fast", null), "");
  assert.equal(speedFor("", ["flex", "fast"]), "");
  assert.equal(speedFor("turbo", ["flex", "fast"]), "");
});
