// The translated view's job (docs/dev/translation.md "The queue"): one queue
// over the pages it was given, free of React, so it runs in Node under the
// tests. pdf/translateState.js makes one per click of the button and keeps
// the page entries it hands back, which the viewer renders.
//
// A producer segments the pages in queue order, plans them (translatePlan.js:
// a paragraph that a column or page break cut in two is one unit) and feeds
// ONE flat list of chunks; `parallel` workers start on the first chunk while
// later pages are still segmenting, and stream across page boundaries — the
// parallel slots never idle at page ends. A chunk whose request fails is
// asked again (requestChunk); one that still fails leaves its paragraphs
// pending for the next job and the workers go on, since one dropped
// connection to the provider says little of the next. The job stops, with
// the last failure's reason, only when FAILS_IN_A_ROW chunks fail with no
// success between them, or at once on a refusal no later request escapes
// (finalRefusal). The server asks the model again for what its reply got
// wrong and names the paragraphs that still failed, which stay pending for
// the next job too. Halting aborts the requests in flight and keeps
// finished chunks.
//
// The paper's brief (its field and terms, which the server writes once and
// puts in every request's prompt) is asked for as the job starts and never
// waited for here: the first chunk goes at once, using the brief only if it
// is made already, so text lands within one request's time; the others go
// once the brief's request is under way and wait for it on the server. At
// the queue's end, the chunks that came back without the brief while others
// came back with it are asked once more, so the paper reads with one set of
// terms; their paragraphs keep the text they have until the new one lands.
import { chunkUnits, createPlanner, partials, requestChunk, results } from "./translatePlan.js";

// Chunks failing one after another with no success between them: the
// provider is down, not one connection lost, and the job stops.
export const FAILS_IN_A_ROW = 3;

// A failure every later request would meet too, which stops the job at
// once: the shared AI allowance used up (a 429 without Retry-After; one
// with it is the account's AI calls all in use, which requestChunk waits
// out), or Gamma refusing the request itself — not signed in, no access to
// the workspace (401, 403), or no AI provider to ask (503).
export const finalRefusal = (err) =>
  (err?.status === 429 && !(err.retryAfter > 0)) || [401, 403, 503].includes(err?.status);

// The chunk requests in flight across jobs, `n` at most: a job has
// `parallel` workers, but jobs of several documents run side by side, and
// together they must stay below the AI calls an account may have open at
// once. Past that the server turns a request away with a 429 and a
// Retry-After that the polling request nearly always loses, since a worker
// whose request just ended sends its next one at once; it would then run out
// of requestChunk's patience and leave its paragraphs in the original. Here
// a request waits for one in flight to end, in the order they came; a halt
// takes a waiting one out of the line. `take(signal)` resolves to what frees
// the place again.
export function createLimit(n) {
  let free = n;
  const waiting = [];
  const take = (signal) => new Promise((resolve, reject) => {
    if (signal?.aborted) return reject(new Error("aborted"));
    let freed = false;
    const release = () => {
      if (freed) return;
      freed = true;
      const next = waiting.shift();
      if (next) next();
      else free += 1;
    };
    if (free > 0) {
      free -= 1;
      return resolve(release);
    }
    const abort = () => {
      waiting.splice(waiting.indexOf(grant), 1);
      reject(new Error("aborted"));
    };
    const grant = () => {
      signal?.removeEventListener("abort", abort);
      resolve(release);
    };
    waiting.push(grant);
    signal?.addEventListener("abort", abort, { once: true });
  });
  return { take };
}

