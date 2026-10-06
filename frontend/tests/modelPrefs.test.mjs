import assert from "node:assert/strict";
import { test } from "node:test";
import { effortFor, providerModels, speedFor } from "../src/chat/modelPrefs.js";

test("the model list is one connection's: the picked one, else the first", () => {
  const models = [
    { id: "a:x", provider: "a" }, { id: "a:y", provider: "a" },
    { id: "b:x", provider: "b" }, { id: "server:c:z", provider: "server:c" },
  ];
  const ids = (providerId) => providerModels(models, providerId).map((m) => m.id);
  assert.deepEqual(ids("b"), ["b:x"]);
  assert.deepEqual(ids("server:c"), ["server:c:z"]);
  // Nothing picked, or a pick that is gone: the first connection, never all.
  assert.deepEqual(ids(""), ["a:x", "a:y"]);
  assert.deepEqual(ids("removed"), ["a:x", "a:y"]);
  assert.deepEqual(providerModels(undefined, "a"), []);
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
