// The translated view's job (src/pdf/translateJob.js) in Node: the fixture
// paper's pages, segmented as the viewer segments them, and a fake server.
import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import { segmentPage } from "../src/pdf/pdfTranslate.js";
import { BREAK } from "../src/pdf/translatePlan.js";
import { FAILS_IN_A_ROW, createLimit, createTranslateJob } from "../src/pdf/translateJob.js";

const PAPER = JSON.parse(fs.readFileSync(new URL("./fixtures/translatePaper.json", import.meta.url)));
const PAGES = new Map(PAPER.pages.map((p) => [p.pn, segmentPage(p.runs)]));
// The paper twice over, for a job with more chunks than its two pages make.
const LONG = new Map([...PAPER.pages, ...PAPER.pages].map((p, k) => [k + 1, segmentPage(p.runs)]));
const last = (xs) => xs[xs.length - 1];

// A text "translated": every part marked, its markers and tokens kept.
const zh = (src) => src.split(BREAK).map((p) => `译${p.trim()}`).join(` ${BREAK} `);
const translated = (texts) => ({ translations: texts.map(zh), failed: [] });

// The server: `answer(texts, call, {signal, onPartial, onStage})` makes each
// request's final reply; `asked` keeps every request's texts and context.
function server(answer = translated) {
  const asked = [];
  const translate = async (texts, signal, onPartial, context, onStage) => {
    asked.push({ texts, context });
    return answer(texts, asked.length, { signal, onPartial, onStage });
  };
  return { asked, translate, texts: () => asked.flatMap((r) => r.texts) };
}

// The viewer around its jobs: the segmented pages, the entries the jobs
// hand back (which seed the next job), each handing, and the statuses they
// report. `paper` is its pages' blocks by page number.
function viewer(paper = PAGES) {
  const translatable = [...paper].flatMap(([pn, blocks]) => blocks.flatMap((b, i) => (b.translate ? [`${pn}:${i}`] : [])));
  const paras = new Map();
  const entries = new Map();
  const paints = [];
  const statuses = [];
  const job = (translate, over = {}) => createTranslateJob({
    pages: [...paper.keys()], label: "document", key: "zh-CN|model", parallel: 2,
    segment: async (pn) => {
      if (!paras.has(pn) && paper.has(pn)) paras.set(pn, paper.get(pn));
      return paras.get(pn) || [];
    },
    cachedParas: (pn) => paras.get(pn) || null,
    seed: (pn) => entries.get(pn),
    translate,
    context: { pageId: "page-1" },
    onPages: (changed) => {
      paints.push(changed);
      for (const [pn, e] of changed) entries.set(pn, e);
    },
    onStatus: (st) => statuses.push(st),
    retry: { pauseMs: 1 },
    ...over,
  });
  // The translatable blocks with a result, and those still pending.
  const filled = () => translatable.filter((b) => { const [pn, i] = b.split(":"); return entries.get(+pn)?.texts[i] != null; });
  const pending = () => translatable.filter((b) => !filled().includes(b));
  return { entries, paints, statuses, job, filled, pending };
}

// Nothing is owed or in flight once a job is over, however it ended.
function settled(entries) {
  for (const e of entries.values()) {
    assert.equal(e.queued, false);
    assert.ok(!e.busy?.size && !Object.keys(e.partial || {}).length);
  }
}

test("a job fills its pages chunk by chunk and ends at full progress", async () => {
  const v = viewer();
  const s = server();
  await v.job(s.translate).run();
  assert.deepEqual(v.pending(), []);
  for (const b of v.filled()) {
    const [pn, i] = b.split(":");
    const text = v.entries.get(+pn).texts[i];
    assert.ok(text === "" || text.startsWith("译"), `${b}: ${text}`);
  }
  assert.ok([...v.entries.values()].every((e) => e.done));
  settled(v.entries);
  assert.ok(s.asked.length > 1);
  assert.ok(s.asked.every((r) => r.context.pageId === "page-1" && "section" in r.context),
    "every request carries the paper and its chunk's context");
  assert.deepEqual(s.asked.map((r) => r.context.waitBrief), s.asked.map((_, n) => n > 0),
    "the first chunk does not wait for the paper's brief, the others do");
  assert.deepEqual(v.statuses[0],
    { running: true, progress: 0, label: "document", reading: false, error: null, untranslated: 0, unanswered: 0, medianMs: null });
  const { medianMs, ...end } = last(v.statuses);
  assert.deepEqual(end,
    { running: false, progress: 1, label: "document", reading: false, error: null, untranslated: 0, unanswered: 0 });
  assert.equal(typeof medianMs, "number");
});

