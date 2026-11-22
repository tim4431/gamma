import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import { addUsage, cachedPercent, contextUsed, conversationUsage, costLabel, estimateTokens, fmtCost, fmtPrice, fmtTokens, liveUsage, usageDetail } from "../src/chat/tokenUsage.js";

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

// The server sums a reply's rounds the same way (gamma/ai_client.py
// add_usage): tests/shared/ai_models.json's `sums`.
const SUMS = JSON.parse(readFileSync(new URL("../../tests/shared/ai_models.json", import.meta.url), "utf8")).sums.cases;

test("usage reports sum as the server sums them: counts, the known costs, unpriced, one billing or mixed", () => {
  for (const c of SUMS) assert.deepEqual(c.calls.reduce((total, usage) => addUsage(total, usage), null), c.sum, c.note);
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

test("a reply's cost line is its rounds' known sum, a lower bound when a round had no price, nothing when nothing is priced", () => {
  const round = (cost, billing = "api") => ({ input: 100, output: 10, cache_read: 0, cache_write: 0, billing, cost });
  assert.equal(costLabel(addUsage(addUsage(null, round(0.012)), round(0.009))), "$0.021");
  assert.equal(costLabel(addUsage(addUsage(null, round(0.02)), round(null))), "~$0.02+");
  // no price at all: tokens alone, never $0
  assert.equal(costLabel(addUsage(null, round(null))), "");
  // a subscription or a shared connection is not billed per token
  assert.equal(costLabel(addUsage(addUsage(null, round(null, "subscription")), round(null, "subscription"))), "");
});

test("a conversation's cost adds the replies that have one; replies saved before costs add none", () => {
  const total = conversationUsage([
    { role: "ai", text: "old", usage: { input: 100, output: 10, cache_read: 0, cache_write: 0 } },
    { role: "ai", text: "a", usage: { input: 200, output: 20, cache_read: 0, cache_write: 0, billing: "api", cost: 0.25 } },
    { role: "ai", text: "b", usage: { input: 300, output: 30, cache_read: 0, cache_write: 0, billing: "api", cost: 1.5 } },
  ]);
  assert.equal(total.input, 600);
  assert.equal(costLabel(total), "$1.75");
  assert.equal(costLabel(conversationUsage([{ role: "ai", usage: { input: 1, output: 1 } }])), "");
});

test("money reads short at every size, and an unknown amount is never $0", () => {
  assert.equal(fmtCost(0), "$0");
  assert.equal(fmtCost(0.004), "<$0.01");
  assert.equal(fmtCost(0.01), "$0.01");
  assert.equal(fmtCost(0.0213), "$0.021");
  assert.equal(fmtCost(0.25), "$0.25");
  assert.equal(fmtCost(0.9996), "$1.00");
  assert.equal(fmtCost(12.3), "$12.30");
  assert.equal(fmtCost(1234.5), "$1,235");
  for (const unknown of [null, undefined, Number.NaN, -1, "0.5"]) assert.equal(fmtCost(unknown), "—");
  assert.equal(costLabel({ input: 1, output: 1, cost: 0.004, unpriced: true }), "<$0.01+");
  assert.equal(costLabel({ input: 1, output: 1, cost: 0, unpriced: true }), "");
  assert.equal(fmtPrice(3), "$3");
  assert.equal(fmtPrice(2.5), "$2.5");
  assert.equal(fmtPrice(0.075), "$0.075");
  assert.equal(fmtPrice(null), "");
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
