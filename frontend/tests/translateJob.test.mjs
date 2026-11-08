// The translated view's job (src/pdf/translateJob.js) in Node: the fixture
// paper's pages, segmented as the viewer segments them, and a fake server.
import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import { segmentPage } from "../src/pdf/pdfTranslate.js";
import { BREAK } from "../src/pdf/translatePlan.js";
import { FAILS_IN_A_ROW, createLimit, createTranslateJob, focusPages, requestChunk } from "../src/pdf/translateJob.js";

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
    { running: true, progress: 0, label: "document", focus: null, reading: false, error: null, refused: 0, untranslated: 0, unanswered: 0, unchanged: 0, unread: [], refsLeft: 0, refsOnly: false, medianMs: null });
  const { medianMs, ...end } = last(v.statuses);
  assert.deepEqual(end,
    { running: false, progress: 1, label: "document", focus: null, reading: false, error: null, refused: 0, untranslated: 0, unanswered: 0, unchanged: 0, unread: [], refsLeft: 0, refsOnly: false });
  assert.equal(typeof medianMs, "number");
});

// A paper of a page of text that ends in a list and a page of list alone, by
// hand: the fixture's four references are too few for a list. A block as the
// segmenter makes it, one column down the page.
const PROSE = "the ensemble of atoms shares one excitation and loses its coherence when the blockade is broken by the motion of atoms.";
function blocks(texts) {
  let y = 60;
  return texts.map((text) => {
    const nLines = text.startsWith("[") ? 2 : 6;
    const b = { text, src: text, spans: [], size: 9, nLines, indent: 0, translate: true, x1: 54, x2: 300, y1: y, y2: y + nLines * 10.5, lines: [] };
    y = b.y2 + 2;
    return b;
  });
}
const REFS = Array.from({ length: 8 }, (_, k) => `[${k + 1}] A. Smith and B. Jones, Phys. Rev. Lett. ${96 + k}, 01040${k} (200${k}).`);
const LISTED = () => new Map([[1, blocks([`Some text ${PROSE}`, ...REFS])], [2, blocks(REFS)]]);
const isRef = (t) => t.startsWith("[");

test("a left block makes its page done and asks for nothing, and progress does not count it", async () => {
  const paper = LISTED();
  const v = viewer(paper);
  const s = server();
  await v.job(s.translate).run();
  assert.deepEqual(s.texts(), [`Some text ${PROSE}`]);
  assert.ok([...v.entries.values()].every((e) => e.done), "both pages are done");
  settled(v.entries);
  assert.deepEqual([...v.entries.get(1).left], [1, 2, 3, 4, 5, 6, 7, 8]);
  assert.deepEqual([...v.entries.get(2).left], [0, 1, 2, 3, 4, 5, 6, 7]);
  assert.equal(v.entries.get(2).texts.every((t) => t === null), true, "the original shows");
  assert.equal(v.entries.get(2).failed.size, 0, "left, and no failure");
  const end = last(v.statuses);
  assert.deepEqual([end.progress, end.untranslated, end.refsLeft, end.refsOnly], [1, 0, 16, false]);
  assert.ok(v.statuses.every((st) => st.progress <= 1));
});

test("a paragraph does not run into a list's short head when the list's next page shows it is one", async () => {
  const open = "We show here that the atoms in the cavity keep their phase over many cycles of the drive and that the coherence of the";
  const head = "of the quantum theory of many atoms held in a cavity field, A. Smith, Phys. Rev. A 12, 345 (2001).";
  const paper = new Map([[1, blocks([open])], [2, blocks([head, REFS[0]])], [3, blocks(REFS)]]);
  const v = viewer(paper);
  const s = server();
  await v.job(s.translate).run();
  assert.deepEqual(s.texts(), [open]);
  assert.deepEqual([...v.entries.get(2).left], [0, 1]);
});

test("a page that is all references sends no request and says so in its status", async () => {
  const paper = new Map([[2, LISTED().get(2)]]);
  const v = viewer(paper);
  const s = server();
  await v.job(s.translate).run();
  assert.equal(s.asked.length, 0);
  assert.equal(v.entries.get(2).done, true);
  const end = last(v.statuses);
  assert.deepEqual([end.running, end.progress, end.refsLeft, end.refsOnly], [false, 1, 8, true]);
});