// The blocks named failed in the entries, as `pn:i`.
const failedBlocks = (v) => [...v.entries].flatMap(([pn, e]) => [...e.failed].map((i) => `${pn}:${i}`));

// A server whose requests fail for the chunks numbered in `failing`, each
// try with its own reason, for a job with one worker: 0 is the first chunk
// asked, and a failed chunk's second try, which comes next, keeps its
// number. `sizes` keeps each chunk's number of texts.
function flaky(failing) {
  const sizes = [];
  let retry = false; // the next request is a failed chunk's second try
  const s = server((texts, call) => {
    const first = !retry;
    retry = false;
    if (first) sizes.push(texts.length);
    if (!failing.includes(sizes.length - 1)) return translated(texts);
    retry = first;
    throw new Error(`provider down (request ${call})`);
  });
  return { ...s, sizes };
}

test("a chunk whose request still fails when asked once more leaves its paragraphs in the original, and the job goes on", async () => {
  const everything = server();
  await viewer().job(everything.translate).run();

  const v = viewer();
  const s = flaky([0]);
  await v.job(s.translate, { parallel: 1 }).run();
  assert.equal(s.asked.length, everything.asked.length + 1, "the first chunk asked twice, every other chunk once");
  const end = last(v.statuses);
  assert.equal(end.error, null, "the job is no failure");
  const lost = s.asked[0].texts.length;
  assert.deepEqual([end.untranslated, end.unanswered], [lost, lost], "its paragraphs count as left in the original");
  assert.ok(end.progress > 0 && end.progress < 1);
  assert.ok(failedBlocks(v).length > 0);
  assert.deepEqual(v.pending().sort(), failedBlocks(v).sort(), "exactly its blocks are pending, named failed");
  for (const b of failedBlocks(v)) assert.equal(v.entries.get(+b.split(":")[0]).done, false);
  settled(v.entries);

  // The next job asks for them alone.
  const again = server();
  await v.job(again.translate).run();
  assert.deepEqual(again.texts().sort(), [...s.asked[0].texts].sort());
  assert.deepEqual(v.pending(), []);
});

test("chunks failing three in a row stop the job with the last one's reason; one that comes back between them starts the count again", async () => {
  assert.equal(FAILS_IN_A_ROW, 3);
  // Every request fails: three chunks, each asked twice, then no more.
  const v = viewer();
  const down = flaky([0, 1, 2, 3, 4, 5, 6, 7]);
  await v.job(down.translate, { parallel: 1 }).run();
  assert.equal(down.asked.length, 6);
  let end = last(v.statuses);
  assert.equal(end.running, false);
  assert.equal(end.error, "provider down (request 6)");
  assert.equal(end.progress, 0);
  assert.deepEqual(v.filled(), []);
  settled(v.entries);

  // Two failures, a chunk that comes back, two more: the job runs to its end.
  const everything = server();
  await viewer(LONG).job(everything.translate).run();
  const chunks = everything.asked.length;
  assert.ok(chunks >= 6, `${chunks} chunks`);
  const w = viewer(LONG);
  const gaps = flaky([0, 1, 3, 4]);
  await w.job(gaps.translate, { parallel: 1 }).run();
  assert.equal(gaps.asked.length, chunks + 4);
  end = last(w.statuses);
  assert.equal(end.error, null);
  const lost = [0, 1, 3, 4].reduce((n, k) => n + gaps.sizes[k], 0);
  assert.deepEqual([end.untranslated, end.unanswered], [lost, lost]);
  assert.deepEqual(w.pending().sort(), failedBlocks(w).sort());

  // Three in a row after a success stop it there.
  const x = viewer(LONG);
  const row = flaky([1, 2, 3]);
  await x.job(row.translate, { parallel: 1 }).run();
  assert.equal(row.asked.length, 1 + 3 * 2, "nothing after the third failed chunk");
  assert.equal(last(x.statuses).error, "provider down (request 7)");
});

