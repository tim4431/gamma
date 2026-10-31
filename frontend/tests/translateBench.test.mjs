// The translation bench's live mode (tests/bench/translateBench.mjs) sends a
// paper's chunks as the viewer's job does: these drive its sendChunks with a
// fake brief request and fake chunk requests, no server.
import test from "node:test";
import assert from "node:assert/strict";
import { sendChunks } from "./bench/translateBench.mjs";

const tick = () => new Promise((resolve) => setTimeout(resolve, 0));

// A brief request the test moves along by hand: `start()` sends its first
// line, `end()` its last.
function briefRequest() {
  const b = { started: false };
  let finish;
  const done = new Promise((resolve) => { finish = resolve; });
  b.request = (onStage) => {
    b.start = () => { b.started = true; onStage("brief"); };
    return done;
  };
  b.end = () => finish({ ready: true });
  return b;
}

test("the bench sends the first chunk at once and the others only once the brief request is under way", async () => {
  const brief = briefRequest();
  const sent = [];
  const ask = async (c, { waitBrief, again }) => {
    sent.push({ c, waitBrief, again, briefStarted: brief.started });
    return { translations: [], brief: brief.started };
  };
  const run = sendChunks([0, 1, 2, 3, 4], { parallel: 3, askBrief: brief.request, ask });
  await tick();
  await tick();
  assert.deepEqual(sent.map((s) => [s.c, s.waitBrief]), [[0, false]]);
  brief.start();
  await tick();
  brief.end();
  await run;
  const later = sent.filter((s) => !s.again && s.c !== 0);
  assert.deepEqual(later.map((s) => s.c).sort(), [1, 2, 3, 4]);
  for (const s of later) assert.equal(s.waitBrief, true);
  for (const s of later) assert.equal(s.briefStarted, true);
});

test("the bench lets the waiting chunks go when the brief request ends without a first line", async () => {
  const sent = [];
  const ask = async (c, { waitBrief }) => { sent.push([c, waitBrief]); return { translations: [], brief: false }; };
  await sendChunks([0, 1, 2], { parallel: 2, askBrief: async () => ({ ready: false }), ask });
  assert.deepEqual(sent.sort(), [[0, false], [1, true], [2, true]]);
});

test("the bench asks the chunks that came back without the brief once more when some came back with it", async () => {
  const brief = briefRequest();
  const sent = [];
  const ask = async (c, { waitBrief, again }) => {
    sent.push({ c, waitBrief, again });
    return { translations: [], brief: c !== 0 || again };
  };
  const run = sendChunks([0, 1, 2], { parallel: 2, askBrief: brief.request, ask });
  await tick();
  brief.start();
  brief.end();
  await run;
  assert.deepEqual(sent.filter((s) => s.again).map((s) => [s.c, s.waitBrief]), [[0, true]]);
});

test("the bench asks nothing again when no chunk came back with the brief", async () => {
  const sent = [];
  const ask = async (c, { again }) => { sent.push([c, again]); return { translations: [], brief: false }; };
  await sendChunks([0, 1], { parallel: 2, askBrief: async () => ({ ready: false }), ask });
  assert.equal(sent.filter(([, again]) => again).length, 0);
});