test("the next job leaves a left block again, and one with `references` brings it back", async () => {
  const v = viewer(LISTED());
  await v.job(server().translate).run();
  const left = new Map([...v.entries].map(([pn, e]) => [pn, [...e.left]]));

  // A plain job asks for nothing more.
  const plain = server();
  await v.job(plain.translate).run();
  assert.equal(plain.asked.length, 0, "the page's text is filled, the list left again");
  assert.deepEqual(new Map([...v.entries].map(([pn, e]) => [pn, [...e.left]])), left);
  assert.ok([...v.entries.values()].every((e) => e.done));

  // Asking for them: the list alone goes, its blocks fill and are left no more.
  const refs = server();
  await v.job(refs.translate, { references: true }).run();
  assert.equal(refs.texts().length, 16);
  assert.ok(refs.texts().every(isRef));
  assert.ok([...v.entries.values()].every((e) => e.done && !e.left.size));
  assert.ok(v.entries.get(2).texts.every((t) => t.startsWith("译")));
  const end = last(v.statuses);
  assert.deepEqual([end.progress, end.refsLeft], [1, 0]);

  // A block with its translation stays: a later plain job leaves nothing.
  const after = server();
  await v.job(after.translate).run();
  assert.equal(after.asked.length, 0);
  assert.ok(v.entries.get(2).texts.every((t) => t.startsWith("译")));
  assert.equal(last(v.statuses).refsLeft, 0);
});