test("a refusal every later request would meet stops the job at once", async () => {
  const refusal = (status, retryAfter = 0) => server(() => {
    throw Object.assign(new Error(`refused ${status}`), { status, retryAfter });
  });
  // The shared AI allowance used up: final, not asked again.
  const v = viewer();
  const spent = refusal(429);
  await v.job(spent.translate, { parallel: 1 }).run();
  assert.equal(spent.asked.length, 1);
  assert.equal(last(v.statuses).error, "refused 429");
  // Gamma's own refusals: no provider, not signed in, no access. Asked once
  // more as any failed request is, then the job stops.
  for (const status of [503, 401, 403]) {
    const w = viewer();
    const s = refusal(status);
    await w.job(s.translate, { parallel: 1 }).run();
    assert.equal(s.asked.length, 2, `${status}`);
    assert.equal(last(w.statuses).error, `refused ${status}`);
  }
});

test("a chunk turned away by a busy account past the wait's patience is dropped, not a refusal of the job", async () => {
  const everything = server();
  await viewer().job(everything.translate).run();
  const first = JSON.stringify(everything.asked[0].texts);
  // The account's AI calls all in use (a 429 with Retry-After) for the first
  // chunk every time it is asked; the others come back.
  const v = viewer();
  const s = server((texts) => {
    if (JSON.stringify(texts) === first) throw Object.assign(new Error("busy"), { status: 429, retryAfter: 0.001 });
    return translated(texts);
  });
  await v.job(s.translate, { parallel: 1, retry: { pauseMs: 1, patienceMs: 5 } }).run();
  const tries = s.asked.filter((r) => JSON.stringify(r.texts) === first).length;
  assert.ok(tries >= 2, `${tries} tries: the wait was waited out`);
  assert.equal(s.asked.length, tries + everything.asked.length - 1, "the job went on to every later chunk");
  const end = last(v.statuses);
  assert.equal(end.error, null, "no refusal of the job");
  const lost = everything.asked[0].texts.length;
  assert.deepEqual([end.untranslated, end.unanswered], [lost, lost]);
  assert.deepEqual(v.pending().sort(), failedBlocks(v).sort());
  settled(v.entries);
});

test("the paragraphs the server could not translate stay pending and count as untranslated", async () => {
  const v = viewer();
  // The server names its first text failed (and answers it as its source).
  const s = server((texts, call) => ({ ...translated(texts), failed: call === 1 ? [0] : [] }));
  await v.job(s.translate, { parallel: 1 }).run();
  const end = last(v.statuses);
  assert.equal(end.error, null);
  assert.equal(end.untranslated, 1);
  assert.ok(end.progress < 1, "its characters are not done");
  const failed = failedBlocks(v);
  assert.ok(failed.length > 0);
  assert.equal(end.unanswered, 0, "the model's, not the connection's");
  assert.deepEqual(v.pending().sort(), failed.sort(), "exactly the failed blocks are pending");
  for (const b of failed) assert.equal(v.entries.get(+b.split(":")[0]).done, false);
  settled(v.entries);
});

test("a halt keeps the chunks that came back and asks for nothing more", async () => {
  const v = viewer();
  let job = null;
  const s = server((texts, call) => {
    if (call === 1) return translated(texts);
    // Halted while the second chunk is asked: its request fails as an
    // aborted fetch does.
    job.halt();
    throw new Error("aborted");
  });
  job = v.job(s.translate, { parallel: 1 });
  await job.run();
  assert.equal(s.asked.length, 2);
  assert.ok(v.filled().length >= s.asked[0].texts.length, "the first chunk's blocks are filled");
  assert.ok(v.pending().length > 0);
  const end = last(v.statuses);
  assert.equal(end.running, false);
  assert.equal(end.error, null, "a halt is no failure");
  assert.equal(end.untranslated, 0);
  assert.ok(end.progress > 0 && end.progress < 1);
  settled(v.entries);
});

