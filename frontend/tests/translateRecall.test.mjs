// The papers this browser last left showing their translation
// (src/pdf/translateRecall.js): bounded, by file, cleared by Show original,
// nothing breaks when the browser's storage does, and the key a paper opens
// under.
import test from "node:test";
import assert from "node:assert/strict";
import { RECALL_MAX, createRecall, langOfKey, modelOfKey, openKey, sameModel } from "../src/pdf/translateRecall.js";

function memoryStorage() {
  const items = new Map();
  let writes = 0;
  return {
    getItem: (k) => (items.has(k) ? items.get(k) : null),
    setItem: (k, v) => { writes += 1; items.set(k, String(v)); },
    writes: () => writes,
    items,
  };
}

test("a paper is remembered under the key it was shown under, until it is shown in the original", () => {
  const storage = memoryStorage();
  const recall = createRecall(storage, "gamma-translated:ada@ws1");
  assert.equal(recall.keyOf("doc-a"), null, "never shown here");
  recall.remember("doc-a", "zh-CN|anthropic:model");
  assert.equal(recall.keyOf("doc-a"), "zh-CN|anthropic:model");
  // Shown again under another language: the key is the last one.
  recall.remember("doc-a", "ja|anthropic:model");
  assert.equal(recall.keyOf("doc-a"), "ja|anthropic:model");
  recall.forget("doc-a");
  assert.equal(recall.keyOf("doc-a"), null);
  // Another account or workspace keeps a list of its own.
  recall.remember("doc-b", "de|m");
  assert.equal(createRecall(storage, "gamma-translated:bob@ws1").keyOf("doc-b"), null);
  assert.equal(createRecall(storage, "gamma-translated:ada@ws1").keyOf("doc-b"), "de|m", "read back as stored");
  // No file, no key: nothing.
  recall.remember("", "de|m");
  recall.remember("doc-c", "");
  assert.equal(recall.keyOf(""), null);
  assert.equal(recall.keyOf("doc-c"), null);
});

test("at most the cap of papers, the one used longest ago going first; showing one again keeps it", () => {
  const storage = memoryStorage();
  const recall = createRecall(storage, "list", 3);
  for (const f of ["a", "b", "c"]) recall.remember(f, "zh|m");
  recall.remember("a", "zh|m"); // a is used again, so b is now the oldest
  recall.remember("d", "zh|m");
  assert.deepEqual(["a", "b", "c", "d"].map((f) => recall.keyOf(f)), ["zh|m", null, "zh|m", "zh|m"]);
  assert.deepEqual(JSON.parse(storage.items.get("list")).map(([f]) => f), ["c", "a", "d"]);
  // The paper used last, under the same key, writes nothing.
  const writes = storage.writes();
  recall.remember("d", "zh|m");
  assert.equal(storage.writes(), writes);
  // Forgetting one not kept writes nothing either.
  recall.forget("zzz");
  assert.equal(storage.writes(), writes);
  assert.ok(RECALL_MAX >= 100 && RECALL_MAX <= 1000, "a few hundred papers by default");
});

test("storage that fails or holds something else remembers nothing and breaks nothing", () => {
  const broken = { getItem: () => { throw new Error("SecurityError"); }, setItem: () => { throw new Error("QuotaExceededError"); } };
  const recall = createRecall(broken, "list");
  recall.remember("a", "zh|m");
  assert.equal(recall.keyOf("a"), null);
  recall.forget("a");
  assert.equal(createRecall(null, "list").keyOf("a"), null);
  const storage = memoryStorage();
  for (const junk of ["not json", "{\"a\": 1}", "[[1, 2], \"x\", [\"ok\", \"zh|m\"]]"]) {
    storage.items.set("list", junk);
    const r = createRecall(storage, "list");
    assert.equal(r.keyOf("a"), null);
    r.remember("b", "de|m");
    assert.equal(r.keyOf("b"), "de|m");
  }
  assert.equal(createRecall(storage, "list").keyOf("ok"), "zh|m", "the well-formed entries stay");
});

test("a paper opens under the settings' key when it names the model it was left under, whatever connection reaches it", () => {
  const left = "zh-CN|LsFgBB5o:stand-in";
  // The same key, or the model through a connection made again or a shared one.
  for (const now of [left, "zh-CN|QZyn6Gxz:stand-in", "zh-CN|server:Zq8_k-2w:stand-in"]) {
    assert.ok(sameModel(left, now), now);
    assert.equal(openKey(left, now), now, now);
  }
  // Another model, language or service: the original.
  for (const now of ["zh-CN|QZyn6Gxz:other", "ja|LsFgBB5o:stand-in", "zh-CN|engine:microsoft", "zh-CN|"]) {
    assert.equal(sameModel(left, now), false, now);
    assert.equal(openKey(left, now), null, now);
  }
  // A bare name with a colon of its own is no registry id.
  assert.equal(sameModel("zh|qwen2.5:7b", "zh|qwen2.6:7b"), false);
  assert.equal(sameModel("zh|engine:microsoft", "zh|engine:microsoft"), true);
  assert.equal(openKey(null, left), null, "never left translated");
});

test("with no chat model on offer, a paper left under one opens under its key, so its kept translation shows", () => {
  const left = "zh-CN|LsFgBB5o:stand-in";
  // The free service stands in for the removed connection, or nothing does.
  assert.equal(openKey(left, "zh-CN|engine:microsoft", { noChatModel: true }), left);
  assert.equal(openKey(left, "zh-CN|", { noChatModel: true }), left);
  assert.equal(modelOfKey(left), "LsFgBB5o:stand-in", "its requests send the model it names");
  // Another language, or a paper left under a service: the settings' key decides.
  assert.equal(openKey(left, "ja|engine:microsoft", { noChatModel: true }), null);
  assert.equal(openKey("zh-CN|engine:deepl", "zh-CN|engine:microsoft", { noChatModel: true }), null);
  assert.equal(openKey(left, "zh-CN|engine:microsoft"), null, "a service picked while a chat model is on offer");
});

test("a translate key opens with the language's tag", () => {
  assert.equal(langOfKey("zh-TW|engine:microsoft"), "zh-TW");
  assert.equal(langOfKey("de|openai:gpt-5"), "de");
  assert.equal(langOfKey("ja"), "ja", "a key with no model");
  assert.equal(langOfKey(""), "");
  assert.equal(langOfKey(undefined), "");
});