test("a job asking for references that stops keeps what has not landed left, so they can be asked for again", async () => {
  const v = viewer(LISTED());
  await v.job(server().translate).run();
  let job = null;
  const halting = server(() => {
    job.halt();
    throw new Error("aborted");
  });
  job = v.job(halting.translate, { references: true, parallel: 1 });
  await job.run();
  const e = v.entries.get(1);
  assert.equal(e.left.size, 8);
  assert.equal(e.done, false, "its blocks are pending until a job leaves or lands them");
  assert.equal(last(v.statuses).refsLeft, 0, "a stopped job says nothing of them");
  const refs = server();
  await v.job(refs.translate, { references: true }).run();
  assert.equal(refs.texts().length, 16);
  assert.ok([...v.entries.values()].every((x) => x.done && !x.left.size));
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

test("a page that cannot be read gets no entry: it stays not done, the job's end says so, and the next job reads it", async () => {
  const v = viewer();
  const read = new Map();
  let broken = true;
  const over = {
    segment: async (pn) => {
      if (pn === 2 && broken) throw new Error("the range read dropped");
      read.set(pn, PAGES.get(pn));
      return read.get(pn);
    },
    cachedParas: (pn) => read.get(pn) || null,
  };
  await v.job(server().translate, over).run();
  assert.equal(v.entries.has(2), false, "no entry, so no done page");
  assert.equal(v.entries.get(1).done, true);
  const end = last(v.statuses);
  assert.deepEqual([end.error, end.untranslated, end.unanswered, end.unread], [null, 1, 0, [2]]);
  settled(v.entries);

  broken = false;
  const again = server();
  await v.job(again.translate, over).run();
  assert.ok(again.asked.length > 0, "the next job asks for the page");
  assert.deepEqual(v.pending(), []);
  assert.equal(v.entries.get(2).done, true);
  assert.deepEqual(last(v.statuses).unread, []);
});

test("a page still unread when its job is halted is not reported, and stays not done", async () => {
  const v = viewer();
  const read = new Map();
  let job = null;
  job = v.job(server().translate, {
    pages: [2],
    segment: async () => { job.halt(); throw new Error("the range read dropped"); },
    cachedParas: (pn) => read.get(pn) || null,
  });
  await job.run();
  assert.equal(v.entries.size, 0);
  const end = last(v.statuses);
  assert.deepEqual([end.untranslated, end.unread], [0, []]);
});

// A job over the long paper whose last page stays in segmentation until
// `release`, so the chunks of the pages before it are all it has counted.
function heldJob(v, translate, over = {}) {
  const pages = new Map();
  let release = null;
  const held = new Promise((resolve) => { release = resolve; });
  const job = v.job(translate, {
    segment: async (pn) => {
      if (pn === LONG.size) await held;
      pages.set(pn, LONG.get(pn));
      return LONG.get(pn);
    },
    cachedParas: (pn) => pages.get(pn) || null,
    ...over,
  });
  return { job, release };
}

test("a job halted with every chunk it planned back, its last pages unplanned, stops short of complete", async () => {
  const v = viewer(LONG);
  let held = null;
  held = heldJob(v, server().translate, {
    onStatus: (st) => {
      v.statuses.push(st);
      // Every chunk planned is back: the share done is 1, shown capped.
      if (st.running && st.progress === 0.95) { held.job.halt(); held.release(); }
    },
  });
  await held.job.run();
  const end = last(v.statuses);
  assert.equal(end.running, false);
  assert.ok(v.entries.size > 0, "the pages planned came back");
  assert.ok(end.progress < 1, `a stopped job is not complete: ${end.progress}`);
});

test("a job halted before it planned a chunk ends at nothing done", async () => {
  const v = viewer(LONG);
  const s = server();
  const held = heldJob(v, s.translate, { pages: [LONG.size] });
  const running = held.job.run();
  held.job.halt();
  held.release();
  await running;
  assert.equal(s.asked.length, 0);
  assert.equal(last(v.statuses).progress, 0);
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

// Page 1's paragraph the column break cut: its first block and its second.
const CUT = [PAGES.get(1).findIndex((b) => b.text.includes("In the rotating frame the")), PAGES.get(1).findIndex((b) => b.text.includes("Hamiltonian reads"))];
// A text "translated again".
const anew = (texts) => ({ translations: texts.map((t) => zh(t).replaceAll("译", "再译")), failed: [] });
// A viewer whose paper is translated, ready for a job on one paragraph.
async function translatedViewer() {
  const v = viewer();
  await v.job(server().translate).run();
  return v;
}
const focused = (v, s, i, over = {}) => v.job(s.translate, { pages: [1], label: "p.1", focus: { pn: 1, i }, fresh: true, ...over });

test("a focused job asks for its block's paragraph alone, afresh and waiting for the brief, and leaves the page as it was", async () => {
  const v = await translatedViewer();
  const before = v.entries.get(1);
  const s = server(anew);
  await focused(v, s, CUT[1]).run();
  assert.equal(s.asked.length, 1, "one request");
  assert.deepEqual(s.asked[0].texts.length, 1, "for one text");
  assert.ok(s.asked[0].texts[0].includes(BREAK), "the whole paragraph, both its parts");
  assert.equal(s.asked[0].context.fresh, true);
  assert.equal(s.asked[0].context.waitBrief, true, "a text asked afresh waits for the brief, the first too");
  const after = v.entries.get(1);
  for (const i of CUT) assert.ok(after.texts[i].startsWith("再译"), after.texts[i]);
  after.texts.forEach((t, i) => { if (!CUT.includes(i)) assert.equal(t, before.texts[i]); });
  assert.equal(after.done, true);
  settled(v.entries);
  const end = last(v.statuses);
  assert.deepEqual([end.focus, end.progress, end.unchanged, end.untranslated], [{ pn: 1, i: CUT[1] }, 1, 0, 0]);
});

test("a focused job leaves a page beside it that holds another key's translation as it is, and asks for the part on its page", async () => {
  const v = viewer();
  await v.job(server().translate, { key: "ja|model" }).run();
  const other = v.entries.get(2);
  assert.ok(other.done && other.texts[1], "page 2 is translated under the other key");
  // Page 1 alone under this key: its last paragraph runs on at page 2's top.
  await v.job(server().translate, { pages: [1], label: "p.1" }).run();
  assert.equal(v.entries.get(2), other);
  const s = server(anew);
  await focused(v, s, 27).run();
  assert.equal(v.entries.get(2), other, "page 2 keeps the other key's entry");
  assert.ok(!s.asked[0].texts[0].includes(BREAK), "the paragraph's part on page 1 alone");
  assert.ok(v.entries.get(1).texts[27].startsWith("再译"));
  assert.deepEqual(focusPages(1, "zh-CN|model", (pn) => v.entries.get(pn)), [0, 1], "page 2 is not the job's");
  assert.deepEqual(focusPages(2, "ja|model", (pn) => v.entries.get(pn)), [2, 3]);
  assert.deepEqual(focusPages(2, "zh-CN|model", () => null), [1, 2, 3], "pages with no entry are the job's");
});

test("a focused job shows no page as queued while its request is out, only its paragraph as busy", async () => {
  const v = await translatedViewer();
  let seen = null;
  const s = server((texts) => {
    seen = v.entries.get(1);
    return anew(texts);
  });
  // A page with a paragraph never asked for: still nothing is washed as queued.
  const e = v.entries.get(1);
  const never = e.texts.findIndex((t, i) => typeof t === "string" && !CUT.includes(i) && t);
  v.entries.set(1, { ...e, texts: e.texts.map((t, i) => (i === never ? undefined : t)), done: false });
  await focused(v, s, CUT[0]).run();
  assert.equal(seen.queued, false);
  assert.deepEqual([...seen.busy].sort(), [...CUT].sort());
  assert.equal(v.entries.get(1).texts[never], undefined, "the paragraph never asked for is still not");
  assert.equal(v.entries.get(1).done, false);
});

test("a paragraph asked again that fails keeps the text it had, with no failed mark, and says so", async () => {
  for (const answer of [
    () => { throw new Error("provider down"); },
    (texts) => ({ translations: texts, failed: texts.map((_, j) => j) }),
  ]) {
    const v = await translatedViewer();
    const had = CUT.map((i) => v.entries.get(1).texts[i]);
    await focused(v, server(answer), CUT[0], { retry: { pauseMs: 1 } }).run();
    assert.deepEqual(CUT.map((i) => v.entries.get(1).texts[i]), had);
    assert.deepEqual(failedBlocks(v), []);
    const end = last(v.statuses);
    assert.deepEqual([end.unchanged, end.untranslated, end.error], [1, 0, null]);
  }
});

test("a paragraph that never had text, asked alone, is marked failed when it fails, and the page's other marks stay", async () => {
  const v = await translatedViewer();
  const e = v.entries.get(1);
  const other = e.texts.findIndex((t, i) => !!t && !CUT.includes(i));
  // Its paragraph came back as its source; another failed in an earlier job.
  v.entries.set(1, { ...e, texts: e.texts.map((t, i) => (CUT.includes(i) ? null : t)), failed: new Set([other]) });
  await focused(v, server(() => { throw new Error("provider down"); }), CUT[0], { retry: { pauseMs: 1 } }).run();
  assert.deepEqual(failedBlocks(v).sort(), [other, ...CUT].map((i) => `1:${i}`).sort());
  assert.deepEqual(CUT.map((i) => v.entries.get(1).texts[i]), [null, null], "the original still shows");
  assert.equal(v.entries.get(1).done, true, "the page owes nothing more than it did");
  assert.equal(last(v.statuses).untranslated, 1);
});

test("the reader's original blocks go through a job, less a block asked for again", async () => {
  const v = await translatedViewer();
  const e = v.entries.get(1);
  const other = e.texts.findIndex((t, i) => !!t && !CUT.includes(i));
  v.entries.set(1, { ...e, original: new Set([other, CUT[0]]) });
  const s = server((texts) => {
    // Turned on while the request is out.
    const now = v.entries.get(1);
    v.entries.set(1, { ...now, original: new Set([...now.original, CUT[1]].filter((i) => i !== other)) });
    return anew(texts);
  });
  await focused(v, s, CUT[0]).run();
  assert.equal(v.entries.get(1).original, undefined, "the landed blocks show their new text, the other one turned off meanwhile");
  v.entries.set(1, { ...v.entries.get(1), original: new Set([other]) });
  await v.job(server().translate, { pages: [1] }).run();
  assert.deepEqual([...v.entries.get(1).original], [other], "a job that lands nothing there keeps it");
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

// A server whose chunks stream `share` of each text, longer than it for a share
// over 1, and hear what the job says then.
function streaming(share, then = translated) {
  return server(async (texts, call, { onPartial }) => {
    texts.forEach((t, j) => onPartial(j, "译".repeat(Math.ceil(t.length * share))));
    return then(texts, call);
  });
}
const runningProgress = (v) => v.statuses.filter((st) => st.running).map((st) => st.progress);
const neverBack = (xs) => xs.every((x, k) => !k || x >= xs[k - 1]);

test("the percentage counts streamed characters before anything lands, and a landing replaces their share", async () => {
  const v = viewer();
  let first = null;
  const s = streaming(0.5, (texts, call) => {
    if (call === 1) first = last(v.statuses).progress;
    return translated(texts);
  });
  await v.job(s.translate, { parallel: 1 }).run();
  assert.ok(first > 0, `the first chunk's streamed text shows: ${first}`);
  assert.ok(neverBack(runningProgress(v)));
  assert.equal(last(v.statuses).progress, 1);
});

test("streamed text longer than its source does not count past its unit, so 100% waits for the last reply", async () => {
  const v = viewer();
  const s = streaming(3, (texts) => {
    assert.ok(last(v.statuses).progress < 1, "a reply is still out");
    return translated(texts);
  });
  await v.job(s.translate, { parallel: 1 }).run();
  assert.ok(neverBack(runningProgress(v)));
  assert.equal(last(v.statuses).progress, 1);
});

test("the percentage does not go back as the total grows, or when a request asked again streams from its start", async () => {
  const v = viewer(LONG);
  let tries = 0;
  const s = server(async (texts, call, { onPartial }) => {
    tries += 1;
    texts.forEach((t, j) => onPartial(j, t.slice(0, tries === 1 ? t.length : 3)));
    if (tries === 1) throw new Error("the stream was cut");
    return translated(texts);
  });
  await v.job(s.translate, { parallel: 1, retry: { pauseMs: 1 } }).run();
  assert.ok(neverBack(runningProgress(v)), JSON.stringify(runningProgress(v)));
  assert.equal(last(v.statuses).progress, 1);
});

test("streaming changes where a job ends in nothing: a lost chunk and a halt end where they would have", async () => {
  const lost = (answer) => {
    let failed = 0;
    return async (texts, call, tools) => {
      await answer(texts, call, tools);
      if (failed < 2) { failed += 1; throw new Error("provider down"); }
      return translated(texts);
    };
  };
  const plain = viewer(), typing = viewer();
  await plain.job(server(lost(async () => {})).translate, { parallel: 1 }).run();
  await typing.job(server(lost(async (texts, call, { onPartial }) => texts.forEach((t, j) => onPartial(j, t)))).translate, { parallel: 1 }).run();
  const end = last(plain.statuses).progress;
  assert.ok(end > 0 && end < 1, `a chunk lost: ${end}`);
  assert.equal(last(typing.statuses).progress, end);

  // A halt with text streamed and nothing back ends at nothing done.
  const v = viewer();
  let job = null;
  const s = server(async (texts, call, { onPartial }) => {
    texts.forEach((t, j) => onPartial(j, t));
    assert.ok(last(v.statuses).progress > 0);
    job.halt();
    return null;
  });
  job = v.job(s.translate, { parallel: 1 });
  await job.run();
  assert.equal(last(v.statuses).progress, 0);
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

// A request answering with the given outcomes in turn: an Error is thrown,
// anything else returned.
const answering = (...outcomes) => {
  const run = async () => {
    run.calls += 1;
    const o = outcomes.shift();
    if (o instanceof Error) throw o;
    return o;
  };
  run.calls = 0;
  return run;
};
const refused = (retryAfter) => Object.assign(new Error("Too many AI requests"), { status: 429, retryAfter });
const quick = { pauseMs: 1 };

test("a chunk's failed request is asked once more, then the failure stands", async () => {
  const ok = { translations: ["x"] };
  let run = answering(new Error("network"), ok);
  assert.equal(await requestChunk(run, null, quick), ok);
  assert.equal(run.calls, 2);
  run = answering(new Error("network"), new Error("still down"), ok);
  await assert.rejects(requestChunk(run, null, quick), /still down/);
  assert.equal(run.calls, 2);
  // A stream cut off before its final line counts as a failure too.
  run = answering(null, null);
  assert.equal(await requestChunk(run, null, quick), null);
  assert.equal(run.calls, 2);
});

test("a refusal because the account's calls are all in use waits as the server says", async () => {
  const ok = { translations: ["x"] };
  let run = answering(refused(0.005), refused(0.005), refused(0.005), new Error("blip"), ok);
  assert.equal(await requestChunk(run, null, quick), ok, "the waits leave the one plain retry");
  assert.equal(run.calls, 5);
  // Past its patience the refusal stands; a 429 without Retry-After (the
  // shared allowance used up) at once.
  run = answering(refused(0.005), ok);
  await assert.rejects(requestChunk(run, null, { pauseMs: 1, patienceMs: 0 }), /Too many/);
  run = answering(Object.assign(new Error("allowance used up"), { status: 429 }), ok);
  await assert.rejects(requestChunk(run, null, quick), /allowance/);
  assert.equal(run.calls, 1);
});

test("halting ends a request's wait without asking again", async () => {
  const ctl = new AbortController();
  const run = answering(refused(60), { translations: ["x"] });
  const asked = requestChunk(run, ctl.signal);
  setTimeout(() => ctl.abort(), 5);
  assert.equal(await asked, null);
  assert.equal(run.calls, 1);
});