test("a re-run seeds from the entries before it and asks only for the rest", async () => {
  const everything = server();
  await viewer().job(everything.translate).run();

  const v = viewer();
  let job = null;
  // The first chunk lands, its first text back as its source (untranslated);
  // then the job is halted.
  const first = server((texts, call) => {
    if (call === 1) return { translations: texts.map((t, j) => (j === 0 ? t : zh(t))), failed: [] };
    job.halt();
    throw new Error("aborted");
  });
  job = v.job(first.translate, { parallel: 1 });
  await job.run();
  const [source, ...landed] = first.asked[0].texts;
  assert.ok(landed.length > 0);
  const untranslated = [...v.entries].flatMap(([pn, e]) => e.texts.flatMap((t, i) => (t === null ? [[pn, i]] : [])));
  assert.ok(untranslated.length > 0);

  const rest = server();
  const before = v.paints.length;
  await v.job(rest.translate).run();
  for (const [pn, i] of untranslated) {
    const seeded = v.paints[before].get(pn);
    assert.equal(seeded.texts[i], undefined, "an untranslated block is pending again, washed as queued");
    assert.equal(seeded.queued, true);
  }
  assert.ok(rest.texts().includes(source), "a text that came back as its source is asked again");
  assert.ok(landed.every((t) => !rest.texts().includes(t)), "finished texts are not");
  assert.deepEqual([...landed, ...rest.texts()].sort(), everything.texts().sort());
  assert.deepEqual(v.pending(), []);
  assert.ok([...v.entries.values()].every((e) => e.done));

  // Entries made under another language or model seed nothing.
  const other = server();
  await v.job(other.translate, { key: "de|model" }).run();
  assert.deepEqual(other.texts().sort(), everything.texts().sort());
});

test("streamed text and the wait for the paper's brief reach the entries and the status", async () => {
  const v = viewer();
  let seen = null;
  const s = server((texts, call, { onPartial, onStage }) => {
    if (call === 1) {
      onStage("brief");
      const reading = last(v.statuses).reading;
      onStage("translate");
      onPartial(0, "译正在");
      seen = {
        reading, after: last(v.statuses).reading,
        busy: [...v.entries.values()].some((e) => e.busy.size),
        partial: [...v.entries.values()].flatMap((e) => Object.values(e.partial)),
      };
    }
    return translated(texts);
  });
  await v.job(s.translate, { parallel: 1 }).run();
  assert.deepEqual(seen, { reading: true, after: false, busy: true, partial: ["译正在"] });
  assert.equal(last(v.statuses).reading, false);
  settled(v.entries);
});

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
async function until(ok, what) {
  for (let i = 0; i < 400 && !ok(); i++) await sleep(5);
  assert.ok(ok(), `timed out waiting for ${what}`);
}

// Every block's text, as [`pn:i`, text].
const blockTexts = (v) => [...v.entries].flatMap(([pn, e]) => e.texts.map((t, i) => [`${pn}:${i}`, t]));

test("the paper's brief is asked for as the job starts and not waited for: the first chunk goes at once, the others once its request is under way", async () => {
  const v = viewer();
  const order = [];
  let underWay = null, made = null;
  const brief = (signal, onStage) => new Promise((resolve) => {
    order.push("brief");
    underWay = () => { order.push("brief under way"); onStage("brief"); };
    made = () => resolve({ ready: true });
  });
  const s = server((texts, call) => { order.push(`chunk ${call}`); return translated(texts); });
  const running = v.job(s.translate, { brief }).run();
  await until(() => order.includes("chunk 1"), "the first chunk");
  await sleep(30);
  assert.deepEqual(order, ["brief", "chunk 1"], "only the first chunk goes before the brief's request is under way");
  underWay();
  await running;
  assert.equal(order.filter((o) => o === "brief").length, 1, "asked once");
  assert.ok(order.indexOf("chunk 2") > order.indexOf("brief under way"));
  assert.deepEqual(v.pending(), [], "the job ended without the brief being made");
  made();
});

test("a brief that cannot be made holds nothing up, and is asked once more as a chunk is", async () => {
  const v = viewer();
  let calls = 0;
  const s = server();
  await v.job(s.translate, { brief: async () => { calls += 1; throw new Error("too many AI calls at once"); } }).run();
  assert.deepEqual(v.pending(), []);
  assert.equal(last(v.statuses).error, null);
  await until(() => calls === 2, "the brief asked once more");
});

test("a try of the brief's request that fails and is asked again keeps the chunks waiting for it", async () => {
  const v = viewer();
  const order = [];
  let tries = 0, underWay = null;
  // The first try is turned away (the account's AI calls all in use: a 429
  // with Retry-After), the second finds the server making the brief.
  const brief = (signal, onStage) => {
    tries += 1;
    order.push(`brief ${tries}`);
    if (tries === 1) return Promise.reject(Object.assign(new Error("too many AI calls at once"), { status: 429, retryAfter: 0.02 }));
    return new Promise((resolve) => {
      underWay = () => { order.push("brief under way"); onStage("brief"); resolve({ ready: true }); };
    });
  };
  const s = server((texts, call) => { order.push(`chunk ${call}`); return translated(texts); });
  const running = v.job(s.translate, { brief }).run();
  await until(() => tries === 2 && s.asked.length === 1, "the brief asked again");
  await sleep(30);
  assert.equal(s.asked.length, 1, "only the first chunk goes while the brief's request waits to be asked again");
  underWay();
  await running;
  assert.ok(order.indexOf("chunk 2") > order.indexOf("brief under way"));
  assert.deepEqual(v.pending(), []);
});

