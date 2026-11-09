// node --test tests/perfMeasure.test.mjs (from frontend/) — the timing
// budgets' measure and budget (tests/perf/measure.mjs): the nearest-rank
// p95, setup kept out of the timed span, and what a case over its budget
// says, in the CI summary and as a run annotation. The budgets themselves
// run apart: npm run test:perf.
import assert from "node:assert/strict";
import { mkdtempSync, readFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import { test } from "node:test";
import { budget, measure, quantile } from "./perf/measure.mjs";

test("quantile is the nearest rank: of 20 samples the p95 is the 19th, the p50 the 10th", () => {
  const sorted = Array.from({ length: 20 }, (_, i) => i + 1);
  assert.equal(quantile(sorted, 0.95), 19);
  assert.equal(quantile(sorted, 0.5), 10);
  assert.equal(quantile(sorted, 1), 20);
  assert.equal(quantile([4], 0.95), 4);
  assert.equal(quantile([1, 2, 3], 0.01), 1);
});

test("measure: warmups untimed, setup's value handed to each call outside the timed span, samples sorted", async () => {
  let setups = 0;
  const calls = [];
  const busy = (ms) => { const end = performance.now() + ms; while (performance.now() < end); };
  const r = await measure((ctx) => calls.push(ctx), { warmup: 2, runs: 6, setup: () => { busy(20); return ++setups; } });
  assert.deepEqual(calls, [1, 2, 3, 4, 5, 6, 7, 8]);
  assert.equal(r.ms.length, 6);
  assert.deepEqual(r.ms, [...r.ms].sort((a, b) => a - b));
  assert.ok(r.p50 < 10, `setup's 20 ms counted: p50 ${r.p50} ms`);
  assert.equal(r.p95, r.ms[5]);
  assert.equal(r.max, r.ms[5]);
  // an async call is awaited inside its timed span
  const slow = await measure(() => new Promise((done) => setTimeout(done, 15)), { warmup: 0, runs: 3 });
  assert.ok(slow.p50 >= 14, `p50 ${slow.p50} ms`);
});

test("measure: under --expose-gc, a collection after each setup and before its call", async () => {
  const was = Object.getOwnPropertyDescriptor(globalThis, "gc");
  const said = [];
  globalThis.gc = () => said.push("gc");
  try {
    await measure(() => said.push("call"), { warmup: 1, runs: 2, setup: () => said.push("setup") });
  } finally {
    if (was) Object.defineProperty(globalThis, "gc", was);
    else delete globalThis.gc;
  }
  assert.deepEqual(said, ["setup", "gc", "call", "setup", "gc", "call", "setup", "gc", "call"]);
});

test("budget: passes at the budget, fails over it naming the case and its numbers, and writes the CI summary's row", () => {
  const dir = mkdtempSync(path.join(tmpdir(), "gamma-perf-"));
  const was = process.env.GITHUB_STEP_SUMMARY;
  process.env.GITHUB_STEP_SUMMARY = path.join(dir, "summary.md");
  try {
    const said = [];
    const t = { name: "a case", diagnostic: (m) => said.push(m) };
    budget(t, { p50: 1, p95: 5, max: 9 }, 5);
    assert.deepEqual(said, ["p50 1.00 ms, p95 5.00 ms, budget 5 ms"]);
    assert.throws(() => budget(t, { p50: 2, p95: 12.34, max: 20 }, 10),
      (err) => err instanceof assert.AssertionError && /^a case: p95 12\.3 ms is over its budget of 10 ms \(budgets were set on .*9950X3D/.test(err.message));
    assert.equal(readFileSync(process.env.GITHUB_STEP_SUMMARY, "utf8"),
      "| frontend | a case | 1.00 | 5.00 | 5 | ok |\n| frontend | a case | 2.00 | 12.3 | 10 | **over** |\n");
  } finally {
    if (was === undefined) delete process.env.GITHUB_STEP_SUMMARY;
    else process.env.GITHUB_STEP_SUMMARY = was;
    rmSync(dir, { recursive: true, force: true });
  }
});

test("budget on GitHub Actions: a case over its budget prints the ::warning annotation naming it, one within prints none", () => {
  const was = { actions: process.env.GITHUB_ACTIONS, summary: process.env.GITHUB_STEP_SUMMARY };
  const write = process.stdout.write;
  const printed = [];
  process.env.GITHUB_ACTIONS = "true";
  delete process.env.GITHUB_STEP_SUMMARY;
  process.stdout.write = (line) => { printed.push(String(line)); return true; };
  try {
    const t = { name: "a case", diagnostic: () => {} };
    budget(t, { p50: 1, p95: 4, max: 9 }, 5);
    assert.throws(() => budget(t, { p50: 2, p95: 12.34, max: 20 }, 10), assert.AssertionError);
  } finally {
    process.stdout.write = write;
    for (const [key, name] of [["actions", "GITHUB_ACTIONS"], ["summary", "GITHUB_STEP_SUMMARY"]]) {
      if (was[key] === undefined) delete process.env[name];
      else process.env[name] = was[key];
    }
  }
  assert.deepEqual(printed, ["::warning title=perf budget::frontend a case: p95 12.3 ms is over its budget of 10 ms\n"]);
});
