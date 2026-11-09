// The library's bulk actions over many pages (delete, move, pin, label,
// copy). `runBulk` sends them CHUNK (100) pages to a request (POST
// /blocks/bulk, which trashes or patches them all in one transaction);
// `runBatch` one request a page, for the work that has no bulk form (a
// copy), up to POOL (4) at once where order does not matter. From ROW_FROM
// (10) items on, the work is a row of Background tasks — "12 of 340
// pages", and Stop, which ends it before the next request. Both resolve
// {done, failed, stopped}: the items that went, the errors of the ones
// that did not, and whether the person stopped it. The batch lives in the
// tab: a reload or a closed tab ends it part way.
// tests/pageBatch.test.mjs.
import { tn } from "../shared/i18n/i18n.js";
import { progressCounts } from "../tasks/taskKinds.js";

export const ROW_FROM = 10;
export const POOL = 4;
export const CHUNK = 100;

const failures = (failed) => tn("{n} page could not be changed: {message}", "{n} pages could not be changed: {message}",
  failed.length, { message: failed[0].message });

// A batch's bookkeeping: what went and what failed, its row from ROW_FROM
// items on (one patch per visible percent, each one re-rendering the app;
// none once it was stopped), and its closing state.
function tracker(total, { name, tasks }) {
  const done = [], failed = [];
  let shown = 0, row = null;
  const ctl = { done, failed, stop: false };
  const counts = () => progressCounts({ done: done.length + failed.length, total, unit: "pages" });
  if (total >= ROW_FROM) {
    row = tasks.addLocal({ kind: "batch", name, info: counts(), fraction: 0, cancel: () => { ctl.stop = true; } });
  }
  ctl.report = () => {
    const fraction = (done.length + failed.length) / total;
    const pct = Math.floor(fraction * 100);
    if (row === null || ctl.stop || pct === shown) return;
    shown = pct;
    tasks.updateLocal(row, { info: counts(), fraction });
  };
  // A stopped row is "cancelled" already, and the result says so too, even
  // when the Stop came as the last items ran: the row and the status agree.
  ctl.end = () => {
    if (row !== null && !ctl.stop) {
      tasks.updateLocal(row, failed.length ? { state: "failed", info: failures(failed), cancel: null }
        : { state: "done", info: counts(), cancel: null });
    }
    return { done, failed, stopped: ctl.stop };
  };
  return ctl;
}

// One request an item: `run(item)` resolves when it went.
export async function runBatch(items, run, { name, tasks, pool = POOL }) {
  const total = items.length;
  const batch = tracker(total, { name, tasks });
  let next = 0;
  const worker = async () => {
    while (!batch.stop && next < total) {
      const item = items[next++];
      try {
        await run(item);
        batch.done.push(item);
      } catch (err) {
        batch.failed.push(err);
      }
      batch.report();
    }
  };
  await Promise.all(Array.from({ length: Math.min(pool, total) }, worker));
  return batch.end();
}

// `size` items a request, one after the other: `send(chunk)` resolves one
// outcome per item of the chunk, in its order — null when it went, an
// Error when it did not (bulkOutcomes). A request that fails as a whole
// fails each of its items with its error, and the next one goes on.
export async function runBulk(items, send, { name, tasks, size = CHUNK }) {
  const batch = tracker(items.length, { name, tasks });
  for (let at = 0; at < items.length && !batch.stop; at += size) {
    const chunk = items.slice(at, at + size);
    let outcomes;
    try {
      outcomes = await send(chunk);
    } catch (err) {
      outcomes = chunk.map(() => err);
    }
    chunk.forEach((item, i) => (outcomes[i] ? batch.failed.push(outcomes[i]) : batch.done.push(item)));
    batch.report();
  }
  return batch.end();
}

// POST /blocks/bulk's `results` as runBulk's outcomes: null for a page
// that went, an Error with the server's detail and status for one refused.
export const bulkOutcomes = (results) => results.map((r) => (r.ok ? null
  : Object.assign(new Error(r.detail || `HTTP ${r.status}`), { status: r.status })));

// A batch's closing status: `said`, what the work did, then where a Stop
// left it and what could not be done, with the first error — that alone
// when nothing was done. "" when there is nothing to say.
export function batchText(said, { done, failed, stopped }, total) {
  const note = [
    stopped ? tn("Stopped after {done} of {n} page.", "Stopped after {done} of {n} pages.", total,
      { done: done.length + failed.length }) : "",
    failed.length ? failures(failed) : "",
  ].filter(Boolean).join(" ");
  return done.length || !note ? [said, note].filter(Boolean).join(" ") : note;
}