test("a halt while the chunks wait for the brief's request stops them there", async () => {
  const v = viewer();
  let job = null;
  // Its request not even sent yet (the document's opening text still being
  // read), so no abort reaches it.
  const brief = () => new Promise(() => {});
  const s = server((texts) => { setTimeout(() => job.halt(), 5); return translated(texts); });
  job = v.job(s.translate, { brief });
  await job.run();
  assert.equal(s.asked.length, 1, "only the first chunk went");
  assert.equal(last(v.statuses).error, null);
  settled(v.entries);
});

test("chunks that came back without the brief are asked once more when others came back with it, and take the new text", async () => {
  const everything = server();
  await viewer().job(everything.translate).run();
  const chunks = everything.asked.length;
  assert.ok(chunks > 2);

  const v = viewer();
  const sameAs = (texts, r) => JSON.stringify(texts) === JSON.stringify(r.texts);
  let before = null;
  // The first request went before the brief was made; the others had it,
  // and so does the first chunk asked again ("再" marks its new text).
  const s = server((texts, call) => {
    if (call === 1) return { ...translated(texts), brief: false };
    if (sameAs(texts, s.asked[0])) {
      before = new Map(blockTexts(v));
      return { translations: texts.map((t) => `再${zh(t)}`), failed: [], brief: true };
    }
    return { ...translated(texts), brief: true };
  });
  await v.job(s.translate).run();
  assert.equal(s.asked.length, chunks + 1, "one request more");
  const again = last(s.asked);
  assert.ok(sameAs(again.texts, s.asked[0]), "for the first chunk");
  assert.equal(again.context.waitBrief, true);
  const changed = blockTexts(v).filter(([b, t]) => before.get(b) !== t);
  assert.ok(changed.length >= s.asked[0].texts.length, JSON.stringify(changed));
  assert.ok(changed.every(([b, t]) => before.get(b)?.startsWith("译") && t.startsWith("再译")),
    "its paragraphs had their text meanwhile, and take the new one");
  assert.ok(blockTexts(v).every(([b, t]) => !t?.startsWith("再") || changed.some(([c]) => c === b)), "nothing else changes");
  assert.deepEqual(v.pending(), []);
  const end = last(v.statuses);
  assert.equal(end.progress, 1);
  assert.equal(end.untranslated, 0);
  settled(v.entries);
});

test("nothing is asked again when no chunk came back with the brief, and a chunk is asked again at most once", async () => {
  const everything = server();
  await viewer().job(everything.translate).run();
  const chunks = everything.asked.length;

  const none = server((texts) => ({ ...translated(texts), brief: false }));
  await viewer().job(none.translate).run();
  assert.equal(none.asked.length, chunks, "the brief never came: nothing to ask again");

  // The first chunk comes back without the brief, asked again too.
  const first = JSON.stringify(everything.asked[0].texts);
  const twice = server((texts) => ({ ...translated(texts), brief: JSON.stringify(texts) !== first }));
  await viewer().job(twice.translate, { parallel: 1 }).run();
  assert.equal(twice.asked.length, chunks + 1);

  // A request asked again that fails, or a unit it cannot translate, keeps
  // the text it has.
  const v = viewer();
  const keeps = server((texts, call) => {
    if (call === 1) return { ...translated(texts), brief: false };
    if (call === chunks + 1) return { translations: texts, failed: texts.map((_, j) => j), brief: true };
    return { ...translated(texts), brief: true };
  });
  await v.job(keeps.translate, { parallel: 1 }).run();
  assert.equal(keeps.asked.length, chunks + 1);
  assert.deepEqual(v.pending(), []);
  assert.equal(last(v.statuses).untranslated, 0);
  const failing = viewer();
  const down = server((texts, call) => {
    if (call === 1) return { ...translated(texts), brief: false };
    if (call > chunks) throw new Error("provider down");
    return { ...translated(texts), brief: true };
  });
  await failing.job(down.translate, { parallel: 1 }).run();
  assert.equal(down.asked.length, chunks + 2, "asked again, once more after it failed, and no more");
  assert.deepEqual(failing.pending(), []);
  assert.equal(last(failing.statuses).error, null, "a failed request asked again is no failure of the job");
});

