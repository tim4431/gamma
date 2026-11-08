// How the timing budgets time (docs/dev/debugging.md "Performance
// budgets"): each case runs a few untimed warmups, then a fixed number of
// timed runs, and its p95 is held to a fixed budget. The p95 is the
// nearest-rank one, so of 20 runs the single slowest is left out.
import assert from "node:assert/strict";
import { appendFileSync } from "node:fs";

// Where the budgets were set: about 5x the p95 measured there, idle.
export const BUDGETS_SET_ON = "a Ryzen 9 9950X3D, Node 22.23, idle (2026-11)";

// The nearest-rank `q` quantile (0 < q ≤ 1) of ascending `sorted` samples:
// the ceil(q·n)-th of them.
export const quantile = (sorted, q) => sorted[Math.min(sorted.length, Math.max(1, Math.ceil(q * sorted.length))) - 1];

// `fn(ctx)` called `warmup` times untimed, then `runs` times timed;
// `ctx` is what `setup()` returns, called before each call and outside the
// timed span (fresh data for a cold run). Either may be async. Under
// `node --expose-gc` (npm run test:perf) a garbage collection runs between
// setup and the call, so one run does not pay for the garbage of the last
// one or of its setup. → {p50, p95, max, ms}, in milliseconds, `ms` every
// timed run ascending.
export async function measure(fn, { warmup = 3, runs = 20, setup } = {}) {
  const ms = [];
  for (let r = 0; r < warmup + runs; r++) {
    const ctx = setup ? await setup() : undefined;
    globalThis.gc?.();
    const t0 = performance.now();
    const out = fn(ctx);
    if (typeof out?.then === "function") await out;
    const dt = performance.now() - t0;
    if (r >= warmup) ms.push(dt);
  }
  ms.sort((a, b) => a - b);
  return { p50: quantile(ms, 0.5), p95: quantile(ms, 0.95), max: ms.at(-1), ms };
}

// The case `t` (a node:test context) held to `limit` ms at its p95: its
// numbers printed as the test's diagnostic, and as a row of the CI run's
// summary table when there is one ($GITHUB_STEP_SUMMARY), then the p95
// asserted. On GitHub Actions a case over its budget also prints a
// `::warning` line, which the run shows as an annotation naming the case
// (it needs the spec reporter: TAP's `# ` prefix hides it, so test:perf
// asks for spec).
export function budget(t, result, limit) {
  const f = (v) => v.toFixed(v < 10 ? 2 : 1);
  const over = result.p95 > limit;
  const breach = `${t.name}: p95 ${f(result.p95)} ms is over its budget of ${limit} ms`;
  t.diagnostic(`p50 ${f(result.p50)} ms, p95 ${f(result.p95)} ms, budget ${limit} ms`);
  if (process.env.GITHUB_STEP_SUMMARY) {
    appendFileSync(process.env.GITHUB_STEP_SUMMARY,
      `| frontend | ${t.name} | ${f(result.p50)} | ${f(result.p95)} | ${limit} | ${over ? "**over**" : "ok"} |\n`);
  }
  if (over && process.env.GITHUB_ACTIONS === "true") process.stdout.write(`::warning title=perf budget::frontend ${breach}\n`);
  assert.ok(!over, `${breach} (budgets were set on ${BUDGETS_SET_ON})`);
}
