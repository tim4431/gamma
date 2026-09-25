import assert from "node:assert/strict";
import { test } from "node:test";
import { addUsage, cachedPercent, contextUsed, conversationUsage, estimateTokens, fmtTokens, liveUsage, usageDetail } from "../src/chat/tokenUsage.js";

test("the live line estimates the streaming round and keeps reported rounds exact", () => {
  assert.equal(estimateTokens(0), 0);
  assert.equal(estimateTokens(4000), 1000);
  assert.equal(liveUsage(null, 0), null);
  assert.deepEqual(liveUsage(null, 400), { input: 0, output: 100, cache_read: 0, cache_write: 0, estimate: true });
  const done = { input: 1000, output: 30, cache_read: 600, cache_write: 0 };
  assert.deepEqual(liveUsage(done, 0), { ...done, estimate: false });
  assert.deepEqual(liveUsage(done, 200), { ...done, output: 80, estimate: true });
});

test("fmtTokens is compact but comparable", () => {
  assert.equal(fmtTokens(0), "0");
  assert.equal(fmtTokens(950), "950");
  assert.equal(fmtTokens(1000), "1k");
  assert.equal(fmtTokens(1234), "1.2k");
  assert.equal(fmtTokens(12_340), "12k");
  assert.equal(fmtTokens(1_250_000), "1.25M");
  assert.equal(fmtTokens(12_500_000), "12.5M");
  assert.equal(fmtTokens(undefined), "0");
});

test("addUsage sums every field and tolerates missing sides", () => {
  const a = { input: 10, output: 2, cache_read: 5, cache_write: 0 };
  const b = { input: 20, output: 3, cache_read: 0, cache_write: 4 };
  assert.deepEqual(addUsage(a, b), { input: 30, output: 5, cache_read: 5, cache_write: 4 });
  assert.deepEqual(addUsage(null, b), b);
  assert.equal(addUsage(null, null), null);
});

test("conversation total counts only replies that carry a report", () => {
  const messages = [
    { role: "user", text: "hi" },
    { role: "ai", text: "old reply without usage" },
    { role: "ai", text: "a", usage: { input: 100, output: 10, cache_read: 80, cache_write: 0 } },
    { role: "ai", text: "b", usage: { input: 200, output: 20, cache_read: 0, cache_write: 0 } },
  ];
  const total = conversationUsage(messages);
  assert.deepEqual(total, { input: 300, output: 30, cache_read: 80, cache_write: 0 });
  assert.equal(cachedPercent(total), 27);
  assert.equal(cachedPercent(null), 0);
  assert.equal(conversationUsage([{ role: "ai", text: "x" }]), null);
});

test("usageDetail spells the counts out", () => {
  const text = usageDetail({ input: 1500, output: 20, cache_read: 1200, cache_write: 0 });
  assert.match(text, /1,500 input tokens/);
  assert.match(text, /20 output tokens/);
  assert.match(text, /1,200 read from the prompt cache \(80% of the input\)/);
  assert.equal(usageDetail(null), "");
});

test("contextUsed reads the latest reply's last round", () => {
  assert.equal(contextUsed([]), null);
  assert.equal(contextUsed([{ role: "ai", text: "old" }]), null);
  // An agent reply's usage sums its rounds; context_tokens is the last one alone.
  const agent = { role: "ai", actions: [{}], usage: { input: 9000, output: 300 }, context_tokens: 3500 };
  assert.equal(contextUsed([{ role: "user" }, agent, { role: "user", text: "next" }]), 3500);
  // A reply saved before context_tokens: single round → its usage; tool rounds → skipped.
  const plain = { role: "ai", usage: { input: 1000, output: 50 } };
  assert.equal(contextUsed([plain, { role: "ai", error: true }]), 1050);
  assert.equal(contextUsed([plain, { role: "ai", actions: [{}], usage: { input: 9000, output: 1 } }]), 1050);
});