test("a paragraph the server could not translate comes back when its chunk is asked again with the brief", async () => {
  const v = viewer();
  const s = server((texts, call) => {
    if (call === 1) return { translations: texts, failed: [0], brief: false };
    return { ...translated(texts), brief: true };
  });
  await v.job(s.translate, { parallel: 1 }).run();
  assert.deepEqual(v.pending(), []);
  const end = last(v.statuses);
  assert.equal(end.untranslated, 0);
  assert.equal(end.progress, 1);
  assert.ok([...v.entries.values()].every((e) => !e.failed.size));
});

test("the median request time counts from when the model writes, not the wait for the brief", async () => {
  const v = viewer();
  const s = server(async (texts, call, { onStage }) => {
    onStage("brief");
    await sleep(60);
    onStage("translate");
    return translated(texts);
  });
  await v.job(s.translate, { parallel: 1 }).run();
  const { medianMs } = last(v.statuses);
  assert.ok(medianMs >= 0 && medianMs < 50, `median ${medianMs} ms`);
  // A job that has nothing left to ask has no median.
  await v.job(server().translate).run();
  assert.equal(last(v.statuses).medianMs, null);
});

test("replies the server had kept or cached took no model call and stay out of the median", async () => {
  // Every chunk kept from before: no request reached the model.
  const v = viewer();
  await v.job(server((texts) => ({ ...translated(texts), cached: true })).translate).run();
  assert.deepEqual(v.pending(), []);
  assert.equal(last(v.statuses).medianMs, null);
  // The first chunk kept, the others from a slow model: the median is the
  // model's.
  const w = viewer();
  const s = server(async (texts, call) => {
    if (call === 1) return { ...translated(texts), cached: true };
    await sleep(40);
    return translated(texts);
  });
  await w.job(s.translate, { parallel: 1 }).run();
  assert.ok(s.asked.length >= 2);
  const { medianMs } = last(w.statuses);
  assert.ok(medianMs >= 30, `median ${medianMs} ms`);
});

test("jobs sharing a limit have no more requests in flight together than it allows, and every chunk comes back", async () => {
  let open = 0, most = 0;
  const slow = () => server(async (texts) => {
    open += 1;
    most = Math.max(most, open);
    await sleep(10);
    open -= 1;
    return translated(texts);
  });
  const limit = createLimit(3);
  const a = viewer(LONG), b = viewer(LONG);
  await Promise.all([
    a.job(slow().translate, { parallel: 4, limit }).run(),
    b.job(slow().translate, { parallel: 4, limit }).run(),
  ]);
  assert.equal(most, 3, "the two jobs' eight workers kept to the limit's three");
  for (const v of [a, b]) {
    assert.deepEqual(v.pending(), []);
    assert.equal(last(v.statuses).untranslated, 0);
  }
});

test("a job halted while its requests wait for a place in the limit leaves the line, and the places go on to the next job", async () => {
  const limit = createLimit(1);
  // A request that holds the one place until its job halts.
  const hog = server((texts, call, { signal }) => new Promise((resolve, reject) => {
    signal.addEventListener("abort", () => reject(new Error("aborted")));
  }));
  const holding = viewer().job(hog.translate, { parallel: 1, limit });
  const held = holding.run();
  await until(() => hog.asked.length === 1, "the request holding the place");

  const waiting = server();
  const queued = viewer().job(waiting.translate, { parallel: 2, limit });
  const run = queued.run();
  await sleep(20);
  assert.equal(waiting.asked.length, 0, "no place, no request");
  queued.halt();
  await run;
  holding.halt();
  await held;
  assert.equal(waiting.asked.length, 0, "the halted job sent nothing");

  const next = viewer();
  await next.job(server().translate, { parallel: 2, limit }).run();
  assert.deepEqual(next.pending(), [], "the place came free for the next job");
});

test("the median request time counts from when a request goes, not its wait for a place in the limit", async () => {
  const limit = createLimit(1);
  const s = server(async (texts) => {
    await sleep(20);
    return translated(texts);
  });
  const v = viewer(LONG);
  await v.job(s.translate, { parallel: 4, limit }).run();
  assert.ok(s.asked.length >= 4);
  const { medianMs } = last(v.statuses);
  assert.ok(medianMs < 60, `median ${medianMs} ms`);
});
