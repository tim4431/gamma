import assert from "node:assert/strict";
import { test } from "node:test";
import { effortFor, fastChoice, modelLabel } from "../src/chat/modelPrefs.js";

test("a model that can't call tools says so in the model menus", () => {
  assert.equal(modelLabel({ model: "gemma3", tools: false, tools_reason: "upstream 400: no tools" }), "gemma3 · no tools");
  // Checked and calling them, or not checked yet: the name alone.
  assert.equal(modelLabel({ model: "gemma3", tools: true }), "gemma3");
  assert.equal(modelLabel({ model: "gemma3", tools: null }), "gemma3");
  assert.equal(modelLabel({ model: "gemma3" }), "gemma3");
});

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

test("the Fast button turns fast on and off, and is there only for a model that has it", () => {
  assert.deepEqual(fastChoice("", ["flex", "fast"]), { on: false, next: "fast" });
  assert.deepEqual(fastChoice("fast", ["fast"]), { on: true, next: "" });
  assert.equal(fastChoice("fast", ["flex"]), null);
  assert.equal(fastChoice("fast", []), null);
  assert.equal(fastChoice("fast", null), null);
});

test("an agent set up to run fast is on with nothing picked, and turned off by name", () => {
  const tiers = ["standard", "fast"];
  assert.deepEqual(fastChoice("", tiers, "fast"), { on: true, next: "standard" });
  assert.deepEqual(fastChoice("standard", tiers, "fast"), { on: false, next: "" });
  // one whose own speed is another is turned on by name, and off goes back to its own
  assert.deepEqual(fastChoice("", tiers, "standard"), { on: false, next: "fast" });
  assert.deepEqual(fastChoice("fast", tiers, "standard"), { on: true, next: "" });
});