// A page's entry, what the viewer draws over the page:
// - `key`: the language and model it was made under; `paras`: the page's
//   segmented blocks;
// - `texts`: results by block index (translatePlan.results): a translation,
//   null for a block that came back as its source (the original shows;
//   "Translate this page" asks again), "" for a block whose words went to
//   the part before it (masked), missing while pending;
// - `done`: no block is pending; `queued`: the job still owes the page some;
// - `busy`: blocks whose request is in flight (their lines shimmer);
//   `partial`: the text streamed so far per block (typed onto the page ahead
//   of the final text);
// - `failed`: blocks the server could not translate in this job, or whose
//   chunk's request failed (pending still, so the page is not done and the
//   next job asks for them; the original shows meanwhile).
// The job's end drops `queued`, `busy` and `partial`: nothing is owed or in
// flight any more, whether it finished, was halted or failed.
//
// What the job is given:
// - `pages` in queue order, `label` for its status, `key` for its entries,
//   `parallel` workers;
// - `segment(pn)` segments a page and caches it; `cachedParas(pn)` is its
//   blocks once segmented, else null;
// - `seed(pn)` is the page's entry from before. Made under `key` from the
//   same blocks, it seeds the page, so a re-run keeps what an earlier job
//   finished and asks only for the rest — its untranslated (null) blocks
//   included, which go back to pending;
// - `translate(texts, signal, onPartial, context, onStage)` is one chunk's
//   request, resolving to the server's final reply ({translations, failed,
//   brief}); its context carries `waitBrief`, false for the first chunk;
// - `brief(signal, onStage)` asks the server for the paper's brief, null
//   when the paper can have none; the job goes on once its request has
//   answered a first line or ended;
// - `context`: what every request carries for the paper ({pageId} or
//   {head}), or a promise of it, so the document's opening text is read
//   while the job already shows as running;
// - `onPages(Map pn → entry)` hears the entries that changed;
//   `onStatus({running, progress, label, reading, error, untranslated,
//   medianMs})` the job's state: `reading` while a request waits for the
//   server to read the paper for its brief (the stream's "stage" lines),
//   and at the end `error` (why the job stopped: the last failed request's
//   reason), `untranslated` (the paragraphs left in the original: those the
//   server could not translate and those whose chunk's request failed),
//   `unanswered` (the latter alone) and `medianMs` (its requests' median
//   time once the model was
//   writing, null when none reached the model: replies the server had kept
//   or cached are not counted);
// - `limit`: a createLimit shared with the other jobs, which each chunk
//   request goes through (the brief's request does not: one per job);
// - `retry`: requestChunk's options.
export function createTranslateJob({
  pages, label, key, parallel, segment, cachedParas, seed, translate, brief = null, context, onPages, onStatus, limit = null, retry,
}) {
  const ctl = new AbortController();
  const halted = () => ctl.signal.aborted;
  const aborted = new Promise((resolve) => ctl.signal.addEventListener("abort", resolve, { once: true }));
  const inJob = new Set(pages);

  let status = { running: true, progress: 0, label, reading: false, error: null, untranslated: 0, unanswered: 0, medianMs: null };
  const report = (patch) => {
    status = { ...status, ...patch };
    onStatus(status);
  };
  // The chunks whose request is waiting on the paper's brief.
  const reading = new Set();
  const readingFor = (c, on) => {
    const was = reading.size > 0;
    if (on) reading.add(c);
    else reading.delete(c);
    if (was !== reading.size > 0) report({ reading: reading.size > 0 });
  };

  // Per-page state, made on first use (a page's own turn, or a unit from a
  // neighbouring page that a paragraph runs into): `out` becomes the entry's
  // texts, `pending` holds the blocks still without a result.
  const states = new Map();
  const stateOf = (pn) => {
    let st = states.get(pn);
    if (!st) {
      const paras = cachedParas(pn) || [];
      const e = seed(pn);
      const out = e && e.key === key && e.paras === paras ? e.texts.map((t) => (t === null ? undefined : t)) : [];
      const pending = new Set();
      paras.forEach((p, i) => { if (p.translate && out[i] == null) pending.add(i); });
      st = { paras, out, pending, busy: new Set(), partial: {}, failed: new Set() };
      states.set(pn, st);
    }
    return st;
  };
  const entryOf = (pn) => {
    const st = stateOf(pn);
    const done = st.pending.size === 0;
    return { key, paras: st.paras, texts: st.out.slice(), done, queued: !done,
             busy: new Set(st.busy), partial: { ...st.partial }, failed: new Set(st.failed) };
  };
  // Each page's entry as last handed out, for the end to settle.
  const shown = new Map();
  const paint = (pns) => {
    const changed = new Map();
    for (const pn of pns) changed.set(pn, entryOf(pn));
    for (const [pn, e] of changed) shown.set(pn, e);
    onPages(changed);
  };
  const pagesOf = (units) => new Set(units.flatMap((u) => u.targets.map((t) => t.pn)));

  // The units that came back, their characters counted done, those the
  // server could not translate, and those whose chunk's request failed
  // (both pending, for the next job).
  const landed = new Set();
  const lost = new Set();
  const unanswered = new Set();
  let totalChars = 0, doneChars = 0;
  // A chunk's reply onto its pages. A unit asked again that fails keeps the
  // text it has; one that failed before and comes back is no longer lost.
  const land = (c, res) => {
    const failed = new Set(res.failed || []);
    c.units.forEach((u, j) => {
      if (failed.has(j)) {
        if (landed.has(u)) return;
        lost.add(u);
        for (const t of u.targets) stateOf(t.pn).failed.add(t.i);
        return;
      }
      if (!landed.has(u)) { landed.add(u); doneChars += u.chars; }
      lost.delete(u);
      for (const r of results(u, res.translations[j])) {
        const st = stateOf(r.pn);
        st.out[r.i] = r.text;
        st.pending.delete(r.i);
        st.failed.delete(r.i);
      }
    });
    paint(pagesOf(c.units));
  };
  // A chunk whose request failed: its paragraphs keep the original.
  const drop = (c) => {
    for (const u of c.units) {
      unanswered.add(u);
      for (const t of u.targets) stateOf(t.pn).failed.add(t.i);
    }
    paint(pagesOf(c.units));
  };
  const answers = (res, c) => Array.isArray(res?.translations) && res.translations.length === c.units.length;
  // One chunk request, sent once the shared limit has a place for it.
  const send = async (request) => {
    const free = limit ? await limit.take(ctl.signal) : null;
    try {
      return await request();
    } finally {
      free?.();
    }
  };

  // The paper's brief, asked for once and not waited for. What it returns
  // settles once the server is making the brief (the request's first line),
  // once the request is over for good (made, found, or failed after its
  // last try), or at a halt. Not when one try fails and requestChunk is to
  // ask again (the account's AI calls all in use): the chunks let through
  // then would find no brief being made and come back without it, only to
  // be asked again at the end. requestChunk's patience bounds the wait.
  function askBrief() {
    if (!brief) return Promise.resolve();
    let started;
    const underWay = new Promise((resolve) => { started = resolve; });
    requestChunk(() => brief(ctl.signal, () => started()), ctl.signal, retry).catch(() => {}).finally(() => started());
    return Promise.race([underWay, aborted]);
  }

  async function run() {
    onStatus(status);
    const briefUnderWay = askBrief();
    const shared = await context;
    const planner = createPlanner({
      paras: cachedParas,
      inJob: (pn) => inJob.has(pn),
      filled: (pn, i) => stateOf(pn).out[i] != null,
    });
    // Producer/consumer: the producer segments pages in queue order and
    // appends chunks; workers pick them up immediately. Idle workers wait on
    // `wake` with a short timeout backstop — the timeout (not an event) is
    // what lets them notice a halt or a failure.
    const queue = [];
    // `failed`: the job stopped; `streak`: chunks failed since the last
    // that came back.
    let failed = false, segDone = false, error = null, sent = 0, streak = 0;
    // How long each request took once the model was writing; whether any
    // came back with the brief, and the chunks that came back without it.
    const times = [];
    let withBrief = false;
    const without = [];
    let wake = null;
    const notify = () => { const w = wake; wake = null; w?.(); };
    const producer = async () => {
      for (const pn of pages) {
        if (halted() || failed) break;
        // The page, and its neighbours in the job: a paragraph that runs
        // over a page break is planned with both pages segmented.
        for (const p of [pn, pn - 1, pn + 1]) {
          if (p !== pn && !inJob.has(p)) continue;
          try { await segment(p); } catch {}
        }
        if (halted()) break;
        const units = planner.planPage(pn);
        const touched = pagesOf(units).add(pn);
        for (const chunk of chunkUnits(units)) {
          queue.push(chunk);
          totalChars += chunk.units.reduce((n, u) => n + u.chars, 0);
        }
        paint(touched);
        notify();
      }
      segDone = true;
      notify();
    };
    const worker = async () => {
      while (!halted() && !failed) {
        const c = queue.shift();
        if (!c) {
          if (segDone) return;
          await new Promise((resolve) => {
            const prev = wake;
            wake = () => { prev?.(); resolve(); };
            setTimeout(resolve, 200);
          });
          continue;
        }
        const waitBrief = sent++ > 0;
        if (waitBrief) await briefUnderWay;
        if (halted()) return;
        let res = null;
        const pns = pagesOf(c.units);
        const targets = c.units.flatMap((u) => u.targets);
        for (const t of targets) stateOf(t.pn).busy.add(t.i);
        paint(pns);
        const onPartial = (j, text) => {
          const u = c.units[j];
          if (halted() || !u) return;
          for (const r of partials(u, text)) stateOf(r.pn).partial[r.i] = r.text;
          paint(pns);
        };
        let began = 0;
        const onStage = (stage) => {
          if (halted()) return;
          if (stage === "translate") began = Date.now();
          readingFor(c, stage === "brief");
        };
        let err = null;
        try {
          res = await requestChunk(() => send(() => {
            began = Date.now();
            return translate(c.texts, ctl.signal, onPartial, { ...shared, ...c.context, waitBrief }, onStage);
          }), ctl.signal, retry);
        } catch (e) { err = e; }
        readingFor(c, false);
        for (const t of targets) { const st = stateOf(t.pn); st.busy.delete(t.i); delete st.partial[t.i]; }
        if (halted()) return;
        if (!answers(res, c)) {
          error = err?.message || "";
          if (++streak >= FAILS_IN_A_ROW || finalRefusal(err)) {
            failed = true;
            return;
          }
          drop(c);
          continue;
        }
        streak = 0;
        // A reply the server had kept or cached cost no model call: its
        // time says nothing of the model's speed.
        if (res.cached !== true) times.push(Date.now() - began);
        if (res.brief === true) withBrief = true;
        else if (res.brief === false) without.push(c);
        land(c, res);
        // While segmentation is still running the denominator is a lower
        // bound, so the progress shown stops short of the end until the
        // total is final.
        const p = totalChars ? doneChars / totalChars : 1;
        report({ progress: segDone ? p : Math.min(p, 0.95) });
      }
    };
    await Promise.all([producer(), ...Array.from({ length: parallel }, worker)]);
    // The chunks that went without the brief, now that it is made: asked
    // once more, whatever comes back. A request that fails leaves the text
    // they have.
    if (!halted() && !failed && withBrief && without.length) {
      await Promise.all(Array.from({ length: Math.min(parallel, without.length) }, async () => {
        for (let c = without.shift(); c && !halted(); c = without.shift()) {
          let res = null;
          try {
            res = await requestChunk(() => send(() => translate(c.texts, ctl.signal, null, { ...shared, ...c.context, waitBrief: true })),
              ctl.signal, retry);
          } catch {}
          if (!halted() && answers(res, c)) land(c, res);
        }
      }));
    }
    const settled = new Map();
    for (const [pn, e] of shown) {
      if (e.queued || e.busy.size || Object.keys(e.partial).length) {
        settled.set(pn, { ...e, queued: false, busy: null, partial: null });
      }
    }
    if (settled.size) onPages(settled);
    const sorted = times.sort((a, b) => a - b);
    report({ running: false, reading: false, progress: totalChars ? doneChars / totalChars : 1,
             error: failed && !halted() ? error : null,
             untranslated: halted() ? 0 : lost.size + unanswered.size, unanswered: halted() ? 0 : unanswered.size,
             medianMs: sorted.length ? (sorted[(sorted.length - 1) >> 1] + sorted[sorted.length >> 1]) / 2 : null });
  }

  return { run, halt: () => ctl.abort() };
}
