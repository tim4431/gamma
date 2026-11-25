// The model menu's list (src/ai/modelMenu.js menuGroups): its order, the
// search across connections and the favorites.
import assert from "node:assert/strict";
import { test } from "node:test";
import { menuGroups, modelFacts, pickableRows } from "../src/ai/modelMenu.js";

const model = (provider, name, extra = {}) => ({ id: `${provider}:${name}`, provider, provider_name: `Conn ${provider.slice(-1)}`, model: name, ...extra });
const A1 = model("aaaaaaaa", "gpt-6", { billing: "subscription", service: "chatgpt" });
const A2 = model("aaaaaaaa", "gpt-mini", { billing: "subscription", service: "chatgpt" });
const B1 = model("bbbbbbbb", "claude-sonnet", { billing: "api", service: "anthropic" });
const S1 = model("server:cccccccc", "kimi", { billing: "shared" });
const MODELS = [A1, A2, B1, S1];
const shape = (groups) => groups.map((g) => [g.key, g.rows.map((r) => r.id)]);

test("favorites first, then a group per connection in the registry's order, with its billing and service", () => {
  const groups = menuGroups(MODELS, { favorites: [B1.id, A1.id] });
  assert.deepEqual(shape(groups), [
    ["favorites", [B1.id, A1.id]],
    ["connection:aaaaaaaa", [A1.id, A2.id]],
    ["connection:bbbbbbbb", [B1.id]],
    ["connection:server:cccccccc", [S1.id]],
  ]);
  assert.deepEqual(groups.slice(1).map((g) => [g.label, g.billing, g.service]),
    [["Conn a", "subscription", "chatgpt"], ["Conn b", "api", "anthropic"], ["Conn c", "shared", ""]]);
  assert.ok(groups[1].rows[0].favorite && !groups[1].rows[1].favorite, "a favorite is starred in its connection's group too");
});

test("listed once (the chat's model panel): a favorite stands in the chat models alone, and a connection left empty goes", () => {
  assert.deepEqual(shape(menuGroups(MODELS, { favorites: [B1.id, A1.id], once: true })), [
    ["favorites", [B1.id, A1.id]],
    ["connection:aaaaaaaa", [A2.id]],
    ["connection:server:cccccccc", [S1.id]],
  ]);
});

test("a search keeps the models matching every word, across connections", () => {
  assert.deepEqual(shape(menuGroups(MODELS, { query: "GPT" })), [["connection:aaaaaaaa", [A1.id, A2.id]]]);
  // the connection's name counts, and every word must match
  assert.deepEqual(shape(menuGroups(MODELS, { query: "conn b" })), [["connection:bbbbbbbb", [B1.id]]]);
  assert.deepEqual(shape(menuGroups(MODELS, { query: "gpt mini", favorites: [A2.id] })),
    [["favorites", [A2.id]], ["connection:aaaaaaaa", [A2.id]]]);
  assert.deepEqual(menuGroups(MODELS, { query: "nothing like it" }), []);
});

test("a pick no longer offered stands at the top, unavailable; a task's menu starts with the chat model", () => {
  const gone = "dddddddd:old-model";
  const groups = menuGroups(MODELS, { value: gone, follow: true, favorites: [gone, A2.id], missing: { [gone]: { model: "old-model", connection: "" } } });
  assert.deepEqual(shape(groups).slice(0, 3), [["missing", [gone]], ["follow", [""]], ["favorites", [gone, A2.id]]]);
  assert.equal(groups[0].rows[0].unavailable, true);
  assert.equal(groups[0].rows[0].label, "old-model", "named as the server names it");
  // A favorite no longer offered keeps its star but cannot be picked.
  assert.equal(groups[2].rows[0].unavailable, true);
  assert.ok(!pickableRows(groups).some((r) => r.id === gone));
  // A pick that is offered is no missing row; translation's services come last.
  const translate = menuGroups(MODELS, { value: "engine:google", follow: true, engines: [{ id: "engine:google", label: "Google" }] });
  assert.deepEqual(shape(translate).map(([key]) => key), ["follow", "connection:aaaaaaaa", "connection:bbbbbbbb", "connection:server:cccccccc", "engines"]);
});

test("a row's facts: what it can't do, pictures, context and a price only where tokens are paid for", () => {
  const kinds = (m, task) => modelFacts(m, { task }).map((f) => f.kind);
  assert.deepEqual(kinds({ ...B1, tools: false, images: true, context: 200000, price: { input: 3, output: 15 } }), ["tools", "vision", "context", "price"]);
  // a subscription shows no price, and an unknown fact is left out
  assert.deepEqual(kinds({ ...A1, context: 1048576, price: { input: 1, output: 2 } }), ["context"]);
  assert.deepEqual(kinds({ ...B1, price: { input: 3 } }), [], "half a price is none");
  // the handwriting task marks a model that sees no pictures, without hiding it
  assert.deepEqual(kinds({ ...A2, images: false }, "ink"), ["ink"]);
  assert.deepEqual(kinds({ ...A2, images: false }), []);
});
